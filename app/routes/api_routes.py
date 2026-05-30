import os
import logging
from flask import Blueprint, request, jsonify, render_template, url_for, session
from sqlalchemy import text

from models import db, Student, Template, TemplateField
from utils import PLACEHOLDER_PATH
from app.services import redis_service
from app.services.redis_service import _redis_candidate_urls, get_redis_client

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)


def get_legacy_helpers():
    import app.legacy_app as legacy
    return legacy


# ================== Task Status Route ==================
@api_bp.route('/taskstatus/<task_id>')
def taskstatus(task_id):
    legacy = get_legacy_helpers()
    task = legacy._get_bulk_job_state(task_id)
    if not task:
        return jsonify({'state': 'FAILURE', 'status': 'Task not found', 'errors': ['Task not found']}), 404
    return jsonify(task)


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


# ================== Versatile Verification Route ==================
@api_bp.route("/verify/<student_identifier>")
def verify_student(student_identifier):
    """
    Public route to verify student details via QR code scan.
    Prioritizes Cloudinary URLs over local files.
    """
    try:
        # Clean the input
        identifier = str(student_identifier).strip()
        
        # Search by ID OR by Hash
        student = Student.query.filter(
            (db.cast(Student.id, db.String) == identifier) |
            (db.func.substr(Student.data_hash, 1, 10) == identifier)
        ).first()
        
        if not student:
            return render_template("verify.html", error="Student record not found.", valid=False)
        
        legacy = get_legacy_helpers()
        final_photo_url = None

        photo_url, local_photo_path = legacy.resolve_student_photo_reference(student)
        if photo_url:
            final_photo_url = photo_url
        elif local_photo_path:
            final_photo_url = url_for('static', filename=f"Uploads/{os.path.basename(local_photo_path)}")
        else:
            final_photo_url = url_for('static', filename=os.path.basename(PLACEHOLDER_PATH))

        student_data = {
            "name": student.name,
            "father_name": student.father_name,
            "school_name": student.school_name,
            "photo_url": final_photo_url,
            "class_name": student.class_name,
            "status": "Verified"
        }
        
        return render_template("verify.html", student=student_data, valid=True)
        
    except Exception as e:
        logger.error(f"Database error during verification: {e}")
        return render_template("verify.html", error="System error.", valid=False), 500


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
