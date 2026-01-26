from dotenv import load_dotenv
load_dotenv()  # Load environment variables BEFORE any other imports

from flask import Flask, render_template, request, url_for, Response, redirect, session, send_file, jsonify, flash, Blueprint
from PIL import Image, ImageDraw, ImageFont,ImageOps
import os
import json
from flask_login import current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime,timezone, UTC
import hashlib
import logging
import re
import smtplib
import socket  # <--- Make sure this is imported at the top
import requests
from email.mime.text import MIMEText
import random
import string
from collections import defaultdict
import textwrap
import pandas as pd
import tempfile
import glob
import io
from io import BytesIO
import base64
import uuid
from concurrent.futures import ThreadPoolExecutor
import time
from flask_wtf.csrf import CSRFProtect 
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
# Ensure fitz is available (it was used in load_template)
import fitz  # PyMuPDF
import qrcode
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from qrcode.image.pil import PilImage
from qrcode.image.styles.moduledrawers import SquareModuleDrawer, RoundedModuleDrawer, CircleModuleDrawer
from sqlalchemy import text, inspect

import numpy as np
from corel_routes import corel_bp
from editor_routes import editor_bp
from utils import (
    UPLOAD_FOLDER, GENERATED_FOLDER, FONTS_FOLDER, PLACEHOLDER_PATH,TEMPLATES_CONFIG, 
    DUPLICATE_CONFIG_PATH,A4_WIDTH_PX, A4_HEIGHT_PX,A4_LANDSCAPE_WIDTH_PX, A4_LANDSCAPE_HEIGHT_PX,DPI,
    STATIC_DIR,
    get_template_settings, get_template_path, get_card_size, apply_text_case,
    get_default_font_config, get_default_photo_config, get_default_qr_config,
    get_photo_settings_for_orientation, get_font_settings_for_orientation,
    get_template_orientation, load_template, load_template_smart, round_photo, is_valid_font_file,
    get_available_fonts, load_font_dynamic, generate_qr_code, generate_data_hash,process_text_for_drawing
    ,trim_transparent_edges, force_rgb
)
from cloudinary_config import upload_image
from models import db, Student, Template, TemplateField,ActivityLog
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded
# Initialize Thread Executor (1 worker prevents memory overload)
executor = ThreadPoolExecutor(max_workers=1)

# In-memory dictionary to track job progress
# Structure: { 'task_id': { 'state': 'PENDING', 'current': 0, 'total': 0, 'status': '' } }
jobs = {}

import logging
from logging.handlers import RotatingFileHandler
import warnings
warnings.filterwarnings("ignore", message="SymbolDatabase.GetPrototype() is deprecated")

try:
    # We rename 'remove' to 'remove_bg' so it doesn't conflict with os.remove()
    from rembg import remove as remove_bg
except ImportError:
    # If the library is missing, set to None so the app doesn't crash
    remove_bg = None
    print("Warning: 'rembg' library not installed. Background removal features will be disabled.")


# ================== App Config ==================
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "fallback_dev_key_if_env_fails")

# Token Serializer
serializer = URLSafeTimedSerializer(app.secret_key)

# Initialize Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["1200 per day", "100 per hour"], # Global default
    storage_uri="memory://" # Store limits in memory
)

# Enable CSRF Protection globally
csrf = CSRFProtect(app)

if not app.debug:
    # Create logs directory if it doesn't exist
    if not os.path.exists('logs'):
        os.mkdir('logs')
        
    file_handler = RotatingFileHandler('logs/app.log', maxBytes=10240, backupCount=10)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    
    app.logger.setLevel(logging.INFO)
    app.logger.info('ID Card Generator Startup')

# ================== Rate Limit Exemption ==================
@limiter.request_filter
def exempt_admins():
    """
    Exempt logged-in admins from ALL rate limits.
    This allows the admin to use the live preview sliders 
    and bulk tools without getting blocked.
    """
    # Check if the 'admin' key exists in the session and is True
    return session.get("admin") is True

# SQLAlchemy configuration
import os

DATABASE_URL = os.getenv("DATABASE_URL")

# Fix old Railway / Heroku postgres:// URLs
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Check Railway Variables.")

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

db.init_app(app)





@app.errorhandler(RateLimitExceeded)
def handle_rate_limit_error(e):
    """
    Handles Rate Limit errors gracefully.
    Returns JSON for API calls, Flash messages for Browser navigation.
    """
    # Log the security event
    logger.warning(f"Rate limit exceeded: {e.description} - IP: {request.remote_addr}")

    # If it's an API call (AJAX) or a fetch request, return JSON
    if request.is_json or request.path.startswith('/api'):
        return jsonify({
            "success": False, 
            "error": f"Rate limit exceeded: {e.description}"
        }), 429

    # Otherwise, show a nice flash message to the user
    flash(f"⚠️ Too many attempts. Please wait a bit before trying again. ({e.description})", "error")
    
    # Redirect back to the page they came from (e.g., the login page)
    # If no referrer, go to the landing page
    return redirect(request.referrer or url_for('landing'))

# Add the rgb_to_hex template filter
@app.template_filter('rgb_to_hex')
def rgb_to_hex(rgb_list):
    """
    Jinja filter to convert RGB list [r, g, b] to hex #RRGGBB format.
    Clamps values to 0-255 and falls back to #000000 for invalid input.
    """
    if isinstance(rgb_list, list) and len(rgb_list) == 3:
        try:
            clamped = [max(0, min(255, int(c))) for c in rgb_list]
            return '#{:02x}{:02x}{:02x}'.format(*clamped)
        except (ValueError, TypeError):
            pass
    return '#000000'

logger = logging.getLogger(__name__)

# ================== Email Config ==================
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")  # Change in production
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")  # MUST be a pbkdf2:sha256 hash

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
  


# ================== Database Initialization ==================
def init_db():
    """Initialize database with SQLAlchemy"""
    try:
        db.create_all()
        logger.info("Database initialized successfully with SQLAlchemy")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise

def check_deadline_passed(template_id):
    """
    Returns True if deadline has passed, False otherwise.
    Uses local server time for comparison.
    """
    if not template_id:
        return False, None
        
    try:
        template = db.session.get(Template, int(template_id))
        
        # If no deadline is set in DB, return False (Open indefinitely)
        if not template or not template.deadline:
            return False, None

        # Get current time (Local System Time)
        now = datetime.now()
        deadline = template.deadline

        # Debugging Logs
        logger.info(f"--- DEADLINE CHECK for Template {template_id} ---")
        logger.info(f"Current Time: {now}")
        logger.info(f"Deadline:     {deadline}")

        if now > deadline:
            logger.warning("Deadline has PASSED.")
            return True, deadline.strftime("%d %B %Y, %I:%M %p")
        
        logger.info("Deadline is in the future.")
        return False, None
    except Exception as e:
        logger.error(f"Error checking deadline: {e}")
        return False, None

# ================== Helper Function ==================
def log_activity(action, target=None, details=None):
    """
    Helper function to log administrative or user actions to the database.
    Silent fail: If logging fails, it logs an error but doesn't crash the app.
    """
    try:
        # Determine who is acting
        if session.get('admin'):
            actor = "Admin"
        elif session.get('student_email'):
            actor = session['student_email']
        else:
            actor = "Anonymous"

        # Create log entry
        log = ActivityLog(
            actor=actor,
            action=action,
            target=str(target) if target else None,
            details=str(details) if details else None,
            ip_address=request.remote_addr,
            timestamp=datetime.now(timezone.utc)
        )
        
        db.session.add(log)
        db.session.commit()
        
    except Exception as e:
        # We catch all exceptions so the main app flow isn't interrupted by a logging failure
        logger.error(f"Failed to log activity: {e}")
        db.session.rollback()
# ================== Activity Log Page Route ==================
@app.route("/admin/activity_log")
def view_activity_log():
    # 1. Security Check
    if not session.get("admin"):
        return redirect(url_for("login"))

    # 2. Pagination Logic
    page = request.args.get('page', 1, type=int)
    per_page = 50  # Show 50 logs per page

    # 3. Fetch Logs (Sorted Newest First)
    pagination = ActivityLog.query.order_by(ActivityLog.timestamp.desc())\
                                  .paginate(page=page, per_page=per_page, error_out=False)
    
    logs = pagination.items

    return render_template(
        "activity_log.html",
        logs=logs,
        pagination=pagination
    )

@app.route("/admin/reset_activity_log", methods=["POST"])
def reset_activity_log():
    # 1. Security Check
    if not session.get("admin"):
        return redirect(url_for("login"))

    try:
        # 2. Delete all records in ActivityLog table
        db.session.query(ActivityLog).delete()
        db.session.commit()
        
        # 3. Create a new entry saying it was reset (so it's not totally empty)
        log_activity("Reset Activity Log", details="All previous history cleared by Admin")
        
        return redirect(url_for("view_activity_log", success="Activity log cleared successfully."))
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error clearing activity log: {e}")
        return redirect(url_for("view_activity_log", error=f"Failed to clear log: {str(e)}"))

def migrate_database():
    """Migration function to create tables and update schema"""
    try:
        with app.app_context():
            # 1. Create tables if they don't exist (This creates 'activity_logs')
            db.create_all()
            
            # 2. Check for missing columns in existing tables
            inspector = inspect(db.engine)
            
            # --- Migrate STUDENTS table ---
            s_columns = [c['name'] for c in inspector.get_columns('students')]
            with db.engine.connect() as conn:
                if 'sheet_filename' not in s_columns:
                    conn.execute(text("ALTER TABLE students ADD COLUMN sheet_filename VARCHAR(255)"))
                    logger.info("Added 'sheet_filename' column to students")
                
                if 'sheet_position' not in s_columns:
                    conn.execute(text("ALTER TABLE students ADD COLUMN sheet_position INTEGER"))
                    logger.info("Added 'sheet_position' column to students")

                # --- Migrate TEMPLATES table ---
                t_columns = [c['name'] for c in inspector.get_columns('templates')]

                # 0. Remove NOT NULL constraint from filename (allow Cloudinary-only templates)
                try:
                    # For PostgreSQL
                    conn.execute(text("ALTER TABLE templates ALTER COLUMN filename DROP NOT NULL"))
                    logger.info("Removed NOT NULL constraint from templates.filename")
                except:
                    # SQLite doesn't support this easily, skip
                    pass
                
                # 1. Add Language Column
                if 'language' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN language VARCHAR(20) DEFAULT 'english'"))
                    logger.info("Added 'language' column to templates")
                
                # 2. Add Text Direction Column
                if 'text_direction' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN text_direction VARCHAR(10) DEFAULT 'ltr'"))
                    logger.info("Added 'text_direction' column to templates")
                
                # 3. Add Template URL Column (for Cloudinary storage)
                if 'template_url' not in t_columns:
                    conn.execute(text("ALTER TABLE templates ADD COLUMN template_url TEXT"))
                    logger.info("Added 'template_url' column to templates")
                
                # List of new columns to check and add
                new_cols = [
                    ('deadline', 'DATETIME'),
                    ('card_width', 'INTEGER DEFAULT 1015'),
                    ('card_height', 'INTEGER DEFAULT 661'),
                    ('sheet_width', 'INTEGER DEFAULT 2480'),
                    ('sheet_height', 'INTEGER DEFAULT 3508'),
                    ('grid_rows', 'INTEGER DEFAULT 5'),
                    ('grid_cols', 'INTEGER DEFAULT 2')
                ]

                for col_name, col_type in new_cols:
                    if col_name not in t_columns:
                        try:
                            conn.execute(text(f"ALTER TABLE templates ADD COLUMN {col_name} {col_type}"))
                            logger.info(f"Added '{col_name}' column to templates")
                        except Exception as e:
                            logger.warning(f"Could not add {col_name}: {e}")
                
                conn.commit()

        logger.info("Database migration check completed")
    except Exception as e:
        logger.error(f"Error during database migration: {e}")

def migrate_template_font_colors():
    """Migrate existing templates to use separate label and value font colors"""
    try:
        templates = Template.query.all()
        migrated_count = 0
        
        for template in templates:
            needs_update = False
            
            # Check if this template needs migration
            if 'font_color' in template.font_settings:
                # Migrate to separate colors
                if 'label_font_color' not in template.font_settings:
                    template.font_settings['label_font_color'] = template.font_settings['font_color']
                    needs_update = True
                if 'value_font_color' not in template.font_settings:
                    template.font_settings['value_font_color'] = template.font_settings['font_color']
                    needs_update = True
                
                if needs_update:
                    migrated_count += 1
                    logger.info(f"Migrated font colors for template {template.id}")
        
        db.session.commit()
        logger.info(f"Font color migration completed: {migrated_count} templates updated")
        
    except Exception as e:
        logger.error(f"Error during font color migration: {e}")

def migrate_photo_background_settings():
    """Add missing background removal keys to old templates"""
    try:
        templates = Template.query.all()
        updated = 0
        default_photo = get_default_photo_config()
        
        for template in templates:
            if template.photo_settings is None:
                template.photo_settings = {}
            
            needs_update = False
            if "remove_background" not in template.photo_settings:
                template.photo_settings["remove_background"] = default_photo["remove_background"]
                needs_update = True
            if "bg_remove_color" not in template.photo_settings:
                template.photo_settings["bg_remove_color"] = default_photo["bg_remove_color"]
                needs_update = True
                
            if needs_update:
                updated += 1
        
        if updated > 0:
            db.session.commit()
            logger.info(f"Migrated background removal settings for {updated} templates")
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error migrating photo background settings: {e}")

def add_template(filename, school_name, card_orientation='landscape', language='english', text_direction='ltr'):
    try:
        # --- Default Dimensions based on Orientation ---
        # CR80 Defaults: 1015x661 (Landscape) or 661x1015 (Portrait)
        if card_orientation == 'portrait':
            width, height = 661, 1015
            rows, cols = 2, 5 # 2 Rows, 5 Cols on A4 Landscape
        else:
            width, height = 1015, 661
            rows, cols = 5, 2 # 5 Rows, 2 Cols on A4 Portrait

        template = Template(
            filename=filename,
            school_name=school_name,
            font_settings=get_default_font_config(),
            photo_settings=get_default_photo_config(),
            qr_settings=get_default_qr_config(),
            card_orientation=card_orientation,
            language=language,              # <--- NEW
            text_direction=text_direction,  # <--- NEW
            
            # --- NEW: Save Dimensions & Grid ---
            card_width=width,
            card_height=height,
            sheet_width=2480,  # Default A4 @ 300 DPI
            sheet_height=3508, # Default A4 @ 300 DPI
            grid_rows=rows,
            grid_cols=cols,
            # -----------------------------------
            
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(template)
        db.session.commit()
        
        logger.info(f"Added template: {filename} ({width}x{height})")
        return template.id
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding template: {e}")
        raise


def add_template_cloudinary(template_url, school_name, card_orientation='landscape', language='english', text_direction='ltr'):
    """
    Add a template with Cloudinary URL (no local file storage).
    
    Args:
        template_url (str): Cloudinary secure URL for the template
        school_name (str): School name
        card_orientation (str): 'landscape' or 'portrait'
        language (str): Language for labels
        text_direction (str): 'ltr' or 'rtl'
    
    Returns:
        int: Template ID
    """
    try:
        # --- Default Dimensions based on Orientation ---
        if card_orientation == 'portrait':
            width, height = 661, 1015
            rows, cols = 2, 5
        else:
            width, height = 1015, 661
            rows, cols = 5, 2

        template = Template(
            filename=None,  # No local file
            template_url=template_url,  # Store Cloudinary URL
            school_name=school_name,
            font_settings=get_default_font_config(),
            photo_settings=get_default_photo_config(),
            qr_settings=get_default_qr_config(),
            card_orientation=card_orientation,
            language=language,
            text_direction=text_direction,
            card_width=width,
            card_height=height,
            sheet_width=2480,
            sheet_height=3508,
            grid_rows=rows,
            grid_cols=cols,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(template)
        db.session.commit()
        
        logger.info(f"Added Cloudinary template: {template_url[:50]}... ({width}x{height})")
        return template.id
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding Cloudinary template: {e}")
        raise

# ================== Template Config ==================
def get_templates():
    try:
        templates = db.session.query(Template).order_by(Template.created_at.desc()).all()
        result = []
        
        for template in templates:
            font_settings = get_default_font_config()
            photo_settings = get_default_photo_config()
            qr_settings = get_default_qr_config()
            
            # Merge with template settings
            if template.font_settings:
                loaded_font = template.font_settings.copy()
                # Handle legacy font_color field
                if 'font_color' in loaded_font and 'label_font_color' not in loaded_font:
                    loaded_font['label_font_color'] = loaded_font['font_color']
                    loaded_font['value_font_color'] = loaded_font['font_color']
                font_settings = {**font_settings, **loaded_font}
            
            if template.photo_settings:
                photo_settings = {**photo_settings, **template.photo_settings}
            
            if template.qr_settings:
                qr_settings = {**qr_settings, **template.qr_settings}
            
            # === MISSING PART ADDED HERE ===
            # Serialize fields for frontend
            template_fields = []
            if template.fields:
                for field in template.fields:
                    template_fields.append({
                        'field_name': field.field_name,
                        'field_label': field.field_label,
                        'field_type': field.field_type,
                        'is_required': field.is_required,
                        'display_order': field.display_order,
                        'field_options': field.field_options
                    })
                      # Sort by display order so they appear correctly on the form
                template_fields.sort(key=lambda x: x['display_order'])
            # ===============================

            result.append({
                'id': template.id,
                'filename': template.filename,
                'school_name': template.school_name,
                'created_at': template.created_at.isoformat() if template.created_at else datetime.now(timezone.utc).isoformat(),
                'font_settings': font_settings,
                'photo_settings': photo_settings,
                'qr_settings': qr_settings,
                'card_orientation': template.card_orientation or 'landscape',
                'deadline': template.deadline.isoformat() if template.deadline else None,
                'fields': template_fields,  # Now this variable exists
                 # --- NEW DIMENSIONS ---
                'card_width': template.card_width or 1015,
                'card_height': template.card_height or 661,
                'sheet_width': template.sheet_width or 2480,
                'sheet_height': template.sheet_height or 3508,
                'grid_rows': template.grid_rows or 5,
                'grid_cols': template.grid_cols or 2
            })
        
        return result
    except Exception as e:
        logger.error(f"Error fetching templates: {e}")
        return []  # Always return empty list on error


def update_template_settings(template_id, font_settings=None, photo_settings=None, qr_settings=None, 
                             card_orientation=None, card_dims=None, sheet_dims=None, grid_layout=None):
    try:
        template = db.session.get(Template, template_id)

        if not template:
            logger.error(f"Template {template_id} not found")
            return
        
        if font_settings is not None:
            default_font = get_default_font_config()
            complete_font_settings = {**default_font, **font_settings}
            template.font_settings = complete_font_settings
        
        if photo_settings is not None:
            default_photo = get_default_photo_config()
            complete_photo_settings = {**default_photo, **photo_settings}
            template.photo_settings = complete_photo_settings
        
        if qr_settings is not None:
            default_qr = get_default_qr_config()
            complete_qr_settings = {**default_qr, **qr_settings}
            template.qr_settings = complete_qr_settings
        
        if card_orientation is not None:
            template.card_orientation = card_orientation

        # --- UPDATE DIMENSIONS ---
        if card_dims:
            # Expecting dict like {'width': 1015, 'height': 661}
            template.card_width = card_dims.get('width', 1015)
            template.card_height = card_dims.get('height', 661)
            
        if sheet_dims:
            # Expecting dict like {'width': 2480, 'height': 3508}
            template.sheet_width = sheet_dims.get('width', 2480)
            template.sheet_height = sheet_dims.get('height', 3508)

        # --- NEW: UPDATE GRID LAYOUT ---
        if grid_layout:
            # Expecting dict like {'rows': 5, 'cols': 2}
            template.grid_rows = grid_layout.get('rows', 5)
            template.grid_cols = grid_layout.get('cols', 2)
        # -------------------------------
        
        db.session.commit()
        log_activity("Updated Template Settings", target=f"Template {template_id}", 
                     details=f"Orientation: {card_orientation}")
        
        logger.info(f"Updated settings for template ID {template_id}, orientation: {card_orientation}")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating template settings: {e}")
        raise
    
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

def check_duplicate_student(form_data, photo_filename=None, student_id=None):
    duplicate_config = load_duplicate_config()
    # Get the template ID from the form data
    current_template_id = form_data.get('template_id')
    
    # If for some reason template_id is missing, we cannot scope it, so return False or handle error
    if not current_template_id:
        # Fallback: if no template ID provided, skip check or check globally (optional)
        return False, None 

    try:
        # Check phone duplicates (Scoped to Template)
        if duplicate_config.get("check_phone", False):
            query = Student.query.filter(
                Student.phone == form_data['phone'],
                Student.template_id == current_template_id  # <--- SCOPE TO SCHOOL
            )
            if student_id:
                query = query.filter(Student.id != student_id)
            if query.first():
                return True, "A student with this phone number already exists in this school."
        
        # Check name and class duplicates (Scoped to Template)
        if duplicate_config.get("check_name_class", True):
            query = Student.query.filter(
                Student.name == form_data['name'],
                Student.class_name == form_data['class_name'],
                Student.template_id == current_template_id  # <--- SCOPE TO SCHOOL
            )
            if student_id:
                query = query.filter(Student.id != student_id)
            if query.first():
                return True, "A student with this name and class combination already exists in this school."
        
        return False, None
    except Exception as e:
        logger.error(f"Error checking duplicates: {e}")
        return True, f"Database error: {str(e)}"
    


try:
    import mediapipe as mp
    mp_face = mp.solutions.face_detection
    face_detector = mp_face.FaceDetection(
        model_selection=0,
        min_detection_confidence=0.5
    )
except Exception as e:
    print("MediaPipe disabled:", e)
    face_detector = None



def auto_crop_face_photo(photo_path, target_width=260, target_height=313, remove_background=False, bg_color="#ffffff"):
    """
    Robust AI Crop:
    1. Fixes Orientation (EXIF rotation).
    2. Detects Face.
    3. Calculates a STRICT PORTRAIT crop box (matches target ratio).
    4. Positions face low enough to include hats/turbans.
    5. Adds white padding if needed (never stretches/distorts).
    """
    try:
        import cv2
        # 1. Load Image & Fix Orientation (Critical for mobile photos)
        pil_img = Image.open(photo_path)
        pil_img = ImageOps.exif_transpose(pil_img) # <--- FIXES LANDSCAPE/ROTION ISSUES
        pil_img = pil_img.convert("RGB")
        
        # ============================================================
        # FIXED: AI BACKGROUND REMOVAL (NO EXTRA SPACE)
        # ============================================================
        if remove_background and remove_bg:
            try:
                logger.info("Starting AI background removal...")
        
                # Convert to PNG for rembg
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                input_bytes = buf.getvalue()
        
                # Run rembg
                output_bytes = remove_bg(input_bytes)
                fg = Image.open(io.BytesIO(output_bytes)).convert("RGBA")
        
                # 🔒 IMPORTANT FIX:
                # Keep ORIGINAL image size to avoid gaps
                bg_rgb = tuple(int(bg_color[i:i+2], 16) for i in (1, 3, 5))
                bg_layer = Image.new("RGB", pil_img.size, bg_rgb)
        
                # Center the foreground on original canvas
                fg_w, fg_h = fg.size
                bg_w, bg_h = bg_layer.size
                offset = ((bg_w - fg_w) // 2, (bg_h - fg_h) // 2)
        
                bg_layer.paste(fg, offset, fg)
                pil_img = bg_layer
        
                logger.info("Background removed without canvas shrink")
        
            except Exception as e:
                logger.error(f"Background removal failed: {e}")
                pil_img = ImageOps.exif_transpose(Image.open(photo_path).convert("RGB"))
        # ============================================================



        img_np = np.array(pil_img)
        h_orig, w_orig, _ = img_np.shape

        # 2. Detect Face (MediaPipe)
        results = face_detector.process(img_np)

        if not results.detections:
            return _fallback_center_crop(pil_img, photo_path, target_width, target_height)

        # 3. Get Main Face Geometry
        detection = max(results.detections, key=lambda d: d.score[0])
        box = detection.location_data.relative_bounding_box
        
        # Face pixel coordinates
        face_w = int(box.width * w_orig)
        face_h = int(box.height * h_orig)
        face_cx = int((box.xmin + box.width / 2) * w_orig)
        face_cy = int((box.ymin + box.height / 2) * h_orig)

        # ============================================================
        # GEOMETRY SETTINGS (Hat & Portrait Optimized)
        # ============================================================
        # Face Height: 45% of image height (Small enough to fit big hats)
        face_to_image_ratio = 0.45 
        
        # Face Center Y: 58% down from top (Leaves space above for hat)
        # 0.50 = Center, 0.60 = Lower down
        face_center_y_ratio = 0.51
        # ============================================================

        # 4. Calculate the Perfect Portrait Crop Box
        # We start with height, then enforce width based on target aspect ratio
        crop_h = int(face_h / face_to_image_ratio)
        
        # Force the aspect ratio to match the ID card (e.g. 260/313 = 0.83)
        target_aspect = target_width / target_height
        crop_w = int(crop_h * target_aspect)

        # 5. Calculate Coordinates
        # Center horizontally on the face
        x1 = face_cx - (crop_w // 2)
        x2 = x1 + crop_w

        # Position vertically (Lower face = more top space)
        y1 = face_cy - int(crop_h * face_center_y_ratio)
        y2 = y1 + crop_h

        # 6. Smart Padding (Handle Out-of-Bounds)
        # If the box goes outside the image, pad the original image with white
        pad_l = max(0, -x1)
        pad_t = max(0, -y1)
        pad_r = max(0, x2 - w_orig)
        pad_b = max(0, y2 - h_orig)

        if any([pad_l, pad_t, pad_r, pad_b]):
            # Add white border
            img_np = cv2.copyMakeBorder(
                img_np, pad_t, pad_b, pad_l, pad_r, 
                cv2.BORDER_CONSTANT, value=(255, 255, 255)
            )
            # Shift crop coordinates to account for padding
            x1 += pad_l
            y1 += pad_t
            x2 += pad_l
            y2 += pad_t

        # 7. Crop
        crop_img = img_np[y1:y2, x1:x2]

        if crop_img.size == 0:
            return _fallback_center_crop(pil_img, photo_path, target_width, target_height)

        # 8. Resize & Save
        # Convert back to PIL
        final_pil = Image.fromarray(crop_img)
        
        # Resize to exact target dimensions
        final_img = final_pil.resize((target_width, target_height), Image.Resampling.LANCZOS)
        final_img.save(photo_path, "JPEG", quality=95)
        return True

    except Exception as e:
        logger.exception(f"Smart crop failed: {e}")
        try:
            return _fallback_center_crop(Image.open(photo_path), photo_path, target_width, target_height)
        except:
            return False


def _fallback_center_crop(pil_img, save_path, target_w, target_h):
    """
    Fallback that respects EXIF rotation and aspect ratio.
    """
    # Fix orientation even in fallback
    pil_img = ImageOps.exif_transpose(pil_img) 
    
    img_w, img_h = pil_img.size
    current_ratio = img_w / img_h
    target_ratio = target_w / target_h

    if current_ratio > target_ratio:
        # Too wide: crop sides
        new_width = int(img_h * target_ratio)
        left = (img_w - new_width) // 2
        crop = pil_img.crop((left, 0, left + new_width, img_h))
    else:
        # Too tall: crop top/bottom
        new_height = int(img_w / target_ratio)
        top = (img_h - new_height) // 2
        crop = pil_img.crop((0, top, img_w, top + new_height))
    
    final = crop.resize((target_w, target_h), Image.Resampling.LANCZOS)
    final.save(save_path, "JPEG", quality=95)
    return True


def _process_photo_pil(pil_img, target_width=260, target_height=313, remove_background=False, bg_color="#ffffff"):
    """
    Process a PIL Image in-memory (no file I/O).
    Crops and optionally removes background.
    Returns processed PIL Image.
    """
    try:
        import cv2
        
        # Ensure RGB
        if pil_img.mode != 'RGB':
            pil_img = pil_img.convert("RGB")
        
        # Background removal (if enabled)
        if remove_background and remove_bg:
            try:
                logger.info("Processing AI background removal...")
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                output_bytes = remove_bg(buf.getvalue())
                fg = Image.open(io.BytesIO(output_bytes)).convert("RGBA")
                
                bg_rgb = tuple(int(bg_color[i:i+2], 16) for i in (1, 3, 5))
                bg_layer = Image.new("RGB", pil_img.size, bg_rgb)
                fg_w, fg_h = fg.size
                bg_w, bg_h = bg_layer.size
                offset = ((bg_w - fg_w) // 2, (bg_h - fg_h) // 2)
                bg_layer.paste(fg, offset, fg)
                pil_img = bg_layer
            except Exception as e:
                logger.warning(f"Background removal failed, continuing: {e}")
        
        # Face detection and crop
        if face_detector:
            img_np = np.array(pil_img)
            h_orig, w_orig, _ = img_np.shape
            results = face_detector.process(img_np)
            
            if results.detections:
                detection = max(results.detections, key=lambda d: d.score[0])
                box = detection.location_data.relative_bounding_box
                
                face_w = int(box.width * w_orig)
                face_h = int(box.height * h_orig)
                face_cx = int((box.xmin + box.width / 2) * w_orig)
                face_cy = int((box.ymin + box.height / 2) * h_orig)
                
                # Calculate crop
                face_to_image_ratio = 0.45
                face_center_y_ratio = 0.51
                crop_h = int(face_h / face_to_image_ratio)
                target_aspect = target_width / target_height
                crop_w = int(crop_h * target_aspect)
                
                x1 = face_cx - (crop_w // 2)
                x2 = x1 + crop_w
                y1 = face_cy - int(crop_h * face_center_y_ratio)
                y2 = y1 + crop_h
                
                # Handle out-of-bounds with padding
                pad_l = max(0, -x1)
                pad_t = max(0, -y1)
                pad_r = max(0, x2 - w_orig)
                pad_b = max(0, y2 - h_orig)
                
                if any([pad_l, pad_t, pad_r, pad_b]):
                    img_np = cv2.copyMakeBorder(
                        img_np, pad_t, pad_b, pad_l, pad_r,
                        cv2.BORDER_CONSTANT, value=(255, 255, 255)
                    )
                    x1 += pad_l
                    y1 += pad_t
                    x2 += pad_l
                    y2 += pad_t
                
                # Crop
                crop_img = img_np[y1:y2, x1:x2]
                if crop_img.size > 0:
                    final_pil = Image.fromarray(crop_img)
                    pil_img = final_pil.resize((target_width, target_height), Image.Resampling.LANCZOS)
        
        return pil_img
    
    except Exception as e:
        logger.warning(f"Photo processing failed: {e}, returning original")
        return pil_img

# Ensure logger is defined
logger = logging.getLogger(__name__)

def send_email(to, subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = os.environ.get("EMAIL_FROM")
    msg['To'] = to

    server = None
    try:
        # 1. Load Config (Hardcode Google Settings to eliminate variable errors)
        smtp_server = "smtp.gmail.com"
        smtp_port = 465  # We are switching back to 465 (SSL)
        password = os.environ.get("EMAIL_PASSWORD")
        
        logger.info(f"📧 Attempting email to {to} via {smtp_server}:{smtp_port} (Force IPv4)")

        # 2. FORCE IPv4 (Crucial Step)
        # This fixes "[Errno 101] Network is unreachable" on Railway
        addr_info = socket.getaddrinfo(smtp_server, smtp_port, socket.AF_INET, socket.SOCK_STREAM)
        family, socktype, proto, canonname, sa = addr_info[0]
        target_ip = sa[0]
        
        logger.info(f"🔗 Resolved Gmail to IPv4: {target_ip}")

        # 3. Connect using SSL (SMTP_SSL)
        # We connect directly to the IPv4 IP with a 30-second timeout
        server = smtplib.SMTP_SSL(target_ip, smtp_port, timeout=30)
        server.set_debuglevel(1)  # Prints connection details to logs
        
        # 4. Login
        logger.info("🔑 Logging in...")
        server.login(msg['From'], password)
        
        # 5. Send
        logger.info("📤 Sending message...")
        server.send_message(msg)
        server.quit()
        
        logger.info(f"✅ Email sent successfully to {to}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to send email: {e}")
        return False
    finally:
        if server:
            try:
                server.quit()
            except:
                pass
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
@limiter.limit("5 per minute")
def login():
    if request.method == "POST":
        # Load from Environment Variables
        env_user = os.environ.get("ADMIN_USERNAME")
        env_hash = os.environ.get("ADMIN_PASSWORD_HASH")

        # Security Check: Ensure env vars are set
        if not env_user or not env_hash:
            logger.error("Admin credentials not set in environment variables!")
            return render_template("login.html", error="Server configuration error. Contact support."), 500

        # Check Username and Password Hash
        username_input = request.form.get("username")
        password_input = request.form.get("password")

        if username_input == env_user and check_password_hash(env_hash, password_input):
            session["admin"] = True
            logger.info("Admin logged in successfully")
            return redirect("/admin")
        else:
            logger.warning("Failed login attempt: Invalid credentials")
            return render_template("login.html", error="Invalid login credentials"), 401
            
    return render_template("login.html")

@app.route("/student_login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
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
                student = Student.query.filter(
                    db.func.lower(Student.email) == db.func.lower(email)
                ).first()
                
                if not student:
                    error = "No account found with this email."
                    logger.warning(f"Login failed: No account found for email {email}")
                elif student.password is None:
                    error = "Account has no password set. Please contact support."
                    logger.warning(f"Login failed: NULL password for email {email}")
                elif check_password_hash(student.password, password):
                    session["student_email"] = student.email
                    session["student_school_name"] = school_name
                    logger.info(f"Login successful for email: {student.email}")
                    # CHANGED: Redirect to index instead of landing page
                    return redirect(url_for("index"))
                else:
                    error = "Invalid password."
                    logger.warning(f"Login failed: Invalid password for email {email}")
            except Exception as e:
                error = f"Database error: {str(e)}"
                logger.error(f"Database error during login for email {email}: {e}")
  
    return render_template("login_student.html", templates=templates, error=error)

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("3 per minute")
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
                existing_student = Student.query.filter(
                    db.func.lower(Student.email) == db.func.lower(email)
                ).first()
                
                if existing_student:
                    error = "Email already registered."
                    logger.warning(f"Registration failed: Email {email} already registered")
                else:
                    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
                    if not hashed_password:
                        error = "Failed to hash password."
                        logger.error("Registration failed: Password hashing error")
                    else:
                        student = Student(
                            name=name,
                            email=email.lower(),
                            password=hashed_password,
                            school_name=school_name,
                            created_at = datetime.now(timezone.utc)

                        )
                        db.session.add(student)
                        db.session.commit()
                        
                        session["student_email"] = email.lower()
                        session["student_school_name"] = school_name
                        logger.info(f"Registered new student: {email}, name: {name}")
                        # CHANGED: Redirect to index instead of landing page
                        return redirect(url_for("index"))
            except Exception as e:
                error = f"Database error: {str(e)}"
                logger.error(f"Database error during registration for email {email}: {e}")
    return render_template("register.html", templates=templates, error=error)

# Search for the existing @app.route("/reset_password"...) and replace it with this:

# -------------------------------------------------------------------------
# 1. REQUEST RESET LINK (User enters email here)
# -------------------------------------------------------------------------
@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        
        # Check if student exists
        student = Student.query.filter(db.func.lower(Student.email) == email).first()
        
        if student:
            # Generate secure token (Contains email + timestamp)
            token = serializer.dumps(email, salt='password-reset-salt')
            
            # Generate the link pointing to the NEW reset route
            link = url_for('reset_password_with_token', token=token, _external=True)
            
            # Send Email
            subject = "Password Reset Request"
            body = f"""Hello {student.name},

You requested to reset your password. Please click the link below to set a new one:

{link}

This link is valid for 1 hour.
If you did not request this, please ignore this email.
"""
            # --- UPDATE: Check return value of send_email ---
            if send_email(email, subject, body):
                logger.info(f"Password reset link sent to {email}")
                flash("A reset link has been sent to your email. Please check your inbox.", "success")
            else:
                logger.error(f"Failed to send reset email to {email}")
                flash("Error sending email. Please try again later.", "error")
            # -----------------------------------------------
        else:
            # Security: Generic message to prevent email enumeration
            flash("If an account exists with that email, a reset link has been sent.", "info")
            
        return redirect(url_for('student_login'))
        
    # Render the form where they type their email
    return render_template("forgot_password.html")

# -------------------------------------------------------------------------
# 2. SET NEW PASSWORD (User clicks link -> Enters new password)
# -------------------------------------------------------------------------
@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password_with_token(token):
    try:
        # Verify Token (Expires in 3600 seconds = 1 hour)
        email = serializer.loads(token, salt='password-reset-salt', max_age=3600)
    except SignatureExpired:
        flash("The reset link has expired. Please request a new one.", "error")
        return redirect(url_for('forgot_password'))
    except BadTimeSignature:
        flash("Invalid reset link.", "error")
        return redirect(url_for('forgot_password'))
    
    # If POST, they are submitting the new password
    if request.method == "POST":
        password = request.form.get("password")
        confirm = request.form.get("confirm_password")
        
        if not password or not confirm:
            flash("Both password fields are required.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        else:
            try:
                student = Student.query.filter(db.func.lower(Student.email) == email).first()
                if student:
                    # Hash and Save
                    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
                    student.password = hashed_password
                    db.session.commit()
                    
                    flash("Your password has been successfully reset! Please log in.", "success")
                    return redirect(url_for("student_login"))
                else:
                    flash("User not found.", "error")
            except Exception as e:
                db.session.rollback()
                logger.error(f"Database error during password reset: {e}")
                flash("An error occurred. Please try again.", "error")

    # Render the form where they type the NEW password
    return render_template("reset_password_token.html", token=token)

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
        student = Student.query.filter_by(email=session['student_email']).first()
        if not student:
            flash('No matching student record found.', 'error')
            return redirect(url_for('admin'))
        
        # Check if email already in use by another student
        existing = Student.query.filter(
            Student.email == email,
            Student.id != student.id
        ).first()
        
        if existing:
            flash('Email already in use.', 'error')
            return redirect(url_for('admin'))
        
        student.email = email
        session['student_email'] = email
        db.session.commit()
        
        flash('Email updated successfully.', 'success')
        
    except Exception as e:
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
        student = Student.query.filter_by(email=session['student_email']).first()
        
        if not student or not student.password:
            flash('Current password is incorrect.', 'error')
            return redirect(url_for('admin'))
        
        try:
            if not check_password_hash(student.password, current_password):
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('admin'))
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            flash('Authentication error. Please try again.', 'error')
            return redirect(url_for('admin'))
        
        hashed_password = generate_password_hash(new_password)
        student.password = hashed_password
        db.session.commit()
        
        flash('Password updated successfully.', 'success')
    except Exception as e:
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
        student = db.session.get(Student, student_id)
        
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404
        
        # Get preview image URL (use Cloudinary URLs if present)
        preview_url = None
        if getattr(student, 'image_url', None):
            preview_url = student.image_url
        elif getattr(student, 'generated_filename', None):
            # Legacy fallback to local file
            preview_filename = student.generated_filename.replace('.pdf', '.jpg')
            preview_path = os.path.join(GENERATED_FOLDER, preview_filename)
            if os.path.exists(preview_path):
                preview_url = url_for('static', filename=f'generated/{preview_filename}')
        
        return jsonify({
            "success": True,
            "name": student.name,
            "class_name": student.class_name,
            "preview_url": preview_url or url_for('static', filename='placeholder.jpg'),
            "pdf_url": getattr(student, 'pdf_url', None),
            "has_preview": preview_url is not None
        })
    except Exception as e:
        logger.error(f"Error fetching student preview: {e}")
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route("/admin/generate_preview/<int:student_id>")
def generate_student_preview(student_id):
    """Generate a fresh preview for a student with Safe Font Fallback"""
    if not session.get("admin") and not session.get("student_email"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        student = db.session.get(Student, student_id)
        
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404
        
        template = db.session.get(Template, student.template_id) if student.template_id else None
        
        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404
        
        # Load template settings
        font_settings = get_default_font_config()
        photo_settings = get_default_photo_config()
        qr_settings = get_default_qr_config()
        
        if template.font_settings:
            loaded_font = template.font_settings.copy()
            if 'font_color' in loaded_font and 'label_font_color' not in loaded_font:
                loaded_font['label_font_color'] = loaded_font['font_color']
                loaded_font['value_font_color'] = loaded_font['font_color']
            font_settings = {**font_settings, **loaded_font}
        
        if template.photo_settings:
            photo_settings = {**photo_settings, **template.photo_settings}
        
        if template.qr_settings:
            qr_settings = {**qr_settings, **template.qr_settings}
        
        # Adjust settings based on orientation
        photo_settings = get_photo_settings_for_orientation(student.template_id, photo_settings)
        font_settings = get_font_settings_for_orientation(student.template_id, font_settings)
        
        # Generate preview
        # Get template path (Cloudinary URL or legacy local path)
        template_path = get_template_path(student.template_id)
        if not template_path:
            return jsonify({"success": False, "error": "Template not found"}), 404
        
        try:
            card_width, card_height = get_card_size(student.template_id)
            template_img = load_template_smart(template_path)
            template_img = template_img.resize((card_width, card_height))
            
            draw = ImageDraw.Draw(template_img)
            
            # Load font paths
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
                apply_text_case(student.name, text_case),
                apply_text_case(student.father_name, text_case),
                apply_text_case(student.class_name, text_case),
                student.dob, 
                apply_text_case(student.address, text_case),
                student.phone 
            ]
            
            # Draw text
            label_x = font_settings["label_x"]
            value_x = font_settings["value_x"]
            start_y = font_settings["start_y"]
            line_height = font_settings["line_height"]
            max_value_width = template_img.width - value_x - 50
            
            for i, (label, value) in enumerate(zip(labels, values)):
                y = start_y + i * line_height
                
                # --- SAFE FONT LOADING FALLBACK ---
                try:
                    # Try loading the user-selected custom font
                    label_font = ImageFont.truetype(FONT_BOLD_PATH, font_settings["label_font_size"])
                except OSError:
                    try:
                        # Fallback 1: Try local arialbd.ttf in fonts folder
                        label_font = ImageFont.truetype(os.path.join(FONTS_FOLDER, "arialbd.ttf"), font_settings["label_font_size"])
                    except OSError:
                        # Fallback 2: Ultimate fallback to system default (Prevents Crash)
                        label_font = ImageFont.load_default()
                        logger.warning(f"Using system default font for student {student.id} label '{label}'")
                # ----------------------------------

                value_font = load_font_dynamic(FONT_REGULAR_PATH, value, max_value_width, font_settings["value_font_size"])
                draw.text((label_x, y), f"{label}:", font=label_font, fill=label_font_color)
                draw.text((value_x, y), value, font=value_font, fill=value_font_color)
            
            # Add photo (Cloudinary URL preferred, fallback to legacy local filename)
            try:
                photo_stream = None
                if getattr(student, 'photo_url', None):
                    # Fetch remote image bytes
                    import requests
                    resp = requests.get(student.photo_url, timeout=8)
                    if resp.status_code == 200:
                        photo_stream = BytesIO(resp.content)
                elif getattr(student, 'photo_filename', None):
                    local_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
                    if os.path.exists(local_path):
                        photo_stream = open(local_path, 'rb')

                if photo_stream:
                    photo_img = Image.open(photo_stream).convert("RGBA").resize(
                        (photo_settings["photo_width"], photo_settings["photo_height"])
                    )
                    radii = [
                        photo_settings.get("photo_border_top_left", 0),
                        photo_settings.get("photo_border_top_right", 0),
                        photo_settings.get("photo_border_bottom_right", 0),
                        photo_settings.get("photo_border_bottom_left", 0)
                    ]
                    photo_img = round_photo(photo_img, radii)
                    template_img.paste(photo_img, (photo_settings["photo_x"], photo_settings["photo_y"]), photo_img)
                    try:
                        if not isinstance(photo_stream, BytesIO):
                            photo_stream.close()
                    except: pass
            except Exception as e:
                logger.error(f"Error adding photo to preview: {e}")
            
            # ---------------- QR CODE ----------------
            if qr_settings.get("enable_qr", False):
                qr_data = "SAMPLE_PREVIEW_DATA"
            
                if qr_settings.get("qr_data_type") == "student_id":
                    qr_data = json.dumps({
                        "student_id": str(student_id),
                        "name": student.name,
                        "school_name": student.school_name
                    })
                elif qr_settings.get("qr_data_type") == "url":
                    qr_data = qr_settings.get("qr_base_url", "") + str(student_id)
                elif qr_settings.get("qr_data_type") == "text":
                    qr_data = qr_settings.get("qr_custom_text", "Sample Text")
                elif qr_settings.get("qr_data_type") == "json":
                    qr_data = json.dumps({
                        "student_id": student_id,
                        "name": student.name,
                        "class": student.class_name,
                        "school_name": student.school_name
                    })
            
                qr_size = qr_settings.get("qr_size", 120)
                qr_img = generate_qr_code(qr_data, qr_settings, qr_size)
            
                # 🔐 FORCE QR TO RGB BEFORE PASTE
                qr_img = force_rgb(qr_img)
            
                qr_x = qr_settings.get("qr_x", 50)
                qr_y = qr_settings.get("qr_y", 50)
            
                qr_img = qr_img.resize((qr_size, qr_size))
                template_img.paste(qr_img, (qr_x, qr_y))
            
            
            # =====================================================
            # 🚨 FINAL JPEG SAFETY (ABSOLUTE LAST IMAGE OPERATION)
            # =====================================================
            template_img = force_rgb(template_img)
            
            logger.info(f"Preview image mode before save: {template_img.mode}")
            logger.warning(f"FINAL IMAGE MODE BEFORE SAVE: {template_img.mode}")
            buf = BytesIO()
            template_img.save(
                buf,
                format="JPEG",
                quality=95,
                subsampling=0,
                optimize=True
            )
            buf.seek(0)
            
            img_bytes = buf.getvalue()
            

            try:
                uploaded = upload_image(img_bytes, folder='generated')
                preview_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
            except Exception as e:
                logger.error(f"Cloudinary upload failed: {e}")
                return jsonify({"success": False, "error": "Failed to upload preview"}), 500

            return jsonify({"success": True, "preview_url": preview_url, "message": "Preview generated successfully"})
            
        except Exception as e:
            logger.error(f"Error generating preview: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({"success": False, "error": f"Preview generation failed: {str(e)}"}), 500
            
    except Exception as e:
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
      
        buf = BytesIO()
        test_img.save(buf, format='JPEG', quality=95)
        buf.seek(0)
        try:
            uploaded = upload_image(buf.getvalue(), folder='generated')
            test_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
        except Exception as e:
            return jsonify({"success": False, "error": f"Upload failed: {e}"}), 500

        return jsonify({"success": True, "message": "Preview test completed", "test_image": test_url})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/admin/download_student_pdf/<int:student_id>")
def download_student_pdf(student_id):
    """Download student PDF"""
    if not session.get("admin") and not session.get("student_email"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
  
    try:
        student = db.session.get(Student, student_id)
        
        # Prefer remote PDF URL stored on student.pdf_url
        if not student:
            return jsonify({"success": False, "error": "PDF not found"}), 404

        if getattr(student, 'pdf_url', None):
            return redirect(student.pdf_url)

        # Legacy fallback: serve local file
        if getattr(student, 'generated_filename', None):
            pdf_filename = student.generated_filename
            pdf_path = os.path.join(GENERATED_FOLDER, pdf_filename)
            if os.path.exists(pdf_path):
                return send_file(pdf_path, as_attachment=True, download_name=pdf_filename)

        return jsonify({"success": False, "error": "PDF file not found"}), 404
        
    except Exception as e:
        logger.error(f"Error downloading student PDF: {e}")
        return jsonify({"success": False, "error": "Database error"}), 500

# ================== Student Credential Management Routes ==================
@app.route("/admin_student_credentials")
def admin_student_credentials():
    if not session.get("admin"):
        return redirect(url_for("login"))
  
    try:
        students = Student.query.filter(
            Student.email.isnot(None),
            Student.email != ''
        ).order_by(Student.created_at.desc()).all()
        
        return render_template(
            "admin_student_credentials.html",
            students=students,
            success=request.args.get("success"),
            error=request.args.get("error")
        )
    except Exception as e:
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
      
        # Check if email already exists
        existing = Student.query.filter_by(email=email).first()
        if existing:
            return redirect(url_for("admin_student_credentials", error="Email already registered"))
        
        # Insert new student credential
        student = Student(
            name=name,
            email=email,
            password=hashed_password,
            school_name=school_name,
            created_at = datetime.now(timezone.utc)

        )
        db.session.add(student)
        db.session.commit()
        
        logger.info(f"Admin added student credential: {email}")
        
        return redirect(url_for("admin_student_credentials", success="Student credential added successfully"))
        
    except Exception as e:
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
      
        student = db.session.get(Student, student_id)
        if not student:
            return redirect(url_for("admin_student_credentials", error="Student not found"))
        
        # Check if email already exists for other students
        existing = Student.query.filter(
            Student.email == email,
            Student.id != student_id
        ).first()
        
        if existing:
            return redirect(url_for("admin_student_credentials", error="Email already registered to another student"))
        
        student.name = name
        student.email = email
        student.school_name = school_name
        
        # Update password only if provided
        if password:
            if len(password) < 6:
                return redirect(url_for("admin_student_credentials", error="Password must be at least 6 characters"))
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
            student.password = hashed_password
        
        db.session.commit()
        logger.info(f"Admin updated student credential for ID: {student_id}")
        
        return redirect(url_for("admin_student_credentials", success="Student credential updated successfully"))
        
    except Exception as e:
        logger.error(f"Error updating student credential: {e}")
        return redirect(url_for("admin_student_credentials", error=f"Error updating student credential: {str(e)}"))

@app.route("/admin_delete_student_credential/<int:student_id>", methods=["POST"])
def admin_delete_student_credential(student_id):
    if not session.get("admin"):
        return redirect(url_for("login"))
  
    try:
        student = db.session.get(Student, student_id)
        if not student:
            return redirect(url_for("admin_student_credentials", error="Student not found"))
        
        # Check if student has any ID cards created
        if student.photo_filename or student.generated_filename:
            return redirect(url_for("admin_student_credentials", error="Cannot delete student with existing ID cards. Delete the ID cards first."))
        
        db.session.delete(student)
        db.session.commit()
        
        logger.info(f"Admin deleted student credential for ID: {student_id}")
        
        return redirect(url_for("admin_student_credentials", success="Student credential deleted successfully"))
        
    except Exception as e:
        logger.error(f"Error deleting student credential: {e}")
        return redirect(url_for("admin_student_credentials", error=f"Error deleting student credential: {str(e)}"))

@app.route("/admin_reset_student_password/<int:student_id>", methods=["POST"])
def admin_reset_student_password(student_id):
    if not session.get("admin"):
        return redirect(url_for("login"))
  
    try:
        student = db.session.get(Student, student_id)
        if not student:
            return redirect(url_for("admin_student_credentials", error="Student not found"))
        
        # Generate a random password
        new_password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
        hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
        
        student.password = hashed_password
        db.session.commit()
        
        # Send the new password
        if student.email:
            try:
                send_email(student.email, "Password Reset", f"Your new password is: {new_password}")
            except Exception as email_error:
                logger.error(f"Error sending email: {email_error}")
                return redirect(url_for("admin_student_credentials", success=f"Password reset successfully. New password: {new_password} (Email failed to send)"))
        
        logger.info(f"Admin reset password for student ID: {student_id}")
        
        return redirect(url_for("admin_student_credentials", success=f"Password reset successfully. New password sent to student's email."))
        
    except Exception as e:
        logger.error(f"Error resetting student password: {e}")
        return redirect(url_for("admin_student_credentials", error=f"Error resetting password: {str(e)}"))

# ================== UPDATED INDEX ROUTE (Admin Email Bypass) ==================

# ================== MAIN INDEX ROUTE ==================
@app.route("/index", methods=["GET", "POST"])
def index():
    # 1. Security Check
    if not session.get("student_email"):
        return redirect(url_for("student_login"))

    # 2. Init Variables
    generated_url = None
    download_url = None
    form_data = {}
    success = None
    error = None
    templates = get_templates()
    show_fetch = True
    unique_edit_id = None
    
    # --- ADDED: Deadline Info Variable ---
    deadline_info = None
    # -------------------------------------
    
    # Auto-select template based on school name
    school_name = session.get("student_school_name")
    selected_template_id = None
    if school_name:
        for t in templates:
            if t['school_name'] == school_name:
                selected_template_id = t['id']
                break

    # --- ADDED: Calculate Deadline Info for Display ---
    # Determine which template is active (from form POST or default selection)
    current_tid = selected_template_id

    if request.method == "POST" and 'edit_student_id' in session:
        edit_id = session.get('edit_student_id')
        student_for_deadline = db.session.get(Student, edit_id)
        if student_for_deadline:
            current_tid = student_for_deadline.template_id
    else:
        current_tid = request.form.get("template_id") or selected_template_id

    if current_tid:
        try:
            # Find template data
            tmpl = next((t for t in templates if str(t['id']) == str(current_tid)), None)
            if tmpl and tmpl.get('deadline'):
                dd = datetime.fromisoformat(tmpl['deadline'])
                now = datetime.now()
                
                if now < dd:
                    # Active Deadline
                    diff = dd - now
                    days = diff.days
                    hours, remainder = divmod(diff.seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    deadline_info = {
                        "active": True,
                        "expired": False,
                        "date": dd.strftime("%d %B %Y, %I:%M %p"),
                        "days": days,
                        "hours": hours,
                        "minutes": minutes
                    }
                else:
                    # Expired Deadline
                    deadline_info = {
                        "active": True,
                        "expired": True,
                        "date": dd.strftime("%d %B %Y, %I:%M %p")
                    }
        except Exception as e:
            logger.error(f"Error calculating deadline info: {e}")
    # --------------------------------------------------

    if request.method == "POST":
        try:
            # === LIMIT CHECK WITH ADMIN BYPASS ===
            is_editing = 'edit_student_id' in session
            current_email = session['student_email'].strip().lower()
            admin_email = os.environ.get("EMAIL_FROM", "").lower() if os.environ.get("EMAIL_FROM") else ""
            
            # Logic: If NOT editing AND email is NOT admin email -> Check Limit
            if not is_editing and current_email != admin_email:
                count = Student.query.filter_by(email=session['student_email']).count()
                
                # Limit is set to 3 cards
                if count >= 3:
                    return render_template("index.html", 
                                           error="⚠️ Limit Reached: You can only generate 3 ID cards per account.", 
                                           templates=templates, 
                                           form_data=request.form, 
                                           selected_template_id=int(request.form.get("template_id", 0)),
                                           deadline_info=deadline_info), 403 # Added deadline_info
            
            # If Admin, log the bypass for debugging
            if current_email == admin_email and not is_editing:
                logger.info(f"Admin email {admin_email} bypassed the 3-card limit.")
            # === END LIMIT CHECK ===

            # 1. Get Template ID
            if is_editing:
                edit_id = session.get('edit_student_id')
                student = db.session.get(Student, edit_id)
                if not student:
                    raise ValueError("Editing student not found")
            
                template_id = student.template_id   # ✅ ONLY SOURCE
            else:
                template_id = int(request.form.get("template_id", 0))
            

            # 2. Check Deadline
            is_passed, deadline_date = check_deadline_passed(template_id)

            # --- ADDED: Strict Deadline Enforcement (No Bypass) ---
            if is_passed:
                error_msg = f"⛔ The deadline passed on {deadline_date}. Card generation is closed for everyone."
                return render_template("index.html", 
                                       error=error_msg, 
                                       templates=templates, 
                                       form_data=request.form, 
                                       selected_template_id=template_id,
                                       deadline_info=deadline_info), 403
            # ------------------------------------------------------

            # Load Settings
            font_settings, photo_settings, qr_settings, card_orientation = get_template_settings(template_id)
            template_path = get_template_path(template_id)
            if not template_path:
                raise ValueError("Template file not found")


            # Capture Form Data
            name = request.form.get("name", "").strip()
            father_name = request.form.get("father_name", "").strip()
            class_name = request.form.get("class_name", "").strip()
            dob = request.form.get("dob", "").strip()
            address = request.form.get("address", "").strip()
            phone = request.form.get("phone", "").strip()
            
            form_data = {
                'name': name, 'father_name': father_name, 'class_name': class_name,
                'dob': dob, 'address': address, 'phone': phone, 'template_id': template_id
            }

            # Check Duplicates (Skip check if editing)
            if not is_editing:
                is_dup, dup_msg = check_duplicate_student(form_data)
                if is_dup:
                    return render_template("index.html", error=dup_msg, templates=templates, 
                                           form_data=form_data, selected_template_id=template_id,
                                           deadline_info=deadline_info), 400 # Added deadline_info

            # Handle Photo (upload to Cloudinary)
            photo_stored = None
            photo_url = None
            if 'photo' in request.files and request.files['photo'].filename:
                photo = request.files['photo']
                photo_fn = secure_filename(photo.filename)
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                
                # Convert to bytes and crop
                photo_bytes = io.BytesIO()
                photo.save(photo_bytes)
                photo_bytes.seek(0)
                
                # Auto-crop using PIL
                try:
                    pil_img = Image.open(photo_bytes)
                    photo_bytes = io.BytesIO()
                    pil_img.save(photo_bytes, format='JPEG')
                    photo_bytes.seek(0)
                    
                    bg_color = photo_settings.get("bg_remove_color", "#ffffff")
                    remove_bg_flag = photo_settings.get("remove_background", False)
                    # Note: auto_crop_face_photo expects file path; we'll skip it for Cloudinary flow
                except: 
                    photo_bytes.seek(0)
                
                # Upload to Cloudinary
                try:
                    uploaded = upload_image(photo_bytes.getvalue(), folder='photos')
                    photo_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
                except Exception as e:
                    logger.error(f"Failed to upload photo to Cloudinary: {e}")
                    photo_url = None
                
                if not photo_url:
                    return render_template("index.html", error="Failed to upload photo. Please try again.", 
                                           templates=templates, form_data=request.form, 
                                           selected_template_id=template_id, deadline_info=deadline_info), 500
                
                photo_stored = f"{timestamp}_{photo_fn}"
            elif request.form.get('photo_url'):
                # Support existing photo_url from previous generation
                photo_url = request.form.get('photo_url')
                photo_stored = photo_url  # Store URL identifier
            elif request.form.get('photo_filename'):
                # Legacy local filename
                photo_stored = request.form.get('photo_filename')
            else:
                raise ValueError("Photo is required")

            # === CARD GENERATION LOGIC ===
            try:
                card_width, card_height = get_card_size(template_id)
                template_path = get_template_path(template_id)
                template_img = load_template_smart(template_path).resize((card_width, card_height))
            except Exception as e:
                logger.error(f"Error loading template {template_id} from {template_path}: {e}")
                return render_template("index.html", error=f"Failed to load template: {str(e)}", 
                                       templates=templates, form_data=request.form,
                                       selected_template_id=template_id, deadline_info=deadline_info), 500
            
            draw = ImageDraw.Draw(template_img)
            
            FONT_BOLD = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
            FONT_REG = os.path.join(FONTS_FOLDER, font_settings["font_regular"])
            try:
                l_font = ImageFont.truetype(FONT_BOLD, font_settings["label_font_size"])
            except:
                l_font = ImageFont.load_default()

            try:
                L_COLOR = tuple(font_settings.get("label_font_color", [0,0,0]))
                V_COLOR = tuple(font_settings.get("value_font_color", [0,0,0]))
            except: L_COLOR = V_COLOR = (0,0,0)

            # --- NEW MULTI-LANGUAGE LOGIC START ---
            template = db.session.get(Template, template_id)
            if template:
                lang = getattr(template, 'language', 'english') or 'english'
                direction = getattr(template, 'text_direction', 'ltr') or 'ltr'
            else:
                lang = 'english'
                direction = 'ltr'

            std_labels = {
                'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
                'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
                'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
                'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
            }
            labels_map = std_labels.get(lang, std_labels['english'])

            all_fields = [
                {'label': labels_map['NAME'], 'val': name, 'order': 10},
                {'label': labels_map['F_NAME'], 'val': father_name, 'order': 20},
                {'label': labels_map['CLASS'], 'val': class_name, 'order': 30},
                {'label': labels_map['DOB'], 'val': dob, 'order': 40},
                {'label': labels_map['MOBILE'], 'val': phone, 'order': 50},
                {'label': labels_map['ADDRESS'], 'val': address, 'order': 60}
            ]
            
            custom_data = {}
            if template:
                for field in TemplateField.query.filter_by(template_id=template_id).order_by(TemplateField.display_order.asc()).all():
                    f_name = field.field_name
                    val = request.form.get(f_name, "").strip()
                    custom_data[f_name] = val
                    all_fields.append({
                        'label': field.field_label, # Uses raw label typed by admin
                        'val': val,
                        'order': field.display_order
                    })

            all_fields.sort(key=lambda x: x['order'])
            
            # --- NEW MULTI-LANGUAGE LOGIC END ---

            # --- PRE-CALCULATE PHOTO BOUNDARIES ---
            p_x = photo_settings.get("photo_x", 0)
            p_y = photo_settings.get("photo_y", 0)
            p_h = photo_settings.get("photo_height", 0)
            p_bottom = p_y + p_h

            # Draw Loop
            label_x = font_settings["label_x"]
            value_x = font_settings["value_x"]
            current_y = font_settings["start_y"]
            line_height = font_settings["line_height"]
            text_case = font_settings.get("text_case", "normal")

            for item in all_fields:
                if current_y > card_height - 20: break
                
                raw_label = item['label']
                raw_val = apply_text_case(item['val'], text_case)

                # --- TEXT SHAPING ---
                display_label = process_text_for_drawing(raw_label, lang)
                display_val = process_text_for_drawing(raw_val, lang)

                # --- DRAWING ---
                label_text_final = display_label + ":" if direction == 'ltr' else ":" + display_label
                draw.text((label_x, current_y), label_text_final, font=l_font, fill=L_COLOR)

                # --- DYNAMIC WIDTH CALCULATION ---
                # Check vertical overlap
                is_vertically_overlapping = (current_y < p_bottom) and ((current_y + line_height) > p_y)

                if is_vertically_overlapping and (p_x > value_x):
                     max_w = p_x - value_x - 15
                else:
                     max_w = card_width - value_x - 20
                # ---------------------------------

                # --- SPECIAL LOGIC FOR ADDRESS: FIT IN 2 LINES (FIXED) ---
                if item['label'] == labels_map['ADDRESS']:
                    draw.text((label_x, current_y), f"{item['label']}:", font=l_font, fill=L_COLOR)

                    # Start shrinking logic
                    curr_size = font_settings["value_font_size"]
                    min_size = 10 
                    wrapped_addr = []
                    
                    # We need the path to reload font for accurate measurement
                    addr_font_path = FONT_REG 

                    while curr_size >= min_size:
                        # 1. Load font at this specific size to measure
                        try:
                            temp_font = ImageFont.truetype(addr_font_path, curr_size)
                        except:
                            temp_font = ImageFont.load_default()

                        # 2. Heuristic wrapping
                        avg_char_w = curr_size * 0.50
                        chars_limit = int(max_w / avg_char_w)
                        if chars_limit < 5: chars_limit = 5 

                        wrapped_addr = textwrap.wrap(raw_val, width=chars_limit, break_long_words=True)
                        
                        # 3. Check pixel width of every line
                        fits_horizontally = True
                        if len(wrapped_addr) <= 2:
                            for line in wrapped_addr:
                                # Shape text for accurate length measurement (Urdu/Arabic support)
                                measure_text = process_text_for_drawing(line, lang)
                                if temp_font.getlength(measure_text) > max_w:
                                    fits_horizontally = False
                                    break
                            
                            if fits_horizontally:
                                # It fits! Use this font
                                addr_font = temp_font
                                break
                        
                        # Decrease size and try again
                        curr_size -= 2
                    
                    # Fallback if loop finishes without finding a fit
                    if 'addr_font' not in locals():
                        try: addr_font = ImageFont.truetype(addr_font_path, min_size)
                        except: addr_font = ImageFont.load_default()

                    # Draw up to 2 lines
                    for line in wrapped_addr[:2]:
                        line_display = process_text_for_drawing(line, lang)
                        draw.text((value_x, current_y), line_display, font=addr_font, fill=V_COLOR)
                        # Use slightly tighter spacing if we shrunk the font significantly
                        spacing = line_height if curr_size > 20 else curr_size + 5
                        current_y += spacing

                # --- STANDARD LOGIC FOR OTHER FIELDS ---
                else:
                    test_font = ImageFont.truetype(FONT_REG, font_settings["value_font_size"])
                    text_len = test_font.getlength(raw_val)

                    # If normal text is too long, wrap it (but no aggressive shrinking)
                    if text_len > max_w:
                        avg_char_w = font_settings["value_font_size"] * 0.55
                        chars_limit = int(max_w / avg_char_w)
                        wrapped = textwrap.wrap(raw_val, width=chars_limit, break_long_words=True)
                        
                        v_font = load_font_dynamic(FONT_REG, max(wrapped, key=len), max_w, font_settings["value_font_size"])
                        
                        for line in wrapped:
                            line_display = process_text_for_drawing(line, lang)
                            draw.text((value_x, current_y), line_display, font=v_font, fill=V_COLOR)
                            current_y += line_height
                    else:
                        v_font = load_font_dynamic(FONT_REG, raw_val, max_w, font_settings["value_font_size"])
                        draw.text((value_x, current_y), display_val, font=v_font, fill=V_COLOR)
                        current_y += line_height

            # Photo & QR (fetch remote photo if needed)
            try:
                if photo_url:
                    # Download photo from Cloudinary
                    import requests
                    resp = requests.get(photo_url, timeout=8)
                    if resp.status_code == 200:
                        ph = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                        ph = ph.resize((photo_settings["photo_width"], photo_settings["photo_height"]))
                        radii = [photo_settings.get(f"photo_border_{k}", 0) for k in ["top_left", "top_right", "bottom_right", "bottom_left"]]
                        ph = round_photo(ph, radii)
                        template_img.paste(ph, (photo_settings["photo_x"], photo_settings["photo_y"]), ph)
                elif photo_stored and not photo_stored.startswith('http'):
                    # Legacy: load from local file
                    ph = Image.open(os.path.join(UPLOAD_FOLDER, photo_stored)).convert("RGBA")
                    ph = ph.resize((photo_settings["photo_width"], photo_settings["photo_height"]))
                    radii = [photo_settings.get(f"photo_border_{k}", 0) for k in ["top_left", "top_right", "bottom_right", "bottom_left"]]
                    ph = round_photo(ph, radii)
                    template_img.paste(ph, (photo_settings["photo_x"], photo_settings["photo_y"]), ph)
            except Exception as e:
                logger.error(f"Error adding photo: {e}")

            data_hash = generate_data_hash(form_data, photo_stored)
            if qr_settings.get("enable_qr"):
                qr_id = data_hash[:10]
                if qr_settings.get("qr_data_type") == "url":
                    base = qr_settings.get("qr_base_url", "")
                    if not base.endswith('/'): base += '/'
                    qr_payload = base + qr_id
                else:
                    qr_payload = qr_id
                
                qr_img = generate_qr_code(qr_payload, qr_settings, qr_settings.get("qr_size", 120))
                qr_img = qr_img.resize((qr_settings.get("qr_size", 120),)*2)
                template_img.paste(qr_img, (qr_settings.get("qr_x", 50), qr_settings.get("qr_y", 50)))

            # =========================================================
            # UPLOAD TO CLOUDINARY (NOT LOCAL SAVE)
            # =========================================================
            
            # --- FIX: Convert to RGB before saving as JPEG ---
            # This fixes "cannot write mode RGBA as JPEG"
            if template_img.mode == 'RGBA':
                # Create a white background for transparency
                background = Image.new("RGB", template_img.size, (255, 255, 255))
                background.paste(template_img, mask=template_img.split()[3])
                template_img = background
            else:
                template_img = template_img.convert("RGB")
            # -------------------------------------------------

            # Save image to bytes for Cloudinary upload
            jpg_buf = io.BytesIO()
            template_img.save(jpg_buf, format='JPEG', quality=95)
            jpg_buf.seek(0)
            jpg_bytes = jpg_buf.getvalue()
            
            pdf_buf = io.BytesIO()
            template_img.save(pdf_buf, format='PDF', quality=95)
            pdf_buf.seek(0)
            pdf_bytes = pdf_buf.getvalue()
            
            # Upload to Cloudinary
            try:
                jpg_result = upload_image(jpg_bytes, folder='generated')
                image_url = jpg_result if isinstance(jpg_result, str) else jpg_result.get('url')
                
                pdf_result = upload_image(pdf_bytes, folder='generated', resource_type='raw')
                pdf_url = pdf_result if isinstance(pdf_result, str) else pdf_result.get('url')
            except Exception as e:
                logger.error(f"Cloudinary upload failed: {e}")
                return render_template("index.html", error=f"Failed to save image: {str(e)}", 
                                       templates=templates, form_data=request.form, 
                                       selected_template_id=template_id, deadline_info=deadline_info), 500
            
            # Legacy URLs (for backward compat, but won't be used if image_url is set)
            generated_url = image_url
            download_url = pdf_url
            # =========================================================

            # DB Insert/Update
            if is_editing:
                edit_id = session['edit_student_id']
                student = db.session.get(Student, edit_id)
                if student:
                    student.name = name
                    student.father_name = father_name
                    student.class_name = class_name
                    student.dob = dob
                    student.address = address
                    student.phone = phone
                    student.photo_url = photo_url  # Store Cloudinary URL
                    student.image_url = image_url  # Store generated card image URL
                    student.pdf_url = pdf_url  # Store generated PDF URL
                    student.created_at = datetime.now(timezone.utc)
                    student.data_hash = data_hash
                    student.template_id = template_id
                    student.school_name = school_name
                    student.custom_data = custom_data
                    db.session.commit()
                    unique_edit_id = edit_id
                    success = "Card Updated Successfully!"
                    session.pop('edit_student_id', None)
            else:
                # New Record
                student = Student(
                    name=name,
                    father_name=father_name,
                    class_name=class_name,
                    dob=dob,
                    address=address,
                    phone=phone,
                    photo_url=photo_url,  # Store Cloudinary URL instead of filename
                    image_url=image_url,  # Store generated card image URL
                    pdf_url=pdf_url,  # Store PDF URL
                    created_at=datetime.now(timezone.utc),
                    data_hash=data_hash,
                    template_id=template_id,
                    school_name=school_name,
                    email=session['student_email'],
                    custom_data=custom_data
                )
                db.session.add(student)
                db.session.commit()
                
                unique_edit_id = student.id
                # NO CALL TO add_card_to_sheet() HERE

                # Email Logic
                try:
                    if student.email:
                        email_subject = "ID Card Generated Successfully - Save Your ID"
                        email_body = f"""Hello {student.name},

Your ID card for {student.school_name} has been generated successfully.

--------------------------------------------------
YOUR UNIQUE ID: {unique_edit_id}
--------------------------------------------------

Please save this ID number. You will need it if you ever want to edit your ID card details later.

Regards,
ID Card Admin
NOOR GRAPHICS AND PRINTERS
"""
                        # --- UPDATED: Check if email sent successfully ---
                        if send_email(student.email, email_subject, email_body):
                            logger.info(f"Email success confirmed for {student.email}")
                        else:
                            logger.error(f"Email FAILED for {student.email} (Check server logs for details)")
                        # -------------------------------------------------

                except Exception as email_err:
                    logger.error(f"Crash in email logic: {email_err}")

                success = f"Card Generated Successfully! (ID: {unique_edit_id}). \n An email with this ID has been sent to you."
            # Clear Form
            form_data = { 'template_id': template_id }

        except Exception as e:
            error = f"Error: {str(e)}"
            logger.error(error)
            return render_template("index.html", error=error, templates=templates, 
                                   form_data=request.form, selected_template_id=template_id,
                                   deadline_info=deadline_info), 500 # Added deadline_info

    return render_template("index.html", generated_url=generated_url, download_url=download_url,
                           form_data=form_data, success=success, error=error, templates=templates, 
                           show_fetch=show_fetch, unique_edit_id=unique_edit_id, 
                           selected_template_id=selected_template_id, deadline_info=deadline_info) # Added deadline_info

@app.route("/fetch_record", methods=["POST"])
def fetch_record():
    unique_id = request.form.get("unique_id", "").strip()
    if not unique_id:
        logger.error("Unique ID is required")
        return jsonify({"success": False, "error": "Unique ID is required"}), 400
  
    try:
        student = db.session.get(Student, unique_id)
        
        logger.info(f"Fetching record for unique_id: {unique_id}, found: {student}")
        
        if student:
            session_email = session.get('student_email')
          
            # If student record has no email, allow access for logged-in users
            if not student.email:
                logger.warning(f"Student record {student.id} has no email associated")
                
                # If user is logged in and has less than 3 records, allow access
                if session_email:
                    user_record_count = Student.query.filter_by(email=session_email).count()
                    
                    if user_record_count < 3:
                        # Update record with session email
                        student.email = session_email
                        db.session.commit()
                        logger.info(f"Associated student {student.id} with email {session_email}")
                        edit_url = url_for('edit_student', student_id=student.id)
                        return jsonify({"success": True, "edit_url": edit_url})
                    else:
                        return jsonify({"success": False, "error": "You have reached your record limit (3)."}), 403
                else:
                    return jsonify({"success": False, "error": "Please login first to access this record."}), 403
          
            # If record has email, check authorization
            if session_email and student.email.lower() == session_email.lower():
                edit_url = url_for('edit_student', student_id=student.id)
                return jsonify({"success": True, "edit_url": edit_url})
            else:
                logger.warning(f"Email mismatch - Student: {student.email}, Session: {session_email}")
                return jsonify({"success": False, "error": "You are not authorized to edit this record"}), 403
        else:
            logger.error(f"No record found for unique ID {unique_id}")
            return jsonify({"success": False, "error": "No record found for this unique ID"}), 404
    except Exception as e:
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
    
    # 1. GET Request
    try:
        student = db.session.get(Student, student_id)
        
        if not student:
            error = f"No student found with ID {student_id}"
            logger.error(error)
            return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                 generated_url=generated_url, download_url=download_url, success=success), 404
        
        # Handle cases where email might be missing
        student_email = student.email
        session_email = session.get('student_email')
        
        logger.info(f"Authorization check - Student ID: {student_id}")
        logger.info(f"Database email: {student_email}")
        logger.info(f"Session email: {session_email}")

        # --- STRICT DEADLINE CHECK ---
        if student and student.template_id:
            is_passed, deadline_date = check_deadline_passed(student.template_id)
            
            if is_passed:
                error = f"⛔ The deadline for editing this ID card passed on {deadline_date}. Updates are disabled."
                # If it's a POST (trying to save), block strictly
                if request.method == "POST":
                    return render_template("edit.html", error=error, templates=templates, form_data=form_data), 403
                else:
                    # If it's a GET (just viewing), show flash error but maybe allow read-only
                    flash(error, 'error')                    # ---
        
        # If student record has no email, check if we can associate it with current session
        if not student_email:
            logger.warning(f"Student record {student_id} has no email associated")
            
            # If user is logged in, check if this is their only record without email
            if session_email:
                # Count how many records this user has
                user_record_count = Student.query.filter_by(email=session_email).count()
                
                # If user has less than 3 records and this record has no email, allow association
                if user_record_count < 3:
                    # Update the record with current user's email
                    student.email = session_email
                    db.session.commit()
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
        # LOGIC UPDATE: Allow if owner OR if admin email matches
        admin_email = os.environ.get("EMAIL_FROM", "").lower()
        is_admin_user = session.get("admin") or (session_email and session_email.lower() == admin_email)
        is_owner = student_email and session_email and (student_email.lower() == session_email.lower())

        if not is_owner and not is_admin_user:
            error = f"You are not authorized to access this record."
            logger.warning(f"Authorization failed for student {student_id}")
            return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                 generated_url=generated_url, download_url=download_url, success=success), 403
        # Authorization successful - populate form data
        form_data = {
            'id': student.id,
            'name': student.name,
            'father_name': student.father_name,
            'class_name': student.class_name,
            'dob': student.dob,
            'address': student.address,
            'phone': student.phone,
            'photo_filename': student.photo_filename,
            'generated_filename': student.generated_filename,
            'template_id': student.template_id,
            'school_name': student.school_name,
            'custom_data': student.custom_data or {} # <--- CRITICAL: Send existing custom data to frontend
        }
        
        # Use Cloudinary URLs if available
        if student.image_url:
            generated_url = student.image_url
        elif student.generated_filename:
            # Fallback to legacy local filename
            preview_filename = student.generated_filename.replace(".pdf", ".jpg")
            generated_url = url_for('static', filename=f'generated/{preview_filename}')
        else:
            generated_url = url_for('static', filename='placeholder.jpg')
        
        download_url = student.pdf_url if student.pdf_url else None
    
    except Exception as e:
        error = f"Error fetching student data: {str(e)}"
        logger.error(error)
        return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                             generated_url=generated_url, download_url=download_url, success=success), 500
  
    if request.method == "POST":
        try:
            student = db.session.get(Student, student_id)
            
            if not student or not student.email:
                # If still no email, use session email for authorization
                session_email = session.get('student_email')
                if not session_email:
                    error = "You must be logged in to edit records."
                    return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                         generated_url=generated_url, download_url=download_url, success=success), 403
                
                # Update the record with session email
                student.email = session_email
                db.session.commit()
                logger.info(f"Updated student {student_id} with email {session_email}")
            else:
                student_email = student.email
                session_email = session.get('student_email')
                
                # LOGIC UPDATE: Check ownership OR admin status
                admin_email = os.environ.get("EMAIL_FROM", "").lower()
                is_admin_user = session.get("admin") or (session_email and session_email.lower() == admin_email)
                is_owner = student_email and session_email and (student_email.lower() == session_email.lower())

                if not is_owner and not is_admin_user:
                    error = "You are not authorized to access this record."
                    logger.warning(f"POST Authorization failed for student {student_id}")
                    return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                         generated_url=generated_url, download_url=download_url, success=success), 403
        except Exception as e:
            error = f"Database error: {str(e)}"
            logger.error(error)
            return render_template("edit.html", error=error, templates=templates, form_data=form_data,
                                 generated_url=generated_url, download_url=download_url, success=success), 500
      
        existing_photo_filename = form_data.get('photo_filename', '')
        template_id = student.template_id
        school_name = next((t['school_name'] for t in templates if t['id'] == template_id), None)
        template_path = get_template_path(template_id)
        if not template_path:
            error = "No template found. Please contact administrator."
            logger.error(f"Template not found for ID {template_id}")
                   
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 400
      
        font_settings, photo_settings, qr_settings, card_orientation = get_template_settings(template_id)
        card_width, card_height = get_card_size(template_id)
      
        FONT_BOLD_PATH = os.path.join(FONTS_FOLDER, font_settings["font_bold"])
        FONT_REGULAR_PATH = os.path.join(FONTS_FOLDER, font_settings["font_regular"])
      
        if not is_valid_font_file(FONT_BOLD_PATH) or not is_valid_font_file(FONT_REGULAR_PATH):
            error = f"Invalid font file: {font_settings['font_bold']} or {font_settings['font_regular']}"
            logger.error(error)
            return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                 form_data=form_data, error=error, templates=templates), 500
      
        # FIXED: Get font colors from settings
        label_font_color = font_settings.get("label_font_color", [0, 0, 0])
        value_font_color = font_settings.get("value_font_color", [0, 0, 0])
      
        # Convert colors to tuples for PIL
        try:
            LABEL_FONT_COLOR = tuple(label_font_color)
            logger.info(f"Label font color: {LABEL_FONT_COLOR}")
        except Exception as e:
            logger.error(f"Error converting label font color: {label_font_color}, error: {e}")
            LABEL_FONT_COLOR = (0, 0, 0)
      
        try:
            VALUE_FONT_COLOR = tuple(value_font_color)
            logger.info(f"Value font color: {VALUE_FONT_COLOR}")
        except Exception as e:
            logger.error(f"Error converting value font color: {value_font_color}, error: {e}")
            VALUE_FONT_COLOR = (0, 0, 0)
      
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
        photo_url = None
        
        # 1. Prefer Cloudinary photo if this edit uploaded one earlier
        if student.photo_filename:
            # photo_filename implies a real photo exists
            photo_url = student.image_url
        
        # 2. Safety fallback
        if not photo_url:
            logger.warning(f"No valid photo found for student {student_id}")
        
      
        if 'photo' in request.files and request.files['photo'].filename:
            photo = request.files['photo']
            photo_fn = secure_filename(photo.filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            photo_stored = f"{timestamp}_{photo_fn}"
            
            try:
                # Read photo to bytes
                photo_bytes = io.BytesIO()
                photo.save(photo_bytes)
                photo_bytes.seek(0)
                
                # Process photo (crop if needed)
                pil_img = Image.open(photo_bytes)
                pil_img = ImageOps.exif_transpose(pil_img)  # Fix orientation
                pil_img = pil_img.convert("RGB")
                
                # Auto-crop face using AI
                photo_settings = photo_settings or {}
                bg_color = photo_settings.get("bg_remove_color", "#ffffff")
                remove_bg_flag = photo_settings.get("remove_background", False)
                
                # Process the PIL image directly (in-memory crop)
                pil_img = _process_photo_pil(
                    pil_img,
                    target_width=photo_settings.get("photo_width", 260),
                    target_height=photo_settings.get("photo_height", 313),
                    remove_background=remove_bg_flag,
                    bg_color=bg_color
                )
                
                # Convert processed image to bytes
                photo_bytes = io.BytesIO()
                pil_img.save(photo_bytes, format="JPEG", quality=95)
                photo_bytes.seek(0)
                
                # Upload to Cloudinary
                photo_url = upload_image(photo_bytes.getvalue(), folder='photos')
            except Exception as e:
                error = f"Error processing photo: {str(e)}"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 500
        else:
            if not photo_stored and not photo_url:
                error = "No photo provided and no existing photo found"
                logger.error(error)
                return render_template("edit.html", generated_url=generated_url, download_url=download_url,
                                     form_data=form_data, error=error, templates=templates), 400
      
        data_hash = generate_data_hash(form_data, photo_stored)
      
        try:
            template = load_template_smart(template_path)
            template = template.resize((card_width, card_height))
            draw = ImageDraw.Draw(template)
          
            # Apply text case transformation
            text_case = font_settings.get("text_case", "normal")
            
            # --- DYNAMIC FIELDS HANDLING ---
            # 1. Capture dynamic values from the form
            custom_data = {}
            dynamic_display = []
            
            # Fetch field definitions for this template
            db_fields = TemplateField.query.filter_by(template_id=template_id).all()
            
            for field in db_fields:
                val = request.form.get(field.field_name, "").strip()
                custom_data[field.field_name] = val
                
                dynamic_display.append({
                    'label': field.field_label.upper(),
                    'value': val,
                    'order': field.display_order
                })
            
            # 2. Combine with Standard Fields
            all_fields = [
                {'label': "NAME", 'value': apply_text_case(name, text_case), 'order': 10},
                {'label': "F.NAME", 'value': apply_text_case(father_name, text_case), 'order': 20},
                {'label': "CLASS", 'value': apply_text_case(class_name, text_case), 'order': 30},
                {'label': "D.O.B.", 'value': dob, 'order': 40},
                {'label': "MOBILE", 'value': phone, 'order': 50},
                {'label': "ADDRESS", 'value': apply_text_case(address, text_case), 'order': 60}
            ]
            
            # Add dynamic fields to the list for drawing
            for d in dynamic_display:
                all_fields.append({
                    'label': d['label'],
                    'value': apply_text_case(d['value'], text_case),
                    'order': d['order']
                })

                
            
            # 3. Sort by display order
            all_fields.sort(key=lambda x: x['order'])
            # -------------------------------

            # --- PRE-CALCULATE PHOTO BOUNDARIES ---
            p_x = photo_settings.get("photo_x", 0)
            p_y = photo_settings.get("photo_y", 0)
            p_h = photo_settings.get("photo_height", 0)
            p_bottom = p_y + p_h

            # Layout Settings
            label_x = font_settings["label_x"]
            value_x = font_settings["value_x"]
            current_y = font_settings["start_y"]
            line_height = font_settings["line_height"]
            
            # Draw Loop
            for item in all_fields:
                lbl = item['label']
                val = item['value'] 
                
                # Stop if we ran out of vertical space
                if current_y > card_height - 20: break
                
                try:
                    l_font = ImageFont.truetype(FONT_BOLD_PATH, font_settings["label_font_size"])
                except:
                    l_font = ImageFont.load_default()

                # --- DYNAMIC WIDTH CALCULATION ---
                # Check vertical overlap
                is_vertically_overlapping = (current_y < p_bottom) and ((current_y + line_height) > p_y)

                if is_vertically_overlapping and (p_x > value_x):
                     max_w = p_x - value_x - 15
                else:
                     max_w = card_width - value_x - 20
                
                # --- ADDRESS LOGIC (PIXEL-ACCURATE, MAX 2 LINES) ---
                if lbl == "ADDRESS":
                    draw.text((label_x, current_y), f"{lbl}:", font=l_font, fill=LABEL_FONT_COLOR)
                
                    curr_size = font_settings["value_font_size"]
                    min_size = 12
                    wrapped_addr = []
                
                    while curr_size >= min_size:
                        # Load font at this size
                        try:
                            addr_font = ImageFont.truetype(FONT_REGULAR_PATH, curr_size)
                        except:
                            addr_font = ImageFont.load_default()
                
                        # Pixel-based wrapping
                        words = val.split()
                        lines = []
                        current_line = ""
                
                        for word in words:
                            test_line = current_line + (" " if current_line else "") + word
                            if draw.textlength(test_line, font=addr_font) <= max_w:
                                current_line = test_line
                            else:
                                if current_line:
                                    lines.append(current_line)
                                current_line = word
                
                        if current_line:
                            lines.append(current_line)
                
                        # Stop if it fits in 2 lines
                        if len(lines) <= 2:
                            wrapped_addr = lines
                            break
                
                        curr_size -= 2
                
                    # Fallback if still too long
                    if not wrapped_addr:
                        wrapped_addr = lines[:2]
                
                    # Draw address lines
                    for line in wrapped_addr[:2]:
                        draw.text((value_x, current_y), line, font=addr_font, fill=VALUE_FONT_COLOR)
                        current_y += curr_size + 6
                
                    continue
                

                # --- STANDARD FIELDS ---
                else:
                    test_font = ImageFont.truetype(FONT_REGULAR_PATH, font_settings["value_font_size"])
                    text_len = test_font.getlength(val)

                    if text_len > max_w:
                        avg_char_w = font_settings["value_font_size"] * 0.55
                        chars_limit = int(max_w / avg_char_w)
                        wrapped = textwrap.wrap(val, width=chars_limit, break_long_words=True)
                        
                        draw.text((label_x, current_y), f"{lbl}:", font=l_font, fill=LABEL_FONT_COLOR)
                        v_font = load_font_dynamic(FONT_REGULAR_PATH, max(wrapped, key=len), max_w, font_settings["value_font_size"])
                        
                        for line in wrapped:
                            draw.text((value_x, current_y), line, font=v_font, fill=VALUE_FONT_COLOR)
                            current_y += line_height
                    else:
                        v_font = load_font_dynamic(FONT_REGULAR_PATH, val, max_w, font_settings["value_font_size"])
                        draw.text((label_x, current_y), f"{lbl}:", font=l_font, fill=LABEL_FONT_COLOR)
                        draw.text((value_x, current_y), val, font=v_font, fill=VALUE_FONT_COLOR)
                        current_y += line_height
          
            try:
                if photo_url:
                    import requests
                    resp = requests.get(photo_url, timeout=8)
                    if resp.status_code == 200:
                        photo_img = Image.open(io.BytesIO(resp.content)).convert("RGBA").resize(
                            (photo_settings["photo_width"], photo_settings["photo_height"]),
                            Image.LANCZOS
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
          
            # Add QR Code if enabled
            if qr_settings.get("enable_qr", False):
                if qr_settings.get("qr_data_type") == "student_id":
                    qr_data = json.dumps({
                        "student_id": str(student_id),
                        "name": name,
                        "school_name": school_name,
                        "photo_url": photo_url,
                        "custom_data": custom_data # Include dynamic fields in QR
                    })
                elif qr_settings.get("qr_data_type") == "url":
                    qr_data = qr_settings.get("qr_base_url", "") + str(student_id)
                elif qr_settings.get("qr_data_type") == "text":
                    qr_data = qr_settings.get("qr_custom_text", "Sample Text")
                elif qr_settings.get("qr_data_type") == "json":
                    qr_data = json.dumps({
                        "student_id": student_id,
                        "name": name,
                        "class": class_name,
                        "school_name": school_name,
                        "photo_url": photo_url,
                        "custom_data": custom_data 
                    })
                else:
                    qr_data = str(student_id)
                
                qr_size = qr_settings.get("qr_size", 120)
                qr_img = generate_qr_code(qr_data, qr_settings, qr_size)
                qr_x = qr_settings.get("qr_x", 50)
                qr_y = qr_settings.get("qr_y", 50)
                
                # Resize QR if needed and paste
                qr_img = qr_img.resize((qr_size, qr_size))
                template.paste(qr_img, (qr_x, qr_y))
          
            # =========================================================
            # CONCURRENCY FIX: SAVE INDIVIDUAL CARD IMAGE TO CLOUDINARY
            # =========================================================
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            jpg_name = f"card_{template_id}_{student_id}_{timestamp}.jpg"
            pdf_name = f"card_{template_id}_{student_id}_{timestamp}.pdf" 
            student.generated_filename = jpg_name

            # --- FIX: Convert to RGB before saving as JPEG ---
            # Uses your existing force_rgb helper function
            template = force_rgb(template) 
            # -------------------------------------------------

            # Convert image to bytes and upload to Cloudinary
            jpg_buffer = io.BytesIO()
            template.save(jpg_buffer, "JPEG", quality=95)
            jpg_buffer.seek(0)
            jpg_url = upload_image(jpg_buffer.getvalue(), folder='cards', resource_type='image')
            
            # Clean up old file if it exists
            if student.generated_filename:
                old_path = os.path.join(GENERATED_FOLDER, student.generated_filename)
                if os.path.exists(old_path):
                    os.remove(old_path)
            
            # Update URLs for frontend display
            generated_url = jpg_url  # Use Cloudinary URL
            # Upload PDF to Cloudinary as well
            pdf_buffer = io.BytesIO()
            template.save(pdf_buffer, "PDF", resolution=300)
            pdf_buffer.seek(0)
            pdf_url = upload_image(pdf_buffer.getvalue(), folder='cards', resource_type='raw')
            download_url = pdf_url  # Use Cloudinary URL
          
            try:
                # Delete old photo if changed
                if student.photo_filename and student.photo_filename != photo_stored:
                    old_photo_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
                    if os.path.exists(old_photo_path):
                        os.remove(old_photo_path)
                
                # Delete old generated files
                if student.generated_filename and student.generated_filename != pdf_name:
                     # Check if old filename stored was PDF or JPG and clean up both variants
                    base_old = os.path.splitext(student.generated_filename)[0]
                    for ext in ['.pdf', '.jpg']:
                        old_file_path = os.path.join(GENERATED_FOLDER, base_old + ext)
                        if os.path.exists(old_file_path):
                            os.remove(old_file_path)
                
                student.name = name
                student.father_name = father_name
                student.class_name = class_name
                student.dob = dob
                student.address = address
                student.phone = phone
                student.photo_filename = photo_stored
                # Store Cloudinary URLs instead of local filenames
                student.image_url = jpg_url
                student.pdf_url = pdf_url
                student.data_hash = data_hash
                student.template_id = template_id
                student.school_name = school_name
                student.custom_data = custom_data # <--- SAVE DYNAMIC FIELDS
                
                db.session.commit()
                
                success = "ID card updated successfully"
                form_data['photo_filename'] = photo_stored
                form_data['generated_filename'] = jpg_name
                form_data['custom_data'] = custom_data
                
            except Exception as e:
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


@app.route("/admin", methods=["GET"])
def admin():
    if 'student_email' not in session and not session.get("admin"):
        flash('Please log in to access the admin panel.', 'error')
        return redirect(url_for('student_login'))
    
    success = request.args.get("success")
    error = request.args.get("error")
    
    try:
        if session.get("admin"):
            # Admin sees all records
            rows = db.session.query(Student).order_by(Student.created_at.desc()).all()
        else:
            # Students see only their records
            rows = db.session.query(Student).filter_by(
                email=session['student_email']
            ).order_by(Student.created_at.desc()).all()
        
        # FIX: Make sure get_templates() returns a list
        templates_list = get_templates()
        
        # Log for debugging
        logger.info(f"Admin panel loaded - User: {session.get('student_email') or 'admin'}, Records found: {len(rows)}, Templates: {len(templates_list)}")
        
        available_fonts = get_available_fonts()
        
        # FIX: Handle empty templates list
        if templates_list and isinstance(templates_list, list) and len(templates_list) > 0:
            current_settings = templates_list[0]['font_settings'] if 'font_settings' in templates_list[0] else get_default_font_config()
            photo_settings = templates_list[0]['photo_settings'] if 'photo_settings' in templates_list[0] else get_default_photo_config()
            qr_settings = templates_list[0]['qr_settings'] if 'qr_settings' in templates_list[0] else get_default_qr_config()
        else:
            current_settings = get_default_font_config()
            photo_settings = get_default_photo_config()
            qr_settings = get_default_qr_config()
        
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
            template_id = row.template_id
            if template_id not in template_rows:
                template_rows[template_id] = []
            template_rows[template_id].append(row)
        
        schools_data = {}
        for row in rows:
            school_name = row.school_name or "Unknown School"
            if school_name not in schools_data:
                schools_data[school_name] = []
            schools_data[school_name].append(row)
        
        # Calculate arrangement info for each template
        template_arrangements = {}
        for template in templates_list:
            orientation = template.get('card_orientation', 'landscape')
            if orientation == 'landscape':
                template_arrangements[template['id']] = "2 columns × 5 rows (10 cards/A4)"
            else:
                template_arrangements[template['id']] = "5 columns × 2 rows (10 cards/A4)"
        
        logger.info("Admin panel loaded successfully")
        return render_template(
            "admin.html",
            rows=rows,
            templates=templates_list,  # Use the list variable
            template_rows=template_rows,
            available_fonts=available_fonts,
            current_settings=current_settings,
            photo_settings=photo_settings,
            qr_settings=qr_settings,
            duplicate_settings=duplicate_settings,
            pdf_sheets=pdf_sheets,
            pdf_sheets_by_template=pdf_sheets_by_template,
            success=success,
            error=error,
            schools_data=schools_data,
            is_admin=session.get("admin", False),
            template_arrangements=template_arrangements
        )
    except Exception as e:
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
            qr_settings={},
            duplicate_settings={},
            pdf_sheets=[],
            pdf_sheets_by_template=defaultdict(list),
            schools_data={},
            is_admin=session.get("admin", False),
            template_arrangements={}
        ), 500
  

# ================== Compile School Sheets Route ==================
@app.route("/admin/download_compiled_school_pdf/<int:template_id>")
def download_compiled_school_pdf(template_id):
    if not session.get("admin"):
        return redirect(url_for("login"))

    try:
        template = db.session.get(Template, template_id)
        if not template:
            return redirect(url_for("admin", error="Template not found"))
            
        students = Student.query.filter_by(template_id=template_id).all()
        if not students:
            return redirect(url_for("admin", error="No students found"))

        buffer = io.BytesIO()

        # =========================================================
        # DYNAMIC SIZE CALCULATION (UNCHANGED)
        # =========================================================
        sheet_w_px = template.sheet_width if template.sheet_width else 2480
        sheet_h_px = template.sheet_height if template.sheet_height else 3508
        
        card_w_px = template.card_width if template.card_width else 1015
        card_h_px = template.card_height if template.card_height else 661

        scale = 72 / 300
        sheet_w_pt = sheet_w_px * scale
        sheet_h_pt = sheet_h_px * scale
        card_w_pt = card_w_px * scale
        card_h_pt = card_h_px * scale
        gap = 10 * scale

        c = canvas.Canvas(buffer, pagesize=(sheet_w_pt, sheet_h_pt))

        cols = template.grid_cols if template.grid_cols else 2
        rows = template.grid_rows if template.grid_rows else 5
        cards_per_sheet = cols * rows

        total_grid_w = (cols * card_w_pt) + ((cols - 1) * gap)
        total_grid_h = (rows * card_h_pt) + ((rows - 1) * gap)

        start_x = (sheet_w_pt - total_grid_w) / 2
        start_y = (sheet_h_pt - total_grid_h) / 2

        # =========================================================
        card_count = 0

        from reportlab.lib.utils import ImageReader

        for student in students:
            if not student.image_url:
                continue

            try:
                response = requests.get(student.image_url, timeout=10)
                response.raise_for_status()

                pil_img = Image.open(io.BytesIO(response.content)).convert("RGB")

                img_buffer = io.BytesIO()
                pil_img.save(img_buffer, format="PNG")
                img_buffer.seek(0)

                img_reader = ImageReader(img_buffer)

            except Exception as e:
                logger.warning(
                    f"Failed to fetch image for student {student.id} from Cloudinary: {e}"
                )
                continue

            idx = card_count % cards_per_sheet
            col = idx % cols
            row = idx // cols

            x = start_x + (col * (card_w_pt + gap))
            grid_top_y = start_y + total_grid_h
            y = grid_top_y - ((row + 1) * card_h_pt) - (row * gap)

            try:
                c.drawImage(
                    img_reader,
                    x,
                    y,
                    width=card_w_pt,
                    height=card_h_pt,
                    preserveAspectRatio=True,
                    mask="auto"
                )
            except Exception as e:
                logger.error(f"Error drawing card {student.id}: {e}")

            card_count += 1

            if card_count % cards_per_sheet == 0:
                c.showPage()

        c.save()
        buffer.seek(0)

        filename = f"FULL_EXPORT_{secure_filename(template.school_name)}_{datetime.now().strftime('%Y%m%d')}.pdf"
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf"
        )

    except Exception as e:
        logger.exception("Compile PDF failed")
        return redirect(url_for("admin", error=f"Compile Error: {str(e)}"))

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
    
    # Capture Language Inputs
    language = request.form.get('language', 'english')
    text_direction = request.form.get('text_direction', 'ltr')

    if file and file.filename.lower().endswith(('.pdf', '.jpg', '.jpeg', '.png')):
        filename = secure_filename(file.filename)
        
        try:
            # Read file to bytes
            file_bytes = io.BytesIO()
            file.save(file_bytes)
            file_bytes.seek(0)
            
            # Upload to Cloudinary
            template_url = upload_image(file_bytes.getvalue(), folder='id_card_templates', resource_type='raw' if filename.lower().endswith('.pdf') else 'image')
            
            if not template_url:
                raise Exception("Failed to upload template to Cloudinary")
            
            # Create template record with Cloudinary URL (no local filename)
            template_id = add_template_cloudinary(template_url, school_name, card_orientation, language, text_direction)
            
            logger.info(f"Template uploaded to Cloudinary: {filename} for school: {school_name} with orientation {card_orientation}")
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
        # Check if request is JSON or form data
        if request.is_json:
            data = request.get_json()
            template_id = int(data.get("template_id"))
        else:
            # Fall back to form data for backward compatibility
            template_id = int(request.form.get("template_id"))
            data = request.form
        
        # Retrieve current template from the database
        template = db.session.get(Template, template_id)

        if not template:
            if request.is_json:
                return jsonify({'success': False, 'error': 'Template not found'}), 404
            return redirect(url_for("admin", error="Template not found"))
        
        current_orientation = template.card_orientation if template.card_orientation else 'landscape'
        
        # Get card_orientation
        if request.is_json:
            card_orientation = data.get("card_orientation", current_orientation)
        else:
            card_orientation = request.form.get("card_orientation", current_orientation)
        
        # --- Handle Deadline ---
        deadline_str = None
        if request.is_json:
            deadline_str = data.get("deadline")
        else:
            deadline_str = request.form.get("deadline")
            
        if deadline_str:
            try:
                # This format matches the HTML <input type="datetime-local">
                template.deadline = datetime.strptime(deadline_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                pass 
        else:
            # If the field was present but empty, clear the deadline
            if request.is_json and "deadline" in data:
                template.deadline = None
            elif "deadline" in request.form:
                template.deadline = None

        # --- ADD THIS LOGIC TO SAVE LANGUAGE ---
        if request.is_json:
            template.language = data.get("language", "english")
            template.text_direction = data.get("text_direction", "ltr")
        else:
            template.language = request.form.get("language", "english")
            template.text_direction = request.form.get("text_direction", "ltr")

        
        # Helper function to safely get integer values
        def safe_get_int(source, key, default):
            if source is None:
                return default
            value = source.get(key)
            if value is None or value == '':
                return default
            try:
                return int(value)
            except (ValueError, TypeError):
                return default
        
        # Helper function to safely get nested value
        def safe_get_nested(source, *keys, default=None):
            if source is None:
                return default
            current = source
            for key in keys:
                if isinstance(current, dict):
                    current = current.get(key)
                else:
                    return default
                if current is None:
                    return default
            return current
        
        # --- SMART DIMENSION HANDLING ---
        # 1. Get raw values
        if request.is_json:
            raw_w = safe_get_int(data, "card_width", 1015)
            raw_h = safe_get_int(data, "card_height", 661)
        else:
            raw_w = get_form_int(request.form, "card_width", 1015)
            raw_h = get_form_int(request.form, "card_height", 661)
        
        # 2. Logic: Auto-swap dimensions if orientation mismatches
        if card_orientation == 'portrait' and raw_w > raw_h:
            final_w, final_h = raw_h, raw_w 
        elif card_orientation == 'landscape' and raw_h > raw_w:
            final_w, final_h = raw_h, raw_w 
        else:
            final_w, final_h = raw_w, raw_h 
        
        card_dims = { 'width': final_w, 'height': final_h }
        
        # Get sheet dimensions
        if request.is_json:
            sheet_dims = {
                'width': safe_get_int(data, "sheet_width", 2480),
                'height': safe_get_int(data, "sheet_height", 3508)
            }
        else:
            sheet_dims = {
                'width': get_form_int(request.form, "sheet_width", 2480),
                'height': get_form_int(request.form, "sheet_height", 3508)
            }
        
        # --- CAPTURE GRID LAYOUT ---
        if request.is_json:
            grid_layout = {
                'rows': safe_get_int(data, "grid_rows", 5),
                'cols': safe_get_int(data, "grid_cols", 2)
            }
        else:
            grid_layout = {
                'rows': get_form_int(request.form, "grid_rows", 5),
                'cols': get_form_int(request.form, "grid_cols", 2)
            }
        
        # Define settings defaults based on the NEW orientation
        is_landscape = (card_orientation == 'landscape')
        
        default_label_size = 40 if is_landscape else 32
        default_value_size = 36 if is_landscape else 28
        default_label_x = 50 if is_landscape else 40
        default_value_x = 280 if is_landscape else 200
        default_start_y = 275 if is_landscape else 120
        default_line_height = 50 if is_landscape else 45
        
        # Get font settings
        if request.is_json:
            font_settings_data = data.get("font_settings", {}) or {}
            font_settings = {
                "font_bold": safe_get_nested(font_settings_data, "font_bold", default="arialbd.ttf"),
                "font_regular": safe_get_nested(font_settings_data, "font_regular", default="arial.ttf"),
                "label_font_size": safe_get_int(font_settings_data, "label_font_size", default_label_size),
                "value_font_size": safe_get_int(font_settings_data, "value_font_size", default_value_size),
                "label_x": safe_get_int(font_settings_data, "label_x", default_label_x),
                "value_x": safe_get_int(font_settings_data, "value_x", default_value_x),
                "start_y": safe_get_int(font_settings_data, "start_y", default_start_y),
                "line_height": safe_get_int(font_settings_data, "line_height", default_line_height),
                "text_case": safe_get_nested(font_settings_data, "text_case", default="normal")
            }
            
            # Handle font colors from JSON with validation
            def validate_color(color, default=[0, 0, 0]):
                if not color:
                    return default
                if isinstance(color, list) and len(color) >= 3:
                    try:
                        # Ensure all values are integers between 0-255
                        return [
                            max(0, min(255, int(color[0] if color[0] is not None else 0))),
                            max(0, min(255, int(color[1] if color[1] is not None else 0))),
                            max(0, min(255, int(color[2] if color[2] is not None else 0)))
                        ]
                    except (ValueError, TypeError):
                        return default
                elif isinstance(color, str):
                    # Handle string format
                    color = color.strip()
                    if color.startswith("#"):
                        try:
                            h = color.lstrip('#')
                            return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
                        except:
                            return default
                    elif ',' in color:
                        try:
                            parts = color.split(',')
                            if len(parts) >= 3:
                                return [int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip())]
                        except:
                            return default
                return default
            
            font_settings["label_font_color"] = validate_color(
                safe_get_nested(font_settings_data, "label_font_color", default=[0, 0, 0])
            )
            font_settings["value_font_color"] = validate_color(
                safe_get_nested(font_settings_data, "value_font_color", default=[0, 0, 0])
            )
        else:
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
            
            # Handle font colors from form data
            label_font_color = request.form.get("label_font_color", "0,0,0").strip()
            value_font_color = request.form.get("value_font_color", "0,0,0").strip()
            
            # Robust Color Parser
            def parse_color(c):
                # If it's already a list (e.g. from JSON), validate and return it
                if isinstance(c, list):
                    if len(c) >= 3:
                        try:
                            return [int(x) for x in c[:3]]
                        except:
                            pass
                    return [0, 0, 0]
                    
                # If it's a string, parse it
                if not c: return [0, 0, 0]
                
                if isinstance(c, str):
                    c = c.strip()
                    # Handle Hex
                    if c.startswith("#") and len(c) == 7:
                        try:
                            h = c.lstrip('#')
                            return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
                        except:
                            return [0, 0, 0]
                    # Handle "r,g,b" string
                    try:
                        parts = c.split(',')
                        if len(parts) >= 3:
                            return [int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip())]
                    except:
                        pass
                
                return [0, 0, 0]
            
            font_settings["label_font_color"] = parse_color(label_font_color)
            font_settings["value_font_color"] = parse_color(value_font_color)
        
        # Define photo settings defaults
        default_photo_x = 725 if is_landscape else 100
        default_photo_y = 200 if is_landscape else 400
        default_photo_width = 260 if is_landscape else 216
        default_photo_height = 313 if is_landscape else 180
        
        # Get photo settings
        if request.is_json:
            photo_settings_data = data.get("photo_settings", {}) or {}
            photo_settings = {
                "photo_x": safe_get_int(photo_settings_data, "photo_x", default_photo_x),
                "photo_y": safe_get_int(photo_settings_data, "photo_y", default_photo_y),
                "photo_width": safe_get_int(photo_settings_data, "photo_width", default_photo_width),
                "photo_height": safe_get_int(photo_settings_data, "photo_height", default_photo_height),
                "photo_border_top_left": safe_get_int(photo_settings_data, "photo_border_top_left", 0),
                "photo_border_top_right": safe_get_int(photo_settings_data, "photo_border_top_right", 0),
                "photo_border_bottom_right": safe_get_int(photo_settings_data, "photo_border_bottom_right", 0),
                "photo_border_bottom_left": safe_get_int(photo_settings_data, "photo_border_bottom_left", 0),
                "remove_background": photo_settings_data.get("remove_background", False) == True,
                "bg_remove_color": photo_settings_data.get("bg_remove_color", "#ffffff")
            }
        else:
            # Form data path (main admin panel)
            photo_settings = {
                "photo_x": get_form_int(request.form, "photo_x", default_photo_x),
                "photo_y": get_form_int(request.form, "photo_y", default_photo_y),
                "photo_width": get_form_int(request.form, "photo_width", default_photo_width),
                "photo_height": get_form_int(request.form, "photo_height", default_photo_height),
                "photo_border_top_left": get_form_int(request.form, "photo_border_top_left", 0),
                "photo_border_top_right": get_form_int(request.form, "photo_border_top_right", 0),
                "photo_border_bottom_right": get_form_int(request.form, "photo_border_bottom_right", 0),
                "photo_border_bottom_left": get_form_int(request.form, "photo_border_bottom_left", 0),
                "remove_background": 'remove_background' in request.form,  # ← Checkbox handling
                "bg_remove_color": request.form.get("bg_color", "#ffffff")
            }
        
        # QR Settings
        qr_settings = get_default_qr_config()
        
        if request.is_json:
            qr_data = data.get("qr_settings", {}) or {}
            if qr_data.get("enable_qr"):
                qr_settings["enable_qr"] = True
                qr_settings["qr_x"] = safe_get_int(qr_data, "qr_x", 50)
                qr_settings["qr_y"] = safe_get_int(qr_data, "qr_y", 50)
                qr_settings["qr_size"] = safe_get_int(qr_data, "qr_size", 120)
                qr_settings["qr_style"] = qr_data.get("qr_style", "square")
                qr_settings["qr_border"] = safe_get_int(qr_data, "qr_border", 2)
                
                qr_settings["qr_fill_color"] = validate_color(
                    safe_get_nested(qr_data, "qr_fill_color", default=[0, 0, 0])
                )
                qr_settings["qr_back_color"] = validate_color(
                    safe_get_nested(qr_data, "qr_back_color", default=[255, 255, 255])
                )
                
                qr_settings["qr_data_type"] = qr_data.get("qr_data_type", "student_id")
                if qr_settings["qr_data_type"] == "text":
                    qr_settings["qr_custom_text"] = qr_data.get("qr_custom_text", "")
                elif qr_settings["qr_data_type"] == "url":
                    qr_settings["qr_base_url"] = qr_data.get("qr_base_url", "https://example.com/verify/")
                
                qr_settings["qr_include_logo"] = bool(qr_data.get("qr_include_logo", False))
                if qr_settings["qr_include_logo"]:
                    qr_settings["qr_logo_path"] = qr_data.get("qr_logo_path", "")
        else:
            if request.form.get("enable_qr"):
                qr_settings["enable_qr"] = True
                qr_settings["qr_x"] = get_form_int(request.form, "qr_x", 50)
                qr_settings["qr_y"] = get_form_int(request.form, "qr_y", 50)
                qr_settings["qr_size"] = get_form_int(request.form, "qr_size", 120)
                qr_settings["qr_style"] = request.form.get("qr_style", "square")
                qr_settings["qr_border"] = get_form_int(request.form, "qr_border", 2)
                
                qr_fill_color = request.form.get("qr_fill_color", "#000000").strip()
                qr_back_color = request.form.get("qr_back_color", "#FFFFFF").strip()
                qr_settings["qr_fill_color"] = parse_color(qr_fill_color)
                qr_settings["qr_back_color"] = parse_color(qr_back_color)
                
                qr_settings["qr_data_type"] = request.form.get("qr_data_type", "student_id")
                if qr_settings["qr_data_type"] == "text":
                    qr_settings["qr_custom_text"] = request.form.get("qr_custom_text", "")
                elif qr_settings["qr_data_type"] == "url":
                    qr_settings["qr_base_url"] = request.form.get("qr_base_url", "https://example.com/verify/")
                if request.form.get("qr_include_logo"):
                    qr_settings["qr_include_logo"] = True
                    qr_settings["qr_logo_path"] = request.form.get("qr_logo_path", "")
        
        # Merge defaults
        default_font = get_default_font_config()
        default_photo = get_default_photo_config()
        default_qr = get_default_qr_config()
        font_settings = {**default_font, **font_settings}
        photo_settings = {**default_photo, **photo_settings}
        qr_settings = {**default_qr, **qr_settings}
        
        # Adjust settings for orientation
        try:
            font_settings = get_font_settings_for_orientation(template_id, font_settings)
            photo_settings = get_photo_settings_for_orientation(template_id, photo_settings)
        except Exception as e:
            logger.warning(f"Error adjusting settings for orientation: {e}")
            # Continue with current settings if adjustment fails
        
        # Update template settings in the database
        try:
            update_template_settings(
                template_id, 
                font_settings, 
                photo_settings, 
                qr_settings, 
                card_orientation,
                card_dims=card_dims, 
                sheet_dims=sheet_dims,
                grid_layout=grid_layout
            )
        except Exception as e:
            logger.error(f"Error in update_template_settings: {e}")
            if request.is_json:
                return jsonify({'success': False, 'error': f'Failed to update template settings: {str(e)}'}), 500
            return redirect(url_for("admin", error=f"Failed to update template settings: {str(e)}"))
        
        db.session.commit()
        
        # Log info
        arrangement = f"{final_w}x{final_h}px ({grid_layout['cols']} cols x {grid_layout['rows']} rows)"
        logger.info(f"Updated template {template_id} with {card_orientation} orientation: {arrangement}")
        
        if request.is_json:
            return jsonify({
                'success': True, 
                'message': f'Template settings updated successfully - {arrangement}',
                'arrangement': arrangement
            })
        else:
            return redirect(url_for("admin", success=f"Template settings updated successfully - {arrangement}"))
        
    except ValueError as e:
        logger.error(f"Invalid input in template settings: {e}")
        error_msg = f"Invalid input in template settings: {str(e)}"
        if request.is_json:
            return jsonify({'success': False, 'error': error_msg}), 400
        return redirect(url_for("admin", error=error_msg))
    except Exception as e:
        logger.error(f"Error updating template settings: {e}")
        error_msg = f"Error updating template settings: {str(e)}"
        if request.is_json:
            return jsonify({'success': False, 'error': error_msg}), 500
        return redirect(url_for("admin", error=error_msg))
    
@app.template_filter('rgb_to_hex')
def rgb_to_hex(rgb_list):
    if isinstance(rgb_list, list) and len(rgb_list) == 3:
        try:
            clamped = [max(0, min(255, int(c))) for c in rgb_list]
            return '#{:02x}{:02x}{:02x}'.format(*clamped)
        except (ValueError, TypeError):
            pass
    return '#000000'

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
                "text_case": request.form.get("text_case", "normal") # Added text case
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
        # Delete all students
        Student.query.delete()
        # Delete all templates
        Template.query.delete()
        db.session.commit()
        
        # Clean up files
        for folder in [UPLOAD_FOLDER, GENERATED_FOLDER]:
            for file in os.listdir(folder):
                file_path = os.path.join(folder, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
        if os.path.exists(TEMPLATES_CONFIG):
            os.remove(TEMPLATES_CONFIG)
        logger.info("All data and files deleted successfully")
        return redirect(url_for("admin", success="All data and files deleted successfully"))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting all data: {e}")
        return redirect(url_for("admin", error=f"Error deleting data: {str(e)}"))

@app.route("/delete_student/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    if not session.get("admin"):
        return redirect(url_for('student_login'))

    try:
        student = db.session.get(Student, student_id)
        if not student:
            return redirect(url_for("admin", error="Student not found"))
        
        # Save name for logging before deletion
        student_name = student.name

        # Delete associated files
        if student.photo_filename and student.photo_filename != "placeholder.jpg":
            photo_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
            if os.path.exists(photo_path):
                try:
                    os.remove(photo_path)
                    logger.info(f"Deleted photo: {photo_path}")
                except Exception as e:
                    logger.warning(f"Could not delete photo file: {e}")

        if student.generated_filename:
            # Try deleting both PDF and JPG versions just in case
            for ext in ['.pdf', '.jpg']:
                # Handle filename replacement safely
                base_name = os.path.splitext(student.generated_filename)[0]
                generated_path = os.path.join(GENERATED_FOLDER, base_name + ext)
                
                if os.path.exists(generated_path):
                    try:
                        os.remove(generated_path)
                        logger.info(f"Deleted generated file: {generated_path}")
                    except Exception as e:
                        logger.warning(f"Could not delete generated file: {e}")
        
        # Delete record from Database
        db.session.delete(student)
        db.session.commit()
        
        # --- LOG ACTIVITY ---
        log_activity("Deleted Student", target=f"ID {student_id}", details=f"Name: {student_name}")
        # --------------------

        logger.info(f"Deleted student ID {student_id}")
        return redirect(url_for("admin", success="Student deleted successfully"))

    except Exception as e:
        db.session.rollback()
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
        import csv
        import io
        
        students = Student.query.all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Name", "Father's Name", "Class", "DOB", "Address", "Phone", "Photo Filename", "Generated Filename", "Created At", "Template ID", "School Name", "Email"])
        
        for student in students:
            writer.writerow([
                student.id, student.name, student.father_name, student.class_name,
                student.dob, student.address, student.phone, student.photo_filename,
                student.generated_filename, student.created_at, student.template_id,
                student.school_name, student.email
            ])
        
        logger.info("Exported student data to CSV")
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=students_export.csv"}
        )
    except Exception as e:
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
        template = db.session.get(Template, template_id)


        if not template:
            return redirect(url_for("admin", error="Template not found"))
        
        template_path = get_template_path(template_id)
        
        # Delete template
        db.session.delete(template)
        
        # Update students with this template
        Student.query.filter_by(template_id=template_id).update({
            'template_id': None,
            'school_name': None
        })
        
        db.session.commit()
        
        if template_path and os.path.exists(template_path):
            os.remove(template_path)
            logger.info(f"Removed template file: {template_path}")
        
        logger.info(f"Removed template ID {template_id}")
        return redirect(url_for("admin", success="Template removed successfully"))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error removing template {template_id}: {e}")
        return redirect(url_for("admin", error=f"Error removing template: {str(e)}"))

# ================== FULL ADMIN PREVIEW ROUTE (Memory Only - No Files) ==================
@app.route("/admin_preview_card", methods=["POST"])
def admin_preview_card():
    try:
        # 1. Get Settings
        data = request.get_json()
        if not data: return jsonify({"success": False, "error": "No data received"}), 400
        template_id = data.get("template_id")
        
        font_settings = {**get_default_font_config(), **data.get("font_settings", {})}
        photo_settings = {**get_default_photo_config(), **data.get("photo_settings", {})}
        qr_settings = {**get_default_qr_config(), **data.get("qr_settings", {})}
        
        # 2. Load Template
        template_path = get_template_path(template_id)
        if not template_path:
            return jsonify({"success": False, "error": "Template not found in database"}), 404
        
        try:
            card_width, card_height = get_card_size(template_id)
            template_img = load_template_smart(template_path).resize((card_width, card_height))
        except Exception as e:
            logger.error(f"Error loading template {template_id} from {template_path}: {e}")
            return jsonify({"success": False, "error": f"Failed to load template: {str(e)}"}), 500
        
        draw = ImageDraw.Draw(template_img)
        
        # --- NEW: ROBUST FONT LOADING LOGIC ---
        def load_safe_font(font_name, size, lang='english'):
            """
            Tries to load the requested font.
            If that fails, searches the FONTS_FOLDER for ANY font that supports Urdu/Arabic.
            """
            # 1. Try the requested font
            primary_path = os.path.join(FONTS_FOLDER, font_name)
            if os.path.exists(primary_path):
                try:
                    return ImageFont.truetype(primary_path, size)
                except:
                    pass # Failed to load, try fallbacks

            # 2. If Language is Urdu/Arabic, search for specific fallbacks in folder
            if lang in ['urdu', 'arabic']:
                # List of common Urdu font filenames to check automatically
                fallbacks = ['JameelNooriNastaleeq.ttf', 'NotoNastaliqUrdu-Regular.ttf', 'Amiri-Regular.ttf', 'arial.ttf']
                
                # Also check any .ttf file currently in the folder
                if os.path.exists(FONTS_FOLDER):
                    available = [f for f in os.listdir(FONTS_FOLDER) if f.endswith('.ttf')]
                    fallbacks = available + fallbacks # Prioritize what's actually there

                for fb in fallbacks:
                    fb_path = os.path.join(FONTS_FOLDER, fb)
                    if os.path.exists(fb_path):
                        try:
                            # print(f"Fallback font loaded: {fb}") # Debuging
                            return ImageFont.truetype(fb_path, size)
                        except: continue
            
            # 3. Last Resort: Default (Will show Tofu boxes for Urdu)
            return ImageFont.load_default()

        try:
            L_COLOR = tuple(font_settings.get("label_font_color", [0,0,0]))
            V_COLOR = tuple(font_settings.get("value_font_color", [0,0,0]))
        except: L_COLOR = V_COLOR = (0, 0, 0)
        
        # --- LANGUAGE LOGIC ---
        template = db.session.get(Template, template_id)
        if template:
            lang = getattr(template, 'language', 'english') or 'english'
            direction = getattr(template, 'text_direction', 'ltr') or 'ltr'
        else:
            lang = 'english'
            direction = 'ltr'

        # Standard Labels
        std_labels = {
            'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
            'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
            'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
            'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
        }
        
        sample_data_map = {
            'english': {'NAME': 'John Doe', 'F_NAME': 'Richard Roe', 'CLASS': 'X - A', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': '123 Long Street Name, Apartment 4B, Big City District, State 560001'},
            'urdu':    {'NAME': 'محمد علی', 'F_NAME': 'احمد علی', 'CLASS': 'دہم - اے', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': 'مکان نمبر 123، سٹریٹ 4، لاہور، پاکستان'},
            'hindi':   {'NAME': 'राहुल कुमार', 'F_NAME': 'अमित कुमार', 'CLASS': '१० - ए', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': 'मकान नं. १२३, गली नं. ४, दिल्ली'},
            'arabic':  {'NAME': 'محمد أحمد', 'F_NAME': 'علي أحمد', 'CLASS': 'العاشر - أ', 'DOB': '01-01-2010', 'MOBILE': '9876543210', 'ADDRESS': 'شارع الملك فيصل، مبنى ٤، الرياض'}
        }

        labels_map = std_labels.get(lang, std_labels['english'])
        values_map = sample_data_map.get(lang, sample_data_map['english'])

        # 4. Prepare Fields
        text_case = font_settings.get("text_case", "normal")
        all_fields = [
            {'label': labels_map['NAME'], 'val': values_map['NAME'], 'order': 10},
            {'label': labels_map['F_NAME'], 'val': values_map['F_NAME'], 'order': 20},
            {'label': labels_map['CLASS'], 'val': values_map['CLASS'], 'order': 30},
            {'label': labels_map['DOB'], 'val': values_map['DOB'], 'order': 40},
            {'label': labels_map['MOBILE'], 'val': values_map['MOBILE'], 'order': 50},
            {'label': labels_map['ADDRESS'], 'val': values_map['ADDRESS'], 'order': 60}
        ]
        
        if template:
            try:
                for field in TemplateField.query.filter_by(template_id=template_id).all():
                    sample_val = f"Sample {field.field_label}"
                    if lang == 'urdu': sample_val = f"نمونہ {field.field_label}"
                    elif lang == 'hindi': sample_val = f"नमूना {field.field_label}"
                    elif lang == 'arabic': sample_val = f"عينة {field.field_label}"

                    all_fields.append({
                        'label': field.field_label,
                        'val': sample_val,
                        'order': field.display_order
                    })
            except: pass

        all_fields.sort(key=lambda x: x['order'])
        
        # --- PRE-CALCULATE ---
        p_x = photo_settings.get("photo_x", 0)
        p_y = photo_settings.get("photo_y", 0)
        p_h = photo_settings.get("photo_height", 0)
        p_bottom = p_y + p_h

        # Draw Loop
        label_x = font_settings["label_x"]
        value_x = font_settings["value_x"]
        current_y = font_settings["start_y"]
        line_height = font_settings["line_height"]

        for item in all_fields:
            if current_y > card_height - 20: break
            
            raw_label = item['label']
            if direction == 'rtl':
                raw_val = item['val']
            else:
                raw_val = apply_text_case(item['val'], text_case)

            # --- TEXT SHAPING ---
            label_dir = 'R' if direction == 'rtl' else 'L'
            display_label = process_text_for_drawing(raw_label, lang, base_dir=label_dir)
            display_val = process_text_for_drawing(raw_val, lang, base_dir='L')

            # --- LOAD FONT SAFELY ---
            # We call the new safe loader here
            l_font = load_safe_font(font_settings.get("font_bold", "arialbd.ttf"), font_settings["label_font_size"], lang)

            # --- DRAWING ---
            if direction == 'rtl':
                label_text_final = ":" + display_label
            else:
                label_text_final = display_label + ":"
                
            draw.text((label_x, current_y), label_text_final, font=l_font, fill=L_COLOR)

            # --- DYNAMIC WIDTH ---
            is_vertically_overlapping = (current_y < p_bottom) and ((current_y + line_height) > p_y)
            if is_vertically_overlapping and (p_x > value_x):
                 max_w = p_x - value_x - 15
            else:
                 max_w = card_width - value_x - 20

            # --- ADDRESS LOGIC ---
            if item['label'] == labels_map['ADDRESS']:
                curr_size = font_settings["value_font_size"]
                min_size = 12 
                wrapped_addr = []
                while curr_size >= min_size:
                    avg_char_w = curr_size * 0.55
                    chars_limit = int(max_w / avg_char_w)
                    if chars_limit < 5: chars_limit = 5 
                    wrapped_addr = textwrap.wrap(raw_val, width=chars_limit, break_long_words=True)
                    if len(wrapped_addr) <= 2: break
                    curr_size -= 2
                
                # Load safe font for address
                addr_font = load_safe_font(font_settings.get("font_regular", "arial.ttf"), curr_size, lang)

                for line in wrapped_addr[:2]:
                    line_display = process_text_for_drawing(line, lang, base_dir='L')
                    draw.text((value_x, current_y), line_display, font=addr_font, fill=V_COLOR)
                    spacing = line_height if curr_size > 20 else curr_size + 5
                    current_y += spacing

            # --- STANDARD FIELDS ---
            else:
                # Load safe font for standard fields
                v_font = load_safe_font(font_settings.get("font_regular", "arial.ttf"), font_settings["value_font_size"], lang)
                
                # Note: Dynamic sizing logic is slightly harder with fallback fonts, 
                # simplified here to use the loaded safe font directly to ensure rendering.
                # If resizing is needed, we'd loop through sizes calling load_safe_font each time.
                
                text_len = v_font.getlength(raw_val)

                if text_len > max_w:
                    avg_char_w = font_settings["value_font_size"] * 0.55
                    chars_limit = int(max_w / avg_char_w)
                    wrapped = textwrap.wrap(raw_val, width=chars_limit, break_long_words=True)
                    
                    for line in wrapped:
                        line_display = process_text_for_drawing(line, lang, base_dir='L')
                        draw.text((value_x, current_y), line_display, font=v_font, fill=V_COLOR)
                        current_y += line_height
                else:
                    draw.text((value_x, current_y), display_val, font=v_font, fill=V_COLOR)
                    current_y += line_height
                        
        # 6. Photo & QR Placeholders
        try:
            if not os.path.exists(PLACEHOLDER_PATH):
                ph = Image.new("RGB", (100, 100), color="gray")
            else:
                ph = Image.open(PLACEHOLDER_PATH).convert("RGBA")
            ph = ph.resize((photo_settings["photo_width"], photo_settings["photo_height"]))
            radii = [photo_settings.get(f"photo_border_{k}", 0) for k in ["top_left", "top_right", "bottom_right", "bottom_left"]]
            ph = round_photo(ph, radii)
            template_img.paste(ph, (photo_settings["photo_x"], photo_settings["photo_y"]), ph)
        except: pass
        
        if qr_settings.get("enable_qr"):
            try:
                qr_img = generate_qr_code("PREVIEW", qr_settings, qr_settings.get("qr_size", 120))
                qr_img = qr_img.resize((qr_settings.get("qr_size", 120),)*2)
                template_img.paste(qr_img, (qr_settings.get("qr_x", 50), qr_settings.get("qr_y", 50)))
            except: pass
        template_img = force_rgb(template_img)
        logger.warning(f"FINAL IMAGE MODE BEFORE SAVE: {template_img.mode}")
        buffer = io.BytesIO()
        template_img.save(buffer,
                          format="JPEG",
                          quality=95,
                          subsampling=0,
                          optimize=True)
        buffer.seek(0)
        img_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        return jsonify({"success": True, "image_data": f"data:image/jpeg;base64,{img_data}"})
        
    except Exception as e:
        logger.error(f"Admin preview failed: {e}")
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
      
        # Save as PDF to Cloudinary
        pdf_buffer = io.BytesIO()
        sheet.save(pdf_buffer, "PDF", resolution=DPI)
        pdf_buffer.seek(0)
        pdf_url = upload_image(pdf_buffer.getvalue(), folder='bulk-sheets', resource_type='raw')
      
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

@app.route("/debug_color/<int:template_id>")
def debug_color(template_id):
    """Debug route to check color settings"""
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403
  
    try:
        template = db.session.get(Template, template_id)


        if not template:
            return jsonify({"error": "Template not found"}), 404
        
        font_settings = template.font_settings or {}
        return jsonify({
            "template_id": template_id,
            "font_settings": font_settings,
            "label_font_color": font_settings.get('label_font_color'),
            "value_font_color": font_settings.get('value_font_color'),
            "label_font_color_type": type(font_settings.get('label_font_color')).__name__ if 'label_font_color' in font_settings else 'None',
            "value_font_color_type": type(font_settings.get('value_font_color')).__name__ if 'value_font_color' in font_settings else 'None'
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/test_color_render")
def test_color_render():
    """Test route to render colors on an image"""
    try:
        # Create a test image
        test_img = Image.new("RGB", (800, 600), color="white")
        draw = ImageDraw.Draw(test_img)
      
        # Test colors
        colors = [
            ("Red", [255, 0, 0]),
            ("Green", [0, 255, 0]),
            ("Blue", [0, 0, 255]),
            ("Black", [0, 0, 0]),
            ("Gray", [128, 128, 128])
        ]
      
        y = 50
        for name, color in colors:
            draw.text((50, y), f"{name}: {color}", fill=tuple(color))
            y += 40
      
        # Save and return to Cloudinary
        test_buffer = io.BytesIO()
        test_img.save(test_buffer, "JPEG", quality=95)
        test_buffer.seek(0)
        test_url = upload_image(test_buffer.getvalue(), folder='test', resource_type='image')
      
        return jsonify({
            "success": True,
            "message": "Color test completed",
            "test_image": test_url
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/debug_students")
def debug_students():
    if not session.get("admin") and not session.get("student_email"):
        return jsonify({"error": "Unauthorized"}), 401
  
    try:
        if session.get("admin"):
            students = Student.query.order_by(Student.id.desc()).limit(10).all()
        else:
            students = Student.query.filter_by(email=session['student_email']).order_by(Student.id.desc()).limit(10).all()
        
        result = []
        for student in students:
            result.append({
                'id': student.id,
                'name': student.name,
                'email': student.email or 'No email',
                'has_password': bool(student.password),
                'generated_filename': student.generated_filename,
                'created_at': student.created_at.isoformat() if student.created_at else None
            })
        
        return jsonify({
            'user': session.get('student_email') or 'admin',
            'student_count': len(students),
            'recent_students': result
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        template = db.session.get(Template, template_id)


        if not template:
            return jsonify({"error": "Template not found"}), 404
        
        font_settings, photo_settings, qr_settings, orientation = get_template_settings(template_id)
        template_path = get_template_path(template_id)
      
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
            "qr_settings": qr_settings,
            "fonts_folder_exists": os.path.exists(FONTS_FOLDER),
            "available_fonts": available_fonts,
            "font_bold_exists": font_bold_exists,
            "font_regular_exists": font_regular_exists,
            "generated_folder_exists": os.path.exists(GENERATED_FOLDER),
            "upload_folder_exists": os.path.exists(UPLOAD_FOLDER),
            "deadline": template.deadline.isoformat() if template.deadline else None
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

# =========================================================
# BACKGROUND THREAD WORKER (Pure SQLAlchemy)
# =========================================================
def background_bulk_generate(task_id, template_id, excel_path, photo_map):
    """
    Background thread to process bulk generation without blocking the server.
    Uses SQLAlchemy ORM for all database operations.
    """
    with app.app_context():
        try:
            # Update Status: Starting
            jobs[task_id].update({'state': 'PROCESSING', 'status': 'Reading Excel file...'})
            
            # 1. Read Excel File (Pandas)
            if excel_path.endswith('.csv'):
                df = pd.read_csv(excel_path)
            else:
                df = pd.read_excel(excel_path, engine='openpyxl')
                
            # Normalize column names
            df.columns = df.columns.str.strip().str.lower()
            total_records = len(df)
            jobs[task_id]['total'] = total_records
            
            # 2. Fetch Template using SQLAlchemy ORM
            template_obj = db.session.get(Template, template_id)
            if not template_obj:
                raise ValueError("Template not found")

            # Load Settings
            font_settings, photo_settings, qr_settings, orientation = get_template_settings(template_id)
            template_path = get_template_path(template_id)
            card_width, card_height = get_card_size(template_id)
            
            # 3. Fetch Dynamic Fields using SQLAlchemy ORM
            dynamic_fields = TemplateField.query.filter_by(template_id=template_id)\
                                        .order_by(TemplateField.display_order.asc())\
                                        .all()

            # Pre-load Fonts (Performance)
            FONT_BOLD = os.path.join(FONTS_FOLDER, font_settings.get("font_bold", "arialbd.ttf"))
            if not os.path.exists(FONT_BOLD): FONT_BOLD = os.path.join(FONTS_FOLDER, "arialbd.ttf")
            
            FONT_REGULAR = os.path.join(FONTS_FOLDER, font_settings.get("font_regular", "arial.ttf"))
            if not os.path.exists(FONT_REGULAR): FONT_REGULAR = os.path.join(FONTS_FOLDER, "arial.ttf")

            # Colors
            try:
                L_COLOR = tuple(font_settings.get("label_font_color", [0,0,0]))
                V_COLOR = tuple(font_settings.get("value_font_color", [0,0,0]))
            except: L_COLOR = V_COLOR = (0,0,0)

            # --- NEW MULTI-LANGUAGE LOGIC START ---
            # Get Template Language settings
            lang = getattr(template_obj, 'language', 'english') or 'english'
            direction = getattr(template_obj, 'text_direction', 'ltr') or 'ltr'

            # Standard Labels Dictionary
            std_labels = {
                'english': {'NAME': 'NAME', 'F_NAME': 'F.NAME', 'CLASS': 'CLASS', 'DOB': 'D.O.B', 'MOBILE': 'MOBILE', 'ADDRESS': 'ADDRESS'},
                'urdu':    {'NAME': 'نام', 'F_NAME': 'ولدیت', 'CLASS': 'جماعت', 'DOB': 'تاریخ پیدائش', 'MOBILE': 'موبائل', 'ADDRESS': 'پتہ'},
                'hindi':   {'NAME': 'नाम', 'F_NAME': 'पिता का नाम', 'CLASS': 'कक्षा', 'DOB': 'जन्म तिथि', 'MOBILE': 'मोबाइल', 'ADDRESS': 'पता'},
                'arabic':  {'NAME': 'الاسم', 'F_NAME': 'اسم الأب', 'CLASS': 'الصف', 'DOB': 'تاريخ الميلاد', 'MOBILE': 'رقم الهاتف', 'ADDRESS': 'العنوان'}
            }
            labels_map = std_labels.get(lang, std_labels['english'])
            # --- NEW MULTI-LANGUAGE LOGIC END ---

            # Counters
            success_count = 0
            skipped_count = 0
            error_count = 0
            errors = []

            # ---------------- MAIN LOOP ----------------
            for idx, row in df.iterrows():
                # Update Job Progress
                jobs[task_id].update({
                    'current': idx + 1,
                    'status': f"Processing student {idx + 1} of {total_records}..."
                })
                
                try:
                    if row.isnull().all(): continue

                    # --- Extract Basic Data ---
                    name = str(row['name']).strip() if pd.notna(row['name']) else ''
                    if not name: continue 

                    father_name = str(row.get('father_name', '')).strip() if pd.notna(row.get('father_name')) else ''
                    class_name = str(row['class_name']).strip() if pd.notna(row.get('class_name')) else ''
                    dob = str(row.get('dob', '')).strip() if pd.notna(row.get('dob')) else ''
                    address = str(row.get('address', '')).strip() if pd.notna(row.get('address')) else ''
                    phone = str(row.get('phone', '')).strip() if pd.notna(row.get('phone')) else ''

                    # --- Extract Dynamic Data ---
                    custom_data = {}
                    dynamic_display = []
                    field_error = False

                    for field in dynamic_fields:
                        val = ''
                        # Use lowercase field_name to match pandas dataframe columns
                        col_name = field.field_name.lower()
                        if col_name in df.columns and pd.notna(row.get(col_name)):
                            val = str(row[col_name]).strip()
                        
                        # Validate Required Fields
                        if field.is_required and not val:
                            errors.append(f"Row {idx+2}: Missing required field '{field.field_label}'")
                            field_error = True
                            break
                        
                        custom_data[field.field_name] = val
                        dynamic_display.append({
                            'label': field.field_label, # Use raw label from DB (already in correct language if typed manually)
                            'value': val,
                            'order': field.display_order
                        })
                    
                    if field_error:
                        error_count += 1
                        continue

                    # --- Photo Matching Logic ---
                    used_photo = "placeholder.jpg"
                    clean_name = name.lower().strip()
                    
                    # 1. Match by Name
                    if clean_name in photo_map:
                        used_photo = photo_map[clean_name]
                    else:
                        # 2. Match by explicit 'photo' column in Excel
                        for col in ['photo_path', 'photo', 'photo_filename']:
                            if col in df.columns and pd.notna(row.get(col)):
                                ref = str(row[col]).strip().lower()
                                ref_key = os.path.splitext(ref)[0]
                                if ref_key in photo_map:
                                    used_photo = photo_map[ref_key]
                                break
                    
                    # --- Duplicate Check (SQLAlchemy) ---
                    form_data = {
                        'name': name, 'father_name': father_name, 'class_name': class_name,
                        'dob': dob, 'address': address, 'phone': phone, 'template_id': template_id
                    }
                    data_hash = generate_data_hash(form_data, used_photo)
                    
                    # Check DB for existing hash
                    existing = Student.query.filter_by(data_hash=data_hash, template_id=template_id).first()
                    
                    if existing:
                        skipped_count += 1
                        continue 

                    # --- DRAW ID CARD IMAGE ---
                    try:
                        template_img = load_template_smart(template_path).resize((card_width, card_height))
                    except Exception as e:
                        logger.error(f"Error loading template {template_id} in visual editor: {e}")
                        error_count += 1
                        errors.append(f"Row {idx+1}: Failed to load template - {str(e)}")
                        continue
                    
                    draw = ImageDraw.Draw(template_img)
                    text_case = font_settings.get("text_case", "normal")

                    # Prepare Fields List (using Translated Labels)
                    all_fields = [
                        {'label': labels_map['NAME'], 'value': apply_text_case(name, text_case), 'order': 10},
                        {'label': labels_map['F_NAME'], 'value': apply_text_case(father_name, text_case), 'order': 20},
                        {'label': labels_map['CLASS'], 'value': apply_text_case(class_name, text_case), 'order': 30},
                        {'label': labels_map['DOB'], 'value': dob, 'order': 40},
                        {'label': labels_map['MOBILE'], 'value': phone, 'order': 50},
                        {'label': labels_map['ADDRESS'], 'value': apply_text_case(address, text_case), 'order': 60}
                    ]
                    
                    # Add Dynamic Fields
                    for d in dynamic_display:
                        all_fields.append({
                            'label': d['label'], 
                            'value': apply_text_case(d['value'], text_case), 
                            'order': d['order']
                        })
                    all_fields.sort(key=lambda x: x['order'])

                    # --- PRE-CALCULATE PHOTO BOUNDARIES ---
                    p_x = photo_settings.get("photo_x", 0)
                    p_y = photo_settings.get("photo_y", 0)
                    p_h = photo_settings.get("photo_height", 0)
                    p_bottom = p_y + p_h

                    # Layout Settings
                    label_x = font_settings["label_x"]
                    value_x = font_settings["value_x"]
                    current_y = font_settings["start_y"]
                    line_height = font_settings["line_height"]
                    
                    # Draw Loop
                    for item in all_fields:
                        if current_y > card_height - 20: break
                        
                        # Get Raw Values
                        raw_label = item['label']
                        raw_val = item['value']

                        # --- TEXT SHAPING (Crucial Step) ---
                        display_label = process_text_for_drawing(raw_label, lang)
                        display_val = process_text_for_drawing(raw_val, lang)

                        try:
                            l_font = ImageFont.truetype(FONT_BOLD, font_settings["label_font_size"])
                        except:
                            l_font = ImageFont.load_default()

                        # --- DRAWING ---
                        # Handle Label formatting (RTL vs LTR)
                        label_text_final = display_label + ":" if direction == 'ltr' else ":" + display_label
                        draw.text((label_x, current_y), label_text_final, font=l_font, fill=L_COLOR)

                        # --- DYNAMIC WIDTH CALCULATION ---
                        # Check vertical overlap with photo
                        is_vertically_overlapping = (current_y < p_bottom) and ((current_y + line_height) > p_y)

                        if is_vertically_overlapping and (p_x > value_x):
                             max_w = p_x - value_x - 15
                        else:
                             max_w = card_width - value_x - 20
                        
                        # --- ADDRESS LOGIC (SHRINK TO 2 LINES) ---
                        if raw_label == labels_map['ADDRESS']:
                            # Start with defined font size
                            curr_size = font_settings["value_font_size"]
                            min_size = 12
                            wrapped_addr = []
                        
                            while curr_size >= min_size:
                                # Load font at this size
                                try:
                                    v_font = ImageFont.truetype(FONT_REGULAR, curr_size)
                                except:
                                    v_font = ImageFont.load_default()
                        
                                # Pixel-based wrapping
                                words = raw_val.split()
                                lines = []
                                current_line = ""
                        
                                for word in words:
                                    test_line = current_line + (" " if current_line else "") + word
                                    if draw.textlength(test_line, font=v_font) <= max_w:
                                        current_line = test_line
                                    else:
                                        if current_line:
                                            lines.append(current_line)
                                        current_line = word
                        
                                if current_line:
                                    lines.append(current_line)
                        
                                # Stop if it fits in 2 lines
                                if len(lines) <= 2:
                                    wrapped_addr = lines
                                    break
                        
                                curr_size -= 2
                        
                            # Fallback safety
                            if not wrapped_addr:
                                wrapped_addr = lines[:2]
                        
                            # Draw address lines
                            for line in wrapped_addr[:2]:
                                line_display = process_text_for_drawing(line, lang)
                                draw.text((value_x, current_y), line_display, font=v_font, fill=V_COLOR)
                                current_y += max(curr_size + 4, line_height - 6)
                        
                            continue


                        # --- STANDARD FIELDS ---
                        else:
                            # Check standard text length
                            temp_font = load_font_dynamic(FONT_REGULAR, raw_val, 2000, font_settings["value_font_size"])
                            text_len = temp_font.getlength(raw_val)

                            if text_len > max_w:
                                avg_char_w = font_settings["value_font_size"] * 0.55
                                chars_limit = int(max_w / avg_char_w)
                                wrapped = textwrap.wrap(raw_val, width=chars_limit, break_long_words=True)
                                
                                v_font = load_font_dynamic(FONT_REGULAR, max(wrapped, key=len), max_w, font_settings["value_font_size"])
                                
                                for line in wrapped:
                                    line_display = process_text_for_drawing(line, lang)
                                    draw.text((value_x, current_y), line_display, font=v_font, fill=V_COLOR)
                                    current_y += line_height
                            else:
                                v_font = load_font_dynamic(FONT_REGULAR, raw_val, max_w, font_settings["value_font_size"])
                                draw.text((value_x, current_y), display_val, font=v_font, fill=V_COLOR)
                                current_y += line_height

                    # Paste Photo
                    try:
                        ph = None
                        # Check if used_photo is a Cloudinary URL
                        if used_photo.startswith('http'):
                            # Fetch from Cloudinary
                            try:
                                response = requests.get(used_photo, timeout=10)
                                ph = Image.open(io.BytesIO(response.content)).convert("RGBA")
                            except Exception as e:
                                logger.warning(f"Failed to fetch photo from Cloudinary: {e}")
                        elif used_photo != "placeholder.jpg":
                            # Legacy local file fallback
                            p_path = os.path.join(UPLOAD_FOLDER, used_photo)
                            if os.path.exists(p_path):
                                ph = Image.open(p_path).convert("RGBA")
                        
                        # Use placeholder if no photo loaded
                        if not ph:
                            ph = Image.open(PLACEHOLDER_PATH).convert("RGBA")
                        
                        ph = ph.resize((photo_settings["photo_width"], photo_settings["photo_height"]))
                        radii = [photo_settings.get(f"photo_border_{k}", 0) for k in ["top_left", "top_right", "bottom_right", "bottom_left"]]
                        ph = round_photo(ph, radii)
                        template_img.paste(ph, (photo_settings["photo_x"], photo_settings["photo_y"]), ph)
                    except: pass

                    # Generate QR
                    if qr_settings.get("enable_qr"):
                        qr_str = data_hash[:10] 
                        qr_img = generate_qr_code(qr_str, qr_settings, qr_settings.get("qr_size", 120))
                        qr_img = qr_img.resize((qr_settings.get("qr_size", 120),)*2)
                        template_img.paste(qr_img, (qr_settings.get("qr_x", 50), qr_settings.get("qr_y", 50)))

                    # --- FIX: Convert to RGB before saving as JPEG ---
                    # Create a temporary RGB version just for saving
                    if template_img.mode == 'RGBA':
                        bg = Image.new("RGB", template_img.size, (255, 255, 255))
                        bg.paste(template_img, mask=template_img.split()[3])
                        save_img = bg
                    else:
                        save_img = template_img.convert("RGB")
                    # -------------------------------------------------

                    # Upload Card Image to Cloudinary
                    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
                    jpg_name = f"card_{template_id}_{ts}_{idx}.jpg"
                    jpg_buffer = io.BytesIO()
                    
                    # Save the converted image (save_img), NOT the original RGBA (template_img)
                    save_img.save(jpg_buffer, "JPEG", quality=95) 
                    
                    jpg_buffer.seek(0)
                    jpg_url = upload_image(jpg_buffer.getvalue(), folder='cards', resource_type='image')

                    # --- DATABASE INSERT (SQLAlchemy) ---
                    student = Student(
                        name=name, father_name=father_name, class_name=class_name,
                        dob=dob, address=address, phone=phone,
                        photo_filename=used_photo,
                        image_url=jpg_url,  # Store Cloudinary URL
                        created_at=datetime.now(timezone.utc),
                        data_hash=data_hash, template_id=template_id,
                        school_name=template_obj.school_name,
                        custom_data=custom_data
                    )
                    db.session.add(student)
                    success_count += 1
                    
                    # Batch Commit (every 20 records) to manage memory
                    if idx % 20 == 0: 
                        db.session.commit()

                except Exception as row_e:
                    errors.append(f"Row {idx+1}: {str(row_e)}")
                    error_count += 1

            # ---------------- LOOP END ----------------

            # Final Commit for remaining records
            db.session.commit()
            
            # Cleanup Input File
            if os.path.exists(excel_path): os.remove(excel_path)
            
            # Update Final Status
            jobs[task_id]['state'] = 'SUCCESS'
            jobs[task_id]['status'] = 'Completed'
            jobs[task_id]['result'] = f"Processed {total_records}. Created: {success_count}, Skipped: {skipped_count}, Errors: {error_count}"
            if errors:
                jobs[task_id]['result'] += f" | First error: {errors[0]}"
            
        except Exception as e:
            logger.error(f"Bulk job failed: {e}")
            import traceback
            traceback.print_exc()
            jobs[task_id]['state'] = 'FAILURE'
            jobs[task_id]['status'] = f"System Error: {str(e)}"
            # At end of function
            summary = f"Created: {success_count}, Errors: {error_count}"
            send_email(os.environ.get("ADMIN_EMAIL"), "Bulk Generation Complete", summary)

# =========================================================
# ROUTE TO TRIGGER THE BACKGROUND THREAD
# =========================================================
@app.route("/bulk_generate", methods=["POST"])
def bulk_generate():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403

    template_id_raw = request.form.get("template_id")
    if not template_id_raw:
        return jsonify({"error": "No template selected"}), 400
    
    template_id = int(template_id_raw)
    
    if 'excel_file' not in request.files:
        return jsonify({"error": "No Excel file uploaded"}), 400
        
    excel_file = request.files['excel_file']
    if excel_file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    try:
        # 1. Save Excel File
        filename = secure_filename(excel_file.filename)
        excel_path = os.path.join(app.root_path, UPLOAD_FOLDER, f"temp_{uuid.uuid4().hex}_{filename}")
        excel_file.save(excel_path)

        # 2. Process & Save Photos to Cloudinary + Apply Background Removal
        photo_map = {}  # Maps name → Cloudinary URL
        if 'bulk_photos' in request.files:
            photos = request.files.getlist('bulk_photos')
            _, photo_settings, _, _ = get_template_settings(template_id)  # Fixed: renamed p_settings → photo_settings
            
            for p in photos:
                if p and p.filename:
                    original_name = secure_filename(p.filename)
                    if not original_name: 
                        continue
                    
                    ts = datetime.now().strftime("%Y%m%d%H%M%S")
                    
                    # Read photo to bytes
                    photo_bytes = io.BytesIO()
                    p.save(photo_bytes)
                    photo_bytes.seek(0)
                    
                    # Process photo (crop + optional background removal)
                    try:
                        pil_img = Image.open(photo_bytes)
                        pil_img = ImageOps.exif_transpose(pil_img)  # Fix orientation
                        pil_img = pil_img.convert("RGB")
                        
                        bg_color = photo_settings.get("bg_remove_color", "#ffffff")
                        remove_bg_flag = photo_settings.get("remove_background", False)
                        
                        # Process in-memory
                        pil_img = _process_photo_pil(
                            pil_img,
                            target_width=photo_settings.get("photo_width", 260),
                            target_height=photo_settings.get("photo_height", 313),
                            remove_background=remove_bg_flag,
                            bg_color=bg_color
                        )
                        
                        # Convert to bytes
                        photo_bytes = io.BytesIO()
                        pil_img.save(photo_bytes, format="JPEG", quality=95)
                        photo_bytes.seek(0)
                    except Exception as e:
                        logger.warning(f"Failed to process photo {original_name}: {e}, using original")
                        photo_bytes.seek(0)
                    
                    # Upload to Cloudinary
                    try:
                        cloud_url = upload_image(photo_bytes.getvalue(), folder='bulk-photos')
                        # Map: lowercase filename without extension → Cloudinary URL
                        key = os.path.splitext(p.filename)[0].lower().strip()
                        photo_map[key] = cloud_url
                    except Exception as e:
                        logger.warning(f"Failed to upload photo {original_name} to Cloudinary: {e}")

        # 3. Start Background Thread
        task_id = uuid.uuid4().hex
        jobs[task_id] = {'state': 'PENDING', 'current': 0, 'total': 0, 'status': 'Initializing...'}
        
        executor.submit(background_bulk_generate, task_id, template_id, excel_path, photo_map)

        # Log Activity
        log_activity("Bulk Generation Started", 
                     target=f"Template ID: {template_id}", 
                     details=f"Task ID: {task_id}, Excel: {filename}, Photos: {len(photo_map)}")

        return jsonify({"success": True, "task_id": task_id})

    except Exception as e:
        logger.error(f"Bulk gen trigger error: {e}")
        log_activity("Bulk Generation Failed", 
                     target=f"Template ID: {template_id}", 
                     details=f"Error: {str(e)}")
        
        return jsonify({"success": False, "error": str(e)}), 500
    
@app.route('/taskstatus/<task_id>')
def taskstatus(task_id):
    task = jobs.get(task_id)
    if not task:
        return jsonify({'state': 'FAILURE', 'status': 'Task not found'}), 404
    return jsonify(task)

@app.route("/admin/preview_bulk_template/<int:template_id>", methods=["GET"])
def preview_bulk_template(template_id):
    """Preview template with dynamic fields for bulk upload"""
    if not session.get("admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    template = db.session.get(Template, template_id)


    if not template:
        return jsonify({"success": False, "error": "Template not found"}), 404
    
    # Get dynamic fields - FIXED
    dynamic_fields = TemplateField.query.filter_by(
        template_id=template_id
    ).order_by(TemplateField.display_order.asc()).all()
    
    # Create CSV template content
    import csv
    import io
    
    # Define columns
    columns = [
        'name',
        'father_name', 
        'class_name',
        'dob',
        'address',
        'phone',
        'photo_path'
    ]
    
    # Add dynamic field columns
    field_info = []
    for field in dynamic_fields:
        columns.append(field.field_name)
        field_info.append({
            'name': field.field_name,
            'label': field.field_label,
            'required': field.is_required,
            'type': field.field_type
        })
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(columns)
    
    # Write sample data
    writer.writerow([
        'John Doe',               # name
        'Robert Doe',             # father_name
        '10-A',                   # class_name
        '2005-01-15',             # dob
        '123 Main Street',        # address
        '9876543210',             # phone
        'photos/john.jpg',        # photo_path
    ] + ['Sample Data' for _ in dynamic_fields])  # Dynamic fields
    
    writer.writerow([
        'Jane Smith',
        'William Smith',
        '11-B',
        '2004-05-20',
        '456 Oak Avenue',
        '9876543211',
        'photos/jane.jpg',
    ] + ['Another Sample' for _ in dynamic_fields])
    
    csv_content = output.getvalue()
    
    return jsonify({
        "success": True,
        "template_name": template.school_name,
        "columns": columns,
        "field_info": field_info,
        "csv_template": csv_content,
        "download_filename": f"{template.school_name}_template.csv"
    })

@app.route("/admin/bulk_upload_guide")
def bulk_upload_guide():
    """Bulk upload guide page"""
    if not session.get("admin"):
        return redirect(url_for("login"))
    
    templates = Template.query.all()
    return render_template("bulk_upload_guide.html", templates=templates)

# ================== CORRECTED FORM FIELD ROUTES ==================

# ================== CORRECTED: Allow Students to View Fields ==================
@app.route('/admin/template/<int:template_id>/form-fields', methods=['GET', 'POST'])
def manage_template_fields(template_id):
    # 1. Check if user is logged in (Either as Admin OR Student)
    is_admin = session.get("admin")
    is_student = session.get("student_email")

    if not is_admin and not is_student:
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    try:
        # GET: Fetch fields (Allowed for Admin AND Student)
        if request.method == 'GET':
            template = db.session.get(Template, template_id)
            if not template:
                return jsonify({"success": False, "message": "Template not found"}), 404
            
            fields = []
            db_fields = TemplateField.query.filter_by(template_id=template_id)\
                        .order_by(TemplateField.display_order.asc()).all()
                        
            for field in db_fields:
                field_dict = {
                    'id': field.id,
                    'template_id': field.template_id,
                    'field_name': field.field_name,
                    'field_label': field.field_label,
                    'field_type': field.field_type,
                    'is_required': field.is_required,
                    'display_order': field.display_order,
                    'field_options': field.field_options or []
                }
                fields.append(field_dict)
            return jsonify(fields)
        
        # POST: Add a new field (Restricted to ADMIN ONLY)
        elif request.method == 'POST':
            if not is_admin:
                return jsonify({"success": False, "message": "Admin privileges required"}), 403

            data = request.json
            if not data:
                return jsonify({"success": False, "message": "No data received"}), 400

            field_name = data.get('field_name', '').strip().lower().replace(' ', '_')
            field_label = data.get('field_label', '').strip()
            field_type = data.get('field_type', 'text')
            
            # Safe Boolean Conversion
            raw_req = data.get('is_required', 0)
            is_required = str(raw_req).lower() in ['true', '1', 'on', 'yes']
            
            display_order = int(data.get('display_order', 0))
            field_options = data.get('field_options', [])
            
            if not field_name or not field_label:
                return jsonify({"success": False, "message": "Name and Label are required"}), 400
            
            field = TemplateField(
                template_id=template_id,
                field_name=field_name,
                field_label=field_label,
                field_type=field_type,
                is_required=is_required,
                display_order=display_order,
                field_options=field_options
            )
            db.session.add(field)
            db.session.commit()
            
            return jsonify({"success": True, "message": "Field added successfully", "id": field.id})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error managing fields for template {template_id}: {e}")
        return jsonify({"success": False, "message": f"Server Error: {str(e)}"}), 500
    
@app.route('/admin/template/form-fields/<int:field_id>', methods=['PUT', 'DELETE'])
def manage_single_field(field_id):
    if not session.get("admin"):
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    
    try:
        field = db.session.get(TemplateField, field_id)
        if not field:
            return jsonify({"success": False, "message": "Field not found"}), 404
        
        # DELETE: Remove a field
        if request.method == 'DELETE':
            db.session.delete(field)
            db.session.commit()
            return jsonify({"success": True, "message": "Field deleted successfully"})
            
        # PUT: Update a field
        elif request.method == 'PUT':
            data = request.json
            if not data:
                return jsonify({"success": False, "message": "No data provided"}), 400
            
            if 'field_label' in data:
                field.field_label = data.get('field_label').strip()
            
            if 'field_type' in data:
                field.field_type = data.get('field_type')
            
            if 'is_required' in data:
                # SAFE BOOLEAN CONVERSION
                raw_req = data.get('is_required')
                field.is_required = str(raw_req).lower() in ['true', '1', 'on', 'yes']
            
            if 'display_order' in data:
                field.display_order = int(data.get('display_order'))
                
            if 'field_options' in data:
                # Ensure it's stored as a new list to trigger SQLAlchemy updates
                field.field_options = list(data.get('field_options', []))
            
            db.session.commit()
            return jsonify({"success": True, "message": "Field updated successfully"})

    except Exception as e:
        db.session.rollback() # Important: Rollback on error
        logger.error(f"Error updating/deleting field {field_id}: {e}")
        return jsonify({"success": False, "message": f"Server Error: {str(e)}"}), 500
    
# ================== Versatile Verification Route ==================
@app.route("/verify/<student_identifier>")
def verify_student(student_identifier):
    """
    Public route to verify student details via QR code scan.
    Prioritizes Cloudinary URLs over local files.
    """
    try:
        # Clean the input
        identifier = str(student_identifier).strip()
        
        # Search by ID OR by Hash (handling the 10-char hash used in QR generation)
        student = Student.query.filter(
            (db.cast(Student.id, db.String) == identifier) |
            (db.func.substr(Student.data_hash, 1, 10) == identifier)
        ).first()
        
        if not student:
            return render_template("verify.html", error="Student record not found.", valid=False)
        
        # --- FIX: LOGIC TO CHOOSE THE CORRECT PHOTO URL ---
        final_photo_url = None

        # 1. Check Cloudinary URL first (New System)
        if getattr(student, 'photo_url', None):
            final_photo_url = student.photo_url
        
        # 2. Fallback to Local File (Old System)
        elif student.photo_filename and student.photo_filename != "placeholder.jpg":
            final_photo_url = url_for('static', filename=f"Uploads/{student.photo_filename}")
            
        # 3. Default to Placeholder if nothing else exists
        else:
            final_photo_url = url_for('static', filename="placeholder.jpg")
        # --------------------------------------------------

        # Prepare data object
        student_data = {
            "name": student.name,
            "father_name": student.father_name,
            "school_name": student.school_name,
            "photo_url": final_photo_url,  # <--- Using the fixed URL
            "class_name": student.class_name, # Added class name for better verification
            "status": "Verified"
        }
        
        return render_template("verify.html", student=student_data, valid=True)
        
    except Exception as e:
        logger.error(f"Database error during verification: {e}")
        return render_template("verify.html", error="System error.", valid=False), 500


@app.route('/download_school_excel/<int:template_id>')
def download_school_excel(template_id):
    # 1. Security Check
    if not session.get("admin"):
        flash('Please log in as admin to download data.', 'error')
        return redirect(url_for('login'))

    try:
        # 2. Get Template info for filename
        template = db.session.get(Template, template_id)
        if not template:
            flash('Template not found.', 'error')
            return redirect(url_for('admin'))

        # 3. Get students for this specific template
        students = Student.query.filter_by(template_id=template_id).all()

        if not students:
            flash('No students found for this school.', 'warning')
            return redirect(url_for('admin'))

        # 4. Prepare Data
        data = []
        for student in students:
            # Base fields
            row = {
                'ID': student.id,
                'Name': student.name,
                'Father Name': student.father_name,
                'Class': student.class_name,
                'DOB': student.dob,
                'Address': student.address,
                'Phone': student.phone,
                'Email': student.email,
                'Photo': student.photo_filename,
                'Created At': student.created_at.strftime('%Y-%m-%d %H:%M:%S') if student.created_at else ''
            }

            # Handle Dynamic Fields (unpack custom_data dictionary)
            # This ensures fields like "Blood Group" get their own column
            if student.custom_data and isinstance(student.custom_data, dict):
                for key, val in student.custom_data.items():
                    # Format key (e.g., "blood_group" -> "Blood Group")
                    header = key.replace('_', ' ').title()
                    row[header] = val

            data.append(row)

        # 5. Create DataFrame
        df = pd.DataFrame(data)

        # 6. Save to Memory Buffer
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Student Data')
        
        output.seek(0)
        
        # 7. Generate clean filename
        clean_school_name = secure_filename(template.school_name)
        timestamp = datetime.now().strftime("%Y%m%d")
        filename = f"{clean_school_name}_Data_{timestamp}.xlsx"
        
        return send_file(
            output,
            download_name=filename,
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        logger.error(f"Error downloading Excel for template {template_id}: {e}")
        flash(f"Error generating Excel file: {str(e)}", 'error')
        return redirect(url_for('admin'))
 # ================== Delete All Sheets for School Route ==================

@app.route("/admin/delete_school_sheets/<int:template_id>", methods=["POST"])
def delete_school_sheets(template_id):
    """
    Deletes ALL generated sheets (completed PDFs, active PNG, and position data)
    for a specific template. Used to clear history after downloading the merged file.
    """
    if not session.get("admin"):
        return redirect(url_for("login"))

    try:
        template = db.session.get(Template, template_id)
        if not template:
            return redirect(url_for("admin", error="Template not found"))

        deleted_count = 0

        # 1. Delete Completed PDFs (sheet_template_{id}_*.pdf)
        pdf_pattern = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}_*.pdf")
        for file_path in glob.glob(pdf_pattern):
            try:
                os.remove(file_path)
                deleted_count += 1
            except Exception as e:
                logger.error(f"Error deleting {file_path}: {e}")

        # 2. Delete Active PNG Sheet (sheet_template_{id}.png)
        png_path = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}.png")
        if os.path.exists(png_path):
            try:
                os.remove(png_path)
                deleted_count += 1
            except Exception as e:
                logger.error(f"Error deleting {png_path}: {e}")

        # Change txt_path to map_file (JSON)
        map_file = os.path.join(GENERATED_FOLDER, f"sheet_map_template_{template_id}.json")
        if os.path.exists(map_file):
            try:
                os.remove(map_file)
            except Exception as e:
                logger.error(f"Error deleting map file: {e}")

        logger.info(f"Deleted {deleted_count} sheets for template {template_id}")
        
        if deleted_count == 0:
            return redirect(url_for("admin", error="No sheets found to delete."))
            
        return redirect(url_for("admin", success=f"Successfully deleted {deleted_count} sheets for {template.school_name}."))

    except Exception as e:
        logger.error(f"Error clearing sheets: {e}")
        return redirect(url_for("admin", error=f"Error clearing sheets: {str(e)}"))

def cleanup_old_files(days=30):
    """
    Deletes files in Uploads and Generated folders that are older than 'days'.
    Returns the count of deleted files.
    """
    try:
        now = time.time()  # This line caused the error before import
        cutoff = now - (days * 86400) # 86400 seconds in a day
        
        deleted_count = 0
        
        # Define folders to clean
        folders_to_clean = [UPLOAD_FOLDER, GENERATED_FOLDER]
        
        for folder in folders_to_clean:
            if not os.path.exists(folder):
                continue
                
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                
                # Skip important files and directories
                if filename == "placeholder.jpg" or filename.startswith(".") or os.path.isdir(file_path): 
                    continue
                
                # Check if file is older than cutoff
                if os.path.isfile(file_path):
                    try:
                        file_time = os.path.getmtime(file_path)
                        if file_time < cutoff:
                            os.remove(file_path)
                            deleted_count += 1
                    except Exception as e:
                        logger.error(f"Error deleting {filename}: {e}")
                        
        logger.info(f"Cleanup finished. Removed {deleted_count} files older than {days} days.")
        return deleted_count
    except Exception as e:
        logger.error(f"Error in cleanup function: {e}")
        return 0

@app.route("/admin/run_cleanup", methods=["POST"])
def run_cleanup():
    if not session.get("admin"):
        return redirect(url_for("login"))
        
    try:
        # Default to 30 days, or get from request if needed
        days = 30
        count = cleanup_old_files(days)
        
        return redirect(url_for("admin", success=f"Cleanup Complete: Removed {count} files older than {days} days."))
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return redirect(url_for("admin", error=f"Cleanup failed: {str(e)}"))

# Add this somewhere in your app.py, perhaps after the 'delete_student' route
@app.route("/delete_all_students_by_template/<int:template_id>", methods=["POST"])
def delete_all_students_by_template(template_id):
    if not session.get("admin"):
        return redirect(url_for("login"))

    try:
        # 1. Get Template details
        template = db.session.get(Template, template_id)
        if not template:
            return redirect(url_for("admin", error="Template not found"))

        # 2. Find all students for this template
        students = Student.query.filter_by(template_id=template_id).all()
        count = len(students)

        if count == 0:
            return redirect(url_for("admin", error="No students found for this school."))

        # 3. Loop through and delete files
        for student in students:
            # Delete uploaded photo
            if student.photo_filename:
                p_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
                if os.path.exists(p_path):
                    try: os.remove(p_path)
                    except: pass
            
            # Delete generated ID card images (JPG & PDF)
            if student.generated_filename:
                base_name = os.path.splitext(student.generated_filename)[0]
                for ext in ['.jpg', '.pdf']:
                    g_path = os.path.join(GENERATED_FOLDER, base_name + ext)
                    if os.path.exists(g_path):
                        try: os.remove(g_path)
                        except: pass
            
            # Delete the record from DB
            db.session.delete(student)

        # 4. Clean up any compiled sheet files for this template
        # (Since we deleted the students, the old sheets are invalid)
        try:
            png_sheet = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}.png")
            if os.path.exists(png_sheet): os.remove(png_sheet)
            
            # Remove positions file
            pos_file = os.path.join(GENERATED_FOLDER, f"positions_template_{template_id}.txt")
            if os.path.exists(pos_file): os.remove(pos_file)

            # Optional: Remove old merged PDFs if you want a complete clean slate
            # pdf_pattern = os.path.join(GENERATED_FOLDER, f"sheet_template_{template_id}_*.pdf")
            # for f in glob.glob(pdf_pattern): os.remove(f)
        except: pass

        db.session.commit()
        
        logger.info(f"Deleted {count} students for template {template_id}")
        return redirect(url_for("admin", success=f"Successfully deleted all {count} cards for {template.school_name}."))

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting school cards: {e}")
        return redirect(url_for("admin", error=f"Error deleting cards: {str(e)}"))

@app.route("/health")
def health_check():
    try:
        # Check DB connection
        db.session.execute(text("SELECT 1"))
        return jsonify({"status": "healthy", "db": "connected"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

@app.route('/favicon.ico')
def favicon():
    """Stop the 404 errors for the browser icon"""
    return "", 204

# Register blueprint

app.register_blueprint(corel_bp, url_prefix='/corel')
app.register_blueprint(editor_bp)
# In your initialization section, after migrate_database():
with app.app_context():
    init_db()
    migrate_database()
    migrate_template_font_colors()
    verify_fonts_available() # Add this line
    migrate_photo_background_settings()  # ← ADD THIS LINE

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
