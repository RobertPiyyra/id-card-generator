"""
Authentication decorators and error handlers extracted from legacy_app.py
"""

import logging
from functools import wraps
from logging.handlers import RotatingFileHandler

from flask import (
    Flask,
    session,
    request,
    redirect,
    url_for,
    jsonify,
    flash,
    current_app,
    g,
)

from app.extensions import limiter
from flask_limiter.errors import RateLimitExceeded
from models import db

logger = logging.getLogger(__name__)


# ================== SafeRotatingFileHandler ==================
class SafeRotatingFileHandler(RotatingFileHandler):
    """
    Windows-safe RotatingFileHandler.

    On Windows, log rotation can fail with PermissionError if the log file is open in another
    process (e.g., an editor/preview). We never want that to break app startup or spam tracebacks,
    so we disable rotation if rollover fails.
    """

    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            # Disable rotation to avoid repeated rollover attempts on every log emit.
            # Continue writing to the existing file.
            self.maxBytes = 0


# ================== Rate Limit Exemption ==================
@limiter.request_filter
def exempt_admins():
    """
    Exempt logged-in admins from ALL rate limits.
    This allows the admin to use the live preview sliders
    and bulk tools without getting blocked.
    """
    # Check if the 'admin' key exists in the session and is True
    return session.get("admin") is True


# ================== Auth Decorators ==================
def login_required(f):
    """Fix 3: Centralized auth decorator for admin routes.
    Usage: @login_required above a route function.
    Redirects to /admin/login if the session has no valid admin key.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin"):
            if request.is_json or request.path.startswith('/api') or request.path.startswith('/corel'):
                return jsonify({"success": False, "error": "Unauthorized"}), 403
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin"):
            if request.is_json or request.path.startswith('/api') or request.path.startswith('/corel'):
                return jsonify({"success": False, "error": "Unauthorized"}), 403
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function


def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin") or session.get("admin_role") != "super_admin":
            if request.is_json or request.path.startswith('/api') or request.path.startswith('/corel'):
                return jsonify({"success": False, "error": "Forbidden"}), 403
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function


def school_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin") or session.get("admin_role") not in ("school_admin", "super_admin"):
            if request.is_json or request.path.startswith('/api') or request.path.startswith('/corel'):
                return jsonify({"success": False, "error": "Forbidden"}), 403
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function


def student_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("student_email") and not session.get("admin"):
            if request.is_json or request.path.startswith('/api') or request.path.startswith('/corel'):
                return jsonify({"success": False, "error": "Unauthorized"}), 403
            return redirect(url_for("auth.student_login"))
        return f(*args, **kwargs)
    return decorated_function


# ================== Error Handlers ==================
def handle_rate_limit_error(e):
    """
    Handles Rate Limit errors gracefully.
    Returns JSON for API calls, Flash messages for Browser navigation.
    """
    # Log the security event
    logger.warning(f"Rate limit exceeded: {e.description} - IP: {request.remote_addr}")

    # If it's an API call (AJAX) or a fetch request, return JSON
    if request.is_json or request.path.startswith('/api'):
        return jsonify({
            "success": False,
            "error": f"Rate limit exceeded: {e.description}"
        }), 429

    # Otherwise, show a nice flash message to the user
    flash(f"⚠️ Too many attempts. Please wait a bit before trying again. ({e.description})", "error")

    # Redirect back to the page they came from (e.g., the login page)
    # If no referrer, go to the landing page
    return redirect(request.referrer or url_for('dashboard.landing'))


def handle_bad_request_error(e):
    """
    Handles 400 Bad Request errors (including CSRF errors).
    Returns JSON for API calls, HTML for browser navigation.
    """
    error_desc = str(e) or "Bad request"
    # Check if this is a CSRF error
    if "CSRF" in error_desc or "token" in error_desc.lower():
        logger.warning(f"CSRF error: {error_desc} - IP: {request.remote_addr}")
        if request.is_json or request.path.startswith('/api') or request.path.startswith('/admin'):
            return jsonify({
                "success": False,
                "error": "CSRF token missing or invalid. Please refresh the page and try again."
            }), 400
        flash("CSRF token error. Please refresh the page and try again.", "error")
        return redirect(request.referrer or url_for('dashboard.landing'))

    logger.warning(f"400 Bad Request: {request.path} - {error_desc}")
    if request.is_json or request.path.startswith('/api') or request.path.startswith('/admin'):
        return jsonify({
            "success": False,
            "error": f"Bad request: {error_desc}"
        }), 400
    return "400 - Bad Request", 400


def handle_payload_too_large(e):
    """Handle file uploads exceeding MAX_CONTENT_LENGTH."""
    logger.warning("413 Payload Too Large: %s", request.path)
    if request.is_json or request.path.startswith('/api'):
        return jsonify({"success": False, "error": "File too large. Maximum upload size is 16MB."}), 413
    flash("File too large. Maximum upload size is 16MB.", "error")
    return redirect(request.referrer or '/')


def handle_unprocessable_entity(e):
    """Handle validation errors."""
    logger.warning("422 Unprocessable Entity: %s - %s", request.path, str(e))
    if request.is_json or request.path.startswith('/api'):
        return jsonify({"success": False, "error": str(e) or "Validation failed"}), 422
    flash(str(e) or "Validation failed. Please check your input.", "error")
    return redirect(request.referrer or '/')


def not_found_error(error):
    logger.warning(f"404 Not Found: {request.path}")
    if request.is_json or request.path.startswith('/api'):
        return jsonify({"success": False, "error": "Resource not found"}), 404
    return "404 - Page Not Found", 404


def internal_error(error):
    db.session.rollback()
    logger.error(f"500 Internal Server Error: {error}")
    if request.is_json or request.path.startswith('/api'):
        return jsonify({"success": False, "error": "An internal server error occurred"}), 500
    return "500 - Internal Server Error", 500


# ================== After Request ==================
def add_security_headers(response):
    """
    Applies security headers to harden the web application against browser-based attacks.
    """
    # Prevent clickjacking
    response.headers['X-Frame-Options'] = 'DENY'
    # Prevent MIME type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Basic Content-Security-Policy (allows CDNs, Cloudinary, and inline scripts/styles used by the visual editor)
    csp = (
        "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:; "
        "img-src 'self' data: blob: https:; "
        "font-src 'self' https: data:;"
    )
    response.headers['Content-Security-Policy'] = csp
    return response


# ================== Template Filter ==================
def rgb_to_hex(rgb_list):
    """
    Jinja filter to convert RGB list [r, g, b] to hex #RRGGBB format.
    Clamps values to 0-255 and falls back to #000000 for invalid input.
    """
    if isinstance(rgb_list, list) and len(rgb_list) == 3:
        try:
            clamped = [max(0, min(255, int(c))) for c in rgb_list]
            return '#{:02x}{:02x}{:02x}'.format(*clamped)
        except (ValueError, TypeError):
            pass
    return '#000000'


# ================== Before Request Handler ==================
def require_login():
    # Allow static assets
    if request.path.startswith('/static/'):
        return

    # Public endpoint whitelist
    public_endpoints = {
        'auth.login', 'auth.register', 'auth.forgot_password',
        'auth.reset_password', 'dashboard.landing_page',
        'student.student_login', 'api.verify_student', 'api.health',
        'dashboard.index', 'dashboard.favicon',  # Student pages
        'static',
        'api.manage_template_fields',  # Form fields for student card generation
    }

    if request.endpoint in public_endpoints:
        return

    # Check by path prefixes for public URLs
    public_paths = ['/login', '/student_login', '/register', '/forgot_password', '/reset_password', '/health', '/verify/']
    if any(request.path.startswith(p) for p in public_paths) or request.path == '/':
        return

    # Check if this request is destined for any of the admin blueprints or path patterns
    is_admin = (
        request.blueprint in {'editor', 'corel', 'enterprise'} or
        request.path.startswith('/admin') or
        request.path.startswith('/editor') or
        request.path.startswith('/corel') or
        request.path.startswith('/enterprise') or
        request.path in {
            "/upload_template", "/delete_all", "/upload_font", "/update_font",
            "/update_photo_position", "/delete_student", "/export_csv", "/download_template",
            "/remove_template", "/update_duplicate_settings", "/delete_pdf", "/update_template_settings",
            "/admin_student_credentials", "/admin_add_student_credential", "/admin_update_student_credential",
            "/admin_delete_student_credential", "/admin_reset_student_password"
        }
    )

    if is_admin and not session.get("admin"):
        logger.warning(f"Unauthorized access to {request.path} (endpoint: {request.endpoint})")
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"error": "Unauthorized"}), 403
        return redirect(url_for("auth.login" if "auth.login" in current_app.view_functions else "login"))
