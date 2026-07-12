"""
Migration: Add performance indexes for frequently queried columns.

This migration adds database indexes to speed up common queries:
- Student: template_id, school_name, created_at, data_hash
- Template: school_name, deadline
- BulkJob: template_id, status, created_at
- BulkJobItem: bulk_job_id, status
- PrintQueue: student_id, admin_id, status
- ActivityLog: timestamp, actor
- NotificationLog: student_id, template_id
"""
from alembic import op
import sqlalchemy as sa

revision = "001_add_performance_indexes"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Student indexes
    op.create_index("idx_student_template_id", "students", ["template_id"])
    op.create_index("idx_student_school_name", "students", ["school_name"])
    op.create_index("idx_student_created_at", "students", ["created_at"])

    # Template indexes
    op.create_index("idx_template_deadline", "templates", ["deadline"])

    # BulkJob indexes
    op.create_index("idx_bulk_job_status", "bulk_jobs", ["status"])

    # BulkJobItem indexes
    op.create_index("idx_bulk_job_item_status", "bulk_job_items", ["status"])

    # PrintQueue indexes
    op.create_index("idx_print_queue_admin_id", "print_queue", ["admin_id"])
    op.create_index("idx_print_queue_status", "print_queue", ["status"])

    # ActivityLog indexes
    op.create_index("idx_activity_log_actor", "activity_logs", ["actor"])

    # NotificationLog indexes
    op.create_index("idx_notification_log_student", "notification_logs", ["student_id"])
    op.create_index("idx_notification_log_template", "notification_logs", ["template_id"])


def downgrade():
    op.drop_index("idx_notification_log_template", "notification_logs")
    op.drop_index("idx_notification_log_student", "notification_logs")
    op.drop_index("idx_activity_log_actor", "activity_logs")
    op.drop_index("idx_print_queue_status", "print_queue")
    op.drop_index("idx_print_queue_admin_id", "print_queue")
    op.drop_index("idx_bulk_job_item_status", "bulk_job_items")
    op.drop_index("idx_bulk_job_status", "bulk_jobs")
    op.drop_index("idx_template_deadline", "templates")
    op.drop_index("idx_student_created_at", "students")
    op.drop_index("idx_student_school_name", "students")
    op.create_index("idx_student_template_id", "students", ["template_id"])
