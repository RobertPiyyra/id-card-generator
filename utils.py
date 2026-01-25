import os
import logging
from PIL import Image, ImageFont, ImageDraw
import fitz # PyMuPDF
import io
from models import db, Template
import hashlib
import qrcode
from qrcode.image.pil import PilImage
from qrcode.image.styles.moduledrawers import SquareModuleDrawer, RoundedModuleDrawer, CircleModuleDrawer
import arabic_reshaper
from bidi.algorithm import get_display

logger = logging.getLogger(__name__)

# ================== Paths ==================
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_ROOT, "static")
UPLOAD_FOLDER = os.path.join(STATIC_DIR, "Uploads")
GENERATED_FOLDER = os.path.join(STATIC_DIR, "generated")
FONTS_FOLDER = os.path.join(STATIC_DIR, "fonts")
TEMPLATES_CONFIG = os.path.join(APP_ROOT, "templates_config.json")
FONT_CONFIG_PATH = os.path.join(APP_ROOT, "font_config.json")
PHOTO_CONFIG_PATH = os.path.join(APP_ROOT, "photo_config.json")
DUPLICATE_CONFIG_PATH = os.path.join(APP_ROOT, "duplicate_config.json")
PLACEHOLDER_PATH = os.path.join(STATIC_DIR, "placeholder.jpg")
DEFAULT_FONTS = ["arial.ttf", "arialbd.ttf"]

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(GENERATED_FOLDER, exist_ok=True)
os.makedirs(FONTS_FOLDER, exist_ok=True)

# Create placeholder.jpg if not exists
if not os.path.exists(PLACEHOLDER_PATH):
    placeholder = Image.new("RGB", (100, 100), color="gray")
    placeholder.save(PLACEHOLDER_PATH)

# ================== Print Sizes ==================
DPI = 300
ID_WIDTH_PX = int((86 / 25.4) * DPI) # 86mm width for landscape
ID_HEIGHT_PX = int((56 / 25.4) * DPI) # 56mm height for landscape
PORTRAIT_WIDTH_PX = ID_HEIGHT_PX # 56mm width for portrait
PORTRAIT_HEIGHT_PX = ID_WIDTH_PX # 86mm height for portrait
A4_WIDTH_PX = int((210 / 25.4) * DPI) # A4 portrait width
A4_HEIGHT_PX = int((297 / 25.4) * DPI) # A4 portrait height
A4_LANDSCAPE_WIDTH_PX = A4_HEIGHT_PX
A4_LANDSCAPE_HEIGHT_PX = A4_WIDTH_PX


def get_template_path(template_id):
    """
    Get the template Cloudinary URL for the given template ID.
    Prefers template_url (Cloudinary), falls back to local filename for legacy templates.
    
    Args:
        template_id (int): Template ID
    
    Returns:
        str: Cloudinary URL or local file path (legacy)
    """
    try:
        template = db.session.get(Template, template_id)

        if template:
            # Use Cloudinary URL if available
            if template.template_url:
                return template.template_url
            # Fallback to legacy local filename
            if template.filename:
                return os.path.join(STATIC_DIR, template.filename)
        
        logger.warning(f"No template found or no URL/filename for template ID {template_id}")
        return None
    except Exception as e:
        logger.error(f"Error fetching template path for ID {template_id}: {e}")
        return None

def get_template_settings(template_id):
    try:
        template = db.session.get(Template, template_id)


        if template:
            font_settings = get_default_font_config()
            photo_settings = get_default_photo_config()
            qr_settings = get_default_qr_config()
            orientation = template.card_orientation or 'landscape'
            
            if template.font_settings:
                loaded_font = template.font_settings.copy()
                
                # Handle legacy font_color field for backward compatibility
                if 'font_color' in loaded_font:
                    # Copy font_color to both label and value colors if they don't exist
                    if 'label_font_color' not in loaded_font:
                        loaded_font['label_font_color'] = loaded_font['font_color']
                    if 'value_font_color' not in loaded_font:
                        loaded_font['value_font_color'] = loaded_font['font_color']
                
                # Merge with defaults
                font_settings.update(loaded_font)
                
                # Ensure color values are properly formatted
                # Convert string colors to lists if needed
                if isinstance(font_settings.get('label_font_color'), str):
                    label_color = font_settings['label_font_color']
                    if label_color.startswith('#'):
                        # Convert hex to RGB list
                        hex_color = label_color.lstrip('#')
                        font_settings['label_font_color'] = [
                            int(hex_color[0:2], 16),
                            int(hex_color[2:4], 16),
                            int(hex_color[4:6], 16)
                        ]
                    else:
                        # Convert comma-separated string to list
                        font_settings['label_font_color'] = [
                            int(x.strip()) for x in label_color.split(',')
                        ]
                
                if isinstance(font_settings.get('value_font_color'), str):
                    value_color = font_settings['value_font_color']
                    if value_color.startswith('#'):
                        # Convert hex to RGB list
                        hex_color = value_color.lstrip('#')
                        font_settings['value_font_color'] = [
                            int(hex_color[0:2], 16),
                            int(hex_color[2:4], 16),
                            int(hex_color[4:6], 16)
                        ]
                    else:
                        # Convert comma-separated string to list
                        font_settings['value_font_color'] = [
                            int(x.strip()) for x in value_color.split(',')
                        ]
            
            if template.photo_settings:
                photo_settings.update(template.photo_settings)
            
            if template.qr_settings:
                qr_settings.update(template.qr_settings)
                
                # Ensure colors are lists
                if isinstance(qr_settings.get('qr_fill_color'), str):
                    fill_color = qr_settings['qr_fill_color']
                    if fill_color.startswith('#'):
                        hex_color = fill_color.lstrip('#')
                        qr_settings['qr_fill_color'] = [
                            int(hex_color[0:2], 16),
                            int(hex_color[2:4], 16),
                            int(hex_color[4:6], 16)
                        ]
                    else:
                        qr_settings['qr_fill_color'] = [
                            int(x.strip()) for x in fill_color.split(',')
                        ]
                
                if isinstance(qr_settings.get('qr_back_color'), str):
                    back_color = qr_settings['qr_back_color']
                    if back_color.startswith('#'):
                        hex_color = back_color.lstrip('#')
                        qr_settings['qr_back_color'] = [
                            int(hex_color[0:2], 16),
                            int(hex_color[2:4], 16),
                            int(hex_color[4:6], 16)
                        ]
                    else:
                        qr_settings['qr_back_color'] = [
                            int(x.strip()) for x in back_color.split(',')
                        ]
            
            # Log the settings for debugging
            logger.info(f"Template {template_id} settings loaded - Label Color: {font_settings.get('label_font_color')}, Value Color: {font_settings.get('value_font_color')}")
            
            return font_settings, photo_settings, qr_settings, orientation
        return get_default_font_config(), get_default_photo_config(), get_default_qr_config(), 'landscape'
    except Exception as e:
        logger.error(f"Error fetching template settings for ID {template_id}: {e}")
        return get_default_font_config(), get_default_photo_config(), get_default_qr_config(), 'landscape'

# ================== Font Config ==================
def is_valid_font_file(filepath):
    if not os.path.exists(filepath) or os.path.basename(filepath).startswith('._'):
        return False
    try:
        ImageFont.truetype(filepath, size=10)
        return True
    except OSError:
        return False

def get_available_fonts():
    fonts = [f for f in os.listdir(FONTS_FOLDER) if f.lower().endswith((".ttf", ".otf"))]
    valid_fonts = [f for f in fonts if is_valid_font_file(os.path.join(FONTS_FOLDER, f))]
    return valid_fonts or DEFAULT_FONTS


# ================== Orientation Helper Functions ==================
def get_template_orientation(template_id):
    try:
        template = db.session.get(Template, template_id)


        if template:
            orientation = template.card_orientation if template.card_orientation else 'landscape'
            logger.debug(f"Template {template_id} orientation: {orientation}")
            return orientation
        return 'landscape'
    except Exception as e:
        logger.error(f"Error fetching orientation for template {template_id}: {e}")
        return 'landscape' # Fallback to landscape if database error

def get_card_size(template_id=None):
    """
    Get dynamic card size from the database.
    Checks for custom dimensions first.
    If missing, falls back to standard defaults based on Orientation.
    """
    # Standard CR80 Defaults (300 DPI)
    LANDSCAPE_W, LANDSCAPE_H = 1015, 661
    PORTRAIT_W, PORTRAIT_H = 661, 1015

    if template_id:
        try:
            # Local import to avoid circular dependencies
            from models import Template, db
            
            template = db.session.get(Template, template_id)
            
            if template:
                # 1. Try to use Custom Dimensions from DB
                if template.card_width and template.card_height:
                    return template.card_width, template.card_height
                
                # 2. If Custom Dimensions are missing/zero, check Orientation
                # This fixes the issue for older templates or fresh uploads
                if template.card_orientation == 'portrait':
                    return PORTRAIT_W, PORTRAIT_H
                else:
                    return LANDSCAPE_W, LANDSCAPE_H
                
        except Exception as e:
            # Check if logger is available, otherwise print
            try:
                logger.error(f"Error fetching template dimensions: {e}")
            except:
                print(f"Error fetching template dimensions: {e}")
    
    # 3. Ultimate Fallback (Standard Landscape)
    return LANDSCAPE_W, LANDSCAPE_H

def get_sheet_size(orientation):
    if orientation == 'landscape':
        return A4_WIDTH_PX, A4_HEIGHT_PX # Portrait sheet for landscape cards
    else:
        return A4_LANDSCAPE_WIDTH_PX, A4_LANDSCAPE_HEIGHT_PX # Landscape sheet for portrait cards

def get_default_font_config():
    """Return default font configuration for landscape orientation"""
    return {
        "font_bold": "arialbd.ttf",
        "font_regular": "arial.ttf",
        "font_color": [0, 0, 0], # Legacy field for backward compatibility
        "label_font_color": [0, 0, 0], # Black for labels
        "value_font_color": [0, 0, 0], # Black for values
        "label_font_size": 40,
        "value_font_size": 36,
        "label_x": 50,
        "value_x": 280,
        "start_y": 275,
        "line_height": 50,
        "text_case": "normal"
    }

def get_default_photo_config():
    """Return default photo configuration for landscape orientation"""
    return {
        "photo_x": 725,
        "photo_y": 200,
        "photo_width": 260,
        "photo_height": 313,
        "photo_border_top_left": 0,
        "photo_border_top_right": 0,
        "photo_border_bottom_right": 0,
        "photo_border_bottom_left": 0,
        "remove_background": False,       # ← ADD THIS LINE
        "bg_remove_color": "#ffffff"      # ← ADD THIS LINE
    }

def get_photo_settings_for_orientation(template_id, photo_settings):
    """Adjust photo settings based on template orientation"""
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

def get_font_settings_for_orientation(template_id, font_settings):
    """Adjust font settings based on template orientation"""
    orientation = get_template_orientation(template_id)
    default_font = get_default_font_config()
    settings = {**default_font, **font_settings}
  
    if orientation == 'portrait':
        if 'label_font_size' not in font_settings:
            settings['label_font_size'] = 32
        if 'value_font_size' not in font_settings:
            settings['value_font_size'] = 28
        if 'label_x' not in font_settings:
            settings['label_x'] = 40
        if 'value_x' not in font_settings:
            settings['value_x'] = 200
        if 'start_y' not in font_settings:
            settings['start_y'] = 120
        if 'line_height' not in font_settings:
            settings['line_height'] = 45
  
    return settings


# ================== Text Case Helper Function ==================
def apply_text_case(text, case_type):
    """Apply text case transformation based on case_type"""
    if not text:
        return text
  
    if case_type == "uppercase":
        return text.upper()
    elif case_type == "lowercase":
        return text.lower()
    elif case_type == "capitalize":
        return text.title()
    else: # normal
        return text

# ================== QR Code Helper Functions ==================
def get_default_qr_config():
    """Return default QR configuration"""
    return {
        "enable_qr": False,
        "qr_x": 50,
        "qr_y": 50,
        "qr_size": 120,
        "qr_style": "square",
        "qr_border": 2,
        "qr_fill_color": [0, 0, 0], # Black
        "qr_back_color": [255, 255, 255], # White
        "qr_data_type": "student_id",
        "qr_custom_text": "",
        "qr_base_url": "https://example.com/verify/",
        "qr_include_logo": False,
        "qr_logo_path": ""
    }

# ================== Helpers ==================
def load_font_dynamic(font_path, text, max_width, start_size):
    """
    Load a font and dynamically adjust its size to fit within max_width
    """
    try:
        # Check if font file exists
        if not os.path.exists(font_path):
            logger.warning(f"Font file not found: {font_path}, using fallback")
            font_path = os.path.join(FONTS_FOLDER, "arial.ttf")
            if not os.path.exists(font_path):
                # Ultimate fallback to default font
                return ImageFont.load_default()
      
        # Start with the requested size
        size = start_size
        font = ImageFont.truetype(font_path, size)
      
        # For very small sizes, don't scale down too much
        min_size = max(8, start_size - 10)
      
        # Reduce font size until text fits within max_width
        while font.getlength(text) > max_width and size > min_size:
            size -= 1
            font = ImageFont.truetype(font_path, size)
          
        return font
      
    except OSError as e:
        logger.warning(f"Error loading font {font_path}: {e}, using fallback")
        try:
            # Try arial font as fallback
            default_font_path = os.path.join(FONTS_FOLDER, "arial.ttf")
            if os.path.exists(default_font_path):
                size = start_size
                font = ImageFont.truetype(default_font_path, size)
                min_size = max(8, start_size - 10)
                while font.getlength(text) > max_width and size > min_size:
                    size -= 1
                return ImageFont.truetype(default_font_path, size)
            else:
                # Ultimate fallback
                return ImageFont.load_default()
        except Exception as fallback_error:
            logger.error(f"Fallback font also failed: {fallback_error}")
            return ImageFont.load_default()
    except Exception as e:
        logger.error(f"Unexpected error in load_font_dynamic: {e}")
        return ImageFont.load_default()


def load_template_from_url(url):
    """
    Load a template image from Cloudinary URL.
    Handles PDF and image formats.
    
    Args:
        url (str): Cloudinary secure URL
    
    Returns:
        PIL.Image: Template image in RGB mode
    
    Raises:
        Exception: If URL is invalid or fetch fails
    """
    try:
        import requests
        
        if not url:
            raise ValueError("Template URL is required")
        
        # Fetch image from Cloudinary
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        logger.info(f"Fetched template from Cloudinary: {url[:50]}...")
        
        # Determine format from URL or content
        content_type = response.headers.get('Content-Type', '')
        
        if 'pdf' in content_type.lower() or url.lower().endswith('.pdf'):
            # Handle PDF
            import fitz  # PyMuPDF
            pdf_doc = fitz.open(stream=response.content, filetype="pdf")
            page = pdf_doc[0]
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data)).convert("RGB")
            pdf_doc.close()
            logger.info(f"PDF template loaded from Cloudinary: {img.size}")
            return img
        else:
            # Handle image (JPG, PNG, etc.)
            img = Image.open(io.BytesIO(response.content)).convert("RGB")
            logger.info(f"Image template loaded from Cloudinary: {img.size}")
            return img
    
    except Exception as e:
        logger.error(f"Error loading template from Cloudinary URL {url}: {e}")
        raise
        raise

      
def load_template_smart(path_or_url):
    """
    Smart template loader that handles both Cloudinary URLs and local file paths.

    Args:
        path_or_url (str): Cloudinary URL (http/https) or local filesystem path

    Returns:
        PIL.Image.Image: Template image in RGBA mode
    """
    try:
        if not path_or_url:
            raise ValueError("Template path or URL is required")

        # --------------------------------------------------
        # CLOUDINARY / REMOTE TEMPLATE
        # --------------------------------------------------
        if path_or_url.startswith(("http://", "https://")):
            import requests
            import io
            from PIL import Image

            response = requests.get(path_or_url, timeout=10)
            response.raise_for_status()

            img = Image.open(io.BytesIO(response.content))
            return img.convert("RGBA")

        # --------------------------------------------------
        # LOCAL TEMPLATE (LEGACY SUPPORT)
        # --------------------------------------------------
        import os
        from PIL import Image

        if not os.path.exists(path_or_url):
            raise FileNotFoundError(f"Template file not found: {path_or_url}")

        img = Image.open(path_or_url)
        return img.convert("RGBA")

    except Exception as e:
        logger.exception(f"❌ Template load failed: {path_or_url}")
        raise RuntimeError(f"Error loading template: {e}")

      
def load_template(path):
    try:
        if not path or not os.path.exists(path):
            raise ValueError(f"Template file not found: {path}")
          
        ext = os.path.splitext(path)[1].lower()
        logger.info(f"Loading template: {path}, extension: {ext}")
      
        if ext == ".pdf":
            import fitz  # PyMuPDF
            pdf_doc = fitz.open(path)
            page = pdf_doc[0]
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data)).convert("RGB")
            pdf_doc.close()
            logger.info(f"PDF template loaded: {img.size}")
            return img
        elif ext in [".jpg", ".jpeg", ".png"]:
            img = Image.open(path).convert("RGB")
            logger.info(f"Image template loaded: {img.size}")
            return img
        else:
            raise ValueError(f"Unsupported template format: {ext}. Use PDF, JPG, or PNG.")
    except Exception as e:
        logger.error(f"Error loading template {path}: {e}")
        # Create a fallback template
        logger.info("Creating fallback template")
        fallback = Image.new("RGB", (1000, 600), color="white")
        draw = ImageDraw.Draw(fallback)
        draw.rectangle([50, 50, 950, 550], outline="black", width=2)
        draw.text((100, 100), "Template Load Error", fill="red")
        draw.text((100, 150), f"File: {os.path.basename(path)}", fill="black")
        draw.text((100, 200), "Using fallback template", fill="blue")
        return fallback

def round_photo(image, radii):
    """
    Apply rounded corners using subtractive masking.
    Starts with an opaque mask and 'cuts out' only the specific corners needed.
    This prevents opposite corners from interfering with each other.
    """
    image = image.convert("RGBA")
    w, h = image.size
    tl, tr, br, bl = radii

    # 1. Start with a completely Opaque (White) mask
    # This means "Show the whole image" by default
    mask = Image.new('L', (w, h), 255)
    draw = ImageDraw.Draw(mask)

    # 2. For each corner, if a radius is set:
    #    A. Draw a BLACK rectangle at the corner (Make it transparent)
    #    B. Draw a WHITE pie slice over it (Make the rounded part opaque again)
    #    This leaves exactly the "corner tip" transparent.

    # --- Top-Left ---
    if tl > 0:
        draw.rectangle([0, 0, tl, tl], fill=0)
        draw.pieslice([0, 0, tl * 2, tl * 2], 180, 270, fill=255)

    # --- Top-Right ---
    if tr > 0:
        draw.rectangle([w - tr, 0, w, tr], fill=0)
        draw.pieslice([w - tr * 2, 0, w, tr * 2], 270, 360, fill=255)

    # --- Bottom-Right ---
    if br > 0:
        draw.rectangle([w - br, h - br, w, h], fill=0)
        draw.pieslice([w - br * 2, h - br * 2, w, h], 0, 90, fill=255)

    # --- Bottom-Left ---
    if bl > 0:
        draw.rectangle([0, h - bl, bl, h], fill=0)
        draw.pieslice([0, h - bl * 2, bl * 2, h], 90, 180, fill=255)

    # Apply the new mask to the image
    image.putalpha(mask)
    return image

def generate_qr_code(data, qr_settings, size=120):
    """
    Generate QR code image using settings
    """
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=size // 21, # Approximate for standard QR
            border=qr_settings.get("qr_border", 2),
        )
        qr.add_data(data)
        qr.make(fit=True)
       
        # Get style
        style = qr_settings.get("qr_style", "square")
        if style == "rounded":
            module_drawer = RoundedModuleDrawer()
        elif style == "circle":
            module_drawer = CircleModuleDrawer()
        elif style == "gapped":
            module_drawer = SquareModuleDrawer()  # Fallback
        else:
            module_drawer = SquareModuleDrawer()
       
        # Colors
        fill_color = tuple(qr_settings.get("qr_fill_color", [0, 0, 0]))
        back_color = tuple(qr_settings.get("qr_back_color", [255, 255, 255]))
       
        # Generate image
        img = qr.make_image(
            image_factory=PilImage,
            module_drawer=module_drawer,
            color_mask=back_color,  # Background
            color_dark=fill_color    # Foreground
        )
       
        # Add logo if enabled
        if qr_settings.get("qr_include_logo", False) and qr_settings.get("qr_logo_path"):
            logo_path = os.path.join(STATIC_DIR, qr_settings["qr_logo_path"])
            if os.path.exists(logo_path):
                logo = Image.open(logo_path).convert("RGBA")
                # Scale logo to 1/5 of QR size
                logo_size = size // 5
                logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
                # Center logo
                pos = ((img.size[0] - logo_size) // 2, (img.size[1] - logo_size) // 2)
                # Paste with transparency
                img.paste(logo, pos, logo)
       
        return img
    except Exception as e:
        logger.error(f"Error generating QR code: {e}")
        # Fallback to simple QR
        try:
            qr = qrcode.QRCode(version=1, box_size=size // 21, border=4)
            qr.add_data(data)
            qr.make(fit=True)
            return qr.make_image(fill_color="black", back_color="white")
        except:
            # Ultimate fallback
            fallback = Image.new("RGB", (size, size), "white")
            draw = ImageDraw.Draw(fallback)
            draw.text((10, 10), "QR Error", fill="black")
            return fallback


def generate_data_hash(form_data, photo_identifier=None):
    """
    Generate a deterministic hash for the student data.
    Accepts either a photo filename or a photo URL as `photo_identifier` for backward compatibility.
    """
    data_string = f"{form_data.get('name','')}{form_data.get('father_name','')}{form_data.get('class_name','')}{form_data.get('dob','')}{form_data.get('address','')}{form_data.get('phone','')}"
    if photo_identifier:
        data_string += str(photo_identifier)
    return hashlib.md5(data_string.encode()).hexdigest()

from bidi.algorithm import get_display
import arabic_reshaper

def process_text_for_drawing(text, lang, base_dir='R'):
    """
    Properly shape and display RTL text (Urdu/Arabic).
    For Urdu/Arabic: Reshapes letters + Reorders for RTL.
    For English/Hindi: Returns original text (LTR).
    
    CRITICAL: base_dir='R' forces the Bidi algorithm to treat the text as 
    Right-to-Left, which fixes the "Mirroring" issue in PIL.
    """
    if not text:
        return ""
    
    text = str(text) # Ensure string
    
    if lang in ['urdu', 'arabic']:
        try:
            # 1. Reshape: Connects the letters (Ligatures)
            reshaped_text = arabic_reshaper.reshape(text)
            
            # 2. Reorder: Fixes direction for display (BiDi)
            # base_dir='R' is usually correct for pure Urdu labels.
            # If the text is mixed (English inside Urdu), allow the caller to specify 'L'.
            display_text = get_display(reshaped_text, base_dir=base_dir)
            
            return display_text
        except Exception as e:
            print(f"Error reshaping text: {e}")
            return text
            
    # For Hindi, English, etc.
    return text

def trim_transparent_edges(pil_img):
    """
    Crops transparent borders from an RGBA image.
    """
    if pil_img.mode != "RGBA":
        return pil_img

    alpha = pil_img.split()[-1]
    bbox = alpha.getbbox()
    if bbox:
        return pil_img.crop(bbox)
    return pil_img

def ensure_rgb(img):
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img
