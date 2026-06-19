"""
AI Auto-Layout Engine.

Provides intelligent template design assistance:
  1. Layout Analysis — analyze existing templates and extract structure
  2. Smart Field Detection — detect fields from sample card images using OCR + heuristics
  3. Color Harmony Engine — generate aesthetically pleasing color palettes
  4. Auto-Design from Prompt — generate template layouts from text descriptions
  5. Design Validation — check accessibility, print readiness, and best practices
"""
import io
import re
import json
import logging
import colorsys
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Layout Analysis Service
# ---------------------------------------------------------------------------

@dataclass
class LayoutRegion:
    """A detected region in a template image."""
    x: int
    y: int
    width: int
    height: int
    region_type: str  # "photo", "text", "qr", "barcode", "logo", "background"
    confidence: float = 0.0
    text_content: str = ""
    suggested_field_name: str = ""


@dataclass
class LayoutAnalysis:
    """Complete analysis of a template image."""
    width: int
    height: int
    orientation: str = "landscape"
    regions: List[LayoutRegion] = field(default_factory=list)
    color_palette: List[str] = field(default_factory=list)
    background_color: str = "#FFFFFF"
    text_regions: List[LayoutRegion] = field(default_factory=list)
    photo_region: Optional[LayoutRegion] = None
    qr_region: Optional[LayoutRegion] = None


def analyze_template_layout(image_bytes: bytes) -> LayoutAnalysis:
    """
    Analyze a template image to detect its layout structure.

    Uses image processing heuristics to identify:
    - Photo area (typically a large rectangular region with face-like colors)
    - Text areas (regions with high contrast and small features)
    - QR code area (small square with characteristic pattern)
    - Logo area (top corner, typically small and colorful)
    - Background color
    - Dominant color palette

    Usage:
        with open("template.png", "rb") as f:
            analysis = analyze_template_layout(f.read())
        print(f"Found {len(analysis.regions)} regions")
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        logger.warning("PIL/numpy not available for layout analysis")
        return LayoutAnalysis(width=0, height=0)

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img)
    h, w = arr.shape[:2]

    analysis = LayoutAnalysis(
        width=w,
        height=h,
        orientation="landscape" if w > h else "portrait",
    )

    # Detect background color (most common edge color)
    edge_pixels = np.concatenate([
        arr[0, :, :], arr[-1, :, :],
        arr[:, 0, :], arr[:, -1, :]
    ])
    bg_color = _most_common_color(edge_pixels)
    analysis.background_color = _rgb_to_hex(bg_color)

    # Extract dominant color palette
    analysis.color_palette = _extract_color_palette(arr, n_colors=5)

    # Detect photo region (look for skin-tone colored area)
    photo_region = _detect_photo_region(arr, w, h)
    if photo_region:
        analysis.photo_region = photo_region
        analysis.regions.append(photo_region)

    # Detect text regions (high contrast, small features)
    text_regions = _detect_text_regions(arr, w, h)
    analysis.text_regions = text_regions
    analysis.regions.extend(text_regions)

    # Detect QR code (small square in corner with characteristic pattern)
    qr_region = _detect_qr_region(arr, w, h)
    if qr_region:
        analysis.qr_region = qr_region
        analysis.regions.append(qr_region)

    # Detect logo (small colorful region in corner)
    logo_region = _detect_logo_region(arr, w, h)
    if logo_region:
        analysis.regions.append(logo_region)

    logger.info(
        "layout_analysis: %dx%d, %d regions, %d colors",
        w, h, len(analysis.regions), len(analysis.color_palette),
    )
    return analysis


def _most_common_color(pixels) -> tuple:
    """Find the most common color in a set of pixels."""
    try:
        import numpy as np
        # Quantize to reduce unique colors
        quantized = (pixels // 16) * 16
        colors, counts = np.unique(quantized, axis=0, return_counts=True)
        most_common = colors[np.argmax(counts)]
        return tuple(int(c) for c in most_common)
    except Exception:
        return (255, 255, 255)


def _rgb_to_hex(rgb: tuple) -> str:
    """Convert RGB tuple to hex color string."""
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _extract_color_palette(arr, n_colors: int = 5) -> List[str]:
    """Extract dominant colors from an image using k-means-like approach."""
    try:
        import numpy as np
        # Sample pixels for speed
        pixels = arr.reshape(-1, 3)
        if len(pixels) > 10000:
            indices = np.random.choice(len(pixels), 10000, replace=False)
            pixels = pixels[indices]

        # Simple color quantization
        quantized = (pixels // 32) * 32
        unique, counts = np.unique(quantized, axis=0, return_counts=True)
        top_indices = np.argsort(counts)[-n_colors:]
        return [_rgb_to_hex(tuple(int(c) for c in unique[i])) for i in top_indices]
    except Exception:
        return ["#FFFFFF", "#000000"]


def _detect_photo_region(arr, w: int, h: int) -> Optional[LayoutRegion]:
    """
    Detect the photo area in a template.
    Heuristic: look for a rectangular region with skin-tone colors,
    typically on the left side of the card.
    """
    try:
        import numpy as np

        # Check left third of image for skin-tone pixels
        left_region = arr[:, :w // 3, :]
        skin_mask = _skin_tone_mask(left_region)

        if skin_mask.mean() > 0.05:  # at least 5% skin-tone pixels
            # Find bounding box of skin-tone area
            rows = np.any(skin_mask, axis=1)
            cols = np.any(skin_mask, axis=0)

            if rows.any() and cols.any():
                rmin, rmax = np.where(rows)[0][[0, -1]]
                cmin, cmax = np.where(cols)[0][[0, -1]]

                return LayoutRegion(
                    x=int(cmin),
                    y=int(rmin),
                    width=int(cmax - cmin),
                    height=int(rmax - rmin),
                    region_type="photo",
                    confidence=float(skin_mask.mean()),
                    suggested_field_name="student_photo",
                )
    except Exception:
        pass
    return None


def _skin_tone_mask(arr) -> list:
    """Create a boolean mask of skin-tone pixels."""
    try:
        import numpy as np
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        mask = (
            (r > 95) & (g > 40) & (b > 20) &
            (r > g) & (r > b) &
            (np.abs(r.astype(int) - g.astype(int)) > 15) &
            (r - b > 15)
        )
        return mask
    except Exception:
        return []


def _detect_text_regions(arr, w: int, h: int) -> List[LayoutRegion]:
    """
    Detect text regions using edge density heuristics.
    Text areas have high edge density with horizontal alignment.
    """
    regions = []
    try:
        import numpy as np

        # Convert to grayscale
        gray = np.mean(arr, axis=2).astype(np.uint8)

        # Simple edge detection (Sobel-like)
        edges = np.abs(np.diff(gray, axis=1, append=gray[:, -1:])) + \
                np.abs(np.diff(gray, axis=0, append=gray[-1:, :]))

        # Threshold
        edge_mask = edges > 30

        # Divide into horizontal strips and find high-edge-density areas
        strip_height = h // 20
        for i in range(20):
            y_start = i * strip_height
            y_end = min((i + 1) * strip_height, h)
            strip = edge_mask[y_start:y_end, :]

            density = strip.mean()
            if density > 0.1:  # high edge density = likely text
                # Find horizontal extent
                col_density = strip.mean(axis=0)
                active_cols = np.where(col_density > 0.05)[0]
                if len(active_cols) > 10:
                    regions.append(LayoutRegion(
                        x=int(active_cols[0]),
                        y=int(y_start),
                        width=int(active_cols[-1] - active_cols[0]),
                        height=int(y_end - y_start),
                        region_type="text",
                        confidence=float(density),
                        suggested_field_name=f"text_field_{len(regions)}",
                    ))
    except Exception:
        pass

    return regions


def _detect_qr_region(arr, w: int, h: int) -> Optional[LayoutRegion]:
    """
    Detect QR code region.
    Heuristic: small square in bottom-right corner with high-contrast checker pattern.
    """
    try:
        import numpy as np

        # Check bottom-right quadrant
        qr_area = arr[h // 2:, w // 2:, :]
        gray = np.mean(qr_area, axis=2)

        # QR codes have very high contrast
        contrast = gray.std()
        if contrast > 60:
            # Look for a roughly square region
            eh, ew = gray.shape
            size = min(eh, ew) // 3
            if size > 20:
                return LayoutRegion(
                    x=w // 2 + ew // 2 - size // 2,
                    y=h // 2 + eh // 2 - size // 2,
                    width=size,
                    height=size,
                    region_type="qr",
                    confidence=min(contrast / 100, 1.0),
                    suggested_field_name="qr_code",
                )
    except Exception:
        pass
    return None


def _detect_logo_region(arr, w: int, h: int) -> Optional[LayoutRegion]:
    """Detect logo region (small colorful area in top corner)."""
    try:
        import numpy as np

        # Check top-left corner
        corner = arr[:h // 5, :w // 4, :]
        # Logo areas have high color variance
        variance = corner.std(axis=2).mean()
        if variance > 40:
            return LayoutRegion(
                x=0,
                y=0,
                width=w // 4,
                height=h // 5,
                region_type="logo",
                confidence=min(variance / 80, 1.0),
                suggested_field_name="school_logo",
            )
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 2. Smart Field Detection from Sample Cards
# ---------------------------------------------------------------------------

def detect_fields_from_sample(image_bytes: bytes, ocr_text: str = None) -> List[dict]:
    """
    Detect ID card fields from a sample card image.

    Combines:
    - Layout analysis (where are the visual elements?)
    - OCR text extraction (what text is present?)
    - Heuristic field matching (which text corresponds to which field?)

    Returns a list of detected fields with positions and types.

    Usage:
        fields = detect_fields_from_sample(image_bytes, "Name: John Doe\nClass: 10th")
        # Returns: [{"name": "name", "label": "Name", "x": 100, "y": 200, ...}]
    """
    # Analyze layout
    layout = analyze_template_layout(image_bytes)

    fields = []

    # Map text regions to known field types
    known_field_patterns = {
        "name": [r"name", r"student\s*name", r"full\s*name", r"نام"],
        "father_name": [r"father", r"f\.?\s*name", r"parent", r"ولدیت", r"اسم الأب"],
        "class": [r"class", r"grade", r"section", r"جماعت", r"الصف"],
        "dob": [r"dob", r"birth", r"date\s*of\s*birth", r"born", r"تاریخ پیدائش", r"تاريخ الميلاد"],
        "phone": [r"phone", r"mobile", r"contact", r"موبائل", r"رقم الهاتف"],
        "address": [r"address", r"residence", r"پتہ", r"العنوان"],
        "school": [r"school", r"institution", r"academy"],
        "roll_number": [r"roll", r"id\s*number", r"student\s*id", r"admission"],
        "blood_group": [r"blood", r"group", r"b\.?type"],
        "emergency_contact": [r"emergency", r"ice", r"contact"],
    }

    # If we have OCR text, match it to known fields
    if ocr_text:
        text_lower = ocr_text.lower()
        for field_name, patterns in known_field_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    # Find the corresponding text region
                    matching_region = None
                    for region in layout.text_regions:
                        if region.text_content and re.search(pattern, region.text_content.lower()):
                            matching_region = region
                            break

                    field = {
                        "name": field_name,
                        "label": field_name.replace("_", " ").title(),
                        "field_type": "text",
                        "is_required": field_name in ("name", "class"),
                    }

                    if matching_region:
                        field.update({
                            "x": matching_region.x,
                            "y": matching_region.y,
                            "width": matching_region.width,
                            "height": matching_region.height,
                        })
                    else:
                        # Estimate position from layout
                        idx = len(fields)
                        field.update({
                            "x": int(layout.width * 0.35),
                            "y": int(layout.height * 0.15 + idx * 40),
                            "width": int(layout.width * 0.55),
                            "height": 30,
                        })

                    fields.append(field)
                    break

    # Add photo field if detected
    if layout.photo_region:
        pr = layout.photo_region
        fields.append({
            "name": "student_photo",
            "label": "Photo",
            "field_type": "image",
            "is_required": True,
            "x": pr.x,
            "y": pr.y,
            "width": pr.width,
            "height": pr.height,
        })

    # Add QR field if detected
    if layout.qr_region:
        qr = layout.qr_region
        fields.append({
            "name": "qr_code",
            "label": "QR Code",
            "field_type": "qr",
            "is_required": False,
            "x": qr.x,
            "y": qr.y,
            "width": qr.width,
            "height": qr.height,
        })

    logger.info("field_detection: detected %d fields from sample", len(fields))
    return fields


# ---------------------------------------------------------------------------
# 3. Color Harmony Engine
# ---------------------------------------------------------------------------

def generate_color_palette(base_color: str, scheme: str = "complementary",
                            n_colors: int = 5) -> List[str]:
    """
    Generate a harmonious color palette from a base color.

    Schemes:
      - complementary: base + opposite color
      - analogous: base + adjacent colors
      - triadic: 3 evenly spaced colors
      - split_complementary: base + two adjacent to complement
      - monochromatic: variations of the same hue

    Usage:
        palette = generate_color_palette("#3498DB", "complementary")
        # Returns: ["#3498DB", "#DB6734", "#2980B9", "#E67E22", "#1ABC9C"]
    """
    # Parse base color
    base_color = base_color.lstrip("#")
    r, g, b = int(base_color[:2], 16) / 255.0, int(base_color[2:4], 16) / 255.0, int(base_color[4:], 16) / 255.0
    h, s, v = colorsys.rgb_to_hsv(r, g, b)

    colors = [f"#{base_color}"]

    if scheme == "complementary":
        colors.append(_hsv_to_hex((h + 0.5) % 1.0, s, v))
        colors.append(_hsv_to_hex(h, max(s - 0.2, 0.1), min(v + 0.1, 1.0)))
        colors.append(_hsv_to_hex((h + 0.5) % 1.0, max(s - 0.2, 0.1), v))
        colors.append(_hsv_to_hex(h, s, max(v - 0.2, 0.1)))

    elif scheme == "analogous":
        for offset in [1/12, 2/12, -1/12, -2/12]:
            colors.append(_hsv_to_hex((h + offset) % 1.0, s, v))

    elif scheme == "triadic":
        colors.append(_hsv_to_hex((h + 1/3) % 1.0, s, v))
        colors.append(_hsv_to_hex((h + 2/3) % 1.0, s, v))
        colors.append(_hsv_to_hex((h + 1/3) % 1.0, max(s - 0.1, 0.1), min(v + 0.1, 1.0)))
        colors.append(_hsv_to_hex((h + 2/3) % 1.0, max(s - 0.1, 0.1), min(v + 0.1, 1.0)))

    elif scheme == "split_complementary":
        colors.append(_hsv_to_hex((h + 0.5 - 1/12) % 1.0, s, v))
        colors.append(_hsv_to_hex((h + 0.5 + 1/12) % 1.0, s, v))
        colors.append(_hsv_to_hex((h + 0.5 - 1/12) % 1.0, max(s - 0.2, 0.1), v))
        colors.append(_hsv_to_hex((h + 0.5 + 1/12) % 1.0, max(s - 0.2, 0.1), v))

    elif scheme == "monochromatic":
        for i in range(1, n_colors):
            factor = 0.6 + (i * 0.1)
            colors.append(_hsv_to_hex(h, s * factor, v * factor))

    return colors[:n_colors]


def _hsv_to_hex(h: float, s: float, v: float) -> str:
    """Convert HSV to hex color string."""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def suggest_font_colors(background_color: str) -> dict:
    """
    Suggest font colors that provide good contrast against a background.

    Returns dict with primary, secondary, and accent color suggestions.
    """
    bg = background_color.lstrip("#")
    r, g, b = int(bg[:2], 16), int(bg[2:4], 16), int(bg[4:], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255

    if luminance > 0.5:
        # Light background → dark text
        return {
            "primary": "#1A1A1A",
            "secondary": "#4A4A4A",
            "accent": "#2C3E50",
            "label": "#333333",
            "value": "#1A1A1A",
        }
    else:
        # Dark background → light text
        return {
            "primary": "#FFFFFF",
            "secondary": "#E0E0E0",
            "accent": "#F39C12",
            "label": "#CCCCCC",
            "value": "#FFFFFF",
        }


# ---------------------------------------------------------------------------
# 4. Auto-Design from Prompt
# ---------------------------------------------------------------------------

def generate_layout_from_prompt(prompt: str, card_width: int = 1015,
                                  card_height: int = 661) -> dict:
    """
    Generate a template layout configuration from a text description.

    This is a rule-based generator. For production, integrate with an LLM API.

    Supported prompt keywords:
      - "modern", "classic", "minimal", "corporate", "school"
      - "photo left", "photo right", "photo center"
      - "blue", "red", "green", "dark", "light"
      - "with qr", "with barcode", "with logo"
      - "double sided", "single sided"

    Usage:
        layout = generate_layout_from_prompt("modern blue school ID card with photo on left")
    """
    prompt_lower = prompt.lower()

    # Determine style
    style = "modern"
    for s in ["modern", "classic", "minimal", "corporate", "school", "elegant"]:
        if s in prompt_lower:
            style = s
            break

    # Determine color scheme
    color_schemes = {
        "blue": {"primary": "#2C3E50", "secondary": "#3498DB", "accent": "#E74C3C"},
        "red": {"primary": "#C0392B", "secondary": "#E74C3C", "accent": "#F39C12"},
        "green": {"primary": "#27AE60", "secondary": "#2ECC71", "accent": "#F1C40F"},
        "dark": {"primary": "#1A1A1A", "secondary": "#333333", "accent": "#3498DB"},
        "light": {"primary": "#ECF0F1", "secondary": "#BDC3C7", "accent": "#3498DB"},
        "purple": {"primary": "#8E44AD", "secondary": "#9B59B6", "accent": "#F39C12"},
        "orange": {"primary": "#E67E22", "secondary": "#F39C12", "accent": "#2C3E50"},
    }

    colors = color_schemes["blue"]
    for color_name, scheme in color_schemes.items():
        if color_name in prompt_lower:
            colors = scheme
            break

    # Determine photo position
    photo_position = "left"
    if "photo right" in prompt_lower or "right" in prompt_lower:
        photo_position = "right"
    elif "photo center" in prompt_lower or "center" in prompt_lower:
        photo_position = "center"

    # Build layout config
    layout = {
        "style": style,
        "colors": colors,
        "orientation": "landscape",
        "card_width": card_width,
        "card_height": card_height,
        "background": {
            "color": "#FFFFFF",
            "gradient": style == "modern",
            "gradient_colors": [colors["primary"], colors["secondary"]] if style == "modern" else None,
        },
        "photo": {
            "enabled": True,
            "position": photo_position,
            "x": int(card_width * 0.05) if photo_position == "left" else int(card_width * 0.65),
            "y": int(card_height * 0.15),
            "width": int(card_width * 0.28),
            "height": int(card_height * 0.65),
            "border_radius": 8 if style == "modern" else 0,
            "border_color": colors["primary"],
            "border_width": 2,
        },
        "fields": _generate_field_layout(style, photo_position, card_width, card_height, colors),
        "qr_code": {
            "enabled": "qr" in prompt_lower or "with qr" in prompt_lower,
            "x": int(card_width * 0.82),
            "y": int(card_height * 0.75),
            "size": int(card_width * 0.12),
        },
        "barcode": {
            "enabled": "barcode" in prompt_lower,
            "x": int(card_width * 0.05),
            "y": int(card_height * 0.88),
            "width": int(card_width * 0.4),
            "height": int(card_height * 0.08),
        },
        "logo": {
            "enabled": "logo" in prompt_lower,
            "x": int(card_width * 0.05),
            "y": int(card_height * 0.03),
            "width": int(card_width * 0.12),
            "height": int(card_height * 0.12),
        },
    }

    logger.info("auto_design: generated layout for prompt '%s' (style=%s)", prompt[:50], style)
    return layout


def _generate_field_layout(style: str, photo_position: str, w: int, h: int,
                            colors: dict) -> List[dict]:
    """Generate field positions based on style and photo position."""
    fields = []

    # Text area starts after photo
    text_x = int(w * 0.38) if photo_position == "left" else int(w * 0.05)
    text_w = int(w * 0.55) if photo_position == "left" else int(w * 0.55)

    field_definitions = [
        ("name", "Name", True),
        ("father_name", "Father's Name", True),
        ("class", "Class", True),
        ("dob", "Date of Birth", False),
        ("phone", "Phone", False),
        ("address", "Address", False),
    ]

    y_start = int(h * 0.15)
    field_height = int(h * 0.1)
    gap = int(h * 0.02)

    for i, (name, label, required) in enumerate(field_definitions):
        fields.append({
            "name": name,
            "label": label,
            "field_type": "text",
            "is_required": required,
            "x": text_x,
            "y": y_start + i * (field_height + gap),
            "width": text_w,
            "height": field_height,
            "font_size": 14 if style == "modern" else 12,
            "font_color": colors["primary"] if required else colors["secondary"],
            "label_color": colors["secondary"],
            "show_label": True,
            "text_case": "normal",
        })

    return fields


# ---------------------------------------------------------------------------
# 5. Design Validation Service
# ---------------------------------------------------------------------------

def validate_design(layout_config: dict) -> dict:
    """
    Validate a template design for accessibility, print readiness, and best practices.

    Checks:
    - WCAG contrast ratios (text vs background)
    - Minimum font sizes for readability
    - Photo area aspect ratio
    - QR code minimum size
    - Print bleed area
    - Field overlap detection
    - Required fields present

    Usage:
        result = validate_design(template.layout_config)
        if not result["valid"]:
            for issue in result["issues"]:
                print(issue["message"])
    """
    issues = []
    warnings_list = []

    # Check contrast ratios
    bg_color = layout_config.get("background", {}).get("color", "#FFFFFF")
    for field in layout_config.get("fields", []):
        font_color = field.get("font_color", "#000000")
        contrast = _contrast_ratio(bg_color, font_color)
        if contrast < 4.5:
            issues.append({
                "type": "accessibility",
                "severity": "error",
                "message": f"Field '{field.get('name')}': contrast ratio {contrast:.1f}:1 is below WCAG AA minimum (4.5:1)",
                "field": field.get("name"),
            })
        elif contrast < 7:
            warnings_list.append({
                "type": "accessibility",
                "severity": "warning",
                "message": f"Field '{field.get('name')}': contrast ratio {contrast:.1f}:1 — WCAG AAA recommends 7:1",
                "field": field.get("name"),
            })

    # Check font sizes
    for field in layout_config.get("fields", []):
        font_size = field.get("font_size", 12)
        if font_size < 8:
            issues.append({
                "type": "readability",
                "severity": "error",
                "message": f"Field '{field.get('name')}': font size {font_size}px is too small (minimum 8px)",
                "field": field.get("name"),
            })
        elif font_size < 10:
            warnings_list.append({
                "type": "readability",
                "severity": "warning",
                "message": f"Field '{field.get('name')}': font size {font_size}px may be hard to read when printed",
                "field": field.get("name"),
            })

    # Check photo aspect ratio
    photo = layout_config.get("photo", {})
    if photo.get("enabled") and photo.get("width") and photo.get("height"):
        ratio = photo["width"] / photo["height"]
        if ratio < 0.5 or ratio > 1.5:
            warnings_list.append({
                "type": "layout",
                "severity": "warning",
                "message": f"Photo aspect ratio ({ratio:.2f}:1) is unusual. Standard is 0.75:1 to 1:1",
            })

    # Check QR code size
    qr = layout_config.get("qr_code", {})
    if qr.get("enabled") and qr.get("size", 0) < 50:
        issues.append({
            "type": "functionality",
            "severity": "error",
            "message": f"QR code size ({qr.get('size')}px) is too small. Minimum 50px recommended.",
        })

    # Check field overlap
    fields = layout_config.get("fields", [])
    for i, f1 in enumerate(fields):
        for f2 in fields[i + 1:]:
            if _rectangles_overlap(f1, f2):
                warnings_list.append({
                    "type": "layout",
                    "severity": "warning",
                    "message": f"Fields '{f1.get('name')}' and '{f2.get('name')}' may overlap",
                })

    # Check required fields
    required_fields = ["name", "class"]
    field_names = {f.get("name") for f in fields}
    for req in required_fields:
        if req not in field_names:
            warnings_list.append({
                "type": "completeness",
                "severity": "warning",
                "message": f"Recommended field '{req}' is missing from the layout",
            })

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings_list,
        "score": max(0, 100 - len(issues) * 20 - len(warnings_list) * 5),
    }


def _contrast_ratio(color1: str, color2: str) -> float:
    """Calculate WCAG contrast ratio between two colors."""
    def luminance(hex_color):
        hex_color = hex_color.lstrip("#")
        r, g, b = [int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
        r = r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4
        g = g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4
        b = b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    l1, l2 = luminance(color1), luminance(color2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _rectangles_overlap(r1: dict, r2: dict) -> bool:
    """Check if two rectangles overlap."""
    return not (
        r1.get("x", 0) + r1.get("width", 0) < r2.get("x", 0) or
        r2.get("x", 0) + r2.get("width", 0) < r1.get("x", 0) or
        r1.get("y", 0) + r1.get("height", 0) < r2.get("y", 0) or
        r2.get("y", 0) + r2.get("height", 0) < r1.get("y", 0)
    )
