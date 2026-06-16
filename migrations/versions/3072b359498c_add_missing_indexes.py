"""add_missing_indexes

Revision ID: 3072b359498c
Revises: 
Create Date: 2026-06-11 00:01:53.151718

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3072b359498c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add missing indexes to existing tables."""
    # These indexes improve performance for frequently-queried columns.
    # Using IF NOT EXISTS so the migration is idempotent.

    # students table
    op.execute("CREATE INDEX IF NOT EXISTS ix_students_email ON students (email)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_students_school_name ON students (school_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_students_template_id ON students (template_id)")

    # templates table
    op.execute("CREATE INDEX IF NOT EXISTS ix_templates_school_name ON templates (school_name)")

    # activity_logs table
    op.execute("CREATE INDEX IF NOT EXISTS ix_activity_logs_timestamp ON activity_logs (timestamp)")

    # admin_users table
    op.execute("CREATE INDEX IF NOT EXISTS ix_admin_users_school_name ON admin_users (school_name)")


def downgrade() -> None:
    """Remove added indexes."""
    op.execute("DROP INDEX IF EXISTS ix_admin_users_school_name")
    op.execute("DROP INDEX IF EXISTS ix_activity_logs_timestamp")
    op.execute("DROP INDEX IF EXISTS ix_templates_school_name")
    op.execute("DROP INDEX IF EXISTS ix_students_template_id")
    op.execute("DROP INDEX IF EXISTS ix_students_school_name")
    op.execute("DROP INDEX IF EXISTS ix_students_email")
