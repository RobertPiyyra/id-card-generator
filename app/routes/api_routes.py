import os
import logging
from datetime import datetime, timezone
import csv
import io
from flask import Blueprint, request, jsonify, render_template, url_for, session
from sqlalchemy import text

from models import (
    db,
    Student,
    Template,
    TemplateField,
    TemplateVersion,
    TemplateWorkflow,
    ImmutableAuditEvent,
    VerificationAudit,
    ImportMapping,
    DisasterRecoverySnapshot,
)
from app.services.template_lifecycle_service import (
    apply_snapshot_to_template,
    create_template_version_snapshot,
    get_or_create_template_workflow,
    get_session_actor,
    log_immutable_audit_event,
    transition_template_workflow,
)
from app.services.premium_service import (
    run_design_qa,
    build_signed_verify_token,
    parse_signed_verify_token,
    simple_photo_quality_score,
)
from utils import PLACEHOLDER_PATH
from utils import get_localized_standard_labels
from app.services import redis_service
from app.services.redis_service import _redis_candidate_urls, get_redis_client
from app.services.bulk_job_service import _get_bulk_job_state, _list_bulk_job_states, _set_bulk_job_state
from app.services.photo_service import resolve_student_photo_reference
from app.legacy_app import admin_required, super_admin_required

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)

# ================== Task Status Route ==================
@api_bp.route('/taskstatus/<task_id>')
def taskstatus(task_id):
    task = _get_bulk_job_state(task_id)
    if not task:
        return jsonify({'state': 'FAILURE', 'status': 'Task not found', 'errors': ['Task not found']}), 404
    return jsonify(task)


@api_bp.route('/admin/bulk-jobs', methods=['GET'])
@admin_required
def list_bulk_jobs():
    limit = request.args.get("limit", 100, type=int)
    rows = _list_bulk_job_states(limit=limit)
    return jsonify({"success": True, "jobs": rows})


@api_bp.route('/admin/bulk-jobs/<task_id>/cancel', methods=['POST'])
@admin_required
def cancel_bulk_job(task_id):
    task = _get_bulk_job_state(task_id)
    if not task:
        return jsonify({"success": False, "message": "Task not found"}), 404
    current_state = str(task.get("state") or "").upper()
    if current_state in {"SUCCESS", "FAILURE", "CANCELLED"}:
        return jsonify({"success": False, "message": f"Cannot cancel task in state {current_state}"}), 400
    _set_bulk_job_state(
        task_id,
        cancel_requested=True,
        status="Cancellation requested by admin...",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    return jsonify({"success": True, "message": "Cancellation requested", "task_id": task_id})


@api_bp.route('/admin/bulk-jobs/<task_id>/failed-rows.csv', methods=['GET'])
@admin_required
def bulk_job_failed_rows_csv(task_id):
    task = _get_bulk_job_state(task_id)
    if not task:
        return jsonify({"success": False, "message": "Task not found"}), 404
    errors = task.get("errors") if isinstance(task, dict) else None
    errors = errors if isinstance(errors, list) else []

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["row_number", "error_message"])
    for e in errors:
        msg = str(e or "").strip()
        row_no = ""
        if msg.lower().startswith("row "):
            parts = msg.split(":", 1)
            head = parts[0].strip()
            tail = parts[1].strip() if len(parts) > 1 else msg
            try:
                row_no = int(head.split()[1])
            except Exception:
                row_no = ""
            writer.writerow([row_no, tail])
        else:
            writer.writerow([row_no, msg])

    csv_bytes = output.getvalue().encode("utf-8")
    from flask import Response
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=bulk_failed_rows_{task_id}.csv"},
    )


# ================== Template Form Fields Routes ==================
@api_bp.route('/admin/template/<int:template_id>/form-fields', methods=['GET', 'POST'])
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
                    'show_label_front': bool(getattr(field, 'show_label_front', True)),
                    'show_value_front': bool(getattr(field, 'show_value_front', True)),
                    'show_label_back': bool(getattr(field, 'show_label_back', False)),
                    'show_value_back': bool(getattr(field, 'show_value_back', False)),
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
            show_label_front = str(data.get('show_label_front', 1)).lower() in ['true', '1', 'on', 'yes']
            show_value_front = str(data.get('show_value_front', 1)).lower() in ['true', '1', 'on', 'yes']
            show_label_back = str(data.get('show_label_back', 0)).lower() in ['true', '1', 'on', 'yes']
            show_value_back = str(data.get('show_value_back', 0)).lower() in ['true', '1', 'on', 'yes']
            
            display_order = int(data.get('display_order', 0))
            field_options = data.get('field_options', [])
            
            if not field_name or not field_label:
                return jsonify({"success": False, "message": "Name and Label are required"}), 400
            if not any([show_label_front, show_value_front, show_label_back, show_value_back]):
                return jsonify({"success": False, "message": "Enable at least one label or value side visibility"}), 400
            
            field = TemplateField(
                template_id=template_id,
                field_name=field_name,
                field_label=field_label,
                field_type=field_type,
                is_required=is_required,
                show_label_front=show_label_front,
                show_value_front=show_value_front,
                show_label_back=show_label_back,
                show_value_back=show_value_back,
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


@api_bp.route('/admin/template/form-fields/<int:field_id>', methods=['PUT', 'DELETE'])
@admin_required
def manage_single_field(field_id):
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

            if 'show_label_front' in data:
                field.show_label_front = str(data.get('show_label_front')).lower() in ['true', '1', 'on', 'yes']

            if 'show_value_front' in data:
                field.show_value_front = str(data.get('show_value_front')).lower() in ['true', '1', 'on', 'yes']

            if 'show_label_back' in data:
                field.show_label_back = str(data.get('show_label_back')).lower() in ['true', '1', 'on', 'yes']

            if 'show_value_back' in data:
                field.show_value_back = str(data.get('show_value_back')).lower() in ['true', '1', 'on', 'yes']

            if not any([
                bool(getattr(field, 'show_label_front', True)),
                bool(getattr(field, 'show_value_front', True)),
                bool(getattr(field, 'show_label_back', False)),
                bool(getattr(field, 'show_value_back', False)),
            ]):
                return jsonify({"success": False, "message": "Enable at least one label or value side visibility"}), 400

            if 'display_order' in data:
                field.display_order = int(data.get('display_order'))
                
            if 'field_options' in data:
                field.field_options = list(data.get('field_options', []))
            
            db.session.commit()
            return jsonify({"success": True, "message": "Field updated successfully"})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating/deleting field {field_id}: {e}")
        return jsonify({"success": False, "message": f"Server Error: {str(e)}"}), 500


# Verification routes relocated to verify_routes.py


# ================== Health Check Route ==================
@api_bp.route("/health")
def health_check():
    try:
        db.session.execute(text("SELECT 1"))
        redis_status = "disabled"
        redis_mode = None
        if _redis_candidate_urls():
            redis_status = "connected" if get_redis_client() is not None else "unavailable"
            redis_mode = redis_service.redis_connection_mode()
        return jsonify({"status": "healthy", "db": "connected", "redis": redis_status, "redis_mode": redis_mode}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

__all__ = ["api_bp"]


@api_bp.route("/admin/template/<int:template_id>/versions", methods=["GET"])
@admin_required
def template_versions(template_id):
    versions = (
        TemplateVersion.query.filter_by(template_id=template_id)
        .order_by(TemplateVersion.version_number.desc())
        .limit(100)
        .all()
    )
    return jsonify(
        {
            "success": True,
            "versions": [
                {
                    "id": v.id,
                    "version_number": v.version_number,
                    "source": v.source,
                    "created_by": v.created_by,
                    "created_role": v.created_role,
                    "rollback_of_version_id": v.rollback_of_version_id,
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                }
                for v in versions
            ],
        }
    )


def compare_template_snapshots(s1, s2):
    import json
    diffs = {}
    
    # 1. Compare core properties
    core_keys = [
        "card_orientation", "language", "text_direction", "is_double_sided",
        "duplex_flip_mode", "card_width", "card_height", "sheet_width",
        "sheet_height", "grid_rows", "grid_cols"
    ]
    core_diffs = {}
    for k in core_keys:
        v1 = s1.get(k)
        v2 = s2.get(k)
        if v1 != v2:
            core_diffs[k] = {"from": v1, "to": v2}
    if core_diffs:
        diffs["core"] = core_diffs

    # 2. Compare nested dicts (font_settings, photo_settings, qr_settings)
    for section in ["font_settings", "photo_settings", "qr_settings",
                    "back_font_settings", "back_photo_settings", "back_qr_settings"]:
        d1 = s1.get(section) or {}
        d2 = s2.get(section) or {}
        sec_diffs = {}
        all_keys = set(d1.keys()) | set(d2.keys())
        for k in all_keys:
            v1 = d1.get(k)
            v2 = d2.get(k)
            if v1 != v2:
                sec_diffs[k] = {"from": v1, "to": v2}
        if sec_diffs:
            diffs[section] = sec_diffs

    # 3. Compare layout_config and back_layout_config
    for layout in ["layout_config", "back_layout_config"]:
        l1 = s1.get(layout) or {}
        l2 = s2.get(layout) or {}
        if isinstance(l1, str):
            try: l1 = json.loads(l1)
            except: l1 = {}
        if isinstance(l2, str):
            try: l2 = json.loads(l2)
            except: l2 = {}
            
        layout_diffs = {}
        
        # Compare fields
        f1 = l1.get("fields") or {}
        f2 = l2.get("fields") or {}
        field_diffs = {}
        all_fields = set(f1.keys()) | set(f2.keys())
        for f in all_fields:
            props1 = f1.get(f) or {}
            props2 = f2.get(f) or {}
            prop_diffs = {}
            all_props = set(props1.keys()) | set(props2.keys())
            for p in all_props:
                val1 = props1.get(p)
                val2 = props2.get(p)
                if val1 != val2:
                    prop_diffs[p] = {"from": val1, "to": val2}
            if prop_diffs:
                field_diffs[f] = prop_diffs
        if field_diffs:
            layout_diffs["fields"] = field_diffs
            
        # Compare custom objects
        obj1 = {o.get("id"): o for o in (l1.get("objects") or []) if o.get("id")}
        obj2 = {o.get("id"): o for o in (l2.get("objects") or []) if o.get("id")}
        obj_diffs = {"added": [], "deleted": [], "modified": []}
        for oid, o in obj2.items():
            if oid not in obj1:
                obj_diffs["added"].append(o)
            else:
                o1 = obj1[oid]
                o2 = o
                prop_diffs = {}
                for pk, pv in o2.items():
                    if o1.get(pk) != pv:
                        prop_diffs[pk] = {"from": o1.get(pk), "to": pv}
                if prop_diffs:
                    obj_diffs["modified"].append({
                        "id": oid,
                        "name": o2.get("name") or o2.get("text") or "Custom Object",
                        "type": o2.get("type"),
                        "changes": prop_diffs
                    })
        for oid, o in obj1.items():
            if oid not in obj2:
                obj_diffs["deleted"].append(o)
        
        if obj_diffs["added"] or obj_diffs["deleted"] or obj_diffs["modified"]:
            layout_diffs["objects"] = obj_diffs
            
        if layout_diffs:
            diffs[layout] = layout_diffs

    return diffs


@api_bp.route("/admin/template/<int:template_id>/version-diff/<int:v1_id>/<int:v2_id>", methods=["GET"])
@admin_required
def template_version_diff(template_id, v1_id, v2_id):
    v1 = TemplateVersion.query.filter_by(id=v1_id, template_id=template_id).first()
    v2 = TemplateVersion.query.filter_by(id=v2_id, template_id=template_id).first()
    
    if not v1 or not v2:
        return jsonify({"success": False, "message": "One or both versions not found"}), 404
        
    s1 = v1.snapshot_json or {}
    s2 = v2.snapshot_json or {}
    
    if v1.version_number > v2.version_number:
        s1, s2 = s2, s1
        v1_num, v2_num = v2.version_number, v1.version_number
        v1_date, v2_date = v2.created_at, v1.created_at
        v1_by, v2_by = v2.created_by, v1.created_by
    else:
        v1_num, v2_num = v1.version_number, v2.version_number
        v1_date, v2_date = v1.created_at, v2.created_at
        v1_by, v2_by = v1.created_by, v2.created_by

    diffs = compare_template_snapshots(s1, s2)
    
    return jsonify({
        "success": True,
        "v1_number": v1_num,
        "v2_number": v2_num,
        "v1_created_at": v1_date.isoformat() if v1_date else None,
        "v2_created_at": v2_date.isoformat() if v2_date else None,
        "v1_created_by": v1_by,
        "v2_created_by": v2_by,
        "diffs": diffs
    })


@api_bp.route("/admin/template/<int:template_id>/rollback/<int:version_id>", methods=["POST"])
@admin_required
def rollback_template_version(template_id, version_id):
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({"success": False, "message": "Template not found"}), 404
    version = TemplateVersion.query.filter_by(id=version_id, template_id=template_id).first()
    if not version:
        return jsonify({"success": False, "message": "Version not found"}), 404
    try:
        apply_snapshot_to_template(template, version.snapshot_json or {})
        actor, actor_role = get_session_actor()
        create_template_version_snapshot(
            template,
            source="rollback",
            note=f"Rollback to version #{version.version_number}",
            rollback_of_version_id=version.id,
            actor=actor,
            actor_role=actor_role,
        )
        log_immutable_audit_event(
            entity_type="template",
            entity_id=template_id,
            action="template_rolled_back",
            payload={"template_id": template_id, "target_version_id": version_id, "target_version_number": version.version_number},
            actor=actor,
            actor_role=actor_role,
        )
        db.session.commit()
        return jsonify({"success": True, "message": "Rollback applied"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route("/admin/template/<int:template_id>/workflow", methods=["GET", "POST"])
@admin_required
def template_workflow(template_id):
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({"success": False, "message": "Template not found"}), 404

    if request.method == "GET":
        wf = get_or_create_template_workflow(template_id)
        db.session.commit()
        return jsonify(
            {
                "success": True,
                "state": wf.state,
                "updated_by": wf.updated_by,
                "updated_role": wf.updated_role,
                "updated_at": wf.updated_at.isoformat() if wf.updated_at else None,
                "note": wf.note,
            }
        )

    payload = request.get_json(silent=True) or {}
    new_state = payload.get("state")
    note = payload.get("note")
    try:
        actor, actor_role = get_session_actor()
        wf = transition_template_workflow(template_id, new_state, note=note, actor=actor, actor_role=actor_role)
        log_immutable_audit_event(
            entity_type="template",
            entity_id=template_id,
            action="template_workflow_transition",
            payload={"template_id": template_id, "new_state": wf.state, "note": note},
            actor=actor,
            actor_role=actor_role,
        )
        db.session.commit()
        return jsonify({"success": True, "state": wf.state})
    except PermissionError as pe:
        db.session.rollback()
        return jsonify({"success": False, "message": str(pe)}), 403
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 400


@api_bp.route("/admin/audit-events", methods=["GET"])
@admin_required
def admin_audit_events():
    entity_type = (request.args.get("entity_type") or "").strip()
    entity_id = (request.args.get("entity_id") or "").strip()
    q = ImmutableAuditEvent.query
    if entity_type:
        q = q.filter(ImmutableAuditEvent.entity_type == entity_type)
    if entity_id:
        q = q.filter(ImmutableAuditEvent.entity_id == entity_id)
    rows = q.order_by(ImmutableAuditEvent.id.desc()).limit(200).all()
    return jsonify(
        {
            "success": True,
            "events": [
                {
                    "id": r.id,
                    "entity_type": r.entity_type,
                    "entity_id": r.entity_id,
                    "action": r.action,
                    "actor": r.actor,
                    "actor_role": r.actor_role,
                    "payload_json": r.payload_json,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }
    )


@api_bp.route("/admin/template/<int:template_id>/design-qa", methods=["GET"])
@admin_required
def template_design_qa(template_id):
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({"success": False, "message": "Template not found"}), 404
    return jsonify({"success": True, "qa": run_design_qa(template)})


@api_bp.route("/admin/template/<int:template_id>/qa-settings", methods=["GET", "POST"])
@admin_required
def template_qa_settings(template_id):
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({"success": False, "message": "Template not found"}), 404
    if request.method == "GET":
        return jsonify({"success": True, "qa_settings": template.qa_settings or {}})
    payload = request.get_json(silent=True) or {}
    template.qa_settings = payload.get("qa_settings") or {}
    db.session.commit()
    return jsonify({"success": True, "qa_settings": template.qa_settings})


@api_bp.route("/admin/preview-modes/<int:student_id>", methods=["GET"])
@admin_required
def preview_modes(student_id):
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({"success": False, "message": "Student not found"}), 404
    visual_url = url_for("dashboard.student_preview", student_id=student_id)
    print_safe_url = url_for("dashboard.generate_preview", student_id=student_id)
    corel_url = url_for("corel.corel_preview", template_id=student.template_id, student_id=student_id, side="front")
    return jsonify({"success": True, "visual_url": visual_url, "print_safe_url": print_safe_url, "corel_url": corel_url})


@api_bp.route("/admin/template/<int:template_id>/batch-rules", methods=["GET", "POST"])
@admin_required
def template_batch_rules(template_id):
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({"success": False, "message": "Template not found"}), 404
    if request.method == "GET":
        return jsonify({"success": True, "rules": template.batch_rules or {}})
    payload = request.get_json(silent=True) or {}
    template.batch_rules = payload.get("rules") or {}
    db.session.commit()
    return jsonify({"success": True, "rules": template.batch_rules})


@api_bp.route("/verify/v2/token/<int:student_id>", methods=["POST"])
@admin_required
def issue_verify_token_v2(student_id):
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({"success": False, "message": "Student not found"}), 404
    ttl = int((request.get_json(silent=True) or {}).get("ttl_seconds") or 3600)
    token_id = f"{student_id}-{int(datetime.now(timezone.utc).timestamp())}"
    token = build_signed_verify_token(
        secret_key=os.getenv("SECRET_KEY", "dev-key"),
        student_id=student.id,
        template_id=student.template_id or 0,
        token_id=token_id,
    )
    return jsonify({"success": True, "token": token, "expires_in": ttl, "verify_url": url_for("api.verify_v2", token=token, _external=True)})

# verification_v2 handler relocated to verify_routes.py


@api_bp.route("/admin/analytics/overview", methods=["GET"])
@admin_required
def analytics_overview():
    rows = db.session.query(Template.school_name, db.func.count(Student.id)).join(Student, Student.template_id == Template.id, isouter=True).group_by(Template.school_name).all()
    by_school = [{"school_name": r[0], "cards_generated": int(r[1] or 0)} for r in rows]
    revoked = db.session.query(db.func.count(Student.id)).filter(Student.verification_revoked == True).scalar() or 0
    low_quality = db.session.query(db.func.count(Student.id)).filter(Student.photo_quality_status == "fail").scalar() or 0
    duplicate_hash = db.session.query(db.func.count(Student.id)).filter(Student.data_hash.isnot(None)).scalar() or 0
    return jsonify({"success": True, "by_school": by_school, "revoked_count": int(revoked), "photo_quality_fail_count": int(low_quality), "records_with_hash": int(duplicate_hash)})


@api_bp.route("/admin/template/<int:template_id>/localization-pack", methods=["GET", "POST"])
@admin_required
def localization_pack(template_id):
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({"success": False, "message": "Template not found"}), 404
    if request.method == "GET":
        return jsonify({"success": True, "localization_pack": template.localization_pack or {}, "language_lock_rules": template.language_lock_rules or {}})
    payload = request.get_json(silent=True) or {}
    template.localization_pack = payload.get("localization_pack") or {}
    template.language_lock_rules = payload.get("language_lock_rules") or {}
    db.session.commit()
    return jsonify({"success": True})


@api_bp.route("/admin/template/<int:template_id>/localized-labels", methods=["GET"])
@admin_required
def template_localized_labels(template_id):
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({"success": False, "message": "Template not found"}), 404
    side = (request.args.get("side") or "front").strip().lower()
    if side not in {"front", "back"}:
        side = "front"
    if side == "back":
        language = (template.back_language or template.language or "english").strip().lower()
    else:
        language = (template.language or "english").strip().lower()
    lock_rules = template.language_lock_rules or {}
    if isinstance(lock_rules, dict):
        locked = str(lock_rules.get(side, "") or "").strip().lower()
        if locked:
            language = locked
    labels = get_localized_standard_labels(language, template.localization_pack or {})
    return jsonify({"success": True, "side": side, "language": language, "labels": labels})


@api_bp.route("/admin/template/<int:template_id>/branding", methods=["GET", "POST"])
@admin_required
def template_branding(template_id):
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({"success": False, "message": "Template not found"}), 404
    if request.method == "GET":
        return jsonify({"success": True, "branding_config": template.branding_config or {}, "print_profile": template.print_profile or {}})
    payload = request.get_json(silent=True) or {}
    template.branding_config = payload.get("branding_config") or {}
    template.print_profile = payload.get("print_profile") or {}
    db.session.commit()
    return jsonify({"success": True})


@api_bp.route("/admin/import-mappings/<int:template_id>", methods=["GET", "POST"])
@admin_required
def import_mappings(template_id):
    if request.method == "GET":
        rows = ImportMapping.query.filter_by(template_id=template_id).order_by(ImportMapping.updated_at.desc()).all()
        return jsonify({"success": True, "mappings": [{"id": r.id, "name": r.name, "mapping_json": r.mapping_json} for r in rows]})
    payload = request.get_json(silent=True) or {}
    row = ImportMapping(
        template_id=template_id,
        school_name=payload.get("school_name"),
        name=(payload.get("name") or f"mapping-{template_id}"),
        mapping_json=payload.get("mapping_json") or {},
        created_by=session.get("student_email"),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "id": row.id})


@api_bp.route("/admin/import-mappings/preview", methods=["POST"])
@admin_required
def import_mapping_preview():
    payload = request.get_json(silent=True) or {}
    headers = payload.get("headers") or []
    mapping = payload.get("mapping_json") or {}
    required = ["name"]
    missing = []
    for req in required:
        src = mapping.get(req)
        if not src or src not in headers:
            missing.append(req)
    return jsonify({"success": True, "valid": len(missing) == 0, "missing_required_targets": missing, "headers": headers, "mapping": mapping})


@api_bp.route("/admin/disaster-recovery/snapshot", methods=["POST"])
@admin_required
def dr_create_snapshot():
    payload = {
        "templates": [
            {
                "id": t.id,
                "school_name": t.school_name,
                "font_settings": t.font_settings,
                "photo_settings": t.photo_settings,
                "qr_settings": t.qr_settings,
                "layout_config": t.layout_config,
                "back_layout_config": t.back_layout_config,
                "batch_rules": t.batch_rules,
                "localization_pack": t.localization_pack,
                "branding_config": t.branding_config,
                "print_profile": t.print_profile,
            }
            for t in Template.query.all()
        ]
    }
    name = f"snapshot-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    row = DisasterRecoverySnapshot(snapshot_name=name, scope="full", payload_json=payload, created_by=session.get("student_email"))
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "snapshot_id": row.id, "snapshot_name": row.snapshot_name})


def _restore_snapshot_payload(payload):
    restored = 0
    for t in payload.get("templates", []) or []:
        template = db.session.get(Template, int(t.get("id"))) if t.get("id") else None
        if not template:
            continue
        template.font_settings = t.get("font_settings") or template.font_settings
        template.photo_settings = t.get("photo_settings") or template.photo_settings
        template.qr_settings = t.get("qr_settings") or template.qr_settings
        template.layout_config = t.get("layout_config")
        template.back_layout_config = t.get("back_layout_config")
        template.batch_rules = t.get("batch_rules") or {}
        template.localization_pack = t.get("localization_pack") or {}
        template.branding_config = t.get("branding_config") or {}
        template.print_profile = t.get("print_profile") or {}
        restored += 1
    db.session.commit()
    return restored


@api_bp.route("/admin/disaster-recovery/snapshots", methods=["GET"])
@admin_required
def dr_list_snapshots():
    rows = DisasterRecoverySnapshot.query.order_by(DisasterRecoverySnapshot.created_at.desc()).limit(100).all()
    return jsonify({"success": True, "snapshots": [{"id": r.id, "snapshot_name": r.snapshot_name, "created_at": r.created_at.isoformat()} for r in rows]})


@api_bp.route("/admin/disaster-recovery/restore/<int:snapshot_id>", methods=["POST"])
@admin_required
def dr_restore_snapshot(snapshot_id):
    snap = db.session.get(DisasterRecoverySnapshot, snapshot_id)
    if not snap:
        return jsonify({"success": False, "message": "Snapshot not found"}), 404
    payload = snap.payload_json or {}
    restored = _restore_snapshot_payload(payload)
    return jsonify({"success": True, "restored_templates": restored})


@api_bp.route("/admin/disaster-recovery/restore-to-date", methods=["POST"])
@admin_required
def dr_restore_to_date():
    payload = request.get_json(silent=True) or {}
    target_at = (payload.get("target_at") or "").strip()
    if not target_at:
        return jsonify({"success": False, "message": "target_at is required"}), 400
    try:
        normalized = target_at.replace("Z", "+00:00")
        target_dt = datetime.fromisoformat(normalized)
        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=timezone.utc)
        target_dt = target_dt.astimezone(timezone.utc)
    except Exception:
        return jsonify({"success": False, "message": "Invalid target_at format"}), 400

    snap = (
        DisasterRecoverySnapshot.query
        .filter(DisasterRecoverySnapshot.created_at <= target_dt)
        .order_by(DisasterRecoverySnapshot.created_at.desc())
        .first()
    )
    if not snap:
        return jsonify({"success": False, "message": "No snapshot exists at/before requested date"}), 404
    restored = _restore_snapshot_payload(snap.payload_json or {})
    return jsonify({
        "success": True,
        "restored_templates": restored,
        "snapshot_id": snap.id,
        "snapshot_name": snap.snapshot_name,
        "snapshot_created_at": snap.created_at.isoformat() if snap.created_at else None,
    })


@api_bp.route("/admin/students/<int:student_id>/photo-quality", methods=["POST"])
@admin_required
def photo_quality_check(student_id):
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({"success": False, "message": "Student not found"}), 404
    photo_url, local_path = resolve_student_photo_reference(student)
    img_bytes = None
    if local_path and os.path.exists(local_path):
        with open(local_path, "rb") as f:
            img_bytes = f.read()
    elif photo_url:
        import requests
        resp = requests.get(photo_url, timeout=10)
        if resp.ok:
            img_bytes = resp.content
    if not img_bytes:
        return jsonify({"success": False, "message": "No photo available"}), 400
    score, status = simple_photo_quality_score(img_bytes)
    student.photo_quality_score = float(score)
    student.photo_quality_status = status
    db.session.commit()
    return jsonify({"success": True, "score": score, "status": status})


@api_bp.route("/admin/students/<int:student_id>/verification-status", methods=["POST"])
@admin_required
def update_student_verification_status(student_id):
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({"success": False, "message": "Student not found"}), 404
    payload = request.get_json(silent=True) or {}
    student.verification_revoked = bool(payload.get("revoked"))
    db.session.commit()
    return jsonify({
        "success": True,
        "student_id": student.id,
        "verification_revoked": bool(student.verification_revoked),
    })


@api_bp.route("/admin/verification-audits", methods=["GET"])
@admin_required
def list_verification_audits():
    student_id = request.args.get("student_id", type=int)
    limit = max(1, min(200, request.args.get("limit", 50, type=int)))
    query = VerificationAudit.query
    if student_id:
        query = query.filter(VerificationAudit.student_id == student_id)
    rows = query.order_by(VerificationAudit.created_at.desc()).limit(limit).all()
    return jsonify({
        "success": True,
        "audits": [
            {
                "id": r.id,
                "student_id": r.student_id,
                "template_id": r.template_id,
                "status": r.status,
                "token_id": r.token_id,
                "ip_address": r.ip_address,
                "user_agent": r.user_agent,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    })
