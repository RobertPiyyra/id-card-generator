"""
Student data helpers and session management.

Provides student form data building, QR hash generation, and session helpers.
Extracted from legacy_app.py.
"""

import logging

from flask import session
from models import Student
from utils import generate_data_hash

logger = logging.getLogger(__name__)


def _build_student_form_data(student_like):
    """Build a dict of core student fields from a Student-like object."""
    return {
        "name": getattr(student_like, "name", "") or "",
        "father_name": getattr(student_like, "father_name", "") or "",
        "class_name": getattr(student_like, "class_name", "") or "",
        "dob": getattr(student_like, "dob", "") or "",
        "address": getattr(student_like, "address", "") or "",
        "phone": getattr(student_like, "phone", "") or "",
    }


def _build_student_image_ref(student_like):
    """Return the best available photo reference for a student."""
    return (
        getattr(student_like, "photo_url", None)
        or getattr(student_like, "photo_filename", None)
        or ""
    )


def _build_qr_hash(student_like):
    """Build a short QR hash for a student."""
    return generate_data_hash(
        _build_student_form_data(student_like),
        _build_student_image_ref(student_like),
    )[:10]


def _build_payload(settings, student_like, student_id, school_name, prefix):
    """Build QR/barcode payload based on settings."""
    data_type = settings.get(f"{prefix}_data_type", "student_id")
    str_student_id = str(student_id) if student_id is not None else None
    if data_type == "url":
        base = settings.get(f"{prefix}_base_url", "") or ""
        if base and not base.endswith("/"):
            base += "/"
        return base + (str_student_id or _build_qr_hash(student_like))
    if data_type == "text":
        return settings.get(f"{prefix}_custom_text", "Sample Text")
    if data_type == "json":
        import json
        return json.dumps({
            "student_id": str_student_id or _build_qr_hash(student_like),
            "name": getattr(student_like, "name", "") or "",
            "class": getattr(student_like, "class_name", "") or "",
            "school_name": school_name or getattr(student_like, "school_name", "") or "",
        })
    return str_student_id or _build_qr_hash(student_like)


def _normalize_school_name(value):
    """Normalize a school name for comparison."""
    import re
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _find_template_dict_by_school(templates, school_name):
    """Find a template dict matching a school name (case/whitespace insensitive)."""
    normalized = _normalize_school_name(school_name)
    if not normalized:
        return None
    for template in templates or []:
        if _normalize_school_name(template.get("school_name")) == normalized:
            return template
    return None


def _student_session_school_name():
    """Get the school name from the current student session."""
    return (session.get("student_school_name") or "").strip()


def _is_admin_session():
    """Check if the current session is an admin session."""
    return bool(session.get("admin"))


def _current_session_email():
    """Get the current student session email."""
    return (session.get("student_email") or "").strip().lower()


def _student_school_access_allowed(student_school_name):
    """Check if the current session can access a student's school."""
    if _is_admin_session() and session.get("admin_role") != "school_admin":
        return True
    locked_school = _student_session_school_name()
    if not locked_school:
        return True
    return _normalize_school_name(locked_school) == _normalize_school_name(student_school_name)


def check_deadline_passed(template_id):
    """Check if the deadline for a template has passed. Returns (is_passed, deadline_date)."""
    try:
        from models import Template
        from datetime import datetime, timezone
        template = Template.query.get(template_id)
        if not template or not template.deadline:
            return False, None
        deadline = template.deadline
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return now > deadline, deadline
    except Exception:
        return False, None
