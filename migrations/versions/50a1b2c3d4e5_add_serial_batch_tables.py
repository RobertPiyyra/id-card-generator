"""Add serial batch tables for photo-first workflow

Revision ID: 50a1b2c3d4e5
Revises: 40a1b2c3d4e5
Create Date: 2026-06-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '50a1b2c3d4e5'
down_revision = '40a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade():
    # SerialBatch
    op.create_table('serial_batches',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('school_name', sa.String(255), nullable=False, index=True),
        sa.Column('template_id', sa.Integer(), sa.ForeignKey('templates.id'), nullable=False),
        sa.Column('prefix', sa.String(50), default='SCH-'),
        sa.Column('status', sa.String(30), default='uploading'),
        sa.Column('created_by', sa.String(255)),
        sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), default=sa.func.now(), onupdate=sa.func.now()),
    )

    # SerialCard
    op.create_table('serial_cards',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('batch_id', sa.Integer(), sa.ForeignKey('serial_batches.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('serial_no', sa.String(50), nullable=False),
        sa.Column('photo_path', sa.String(500)),
        sa.Column('photo_thumbnail', sa.String(500)),
        sa.Column('name', sa.String(255)),
        sa.Column('father_name', sa.String(255)),
        sa.Column('class_name', sa.String(100)),
        sa.Column('dob', sa.String(50)),
        sa.Column('address', sa.Text()),
        sa.Column('phone', sa.String(50)),
        sa.Column('custom_data', sa.JSON(), default=dict),
        sa.Column('status', sa.String(30), default='photo_only'),
        sa.Column('error_message', sa.Text()),
        sa.Column('rendered_path', sa.String(500)),
        sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint('batch_id', 'serial_no', name='uq_batch_serial'),
    )


def downgrade():
    op.drop_table('serial_cards')
    op.drop_table('serial_batches')
