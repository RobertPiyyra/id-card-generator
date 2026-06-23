"""
Template upload and storage service.

Handles file upload, Cloudinary storage, PDF processing, and duplicate detection.
Extracted from legacy_app.py.
"""

import io
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import fitz  # PyMuPDF
from flask import current_app
from PIL import Image
from werkzeug.utils import secure_filename

from app.services.photo_service import _process_photo_pil
from cloudinary_config import upload_image
from models import Student, Template, db
from utils import (
    GENERATED_FOLDER,
    STATIC_DIR,
    DUPLICATE_CONFIG_PATH,
    get_default_font_config,
    get_default_photo_config,
    get_default_qr_config,
    get_template_path,
    get_template_settings,
    load_template_smart,
)

logger = logging.getLogger(__name__)


def store_template_upload_asset(file_storage, *, side_label):
    """Upload a template file (front or back) and return storage info."""
    if file_storage is None or not file_storage.filename:
        raise ValueError(f"{side_label} template file is required")
    filename = secure_filename(file_storage.filename)
    file_bytes = io.BytesIO()
    file_storage.save(file_bytes)
    file_bytes.seek(0)
    return store_template_upload_bytes(
        file_bytes.getvalue(), filename, side_label=side_label,
    )


def _extract_pdf_upload_payload(raw_bytes, side_label):
    """Extract and validate PDF from uploaded bytes."""
    pdf_header_pos = raw_bytes.find(b"%PDF")
    if pdf_header_pos < 0:
        raise ValueError("Uploaded file does not contain a PDF header.")
    upload_payload = raw_bytes[pdf_header_pos:]
    if len(upload_payload) < 128:
        raise ValueError("Uploaded PDF is too small and appears truncated.")
    pdf_doc = fitz.open(stream=upload_payload, filetype="pdf")
    try:
        page_count = pdf_doc.page_count
        if page_count < 1:
            raise ValueError("Uploaded PDF has no pages.")
        _ = pdf_doc[0].get_pixmap(dpi=72)
    finally:
        pdf_doc.close()
    return upload_payload, page_count


def _single_pdf_page_bytes(pdf_bytes, page_index):
    """Extract a single page from PDF bytes."""
    src_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    dst_doc = fitz.open()
    try:
        if page_index < 0 or page_index >= src_doc.page_count:
            raise ValueError(f"PDF page {page_index + 1} is missing.")
        dst_doc.insert_pdf(src_doc, from_page=page_index, to_page=page_index)
        return dst_doc.tobytes(
            garbage=4, clean=False, deflate=False,
            deflate_images=False, deflate_fonts=False,
            expand=255, linear=False, no_new_id=True,
            pretty=False, use_objstms=0,
        )
    finally:
        dst_doc.close()
        src_doc.close()


def _template_side_filename(filename, side_label):
    """Generate a side-specific filename."""
    safe_name = secure_filename(filename or "template.pdf")
    stem, ext = os.path.splitext(safe_name)
    ext = ext or ".pdf"
    return f"{stem}_{side_label.lower()}{ext}"


def store_template_upload_bytes(raw_bytes, filename, *, side_label):
    """Store template bytes locally and optionally upload to Cloudinary."""
    from utils import get_storage_backend
    storage_backend = get_storage_backend()

    filename = secure_filename(filename or f"{side_label.lower()}_template")
    raw_bytes = raw_bytes if isinstance(raw_bytes, bytes) else bytes(raw_bytes or b"")
    if not raw_bytes:
        raise ValueError(f"{side_label} template file is empty")
    if not filename.lower().endswith((".pdf", ".jpg", ".jpeg", ".png")):
        raise ValueError(
            f"Invalid {side_label.lower()} template format. Use PDF, JPG, or PNG"
        )

    file_ext = os.path.splitext(filename)[1].lower()
    is_pdf_upload = file_ext == ".pdf"
    upload_payload = raw_bytes
    page_count = None

    if is_pdf_upload:
        try:
            upload_payload, page_count = _extract_pdf_upload_payload(raw_bytes, side_label)
        except Exception as pdf_err:
            raise ValueError(f"Uploaded {side_label.lower()} PDF is invalid: {pdf_err}")
    else:
        try:
            test_img = Image.open(io.BytesIO(raw_bytes))
            test_img.verify()
        except Exception as img_err:
            raise ValueError(f"Uploaded {side_label.lower()} image is invalid: {img_err}")

    templates_dir = os.path.join(STATIC_DIR, "templates_uploads")
    os.makedirs(templates_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    stored_name = f"{ts}_{uuid.uuid4().hex}_{filename}"
    local_abs_path = os.path.join(templates_dir, stored_name)
    with open(local_abs_path, "wb") as local_file:
        local_file.write(upload_payload)
    local_rel_filename = f"templates_uploads/{stored_name}"

    remote_url = None
    if storage_backend != "local":
        remote_url = upload_image(
            upload_payload,
            folder="id_card_templates",
            resource_type="raw" if is_pdf_upload else "image",
            format="pdf" if is_pdf_upload else file_ext.lstrip(".") or None,
        )

        if not remote_url:
            raise RuntimeError(
                f"Failed to upload {side_label.lower()} template to Cloudinary"
            )

        last_remote_err = None
        for _ in range(5):
            try:
                _ = load_template_smart(remote_url)
                last_remote_err = None
                break
            except Exception as remote_err:
                last_remote_err = remote_err
                time.sleep(0.8)
        if last_remote_err is not None:
            last_err_text = str(last_remote_err)
            if "network/DNS" in last_err_text:
                logger.warning(
                    "%s uploaded, but immediate Cloudinary read-check failed due network/DNS. Using local backup copy.",
                    side_label,
                )
                remote_url = None
            elif (
                "HTTP 401" in last_err_text
                or "HTTP 403" in last_err_text
                or "unauthorized/forbidden" in last_err_text.lower()
            ):
                logger.warning(
                    "%s uploaded, but Cloudinary denied public access. Using local backup copy.",
                    side_label,
                )
                remote_url = None
            else:
                raise RuntimeError(
                    f"Uploaded {side_label.lower()} template is not readable from Cloudinary after retry. Details: {last_remote_err}"
                )

    return {
        "filename": local_rel_filename,
        "template_url": remote_url,
        "is_pdf": is_pdf_upload,
        "page_count": page_count,
    }


def load_duplicate_config():
    """Load duplicate checking config from disk."""
    default_config = {"check_phone": False, "check_name_class": True}
    if os.path.exists(DUPLICATE_CONFIG_PATH):
        try:
            with open(DUPLICATE_CONFIG_PATH, "r") as f:
                return {**default_config, **json.load(f)}
        except json.JSONDecodeError as e:
            logger.error("Error loading duplicate config: %s", e)
    return default_config


def save_duplicate_config(config):
    """Save duplicate checking config to disk."""
    try:
        with open(DUPLICATE_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
        logger.info("Duplicate config saved successfully")
    except Exception as e:
        logger.error("Error saving duplicate config: %s", e)


def check_duplicate_student(form_data, photo_filename=None, student_id=None):
    """Check if a student with matching fields already exists in the same template."""
    duplicate_config = load_duplicate_config()
    current_template_id = form_data.get("template_id")

    if not current_template_id:
        return False, None

    try:
        if duplicate_config.get("check_phone", False):
            query = Student.query.filter(
                Student.phone == form_data["phone"],
                Student.template_id == current_template_id,
            )
            if student_id:
                query = query.filter(Student.id != student_id)
            if query.first():
                return True, "A student with this phone number already exists in this school."

        if duplicate_config.get("check_name_class", True):
            query = Student.query.filter(
                Student.name == form_data["name"],
                Student.class_name == form_data["class_name"],
                Student.template_id == current_template_id,
            )
            if student_id:
                query = query.filter(Student.id != student_id)
            if query.first():
                return True, "A student with this name and class combination already exists in this school."

        return False, None
    except Exception as e:
        logger.error("Error checking duplicates: %s", e)
        return True, f"Database error: {str(e)}"

def _looks_like_pdf_template_source(path_or_url):
    """Return True if the path/URL looks like a PDF template source."""
    try:
        src = str(path_or_url or "").strip().lower()
    except Exception:
        return False
    if not src:
        return False
    src_no_query = src.split("?", 1)[0]
    if src_no_query.endswith(".pdf") or "/raw/upload/" in src_no_query:
        return True
    if src.startswith(("http://", "https://")):
        return False
    try:
        with open(path_or_url, "rb") as fh:
            return b"%PDF" in fh.read(16)
    except Exception:
        return False


def get_templates():
    """Get all templates — re-exported from core_services for backward compat."""
    from app.services.core_services import get_templates as _get_templates
    return _get_templates()
