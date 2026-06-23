"""
Authentication and authorization decorators.

Extracted from app/legacy_app.py to provide a single source of truth
for auth decorators used across the application.

USAGE: These functions are identical copies of those in legacy_app.py.
The original definitions shadow these imports at runtime.
To activate: uncomment the import at the bottom of legacy_app.py.
"""
import logging
from functools import wraps

from flask import jsonify, redirect, request, session, url_for

logger = logging.getLogger(__name__)


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
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin"):
            if request.is_json or request.path.startswith('/api') or request.path.startswith('/corel'):
                return jsonify({"success": False, "error": "Unauthorized"}), 403
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function


def super_admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin") or session.get("admin_role") != "super_admin":
            if request.is_json or request.path.startswith('/api') or request.path.startswith('/corel'):
                return jsonify({"success": False, "error": "Forbidden"}), 403
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function


def school_admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin") or session.get("admin_role") not in ("school_admin", "super_admin"):
            if request.is_json or request.path.startswith('/api') or request.path.startswith('/corel'):
                return jsonify({"success": False, "error": "Forbidden"}), 403
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function


def student_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("student_email") and not session.get("admin"):
            if request.is_json or request.path.startswith('/api') or request.path.startswith('/corel'):
                return jsonify({"success": False, "error": "Unauthorized"}), 403
            return redirect(url_for("auth.student_login"))
        return f(*args, **kwargs)
    return decorated_function


def exempt_admins():
    """
    Exempt logged-in admins from ALL rate limits.
    This allows the admin to use the live preview sliders
    and bulk tools without getting blocked.
    """
    return session.get("admin") is True
