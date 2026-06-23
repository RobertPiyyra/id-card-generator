"""
Utility and helper functions extracted from legacy_app.py.

Provides small reusable helpers for form data, caching, color parsing,
font fitting, email, file I/O, and template management.
"""

import base64
import io
import logging
import os
import re
import ssl
import smtplib
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlparse

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from flask import (
    Flask,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask import Blueprint
from werkzeug.utils import secure_filename

from app.extensions import limiter
from models import (
    ActivityLog,
    AdminUser,
    BulkJob,
    BulkJobItem,
    Student,
    Template,
    TemplateField,
    TemplateVersion,
    TemplateWorkflow,
    db,
)
from utils import (
    DUPLICATE_CONFIG_PATH,
    FONTS_FOLDER,
    GENERATED_FOLDER,
    STATIC_DIR,
    TEMPLATES_CONFIG,
    UPLOAD_FOLDER,
    get_default_font_config,
    get_default_photo_config,
    get_default_qr_config,
    get_template_path,
    load_font_dynamic,
    load_template,
    load_template_smart,
    parse_layout_config,
)

logger = logging.getLogger(__name__)


# ================== Form Helpers ==================

def get_form_int(form_data, key, default=0):
    """Safely get integer value from form data."""
    try:
        value = form_data.get(key)
        if value is None or value == "":
            return default
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_get_int(source, key, default=0):
    """Safely get integer value from dict/JSON source."""
    if source is None:
        return default
    value = source.get(key)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_get_bool(source, key, default=False):
    """Safely get boolean value from dict/JSON source."""
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


def safe_get_nested(source, *keys, default=None):
    """Safely get nested value from dict/JSON source."""
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


# ================== Color Helpers ==================

def validate_color(color, default=None):
    """Validate and normalize a color value to [r, g, b] list."""
    if default is None:
        default = [0, 0, 0]
    if not color:
        return default
    if isinstance(color, list) and len(color) >= 3:
        try:
            return [
                max(0, min(255, int(color[0] if color[0] is not None else 0))),
                max(0, min(255, int(color[1] if color[1] is not None else 0))),
                max(0, min(255, int(color[2] if color[2] is not None else 0))),
            ]
        except (ValueError, TypeError):
            return default
    elif isinstance(color, str):
        color = color.strip()
        if color.startswith("#"):
            try:
                h = color.lstrip("#")
                return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
            except Exception:
                return default
        elif "," in color:
            try:
                parts = color.split(",")
                if len(parts) >= 3:
                    return [int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip())]
            except Exception:
                return default
    return default


def parse_color(c):
    """Parse a color from string or list format."""
    if isinstance(c, list):
        if len(c) >= 3:
            try:
                return [int(x) for x in c[:3]]
            except Exception:
                pass
        return [0, 0, 0]
    if not c:
        return [0, 0, 0]
    if isinstance(c, str):
        c = c.strip()
        if c.startswith("#") and len(c) == 7:
            try:
                h = c.lstrip("#")
                return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
            except Exception:
                return [0, 0, 0]
        try:
            parts = c.split(",")
            if len(parts) >= 3:
                return [int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip())]
        except Exception:
            pass
    return [0, 0, 0]


def rgb_to_hex(rgb_list):
    """Convert RGB list [r, g, b] to hex #RRGGBB format."""
    if isinstance(rgb_list, list) and len(rgb_list) == 3:
        try:
            clamped = [max(0, min(255, int(c))) for c in rgb_list]
            return "#{:02x}{:02x}{:02x}".format(*clamped)
        except (ValueError, TypeError):
            pass
    return "#000000"


def _hex_to_rgb_for_editor(value, fallback=(0, 0, 0)):
    """Convert a color value to an (r, g, b) tuple for the visual editor."""
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


# ================== Font Fitting ==================

def fit_loaded_font_to_single_line(draw, font_loader, display_text, max_width, start_size, language="english", min_size=6):
    """Shrink a font until the text fits on one line."""
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
            text_len = draw.textlength(display_text, font=font)
            if text_len <= safe_width:
                return font, int(getattr(font, "size", size) or size)
        except Exception:
            return font, int(getattr(font, "size", size) or size)
        size -= 1

    if last_font is None:
        last_font = font_loader(min_size)
    return last_font, int(getattr(last_font, "size", min_size) or min_size)


def fit_dynamic_font_to_single_line(draw, font_path, display_text, max_width, start_size, language="english", min_size=6):
    """Shrink a dynamic font until the text fits on one line."""
    return fit_loaded_font_to_single_line(
        draw,
        lambda size: load_font_dynamic(font_path, display_text or "X", 10**9, size, language=language),
        display_text,
        max_width,
        start_size,
        language=language,
        min_size=min_size,
    )


# ================== Caching Helpers ==================

def with_cache_bust(url):
    """Append a cache-busting query param for preview images."""
    if not url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}v={int(time.time() * 1000)}"


def _get_cached_media_image(key_prefix, buffer_bytes, generate_fn):
    """Cache a generated image (QR, barcode) in Redis with stampede protection."""
    from app.services.redis_service import (
        _redis_cache_key,
        _redis_delete,
        _redis_get,
        _redis_set,
        _redis_acquire_lock,
    )
    cache_key = _redis_cache_key(key_prefix, buffer_bytes)
    cached = _redis_get(cache_key)
    if cached is not None:
        try:
            img = Image.open(io.BytesIO(cached))
            img.load()
            return img.convert("RGBA")
        except Exception as e:
            logger.warning("Media cache decode failed for %s: %s", cache_key, e)
            _redis_delete(cache_key)

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
    """Cache a QR code image."""
    from app.services.qr_service import generate_qr_code
    logo_key = f"{qr_settings.get('qr_include_logo', False)}:{qr_settings.get('qr_logo_path', '')}"
    return _get_cached_media_image(
        "qr",
        f"{payload}:{size}:{qr_settings.get('qr_data_type','default')}:{logo_key}".encode("utf-8", "ignore"),
        lambda: generate_qr_code(payload, qr_settings, size),
    )


def _get_cached_barcode_image(payload, qr_settings, width, height):
    """Cache a barcode image."""
    from app.services.barcode_service import generate_barcode_code128
    return _get_cached_media_image(
        "barcode",
        f"{payload}:{width}:{height}:{qr_settings.get('barcode_data_type','default')}".encode("utf-8", "ignore"),
        lambda: generate_barcode_code128(payload, qr_settings, width=width, height=height),
    )


# ================== Student Data Helpers ==================

def _build_student_form_data(student_like):
    """Build a dict of standard form fields from a student-like object."""
    return {
        "name": getattr(student_like, "name", "") or "",
        "father_name": getattr(student_like, "father_name", "") or "",
        "class_name": getattr(student_like, "class_name", "") or "",
        "dob": getattr(student_like, "dob", "") or "",
        "address": getattr(student_like, "address", "") or "",
        "phone": getattr(student_like, "phone", "") or "",
    }


def _build_student_image_ref(student_like):
    """Return the best available photo reference for a student."""
    return getattr(student_like, "photo_url", None) or getattr(student_like, "photo_filename", None) or ""


def _build_qr_hash(student_like):
    """Build a short QR hash from student data."""
    from utils import generate_data_hash
    return generate_data_hash(_build_student_form_data(student_like), _build_student_image_ref(student_like))[:10]


def _build_payload(settings, student_like, student_id, school_name, prefix):
    """Build the QR/barcode payload string for a student."""
    data_type = settings.get(f"{prefix}_data_type", "student_id")
    str_student_id = str(student_id) if student_id is not None else None
    if data_type == "url":
        base = settings.get(f"{prefix}_base_url", "") or ""
        if base and not base.endswith("/"):
            base += "/"
        return base + (str_student_id or _build_qr_hash(student_like))
    if data_type == "text":
        return settings.get(f"{prefix}_custom_text", "Sample Text")
    if data_type == "json":
        import json
        return json.dumps({
            "student_id": str_student_id or _build_qr_hash(student_like),
            "name": getattr(student_like, "name", "") or "",
            "class": getattr(student_like, "class_name", "") or "",
            "school_name": school_name or getattr(student_like, "school_name", "") or "",
        })
    return str_student_id or _build_qr_hash(student_like)


# ================== Field Layout Helpers ==================

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
    from utils import get_field_layout_item

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
    """Return the initial Y cursor for the given side's layout flow."""
    from utils import get_layout_flow_start_y

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


# ================== Language / Translation ==================

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
    """Return 'rtl' for Urdu/Arabic, 'ltr' otherwise."""
    return "rtl" if str(language or "").strip().lower() in {"urdu", "arabic"} else "ltr"


def validate_double_sided_language_pair(front_language, back_language):
    """Return True if both languages are supported."""
    front = str(front_language or "english").strip().lower()
    back = str(back_language or "english").strip().lower()
    return front in SUPPORTED_TEMPLATE_LANGUAGES and back in SUPPORTED_TEMPLATE_LANGUAGES


def _should_skip_translation(raw_value, field_key=None, field_type=None):
    """Return True if the value should not be translated."""
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
    """Extract translated text from Google Translate API response."""
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
    """Detect the language of the given text."""
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
    """Translate text using Google Translate API (cached)."""
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
    """Translate a value for the template's target language side."""
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


def get_template_language_direction_from_obj(template_obj, side="front"):
    """Return (language, text_direction) for a template side."""
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


# ================== Layout Field Helpers ==================

def field_consumes_layout_space(layout_item, raw_value=""):
    """Return True when a field should reserve vertical flow space."""
    if not isinstance(layout_item, dict):
        return bool(str(raw_value or "").strip())
    if layout_item.get("label_visible"):
        return True
    return bool(layout_item.get("value_visible")) and bool(str(raw_value or "").strip())


def field_advances_layout_flow(layout_item, raw_value="", *, separate_colon=False):
    """Return True only for fields that should advance the flowing Y cursor."""
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
    """Decide whether a field should still render vertically."""
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


# ================== Custom Object Rendering ==================

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

    from utils import get_draw_text_kwargs

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
                from utils import download_font_if_missing
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

            font = load_font_dynamic(font_path, text, template_img.width, font_size, language=language)
            char_space_px = font_size * (char_spacing / 1000.0) * scale

            bbox = draw.textbbox((0, 0), text, font=font)
            spaced_w = 0.0
            for char in text:
                spaced_w += draw.textlength(char, font=font)
            spaced_w += char_space_px * (len(text) - 1)

            text_w = max(1, int(spaced_w) + 6)
            text_h = max(1, (bbox[3] - bbox[1]) + 6)
            overlay = Image.new("RGBA", (text_w, text_h), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)

            cursor_x = 3 - bbox[0]
            is_rtl = (language in ("urdu", "arabic") or any(ord(c) >= 0x0600 and ord(c) <= 0x06FF for c in text))
            if is_rtl:
                overlay_draw.text((3 - bbox[0], 3 - bbox[1]), text, font=font, fill=fill_rgba)
            else:
                for char in text:
                    overlay_draw.text((cursor_x, 3 - bbox[1]), char, font=font, fill=fill_rgba)
                    cursor_x += draw.textlength(char, font=font) + char_space_px

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


# ================== PDF / Image Helpers ==================

def _looks_like_pdf_template_source(path_or_url):
    """Return True if the path/URL looks like a PDF template source."""
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
    """Convert an image to RGB mode, handling transparency."""
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


# ================== Email ==================

def send_email(to, subject, body):
    """Send an email via Gmail SMTP (SSL)."""
    from email.mime.text import MIMEText
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = os.environ.get("EMAIL_FROM")
    msg["To"] = to

    server = None
    try:
        smtp_server = "smtp.gmail.com"
        smtp_port = 465
        password = os.environ.get("EMAIL_PASSWORD")

        logger.info("Sending email to %s via %s:%s...", to, smtp_server, smtp_port)

        addr_info = socket.getaddrinfo(smtp_server, smtp_port, socket.AF_INET, socket.SOCK_STREAM)
        family, socktype, proto, canonname, sa = addr_info[0]
        target_ip = sa[0]

        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(target_ip, smtp_port, context=context, timeout=30)

        server.login(msg["From"], password)
        server.send_message(msg)
        server.quit()

        logger.info("Email sent successfully to %s", to)
        return True

    except Exception as e:
        logger.error("Failed to send email: %s", e)
        return False
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass


# ================== File I/O ==================

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


# ================== Face Detection ==================

# Lazy mediapipe import — face_service.py handles lazy loading now
mp_face = None

_detector_lock = threading.Lock()


def _get_face_detector():
    """Return a MediaPipe FaceDetection instance or None."""
    if mp_face is None:
        return None
    try:
        return mp_face.FaceDetection(model_selection=0, min_detection_confidence=0.5)
    except Exception as e:
        logger.warning("Error initializing MediaPipe face detector: %s", e)
        return None


def _fallback_center_crop(pil_img, save_path, target_w, target_h):
    """Fallback that respects EXIF rotation and aspect ratio."""
    from app.services.photo_service import _process_photo_pil
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
    """Crop with padding for out-of-bounds regions."""
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
    """Compute a center crop box for the target aspect ratio."""
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
    """Detect face and return a crop box centered on the face."""
    try:
        rgb_img = pil_img.convert("RGB")
        img_np = np.array(rgb_img)
        h_orig, w_orig = img_np.shape[:2]
        if h_orig <= 0 or w_orig <= 0:
            return None

        with _detector_lock:
            detector = _get_face_detector()
            if detector is None:
                return None
            try:
                results = detector.process(img_np.copy())
            finally:
                try:
                    detector.close()
                except Exception:
                    pass

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


# ================== Template Upload Helpers ==================

def store_template_upload_asset(file_storage, *, side_label):
    """Save an uploaded template file (front or back) to disk and Cloudinary."""
    if file_storage is None or not file_storage.filename:
        raise ValueError(f"{side_label} template file is required")
    filename = secure_filename(file_storage.filename)
    file_bytes = io.BytesIO()
    file_storage.save(file_bytes)
    file_bytes.seek(0)
    return store_template_upload_bytes(file_bytes.getvalue(), filename, side_label=side_label)


def _extract_pdf_upload_payload(raw_bytes, side_label):
    """Extract PDF bytes from raw upload bytes, starting at %PDF header."""
    import fitz
    pdf_header_pos = raw_bytes.find(b"%PDF")
    if pdf_header_pos < 0:
        raise ValueError("Uploaded file does not contain a PDF header.")
    upload_payload = raw_bytes[pdf_header_pos:]
    if len(upload_payload) < 128:
        raise ValueError("Uploaded PDF is too small and appears truncated.")
    pdf_doc = fitz.open(stream=upload_payload, filetype="pdf")
    try:
        page_count = pdf_doc.page_count
        if page_count < 1:
            raise ValueError("Uploaded PDF has no pages.")
        _ = pdf_doc[0].get_pixmap(dpi=72)
    finally:
        pdf_doc.close()
    return upload_payload, page_count


def _single_pdf_page_bytes(pdf_bytes, page_index):
    """Extract a single page from a PDF as bytes."""
    import fitz
    src_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    dst_doc = fitz.open()
    try:
        if page_index < 0 or page_index >= src_doc.page_count:
            raise ValueError(f"PDF page {page_index + 1} is missing.")
        dst_doc.insert_pdf(src_doc, from_page=page_index, to_page=page_index)
        return dst_doc.tobytes(
            garbage=4, clean=False, deflate=False,
            deflate_images=False, deflate_fonts=False,
            expand=255, linear=False, no_new_id=True,
            pretty=False, use_objstms=0,
        )
    finally:
        dst_doc.close()
        src_doc.close()


def _template_side_filename(filename, side_label):
    """Generate a side-specific filename for template uploads."""
    safe_name = secure_filename(filename or "template.pdf")
    stem, ext = os.path.splitext(safe_name)
    ext = ext or ".pdf"
    return f"{stem}_{side_label.lower()}{ext}"


def store_template_upload_bytes(raw_bytes, filename, *, side_label):
    """Store uploaded template bytes locally and on Cloudinary if configured."""
    from cloudinary_config import upload_image
    from utils import get_storage_backend

    filename = secure_filename(filename or f"{side_label.lower()}_template")
    raw_bytes = raw_bytes if isinstance(raw_bytes, bytes) else bytes(raw_bytes or b"")
    if not raw_bytes:
        raise ValueError(f"{side_label} template file is empty")
    if not filename.lower().endswith((".pdf", ".jpg", ".jpeg", ".png")):
        raise ValueError(f"Invalid {side_label.lower()} template format. Use PDF, JPG, or PNG")

    file_ext = os.path.splitext(filename)[1].lower()
    is_pdf_upload = file_ext == ".pdf"
    upload_payload = raw_bytes
    page_count = None

    if is_pdf_upload:
        try:
            upload_payload, page_count = _extract_pdf_upload_payload(raw_bytes, side_label)
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
    if get_storage_backend() != "local":
        remote_url = upload_image(
            upload_payload,
            folder="id_card_templates",
            resource_type="raw" if is_pdf_upload else "image",
            format="pdf" if is_pdf_upload else file_ext.lstrip(".") or None,
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
        "is_pdf": is_pdf_upload,
        "page_count": page_count,
    }


# ================== Duplicate Config ==================

def load_duplicate_config():
    """Load duplicate checking config from disk."""
    default_config = {"check_phone": False, "check_name_class": True}
    if os.path.exists(DUPLICATE_CONFIG_PATH):
        try:
            with open(DUPLICATE_CONFIG_PATH, "r") as f:
                return {**default_config, **json.load(f)}
        except json.JSONDecodeError as e:
            logger.error("Error loading duplicate config: %s", e)
    return default_config


def save_duplicate_config(config):
    """Save duplicate checking config to disk."""
    try:
        with open(DUPLICATE_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
        logger.info("Duplicate config saved successfully")
    except Exception as e:
        logger.error("Error saving duplicate config: %s", e)


def check_duplicate_student(form_data, photo_filename=None, student_id=None):
    """Check if a student with the same phone or name+class already exists."""
    duplicate_config = load_duplicate_config()
    current_template_id = form_data.get("template_id")

    if not current_template_id:
        return False, None

    try:
        if duplicate_config.get("check_phone", False):
            query = Student.query.filter(
                Student.phone == form_data["phone"],
                Student.template_id == current_template_id,
            )
            if student_id:
                query = query.filter(Student.id != student_id)
            if query.first():
                return True, "A student with this phone number already exists in this school."

        if duplicate_config.get("check_name_class", True):
            query = Student.query.filter(
                Student.name == form_data["name"],
                Student.class_name == form_data["class_name"],
                Student.template_id == current_template_id,
            )
            if student_id:
                query = query.filter(Student.id != student_id)
            if query.first():
                return True, "A student with this name and class combination already exists in this school."

        return False, None
    except Exception as e:
        logger.error("Error checking duplicates: %s", e)
        return True, f"Database error: {str(e)}"


# ================== Layout Config Helpers ==================

def clear_layout_field_overrides(template, side="front"):
    """Let Template Settings control label/value text after Visual Editor was used."""
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


# ================== Template Management ==================

def update_template_settings(template_id, font_settings=None, photo_settings=None, qr_settings=None,
                             card_orientation=None, card_dims=None, sheet_dims=None, grid_layout=None,
                             back_font_settings=None, back_photo_settings=None, back_qr_settings=None,
                             is_double_sided=None, duplex_flip_mode=None):
    """Update template settings in the database."""
    from app.services.template_lifecycle_service import (
        create_template_version_snapshot,
        get_session_actor,
        log_immutable_audit_event,
    )
    try:
        template = db.session.get(Template, template_id)

        if not template:
            logger.error("Template %s not found", template_id)
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

        if card_dims:
            template.card_width = card_dims.get("width", 1015)
            template.card_height = card_dims.get("height", 661)

        if sheet_dims:
            template.sheet_width = sheet_dims.get("width", 2480)
            template.sheet_height = sheet_dims.get("height", 3508)

        if grid_layout:
            template.grid_rows = grid_layout.get("rows", 5)
            template.grid_cols = grid_layout.get("cols", 2)
        if is_double_sided is not None:
            template.is_double_sided = bool(is_double_sided)
        if duplex_flip_mode:
            template.duplex_flip_mode = duplex_flip_mode

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

        logger.info("Updated settings for template ID %s, orientation: %s", template_id, card_orientation)
    except Exception as e:
        db.session.rollback()
        logger.error("Error updating template settings: %s", e)
        raise


def add_template(
    filename,
    school_name,
    card_orientation="landscape",
    language="english",
    text_direction="ltr",
    *,
    is_double_sided=False,
    back_filename=None,
    back_template_url=None,
    back_language=None,
    back_text_direction=None,
):
    """Add a new template to the database."""
    try:
        if card_orientation == "portrait":
            width, height = 661, 1015
            rows, cols = 2, 5
        else:
            width, height = 1015, 661
            rows, cols = 5, 2

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
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(template)
        db.session.commit()

        logger.info("Added template: %s (%sx%s)", filename, width, height)
        return template.id
    except Exception as e:
        db.session.rollback()
        logger.error("Error adding template: %s", e)
        raise


def add_template_cloudinary(
    template_url,
    school_name,
    card_orientation="landscape",
    language="english",
    text_direction="ltr",
    filename=None,
    *,
    is_double_sided=False,
    back_filename=None,
    back_template_url=None,
    back_language=None,
    back_text_direction=None,
):
    """Add a template with Cloudinary URL (no local file storage)."""
    try:
        if card_orientation == "portrait":
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
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(template)
        db.session.commit()

        safe_url = str(template_url or "Local Fallback")
        logger.info("Added Cloudinary template: %s... (%sx%s)", safe_url[:50], width, height)
        return template.id
    except Exception as e:
        db.session.rollback()
        logger.error("Error adding Cloudinary template: %s", e)
        raise


def get_templates():
    """Fetch all templates as dicts, filtered by school for school_admin."""
    try:
        query = db.session.query(Template).order_by(Template.created_at.desc())

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

            if template.font_settings:
                loaded_font = template.font_settings.copy()
                if "font_color" in loaded_font and "label_font_color" not in loaded_font:
                    loaded_font["label_font_color"] = loaded_font["font_color"]
                    loaded_font["value_font_color"] = loaded_font["font_color"]
                font_settings = {**font_settings, **loaded_font}

            if template.photo_settings:
                photo_settings = {**photo_settings, **template.photo_settings}

            if template.qr_settings:
                qr_settings = {**qr_settings, **template.qr_settings}
            back_font_settings = {**get_default_font_config(), **(template.back_font_settings or {})}
            back_photo_settings = {**get_default_photo_config(), **(template.back_photo_settings or {})}
            back_qr_settings = {**get_default_qr_config(), **(template.back_qr_settings or {})}

            template_fields = []
            if template.fields:
                for field in template.fields:
                    template_fields.append({
                        "field_name": field.field_name,
                        "field_label": field.field_label,
                        "field_type": field.field_type,
                        "is_required": field.is_required,
                        "show_label_front": bool(getattr(field, "show_label_front", True)),
                        "show_value_front": bool(getattr(field, "show_value_front", True)),
                        "show_label_back": bool(getattr(field, "show_label_back", False)),
                        "show_value_back": bool(getattr(field, "show_value_back", False)),
                        "display_order": field.display_order,
                        "field_options": field.field_options,
                    })
                template_fields.sort(key=lambda x: int(x.get("display_order") or 0))

            result.append({
                "id": template.id,
                "filename": template.filename,
                "template_url": template.template_url,
                "back_filename": template.back_filename,
                "back_template_url": template.back_template_url,
                "source_path": source_path,
                "source_name": source_basename or "No source",
                "back_source_path": back_source_path,
                "back_source_name": back_source_basename or "No back source",
                "school_name": template.school_name,
                "created_at": template.created_at.isoformat() if template.created_at else datetime.now(timezone.utc).isoformat(),
                "font_settings": font_settings,
                "photo_settings": photo_settings,
                "qr_settings": qr_settings,
                "back_font_settings": back_font_settings,
                "back_photo_settings": back_photo_settings,
                "back_qr_settings": back_qr_settings,
                "card_orientation": template.card_orientation or "landscape",
                "language": template.language or "english",
                "text_direction": template.text_direction or "ltr",
                "back_language": template.back_language or template.language or "english",
                "back_text_direction": template.back_text_direction or template.text_direction or "ltr",
                "back_layout_config": template.back_layout_config,
                "is_double_sided": bool(template.is_double_sided),
                "duplex_flip_mode": template.duplex_flip_mode or "long_edge",
                "deadline": template.deadline.isoformat() if template.deadline else None,
                "fields": template_fields,
                "card_width": template.card_width or 1015,
                "card_height": template.card_height or 661,
                "sheet_width": template.sheet_width or 2480,
                "sheet_height": template.sheet_height or 3508,
                "grid_rows": template.grid_rows or 5,
                "grid_cols": template.grid_cols or 2,
            })

        return result
    except Exception as e:
        logger.error("Error fetching templates: %s", e)
        return []


# ================== Session / RBAC Helpers ==================

def _normalize_school_name(value):
    """Normalize a school name for comparison."""
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _find_template_dict_by_school(templates, school_name):
    """Find a template dict by school name (normalized)."""
    normalized = _normalize_school_name(school_name)
    if not normalized:
        return None
    for template in templates or []:
        if _normalize_school_name(template.get("school_name")) == normalized:
            return template
    return None


def _student_session_school_name():
    """Return the school name from the current student session."""
    return (session.get("student_school_name") or "").strip()


def _is_admin_session():
    """Return True if the current session is an admin session."""
    return bool(session.get("admin"))


def _current_session_email():
    """Return the current session's student email."""
    return (session.get("student_email") or "").strip().lower()


def _student_school_access_allowed(student_school_name):
    """Check if the current session can access a student's school."""
    if _is_admin_session() and session.get("admin_role") != "school_admin":
        return True
    locked_school = _student_session_school_name()
    if not locked_school:
        return True
    return _normalize_school_name(locked_school) == _normalize_school_name(student_school_name)


# ================== Misc Helpers ==================

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
        logger.warning("Failed to load back template for template %s: %s", template_obj.id, e)
        return None


def check_deadline_passed(template_id):
    """Return (True, deadline_str) if deadline has passed, (False, None) otherwise."""
    if not template_id:
        return False, None

    try:
        template = db.session.get(Template, int(template_id))

        if not template or not template.deadline:
            return False, None

        now = datetime.now()
        deadline = template.deadline

        if now > deadline:
            return True, deadline.strftime("%d %B %Y, %I:%M %p")

        return False, None
    except Exception as e:
        logger.error("Error checking deadline: %s", e)
        return False, None


def log_activity(action, target=None, details=None):
    """Log an administrative or user action to the database."""
    try:
        if session.get("admin"):
            actor = "Admin"
        elif session.get("student_email"):
            actor = session["student_email"]
        else:
            actor = "Anonymous"

        log = ActivityLog(
            actor=actor,
            action=action,
            target=str(target) if target else None,
            details=str(details) if details else None,
            ip_address=request.remote_addr,
            timestamp=datetime.now(timezone.utc),
        )

        db.session.add(log)
        db.session.commit()

    except Exception as e:
        logger.error("Failed to log activity: %s", e)
        db.session.rollback()


def cleanup_old_files():
    """Clean up old generated and uploaded files."""
    try:
        now = time.time()
        max_age = 86400  # 24 hours
        for folder in [GENERATED_FOLDER, UPLOAD_FOLDER]:
            if not os.path.exists(folder):
                continue
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                if os.path.isfile(file_path):
                    file_age = now - os.path.getmtime(file_path)
                    if file_age > max_age:
                        try:
                            os.remove(file_path)
                            logger.info("Cleaned up old file: %s", file_path)
                        except Exception as e:
                            logger.warning("Failed to clean up %s: %s", file_path, e)
    except Exception as e:
        logger.error("Error cleaning up old files: %s", e)


# ================== Bulk Generation Helpers ==================

def _format_bulk_generation_error(exc, row_index=None):
    """Format a bulk generation error for display."""
    prefix = f"Row {row_index}: " if row_index is not None else ""
    return f"{prefix}{exc}"


def _sort_key(item):
    """Sort key for bulk generation results."""
    if isinstance(item, dict):
        return item.get("row_index", 0)
    return 0


def _apply_batch_rules_for_row(row_data, batch_rules, template):
    """Apply batch rules to a row of student data."""
    if not batch_rules or not isinstance(batch_rules, dict):
        return row_data
    result = dict(row_data)
    for rule_key, rule_value in batch_rules.items():
        if rule_key.startswith("set_"):
            field_name = rule_key[4:]
            result[field_name] = rule_value
        elif rule_key.startswith("prefix_"):
            field_name = rule_key[7:]
            if field_name in result and result[field_name]:
                result[field_name] = str(rule_value) + str(result[field_name])
    return result


def _apply_import_mapping_to_dataframe(df, import_mapping):
    """Apply an import mapping to a pandas DataFrame."""
    if not import_mapping or df is None or df.empty:
        return df
    try:
        mapping_json = import_mapping.get("mapping_json", {})
        if isinstance(mapping_json, str):
            mapping_json = json.loads(mapping_json)
        if mapping_json:
            df = df.rename(columns=mapping_json)
    except Exception as e:
        logger.warning("Failed to apply import mapping: %s", e)
    return df
# Refactored module imports — these provide cleaner organizational structure.
# The original function definitions above shadow these imports at runtime,
# preserving exact backward-compatible behavior.
# Canonical sources for duplicated functions:
# - layout functions     -> app.services.layout_service
# - template operations  -> app.template_ops
# - DB migrations        -> app.db_migrations
# - translation          -> app.services.translation_service
