"""
REST API routes for webhook management.

All routes require API key authentication via the X-API-Key header.
"""


import logging

from flask import Blueprint, g, jsonify, request

from app.services.api_auth import require_api_key
from app.services.webhook_service import (
    WEBHOOK_EVENTS,
    delete_webhook,
    generate_webhook_secret,
    get_delivery_log,
    list_webhooks,
    register_webhook,
)
from models import WebhookEndpoint, db

logger = logging.getLogger(__name__)

rest_api_bp = Blueprint("rest_api", __name__, url_prefix="/api/v1")


# ---------------------------------------------------------------------------
# Webhook management routes
# ---------------------------------------------------------------------------


@rest_api_bp.route("/webhooks", methods=["GET"])
@require_api_key("webhooks:manage")
def list_webhooks_route():
    """
    List all webhook endpoints for the authenticated organization.

    Returns:
        JSON list of webhook endpoints.
    """
    org_id = getattr(g.api_key, "organization_id", None)
    webhooks = list_webhooks(organization_id=org_id)
    return jsonify(
        {
            "success": True,
            "webhooks": [_serialize_webhook(w) for w in webhooks],
        }
    )


@rest_api_bp.route("/webhooks", methods=["POST"])
@require_api_key("webhooks:manage")
def create_webhook_route():
    """
    Register a new webhook endpoint.

    Request body:
        - url (str): HTTPS URL to deliver events to.
        - events (list): Event types to subscribe to.
        - name (str, optional): Human-readable name.
        - secret (str, optional): HMAC secret (auto-generated if omitted).

    Returns:
        JSON with the created webhook endpoint.
    """
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"success": False, "error": "JSON body required"}), 400

    url = (payload.get("url") or "").strip()
    events = payload.get("events") or []
    name = (payload.get("name") or "").strip()
    secret = (payload.get("secret") or "").strip()

    if not url:
        return jsonify({"success": False, "error": "url is required"}), 400
    if not events:
        return jsonify({"success": False, "error": "events is required"}), 400

    # Validate event types
    invalid = [e for e in events if e not in WEBHOOK_EVENTS]
    if invalid:
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"Invalid event types: {invalid}",
                    "valid_events": WEBHOOK_EVENTS,
                }
            ),
            400,
        )

    if not secret:
        secret = generate_webhook_secret()

    org_id = getattr(g.api_key, "organization_id", None)
    webhook = register_webhook(
        url=url,
        events=events,
        secret=secret,
        name=name or url,
        organization_id=org_id,
        created_by=getattr(g.api_key, "name", None),
    )

    return (
        jsonify(
            {
                "success": True,
                "webhook": _serialize_webhook(webhook),
                "secret": secret,  # Returned only once at creation time
            }
        ),
        201,
    )


@rest_api_bp.route("/webhooks/<int:webhook_id>", methods=["DELETE"])
@require_api_key("webhooks:manage")
def delete_webhook_route(webhook_id):
    """
    Delete a webhook endpoint.

    Args:
        webhook_id: ID of the webhook to delete.

    Returns:
        JSON confirmation of deletion.
    """
    org_id = getattr(g.api_key, "organization_id", None)
    deleted = delete_webhook(webhook_id, organization_id=org_id)
    if not deleted:
        return jsonify({"success": False, "error": "Webhook not found"}), 404
    return jsonify({"success": True, "message": "Webhook deleted"})


@rest_api_bp.route("/webhooks/<int:webhook_id>/deliveries", methods=["GET"])
@require_api_key("webhooks:manage")
def webhook_deliveries_route(webhook_id):
    """
    Get delivery history for a webhook endpoint.

    Query params:
        - limit (int): Max records to return (default 50).
        - offset (int): Records to skip (default 0).

    Returns:
        JSON list of delivery records.
    """
    org_id = getattr(g.api_key, "organization_id", None)

    # Verify the webhook belongs to this organization
    webhook = db.session.get(WebhookEndpoint, webhook_id)
    if webhook is None or (org_id is not None and webhook.organization_id != org_id):
        return jsonify({"success": False, "error": "Webhook not found"}), 404

    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)

    # Clamp limit
    limit = max(1, min(limit, 200))

    deliveries = get_delivery_log(webhook_id, limit=limit, offset=offset)
    return jsonify(
        {
            "success": True,
            "deliveries": [_serialize_delivery(d) for d in deliveries],
        }
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_webhook(webhook) -> dict:
    """Serialize a WebhookEndpoint to a JSON-compatible dict."""
    return {
        "id": webhook.id,
        "name": webhook.name,
        "url": webhook.url,
        "events": webhook.events or [],
        "is_active": webhook.is_active,
        "last_triggered_at": (
            webhook.last_triggered_at.isoformat()
            if webhook.last_triggered_at
            else None
        ),
        "last_status_code": webhook.last_status_code,
        "failure_count": webhook.failure_count or 0,
        "created_at": webhook.created_at.isoformat() if webhook.created_at else None,
    }


def _serialize_delivery(delivery) -> dict:
    """Serialize a WebhookDelivery to a JSON-compatible dict."""
    return {
        "id": delivery.id,
        "webhook_id": delivery.webhook_id,
        "event_type": delivery.event_type,
        "payload_json": delivery.payload_json,
        "status_code": delivery.status_code,
        "response_body": delivery.response_body,
        "retry_count": delivery.retry_count or 0,
        "delivered": delivery.delivered,
        "error_message": delivery.error_message,
        "created_at": delivery.created_at.isoformat() if delivery.created_at else None,
    }
# ---------------------------------------------------------------------------
# Student CRUD routes
# ---------------------------------------------------------------------------

@rest_api_bp.route("/students", methods=["GET"])
@require_api_key("students:read")
def list_students():
    """List students with pagination and filtering."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    per_page = max(10, min(per_page, 200))
    template_id = request.args.get("template_id", type=int)
    school_name = request.args.get("school_name", type=str)

    query = Student.query.order_by(Student.created_at.desc())
    if template_id:
        query = query.filter_by(template_id=template_id)
    if school_name:
        query = query.filter_by(school_name=school_name)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    students = [{
        "id": s.id,
        "name": s.name,
        "father_name": s.father_name,
        "class_name": s.class_name,
        "dob": s.dob,
        "phone": s.phone,
        "email": s.email,
        "school_name": s.school_name,
        "template_id": s.template_id,
        "photo_url": s.photo_url,
        "image_url": s.image_url,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    } for s in pagination.items]

    return jsonify({
        "success": True,
        "students": students,
        "total": pagination.total,
        "page": page,
        "per_page": per_page,
        "pages": pagination.pages,
    })


@rest_api_bp.route("/students/<int:student_id>", methods=["GET"])
@require_api_key("students:read")
def get_student(student_id):
    """Get a single student by ID."""
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({"success": False, "error": "Student not found"}), 404
    return jsonify({
        "success": True,
        "student": {
            "id": student.id,
            "name": student.name,
            "father_name": student.father_name,
            "class_name": student.class_name,
            "dob": student.dob,
            "phone": student.phone,
            "email": student.email,
            "address": student.address,
            "school_name": student.school_name,
            "template_id": student.template_id,
            "photo_url": student.photo_url,
            "image_url": student.image_url,
            "pdf_url": student.pdf_url,
            "created_at": student.created_at.isoformat() if student.created_at else None,
        },
    })


@rest_api_bp.route("/students", methods=["POST"])
@require_api_key("students:write")
def create_student():
    """Create a new student."""
    data = request.get_json(silent=True) or {}
    required = ["name", "school_name", "template_id"]
    for field in required:
        if field not in data:
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

    student = Student(
        name=data["name"],
        father_name=data.get("father_name", ""),
        class_name=data.get("class_name", ""),
        dob=data.get("dob", ""),
        phone=data.get("phone", ""),
        email=data.get("email", ""),
        address=data.get("address", ""),
        school_name=data["school_name"],
        template_id=data["template_id"],
        photo_url=data.get("photo_url", ""),
    )
    db.session.add(student)
    db.session.commit()
    logger.info(f"API: Created student {student.id}: {student.name}")
    return jsonify({"success": True, "student": {"id": student.id, "name": student.name}}), 201


@rest_api_bp.route("/students/<int:student_id>", methods=["PUT"])
@require_api_key("students:write")
def update_student(student_id):
    """Update an existing student."""
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({"success": False, "error": "Student not found"}), 404

    data = request.get_json(silent=True) or {}
    for field in ["name", "father_name", "class_name", "dob", "phone", "email", "address", "school_name", "template_id", "photo_url"]:
        if field in data:
            setattr(student, field, data[field])

    db.session.commit()
    logger.info(f"API: Updated student {student.id}")
    return jsonify({"success": True, "student": {"id": student.id, "name": student.name}})


@rest_api_bp.route("/students/<int:student_id>", methods=["DELETE"])
@require_api_key("students:write")
def delete_student(student_id):
    """Delete a student."""
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({"success": False, "error": "Student not found"}), 404

    db.session.delete(student)
    db.session.commit()
    logger.info(f"API: Deleted student {student_id}")
    return jsonify({"success": True, "message": "Student deleted"})
# ---------------------------------------------------------------------------
# Template CRUD routes
# ---------------------------------------------------------------------------

@rest_api_bp.route("/templates", methods=["GET"])
@require_api_key("templates:read")
def list_templates():
    """List templates with pagination."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    query = Template.query.order_by(Template.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    templates = [{
        "id": t.id,
        "filename": t.filename,
        "template_url": t.template_url,
        "school_name": t.school_name,
        "card_orientation": t.card_orientation or "landscape",
        "language": t.language or "english",
        "is_double_sided": bool(t.is_double_sided),
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in pagination.items]
    return jsonify({"success": True, "templates": templates, "total": pagination.total})


@rest_api_bp.route("/templates/<int:template_id>", methods=["GET"])
@require_api_key("templates:read")
def get_template(template_id):
    """Get a single template."""
    t = db.session.get(Template, template_id)
    if not t:
        return jsonify({"success": False, "error": "Template not found"}), 404
    return jsonify({
        "success": True,
        "template": {
            "id": t.id, "filename": t.filename, "template_url": t.template_url,
            "school_name": t.school_name, "card_orientation": t.card_orientation or "landscape",
            "language": t.language or "english", "text_direction": t.text_direction or "ltr",
            "is_double_sided": bool(t.is_double_sided),
            "font_settings": t.font_settings, "photo_settings": t.photo_settings,
            "qr_settings": t.qr_settings, "layout_config": t.layout_config,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        },
    })


@rest_api_bp.route("/templates", methods=["POST"])
@require_api_key("templates:write")
def create_template():
    """Create a new template."""
    data = request.get_json(silent=True) or {}
    for field in ["filename", "school_name"]:
        if field not in data:
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400
    from app.legacy_app import get_default_font_config, get_default_photo_config, get_default_qr_config
    t = Template(
        filename=data["filename"], school_name=data["school_name"],
        card_orientation=data.get("card_orientation", "landscape"),
        language=data.get("language", "english"),
        text_direction=data.get("text_direction", "ltr"),
        font_settings=get_default_font_config(),
        photo_settings=get_default_photo_config(),
        qr_settings=get_default_qr_config(),
    )
    db.session.add(t)
    db.session.commit()
    return jsonify({"success": True, "template": {"id": t.id, "filename": t.filename}}), 201


@rest_api_bp.route("/templates/<int:template_id>", methods=["DELETE"])
@require_api_key("templates:write")
def delete_template(template_id):
    """Delete a template."""
    t = db.session.get(Template, template_id)
    if not t:
        return jsonify({"success": False, "error": "Template not found"}), 404
    db.session.delete(t)
    db.session.commit()
    return jsonify({"success": True, "message": "Template deleted"})
# ---------------------------------------------------------------------------
# Bulk Job routes
# ---------------------------------------------------------------------------

@rest_api_bp.route("/bulk-jobs", methods=["GET"])
@require_api_key("bulk:read")
def api_list_bulk_jobs():
    """List bulk jobs."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    from app.services.bulk_job_service import _list_bulk_job_states
    jobs = _list_bulk_job_states(limit=per_page)
    return jsonify({"success": True, "jobs": jobs})


@rest_api_bp.route("/bulk-jobs/<task_id>", methods=["GET"])
@require_api_key("bulk:read")
def api_get_bulk_job(task_id):
    """Get bulk job status."""
    from app.services.bulk_job_service import _get_bulk_job_state
    job = _get_bulk_job_state(task_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    return jsonify({"success": True, "job": job})


@rest_api_bp.route("/bulk-jobs/<task_id>/cancel", methods=["POST"])
@require_api_key("bulk:write")
def api_cancel_bulk_job(task_id):
    """Cancel a bulk job."""
    from app.services.bulk_job_service import _set_bulk_job_state
    _set_bulk_job_state(task_id, cancel_requested=True, status="Cancellation requested")
    return jsonify({"success": True, "message": "Cancellation requested"})


# ---------------------------------------------------------------------------
# Print Queue routes
# ---------------------------------------------------------------------------

@rest_api_bp.route("/print-queue", methods=["GET"])
@require_api_key("print:read")
def api_get_print_queue():
    """Get print queue status and jobs."""
    status_filter = request.args.get("status", type=str)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    from app.services.print_queue_service import get_queue_status, get_queue_jobs
    stats = get_queue_status()
    jobs, total = get_queue_jobs(status=status_filter, page=page, per_page=per_page)
    return jsonify({
        "success": True,
        "stats": stats,
        "jobs": [{"id": j.id, "template_id": j.template_id, "student_id": j.student_id,
                  "job_type": j.job_type, "priority": j.priority, "status": j.status,
                  "created_at": j.created_at.isoformat() if j.created_at else None} for j in jobs],
        "total": total,
    })


@rest_api_bp.route("/print-queue", methods=["POST"])
@require_api_key("print:write")
def api_add_print_job():
    """Add a print job to the queue."""
    data = request.get_json(silent=True) or {}
    for field in ["template_id"]:
        if field not in data:
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400
    from app.services.print_queue_service import add_print_job
    job_id = add_print_job(
        template_id=data["template_id"],
        student_id=data.get("student_id"),
        job_type=data.get("job_type", "single"),
        priority=data.get("priority", 5),
        card_side=data.get("card_side", "front"),
        copies=data.get("copies", 1),
    )
    return jsonify({"success": True, "job_id": job_id}), 201


@rest_api_bp.route("/print-queue/<int:job_id>/cancel", methods=["POST"])
@require_api_key("print:write")
def api_cancel_print_job(job_id):
    """Cancel a print job."""
    from app.services.print_queue_service import cancel_job
    if cancel_job(job_id):
        return jsonify({"success": True, "message": "Job cancelled"})
    return jsonify({"success": False, "error": "Job not found or already processed"}), 400


@rest_api_bp.route("/print-queue/<int:job_id>/reprint", methods=["POST"])
@require_api_key("print:write")
def api_reprint_job(job_id):
    """Create a reprint job."""
    from app.services.print_queue_service import reprint_job
    new_id = reprint_job(job_id, priority=3)
    if new_id:
        return jsonify({"success": True, "job_id": new_id, "message": "Reprint job created"})
    return jsonify({"success": False, "error": "Original job not found"}), 404


@rest_api_bp.route("/print-history", methods=["GET"])
@require_api_key("print:read")
def api_print_history():
    """Get print history."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    template_id = request.args.get("template_id", type=int)
    from app.services.print_queue_service import get_print_history
    jobs, total = get_print_history(template_id=template_id, page=page, per_page=per_page)
    return jsonify({
        "success": True,
        "history": [{"id": j.id, "template_id": j.template_id, "student_id": j.student_id,
                     "status": j.status, "created_at": j.created_at.isoformat() if j.created_at else None} for j in jobs],
        "total": total,
    })


# ---------------------------------------------------------------------------
# NFC Encoding routes
# ---------------------------------------------------------------------------

@rest_api_bp.route("/nfc-encodings", methods=["GET"])
@require_api_key("nfc:read")
def api_list_nfc_encodings():
    """List NFC encoding jobs."""
    status_filter = request.args.get("status", type=str)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    from app.services.nfc_service import list_encodings, get_encoding_stats
    jobs, total = list_encodings(status=status_filter, page=page, per_page=per_page)
    stats = get_encoding_stats()
    return jsonify({
        "success": True,
        "encodings": [get_encoding_status(j.id) for j in jobs],
        "stats": stats, "total": total,
    })


@rest_api_bp.route("/nfc-encodings", methods=["POST"])
@require_api_key("nfc:write")
def api_create_nfc_encoding():
    """Create an NFC encoding job."""
    data = request.get_json(silent=True) or {}
    for field in ["student_id", "template_id"]:
        if field not in data:
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400
    from app.services.nfc_service import create_encoding_job
    job_id, msg = create_encoding_job(
        student_id=data["student_id"], template_id=data["template_id"],
        chip_type=data.get("chip_type", "MIFARE_1K"),
    )
    if job_id:
        return jsonify({"success": True, "encoding_id": job_id}), 201
    return jsonify({"success": False, "error": msg}), 400


@rest_api_bp.route("/nfc-encodings/<int:encoding_id>/encode", methods=["POST"])
@require_api_key("nfc:write")
def api_encode_nfc(encoding_id):
    """Execute NFC encoding."""
    from app.services.nfc_service import encode_chip
    success, msg = encode_chip(encoding_id)
    if success:
        return jsonify({"success": True, "message": "Encoding completed"})
    return jsonify({"success": False, "error": msg}), 400


@rest_api_bp.route("/nfc-encodings/<int:encoding_id>/verify", methods=["POST"])
@require_api_key("nfc:write")
def api_verify_nfc(encoding_id):
    """Verify an NFC encoding."""
    from app.services.nfc_service import verify_encoding
    success, msg = verify_encoding(encoding_id)
    if success:
        return jsonify({"success": True, "message": "Verification passed"})
    return jsonify({"success": False, "error": msg}), 400


@rest_api_bp.route("/nfc-encodings/<int:encoding_id>", methods=["GET"])
@require_api_key("nfc:read")
def api_get_nfc_encoding(encoding_id):
    """Get NFC encoding status."""
    from app.services.nfc_service import get_encoding_status
    status = get_encoding_status(encoding_id)
    if not status:
        return jsonify({"success": False, "error": "Encoding not found"}), 404
    return jsonify({"success": True, "encoding": status})


@rest_api_bp.route("/nfc/batch-encode", methods=["POST"])
@require_api_key("nfc:write")
def api_batch_nfc_encode():
    """Batch create NFC encoding jobs for a template."""
    data = request.get_json(silent=True) or {}
    for field in ["template_id"]:
        if field not in data:
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400
    from app.services.nfc_service import batch_create_encoding_jobs
    job_ids, errors = batch_create_encoding_jobs(
        template_id=data["template_id"],
        chip_type=data.get("chip_type", "MIFARE_1K"),
    )
    return jsonify({
        "success": True, "created": len(job_ids), "job_ids": job_ids,
        "errors": errors,
    }), 201
