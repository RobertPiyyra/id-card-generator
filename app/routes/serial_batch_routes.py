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

from models import db, Template, SerialBatch, SerialCard
from app.decorators import school_admin_required, super_admin_required
from app.services.serial_batch_service import (
    create_batch, get_batch, list_batches, get_batch_cards,
    upload_photos, update_card_details, delete_card,
    _batch_dir, _thumbnail_path
)
from app.utils.helper_utils import get_template_settings, get_card_size
from utils import STATIC_DIR, GENERATED_FOLDER, FONTS_FOLDER

logger = logging.getLogger(__name__)

serial_batch_bp = Blueprint('serial_batch', __name__)


# ================== Helper: Build student-like dict from SerialCard ==================

def _card_to_student_dict(card, template_id):
    """Convert a SerialCard to a student-like dict compatible with render_student_card_side."""
    return {
        'name': card.name or '',
        'father_name': card.father_name or '',
        'class_name': card.class_name or '',
        'dob': card.dob or '',
        'address': card.address or '',
        'phone': card.phone or '',
        'photo_url': card.photo_path or '',  # render_service uses photo_url
        'photo_filename': card.photo_path or '',
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
        created_by=session.get('admin_school') or session.get('student_email', 'unknown')
    )
    return jsonify({
        'success': True,
        'batch_id': batch.id,
        'school_name': batch.school_name,
        'template_id': batch.template_id,
        'prefix': batch.prefix,
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
            'photo_thumbnail': card.photo_thumbnail,
            'has_photo': bool(card.photo_path and os.path.exists(card.photo_path)) if card.photo_path else False,
        })

    return jsonify({
        'batch': {
            'id': batch.id,
            'school_name': batch.school_name,
            'template_id': batch.template_id,
            'prefix': batch.prefix,
            'status': batch.status,
            'created_at': batch.created_at.isoformat() if batch.created_at else None,
        },
        'cards': card_list,
    })


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
        'photo_thumbnail': card.photo_thumbnail,
        'photo_path': card.photo_path,
        'batch_id': card.batch_id,
    })


@serial_batch_bp.route('/<int:batch_id>/cards/<int:card_id>', methods=['POST', 'PUT'])
@school_admin_required
def update_card(batch_id, card_id):
    """Update card details."""
    school_name = None if session.get('admin_role') == 'super_admin' else session.get('admin_school')
    data = request.getJson() or {}

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
        'photo_thumbnail': card.photo_thumbnail,
        'photo_path': card.photo_path,
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

        rendered_img = render_student_card_side(
            template_obj=template,
            student_like=type('StudentLike', (), student_data)(),
            side='front',
            include_photo=True,
            include_qr=True,
            include_barcode=True,
        )

        if rendered_img is None:
            return jsonify({'error': 'Rendering failed'}), 500

        # Convert PIL Image to PDF bytes
        pdf_io = io.BytesIO()
        rendered_img = rendered_img.convert('RGB')
        rendered_img.save(pdf_io, format='PDF', quality=95)
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
        out_doc = fitz.open()

        for card in cards:
            # Generate if not already rendered
            if not card.rendered_path or not os.path.exists(card.rendered_path):
                template = db.session.get(Template, batch.template_id)
                from app.services.render_service import render_student_card_side
                student_data = _card_to_student_dict(card, template.id)
                rendered_img = render_student_card_side(
                    template_obj=template,
                    student_like=type('StudentLike', (), student_data)(),
                    side='front',
                    include_photo=True,
                    include_qr=True,
                    include_barcode=True,
                )
                if rendered_img:
                    rendered_dir = _batch_dir(batch_id, 'rendered')
                    os.makedirs(rendered_dir, exist_ok=True)
                    output_path = os.path.join(rendered_dir, f'card_{card.id}.pdf')
                    rendered_img.convert('RGB').save(output_path, format='PDF')
                    card.rendered_path = output_path
                    card.status = 'rendered'
                    db.session.commit()

            if card.rendered_path and os.path.exists(card.rendered_path):
                card_doc = fitz.open(card.rendered_path)
                out_doc.insert_pdf(card_doc)
                card_doc.close()

        if len(out_doc) == 0:
            return jsonify({'error': 'No cards could be rendered'}), 500

        pdf_bytes = out_doc.tobytes()
        out_doc.close()

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
