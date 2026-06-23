"""
Template management operations.

Extracted from app/legacy_app.py to separate template CRUD operations
from the main application module.
"""
import logging
import os
from datetime import datetime, timezone

from models import Template

logger = logging.getLogger(__name__)


def resolve_student_card_preview_urls(student, GENERATED_FOLDER):
    """Return front/back preview URLs for a generated student card."""
    from flask import url_for

    preview_url = None
    back_preview_url = None

    if getattr(student, "image_url", None):
        preview_url = student.image_url
    elif getattr(student, "generated_filename", None):
        preview_filename = str(student.generated_filename)
        if preview_filename.lower().endswith(".pdf"):
            preview_filename = preview_filename[:-4] + ".jpg"
        preview_path = os.path.join(GENERATED_FOLDER, preview_filename)
        if os.path.exists(preview_path):
            preview_url = url_for("static", filename=f"generated/{preview_filename}")

    if getattr(student, "back_image_url", None):
        back_preview_url = student.back_image_url
    elif getattr(student, "back_generated_filename", None):
        back_preview_path = os.path.join(GENERATED_FOLDER, str(student.back_generated_filename))
        if os.path.exists(back_preview_path):
            back_preview_url = url_for("static", filename=f"generated/{student.back_generated_filename}")

    return preview_url, back_preview_url


def load_static_back_template_image(template_obj, card_width, card_height, get_template_path, load_template_smart):
    """Load the back template image for static back-side rendering."""
    if not template_obj or not getattr(template_obj, "is_double_sided", False):
        return None

    back_template_path = get_template_path(template_obj.id, side="back")
    if not back_template_path:
        return None

    try:
        return load_template_smart(back_template_path).resize((card_width, card_height))
    except Exception as e:
        logger.warning(f"Failed to load back template for template {template_obj.id}: {e}")
        return None


def add_template(
    db,
    filename,
    school_name,
    card_orientation='landscape',
    language='english',
    text_direction='ltr',
    *,
    is_double_sided=False,
    back_filename=None,
    back_template_url=None,
    back_language=None,
    back_text_direction=None,
    get_default_font_config,
    get_default_photo_config,
    get_default_qr_config,
):
    try:
        # --- Default Dimensions based on Orientation ---
        # CR80 Defaults: 1015x661 (Landscape) or 661x1015 (Portrait)
        if card_orientation == 'portrait':
            width, height = 661, 1015
            rows, cols = 2, 5  # 2 Rows, 5 Cols on A4 Landscape
        else:
            width, height = 1015, 661
            rows, cols = 5, 2  # 5 Rows, 2 Cols on A4 Portrait

        template = Template(
            filename=filename,
            back_filename=back_filename,
            back_template_url=back_template_url,
            school_name=school_name,
            font_settings=get_default_font_config(),
            photo_settings=get_default_photo_config(),
            qr_settings=get_default_qr_config(),
            back_font_settings=get_default_font_config(),
            back_photo_settings=get_default_photo_config(),
            back_qr_settings=get_default_qr_config(),
            card_orientation=card_orientation,
            language=language,
            text_direction=text_direction,
            back_language=(back_language or language),
            back_text_direction=(back_text_direction or text_direction),
            is_double_sided=bool(is_double_sided),

            # --- NEW: Save Dimensions & Grid ---
            card_width=width,
            card_height=height,
            sheet_width=2480,  # Default A4 @ 300 DPI
            sheet_height=3508,  # Default A4 @ 300 DPI
            grid_rows=rows,
            grid_cols=cols,
            # -----------------------------------

            created_at=datetime.now(timezone.utc)
        )
        db.session.add(template)
        db.session.commit()

        logger.info(f"Added template: {filename} ({width}x{height})")
        return template.id
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding template: {e}")
        raise


def add_template_cloudinary(
    db,
    template_url,
    school_name,
    card_orientation='landscape',
    language='english',
    text_direction='ltr',
    filename=None,
    *,
    is_double_sided=False,
    back_filename=None,
    back_template_url=None,
    back_language=None,
    back_text_direction=None,
    get_default_font_config,
    get_default_photo_config,
    get_default_qr_config,
):
    """
    Add a template with Cloudinary URL (no local file storage).

    Args:
        template_url (str): Cloudinary secure URL for the template
        school_name (str): School name
        card_orientation (str): 'landscape' or 'portrait'
        language (str): Language for labels
        text_direction (str): 'ltr' or 'rtl'
        filename (str | None): Optional local backup path under static/

    Returns:
        int: Template ID
    """
    try:
        # --- Default Dimensions based on Orientation ---
        if card_orientation == 'portrait':
            width, height = 661, 1015
            rows, cols = 2, 5
        else:
            width, height = 1015, 661
            rows, cols = 5, 2

        template = Template(
            filename=filename,
            template_url=template_url,
            back_filename=back_filename,
            back_template_url=back_template_url,
            school_name=school_name,
            font_settings=get_default_font_config(),
            photo_settings=get_default_photo_config(),
            qr_settings=get_default_qr_config(),
            back_font_settings=get_default_font_config(),
            back_photo_settings=get_default_photo_config(),
            back_qr_settings=get_default_qr_config(),
            card_orientation=card_orientation,
            language=language,
            text_direction=text_direction,
            back_language=(back_language or language),
            back_text_direction=(back_text_direction or text_direction),
            is_double_sided=bool(is_double_sided),
            card_width=width,
            card_height=height,
            sheet_width=2480,
            sheet_height=3508,
            grid_rows=rows,
            grid_cols=cols,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(template)
        db.session.commit()

        safe_url = str(template_url or "Local Fallback")
        logger.info(f"Added Cloudinary template: {safe_url[:50]}... ({width}x{height})")
        return template.id
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding Cloudinary template: {e}")
        raise


def get_templates(db, session, get_default_font_config, get_default_photo_config, get_default_qr_config):
    try:
        query = db.session.query(Template).order_by(Template.created_at.desc())

        # RBAC Filtering: School admins only see their assigned school
        if session.get("admin") and session.get("admin_role") == "school_admin":
            if session.get("admin_school"):
                query = query.filter_by(school_name=session.get("admin_school"))

        templates = query.all()
        result = []

        for template in templates:
            font_settings = get_default_font_config()
            photo_settings = get_default_photo_config()
            qr_settings = get_default_qr_config()
            source_path = template.filename or template.template_url or ""
            source_basename = os.path.basename(source_path.split("?", 1)[0]) if source_path else ""
            if source_basename and len(source_basename) > 90:
                source_basename = source_basename[:87] + "..."
            back_source_path = template.back_filename or template.back_template_url or ""
            back_source_basename = os.path.basename(back_source_path.split("?", 1)[0]) if back_source_path else ""
            if back_source_basename and len(back_source_basename) > 90:
                back_source_basename = back_source_basename[:87] + "..."

            # Merge with template settings
            if template.font_settings:
                loaded_font = template.font_settings.copy()
                # Handle legacy font_color field
                if 'font_color' in loaded_font and 'label_font_color' not in loaded_font:
                    loaded_font['label_font_color'] = loaded_font['font_color']
                    loaded_font['value_font_color'] = loaded_font['font_color']
                font_settings = {**font_settings, **loaded_font}

            if template.photo_settings:
                photo_settings = {**photo_settings, **template.photo_settings}

            if template.qr_settings:
                qr_settings = {**qr_settings, **template.qr_settings}
            back_font_settings = {**get_default_font_config(), **(template.back_font_settings or {})}
            back_photo_settings = {**get_default_photo_config(), **(template.back_photo_settings or {})}
            back_qr_settings = {**get_default_qr_config(), **(template.back_qr_settings or {})}

            # Serialize fields for frontend
            template_fields = []
            if template.fields:
                for field in template.fields:
                    template_fields.append({
                        'field_name': field.field_name,
                        'field_label': field.field_label,
                        'field_type': field.field_type,
                        'is_required': field.is_required,
                        'show_label_front': bool(getattr(field, 'show_label_front', True)),
                        'show_value_front': bool(getattr(field, 'show_value_front', True)),
                        'show_label_back': bool(getattr(field, 'show_label_back', False)),
                        'show_value_back': bool(getattr(field, 'show_value_back', False)),
                        'display_order': field.display_order,
                        'field_options': field.field_options
                    })
                # Sort by display order so they appear correctly on the form
                template_fields.sort(key=lambda x: int(x.get('display_order') or 0))

            result.append({
                'id': template.id,
                'filename': template.filename,
                'template_url': template.template_url,
                'back_filename': template.back_filename,
                'back_template_url': template.back_template_url,
                'source_path': source_path,
                'source_name': source_basename or "No source",
                'back_source_path': back_source_path,
                'back_source_name': back_source_basename or "No back source",
                'school_name': template.school_name,
                'created_at': template.created_at.isoformat() if template.created_at else datetime.now(timezone.utc).isoformat(),
                'font_settings': font_settings,
                'photo_settings': photo_settings,
                'qr_settings': qr_settings,
                'back_font_settings': back_font_settings,
                'back_photo_settings': back_photo_settings,
                'back_qr_settings': back_qr_settings,
                'card_orientation': template.card_orientation or 'landscape',
                'language': template.language or 'english',
                'text_direction': template.text_direction or 'ltr',
                'back_language': template.back_language or template.language or 'english',
                'back_text_direction': template.back_text_direction or template.text_direction or 'ltr',
                'back_layout_config': template.back_layout_config,
                'is_double_sided': bool(template.is_double_sided),
                'duplex_flip_mode': template.duplex_flip_mode or 'long_edge',
                'deadline': template.deadline.isoformat() if template.deadline else None,
                'fields': template_fields,
                'card_width': template.card_width or 1015,
                'card_height': template.card_height or 661,
                'sheet_width': template.sheet_width or 2480,
                'sheet_height': template.sheet_height or 3508,
                'grid_rows': template.grid_rows or 5,
                'grid_cols': template.grid_cols or 2
            })

        return result
    except Exception as e:
        logger.error(f"Error fetching templates: {e}")
        return []  # Always return empty list on error


def update_template_settings(
    db,
    template_id,
    font_settings=None,
    photo_settings=None,
    qr_settings=None,
    card_orientation=None,
    card_dims=None,
    sheet_dims=None,
    grid_layout=None,
    back_font_settings=None,
    back_photo_settings=None,
    back_qr_settings=None,
    is_double_sided=None,
    duplex_flip_mode=None,
    get_default_font_config=None,
    get_default_photo_config=None,
    get_default_qr_config=None,
    create_template_version_snapshot=None,
    log_immutable_audit_event=None,
    get_session_actor=None,
    log_activity=None,
):
    """Update template settings in the database."""
    try:
        template = db.session.get(Template, template_id)

        if not template:
            logger.error(f"Template {template_id} not found")
            return

        if font_settings is not None:
            default_font = get_default_font_config()
            complete_font_settings = {**default_font, **font_settings}
            template.font_settings = complete_font_settings

        if photo_settings is not None:
            default_photo = get_default_photo_config()
            complete_photo_settings = {**default_photo, **photo_settings}
            template.photo_settings = complete_photo_settings

        if qr_settings is not None:
            default_qr = get_default_qr_config()
            complete_qr_settings = {**default_qr, **qr_settings}
            template.qr_settings = complete_qr_settings
        if back_font_settings is not None:
            default_font = get_default_font_config()
            template.back_font_settings = {**default_font, **back_font_settings}
        if back_photo_settings is not None:
            default_photo = get_default_photo_config()
            template.back_photo_settings = {**default_photo, **back_photo_settings}
        if back_qr_settings is not None:
            default_qr = get_default_qr_config()
            template.back_qr_settings = {**default_qr, **back_qr_settings}

        if card_orientation is not None:
            template.card_orientation = card_orientation

        # --- UPDATE DIMENSIONS ---
        if card_dims:
            # Expecting dict like {'width': 1015, 'height': 661}
            template.card_width = card_dims.get('width', 1015)
            template.card_height = card_dims.get('height', 661)

        if sheet_dims:
            # Expecting dict like {'width': 2480, 'height': 3508}
            template.sheet_width = sheet_dims.get('width', 2480)
            template.sheet_height = sheet_dims.get('height', 3508)

        # --- NEW: UPDATE GRID LAYOUT ---
        if grid_layout:
            # Expecting dict like {'rows': 5, 'cols': 2}
            template.grid_rows = grid_layout.get('rows', 5)
            template.grid_cols = grid_layout.get('cols', 2)
        if is_double_sided is not None:
            template.is_double_sided = bool(is_double_sided)
        if duplex_flip_mode:
            template.duplex_flip_mode = duplex_flip_mode
        # -------------------------------

        db.session.commit()
        try:
            actor, actor_role = get_session_actor()
            create_template_version_snapshot(template, source="update_template_settings", actor=actor, actor_role=actor_role)
            log_immutable_audit_event(
                entity_type="template",
                entity_id=template.id,
                action="template_settings_updated",
                payload={"template_id": template.id, "card_orientation": template.card_orientation},
                actor=actor,
                actor_role=actor_role,
            )
            db.session.commit()
        except Exception as lifecycle_exc:
            db.session.rollback()
            logger.warning("Template lifecycle hooks failed for template %s: %s", template_id, lifecycle_exc)
        log_activity("Updated Template Settings", target=f"Template {template_id}",
                     details=f"Orientation: {card_orientation}")

        logger.info(f"Updated settings for template ID {template_id}, orientation: {card_orientation}")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating template settings: {e}")
        raise
