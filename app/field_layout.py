"""
Field layout helper functions.

Extracted from app/legacy_app.py — handles template field positioning,
visibility flags, language direction, and custom object rendering.

USAGE: These functions are identical copies of those in legacy_app.py.
The original definitions shadow these imports at runtime.
"""
import logging
import math
import os
from datetime import datetime, timezone

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

from models import TemplateField

logger = logging.getLogger(__name__)

# Constants preserved from legacy_app.py
SUPPORTED_TEMPLATE_LANGUAGES = {"english", "urdu", "hindi", "arabic"}
LANGUAGE_TO_TRANSLATE_CODE = {
    "english": "en",
    "urdu": "ur",
    "hindi": "hi",
    "arabic": "ar",
}
NON_TRANSLATABLE_FIELD_KEYS = {"DOB", "MOBILE"}
NON_TRANSLATABLE_FIELD_TYPES = {"date", "number", "tel", "email"}


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
    from app.helpers import keep_layout_field_visibility_only

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


def apply_layout_custom_objects_pil(template_img, template_obj, font_settings, side="front", language="english", render_scale=1.0):
    """Render free custom editor objects stored in layout_config.objects."""
    if template_img is None or template_obj is None:
        return
    layout_config_raw = getattr(template_obj, "back_layout_config", None) if str(side or "front").lower() == "back" else getattr(template_obj, "layout_config", None)
    if not layout_config_raw:
        return

    from utils import parse_layout_config, get_draw_text_kwargs, load_font_dynamic

    try:
        parsed = parse_layout_config(layout_config_raw)
    except Exception:
        parsed = None
    if not parsed:
        return

    objects = parsed.get("objects", [])
    if not objects:
        return

    draw = ImageDraw.Draw(template_img)
    img_w, img_h = template_img.size

    for obj in objects:
        try:
            obj_type = obj.get("type", "")
            x = int(obj.get("x", 0) * render_scale)
            y = int(obj.get("y", 0) * render_scale)
            w = int(obj.get("w", 0) * render_scale)
            h = int(obj.get("h", 0) * render_scale)
            angle = float(obj.get("angle", 0))
            fill_color = obj.get("fill_color", [0, 0, 0])
            stroke_color = obj.get("stroke_color", [0, 0, 0])
            stroke_width = int(obj.get("stroke_width", 1) * render_scale)
            opacity = float(obj.get("opacity", 1.0))
            text = obj.get("text", "")
            font_name = obj.get("font_name", "arial.ttf")
            font_size = int(obj.get("font_size", 20) * render_scale)
            text_direction = obj.get("text_direction", "ltr")
            text_anchor = obj.get("text_anchor", "lt")
            image_path = obj.get("image_path", "")
            image_data = obj.get("image_data", "")
            shape = obj.get("shape", "rect")
            radii = obj.get("radii", [0, 0, 0, 0])

            if opacity <= 0:
                continue

            if obj_type == "image":
                img_obj = None
                if image_data:
                    import base64, io
                    try:
                        img_bytes = base64.b64decode(image_data.split(",", 1)[1] if "," in image_data else image_data)
                        img_obj = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                    except Exception:
                        img_obj = None
                elif image_path:
                    abs_path = os.path.join(os.path.dirname(__file__), "static", image_path.lstrip("/"))
                    if os.path.exists(abs_path):
                        try:
                            img_obj = Image.open(abs_path).convert("RGBA")
                        except Exception:
                            img_obj = None
                if img_obj:
                    img_obj = img_obj.resize((w, h), Image.LANCZOS)
                    if angle != 0:
                        img_obj = img_obj.rotate(angle, expand=True, resample=Image.BICUBIC)
                    if opacity < 1.0:
                        alpha = img_obj.split()[3]
                        alpha = alpha.point(lambda p: int(p * opacity))
                        img_obj.putalpha(alpha)
                    template_img.paste(img_obj, (x, y), img_obj)

            elif obj_type == "shape":
                overlay = Image.new("RGBA", template_img.size, (0, 0, 0, 0))
                overlay_draw = ImageDraw.Draw(overlay)
                fill_rgba = tuple(fill_color[:3]) + (int(255 * opacity),)
                stroke_rgba = tuple(stroke_color[:3]) + (int(255 * opacity),) if stroke_width > 0 else None
                if shape == "ellipse":
                    overlay_draw.ellipse([x, y, x + w, y + h], fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
                elif shape == "rounded_rect":
                    r = int(min(radii[0], w / 2, h / 2)) if radii else 0
                    if r > 0:
                        overlay_draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
                    else:
                        overlay_draw.rectangle([x, y, x + w, y + h], fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
                else:
                    overlay_draw.rectangle([x, y, x + w, y + h], fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
                if angle != 0:
                    overlay = overlay.rotate(angle, resample=Image.BICUBIC)
                template_img.paste(Image.alpha_composite(template_img.convert("RGBA"), overlay).convert(template_img.mode), (0, 0))

            elif obj_type == "text" and text:
                try:
                    font = load_font_dynamic(font_name, text, None, font_size, language=language)
                except Exception:
                    font = ImageFont.load_default()
                from app.helpers import process_text_for_drawing
                display_text = process_text_for_drawing(text, language)
                bbox = draw.textbbox((0, 0), display_text, font=font, **get_draw_text_kwargs(display_text, language))
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
                txt_img = Image.new("RGBA", (max(text_w + 4, 1), max(text_h + 4, 1)), (0, 0, 0, 0))
                txt_draw = ImageDraw.Draw(txt_img)
                fill_rgba = tuple(fill_color[:3]) + (int(255 * opacity),)
                txt_draw.text((-bbox[0] + 2, -bbox[1] + 2), display_text, font=font, fill=fill_rgba)
                if angle != 0:
                    txt_img = txt_img.rotate(angle, expand=True, resample=Image.BICUBIC)
                paste_x = x
                paste_y = y
                if text_anchor == "mm":
                    paste_x = x - txt_img.size[0] // 2
                    paste_y = y - txt_img.size[1] // 2
                elif text_anchor == "mt":
                    paste_x = x - txt_img.size[0] // 2
                elif text_anchor == "rt":
                    paste_x = x - txt_img.size[0]
                elif text_anchor == "rm":
                    paste_x = x - txt_img.size[0]
                    paste_y = y - txt_img.size[1] // 2
                elif text_anchor == "rb":
                    paste_x = x - txt_img.size[0]
                    paste_y = y - txt_img.size[1]
                elif text_anchor == "mb":
                    paste_x = x - txt_img.size[0] // 2
                    paste_y = y - txt_img.size[1]
                elif text_anchor == "lm":
                    paste_y = y - txt_img.size[1] // 2
                template_img.paste(txt_img, (paste_x, paste_y), txt_img)

        except Exception as obj_exc:
            logger.warning("Skipping custom object in layout render: %s", obj_exc)
