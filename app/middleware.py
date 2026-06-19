"""
Production middleware: security headers, request ID tracking, CORS.
"""
import uuid
import time
import logging

from flask import request, g, jsonify, make_response
from functools import wraps

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

        @csrf.exempt
        def _exempt_api():
            pass

        # Exempt all API, webhook, and health routes
        exempt_prefixes = (
            "/api/", "/health", "/verify/", "/enterprise/admin/api/",
            "/enterprise/webhooks/",
        )

        original_csrf_error = None

        @app.before_request
        def _csrf_exempt_api():
            if any(request.path.startswith(p) for p in exempt_prefixes):
                request.environ["csrf_exempt"] = True

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
