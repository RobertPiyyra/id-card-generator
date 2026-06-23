# SQLAlchemy imports
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, JSON, Float
from sqlalchemy.orm import relationship, backref
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.sql import func
from sqlalchemy import text, inspect
from datetime import datetime, timezone

db = SQLAlchemy()

# ================== Database Models ==================

class Template(db.Model):
    __tablename__ = 'templates'
    
    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=True)  # Legacy: 😎kept for backward compatibility
    template_url = Column(Text, nullable=True)  # NEW: Cloudinary URL for the template image
    back_filename = Column(String(255), nullable=True)
    back_template_url = Column(Text, nullable=True)
    school_name = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Settings stored as JSON
    font_settings = Column(MutableDict.as_mutable(JSON), default=dict)
    photo_settings = Column(MutableDict.as_mutable(JSON), default=dict)
    qr_settings = Column(MutableDict.as_mutable(JSON), default=dict)
    back_font_settings = Column(MutableDict.as_mutable(JSON), default=dict)
    back_photo_settings = Column(MutableDict.as_mutable(JSON), default=dict)
    back_qr_settings = Column(MutableDict.as_mutable(JSON), default=dict)
    
    card_orientation = Column(String(20), default='landscape')
    deadline = Column(DateTime, nullable=True)
    is_double_sided = Column(Boolean, default=False)
    duplex_flip_mode = Column(String(20), default='long_edge')

    # --- ADD THESE TWO LINES ---
    language = db.Column(db.String(20), default='english')
    text_direction = db.Column(db.String(10), default='ltr')
    back_language = db.Column(db.String(20), default='english')
    back_text_direction = db.Column(db.String(10), default='ltr')
    # ---------------------------

    # --- NEW: Custom Dimensions (in pixels @ 300 DPI) ---
    # Default is CR80 size (1015x661) and A4 sheet (2480x3508)
    card_width = Column(Integer, default=1015)
    card_height = Column(Integer, default=661)
    sheet_width = Column(Integer, default=2480) # A4 @ 300 DPI
    sheet_height = Column(Integer, default=3508)

    # --- NEW: Custom Grid Layout ---
    grid_rows = Column(Integer, default=5) # Default 5 rows
    grid_cols = Column(Integer, default=2) # Default 2 cols

    layout_config = db.Column(db.Text, nullable=True)  # JSON string for layout configuration
    back_layout_config = db.Column(db.Text, nullable=True)
    qa_settings = Column(MutableDict.as_mutable(JSON), default=dict)
    batch_rules = Column(MutableDict.as_mutable(JSON), default=dict)
    localization_pack = Column(MutableDict.as_mutable(JSON), default=dict)
    language_lock_rules = Column(MutableDict.as_mutable(JSON), default=dict)
    branding_config = Column(MutableDict.as_mutable(JSON), default=dict)
    print_profile = Column(MutableDict.as_mutable(JSON), default=dict)
    verification_config = Column(MutableDict.as_mutable(JSON), default=dict)

    # Relationships
    students = relationship('Student', backref='template_rel', lazy='dynamic')
    fields = relationship('TemplateField', backref='template', cascade='all, delete-orphan')

class TemplateField(db.Model):
    __tablename__ = 'template_fields'
    
    id = Column(Integer, primary_key=True)
    template_id = Column(Integer, ForeignKey('templates.id', ondelete='CASCADE'), nullable=False)
    field_name = Column(String(100), nullable=False)
    field_label = Column(String(100), nullable=False)
    field_type = Column(String(50), nullable=False)  # text, number, select, etc.
    is_required = Column(Boolean, default=False)
    show_label_front = Column(Boolean, default=True)
    show_value_front = Column(Boolean, default=True)
    show_label_back = Column(Boolean, default=False)
    show_value_back = Column(Boolean, default=False)
    display_order = Column(Integer, default=0)
    field_options = Column(JSON, default=list)  # For select fields

class Student(db.Model):
    __tablename__ = 'students'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    father_name = Column(String(255))
    class_name = Column(String(100))
    dob = Column(String(50))
    address = Column(Text)
    phone = Column(String(50))
    
    # Files (store URLs instead of local filenames)
    photo_url = Column(String(1024))
    image_url = Column(String(1024))  # Points to individual card image (JPG) stored on Cloudinary
    back_image_url = Column(String(1024))
    pdf_url = Column(String(1024))
    
    # Legacy fields (for backward compatibility - DO NOT DELETE)
    photo_filename = Column(String(255))  # Legacy: local photo filename
    generated_filename = Column(String(255))  # Legacy: local generated card filename
    back_generated_filename = Column(String(255))
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    data_hash = Column(String(255), unique=True)
    
    # Relationships
    template_id = Column(Integer, ForeignKey('templates.id'), index=True)
    school_name = Column(String(255), index=True)
    
    # User Auth
    email = Column(String(255), unique=False, index=True)
    password = Column(String(255))
    
    # Dynamic Data
    custom_data = Column(MutableDict.as_mutable(JSON), default=dict)
    
    # Sheet Tracking (Legacy support)
    sheet_filename = Column(String(255)) 
    sheet_position = Column(Integer)
    verification_revoked = Column(Boolean, default=False)
    photo_quality_score = Column(Float, default=0.0)
    photo_quality_status = Column(String(20), default='unknown')


# ================== Activity Log Model ==================
class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    actor = db.Column(db.String(100))        # Who performed the action (Email or 'Admin')
    action = db.Column(db.String(100))       # What action was taken (e.g., "Deleted Student")
    target = db.Column(db.String(100))       # Target ID or Name (optional)
    details = db.Column(db.String(255))      # Additional details
    ip_address = db.Column(db.String(50))    # User's IP address
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


# ================== Notification Preferences Model ==================
class NotificationPreference(db.Model):
    __tablename__ = 'notification_preferences'
    
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False)
    
    # Notification channels
    email_enabled = db.Column(db.Boolean, default=True)
    sms_enabled = db.Column(db.Boolean, default=True)
    
    # Notification types
    notify_deadline_approaching = db.Column(db.Boolean, default=True)  # 3 days before
    notify_card_ready = db.Column(db.Boolean, default=True)
    notify_errors = db.Column(db.Boolean, default=True)
    
    # SMS phone number
    phone_number = db.Column(db.String(20))
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    student = db.relationship('Student', backref=db.backref('notification_preference', uselist=False))


# ================== Notification Log Model ==================
class NotificationLog(db.Model):
    __tablename__ = 'notification_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    recipient_email = db.Column(db.String(255))
    recipient_phone = db.Column(db.String(20))
    
    notification_type = db.Column(db.String(50))  # 'deadline', 'card_ready', 'error'
    channel = db.Column(db.String(20))  # 'email' or 'sms'
    
    subject = db.Column(db.String(255))
    message = db.Column(db.Text)
    
    status = db.Column(db.String(20), default='pending')  # 'pending', 'sent', 'failed'
    error_message = db.Column(db.Text)
    
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=True)
    template_id = db.Column(db.Integer, db.ForeignKey('templates.id'), nullable=True)
    
    sent_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    student = db.relationship('Student', backref=db.backref('notifications', lazy=True))
    template = db.relationship('Template', backref=db.backref('notifications', lazy=True))


# ================== Keyboard Language Preference Model ==================
class KeyboardLanguagePreference(db.Model):
    __tablename__ = 'keyboard_language_preferences'
    
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('templates.id'), nullable=False)
    
    # Language and keyboard mapping
    language = db.Column(db.String(50), nullable=False)  # e.g., 'english', 'urdu', 'arabic', 'hindi'
    keyboard_layout = db.Column(db.String(100))  # e.g., 'en-US', 'ur', 'ar', 'hi'
    font_family = db.Column(db.String(100))
    
    # Auto-switch enabled for this template
    auto_switch_enabled = db.Column(db.Boolean, default=True)
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    template = db.relationship('Template', backref=db.backref('keyboard_preferences', lazy=True))

# ================== Admin User Model ==================
class AdminUser(db.Model):
    __tablename__ = 'admin_users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default='school_admin')  # e.g., 'super_admin' or 'school_admin'
    school_name = db.Column(db.String(255), nullable=True, index=True)   # Scopes access for school_admin
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ================== Serial Batch Models ==================
class SerialBatch(db.Model):
    __tablename__ = 'serial_batches'

    id = db.Column(db.Integer, primary_key=True)
    school_name = db.Column(db.String(255), nullable=False, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey('templates.id'), nullable=False)
    prefix = db.Column(db.String(50), default='SCH-')
    status = db.Column(db.String(30), default='uploading')  # uploading, ready, filling, rendering, done, error
    created_by = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    template = db.relationship('Template', backref=db.backref('serial_batches', lazy='dynamic'))
    cards = db.relationship('SerialCard', backref='batch', lazy='dynamic', cascade='all, delete-orphan')


class SerialCard(db.Model):
    __tablename__ = 'serial_cards'

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('serial_batches.id', ondelete='CASCADE'), nullable=False, index=True)
    serial_no = db.Column(db.String(50), nullable=False)  # e.g. "SCH-001"
    photo_path = db.Column(db.String(500))
    photo_thumbnail = db.Column(db.String(500))
    name = db.Column(db.String(255))
    father_name = db.Column(db.String(255))
    class_name = db.Column(db.String(100))
    dob = db.Column(db.String(50))
    address = db.Column(db.Text)
    phone = db.Column(db.String(50))
    custom_data = db.Column(MutableDict.as_mutable(JSON), default=dict)
    status = db.Column(db.String(30), default='photo_only')  # photo_only, details_filled, rendered, error
    error_message = db.Column(db.Text)
    rendered_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint('batch_id', 'serial_no', name='uq_batch_serial'),
    )


# ================== Phase-1 Premium Models ==================
class TemplateVersion(db.Model):
    __tablename__ = 'template_versions'

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('templates.id', ondelete='CASCADE'), nullable=False, index=True)
    version_number = db.Column(db.Integer, nullable=False, default=1)
    snapshot_json = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    source = db.Column(db.String(80), nullable=True)  # e.g., admin_settings, visual_editor, rollback
    created_by = db.Column(db.String(255), nullable=True)
    created_role = db.Column(db.String(80), nullable=True)
    rollback_of_version_id = db.Column(db.Integer, nullable=True)
    checksum = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    template = db.relationship('Template', backref=db.backref('versions', lazy='dynamic', cascade='all, delete-orphan'))


class TemplateWorkflow(db.Model):
    __tablename__ = 'template_workflows'

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('templates.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    state = db.Column(db.String(32), nullable=False, default='draft')  # draft, review, approved, published
    updated_by = db.Column(db.String(255), nullable=True)
    updated_role = db.Column(db.String(80), nullable=True)
    note = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    template = db.relationship('Template', backref=db.backref('workflow', uselist=False, cascade='all, delete-orphan'))


class ImmutableAuditEvent(db.Model):
    __tablename__ = 'immutable_audit_events'

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(80), nullable=False, index=True)  # template, bulk_job, etc.
    entity_id = db.Column(db.String(80), nullable=False, index=True)
    action = db.Column(db.String(120), nullable=False)
    actor = db.Column(db.String(255), nullable=True)
    actor_role = db.Column(db.String(80), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    payload_json = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    prev_event_hash = db.Column(db.String(128), nullable=True)
    event_hash = db.Column(db.String(128), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)


class BulkJob(db.Model):
    __tablename__ = 'bulk_jobs'

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('templates.id', ondelete='SET NULL'), nullable=True, index=True)
    job_type = db.Column(db.String(80), nullable=False, default='bulk_card_generation')
    status = db.Column(db.String(32), nullable=False, default='draft')  # draft, review, approved, published, processing, completed, failed
    total_items = db.Column(db.Integer, nullable=False, default=0)
    processed_items = db.Column(db.Integer, nullable=False, default=0)
    failed_items = db.Column(db.Integer, nullable=False, default=0)
    meta_json = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    created_by = db.Column(db.String(255), nullable=True)
    created_role = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, onupdate=lambda: datetime.now(timezone.utc))

    template = db.relationship('Template', backref=db.backref('bulk_jobs', lazy='dynamic'))


class BulkJobItem(db.Model):
    __tablename__ = 'bulk_job_items'

    id = db.Column(db.Integer, primary_key=True)
    bulk_job_id = db.Column(db.Integer, db.ForeignKey('bulk_jobs.id', ondelete='CASCADE'), nullable=False, index=True)
    row_index = db.Column(db.Integer, nullable=False, default=0)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='SET NULL'), nullable=True, index=True)
    status = db.Column(db.String(32), nullable=False, default='pending')  # pending, processing, completed, failed, skipped
    error_message = db.Column(db.Text, nullable=True)
    payload_json = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, onupdate=lambda: datetime.now(timezone.utc))

    bulk_job = db.relationship('BulkJob', backref=db.backref('items', lazy='dynamic', cascade='all, delete-orphan'))
    student = db.relationship('Student', backref=db.backref('bulk_job_items', lazy='dynamic'))


class VerificationAudit(db.Model):
    __tablename__ = 'verification_audits'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='SET NULL'), nullable=True, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey('templates.id', ondelete='SET NULL'), nullable=True, index=True)
    token_id = db.Column(db.String(128), nullable=True, index=True)
    status = db.Column(db.String(32), nullable=False, default='ok')  # ok, revoked, expired, invalid, tampered
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    details_json = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    student = db.relationship('Student', backref=db.backref('verification_events', lazy='dynamic'))
    template = db.relationship('Template', backref=db.backref('verification_events', lazy='dynamic'))


class ImportMapping(db.Model):
    __tablename__ = 'import_mappings'

    id = db.Column(db.Integer, primary_key=True)
    school_name = db.Column(db.String(255), nullable=True, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey('templates.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    mapping_json = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    created_by = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, onupdate=lambda: datetime.now(timezone.utc))

    template = db.relationship('Template', backref=db.backref('import_mappings', lazy='dynamic', cascade='all, delete-orphan'))


class DisasterRecoverySnapshot(db.Model):
    __tablename__ = 'dr_snapshots'

    id = db.Column(db.Integer, primary_key=True)
    snapshot_name = db.Column(db.String(160), nullable=False)
    scope = db.Column(db.String(64), nullable=False, default='full')
    payload_json = db.Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)
    created_by = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


# ================== Enterprise Extension Models ==================

class Organization(db.Model):
    __tablename__ = 'organizations'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True)
    slug = db.Column(db.String(100), nullable=False, unique=True)
    logo_url = db.Column(db.String(1024))
    primary_color = db.Column(db.String(7), default='#2563eb')
    dark_mode_default = db.Column(db.Boolean, default=False)
    max_users = db.Column(db.Integer, default=10)
    max_templates = db.Column(db.Integer, default=50)
    max_cards_per_month = db.Column(db.Integer, default=10000)
    features_json = db.Column(MutableDict.as_mutable(JSON), default=dict)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    branches = relationship('Branch', backref='organization', lazy='dynamic', cascade='all, delete-orphan')
    departments = relationship('Department', backref='organization', lazy='dynamic', cascade='all, delete-orphan')
    api_keys = relationship('ApiKey', backref='organization', lazy='dynamic', cascade='all, delete-orphan')
    access_policies = relationship('AccessPolicy', backref='organization', lazy='dynamic', cascade='all, delete-orphan')


class Branch(db.Model):
    __tablename__ = 'branches'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    code = db.Column(db.String(20))
    address = db.Column(db.Text)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    departments = relationship('Department', backref='branch', lazy='dynamic', cascade='all, delete-orphan')


class Department(db.Model):
    __tablename__ = 'departments'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    branch_id = db.Column(db.Integer, ForeignKey('branches.id', ondelete='SET NULL'), nullable=True, index=True)
    parent_id = db.Column(db.Integer, ForeignKey('departments.id', ondelete='SET NULL'), nullable=True, index=True)
    name = db.Column(db.String(255), nullable=False)
    code = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    children = relationship('Department', backref=backref('parent', remote_side=[id]), lazy='dynamic')


class LoginHistory(db.Model):
    __tablename__ = 'login_history'

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, ForeignKey('admin_users.id', ondelete='SET NULL'), nullable=True, index=True)
    username = db.Column(db.String(100), nullable=False, index=True)
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(512))
    device_type = db.Column(db.String(50))
    browser = db.Column(db.String(100))
    os = db.Column(db.String(100))
    country = db.Column(db.String(100))
    login_success = db.Column(db.Boolean, default=True)
    failure_reason = db.Column(db.String(255))
    session_token = db.Column(db.String(255), index=True)
    logged_out_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    admin = relationship('AdminUser', backref=backref('login_history', lazy='dynamic'))


class UserSession(db.Model):
    __tablename__ = 'user_sessions'

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, ForeignKey('admin_users.id', ondelete='CASCADE'), nullable=False, index=True)
    session_token = db.Column(db.String(255), nullable=False, unique=True, index=True)
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(512))
    device_fingerprint = db.Column(db.String(128))
    two_factor_verified = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    last_activity_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    admin = relationship('AdminUser', backref=backref('sessions', lazy='dynamic', cascade='all, delete-orphan'))


class TwoFactorBackupCode(db.Model):
    __tablename__ = 'two_factor_backup_codes'

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, ForeignKey('admin_users.id', ondelete='CASCADE'), nullable=False, index=True)
    code_hash = db.Column(db.String(255), nullable=False)
    used = db.Column(db.Boolean, default=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    admin = relationship('AdminUser', backref=backref('backup_codes', lazy='dynamic', cascade='all, delete-orphan'))


class ApiKey(db.Model):
    __tablename__ = 'api_keys'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    key_prefix = db.Column(db.String(8), nullable=False, index=True)
    key_hash = db.Column(db.String(255), nullable=False, unique=True)
    scopes = db.Column(MutableDict.as_mutable(JSON), default=dict)
    rate_limit = db.Column(db.Integer, default=1000)
    request_count = db.Column(db.Integer, default=0)
    last_used_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    logs = relationship('ApiKeyLog', backref='api_key', lazy='dynamic', cascade='all, delete-orphan')


class ApiKeyLog(db.Model):
    __tablename__ = 'api_key_logs'

    id = db.Column(db.Integer, primary_key=True)
    api_key_id = db.Column(db.Integer, ForeignKey('api_keys.id', ondelete='CASCADE'), nullable=False, index=True)
    method = db.Column(db.String(10))
    path = db.Column(db.String(512))
    status_code = db.Column(db.Integer)
    ip_address = db.Column(db.String(64))
    response_time_ms = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class WebhookEndpoint(db.Model):
    __tablename__ = 'webhook_endpoints'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(512), nullable=False)
    secret = db.Column(db.String(255))
    events = db.Column(MutableDict.as_mutable(JSON), default=list)
    is_active = db.Column(db.Boolean, default=True)
    last_triggered_at = db.Column(db.DateTime, nullable=True)
    last_status_code = db.Column(db.Integer, nullable=True)
    failure_count = db.Column(db.Integer, default=0)
    created_by = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    deliveries = relationship('WebhookDelivery', backref='webhook', lazy='dynamic', cascade='all, delete-orphan')


class WebhookDelivery(db.Model):
    __tablename__ = 'webhook_deliveries'

    id = db.Column(db.Integer, primary_key=True)
    webhook_id = db.Column(db.Integer, ForeignKey('webhook_endpoints.id', ondelete='CASCADE'), nullable=False, index=True)
    event_type = db.Column(db.String(100), nullable=False)
    payload_json = db.Column(MutableDict.as_mutable(JSON), default=dict)
    status_code = db.Column(db.Integer, nullable=True)
    response_body = db.Column(db.Text)
    retry_count = db.Column(db.Integer, default=0)
    delivered = db.Column(db.Boolean, default=False)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class AccessPolicy(db.Model):
    __tablename__ = 'access_policies'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    role = db.Column(db.String(50), nullable=False)
    resource = db.Column(db.String(100), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    allowed = db.Column(db.Boolean, default=True)
    conditions_json = db.Column(MutableDict.as_mutable(JSON), default=dict)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class SystemMetric(db.Model):
    __tablename__ = 'system_metrics'

    id = db.Column(db.Integer, primary_key=True)
    metric_name = db.Column(db.String(100), nullable=False, index=True)
    metric_value = db.Column(db.Float, nullable=False)
    metric_unit = db.Column(db.String(30))
    labels_json = db.Column(MutableDict.as_mutable(JSON), default=dict)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class DataArchive(db.Model):
    __tablename__ = 'data_archives'

    id = db.Column(db.Integer, primary_key=True)
    archive_name = db.Column(db.String(200), nullable=False)
    entity_type = db.Column(db.String(80), nullable=False)
    entity_ids_json = db.Column(MutableDict.as_mutable(JSON), default=list)
    file_path = db.Column(db.String(512))
    file_size_bytes = db.Column(db.Integer, default=0)
    record_count = db.Column(db.Integer, default=0)
    compressed = db.Column(db.Boolean, default=True)
    restored_at = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class ScheduledTask(db.Model):
    __tablename__ = 'scheduled_tasks'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    task_type = db.Column(db.String(80), nullable=False)
    cron_expression = db.Column(db.String(100))
    params_json = db.Column(MutableDict.as_mutable(JSON), default=dict)
    is_active = db.Column(db.Boolean, default=True)
    last_run_at = db.Column(db.DateTime, nullable=True)
    last_run_status = db.Column(db.String(20))
    last_run_output = db.Column(db.Text)
    run_count = db.Column(db.Integer, default=0)
    fail_count = db.Column(db.Integer, default=0)
    next_run_at = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class OcrResult(db.Model):
    __tablename__ = 'ocr_results'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, ForeignKey('students.id', ondelete='SET NULL'), nullable=True, index=True)
    source_image_url = db.Column(db.String(1024))
    extracted_text = db.Column(db.Text)
    extracted_fields = db.Column(MutableDict.as_mutable(JSON), default=dict)
    confidence_score = db.Column(db.Float, default=0.0)
    processing_time_ms = db.Column(db.Float)
    model_used = db.Column(db.String(100))
    verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    student = relationship('Student', backref=backref('ocr_results', lazy='dynamic'))
# ================== Print Queue Models ==================

class PrintQueue(db.Model):
    __tablename__ = 'print_queue'
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('templates.id'), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=True, index=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('admin_users.id'), nullable=True)
    job_type = db.Column(db.String(20), default='single')
    priority = db.Column(db.Integer, default=5)
    status = db.Column(db.String(20), default='pending')
    printer_name = db.Column(db.String(100))
    card_side = db.Column(db.String(10), default='front')
    copies = db.Column(db.Integer, default=1)
    error_message = db.Column(db.Text)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    template = relationship('Template', backref=db.backref('print_jobs', lazy='dynamic'))
    student = relationship('Student', backref=db.backref('print_jobs', lazy='dynamic'))


class PrintHistory(db.Model):
    __tablename__ = 'print_history'
    id = db.Column(db.Integer, primary_key=True)
    print_queue_id = db.Column(db.Integer, db.ForeignKey('print_queue.id'), nullable=True)
    template_id = db.Column(db.Integer, db.ForeignKey('templates.id'), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=True, index=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('admin_users.id'), nullable=True)
    printer_name = db.Column(db.String(100))
    job_type = db.Column(db.String(20))
    status = db.Column(db.String(20))
    card_side = db.Column(db.String(10))
    copies = db.Column(db.Integer, default=1)
    error_message = db.Column(db.Text)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class NfcEncoding(db.Model):
    __tablename__ = 'nfc_encodings'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey('templates.id'), nullable=False)
    chip_type = db.Column(db.String(50), default='MIFARE_1K')
    encoding_data = db.Column(JSON)
    uid = db.Column(db.String(50), unique=True)
    status = db.Column(db.String(20), default='pending')
    encoded_at = db.Column(db.DateTime)
    verified_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    student = relationship('Student', backref=db.backref('nfc_encodings', lazy='dynamic'))
