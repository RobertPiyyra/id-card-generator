import hashlib
import json
from datetime import datetime, timezone

from flask import request, session

from models import (
    db,
    TemplateVersion,
    TemplateWorkflow,
    ImmutableAuditEvent,
)


WORKFLOW_STATES = ("draft", "review", "approved", "published")
WORKFLOW_TRANSITIONS = {
    "draft": {"review"},
    "review": {"draft", "approved"},
    "approved": {"review", "published"},
    "published": {"review"},
}


def get_session_actor():
    if session.get("admin"):
        return (
            session.get("student_email") or session.get("admin_school") or "admin",
            session.get("admin_role") or "admin",
        )
    if session.get("student_email"):
        return session.get("student_email"), "student"
    return "system", "system"


def _stable_json(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _compute_hash(payload):
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def serialize_template_snapshot(template):
    return {
        "template_id": template.id,
        "school_name": template.school_name,
        "filename": template.filename,
        "template_url": template.template_url,
        "back_filename": template.back_filename,
        "back_template_url": template.back_template_url,
        "font_settings": template.font_settings or {},
        "photo_settings": template.photo_settings or {},
        "qr_settings": template.qr_settings or {},
        "back_font_settings": template.back_font_settings or {},
        "back_photo_settings": template.back_photo_settings or {},
        "back_qr_settings": template.back_qr_settings or {},
        "layout_config": template.layout_config,
        "back_layout_config": template.back_layout_config,
        "language": template.language,
        "text_direction": template.text_direction,
        "back_language": template.back_language,
        "back_text_direction": template.back_text_direction,
        "card_orientation": template.card_orientation,
        "deadline": template.deadline.isoformat() if template.deadline else None,
        "is_double_sided": bool(template.is_double_sided),
        "duplex_flip_mode": template.duplex_flip_mode,
        "card_width": template.card_width,
        "card_height": template.card_height,
        "sheet_width": template.sheet_width,
        "sheet_height": template.sheet_height,
        "grid_rows": template.grid_rows,
        "grid_cols": template.grid_cols,
    }


def create_template_version_snapshot(template, *, source="unknown", note=None, rollback_of_version_id=None, actor=None, actor_role=None):
    snapshot = serialize_template_snapshot(template)
    max_version = (
        db.session.query(db.func.max(TemplateVersion.version_number))
        .filter(TemplateVersion.template_id == template.id)
        .scalar()
        or 0
    )
    if actor is None or actor_role is None:
        actor, actor_role = get_session_actor()

    payload = {
        "template_id": template.id,
        "version_number": max_version + 1,
        "source": source,
        "note": note or "",
        "rollback_of_version_id": rollback_of_version_id,
        "snapshot": snapshot,
    }
    checksum = _compute_hash(payload)
    version = TemplateVersion(
        template_id=template.id,
        version_number=max_version + 1,
        snapshot_json=snapshot,
        source=source,
        created_by=actor,
        created_role=actor_role,
        rollback_of_version_id=rollback_of_version_id,
        checksum=checksum,
    )
    db.session.add(version)
    return version


def _client_context():
    try:
        return request.remote_addr, (request.headers.get("User-Agent") or "")[:512]
    except Exception:
        return None, None


def log_immutable_audit_event(*, entity_type, entity_id, action, payload=None, actor=None, actor_role=None):
    payload = payload or {}
    if actor is None or actor_role is None:
        actor, actor_role = get_session_actor()
    ip_address, user_agent = _client_context()
    prev_event = (
        ImmutableAuditEvent.query.order_by(ImmutableAuditEvent.id.desc()).first()
    )
    prev_hash = prev_event.event_hash if prev_event else ""
    raw = {
        "entity_type": str(entity_type),
        "entity_id": str(entity_id),
        "action": str(action),
        "actor": actor,
        "actor_role": actor_role,
        "ip_address": ip_address or "",
        "user_agent": user_agent or "",
        "payload": payload,
        "prev_event_hash": prev_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    event_hash = _compute_hash(raw)
    event = ImmutableAuditEvent(
        entity_type=str(entity_type),
        entity_id=str(entity_id),
        action=str(action),
        actor=actor,
        actor_role=actor_role,
        ip_address=ip_address,
        user_agent=user_agent,
        payload_json=payload,
        prev_event_hash=prev_hash or None,
        event_hash=event_hash,
    )
    db.session.add(event)
    return event


def get_or_create_template_workflow(template_id, *, actor=None, actor_role=None):
    wf = TemplateWorkflow.query.filter_by(template_id=template_id).first()
    if wf:
        return wf
    if actor is None or actor_role is None:
        actor, actor_role = get_session_actor()
    wf = TemplateWorkflow(
        template_id=template_id,
        state="draft",
        updated_by=actor,
        updated_role=actor_role,
    )
    db.session.add(wf)
    return wf


def transition_template_workflow(template_id, new_state, *, note=None, actor=None, actor_role=None):
    state = str(new_state or "").strip().lower()
    if state not in WORKFLOW_STATES:
        raise ValueError("Invalid workflow state")
    if actor is None or actor_role is None:
        actor, actor_role = get_session_actor()

    wf = get_or_create_template_workflow(template_id, actor=actor, actor_role=actor_role)
    current = (wf.state or "draft").strip().lower()
    if current != state and state not in WORKFLOW_TRANSITIONS.get(current, set()):
        raise ValueError(f"Invalid transition: {current} -> {state}")

    if state in {"approved", "published"} and actor_role == "school_admin":
        raise PermissionError("school_admin cannot move template to approved/published")

    wf.state = state
    wf.updated_by = actor
    wf.updated_role = actor_role
    wf.note = note
    wf.updated_at = datetime.now(timezone.utc)
    return wf


def apply_snapshot_to_template(template, snapshot):
    keys = [
        "school_name",
        "filename",
        "template_url",
        "back_filename",
        "back_template_url",
        "font_settings",
        "photo_settings",
        "qr_settings",
        "back_font_settings",
        "back_photo_settings",
        "back_qr_settings",
        "layout_config",
        "back_layout_config",
        "language",
        "text_direction",
        "back_language",
        "back_text_direction",
        "card_orientation",
        "is_double_sided",
        "duplex_flip_mode",
        "card_width",
        "card_height",
        "sheet_width",
        "sheet_height",
        "grid_rows",
        "grid_cols",
    ]
    for key in keys:
        if key in snapshot:
            setattr(template, key, snapshot.get(key))

    deadline_val = snapshot.get("deadline")
    if deadline_val:
        try:
            template.deadline = datetime.fromisoformat(deadline_val.replace("Z", "+00:00"))
        except Exception:
            template.deadline = None
    else:
        template.deadline = None
