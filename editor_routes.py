from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, send_file, make_response, current_app
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


def _layout_attr_for_side(side):
    return "back_layout_config" if side == "back" else "layout_config"


def _photo_attr_for_side(side):
    return "back_photo_settings" if side == "back" else "photo_settings"


def _qr_attr_for_side(side):
    return "back_qr_settings" if side == "back" else "qr_settings"


def _get_template_layout_payload(template, side):
    side_name = _normalize_editor_side(template, side)
    layout_raw = getattr(template, _layout_attr_for_side(side_name), None)
    return {
        "template_id": template.id,
        "side": side_name,
        "layout_config": parse_layout_config(layout_raw),
        "photo_settings": getattr(template, _photo_attr_for_side(side_name), None) or {},
        "qr_settings": getattr(template, _qr_attr_for_side(side_name), None) or {},
    }


def _save_template_layout_payload(template, side, data):
    side_name = _normalize_editor_side(template, side)
    layout_attr = _layout_attr_for_side(side_name)
    photo_attr = _photo_attr_for_side(side_name)
    qr_attr = _qr_attr_for_side(side_name)

    parsed_layout = parse_layout_config((data or {}).get("layout_config"))
    setattr(template, layout_attr, json.dumps(parsed_layout, ensure_ascii=False) if parsed_layout else None)

    if isinstance((data or {}).get("photo_settings"), dict):
        current_photo = dict(getattr(template, photo_attr, None) or {})
        incoming_photo = data.get("photo_settings") or {}
        for key in ("photo_x", "photo_y", "photo_width", "photo_height"):
            if key in incoming_photo:
                current_photo[key] = int(incoming_photo.get(key))
        if "enable_photo" in incoming_photo:
            current_photo["enable_photo"] = bool(incoming_photo.get("enable_photo"))
        setattr(template, photo_attr, current_photo)

    if isinstance((data or {}).get("qr_settings"), dict):
        current_qr = dict(getattr(template, qr_attr, None) or {})
        incoming_qr = data.get("qr_settings") or {}
        for key in ("qr_x", "qr_y", "qr_size"):
            if key in incoming_qr:
                current_qr[key] = int(incoming_qr.get(key))
        if "enable_qr" in incoming_qr:
            current_qr["enable_qr"] = bool(incoming_qr.get("enable_qr"))
        setattr(template, qr_attr, current_qr)

    db.session.commit()
    return _get_template_layout_payload(template, side_name)



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
    layout_config = parse_layout_config(template.back_layout_config if side == "back" else template.layout_config)
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
        editor_layout_config=layout_config or {},
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
# 3. Unified Layout API
# =========================================================
@editor_bp.route('/admin/template_layout/<int:template_id>', methods=["GET", "POST"])
def template_layout_api(template_id):
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403

    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({"error": "Template not found"}), 404

    request_payload = request.get_json(silent=True) or {}
    side = _normalize_editor_side(template, request.args.get("side") or request_payload.get("settings_side"))

    if request.method == "GET":
        return jsonify({"success": True, **_get_template_layout_payload(template, side)})

    try:
        payload = _save_template_layout_payload(template, side, request_payload)
        return jsonify({"success": True, **payload})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@editor_bp.route('/admin/get_editor_fields/<int:template_id>')
def get_editor_fields(template_id):
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403

    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({"error": "Template not found"}), 404

    side = _normalize_editor_side(template, request.args.get("side"))
    payload = _get_template_layout_payload(template, side)
    fields = payload.get("layout_config", {}).get("fields", {})
    output = []
    for field_key, field_obj in fields.items():
        if not isinstance(field_obj, dict):
            continue
        output.append({
            "key": field_key,
            "x": int(field_obj.get("x", 0) or 0),
            "y": int(field_obj.get("y", 0) or 0),
            "size": int(field_obj.get("font_size", 0) or 0),
            "color": field_obj.get("color"),
            "visible": bool(field_obj.get("visible", True)),
        })
    return jsonify(output)

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

    try:
        payload = _save_template_layout_payload(
            template,
            data.get("settings_side"),
            data,
        )
        return jsonify({'success': True, **payload})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
