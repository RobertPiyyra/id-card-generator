"""
Dashboard route helper functions.

Extracted from app/routes/dashboard_routes.py — contains wrapper functions
that delegate to legacy helpers, and the draw_aligned_colon_pil_helper.

USAGE: These functions are identical copies of those in dashboard_routes.py.
The original definitions shadow these imports at runtime.
"""
import logging
import uuid

from flask import Blueprint

logger = logging.getLogger(__name__)
def _is_admin_session(*args, **kwargs):
    return get_legacy_helpers()._is_admin_session(*args, **kwargs)

def _student_session_school_name(*args, **kwargs):
    return get_legacy_helpers()._student_session_school_name(*args, **kwargs)

def _find_template_dict_by_school(*args, **kwargs):
    return get_legacy_helpers()._find_template_dict_by_school(*args, **kwargs)

def _student_school_access_allowed(*args, **kwargs):
    return get_legacy_helpers()._student_school_access_allowed(*args, **kwargs)

def _current_session_email(*args, **kwargs):
    return get_legacy_helpers()._current_session_email(*args, **kwargs)

def check_deadline_passed(*args, **kwargs):
    return get_legacy_helpers().check_deadline_passed(*args, **kwargs)

def _normalize_school_name(*args, **kwargs):
    return get_legacy_helpers()._normalize_school_name(*args, **kwargs)

def _build_student_form_data(*args, **kwargs):
    return get_legacy_helpers()._build_student_form_data(*args, **kwargs)

def check_duplicate_student(*args, **kwargs):
    return get_legacy_helpers().check_duplicate_student(*args, **kwargs)

def _prepare_uploaded_student_photo_bytes(*args, **kwargs):
    return get_legacy_helpers()._prepare_uploaded_student_photo_bytes(*args, **kwargs)

def _prepare_camera_student_photo_bytes(*args, **kwargs):
    return get_legacy_helpers()._prepare_camera_student_photo_bytes(*args, **kwargs)

def _prepare_student_photo_image_bytes(*args, **kwargs):
    return get_legacy_helpers()._prepare_student_photo_image_bytes(*args, **kwargs)

def upload_image(*args, **kwargs):
    return get_legacy_helpers().upload_image(*args, **kwargs)

def notify_card_ready(*args, **kwargs):
    return get_legacy_helpers().notify_card_ready(*args, **kwargs)

def notify_deadline_approaching(*args, **kwargs):
    return get_legacy_helpers().notify_deadline_approaching(*args, **kwargs)

def get_template_language_direction_from_obj(*args, **kwargs):
    return get_legacy_helpers().get_template_language_direction_from_obj(*args, **kwargs)

def fit_loaded_font_to_single_line(*args, **kwargs):
    return get_legacy_helpers().fit_loaded_font_to_single_line(*args, **kwargs)

def fit_dynamic_font_to_single_line(*args, **kwargs):
    return get_legacy_helpers().fit_dynamic_font_to_single_line(*args, **kwargs)

def _get_render_dynamic_fields(*args, **kwargs):
    return get_legacy_helpers()._get_render_dynamic_fields(*args, **kwargs)

def log_activity(*args, **kwargs):
    return get_legacy_helpers().log_activity(*args, **kwargs)


from models import db, Student, Template, TemplateField, ActivityLog
from utils import (
    UPLOAD_FOLDER, GENERATED_FOLDER, PLACEHOLDER_PATH, FONTS_FOLDER,
    get_template_path, get_card_size, load_template_smart, get_storage_backend,
    get_draw_text_kwargs, get_template_settings, round_photo,
    generate_data_hash, is_valid_font_file, get_anchor_max_text_width,
)

def get_legacy_helpers():
    import app.legacy_app as legacy
    return legacy

def _write_binary_file_atomic(*args, **kwargs):
    return get_legacy_helpers()._write_binary_file_atomic(*args, **kwargs)

def field_within_vertical_bounds(*args, **kwargs):
    return get_legacy_helpers().field_within_vertical_bounds(*args, **kwargs)

def load_static_back_template_image(*args, **kwargs):
    return get_legacy_helpers().load_static_back_template_image(*args, **kwargs)

def order_to_field_key(*args, **kwargs):
    return get_legacy_helpers().order_to_field_key(*args, **kwargs)

def send_email(*args, **kwargs):
    return get_legacy_helpers().send_email(*args, **kwargs)

def get_templates(*args, **kwargs):
    return get_legacy_helpers().get_templates(*args, **kwargs)

def get_available_fonts(*args, **kwargs):
    return get_legacy_helpers().get_available_fonts(*args, **kwargs)

def get_default_font_config(*args, **kwargs):
    return get_legacy_helpers().get_default_font_config(*args, **kwargs)

def get_default_photo_config(*args, **kwargs):
    return get_legacy_helpers().get_default_photo_config(*args, **kwargs)

def get_default_qr_config(*args, **kwargs):
    return get_legacy_helpers().get_default_qr_config(*args, **kwargs)

def load_duplicate_config(*args, **kwargs):
    return get_legacy_helpers().load_duplicate_config(*args, **kwargs)

def resolve_student_card_preview_urls(*args, **kwargs):
    return get_legacy_helpers().resolve_student_card_preview_urls(*args, **kwargs)

def with_cache_bust(*args, **kwargs):
    return get_legacy_helpers().with_cache_bust(*args, **kwargs)

def get_photo_settings_for_orientation(*args, **kwargs):
    return get_legacy_helpers().get_photo_settings_for_orientation(*args, **kwargs)

def get_font_settings_for_orientation(*args, **kwargs):
    return get_legacy_helpers().get_font_settings_for_orientation(*args, **kwargs)

def apply_text_case(*args, **kwargs):
    return get_legacy_helpers().apply_text_case(*args, **kwargs)

def get_initial_flow_y_for_side(*args, **kwargs):
    return get_legacy_helpers().get_initial_flow_y_for_side(*args, **kwargs)

def resolve_field_layout(*args, **kwargs):
    return get_legacy_helpers().resolve_field_layout(*args, **kwargs)

def process_text_for_drawing(*args, **kwargs):
    return get_legacy_helpers().process_text_for_drawing(*args, **kwargs)

def split_label_and_colon(*args, **kwargs):
    return get_legacy_helpers().split_label_and_colon(*args, **kwargs)

def field_consumes_layout_space(*args, **kwargs):
    return get_legacy_helpers().field_consumes_layout_space(*args, **kwargs)

def load_font_dynamic(*args, **kwargs):
    return get_legacy_helpers().load_font_dynamic(*args, **kwargs)

def flip_x_for_text_direction(*args, **kwargs):
    return get_legacy_helpers().flip_x_for_text_direction(*args, **kwargs)

def draw_aligned_colon_pil(*args, **kwargs):
    return get_legacy_helpers().draw_aligned_colon_pil(*args, **kwargs)

def load_student_photo_rgba(*args, **kwargs):
    return get_legacy_helpers().load_student_photo_rgba(*args, **kwargs)

def generate_qr_code(*args, **kwargs):
    return get_legacy_helpers().generate_qr_code(*args, **kwargs)

def generate_barcode_code128(*args, **kwargs):
    return get_legacy_helpers().generate_barcode_code128(*args, **kwargs)

def apply_layout_custom_objects_pil(*args, **kwargs):
    return get_legacy_helpers().apply_layout_custom_objects_pil(*args, **kwargs)

def force_rgb(*args, **kwargs):
    return get_legacy_helpers().force_rgb(*args, **kwargs)

def _get_cached_final_card(*args, **kwargs):
    return get_legacy_helpers()._get_cached_final_card(*args, **kwargs)

def draw_text_gradient(*args, **kwargs):
    return get_legacy_helpers().draw_text_gradient(*args, **kwargs)

def cleanup_old_files(*args, **kwargs):
    return get_legacy_helpers().cleanup_old_files(*args, **kwargs)

def resolve_student_photo_reference(*args, **kwargs):
    return get_legacy_helpers().resolve_student_photo_reference(*args, **kwargs)

def field_advances_layout_flow(*args, **kwargs):
    return get_legacy_helpers().field_advances_layout_flow(*args, **kwargs)

def draw_aligned_colon_pil_v1(*args, **kwargs):
    return get_legacy_helpers().draw_aligned_colon_pil_v1(*args, **kwargs)

def resolve_field_layout_for_side(*args, **kwargs):
    return get_legacy_helpers().resolve_field_layout_for_side(*args, **kwargs)

# Fallback for upload_image
try:
    from cloudinary_config import upload_image
except ImportError:
    upload_image = None

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__)
STORAGE_BACKEND = get_storage_backend()


def draw_aligned_colon_pil_helper(draw, img_width, direction, value_x, y, colon_text, font, fill, lang, gap, anchor_x=None, grow_mode=None, target_image=None, enable_gradient=False, bottom_color=None):
    try:
        draw_aligned_colon_pil(draw, img_width, direction, value_x, y, colon_text, font, fill, lang, gap, anchor_x=anchor_x, grow_mode=grow_mode, target_image=target_image, enable_gradient=enable_gradient, bottom_color=bottom_color)
    except Exception as e:
        logger.warning(f"Error drawing aligned colon: {e}")


