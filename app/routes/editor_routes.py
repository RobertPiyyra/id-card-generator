from app.legacy_app import admin_required
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, send_file, make_response, current_app
from models import db, Template, TemplateField
from sqlalchemy.orm.attributes import flag_modified
from app.services.template_lifecycle_service import create_template_version_snapshot, log_immutable_audit_event, get_session_actor
from utils import (
    get_template_path,
    load_template_smart,
    get_template_settings,
    get_template_language_direction,
    parse_layout_config,
    derive_font_settings_from_layout_config,
    normalize_photo_shape,
)
import json
import io
import time
import os
from itsdangerous import URLSafeSerializer, BadSignature

editor_bp = Blueprint('editor', __name__)


def _normalize_editor_side(template, side):
    side_name = (side or "front").strip().lower()
    if side_name not in {"front", "back"}:
        side_name = "front"
    if side_name == "back" and not getattr(template, "is_double_sided", False):
        side_name = "front"
    return side_name


def _editor_image_token_serializer():
    return URLSafeSerializer(current_app.secret_key or "editor-image-fallback", salt="editor-template-image")


def _make_editor_image_token(template_id, side):
    return _editor_image_token_serializer().dumps({
        "template_id": int(template_id),
        "side": side,
    })


def _is_valid_editor_image_token(token, template_id, side):
    if not token:
        return False
    try:
        payload = _editor_image_token_serializer().loads(token)
    except BadSignature:
        return False
    return payload == {"template_id": int(template_id), "side": side}


def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _normalize_rgb_list(value, default=None):
    if value is None:
        return default

    if isinstance(value, str):
        color = value.strip()
        if color.startswith("#") and len(color) in {4, 7}:
            try:
                if len(color) == 4:
                    color = "#" + "".join(ch * 2 for ch in color[1:])
                return [int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)]
            except (TypeError, ValueError):
                return default
        if "," in color:
            value = color.split(",")

    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return [max(0, min(255, int(component))) for component in value[:3]]
        except (TypeError, ValueError):
            return default

    return default


def _merge_font_settings(current_font, incoming_font):
    if not isinstance(incoming_font, dict):
        return current_font or {}

    merged = dict(current_font or {})
    string_keys = ("font_bold", "font_regular", "text_case")
    int_keys = (
        "label_font_size", "value_font_size", "label_x", "value_x",
        "start_y", "line_height", "label_colon_gap", "address_max_lines",
    )
    bool_keys = ("show_label_colon", "align_label_colon")
    color_keys = ("label_font_color", "value_font_color", "colon_font_color", "font_color")

    for key in string_keys:
        if key in incoming_font and incoming_font[key] is not None:
            merged[key] = str(incoming_font[key])

    for key in int_keys:
        if key in incoming_font:
            try:
                merged[key] = int(incoming_font[key])
            except (TypeError, ValueError):
                pass

    for key in bool_keys:
        if key in incoming_font:
            merged[key] = _coerce_bool(incoming_font[key], bool(merged.get(key, False)))

    for key in color_keys:
        if key in incoming_font:
            normalized_color = _normalize_rgb_list(
                incoming_font[key],
                _normalize_rgb_list(merged.get(key)),
            )
            if normalized_color is not None:
                merged[key] = normalized_color

    return merged


def _assign_template_json_setting(template, attr_name, value):
    setattr(template, attr_name, dict(value or {}))
    flag_modified(template, attr_name)


def _template_source_name(template, side):
    if side == "back":
        source_path = getattr(template, "back_filename", None) or getattr(template, "back_template_url", None) or ""
        fallback = "No back source"
    else:
        source_path = getattr(template, "filename", None) or getattr(template, "template_url", None) or ""
        fallback = "No source"

    source_name = os.path.basename(str(source_path).split("?", 1)[0]) if source_path else ""
    if source_name and len(source_name) > 90:
        source_name = source_name[:87] + "..."
    return source_name or fallback


def _template_settings_payload(template, side):
    font_settings, photo_settings, qr_settings, orientation = get_template_settings(template.id, side=side)
    layout_raw = template.back_layout_config if side == "back" else template.layout_config
    language = template.back_language if side == "back" else template.language
    text_direction = template.back_text_direction if side == "back" else template.text_direction
    deadline = template.deadline.strftime("%Y-%m-%dT%H:%M") if getattr(template, "deadline", None) else ""

    return {
        "success": True,
        "template_id": template.id,
        "side": side,
        "is_double_sided": bool(getattr(template, "is_double_sided", False)),
        "font_settings": font_settings or {},
        "photo_settings": photo_settings or {},
        "qr_settings": qr_settings or {},
        "layout_config": parse_layout_config(layout_raw),
        "language": language or "english",
        "text_direction": text_direction or "ltr",
        "card_orientation": orientation or template.card_orientation or "landscape",
        "deadline": deadline,
        "card_width": template.card_width or 1015,
        "card_height": template.card_height or 661,
        "sheet_width": template.sheet_width or 2480,
        "sheet_height": template.sheet_height or 3508,
        "grid_rows": template.grid_rows or 5,
        "grid_cols": template.grid_cols or 2,
        "source_name": _template_source_name(template, side),
    }


# =========================================================
# 1. Main Editor Page Route
# =========================================================
@editor_bp.route("/admin/template_editor/<int:template_id>")
@admin_required
def template_editor(template_id):
    
    template = db.session.get(Template, template_id)
    if not template: return "Template not found", 404
    if session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
        return "Unauthorized access", 403
    side = _normalize_editor_side(template, request.args.get("side"))
    
    # URL for the template background image (served via our helper route)
    image_url = url_for(
        'editor.get_template_image',
        template_id=template.id,
        side=side,
        token=_make_editor_image_token(template.id, side),
        v=int(time.time() * 1000),
    )
    font_settings = template.back_font_settings if side == "back" else template.font_settings
    photo_settings = template.back_photo_settings if side == "back" else template.photo_settings
    qr_settings = template.back_qr_settings if side == "back" else template.qr_settings
    layout_config = template.back_layout_config if side == "back" else template.layout_config
    language = template.back_language if side == "back" else template.language
    text_direction = template.back_text_direction if side == "back" else template.text_direction
    editor_dynamic_fields = []
    for field in (
        db.session.query(TemplateField)
        .filter_by(template_id=template.id)
        .order_by(TemplateField.display_order.asc())
        .all()
    ):
        editor_dynamic_fields.append({
            "key": field.field_name,
            "label": field.field_label,
            "order": field.display_order,
            "show_label_front": bool(getattr(field, "show_label_front", True)),
            "show_value_front": bool(getattr(field, "show_value_front", True)),
            "show_label_back": bool(getattr(field, "show_label_back", False)),
            "show_value_back": bool(getattr(field, "show_value_back", False)),
        })
    
    return render_template(
        'visual_editor.html', 
        template=template,
        template_id=template_id,
        image_url=image_url,
        editor_side=side,
        editor_font_settings=font_settings or {},
        editor_photo_settings=photo_settings or {},
        editor_qr_settings=qr_settings or {},
        editor_layout_config=layout_config or "{}",
        editor_language=(language or "english"),
        editor_text_direction=(text_direction or "ltr"),
        editor_dynamic_fields=editor_dynamic_fields,
    )

# =========================================================
# 2. Helper: Serve Template as Flat Image (JPG)
# =========================================================
@editor_bp.route("/editor/get_template_image/<int:template_id>")
@admin_required
def get_template_image(template_id):
    """
    Serves the template file (PDF or Image) as a high-quality JPEG 
    for the visual editor canvas.
    """
    template = db.session.get(Template, template_id)
    if template and session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
        return "Unauthorized access", 403
    side = _normalize_editor_side(template, request.args.get("side")) if template else "front"
    if not session.get("admin"):
        token = (request.args.get("token") or "").strip()
        if not _is_valid_editor_image_token(token, template_id, side):
            return "Unauthorized", 403

    template_path = get_template_path(template_id, side=side)
    if not template_path:
        return "File not found", 404

    try:
        # 1. Load using robust utility (handles PDF conversion automatically)
        img = load_template_smart(template_path)
        
        # 2. Convert to RGB to ensure compatibility (drops Alpha channel)
        if img.mode != 'RGB':
            img = img.convert('RGB')
            
        # 3. Save to memory buffer as JPEG
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=95)
        buffer.seek(0)
        
        response = send_file(buffer, mimetype='image/jpeg')
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    except Exception as e:
        logger.error(f"Editor Image Error: {e}")
        return "Error processing image", 500

# =========================================================
# 3. API: Get Individual Field Settings
# =========================================================
@editor_bp.route('/admin/get_editor_fields/<int:template_id>')
@admin_required
def get_editor_fields(template_id):
    """
    Returns JSON of the template's layout_config.
    """

    template = db.session.get(Template, template_id)
    if not template: return jsonify({"error": "Template not found"}), 404
    if session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
        return jsonify({"error": "Unauthorized access"}), 403
    
    side = request.args.get("side", "front").strip().lower()
    if side == "back" and getattr(template, "is_double_sided", False):
        layout_config_raw = template.back_layout_config
    else:
        layout_config_raw = template.layout_config

    parsed_layout = parse_layout_config(layout_config_raw)
    return jsonify(parsed_layout)


@editor_bp.route('/admin/template_settings/<int:template_id>')
@admin_required
def get_template_settings_api(template_id):
    """
    Returns the current side-specific template settings used by both editors.
    Admin Settings uses this to avoid stale data when returning from Visual Editor.
    """

    template = db.session.get(Template, template_id)
    if not template: return jsonify({"success": False, "error": "Template not found"}), 404
    if session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
        return jsonify({"success": False, "error": "Unauthorized access"}), 403

    side = _normalize_editor_side(template, request.args.get("side"))
    return jsonify(_template_settings_payload(template, side))

# =========================================================
# 4. API: Save All Settings (Global + Fields)
# =========================================================
@editor_bp.route("/admin/save_field_settings", methods=["POST"])
@admin_required
def save_field_settings():
    """
    Saves BOTH the global template settings (Photo/QR position) 
    AND the individual text field positions.
    """
    
    data = request.json
    template_id = data.get('template_id')

    if not template_id:
        return jsonify({"error": "template_id missing"}), 400
    
    try:
        template_id = int(template_id)
    except ValueError:
        return jsonify({"error": "invalid template_id"}), 400

    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({"error": "Template not found"}), 404
    if session.get("admin_role") == "school_admin" and template.school_name != session.get("admin_school"):
        return jsonify({"error": "Unauthorized access"}), 403

    settings_side = str(data.get("settings_side") or "front").strip().lower()
    if settings_side not in {"front", "back"}:
        settings_side = "front"

    try:
        parsed_layout = None

        # Save side language/direction from either editor.
        allowed_languages = {"english", "urdu", "hindi", "arabic"}
        allowed_directions = {"ltr", "rtl"}
        incoming_language = str(data.get("language") or "").strip().lower()
        incoming_direction = str(data.get("text_direction") or "").strip().lower()
        if incoming_language in allowed_languages:
            if settings_side == "back":
                template.back_language = incoming_language
            else:
                template.language = incoming_language
        if incoming_direction in allowed_directions:
            if settings_side == "back":
                template.back_text_direction = incoming_direction
            else:
                template.text_direction = incoming_direction

        # Save global text settings from Visual Editor as the same source used by Admin Settings.
        if 'font_settings' in data and isinstance(data.get('font_settings'), dict):
            current_font = (template.back_font_settings if settings_side == "back" else template.font_settings) or {}
            merged_font = _merge_font_settings(current_font, data.get('font_settings') or {})
            if settings_side == "back":
                _assign_template_json_setting(template, "back_font_settings", merged_font)
            else:
                _assign_template_json_setting(template, "font_settings", merged_font)

         # Save photo settings as before
        if 'photo_settings' in data and isinstance(data.get('photo_settings'), dict):
            incoming_photo = data.get('photo_settings') or {}
            existing_photo = (template.back_photo_settings if settings_side == "back" else template.photo_settings) or {}
            current_photo = dict(existing_photo)
            current_photo['photo_x'] = int(incoming_photo.get('photo_x', current_photo.get('photo_x', 0)))
            current_photo['photo_y'] = int(incoming_photo.get('photo_y', current_photo.get('photo_y', 0)))
            current_photo['photo_width'] = int(incoming_photo.get('photo_width', current_photo.get('photo_width', 100)))
            current_photo['photo_height'] = int(incoming_photo.get('photo_height', current_photo.get('photo_height', 100)))
            current_photo['corel_editable_photo_mode'] = str(
                incoming_photo.get('corel_editable_photo_mode', current_photo.get('corel_editable_photo_mode', 'frame_only'))
            ).strip().lower() or 'frame_only'
            current_photo['photo_shape'] = normalize_photo_shape(
                incoming_photo.get('photo_shape', current_photo.get('photo_shape', 'rectangle'))
            )
            current_photo['photo_shape_inset'] = int(
                incoming_photo.get('photo_shape_inset', current_photo.get('photo_shape_inset', 0)) or 0
            )
            # Save enable_photo visibility flag from visual editor
            if 'enable_photo' in incoming_photo:
                current_photo['enable_photo'] = bool(incoming_photo['enable_photo'])
            # FIX 1: Persist photo frame color and border radii from visual editor
            current_photo['photo_frame_color'] = str(
                incoming_photo.get('photo_frame_color', current_photo.get('photo_frame_color', '#8c2440'))
            ).strip() or '#8c2440'
            for _corner in ['top_left', 'top_right', 'bottom_right', 'bottom_left']:
                _key = f'photo_border_{_corner}'
                current_photo[_key] = int(incoming_photo.get(_key, current_photo.get(_key, 0)))
            if settings_side == "back":
                _assign_template_json_setting(template, "back_photo_settings", current_photo)
            else:
                _assign_template_json_setting(template, "photo_settings", current_photo)
        elif 'photo_x' in data:
            existing_photo = (template.back_photo_settings if settings_side == "back" else template.photo_settings) or {}
            current_photo = dict(existing_photo)
            current_photo['photo_x'] = int(data.get('photo_x', 0))
            current_photo['photo_y'] = int(data.get('photo_y', 0))
            current_photo['photo_width'] = int(data.get('photo_width', 100))
            current_photo['photo_height'] = int(data.get('photo_height', 100))
            if 'corel_editable_photo_mode' in data:
                current_photo['corel_editable_photo_mode'] = str(
                    data.get('corel_editable_photo_mode', 'frame_only')
                ).strip().lower() or 'frame_only'
            if 'photo_shape' in data:
                current_photo['photo_shape'] = normalize_photo_shape(data.get('photo_shape', 'rectangle'))
            if 'photo_shape_inset' in data:
                current_photo['photo_shape_inset'] = int(data.get('photo_shape_inset', 0) or 0)
            if settings_side == "back":
                _assign_template_json_setting(template, "back_photo_settings", current_photo)
            else:
                _assign_template_json_setting(template, "photo_settings", current_photo)

        # Save QR + barcode settings from visual editor
        if 'qr_settings' in data and isinstance(data.get('qr_settings'), dict):
            incoming_qr = data.get('qr_settings') or {}
            existing_qr = (template.back_qr_settings if settings_side == "back" else template.qr_settings) or {}
            current_qr = dict(existing_qr)
            # Merge ALL keys from incoming into stored qr_settings
            # This preserves qr_data_type, qr_base_url etc. while updating positions
            _QR_NUMERIC_KEYS = [
                'qr_x', 'qr_y', 'qr_size',
                'barcode_x', 'barcode_y', 'barcode_width', 'barcode_height',
            ]
            _QR_BOOL_KEYS = ['enable_qr', 'enable_barcode']
            for k in _QR_NUMERIC_KEYS:
                if k in incoming_qr:
                    try:
                        current_qr[k] = int(incoming_qr[k])
                    except (TypeError, ValueError):
                        pass
            for k in _QR_BOOL_KEYS:
                if k in incoming_qr:
                    current_qr[k] = bool(incoming_qr[k])
            # Copy any other non-sensitive string/dict fields
            _PASSTHROUGH_KEYS = [
                'qr_data_type', 'qr_base_url', 'qr_custom_text',
                'qr_color', 'qr_bg_color', 'qr_style',
                'barcode_data_type', 'barcode_base_url', 'barcode_custom_text',
                'barcode_color', 'barcode_bg_color',
            ]
            for k in _PASSTHROUGH_KEYS:
                if k in incoming_qr:
                    current_qr[k] = incoming_qr[k]

            # Map editor/legacy aliases to active QR setting keys
            if 'qr_color' in incoming_qr:
                current_qr['qr_fill_color'] = incoming_qr['qr_color']
            if 'qr_bg_color' in incoming_qr:
                current_qr['qr_back_color'] = incoming_qr['qr_bg_color']
            if 'barcode_color' in incoming_qr:
                current_qr['barcode_fill_color'] = incoming_qr['barcode_color']
            if 'barcode_bg_color' in incoming_qr:
                current_qr['barcode_back_color'] = incoming_qr['barcode_bg_color']

            if settings_side == "back":
                _assign_template_json_setting(template, "back_qr_settings", current_qr)
            else:
                _assign_template_json_setting(template, "qr_settings", current_qr)

        # Save layout_config JSON from editor
        if 'layout_config' in data:
            parsed_layout = parse_layout_config(data['layout_config'])
            if settings_side == "back":
                template.back_layout_config = json.dumps(parsed_layout, ensure_ascii=False) if parsed_layout else None
                _assign_template_json_setting(template, "back_font_settings", derive_font_settings_from_layout_config(
                    parsed_layout,
                    template.back_font_settings or {},
                ))
            else:
                template.layout_config = json.dumps(parsed_layout, ensure_ascii=False) if parsed_layout else None
                _assign_template_json_setting(template, "font_settings", derive_font_settings_from_layout_config(
                    parsed_layout,
                    template.font_settings or {},
                ))

        from datetime import datetime, timezone
        template.updated_at = datetime.now(timezone.utc)

        db.session.commit()
        actor, actor_role = get_session_actor()
        create_template_version_snapshot(template, source="visual_editor_save", actor=actor, actor_role=actor_role)
        log_immutable_audit_event(
            entity_type="template",
            entity_id=template.id,
            action="visual_editor_settings_saved",
            payload={"template_id": template.id, "settings_side": settings_side},
            actor=actor,
            actor_role=actor_role,
        )
        db.session.commit()
        return jsonify(_template_settings_payload(template, settings_side))
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
