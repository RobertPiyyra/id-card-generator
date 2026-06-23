"""
Layout field resolution helpers.

Provides field layout resolution, visibility flags, and flow management.
Extracted from legacy_app.py.
"""

import logging
from flask import current_app

from models import TemplateField
from utils import (
    get_field_layout_item,
    get_layout_flow_start_y,
    get_template_language_direction,
    parse_layout_config,
)

logger = logging.getLogger(__name__)


def get_template_field_side_flags(template_obj, field_key, side="front"):
    """Return default label/value visibility for a dynamic field on the requested side."""
    if not template_obj or not field_key:
        return None

    cache = getattr(template_obj, "_field_side_visibility_cache", None)
    if cache is None:
        cache = {}
        try:
            db_fields = TemplateField.query.filter_by(
                template_id=template_obj.id,
            ).order_by(TemplateField.display_order.asc()).all()
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


def resolve_field_layout(template_obj, field_key, default_label_x, default_value_x, default_y):
    """Return label/value x-y-visibility for one field (front side shorthand)."""
    return resolve_field_layout_for_side(
        template_obj, field_key,
        default_label_x, default_value_x, default_y,
        side="front",
    )


def resolve_field_layout_for_side(
    template_obj, field_key, default_label_x, default_value_x, default_y, side="front",
):
    """Return label/value x-y-visibility for one field from the selected side layout config."""
    side_name = str(side or "front").strip().lower()
    text_direction = "ltr"
    try:
        direction_attr = "back_text_direction" if side_name == "back" else "text_direction"
        text_direction = (
            getattr(template_obj, direction_attr, "ltr") or "ltr"
        ).strip().lower()
    except Exception:
        text_direction = "ltr"

    layout_config = (
        getattr(template_obj, "back_layout_config", None)
        if side_name == "back"
        else getattr(template_obj, "layout_config", None)
    )
    if getattr(template_obj, "_ignore_layout_field_overrides", False):
        from utils import keep_layout_field_visibility_only
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
        layout_config, field_key,
        default_label_x, default_value_x, default_y,
        text_direction=text_direction,
        default_label_visible=default_label_visible,
        default_value_visible=default_value_visible,
        default_colon_visible=default_colon_visible,
        prefer_nested_part_layout=field_side_flags is not None,
    )


def get_initial_flow_y_for_side(template_obj, font_settings, side="front"):
    """Get the initial Y cursor for flowing fields on a template side."""
    side_name = "back" if str(side or "front").strip().lower() == "back" else "front"
    try:
        default_start_y = int((font_settings or {}).get("start_y", 0) or 0)
    except Exception:
        default_start_y = 0

    if not template_obj:
        return default_start_y

    layout_config = (
        getattr(template_obj, "back_layout_config", None)
        if side_name == "back"
        else getattr(template_obj, "layout_config", None)
    )
    if getattr(template_obj, "_ignore_layout_field_overrides", False):
        return default_start_y

    visibility_map = {}
    try:
        db_fields = TemplateField.query.filter_by(
            template_id=template_obj.id,
        ).order_by(TemplateField.display_order.asc()).all()
    except Exception:
        db_fields = []

    for field in db_fields:
        visibility_map[field.field_name] = {
            "label": bool(
                getattr(field, "show_label_back" if side_name == "back" else "show_label_front",
                        side_name != "back")
            ),
            "value": bool(
                getattr(field, "show_value_back" if side_name == "back" else "show_value_front",
                        side_name != "back")
            ),
        }

    return get_layout_flow_start_y(layout_config, default_start_y, visibility_map)


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


def get_form_int(form_data, key, default=0):
    """Safely get integer value from form data."""
    try:
        value = form_data.get(key)
        if value is None or value == "":
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


def _hex_to_rgb_for_editor(value, fallback=(0, 0, 0)):
    """Convert a color value to an RGB tuple for the editor."""
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
