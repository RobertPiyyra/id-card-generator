"""
Extended models for multi-tenant SaaS, advanced features.

These are NEW models that extend the existing schema.
They don't modify any existing models.
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, JSON, Float
from sqlalchemy.orm import relationship
from sqlalchemy.ext.mutable import MutableDict

# Import the existing db instance
from models import db


class Tenant(db.Model):
    """Multi-tenant organization (school, company, etc.)."""
    __tablename__ = 'tenants'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    subdomain = Column(String(63), unique=True, nullable=False, index=True)
    custom_domain = Column(String(255), unique=True, nullable=True, index=True)
    plan = Column(String(50), default='free')
    is_active = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)  # default tenant for single-tenant mode

    # API access
    api_key = Column(String(128), unique=True, nullable=True)

    # White-label config
    white_label_config = Column(MutableDict.as_mutable(JSON), default=dict)

    # Billing
    billing_email = Column(String(255))
    billing_cycle = Column(String(20), default='monthly')
    subscription_status = Column(String(20), default='active')
    subscription_ends_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    activity_logs = relationship('TenantActivityLog', backref='tenant', lazy='dynamic')


class TenantActivityLog(db.Model):
    """Activity log scoped per tenant."""
    __tablename__ = 'tenant_activity_logs'

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    actor = Column(String(255), nullable=False)
    action = Column(String(100), nullable=False)
    target = Column(String(500))
    details = Column(Text)
    ip_address = Column(String(45))
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class CollaborationSession(db.Model):
    """Active collaboration sessions for real-time editing."""
    __tablename__ = 'collaboration_sessions'

    id = Column(Integer, primary_key=True)
    template_id = Column(Integer, ForeignKey('templates.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, nullable=False)
    user_name = Column(String(255))
    socket_id = Column(String(255), unique=True)
    is_active = Column(Boolean, default=True)
    cursor_x = Column(Integer, default=0)
    cursor_y = Column(Integer, default=0)
    active_field = Column(String(100))
    color = Column(String(7), default='#FF6B6B')
    joined_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_active_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CollaborationLock(db.Model):
    """Field locks for real-time collaboration."""
    __tablename__ = 'collaboration_locks'

    id = Column(Integer, primary_key=True)
    template_id = Column(Integer, ForeignKey('templates.id', ondelete='CASCADE'), nullable=False, index=True)
    field_id = Column(String(100), nullable=False)
    locked_by_session_id = Column(String(255), nullable=False)
    locked_by_name = Column(String(255))
    locked_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # One lock per field per template
        db.UniqueConstraint('template_id', 'field_id', name='uq_template_field_lock'),
    )


class DesignAnalysis(db.Model):
    """AI design analysis results for templates."""
    __tablename__ = 'design_analyses'

    id = Column(Integer, primary_key=True)
    template_id = Column(Integer, ForeignKey('templates.id', ondelete='CASCADE'), nullable=False, index=True)
    analysis_type = Column(String(50), nullable=False)  # 'layout', 'accessibility', 'color_harmony'
    score = Column(Float, default=0)
    issues = Column(JSON, default=list)
    suggestions = Column(JSON, default=list)
    raw_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SubscriptionInvoice(db.Model):
    """Subscription billing invoices."""
    __tablename__ = 'subscription_invoices'

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    invoice_number = Column(String(100), unique=True, nullable=False)
    plan = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(3), default='USD')
    status = Column(String(20), default='pending')  # pending, paid, failed, refunded
    billing_cycle = Column(String(20))
    period_start = Column(DateTime)
    period_end = Column(DateTime)
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class WebhookEventLog(db.Model):
    """Log of all webhook events for debugging."""
    __tablename__ = 'webhook_event_logs'

    id = Column(Integer, primary_key=True)
    event_type = Column(String(100), nullable=False, index=True)
    payload = Column(JSON, default=dict)
    source = Column(String(100), default='system')
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
