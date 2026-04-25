import os
import json
import re
import logging
import unicodedata
import time
from urllib.parse import urlparse
from PIL import Image, ImageFont, ImageDraw
import fitz # PyMuPDF
import io
from models import db, Template
import hashlib
import qrcode
from qrcode.image.pil import PilImage
from qrcode.image.styles.moduledrawers import SquareModuleDrawer, RoundedModuleDrawer, CircleModuleDrawer
import arabic_reshaper
from bidi.algorithm import get_display

logger = logging.getLogger(__name__)


def _has_pillow_raqm():
    """
    True when Pillow was built with libraqm support (required for proper Indic shaping).
    """
    try:
        from PIL import features as pil_features
        return bool(pil_features.check("raqm"))
    except Exception:
        return False


PIL_RAQM_AVAILABLE = _has_pillow_raqm()
_HINDI_BASIC_ENGINE_WARNED = False
_FONT_CMAP_CACHE = {}
_FONT_MISSING_GLYPH_WARNED = set()
_REQUESTED_FONT_UNSAFE_WARNED = set()
try:
    # Professional fallback profile:
    # some legacy Arabic fonts miss isolated presentation-form glyphs (e.g. U+FBAE),
    # which appears as tofu boxes. This option keeps isolated letters in base Unicode.
    _ARABIC_RESHAPER = arabic_reshaper.ArabicReshaper(
        configuration={"use_unshaped_instead_of_isolated": True}
    )
except Exception:
    _ARABIC_RESHAPER = None


def _warn_hindi_basic_engine_once():
    global _HINDI_BASIC_ENGINE_WARNED
    if _HINDI_BASIC_ENGINE_WARNED:
        return
    _HINDI_BASIC_ENGINE_WARNED = True
    logger.warning(
        "Pillow RAQM is not available. Hindi fallback shaping is being used (best effort). "
        "Install Pillow with libraqm for fully-correct matra placement."
    )


def _clean_bidi_controls(text: str) -> str:
    """
    Remove hidden bidi/control chars that frequently cause tofu boxes in Pillow/ReportLab.

    Keep ZWNJ/ZWJ because they can be semantically important in Arabic-script words.
    """
    if text is None:
        return ""
    cleaned = []
    for ch in str(text):
        cp = ord(ch)
        cat = unicodedata.category(ch)
        if cp in {0xFFFD, 0xFEFF}:  # replacement char / BOM
            continue
        if cat in {"Cc", "Cs"}:
            continue
        if cat == "Cf" and ch not in {"\u200c", "\u200d"}:
            continue
        cleaned.append(ch)
    return "".join(cleaned)


def _required_codepoints_for_render(text: str) -> set[int]:
    required = set()
    for ch in _clean_bidi_controls(text):
        if ch.isspace():
            continue
        required.add(ord(ch))
    return required


def _font_codepoints(font_path: str):
    """
    Return supported Unicode codepoints from a font cmap.
    Cached per path for performance.
    """
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


def _font_covers_text(font_path: str, text: str) -> bool:
    required = _required_codepoints_for_render(text)
    if not required:
        return True

    supported = _font_codepoints(font_path)
    if supported is None:
        # If cmap couldn't be read, don't hard-fail; let Pillow attempt loading.
        return True
    return required.issubset(supported)

# ================== Paths ==================
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_ROOT, "static")
UPLOAD_FOLDER = os.path.join(STATIC_DIR, "Uploads")
GENERATED_FOLDER = os.path.join(STATIC_DIR, "generated")
FONTS_FOLDER = os.path.join(STATIC_DIR, "fonts")
TEMPLATES_CONFIG = os.path.join(APP_ROOT, "templates_config.json")
FONT_CONFIG_PATH = os.path.join(APP_ROOT, "font_config.json")
PHOTO_CONFIG_PATH = os.path.join(APP_ROOT, "photo_config.json")
DUPLICATE_CONFIG_PATH = os.path.join(APP_ROOT, "duplicate_config.json")
PLACEHOLDER_PATH = os.path.join(STATIC_DIR, "placeholder.jpg")
DEFAULT_FONTS = ["arial.ttf", "arialbd.ttf"]

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(GENERATED_FOLDER, exist_ok=True)
os.makedirs(FONTS_FOLDER, exist_ok=True)

# Create placeholder.jpg if not exists
if not os.path.exists(PLACEHOLDER_PATH):
    placeholder = Image.new("RGB", (100, 100), color="gray")
    placeholder.save(PLACEHOLDER_PATH)

# ================== Print Sizes ==================
DPI = 300
ID_WIDTH_PX = int((86 / 25.4) * DPI) # 86mm width for landscape
ID_HEIGHT_PX = int((56 / 25.4) * DPI) # 56mm height for landscape
PORTRAIT_WIDTH_PX = ID_HEIGHT_PX # 56mm width for portrait
PORTRAIT_HEIGHT_PX = ID_WIDTH_PX # 86mm height for portrait
A4_WIDTH_PX = int((210 / 25.4) * DPI) # A4 portrait width
A4_HEIGHT_PX = int((297 / 25.4) * DPI) # A4 portrait height
A4_LANDSCAPE_WIDTH_PX = A4_HEIGHT_PX
A4_LANDSCAPE_HEIGHT_PX = A4_WIDTH_PX


def _normalize_template_source_url(url: str) -> str:
    """Normalize remote template URLs to a stable fetchable form."""
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
    """
    Get the template source for a template ID.
    Prefers local filename when present/readable, else uses Cloudinary URL.
    
    Args:
        template_id (int): Template ID
    
    Returns:
        str: Cloudinary URL or local file path (legacy)
    """
    try:
        template = db.session.get(Template, template_id)

        if template:
            side_data = _resolve_template_side(template, side=side)
            prefer_remote = get_storage_backend() == "cloudinary"

            # In deployed/Cloudinary mode, prefer durable remote storage over
            # Railway's ephemeral filesystem. Local mode keeps the old behavior.
            if prefer_remote and side_data["template_url"]:
                return _normalize_template_source_url(side_data["template_url"])

            if side_data["filename"]:
                local_path = os.path.join(STATIC_DIR, side_data["filename"])
                if os.path.exists(local_path):
                    return local_path

            if side_data["template_url"]:
                return _normalize_template_source_url(side_data["template_url"])

            # Legacy local filename fallback even if missing (caller will report missing file).
            if side_data["filename"]:
                return os.path.join(STATIC_DIR, side_data["filename"])
        
        logger.warning(f"No template found or no URL/filename for template ID {template_id}")
        return None
    except Exception as e:
        logger.error(f"Error fetching template path for ID {template_id}: {e}")
        return None

def get_template_settings(template_id, side="front"):
    try:
        template = db.session.get(Template, template_id)


        if template:
            side_data = _resolve_template_side(template, side=side)
            font_settings = get_default_font_config()
            photo_settings = get_default_photo_config()
            qr_settings = get_default_qr_config()
            orientation = template.card_orientation or 'landscape'
            
            if side_data["font_settings"]:
                loaded_font = side_data["font_settings"].copy()
                
                # Handle legacy font_color field for backward compatibility
                if 'font_color' in loaded_font:
                    # Copy font_color to both label and value colors if they don't exist
                    if 'label_font_color' not in loaded_font:
                        loaded_font['label_font_color'] = loaded_font['font_color']
                    if 'value_font_color' not in loaded_font:
                        loaded_font['value_font_color'] = loaded_font['font_color']
                
                # Merge with defaults
                font_settings.update(loaded_font)
                
                # Ensure color values are properly formatted
                # Convert string colors to lists if needed
                if isinstance(font_settings.get('label_font_color'), str):
                    label_color = font_settings['label_font_color']
                    if label_color.startswith('#'):
                        # Convert hex to RGB list
                        hex_color = label_color.lstrip('#')
                        font_settings['label_font_color'] = [
                            int(hex_color[0:2], 16),
                            int(hex_color[2:4], 16),
                            int(hex_color[4:6], 16)
                        ]
                    else:
                        # Convert comma-separated string to list
                        font_settings['label_font_color'] = [
                            int(x.strip()) for x in label_color.split(',')
                        ]
                
                if isinstance(font_settings.get('value_font_color'), str):
                    value_color = font_settings['value_font_color']
                    if value_color.startswith('#'):
                        # Convert hex to RGB list
                        hex_color = value_color.lstrip('#')
                        font_settings['value_font_color'] = [
                            int(hex_color[0:2], 16),
                            int(hex_color[2:4], 16),
                            int(hex_color[4:6], 16)
                        ]
                    else:
                        # Convert comma-separated string to list
                        font_settings['value_font_color'] = [
                            int(x.strip()) for x in value_color.split(',')
                        ]
            
            if side_data["photo_settings"]:
                photo_settings.update(side_data["photo_settings"])
            
            if side_data["qr_settings"]:
                qr_settings.update(side_data["qr_settings"])
                
                # Ensure colors are lists
                if isinstance(qr_settings.get('qr_fill_color'), str):
                    fill_color = qr_settings['qr_fill_color']
                    if fill_color.startswith('#'):
                        hex_color = fill_color.lstrip('#')
                        qr_settings['qr_fill_color'] = [
                            int(hex_color[0:2], 16),
                            int(hex_color[2:4], 16),
                            int(hex_color[4:6], 16)
                        ]
                    else:
                        qr_settings['qr_fill_color'] = [
                            int(x.strip()) for x in fill_color.split(',')
                        ]
                
                if isinstance(qr_settings.get('qr_back_color'), str):
                    back_color = qr_settings['qr_back_color']
                    if back_color.startswith('#'):
                        hex_color = back_color.lstrip('#')
                        qr_settings['qr_back_color'] = [
                            int(hex_color[0:2], 16),
                            int(hex_color[2:4], 16),
                            int(hex_color[4:6], 16)
                        ]
                    else:
                        qr_settings['qr_back_color'] = [
                            int(x.strip()) for x in back_color.split(',')
                        ]
            
            # Log the settings for debugging
            logger.info(f"Template {template_id} settings loaded - Label Color: {font_settings.get('label_font_color')}, Value Color: {font_settings.get('value_font_color')}")
            
            return font_settings, photo_settings, qr_settings, orientation
        return get_default_font_config(), get_default_photo_config(), get_default_qr_config(), 'landscape'
    except Exception as e:
        logger.error(f"Error fetching template settings for ID {template_id}: {e}")
        return get_default_font_config(), get_default_photo_config(), get_default_qr_config(), 'landscape'


def get_template_language_direction(template_id, side="front"):
    try:
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
        template = db.session.get(Template, template_id)
        if not template:
            return None
        side_data = _resolve_template_side(template, side=side)
        return side_data.get("layout_config")
    except Exception as e:
        logger.error(f"Error fetching template layout config for ID {template_id}: {e}")
        return None

# ================== Font Config ==================
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


# ================== Orientation Helper Functions ==================
def get_template_orientation(template_id):
    try:
        template = db.session.get(Template, template_id)


        if template:
            orientation = template.card_orientation if template.card_orientation else 'landscape'
            logger.debug(f"Template {template_id} orientation: {orientation}")
            return orientation
        return 'landscape'
    except Exception as e:
        logger.error(f"Error fetching orientation for template {template_id}: {e}")
        return 'landscape' # Fallback to landscape if database error

def get_card_size(template_id=None):
    """
    Get dynamic card size from the database.
    Checks for custom dimensions first.
    If missing, falls back to standard defaults based on Orientation.
    """
    # Standard CR80 Defaults (300 DPI)
    LANDSCAPE_W, LANDSCAPE_H = 1015, 661
    PORTRAIT_W, PORTRAIT_H = 661, 1015

    if template_id:
        try:
            # Local import to avoid circular dependencies
            from models import Template, db
            
            template = db.session.get(Template, template_id)
            
            if template:
                # 1. Try to use Custom Dimensions from DB
                if template.card_width and template.card_height:
                    return template.card_width, template.card_height
                
                # 2. If Custom Dimensions are missing/zero, check Orientation
                # This fixes the issue for older templates or fresh uploads
                if template.card_orientation == 'portrait':
                    return PORTRAIT_W, PORTRAIT_H
                else:
                    return LANDSCAPE_W, LANDSCAPE_H
                
        except Exception as e:
            # Check if logger is available, otherwise print
            try:
                logger.error(f"Error fetching template dimensions: {e}")
            except:
                print(f"Error fetching template dimensions: {e}")
    
    # 3. Ultimate Fallback (Standard Landscape)
    return LANDSCAPE_W, LANDSCAPE_H

def get_sheet_size(orientation):
    if orientation == 'landscape':
        return A4_WIDTH_PX, A4_HEIGHT_PX # Portrait sheet for landscape cards
    else:
        return A4_LANDSCAPE_WIDTH_PX, A4_LANDSCAPE_HEIGHT_PX # Landscape sheet for portrait cards

def get_default_font_config():
    """Return default font configuration for landscape orientation"""
    return {
        "font_bold": "arialbd.ttf",
        "font_regular": "arial.ttf",
        "font_color": [0, 0, 0], # Legacy field for backward compatibility
        "label_font_color": [0, 0, 0], # Black for labels
        "value_font_color": [0, 0, 0], # Black for values
        "colon_font_color": [0, 0, 0], # Optional independent color for aligned colon
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
    }

def get_default_photo_config():
    """Return default photo configuration for landscape orientation"""
    return {
        "enable_photo": True,
        "photo_x": 725,
        "photo_y": 200,
        "photo_width": 260,
        "photo_height": 313,
        "photo_border_top_left": 0,
        "photo_border_top_right": 0,
        "photo_border_bottom_right": 0,
        "photo_border_bottom_left": 0,
        "corel_editable_photo_mode": "frame_only",
    }

def get_photo_settings_for_orientation(template_id, photo_settings):
    """Adjust photo settings based on template orientation"""
    orientation = get_template_orientation(template_id)
    default_photo = get_default_photo_config()
    settings = {**default_photo, **photo_settings}
  
    if orientation == 'portrait':
        if 'photo_x' not in photo_settings:
            settings['photo_x'] = 100
        if 'photo_y' not in photo_settings:
            settings['photo_y'] = 400
        if 'photo_width' not in photo_settings:
            settings['photo_width'] = 216
        if 'photo_height' not in photo_settings:
            settings['photo_height'] = 180
  
    return settings

def get_font_settings_for_orientation(template_id, font_settings):
    """Adjust font settings based on template orientation"""
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


# ================== Text Case Helper Function ==================
def apply_text_case(text, case_type):
    """Apply text case transformation based on case_type"""
    if not text:
        return text
  
    if case_type == "uppercase":
        return text.upper()
    elif case_type == "lowercase":
        return text.lower()
    elif case_type == "capitalize":
        return text.title()
    else: # normal
        return text

# ================== QR Code Helper Functions ==================
def get_default_qr_config():
    """Return default QR configuration"""
    return {
        "enable_qr": False,
        "qr_x": 50,
        "qr_y": 50,
        "qr_size": 120,
        "qr_style": "square",
        "qr_border": 2,
        "qr_fill_color": [0, 0, 0], # Black
        "qr_back_color": [255, 255, 255], # White
        "qr_data_type": "student_id",
        "qr_custom_text": "",
        "qr_base_url": "https://example.com/verify/",
        "qr_include_logo": False,
        "qr_logo_path": ""
    }

# ================== Helpers ==================
def _normalize_language(language: str) -> str:
    return (language or "english").strip().lower()


def _presentation_forms_font_fallbacks():
    """
    Return a list of fonts (filenames) that are likely to include Arabic Presentation Forms glyphs.

    Why this exists:
    - When Pillow is built without RAQM, it cannot apply OpenType shaping (HarfBuzz/FriBidi).
    - For Urdu/Arabic we therefore use `arabic_reshaper` + `python-bidi`, which produces codepoints in
      the Arabic Presentation Forms blocks (FB50/FDFF/FE70/FEFF).
    - Many modern Arabic/Urdu fonts (e.g., Noto Nastaliq/Amiri) rely on OpenType shaping and may NOT
      include Presentation Forms glyphs, which causes tofu squares (□).

    These older "Arabic" TTFs typically include those glyphs and render correctly without RAQM.
    """
    preferred = ["arabtype.ttf", "ARABIAN.TTF", "ARABIA.TTF", "ARB.TTF"]

    existing = []
    for name in preferred:
        try:
            if os.path.exists(os.path.join(FONTS_FOLDER, name)):
                existing.append(name)
        except Exception:
            continue
    return existing


def _language_font_fallbacks(language: str):
    """
    Ordered Unicode font fallbacks per language.

    Note:
    - For Urdu/Arabic/Hindi we intentionally do NOT include Arial as a fallback,
      because it often renders missing glyphs as tofu (□) or produces broken shaping.
    """
    lang = _normalize_language(language)

    safe_pf = _presentation_forms_font_fallbacks()
    # RAQM-first policy:
    # - With RAQM: modern OpenType Urdu/Arabic fonts (Noto/Amiri) are preferred.
    # - Without RAQM: reshaper+bidi emits Presentation Forms, so legacy PF-safe fonts must come first.
    urdu_modern = [
        "NotoNastaliqUrdu-Regular.ttf",
        "NotoNastaliqUrdu-Medium.ttf",
    ]
    arabic_modern = [
        "Amiri-BoldItalic.ttf",
    ]
    urdu_priority = [*urdu_modern, *safe_pf] if PIL_RAQM_AVAILABLE else [*safe_pf, *urdu_modern]
    arabic_priority = [*arabic_modern, *safe_pf] if PIL_RAQM_AVAILABLE else [*safe_pf, *arabic_modern]

    mapping = {
        "urdu": urdu_priority,
        "arabic": arabic_priority,
        "hindi": [
            "TiroDevanagariHindi-Regular.ttf",
        ],
        "english": [
            "arial.ttf",
            "arialbd.ttf",
        ],
    }

    candidates = mapping.get(lang, mapping["english"])

    # Return only fonts that exist in our fonts folder (keeps callers' loops fast/clean).
    existing = []
    for name in candidates:
        try:
            if os.path.exists(os.path.join(FONTS_FOLDER, name)):
                existing.append(name)
        except Exception:
            continue
    return existing or candidates


def load_font_dynamic(font_path, text, max_width, start_size, language="english"):
    """
    Load a font and dynamically adjust its size to fit within max_width.

    - Accepts either an absolute file path OR a bare filename (resolved from /static/fonts/).
    - For Urdu/Arabic/Hindi: never fall back to Arial; use Unicode fallbacks instead.
    """
    text = "" if text is None else str(text)
    lang = _normalize_language(language)

    def _resolve_font_path(path_or_name: str | None) -> str:
        if not path_or_name:
            return ""
        p = str(path_or_name).strip()
        if not p:
            return ""
        if os.path.isabs(p):
            return p
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

    def _load_truetype(candidate_path: str, size: int):
        # Force RAQM layout engine for Hindi when available so matras are placed correctly.
        if use_raqm_layout:
            try:
                return ImageFont.truetype(candidate_path, size, layout_engine=ImageFont.Layout.RAQM)
            except TypeError:
                # Older Pillow may not accept `layout_engine` argument in this build.
                pass
        return ImageFont.truetype(candidate_path, size)

    # Build candidate font paths (requested + language fallbacks).
    candidates: list[str] = []

    requested_path = _resolve_font_path(font_path)
    prepend_language_fallbacks = lang == "hindi"

    if prepend_language_fallbacks:
        for fname in _language_font_fallbacks(lang):
            candidates.append(os.path.join(FONTS_FOLDER, fname))

    if requested_path:
        # Without RAQM we reshape Urdu/Arabic into Presentation Forms codepoints.
        # In that mode, modern OpenType-only fonts (e.g. Noto/Amiri) can show tofu (□),
        # so we skip unsafe requested fonts and move to PF-safe fallbacks.
        if lang in {"urdu", "arabic"} and not PIL_RAQM_AVAILABLE:
            safe_names = {n.lower() for n in _presentation_forms_font_fallbacks()}
            requested_name = os.path.basename(requested_path).lower()
            if safe_names and requested_name not in safe_names:
                warn_key = (requested_name, lang)
                if warn_key not in _REQUESTED_FONT_UNSAFE_WARNED:
                    _REQUESTED_FONT_UNSAFE_WARNED.add(warn_key)
                    logger.warning(
                        "Requested font '%s' for %s is not Presentation-Forms compatible; using fallback fonts to avoid tofu (□).",
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

    # For English and other LTR languages, allow Arial as the last resort.
    if lang not in {"urdu", "arabic", "hindi"}:
        candidates.append(os.path.join(FONTS_FOLDER, "arial.ttf"))
        candidates.append(os.path.join(FONTS_FOLDER, "arialbd.ttf"))

    # Deduplicate while preserving order.
    seen = set()
    candidates = [p for p in candidates if p and not (p in seen or seen.add(p))]

    coverage_text = text
    if lang in {"urdu", "arabic"} and not PIL_RAQM_AVAILABLE and text:
        # Without RAQM we draw reshaped+BiDi text (Presentation Forms). Font coverage must be
        # validated against the final display string, not raw Unicode input, otherwise some
        # legacy Arabic fonts pass the raw check but still render tofu (□) at draw time.
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

            # Only shrink-to-fit if a positive max_width was provided.
            if max_width and float(max_width) > 0:
                try:
                    while font.getlength(text) > max_width and size > min_size:
                        size -= 1
                        font = _load_truetype(candidate_path, size)
                except Exception:
                    # Fallback width measurement for older Pillow builds/fonts.
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

    # Final fallback (never crash rendering).
    if lang in {"urdu", "arabic", "hindi"}:
        logger.warning(f"No usable Unicode font found for language '{lang}'. Using default PIL font.")
        return ImageFont.load_default()

    # English fallback (Arial if present, else PIL default)
    try:
        arial_path = os.path.join(FONTS_FOLDER, "arial.ttf")
        if os.path.exists(arial_path):
            return _load_truetype(arial_path, start_size_int)
    except Exception:
        pass
    return ImageFont.load_default()


def load_template_from_url(url):
    """
    Load a template image from Cloudinary URL.
    Handles PDF and image formats.
    
    Args:
        url (str): Cloudinary secure URL
    
    Returns:
        PIL.Image: Template image in RGB mode
    
    Raises:
        Exception: If URL is invalid or fetch fails
    """
    try:
        import requests
        
        if not url:
            raise ValueError("Template URL is required")
        
        # Fetch image from Cloudinary
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        logger.info(f"Fetched template from Cloudinary: {url[:50]}...")
        
        # Determine format from URL or content
        content_type = response.headers.get('Content-Type', '')
        
        if 'pdf' in content_type.lower() or url.lower().endswith('.pdf') or (response.content.find(b'%PDF') >= 0 and response.content.find(b'%PDF') < 1024):
            # Handle PDF
            import fitz  # PyMuPDF
            pdf_header_pos = response.content.find(b'%PDF')
            pdf_bytes = response.content[pdf_header_pos:] if pdf_header_pos >= 0 else response.content
            if len(pdf_bytes) < 128:
                raise ValueError(f"Downloaded PDF appears truncated ({len(pdf_bytes)} bytes)")
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = pdf_doc[0]
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data)).convert("RGB")
            pdf_doc.close()
            logger.info(f"PDF template loaded from Cloudinary: {img.size}")
            return img
        else:
            # Handle image (JPG, PNG, etc.)
            img = Image.open(io.BytesIO(response.content)).convert("RGB")
            logger.info(f"Image template loaded from Cloudinary: {img.size}")
            return img
    
    except Exception as e:
        logger.error(f"Error loading template from Cloudinary URL {url}: {e}")
        raise
        raise

      
def load_template_smart(path_or_url):
    """
    Smart template loader that handles both Cloudinary URLs and local file paths.

    Args:
        path_or_url (str): Cloudinary URL (http/https) or local filesystem path

    Returns:
        PIL.Image.Image: Template image in RGBA mode
    """
    try:
        if not path_or_url:
            raise ValueError("Template path or URL is required")

        # --------------------------------------------------
        # CLOUDINARY / REMOTE TEMPLATE
        # --------------------------------------------------
        if path_or_url.startswith(("http://", "https://")):
            import requests
            from PIL import Image

            source_url = _normalize_template_source_url(path_or_url)
            last_network_error = None
            response = None
            for attempt in range(3):
                try:
                    response = requests.get(source_url, timeout=12)
                    response.raise_for_status()
                    last_network_error = None
                    break
                except requests.exceptions.RequestException as req_err:
                    status_code = getattr(getattr(req_err, "response", None), "status_code", None)
                    if status_code in (401, 403):
                        raise RuntimeError(
                            "Template URL returned unauthorized/forbidden (HTTP "
                            f"{status_code}). Cloudinary is denying public delivery for this file."
                        )
                    last_network_error = req_err
                    if attempt < 2:
                        time.sleep(0.7 * (attempt + 1))
            if last_network_error is not None or response is None:
                raise RuntimeError(
                    f"Template download failed (network/DNS): {last_network_error}"
                )

            content_type = response.headers.get('Content-Type', '')
            content_length = len(response.content)
            logger.info(
                f"Downloaded file: size={content_length} bytes, content-type={content_type}, "
                f"url={source_url[:80]}"
            )
            
            # Detect PDF: check content-type, file extension, or Cloudinary's /raw/upload/ path
            is_pdf = (
                'pdf' in content_type.lower() or 
                source_url.lower().endswith('.pdf') or
                '/raw/upload/' in source_url or  # Cloudinary raw files (PDFs)
                (response.content.find(b'%PDF') >= 0 and response.content.find(b'%PDF') < 1024)
            )
            
            if is_pdf:
                try:
                    # Allow common valid PDFs that may include a small binary/text preamble.
                    pdf_header_pos = response.content.find(b'%PDF')
                    if pdf_header_pos < 0:
                        logger.error(f"File does not appear to be a PDF. First bytes: {response.content[:20]}")
                        raise ValueError("Downloaded file is not a valid PDF (missing PDF signature)")
                    pdf_bytes = response.content[pdf_header_pos:]
                    # Corrupt/empty PDFs are seen as tiny header-only payloads and cannot be rendered.
                    if len(pdf_bytes) < 128:
                        raise RuntimeError(
                            f"Downloaded PDF is too small ({len(pdf_bytes)} bytes) and appears corrupted. "
                            "Please re-upload the PDF template."
                        )
                    
                    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page = pdf_doc[0]
                    pix = page.get_pixmap(dpi=300)
                    img_data = pix.tobytes("png")
                    img = Image.open(io.BytesIO(img_data)).convert("RGBA")
                    pdf_doc.close()
                    logger.info(f"✅ PDF template loaded successfully from Cloudinary: {img.size}")
                    return img
                except Exception as pdf_error:
                    if isinstance(pdf_error, RuntimeError):
                        raise
                    logger.error(f"PDF loading failed: {pdf_error}. Attempting fallback to image format.")
                    try:
                        # Try fallback: maybe it's an image despite being marked as PDF
                        img = Image.open(io.BytesIO(response.content))
                        logger.info(f"✅ Loaded as image format successfully: {img.size}")
                        return img.convert("RGBA")
                    except Exception as fallback_error:
                        logger.error(f"Fallback image loading also failed: {fallback_error}")
                        raise RuntimeError(f"Cannot load template - neither PDF nor image: {fallback_error}")
            else:
                try:
                    img = Image.open(io.BytesIO(response.content))
                    logger.info(f"✅ Image template loaded successfully: {img.size}")
                    return img.convert("RGBA")
                except Exception as img_error:
                    logger.error(f"Image loading failed: {img_error}")
                    raise RuntimeError(f"Cannot load template image: {img_error}")

        # --------------------------------------------------
        # LOCAL TEMPLATE (LEGACY SUPPORT)
        # --------------------------------------------------
        import os
        from PIL import Image

        if not os.path.exists(path_or_url):
            raise FileNotFoundError(f"Template file not found: {path_or_url}")

        local_ext = os.path.splitext(str(path_or_url))[1].lower()
        is_local_pdf = local_ext == ".pdf"
        if not is_local_pdf:
            try:
                with open(path_or_url, "rb") as fh:
                    header = fh.read(1024)
                is_local_pdf = b"%PDF" in header
            except Exception:
                is_local_pdf = False

        if is_local_pdf:
            try:
                pdf_doc = fitz.open(path_or_url)
                if pdf_doc.page_count < 1:
                    pdf_doc.close()
                    raise ValueError("Local PDF template has no pages")
                page = pdf_doc[0]
                pix = page.get_pixmap(dpi=300)
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data)).convert("RGBA")
                pdf_doc.close()
                logger.info(f"✅ Local PDF template loaded successfully: {img.size}")
                return img
            except Exception as local_pdf_err:
                raise RuntimeError(f"Cannot load local PDF template: {local_pdf_err}")

        img = Image.open(path_or_url)
        return img.convert("RGBA")

    except Exception as e:
        logger.exception(f"❌ Template load failed: {path_or_url}")
        raise RuntimeError(f"Error loading template: {e}")

      
def load_template(path):
    try:
        if not path or not os.path.exists(path):
            raise ValueError(f"Template file not found: {path}")
          
        ext = os.path.splitext(path)[1].lower()
        logger.info(f"Loading template: {path}, extension: {ext}")
      
        if ext == ".pdf":
            import fitz  # PyMuPDF
            pdf_doc = fitz.open(path)
            page = pdf_doc[0]
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data)).convert("RGB")
            pdf_doc.close()
            logger.info(f"PDF template loaded: {img.size}")
            return img
        elif ext in [".jpg", ".jpeg", ".png"]:
            img = Image.open(path).convert("RGB")
            logger.info(f"Image template loaded: {img.size}")
            return img
        else:
            raise ValueError(f"Unsupported template format: {ext}. Use PDF, JPG, or PNG.")
    except Exception as e:
        logger.error(f"Error loading template {path}: {e}")
        # Create a fallback template
        logger.info("Creating fallback template")
        fallback = Image.new("RGB", (1000, 600), color="white")
        draw = ImageDraw.Draw(fallback)
        draw.rectangle([50, 50, 950, 550], outline="black", width=2)
        draw.text((100, 100), "Template Load Error", fill="red")
        draw.text((100, 150), f"File: {os.path.basename(path)}", fill="black")
        draw.text((100, 200), "Using fallback template", fill="blue")
        return fallback

def round_photo(image, radii):
    """
    Apply rounded corners using subtractive masking.
    Starts with an opaque mask and 'cuts out' only the specific corners needed.
    This prevents opposite corners from interfering with each other.
    """
    image = image.convert("RGBA")
    w, h = image.size
    tl, tr, br, bl = radii

    # 1. Start with a completely Opaque (White) mask
    # This means "Show the whole image" by default
    mask = Image.new('L', (w, h), 255)
    draw = ImageDraw.Draw(mask)

    # 2. For each corner, if a radius is set:
    #    A. Draw a BLACK rectangle at the corner (Make it transparent)
    #    B. Draw a WHITE pie slice over it (Make the rounded part opaque again)
    #    This leaves exactly the "corner tip" transparent.

    # --- Top-Left ---
    if tl > 0:
        draw.rectangle([0, 0, tl, tl], fill=0)
        draw.pieslice([0, 0, tl * 2, tl * 2], 180, 270, fill=255)

    # --- Top-Right ---
    if tr > 0:
        draw.rectangle([w - tr, 0, w, tr], fill=0)
        draw.pieslice([w - tr * 2, 0, w, tr * 2], 270, 360, fill=255)

    # --- Bottom-Right ---
    if br > 0:
        draw.rectangle([w - br, h - br, w, h], fill=0)
        draw.pieslice([w - br * 2, h - br * 2, w, h], 0, 90, fill=255)

    # --- Bottom-Left ---
    if bl > 0:
        draw.rectangle([0, h - bl, bl, h], fill=0)
        draw.pieslice([0, h - bl * 2, bl * 2, h], 90, 180, fill=255)

    # Apply the new mask to the image
    image.putalpha(mask)
    return image

def generate_qr_code(data, qr_settings, size=120):
    """
    Generate QR code image using settings
    """
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=size // 21, # Approximate for standard QR
            border=qr_settings.get("qr_border", 2),
        )
        qr.add_data(data)
        qr.make(fit=True)
       
        # Get style
        style = qr_settings.get("qr_style", "square")
        if style == "rounded":
            module_drawer = RoundedModuleDrawer()
        elif style == "circle":
            module_drawer = CircleModuleDrawer()
        elif style == "gapped":
            module_drawer = SquareModuleDrawer()  # Fallback
        else:
            module_drawer = SquareModuleDrawer()
       
        # Colors
        fill_color = tuple(qr_settings.get("qr_fill_color", [0, 0, 0]))
        back_color = tuple(qr_settings.get("qr_back_color", [255, 255, 255]))
       
        # Generate image
        img = qr.make_image(
            image_factory=PilImage,
            module_drawer=module_drawer,
            color_mask=back_color,  # Background
            color_dark=fill_color    # Foreground
        )
       
        # Add logo if enabled
        if qr_settings.get("qr_include_logo", False) and qr_settings.get("qr_logo_path"):
            logo_path = os.path.join(STATIC_DIR, qr_settings["qr_logo_path"])
            if os.path.exists(logo_path):
                logo = Image.open(logo_path).convert("RGBA")
                # Scale logo to 1/5 of QR size
                logo_size = size // 5
                logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
                # Center logo
                pos = ((img.size[0] - logo_size) // 2, (img.size[1] - logo_size) // 2)
                # Paste with transparency
                img.paste(logo, pos, logo)
       
        return img.convert("RGB")
    except Exception as e:
        logger.error(f"Error generating QR code: {e}")
        # Fallback to simple QR
        try:
            qr = qrcode.QRCode(version=1, box_size=size // 21, border=4)
            qr.add_data(data)
            qr.make(fit=True)
            return qr.make_image(fill_color="black", back_color="white")
        except:
            # Ultimate fallback
            fallback = Image.new("RGB", (size, size), "white")
            draw = ImageDraw.Draw(fallback)
            draw.text((10, 10), "QR Error", fill="black")
            return fallback


def generate_barcode_code128(data, qr_settings, width=220, height=70):
    """
    Generate a Code128 barcode as a PIL image.

    We use `python-barcode` (ImageWriter) to avoid ReportLab renderPM backend issues and to keep
    barcode generation consistent across preview/PDF/bulk flows.
    """
    try:
        payload = "" if data is None else str(data)
        width = int(width) if width else 220
        height = int(height) if height else 70
        width = max(40, width)
        height = max(30, height)

        fill = tuple(qr_settings.get("barcode_fill_color", [0, 0, 0])) if isinstance(qr_settings, dict) else (0, 0, 0)
        back = tuple(qr_settings.get("barcode_back_color", [255, 255, 255])) if isinstance(qr_settings, dict) else (255, 255, 255)

        import barcode
        from barcode.writer import ImageWriter

        code128_cls = barcode.get_barcode_class("code128")
        code = code128_cls(payload, writer=ImageWriter())

        out = io.BytesIO()
        # ImageWriter accepts PIL-compatible colors for foreground/background.
        code.write(
            out,
            options={
                "write_text": False,
                "foreground": fill,
                "background": back,
            },
        )
        out.seek(0)
        img = Image.open(out).convert("RGB")
        img = img.resize((width, height), Image.LANCZOS)
        return img

    except Exception as e:
        logger.error(f"Error generating barcode: {e}")
        try:
            width = int(width) if width else 220
            height = int(height) if height else 70
            width = max(40, width)
            height = max(30, height)
        except Exception:
            width, height = 220, 70

        fallback = Image.new("RGB", (width, height), "white")
        d = ImageDraw.Draw(fallback)
        d.rectangle([0, 0, width - 1, height - 1], outline="black", width=2)
        d.text((10, max(5, (height // 2) - 8)), "BARCODE", fill="black")
        return fallback


def generate_data_hash(form_data, photo_identifier=None):
    """
    Generate a deterministic hash for the student data.
    Accepts either a photo filename or a photo URL as `photo_identifier` for backward compatibility.
    """
    data_string = f"{form_data.get('name','')}{form_data.get('father_name','')}{form_data.get('class_name','')}{form_data.get('dob','')}{form_data.get('address','')}{form_data.get('phone','')}"
    if photo_identifier:
        data_string += str(photo_identifier)
    return hashlib.md5(data_string.encode()).hexdigest()

from bidi.algorithm import get_display
import arabic_reshaper

_DEVANAGARI_I_MATRA = "\u093f"
_DEVANAGARI_VIRAMA = "\u094d"
_DEVANAGARI_NUKTA = "\u093c"
_DEVANAGARI_OTHER_MARKS = {
    "\u0900", "\u0901", "\u0902", "\u0903",  # chandrabindu/anusvara/visarga
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


def _contains_devanagari(text: str) -> bool:
    text = "" if text is None else str(text)
    for ch in text:
        cp = ord(ch)
        if (0x0900 <= cp <= 0x097F) or (0xA8E0 <= cp <= 0xA8FF):
            return True
    return False


def _contains_arabic_script(text: str) -> bool:
    text = "" if text is None else str(text)
    for ch in text:
        cp = ord(ch)
        for start, end in _ARABIC_UNICODE_RANGES:
            if start <= cp <= end:
                return True
    return False


def _reorder_devanagari_i_matra_for_basic_engine(text: str) -> str:
    """
    Best-effort fallback for Hindi when RAQM shaping is unavailable.

    PIL basic layout can draw the short-i matra (ि) after the consonant (logical order) which
    appears wrong visually (e.g. अमित -> अमति). This reorders only that matra to visual position.

    Note: this is intentionally limited and does not replace full Indic shaping.
    """
    chars = list("" if text is None else str(text))
    index = 0
    while index < len(chars):
        if chars[index] != _DEVANAGARI_I_MATRA or index == 0:
            index += 1
            continue

        cluster_start = index - 1

        # Skip signs that are stored after base in logical order.
        while cluster_start > 0 and chars[cluster_start] in _DEVANAGARI_OTHER_MARKS:
            cluster_start -= 1

        # Move over conjunct clusters joined by virama.
        while cluster_start > 1 and chars[cluster_start - 1] == _DEVANAGARI_VIRAMA:
            cluster_start -= 2
            while cluster_start > 0 and chars[cluster_start] == _DEVANAGARI_NUKTA:
                cluster_start -= 1

        matra = chars.pop(index)
        chars.insert(max(0, cluster_start), matra)
        index += 1

    return "".join(chars)


def _script_hint_for_text(text: str) -> str:
    if _contains_devanagari(text):
        return "hindi"
    if _contains_arabic_script(text):
        return "arabic"
    return "english"


def _safe_bidi_get_display(text: str, base_dir: str = "R") -> str:
    """
    Compatibility wrapper for python-bidi.

    Some environments ship an older `python-bidi` where `get_display(..., base_dir=...)`
    is not supported. We try with `base_dir` first, then fallback cleanly.
    """
    try:
        return get_display(text, base_dir=base_dir)
    except TypeError:
        return get_display(text)


def process_text_for_drawing(text, lang, base_dir='R'):
    """
    Properly shape and display RTL text (Urdu/Arabic).
    For Urdu/Arabic: Reshapes letters + Reorders for RTL.
    For English/Hindi: Returns original text (LTR).
    
    CRITICAL: base_dir='R' forces the Bidi algorithm to treat the text as 
    Right-to-Left, which fixes the "Mirroring" issue in PIL.
    """
    if not text:
        return ""
    
    text = _clean_bidi_controls(text)
    if not text:
        return ""

    lang = _normalize_language(lang)
    
    if lang in ['urdu', 'arabic']:
        # With RAQM, keep logical Unicode text and let HarfBuzz/FriBidi handle shaping+BiDi.
        # This enables modern Urdu/Arabic fonts (Noto/Amiri/etc.) without tofu.
        if PIL_RAQM_AVAILABLE:
            return text
        try:
            # 1. Reshape: Connects the letters (Ligatures)
            if _ARABIC_RESHAPER is not None:
                reshaped_text = _ARABIC_RESHAPER.reshape(text)
            else:
                reshaped_text = arabic_reshaper.reshape(text)
            
            # 2. Reorder: Fixes direction for display (BiDi)
            # base_dir='R' is usually correct for pure Urdu labels.
            # If the text is mixed (English inside Urdu), allow the caller to specify 'L'.
            display_text = _safe_bidi_get_display(reshaped_text, base_dir=base_dir)
            
            return _clean_bidi_controls(display_text)
        except Exception as e:
            logger.warning(f"Error reshaping Arabic/Urdu text: {e}")
            return text
            
    if lang == "hindi":
        # With RAQM, keep logical Unicode order and let HarfBuzz place matras correctly.
        if PIL_RAQM_AVAILABLE:
            return text
        # Without RAQM, apply best-effort short-i fallback so text is readable.
        return _reorder_devanagari_i_matra_for_basic_engine(text)

    # For English, etc.
    return text


def get_draw_text_kwargs(text, language):
    """
    Pillow drawing kwargs for `ImageDraw.text()`/`textbbox()`/`textlength()`.

    Important:
    - Urdu/Arabic with RAQM: pass explicit RTL + language hints, and keep text unshaped.
    - Urdu/Arabic without RAQM: `process_text_for_drawing()` already applies reshaper+bidi,
      so kwargs stay empty to avoid double reordering.

    For Hindi:
    - When RAQM is available, we pass language/direction hints so matras are shaped correctly.
    - Without RAQM, kwargs stay empty and `process_text_for_drawing()` applies fallback reordering.
    """
    lang = _normalize_language(language)
    if lang in {"urdu", "arabic"} and PIL_RAQM_AVAILABLE:
        return {"direction": "rtl", "language": "ur" if lang == "urdu" else "ar"}
    if lang == "hindi" and PIL_RAQM_AVAILABLE:
        return {"direction": "ltr", "language": "hi"}
    return {}


def format_label_for_drawing(label_text, language, text_direction, include_colon=True):
    """
    Format a label with a colon in a direction-aware way.

    - LTR: "NAME:"
    - RTL: ":نام"

    Note: `process_text_for_drawing()` already returns display-order text for RTL scripts.
    """
    label = "" if label_text is None else str(label_text)
    direction = (text_direction or "ltr").strip().lower()
    lang = _normalize_language(language)

    # Normalize legacy English base labels into the active script.
    # This protects older render paths that may still pass NAME/F.NAME/etc.
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


def split_label_and_colon(label_text, language, text_direction, include_colon=True, align_colon=False):
    """
    Build label text for drawing.

    Returns:
    - (full_label, "") when using inline colon or colon disabled
    - (label_without_colon, ":") when aligned colon mode is enabled
    """
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


def colon_anchor_for_value(value_x, text_direction, gap_px=8):
    """
    Return `(anchor_x, grow_mode)` for drawing an aligned colon near `value_x`.
    """
    direction = (text_direction or "ltr").strip().lower()
    try:
        gap = max(0, int(gap_px))
    except Exception:
        gap = 8

    if direction == "rtl":
        return float(value_x) + float(gap), "left"
    return float(value_x) - float(gap), "right"


def _normalize_grow_mode(grow_mode, text_direction):
    """
    Normalize requested growth/anchor mode.

    Allowed values:
    - left   : text grows to the right from anchor
    - center : text grows both sides from anchor
    - right  : text grows to the left from anchor

    Backward-compatible defaults:
    - LTR templates default to left-anchored behavior
    - RTL templates default to right-anchored behavior
    """
    direction = (text_direction or "ltr").strip().lower()
    if isinstance(grow_mode, str):
        mode = grow_mode.strip().lower()
        if mode in {"left", "center", "right"}:
            return mode
    return "right" if direction == "rtl" else "left"


def _measure_text_width(text, font, draw=None):
    """Measure text width with safe fallbacks."""
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
    """
    Direction-aware X placement with anchor growth modes.

    Growth modes:
    - left   : draw_x = anchor
    - center : draw_x = anchor - text_width/2
    - right  : draw_x = anchor - text_width

    Anchor is mirrored for RTL:
    - LTR anchor: x
    - RTL anchor: image_width - x
    """
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
    """
    Return the usable width for text anchored at `anchor_x`.

    For `center` growth mode, width is measured symmetrically around the anchor so
    the text can expand to both sides instead of inheriting one-sided LTR/RTL logic.
    """
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


def _normalize_hex_color(value):
    """
    Normalize a color input to '#RRGGBB' (uppercase), or return None if invalid.
    Accepts:
    - '#RRGGBB'
    - [r,g,b]
    - (r,g,b)
    """
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

    return None


def parse_layout_config(layout_config):
    """
    Parse and sanitize template.layout_config (from visual editor).

    Accepts a dict or a JSON string. Returns a dict with at least:
      { "fields": { FIELD_KEY: { "label": {...}, "value": {...}, "colon": {...} } } }

    Sanitization rules (minimal + safe):
    - Ensure top-level and `fields` are dicts
    - Coerce x/y to int if present
    - Coerce visible to bool if present
    - Normalize color to '#RRGGBB' if present (drop if invalid)
    - Preserve unknown keys for forward compatibility
    """
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
                # Legacy visual-editor saves often persisted an explicit `y`
                # without the later `manual_y` marker. Treat that as a manual
                # position so preview/card/PDF flows keep honoring it.
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
            for key in ("x", "y", "width", "height", "x2", "y2", "font_size", "stroke_width", "opacity", "angle"):
                if key in obj:
                    try:
                        sanitized[key] = int(obj.get(key))
                    except Exception:
                        pass
            if obj_type == "text":
                sanitized["text"] = str(obj.get("text") or "Text")
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
    """
    Read one field's layout from template.layout_config.

    Returns a dict with effective label/value x/y + visibility + optional colors:
      - label_x, label_y, label_visible, label_color (rgb tuple)
      - value_x, value_y, value_visible, value_color (rgb tuple)
      - colon_x, colon_y, colon_visible, colon_color (rgb tuple)
    """
    result = {
        "label_x": default_label_x,
        "label_y": default_y,
        "label_visible": bool(default_label_visible),
        "label_manual_y": False,
        "label_grow": _normalize_grow_mode(None, text_direction),
        "label_font_size": None,
        "value_x": default_value_x,
        "value_y": default_y,
        "value_visible": bool(default_value_visible),
        "value_manual_y": False,
        "value_grow": _normalize_grow_mode(None, text_direction),
        "value_font_size": None,
        "colon_x": None,
        "colon_y": default_y,
        "colon_visible": bool(default_label_visible if default_colon_visible is None else default_colon_visible),
        "colon_manual_y": False,
        "colon_grow": "left" if text_direction == "rtl" else "right",
        "colon_font_size": None,
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
            for key in ("x", "y", "manual_y", "font_size", "color", "grow"):
                if key in part_obj:
                    return True
        flat_keys = [
            f"{part_name}_font_size",
            f"{part_name}_color",
            f"{part_name}_grow",
        ]
        if not prefer_nested_part_layout:
            flat_keys = [f"{part_name}_x", f"{part_name}_y"] + flat_keys
        for key in flat_keys:
            if key in field_obj:
                return True
        return False

    explicit_visibility = {
        "label": False,
        "value": False,
        "colon": False,
    }

    # Backward compatibility:
    # Older visual-editor saves sometimes persisted flat keys like `value_x` / `value_y`
    # without the newer nested `value: { x, y }` structure. Honor those legacy keys first,
    # then allow nested part objects to override them when present.
    for prefix in ("label", "value", "colon"):
        flat_x = f"{prefix}_x"
        flat_y = f"{prefix}_y"
        flat_visible = f"{prefix}_visible"
        flat_grow = f"{prefix}_grow"
        flat_font_size = f"{prefix}_font_size"
        flat_color = f"{prefix}_color"

        if not prefer_nested_part_layout and flat_x in field_obj:
            try:
                result[flat_x] = int(field_obj.get(flat_x))
            except Exception:
                pass
        if not prefer_nested_part_layout and flat_y in field_obj:
            try:
                flat_y_value = int(field_obj.get(flat_y))
                try:
                    default_y_value = int(default_y)
                except Exception:
                    default_y_value = flat_y_value
                # Legacy flat Y positions from the visual editor act as baseline slots
                # for flowing fields, not absolute locks. This lets inserted dynamic
                # fields push later standard lines down instead of overlapping them.
                result[flat_y] = max(flat_y_value, default_y_value)
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
            # Side-level field settings are authoritative. Layout visibility can further hide,
            # but it must not re-enable a part that is disabled for this side.
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

    # If a part has been explicitly positioned/styled in the visual editor, treat it as visible
    # unless that same layout explicitly set `visible: false`. This keeps old templates with
    # saved coordinates from disappearing just because their legacy DB side-visibility flags
    # were left off.
    for prefix in ("label", "value", "colon"):
        visible_key = f"{prefix}_visible"
        if not explicit_visibility[prefix] and _part_has_explicit_layout(prefix):
            result[visible_key] = True

    return result


def get_layout_flow_start_y(layout_config_raw, default_start_y, field_visibility=None):
    """
    Derive the effective flow start Y from saved layout coordinates.

    Older templates often have stale `font_settings.start_y` values while the real field
    positions live in `layout_config.fields.*_y`. Using the first visible saved field Y as
    the flow baseline keeps dynamic inserted fields aligned with the actual template grid.
    """
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


def get_storage_backend():
    """
    Decide which storage backend to use.

    Values:
    - local: store templates/photos/cards under `static/`
    - cloudinary: store on Cloudinary
    - auto (default): Cloudinary if credentials exist, else local
    """
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


def get_cloudinary_face_crop_url(url, w, h):
    """
    Build a Cloudinary face-crop transformation URL.

    Returns:
    - transformed URL string if `url` looks like a Cloudinary /upload/ URL
    - None otherwise (caller should fall back to original url)
    """
    if not url or not isinstance(url, str):
        return None
    if "/upload/" not in url:
        return None

    try:
        w = int(w)
        h = int(h)
        if w <= 0 or h <= 0:
            return None
    except Exception:
        return None

    prefix, rest = url.split("/upload/", 1)
    transform = f"c_fill,g_face,w_{w},h_{h}"
    return f"{prefix}/upload/{transform}/{rest}"


def trim_transparent_edges(pil_img):
    """
    Crops transparent borders from an RGBA image.
    """
    if pil_img.mode != "RGBA":
        return pil_img

    alpha = pil_img.split()[-1]
    bbox = alpha.getbbox()
    if bbox:
        return pil_img.crop(bbox)
    return pil_img

from PIL import Image

def force_rgb(img, bg_color=(255, 255, 255)):
    """
    Ensures image is RGB-safe for JPEG export.
    Handles RGBA / LA / P modes safely.
    """
    if img.mode == "RGB":
        return img

    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, bg_color)
        bg.paste(img, mask=img.split()[-1])
        return bg

    return img.convert("RGB")
