"""Add enterprise extension tables

Revision ID: 40a1b2c3d4e5
Revises: 3072b359498c
Create Date: 2026-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '40a1b2c3d4e5'
down_revision = '3072b359498c'
branch_labels = None
depends_on = None


def upgrade():
    # Organization
    op.create_table('organizations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False, unique=True),
        sa.Column('slug', sa.String(100), nullable=False, unique=True),
        sa.Column('logo_url', sa.String(1024)),
        sa.Column('primary_color', sa.String(7), default='#2563eb'),
        sa.Column('dark_mode_default', sa.Boolean(), default=False),
        sa.Column('max_users', sa.Integer(), default=10),
        sa.Column('max_templates', sa.Integer(), default=50),
        sa.Column('max_cards_per_month', sa.Integer(), default=10000),
        sa.Column('features_json', sa.JSON(), default={}),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('updated_at', sa.DateTime()),
    )

    # Branch
    op.create_table('branches',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('code', sa.String(20)),
        sa.Column('address', sa.Text()),
        sa.Column('phone', sa.String(50)),
        sa.Column('email', sa.String(255)),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime()),
    )

    # Department
    op.create_table('departments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('branch_id', sa.Integer(), sa.ForeignKey('branches.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('code', sa.String(20)),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime()),
    )

    # Login History
    op.create_table('login_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('admin_id', sa.Integer(), sa.ForeignKey('admin_users.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('username', sa.String(100), nullable=False, index=True),
        sa.Column('ip_address', sa.String(64)),
        sa.Column('user_agent', sa.String(512)),
        sa.Column('device_type', sa.String(50)),
        sa.Column('browser', sa.String(100)),
        sa.Column('os', sa.String(100)),
        sa.Column('country', sa.String(100)),
        sa.Column('login_success', sa.Boolean(), default=True),
        sa.Column('failure_reason', sa.String(255)),
        sa.Column('session_token', sa.String(255), index=True),
        sa.Column('logged_out_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), index=True),
    )

    # User Sessions
    op.create_table('user_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('admin_id', sa.Integer(), sa.ForeignKey('admin_users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('session_token', sa.String(255), nullable=False, unique=True, index=True),
        sa.Column('ip_address', sa.String(64)),
        sa.Column('user_agent', sa.String(512)),
        sa.Column('device_fingerprint', sa.String(128)),
        sa.Column('two_factor_verified', sa.Boolean(), default=False),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('last_activity_at', sa.DateTime()),
        sa.Column('created_at', sa.DateTime()),
    )

    # 2FA Backup Codes
    op.create_table('two_factor_backup_codes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('admin_id', sa.Integer(), sa.ForeignKey('admin_users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('code_hash', sa.String(255), nullable=False),
        sa.Column('used', sa.Boolean(), default=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime()),
    )

    # API Keys
    op.create_table('api_keys',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('key_prefix', sa.String(8), nullable=False, index=True),
        sa.Column('key_hash', sa.String(255), nullable=False, unique=True),
        sa.Column('scopes', sa.JSON(), default={}),
        sa.Column('rate_limit', sa.Integer(), default=1000),
        sa.Column('request_count', sa.Integer(), default=0),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('created_by', sa.String(255)),
        sa.Column('created_at', sa.DateTime()),
    )

    # API Key Logs
    op.create_table('api_key_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('api_key_id', sa.Integer(), sa.ForeignKey('api_keys.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('method', sa.String(10)),
        sa.Column('path', sa.String(512)),
        sa.Column('status_code', sa.Integer()),
        sa.Column('ip_address', sa.String(64)),
        sa.Column('response_time_ms', sa.Float()),
        sa.Column('created_at', sa.DateTime(), index=True),
    )

    # Webhook Endpoints
    op.create_table('webhook_endpoints',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('url', sa.String(512), nullable=False),
        sa.Column('secret', sa.String(255)),
        sa.Column('events', sa.JSON(), default=[]),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('last_triggered_at', sa.DateTime(), nullable=True),
        sa.Column('last_status_code', sa.Integer(), nullable=True),
        sa.Column('failure_count', sa.Integer(), default=0),
        sa.Column('created_by', sa.String(255)),
        sa.Column('created_at', sa.DateTime()),
    )

    # Webhook Deliveries
    op.create_table('webhook_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('webhook_id', sa.Integer(), sa.ForeignKey('webhook_endpoints.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('payload_json', sa.JSON(), default={}),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('response_body', sa.Text()),
        sa.Column('retry_count', sa.Integer(), default=0),
        sa.Column('delivered', sa.Boolean(), default=False),
        sa.Column('error_message', sa.Text()),
        sa.Column('created_at', sa.DateTime(), index=True),
    )

    # Access Policies
    op.create_table('access_policies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('role', sa.String(50), nullable=False),
        sa.Column('resource', sa.String(100), nullable=False),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('allowed', sa.Boolean(), default=True),
        sa.Column('conditions_json', sa.JSON(), default={}),
        sa.Column('created_at', sa.DateTime()),
    )

    # System Metrics
    op.create_table('system_metrics',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('metric_name', sa.String(100), nullable=False, index=True),
        sa.Column('metric_value', sa.Float(), nullable=False),
        sa.Column('metric_unit', sa.String(30)),
        sa.Column('labels_json', sa.JSON(), default={}),
        sa.Column('created_at', sa.DateTime(), index=True),
    )

    # Data Archives
    op.create_table('data_archives',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('archive_name', sa.String(200), nullable=False),
        sa.Column('entity_type', sa.String(80), nullable=False),
        sa.Column('entity_ids_json', sa.JSON(), default=[]),
        sa.Column('file_path', sa.String(512)),
        sa.Column('file_size_bytes', sa.Integer(), default=0),
        sa.Column('record_count', sa.Integer(), default=0),
        sa.Column('compressed', sa.Boolean(), default=True),
        sa.Column('restored_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(255)),
        sa.Column('created_at', sa.DateTime(), index=True),
    )

    # Scheduled Tasks
    op.create_table('scheduled_tasks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('task_type', sa.String(80), nullable=False),
        sa.Column('cron_expression', sa.String(100)),
        sa.Column('params_json', sa.JSON(), default={}),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('last_run_status', sa.String(20)),
        sa.Column('last_run_output', sa.Text()),
        sa.Column('run_count', sa.Integer(), default=0),
        sa.Column('fail_count', sa.Integer(), default=0),
        sa.Column('next_run_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(255)),
        sa.Column('created_at', sa.DateTime()),
    )

    # OCR Results
    op.create_table('ocr_results',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('students.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('source_image_url', sa.String(1024)),
        sa.Column('extracted_text', sa.Text()),
        sa.Column('extracted_fields', sa.JSON(), default={}),
        sa.Column('confidence_score', sa.Float(), default=0.0),
        sa.Column('processing_time_ms', sa.Float()),
        sa.Column('model_used', sa.String(100)),
        sa.Column('verified', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(), index=True),
    )


def downgrade():
    op.drop_table('ocr_results')
    op.drop_table('scheduled_tasks')
    op.drop_table('data_archives')
    op.drop_table('system_metrics')
    op.drop_table('access_policies')
    op.drop_table('webhook_deliveries')
    op.drop_table('webhook_endpoints')
    op.drop_table('api_key_logs')
    op.drop_table('api_keys')
    op.drop_table('two_factor_backup_codes')
    op.drop_table('user_sessions')
    op.drop_table('login_history')
    op.drop_table('departments')
    op.drop_table('branches')
    op.drop_table('organizations')
