"""
Helper utilities: paths, storage backend, template resolution, data hashing.

This module owns:
- Path constants (STATIC_DIR, UPLOAD_FOLDER, etc.)
- Static placeholder image generation
- Storage backend selection (local vs Cloudinary)
- Template path resolution (front/back sides)
- Template settings loader
- Deterministic data hashing for student records

NOTE: Default config getters (fonts, photo, QR) are imported lazily inside
functions to avoid circular imports with the corresponding modules.
"""

import hashlib
import logging
import os
from urllib.parse import urlparse

from PIL import Image, ImageDraw

from models import db, Template

logger = logging.getLogger(__name__)


# ================== Paths ==================
APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC_DIR = os.path.join(APP_ROOT, "static")
UPLOAD_FOLDER = os.path.join(STATIC_DIR, "Uploads")
GENERATED_FOLDER = os.path.join(STATIC_DIR, "generated")
FONTS_FOLDER = os.path.join(STATIC_DIR, "fonts")
LOGOS_FOLDER = os.path.join(STATIC_DIR, "logos")
TEMPLATES_CONFIG = os.path.join(APP_ROOT, "templates_config.json")
FONT_CONFIG_PATH = os.path.join(APP_ROOT, "font_config.json")
PHOTO_CONFIG_PATH = os.path.join(APP_ROOT, "photo_config.json")
DUPLICATE_CONFIG_PATH = os.path.join(APP_ROOT, "duplicate_config.json")
PLACEHOLDER_PATH = os.path.join(STATIC_DIR, "placeholder.jpg")
PHOTO_PLACEHOLDER_PATH = os.path.join(STATIC_DIR, "photo_placeholder.png")
QR_PLACEHOLDER_PATH = os.path.join(LOGOS_FOLDER, "qr_placeholder.png")
DEFAULT_FONTS = ["arial.ttf", "arialbd.ttf"]

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(GENERATED_FOLDER, exist_ok=True)
os.makedirs(FONTS_FOLDER, exist_ok=True)
os.makedirs(LOGOS_FOLDER, exist_ok=True)


def _ensure_static_placeholders():
    """Create placeholder assets referenced by templates if they are missing."""
    if not os.path.exists(PLACEHOLDER_PATH):
        placeholder = Image.new("RGB", (320, 380), color="#e5e7eb")
        draw = ImageDraw.Draw(placeholder)
        draw.rectangle((12, 12, 308, 368), outline="#9ca3af", width=4)
        draw.ellipse((110, 68, 210, 168), fill="#cbd5e1")
        draw.rounded_rectangle((70, 208, 250, 318), radius=36, fill="#cbd5e1")
        placeholder.save(PLACEHOLDER_PATH, quality=90)

    if not os.path.exists(PHOTO_PLACEHOLDER_PATH):
        photo = Image.new("RGBA", (320, 380), color="#f3f4f6")
        draw = ImageDraw.Draw(photo)
        draw.rectangle((12, 12, 308, 368), outline="#9ca3af", width=4)
        draw.ellipse((110, 70, 210, 170), fill="#d1d5db")
        draw.rounded_rectangle((70, 212, 250, 320), radius=36, fill="#d1d5db")
        photo.save(PHOTO_PLACEHOLDER_PATH)

    if not os.path.exists(QR_PLACEHOLDER_PATH):
        qr = Image.new("RGBA", (240, 240), color="#ffffff")
        draw = ImageDraw.Draw(qr)
        draw.rectangle((8, 8, 232, 232), outline="#9ca3af", width=4)
        for x, y in ((34, 34), (154, 34), (34, 154)):
            draw.rectangle((x, y, x + 52, y + 52), outline="#111827", width=8)
            draw.rectangle((x + 18, y + 18, x + 34, y + 34), fill="#111827")
        draw.rectangle((150, 150, 174, 174), fill="#111827")
        draw.rectangle((190, 150, 206, 206), fill="#111827")
        draw.rectangle((150, 190, 182, 206), fill="#111827")
        qr.save(QR_PLACEHOLDER_PATH)


_ensure_static_placeholders()


def _normalize_template_source_url(url):
    value = str(url or "").strip()
    if not value:
        return value
    try:
        parsed = urlparse(value)
        if parsed.scheme == "http" and parsed.netloc.lower() == "res.cloudinary.com":
            return "https://" + value[len("http://"):]
    except Exception:
        return value
    return value


def _resolve_template_side(template, side="front"):
    side_name = str(side or "front").strip().lower()
    if side_name == "back":
        return {
            "filename": getattr(template, "back_filename", None),
            "template_url": getattr(template, "back_template_url", None),
            "font_settings": getattr(template, "back_font_settings", None),
            "photo_settings": getattr(template, "back_photo_settings", None),
            "qr_settings": getattr(template, "back_qr_settings", None),
            "language": getattr(template, "back_language", None),
            "text_direction": getattr(template, "back_text_direction", None),
            "layout_config": getattr(template, "back_layout_config", None),
        }
    return {
        "filename": getattr(template, "filename", None),
        "template_url": getattr(template, "template_url", None),
        "font_settings": getattr(template, "font_settings", None),
        "photo_settings": getattr(template, "photo_settings", None),
        "qr_settings": getattr(template, "qr_settings", None),
        "language": getattr(template, "language", None),
        "text_direction": getattr(template, "text_direction", None),
        "layout_config": getattr(template, "layout_config", None),
    }


def get_template_path(template_id, side="front"):
    try:
        template = db.session.get(Template, template_id)
        if template:
            side_data = _resolve_template_side(template, side=side)
            prefer_remote = get_storage_backend() == "cloudinary"
            if prefer_remote and side_data["template_url"]:
                return _normalize_template_source_url(side_data["template_url"])
            if side_data["filename"]:
                local_path = os.path.join(STATIC_DIR, side_data["filename"])
                if os.path.exists(local_path):
                    return local_path
            if side_data["template_url"]:
                return _normalize_template_source_url(side_data["template_url"])
            if side_data["filename"]:
                return os.path.join(STATIC_DIR, side_data["filename"])
        logger.warning(f"No template found or no URL/filename for template ID {template_id}")
        return None
    except Exception as e:
        logger.error(f"Error fetching template path for ID {template_id}: {e}")
        return None


def _parse_rgb_color(color_val):
    if not color_val:
        return [0, 0, 0]
    if isinstance(color_val, str):
        val = color_val.strip()
        if val.startswith("#"):
            hex_color = val.lstrip("#")
            try:
                if len(hex_color) == 6:
                    return [
                        int(hex_color[0:2], 16),
                        int(hex_color[2:4], 16),
                        int(hex_color[4:6], 16),
                    ]
                elif len(hex_color) == 3:
                    return [
                        int(hex_color[0] * 2, 16),
                        int(hex_color[1] * 2, 16),
                        int(hex_color[2] * 2, 16),
                    ]
            except Exception:
                return [0, 0, 0]
        else:
            try:
                return [int(x.strip()) for x in val.split(",")]
            except Exception:
                return [0, 0, 0]
    if isinstance(color_val, (list, tuple)):
        try:
            return [int(x) for x in color_val[:3]]
        except Exception:
            return [0, 0, 0]
    return [0, 0, 0]


def get_template_settings(template_id, side="front"):
    from app.utils.fonts import get_default_font_config
    from app.utils.image_utils import get_default_photo_config, get_default_qr_config

    try:
        template = db.session.get(Template, template_id)

        if template:
            side_data = _resolve_template_side(template, side=side)
            font_settings = get_default_font_config()
            photo_settings = get_default_photo_config()
            qr_settings = get_default_qr_config()
            orientation = template.card_orientation or "landscape"

            if side_data["font_settings"]:
                loaded_font = side_data["font_settings"].copy()
                if "font_color" in loaded_font:
                    if "label_font_color" not in loaded_font:
                        loaded_font["label_font_color"] = loaded_font["font_color"]
                    if "value_font_color" not in loaded_font:
                        loaded_font["value_font_color"] = loaded_font["font_color"]
                font_settings.update(loaded_font)
                color_keys = [
                    "font_color", "label_font_color", "value_font_color", "colon_font_color",
                    "label_font_color_bottom", "value_font_color_bottom", "colon_font_color_bottom",
                ]
                for key in color_keys:
                    if key in font_settings:
                        font_settings[key] = _parse_rgb_color(font_settings[key])

            if side_data["photo_settings"]:
                photo_settings.update(side_data["photo_settings"])

            if side_data["qr_settings"]:
                qr_settings.update(side_data["qr_settings"])
                if "qr_color" in qr_settings and "qr_fill_color" not in qr_settings:
                    qr_settings["qr_fill_color"] = _parse_rgb_color(qr_settings["qr_color"])
                if "qr_bg_color" in qr_settings and "qr_back_color" not in qr_settings:
                    qr_settings["qr_back_color"] = _parse_rgb_color(qr_settings["qr_bg_color"])

                if isinstance(qr_settings.get("qr_fill_color"), str):
                    fill_color = qr_settings["qr_fill_color"]
                    if fill_color.startswith("#"):
                        hex_color = fill_color.lstrip("#")
                        qr_settings["qr_fill_color"] = [
                            int(hex_color[0:2], 16),
                            int(hex_color[2:4], 16),
                            int(hex_color[4:6], 16),
                        ]
                    else:
                        qr_settings["qr_fill_color"] = [
                            int(x.strip()) for x in fill_color.split(",")
                        ]

                if isinstance(qr_settings.get("qr_back_color"), str):
                    back_color = qr_settings["qr_back_color"]
                    if back_color.startswith("#"):
                        hex_color = back_color.lstrip("#")
                        qr_settings["qr_back_color"] = [
                            int(hex_color[0:2], 16),
                            int(hex_color[2:4], 16),
                            int(hex_color[4:6], 16),
                        ]
                    else:
                        qr_settings["qr_back_color"] = [
                            int(x.strip()) for x in back_color.split(",")
                        ]

            logger.info(
                f"Template {template_id} settings loaded - Label Color: {font_settings.get('label_font_color')}, Value Color: {font_settings.get('value_font_color')}"
            )
            return font_settings, photo_settings, qr_settings, orientation

        return (
            get_default_font_config(),
            get_default_photo_config(),
            get_default_qr_config(),
            "landscape",
        )
    except Exception as e:
        logger.error(f"Error fetching template settings for ID {template_id}: {e}")
        return (
            get_default_font_config(),
            get_default_photo_config(),
            get_default_qr_config(),
            "landscape",
        )


def generate_data_hash(form_data, photo_identifier=None):
    data_string = (
        f"{form_data.get('name', '')}"
        f"{form_data.get('father_name', '')}"
        f"{form_data.get('class_name', '')}"
        f"{form_data.get('dob', '')}"
        f"{form_data.get('address', '')}"
        f"{form_data.get('phone', '')}"
    )
    if photo_identifier:
        data_string += str(photo_identifier)
    return hashlib.md5(data_string.encode()).hexdigest()


def get_storage_backend():
    mode = (os.getenv("STORAGE_BACKEND") or "auto").strip().lower()
    if mode in {"local", "filesystem"}:
        return "local"

    want_cloud = mode in {"cloudinary", "cloud"} or mode == "auto"
    if not want_cloud:
        return "local"

    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    api_key = os.getenv("CLOUDINARY_API_KEY")
    api_secret = os.getenv("CLOUDINARY_API_SECRET")
    configured = bool(cloud_name and api_key and api_secret)

    if mode in {"cloudinary", "cloud"} and not configured:
        logger.error("Cloudinary credentials are missing but STORAGE_BACKEND=cloudinary was requested.")
        return "cloudinary"

    return "cloudinary" if configured else "local"


__all__ = [
    "APP_ROOT",
    "DEFAULT_FONTS",
    "DUPLICATE_CONFIG_PATH",
    "FONTS_FOLDER",
    "FONT_CONFIG_PATH",
    "GENERATED_FOLDER",
    "LOGOS_FOLDER",
    "PHOTO_CONFIG_PATH",
    "PHOTO_PLACEHOLDER_PATH",
    "PLACEHOLDER_PATH",
    "QR_PLACEHOLDER_PATH",
    "STATIC_DIR",
    "TEMPLATES_CONFIG",
    "UPLOAD_FOLDER",
    "_ensure_static_placeholders",
    "_normalize_template_source_url",
    "_parse_rgb_color",
    "_resolve_template_side",
    "generate_data_hash",
    "get_storage_backend",
    "get_template_path",
    "get_template_settings",
]
