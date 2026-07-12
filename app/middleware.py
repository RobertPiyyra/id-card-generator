"""
Production middleware: security headers, request ID tracking, CORS.
"""
import uuid
import time
import logging

from flask import request, g, jsonify, make_response, current_app, session
from functools import wraps
from app.extensions import db
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request ID middleware
# ---------------------------------------------------------------------------

def _generate_request_id():
    """Generate a short unique request ID."""
    return uuid.uuid4().hex[:12]


def init_request_id(app):
    """Attach a unique request ID to every incoming request."""

    @app.before_request
    def _before():
        g.request_id = request.headers.get("X-Request-ID") or _generate_request_id()
        g.request_start = time.monotonic()

    @app.after_request
    def _after(response):
        response.headers["X-Request-ID"] = g.get("request_id", "")
        return response


# ---------------------------------------------------------------------------
# Security headers middleware (HSTS, CSP, etc.)
# ---------------------------------------------------------------------------

def init_security_headers(app):
    """Apply hardened security headers to every response."""

    @app.after_request
    def _security_headers(response):
        # HSTS — force HTTPS for 1 year (only in production)
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        # Clickjacking protection
        response.headers.setdefault("X-Frame-Options", "DENY")

        # MIME sniffing protection
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

        # Referrer policy
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

        # Permissions policy — disable unused browser features
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(self), microphone=(), geolocation=(), payment=()",
        )

        return response


# ---------------------------------------------------------------------------
# CORS support (flask-cors)
# ---------------------------------------------------------------------------

_cors = None


def init_cors(app):
    """Enable CORS for API routes only."""
    global _cors
    try:
        from flask_cors import CORS
    except ImportError:
        logger.warning("flask-cors not installed — CORS support disabled")
        return

    _cors = CORS(
        app,
        resources={
            r"/api/*": {
                "origins": [o.strip() for o in app.config.get("CORS_ORIGINS", "").split(",") if o.strip()] or ["http://localhost:5000"],
                "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                "allow_headers": ["Content-Type", "Authorization", "X-CSRFToken", "X-Request-ID"],
                "supports_credentials": True,
            }
        },
    )


# ---------------------------------------------------------------------------
# CSRF exemption for API routes
# ---------------------------------------------------------------------------

def init_csrf_exemptions(app):
    """Exempt API and webhook routes from CSRF protection."""
    try:
        from flask_wtf.csrf import CSRFProtect
        csrf = app.extensions.get("csrf")
        if csrf is None:
            return

        # Exempt paths — these prefixes and endpoints skip CSRF validation.
        # We patch csrf_protect directly because the before_request ordering
        # means our hook runs after csrf_protect validates the token.
        exempt_prefixes = (
            "/api/", "/health", "/verify/", "/enterprise/admin/api/",
            "/enterprise/webhooks/", "/admin/serial_batches/api/",
        )

        original_csrf_protect = None

        for fn in app.before_request_funcs.get(None, []):
            if fn.__name__ == "csrf_protect":
                original_csrf_protect = fn
                break

        if original_csrf_protect is not None:
            def _patched_csrf_protect():
                if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                    if any(request.path.startswith(p) for p in exempt_prefixes):
                        return  # Skip CSRF check for exempt paths
                original_csrf_protect()
            # Replace in the before_request list
            idx = app.before_request_funcs[None].index(original_csrf_protect)
            app.before_request_funcs[None][idx] = _patched_csrf_protect
            # Also patch the reference held by the CSRFProtect extension
            csrf.protect = lambda: None  # disable direct protect calls
        else:
            # Fallback: use before_request with priority ordering
            @app.before_request
            def _csrf_exempt_api():
                pass

    except Exception as exc:
        logger.warning("CSRF exemption setup failed: %s", exc)


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def json_error(message, status_code=400, **extra):
    """Return a consistent JSON error response."""
    payload = {"success": False, "error": message}
    payload.update(extra)
    response = jsonify(payload)
    response.status_code = status_code
    return response


def json_success(data=None, status_code=200, **extra):
    """Return a consistent JSON success response."""
    payload = {"success": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    response = jsonify(payload)
    response.status_code = status_code
    return response


# ---------------------------------------------------------------------------
# Security audit logging
# ---------------------------------------------------------------------------

def log_security_event(event_type, details=None, level="warning"):
    """Log a security-relevant event with request context."""
    log_fn = getattr(logger, level, logger.warning)
    log_fn(
        "security_event",
        extra={
            "event_type": event_type,
            "request_id": getattr(g, "request_id", ""),
            "ip": request.remote_addr if request else "",
            "path": request.path if request else "",
            "user_agent": request.user_agent.string if request and request.user_agent else "",
            "details": details or {},
        },
    )


# ---------------------------------------------------------------------------
# Auth helpers (replacing inline session checks)
# ---------------------------------------------------------------------------

def admin_required(f):
    """Decorator: require admin session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not getattr(g, "is_admin", False):
            if request.is_json or request.path.startswith("/api"):
                return json_error("Authentication required", 401)
            from flask import redirect, url_for, flash
            flash("Please log in as admin.", "error")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def student_required(f):
    """Decorator: require student or admin session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not (getattr(g, "is_student", False) or getattr(g, "is_admin", False)):
            if request.is_json or request.path.startswith("/api"):
                return json_error("Authentication required", 401)
            from flask import redirect, url_for
            return redirect(url_for("auth.student_login"))
        return f(*args, **kwargs)
    return decorated


def tiered_limit(free_limit="10/hour", basic_limit="100/hour", pro_limit="1000/hour"):
    """
    Decorator: rate limit based on user tier.
    Reads tier from AdminUser.organization relationship.
    Falls back to free_limit for unauthenticated users.

    Usage:
        @tiered_limit(free_limit="5/hour", basic_limit="50/hour", pro_limit="500/hour")
        def my_endpoint():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            # Determine user tier
            from flask import g
            tier = "free"

            admin_id = session.get("admin_id")
            if admin_id:
                try:
                    from models import AdminUser
                    admin = db.session.get(AdminUser, admin_id)
                    if admin and admin.role == "super_admin":
                        tier = "pro"
                    elif admin:
                        tier = "basic"
                except Exception:
                    pass

            # Apply rate limit based on tier
            limit_map = {
                "free": free_limit,
                "basic": basic_limit,
                "pro": pro_limit,
            }
            limit_str = limit_map.get(tier, free_limit)

            # Use Flask-Limiter for enforcement
            limiter = current_app.extensions.get("limiter")
            if limiter:
                key = f"{tier}:{request.remote_addr}"
                try:
                    limiter.check(key, limit_str)
                except Exception:
                    return json_error(
                        f"Rate limit exceeded. Tier: {tier}. Limit: {limit_str}.",
                        429,
                    )

            return f(*args, **kwargs)
        return decorated
    return decorator


# ---------------------------------------------------------------------------
# Auth context middleware
# ---------------------------------------------------------------------------

def init_auth_context(app):
    """Set g.is_admin and g.is_student from session on every request."""

    @app.before_request
    def _auth_context():
        from flask import session
        g.is_admin = bool(session.get("admin"))
        g.is_student = bool(session.get("student_email"))


# ---------------------------------------------------------------------------
# Initialise everything
# ---------------------------------------------------------------------------

def init_middleware(app):
    """Register all production middleware with the Flask app."""
    init_request_id(app)
    init_security_headers(app)
    init_cors(app)
    init_csrf_exemptions(app)
    init_auth_context(app)

    # Enable gzip compression for responses
    try:
        from flask_compress import Compress
        Compress(app)
    except Exception as exc:
        logger.warning("Flask-Compress init failed: %s", exc)
