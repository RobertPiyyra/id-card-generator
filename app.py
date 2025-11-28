from flask import Flask, render_template, request, url_for, Response, redirect, session, send_file, jsonify, flash, Blueprint
from PIL import Image, ImageDraw, ImageFont
import os
import json
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import sqlite3
import csv
import fitz
import io
import hashlib
import logging
import re
import smtplib
from email.mime.text import MIMEText
import random
import string
from collections import defaultdict
import textwrap
import pandas as pd
import tempfile

# ================== App Config ==================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "changeme_in_production")
logger = logging.getLogger(__name__)

# ================== Email Config ==================
EMAIL_FROM = os.environ.get("EMAIL_FROM", "your_email@example.com")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "your_password")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.example.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 465))

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
DB_FILE = os.path.join(APP_ROOT, "students.db")
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
ID_WIDTH_PX = int((86 / 25.4) * DPI)  # 86mm width for landscape
ID_HEIGHT_PX = int((56 / 25.4) * DPI)  # 56mm height for landscape
PORTRAIT_WIDTH_PX = ID_HEIGHT_PX       # 56mm width for portrait
PORTRAIT_HEIGHT_PX = ID_WIDTH_PX       # 86mm height for portrait
A4_WIDTH_PX = int((210 / 25.4) * DPI)  # A4 portrait width
A4_HEIGHT_PX = int((297 / 25.4) * DPI) # A4 portrait height
A4_LANDSCAPE_WIDTH_PX = A4_HEIGHT_PX
A4_LANDSCAPE_HEIGHT_PX = A4_WIDTH_PX

# ================== Student Blueprint ==================
student_bp = Blueprint('student', __name__)

# ================== Helper Functions ==================
def get_form_int(form_data, key, default=0):
    """Safely get integer value from form data"""
    try:
        value = form_data.get(key)
        if value is None or value == '':
            return default
        return int(value)
    except (ValueError, TypeError):
        return default
    




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
    else:  # normal
        return text

# ================== Database ==================
def init_db():
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("PRAGMA foreign_keys = ON")
            
            # Create students table
            c.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    father_name TEXT,
                    class_name TEXT,
                    dob TEXT,
                    address TEXT,
                    phone TEXT,
                    photo_filename TEXT,
                    generated_filename TEXT,
                    created_at TEXT,
                    data_hash TEXT UNIQUE,
                    template_id INTEGER,
                    school_name TEXT,
                    email TEXT UNIQUE,
                    password TEXT NOT NULL,
                    FOREIGN KEY (template_id) REFERENCES templates(id)
                )
            """)
            
            # Create templates table with orientation
            c.execute("""
                CREATE TABLE IF NOT EXISTS templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL UNIQUE,
                    school_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    font_settings TEXT DEFAULT '{}',
                    photo_settings TEXT DEFAULT '{}',
                    card_orientation TEXT DEFAULT 'landscape'
                )
            """)
            
            conn.commit()
            logger.info("Database initialized successfully")
    except sqlite3.Error as e:
        logger.error(f"Error initializing database: {e}")
        raise

def migrate_database():
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            
            # Add card_orientation column if it doesn't exist
            c.execute("PRAGMA table_info(templates)")
            columns = [col[1] for col in c.fetchall()]
            if 'card_orientation' not in columns:
                c.execute("ALTER TABLE templates ADD COLUMN card_orientation TEXT DEFAULT 'landscape'")
                logger.info("Added card_orientation column to templates table")
            
            # Check and remove unique constraint on phone
            c.execute("PRAGMA index_list(students)")
            indexes = c.fetchall()
            phone_has_unique = False
            for index in indexes:
                index_name = index[1]
                c.execute(f"PRAGMA index_info({index_name})")
                index_info = c.fetchall()
                for info in index_info:
                    if info[2] == "phone":
                        phone_has_unique = True
                        break
                if phone_has_unique:
                    break
            
            if phone_has_unique:
                logger.info("Phone column has unique constraint, recreating table...")
                c.execute("""
                    CREATE TABLE students_temp (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        father_name TEXT,
                        class_name TEXT,
                        dob TEXT,
                        address TEXT,
                        phone TEXT,
                        photo_filename TEXT,
                        generated_filename TEXT,
                        created_at TEXT,
                        data_hash TEXT UNIQUE,
                        template_id INTEGER,
                        school_name TEXT,
                        email TEXT UNIQUE,
                        password TEXT NOT NULL,
                        FOREIGN KEY (template_id) REFERENCES templates(id)
                    )
                """)
                c.execute("""
                    INSERT INTO students_temp
                    SELECT id, name, father_name, class_name, dob, address, phone,
                           photo_filename, generated_filename, created_at, data_hash,
                           template_id, school_name, email, password
                    FROM students
                    WHERE password IS NOT NULL
                """)
                c.execute("DROP TABLE students")
                c.execute("ALTER TABLE students_temp RENAME TO students")
                logger.info("Table recreated without phone unique constraint")
            
            # Add email and password columns if not exist
            c.execute("PRAGMA table_info(students)")
            student_columns = [col[1] for col in c.fetchall()]
            if 'email' not in student_columns:
                c.execute("ALTER TABLE students ADD COLUMN email TEXT")
                logger.info("Added email column to students table")
            if 'password' not in student_columns:
                c.execute("ALTER TABLE students ADD COLUMN password TEXT NOT NULL DEFAULT ''")
                logger.info("Added password column to students table")
            
            # Check templates table columns
            c.execute("PRAGMA table_info(templates)")
            columns = [col[1] for col in c.fetchall()]
            if 'font_settings' not in columns:
                c.execute("ALTER TABLE templates ADD COLUMN font_settings TEXT DEFAULT '{}'")
            if 'photo_settings' not in columns:
                c.execute("ALTER TABLE templates ADD COLUMN photo_settings TEXT DEFAULT '{}'")
            
            conn.commit()
            logger.info("Database migration completed")
    except sqlite3.Error as e:
        logger.error(f"Error during database migration: {e}")
        raise

def migrate_template_font_colors():
    """Migrate existing templates to use separate label and value font colors"""
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT id, font_settings FROM templates")
            templates = c.fetchall()
            
            migrated_count = 0
            for template_id, font_settings_json in templates:
                if font_settings_json:
                    try:
                        font_settings = json.loads(font_settings_json)
                        needs_update = False
                        
                        # Check if this template needs migration
                        if 'font_color' in font_settings:
                            # Migrate to separate colors
                            if 'label_font_color' not in font_settings:
                                font_settings['label_font_color'] = font_settings['font_color']
                                needs_update = True
                            if 'value_font_color' not in font_settings:
                                font_settings['value_font_color'] = font_settings['font_color']
                                needs_update = True
                            
                        if needs_update:
                            c.execute("UPDATE templates SET font_settings = ? WHERE id = ?", 
                                     (json.dumps(font_settings), template_id))
                            migrated_count += 1
                            logger.info(f"Migrated font colors for template {template_id}")
                            
                    except json.JSONDecodeError:
                        continue
            
            conn.commit()
            logger.info(f"Font color migration completed: {migrated_count} templates updated")
            
    except sqlite3.Error as e:
        logger.error(f"Error during font color migration: {e}")

# ================== Orientation Helper Functions ==================
def get_template_orientation(template_id):
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT card_orientation FROM templates WHERE id = ?", (template_id,))
            result = c.fetchone()
            orientation = result[0] if result and result[0] else 'landscape'
            logger.debug(f"Template {template_id} orientation: {orientation}")
            return orientation
    except sqlite3.Error as e:
        logger.error(f"Error fetching orientation for template {template_id}: {e}")
        return 'landscape'  # Fallback to landscape if database error

def get_card_size(template_id=None):
    """Get card size based on orientation"""
    if template_id:
        try:
            orientation = get_template_orientation(template_id)
            if orientation == 'portrait':
                return PORTRAIT_WIDTH_PX, PORTRAIT_HEIGHT_PX
            else:
                return ID_WIDTH_PX, ID_HEIGHT_PX
        except Exception as e:
            logger.error(f"Error fetching template orientation: {e}")
    
    # Default to landscape
    return ID_WIDTH_PX, ID_HEIGHT_PX

def get_sheet_size(orientation):
    if orientation == 'landscape':
        return A4_WIDTH_PX, A4_HEIGHT_PX  # Portrait sheet for landscape cards
    else:
        return A4_LANDSCAPE_WIDTH_PX, A4_LANDSCAPE_HEIGHT_PX  # Landscape sheet for portrait cards

def get_default_font_config():
    """Return default font configuration for landscape orientation"""
    return {
        "font_bold": "arialbd.ttf",
        "font_regular": "arial.ttf",
        "font_color": [0, 0, 0],  # Legacy field for backward compatibility
        "label_font_color": [0, 0, 0],  # Black for labels
        "value_font_color": [0, 0, 0],  # Black for values
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
        "photo_border_bottom_left": 0
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

# ================== Template Config ==================
def get_templates():
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT id, filename, school_name, created_at, font_settings, photo_settings, card_orientation FROM templates ORDER BY created_at DESC")
            templates = []
            for row in c.fetchall():
                font_settings = get_default_font_config()
                photo_settings = get_default_photo_config()
                
                if row[4]:
                    try:
                        loaded_font = json.loads(row[4])
                        # Handle legacy font_color field
                        if 'font_color' in loaded_font and 'label_font_color' not in loaded_font:
                            loaded_font['label_font_color'] = loaded_font['font_color']
                            loaded_font['value_font_color'] = loaded_font['font_color']
                        font_settings = {**font_settings, **loaded_font}
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid font settings JSON for template {row[0]}, using defaults")
                
                if row[5]:
                    try:
                        loaded_photo = json.loads(row[5])
                        photo_settings = {**photo_settings, **loaded_photo}
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid photo settings JSON for template {row[0]}, using defaults")
                
                template = {
                    'id': row[0],
                    'filename': row[1],
                    'school_name': row[2],
                    'created_at': row[3],
                    'font_settings': font_settings,
                    'photo_settings': photo_settings,
                    'card_orientation': row[6] or 'landscape'
                }
                templates.append(template)
            return templates
    except sqlite3.Error as e:
        logger.error(f"Error fetching templates: {e}")
        return []

def add_template(filename, school_name, card_orientation='landscape'):
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT MAX(id) FROM templates")
            max_id = c.fetchone()[0]
            template_id = (max_id or 0) + 1
            
            default_font_settings = get_default_font_config()
            default_photo_settings = get_default_photo_config()
            
            c.execute("INSERT OR REPLACE INTO templates (id, filename, school_name, created_at, font_settings, photo_settings, card_orientation) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (template_id, filename, school_name, datetime.now().isoformat(),
                       json.dumps(default_font_settings), json.dumps(default_photo_settings), card_orientation))
            conn.commit()
            logger.info(f"Added template: {filename} for school: {school_name} with orientation {card_orientation}")
            return template_id
    except sqlite3.Error as e:
        logger.error(f"Error adding template: {e}")
        raise

def update_template_settings(template_id, font_settings=None, photo_settings=None, card_orientation=None):
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            
            if font_settings is not None:
                default_font = get_default_font_config()
                complete_font_settings = {**default_font, **font_settings}
                c.execute("UPDATE templates SET font_settings = ? WHERE id = ?",
                         (json.dumps(complete_font_settings), template_id))
            
            if photo_settings is not None:
                default_photo = get_default_photo_config()
                complete_photo_settings = {**default_photo, **photo_settings}
                c.execute("UPDATE templates SET photo_settings = ? WHERE id = ?",
                         (json.dumps(complete_photo_settings), template_id))
            
            if card_orientation is not None:
                c.execute("UPDATE templates SET card_orientation = ? WHERE id = ?",
                         (card_orientation, template_id))
            
            conn.commit()
            logger.info(f"Updated settings for template ID {template_id}, orientation: {card_orientation}")
    except sqlite3.Error as e:
        logger.error(f"Error updating template settings: {e}")
        raise

def get_template_path(template_id):
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT filename FROM templates WHERE id = ?", (template_id,))
            result = c.fetchone()
            return os.path.join(STATIC_DIR, result[0]) if result else None
    except sqlite3.Error as e:
        logger.error(f"Error fetching template path for ID {template_id}: {e}")
        return None

def get_template_settings(template_id):
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT font_settings, photo_settings, card_orientation FROM templates WHERE id = ?", (template_id,))
            result = c.fetchone()
            
            if result:
                font_settings = get_default_font_config()
                photo_settings = get_default_photo_config()
                orientation = result[2] or 'landscape'
                
                if result[0]:
                    try:
                        loaded_font = json.loads(result[0])
                        # Handle legacy font_color field for backward compatibility
                        if 'font_color' in loaded_font:
                            # Copy font_color to both label and value colors if they don't exist
                            if 'label_font_color' not in loaded_font:
                                loaded_font['label_font_color'] = loaded_font['font_color']
                            if 'value_font_color' not in loaded_font:
                                loaded_font['value_font_color'] = loaded_font['font_color']
                        font_settings = {**font_settings, **loaded_font}
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid font settings JSON for template {template_id}, using defaults")
                
                if result[1]:
                    try:
                        loaded_photo = json.loads(result[1])
                        photo_settings = {**photo_settings, **loaded_photo}
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid photo settings JSON for template {template_id}, using defaults")
                
                # Adjust settings based on orientation if not overridden
                if orientation == 'portrait':
                    if 'photo_x' not in loaded_photo:
                        photo_settings['photo_x'] = 100
                    if 'photo_y' not in loaded_photo:
                        photo_settings['photo_y'] = 400
                    if 'photo_width' not in loaded_photo:
                        photo_settings['photo_width'] = 216
                    if 'photo_height' not in loaded_photo:
                        photo_settings['photo_height'] = 180
                    
                    if 'label_font_size' not in loaded_font:
                        font_settings['label_font_size'] = 32
                    if 'value_font_size' not in loaded_font:
                        font_settings['value_font_size'] = 28
                    if 'label_x' not in loaded_font:
                        font_settings['label_x'] = 40
                    if 'value_x' not in loaded_font:
                        font_settings['value_x'] = 200
                    if 'start_y' not in loaded_font:
                        font_settings['start_y'] = 120
                    if 'line_height' not in loaded_font:
                        font_settings['line_height'] = 45
                
                return font_settings, photo_settings, orientation
            return get_default_font_config(), get_default_photo_config(), 'landscape'
    except sqlite3.Error as e:
        logger.error(f"Error fetching template settings for ID {template_id}: {e}")
        return get_default_font_config(), get_default_photo_config(), 'landscape'

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

# ================== Duplicate Config ==================
def load_duplicate_config():
    default_config = {"check_phone": False, "check_name_class": True}
    if os.path.exists(DUPLICATE_CONFIG_PATH):
        try:
            with open(DUPLICATE_CONFIG_PATH, "r") as f:
                return {**default_config, **json.load(f)}
        except json.JSONDecodeError as e:
            logger.error(f"Error loading duplicate config: {e}")
    return default_config

def save_duplicate_config(config):
    try:
        with open(DUPLICATE_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
        logger.info("Duplicate config saved successfully")
    except Exception as e:
        logger.error(f"Error saving duplicate config: {e}")

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
    Apply rounded corners to an image.
    
    :param image: PIL Image object (will be converted to RGBA if necessary)
    :param radii: list of 4 ints [top_left, top_right, bottom_right, bottom_left]
    :return: Image with alpha channel for transparency in corners
    """
    image = image.convert("RGBA")
    width, height = image.size
    mask = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(mask)
    tl, tr, br, bl = radii

    # Fill the main areas
    draw.rectangle((tl, 0, width - tr, height), fill=255)
    draw.rectangle((0, tl, width, height - bl), fill=255)

    # Draw corner arcs
    if tl > 0:
        draw.pieslice((0, 0, tl*2, tl*2), 180, 270, fill=255)
    if tr > 0:
        draw.pieslice((width - tr*2, 0, width, tr*2), 270, 360, fill=255)
    if br > 0:
        draw.pieslice((width - br*2, height - br*2, width, height), 0, 90, fill=255)
    if bl > 0:
        draw.pieslice((0, height - bl*2, bl*2, height), 90, 180, fill=255)

    image.putalpha(mask)
    return image

def add_card_to_sheet(card_img, template_id):
    try:
        card_width, card_height = get_card_size(template_id)
        orientation = get_template_orientation(template_id)
        GAP = 5  # Gap between cards in pixels
        
        if orientation == 'landscape':
            sheet_size = (A4_WIDTH_PX, A4_HEIGHT_PX)  # Portrait sheet
            max_cols = 2
            max_rows = 5
        else:
            sheet_size = (A4_LANDSCAPE_WIDTH_PX, A4_LANDSCAPE_HEIGHT_PX)  # Landscape sheet
            max_cols = 5
            max_rows = 2
        
        max_cards = max_cols * max_rows  # 10 cards per sheet
        sheet_path = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}.png")
        positions_file = os.path.join(GENERATED_FOLDER, f"positions_template_{template_id}.txt")

        if not isinstance(card_img, Image.Image):
            raise ValueError("Card image must be a PIL Image object")
        if card_img.mode != "RGB":
            card_img = card_img.convert("RGB")

        # Create new sheet or load existing
        if not os.path.exists(sheet_path):
            sheet = Image.new("RGB", sheet_size, "white")
            positions = []
        else:
            sheet = Image.open(sheet_path)
            if sheet.size != sheet_size:
                sheet = Image.new("RGB", sheet_size, "white")
                positions = []
            else:
                positions = []
                if os.path.exists(positions_file):
                    with open(positions_file, "r") as f:
                        positions = [tuple(map(int, p.split(','))) for p in f.read().split(';') if p]

        # Check if sheet is full (10 cards)
        if len(positions) >= max_cards:
            # Generate PDF when sheet is full
            pdf_name = f"sheet_template_{template_id}_{orientation}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
            pdf_path = os.path.join(GENERATED_FOLDER, pdf_name)
            sheet.save(pdf_path, "PDF", resolution=DPI)
            
            # Clean up temporary files
            if os.path.exists(sheet_path):
                os.remove(sheet_path)
            if os.path.exists(positions_file):
                os.remove(positions_file)
                
            # Start new sheet
            sheet = Image.new("RGB", sheet_size, "white")
            positions = []

        # Calculate grid position with gap
        total_grid_width = max_cols * card_width + (max_cols - 1) * GAP
        total_grid_height = max_rows * card_height + (max_rows - 1) * GAP
        
        # Center the grid on A4 sheet with optimal margins
        margin_x = max(20, (sheet_size[0] - total_grid_width) // 2)
        margin_y = max(20, (sheet_size[1] - total_grid_height) // 2)
        
        # Calculate position for current card
        col = len(positions) % max_cols
        row = len(positions) // max_cols
        x = margin_x + col * (card_width + GAP)
        y = margin_y + row * (card_height + GAP)

        # Resize and paste the card
        resized_card = card_img.resize((card_width, card_height))
        sheet.paste(resized_card, (x, y))
        positions.append((x, y))

        # Save the updated sheet
        sheet.save(sheet_path, "PNG")
        with open(positions_file, "w") as f:
            f.write(';'.join([f"{px},{py}" for px, py in positions]))
            
        logger.info(f"Added card to {orientation} sheet. Template: {template_id}, Position: {len(positions)}/{max_cards}, Grid: {max_cols}x{max_rows} with {GAP}px gap")
            
    except Exception as e:
        logger.error(f"Error in add_card_to_sheet: {e}")
        raise

def generate_data_hash(form_data, photo_filename=None):
    data_string = f"{form_data['name']}{form_data['father_name']}{form_data['class_name']}{form_data['dob']}{form_data['address']}{form_data['phone']}"
    if photo_filename:
        data_string += photo_filename
    return hashlib.md5(data_string.encode()).hexdigest()

def check_duplicate_student(form_data, photo_filename=None, student_id=None):
    duplicate_config = load_duplicate_config()
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            if duplicate_config.get("check_phone", False):
                c.execute("SELECT id FROM students WHERE phone = ? AND id != ?",
                          (form_data['phone'], student_id or 0))
                if c.fetchone():
                    return True, "A student with this phone number already exists."
            if duplicate_config.get("check_name_class", True):
                c.execute("SELECT id FROM students WHERE name = ? AND class_name = ? AND id != ?",
                          (form_data['name'], form_data['class_name'], student_id or 0))
                if c.fetchone():
                    return True, "A student with this name and class combination already exists."
            return False, None
    except sqlite3.Error as e:
        logger.error(f"Error checking duplicates: {e}")
        return True, f"Database error: {str(e)}"

def crop_photo_to_aspect_ratio(photo_path, aspect_ratio=(3, 4)):
    try:
        with Image.open(photo_path) as img:
            width, height = img.size
            target_ratio = aspect_ratio[0] / aspect_ratio[1]
            current_ratio = width / height
            if current_ratio > target_ratio:
                new_width = int(height * target_ratio)
                left = (width - new_width) // 2
                right = left + new_width
                box = (left, 0, right, height)
            else:
                new_height = int(width / target_ratio)
                top = (height - new_height) // 2
                bottom = top + new_height
                box = (0, top, width, bottom)
            cropped_img = img.crop(box)
            cropped_img.save(photo_path)
            return True
    except Exception as e:
        logger.error(f"Error cropping photo {photo_path}: {e}")
        return False

def send_email(to, subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_FROM
    msg['To'] = to
    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")



# ================== Landing Page Routes ==================
@app.route("/")
def landing():
    """Landing page route"""
    return render_template("landing_page.html")

@app.route("/home")
def home():
    """Alternative home route"""
    return redirect(url_for("landing"))

@app.route("/documentation")
def documentation():
    """Documentation page"""
    return render_template("documentation.html")

@app.route("/about")
def about():
    """About page"""
    return render_template("about.html")


# ================== Auth ==================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["username"] == "admin" and request.form["password"] == "secret123":
            session["admin"] = True
            logger.info("Admin logged in successfully")
            return redirect("/admin")
        else:
            logger.warning("Failed login attempt")
            return render_template("login.html", error="Invalid login credentials"), 401
    return render_template("login.html")


@app.route("/student_login", methods=["GET", "POST"])
def student_login():
    templates = get_templates()
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        school_name = request.form.get("school_name", "").strip()
        
        logger.info(f"Login attempt for email: {email}, school: {school_name}")
        
        if not email or not password or not school_name:
            error = "All fields are required."
            logger.warning("Login failed: Missing required fields")
        else:
            try:
                with sqlite3.connect(DB_FILE, timeout=10) as conn:
                    c = conn.cursor()
                    c.execute("SELECT email, password FROM students WHERE LOWER(email) = LOWER(?)", (email,))
                    student = c.fetchone()
                    
                    if not student:
                        error = "No account found with this email."
                        logger.warning(f"Login failed: No account found for email {email}")
                    elif student[1] is None:
                        error = "Account has no password set. Please contact support."
                        logger.warning(f"Login failed: NULL password for email {email}")
                    elif check_password_hash(student[1], password):
                        session["student_email"] = student[0]
                        session["student_school_name"] = school_name
                        logger.info(f"Login successful for email: {student[0]}")
                        # CHANGED: Redirect to index instead of landing page
                        return redirect(url_for("index"))
                    else:
                        error = "Invalid password."
                        logger.warning(f"Login failed: Invalid password for email {email}")
            except sqlite3.Error as e:
                error = f"Database error: {str(e)}"
                logger.error(f"Database error during login for email {email}: {e}")
    
    return render_template("login_student.html", templates=templates, error=error)

@app.route("/register", methods=["GET", "POST"])
def register():
    templates = get_templates()
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        school_name = request.form.get("school_name", "").strip()
        name = request.form.get("name", "").strip()
        
        if not all([email, password, confirm_password, school_name, name]):
            error = "All fields (name, email, password, confirm password, school name) are required."
            logger.warning("Registration failed: Missing required fields")
        elif not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
            error = "Invalid email address."
            logger.warning(f"Registration failed: Invalid email {email}")
        elif password != confirm_password:
            error = "Passwords do not match."
            logger.warning("Registration failed: Passwords do not match")
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
            logger.warning("Registration failed: Password too short")
        elif len(name) < 2:
            error = "Name must be at least 2 characters."
            logger.warning("Registration failed: Name too short")
        else:
            try:
                with sqlite3.connect(DB_FILE, timeout=10) as conn:
                    c = conn.cursor()
                    c.execute("SELECT id FROM students WHERE LOWER(email) = LOWER(?)", (email,))
                    if c.fetchone():
                        error = "Email already registered."
                        logger.warning(f"Registration failed: Email {email} already registered")
                    else:
                        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
                        if not hashed_password:
                            error = "Failed to hash password."
                            logger.error("Registration failed: Password hashing error")
                        else:
                            c.execute("INSERT INTO students (name, email, password, school_name) VALUES (?, ?, ?, ?)",
                                      (name, email.lower(), hashed_password, school_name))
                            conn.commit()
                            session["student_email"] = email.lower()
                            session["student_school_name"] = school_name
                            logger.info(f"Registered new student: {email}, name: {name}")
                            # CHANGED: Redirect to index instead of landing page
                            return redirect(url_for("index"))
            except sqlite3.OperationalError as e:
                error = f"Database error: {str(e)}"
                logger.error(f"Database error during registration for email {email}: {e}")
            except sqlite3.Error as e:
                error = f"Database error: {str(e)}"
                logger.error(f"General database error during registration for email {email}: {e}")
    return render_template("register.html", templates=templates, error=error)

@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not email:
            flash("Email is required.", "error")
            return render_template("reset_password.html")
        try:
            with sqlite3.connect(DB_FILE, timeout=10) as conn:
                c = conn.cursor()
                c.execute("SELECT id FROM students WHERE email = ?", (email,))
                student = c.fetchone()
                if student:
                    temp_password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
                    hashed_password = generate_password_hash(temp_password, method='pbkdf2:sha256')
                    c.execute("UPDATE students SET password = ? WHERE email = ?", (hashed_password, email))
                    conn.commit()
                    send_email(email, "Password Reset", f"Your temporary password is: {temp_password}")
                    flash("A temporary password has been sent to your email.", "success")
                else:
                    flash("No account found with this email.", "error")
        except sqlite3.Error as e:
            flash(f"Database error: {str(e)}", "error")
            logger.error(f"Error resetting password: {e}")
        return redirect(url_for("reset_password"))
    return render_template("reset_password.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("student_login"))

@app.before_request
def require_login():
    admin_routes = ["/admin", "/upload_template", "/delete_all", "/upload_font", "/update_font", 
                    "/update_photo_position", "/delete_student", "/export_csv", "/download_template", 
                    "/remove_template", "/update_duplicate_settings", "/delete_pdf", "/update_template_settings",
                    "/admin_student_credentials", "/admin_add_student_credential", "/admin_update_student_credential",
                    "/admin_delete_student_credential", "/admin_reset_student_password", "/admin/student_preview",
                    "/admin/generate_preview", "/admin/download_student_pdf"]
    if request.path in admin_routes and not session.get("admin"):
        logger.warning(f"Unauthorized access attempt to {request.path}")
        return redirect(url_for("login"))

# ================== Student Routes ==================
@student_bp.route("/update_email", methods=["POST"])
def update_email():
    if 'student_email' not in session:
        flash('Please log in to update your email.', 'error')
        return redirect(url_for('student_login'))
    
    email = request.form.get('email')
    if not email or not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
        flash('Invalid email address.', 'error')
        return redirect(url_for('admin'))
    
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM students WHERE email = ? AND email != ?", (email, session['student_email']))
            if c.fetchone():
                flash('Email already in use.', 'error')
                return redirect(url_for('admin'))
            
            c.execute("UPDATE students SET email = ? WHERE email = ?", (email, session['student_email']))
            if c.rowcount == 0:
                flash('No matching student record found.', 'error')
            else:
                session['student_email'] = email
                flash('Email updated successfully.', 'success')
            conn.commit()
    except sqlite3.Error as e:
        flash(f'Error updating email: {e}', 'error')
        logger.error(f"Error updating email: {e}")
        return redirect(url_for('admin'))
    
    return redirect(url_for('admin'))

@student_bp.route("/update_password", methods=["POST"])
def update_password():
    if 'student_email' not in session:
        flash('Please log in to update your password.', 'error')
        return redirect(url_for('student_login'))
    
    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    if not all([current_password, new_password, confirm_password]):
        flash('All password fields are required.', 'error')
        return redirect(url_for('admin'))
    
    if new_password != confirm_password:
        flash('New password and confirmation do not match.', 'error')
        return redirect(url_for('admin'))
    
    if len(new_password) < 6:
        flash('New password must be at least 6 characters.', 'error')
        return redirect(url_for('admin'))

    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT password FROM students WHERE email = ?", (session['student_email'],))
            student = c.fetchone()
            
            if not student or not student[0]:
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('admin'))
            
            try:
                if not check_password_hash(student[0], current_password):
                    flash('Current password is incorrect.', 'error')
                    return redirect(url_for('admin'))
            except Exception as e:
                logger.error(f"Password verification error: {e}")
                flash('Authentication error. Please try again.', 'error')
                return redirect(url_for('admin'))
            
            hashed_password = generate_password_hash(new_password)
            c.execute("UPDATE students SET password = ? WHERE email = ?", 
                     (hashed_password, session['student_email']))
            conn.commit()
            flash('Password updated successfully.', 'success')
    except sqlite3.Error as e:
        flash(f'Error updating password: {e}', 'error')
        logger.error(f"Error updating password: {e}")
        return redirect(url_for('admin'))
    
    return redirect(url_for('admin'))

# ================== Preview Routes ==================
@app.route("/admin/student_preview/<int:student_id>")
def admin_student_preview(student_id):
    """Get student preview data"""
    if not session.get("admin") and not session.get("student_email"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT name, class_name, generated_filename, template_id 
                FROM students WHERE id = ?
            """, (student_id,))
            student = c.fetchone()
            
            if not student:
                return jsonify({"success": False, "error": "Student not found"}), 404
            
            name, class_name, generated_filename, template_id = student
            
            # Get preview image URL
            preview_url = None
            if generated_filename:
                preview_filename = generated_filename.replace('.pdf', '.jpg')
                preview_path = os.path.join(GENERATED_FOLDER, preview_filename)
                if os.path.exists(preview_path):
                    preview_url = url_for('static', filename=f'generated/{preview_filename}')
            
            return jsonify({
                "success": True,
                "name": name,
                "class_name": class_name,
                "preview_url": preview_url or url_for('static', filename='placeholder.jpg'),
                "has_preview": preview_url is not None
            })
    except sqlite3.Error as e:
        logger.error(f"Error fetching student preview: {e}")
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route("/admin/generate_preview/<int:student_id>")
def generate_student_preview(student_id):
    """Generate a fresh preview for a student"""
    if not session.get("admin") and not session.get("student_email"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT s.name, s.father_name, s.class_name, s.dob, s.address, s.phone, 
                       s.photo_filename, s.generated_filename, s.template_id,
                       t.filename, t.font_settings, t.photo_settings, t.card_orientation
                FROM students s
                LEFT JOIN templates t ON s.template_id = t.id
                WHERE s.id = ?
            """, (student_id,))
            result = c.fetchone()
            
            if not result:
                return jsonify({"success": False, "error": "Student not found"}), 404
            
            # Unpack result
            (name, father_name, class_name, dob, address, phone, 
             photo_filename, generated_filename, template_id,
             template_filename, font_settings_json, photo_settings_json, card_orientation) = result
            
            # Load template settings
            font_settings = get_default_font_config()
            photo_settings = get_default_photo_config()
            
            if font_settings_json:
                try:
                    loaded_font = json.loads(font_settings_json)
                    # Handle legacy font_color field
                    if 'font_color' in loaded_font and 'label_font_color' not in loaded_font:
                        loaded_font['label_font_color'] = loaded_font['font_color']
                        loaded_font['value_font_color'] = loaded_font['font_color']
                    font_settings = {**font_settings, **loaded_font}
                except json.JSONDecodeError:
                    logger.warning(f"Invalid font settings for student {student_id}")
            
            if photo_settings_json:
                try:
                    loaded_photo = json.loads(photo_settings_json)
                    photo_settings = {**photo_settings, **loaded_photo}
                except json.JSONDecodeError:
                    logger.warning(f"Invalid photo settings for student {student_id}")
            
            # Adjust settings based on orientation
            photo_settings = get_photo_settings_for_orientation(template_id, photo_settings)
            font_settings = get_font_settings_for_orientation(template_id, font_settings)
            
            # Generate preview
            template_path = os.path.join(STATIC_DIR, template_filename) if template_filename else None
            if not template_path or not os.path.exists(template_path):
                return jsonify({"success": False, "error": "Template not found"}), 404
            
            try:
                card_width, card_height = get_card_size(template_id)
                template = load_template(template_path)
                template = template.resize((card_width, card_height))
                
                draw = ImageDraw.Draw(template)
                
                # Load fonts
                FONT_BOLD_PATH = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
                FONT_REGULAR_PATH = os.path.join(FONTS_FOLDER, font_settings["font_regular"])
                
                # Handle font colors safely
                try:
                    label_font_color = tuple(font_settings.get("label_font_color", [0, 0, 0]))
                    value_font_color = tuple(font_settings.get("value_font_color", [0, 0, 0]))
                except Exception as e:
                    logger.warning(f"Error parsing font colors: {e}, using default black")
                    label_font_color = (0, 0, 0)
                    value_font_color = (0, 0, 0)
                
                # Apply text case transformation
                text_case = font_settings.get("text_case", "normal")
                labels = ["NAME", "F.NAME", "CLASS", "D.O.B.", "ADDRESS", "MOBILE"]
                values = [
                    apply_text_case(name, text_case),
                    apply_text_case(father_name, text_case),
                    apply_text_case(class_name, text_case),
                    dob,  # Don't apply text case to date
                    apply_text_case(address, text_case),
                    phone  # Don't apply text case to phone number
                ]
                
                # Draw text
                label_x = font_settings["label_x"]
                value_x = font_settings["value_x"]
                start_y = font_settings["start_y"]
                line_height = font_settings["line_height"]
                max_value_width = template.width - value_x - 50
                
                for i, (label, value) in enumerate(zip(labels, values)):
                    y = start_y + i * line_height
                    try:
                        label_font = ImageFont.truetype(FONT_BOLD_PATH, font_settings["label_font_size"])
                    except OSError:
                        label_font = ImageFont.truetype(os.path.join(FONTS_FOLDER, "arialbd.ttf"), font_settings["label_font_size"])
                    
                    value_font = load_font_dynamic(FONT_REGULAR_PATH, value, max_value_width, font_settings["value_font_size"])
                    draw.text((label_x, y), f"{label}:", font=label_font, fill=label_font_color)
                    draw.text((value_x, y), value, font=value_font, fill=value_font_color)
                
                # Add photo
                if photo_filename:
                    photo_path = os.path.join(UPLOAD_FOLDER, photo_filename)
                    if os.path.exists(photo_path):
                        try:
                            photo_img = Image.open(photo_path).convert("RGBA").resize(
                                (photo_settings["photo_width"], photo_settings["photo_height"])
                            )
                            radii = [
                                photo_settings.get("photo_border_top_left", 0),
                                photo_settings.get("photo_border_top_right", 0),
                                photo_settings.get("photo_border_bottom_right", 0),
                                photo_settings.get("photo_border_bottom_left", 0)
                            ]
                            photo_img = round_photo(photo_img, radii)
                            template.paste(photo_img, (photo_settings["photo_x"], photo_settings["photo_y"]), photo_img)
                        except Exception as e:
                            logger.error(f"Error adding photo to preview: {e}")
                
                # Save preview
                preview_filename = f"preview_{student_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
                preview_path = os.path.join(GENERATED_FOLDER, preview_filename)
                template.save(preview_path, "JPEG", quality=95)
                
                preview_url = url_for('static', filename=f'generated/{preview_filename}')
                
                return jsonify({
                    "success": True,
                    "preview_url": preview_url,
                    "message": "Preview generated successfully"
                })
                
            except Exception as e:
                logger.error(f"Error generating preview: {e}")
                return jsonify({"success": False, "error": f"Preview generation failed: {str(e)}"}), 500
                
    except sqlite3.Error as e:
        logger.error(f"Database error generating preview: {e}")
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route("/test_preview")
def test_preview():
    """Test route to verify preview generation works"""
    try:
        # Create a simple test image
        test_img = Image.new("RGB", (800, 500), color="lightblue")
        draw = ImageDraw.Draw(test_img)
        draw.rectangle([50, 50, 750, 450], outline="blue", width=3)
        draw.text((100, 100), "Preview Test - Working!", fill="black")
        draw.text((100, 150), f"Time: {datetime.now()}", fill="black")
        
        test_path = os.path.join(GENERATED_FOLDER, "test_preview.jpg")
        test_img.save(test_path, "JPEG", quality=95)
        
        return jsonify({
            "success": True,
            "message": "Preview test completed",
            "test_image": url_for("static", filename="generated/test_preview.jpg")
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/admin/download_student_pdf/<int:student_id>")
def download_student_pdf(student_id):
    """Download student PDF"""
    if not session.get("admin") and not session.get("student_email"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT generated_filename FROM students WHERE id = ?", (student_id,))
            result = c.fetchone()
            
            if not result or not result[0]:
                return jsonify({"success": False, "error": "PDF not found"}), 404
            
            pdf_filename = result[0]
            pdf_path = os.path.join(GENERATED_FOLDER, pdf_filename)
            
            if not os.path.exists(pdf_path):
                return jsonify({"success": False, "error": "PDF file not found"}), 404
            
            return send_file(pdf_path, as_attachment=True, download_name=pdf_filename)
            
    except sqlite3.Error as e:
        logger.error(f"Error downloading student PDF: {e}")
        return jsonify({"success": False, "error": "Database error"}), 500

# ================== Student Credential Management Routes ==================
@app.route("/admin_student_credentials")
def admin_student_credentials():
    if not session.get("admin"):
        return redirect(url_for("login"))
    
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT id, name, email, school_name, created_at 
                FROM students 
                WHERE email IS NOT NULL AND email != '' 
                ORDER BY created_at DESC
            """)
            students = c.fetchall()
            
            return render_template(
                "admin_student_credentials.html",
                students=students,
                success=request.args.get("success"),
                error=request.args.get("error")
            )
    except sqlite3.Error as e:
        logger.error(f"Error fetching student credentials: {e}")
        return redirect(url_for("admin", error=f"Error fetching student credentials: {str(e)}"))

@app.route("/admin_add_student_credential", methods=["POST"])
def admin_add_student_credential():
    if not session.get("admin"):
        return redirect(url_for("login"))
    
    try:
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        school_name = request.form.get("school_name", "").strip()
        
        if not all([name, email, password, school_name]):
            return redirect(url_for("admin_student_credentials", error="All fields are required"))
        
        if not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
            return redirect(url_for("admin_student_credentials", error="Invalid email address"))
        
        if len(password) < 6:
            return redirect(url_for("admin_student_credentials", error="Password must be at least 6 characters"))
        
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            
            # Check if email already exists
            c.execute("SELECT id FROM students WHERE email = ?", (email,))
            if c.fetchone():
                return redirect(url_for("admin_student_credentials", error="Email already registered"))
            
            # Insert new student credential
            c.execute("""
                INSERT INTO students (name, email, password, school_name, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (name, email, hashed_password, school_name, datetime.now().isoformat()))
            
            conn.commit()
            logger.info(f"Admin added student credential: {email}")
            
            return redirect(url_for("admin_student_credentials", success="Student credential added successfully"))
            
    except sqlite3.Error as e:
        logger.error(f"Error adding student credential: {e}")
        return redirect(url_for("admin_student_credentials", error=f"Error adding student credential: {str(e)}"))

@app.route("/admin_update_student_credential/<int:student_id>", methods=["POST"])
def admin_update_student_credential(student_id):
    if not session.get("admin"):
        return redirect(url_for("login"))
    
    try:
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        school_name = request.form.get("school_name", "").strip()
        
        if not all([name, email, school_name]):
            return redirect(url_for("admin_student_credentials", error="Name, email, and school name are required"))
        
        if not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
            return redirect(url_for("admin_student_credentials", error="Invalid email address"))
        
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            
            # Check if email already exists for other students
            c.execute("SELECT id FROM students WHERE email = ? AND id != ?", (email, student_id))
            if c.fetchone():
                return redirect(url_for("admin_student_credentials", error="Email already registered to another student"))
            
            update_data = [name, email, school_name]
            update_query = "UPDATE students SET name = ?, email = ?, school_name = ?"
            
            # Update password only if provided
            if password:
                if len(password) < 6:
                    return redirect(url_for("admin_student_credentials", error="Password must be at least 6 characters"))
                hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
                update_data.append(hashed_password)
                update_query += ", password = ?"
            
            update_data.append(student_id)
            update_query += " WHERE id = ?"
            
            c.execute(update_query, update_data)
            conn.commit()
            logger.info(f"Admin updated student credential for ID: {student_id}")
            
            return redirect(url_for("admin_student_credentials", success="Student credential updated successfully"))
            
    except sqlite3.Error as e:
        logger.error(f"Error updating student credential: {e}")
        return redirect(url_for("admin_student_credentials", error=f"Error updating student credential: {str(e)}"))

@app.route("/admin_delete_student_credential/<int:student_id>", methods=["POST"])
def admin_delete_student_credential(student_id):
    if not session.get("admin"):
        return redirect(url_for("login"))
    
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            
            # Check if student has any ID cards created
            c.execute("SELECT COUNT(*) FROM students WHERE id = ? AND (photo_filename IS NOT NULL OR generated_filename IS NOT NULL)", (student_id,))
            has_cards = c.fetchone()[0] > 0
            
            if has_cards:
                return redirect(url_for("admin_student_credentials", error="Cannot delete student with existing ID cards. Delete the ID cards first."))
            
            c.execute("DELETE FROM students WHERE id = ?", (student_id,))
            conn.commit()
            logger.info(f"Admin deleted student credential for ID: {student_id}")
            
            return redirect(url_for("admin_student_credentials", success="Student credential deleted successfully"))
            
    except sqlite3.Error as e:
        logger.error(f"Error deleting student credential: {e}")
        return redirect(url_for("admin_student_credentials", error=f"Error deleting student credential: {str(e)}"))

@app.route("/admin_reset_student_password/<int:student_id>", methods=["POST"])
def admin_reset_student_password(student_id):
    if not session.get("admin"):
        return redirect(url_for("login"))
    
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            
            # Generate a random password
            new_password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
            hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
            
            c.execute("UPDATE students SET password = ? WHERE id = ?", (hashed_password, student_id))
            conn.commit()
            
            # Get student email to send the new password
            c.execute("SELECT email, name FROM students WHERE id = ?", (student_id,))
            student = c.fetchone()
            
            if student:
                student_email, student_name = student
                try:
                    send_email(student_email, "Password Reset", f"Your new password is: {new_password}")
                except Exception as email_error:
                    logger.error(f"Error sending email: {email_error}")
                    return redirect(url_for("admin_student_credentials", success=f"Password reset successfully. New password: {new_password} (Email failed to send)"))
            
            logger.info(f"Admin reset password for student ID: {student_id}")
            
            return redirect(url_for("admin_student_credentials", success=f"Password reset successfully. New password sent to student's email."))
            
    except sqlite3.Error as e:
        logger.error(f"Error resetting student password: {e}")
        return redirect(url_for("admin_student_credentials", error=f"Error resetting password: {str(e)}"))

# ================== Main Routes ==================
@app.route("/index", methods=["GET", "POST"])
def index():
    # CHANGED: Check if user is logged in, redirect to login if not
    if not session.get("student_email"):
        return redirect(url_for("student_login"))
    
    generated_url = None
    download_url = None
    form_data = {}
    success = None
    error = None
    templates = get_templates()
    show_fetch = True
    unique_edit_id = None
    school_name = session.get("student_school_name")
    selected_template_id = None
    if school_name:
        for t in templates:
            if t['school_name'] == school_name:
                selected_template_id = t['id']
                break

    if request.method == "POST":
        template_id = int(request.form.get("template_id", 0))
        school_name = next((t['school_name'] for t in templates if t['id'] == template_id), None)
        template_path = get_template_path(template_id)
        if not template_path or not os.path.exists(template_path):
            error = "No template found. Please select a valid template."
            logger.error(f"Template not found for ID {template_id}")
            return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 400
        
        font_settings, photo_settings, card_orientation = get_template_settings(template_id)
        card_width, card_height = get_card_size(template_id)
        
        FONT_BOLD_PATH = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
        FONT_REGULAR_PATH = os.path.join(FONTS_FOLDER, font_settings["font_regular"])
        
        if not is_valid_font_file(FONT_BOLD_PATH) or not is_valid_font_file(FONT_REGULAR_PATH):
            error = f"Invalid font file: {font_settings['font_bold']} or {font_settings['font_regular']}"
            logger.error(error)
            return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 500
        
        # FIXED: Handle font color with backward compatibility
        font_color_input = font_settings.get("font_color")  # Legacy support
        if font_color_input is None:
            # Use label font color as default if font_color doesn't exist
            font_color_input = font_settings.get("label_font_color", [0, 0, 0])
        
        try:
            if isinstance(font_color_input, str):
                if font_color_input.startswith("#") and len(font_color_input) == 7:
                    FONT_COLOR = tuple(int(font_color_input[i:i+2], 16) for i in (1, 3, 5))
                else:
                    FONT_COLOR = tuple(map(int, font_color_input.split(",")))
            else:
                FONT_COLOR = tuple(font_color_input)
        except Exception as e:
            error = f"Invalid font color format: {str(e)}"
            logger.error(error)
            return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 400
        
        name = request.form.get("name", "").strip()
        father_name = request.form.get("father_name", "").strip()
        class_name = request.form.get("class_name", "").strip()
        dob = request.form.get("dob", "").strip()
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        form_data = {
            'name': name,
            'father_name': father_name,
            'class_name': class_name,
            'dob': dob,
            'address': address,
            'phone': phone,
            'template_id': template_id,
            'school_name': school_name
        }
        
        try:
            with sqlite3.connect(DB_FILE, timeout=10) as conn:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM students WHERE email = ?", (session['student_email'],))
                count = c.fetchone()[0]
                if count >= 3:
                    error = "You have reached the maximum limit of 3 ID cards per email."
                    return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                         form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 400
        except sqlite3.Error as e:
            error = f"Database error: {str(e)}"
            logger.error(error)
            return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 500
        
        is_duplicate, duplicate_message = check_duplicate_student(form_data)
        if is_duplicate:
            logger.warning(f"Duplicate student detected: {duplicate_message}")
            return render_template("index.html", generated_url=generated_url,
                                 download_url=download_url, form_data=form_data,
                                 error=duplicate_message, templates=templates, show_fetch=show_fetch), 400
        
        photo_fn = None
        photo_stored = None
        photo_path = None
        if 'photo' in request.files and request.files['photo'].filename:
            photo = request.files['photo']
            photo_fn = secure_filename(photo.filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            photo_stored = f"{timestamp}_{photo_fn}"
            photo_path = os.path.join(UPLOAD_FOLDER, photo_stored)
            try:
                photo.save(photo_path)
                crop_photo_to_aspect_ratio(photo_path)
            except Exception as e:
                error = f"Error saving photo: {str(e)}"
                logger.error(error)
                return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 500
        else:
            error = "Photo is required"
            logger.error(error)
            return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 400
        
        data_hash = generate_data_hash(form_data, photo_stored)
        
        try:
            template = load_template(template_path)
            template = template.resize((card_width, card_height))
            draw = ImageDraw.Draw(template)
            
            # Apply text case transformation
            text_case = font_settings.get("text_case", "normal")
            labels = ["NAME", "F.NAME", "CLASS", "D.O.B.", "MOBILE", "ADDRESS"]
            values = [
                apply_text_case(name, text_case),
                apply_text_case(father_name, text_case),
                apply_text_case(class_name, text_case),
                dob,
                phone,
                apply_text_case(address, text_case)
            ]
            
            # Draw text with adjusted settings
            label_x = font_settings["label_x"]
            value_x = font_settings["value_x"]
            start_y = font_settings["start_y"]
            line_height = font_settings["line_height"]
            max_value_width = template.width - value_x - 20
            
            for i, (label, value) in enumerate(zip(labels, values)):
                y = start_y + i * line_height
                try:
                    label_font = ImageFont.truetype(FONT_BOLD_PATH, font_settings["label_font_size"])
                except OSError as e:
                    error = f"Failed to load font {FONT_BOLD_PATH}: {str(e)}"
                    logger.error(error)
                    return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                         form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 500
                
                if label == "ADDRESS":
                    char_width = font_settings["value_font_size"] * 0.6
                    max_chars = int(max_value_width / char_width)
                    wrapped_lines = textwrap.wrap(value, width=max_chars) or [""]
                    
                    draw.text((label_x, y), f"{label}:", font=label_font, fill=FONT_COLOR)
                    value_font = load_font_dynamic(FONT_REGULAR_PATH, max(wrapped_lines, key=len, default=""), max_value_width, font_settings["value_font_size"])
                    for line in wrapped_lines:
                        draw.text((value_x, y), line, font=value_font, fill=FONT_COLOR)
                        y += line_height
                else:
                    value_font = load_font_dynamic(FONT_REGULAR_PATH, value, max_value_width, font_settings["value_font_size"])
                    draw.text((label_x, y), f"{label}:", font=label_font, fill=FONT_COLOR)
                    draw.text((value_x, y), value, font=value_font, fill=FONT_COLOR)
            
            try:
                photo_img = Image.open(photo_path).convert("RGBA").resize(
                    (photo_settings["photo_width"], photo_settings["photo_height"])
                )
                radii = [
                    photo_settings.get("photo_border_top_left", 0),
                    photo_settings.get("photo_border_top_right", 0),
                    photo_settings.get("photo_border_bottom_right", 0),
                    photo_settings.get("photo_border_bottom_left", 0)
                ]
                photo_img = round_photo(photo_img, radii)
                template.paste(photo_img, (photo_settings["photo_x"], photo_settings["photo_y"]), photo_img)
            except Exception as e:
                error = f"Error processing photo: {str(e)}"
                logger.error(error)
                return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 500
            
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            preview_name = f"gen_{timestamp}.jpg"
            preview_path = os.path.join(GENERATED_FOLDER, preview_name)
            template.save(preview_path, "JPEG", quality=95)
            
            pdf_name = f"gen_{timestamp}.pdf"
            pdf_path = os.path.join(GENERATED_FOLDER, pdf_name)
            template.save(pdf_path, "PDF", resolution=300)
            
            generated_url = url_for("static", filename=f"generated/{preview_name}")
            download_url = url_for("static", filename=f"generated/{pdf_name}")
            
            add_card_to_sheet(template, template_id)
            
            try:
                with sqlite3.connect(DB_FILE, timeout=10) as conn:
                    c = conn.cursor()
                    session.pop('edit_student_id', None)
                    c.execute("""
                        INSERT INTO students
                        (name, father_name, class_name, dob, address, phone, photo_filename, generated_filename,
                         created_at, data_hash, template_id, school_name, email)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (name, father_name, class_name, dob, address, phone, photo_stored,
                          pdf_name, datetime.now().isoformat(), data_hash, template_id, school_name,
                          session['student_email']))
                    unique_edit_id = c.lastrowid
                    success = f"ID card generated successfully. Your unique edit ID is {unique_edit_id}. Remember this to edit your card later."
                    conn.commit()
            except sqlite3.IntegrityError as e:
                error = "A student with identical information already exists." if "UNIQUE constraint failed: students.data_hash" in str(e) else f"Database error: {str(e)}"
                logger.error(f"Integrity error: {e}")
                return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 500
            except sqlite3.Error as e:
                error = f"Database error: {str(e)}"
                logger.error(error)
                return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 500
            form_data = {}
            show_fetch = True
        except Exception as e:
            error = f"Error generating ID card: {str(e)}"
            logger.error(error)
            return render_template("index.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates, show_fetch=show_fetch), 500
    
    return render_template("index.html", generated_url=generated_url, download_url=download_url,
                          form_data=form_data, success=success, error=error, templates=templates, show_fetch=show_fetch,
                          unique_edit_id=unique_edit_id, selected_template_id=selected_template_id)

@app.route("/fetch_record", methods=["POST"])
def fetch_record():
    unique_id = request.form.get("unique_id", "").strip()
    if not unique_id:
        logger.error("Unique ID is required")
        return jsonify({"success": False, "error": "Unique ID is required"}), 400
    
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT id, email FROM students WHERE id = ?", (unique_id,))
            student = c.fetchone()
            
            logger.info(f"Fetching record for unique_id: {unique_id}, found: {student}")
            
            if student:
                student_id, student_email = student
                session_email = session.get('student_email')
                
                # Handle records without email
                if not student_email:
                    logger.warning(f"Student record {student_id} has no email")
                    
                    # If user is logged in, check if we can associate this record
                    if session_email:
                        # Count user's current records
                        c.execute("SELECT COUNT(*) FROM students WHERE email = ?", (session_email,))
                        user_record_count = c.fetchone()[0]
                        
                        if user_record_count < 3:
                            # Update record with session email
                            c.execute("UPDATE students SET email = ? WHERE id = ?", (session_email, student_id))
                            conn.commit()
                            logger.info(f"Associated student {student_id} with email {session_email}")
                            edit_url = url_for('edit_student', student_id=student_id)
                            return jsonify({"success": True, "edit_url": edit_url})
                        else:
                            return jsonify({"success": False, "error": "This record has no email associated and you have reached your record limit."}), 403
                    else:
                        return jsonify({"success": False, "error": "This record has no email associated. Please login first."}), 403
                
                # Normal email comparison
                if student_email and session_email and student_email.lower() == session_email.lower():
                    edit_url = url_for('edit_student', student_id=student_id)
                    return jsonify({"success": True, "edit_url": edit_url})
                else:
                    logger.warning(f"Email mismatch - Student: {student_email}, Session: {session_email}")
                    return jsonify({"success": False, "error": "You are not authorized to edit this record"}), 403
            else:
                logger.error(f"No record found for unique ID {unique_id}")
                return jsonify({"success": False, "error": "No record found for this unique ID"}), 404
    except sqlite3.Error as e:
        logger.error(f"Error fetching record for unique ID {unique_id}: {e}")
        return jsonify({"success": False, "error": f"Error fetching record: {str(e)}"}), 500
    
@app.route("/clear_edit_session", methods=["POST"])
def clear_edit_session():
    session.pop('edit_student_id', None)
    return jsonify({"success": True})

@app.route("/edit/<int:student_id>", methods=["GET", "POST"])
def edit_student(student_id):
    generated_url = None
    download_url = None
    form_data = {}
    success = None
    error = None
    templates = get_templates()
    
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM students WHERE id = ?", (student_id,))
            student = c.fetchone()
            
            if not student:
                error = f"No student found with ID {student_id}"
                logger.error(error)
                return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                     generated_url=generated_url, download_url=download_url, success=success), 404
            
            # Handle cases where email might be missing
            student_email = student[13] if len(student) > 13 else None
            session_email = session.get('student_email')
            
            logger.info(f"Authorization check - Student ID: {student_id}")
            logger.info(f"Database email: {student_email}")
            logger.info(f"Session email: {session_email}")
            
            # If student record has no email, check if we can associate it with current session
            if not student_email:
                logger.warning(f"Student record {student_id} has no email associated")
                
                # If user is logged in, check if this is their only record without email
                if session_email:
                    # Count how many records this user has
                    c.execute("SELECT COUNT(*) FROM students WHERE email = ?", (session_email,))
                    user_record_count = c.fetchone()[0]
                    
                    # If user has less than 3 records and this record has no email, allow association
                    if user_record_count < 3:
                        # Update the record with current user's email
                        c.execute("UPDATE students SET email = ? WHERE id = ?", (session_email, student_id))
                        conn.commit()
                        student_email = session_email
                        logger.info(f"Associated student {student_id} with email {session_email}")
                    else:
                        error = "This record has no email associated and cannot be edited. Please contact administrator."
                        return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                             generated_url=generated_url, download_url=download_url, success=success), 403
                else:
                    error = "This record has no email associated. Please login and try again."
                    return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                         generated_url=generated_url, download_url=download_url, success=success), 403
            
            # Normal authorization check
            if student_email.lower() != session_email.lower():
                error = f"You are not authorized to access this record."
                logger.warning(f"Authorization failed for student {student_id}")
                return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                     generated_url=generated_url, download_url=download_url, success=success), 403
            
            # Authorization successful - populate form data
            form_data = {
                'id': student[0],
                'name': student[1],
                'father_name': student[2],
                'class_name': student[3],
                'dob': student[4],
                'address': student[5],
                'phone': student[6],
                'photo_filename': student[7],
                'generated_filename': student[8],
                'template_id': student[11],
                'school_name': student[12]
            }
            
            preview_filename = student[8].replace(".pdf", ".jpg") if student[8] else None
            preview_path = os.path.join(GENERATED_FOLDER, preview_filename) if preview_filename else None
            if preview_path and os.path.exists(preview_path):
                generated_url = url_for('static', filename=f'generated/{preview_filename}')
            else:
                generated_url = url_for('static', filename='placeholder.jpg')
            download_url = url_for('static', filename=f'generated/{student[8]}') if student[8] else None
        
    except sqlite3.Error as e:
        error = f"Error fetching student data: {str(e)}"
        logger.error(error)
        return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                             generated_url=generated_url, download_url=download_url, success=success), 500
    
    if request.method == "POST":
        try:
            with sqlite3.connect(DB_FILE, timeout=10) as conn:
                c = conn.cursor()
                c.execute("SELECT email FROM students WHERE id = ?", (student_id,))
                student_email_result = c.fetchone()
                
                if not student_email_result or not student_email_result[0]:
                    # If still no email, use session email for authorization
                    session_email = session.get('student_email')
                    if not session_email:
                        error = "You must be logged in to edit records."
                        return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                             generated_url=generated_url, download_url=download_url, success=success), 403
                    
                    # Update the record with session email
                    c.execute("UPDATE students SET email = ? WHERE id = ?", (session_email, student_id))
                    conn.commit()
                    logger.info(f"Updated student {student_id} with email {session_email}")
                else:
                    student_email = student_email_result[0]
                    session_email = session.get('student_email')
                    
                    if student_email.lower() != session_email.lower():
                        error = "You are not authorized to access this record."
                        logger.warning(f"POST Authorization failed for student {student_id}")
                        return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                             generated_url=generated_url, download_url=download_url, success=success), 403
        except sqlite3.Error as e:
            error = f"Database error: {str(e)}"
            logger.error(error)
            return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                 generated_url=generated_url, download_url=download_url, success=success), 500
        
        existing_photo_filename = form_data.get('photo_filename', '')
        template_id = int(request.form.get("template_id", 0))
        school_name = next((t['school_name'] for t in templates if t['id'] == template_id), None)
        template_path = get_template_path(template_id)
        
        if not template_path or not os.path.exists(template_path):
            error = "No template found. Please select a valid template."
            logger.error(f"Template not found for ID {template_id}")
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 400
        
        font_settings, photo_settings, card_orientation = get_template_settings(template_id)
        card_width, card_height = get_card_size(template_id)
        
        FONT_BOLD_PATH = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
        FONT_REGULAR_PATH = os.path.join(FONTS_FOLDER, font_settings["font_regular"])
        
        if not is_valid_font_file(FONT_BOLD_PATH) or not is_valid_font_file(FONT_REGULAR_PATH):
            error = f"Invalid font file: {font_settings['font_bold']} or {font_settings['font_regular']}"
            logger.error(error)
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 500
        
        # FIXED: Handle font color with backward compatibility
        font_color_input = font_settings.get("font_color")  # Legacy support
        if font_color_input is None:
            # Use label font color as default if font_color doesn't exist
            font_color_input = font_settings.get("label_font_color", [0, 0, 0])
        
        try:
            if isinstance(font_color_input, str):
                if font_color_input.startswith("#") and len(font_color_input) == 7:
                    FONT_COLOR = tuple(int(font_color_input[i:i+2], 16) for i in (1, 3, 5))
                else:
                    FONT_COLOR = tuple(map(int, font_color_input.split(",")))
            else:
                FONT_COLOR = tuple(font_color_input)
        except Exception as e:
            error = f"Invalid font color format: {str(e)}"
            logger.error(error)
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 400
        
        name = request.form.get("name", "").strip()
        father_name = request.form.get("father_name", "").strip()
        class_name = request.form.get("class_name", "").strip()
        dob = request.form.get("dob", "").strip()
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        
        form_data = {
            'id': student_id,
            'name': name,
            'father_name': father_name,
            'class_name': class_name,
            'dob': dob,
            'address': address,
            'phone': phone,
            'template_id': template_id,
            'school_name': school_name
        }
        
        is_duplicate, duplicate_message = check_duplicate_student(form_data, None, student_id)
        if is_duplicate:
            logger.warning(f"Duplicate student detected: {duplicate_message}")
            return render_template("edit.html", generated_url=generated_url,
                                 download_url=download_url, form_data=form_data,
                                 error=duplicate_message, templates=templates), 400
        
        photo_fn = None
        photo_stored = existing_photo_filename
        photo_path = os.path.join(UPLOAD_FOLDER, photo_stored) if photo_stored else None
        
        if 'photo' in request.files and request.files['photo'].filename:
            photo = request.files['photo']
            photo_fn = secure_filename(photo.filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            photo_stored = f"{timestamp}_{photo_fn}"
            photo_path = os.path.join(UPLOAD_FOLDER, photo_stored)
            try:
                photo.save(photo_path)
                crop_photo_to_aspect_ratio(photo_path)
            except Exception as e:
                error = f"Error saving photo: {str(e)}"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 500
        else:
            if not photo_stored or not os.path.exists(photo_path):
                error = "No photo provided and no existing photo found"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 400
        
        data_hash = generate_data_hash(form_data, photo_stored)
        
        try:
            template = load_template(template_path)
            template = template.resize((card_width, card_height))
            draw = ImageDraw.Draw(template)
            
            # Apply text case transformation
            text_case = font_settings.get("text_case", "normal")
            labels = ["NAME", "F.NAME", "CLASS", "D.O.B.", "MOBILE", "ADDRESS"]
            values = [
                apply_text_case(name, text_case),
                apply_text_case(father_name, text_case),
                apply_text_case(class_name, text_case),
                dob,
                phone,
                apply_text_case(address, text_case)
            ]
            
            # Draw text with adjusted settings
            label_x = font_settings["label_x"]
            value_x = font_settings["value_x"]
            start_y = font_settings["start_y"]
            line_height = font_settings["line_height"]
            max_value_width = template.width - value_x - 20
            
            for i, (label, value) in enumerate(zip(labels, values)):
                y = start_y + i * line_height
                try:
                    label_font = ImageFont.truetype(FONT_BOLD_PATH, font_settings["label_font_size"])
                except OSError as e:
                    error = f"Failed to load font {FONT_BOLD_PATH}: {str(e)}"
                    logger.error(error)
                    return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                         form_data=form_data, error=error, templates=templates), 500
                
                if label == "ADDRESS":
                    char_width = font_settings["value_font_size"] * 0.6
                    max_chars = int(max_value_width / char_width)
                    wrapped_lines = textwrap.wrap(value, width=max_chars) or [""]
                    
                    draw.text((label_x, y), f"{label}:", font=label_font, fill=FONT_COLOR)
                    value_font = load_font_dynamic(FONT_REGULAR_PATH, max(wrapped_lines, key=len, default=""), max_value_width, font_settings["value_font_size"])
                    for line in wrapped_lines:
                        draw.text((value_x, y), line, font=value_font, fill=FONT_COLOR)
                        y += line_height
                else:
                    value_font = load_font_dynamic(FONT_REGULAR_PATH, value, max_value_width, font_settings["value_font_size"])
                    draw.text((label_x, y), f"{label}:", font=label_font, fill=FONT_COLOR)
                    draw.text((value_x, y), value, font=value_font, fill=FONT_COLOR)
            
            try:
                photo_img = Image.open(photo_path).convert("RGBA").resize(
                    (photo_settings["photo_width"], photo_settings["photo_height"])
                )
                radii = [
                    photo_settings.get("photo_border_top_left", 0),
                    photo_settings.get("photo_border_top_right", 0),
                    photo_settings.get("photo_border_bottom_right", 0),
                    photo_settings.get("photo_border_bottom_left", 0)
                ]
                photo_img = round_photo(photo_img, radii)
                template.paste(photo_img, (photo_settings["photo_x"], photo_settings["photo_y"]), photo_img)
            except Exception as e:
                error = f"Error processing photo: {str(e)}"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 500
            
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            preview_name = f"gen_{timestamp}.jpg"
            preview_path = os.path.join(GENERATED_FOLDER, preview_name)
            template.save(preview_path, "JPEG", quality=95)
            
            pdf_name = f"gen_{timestamp}.pdf"
            pdf_path = os.path.join(GENERATED_FOLDER, pdf_name)
            template.save(pdf_path, "PDF", resolution=300)
            
            generated_url = url_for("static", filename=f"generated/{preview_name}")
            download_url = url_for("static", filename=f"generated/{pdf_name}")
            
            add_card_to_sheet(template, template_id)
            
            try:
                with sqlite3.connect(DB_FILE, timeout=10) as conn:
                    c = conn.cursor()
                    c.execute("SELECT photo_filename, generated_filename FROM students WHERE id = ?", (student_id,))
                    old_files = c.fetchone()
                    if old_files:
                        old_photo, old_generated = old_files
                        if old_photo and old_photo != photo_stored:
                            old_photo_path = os.path.join(UPLOAD_FOLDER, old_photo)
                            if os.path.exists(old_photo_path):
                                os.remove(old_photo_path)
                        if old_generated and old_generated != pdf_name:
                            for ext in ['.pdf', '.jpg']:
                                old_file_path = os.path.join(GENERATED_FOLDER, old_generated.replace('.pdf', ext))
                                if os.path.exists(old_file_path):
                                    os.remove(old_file_path)
                    
                    c.execute("""
                        UPDATE students
                        SET name = ?, father_name = ?, class_name = ?, dob = ?, address = ?, phone = ?,
                            photo_filename = ?, generated_filename = ?, created_at = ?, data_hash = ?,
                            template_id = ?, school_name = ?
                        WHERE id = ?
                    """, (name, father_name, class_name, dob, address, phone, photo_stored,
                          pdf_name, datetime.now().isoformat(), data_hash, template_id, school_name, student_id))
                    conn.commit()
                    success = "ID card updated successfully"
                    form_data['photo_filename'] = photo_stored
                    form_data['generated_filename'] = pdf_name
            except sqlite3.Error as e:
                error = f"Database error: {str(e)}"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 500
        
        except Exception as e:
            error = f"Error generating ID card: {str(e)}"
            logger.error(error)
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 500
    
    return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                          form_data=form_data, success=success, error=error, templates=templates)

# ================== Admin Routes ==================
@app.route("/admin", methods=["GET"])
def admin():
    if 'student_email' not in session and not session.get("admin"):
        flash('Please log in to access the admin panel.', 'error')
        return redirect(url_for('student_login'))
    
    success = request.args.get("success")
    error = request.args.get("error")
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            if session.get("admin"):
                c.execute("SELECT id, name, father_name, class_name, dob, address, phone, photo_filename, generated_filename, created_at, template_id, school_name, email FROM students ORDER BY created_at DESC")
            else:
                c.execute("SELECT id, name, father_name, class_name, dob, address, phone, photo_filename, generated_filename, created_at, template_id, school_name, email FROM students WHERE email = ? ORDER BY created_at DESC",
                         (session['student_email'],))
            rows = c.fetchall()
            templates = get_templates()
            
            available_fonts = get_available_fonts()
            current_settings = templates[0]['font_settings'] if templates else get_default_font_config()
            photo_settings = templates[0]['photo_settings'] if templates else get_default_photo_config()
            duplicate_settings = load_duplicate_config()
            
            pdf_sheets = [f for f in os.listdir(GENERATED_FOLDER) if f.endswith('.pdf') and f.startswith('sheet_template_')]
            pdf_sheets_by_template = defaultdict(list)
            for f in pdf_sheets:
                parts = f.split('_')
                if len(parts) >= 4 and parts[0] == 'sheet' and parts[1] == 'template':
                    try:
                        template_id = int(parts[2])
                        pdf_sheets_by_template[template_id].append(f)
                    except ValueError:
                        continue
            
            template_rows = {}
            for row in rows:
                template_id = row[10]
                if template_id not in template_rows:
                    template_rows[template_id] = []
                template_rows[template_id].append(row)
            
            schools_data = {}
            for row in rows:
                school_name = row[11] or "Unknown School"
                if school_name not in schools_data:
                    schools_data[school_name] = []
                schools_data[school_name].append(row)
            
            # Calculate arrangement info for each template
            template_arrangements = {}
            for template in templates:
                orientation = template.get('card_orientation', 'landscape')
                if orientation == 'landscape':
                    template_arrangements[template['id']] = "2 columns × 5 rows (10 cards/A4)"
                else:
                    template_arrangements[template['id']] = "5 columns × 2 rows (10 cards/A4)"
            
            logger.info("Admin panel loaded successfully")
            return render_template(
                "admin.html",
                rows=rows,
                templates=templates,
                template_rows=template_rows,
                available_fonts=available_fonts,
                current_settings=current_settings,
                photo_settings=photo_settings,
                duplicate_settings=duplicate_settings,
                pdf_sheets=pdf_sheets,
                pdf_sheets_by_template=pdf_sheets_by_template,
                success=success,
                error=error,
                schools_data=schools_data,
                is_admin=session.get("admin", False),
                template_arrangements=template_arrangements
            )
    except sqlite3.Error as e:
        logger.error(f"Error loading admin panel: {e}")
        return render_template(
            "admin.html",
            error=f"Error loading admin panel: {str(e)}",
            rows=[],
            templates=[],
            template_rows={},
            available_fonts=[],
            current_settings={},
            photo_settings={},
            duplicate_settings={},
            pdf_sheets=[],
            pdf_sheets_by_template=defaultdict(list),
            schools_data={},
            is_admin=session.get("admin", False),
            template_arrangements={}
        ), 500

@app.route("/upload_template", methods=["POST"])
def upload_template():
    if 'template' not in request.files or 'school_name' not in request.form:
        logger.error("Template file and school name are required")
        return redirect(url_for("admin", error="Template file and school name are required"))
    file = request.files['template']
    school_name = request.form['school_name'].strip()
    card_orientation = request.form.get('card_orientation', 'landscape')
    if file.filename == '' or not school_name:
        logger.error("No file selected or school name empty")
        return redirect(url_for("admin", error="No file selected or school name empty"))
    if file and file.filename.lower().endswith(('.pdf', '.jpg', '.jpeg', '.png')):
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        stored_filename = f"{timestamp}_{filename}"
        file_path = os.path.join(UPLOAD_FOLDER, stored_filename)
        try:
            file.save(file_path)
            template_id = add_template(os.path.join("Uploads", stored_filename), school_name, card_orientation)
            logger.info(f"Template uploaded: {stored_filename} for school: {school_name} with orientation {card_orientation}")
            return redirect(url_for("admin", success="Template uploaded successfully"))
        except Exception as e:
            logger.error(f"Error uploading template: {e}")
            return redirect(url_for("admin", error=f"Error uploading template: {str(e)}"))
    else:
        logger.error("Invalid file format for template")
        return redirect(url_for("admin", error="Invalid file format. Use PDF, JPG, or PNG"))

@app.route("/update_template_settings", methods=["POST"])
def update_template_settings_route():
    try:
        template_id = int(request.form.get("template_id"))
        
        # Retrieve current template orientation from the database
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT card_orientation FROM templates WHERE id = ?", (template_id,))
            result = c.fetchone()
            current_orientation = result[0] if result and result[0] else 'landscape'  # Fallback to landscape if not set
        
        # Get card_orientation from form, default to current_orientation
        card_orientation = request.form.get("card_orientation", current_orientation)
        
        # Define font settings with defaults based on orientation
        default_label_size = 40 if card_orientation == 'landscape' else 32
        default_value_size = 36 if card_orientation == 'landscape' else 28
        default_label_x = 50 if card_orientation == 'landscape' else 40
        default_value_x = 280 if card_orientation == 'landscape' else 200
        default_start_y = 275 if card_orientation == 'landscape' else 120
        default_line_height = 50 if card_orientation == 'landscape' else 45

        font_settings = {
            "font_bold": request.form.get("font_bold", "arialbd.ttf"),
            "font_regular": request.form.get("font_regular", "arial.ttf"),
            "label_font_size": get_form_int(request.form, "label_font_size", default_label_size),
            "value_font_size": get_form_int(request.form, "value_font_size", default_value_size),
            "label_x": get_form_int(request.form, "label_x", default_label_x),
            "value_x": get_form_int(request.form, "value_x", default_value_x),
            "start_y": get_form_int(request.form, "start_y", default_start_y),
            "line_height": get_form_int(request.form, "line_height", default_line_height),
            "text_case": request.form.get("text_case", "normal")
        }
        
        # Handle font colors for label and value separately
        label_font_color = request.form.get("label_font_color", "0,0,0")
        value_font_color = request.form.get("value_font_color", "0,0,0")
        
        try:
            if label_font_color.startswith("#") and len(label_font_color) == 7:
                font_settings["label_font_color"] = [int(label_font_color[i:i+2], 16) for i in (1, 3, 5)]
            else:
                font_settings["label_font_color"] = [int(x.strip()) for x in label_font_color.split(",")[:3]]
        except Exception as e:
            logger.warning(f"Invalid label font color format: {label_font_color}, defaulting to black")
            font_settings["label_font_color"] = [0, 0, 0]
        
        try:
            if value_font_color.startswith("#") and len(value_font_color) == 7:
                font_settings["value_font_color"] = [int(value_font_color[i:i+2], 16) for i in (1, 3, 5)]
            else:
                font_settings["value_font_color"] = [int(x.strip()) for x in value_font_color.split(",")[:3]]
        except Exception as e:
            logger.warning(f"Invalid value font color format: {value_font_color}, defaulting to black")
            font_settings["value_font_color"] = [0, 0, 0]
        
        # Define photo settings with defaults based on orientation
        default_photo_x = 725 if card_orientation == 'landscape' else 100
        default_photo_y = 200 if card_orientation == 'landscape' else 400
        default_photo_width = 260 if card_orientation == 'landscape' else 216
        default_photo_height = 313 if card_orientation == 'landscape' else 180

        photo_settings = {
            "photo_x": get_form_int(request.form, "photo_x", default_photo_x),
            "photo_y": get_form_int(request.form, "photo_y", default_photo_y),
            "photo_width": get_form_int(request.form, "photo_width", default_photo_width),
            "photo_height": get_form_int(request.form, "photo_height", default_photo_height),
            "photo_border_top_left": get_form_int(request.form, "photo_border_top_left", 0),
            "photo_border_top_right": get_form_int(request.form, "photo_border_top_right", 0),
            "photo_border_bottom_right": get_form_int(request.form, "photo_border_bottom_right", 0),
            "photo_border_bottom_left": get_form_int(request.form, "photo_border_bottom_left", 0)
        }
        
        # Merge with default settings
        default_font = get_default_font_config()
        default_photo = get_default_photo_config()
        font_settings = {**default_font, **font_settings}
        photo_settings = {**default_photo, **photo_settings}
        
        # Adjust settings for orientation
        font_settings = get_font_settings_for_orientation(template_id, font_settings)
        photo_settings = get_photo_settings_for_orientation(template_id, photo_settings)
        
        # Update template settings in the database
        update_template_settings(template_id, font_settings, photo_settings, card_orientation)
        
        # Log arrangement info
        arrangement = "2 columns × 5 rows (10 cards/A4)" if card_orientation == 'landscape' else "5 columns × 2 rows (10 cards/A4)"
        logger.info(f"Updated template {template_id} with {card_orientation} orientation: {arrangement}")
        return redirect(url_for("admin", success=f"Template settings updated successfully - {arrangement}"))
        
    except ValueError as e:
        logger.error(f"Invalid input in template settings: {e}")
        return redirect(url_for("admin", error=f"Invalid input in template settings: {str(e)}"))
    except sqlite3.Error as e:
        logger.error(f"Database error updating template settings: {e}")
        return redirect(url_for("admin", error=f"Error updating template settings: {str(e)}"))
    except Exception as e:
        logger.error(f"Unexpected error updating template settings: {e}")
        return redirect(url_for("admin", error=f"Unexpected error updating template settings: {str(e)}"))

@app.route("/upload_font", methods=["POST"])
def upload_font():
    if 'font' not in request.files:
        logger.error("No font file uploaded")
        return redirect(url_for("admin", error="No font file uploaded"))
    file = request.files['font']
    if file.filename == '':
        logger.error("No font file selected")
        return redirect(url_for("admin", error="No font file selected"))
    if file and file.filename.lower().endswith(('.ttf', '.otf')):
        filename = secure_filename(file.filename)
        file_path = os.path.join(FONTS_FOLDER, filename)
        try:
            file.save(file_path)
            if is_valid_font_file(file_path):
                logger.info(f"Font uploaded: {filename}")
                return redirect(url_for("admin", success="Font uploaded successfully"))
            else:
                os.remove(file_path)
                logger.error(f"Invalid font file: {filename}")
                return redirect(url_for("admin", error="Invalid font file"))
        except Exception as e:
            logger.error(f"Error uploading font: {e}")
            return redirect(url_for("admin", error=f"Error uploading font: {str(e)}"))
    else:
        logger.error("Invalid font file format")
        return redirect(url_for("admin", error="Invalid file format. Use TTF or OTF"))

@app.route("/update_font", methods=["POST"])
def update_font():
    try:
        templates = get_templates()
        if templates:
            template_id = templates[0]['id']
            font_settings = {
                "font_bold": request.form.get("font_bold", "arialbd.ttf"),
                "font_regular": request.form.get("font_regular", "arial.ttf"),
                "font_color": [int(x) for x in request.form.get("font_color", "0,0,0").split(",")[:3]],
                "label_font_size": int(request.form.get("label_font_size", 40)),
                "value_font_size": int(request.form.get("value_font_size", 36)),
                "label_x": int(request.form.get("label_x", 50)),
                "value_x": int(request.form.get("value_x", 280)),
                "start_y": int(request.form.get("start_y", 275)),
                "line_height": int(request.form.get("line_height", 50)),
                "text_case": request.form.get("text_case", "normal")  # Added text case
            }
            update_template_settings(template_id, font_settings=font_settings)
            return redirect(url_for("admin", success="Font settings updated successfully"))
        else:
            return redirect(url_for("admin", error="No templates available"))
    except Exception as e:
        logger.error(f"Error in legacy font update: {e}")
        return redirect(url_for("admin", error="Please use template-specific settings"))

@app.route("/update_photo_position", methods=["POST"])
def update_photo_position():
    try:
        templates = get_templates()
        if templates:
            template_id = templates[0]['id']
            photo_settings = {
                "photo_x": int(request.form.get("photo_x", 725)),
                "photo_y": int(request.form.get("photo_y", 200)),
                "photo_width": int(request.form.get("photo_width", 260)),
                "photo_height": int(request.form.get("photo_height", 313))
            }
            update_template_settings(template_id, photo_settings=photo_settings)
            return redirect(url_for("admin", success="Photo settings updated successfully"))
        else:
            return redirect(url_for("admin", error="No templates available"))
    except Exception as e:
        logger.error(f"Error in legacy photo update: {e}")
        return redirect(url_for("admin", error="Please use template-specific settings"))

@app.route("/update_duplicate_settings", methods=["POST"])
def update_duplicate_settings():
    try:
        check_phone = 'check_phone' in request.form
        check_name_class = 'check_name_class' in request.form
        config = {
            "check_phone": check_phone,
            "check_name_class": check_name_class
        }
        save_duplicate_config(config)
        logger.info("Duplicate settings updated successfully")
        return redirect(url_for("admin", success="Duplicate settings updated successfully"))
    except Exception as e:
        logger.error(f"Error updating duplicate settings: {e}")
        return redirect(url_for("admin", error=f"Error updating duplicate settings: {str(e)}"))

@app.route("/delete_all", methods=["POST"])
def delete_all():
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM students")
            c.execute("DELETE FROM templates")
            conn.commit()
        for folder in [UPLOAD_FOLDER, GENERATED_FOLDER]:
            for file in os.listdir(folder):
                file_path = os.path.join(folder, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
        if os.path.exists(TEMPLATES_CONFIG):
            os.remove(TEMPLATES_CONFIG)
        logger.info("All data and files deleted successfully")
        return redirect(url_for("admin", success="All data and files deleted successfully"))
    except sqlite3.Error as e:
        logger.error(f"Error deleting all data: {e}")
        return redirect(url_for("admin", error=f"Error deleting data: {str(e)}"))

@app.route("/delete_student/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT photo_filename, generated_filename FROM students WHERE id = ?", (student_id,))
            files = c.fetchone()
            if files:
                photo_filename, generated_filename = files
                if photo_filename:
                    photo_path = os.path.join(UPLOAD_FOLDER, photo_filename)
                    if os.path.exists(photo_path):
                        os.remove(photo_path)
                        logger.info(f"Deleted photo: {photo_path}")
                if generated_filename:
                    for ext in ['.pdf', '.jpg']:
                        generated_path = os.path.join(GENERATED_FOLDER, generated_filename.replace('.pdf', ext))
                        if os.path.exists(generated_path):
                            os.remove(generated_path)
                            logger.info(f"Deleted generated file: {generated_path}")
            c.execute("DELETE FROM students WHERE id = ?", (student_id,))
            conn.commit()
            logger.info(f"Deleted student ID {student_id}")
            return redirect(url_for("admin", success="Student deleted successfully"))
    except sqlite3.Error as e:
        logger.error(f"Error deleting student {student_id}: {e}")
        return redirect(url_for("admin", error=f"Error deleting student: {str(e)}"))

@app.route("/delete_pdf/<path:filename>", methods=["POST"])
def delete_pdf(filename):
    pdf_path = os.path.join(GENERATED_FOLDER, filename)
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
        logger.info(f"Deleted PDF: {filename}")
        return redirect(url_for("admin", success="PDF deleted successfully"))
    else:
        return redirect(url_for("admin", error="PDF not found"))

@app.route("/export_csv")
def export_csv():
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT id, name, father_name, class_name, dob, address, phone, photo_filename, generated_filename, created_at, template_id, school_name, email FROM students")
            rows = c.fetchall()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "Name", "Father's Name", "Class", "DOB", "Address", "Phone", "Photo Filename", "Generated Filename", "Created At", "Template ID", "School Name", "Email"])
            writer.writerows(rows)
            logger.info("Exported student data to CSV")
            return Response(
                output.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment;filename=students_export.csv"}
            )
    except sqlite3.Error as e:
        logger.error(f"Error exporting CSV: {e}")
        return redirect(url_for("admin", error=f"Error exporting CSV: {str(e)}"))

@app.route("/download_template/<int:template_id>")
def download_template(template_id):
    template_path = get_template_path(template_id)
    if template_path and os.path.exists(template_path):
        logger.info(f"Downloading template ID {template_id}")
        return send_file(template_path, as_attachment=True)
    else:
        logger.error(f"No template available for ID {template_id}")
        return redirect(url_for("admin", error="No template available for download"))

@app.route("/remove_template/<int:template_id>", methods=["POST"])
def remove_template(template_id):
    try:
        template_path = get_template_path(template_id)
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM templates WHERE id = ?", (template_id,))
            c.execute("UPDATE students SET template_id = NULL, school_name = NULL WHERE template_id = ?", (template_id,))
            conn.commit()
        if template_path and os.path.exists(template_path):
            os.remove(template_path)
            logger.info(f"Removed template file: {template_path}")
        logger.info(f"Removed template ID {template_id}")
        return redirect(url_for("admin", success="Template removed successfully"))
    except sqlite3.Error as e:
        logger.error(f"Error removing template {template_id}: {e}")
        return redirect(url_for("admin", error=f"Error removing template: {str(e)}"))

@app.route("/admin_preview_card", methods=["POST"])
def admin_preview_card():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data received"}), 400
            
        template_id = data.get("template_id")
        font_settings = data.get("font_settings", {})
        photo_settings = data.get("photo_settings", {})
        card_orientation = data.get("card_orientation", "landscape")

        logger.info(f"Generating admin preview for template {template_id}")

        template_path = get_template_path(template_id)
        if not template_path or not os.path.exists(template_path):
            return jsonify({"success": False, "error": "Template not found"}), 404

        # Get default settings and merge with provided settings
        default_font = get_default_font_config()
        default_photo = get_default_photo_config()
        
        # Merge settings
        complete_font_settings = {**default_font, **font_settings}
        complete_photo_settings = {**default_photo, **photo_settings}

        # Generate the preview
        card_width, card_height = get_card_size(template_id)
        
        try:
            template = load_template(template_path)
            template = template.resize((card_width, card_height))
            draw = ImageDraw.Draw(template)
            
            # Load fonts with fallbacks - FIXED: Import ImageFont at the top of the file
            FONT_BOLD_PATH = os.path.join(FONTS_FOLDER, complete_font_settings["font_bold"])
            FONT_REGULAR_PATH = os.path.join(FONTS_FOLDER, complete_font_settings["font_regular"])
            
            # Check if fonts exist, use fallbacks if not
            if not os.path.exists(FONT_BOLD_PATH):
                logger.warning(f"Bold font not found: {FONT_BOLD_PATH}, using fallback")
                FONT_BOLD_PATH = os.path.join(FONTS_FOLDER, "arialbd.ttf")
            if not os.path.exists(FONT_REGULAR_PATH):
                logger.warning(f"Regular font not found: {FONT_REGULAR_PATH}, using fallback")
                FONT_REGULAR_PATH = os.path.join(FONTS_FOLDER, "arial.ttf")
            
            # Handle font colors safely
            try:
                label_font_color = tuple(complete_font_settings.get("label_font_color", [0, 0, 0]))
            except Exception as e:
                logger.warning(f"Error parsing label font color: {e}, using default black")
                label_font_color = (0, 0, 0)
            
            try:
                value_font_color = tuple(complete_font_settings.get("value_font_color", [0, 0, 0]))
            except Exception as e:
                logger.warning(f"Error parsing value font color: {e}, using default black")
                value_font_color = (0, 0, 0)
            
            # Apply text case transformation to sample values
            text_case = complete_font_settings.get("text_case", "normal")
            labels = ["NAME", "F.NAME", "CLASS", "D.O.B.", "MOBILE", "ADDRESS"]
            sample_values = ["John Doe", "Richard Roe", "10th Grade", "01-01-2000", "9999999999", "123 Main Street"]
            values = [apply_text_case(value, text_case) for value in sample_values]
            
            label_x = complete_font_settings["label_x"]
            value_x = complete_font_settings["value_x"]
            start_y = complete_font_settings["start_y"]
            line_height = complete_font_settings["line_height"]
            max_value_width = template.width - value_x - 20

            # Draw text on template
            for i, (label, value) in enumerate(zip(labels, values)):
                y = start_y + i * line_height
                
                # Load label font
                try:
                    label_font = ImageFont.truetype(FONT_BOLD_PATH, complete_font_settings["label_font_size"])
                except Exception as e:
                    logger.warning(f"Failed to load bold font {FONT_BOLD_PATH}: {e}, using fallback")
                    label_font = ImageFont.truetype(os.path.join(FONTS_FOLDER, "arialbd.ttf"), complete_font_settings["label_font_size"])
                
                # Load value font with dynamic sizing
                value_font = load_font_dynamic(FONT_REGULAR_PATH, value, max_value_width, complete_font_settings["value_font_size"])
                
                # Draw label and value
                draw.text((label_x, y), f"{label}:", font=label_font, fill=label_font_color)
                draw.text((value_x, y), value, font=value_font, fill=value_font_color)

            # Add photo placeholder
            photo_x = complete_photo_settings["photo_x"]
            photo_y = complete_photo_settings["photo_y"]
            photo_width = complete_photo_settings["photo_width"]
            photo_height = complete_photo_settings["photo_height"]
            
            try:
                placeholder_photo = PLACEHOLDER_PATH
                if not os.path.exists(placeholder_photo):
                    # Create a simple placeholder if it doesn't exist
                    placeholder_img = Image.new("RGB", (100, 100), color="gray")
                    placeholder_img.save(placeholder_photo)
                
                photo_img = Image.open(placeholder_photo).convert("RGBA").resize((photo_width, photo_height))
                radii = [
                    complete_photo_settings.get("photo_border_top_left", 0),
                    complete_photo_settings.get("photo_border_top_right", 0),
                    complete_photo_settings.get("photo_border_bottom_right", 0),
                    complete_photo_settings.get("photo_border_bottom_left", 0)
                ]
                photo_img = round_photo(photo_img, radii)
                template.paste(photo_img, (photo_x, photo_y), photo_img)
            except Exception as photo_error:
                logger.warning(f"Error adding photo to preview: {photo_error}")

            # Save preview
            preview_name = f"admin_preview_{template_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
            preview_path = os.path.join(GENERATED_FOLDER, preview_name)
            
            # Ensure the directory exists
            os.makedirs(os.path.dirname(preview_path), exist_ok=True)
            
            template.save(preview_path, "JPEG", quality=95)
            preview_url = url_for("static", filename=f"generated/{preview_name}")

            logger.info(f"Preview generated successfully: {preview_path}")
            return jsonify({"success": True, "preview_url": preview_url})
            
        except Exception as template_error:
            logger.error(f"Error processing template: {template_error}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return jsonify({"success": False, "error": f"Template processing error: {str(template_error)}"}), 500

    except Exception as e:
        logger.error(f"Error in admin_preview_card: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500
    
@app.route("/force_generate_pdf/<int:template_id>", methods=["POST"])
def force_generate_pdf(template_id):
    try:
        sheet_path = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}.png")
        positions_file = os.path.join(GENERATED_FOLDER, f"positions_template_{template_id}.txt")
        
        if not os.path.exists(sheet_path):
            logger.error(f"No sheet found for template {template_id}")
            return redirect(url_for("admin", error=f"No sheet found for template {template_id}"))
        
        sheet = Image.open(sheet_path)
        positions = []
        if os.path.exists(positions_file):
            with open(positions_file, "r") as f:
                positions = [tuple(map(int, p.split(','))) for p in f.read().split(';') if p]
        
        orientation = get_template_orientation(template_id)
        card_count = len(positions)
        
        # Generate descriptive PDF name
        if orientation == 'landscape':
            arrangement = "2x5_landscape"
            arrangement_desc = "2 columns × 5 rows (Landscape)"
        else:
            arrangement = "5x2_portrait" 
            arrangement_desc = "5 columns × 2 rows (Portrait)"
            
        pdf_name = f"ID_Cards_{arrangement}_{card_count}_cards_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
        pdf_path = os.path.join(GENERATED_FOLDER, pdf_name)
        
        # Save as PDF
        sheet.save(pdf_path, "PDF", resolution=DPI)
        
        logger.info(f"Generated PDF: {arrangement_desc} with {card_count} cards")
        
        # Clean up temporary files
        if os.path.exists(sheet_path):
            os.remove(sheet_path)
        if os.path.exists(positions_file):
            os.remove(positions_file)
            
        success_msg = f"PDF generated: {arrangement_desc} with {card_count} cards - {pdf_name}"
        return redirect(url_for("admin", success=success_msg))
        
    except Exception as e:
        logger.error(f"Error forcing PDF generation for template {template_id}: {e}")
        return redirect(url_for("admin", error=f"Error generating PDF: {str(e)}"))

@app.route("/debug_session")
def debug_session():
    if not session.get("student_email") and not session.get("admin"):
        return jsonify({"error": "Not logged in"}), 401
    
    return jsonify({
        "student_email": session.get("student_email"),
        "student_school_name": session.get("student_school_name"),
        "admin": session.get("admin"),
        "all_session_keys": list(session.keys())
    })

@app.route("/debug_template/<int:template_id>")
def debug_template(template_id):
    """Debug route to check template and settings"""
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        template_path = get_template_path(template_id)
        font_settings, photo_settings, orientation = get_template_settings(template_id)
        
        # Check if template file exists and is readable
        template_exists = os.path.exists(template_path) if template_path else False
        template_readable = False
        template_size = "N/A"
        
        if template_exists:
            try:
                with Image.open(template_path) as img:
                    template_readable = True
                    template_size = f"{img.size[0]}x{img.size[1]}"
            except Exception as e:
                template_readable = False
        
        # Check fonts
        available_fonts = get_available_fonts()
        font_bold_exists = os.path.exists(os.path.join(FONTS_FOLDER, font_settings["font_bold"]))
        font_regular_exists = os.path.exists(os.path.join(FONTS_FOLDER, font_settings["font_regular"]))
        
        return jsonify({
            "template_id": template_id,
            "template_path": template_path,
            "template_exists": template_exists,
            "template_readable": template_readable,
            "template_size": template_size,
            "orientation": orientation,
            "font_settings": font_settings,
            "photo_settings": photo_settings,
            "fonts_folder_exists": os.path.exists(FONTS_FOLDER),
            "available_fonts": available_fonts,
            "font_bold_exists": font_bold_exists,
            "font_regular_exists": font_regular_exists,
            "generated_folder_exists": os.path.exists(GENERATED_FOLDER),
            "upload_folder_exists": os.path.exists(UPLOAD_FOLDER)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def verify_fonts_available():
    """Verify that required fonts are available"""
    required_fonts = ["arial.ttf", "arialbd.ttf"]
    missing_fonts = []
    
    for font in required_fonts:
        font_path = os.path.join(FONTS_FOLDER, font)
        if not os.path.exists(font_path):
            missing_fonts.append(font)
    
    if missing_fonts:
        logger.warning(f"Missing fonts: {missing_fonts}")
        # You might want to download or create default fonts here
        return False
    
    # Test if fonts can be loaded
    for font in required_fonts:
        font_path = os.path.join(FONTS_FOLDER, font)
        try:
            ImageFont.truetype(font_path, 12)
        except Exception as e:
            logger.error(f"Font {font} cannot be loaded: {e}")
            return False
    
    logger.info("All required fonts are available and loadable")
    return True

# --------------------------------------------------------------
#  BULK GENERATE – FULLY SUPPORTS photo_path COLUMN
# --------------------------------------------------------------
@app.route("/bulk_generate", methods=["POST"])
def bulk_generate():
    if not session.get("admin"):
        return redirect(url_for("login"))

    if 'excel_file' not in request.files:
        return redirect(url_for("admin", error="No file uploaded"))

    file = request.files['excel_file']
    template_id = request.form.get("template_id")
    if not template_id:
        return redirect(url_for("admin", error="No template selected"))
    if file.filename == '':
        return redirect(url_for("admin", error="No file selected"))

    try:
        # ---------- READ EXCEL / CSV ----------
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file, engine='openpyxl')

        required_cols = ['name', 'father_name', 'class_name', 'dob', 'address', 'phone']
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            return redirect(url_for("admin", error=f"Missing columns: {', '.join(missing)}"))

        success_count = 0
        error_count   = 0
        errors        = []

        # ---------- TEMPLATE SETTINGS ----------
        font_settings, photo_settings, orientation = get_template_settings(int(template_id))
        template_path = get_template_path(int(template_id))
        if not template_path or not os.path.exists(template_path):
            return redirect(url_for("admin", error="Template file not found"))

        card_width, card_height = get_card_size(int(template_id))

        # ---------- PROCESS EACH ROW ----------
        for idx, row in df.iterrows():
            try:
                # ----- Basic fields -----
                name        = str(row['name']).strip()
                father_name = str(row.get('father_name', '')).strip()
                class_name  = str(row['class_name']).strip()
                dob         = str(row.get('dob', '')).strip()
                address     = str(row.get('address', '')).strip()
                phone       = str(row.get('phone', '')).strip()

                if not name or not class_name:
                    continue  # skip empty rows

                form_data = {
                    'name': name, 'father_name': father_name, 'class_name': class_name,
                    'dob': dob, 'address': address, 'phone': phone
                }

                # ----- Duplicate check -----
                is_dup, msg = check_duplicate_student(form_data)
                if is_dup:
                    errors.append(f"Row {idx+2}: {msg}")
                    error_count += 1
                    continue

                # ----- PHOTO PATH HANDLING -----
                photo_path = PLACEHOLDER_PATH          # default
                used_photo_filename = "placeholder.jpg"

                raw_photo = row.get('photo_path') or row.get('photo') or row.get('photo_url')
                if pd.notna(raw_photo):
                    candidate = str(raw_photo).strip()
                    # support absolute or relative paths
                    full_path = candidate if os.path.isabs(candidate) else os.path.join(APP_ROOT, candidate)
                    if os.path.isfile(full_path):
                        photo_path = full_path
                        used_photo_filename = os.path.basename(full_path)
                    else:
                        errors.append(f"Row {idx+2}: Photo not found → {candidate}")
                        error_count += 1

                # ----- GENERATE CARD -----
                template = load_template(template_path).resize((card_width, card_height))
                draw = ImageDraw.Draw(template)

                # ----- TEXT -----
                text_case = font_settings.get("text_case", "normal")
                labels = ["NAME", "F.NAME", "CLASS", "D.O.B.", "MOBILE", "ADDRESS"]
                values = [
                    apply_text_case(name, text_case),
                    apply_text_case(father_name, text_case),
                    apply_text_case(class_name, text_case),
                    dob,
                    phone,
                    apply_text_case(address, text_case)
                ]

                # FIXED: Handle font color with backward compatibility
                font_color_input = font_settings.get("font_color")  # Legacy support
                if font_color_input is None:
                    # Use label font color as default if font_color doesn't exist
                    font_color_input = font_settings.get("label_font_color", [0, 0, 0])
                
                try:
                    if isinstance(font_color_input, str):
                        if font_color_input.startswith("#") and len(font_color_input) == 7:
                            FONT_COLOR = tuple(int(font_color_input[i:i+2], 16) for i in (1, 3, 5))
                        else:
                            FONT_COLOR = tuple(map(int, font_color_input.split(",")))
                    else:
                        FONT_COLOR = tuple(font_color_input)
                except Exception as e:
                    # Fallback to black
                    FONT_COLOR = (0, 0, 0)

                label_x      = font_settings["label_x"]
                value_x      = font_settings["value_x"]
                start_y      = font_settings["start_y"]
                line_height  = font_settings["line_height"]
                max_val_w    = template.width - value_x - 20

                for i, (lbl, val) in enumerate(zip(labels, values)):
                    y = start_y + i * line_height
                    lbl_font = ImageFont.truetype(
                        os.path.join(FONTS_FOLDER, font_settings["font_bold"]),
                        font_settings["label_font_size"]
                    )
                    if lbl == "ADDRESS":
                        char_w   = font_settings["value_font_size"] * 0.6
                        max_chars = int(max_val_w / char_w)
                        wrapped   = textwrap.wrap(val, width=max_chars) or [""]
                        val_font  = load_font_dynamic(
                            os.path.join(FONTS_FOLDER, font_settings["font_regular"]),
                            max(wrapped, key=len, default=""), max_val_w,
                            font_settings["value_font_size"]
                        )
                        draw.text((label_x, y), f"{lbl}:", font=lbl_font, fill=FONT_COLOR)
                        for line in wrapped:
                            draw.text((value_x, y), line, font=val_font, fill=FONT_COLOR)
                            y += line_height
                    else:
                        val_font = load_font_dynamic(
                            os.path.join(FONTS_FOLDER, font_settings["font_regular"]),
                            val, max_val_w, font_settings["value_font_size"]
                        )
                        draw.text((label_x, y), f"{lbl}:", font=lbl_font, fill=FONT_COLOR)
                        draw.text((value_x, y), val, font=val_font, fill=FONT_COLOR)

                # ----- PHOTO -----
                try:
                    ph = Image.open(photo_path).convert("RGBA").resize(
                        (photo_settings["photo_width"], photo_settings["photo_height"])
                    )
                    radii = [
                        photo_settings.get("photo_border_top_left", 0),
                        photo_settings.get("photo_border_top_right", 0),
                        photo_settings.get("photo_border_bottom_right", 0),
                        photo_settings.get("photo_border_bottom_left", 0)
                    ]
                    ph = round_photo(ph, radii)
                    template.paste(ph, (photo_settings["photo_x"], photo_settings["photo_y"]), ph)
                except Exception as e_ph:
                    errors.append(f"Row {idx+2}: Photo error → {e_ph}")
                    error_count += 1
                    # fallback to placeholder
                    ph = Image.open(PLACEHOLDER_PATH).convert("RGBA").resize(
                        (photo_settings["photo_width"], photo_settings["photo_height"])
                    )
                    ph = round_photo(ph, radii)
                    template.paste(ph, (photo_settings["photo_x"], photo_settings["photo_y"]), ph)

                # ----- SAVE FILES -----
                ts      = datetime.now().strftime("%Y%m%d%H%M%S%f")
                pdf_name = f"bulk_{ts}_{idx}.pdf"
                jpg_name = f"bulk_{ts}_{idx}.jpg"
                pdf_path = os.path.join(GENERATED_FOLDER, pdf_name)
                jpg_path = os.path.join(GENERATED_FOLDER, jpg_name)

                template.save(pdf_path, "PDF", resolution=300)
                template.save(jpg_path, "JPEG", quality=95)

                # ----- ADD TO PRINT SHEET -----
                add_card_to_sheet(template, int(template_id))

                # ----- DATABASE INSERT -----
                data_hash   = generate_data_hash(form_data, used_photo_filename)
                school_name = next(t['school_name'] for t in get_templates() if t['id'] == int(template_id))

                with sqlite3.connect(DB_FILE) as conn:
                    c = conn.cursor()
                    c.execute("""
                        INSERT INTO students
                        (name, father_name, class_name, dob, address, phone,
                         photo_filename, generated_filename, created_at,
                         data_hash, template_id, school_name, email)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (name, father_name, class_name, dob, address, phone,
                          used_photo_filename, pdf_name, datetime.now().isoformat(),
                          data_hash, template_id, school_name, None))
                    conn.commit()

                success_count += 1

            except Exception as e:
                errors.append(f"Row {idx+2}: {str(e)}")
                error_count += 1
                continue

        # ---------- FINAL MESSAGE ----------
        msg = f"Bulk generation complete: {success_count} success, {error_count} failed."
        if errors:
            msg += f" | Errors: {' | '.join(errors[:5])}..."
        return redirect(url_for("admin", success=msg))

    except Exception as e:
        return redirect(url_for("admin", error=f"Bulk error: {str(e)}"))

# Register blueprint
app.register_blueprint(student_bp, url_prefix='/student')

# In your initialization section, after migrate_database():
with app.app_context():
    init_db()
    migrate_database()
    migrate_template_font_colors()
    verify_fonts_available()  # Add this line

if __name__ == "__main__":
    app.run(debug=True, threaded=False, processes=1)