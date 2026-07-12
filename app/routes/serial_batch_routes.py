"""
Serial Batch Routes — Photo-first ID card generation for school admins.

Provides HTTP endpoints for:
- Batch creation and management
- Photo upload with auto-serial assignment
- Card detail updates
- Single and batch card generation (PDF)
"""
import io
import os
import logging

from flask import (
    Blueprint, request, jsonify, session, render_template,
    redirect, url_for, flash, send_file, abort
)
from PIL import Image
from sqlalchemy import and_

from models import db, Template, SerialBatch, SerialCard, Student
from app.decorators import school_admin_required, super_admin_required
import uuid
from datetime import datetime
from app.services.serial_batch_service import (
    create_batch, get_batch, list_batches, get_batch_cards,
    upload_photos, update_card_details, delete_card, delete_batch,
    _batch_dir, _thumbnail_path
)


logger = logging.getLogger(__name__)

serial_batch_bp = Blueprint('serial_batch', __name__)


# ================== Helper: Build student-like dict from SerialCard ==================

def to_relative_static(path):
    if not path:
        return ""
    normalized = path.replace("\\", "/")
    idx = normalized.find('static/')
    if idx != -1:
        return normalized[idx:]
    return path


def _find_student_by_serial(template_id, serial_no):
    if not serial_no:
        return None
    try:
        return (
            Student.query
            .filter(Student.template_id == template_id)
            .filter(Student.custom_data["serial_no"].as_string() == str(serial_no))
            .first()
        )
    except Exception:
        students = Student.query.filter_by(template_id=template_id).all()
        for student in students:
            if student.custom_data and student.custom_data.get('serial_no') == serial_no:
                return student
    return None


def _card_to_student_dict(card, template_id):
    """Convert a SerialCard to a student-like dict compatible with render_student_card_side."""
    return {
        'name': card.name or '',
        'father_name': card.father_name or '',
        'class_name': card.class_name or '',
        'dob': card.dob or '',
        'address': card.address or '',
        'phone': card.phone or '',
        'photo_url': to_relative_static(card.photo_path),  # render_service uses photo_url
        'photo_filename': to_relative_static(card.photo_path),
        'template_id': template_id,
        'custom_data': card.custom_data or {},
    }


# ================== List & Create Batches ==================

@serial_batch_bp.route('/')
@school_admin_required
def list_serial_batches():
    """List serial batches. School admin sees own batches, super admin sees all."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    is_super = session.get('admin_role') == 'super_admin'
    school_name = None if is_super else session.get('admin_school')

    pagination = list_batches(school_name=school_name, page=page, per_page=per_page)
    batches = []
    for b in pagination.items:
        batches.append({
            'id': b.id,
            'school_name': b.school_name,
            'template_id': b.template_id,
            'prefix': b.prefix,
            'class_name': b.class_name,
            'status': b.status,
            'card_count': b.cards.count() if hasattr(b.cards, 'count') else len(b.cards),
            'created_at': b.created_at.isoformat() if b.created_at else None,
        })
    return jsonify({'batches': batches, 'total': pagination.total, 'page': page})


@serial_batch_bp.route('/', methods=['POST'])
@school_admin_required
def create_serial_batch():
    """Create a new serial batch. School admin creates for their own school."""
    data = request.get_json() or {}
    template_id = data.get('template_id')
    prefix = (data.get('prefix') or 'SCH-').strip().upper()
    class_name = (data.get('class_name') or '').strip() or None

    if not template_id:
        return jsonify({'success': False, 'error': 'template_id is required'}), 400

    # Verify template belongs to this school
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify({'success': False, 'error': 'Template not found'}), 404

    is_super = session.get('admin_role') == 'super_admin'
    school_name = session.get('admin_school') if not is_super else template.school_name

    if not is_super and template.school_name != school_name:
        return jsonify({'success': False, 'error': 'Access denied to this template'}), 403

    batch = create_batch(
        school_name=school_name,
        template_id=int(template_id),
        prefix=prefix,
        class_name=class_name,
        created_by=session.get('admin_school') or session.get('student_email', 'unknown')
    )
    return jsonify({
        'success': True,
        'batch_id': batch.id,
        'school_name': batch.school_name,
        'template_id': batch.template_id,
        'prefix': batch.prefix,
        'class_name': batch.class_name,
        'status': batch.status,
    })


# ================== View Batch & Cards ==================

@serial_batch_bp.route('/<int:batch_id>')
@school_admin_required
def view_batch(batch_id):
    """Get batch details with all cards (JSON)."""
    school_name = None if session.get('admin_role') == 'super_admin' else session.get('admin_school')
    batch = get_batch(batch_id, school_name=school_name)
    if not batch:
        return jsonify({'error': 'Batch not found'}), 404

    cards_query = get_batch_cards(batch_id)
    cards = cards_query.get('items', cards_query) if isinstance(cards_query, dict) else cards_query
    if hasattr(cards, 'all'):
        cards = cards.all()

    card_list = []
    for card in cards:
        card_list.append({
            'id': card.id,
            'serial_no': card.serial_no,
            'name': card.name,
            'father_name': card.father_name,
            'class_name': card.class_name,
            'dob': card.dob,
            'address': card.address,
            'phone': card.phone,
            'status': card.status,
            'photo_thumbnail': to_relative_static(card.photo_thumbnail),
            'has_photo': bool(card.photo_path and os.path.exists(card.photo_path)) if card.photo_path else False,
        })

    return jsonify({
        'batch': {
            'id': batch.id,
            'school_name': batch.school_name,
            'template_id': batch.template_id,
            'prefix': batch.prefix,
            'class_name': batch.class_name,
            'status': batch.status,
            'created_at': batch.created_at.isoformat() if batch.created_at else None,
        },
        'cards': card_list,
    })



# ================== Delete Batch ==================

@serial_batch_bp.route('/<int:batch_id>', methods=['DELETE'])
@school_admin_required
def delete_batch_route(batch_id):
    """Delete an entire batch and all its files."""
    school_name = None if session.get('admin_role') == 'super_admin' else session.get('admin_school')

    try:
        delete_batch(batch_id, school_name=school_name)
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Delete batch error: {e}")
        return jsonify({'success': False, 'error': 'Delete failed'}), 500


# ================== Photo Upload ==================

@serial_batch_bp.route('/<int:batch_id>/upload', methods=['POST'])
@school_admin_required
def upload_batch_photos(batch_id):
    """Upload photos to a batch. Each photo gets an auto-assigned serial number."""
    school_name = None if session.get('admin_role') == 'super_admin' else session.get('admin_school')

    if 'photos' not in request.files:
        return jsonify({'success': False, 'error': 'No photos provided'}), 400

    files = request.files.getlist('photos')
    if not files:
        return jsonify({'success': False, 'error': 'No photos provided'}), 400

    try:
        cards = upload_photos(batch_id, files, school_name=school_name)
        return jsonify({
            'success': True,
            'uploaded': len(cards),
            'cards': [{'id': c.id, 'serial_no': c.serial_no} for c in cards]
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return jsonify({'success': False, 'error': 'Upload failed'}), 500


# ================== Card CRUD ==================

@serial_batch_bp.route('/<int:batch_id>/cards/<int:card_id>', methods=['GET'])
@school_admin_required
def get_card_detail(batch_id, card_id):
    """Get a single card's details."""
    school_name = None if session.get('admin_role') == 'super_admin' else session.get('admin_school')
    batch = get_batch(batch_id, school_name=school_name)
    if not batch:
        return jsonify({'error': 'Batch not found'}), 404

    card = SerialCard.query.filter_by(id=card_id, batch_id=batch_id).first()
    if not card:
        return jsonify({'error': 'Card not found'}), 404

    return jsonify({
        'id': card.id,
        'serial_no': card.serial_no,
        'name': card.name,
        'father_name': card.father_name,
        'class_name': card.class_name,
        'dob': card.dob,
        'address': card.address,
        'phone': card.phone,
        'custom_data': card.custom_data or {},
        'status': card.status,
        'photo_thumbnail': to_relative_static(card.photo_thumbnail),
        'photo_path': to_relative_static(card.photo_path),
        'batch_id': card.batch_id,
    })


@serial_batch_bp.route('/<int:batch_id>/cards/<int:card_id>', methods=['POST', 'PUT'])
@school_admin_required
def update_card(batch_id, card_id):
    """Update card details."""
    school_name = None if session.get('admin_role') == 'super_admin' else session.get('admin_school')
    data = request.get_json() or {}

    try:
        card = update_card_details(batch_id, card_id, data, school_name=school_name)
        return jsonify({
            'success': True,
            'card': {
                'id': card.id,
                'serial_no': card.serial_no,
                'name': card.name,
                'father_name': card.father_name,
                'class_name': card.class_name,
                'dob': card.dob,
                'address': card.address,
                'phone': card.phone,
                'status': card.status,
            }
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Update error: {e}")
        return jsonify({'success': False, 'error': 'Update failed'}), 500


@serial_batch_bp.route('/<int:batch_id>/cards/<int:card_id>', methods=['DELETE'])
@school_admin_required
def delete_card_route(batch_id, card_id):
    """Delete a card."""
    school_name = None if session.get('admin_role') == 'super_admin' else session.get('admin_school')

    try:
        delete_card(batch_id, card_id, school_name=school_name)
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ================== Serial Lookup (for index page search) ==================

@serial_batch_bp.route('/api/serial_lookup/<serial_no>')
@school_admin_required
def serial_lookup(serial_no):
    """Look up a serial card by number for the current school admin."""
    school_name = session.get('admin_school')
    if not school_name and session.get('admin_role') != 'super_admin':
        return jsonify({'error': 'Not authorized'}), 403

    query = SerialCard.query.join(SerialBatch).filter(
        SerialCard.serial_no == serial_no
    )
    if school_name:
        query = query.filter(SerialBatch.school_name == school_name)

    card = query.first()
    if not card:
        return jsonify({'error': 'Serial number not found'}), 404

    return jsonify({
        'id': card.id,
        'serial_no': card.serial_no,
        'name': card.name,
        'father_name': card.father_name,
        'class_name': card.class_name,
        'dob': card.dob,
        'address': card.address,
        'phone': card.phone,
        'photo_thumbnail': to_relative_static(card.photo_thumbnail),
        'photo_path': to_relative_static(card.photo_path),
        'status': card.status,
        'batch_id': card.batch_id,
    })


# ================== Card Generation ==================

@serial_batch_bp.route('/<int:batch_id>/generate/<int:card_id>', methods=['POST'])
@school_admin_required
def generate_card(batch_id, card_id):
    """Generate ID card PDF for a single SerialCard."""
    school_name = None if session.get('admin_role') == 'super_admin' else session.get('admin_school')
    batch = get_batch(batch_id, school_name=school_name)
    if not batch:
        return jsonify({'error': 'Batch not found'}), 404

    card = SerialCard.query.filter_by(id=card_id, batch_id=batch_id).first()
    if not card:
        return jsonify({'error': 'Card not found'}), 404

    if not card.name:
        return jsonify({'error': 'Card details incomplete — name is required'}), 400

    template = db.session.get(Template, batch.template_id)
    if not template:
        return jsonify({'error': 'Template not found'}), 404

    try:
        from app.services.render_service import render_student_card_side
        student_data = _card_to_student_dict(card, template.id)
        student_like = type('StudentLike', (), student_data)()

        rendered_img = render_student_card_side(
            template_obj=template,
            student_like=student_like,
            side='front',
            include_photo=True,
            include_qr=True,
            include_barcode=True,
        )

        if rendered_img is None:
            return jsonify({'error': 'Rendering failed'}), 500

        # Support double-sided rendering
        back_img = None
        if getattr(template, "is_double_sided", False):
            try:
                back_img = render_student_card_side(
                    template_obj=template,
                    student_like=student_like,
                    side='back',
                    include_photo=True,
                    include_qr=True,
                    include_barcode=True,
                )
                if back_img:
                    back_img = back_img.convert('RGB')
            except Exception as be:
                logger.warning(f"Failed to render back card image: {be}")
                back_img = None

        # Convert PIL Image to PDF bytes
        pdf_io = io.BytesIO()
        rendered_img = rendered_img.convert('RGB')
        if back_img:
            rendered_img.save(pdf_io, format='PDF', save_all=True, append_images=[back_img], quality=90)
        else:
            rendered_img.save(pdf_io, format='PDF', quality=90)
        pdf_io.seek(0)

        # Optionally save to disk
        output_dir = _batch_dir(batch_id)
        rendered_dir = os.path.join(output_dir, 'rendered')
        os.makedirs(rendered_dir, exist_ok=True)
        output_path = os.path.join(rendered_dir, f'card_{card_id}.pdf')
        with open(output_path, 'wb') as f:
            f.write(pdf_io.getvalue())
        card.rendered_path = output_path
        card.status = 'rendered'

        # Create or Update corresponding Student record
        student = _find_student_by_serial(template.id, card.serial_no)
        if not student:
            student = Student()
            db.session.add(student)

        student.name = card.name
        student.father_name = card.father_name
        student.class_name = card.class_name
        student.dob = card.dob
        student.address = card.address
        student.phone = card.phone
        student.template_id = template.id
        student.school_name = template.school_name
        student.custom_data = dict(card.custom_data or {})
        student.custom_data['serial_no'] = card.serial_no
        student.photo_filename = to_relative_static(card.photo_path)
        student.photo_url = to_relative_static(card.photo_path)

        # Save standard preview JPG/PDF files to standard generated folder
        from utils import GENERATED_FOLDER, get_storage_backend
        os.makedirs(GENERATED_FOLDER, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
        base = f"card_{template.id}_{ts}_{uuid.uuid4().hex}"
        jpg_name = f"{base}.webp"
        pdf_name = f"{base}.pdf"

        rendered_img.save(os.path.join(GENERATED_FOLDER, jpg_name), 'WEBP', quality=90)
        if back_img:
            back_jpg_name = f"{base}_back.webp"
            back_img.save(os.path.join(GENERATED_FOLDER, back_jpg_name), 'WEBP', quality=90)
            student.back_generated_filename = back_jpg_name

        with open(os.path.join(GENERATED_FOLDER, pdf_name), 'wb') as f:
            f.write(pdf_io.getvalue())

        storage_backend = get_storage_backend()
        if storage_backend == "local":
            student.generated_filename = pdf_name
            student.image_url = None
            student.pdf_url = None
            if back_img:
                student.back_image_url = None
        else:
            # Upload to Cloudinary if setup
            try:
                from app.services.photo_service import upload_image
                jpg_bytes = open(os.path.join(GENERATED_FOLDER, jpg_name), "rb").read()
                jpg_result = upload_image(jpg_bytes, folder='generated')
                student.image_url = jpg_result if isinstance(jpg_result, str) else jpg_result.get('url')

                if back_img:
                    back_bytes = open(os.path.join(GENERATED_FOLDER, back_jpg_name), "rb").read()
                    back_result = upload_image(back_bytes, folder='generated')
                    student.back_image_url = back_result if isinstance(back_result, str) else back_result.get('url')

                pdf_bytes_content = pdf_io.getvalue()
                pdf_result = upload_image(pdf_bytes_content, folder='generated', resource_type='raw')
                student.pdf_url = pdf_result if isinstance(pdf_result, str) else pdf_result.get('url')
                student.photo_url = upload_image(open(card.photo_path, "rb").read(), folder='photos')
            except Exception as cl_err:
                logger.error(f"Failed to upload serial generated card to Cloudinary: {cl_err}")
                student.generated_filename = pdf_name

        db.session.commit()

        pdf_io.seek(0)
        return send_file(
            pdf_io,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'IDCard_{card.serial_no}.pdf'
        )
    except Exception as e:
        logger.error(f"Card generation error: {e}", exc_info=True)
        return jsonify({'error': f'Generation failed: {str(e)}'}), 500


@serial_batch_bp.route('/<int:batch_id>/download_all', methods=['GET'])
@school_admin_required
def download_all_cards(batch_id):
    """Generate a combined PDF of all completed cards in the batch."""
    school_name = None if session.get('admin_role') == 'super_admin' else session.get('admin_school')
    batch = get_batch(batch_id, school_name=school_name)
    if not batch:
        return jsonify({'error': 'Batch not found'}), 404

    cards = SerialCard.query.filter(
        SerialCard.batch_id == batch_id,
        SerialCard.status.in_(['details_filled', 'rendered'])
    ).order_by(SerialCard.serial_no).all()

    if not cards:
        return jsonify({'error': 'No completed cards to download'}), 400

    try:
        import fitz  # PyMuPDF
        from models import TemplateField
        template = db.session.get(Template, batch.template_id)
        if not template:
            return jsonify({'error': 'Template not found'}), 404

        # 1. Pre-load all template fields once to avoid database N+1 queries
        template_fields = TemplateField.query.filter_by(template_id=template.id).order_by(TemplateField.display_order.asc()).all()

        # 2. Build map of serial_no -> Student in O(N) to avoid O(N^2) lookups
        all_students = Student.query.filter_by(template_id=template.id).all()
        student_map = {}
        for s in all_students:
            if s.custom_data and s.custom_data.get('serial_no'):
                student_map[s.custom_data['serial_no']] = s

        # 3. Identify cards that need rendering
        cards_to_render = []
        for card in cards:
            if not card.rendered_path or not os.path.exists(card.rendered_path):
                cards_to_render.append(card)

        # 4. Render cards in parallel if any need rendering
        rendered_results = {}
        if cards_to_render:
            from flask import current_app
            app = current_app._get_current_object()
            from app.services.render_service import render_student_card_side

            def render_card_task(card_item):
                student_data = _card_to_student_dict(card_item, template.id)
                student_like = type('StudentLike', (), student_data)()
                student_like._template_fields = template_fields  # inject pre-loaded fields cache

                with app.app_context():
                    try:
                        front_img = render_student_card_side(
                            template_obj=template,
                            student_like=student_like,
                            side='front',
                            include_photo=True,
                            include_qr=True,
                            include_barcode=True,
                        )
                        if front_img:
                            front_img = front_img.convert('RGB')

                        back_img = None
                        if getattr(template, "is_double_sided", False):
                            back_img = render_student_card_side(
                                template_obj=template,
                                student_like=student_like,
                                side='back',
                                include_photo=True,
                                include_qr=True,
                                include_barcode=True,
                            )
                            if back_img:
                                back_img = back_img.convert('RGB')
                        return card_item.id, front_img, back_img, None
                    except Exception as exc:
                        return card_item.id, None, None, str(exc)

            from app.services.parallel_render import get_optimal_workers
            workers = get_optimal_workers(len(cards_to_render))

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for card_id, front_img, back_img, err in executor.map(render_card_task, cards_to_render):
                    if err:
                        logger.error(f"Parallel card rendering error for card {card_id}: {err}")
                    rendered_results[card_id] = (front_img, back_img)

        # 5. Process each card (saving, PDF creation, DB updates) in the main thread
        out_doc = fitz.open()
        rendered_pdfs = {}  # card_id -> pdf_bytes

        from utils import GENERATED_FOLDER, get_storage_backend
        storage_backend = get_storage_backend()
        os.makedirs(GENERATED_FOLDER, exist_ok=True)

        for card in cards:
            if not card.rendered_path or not os.path.exists(card.rendered_path):
                front_img, back_img = rendered_results.get(card.id, (None, None))
                if not front_img:
                    logger.error(f"Card {card.id} rendering was unsuccessful.")
                    continue

                pdf_io = io.BytesIO()
                if back_img:
                    front_img.save(pdf_io, format='PDF', save_all=True, append_images=[back_img], quality=90)
                else:
                    front_img.save(pdf_io, format='PDF', quality=90)
                pdf_bytes_data = pdf_io.getvalue()
                rendered_pdfs[card.id] = pdf_bytes_data

                # Save to batch rendered dir
                rendered_dir = os.path.join(_batch_dir(batch_id), 'rendered')
                os.makedirs(rendered_dir, exist_ok=True)
                output_path = os.path.join(rendered_dir, f'card_{card.id}.pdf')
                with open(output_path, 'wb') as f:
                    f.write(pdf_bytes_data)
                card.rendered_path = output_path
                card.status = 'rendered'

                # Create or Update corresponding Student record
                student = student_map.get(card.serial_no)
                if not student:
                    student = Student()
                    db.session.add(student)
                    student_map[card.serial_no] = student

                student.name = card.name
                student.father_name = card.father_name
                student.class_name = card.class_name
                student.dob = card.dob
                student.address = card.address
                student.phone = card.phone
                student.template_id = template.id
                student.school_name = template.school_name
                student.custom_data = dict(card.custom_data or {})
                student.custom_data['serial_no'] = card.serial_no
                student.photo_filename = to_relative_static(card.photo_path)
                student.photo_url = to_relative_static(card.photo_path)

                # Save in standard generated folder for previews
                ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
                base = f"card_{template.id}_{ts}_{uuid.uuid4().hex}"
                jpg_name = f"{base}.webp"
                pdf_name = f"{base}.pdf"

                front_img.save(os.path.join(GENERATED_FOLDER, jpg_name), 'WEBP', quality=90)
                if back_img:
                    back_jpg_name = f"{base}_back.webp"
                    back_img.save(os.path.join(GENERATED_FOLDER, back_jpg_name), 'WEBP', quality=90)
                    student.back_generated_filename = back_jpg_name

                with open(os.path.join(GENERATED_FOLDER, pdf_name), 'wb') as f:
                    f.write(pdf_bytes_data)

                if storage_backend == "local":
                    student.generated_filename = pdf_name
                    student.image_url = None
                    student.pdf_url = None
                    if back_img:
                        student.back_image_url = None
                else:
                    # Upload to Cloudinary if setup
                    try:
                        from app.services.photo_service import upload_image
                        jpg_bytes = open(os.path.join(GENERATED_FOLDER, jpg_name), "rb").read()
                        jpg_result = upload_image(jpg_bytes, folder='generated')
                        student.image_url = jpg_result if isinstance(jpg_result, str) else jpg_result.get('url')

                        if back_img:
                            back_bytes = open(os.path.join(GENERATED_FOLDER, back_jpg_name), "rb").read()
                            back_result = upload_image(back_bytes, folder='generated')
                            student.back_image_url = back_result if isinstance(back_result, str) else back_result.get('url')

                        pdf_result = upload_image(pdf_bytes_data, folder='generated', resource_type='raw')
                        student.pdf_url = pdf_result if isinstance(pdf_result, str) else pdf_result.get('url')
                        student.photo_url = upload_image(open(card.photo_path, "rb").read(), folder='photos')
                    except Exception as cl_err:
                        logger.error(f"Failed to upload serial generated card to Cloudinary: {cl_err}")
                        student.generated_filename = pdf_name

            # Merge PDF in-memory (bypassing disk reading if we already generated it, or loading from disk if already cached)
            if card.rendered_path:
                if card.id in rendered_pdfs:
                    card_doc = fitz.open(stream=rendered_pdfs[card.id], filetype="pdf")
                else:
                    card_doc = fitz.open(card.rendered_path)
                out_doc.insert_pdf(card_doc)
                card_doc.close()

        if len(out_doc) == 0:
            return jsonify({'error': 'No cards could be rendered'}), 500

        pdf_bytes = out_doc.tobytes()
        out_doc.close()

        # Batch database commit (performed once at the very end)
        db.session.commit()

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'IDCards_{batch.school_name}_batch{batch.id}.pdf'
        )
    except Exception as e:
        logger.error(f"Batch download error: {e}", exc_info=True)
        return jsonify({'error': f'Download failed: {str(e)}'}), 500
