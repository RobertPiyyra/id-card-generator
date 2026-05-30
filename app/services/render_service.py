import os
import io
import math
import logging
import json
import base64
import requests
import fitz
import time
import textwrap
from PIL import Image, ImageDraw, ImageFont, ImageOps

# Flask / database
from flask import current_app, url_for
from models import db, Student, Template, TemplateField
from app.services.qr_service import generate_qr_code
from app.services.barcode_service import generate_barcode_code128
from app.services.redis_service import (
    REDIS_CACHE_TTL,
    _redis_cache_key,
    _redis_get,
    _redis_set,
    _redis_delete,
    _redis_acquire_lock,
    get_redis_client,
)
from utils import (
    UPLOAD_FOLDER, GENERATED_FOLDER, FONTS_FOLDER, PLACEHOLDER_PATH,
    get_template_settings, get_template_path, get_card_size, apply_text_case,
    get_default_font_config, get_default_photo_config, get_default_qr_config,
    get_photo_settings_for_orientation, get_font_settings_for_orientation,
    get_template_orientation, load_template, load_template_smart, round_photo, is_valid_font_file,
    get_available_fonts, load_font_dynamic, generate_data_hash, process_text_for_drawing,
    download_font_if_missing, flip_x_for_text_direction, get_draw_text_kwargs, trim_transparent_edges,
    force_rgb, get_cloudinary_face_crop_url, get_storage_backend, parse_layout_config,
    get_field_layout_item, split_label_and_colon, colon_anchor_for_value, get_template_language_direction,
    get_template_layout_config, get_anchor_max_text_width, get_layout_flow_start_y,
    derive_font_settings_from_layout_config
)

# Cross references
from app.services.photo_service import load_student_photo_rgba, _process_photo_pil

logger = logging.getLogger(__name__)

# Constants
A4_WIDTH_PX = 2480
A4_HEIGHT_PX = 3508
DPI = 300

def get_legacy_helpers():
    import app.legacy_app as legacy
    return legacy

def _build_student_image_ref(*args, **kwargs):
    return get_legacy_helpers()._build_student_image_ref(*args, **kwargs)

def _build_qr_hash(*args, **kwargs):
    return get_legacy_helpers()._build_qr_hash(*args, **kwargs)

def _build_payload(*args, **kwargs):
    return get_legacy_helpers()._build_payload(*args, **kwargs)

def _get_cached_qr_image(*args, **kwargs):
    return get_legacy_helpers()._get_cached_qr_image(*args, **kwargs)

def _get_cached_barcode_image(*args, **kwargs):
    return get_legacy_helpers()._get_cached_barcode_image(*args, **kwargs)

def _looks_like_pdf_template_source(*args, **kwargs):
    return get_legacy_helpers()._looks_like_pdf_template_source(*args, **kwargs)

def _flatten_to_rgb(*args, **kwargs):
    return get_legacy_helpers()._flatten_to_rgb(*args, **kwargs)

def apply_layout_custom_objects_pil(*args, **kwargs):
    return get_legacy_helpers().apply_layout_custom_objects_pil(*args, **kwargs)

def get_initial_flow_y_for_side(*args, **kwargs):
    return get_legacy_helpers().get_initial_flow_y_for_side(*args, **kwargs)

def field_within_vertical_bounds(*args, **kwargs):
    return get_legacy_helpers().field_within_vertical_bounds(*args, **kwargs)

def fit_dynamic_font_to_single_line(*args, **kwargs):
    return get_legacy_helpers().fit_dynamic_font_to_single_line(*args, **kwargs)

def order_to_field_key(*args, **kwargs):
    return get_legacy_helpers().order_to_field_key(*args, **kwargs)

def translate_value_for_template_side(*args, **kwargs):
    return get_legacy_helpers().translate_value_for_template_side(*args, **kwargs)

def resolve_field_layout_for_side(*args, **kwargs):
    return get_legacy_helpers().resolve_field_layout_for_side(*args, **kwargs)

def field_advances_layout_flow(*args, **kwargs):
    return get_legacy_helpers().field_advances_layout_flow(*args, **kwargs)

def field_consumes_layout_space(*args, **kwargs):
    return get_legacy_helpers().field_consumes_layout_space(*args, **kwargs)

# Helper from legacy_app
def get_template_language_direction_from_obj(template, side="front"):
    lang = "english"
    direction = "ltr"
    if template:
        if side == "back":
            lang = getattr(template, "back_language", "english") or "english"
            direction = getattr(template, "back_direction", "ltr") or "ltr"
        else:
            lang = getattr(template, "language", "english") or "english"
            direction = getattr(template, "direction", "ltr") or "ltr"
    return lang, direction



def _get_cached_photo(student_like, photo_settings, photo_w, photo_h):
    photo_ref = _build_student_image_ref(student_like)

    cache_key = _redis_cache_key(
        "photo",
        photo_ref,
        photo_w,
        photo_h,
        json.dumps(photo_settings, sort_keys=True)
    )

    # 🔍 Try cache
    cached = _redis_get(cache_key)
    if cached:
        try:
            img = Image.open(io.BytesIO(cached))
            img.load()
            return img.convert("RGB")
        except Exception as e:
            logger.warning(f"Photo cache decode failed for {cache_key}: {e}")
            _redis_delete(cache_key)

    # 🚫 Stampede protection
    lock_key = cache_key + ":lock"
    if not _redis_acquire_lock(lock_key, ttl=5):
        time.sleep(0.05)
        cached = _redis_get(cache_key)
        if cached:
            try:
                img = Image.open(io.BytesIO(cached))
                img.load()
                return img.convert("RGB")
            except Exception:
                pass

    try:
        # 🧠 Generate fresh
        img = _load_card_photo_image(student_like, photo_settings, photo_w, photo_h)

        if img:
            try:
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=85, method=6)
                _redis_set(cache_key, buf.getvalue())
            except Exception as e:
                logger.warning(f"Photo cache write failed: {e}")

        return img

    finally:
        _redis_delete(lock_key)


def _get_cached_final_card(
    template_obj,
    student_like,
    side,
    student_id,
    school_name,
    render_scale,
    include_photo=True,
    include_qr=True,
    include_barcode=True,
    include_text=True,
):
    cache_key = _redis_cache_key(
        "final_card",
        template_obj.id,
        str(getattr(template_obj, "updated_at", "no_update")),
        side,
        student_id,
        _build_qr_hash(student_like),
        render_scale,
        include_photo,
        include_qr,
        include_barcode,
        include_text
    )

    # 🔍 Try cache
    cached = _redis_get(cache_key)
    if cached:
        try:
            img = Image.open(io.BytesIO(cached))
            img.load()
            return img.convert("RGB")
        except Exception as e:
            logger.warning(f"Final cache decode failed for {cache_key}: {e}")
            _redis_delete(cache_key)

    # 🚫 Stampede protection
    lock_key = cache_key + ":lock"
    if not _redis_acquire_lock(lock_key, ttl=5):
        time.sleep(0.05)
        cached = _redis_get(cache_key)
        if cached:
            try:
                img = Image.open(io.BytesIO(cached))
                img.load()
                return img.convert("RGB")
            except Exception:
                pass

    try:
        # 🧠 Generate fresh
        img = render_student_card_side(
            template_obj,
            student_like,
            side=side,
            student_id=student_id,
            school_name=school_name,
            render_scale=render_scale,
            include_photo=include_photo,
            include_qr=include_qr,
            include_barcode=include_barcode,
            include_text=include_text
        )

        if img:
            try:
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=85, method=6)
                _redis_set(cache_key, buf.getvalue())
            except Exception as e:
                logger.warning(f"Final card cache write failed: {e}")

        return img

    finally:
        _redis_delete(lock_key)


def _render_qr_and_barcode(template_img, qr_settings, student_like, student_id, school_name, scale=1.0, include_qr=True, include_barcode=True):
    qr_id = _build_qr_hash(student_like)
    if include_qr and qr_settings.get('enable_qr', False):
        qr_payload = _build_payload(qr_settings, student_like, student_id, school_name, 'qr')
        qr_size = max(1, int(round(float(qr_settings.get('qr_size', 120) or 120) * scale)))
        qr_x = int(round(float(qr_settings.get('qr_x', 50) or 50) * scale))
        qr_y = int(round(float(qr_settings.get('qr_y', 50) or 50) * scale))
        qr_img = _get_cached_qr_image(qr_payload, qr_settings, qr_size)
        try:
            template_img.paste(qr_img, (qr_x, qr_y))
        except Exception as exc:
            logger.error('Failed to paste QR code: %s', exc)

    if include_barcode and qr_settings.get('enable_barcode', False):
        barcode_payload = _build_payload(qr_settings, student_like, student_id, school_name, 'barcode')
        barcode_w = max(40, int(round(float(qr_settings.get('barcode_width', 220) or 220) * scale)))
        barcode_h = max(30, int(round(float(qr_settings.get('barcode_height', 70) or 70) * scale)))
        barcode_x = int(round(float(qr_settings.get('barcode_x', 50) or 50) * scale))
        barcode_y = int(round(float(qr_settings.get('barcode_y', 200) or 200) * scale))
        barcode_img = _get_cached_barcode_image(barcode_payload, qr_settings, barcode_w, barcode_h)
        try:
            template_img.paste(barcode_img, (barcode_x, barcode_y))
        except Exception as exc:
            logger.error('Failed to paste barcode: %s', exc)


def _photo_settings_dimensions(photo_settings, scale=1.0):
    photo_w = max(1, int(round(float(photo_settings.get('photo_width', 0) or 0) * scale)))
    photo_h = max(1, int(round(float(photo_settings.get('photo_height', 0) or 0) * scale)))
    photo_x = int(round(float(photo_settings.get('photo_x', 0) or 0) * scale))
    photo_y = int(round(float(photo_settings.get('photo_y', 0) or 0) * scale))
    radii = [
        int(round(float(photo_settings.get('photo_border_top_left', 0) or 0) * scale)),
        int(round(float(photo_settings.get('photo_border_top_right', 0) or 0) * scale)),
        int(round(float(photo_settings.get('photo_border_bottom_right', 0) or 0) * scale)),
        int(round(float(photo_settings.get('photo_border_bottom_left', 0) or 0) * scale)),
    ]
    return photo_w, photo_h, photo_x, photo_y, radii


def _load_card_photo_image(student_like, photo_settings, photo_w, photo_h):
    photo_img = load_student_photo_rgba(
        student_like,
        photo_w,
        photo_h,
        timeout=8,
        photo_settings=photo_settings,
    )
    if photo_img is not None:
        return photo_img

    logger.warning('Using placeholder image for student %s', getattr(student_like, 'id', 'unknown'))
    if not os.path.exists(PLACEHOLDER_PATH):
        return None
    try:
        placeholder = Image.open(PLACEHOLDER_PATH).convert('RGBA')
        return ImageOps.fit(placeholder, (photo_w, photo_h), Image.Resampling.LANCZOS)
    except Exception as exc:
        logger.warning('Unable to load placeholder image: %s', exc)
        return None


def _render_student_photo(template_img, student_like, photo_settings, scale=1.0):
    if not photo_settings.get('enable_photo', True):
        return
    photo_w, photo_h, photo_x, photo_y, radii = _photo_settings_dimensions(photo_settings, scale)
    photo_img = _get_cached_photo(student_like, photo_settings, photo_w, photo_h)    
    if not photo_img:
        return
    try:
        border_color = photo_settings.get('photo_frame_color')
        border_thickness = max(1.0, 2.0 * scale) if border_color else 0
        photo_img = round_photo(photo_img, radii, border_color=border_color, border_thickness=border_thickness)
        template_img.paste(photo_img, (photo_x, photo_y), photo_img)
    except Exception as exc:
        logger.error('Error rendering student photo: %s', exc)


def _build_card_field_list(student_like, template_obj, template_id, lang):
    std_labels = {
        'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
        'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
        'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
        'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'},
    }
    labels_map = std_labels.get(lang, std_labels['english'])
    fields = [
        {'key': 'NAME', 'label': labels_map['NAME'], 'val': getattr(student_like, 'name', '') or '', 'order': 10, 'field_type': 'text', 'translate_label': False},
        {'key': 'F_NAME', 'label': labels_map['F_NAME'], 'val': getattr(student_like, 'father_name', '') or '', 'order': 20, 'field_type': 'text', 'translate_label': False},
        {'key': 'CLASS', 'label': labels_map['CLASS'], 'val': getattr(student_like, 'class_name', '') or '', 'order': 30, 'field_type': 'text', 'translate_label': False},
        {'key': 'DOB', 'label': labels_map['DOB'], 'val': getattr(student_like, 'dob', '') or '', 'order': 40, 'field_type': 'date', 'translate_label': False},
        {'key': 'MOBILE', 'label': labels_map['MOBILE'], 'val': getattr(student_like, 'phone', '') or '', 'order': 50, 'field_type': 'tel', 'translate_label': False},
        {'key': 'ADDRESS', 'label': labels_map['ADDRESS'], 'val': getattr(student_like, 'address', '') or '', 'order': 60, 'field_type': 'textarea', 'translate_label': False},
    ]
    custom_data = getattr(student_like, '_template_fields', None) or getattr(student_like, 'custom_data', None) or {}
    for field in _get_render_dynamic_fields(student_like, template_id):
        fields.append({
            'key': field.field_name,
            'label': field.field_label,
            'val': custom_data.get(field.field_name, '') or '',
            'order': field.display_order,
            'field_type': field.field_type,
            'translate_label': True,
        })
    return sorted(fields, key=lambda item: int(item.get('order') or 0))


def draw_text_gradient(draw, position, text, font, top_color, bottom_color, enable_gradient, lang, target_image=None, **kwargs):
    """Draws text with a vertical gradient from top_color to bottom_color if enable_gradient is True."""
    if not text:
        return
    if not enable_gradient or not target_image:
        draw.text(position, text, font=font, fill=top_color, **kwargs)
        return
    try:
        bbox = draw.textbbox((0, 0), text, font=font, **kwargs)
        w = int(bbox[2] - bbox[0])
        h = int(bbox[3] - bbox[1])
        if w <= 0 or h <= 0:
            draw.text(position, text, font=font, fill=top_color, **kwargs)
            return
        
        pad = 20
        # Draw text mask
        mask = Image.new("L", (w + pad * 2, h + pad * 2), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=255, **kwargs)
        
        # Build gradient
        gradient = Image.new("RGBA", (w + pad * 2, h + pad * 2))
        
        def to_rgb(c):
            if isinstance(c, (list, tuple)):
                return tuple(c[:3])
            if isinstance(c, str) and c.startswith('#'):
                h_val = c.lstrip('#')
                return tuple(int(h_val[i:i+2], 16) for i in (0, 2, 4))
            return (0, 0, 0)
            
        rgb_top = to_rgb(top_color)
        rgb_bottom = to_rgb(bottom_color)
        
        for y_idx in range(h + pad * 2):
            factor = y_idx / float(h + pad * 2 - 1) if (h + pad * 2 > 1) else 0.0
            r = int(rgb_top[0] + (rgb_bottom[0] - rgb_top[0]) * factor)
            g = int(rgb_top[1] + (rgb_bottom[1] - rgb_top[1]) * factor)
            b = int(rgb_top[2] + (rgb_bottom[2] - rgb_top[2]) * factor)
            for x_idx in range(w + pad * 2):
                gradient.putpixel((x_idx, y_idx), (r, g, b, 255))
                
        paste_x = int(position[0] + bbox[0] - pad)
        paste_y = int(position[1] + bbox[1] - pad)
        target_image.paste(gradient, (paste_x, paste_y), mask)
    except Exception as e:
        logging.warning(f"Error drawing text gradient: {e}")
        draw.text(position, text, font=font, fill=top_color, **kwargs)


def _render_student_fields(template_img, template_obj, student_like, font_settings, photo_settings, side, lang, direction):
    template_id = template_obj.id
    card_width = template_img.width
    card_height = template_img.height
    font_bold_path = os.path.join(FONTS_FOLDER, font_settings['font_bold'])
    font_reg_path = os.path.join(FONTS_FOLDER, font_settings['font_regular'])

    label_fill_default = tuple(font_settings.get('label_font_color', [0, 0, 0]))
    value_fill_default = tuple(font_settings.get('value_font_color', [0, 0, 0]))
    colon_fill_default = tuple(font_settings.get('colon_font_color', list(label_fill_default)))
    
    enable_label_gradient = bool(font_settings.get('enable_label_gradient', False))
    label_fill_bottom = tuple(font_settings.get('label_font_color_bottom', [51, 51, 51]))
    
    enable_value_gradient = bool(font_settings.get('enable_value_gradient', False))
    value_fill_bottom = tuple(font_settings.get('value_font_color_bottom', [51, 51, 51]))
    
    enable_colon_gradient = bool(font_settings.get('enable_colon_gradient', False))
    colon_fill_bottom = tuple(font_settings.get('colon_font_color_bottom', [51, 51, 51]))

    text_case = font_settings.get('text_case', 'normal')
    show_label_colon = bool(font_settings.get('show_label_colon', True))
    align_label_colon = bool(font_settings.get('align_label_colon', True))
    label_colon_gap = int(font_settings.get('label_colon_gap', 8) or 8)

    p_x = photo_settings.get('photo_x', 0) if photo_settings.get('enable_photo', True) else 0
    p_y = photo_settings.get('photo_y', 0) if photo_settings.get('enable_photo', True) else 0
    p_w = photo_settings.get('photo_width', 0) if photo_settings.get('enable_photo', True) else 0
    p_h = photo_settings.get('photo_height', 0) if photo_settings.get('enable_photo', True) else 0

    draw = ImageDraw.Draw(template_img)
    fields = _build_card_field_list(student_like, template_obj, template_id, lang)

    label_x = font_settings['label_x']
    value_x = font_settings['value_x']
    current_y = get_initial_flow_y_for_side(template_obj, font_settings, side=side)
    line_height = font_settings['line_height']
    address_max_lines = int(font_settings.get("address_max_lines", 2))

    for item in fields:
        label_source = item['label']
        if item.get('translate_label'):
            label_source = translate_value_for_template_side(
                template_obj,
                side,
                label_source,
                field_key=f"{item.get('key')}_LABEL",
                field_type='label',
            )
        raw_label = apply_text_case(label_source, text_case)
        translated_value = translate_value_for_template_side(
            template_obj,
            side,
            item['val'],
            field_key=item.get('key'),
            field_type=item.get('field_type'),
        )
        raw_val = apply_text_case(translated_value, text_case)
        display_label = process_text_for_drawing(raw_label, lang)
        display_val = process_text_for_drawing(raw_val, lang)

        field_key = item.get('key') or order_to_field_key(item.get('order'))
        layout_item = resolve_field_layout_for_side(template_obj, field_key, label_x, value_x, current_y, side=side)
        if not field_within_vertical_bounds(layout_item, current_y, card_height):
            continue

        label_x_eff = layout_item['label_x']
        value_x_eff = layout_item['value_x']
        label_y_eff = layout_item['label_y']
        value_y_eff = layout_item['value_y']
        label_fill = layout_item.get('label_color') or label_fill_default
        value_fill = layout_item.get('value_color') or value_fill_default
        colon_fill = layout_item.get('colon_color') or colon_fill_default
        label_font_size_eff = max(1, int(layout_item.get('label_font_size') or font_settings['label_font_size']))
        value_font_size_eff = max(1, int(layout_item.get('value_font_size') or font_settings['value_font_size']))
        colon_font_size_eff = max(1, int(layout_item.get('colon_font_size') or label_font_size_eff))
        colon_y_eff = layout_item.get('colon_y', label_y_eff)
        colon_x_eff = layout_item.get('colon_x')
        colon_grow_eff = layout_item.get('colon_grow')

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

        label_font = load_font_dynamic(font_bold_path, label_text_final, 10**9, label_font_size_eff, language=lang)
        colon_font = load_font_dynamic(font_bold_path, colon_text_final or ':', 10**9, colon_font_size_eff, language=lang)
        if layout_item['label_visible']:
            label_draw_x = flip_x_for_text_direction(
                label_x_eff, label_text_final, label_font, card_width, direction, draw=draw, grow_mode=layout_item['label_grow']
            )
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
                **get_draw_text_kwargs(label_text_final, lang)
            )
            draw_aligned_colon_pil(
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
            grow_mode=layout_item['value_grow'],
            photo_x=p_x,
            photo_y=p_y,
            photo_width=p_w,
            photo_height=p_h,
            page_margin=20,
            photo_gap=15,
            min_width=20,
        ))

        if field_key == 'ADDRESS':
            curr_size = value_font_size_eff
            min_size = 10
            wrapped_addr = []
            while curr_size >= min_size:
                addr_font = load_font_dynamic(font_reg_path, 'X', 10**9, curr_size, language=lang)
                avg_char_w = curr_size * 0.50
                chars_limit = max(5, int(max_w / max(avg_char_w, 1)))
                wrapped_addr = textwrap.wrap(raw_val, width=chars_limit, break_long_words=True)
                fits_horizontally = len(wrapped_addr) <= address_max_lines
                if fits_horizontally:
                    for line in wrapped_addr:
                        measure_text = process_text_for_drawing(line, lang)
                        if draw.textlength(measure_text, font=addr_font, **get_draw_text_kwargs(measure_text, lang)) > max_w:
                            fits_horizontally = False
                            break
                if fits_horizontally:
                    break
                curr_size -= 2
            if curr_size < min_size:
                addr_font = load_font_dynamic(font_reg_path, 'X', 10**9, min_size, language=lang)
            for line in wrapped_addr[:address_max_lines]:
                line_display = process_text_for_drawing(line, lang)
                if layout_item['value_visible']:
                    value_draw_x = flip_x_for_text_direction(
                        value_x_eff, line_display, addr_font, card_width, direction, draw=draw, grow_mode=layout_item['value_grow']
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
                        **get_draw_text_kwargs(line_display, lang)
                    )
                spacing = line_height if curr_size > 20 else curr_size + 5
                value_y_eff += spacing
                if advances_flow:
                    current_y += spacing
            continue

        value_font, _ = fit_dynamic_font_to_single_line(
            draw,
            font_reg_path,
            display_val,
            max_w,
            value_font_size_eff,
            language=lang,
        )
        if layout_item['value_visible']:
            value_draw_x = flip_x_for_text_direction(
                value_x_eff, display_val, value_font, card_width, direction, draw=draw, grow_mode=layout_item['value_grow']
            )
            draw_text_gradient(
                draw,
                (value_draw_x, value_y_eff),
                display_val,
                font=value_font,
                top_color=value_fill,
                bottom_color=value_fill_bottom,
                enable_gradient=enable_value_gradient,
                lang=lang,
                target_image=template_img,
                **get_draw_text_kwargs(display_val, lang)
            )
        if advances_flow:
            current_y += line_height


def render_student_card_side_background(
    template_obj,
    student_like,
    side='front',
    student_id=None,
    school_name=None,
    render_scale=1.0,
    include_photo=True,
    include_qr=True,
    include_barcode=True,
):
    return _get_cached_final_card(
    template_obj,
    student_like,
    side=side,
    student_id=student_id,
    school_name=school_name,
    render_scale=render_scale,
    include_photo=include_photo,
    include_qr=include_qr,
    include_barcode=include_barcode,
    include_text=False
)


def render_student_card_side(
    template_obj,
    student_like,
    side='front',
    student_id=None,
    school_name=None,
    render_scale=1.0,
    include_photo=True,
    include_qr=True,
    include_barcode=True,
    include_text=True,
):
    if not template_obj:
        return None

    template_id = template_obj.id
    template_path = get_template_path(template_id, side=side)
    if not template_path:
        return None

    font_settings, photo_settings, qr_settings, _ = get_template_settings(template_id, side=side)
    card_width, card_height = get_card_size(template_id)
    template_img = _load_template_image_for_render(template_path, card_width, card_height, render_scale=render_scale)
    lang, direction = get_template_language_direction_from_obj(template_obj, side=side)

    if include_text:
        _render_student_fields(template_img, template_obj, student_like, font_settings, photo_settings, side, lang, direction)

    if include_photo:
        _render_student_photo(template_img, student_like, photo_settings, scale=max(1.0, float(render_scale or 1.0)))

    if include_qr or include_barcode:
        _render_qr_and_barcode(template_img, qr_settings, student_like, student_id, school_name, scale=max(1.0, float(render_scale or 1.0)), include_qr=include_qr, include_barcode=include_barcode)

    if template_img.size != (
        max(1, int(round(card_width * max(1.0, float(render_scale or 1.0))))),
        max(1, int(round(card_height * max(1.0, float(render_scale or 1.0))))),
    ):
        template_img = template_img.resize(
            (
                max(1, int(round(card_width * max(1.0, float(render_scale or 1.0))))),
                max(1, int(round(card_height * max(1.0, float(render_scale or 1.0))))),
            ),
            Image.LANCZOS,
        )

    apply_layout_custom_objects_pil(template_img, template_obj, font_settings, side=side, language=lang, render_scale=max(1.0, float(render_scale or 1.0)))
    return template_img


def draw_aligned_colon_pil(
    draw,
    image_width,
    direction,
    value_x,
    y,
    colon_text,
    colon_font,
    fill,
    language,
    colon_gap,
    anchor_x=None,
    grow_mode=None,
    target_image=None,
    enable_gradient=False,
    bottom_color=None,
):
    """Draw a standalone aligned colon near the value anchor with optional gradient support."""
    if not colon_text:
        return
    if anchor_x is None:
        colon_anchor_x, colon_grow = colon_anchor_for_value(value_x, direction, gap_px=colon_gap)
    else:
        colon_anchor_x = anchor_x
        colon_grow = grow_mode or ("left" if str(direction or "ltr").strip().lower() == "rtl" else "right")
    colon_draw_x = flip_x_for_text_direction(
        colon_anchor_x,
        colon_text,
        colon_font,
        image_width,
        direction,
        draw=draw,
        grow_mode=colon_grow,
    )
    draw_text_gradient(
        draw,
        (colon_draw_x, y),
        colon_text,
        font=colon_font,
        top_color=fill,
        bottom_color=bottom_color,
        enable_gradient=enable_gradient,
        lang=language,
        target_image=target_image,
        **get_draw_text_kwargs(colon_text, language)
    )


def _load_template_image_for_render_cached(path_or_url, target_w, target_h, scale_key):
    target_w = max(1, int(target_w or 1))
    target_h = max(1, int(target_h or 1))
    scale = max(1.0, float(scale_key or 1.0))
    image_open = getattr(Image, "open_original", Image.open)

    if _looks_like_pdf_template_source(path_or_url):
        try:
            if str(path_or_url).startswith(("http://", "https://")):
                resp = requests.get(path_or_url, timeout=15)
                resp.raise_for_status()
                payload = resp.content or b""
                pdf_header_pos = payload.find(b"%PDF")
                if pdf_header_pos >= 0:
                    payload = payload[pdf_header_pos:]
                pdf_doc = fitz.open(stream=payload, filetype="pdf")
            else:
                pdf_doc = fitz.open(path_or_url)
            try:
                page = pdf_doc[0]
                render_dpi = max(int(DPI), int(round(DPI * scale)))
                pix = page.get_pixmap(dpi=render_dpi, alpha=False, colorspace=fitz.csRGB)
                img = image_open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            finally:
                pdf_doc.close()
            if img.size != (target_w, target_h):
                img = img.resize((target_w, target_h), Image.LANCZOS)
        except Exception as exc:
            logger.warning("High-DPI PDF template render failed for %s: %s", path_or_url, exc)
            img = load_template_smart(path_or_url)
            img = _flatten_to_rgb(img)
            if img.size != (target_w, target_h):
                img = img.resize((target_w, target_h), Image.LANCZOS)
    else:
        img = load_template_smart(path_or_url)
        img = _flatten_to_rgb(img)
        if img.size != (target_w, target_h):
            img = img.resize((target_w, target_h), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _load_template_image_for_render(path_or_url, card_width, card_height, render_scale=1.0):
    """
    Load a template image for rendering, with optional higher-DPI PDF rasterization.

    This is used by the compiled Corel export path to make uploaded PDF templates look
    stronger when we intentionally flatten the template background for compatibility.
    """
    scale = max(1.0, float(render_scale or 1.0))
    target_w = max(1, int(round(float(card_width) * scale)))
    target_h = max(1, int(round(float(card_height) * scale)))

    cache_key = round(scale, 3)
    payload = _load_template_image_for_render_cached(path_or_url, target_w, target_h, cache_key)
    return Image.open(io.BytesIO(payload)).convert("RGB")


def _get_render_dynamic_fields(student_like, template_id):
    """Allow callers like bulk generation to inject preloaded template fields."""
    cached_fields = getattr(student_like, "_template_fields", None)
    if cached_fields is not None:
        return cached_fields
    return TemplateField.query.filter_by(template_id=template_id).order_by(TemplateField.display_order.asc()).all()


def build_student_card_text_runs(template_obj, student_like, side="front"):
    """Return text draw instructions using the same layout flow as the PIL renderer."""
    if not template_obj:
        return {"runs": [], "lang": "english", "direction": "ltr", "card_width": 0, "card_height": 0}

    template_id = template_obj.id
    font_settings, photo_settings, _, _ = get_template_settings(template_id, side=side)
    card_width, card_height = get_card_size(template_id)
    measure_img = Image.new("RGB", (max(1, card_width), max(1, card_height)), (255, 255, 255))
    draw = ImageDraw.Draw(measure_img)

    try:
        label_fill_default = tuple(font_settings.get("label_font_color", [0, 0, 0]))
        value_fill_default = tuple(font_settings.get("value_font_color", [0, 0, 0]))
        colon_fill_default = tuple(font_settings.get("colon_font_color", list(label_fill_default)))
    except Exception:
        label_fill_default = (0, 0, 0)
        value_fill_default = (0, 0, 0)
        colon_fill_default = label_fill_default

    lang, direction = get_template_language_direction_from_obj(template_obj, side=side)
    font_bold_path = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
    font_reg_path = os.path.join(FONTS_FOLDER, font_settings["font_regular"])

    std_labels = {
        'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
        'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
        'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
        'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
    }
    labels_map = std_labels.get(lang, std_labels['english'])

    text_case = font_settings.get("text_case", "normal")
    show_label_colon = bool(font_settings.get("show_label_colon", True))
    align_label_colon = bool(font_settings.get("align_label_colon", True))
    label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)

    all_fields = [
        {'key': 'NAME', 'label': labels_map['NAME'], 'val': getattr(student_like, "name", "") or "", 'order': 10, 'field_type': 'text', 'translate_label': False},
        {'key': 'F_NAME', 'label': labels_map['F_NAME'], 'val': getattr(student_like, "father_name", "") or "", 'order': 20, 'field_type': 'text', 'translate_label': False},
        {'key': 'CLASS', 'label': labels_map['CLASS'], 'val': getattr(student_like, "class_name", "") or "", 'order': 30, 'field_type': 'text', 'translate_label': False},
        {'key': 'DOB', 'label': labels_map['DOB'], 'val': getattr(student_like, "dob", "") or "", 'order': 40, 'field_type': 'date', 'translate_label': False},
        {'key': 'MOBILE', 'label': labels_map['MOBILE'], 'val': getattr(student_like, "phone", "") or "", 'order': 50, 'field_type': 'tel', 'translate_label': False},
        {'key': 'ADDRESS', 'label': labels_map['ADDRESS'], 'val': getattr(student_like, "address", "") or "", 'order': 60, 'field_type': 'textarea', 'translate_label': False},
    ]

    custom_data = getattr(student_like, "custom_data", None) or {}
    for field in _get_render_dynamic_fields(student_like, template_id):
        all_fields.append({
            'key': field.field_name,
            'label': field.field_label,
            'val': custom_data.get(field.field_name, "") or "",
            'order': field.display_order,
            'field_type': field.field_type,
            'translate_label': True,
        })
    all_fields.sort(key=lambda item: item['order'])

    photo_enabled = bool(photo_settings.get("enable_photo", True))
    p_x = photo_settings.get("photo_x", 0) if photo_enabled else 0
    p_y = photo_settings.get("photo_y", 0) if photo_enabled else 0
    p_w = photo_settings.get("photo_width", 0) if photo_enabled else 0
    p_h = photo_settings.get("photo_height", 0) if photo_enabled else 0

    label_x = font_settings["label_x"]
    value_x = font_settings["value_x"]
    current_y = get_initial_flow_y_for_side(template_obj, font_settings, side=side)
    line_height = font_settings["line_height"]
    runs = []
    address_max_lines = int(font_settings.get("address_max_lines", 2))

    for item in all_fields:
        label_source = item['label']
        if item.get('translate_label'):
            label_source = translate_value_for_template_side(
                template_obj,
                side,
                label_source,
                field_key=f"{item.get('key')}_LABEL",
                field_type='label',
            )
        raw_label = apply_text_case(label_source, text_case)
        translated_value = translate_value_for_template_side(
            template_obj,
            side,
            item['val'],
            field_key=item.get('key'),
            field_type=item.get('field_type'),
        )
        raw_val = apply_text_case(translated_value, text_case)
        display_label = process_text_for_drawing(raw_label, lang)
        display_val = process_text_for_drawing(raw_val, lang)
        field_key = item.get('key') or order_to_field_key(item.get('order'))
        layout_item = resolve_field_layout_for_side(template_obj, field_key, label_x, value_x, current_y, side=side)
        if not field_within_vertical_bounds(layout_item, current_y, card_height):
            continue
        label_x_eff = layout_item["label_x"]
        value_x_eff = layout_item["value_x"]
        label_y_eff = layout_item["label_y"]
        value_y_eff = layout_item["value_y"]
        label_fill = layout_item.get("label_color") or label_fill_default
        value_fill = layout_item.get("value_color") or value_fill_default
        colon_fill = layout_item.get("colon_color") or colon_fill_default
        label_font_size_eff = max(1, int(layout_item.get("label_font_size") or font_settings["label_font_size"]))
        value_font_size_eff = max(1, int(layout_item.get("value_font_size") or font_settings["value_font_size"]))
        colon_font_size_eff = max(1, int(layout_item.get("colon_font_size") or label_font_size_eff))
        colon_y_eff = layout_item.get("colon_y", label_y_eff)
        colon_x_eff = layout_item.get("colon_x")
        colon_grow_eff = layout_item.get("colon_grow")

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

        label_font = load_font_dynamic(font_bold_path, label_text_final or "X", 10**9, label_font_size_eff, language=lang)
        colon_font = load_font_dynamic(font_bold_path, colon_text_final or ":", 10**9, colon_font_size_eff, language=lang)
        if layout_item["label_visible"] and label_text_final:
            label_draw_x = flip_x_for_text_direction(
                label_x_eff, label_text_final, label_font, card_width, direction, draw=draw, grow_mode=layout_item["label_grow"]
            )
            runs.append({
                "part": "label",
                "text": label_text_final,
                "x": int(label_draw_x),
                "y": int(label_y_eff),
                "font_path": font_bold_path,
                "font_size": int(label_font_size_eff),
                "color": tuple(label_fill),
                "language": lang,
                "direction": direction,
            })
            if colon_text_final:
                if colon_x_eff is None:
                    colon_anchor_x, colon_grow = colon_anchor_for_value(value_x_eff, direction, gap_px=label_colon_gap)
                else:
                    colon_anchor_x = colon_x_eff
                    colon_grow = colon_grow_eff or ("left" if str(direction or "ltr").strip().lower() == "rtl" else "right")
                colon_draw_x = flip_x_for_text_direction(
                    colon_anchor_x, colon_text_final, colon_font, card_width, direction, draw=draw, grow_mode=colon_grow
                )
                runs.append({
                    "part": "colon",
                    "text": colon_text_final,
                    "x": int(colon_draw_x),
                    "y": int(colon_y_eff),
                    "font_path": font_bold_path,
                    "font_size": int(colon_font_size_eff),
                    "color": tuple(colon_fill),
                    "language": lang,
                    "direction": direction,
                })

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

        if field_key == "ADDRESS":
            curr_size = value_font_size_eff
            min_size = 10
            wrapped_addr = []
            while curr_size >= min_size:
                addr_font = load_font_dynamic(font_reg_path, "X", 10**9, curr_size, language=lang)
                avg_char_w = curr_size * 0.50
                chars_limit = max(5, int(max_w / max(avg_char_w, 1))) if avg_char_w > 0 else 20
                wrapped_addr = textwrap.wrap(raw_val, width=chars_limit, break_long_words=True)
                fits_horizontally = len(wrapped_addr) <= address_max_lines
                if fits_horizontally:
                    for line in wrapped_addr:
                        measure_text = process_text_for_drawing(line, lang)
                        if draw.textlength(measure_text, font=addr_font, **get_draw_text_kwargs(measure_text, lang)) > max_w:
                            fits_horizontally = False
                            break
                if fits_horizontally:
                    break
                curr_size -= 2
            if curr_size < min_size:
                addr_font = load_font_dynamic(font_reg_path, "X", 10**9, min_size, language=lang)
            for line in wrapped_addr[:address_max_lines]:
                line_display = process_text_for_drawing(line, lang)
                if layout_item["value_visible"]:
                    value_draw_x = flip_x_for_text_direction(
                        value_x_eff, line_display, addr_font, card_width, direction, draw=draw, grow_mode=layout_item["value_grow"]
                    )
                    runs.append({
                        "part": "value",
                        "text": line_display,
                        "x": int(value_draw_x),
                        "y": int(value_y_eff),
                        "font_path": font_reg_path,
                        "font_size": int(curr_size if curr_size >= min_size else min_size),
                        "color": tuple(value_fill),
                        "language": lang,
                        "direction": direction,
                    })
                spacing = line_height if curr_size > 20 else curr_size + 5
                value_y_eff += spacing
                if advances_flow:
                    current_y += spacing
            continue

        value_font, fitted_value_font_size = fit_dynamic_font_to_single_line(
            draw,
            font_reg_path,
            display_val,
            max_w,
            value_font_size_eff,
            language=lang,
        )
        if layout_item["value_visible"]:
            value_draw_x = flip_x_for_text_direction(
                value_x_eff, display_val, value_font, card_width, direction, draw=draw, grow_mode=layout_item["value_grow"]
            )
            runs.append({
                "part": "value",
                "text": display_val,
                "x": int(value_draw_x),
                "y": int(value_y_eff),
                "font_path": font_reg_path,
                "font_size": int(fitted_value_font_size),
                "color": tuple(value_fill),
                "language": lang,
                "direction": direction,
            })
        if advances_flow:
            current_y += line_height

    return {
        "runs": runs,
        "lang": lang,
        "direction": direction,
        "card_width": card_width,
        "card_height": card_height,
    }
