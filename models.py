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
    school_name = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    
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
    template_id = Column(Integer, ForeignKey('templates.id'))
    school_name = Column(String(255))
    
    # User Auth
    email = Column(String(255), unique=False)
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
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


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
    school_name = db.Column(db.String(255), nullable=True)   # Scopes access for school_admin
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


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
