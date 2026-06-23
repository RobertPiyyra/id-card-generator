"""
Text shaping and BiDi functions for CorelDRAW export.

Extracted from app/services/corel_export_service.py — handles Arabic script
detection, BiDi text processing, and text direction normalization.

USAGE: These functions are identical copies of those in corel_export_service.py.
The original definitions shadow these imports at runtime.
"""
import logging
import re
import unicodedata

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import ImageFont

from app.utils.text_utils import _normalize_language

logger = logging.getLogger(__name__)

try:
    _ARABIC_RESHAPER = arabic_reshaper.ArabicReshaper(
        configuration={"use_unshaped_instead_of_isolated": True}
    )
except Exception:
    _ARABIC_RESHAPER = None
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




def process_text_for_vector(text: str, language: str, *, for_native_bidi: bool = False) -> str:
    """
    Prepare text for vector PDF drawing.

    Why this exists:
    - ReportLab does not do complex shaping (joining) or BiDi reordering by itself.
    - Arabic/Urdu need reshaping (glyph joining) + BiDi to display correctly.
    - Hindi (Devanagari) is LTR and does not need BiDi, so return unchanged.

    Args:
        text: The raw text to process.
        language: The language code (urdu, arabic, hindi, english, etc).
        for_native_bidi: If True, skip BiDi reordering (caller handles BiDi natively,
            e.g. PyMuPDF insert_text). Only reshaping is applied. Arabic/Urdu glyphs
            are still joined correctly but text is left in logical order for the
            rendering engine to handle directionality.
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
            if for_native_bidi:
                # Caller handles BiDi (e.g. PyMuPDF). Return reshaped text in
                # logical order — do NOT run get_display() which reorders for
                # LTR-only renderers like ReportLab.
                return _clean_bidi_controls(reshaped)
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




