from app.legacy_app import admin_required, super_admin_required, school_admin_required, student_required
import os
import time
import uuid
import logging
import io
import json
import textwrap
import zipfile
import mimetypes
from types import SimpleNamespace
from datetime import datetime, timezone
from collections import defaultdict
from io import BytesIO
import glob
import pandas as pd
import requests
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_file, current_app
from PIL import Image, ImageDraw, ImageOps

import random
import string

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


# Helper function to draw aligned colon in PIL
def draw_aligned_colon_pil_helper(draw, img_width, direction, value_x, y, colon_text, font, fill, lang, gap, anchor_x=None, grow_mode=None, target_image=None, enable_gradient=False, bottom_color=None):
    try:
        draw_aligned_colon_pil(draw, img_width, direction, value_x, y, colon_text, font, fill, lang, gap, anchor_x=anchor_x, grow_mode=grow_mode, target_image=target_image, enable_gradient=enable_gradient, bottom_color=bottom_color)
    except Exception as e:
        logger.warning(f"Error drawing aligned colon: {e}")


# ================== Landing Page Routes ==================
@dashboard_bp.route("/")
def landing():
    """Landing page route"""
    return render_template("landing_page.html")


@dashboard_bp.route("/home")
def home():
    """Alternative home route"""
    return redirect(url_for("dashboard.landing"))


@dashboard_bp.route("/documentation")
def documentation():
    """Documentation page"""
    return render_template("documentation.html")


@dashboard_bp.route("/user_guide")
def user_guide():
    """Admin user guide page"""
    return render_template("user_guide.html")


@dashboard_bp.route("/about")
def about():
    """About page"""
    return render_template("about.html")


@dashboard_bp.route("/api-docs")
@admin_required
def api_docs():
    """API documentation page."""
    return render_template("api_docs.html")


@dashboard_bp.route("/student-portal")
@student_required
def student_portal():
    """Student self-service portal."""
    student = None
    if session.get("student_email"):
        student = Student.query.filter_by(email=session["student_email"]).first()
    return render_template("student_portal.html", student=student)


@dashboard_bp.route("/print-queue")
@admin_required
def print_queue_dashboard():
    return render_template("print_queue.html")


@dashboard_bp.route("/nfc-encoding")
@admin_required
def nfc_dashboard():
    """NFC encoding dashboard."""
    templates_list = get_templates() if 'get_templates' in dir() else []
    return render_template("nfc_dashboard.html", templates=templates_list)


# ================== Admin Dashboard ==================
@dashboard_bp.route("/admin", methods=["GET"])
@admin_required
def admin():
    success = request.args.get("success")
    error = request.args.get("error")
    
    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        per_page = max(10, min(per_page, 200))  # clamp between 10 and 200

        if session.get("admin"):
            # RBAC: Super admin sees all, School admin sees only their school
            query = db.session.query(Student).order_by(Student.created_at.desc())
            if session.get("admin_role") == "school_admin":
                query = query.filter_by(school_name=session.get("admin_school"))
            pagination = query.paginate(page=page, per_page=per_page, error_out=False)
            rows = pagination.items
            total_students = pagination.total
        else:
            rows = []
            total_students = 0

        # FIX: Make sure get_templates() returns a list
        templates_list = get_templates()

        # Log for debugging
        logger.info(f"Admin panel loaded - User: {session.get('student_email') or 'admin'}, Records: {len(rows)}/{total_students}, Page: {page}, Templates: {len(templates_list)}")
        
        available_fonts = get_available_fonts()
        
        # FIX: Handle empty templates list
        if templates_list and isinstance(templates_list, list) and len(templates_list) > 0:
            current_settings = templates_list[0]['font_settings'] if 'font_settings' in templates_list[0] else get_default_font_config()
            photo_settings = templates_list[0]['photo_settings'] if 'photo_settings' in templates_list[0] else get_default_photo_config()
            qr_settings = templates_list[0]['qr_settings'] if 'qr_settings' in templates_list[0] else get_default_qr_config()
        else:
            current_settings = get_default_font_config()
            photo_settings = get_default_photo_config()
            qr_settings = get_default_qr_config()
        
        duplicate_settings = load_duplicate_config()
        
        pdf_sheets = [f for f in os.listdir(GENERATED_FOLDER) if f.endswith('.pdf') and f.startswith('sheet_template_')]
        pdf_sheets_by_template = defaultdict(list)
        for f in pdf_sheets:
            parts = f.split('_')
            if len(parts) >= 4 and parts[0] == 'sheet' and parts[1] == 'template':
                try:
                    template_id = int(parts[2])
                    pdf_sheets_by_template[template_id].append(f)
                except ValueError:
                    continue
        
        template_rows = {}
        for row in rows:
            template_id = row.template_id
            if template_id not in template_rows:
                template_rows[template_id] = []
            template_rows[template_id].append(row)
        
        schools_data = {}
        for row in rows:
            school_name = row.school_name or "Unknown School"
            if school_name not in schools_data:
                schools_data[school_name] = []
            schools_data[school_name].append(row)
        
        # Calculate arrangement info for each template
        template_arrangements = {}
        for template in templates_list:
            orientation = template.get('card_orientation', 'landscape')
            if orientation == 'landscape':
                template_arrangements[template['id']] = "2 columns × 5 rows (10 cards/A4)"
            else:
                template_arrangements[template['id']] = "5 columns × 2 rows (10 cards/A4)"
        
        logger.info("Admin panel loaded successfully")
        return render_template(
            "admin.html",
            rows=rows,
            templates=templates_list,
            template_rows=template_rows,
            available_fonts=available_fonts,
            current_settings=current_settings,
            photo_settings=photo_settings,
            qr_settings=qr_settings,
            duplicate_settings=duplicate_settings,
            pdf_sheets=pdf_sheets,
            pdf_sheets_by_template=pdf_sheets_by_template,
            success=success,
            error=error,
            schools_data=schools_data,
            is_admin=session.get("admin", False),
            template_arrangements=template_arrangements,
            pagination=pagination,
        )
    except Exception as e:
        logger.error(f"Error loading admin panel: {e}")
        return render_template(
            "admin.html",
            error=f"Error loading admin panel: {str(e)}",
            rows=[],
            templates=[],
            template_rows={},
            available_fonts=[],
            current_settings={},
            photo_settings={},
            qr_settings={},
            duplicate_settings={},
            pdf_sheets=[],
            pdf_sheets_by_template=defaultdict(list),
            schools_data={},
            is_admin=session.get("admin", False),
            template_arrangements={},
            pagination=None,
        ), 500


# ================== Compile School Sheets Route ==================
@dashboard_bp.route("/admin/download_compiled_school_pdf/<int:template_id>")
@admin_required
def download_compiled_school_pdf(template_id):
    mode = (request.args.get("mode") or "").strip().lower() or "print"
    if mode not in {"print", "editable"}:
        mode = "print"
    return redirect(url_for("corel.download_compiled_vector_pdf", template_id=template_id, mode=mode))


# ================== Preview Routes ==================
@dashboard_bp.route("/admin/student_preview/<int:student_id>")
@admin_required
def admin_student_preview(student_id):
    """Get student preview data"""
  
    try:
        student = db.session.get(Student, student_id)
        
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404
        
        preview_url, back_preview_url = resolve_student_card_preview_urls(student)
        
        return jsonify({
            "success": True,
            "name": student.name,
            "class_name": student.class_name,
            "preview_url": with_cache_bust(preview_url or url_for('static', filename=os.path.basename(PLACEHOLDER_PATH))),
            "back_preview_url": with_cache_bust(back_preview_url) if back_preview_url else None,
            "pdf_url": getattr(student, 'pdf_url', None),
            "has_preview": preview_url is not None,
            "has_back_preview": back_preview_url is not None,
        })
    except Exception as e:
        logger.error(f"Error fetching student preview: {e}")
        return jsonify({"success": False, "error": "Database error"}), 500


@dashboard_bp.route("/admin/generate_preview/<int:student_id>")
@admin_required
def generate_student_preview(student_id):
    """Generate a fresh preview for a student with Safe Font Fallback"""

    try:
        student = db.session.get(Student, student_id)
        
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404
        
        template = db.session.get(Template, student.template_id) if student.template_id else None
        
        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404
        
        # Load template settings
        font_settings = get_default_font_config()
        photo_settings = get_default_photo_config()
        qr_settings = get_default_qr_config()
        
        if template.font_settings:
            loaded_font = template.font_settings.copy()
            if 'font_color' in loaded_font and 'label_font_color' not in loaded_font:
                loaded_font['label_font_color'] = loaded_font['font_color']
                loaded_font['value_font_color'] = loaded_font['font_color']
            font_settings = {**font_settings, **loaded_font}
        
        if template.photo_settings:
            photo_settings = {**photo_settings, **template.photo_settings}
        
        if template.qr_settings:
            qr_settings = {**qr_settings, **template.qr_settings}
        
        # Adjust settings based on orientation
        photo_settings = get_photo_settings_for_orientation(student.template_id, photo_settings)
        font_settings = get_font_settings_for_orientation(student.template_id, font_settings)
        
        # Generate preview
        template_path = get_template_path(student.template_id)
        if not template_path:
            return jsonify({"success": False, "error": "Template not found"}), 404
        
        try:
            card_width, card_height = get_card_size(student.template_id)
            template_img = load_template_smart(template_path)
            template_img = template_img.resize((card_width, card_height))
            
            draw = ImageDraw.Draw(template_img)
            
            # Load font paths
            FONT_BOLD_PATH = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
            FONT_REGULAR_PATH = os.path.join(FONTS_FOLDER, font_settings["font_regular"])
            
            # Handle font colors safely
            try:
                label_font_color = tuple(font_settings.get("label_font_color", [0, 0, 0]))
                value_font_color = tuple(font_settings.get("value_font_color", [0, 0, 0]))
                colon_font_color = tuple(font_settings.get("colon_font_color", list(label_font_color)))
            except Exception as e:
                logger.warning(f"Error parsing font colors: {e}, using default black")
                label_font_color = (0, 0, 0)
                value_font_color = (0, 0, 0)
                colon_font_color = label_font_color
            
            enable_label_gradient = bool(font_settings.get("enable_label_gradient", False))
            label_fill_bottom = tuple(font_settings.get("label_font_color_bottom", [51, 51, 51]))

            enable_value_gradient = bool(font_settings.get("enable_value_gradient", False))
            value_fill_bottom = tuple(font_settings.get("value_font_color_bottom", [51, 51, 51]))

            enable_colon_gradient = bool(font_settings.get("enable_colon_gradient", False))
            colon_fill_bottom = tuple(font_settings.get("colon_font_color_bottom", [51, 51, 51]))
            
            # Apply text case transformation
            text_case = font_settings.get("text_case", "normal")
            show_label_colon = bool(font_settings.get("show_label_colon", True))
            align_label_colon = bool(font_settings.get("align_label_colon", True))
            label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)
            field_keys = ["NAME", "F_NAME", "CLASS", "DOB", "ADDRESS", "MOBILE"]
            values = [
                apply_text_case(student.name, text_case),
                apply_text_case(student.father_name, text_case),
                apply_text_case(student.class_name, text_case),
                student.dob, 
                apply_text_case(student.address, text_case),
                student.phone 
            ]
            
            # Draw text
            label_x = font_settings["label_x"]
            value_x = font_settings["value_x"]
            line_height = font_settings["line_height"]

            # Template language + direction
            lang = (getattr(template, "language", "english") or "english").strip().lower()
            direction = (getattr(template, "text_direction", "ltr") or "ltr").strip().lower()

            if direction == "rtl" and lang == "english":
                lang = "urdu"

            std_labels = {
                'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
                'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
                'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
                'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
            }
            labels_map = std_labels.get(lang, std_labels['english'])
            labels = [
                labels_map["NAME"],
                labels_map["F_NAME"],
                labels_map["CLASS"],
                labels_map["DOB"],
                labels_map["ADDRESS"],
                labels_map["MOBILE"],
            ]

            p_x = photo_settings.get('photo_x', 0) if photo_settings.get('enable_photo', True) else 0
            p_y = photo_settings.get('photo_y', 0) if photo_settings.get('enable_photo', True) else 0
            p_w = photo_settings.get('photo_width', 0) if photo_settings.get('enable_photo', True) else 0
            p_h = photo_settings.get('photo_height', 0) if photo_settings.get('enable_photo', True) else 0

            current_y = get_initial_flow_y_for_side(template, font_settings, side="front")
            for i, (label, value) in enumerate(zip(labels, values)):
                y = current_y
                field_key = field_keys[i] if i < len(field_keys) else None
                layout_item = resolve_field_layout_for_side(template, field_key, label_x, value_x, y, side="front")
                label_x_eff = layout_item["label_x"]
                value_x_eff = layout_item["value_x"]
                label_y_eff = layout_item["label_y"]
                value_y_eff = layout_item["value_y"]
                label_visible = layout_item["label_visible"]
                value_visible = layout_item["value_visible"]
                label_fill = layout_item.get("label_color") or label_font_color
                value_fill = layout_item.get("value_color") or value_font_color
                colon_fill = layout_item.get("colon_color") or colon_font_color
                label_font_size_eff = max(1, int(layout_item.get("label_font_size") or font_settings["label_font_size"]))
                value_font_size_eff = max(1, int(layout_item.get("value_font_size") or font_settings["value_font_size"]))
                colon_font_size_eff = max(1, int(layout_item.get("colon_font_size") or label_font_size_eff))
                colon_y_eff = layout_item.get("colon_y", label_y_eff)
                colon_x_eff = layout_item.get("colon_x")
                colon_grow_eff = layout_item.get("colon_grow")
                max_value_width = int(get_anchor_max_text_width(
                    card_width=template_img.width,
                    anchor_x=value_x_eff,
                    text_direction=direction,
                    line_y=value_y_eff,
                    line_height=line_height,
                    grow_mode=layout_item['value_grow'],
                    photo_x=p_x,
                    photo_y=p_y,
                    photo_width=p_w,
                    photo_height=p_h,
                    page_margin=20,
                    photo_gap=15,
                    min_width=20,
                ))

                # Text shaping
                display_label = process_text_for_drawing(apply_text_case(label, text_case), lang)
                display_value = process_text_for_drawing(apply_text_case(value, text_case), lang)
                label_text_final, colon_text_final = split_label_and_colon(
                    display_label,
                    lang,
                    direction,
                    include_colon=show_label_colon,
                    align_colon=align_label_colon,
                )

                if not field_consumes_layout_space(layout_item, value):
                    continue
                
                advances_flow = field_advances_layout_flow(layout_item, value, separate_colon=bool(colon_text_final))
                if advances_flow:
                    current_y = max(int(current_y), int(label_y_eff), int(value_y_eff))

                # Language-aware Unicode font loading
                label_font = load_font_dynamic(
                    FONT_BOLD_PATH, label_text_final, template_img.width, label_font_size_eff, language=lang
                )
                colon_font = load_font_dynamic(
                    FONT_BOLD_PATH, colon_text_final or ":", template_img.width, colon_font_size_eff, language=lang
                )
                value_font = load_font_dynamic(
                    FONT_REGULAR_PATH, display_value, max_value_width, value_font_size_eff, language=lang
                )

                # RTL layout: mirror X
                label_draw_x = flip_x_for_text_direction(
                    label_x_eff,
                    label_text_final,
                    label_font,
                    template_img.width,
                    direction,
                    draw=draw,
                    grow_mode=layout_item["label_grow"],
                )
                value_draw_x = flip_x_for_text_direction(
                    value_x_eff,
                    display_value,
                    value_font,
                    template_img.width,
                    direction,
                    draw=draw,
                    grow_mode=layout_item["value_grow"],
                )

                if label_visible:
                    draw_text_gradient(
                        draw,
                        (label_draw_x, label_y_eff),
                        label_text_final,
                        font=label_font,
                        top_color=label_fill,
                        bottom_color=label_fill_bottom,
                        enable_gradient=enable_label_gradient,
                        lang=lang,
                        target_image=template_img,
                        **get_draw_text_kwargs(label_text_final, lang),
                    )
                    draw_aligned_colon_pil_helper(
                        draw,
                        template_img.width,
                        direction,
                        value_x_eff,
                        colon_y_eff,
                        colon_text_final,
                        colon_font,
                        colon_fill,
                        lang,
                        label_colon_gap,
                        anchor_x=colon_x_eff,
                        grow_mode=colon_grow_eff,
                        target_image=template_img,
                        enable_gradient=enable_colon_gradient,
                        bottom_color=colon_fill_bottom,
                    )
                if value_visible:
                    if field_key == "ADDRESS":
                        from app.services.render_service import fit_wrapped_text_pil
                        
                        address_max_lines = int(font_settings.get("address_max_lines", 2))
                        value_char_spacing = layout_item.get("value_char_spacing", 0)
                        
                        # Get font loader
                        def font_loader(size_px):
                            return load_font_dynamic(FONT_REGULAR_PATH, "X", template_img.width, size_px, language=lang)
                        
                        # Fit wrapped text for address
                        best_size, wrapped_lines = fit_wrapped_text_pil(
                            value,
                            font_loader,
                            start_size_px=value_font_size_eff,
                            min_size_px=10,
                            max_width_px=max_value_width,
                            max_lines=address_max_lines,
                            char_spacing=value_char_spacing,
                            draw=draw,
                            lang=lang,
                        )
                        
                        # Load font at best size
                        best_font = load_font_dynamic(FONT_REGULAR_PATH, "X", template_img.width, best_size, language=lang)
                        
                        # Draw each line
                        val_line_height = layout_item.get("value_line_height") or line_height
                        try:
                            val_lh = float(val_line_height)
                        except (ValueError, TypeError):
                            val_lh = 1.2
                        spacing = val_lh if val_lh > 10 else best_size * (val_lh if val_lh > 0 else 1.2)
                        
                        for line in wrapped_lines[:address_max_lines]:
                            line_display = process_text_for_drawing(line, lang)
                            if layout_item["value_visible"]:
                                line_draw_x = flip_x_for_text_direction(
                                    value_x_eff,
                                    line_display,
                                    best_font,
                                    template_img.width,
                                    direction,
                                    draw=draw,
                                    grow_mode=layout_item["value_grow"],
                                )
                                draw_text_gradient(
                                    draw,
                                    (line_draw_x, value_y_eff),
                                    line_display,
                                    font=best_font,
                                    top_color=value_fill,
                                    bottom_color=value_fill_bottom,
                                    enable_gradient=enable_value_gradient,
                                    lang=lang,
                                    target_image=template_img,
                                    **get_draw_text_kwargs(line_display, lang),
                                )
                            value_y_eff += spacing
                            if advances_flow:
                                current_y += spacing
                        continue
                    else:
                        # For non-ADDRESS fields, draw normally
                        draw_text_gradient(
                            draw,
                            (value_draw_x, value_y_eff),
                            display_value,
                            font=value_font,
                            top_color=value_fill,
                            bottom_color=value_fill_bottom,
                            enable_gradient=enable_value_gradient,
                            lang=lang,
                            target_image=template_img,
                            **get_draw_text_kwargs(display_value, lang),
                        )

                if advances_flow:
                    current_y += line_height
            
            # Add photo
            if photo_settings.get("enable_photo", True):
                try:
                    photo_img = load_student_photo_rgba(
                        student,
                        photo_settings["photo_width"],
                        photo_settings["photo_height"],
                        timeout=8,
                        photo_settings=photo_settings,
                    )
                    if photo_img is None:
                        logger.error(f"Failed to load photo for student {getattr(student, 'id', 'unknown')}, using placeholder")
                        if os.path.exists(PLACEHOLDER_PATH):
                            ph_w = int(float(photo_settings.get("photo_width", 100) or 100))
                            ph_h = int(float(photo_settings.get("photo_height", 100) or 100))
                            photo_img = Image.open(PLACEHOLDER_PATH).convert("RGBA")
                            photo_img = ImageOps.fit(
                                photo_img, (ph_w, ph_h), Image.Resampling.LANCZOS
                            )
                    if photo_img:
                        radii = [
                            int(float(photo_settings.get("photo_border_top_left", 0) or 0)),
                            int(float(photo_settings.get("photo_border_top_right", 0) or 0)),
                            int(float(photo_settings.get("photo_border_bottom_right", 0) or 0)),
                            int(float(photo_settings.get("photo_border_bottom_left", 0) or 0))
                        ]
                        photo_img = round_photo(
                            photo_img,
                            radii,
                            border_color=photo_settings.get("photo_frame_color"),
                            border_thickness=2 if photo_settings.get("photo_frame_color") else 0,
                            shape=photo_settings.get("photo_shape", "rectangle"),
                            shape_inset=photo_settings.get("photo_shape_inset", 0),
                        )
                        template_img.paste(photo_img, (photo_settings["photo_x"], photo_settings["photo_y"]), photo_img)
                except Exception as e:
                    logger.error(f"Error adding photo to preview: {e}")
            
            # ---------------- QR CODE ----------------
            if qr_settings.get("enable_qr", False):
                qr_data = "SAMPLE_PREVIEW_DATA"
                if qr_settings.get("qr_data_type") == "student_id":
                    qr_data = json.dumps({
                        "student_id": str(student_id),
                        "name": student.name,
                        "school_name": student.school_name
                    })
                elif qr_settings.get("qr_data_type") == "url":
                    qr_data = qr_settings.get("qr_base_url", "") + str(student_id)
                elif qr_settings.get("qr_data_type") == "text":
                    qr_data = qr_settings.get("qr_custom_text", "Sample Text")
                elif qr_settings.get("qr_data_type") == "json":
                    qr_data = json.dumps({
                        "student_id": student_id,
                        "name": student.name,
                        "class": student.class_name,
                        "school_name": student.school_name
                    })

                qr_x = qr_settings.get("qr_x", 50)
                qr_y = qr_settings.get("qr_y", 50)
                qr_size = qr_settings.get("qr_size", 120)
                qr_img = generate_qr_code(qr_data, qr_settings, qr_size)
                qr_img = force_rgb(qr_img).resize((qr_size, qr_size))
                template_img.paste(qr_img, (qr_x, qr_y))

            # ---------------- BARCODE ----------------
            if qr_settings.get("enable_barcode", False):
                barcode_data = str(student_id)
                barcode_data_type = qr_settings.get("barcode_data_type", "student_id")
                if barcode_data_type == "url":
                    barcode_data = qr_settings.get("barcode_base_url", "") + str(student_id)
                elif barcode_data_type == "text":
                    barcode_data = qr_settings.get("barcode_custom_text", "Sample Text")
                elif barcode_data_type == "json":
                    barcode_data = json.dumps({
                        "student_id": student_id,
                        "name": student.name,
                        "class": student.class_name,
                        "school_name": student.school_name
                    })
                else:
                    barcode_data = str(student_id)

                barcode_x = qr_settings.get("barcode_x", 50)
                barcode_y = qr_settings.get("barcode_y", 200)
                barcode_w = max(40, int(qr_settings.get("barcode_width", 220)))
                barcode_h = max(30, int(qr_settings.get("barcode_height", 70)))
                barcode_img = generate_barcode_code128(barcode_data, qr_settings, width=barcode_w, height=barcode_h)
                barcode_img = force_rgb(barcode_img).resize((barcode_w, barcode_h))
                template_img.paste(barcode_img, (barcode_x, barcode_y))

            apply_layout_custom_objects_pil(template_img, template, font_settings, side="front", language=lang)
            
            # Final JPEG operations
            template_img = force_rgb(template_img)
            
            buf = BytesIO()
            template_img.save(
                buf,
                format="JPEG",
                quality=95,
                subsampling=0,
                optimize=True
            )
            buf.seek(0)
            img_bytes = buf.getvalue()
            
            try:
                if STORAGE_BACKEND == "local":
                    os.makedirs(GENERATED_FOLDER, exist_ok=True)
                    preview_name = f"preview_{student_id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex}.jpg"
                    with open(os.path.join(GENERATED_FOLDER, preview_name), "wb") as fh:
                        fh.write(img_bytes)
                    preview_url = url_for('static', filename=f'generated/{preview_name}')
                else:
                    if upload_image:
                        uploaded = upload_image(img_bytes, folder='generated')
                        preview_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
                    else:
                        preview_url = None
            except Exception as e:
                logger.error(f"Preview save failed: {e}")
                return jsonify({"success": False, "error": "Failed to save preview"}), 500

            back_preview_url = None
            if getattr(template, "is_double_sided", False):
                try:
                    render_scale = 1.0
                    back_img = _get_cached_final_card(
                        template,
                        student,
                        side="back",
                        student_id=student_id,
                        school_name=student.school_name,
                        render_scale=render_scale,
                    )

                    if back_img is not None:
                        back_img = force_rgb(back_img)
                        back_buf = BytesIO()
                        back_img.save(back_buf, format="JPEG", quality=95, subsampling=0, optimize=True)
                        back_buf.seek(0)
                        if STORAGE_BACKEND == "local":
                            back_name = f"preview_back_{student_id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex}.jpg"
                            with open(os.path.join(GENERATED_FOLDER, back_name), "wb") as fh:
                                fh.write(back_buf.getvalue())
                            back_preview_url = url_for('static', filename=f'generated/{back_name}')
                        else:
                            if upload_image:
                                uploaded_back = upload_image(back_buf.getvalue(), folder='generated')
                                back_preview_url = uploaded_back if isinstance(uploaded_back, str) else uploaded_back.get('url')
                except Exception as back_exc:
                    logger.error(f"Back preview generation failed: {back_exc}")

            return jsonify({
                "success": True,
                "preview_url": with_cache_bust(preview_url),
                "back_preview_url": with_cache_bust(back_preview_url) if back_preview_url else None,
                "message": "Preview generated successfully"
            })
            
        except Exception as e:
            logger.error(f"Error generating preview: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({"success": False, "error": f"Preview generation failed: {str(e)}"}), 500
            
    except Exception as e:
        logger.error(f"Database error generating preview: {e}")
        return jsonify({"success": False, "error": "Database error"}), 500


@dashboard_bp.route("/test_preview")
@admin_required
def test_preview():
    """Test route to verify preview generation works"""
    try:
        test_img = Image.new("RGB", (800, 500), color="lightblue")
        draw = ImageDraw.Draw(test_img)
        draw.rectangle([50, 50, 750, 450], outline="blue", width=3)
        draw.text((100, 100), process_text_for_drawing("Preview Test - Working!", "english"), fill="black")
        draw.text((100, 150), process_text_for_drawing(f"Time: {datetime.now()}", "english"), fill="black")
      
        buf = BytesIO()
        test_img.save(buf, format='JPEG', quality=95)
        buf.seek(0)
        try:
            if STORAGE_BACKEND == "local":
                os.makedirs(GENERATED_FOLDER, exist_ok=True)
                test_name = f"test_preview_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex}.jpg"
                with open(os.path.join(GENERATED_FOLDER, test_name), "wb") as fh:
                    fh.write(buf.getvalue())
                test_url = url_for('static', filename=f'generated/{test_name}')
            else:
                if upload_image:
                    uploaded = upload_image(buf.getvalue(), folder='generated')
                    test_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
                else:
                    test_url = None
        except Exception as e:
            return jsonify({"success": False, "error": f"Save failed: {e}"}), 500

        return jsonify({"success": True, "message": "Preview test completed", "test_image": test_url})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@dashboard_bp.route("/admin/download_student_pdf/<int:student_id>")
@admin_required
def download_student_pdf(student_id):
    """Download student PDF"""
  
    try:
        student = db.session.get(Student, student_id)
        
        if not student:
            return jsonify({"success": False, "error": "PDF not found"}), 404

        if getattr(student, 'pdf_url', None):
            return redirect(student.pdf_url)

        if getattr(student, 'generated_filename', None):
            pdf_filename = student.generated_filename
            pdf_path = os.path.join(GENERATED_FOLDER, pdf_filename)
            if os.path.exists(pdf_path):
                if str(pdf_filename).lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    front_img = Image.open(pdf_path).convert("RGB")
                    append_images = []
                    back_name = getattr(student, "back_generated_filename", None)
                    if back_name:
                        back_path = os.path.join(GENERATED_FOLDER, back_name)
                        if os.path.exists(back_path):
                            append_images.append(Image.open(back_path).convert("RGB"))
                    
                    pdf_io = BytesIO()
                    front_img.save(pdf_io, format="PDF", save_all=True, append_images=append_images)
                    pdf_io.seek(0)
                    return send_file(
                        pdf_io,
                        download_name=f"{student.name.replace(' ', '_')}_card.pdf",
                        as_attachment=True,
                        mimetype="application/pdf"
                    )
                return send_file(pdf_path, as_attachment=True)
        return jsonify({"success": False, "error": "Local PDF file not found on disk"}), 404
    except Exception as e:
        logger.error(f"Error downloading student PDF: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ================== Excel Export ==================
@dashboard_bp.route('/download_school_excel/<int:template_id>')
@admin_required
def download_school_excel(template_id):

    try:
        template = db.session.get(Template, template_id)
        if not template:
            flash('Template not found.', 'error')
            return redirect(url_for('dashboard.admin'))
            
        if session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
            flash('You are not authorized to access this school.', 'error')
            return redirect(url_for('dashboard.admin'))

        students = Student.query.filter_by(template_id=template_id).all()

        if not students:
            flash('No students found for this school.', 'warning')
            return redirect(url_for('dashboard.admin'))

        data = []
        for student in students:
            row = {
                'ID': student.id,
                'Name': student.name,
                'Father Name': student.father_name,
                'Class': student.class_name,
                'DOB': student.dob,
                'Address': student.address,
                'Phone': student.phone,
                'Email': student.email,
                'Photo': student.photo_filename,
                'Created At': student.created_at.strftime('%Y-%m-%d %H:%M:%S') if student.created_at else ''
            }

            if student.custom_data:
                from app.services.render_service import normalize_custom_data
                norm_data = normalize_custom_data(student.custom_data)
                for key, val in norm_data.items():
                    header = key.replace('_', ' ').title()
                    row[header] = val

            data.append(row)

        df = pd.DataFrame(data)

        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Student Data')
        
        output.seek(0)
        
        clean_school_name = secure_filename(template.school_name)
        timestamp = datetime.now().strftime("%Y%m%d")
        filename = f"{clean_school_name}_Data_{timestamp}.xlsx"
        
        return send_file(
            output,
            download_name=filename,
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        logger.error(f"Error downloading Excel for template {template_id}: {e}")
        flash(f"Error generating Excel file: {str(e)}", 'error')
        return redirect(url_for('dashboard.admin'))


def _guess_photo_zip_extension(local_path=None, photo_url=None, content_type=None):
    """Pick a stable image extension for photo zip members."""
    ext = ""
    for source in (local_path, photo_url):
        if source:
            candidate = os.path.splitext(str(source).split("?", 1)[0])[1].lower()
            if candidate in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
                ext = candidate
                break
    if not ext and content_type:
        guessed = mimetypes.guess_extension(str(content_type).split(";", 1)[0].strip())
        if guessed:
            ext = guessed.lower()
    if ext == ".jpe":
        ext = ".jpg"
    return ext or ".jpg"


def _unique_zip_member_name(base_name, extension, used_names):
    clean_base = secure_filename(base_name or "") or "student_photo"
    clean_ext = extension if str(extension).startswith(".") else f".{extension}"
    candidate = f"{clean_base}{clean_ext}"
    counter = 2
    while candidate.lower() in used_names:
        candidate = f"{clean_base}_{counter}{clean_ext}"
        counter += 1
    used_names.add(candidate.lower())
    return candidate


@dashboard_bp.route('/download_school_photos_zip/<int:template_id>')
@admin_required
def download_school_photos_zip(template_id):
    """Download all available student photos for one template as a zip."""
    try:
        if not session.get("admin"):
            flash('Admin login required to download school photos.', 'error')
            return redirect(url_for('dashboard.admin'))

        template = db.session.get(Template, template_id)
        if not template:
            flash('Template not found.', 'error')
            return redirect(url_for('dashboard.admin'))

        if session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
            flash('You are not authorized to download photos for this school.', 'error')
            return redirect(url_for('dashboard.admin'))

        students = (
            Student.query
            .filter_by(template_id=template_id)
            .order_by(Student.name.asc(), Student.id.asc())
            .all()
        )

        if not students:
            flash('No students found for this school.', 'warning')
            return redirect(url_for('dashboard.admin'))

        output = BytesIO()
        used_names = set()
        added_count = 0

        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for student in students:
                photo_url, local_path = resolve_student_photo_reference(student)
                photo_bytes = None
                content_type = None

                if local_path and os.path.exists(local_path):
                    try:
                        with open(local_path, "rb") as photo_file:
                            photo_bytes = photo_file.read()
                    except OSError as exc:
                        logger.warning("Unable to read local photo for student %s: %s", student.id, exc)
                elif photo_url:
                    try:
                        response = requests.get(photo_url, timeout=15)
                        response.raise_for_status()
                        photo_bytes = response.content
                        content_type = response.headers.get("Content-Type")
                    except Exception as exc:
                        logger.warning("Unable to download remote photo for student %s: %s", student.id, exc)

                if not photo_bytes:
                    continue

                extension = _guess_photo_zip_extension(local_path, photo_url, content_type)
                member_name = _unique_zip_member_name(
                    getattr(student, "name", None) or f"student_{student.id}",
                    extension,
                    used_names,
                )
                zip_file.writestr(member_name, photo_bytes)
                added_count += 1

        if added_count == 0:
            flash('No student photos were available for this school.', 'warning')
            return redirect(url_for('dashboard.admin'))

        output.seek(0)
        clean_school_name = secure_filename(template.school_name) or f"template_{template_id}"
        timestamp = datetime.now().strftime("%Y%m%d")
        filename = f"{clean_school_name}_Photos_{timestamp}.zip"

        return send_file(
            output,
            download_name=filename,
            as_attachment=True,
            mimetype='application/zip'
        )

    except Exception as e:
        logger.error(f"Error downloading photo zip for template {template_id}: {e}")
        flash(f"Error generating photo zip: {str(e)}", 'error')
        return redirect(url_for('dashboard.admin'))


# ================== Delete School Sheets ==================
@dashboard_bp.route("/admin/delete_school_sheets/<int:template_id>", methods=["POST"])
@admin_required
def delete_school_sheets(template_id):

    try:
        template = db.session.get(Template, template_id)
        if not template:
            return redirect(url_for("dashboard.admin", error="Template not found"))

        if session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
            return redirect(url_for('dashboard.admin', error="You are not authorized to access this school."))

        deleted_count = 0

        # Delete Completed PDFs
        pdf_pattern = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}_*.pdf")
        for file_path in glob.glob(pdf_pattern):
            try:
                os.remove(file_path)
                deleted_count += 1
            except Exception as e:
                logger.error(f"Error deleting {file_path}: {e}")

        # Delete Active PNG Sheet
        png_path = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}.png")
        if os.path.exists(png_path):
            try:
                os.remove(png_path)
                deleted_count += 1
            except Exception as e:
                logger.error(f"Error deleting {png_path}: {e}")

        # Remove positions JSON map
        map_file = os.path.join(GENERATED_FOLDER, f"sheet_map_template_{template_id}.json")
        if os.path.exists(map_file):
            try:
                os.remove(map_file)
            except Exception as e:
                logger.error(f"Error deleting map file: {e}")

        logger.info(f"Deleted {deleted_count} sheets for template {template_id}")
        
        if deleted_count == 0:
            return redirect(url_for("dashboard.admin", error="No sheets found to delete."))
            
        return redirect(url_for("dashboard.admin", success=f"Successfully deleted {deleted_count} sheets for {template.school_name}."))

    except Exception as e:
        logger.error(f"Error clearing sheets: {e}")
        return redirect(url_for("dashboard.admin", error=f"Error clearing sheets: {str(e)}"))


# ================== Cleanup Old Files ==================
@dashboard_bp.route("/admin/run_cleanup", methods=["POST"])
@super_admin_required
def run_cleanup():
        
    try:
        days = 30
        count = cleanup_old_files(days)
        return redirect(url_for("dashboard.admin", success=f"Cleanup Complete: Removed {count} files older than {days} days."))
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return redirect(url_for("dashboard.admin", error=f"Cleanup failed: {str(e)}"))


# ================== Delete Students by Template ==================
@dashboard_bp.route("/delete_all_students_by_template/<int:template_id>", methods=["POST"])
@admin_required
def delete_all_students_by_template(template_id):

    try:
        template = db.session.get(Template, template_id)
        if not template:
            return redirect(url_for("dashboard.admin", error="Template not found"))

        students = Student.query.filter_by(template_id=template_id).all()
        count = len(students)

        if count == 0:
            return redirect(url_for("dashboard.admin", error="No students found for this school."))

        if session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
            return redirect(url_for('dashboard.admin', error="You are not authorized to access this school."))

        for student in students:
            if student.photo_filename:
                p_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
                if os.path.exists(p_path):
                    try: os.remove(p_path)
                    except Exception: pass
            
            if student.generated_filename:
                base_name = os.path.splitext(student.generated_filename)[0]
                for ext in ['.jpg', '.pdf']:
                    g_path = os.path.join(GENERATED_FOLDER, base_name + ext)
                    if os.path.exists(g_path):
                        try: os.remove(g_path)
                        except Exception: pass
            
            db.session.delete(student)

        try:
            png_sheet = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}.png")
            if os.path.exists(png_sheet): os.remove(png_sheet)
            
            pos_file = os.path.join(GENERATED_FOLDER, f"positions_template_{template_id}.txt")
            if os.path.exists(pos_file): os.remove(pos_file)
        except Exception: pass

        db.session.commit()
        
        logger.info(f"Deleted {count} students for template {template_id}")
        return redirect(url_for("dashboard.admin", success=f"Successfully deleted all {count} cards for {template.school_name}."))

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting school cards: {e}")
        return redirect(url_for("dashboard.admin", error=f"Error deleting cards: {str(e)}"))


# ================== Favicon ==================
@dashboard_bp.route('/favicon.ico')
def favicon():
    """Stop the 404 errors for the browser icon"""
    return "", 204

__all__ = ["dashboard_bp"]


@dashboard_bp.route("/index", methods=["GET", "POST"])
@student_required
def index():
    # 2. Init Variables
    generated_url = None
    back_generated_url = None
    download_url = None
    form_data = {}
    success = None
    error = None
    templates = get_templates()
    show_fetch = True
    unique_edit_id = None
    
    # --- ADDED: Deadline Info Variable ---
    deadline_info = None
    # -------------------------------------
    
    # Auto-select template based on school name
    school_name = _student_session_school_name()
    if not school_name and _is_admin_session() and session.get("admin_role") == "school_admin":
        school_name = (session.get("admin_school") or "").strip()
    locked_template = _find_template_dict_by_school(templates, school_name)
    
    is_super_admin = _is_admin_session() and session.get("admin_role") != "school_admin"
    if is_super_admin:
        student_school_locked = False
        selected_template_id = locked_template["id"] if locked_template else None
    else:
        student_school_locked = locked_template is not None
        selected_template_id = locked_template["id"] if locked_template else None
        if student_school_locked:
            templates = [locked_template]
        elif school_name:
            error = f"Your school template '{school_name}' is not available right now. Please contact the administrator."

    # --- ADDED: Calculate Deadline Info for Display ---
    # Determine which template is active (from form POST or default selection)
    current_tid = selected_template_id

    if request.method == "POST" and 'edit_student_id' in session:
        edit_id = session.get('edit_student_id')
        student_for_deadline = db.session.get(Student, edit_id)
        if student_for_deadline:
            current_tid = student_for_deadline.template_id
    elif student_school_locked:
        current_tid = selected_template_id
    else:
        current_tid = request.form.get("template_id") or selected_template_id

    if current_tid:
        try:
            # Find template data
            tmpl = next((t for t in templates if str(t['id']) == str(current_tid)), None)
            if tmpl and tmpl.get('deadline'):
                dd = datetime.fromisoformat(tmpl['deadline'])
                now = datetime.now()
                
                if now < dd:
                    # Active Deadline
                    diff = dd - now
                    days = diff.days
                    hours, remainder = divmod(diff.seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    deadline_info = {
                        "active": True,
                        "expired": False,
                        "date": dd.strftime("%d %B %Y, %I:%M %p"),
                        "days": days,
                        "hours": hours,
                        "minutes": minutes
                    }
                else:
                    # Expired Deadline
                    deadline_info = {
                        "active": True,
                        "expired": True,
                        "date": dd.strftime("%d %B %Y, %I:%M %p")
                    }
        except Exception as e:
            logger.error(f"Error calculating deadline info: {e}")
    # --------------------------------------------------

    if request.method == "POST":
        template_id = None
        try:
            # === LIMIT CHECK: Admins bypass, users limited to 3 ===
            is_editing = 'edit_student_id' in session
            is_admin = session.get("admin") == True
            
            if not is_editing and not is_admin and session.get('student_email'):
                count = Student.query.filter_by(email=session.get('student_email')).count()
                
                # Limit is set to 3 cards
                if count >= 3:
                    return render_template("index.html", 
                                           error="You have reached your limit (3 cards).", 
                                           templates=templates, 
                                           form_data=request.form, 
                                           selected_template_id=int(request.form.get("template_id", 0)),
                                           deadline_info=deadline_info), 403
            
            if is_admin and not is_editing:
                logger.info("Admin bypassed the 3-card generation limit.")
            # === END LIMIT CHECK ===

            # 1. Get Template ID
            if is_editing:
                edit_id = session.get('edit_student_id')
                student = db.session.get(Student, edit_id)
                if not student:
                    raise ValueError("Editing student not found")
            
                template_id = student.template_id   # ✅ ONLY SOURCE
            else:
                posted_template_id = request.form.get("template_id", "0")
                try:
                    posted_template_id = int(posted_template_id)
                except ValueError:
                    posted_template_id = 0
                template_id = selected_template_id if student_school_locked else posted_template_id
                if student_school_locked and posted_template_id and posted_template_id != selected_template_id:
                    logger.warning(
                        "Blocked template override for student %s: posted=%s locked=%s",
                        session.get("student_email"),
                        posted_template_id,
                        selected_template_id,
                    )

            if not template_id:
                raise ValueError("Please select a valid school template.")

            template = db.session.get(Template, template_id)
            if not template:
                raise ValueError("Selected school template was not found.")
            if student_school_locked and not _student_school_access_allowed(getattr(template, "school_name", "")):
                raise PermissionError("You can only generate ID cards for your own school.")

            # 2. Check Deadline
            is_passed, deadline_date = check_deadline_passed(template_id)

            # Only super admin can bypass expired generation deadlines.
            if is_passed and session.get("admin_role") != "super_admin":
                error_msg = f"⛔ The deadline passed on {deadline_date}. Card generation is closed."
                return render_template("index.html", 
                                       error=error_msg, 
                                       templates=templates, 
                                       form_data=request.form, 
                                       selected_template_id=template_id,
                                       deadline_info=deadline_info), 403
            if is_passed and session.get("admin_role") == "super_admin":
                logger.info("Super admin bypassed the generation deadline for template %s.", template_id)

            # Load Settings
            font_settings, photo_settings, qr_settings, card_orientation = get_template_settings(template_id)
            template_path = get_template_path(template_id)
            if not template_path:
                raise ValueError("Template file not found")


            # Capture Form Data
            name = request.form.get("name", "").strip()
            father_name = request.form.get("father_name", "").strip()
            class_name = request.form.get("class_name", "").strip()
            dob = request.form.get("dob", "").strip()
            address = request.form.get("address", "").strip()
            phone = request.form.get("phone", "").strip()
            provided_email = request.form.get("email", "").strip().lower()
            
            form_data = {
                'name': name, 'father_name': father_name, 'class_name': class_name,
                'dob': dob, 'address': address, 'phone': phone, 'template_id': template_id
            }

            # Check Duplicates (Skip check if editing)
            if not is_editing:
                is_dup, dup_msg = check_duplicate_student(form_data)
                if is_dup:
                    return render_template("index.html", error=dup_msg, templates=templates, 
                                           form_data=form_data, selected_template_id=template_id,
                                           deadline_info=deadline_info), 400 # Added deadline_info

            # Handle Photo (Cloudinary on deployed, local filesystem when running locally)
            photo_stored = None
            photo_url = None
            if 'photo' in request.files and request.files['photo'].filename:
                photo = request.files['photo']
                photo_fn = secure_filename(photo.filename)
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

                try:
                    photo_bytes = _prepare_uploaded_student_photo_bytes(photo, photo_settings)
                except ValueError as e:
                    logger.warning(f"Uploaded photo rejected: {e}")
                    return render_template(
                        "index.html",
                        error=str(e),
                        templates=templates,
                        form_data=request.form,
                        selected_template_id=template_id,
                        deadline_info=deadline_info,
                    ), 400
                except Exception as e:
                    logger.error(f"Unexpected photo preparation failure: {e}")
                    return render_template(
                        "index.html",
                        error="Failed to process photo. Please try another image.",
                        templates=templates,
                        form_data=request.form,
                        selected_template_id=template_id,
                        deadline_info=deadline_info,
                    ), 500

                photo_stored = f"{timestamp}_{photo_fn}"

                if STORAGE_BACKEND == "local":
                    try:
                        local_photo_path = os.path.join(UPLOAD_FOLDER, photo_stored)
                        _write_binary_file_atomic(local_photo_path, photo_bytes)
                        photo_url = None
                    except Exception as e:
                        logger.error(f"Failed to save photo locally: {e}")
                        return render_template(
                            "index.html",
                            error="Failed to save photo. Please try again.",
                            templates=templates,
                            form_data=request.form,
                            selected_template_id=template_id,
                            deadline_info=deadline_info,
                        ), 500
                else:
                    # Upload to Cloudinary
                    try:
                        uploaded = upload_image(photo_bytes, folder='photos')
                        photo_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
                    except Exception as e:
                        logger.error(f"Failed to upload photo to Cloudinary: {e}")
                        photo_url = None

                    if not photo_url:
                        return render_template(
                            "index.html",
                            error="Failed to upload photo. Please try again.",
                            templates=templates,
                            form_data=request.form,
                            selected_template_id=template_id,
                            deadline_info=deadline_info,
                        ), 500
            elif request.form.get('photo_data'):
                photo_fn = "camera_capture.jpg"
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

                try:
                    photo_bytes = _prepare_camera_student_photo_bytes(
                        request.form.get('photo_data'),
                        photo_settings,
                    )
                except ValueError as e:
                    logger.warning(f"Captured photo rejected: {e}")
                    return render_template(
                        "index.html",
                        error=str(e),
                        templates=templates,
                        form_data=request.form,
                        selected_template_id=template_id,
                        deadline_info=deadline_info,
                    ), 400
                except Exception as e:
                    logger.error(f"Unexpected captured photo preparation failure: {e}")
                    return render_template(
                        "index.html",
                        error="Failed to process captured photo. Please retake the photo.",
                        templates=templates,
                        form_data=request.form,
                        selected_template_id=template_id,
                        deadline_info=deadline_info,
                    ), 500

                photo_stored = f"{timestamp}_{photo_fn}"

                if STORAGE_BACKEND == "local":
                    try:
                        local_photo_path = os.path.join(UPLOAD_FOLDER, photo_stored)
                        _write_binary_file_atomic(local_photo_path, photo_bytes)
                        photo_url = None
                    except Exception as e:
                        logger.error(f"Failed to save captured photo locally: {e}")
                        return render_template(
                            "index.html",
                            error="Failed to save captured photo. Please try again.",
                            templates=templates,
                            form_data=request.form,
                            selected_template_id=template_id,
                            deadline_info=deadline_info,
                        ), 500
                else:
                    # Upload to Cloudinary
                    try:
                        uploaded = upload_image(photo_bytes, folder='photos')
                        photo_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
                    except Exception as e:
                        logger.error(f"Failed to upload captured photo to Cloudinary: {e}")
                        photo_url = None

                    if not photo_url:
                        return render_template(
                            "index.html",
                            error="Failed to upload captured photo. Please try again.",
                            templates=templates,
                            form_data=request.form,
                            selected_template_id=template_id,
                            deadline_info=deadline_info,
                        ), 500
            elif request.form.get('photo_url'):
                # Support existing photo_url from previous generation
                photo_url = request.form.get('photo_url')
                photo_stored = photo_url  # Store URL identifier
            elif request.form.get('photo_filename'):
                # Legacy local filename
                photo_stored = request.form.get('photo_filename')
            else:
                raise ValueError("Photo is required")

            # === CARD GENERATION LOGIC ===
            try:
                card_width, card_height = get_card_size(template_id)
                template_path = get_template_path(template_id)
                template_img = load_template_smart(template_path).resize((card_width, card_height))
            except Exception as e:
                logger.error(f"Error loading template {template_id} from {template_path}: {e}")
                return render_template("index.html", error=f"Failed to load template: {str(e)}", 
                                       templates=templates, form_data=request.form,
                                       selected_template_id=template_id, deadline_info=deadline_info), 500
            
            draw = ImageDraw.Draw(template_img)

            try:
                L_COLOR = tuple(font_settings.get("label_font_color", [0,0,0]))
                V_COLOR = tuple(font_settings.get("value_font_color", [0,0,0]))
                C_COLOR = tuple(font_settings.get("colon_font_color", list(L_COLOR)))
            except Exception:
                L_COLOR = V_COLOR = (0,0,0)
                C_COLOR = L_COLOR

            enable_label_gradient = bool(font_settings.get("enable_label_gradient", False))
            label_fill_bottom = tuple(font_settings.get("label_font_color_bottom", [51, 51, 51]))

            enable_value_gradient = bool(font_settings.get("enable_value_gradient", False))
            value_fill_bottom = tuple(font_settings.get("value_font_color_bottom", [51, 51, 51]))

            enable_colon_gradient = bool(font_settings.get("enable_colon_gradient", False))
            colon_fill_bottom = tuple(font_settings.get("colon_font_color_bottom", [51, 51, 51]))

            # --- NEW MULTI-LANGUAGE LOGIC START ---
            if template:
                lang = (getattr(template, 'language', 'english') or 'english').strip().lower()
                direction = (getattr(template, 'text_direction', 'ltr') or 'ltr').strip().lower()
            else:
                lang = 'english'
                direction = 'ltr'
            if direction == "rtl" and lang == "english":
                lang = "urdu"

            # Load font paths (language-aware fallback happens in load_font_dynamic)
            FONT_BOLD = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
            FONT_REG = os.path.join(FONTS_FOLDER, font_settings["font_regular"])
            l_font = load_font_dynamic(FONT_BOLD, "X", 10**9, font_settings["label_font_size"], language=lang)

            std_labels = {
                'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
                'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
                'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
                'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
            }
            labels_map = std_labels.get(lang, std_labels['english'])

            all_fields = [
                {'key': 'NAME', 'label': labels_map['NAME'], 'val': name, 'order': 10},
                {'key': 'F_NAME', 'label': labels_map['F_NAME'], 'val': father_name, 'order': 20},
                {'key': 'CLASS', 'label': labels_map['CLASS'], 'val': class_name, 'order': 30},
                {'key': 'DOB', 'label': labels_map['DOB'], 'val': dob, 'order': 40},
                {'key': 'MOBILE', 'label': labels_map['MOBILE'], 'val': phone, 'order': 50},
                {'key': 'ADDRESS', 'label': labels_map['ADDRESS'], 'val': address, 'order': 60}
            ]
            
            custom_data = {}
            if template:
                for field in TemplateField.query.filter_by(template_id=template_id).order_by(TemplateField.display_order.asc()).all():
                    f_name = field.field_name
                    val = request.form.get(f_name, "").strip()
                    custom_data[f_name] = val
                    all_fields.append({
                        'key': field.field_name,
                        'label': field.field_label, # Uses raw label typed by admin
                        'val': val,
                        'order': field.display_order
                    })

            all_fields.sort(key=lambda x: int(x.get('order') or 0))
            
            # --- NEW MULTI-LANGUAGE LOGIC END ---

            # --- PRE-CALCULATE PHOTO BOUNDARIES ---
            p_x = photo_settings.get("photo_x", 0)
            p_y = photo_settings.get("photo_y", 0)
            p_w = photo_settings.get("photo_width", 0)
            p_h = photo_settings.get("photo_height", 0)
            p_bottom = p_y + p_h

            # Draw Loop
            label_x = font_settings["label_x"]
            value_x = font_settings["value_x"]
            current_y = get_initial_flow_y_for_side(template, font_settings, side="front")
            line_height = font_settings["line_height"]
            text_case = font_settings.get("text_case", "normal")
            show_label_colon = bool(font_settings.get("show_label_colon", True))
            address_max_lines = int(font_settings.get("address_max_lines", 2))
            align_label_colon = bool(font_settings.get("align_label_colon", True))
            label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)

            for item in all_fields:
                raw_label = apply_text_case(item['label'], text_case)
                raw_val = apply_text_case(item['val'], text_case)

                # --- TEXT SHAPING ---
                display_label = process_text_for_drawing(raw_label, lang)
                display_val = process_text_for_drawing(raw_val, lang)
                field_key = item.get('key') or order_to_field_key(item.get('order'))
                layout_item = resolve_field_layout(template, field_key, label_x, value_x, current_y)
                if not field_within_vertical_bounds(layout_item, current_y, card_height):
                    continue
                label_x_eff = layout_item["label_x"]
                value_x_eff = layout_item["value_x"]
                label_y_eff = layout_item["label_y"]
                value_y_eff = layout_item["value_y"]
                label_fill = layout_item.get("label_color") or L_COLOR
                value_fill = layout_item.get("value_color") or V_COLOR
                colon_fill = layout_item.get("colon_color") or C_COLOR
                label_font_size_eff = max(1, int(layout_item.get("label_font_size") or font_settings["label_font_size"]))
                value_font_size_eff = max(1, int(layout_item.get("value_font_size") or font_settings["value_font_size"]))
                colon_font_size_eff = max(1, int(layout_item.get("colon_font_size") or label_font_size_eff))
                colon_y_eff = layout_item.get("colon_y", label_y_eff)
                colon_x_eff = layout_item.get("colon_x")
                colon_grow_eff = layout_item.get("colon_grow")

                # --- DRAWING ---
                label_text_final, colon_text_final = split_label_and_colon(
                    display_label,
                    lang,
                    direction,
                    include_colon=show_label_colon,
                    align_colon=align_label_colon,
                )
                if not field_consumes_layout_space(layout_item, raw_val):
                    continue
                advances_flow = field_advances_layout_flow(layout_item, raw_val, separate_colon=bool(colon_text_final))
                if advances_flow:
                    current_y = max(int(current_y), int(label_y_eff), int(value_y_eff))
                l_font = load_font_dynamic(FONT_BOLD, label_text_final, 10**9, label_font_size_eff, language=lang)
                colon_font = load_font_dynamic(FONT_BOLD, colon_text_final or ":", 10**9, colon_font_size_eff, language=lang)
                if layout_item["label_visible"]:
                    label_draw_x = flip_x_for_text_direction(
                        label_x_eff,
                        label_text_final,
                        l_font,
                        card_width,
                        direction,
                        draw=draw,
                        grow_mode=layout_item["label_grow"],
                    )
                    draw_text_gradient(
                        draw,
                        (label_draw_x, label_y_eff),
                        label_text_final,
                        font=l_font,
                        top_color=label_fill,
                        bottom_color=label_fill_bottom,
                        enable_gradient=enable_label_gradient,
                        lang=lang,
                        target_image=template_img,
                        **get_draw_text_kwargs(label_text_final, lang),
                    )
                    draw_aligned_colon_pil_helper(
                        draw,
                        card_width,
                        direction,
                        value_x_eff,
                        colon_y_eff,
                        colon_text_final,
                        colon_font,
                        colon_fill,
                        lang,
                        label_colon_gap,
                        anchor_x=colon_x_eff,
                        grow_mode=colon_grow_eff,
                        target_image=template_img,
                        enable_gradient=enable_colon_gradient,
                        bottom_color=colon_fill_bottom,
                    )

                max_w = int(get_anchor_max_text_width(
                    card_width=card_width,
                    anchor_x=value_x_eff,
                    text_direction=direction,
                    line_y=value_y_eff,
                    line_height=line_height,
                    grow_mode=layout_item["value_grow"],
                    photo_x=p_x,
                    photo_y=p_y,
                    photo_width=p_w,
                    photo_height=p_h,
                    page_margin=20,
                    photo_gap=15,
                    min_width=20,
                ))

                if field_key == 'ADDRESS':
                    from app.services.render_service import fit_wrapped_text_pil
                    
                    value_char_spacing = layout_item.get("value_char_spacing", 0)
                    
                    # Get font loader
                    def font_loader(size_px):
                        return load_font_dynamic(FONT_REG, "X", card_width, size_px, language=lang)
                    
                    # Fit wrapped text for address
                    best_size, wrapped_lines = fit_wrapped_text_pil(
                        raw_val,
                        font_loader,
                        start_size_px=value_font_size_eff,
                        min_size_px=10,
                        max_width_px=max_w,
                        max_lines=address_max_lines,
                        char_spacing=value_char_spacing,
                        draw=draw,
                        lang=lang,
                    )
                    
                    # Load font at best size
                    addr_font = load_font_dynamic(FONT_REG, "X", card_width, best_size, language=lang)
                    
                    # Draw each line
                    val_line_height = layout_item.get("value_line_height") or line_height
                    try:
                        val_lh = float(val_line_height)
                    except (ValueError, TypeError):
                        val_lh = 1.2
                    spacing = val_lh if val_lh > 10 else best_size * (val_lh if val_lh > 0 else 1.2)
                    
                    for line in wrapped_lines[:address_max_lines]:
                        line_display = process_text_for_drawing(line, lang)
                        if layout_item["value_visible"]:
                            value_draw_x = flip_x_for_text_direction(
                                value_x_eff,
                                line_display,
                                addr_font,
                                card_width,
                                direction,
                                draw=draw,
                                grow_mode=layout_item["value_grow"],
                            )
                            draw_text_gradient(
                                draw,
                                (value_draw_x, value_y_eff),
                                line_display,
                                font=addr_font,
                                top_color=value_fill,
                                bottom_color=value_fill_bottom,
                                enable_gradient=enable_value_gradient,
                                lang=lang,
                                target_image=template_img,
                                **get_draw_text_kwargs(line_display, lang),
                            )
                        value_y_eff += spacing
                        if advances_flow:
                            current_y += spacing

                # --- STANDARD LOGIC FOR OTHER FIELDS ---
                else:
                    # For all non-address fields: shrink font to fit in one line, no wrapping
                    curr_size = value_font_size_eff
                    min_size = 10
                    v_font = None
                    while curr_size >= min_size:
                        temp_font = load_font_dynamic(FONT_REG, display_val, max_w, curr_size, language=lang)
                        text_len = draw.textlength(display_val, font=temp_font, **get_draw_text_kwargs(display_val, lang))
                        if text_len <= max_w:
                            v_font = temp_font
                            break
                        curr_size -= 2
                    if v_font is None:
                        v_font = load_font_dynamic(FONT_REG, display_val, max_w, min_size, language=lang)
                    if layout_item["value_visible"]:
                        value_draw_x = flip_x_for_text_direction(
                            value_x_eff,
                            display_val,
                            v_font,
                            card_width,
                            direction,
                            draw=draw,
                            grow_mode=layout_item["value_grow"],
                        )
                        draw_text_gradient(
                            draw,
                            (value_draw_x, value_y_eff),
                            display_val,
                            font=v_font,
                            top_color=value_fill,
                            bottom_color=value_fill_bottom,
                            enable_gradient=enable_value_gradient,
                            lang=lang,
                            target_image=template_img,
                            **get_draw_text_kwargs(display_val, lang),
                        )
                    if advances_flow:
                        current_y += line_height

            # Photo & QR (fetch remote photo if needed)
            if photo_settings.get("enable_photo", True):
                try:
                    photo_subject = SimpleNamespace(
                        photo_url=photo_url or None,
                        photo_filename=photo_stored or None,
                        image_url=None,
                    )
                    ph = load_student_photo_rgba(
                        photo_subject,
                        photo_settings["photo_width"],
                        photo_settings["photo_height"],
                        timeout=10,
                        photo_settings=photo_settings,
                    )
                    if ph is None:
                        logger.error(f"Failed to load photo for photo_subject, using placeholder")
                        if os.path.exists(PLACEHOLDER_PATH):
                            ph_w = int(float(photo_settings.get("photo_width", 100) or 100))
                            ph_h = int(float(photo_settings.get("photo_height", 100) or 100))
                            ph = Image.open(PLACEHOLDER_PATH).convert("RGBA")
                            ph = ImageOps.fit(
                                ph, (ph_w, ph_h), Image.Resampling.LANCZOS
                            )
                    if ph:
                        radii = [int(float(photo_settings.get(f"photo_border_{k}", 0) or 0)) for k in ["top_left", "top_right", "bottom_right", "bottom_left"]]
                        ph = round_photo(
                            ph,
                            radii,
                            border_color=photo_settings.get("photo_frame_color"),
                            border_thickness=2 if photo_settings.get("photo_frame_color") else 0,
                            shape=photo_settings.get("photo_shape", "rectangle"),
                            shape_inset=photo_settings.get("photo_shape_inset", 0),
                        )
                        template_img.paste(ph, (photo_settings["photo_x"], photo_settings["photo_y"]), ph)
                except Exception as e:
                    logger.error(f"Error adding photo: {e}")

            data_hash = generate_data_hash(form_data, photo_stored)
            qr_id = data_hash[:10]

            if qr_settings.get("enable_qr", False):
                if qr_settings.get("qr_data_type") == "url":
                    base = qr_settings.get("qr_base_url", "")
                    if not base.endswith('/'):
                        base += '/'
                    qr_payload = base + qr_id
                elif qr_settings.get("qr_data_type") == "text":
                    qr_payload = qr_settings.get("qr_custom_text", "Sample Text")
                elif qr_settings.get("qr_data_type") == "json":
                    qr_payload = json.dumps({
                        "student_id": qr_id,
                        "name": name,
                        "class": class_name,
                        "school_name": school_name
                    })
                else:
                    qr_payload = qr_id

                code_size = qr_settings.get("qr_size", 120)
                code_x = qr_settings.get("qr_x", 50)
                code_y = qr_settings.get("qr_y", 50)
                qr_img = generate_qr_code(qr_payload, qr_settings, code_size)
                qr_img = qr_img.resize((code_size, code_size))
                template_img.paste(qr_img, (code_x, code_y))

            if qr_settings.get("enable_barcode", False):
                barcode_type = qr_settings.get("barcode_data_type", "student_id")
                if barcode_type == "url":
                    base = qr_settings.get("barcode_base_url", "")
                    if not base.endswith('/'):
                        base += '/'
                    barcode_payload = base + qr_id
                elif barcode_type == "text":
                    barcode_payload = qr_settings.get("barcode_custom_text", "Sample Text")
                elif barcode_type == "json":
                    barcode_payload = json.dumps({
                        "student_id": qr_id,
                        "name": name,
                        "class": class_name,
                        "school_name": school_name
                    })
                else:
                    barcode_payload = qr_id

                barcode_x = qr_settings.get("barcode_x", 50)
                barcode_y = qr_settings.get("barcode_y", 200)
                barcode_w = max(40, int(qr_settings.get("barcode_width", 220)))
                barcode_h = max(30, int(qr_settings.get("barcode_height", 70)))
                barcode_img = generate_barcode_code128(barcode_payload, qr_settings, width=barcode_w, height=barcode_h)
                template_img.paste(barcode_img, (barcode_x, barcode_y))

            apply_layout_custom_objects_pil(template_img, template, font_settings, side="front", language=lang)

            side_render_student = SimpleNamespace(
                name=name,
                father_name=father_name,
                class_name=class_name,
                dob=dob,
                address=address,
                phone=phone,
                photo_url=photo_url,
                photo_filename=photo_stored if photo_stored and not str(photo_stored).startswith("http") else None,
                custom_data=custom_data,
                school_name=school_name,
            )

            student_id = getattr(side_render_student, "id", None)
            render_scale = 1.0
            school_name = getattr(template, "school_name", None) or getattr(student, "school_name", "")



            front_template_img = _get_cached_final_card(
                template,
                side_render_student,
                side="front",
                student_id=student_id,
                school_name=school_name,
                render_scale=render_scale,
            )

            if front_template_img is not None:
                template_img = front_template_img

            # --- Robust back_template_img handling ---

            # Always define back_template_img before use
            back_template_img = None
            if getattr(template, "is_double_sided", False):
                try:
                    back_template_img = _get_cached_final_card(
                        template,
                        side_render_student,
                        side="back",
                        student_id=student_id,
                        school_name=school_name,
                        render_scale=render_scale,
                    ) or load_static_back_template_image(template, card_width, card_height)
                except Exception as e:
                    logger.warning(f"Failed to render back card image: {e}")
                    back_template_img = None
            else:
                back_template_img = None

            # --- End robust back_template_img handling ---

            # =========================================================
            # SAVE OUTPUT (Cloudinary on deployed, local filesystem when running locally)
            # =========================================================
            
            # --- FIX: Convert to RGB before saving as JPEG ---
            # This fixes "cannot write mode RGBA as JPEG"
            if template_img.mode == 'RGBA':
                # Create a white background for transparency
                background = Image.new("RGB", template_img.size, (255, 255, 255))
                background.paste(template_img, mask=template_img.split()[3])
                template_img = background
            else:
                template_img = template_img.convert("RGB")
            if back_template_img is not None:
                if back_template_img.mode == 'RGBA':
                    back_background = Image.new("RGB", back_template_img.size, (255, 255, 255))
                    back_background.paste(back_template_img, mask=back_template_img.split()[3])
                    back_template_img = back_background
                else:
                    back_template_img = back_template_img.convert("RGB")
            elif getattr(template, "is_double_sided", False):
                logger.warning("Double-sided template but back image missing; skipping back image.")
            # -------------------------------------------------

            # Save image to bytes
            jpg_buf = io.BytesIO()
            template_img.save(jpg_buf, format='JPEG', quality=95)
            jpg_buf.seek(0)
            jpg_bytes = jpg_buf.getvalue()
            back_jpg_bytes = None
            if back_template_img is not None:
                back_jpg_buf = io.BytesIO()
                back_template_img.save(back_jpg_buf, format='JPEG', quality=95)
                back_jpg_buf.seek(0)
                back_jpg_bytes = back_jpg_buf.getvalue()

            pdf_buf = io.BytesIO()
            if back_template_img is not None:
                template_img.save(pdf_buf, format='PDF', save_all=True, append_images=[back_template_img], quality=95)
            else:
                template_img.save(pdf_buf, format='PDF', quality=95)
            pdf_buf.seek(0)
            pdf_bytes = pdf_buf.getvalue()

            image_url = None
            back_image_url = None
            pdf_url = None
            generated_filename = None
            back_generated_filename = None

            if STORAGE_BACKEND == "local":
                try:
                    os.makedirs(GENERATED_FOLDER, exist_ok=True)
                    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
                    base = f"card_{template_id}_{ts}_{uuid.uuid4().hex}"
                    jpg_name = f"{base}.jpg"
                    pdf_name = f"{base}.pdf"
                    back_jpg_name = f"{base}_back.jpg" if back_jpg_bytes is not None else None

                    with open(os.path.join(GENERATED_FOLDER, jpg_name), "wb") as fh:
                        fh.write(jpg_bytes)
                    if back_jpg_name and back_jpg_bytes is not None:
                        with open(os.path.join(GENERATED_FOLDER, back_jpg_name), "wb") as fh:
                            fh.write(back_jpg_bytes)
                    with open(os.path.join(GENERATED_FOLDER, pdf_name), "wb") as fh:
                        fh.write(pdf_bytes)

                    generated_filename = pdf_name  # legacy field; preview jpg is derived from this
                    back_generated_filename = back_jpg_name
                    generated_url = with_cache_bust(url_for('static', filename=f'generated/{jpg_name}'))
                    download_url = url_for('static', filename=f'generated/{pdf_name}')
                    if back_jpg_name:
                        back_generated_url = with_cache_bust(url_for('static', filename=f'generated/{back_jpg_name}'))
                except Exception as e:
                    logger.error(f"Local save failed: {e}")
                    return render_template(
                        "index.html",
                        error=f"Failed to save image: {str(e)}",
                        templates=templates,
                        form_data=request.form,
                        selected_template_id=template_id,
                        deadline_info=deadline_info,
                    ), 500
            else:
                # Upload to Cloudinary
                try:
                    jpg_result = upload_image(jpg_bytes, folder='generated')
                    image_url = jpg_result if isinstance(jpg_result, str) else jpg_result.get('url')
                    if back_jpg_bytes is not None:
                        back_jpg_result = upload_image(back_jpg_bytes, folder='generated')
                        back_image_url = back_jpg_result if isinstance(back_jpg_result, str) else back_jpg_result.get('url')

                    pdf_result = upload_image(pdf_bytes, folder='generated', resource_type='raw')
                    pdf_url = pdf_result if isinstance(pdf_result, str) else pdf_result.get('url')
                except Exception as e:
                    logger.error(f"Cloudinary upload failed: {e}")
                    return render_template("index.html", error=f"Failed to save image: {str(e)}",
                                           templates=templates, form_data=request.form,
                                           selected_template_id=template_id, deadline_info=deadline_info), 500

                generated_url = with_cache_bust(image_url)
                download_url = pdf_url
                if back_image_url:
                    back_generated_url = with_cache_bust(back_image_url)
            # =========================================================

            # DB Insert/Update
            if is_editing:
                edit_id = session['edit_student_id']
                student = db.session.get(Student, edit_id)
                if student:
                    student.name = name
                    student.father_name = father_name
                    student.class_name = class_name
                    student.dob = dob
                    student.address = address
                    student.phone = phone
                    if is_admin and provided_email:
                        student.email = provided_email
                    if STORAGE_BACKEND == "local":
                        student.photo_url = None
                        student.photo_filename = photo_stored
                        student.image_url = None
                        student.back_image_url = None
                        student.pdf_url = None
                        student.generated_filename = generated_filename
                        student.back_generated_filename = back_generated_filename
                    else:
                        student.photo_url = photo_url  # Store Cloudinary URL
                        student.image_url = image_url  # Store generated card image URL
                        student.back_image_url = back_image_url
                        student.pdf_url = pdf_url  # Store generated PDF URL
                    student.created_at = datetime.now(timezone.utc)
                    student.data_hash = data_hash
                    student.template_id = template_id
                    student.school_name = school_name
                    student.custom_data = custom_data
                    db.session.commit()
                    unique_edit_id = edit_id
                    success = "Card Updated Successfully!"
                    session.pop('edit_student_id', None)
            else:
                # New Record
                final_email = (provided_email or None) if is_admin else session.get('student_email')

                student = Student(
                    name=name,
                    father_name=father_name,
                    class_name=class_name,
                    dob=dob,
                    address=address,
                    phone=phone,
                    photo_url=None if STORAGE_BACKEND == "local" else photo_url,
                    photo_filename=photo_stored if STORAGE_BACKEND == "local" else None,
                    image_url=None if STORAGE_BACKEND == "local" else image_url,
                    back_image_url=None if STORAGE_BACKEND == "local" else back_image_url,
                    pdf_url=None if STORAGE_BACKEND == "local" else pdf_url,
                    generated_filename=generated_filename if STORAGE_BACKEND == "local" else None,
                    back_generated_filename=back_generated_filename if STORAGE_BACKEND == "local" else None,
                    created_at=datetime.now(timezone.utc),
                    data_hash=data_hash,
                    template_id=template_id,
                    school_name=school_name,
                    email=final_email,
                    custom_data=custom_data
                )
                db.session.add(student)
                db.session.commit()
                
                unique_edit_id = student.id
                # NO CALL TO add_card_to_sheet() HERE

                # Email Logic
                try:
                    if student.email:
                        email_subject = "ID Card Generated Successfully - Save Your ID"
                        email_body = f"""Hello {student.name},

Your ID card for {student.school_name} has been generated successfully.

--------------------------------------------------
YOUR UNIQUE ID: {unique_edit_id}
--------------------------------------------------

Please save this ID number. You will need it if you ever want to edit your ID card details later.

Regards,
ID Card Admin
NOOR GRAPHICS AND PRINTERS
"""
                        # --- UPDATED: Check if email sent successfully ---
                        if send_email(student.email, email_subject, email_body):
                            logger.info(f"Email success confirmed for {student.email}")
                        else:
                            logger.error(f"Email FAILED for {student.email} (Check server logs for details)")
                        # -------------------------------------------------

                except Exception as email_err:
                    logger.error(f"Crash in email logic: {email_err}")

                if student.email:
                    success = f"Card Generated Successfully! (ID: {unique_edit_id}). \n An email with this ID has been sent to you."
                else:
                    success = f"Card Generated Successfully! (ID: {unique_edit_id})."
            # Clear Form
            form_data = { 'template_id': template_id }

        except Exception as e:
            db.session.rollback()
            error = f"Error: {str(e)}"
            logger.exception("Error in index POST")
            safe_template_id = template_id if template_id else selected_template_id
            return render_template("index.html", error=error, templates=templates, 
                                   form_data=request.form, selected_template_id=safe_template_id,
                                   deadline_info=deadline_info), 500 # Added deadline_info

    return render_template("index.html", generated_url=generated_url, back_generated_url=back_generated_url, download_url=download_url,
                           form_data=form_data, success=success, error=error, templates=templates, 
                           show_fetch=show_fetch, unique_edit_id=unique_edit_id, 
                           selected_template_id=selected_template_id, deadline_info=deadline_info) # Added deadline_info


@dashboard_bp.route("/fetch_record", methods=["POST"])
def fetch_record():
    unique_id_raw = request.form.get("unique_id", "").strip()
    if not unique_id_raw:
        logger.error("Unique ID is required")
        return jsonify({"success": False, "error": "Unique ID is required"}), 400

    # Allow users to type IDs using non-ASCII digits (e.g., Urdu/Arabic-Indic digits).
    # Browsers may submit these digits as Unicode; SQLite primary keys are numeric, so normalize.
    try:
        import unicodedata

        normalized = []
        for ch in str(unique_id_raw):
            if ch.isspace():
                continue
            if ch.isdigit():
                try:
                    normalized.append(str(unicodedata.digit(ch)))
                except Exception:
                    normalized.append(ch)
            else:
                normalized.append(ch)
        unique_id_norm = "".join(normalized)
    except Exception:
        unique_id_norm = unique_id_raw

    if not str(unique_id_norm).isdigit():
        return jsonify({"success": False, "error": "Unique ID must be numeric"}), 400

    unique_id = int(unique_id_norm)
  
    try:
        student = db.session.get(Student, unique_id)
        
        logger.info(f"Fetching record for unique_id: {unique_id} (raw: {unique_id_raw}), found: {student}")
        
        if student:
            if _is_admin_session():
                edit_url = url_for('dashboard.edit_student', student_id=student.id)
                return jsonify({"success": True, "edit_url": edit_url})

            session_email = session.get('student_email')
            if not _student_school_access_allowed(student.school_name):
                logger.warning(
                    "Blocked record access for %s: record school=%s locked school=%s",
                    session_email,
                    student.school_name,
                    _student_session_school_name(),
                )
                return jsonify({"success": False, "error": "This ID card belongs to another school."}), 403
          
            # If student record has no email, allow access for logged-in users
            if not student.email:
                logger.warning(f"Student record {student.id} has no email associated")
                
                # If user is logged in and has less than 3 records, allow access
                if session_email:
                    user_record_count = Student.query.filter_by(email=session_email).count()
                    
                    if user_record_count < 3:
                        # Update record with session email
                        student.email = session_email
                        db.session.commit()
                        logger.info(f"Associated student {student.id} with email {session_email}")
                        edit_url = url_for('dashboard.edit_student', student_id=student.id)
                        return jsonify({"success": True, "edit_url": edit_url})
                    else:
                        return jsonify({"success": False, "error": "You have reached your record limit (3)."}), 403
                else:
                    return jsonify({"success": False, "error": "Please login first to access this record."}), 403
          
            # If record has email, check authorization
            if session_email and student.email.lower() == session_email.lower():
                edit_url = url_for('dashboard.edit_student', student_id=student.id)
                return jsonify({"success": True, "edit_url": edit_url})
            else:
                logger.warning(f"Email mismatch - Student: {student.email}, Session: {session_email}")
                return jsonify({"success": False, "error": "You are not authorized to edit this record"}), 403
        else:
            logger.error(f"No record found for unique ID {unique_id}")
            return jsonify({"success": False, "error": "No record found for this unique ID"}), 404
    except Exception as e:
        logger.error(f"Error fetching record for unique ID {unique_id}: {e}")
        return jsonify({"success": False, "error": f"Error fetching record: {str(e)}"}), 500


@dashboard_bp.route("/clear_edit_session", methods=["POST"])
def clear_edit_session():
    session.pop('edit_student_id', None)
    return jsonify({"success": True})


@dashboard_bp.route("/edit/<int:student_id>", methods=["GET", "POST"])
@student_required
def edit_student(student_id):
    generated_url = None
    back_generated_url = None
    download_url = None
    form_data = {}
    success = None
    error = None
    templates = get_templates()
    
    # 1. GET Request
    try:
        student = db.session.get(Student, student_id)
        
        if not student:
            error = f"No student found with ID {student_id}"
            logger.error(error)
            return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                 generated_url=generated_url, download_url=download_url, success=success), 404
        
        # --- UPDATED AUTHORIZATION CHECK ---
        # Goal: Admins = Access All. Students = Access Own. Bulk (No Email) = Admin Only.
        
        is_admin = session.get("admin")
        current_user_email = session.get('student_email', '').strip().lower()
        record_email = (student.email or '').strip().lower()
        
        # Check ownership (only valid if record actually has an email)
        is_owner = (current_user_email and record_email and current_user_email == record_email)

        if not is_admin:
            # If not Admin, check strict rules:
            if not record_email:
                 # Case: Bulk Card (No Email) -> BLOCK
                 error = "Restricted: Bulk-generated cards can only be edited by an Administrator."
                 logger.warning(f"Unauthorized edit attempt on Bulk Card {student_id} by {current_user_email}")
                 return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                      generated_url=generated_url, download_url=download_url, success=success), 403
            
            if not is_owner:
                 # Case: Email exists but doesn't match logged-in user -> BLOCK
                 error = "You are not authorized to edit this record."
                 logger.warning(f"Unauthorized edit attempt on Student {student_id} by {current_user_email}")
                 return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                      generated_url=generated_url, download_url=download_url, success=success), 403
            if not _student_school_access_allowed(student.school_name):
                error = "This ID card belongs to another school."
                logger.warning(
                    "Blocked cross-school edit attempt on Student %s by %s",
                    student_id,
                    current_user_email,
                )
                return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                     generated_url=generated_url, download_url=download_url, success=success), 403

        # -----------------------------------

        # --- STRICT DEADLINE CHECK ---
        if student and student.template_id:
            is_passed, deadline_date = check_deadline_passed(student.template_id)
            
            if is_passed:
                error = f"⛔ The deadline for editing this ID card passed on {deadline_date}. Updates are disabled."
                # If it's a POST (trying to save), block strictly (Admin exception optional)
                if request.method == "POST" and not is_admin:
                    return render_template("edit.html", error=error, templates=templates, form_data=form_data), 403
                else:
                    # If it's a GET (just viewing), show flash error but maybe allow read-only
                    flash(error, 'error')
        # -----------------------------
        
        # Authorization successful - populate form data
        form_data = {
            'id': student.id,
            'name': student.name,
            'father_name': student.father_name,
            'class_name': student.class_name,
            'dob': student.dob,
            'address': student.address,
            'phone': student.phone,
            'photo_filename': student.photo_filename,
            'generated_filename': student.generated_filename,
            'template_id': student.template_id,
            'school_name': student.school_name,
            'custom_data': student.custom_data or {} 
        }
        
        preview_url, back_preview_url = resolve_student_card_preview_urls(student)
        generated_url = with_cache_bust(preview_url or url_for('static', filename=os.path.basename(PLACEHOLDER_PATH)))
        back_generated_url = with_cache_bust(back_preview_url) if back_preview_url else None
        
        download_url = student.pdf_url if student.pdf_url else None
    
    except Exception as e:
        error = f"Error fetching student data: {str(e)}"
        logger.error(error)
        return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                             generated_url=generated_url, download_url=download_url, success=success), 500
  
    if request.method == "POST":
        try:
            student = db.session.get(Student, student_id)
            
            # --- UPDATED POST AUTHORIZATION ---
            if not session.get("admin"):
                current_user_email = session.get('student_email', '').strip().lower()
                record_email = (student.email or '').strip().lower()
                
                # Strict check: Block if no email (Bulk) OR Email mismatch
                if not record_email or current_user_email != record_email:
                     error = "You are not authorized to edit this record."
                     return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                          generated_url=generated_url, download_url=download_url, success=success), 403
                if not _student_school_access_allowed(student.school_name):
                     error = "This ID card belongs to another school."
                     return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                          generated_url=generated_url, download_url=download_url, success=success), 403
            # ----------------------------------

        except Exception as e:
            error = f"Database error: {str(e)}"
            logger.error(error)
            return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                 generated_url=generated_url, download_url=download_url, success=success), 500
      
        existing_photo_filename = form_data.get('photo_filename', '')
        template_id = student.template_id
        school_name = next((t['school_name'] for t in templates if t['id'] == template_id), None)
        template_path = get_template_path(template_id)
        if not template_path:
            error = "No template found. Please contact administrator."
            logger.error(f"Template not found for ID {template_id}")
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 400
      
        font_settings, photo_settings, qr_settings, card_orientation = get_template_settings(template_id)
        card_width, card_height = get_card_size(template_id)

        # Template language + direction for text shaping + RTL layout
        template_obj = db.session.get(Template, template_id)
        if template_obj:
            lang = (getattr(template_obj, "language", "english") or "english").strip().lower()
            direction = (getattr(template_obj, "text_direction", "ltr") or "ltr").strip().lower()
        else:
            lang = "english"
            direction = "ltr"
        if direction == "rtl" and lang == "english":
            lang = "urdu"
      
        FONT_BOLD_PATH = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
        FONT_REGULAR_PATH = os.path.join(FONTS_FOLDER, font_settings["font_regular"])
      
        if not is_valid_font_file(FONT_BOLD_PATH) or not is_valid_font_file(FONT_REGULAR_PATH):
            error = f"Invalid font file: {font_settings['font_bold']} or {font_settings['font_regular']}"
            logger.error(error)
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 500
      
        # Get font colors from settings
        label_font_color = font_settings.get("label_font_color", [0, 0, 0])
        value_font_color = font_settings.get("value_font_color", [0, 0, 0])
      
        try:
            LABEL_FONT_COLOR = tuple(label_font_color)
        except Exception as e:
            LABEL_FONT_COLOR = (0, 0, 0)
      
        try:
            VALUE_FONT_COLOR = tuple(value_font_color)
        except Exception as e:
            VALUE_FONT_COLOR = (0, 0, 0)
        try:
            COLON_FONT_COLOR = tuple(font_settings.get("colon_font_color", list(LABEL_FONT_COLOR)))
        except Exception:
            COLON_FONT_COLOR = LABEL_FONT_COLOR

        enable_label_gradient = bool(font_settings.get("enable_label_gradient", False))
        label_fill_bottom = tuple(font_settings.get("label_font_color_bottom", [51, 51, 51]))

        enable_value_gradient = bool(font_settings.get("enable_value_gradient", False))
        value_fill_bottom = tuple(font_settings.get("value_font_color_bottom", [51, 51, 51]))

        enable_colon_gradient = bool(font_settings.get("enable_colon_gradient", False))
        colon_fill_bottom = tuple(font_settings.get("colon_font_color_bottom", [51, 51, 51]))
      
        name = request.form.get("name", "").strip()
        father_name = request.form.get("father_name", "").strip()
        class_name = request.form.get("class_name", "").strip()
        dob = request.form.get("dob", "").strip()
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
      
        form_data = {
            'id': student_id,
            'name': name,
            'father_name': father_name,
            'class_name': class_name,
            'dob': dob,
            'address': address,
            'phone': phone,
            'template_id': template_id,
            'school_name': school_name
        }
      
        is_duplicate, duplicate_message = check_duplicate_student(form_data, None, student_id)
        if is_duplicate:
            logger.warning(f"Duplicate student detected: {duplicate_message}")
            return render_template("edit.html", generated_url=generated_url,
                                 download_url=download_url, form_data=form_data,
                                 error=duplicate_message, templates=templates), 400
      
        photo_fn = None
        photo_stored = existing_photo_filename
        photo_url, existing_local_photo_path = resolve_student_photo_reference(student)

        if existing_local_photo_path:
            photo_stored = os.path.basename(existing_local_photo_path)

        if not photo_url and not existing_local_photo_path:
            logger.warning(f"No valid photo found for student {student_id}")
        
      
        if 'photo' in request.files and request.files['photo'].filename:
            photo = request.files['photo']
            photo_fn = secure_filename(photo.filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            photo_stored = f"{timestamp}_{photo_fn}"
            
            try:
                photo_bytes = _prepare_uploaded_student_photo_bytes(photo, photo_settings)

                if STORAGE_BACKEND == "local":
                    try:
                        local_photo_path = os.path.join(UPLOAD_FOLDER, photo_stored)
                        _write_binary_file_atomic(local_photo_path, photo_bytes)
                        photo_url = None
                    except Exception as e:
                        error = f"Error saving photo locally: {str(e)}"
                        logger.error(error)
                        return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                             form_data=form_data, error=error, templates=templates), 500
                else:
                    # Upload to Cloudinary
                    photo_url = upload_image(photo_bytes, folder='photos')
            except ValueError as e:
                error = str(e)
                logger.warning(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 400
            except Exception as e:
                error = f"Error processing photo: {str(e)}"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 500
        else:
            if not photo_stored and not photo_url:
                error = "No photo provided and no existing photo found"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 400
      
        data_hash = generate_data_hash(form_data, photo_stored)
      
        try:
            template = load_template_smart(template_path)
            template = template.resize((card_width, card_height))
            draw = ImageDraw.Draw(template)
          
            # Apply text case transformation
            text_case = font_settings.get("text_case", "normal")
            show_label_colon = bool(font_settings.get("show_label_colon", True))
            align_label_colon = bool(font_settings.get("align_label_colon", True))
            address_max_lines = int(font_settings.get("address_max_lines", 2))
            label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)
            
            # --- DYNAMIC FIELDS HANDLING ---
            # 1. Capture dynamic values from the form
            custom_data = {}
            dynamic_display = []
            std_labels = {
                'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
                'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
                'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
                'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
            }
            labels_map = std_labels.get(lang, std_labels['english'])
            
            # Fetch field definitions for this template
            db_fields = TemplateField.query.filter_by(template_id=template_id).order_by(TemplateField.display_order.asc()).all()
            
            for field in db_fields:
                val = request.form.get(field.field_name, "").strip()
                custom_data[field.field_name] = val
                
                dynamic_display.append({
                    'key': field.field_name,
                    'label': field.field_label,
                    'value': val,
                    'order': field.display_order
                })
            
            # 2. Combine with Standard Fields
            all_fields = [
                {'key': "NAME", 'label': labels_map['NAME'], 'value': apply_text_case(name, text_case), 'order': 10},
                {'key': "F_NAME", 'label': labels_map['F_NAME'], 'value': apply_text_case(father_name, text_case), 'order': 20},
                {'key': "CLASS", 'label': labels_map['CLASS'], 'value': apply_text_case(class_name, text_case), 'order': 30},
                {'key': "DOB", 'label': labels_map['DOB'], 'value': dob, 'order': 40},
                {'key': "MOBILE", 'label': labels_map['MOBILE'], 'value': phone, 'order': 50},
                {'key': "ADDRESS", 'label': labels_map['ADDRESS'], 'value': apply_text_case(address, text_case), 'order': 60}
            ]
            
            # Add dynamic fields to the list for drawing
            for d in dynamic_display:
                all_fields.append({
                    'key': d.get('key'),
                    'label': d['label'],
                    'value': apply_text_case(d['value'], text_case),
                    'order': d['order']
                })
            
            # 3. Sort by display order
            all_fields.sort(key=lambda x: int(x.get('order') or 0))
            # -------------------------------

            # --- PRE-CALCULATE PHOTO BOUNDARIES ---
            p_x = photo_settings.get("photo_x", 0)
            p_y = photo_settings.get("photo_y", 0)
            p_w = photo_settings.get("photo_width", 0)
            p_h = photo_settings.get("photo_height", 0)
            p_bottom = p_y + p_h

            # Layout Settings
            label_x = font_settings["label_x"]
            value_x = font_settings["value_x"]
            current_y = get_initial_flow_y_for_side(template, font_settings, side="front")
            line_height = font_settings["line_height"]

            # Load label font once (language-aware Unicode fallback)
            l_font = load_font_dynamic(FONT_BOLD_PATH, "X", 10**9, font_settings["label_font_size"], language=lang)
            
            # Draw Loop
            for item in all_fields:
                lbl = apply_text_case(item['label'], text_case)
                val = apply_text_case(item['value'], text_case)

                # Text shaping for correct Urdu/Arabic joining + RTL order (Hindi/English unchanged)
                display_label = process_text_for_drawing(lbl, lang)
                display_val = process_text_for_drawing(val, lang)
                field_key = item.get('key') or order_to_field_key(item.get('order'))
                layout_item = resolve_field_layout(template_obj, field_key, label_x, value_x, current_y)
                if not field_within_vertical_bounds(layout_item, current_y, card_height):
                    continue
                label_text_final, colon_text_final = split_label_and_colon(
                    display_label,
                    lang,
                    direction,
                    include_colon=show_label_colon,
                    align_colon=align_label_colon,
                )
                if not field_consumes_layout_space(layout_item, val):
                    continue
                advances_flow = field_advances_layout_flow(layout_item, val, separate_colon=bool(colon_text_final))
                label_x_eff = layout_item["label_x"]
                value_x_eff = layout_item["value_x"]
                label_y_eff = layout_item["label_y"]
                value_y_eff = layout_item["value_y"]
                label_fill = layout_item.get("label_color") or LABEL_FONT_COLOR
                value_fill = layout_item.get("value_color") or VALUE_FONT_COLOR
                colon_fill = layout_item.get("colon_color") or COLON_FONT_COLOR
                label_font_size_eff = max(1, int(layout_item.get("label_font_size") or font_settings["label_font_size"]))
                value_font_size_eff = max(1, int(layout_item.get("value_font_size") or font_settings["value_font_size"]))
                colon_font_size_eff = max(1, int(layout_item.get("colon_font_size") or label_font_size_eff))
                colon_y_eff = layout_item.get("colon_y", label_y_eff)
                colon_x_eff = layout_item.get("colon_x")
                colon_grow_eff = layout_item.get("colon_grow")
                
                if advances_flow:
                    current_y = max(int(current_y), int(label_y_eff), int(value_y_eff))

                l_font = load_font_dynamic(FONT_BOLD_PATH, label_text_final, 10**9, label_font_size_eff, language=lang)
                colon_font = load_font_dynamic(FONT_BOLD_PATH, colon_text_final or ":", 10**9, colon_font_size_eff, language=lang)
                
                label_draw_x = flip_x_for_text_direction(
                    label_x_eff,
                    label_text_final,
                    l_font,
                    card_width,
                    direction,
                    draw=draw,
                    grow_mode=layout_item["label_grow"],
                )

                max_w = int(get_anchor_max_text_width(
                    card_width=card_width,
                    anchor_x=value_x_eff,
                    text_direction=direction,
                    line_y=value_y_eff,
                    line_height=line_height,
                    grow_mode=layout_item["value_grow"],
                    photo_x=p_x,
                    photo_y=p_y,
                    photo_width=p_w,
                    photo_height=p_h,
                    page_margin=20,
                    photo_gap=15,
                    min_width=20,
                ))
                
                # --- ADDRESS LOGIC (PIXEL-ACCURATE, DYNAMIC LINES) ---
                if field_key == "ADDRESS":
                    if layout_item["label_visible"]:
                        draw_text_gradient(
                            draw,
                            (label_draw_x, label_y_eff),
                            label_text_final,
                            font=l_font,
                            top_color=label_fill,
                            bottom_color=label_fill_bottom,
                            enable_gradient=enable_label_gradient,
                            lang=lang,
                            target_image=template,
                            **get_draw_text_kwargs(label_text_final, lang),
                        )
                        draw_aligned_colon_pil_helper(
                            draw,
                            card_width,
                            direction,
                            value_x_eff,
                            colon_y_eff,
                            colon_text_final,
                            colon_font,
                            colon_fill,
                            lang,
                            label_colon_gap,
                            anchor_x=colon_x_eff,
                            grow_mode=colon_grow_eff,
                            target_image=template,
                            enable_gradient=enable_colon_gradient,
                            bottom_color=colon_fill_bottom,
                        )
                
                    value_char_spacing = layout_item.get("value_char_spacing", 0)
                    
                    # Get font loader
                    def font_loader(size_px):
                        return load_font_dynamic(FONT_REGULAR_PATH, "X", card_width, size_px, language=lang)
                    
                    # Fit wrapped text for address
                    best_size, wrapped_lines = fit_wrapped_text_pil(
                        val,
                        font_loader,
                        start_size_px=value_font_size_eff,
                        min_size_px=10,
                        max_width_px=max_w,
                        max_lines=address_max_lines,
                        char_spacing=value_char_spacing,
                        draw=draw,
                        lang=lang,
                    )
                    
                    # Load font at best size
                    addr_font = load_font_dynamic(FONT_REGULAR_PATH, "X", card_width, best_size, language=lang)
                    
                    # Draw each line
                    val_line_height = layout_item.get("value_line_height") or line_height
                    try:
                        val_lh = float(val_line_height)
                    except (ValueError, TypeError):
                        val_lh = 1.2
                    spacing = val_lh if val_lh > 10 else best_size * (val_lh if val_lh > 0 else 1.2)
                    
                    for line in wrapped_lines[:address_max_lines]:
                        line_display = process_text_for_drawing(line, lang)
                        if layout_item["value_visible"]:
                            value_draw_x = flip_x_for_text_direction(
                                value_x_eff,
                                line_display,
                                addr_font,
                                card_width,
                                direction,
                                draw=draw,
                                grow_mode=layout_item["value_grow"],
                            )
                            draw_text_gradient(
                                draw,
                                (value_draw_x, value_y_eff),
                                line_display,
                                font=addr_font,
                                top_color=value_fill,
                                bottom_color=value_fill_bottom,
                                enable_gradient=enable_value_gradient,
                                lang=lang,
                                target_image=template,
                                **get_draw_text_kwargs(line_display, lang),
                            )
                        value_y_eff += spacing
                        if advances_flow:
                            current_y += spacing
                
                    continue
                

                # --- STANDARD FIELDS ---
                else:
                    if layout_item["label_visible"]:
                        draw_text_gradient(
                            draw,
                            (label_draw_x, label_y_eff),
                            label_text_final,
                            font=l_font,
                            top_color=label_fill,
                            bottom_color=label_fill_bottom,
                            enable_gradient=enable_label_gradient,
                            lang=lang,
                            target_image=template,
                            **get_draw_text_kwargs(label_text_final, lang),
                        )
                        draw_aligned_colon_pil_helper(
                            draw,
                            card_width,
                            direction,
                            value_x_eff,
                            colon_y_eff,
                            colon_text_final,
                            colon_font,
                            colon_fill,
                            lang,
                            label_colon_gap,
                            anchor_x=colon_x_eff,
                            grow_mode=colon_grow_eff,
                            target_image=template,
                            enable_gradient=enable_colon_gradient,
                            bottom_color=colon_fill_bottom,
                        )

                    v_font, _ = fit_dynamic_font_to_single_line(
                        draw,
                        FONT_REGULAR_PATH,
                        display_val,
                        max_w,
                        value_font_size_eff,
                        language=lang,
                    )
                    if layout_item["value_visible"]:
                        value_draw_x = flip_x_for_text_direction(
                            value_x_eff,
                            display_val,
                            v_font,
                            card_width,
                            direction,
                            draw=draw,
                            grow_mode=layout_item["value_grow"],
                        )
                        draw_text_gradient(
                            draw,
                            (value_draw_x, value_y_eff),
                            display_val,
                            font=v_font,
                            top_color=value_fill,
                            bottom_color=value_fill_bottom,
                            enable_gradient=enable_value_gradient,
                            lang=lang,
                            target_image=template,
                            **get_draw_text_kwargs(display_val, lang),
                        )
                    if advances_flow:
                        current_y += line_height
          
            if photo_settings.get("enable_photo", True):
                try:
                    photo_subject = SimpleNamespace(
                        photo_url=photo_url or None,
                        photo_filename=photo_stored or None,
                        image_url=None,
                    )
                    photo_img = load_student_photo_rgba(
                        photo_subject,
                        photo_settings["photo_width"],
                        photo_settings["photo_height"],
                        timeout=10,
                        photo_settings=photo_settings,
                    )
                    if photo_img is None:
                        logger.error(f"Failed to load photo for photo_subject, using placeholder")
                        if os.path.exists(PLACEHOLDER_PATH):
                            ph_w = int(float(photo_settings.get("photo_width", 100) or 100))
                            ph_h = int(float(photo_settings.get("photo_height", 100) or 100))
                            photo_img = Image.open(PLACEHOLDER_PATH).convert("RGBA")
                            photo_img = ImageOps.fit(
                                photo_img, (ph_w, ph_h), Image.Resampling.LANCZOS
                            )
                    if photo_img:
                        radii = [
                            int(float(photo_settings.get("photo_border_top_left", 0) or 0)),
                            int(float(photo_settings.get("photo_border_top_right", 0) or 0)),
                            int(float(photo_settings.get("photo_border_bottom_right", 0) or 0)),
                            int(float(photo_settings.get("photo_border_bottom_left", 0) or 0))
                        ]
                        photo_img = round_photo(
                            photo_img,
                            radii,
                            border_color=photo_settings.get("photo_frame_color"),
                            border_thickness=2 if photo_settings.get("photo_frame_color") else 0,
                            shape=photo_settings.get("photo_shape", "rectangle"),
                            shape_inset=photo_settings.get("photo_shape_inset", 0),
                        )
                        template.paste(photo_img, (photo_settings["photo_x"], photo_settings["photo_y"]), photo_img)

                except Exception as e:
                    error = f"Error processing photo: {str(e)}"
                    logger.error(error)
                    return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                         form_data=form_data, error=error, templates=templates), 500
          
            # Add QR Code if enabled
            if qr_settings.get("enable_qr", False):
                if qr_settings.get("qr_data_type") == "student_id":
                    qr_data = json.dumps({
                        "student_id": str(student_id),
                        "name": name,
                        "school_name": school_name,
                        "photo_url": photo_url,
                        "custom_data": custom_data
                    })
                elif qr_settings.get("qr_data_type") == "url":
                    qr_data = qr_settings.get("qr_base_url", "") + str(student_id)
                elif qr_settings.get("qr_data_type") == "text":
                    qr_data = qr_settings.get("qr_custom_text", "Sample Text")
                elif qr_settings.get("qr_data_type") == "json":
                    qr_data = json.dumps({
                        "student_id": student_id,
                        "name": name,
                        "class": class_name,
                        "school_name": school_name,
                        "photo_url": photo_url,
                        "custom_data": custom_data
                    })
                else:
                    qr_data = str(student_id)

                qr_size = qr_settings.get("qr_size", 120)
                qr_x = qr_settings.get("qr_x", 50)
                qr_y = qr_settings.get("qr_y", 50)
                qr_img = generate_qr_code(qr_data, qr_settings, qr_size)
                qr_img = qr_img.resize((qr_size, qr_size))
                template.paste(qr_img, (qr_x, qr_y))

            # Add Barcode if enabled
            if qr_settings.get("enable_barcode", False):
                barcode_type = qr_settings.get("barcode_data_type", "student_id")
                if barcode_type == "url":
                    barcode_data = qr_settings.get("barcode_base_url", "") + str(student_id)
                elif barcode_type == "text":
                    barcode_data = qr_settings.get("barcode_custom_text", "Sample Text")
                elif barcode_type == "json":
                    barcode_data = json.dumps({
                        "student_id": student_id,
                        "name": name,
                        "class": class_name,
                        "school_name": school_name,
                        "photo_url": photo_url,
                        "custom_data": custom_data
                    })
                else:
                    barcode_data = str(student_id)

                barcode_x = qr_settings.get("barcode_x", 50)
                barcode_y = qr_settings.get("barcode_y", 200)
                barcode_w = max(40, int(qr_settings.get("barcode_width", 220)))
                barcode_h = max(30, int(qr_settings.get("barcode_height", 70)))
                barcode_img = generate_barcode_code128(barcode_data, qr_settings, width=barcode_w, height=barcode_h)
                template.paste(barcode_img, (barcode_x, barcode_y))

            apply_layout_custom_objects_pil(template, template_obj, font_settings, side="front", language=lang)
          
            # =========================================================
            # SAVE INDIVIDUAL CARD (Cloudinary on deployed, local filesystem when running locally)
            # =========================================================
            side_render_student = SimpleNamespace(
                name=name,
                father_name=father_name,
                class_name=class_name,
                dob=dob,
                address=address,
                phone=phone,
                photo_url=photo_url,
                photo_filename=photo_stored if photo_stored and not str(photo_stored).startswith("http") else None,
                custom_data=custom_data,
                school_name=school_name,
            )
            student_id = getattr(side_render_student, "id", None)
            render_scale = 1.0
            school_name = getattr(template_obj, "school_name", None) or getattr(student, "school_name", "")

            
            front_image = _get_cached_final_card(
                template_obj,
                side_render_student,
                side="front",
                student_id=student_id,
                school_name=school_name,
                render_scale=render_scale,
            )
            
            if front_image is None:
                raise RuntimeError("Failed to render front card image")
            
            back_image = None
            if getattr(template_obj, "is_double_sided", False):
                back_image = _get_cached_final_card(
                    template_obj,
                    side_render_student,
                    side="back",
                    student_id=student_id,
                    school_name=school_name,
                    render_scale=render_scale,
                ) or load_static_back_template_image(template_obj, card_width, card_height)

            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            jpg_name = f"card_{template_id}_{student_id}_{timestamp}.jpg"
            back_jpg_name = f"card_{template_id}_{student_id}_{timestamp}_back.jpg" if back_image is not None else None
            pdf_name = f"card_{template_id}_{student_id}_{timestamp}.pdf"

            template = force_rgb(template)
            if back_image is not None:
                back_image = force_rgb(back_image)

            jpg_url = None
            back_jpg_url = None
            pdf_url = None

            if STORAGE_BACKEND == "local":
                os.makedirs(GENERATED_FOLDER, exist_ok=True)
                with open(os.path.join(GENERATED_FOLDER, jpg_name), "wb") as fh:
                    buf = io.BytesIO()
                    template.save(buf, "JPEG", quality=95)
                    buf.seek(0)
                    fh.write(buf.getvalue())

                if back_jpg_name and back_image is not None:
                    with open(os.path.join(GENERATED_FOLDER, back_jpg_name), "wb") as fh:
                        buf = io.BytesIO()
                        back_image.save(buf, "JPEG", quality=95)
                        buf.seek(0)
                        fh.write(buf.getvalue())

                with open(os.path.join(GENERATED_FOLDER, pdf_name), "wb") as fh:
                    buf = io.BytesIO()
                    if back_image is not None:
                        template.save(buf, "PDF", save_all=True, append_images=[back_image], resolution=300)
                    else:
                        template.save(buf, "PDF", resolution=300)
                    buf.seek(0)
                    fh.write(buf.getvalue())

                student.generated_filename = pdf_name
                generated_url = with_cache_bust(url_for('static', filename=f'generated/{jpg_name}'))
                back_generated_url = with_cache_bust(url_for('static', filename=f'generated/{back_jpg_name}')) if back_jpg_name else None
                download_url = url_for('static', filename=f'generated/{pdf_name}')
            else:
                # Convert image to bytes and upload to Cloudinary
                jpg_buffer = io.BytesIO()
                template.save(jpg_buffer, "JPEG", quality=95)
                jpg_buffer.seek(0)
                jpg_url = upload_image(jpg_buffer.getvalue(), folder='cards', resource_type='image')

                # Update URLs for frontend display
                generated_url = with_cache_bust(jpg_url)  # Use Cloudinary URL

                if back_image is not None:
                    back_jpg_buffer = io.BytesIO()
                    back_image.save(back_jpg_buffer, "JPEG", quality=95)
                    back_jpg_buffer.seek(0)
                    back_jpg_url = upload_image(back_jpg_buffer.getvalue(), folder='cards', resource_type='image')
                    back_generated_url = with_cache_bust(back_jpg_url)

                # Upload PDF to Cloudinary as well
                pdf_buffer = io.BytesIO()
                if back_image is not None:
                    template.save(pdf_buffer, "PDF", save_all=True, append_images=[back_image], resolution=300)
                else:
                    template.save(pdf_buffer, "PDF", resolution=300)
                pdf_buffer.seek(0)
                pdf_url = upload_image(pdf_buffer.getvalue(), folder='cards', resource_type='raw')
                download_url = pdf_url  # Use Cloudinary URL
          
            try:
                # Delete old photo if changed
                if student.photo_filename and student.photo_filename != photo_stored:
                    old_photo_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
                    if os.path.exists(old_photo_path):
                        os.remove(old_photo_path)
                
                # Delete old generated files
                if student.generated_filename and student.generated_filename != pdf_name:
                     # Check if old filename stored was PDF or JPG and clean up both variants
                    base_old = os.path.splitext(student.generated_filename)[0]
                    for ext in ['.pdf', '.jpg']:
                        old_file_path = os.path.join(GENERATED_FOLDER, base_old + ext)
                        if os.path.exists(old_file_path):
                            os.remove(old_file_path)
                if student.back_generated_filename and student.back_generated_filename != back_jpg_name:
                    old_back_path = os.path.join(GENERATED_FOLDER, student.back_generated_filename)
                    if os.path.exists(old_back_path):
                        os.remove(old_back_path)
                
                student.name = name
                student.father_name = father_name
                student.class_name = class_name
                student.dob = dob
                student.address = address
                student.phone = phone
                if STORAGE_BACKEND == "local":
                    student.photo_url = None
                    student.photo_filename = photo_stored
                    student.image_url = None
                    student.back_image_url = None
                    student.pdf_url = None
                    student.back_generated_filename = back_jpg_name
                else:
                    student.photo_url = photo_url
                    student.photo_filename = photo_stored if photo_stored and not str(photo_stored).startswith("http") else None
                    # Store Cloudinary URLs instead of local filenames
                    student.image_url = jpg_url
                    student.back_image_url = back_jpg_url
                    student.pdf_url = pdf_url
                    student.back_generated_filename = None
                student.data_hash = data_hash
                student.template_id = template_id
                student.school_name = school_name
                student.custom_data = custom_data # <--- SAVE DYNAMIC FIELDS
                
                db.session.commit()
                
                success = "ID card updated successfully"
                form_data['photo_filename'] = photo_stored
                form_data['generated_filename'] = jpg_name
                form_data['custom_data'] = custom_data
                
            except Exception as e:
                error = f"Database error: {str(e)}"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 500
      
        except Exception as e:
            error = f"Error generating ID card: {str(e)}"
            logger.error(error)
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 500
  
    return render_template("edit.html", generated_url=generated_url, back_generated_url=back_generated_url, download_url=download_url,
                          form_data=form_data, success=success, error=error, templates=templates)


@dashboard_bp.route("/admin/activity_log")
@admin_required
def view_activity_log():
    # 2. Pagination Logic
    page = request.args.get('page', 1, type=int)
    per_page = 50  # Show 50 logs per page

    # 3. Fetch Logs (Sorted Newest First)
    pagination = ActivityLog.query.order_by(ActivityLog.timestamp.desc())\
                                  .paginate(page=page, per_page=per_page, error_out=False)
    
    logs = pagination.items

    return render_template(
        "activity_log.html",
        logs=logs,
        pagination=pagination
    )

@dashboard_bp.route("/admin/reset_activity_log", methods=["POST"])
@super_admin_required
def reset_activity_log():
    try:
        # 2. Delete all records in ActivityLog table
        db.session.query(ActivityLog).delete()
        db.session.commit()
        
        # 3. Create a new entry saying it was reset (so it's not totally empty)
        log_activity("Reset Activity Log", details="All previous history cleared by Admin")
        
        return redirect(url_for("dashboard.view_activity_log", success="Activity log cleared successfully."))
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error clearing activity log: {e}")
        return redirect(url_for("dashboard.view_activity_log", error=f"Failed to clear log: {str(e)}"))
# Refactored module imports — these provide cleaner organizational structure.
# The original function definitions above shadow these imports at runtime,
# preserving exact backward-compatible behavior.
# These routes delegate rendering helpers to:
# from app.routes.dashboard_render import (...)
