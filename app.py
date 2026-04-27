from dotenv import load_dotenv
load_dotenv()  # Load environment variables BEFORE any other imports
import socket  # <--- Make sure this is imported at the top
from flask import Flask, render_template, request, url_for, Response, redirect, session, send_file, jsonify, flash, Blueprint
from PIL import Image, ImageDraw, ImageFont,ImageOps
import os
import json
from flask_login import current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime,timezone, UTC
import hashlib
import logging
import re
import smtplib
from urllib.parse import urlparse

import requests
from email.mime.text import MIMEText
import random
import string
from collections import defaultdict
from functools import lru_cache
import textwrap
import pandas as pd
import tempfile
import glob
import io
from io import BytesIO
import base64
import uuid
from concurrent.futures import ThreadPoolExecutor
import time
from flask_wtf.csrf import CSRFProtect 
from reportlab.pdfgen import canvas
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, get_current_job
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
from corel_routes import corel_bp
from editor_routes import editor_bp
from utils import (
    UPLOAD_FOLDER, GENERATED_FOLDER, FONTS_FOLDER, PLACEHOLDER_PATH,TEMPLATES_CONFIG, 
    DUPLICATE_CONFIG_PATH,A4_WIDTH_PX, A4_HEIGHT_PX,A4_LANDSCAPE_WIDTH_PX, A4_LANDSCAPE_HEIGHT_PX,DPI,
    STATIC_DIR,
    get_template_settings, get_template_path, get_card_size, apply_text_case,
    get_default_font_config, get_default_photo_config, get_default_qr_config,
    get_photo_settings_for_orientation, get_font_settings_for_orientation,
    get_template_orientation, load_template, load_template_smart, round_photo, is_valid_font_file,
    get_available_fonts, load_font_dynamic, generate_qr_code, generate_barcode_code128, generate_data_hash,process_text_for_drawing
    ,flip_x_for_text_direction, get_draw_text_kwargs, trim_transparent_edges, force_rgb,
    get_cloudinary_face_crop_url, get_storage_backend, parse_layout_config, get_field_layout_item,
    split_label_and_colon, colon_anchor_for_value, get_template_language_direction,
    get_template_layout_config, get_anchor_max_text_width, get_layout_flow_start_y
)
from cloudinary_config import upload_image
from models import db, Student, Template, TemplateField, ActivityLog, NotificationPreference, NotificationLog, KeyboardLanguagePreference, AdminUser
from notifications import (
    notify_deadline_approaching, notify_card_ready, notify_generation_error,
    check_and_notify_approaching_deadlines
)
from keyboard_switcher import (
    get_keyboard_config, generate_keyboard_switcher_script, 
    generate_keyboard_control_html, get_all_keyboard_languages
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
# Initialize Thread Executor (limit workers to prevent memory overload)
max_workers = min(32, os.cpu_count() * 2)
executor = ThreadPoolExecutor(max_workers=max_workers)
# In-memory dictionary to track job progress
# Structure: { 'task_id': { 'state': 'PENDING', 'current': 0, 'total': 0, 'status': '' } }
jobs = {}

import logging
from logging.handlers import RotatingFileHandler
import warnings
import time
from types import SimpleNamespace
warnings.filterwarnings("ignore", message="SymbolDatabase.GetPrototype() is deprecated")
logger = logging.getLogger(__name__)

REDIS_URL = (os.environ.get("REDIS_URL") or "").strip()
REDIS_PUBLIC_URL = (os.environ.get("REDIS_PUBLIC_URL") or "").strip()
REDIS_CACHE_TTL = int(os.environ.get("REDIS_CACHE_TTL", "86400"))
REDIS_CONNECT_TIMEOUT = float(os.environ.get("REDIS_CONNECT_TIMEOUT", "2"))
REDIS_SOCKET_TIMEOUT = float(os.environ.get("REDIS_SOCKET_TIMEOUT", "2"))
REDIS_RETRY_SECONDS = int(os.environ.get("REDIS_RETRY_SECONDS", "30"))
redis_client = None
task_queue = None
_redis_last_error_at = 0
_redis_warned_missing_url = False
_active_redis_url = None


def _redis_url_hostname(redis_url=None):
    try:
        return urlparse(redis_url or REDIS_URL).hostname or ""
    except Exception:
        return ""


def _redis_connection_hint(redis_url=None):
    hostname = _redis_url_hostname(redis_url)
    if hostname.endswith(".railway.internal"):
        extra = ""
        if REDIS_PUBLIC_URL:
            extra = " Falling back to REDIS_PUBLIC_URL when available."
        return (
            " Railway private Redis hosts are reachable only from Railway services "
            "in the same project/environment. Use REDIS_PUBLIC_URL for local tests."
            + extra
        )
    return ""


def _redis_candidate_urls():
    candidates = []
    for value in (REDIS_URL, REDIS_PUBLIC_URL):
        value = (value or "").strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def get_redis_client():
    """
    Lazily connect to Redis using Railway's REDIS_URL.

    Redis is optional: if the service is not attached, down, or temporarily
    unreachable, the app skips caching/queueing and continues rendering.
    """
    global redis_client, _redis_last_error_at, _redis_warned_missing_url, _active_redis_url

    candidates = _redis_candidate_urls()
    if not candidates:
        if not _redis_warned_missing_url:
            logger.warning("REDIS_URL / REDIS_PUBLIC_URL are not set; Redis cache and RQ queue are disabled.")
            _redis_warned_missing_url = True
        return None

    now = time.time()
    if redis_client is None and _redis_last_error_at and now - _redis_last_error_at < REDIS_RETRY_SECONDS:
        return None

    if redis_client is None:
        failures = []
        for redis_url in candidates:
            try:
                candidate = Redis.from_url(
                    redis_url,
                    decode_responses=False,
                    socket_connect_timeout=REDIS_CONNECT_TIMEOUT,
                    socket_timeout=REDIS_SOCKET_TIMEOUT,
                    health_check_interval=30,
                    retry_on_timeout=True,
                )
                candidate.ping()
                redis_client = candidate
                _active_redis_url = redis_url
                if redis_url == REDIS_URL:
                    logger.info("Connected to Redis using REDIS_URL.")
                else:
                    for label, failure_exc, failure_hint in failures:
                        logger.info(
                            "Redis connection attempt failed for %s but fallback recovered: %s%s",
                            label,
                            failure_exc,
                            failure_hint,
                        )
                    logger.info("Connected to Redis using REDIS_PUBLIC_URL fallback.")
                break
            except Exception as exc:
                failures.append((
                    "REDIS_URL" if redis_url == REDIS_URL else "REDIS_PUBLIC_URL",
                    exc,
                    _redis_connection_hint(redis_url),
                ))
        if redis_client is None:
            _redis_last_error_at = now
            _active_redis_url = None
            for label, failure_exc, failure_hint in failures:
                logger.warning(
                    "Redis connection attempt failed for %s: %s%s",
                    label,
                    failure_exc,
                    failure_hint,
                )
            logger.warning("Redis unavailable; continuing without Redis cache/queue.")

    return redis_client


def _mark_redis_unavailable(exc):
    global redis_client, task_queue, _redis_last_error_at, _active_redis_url
    _redis_last_error_at = time.time()
    redis_client = None
    task_queue = None
    _active_redis_url = None
    logger.warning("Redis operation failed; continuing without Redis temporarily: %s", exc)


def get_task_queue():
    global task_queue
    client = get_redis_client()
    if client is None:
        return None
    if task_queue is None:
        task_queue = Queue("id_card_bulk", connection=client)
    return task_queue


def _redis_cache_key(*parts):
    normalized = []
    for part in parts:
        if part is None:
            normalized.append("none")
            continue
        if isinstance(part, bytes):
            normalized.append(hashlib.sha256(part).hexdigest())
            continue
        text = str(part)
        if len(text) > 256:
            text = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
        normalized.append(re.sub(r"[^0-9A-Za-z_.-]", "_", text))
    return "id_card:" + ":".join(normalized)


def _redis_get(key):
    client = get_redis_client()
    if client is None:
        return None
    try:
        return client.get(key)
    except RedisError as exc:
        logger.warning("Redis cache read failed for %s: %s", key, exc)
        _mark_redis_unavailable(exc)
        return None


def _redis_set(key, value, ttl=REDIS_CACHE_TTL):
    client = get_redis_client()
    if client is None:
        return False
    try:
        return bool(client.set(key, value, ex=ttl))
    except RedisError as exc:
        logger.warning("Redis cache write failed for %s: %s", key, exc)
        _mark_redis_unavailable(exc)
        return False


def _redis_delete(key):
    client = get_redis_client()
    if client is None:
        return False
    try:
        client.delete(key)
        return True
    except RedisError as exc:
        _mark_redis_unavailable(exc)
        return False


def _redis_acquire_lock(lock_key, ttl=5):
    client = get_redis_client()
    if client is None:
        return True
    try:
        return bool(client.set(lock_key, b"1", nx=True, ex=ttl))
    except RedisError as exc:
        _mark_redis_unavailable(exc)
        return True


def fit_loaded_font_to_single_line(draw, font_loader, display_text, max_width, start_size, language="english", min_size=6):
    """Shrink a font until the text fits on one line; wrapping is reserved for address fields."""
    display_text = str(display_text or "")
    try:
        safe_width = max(1, int(float(max_width)))
    except Exception:
        safe_width = 1
    try:
        size = max(int(float(start_size or min_size)), int(min_size))
    except Exception:
        size = int(min_size)
    min_size = max(1, int(min_size))

    last_font = None
    while size >= min_size:
        font = font_loader(size)
        last_font = font
        try:
            text_len = draw.textlength(display_text, font=font, **get_draw_text_kwargs(display_text, language))
            if text_len <= safe_width:
                return font, int(getattr(font, "size", size) or size)
        except Exception:
            return font, int(getattr(font, "size", size) or size)
        size -= 1

    if last_font is None:
        last_font = font_loader(min_size)
    return last_font, int(getattr(last_font, "size", min_size) or min_size)


def fit_dynamic_font_to_single_line(draw, font_path, display_text, max_width, start_size, language="english", min_size=6):
    return fit_loaded_font_to_single_line(
        draw,
        lambda size: load_font_dynamic(font_path, display_text or "X", 10**9, size, language=language),
        display_text,
        max_width,
        start_size,
        language=language,
        min_size=min_size,
    )


def _get_cached_photo(student_like, photo_settings, photo_w, photo_h):
    photo_ref = _build_student_image_ref(student_like)

    cache_key = _redis_cache_key(
        "photo",
        photo_ref,
        photo_w,
        photo_h,
        json.dumps(photo_settings, sort_keys=True)
    )

    # 🔍 Try cache
    cached = _redis_get(cache_key)
    if cached:
        try:
            img = Image.open(io.BytesIO(cached))
            img.load()
            return img.convert("RGB")
        except Exception as e:
            logger.warning(f"Photo cache decode failed for {cache_key}: {e}")
            _redis_delete(cache_key)

    # 🚫 Stampede protection
    lock_key = cache_key + ":lock"
    if not _redis_acquire_lock(lock_key, ttl=5):
        time.sleep(0.05)
        cached = _redis_get(cache_key)
        if cached:
            try:
                img = Image.open(io.BytesIO(cached))
                img.load()
                return img.convert("RGB")
            except Exception:
                pass

    try:
        # 🧠 Generate fresh
        img = _load_card_photo_image(student_like, photo_settings, photo_w, photo_h)

        if img:
            try:
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=85, method=6)
                _redis_set(cache_key, buf.getvalue())
            except Exception as e:
                logger.warning(f"Photo cache write failed: {e}")

        return img

    finally:
        _redis_delete(lock_key)


def _get_cached_final_card(
    template_obj,
    student_like,
    side,
    student_id,
    school_name,
    render_scale,
    include_photo=True,
    include_qr=True,
    include_barcode=True,
    include_text=True,
):
    cache_key = _redis_cache_key(
        "final_card",
        template_obj.id,
        str(getattr(template_obj, "updated_at", "no_update")),
        side,
        student_id,
        _build_qr_hash(student_like),
        render_scale,
        include_photo,
        include_qr,
        include_barcode,
        include_text
    )

    # 🔍 Try cache
    cached = _redis_get(cache_key)
    if cached:
        try:
            img = Image.open(io.BytesIO(cached))
            img.load()
            return img.convert("RGB")
        except Exception as e:
            logger.warning(f"Final cache decode failed for {cache_key}: {e}")
            _redis_delete(cache_key)

    # 🚫 Stampede protection
    lock_key = cache_key + ":lock"
    if not _redis_acquire_lock(lock_key, ttl=5):
        time.sleep(0.05)
        cached = _redis_get(cache_key)
        if cached:
            try:
                img = Image.open(io.BytesIO(cached))
                img.load()
                return img.convert("RGB")
            except Exception:
                pass

    try:
        # 🧠 Generate fresh
        img = render_student_card_side(
            template_obj,
            student_like,
            side=side,
            student_id=student_id,
            school_name=school_name,
            render_scale=render_scale,
            include_photo=include_photo,
            include_qr=include_qr,
            include_barcode=include_barcode,
            include_text=include_text
        )

        if img:
            try:
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=85, method=6)
                _redis_set(cache_key, buf.getvalue())
            except Exception as e:
                logger.warning(f"Final card cache write failed: {e}")

        return img

    finally:
        _redis_delete(lock_key)

# Storage mode:
# - local: store templates/photos/cards under `static/` (best for local development)
# - cloudinary: store on Cloudinary (best for deployed environments)
# - auto (default): Cloudinary if credentials exist, else local
STORAGE_BACKEND = get_storage_backend()


def _get_cached_media_image(key_prefix, buffer_bytes, generate_fn):
    cache_key = _redis_cache_key(key_prefix, buffer_bytes)
    cached = _redis_get(cache_key)
    if cached is not None:
        try:
            img = Image.open(io.BytesIO(cached))
            img.load()
            return img.convert("RGBA")
        except Exception as e:
            logger.warning(f"Media cache decode failed for {cache_key}: {e}")
            _redis_delete(cache_key)

    # 🚫 Stampede protection
    lock_key = cache_key + ":lock"
    if not _redis_acquire_lock(lock_key, ttl=5):
        time.sleep(0.05)
        cached = _redis_get(cache_key)
        if cached is not None:
            try:
                img = Image.open(io.BytesIO(cached))
                img.load()
                return img.convert("RGBA")
            except Exception:
                pass

    try:
        img = generate_fn()
        if img is not None:
            try:
                out = io.BytesIO()
                img.save(out, format="PNG")
                _redis_set(cache_key, out.getvalue())
            except Exception as exc:
                logger.warning("Failed to cache media image %s: %s", cache_key, exc)
        return img
    finally:
        _redis_delete(lock_key)


def _get_cached_qr_image(payload, qr_settings, size):
    return _get_cached_media_image(
        "qr",
        f"{payload}:{size}:{qr_settings.get('qr_data_type','default')}".encode("utf-8", "ignore"),
        lambda: generate_qr_code(payload, qr_settings, size),
    )


def _get_cached_barcode_image(payload, qr_settings, width, height):
    return _get_cached_media_image(
        "barcode",
        f"{payload}:{width}:{height}:{qr_settings.get('barcode_data_type','default')}".encode("utf-8", "ignore"),
        lambda: generate_barcode_code128(payload, qr_settings, width=width, height=height),
    )


def _build_student_form_data(student_like):
    return {
        'name': getattr(student_like, 'name', '') or '',
        'father_name': getattr(student_like, 'father_name', '') or '',
        'class_name': getattr(student_like, 'class_name', '') or '',
        'dob': getattr(student_like, 'dob', '') or '',
        'address': getattr(student_like, 'address', '') or '',
        'phone': getattr(student_like, 'phone', '') or '',
    }


def _build_student_image_ref(student_like):
    return getattr(student_like, 'photo_url', None) or getattr(student_like, 'photo_filename', None) or ''


def _build_qr_hash(student_like):
    return generate_data_hash(_build_student_form_data(student_like), _build_student_image_ref(student_like))[:10]


def _build_payload(settings, student_like, student_id, school_name, prefix):
    data_type = settings.get(f'{prefix}_data_type', 'student_id')
    if data_type == 'url':
        base = settings.get(f'{prefix}_base_url', '') or ''
        if base and not base.endswith('/'):
            base += '/'
        return base + (student_id or _build_qr_hash(student_like))
    if data_type == 'text':
        return settings.get(f'{prefix}_custom_text', 'Sample Text')
    if data_type == 'json':
        return json.dumps({
            'student_id': student_id or _build_qr_hash(student_like),
            'name': getattr(student_like, 'name', '') or '',
            'class': getattr(student_like, 'class_name', '') or '',
            'school_name': school_name or getattr(student_like, 'school_name', '') or '',
        })
    return _build_qr_hash(student_like)


def _render_qr_and_barcode(template_img, qr_settings, student_like, student_id, school_name, scale=1.0, include_qr=True, include_barcode=True):
    qr_id = _build_qr_hash(student_like)
    if include_qr and qr_settings.get('enable_qr', False):
        qr_payload = _build_payload(qr_settings, student_like, student_id, school_name, 'qr')
        qr_size = max(1, int(round(float(qr_settings.get('qr_size', 120) or 120) * scale)))
        qr_x = int(round(float(qr_settings.get('qr_x', 50) or 50) * scale))
        qr_y = int(round(float(qr_settings.get('qr_y', 50) or 50) * scale))
        qr_img = _get_cached_qr_image(qr_payload, qr_settings, qr_size)
        try:
            template_img.paste(qr_img, (qr_x, qr_y))
        except Exception as exc:
            logger.error('Failed to paste QR code: %s', exc)

    if include_barcode and qr_settings.get('enable_barcode', False):
        barcode_payload = _build_payload(qr_settings, student_like, student_id, school_name, 'barcode')
        barcode_w = max(40, int(round(float(qr_settings.get('barcode_width', 220) or 220) * scale)))
        barcode_h = max(30, int(round(float(qr_settings.get('barcode_height', 70) or 70) * scale)))
        barcode_x = int(round(float(qr_settings.get('barcode_x', 50) or 50) * scale))
        barcode_y = int(round(float(qr_settings.get('barcode_y', 200) or 200) * scale))
        barcode_img = _get_cached_barcode_image(barcode_payload, qr_settings, barcode_w, barcode_h)
        try:
            template_img.paste(barcode_img, (barcode_x, barcode_y))
        except Exception as exc:
            logger.error('Failed to paste barcode: %s', exc)


def _photo_settings_dimensions(photo_settings, scale=1.0):
    photo_w = max(1, int(round(float(photo_settings.get('photo_width', 0) or 0) * scale)))
    photo_h = max(1, int(round(float(photo_settings.get('photo_height', 0) or 0) * scale)))
    photo_x = int(round(float(photo_settings.get('photo_x', 0) or 0) * scale))
    photo_y = int(round(float(photo_settings.get('photo_y', 0) or 0) * scale))
    radii = [
        int(round(float(photo_settings.get('photo_border_top_left', 0) or 0) * scale)),
        int(round(float(photo_settings.get('photo_border_top_right', 0) or 0) * scale)),
        int(round(float(photo_settings.get('photo_border_bottom_right', 0) or 0) * scale)),
        int(round(float(photo_settings.get('photo_border_bottom_left', 0) or 0) * scale)),
    ]
    return photo_w, photo_h, photo_x, photo_y, radii


def _load_card_photo_image(student_like, photo_settings, photo_w, photo_h):
    photo_img = load_student_photo_rgba(
        student_like,
        photo_w,
        photo_h,
        timeout=8,
        photo_settings=photo_settings,
    )
    if photo_img is not None:
        return photo_img

    logger.warning('Using placeholder image for student %s', getattr(student_like, 'id', 'unknown'))
    if not os.path.exists(PLACEHOLDER_PATH):
        return None
    try:
        placeholder = Image.open(PLACEHOLDER_PATH).convert('RGBA')
        return ImageOps.fit(placeholder, (photo_w, photo_h), Image.Resampling.LANCZOS)
    except Exception as exc:
        logger.warning('Unable to load placeholder image: %s', exc)
        return None


def _render_student_photo(template_img, student_like, photo_settings, scale=1.0):
    if not photo_settings.get('enable_photo', True):
        return
    photo_w, photo_h, photo_x, photo_y, radii = _photo_settings_dimensions(photo_settings, scale)
    photo_img = _get_cached_photo(student_like, photo_settings, photo_w, photo_h)    
    if not photo_img:
        return
    try:
        photo_img = round_photo(photo_img, radii)
        template_img.paste(photo_img, (photo_x, photo_y), photo_img)
    except Exception as exc:
        logger.error('Error rendering student photo: %s', exc)


def _build_card_field_list(student_like, template_obj, template_id, lang):
    std_labels = {
        'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
        'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
        'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
        'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'},
    }
    labels_map = std_labels.get(lang, std_labels['english'])
    fields = [
        {'key': 'NAME', 'label': labels_map['NAME'], 'val': getattr(student_like, 'name', '') or '', 'order': 10, 'field_type': 'text', 'translate_label': False},
        {'key': 'F_NAME', 'label': labels_map['F_NAME'], 'val': getattr(student_like, 'father_name', '') or '', 'order': 20, 'field_type': 'text', 'translate_label': False},
        {'key': 'CLASS', 'label': labels_map['CLASS'], 'val': getattr(student_like, 'class_name', '') or '', 'order': 30, 'field_type': 'text', 'translate_label': False},
        {'key': 'DOB', 'label': labels_map['DOB'], 'val': getattr(student_like, 'dob', '') or '', 'order': 40, 'field_type': 'date', 'translate_label': False},
        {'key': 'MOBILE', 'label': labels_map['MOBILE'], 'val': getattr(student_like, 'phone', '') or '', 'order': 50, 'field_type': 'tel', 'translate_label': False},
        {'key': 'ADDRESS', 'label': labels_map['ADDRESS'], 'val': getattr(student_like, 'address', '') or '', 'order': 60, 'field_type': 'textarea', 'translate_label': False},
    ]
    custom_data = getattr(student_like, '_template_fields', None) or getattr(student_like, 'custom_data', None) or {}
    for field in _get_render_dynamic_fields(student_like, template_id):
        fields.append({
            'key': field.field_name,
            'label': field.field_label,
            'val': custom_data.get(field.field_name, '') or '',
            'order': field.display_order,
            'field_type': field.field_type,
            'translate_label': True,
        })
    return sorted(fields, key=lambda item: int(item.get('order') or 0))


def _render_student_fields(template_img, template_obj, student_like, font_settings, photo_settings, side, lang, direction):
    template_id = template_obj.id
    card_width = template_img.width
    card_height = template_img.height
    font_bold_path = os.path.join(FONTS_FOLDER, font_settings['font_bold'])
    font_reg_path = os.path.join(FONTS_FOLDER, font_settings['font_regular'])

    label_fill_default = tuple(font_settings.get('label_font_color', [0, 0, 0]))
    value_fill_default = tuple(font_settings.get('value_font_color', [0, 0, 0]))
    colon_fill_default = tuple(font_settings.get('colon_font_color', list(label_fill_default)))
    text_case = font_settings.get('text_case', 'normal')
    show_label_colon = bool(font_settings.get('show_label_colon', True))
    align_label_colon = bool(font_settings.get('align_label_colon', True))
    label_colon_gap = int(font_settings.get('label_colon_gap', 8) or 8)

    p_x = photo_settings.get('photo_x', 0) if photo_settings.get('enable_photo', True) else 0
    p_y = photo_settings.get('photo_y', 0) if photo_settings.get('enable_photo', True) else 0
    p_w = photo_settings.get('photo_width', 0) if photo_settings.get('enable_photo', True) else 0
    p_h = photo_settings.get('photo_height', 0) if photo_settings.get('enable_photo', True) else 0

    draw = ImageDraw.Draw(template_img)
    fields = _build_card_field_list(student_like, template_obj, template_id, lang)

    label_x = font_settings['label_x']
    value_x = font_settings['value_x']
    current_y = get_initial_flow_y_for_side(template_obj, font_settings, side=side)
    line_height = font_settings['line_height']

    for item in fields:
        label_source = item['label']
        if item.get('translate_label'):
            label_source = translate_value_for_template_side(
                template_obj,
                side,
                label_source,
                field_key=f"{item.get('key')}_LABEL",
                field_type='label',
            )
        raw_label = apply_text_case(label_source, text_case)
        translated_value = translate_value_for_template_side(
            template_obj,
            side,
            item['val'],
            field_key=item.get('key'),
            field_type=item.get('field_type'),
        )
        raw_val = apply_text_case(translated_value, text_case)
        display_label = process_text_for_drawing(raw_label, lang)
        display_val = process_text_for_drawing(raw_val, lang)

        field_key = item.get('key') or order_to_field_key(item.get('order'))
        layout_item = resolve_field_layout_for_side(template_obj, field_key, label_x, value_x, current_y, side=side)
        if not field_within_vertical_bounds(layout_item, current_y, card_height):
            continue

        label_x_eff = layout_item['label_x']
        value_x_eff = layout_item['value_x']
        label_y_eff = layout_item['label_y']
        value_y_eff = layout_item['value_y']
        label_fill = layout_item.get('label_color') or label_fill_default
        value_fill = layout_item.get('value_color') or value_fill_default
        colon_fill = layout_item.get('colon_color') or colon_fill_default
        label_font_size_eff = max(1, int(layout_item.get('label_font_size') or font_settings['label_font_size']))
        value_font_size_eff = max(1, int(layout_item.get('value_font_size') or font_settings['value_font_size']))
        colon_font_size_eff = max(1, int(layout_item.get('colon_font_size') or label_font_size_eff))
        colon_y_eff = layout_item.get('colon_y', label_y_eff)
        colon_x_eff = layout_item.get('colon_x')
        colon_grow_eff = layout_item.get('colon_grow')

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
        if advances_flow:
            current_y = max(int(current_y), int(label_y_eff), int(value_y_eff))

        label_font = load_font_dynamic(font_bold_path, label_text_final, 10**9, label_font_size_eff, language=lang)
        colon_font = load_font_dynamic(font_bold_path, colon_text_final or ':', 10**9, colon_font_size_eff, language=lang)
        if layout_item['label_visible']:
            label_draw_x = flip_x_for_text_direction(
                label_x_eff, label_text_final, label_font, card_width, direction, draw=draw, grow_mode=layout_item['label_grow']
            )
            draw.text((label_draw_x, label_y_eff), label_text_final, font=label_font, fill=label_fill, **get_draw_text_kwargs(label_text_final, lang))
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
            )

        max_w = int(get_anchor_max_text_width(
            card_width=card_width,
            anchor_x=value_x_eff,
            text_direction=direction,
            line_y=value_y_eff,
            line_height=line_height,
            grow_mode=layout_item['value_grow'],
            photo_x=p_x,
            photo_y=p_y,
            photo_width=p_w,
            photo_height=p_h,
            page_margin=20,
            photo_gap=15,
            min_width=20,
        ))

        if field_key == 'ADDRESS':
            curr_size = value_font_size_eff
            min_size = 10
            wrapped_addr = []
            while curr_size >= min_size:
                addr_font = load_font_dynamic(font_reg_path, 'X', 10**9, curr_size, language=lang)
                avg_char_w = curr_size * 0.50
                chars_limit = max(5, int(max_w / max(avg_char_w, 1)))
                wrapped_addr = textwrap.wrap(raw_val, width=chars_limit, break_long_words=True)
                fits_horizontally = len(wrapped_addr) <= 2
                if fits_horizontally:
                    for line in wrapped_addr:
                        measure_text = process_text_for_drawing(line, lang)
                        if draw.textlength(measure_text, font=addr_font, **get_draw_text_kwargs(measure_text, lang)) > max_w:
                            fits_horizontally = False
                            break
                if fits_horizontally:
                    break
                curr_size -= 2
            if curr_size < min_size:
                addr_font = load_font_dynamic(font_reg_path, 'X', 10**9, min_size, language=lang)
            for line in wrapped_addr[:2]:
                line_display = process_text_for_drawing(line, lang)
                if layout_item['value_visible']:
                    value_draw_x = flip_x_for_text_direction(
                        value_x_eff, line_display, addr_font, card_width, direction, draw=draw, grow_mode=layout_item['value_grow']
                    )
                    draw.text((value_draw_x, value_y_eff), line_display, font=addr_font, fill=value_fill, **get_draw_text_kwargs(line_display, lang))
                spacing = line_height if curr_size > 20 else curr_size + 5
                value_y_eff += spacing
                if advances_flow:
                    current_y += spacing
            continue

        value_font, _ = fit_dynamic_font_to_single_line(
            draw,
            font_reg_path,
            display_val,
            max_w,
            value_font_size_eff,
            language=lang,
        )
        if layout_item['value_visible']:
            value_draw_x = flip_x_for_text_direction(
                value_x_eff, display_val, value_font, card_width, direction, draw=draw, grow_mode=layout_item['value_grow']
            )
            draw.text((value_draw_x, value_y_eff), display_val, font=value_font, fill=value_fill, **get_draw_text_kwargs(display_val, lang))
        if advances_flow:
            current_y += line_height


def render_student_card_side_background(
    template_obj,
    student_like,
    side='front',
    student_id=None,
    school_name=None,
    render_scale=1.0,
    include_photo=True,
    include_qr=True,
    include_barcode=True,
):
    return _get_cached_final_card(
    template_obj,
    student_like,
    side=side,
    student_id=student_id,
    school_name=school_name,
    render_scale=render_scale,
    include_photo=include_photo,
    include_qr=include_qr,
    include_barcode=include_barcode,
    include_text=False
)


def render_student_card_side(
    template_obj,
    student_like,
    side='front',
    student_id=None,
    school_name=None,
    render_scale=1.0,
    include_photo=True,
    include_qr=True,
    include_barcode=True,
    include_text=True,
):
    if not template_obj:
        return None

    template_id = template_obj.id
    template_path = get_template_path(template_id, side=side)
    if not template_path:
        return None

    font_settings, photo_settings, qr_settings, _ = get_template_settings(template_id, side=side)
    card_width, card_height = get_card_size(template_id)
    template_img = _load_template_image_for_render(template_path, card_width, card_height, render_scale=render_scale)
    lang, direction = get_template_language_direction_from_obj(template_obj, side=side)

    if include_text:
        _render_student_fields(template_img, template_obj, student_like, font_settings, photo_settings, side, lang, direction)

    if include_photo:
        _render_student_photo(template_img, student_like, photo_settings, scale=max(1.0, float(render_scale or 1.0)))

    if include_qr or include_barcode:
        _render_qr_and_barcode(template_img, qr_settings, student_like, student_id, school_name, scale=max(1.0, float(render_scale or 1.0)), include_qr=include_qr, include_barcode=include_barcode)

    if template_img.size != (
        max(1, int(round(card_width * max(1.0, float(render_scale or 1.0))))),
        max(1, int(round(card_height * max(1.0, float(render_scale or 1.0))))),
    ):
        template_img = template_img.resize(
            (
                max(1, int(round(card_width * max(1.0, float(render_scale or 1.0))))),
                max(1, int(round(card_height * max(1.0, float(render_scale or 1.0))))),
            ),
            Image.LANCZOS,
        )

    apply_layout_custom_objects_pil(template_img, template_obj, font_settings, side=side, language=lang, render_scale=max(1.0, float(render_scale or 1.0)))
    return template_img


def draw_aligned_colon_pil(
    draw,
    image_width,
    direction,
    value_x,
    y,
    colon_text,
    colon_font,
    fill,
    language,
    colon_gap,
    anchor_x=None,
    grow_mode=None,
):
    """Draw a standalone aligned colon near the value anchor."""
    if not colon_text:
        return
    if anchor_x is None:
        colon_anchor_x, colon_grow = colon_anchor_for_value(value_x, direction, gap_px=colon_gap)
    else:
        colon_anchor_x = anchor_x
        colon_grow = grow_mode or ("left" if str(direction or "ltr").strip().lower() == "rtl" else "right")
    colon_draw_x = flip_x_for_text_direction(
        colon_anchor_x,
        colon_text,
        colon_font,
        image_width,
        direction,
        draw=draw,
        grow_mode=colon_grow,
    )
    draw.text(
        (colon_draw_x, y),
        colon_text,
        font=colon_font,
        fill=fill,
        **get_draw_text_kwargs(colon_text, language),
    )


def with_cache_bust(url):
    """Append a cache-busting query param for preview images."""
    if not url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}v={int(time.time() * 1000)}"

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
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "fallback_dev_key_if_env_fails")

logger = logging.getLogger(__name__)

# When running locally, keep templates auto-reloading so HTML changes (e.g. i18n/RTL tweaks)
# take effect without needing debug mode.
try:
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
except Exception:
    pass

# Token Serializer
serializer = URLSafeTimedSerializer(app.secret_key)

# Initialize Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["12000 per day", "1000 per hour"], # Global default
    storage_uri="memory://" # Store limits in memory
)

# Enable CSRF Protection globally
csrf = CSRFProtect(app)

# ================== Initialize Notification Scheduler ==================
scheduler = BackgroundScheduler()
scheduler.add_job(
    func=check_and_notify_approaching_deadlines,
    trigger="cron",
    hour="9",  # Run at 9 AM daily
    minute="0",
    id="deadline_check_job",
    name="Check and notify approaching deadlines"
)

try:
    scheduler.start()
    logger.info("Background scheduler started for deadline notifications")
except Exception as e:
    logger.error(f"Failed to start scheduler: {e}")

# Shutdown scheduler when app exits
atexit.register(lambda: scheduler.shutdown() if scheduler.running else None)

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

# SQLAlchemy configuration
import os

# Prefer modern `DATABASE_URL` (Railway/Heroku style), but also support the more typical
# Flask-SQLAlchemy env var name used in many local setups.
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")

# Fix old Railway / Heroku postgres:// URLs
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# If still no DATABASE_URL, use SQLite🔥
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///local_dev.db"

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Check Railway Variables.")

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

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
    return redirect(request.referrer or url_for('landing'))

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

logger = logging.getLogger(__name__)

# ================== Email Config ==================
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")  # Change in production
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")  # MUST be a pbkdf2:sha256 hash

# ================== Student Blueprint ==================
student_bp = Blueprint('student', __name__)

# ================== Helper Functions ==================
def get_form_int(form_data, key, default=0):
    """Safely get integer value from form data"""
    try:
        value = form_data.get(key)
        if value is None or value == '':
            return default
        return int(value)
    except (ValueError, TypeError):
        return default


def order_to_field_key(order_value):
    """Map standard display order to stable field key names."""
    mapping = {
        10: "NAME",
        20: "F_NAME",
        30: "CLASS",
        40: "DOB",
        50: "MOBILE",
        60: "ADDRESS",
    }
    return mapping.get(order_value)


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


def default_text_direction_for_language(language):
    return "rtl" if str(language or "").strip().lower() in {"urdu", "arabic"} else "ltr"


def validate_double_sided_language_pair(front_language, back_language):
    front = str(front_language or "english").strip().lower()
    back = str(back_language or "english").strip().lower()
    return front in SUPPORTED_TEMPLATE_LANGUAGES and back in SUPPORTED_TEMPLATE_LANGUAGES


def _should_skip_translation(raw_value, field_key=None, field_type=None):
    text = str(raw_value or "").strip()
    if not text:
        return True

    normalized_key = str(field_key or "").strip().upper()
    normalized_type = str(field_type or "").strip().lower()

    if normalized_key in NON_TRANSLATABLE_FIELD_KEYS:
        return True
    if normalized_type in NON_TRANSLATABLE_FIELD_TYPES:
        return True
    if "@" in text or "://" in text:
        return True

    letters = re.findall(r"[A-Za-z\u0600-\u06FF\u0900-\u097F]", text)
    if not letters:
        return True

    compact = re.sub(r"\s+", "", text)
    if compact and re.fullmatch(r"[\d\W_]+", compact):
        return True

    return False


def _extract_google_translate_text(payload):
    if not isinstance(payload, list) or not payload:
        return ""
    segments = payload[0]
    if not isinstance(segments, list):
        return ""
    return "".join(
        str(segment[0])
        for segment in segments
        if isinstance(segment, list) and segment and segment[0] is not None
    ).strip()


def detect_translation_source_language(raw_text, fallback="english"):
    text = str(raw_text or "").strip()
    if not text:
        return str(fallback or "english").strip().lower()

    if re.search(r"[\u0900-\u097F]", text):
        return "hindi"
    if re.search(r"[\u0600-\u06FF]", text):
        hinted = str(fallback or "").strip().lower()
        if hinted in {"urdu", "arabic"}:
            return hinted
        return "urdu"
    if re.search(r"[A-Za-z]", text):
        return "english"
    return str(fallback or "english").strip().lower()


@lru_cache(maxsize=4096)
def _google_translate_text(raw_text, source_language, target_language):
    text = str(raw_text or "").strip()
    source = str(source_language or "").strip().lower()
    target = str(target_language or "").strip().lower()
    if not text or source == target:
        return text

    source_code = LANGUAGE_TO_TRANSLATE_CODE.get(source)
    target_code = LANGUAGE_TO_TRANSLATE_CODE.get(target)
    if not source_code or not target_code:
        return text

    try:
        if GOOGLE_TRANSLATE_API_KEY:
            response = requests.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": GOOGLE_TRANSLATE_API_KEY},
                json={
                    "q": text,
                    "source": source_code,
                    "target": target_code,
                    "format": "text",
                },
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            translated = (
                payload.get("data", {})
                .get("translations", [{}])[0]
                .get("translatedText", "")
            )
            return str(translated or "").strip() or text

        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": source_code,
                "tl": target_code,
                "dt": "t",
                "q": text,
            },
            timeout=8,
        )
        response.raise_for_status()
        translated = _extract_google_translate_text(response.json())
        return translated or text
    except Exception as exc:
        logger.warning(
            "Google translation failed for %s -> %s: %s",
            source,
            target,
            exc,
        )
        return text


def translate_value_for_template_side(template_obj, side, raw_value, *, field_key=None, field_type=None):
    text = str(raw_value or "")
    if not template_obj:
        return text

    target_language, _ = get_template_language_direction_from_obj(template_obj, side=side)
    source_hint = (getattr(template_obj, "language", "english") or "english").strip().lower()
    source_language = detect_translation_source_language(text, fallback=source_hint)

    if source_language == target_language:
        return text
    if source_language not in SUPPORTED_TEMPLATE_LANGUAGES or target_language not in SUPPORTED_TEMPLATE_LANGUAGES:
        return text
    if _should_skip_translation(text, field_key=field_key, field_type=field_type):
        return text

    return _google_translate_text(text, source_language, target_language)


def field_consumes_layout_space(layout_item, raw_value=""):
    """Return True when a field should reserve vertical flow space."""
    if not isinstance(layout_item, dict):
        return bool(str(raw_value or "").strip())
    if layout_item.get("label_visible"):
        return True
    return bool(layout_item.get("value_visible")) and bool(str(raw_value or "").strip())


def field_advances_layout_flow(layout_item, raw_value="", *, separate_colon=False):
    """
    Return True only for fields that should advance the flowing Y cursor.

    Manually positioned fields must render at their saved coordinates without dragging
    later fields down the card.
    """
    if not field_consumes_layout_space(layout_item, raw_value):
        return False
    if not isinstance(layout_item, dict):
        return True

    has_value = bool(str(raw_value or "").strip())
    if layout_item.get("label_visible") and layout_item.get("label_manual_y"):
        return False
    if has_value and layout_item.get("value_visible") and layout_item.get("value_manual_y"):
        return False
    if separate_colon and layout_item.get("colon_visible") and layout_item.get("colon_manual_y"):
        return False
    return True


def field_within_vertical_bounds(layout_item, default_y, card_height, margin=20):
    """
    Decide whether a field should still render vertically.

    Older render loops used only the flowing `current_y` cursor and could break before
    reaching later fields, even when those fields had an explicit manual position saved
    in the visual editor. We instead trust the resolved field layout first.
    """
    try:
        limit = int(card_height) - int(margin)
    except Exception:
        limit = int(card_height or 0)

    if not isinstance(layout_item, dict):
        try:
            return int(default_y or 0) <= limit
        except Exception:
            return True

    y_candidates = []
    if layout_item.get("label_visible"):
        y_candidates.append(int(layout_item.get("label_y", default_y)))
    if layout_item.get("value_visible"):
        y_candidates.append(int(layout_item.get("value_y", default_y)))
    if layout_item.get("colon_visible"):
        y_candidates.append(int(layout_item.get("colon_y", layout_item.get("label_y", default_y))))

    if not y_candidates:
        try:
            y_candidates.append(int(layout_item.get("value_y", layout_item.get("label_y", default_y))))
        except Exception:
            y_candidates.append(int(default_y or 0))

    return min(y_candidates) <= limit


def _hex_to_rgb_for_editor(value, fallback=(0, 0, 0)):
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return tuple(int(v) for v in value[:3])
        except Exception:
            return fallback
    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))
        except Exception:
            return fallback
    return fallback


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

        if obj_type == "rect":
            w = max(1, int(round((obj.get("width", 120) or 120) * scale)))
            h = max(1, int(round((obj.get("height", 60) or 60) * scale)))
            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(overlay).rectangle([0, 0, w, h], fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
            _paste_rotated_overlay(overlay, x, y, angle)
        elif obj_type == "circle":
            w = max(1, int(round((obj.get("width", 80) or 80) * scale)))
            h = max(1, int(round((obj.get("height", obj.get("width", 80)) or obj.get("width", 80)) * scale)))
            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(overlay).ellipse([0, 0, w, h], fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
            _paste_rotated_overlay(overlay, x, y, angle)
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
            _paste_rotated_overlay(overlay, min_x, min_y, angle)
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
                _paste_rotated_overlay(overlay, x, y, angle)
            except Exception as image_err:
                logger.warning("Skipping custom image object due to render error: %s", image_err)
        elif obj_type == "text":
            text = str(obj.get("text") or "Text")
            font_size = max(8, int(round((obj.get("font_size", 24) or 24) * scale)))
            font_path = font_bold_path if bool(obj.get("bold")) else font_reg_path
            font = load_font_dynamic(font_path, text, template_img.width, font_size, language=language)
            bbox = draw.textbbox((0, 0), text, font=font, **get_draw_text_kwargs(text, language))
            text_w = max(1, (bbox[2] - bbox[0]) + 6)
            text_h = max(1, (bbox[3] - bbox[1]) + 6)
            overlay = Image.new("RGBA", (text_w, text_h), (0, 0, 0, 0))
            ImageDraw.Draw(overlay).text((3 - bbox[0], 3 - bbox[1]), text, font=font, fill=fill_rgba, **get_draw_text_kwargs(text, language))
            _paste_rotated_overlay(overlay, x, y, angle)


def _looks_like_pdf_template_source(path_or_url):
    try:
        src = str(path_or_url or "").strip().lower()
    except Exception:
        return False
    if not src:
        return False
    src_no_query = src.split("?", 1)[0]
    if src_no_query.endswith(".pdf") or "/raw/upload/" in src_no_query:
        return True
    if src.startswith(("http://", "https://")):
        return False
    try:
        with open(path_or_url, "rb") as fh:
            return b"%PDF" in fh.read(16)
    except Exception:
        return False


def _flatten_to_rgb(image):
    if image is None:
        return None
    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        alpha = image.getchannel("A") if "A" in image.getbands() else None
        background.paste(image.convert("RGBA"), mask=alpha)
        return background
    return image.convert("RGB")


@lru_cache(maxsize=32)
def _load_template_image_for_render_cached(path_or_url, target_w, target_h, scale_key):
    target_w = max(1, int(target_w or 1))
    target_h = max(1, int(target_h or 1))
    scale = max(1.0, float(scale_key or 1.0))
    image_open = getattr(Image, "open_original", Image.open)

    if _looks_like_pdf_template_source(path_or_url):
        try:
            if str(path_or_url).startswith(("http://", "https://")):
                resp = requests.get(path_or_url, timeout=15)
                resp.raise_for_status()
                payload = resp.content or b""
                pdf_header_pos = payload.find(b"%PDF")
                if pdf_header_pos >= 0:
                    payload = payload[pdf_header_pos:]
                pdf_doc = fitz.open(stream=payload, filetype="pdf")
            else:
                pdf_doc = fitz.open(path_or_url)
            try:
                page = pdf_doc[0]
                render_dpi = max(int(DPI), int(round(DPI * scale)))
                pix = page.get_pixmap(dpi=render_dpi, alpha=False, colorspace=fitz.csRGB)
                img = image_open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            finally:
                pdf_doc.close()
            if img.size != (target_w, target_h):
                img = img.resize((target_w, target_h), Image.LANCZOS)
        except Exception as exc:
            logger.warning("High-DPI PDF template render failed for %s: %s", path_or_url, exc)
            img = load_template_smart(path_or_url)
            img = _flatten_to_rgb(img)
            if img.size != (target_w, target_h):
                img = img.resize((target_w, target_h), Image.LANCZOS)
    else:
        img = load_template_smart(path_or_url)
        img = _flatten_to_rgb(img)
        if img.size != (target_w, target_h):
            img = img.resize((target_w, target_h), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _load_template_image_for_render(path_or_url, card_width, card_height, render_scale=1.0):
    """
    Load a template image for rendering, with optional higher-DPI PDF rasterization.

    This is used by the compiled Corel export path to make uploaded PDF templates look
    stronger when we intentionally flatten the template background for compatibility.
    """
    scale = max(1.0, float(render_scale or 1.0))
    target_w = max(1, int(round(float(card_width) * scale)))
    target_h = max(1, int(round(float(card_height) * scale)))

    cache_key = round(scale, 3)
    payload = _load_template_image_for_render_cached(path_or_url, target_w, target_h, cache_key)
    return Image.open(io.BytesIO(payload)).convert("RGB")


def _get_render_dynamic_fields(student_like, template_id):
    """Allow callers like bulk generation to inject preloaded template fields."""
    cached_fields = getattr(student_like, "_template_fields", None)
    if cached_fields is not None:
        return cached_fields
    return TemplateField.query.filter_by(template_id=template_id).order_by(TemplateField.display_order.asc()).all()


def build_student_card_text_runs(template_obj, student_like, side="front"):
    """Return text draw instructions using the same layout flow as the PIL renderer."""
    if not template_obj:
        return {"runs": [], "lang": "english", "direction": "ltr", "card_width": 0, "card_height": 0}

    template_id = template_obj.id
    font_settings, photo_settings, _, _ = get_template_settings(template_id, side=side)
    card_width, card_height = get_card_size(template_id)
    measure_img = Image.new("RGB", (max(1, card_width), max(1, card_height)), (255, 255, 255))
    draw = ImageDraw.Draw(measure_img)

    try:
        label_fill_default = tuple(font_settings.get("label_font_color", [0, 0, 0]))
        value_fill_default = tuple(font_settings.get("value_font_color", [0, 0, 0]))
        colon_fill_default = tuple(font_settings.get("colon_font_color", list(label_fill_default)))
    except Exception:
        label_fill_default = (0, 0, 0)
        value_fill_default = (0, 0, 0)
        colon_fill_default = label_fill_default

    lang, direction = get_template_language_direction_from_obj(template_obj, side=side)
    font_bold_path = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
    font_reg_path = os.path.join(FONTS_FOLDER, font_settings["font_regular"])

    std_labels = {
        'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
        'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
        'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
        'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
    }
    labels_map = std_labels.get(lang, std_labels['english'])

    text_case = font_settings.get("text_case", "normal")
    show_label_colon = bool(font_settings.get("show_label_colon", True))
    align_label_colon = bool(font_settings.get("align_label_colon", True))
    label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)

    all_fields = [
        {'key': 'NAME', 'label': labels_map['NAME'], 'val': getattr(student_like, "name", "") or "", 'order': 10, 'field_type': 'text', 'translate_label': False},
        {'key': 'F_NAME', 'label': labels_map['F_NAME'], 'val': getattr(student_like, "father_name", "") or "", 'order': 20, 'field_type': 'text', 'translate_label': False},
        {'key': 'CLASS', 'label': labels_map['CLASS'], 'val': getattr(student_like, "class_name", "") or "", 'order': 30, 'field_type': 'text', 'translate_label': False},
        {'key': 'DOB', 'label': labels_map['DOB'], 'val': getattr(student_like, "dob", "") or "", 'order': 40, 'field_type': 'date', 'translate_label': False},
        {'key': 'MOBILE', 'label': labels_map['MOBILE'], 'val': getattr(student_like, "phone", "") or "", 'order': 50, 'field_type': 'tel', 'translate_label': False},
        {'key': 'ADDRESS', 'label': labels_map['ADDRESS'], 'val': getattr(student_like, "address", "") or "", 'order': 60, 'field_type': 'textarea', 'translate_label': False},
    ]

    custom_data = getattr(student_like, "custom_data", None) or {}
    for field in _get_render_dynamic_fields(student_like, template_id):
        all_fields.append({
            'key': field.field_name,
            'label': field.field_label,
            'val': custom_data.get(field.field_name, "") or "",
            'order': field.display_order,
            'field_type': field.field_type,
            'translate_label': True,
        })
    all_fields.sort(key=lambda item: item['order'])

    photo_enabled = bool(photo_settings.get("enable_photo", True))
    p_x = photo_settings.get("photo_x", 0) if photo_enabled else 0
    p_y = photo_settings.get("photo_y", 0) if photo_enabled else 0
    p_w = photo_settings.get("photo_width", 0) if photo_enabled else 0
    p_h = photo_settings.get("photo_height", 0) if photo_enabled else 0

    label_x = font_settings["label_x"]
    value_x = font_settings["value_x"]
    current_y = get_initial_flow_y_for_side(template_obj, font_settings, side=side)
    line_height = font_settings["line_height"]
    runs = []

    for item in all_fields:
        label_source = item['label']
        if item.get('translate_label'):
            label_source = translate_value_for_template_side(
                template_obj,
                side,
                label_source,
                field_key=f"{item.get('key')}_LABEL",
                field_type='label',
            )
        raw_label = apply_text_case(label_source, text_case)
        translated_value = translate_value_for_template_side(
            template_obj,
            side,
            item['val'],
            field_key=item.get('key'),
            field_type=item.get('field_type'),
        )
        raw_val = apply_text_case(translated_value, text_case)
        display_label = process_text_for_drawing(raw_label, lang)
        display_val = process_text_for_drawing(raw_val, lang)
        field_key = item.get('key') or order_to_field_key(item.get('order'))
        layout_item = resolve_field_layout_for_side(template_obj, field_key, label_x, value_x, current_y, side=side)
        if not field_within_vertical_bounds(layout_item, current_y, card_height):
            continue
        label_x_eff = layout_item["label_x"]
        value_x_eff = layout_item["value_x"]
        label_y_eff = layout_item["label_y"]
        value_y_eff = layout_item["value_y"]
        label_fill = layout_item.get("label_color") or label_fill_default
        value_fill = layout_item.get("value_color") or value_fill_default
        colon_fill = layout_item.get("colon_color") or colon_fill_default
        label_font_size_eff = max(1, int(layout_item.get("label_font_size") or font_settings["label_font_size"]))
        value_font_size_eff = max(1, int(layout_item.get("value_font_size") or font_settings["value_font_size"]))
        colon_font_size_eff = max(1, int(layout_item.get("colon_font_size") or label_font_size_eff))
        colon_y_eff = layout_item.get("colon_y", label_y_eff)
        colon_x_eff = layout_item.get("colon_x")
        colon_grow_eff = layout_item.get("colon_grow")

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
        if advances_flow:
            current_y = max(int(current_y), int(label_y_eff), int(value_y_eff))

        label_font = load_font_dynamic(font_bold_path, label_text_final or "X", 10**9, label_font_size_eff, language=lang)
        colon_font = load_font_dynamic(font_bold_path, colon_text_final or ":", 10**9, colon_font_size_eff, language=lang)
        if layout_item["label_visible"] and label_text_final:
            label_draw_x = flip_x_for_text_direction(
                label_x_eff, label_text_final, label_font, card_width, direction, draw=draw, grow_mode=layout_item["label_grow"]
            )
            runs.append({
                "part": "label",
                "text": label_text_final,
                "x": int(label_draw_x),
                "y": int(label_y_eff),
                "font_path": font_bold_path,
                "font_size": int(label_font_size_eff),
                "color": tuple(label_fill),
                "language": lang,
                "direction": direction,
            })
            if colon_text_final:
                if colon_x_eff is None:
                    colon_anchor_x, colon_grow = colon_anchor_for_value(value_x_eff, direction, gap_px=label_colon_gap)
                else:
                    colon_anchor_x = colon_x_eff
                    colon_grow = colon_grow_eff or ("left" if str(direction or "ltr").strip().lower() == "rtl" else "right")
                colon_draw_x = flip_x_for_text_direction(
                    colon_anchor_x, colon_text_final, colon_font, card_width, direction, draw=draw, grow_mode=colon_grow
                )
                runs.append({
                    "part": "colon",
                    "text": colon_text_final,
                    "x": int(colon_draw_x),
                    "y": int(colon_y_eff),
                    "font_path": font_bold_path,
                    "font_size": int(colon_font_size_eff),
                    "color": tuple(colon_fill),
                    "language": lang,
                    "direction": direction,
                })

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

        if field_key == "ADDRESS":
            curr_size = value_font_size_eff
            min_size = 10
            wrapped_addr = []
            while curr_size >= min_size:
                addr_font = load_font_dynamic(font_reg_path, "X", 10**9, curr_size, language=lang)
                avg_char_w = curr_size * 0.50
                chars_limit = max(5, int(max_w / max(avg_char_w, 1)))
                wrapped_addr = textwrap.wrap(raw_val, width=chars_limit, break_long_words=True)
                fits_horizontally = len(wrapped_addr) <= 2
                if fits_horizontally:
                    for line in wrapped_addr:
                        measure_text = process_text_for_drawing(line, lang)
                        if draw.textlength(measure_text, font=addr_font, **get_draw_text_kwargs(measure_text, lang)) > max_w:
                            fits_horizontally = False
                            break
                if fits_horizontally:
                    break
                curr_size -= 2
            if curr_size < min_size:
                addr_font = load_font_dynamic(font_reg_path, "X", 10**9, min_size, language=lang)
            for line in wrapped_addr[:2]:
                line_display = process_text_for_drawing(line, lang)
                if layout_item["value_visible"]:
                    value_draw_x = flip_x_for_text_direction(
                        value_x_eff, line_display, addr_font, card_width, direction, draw=draw, grow_mode=layout_item["value_grow"]
                    )
                    runs.append({
                        "part": "value",
                        "text": line_display,
                        "x": int(value_draw_x),
                        "y": int(value_y_eff),
                        "font_path": font_reg_path,
                        "font_size": int(curr_size if curr_size >= min_size else min_size),
                        "color": tuple(value_fill),
                        "language": lang,
                        "direction": direction,
                    })
                spacing = line_height if curr_size > 20 else curr_size + 5
                value_y_eff += spacing
                if advances_flow:
                    current_y += spacing
            continue

        value_font, fitted_value_font_size = fit_dynamic_font_to_single_line(
            draw,
            font_reg_path,
            display_val,
            max_w,
            value_font_size_eff,
            language=lang,
        )
        if layout_item["value_visible"]:
            value_draw_x = flip_x_for_text_direction(
                value_x_eff, display_val, value_font, card_width, direction, draw=draw, grow_mode=layout_item["value_grow"]
            )
            runs.append({
                "part": "value",
                "text": display_val,
                "x": int(value_draw_x),
                "y": int(value_y_eff),
                "font_path": font_reg_path,
                "font_size": int(fitted_value_font_size),
                "color": tuple(value_fill),
                "language": lang,
                "direction": direction,
            })
        if advances_flow:
            current_y += line_height

    return {
        "runs": runs,
        "lang": lang,
        "direction": direction,
        "card_width": card_width,
        "card_height": card_height,
    }



def draw_aligned_colon_pil(
    draw,
    image_width,
    direction,
    value_x,
    y,
    colon_text,
    colon_font,
    fill,
    language,
    colon_gap,
    anchor_x=None,
    grow_mode=None,
):
    """Draw a standalone aligned colon near the value anchor."""
    if not colon_text:
        return
    if anchor_x is None:
        colon_anchor_x, colon_grow = colon_anchor_for_value(value_x, direction, gap_px=colon_gap)
    else:
        colon_anchor_x = anchor_x
        colon_grow = grow_mode or ("left" if str(direction or "ltr").strip().lower() == "rtl" else "right")
    colon_draw_x = flip_x_for_text_direction(
        colon_anchor_x,
        colon_text,
        colon_font,
        image_width,
        direction,
        draw=draw,
        grow_mode=colon_grow,
    )
    draw.text(
        (colon_draw_x, y),
        colon_text,
        font=colon_font,
        fill=fill,
        **get_draw_text_kwargs(colon_text, language),
    )
  


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
@app.route("/admin/activity_log")
def view_activity_log():
    # 1. Security Check
    if not session.get("admin"):
        return redirect(url_for("login"))

    # 2. Pagination Logic
    page = request.args.get('page', 1, type=int)
    per_page = 50  # Show 50 logs per page

    # 3. Fetch Logs (Sorted Newest First)
    pagination = ActivityLog.query.order_by(ActivityLog.timestamp.desc())\
                                  .paginate(page=page, per_page=per_page, error_out=False)
    
    logs = pagination.items

    return render_template(
        "activity_log.html",
        logs=logs,
        pagination=pagination
    )

@app.route("/admin/reset_activity_log", methods=["POST"])
def reset_activity_log():
    # 1. Security Check
    if not session.get("admin"):
        return redirect(url_for("login"))

    try:
        # 2. Delete all records in ActivityLog table
        db.session.query(ActivityLog).delete()
        db.session.commit()
        
        # 3. Create a new entry saying it was reset (so it's not totally empty)
        log_activity("Reset Activity Log", details="All previous history cleared by Admin")
        
        return redirect(url_for("view_activity_log", success="Activity log cleared successfully."))
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error clearing activity log: {e}")
        return redirect(url_for("view_activity_log", error=f"Failed to clear log: {str(e)}"))


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
                except:
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
                
                conn.commit()

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

def split_photo_reference(photo_ref):
    """Split a stored photo reference into a remote URL or local upload path."""
    value = str(photo_ref or "").strip()
    if not value or value == "placeholder.jpg":
        return None, None
    if value.startswith(("http://", "https://")):
        return value, None
    normalized = value.replace("\\", "/")
    if os.path.isabs(value) and os.path.exists(value):
        return None, value
    if normalized.startswith("/static/"):
        local_candidate = os.path.join(STATIC_DIR, normalized[len("/static/"):])
        if os.path.exists(local_candidate):
            return None, local_candidate
    if normalized.startswith("static/"):
        local_candidate = os.path.join(STATIC_DIR, normalized[len("static/"):])
        if os.path.exists(local_candidate):
            return None, local_candidate
    if normalized.startswith("uploads/") or normalized.startswith("Uploads/"):
        local_candidate = os.path.join(STATIC_DIR, normalized)
        if os.path.exists(local_candidate):
            return None, local_candidate
    if os.path.exists(normalized):
        return None, normalized
    return None, os.path.join(UPLOAD_FOLDER, value)

def resolve_student_photo_reference(student):
    """
    Resolve the best available photo source for a student.

    Supports:
    - `photo_url` for current cloud records
    - `photo_filename` containing a legacy local filename
    - `photo_filename` containing a legacy/bulk remote URL
    """
    image_url = str(getattr(student, "image_url", "") or "").strip()

    photo_url, local_path = split_photo_reference(getattr(student, "photo_url", None))
    if photo_url and image_url and photo_url == image_url:
        logger.warning(
            f"Student {getattr(student, 'id', 'unknown')} has photo_url == image_url; ignoring photo_url"
        )
        photo_url = None

    if photo_url or local_path:
        return photo_url, local_path

    fallback_url, fallback_local_path = split_photo_reference(getattr(student, "photo_filename", None))
    if fallback_url and image_url and fallback_url == image_url:
        logger.warning(
            f"Student {getattr(student, 'id', 'unknown')} has photo_filename URL equal to image_url; ignoring fallback URL"
        )
        fallback_url = None

    return fallback_url, fallback_local_path

def load_student_photo_rgba(student, width, height, timeout=10, photo_settings=None, allow_placeholder=True):
    """Backward-compatible wrapper around the shared student photo preparation flow."""
    return load_student_photo_rgba_prepared(
        student,
        width,
        height,
        timeout=timeout,
        photo_settings=photo_settings,
        allow_placeholder=allow_placeholder,
    )


def load_student_photo_rgba_prepared(
    student,
    width,
    height,
    timeout=10,
    photo_settings=None,
    allow_placeholder=True,
):
    """
    Load and normalize a student photo for a target frame.

    This is the single source of truth for card photo behavior across previews,
    generated cards, bulk output, and PDF/Corel exports.
    """
    photo_settings = photo_settings or {}
    image_open = getattr(Image, "open_original", Image.open)
    photo_url, local_path = resolve_student_photo_reference(student)
    prepared_cache = getattr(student, "_prepared_photo_cache", None)
    cache_key = None
    if isinstance(prepared_cache, dict):
        cache_source = photo_url or local_path or ("__placeholder__" if allow_placeholder else "__none__")
        cache_key = (
            str(cache_source),
            int(width or 0),
            int(height or 0),
            bool(allow_placeholder),
        )
        cached_payload = prepared_cache.get(cache_key)
        if cached_payload:
            cached_img = image_open(io.BytesIO(cached_payload))
            cached_img.load()
            return cached_img.convert("RGBA")

    def _load_detached_image(image_bytes):
        photo_img = image_open(io.BytesIO(image_bytes))
        photo_img.load()
        return photo_img.copy()

    try:
        photo_img = None
        if photo_url:
            logger.info(f"Loading student photo from URL: {photo_url}")
            response = requests.get(photo_url, timeout=timeout)
            response.raise_for_status()
            photo_img = _load_detached_image(response.content)

        if photo_img is None and local_path and os.path.exists(local_path):
            try:
                if os.path.getsize(local_path) <= 0:
                    logger.warning("Student photo file is empty: %s", local_path)
                    local_path = None
            except OSError:
                local_path = None

        if photo_img is None and local_path and os.path.exists(local_path):
            logger.info(f"Loading student photo from local path: {local_path}")
            with open(local_path, "rb") as fh:
                photo_img = _load_detached_image(fh.read())

        if photo_img is None and allow_placeholder and os.path.exists(PLACEHOLDER_PATH):
            logger.info(f"Loading placeholder photo: {PLACEHOLDER_PATH}")
            with open(PLACEHOLDER_PATH, "rb") as fh:
                photo_img = _load_detached_image(fh.read())

        if photo_img is None:
            logger.error("No photo source available and placeholder not allowed or missing")
            return None

        prepared_img = _process_photo_pil(
            photo_img,
            target_width=width,
            target_height=height,
        )
        if prepared_img is None:
            return None
        prepared_img.load()
        prepared_img = prepared_img.copy()
        if cache_key and isinstance(prepared_cache, dict):
            cache_buffer = io.BytesIO()
            prepared_img.save(cache_buffer, format="PNG")
            prepared_cache[cache_key] = cache_buffer.getvalue()
        return prepared_img
    except Exception as exc:
        logger.warning("Unable to prepare student photo: %s", exc)
        return None

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

def photo_match_aliases(value):
    """
    Build forgiving lookup aliases for bulk photo matching.

    Supports matching by:
    - exact filename stem
    - basename from `photo_path` values like `photos/john.jpg`
    - names with spaces/underscores/hyphens normalized
    """
    raw = str(value or "").strip().lower()
    if not raw:
        return set()

    normalized_path = raw.replace("\\", "/")
    basename = os.path.basename(normalized_path)
    stem = os.path.splitext(basename)[0]
    path_stem = os.path.splitext(normalized_path)[0]

    aliases = set()
    for item in {raw, normalized_path, basename, stem, path_stem}:
        cleaned = str(item or "").strip().lower().strip("./")
        if not cleaned:
            continue
        aliases.add(cleaned)

        compact = re.sub(r"[\s_\-]+", "", cleaned)
        if compact:
            aliases.add(compact)

        underscore = re.sub(r"[\s\-]+", "_", cleaned).strip("_")
        if underscore:
            aliases.add(underscore)

        spaced = re.sub(r"[_\-]+", " ", cleaned)
        spaced = re.sub(r"\s+", " ", spaced).strip()
        if spaced:
            aliases.add(spaced)

    return aliases

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
        
        for template in templates:
            font_settings = get_default_font_config()
            photo_settings = get_default_photo_config()
            qr_settings = get_default_qr_config()
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
            back_font_settings = {**get_default_font_config(), **(template.back_font_settings or {})}
            back_photo_settings = {**get_default_photo_config(), **(template.back_photo_settings or {})}
            back_qr_settings = {**get_default_qr_config(), **(template.back_qr_settings or {})}
            
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


def _normalize_school_name(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _find_template_dict_by_school(templates, school_name):
    normalized = _normalize_school_name(school_name)
    if not normalized:
        return None
    for template in templates or []:
        if _normalize_school_name(template.get("school_name")) == normalized:
            return template
    return None


def _student_session_school_name():
    return (session.get("student_school_name") or "").strip()


def _is_admin_session():
    return bool(session.get("admin"))


def _current_session_email():
    return (session.get("student_email") or "").strip().lower()


def _student_school_access_allowed(student_school_name):
    if _is_admin_session() and session.get("admin_role") != "school_admin":
        return True
    locked_school = _student_session_school_name()
    if not locked_school:
        return True
    return _normalize_school_name(locked_school) == _normalize_school_name(student_school_name)


def store_template_upload_asset(file_storage, *, side_label):
    if file_storage is None or not file_storage.filename:
        raise ValueError(f"{side_label} template file is required")
    if not file_storage.filename.lower().endswith(('.pdf', '.jpg', '.jpeg', '.png')):
        raise ValueError(f"Invalid {side_label.lower()} template format. Use PDF, JPG, or PNG")

    filename = secure_filename(file_storage.filename)
    file_bytes = io.BytesIO()
    file_storage.save(file_bytes)
    file_bytes.seek(0)
    raw_bytes = file_bytes.getvalue()
    file_ext = os.path.splitext(filename)[1].lower()
    is_pdf_upload = file_ext == ".pdf"
    upload_payload = raw_bytes

    if is_pdf_upload:
        try:
            pdf_header_pos = raw_bytes.find(b"%PDF")
            if pdf_header_pos < 0:
                raise ValueError("Uploaded file does not contain a PDF header.")
            upload_payload = raw_bytes[pdf_header_pos:]
            if len(upload_payload) < 128:
                raise ValueError("Uploaded PDF is too small and appears truncated.")
            pdf_doc = fitz.open(stream=upload_payload, filetype="pdf")
            if pdf_doc.page_count < 1:
                pdf_doc.close()
                raise ValueError("Uploaded PDF has no pages.")
            _ = pdf_doc[0].get_pixmap(dpi=72)
            pdf_doc.close()
        except Exception as pdf_err:
            raise ValueError(f"Uploaded {side_label.lower()} PDF is invalid: {pdf_err}")
    else:
        try:
            test_img = Image.open(io.BytesIO(raw_bytes))
            test_img.verify()
        except Exception as img_err:
            raise ValueError(f"Uploaded {side_label.lower()} image is invalid: {img_err}")

    templates_dir = os.path.join(STATIC_DIR, "templates_uploads")
    os.makedirs(templates_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    stored_name = f"{ts}_{uuid.uuid4().hex}_{filename}"
    local_abs_path = os.path.join(templates_dir, stored_name)
    with open(local_abs_path, "wb") as local_file:
        local_file.write(upload_payload)
    local_rel_filename = f"templates_uploads/{stored_name}"

    remote_url = None
    if STORAGE_BACKEND != "local":
        remote_url = upload_image(
            upload_payload,
            folder='id_card_templates',
            resource_type='raw' if is_pdf_upload else 'image',
            format='pdf' if is_pdf_upload else file_ext.lstrip('.') or None,
        )

        if not remote_url:
            raise RuntimeError(f"Failed to upload {side_label.lower()} template to Cloudinary")

        last_remote_err = None
        for _ in range(5):
            try:
                _ = load_template_smart(remote_url)
                last_remote_err = None
                break
            except Exception as remote_err:
                last_remote_err = remote_err
                time.sleep(0.8)
        if last_remote_err is not None:
            last_err_text = str(last_remote_err)
            if "network/DNS" in last_err_text:
                logger.warning(
                    "%s uploaded, but immediate Cloudinary read-check failed due network/DNS. Using local backup copy.",
                    side_label,
                )
                remote_url = None
            elif "HTTP 401" in last_err_text or "HTTP 403" in last_err_text or "unauthorized/forbidden" in last_err_text.lower():
                logger.warning(
                    "%s uploaded, but Cloudinary denied public access. Using local backup copy.",
                    side_label,
                )
                remote_url = None
            else:
                raise RuntimeError(
                    f"Uploaded {side_label.lower()} template is not readable from Cloudinary after retry. Details: {last_remote_err}"
                )

    return {
        "filename": local_rel_filename,
        "template_url": remote_url,
    }




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
        log_activity("Updated Template Settings", target=f"Template {template_id}", 
                     details=f"Orientation: {card_orientation}")
        
        logger.info(f"Updated settings for template ID {template_id}, orientation: {card_orientation}")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating template settings: {e}")
        raise
    
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
    


try:
    import mediapipe as mp
    mp_face = mp.solutions.face_detection
    face_detector = mp_face.FaceDetection(
        model_selection=0,
        min_detection_confidence=0.5
    )
except Exception as e:
    logger.warning("MediaPipe face detection disabled: %s", e)
    face_detector = None


def auto_crop_face_photo(photo_path, target_width=260, target_height=313):
    """
    Normalize a photo file in-place using the shared card photo behavior.
    """
    try:
        image_open = getattr(Image, "open_original", Image.open)
        pil_img = image_open(photo_path)
        final_img = _process_photo_pil(
            pil_img,
            target_width=target_width,
            target_height=target_height,
        )
        if final_img.mode == "RGBA":
            rgb = Image.new("RGB", final_img.size, (255, 255, 255))
            rgb.paste(final_img, mask=final_img.getchannel("A"))
            final_img = rgb
        elif final_img.mode != "RGB":
            final_img = final_img.convert("RGB")
        final_img.save(photo_path, "JPEG", quality=95, subsampling=0)
        return True

    except Exception as e:
        logger.exception(f"Smart crop failed: {e}")
        try:
            image_open = getattr(Image, "open_original", Image.open)
            return _fallback_center_crop(image_open(photo_path), photo_path, target_width, target_height)
        except:
            return False


def _fallback_center_crop(pil_img, save_path, target_w, target_h):
    """
    Fallback that respects EXIF rotation and aspect ratio.
    """
    final = _process_photo_pil(
        pil_img,
        target_width=target_w,
        target_height=target_h,
    )
    if final.mode == "RGBA":
        rgb = Image.new("RGB", final.size, (255, 255, 255))
        rgb.paste(final, mask=final.getchannel("A"))
        final = rgb
    elif final.mode != "RGB":
        final = final.convert("RGB")
    final.save(save_path, "JPEG", quality=95)
    return True


def _crop_with_padding(pil_img, crop_box, fill_rgb=(255, 255, 255)):
    x1, y1, x2, y2 = [int(round(v)) for v in crop_box]
    crop_w = max(1, x2 - x1)
    crop_h = max(1, y2 - y1)
    src = pil_img.convert("RGB")
    canvas = Image.new("RGB", (crop_w, crop_h), fill_rgb)

    src_x1 = max(0, x1)
    src_y1 = max(0, y1)
    src_x2 = min(src.width, x2)
    src_y2 = min(src.height, y2)
    if src_x2 <= src_x1 or src_y2 <= src_y1:
        return canvas

    region = src.crop((src_x1, src_y1, src_x2, src_y2))
    paste_x = max(0, -x1)
    paste_y = max(0, -y1)
    canvas.paste(region, (paste_x, paste_y))
    return canvas


def _center_crop_box(img_w, img_h, target_ratio):
    if img_w <= 0 or img_h <= 0:
        return (0, 0, max(1, img_w), max(1, img_h))
    current_ratio = float(img_w) / float(img_h)
    if current_ratio > target_ratio:
        crop_w = max(1, int(round(img_h * target_ratio)))
        left = int(round((img_w - crop_w) / 2.0))
        return (left, 0, left + crop_w, img_h)
    crop_h = max(1, int(round(img_w / target_ratio)))
    top = int(round((img_h - crop_h) / 2.0))
    return (0, top, img_w, top + crop_h)


def _detect_face_crop_box(pil_img, target_width, target_height):
    if not face_detector:
        return None
    try:
        rgb_img = pil_img.convert("RGB")
        img_np = np.array(rgb_img)
        h_orig, w_orig = img_np.shape[:2]
        if h_orig <= 0 or w_orig <= 0:
            return None

        results = face_detector.process(img_np.copy())
        if not results or not results.detections:
            return None

        detection = max(results.detections, key=lambda d: d.score[0])
        box = detection.location_data.relative_bounding_box
        face_h = max(1, int(box.height * h_orig))
        face_cx = int((box.xmin + (box.width / 2.0)) * w_orig)
        face_cy = int((box.ymin + (box.height / 2.0)) * h_orig)

        target_ratio = float(target_width) / float(max(1, target_height))
        face_to_image_ratio = 0.45
        face_center_y_ratio = 0.51
        crop_h = max(1, int(round(face_h / face_to_image_ratio)))
        crop_w = max(1, int(round(crop_h * target_ratio)))

        x1 = face_cx - (crop_w // 2)
        y1 = face_cy - int(round(crop_h * face_center_y_ratio))
        return (x1, y1, x1 + crop_w, y1 + crop_h)
    except Exception as exc:
        logger.warning("Face detection crop fallback triggered: %s", exc)
        return None


def _process_photo_pil(pil_img, target_width=260, target_height=313, cache_key_extra=None):
    """
    Normalize a student photo to the requested card frame and return RGBA.
    Now includes Redis caching for performance.
    """
    try:
        # 🔑 Build cache key
        try:
            img_bytes = pil_img.tobytes()
        except Exception:
            buf_tmp = io.BytesIO()
            pil_img.save(buf_tmp, format="PNG")
            img_bytes = buf_tmp.getvalue()

        cache_key = _redis_cache_key(
            "processed_photo",
            img_bytes,
            target_width,
            target_height,
            cache_key_extra
        )

        # 🔍 Try cache
        cached = _redis_get(cache_key)
        if cached:
            try:
                return Image.open(io.BytesIO(cached)).convert("RGBA")
            except Exception:
                pass

        # ================= ORIGINAL LOGIC =================

        pil_img.load()
        pil_img = pil_img.copy()

        try:
            pil_img = ImageOps.exif_transpose(pil_img)
        except Exception:
            pass

        target_width = max(1, int(target_width or 1))
        target_height = max(1, int(target_height or 1))

        base_img = pil_img.convert("RGB")
        fill_rgb = (255, 255, 255)
        target_ratio = float(target_width) / float(target_height)

        crop_box = _detect_face_crop_box(base_img, target_width, target_height)
        if crop_box is None:
            crop_box = _center_crop_box(base_img.width, base_img.height, target_ratio)

        cropped_img = _crop_with_padding(base_img, crop_box, fill_rgb=fill_rgb)

        result_img = cropped_img.convert("RGBA")

        if result_img.size != (target_width, target_height):
            result_img = ImageOps.fit(result_img, (target_width, target_height), Image.Resampling.LANCZOS)

        # 💾 Save to Redis
        try:
            buf = io.BytesIO()
            result_img.save(buf, format="PNG", optimize=True, compress_level=6)
            _redis_set(cache_key, buf.getvalue())
        except Exception as e:
            logger.warning(f"Photo cache save failed: {e}")

        return result_img

    except Exception as e:
        logger.warning(f"Photo processing failed: {e}, returning original")

        fallback = pil_img.convert("RGBA") if pil_img.mode != "RGBA" else pil_img.copy()

        if fallback.size != (max(1, int(target_width or 1)), max(1, int(target_height or 1))):
            fallback = ImageOps.fit(
                fallback,
                (max(1, int(target_width or 1)), max(1, int(target_height or 1))),
                Image.Resampling.LANCZOS
            )

        return fallback

def _read_uploaded_file_bytes(file_storage, *, file_label="file"):
    """Read uploaded bytes robustly so we never silently persist empty files."""
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise ValueError(f"{file_label.capitalize()} is required.")

    raw_bytes = b""
    try:
        stream = getattr(file_storage, "stream", None)
        if stream is not None:
            try:
                stream.seek(0)
            except Exception:
                pass
        raw_bytes = file_storage.read() or b""
        if not raw_bytes and hasattr(file_storage, "save"):
            buffer = io.BytesIO()
            file_storage.save(buffer)
            raw_bytes = buffer.getvalue() or b""
    finally:
        stream = getattr(file_storage, "stream", None)
        if stream is not None:
            try:
                stream.seek(0)
            except Exception:
                pass

    if not raw_bytes:
        raise ValueError(f"Uploaded {file_label} is empty. Please choose the image again.")
    return raw_bytes


def _prepare_uploaded_student_photo_bytes(file_storage, photo_settings=None):
    """
    Validate and normalize an uploaded student photo to JPEG bytes.
    Falls back to a normalized original if smart processing fails.
    """
    photo_settings = photo_settings or {}
    raw_bytes = _read_uploaded_file_bytes(file_storage, file_label="photo")

    try:
        source_img = Image.open(io.BytesIO(raw_bytes))
        source_img.load()
    except Exception as exc:
        raise ValueError(f"Uploaded photo is not a valid image: {exc}") from exc

    source_img = ImageOps.exif_transpose(source_img).convert("RGB")
    try:
        processed_img = _process_photo_pil(
            source_img,
            target_width=photo_settings.get("photo_width", 260),
            target_height=photo_settings.get("photo_height", 313),
        )
        if processed_img is None:
            processed_img = source_img
    except Exception as exc:
        logger.warning("Failed to process uploaded photo '%s': %s", getattr(file_storage, "filename", ""), exc)
        processed_img = source_img

    if processed_img.mode == "RGBA":
        flattened = Image.new("RGB", processed_img.size, (255, 255, 255))
        flattened.paste(processed_img, mask=processed_img.getchannel("A"))
        processed_img = flattened
    elif processed_img.mode != "RGB":
        processed_img = processed_img.convert("RGB")

    output = io.BytesIO()
    processed_img.save(output, format="JPEG", quality=95)
    processed_bytes = output.getvalue()
    if not processed_bytes:
        raise ValueError("Processed photo is empty after conversion. Please try another image.")
    return processed_bytes


def _write_binary_file_atomic(path, payload):
    """Write bytes atomically and refuse empty output files."""
    data = payload if isinstance(payload, bytes) else bytes(payload or b"")
    if not data:
        raise ValueError(f"Refusing to write empty file: {os.path.basename(path)}")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp_{uuid.uuid4().hex}"
    with open(tmp_path, "wb") as fh:
        fh.write(data)
    if os.path.getsize(tmp_path) <= 0:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise ValueError(f"Failed to save photo bytes for {os.path.basename(path)}")
    os.replace(tmp_path, path)
    return path

# Ensure logger is defined
logger = logging.getLogger(__name__)

import socket
import smtplib
from email.mime.text import MIMEText
import logging

# Ensure logger is defined
logger = logging.getLogger(__name__)

import socket
import smtplib
import ssl
from email.mime.text import MIMEText
import logging

# Ensure logger is defined
logger = logging.getLogger(__name__)

def send_email(to, subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = os.environ.get("EMAIL_FROM")
    msg['To'] = to

    server = None
    try:
        # 1. Configuration (Force Port 465 for SSL)
        smtp_server = "smtp.gmail.com"
        smtp_port = 465
        password = os.environ.get("EMAIL_PASSWORD")
        
        logger.info(f"📧 Sending email to {to} via {smtp_server}:{smtp_port}...")

        # 2. FORCE IPv4 (Fixes 'Network is unreachable')
        # We manually resolve the IP to ensure we don't accidentally use IPv6
        addr_info = socket.getaddrinfo(smtp_server, smtp_port, socket.AF_INET, socket.SOCK_STREAM)
        family, socktype, proto, canonname, sa = addr_info[0]
        target_ip = sa[0]
        
        logger.info(f"🔗 Connecting to Gmail IPv4: {target_ip}")

        # 3. Connect via SMTP_SSL (Implicit SSL)
        # We increase timeout to 30s to handle network lag
        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(target_ip, smtp_port, context=context, timeout=30)
        
        # 4. Login & Send
        logger.info("🔑 Logging in...")
        server.login(msg['From'], password)
        server.send_message(msg)
        server.quit()
        
        logger.info(f"✅ Email sent successfully to {to}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to send email: {e}")
        return False
    finally:
        if server:
            try:
                server.quit()
            except:
                pass
# ================== Landing Page Routes ==================
@app.route("/")
def landing():
    """Landing page route"""
    return render_template("landing_page.html")

@app.route("/home")
def home():
    """Alternative home route"""
    return redirect(url_for("landing"))

@app.route("/documentation")
def documentation():
    """Documentation page"""
    return render_template("documentation.html")

@app.route("/user_guide")
def user_guide():
    """Admin user guide page"""
    if not session.get("admin") and not session.get("student_email"):
        return redirect(url_for("login"))
    return render_template("admin_user_guide.html")

@app.route("/about")
def about():
    """About page"""
    return render_template("about.html")

# ================== Auth ==================

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    if request.method == "POST":
        # Load from Environment Variables
        env_user = os.environ.get("ADMIN_USERNAME")
        env_hash = os.environ.get("ADMIN_PASSWORD_HASH")

        # Security Check: Ensure env vars are set
        if not env_user or not env_hash:
            logger.error("Admin credentials not set in environment variables!")
            return render_template("login.html", error="Server configuration error. Contact support."), 500

        # Check Username and Password Hash
        username_input = request.form.get("username")
        password_input = request.form.get("password")

        # 1. Check DB for RBAC user (School Admin or custom Super Admin)
        admin_user = AdminUser.query.filter(db.func.lower(AdminUser.username) == username_input.lower()).first()
        
        if admin_user and check_password_hash(admin_user.password_hash, password_input):
            session["admin"] = True
            session["admin_role"] = admin_user.role
            session["admin_school"] = admin_user.school_name
            logger.info(f"Admin logged in successfully: {username_input} ({admin_user.role})")
            return redirect("/admin")
            
        # 2. Fallback to Root Super Admin via Env Vars
        if env_user and env_hash and username_input == env_user and check_password_hash(env_hash, password_input):
            session["admin"] = True
            session["admin_role"] = "super_admin"
            session["admin_school"] = None
            logger.info("SuperAdmin logged in via environment variables")
            return redirect("/admin")
            
        logger.warning("Failed login attempt: Invalid credentials")
        return render_template("login.html", error="Invalid login credentials"), 401
            
    return render_template("login.html")

@app.route("/student_login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def student_login():
    templates = get_templates()
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        school_name = request.form.get("school_name", "").strip()
        selected_template = _find_template_dict_by_school(templates, school_name)
      
        logger.info(f"Login attempt for email: {email}, school: {school_name}")
      
        if not email or not password or not school_name:
            error = "All fields are required."
            logger.warning("Login failed: Missing required fields")
        elif not selected_template:
            error = "Selected school is not available."
            logger.warning("Login failed: Unknown school selection '%s'", school_name)
        else:
            try:
                accounts = Student.query.filter(
                    db.func.lower(Student.email) == db.func.lower(email),
                    Student.password.isnot(None),
                ).order_by(Student.created_at.asc()).all()
                student = next(
                    (row for row in accounts if _normalize_school_name(row.school_name) == _normalize_school_name(school_name)),
                    None,
                )
                
                if not student:
                    error = "No account found for this email and school."
                    logger.warning("Login failed: No account found for email %s in school %s", email, school_name)
                elif check_password_hash(student.password, password):
                    session["student_email"] = student.email
                    session["student_school_name"] = student.school_name or selected_template["school_name"]
                    session["student_template_id"] = selected_template["id"]
                    logger.info(f"Login successful for email: {student.email}")
                    # CHANGED: Redirect to index instead of landing page
                    return redirect(url_for("index"))
                else:
                    error = "Invalid password."
                    logger.warning(f"Login failed: Invalid password for email {email}")
            except Exception as e:
                error = f"Database error: {str(e)}"
                logger.error(f"Database error during login for email {email}: {e}")
  
    return render_template("login_student.html", templates=templates, error=error)

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("3 per minute")
def register():
    templates = get_templates()
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        school_name = request.form.get("school_name", "").strip()
        name = request.form.get("name", "").strip()
        selected_template = _find_template_dict_by_school(templates, school_name)
      
        if not all([email, password, confirm_password, school_name, name]):
            error = "All fields (name, email, password, confirm password, school name) are required."
            logger.warning("Registration failed: Missing required fields")
        elif not selected_template:
            error = "Selected school is not available."
            logger.warning("Registration failed: Unknown school selection '%s'", school_name)
        elif not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
            error = "Invalid email address."
            logger.warning(f"Registration failed: Invalid email {email}")
        elif password != confirm_password:
            error = "Passwords do not match."
            logger.warning("Registration failed: Passwords do not match")
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
            logger.warning("Registration failed: Password too short")
        elif len(name) < 2:
            error = "Name must be at least 2 characters."
            logger.warning("Registration failed: Name too short")
        else:
            try:
                existing_student = Student.query.filter(
                    db.func.lower(Student.email) == db.func.lower(email),
                    Student.password.isnot(None),
                ).first()
                
                if existing_student:
                    error = "Email already registered."
                    logger.warning(f"Registration failed: Email {email} already registered")
                else:
                    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
                    if not hashed_password:
                        error = "Failed to hash password."
                        logger.error("Registration failed: Password hashing error")
                    else:
                        student = Student(
                            name=name,
                            email=email.lower(),
                            password=hashed_password,
                            school_name=selected_template["school_name"],
                            created_at = datetime.now(timezone.utc)

                        )
                        db.session.add(student)
                        db.session.commit()
                        
                        session["student_email"] = email.lower()
                        session["student_school_name"] = selected_template["school_name"]
                        session["student_template_id"] = selected_template["id"]
                        logger.info(f"Registered new student: {email}, name: {name}")
                        # CHANGED: Redirect to index instead of landing page
                        return redirect(url_for("index"))
            except Exception as e:
                error = f"Database error: {str(e)}"
                logger.error(f"Database error during registration for email {email}: {e}")
    return render_template("register.html", templates=templates, error=error)

# Search for the existing @app.route("/reset_password"...) and replace it with this:

# -------------------------------------------------------------------------
# 1. REQUEST RESET LINK (User enters email here)
# -------------------------------------------------------------------------
@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        
        # Check if student exists
        student = Student.query.filter(db.func.lower(Student.email) == email).first()
        
        if student:
            # Generate secure token (Contains email + timestamp)
            token = serializer.dumps(email, salt='password-reset-salt')
            
            # Generate the link pointing to the NEW reset route
            link = url_for('reset_password_with_token', token=token, _external=True)
            
            # Send Email
            subject = "Password Reset Request"
            body = f"""Hello {student.name},

You requested to reset your password. Please click the link below to set a new one:

{link}

This link is valid for 1 hour.
If you did not request this, please ignore this email.
"""
            # --- UPDATE: Check return value of send_email ---
            if send_email(email, subject, body):
                logger.info(f"Password reset link sent to {email}")
                flash("A reset link has been sent to your email. Please check your inbox.", "success")
            else:
                logger.error(f"Failed to send reset email to {email}")
                flash("Error sending email. Please try again later.", "error")
            # -----------------------------------------------
        else:
            # Security: Generic message to prevent email enumeration
            flash("If an account exists with that email, a reset link has been sent.", "info")
            
        return redirect(url_for('student_login'))
        
    # Render the form where they type their email
    return render_template("forgot_password.html")

# -------------------------------------------------------------------------
# 2. SET NEW PASSWORD (User clicks link -> Enters new password)
# -------------------------------------------------------------------------
@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password_with_token(token):
    try:
        # Verify Token (Expires in 3600 seconds = 1 hour)
        email = serializer.loads(token, salt='password-reset-salt', max_age=3600)
    except SignatureExpired:
        flash("The reset link has expired. Please request a new one.", "error")
        return redirect(url_for('forgot_password'))
    except BadTimeSignature:
        flash("Invalid reset link.", "error")
        return redirect(url_for('forgot_password'))
    
    # If POST, they are submitting the new password
    if request.method == "POST":
        password = request.form.get("password")
        confirm = request.form.get("confirm_password")
        
        if not password or not confirm:
            flash("Both password fields are required.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        else:
            try:
                student = Student.query.filter(db.func.lower(Student.email) == email).first()
                if student:
                    # Hash and Save
                    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
                    student.password = hashed_password
                    db.session.commit()
                    
                    flash("Your password has been successfully reset! Please log in.", "success")
                    return redirect(url_for("student_login"))
                else:
                    flash("User not found.", "error")
            except Exception as e:
                db.session.rollback()
                logger.error(f"Database error during password reset: {e}")
                flash("An error occurred. Please try again.", "error")

    # Render the form where they type the NEW password
    return render_template("reset_password_token.html", token=token)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("student_login"))

@app.before_request
def require_login():
    admin_routes = ["/admin", "/upload_template", "/delete_all", "/upload_font", "/update_font",
                    "/update_photo_position", "/delete_student", "/export_csv", "/download_template",
                    "/remove_template", "/update_duplicate_settings", "/delete_pdf", "/update_template_settings",
                    "/admin_student_credentials", "/admin_add_student_credential", "/admin_update_student_credential",
                    "/admin_delete_student_credential", "/admin_reset_student_password", "/admin/student_preview",
                    "/admin/generate_preview", "/admin/download_student_pdf"]
    if request.path in admin_routes and not session.get("admin"):
        logger.warning(f"Unauthorized access attempt to {request.path}")
        return redirect(url_for("login"))

# ================== Student Routes ==================
@student_bp.route("/update_email", methods=["POST"])
def update_email():
    if 'student_email' not in session:
        flash('Please log in to update your email.', 'error')
        return redirect(url_for('student_login'))
  
    email = request.form.get('email')
    if not email or not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
        flash('Invalid email address.', 'error')
        return redirect(url_for('admin'))
  
    try:
        student = Student.query.filter_by(email=session['student_email']).first()
        if not student:
            flash('No matching student record found.', 'error')
            return redirect(url_for('admin'))
        
        # Check if email already in use by another student
        existing = Student.query.filter(
            Student.email == email,
            Student.id != student.id
        ).first()
        
        if existing:
            flash('Email already in use.', 'error')
            return redirect(url_for('admin'))
        
        student.email = email
        session['student_email'] = email
        db.session.commit()
        
        flash('Email updated successfully.', 'success')
        
    except Exception as e:
        flash(f'Error updating email: {e}', 'error')
        logger.error(f"Error updating email: {e}")
        return redirect(url_for('admin'))
  
    return redirect(url_for('admin'))

@student_bp.route("/update_password", methods=["POST"])
def update_password():
    if 'student_email' not in session:
        flash('Please log in to update your password.', 'error')
        return redirect(url_for('student_login'))
  
    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')
    if not all([current_password, new_password, confirm_password]):
        flash('All password fields are required.', 'error')
        return redirect(url_for('admin'))
  
    if new_password != confirm_password:
        flash('New password and confirmation do not match.', 'error')
        return redirect(url_for('admin'))
  
    if len(new_password) < 6:
        flash('New password must be at least 6 characters.', 'error')
        return redirect(url_for('admin'))
    try:
        student = Student.query.filter_by(email=session['student_email']).first()
        
        if not student or not student.password:
            flash('Current password is incorrect.', 'error')
            return redirect(url_for('admin'))
        
        try:
            if not check_password_hash(student.password, current_password):
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('admin'))
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            flash('Authentication error. Please try again.', 'error')
            return redirect(url_for('admin'))
        
        hashed_password = generate_password_hash(new_password)
        student.password = hashed_password
        db.session.commit()
        
        flash('Password updated successfully.', 'success')
    except Exception as e:
        flash(f'Error updating password: {e}', 'error')
        logger.error(f"Error updating password: {e}")
        return redirect(url_for('admin'))
  
    return redirect(url_for('admin'))

# ================== Preview Routes ==================
@app.route("/admin/student_preview/<int:student_id>")
def admin_student_preview(student_id):
    """Get student preview data"""
    if not session.get("admin") and not session.get("student_email"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
  
    try:
        student = db.session.get(Student, student_id)
        
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404
        
        preview_url, back_preview_url = resolve_student_card_preview_urls(student)
        
        return jsonify({
            "success": True,
            "name": student.name,
            "class_name": student.class_name,
            "preview_url": with_cache_bust(preview_url or url_for('static', filename=os.path.basename(PLACEHOLDER_PATH))),
            "back_preview_url": with_cache_bust(back_preview_url) if back_preview_url else None,
            "pdf_url": getattr(student, 'pdf_url', None),
            "has_preview": preview_url is not None,
            "has_back_preview": back_preview_url is not None,
        })
    except Exception as e:
        logger.error(f"Error fetching student preview: {e}")
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route("/admin/generate_preview/<int:student_id>")
def generate_student_preview(student_id):
    """Generate a fresh preview for a student with Safe Font Fallback"""
    if not session.get("admin") and not session.get("student_email"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        student = db.session.get(Student, student_id)
        
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404
        
        template = db.session.get(Template, student.template_id) if student.template_id else None
        
        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404
        
        # Load template settings
        font_settings = get_default_font_config()
        photo_settings = get_default_photo_config()
        qr_settings = get_default_qr_config()
        
        if template.font_settings:
            loaded_font = template.font_settings.copy()
            if 'font_color' in loaded_font and 'label_font_color' not in loaded_font:
                loaded_font['label_font_color'] = loaded_font['font_color']
                loaded_font['value_font_color'] = loaded_font['font_color']
            font_settings = {**font_settings, **loaded_font}
        
        if template.photo_settings:
            photo_settings = {**photo_settings, **template.photo_settings}
        
        if template.qr_settings:
            qr_settings = {**qr_settings, **template.qr_settings}
        
        # Adjust settings based on orientation
        photo_settings = get_photo_settings_for_orientation(student.template_id, photo_settings)
        font_settings = get_font_settings_for_orientation(student.template_id, font_settings)
        
        # Generate preview
        # Get template path (Cloudinary URL or legacy local path)
        template_path = get_template_path(student.template_id)
        if not template_path:
            return jsonify({"success": False, "error": "Template not found"}), 404
        
        try:
            card_width, card_height = get_card_size(student.template_id)
            template_img = load_template_smart(template_path)
            template_img = template_img.resize((card_width, card_height))
            
            draw = ImageDraw.Draw(template_img)
            
            # Load font paths
            FONT_BOLD_PATH = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
            FONT_REGULAR_PATH = os.path.join(FONTS_FOLDER, font_settings["font_regular"])
            
            # Handle font colors safely
            try:
                label_font_color = tuple(font_settings.get("label_font_color", [0, 0, 0]))
                value_font_color = tuple(font_settings.get("value_font_color", [0, 0, 0]))
                colon_font_color = tuple(font_settings.get("colon_font_color", list(label_font_color)))
            except Exception as e:
                logger.warning(f"Error parsing font colors: {e}, using default black")
                label_font_color = (0, 0, 0)
                value_font_color = (0, 0, 0)
                colon_font_color = label_font_color
            
            # Apply text case transformation
            text_case = font_settings.get("text_case", "normal")
            show_label_colon = bool(font_settings.get("show_label_colon", True))
            align_label_colon = bool(font_settings.get("align_label_colon", True))
            label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)
            field_keys = ["NAME", "F_NAME", "CLASS", "DOB", "ADDRESS", "MOBILE"]
            values = [
                apply_text_case(student.name, text_case),
                apply_text_case(student.father_name, text_case),
                apply_text_case(student.class_name, text_case),
                student.dob, 
                apply_text_case(student.address, text_case),
                student.phone 
            ]
            
            # Draw text
            label_x = font_settings["label_x"]
            value_x = font_settings["value_x"]
            start_y = font_settings["start_y"]
            line_height = font_settings["line_height"]

            # Template language + direction
            lang = (getattr(template, "language", "english") or "english").strip().lower()
            direction = (getattr(template, "text_direction", "ltr") or "ltr").strip().lower()

            # Fallback for old/misaligned templates where RTL is set but language stayed default.
            if direction == "rtl" and lang == "english":
                lang = "urdu"

            std_labels = {
                'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
                'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
                'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
                'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
            }
            labels_map = std_labels.get(lang, std_labels['english'])
            labels = [
                labels_map["NAME"],
                labels_map["F_NAME"],
                labels_map["CLASS"],
                labels_map["DOB"],
                labels_map["ADDRESS"],
                labels_map["MOBILE"],
            ]

            current_y = get_initial_flow_y_for_side(template, font_settings, side="front")
            for i, (label, value) in enumerate(zip(labels, values)):
                y = current_y
                field_key = field_keys[i] if i < len(field_keys) else None
                layout_item = resolve_field_layout(template, field_key, label_x, value_x, y)
                label_x_eff = layout_item["label_x"]
                value_x_eff = layout_item["value_x"]
                label_y_eff = layout_item["label_y"]
                value_y_eff = layout_item["value_y"]
                label_visible = layout_item["label_visible"]
                value_visible = layout_item["value_visible"]
                label_fill = layout_item.get("label_color") or label_font_color
                value_fill = layout_item.get("value_color") or value_font_color
                colon_fill = layout_item.get("colon_color") or colon_font_color
                label_font_size_eff = max(1, int(layout_item.get("label_font_size") or font_settings["label_font_size"]))
                value_font_size_eff = max(1, int(layout_item.get("value_font_size") or font_settings["value_font_size"]))
                colon_font_size_eff = max(1, int(layout_item.get("colon_font_size") or label_font_size_eff))
                colon_y_eff = layout_item.get("colon_y", label_y_eff)
                colon_x_eff = layout_item.get("colon_x")
                colon_grow_eff = layout_item.get("colon_grow")
                max_value_width = template_img.width - value_x_eff - 50

                # Text shaping for correct Urdu/Arabic joining + RTL order (Hindi/English unchanged)
                display_label = process_text_for_drawing(apply_text_case(label, text_case), lang)
                display_value = process_text_for_drawing(apply_text_case(value, text_case), lang)
                label_text_final, colon_text_final = split_label_and_colon(
                    display_label,
                    lang,
                    direction,
                    include_colon=show_label_colon,
                    align_colon=align_label_colon,
                )

                if not field_consumes_layout_space(layout_item, value):
                    continue
                    advances_flow = field_advances_layout_flow(layout_item, value, separate_colon=bool(colon_text_final))
                if advances_flow:
                    current_y = max(int(current_y), int(label_y_eff), int(value_y_eff))

                # Language-aware Unicode font loading (no Arial for Arabic/Urdu/Hindi)
                label_font = load_font_dynamic(
                    FONT_BOLD_PATH, label_text_final, template_img.width, label_font_size_eff, language=lang
                )
                colon_font = load_font_dynamic(
                    FONT_BOLD_PATH, colon_text_final or ":", template_img.width, colon_font_size_eff, language=lang
                )
                value_font = load_font_dynamic(
                    FONT_REGULAR_PATH, display_value, max_value_width, value_font_size_eff, language=lang
                )

                # RTL layout: mirror X so text aligns from the right side (Y preserved)
                label_draw_x = flip_x_for_text_direction(
                    label_x_eff,
                    label_text_final,
                    label_font,
                    template_img.width,
                    direction,
                    draw=draw,
                    grow_mode=layout_item["label_grow"],
                )
                value_draw_x = flip_x_for_text_direction(
                    value_x_eff,
                    display_value,
                    value_font,
                    template_img.width,
                    direction,
                    draw=draw,
                    grow_mode=layout_item["value_grow"],
                )

                if label_visible:
                    draw.text(
                        (label_draw_x, label_y_eff),
                        label_text_final,
                        font=label_font,
                        fill=label_fill,
                        **get_draw_text_kwargs(label_text_final, lang),
                    )
                    draw_aligned_colon_pil(
                        draw,
                        template_img.width,
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
                    )
                if value_visible:
                    draw.text(
                        (value_draw_x, value_y_eff),
                        display_value,
                        font=value_font,
                        fill=value_fill,
                        **get_draw_text_kwargs(display_value, lang),
                    )
                if advances_flow:
                    current_y += line_height
            
            # Add photo (supports photo_url, local photo_filename, and legacy URL-in-photo_filename)
            if photo_settings.get("enable_photo", True):
                try:
                    photo_img = load_student_photo_rgba(
                        student,
                        photo_settings["photo_width"],
                        photo_settings["photo_height"],
                        timeout=8,
                        photo_settings=photo_settings,
                    )
                    if photo_img is None:
                        logger.error(f"Failed to load photo for student {getattr(student, 'id', 'unknown')}, using placeholder")
                        if os.path.exists(PLACEHOLDER_PATH):
                            ph_w = int(float(photo_settings.get("photo_width", 100) or 100))
                            ph_h = int(float(photo_settings.get("photo_height", 100) or 100))
                            photo_img = Image.open(PLACEHOLDER_PATH).convert("RGBA")
                            photo_img = ImageOps.fit(
                                photo_img, (ph_w, ph_h), Image.Resampling.LANCZOS
                            )
                    if photo_img:
                        radii = [
                            int(float(photo_settings.get("photo_border_top_left", 0) or 0)),
                            int(float(photo_settings.get("photo_border_top_right", 0) or 0)),
                            int(float(photo_settings.get("photo_border_bottom_right", 0) or 0)),
                            int(float(photo_settings.get("photo_border_bottom_left", 0) or 0))
                        ]
                        photo_img = round_photo(photo_img, radii)
                        template_img.paste(photo_img, (photo_settings["photo_x"], photo_settings["photo_y"]), photo_img)
                except Exception as e:
                    logger.error(f"Error adding photo to preview: {e}")
            
            # ---------------- QR CODE ----------------
            if qr_settings.get("enable_qr", False):
                qr_data = "SAMPLE_PREVIEW_DATA"
                if qr_settings.get("qr_data_type") == "student_id":
                    qr_data = json.dumps({
                        "student_id": str(student_id),
                        "name": student.name,
                        "school_name": student.school_name
                    })
                elif qr_settings.get("qr_data_type") == "url":
                    qr_data = qr_settings.get("qr_base_url", "") + str(student_id)
                elif qr_settings.get("qr_data_type") == "text":
                    qr_data = qr_settings.get("qr_custom_text", "Sample Text")
                elif qr_settings.get("qr_data_type") == "json":
                    qr_data = json.dumps({
                        "student_id": student_id,
                        "name": student.name,
                        "class": student.class_name,
                        "school_name": student.school_name
                    })

                qr_x = qr_settings.get("qr_x", 50)
                qr_y = qr_settings.get("qr_y", 50)
                qr_size = qr_settings.get("qr_size", 120)
                qr_img = generate_qr_code(qr_data, qr_settings, qr_size)
                qr_img = force_rgb(qr_img).resize((qr_size, qr_size))
                template_img.paste(qr_img, (qr_x, qr_y))

            # ---------------- BARCODE ----------------
            if qr_settings.get("enable_barcode", False):
                barcode_data = str(student_id)
                barcode_data_type = qr_settings.get("barcode_data_type", "student_id")
                if barcode_data_type == "url":
                    barcode_data = qr_settings.get("barcode_base_url", "") + str(student_id)
                elif barcode_data_type == "text":
                    barcode_data = qr_settings.get("barcode_custom_text", "Sample Text")
                elif barcode_data_type == "json":
                    barcode_data = json.dumps({
                        "student_id": student_id,
                        "name": student.name,
                        "class": student.class_name,
                        "school_name": student.school_name
                    })
                else:
                    barcode_data = str(student_id)

                barcode_x = qr_settings.get("barcode_x", 50)
                barcode_y = qr_settings.get("barcode_y", 200)
                barcode_w = max(40, int(qr_settings.get("barcode_width", 220)))
                barcode_h = max(30, int(qr_settings.get("barcode_height", 70)))
                barcode_img = generate_barcode_code128(barcode_data, qr_settings, width=barcode_w, height=barcode_h)
                barcode_img = force_rgb(barcode_img).resize((barcode_w, barcode_h))
                template_img.paste(barcode_img, (barcode_x, barcode_y))

            apply_layout_custom_objects_pil(template_img, template, font_settings, side="front", language=lang)
            
            
            # =====================================================
            # 🚨 FINAL JPEG SAFETY (ABSOLUTE LAST IMAGE OPERATION)
            # =====================================================
            template_img = force_rgb(template_img)
            
            logger.info(f"Preview image mode before save: {template_img.mode}")
            logger.warning(f"FINAL IMAGE MODE BEFORE SAVE: {template_img.mode}")
            buf = BytesIO()
            template_img.save(
                buf,
                format="JPEG",
                quality=95,
                subsampling=0,
                optimize=True
            )
            buf.seek(0)
            
            img_bytes = buf.getvalue()
            

            try:
                if STORAGE_BACKEND == "local":
                    os.makedirs(GENERATED_FOLDER, exist_ok=True)
                    preview_name = f"preview_{student_id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex}.jpg"
                    with open(os.path.join(GENERATED_FOLDER, preview_name), "wb") as fh:
                        fh.write(img_bytes)
                    preview_url = url_for('static', filename=f'generated/{preview_name}')
                else:
                    uploaded = upload_image(img_bytes, folder='generated')
                    preview_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
            except Exception as e:
                logger.error(f"Preview save failed: {e}")
                return jsonify({"success": False, "error": "Failed to save preview"}), 500

            back_preview_url = None
            if getattr(template, "is_double_sided", False):
                try:
                    student_id = getattr(student, "id", None)
                    render_scale = 1.0
                    school_name = getattr(template_obj, "school_name", None) or getattr(student, "school_name", "")

                    
                    back_img = _get_cached_final_card(
                        template,
                        student,
                        side="back",
                        student_id=student_id,
                        school_name=student.school_name,
                        render_scale=render_scale,
                    )

                    if back_img is not None:
                        back_img = force_rgb(back_img)
                        back_buf = BytesIO()
                        back_img.save(back_buf, format="JPEG", quality=95, subsampling=0, optimize=True)
                        back_buf.seek(0)
                        if STORAGE_BACKEND == "local":
                            back_name = f"preview_back_{student_id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex}.jpg"
                            with open(os.path.join(GENERATED_FOLDER, back_name), "wb") as fh:
                                fh.write(back_buf.getvalue())
                            back_preview_url = url_for('static', filename=f'generated/{back_name}')
                        else:
                            uploaded_back = upload_image(back_buf.getvalue(), folder='generated')
                            back_preview_url = uploaded_back if isinstance(uploaded_back, str) else uploaded_back.get('url')
                except Exception as back_exc:
                    logger.error(f"Back preview generation failed: {back_exc}")

            return jsonify({
                "success": True,
                "preview_url": with_cache_bust(preview_url),
                "back_preview_url": with_cache_bust(back_preview_url) if back_preview_url else None,
                "message": "Preview generated successfully"
            })
            
        except Exception as e:
            logger.error(f"Error generating preview: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({"success": False, "error": f"Preview generation failed: {str(e)}"}), 500
            
    except Exception as e:
        logger.error(f"Database error generating preview: {e}")
        return jsonify({"success": False, "error": "Database error"}), 500
    
@app.route("/test_preview")
def test_preview():
    """Test route to verify preview generation works"""
    try:
        # Create a simple test image
        test_img = Image.new("RGB", (800, 500), color="lightblue")
        draw = ImageDraw.Draw(test_img)
        draw.rectangle([50, 50, 750, 450], outline="blue", width=3)
        draw.text((100, 100), process_text_for_drawing("Preview Test - Working!", "english"), fill="black")
        draw.text((100, 150), process_text_for_drawing(f"Time: {datetime.now()}", "english"), fill="black")
      
        buf = BytesIO()
        test_img.save(buf, format='JPEG', quality=95)
        buf.seek(0)
        try:
            if STORAGE_BACKEND == "local":
                os.makedirs(GENERATED_FOLDER, exist_ok=True)
                test_name = f"test_preview_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex}.jpg"
                with open(os.path.join(GENERATED_FOLDER, test_name), "wb") as fh:
                    fh.write(buf.getvalue())
                test_url = url_for('static', filename=f'generated/{test_name}')
            else:
                uploaded = upload_image(buf.getvalue(), folder='generated')
                test_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
        except Exception as e:
            return jsonify({"success": False, "error": f"Save failed: {e}"}), 500

        return jsonify({"success": True, "message": "Preview test completed", "test_image": test_url})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/admin/download_student_pdf/<int:student_id>")
def download_student_pdf(student_id):
    """Download student PDF"""
    if not session.get("admin") and not session.get("student_email"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
  
    try:
        student = db.session.get(Student, student_id)
        
        # Prefer remote PDF URL stored on student.pdf_url
        if not student:
            return jsonify({"success": False, "error": "PDF not found"}), 404

        if getattr(student, 'pdf_url', None):
            return redirect(student.pdf_url)

        # Legacy fallback: serve local file
        if getattr(student, 'generated_filename', None):
            pdf_filename = student.generated_filename
            pdf_path = os.path.join(GENERATED_FOLDER, pdf_filename)
            if os.path.exists(pdf_path):
                if str(pdf_filename).lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    front_img = Image.open(pdf_path).convert("RGB")
                    append_images = []
                    back_name = getattr(student, "back_generated_filename", None)
                    if back_name:
                        back_path = os.path.join(GENERATED_FOLDER, str(back_name))
                        if os.path.exists(back_path):
                            append_images.append(Image.open(back_path).convert("RGB"))
                    pdf_buffer = io.BytesIO()
                    if append_images:
                        front_img.save(pdf_buffer, "PDF", resolution=300, save_all=True, append_images=append_images)
                    else:
                        front_img.save(pdf_buffer, "PDF", resolution=300)
                    pdf_buffer.seek(0)
                    download_name = f"{os.path.splitext(str(pdf_filename))[0]}.pdf"
                    return send_file(pdf_buffer, as_attachment=True, download_name=download_name, mimetype="application/pdf")
                return send_file(pdf_path, as_attachment=True, download_name=pdf_filename)

        return jsonify({"success": False, "error": "PDF file not found"}), 404
        
    except Exception as e:
        logger.error(f"Error downloading student PDF: {e}")
        return jsonify({"success": False, "error": "Database error"}), 500

# ================== Student Credential Management Routes ==================
@app.route("/admin_student_credentials")
def admin_student_credentials():
    if not session.get("admin"):
        return redirect(url_for("login"))
  
    try:
        students = Student.query.filter(
            Student.email.isnot(None),
            Student.email != ''
        ).order_by(Student.created_at.desc()).all()
        
        return render_template(
            "admin_student_credentials.html",
            students=students,
            success=request.args.get("success"),
            error=request.args.get("error")
        )
    except Exception as e:
        logger.error(f"Error fetching student credentials: {e}")
        return redirect(url_for("admin", error=f"Error fetching student credentials: {str(e)}"))

@app.route("/admin_add_student_credential", methods=["POST"])
def admin_add_student_credential():
    if not session.get("admin"):
        return redirect(url_for("login"))
  
    try:
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        school_name = request.form.get("school_name", "").strip()
      
        if not all([name, email, password, school_name]):
            return redirect(url_for("admin_student_credentials", error="All fields are required"))
      
        if not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
            return redirect(url_for("admin_student_credentials", error="Invalid email address"))
      
        if len(password) < 6:
            return redirect(url_for("admin_student_credentials", error="Password must be at least 6 characters"))
      
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
      
        # Check if email already exists
        existing = Student.query.filter_by(email=email).first()
        if existing:
            return redirect(url_for("admin_student_credentials", error="Email already registered"))
        
        # Insert new student credential
        student = Student(
            name=name,
            email=email,
            password=hashed_password,
            school_name=school_name,
            created_at = datetime.now(timezone.utc)

        )
        db.session.add(student)
        db.session.commit()
        
        logger.info(f"Admin added student credential: {email}")
        
        return redirect(url_for("admin_student_credentials", success="Student credential added successfully"))
        
    except Exception as e:
        logger.error(f"Error adding student credential: {e}")
        return redirect(url_for("admin_student_credentials", error=f"Error adding student credential: {str(e)}"))

@app.route("/admin_update_student_credential/<int:student_id>", methods=["POST"])
def admin_update_student_credential(student_id):
    if not session.get("admin"):
        return redirect(url_for("login"))
  
    try:
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        school_name = request.form.get("school_name", "").strip()
      
        if not all([name, email, school_name]):
            return redirect(url_for("admin_student_credentials", error="Name, email, and school name are required"))
      
        if not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
            return redirect(url_for("admin_student_credentials", error="Invalid email address"))
      
        student = db.session.get(Student, student_id)
        if not student:
            return redirect(url_for("admin_student_credentials", error="Student not found"))
        
        # Check if email already exists for other students
        existing = Student.query.filter(
            Student.email == email,
            Student.id != student_id
        ).first()
        
        if existing:
            return redirect(url_for("admin_student_credentials", error="Email already registered to another student"))
        
        student.name = name
        student.email = email
        student.school_name = school_name
        
        # Update password only if provided
        if password:
            if len(password) < 6:
                return redirect(url_for("admin_student_credentials", error="Password must be at least 6 characters"))
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
            student.password = hashed_password
        
        db.session.commit()
        logger.info(f"Admin updated student credential for ID: {student_id}")
        
        return redirect(url_for("admin_student_credentials", success="Student credential updated successfully"))
        
    except Exception as e:
        logger.error(f"Error updating student credential: {e}")
        return redirect(url_for("admin_student_credentials", error=f"Error updating student credential: {str(e)}"))

@app.route("/admin_delete_student_credential/<int:student_id>", methods=["POST"])
def admin_delete_student_credential(student_id):
    if not session.get("admin"):
        return redirect(url_for("login"))
  
    try:
        student = db.session.get(Student, student_id)
        if not student:
            return redirect(url_for("admin_student_credentials", error="Student not found"))
        
        # Check if student has any ID cards created
        if student.photo_filename or student.generated_filename:
            return redirect(url_for("admin_student_credentials", error="Cannot delete student with existing ID cards. Delete the ID cards first."))
        
        db.session.delete(student)
        db.session.commit()
        
        logger.info(f"Admin deleted student credential for ID: {student_id}")
        
        return redirect(url_for("admin_student_credentials", success="Student credential deleted successfully"))
        
    except Exception as e:
        logger.error(f"Error deleting student credential: {e}")
        return redirect(url_for("admin_student_credentials", error=f"Error deleting student credential: {str(e)}"))

@app.route("/admin_reset_student_password/<int:student_id>", methods=["POST"])
def admin_reset_student_password(student_id):
    if not session.get("admin"):
        return redirect(url_for("login"))
  
    try:
        student = db.session.get(Student, student_id)
        if not student:
            return redirect(url_for("admin_student_credentials", error="Student not found"))
        
        # Generate a random password
        new_password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
        hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
        
        student.password = hashed_password
        db.session.commit()
        
        # Send the new password
        if student.email:
            try:
                send_email(student.email, "Password Reset", f"Your new password is: {new_password}")
            except Exception as email_error:
                logger.error(f"Error sending email: {email_error}")
                return redirect(url_for("admin_student_credentials", success=f"Password reset successfully. New password: {new_password} (Email failed to send)"))
        
        logger.info(f"Admin reset password for student ID: {student_id}")
        
        return redirect(url_for("admin_student_credentials", success=f"Password reset successfully. New password sent to student's email."))
        
    except Exception as e:
        logger.error(f"Error resetting student password: {e}")
        return redirect(url_for("admin_student_credentials", error=f"Error resetting password: {str(e)}"))

# ================== UPDATED INDEX ROUTE (Admin Email Bypass) ==================

# ================== MAIN INDEX ROUTE ==================
@app.route("/index", methods=["GET", "POST"])
def index():
    # 1. Security Check
    if not session.get("student_email") and not _is_admin_session():
        return redirect(url_for("student_login"))

    # 2. Init Variables
    generated_url = None
    back_generated_url = None
    download_url = None
    form_data = {}
    success = None
    error = None
    templates = get_templates()
    show_fetch = True
    unique_edit_id = None
    
    # --- ADDED: Deadline Info Variable ---
    deadline_info = None
    # -------------------------------------
    
    # Auto-select template based on school name
    school_name = _student_session_school_name()
    if not school_name and _is_admin_session() and session.get("admin_role") == "school_admin":
        school_name = (session.get("admin_school") or "").strip()
    locked_template = _find_template_dict_by_school(templates, school_name)
    
    is_super_admin = _is_admin_session() and session.get("admin_role") != "school_admin"
    if is_super_admin:
        student_school_locked = False
        selected_template_id = locked_template["id"] if locked_template else None
    else:
        student_school_locked = locked_template is not None
        selected_template_id = locked_template["id"] if locked_template else None
        if student_school_locked:
            templates = [locked_template]
        elif school_name:
            error = f"Your school template '{school_name}' is not available right now. Please contact the administrator."

    # --- ADDED: Calculate Deadline Info for Display ---
    # Determine which template is active (from form POST or default selection)
    current_tid = selected_template_id

    if request.method == "POST" and 'edit_student_id' in session:
        edit_id = session.get('edit_student_id')
        student_for_deadline = db.session.get(Student, edit_id)
        if student_for_deadline:
            current_tid = student_for_deadline.template_id
    elif student_school_locked:
        current_tid = selected_template_id
    else:
        current_tid = request.form.get("template_id") or selected_template_id

    if current_tid:
        try:
            # Find template data
            tmpl = next((t for t in templates if str(t['id']) == str(current_tid)), None)
            if tmpl and tmpl.get('deadline'):
                dd = datetime.fromisoformat(tmpl['deadline'])
                now = datetime.now()
                
                if now < dd:
                    # Active Deadline
                    diff = dd - now
                    days = diff.days
                    hours, remainder = divmod(diff.seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    deadline_info = {
                        "active": True,
                        "expired": False,
                        "date": dd.strftime("%d %B %Y, %I:%M %p"),
                        "days": days,
                        "hours": hours,
                        "minutes": minutes
                    }
                else:
                    # Expired Deadline
                    deadline_info = {
                        "active": True,
                        "expired": True,
                        "date": dd.strftime("%d %B %Y, %I:%M %p")
                    }
        except Exception as e:
            logger.error(f"Error calculating deadline info: {e}")
    # --------------------------------------------------

    if request.method == "POST":
        try:
            # === LIMIT CHECK: Only super admin bypasses the 3-card limit ===
            is_editing = 'edit_student_id' in session
            is_super_admin = session.get("admin_role") == "super_admin"
            
            if not is_editing and not is_super_admin and session.get('student_email'):
                count = Student.query.filter_by(email=session.get('student_email')).count()
                
                # Limit is set to 3 cards
                if count >= 3:
                    return render_template("index.html", 
                                           error="⚠️ Limit Reached: You can only generate 3 ID cards per account.", 
                                           templates=templates, 
                                           form_data=request.form, 
                                           selected_template_id=int(request.form.get("template_id", 0)),
                                           deadline_info=deadline_info), 403 # Added deadline_info
            
            if is_admin and not is_editing:
                logger.info("Admin session bypassed the 3-card generation limit.")
            # === END LIMIT CHECK ===

            # 1. Get Template ID
            if is_editing:
                edit_id = session.get('edit_student_id')
                student = db.session.get(Student, edit_id)
                if not student:
                    raise ValueError("Editing student not found")
            
                template_id = student.template_id   # ✅ ONLY SOURCE
            else:
                posted_template_id = int(request.form.get("template_id", 0))
                template_id = selected_template_id if student_school_locked else posted_template_id
                if student_school_locked and posted_template_id and posted_template_id != selected_template_id:
                    logger.warning(
                        "Blocked template override for student %s: posted=%s locked=%s",
                        session.get("student_email"),
                        posted_template_id,
                        selected_template_id,
                    )
            
            if not template_id:
                raise ValueError("Please select a valid school template.")

            template = db.session.get(Template, template_id)
            if not template:
                raise ValueError("Selected school template was not found.")
            if student_school_locked and not _student_school_access_allowed(getattr(template, "school_name", "")):
                raise PermissionError("You can only generate ID cards for your own school.")

            # 2. Check Deadline
            is_passed, deadline_date = check_deadline_passed(template_id)

            # Only super admin can bypass expired generation deadlines.
            if is_passed and session.get("admin_role") != "super_admin":
                error_msg = f"⛔ The deadline passed on {deadline_date}. Card generation is closed."
                return render_template("index.html", 
                                       error=error_msg, 
                                       templates=templates, 
                                       form_data=request.form, 
                                       selected_template_id=template_id,
                                       deadline_info=deadline_info), 403
            if is_passed and session.get("admin_role") == "super_admin":
                logger.info("Super admin bypassed the generation deadline for template %s.", template_id)

            # Load Settings
            font_settings, photo_settings, qr_settings, card_orientation = get_template_settings(template_id)
            template_path = get_template_path(template_id)
            if not template_path:
                raise ValueError("Template file not found")


            # Capture Form Data
            name = request.form.get("name", "").strip()
            father_name = request.form.get("father_name", "").strip()
            class_name = request.form.get("class_name", "").strip()
            dob = request.form.get("dob", "").strip()
            address = request.form.get("address", "").strip()
            phone = request.form.get("phone", "").strip()
            provided_email = request.form.get("email", "").strip().lower()
            
            form_data = {
                'name': name, 'father_name': father_name, 'class_name': class_name,
                'dob': dob, 'address': address, 'phone': phone, 'template_id': template_id
            }

            # Check Duplicates (Skip check if editing)
            if not is_editing:
                is_dup, dup_msg = check_duplicate_student(form_data)
                if is_dup:
                    return render_template("index.html", error=dup_msg, templates=templates, 
                                           form_data=form_data, selected_template_id=template_id,
                                           deadline_info=deadline_info), 400 # Added deadline_info

            # Handle Photo (Cloudinary on deployed, local filesystem when running locally)
            photo_stored = None
            photo_url = None
            if 'photo' in request.files and request.files['photo'].filename:
                photo = request.files['photo']
                photo_fn = secure_filename(photo.filename)
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

                try:
                    photo_bytes = _prepare_uploaded_student_photo_bytes(photo, photo_settings)
                except ValueError as e:
                    logger.warning(f"Uploaded photo rejected: {e}")
                    return render_template(
                        "index.html",
                        error=str(e),
                        templates=templates,
                        form_data=request.form,
                        selected_template_id=template_id,
                        deadline_info=deadline_info,
                    ), 400
                except Exception as e:
                    logger.error(f"Unexpected photo preparation failure: {e}")
                    return render_template(
                        "index.html",
                        error="Failed to process photo. Please try another image.",
                        templates=templates,
                        form_data=request.form,
                        selected_template_id=template_id,
                        deadline_info=deadline_info,
                    ), 500

                photo_stored = f"{timestamp}_{photo_fn}"

                if STORAGE_BACKEND == "local":
                    try:
                        local_photo_path = os.path.join(UPLOAD_FOLDER, photo_stored)
                        _write_binary_file_atomic(local_photo_path, photo_bytes)
                        photo_url = None
                    except Exception as e:
                        logger.error(f"Failed to save photo locally: {e}")
                        return render_template(
                            "index.html",
                            error="Failed to save photo. Please try again.",
                            templates=templates,
                            form_data=request.form,
                            selected_template_id=template_id,
                            deadline_info=deadline_info,
                        ), 500
                else:
                    # Upload to Cloudinary
                    try:
                        uploaded = upload_image(photo_bytes, folder='photos')
                        photo_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
                    except Exception as e:
                        logger.error(f"Failed to upload photo to Cloudinary: {e}")
                        photo_url = None

                    if not photo_url:
                        return render_template(
                            "index.html",
                            error="Failed to upload photo. Please try again.",
                            templates=templates,
                            form_data=request.form,
                            selected_template_id=template_id,
                            deadline_info=deadline_info,
                        ), 500
            elif request.form.get('photo_url'):
                # Support existing photo_url from previous generation
                photo_url = request.form.get('photo_url')
                photo_stored = photo_url  # Store URL identifier
            elif request.form.get('photo_filename'):
                # Legacy local filename
                photo_stored = request.form.get('photo_filename')
            else:
                raise ValueError("Photo is required")

            # === CARD GENERATION LOGIC ===
            try:
                card_width, card_height = get_card_size(template_id)
                template_path = get_template_path(template_id)
                template_img = load_template_smart(template_path).resize((card_width, card_height))
            except Exception as e:
                logger.error(f"Error loading template {template_id} from {template_path}: {e}")
                return render_template("index.html", error=f"Failed to load template: {str(e)}", 
                                       templates=templates, form_data=request.form,
                                       selected_template_id=template_id, deadline_info=deadline_info), 500
            
            draw = ImageDraw.Draw(template_img)

            try:
                L_COLOR = tuple(font_settings.get("label_font_color", [0,0,0]))
                V_COLOR = tuple(font_settings.get("value_font_color", [0,0,0]))
                C_COLOR = tuple(font_settings.get("colon_font_color", list(L_COLOR)))
            except:
                L_COLOR = V_COLOR = (0,0,0)
                C_COLOR = L_COLOR

            # --- NEW MULTI-LANGUAGE LOGIC START ---
            if template:
                lang = (getattr(template, 'language', 'english') or 'english').strip().lower()
                direction = (getattr(template, 'text_direction', 'ltr') or 'ltr').strip().lower()
            else:
                lang = 'english'
                direction = 'ltr'
            if direction == "rtl" and lang == "english":
                lang = "urdu"

            # Load font paths (language-aware fallback happens in load_font_dynamic)
            FONT_BOLD = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
            FONT_REG = os.path.join(FONTS_FOLDER, font_settings["font_regular"])
            l_font = load_font_dynamic(FONT_BOLD, "X", 10**9, font_settings["label_font_size"], language=lang)

            std_labels = {
                'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
                'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
                'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
                'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
            }
            labels_map = std_labels.get(lang, std_labels['english'])

            all_fields = [
                {'key': 'NAME', 'label': labels_map['NAME'], 'val': name, 'order': 10},
                {'key': 'F_NAME', 'label': labels_map['F_NAME'], 'val': father_name, 'order': 20},
                {'key': 'CLASS', 'label': labels_map['CLASS'], 'val': class_name, 'order': 30},
                {'key': 'DOB', 'label': labels_map['DOB'], 'val': dob, 'order': 40},
                {'key': 'MOBILE', 'label': labels_map['MOBILE'], 'val': phone, 'order': 50},
                {'key': 'ADDRESS', 'label': labels_map['ADDRESS'], 'val': address, 'order': 60}
            ]
            
            custom_data = {}
            if template:
                for field in TemplateField.query.filter_by(template_id=template_id).order_by(TemplateField.display_order.asc()).all():
                    f_name = field.field_name
                    val = request.form.get(f_name, "").strip()
                    custom_data[f_name] = val
                    all_fields.append({
                        'key': field.field_name,
                        'label': field.field_label, # Uses raw label typed by admin
                        'val': val,
                        'order': field.display_order
                    })

            all_fields.sort(key=lambda x: int(x.get('order') or 0))
            
            # --- NEW MULTI-LANGUAGE LOGIC END ---

            # --- PRE-CALCULATE PHOTO BOUNDARIES ---
            p_x = photo_settings.get("photo_x", 0)
            p_y = photo_settings.get("photo_y", 0)
            p_w = photo_settings.get("photo_width", 0)
            p_h = photo_settings.get("photo_height", 0)
            p_bottom = p_y + p_h

            # Draw Loop
            label_x = font_settings["label_x"]
            value_x = font_settings["value_x"]
            current_y = get_initial_flow_y_for_side(template, font_settings, side="front")
            line_height = font_settings["line_height"]
            text_case = font_settings.get("text_case", "normal")
            show_label_colon = bool(font_settings.get("show_label_colon", True))
            align_label_colon = bool(font_settings.get("align_label_colon", True))
            label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)

            for item in all_fields:
                raw_label = apply_text_case(item['label'], text_case)
                raw_val = apply_text_case(item['val'], text_case)

                # --- TEXT SHAPING ---
                display_label = process_text_for_drawing(raw_label, lang)
                display_val = process_text_for_drawing(raw_val, lang)
                field_key = item.get('key') or order_to_field_key(item.get('order'))
                layout_item = resolve_field_layout(template, field_key, label_x, value_x, current_y)
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

                # --- DRAWING ---
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
                if advances_flow:
                    current_y = max(int(current_y), int(label_y_eff), int(value_y_eff))
                l_font = load_font_dynamic(FONT_BOLD, label_text_final, 10**9, label_font_size_eff, language=lang)
                colon_font = load_font_dynamic(FONT_BOLD, colon_text_final or ":", 10**9, colon_font_size_eff, language=lang)
                if layout_item["label_visible"]:
                    label_draw_x = flip_x_for_text_direction(
                        label_x_eff,
                        label_text_final,
                        l_font,
                        card_width,
                        direction,
                        draw=draw,
                        grow_mode=layout_item["label_grow"],
                    )
                    draw.text(
                        (label_draw_x, label_y_eff),
                        label_text_final,
                        font=l_font,
                        fill=label_fill,
                        **get_draw_text_kwargs(label_text_final, lang),
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

                # --- SPECIAL LOGIC FOR ADDRESS: FIT IN 2 LINES (FIXED) ---
                if item['label'] == labels_map['ADDRESS']:
                    # Start shrinking logic
                    curr_size = value_font_size_eff
                    min_size = 10 
                    wrapped_addr = []
                    
                    # We need the path to reload font for accurate measurement
                    addr_font_path = FONT_REG 

                    while curr_size >= min_size:
                        # 1. Load font at this specific size to measure
                        temp_font = load_font_dynamic(addr_font_path, "X", 10**9, curr_size, language=lang)

                        # 2. Heuristic wrapping
                        avg_char_w = curr_size * 0.50
                        chars_limit = int(max_w / avg_char_w)
                        if chars_limit < 5: chars_limit = 5 

                        wrapped_addr = textwrap.wrap(raw_val, width=chars_limit, break_long_words=True)
                        
                        # 3. Check pixel width of every line
                        fits_horizontally = True
                        if len(wrapped_addr) <= 2:
                            for line in wrapped_addr:
                                # Shape text for accurate length measurement (Urdu/Arabic support)
                                measure_text = process_text_for_drawing(line, lang)
                                if draw.textlength(measure_text, font=temp_font, **get_draw_text_kwargs(measure_text, lang)) > max_w:
                                    fits_horizontally = False
                                    break
                            
                            if fits_horizontally:
                                # It fits! Use this font
                                addr_font = temp_font
                                break
                        
                        # Decrease size and try again
                        curr_size -= 2
                    
                    # Fallback if loop finishes without finding a fit
                    if 'addr_font' not in locals():
                        addr_font = load_font_dynamic(addr_font_path, "X", 10**9, min_size, language=lang)

                    # Draw up to 2 lines
                    for line in wrapped_addr[:2]:
                        line_display = process_text_for_drawing(line, lang)
                        # Use slightly tighter spacing if we shrunk the font significantly
                        spacing = line_height if curr_size > 20 else curr_size + 5
                        if layout_item["value_visible"]:
                            value_draw_x = flip_x_for_text_direction(
                                value_x_eff,
                                line_display,
                                addr_font,
                                card_width,
                                direction,
                                draw=draw,
                                grow_mode=layout_item["value_grow"],
                            )
                            draw.text(
                                (value_draw_x, value_y_eff),
                                line_display,
                                font=addr_font,
                                fill=value_fill,
                                **get_draw_text_kwargs(line_display, lang),
                            )
                        value_y_eff += spacing
                        if advances_flow:
                            current_y += spacing

                # --- STANDARD LOGIC FOR OTHER FIELDS ---
                else:
                    # For all non-address fields: shrink font to fit in one line, no wrapping
                    curr_size = value_font_size_eff
                    min_size = 10
                    v_font = None
                    while curr_size >= min_size:
                        temp_font = load_font_dynamic(FONT_REG, display_val, max_w, curr_size, language=lang)
                        text_len = draw.textlength(display_val, font=temp_font, **get_draw_text_kwargs(display_val, lang))
                        if text_len <= max_w:
                            v_font = temp_font
                            break
                        curr_size -= 2
                    if v_font is None:
                        v_font = load_font_dynamic(FONT_REG, display_val, max_w, min_size, language=lang)
                    if layout_item["value_visible"]:
                        value_draw_x = flip_x_for_text_direction(
                            value_x_eff,
                            display_val,
                            v_font,
                            card_width,
                            direction,
                            draw=draw,
                            grow_mode=layout_item["value_grow"],
                        )
                        draw.text(
                            (value_draw_x, value_y_eff),
                            display_val,
                            font=v_font,
                            fill=value_fill,
                            **get_draw_text_kwargs(display_val, lang),
                        )
                    if advances_flow:
                        current_y += line_height

            # Photo & QR (fetch remote photo if needed)
            if photo_settings.get("enable_photo", True):
                try:
                    photo_subject = SimpleNamespace(
                        photo_url=photo_url or None,
                        photo_filename=photo_stored or None,
                        image_url=None,
                    )
                    ph = load_student_photo_rgba(
                        photo_subject,
                        photo_settings["photo_width"],
                        photo_settings["photo_height"],
                        timeout=10,
                        photo_settings=photo_settings,
                    )
                    if ph is None:
                        logger.error(f"Failed to load photo for photo_subject, using placeholder")
                        if os.path.exists(PLACEHOLDER_PATH):
                            ph_w = int(float(photo_settings.get("photo_width", 100) or 100))
                            ph_h = int(float(photo_settings.get("photo_height", 100) or 100))
                            ph = Image.open(PLACEHOLDER_PATH).convert("RGBA")
                            ph = ImageOps.fit(
                                ph, (ph_w, ph_h), Image.Resampling.LANCZOS
                            )
                    if ph:
                        radii = [int(float(photo_settings.get(f"photo_border_{k}", 0) or 0)) for k in ["top_left", "top_right", "bottom_right", "bottom_left"]]
                        ph = round_photo(ph, radii)
                        template_img.paste(ph, (photo_settings["photo_x"], photo_settings["photo_y"]), ph)
                except Exception as e:
                    logger.error(f"Error adding photo: {e}")

            data_hash = generate_data_hash(form_data, photo_stored)
            qr_id = data_hash[:10]

            if qr_settings.get("enable_qr", False):
                if qr_settings.get("qr_data_type") == "url":
                    base = qr_settings.get("qr_base_url", "")
                    if not base.endswith('/'):
                        base += '/'
                    qr_payload = base + qr_id
                elif qr_settings.get("qr_data_type") == "text":
                    qr_payload = qr_settings.get("qr_custom_text", "Sample Text")
                elif qr_settings.get("qr_data_type") == "json":
                    qr_payload = json.dumps({
                        "student_id": qr_id,
                        "name": name,
                        "class": class_name,
                        "school_name": school_name
                    })
                else:
                    qr_payload = qr_id

                code_size = qr_settings.get("qr_size", 120)
                code_x = qr_settings.get("qr_x", 50)
                code_y = qr_settings.get("qr_y", 50)
                qr_img = generate_qr_code(qr_payload, qr_settings, code_size)
                qr_img = qr_img.resize((code_size, code_size))
                template_img.paste(qr_img, (code_x, code_y))

            if qr_settings.get("enable_barcode", False):
                barcode_type = qr_settings.get("barcode_data_type", "student_id")
                if barcode_type == "url":
                    base = qr_settings.get("barcode_base_url", "")
                    if not base.endswith('/'):
                        base += '/'
                    barcode_payload = base + qr_id
                elif barcode_type == "text":
                    barcode_payload = qr_settings.get("barcode_custom_text", "Sample Text")
                elif barcode_type == "json":
                    barcode_payload = json.dumps({
                        "student_id": qr_id,
                        "name": name,
                        "class": class_name,
                        "school_name": school_name
                    })
                else:
                    barcode_payload = qr_id

                barcode_x = qr_settings.get("barcode_x", 50)
                barcode_y = qr_settings.get("barcode_y", 200)
                barcode_w = max(40, int(qr_settings.get("barcode_width", 220)))
                barcode_h = max(30, int(qr_settings.get("barcode_height", 70)))
                barcode_img = generate_barcode_code128(barcode_payload, qr_settings, width=barcode_w, height=barcode_h)
                template_img.paste(barcode_img, (barcode_x, barcode_y))

            apply_layout_custom_objects_pil(template_img, template, font_settings, side="front", language=lang)

            side_render_student = SimpleNamespace(
                name=name,
                father_name=father_name,
                class_name=class_name,
                dob=dob,
                address=address,
                phone=phone,
                photo_url=photo_url,
                photo_filename=photo_stored if photo_stored and not str(photo_stored).startswith("http") else None,
                custom_data=custom_data,
                school_name=school_name,
            )

            student_id = getattr(side_render_student, "id", None)
            render_scale = 1.0
            school_name = getattr(template, "school_name", None) or getattr(student, "school_name", "")



            front_template_img = _get_cached_final_card(
                template,
                side_render_student,
                side="front",
                student_id=student_id,
                school_name=school_name,
                render_scale=render_scale,
            )

            if front_template_img is not None:
                template_img = front_template_img

            # --- Robust back_template_img handling ---

            # Always define back_template_img before use
            back_template_img = None
            if getattr(template, "is_double_sided", False):
                try:
                    back_template_img = _get_cached_final_card(
                        template,
                        side_render_student,
                        side="back",
                        student_id=student_id,
                        school_name=school_name,
                        render_scale=render_scale,
                    ) or load_static_back_template_image(template, card_width, card_height)
                except Exception as e:
                    logger.warning(f"Failed to render back card image: {e}")
                    back_template_img = None
            else:
                back_template_img = None

            # --- End robust back_template_img handling ---

            # =========================================================
            # SAVE OUTPUT (Cloudinary on deployed, local filesystem when running locally)
            # =========================================================
            
            # --- FIX: Convert to RGB before saving as JPEG ---
            # This fixes "cannot write mode RGBA as JPEG"
            if template_img.mode == 'RGBA':
                # Create a white background for transparency
                background = Image.new("RGB", template_img.size, (255, 255, 255))
                background.paste(template_img, mask=template_img.split()[3])
                template_img = background
            else:
                template_img = template_img.convert("RGB")
            if back_template_img is not None:
                if back_template_img.mode == 'RGBA':
                    back_background = Image.new("RGB", back_template_img.size, (255, 255, 255))
                    back_background.paste(back_template_img, mask=back_template_img.split()[3])
                    back_template_img = back_background
                else:
                    back_template_img = back_template_img.convert("RGB")
            elif getattr(template, "is_double_sided", False):
                logger.warning("Double-sided template but back image missing; skipping back image.")
            # -------------------------------------------------

            # Save image to bytes
            jpg_buf = io.BytesIO()
            template_img.save(jpg_buf, format='JPEG', quality=95)
            jpg_buf.seek(0)
            jpg_bytes = jpg_buf.getvalue()
            back_jpg_bytes = None
            if back_template_img is not None:
                back_jpg_buf = io.BytesIO()
                back_template_img.save(back_jpg_buf, format='JPEG', quality=95)
                back_jpg_buf.seek(0)
                back_jpg_bytes = back_jpg_buf.getvalue()

            pdf_buf = io.BytesIO()
            if back_template_img is not None:
                template_img.save(pdf_buf, format='PDF', save_all=True, append_images=[back_template_img], quality=95)
            else:
                template_img.save(pdf_buf, format='PDF', quality=95)
            pdf_buf.seek(0)
            pdf_bytes = pdf_buf.getvalue()

            image_url = None
            back_image_url = None
            pdf_url = None
            generated_filename = None
            back_generated_filename = None

            if STORAGE_BACKEND == "local":
                try:
                    os.makedirs(GENERATED_FOLDER, exist_ok=True)
                    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
                    base = f"card_{template_id}_{ts}_{uuid.uuid4().hex}"
                    jpg_name = f"{base}.jpg"
                    pdf_name = f"{base}.pdf"
                    back_jpg_name = f"{base}_back.jpg" if back_jpg_bytes is not None else None

                    with open(os.path.join(GENERATED_FOLDER, jpg_name), "wb") as fh:
                        fh.write(jpg_bytes)
                    if back_jpg_name and back_jpg_bytes is not None:
                        with open(os.path.join(GENERATED_FOLDER, back_jpg_name), "wb") as fh:
                            fh.write(back_jpg_bytes)
                    with open(os.path.join(GENERATED_FOLDER, pdf_name), "wb") as fh:
                        fh.write(pdf_bytes)

                    generated_filename = pdf_name  # legacy field; preview jpg is derived from this
                    back_generated_filename = back_jpg_name
                    generated_url = with_cache_bust(url_for('static', filename=f'generated/{jpg_name}'))
                    download_url = url_for('static', filename=f'generated/{pdf_name}')
                    if back_jpg_name:
                        back_generated_url = with_cache_bust(url_for('static', filename=f'generated/{back_jpg_name}'))
                except Exception as e:
                    logger.error(f"Local save failed: {e}")
                    return render_template(
                        "index.html",
                        error=f"Failed to save image: {str(e)}",
                        templates=templates,
                        form_data=request.form,
                        selected_template_id=template_id,
                        deadline_info=deadline_info,
                    ), 500
            else:
                # Upload to Cloudinary
                try:
                    jpg_result = upload_image(jpg_bytes, folder='generated')
                    image_url = jpg_result if isinstance(jpg_result, str) else jpg_result.get('url')
                    if back_jpg_bytes is not None:
                        back_jpg_result = upload_image(back_jpg_bytes, folder='generated')
                        back_image_url = back_jpg_result if isinstance(back_jpg_result, str) else back_jpg_result.get('url')

                    pdf_result = upload_image(pdf_bytes, folder='generated', resource_type='raw')
                    pdf_url = pdf_result if isinstance(pdf_result, str) else pdf_result.get('url')
                except Exception as e:
                    logger.error(f"Cloudinary upload failed: {e}")
                    return render_template("index.html", error=f"Failed to save image: {str(e)}",
                                           templates=templates, form_data=request.form,
                                           selected_template_id=template_id, deadline_info=deadline_info), 500

                generated_url = with_cache_bust(image_url)
                download_url = pdf_url
                if back_image_url:
                    back_generated_url = with_cache_bust(back_image_url)
            # =========================================================

            # DB Insert/Update
            if is_editing:
                edit_id = session['edit_student_id']
                student = db.session.get(Student, edit_id)
                if student:
                    student.name = name
                    student.father_name = father_name
                    student.class_name = class_name
                    student.dob = dob
                    student.address = address
                    student.phone = phone
                    if is_admin and provided_email:
                        student.email = provided_email
                    if STORAGE_BACKEND == "local":
                        student.photo_url = None
                        student.photo_filename = photo_stored
                        student.image_url = None
                        student.back_image_url = None
                        student.pdf_url = None
                        student.generated_filename = generated_filename
                        student.back_generated_filename = back_generated_filename
                    else:
                        student.photo_url = photo_url  # Store Cloudinary URL
                        student.image_url = image_url  # Store generated card image URL
                        student.back_image_url = back_image_url
                        student.pdf_url = pdf_url  # Store generated PDF URL
                    student.created_at = datetime.now(timezone.utc)
                    student.data_hash = data_hash
                    student.template_id = template_id
                    student.school_name = school_name
                    student.custom_data = custom_data
                    db.session.commit()
                    unique_edit_id = edit_id
                    success = "Card Updated Successfully!"
                    session.pop('edit_student_id', None)
            else:
                # New Record
                final_email = (provided_email or None) if is_admin else session.get('student_email')

                student = Student(
                    name=name,
                    father_name=father_name,
                    class_name=class_name,
                    dob=dob,
                    address=address,
                    phone=phone,
                    photo_url=None if STORAGE_BACKEND == "local" else photo_url,
                    photo_filename=photo_stored if STORAGE_BACKEND == "local" else None,
                    image_url=None if STORAGE_BACKEND == "local" else image_url,
                    back_image_url=None if STORAGE_BACKEND == "local" else back_image_url,
                    pdf_url=None if STORAGE_BACKEND == "local" else pdf_url,
                    generated_filename=generated_filename if STORAGE_BACKEND == "local" else None,
                    back_generated_filename=back_generated_filename if STORAGE_BACKEND == "local" else None,
                    created_at=datetime.now(timezone.utc),
                    data_hash=data_hash,
                    template_id=template_id,
                    school_name=school_name,
                    email=final_email,
                    custom_data=custom_data
                )
                db.session.add(student)
                db.session.commit()
                
                unique_edit_id = student.id
                # NO CALL TO add_card_to_sheet() HERE

                # Email Logic
                try:
                    if student.email:
                        email_subject = "ID Card Generated Successfully - Save Your ID"
                        email_body = f"""Hello {student.name},

Your ID card for {student.school_name} has been generated successfully.

--------------------------------------------------
YOUR UNIQUE ID: {unique_edit_id}
--------------------------------------------------

Please save this ID number. You will need it if you ever want to edit your ID card details later.

Regards,
ID Card Admin
NOOR GRAPHICS AND PRINTERS
"""
                        # --- UPDATED: Check if email sent successfully ---
                        if send_email(student.email, email_subject, email_body):
                            logger.info(f"Email success confirmed for {student.email}")
                        else:
                            logger.error(f"Email FAILED for {student.email} (Check server logs for details)")
                        # -------------------------------------------------

                except Exception as email_err:
                    logger.error(f"Crash in email logic: {email_err}")

                if student.email:
                    success = f"Card Generated Successfully! (ID: {unique_edit_id}). \n An email with this ID has been sent to you."
                else:
                    success = f"Card Generated Successfully! (ID: {unique_edit_id})."
            # Clear Form
            form_data = { 'template_id': template_id }

        except Exception as e:
            error = f"Error: {str(e)}"
            logger.error(error)
            return render_template("index.html", error=error, templates=templates, 
                                   form_data=request.form, selected_template_id=template_id,
                                   deadline_info=deadline_info), 500 # Added deadline_info

    return render_template("index.html", generated_url=generated_url, back_generated_url=back_generated_url, download_url=download_url,
                           form_data=form_data, success=success, error=error, templates=templates, 
                           show_fetch=show_fetch, unique_edit_id=unique_edit_id, 
                           selected_template_id=selected_template_id, deadline_info=deadline_info) # Added deadline_info

@app.route("/fetch_record", methods=["POST"])
def fetch_record():
    unique_id_raw = request.form.get("unique_id", "").strip()
    if not unique_id_raw:
        logger.error("Unique ID is required")
        return jsonify({"success": False, "error": "Unique ID is required"}), 400

    # Allow users to type IDs using non-ASCII digits (e.g., Urdu/Arabic-Indic digits).
    # Browsers may submit these digits as Unicode; SQLite primary keys are numeric, so normalize.
    try:
        import unicodedata

        normalized = []
        for ch in str(unique_id_raw):
            if ch.isspace():
                continue
            if ch.isdigit():
                try:
                    normalized.append(str(unicodedata.digit(ch)))
                except Exception:
                    normalized.append(ch)
            else:
                normalized.append(ch)
        unique_id_norm = "".join(normalized)
    except Exception:
        unique_id_norm = unique_id_raw

    if not str(unique_id_norm).isdigit():
        return jsonify({"success": False, "error": "Unique ID must be numeric"}), 400

    unique_id = int(unique_id_norm)
  
    try:
        student = db.session.get(Student, unique_id)
        
        logger.info(f"Fetching record for unique_id: {unique_id} (raw: {unique_id_raw}), found: {student}")
        
        if student:
            if _is_admin_session():
                edit_url = url_for('edit_student', student_id=student.id)
                return jsonify({"success": True, "edit_url": edit_url})

            session_email = session.get('student_email')
            if not _student_school_access_allowed(student.school_name):
                logger.warning(
                    "Blocked record access for %s: record school=%s locked school=%s",
                    session_email,
                    student.school_name,
                    _student_session_school_name(),
                )
                return jsonify({"success": False, "error": "This ID card belongs to another school."}), 403
          
            # If student record has no email, allow access for logged-in users
            if not student.email:
                logger.warning(f"Student record {student.id} has no email associated")
                
                # If user is logged in and has less than 3 records, allow access
                if session_email:
                    user_record_count = Student.query.filter_by(email=session_email).count()
                    
                    if user_record_count < 3:
                        # Update record with session email
                        student.email = session_email
                        db.session.commit()
                        logger.info(f"Associated student {student.id} with email {session_email}")
                        edit_url = url_for('edit_student', student_id=student.id)
                        return jsonify({"success": True, "edit_url": edit_url})
                    else:
                        return jsonify({"success": False, "error": "You have reached your record limit (3)."}), 403
                else:
                    return jsonify({"success": False, "error": "Please login first to access this record."}), 403
          
            # If record has email, check authorization
            if session_email and student.email.lower() == session_email.lower():
                edit_url = url_for('edit_student', student_id=student.id)
                return jsonify({"success": True, "edit_url": edit_url})
            else:
                logger.warning(f"Email mismatch - Student: {student.email}, Session: {session_email}")
                return jsonify({"success": False, "error": "You are not authorized to edit this record"}), 403
        else:
            logger.error(f"No record found for unique ID {unique_id}")
            return jsonify({"success": False, "error": "No record found for this unique ID"}), 404
    except Exception as e:
        logger.error(f"Error fetching record for unique ID {unique_id}: {e}")
        return jsonify({"success": False, "error": f"Error fetching record: {str(e)}"}), 500
  
@app.route("/clear_edit_session", methods=["POST"])
def clear_edit_session():
    session.pop('edit_student_id', None)
    return jsonify({"success": True})

@app.route("/edit/<int:student_id>", methods=["GET", "POST"])
def edit_student(student_id):
    generated_url = None
    back_generated_url = None
    download_url = None
    form_data = {}
    success = None
    error = None
    templates = get_templates()
    
    # 1. GET Request
    try:
        student = db.session.get(Student, student_id)
        
        if not student:
            error = f"No student found with ID {student_id}"
            logger.error(error)
            return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                 generated_url=generated_url, download_url=download_url, success=success), 404
        
        # --- UPDATED AUTHORIZATION CHECK ---
        # Goal: Admins = Access All. Students = Access Own. Bulk (No Email) = Admin Only.
        
        is_admin = session.get("admin")
        current_user_email = session.get('student_email', '').strip().lower()
        record_email = (student.email or '').strip().lower()
        
        # Check ownership (only valid if record actually has an email)
        is_owner = (current_user_email and record_email and current_user_email == record_email)

        if not is_admin:
            # If not Admin, check strict rules:
            if not record_email:
                 # Case: Bulk Card (No Email) -> BLOCK
                 error = "Restricted: Bulk-generated cards can only be edited by an Administrator."
                 logger.warning(f"Unauthorized edit attempt on Bulk Card {student_id} by {current_user_email}")
                 return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                      generated_url=generated_url, download_url=download_url, success=success), 403
            
            if not is_owner:
                 # Case: Email exists but doesn't match logged-in user -> BLOCK
                 error = "You are not authorized to edit this record."
                 logger.warning(f"Unauthorized edit attempt on Student {student_id} by {current_user_email}")
                 return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                      generated_url=generated_url, download_url=download_url, success=success), 403
            if not _student_school_access_allowed(student.school_name):
                error = "This ID card belongs to another school."
                logger.warning(
                    "Blocked cross-school edit attempt on Student %s by %s",
                    student_id,
                    current_user_email,
                )
                return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                     generated_url=generated_url, download_url=download_url, success=success), 403

        # -----------------------------------

        # --- STRICT DEADLINE CHECK ---
        if student and student.template_id:
            is_passed, deadline_date = check_deadline_passed(student.template_id)
            
            if is_passed:
                error = f"⛔ The deadline for editing this ID card passed on {deadline_date}. Updates are disabled."
                # If it's a POST (trying to save), block strictly (Admin exception optional)
                if request.method == "POST" and not is_admin:
                    return render_template("edit.html", error=error, templates=templates, form_data=form_data), 403
                else:
                    # If it's a GET (just viewing), show flash error but maybe allow read-only
                    flash(error, 'error')
        # -----------------------------
        
        # Authorization successful - populate form data
        form_data = {
            'id': student.id,
            'name': student.name,
            'father_name': student.father_name,
            'class_name': student.class_name,
            'dob': student.dob,
            'address': student.address,
            'phone': student.phone,
            'photo_filename': student.photo_filename,
            'generated_filename': student.generated_filename,
            'template_id': student.template_id,
            'school_name': student.school_name,
            'custom_data': student.custom_data or {} 
        }
        
        preview_url, back_preview_url = resolve_student_card_preview_urls(student)
        generated_url = with_cache_bust(preview_url or url_for('static', filename=os.path.basename(PLACEHOLDER_PATH)))
        back_generated_url = with_cache_bust(back_preview_url) if back_preview_url else None
        
        download_url = student.pdf_url if student.pdf_url else None
    
    except Exception as e:
        error = f"Error fetching student data: {str(e)}"
        logger.error(error)
        return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                             generated_url=generated_url, download_url=download_url, success=success), 500
  
    if request.method == "POST":
        try:
            student = db.session.get(Student, student_id)
            
            # --- UPDATED POST AUTHORIZATION ---
            if not session.get("admin"):
                current_user_email = session.get('student_email', '').strip().lower()
                record_email = (student.email or '').strip().lower()
                
                # Strict check: Block if no email (Bulk) OR Email mismatch
                if not record_email or current_user_email != record_email:
                     error = "You are not authorized to edit this record."
                     return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                          generated_url=generated_url, download_url=download_url, success=success), 403
                if not _student_school_access_allowed(student.school_name):
                     error = "This ID card belongs to another school."
                     return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                          generated_url=generated_url, download_url=download_url, success=success), 403
            # ----------------------------------

        except Exception as e:
            error = f"Database error: {str(e)}"
            logger.error(error)
            return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                 generated_url=generated_url, download_url=download_url, success=success), 500
      
        existing_photo_filename = form_data.get('photo_filename', '')
        template_id = student.template_id
        school_name = next((t['school_name'] for t in templates if t['id'] == template_id), None)
        template_path = get_template_path(template_id)
        if not template_path:
            error = "No template found. Please contact administrator."
            logger.error(f"Template not found for ID {template_id}")
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 400
      
        font_settings, photo_settings, qr_settings, card_orientation = get_template_settings(template_id)
        card_width, card_height = get_card_size(template_id)

        # Template language + direction for text shaping + RTL layout
        template_obj = db.session.get(Template, template_id)
        if template_obj:
            lang = (getattr(template_obj, "language", "english") or "english").strip().lower()
            direction = (getattr(template_obj, "text_direction", "ltr") or "ltr").strip().lower()
        else:
            lang = "english"
            direction = "ltr"
        if direction == "rtl" and lang == "english":
            lang = "urdu"
      
        FONT_BOLD_PATH = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
        FONT_REGULAR_PATH = os.path.join(FONTS_FOLDER, font_settings["font_regular"])
      
        if not is_valid_font_file(FONT_BOLD_PATH) or not is_valid_font_file(FONT_REGULAR_PATH):
            error = f"Invalid font file: {font_settings['font_bold']} or {font_settings['font_regular']}"
            logger.error(error)
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 500
      
        # Get font colors from settings
        label_font_color = font_settings.get("label_font_color", [0, 0, 0])
        value_font_color = font_settings.get("value_font_color", [0, 0, 0])
      
        # Convert colors to tuples for PIL
        try:
            LABEL_FONT_COLOR = tuple(label_font_color)
        except Exception as e:
            LABEL_FONT_COLOR = (0, 0, 0)
      
        try:
            VALUE_FONT_COLOR = tuple(value_font_color)
        except Exception as e:
            VALUE_FONT_COLOR = (0, 0, 0)
        try:
            COLON_FONT_COLOR = tuple(font_settings.get("colon_font_color", list(LABEL_FONT_COLOR)))
        except Exception:
            COLON_FONT_COLOR = LABEL_FONT_COLOR
      
        name = request.form.get("name", "").strip()
        father_name = request.form.get("father_name", "").strip()
        class_name = request.form.get("class_name", "").strip()
        dob = request.form.get("dob", "").strip()
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
      
        form_data = {
            'id': student_id,
            'name': name,
            'father_name': father_name,
            'class_name': class_name,
            'dob': dob,
            'address': address,
            'phone': phone,
            'template_id': template_id,
            'school_name': school_name
        }
      
        is_duplicate, duplicate_message = check_duplicate_student(form_data, None, student_id)
        if is_duplicate:
            logger.warning(f"Duplicate student detected: {duplicate_message}")
            return render_template("edit.html", generated_url=generated_url,
                                 download_url=download_url, form_data=form_data,
                                 error=duplicate_message, templates=templates), 400
      
        photo_fn = None
        photo_stored = existing_photo_filename
        photo_url, existing_local_photo_path = resolve_student_photo_reference(student)

        if existing_local_photo_path:
            photo_stored = os.path.basename(existing_local_photo_path)

        if not photo_url and not existing_local_photo_path:
            logger.warning(f"No valid photo found for student {student_id}")
        
      
        if 'photo' in request.files and request.files['photo'].filename:
            photo = request.files['photo']
            photo_fn = secure_filename(photo.filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            photo_stored = f"{timestamp}_{photo_fn}"
            
            try:
                photo_bytes = _prepare_uploaded_student_photo_bytes(photo, photo_settings)

                if STORAGE_BACKEND == "local":
                    try:
                        local_photo_path = os.path.join(UPLOAD_FOLDER, photo_stored)
                        _write_binary_file_atomic(local_photo_path, photo_bytes)
                        photo_url = None
                    except Exception as e:
                        error = f"Error saving photo locally: {str(e)}"
                        logger.error(error)
                        return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                             form_data=form_data, error=error, templates=templates), 500
                else:
                    # Upload to Cloudinary
                    photo_url = upload_image(photo_bytes, folder='photos')
            except ValueError as e:
                error = str(e)
                logger.warning(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 400
            except Exception as e:
                error = f"Error processing photo: {str(e)}"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 500
        else:
            if not photo_stored and not photo_url:
                error = "No photo provided and no existing photo found"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 400
      
        data_hash = generate_data_hash(form_data, photo_stored)
      
        try:
            template = load_template_smart(template_path)
            template = template.resize((card_width, card_height))
            draw = ImageDraw.Draw(template)
          
            # Apply text case transformation
            text_case = font_settings.get("text_case", "normal")
            show_label_colon = bool(font_settings.get("show_label_colon", True))
            align_label_colon = bool(font_settings.get("align_label_colon", True))
            label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)
            
            # --- DYNAMIC FIELDS HANDLING ---
            # 1. Capture dynamic values from the form
            custom_data = {}
            dynamic_display = []
            std_labels = {
                'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
                'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
                'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
                'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
            }
            labels_map = std_labels.get(lang, std_labels['english'])
            
            # Fetch field definitions for this template
            db_fields = TemplateField.query.filter_by(template_id=template_id).order_by(TemplateField.display_order.asc()).all()
            
            for field in db_fields:
                val = request.form.get(field.field_name, "").strip()
                custom_data[field.field_name] = val
                
                dynamic_display.append({
                    'key': field.field_name,
                    'label': field.field_label,
                    'value': val,
                    'order': field.display_order
                })
            
            # 2. Combine with Standard Fields
            all_fields = [
                {'key': "NAME", 'label': labels_map['NAME'], 'value': apply_text_case(name, text_case), 'order': 10},
                {'key': "F_NAME", 'label': labels_map['F_NAME'], 'value': apply_text_case(father_name, text_case), 'order': 20},
                {'key': "CLASS", 'label': labels_map['CLASS'], 'value': apply_text_case(class_name, text_case), 'order': 30},
                {'key': "DOB", 'label': labels_map['DOB'], 'value': dob, 'order': 40},
                {'key': "MOBILE", 'label': labels_map['MOBILE'], 'value': phone, 'order': 50},
                {'key': "ADDRESS", 'label': labels_map['ADDRESS'], 'value': apply_text_case(address, text_case), 'order': 60}
            ]
            
            # Add dynamic fields to the list for drawing
            for d in dynamic_display:
                all_fields.append({
                    'key': d.get('key'),
                    'label': d['label'],
                    'value': apply_text_case(d['value'], text_case),
                    'order': d['order']
                })
            
            # 3. Sort by display order
            all_fields.sort(key=lambda x: int(x.get('order') or 0))
            # -------------------------------

            # --- PRE-CALCULATE PHOTO BOUNDARIES ---
            p_x = photo_settings.get("photo_x", 0)
            p_y = photo_settings.get("photo_y", 0)
            p_w = photo_settings.get("photo_width", 0)
            p_h = photo_settings.get("photo_height", 0)
            p_bottom = p_y + p_h

            # Layout Settings
            label_x = font_settings["label_x"]
            value_x = font_settings["value_x"]
            current_y = get_initial_flow_y_for_side(template, font_settings, side="front")
            line_height = font_settings["line_height"]

            # Load label font once (language-aware Unicode fallback)
            l_font = load_font_dynamic(FONT_BOLD_PATH, "X", 10**9, font_settings["label_font_size"], language=lang)
            
            # Draw Loop
            for item in all_fields:
                lbl = apply_text_case(item['label'], text_case)
                val = apply_text_case(item['value'], text_case)

                # Text shaping for correct Urdu/Arabic joining + RTL order (Hindi/English unchanged)
                display_label = process_text_for_drawing(lbl, lang)
                display_val = process_text_for_drawing(val, lang)
                field_key = item.get('key') or order_to_field_key(item.get('order'))
                layout_item = resolve_field_layout(template_obj, field_key, label_x, value_x, current_y)
                if not field_within_vertical_bounds(layout_item, current_y, card_height):
                    continue
                label_text_final, colon_text_final = split_label_and_colon(
                    display_label,
                    lang,
                    direction,
                    include_colon=show_label_colon,
                    align_colon=align_label_colon,
                )
                if not field_consumes_layout_space(layout_item, val):
                    continue
                advances_flow = field_advances_layout_flow(layout_item, val, separate_colon=bool(colon_text_final))
                label_x_eff = layout_item["label_x"]
                value_x_eff = layout_item["value_x"]
                label_y_eff = layout_item["label_y"]
                value_y_eff = layout_item["value_y"]
                label_fill = layout_item.get("label_color") or LABEL_FONT_COLOR
                value_fill = layout_item.get("value_color") or VALUE_FONT_COLOR
                colon_fill = layout_item.get("colon_color") or COLON_FONT_COLOR
                label_font_size_eff = max(1, int(layout_item.get("label_font_size") or font_settings["label_font_size"]))
                value_font_size_eff = max(1, int(layout_item.get("value_font_size") or font_settings["value_font_size"]))
                colon_font_size_eff = max(1, int(layout_item.get("colon_font_size") or label_font_size_eff))
                colon_y_eff = layout_item.get("colon_y", label_y_eff)
                colon_x_eff = layout_item.get("colon_x")
                colon_grow_eff = layout_item.get("colon_grow")
                
                if advances_flow:
                    current_y = max(int(current_y), int(label_y_eff), int(value_y_eff))

                l_font = load_font_dynamic(FONT_BOLD_PATH, label_text_final, 10**9, label_font_size_eff, language=lang)
                colon_font = load_font_dynamic(FONT_BOLD_PATH, colon_text_final or ":", 10**9, colon_font_size_eff, language=lang)
                
                label_draw_x = flip_x_for_text_direction(
                    label_x_eff,
                    label_text_final,
                    l_font,
                    card_width,
                    direction,
                    draw=draw,
                    grow_mode=layout_item["label_grow"],
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
                
                # --- ADDRESS LOGIC (PIXEL-ACCURATE, MAX 2 LINES) ---
                if field_key == "ADDRESS" or lbl == "ADDRESS":
                    if layout_item["label_visible"]:
                        draw.text(
                            (label_draw_x, label_y_eff),
                            label_text_final,
                            font=l_font,
                            fill=label_fill,
                            **get_draw_text_kwargs(label_text_final, lang),
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
                        )
                
                    curr_size = value_font_size_eff
                    min_size = 12
                    wrapped_addr = []
                
                    while curr_size >= min_size:
                        # Load font at this size
                        addr_font = load_font_dynamic(FONT_REGULAR_PATH, "X", 10**9, curr_size, language=lang)
                
                        # Pixel-based wrapping
                        words = val.split()
                        lines = []
                        current_line = ""
                
                        for word in words:
                            test_line = current_line + (" " if current_line else "") + word
                            measure_line = process_text_for_drawing(test_line, lang)
                            if draw.textlength(measure_line, font=addr_font, **get_draw_text_kwargs(measure_line, lang)) <= max_w:
                                current_line = test_line
                            else:
                                if current_line:
                                    lines.append(current_line)
                                current_line = word
                
                        if current_line:
                            lines.append(current_line)
                
                        # Stop if it fits in 2 lines
                        if len(lines) <= 2:
                            wrapped_addr = lines
                            break
                
                        curr_size -= 2
                
                    # Fallback if still too long
                    if not wrapped_addr:
                        wrapped_addr = lines[:2]
                
                    # Draw address lines
                    for line in wrapped_addr[:2]:
                        line_display = process_text_for_drawing(line, lang)
                        if layout_item["value_visible"]:
                            value_draw_x = flip_x_for_text_direction(
                                value_x_eff,
                                line_display,
                                addr_font,
                                card_width,
                                direction,
                                draw=draw,
                                grow_mode=layout_item["value_grow"],
                            )
                            draw.text(
                                (value_draw_x, value_y_eff),
                                line_display,
                                font=addr_font,
                                fill=value_fill,
                                **get_draw_text_kwargs(line_display, lang),
                            )
                        value_y_eff += curr_size + 6
                        if advances_flow:
                            current_y += curr_size + 6
                
                    continue
                

                # --- STANDARD FIELDS ---
                else:
                    if layout_item["label_visible"]:
                        draw.text(
                            (label_draw_x, label_y_eff),
                            label_text_final,
                            font=l_font,
                            fill=label_fill,
                            **get_draw_text_kwargs(label_text_final, lang),
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
                        )

                    v_font, _ = fit_dynamic_font_to_single_line(
                        draw,
                        FONT_REGULAR_PATH,
                        display_val,
                        max_w,
                        value_font_size_eff,
                        language=lang,
                    )
                    if layout_item["value_visible"]:
                        value_draw_x = flip_x_for_text_direction(
                            value_x_eff,
                            display_val,
                            v_font,
                            card_width,
                            direction,
                            draw=draw,
                            grow_mode=layout_item["value_grow"],
                        )
                        draw.text(
                            (value_draw_x, value_y_eff),
                            display_val,
                            font=v_font,
                            fill=value_fill,
                            **get_draw_text_kwargs(display_val, lang),
                        )
                    if advances_flow:
                        current_y += line_height
          
            if photo_settings.get("enable_photo", True):
                try:
                    photo_subject = SimpleNamespace(
                        photo_url=photo_url or None,
                        photo_filename=photo_stored or None,
                        image_url=None,
                    )
                    photo_img = load_student_photo_rgba(
                        photo_subject,
                        photo_settings["photo_width"],
                        photo_settings["photo_height"],
                        timeout=10,
                        photo_settings=photo_settings,
                    )
                    if photo_img is None:
                        logger.error(f"Failed to load photo for photo_subject, using placeholder")
                        if os.path.exists(PLACEHOLDER_PATH):
                            ph_w = int(float(photo_settings.get("photo_width", 100) or 100))
                            ph_h = int(float(photo_settings.get("photo_height", 100) or 100))
                            photo_img = Image.open(PLACEHOLDER_PATH).convert("RGBA")
                            photo_img = ImageOps.fit(
                                photo_img, (ph_w, ph_h), Image.Resampling.LANCZOS
                            )
                    if photo_img:
                        radii = [
                            int(float(photo_settings.get("photo_border_top_left", 0) or 0)),
                            int(float(photo_settings.get("photo_border_top_right", 0) or 0)),
                            int(float(photo_settings.get("photo_border_bottom_right", 0) or 0)),
                            int(float(photo_settings.get("photo_border_bottom_left", 0) or 0))
                        ]
                        photo_img = round_photo(photo_img, radii)
                        template.paste(photo_img, (photo_settings["photo_x"], photo_settings["photo_y"]), photo_img)

                except Exception as e:
                    error = f"Error processing photo: {str(e)}"
                    logger.error(error)
                    return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                         form_data=form_data, error=error, templates=templates), 500
          
            # Add QR Code if enabled
            if qr_settings.get("enable_qr", False):
                if qr_settings.get("qr_data_type") == "student_id":
                    qr_data = json.dumps({
                        "student_id": str(student_id),
                        "name": name,
                        "school_name": school_name,
                        "photo_url": photo_url,
                        "custom_data": custom_data
                    })
                elif qr_settings.get("qr_data_type") == "url":
                    qr_data = qr_settings.get("qr_base_url", "") + str(student_id)
                elif qr_settings.get("qr_data_type") == "text":
                    qr_data = qr_settings.get("qr_custom_text", "Sample Text")
                elif qr_settings.get("qr_data_type") == "json":
                    qr_data = json.dumps({
                        "student_id": student_id,
                        "name": name,
                        "class": class_name,
                        "school_name": school_name,
                        "photo_url": photo_url,
                        "custom_data": custom_data
                    })
                else:
                    qr_data = str(student_id)

                qr_size = qr_settings.get("qr_size", 120)
                qr_x = qr_settings.get("qr_x", 50)
                qr_y = qr_settings.get("qr_y", 50)
                qr_img = generate_qr_code(qr_data, qr_settings, qr_size)
                qr_img = qr_img.resize((qr_size, qr_size))
                template.paste(qr_img, (qr_x, qr_y))

            # Add Barcode if enabled
            if qr_settings.get("enable_barcode", False):
                barcode_type = qr_settings.get("barcode_data_type", "student_id")
                if barcode_type == "url":
                    barcode_data = qr_settings.get("barcode_base_url", "") + str(student_id)
                elif barcode_type == "text":
                    barcode_data = qr_settings.get("barcode_custom_text", "Sample Text")
                elif barcode_type == "json":
                    barcode_data = json.dumps({
                        "student_id": student_id,
                        "name": name,
                        "class": class_name,
                        "school_name": school_name,
                        "photo_url": photo_url,
                        "custom_data": custom_data
                    })
                else:
                    barcode_data = str(student_id)

                barcode_x = qr_settings.get("barcode_x", 50)
                barcode_y = qr_settings.get("barcode_y", 200)
                barcode_w = max(40, int(qr_settings.get("barcode_width", 220)))
                barcode_h = max(30, int(qr_settings.get("barcode_height", 70)))
                barcode_img = generate_barcode_code128(barcode_data, qr_settings, width=barcode_w, height=barcode_h)
                template.paste(barcode_img, (barcode_x, barcode_y))

            apply_layout_custom_objects_pil(template, template_obj, font_settings, side="front", language=lang)
          
            # =========================================================
            # SAVE INDIVIDUAL CARD (Cloudinary on deployed, local filesystem when running locally)
            # =========================================================
            side_render_student = SimpleNamespace(
                name=name,
                father_name=father_name,
                class_name=class_name,
                dob=dob,
                address=address,
                phone=phone,
                photo_url=photo_url,
                photo_filename=photo_stored if photo_stored and not str(photo_stored).startswith("http") else None,
                custom_data=custom_data,
                school_name=school_name,
            )
            student_id = getattr(side_render_student, "id", None)
            render_scale = 1.0
            school_name = getattr(template_obj, "school_name", None) or getattr(student, "school_name", "")

            
            front_image = _get_cached_final_card(
                template_obj,
                side_render_student,
                side="front",
                student_id=student_id,
                school_name=school_name,
                render_scale=render_scale,
            )
            
            if front_image is None:
                raise RuntimeError("Failed to render front card image")
            
            back_image = None
            if getattr(template_obj, "is_double_sided", False):
                back_image = _get_cached_final_card(
                    template_obj,
                    side_render_student,
                    side="back",
                    student_id=student_id,
                    school_name=school_name,
                    render_scale=render_scale,
                ) or load_static_back_template_image(template_obj, card_width, card_height)

            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            jpg_name = f"card_{template_id}_{student_id}_{timestamp}.jpg"
            back_jpg_name = f"card_{template_id}_{student_id}_{timestamp}_back.jpg" if back_image is not None else None
            pdf_name = f"card_{template_id}_{student_id}_{timestamp}.pdf"

            template = force_rgb(template)
            if back_image is not None:
                back_image = force_rgb(back_image)

            jpg_url = None
            back_jpg_url = None
            pdf_url = None

            if STORAGE_BACKEND == "local":
                os.makedirs(GENERATED_FOLDER, exist_ok=True)
                with open(os.path.join(GENERATED_FOLDER, jpg_name), "wb") as fh:
                    buf = io.BytesIO()
                    template.save(buf, "JPEG", quality=95)
                    buf.seek(0)
                    fh.write(buf.getvalue())

                if back_jpg_name and back_image is not None:
                    with open(os.path.join(GENERATED_FOLDER, back_jpg_name), "wb") as fh:
                        buf = io.BytesIO()
                        back_image.save(buf, "JPEG", quality=95)
                        buf.seek(0)
                        fh.write(buf.getvalue())

                with open(os.path.join(GENERATED_FOLDER, pdf_name), "wb") as fh:
                    buf = io.BytesIO()
                    if back_image is not None:
                        template.save(buf, "PDF", save_all=True, append_images=[back_image], resolution=300)
                    else:
                        template.save(buf, "PDF", resolution=300)
                    buf.seek(0)
                    fh.write(buf.getvalue())

                student.generated_filename = pdf_name
                generated_url = with_cache_bust(url_for('static', filename=f'generated/{jpg_name}'))
                back_generated_url = with_cache_bust(url_for('static', filename=f'generated/{back_jpg_name}')) if back_jpg_name else None
                download_url = url_for('static', filename=f'generated/{pdf_name}')
            else:
                # Convert image to bytes and upload to Cloudinary
                jpg_buffer = io.BytesIO()
                template.save(jpg_buffer, "JPEG", quality=95)
                jpg_buffer.seek(0)
                jpg_url = upload_image(jpg_buffer.getvalue(), folder='cards', resource_type='image')

                # Update URLs for frontend display
                generated_url = with_cache_bust(jpg_url)  # Use Cloudinary URL

                if back_image is not None:
                    back_jpg_buffer = io.BytesIO()
                    back_image.save(back_jpg_buffer, "JPEG", quality=95)
                    back_jpg_buffer.seek(0)
                    back_jpg_url = upload_image(back_jpg_buffer.getvalue(), folder='cards', resource_type='image')
                    back_generated_url = with_cache_bust(back_jpg_url)

                # Upload PDF to Cloudinary as well
                pdf_buffer = io.BytesIO()
                if back_image is not None:
                    template.save(pdf_buffer, "PDF", save_all=True, append_images=[back_image], resolution=300)
                else:
                    template.save(pdf_buffer, "PDF", resolution=300)
                pdf_buffer.seek(0)
                pdf_url = upload_image(pdf_buffer.getvalue(), folder='cards', resource_type='raw')
                download_url = pdf_url  # Use Cloudinary URL
          
            try:
                # Delete old photo if changed
                if student.photo_filename and student.photo_filename != photo_stored:
                    old_photo_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
                    if os.path.exists(old_photo_path):
                        os.remove(old_photo_path)
                
                # Delete old generated files
                if student.generated_filename and student.generated_filename != pdf_name:
                     # Check if old filename stored was PDF or JPG and clean up both variants
                    base_old = os.path.splitext(student.generated_filename)[0]
                    for ext in ['.pdf', '.jpg']:
                        old_file_path = os.path.join(GENERATED_FOLDER, base_old + ext)
                        if os.path.exists(old_file_path):
                            os.remove(old_file_path)
                if student.back_generated_filename and student.back_generated_filename != back_jpg_name:
                    old_back_path = os.path.join(GENERATED_FOLDER, student.back_generated_filename)
                    if os.path.exists(old_back_path):
                        os.remove(old_back_path)
                
                student.name = name
                student.father_name = father_name
                student.class_name = class_name
                student.dob = dob
                student.address = address
                student.phone = phone
                if STORAGE_BACKEND == "local":
                    student.photo_url = None
                    student.photo_filename = photo_stored
                    student.image_url = None
                    student.back_image_url = None
                    student.pdf_url = None
                    student.back_generated_filename = back_jpg_name
                else:
                    student.photo_url = photo_url
                    student.photo_filename = photo_stored if photo_stored and not str(photo_stored).startswith("http") else None
                    # Store Cloudinary URLs instead of local filenames
                    student.image_url = jpg_url
                    student.back_image_url = back_jpg_url
                    student.pdf_url = pdf_url
                    student.back_generated_filename = None
                student.data_hash = data_hash
                student.template_id = template_id
                student.school_name = school_name
                student.custom_data = custom_data # <--- SAVE DYNAMIC FIELDS
                
                db.session.commit()
                
                success = "ID card updated successfully"
                form_data['photo_filename'] = photo_stored
                form_data['generated_filename'] = jpg_name
                form_data['custom_data'] = custom_data
                
            except Exception as e:
                error = f"Database error: {str(e)}"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 500
      
        except Exception as e:
            error = f"Error generating ID card: {str(e)}"
            logger.error(error)
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 500
  
    return render_template("edit.html", generated_url=generated_url, back_generated_url=back_generated_url, download_url=download_url,
                          form_data=form_data, success=success, error=error, templates=templates)


@app.route("/admin", methods=["GET"])
def admin():
    if 'student_email' not in session and not session.get("admin"):
        flash('Please log in to access the admin panel.', 'error')
        return redirect(url_for('student_login'))
    
    success = request.args.get("success")
    error = request.args.get("error")
    
    try:
        if session.get("admin"):
            # RBAC: Super admin sees all, School admin sees only their school
            if session.get("admin_role") == "school_admin":
                rows = db.session.query(Student).filter_by(school_name=session.get("admin_school")).order_by(Student.created_at.desc()).all()
            else:
                rows = db.session.query(Student).order_by(Student.created_at.desc()).all()
        else:
            # Students see only their records
            rows = db.session.query(Student).filter_by(
                email=session['student_email']
            ).order_by(Student.created_at.desc()).all()
        
        # FIX: Make sure get_templates() returns a list
        templates_list = get_templates()
        
        # Log for debugging
        logger.info(f"Admin panel loaded - User: {session.get('student_email') or 'admin'}, Records found: {len(rows)}, Templates: {len(templates_list)}")
        
        available_fonts = get_available_fonts()
        
        # FIX: Handle empty templates list
        if templates_list and isinstance(templates_list, list) and len(templates_list) > 0:
            current_settings = templates_list[0]['font_settings'] if 'font_settings' in templates_list[0] else get_default_font_config()
            photo_settings = templates_list[0]['photo_settings'] if 'photo_settings' in templates_list[0] else get_default_photo_config()
            qr_settings = templates_list[0]['qr_settings'] if 'qr_settings' in templates_list[0] else get_default_qr_config()
        else:
            current_settings = get_default_font_config()
            photo_settings = get_default_photo_config()
            qr_settings = get_default_qr_config()
        
        duplicate_settings = load_duplicate_config()
        
        pdf_sheets = [f for f in os.listdir(GENERATED_FOLDER) if f.endswith('.pdf') and f.startswith('sheet_template_')]
        pdf_sheets_by_template = defaultdict(list)
        for f in pdf_sheets:
            parts = f.split('_')
            if len(parts) >= 4 and parts[0] == 'sheet' and parts[1] == 'template':
                try:
                    template_id = int(parts[2])
                    pdf_sheets_by_template[template_id].append(f)
                except ValueError:
                    continue
        
        template_rows = {}
        for row in rows:
            template_id = row.template_id
            if template_id not in template_rows:
                template_rows[template_id] = []
            template_rows[template_id].append(row)
        
        schools_data = {}
        for row in rows:
            school_name = row.school_name or "Unknown School"
            if school_name not in schools_data:
                schools_data[school_name] = []
            schools_data[school_name].append(row)
        
        # Calculate arrangement info for each template
        template_arrangements = {}
        for template in templates_list:
            orientation = template.get('card_orientation', 'landscape')
            if orientation == 'landscape':
                template_arrangements[template['id']] = "2 columns × 5 rows (10 cards/A4)"
            else:
                template_arrangements[template['id']] = "5 columns × 2 rows (10 cards/A4)"
        
        logger.info("Admin panel loaded successfully")
        return render_template(
            "admin.html",
            rows=rows,
            templates=templates_list,  # Use the list variable
            template_rows=template_rows,
            available_fonts=available_fonts,
            current_settings=current_settings,
            photo_settings=photo_settings,
            qr_settings=qr_settings,
            duplicate_settings=duplicate_settings,
            pdf_sheets=pdf_sheets,
            pdf_sheets_by_template=pdf_sheets_by_template,
            success=success,
            error=error,
            schools_data=schools_data,
            is_admin=session.get("admin", False),
            template_arrangements=template_arrangements
        )
    except Exception as e:
        logger.error(f"Error loading admin panel: {e}")
        return render_template(
            "admin.html",
            error=f"Error loading admin panel: {str(e)}",
            rows=[],
            templates=[],
            template_rows={},
            available_fonts=[],
            current_settings={},
            photo_settings={},
            qr_settings={},
            duplicate_settings={},
            pdf_sheets=[],
            pdf_sheets_by_template=defaultdict(list),
            schools_data={},
            is_admin=session.get("admin", False),
            template_arrangements={}
        ), 500
  

# ================== Compile School Sheets Route ==================
@app.route("/admin/download_compiled_school_pdf/<int:template_id>")
def download_compiled_school_pdf(template_id):
    """
    Compile using the vector engine so latest template text settings (including
    text-case transformation for both labels and values) are always applied.
    """
    if not session.get("admin"):
        return redirect(url_for("login"))
    mode = (request.args.get("mode") or "").strip().lower() or "print"
    if mode not in {"print", "editable"}:
        mode = "print"
    return redirect(url_for("corel.download_compiled_vector_pdf", template_id=template_id, mode=mode))

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
        return redirect(url_for("admin", error="Front template file and school name are required"))
        
    # Enforce RBAC for School Admins (can only upload to their assigned school)
    if session.get("admin_role") == "school_admin" and school_name != session.get("admin_school"):
        return redirect(url_for("admin", error="You can only upload templates for your assigned school."))
    
    file = front_file
    card_orientation = request.form.get('card_orientation', 'landscape')
    template_mode = (request.form.get('template_mode', 'single') or 'single').strip().lower()
    is_double_sided = template_mode == 'double'
    
    if file.filename == '' or not school_name:
        logger.error("No file selected or school name empty")
        return redirect(url_for("admin", error="No file selected or school name empty"))
    if is_double_sided and (back_file is None or back_file.filename == ''):
        logger.error("Double-sided template requires both front and back files")
        return redirect(url_for("admin", error="Double-sided template requires both front and back files"))
    
    # Capture side-aware language inputs
    language = (request.form.get('language', 'english') or 'english').strip().lower()
    back_language = (request.form.get('back_language') or language).strip().lower()
    text_direction = (request.form.get('text_direction') or default_text_direction_for_language(language)).strip().lower()
    back_text_direction = (request.form.get('back_text_direction') or default_text_direction_for_language(back_language)).strip().lower()

    if language not in SUPPORTED_TEMPLATE_LANGUAGES:
        return redirect(url_for("admin", error="Invalid front language selected"))
    if back_language not in SUPPORTED_TEMPLATE_LANGUAGES:
        return redirect(url_for("admin", error="Invalid back language selected"))
    if text_direction not in {"ltr", "rtl"}:
        text_direction = default_text_direction_for_language(language)
    if back_text_direction not in {"ltr", "rtl"}:
        back_text_direction = default_text_direction_for_language(back_language)
    if is_double_sided and not validate_double_sided_language_pair(language, back_language):
        return redirect(url_for(
            "admin",
            error="Invalid language selection for double-sided template.",
        ))

    if file and file.filename.lower().endswith(('.pdf', '.jpg', '.jpeg', '.png')):
        try:
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
            return redirect(url_for("admin", success="Template uploaded successfully"))
        except Exception as e:
            logger.error(f"Error uploading template: {e}")
            return redirect(url_for("admin", error=f"Error uploading template: {str(e)}"))
    else:
        logger.error("Invalid file format for template")
        return redirect(url_for("admin", error="Invalid file format. Use PDF, JPG, or PNG"))

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
            return redirect(url_for("admin", error="Template not found"))
        
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
                return redirect(url_for("admin", error=error_message))

        # Optional advanced visual-editor layout config:
        # stores per-field label/value x,y and visibility flags.
        if request.is_json and isinstance(data, dict) and "layout_config" in data:
            incoming_layout = data.get("layout_config")
            parsed_layout = parse_layout_config(incoming_layout)
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
                "label_x": safe_get_int(font_settings_data, "label_x", default_label_x),
                "value_x": safe_get_int(font_settings_data, "value_x", default_value_x),
                "start_y": safe_get_int(font_settings_data, "start_y", default_start_y),
                "line_height": safe_get_int(font_settings_data, "line_height", default_line_height),
                "text_case": safe_get_nested(font_settings_data, "text_case", default="normal"),
                "show_label_colon": safe_get_bool(font_settings_data, "show_label_colon", True),
                "align_label_colon": safe_get_bool(font_settings_data, "align_label_colon", True),
                "label_colon_gap": safe_get_int(font_settings_data, "label_colon_gap", 8),
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
                        except:
                            return default
                    elif ',' in color:
                        try:
                            parts = color.split(',')
                            if len(parts) >= 3:
                                return [int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip())]
                        except:
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
        else:
            font_settings = {
                "font_bold": request.form.get("font_bold", "arialbd.ttf"),
                "font_regular": request.form.get("font_regular", "arial.ttf"),
                "label_font_size": get_form_int(request.form, "label_font_size", default_label_size),
                "value_font_size": get_form_int(request.form, "value_font_size", default_value_size),
                "label_x": get_form_int(request.form, "label_x", default_label_x),
                "value_x": get_form_int(request.form, "value_x", default_value_x),
                "start_y": get_form_int(request.form, "start_y", default_start_y),
                "line_height": get_form_int(request.form, "line_height", default_line_height),
                "text_case": request.form.get("text_case", "normal"),
                # Unchecked checkboxes are omitted from form posts.
                "show_label_colon": (request.form.get("show_label_colon", "off").strip().lower() in {"1", "true", "yes", "on"}),
                "align_label_colon": (request.form.get("align_label_colon", "off").strip().lower() in {"1", "true", "yes", "on"}),
                "label_colon_gap": get_form_int(request.form, "label_colon_gap", 8),
            }
            
            # Handle font colors from form data
            label_font_color = request.form.get("label_font_color", "0,0,0").strip()
            value_font_color = request.form.get("value_font_color", "0,0,0").strip()
            colon_font_color = request.form.get("colon_font_color", label_font_color).strip()
            
            # Robust Color Parser
            def parse_color(c):
                # If it's already a list (e.g. from JSON), validate and return it
                if isinstance(c, list):
                    if len(c) >= 3:
                        try:
                            return [int(x) for x in c[:3]]
                        except:
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
                        except:
                            return [0, 0, 0]
                    # Handle "r,g,b" string
                    try:
                        parts = c.split(',')
                        if len(parts) >= 3:
                            return [int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip())]
                    except:
                        pass
                
                return [0, 0, 0]
            
            font_settings["label_font_color"] = parse_color(label_font_color)
            font_settings["value_font_color"] = parse_color(value_font_color)
            font_settings["colon_font_color"] = parse_color(colon_font_color)
        
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
                if qr_settings["qr_include_logo"]:
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
                if request.form.get("qr_include_logo"):
                    qr_settings["qr_include_logo"] = True
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
        
        # Update template settings in the database
        try:
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
            return redirect(url_for("admin", error=f"Failed to update template settings: {str(e)}"))
        
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
            return redirect(url_for("admin", success=f"Template settings updated successfully - {arrangement}"))
        
    except ValueError as e:
        logger.error(f"Invalid input in template settings: {e}")
        error_msg = f"Invalid input in template settings: {str(e)}"
        if request.is_json:
            return jsonify({'success': False, 'error': error_msg}), 400
        return redirect(url_for("admin", error=error_msg))
    except Exception as e:
        logger.error(f"Error updating template settings: {e}")
        error_msg = f"Error updating template settings: {str(e)}"
        if request.is_json:
            return jsonify({'success': False, 'error': error_msg}), 500
        return redirect(url_for("admin", error=error_msg))
    
@app.template_filter('rgb_to_hex')
def rgb_to_hex(rgb_list):
    if isinstance(rgb_list, list) and len(rgb_list) == 3:
        try:
            clamped = [max(0, min(255, int(c))) for c in rgb_list]
            return '#{:02x}{:02x}{:02x}'.format(*clamped)
        except (ValueError, TypeError):
            pass
    return '#000000'

@app.route("/upload_font", methods=["POST"])
def upload_font():
    if 'font' not in request.files:
        logger.error("No font file uploaded")
        return redirect(url_for("admin", error="No font file uploaded"))
    file = request.files['font']
    if file.filename == '':
        logger.error("No font file selected")
        return redirect(url_for("admin", error="No font file selected"))
    if file and file.filename.lower().endswith(('.ttf', '.otf')):
        filename = secure_filename(file.filename)
        file_path = os.path.join(FONTS_FOLDER, filename)
        try:
            file.save(file_path)
            if is_valid_font_file(file_path):
                logger.info(f"Font uploaded: {filename}")
                return redirect(url_for("admin", success="Font uploaded successfully"))
            else:
                os.remove(file_path)
                logger.error(f"Invalid font file: {filename}")
                return redirect(url_for("admin", error="Invalid font file"))
        except Exception as e:
            logger.error(f"Error uploading font: {e}")
            return redirect(url_for("admin", error=f"Error uploading font: {str(e)}"))
    else:
        logger.error("Invalid font file format")
        return redirect(url_for("admin", error="Invalid file format. Use TTF or OTF"))

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
            return redirect(url_for("admin", success="Font settings updated successfully"))
        else:
            return redirect(url_for("admin", error="No templates available"))
    except Exception as e:
        logger.error(f"Error in legacy font update: {e}")
        return redirect(url_for("admin", error="Please use template-specific settings"))

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
            return redirect(url_for("admin", success="Photo settings updated successfully"))
        else:
            return redirect(url_for("admin", error="No templates available"))
    except Exception as e:
        logger.error(f"Error in legacy photo update: {e}")
        return redirect(url_for("admin", error="Please use template-specific settings"))

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
        return redirect(url_for("admin", success="Duplicate settings updated successfully"))
    except Exception as e:
        logger.error(f"Error updating duplicate settings: {e}")
        return redirect(url_for("admin", error=f"Error updating duplicate settings: {str(e)}"))

@app.route("/delete_all", methods=["POST"])
def delete_all():
    # Enforce RBAC: Only super admin can nuke the entire database
    if session.get("admin_role") != "super_admin":
        return redirect(url_for("admin", error="Only super administrators can perform global deletion."))
        
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
        return redirect(url_for("admin", success="All data and files deleted successfully"))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting all data: {e}")
        return redirect(url_for("admin", error=f"Error deleting data: {str(e)}"))

@app.route("/delete_student/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    if not session.get("admin"):
        return redirect(url_for('student_login'))

    try:
        student = db.session.get(Student, student_id)
        if not student:
            return redirect(url_for("admin", error="Student not found"))
        
        # Enforce RBAC
        if session.get("admin_role") == "school_admin" and student.school_name != session.get("admin_school"):
            return redirect(url_for("admin", error="Unauthorized to delete student from this school."))

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
        return redirect(url_for("admin", success="Student deleted successfully"))

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting student {student_id}: {e}")
        return redirect(url_for("admin", error=f"Error deleting student: {str(e)}"))
    
@app.route("/delete_pdf/<path:filename>", methods=["POST"])
def delete_pdf(filename):
    pdf_path = os.path.join(GENERATED_FOLDER, filename)
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
        logger.info(f"Deleted PDF: {filename}")
        return redirect(url_for("admin", success="PDF deleted successfully"))
    else:
        return redirect(url_for("admin", error="PDF not found"))

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
        return redirect(url_for("admin", error=f"Error exporting CSV: {str(e)}"))

@app.route("/download_template/<int:template_id>")
def download_template(template_id):
    if not session.get("admin"):
        return redirect(url_for("login"))
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
        return redirect(url_for("admin", error="No template available for download"))


@app.route("/remove_template/<int:template_id>", methods=["POST"])
def remove_template(template_id):
    try:
        template = db.session.get(Template, template_id)


        if not template:
            return redirect(url_for("admin", error="Template not found"))
        
        # Enforce RBAC
        if session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
            return redirect(url_for("admin", error="Unauthorized to remove template for this school."))
            
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
        return redirect(url_for("admin", success="Template removed successfully"))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error removing template {template_id}: {e}")
        return redirect(url_for("admin", error=f"Error removing template: {str(e)}"))

# ================== FULL ADMIN PREVIEW ROUTE (Memory Only - No Files) ==================
@app.route("/admin_preview_card", methods=["POST"])
def admin_preview_card():
    try:
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
        data = request.get_json()
        if not data: return jsonify({"success": False, "error": "No data received"}), 400
        template_id = data.get("template_id")
        side = (data.get("side") or "front").strip().lower()
        
        font_settings = {**get_default_font_config(), **data.get("font_settings", {})}
        photo_settings = {**get_default_photo_config(), **data.get("photo_settings", {})}
        qr_settings = {**get_default_qr_config(), **data.get("qr_settings", {})}
        
        # 2. Load Template
        template_path = get_template_path(template_id, side=side)
        if not template_path:
            return jsonify({"success": False, "error": "Template not found in database"}), 404
        
        try:
            card_width, card_height = get_card_size(template_id)
            template_img = load_template_smart(template_path).resize((card_width, card_height))
        except Exception as e:
            logger.error(f"Error loading template {template_id} from {template_path}: {e}")
            err_text = str(e)
            status_code = 422 if ("re-upload" in err_text.lower() or "network/dns" in err_text.lower()) else 500
            return jsonify({"success": False, "error": f"Failed to load template: {err_text}"}), status_code
        
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
        except:
            L_COLOR = V_COLOR = (0, 0, 0)
            C_COLOR = L_COLOR
        
        # --- LANGUAGE LOGIC ---
        template = db.session.get(Template, template_id)
        lang, direction = get_template_language_direction(template_id, side=side)

        # Standard Labels
        std_labels = {
            'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
            'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
            'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
            'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
        }
        
        sample_data_map = {
            'english': {'NAME': 'John Doe', 'F_NAME': 'Richard Roe', 'CLASS': 'X - A', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': '123 Long Street Name, Apartment 4B, Big City District, State 560001'},
            'urdu':    {'NAME': 'محمد علی', 'F_NAME': 'احمد علی', 'CLASS': 'دہم - اے', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': 'مکان نمبر 123، سٹریٹ 4، لاہور، پاکستان'},
            'hindi':   {'NAME': 'राहुल कुमार', 'F_NAME': 'अमित कुमार', 'CLASS': '१० - ए', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': 'मकान नं. १२३, गली नं. ४, दिल्ली'},
            'arabic':  {'NAME': 'محمد أحمد', 'F_NAME': 'علي أحمد', 'CLASS': 'العاشر - أ', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': 'شارع الملك فيصل، مبنى ٤، الرياض'}
        }

        labels_map = std_labels.get(lang, std_labels['english'])
        values_map = sample_data_map.get(lang, sample_data_map['english'])

        layout_config_raw = get_template_layout_config(template_id, side=side)
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
            except: pass

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
            if advances_flow:
                current_y = max(int(current_y), int(label_y_eff), int(value_y_eff))
            l_font = load_safe_font(
                font_settings.get("font_bold", "arialbd.ttf"),
                label_font_size_eff,
                lang,
                label_text_final,
            )
            colon_font = load_safe_font(
                font_settings.get("font_bold", "arialbd.ttf"),
                colon_font_size_eff,
                lang,
                colon_text_final or ":",
            )
            if layout_item["label_visible"]:
                label_draw_x = flip_x_for_text_direction(
                    label_x_eff,
                    label_text_final,
                    l_font,
                    card_width,
                    direction,
                    draw=draw,
                    grow_mode=layout_item["label_grow"],
                )
                draw.text(
                    (label_draw_x, label_y_eff),
                    label_text_final,
                    font=l_font,
                    fill=label_fill,
                    **get_draw_text_kwargs(label_text_final, lang),
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

            # --- ADDRESS LOGIC ---
            if item['label'] == labels_map['ADDRESS']:
                curr_size = value_font_size_eff
                min_size = 12 
                wrapped_addr = []
                while curr_size >= min_size:
                    avg_char_w = curr_size * 0.55
                    chars_limit = max(5, int(max_w / max(avg_char_w, 1)))
                    wrapped_addr = safe_wrap_preview(raw_val, chars_limit)
                    if len(wrapped_addr) <= 2: break
                    curr_size -= 2
                
                # Load safe font for address using real shaped text (better glyph matching)
                addr_font = load_safe_font(
                    font_settings.get("font_regular", "arial.ttf"),
                    curr_size,
                    lang,
                    display_val,
                )

                for line in wrapped_addr[:2]:
                    line_display = process_text_for_drawing(line, lang)
                    spacing = line_height if curr_size > 20 else curr_size + 5
                    if layout_item["value_visible"]:
                        value_draw_x = flip_x_for_text_direction(
                            value_x_eff,
                            line_display,
                            addr_font,
                            card_width,
                            direction,
                            draw=draw,
                            grow_mode=layout_item["value_grow"],
                        )
                        draw.text(
                            (value_draw_x, value_y_eff),
                            line_display,
                            font=addr_font,
                            fill=value_fill,
                            **get_draw_text_kwargs(line_display, lang),
                        )
                    value_y_eff += spacing
                    if advances_flow:
                        current_y += spacing

            # --- STANDARD FIELDS ---
            else:
                v_font, _ = fit_loaded_font_to_single_line(
                    draw,
                    lambda size: load_safe_font(
                        font_settings.get("font_regular", "arial.ttf"),
                        size,
                        lang,
                        display_val,
                    ),
                    display_val,
                    max_w,
                    value_font_size_eff,
                    language=lang,
                )
                if layout_item["value_visible"]:
                    value_draw_x = flip_x_for_text_direction(
                        value_x_eff,
                        display_val,
                        v_font,
                        card_width,
                        direction,
                        draw=draw,
                        grow_mode=layout_item["value_grow"],
                    )
                    draw.text(
                        (value_draw_x, value_y_eff),
                        display_val,
                        font=v_font,
                        fill=value_fill,
                        **get_draw_text_kwargs(display_val, lang),
                    )
                if advances_flow:
                    current_y += line_height
                        
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
                ph = round_photo(ph, radii)
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
        logger.warning(f"FINAL IMAGE MODE BEFORE SAVE: {template_img.mode}")
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
            return redirect(url_for("admin", error=f"No sheet found for template {template_id}"))
      
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
        return redirect(url_for("admin", success=success_msg))
      
    except Exception as e:
        logger.error(f"Error forcing PDF generation for template {template_id}: {e}")
        return redirect(url_for("admin", error=f"Error generating PDF: {str(e)}"))

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


def _set_bulk_job_state(task_id, **updates):
    task = jobs.setdefault(task_id, {"task_id": task_id})
    task.update(updates)
    try:
        _redis_set(
            _redis_cache_key("bulk_job", task_id),
            json.dumps(task, default=str).encode("utf-8"),
            ttl=86400,
        )
    except Exception as exc:
        logger.warning("Failed to publish bulk job state for %s: %s", task_id, exc)


def _get_bulk_job_state(task_id):
    cached = _redis_get(_redis_cache_key("bulk_job", task_id))
    if cached:
        try:
            if isinstance(cached, bytes):
                cached = cached.decode("utf-8")
            return json.loads(cached)
        except Exception as exc:
            logger.warning("Failed to decode cached bulk job state for %s: %s", task_id, exc)
    return jobs.get(task_id)


def _publish_bulk_job_errors(task_id, errors):
    _set_bulk_job_state(
        task_id,
        errors=list(errors[:10]),
        error_count=len(errors),
        first_error=(errors[0] if errors else None),
    )



# =========================================================
# BACKGROUND THREAD WORKER (Pure SQLAlchemy)
# =========================================================
def background_bulk_generate(task_id, template_id, excel_path, photo_map):
    """
    Background thread to process bulk generation without blocking the server.
    Uses SQLAlchemy ORM for all database operations.
    """
    with app.app_context():
        success_count = 0
        skipped_count = 0
        error_count = 0
        total_records = 0
        errors = []
        try:
            _set_bulk_job_state(task_id, state='PROCESSING', status='Reading Excel file...')

            if excel_path.endswith('.csv'):
                df = pd.read_csv(excel_path)
            else:
                df = pd.read_excel(excel_path, engine='openpyxl')

            df.columns = df.columns.str.strip().str.lower()
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

                    used_photo = "placeholder.jpg"
                    clean_name = name.lower().strip()
                    for alias in photo_match_aliases(clean_name):
                        if alias in photo_map:
                            used_photo = photo_map[alias]
                            break
                    else:
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

                    side_render_student = SimpleNamespace(
                        name=name,
                        father_name=father_name,
                        class_name=class_name,
                        dob=dob,
                        address=address,
                        phone=phone,
                        photo_url=used_photo if str(used_photo or "").startswith("http") else None,
                        photo_filename=used_photo if used_photo and not str(used_photo).startswith("http") and used_photo != "placeholder.jpg" else None,
                        custom_data=custom_data,
                        school_name=school_name,
                        _template_fields=dynamic_fields,
                        _prepared_photo_cache=photo_cache,
                    )

                    student_id = getattr(side_render_student, "id", None)
                    render_scale = 1.0
                    school_name = getattr(template_obj, "school_name", None) or getattr(student, "school_name", "")

                    
                    front_image = _get_cached_final_card(
                        template_obj,
                        side_render_student,
                        side="front",
                        student_id=student_id,
                        school_name=school_name,
                        render_scale=render_scale,
                    )
                    
                    if front_image is None:
                        raise RuntimeError("Failed to render front card image")
                    
                    back_image = None
                    if getattr(template_obj, "is_double_sided", False):
                        back_image = _get_cached_final_card(
                            template_obj,
                            side_render_student,
                            side="back",
                            student_id=student_id,
                            school_name=school_name,
                            render_scale=render_scale,
                        ) or load_static_back_template_image(template_obj, card_width, card_height)
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

                    student = Student(
                        name=name,
                        father_name=father_name,
                        class_name=class_name,
                        dob=dob,
                        address=address,
                        phone=phone,
                        photo_url=None if STORAGE_BACKEND == "local" else (used_photo if str(used_photo).startswith("http") else None),
                        photo_filename=used_photo if STORAGE_BACKEND == "local" else (used_photo if used_photo and not str(used_photo).startswith("http") else None),
                        image_url=None if STORAGE_BACKEND == "local" else image_url,
                        back_image_url=None if STORAGE_BACKEND == "local" else back_image_url,
                        pdf_url=None,
                        generated_filename=generated_filename if STORAGE_BACKEND == "local" else None,
                        back_generated_filename=back_generated_filename if STORAGE_BACKEND == "local" else None,
                        created_at=datetime.now(timezone.utc),
                        data_hash=data_hash,
                        template_id=template_id,
                        school_name=school_name,
                        custom_data=custom_data,
                    )
                    pending_rows.append({
                        "student": student,
                        "data_hash": data_hash,
                        "row_number": idx + 2,
                        "cleanup_paths": cleanup_paths,
                    })
                    _flush_pending_rows(force=False)

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
                )
            elif success_count == 0 and skipped_count > 0 and error_count == 0:
                _set_bulk_job_state(
                    task_id,
                    state='SUCCESS',
                    status='Completed with no new cards created.',
                    result=summary,
                )
            elif error_count > 0:
                _set_bulk_job_state(
                    task_id,
                    state='SUCCESS',
                    status='Completed with some row errors.',
                    result=summary,
                )
            else:
                _set_bulk_job_state(
                    task_id,
                    state='SUCCESS',
                    status='Completed',
                    result=summary,
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
    
    template_obj = db.session.get(Template, template_id)
    if not template_obj:
        return jsonify({"success": False, "error": "Template not found", "errors": ["Template not found"]}), 404

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
        )
        queue = get_task_queue()
        if queue is not None:
            try:
                job = queue.enqueue(
                    background_bulk_generate,
                    args=(task_id, template_id, excel_path, photo_map),
                    job_id=task_id,
                    job_timeout='1h',
                )
                task_id = job.get_id()
            except Exception as queue_error:
                logger.warning("RQ enqueue failed; using local executor: %s", queue_error)
                executor.submit(background_bulk_generate, task_id, template_id, excel_path, photo_map)
        else:
            logger.warning("Redis/RQ unavailable; using local executor for bulk generation.")
            executor.submit(background_bulk_generate, task_id, template_id, excel_path, photo_map)

        # Log Activity
        log_activity("Bulk Generation Started", 
                     target=f"Template ID: {template_id}", 
                     details=f"Task ID: {task_id}, Excel: {filename}, Photos: {len(photo_map)}")

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
    
@app.route('/taskstatus/<task_id>')
def taskstatus(task_id):
    task = _get_bulk_job_state(task_id)
    if not task:
        return jsonify({'state': 'FAILURE', 'status': 'Task not found', 'errors': ['Task not found']}), 404
    return jsonify(task)

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
        return redirect(url_for("login"))
    
    templates = Template.query.all()
    return render_template("bulk_upload_guide.html", templates=templates)

# ================== CORRECTED FORM FIELD ROUTES ==================

# ================== CORRECTED: Allow Students to View Fields ==================
@app.route('/admin/template/<int:template_id>/form-fields', methods=['GET', 'POST'])
def manage_template_fields(template_id):
    # 1. Check if user is logged in (Either as Admin OR Student)
    is_admin = session.get("admin")
    is_student = session.get("student_email")

    if not is_admin and not is_student:
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    try:
        # GET: Fetch fields (Allowed for Admin AND Student)
        if request.method == 'GET':
            template = db.session.get(Template, template_id)
            if not template:
                return jsonify({"success": False, "message": "Template not found"}), 404
            
            fields = []
            db_fields = TemplateField.query.filter_by(template_id=template_id)\
                        .order_by(TemplateField.display_order.asc()).all()
                        
            for field in db_fields:
                field_dict = {
                    'id': field.id,
                    'template_id': field.template_id,
                    'field_name': field.field_name,
                    'field_label': field.field_label,
                    'field_type': field.field_type,
                    'is_required': field.is_required,
                    'show_label_front': bool(getattr(field, 'show_label_front', True)),
                    'show_value_front': bool(getattr(field, 'show_value_front', True)),
                    'show_label_back': bool(getattr(field, 'show_label_back', False)),
                    'show_value_back': bool(getattr(field, 'show_value_back', False)),
                    'display_order': field.display_order,
                    'field_options': field.field_options or []
                }
                fields.append(field_dict)
            return jsonify(fields)
        
        # POST: Add a new field (Restricted to ADMIN ONLY)
        elif request.method == 'POST':
            if not is_admin:
                return jsonify({"success": False, "message": "Admin privileges required"}), 403

            data = request.json
            if not data:
                return jsonify({"success": False, "message": "No data received"}), 400

            field_name = data.get('field_name', '').strip().lower().replace(' ', '_')
            field_label = data.get('field_label', '').strip()
            field_type = data.get('field_type', 'text')
            
            # Safe Boolean Conversion
            raw_req = data.get('is_required', 0)
            is_required = str(raw_req).lower() in ['true', '1', 'on', 'yes']
            show_label_front = str(data.get('show_label_front', 1)).lower() in ['true', '1', 'on', 'yes']
            show_value_front = str(data.get('show_value_front', 1)).lower() in ['true', '1', 'on', 'yes']
            show_label_back = str(data.get('show_label_back', 0)).lower() in ['true', '1', 'on', 'yes']
            show_value_back = str(data.get('show_value_back', 0)).lower() in ['true', '1', 'on', 'yes']
            
            display_order = int(data.get('display_order', 0))
            field_options = data.get('field_options', [])
            
            if not field_name or not field_label:
                return jsonify({"success": False, "message": "Name and Label are required"}), 400
            if not any([show_label_front, show_value_front, show_label_back, show_value_back]):
                return jsonify({"success": False, "message": "Enable at least one label or value side visibility"}), 400
            
            field = TemplateField(
                template_id=template_id,
                field_name=field_name,
                field_label=field_label,
                field_type=field_type,
                is_required=is_required,
                show_label_front=show_label_front,
                show_value_front=show_value_front,
                show_label_back=show_label_back,
                show_value_back=show_value_back,
                display_order=display_order,
                field_options=field_options
            )
            db.session.add(field)
            db.session.commit()
            
            return jsonify({"success": True, "message": "Field added successfully", "id": field.id})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error managing fields for template {template_id}: {e}")
        return jsonify({"success": False, "message": f"Server Error: {str(e)}"}), 500
    
@app.route('/admin/template/form-fields/<int:field_id>', methods=['PUT', 'DELETE'])
def manage_single_field(field_id):
    if not session.get("admin"):
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    
    try:
        field = db.session.get(TemplateField, field_id)
        if not field:
            return jsonify({"success": False, "message": "Field not found"}), 404
        
        # DELETE: Remove a field
        if request.method == 'DELETE':
            db.session.delete(field)
            db.session.commit()
            return jsonify({"success": True, "message": "Field deleted successfully"})
            
        # PUT: Update a field
        elif request.method == 'PUT':
            data = request.json
            if not data:
                return jsonify({"success": False, "message": "No data provided"}), 400
            
            if 'field_label' in data:
                field.field_label = data.get('field_label').strip()
            
            if 'field_type' in data:
                field.field_type = data.get('field_type')
            
            if 'is_required' in data:
                # SAFE BOOLEAN CONVERSION
                raw_req = data.get('is_required')
                field.is_required = str(raw_req).lower() in ['true', '1', 'on', 'yes']

            if 'show_label_front' in data:
                field.show_label_front = str(data.get('show_label_front')).lower() in ['true', '1', 'on', 'yes']

            if 'show_value_front' in data:
                field.show_value_front = str(data.get('show_value_front')).lower() in ['true', '1', 'on', 'yes']

            if 'show_label_back' in data:
                field.show_label_back = str(data.get('show_label_back')).lower() in ['true', '1', 'on', 'yes']

            if 'show_value_back' in data:
                field.show_value_back = str(data.get('show_value_back')).lower() in ['true', '1', 'on', 'yes']

            if not any([
                bool(getattr(field, 'show_label_front', True)),
                bool(getattr(field, 'show_value_front', True)),
                bool(getattr(field, 'show_label_back', False)),
                bool(getattr(field, 'show_value_back', False)),
            ]):
                return jsonify({"success": False, "message": "Enable at least one label or value side visibility"}), 400

            if 'display_order' in data:
                field.display_order = int(data.get('display_order'))
                
            if 'field_options' in data:
                # Ensure it's stored as a new list to trigger SQLAlchemy updates
                field.field_options = list(data.get('field_options', []))
            
            db.session.commit()
            return jsonify({"success": True, "message": "Field updated successfully"})

    except Exception as e:
        db.session.rollback() # Important: Rollback on error
        logger.error(f"Error updating/deleting field {field_id}: {e}")
        return jsonify({"success": False, "message": f"Server Error: {str(e)}"}), 500
    
# ================== Versatile Verification Route ==================
@app.route("/verify/<student_identifier>")
def verify_student(student_identifier):
    """
    Public route to verify student details via QR code scan.
    Prioritizes Cloudinary URLs over local files.
    """
    try:
        # Clean the input
        identifier = str(student_identifier).strip()
        
        # Search by ID OR by Hash (handling the 10-char hash used in QR generation)
        student = Student.query.filter(
            (db.cast(Student.id, db.String) == identifier) |
            (db.func.substr(Student.data_hash, 1, 10) == identifier)
        ).first()
        
        if not student:
            return render_template("verify.html", error="Student record not found.", valid=False)
        
        # --- FIX: LOGIC TO CHOOSE THE CORRECT PHOTO URL ---
        final_photo_url = None

        # 1. Check persisted photo reference
        photo_url, local_photo_path = resolve_student_photo_reference(student)
        if photo_url:
            final_photo_url = photo_url
        elif local_photo_path:
            final_photo_url = url_for('static', filename=f"Uploads/{os.path.basename(local_photo_path)}")
            
        # 3. Default to Placeholder if nothing else exists
        else:
            # Use the neutral photo placeholder (not the legacy `placeholder.jpg`, which may be
            # user-replaced and can render as a "mini ID card" inside the photo box).
            final_photo_url = url_for('static', filename=os.path.basename(PLACEHOLDER_PATH))
        # --------------------------------------------------

        # Prepare data object
        student_data = {
            "name": student.name,
            "father_name": student.father_name,
            "school_name": student.school_name,
            "photo_url": final_photo_url,  # <--- Using the fixed URL
            "class_name": student.class_name, # Added class name for better verification
            "status": "Verified"
        }
        
        return render_template("verify.html", student=student_data, valid=True)
        
    except Exception as e:
        logger.error(f"Database error during verification: {e}")
        return render_template("verify.html", error="System error.", valid=False), 500


@app.route('/download_school_excel/<int:template_id>')
def download_school_excel(template_id):
    # 1. Security Check
    if not session.get("admin"):
        flash('Please log in as admin to download data.', 'error')
        return redirect(url_for('login'))

    try:
        # 2. Get Template info for filename
        template = db.session.get(Template, template_id)
        if not template:
            flash('Template not found.', 'error')
            return redirect(url_for('admin'))

        # 3. Get students for this specific template
        students = Student.query.filter_by(template_id=template_id).all()

        if not students:
            flash('No students found for this school.', 'warning')
            return redirect(url_for('admin'))

        # 4. Prepare Data
        data = []
        for student in students:
            # Base fields
            row = {
                'ID': student.id,
                'Name': student.name,
                'Father Name': student.father_name,
                'Class': student.class_name,
                'DOB': student.dob,
                'Address': student.address,
                'Phone': student.phone,
                'Email': student.email,
                'Photo': student.photo_filename,
                'Created At': student.created_at.strftime('%Y-%m-%d %H:%M:%S') if student.created_at else ''
            }

            # Handle Dynamic Fields (unpack custom_data dictionary)
            # This ensures fields like "Blood Group" get their own column
            if student.custom_data and isinstance(student.custom_data, dict):
                for key, val in student.custom_data.items():
                    # Format key (e.g., "blood_group" -> "Blood Group")
                    header = key.replace('_', ' ').title()
                    row[header] = val

            data.append(row)

        # 5. Create DataFrame
        df = pd.DataFrame(data)

        # 6. Save to Memory Buffer
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Student Data')
        
        output.seek(0)
        
        # 7. Generate clean filename
        clean_school_name = secure_filename(template.school_name)
        timestamp = datetime.now().strftime("%Y%m%d")
        filename = f"{clean_school_name}_Data_{timestamp}.xlsx"
        
        return send_file(
            output,
            download_name=filename,
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        logger.error(f"Error downloading Excel for template {template_id}: {e}")
        flash(f"Error generating Excel file: {str(e)}", 'error')
        return redirect(url_for('admin'))
 # ================== Delete All Sheets for School Route ==================

@app.route("/admin/delete_school_sheets/<int:template_id>", methods=["POST"])
def delete_school_sheets(template_id):
    """
    Deletes ALL generated sheets (completed PDFs, active PNG, and position data)
    for a specific template. Used to clear history after downloading the merged file.
    """
    if not session.get("admin"):
        return redirect(url_for("login"))

    try:
        template = db.session.get(Template, template_id)
        if not template:
            return redirect(url_for("admin", error="Template not found"))

        deleted_count = 0

        # 1. Delete Completed PDFs (sheet_template_{id}_*.pdf)
        pdf_pattern = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}_*.pdf")
        for file_path in glob.glob(pdf_pattern):
            try:
                os.remove(file_path)
                deleted_count += 1
            except Exception as e:
                logger.error(f"Error deleting {file_path}: {e}")

        # 2. Delete Active PNG Sheet (sheet_template_{id}.png)
        png_path = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}.png")
        if os.path.exists(png_path):
            try:
                os.remove(png_path)
                deleted_count += 1
            except Exception as e:
                logger.error(f"Error deleting {png_path}: {e}")

        # Change txt_path to map_file (JSON)
        map_file = os.path.join(GENERATED_FOLDER, f"sheet_map_template_{template_id}.json")
        if os.path.exists(map_file):
            try:
                os.remove(map_file)
            except Exception as e:
                logger.error(f"Error deleting map file: {e}")

        logger.info(f"Deleted {deleted_count} sheets for template {template_id}")
        
        if deleted_count == 0:
            return redirect(url_for("admin", error="No sheets found to delete."))
            
        return redirect(url_for("admin", success=f"Successfully deleted {deleted_count} sheets for {template.school_name}."))

    except Exception as e:
        logger.error(f"Error clearing sheets: {e}")
        return redirect(url_for("admin", error=f"Error clearing sheets: {str(e)}"))

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

@app.route("/admin/run_cleanup", methods=["POST"])
def run_cleanup():
    if not session.get("admin"):
        return redirect(url_for("login"))
        
    try:
        # Default to 30 days, or get from request if needed
        days = 30
        count = cleanup_old_files(days)
        
        return redirect(url_for("admin", success=f"Cleanup Complete: Removed {count} files older than {days} days."))
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return redirect(url_for("admin", error=f"Cleanup failed: {str(e)}"))

# Add this somewhere in your app.py, perhaps after the 'delete_student' route
@app.route("/delete_all_students_by_template/<int:template_id>", methods=["POST"])
def delete_all_students_by_template(template_id):
    if not session.get("admin"):
        return redirect(url_for("login"))

    try:
        # 1. Get Template details
        template = db.session.get(Template, template_id)
        if not template:
            return redirect(url_for("admin", error="Template not found"))

        # 2. Find all students for this template
        students = Student.query.filter_by(template_id=template_id).all()
        count = len(students)

        if count == 0:
            return redirect(url_for("admin", error="No students found for this school."))

        # 3. Loop through and delete files
        for student in students:
            # Delete uploaded photo
            if student.photo_filename:
                p_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
                if os.path.exists(p_path):
                    try: os.remove(p_path)
                    except: pass
            
            # Delete generated ID card images (JPG & PDF)
            if student.generated_filename:
                base_name = os.path.splitext(student.generated_filename)[0]
                for ext in ['.jpg', '.pdf']:
                    g_path = os.path.join(GENERATED_FOLDER, base_name + ext)
                    if os.path.exists(g_path):
                        try: os.remove(g_path)
                        except: pass
            
            # Delete the record from DB
            db.session.delete(student)

        # 4. Clean up any compiled sheet files for this template
        # (Since we deleted the students, the old sheets are invalid)
        try:
            png_sheet = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}.png")
            if os.path.exists(png_sheet): os.remove(png_sheet)
            
            # Remove positions file
            pos_file = os.path.join(GENERATED_FOLDER, f"positions_template_{template_id}.txt")
            if os.path.exists(pos_file): os.remove(pos_file)

            # Optional: Remove old merged PDFs if you want a complete clean slate
            # pdf_pattern = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}_*.pdf")
            # for f in glob.glob(pdf_pattern): os.remove(f)
        except: pass

        db.session.commit()
        
        logger.info(f"Deleted {count} students for template {template_id}")
        return redirect(url_for("admin", success=f"Successfully deleted all {count} cards for {template.school_name}."))

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting school cards: {e}")
        return redirect(url_for("admin", error=f"Error deleting cards: {str(e)}"))

@app.route("/health")
def health_check():
    try:
        # Check DB connection
        db.session.execute(text("SELECT 1"))
        redis_status = "disabled"
        redis_mode = None
        if _redis_candidate_urls():
            redis_status = "connected" if get_redis_client() is not None else "unavailable"
            if _active_redis_url == REDIS_URL:
                redis_mode = "private"
            elif _active_redis_url == REDIS_PUBLIC_URL:
                redis_mode = "public_fallback"
        return jsonify({"status": "healthy", "db": "connected", "redis": redis_status, "redis_mode": redis_mode}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

@app.route('/favicon.ico')
def favicon():
    """Stop the 404 errors for the browser icon"""
    return "", 204

# Register blueprint

app.register_blueprint(corel_bp, url_prefix='/corel')
app.register_blueprint(editor_bp)
# In your initialization section, after migrate_database():
with app.app_context():
    init_db()
    migrate_database()
    migrate_template_font_colors()
    verify_fonts_available() # Add this line
    migrate_photo_settings()
    repair_student_photo_url_recursion()

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
