"""
Image utilities: template loading, photo processing, QR/barcode generation.

This module owns:
- Template loaders (local + Cloudinary, PDF + image)
- Photo shape masking (rounded, polygon, custom)
- Photo border rendering
- QR code generation with styling
- Code128 barcode generation
- Cloudinary face-crop URL builder
- Transparent edge trimming
- RGB conversion
- Default photo & QR config dicts

Cross-module deps:
- app.utils.helper_utils for STATIC_DIR
"""

import io
import json
import logging
import math
import os
import time
from urllib.parse import urlparse

import fitz
import qrcode
from PIL import Image, ImageDraw, ImageFont
from qrcode.image.pil import PilImage
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import SolidFillColorMask
from qrcode.image.styles.moduledrawers import CircleModuleDrawer, RoundedModuleDrawer, SquareModuleDrawer

from app.utils.helper_utils import STATIC_DIR

logger = logging.getLogger(__name__)


# ================== Template Loaders ==================
def load_template_from_url(url):
    """Load a template image from Cloudinary URL. Handles PDF and image formats."""
    import requests
    try:
        if not url:
            raise ValueError("Template URL is required")
        last_err = None
        response = None
        for attempt in range(3):
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                last_err = None
                break
            except requests.exceptions.RequestException as e:
                last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        if last_err is not None or response is None:
            raise RuntimeError(f"Failed to fetch template from URL: {last_err}")
        logger.info(f"Fetched template from Cloudinary: {url[:50]}...")
        content_type = response.headers.get('Content-Type', '')
        if 'pdf' in content_type.lower() or url.lower().endswith('.pdf') or (response.content.find(b'%PDF') >= 0 and response.content.find(b'%PDF') < 1024):
            pdf_header_pos = response.content.find(b'%PDF')
            pdf_bytes = response.content[pdf_header_pos:] if pdf_header_pos >= 0 else response.content
            if len(pdf_bytes) < 128:
                raise ValueError(f"Downloaded PDF appears truncated ({len(pdf_bytes)} bytes)")
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = pdf_doc[0]
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data)).convert("RGB")
            pdf_doc.close()
            logger.info(f"PDF template loaded from Cloudinary: {img.size}")
            return img
        img = Image.open(io.BytesIO(response.content)).convert("RGB")
        logger.info(f"Image template loaded from Cloudinary: {img.size}")
        return img
    except Exception as e:
        logger.error(f"Error loading template from Cloudinary URL {url}: {e}")
        raise


def load_template_smart(path_or_url):
    """Smart template loader that handles both Cloudinary URLs and local file paths."""
    import requests
    try:
        if not path_or_url:
            raise ValueError("Template path or URL is required")

        if path_or_url.startswith(("http://", "https://")):
            source_url = _normalize_template_source_url(path_or_url)
            last_network_error = None
            response = None
            for attempt in range(3):
                try:
                    response = requests.get(source_url, timeout=12)
                    response.raise_for_status()
                    last_network_error = None
                    break
                except requests.exceptions.RequestException as req_err:
                    status_code = getattr(getattr(req_err, "response", None), "status_code", None)
                    if status_code in (401, 403):
                        raise RuntimeError(
                            "Template URL returned unauthorized/forbidden (HTTP "
                            f"{status_code}). Cloudinary is denying public delivery for this file."
                        )
                    last_network_error = req_err
                    if attempt < 2:
                        time.sleep(0.7 * (attempt + 1))
            if last_network_error is not None or response is None:
                raise RuntimeError(f"Template download failed (network/DNS): {last_network_error}")

            content_type = response.headers.get('Content-Type', '')
            content_length = len(response.content)
            logger.info(
                f"Downloaded file: size={content_length} bytes, content-type={content_type}, "
                f"url={source_url[:80]}"
            )
            is_pdf = (
                'pdf' in content_type.lower()
                or source_url.lower().endswith('.pdf')
                or '/raw/upload/' in source_url
                or (response.content.find(b'%PDF') >= 0 and response.content.find(b'%PDF') < 1024)
            )

            if is_pdf:
                try:
                    pdf_header_pos = response.content.find(b'%PDF')
                    if pdf_header_pos < 0:
                        logger.error(f"File does not appear to be a PDF. First bytes: {response.content[:20]}")
                        raise ValueError("Downloaded file is not a valid PDF (missing PDF signature)")
                    pdf_bytes = response.content[pdf_header_pos:]
                    if len(pdf_bytes) < 128:
                        raise RuntimeError(
                            f"Downloaded PDF is too small ({len(pdf_bytes)} bytes) and appears corrupted. "
                            "Please re-upload the PDF template."
                        )
                    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page = pdf_doc[0]
                    pix = page.get_pixmap(dpi=300)
                    img_data = pix.tobytes("png")
                    img = Image.open(io.BytesIO(img_data)).convert("RGBA")
                    pdf_doc.close()
                    logger.info(f"PDF template loaded successfully from Cloudinary: {img.size}")
                    return img
                except Exception as pdf_error:
                    if isinstance(pdf_error, RuntimeError):
                        raise
                    logger.error(f"PDF loading failed: {pdf_error}. Attempting fallback to image format.")
                    try:
                        img = Image.open(io.BytesIO(response.content))
                        logger.info(f"Loaded as image format successfully: {img.size}")
                        return img.convert("RGBA")
                    except Exception as fallback_error:
                        logger.error(f"Fallback image loading also failed: {fallback_error}")
                        raise RuntimeError(f"Cannot load template - neither PDF nor image: {fallback_error}")
            try:
                img = Image.open(io.BytesIO(response.content))
                logger.info(f"Image template loaded successfully: {img.size}")
                return img.convert("RGBA")
            except Exception as img_error:
                logger.error(f"Image loading failed: {img_error}")
                raise RuntimeError(f"Cannot load template image: {img_error}")

        if not os.path.exists(path_or_url):
            raise FileNotFoundError(f"Template file not found: {path_or_url}")

        local_ext = os.path.splitext(str(path_or_url))[1].lower()
        is_local_pdf = local_ext == ".pdf"
        if not is_local_pdf:
            try:
                with open(path_or_url, "rb") as fh:
                    header = fh.read(1024)
                is_local_pdf = b"%PDF" in header
            except Exception:
                is_local_pdf = False

        if is_local_pdf:
            try:
                pdf_doc = fitz.open(path_or_url)
                if pdf_doc.page_count < 1:
                    pdf_doc.close()
                    raise ValueError("Local PDF template has no pages")
                page = pdf_doc[0]
                pix = page.get_pixmap(dpi=300)
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data)).convert("RGBA")
                pdf_doc.close()
                logger.info(f"Local PDF template loaded successfully: {img.size}")
                return img
            except Exception as local_pdf_err:
                raise RuntimeError(f"Cannot load local PDF template: {local_pdf_err}")

        img = Image.open(path_or_url)
        return img.convert("RGBA")
    except Exception as e:
        logger.exception(f"Template load failed: {path_or_url}")
        raise RuntimeError(f"Error loading template: {e}")


def load_template(path):
    try:
        if not path or not os.path.exists(path):
            raise ValueError(f"Template file not found: {path}")
        ext = os.path.splitext(path)[1].lower()
        logger.info(f"Loading template: {path}, extension: {ext}")
        if ext == ".pdf":
            pdf_doc = fitz.open(path)
            page = pdf_doc[0]
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data)).convert("RGB")
            pdf_doc.close()
            logger.info(f"PDF template loaded: {img.size}")
            return img
        if ext in [".jpg", ".jpeg", ".png"]:
            img = Image.open(path).convert("RGB")
            logger.info(f"Image template loaded: {img.size}")
            return img
        raise ValueError(f"Unsupported template format: {ext}. Use PDF, JPG, or PNG.")
    except Exception as e:
        logger.error(f"Error loading template {path}: {e}")
        logger.info("Creating fallback template")
        fallback = Image.new("RGB", (1000, 600), color="white")
        draw = ImageDraw.Draw(fallback)
        draw.rectangle([50, 50, 950, 550], outline="black", width=2)
        draw.text((100, 100), "Template Load Error", fill="red")
        draw.text((100, 150), f"File: {os.path.basename(path)}", fill="black")
        draw.text((100, 200), "Using fallback template", fill="blue")
        return fallback


# ================== Photo Shape & Masking ==================
_PHOTO_POLYGON_SIDES = {
    "triangle": 3,
    "diamond": 4,
    "pentagon": 5,
    "hexagon": 6,
    "heptagon": 7,
    "octagon": 8,
}


def normalize_photo_shape(value):
    val_str = str(value or "rectangle").strip()
    base_shape = val_str.split(":")[0].lower().replace("_", "-")
    aliases = {
        "rect": "rectangle",
        "square": "rectangle",
        "round": "rounded",
        "rounded-rectangle": "rounded",
        "rounded_rect": "rounded",
        "circle": "circle",
        "ellipse": "circle",
        "oval": "circle",
        "rhombus": "diamond",
    }
    base_shape = aliases.get(base_shape, base_shape)
    if base_shape.startswith("custom-polygon"):
        if ":" not in val_str:
            return "custom-polygon:[[0.5,0],[1,0.25],[1,0.75],[0.5,1],[0,0.75],[0,0.25]]"
        return val_str
    if base_shape in {"rectangle", "rounded", "circle"} or base_shape in _PHOTO_POLYGON_SIDES:
        return val_str
    return "rectangle"


def _photo_border_rgba(border_color):
    if isinstance(border_color, (list, tuple)) and len(border_color) >= 3:
        try:
            return (int(border_color[0]), int(border_color[1]), int(border_color[2]), 255)
        except (ValueError, TypeError):
            return (140, 36, 64, 255)
    color_hex = str(border_color or "").strip()
    if color_hex.startswith("#"):
        color_hex = color_hex[1:]
    if len(color_hex) == 6:
        try:
            return (
                int(color_hex[0:2], 16),
                int(color_hex[2:4], 16),
                int(color_hex[4:6], 16),
                255,
            )
        except ValueError:
            pass
    return (140, 36, 64, 255)


def _regular_polygon_points(width, height, sides, rotation_degrees=-90, inset=0, cap_height=None):
    w = max(1.0, float(width))
    h = max(1.0, float(height))
    cx = w / 2.0
    cy = h / 2.0
    rx = max(1.0, (w / 2.0) - float(inset or 0))
    ry = max(1.0, (h / 2.0) - float(inset or 0))

    if int(sides) == 6 and rotation_degrees in (-90, 270, -90.0, 270.0):
        w_eff = max(1.0, w - 2.0 * float(inset or 0))
        h_eff = max(1.0, h - 2.0 * float(inset or 0))
        cap_h = min(h_eff / 2.0, w_eff * 0.288675135)
        if cap_height is not None:
            try:
                cap_h = max(0.0, min(h_eff / 2.0, float(cap_height)))
            except Exception:
                pass
        return [
            (cx, float(inset or 0)),
            (cx + (w_eff / 2.0), float(inset or 0) + cap_h),
            (cx + (w_eff / 2.0), float(inset or 0) + h_eff - cap_h),
            (cx, float(inset or 0) + h_eff),
            (cx - (w_eff / 2.0), float(inset or 0) + h_eff - cap_h),
            (cx - (w_eff / 2.0), float(inset or 0) + cap_h),
        ]

    points = []
    for idx in range(max(3, int(sides))):
        angle = math.radians(rotation_degrees + (360.0 * idx / max(3, int(sides))))
        points.append((cx + rx * math.cos(angle), cy + ry * math.sin(angle)))
    return points


def _hex_cap_height_from_shape(shape, width, height):
    if ":" not in str(shape or ""):
        return None
    base_shape, raw_cap = str(shape).split(":", 1)
    if base_shape.strip().lower() != "hexagon":
        return None
    try:
        return max(0.0, min(float(height) / 2.0, float(raw_cap)))
    except Exception:
        return None


def _shape_mask(size, shape, inset=0):
    w, h = size
    inset = max(0, int(float(inset or 0)))
    inset = min(inset, max(0, (min(w, h) - 2) // 2))
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    if shape == "circle":
        draw.ellipse((inset, inset, w - 1 - inset, h - 1 - inset), fill=255)
    elif shape == "diamond":
        draw.polygon([
            (w / 2, inset),
            (w - 1 - inset, h / 2),
            (w / 2, h - 1 - inset),
            (inset, h / 2),
        ], fill=255)
    elif shape.startswith("custom-polygon:"):
        try:
            normalized_points = json.loads(shape[len("custom-polygon:"):])
            w_eff = w - 2 * inset
            h_eff = h - 2 * inset
            points = [(inset + px * w_eff, inset + py * h_eff) for px, py in normalized_points]
            draw.polygon(points, fill=255)
        except Exception:
            draw.rectangle((inset, inset, w - 1 - inset, h - 1 - inset), fill=255)
    else:
        base_shape = shape.split(":")[0].lower()
        sides = _PHOTO_POLYGON_SIDES.get(base_shape)
        if sides:
            cap_h = _hex_cap_height_from_shape(shape, w - (2 * inset), h - (2 * inset))
            draw.polygon(_regular_polygon_points(w, h, sides, inset=inset, cap_height=cap_h), fill=255)
        else:
            draw.rectangle((inset, inset, w - 1 - inset, h - 1 - inset), fill=255)
    return mask


def round_photo(image, radii, border_color=None, border_thickness=0, shape=None, polygon_sides=None, shape_inset=0):
    """Apply rounded corners using subtractive masking. Optionally renders a border."""
    image = image.convert("RGBA")
    w, h = image.size
    tl, tr, br, bl = [int(float(r or 0)) for r in radii]
    normalized_shape = normalize_photo_shape(shape)
    shape_inset = max(0, int(float(shape_inset or 0)))
    shape_inset = min(shape_inset, max(0, (min(w, h) - 2) // 2))

    if polygon_sides:
        try:
            sides = max(3, min(12, int(polygon_sides)))
            if sides != _PHOTO_POLYGON_SIDES.get(normalized_shape):
                normalized_shape = f"polygon-{sides}"
                _PHOTO_POLYGON_SIDES[normalized_shape] = sides
        except Exception:
            pass

    if normalized_shape not in {"rectangle", "rounded"}:
        mask = _shape_mask((w, h), normalized_shape, inset=shape_inset)
        image.putalpha(mask)
        t = int(border_thickness or 0)
        if border_color and t > 0:
            try:
                color = _photo_border_rgba(border_color)
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw_ov = ImageDraw.Draw(overlay)
                inset = shape_inset + max(1, t // 2)
                if normalized_shape == "circle":
                    draw_ov.ellipse((inset, inset, w - 1 - inset, h - 1 - inset), outline=color, width=t)
                elif normalized_shape == "diamond":
                    points = [(w / 2, inset), (w - 1 - inset, h / 2), (w / 2, h - 1 - inset), (inset, h / 2)]
                    draw_ov.line(points + [points[0]], fill=color, width=t, joint="curve")
                elif normalized_shape.startswith("custom-polygon:"):
                    try:
                        normalized_points = json.loads(normalized_shape[len("custom-polygon:"):])
                        w_eff = w - 2 * inset
                        h_eff = h - 2 * inset
                        points = [(inset + px * w_eff, inset + py * h_eff) for px, py in normalized_points]
                        draw_ov.line(points + [points[0]], fill=color, width=t, joint="curve")
                    except Exception:
                        pass
                else:
                    base_shape = normalized_shape.split(":")[0].lower()
                    sides = _PHOTO_POLYGON_SIDES.get(base_shape, 6)
                    cap_h = _hex_cap_height_from_shape(normalized_shape, w - (2 * inset), h - (2 * inset))
                    points = _regular_polygon_points(w, h, sides, inset=inset, cap_height=cap_h)
                    draw_ov.line(points + [points[0]], fill=color, width=t, joint="curve")
                image = Image.alpha_composite(image, overlay)
            except Exception as border_err:
                logging.getLogger("legacy_app").warning(f"Error rendering shaped photo border: {border_err}")
        return image

    mask = Image.new('L', (w, h), 255)
    draw = ImageDraw.Draw(mask)
    if tl > 0:
        draw.rectangle([0, 0, tl, tl], fill=0)
        draw.pieslice([0, 0, tl * 2, tl * 2], 180, 270, fill=255)
    if tr > 0:
        draw.rectangle([w - tr, 0, w, tr], fill=0)
        draw.pieslice([w - tr * 2, 0, w, tr * 2], 270, 360, fill=255)
    if br > 0:
        draw.rectangle([w - br, h - br, w, h], fill=0)
        draw.pieslice([w - br * 2, h - br * 2, w, h], 0, 90, fill=255)
    if bl > 0:
        draw.rectangle([0, h - bl, bl, h], fill=0)
        draw.pieslice([0, h - bl * 2, bl * 2, h], 90, 180, fill=255)
    image.putalpha(mask)

    t = int(border_thickness or 0)
    if border_color and t > 0:
        try:
            color = _photo_border_rgba(border_color)
            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw_ov = ImageDraw.Draw(overlay)
            offset = t / 2.0
            draw_ov.line([(int(tl), int(offset)), (int(w - tr), int(offset))], fill=color, width=t)
            draw_ov.line([(int(w - offset), int(tr)), (int(w - offset), int(h - br))], fill=color, width=t)
            draw_ov.line([(int(w - br), int(h - offset)), (int(bl), int(h - offset))], fill=color, width=t)
            draw_ov.line([(int(offset), int(h - bl)), (int(offset), int(tl))], fill=color, width=t)
            if tl > 0 and tl * 2 > t:
                draw_ov.arc([int(offset), int(offset), int(tl * 2 - offset), int(tl * 2 - offset)], 180, 270, fill=color, width=t)
            if tr > 0 and tr * 2 > t:
                draw_ov.arc([int(w - tr * 2 + offset), int(offset), int(w - offset), int(tr * 2 - offset)], 270, 360, fill=color, width=t)
            if br > 0 and br * 2 > t:
                draw_ov.arc([int(w - br * 2 + offset), int(h - br * 2 + offset), int(w - offset), int(h - offset)], 0, 90, fill=color, width=t)
            if bl > 0 and bl * 2 > t:
                draw_ov.arc([int(offset), int(h - bl * 2 + offset), int(bl * 2 - offset), int(h - offset)], 90, 180, fill=color, width=t)
            image = Image.alpha_composite(image, overlay)
        except Exception as border_err:
            logging.getLogger("legacy_app").warning(f"Error rendering photo border: {border_err}")

    return image


# ================== QR Code ==================
def get_default_qr_config():
    """Return default QR configuration."""
    return {
        "enable_qr": False,
        "qr_x": 50,
        "qr_y": 50,
        "qr_size": 120,
        "qr_style": "square",
        "qr_border": 2,
        "qr_fill_color": [0, 0, 0],
        "qr_back_color": [255, 255, 255],
        "qr_data_type": "student_id",
        "qr_custom_text": "",
        "qr_base_url": "https://example.com/verify/",
        "qr_include_logo": False,
        "qr_logo_path": "",
    }


def _parse_rgb_color(color_val):
    """Parse a color value into [r, g, b] integer list (local copy)."""
    if not color_val:
        return [0, 0, 0]
    if isinstance(color_val, str):
        val = color_val.strip()
        if val.startswith("#"):
            hex_color = val.lstrip("#")
            try:
                if len(hex_color) == 6:
                    return [
                        int(hex_color[0:2], 16),
                        int(hex_color[2:4], 16),
                        int(hex_color[4:6], 16),
                    ]
                if len(hex_color) == 3:
                    return [
                        int(hex_color[0] * 2, 16),
                        int(hex_color[1] * 2, 16),
                        int(hex_color[2] * 2, 16),
                    ]
            except Exception:
                return [0, 0, 0]
        try:
            return [int(x.strip()) for x in val.split(",")]
        except Exception:
            return [0, 0, 0]
    if isinstance(color_val, (list, tuple)):
        try:
            return [int(x) for x in color_val[:3]]
        except Exception:
            return [0, 0, 0]
    return [0, 0, 0]


def _normalize_template_source_url(url):
    """Local helper used by load_template_smart."""
    value = str(url or "").strip()
    if not value:
        return value
    try:
        parsed = urlparse(value)
        if parsed.scheme == "http" and parsed.netloc.lower() == "res.cloudinary.com":
            return "https://" + value[len("http://"):]
    except Exception:
        return value
    return value


def generate_qr_code(data, qr_settings, size=120):
    """Generate QR code image using settings."""
    INTERNAL_SIZE = max(size, 300)
    try:
        if not isinstance(qr_settings, dict):
            qr_settings = {}
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=INTERNAL_SIZE // 21,
            border=qr_settings.get("qr_border", 2),
        )
        qr.add_data(data)
        qr.make(fit=True)
        style = qr_settings.get("qr_style", "square")
        if style == "rounded":
            module_drawer = RoundedModuleDrawer()
        elif style == "circle":
            module_drawer = CircleModuleDrawer()
        else:
            module_drawer = SquareModuleDrawer()
        fill_color = tuple(_parse_rgb_color(qr_settings.get("qr_fill_color", qr_settings.get("qr_color", [0, 0, 0]))))
        back_color = tuple(_parse_rgb_color(qr_settings.get("qr_back_color", qr_settings.get("qr_bg_color", [255, 255, 255]))))
        img = qr.make_image(
            image_factory=StyledPilImage,
            module_drawer=module_drawer,
            color_mask=SolidFillColorMask(back_color=back_color, front_color=fill_color),
        )
        if hasattr(img, 'get_image'):
            img = img.get_image()
        else:
            img = img.convert("RGB")
        img = img.resize((INTERNAL_SIZE, INTERNAL_SIZE), Image.LANCZOS)

        include_logo = qr_settings.get("qr_include_logo", False)
        logo_rel_path = qr_settings.get("qr_logo_path", "")
        if include_logo and logo_rel_path:
            logo_path = os.path.join(STATIC_DIR, logo_rel_path)
            if os.path.exists(logo_path):
                logo = Image.open(logo_path).convert("RGBA")
                logo_size = int(round(INTERNAL_SIZE * 0.28))
                logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
                img = img.convert("RGBA")
                pos = ((INTERNAL_SIZE - logo_size) // 2, (INTERNAL_SIZE - logo_size) // 2)
                img.paste(logo, pos, logo)

        img = img.resize((size, size), Image.LANCZOS)
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, back_color)
            bg.paste(img, mask=img.split()[3])
            return bg
        return img.convert("RGB")
    except Exception as e:
        logger.error(f"Error generating QR code: {e}")
        try:
            qr = qrcode.QRCode(version=1, box_size=size // 21, border=4)
            qr.add_data(data)
            qr.make(fit=True)
            fallback_img = qr.make_image(fill_color="black", back_color="white")
            return fallback_img.resize((size, size), Image.LANCZOS).convert("RGB")
        except Exception:
            fallback = Image.new("RGB", (size, size), "white")
            draw = ImageDraw.Draw(fallback)
            draw.text((10, 10), "QR Error", fill="black")
            return fallback


def generate_barcode_code128(data, qr_settings, width=220, height=70):
    """Generate a Code128 barcode as a PIL image."""
    try:
        payload = "" if data is None else str(data)
        width = int(width) if width else 220
        height = int(height) if height else 70
        width = max(40, width)
        height = max(30, height)
        fill = tuple(qr_settings.get("barcode_fill_color", [0, 0, 0])) if isinstance(qr_settings, dict) else (0, 0, 0)
        back = tuple(qr_settings.get("barcode_back_color", [255, 255, 255])) if isinstance(qr_settings, dict) else (255, 255, 255)
        import barcode
        from barcode.writer import ImageWriter
        code128_cls = barcode.get_barcode_class("code128")
        code = code128_cls(payload, writer=ImageWriter())
        out = io.BytesIO()
        code.write(out, options={"write_text": False, "foreground": fill, "background": back})
        out.seek(0)
        img = Image.open(out).convert("RGB")
        img = img.resize((width, height), Image.LANCZOS)
        return img
    except Exception as e:
        logger.error(f"Error generating barcode: {e}")
        try:
            width = int(width) if width else 220
            height = int(height) if height else 70
            width = max(40, width)
            height = max(30, height)
        except Exception:
            width, height = 220, 70
        fallback = Image.new("RGB", (width, height), "white")
        d = ImageDraw.Draw(fallback)
        d.rectangle([0, 0, width - 1, height - 1], outline="black", width=2)
        d.text((10, max(5, (height // 2) - 8)), "BARCODE", fill="black")
        return fallback


# ================== Cloudinary / Misc ==================
def get_cloudinary_face_crop_url(url, w, h):
    """Build a Cloudinary face-crop transformation URL."""
    if not url or not isinstance(url, str):
        return None
    if "/upload/" not in url:
        return None
    try:
        w = int(w)
        h = int(h)
        if w <= 0 or h <= 0:
            return None
    except Exception:
        return None
    prefix, rest = url.split("/upload/", 1)
    transform = f"c_fill,g_face,w_{w},h_{h}"
    return f"{prefix}/upload/{transform}/{rest}"


def trim_transparent_edges(pil_img):
    """Crops transparent borders from an RGBA image."""
    if pil_img.mode != "RGBA":
        return pil_img
    alpha = pil_img.split()[-1]
    bbox = alpha.getbbox()
    if bbox:
        return pil_img.crop(bbox)
    return pil_img


def force_rgb(img, bg_color=(255, 255, 255)):
    """Ensures image is RGB-safe for JPEG export. Handles RGBA / LA / P modes safely."""
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, bg_color)
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


# ================== Default Photo Config ==================
def get_default_photo_config():
    """Return default photo configuration for landscape orientation."""
    return {
        "enable_photo": True,
        "photo_x": 725,
        "photo_y": 200,
        "photo_width": 260,
        "photo_height": 313,
        "photo_border_top_left": 0,
        "photo_border_top_right": 0,
        "photo_border_bottom_right": 0,
        "photo_border_bottom_left": 0,
        "photo_shape": "rectangle",
        "photo_shape_inset": 0,
        "corel_editable_photo_mode": "frame_only",
    }


def get_photo_settings_for_orientation(template_id, photo_settings):
    """Adjust photo settings based on template orientation."""
    from app.utils.layout_utils import get_template_orientation
    orientation = get_template_orientation(template_id)
    default_photo = get_default_photo_config()
    settings = {**default_photo, **photo_settings}
    if orientation == 'portrait':
        if 'photo_x' not in photo_settings:
            settings['photo_x'] = 100
        if 'photo_y' not in photo_settings:
            settings['photo_y'] = 400
        if 'photo_width' not in photo_settings:
            settings['photo_width'] = 216
        if 'photo_height' not in photo_settings:
            settings['photo_height'] = 180
    return settings


__all__ = [
    "force_rgb",
    "get_cloudinary_face_crop_url",
    "get_default_photo_config",
    "get_default_qr_config",
    "get_photo_settings_for_orientation",
    "load_template",
    "load_template_from_url",
    "load_template_smart",
    "generate_barcode_code128",
    "generate_qr_code",
    "round_photo",
    "trim_transparent_edges",
]
