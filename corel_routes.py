import os
import io
import json
import math
import logging
import html
import re
import unicodedata
import requests
from flask import Blueprint, send_file, session, redirect, url_for, current_app, request
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.colors import Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import createBarcodeDrawing, qr as rl_qr
from reportlab.graphics.shapes import Drawing
from PIL import Image, ImageDraw, ImageFont, ImageOps
import arabic_reshaper
from bidi.algorithm import get_display
import fitz  # PyMuPDF

# Import models and utils
from models import db, Student, Template, TemplateField
from utils import (
    UPLOAD_FOLDER, GENERATED_FOLDER, FONTS_FOLDER, PLACEHOLDER_PATH,
    get_template_settings, get_template_path, get_card_size, 
    get_template_orientation, generate_qr_code, generate_barcode_code128, generate_data_hash,
    load_template, _language_font_fallbacks, _presentation_forms_font_fallbacks,
    process_text_for_drawing, get_draw_text_kwargs,
    split_label_and_colon, colon_anchor_for_value,
    load_font_dynamic, get_field_layout_item, PIL_RAQM_AVAILABLE, _font_covers_text,
    get_cloudinary_face_crop_url, round_photo, parse_layout_config,
)
from utils import load_template_smart
corel_bp = Blueprint('corel', __name__)
logger = logging.getLogger(__name__)
try:
    _ARABIC_RESHAPER = arabic_reshaper.ArabicReshaper(
        configuration={"use_unshaped_instead_of_isolated": True}
    )
except Exception:
    _ARABIC_RESHAPER = None


def local_apply_text_case(text, case_type):
    if not text: return ""
    text = str(text)
    if case_type == "uppercase": return text.upper()
    elif case_type == "lowercase": return text.lower()
    elif case_type == "capitalize": return text.title()
    return text


def _normalize_language(language: str) -> str:
    return (language or "english").strip().lower()


_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)

_ORDER_TO_KEY = {
    10: "NAME",
    20: "F_NAME",
    30: "CLASS",
    40: "DOB",
    50: "MOBILE",
    60: "ADDRESS",
}


def _field_key_from_item(item: dict) -> str | None:
    if not isinstance(item, dict):
        return None
    return item.get("k") or item.get("key") or _ORDER_TO_KEY.get(item.get("ord"))

def _field_wrap_policy(field_key: str | None) -> dict:
    key = str(field_key or "").strip().upper()
    defaults = {
        "max_lines": 3,
        "min_scale": 0.78,
        "line_height_factor": 1.15,
    }
    per_field = {
        "NAME": {"max_lines": 2, "min_scale": 0.84, "line_height_factor": 1.12},
        "F_NAME": {"max_lines": 2, "min_scale": 0.8, "line_height_factor": 1.12},
        "CLASS": {"max_lines": 1, "min_scale": 0.9, "line_height_factor": 1.08},
        "DOB": {"max_lines": 1, "min_scale": 0.88, "line_height_factor": 1.08},
        "MOBILE": {"max_lines": 1, "min_scale": 0.88, "line_height_factor": 1.08},
        "PHONE": {"max_lines": 1, "min_scale": 0.88, "line_height_factor": 1.08},
        "ADDRESS": {"max_lines": 2, "min_scale": 0.72, "line_height_factor": 1.15},
    }
    policy = dict(defaults)
    policy.update(per_field.get(key, {}))
    return policy


def _field_consumes_layout_space(layout_item: dict | None, raw_value: str = "") -> bool:
    if not isinstance(layout_item, dict):
        return bool(str(raw_value or "").strip())
    if layout_item.get("label_visible"):
        return True
    return bool(layout_item.get("value_visible")) and bool(str(raw_value or "").strip())


def _draw_custom_editor_objects_pdf(c, layout_config_raw, card_x, card_bottom_y, card_h_pt, scale, reg_font_name):
    parsed = parse_layout_config(layout_config_raw)
    objects = parsed.get("objects") if isinstance(parsed, dict) else None
    if not isinstance(objects, list):
        return
    for obj in objects:
        if not isinstance(obj, dict) or not obj.get("visible", True):
            continue
        kind = str(obj.get("type") or "").strip().lower()
        x = card_x + (float(obj.get("x", 0)) * scale)
        y = card_bottom_y + (card_h_pt - (float(obj.get("y", 0)) * scale))
        angle = float(obj.get("angle", 0) or 0)
        opacity = max(0.0, min(1.0, float(obj.get("opacity", 100) or 100) / 100.0))
        fill_hex = str(obj.get("fill") or "#1f4e8c")
        stroke_hex = str(obj.get("stroke") or fill_hex)
        def _hex_to_color(h):
            try:
                return Color(int(h[1:3],16)/255.0, int(h[3:5],16)/255.0, int(h[5:7],16)/255.0)
            except Exception:
                return Color(0.12,0.31,0.55)
        fill = _hex_to_color(fill_hex)
        stroke = _hex_to_color(stroke_hex)
        stroke_width = max(0.5, float(obj.get("stroke_width", 2)) * scale)
        if kind == "text":
            c.saveState()
            c.translate(x, y)
            if angle:
                c.rotate(-angle)
            if hasattr(c, "setFillAlpha"):
                c.setFillAlpha(opacity)
            c.setFillColor(fill)
            c.setFont(reg_font_name, max(6.0, float(obj.get("font_size", 24)) * scale))
            c.drawString(0, 0, str(obj.get("text") or "Text"))
            c.restoreState()
        elif kind == "rect":
            w = max(1.0, float(obj.get("width", 120)) * scale)
            h = max(1.0, float(obj.get("height", 60)) * scale)
            c.saveState()
            c.translate(x + (w / 2.0), y - (h / 2.0))
            if angle:
                c.rotate(-angle)
            if hasattr(c, "setFillAlpha"):
                c.setFillAlpha(opacity)
            if hasattr(c, "setStrokeAlpha"):
                c.setStrokeAlpha(opacity)
            c.setFillColor(fill)
            c.setStrokeColor(stroke)
            c.setLineWidth(stroke_width)
            c.rect(-(w / 2.0), -(h / 2.0), w, h, fill=1, stroke=1)
            c.restoreState()
        elif kind == "circle":
            w = max(1.0, float(obj.get("width", 80)) * scale)
            h = max(1.0, float(obj.get("height", obj.get("width", 80))) * scale)
            c.saveState()
            c.translate(x + (w / 2.0), y - (h / 2.0))
            if angle:
                c.rotate(-angle)
            if hasattr(c, "setFillAlpha"):
                c.setFillAlpha(opacity)
            if hasattr(c, "setStrokeAlpha"):
                c.setStrokeAlpha(opacity)
            c.setFillColor(fill)
            c.setStrokeColor(stroke)
            c.setLineWidth(stroke_width)
            c.ellipse(-(w / 2.0), -(h / 2.0), (w / 2.0), (h / 2.0), fill=1, stroke=1)
            c.restoreState()
        elif kind == "line":
            x2 = card_x + (float(obj.get("x2", obj.get("x", 0) + 120)) * scale)
            y2 = card_bottom_y + (card_h_pt - (float(obj.get("y2", obj.get("y", 0))) * scale))
            c.saveState()
            if hasattr(c, "setStrokeAlpha"):
                c.setStrokeAlpha(opacity)
            c.setStrokeColor(stroke)
            c.setLineWidth(stroke_width)
            c.line(x, y, x2, y2)
            c.restoreState()
        elif kind == "image":
            src = str(obj.get("src") or "").strip()
            if not src:
                continue
            try:
                if src.startswith("data:image"):
                    _, encoded = src.split(",", 1)
                    image_bytes = base64.b64decode(encoded)
                    image_reader = ImageReader(io.BytesIO(image_bytes))
                elif src.startswith(("http://", "https://")):
                    resp = requests.get(src, timeout=10)
                    resp.raise_for_status()
                    image_reader = ImageReader(io.BytesIO(resp.content))
                else:
                    image_path = src if os.path.isabs(src) else os.path.join(STATIC_DIR, src.lstrip("/"))
                    image_reader = ImageReader(image_path)
                w = max(1.0, float(obj.get("width", 120)) * scale)
                h = max(1.0, float(obj.get("height", 120)) * scale)
                if opacity < 0.999:
                    if src.startswith("data:image"):
                        overlay = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
                    elif src.startswith(("http://", "https://")):
                        overlay = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                    else:
                        overlay = Image.open(image_path).convert("RGBA")
                    alpha_channel = overlay.getchannel("A").point(lambda px: int(px * opacity))
                    overlay.putalpha(alpha_channel)
                    image_reader = ImageReader(overlay)
                c.saveState()
                c.translate(x + (w / 2.0), y - (h / 2.0))
                if angle:
                    c.rotate(-angle)
                c.drawImage(image_reader, -(w / 2.0), -(h / 2.0), width=w, height=h, mask="auto")
                c.restoreState()
            except Exception as image_err:
                logger.warning("Skipping custom image object in PDF render due to error: %s", image_err)


def _contains_arabic_script(text: str) -> bool:
    if not text:
        return False
    for ch in str(text):
        cp = ord(ch)
        for start, end in _ARABIC_RANGES:
            if start <= cp <= end:
                return True
    return False


def _safe_bidi_get_display(text: str, base_dir: str = "R") -> str:
    """
    Compatibility wrapper for python-bidi versions that may not support `base_dir`.
    """
    try:
        return get_display(text, base_dir=base_dir)
    except TypeError:
        return get_display(text)


def _clean_bidi_controls(text: str) -> str:
    if text is None:
        return ""
    cleaned = []
    for ch in str(text):
        cp = ord(ch)
        cat = unicodedata.category(ch)
        if cp in {0xFFFD, 0xFEFF}:
            continue
        if cat in {"Cc", "Cs"}:
            continue
        if cat == "Cf" and ch not in {"\u200c", "\u200d"}:
            continue
        cleaned.append(ch)
    return "".join(cleaned)


def process_text_for_vector(text: str, language: str) -> str:
    """
    Prepare text for ReportLab drawing.

    Why this exists:
    - ReportLab does not do complex shaping (joining) or BiDi reordering by itself.
    - Arabic/Urdu need reshaping (glyph joining) + BiDi to display correctly.
    - Hindi (Devanagari) is LTR and does not need BiDi, so return unchanged.
    """
    text = _clean_bidi_controls(text)
    if not text:
        return ""
    language = _normalize_language(language)

    # If template language is English but the value contains Arabic-script, still process it.
    if language not in {"arabic", "urdu"} and _contains_arabic_script(text):
        language = "arabic"

    if language in {"arabic", "urdu"}:
        try:
            if _ARABIC_RESHAPER is not None:
                reshaped = _ARABIC_RESHAPER.reshape(text)
            else:
                reshaped = arabic_reshaper.reshape(text)
            # base_dir='R' ensures stable RTL display for ReportLab (which draws LTR only).
            return _clean_bidi_controls(_safe_bidi_get_display(reshaped, base_dir="R"))
        except Exception as exc:
            logger.warning("Vector text shaping failed for Arabic/Urdu: %s", exc)
            return text

    # Hindi / English / others
    return text


def _normalize_grow_mode(grow_mode, direction: str) -> str:
    direction = (direction or "ltr").strip().lower()
    if isinstance(grow_mode, str):
        mode = grow_mode.strip().lower()
        if mode in {"left", "center", "right"}:
            return mode
    return "right" if direction == "rtl" else "left"


def _x_for_direction(card_x, card_w_pt, x_px, text, font_name, font_size_pt, scale, direction: str, grow_mode=None) -> float:
    """
    Direction-aware X placement with anchor growth mode.
    """
    direction = (direction or "ltr").strip().lower()
    mode = _normalize_grow_mode(grow_mode, direction)
    try:
        text_w = pdfmetrics.stringWidth(text, font_name, font_size_pt)
    except Exception:
        text_w = 0

    anchor = card_x + ((card_w_pt - (x_px * scale)) if direction == "rtl" else (x_px * scale))
    if mode == "left":
        return anchor
    if mode == "center":
        return anchor - (text_w / 2.0)
    return anchor - text_w


def _x_for_direction_raster(card_x, card_w_pt, x_px, text_width_px: float, scale, direction: str, grow_mode=None) -> float:
    """
    Direction-aware X placement for rasterized runs (measured in px).
    """
    direction = (direction or "ltr").strip().lower()
    mode = _normalize_grow_mode(grow_mode, direction)
    text_w_pt = float(text_width_px or 0) * scale
    anchor = card_x + ((card_w_pt - (x_px * scale)) if direction == "rtl" else (x_px * scale))
    if mode == "left":
        return anchor
    if mode == "center":
        return anchor - (text_w_pt / 2.0)
    return anchor - text_w_pt


_PIL_FONT_CACHE: dict[tuple[str, int, str], ImageFont.ImageFont] = {}
_RASTER_TEXT_METRICS_CACHE: dict[tuple[tuple[str, str, int, str], str, str], tuple[tuple[int, int, int, int], int, int, float, float]] = {}
_VECTOR_TEXT_WIDTH_CACHE: dict[tuple[str, float, str], float] = {}


def _get_pil_font(font_path_or_name: str, font_size_px: int, language: str) -> ImageFont.ImageFont:
    """
    Load a Pillow font for text rasterization, with a small cache.

    Notes:
    - We intentionally go through `load_font_dynamic()` because it contains our Unicode fallbacks
      and avoids Arial for Arabic/Urdu/Hindi.
    """
    key = (str(font_path_or_name or ""), int(font_size_px), str(language or ""))
    cached = _PIL_FONT_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        lang = _normalize_language(language)
        sample_text = {
            "urdu": "نمونہ",
            "arabic": "عربي",
            "hindi": "परीक्षण",
        }.get(lang, "X")
        font = load_font_dynamic(
            font_path_or_name,
            sample_text,
            max_width=0,
            start_size=font_size_px,
            language=language,
        )
    except Exception:
        font = ImageFont.load_default()

    _PIL_FONT_CACHE[key] = font
    return font

def _pil_font_signature(pil_font: ImageFont.ImageFont) -> tuple[str, str, int, str]:
    try:
        font_name = "|".join(str(part) for part in pil_font.getname())
    except Exception:
        font_name = pil_font.__class__.__name__
    font_path = str(getattr(pil_font, "path", "") or "")
    font_size = int(getattr(pil_font, "size", 0) or 0)
    return (
        font_path,
        font_name,
        font_size,
        pil_font.__class__.__name__,
    )

def _measure_raster_text_metrics(
    text: str,
    pil_font: ImageFont.ImageFont,
    language: str,
) -> tuple[tuple[int, int, int, int], int, int, float, float]:
    text = "" if text is None else str(text)
    cache_key = (_pil_font_signature(pil_font), str(language or ""), text)
    cached = _RASTER_TEXT_METRICS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    draw_kwargs = get_draw_text_kwargs(text, language)
    dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    drawer = ImageDraw.Draw(dummy)

    try:
        bbox = drawer.textbbox((0, 0), text, font=pil_font, **draw_kwargs)
    except Exception:
        bbox = (0, 0, 0, 0)

    bbox_w = max(0, int(math.ceil((bbox[2] - bbox[0]) or 0)))
    bbox_h = max(0, int(math.ceil((bbox[3] - bbox[1]) or 0)))
    w = max(1, bbox_w)
    h = max(1, bbox_h)

    try:
        width_px = float(drawer.textlength(text, font=pil_font, **draw_kwargs))
    except Exception:
        try:
            width_px = float(pil_font.getlength(text))
        except Exception:
            width_px = float(bbox_w)
    width_px = float(max(width_px, float(bbox_w)))

    try:
        ascent, _descent = pil_font.getmetrics()
        baseline_y_px = float(ascent - bbox[1])
    except Exception:
        baseline_y_px = float(max(0, -bbox[1]))

    measured = (bbox, w, h, baseline_y_px, width_px)
    _RASTER_TEXT_METRICS_CACHE[cache_key] = measured
    return measured


def _build_text_image(text: str, pil_font: ImageFont.ImageFont, fill_rgba: tuple[int, int, int, int], language: str) -> tuple[Image.Image, float, float]:
    """
    Render text into a transparent RGBA image.

    Returns:
    - image
    - baseline_y_px: y offset (in px) from top of image to text baseline
    - width_px: rendered width in pixels (used for RTL anchoring)

    Why:
    - ReportLab doesn't do complex script shaping (Urdu/Arabic/Hindi). Pillow+RAQM does, so we
      rasterize those runs to avoid font substitution and keep the same look as the preview.
    """
    text = "" if text is None else str(text)
    draw_kwargs = get_draw_text_kwargs(text, language)
    bbox, w, h, baseline_y_px, width_px = _measure_raster_text_metrics(text, pil_font, language)
    pad_x = max(1, int(math.ceil(max(1, getattr(pil_font, "size", 0)) * 0.08)))
    pad_y = max(1, int(math.ceil(max(1, getattr(pil_font, "size", 0)) * 0.14)))
    img_w = max(1, w + (pad_x * 2))
    img_h = max(1, h + (pad_y * 2))
    baseline_y_px += pad_y

    # Render
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    dr.text((pad_x - bbox[0], pad_y - bbox[1]), text, font=pil_font, fill=fill_rgba, **draw_kwargs)
    return img, baseline_y_px, float(max(width_px + (pad_x * 2), img_w))

def draw_custom_rounded_rect(c, x, y, w, h, radii):
    tl, tr, br, bl = [float(r) for r in radii]
    path = c.beginPath()
    path.moveTo(x, y + h - tl)
    if tl > 0: path.arcTo(x, y + h - 2*tl, x + 2*tl, y + h, 180, -90)
    else: path.lineTo(x, y + h) 
    path.lineTo(x + w - tr, y + h)
    if tr > 0: path.arcTo(x + w - 2*tr, y + h - 2*tr, x + w, y + h, 90, -90)
    else: path.lineTo(x + w, y + h)
    path.lineTo(x + w, y + br)
    if br > 0: path.arcTo(x + w - 2*br, y, x + w, y + 2*br, 0, -90)
    else: path.lineTo(x + w, y)
    path.lineTo(x + bl, y)
    if bl > 0: path.arcTo(x, y, x + 2*bl, y + 2*bl, 270, -90)
    else: path.lineTo(x, y)
    path.close()
    return path


LAYOUT_DPI = 300
PRINT_DPI = 600
DEFAULT_EXPORT_MODE = "print"
SUPPORTED_EXPORT_MODES = {"editable", "print"}
SUPPORTED_COREL_PHOTO_MODES = {"embed", "frame_only"}


def parse_pdf_export_mode(mode_raw: str | None) -> str | None:
    """Parse export mode from query/form input."""
    if mode_raw is None:
        return DEFAULT_EXPORT_MODE
    mode = str(mode_raw).strip().lower()
    if not mode:
        return DEFAULT_EXPORT_MODE
    if mode in SUPPORTED_EXPORT_MODES:
        return mode
    return None


def _render_profile(mode: str) -> dict:
    mode = (mode or DEFAULT_EXPORT_MODE).strip().lower()
    is_print = mode == "print"
    raster_multiplier = 2 if is_print else 1
    return {
        "mode": mode,
        "layout_dpi": LAYOUT_DPI,
        "asset_dpi": PRINT_DPI if is_print else LAYOUT_DPI,
        "raster_multiplier": raster_multiplier,
    }


def _corel_editable_photo_mode(photo_settings: dict | None) -> str:
    mode = str((photo_settings or {}).get("corel_editable_photo_mode", "frame_only") or "frame_only").strip().lower()
    if mode in SUPPORTED_COREL_PHOTO_MODES:
        return mode
    return "frame_only"

def _normalize_wrap_text(text: str) -> str:
    raw = "" if text is None else str(text)
    raw = unicodedata.normalize("NFC", raw)
    raw = raw.replace("\u00A0", " ").replace("\u202F", " ").replace("\t", " ")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = _clean_bidi_controls(raw)
    cleaned_lines: list[str] = []
    for line in raw.split("\n"):
        compact = " ".join(line.split()).strip()
        if compact:
            cleaned_lines.append(compact)
    return "\n".join(cleaned_lines).strip()

def _measure_vector_text_width(text: str, font_name: str, font_size_pt: float) -> float:
    cache_key = (str(font_name or ""), round(float(font_size_pt or 0.0), 4), str(text or ""))
    cached = _VECTOR_TEXT_WIDTH_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        width = float(pdfmetrics.stringWidth(str(text or ""), font_name, float(font_size_pt)))
    except Exception:
        width = float(max(0, len(str(text or ""))) * max(1.0, float(font_size_pt)) * 0.55)
    _VECTOR_TEXT_WIDTH_CACHE[cache_key] = width
    return width

def _measure_raster_text_width(
    text: str,
    *,
    font_path_or_name: str,
    font_size_pt: float,
    language: str,
    scale: float,
    raster_multiplier: int,
) -> float:
    text = str(text or "")
    if not text:
        return 0.0

    scale = max(float(scale or 0.0), 0.001)
    raster_multiplier = max(1, int(raster_multiplier or 1))
    font_size_px = max(1, int(round((float(font_size_pt) / scale) * raster_multiplier)))
    pil_font = _get_pil_font(font_path_or_name, font_size_px, language)
    try:
        _bbox, _w, _h, _baseline, width_px = _measure_raster_text_metrics(text, pil_font, language)
    except Exception:
        width_px = float(max(0, len(text)) * font_size_px * 0.55)
    return width_px * (scale / raster_multiplier)

def _ellipsize_to_width(text: str, max_width_pt: float, measure_fn) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    ellipsis = "..."
    if measure_fn(value) <= max_width_pt:
        return value
    if measure_fn(ellipsis) > max_width_pt:
        return ""
    words = value.split()
    if len(words) > 1:
        for count in range(len(words), 0, -1):
            candidate = " ".join(words[:count]).rstrip()
            if not candidate:
                continue
            candidate = candidate + ellipsis
            if measure_fn(candidate) <= max_width_pt:
                return candidate

    low, high = 0, len(value)
    best = ellipsis
    while low <= high:
        mid = (low + high) // 2
        candidate = value[:mid].rstrip() + ellipsis
        if measure_fn(candidate) <= max_width_pt:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best

def _split_wrap_units(text: str) -> list[str]:
    text = str(text or "")
    if not text:
        return []

    parts = re.findall(r"\S+|\s+", text)
    units: list[str] = []
    break_after = {"/", "\\", "|", ",", ";", ":", "-", "_", ")"}
    break_before = {"(", "[", "{", "#"}

    for part in parts:
        if not part:
            continue
        if part.isspace():
            continue

        token = ""
        for ch in part:
            if ch in break_before and token:
                units.append(token)
                token = ch
                continue

            token += ch
            if ch in break_after:
                units.append(token)
                token = ""

        if token:
            units.append(token)

    return units

def _rebalance_wrapped_lines(lines: list[str], max_width_pt: float, measure_fn) -> list[str]:
    if len(lines) < 2:
        return lines

    updated = list(lines)
    prev_line = updated[-2].strip()
    last_line = updated[-1].strip()
    if not prev_line or not last_line:
        return updated

    prev_parts = prev_line.split()
    last_parts = last_line.split()
    if len(prev_parts) < 2 or len(last_parts) != 1:
        return updated

    moved = prev_parts[-1]
    new_prev = " ".join(prev_parts[:-1]).strip()
    new_last = f"{moved} {last_line}".strip()
    if not new_prev:
        return updated
    if measure_fn(new_prev) > max_width_pt or measure_fn(new_last) > max_width_pt:
        return updated

    updated[-2] = new_prev
    updated[-1] = new_last
    return updated

def _wrap_text_by_width(text: str, max_width_pt: float, measure_fn) -> list[str]:
    raw_text = str(text or "")
    paragraphs = [segment for segment in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if segment.strip()]
    if not paragraphs:
        paragraphs = [_normalize_wrap_text(raw_text)]
    wrapped_lines: list[str] = []

    for paragraph in paragraphs:
        lines = _wrap_text_by_width_single(_normalize_wrap_text(paragraph), max_width_pt, measure_fn)
        wrapped_lines.extend(lines)

    return wrapped_lines or [""]

def _wrap_text_by_width_single(text: str, max_width_pt: float, measure_fn) -> list[str]:
    text = _normalize_wrap_text(text)
    if not text:
        return [""]

    if max_width_pt <= 1:
        return [text]

    words = _split_wrap_units(text)
    lines: list[str] = []
    current = ""

    def flush_current():
        nonlocal current
        if current:
            lines.append(current)
            current = ""

    for word in words:
        if not word:
            continue
        candidate = f"{current} {word}".strip() if current else word
        if measure_fn(candidate) <= max_width_pt:
            current = candidate
            continue

        if current:
            flush_current()

        if measure_fn(word) <= max_width_pt:
            current = word
            continue

        # Hard-break a single overlong token.
        chunk = ""
        for ch in word:
            test_chunk = chunk + ch
            if chunk and measure_fn(test_chunk) > max_width_pt:
                lines.append(chunk)
                chunk = ch
            else:
                chunk = test_chunk
        current = chunk

    flush_current()
    return _rebalance_wrapped_lines(lines or [text], max_width_pt, measure_fn)

def _fit_wrapped_text(
    text: str,
    *,
    font_name: str,
    start_size_pt: float,
    min_size_pt: float,
    max_width_pt: float,
    max_lines: int,
    max_height_pt: float | None = None,
    line_height_factor: float = 1.15,
    measure_builder=None,
) -> tuple[float, list[str]]:
    text = _normalize_wrap_text(text)
    if not text:
        return float(start_size_pt), [""]

    max_lines = max(1, int(max_lines or 1))
    min_size_pt = float(min_size_pt)
    start_size_pt = max(min_size_pt, float(start_size_pt))
    line_height_factor = max(1.0, float(line_height_factor or 1.15))
    max_height_pt = float(max_height_pt) if max_height_pt else None
    if measure_builder is None:
        measure_builder = lambda size_pt: (lambda s, _size=size_pt: _measure_vector_text_width(s, font_name, _size))

    def _effective_max_lines(size_pt: float) -> int:
        allowed = max_lines
        if max_height_pt:
            line_height_pt = max(size_pt * line_height_factor, 0.1)
            allowed = min(allowed, max(1, int(max_height_pt / line_height_pt)))
        return max(1, allowed)

    def _fits(size_pt: float) -> tuple[bool, list[str]]:
        measure_fn = measure_builder(size_pt)
        lines = _wrap_text_by_width(text, max_width_pt, measure_fn)
        allowed_lines = _effective_max_lines(size_pt)
        fits_width = all(measure_fn(line) <= max_width_pt for line in lines)
        fits_height = len(lines) <= allowed_lines
        return fits_width and fits_height, lines

    step = 0.25
    sizes: list[float] = []
    curr_size = min_size_pt
    while curr_size <= start_size_pt + 0.0001:
        sizes.append(round(curr_size, 4))
        curr_size += step

    low = 0
    high = len(sizes) - 1
    best_index = 0
    best_lines = [text]

    while low <= high:
        mid = (low + high) // 2
        size_pt = sizes[mid]
        fits, lines = _fits(size_pt)
        if fits:
            best_index = mid
            best_lines = lines
            low = mid + 1
        else:
            high = mid - 1

    best_size = sizes[best_index]
    best_measure = measure_builder(best_size)
    best_lines = _wrap_text_by_width(text, max_width_pt, best_measure)
    best_allowed_lines = _effective_max_lines(best_size)
    if len(best_lines) <= best_allowed_lines and all(best_measure(line) <= max_width_pt for line in best_lines):
        return best_size, best_lines

    final_measure = measure_builder(min_size_pt)
    final_lines = _wrap_text_by_width(text, max_width_pt, final_measure)
    final_allowed_lines = _effective_max_lines(min_size_pt)
    if len(final_lines) > final_allowed_lines:
        final_lines = final_lines[:final_allowed_lines]
        final_lines[-1] = _ellipsize_to_width(final_lines[-1], max_width_pt, final_measure)
    else:
        final_lines = [
            _ellipsize_to_width(line, max_width_pt, final_measure) if final_measure(line) > max_width_pt else line
            for line in final_lines
        ]
    return min_size_pt, final_lines


def _is_probably_pdf_source(src: str, content_type: str | None = None, content: bytes | None = None) -> bool:
    """Best-effort PDF detection for local paths and URLs."""
    src_l = (src or "").strip().lower()
    ct_l = (content_type or "").strip().lower()
    body = content or b""
    if ".pdf" in src_l:
        return True
    if "application/pdf" in ct_l:
        return True
    if body.startswith(b"%PDF-"):
        return True
    return False


def _load_template_for_pdf(path_or_url: str, target_dpi: int, min_size: tuple[int, int] | None = None) -> Image.Image | None:
    """
    Load template at requested DPI for PDF export.
    For PDF templates we render via PyMuPDF at the target DPI.
    """
    if not path_or_url:
        return None
    try:
        src = str(path_or_url)
        is_url = src.startswith(("http://", "https://"))
        content_type = ""
        payload: bytes | None = None

        if is_url:
            resp = requests.get(src, timeout=15)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            payload = resp.content

        if _is_probably_pdf_source(src, content_type=content_type, content=payload):
            if is_url:
                pdf_doc = fitz.open(stream=payload, filetype="pdf")
            else:
                pdf_doc = fitz.open(src)
            try:
                page = pdf_doc[0]
                pix = page.get_pixmap(dpi=max(72, int(target_dpi)), alpha=False)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            finally:
                pdf_doc.close()
        else:
            if is_url and payload is not None:
                img = Image.open(io.BytesIO(payload))
            else:
                img = load_template_smart(src)
            if img.mode in ("RGBA", "LA"):
                rgb = Image.new("RGB", img.size, (255, 255, 255))
                rgb.paste(img, mask=img.split()[-1])
                img = rgb
            elif img.mode != "RGB":
                img = img.convert("RGB")

        if min_size:
            min_w = max(1, int(min_size[0]))
            min_h = max(1, int(min_size[1]))
            if img.size[0] < min_w or img.size[1] < min_h:
                img = img.resize((max(min_w, img.size[0]), max(min_h, img.size[1])), Image.LANCZOS)
        return img
    except Exception as exc:
        logger.warning("Template preload failed for PDF export (%s): %s", path_or_url, exc)
        return None

def _read_template_pdf_bytes(path_or_url: str) -> bytes | None:
    """Return original PDF bytes for a template source, preserving vector content."""
    if not path_or_url:
        return None

    src = str(path_or_url).strip()
    is_url = src.startswith(("http://", "https://"))
    content_type = ""
    payload = b""

    try:
        if is_url:
            resp = requests.get(src, timeout=15)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            payload = resp.content or b""
        else:
            with open(src, "rb") as fh:
                payload = fh.read()

        if not _is_probably_pdf_source(src, content_type=content_type, content=payload):
            return None

        pdf_header_pos = payload.find(b"%PDF")
        if pdf_header_pos < 0:
            return None
        pdf_bytes = payload[pdf_header_pos:]
        if len(pdf_bytes) < 128:
            return None
        return pdf_bytes
    except Exception as exc:
        logger.warning("Failed to read template PDF bytes (%s): %s", path_or_url, exc)
        return None

def _compose_vector_template_export(
    template_pdf_bytes: bytes,
    overlay_pdf_bytes: bytes,
    placements: list[dict],
    sheet_w_pt: float,
    sheet_h_pt: float,
) -> bytes:
    """
    Compose a vector-preserving export by placing the original template PDF page repeatedly
    under the generated overlay PDF pages.
    """
    template_doc = fitz.open(stream=template_pdf_bytes, filetype="pdf")
    overlay_doc = fitz.open(stream=overlay_pdf_bytes, filetype="pdf")
    out_doc = fitz.open()

    try:
        if len(template_doc) < 1:
            raise RuntimeError("Template PDF has no pages")

        placements_by_page: dict[int, list[dict]] = {}
        for item in placements:
            placements_by_page.setdefault(int(item["page_index"]), []).append(item)

        for page_index in range(len(overlay_doc)):
            out_page = out_doc.new_page(width=float(sheet_w_pt), height=float(sheet_h_pt))

            for item in placements_by_page.get(page_index, []):
                rect = fitz.Rect(
                    float(item["x0"]),
                    float(item["y0"]),
                    float(item["x1"]),
                    float(item["y1"]),
                )
                out_page.show_pdf_page(rect, template_doc, 0, keep_proportion=False, overlay=False)

            out_page.show_pdf_page(out_page.rect, overlay_doc, page_index, keep_proportion=False, overlay=True)

        return out_doc.tobytes(garbage=4, deflate=True)
    finally:
        try:
            template_doc.close()
        except Exception:
            pass
        try:
            overlay_doc.close()
        except Exception:
            pass
        try:
            out_doc.close()
        except Exception:
            pass

def _generate_direct_editable_pdf_template_export(
    *,
    template,
    template_id: int,
    students: list,
    template_pdf_bytes: bytes,
    font_settings: dict,
    photo_settings: dict,
    qr_settings: dict,
    layout_config_raw,
    labels_map: dict,
    sheet_w_pt: float,
    sheet_h_pt: float,
    card_w_pt: float,
    card_h_pt: float,
    start_x_pt: float,
    start_y_pt: float,
    gap_pt: float,
    cols: int,
    rows: int,
    card_w_px: int,
    card_h_px: int,
    lang: str,
    direction: str,
    reg_font_name: str,
    bold_font_name: str,
    reg_font_path: str | None,
    bold_font_path: str | None,
) -> bytes:
    template_doc = fitz.open(stream=template_pdf_bytes, filetype="pdf")
    out_doc = fitz.open()

    try:
        if len(template_doc) < 1:
            raise RuntimeError("Template PDF has no pages")

        db_fields = TemplateField.query.filter_by(template_id=template_id).all()
        fitz_reg_font = f"fz_reg_{template_id}"
        fitz_bold_font = f"fz_bold_{template_id}"

        def _fitz_rgb(rgb):
            r, g, b = rgb
            return (
                max(0, min(255, int(r))) / 255.0,
                max(0, min(255, int(g))) / 255.0,
                max(0, min(255, int(b))) / 255.0,
            )

        def _load_student_photo_stream(student, target_w_px: int, target_h_px: int):
            photo_bytes_io = None
            has_real_student_photo = False
            generated_card_url = str(getattr(student, "image_url", "") or "").strip()
            photo_url_value = str(getattr(student, "photo_url", "") or "").strip()
            photo_filename_value = str(getattr(student, "photo_filename", "") or "").strip()

            if photo_url_value and generated_card_url and photo_url_value == generated_card_url:
                photo_url_value = ""
            if photo_filename_value.startswith("http") and generated_card_url and photo_filename_value == generated_card_url:
                photo_filename_value = ""

            if photo_url_value:
                try:
                    processed_url = get_cloudinary_face_crop_url(photo_url_value, target_w_px, target_h_px) or photo_url_value
                    resp = requests.get(processed_url, timeout=10)
                    if resp.status_code != 200 and processed_url != photo_url_value:
                        resp = requests.get(photo_url_value, timeout=10)
                    if resp.status_code == 200:
                        photo_bytes_io = io.BytesIO(resp.content)
                        has_real_student_photo = True
                except Exception:
                    photo_bytes_io = None

            if photo_bytes_io is None and photo_filename_value:
                if photo_filename_value.startswith("http"):
                    try:
                        resp = requests.get(photo_filename_value, timeout=10)
                        if resp.status_code == 200:
                            photo_bytes_io = io.BytesIO(resp.content)
                            has_real_student_photo = True
                    except Exception:
                        photo_bytes_io = None
                else:
                    p_path = os.path.join(UPLOAD_FOLDER, photo_filename_value)
                    if os.path.exists(p_path):
                        with open(p_path, "rb") as fh:
                            photo_bytes_io = io.BytesIO(fh.read())
                        has_real_student_photo = True

            if photo_bytes_io is None and os.path.exists(PLACEHOLDER_PATH):
                try:
                    with open(PLACEHOLDER_PATH, "rb") as fh:
                        photo_bytes_io = io.BytesIO(fh.read())
                except Exception:
                    photo_bytes_io = None

            return photo_bytes_io, has_real_student_photo

        def _prepare_box_image(photo_bytes_io, target_w_px: int, target_h_px: int) -> bytes | None:
            if photo_bytes_io is None:
                return None
            try:
                photo_bytes_io.seek(0)
                img = Image.open(photo_bytes_io)
                img = ImageOps.exif_transpose(img)
                img = img.convert("RGB")
                img = ImageOps.fit(
                    img,
                    (max(1, int(target_w_px)), max(1, int(target_h_px))),
                    method=Image.LANCZOS,
                    centering=(0.5, 0.35),
                )
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=95)
                return out.getvalue()
            except Exception:
                return None

        def _rounded_clip_stream(image_name: str, rect: fitz.Rect, page_height: float, radii: list[float], box_w_px: int, box_h_px: int) -> bytes:
            k = 0.5522847498307936
            tl, tr, br, bl = [max(0.0, float(r or 0.0)) for r in radii]
            rx_tl = min(tl / max(1.0, float(box_w_px)), 0.5)
            ry_tl = min(tl / max(1.0, float(box_h_px)), 0.5)
            rx_tr = min(tr / max(1.0, float(box_w_px)), 0.5)
            ry_tr = min(tr / max(1.0, float(box_h_px)), 0.5)
            rx_br = min(br / max(1.0, float(box_w_px)), 0.5)
            ry_br = min(br / max(1.0, float(box_h_px)), 0.5)
            rx_bl = min(bl / max(1.0, float(box_w_px)), 0.5)
            ry_bl = min(bl / max(1.0, float(box_h_px)), 0.5)

            def fmt(v: float) -> str:
                return f"{v:.6f}".rstrip("0").rstrip(".") or "0"

            parts = [
                "q",
                f"{fmt(rect.width)} 0 0 {fmt(rect.height)} {fmt(rect.x0)} {fmt(page_height - rect.y1)} cm",
                f"{fmt(rx_tl)} 1 m",
                f"{fmt(1 - rx_tr)} 1 l",
            ]
            if rx_tr > 0 or ry_tr > 0:
                parts.append(
                    f"{fmt(1 - rx_tr + rx_tr * k)} 1 {fmt(1)} {fmt(1 - ry_tr + ry_tr * k)} {fmt(1)} {fmt(1 - ry_tr)} c"
                )
            else:
                parts.append("1 1 l")
            parts.append(f"1 {fmt(ry_br)} l")
            if rx_br > 0 or ry_br > 0:
                parts.append(
                    f"{fmt(1)} {fmt(ry_br - ry_br * k)} {fmt(1 - rx_br + rx_br * k)} 0 {fmt(1 - rx_br)} 0 c"
                )
            else:
                parts.append("1 0 l")
            parts.append(f"{fmt(rx_bl)} 0 l")
            if rx_bl > 0 or ry_bl > 0:
                parts.append(
                    f"{fmt(rx_bl - rx_bl * k)} 0 0 {fmt(ry_bl - ry_bl * k)} 0 {fmt(ry_bl)} c"
                )
            else:
                parts.append("0 0 l")
            parts.append(f"0 {fmt(1 - ry_tl)} l")
            if rx_tl > 0 or ry_tl > 0:
                parts.append(
                    f"0 {fmt(1 - ry_tl + ry_tl * k)} {fmt(rx_tl - rx_tl * k)} 1 {fmt(rx_tl)} 1 c"
                )
            else:
                parts.append("0 1 l")
            parts.extend(["h", "W n", f"/{image_name} Do", "Q", ""])
            return "\n".join(parts).encode("ascii")

        def _apply_rounded_image_clip(
            page: fitz.Page,
            image_xref: int,
            rect: fitz.Rect,
            radii: list[float],
            box_w_px: int,
            box_h_px: int,
        ) -> None:
            if not any(float(r or 0) > 0 for r in radii):
                return
            try:
                images = page.get_images(full=True)
                image_name = None
                for img in images:
                    if img and img[0] == image_xref:
                        image_name = img[7]
                        break
                if not image_name:
                    return

                content_xrefs = list(page.get_contents() or [])
                if not content_xrefs:
                    return

                clip_stream = _rounded_clip_stream(
                    image_name,
                    rect,
                    float(page.rect.height),
                    radii,
                    box_w_px,
                    box_h_px,
                )
                page.parent.update_stream(content_xrefs[-1], clip_stream, compress=0)
            except Exception:
                logger.exception("Failed to apply rounded clip to editable PDF photo")

        def _draw_rounded_photo_frame(
            page: fitz.Page,
            rect: fitz.Rect,
            radii: list[float],
            box_w_px: int,
            box_h_px: int,
            stroke_width: float,
            color: tuple[float, float, float],
        ) -> None:
            tl, tr, br, bl = [max(0.0, float(r or 0.0)) for r in radii]
            if not any(v > 0 for v in (tl, tr, br, bl)):
                page.draw_rect(rect, color=color, width=stroke_width, overlay=True)
                return

            k = 0.5522847498307936
            rx_tl = min((tl / max(1.0, float(box_w_px))) * rect.width, rect.width / 2.0)
            ry_tl = min((tl / max(1.0, float(box_h_px))) * rect.height, rect.height / 2.0)
            rx_tr = min((tr / max(1.0, float(box_w_px))) * rect.width, rect.width / 2.0)
            ry_tr = min((tr / max(1.0, float(box_h_px))) * rect.height, rect.height / 2.0)
            rx_br = min((br / max(1.0, float(box_w_px))) * rect.width, rect.width / 2.0)
            ry_br = min((br / max(1.0, float(box_h_px))) * rect.height, rect.height / 2.0)
            rx_bl = min((bl / max(1.0, float(box_w_px))) * rect.width, rect.width / 2.0)
            ry_bl = min((bl / max(1.0, float(box_h_px))) * rect.height, rect.height / 2.0)

            x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
            shape = page.new_shape()
            shape.draw_line((x0 + rx_tl, y0), (x1 - rx_tr, y0))
            if rx_tr > 0 or ry_tr > 0:
                shape.draw_bezier(
                    (x1 - rx_tr, y0),
                    (x1 - rx_tr + rx_tr * k, y0),
                    (x1, y0 + ry_tr - ry_tr * k),
                    (x1, y0 + ry_tr),
                )
            else:
                shape.draw_line((x1, y0), (x1, y0))

            shape.draw_line((x1, y0 + ry_tr), (x1, y1 - ry_br))
            if rx_br > 0 or ry_br > 0:
                shape.draw_bezier(
                    (x1, y1 - ry_br),
                    (x1, y1 - ry_br + ry_br * k),
                    (x1 - rx_br + rx_br * k, y1),
                    (x1 - rx_br, y1),
                )
            else:
                shape.draw_line((x1, y1), (x1, y1))

            shape.draw_line((x1 - rx_br, y1), (x0 + rx_bl, y1))
            if rx_bl > 0 or ry_bl > 0:
                shape.draw_bezier(
                    (x0 + rx_bl, y1),
                    (x0 + rx_bl - rx_bl * k, y1),
                    (x0, y1 - ry_bl + ry_bl * k),
                    (x0, y1 - ry_bl),
                )
            else:
                shape.draw_line((x0, y1), (x0, y1))

            shape.draw_line((x0, y1 - ry_bl), (x0, y0 + ry_tl))
            if rx_tl > 0 or ry_tl > 0:
                shape.draw_bezier(
                    (x0, y0 + ry_tl),
                    (x0, y0 + ry_tl - ry_tl * k),
                    (x0 + rx_tl - rx_tl * k, y0),
                    (x0 + rx_tl, y0),
                )
            else:
                shape.draw_line((x0, y0), (x0, y0))

            shape.finish(
                width=stroke_width,
                color=color,
                fill=None,
                closePath=True,
            )
            shape.commit(overlay=True)

        page = None
        x_scale = float(card_w_pt) / max(1.0, float(card_w_px))
        y_scale = float(card_h_pt) / max(1.0, float(card_h_px))
        text_scale = min(x_scale, y_scale)

        cards_per_sheet = max(1, int(cols) * int(rows))
        for idx, student in enumerate(students):
            idx_on_sheet = idx % cards_per_sheet
            col_idx = idx_on_sheet % cols
            row_idx = idx_on_sheet // cols

            if idx_on_sheet == 0:
                page = out_doc.new_page(width=float(sheet_w_pt), height=float(sheet_h_pt))

            card_x = start_x_pt + (col_idx * (card_w_pt + gap_pt))
            card_top_y = start_y_pt - (row_idx * (card_h_pt + gap_pt))
            card_bottom_y = card_top_y - card_h_pt
            slot_top = float(sheet_h_pt - card_top_y)
            slot_bottom = float(sheet_h_pt - card_bottom_y)
            card_rect = fitz.Rect(float(card_x), slot_top, float(card_x + card_w_pt), slot_bottom)

            page.show_pdf_page(card_rect, template_doc, 0, keep_proportion=False, overlay=False)
            page_w_pt = float(page.rect.width)
            text_scale = min(x_scale, y_scale)

            # Keep editable PDF-template exports on Corel-safer base PDF fonts.
            page_reg_font = "helv"
            page_bold_font = "hebo"

            label_default_rgb = tuple(font_settings.get("label_font_color", [0, 0, 0]))
            value_default_rgb = tuple(font_settings.get("value_font_color", [0, 0, 0]))
            colon_default_rgb = tuple(font_settings.get("colon_font_color", list(label_default_rgb)))
            text_case = font_settings.get("text_case", "normal")
            show_label_colon = bool(font_settings.get("show_label_colon", True))
            align_label_colon = bool(font_settings.get("align_label_colon", True))
            label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)

            try:
                px_px = photo_settings.get("photo_x", 0)
                py_px = photo_settings.get("photo_y", 0)
                pw_px = photo_settings.get("photo_width", 100)
                ph_px = photo_settings.get("photo_height", 100)
                photo_rect = fitz.Rect(
                    float(card_x) + (float(px_px) * x_scale),
                    slot_top + (float(py_px) * y_scale),
                    float(card_x) + (float(px_px + pw_px) * x_scale),
                    slot_top + (float(py_px + ph_px) * y_scale),
                )
                radii = [
                    photo_settings.get("photo_border_top_left", 0),
                    photo_settings.get("photo_border_top_right", 0),
                    photo_settings.get("photo_border_bottom_right", 0),
                    photo_settings.get("photo_border_bottom_left", 0),
                ]
                editable_photo_mode = _corel_editable_photo_mode(photo_settings)
                draw_editable_photo_frame = editable_photo_mode == "frame_only"
                photo_bytes_io, has_real_student_photo = _load_student_photo_stream(student, pw_px, ph_px)
                if photo_bytes_io and (has_real_student_photo or not draw_editable_photo_frame):
                    prepared_photo = _prepare_box_image(photo_bytes_io, pw_px, ph_px)
                    if prepared_photo:
                        before_contents = list(page.get_contents() or [])
                        image_xref = page.insert_image(
                            photo_rect,
                            stream=prepared_photo,
                            overlay=True,
                            keep_proportion=False,
                        )
                        after_contents = list(page.get_contents() or [])
                        if len(after_contents) > len(before_contents):
                            _apply_rounded_image_clip(
                                page,
                                image_xref,
                                photo_rect,
                                radii,
                                pw_px,
                                ph_px,
                            )
                if draw_editable_photo_frame:
                    try:
                        _draw_rounded_photo_frame(
                            page,
                            photo_rect,
                            radii,
                            pw_px,
                            ph_px,
                            max(0.8, 1.2 * text_scale),
                            (0.55, 0.14, 0.24),
                        )
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                form_data = {
                    "name": student.name,
                    "father_name": student.father_name,
                    "class_name": student.class_name,
                    "dob": student.dob,
                    "address": student.address,
                    "phone": student.phone,
                }
                photo_ref = getattr(student, "photo_url", None) or getattr(student, "photo_filename", None) or ""
                data_hash = generate_data_hash(form_data, photo_ref)
                qr_id = data_hash[:10]

                if bool(qr_settings.get("enable_qr", False)):
                    qr_type = qr_settings.get("qr_data_type", "student_id")
                    if qr_type == "url":
                        base = qr_settings.get("qr_base_url", "")
                        if base and not base.endswith("/"):
                            base += "/"
                        qr_payload = base + qr_id
                    elif qr_type == "text":
                        qr_payload = qr_settings.get("qr_custom_text", "Sample")
                    elif qr_type == "json":
                        qr_payload = json.dumps({
                            "student_id": qr_id,
                            "name": student.name,
                            "class": student.class_name,
                            "school_name": template.school_name,
                        })
                    else:
                        qr_payload = qr_id

                    size_px = max(40, int(qr_settings.get("qr_size", 120)))
                    q_x_px = int(qr_settings.get("qr_x", 50))
                    q_y_px = int(qr_settings.get("qr_y", 50))
                    qr_rect = fitz.Rect(
                        float(card_x) + (float(q_x_px) * x_scale),
                        slot_top + (float(q_y_px) * y_scale),
                        float(card_x) + (float(q_x_px + size_px) * x_scale),
                        slot_top + (float(q_y_px + size_px) * y_scale),
                    )
                    qr_img = generate_qr_code(qr_payload, qr_settings, max(40, size_px)).convert("RGB")
                    qr_buf = io.BytesIO()
                    qr_img.save(qr_buf, format="PNG")
                    page.insert_image(qr_rect, stream=qr_buf.getvalue(), overlay=True, keep_proportion=False)

                if bool(qr_settings.get("enable_barcode", False)):
                    barcode_type = qr_settings.get("barcode_data_type", "student_id")
                    if barcode_type == "url":
                        base = qr_settings.get("barcode_base_url", "")
                        if base and not base.endswith("/"):
                            base += "/"
                        barcode_payload = base + qr_id
                    elif barcode_type == "text":
                        barcode_payload = qr_settings.get("barcode_custom_text", "Sample")
                    elif barcode_type == "json":
                        barcode_payload = json.dumps({
                            "student_id": qr_id,
                            "name": student.name,
                            "class": student.class_name,
                            "school_name": template.school_name,
                        })
                    else:
                        barcode_payload = qr_id

                    barcode_w_px = max(40, int(qr_settings.get("barcode_width", 220)))
                    barcode_h_px = max(30, int(qr_settings.get("barcode_height", 70)))
                    barcode_x_px = int(qr_settings.get("barcode_x", 50))
                    barcode_y_px = int(qr_settings.get("barcode_y", 200))
                    barcode_rect = fitz.Rect(
                        float(card_x) + (float(barcode_x_px) * x_scale),
                        slot_top + (float(barcode_y_px) * y_scale),
                        float(card_x) + (float(barcode_x_px + barcode_w_px) * x_scale),
                        slot_top + (float(barcode_y_px + barcode_h_px) * y_scale),
                    )
                    barcode_img = generate_barcode_code128(
                        barcode_payload,
                        qr_settings,
                        width=barcode_w_px,
                        height=barcode_h_px,
                    ).convert("RGB")
                    barcode_buf = io.BytesIO()
                    barcode_img.save(barcode_buf, format="PNG")
                    page.insert_image(barcode_rect, stream=barcode_buf.getvalue(), overlay=True, keep_proportion=False)
            except Exception:
                pass

            fields = [
                {"k": "NAME", "l": local_apply_text_case(labels_map["NAME"], text_case), "v": local_apply_text_case(student.name, text_case), "ord": 10},
                {"k": "F_NAME", "l": local_apply_text_case(labels_map["F_NAME"], text_case), "v": local_apply_text_case(student.father_name, text_case), "ord": 20},
                {"k": "CLASS", "l": local_apply_text_case(labels_map["CLASS"], text_case), "v": local_apply_text_case(student.class_name, text_case), "ord": 30},
                {"k": "DOB", "l": local_apply_text_case(labels_map["DOB"], text_case), "v": local_apply_text_case(student.dob, text_case), "ord": 40},
                {"k": "MOBILE", "l": local_apply_text_case(labels_map["MOBILE"], text_case), "v": local_apply_text_case(student.phone, text_case), "ord": 50},
                {"k": "ADDRESS", "l": local_apply_text_case(labels_map["ADDRESS"], text_case), "v": local_apply_text_case(student.address, text_case), "ord": 60},
            ]
            if student.custom_data:
                for f in db_fields:
                    fields.append(
                        {
                            "k": f.field_name,
                            "l": local_apply_text_case(f.field_label, text_case),
                            "v": local_apply_text_case(student.custom_data.get(f.field_name, ""), text_case),
                            "ord": f.display_order,
                        }
                    )
            fields.sort(key=lambda x: x["ord"])

            start_y_text_px = font_settings.get("start_y", 200)
            label_x_px = font_settings.get("label_x", 50)
            value_x_px = font_settings.get("value_x", 250)
            current_y_px = start_y_text_px
            line_height_px = font_settings.get("line_height", 50)
            p_x_px = photo_settings.get("photo_x", 0)
            p_y_px = photo_settings.get("photo_y", 0)
            p_h_px = photo_settings.get("photo_height", 0)
            p_bottom_px = p_y_px + p_h_px

            for field in fields:
                field_key = _field_key_from_item(field)
                layout_item = get_field_layout_item(
                    layout_config_raw,
                    field_key,
                    label_x_px,
                    value_x_px,
                    current_y_px,
                    text_direction=direction,
                )
                label_x_eff = layout_item["label_x"]
                value_x_eff = layout_item["value_x"]
                label_y_eff = layout_item["label_y"]
                value_y_eff = layout_item["value_y"]
                label_visible = layout_item["label_visible"]
                value_visible = layout_item["value_visible"]
                label_grow = layout_item.get("label_grow")
                value_grow = layout_item.get("value_grow")
                label_rgb = layout_item.get("label_color") or label_default_rgb
                value_rgb = layout_item.get("value_color") or value_default_rgb
                label_size_px_eff = max(1, int(layout_item.get("label_font_size") or font_settings.get("label_font_size", 40)))
                value_size_px_eff = max(1, int(layout_item.get("value_font_size") or font_settings.get("value_font_size", 36)))
                lbl_size_pt_eff = label_size_px_eff * text_scale
                val_size_pt_eff = value_size_px_eff * text_scale

                current_y_px = max(int(current_y_px), int(label_y_eff), int(value_y_eff))

                if label_visible:
                    label_text, colon_text = split_label_and_colon(
                        process_text_for_vector(field["l"], lang),
                        lang,
                        direction,
                        include_colon=show_label_colon,
                        align_colon=align_label_colon,
                    )
                    baseline_y_pt = (label_y_eff * y_scale) + lbl_size_pt_eff
                    if label_text:
                        label_x = _x_for_direction(
                            float(card_x),
                            float(card_w_pt),
                            label_x_eff,
                            label_text,
                            bold_font_name,
                            lbl_size_pt_eff,
                            x_scale,
                            direction,
                            grow_mode=label_grow,
                        )
                        page.insert_text(
                            fitz.Point(label_x, slot_top + baseline_y_pt),
                            label_text,
                            fontsize=lbl_size_pt_eff,
                            fontname=page_bold_font,
                            color=_fitz_rgb(label_rgb),
                            overlay=True,
                        )
                    if colon_text:
                        colon_anchor_px, colon_grow = colon_anchor_for_value(value_x_eff, direction, gap_px=label_colon_gap)
                        colon_x = _x_for_direction(
                            float(card_x),
                            float(card_w_pt),
                            colon_anchor_px,
                            colon_text,
                            bold_font_name,
                            lbl_size_pt_eff,
                            x_scale,
                            direction,
                            grow_mode=colon_grow,
                        )
                        page.insert_text(
                            fitz.Point(colon_x, slot_top + baseline_y_pt),
                            colon_text,
                            fontsize=lbl_size_pt_eff,
                            fontname=page_bold_font,
                            color=_fitz_rgb(colon_default_rgb),
                            overlay=True,
                        )

                val_text = process_text_for_vector(field["v"], lang)
                if field.get("k") == "ADDRESS" and text_case == "normal" and val_text and val_text.isupper() and len(val_text) > 10:
                    val_text = val_text.title()

                is_vertically_overlapping = (value_y_eff < p_bottom_px) and ((value_y_eff + line_height_px) > p_y_px)
                if direction == "rtl":
                    anchor_px = card_w_px - value_x_eff
                    left_limit_px = 20
                    if is_vertically_overlapping:
                        photo_right_px = p_x_px + photo_settings.get("photo_width", 0)
                        left_limit_px = max(left_limit_px, photo_right_px + 15)
                    max_w_px = max(50, anchor_px - left_limit_px)
                else:
                    if is_vertically_overlapping and (p_x_px > value_x_eff):
                        max_w_px = p_x_px - value_x_eff - 15
                    else:
                        max_w_px = card_w_px - value_x_eff - 20

                max_width_pt = max_w_px * x_scale
                remaining_h_px = max(1.0, float(card_h_px - 20) - float(value_y_eff))
                remaining_h_pt = max(text_scale, remaining_h_px * y_scale)
                wrap_policy = _field_wrap_policy(field_key)
                line_height_factor = float(wrap_policy.get("line_height_factor", 1.15))
                min_font_size_pt = max(8 * text_scale, val_size_pt_eff * float(wrap_policy.get("min_scale", 0.78)))
                field_max_lines = max(
                    1,
                    min(
                        int(wrap_policy.get("max_lines", 3)),
                        int(remaining_h_pt / max(min_font_size_pt * line_height_factor, text_scale)),
                    ),
                )
                curr_font_size, lines = _fit_wrapped_text(
                    val_text,
                    font_name=reg_font_name,
                    start_size_pt=val_size_pt_eff,
                    min_size_pt=min_font_size_pt,
                    max_width_pt=max_width_pt,
                    max_lines=field_max_lines,
                    max_height_pt=remaining_h_pt,
                    line_height_factor=line_height_factor,
                )
                line_spacing = curr_font_size * line_height_factor

                for i, line in enumerate(lines):
                    if not value_visible:
                        continue
                    baseline_y_pt = (value_y_eff * y_scale) + curr_font_size + (i * line_spacing)
                    vx = _x_for_direction(
                        float(card_x),
                        float(card_w_pt),
                        value_x_eff,
                        line,
                        reg_font_name,
                        curr_font_size,
                        x_scale,
                        direction,
                        grow_mode=value_grow,
                    )
                    page.insert_text(
                        fitz.Point(vx, slot_top + baseline_y_pt),
                        line,
                        fontsize=curr_font_size,
                        fontname=page_reg_font,
                        color=_fitz_rgb(value_rgb),
                        overlay=True,
                    )

                if len(lines) > 1:
                    extra_h_px = ((len(lines) - 1) * line_spacing) / max(y_scale, 0.001)
                    current_y_px += extra_h_px
                current_y_px += line_height_px

        return out_doc.tobytes(
            garbage=0,
            clean=False,
            deflate=False,
            deflate_images=False,
            deflate_fonts=False,
            linear=False,
            pretty=False,
            use_objstms=0,
        )
    finally:
        try:
            template_doc.close()
        except Exception:
            pass
        try:
            out_doc.close()
        except Exception:
            pass


def _draw_vector_qr(c, payload: str, x: float, y: float, width: float, height: float, fill_color: Color):
    """Draw vector QR for editable PDFs."""
    qr_widget = rl_qr.QrCodeWidget(str(payload or ""))
    qr_widget.barFillColor = fill_color
    bounds = qr_widget.getBounds()
    bw = max(1e-6, bounds[2] - bounds[0])
    bh = max(1e-6, bounds[3] - bounds[1])
    sx = width / bw
    sy = height / bh
    drawing = Drawing(width, height, transform=[sx, 0, 0, sy, -bounds[0] * sx, -bounds[1] * sy])
    drawing.add(qr_widget)
    renderPDF.draw(drawing, c, x, y)


def _draw_vector_barcode(c, payload: str, x: float, y: float, width: float, height: float, fill_color: Color):
    """Draw vector Code128 barcode for editable PDFs."""
    value = str(payload or "")
    drawing = createBarcodeDrawing(
        "Code128",
        value=value,
        barHeight=max(1.0, float(height)),
        humanReadable=False,
        barFillColor=fill_color,
    )
    bw = max(1e-6, float(getattr(drawing, "width", 1.0)))
    bh = max(1e-6, float(getattr(drawing, "height", 1.0)))
    c.saveState()
    c.translate(x, y)
    c.scale(float(width) / bw, float(height) / bh)
    renderPDF.draw(drawing, c, 0, 0)
    c.restoreState()


def _queue_hb_run(
    runs: list[dict],
    *,
    page_index: int,
    card_x: float,
    card_w_pt: float,
    card_bottom_y: float,
    card_h_pt: float,
    x_px: float,
    y_px: float,
    max_w_pt: float,
    box_h_pt: float,
    scale: float,
    direction: str,
    text: str,
    font_file: str,
    font_size_pt: float,
    color_rgb: tuple[int, int, int],
):
    """Queue a HarfBuzz/Pango text run for post-render PDF overlay."""
    text = "" if text is None else str(text)
    if not text.strip():
        return

    direction = (direction or "ltr").strip().lower()
    max_w_pt = max(8.0, float(max_w_pt))
    box_h_pt = max(float(font_size_pt) * 1.2, float(box_h_pt))

    if direction == "rtl":
        anchor_x = card_x + (card_w_pt - (float(x_px) * scale))
        x1 = max(card_x + (2 * scale), anchor_x)
        x0 = max(card_x + (2 * scale), x1 - max_w_pt)
        align = "right"
    else:
        x0 = card_x + (float(x_px) * scale)
        x1 = min(card_x + card_w_pt - (2 * scale), x0 + max_w_pt)
        align = "left"

    y_top_bottom_space = card_bottom_y + card_h_pt - (float(y_px) * scale)
    y1_bottom = max(card_bottom_y + 1.0, y_top_bottom_space)
    y0_bottom = max(card_bottom_y + 0.5, y1_bottom - box_h_pt)

    runs.append(
        {
            "page_index": int(page_index),
            "x0": float(x0),
            "x1": float(max(x1, x0 + 2.0)),
            "y0_bottom": float(y0_bottom),
            "y1_bottom": float(max(y1_bottom, y0_bottom + 2.0)),
            "text": text,
            "font_file": (font_file or "").strip(),
            "font_size_pt": float(max(1.0, font_size_pt)),
            "color_rgb": tuple(int(max(0, min(255, c))) for c in (color_rgb or (0, 0, 0))),
            "direction": direction,
            "align": align,
        }
    )


def _apply_hb_text_overlay(pdf_bytes: bytes, runs: list[dict], page_height_pt: float) -> bytes:
    """
    Overlay shaped text runs using PyMuPDF HTML engine (HarfBuzz-backed).
    This keeps Unicode text objects in the output PDF instead of rasterized text.
    """
    if not runs:
        return pdf_bytes

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    archive = fitz.Archive(FONTS_FOLDER)
    css_cache: dict[str, tuple[str, str]] = {}

    for run in runs:
        page_index = int(run.get("page_index", 0))
        if page_index < 0 or page_index >= len(doc):
            continue
        page = doc[page_index]

        y0_top = float(page_height_pt) - float(run["y1_bottom"])
        y1_top = float(page_height_pt) - float(run["y0_bottom"])
        rect = fitz.Rect(float(run["x0"]), y0_top, float(run["x1"]), y1_top)
        if rect.width < 1 or rect.height < 1:
            continue

        font_file = run.get("font_file", "")
        font_basename = os.path.basename(font_file) if font_file else ""

        if font_basename in css_cache:
            font_family, css = css_cache[font_basename]
        else:
            if font_basename and os.path.exists(os.path.join(FONTS_FOLDER, font_basename)):
                safe_name = f"hb_{abs(hash(font_basename))}"
                font_family = safe_name
                css = (
                    f"@font-face {{ font-family: '{safe_name}'; src: url('{font_basename}'); }}\n"
                    "body { margin: 0; padding: 0; }\n"
                )
            else:
                font_family = "sans-serif"
                css = "body { margin: 0; padding: 0; }\n"
            css_cache[font_basename] = (font_family, css)

        r, g, b = run["color_rgb"]
        direction = "rtl" if run.get("direction") == "rtl" else "ltr"
        align = "right" if run.get("align") == "right" else "left"
        font_size_pt = float(run.get("font_size_pt", 10.0))
        text = html.escape(run.get("text", ""))

        html_text = (
            "<div "
            f"style=\"font-family:'{font_family}';"
            f"font-size:{font_size_pt:.2f}pt;"
            "line-height:1.1;"
            f"color:rgb({r},{g},{b});"
            f"direction:{direction};text-align:{align};"
            "white-space:pre-wrap;\">"
            f"{text}</div>"
        )

        try:
            page.insert_htmlbox(rect, html_text, css=css, archive=archive, scale_low=0)
        except Exception as hb_exc:
            logger.warning("HB overlay insert failed (page=%s): %s", page_index, hb_exc)

    out = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return out

@corel_bp.route("/download_compiled_vector_pdf/<int:template_id>")
def download_compiled_vector_pdf(template_id):
    if not session.get("admin"):
        return redirect(url_for("login"))

    try:
        mode = parse_pdf_export_mode(request.args.get("mode") or request.form.get("mode"))
        if mode is None:
            return "Invalid PDF mode. Use `editable` or `print`.", 400
        profile = _render_profile(mode)
        asset_dpi = int(profile["asset_dpi"])
        raster_multiplier = int(profile["raster_multiplier"])
        text_raster_scale = (72.0 / LAYOUT_DPI) / max(1, raster_multiplier)

        # 1. Fetch Data
        template = db.session.get(Template, template_id)
        if not template:
            return "No data found", 404

        # 2. Settings
        font_settings, photo_settings, qr_settings, orientation = get_template_settings(template_id)
        template_path = get_template_path(template_id)
        back_template_path = get_template_path(template_id, side="back") if getattr(template, "is_double_sided", False) else None
        
        buffer = io.BytesIO()
        template_pdf_bytes = _read_template_pdf_bytes(template_path)
        preserve_vector_template = bool(template_pdf_bytes)

        students = Student.query.filter_by(template_id=template_id).all()
        if not students:
            return "No data found", 404
        
        # =========================================================
        # 3. DYNAMIC DIMENSIONS & GRID
        # =========================================================
        # Get Dimensions from DB (Pixels @ 300 DPI)
        sheet_w_px = template.sheet_width if template.sheet_width else 2480
        sheet_h_px = template.sheet_height if template.sheet_height else 3508
        
        card_w_px = template.card_width if template.card_width else 1015
        card_h_px = template.card_height if template.card_height else 661

        # Get Grid Layout from DB
        cols = template.grid_cols if template.grid_cols else 2
        rows = template.grid_rows if template.grid_rows else 5

        # Scale Factor: 300 DPI design pixels -> 72 DPI PDF points.
        scale = 72.0 / LAYOUT_DPI

        if min(sheet_w_px, sheet_h_px, card_w_px, card_h_px, cols, rows) <= 0:
            return "Invalid template dimensions/grid settings. Width/height/rows/cols must be > 0.", 400
        
        sheet_w_pt = sheet_w_px * scale
        sheet_h_pt = sheet_h_px * scale
        card_w_pt = card_w_px * scale
        card_h_pt = card_h_px * scale
        gap_pt = 10 * scale
        
        # Calculate Layout & Centering
        total_grid_w_pt = (cols * card_w_pt) + ((cols - 1) * gap_pt)
        total_grid_h_pt = (rows * card_h_pt) + ((rows - 1) * gap_pt)
        
        start_x_pt = (sheet_w_pt - total_grid_w_pt) / 2
        bottom_margin = (sheet_h_pt - total_grid_h_pt) / 2
        start_y_pt = bottom_margin + total_grid_h_pt

        # PDF 1.4 keeps broad compatibility with CorelDRAW/Acrobat.
        c = canvas.Canvas(
            buffer,
            pagesize=(sheet_w_pt, sheet_h_pt),
            pageCompression=1,
            pdfVersion=(1, 4),
        )

        # --- REGISTER FONTS ---
        # Corel (and some PDF editors) can substitute fonts if the regular font file is missing or
        # un-registerable (e.g., OTF selected). That often looks like "labels OK but values changed".
        # We try hard to register a consistent regular/bold pair, and we avoid Arial for Urdu/Arabic/Hindi.
        reg_font_name = f"Font_{template_id}_Reg"
        bold_font_name = f"Font_{template_id}_Bold"

        lang = _normalize_language(getattr(template, "language", "english"))
        direction = (getattr(template, "text_direction", "ltr") or "ltr").strip().lower()
        use_harfbuzz_overlay = False
        font_reg_file = (font_settings.get("font_regular") or "").strip()
        font_bold_file = (font_settings.get("font_bold") or "").strip()

        # Hindi always needs raster fallback because ReportLab does not shape Devanagari.
        # For editable Corel export with PDF templates, rasterize Urdu/Arabic overlay text too:
        # this keeps the template itself editable while avoiding Corel import issues on complex-script text objects.
        rasterize_complex_text = (
            lang in {"hindi"} or
            (preserve_vector_template and mode == "editable" and lang in {"urdu", "arabic"})
        )
        force_vector_for_language = lang in {"urdu", "arabic"} and not rasterize_complex_text

        def _is_ttf(path: str) -> bool:
            return str(path or "").lower().endswith(".ttf")

        pf_safe_fonts = _presentation_forms_font_fallbacks() if lang in {"urdu", "arabic"} else []
        pf_safe_set = {n.lower() for n in pf_safe_fonts}
        vector_pf_sample = process_text_for_vector("محمد علی", lang) if lang in {"urdu", "arabic"} else ""
        requested_non_pf = (
            lang in {"urdu", "arabic"}
            and (
                (font_reg_file and font_reg_file.lower() not in pf_safe_set)
                or (font_bold_file and font_bold_file.lower() not in pf_safe_set)
            )
        )
        # Urdu/Arabic: never rasterize in this route; always use vector-safe font fallback.
        if requested_non_pf and force_vector_for_language:
            logger.warning(
                "Selected %s font is not Presentation-Forms-safe; forcing vector-safe fallback so text stays editable.",
                lang,
            )
        elif requested_non_pf and not PIL_RAQM_AVAILABLE:
            logger.warning(
                "Selected %s font requires RAQM for correct shaping, but RAQM is unavailable; "
                "falling back to Presentation-Forms-safe vector fonts.",
                lang,
            )

        if lang in {"urdu", "arabic"} and not rasterize_complex_text and not pf_safe_fonts:
            raise RuntimeError(                    
                "Urdu/Arabic vector export requires a Presentation-Forms-compatible `.ttf` in `static/fonts/` "
                "(arabtype.ttf, ARABIAN.TTF, ARABIA.TTF, ARB.TTF)."
            )

        def _existing_font_path(filename: str) -> str | None:
            if not filename:
                return None
            # For Urdu/Arabic vector export we must use a Presentation-Forms-compatible TTF,
            # otherwise shaped text renders as tofu squares (□) in Corel/PDF viewers.
            if (
                lang in {"urdu", "arabic"}
                and not rasterize_complex_text
                and pf_safe_set
                and str(filename).lower() not in pf_safe_set
            ):
                return None
            p = os.path.join(FONTS_FOLDER, filename)
            if not os.path.exists(p):
                return None
            if lang in {"urdu", "arabic"} and not rasterize_complex_text and vector_pf_sample:
                try:
                    if not _font_covers_text(p, vector_pf_sample):
                        return None
                except Exception:
                    return None
            return p

        def _derive_regular_from_bold(bold_filename: str) -> str | None:
            if not bold_filename:
                return None
            base = bold_filename
            for a, b in [
                ("-Bold", "-Regular"),
                ("_Bold", "_Regular"),
                (" Bold", " Regular"),
                ("Bold", "Regular"),
                ("bd", ""),
                ("BD", ""),
            ]:
                cand = base.replace(a, b)
                if cand != base:
                    p = _existing_font_path(cand)
                    if p:
                        return p
            return None

        def _derive_bold_from_regular(reg_filename: str) -> str | None:
            if not reg_filename:
                return None
            base = reg_filename
            for a, b in [
                ("-Regular", "-Bold"),
                ("_Regular", "_Bold"),
                (" Regular", " Bold"),
                ("Regular", "Bold"),
            ]:
                cand = base.replace(a, b)
                if cand != base:
                    p = _existing_font_path(cand)
                    if p:
                        return p
            return None

        # Candidate filenames (ordered)
        if lang in {"urdu", "arabic"} and not rasterize_complex_text:
            fallback_names = pf_safe_fonts
        else:
            fallback_names = _language_font_fallbacks(lang)
        # For English, allow the legacy arial defaults as a last resort.
        if lang not in {"arabic", "urdu", "hindi"}:
            fallback_names = list(dict.fromkeys([*fallback_names, "arial.ttf", "arialbd.ttf"]))

        # Build candidate path lists
        reg_candidates: list[str] = []
        bold_candidates: list[str] = []

        p_reg = _existing_font_path(font_reg_file)
        p_bold = _existing_font_path(font_bold_file)

        if lang in {"urdu", "arabic"} and not rasterize_complex_text:
            if font_reg_file and not p_reg:
                logger.warning(
                    "Ignoring selected regular font '%s' for %s vector export; using Presentation-Forms-safe fallback.",
                    font_reg_file,
                    lang,
                )
            if font_bold_file and not p_bold:
                logger.warning(
                    "Ignoring selected bold font '%s' for %s vector export; using Presentation-Forms-safe fallback.",
                    font_bold_file,
                    lang,
                )

        if p_reg:
            reg_candidates.append(p_reg)
        if p_bold:
            bold_candidates.append(p_bold)

        # If one side is missing, try to derive from the other (keeps family consistent)
        if not reg_candidates and font_bold_file:
            derived = _derive_regular_from_bold(font_bold_file)
            if derived:
                reg_candidates.append(derived)
        if not bold_candidates and font_reg_file:
            derived = _derive_bold_from_regular(font_reg_file)
            if derived:
                bold_candidates.append(derived)

        # Add language fallbacks (TTF only)
        for name in fallback_names:
            p = _existing_font_path(name)
            if p and p not in reg_candidates:
                reg_candidates.append(p)
            if p and p not in bold_candidates:
                bold_candidates.append(p)

        # Final fallback: if no bold candidate, use regular candidate (consistent font)
        if not bold_candidates and reg_candidates:
            bold_candidates = [reg_candidates[0]]
        if not reg_candidates and bold_candidates:
            reg_candidates = [bold_candidates[0]]

        # These are used when we rasterize complex scripts to keep the *exact* same look as the PIL preview.
        # (ReportLab doesn't shape Urdu/Arabic/Hindi; Corel may substitute fonts for missing glyphs.)
        pil_reg_path = p_reg or (reg_candidates[0] if reg_candidates else "")
        pil_bold_path = p_bold or (bold_candidates[0] if bold_candidates else pil_reg_path)
        hb_font_reg_file = os.path.basename(pil_reg_path) if pil_reg_path else os.path.basename(font_reg_file)
        hb_font_bold_file = os.path.basename(pil_bold_path) if pil_bold_path else os.path.basename(font_bold_file)

        def _try_register_pair(reg_path: str | None, bold_path: str | None) -> bool:
            nonlocal reg_font_name, bold_font_name
            if not reg_path or not os.path.exists(reg_path) or not _is_ttf(reg_path):
                return False
            if not bold_path or not os.path.exists(bold_path) or not _is_ttf(bold_path):
                bold_path = reg_path
            try:
                # Register regular
                if reg_font_name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(reg_font_name, reg_path))
                # Register bold (may be same file)
                if bold_font_name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(bold_font_name, bold_path))
                return True
            except Exception:
                return False

        registered = False
        for rp in reg_candidates[:6]:
            for bp in bold_candidates[:6]:
                if _try_register_pair(rp, bp):
                    registered = True
                    break
            if registered:
                break

        if not registered:
            if lang in {"urdu", "arabic"} and not rasterize_complex_text:
                raise RuntimeError(
                    "Urdu/Arabic vector export requires a Presentation-Forms-compatible `.ttf` in `static/fonts/` "
                    "(arabtype.ttf, ARABIAN.TTF, ARABIA.TTF, ARB.TTF)."
                )
            reg_font_name = "Helvetica"
            bold_font_name = "Helvetica-Bold"
            logger.warning(
                "Template %s export mode=%s: selected fonts unavailable, using fallback font pair (%s, %s).",
                template_id,
                mode,
                reg_font_name,
                bold_font_name,
            )

        def _rgb_tuple(color_list, fallback=(0, 0, 0)):
            if not color_list or len(color_list) < 3:
                return fallback
            try:
                r = int(color_list[0])
                g = int(color_list[1])
                b = int(color_list[2])
                return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
            except Exception:
                return fallback

        def _rl_color_from_rgb(rgb):
            r, g, b = rgb
            return Color(r / 255.0, g / 255.0, b / 255.0)

        label_default_rgb = _rgb_tuple(font_settings.get("label_font_color", [0, 0, 0]))
        value_default_rgb = _rgb_tuple(font_settings.get("value_font_color", [0, 0, 0]))
        colon_default_rgb = _rgb_tuple(font_settings.get("colon_font_color", list(label_default_rgb)), fallback=label_default_rgb)
        l_color = _rl_color_from_rgb(label_default_rgb)
        v_color = _rl_color_from_rgb(value_default_rgb)
        layout_config_raw = getattr(template, "layout_config", None)

        lbl_size_pt = font_settings.get('label_font_size', 40) * scale
        val_size_pt = font_settings.get('value_font_size', 36) * scale

        std_labels = {
            'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B.', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
            'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
            'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
            'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
        }
        labels_map = std_labels.get(lang, std_labels['english'])

        if preserve_vector_template and mode == "editable":
            editable_bytes = _generate_direct_editable_pdf_template_export(
                template=template,
                template_id=template_id,
                students=students,
                template_pdf_bytes=template_pdf_bytes,
                font_settings=font_settings,
                photo_settings=photo_settings,
                qr_settings=qr_settings,
                layout_config_raw=layout_config_raw,
                labels_map=labels_map,
                sheet_w_pt=sheet_w_pt,
                sheet_h_pt=sheet_h_pt,
                card_w_pt=card_w_pt,
                card_h_pt=card_h_pt,
                start_x_pt=start_x_pt,
                start_y_pt=start_y_pt,
                gap_pt=gap_pt,
                cols=cols,
                rows=rows,
                card_w_px=card_w_px,
                card_h_px=card_h_px,
                lang=lang,
                direction=direction,
                reg_font_name=reg_font_name,
                bold_font_name=bold_font_name,
                reg_font_path=reg_candidates[0] if reg_candidates else (pil_reg_path or None),
                bold_font_path=bold_candidates[0] if bold_candidates else (pil_bold_path or None),
            )
            buffer = io.BytesIO(editable_bytes)
            buffer.seek(0)
            filename = f"COREL_EDITABLE_{template.school_name}.pdf"
            logger.info(
                "Generated direct editable Corel PDF template_id=%s cards=%s",
                template_id,
                len(students),
            )
            return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')
        
        # 6. Process Loop
        cards_per_sheet = cols * rows
        card_count = 0
        card_warnings: list[dict] = []
        hb_overlay_runs: list[dict] = []
        template_card_placements: list[dict] = []
        
        # PRELOAD BACKGROUND
        bg_image_reader = None
        if template_path and not preserve_vector_template:
            try:
                bg_pil = _load_template_for_pdf(
                    template_path,
                    target_dpi=asset_dpi,
                    min_size=(
                        max(1, int(card_w_px * raster_multiplier)),
                        max(1, int(card_h_px * raster_multiplier)),
                    ),
                )
                if bg_pil is None:
                    raise RuntimeError("Template background failed to load")
                bg_stream = io.BytesIO()
                bg_pil.save(bg_stream, format="PNG")
                bg_stream.seek(0)
                bg_image_reader = ImageReader(bg_stream)
            except Exception as e:
                logger.warning("Background preload error (template_id=%s, mode=%s): %s", template_id, mode, e)

        back_bg_image_reader = None
        if back_template_path:
            try:
                back_bg_pil = _load_template_for_pdf(
                    back_template_path,
                    target_dpi=asset_dpi,
                    min_size=(
                        max(1, int(card_w_px * raster_multiplier)),
                        max(1, int(card_h_px * raster_multiplier)),
                    ),
                )
                if back_bg_pil is None:
                    raise RuntimeError("Back template background failed to load")
                back_bg_stream = io.BytesIO()
                back_bg_pil.save(back_bg_stream, format="PNG")
                back_bg_stream.seek(0)
                back_bg_image_reader = ImageReader(back_bg_stream)
            except Exception as e:
                logger.warning("Back background preload error (template_id=%s, mode=%s): %s", template_id, mode, e)

        for student in students:
            idx_on_sheet = card_count % cards_per_sheet
            col_idx = idx_on_sheet % cols
            row_idx = idx_on_sheet // cols

            # Calculate Card Position
            card_x = start_x_pt + (col_idx * (card_w_pt + gap_pt))
            card_top_y = start_y_pt - (row_idx * (card_h_pt + gap_pt))
            card_bottom_y = card_top_y - card_h_pt

            if preserve_vector_template:
                template_card_placements.append(
                    {
                        "page_index": int(card_count // cards_per_sheet),
                        "x0": float(card_x),
                        "y0": float(sheet_h_pt - card_top_y),
                        "x1": float(card_x + card_w_pt),
                        "y1": float(sheet_h_pt - card_bottom_y),
                    }
                )

            # --- A. BACKGROUND ---
            if not preserve_vector_template:
                c.setFillColor(Color(1, 1, 1))
                c.rect(card_x, card_bottom_y, card_w_pt, card_h_pt, fill=1, stroke=0)

            if bg_image_reader:
                try:
                    c.drawImage(bg_image_reader, card_x, card_bottom_y, width=card_w_pt, height=card_h_pt)
                except Exception as e:
                    print(f"Draw BG Error: {e}")

            _draw_custom_editor_objects_pdf(
                c,
                layout_config_raw,
                card_x,
                card_bottom_y,
                card_h_pt,
                scale,
                reg_font_name,
            )

            # --- B. PHOTO ---
            px_px = photo_settings.get('photo_x', 0)
            py_px = photo_settings.get('photo_y', 0)
            pw_px = photo_settings.get('photo_width', 100)
            ph_px = photo_settings.get('photo_height', 100)

            photo_x = card_x + (px_px * scale)
            photo_y = card_bottom_y + (card_h_pt - (py_px * scale) - (ph_px * scale))
            photo_w = pw_px * scale
            photo_h = ph_px * scale

            r_tl = float(photo_settings.get('photo_border_top_left', 0)) * scale
            r_tr = float(photo_settings.get('photo_border_top_right', 0)) * scale
            r_br = float(photo_settings.get('photo_border_bottom_right', 0)) * scale
            r_bl = float(photo_settings.get('photo_border_bottom_left', 0)) * scale
            radii = [r_tl, r_tr, r_br, r_bl]

            editable_photo_mode = _corel_editable_photo_mode(photo_settings)
            draw_editable_photo_frame = mode == "editable" and editable_photo_mode == "frame_only"
            photo_bytes_io = None
            has_real_student_photo = False

            # Support photo stored as Cloudinary URL (`photo_url`) or legacy local filename.
            # IMPORTANT: do NOT use `student.image_url` here (that's the generated *card* image),
            # otherwise you'll get the "thumbnail card inside card" bug.
            if getattr(student, "photo_url", None):
                try:
                    resp = requests.get(student.photo_url, timeout=10)
                    if resp.status_code == 200:
                        photo_bytes_io = io.BytesIO(resp.content)
                        has_real_student_photo = True
                except Exception:
                    photo_bytes_io = None

            if photo_bytes_io is None and getattr(student, "photo_filename", None):
                if str(student.photo_filename).startswith("http"):
                    try:
                        resp = requests.get(str(student.photo_filename), timeout=10)
                        if resp.status_code == 200:
                            photo_bytes_io = io.BytesIO(resp.content)
                            has_real_student_photo = True
                    except Exception:
                        photo_bytes_io = None
                else:
                    p_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
                    if os.path.exists(p_path):
                        with open(p_path, "rb") as fh:
                            photo_bytes_io = io.BytesIO(fh.read())
                        has_real_student_photo = True

            if photo_bytes_io is None and os.path.exists(PLACEHOLDER_PATH) and not draw_editable_photo_frame:
                try:
                    with open(PLACEHOLDER_PATH, "rb") as fh:
                        photo_bytes_io = io.BytesIO(fh.read())
                except Exception:
                    photo_bytes_io = None

            if photo_bytes_io and (has_real_student_photo or not draw_editable_photo_frame):
                c.saveState()
                if all(r == r_tl for r in radii) and r_tl > 0:
                    path = c.beginPath()
                    path.roundRect(photo_x, photo_y, photo_w, photo_h, r_tl)
                    c.clipPath(path, stroke=0)
                elif any(r > 0 for r in radii):
                    path = draw_custom_rounded_rect(c, photo_x, photo_y, photo_w, photo_h, radii)
                    c.clipPath(path, stroke=0)

                try:
                    if mode == "print":
                        try:
                            photo_bytes_io.seek(0)
                            photo_img = Image.open(photo_bytes_io)
                            if photo_img.mode in ("RGBA", "LA"):
                                rgb = Image.new("RGB", photo_img.size, (255, 255, 255))
                                rgb.paste(photo_img, mask=photo_img.split()[-1])
                                photo_img = rgb
                            elif photo_img.mode != "RGB":
                                photo_img = photo_img.convert("RGB")
                            min_w = max(1, int(round(pw_px * raster_multiplier)))
                            min_h = max(1, int(round(ph_px * raster_multiplier)))
                            if photo_img.size[0] < min_w or photo_img.size[1] < min_h:
                                photo_img = photo_img.resize(
                                    (max(min_w, photo_img.size[0]), max(min_h, photo_img.size[1])),
                                    Image.LANCZOS,
                                )
                            reader = ImageReader(photo_img)
                        except Exception:
                            photo_bytes_io.seek(0)
                            reader = ImageReader(photo_bytes_io)
                    else:
                        photo_bytes_io.seek(0)
                        reader = ImageReader(photo_bytes_io)
                    c.drawImage(reader, photo_x, photo_y, width=photo_w, height=photo_h, mask="auto")
                except Exception:
                    pass
                c.restoreState()

            if draw_editable_photo_frame:
                c.saveState()
                c.setStrokeColor(Color(0.55, 0.14, 0.24))
                c.setLineWidth(max(0.8, 1.2 * scale))
                if all(r == r_tl for r in radii) and r_tl > 0:
                    c.roundRect(photo_x, photo_y, photo_w, photo_h, r_tl, stroke=1, fill=0)
                elif any(r > 0 for r in radii):
                    path = draw_custom_rounded_rect(c, photo_x, photo_y, photo_w, photo_h, radii)
                    c.drawPath(path, stroke=1, fill=0)
                else:
                    c.rect(photo_x, photo_y, photo_w, photo_h, stroke=1, fill=0)
                c.restoreState()

            # --- C. QR / BARCODE ---
            try:
                form_data = {
                    'name': student.name, 'father_name': student.father_name,
                    'class_name': student.class_name, 'dob': student.dob,
                    'address': student.address, 'phone': student.phone
                }
                photo_ref = getattr(student, "photo_url", None) or getattr(student, "photo_filename", None) or ""
                data_hash = generate_data_hash(form_data, photo_ref)
                qr_id = data_hash[:10]

                if bool(qr_settings.get("enable_qr", False)):
                    qr_type = qr_settings.get("qr_data_type", "student_id")
                    if qr_type == "url":
                        base = qr_settings.get("qr_base_url", "")
                        if base and not base.endswith('/'):
                            base += '/'
                        qr_payload = base + qr_id
                    elif qr_type == "text":
                        qr_payload = qr_settings.get("qr_custom_text", "Sample")
                    elif qr_type == "json":
                        qr_payload = json.dumps({
                            "student_id": qr_id,
                            "name": student.name,
                            "class": student.class_name,
                            "school_name": template.school_name
                        })
                    else:
                        qr_payload = qr_id

                    size_px = max(40, int(qr_settings.get("qr_size", 120)))
                    q_x_px = int(qr_settings.get("qr_x", 50))
                    q_y_px = int(qr_settings.get("qr_y", 50))
                    qr_x = card_x + (q_x_px * scale)
                    qr_y = card_bottom_y + (card_h_pt - (q_y_px * scale) - (size_px * scale))
                    qr_w = size_px * scale
                    qr_h = size_px * scale
                    if mode == "editable":
                        qr_rgb = tuple(qr_settings.get("qr_fill_color", [0, 0, 0])) if isinstance(qr_settings, dict) else (0, 0, 0)
                        qr_fill = Color(
                            max(0, min(255, int(qr_rgb[0]))) / 255.0,
                            max(0, min(255, int(qr_rgb[1]))) / 255.0,
                            max(0, min(255, int(qr_rgb[2]))) / 255.0,
                        )
                        _draw_vector_qr(c, qr_payload, qr_x, qr_y, qr_w, qr_h, qr_fill)
                    else:
                        raster_qr_size = max(40, int(size_px * raster_multiplier))
                        qr_pil = generate_qr_code(qr_payload, qr_settings, raster_qr_size).convert("RGB")
                        c.drawImage(ImageReader(qr_pil), qr_x, qr_y, width=qr_w, height=qr_h)

                if bool(qr_settings.get("enable_barcode", False)):
                    barcode_type = qr_settings.get("barcode_data_type", "student_id")
                    if barcode_type == "url":
                        base = qr_settings.get("barcode_base_url", "")
                        if base and not base.endswith('/'):
                            base += '/'
                        barcode_payload = base + qr_id
                    elif barcode_type == "text":
                        barcode_payload = qr_settings.get("barcode_custom_text", "Sample")
                    elif barcode_type == "json":
                        barcode_payload = json.dumps({
                            "student_id": qr_id,
                            "name": student.name,
                            "class": student.class_name,
                            "school_name": template.school_name
                        })
                    else:
                        barcode_payload = qr_id

                    barcode_w_px = max(40, int(qr_settings.get("barcode_width", 220)))
                    barcode_h_px = max(30, int(qr_settings.get("barcode_height", 70)))
                    barcode_x_px = int(qr_settings.get("barcode_x", 50))
                    barcode_y_px = int(qr_settings.get("barcode_y", 200))
                    barcode_x = card_x + (barcode_x_px * scale)
                    barcode_y = card_bottom_y + (card_h_pt - (barcode_y_px * scale) - (barcode_h_px * scale))
                    barcode_w = barcode_w_px * scale
                    barcode_h = barcode_h_px * scale
                    if mode == "editable":
                        barcode_rgb = tuple(qr_settings.get("barcode_fill_color", [0, 0, 0])) if isinstance(qr_settings, dict) else (0, 0, 0)
                        bar_fill = Color(
                            max(0, min(255, int(barcode_rgb[0]))) / 255.0,
                            max(0, min(255, int(barcode_rgb[1]))) / 255.0,
                            max(0, min(255, int(barcode_rgb[2]))) / 255.0,
                        )
                        _draw_vector_barcode(c, barcode_payload, barcode_x, barcode_y, barcode_w, barcode_h, bar_fill)
                    else:
                        barcode_pil = generate_barcode_code128(
                            barcode_payload,
                            qr_settings,
                            width=max(40, int(barcode_w_px * raster_multiplier)),
                            height=max(30, int(barcode_h_px * raster_multiplier))
                        ).convert("RGB")
                        c.drawImage(ImageReader(barcode_pil), barcode_x, barcode_y, width=barcode_w, height=barcode_h)
            except Exception as code_exc:
                card_warnings.append(
                    {
                        "student_id": getattr(student, "id", None),
                        "section": "qr_barcode",
                        "error": str(code_exc),
                    }
                )
                logger.warning(
                    "QR/Barcode render issue (template_id=%s, student_id=%s, mode=%s): %s",
                    template_id,
                    getattr(student, "id", "unknown"),
                    mode,
                    code_exc,
                )

            # --- D. TEXT (UPDATED WIDTH LOGIC) ---
           # --- D. TEXT (DYNAMIC WIDTH & ADDRESS SHRINKING) ---
            text_case = font_settings.get("text_case", "normal")
            show_label_colon = bool(font_settings.get("show_label_colon", True))
            align_label_colon = bool(font_settings.get("align_label_colon", True))
            label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)
            
            fields = [
                {'k': "NAME", 'l': local_apply_text_case(labels_map['NAME'], text_case), 'v': local_apply_text_case(student.name, text_case), 'ord': 10},
                {'k': "F_NAME", 'l': local_apply_text_case(labels_map['F_NAME'], text_case), 'v': local_apply_text_case(student.father_name, text_case), 'ord': 20},
                {'k': "CLASS", 'l': local_apply_text_case(labels_map['CLASS'], text_case), 'v': local_apply_text_case(student.class_name, text_case), 'ord': 30},
                {'k': "DOB", 'l': local_apply_text_case(labels_map['DOB'], text_case), 'v': local_apply_text_case(student.dob, text_case), 'ord': 40},
                {'k': "MOBILE", 'l': local_apply_text_case(labels_map['MOBILE'], text_case), 'v': local_apply_text_case(student.phone, text_case), 'ord': 50},
                {'k': "ADDRESS", 'l': local_apply_text_case(labels_map['ADDRESS'], text_case), 'v': local_apply_text_case(student.address, text_case), 'ord': 60}
            ]
            
            if student.custom_data:
                db_fields = TemplateField.query.filter_by(template_id=template_id).all()
                for f in db_fields:
                    val = student.custom_data.get(f.field_name, "")
                    fields.append({
                        'k': f.field_name,
                        'l': local_apply_text_case(f.field_label, text_case),
                        'v': local_apply_text_case(val, text_case),
                        'ord': f.display_order
                    })
            
            fields.sort(key=lambda x: x['ord'])

            start_y_text_px = font_settings.get('start_y', 200)
            label_x_px = font_settings.get('label_x', 50)
            value_x_px = font_settings.get('value_x', 250)
            current_y_px = start_y_text_px
            line_height_px = font_settings.get('line_height', 50)

            # Photo Vertical Boundaries (Pixels)
            p_x_px = photo_settings.get("photo_x", 0)
            p_y_px = photo_settings.get("photo_y", 0)
            p_h_px = photo_settings.get("photo_height", 0)
            p_bottom_px = p_y_px + p_h_px

            def _baseline_y(top_y_px: float, font_size_pt: float) -> float:
                return card_bottom_y + (card_h_pt - (top_y_px * scale) - font_size_pt)

            for field in fields:
                field_key = _field_key_from_item(field)
                layout_item = get_field_layout_item(
                    layout_config_raw,
                    field_key,
                    label_x_px,
                    value_x_px,
                    current_y_px,
                    text_direction=direction,
                )
                label_x_eff = layout_item["label_x"]
                value_x_eff = layout_item["value_x"]
                label_y_eff = layout_item["label_y"]
                value_y_eff = layout_item["value_y"]
                label_visible = layout_item["label_visible"]
                value_visible = layout_item["value_visible"]
                label_grow = layout_item.get("label_grow")
                value_grow = layout_item.get("value_grow")
                label_rgb = layout_item.get("label_color") or label_default_rgb
                value_rgb = layout_item.get("value_color") or value_default_rgb
                label_size_px_eff = max(1, int(layout_item.get("label_font_size") or font_settings.get("label_font_size", 40)))
                value_size_px_eff = max(1, int(layout_item.get("value_font_size") or font_settings.get("value_font_size", 36)))
                lbl_size_pt_eff = label_size_px_eff * scale
                val_size_pt_eff = value_size_px_eff * scale

                if not _field_consumes_layout_space(layout_item, field.get("v", "")):
                    continue

                # If a field is moved lower via layout overrides, move the baseline down so defaults don't overlap.
                current_y_px = max(int(current_y_px), int(label_y_eff), int(value_y_eff))

                label_pdf_y = _baseline_y(label_y_eff, lbl_size_pt_eff)
                # Draw Label
                if label_visible and not use_harfbuzz_overlay:
                    c.setFillColor(_rl_color_from_rgb(label_rgb))
                    if rasterize_complex_text:
                        shaped_label = process_text_for_drawing(field["l"], lang)
                        label_text, colon_text = split_label_and_colon(
                            shaped_label,
                            lang,
                            direction,
                            include_colon=show_label_colon,
                            align_colon=align_label_colon,
                        )
                        lbl_size_px = max(1, int(round(label_size_px_eff * raster_multiplier)))
                        pil_font = _get_pil_font(pil_bold_path, lbl_size_px, lang)
                        fill = (
                            int(label_rgb[0]),
                            int(label_rgb[1]),
                            int(label_rgb[2]),
                            255,
                        )
                        colon_fill = (
                            int(colon_default_rgb[0]),
                            int(colon_default_rgb[1]),
                            int(colon_default_rgb[2]),
                            255,
                        )
                        if label_text:
                            img, baseline_y_px, width_px = _build_text_image(label_text, pil_font, fill, lang)
                            label_x = _x_for_direction_raster(
                                card_x,
                                card_w_pt,
                                label_x_eff,
                                width_px,
                                text_raster_scale,
                                direction,
                                grow_mode=label_grow,
                            )
                            c.drawImage(
                                ImageReader(img),
                                label_x,
                                label_pdf_y - (baseline_y_px * text_raster_scale),
                                width=img.size[0] * text_raster_scale,
                                height=img.size[1] * text_raster_scale,
                                mask="auto",
                            )
                        if colon_text:
                            colon_img, colon_baseline_y_px, colon_width_px = _build_text_image(colon_text, pil_font, colon_fill, lang)
                            colon_anchor_px, colon_grow = colon_anchor_for_value(value_x_eff, direction, gap_px=label_colon_gap)
                            colon_x = _x_for_direction_raster(
                                card_x,
                                card_w_pt,
                                colon_anchor_px,
                                colon_width_px,
                                text_raster_scale,
                                direction,
                                grow_mode=colon_grow,
                            )
                            c.drawImage(
                                ImageReader(colon_img),
                                colon_x,
                                label_pdf_y - (colon_baseline_y_px * text_raster_scale),
                                width=colon_img.size[0] * text_raster_scale,
                                height=colon_img.size[1] * text_raster_scale,
                                mask="auto",
                            )
                    else:
                        c.setFont(bold_font_name, lbl_size_pt_eff)
                        shaped_label = process_text_for_vector(field["l"], lang)
                        label_text, colon_text = split_label_and_colon(
                            shaped_label,
                            lang,
                            direction,
                            include_colon=show_label_colon,
                            align_colon=align_label_colon,
                        )
                        if label_text:
                            label_x = _x_for_direction(
                                card_x,
                                card_w_pt,
                                label_x_eff,
                                label_text,
                                bold_font_name,
                                lbl_size_pt_eff,
                                scale,
                                direction,
                                grow_mode=label_grow,
                            )
                            c.drawString(label_x, label_pdf_y, label_text)
                        if colon_text:
                            c.setFillColor(_rl_color_from_rgb(colon_default_rgb))
                            colon_anchor_px, colon_grow = colon_anchor_for_value(value_x_eff, direction, gap_px=label_colon_gap)
                            colon_x = _x_for_direction(
                                card_x,
                                card_w_pt,
                                colon_anchor_px,
                                colon_text,
                                bold_font_name,
                                lbl_size_pt_eff,
                                scale,
                                direction,
                                grow_mode=colon_grow,
                            )
                            c.drawString(colon_x, label_pdf_y, colon_text)
                            c.setFillColor(_rl_color_from_rgb(label_rgb))
                
                c.setFillColor(_rl_color_from_rgb(value_rgb))
                val_text = process_text_for_drawing(field["v"], lang) if rasterize_complex_text else process_text_for_vector(field["v"], lang)

                # --- 1. DYNAMIC WIDTH CALCULATION ---
                # Check overlap using Pixel coordinates (Top-Down)
                # We check if the current text line Y intersects with the Photo Y range
                is_vertically_overlapping = (value_y_eff < p_bottom_px) and ((value_y_eff + line_height_px) > p_y_px)

                if direction == "rtl":
                    # RTL: text box extends to the *left* from the mirrored anchor.
                    anchor_px = card_w_px - value_x_eff
                    left_limit_px = 20
                    if is_vertically_overlapping:
                        photo_right_px = p_x_px + photo_settings.get("photo_width", 0)
                        left_limit_px = max(left_limit_px, photo_right_px + 15)
                    max_w_px = max(50, anchor_px - left_limit_px)
                else:
                    if is_vertically_overlapping and (p_x_px > value_x_eff):
                        # Overlap detected: Restrict width to stop before photo
                        max_w_px = p_x_px - value_x_eff - 15
                    else:
                        # No overlap (Photo is above/below/left): Use full width
                        max_w_px = card_w_px - value_x_eff - 20
                
                max_width_pt = max_w_px * scale
                remaining_h_px = max(1.0, float(card_h_px - 20) - float(value_y_eff))
                remaining_h_pt = max(scale, remaining_h_px * scale)
                wrap_policy = _field_wrap_policy(field_key)
                line_height_factor = float(wrap_policy.get("line_height_factor", 1.15))
                min_font_size_pt = max(8 * scale, val_size_pt_eff * float(wrap_policy.get("min_scale", 0.78)))
                field_max_lines = max(
                    1,
                    min(
                        int(wrap_policy.get("max_lines", 3)),
                        int(remaining_h_pt / max(min_font_size_pt * line_height_factor, scale)),
                    ),
                )

                if rasterize_complex_text:
                    value_measure_builder = lambda size_pt: (
                        lambda s, _size=size_pt: _measure_raster_text_width(
                            s,
                            font_path_or_name=pil_reg_path,
                            font_size_pt=_size,
                            language=lang,
                            scale=scale,
                            raster_multiplier=raster_multiplier,
                        )
                    )
                else:
                    value_measure_builder = lambda size_pt: (
                        lambda s, _size=size_pt: _measure_vector_text_width(s, reg_font_name, _size)
                    )
                # ------------------------------------

                if use_harfbuzz_overlay:
                    if label_visible:
                        hb_label_text, hb_colon_text = split_label_and_colon(
                            field["l"],
                            lang,
                            direction,
                            include_colon=show_label_colon,
                            align_colon=align_label_colon,
                        )
                        if hb_label_text:
                            _queue_hb_run(
                                hb_overlay_runs,
                                page_index=card_count // cards_per_sheet,
                                card_x=card_x,
                                card_w_pt=card_w_pt,
                                card_bottom_y=card_bottom_y,
                                card_h_pt=card_h_pt,
                                x_px=label_x_eff,
                                y_px=label_y_eff,
                                max_w_pt=max(40 * scale, card_w_pt - (24 * scale)),
                                box_h_pt=max(lbl_size_pt_eff * 1.6, line_height_px * scale * 1.2),
                                scale=scale,
                                direction=direction,
                                text=hb_label_text,
                                font_file=hb_font_bold_file,
                                font_size_pt=lbl_size_pt_eff,
                                color_rgb=label_rgb,
                            )
                        if hb_colon_text:
                            colon_anchor_px, _ = colon_anchor_for_value(value_x_eff, direction, gap_px=label_colon_gap)
                            _queue_hb_run(
                                hb_overlay_runs,
                                page_index=card_count // cards_per_sheet,
                                card_x=card_x,
                                card_w_pt=card_w_pt,
                                card_bottom_y=card_bottom_y,
                                card_h_pt=card_h_pt,
                                x_px=colon_anchor_px,
                                y_px=label_y_eff,
                                max_w_pt=max(20 * scale, card_w_pt - (24 * scale)),
                                box_h_pt=max(lbl_size_pt_eff * 1.6, line_height_px * scale * 1.2),
                                scale=scale,
                                direction=direction,
                                text=hb_colon_text,
                                font_file=hb_font_bold_file,
                                font_size_pt=lbl_size_pt_eff,
                                color_rgb=colon_default_rgb,
                            )

                    if value_visible:
                        hb_value_text = field["v"] or ""
                        if field.get("k") == "ADDRESS" and text_case == "normal" and hb_value_text and hb_value_text.isupper() and len(hb_value_text) > 10:
                            hb_value_text = hb_value_text.title()
                        value_box_h_pt = max(val_size_pt_eff * 1.8, line_height_px * scale * 1.4)
                        if field.get("k") == "ADDRESS":
                            value_box_h_pt = max(value_box_h_pt, line_height_px * scale * 2.6)
                            current_y_px += (line_height_px * 0.5)
                        _queue_hb_run(
                            hb_overlay_runs,
                            page_index=card_count // cards_per_sheet,
                            card_x=card_x,
                            card_w_pt=card_w_pt,
                            card_bottom_y=card_bottom_y,
                            card_h_pt=card_h_pt,
                            x_px=value_x_eff,
                            y_px=value_y_eff,
                            max_w_pt=max(20 * scale, max_width_pt),
                            box_h_pt=value_box_h_pt,
                            scale=scale,
                            direction=direction,
                            text=hb_value_text,
                            font_file=hb_font_reg_file,
                            font_size_pt=val_size_pt_eff,
                            color_rgb=value_rgb,
                        )

                    current_y_px += line_height_px
                    continue

                # --- 2. ADDRESS FIELD LOGIC (SHRINK TO FIT 2 LINES) ---
                if field.get('k') == "ADDRESS":
                    # Title case for better readability if all caps
                    if text_case == "normal" and val_text and val_text.isupper() and len(val_text) > 10:
                        val_text = val_text.title()

                    address_max_lines = max(
                        1,
                        min(
                            field_max_lines,
                            int(remaining_h_pt / max(min_font_size_pt * line_height_factor, scale)),
                        ),
                    )
                    curr_font_size, lines = _fit_wrapped_text(
                        val_text,
                        font_name=reg_font_name,
                        start_size_pt=val_size_pt_eff,
                        min_size_pt=min_font_size_pt,
                        max_width_pt=max_width_pt,
                        max_lines=address_max_lines,
                        max_height_pt=remaining_h_pt,
                        line_height_factor=line_height_factor,
                        measure_builder=value_measure_builder,
                    )

                    # Draw up to 2 lines
                    if not rasterize_complex_text:
                        c.setFont(reg_font_name, curr_font_size)
                    line_spacing = curr_font_size * line_height_factor
                    value_base_y = _baseline_y(value_y_eff, curr_font_size)
                    
                    for i, line in enumerate(lines[:address_max_lines]):
                        draw_y = value_base_y - (i * line_spacing)
                        if not value_visible:
                            continue
                        if rasterize_complex_text:
                            size_px = max(1, int(round((curr_font_size / scale) * raster_multiplier)))
                            pil_font = _get_pil_font(pil_reg_path, size_px, lang)
                            fill = (
                                int(value_rgb[0]),
                                int(value_rgb[1]),
                                int(value_rgb[2]),
                                255,
                            )
                            img, baseline_y_px, width_px = _build_text_image(line, pil_font, fill, lang)
                            vx = _x_for_direction_raster(
                                card_x,
                                card_w_pt,
                                value_x_eff,
                                width_px,
                                text_raster_scale,
                                direction,
                                grow_mode=value_grow,
                            )
                            c.drawImage(
                                ImageReader(img),
                                vx,
                                draw_y - (baseline_y_px * text_raster_scale),
                                width=img.size[0] * text_raster_scale,
                                height=img.size[1] * text_raster_scale,
                                mask="auto",
                            )
                        else:
                            vx = _x_for_direction(
                                card_x,
                                card_w_pt,
                                value_x_eff,
                                line,
                                reg_font_name,
                                curr_font_size,
                                scale,
                                direction,
                                grow_mode=value_grow,
                            )
                            c.drawString(vx, draw_y, line)
                    
                    # If we used 2 lines, add a little extra spacing for the next field
                    if len(lines) > 1:
                        # Add half a line height extra
                        current_y_px += (line_height_px * 0.5)

                # --- 3. STANDARD FIELDS LOGIC ---
                else:
                    standard_max_lines = max(
                        1,
                        min(
                            field_max_lines,
                            int(remaining_h_pt / max(min_font_size_pt * line_height_factor, scale)),
                        ),
                    )
                    curr_font_size, lines = _fit_wrapped_text(
                        val_text,
                        font_name=reg_font_name,
                        start_size_pt=val_size_pt_eff,
                        min_size_pt=min_font_size_pt,
                        max_width_pt=max_width_pt,
                        max_lines=standard_max_lines,
                        max_height_pt=remaining_h_pt,
                        line_height_factor=line_height_factor,
                        measure_builder=value_measure_builder,
                    )

                    if not rasterize_complex_text:
                        c.setFont(reg_font_name, curr_font_size)
                    line_spacing = curr_font_size * line_height_factor
                    value_base_y = _baseline_y(value_y_eff, curr_font_size)

                    for i, line in enumerate(lines):
                        draw_y = value_base_y - (i * line_spacing)
                        if not value_visible:
                            continue
                        if rasterize_complex_text:
                            size_px = max(1, int(round((curr_font_size / scale) * raster_multiplier)))
                            pil_font = _get_pil_font(pil_reg_path, size_px, lang)
                            fill = (
                                int(value_rgb[0]),
                                int(value_rgb[1]),
                                int(value_rgb[2]),
                                255,
                            )
                            img, baseline_y_px, width_px = _build_text_image(line, pil_font, fill, lang)
                            vx = _x_for_direction_raster(
                                card_x,
                                card_w_pt,
                                value_x_eff,
                                width_px,
                                text_raster_scale,
                                direction,
                                grow_mode=value_grow,
                            )
                            c.drawImage(
                                ImageReader(img),
                                vx,
                                draw_y - (baseline_y_px * text_raster_scale),
                                width=img.size[0] * text_raster_scale,
                                height=img.size[1] * text_raster_scale,
                                mask="auto",
                            )
                        else:
                            vx = _x_for_direction(
                                card_x,
                                card_w_pt,
                                value_x_eff,
                                line,
                                reg_font_name,
                                curr_font_size,
                                scale,
                                direction,
                                grow_mode=value_grow,
                            )
                            c.drawString(vx, draw_y, line)

                    if len(lines) > 1:
                        extra_h_px = ((len(lines) - 1) * line_spacing) / scale
                        current_y_px += extra_h_px
                
                # Move to next field position
                current_y_px += line_height_px
                
            card_count += 1
            if card_count % cards_per_sheet == 0:
                c.showPage()
                c.setFillColor(Color(0, 0, 0))

        c.save()
        buffer.seek(0)
        if use_harfbuzz_overlay and hb_overlay_runs:
            try:
                overlay_bytes = _apply_hb_text_overlay(buffer.getvalue(), hb_overlay_runs, page_height_pt=sheet_h_pt)
                buffer = io.BytesIO(overlay_bytes)
                buffer.seek(0)
                logger.info(
                    "Applied HB overlay runs=%s template_id=%s mode=%s",
                    len(hb_overlay_runs),
                    template_id,
                    mode,
                )
            except Exception as hb_exc:
                card_warnings.append(
                    {
                        "student_id": None,
                        "section": "hb_overlay",
                        "error": str(hb_exc),
                    }
                )
                logger.warning("HB overlay failed template_id=%s mode=%s: %s", template_id, mode, hb_exc)

        if preserve_vector_template:
            composed_bytes = _compose_vector_template_export(
                template_pdf_bytes,
                buffer.getvalue(),
                template_card_placements,
                sheet_w_pt,
                sheet_h_pt,
            )
            buffer = io.BytesIO(composed_bytes)
            buffer.seek(0)

        if getattr(template, "is_double_sided", False):
            back_buffer = io.BytesIO()
            back_canvas = canvas.Canvas(
                back_buffer,
                pagesize=(sheet_w_pt, sheet_h_pt),
                pageCompression=1,
                pdfVersion=(1, 4),
            )
            back_cards_drawn = 0

            for idx, student in enumerate(students):
                idx_on_sheet = idx % cards_per_sheet
                col_idx = idx_on_sheet % cols
                row_idx = idx_on_sheet // cols

                card_x = start_x_pt + (col_idx * (card_w_pt + gap_pt))
                card_top_y = start_y_pt - (row_idx * (card_h_pt + gap_pt))
                card_bottom_y = card_top_y - card_h_pt

                back_reader = None
                if getattr(student, "back_image_url", None):
                    try:
                        resp = requests.get(student.back_image_url, timeout=10)
                        if resp.status_code == 200:
                            back_reader = ImageReader(io.BytesIO(resp.content))
                    except Exception:
                        back_reader = None
                elif getattr(student, "back_generated_filename", None):
                    back_path = os.path.join(GENERATED_FOLDER, str(student.back_generated_filename))
                    if os.path.exists(back_path):
                        back_reader = ImageReader(back_path)

                if back_reader is None:
                    back_reader = back_bg_image_reader

                if back_reader is None:
                    continue

                back_canvas.setFillColor(Color(1, 1, 1))
                back_canvas.rect(card_x, card_bottom_y, card_w_pt, card_h_pt, fill=1, stroke=0)
                try:
                    back_canvas.drawImage(back_reader, card_x, card_bottom_y, width=card_w_pt, height=card_h_pt)
                except Exception as e:
                    logger.warning(
                        "Back card draw issue (template_id=%s, student_id=%s, mode=%s): %s",
                        template_id,
                        getattr(student, "id", "unknown"),
                        mode,
                        e,
                    )
                    continue

                back_cards_drawn += 1
                if (idx + 1) < len(students) and (idx + 1) % cards_per_sheet == 0:
                    back_canvas.showPage()
                    back_canvas.setFillColor(Color(0, 0, 0))

            if back_cards_drawn:
                back_canvas.save()
                back_buffer.seek(0)
                front_doc = fitz.open(stream=buffer.getvalue(), filetype="pdf")
                back_doc = fitz.open(stream=back_buffer.getvalue(), filetype="pdf")
                merged_doc = fitz.open()
                max_pages = max(len(front_doc), len(back_doc))
                for page_index in range(max_pages):
                    if page_index < len(front_doc):
                        merged_doc.insert_pdf(front_doc, from_page=page_index, to_page=page_index)
                    if page_index < len(back_doc):
                        merged_doc.insert_pdf(back_doc, from_page=page_index, to_page=page_index)
                merged_bytes = merged_doc.tobytes(garbage=0, deflate=True)
                back_doc.close()
                front_doc.close()
                merged_doc.close()
                buffer = io.BytesIO(merged_bytes)
                buffer.seek(0)

        prefix = "COREL_EDITABLE" if mode == "editable" else "COREL_PRINT_600DPI"
        filename = f"{prefix}_{template.school_name}.pdf"
        logger.info(
            "Generated Corel PDF template_id=%s mode=%s cards=%s asset_dpi=%s warnings=%s",
            template_id,
            mode,
            card_count,
            asset_dpi,
            len(card_warnings),
        )
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error generating PDF: {str(e)}", 500
