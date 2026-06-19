"""
Font utilities: discovery, validation, loading, fallback resolution.

This module owns:
- Font file validation (Pillow-readable)
- Font discovery in static/fonts/
- Default font configuration
- Per-language fallback lists (English/Urdu/Arabic/Hindi)
- Font cmap/codepoint inspection (via fontTools when available)
- Shrink-to-fit font loader with RAQM-aware shaping
- On-demand Google Fonts downloader
- Orientation-aware font defaults

Cross-module deps:
- app.utils.helper_utils for FONTS_FOLDER, DEFAULT_FONTS
- app.utils.text_utils for process_text_for_drawing (Hindi/Arabic shaping)
"""

import logging
import os
import re

from PIL import ImageFont

from app.utils.helper_utils import DEFAULT_FONTS, FONTS_FOLDER

logger = logging.getLogger(__name__)


# ================== Font Cache Globals ==================
_FONT_CMAP_CACHE = {}
_FONT_PIL_CACHE = {}
_FONT_MISSING_GLYPH_WARNED = set()
_REQUESTED_FONT_UNSAFE_WARNED = set()


# ================== Font Validation & Discovery ==================
def is_valid_font_file(filepath):
    if not os.path.exists(filepath) or os.path.basename(filepath).startswith('._'):
        return False
    try:
        ImageFont.truetype(filepath, size=10)
        return True
    except OSError:
        return False


def get_available_fonts():
    fonts = [f for f in os.listdir(FONTS_FOLDER) if f.lower().endswith((".ttf", ".otf"))]
    valid_fonts = [f for f in fonts if is_valid_font_file(os.path.join(FONTS_FOLDER, f))]
    return valid_fonts or DEFAULT_FONTS


# ================== Default Font Config ==================
def get_default_font_config():
    """Return default font configuration for landscape orientation."""
    return {
        "font_bold": "arialbd.ttf",
        "font_regular": "arial.ttf",
        "font_color": [0, 0, 0],
        "label_font_color": [0, 0, 0],
        "value_font_color": [0, 0, 0],
        "colon_font_color": [0, 0, 0],
        "label_font_size": 40,
        "value_font_size": 36,
        "label_x": 50,
        "value_x": 280,
        "start_y": 275,
        "line_height": 50,
        "text_case": "normal",
        "show_label_colon": True,
        "align_label_colon": True,
        "label_colon_gap": 8,
        "address_max_lines": 2,
        "enable_label_gradient": False,
        "label_font_color_bottom": [51, 51, 51],
        "enable_value_gradient": False,
        "value_font_color_bottom": [51, 51, 51],
        "enable_colon_gradient": False,
        "colon_font_color_bottom": [51, 51, 51],
    }


def get_font_settings_for_orientation(template_id, font_settings):
    """Adjust font settings based on template orientation."""
    from app.utils.layout_utils import get_template_orientation
    orientation = get_template_orientation(template_id)
    default_font = get_default_font_config()
    settings = {**default_font, **font_settings}
    if orientation == 'portrait':
        if 'label_font_size' not in font_settings:
            settings['label_font_size'] = 32
        if 'value_font_size' not in font_settings:
            settings['value_font_size'] = 28
        if 'label_x' not in font_settings:
            settings['label_x'] = 40
        if 'value_x' not in font_settings:
            settings['value_x'] = 200
        if 'start_y' not in font_settings:
            settings['start_y'] = 120
        if 'line_height' not in font_settings:
            settings['line_height'] = 45
    return settings


# ================== Font Codepoint Inspection ==================
def _required_codepoints_for_render(text):
    from app.utils.text_utils import _clean_bidi_controls
    required = set()
    for ch in _clean_bidi_controls(text):
        if ch.isspace():
            continue
        required.add(ord(ch))
    return required


def _font_codepoints(font_path):
    """Return supported Unicode codepoints from a font cmap. Cached per path."""
    path = str(font_path or "")
    if not path:
        return None
    if path in _FONT_CMAP_CACHE:
        return _FONT_CMAP_CACHE[path]

    try:
        from fontTools.ttLib import TTFont as FTFont
        tt = FTFont(path, lazy=True)
        cps = set()
        cmap = tt.get("cmap")
        if cmap and getattr(cmap, "tables", None):
            for table in cmap.tables:
                cmap_dict = getattr(table, "cmap", None)
                if cmap_dict:
                    cps.update(cmap_dict.keys())
        try:
            tt.close()
        except Exception:
            pass
        _FONT_CMAP_CACHE[path] = cps
        return cps
    except Exception:
        _FONT_CMAP_CACHE[path] = None
        return None


def _font_covers_text(font_path, text):
    required = _required_codepoints_for_render(text)
    if not required:
        return True
    supported = _font_codepoints(font_path)
    if supported is None:
        return True
    return required.issubset(supported)


# ================== Language Font Fallbacks ==================
def _presentation_forms_font_fallbacks():
    """Return fonts likely to include Arabic Presentation Forms glyphs."""
    preferred = ["arabtype.ttf", "ARABIAN.TTF", "ARABIA.TTF", "ARB.TTF"]
    existing = []
    for name in preferred:
        try:
            if os.path.exists(os.path.join(FONTS_FOLDER, name)):
                existing.append(name)
        except Exception:
            continue
    return existing


def _language_font_fallbacks(language):
    """Ordered Unicode font fallbacks per language."""
    from app.utils.text_utils import _normalize_language, PIL_RAQM_AVAILABLE
    lang = _normalize_language(language)

    safe_pf = _presentation_forms_font_fallbacks()
    urdu_modern = [
        "NotoNastaliqUrdu-Regular.ttf",
        "NotoNastaliqUrdu-Medium.ttf",
    ]
    arabic_modern = ["Amiri-BoldItalic.ttf"]
    urdu_priority = [*urdu_modern, *safe_pf] if PIL_RAQM_AVAILABLE else [*safe_pf, *urdu_modern]
    arabic_priority = [*arabic_modern, *safe_pf] if PIL_RAQM_AVAILABLE else [*safe_pf, *arabic_modern]

    mapping = {
        "urdu": urdu_priority,
        "arabic": arabic_priority,
        "hindi": ["TiroDevanagariHindi-Regular.ttf"],
        "english": ["arial.ttf", "arialbd.ttf"],
    }

    candidates = mapping.get(lang, mapping["english"])
    existing = []
    for name in candidates:
        try:
            if os.path.exists(os.path.join(FONTS_FOLDER, name)):
                existing.append(name)
        except Exception:
            continue
    return existing or candidates


def download_font_if_missing(font_family):
    """Download the .ttf file for a given Google Font family if not present."""
    import requests
    if not font_family:
        return None

    standard_fonts = {"arial", "times new roman", "courier new", "helvetica", "georgia"}
    if font_family.lower() in standard_fonts:
        return None

    font_filename = f"{font_family.replace(' ', '')}.ttf"
    local_path = os.path.join(FONTS_FOLDER, font_filename)
    if os.path.exists(local_path):
        return local_path

    logger.info(f"Downloading Google Font: {font_family}")
    try:
        url = f"https://fonts.googleapis.com/css?family={font_family.replace(' ', '+')}"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            content = res.text
            urls = re.findall(r'url\((https://[^)]+\.ttf)\)', content)
            if urls:
                font_url = urls[0]
                logger.info(f"Downloading font file from {font_url}")
                font_res = requests.get(font_url, timeout=15)
                if font_res.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(font_res.content)
                    logger.info(f"Successfully saved font {font_family} to {local_path}")
                    return local_path
        logger.warning(f"Could not download font {font_family} from Google Fonts")
    except Exception as e:
        logger.error(f"Error downloading font {font_family}: {e}")
    return None


# ================== Dynamic Font Loader ==================
def load_font_dynamic(font_path, text, max_width, start_size, language="english"):
    """Load a font and dynamically adjust its size to fit within max_width.

    - Accepts an absolute path OR a bare filename (resolved from /static/fonts/).
    - For Urdu/Arabic/Hindi: never fall back to Arial; use Unicode fallbacks instead.
    """
    from PIL import Image, ImageDraw
    from app.utils.text_utils import (
        PIL_RAQM_AVAILABLE,
        _normalize_language,
        _warn_hindi_basic_engine_once,
        process_text_for_drawing,
    )

    text = "" if text is None else str(text)
    lang = _normalize_language(language)

    def _resolve_font_path(path_or_name):
        if not path_or_name:
            return ""
        p = str(path_or_name).strip()
        if not p:
            return ""
        if os.path.isabs(p):
            return p
        base, ext = os.path.splitext(p)
        if not ext:
            download_font_if_missing(p)
            p = f"{p.replace(' ', '')}.ttf"
        return os.path.join(FONTS_FOLDER, p)

    start_size_int = int(start_size) if start_size else 10
    start_size_int = max(1, start_size_int)
    min_size = max(8, start_size_int - 10)

    use_raqm_layout = (
        lang in {"hindi", "urdu", "arabic"}
        and PIL_RAQM_AVAILABLE
        and hasattr(ImageFont, "Layout")
        and hasattr(ImageFont.Layout, "RAQM")
    )
    if lang == "hindi" and not use_raqm_layout:
        _warn_hindi_basic_engine_once()

    def _load_truetype(candidate_path, size):
        cache_key = (candidate_path, size, use_raqm_layout)
        if cache_key in _FONT_PIL_CACHE:
            return _FONT_PIL_CACHE[cache_key]
        font = None
        if use_raqm_layout:
            try:
                font = ImageFont.truetype(candidate_path, size, layout_engine=ImageFont.Layout.RAQM)
            except TypeError:
                pass
        if font is None:
            font = ImageFont.truetype(candidate_path, size)
        _FONT_PIL_CACHE[cache_key] = font
        return font

    candidates = []
    requested_path = _resolve_font_path(font_path)
    prepend_language_fallbacks = lang == "hindi"

    if prepend_language_fallbacks:
        for fname in _language_font_fallbacks(lang):
            candidates.append(os.path.join(FONTS_FOLDER, fname))

    if requested_path:
        if lang in {"urdu", "arabic"} and not PIL_RAQM_AVAILABLE:
            safe_names = {n.lower() for n in _presentation_forms_font_fallbacks()}
            requested_name = os.path.basename(requested_path).lower()
            if safe_names and requested_name not in safe_names:
                warn_key = (requested_name, lang)
                if warn_key not in _REQUESTED_FONT_UNSAFE_WARNED:
                    _REQUESTED_FONT_UNSAFE_WARNED.add(warn_key)
                    logger.warning(
                        "Requested font '%s' for %s is not Presentation-Forms compatible; using fallback fonts to avoid tofu.",
                        os.path.basename(requested_path),
                        lang,
                    )
            else:
                candidates.append(requested_path)
        else:
            candidates.append(requested_path)

    if not prepend_language_fallbacks:
        for fname in _language_font_fallbacks(lang):
            candidates.append(os.path.join(FONTS_FOLDER, fname))

    if lang not in {"urdu", "arabic", "hindi"}:
        candidates.append(os.path.join(FONTS_FOLDER, "arial.ttf"))
        candidates.append(os.path.join(FONTS_FOLDER, "arialbd.ttf"))

    seen = set()
    candidates = [p for p in candidates if p and not (p in seen or seen.add(p))]

    coverage_text = text
    if lang in {"urdu", "arabic"} and not PIL_RAQM_AVAILABLE and text:
        try:
            coverage_text = process_text_for_drawing(text, lang)
        except Exception:
            coverage_text = text

    for candidate_path in candidates:
        try:
            if not os.path.exists(candidate_path):
                continue

            if lang in {"urdu", "arabic", "hindi"} and coverage_text:
                if not _font_covers_text(candidate_path, coverage_text):
                    warn_key = (os.path.basename(candidate_path).lower(), lang)
                    if warn_key not in _FONT_MISSING_GLYPH_WARNED:
                        _FONT_MISSING_GLYPH_WARNED.add(warn_key)
                        logger.warning(
                            "Skipping font '%s' for %s because glyph coverage is incomplete for current text.",
                            os.path.basename(candidate_path),
                            lang,
                        )
                    continue

            size = start_size_int
            font = _load_truetype(candidate_path, size)

            if max_width and float(max_width) > 0:
                try:
                    while font.getlength(text) > max_width and size > min_size:
                        size -= 1
                        font = _load_truetype(candidate_path, size)
                except Exception:
                    try:
                        dummy = Image.new("RGB", (1, 1), "white")
                        d = ImageDraw.Draw(dummy)
                        while True:
                            bbox = d.textbbox((0, 0), text, font=font)
                            w = (bbox[2] - bbox[0]) if bbox else 0
                            if w <= max_width or size <= min_size:
                                break
                            size -= 1
                            font = _load_truetype(candidate_path, size)
                    except Exception:
                        pass

            return font
        except Exception as e:
            logger.warning(f"Error loading font {candidate_path}: {e}")
            continue

    if lang in {"urdu", "arabic", "hindi"}:
        logger.warning(f"No usable Unicode font found for language '{lang}'. Using default PIL font.")
        return ImageFont.load_default()

    try:
        arial_path = os.path.join(FONTS_FOLDER, "arial.ttf")
        if os.path.exists(arial_path):
            return _load_truetype(arial_path, start_size_int)
    except Exception:
        pass
    return ImageFont.load_default()


__all__ = [
    "_font_codepoints",
    "_font_covers_text",
    "_language_font_fallbacks",
    "_presentation_forms_font_fallbacks",
    "_required_codepoints_for_render",
    "download_font_if_missing",
    "get_available_fonts",
    "get_default_font_config",
    "get_font_settings_for_orientation",
    "is_valid_font_file",
    "load_font_dynamic",
]
