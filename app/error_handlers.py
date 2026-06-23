"""
Flask error handlers and security headers.

Extracted from app/legacy_app.py to centralize error handling.

USAGE: These functions are identical copies of those in legacy_app.py.
The original definitions shadow these imports at runtime.
To activate: uncomment the import at the bottom of legacy_app.py.
"""
import logging

from flask import flash, jsonify, redirect, request, url_for

from app.config import Config

logger = logging.getLogger(__name__)

# Email config constants (preserved from legacy_app.py)
EMAIL_FROM = Config.EMAIL_FROM
EMAIL_PASSWORD = Config.EMAIL_PASSWORD
SMTP_SERVER = Config.SMTP_SERVER
SMTP_PORT = Config.SMTP_PORT
ADMIN_USERNAME = Config.ADMIN_USERNAME
ADMIN_PASSWORD_HASH = Config.ADMIN_PASSWORD_HASH


def handle_rate_limit_error(e, app, db):
    """
    Handles Rate Limit errors gracefully.
    Returns JSON for API calls, Flash messages for Browser navigation.
    """
    logger.warning(f"Rate limit exceeded: {e.description} - IP: {request.remote_addr}")
    if request.is_json or request.path.startswith('/api'):
        return jsonify({
            "success": False,
            "error": f"Rate limit exceeded: {e.description}"
        }), 429
    flash(f"⚠️ Too many attempts. Please wait a bit before trying again. ({e.description})", "error")
    return redirect(request.referrer or url_for('dashboard.landing'))


def handle_bad_request_error(e, app, db):
    """
    Handles 400 Bad Request errors (including CSRF errors).
    Returns JSON for API calls, HTML for browser navigation.
    """
    error_desc = str(e) or "Bad request"
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


def handle_payload_too_large(e, app, db):
    """Handle file uploads exceeding MAX_CONTENT_LENGTH."""
    logger.warning("413 Payload Too Large: %s", request.path)
    max_mb = app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)
    if request.is_json or request.path.startswith('/api') or request.path.startswith('/bulk'):
        return jsonify({"success": False, "error": f"File too large. Maximum upload size is {max_mb}MB."}), 413
    flash(f"File too large. Maximum upload size is {max_mb}MB.", "error")
    return redirect(request.referrer or '/')


def handle_unprocessable_entity(e, app, db):
    """Handle validation errors."""
    logger.warning("422 Unprocessable Entity: %s - %s", request.path, str(e))
    if request.is_json or request.path.startswith('/api'):
        return jsonify({"success": False, "error": str(e) or "Validation failed"}), 422
    flash(str(e) or "Validation failed. Please check your input.", "error")
    return redirect(request.referrer or '/')


def not_found_error(error, app, db):
    logger.warning(f"404 Not Found: {request.path}")
    if request.is_json or request.path.startswith('/api'):
        return jsonify({"success": False, "error": "Resource not found"}), 404
    return "404 - Page Not Found", 404


def internal_error(error, app, db):
    db.session.rollback()
    logger.error(f"500 Internal Server Error: {error}")
    if request.is_json or request.path.startswith('/api'):
        return jsonify({"success": False, "error": "An internal server error occurred"}), 500
    return "500 - Internal Server Error", 500


def add_security_headers(response):
    """
    Applies security headers to harden the web application against browser-based attacks.
    """
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    csp = (
        "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:; "
        "img-src 'self' data: blob: https:; "
        "font-src 'self' https: data:;"
    )
    response.headers['Content-Security-Policy'] = csp
    return response


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
