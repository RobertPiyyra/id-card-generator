"""
Text utilities: script detection, RTL shaping, bidi/reshaper, label formatting.

This module owns:
- Script detection (Devanagari, Arabic, Latin)
- BiDi control character cleanup
- Arabic reshaping + BiDi reordering (with/without RAQM)
- Devanagari matra reordering (Hindi fallback when no RAQM)
- Language normalization
- Pillow drawing kwargs for RTL/LTR
- Localized standard labels (NAME, F_NAME, etc.)
- Label/col-on splitting for aligned colon mode
- Text case transformation

Cross-module deps:
- None (this is a low-level module)
"""

import logging
import re
import unicodedata

import arabic_reshaper
from bidi.algorithm import get_display

logger = logging.getLogger(__name__)


# ================== RAQM Support Detection ==================
def _has_pillow_raqm():
    try:
        from PIL import features as pil_features
        return bool(pil_features.check("raqm"))
    except Exception:
        return False


PIL_RAQM_AVAILABLE = _has_pillow_raqm()
_HINDI_BASIC_ENGINE_WARNED = False

try:
    _ARABIC_RESHAPER = arabic_reshaper.ArabicReshaper(
        configuration={"use_unshaped_instead_of_isolated": True}
    )
except Exception:
    _ARABIC_RESHAPER = None


# ================== BiDi / Script Constants ==================
_DEVANAGARI_I_MATRA = "\u093f"
_DEVANAGARI_VIRAMA = "\u094d"
_DEVANAGARI_NUKTA = "\u093c"
_DEVANAGARI_OTHER_MARKS = {
    "\u0900", "\u0901", "\u0902", "\u0903",
    "\u093a", "\u093b", "\u0941", "\u0942", "\u0943", "\u0944", "\u0945",
    "\u0946", "\u0947", "\u0948", "\u0949", "\u094a", "\u094b", "\u094c",
    "\u094e", "\u094f", "\u0951", "\u0952",
}
_ARABIC_UNICODE_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)


def _warn_hindi_basic_engine_once():
    global _HINDI_BASIC_ENGINE_WARNED
    if _HINDI_BASIC_ENGINE_WARNED:
        return
    _HINDI_BASIC_ENGINE_WARNED = True
    logger.warning(
        "Pillow RAQM is not available. Hindi fallback shaping is being used (best effort). "
        "Install Pillow with libraqm for fully-correct matra placement."
    )


def _clean_bidi_controls(text):
    """Remove hidden bidi/control chars that frequently cause tofu boxes in Pillow/ReportLab.

    Keep ZWNJ/ZWJ because they can be semantically important in Arabic-script words.
    """
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


def _contains_devanagari(text):
    text = "" if text is None else str(text)
    for ch in text:
        cp = ord(ch)
        if (0x0900 <= cp <= 0x097F) or (0xA8E0 <= cp <= 0xA8FF):
            return True
    return False


def _contains_arabic_script(text):
    text = "" if text is None else str(text)
    for ch in text:
        cp = ord(ch)
        for start, end in _ARABIC_UNICODE_RANGES:
            if start <= cp <= end:
                return True
    return False


def _reorder_devanagari_i_matra_for_basic_engine(text):
    """Best-effort fallback for Hindi when RAQM shaping is unavailable.

    PIL basic layout can draw the short-i matra after the consonant which appears wrong.
    This reorders only that matra to visual position.
    """
    chars = list("" if text is None else str(text))
    index = 0
    while index < len(chars):
        if chars[index] != _DEVANAGARI_I_MATRA or index == 0:
            index += 1
            continue
        cluster_start = index - 1
        while cluster_start > 0 and chars[cluster_start] in _DEVANAGARI_OTHER_MARKS:
            cluster_start -= 1
        while cluster_start > 1 and chars[cluster_start - 1] == _DEVANAGARI_VIRAMA:
            cluster_start -= 2
            while cluster_start > 0 and chars[cluster_start] == _DEVANAGARI_NUKTA:
                cluster_start -= 1
        matra = chars.pop(index)
        chars.insert(max(0, cluster_start), matra)
        index += 1
    return "".join(chars)


def _script_hint_for_text(text):
    if _contains_devanagari(text):
        return "hindi"
    if _contains_arabic_script(text):
        return "arabic"
    return "english"


def _safe_bidi_get_display(text, base_dir="R"):
    try:
        return get_display(text, base_dir=base_dir)
    except TypeError:
        return get_display(text)


def _normalize_language(language):
    return (language or "english").strip().lower()


def process_text_for_drawing(text, lang, base_dir="R"):
    """Properly shape and display RTL text (Urdu/Arabic).
    For Urdu/Arabic: Reshapes letters + Reorders for RTL.
    For English/Hindi: Returns original text (LTR).
    """
    if not text:
        return ""
    text = _clean_bidi_controls(text)
    if not text:
        return ""
    lang = _normalize_language(lang)
    if lang in ["urdu", "arabic"]:
        if PIL_RAQM_AVAILABLE:
            return text
        try:
            if _ARABIC_RESHAPER is not None:
                reshaped_text = _ARABIC_RESHAPER.reshape(text)
            else:
                reshaped_text = arabic_reshaper.reshape(text)
            display_text = _safe_bidi_get_display(reshaped_text, base_dir=base_dir)
            return _clean_bidi_controls(display_text)
        except Exception as e:
            logger.warning(f"Error reshaping Arabic/Urdu text: {e}")
            return text
    if lang == "hindi":
        if PIL_RAQM_AVAILABLE:
            return text
        return _reorder_devanagari_i_matra_for_basic_engine(text)
    return text


def get_draw_text_kwargs(text, language):
    """Pillow drawing kwargs for ImageDraw.text() / textbbox() / textlength()."""
    lang = _normalize_language(language)
    if lang in {"urdu", "arabic"} and PIL_RAQM_AVAILABLE:
        return {"direction": "rtl", "language": "ur" if lang == "urdu" else "ar"}
    if lang == "hindi" and PIL_RAQM_AVAILABLE:
        return {"direction": "ltr", "language": "hi"}
    return {}


def format_label_for_drawing(label_text, language, text_direction, include_colon=True):
    """Format a label with a colon in a direction-aware way (LTR: 'NAME:', RTL: ':نام')."""
    label = "" if label_text is None else str(label_text)
    direction = (text_direction or "ltr").strip().lower()
    lang = _normalize_language(language)
    key = label.strip().upper().replace(" ", "")
    key_aliases = {
        "NAME": "NAME",
        "F.NAME": "F_NAME",
        "FNAME": "F_NAME",
        "CLASS": "CLASS",
        "D.O.B": "DOB",
        "D.O.B.": "DOB",
        "DOB": "DOB",
        "MOBILE": "MOBILE",
        "ADDRESS": "ADDRESS",
    }
    std_key = key_aliases.get(key)
    if lang == "english" and direction == "rtl":
        lang = "urdu"
    localized_labels = {
        "urdu": {
            "NAME": "نام",
            "F_NAME": "ولدیت",
            "CLASS": "جماعت",
            "DOB": "تاریخ پیدائش",
            "MOBILE": "موبائل",
            "ADDRESS": "پتہ",
        },
        "hindi": {
            "NAME": "नाम",
            "F_NAME": "पिता का नाम",
            "CLASS": "कक्षा",
            "DOB": "जन्म तिथि",
            "MOBILE": "मोबाइल",
            "ADDRESS": "पता",
        },
        "arabic": {
            "NAME": "الاسم",
            "F_NAME": "اسم الأب",
            "CLASS": "الصف",
            "DOB": "تاريخ الميلاد",
            "MOBILE": "رقم الهاتف",
            "ADDRESS": "العنوان",
        },
    }
    if std_key and lang in localized_labels:
        label = localized_labels[lang].get(std_key, label)
    if not label:
        return label
    if not bool(include_colon):
        return label
    if direction == "rtl":
        return label if label.startswith(":") else f":{label}"
    return label if label.endswith(":") else f"{label}:"


def get_localized_standard_labels(language, localization_pack=None):
    """Resolve standard label dictionary for a template language, with optional overrides."""
    lang = _normalize_language(language)
    defaults = {
        "english": {
            "NAME": "NAME",
            "F_NAME": "F.NAME",
            "CLASS": "CLASS",
            "DOB": "D.O.B",
            "MOBILE": "MOBILE",
            "ADDRESS": "ADDRESS",
        },
        "urdu": {
            "NAME": "نام",
            "F_NAME": "ولدیت",
            "CLASS": "جماعت",
            "DOB": "تاریخ پیدائش",
            "MOBILE": "موبائل",
            "ADDRESS": "پتہ",
        },
        "hindi": {
            "NAME": "नाम",
            "F_NAME": "पिता का नाम",
            "CLASS": "कक्षा",
            "DOB": "जन्म तिथि",
            "MOBILE": "मोबाइल",
            "ADDRESS": "पता",
        },
        "arabic": {
            "NAME": "الاسم",
            "F_NAME": "اسم الأب",
            "CLASS": "الصف",
            "DOB": "تاريخ الميلاد",
            "MOBILE": "رقم الهاتف",
            "ADDRESS": "العنوان",
        },
    }
    base = dict(defaults.get(lang, defaults["english"]))
    pack = localization_pack if isinstance(localization_pack, dict) else {}
    pack_lang = pack.get(lang) if isinstance(pack.get(lang), dict) else {}
    aliases = {"name": "NAME", "f_name": "F_NAME", "class": "CLASS", "dob": "DOB", "mobile": "MOBILE", "address": "ADDRESS"}
    for raw_key, raw_val in pack_lang.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        canonical = aliases.get(key.lower(), key.upper())
        if canonical in base and raw_val is not None and str(raw_val).strip():
            base[canonical] = str(raw_val)
    return base


def split_label_and_colon(label_text, language, text_direction, include_colon=True, align_colon=False):
    """Build label text for drawing. Returns (full_label, colon_str)."""
    full_label = format_label_for_drawing(
        label_text,
        language,
        text_direction,
        include_colon=include_colon,
    )
    if not full_label or not include_colon or not align_colon:
        return full_label, ""
    direction = (text_direction or "ltr").strip().lower()
    if direction == "rtl":
        if full_label.startswith(":"):
            return full_label[1:], ":"
        return full_label, ":"
    if full_label.endswith(":"):
        return full_label[:-1], ":"
    return full_label, ":"


def apply_text_case(text, case_type):
    """Apply text case transformation based on case_type."""
    if not text:
        return text
    if case_type == "uppercase":
        return text.upper()
    if case_type == "lowercase":
        return text.lower()
    if case_type == "capitalize":
        return text.title()
    return text


__all__ = [
    "PIL_RAQM_AVAILABLE",
    "_contains_arabic_script",
    "_contains_devanagari",
    "_clean_bidi_controls",
    "_normalize_language",
    "_reorder_devanagari_i_matra_for_basic_engine",
    "_safe_bidi_get_display",
    "_script_hint_for_text",
    "_warn_hindi_basic_engine_once",
    "apply_text_case",
    "format_label_for_drawing",
    "get_draw_text_kwargs",
    "get_localized_standard_labels",
    "process_text_for_drawing",
    "split_label_and_colon",
]
