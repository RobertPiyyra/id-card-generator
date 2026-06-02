import os
import logging
import json
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, render_template, url_for

from models import db, Student, Template, VerificationAudit
from app.services.premium_service import parse_signed_verify_token
from app.services.photo_service import resolve_student_photo_reference
from utils import PLACEHOLDER_PATH
from app.extensions import limiter

logger = logging.getLogger(__name__)

verify_bp = Blueprint("verify_routes", __name__)

@verify_bp.route("/verify/<token>", methods=["GET"])
@verify_bp.route("/verify/v2/<token>", methods=["GET"])
@limiter.limit("30 per minute")
def verify_card(token):
    """
    Public rate-limited route to verify a student card's validity.
    Attempts to decrypt the signed token first. Falls back to search by
    student ID or hash substring for legacy barcode/QR code compatibility.
    """
    payload = None
    status = "ok"
    student = None
    decoded = False
    
    # 1. Try decoding the signed token format
    try:
        payload, status = parse_signed_verify_token(
            secret_key=os.getenv("SECRET_KEY", "dev-key"),
            token=token,
            max_age_seconds=86400  # Token valid for 24 hours
        )
        if status == "ok" and payload:
            student = db.session.get(Student, int(payload.get("sid")))
            decoded = True
    except Exception as e:
        logger.debug(f"Token decoding exception: {e}")
        status = "invalid"

    # 2. Fall back to searching by student ID or MD5 hash substring if token decode failed
    if not student:
        identifier = str(token).strip()
        student = Student.query.filter(
            (db.cast(Student.id, db.String) == identifier) |
            (db.func.substr(Student.data_hash, 1, 10) == identifier)
        ).first()
        
        if student:
            status = "ok"
            decoded = False
            payload = {
                "sid": student.id,
                "tid": student.template_id or 0,
                "jti": f"legacy-{student.id}"
            }
        else:
            # If no student matches legacy check either
            if not decoded:
                status = "invalid"

    # 3. Log the scan event to the database audit trail
    try:
        audit = VerificationAudit(
            status=status,
            token_id=(payload.get("jti") if payload else None),
            student_id=(student.id if student else (payload.get("sid") if payload else None)),
            template_id=(student.template_id if student else (payload.get("tid") if payload else None)),
            ip_address=request.remote_addr,
            user_agent=(request.headers.get("User-Agent") or "")[:512],
            details_json={
                "token_status": status,
                "decoded_successfully": decoded,
                "raw_token_preview": token[:60] + "..." if len(token) > 60 else token
            }
        )
        db.session.add(audit)
        db.session.commit()
    except Exception as audit_err:
        db.session.rollback()
        logger.error(f"Failed to save verification audit: {audit_err}")

    # 4. Handle non-verified statuses
    if not student:
        return render_template(
            "verify_card.html",
            error="Student credential not found or signature is invalid.",
            status="invalid",
            valid=False
        )

    if status == "expired":
        return render_template(
            "verify_card.html",
            error="This verification link has expired.",
            status="expired",
            student=student,
            valid=False
        )

    if status == "tampered":
        return render_template(
            "verify_card.html",
            error="Warning: Credential token has a signature mismatch (tampered).",
            status="tampered",
            student=student,
            valid=False
        )

    # 5. Check if the credential was explicitly revoked
    if bool(getattr(student, "verification_revoked", False)):
        return render_template(
            "verify_card.html",
            error="This credential has been officially revoked by the school administration.",
            status="revoked",
            student=student,
            valid=False
        )

    # 6. Retrieve template for logo/branding if available
    template = db.session.get(Template, student.template_id) if student.template_id else None
    
    # 7. Resolve photo URL
    final_photo_url = None
    photo_url, local_photo_path = resolve_student_photo_reference(student)
    if photo_url:
        final_photo_url = photo_url
    elif local_photo_path:
        final_photo_url = url_for('static', filename=f"Uploads/{os.path.basename(local_photo_path)}")
    else:
        final_photo_url = url_for('static', filename=os.path.basename(PLACEHOLDER_PATH))

    student_data = {
        "id": student.id,
        "name": student.name,
        "father_name": student.father_name,
        "school_name": student.school_name,
        "photo_url": final_photo_url,
        "class_name": student.class_name,
        "roll_number": getattr(student, "roll_number", None) or getattr(student, "roll_no", None) or f"REG-{student.id:04d}",
        "status": "Verified",
        "school_seal_url": template.template_url if template else None,
        "verification_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "details_hash": getattr(student, "data_hash", None)
    }

    return render_template(
        "verify_card.html",
        student=student_data,
        status="ok",
        valid=True
    )
