from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, send_file, make_response, current_app
from models import db, Template, TemplateField, FieldSetting
from models import db, Template, TemplateField
from utils import (
    get_template_path,
    load_template_smart,
    get_template_settings,
    get_template_language_direction,
    parse_layout_config,
)
import json
import io
import time
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



# =========================================================
# 1. Main Editor Page Route
# =========================================================
@editor_bp.route("/admin/template_editor/<int:template_id>")
def template_editor(template_id):
    if not session.get("admin"): return redirect(url_for("login"))
    
    template = db.session.get(Template, template_id)
    if not template: return "Template not found", 404
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
def get_template_image(template_id):
    """
    Serves the template file (PDF or Image) as a high-quality JPEG 
    for the visual editor canvas.
    """
    template = db.session.get(Template, template_id)
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
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
    except Exception as e:
        print(f"Editor Image Error: {e}")
        return "Error processing image", 500

# =========================================================
# 3. API: Get Individual Field Settings
# =========================================================
@editor_bp.route('/admin/get_editor_fields/<int:template_id>')
def get_editor_fields(template_id):
    """
    Returns JSON list of all text fields and their positions.
    If no settings exist, it returns a smart default set.
    """
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 403

    # 1. Get saved positions from DB
    settings = FieldSetting.query.filter_by(template_id=template_id).all()
    template = db.session.get(Template, template_id)
    if not template: return jsonify({"error": "Template not found"}), 404
    
    # 2. If no settings exist, return DEFAULTS based on standard ID card layout
    if not settings:
        default_fields = [
            {'key': 'name', 'label': 'Name', 'x': 50, 'y': 100, 'size': 30, 'color': '#000000', 'bold': True},
            {'key': 'father_name', 'label': 'Father Name', 'x': 50, 'y': 150, 'size': 30, 'color': '#000000', 'bold': False},
            {'key': 'class', 'label': 'Class', 'x': 50, 'y': 200, 'size': 30, 'color': '#000000', 'bold': False},
            {'key': 'dob', 'label': 'D.O.B', 'x': 50, 'y': 250, 'size': 30, 'color': '#000000', 'bold': False},
            {'key': 'phone', 'label': 'Phone', 'x': 50, 'y': 300, 'size': 30, 'color': '#000000', 'bold': False},
            {'key': 'address', 'label': 'Address', 'x': 50, 'y': 350, 'size': 25, 'color': '#000000', 'bold': False},
        ]
        return jsonify(default_fields)
    side = request.args.get("side", "front").strip().lower()
    if side == "back" and getattr(template, "is_double_sided", False):
        layout_config_raw = template.back_layout_config
    else:
        layout_config_raw = template.layout_config

    # 3. Serialize stored settings
    output = []
    for s in settings:
        output.append({
            'key': s.field_key,
            # Generate a nice label from key if custom label is missing (e.g., 'father_name' -> 'Father Name')
            'label': s.custom_label or s.field_key.replace('_', ' ').title(),
            'x': s.x_pos,
            'y': s.y_pos,
            'size': s.font_size,
            'color': s.color,
            'bold': s.is_bold,
            'visible': s.is_visible
        })
        
    return jsonify(output)
    parsed_layout = parse_layout_config(layout_config_raw)
    return jsonify(parsed_layout)

# =========================================================
# 4. API: Save All Settings (Global + Fields)
# =========================================================
@editor_bp.route("/admin/save_field_settings", methods=["POST"])
def save_field_settings():
    """
    Saves BOTH the global template settings (Photo/QR position) 
    AND the individual text field positions.
    """
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 403
    
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

    settings_side = str(data.get("settings_side") or "front").strip().lower()
    if settings_side not in {"front", "back"}:
        settings_side = "front"

    try:
        # Save photo settings as before
        if 'photo_settings' in data and isinstance(data.get('photo_settings'), dict):
            incoming_photo = data.get('photo_settings') or {}
            current_photo = (template.back_photo_settings if settings_side == "back" else template.photo_settings) or {}
            current_photo['photo_x'] = int(incoming_photo.get('photo_x', current_photo.get('photo_x', 0)))
            current_photo['photo_y'] = int(incoming_photo.get('photo_y', current_photo.get('photo_y', 0)))
            current_photo['photo_width'] = int(incoming_photo.get('photo_width', current_photo.get('photo_width', 100)))
            current_photo['photo_height'] = int(incoming_photo.get('photo_height', current_photo.get('photo_height', 100)))
            current_photo['corel_editable_photo_mode'] = str(
                incoming_photo.get('corel_editable_photo_mode', current_photo.get('corel_editable_photo_mode', 'frame_only'))
            ).strip().lower() or 'frame_only'
            if settings_side == "back":
                template.back_photo_settings = current_photo
            else:
                template.photo_settings = current_photo
        elif 'photo_x' in data:
            current_photo = (template.back_photo_settings if settings_side == "back" else template.photo_settings) or {}
            current_photo['photo_x'] = int(data.get('photo_x', 0))
            current_photo['photo_y'] = int(data.get('photo_y', 0))
            current_photo['photo_width'] = int(data.get('photo_width', 100))
            current_photo['photo_height'] = int(data.get('photo_height', 100))
            if 'corel_editable_photo_mode' in data:
                current_photo['corel_editable_photo_mode'] = str(
                    data.get('corel_editable_photo_mode', 'frame_only')
                ).strip().lower() or 'frame_only'
            if settings_side == "back":
                template.back_photo_settings = current_photo
            else:
                template.photo_settings = current_photo

        # Save layout_config JSON from editor
        if 'layout_config' in data:
            from utils import parse_layout_config
            parsed_layout = parse_layout_config(data['layout_config'])
            if settings_side == "back":
                template.back_layout_config = json.dumps(parsed_layout, ensure_ascii=False) if parsed_layout else None
            else:
                template.layout_config = json.dumps(parsed_layout, ensure_ascii=False) if parsed_layout else None

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
