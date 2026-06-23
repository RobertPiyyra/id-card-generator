from app.legacy_app import admin_required
import os
import io
import json
import math
import logging
import html
import re
import sys
import unicodedata
import requests
import base64
import traceback
import fitz
from types import SimpleNamespace
from functools import lru_cache
from flask import Blueprint, send_file, session, redirect, url_for, current_app, request, Response, jsonify
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.colors import Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import reportlab.pdfbase.pdfdoc as reportlab_pdfdoc
from reportlab.lib.utils import ImageReader

from models import db, Template, TemplateField, Student
from app.utils.helper_utils import FONTS_FOLDER, GENERATED_FOLDER, PLACEHOLDER_PATH, get_template_path, get_template_settings, generate_data_hash
from app.utils.text_utils import PIL_RAQM_AVAILABLE, get_localized_standard_labels, process_text_for_drawing, split_label_and_colon
from app.utils.image_utils import generate_barcode_code128, generate_qr_code, round_photo
from app.utils.layout_utils import get_anchor_max_text_width, colon_anchor_for_value
from app.utils.fonts import _language_font_fallbacks, _presentation_forms_font_fallbacks, _font_covers_text

logger = logging.getLogger(__name__)


# Monkeypatch PDFPage check_format and Canvas _setShadingUsed to support /Pattern dictionaries
orig_check_format = reportlab_pdfdoc.PDFPage.check_format

# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import LAYOUT_DPI  # noqa: E402
from app.services.corel_export_service import _my_check_format  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _my_setShadingUsed  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _parse_hex_to_rgb_normalized  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import local_apply_text_case  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _corel_safe_pdf_bytes  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _is_valid_pdf_bytes  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _strip_marked_content_operators  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _strip_page_level_pdf_keys  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _strip_optional_content_pypdf_page  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _save_pikepdf_corel  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _normalize_pdf_for_corel  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _rebuild_optional_content_catalog  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _flatten_optional_content_pdf_bytes  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _aggressive_corel_flatten  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _make_corel_friendly  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _template_pdf_has_corel_hostile_features  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _rasterize_template_pdf_for_editable_overlay  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _build_template_card_placements  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _get_app_card_render_helpers  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _safe_canvas_font_name  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _run_baseline_px  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _draw_raster_text_run_on_canvas  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _draw_text_runs_on_canvas  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _pil_image_reader  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _student_qr_identifier  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _draw_editable_media_overlays  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _build_compiled_sheet_via_app_renderer  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _compose_vector_template_export_pypdf  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _compose_card_pages_to_sheet_pypdf  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _interleave_pdf_bytes  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _detect_translation_source_language  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _should_skip_translation  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _extract_google_translate_text  # noqa: E402

from flask import Blueprint
corel_bp = Blueprint('corel', __name__)
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _google_translate_text  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _translate_value_for_export  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _normalize_language  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _field_key_from_item  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _get_template_field_side_flags  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _resolve_pdf_field_layout  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _initial_flow_y_px  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _field_wrap_policy  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _field_consumes_layout_space  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _field_advances_layout_flow  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _draw_custom_editor_objects_pdf  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _contains_arabic_script  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _safe_bidi_get_display  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _clean_bidi_controls  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import process_text_for_vector  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _normalize_grow_mode  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _x_for_direction  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _x_for_direction_raster  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _get_pil_font  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _pil_font_signature  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _measure_raster_text_metrics  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _build_text_image  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import draw_custom_rounded_rect  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _photo_shape_points  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _ellipse_path_reportlab  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _polygon_path_reportlab  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _clip_photo_shape_reportlab  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _draw_photo_frame_reportlab  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import parse_pdf_export_mode  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _render_profile  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _corel_editable_photo_mode  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _normalize_wrap_text  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _measure_vector_text_width  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _measure_raster_text_width  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _ellipsize_to_width  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _split_wrap_units  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _rebalance_wrapped_lines  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _wrap_text_by_width  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _wrap_text_by_width_single  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _fit_wrapped_text  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _is_probably_pdf_source  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _load_template_for_pdf  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _read_template_pdf_bytes  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _compose_vector_template_export  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _generate_direct_editable_pdf_template_export  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _draw_vector_qr  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _draw_vector_barcode  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _queue_hb_run  # noqa: E402
# Extracted to app/services/corel_export_service.py
from app.services.corel_export_service import _apply_hb_text_overlay  # noqa: E402
@corel_bp.route("/corel/preview/<int:template_id>")
@admin_required
def corel_preview(template_id):

    template = db.session.get(Template, template_id)
    if not template:
        # Return placeholder image if template missing
        if os.path.exists(PLACEHOLDER_PATH):
            placeholder_img = Image.open(PLACEHOLDER_PATH).convert('RGB')
            buf = io.BytesIO()
            placeholder_img.save(buf, format='JPEG', quality=95)
            buf.seek(0)
            return Response(buf.getvalue(), mimetype='image/jpeg')
        return "Template not found", 404

    side = request.args.get("side", "front").lower()
    if side not in ("front", "back"):
        side = "front"

    student_id = request.args.get("student_id")
    student = None
    if student_id:
        try:
            student = db.session.get(Student, int(student_id))
        except ValueError:
            pass

    if not student or student.template_id != template_id:
        student = Student.query.filter_by(template_id=template_id).first()

    if not student:
        # Create a dummy student for preview if none exist
        student = Student(
            id=0,
            admission_no="1234",
            student_name="JOHN DOE",
            father_name="RICHARD DOE",
            class_name="CLASS 10",
            section="A",
            roll_no="25",
            dob="2010-01-01",
            phone="1234567890",
            address="123 MAIN STREET, NEW YORK, NY",
            photo_url=None,
            template_id=template_id
        )

    try:
        card_w_px = template.card_width if template.card_width else 1015
        card_h_px = template.card_height if template.card_height else 661
        scale = 72.0 / LAYOUT_DPI

        card_w_pt = card_w_px * scale
        card_h_pt = card_h_px * scale

        mode = request.args.get("mode", "print").lower()
        if mode not in ("print", "editable"):
            mode = "print"

        pdf_bytes = _build_compiled_sheet_via_app_renderer(
            template=template,
            students=[student],
            side=side,
            mode=mode,
            sheet_w_pt=card_w_pt,
            sheet_h_pt=card_h_pt,
            card_w_pt=card_w_pt,
            card_h_pt=card_h_pt,
            start_x_pt=0,
            start_y_pt=card_h_pt,
            gap_pt=0,
            cols=1,
            rows=1,
            scale=scale,
        )

        from flask import Response
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[0]
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("jpeg")
        doc.close()

        return Response(img_bytes, mimetype="image/jpeg")
    except Exception as e:
        logger.error(f"Corel preview rendering failed: {e}", exc_info=True)
        return f"Preview generation failed: {str(e)}", 500


@corel_bp.route("/download_compiled_vector_pdf/<int:template_id>")
@admin_required
def download_compiled_vector_pdf(template_id):

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
            
        if session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
            return "Unauthorized access to this school's data", 403
            
        try:
            from app.services.premium_service import run_design_qa
            qa_settings = (getattr(template, "qa_settings", None) or {})
            if bool(qa_settings.get("enforce_before_pdf_export")):
                qa_result = run_design_qa(template)
                if not bool(qa_result.get("ok")):
                    return jsonify({
                        "success": False,
                        "message": "Design QA failed. Resolve issues before PDF export.",
                        "qa": qa_result
                    }), 400
        except Exception as qa_exc:
            logger.warning("PDF export QA gate skipped due to error: %s", qa_exc)

        # 2. Settings
        font_settings, photo_settings, qr_settings, orientation = get_template_settings(template_id, side="front")
        back_font_settings, back_photo_settings, back_qr_settings, _ = get_template_settings(template_id, side="back")
        template_path = get_template_path(template_id)
        back_template_path = get_template_path(template_id, side="back") if getattr(template, "is_double_sided", False) else None
        
        buffer = io.BytesIO()
        template_pdf_bytes = _read_template_pdf_bytes(template_path)
        preserve_vector_template = bool(template_pdf_bytes)
        back_template_pdf_bytes = _read_template_pdf_bytes(back_template_path) if back_template_path else None
        preserve_vector_back_template = bool(back_template_pdf_bytes)
        editable_template_pdf_bytes = template_pdf_bytes
        editable_back_template_pdf_bytes = back_template_pdf_bytes
        if mode == "editable" and template_pdf_bytes:
            flattened_front_template = _flatten_optional_content_pdf_bytes(template_pdf_bytes)
            editable_template_pdf_bytes = _rasterize_template_pdf_for_editable_overlay(
                flattened_front_template,
                dpi=asset_dpi,
            )
        if mode == "editable" and back_template_pdf_bytes:
            flattened_back_template = _flatten_optional_content_pdf_bytes(back_template_pdf_bytes)
            editable_back_template_pdf_bytes = _rasterize_template_pdf_for_editable_overlay(
                flattened_back_template,
                dpi=asset_dpi,
            )

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

        try:
            front_bytes = _build_compiled_sheet_via_app_renderer(
                template=template,
                students=students,
                side="front",
                mode=mode,
                sheet_w_pt=sheet_w_pt,
                sheet_h_pt=sheet_h_pt,
                card_w_pt=card_w_pt,
                card_h_pt=card_h_pt,
                start_x_pt=start_x_pt,
                start_y_pt=start_y_pt,
                gap_pt=gap_pt,
                cols=cols,
                rows=rows,
                scale=scale,
            )

            final_bytes = front_bytes
            if getattr(template, "is_double_sided", False):
                back_bytes = _build_compiled_sheet_via_app_renderer(
                    template=template,
                    students=students,
                    side="back",
                    mode=mode,
                    sheet_w_pt=sheet_w_pt,
                    sheet_h_pt=sheet_h_pt,
                    card_w_pt=card_w_pt,
                    card_h_pt=card_h_pt,
                    start_x_pt=start_x_pt,
                    start_y_pt=start_y_pt,
                    gap_pt=gap_pt,
                    cols=cols,
                    rows=rows,
                    scale=scale,
                )
                final_bytes = _interleave_pdf_bytes(front_bytes, back_bytes, mode=mode)
                final_bytes = _make_corel_friendly(final_bytes, mode=mode)

            buffer = io.BytesIO(final_bytes)
            buffer.seek(0)
            prefix = "COREL_EDITABLE" if mode == "editable" else "COREL_PRINT_600DPI"
            filename = f"{prefix}_{template.school_name}.pdf"
            logger.info(
                "Generated Corel PDF via app renderer template_id=%s mode=%s cards=%s",
                template_id,
                mode,
                len(students),
            )
            return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')
        except Exception as renderer_exc:
            logger.warning(
                "App renderer compile path failed template_id=%s mode=%s, falling back to legacy route: %s",
                template_id,
                mode,
                renderer_exc,
            )

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
        back_lang = _normalize_language(getattr(template, "back_language", None) or getattr(template, "language", "english"))
        back_direction = (getattr(template, "back_text_direction", None) or getattr(template, "text_direction", "ltr") or "ltr").strip().lower()
        lock_rules = getattr(template, "language_lock_rules", None) or {}
        if isinstance(lock_rules, dict):
            locked_front = _normalize_language(lock_rules.get("front") or "")
            locked_back = _normalize_language(lock_rules.get("back") or "")
            if locked_front:
                lang = locked_front
                direction = "rtl" if locked_front in {"urdu", "arabic"} else "ltr"
            if locked_back:
                back_lang = locked_back
                back_direction = "rtl" if locked_back in {"urdu", "arabic"} else "ltr"
        use_harfbuzz_overlay = False
        font_reg_file = (font_settings.get("font_regular") or "").strip()
        font_bold_file = (font_settings.get("font_bold") or "").strip()

        # Hindi always needs raster fallback because ReportLab does not shape Devanagari.
        # For editable Corel export with PDF templates, rasterize Urdu/Arabic overlay text too:
        # this keeps the template itself editable while avoiding Corel import issues on complex-script text objects.
        rasterize_complex_text = (
            lang in {"hindi"} or
            (mode != "editable" and lang in {"urdu", "arabic"})
        )
        force_vector_for_language = mode == "editable" and lang in {"urdu", "arabic"}

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

        def _pick_side_font_paths(side_lang: str, side_font_settings: dict | None, default_reg: str | None, default_bold: str | None) -> tuple[str | None, str | None]:
            side_lang = _normalize_language(side_lang)
            settings = side_font_settings or {}
            requested_reg = os.path.join(FONTS_FOLDER, str(settings.get("font_regular") or "").strip()) if settings.get("font_regular") else ""
            requested_bold = os.path.join(FONTS_FOLDER, str(settings.get("font_bold") or "").strip()) if settings.get("font_bold") else ""

            if mode == "editable" and side_lang in {"urdu", "arabic"}:
                side_sample = process_text_for_vector("محمد علی", side_lang)
                for font_name in _presentation_forms_font_fallbacks():
                    font_path = os.path.join(FONTS_FOLDER, font_name)
                    if not os.path.exists(font_path):
                        continue
                    try:
                        if side_sample and not _font_covers_text(font_path, side_sample):
                            continue
                    except Exception:
                        continue
                    logger.info(
                        "Corel editable font override: language=%s requested=(%s,%s) using Presentation-Forms-safe font=%s",
                        side_lang,
                        settings.get("font_regular"),
                        settings.get("font_bold"),
                        font_name,
                    )
                    return font_path, font_path

            if side_lang == "hindi":
                hindi_fallbacks = _language_font_fallbacks("hindi")
                if hindi_fallbacks:
                    chosen = os.path.join(FONTS_FOLDER, hindi_fallbacks[0])
                    if os.path.exists(chosen):
                        return chosen, chosen

            reg_path = requested_reg if requested_reg and os.path.exists(requested_reg) else (default_reg or default_bold)
            bold_path = requested_bold if requested_bold and os.path.exists(requested_bold) else (default_bold or reg_path)
            return reg_path, bold_path

        front_side_reg_path, front_side_bold_path = _pick_side_font_paths(lang, font_settings, pil_reg_path or None, pil_bold_path or None)
        back_side_reg_path, back_side_bold_path = _pick_side_font_paths(back_lang, back_font_settings, pil_reg_path or None, pil_bold_path or None)

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
        # Fix 2/10: global colon font size for raster path
        colon_font_size_px_raster = int(font_settings.get("colon_font_size") or font_settings.get("label_font_size", 40))
        l_color = _rl_color_from_rgb(label_default_rgb)
        v_color = _rl_color_from_rgb(value_default_rgb)
        layout_config_raw = getattr(template, "layout_config", None)

        lbl_size_pt = font_settings.get('label_font_size', 40) * scale
        val_size_pt = font_settings.get('value_font_size', 36) * scale

        localization_pack = getattr(template, "localization_pack", None) or {}
        labels_map = get_localized_standard_labels(lang, localization_pack)
        back_labels_map = get_localized_standard_labels(back_lang, localization_pack)

        # For editable PDF-template exports, build complete card pages first, then impose
        # those finished pages onto the template's configured sheet layout. This keeps
        # the admin sheet arrangement while avoiding the older raw-template/overlay merge.
        use_direct_pdf_template_editable = (mode == "editable")
        if use_direct_pdf_template_editable and preserve_vector_template and mode == "editable" and (
            not getattr(template, "is_double_sided", False) or preserve_vector_back_template
        ):
            placements = _build_template_card_placements(
                student_count=len(students),
                cols=cols,
                rows=rows,
                start_x_pt=start_x_pt,
                start_y_pt=start_y_pt,
                gap_pt=gap_pt,
                card_w_pt=card_w_pt,
                card_h_pt=card_h_pt,
                sheet_h_pt=sheet_h_pt,
            )
            front_card_pages_bytes = _generate_direct_editable_pdf_template_export(
                template=template,
                template_id=template_id,
                students=students,
                template_pdf_bytes=editable_template_pdf_bytes or _flatten_optional_content_pdf_bytes(template_pdf_bytes),
                font_settings=font_settings,
                photo_settings=photo_settings,
                qr_settings=qr_settings,
                layout_config_raw=layout_config_raw,
                labels_map=labels_map,
                sheet_w_pt=card_w_pt,
                sheet_h_pt=card_h_pt,
                card_w_pt=card_w_pt,
                card_h_pt=card_h_pt,
                start_x_pt=0,
                start_y_pt=card_h_pt,
                gap_pt=0,
                cols=1,
                rows=1,
                card_w_px=card_w_px,
                card_h_px=card_h_px,
                lang=lang,
                direction=direction,
                reg_font_name=reg_font_name,
                bold_font_name=bold_font_name,
                reg_font_path=front_side_reg_path,
                bold_font_path=front_side_bold_path,
                side="front",
                source_language=lang,
                include_template_background=True,
                mode=mode,
            )
            editable_bytes = _compose_card_pages_to_sheet_pypdf(
                front_card_pages_bytes,
                placements,
                sheet_w_pt,
                sheet_h_pt,
                mode=mode,
            )
            if getattr(template, "is_double_sided", False) and preserve_vector_back_template:
                back_card_pages_bytes = _generate_direct_editable_pdf_template_export(
                    template=template,
                    template_id=template_id,
                    students=students,
                    template_pdf_bytes=editable_back_template_pdf_bytes or _flatten_optional_content_pdf_bytes(back_template_pdf_bytes),
                    font_settings=back_font_settings,
                    photo_settings=back_photo_settings,
                    qr_settings=back_qr_settings,
                    layout_config_raw=getattr(template, "back_layout_config", None),
                    labels_map=back_labels_map,
                    sheet_w_pt=card_w_pt,
                    sheet_h_pt=card_h_pt,
                    card_w_pt=card_w_pt,
                    card_h_pt=card_h_pt,
                    start_x_pt=0,
                    start_y_pt=card_h_pt,
                    gap_pt=0,
                    cols=1,
                    rows=1,
                    card_w_px=card_w_px,
                    card_h_px=card_h_px,
                    lang=back_lang,
                    direction=back_direction,
                    reg_font_name=reg_font_name,
                    bold_font_name=bold_font_name,
                    reg_font_path=back_side_reg_path,
                    bold_font_path=back_side_bold_path,
                    side="back",
                    source_language=lang,
                    include_template_background=True,
                    mode=mode,
                )
                back_editable_bytes = _compose_card_pages_to_sheet_pypdf(
                    back_card_pages_bytes,
                    placements,
                    sheet_w_pt,
                    sheet_h_pt,
                    mode=mode,
                )
                editable_bytes = _interleave_pdf_bytes(editable_bytes, back_editable_bytes, mode=mode)

            if mode == "editable":
                editable_bytes = _make_corel_friendly(editable_bytes, mode=mode)
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
        if template_path and (mode == "editable" or not preserve_vector_template):
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
                    logger.error(f"Draw BG Error: {e}")

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

            photo_radii_px = [
                int(float(photo_settings.get('photo_border_top_left', 0) or 0)),
                int(float(photo_settings.get('photo_border_top_right', 0) or 0)),
                int(float(photo_settings.get('photo_border_bottom_right', 0) or 0)),
                int(float(photo_settings.get('photo_border_bottom_left', 0) or 0)),
            ]
            photo_shape = photo_settings.get("photo_shape", "rectangle")
            r_tl, r_tr, r_br, r_bl = [float(radius) * scale for radius in photo_radii_px]
            radii = [r_tl, r_tr, r_br, r_bl]

            editable_photo_mode = _corel_editable_photo_mode(photo_settings)
            draw_editable_photo_frame = mode == "editable" and editable_photo_mode == "frame_only"
            photo_bytes_io = None
            has_real_student_photo = False

            if photo_settings.get("enable_photo", True):
                try:
                    load_student_photo_rgba_fn = _get_app_card_render_helpers()["load_student_photo_rgba"]
                    prepared_photo = load_student_photo_rgba_fn(
                        student,
                        max(1, int(round(pw_px))),
                        max(1, int(round(ph_px))),
                        timeout=10,
                        photo_settings=photo_settings,
                        allow_placeholder=not draw_editable_photo_frame,
                    )
                except TypeError:
                    prepared_photo = load_student_photo_rgba_fn(
                        student,
                        max(1, int(round(pw_px))),
                        max(1, int(round(ph_px))),
                        timeout=10,
                    )
                except Exception:
                    prepared_photo = None

                if prepared_photo is not None:
                    has_real_student_photo = bool(
                        str(getattr(student, "photo_url", "") or "").strip()
                        or str(getattr(student, "photo_filename", "") or "").strip()
                    )
                    prepared_photo = round_photo(
                        prepared_photo,
                        photo_radii_px,
                        shape=photo_shape,
                        shape_inset=photo_settings.get("photo_shape_inset", 0),
                    )
                    photo_bytes_io = io.BytesIO()
                    prepared_photo.save(photo_bytes_io, format="PNG")
                    photo_bytes_io.seek(0)

                if photo_bytes_io and (has_real_student_photo or not draw_editable_photo_frame):
                    c.saveState()
                    _clip_photo_shape_reportlab(
                        c,
                        photo_x,
                        photo_y,
                        photo_w,
                        photo_h,
                        radii,
                        photo_shape,
                        float(photo_settings.get("photo_shape_inset", 0) or 0) * scale,
                        shape_geometry_scale=scale,
                    )

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
                    _fr, _fg, _fb = _parse_hex_to_rgb_normalized(photo_settings.get("photo_frame_color"))
                    c.setStrokeColor(Color(_fr, _fg, _fb))
                    c.setLineWidth(max(0.8, 1.2 * scale))
                    _draw_photo_frame_reportlab(
                        c,
                        photo_x,
                        photo_y,
                        photo_w,
                        photo_h,
                        radii,
                        photo_shape,
                        float(photo_settings.get("photo_shape_inset", 0) or 0) * scale,
                        shape_geometry_scale=scale,
                    )
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
            config_address_max_lines = int(font_settings.get("address_max_lines", 2) or 2)
            label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)
            
            fields = [
                {'k': "NAME", 'l': local_apply_text_case(labels_map['NAME'], text_case), 'v': local_apply_text_case(student.name, text_case), 'ord': 10},
                {'k': "F_NAME", 'l': local_apply_text_case(labels_map['F_NAME'], text_case), 'v': local_apply_text_case(student.father_name, text_case), 'ord': 20},
                {'k': "CLASS", 'l': local_apply_text_case(labels_map['CLASS'], text_case), 'v': local_apply_text_case(student.class_name, text_case), 'ord': 30},
                {'k': "DOB", 'l': local_apply_text_case(labels_map['DOB'], text_case), 'v': local_apply_text_case(student.dob, text_case), 'ord': 40},
                {'k': "MOBILE", 'l': local_apply_text_case(labels_map['MOBILE'], text_case), 'v': local_apply_text_case(student.phone, text_case), 'ord': 50},
                {'k': "ADDRESS", 'l': local_apply_text_case(labels_map['ADDRESS'], text_case), 'v': local_apply_text_case(student.address, text_case), 'ord': 60}
            ]
            
            from app.services.render_service import normalize_custom_data
            custom_data = normalize_custom_data(getattr(student, "custom_data", None))
            db_fields = TemplateField.query.filter_by(template_id=template_id).order_by(TemplateField.display_order.asc()).all()
            for f in db_fields:
                val = custom_data.get(f.field_name, "")
                fields.append({
                    'k': f.field_name,
                    'l': local_apply_text_case(f.field_label, text_case),
                    'v': local_apply_text_case(val, text_case),
                    'ord': f.display_order
                })
            
            fields.sort(key=lambda x: int(x.get('ord') or 0))

            start_y_text_px = font_settings.get('start_y', 200)
            label_x_px = font_settings.get('label_x', 50)
            value_x_px = font_settings.get('value_x', 250)
            current_y_px = _initial_flow_y_px(template, font_settings, side="front")
            line_height_px = font_settings.get('line_height', 50)

            # Photo Vertical Boundaries (Pixels)
            photo_enabled = bool(photo_settings.get("enable_photo", True))
            p_x_px = photo_settings.get("photo_x", 0) if photo_enabled else 0
            p_y_px = photo_settings.get("photo_y", 0) if photo_enabled else 0
            p_h_px = photo_settings.get("photo_height", 0) if photo_enabled else 0
            p_bottom_px = p_y_px + p_h_px

            def _baseline_y(top_y_px: float, font_size_pt: float) -> float:
                return card_bottom_y + (card_h_pt - (top_y_px * scale) - font_size_pt)

            for field in fields:
                field_key = _field_key_from_item(field)
                layout_item = _resolve_pdf_field_layout(
                    template,
                    field_key,
                    label_x_px,
                    value_x_px,
                    current_y_px,
                    side="front",
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
                colon_rgb = layout_item.get("colon_color") or colon_default_rgb
                label_size_px_eff = max(1, int(layout_item.get("label_font_size") or font_settings.get("label_font_size", 40)))
                value_size_px_eff = max(1, int(layout_item.get("value_font_size") or font_settings.get("value_font_size", 36)))
                colon_size_px_eff = max(1, int(layout_item.get("colon_font_size") or colon_font_size_px_raster))
                lbl_size_pt_eff = label_size_px_eff * scale
                val_size_pt_eff = value_size_px_eff * scale
                colon_size_pt_eff = colon_size_px_eff * scale

                if not _field_consumes_layout_space(layout_item, field.get("v", "")):
                    continue
                advances_flow = _field_advances_layout_flow(
                    layout_item,
                    field.get("v", ""),
                    separate_colon=bool(show_label_colon and align_label_colon),
                )

                if advances_flow:
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
                        # Fix 14: per-field colon color for raster path
                        _raster_colon_rgb = layout_item.get("colon_color") or colon_default_rgb
                        colon_fill = (
                            int(_raster_colon_rgb[0]),
                            int(_raster_colon_rgb[1]),
                            int(_raster_colon_rgb[2]),
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
                                _pil_image_reader(img, preserve_alpha=True),
                                label_x,
                                label_pdf_y - (baseline_y_px * text_raster_scale),
                                width=img.size[0] * text_raster_scale,
                                height=img.size[1] * text_raster_scale,
                                mask="auto",
                            )
                        if colon_text:
                            colon_size_px = max(1, int(round(colon_size_px_eff * raster_multiplier)))
                            pil_colon_font = _get_pil_font(pil_bold_path, colon_size_px, lang)
                            colon_img, colon_baseline_y_px, colon_width_px = _build_text_image(colon_text, pil_colon_font, colon_fill, lang)
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
                                _pil_image_reader(colon_img, preserve_alpha=True),
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
                            c.setFillColor(_rl_color_from_rgb(colon_rgb))
                            c.setFont(bold_font_name, colon_size_pt_eff)
                            colon_anchor_px, colon_grow = colon_anchor_for_value(value_x_eff, direction, gap_px=label_colon_gap)
                            colon_x = _x_for_direction(
                                card_x,
                                card_w_pt,
                                colon_anchor_px,
                                colon_text,
                                bold_font_name,
                                colon_size_pt_eff,
                                scale,
                                direction,
                                grow_mode=colon_grow,
                            )
                            c.drawString(colon_x, label_pdf_y, colon_text)
                            c.setFillColor(_rl_color_from_rgb(label_rgb))
                            c.setFont(bold_font_name, lbl_size_pt_eff)
                
                c.setFillColor(_rl_color_from_rgb(value_rgb))
                val_text = process_text_for_drawing(field["v"], lang) if rasterize_complex_text else process_text_for_vector(field["v"], lang)

                max_w_px = get_anchor_max_text_width(
                    card_width=card_w_px,
                    anchor_x=value_x_eff,
                    text_direction=direction,
                    line_y=value_y_eff,
                    line_height=line_height_px,
                    grow_mode=value_grow,
                    photo_x=p_x_px,
                    photo_y=p_y_px,
                    photo_width=(photo_settings.get("photo_width", 0) if photo_enabled else 0),
                    photo_height=(photo_settings.get("photo_height", 0) if photo_enabled else 0),
                    page_margin=20,
                    photo_gap=15,
                    min_width=50,
                )

                max_width_pt = float(max_w_px) * scale
                remaining_h_px = max(1.0, float(card_h_px - 20) - float(value_y_eff))
                remaining_h_pt = max(scale, remaining_h_px * scale)
                wrap_policy = _field_wrap_policy(field_key, config_address_max_lines)
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
                                box_h_pt=max(colon_size_pt_eff * 1.6, line_height_px * scale * 1.2),
                                scale=scale,
                                direction=direction,
                                text=hb_colon_text,
                                font_file=hb_font_bold_file,
                                font_size_pt=colon_size_pt_eff,
                                color_rgb=colon_rgb,
                            )

                    if value_visible:
                        hb_value_text = field["v"] or ""
                        if field.get("k") == "ADDRESS" and text_case == "normal" and hb_value_text and hb_value_text.isupper() and len(hb_value_text) > 10:
                            hb_value_text = hb_value_text.title()
                        value_box_h_pt = max(val_size_pt_eff * 1.8, line_height_px * scale * 1.4)
                        if field.get("k") == "ADDRESS":
                            value_box_h_pt = max(value_box_h_pt, line_height_px * scale * 2.6)
                            if advances_flow:
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

                    if advances_flow:
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
                        config_address_max_lines,
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
                                _pil_image_reader(img, preserve_alpha=True),
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
                        if advances_flow:
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

                    for i, line in enumerate(lines[:standard_max_lines]):
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
                                _pil_image_reader(img, preserve_alpha=True),
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
                        if advances_flow:
                            current_y_px += extra_h_px
                
                # Move to next field position
                if advances_flow:
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

        if preserve_vector_template and mode != "editable":
            composed_bytes = _compose_vector_template_export(
                template_pdf_bytes,
                buffer.getvalue(),
                template_card_placements,
                sheet_w_pt,
                sheet_h_pt,
                mode=mode,
            )
            buffer = io.BytesIO(composed_bytes)
            buffer.seek(0)

        if getattr(template, "is_double_sided", False):
            if preserve_vector_back_template and mode != "editable":
                back_side_bytes = _generate_direct_editable_pdf_template_export(
                    template=template,
                    template_id=template_id,
                    students=students,
                    template_pdf_bytes=back_template_pdf_bytes,
                    font_settings=back_font_settings,
                    photo_settings=back_photo_settings,
                    qr_settings=back_qr_settings,
                    layout_config_raw=getattr(template, "back_layout_config", None),
                    labels_map=back_labels_map,
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
                    lang=back_lang,
                    direction=back_direction,
                    reg_font_name=reg_font_name,
                    bold_font_name=bold_font_name,
                    reg_font_path=back_side_reg_path,
                    bold_font_path=back_side_bold_path,
                    side="back",
                    source_language=lang,
                    mode=mode,
                )
                front_doc = fitz.open(stream=buffer.getvalue(), filetype="pdf")
                back_doc = fitz.open(stream=back_side_bytes, filetype="pdf")
                merged_doc = fitz.open()
                max_pages = max(len(front_doc), len(back_doc))
                for page_index in range(max_pages):
                    if page_index < len(front_doc):
                        merged_doc.insert_pdf(front_doc, from_page=page_index, to_page=page_index)
                    if page_index < len(back_doc):
                        merged_doc.insert_pdf(back_doc, from_page=page_index, to_page=page_index)
                merged_bytes = _corel_safe_pdf_bytes(merged_doc, garbage=4, clean=False)
                back_doc.close()
                front_doc.close()
                merged_doc.close()
                buffer = io.BytesIO(merged_bytes)
                buffer.seek(0)
            else:
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
                    merged_bytes = _corel_safe_pdf_bytes(merged_doc, garbage=4, clean=False)
                    back_doc.close()
                    front_doc.close()
                    merged_doc.close()
                    buffer = io.BytesIO(merged_bytes)
                    buffer.seek(0)

        if mode == "editable":
            cleaned_bytes = _make_corel_friendly(buffer.getvalue(), mode=mode)
            if _is_valid_pdf_bytes(cleaned_bytes):
                buffer = io.BytesIO(cleaned_bytes)
            else:
                logger.warning("Final editable Corel cleanup returned invalid PDF; returning original bytes")
                buffer = io.BytesIO(buffer.getvalue())
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
