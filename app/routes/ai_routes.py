"""
AI Layout API routes.

Endpoints:
    POST /api/ai/analyze-layout      — Upload template image, get region analysis
    POST /api/ai/design-from-prompt  — Generate layout from text prompt
    POST /api/ai/color-palette       — Generate color palette from base color
    POST /api/ai/validate-design     — Validate a layout config for WCAG compliance
    POST /api/ai/field-detection     — Detect fields from a sample card image
    GET  /api/ai/status              — Check AI service availability
"""
import io
import json
import logging

from flask import Blueprint, request, jsonify

from app.legacy_app import admin_required, super_admin_required

logger = logging.getLogger(__name__)

ai_bp = Blueprint("ai", __name__, url_prefix="/api/ai")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_error(message, status=400):
    return jsonify({"success": False, "error": message}), status


def _image_from_request(field_name="image"):
    """Extract image bytes from multipart request."""
    f = request.files.get(field_name)
    if not f or not f.filename:
        return None, "No image file provided."
    # Read with size limit (10 MB)
    max_size = 10 * 1024 * 1024
    data = f.read(max_size + 1)
    if len(data) > max_size:
        return None, f"File too large. Maximum size is 10 MB."
    if len(data) == 0:
        return None, "Empty file provided."
    return data, None


# ---------------------------------------------------------------------------
# 1. Layout Analysis
# ---------------------------------------------------------------------------

@ai_bp.route("/analyze-layout", methods=["POST"])
@admin_required
def analyze_layout():
    """Upload a template image and return detected regions, colors, suggestions."""
    image_bytes, err = _image_from_request("image")
    if err:
        return _json_error(err)

    side = request.form.get("side", "front")
    try:
        from app.services.ai_layout import analyze_template_layout

        analysis = analyze_template_layout(image_bytes)
        return jsonify({
            "success": True,
            "regions": {
                "photo": {
                    "x": analysis.photo_region.x,
                    "y": analysis.photo_region.y,
                    "width": analysis.photo_region.width,
                    "height": analysis.photo_region.height,
                    "confidence": analysis.photo_region.confidence,
                } if analysis.photo_region else None,
                "text": [
                    {
                        "x": r.x, "y": r.y,
                        "width": r.width, "height": r.height,
                        "confidence": r.confidence,
                        "suggested_field_name": r.suggested_field_name,
                    }
                    for r in (analysis.text_regions or [])
                ],
                "qr": {
                    "x": analysis.qr_region.x,
                    "y": analysis.qr_region.y,
                    "width": analysis.qr_region.width,
                    "height": analysis.qr_region.height,
                } if analysis.qr_region else None,
                "logo": {
                    "x": analysis.logo_region.x,
                    "y": analysis.logo_region.y,
                    "width": analysis.logo_region.width,
                    "height": analysis.logo_region.height,
                } if analysis.logo_region else None,
            },
            "color_palette": analysis.dominant_colors,
            "suggested_card_width": analysis.suggested_card_width,
            "suggested_card_height": analysis.suggested_card_height,
            "background_color": analysis.background_color,
            "side": side,
        })
    except Exception as exc:
        logger.exception("AI layout analysis failed")
        return _json_error(f"Analysis failed: {exc}", 500)


# ---------------------------------------------------------------------------
# 2. Design from Prompt
# ---------------------------------------------------------------------------

@ai_bp.route("/design-from-prompt", methods=["POST"])
@admin_required
def design_from_prompt():
    """Generate a card layout from a natural-language prompt."""
    payload = request.get_json(silent=True) or request.form
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        return _json_error("Prompt is required.")

    card_width = int(payload.get("card_width", 1015))
    card_height = int(payload.get("card_height", 661))
    template_id = payload.get("template_id")

    try:
        from app.services.ai_layout import generate_layout_from_prompt

        layout = generate_layout_from_prompt(
            prompt, card_width=card_width, card_height=card_height,
        )
        return jsonify({
            "success": True,
            "layout": layout,
            "prompt": prompt,
            "card_width": card_width,
            "card_height": card_height,
        })
    except Exception as exc:
        logger.exception("AI design-from-prompt failed")
        return _json_error(f"Design generation failed: {exc}", 500)


# ---------------------------------------------------------------------------
# 3. Color Palette Generation
# ---------------------------------------------------------------------------

@ai_bp.route("/color-palette", methods=["POST"])
@admin_required
def color_palette():
    """Generate a harmonious color palette from a base color."""
    payload = request.get_json(silent=True) or request.form
    base_color = str(payload.get("base_color", "")).strip()
    if not base_color:
        return _json_error("Base color is required.")

    scheme = str(payload.get("scheme", "complementary")).strip()
    n_colors = int(payload.get("n_colors", 5))
    background_color = payload.get("background_color", "").strip() or None

    try:
        from app.services.ai_layout import (
            generate_color_palette,
            suggest_font_colors,
        )

        colors = generate_color_palette(base_color, scheme=scheme, n_colors=n_colors)
        font_suggestions = {}
        if background_color:
            font_suggestions = suggest_font_colors(background_color)

        return jsonify({
            "success": True,
            "colors": colors,
            "scheme": scheme,
            "base_color": base_color,
            "font_suggestions": font_suggestions,
        })
    except Exception as exc:
        logger.exception("AI color palette failed")
        return _json_error(f"Color generation failed: {exc}", 500)


# ---------------------------------------------------------------------------
# 4. Design Validation
# ---------------------------------------------------------------------------

@ai_bp.route("/validate-design", methods=["POST"])
@admin_required
def validate_design():
    """Validate a layout configuration for WCAG compliance, overlap, spacing."""
    payload = request.get_json(silent=True) or request.form
    layout_json = payload.get("layout_config") or payload.get("layout")
    if isinstance(layout_json, str):
        try:
            layout_config = json.loads(layout_json)
        except json.JSONDecodeError:
            return _json_error("Invalid JSON in layout_config.")
    elif isinstance(layout_json, dict):
        layout_config = layout_json
    else:
        return _json_error("layout_config (JSON object or string) is required.")

    template_id = payload.get("template_id")

    try:
        from app.services.ai_layout import validate_design

        result = validate_design(layout_config)
        return jsonify({
            "success": True,
            "validation": result,
        })
    except Exception as exc:
        logger.exception("AI design validation failed")
        return _json_error(f"Validation failed: {exc}", 500)


# ---------------------------------------------------------------------------
# 5. Field Detection from Sample Card
# ---------------------------------------------------------------------------

@ai_bp.route("/field-detection", methods=["POST"])
@admin_required
def field_detection():
    """Detect data fields from a sample ID card image."""
    image_bytes, err = _image_from_request("image")
    if err:
        return _json_error(err)

    ocr_text = request.form.get("ocr_text", "") or None

    try:
        from app.services.ai_layout import detect_fields_from_sample

        fields = detect_fields_from_sample(image_bytes, ocr_text=ocr_text)
        return jsonify({
            "success": True,
            "fields": fields,
        })
    except Exception as exc:
        logger.exception("AI field detection failed")
        return _json_error(f"Field detection failed: {exc}", 500)


# ---------------------------------------------------------------------------
# 6. Service Status
# ---------------------------------------------------------------------------

@ai_bp.route("/status", methods=["GET"])
@admin_required
def ai_status():
    """Return availability of AI services."""
    status = {
        "layout_analysis": False,
        "design_generation": False,
        "color_engine": False,
        "field_detection": False,
        "design_validation": False,
    }
    try:
        from app.services.ai_layout import (
            analyze_template_layout,
            generate_layout_from_prompt,
            generate_color_palette,
            detect_fields_from_sample,
            validate_design,
        )
        status = {
            "layout_analysis": True,
            "design_generation": True,
            "color_engine": True,
            "field_detection": True,
            "design_validation": True,
        }
    except ImportError:
        logger.warning("ai_layout service not available")

    return jsonify({"success": True, "services": status})

# ---------------------------------------------------------------------------
# 7. Create Template from AI Layout
# ---------------------------------------------------------------------------

@ai_bp.route("/create-template-from-layout", methods=["POST"])
@admin_required
def create_template_from_layout():
    """Create a new template from an AI-generated layout configuration."""
    payload = request.get_json(silent=True) or request.form
    layout = payload.get("layout")
    if not layout or not isinstance(layout, dict):
        return _json_error("Layout data is required.")

    school_name = str(payload.get("school_name", "AI Generated Template")).strip()
    if not school_name:
        school_name = "AI Generated Template"

    card_width = int(layout.get("card_width", 1015))
    card_height = int(layout.get("card_height", 661))
    orientation = str(layout.get("orientation", "landscape")).strip()

    try:
        from app.legacy_app import add_template, get_default_font_config, get_default_photo_config, get_default_qr_config
        from models import Template, db
        from datetime import datetime, timezone
        import time
        from werkzeug.utils import secure_filename
        from PIL import Image, ImageDraw
        import io
        from app.services.template_upload_service import store_template_upload_bytes

        # Build font_settings from AI layout colors
        colors = layout.get("colors", {})
        font_settings = get_default_font_config()
        if colors.get("primary"):
            font_settings["label_font_color"] = _hex_to_rgb_array(colors["primary"])
        if colors.get("secondary"):
            font_settings["value_font_color"] = _hex_to_rgb_array(colors["secondary"])
        if colors.get("accent"):
            font_settings["colon_font_color"] = _hex_to_rgb_array(colors["accent"])

        # Use first field's font_size if available
        fields = layout.get("fields", [])
        if fields and fields[0].get("font_size"):
            font_settings["label_font_size"] = fields[0]["font_size"]
            font_settings["value_font_size"] = fields[0]["font_size"]

        # Build photo_settings from AI layout
        photo_settings = get_default_photo_config()
        photo = layout.get("photo", {})
        if photo.get("enabled"):
            photo_settings["photo_x"] = photo.get("x", 50)
            photo_settings["photo_y"] = photo.get("y", 99)
            photo_settings["photo_width"] = photo.get("width", 284)
            photo_settings["photo_height"] = photo.get("height", 429)

        # Build qr_settings from AI layout
        qr_settings = get_default_qr_config()
        qr_code = layout.get("qr_code", {})
        if qr_code.get("enabled"):
            qr_settings["enable_qr"] = True
            qr_settings["qr_x"] = qr_code.get("x", 832)
            qr_settings["qr_y"] = qr_code.get("y", 495)
            qr_settings["qr_size"] = qr_code.get("size", 120)

        barcode = layout.get("barcode", {})
        if barcode.get("enabled"):
            qr_settings["enable_barcode"] = True

        # Build layout_config from AI fields (converted to standard nested dictionary format)
        layout_config = None
        if fields:
            key_mapping = {
                "name": "NAME",
                "father_name": "F_NAME",
                "class": "CLASS",
                "dob": "DOB",
                "phone": "MOBILE",
                "address": "ADDRESS"
            }
            nested_fields = {}
            for f in fields:
                orig_name = f.get("name", "").lower()
                system_key = key_mapping.get(orig_name, orig_name.upper())
                nested_fields[system_key] = {
                    "label": {
                        "x": f.get("x", 50),
                        "y": f.get("y", 100),
                        "visible": True,
                        "font_size": f.get("font_size", 14),
                        "grow": "left"
                    },
                    "value": {
                        "x": f.get("x", 50) + int(f.get("width", 200) * 0.4),
                        "y": f.get("y", 100),
                        "visible": True,
                        "font_size": f.get("font_size", 14),
                        "grow": "left"
                    }
                }
            layout_config = json.dumps({"fields": nested_fields}, ensure_ascii=False)

        # Determine dimensions from orientation
        if orientation == "portrait":
            width, height = 661, 1015
            rows, cols = 2, 5
        else:
            width, height = 1015, 661
            rows, cols = 5, 2

        # Override with explicit dimensions if provided
        if card_width:
            width = card_width
        if card_height:
            height = card_height

        # Sanitize school_name to create safe file name
        safe_school_name = secure_filename(school_name) or "ai_template"
        filename = f"ai_{safe_school_name[:40]}_{int(time.time())}.png"

        # Generate base template image
        background_obj = layout.get("background") or {}
        bg_color = None
        if isinstance(background_obj, dict):
            bg_color = background_obj.get("color")
        if not bg_color:
            bg_color = layout.get("background_color") or "#FFFFFF"

        try:
            img = Image.new("RGB", (width, height), bg_color)
        except Exception:
            img = Image.new("RGB", (width, height), "#FFFFFF")

        # Render background gradient if enabled
        if isinstance(background_obj, dict) and background_obj.get("gradient") and background_obj.get("gradient_colors"):
            grad_colors = background_obj.get("gradient_colors")
            if len(grad_colors) >= 2:
                try:
                    c1 = _hex_to_rgb_array(grad_colors[0])
                    c2 = _hex_to_rgb_array(grad_colors[1])
                    draw = ImageDraw.Draw(img)
                    for y in range(height):
                        ratio = y / max(1, height - 1)
                        r = int(c1[0] * (1 - ratio) + c2[0] * ratio)
                        g = int(c1[1] * (1 - ratio) + c2[1] * ratio)
                        b = int(c1[2] * (1 - ratio) + c2[2] * ratio)
                        draw.line([(0, y), (width, y)], fill=(r, g, b))
                except Exception as e:
                    logger.warning("Failed to render background gradient: %s", e)

        # Save generated background image using standard helper (supports Local/Cloudinary)
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        stored_filename = filename
        template_url = None
        try:
            asset = store_template_upload_bytes(img_bytes.getvalue(), filename, side_label="front")
            stored_filename = asset.get("filename") or filename
            template_url = asset.get("template_url")
        except Exception as e:
            logger.warning("Failed to save AI template background image: %s", e)

        template = Template(
            filename=stored_filename,
            template_url=template_url,
            school_name=school_name,
            font_settings=font_settings,
            photo_settings=photo_settings,
            qr_settings=qr_settings,
            back_font_settings=get_default_font_config(),
            back_photo_settings=get_default_photo_config(),
            back_qr_settings=get_default_qr_config(),
            card_orientation=orientation,
            language="english",
            text_direction="ltr",
            card_width=width,
            card_height=height,
            sheet_width=2480,
            sheet_height=3508,
            grid_rows=rows,
            grid_cols=cols,
            layout_config=layout_config,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(template)
        db.session.commit()

        logger.info("AI template created: id=%d name=%s path=%s", template.id, school_name, stored_filename)

        return jsonify({
            "success": True,
            "template_id": template.id,
            "school_name": school_name,
            "card_width": width,
            "card_height": height,
            "orientation": orientation,
            "message": f"Template '{school_name}' created successfully!",
        })
    except Exception as exc:
        db.session.rollback()
        logger.exception("AI create-template failed")
        return _json_error(f"Template creation failed: {exc}", 500)


def _hex_to_rgb_array(hex_color):
    """Convert hex color string to RGB array."""
    try:
        h = str(hex_color).lstrip("#")
        return [int(h[i:i+2], 16) for i in (0, 2, 4)]
    except Exception:
        return [0, 0, 0]
