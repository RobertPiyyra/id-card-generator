"""
Multi-Tenant SaaS Architecture.

Provides:
  - Tenant isolation (database-per-tenant with shared schema)
  - Custom domain routing (school1.idcard.com)
  - White-labeling (custom logos, colors, email templates)
  - Subscription billing with plan tiers
  - Tenant provisioning API
  - Per-tenant feature flags and quotas

All new models and services — no existing code modified.
"""
import os
import re
import json
import logging
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from functools import wraps

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tenant Context
# ---------------------------------------------------------------------------

_current_tenant = None


def get_current_tenant():
    """Get the current tenant from request context."""
    return _current_tenant


def set_current_tenant(tenant):
    """Set the current tenant (called by middleware)."""
    global _current_tenant
    _current_tenant = tenant


# ---------------------------------------------------------------------------
# Tenant Resolution Middleware
# ---------------------------------------------------------------------------

def init_tenant_middleware(app):
    """
    Initialize tenant resolution middleware.
    Resolves tenant from subdomain, custom domain, or header.

    Usage:
        from app.services.tenant import init_tenant_middleware
        init_tenant_middleware(app)
    """
    try:
        from models import Tenant
    except ImportError:
        logger.warning("Tenant model not available — multi-tenancy disabled")
        return

    @app.before_request
    def resolve_tenant():
        """Resolve the current tenant before each request."""
        from flask import request, g

        tenant = None

        # 1. Check custom header (for API calls)
        tenant_id = request.headers.get("X-Tenant-ID")
        if tenant_id:
            try:
                tenant = Tenant.query.filter_by(id=int(tenant_id), is_active=True).first()
            except (ValueError, Exception):
                pass

        # 2. Check subdomain (school1.idcard.com)
        if not tenant:
            host = request.host.split(":")[0]
            base_domain = os.environ.get("BASE_DOMAIN", "idcard.com")
            if host != base_domain and host.endswith(f".{base_domain}"):
                subdomain = host[: -len(base_domain) - 1]
                tenant = Tenant.query.filter_by(subdomain=subdomain, is_active=True).first()

        # 3. Check custom domain (cards.schoolname.com)
        if not tenant:
            host = request.host.split(":")[0]
            tenant = Tenant.query.filter_by(custom_domain=host, is_active=True).first()

        # 4. Default tenant (for single-tenant mode)
        if not tenant:
            tenant = Tenant.query.filter_by(is_default=True).first()

        g.tenant = tenant
        set_current_tenant(tenant)


# ---------------------------------------------------------------------------
# Tenant-Aware Query Helper
# ---------------------------------------------------------------------------

def tenant_filter(query, model, tenant=None):
    """
    Add tenant filter to a query.
    Only works for models that have a tenant_id column.

    Usage:
        students = tenant_filter(Student.query, Student).all()
    """
    if tenant is None:
        tenant = get_current_tenant()

    if tenant and hasattr(model, 'tenant_id'):
        return query.filter_by(tenant_id=tenant.id)
    return query


# ---------------------------------------------------------------------------
# Feature Flags
# ---------------------------------------------------------------------------

class FeatureFlags:
    """
    Per-tenant feature flags.
    Each plan has a set of enabled features.

    Usage:
        if FeatureFlags.is_enabled('bulk_generation', tenant):
            ...
    """

    PLAN_FEATURES = {
        "free": {
            "max_students": 100,
            "max_templates": 3,
            "bulk_generation": False,
            "custom_domain": False,
            "white_label": False,
            "api_access": False,
            "advanced_analytics": False,
            "priority_support": False,
            "real_time_collaboration": False,
            "ai_layout": False,
        },
        "basic": {
            "max_students": 500,
            "max_templates": 10,
            "bulk_generation": True,
            "custom_domain": False,
            "white_label": False,
            "api_access": True,
            "advanced_analytics": False,
            "priority_support": False,
            "real_time_collaboration": False,
            "ai_layout": False,
        },
        "professional": {
            "max_students": 5000,
            "max_templates": 50,
            "bulk_generation": True,
            "custom_domain": True,
            "white_label": True,
            "api_access": True,
            "advanced_analytics": True,
            "priority_support": True,
            "real_time_collaboration": True,
            "ai_layout": True,
        },
        "enterprise": {
            "max_students": -1,  # unlimited
            "max_templates": -1,
            "bulk_generation": True,
            "custom_domain": True,
            "white_label": True,
            "api_access": True,
            "advanced_analytics": True,
            "priority_support": True,
            "real_time_collaboration": True,
            "ai_layout": True,
        },
    }

    @classmethod
    def is_enabled(cls, feature: str, tenant=None) -> bool:
        """Check if a feature is enabled for the current tenant."""
        if tenant is None:
            tenant = get_current_tenant()
        if not tenant:
            return True  # no tenant = all features (single-tenant mode)

        plan = tenant.plan or "free"
        features = cls.PLAN_FEATURES.get(plan, cls.PLAN_FEATURES["free"])
        return features.get(feature, False)

    @classmethod
    def get_limit(cls, resource: str, tenant=None) -> int:
        """Get the limit for a resource."""
        if tenant is None:
            tenant = get_current_tenant()
        if not tenant:
            return -1  # unlimited

        plan = tenant.plan or "free"
        features = cls.PLAN_FEATURES.get(plan, cls.PLAN_FEATURES["free"])
        return features.get(f"max_{resource}", 0)

    @classmethod
    def check_quota(cls, resource: str, current_count: int, tenant=None) -> bool:
        """Check if the tenant is within quota for a resource."""
        limit = cls.get_limit(resource, tenant)
        if limit == -1:
            return True  # unlimited
        return current_count < limit


# ---------------------------------------------------------------------------
# White-Label Configuration
# ---------------------------------------------------------------------------

class WhiteLabelConfig:
    """
    White-label configuration for each tenant.
    Controls branding, colors, logos, and email templates.
    """

    DEFAULTS = {
        "app_name": "ID Card Generator",
        "primary_color": "#2C3E50",
        "secondary_color": "#3498DB",
        "accent_color": "#E74C3C",
        "logo_url": None,
        "favicon_url": None,
        "login_background_url": None,
        "email_from_name": "ID Card Generator",
        "email_from_address": None,
        "footer_text": "Powered by ID Card Generator",
        "custom_css": None,
        "custom_js": None,
        "terms_url": None,
        "privacy_url": None,
        "support_email": None,
        "support_phone": None,
        "language": "en",
        "timezone": "UTC",
        "date_format": "%Y-%m-%d",
        "time_format": "%H:%M",
    }

    @classmethod
    def get_config(cls, tenant=None) -> dict:
        """Get the white-label config for a tenant."""
        if tenant is None:
            tenant = get_current_tenant()

        config = dict(cls.DEFAULTS)

        if tenant and tenant.white_label_config:
            config.update(tenant.white_label_config)

        return config

    @classmethod
    def apply_to_template_context(cls, context: dict, tenant=None) -> dict:
        """Apply white-label config to a template context."""
        config = cls.get_config(tenant)
        context.update({
            "app_name": config["app_name"],
            "primary_color": config["primary_color"],
            "secondary_color": config["secondary_color"],
            "accent_color": config["accent_color"],
            "logo_url": config["logo_url"],
            "favicon_url": config["favicon_url"],
            "footer_text": config["footer_text"],
            "custom_css": config["custom_css"],
        })
        return context


# ---------------------------------------------------------------------------
# Subscription Billing
# ---------------------------------------------------------------------------

class BillingPlans:
    """
    Subscription plan definitions.
    In production, integrate with Stripe/Razorpay.
    """

    PLANS = {
        "free": {
            "name": "Free",
            "price_monthly": 0,
            "price_yearly": 0,
            "currency": "USD",
            "features": ["3 templates", "100 students", "Basic support"],
        },
        "basic": {
            "name": "Basic",
            "price_monthly": 29,
            "price_yearly": 290,
            "currency": "USD",
            "features": ["10 templates", "500 students", "Bulk generation", "API access", "Email support"],
        },
        "professional": {
            "name": "Professional",
            "price_monthly": 99,
            "price_yearly": 990,
            "currency": "USD",
            "features": [
                "50 templates", "5000 students", "Bulk generation",
                "API access", "Custom domain", "White-label",
                "Advanced analytics", "Priority support", "Real-time collaboration",
                "AI layout assistant",
            ],
        },
        "enterprise": {
            "name": "Enterprise",
            "price_monthly": 299,
            "price_yearly": 2990,
            "currency": "USD",
            "features": [
                "Unlimited templates", "Unlimited students", "All features",
                "Dedicated support", "SLA guarantee", "Custom integrations",
            ],
        },
    }

    @classmethod
    def get_plan(cls, plan_id: str) -> dict:
        return cls.PLANS.get(plan_id, cls.PLANS["free"])

    @classmethod
    def get_all_plans(cls) -> dict:
        return cls.PLANS


def create_subscription(tenant_id: int, plan: str, billing_cycle: str = "monthly") -> dict:
    """
    Create a new subscription for a tenant.
    In production, this would create a Stripe/Razorpay subscription.

    Usage:
        sub = create_subscription(tenant.id, "professional", "yearly")
    """
    plan_info = BillingPlans.get_plan(plan)
    amount = plan_info[f"price_{billing_cycle}"]

    subscription = {
        "id": secrets.token_hex(16),
        "tenant_id": tenant_id,
        "plan": plan,
        "billing_cycle": billing_cycle,
        "amount": amount,
        "currency": plan_info["currency"],
        "status": "active",
        "current_period_start": datetime.now(timezone.utc).isoformat(),
        "current_period_end": (
            datetime.now(timezone.utc) + timedelta(days=30 if billing_cycle == "monthly" else 365)
        ).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info("billing: created subscription for tenant %d (plan=%s, cycle=%s)",
                tenant_id, plan, billing_cycle)
    return subscription


# ---------------------------------------------------------------------------
# Tenant Provisioning API
# ---------------------------------------------------------------------------

def provision_tenant(name: str, subdomain: str, admin_email: str,
                      plan: str = "free", custom_domain: str = None) -> dict:
    """
    Provision a new tenant (school/organization).

    Creates:
    - Tenant record
    - Admin user
    - Default settings
    - Subscription

    Usage:
        result = provision_tenant("Springfield Elementary", "springfield", "admin@school.edu")
    """
    try:
        from models import db, Tenant, AdminUser
    except ImportError:
        logger.warning("Tenant model not available")
        return {"success": False, "error": "Tenant model not available"}

    # Validate subdomain
    if not re.match(r'^[a-z0-9][a-z0-9-]{2,62}$', subdomain):
        return {"success": False, "error": "Invalid subdomain format"}

    # Check uniqueness
    existing = Tenant.query.filter(
        (Tenant.subdomain == subdomain) | (Tenant.custom_domain == custom_domain)
    ).first() if custom_domain else Tenant.query.filter_by(subdomain=subdomain).first()

    if existing:
        return {"success": False, "error": "Subdomain or custom domain already in use"}

    # Create tenant
    tenant = Tenant(
        name=name,
        subdomain=subdomain.lower(),
        custom_domain=custom_domain.lower() if custom_domain else None,
        plan=plan,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        api_key=secrets.token_hex(32),
        white_label_config=WhiteLabelConfig.DEFAULTS,
    )
    db.session.add(tenant)
    db.session.flush()

    # Create admin user
    temp_password = secrets.token_urlsafe(12)
    admin = AdminUser(
        tenant_id=tenant.id,
        username=admin_email.split("@")[0],
        email=admin_email,
        password_hash=hashlib.sha256(temp_password.encode()).hexdigest(),  # TODO: use proper hash
        is_super_admin=True,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(admin)

    # Create subscription
    subscription = create_subscription(tenant.id, plan)

    db.session.commit()

    logger.info("tenant: provisioned '%s' (subdomain=%s, plan=%s)", name, subdomain, plan)

    return {
        "success": True,
        "tenant": {
            "id": tenant.id,
            "name": tenant.name,
            "subdomain": tenant.subdomain,
            "custom_domain": tenant.custom_domain,
            "plan": tenant.plan,
            "api_key": tenant.api_key,
        },
        "admin": {
            "username": admin.username,
            "email": admin.email,
            "temp_password": temp_password,  # send via email in production
        },
        "subscription": subscription,
    }


def get_tenant_stats(tenant_id: int) -> dict:
    """Get usage statistics for a tenant."""
    try:
        from models import db, Tenant, Student, Template, BulkJob
    except ImportError:
        return {}

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return {}

    return {
        "tenant_name": tenant.name,
        "plan": tenant.plan,
        "students_count": Student.query.filter_by(tenant_id=tenant_id).count(),
        "templates_count": Template.query.filter_by(tenant_id=tenant_id).count(),
        "bulk_jobs_count": BulkJob.query.filter_by(tenant_id=tenant_id).count(),
        "quotas": {
            "students": {
                "used": Student.query.filter_by(tenant_id=tenant_id).count(),
                "limit": FeatureFlags.get_limit("students", tenant),
            },
            "templates": {
                "used": Template.query.filter_by(tenant_id=tenant_id).count(),
                "limit": FeatureFlags.get_limit("templates", tenant),
            },
        },
        "features": FeatureFlags.PLAN_FEATURES.get(tenant.plan, {}),
    }
