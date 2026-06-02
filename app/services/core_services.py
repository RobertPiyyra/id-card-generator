import os
import re
import logging
import smtplib
import socket
import ssl
from email.mime.text import MIMEText
from datetime import datetime, timezone
from flask import session, request
from models import db, Template, TemplateField, ActivityLog
from utils import get_default_font_config, get_default_photo_config, get_default_qr_config

logger = logging.getLogger(__name__)

def _normalize_school_name(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()

def _find_template_dict_by_school(templates, school_name):
    normalized = _normalize_school_name(school_name)
    if not normalized:
        return None
    for template in templates or []:
        if _normalize_school_name(template.get("school_name")) == normalized:
            return template
    return None

def get_templates():
    try:
        query = db.session.query(Template).order_by(Template.created_at.desc())
        if session.get("admin") and session.get("admin_role") == "school_admin":
            if session.get("admin_school"):
                query = query.filter_by(school_name=session.get("admin_school"))
        templates = query.all()
        result = []
        for template in templates:
            font_settings = get_default_font_config()
            photo_settings = get_default_photo_config()
            qr_settings = get_default_qr_config()
            source_path = template.filename or template.template_url or ""
            source_basename = os.path.basename(source_path.split("?", 1)[0]) if source_path else ""
            if source_basename and len(source_basename) > 90:
                source_basename = source_basename[:87] + "..."
            back_source_path = template.back_filename or template.back_template_url or ""
            back_source_basename = os.path.basename(back_source_path.split("?", 1)[0]) if back_source_path else ""
            if back_source_basename and len(back_source_basename) > 90:
                back_source_basename = back_source_basename[:87] + "..."
            
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
            back_font_settings = {**get_default_font_config(), **(template.back_font_settings or {})}
            back_photo_settings = {**get_default_photo_config(), **(template.back_photo_settings or {})}
            back_qr_settings = {**get_default_qr_config(), **(template.back_qr_settings or {})}
            
            template_fields = []
            if template.fields:
                for field in template.fields:
                    template_fields.append({
                        'field_name': field.field_name,
                        'field_label': field.field_label,
                        'field_type': field.field_type,
                        'is_required': field.is_required,
                        'show_label_front': bool(getattr(field, 'show_label_front', True)),
                        'show_value_front': bool(getattr(field, 'show_value_front', True)),
                        'show_label_back': bool(getattr(field, 'show_label_back', False)),
                        'show_value_back': bool(getattr(field, 'show_value_back', False)),
                        'display_order': field.display_order,
                        'field_options': field.field_options
                    })
                template_fields.sort(key=lambda x: int(x.get('display_order') or 0))

            result.append({
                'id': template.id,
                'filename': template.filename,
                'template_url': template.template_url,
                'back_filename': template.back_filename,
                'back_template_url': template.back_template_url,
                'source_path': source_path,
                'source_name': source_basename or "No source",
                'back_source_path': back_source_path,
                'back_source_name': back_source_basename or "No back source",
                'school_name': template.school_name,
                'created_at': template.created_at.isoformat() if template.created_at else datetime.now(timezone.utc).isoformat(),
                'font_settings': font_settings,
                'photo_settings': photo_settings,
                'qr_settings': qr_settings,
                'back_font_settings': back_font_settings,
                'back_photo_settings': back_photo_settings,
                'back_qr_settings': back_qr_settings,
                'card_orientation': template.card_orientation or 'landscape',
                'language': template.language or 'english',
                'text_direction': template.text_direction or 'ltr',
                'back_language': template.back_language or template.language or 'english',
                'back_text_direction': template.back_text_direction or template.text_direction or 'ltr',
                'back_layout_config': template.back_layout_config,
                'is_double_sided': bool(template.is_double_sided),
                'duplex_flip_mode': template.duplex_flip_mode or 'long_edge',
                'deadline': template.deadline.isoformat() if template.deadline else None,
                'fields': template_fields,
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
        return []

def log_activity(action, target=None, details=None):
    try:
        if session.get('admin'):
            actor = "Admin"
        elif session.get('student_email'):
            actor = session['student_email']
        else:
            actor = "Anonymous"

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
        logger.error(f"Failed to log activity: {e}")
        db.session.rollback()

def send_email(to, subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = os.environ.get("EMAIL_FROM")
    msg['To'] = to
    server = None
    try:
        smtp_server = "smtp.gmail.com"
        smtp_port = 465
        password = os.environ.get("EMAIL_PASSWORD")
        addr_info = socket.getaddrinfo(smtp_server, smtp_port, socket.AF_INET, socket.SOCK_STREAM)
        target_ip = addr_info[0][4][0]
        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(target_ip, smtp_port, context=context, timeout=30)
        server.login(msg['From'], password)
        server.send_message(msg)
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False
    finally:
        if server:
            try:
                server.quit()
            except:
                pass