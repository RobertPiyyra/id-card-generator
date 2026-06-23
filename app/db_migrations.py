"""
Database initialization and migration functions.

Extracted from app/legacy_app.py to separate database schema management
from the main application module.
"""
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import inspect, text

from models import Template, TemplateWorkflow

logger = logging.getLogger(__name__)


def init_db(db):
    """Initialize database with SQLAlchemy"""
    try:
        db.create_all()
        logger.info("Database initialized successfully with SQLAlchemy")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise


def check_deadline_passed(template_id, db):
    """
    Returns True if deadline has passed, False otherwise.
    Uses local server time for comparison.
    """
    if not template_id:
        return False, None

    try:
        template = db.session.get(Template, int(template_id))

        # If no deadline is set in DB, return False (Open indefinitely)
        if not template or not template.deadline:
            return False, None

        # Get current time (Local System Time)
        now = datetime.now()
        deadline = template.deadline

        # Debugging Logs
        logger.info(f"--- DEADLINE CHECK for Template {template_id} ---")
        logger.info(f"Current Time: {now}")
        logger.info(f"Deadline:     {deadline}")

        if now > deadline:
            logger.warning("Deadline has PASSED.")
            return True, deadline.strftime("%d %B %Y, %I:%M %p")

        logger.info("Deadline is in the future.")
        return False, None
    except Exception as e:
        logger.error(f"Error checking deadline: {e}")
        return False, None


def log_activity(db, session, request, action, target=None, details=None):
    """
    Helper function to log administrative or user actions to the database.
    Silent fail: If logging fails, it logs an error but doesn't crash the app.
    """
    from models import ActivityLog

    try:
        # Determine who is acting
        if session.get('admin'):
            actor = "Admin"
        elif session.get('student_email'):
            actor = session['student_email']
        else:
            actor = "Anonymous"

        # Create log entry
        log = ActivityLog(
            actor=actor,
            action=action,
            target=str(target) if target else None,
            details=str(details) if details else None,
            ip_address=request.remote_addr,
            timestamp=datetime.now(timezone.utc)
        )

        db.session.add(log)
        db.session.commit()

    except Exception as e:
        # We catch all exceptions so the main app flow isn't interrupted by a logging failure
        logger.error(f"Failed to log activity: {e}")
        db.session.rollback()


def _quote_db_identifier(db, identifier):
    """Quote a table or column name for the active SQLAlchemy dialect."""
    return db.engine.dialect.identifier_preparer.quote(identifier)


def _run_schema_ddl(db, sql, success_message, *, warning_message=None):
    """
    Run one schema DDL statement in its own transaction.

    PostgreSQL aborts the whole transaction after any failed statement. Keeping
    each ALTER isolated prevents a harmless/expected migration miss from rolling
    back earlier columns that were successfully added.
    """
    try:
        with db.engine.begin() as conn:
            conn.execute(text(sql))
        logger.info(success_message)
        return True
    except Exception as e:
        logger.warning("%s: %s", warning_message or "Schema migration skipped/failed", e)
        return False


def _get_table_column_names(inspector, table_name):
    try:
        return {c["name"] for c in inspector.get_columns(table_name)}
    except Exception as e:
        logger.warning(f"Could not inspect table '{table_name}': {e}")
        return set()


def _add_column_if_missing(db, table_name, column_name, column_type_sql):
    inspector = inspect(db.engine)
    if column_name in _get_table_column_names(inspector, table_name):
        return False

    table_sql = _quote_db_identifier(db, table_name)
    column_sql = _quote_db_identifier(db, column_name)
    return _run_schema_ddl(
        db,
        f"ALTER TABLE {table_sql} ADD COLUMN {column_sql} {column_type_sql}",
        f"Added '{column_name}' column to {table_name}",
        warning_message=f"Could not add {table_name}.{column_name}",
    )


def sync_model_columns_to_database(db):
    """
    Best-effort additive schema sync for Railway/Postgres and local SQLite.

    This app historically used db.create_all() plus hand-written ALTER TABLE
    statements. create_all() does not add columns to existing tables, so older
    deployed databases can miss fields that the SQLAlchemy models now select.
    """
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    dialect = db.engine.dialect

    for mapper in sorted(db.Model.registry.mappers, key=lambda m: m.local_table.name):
        table = mapper.local_table
        table_name = table.name
        if table_name not in existing_tables:
            continue

        existing_columns = _get_table_column_names(inspector, table_name)
        for column in table.columns:
            if column.name in existing_columns or column.primary_key:
                continue

            try:
                column_type_sql = column.type.compile(dialect=dialect)
            except Exception as e:
                logger.warning(f"Could not compile type for {table_name}.{column.name}: {e}")
                continue

            _add_column_if_missing(db, table_name, column.name, column_type_sql)


def migrate_database(app, db, get_default_photo_config):
    """Migration function to create tables and update schema"""
    try:
        with app.app_context():
            # 1. Create tables if they don't exist (This creates 'activity_logs')
            db.create_all()

            # 1b. Add any model columns missing from older Railway/Postgres tables.
            # This fixes errors like: column students.back_image_url does not exist.
            sync_model_columns_to_database(db)

            # 2. Check for missing columns in existing tables
            inspector = inspect(db.engine)

            # --- Migrate STUDENTS table ---
            s_columns = [c['name'] for c in inspector.get_columns('students')]
            with db.engine.connect() as conn:
                if 'sheet_filename' not in s_columns:
                    conn.execute(text("ALTER TABLE students ADD COLUMN sheet_filename VARCHAR(255)"))
                    logger.info("Added 'sheet_filename' column to students")

                if 'sheet_position' not in s_columns:
                    conn.execute(text("ALTER TABLE students ADD COLUMN sheet_position INTEGER"))
                    logger.info("Added 'sheet_position' column to students")
                if 'back_image_url' not in s_columns:
                    conn.execute(text("ALTER TABLE students ADD COLUMN back_image_url VARCHAR(1024)"))
                    logger.info("Added 'back_image_url' column to students")
                if 'back_generated_filename' not in s_columns:
                    conn.execute(text("ALTER TABLE students ADD COLUMN back_generated_filename VARCHAR(255)"))
                    logger.info("Added 'back_generated_filename' column to students")

                # --- Migrate TEMPLATES table ---
                t_columns = [c['name'] for c in inspector.get_columns('templates')]

                # 0. Remove NOT NULL constraint from filename (allow Cloudinary-only templates)
                try:
                    # For PostgreSQL
                    conn.execute(text("ALTER TABLE templates ALTER COLUMN filename DROP NOT NULL"))
                    logger.info("Removed NOT NULL constraint from templates.filename")
                except Exception:
                    # SQLite doesn't support this easily, skip
                    pass

                # 1. Add Language Column
                if 'language' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN language VARCHAR(20) DEFAULT 'english'"))
                    logger.info("Added 'language' column to templates")

                # 2. Add Text Direction Column
                if 'text_direction' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN text_direction VARCHAR(10) DEFAULT 'ltr'"))
                    logger.info("Added 'text_direction' column to templates")

                # 3. Add Template URL Column (for Cloudinary storage)
                if 'template_url' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN template_url TEXT"))
                    logger.info("Added 'template_url' column to templates")
                if 'back_filename' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_filename VARCHAR(255)"))
                    logger.info("Added 'back_filename' column to templates")
                if 'back_template_url' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_template_url TEXT"))
                    logger.info("Added 'back_template_url' column to templates")
                if 'back_font_settings' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_font_settings JSON"))
                    logger.info("Added 'back_font_settings' column to templates")
                if 'back_photo_settings' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_photo_settings JSON"))
                    logger.info("Added 'back_photo_settings' column to templates")
                if 'back_qr_settings' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_qr_settings JSON"))
                    logger.info("Added 'back_qr_settings' column to templates")
                if 'back_layout_config' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_layout_config TEXT"))
                    logger.info("Added 'back_layout_config' column to templates")
                if 'back_language' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_language VARCHAR(20) DEFAULT 'english'"))
                    logger.info("Added 'back_language' column to templates")
                if 'back_text_direction' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_text_direction VARCHAR(10) DEFAULT 'ltr'"))
                    logger.info("Added 'back_text_direction' column to templates")
                if 'is_double_sided' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN is_double_sided BOOLEAN DEFAULT 0"))
                    logger.info("Added 'is_double_sided' column to templates")
                if 'duplex_flip_mode' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN duplex_flip_mode VARCHAR(20) DEFAULT 'long_edge'"))
                    logger.info("Added 'duplex_flip_mode' column to templates")

                # List of new columns to check and add
                new_cols = [
                    ('deadline', 'DATETIME'),
                    ('card_width', 'INTEGER DEFAULT 1015'),
                    ('card_height', 'INTEGER DEFAULT 661'),
                    ('sheet_width', 'INTEGER DEFAULT 2480'),
                    ('sheet_height', 'INTEGER DEFAULT 3508'),
                    ('grid_rows', 'INTEGER DEFAULT 5'),
                    ('grid_cols', 'INTEGER DEFAULT 2')
                ]

                for col_name, col_type in new_cols:
                    if col_name not in t_columns:
                        try:
                            conn.execute(text(f"ALTER TABLE templates ADD COLUMN {col_name} {col_type}"))
                            logger.info(f"Added '{col_name}' column to templates")
                        except Exception as e:
                            logger.warning(f"Could not add {col_name}: {e}")

                # --- Migrate TEMPLATE_FIELDS table (legacy schema compatibility) ---
                # Some older databases created a `template_fields` table with only:
                # (template_id, field_id, display_order). The current app expects a richer schema
                # with an autoincrement `id` plus `field_name/field_label/field_type/...`.
                try:
                    table_names = set(inspector.get_table_names())
                    if "template_fields" in table_names:
                        tf_columns = [c["name"] for c in inspector.get_columns("template_fields")]
                        required_cols = {
                            "id",
                            "template_id",
                            "field_name",
                            "field_label",
                            "field_type",
                            "is_required",
                            "display_order",
                            "field_options",
                        }
                        if not required_cols.issubset(set(tf_columns)):
                            if db.engine.dialect.name == "sqlite":
                                legacy_name = "template_fields_legacy"
                                if legacy_name in table_names:
                                    legacy_name = f"template_fields_legacy_{int(time.time())}"

                                # SQLite can't add a PRIMARY KEY column via ALTER TABLE, so rebuild.
                                conn.execute(text("PRAGMA foreign_keys=OFF"))
                                conn.execute(text(f"ALTER TABLE template_fields RENAME TO {legacy_name}"))
                                conn.execute(text("""
                                    CREATE TABLE template_fields (
                                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                                        template_id INTEGER NOT NULL,
                                        field_name VARCHAR(100) NOT NULL,
                                        field_label VARCHAR(100) NOT NULL,
                                        field_type VARCHAR(50) NOT NULL,
                                        is_required BOOLEAN DEFAULT 0,
                                        show_label_front BOOLEAN DEFAULT 1,
                                        show_value_front BOOLEAN DEFAULT 1,
                                        show_label_back BOOLEAN DEFAULT 0,
                                        show_value_back BOOLEAN DEFAULT 0,
                                        display_order INTEGER DEFAULT 0,
                                        field_options JSON DEFAULT '[]',
                                        FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE
                                    )
                                """))

                                # Best-effort carryover (creates placeholder field names/labels).
                                conn.execute(text(f"""
                                    INSERT INTO template_fields (
                                        template_id, field_name, field_label, field_type, is_required, display_order, field_options
                                    )
                                    SELECT
                                        template_id,
                                        'field_' || COALESCE(CAST(field_id AS TEXT), '0'),
                                        'Field ' || COALESCE(CAST(field_id AS TEXT), '0'),
                                        'text',
                                        0,
                                        COALESCE(display_order, 0),
                                        '[]'
                                    FROM {legacy_name}
                                """))
                                conn.execute(text("PRAGMA foreign_keys=ON"))
                                logger.info(f"Migrated legacy template_fields schema -> new schema (kept old table as {legacy_name})")
                            else:
                                logger.warning("template_fields table schema is legacy/invalid; auto-migration is only implemented for SQLite")
                        else:
                            extra_tf_cols = [
                                ("show_label_front", "BOOLEAN DEFAULT 1"),
                                ("show_value_front", "BOOLEAN DEFAULT 1"),
                                ("show_label_back", "BOOLEAN DEFAULT 0"),
                                ("show_value_back", "BOOLEAN DEFAULT 0"),
                            ]
                            for col_name, col_type in extra_tf_cols:
                                if col_name not in tf_columns:
                                    try:
                                        conn.execute(text(f"ALTER TABLE template_fields ADD COLUMN {col_name} {col_type}"))
                                        logger.info(f"Added '{col_name}' column to template_fields")
                                    except Exception as inner_e:
                                        logger.warning(f"Could not add {col_name} to template_fields: {inner_e}")
                except Exception as e:
                    logger.warning(f"TemplateField migration skipped/failed: {e}")

                # --- Premium defaults backfill (safe/no-op for already populated rows) ---
                try:
                    conn.execute(text("UPDATE students SET verification_revoked = 0 WHERE verification_revoked IS NULL"))
                except Exception:
                    pass
                try:
                    conn.execute(text("UPDATE students SET photo_quality_score = 0 WHERE photo_quality_score IS NULL"))
                except Exception:
                    pass
                try:
                    conn.execute(text("UPDATE students SET photo_quality_status = 'unknown' WHERE photo_quality_status IS NULL"))
                except Exception:
                    pass
                try:
                    # Keep JSON fields non-null for premium settings UIs.
                    conn.execute(text("UPDATE templates SET qa_settings = '{}' WHERE qa_settings IS NULL"))
                    conn.execute(text("UPDATE templates SET batch_rules = '{}' WHERE batch_rules IS NULL"))
                    conn.execute(text("UPDATE templates SET localization_pack = '{}' WHERE localization_pack IS NULL"))
                    conn.execute(text("UPDATE templates SET language_lock_rules = '{}' WHERE language_lock_rules IS NULL"))
                    conn.execute(text("UPDATE templates SET branding_config = '{}' WHERE branding_config IS NULL"))
                    conn.execute(text("UPDATE templates SET print_profile = '{}' WHERE print_profile IS NULL"))
                    conn.execute(text("UPDATE templates SET verification_config = '{}' WHERE verification_config IS NULL"))
                except Exception:
                    pass

                conn.commit()

            # Ensure workflow row exists for all templates (backfill safe/no-op for existing)
            try:
                templates_all = Template.query.all()
                for _t in templates_all:
                    existing_wf = TemplateWorkflow.query.filter_by(template_id=_t.id).first()
                    if not existing_wf:
                        db.session.add(TemplateWorkflow(template_id=_t.id, state="draft", updated_by="migration", updated_role="system"))
                db.session.commit()
            except Exception as wf_e:
                db.session.rollback()
                logger.warning(f"Template workflow backfill skipped: {wf_e}")

        logger.info("Database migration check completed")
    except Exception as e:
        logger.error(f"Error during database migration: {e}")


def migrate_template_font_colors(db):
    """Migrate existing templates to use separate label and value font colors"""
    try:
        templates = Template.query.all()
        migrated_count = 0

        for template in templates:
            needs_update = False

            # Check if this template needs migration
            if 'font_color' in template.font_settings:
                # Migrate to separate colors
                if 'label_font_color' not in template.font_settings:
                    template.font_settings['label_font_color'] = template.font_settings['font_color']
                    needs_update = True
                if 'value_font_color' not in template.font_settings:
                    template.font_settings['value_font_color'] = template.font_settings['font_color']
                    needs_update = True

                if needs_update:
                    migrated_count += 1
                    logger.info(f"Migrated font colors for template {template.id}")

        db.session.commit()
        logger.info(f"Font color migration completed: {migrated_count} templates updated")

    except Exception as e:
        logger.error(f"Error during font color migration: {e}")


def migrate_photo_settings(db, get_default_photo_config):
    """Add missing photo config keys to old templates."""
    try:
        templates = Template.query.all()
        updated = 0
        default_photo = get_default_photo_config()

        for template in templates:
            if template.photo_settings is None:
                template.photo_settings = {}

            needs_update = False
            if "corel_editable_photo_mode" not in template.photo_settings:
                template.photo_settings["corel_editable_photo_mode"] = default_photo["corel_editable_photo_mode"]
                needs_update = True

            if needs_update:
                updated += 1

        if updated > 0:
            db.session.commit()
            logger.info(f"Migrated photo settings for {updated} templates")

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error migrating photo settings: {e}")


def repair_student_photo_url_recursion(db):
    """
    Repair bad student records where `photo_url` was accidentally overwritten with the generated
    card image URL (`image_url`). This causes the "thumbnail card inside card" bug when regenerating.

    We can often restore the real photo URL from `photo_filename` (bulk/legacy flows sometimes store
    Cloudinary photo URLs there).
    """
    from models import Student

    try:
        # Only scan records that have both URLs set.
        candidates = Student.query.filter(
            Student.photo_url.isnot(None),
            Student.image_url.isnot(None),
        ).all()

        fixed = 0
        for s in candidates:
            try:
                if not s.photo_url or not s.image_url:
                    continue
                if str(s.photo_url) != str(s.image_url):
                    continue

                # If photo_filename contains a URL, restore from it.
                if getattr(s, "photo_filename", None) and str(s.photo_filename).startswith("http"):
                    s.photo_url = str(s.photo_filename)
                    fixed += 1
                    continue

                # Otherwise, clear photo_url so rendering can fall back to local filename/placeholder.
                s.photo_url = None
                fixed += 1
            except Exception:
                continue

        if fixed:
            db.session.commit()
            logger.info(f"Repaired {fixed} student records with photo_url == image_url")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error repairing student photo_url recursion: {e}")
def add_verification_index(db):
    """Add index on student data_hash for faster verification lookups."""
    try:
        from sqlalchemy import text
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_students_data_hash ON students(data_hash)"
        ))
        db.session.commit()
        logger.info("Added ix_students_data_hash index")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not add data_hash index: {e}")
