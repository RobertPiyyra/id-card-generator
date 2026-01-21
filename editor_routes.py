from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, send_file
from models import db, Template, FieldSetting
from utils import get_template_path, get_template_settings, load_template
import json
import os
import io
from PIL import Image

editor_bp = Blueprint('editor', __name__)

# =========================================================
# 1. Main Editor Page Route
# =========================================================
@editor_bp.route("/admin/template_editor/<int:template_id>")
def template_editor(template_id):
    if not session.get("admin"): return redirect(url_for("login"))
    
    template = db.session.get(Template, template_id)
    if not template: return "Template not found", 404
    
    # URL for the template background image (served via our helper route)
    image_url = url_for('editor.get_template_image', template_id=template.id)
    
    return render_template(
        'visual_editor.html', 
        template=template,
        template_id=template_id,
        image_url=image_url
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
    if not session.get("admin"): return "Unauthorized", 403
    
    template_path = get_template_path(template_id)
    if not template_path or not os.path.exists(template_path):
        return "File not found", 404

    try:
        # 1. Load using robust utility (handles PDF conversion automatically)
        img = load_template(template_path)
        
        # 2. Convert to RGB to ensure compatibility (drops Alpha channel)
        if img.mode != 'RGB':
            img = img.convert('RGB')
            
        # 3. Save to memory buffer as JPEG
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=95)
        buffer.seek(0)
        
        return send_file(buffer, mimetype='image/jpeg')
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
    
    # 1. Fetch Template
    template = db.session.get(Template, template_id)
    if not template: return jsonify({"error": "Template not found"}), 404

    try:
        # --- A. Save Photo Settings ---
        # We merge with existing settings to avoid losing border/background preferences
        if 'photo_x' in data:
            current_photo = template.photo_settings or {}
            # Update position/size from visual editor
            current_photo['photo_x'] = int(data.get('photo_x', 0))
            current_photo['photo_y'] = int(data.get('photo_y', 0))
            current_photo['photo_width'] = int(data.get('photo_width', 100))
            current_photo['photo_height'] = int(data.get('photo_height', 100))
            template.photo_settings = current_photo

        # --- B. Save Individual Field Settings ---
        fields_data = data.get('fields', [])
        if fields_data:
            # Clear old field positions for this template (clean slate strategy)
            FieldSetting.query.filter_by(template_id=template_id).delete()
            
            # Add new positions
            for f in fields_data:
                new_setting = FieldSetting(
                    template_id=template_id,
                    field_key=f['key'],
                    x_pos=int(f['x']),
                    y_pos=int(f['y']),
                    font_size=int(f['size']),
                    color=f['color'],
                    is_bold=f.get('bold', False),
                    is_visible=f.get('visible', True),
                    custom_label=f.get('label') # Optional: save custom label if edited
                )
                db.session.add(new_setting)

        db.session.commit()
        return jsonify({"success": True})
        
    except Exception as e:
        db.session.rollback()
        print(f"Save Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500