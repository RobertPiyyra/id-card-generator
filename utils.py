import os
import json
import re
import logging
import unicodedata
import time
import math
from urllib.parse import urlparse
from PIL import Image, ImageFont, ImageDraw
import fitz # PyMuPDF
import io
from models import db, Template
import hashlib
import qrcode
from qrcode.image.pil import PilImage
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import SolidFillColorMask
from qrcode.image.styles.moduledrawers import SquareModuleDrawer, RoundedModuleDrawer, CircleModuleDrawer
import arabic_reshaper
from bidi.algorithm import get_display

logger = logging.getLogger(__name__)


# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import _has_pillow_raqm  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import _warn_hindi_basic_engine_once  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import _clean_bidi_controls  # noqa: E402

# Extracted to app.utils.fonts — backward-compat re-export
from app.utils.fonts import _required_codepoints_for_render  # noqa: E402

# Extracted to app.utils.fonts — backward-compat re-export
from app.utils.fonts import _font_codepoints  # noqa: E402

# Extracted to app.utils.fonts — backward-compat re-export
from app.utils.fonts import _font_covers_text  # noqa: E402

# Extracted to app.utils.helper_utils — backward-compat re-export
from app.utils.helper_utils import _ensure_static_placeholders  # noqa: E402

# Extracted to app.utils.helper_utils — backward-compat re-export
from app.utils.helper_utils import _normalize_template_source_url  # noqa: E402

# Extracted to app.utils.helper_utils — backward-compat re-export
from app.utils.helper_utils import _resolve_template_side  # noqa: E402

# Extracted to app.utils.helper_utils — backward-compat re-export
from app.utils.helper_utils import get_template_path  # noqa: E402

# Extracted to app.utils.helper_utils — backward-compat re-export
from app.utils.helper_utils import _parse_rgb_color  # noqa: E402

# Extracted to app.utils.helper_utils — backward-compat re-export
from app.utils.helper_utils import get_template_settings  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import get_template_language_direction  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import get_template_layout_config  # noqa: E402

# Extracted to app.utils.fonts — backward-compat re-export
from app.utils.fonts import is_valid_font_file  # noqa: E402

# Extracted to app.utils.fonts — backward-compat re-export
from app.utils.fonts import get_available_fonts  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import get_template_orientation  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import get_card_size  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import get_sheet_size  # noqa: E402

# Extracted to app.utils.fonts — backward-compat re-export
from app.utils.fonts import get_default_font_config  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import get_default_photo_config  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import get_photo_settings_for_orientation  # noqa: E402

# Extracted to app.utils.fonts — backward-compat re-export
from app.utils.fonts import get_font_settings_for_orientation  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import apply_text_case  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import get_default_qr_config  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import _normalize_language  # noqa: E402

# Extracted to app.utils.fonts — backward-compat re-export
from app.utils.fonts import _presentation_forms_font_fallbacks  # noqa: E402

# Extracted to app.utils.fonts — backward-compat re-export
from app.utils.fonts import _language_font_fallbacks  # noqa: E402

# Extracted to app.utils.fonts — backward-compat re-export
from app.utils.fonts import download_font_if_missing  # noqa: E402

# Extracted to app.utils.fonts — backward-compat re-export
from app.utils.fonts import load_font_dynamic  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import load_template_from_url  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import load_template_smart  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import load_template  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import normalize_photo_shape  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import _photo_border_rgba  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import _regular_polygon_points  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import _hex_cap_height_from_shape  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import _shape_mask  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import round_photo  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import generate_qr_code  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import generate_barcode_code128  # noqa: E402

# Extracted to app.utils.helper_utils — backward-compat re-export
from app.utils.helper_utils import generate_data_hash  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import _contains_devanagari  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import _contains_arabic_script  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import _reorder_devanagari_i_matra_for_basic_engine  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import _script_hint_for_text  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import _safe_bidi_get_display  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import process_text_for_drawing  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import get_draw_text_kwargs  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import format_label_for_drawing  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import get_localized_standard_labels  # noqa: E402

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import split_label_and_colon  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import colon_anchor_for_value  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import _normalize_grow_mode  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import _measure_text_width  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import flip_x_for_text_direction  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import get_anchor_max_text_width  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import _normalize_hex_color  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import parse_layout_config  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import _layout_int  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import _layout_part_int  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import _ordered_layout_field_keys  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import derive_font_settings_from_layout_config  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import _hex_to_rgb_tuple  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import get_field_layout_item  # noqa: E402

# Extracted to app.utils.layout_utils — backward-compat re-export
from app.utils.layout_utils import get_layout_flow_start_y  # noqa: E402

# Extracted to app.utils.helper_utils — backward-compat re-export
from app.utils.helper_utils import get_storage_backend  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import get_cloudinary_face_crop_url  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import trim_transparent_edges  # noqa: E402

# Extracted to app.utils.image_utils — backward-compat re-export
from app.utils.image_utils import force_rgb  # noqa: E402

# Extracted to app.utils.helper_utils — backward-compat re-exports
from app.utils.helper_utils import (  # noqa: F401,F403
    APP_ROOT,
    STATIC_DIR,
    UPLOAD_FOLDER,
    GENERATED_FOLDER,
    FONTS_FOLDER,
    LOGOS_FOLDER,
    PLACEHOLDER_PATH,
    PHOTO_PLACEHOLDER_PATH,
    QR_PLACEHOLDER_PATH,
    TEMPLATES_CONFIG,
    FONT_CONFIG_PATH,
    PHOTO_CONFIG_PATH,
    DUPLICATE_CONFIG_PATH,
    DEFAULT_FONTS,
)

# Extracted to app.utils.layout_utils — backward-compat re-exports
from app.utils.layout_utils import (  # noqa: F401,F403
    DPI,
    A4_WIDTH_PX,
    A4_HEIGHT_PX,
    A4_LANDSCAPE_WIDTH_PX,
    A4_LANDSCAPE_HEIGHT_PX,
)

# Extracted to app.utils.text_utils — backward-compat re-export
from app.utils.text_utils import PIL_RAQM_AVAILABLE  # noqa: F401
