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
    return f.read(), None


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
    template_id = payload.get("template_id", type=int)

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

    template_id = payload.get("template_id", type=int)

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
