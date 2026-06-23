from dotenv import load_dotenv
load_dotenv()  # Load environment variables BEFORE any other imports
import socket  # <--- Make sure this is imported at the top
from flask import Flask, render_template, request, url_for, Response, redirect, session, send_file, jsonify, flash, Blueprint
from PIL import Image, ImageDraw, ImageFont,ImageOps
import os
import json
import threading
from flask_login import current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
import hashlib
import logging
import re
import smtplib
import ssl
from urllib.parse import urlparse

import requests
from email.mime.text import MIMEText
import random
import string
from collections import defaultdict
from functools import lru_cache
import textwrap
# import pandas as pd  # Lazy imported in background_bulk_generate()
import tempfile
import glob
import io
from io import BytesIO
import base64
import uuid
from concurrent.futures import ThreadPoolExecutor
import time
from reportlab.pdfgen import canvas
from rq import get_current_job
from reportlab.lib.pagesizes import A4, landscape
# Ensure fitz is available (it was used in load_template)
import fitz  # PyMuPDF
import qrcode
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from qrcode.image.pil import PilImage
from qrcode.image.styles.moduledrawers import SquareModuleDrawer, RoundedModuleDrawer, CircleModuleDrawer
from sqlalchemy import text, inspect
from sqlalchemy.exc import IntegrityError

import numpy as np
from app.config import Config
from app.extensions import csrf, limiter, scheduler
from app.services import redis_service
from app.services.redis_service import (
    REDIS_CACHE_TTL,
    REDIS_PUBLIC_URL,
    REDIS_URL,
    _redis_acquire_lock,
    _redis_cache_key,
    _redis_candidate_urls,
    _redis_delete,
    _redis_get,
    _redis_set,
    get_redis_client,
    get_task_queue,
)
from app.services.scheduler_service import configure_notification_scheduler
from app.services.qr_service import generate_qr_code
from app.services.barcode_service import generate_barcode_code128
from utils import (
    UPLOAD_FOLDER, GENERATED_FOLDER, FONTS_FOLDER, PLACEHOLDER_PATH,TEMPLATES_CONFIG, 
    DUPLICATE_CONFIG_PATH,A4_WIDTH_PX, A4_HEIGHT_PX,A4_LANDSCAPE_WIDTH_PX, A4_LANDSCAPE_HEIGHT_PX,DPI,
    STATIC_DIR,
    get_template_settings, get_template_path, get_card_size, apply_text_case,
    get_default_font_config, get_default_photo_config, get_default_qr_config,
    get_photo_settings_for_orientation, get_font_settings_for_orientation,
    get_template_orientation, load_template, load_template_smart, round_photo, is_valid_font_file,
    get_available_fonts, load_font_dynamic, generate_data_hash,process_text_for_drawing, download_font_if_missing
    ,flip_x_for_text_direction, get_draw_text_kwargs, trim_transparent_edges, force_rgb,
    get_cloudinary_face_crop_url, get_storage_backend, parse_layout_config, get_field_layout_item,
    split_label_and_colon, colon_anchor_for_value, get_template_language_direction,
    get_template_layout_config, get_anchor_max_text_width, get_layout_flow_start_y,
    derive_font_settings_from_layout_config
    ,get_localized_standard_labels, normalize_photo_shape
)
from cloudinary_config import upload_image
from models import db, Student, Template, TemplateField, ActivityLog, NotificationPreference, NotificationLog, KeyboardLanguagePreference, AdminUser, TemplateVersion, TemplateWorkflow, ImmutableAuditEvent, BulkJob, BulkJobItem, ImportMapping
from app.services.template_lifecycle_service import create_template_version_snapshot, log_immutable_audit_event, get_session_actor
from app.services.notification_service import (
    notify_deadline_approaching, notify_card_ready, notify_generation_error,
    check_and_notify_approaching_deadlines
)
from keyboard_switcher import (
    get_keyboard_config, generate_keyboard_switcher_script, 
    generate_keyboard_control_html, get_all_keyboard_languages
)
from flask_limiter.errors import RateLimitExceeded
# Initialize Thread Executor (limit workers to prevent memory overload)
max_workers = min(32, os.cpu_count() * 2)
executor = ThreadPoolExecutor(max_workers=max_workers)


# Modular service imports for refactored photo and render pipelines
from app.services.render_service import (
    render_student_card_side_background,
    render_student_card_side,
    build_student_card_text_runs,
    draw_aligned_colon_pil,
    draw_text_gradient,
    _get_cached_photo,
    _get_cached_final_card,
    _render_qr_and_barcode,
    _photo_settings_dimensions,
    _load_card_photo_image,
    _render_student_photo,
    _build_card_field_list,
    _render_student_fields,
    _load_template_image_for_render_cached,
    _load_template_image_for_render,
    _get_render_dynamic_fields
)
from app.services.photo_service import (
    split_photo_reference,
    resolve_student_photo_reference,
    load_student_photo_rgba,
    load_student_photo_rgba_prepared,
    photo_match_aliases,
    auto_crop_face_photo,
    _process_photo_pil,
    _prepare_uploaded_student_photo_bytes,
    _prepare_student_photo_image_bytes,
    _prepare_camera_student_photo_bytes
)
from app.services.bulk_job_service import (
    _set_bulk_job_state,
    _get_bulk_job_state,
    _list_bulk_job_states,
    _publish_bulk_job_errors,
)


def _cleanup_lost_bulk_jobs():
    try:
        os.makedirs("instance", exist_ok=True)
        filepath = os.path.join("instance", "bulk_jobs.json")
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                disk_jobs = json.load(f)
            updated = False
            for task_id, job in list(disk_jobs.items()):
                if job.get("state") in ("PENDING", "PROCESSING"):
                    job["state"] = "FAILED"
                    job["status"] = "Task lost due to server restart. Please try again."
                    updated = True
            if updated:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(disk_jobs, f, default=str)
    except Exception as e:
        pass

_cleanup_lost_bulk_jobs()

from logging.handlers import RotatingFileHandler
import warnings
import time
from types import SimpleNamespace
warnings.filterwarnings("ignore", message="SymbolDatabase.GetPrototype() is deprecated")
logger = logging.getLogger(__name__)

# Configure structured JSON logging
try:
    from app.logging_config import setup_logging
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
except ImportError:
    pass  # python-json-logger not installed yet

# Extracted to app/services/font_service.py — backward-compat re-export
from app.services.font_service import (  # noqa: E402
    fit_loaded_font_to_single_line,
    fit_dynamic_font_to_single_line,
)





# Storage mode:
# - local: store templates/photos/cards under `static/` (best for local development)
# - cloudinary: store on Cloudinary (best for deployed environments)
# - auto (default): Cloudinary if credentials exist, else local
STORAGE_BACKEND = get_storage_backend()


# Extracted to app/services/cache_service.py — backward-compat re-export
from app.services.cache_service import (  # noqa: E402
    _get_cached_media_image,
    _get_cached_qr_image,
    _get_cached_barcode_image,
)


# Extracted to app/services/student_service.py — backward-compat re-export
from app.services.student_service import (  # noqa: E402
    _build_student_form_data,
    _build_student_image_ref,
    _build_qr_hash,
    _build_payload,
)




















# Extracted to app/services/cache_service.py — backward-compat re-export
from app.services.cache_service import with_cache_bust  # noqa: E402

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

from PIL import Image, ImageOps



# ================== App Config ==================
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
app = Flask(__name__, root_path=PROJECT_ROOT)
app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024  # 250MB upload limit for bulk generation

from app.config import get_config
app.config.from_object(get_config())
app.secret_key = app.config["SECRET_KEY"]
if not app.secret_key:
    raise RuntimeError(
        "SECRET_KEY is not set. Set it in your .env file or environment. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )



# When running locally, keep templates auto-reloading so HTML changes (e.g. i18n/RTL tweaks)
# take effect without needing debug mode.
try:
    app.config["TEMPLATES_AUTO_RELOAD"] = Config.TEMPLATES_AUTO_RELOAD
    app.jinja_env.auto_reload = True
except Exception:
    pass

# Token Serializer
serializer = URLSafeTimedSerializer(app.secret_key)

limiter.init_app(app)
csrf.init_app(app)

scheduler = configure_notification_scheduler(check_and_notify_approaching_deadlines)

# Production middleware (request ID, security headers, CORS, CSRF exemptions, auth context)
try:
    from app.middleware import init_middleware
    init_middleware(app)
except Exception as exc:
    logger.warning("Middleware initialization failed: %s", exc)

# Sentry error tracking
try:
    from app.sentry_config import init_sentry
    init_sentry(app)
except Exception as exc:
    logger.warning("Sentry initialization failed: %s", exc)

# Observability (metrics, health checks, timing, slow query logging)
try:
    from app.observability import init_observability
    init_observability(app)
except Exception as exc:
    logger.warning("Observability initialization failed: %s", exc)

# Performance optimizations (caching, lazy loading, connection pooling)
try:
    from app.performance import init_performance
    init_performance(app)
except Exception as exc:
    logger.warning("Performance initialization failed: %s", exc)

# Celery event-driven task queue
try:
    from app.celery_config import make_celery
    celery_app = make_celery(app)
    app.extensions["celery"] = celery_app
    logger.info("Celery initialized")
except Exception as exc:
    logger.warning("Celery initialization failed (tasks will use fallback): %s", exc)

# Real-time collaboration (WebSocket)
try:
    from app.services.collaboration import init_socketio
    socketio = init_socketio(app)
    if socketio:
        app.extensions["socketio"] = socketio
        logger.info("SocketIO initialized")
except Exception as exc:
    logger.warning("SocketIO initialization failed: %s", exc)

# Multi-tenant middleware
try:
    from app.services.tenant import init_tenant_middleware
    init_tenant_middleware(app)
except Exception as exc:
    logger.warning("Tenant middleware initialization failed: %s", exc)

# GraphQL API
try:
    from app.api.graphql import init_graphql_view
    init_graphql_view(app)
except Exception as exc:
    logger.warning("GraphQL initialization failed: %s", exc)

if not app.debug:
    # Create logs directory if it doesn't exist
    if not os.path.exists('logs'):
        os.mkdir('logs')
        
    # Use a safer rotating handler on Windows; keep size large enough to avoid constant rollovers.
    file_handler = SafeRotatingFileHandler('logs/app.log', maxBytes=5 * 1024 * 1024, backupCount=10, delay=True)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    
    app.logger.setLevel(logging.INFO)
    app.logger.info('ID Card Generator Startup')

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


def login_required(f):
    """Fix 3: Centralized auth decorator for admin routes.
    Usage: @login_required above a route function.
    Redirects to /admin/login if the session has no valid admin key.
    """
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin"):
            from flask import redirect, url_for, request, jsonify
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
            from flask import redirect, url_for, request, jsonify
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
            from flask import redirect, url_for, request, jsonify
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
            from flask import redirect, url_for, request, jsonify
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
            from flask import redirect, url_for, request, jsonify
            if request.is_json or request.path.startswith('/api') or request.path.startswith('/corel'):
                return jsonify({"success": False, "error": "Unauthorized"}), 403
            return redirect(url_for("auth.student_login"))
        return f(*args, **kwargs)
    return decorated_function


DATABASE_URL = app.config["SQLALCHEMY_DATABASE_URI"]

db.init_app(app)


@app.errorhandler(RateLimitExceeded)
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


@app.errorhandler(400)
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
@app.errorhandler(413)
def handle_payload_too_large(e):
    """Handle file uploads exceeding MAX_CONTENT_LENGTH."""
    logger.warning("413 Payload Too Large: %s", request.path)
    max_mb = app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)
    if request.is_json or request.path.startswith('/api') or request.path.startswith('/bulk'):
        return jsonify({"success": False, "error": f"File too large. Maximum upload size is {max_mb}MB."}), 413
    from flask import flash, redirect, request as req
    flash(f"File too large. Maximum upload size is {max_mb}MB.", "error")
    return redirect(req.referrer or '/')

@app.errorhandler(422)
def handle_unprocessable_entity(e):
    """Handle validation errors."""
    logger.warning("422 Unprocessable Entity: %s - %s", request.path, str(e))
    if request.is_json or request.path.startswith('/api'):
        return jsonify({"success": False, "error": str(e) or "Validation failed"}), 422
    from flask import flash, redirect, request as req
    flash(str(e) or "Validation failed. Please check your input.", "error")
    return redirect(req.referrer or '/')


@app.errorhandler(404)
def not_found_error(error):
    logger.warning(f"404 Not Found: {request.path}")
    if request.is_json or request.path.startswith('/api'):
        return jsonify({"success": False, "error": "Resource not found"}), 404
    return "404 - Page Not Found", 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    logger.error(f"500 Internal Server Error: {error}")
    if request.is_json or request.path.startswith('/api'):
        return jsonify({"success": False, "error": "An internal server error occurred"}), 500
    return "500 - Internal Server Error", 500

@app.after_request
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

# Add the rgb_to_hex template filter
@app.template_filter('rgb_to_hex')
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


# ================== Email Config ==================
EMAIL_FROM = Config.EMAIL_FROM
EMAIL_PASSWORD = Config.EMAIL_PASSWORD
SMTP_SERVER = Config.SMTP_SERVER
SMTP_PORT = Config.SMTP_PORT
ADMIN_USERNAME = Config.ADMIN_USERNAME  # Change in production
ADMIN_PASSWORD_HASH = Config.ADMIN_PASSWORD_HASH  # MUST be a pbkdf2:sha256 hash

# ================== Student Blueprint ==================
student_bp = Blueprint('student', __name__)

# ================== Helper Functions ==================
# Extracted to app/services/layout_service.py — backward-compat re-export
from app.services.layout_service import get_form_int, order_to_field_key  # noqa: E402


def resolve_field_layout(template_obj, field_key, default_label_x, default_value_x, default_y):
    """Return label/value x-y-visibility for one field from template.layout_config."""
    return resolve_field_layout_for_side(template_obj, field_key, default_label_x, default_value_x, default_y, side="front")


def get_template_field_side_flags(template_obj, field_key, side="front"):
    """Return default label/value visibility for a dynamic field on the requested side."""
    if not template_obj or not field_key:
        return None

    cache = getattr(template_obj, "_field_side_visibility_cache", None)
    if cache is None:
        cache = {}
        try:
            db_fields = TemplateField.query.filter_by(template_id=template_obj.id).order_by(TemplateField.display_order.asc()).all()
        except Exception:
            db_fields = []
        for field in db_fields:
            cache[field.field_name] = {
                "front": {
                    "label": bool(getattr(field, "show_label_front", True)),
                    "value": bool(getattr(field, "show_value_front", True)),
                },
                "back": {
                    "label": bool(getattr(field, "show_label_back", False)),
                    "value": bool(getattr(field, "show_value_back", False)),
                },
            }
        setattr(template_obj, "_field_side_visibility_cache", cache)

    field_flags = cache.get(field_key)
    if not field_flags:
        return None

    side_name = "back" if str(side or "front").strip().lower() == "back" else "front"
    chosen = field_flags.get(side_name) or {}
    return {
        "label_visible": bool(chosen.get("label", True)),
        "value_visible": bool(chosen.get("value", True)),
        "colon_visible": bool(chosen.get("label", True)),
    }


def resolve_field_layout_for_side(template_obj, field_key, default_label_x, default_value_x, default_y, side="front"):
    """Return label/value x-y-visibility for one field from the selected side layout config."""
    side_name = str(side or "front").strip().lower()
    text_direction = "ltr"
    try:
        direction_attr = "back_text_direction" if side_name == "back" else "text_direction"
        text_direction = (getattr(template_obj, direction_attr, "ltr") or "ltr").strip().lower()
    except Exception:
        text_direction = "ltr"

    layout_config = getattr(template_obj, "back_layout_config", None) if side_name == "back" else getattr(template_obj, "layout_config", None)
    if getattr(template_obj, "_ignore_layout_field_overrides", False):
        layout_config = keep_layout_field_visibility_only(layout_config)
    field_side_flags = get_template_field_side_flags(template_obj, field_key, side=side_name)
    default_visibility = field_side_flags or {}
    default_label_visible = default_visibility.get("label_visible", True)
    default_value_visible = default_visibility.get("value_visible", True)
    default_colon_visible = default_visibility.get("colon_visible", default_label_visible)

    if not field_key:
        return {
            "label_x": default_label_x,
            "label_y": default_y,
            "label_visible": default_label_visible,
            "label_grow": "right" if text_direction == "rtl" else "left",
            "label_font_size": None,
            "value_x": default_value_x,
            "value_y": default_y,
            "value_visible": default_value_visible,
            "value_grow": "right" if text_direction == "rtl" else "left",
            "value_font_size": None,
            "colon_x": None,
            "colon_y": default_y,
            "colon_visible": default_colon_visible,
            "colon_grow": "left" if text_direction == "rtl" else "right",
            "colon_font_size": None,
        }
    return get_field_layout_item(
        layout_config,
        field_key,
        default_label_x,
        default_value_x,
        default_y,
        text_direction=text_direction,
        default_label_visible=default_label_visible,
        default_value_visible=default_value_visible,
        default_colon_visible=default_colon_visible,
        prefer_nested_part_layout=field_side_flags is not None,
    )


def get_initial_flow_y_for_side(template_obj, font_settings, side="front"):
    side_name = "back" if str(side or "front").strip().lower() == "back" else "front"
    try:
        default_start_y = int((font_settings or {}).get("start_y", 0) or 0)
    except Exception:
        default_start_y = 0

    if not template_obj:
        return default_start_y

    layout_config = getattr(template_obj, "back_layout_config", None) if side_name == "back" else getattr(template_obj, "layout_config", None)
    if getattr(template_obj, "_ignore_layout_field_overrides", False):
        return default_start_y
    visibility_map = {}
    try:
        db_fields = TemplateField.query.filter_by(template_id=template_obj.id).order_by(TemplateField.display_order.asc()).all()
    except Exception:
        db_fields = []

    for field in db_fields:
        visibility_map[field.field_name] = {
            "label": bool(getattr(field, "show_label_back" if side_name == "back" else "show_label_front", side_name != "back")),
            "value": bool(getattr(field, "show_value_back" if side_name == "back" else "show_value_front", side_name != "back")),
        }

    return get_layout_flow_start_y(layout_config, default_start_y, visibility_map)


def get_template_language_direction_from_obj(template_obj, side="front"):
    side_name = str(side or "front").strip().lower()
    if side_name == "back":
        lang = (getattr(template_obj, "back_language", None) or getattr(template_obj, "language", "english") or "english").strip().lower()
        direction = (getattr(template_obj, "back_text_direction", None) or getattr(template_obj, "text_direction", "ltr") or "ltr").strip().lower()
    else:
        lang = (getattr(template_obj, "language", "english") or "english").strip().lower()
        direction = (getattr(template_obj, "text_direction", "ltr") or "ltr").strip().lower()
    if direction == "rtl" and lang == "english":
        lang = "urdu"
    return lang, direction


SUPPORTED_TEMPLATE_LANGUAGES = {"english", "urdu", "hindi", "arabic"}
LANGUAGE_TO_TRANSLATE_CODE = {
    "english": "en",
    "urdu": "ur",
    "hindi": "hi",
    "arabic": "ar",
}
NON_TRANSLATABLE_FIELD_KEYS = {"DOB", "MOBILE"}
NON_TRANSLATABLE_FIELD_TYPES = {"date", "number", "tel", "email"}


# Extracted to app/services/translation_service.py — backward-compat re-export
from app.services.translation_service import (  # noqa: E402
    default_text_direction_for_language,
    validate_double_sided_language_pair,
    _should_skip_translation,
    _extract_google_translate_text,
    detect_translation_source_language,
    _google_translate_text,
    translate_value_for_template_side,
    get_template_language_direction_from_obj,
)


# Extracted to app/services/layout_service.py — backward-compat re-export
from app.services.layout_service import (  # noqa: E402
    field_consumes_layout_space,
    field_advances_layout_flow,
    field_within_vertical_bounds,
)


def apply_layout_custom_objects_pil(template_img, template_obj, font_settings, side="front", language="english", render_scale=1.0):
    """Render free custom editor objects stored in layout_config.objects."""
    if template_img is None or template_obj is None:
        return
    layout_config_raw = getattr(template_obj, "back_layout_config", None) if str(side or "front").lower() == "back" else getattr(template_obj, "layout_config", None)
    parsed = parse_layout_config(layout_config_raw)
    objects = parsed.get("objects") if isinstance(parsed, dict) else None
    if not isinstance(objects, list) or not objects:
        return

    draw = ImageDraw.Draw(template_img)
    scale = max(1.0, float(render_scale or 1.0))
    font_reg_path = os.path.join(FONTS_FOLDER, font_settings.get("font_regular", "arial.ttf"))
    font_bold_path = os.path.join(FONTS_FOLDER, font_settings.get("font_bold", "arialbd.ttf"))

    def _paste_rotated_overlay(overlay_img, left, top, angle):
        if overlay_img is None:
            return
        angle = float(angle or 0)
        if abs(angle) < 0.01:
            template_img.paste(overlay_img, (int(left), int(top)), overlay_img)
            return
        rotated = overlay_img.rotate(-angle, expand=True, resample=Image.BICUBIC)
        offset_x = int(round(left - ((rotated.width - overlay_img.width) / 2)))
        offset_y = int(round(top - ((rotated.height - overlay_img.height) / 2)))
        template_img.paste(rotated, (offset_x, offset_y), rotated)

    for obj in objects:
        if not isinstance(obj, dict) or not obj.get("visible", True):
            continue
        obj_type = str(obj.get("type") or "").strip().lower()
        x = int(round((obj.get("x", 0) or 0) * scale))
        y = int(round((obj.get("y", 0) or 0) * scale))
        angle = float(obj.get("angle", 0) or 0)
        opacity = max(0, min(100, int(obj.get("opacity", 100) or 100)))
        alpha = int(round((opacity / 100.0) * 255))
        fill = _hex_to_rgb_for_editor(obj.get("fill"), (11, 63, 138))
        stroke = _hex_to_rgb_for_editor(obj.get("stroke"), fill)
        stroke_width = max(1, int(round((obj.get("stroke_width", 2) or 2) * scale)))
        fill_rgba = tuple(fill) + (alpha,)
        stroke_rgba = tuple(stroke) + (alpha,)

        overlay = None
        paste_x, paste_y = x, y

        if obj_type == "rect":
            w = max(1, int(round((obj.get("width", 120) or 120) * scale)))
            h = max(1, int(round((obj.get("height", 60) or 60) * scale)))
            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw_ctx = ImageDraw.Draw(overlay)
            rx = int(round((obj.get("rx", 0) or 0) * scale))
            ry = int(round((obj.get("ry", 0) or 0) * scale))
            if rx > 0 or ry > 0:
                try:
                    draw_ctx.rounded_rectangle([0, 0, w, h], radius=max(rx, ry), fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
                except AttributeError:
                    draw_ctx.rectangle([0, 0, w, h], fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
            else:
                draw_ctx.rectangle([0, 0, w, h], fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
        elif obj_type == "circle":
            w = max(1, int(round((obj.get("width", 80) or 80) * scale)))
            h = max(1, int(round((obj.get("height", obj.get("width", 80)) or obj.get("width", 80)) * scale)))
            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(overlay).ellipse([0, 0, w, h], fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
        elif obj_type == "line":
            x2 = int(round((obj.get("x2", obj.get("x", 0) + 120) or (obj.get("x", 0) + 120)) * scale))
            y2 = int(round((obj.get("y2", obj.get("y", 0)) or obj.get("y", 0)) * scale))
            min_x = min(x, x2)
            min_y = min(y, y2)
            line_w = max(1, abs(x2 - x) + stroke_width + 4)
            line_h = max(1, abs(y2 - y) + stroke_width + 4)
            start_x = 2 if x <= x2 else line_w - 2
            start_y = 2 if y <= y2 else line_h - 2
            end_x = line_w - 2 if x <= x2 else 2
            end_y = line_h - 2 if y <= y2 else 2
            overlay = Image.new("RGBA", (line_w, line_h), (0, 0, 0, 0))
            ImageDraw.Draw(overlay).line([start_x, start_y, end_x, end_y], fill=stroke_rgba, width=stroke_width)
            paste_x, paste_y = min_x, min_y
        elif obj_type == "image":
            src = str(obj.get("src") or "").strip()
            if not src:
                continue
            try:
                if src.startswith("data:image"):
                    header, encoded = src.split(",", 1)
                    image_bytes = base64.b64decode(encoded)
                    overlay = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
                elif src.startswith(("http://", "https://")):
                    resp = requests.get(src, timeout=10)
                    resp.raise_for_status()
                    overlay = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                else:
                    image_path = src if os.path.isabs(src) else os.path.join(STATIC_DIR, src.lstrip("/"))
                    overlay = Image.open(image_path).convert("RGBA")
                w = max(1, int(round((obj.get("width", overlay.width) or overlay.width) * scale)))
                h = max(1, int(round((obj.get("height", overlay.height) or overlay.height) * scale)))
                overlay = overlay.resize((w, h), Image.LANCZOS)
                if alpha < 255:
                    alpha_channel = overlay.getchannel("A").point(lambda px: int(px * (alpha / 255.0)))
                    overlay.putalpha(alpha_channel)
            except Exception as image_err:
                logger.warning("Skipping custom image object due to render error: %s", image_err)
                continue
        elif obj_type == "text":
            text = str(obj.get("text") if obj.get("text") is not None else "Text")
            if not text:
                continue
            font_size = max(8, int(round((obj.get("font_size", 24) or 24) * scale)))
            font_family = obj.get("font_family")
            font_path = None
            if font_family:
                download_font_if_missing(font_family)
                clean_family = font_family.replace(" ", "")
                local_font_file = os.path.join(FONTS_FOLDER, f"{clean_family}.ttf")
                if os.path.exists(local_font_file):
                    font_path = local_font_file
                else:
                    local_font_file = os.path.join(FONTS_FOLDER, f"{font_family}.ttf")
                    if os.path.exists(local_font_file):
                        font_path = local_font_file
                    else:
                        local_font_file = os.path.join(FONTS_FOLDER, f"{font_family}-Regular.ttf")
                        if os.path.exists(local_font_file):
                            font_path = local_font_file
            if not font_path:
                font_path = font_bold_path if bool(obj.get("bold")) else font_reg_path
            char_spacing = int(obj.get("char_spacing" or 0) or 0)
            auto_fit = bool(obj.get("auto_fit", False))
            auto_fit_width = int(obj.get("auto_fit_width" or 200) or 200)

            # Auto-fit logic
            if auto_fit and auto_fit_width:
                max_w_val = float(auto_fit_width) * scale
                while font_size > 6:
                    temp_font = load_font_dynamic(font_path, text, template_img.width, font_size, language=language)
                    char_space_px = font_size * (char_spacing / 1000.0) * scale
                    w = 0.0
                    for char in text:
                        w += draw.textlength(char, font=temp_font, **get_draw_text_kwargs(text, language))
                    w += char_space_px * (len(text) - 1)
                    if w <= max_w_val:
                        break
                    font_size -= 1

            font = load_font_dynamic(font_path, text, template_img.width, font_size, language=language)
            char_space_px = font_size * (char_spacing / 1000.0) * scale

            bbox = draw.textbbox((0, 0), text, font=font, **get_draw_text_kwargs(text, language))
            spaced_w = 0.0
            for char in text:
                spaced_w += draw.textlength(char, font=font, **get_draw_text_kwargs(text, language))
            spaced_w += char_space_px * (len(text) - 1)
            
            text_w = max(1, int(spaced_w) + 6)
            text_h = max(1, (bbox[3] - bbox[1]) + 6)
            overlay = Image.new("RGBA", (text_w, text_h), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            
            # Draw letter by letter with spacing
            cursor_x = 3 - bbox[0]
            is_rtl = (language in ("urdu", "arabic") or any(ord(c) >= 0x0600 and ord(c) <= 0x06FF for c in text))
            if is_rtl:
                overlay_draw.text((3 - bbox[0], 3 - bbox[1]), text, font=font, fill=fill_rgba, **get_draw_text_kwargs(text, language))
            else:
                for char in text:
                    overlay_draw.text((cursor_x, 3 - bbox[1]), char, font=font, fill=fill_rgba, **get_draw_text_kwargs(text, language))
                    cursor_x += draw.textlength(char, font=font, **get_draw_text_kwargs(text, language)) + char_space_px

        if overlay is not None:
            if obj.get("flip_x"):
                overlay = overlay.transpose(Image.FLIP_LEFT_RIGHT)
            if obj.get("flip_y"):
                overlay = overlay.transpose(Image.FLIP_TOP_BOTTOM)

            shadow_color_hex = obj.get("shadow_color")
            shadow_blur = int(round((obj.get("shadow_blur", 0) or 0) * scale))
            shadow_offset_x = int(round((obj.get("shadow_offset_x", 0) or 0) * scale))
            shadow_offset_y = int(round((obj.get("shadow_offset_y", 0) or 0) * scale))

            if shadow_color_hex and (shadow_blur > 0 or shadow_offset_x != 0 or shadow_offset_y != 0):
                from PIL import ImageFilter
                shadow_rgb = _hex_to_rgb_for_editor(shadow_color_hex, (0, 0, 0))
                alpha_mask = overlay.getchannel("A")
                shadow_img = Image.new("RGBA", overlay.size, shadow_rgb + (alpha,))
                shadow_img.putalpha(alpha_mask)
                
                if shadow_blur > 0:
                    pad = shadow_blur * 2
                    padded_shadow = Image.new("RGBA", (overlay.width + 2 * pad, overlay.height + 2 * pad), (0, 0, 0, 0))
                    padded_shadow.paste(shadow_img, (pad, pad))
                    padded_shadow = padded_shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
                    _paste_rotated_overlay(padded_shadow, paste_x + shadow_offset_x - pad, paste_y + shadow_offset_y - pad, angle)
                else:
                    _paste_rotated_overlay(shadow_img, paste_x + shadow_offset_x, paste_y + shadow_offset_y, angle)

            _paste_rotated_overlay(overlay, paste_x, paste_y, angle)


# Extracted to app/services/template_upload_service.py — backward-compat re-export
from app.services.template_upload_service import _looks_like_pdf_template_source  # noqa: E402
from app.services.render_service import _flatten_to_rgb  # noqa: E402
from app.services.layout_service import _hex_to_rgb_for_editor  # noqa: E402



# ================== Database Initialization ==================
def init_db():
    """Initialize database with SQLAlchemy"""
    try:
        db.create_all()
        logger.info("Database initialized successfully with SQLAlchemy")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise

def check_deadline_passed(template_id):
    """
    Returns True if deadline has passed, False otherwise.
    Uses local server time for comparison.
    """
    if not template_id:
        return False, None
        
    try:
        template = db.session.get(Template, int(template_id))
        
        # If no deadline is set in DB, return False (Open indefinitely)
        if not template or not template.deadline:
            return False, None

        # Get current time (Local System Time)
        now = datetime.now()
        deadline = template.deadline

        # Debugging Logs
        logger.info(f"--- DEADLINE CHECK for Template {template_id} ---")
        logger.info(f"Current Time: {now}")
        logger.info(f"Deadline:     {deadline}")

        if now > deadline:
            logger.warning("Deadline has PASSED.")
            return True, deadline.strftime("%d %B %Y, %I:%M %p")
        
        logger.info("Deadline is in the future.")
        return False, None
    except Exception as e:
        logger.error(f"Error checking deadline: {e}")
        return False, None

# ================== Helper Function ==================
def log_activity(action, target=None, details=None):
    """
    Helper function to log administrative or user actions to the database.
    Silent fail: If logging fails, it logs an error but doesn't crash the app.
    """
    try:
        # Determine who is acting
        if session.get('admin'):
            actor = "Admin"
        elif session.get('student_email'):
            actor = session['student_email']
        else:
            actor = "Anonymous"

        # Create log entry
        log = ActivityLog(
            actor=actor,
            action=action,
            target=str(target) if target else None,
            details=str(details) if details else None,
            ip_address=request.remote_addr,
            timestamp=datetime.now(timezone.utc)
        )
        
        db.session.add(log)
        db.session.commit()
        
    except Exception as e:
        # We catch all exceptions so the main app flow isn't interrupted by a logging failure
        logger.error(f"Failed to log activity: {e}")
        db.session.rollback()
# ================== Activity Log Page Route ==================


# ================== School Admin Management ==================


def _quote_db_identifier(identifier):
    """Quote a table or column name for the active SQLAlchemy dialect."""
    return db.engine.dialect.identifier_preparer.quote(identifier)


def _run_schema_ddl(sql, success_message, *, warning_message=None):
    """
    Run one schema DDL statement in its own transaction.

    PostgreSQL aborts the whole transaction after any failed statement. Keeping
    each ALTER isolated prevents a harmless/expected migration miss from rolling
    back earlier columns that were successfully added.
    """
    try:
        with db.engine.begin() as conn:
            conn.execute(text(sql))
        logger.info(success_message)
        return True
    except Exception as e:
        logger.warning("%s: %s", warning_message or "Schema migration skipped/failed", e)
        return False


def _get_table_column_names(inspector, table_name):
    try:
        return {c["name"] for c in inspector.get_columns(table_name)}
    except Exception as e:
        logger.warning(f"Could not inspect table '{table_name}': {e}")
        return set()


def _add_column_if_missing(table_name, column_name, column_type_sql):
    inspector = inspect(db.engine)
    if column_name in _get_table_column_names(inspector, table_name):
        return False

    table_sql = _quote_db_identifier(table_name)
    column_sql = _quote_db_identifier(column_name)
    return _run_schema_ddl(
        f"ALTER TABLE {table_sql} ADD COLUMN {column_sql} {column_type_sql}",
        f"Added '{column_name}' column to {table_name}",
        warning_message=f"Could not add {table_name}.{column_name}",
    )


def sync_model_columns_to_database():
    """
    Best-effort additive schema sync for Railway/Postgres and local SQLite.

    This app historically used db.create_all() plus hand-written ALTER TABLE
    statements. create_all() does not add columns to existing tables, so older
    deployed databases can miss fields that the SQLAlchemy models now select.
    """
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    dialect = db.engine.dialect

    for mapper in sorted(db.Model.registry.mappers, key=lambda m: m.local_table.name):
        table = mapper.local_table
        table_name = table.name
        if table_name not in existing_tables:
            continue

        existing_columns = _get_table_column_names(inspector, table_name)
        for column in table.columns:
            if column.name in existing_columns or column.primary_key:
                continue

            try:
                column_type_sql = column.type.compile(dialect=dialect)
            except Exception as e:
                logger.warning(f"Could not compile type for {table_name}.{column.name}: {e}")
                continue

            _add_column_if_missing(table_name, column.name, column_type_sql)


def migrate_database():
    """Migration function to create tables and update schema"""
    try:
        with app.app_context():
            # 1. Create tables if they don't exist (This creates 'activity_logs')
            db.create_all()

            # 1b. Add any model columns missing from older Railway/Postgres tables.
            # This fixes errors like: column students.back_image_url does not exist.
            sync_model_columns_to_database()
            
            # 2. Check for missing columns in existing tables
            inspector = inspect(db.engine)
            
            # --- Migrate STUDENTS table ---
            s_columns = [c['name'] for c in inspector.get_columns('students')]
            with db.engine.connect() as conn:
                if 'sheet_filename' not in s_columns:
                    conn.execute(text("ALTER TABLE students ADD COLUMN sheet_filename VARCHAR(255)"))
                    logger.info("Added 'sheet_filename' column to students")
                
                if 'sheet_position' not in s_columns:
                    conn.execute(text("ALTER TABLE students ADD COLUMN sheet_position INTEGER"))
                    logger.info("Added 'sheet_position' column to students")
                if 'back_image_url' not in s_columns:
                    conn.execute(text("ALTER TABLE students ADD COLUMN back_image_url VARCHAR(1024)"))
                    logger.info("Added 'back_image_url' column to students")
                if 'back_generated_filename' not in s_columns:
                    conn.execute(text("ALTER TABLE students ADD COLUMN back_generated_filename VARCHAR(255)"))
                    logger.info("Added 'back_generated_filename' column to students")

                # --- Migrate TEMPLATES table ---
                t_columns = [c['name'] for c in inspector.get_columns('templates')]

                # 0. Remove NOT NULL constraint from filename (allow Cloudinary-only templates)
                try:
                    # For PostgreSQL
                    conn.execute(text("ALTER TABLE templates ALTER COLUMN filename DROP NOT NULL"))
                    logger.info("Removed NOT NULL constraint from templates.filename")
                except Exception:
                    # SQLite doesn't support this easily, skip
                    pass
                
                # 1. Add Language Column
                if 'language' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN language VARCHAR(20) DEFAULT 'english'"))
                    logger.info("Added 'language' column to templates")
                
                # 2. Add Text Direction Column
                if 'text_direction' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN text_direction VARCHAR(10) DEFAULT 'ltr'"))
                    logger.info("Added 'text_direction' column to templates")
                
                # 3. Add Template URL Column (for Cloudinary storage)
                if 'template_url' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN template_url TEXT"))
                    logger.info("Added 'template_url' column to templates")
                if 'back_filename' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_filename VARCHAR(255)"))
                    logger.info("Added 'back_filename' column to templates")
                if 'back_template_url' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_template_url TEXT"))
                    logger.info("Added 'back_template_url' column to templates")
                if 'back_font_settings' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_font_settings JSON"))
                    logger.info("Added 'back_font_settings' column to templates")
                if 'back_photo_settings' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_photo_settings JSON"))
                    logger.info("Added 'back_photo_settings' column to templates")
                if 'back_qr_settings' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_qr_settings JSON"))
                    logger.info("Added 'back_qr_settings' column to templates")
                if 'back_layout_config' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_layout_config TEXT"))
                    logger.info("Added 'back_layout_config' column to templates")
                if 'back_language' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_language VARCHAR(20) DEFAULT 'english'"))
                    logger.info("Added 'back_language' column to templates")
                if 'back_text_direction' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN back_text_direction VARCHAR(10) DEFAULT 'ltr'"))
                    logger.info("Added 'back_text_direction' column to templates")
                if 'is_double_sided' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN is_double_sided BOOLEAN DEFAULT 0"))
                    logger.info("Added 'is_double_sided' column to templates")
                if 'duplex_flip_mode' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN duplex_flip_mode VARCHAR(20) DEFAULT 'long_edge'"))
                    logger.info("Added 'duplex_flip_mode' column to templates")
                
                # List of new columns to check and add
                new_cols = [
                    ('deadline', 'DATETIME'),
                    ('card_width', 'INTEGER DEFAULT 1015'),
                    ('card_height', 'INTEGER DEFAULT 661'),
                    ('sheet_width', 'INTEGER DEFAULT 2480'),
                    ('sheet_height', 'INTEGER DEFAULT 3508'),
                    ('grid_rows', 'INTEGER DEFAULT 5'),
                    ('grid_cols', 'INTEGER DEFAULT 2')
                ]

                for col_name, col_type in new_cols:
                    if col_name not in t_columns:
                        try:
                            conn.execute(text(f"ALTER TABLE templates ADD COLUMN {col_name} {col_type}"))
                            logger.info(f"Added '{col_name}' column to templates")
                        except Exception as e:
                            logger.warning(f"Could not add {col_name}: {e}")

                # --- Migrate TEMPLATE_FIELDS table (legacy schema compatibility) ---
                # Some older databases created a `template_fields` table with only:
                # (template_id, field_id, display_order). The current app expects a richer schema
                # with an autoincrement `id` plus `field_name/field_label/field_type/...`.
                try:
                    table_names = set(inspector.get_table_names())
                    if "template_fields" in table_names:
                        tf_columns = [c["name"] for c in inspector.get_columns("template_fields")]
                        required_cols = {
                            "id",
                            "template_id",
                            "field_name",
                            "field_label",
                            "field_type",
                            "is_required",
                            "display_order",
                            "field_options",
                        }
                        if not required_cols.issubset(set(tf_columns)):
                            if db.engine.dialect.name == "sqlite":
                                legacy_name = "template_fields_legacy"
                                if legacy_name in table_names:
                                    legacy_name = f"template_fields_legacy_{int(time.time())}"

                                # SQLite can't add a PRIMARY KEY column via ALTER TABLE, so rebuild.
                                conn.execute(text("PRAGMA foreign_keys=OFF"))
                                conn.execute(text(f"ALTER TABLE template_fields RENAME TO {legacy_name}"))
                                conn.execute(text("""
                                    CREATE TABLE template_fields (
                                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                                        template_id INTEGER NOT NULL,
                                        field_name VARCHAR(100) NOT NULL,
                                        field_label VARCHAR(100) NOT NULL,
                                        field_type VARCHAR(50) NOT NULL,
                                        is_required BOOLEAN DEFAULT 0,
                                        show_label_front BOOLEAN DEFAULT 1,
                                        show_value_front BOOLEAN DEFAULT 1,
                                        show_label_back BOOLEAN DEFAULT 0,
                                        show_value_back BOOLEAN DEFAULT 0,
                                        display_order INTEGER DEFAULT 0,
                                        field_options JSON DEFAULT '[]',
                                        FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE
                                    )
                                """))

                                # Best-effort carryover (creates placeholder field names/labels).
                                conn.execute(text(f"""
                                    INSERT INTO template_fields (
                                        template_id, field_name, field_label, field_type, is_required, display_order, field_options
                                    )
                                    SELECT
                                        template_id,
                                        'field_' || COALESCE(CAST(field_id AS TEXT), '0'),
                                        'Field ' || COALESCE(CAST(field_id AS TEXT), '0'),
                                        'text',
                                        0,
                                        COALESCE(display_order, 0),
                                        '[]'
                                    FROM {legacy_name}
                                """))
                                conn.execute(text("PRAGMA foreign_keys=ON"))
                                logger.info(f"Migrated legacy template_fields schema -> new schema (kept old table as {legacy_name})")
                            else:
                                logger.warning("template_fields table schema is legacy/invalid; auto-migration is only implemented for SQLite")
                        else:
                            extra_tf_cols = [
                                ("show_label_front", "BOOLEAN DEFAULT 1"),
                                ("show_value_front", "BOOLEAN DEFAULT 1"),
                                ("show_label_back", "BOOLEAN DEFAULT 0"),
                                ("show_value_back", "BOOLEAN DEFAULT 0"),
                            ]
                            for col_name, col_type in extra_tf_cols:
                                if col_name not in tf_columns:
                                    try:
                                        conn.execute(text(f"ALTER TABLE template_fields ADD COLUMN {col_name} {col_type}"))
                                        logger.info(f"Added '{col_name}' column to template_fields")
                                    except Exception as inner_e:
                                        logger.warning(f"Could not add {col_name} to template_fields: {inner_e}")
                except Exception as e:
                    logger.warning(f"TemplateField migration skipped/failed: {e}")

                # --- Premium defaults backfill (safe/no-op for already populated rows) ---
                try:
                    conn.execute(text("UPDATE students SET verification_revoked = 0 WHERE verification_revoked IS NULL"))
                except Exception:
                    pass
                try:
                    conn.execute(text("UPDATE students SET photo_quality_score = 0 WHERE photo_quality_score IS NULL"))
                except Exception:
                    pass
                try:
                    conn.execute(text("UPDATE students SET photo_quality_status = 'unknown' WHERE photo_quality_status IS NULL"))
                except Exception:
                    pass
                try:
                    # Keep JSON fields non-null for premium settings UIs.
                    conn.execute(text("UPDATE templates SET qa_settings = '{}' WHERE qa_settings IS NULL"))
                    conn.execute(text("UPDATE templates SET batch_rules = '{}' WHERE batch_rules IS NULL"))
                    conn.execute(text("UPDATE templates SET localization_pack = '{}' WHERE localization_pack IS NULL"))
                    conn.execute(text("UPDATE templates SET language_lock_rules = '{}' WHERE language_lock_rules IS NULL"))
                    conn.execute(text("UPDATE templates SET branding_config = '{}' WHERE branding_config IS NULL"))
                    conn.execute(text("UPDATE templates SET print_profile = '{}' WHERE print_profile IS NULL"))
                    conn.execute(text("UPDATE templates SET verification_config = '{}' WHERE verification_config IS NULL"))
                except Exception:
                    pass
                
                conn.commit()

            # Ensure workflow row exists for all templates (backfill safe/no-op for existing)
            try:
                templates_all = Template.query.all()
                for _t in templates_all:
                    existing_wf = TemplateWorkflow.query.filter_by(template_id=_t.id).first()
                    if not existing_wf:
                        db.session.add(TemplateWorkflow(template_id=_t.id, state="draft", updated_by="migration", updated_role="system"))
                db.session.commit()
            except Exception as wf_e:
                db.session.rollback()
                logger.warning(f"Template workflow backfill skipped: {wf_e}")

        logger.info("Database migration check completed")
    except Exception as e:
        logger.error(f"Error during database migration: {e}")

def migrate_template_font_colors():
    """Migrate existing templates to use separate label and value font colors"""
    try:
        templates = Template.query.all()
        migrated_count = 0
        
        for template in templates:
            needs_update = False
            
            # Check if this template needs migration
            if 'font_color' in template.font_settings:
                # Migrate to separate colors
                if 'label_font_color' not in template.font_settings:
                    template.font_settings['label_font_color'] = template.font_settings['font_color']
                    needs_update = True
                if 'value_font_color' not in template.font_settings:
                    template.font_settings['value_font_color'] = template.font_settings['font_color']
                    needs_update = True
                
                if needs_update:
                    migrated_count += 1
                    logger.info(f"Migrated font colors for template {template.id}")
        
        db.session.commit()
        logger.info(f"Font color migration completed: {migrated_count} templates updated")
        
    except Exception as e:
        logger.error(f"Error during font color migration: {e}")

def migrate_photo_settings():
    """Add missing photo config keys to old templates."""
    try:
        templates = Template.query.all()
        updated = 0
        default_photo = get_default_photo_config()
        
        for template in templates:
            if template.photo_settings is None:
                template.photo_settings = {}
            
            needs_update = False
            if "corel_editable_photo_mode" not in template.photo_settings:
                template.photo_settings["corel_editable_photo_mode"] = default_photo["corel_editable_photo_mode"]
                needs_update = True
                
            if needs_update:
                updated += 1
        
        if updated > 0:
            db.session.commit()
            logger.info(f"Migrated photo settings for {updated} templates")
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error migrating photo settings: {e}")


def repair_student_photo_url_recursion():
    """
    Repair bad student records where `photo_url` was accidentally overwritten with the generated
    card image URL (`image_url`). This causes the "thumbnail card inside card" bug when regenerating.

    We can often restore the real photo URL from `photo_filename` (bulk/legacy flows sometimes store
    Cloudinary photo URLs there).
    """
    try:
        # Only scan records that have both URLs set.
        candidates = Student.query.filter(
            Student.photo_url.isnot(None),
            Student.image_url.isnot(None),
        ).all()

        fixed = 0
        for s in candidates:
            try:
                if not s.photo_url or not s.image_url:
                    continue
                if str(s.photo_url) != str(s.image_url):
                    continue

                # If photo_filename contains a URL, restore from it.
                if getattr(s, "photo_filename", None) and str(s.photo_filename).startswith("http"):
                    s.photo_url = str(s.photo_filename)
                    fixed += 1
                    continue

                # Otherwise, clear photo_url so rendering can fall back to local filename/placeholder.
                s.photo_url = None
                fixed += 1
            except Exception:
                continue

        if fixed:
            db.session.commit()
            logger.info(f"Repaired {fixed} student records with photo_url == image_url")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error repairing student photo_url recursion: {e}")






def resolve_student_card_preview_urls(student):
    """Return front/back preview URLs for a generated student card."""
    preview_url = None
    back_preview_url = None

    if getattr(student, "image_url", None):
        preview_url = student.image_url
    elif getattr(student, "generated_filename", None):
        preview_filename = str(student.generated_filename)
        if preview_filename.lower().endswith(".pdf"):
            preview_filename = preview_filename[:-4] + ".jpg"
        preview_path = os.path.join(GENERATED_FOLDER, preview_filename)
        if os.path.exists(preview_path):
            preview_url = url_for("static", filename=f"generated/{preview_filename}")

    if getattr(student, "back_image_url", None):
        back_preview_url = student.back_image_url
    elif getattr(student, "back_generated_filename", None):
        back_preview_path = os.path.join(GENERATED_FOLDER, str(student.back_generated_filename))
        if os.path.exists(back_preview_path):
            back_preview_url = url_for("static", filename=f"generated/{student.back_generated_filename}")

    return preview_url, back_preview_url

def load_static_back_template_image(template_obj, card_width, card_height):
    """Load the back template image for static back-side rendering."""
    if not template_obj or not getattr(template_obj, "is_double_sided", False):
        return None

    back_template_path = get_template_path(template_obj.id, side="back")
    if not back_template_path:
        return None

    try:
        return load_template_smart(back_template_path).resize((card_width, card_height))
    except Exception as e:
        logger.warning(f"Failed to load back template for template {template_obj.id}: {e}")
        return None


def add_template(
    filename,
    school_name,
    card_orientation='landscape',
    language='english',
    text_direction='ltr',
    *,
    is_double_sided=False,
    back_filename=None,
    back_template_url=None,
    back_language=None,
    back_text_direction=None,
):
    try:
        # --- Default Dimensions based on Orientation ---
        # CR80 Defaults: 1015x661 (Landscape) or 661x1015 (Portrait)
        if card_orientation == 'portrait':
            width, height = 661, 1015
            rows, cols = 2, 5 # 2 Rows, 5 Cols on A4 Landscape
        else:
            width, height = 1015, 661
            rows, cols = 5, 2 # 5 Rows, 2 Cols on A4 Portrait

        template = Template(
            filename=filename,
            back_filename=back_filename,
            back_template_url=back_template_url,
            school_name=school_name,
            font_settings=get_default_font_config(),
            photo_settings=get_default_photo_config(),
            qr_settings=get_default_qr_config(),
            back_font_settings=get_default_font_config(),
            back_photo_settings=get_default_photo_config(),
            back_qr_settings=get_default_qr_config(),
            card_orientation=card_orientation,
            language=language,              # <--- NEW
            text_direction=text_direction,  # <--- NEW
            back_language=(back_language or language),
            back_text_direction=(back_text_direction or text_direction),
            is_double_sided=bool(is_double_sided),
            
            # --- NEW: Save Dimensions & Grid ---
            card_width=width,
            card_height=height,
            sheet_width=2480,  # Default A4 @ 300 DPI
            sheet_height=3508, # Default A4 @ 300 DPI
            grid_rows=rows,
            grid_cols=cols,
            # -----------------------------------
            
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(template)
        db.session.commit()
        
        logger.info(f"Added template: {filename} ({width}x{height})")
        return template.id
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding template: {e}")
        raise


def add_template_cloudinary(
    template_url,
    school_name,
    card_orientation='landscape',
    language='english',
    text_direction='ltr',
    filename=None,
    *,
    is_double_sided=False,
    back_filename=None,
    back_template_url=None,
    back_language=None,
    back_text_direction=None,
):
    """
    Add a template with Cloudinary URL (no local file storage).
    
    Args:
        template_url (str): Cloudinary secure URL for the template
        school_name (str): School name
        card_orientation (str): 'landscape' or 'portrait'
        language (str): Language for labels
        text_direction (str): 'ltr' or 'rtl'
        filename (str | None): Optional local backup path under static/
    
    Returns:
        int: Template ID
    """
    try:
        # --- Default Dimensions based on Orientation ---
        if card_orientation == 'portrait':
            width, height = 661, 1015
            rows, cols = 2, 5
        else:
            width, height = 1015, 661
            rows, cols = 5, 2

        template = Template(
            filename=filename,
            template_url=template_url,
            back_filename=back_filename,
            back_template_url=back_template_url,
            school_name=school_name,
            font_settings=get_default_font_config(),
            photo_settings=get_default_photo_config(),
            qr_settings=get_default_qr_config(),
            back_font_settings=get_default_font_config(),
            back_photo_settings=get_default_photo_config(),
            back_qr_settings=get_default_qr_config(),
            card_orientation=card_orientation,
            language=language,
            text_direction=text_direction,
            back_language=(back_language or language),
            back_text_direction=(back_text_direction or text_direction),
            is_double_sided=bool(is_double_sided),
            card_width=width,
            card_height=height,
            sheet_width=2480,
            sheet_height=3508,
            grid_rows=rows,
            grid_cols=cols,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(template)
        db.session.commit()
        
        safe_url = str(template_url or "Local Fallback")
        logger.info(f"Added Cloudinary template: {safe_url[:50]}... ({width}x{height})")
        return template.id
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding Cloudinary template: {e}")
        raise

# ================== Template Config ==================
def get_templates():
    try:
        query = db.session.query(Template).order_by(Template.created_at.desc())
        
        # RBAC Filtering: School admins only see their assigned school
        if session.get("admin") and session.get("admin_role") == "school_admin":
            if session.get("admin_school"):
                query = query.filter_by(school_name=session.get("admin_school"))
                
        templates = query.all()
        result = []

        # Pre-compute default settings once (not per-template)
        default_font = get_default_font_config()
        default_photo = get_default_photo_config()
        default_qr = get_default_qr_config()

        for template in templates:
            font_settings = default_font.copy()
            photo_settings = default_photo.copy()
            qr_settings = default_qr.copy()
            source_path = template.filename or template.template_url or ""
            source_basename = os.path.basename(source_path.split("?", 1)[0]) if source_path else ""
            if source_basename and len(source_basename) > 90:
                source_basename = source_basename[:87] + "..."
            back_source_path = template.back_filename or template.back_template_url or ""
            back_source_basename = os.path.basename(back_source_path.split("?", 1)[0]) if back_source_path else ""
            if back_source_basename and len(back_source_basename) > 90:
                back_source_basename = back_source_basename[:87] + "..."

            # Merge with template settings
            if template.font_settings:
                loaded_font = template.font_settings.copy()
                # Handle legacy font_color field
                if 'font_color' in loaded_font and 'label_font_color' not in loaded_font:
                    loaded_font['label_font_color'] = loaded_font['font_color']
                    loaded_font['value_font_color'] = loaded_font['font_color']
                font_settings = {**font_settings, **loaded_font}

            if template.photo_settings:
                photo_settings = {**photo_settings, **template.photo_settings}

            if template.qr_settings:
                qr_settings = {**qr_settings, **template.qr_settings}
            back_font_settings = {**default_font, **(template.back_font_settings or {})}
            back_photo_settings = {**default_photo, **(template.back_photo_settings or {})}
            back_qr_settings = {**default_qr, **(template.back_qr_settings or {})}
            
            # === MISSING PART ADDED HERE ===
            # Serialize fields for frontend
            template_fields = []
            if template.fields:
                for field in template.fields:
                    template_fields.append({
                        'field_name': field.field_name,
                        'field_label': field.field_label,
                        'field_type': field.field_type,
                        'is_required': field.is_required,
                        'show_label_front': bool(getattr(field, 'show_label_front', True)),
                        'show_value_front': bool(getattr(field, 'show_value_front', True)),
                        'show_label_back': bool(getattr(field, 'show_label_back', False)),
                        'show_value_back': bool(getattr(field, 'show_value_back', False)),
                        'display_order': field.display_order,
                        'field_options': field.field_options
                    })
                      # Sort by display order so they appear correctly on the form
                template_fields.sort(key=lambda x: int(x.get('display_order') or 0))
            # ===============================

            result.append({
                'id': template.id,
                'filename': template.filename,
                'template_url': template.template_url,
                'back_filename': template.back_filename,
                'back_template_url': template.back_template_url,
                'source_path': source_path,
                'source_name': source_basename or "No source",
                'back_source_path': back_source_path,
                'back_source_name': back_source_basename or "No back source",
                'school_name': template.school_name,
                'created_at': template.created_at.isoformat() if template.created_at else datetime.now(timezone.utc).isoformat(),
                'font_settings': font_settings,
                'photo_settings': photo_settings,
                'qr_settings': qr_settings,
                'back_font_settings': back_font_settings,
                'back_photo_settings': back_photo_settings,
                'back_qr_settings': back_qr_settings,
                'card_orientation': template.card_orientation or 'landscape',
                'language': template.language or 'english',
                'text_direction': template.text_direction or 'ltr',
                'back_language': template.back_language or template.language or 'english',
                'back_text_direction': template.back_text_direction or template.text_direction or 'ltr',
                'back_layout_config': template.back_layout_config,
                'is_double_sided': bool(template.is_double_sided),
                'duplex_flip_mode': template.duplex_flip_mode or 'long_edge',
                'deadline': template.deadline.isoformat() if template.deadline else None,
                'fields': template_fields,  # Now this variable exists
                 # --- NEW DIMENSIONS ---
                'card_width': template.card_width or 1015,
                'card_height': template.card_height or 661,
                'sheet_width': template.sheet_width or 2480,
                'sheet_height': template.sheet_height or 3508,
                'grid_rows': template.grid_rows or 5,
                'grid_cols': template.grid_cols or 2
            })
        
        return result
    except Exception as e:
        logger.error(f"Error fetching templates: {e}")
        return []  # Always return empty list on error


# Extracted to app/services/student_service.py — backward-compat re-export
from app.services.student_service import (  # noqa: E402
    _normalize_school_name,
    _find_template_dict_by_school,
    _student_session_school_name,
    _is_admin_session,
    _current_session_email,
    _student_school_access_allowed,
)


# Extracted to app/services/template_upload_service.py — backward-compat re-export
from app.services.template_upload_service import (  # noqa: E402
    store_template_upload_asset,
    _extract_pdf_upload_payload,
    _single_pdf_page_bytes,
    _template_side_filename,
    store_template_upload_bytes,
)




def update_template_settings(template_id, font_settings=None, photo_settings=None, qr_settings=None, 
                             card_orientation=None, card_dims=None, sheet_dims=None, grid_layout=None,
                             back_font_settings=None, back_photo_settings=None, back_qr_settings=None,
                             is_double_sided=None, duplex_flip_mode=None):
    try:
        template = db.session.get(Template, template_id)

        if not template:
            logger.error(f"Template {template_id} not found")
            return
        
        if font_settings is not None:
            default_font = get_default_font_config()
            complete_font_settings = {**default_font, **font_settings}
            template.font_settings = complete_font_settings
        
        if photo_settings is not None:
            default_photo = get_default_photo_config()
            complete_photo_settings = {**default_photo, **photo_settings}
            template.photo_settings = complete_photo_settings
        
        if qr_settings is not None:
            default_qr = get_default_qr_config()
            complete_qr_settings = {**default_qr, **qr_settings}
            template.qr_settings = complete_qr_settings
        if back_font_settings is not None:
            default_font = get_default_font_config()
            template.back_font_settings = {**default_font, **back_font_settings}
        if back_photo_settings is not None:
            default_photo = get_default_photo_config()
            template.back_photo_settings = {**default_photo, **back_photo_settings}
        if back_qr_settings is not None:
            default_qr = get_default_qr_config()
            template.back_qr_settings = {**default_qr, **back_qr_settings}
        
        if card_orientation is not None:
            template.card_orientation = card_orientation

        # --- UPDATE DIMENSIONS ---
        if card_dims:
            # Expecting dict like {'width': 1015, 'height': 661}
            template.card_width = card_dims.get('width', 1015)
            template.card_height = card_dims.get('height', 661)
            
        if sheet_dims:
            # Expecting dict like {'width': 2480, 'height': 3508}
            template.sheet_width = sheet_dims.get('width', 2480)
            template.sheet_height = sheet_dims.get('height', 3508)

        # --- NEW: UPDATE GRID LAYOUT ---
        if grid_layout:
            # Expecting dict like {'rows': 5, 'cols': 2}
            template.grid_rows = grid_layout.get('rows', 5)
            template.grid_cols = grid_layout.get('cols', 2)
        if is_double_sided is not None:
            template.is_double_sided = bool(is_double_sided)
        if duplex_flip_mode:
            template.duplex_flip_mode = duplex_flip_mode
        # -------------------------------
        
        db.session.commit()
        try:
            actor, actor_role = get_session_actor()
            create_template_version_snapshot(template, source="update_template_settings", actor=actor, actor_role=actor_role)
            log_immutable_audit_event(
                entity_type="template",
                entity_id=template.id,
                action="template_settings_updated",
                payload={"template_id": template.id, "card_orientation": template.card_orientation},
                actor=actor,
                actor_role=actor_role,
            )
            db.session.commit()
        except Exception as lifecycle_exc:
            db.session.rollback()
            logger.warning("Template lifecycle hooks failed for template %s: %s", template_id, lifecycle_exc)
        log_activity("Updated Template Settings", target=f"Template {template_id}", 
                     details=f"Orientation: {card_orientation}")
        
        logger.info(f"Updated settings for template ID {template_id}, orientation: {card_orientation}")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating template settings: {e}")
        raise


def clear_layout_field_overrides(template, side="front"):
    """
    Let Template Settings control label/value text after Visual Editor was used,
    without deleting custom visual objects such as logos, shapes, lines, or text blocks.
    """
    if not template:
        return False

    side_name = "back" if str(side or "front").strip().lower() == "back" else "front"
    attr = "back_layout_config" if side_name == "back" else "layout_config"
    current = parse_layout_config(getattr(template, attr, None))
    if not current or "fields" not in current:
        return False
    parsed = keep_layout_field_visibility_only(current)
    setattr(template, attr, json.dumps(parsed, ensure_ascii=False) if parsed else None)
    return True


def keep_layout_field_visibility_only(layout_config_raw):
    """Strip field position/style overrides while preserving explicit show/hide choices."""
    parsed = parse_layout_config(layout_config_raw)
    if not parsed:
        return {}

    fields = parsed.get("fields")
    if not isinstance(fields, dict):
        parsed.pop("fields", None)
        return parsed

    visibility_fields = {}
    for field_key, field_obj in fields.items():
        if not isinstance(field_obj, dict):
            continue
        next_field = {}
        for part in ("label", "value", "colon"):
            part_obj = field_obj.get(part)
            visible_key = f"{part}_visible"
            if isinstance(part_obj, dict) and "visible" in part_obj:
                next_field[part] = {"visible": bool(part_obj.get("visible"))}
            elif visible_key in field_obj:
                next_field[part] = {"visible": bool(field_obj.get(visible_key))}
        if next_field:
            visibility_fields[str(field_key)] = next_field

    if visibility_fields:
        parsed["fields"] = visibility_fields
    else:
        parsed.pop("fields", None)
    return parsed
    
# ================== Duplicate Config ==================
def load_duplicate_config():
    default_config = {"check_phone": False, "check_name_class": True}
    if os.path.exists(DUPLICATE_CONFIG_PATH):
        try:
            with open(DUPLICATE_CONFIG_PATH, "r") as f:
                return {**default_config, **json.load(f)}
        except json.JSONDecodeError as e:
            logger.error(f"Error loading duplicate config: {e}")
    return default_config

def save_duplicate_config(config):
    try:
        with open(DUPLICATE_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
        logger.info("Duplicate config saved successfully")
    except Exception as e:
        logger.error(f"Error saving duplicate config: {e}")

def check_duplicate_student(form_data, photo_filename=None, student_id=None):
    duplicate_config = load_duplicate_config()
    # Get the template ID from the form data
    current_template_id = form_data.get('template_id')
    
    # If for some reason template_id is missing, we cannot scope it, so return False or handle error
    if not current_template_id:
        # Fallback: if no template ID provided, skip check or check globally (optional)
        return False, None 

    try:
        # Check phone duplicates (Scoped to Template)
        if duplicate_config.get("check_phone", False):
            query = Student.query.filter(
                Student.phone == form_data['phone'],
                Student.template_id == current_template_id  # <--- SCOPE TO SCHOOL
            )
            if student_id:
                query = query.filter(Student.id != student_id)
            if query.first():
                return True, "A student with this phone number already exists in this school."
        
        # Check name and class duplicates (Scoped to Template)
        if duplicate_config.get("check_name_class", True):
            query = Student.query.filter(
                Student.name == form_data['name'],
                Student.class_name == form_data['class_name'],
                Student.template_id == current_template_id  # <--- SCOPE TO SCHOOL
            )
            if student_id:
                query = query.filter(Student.id != student_id)
            if query.first():
                return True, "A student with this name and class combination already exists in this school."
        
        return False, None
    except Exception as e:
        logger.error(f"Error checking duplicates: {e}")
        return True, f"Database error: {str(e)}"
    


# Lazy mediapipe import — face_service.py handles lazy loading now
mp_face = None

_detector_lock = threading.Lock()

# Extracted to app/services/face_service.py — backward-compat re-export
from app.services.face_service import _get_face_detector  # noqa: E402




# Extracted to app/services/face_service.py — backward-compat re-export
from app.services.face_service import (  # noqa: E402
    _fallback_center_crop,
    _crop_with_padding,
    _center_crop_box,
    _detect_face_crop_box,
)



# Extracted to app/services/file_service.py — backward-compat re-export
from app.services.file_service import _read_uploaded_file_bytes  # noqa: E402








# Extracted to app/services/file_service.py — backward-compat re-export
from app.services.file_service import _write_binary_file_atomic  # noqa: E402




# Extracted to app/services/email_service.py — backward-compat re-export
from app.services.email_service import send_email  # noqa: E402
# ================== Landing Page Routes ==================

# ================== Auth ==================

# Search for the existing @app.route("/reset_password"...) and replace it with this:

# -------------------------------------------------------------------------
# 1. REQUEST RESET LINK (User enters email here)
# -------------------------------------------------------------------------

# -------------------------------------------------------------------------
# 2. SET NEW PASSWORD (User clicks link -> Enters new password)
# -------------------------------------------------------------------------

@app.before_request
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
        return redirect(url_for("auth.login" if "auth.login" in app.view_functions else "login"))

# ================== Student Routes ==================
@student_bp.route("/update_email", methods=["POST"])
def update_email():
    if 'student_email' not in session:
        flash('Please log in to update your email.', 'error')
        return redirect(url_for('auth.student_login'))
  
    email = request.form.get('email')
    if not email or not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
        flash('Invalid email address.', 'error')
        return redirect(url_for('dashboard.index'))
  
    try:
        student = Student.query.filter_by(email=session['student_email']).first()
        if not student:
            flash('No matching student record found.', 'error')
            return redirect(url_for('dashboard.index'))
        
        # Check if email already in use by another student
        existing = Student.query.filter(
            Student.email == email,
            Student.id != student.id
        ).first()
        
        if existing:
            flash('Email already in use.', 'error')
            return redirect(url_for('dashboard.index'))
        
        student.email = email
        session['student_email'] = email
        db.session.commit()
        
        flash('Email updated successfully.', 'success')
        
    except Exception as e:
        flash(f'Error updating email: {e}', 'error')
        logger.error(f"Error updating email: {e}")
        return redirect(url_for('dashboard.index'))
  
    return redirect(url_for('dashboard.index'))

@student_bp.route("/update_password", methods=["POST"])
def update_password():
    if 'student_email' not in session:
        flash('Please log in to update your password.', 'error')
        return redirect(url_for('auth.student_login'))
  
    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')
    if not all([current_password, new_password, confirm_password]):
        flash('All password fields are required.', 'error')
        return redirect(url_for('dashboard.index'))
  
    if new_password != confirm_password:
        flash('New password and confirmation do not match.', 'error')
        return redirect(url_for('dashboard.index'))
  
    if len(new_password) < 6:
        flash('New password must be at least 6 characters.', 'error')
        return redirect(url_for('dashboard.index'))
    try:
        student = Student.query.filter_by(email=session['student_email']).first()
        
        if not student or not student.password:
            flash('Current password is incorrect.', 'error')
            return redirect(url_for('dashboard.index'))
        
        try:
            if not check_password_hash(student.password, current_password):
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('dashboard.index'))
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            flash('Authentication error. Please try again.', 'error')
            return redirect(url_for('dashboard.index'))
        
        hashed_password = generate_password_hash(new_password)
        student.password = hashed_password
        db.session.commit()
        
        flash('Password updated successfully.', 'success')
    except Exception as e:
        flash(f'Error updating password: {e}', 'error')
        logger.error(f"Error updating password: {e}")
        return redirect(url_for('dashboard.index'))
  
    return redirect(url_for('dashboard.index'))


@student_bp.route("/update_photo", methods=["POST"])
def student_update_photo():
    """Allow student to update their own photo via webcam or upload."""
    if 'student_email' not in session:
        return redirect(url_for("auth.student_login"))

    from app.services.photo_service import auto_crop_face_photo, _process_photo_pil
    from app.services.cache_service import invalidate_student_caches
    from werkzeug.utils import secure_filename
    import io

    photo_file = request.files.get('photo')
    if not photo_file or not photo_file.filename:
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "error": "No photo provided"}), 400
        flash("No photo provided.", "error")
        return redirect(url_for("dashboard.student_portal"))

    try:
        student = Student.query.filter_by(email=session["student_email"]).first()
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404

        # Read and process photo
        raw_bytes = photo_file.read()
        if not raw_bytes:
            return jsonify({"success": False, "error": "Empty photo file"}), 400

        # Process photo with face detection and cropping
        processed = _process_photo_pil(
            Image.open(io.BytesIO(raw_bytes)),
            target_width=260, target_height=313,
            auto_crop_fn=auto_crop_face_photo
        )

        # Save to Cloudinary or local
        from app.legacy_app import UPLOAD_FOLDER, STORAGE_BACKEND
        import os, uuid
        filename = f"student_{student.id}_{uuid.uuid4().hex[:8]}.jpg"

        if STORAGE_BACKEND == "local":
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            processed.save(filepath, "JPEG", quality=90)
            student.photo_filename = filename
            student.photo_url = None
        else:
            from cloudinary_config import upload_image
            buf = io.BytesIO()
            processed.save(buf, format="JPEG", quality=90)
            buf.seek(0)
            result = upload_image(buf.getvalue(), folder="student_photos")
            student.photo_url = result if isinstance(result, str) else result.get("url", "")
            student.photo_filename = filename

        db.session.commit()
        invalidate_student_caches(student.id)

        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True, "photo_url": student.photo_url})
        flash("Photo updated successfully! Your new ID card will be generated.", "success")
        return redirect(url_for("dashboard.student_portal"))
    except Exception as e:
        logger.error(f"Error updating student photo: {e}")
        db.session.rollback()
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"Error updating photo: {e}", "error")
        return redirect(url_for("dashboard.student_portal"))


@student_bp.route("/request_reprint", methods=["POST"])
def student_request_reprint():
    """Allow student to request a reprint of their ID card."""
    if 'student_email' not in session:
        return redirect(url_for("auth.student_login"))

    try:
        student = Student.query.filter_by(email=session["student_email"]).first()
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404

        # Add to print queue with high priority
        from app.services.print_queue_service import add_print_job
        job_id = add_print_job(
            template_id=student.template_id,
            student_id=student.id,
            job_type='reprint',
            priority=2,  # High priority for reprint requests
        )

        # Log the request
        from app.legacy_app import log_activity
        log_activity("Reprint requested", target=f"Student {student.name} (ID: {student.id})",
                     details=f"Print job {job_id} created")

        # Send notification
        try:
            from app.services.notification_service import notify_card_ready
            notify_card_ready(student.id, student.template_id, reprint=True)
        except Exception:
            pass

        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True, "message": "Reprint requested", "job_id": job_id})
        flash("Reprint requested! You will be notified when your new card is ready.", "success")
        return redirect(url_for("dashboard.student_portal"))
    except Exception as e:
        logger.error(f"Error requesting reprint: {e}")
        db.session.rollback()
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"Error requesting reprint: {e}", "error")
        return redirect(url_for("dashboard.student_portal"))

# ================== Student Credential Management Routes ==================
# ================== UPDATED INDEX ROUTE (Admin Email Bypass) ==================

# ================== MAIN INDEX ROUTE ==================
  

# ================== Compile School Sheets Route ==================

@app.route("/upload_template", methods=["POST"])
def upload_template():
    front_file = request.files.get('front_template') or request.files.get('template')
    back_file = request.files.get('back_template')
    if front_file is None and request.files:
        for candidate in request.files.values():
            if getattr(candidate, "filename", ""):
                front_file = candidate
                break
    school_name = (request.form.get('school_name') or '').strip()
    if front_file is None or not school_name:
        logger.error("Front template file and school name are required")
        return redirect(url_for('dashboard.admin', error="Front template file and school name are required"))
        
    # Enforce RBAC for School Admins (can only upload to their assigned school)
    if session.get("admin_role") == "school_admin" and school_name != session.get("admin_school"):
        return redirect(url_for('dashboard.admin', error="You can only upload templates for your assigned school."))
    
    file = front_file
    card_orientation = request.form.get('card_orientation', 'landscape')
    template_mode = (request.form.get('template_mode', 'single') or 'single').strip().lower()
    is_double_sided = template_mode == 'double'
    
    if file.filename == '' or not school_name:
        logger.error("No file selected or school name empty")
        return redirect(url_for('dashboard.admin', error="No file selected or school name empty"))
    
    # Capture side-aware language inputs
    language = (request.form.get('language', 'english') or 'english').strip().lower()
    back_language = (request.form.get('back_language') or language).strip().lower()
    text_direction = (request.form.get('text_direction') or default_text_direction_for_language(language)).strip().lower()
    back_text_direction = (request.form.get('back_text_direction') or default_text_direction_for_language(back_language)).strip().lower()

    if language not in SUPPORTED_TEMPLATE_LANGUAGES:
        return redirect(url_for('dashboard.admin', error="Invalid front language selected"))
    if back_language not in SUPPORTED_TEMPLATE_LANGUAGES:
        return redirect(url_for('dashboard.admin', error="Invalid back language selected"))
    if text_direction not in {"ltr", "rtl"}:
        text_direction = default_text_direction_for_language(language)
    if back_text_direction not in {"ltr", "rtl"}:
        back_text_direction = default_text_direction_for_language(back_language)
    if is_double_sided and not validate_double_sided_language_pair(language, back_language):
        return redirect(url_for('dashboard.admin',
            error="Invalid language selection for double-sided template.",
        ))

    if file and file.filename.lower().endswith(('.pdf', '.jpg', '.jpeg', '.png')):
        try:
            back_file_present = bool(back_file is not None and back_file.filename)
            if is_double_sided and not back_file_present:
                if not file.filename.lower().endswith('.pdf'):
                    raise ValueError("Back template file is required unless the front file is a 2-page PDF.")

                front_filename = secure_filename(file.filename)
                front_buffer = io.BytesIO()
                file.save(front_buffer)
                source_pdf_bytes, page_count = _extract_pdf_upload_payload(front_buffer.getvalue(), "Front")
                if page_count < 2:
                    raise ValueError("Double-sided upload needs a back template file or a front PDF with at least 2 pages.")

                front_asset = store_template_upload_bytes(
                    _single_pdf_page_bytes(source_pdf_bytes, 0),
                    _template_side_filename(front_filename, "front"),
                    side_label="Front",
                )
                back_asset = store_template_upload_bytes(
                    _single_pdf_page_bytes(source_pdf_bytes, 1),
                    _template_side_filename(front_filename, "back"),
                    side_label="Back",
                )
            else:
                front_asset = store_template_upload_asset(file, side_label="Front")
                back_asset = store_template_upload_asset(back_file, side_label="Back") if is_double_sided else None

            if STORAGE_BACKEND == "local":
                add_template(
                    front_asset["filename"],
                    school_name,
                    card_orientation,
                    language,
                    text_direction,
                    is_double_sided=is_double_sided,
                    back_filename=(back_asset or {}).get("filename"),
                    back_language=back_language,
                    back_text_direction=back_text_direction,
                )
                logger.info(
                    "Template saved locally: front=%s back=%s for school=%s (%s, %s)",
                    front_asset["filename"],
                    (back_asset or {}).get("filename"),
                    school_name,
                    card_orientation,
                    template_mode,
                )
            else:
                add_template_cloudinary(
                    front_asset["template_url"],
                    school_name,
                    card_orientation,
                    language,
                    text_direction,
                    filename=front_asset["filename"],
                    is_double_sided=is_double_sided,
                    back_filename=(back_asset or {}).get("filename"),
                    back_template_url=(back_asset or {}).get("template_url"),
                    back_language=back_language,
                    back_text_direction=back_text_direction,
                )
                logger.info(
                    "Template uploaded to Cloudinary: school=%s orientation=%s mode=%s",
                    school_name,
                    card_orientation,
                    template_mode,
                )
            return redirect(url_for('dashboard.admin', success="Template uploaded successfully"))
        except Exception as e:
            logger.error(f"Error uploading template: {e}")
            return redirect(url_for('dashboard.admin', error=f"Error uploading template: {str(e)}"))
    else:
        logger.error("Invalid file format for template")
        return redirect(url_for('dashboard.admin', error="Invalid file format. Use PDF, JPG, or PNG"))

@app.route("/update_template_settings", methods=["POST"])
def update_template_settings_route():
    try:
        # Check if request is JSON or form data
        if request.is_json:
            data = request.get_json()
            template_id = int(data.get("template_id"))
        else:
            # Fall back to form data for backward compatibility
            template_id = int(request.form.get("template_id"))
            data = request.form
        
        # Retrieve current template from the database
        template = db.session.get(Template, template_id)

        if not template:
            if request.is_json:
                return jsonify({'success': False, 'error': 'Template not found'}), 404
            return redirect(url_for('dashboard.admin', error="Template not found"))
        
        current_orientation = template.card_orientation if template.card_orientation else 'landscape'
        settings_side = "front"
        if request.is_json:
            settings_side = (data.get("settings_side") or "front").strip().lower()
        else:
            settings_side = (request.form.get("settings_side") or "front").strip().lower()
        if settings_side not in {"front", "back"}:
            settings_side = "front"
        
        # Get card_orientation
        if request.is_json:
            card_orientation = data.get("card_orientation", current_orientation)
        else:
            card_orientation = request.form.get("card_orientation", current_orientation)
        
        # --- Handle Deadline ---
        deadline_str = None
        if request.is_json:
            deadline_str = data.get("deadline")
        else:
            deadline_str = request.form.get("deadline")
            
        if deadline_str:
            try:
                # This format matches the HTML <input type="datetime-local">
                template.deadline = datetime.strptime(deadline_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                pass 
        else:
            # If the field was present but empty, clear the deadline
            if request.is_json and "deadline" in data:
                template.deadline = None
            elif "deadline" in request.form:
                template.deadline = None

        # --- Save language + text direction ---
        # Important: when updating other template settings, some clients may send defaults
        # ("english"/"ltr") even when the template was previously configured for Urdu/Arabic RTL.
        # To prevent accidental resets, we only allow a default reset when the client explicitly opts in.
        allowed_languages = {"english", "urdu", "hindi", "arabic"}
        allowed_directions = {"ltr", "rtl"}

        current_language = (
            template.back_language if settings_side == "back" else template.language
        ) or "english"
        current_text_direction = (
            template.back_text_direction if settings_side == "back" else template.text_direction
        ) or "ltr"
        current_language = current_language.strip().lower()
        current_text_direction = current_text_direction.strip().lower()

        if request.is_json:
            incoming_language = (data.get("language") or "").strip().lower()
            incoming_text_direction = (data.get("text_direction") or "").strip().lower()
            language_direction_explicit = bool(data.get("language_direction_explicit"))
        else:
            incoming_language = (request.form.get("language") or "").strip().lower()
            incoming_text_direction = (request.form.get("text_direction") or "").strip().lower()
            explicit_raw = (request.form.get("language_direction_explicit") or "").strip().lower()
            language_direction_explicit = explicit_raw in {"1", "true", "yes", "on"}

        if incoming_language and incoming_language not in allowed_languages:
            incoming_language = ""
        if incoming_text_direction and incoming_text_direction not in allowed_directions:
            incoming_text_direction = ""

        # Localization Pack Manager: side-level language lock enforcement
        # Example: {"front":"english","back":"hindi"}
        lock_rules = getattr(template, "language_lock_rules", None) or {}
        side_lock_lang = ""
        if isinstance(lock_rules, dict):
            side_lock_lang = str(lock_rules.get(settings_side, "") or "").strip().lower()
        if side_lock_lang in allowed_languages:
            incoming_language = side_lock_lang
            incoming_text_direction = default_text_direction_for_language(side_lock_lang)

        # If the client did not explicitly confirm language/direction changes, don't allow
        # "reset to defaults" to overwrite an already-configured template.
        if not language_direction_explicit:
            if (
                incoming_language == "english"
                and incoming_text_direction == "ltr"
                and (current_language != "english" or current_text_direction != "ltr")
            ):
                incoming_language = ""
                incoming_text_direction = ""

        if incoming_language:
            if settings_side == "back":
                template.back_language = incoming_language
            else:
                template.language = incoming_language
        if incoming_text_direction:
            if settings_side == "back":
                template.back_text_direction = incoming_text_direction
            else:
                template.text_direction = incoming_text_direction

        if bool(getattr(template, "is_double_sided", False)):
            effective_front_language = (template.language or "english").strip().lower()
            effective_back_language = (template.back_language or effective_front_language).strip().lower()
            if not validate_double_sided_language_pair(effective_front_language, effective_back_language):
                error_message = "Invalid language selection for double-sided template."
                if request.is_json:
                    return jsonify({'success': False, 'error': error_message}), 400
                return redirect(url_for('dashboard.admin', error=error_message))

        parsed_layout_for_font_sync = None

        # Optional advanced visual-editor layout config:
        # stores per-field label/value x,y and visibility flags.
        if request.is_json and isinstance(data, dict) and "layout_config" in data:
            incoming_layout = data.get("layout_config")
            parsed_layout = parse_layout_config(incoming_layout)
            parsed_layout_for_font_sync = parsed_layout
            if settings_side == "back":
                template.back_layout_config = json.dumps(parsed_layout, ensure_ascii=False) if parsed_layout else None
            else:
                template.layout_config = json.dumps(parsed_layout, ensure_ascii=False) if parsed_layout else None

        
        # Helper function to safely get integer values
        def safe_get_int(source, key, default):
            if source is None:
                return default
            value = source.get(key)
            if value is None or value == '':
                return default
            try:
                return int(value)
            except (ValueError, TypeError):
                return default

        def safe_get_bool(source, key, default=False):
            if source is None:
                return default
            value = source.get(key)
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return default
        
        # Helper function to safely get nested value
        def safe_get_nested(source, *keys, default=None):
            if source is None:
                return default
            current = source
            for key in keys:
                if isinstance(current, dict):
                    current = current.get(key)
                else:
                    return default
                if current is None:
                    return default
            return current
        
        # --- SMART DIMENSION HANDLING ---
        # 1. Get raw values
        if request.is_json:
            raw_w = safe_get_int(data, "card_width", template.card_width or 1015)
            raw_h = safe_get_int(data, "card_height", template.card_height or 661)
        else:
            raw_w = get_form_int(request.form, "card_width", 1015)
            raw_h = get_form_int(request.form, "card_height", 661)
        
        # 2. Logic: Auto-swap dimensions if orientation mismatches
        if card_orientation == 'portrait' and raw_w > raw_h:
            final_w, final_h = raw_h, raw_w 
        elif card_orientation == 'landscape' and raw_h > raw_w:
            final_w, final_h = raw_h, raw_w 
        else:
            final_w, final_h = raw_w, raw_h 
        
        card_dims = { 'width': final_w, 'height': final_h }
        
        # Get sheet dimensions
        if request.is_json:
            sheet_dims = {
                'width': safe_get_int(data, "sheet_width", template.sheet_width or 2480),
                'height': safe_get_int(data, "sheet_height", template.sheet_height or 3508)
            }
        else:
            sheet_dims = {
                'width': get_form_int(request.form, "sheet_width", 2480),
                'height': get_form_int(request.form, "sheet_height", 3508)
            }
        
        # --- CAPTURE GRID LAYOUT ---
        if request.is_json:
            grid_layout = {
                'rows': safe_get_int(data, "grid_rows", template.grid_rows or 5),
                'cols': safe_get_int(data, "grid_cols", template.grid_cols or 2)
            }
        else:
            grid_layout = {
                'rows': get_form_int(request.form, "grid_rows", 5),
                'cols': get_form_int(request.form, "grid_cols", 2)
            }
        
        # Define settings defaults based on the NEW orientation
        is_landscape = (card_orientation == 'landscape')
        
        default_label_size = 40 if is_landscape else 32
        default_value_size = 36 if is_landscape else 28
        default_label_x = 50 if is_landscape else 40
        default_value_x = 280 if is_landscape else 200
        default_start_y = 275 if is_landscape else 120
        default_line_height = 50 if is_landscape else 45
        
        # Get font settings
        if request.is_json:
            font_settings_data = data.get("font_settings", {}) or {}
            font_settings = {
                "font_bold": safe_get_nested(font_settings_data, "font_bold", default="arialbd.ttf"),
                "font_regular": safe_get_nested(font_settings_data, "font_regular", default="arial.ttf"),
                "label_font_size": safe_get_int(font_settings_data, "label_font_size", default_label_size),
                "value_font_size": safe_get_int(font_settings_data, "value_font_size", default_value_size),
                # Fix 2/10: persist colon_font_size; fall back to label_font_size
                "colon_font_size": safe_get_int(font_settings_data, "colon_font_size", safe_get_int(font_settings_data, "label_font_size", default_label_size)),
                "label_x": safe_get_int(font_settings_data, "label_x", default_label_x),
                "value_x": safe_get_int(font_settings_data, "value_x", default_value_x),
                "start_y": safe_get_int(font_settings_data, "start_y", default_start_y),
                "line_height": safe_get_int(font_settings_data, "line_height", default_line_height),
                "text_case": safe_get_nested(font_settings_data, "text_case", default="normal"),
                "show_label_colon": safe_get_bool(font_settings_data, "show_label_colon", True),
                "align_label_colon": safe_get_bool(font_settings_data, "align_label_colon", True),
                "label_colon_gap": safe_get_int(font_settings_data, "label_colon_gap", 8),
                "address_max_lines": safe_get_int(font_settings_data, "address_max_lines", 2),
                # Text gradient settings — MUST be saved or gradient is lost on reload
                "enable_label_gradient": safe_get_bool(font_settings_data, "enable_label_gradient", False),
                "enable_value_gradient": safe_get_bool(font_settings_data, "enable_value_gradient", False),
                "enable_colon_gradient": safe_get_bool(font_settings_data, "enable_colon_gradient", False),
            }
            
            # Handle font colors from JSON with validation
            def validate_color(color, default=[0, 0, 0]):
                if not color:
                    return default
                if isinstance(color, list) and len(color) >= 3:
                    try:
                        # Ensure all values are integers between 0-255
                        return [
                            max(0, min(255, int(color[0] if color[0] is not None else 0))),
                            max(0, min(255, int(color[1] if color[1] is not None else 0))),
                            max(0, min(255, int(color[2] if color[2] is not None else 0)))
                        ]
                    except (ValueError, TypeError):
                        return default
                elif isinstance(color, str):
                    # Handle string format
                    color = color.strip()
                    if color.startswith("#"):
                        try:
                            h = color.lstrip('#')
                            return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
                        except Exception:
                            return default
                    elif ',' in color:
                        try:
                            parts = color.split(',')
                            if len(parts) >= 3:
                                return [int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip())]
                        except Exception:
                            return default
                return default
            
            font_settings["label_font_color"] = validate_color(
                safe_get_nested(font_settings_data, "label_font_color", default=[0, 0, 0])
            )
            font_settings["value_font_color"] = validate_color(
                safe_get_nested(font_settings_data, "value_font_color", default=[0, 0, 0])
            )
            font_settings["colon_font_color"] = validate_color(
                safe_get_nested(font_settings_data, "colon_font_color", default=font_settings["label_font_color"])
            )
            # Gradient bottom colors
            font_settings["label_font_color_bottom"] = validate_color(
                safe_get_nested(font_settings_data, "label_font_color_bottom", default=[51, 51, 51])
            )
            font_settings["value_font_color_bottom"] = validate_color(
                safe_get_nested(font_settings_data, "value_font_color_bottom", default=[51, 51, 51])
            )
            font_settings["colon_font_color_bottom"] = validate_color(
                safe_get_nested(font_settings_data, "colon_font_color_bottom", default=[51, 51, 51])
            )
        else:
            font_settings = {
                "font_bold": request.form.get("font_bold", "arialbd.ttf"),
                "font_regular": request.form.get("font_regular", "arial.ttf"),
                "label_font_size": get_form_int(request.form, "label_font_size", default_label_size),
                "value_font_size": get_form_int(request.form, "value_font_size", default_value_size),
                # Fix 2/10: persist colon_font_size from form
                "colon_font_size": get_form_int(request.form, "colon_font_size", get_form_int(request.form, "label_font_size", default_label_size)),
                "label_x": get_form_int(request.form, "label_x", default_label_x),
                "value_x": get_form_int(request.form, "value_x", default_value_x),
                "start_y": get_form_int(request.form, "start_y", default_start_y),
                "line_height": get_form_int(request.form, "line_height", default_line_height),
                "text_case": request.form.get("text_case", "normal"),
                # Unchecked checkboxes are omitted from form posts.
                "show_label_colon": (request.form.get("show_label_colon", "off").strip().lower() in {"1", "true", "yes", "on"}),
                "align_label_colon": (request.form.get("align_label_colon", "off").strip().lower() in {"1", "true", "yes", "on"}),
                "label_colon_gap": get_form_int(request.form, "label_colon_gap", 8),
                "address_max_lines": get_form_int(request.form, "address_max_lines", 2),
                # Text gradient settings for form data
                "enable_label_gradient": (request.form.get("enable_label_gradient", "off").strip().lower() in {"1", "true", "yes", "on"}),
                "enable_value_gradient": (request.form.get("enable_value_gradient", "off").strip().lower() in {"1", "true", "yes", "on"}),
                "enable_colon_gradient": (request.form.get("enable_colon_gradient", "off").strip().lower() in {"1", "true", "yes", "on"}),
            }
            
            # Handle font colors from form data
            label_font_color = request.form.get("label_font_color", "0,0,0").strip()
            value_font_color = request.form.get("value_font_color", "0,0,0").strip()
            colon_font_color = request.form.get("colon_font_color", label_font_color).strip()
            label_font_color_bottom = request.form.get("label_font_color_bottom", "51,51,51").strip()
            value_font_color_bottom = request.form.get("value_font_color_bottom", "51,51,51").strip()
            colon_font_color_bottom = request.form.get("colon_font_color_bottom", "51,51,51").strip()
            
            # Robust Color Parser
            def parse_color(c):
                # If it's already a list (e.g. from JSON), validate and return it
                if isinstance(c, list):
                    if len(c) >= 3:
                        try:
                            return [int(x) for x in c[:3]]
                        except Exception:
                            pass
                    return [0, 0, 0]
                    
                # If it's a string, parse it
                if not c: return [0, 0, 0]
                
                if isinstance(c, str):
                    c = c.strip()
                    # Handle Hex
                    if c.startswith("#") and len(c) == 7:
                        try:
                            h = c.lstrip('#')
                            return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
                        except Exception:
                            return [0, 0, 0]
                    # Handle "r,g,b" string
                    try:
                        parts = c.split(',')
                        if len(parts) >= 3:
                            return [int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip())]
                    except Exception:
                        pass
                
                return [0, 0, 0]
            
            font_settings["label_font_color"] = parse_color(label_font_color)
            font_settings["value_font_color"] = parse_color(value_font_color)
            font_settings["colon_font_color"] = parse_color(colon_font_color)
            font_settings["label_font_color_bottom"] = parse_color(label_font_color_bottom)
            font_settings["value_font_color_bottom"] = parse_color(value_font_color_bottom)
            font_settings["colon_font_color_bottom"] = parse_color(colon_font_color_bottom)
        
        # Define photo settings defaults
        default_photo_x = 725 if is_landscape else 100
        default_photo_y = 200 if is_landscape else 400
        default_photo_width = 260 if is_landscape else 216
        default_photo_height = 313 if is_landscape else 180
        
        # Get photo settings
        if request.is_json:
            photo_settings_data = data.get("photo_settings", {}) or {}
            photo_settings = {
                "enable_photo": bool(photo_settings_data.get("enable_photo", True)),
                "photo_x": safe_get_int(photo_settings_data, "photo_x", default_photo_x),
                "photo_y": safe_get_int(photo_settings_data, "photo_y", default_photo_y),
                "photo_width": safe_get_int(photo_settings_data, "photo_width", default_photo_width),
                "photo_height": safe_get_int(photo_settings_data, "photo_height", default_photo_height),
                "photo_border_top_left": safe_get_int(photo_settings_data, "photo_border_top_left", 0),
                "photo_border_top_right": safe_get_int(photo_settings_data, "photo_border_top_right", 0),
                "photo_border_bottom_right": safe_get_int(photo_settings_data, "photo_border_bottom_right", 0),
                "photo_border_bottom_left": safe_get_int(photo_settings_data, "photo_border_bottom_left", 0),
                "photo_frame_color": str(photo_settings_data.get("photo_frame_color") or "#8c2440").strip() or "#8c2440",
                "photo_shape": normalize_photo_shape(photo_settings_data.get("photo_shape") or "rectangle"),
                "photo_shape_inset": safe_get_int(photo_settings_data, "photo_shape_inset", 0),
                "corel_editable_photo_mode": str(
                    photo_settings_data.get("corel_editable_photo_mode", "frame_only")
                ).strip().lower() or "frame_only",
            }
        else:
            # Form data path (main admin panel)
            photo_settings = {
                "enable_photo": ((request.form.get("enable_photo", "on") or "on").strip().lower() in {"1", "true", "yes", "on"}),
                "photo_x": get_form_int(request.form, "photo_x", default_photo_x),
                "photo_y": get_form_int(request.form, "photo_y", default_photo_y),
                "photo_width": get_form_int(request.form, "photo_width", default_photo_width),
                "photo_height": get_form_int(request.form, "photo_height", default_photo_height),
                "photo_border_top_left": get_form_int(request.form, "photo_border_top_left", 0),
                "photo_border_top_right": get_form_int(request.form, "photo_border_top_right", 0),
                "photo_border_bottom_right": get_form_int(request.form, "photo_border_bottom_right", 0),
                "photo_border_bottom_left": get_form_int(request.form, "photo_border_bottom_left", 0),
                "photo_frame_color": str(request.form.get("photo_frame_color") or "#8c2440").strip() or "#8c2440",
                "photo_shape": normalize_photo_shape(request.form.get("photo_shape") or "rectangle"),
                "photo_shape_inset": get_form_int(request.form, "photo_shape_inset", 0),
                "corel_editable_photo_mode": (
                    (request.form.get("corel_editable_photo_mode", "frame_only") or "frame_only").strip().lower()
                ),
            }
        
        # QR Settings
        qr_settings = get_default_qr_config()
        
        if request.is_json:
            qr_data = data.get("qr_settings", {}) or {}
            qr_settings["enable_qr"] = bool(qr_data.get("enable_qr", False))
            qr_settings["enable_barcode"] = bool(qr_data.get("enable_barcode", False))

            if qr_settings["enable_qr"]:
                qr_settings["qr_x"] = safe_get_int(qr_data, "qr_x", 50)
                qr_settings["qr_y"] = safe_get_int(qr_data, "qr_y", 50)
                qr_settings["qr_size"] = safe_get_int(qr_data, "qr_size", 120)
                qr_settings["qr_style"] = qr_data.get("qr_style", "square")
                qr_settings["qr_border"] = safe_get_int(qr_data, "qr_border", 2)
                
                qr_settings["qr_fill_color"] = validate_color(
                    safe_get_nested(qr_data, "qr_fill_color", default=[0, 0, 0])
                )
                qr_settings["qr_back_color"] = validate_color(
                    safe_get_nested(qr_data, "qr_back_color", default=[255, 255, 255])
                )
                
                qr_settings["qr_data_type"] = qr_data.get("qr_data_type", "student_id")
                if qr_settings["qr_data_type"] == "text":
                    qr_settings["qr_custom_text"] = qr_data.get("qr_custom_text", "")
                elif qr_settings["qr_data_type"] == "url":
                    qr_settings["qr_base_url"] = qr_data.get("qr_base_url", "https://example.com/verify/")
                
                qr_settings["qr_include_logo"] = bool(qr_data.get("qr_include_logo", False))
                # Always save logo path so it persists when toggling enable/disable
                qr_settings["qr_logo_path"] = qr_data.get("qr_logo_path", "")

            if qr_settings["enable_barcode"]:
                qr_settings["barcode_x"] = safe_get_int(qr_data, "barcode_x", 50)
                qr_settings["barcode_y"] = safe_get_int(qr_data, "barcode_y", 200)
                qr_settings["barcode_width"] = safe_get_int(qr_data, "barcode_width", 220)
                qr_settings["barcode_height"] = safe_get_int(qr_data, "barcode_height", 70)
                qr_settings["barcode_fill_color"] = validate_color(
                    safe_get_nested(qr_data, "barcode_fill_color", default=[0, 0, 0])
                )
                qr_settings["barcode_back_color"] = validate_color(
                    safe_get_nested(qr_data, "barcode_back_color", default=[255, 255, 255])
                )
                qr_settings["barcode_data_type"] = qr_data.get("barcode_data_type", "student_id")
                if qr_settings["barcode_data_type"] == "text":
                    qr_settings["barcode_custom_text"] = qr_data.get("barcode_custom_text", "")
                elif qr_settings["barcode_data_type"] == "url":
                    qr_settings["barcode_base_url"] = qr_data.get("barcode_base_url", "https://example.com/verify/")
        else:
            qr_settings["enable_qr"] = bool(request.form.get("enable_qr"))
            qr_settings["enable_barcode"] = bool(request.form.get("enable_barcode"))

            if qr_settings["enable_qr"]:
                qr_settings["qr_x"] = get_form_int(request.form, "qr_x", 50)
                qr_settings["qr_y"] = get_form_int(request.form, "qr_y", 50)
                qr_settings["qr_size"] = get_form_int(request.form, "qr_size", 120)
                qr_settings["qr_style"] = request.form.get("qr_style", "square")
                qr_settings["qr_border"] = get_form_int(request.form, "qr_border", 2)
                
                qr_fill_color = request.form.get("qr_fill_color", "#000000").strip()
                qr_back_color = request.form.get("qr_back_color", "#FFFFFF").strip()
                qr_settings["qr_fill_color"] = parse_color(qr_fill_color)
                qr_settings["qr_back_color"] = parse_color(qr_back_color)
                
                qr_settings["qr_data_type"] = request.form.get("qr_data_type", "student_id")
                if qr_settings["qr_data_type"] == "text":
                    qr_settings["qr_custom_text"] = request.form.get("qr_custom_text", "")
                elif qr_settings["qr_data_type"] == "url":
                    qr_settings["qr_base_url"] = request.form.get("qr_base_url", "https://example.com/verify/")
                qr_settings["qr_include_logo"] = bool(request.form.get("qr_include_logo"))
                # Always save logo path so it persists when toggling enable/disable
                qr_settings["qr_logo_path"] = request.form.get("qr_logo_path", "")

            if qr_settings["enable_barcode"]:
                qr_settings["barcode_x"] = get_form_int(request.form, "barcode_x", 50)
                qr_settings["barcode_y"] = get_form_int(request.form, "barcode_y", 200)
                qr_settings["barcode_width"] = get_form_int(request.form, "barcode_width", 220)
                qr_settings["barcode_height"] = get_form_int(request.form, "barcode_height", 70)
                barcode_fill_color = request.form.get("barcode_fill_color", "#000000").strip()
                barcode_back_color = request.form.get("barcode_back_color", "#FFFFFF").strip()
                qr_settings["barcode_fill_color"] = parse_color(barcode_fill_color)
                qr_settings["barcode_back_color"] = parse_color(barcode_back_color)
                qr_settings["barcode_data_type"] = request.form.get("barcode_data_type", "student_id")
                if qr_settings["barcode_data_type"] == "text":
                    qr_settings["barcode_custom_text"] = request.form.get("barcode_custom_text", "")
                elif qr_settings["barcode_data_type"] == "url":
                    qr_settings["barcode_base_url"] = request.form.get("barcode_base_url", "https://example.com/verify/")
        
        # Merge defaults
        default_font = get_default_font_config()
        default_photo = get_default_photo_config()
        default_qr = get_default_qr_config()
        font_settings = {**default_font, **font_settings}
        photo_settings = {**default_photo, **photo_settings}
        qr_settings = {**default_qr, **qr_settings}
        
        # Adjust settings for orientation
        try:
            font_settings = get_font_settings_for_orientation(template_id, font_settings)
            photo_settings = get_photo_settings_for_orientation(template_id, photo_settings)
        except Exception as e:
            logger.warning(f"Error adjusting settings for orientation: {e}")
            # Continue with current settings if adjustment fails

        if parsed_layout_for_font_sync:
            font_settings = derive_font_settings_from_layout_config(parsed_layout_for_font_sync, font_settings)
        
        # Update template settings in the database
        try:
            clear_layout_requested = False
            if request.is_json and isinstance(data, dict):
                clear_layout_requested = str(data.get("clear_layout_field_overrides", "")).strip().lower() in {"1", "true", "yes", "on"}
            else:
                clear_layout_requested = str(request.form.get("clear_layout_field_overrides", "")).strip().lower() in {"1", "true", "yes", "on"}
            if clear_layout_requested:
                clear_layout_field_overrides(template, settings_side)

            update_kwargs = {
                "template_id": template_id,
                "card_orientation": card_orientation,
                "card_dims": card_dims,
                "sheet_dims": sheet_dims,
                "grid_layout": grid_layout,
            }
            if settings_side == "back":
                update_kwargs["back_font_settings"] = font_settings
                update_kwargs["back_photo_settings"] = photo_settings
                update_kwargs["back_qr_settings"] = qr_settings
            else:
                update_kwargs["font_settings"] = font_settings
                update_kwargs["photo_settings"] = photo_settings
                update_kwargs["qr_settings"] = qr_settings
            update_template_settings(**update_kwargs)
        except Exception as e:
            logger.error(f"Error in update_template_settings: {e}")
            if request.is_json:
                return jsonify({'success': False, 'error': f'Failed to update template settings: {str(e)}'}), 500
            return redirect(url_for('dashboard.admin', error=f"Failed to update template settings: {str(e)}"))
        
        db.session.commit()
        
        # Log info
        arrangement = f"{final_w}x{final_h}px ({grid_layout['cols']} cols x {grid_layout['rows']} rows)"
        logger.info(f"Updated template {template_id} with {card_orientation} orientation: {arrangement}")
        
        if request.is_json:
            return jsonify({
                'success': True, 
                'message': f'Template settings updated successfully - {arrangement}',
                'arrangement': arrangement
            })
        else:
            return redirect(url_for('dashboard.admin', success=f"Template settings updated successfully - {arrangement}"))
        
    except ValueError as e:
        logger.error(f"Invalid input in template settings: {e}")
        error_msg = f"Invalid input in template settings: {str(e)}"
        if request.is_json:
            return jsonify({'success': False, 'error': error_msg}), 400
        return redirect(url_for('dashboard.admin', error=error_msg))
    except Exception as e:
        logger.error(f"Error updating template settings: {e}")
        error_msg = f"Error updating template settings: {str(e)}"
        if request.is_json:
            return jsonify({'success': False, 'error': error_msg}), 500
        return redirect(url_for('dashboard.admin', error=error_msg))
    

@app.route("/upload_font", methods=["POST"])
def upload_font():
    if 'font' not in request.files:
        logger.error("No font file uploaded")
        return redirect(url_for('dashboard.admin', error="No font file uploaded"))
    file = request.files['font']
    if file.filename == '':
        logger.error("No font file selected")
        return redirect(url_for('dashboard.admin', error="No font file selected"))
    if file and file.filename.lower().endswith(('.ttf', '.otf')):
        filename = secure_filename(file.filename)
        file_path = os.path.join(FONTS_FOLDER, filename)
        try:
            file.save(file_path)
            if is_valid_font_file(file_path):
                logger.info(f"Font uploaded: {filename}")
                return redirect(url_for('dashboard.admin', success="Font uploaded successfully"))
            else:
                os.remove(file_path)
                logger.error(f"Invalid font file: {filename}")
                return redirect(url_for('dashboard.admin', error="Invalid font file"))
        except Exception as e:
            logger.error(f"Error uploading font: {e}")
            return redirect(url_for('dashboard.admin', error=f"Error uploading font: {str(e)}"))
    else:
        logger.error("Invalid font file format")
        return redirect(url_for('dashboard.admin', error="Invalid file format. Use TTF or OTF"))

@app.route("/upload_qr_logo", methods=["POST"])
@login_required
def upload_qr_logo():
    if 'qr_logo_file' not in request.files:
        logger.warning("Upload QR logo failed: 'qr_logo_file' key not found in request.files")
        return jsonify({"success": False, "error": "No logo file uploaded"}), 400
    file = request.files['qr_logo_file']
    if file.filename == '':
        logger.warning("Upload QR logo failed: filename is empty")
        return jsonify({"success": False, "error": "No file selected"}), 400
    
    # Check file extension
    allowed_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.svg'}
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_extensions:
        logger.warning(f"Upload QR logo failed: file extension '{file_ext}' is not allowed")
        return jsonify({"success": False, "error": f"Invalid image format '{file_ext}'. Allowed formats: PNG, JPG, JPEG, GIF, SVG"}), 400

    try:
        from werkzeug.utils import secure_filename
        import uuid
        safe_filename = secure_filename(file.filename)
        filename = f"qr_logo_{int(time.time())}_{uuid.uuid4().hex[:6]}{file_ext}"
        
        logos_dir = os.path.join(STATIC_DIR, "logos")
        os.makedirs(logos_dir, exist_ok=True)
        
        file_path = os.path.join(logos_dir, filename)
        file.save(file_path)
        
        logo_rel_path = f"logos/{filename}"
        
        return jsonify({
            "success": True,
            "logo_path": logo_rel_path
        })
    except Exception as e:
        logger.error(f"Error uploading QR logo: {e}")
        return jsonify({"success": False, "error": f"Error saving file: {str(e)}"}), 500

@app.route("/update_font", methods=["POST"])
def update_font():
    try:
        templates = get_templates()
        if templates:
            template_id = templates[0]['id']
            font_settings = {
                "font_bold": request.form.get("font_bold", "arialbd.ttf"),
                "font_regular": request.form.get("font_regular", "arial.ttf"),
                "font_color": [int(x) for x in request.form.get("font_color", "0,0,0").split(",")[:3]],
                "colon_font_color": [int(x) for x in request.form.get("colon_font_color", request.form.get("font_color", "0,0,0")).split(",")[:3]],
                "label_font_size": int(request.form.get("label_font_size", 40)),
                "value_font_size": int(request.form.get("value_font_size", 36)),
                "label_x": int(request.form.get("label_x", 50)),
                "value_x": int(request.form.get("value_x", 280)),
                "start_y": int(request.form.get("start_y", 275)),
                "line_height": int(request.form.get("line_height", 50)),
                "text_case": request.form.get("text_case", "normal"), # Added text case
                "show_label_colon": (request.form.get("show_label_colon", "off").strip().lower() in {"1", "true", "yes", "on"}),
                "align_label_colon": (request.form.get("align_label_colon", "off").strip().lower() in {"1", "true", "yes", "on"}),
                "label_colon_gap": int(request.form.get("label_colon_gap", 8)),
            }
            
            # --- FIX: Clear layout_config fields on legacy font save ---
            if str(request.form.get("clear_layout_field_overrides", "")).strip().lower() in {"1", "true", "yes", "on"}:
                template = db.session.get(Template, template_id)
                if template:
                    for side_attr in ['layout_config', 'back_layout_config']:
                        config_str = getattr(template, side_attr)
                        if config_str:
                            try:
                                cfg = json.loads(config_str)
                                if "fields" in cfg:
                                    cfg["fields"] = {}
                                    setattr(template, side_attr, json.dumps(cfg))
                            except Exception:
                                pass
            # -----------------------------------------------------------
            
            update_template_settings(template_id, font_settings=font_settings)
            return redirect(url_for('dashboard.admin', success="Font settings updated successfully"))
        else:
            return redirect(url_for('dashboard.admin', error="No templates available"))
    except Exception as e:
        logger.error(f"Error in legacy font update: {e}")
        return redirect(url_for('dashboard.admin', error="Please use template-specific settings"))

@app.route("/update_photo_position", methods=["POST"])
def update_photo_position():
    try:
        templates = get_templates()
        if templates:
            template_id = templates[0]['id']
            photo_settings = {
                "photo_x": int(request.form.get("photo_x", 725)),
                "photo_y": int(request.form.get("photo_y", 200)),
                "photo_width": int(request.form.get("photo_width", 260)),
                "photo_height": int(request.form.get("photo_height", 313)),
                "corel_editable_photo_mode": (
                    (request.form.get("corel_editable_photo_mode", "frame_only") or "frame_only").strip().lower()
                ),
            }
            update_template_settings(template_id, photo_settings=photo_settings)
            return redirect(url_for('dashboard.admin', success="Photo settings updated successfully"))
        else:
            return redirect(url_for('dashboard.admin', error="No templates available"))
    except Exception as e:
        logger.error(f"Error in legacy photo update: {e}")
        return redirect(url_for('dashboard.admin', error="Please use template-specific settings"))

@app.route("/update_duplicate_settings", methods=["POST"])
def update_duplicate_settings():
    try:
        check_phone = 'check_phone' in request.form
        check_name_class = 'check_name_class' in request.form
        config = {
            "check_phone": check_phone,
            "check_name_class": check_name_class
        }
        save_duplicate_config(config)
        logger.info("Duplicate settings updated successfully")
        return redirect(url_for('dashboard.admin', success="Duplicate settings updated successfully"))
    except Exception as e:
        logger.error(f"Error updating duplicate settings: {e}")
        return redirect(url_for('dashboard.admin', error=f"Error updating duplicate settings: {str(e)}"))

@app.route("/delete_all", methods=["POST"])
def delete_all():
    # Enforce RBAC: Only super admin can nuke the entire database
    if session.get("admin_role") != "super_admin":
        return redirect(url_for('dashboard.admin', error="Only super administrators can perform global deletion."))
        
    try:
        # Delete all students
        Student.query.delete()
        # Delete all templates
        Template.query.delete()
        db.session.commit()
        
        # Clean up files
        for folder in [UPLOAD_FOLDER, GENERATED_FOLDER]:
            for file in os.listdir(folder):
                file_path = os.path.join(folder, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
        if os.path.exists(TEMPLATES_CONFIG):
            os.remove(TEMPLATES_CONFIG)
        logger.info("All data and files deleted successfully")
        return redirect(url_for('dashboard.admin', success="All data and files deleted successfully"))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting all data: {e}")
        return redirect(url_for('dashboard.admin', error=f"Error deleting data: {str(e)}"))

@app.route("/delete_student/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    if not session.get("admin"):
        return redirect(url_for('auth.student_login'))

    try:
        student = db.session.get(Student, student_id)
        if not student:
            return redirect(url_for('dashboard.admin', error="Student not found"))
        
        # Enforce RBAC
        if session.get("admin_role") == "school_admin" and student.school_name != session.get("admin_school"):
            return redirect(url_for('dashboard.admin', error="Unauthorized to delete student from this school."))

        # Save name for logging before deletion
        student_name = student.name

        # Delete associated files
        if student.photo_filename and student.photo_filename != "placeholder.jpg":
            photo_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
            if os.path.exists(photo_path):
                try:
                    os.remove(photo_path)
                    logger.info(f"Deleted photo: {photo_path}")
                except Exception as e:
                    logger.warning(f"Could not delete photo file: {e}")

        if student.generated_filename:
            # Try deleting both PDF and JPG versions just in case
            for ext in ['.pdf', '.jpg']:
                # Handle filename replacement safely
                base_name = os.path.splitext(student.generated_filename)[0]
                generated_path = os.path.join(GENERATED_FOLDER, base_name + ext)
                
                if os.path.exists(generated_path):
                    try:
                        os.remove(generated_path)
                        logger.info(f"Deleted generated file: {generated_path}")
                    except Exception as e:
                        logger.warning(f"Could not delete generated file: {e}")
        
        # Delete record from Database
        db.session.delete(student)
        db.session.commit()
        
        # --- LOG ACTIVITY ---
        log_activity("Deleted Student", target=f"ID {student_id}", details=f"Name: {student_name}")
        # --------------------

        logger.info(f"Deleted student ID {student_id}")
        return redirect(url_for('dashboard.admin', success="Student deleted successfully"))

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting student {student_id}: {e}")
        return redirect(url_for('dashboard.admin', error=f"Error deleting student: {str(e)}"))
    
@app.route("/delete_pdf/<path:filename>", methods=["POST"])
def delete_pdf(filename):
    pdf_path = os.path.join(GENERATED_FOLDER, filename)
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
        logger.info(f"Deleted PDF: {filename}")
        return redirect(url_for('dashboard.admin', success="PDF deleted successfully"))
    else:
        return redirect(url_for('dashboard.admin', error="PDF not found"))

@app.route("/export_csv")
def export_csv():
    try:
        import csv
        import io
        
        students = Student.query.all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Name", "Father's Name", "Class", "DOB", "Address", "Phone", "Photo Filename", "Generated Filename", "Created At", "Template ID", "School Name", "Email"])
        
        for student in students:
            writer.writerow([
                student.id, student.name, student.father_name, student.class_name,
                student.dob, student.address, student.phone, student.photo_filename,
                student.generated_filename, student.created_at, student.template_id,
                student.school_name, student.email
            ])
        
        logger.info("Exported student data to CSV")
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=students_export.csv"}
        )
    except Exception as e:
        logger.error(f"Error exporting CSV: {e}")
        return redirect(url_for('dashboard.admin', error=f"Error exporting CSV: {str(e)}"))

@app.route("/download_template/<int:template_id>")
def download_template(template_id):
    if not session.get("admin"):
        return redirect(url_for('auth.login'))
    side = (request.args.get("side") or "front").strip().lower()
    if side not in {"front", "back"}:
        side = "front"
    template_path = get_template_path(template_id, side=side)
    if template_path and template_path.startswith(("http://", "https://")):
        logger.info(f"Downloading remote template ID {template_id} side={side}")
        return redirect(template_path)
    if template_path and os.path.exists(template_path):
        logger.info(f"Downloading template ID {template_id} side={side}")
        return send_file(template_path, as_attachment=True)
    else:
        logger.error(f"No template available for ID {template_id} side={side}")
        return redirect(url_for('dashboard.admin', error="No template available for download"))


@app.route("/remove_template/<int:template_id>", methods=["POST"])
def remove_template(template_id):
    try:
        template = db.session.get(Template, template_id)


        if not template:
            return redirect(url_for('dashboard.admin', error="Template not found"))
        
        # Enforce RBAC
        if session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
            return redirect(url_for('dashboard.admin', error="Unauthorized to remove template for this school."))
            
        template_path = get_template_path(template_id)
        
        # Delete template
        db.session.delete(template)
        
        # Update students with this template
        Student.query.filter_by(template_id=template_id).update({
            'template_id': None,
            'school_name': None
        })
        
        db.session.commit()
        
        if template_path and os.path.exists(template_path):
            os.remove(template_path)
            logger.info(f"Removed template file: {template_path}")
        
        logger.info(f"Removed template ID {template_id}")
        return redirect(url_for('dashboard.admin', success="Template removed successfully"))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error removing template {template_id}: {e}")
        return redirect(url_for('dashboard.admin', error=f"Error removing template: {str(e)}"))

# ================== FULL ADMIN PREVIEW ROUTE (Memory Only - No Files) ==================
@app.route("/admin_preview_card", methods=["POST"])
def admin_preview_card():
    try:
        data = request.get_json()
        logger.debug(f"Incoming admin_preview_card data: {data}")
        if not data: return jsonify({"success": False, "error": "No data received"}), 400

        def safe_wrap_preview(text, width):
            """Prevent textwrap from crashing on invalid/zero widths from layout math."""
            try:
                safe_width = int(float(width))
            except Exception:
                safe_width = 1
            if safe_width < 1:
                safe_width = 1
            return textwrap.wrap(str(text or ""), width=safe_width, break_long_words=True)

        # 1. Get Settings
        template_id = data.get("template_id")
        if not template_id:
            return jsonify({"success": False, "error": "Template ID missing"}), 400
        
        side = (data.get("side") or "front").strip().lower()
        
        font_settings = {**get_default_font_config(), **data.get("font_settings", {})}
        photo_settings = {**get_default_photo_config(), **data.get("photo_settings", {})}
        photo_settings["photo_shape"] = normalize_photo_shape(photo_settings.get("photo_shape", "rectangle"))
        qr_settings = {**get_default_qr_config(), **data.get("qr_settings", {})}
        
        # 2. Load Template
        template = db.session.get(Template, template_id)
        if not template:
            return jsonify({"success": False, "error": f"Template {template_id} not found in database"}), 404

        template_path = get_template_path(template_id, side=side)
        if not template_path:
            return jsonify({"success": False, "error": "Template file path could not be resolved"}), 404
        
        try:
            card_width, card_height = get_card_size(template_id)
            template_img = load_template_smart(template_path).resize((card_width, card_height))
        except Exception as e:
            logger.error(f"Error loading template {template_id} from {template_path}: {e}")
            return jsonify({"success": False, "error": f"Failed to load template: {str(e)}"}), 500
        
        draw = ImageDraw.Draw(template_img)
        
        # --- NEW: ROBUST FONT LOADING LOGIC ---
        def load_safe_font(font_name, size, lang="english", sample_text=""):
            """
            Load a font from /static/fonts/ with language-aware fallbacks.
            Uses load_font_dynamic so Arabic/Urdu/Hindi never fall back to Arial.
            """
            requested_path = os.path.join(FONTS_FOLDER, font_name) if font_name else ""
            text_for_pick = sample_text if sample_text else " "
            return load_font_dynamic(requested_path, text_for_pick, 10**9, size, language=lang)

        try:
            L_COLOR = tuple(font_settings.get("label_font_color", [0,0,0]))
            V_COLOR = tuple(font_settings.get("value_font_color", [0,0,0]))
            C_COLOR = tuple(font_settings.get("colon_font_color", list(L_COLOR)))
        except Exception:
            L_COLOR = V_COLOR = (0, 0, 0)
            C_COLOR = L_COLOR

        enable_label_gradient = bool(font_settings.get("enable_label_gradient", False))
        label_fill_bottom = tuple(font_settings.get("label_font_color_bottom", [51, 51, 51]))

        enable_value_gradient = bool(font_settings.get("enable_value_gradient", False))
        value_fill_bottom = tuple(font_settings.get("value_font_color_bottom", [51, 51, 51]))

        enable_colon_gradient = bool(font_settings.get("enable_colon_gradient", False))
        colon_fill_bottom = tuple(font_settings.get("colon_font_color_bottom", [51, 51, 51]))
        
        # --- LANGUAGE LOGIC ---
        lang, direction = get_template_language_direction(template_id, side=side)
        incoming_preview_layout = None
        if template is not None:
            ignore_layout_fields = str(data.get("ignore_layout_fields", "")).strip().lower() in {"1", "true", "yes", "on"}
            setattr(template, "_ignore_layout_field_overrides", ignore_layout_fields)
            if "layout_config" in data:
                incoming_preview_layout = parse_layout_config(data.get("layout_config"))
                preview_layout_attr = "back_layout_config" if side == "back" else "layout_config"
                setattr(
                    template,
                    preview_layout_attr,
                    json.dumps(incoming_preview_layout, ensure_ascii=False) if incoming_preview_layout else None,
                )
        incoming_lang = str(data.get("language") or "").strip().lower()
        incoming_direction = str(data.get("text_direction") or "").strip().lower()
        lock_rules = getattr(template, "language_lock_rules", None) if template else {}
        if isinstance(lock_rules, dict):
            locked_lang = str(lock_rules.get(side, "") or "").strip().lower()
            if locked_lang in {"english", "urdu", "hindi", "arabic"}:
                incoming_lang = locked_lang
                incoming_direction = "rtl" if locked_lang in {"urdu", "arabic"} else "ltr"
        if incoming_lang in {"english", "urdu", "hindi", "arabic"}:
            lang = incoming_lang
        if incoming_direction in {"ltr", "rtl"}:
            direction = incoming_direction

        localization_pack = getattr(template, "localization_pack", None) if template else None
        labels_map = get_localized_standard_labels(lang, localization_pack)

        sample_data_map = {
            'english': {'NAME': 'John Doe', 'F_NAME': 'Richard Roe', 'CLASS': 'X - A', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': '123 Long Street Name, Apartment 4B, Big City District, State 560001'},
            'urdu':    {'NAME': 'محمد علی', 'F_NAME': 'احمد علی', 'CLASS': 'دہم - اے', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': 'مکان نمبر 123، سٹریٹ 4، لاہور، پاکستان'},
            'hindi':   {'NAME': 'राहुल कुमार', 'F_NAME': 'अमित कुमार', 'CLASS': '१० - ए', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': 'मकान नं. १२३, गली नं. ४, दिल्ली'},
            'arabic':  {'NAME': 'محمد أحمد', 'F_NAME': 'علي أحمد', 'CLASS': 'العاشر - أ', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': 'شارع الملك فيصل، مبنى ٤، الرياض'}
        }

        values_map = sample_data_map.get(lang, sample_data_map['english'])

        layout_config_raw = (
            json.dumps(incoming_preview_layout, ensure_ascii=False)
            if incoming_preview_layout is not None
            else get_template_layout_config(template_id, side=side)
        )
        # 4. Prepare Fields
        text_case = font_settings.get("text_case", "normal")
        show_label_colon = bool(font_settings.get("show_label_colon", True))
        align_label_colon = bool(font_settings.get("align_label_colon", True))
        label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)
        all_fields = [
            {'key': 'NAME', 'label': labels_map['NAME'], 'val': values_map['NAME'], 'order': 10},
            {'key': 'F_NAME', 'label': labels_map['F_NAME'], 'val': values_map['F_NAME'], 'order': 20},
            {'key': 'CLASS', 'label': labels_map['CLASS'], 'val': values_map['CLASS'], 'order': 30},
            {'key': 'DOB', 'label': labels_map['DOB'], 'val': values_map['DOB'], 'order': 40},
            {'key': 'MOBILE', 'label': labels_map['MOBILE'], 'val': values_map['MOBILE'], 'order': 50},
            {'key': 'ADDRESS', 'label': labels_map['ADDRESS'], 'val': values_map['ADDRESS'], 'order': 60}
        ]
        
        if template:
            try:
                for field in TemplateField.query.filter_by(template_id=template_id).order_by(TemplateField.display_order.asc()).all():
                    sample_val = f"Sample {field.field_label}"
                    if lang == 'urdu': sample_val = f"نمونہ {field.field_label}"
                    elif lang == 'hindi': sample_val = f"नमूना {field.field_label}"
                    elif lang == 'arabic': sample_val = f"عينة {field.field_label}"

                    all_fields.append({
                        'key': field.field_name,
                        'label': field.field_label,
                        'val': sample_val,
                        'order': field.display_order
                    })
            except Exception: pass

        all_fields.sort(key=lambda x: int(x.get('order') or 0))
        
        # --- PRE-CALCULATE ---
        p_x = photo_settings.get("photo_x", 0)
        p_y = photo_settings.get("photo_y", 0)
        p_w = photo_settings.get("photo_width", 0)
        p_h = photo_settings.get("photo_height", 0)
        p_bottom = p_y + p_h

        # Draw Loop
        label_x = font_settings["label_x"]
        value_x = font_settings["value_x"]
        current_y = get_initial_flow_y_for_side(template, font_settings, side=side)
        line_height = font_settings["line_height"]

        address_max_lines = int(font_settings.get("address_max_lines", 2))
        for item in all_fields:
            raw_label = apply_text_case(item['label'], text_case)
            raw_val = apply_text_case(item['val'], text_case)

            # --- TEXT SHAPING ---
            display_label = process_text_for_drawing(raw_label, lang)
            display_val = process_text_for_drawing(raw_val, lang)

            field_key = item.get('key') or order_to_field_key(item.get('order'))
            layout_item = resolve_field_layout_for_side(
                template,
                field_key,
                label_x,
                value_x,
                current_y,
                side=side,
            )
            if not field_within_vertical_bounds(layout_item, current_y, card_height):
                continue
            label_x_eff = layout_item["label_x"]
            value_x_eff = layout_item["value_x"]
            label_y_eff = layout_item["label_y"]
            value_y_eff = layout_item["value_y"]
            label_fill = layout_item.get("label_color") or L_COLOR
            value_fill = layout_item.get("value_color") or V_COLOR
            colon_fill = layout_item.get("colon_color") or C_COLOR
            label_font_size_eff = max(1, int(layout_item.get("label_font_size") or font_settings["label_font_size"]))
            value_font_size_eff = max(1, int(layout_item.get("value_font_size") or font_settings["value_font_size"]))
            colon_font_size_eff = max(1, int(layout_item.get("colon_font_size") or label_font_size_eff))
            colon_y_eff = layout_item.get("colon_y", label_y_eff)
            colon_x_eff = layout_item.get("colon_x")
            colon_grow_eff = layout_item.get("colon_grow")
            l_font = load_safe_font(font_settings.get("font_bold", "arialbd.ttf"), label_font_size_eff, lang)

            label_text_final, colon_text_final = split_label_and_colon(
                display_label,
                lang,
                direction,
                include_colon=show_label_colon,
                align_colon=align_label_colon,
            )
            if not field_consumes_layout_space(layout_item, raw_val):
                continue
            advances_flow = field_advances_layout_flow(layout_item, raw_val, separate_colon=bool(colon_text_final))
            from app.services.render_service import (
                draw_text_with_spacing_pil,
                measure_text_width_with_spacing_local,
                flip_x_for_text_direction_local,
            )

            label_char_spacing = layout_item.get("label_char_spacing", 0)
            label_line_height = layout_item.get("label_line_height") or line_height

            if layout_item.get("label_auto_fit") and layout_item.get("label_max_width"):
                max_w_lbl = float(layout_item["label_max_width"])
                while label_font_size_eff > 6:
                    temp_lbl_font = load_safe_font(font_settings.get("font_bold", "arialbd.ttf"), label_font_size_eff, lang, label_text_final)
                    w = measure_text_width_with_spacing_local(label_text_final, temp_lbl_font, label_char_spacing, draw=draw, **get_draw_text_kwargs(label_text_final, lang))
                    if w <= max_w_lbl:
                        break
                    label_font_size_eff -= 1

            l_font = load_safe_font(font_settings.get("font_bold", "arialbd.ttf"), label_font_size_eff, lang, label_text_final)
            colon_font = load_safe_font(font_settings.get("font_bold", "arialbd.ttf"), colon_font_size_eff, lang, colon_text_final or ":")

            if layout_item["label_visible"]:
                lbl_w = measure_text_width_with_spacing_local(label_text_final, l_font, label_char_spacing, draw=draw, **get_draw_text_kwargs(label_text_final, lang))
                label_draw_x = flip_x_for_text_direction_local(
                    label_x_eff,
                    lbl_w,
                    card_width,
                    direction,
                    grow_mode=layout_item["label_grow"],
                )
                draw_text_with_spacing_pil(
                    draw,
                    (label_draw_x, label_y_eff),
                    label_text_final,
                    font=l_font,
                    fill=label_fill,
                    char_spacing=label_char_spacing,
                    target_image=template_img,
                    enable_gradient=enable_label_gradient,
                    bottom_color=label_fill_bottom,
                    **{"direction": direction, **get_draw_text_kwargs(label_text_final, lang)}
                )
                draw_aligned_colon_pil(
                    draw,
                    card_width,
                    direction,
                    value_x_eff,
                    colon_y_eff,
                    colon_text_final,
                    colon_font,
                    colon_fill,
                    lang,
                    label_colon_gap,
                    anchor_x=colon_x_eff,
                    grow_mode=colon_grow_eff,
                    target_image=template_img,
                    enable_gradient=enable_colon_gradient,
                    bottom_color=colon_fill_bottom,
                )

            max_w = int(get_anchor_max_text_width(
                card_width=card_width,
                anchor_x=value_x_eff,
                text_direction=direction,
                line_y=value_y_eff,
                line_height=line_height,
                grow_mode=layout_item["value_grow"],
                photo_x=p_x,
                photo_y=p_y,
                photo_width=p_w,
                photo_height=p_h,
                page_margin=20,
                photo_gap=15,
                min_width=20,
            ))

            value_char_spacing = layout_item.get("value_char_spacing", 0)
            value_line_height = layout_item.get("value_line_height") or line_height

            # --- ADDRESS LOGIC (DYNAMIC LINES) ---
            if field_key == 'ADDRESS':
                from app.services.render_service import fit_wrapped_text_pil
                
                # Get font loader
                def font_loader(size_px):
                    return load_safe_font(font_settings.get("font_regular", "arial.ttf"), size_px, lang, raw_val)
                
                best_size, wrapped_lines = fit_wrapped_text_pil(
                    raw_val,
                    font_loader,
                    start_size_px=value_font_size_eff,
                    min_size_px=10,
                    max_width_px=max_w,
                    max_lines=address_max_lines,
                    char_spacing=value_char_spacing,
                    draw=draw,
                    lang=lang,
                )
                
                addr_font = load_safe_font(
                    font_settings.get("font_regular", "arial.ttf"),
                    best_size,
                    lang,
                    raw_val,
                )
                
                try:
                    val_lh = float(value_line_height)
                except (ValueError, TypeError):
                    val_lh = 1.2
                spacing = val_lh if val_lh > 10 else best_size * (val_lh if val_lh > 0 else 1.2)
                
                for line in wrapped_lines[:address_max_lines]:
                    line_display = process_text_for_drawing(line, lang)
                    if layout_item["value_visible"]:
                        line_w = measure_text_width_with_spacing_local(line_display, addr_font, value_char_spacing, draw=draw, **get_draw_text_kwargs(line_display, lang))
                        value_draw_x = flip_x_for_text_direction_local(
                            value_x_eff,
                            line_w,
                            card_width,
                            direction,
                            grow_mode=layout_item["value_grow"],
                        )
                        draw_text_with_spacing_pil(
                            draw,
                            (value_draw_x, value_y_eff),
                            line_display,
                            font=addr_font,
                            fill=value_fill,
                            char_spacing=value_char_spacing,
                            target_image=template_img,
                            enable_gradient=enable_value_gradient,
                            bottom_color=value_fill_bottom,
                            **{"direction": direction, **get_draw_text_kwargs(line_display, lang)}
                        )
                    value_y_eff += spacing
                    if advances_flow:
                        current_y += spacing

            # --- STANDARD FIELDS ---
            else:
                if layout_item.get("value_auto_fit") and layout_item.get("value_max_width"):
                    max_w_val = float(layout_item["value_max_width"])
                    while value_font_size_eff > 6:
                        temp_val_font = load_safe_font(font_settings.get("font_regular", "arial.ttf"), value_font_size_eff, lang, display_val)
                        w = measure_text_width_with_spacing_local(display_val, temp_val_font, value_char_spacing, draw=draw, **get_draw_text_kwargs(display_val, lang))
                        if w <= max_w_val:
                            break
                        value_font_size_eff -= 1

                v_font = load_safe_font(
                    font_settings.get("font_regular", "arial.ttf"),
                    value_font_size_eff,
                    lang,
                    display_val,
                )
                if layout_item["value_visible"]:
                    val_w = measure_text_width_with_spacing_local(display_val, v_font, value_char_spacing, draw=draw, **get_draw_text_kwargs(display_val, lang))
                    value_draw_x = flip_x_for_text_direction_local(
                        value_x_eff,
                        val_w,
                        card_width,
                        direction,
                        grow_mode=layout_item["value_grow"],
                    )
                    draw_text_with_spacing_pil(
                        draw,
                        (value_draw_x, value_y_eff),
                        display_val,
                        font=v_font,
                        fill=value_fill,
                        char_spacing=value_char_spacing,
                        target_image=template_img,
                        enable_gradient=enable_value_gradient,
                        bottom_color=value_fill_bottom,
                        **{"direction": direction, **get_draw_text_kwargs(display_val, lang)}
                    )
                if advances_flow:
                    current_y += value_line_height
                        
        # 6. Photo & QR Placeholders
        if photo_settings.get("enable_photo", True):
            try:
                if not os.path.exists(PLACEHOLDER_PATH):
                    ph = Image.new("RGB", (100, 100), color="gray")
                else:
                    image_open = getattr(Image, "open_original", Image.open)
                    ph = image_open(PLACEHOLDER_PATH)
                ph = _process_photo_pil(
                    ph,
                    target_width=int(float(photo_settings.get("photo_width", 100) or 100)),
                    target_height=int(float(photo_settings.get("photo_height", 100) or 100)),
                )
                radii = [int(float(photo_settings.get(f"photo_border_{k}", 0) or 0)) for k in ["top_left", "top_right", "bottom_right", "bottom_left"]]
                ph = round_photo(
                    ph,
                    radii,
                    border_color=photo_settings.get("photo_frame_color"),
                    border_thickness=2 if photo_settings.get("photo_frame_color") else 0,
                    shape=photo_settings.get("photo_shape", "rectangle"),
                    shape_inset=photo_settings.get("photo_shape_inset", 0),
                )
                px = int(float(photo_settings.get("photo_x", 0) or 0))
                py = int(float(photo_settings.get("photo_y", 0) or 0))
                template_img.paste(ph, (px, py), ph)
            except Exception as e:
                logger.warning(f"Live preview photo error: {e}")
                pass
        
        if qr_settings.get("enable_qr", False):
            try:
                code_size = int(float(qr_settings.get("qr_size", 120) or 120))
                code_x = int(float(qr_settings.get("qr_x", 50) or 50))
                code_y = int(float(qr_settings.get("qr_y", 50) or 50))
                qr_img = generate_qr_code("PREVIEW", qr_settings, code_size)
                qr_img = qr_img.resize((code_size, code_size))
                template_img.paste(qr_img, (code_x, code_y))
            except Exception as e:
                logger.warning(f"Live preview QR error: {e}")
                pass

        if qr_settings.get("enable_barcode", False):
            try:
                barcode_x = int(float(qr_settings.get("barcode_x", 50) or 50))
                barcode_y = int(float(qr_settings.get("barcode_y", 200) or 200))
                barcode_w = max(40, int(float(qr_settings.get("barcode_width", 220) or 220)))
                barcode_h = max(30, int(float(qr_settings.get("barcode_height", 70) or 70)))
                barcode_img = generate_barcode_code128("PREVIEW", qr_settings, width=barcode_w, height=barcode_h)
                template_img.paste(barcode_img, (barcode_x, barcode_y))
            except Exception as e:
                logger.warning(f"Live preview barcode error: {e}")
                pass
        apply_layout_custom_objects_pil(template_img, template, font_settings, side=side, language=lang)
        template_img = force_rgb(template_img)
        logger.debug(f"FINAL IMAGE MODE BEFORE SAVE: {template_img.mode}")
        buffer = io.BytesIO()
        template_img.save(buffer,
                          format="JPEG",
                          quality=95,
                          subsampling=0,
                          optimize=True)
        buffer.seek(0)
        img_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        return jsonify({"success": True, "image_data": f"data:image/jpeg;base64,{img_data}"})
        
    except Exception as e:
        logger.exception("Admin preview traceback")
        logger.error(f"Admin preview failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    
@app.route("/force_generate_pdf/<int:template_id>", methods=["POST"])
def force_generate_pdf(template_id):
    try:
        sheet_path = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}.png")
        positions_file = os.path.join(GENERATED_FOLDER, f"positions_template_{template_id}.txt")
      
        if not os.path.exists(sheet_path):
            logger.error(f"No sheet found for template {template_id}")
            return redirect(url_for('dashboard.admin', error=f"No sheet found for template {template_id}"))
      
        sheet = Image.open(sheet_path)
        positions = []
        if os.path.exists(positions_file):
            with open(positions_file, "r") as f:
                positions = [tuple(map(int, p.split(','))) for p in f.read().split(';') if p]
      
        orientation = get_template_orientation(template_id)
        card_count = len(positions)
      
        # Generate descriptive PDF name
        if orientation == 'landscape':
            arrangement = "2x5_landscape"
            arrangement_desc = "2 columns × 5 rows (Landscape)"
        else:
            arrangement = "5x2_portrait"
            arrangement_desc = "5 columns × 2 rows (Portrait)"
          
        pdf_name = f"ID_Cards_{arrangement}_{card_count}_cards_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
      
        # Save as PDF (Cloudinary on deployed, local filesystem when running locally)
        pdf_buffer = io.BytesIO()
        sheet.save(pdf_buffer, "PDF", resolution=DPI)
        pdf_buffer.seek(0)
        if STORAGE_BACKEND == "local":
            os.makedirs(GENERATED_FOLDER, exist_ok=True)
            with open(os.path.join(GENERATED_FOLDER, pdf_name), "wb") as fh:
                fh.write(pdf_buffer.getvalue())
        else:
            pdf_url = upload_image(pdf_buffer.getvalue(), folder='bulk-sheets', resource_type='raw')
      
        logger.info(f"Generated PDF: {arrangement_desc} with {card_count} cards")
      
        # Clean up temporary files
        if os.path.exists(sheet_path):
            os.remove(sheet_path)
        if os.path.exists(positions_file):
            os.remove(positions_file)
          
        success_msg = f"PDF generated: {arrangement_desc} with {card_count} cards - {pdf_name}"
        return redirect(url_for('dashboard.admin', success=success_msg))
      
    except Exception as e:
        logger.error(f"Error forcing PDF generation for template {template_id}: {e}")
        return redirect(url_for('dashboard.admin', error=f"Error generating PDF: {str(e)}"))

@app.route("/debug_color/<int:template_id>")
def debug_color(template_id):
    """Debug route to check color settings"""
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403
  
    try:
        template = db.session.get(Template, template_id)


        if not template:
            return jsonify({"error": "Template not found"}), 404
        
        font_settings = template.font_settings or {}
        return jsonify({
            "template_id": template_id,
            "font_settings": font_settings,
            "label_font_color": font_settings.get('label_font_color'),
            "value_font_color": font_settings.get('value_font_color'),
            "label_font_color_type": type(font_settings.get('label_font_color')).__name__ if 'label_font_color' in font_settings else 'None',
            "value_font_color_type": type(font_settings.get('value_font_color')).__name__ if 'value_font_color' in font_settings else 'None'
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/test_color_render")
def test_color_render():
    """Test route to render colors on an image"""
    try:
        # Create a test image
        test_img = Image.new("RGB", (800, 600), color="white")
        draw = ImageDraw.Draw(test_img)
      
        # Test colors
        colors = [
            ("Red", [255, 0, 0]),
            ("Green", [0, 255, 0]),
            ("Blue", [0, 0, 255]),
            ("Black", [0, 0, 0]),
            ("Gray", [128, 128, 128])
        ]
      
        y = 50
        for name, color in colors:
            draw.text((50, y), process_text_for_drawing(f"{name}: {color}", "english"), fill=tuple(color))
            y += 40
      
        # Save and return (Cloudinary on deployed, local filesystem when running locally)
        test_buffer = io.BytesIO()
        test_img.save(test_buffer, "JPEG", quality=95)
        test_buffer.seek(0)
        if STORAGE_BACKEND == "local":
            os.makedirs(GENERATED_FOLDER, exist_ok=True)
            test_name = f"test_color_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex}.jpg"
            with open(os.path.join(GENERATED_FOLDER, test_name), "wb") as fh:
                fh.write(test_buffer.getvalue())
            test_url = url_for('static', filename=f'generated/{test_name}')
        else:
            test_url = upload_image(test_buffer.getvalue(), folder='test', resource_type='image')
      
        return jsonify({
            "success": True,
            "message": "Color test completed",
            "test_image": test_url
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/debug_students")
def debug_students():
    if not session.get("admin") and not session.get("student_email"):
        return jsonify({"error": "Unauthorized"}), 401
  
    try:
        if session.get("admin"):
            students = Student.query.order_by(Student.id.desc()).limit(10).all()
        else:
            students = Student.query.filter_by(email=session['student_email']).order_by(Student.id.desc()).limit(10).all()
        
        result = []
        for student in students:
            result.append({
                'id': student.id,
                'name': student.name,
                'email': student.email or 'No email',
                'has_password': bool(student.password),
                'generated_filename': student.generated_filename,
                'created_at': student.created_at.isoformat() if student.created_at else None
            })
        
        return jsonify({
            'user': session.get('student_email') or 'admin',
            'student_count': len(students),
            'recent_students': result
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/debug_session")
def debug_session():
    if not session.get("student_email") and not session.get("admin"):
        return jsonify({"error": "Not logged in"}), 401
  
    return jsonify({
        "student_email": session.get("student_email"),
        "student_school_name": session.get("student_school_name"),
        "admin": session.get("admin"),
        "all_session_keys": list(session.keys())
    })

@app.route("/debug_template/<int:template_id>")
def debug_template(template_id):
    """Debug route to check template and settings"""
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403
  
    try:
        template = db.session.get(Template, template_id)


        if not template:
            return jsonify({"error": "Template not found"}), 404
        
        font_settings, photo_settings, qr_settings, orientation = get_template_settings(template_id)
        template_path = get_template_path(template_id)
      
        # Check if template file exists and is readable
        template_exists = os.path.exists(template_path) if template_path else False
        template_readable = False
        template_size = "N/A"
      
        if template_exists:
            try:
                with Image.open(template_path) as img:
                    template_readable = True
                    template_size = f"{img.size[0]}x{img.size[1]}"
            except Exception as e:
                template_readable = False
      
        # Check fonts
        available_fonts = get_available_fonts()
        font_bold_exists = os.path.exists(os.path.join(FONTS_FOLDER, font_settings["font_bold"]))
        font_regular_exists = os.path.exists(os.path.join(FONTS_FOLDER, font_settings["font_regular"]))
      
        return jsonify({
            "template_id": template_id,
            "template_path": template_path,
            "template_exists": template_exists,
            "template_readable": template_readable,
            "template_size": template_size,
            "orientation": orientation,
            "font_settings": font_settings,
            "photo_settings": photo_settings,
            "qr_settings": qr_settings,
            "fonts_folder_exists": os.path.exists(FONTS_FOLDER),
            "available_fonts": available_fonts,
            "font_bold_exists": font_bold_exists,
            "font_regular_exists": font_regular_exists,
            "generated_folder_exists": os.path.exists(GENERATED_FOLDER),
            "upload_folder_exists": os.path.exists(UPLOAD_FOLDER),
            "deadline": template.deadline.isoformat() if template.deadline else None
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/validate_templates", methods=["GET"])
def validate_templates():
    """Validate all template sources (Cloudinary URLs or local files)."""
    if not session.get("admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        templates = Template.query.order_by(Template.created_at.desc()).all()
        results = []
        ok_count = 0
        broken_count = 0

        for tpl in templates:
            src = get_template_path(tpl.id)
            row = {
                "template_id": tpl.id,
                "school_name": tpl.school_name,
                "source": src,
            }

            if not src:
                row["status"] = "broken"
                row["error"] = "Template source is missing (no URL/file path)."
                broken_count += 1
                results.append(row)
                continue

            try:
                img = load_template_smart(src)
                w, h = img.size
                row["status"] = "ok"
                row["size"] = {"width": int(w), "height": int(h)}
                ok_count += 1
            except Exception as e:
                row["status"] = "broken"
                row["error"] = str(e)
                broken_count += 1

            results.append(row)

        return jsonify(
            {
                "success": True,
                "total": len(results),
                "ok": ok_count,
                "broken": broken_count,
                "results": results,
            }
        )
    except Exception as e:
        logger.error(f"Template validation failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/admin/test_cloudinary_connection", methods=["GET"])
def test_cloudinary_connection():
    """Test connection to Cloudinary and verify API configurations."""
    if not session.get("admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        from cloudinary_config import CLOUDINARY_CONFIGURED
        import cloudinary
        import cloudinary.uploader
        import io
        import uuid
        
        # 1. Check if configured
        if not CLOUDINARY_CONFIGURED:
            return jsonify({
                "success": False,
                "error": "Cloudinary is not configured. Environment variables (CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET) are missing."
            })

        # 2. Try simple upload + delete check
        # 1x1 transparent PNG pixel bytes
        pixel_bytes = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
        
        try:
            public_id = f"test_conn_{uuid.uuid4().hex}"
            file_obj = io.BytesIO(pixel_bytes)
            file_obj.name = "test_pixel.png"
            
            result = cloudinary.uploader.upload(
                file_obj,
                public_id=public_id,
                folder="connection_test",
                resource_type="image"
            )
            
            uploaded_url = result.get("secure_url") or result.get("url")
            full_public_id = result.get("public_id")
            
            # Clean up immediately
            if full_public_id:
                cloudinary.uploader.destroy(full_public_id)
                
            return jsonify({
                "success": True,
                "message": "Cloudinary connection test passed successfully!",
                "cloud_name": cloudinary.config().cloud_name,
                "api_key": cloudinary.config().api_key[:4] + "****" if cloudinary.config().api_key else None,
                "uploaded_url": uploaded_url
            })
            
        except Exception as upload_err:
            return jsonify({
                "success": False,
                "error": f"Cloudinary API Error (Upload failed): {str(upload_err)}"
            })

    except Exception as e:
        logger.error(f"Cloudinary test connection failed: {e}")
        return jsonify({
            "success": False,
            "error": f"System Error during Cloudinary check: {str(e)}"
        }), 500


def verify_fonts_available():
    """Verify that required fonts are available"""
    required_fonts = ["arial.ttf", "arialbd.ttf"]
    missing_fonts = []
  
    for font in required_fonts:
        font_path = os.path.join(FONTS_FOLDER, font)
        if not os.path.exists(font_path):
            missing_fonts.append(font)
  
    if missing_fonts:
        logger.warning(f"Missing fonts: {missing_fonts}")
        # You might want to download or create default fonts here
        return False
  
    # Test if fonts can be loaded
    for font in required_fonts:
        font_path = os.path.join(FONTS_FOLDER, font)
        try:
            ImageFont.truetype(font_path, 12)
        except Exception as e:
            logger.error(f"Font {font} cannot be loaded: {e}")
            return False
  
    logger.info("All required fonts are available and loadable")
    return True


def _format_bulk_generation_error(exc):
    """Convert low-level exceptions into clearer admin-facing bulk-job errors."""
    if exc is None:
        return "Unknown bulk generation error"

    if isinstance(exc, KeyError):
        missing = str(exc).strip("'\" ")
        return f"Excel column missing: '{missing}'"

    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__

    lowered = message.lower()
    if "name 'template' is not defined" in lowered or "name 'side' is not defined" in lowered:
        return "Bulk generation worker hit an internal layout error. Please retry after updating the server."
    if "cannot identify image file" in lowered:
        return "One of the uploaded photos is not a valid image file."
    if "template not found" in lowered:
        return "Selected template was not found."
    if "failed to load front template" in lowered:
        return message

    return message

    def _sort_key(item):
        for k in ("updated_at", "started_at", "created_at"):
            v = item.get(k)
            if isinstance(v, str) and v:
                return v
        return ""

    rows.sort(key=_sort_key, reverse=True)
    return rows[: max(1, int(limit or 100))]
def _apply_batch_rules_for_row(template_obj, row_data, custom_data):
    rules = (getattr(template_obj, "batch_rules", None) or {})
    if not isinstance(rules, dict):
        return row_data, custom_data

    # 1) auto-hide field if empty -> clear label/value visibility hints in custom_data metadata
    for field_name in rules.get("auto_hide_if_empty", []) or []:
        key = str(field_name or "").strip()
        if key and not str((row_data or {}).get(key, "")).strip():
            custom_data[f"__hide__{key}"] = True

    # 2) text color by class/section -> attach hint for renderer/editor compatibility
    class_rules = rules.get("text_color_by_class") or {}
    class_name = str((row_data or {}).get("class_name", "")).strip()
    if class_name and class_name in class_rules:
        custom_data["__value_color_override__"] = class_rules.get(class_name)

    # 3) switch layout by language
    lang_rules = rules.get("layout_by_language") or {}
    lang = str((row_data or {}).get("language", "")).strip().lower()
    if lang and lang in lang_rules:
        custom_data["__layout_profile__"] = lang_rules.get(lang)

    # 4) qr profile by template/school
    qr_profile = rules.get("qr_profile") or {}
    if qr_profile:
        custom_data["__qr_profile__"] = qr_profile

    return row_data, custom_data



# =========================================================
# BACKGROUND THREAD WORKER (Pure SQLAlchemy)
# =========================================================
def _apply_import_mapping_to_dataframe(df, mapping_json):
    if df is None or not isinstance(mapping_json, dict):
        return df
    rename_map = {}
    for target_field, source_header in mapping_json.items():
        target = str(target_field or "").strip().lower()
        source = str(source_header or "").strip().lower()
        if not target or not source:
            continue
        if source in df.columns and target not in df.columns:
            rename_map[source] = target
    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def background_bulk_generate(task_id, template_id, excel_path, photo_map, import_mapping_id=None):
    """
    Background thread to process bulk generation without blocking the server.
    Uses SQLAlchemy ORM for all database operations.
    
    For bulk jobs with > 10 students, uses parallel rendering via ThreadPoolExecutor
    to significantly speed up card generation on multi-core systems.
    """
    from app.services.parallel_render import (
        bulk_render_students,
        get_optimal_workers,
        render_cards_parallel_to_bytes,
    )
    with app.app_context():
        success_count = 0
        skipped_count = 0
        error_count = 0
        total_records = 0
        errors = []
        render_inputs = []
        try:
            _set_bulk_job_state(
                task_id,
                state='PROCESSING',
                status='Reading Excel file...',
                cancel_requested=False,
                started_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            import pandas as pd  # Lazy import — Avoids 340ms startup cost when bulk generation is not used
            if excel_path.endswith('.csv'):
                df = pd.read_csv(excel_path)
            else:
                df = pd.read_excel(excel_path, engine='openpyxl')

            df.columns = df.columns.str.strip().str.lower()
            if import_mapping_id:
                try:
                    mapping_row = db.session.get(ImportMapping, int(import_mapping_id))
                    if mapping_row and isinstance(mapping_row.mapping_json, dict):
                        df = _apply_import_mapping_to_dataframe(df, mapping_row.mapping_json)
                        logger.info("Applied import mapping %s during bulk generation", import_mapping_id)
                except Exception as mapping_exc:
                    logger.warning("Import mapping application failed (%s): %s", import_mapping_id, mapping_exc)
            required_columns = ["name"]
            missing_required = [col for col in required_columns if col not in df.columns]
            if missing_required:
                raise ValueError(
                    "Excel file is missing required column(s): " + ", ".join(missing_required)
                )

            total_records = len(df)
            _set_bulk_job_state(task_id, total=total_records)

            template_obj = db.session.get(Template, template_id)
            if not template_obj:
                raise ValueError("Template not found")

            template_path = get_template_path(template_id, side="front")
            back_template_path = get_template_path(template_id, side="back") if getattr(template_obj, "is_double_sided", False) else None
            card_width, card_height = get_card_size(template_id)
            template_school_name = getattr(template_obj, "school_name", "") or ""

            try:
                _load_template_image_for_render(template_path, card_width, card_height, render_scale=1.0)
            except Exception as e:
                logger.error(f"Error loading template {template_id} for bulk generation: {e}")
                raise RuntimeError(f"Failed to load front template - {e}")

            if back_template_path:
                try:
                    _load_template_image_for_render(back_template_path, card_width, card_height, render_scale=1.0)
                except Exception as e:
                    logger.warning(f"Bulk back template preload failed for template {template_id}: {e}")

            dynamic_fields = TemplateField.query.filter_by(template_id=template_id)\
                                        .order_by(TemplateField.display_order.asc())\
                                        .all()
            existing_hashes = {
                row[0]
                for row in db.session.query(Student.data_hash).filter_by(template_id=template_id).all()
                if row[0]
            }
            seen_hashes = set()
            photo_cache = {}
            pending_rows = []
            commit_batch_size = 25
            last_progress_update = 0.0

            # Parallel rendering: collect student data first, then render in parallel
            parallel_batch_size = 0
            parallel_students = []  # List of (idx, row_data_dict) for parallel rendering
            use_parallel = total_records > 10  # Use parallel for bulk jobs > 10 cards
            parallel_workers = get_optimal_workers(total_records) if use_parallel else 1

            if use_parallel:
                logger.info(f"Bulk generation: using parallel rendering with {parallel_workers} workers for {total_records} cards")
            def _cleanup_generated_paths(paths):
                if STORAGE_BACKEND != "local":
                    return
                for path in paths or []:
                    if path and os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception as cleanup_error:
                            logger.warning(f"Failed to cleanup bulk artifact {path}: {cleanup_error}")

            def _push_progress(current_index, *, force=False):
                nonlocal last_progress_update
                task_state = _get_bulk_job_state(task_id) or {}
                if bool(task_state.get("cancel_requested")):
                    raise RuntimeError("Bulk job cancelled by admin.")
                now = time.monotonic()
                if (
                    not force
                    and current_index not in (1, total_records)
                    and current_index % 5 != 0
                    and (now - last_progress_update) < 0.75
                ):
                    return
                _set_bulk_job_state(
                    task_id,
                    current=current_index,
                    status=f"Processing student {current_index} of {total_records}...",
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
                last_progress_update = now

            def _flush_pending_rows(force=False):
                nonlocal pending_rows, success_count, skipped_count, error_count
                if not pending_rows:
                    return
                if not force and len(pending_rows) < commit_batch_size:
                    return

                batch = pending_rows
                pending_rows = []

                def _commit_single(meta):
                    nonlocal success_count, skipped_count, error_count
                    student_obj = meta["student"]
                    db.session.add(student_obj)
                    try:
                        db.session.commit()
                        success_count += 1
                        seen_hashes.add(meta["data_hash"])
                        existing_hashes.add(meta["data_hash"])
                    except IntegrityError as row_error:
                        db.session.rollback()
                        _cleanup_generated_paths(meta.get("cleanup_paths"))
                        if "data_hash" in str(row_error).lower():
                            skipped_count += 1
                            seen_hashes.add(meta["data_hash"])
                            existing_hashes.add(meta["data_hash"])
                            errors.append(f"Row {meta['row_number']}: Duplicate student data skipped")
                        else:
                            error_count += 1
                            errors.append(f"Row {meta['row_number']}: Database error - {_format_bulk_generation_error(row_error)}")
                    except Exception as row_error:
                        db.session.rollback()
                        _cleanup_generated_paths(meta.get("cleanup_paths"))
                        error_count += 1
                        errors.append(f"Row {meta['row_number']}: {_format_bulk_generation_error(row_error)}")

                try:
                    db.session.add_all([meta["student"] for meta in batch])
                    db.session.commit()
                    success_count += len(batch)
                    for meta in batch:
                        seen_hashes.add(meta["data_hash"])
                        existing_hashes.add(meta["data_hash"])
                except Exception:
                    db.session.rollback()
                    for meta in batch:
                        _commit_single(meta)
                    _publish_bulk_job_errors(task_id, errors)

            for idx, row in df.iterrows():
                task_state = _get_bulk_job_state(task_id) or {}
                if bool(task_state.get("cancel_requested")):
                    raise RuntimeError("Bulk job cancelled by admin.")
                _push_progress(idx + 1)

                try:
                    if row.isnull().all():
                        continue

                    name = str(row.get('name', '')).strip() if pd.notna(row.get('name')) else ''
                    if not name:
                        continue

                    father_name = str(row.get('father_name', '')).strip() if pd.notna(row.get('father_name')) else ''
                    class_name = str(row.get('class_name', '')).strip() if pd.notna(row.get('class_name')) else ''
                    dob = str(row.get('dob', '')).strip() if pd.notna(row.get('dob')) else ''
                    address = str(row.get('address', '')).strip() if pd.notna(row.get('address')) else ''
                    phone = str(row.get('phone', '')).strip() if pd.notna(row.get('phone')) else ''

                    custom_data = {}
                    field_error = False
                    for field in dynamic_fields:
                        col_name = field.field_name.lower()
                        val = str(row[col_name]).strip() if col_name in df.columns and pd.notna(row.get(col_name)) else ''
                        if field.is_required and not val:
                            errors.append(f"Row {idx+2}: Missing required field '{field.field_label}'")
                            field_error = True
                            break
                        custom_data[field.field_name] = val

                    if field_error:
                        error_count += 1
                        _publish_bulk_job_errors(task_id, errors)
                        continue

                    row_data_for_rules = {
                        "name": name,
                        "father_name": father_name,
                        "class_name": class_name,
                        "dob": dob,
                        "address": address,
                        "phone": phone,
                        "language": (template_obj.language or "english"),
                    }
                    row_data_for_rules, custom_data = _apply_batch_rules_for_row(template_obj, row_data_for_rules, custom_data)

                    used_photo = "placeholder.jpg"
                    
                    # Generate match candidates to handle name, name + father name, roll no + name etc.
                    match_candidates = []
                    
                    # 1. Base name
                    clean_name = name.strip()
                    if clean_name:
                        match_candidates.append(clean_name)
                        
                        # 2. Name + Father Name
                        clean_father = father_name.strip()
                        if clean_father:
                            match_candidates.append(f"{clean_name} {clean_father}")
                            match_candidates.append(f"{clean_father} {clean_name}")
                            match_candidates.append(f"{clean_name}_{clean_father}")
                            match_candidates.append(f"{clean_father}_{clean_name}")
                            
                        # 3. Roll number or ID combined with Name
                        for col in row.index:
                            col_lower = str(col).lower().strip()
                            if col_lower in ['roll_no', 'rollno', 'roll', 'admission_no', 'admissionno', 'id', 'reg_no', 'regno', 'student_id', 'studentid']:
                                val = str(row[col]).strip()
                                # Strip trailing decimals if pandas parsed integer as float
                                if val.endswith('.0'):
                                    val = val[:-2]
                                if val:
                                    match_candidates.append(f"{val} {clean_name}")
                                    match_candidates.append(f"{clean_name} {val}")
                                    match_candidates.append(f"{val}_{clean_name}")
                                    match_candidates.append(f"{clean_name}_{val}")

                    # Attempt matching against photo_map aliases
                    matched_photo = None
                    for cand in match_candidates:
                        for alias in photo_match_aliases(cand):
                            if alias in photo_map:
                                matched_photo = photo_map[alias]
                                break
                        if matched_photo:
                            break
                            
                    if matched_photo:
                        used_photo = matched_photo
                    else:
                        # Fallback to checking explicit photo columns in the Excel row
                        for col in ['photo_filename', 'photo_path', 'photo']:
                            if col not in df.columns or pd.isna(row.get(col)):
                                continue
                            ref = str(row[col]).strip()
                            for alias in photo_match_aliases(ref):
                                if alias in photo_map:
                                    used_photo = photo_map[alias]
                                    break
                            if used_photo != "placeholder.jpg":
                                break

                    form_data = {
                        'name': name,
                        'father_name': father_name,
                        'class_name': class_name,
                        'dob': dob,
                        'address': address,
                        'phone': phone,
                        'template_id': template_id,
                    }
                    data_hash = generate_data_hash(form_data, used_photo)
                    if data_hash in seen_hashes or data_hash in existing_hashes:
                        skipped_count += 1
                        seen_hashes.add(data_hash)
                        continue

                    school_name = template_school_name

                    render_inputs.append({
                        'name': name,
                        'father_name': father_name,
                        'class_name': class_name,
                        'dob': dob,
                        'address': address,
                        'phone': phone,
                        'photo_url': used_photo if str(used_photo or "").startswith("http") else None,
                        'photo_filename': used_photo if used_photo and not str(used_photo).startswith("http") and used_photo != "placeholder.jpg" else None,
                        'custom_data': custom_data,
                        'school_name': school_name,
                        '_template_fields': dynamic_fields,
                        '_prepared_photo_cache': photo_cache,
                        'row_idx': idx,
                        'data_hash': data_hash,
                    })

                    is_last_row = (idx == len(df) - 1)
                    if len(render_inputs) >= 8 or is_last_row:
                        batch_workers = get_optimal_workers(len(render_inputs))
                        batch_results = bulk_render_students(
                            app, template_obj, render_inputs, max_workers=batch_workers,
                        )
                        for r in batch_results:
                            r_data = r.get('student_data') or {}
                            r_row_idx = r_data.get('row_idx', idx)
                            r_hash = r_data.get('data_hash') or data_hash
                            if not r.get('success'):
                                error_count += 1
                                errors.append(
                                    f"Row {r_row_idx + 2}: {r.get('error') or 'render failed'}"
                                )
                                _publish_bulk_job_errors(task_id, errors)
                                continue
                            front_image = r.get('front_image')
                            back_image = r.get('back_image')
                            front_rgb = _flatten_to_rgb(front_image)
                            back_rgb = _flatten_to_rgb(back_image) if back_image is not None else None

                            image_url = None
                            back_image_url = None
                            generated_filename = None
                            back_generated_filename = None
                            cleanup_paths = []

                            ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
                            base = f"card_{template_id}_{ts}_{uuid.uuid4().hex}"

                            if STORAGE_BACKEND == "local":
                                os.makedirs(GENERATED_FOLDER, exist_ok=True)
                                jpg_name = f"{base}.jpg"
                                jpg_path = os.path.join(GENERATED_FOLDER, jpg_name)
                                front_rgb.save(jpg_path, format="JPEG", quality=95)
                                cleanup_paths.append(jpg_path)
                                generated_filename = jpg_name

                                if back_rgb is not None:
                                    back_jpg_name = f"{base}_back.jpg"
                                    back_jpg_path = os.path.join(GENERATED_FOLDER, back_jpg_name)
                                    back_rgb.save(back_jpg_path, format="JPEG", quality=95)
                                    cleanup_paths.append(back_jpg_path)
                                    back_generated_filename = back_jpg_name
                            else:
                                jpg_buffer = io.BytesIO()
                                front_rgb.save(jpg_buffer, format="JPEG", quality=95)
                                jpg_buffer.seek(0)
                                image_url = upload_image(jpg_buffer.getvalue(), folder='cards', resource_type='image')
                                if back_rgb is not None:
                                    back_jpg_buffer = io.BytesIO()
                                    back_rgb.save(back_jpg_buffer, format="JPEG", quality=95)
                                    back_jpg_buffer.seek(0)
                                    back_image_url = upload_image(back_jpg_buffer.getvalue(), folder='cards', resource_type='image')

                            used_photo_r = (
                                r_data.get('photo_filename')
                                or r_data.get('photo_url')
                                or "placeholder.jpg"
                            )
                            student = Student(
                                name=r_data.get('name', name),
                                father_name=r_data.get('father_name', father_name),
                                class_name=r_data.get('class_name', class_name),
                                dob=r_data.get('dob', dob),
                                address=r_data.get('address', address),
                                phone=r_data.get('phone', phone),
                                photo_url=None if STORAGE_BACKEND == "local" else (r_data.get('photo_url') if str(r_data.get('photo_url') or "").startswith("http") else None),
                                photo_filename=used_photo_r if STORAGE_BACKEND == "local" else (r_data.get('photo_filename') if r_data.get('photo_filename') and used_photo_r != "placeholder.jpg" else None),
                                image_url=None if STORAGE_BACKEND == "local" else image_url,
                                back_image_url=None if STORAGE_BACKEND == "local" else back_image_url,
                                pdf_url=None,
                                generated_filename=generated_filename if STORAGE_BACKEND == "local" else None,
                                back_generated_filename=back_generated_filename if STORAGE_BACKEND == "local" else None,
                                created_at=datetime.now(timezone.utc),
                                data_hash=r_hash,
                                template_id=template_id,
                                school_name=school_name,
                                custom_data=r_data.get('custom_data', custom_data),
                            )
                            pending_rows.append({
                                "student": student,
                                "data_hash": r_hash,
                                "row_number": r_row_idx + 2,
                                "cleanup_paths": cleanup_paths,
                            })
                            _flush_pending_rows(force=False)
                        render_inputs = []

                except Exception as row_e:
                    db.session.rollback()
                    errors.append(f"Row {idx+2}: {_format_bulk_generation_error(row_e)}")
                    error_count += 1
                    _publish_bulk_job_errors(task_id, errors)

            _flush_pending_rows(force=True)

            summary = f"Processed {total_records}. Created: {success_count}, Skipped: {skipped_count}, Errors: {error_count}"
            _publish_bulk_job_errors(task_id, errors)
            if success_count == 0 and error_count > 0:
                _set_bulk_job_state(
                    task_id,
                    state='FAILURE',
                    status='No ID cards were generated. See error details below.',
                    result=summary,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            elif success_count == 0 and skipped_count > 0 and error_count == 0:
                _set_bulk_job_state(
                    task_id,
                    state='SUCCESS',
                    status='Completed with no new cards created.',
                    result=summary,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            elif error_count > 0:
                _set_bulk_job_state(
                    task_id,
                    state='SUCCESS',
                    status='Completed with some row errors.',
                    result=summary,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                _set_bulk_job_state(
                    task_id,
                    state='SUCCESS',
                    status='Completed',
                    result=summary,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            
        except Exception as e:
            formatted_error = _format_bulk_generation_error(e)
            logger.error(f"Bulk job failed: {formatted_error}")
            import traceback
            traceback.print_exc()
            errors.insert(0, formatted_error)
            _publish_bulk_job_errors(task_id, errors)
            _set_bulk_job_state(
                task_id,
                state='FAILURE',
                status=f"System Error: {formatted_error}",
                result=f"Processed {total_records}. Created: {success_count}, Skipped: {skipped_count}, Errors: {max(error_count, 1)}",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            try:
                if excel_path and os.path.exists(excel_path):
                    os.remove(excel_path)
            except Exception as cleanup_error:
                logger.warning(f"Failed to remove bulk temp file {excel_path}: {cleanup_error}")

            try:
                summary = f"Created: {success_count}, Skipped: {skipped_count}, Errors: {error_count}"
                send_email(os.environ.get("ADMIN_EMAIL"), "Bulk Generation Complete", summary)
            except Exception as email_error:
                logger.warning(f"Bulk generation completion email failed: {email_error}")

# =========================================================
# ROUTE TO TRIGGER THE BACKGROUND THREAD
# =========================================================
@app.route("/bulk_generate", methods=["POST"])
def bulk_generate():
    if not session.get("admin"):
        return jsonify({"success": False, "error": "Unauthorized", "errors": ["Unauthorized"]}), 403

    # Only super admin can generate cards
    if session.get("admin_role") != "super_admin":
        return jsonify({"success": False, "error": "Only super admin can generate cards.", "errors": ["Only super admin can generate cards."]}), 403

    template_id_raw = request.form.get("template_id")
    if not template_id_raw:
        return jsonify({"success": False, "error": "No template selected", "errors": ["No template selected"]}), 400
    
    template_id = int(template_id_raw)
    import_mapping_id_raw = (request.form.get("import_mapping_id") or "").strip()
    import_mapping_id = None
    if import_mapping_id_raw:
        try:
            import_mapping_id = int(import_mapping_id_raw)
        except Exception:
            return jsonify({"success": False, "error": "Invalid import mapping selected", "errors": ["Invalid import mapping selected"]}), 400
        mapping_row = db.session.get(ImportMapping, import_mapping_id)
        if not mapping_row or int(mapping_row.template_id or 0) != template_id:
            return jsonify({"success": False, "error": "Import mapping does not belong to selected template", "errors": ["Import mapping does not belong to selected template"]}), 400
    
    template_obj = db.session.get(Template, template_id)
    if not template_obj:
        return jsonify({"success": False, "error": "Template not found", "errors": ["Template not found"]}), 404
    try:
        from app.services.premium_service import run_design_qa
        qa_settings = (getattr(template_obj, "qa_settings", None) or {})
        if bool(qa_settings.get("enforce_before_bulk_generate")):
            qa_result = run_design_qa(template_obj)
            if not bool(qa_result.get("ok")):
                return jsonify({
                    "success": False,
                    "error": "Design QA failed. Fix template issues before bulk generate.",
                    "qa": qa_result,
                    "errors": ["Design QA failed. Fix template issues before bulk generate."]
                }), 400
    except Exception as qa_exc:
        logger.warning(f"Bulk generate QA gate skipped due to error: {qa_exc}")

    if 'excel_file' not in request.files:
        return jsonify({"success": False, "error": "No Excel file uploaded", "errors": ["No Excel file uploaded"]}), 400
        
    excel_file = request.files['excel_file']
    if excel_file.filename == '':
        return jsonify({"success": False, "error": "No file selected", "errors": ["No file selected"]}), 400

    try:
        # 1. Save Excel File
        filename = secure_filename(excel_file.filename)
        excel_path = os.path.join(app.root_path, UPLOAD_FOLDER, f"temp_{uuid.uuid4().hex}_{filename}")
        excel_file.save(excel_path)

        # 2. Process & Save Photos to Cloudinary
        photo_map = {}  # Maps name → Cloudinary URL
        if 'bulk_photos' in request.files:
            photos = request.files.getlist('bulk_photos')
            _, photo_settings, _, _ = get_template_settings(template_id)  # Fixed: renamed p_settings → photo_settings
            
            for p in photos:
                if p and p.filename:
                    original_name = secure_filename(p.filename)
                    if not original_name: 
                        continue
                    
                    ts = datetime.now().strftime("%Y%m%d%H%M%S")

                    try:
                        photo_bytes = _prepare_uploaded_student_photo_bytes(p, photo_settings)
                    except ValueError as e:
                        logger.warning(f"Skipping bulk photo {original_name}: {e}")
                        continue
                    except Exception as e:
                        logger.warning(f"Failed to process photo {original_name}: {e}")
                        continue
                    
                    if STORAGE_BACKEND == "local":
                        try:
                            stored_name = f"{ts}_{uuid.uuid4().hex}_{original_name}"
                            local_path = os.path.join(UPLOAD_FOLDER, stored_name)
                            _write_binary_file_atomic(local_path, photo_bytes)
                            for alias in photo_match_aliases(original_name):
                                photo_map.setdefault(alias, stored_name)
                        except Exception as e:
                            logger.warning(f"Failed to save bulk photo {original_name} locally: {e}")
                    else:
                        # Upload to Cloudinary
                        try:
                            cloud_url = upload_image(photo_bytes, folder='bulk-photos')
                            for alias in photo_match_aliases(original_name):
                                photo_map.setdefault(alias, cloud_url)
                        except Exception as e:
                            logger.warning(f"Failed to upload photo {original_name} to Cloudinary: {e}")

        # 3. Start background generation. Prefer RQ when Railway Redis is
        # available, otherwise fall back to the local executor.
        task_id = uuid.uuid4().hex
        _set_bulk_job_state(
            task_id,
            state='PENDING',
            current=0,
            total=0,
            status='Queued',
            errors=[],
            error_count=0,
            template_id=template_id,
            import_mapping_id=import_mapping_id,
            excel_filename=filename,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        queue = get_task_queue()
        use_rq = False
        if queue is not None:
            try:
                from rq import Worker
                workers = Worker.all(queue=queue)
                if len(workers) > 0:
                    use_rq = True
                else:
                    logger.warning("Redis is running, but no RQ worker is active on 'id_card_bulk' queue. Falling back to local executor.")
            except Exception as e:
                logger.warning("Failed to check active RQ workers: %s", e)

        if use_rq:
            try:
                job = queue.enqueue(
                    background_bulk_generate,
                    args=(task_id, template_id, excel_path, photo_map, import_mapping_id),
                    job_id=task_id,
                    job_timeout='1h',
                )
                task_id = job.get_id()
            except Exception as queue_error:
                logger.warning("RQ enqueue failed; using local executor: %s", queue_error)
                executor.submit(background_bulk_generate, task_id, template_id, excel_path, photo_map, import_mapping_id)
        else:
            logger.warning("Redis/RQ unavailable or no active workers; using local executor for bulk generation.")
            executor.submit(background_bulk_generate, task_id, template_id, excel_path, photo_map, import_mapping_id)

        # Log Activity
        log_activity("Bulk Generation Started", 
                     target=f"Template ID: {template_id}", 
                     details=f"Task ID: {task_id}, Excel: {filename}, Photos: {len(photo_map)}, Mapping: {import_mapping_id or 'none'}")

        return jsonify({"success": True, "task_id": task_id})

    except Exception as e:
        formatted_error = _format_bulk_generation_error(e)
        logger.error(f"Bulk gen trigger error: {formatted_error}")
        log_activity("Bulk Generation Failed", 
                     target=f"Template ID: {template_id}", 
                     details=f"Error: {formatted_error}")
        
        return jsonify({
            "success": False,
            "error": formatted_error,
            "errors": [formatted_error],
        }), 500
    
# taskstatus route moved to api_routes.py

@app.route("/admin/preview_bulk_template/<int:template_id>", methods=["GET"])
def preview_bulk_template(template_id):
    """Preview template with dynamic fields for bulk upload"""
    if not session.get("admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    template = db.session.get(Template, template_id)


    if not template:
        return jsonify({"success": False, "error": "Template not found"}), 404
    
    is_super_admin = session.get("admin_role") != "school_admin"
    if not is_super_admin and template.school_name != session.get("admin_school"):
        return jsonify({"success": False, "error": "You can only access templates for your assigned school."}), 403

    # Get dynamic fields - FIXED
    dynamic_fields = TemplateField.query.filter_by(
        template_id=template_id
    ).order_by(TemplateField.display_order.asc()).all()
    
    # Create CSV template content
    import csv
    import io
    
    # Define columns
    columns = [
        'name',
        'father_name', 
        'class_name',
        'dob',
        'address',
        'phone',
        'photo_path'
    ]
    
    # Add dynamic field columns
    field_info = []
    for field in dynamic_fields:
        columns.append(field.field_name)
        field_info.append({
            'name': field.field_name,
            'label': field.field_label,
            'required': field.is_required,
            'type': field.field_type,
            'show_label_front': bool(getattr(field, 'show_label_front', True)),
            'show_value_front': bool(getattr(field, 'show_value_front', True)),
            'show_label_back': bool(getattr(field, 'show_label_back', False)),
            'show_value_back': bool(getattr(field, 'show_value_back', False)),
        })
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(columns)
    
    # Write sample data
    writer.writerow([
        'John Doe',               # name
        'Robert Doe',             # father_name
        '10-A',                   # class_name
        '2005-01-15',             # dob
        '123 Main Street',        # address
        '9876543210',             # phone
        'photos/john.jpg',        # photo_path
    ] + ['Sample Data' for _ in dynamic_fields])  # Dynamic fields
    
    writer.writerow([
        'Jane Smith',
        'William Smith',
        '11-B',
        '2004-05-20',
        '456 Oak Avenue',
        '9876543211',
        'photos/jane.jpg',
    ] + ['Another Sample' for _ in dynamic_fields])
    
    csv_content = output.getvalue()
    
    return jsonify({
        "success": True,
        "template_name": template.school_name,
        "columns": columns,
        "field_info": field_info,
        "csv_template": csv_content,
        "download_filename": f"{template.school_name}_template.csv"
    })

@app.route("/admin/bulk_upload_guide")
def bulk_upload_guide():
    """Bulk upload guide page"""
    if not session.get("admin"):
        return redirect(url_for('auth.login'))
    
    templates = Template.query.all()
    return render_template("bulk_upload_guide.html", templates=templates)

# ================== CORRECTED FORM FIELD ROUTES ==================

# ================== CORRECTED: Allow Students to View Fields ==================
# manage_template_fields moved to api_routes.py
    
# manage_single_field moved to api_routes.py
# ================== Versatile Verification Route ==================
# verify_student moved to api_routes.py
# download_school_excel moved to dashboard_routes.py
# delete_school_sheets moved to dashboard_routes.py
def cleanup_old_files(days=30):
    """
    Deletes files in Uploads and Generated folders that are older than 'days'.
    Returns the count of deleted files.
    """
    try:
        now = time.time()  # This line caused the error before import
        cutoff = now - (days * 86400) # 86400 seconds in a day
        
        deleted_count = 0
        
        # Define folders to clean
        folders_to_clean = [UPLOAD_FOLDER, GENERATED_FOLDER]
        
        for folder in folders_to_clean:
            if not os.path.exists(folder):
                continue
                
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                
                # Skip important files and directories
                if filename == "placeholder.jpg" or filename.startswith(".") or os.path.isdir(file_path): 
                    continue
                
                # Check if file is older than cutoff
                if os.path.isfile(file_path):
                    try:
                        file_time = os.path.getmtime(file_path)
                        if file_time < cutoff:
                            os.remove(file_path)
                            deleted_count += 1
                    except Exception as e:
                        logger.error(f"Error deleting {filename}: {e}")
                        
        logger.info(f"Cleanup finished. Removed {deleted_count} files older than {days} days.")
        return deleted_count
    except Exception as e:
        logger.error(f"Error in cleanup function: {e}")
        return 0

# run_cleanup moved to dashboard_routes.py
# delete_all_students_by_template moved to dashboard_routes.py
with app.app_context():
    init_db()
    migrate_database()
    migrate_template_font_colors()
    verify_fonts_available() # Add this line
    migrate_photo_settings()
    repair_student_photo_url_recursion()
    from app.observability import verify_startup_dependencies
    verify_startup_dependencies(app)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    try:
        app.run(host="0.0.0.0", port=port, debug=False)
    except OSError as e:
        logger.error(f"Failed to start server: {e}")
        if "Address already in use" in str(e):
            logger.error(f"Port {port} is already in use. Stop the other process or use a different port.")
        raise
# Refactored module imports — these provide cleaner organizational structure.
# The original function definitions above shadow these imports at runtime,
# preserving exact backward-compatible behavior.
# To activate: uncomment the imports below.
#
# from app.auth_decorators import (
#     exempt_admins, login_required, admin_required,
#     super_admin_required, school_admin_required, student_required,
# )
#
# from app.error_handlers import (
#     handle_rate_limit_error, handle_bad_request_error,
#     handle_payload_too_large, handle_unprocessable_entity,
#     not_found_error, internal_error, add_security_headers, rgb_to_hex,
# )
#
# from app.field_layout import (
#     resolve_field_layout, get_template_field_side_flags,
#     resolve_field_layout_for_side, get_initial_flow_y_for_side,
#     get_template_language_direction_from_obj,
#     apply_layout_custom_objects_pil,
# )
#
# from app.db_migrations import (
#     init_db, check_deadline_passed, log_activity,
#     _quote_db_identifier, _run_schema_ddl, _get_table_column_names,
#     _add_column_if_missing, sync_model_columns_to_database,
#     migrate_database, migrate_template_font_colors, migrate_photo_settings,
#     repair_student_photo_url_recursion,
# )
#
# from app.template_ops import (
#     resolve_student_card_preview_urls, load_static_back_template_image,
#     add_template, add_template_cloudinary, get_templates,
#     update_template_settings,
# )
