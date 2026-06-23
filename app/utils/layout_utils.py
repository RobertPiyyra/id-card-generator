"""
Layout utilities: card sizing, layout config parsing, field positioning.

This module owns:
- Print size constants (DPI, A4, ID card dimensions)
- Card/sheet size resolution
- Template orientation lookup
- Layout config parsing & sanitization
- Field layout extraction (per label/value/colon)
- Layout -> font settings derivation
- Direction-aware X positioning (RTL/LTR)
- Text measurement helpers

Cross-module deps:
- app.utils.text_utils for _normalize_language
- app.utils.helper_utils for STATIC_DIR (via image_utils)
"""

import json
import logging
import re

from app.utils.text_utils import _normalize_language

logger = logging.getLogger(__name__)


# ================== Print Sizes ==================
DPI = 300
ID_WIDTH_PX = int((86 / 25.4) * DPI)
ID_HEIGHT_PX = int((56 / 25.4) * DPI)
PORTRAIT_WIDTH_PX = ID_HEIGHT_PX
PORTRAIT_HEIGHT_PX = ID_WIDTH_PX
A4_WIDTH_PX = int((210 / 25.4) * DPI)
A4_HEIGHT_PX = int((297 / 25.4) * DPI)
A4_LANDSCAPE_WIDTH_PX = A4_HEIGHT_PX
A4_LANDSCAPE_HEIGHT_PX = A4_WIDTH_PX


_LAYOUT_STANDARD_FIELD_ORDER = ("NAME", "F_NAME", "CLASS", "DOB", "MOBILE", "ADDRESS")


# ================== Card / Sheet Sizing ==================
def get_card_size(template_id=None):
    """Get dynamic card size from the database or fall back to CR80 defaults."""
    LANDSCAPE_W, LANDSCAPE_H = 1015, 661
    PORTRAIT_W, PORTRAIT_H = 661, 1015
    if template_id:
        try:
            from models import Template, db
            template = db.session.get(Template, template_id)
            if template:
                if template.card_width and template.card_height:
                    return template.card_width, template.card_height
                if template.card_orientation == 'portrait':
                    return PORTRAIT_W, PORTRAIT_H
                return LANDSCAPE_W, LANDSCAPE_H
        except Exception as e:
            logger.error("Error fetching template dimensions: %s", e)
    return LANDSCAPE_W, LANDSCAPE_H


def get_sheet_size(orientation):
    if orientation == 'landscape':
        return A4_LANDSCAPE_WIDTH_PX, A4_LANDSCAPE_HEIGHT_PX
    return A4_WIDTH_PX, A4_HEIGHT_PX


def get_template_orientation(template_id):
    try:
        from models import Template, db
        template = db.session.get(Template, template_id)
        if template:
            orientation = template.card_orientation if template.card_orientation else 'landscape'
            logger.debug(f"Template {template_id} orientation: {orientation}")
            return orientation
        return 'landscape'
    except Exception as e:
        logger.error(f"Error fetching orientation for template {template_id}: {e}")
        return 'landscape'


def get_template_language_direction(template_id, side="front"):
    try:
        from models import Template, db
        from app.utils.helper_utils import _resolve_template_side
        template = db.session.get(Template, template_id)
        if not template:
            return "english", "ltr"
        side_data = _resolve_template_side(template, side=side)
        lang = (side_data.get("language") or template.language or "english").strip().lower()
        direction = (side_data.get("text_direction") or template.text_direction or "ltr").strip().lower()
        return lang, direction
    except Exception as e:
        logger.error(f"Error fetching template language/direction for ID {template_id}: {e}")
        return "english", "ltr"


def get_template_layout_config(template_id, side="front"):
    try:
        from models import Template, db
        from app.utils.helper_utils import _resolve_template_side
        template = db.session.get(Template, template_id)
        if not template:
            return None
        side_data = _resolve_template_side(template, side=side)
        return side_data.get("layout_config")
    except Exception as e:
        logger.error(f"Error fetching template layout config for ID {template_id}: {e}")
        return None


# ================== Color / Hex Normalization ==================
def _normalize_hex_color(value):
    """Normalize a color input to '#RRGGBB' (uppercase), or return None if invalid."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            r = max(0, min(255, int(value[0])))
            g = max(0, min(255, int(value[1])))
            b = max(0, min(255, int(value[2])))
            return f"#{r:02X}{g:02X}{b:02X}"
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return s.upper()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", s):
        r, g, b = s[1] * 2, s[2] * 2, s[3] * 2
        return f"#{r}{g}{b}".upper()
    return None


def _hex_to_rgb_tuple(value):
    norm = _normalize_hex_color(value)
    if not norm:
        return None
    try:
        r = int(norm[1:3], 16)
        g = int(norm[3:5], 16)
        b = int(norm[5:7], 16)
        return (r, g, b)
    except Exception:
        return None


def _layout_int(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _layout_part_int(field_obj, part_name, nested_key, flat_key=None):
    if not isinstance(field_obj, dict):
        return None
    part_obj = field_obj.get(part_name)
    if isinstance(part_obj, dict):
        value = _layout_int(part_obj.get(nested_key))
        if value is not None:
            return value
    if flat_key:
        return _layout_int(field_obj.get(flat_key))
    return None


def _ordered_layout_field_keys(fields):
    if not isinstance(fields, dict):
        return []
    keys = [key for key in _LAYOUT_STANDARD_FIELD_ORDER if key in fields]
    for key in fields.keys():
        if key not in keys:
            keys.append(key)
    return keys


# ================== Layout Config Parsing ==================
def parse_layout_config(layout_config):
    """Parse and sanitize template.layout_config (from visual editor)."""
    if layout_config is None or layout_config == "":
        return {}
    data = layout_config
    if isinstance(layout_config, str):
        try:
            data = json.loads(layout_config)
        except Exception:
            return {}
    if not isinstance(data, dict):
        return {}

    out = dict(data)
    fields = data.get("fields")
    if not isinstance(fields, dict):
        fields = {}

    sanitized_fields = {}
    for field_key, field_obj in fields.items():
        if not isinstance(field_obj, dict):
            continue
        sanitized_field = dict(field_obj)
        for part in ("label", "value", "colon"):
            part_obj = field_obj.get(part)
            if not isinstance(part_obj, dict):
                continue
            sanitized_part = dict(part_obj)

            if "x" in part_obj:
                try:
                    sanitized_part["x"] = int(part_obj.get("x"))
                except Exception:
                    sanitized_part.pop("x", None)

            if "y" in part_obj:
                try:
                    sanitized_part["y"] = int(part_obj.get("y"))
                except Exception:
                    sanitized_part.pop("y", None)

            if "manual_y" in part_obj:
                sanitized_part["manual_y"] = bool(part_obj.get("manual_y"))
            elif "y" in sanitized_part:
                sanitized_part["manual_y"] = True

            if "visible" in part_obj:
                sanitized_part["visible"] = bool(part_obj.get("visible"))

            if "color" in part_obj:
                norm = _normalize_hex_color(part_obj.get("color"))
                if norm:
                    sanitized_part["color"] = norm
                else:
                    sanitized_part.pop("color", None)

            if "grow" in part_obj:
                grow = part_obj.get("grow")
                if isinstance(grow, str) and grow.strip().lower() in {"left", "center", "right"}:
                    sanitized_part["grow"] = grow.strip().lower()
                else:
                    sanitized_part.pop("grow", None)

            if "font_size" in part_obj:
                try:
                    fs = int(part_obj.get("font_size"))
                    if 6 <= fs <= 500:
                        sanitized_part["font_size"] = fs
                    else:
                        sanitized_part.pop("font_size", None)
                except Exception:
                    sanitized_part.pop("font_size", None)

            sanitized_field[part] = sanitized_part

        sanitized_fields[str(field_key)] = sanitized_field

    out["fields"] = sanitized_fields

    objects = data.get("objects")
    sanitized_objects = []
    if isinstance(objects, list):
        for idx, obj in enumerate(objects):
            if not isinstance(obj, dict):
                continue
            obj_type = str(obj.get("type") or "").strip().lower()
            if obj_type not in {"text", "rect", "circle", "line", "image", "triangle"}:
                continue
            sanitized = {
                "id": str(obj.get("id") or f"obj_{idx}"),
                "type": obj_type,
                "visible": bool(obj.get("visible", True)),
                "locked": bool(obj.get("locked", False)),
            }
            if "name" in obj:
                sanitized["name"] = str(obj.get("name") or "").strip()[:120]
            for key in ("x", "y", "width", "height", "x2", "y2", "font_size",
                       "stroke_width", "opacity", "angle", "rx", "ry",
                       "shadow_blur", "shadow_offset_x", "shadow_offset_y",
                       "char_spacing"):
                if key in obj:
                    try:
                        sanitized[key] = int(obj.get(key))
                    except Exception:
                        pass
            if "line_height" in obj:
                try:
                    sanitized["line_height"] = float(obj.get("line_height"))
                except Exception:
                    pass
            for key in ("underline", "flip_x", "flip_y", "bold", "italic"):
                if key in obj:
                    sanitized[key] = bool(obj.get(key))
            for key in ("font_family", "font_weight", "font_style",
                       "text_align", "stroke_dash_array"):
                if key in obj:
                    sanitized[key] = str(obj.get(key) or "").strip()[:100]
            if "shadow_color" in obj:
                norm = _normalize_hex_color(obj.get("shadow_color"))
                if norm:
                    sanitized["shadow_color"] = norm
            if obj_type == "text":
                sanitized["text"] = str(obj.get("text") if obj.get("text") is not None else "Text")
            if obj_type == "image":
                src = str(obj.get("src") or "").strip()
                if src:
                    sanitized["src"] = src
            if "fill" in obj:
                norm = _normalize_hex_color(obj.get("fill"))
                if norm:
                    sanitized["fill"] = norm
            if "stroke" in obj:
                norm = _normalize_hex_color(obj.get("stroke"))
                if norm:
                    sanitized["stroke"] = norm
            sanitized_objects.append(sanitized)
    if sanitized_objects:
        out["objects"] = sanitized_objects
    else:
        out.pop("objects", None)
    return out


def derive_font_settings_from_layout_config(layout_config, font_settings=None):
    """Derive the legacy/global text controls from visual-editor field layout."""
    settings = dict(font_settings or {})
    parsed = parse_layout_config(layout_config)
    fields = parsed.get("fields") if isinstance(parsed, dict) else None
    if not isinstance(fields, dict) or not fields:
        return settings

    row_y_values = []
    label_x_set = False
    value_x_set = False
    label_size_set = False
    value_size_set = False
    for key in _ordered_layout_field_keys(fields):
        field_obj = fields.get(key)
        if not isinstance(field_obj, dict):
            continue

        label_x = _layout_part_int(field_obj, "label", "x", "label_x")
        value_x = _layout_part_int(field_obj, "value", "x", "value_x")
        label_y = _layout_part_int(field_obj, "label", "y", "label_y")
        value_y = _layout_part_int(field_obj, "value", "y", "value_y")
        label_size = _layout_part_int(field_obj, "label", "font_size", "label_font_size")
        value_size = _layout_part_int(field_obj, "value", "font_size", "value_font_size")

        if label_x is not None and not label_x_set:
            settings["label_x"] = label_x
            label_x_set = True
        if value_x is not None and not value_x_set:
            settings["value_x"] = value_x
            value_x_set = True
        if label_size is not None and not label_size_set:
            settings["label_font_size"] = label_size
            label_size_set = True
        if value_size is not None and not value_size_set:
            settings["value_font_size"] = value_size
            value_size_set = True

        y_candidates = [value for value in (label_y, value_y) if value is not None]
        if y_candidates:
            row_y_values.append(min(y_candidates))

    if row_y_values:
        settings["start_y"] = row_y_values[0]
        positive_diffs = []
        last_y = row_y_values[0]
        for row_y in row_y_values[1:]:
            diff = int(row_y) - int(last_y)
            if diff > 0:
                positive_diffs.append(diff)
                last_y = row_y
        if positive_diffs:
            settings["line_height"] = positive_diffs[0]
    return settings


# ================== Field Layout Extraction ==================
def get_field_layout_item(
    layout_config_raw,
    field_key,
    default_label_x,
    default_value_x,
    default_y,
    text_direction="ltr",
    default_label_visible=True,
    default_value_visible=True,
    default_colon_visible=None,
    prefer_nested_part_layout=False,
):
    """Read one field's layout from template.layout_config."""
    result = {
        "label_x": default_label_x,
        "label_y": default_y,
        "label_visible": bool(default_label_visible),
        "label_manual_y": False,
        "label_grow": _normalize_grow_mode(None, text_direction),
        "label_font_size": None,
        "label_char_spacing": 0,
        "label_line_height": 1.16,
        "label_auto_fit": False,
        "label_max_width": 200,
        "value_x": default_value_x,
        "value_y": default_y,
        "value_visible": bool(default_value_visible),
        "value_manual_y": False,
        "value_grow": _normalize_grow_mode(None, text_direction),
        "value_font_size": None,
        "value_char_spacing": 0,
        "value_line_height": 1.16,
        "value_auto_fit": False,
        "value_max_width": 200,
        "colon_x": None,
        "colon_y": default_y,
        "colon_visible": bool(default_label_visible if default_colon_visible is None else default_colon_visible),
        "colon_manual_y": False,
        "colon_grow": "left" if text_direction == "rtl" else "right",
        "colon_font_size": None,
        "colon_char_spacing": 0,
        "colon_line_height": 1.16,
        "colon_auto_fit": False,
        "colon_max_width": 200,
    }

    if not field_key:
        return result

    data = layout_config_raw
    if isinstance(layout_config_raw, str):
        try:
            data = json.loads(layout_config_raw)
        except Exception:
            data = None

    if not isinstance(data, dict):
        return result

    fields = data.get("fields")
    if not isinstance(fields, dict):
        return result

    field_obj = fields.get(field_key)
    if not isinstance(field_obj, dict):
        return result

    def _part_has_explicit_layout(part_name):
        part_obj = field_obj.get(part_name)
        if isinstance(part_obj, dict):
            for key in ("x", "y", "manual_y", "font_size", "color", "grow",
                       "char_spacing", "line_height", "auto_fit", "max_width"):
                if key in part_obj:
                    return True
        flat_keys = [
            f"{part_name}_x", f"{part_name}_y", f"{part_name}_font_size",
            f"{part_name}_color", f"{part_name}_grow", f"{part_name}_char_spacing",
            f"{part_name}_line_height", f"{part_name}_auto_fit", f"{part_name}_max_width",
        ]
        for key in flat_keys:
            if key in field_obj:
                return True
        return False

    explicit_visibility = {"label": False, "value": False, "colon": False}

    for prefix in ("label", "value", "colon"):
        flat_x = f"{prefix}_x"
        flat_y = f"{prefix}_y"
        flat_visible = f"{prefix}_visible"
        flat_grow = f"{prefix}_grow"
        flat_font_size = f"{prefix}_font_size"
        flat_color = f"{prefix}_color"
        flat_char_spacing = f"{prefix}_char_spacing"
        flat_line_height = f"{prefix}_line_height"
        flat_auto_fit = f"{prefix}_auto_fit"
        flat_max_width = f"{prefix}_max_width"

        part_obj = field_obj.get(prefix)
        nested_has_x = isinstance(part_obj, dict) and "x" in part_obj
        nested_has_y = isinstance(part_obj, dict) and "y" in part_obj

        if flat_x in field_obj and not nested_has_x:
            try:
                result[flat_x] = int(field_obj.get(flat_x))
            except Exception:
                pass
        if flat_y in field_obj and not nested_has_y:
            try:
                flat_y_value = int(field_obj.get(flat_y))
                result[flat_y] = flat_y_value
                result[f"{prefix}_manual_y"] = True
            except Exception:
                pass
        if flat_visible in field_obj:
            explicit_visibility[prefix] = True
            result[flat_visible] = bool(field_obj.get(flat_visible)) and bool(result[flat_visible])
        if flat_grow in field_obj:
            result[flat_grow] = _normalize_grow_mode(field_obj.get(flat_grow), text_direction)
        if flat_font_size in field_obj:
            try:
                fs = int(field_obj.get(flat_font_size))
                if fs > 0:
                    result[flat_font_size] = fs
            except Exception:
                pass
        if flat_color in field_obj:
            rgb = _hex_to_rgb_tuple(field_obj.get(flat_color))
            if rgb:
                result[flat_color] = rgb
        if flat_char_spacing in field_obj:
            try:
                result[flat_char_spacing] = int(field_obj.get(flat_char_spacing))
            except Exception:
                pass
        if flat_line_height in field_obj:
            try:
                result[flat_line_height] = float(field_obj.get(flat_line_height))
            except Exception:
                pass
        if flat_auto_fit in field_obj:
            result[flat_auto_fit] = bool(field_obj.get(flat_auto_fit))
        if flat_max_width in field_obj:
            try:
                result[flat_max_width] = int(field_obj.get(flat_max_width))
            except Exception:
                pass

    for part, prefix in (("label", "label"), ("value", "value"), ("colon", "colon")):
        part_obj = field_obj.get(part)
        if not isinstance(part_obj, dict):
            continue

        if "x" in part_obj:
            try:
                result[f"{prefix}_x"] = int(part_obj.get("x"))
            except Exception:
                pass
        if "y" in part_obj:
            try:
                result[f"{prefix}_y"] = int(part_obj.get("y"))
                result[f"{prefix}_manual_y"] = bool(part_obj.get("manual_y")) or "manual_y" not in part_obj
            except Exception:
                pass
        if "visible" in part_obj:
            explicit_visibility[prefix] = True
            result[f"{prefix}_visible"] = bool(part_obj.get("visible")) and bool(result[f"{prefix}_visible"])
        if "grow" in part_obj:
            result[f"{prefix}_grow"] = _normalize_grow_mode(part_obj.get("grow"), text_direction)
        if "font_size" in part_obj:
            try:
                fs = int(part_obj.get("font_size"))
                if fs > 0:
                    result[f"{prefix}_font_size"] = fs
            except Exception:
                pass
        if "color" in part_obj:
            rgb = _hex_to_rgb_tuple(part_obj.get("color"))
            if rgb:
                result[f"{prefix}_color"] = rgb
        if "char_spacing" in part_obj:
            try:
                result[f"{prefix}_char_spacing"] = int(part_obj.get("char_spacing"))
            except Exception:
                pass
        if "line_height" in part_obj:
            try:
                result[f"{prefix}_line_height"] = float(part_obj.get("line_height"))
            except Exception:
                pass
        if "auto_fit" in part_obj:
            result[f"{prefix}_auto_fit"] = bool(part_obj.get("auto_fit"))
        if "max_width" in part_obj:
            try:
                result[f"{prefix}_max_width"] = int(part_obj.get("max_width"))
            except Exception:
                pass

    for prefix in ("label", "value", "colon"):
        visible_key = f"{prefix}_visible"
        if not explicit_visibility[prefix] and _part_has_explicit_layout(prefix):
            result[visible_key] = True

    return result


def get_layout_flow_start_y(layout_config_raw, default_start_y, field_visibility=None):
    """Derive the effective flow start Y from saved layout coordinates."""
    try:
        fallback = int(default_start_y or 0)
    except Exception:
        fallback = 0

    parsed = parse_layout_config(layout_config_raw)
    fields = parsed.get("fields") if isinstance(parsed, dict) else None
    if not isinstance(fields, dict):
        return fallback

    visibility_map = field_visibility if isinstance(field_visibility, dict) else {}
    y_candidates = []

    for field_key, field_obj in fields.items():
        if not isinstance(field_obj, dict):
            continue

        field_vis = visibility_map.get(str(field_key), {})
        label_visible = bool(field_vis.get("label", True))
        value_visible = bool(field_vis.get("value", True))

        def _pick_y(part_name, flat_key):
            part_obj = field_obj.get(part_name)
            if isinstance(part_obj, dict) and "y" in part_obj:
                try:
                    return int(part_obj.get("y"))
                except Exception:
                    return None
            if flat_key in field_obj:
                try:
                    return int(field_obj.get(flat_key))
                except Exception:
                    return None
            return None

        if label_visible:
            y = _pick_y("label", "label_y")
            if y is not None:
                y_candidates.append(y)
        if value_visible:
            y = _pick_y("value", "value_y")
            if y is not None:
                y_candidates.append(y)

    return min(y_candidates) if y_candidates else fallback


# ================== Direction-aware Positioning ==================
def _normalize_grow_mode(grow_mode, text_direction):
    """Normalize requested growth/anchor mode (left/center/right)."""
    direction = (text_direction or "ltr").strip().lower()
    if isinstance(grow_mode, str):
        mode = grow_mode.strip().lower()
        if mode in {"left", "center", "right"}:
            return mode
    return "right" if direction == "rtl" else "left"


def _measure_text_width(text, font, draw=None):
    """Measure text width with safe fallbacks."""
    from app.utils.text_utils import _script_hint_for_text, get_draw_text_kwargs
    text = "" if text is None else str(text)
    if not text:
        return 0.0

    lang_hint = _script_hint_for_text(text)
    draw_kwargs = get_draw_text_kwargs(text, language=lang_hint)

    try:
        if draw is not None and hasattr(draw, "textlength"):
            return float(draw.textlength(text, font=font, **draw_kwargs))
    except Exception:
        pass

    try:
        if font is not None and hasattr(font, "getlength"):
            return float(font.getlength(text))
    except Exception:
        pass

    try:
        if draw is not None and hasattr(draw, "textbbox"):
            bbox = draw.textbbox((0, 0), text, font=font, **draw_kwargs)
            if bbox:
                return float(bbox[2] - bbox[0])
    except Exception:
        pass

    return 0.0


def flip_x_for_text_direction(x, text, font, image_width, text_direction, draw=None, grow_mode=None):
    """Direction-aware X placement with anchor growth modes."""
    direction = (text_direction or "ltr").strip().lower()
    mode = _normalize_grow_mode(grow_mode, direction)
    text_w = _measure_text_width(text, font, draw=draw)

    try:
        x_f = float(x)
        img_w = float(image_width)
        anchor = (img_w - x_f) if direction == "rtl" else x_f

        if mode == "left":
            return anchor
        if mode == "center":
            return anchor - (float(text_w) / 2.0)
        return anchor - float(text_w)
    except Exception:
        return x


def get_anchor_max_text_width(
    card_width,
    anchor_x,
    text_direction,
    line_y,
    line_height,
    grow_mode=None,
    photo_x=0,
    photo_y=0,
    photo_width=0,
    photo_height=0,
    page_margin=20,
    photo_gap=15,
    min_width=20,
):
    """Return the usable width for text anchored at anchor_x."""
    try:
        direction = (text_direction or "ltr").strip().lower()
        mode = _normalize_grow_mode(grow_mode, direction)
        card_w = float(card_width)
        anchor = (card_w - float(anchor_x)) if direction == "rtl" else float(anchor_x)
        left_limit = float(page_margin)
        right_limit = card_w - float(page_margin)

        photo_w = max(0.0, float(photo_width or 0))
        photo_h = max(0.0, float(photo_height or 0))
        if photo_w > 0 and photo_h > 0:
            line_top = float(line_y or 0)
            line_bottom = line_top + float(line_height or 0)
            photo_top = float(photo_y or 0)
            photo_bottom = photo_top + photo_h
            overlaps_photo = (line_top < photo_bottom) and (line_bottom > photo_top)
            if overlaps_photo:
                photo_left = float(photo_x or 0)
                photo_right = photo_left + photo_w
                if photo_right <= anchor:
                    left_limit = max(left_limit, photo_right + float(photo_gap))
                elif photo_left >= anchor:
                    right_limit = min(right_limit, photo_left - float(photo_gap))
                else:
                    left_limit = max(left_limit, photo_right + float(photo_gap))
                    right_limit = min(right_limit, photo_left - float(photo_gap))

        available_left = max(0.0, anchor - left_limit)
        available_right = max(0.0, right_limit - anchor)

        if mode == "left":
            max_width = available_right
        elif mode == "right":
            max_width = available_left
        else:
            max_width = 2.0 * min(available_left, available_right)

        return max(float(min_width), float(max_width))
    except Exception:
        return float(min_width)


def colon_anchor_for_value(value_x, text_direction, gap_px=8):
    """Return (anchor_x, grow_mode) for drawing an aligned colon near value_x."""
    direction = (text_direction or "ltr").strip().lower()
    try:
        gap = max(0, int(gap_px))
    except Exception:
        gap = 8

    if direction == "rtl":
        return float(value_x) + float(gap), "left"
    return float(value_x) - float(gap), "right"


__all__ = [
    "A4_HEIGHT_PX",
    "A4_LANDSCAPE_HEIGHT_PX",
    "A4_LANDSCAPE_WIDTH_PX",
    "A4_WIDTH_PX",
    "DPI",
    "ID_HEIGHT_PX",
    "ID_WIDTH_PX",
    "PORTRAIT_HEIGHT_PX",
    "PORTRAIT_WIDTH_PX",
    "colon_anchor_for_value",
    "derive_font_settings_from_layout_config",
    "flip_x_for_text_direction",
    "get_anchor_max_text_width",
    "get_card_size",
    "get_field_layout_item",
    "get_layout_flow_start_y",
    "get_sheet_size",
    "get_template_language_direction",
    "get_template_layout_config",
    "get_template_orientation",
    "parse_layout_config",
]
