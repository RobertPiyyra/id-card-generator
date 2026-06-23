"""
Serial Batch Service — Photo-first workflow for school admins.

Handles:
- Batch creation and lifecycle
- Photo upload, storage, and thumbnail generation
- Photo-on-template composite generation
- Card detail updates
- CSV import
- Render orchestration
"""
import os
import io
import csv
import json
import uuid
import logging
from datetime import datetime, timezone
from threading import Thread

from PIL import Image, ImageDraw
from flask import current_app
from sqlalchemy import and_

from models import db, SerialBatch, SerialCard, Template, Student
from utils import UPLOAD_FOLDER, STATIC_DIR
from app.utils.helper_utils import get_template_settings
from app.utils.layout_utils import get_card_size
from app.utils.image_utils import get_default_photo_config
from app.services.render_service import render_student_card_side

logger = logging.getLogger(__name__)

# Storage for serial batch photos
SERIAL_BATCH_DIR = os.path.join(STATIC_DIR, 'serial_batches')
os.makedirs(SERIAL_BATCH_DIR, exist_ok=True)

THUMBNAIL_WIDTH = 150
THUMBNAIL_HEIGHT = 190


def _batch_dir(batch_id):
    return os.path.join(SERIAL_BATCH_DIR, str(batch_id))


def _photo_path(batch_id, filename):
    return os.path.join(_batch_dir(batch_id), filename)


def _thumbnail_path(batch_id, card_id):
    return os.path.join(_batch_dir(batch_id), 'thumbs', f'card_{card_id}.jpg')


def _ensure_dirs(batch_id):
    d = _batch_dir(batch_id)
    os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(d, 'thumbs'), exist_ok=True)
    os.makedirs(os.path.join(d, 'rendered'), exist_ok=True)


def _get_next_serial(batch):
    """Get the next serial number for a batch."""
    existing = db.session.query(SerialCard.serial_no).filter_by(batch_id=batch.id).all()
    existing_numbers = set()
    for (serial_no,) in existing:
        if serial_no and serial_no.startswith(batch.prefix):
            try:
                num = int(serial_no[len(batch.prefix):])
                existing_numbers.add(num)
            except ValueError:
                pass
    next_num = 1
    while next_num in existing_numbers:
        next_num += 1
    return f"{batch.prefix}{next_num:03d}"


def create_batch(school_name, template_id, prefix='SCH-', created_by=None):
    """Create a new SerialBatch."""
    batch = SerialBatch(
        school_name=school_name,
        template_id=template_id,
        prefix=prefix,
        status='uploading',
        created_by=created_by,
    )
    db.session.add(batch)
    db.session.commit()
    _ensure_dirs(batch.id)
    logger.info(f"Created SerialBatch {batch.id} for school={school_name}, template={template_id}")
    return batch


def get_batch(batch_id, school_name=None):
    """Get a batch by ID, optionally filtering by school_name for RBAC."""
    query = SerialBatch.query.filter_by(id=batch_id)
    if school_name:
        query = query.filter_by(school_name=school_name)
    return query.first()


def list_batches(school_name=None, page=1, per_page=20):
    """List batches, optionally filtered by school."""
    query = SerialBatch.query.order_by(SerialBatch.created_at.desc())
    if school_name:
        query = query.filter_by(school_name=school_name)
    return query.paginate(page=page, per_page=per_page, error_out=False)


def get_batch_cards(batch_id, page=None, per_page=None):
    """Get all cards for a batch, ordered by serial number."""
    query = SerialCard.query.filter_by(batch_id=batch_id).order_by(SerialCard.serial_no)
    if page and per_page:
        return query.paginate(page=page, per_page=per_page, error_out=False)
    return {'items': query.all(), 'total': query.count()}


def upload_photos(batch_id, files, school_name=None):
    """
    Upload photos for a batch. Each file gets a new serial number.
    
    Returns list of created SerialCard objects.
    """
    batch = get_batch(batch_id, school_name=school_name)
    if not batch:
        raise ValueError("Batch not found")
    if batch.status not in ('uploading', 'ready'):
        raise ValueError(f"Cannot upload photos when batch status is '{batch.status}'")

    _ensure_dirs(batch.id)
    created_cards = []

    for file in files:
        if not file or not file.filename:
            continue

        # Validate file type
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.bmp'):
            continue

        # Generate unique filename
        unique_name = f"{uuid.uuid4().hex}{ext}"
        filepath = _photo_path(batch_id, unique_name)

        # Save file
        file.save(filepath)

        # Assign serial number
        serial_no = _get_next_serial(batch)

        # Create card record
        card = SerialCard(
            batch_id=batch.id,
            serial_no=serial_no,
            photo_path=filepath,
            status='photo_only',
        )
        db.session.add(card)
        created_cards.append(card)

    if created_cards:
        batch.status = 'ready'
        db.session.commit()

        # Generate thumbnails in background
        for card in created_cards:
            try:
                _generate_thumbnail(batch, card)
            except Exception as e:
                logger.warning(f"Thumbnail generation failed for card {card.id}: {e}")

    return created_cards


def _generate_thumbnail(batch, card):
    """Generate a photo-on-template thumbnail for a card."""
    if not card.photo_path or not os.path.exists(card.photo_path):
        return

    template = db.session.get(Template, batch.template_id)
    if not template:
        return

    try:
        font_settings, photo_settings, qr_settings, orientation = get_template_settings(
            template.id, side='front'
        )
    except Exception:
        photo_settings = get_default_photo_config()

    # Load template image
    from app.legacy_app import get_template_path, _load_template_image_for_render
    template_path = get_template_path(template.id, side='front')
    if not template_path or not os.path.exists(template_path):
        # Fallback: just resize the photo
        thumb = _resize_photo_thumbnail(card.photo_path, template, photo_settings)
    else:
        card_width, card_height = get_card_size(template.id)
        template_img = _load_template_image_for_render(template_path, card_width, card_height, render_scale=1.0)

        # Load and place photo
        photo_img = Image.open(card.photo_path).convert('RGBA')
        photo_w, photo_h = photo_settings.get('photo_width', 200), photo_settings.get('photo_height', 250)
        photo_x = photo_settings.get('photo_x', 50)
        photo_y = photo_settings.get('photo_y', 50)

        # Resize photo to fit
        photo_img.thumbnail((photo_w, photo_h), Image.LANCZOS)

        # Create circular mask if needed
        shape = photo_settings.get('photo_shape', 'circle')
        if shape == 'circle':
            mask = Image.new('L', photo_img.size, 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, photo_img.width, photo_img.height), fill=255)
            template_img.paste(photo_img, (photo_x, photo_y), mask)
        else:
            template_img.paste(photo_img, (photo_x, photo_y), photo_img if photo_img.mode == 'RGBA' else None)

        # Scale to thumbnail
        thumb = template_img.copy()
        thumb.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)

    # Save thumbnail
    thumb_path = _thumbnail_path(batch.id, card.id)
    thumb = thumb.convert('RGB')
    thumb.save(thumb_path, 'JPEG', quality=85)
    card.photo_thumbnail = thumb_path
    db.session.commit()


def _resize_photo_thumbnail(photo_path, template, photo_settings):
    """Fallback: just resize the photo when template is unavailable."""
    img = Image.open(photo_path).convert('RGBA')
    photo_w = photo_settings.get('photo_width', 200)
    photo_h = photo_settings.get('photo_height', 250)
    img.thumbnail((photo_w, photo_h), Image.LANCZOS)

    # Create white background
    bg = Image.new('RGB', (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), (255, 255, 255))
    # Center the photo
    x = (THUMBNAIL_WIDTH - img.width) // 2
    y = (THUMBNAIL_HEIGHT - img.height) // 2
    bg.paste(img, (x, y), img if img.mode == 'RGBA' else None)
    return bg


def update_card_details(batch_id, card_id, details, school_name=None):
    """Update card details."""
    query = SerialCard.query.filter_by(id=card_id, batch_id=batch_id)
    card = query.first()
    if not card:
        raise ValueError("Card not found")

    # Update fields
    field_names = ['name', 'father_name', 'class_name', 'dob', 'address', 'phone']
    for field in field_names:
        if field in details:
            setattr(card, field, details[field])

    # Custom data
    if 'custom_data' in details:
        card.custom_data = details['custom_data']

    # Update status
    if card.status == 'photo_only' and any(details.get(f) for f in field_names):
        card.status = 'details_filled'

    card.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return card


def delete_card(batch_id, card_id, school_name=None):
    """Delete a card and its files."""
    query = SerialCard.query.filter_by(id=card_id, batch_id=batch_id)
    if school_name:
        batch = get_batch(batch_id, school_name)
        if not batch:
            raise ValueError("Batch not found")
    card = query.first()
    if not card:
        raise ValueError("Card not found")

    # Delete files
    for path in [card.photo_path, card.photo_thumbnail, card.rendered_path]:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    db.session.delete(card)
    db.session.commit()


def import_details_from_csv(batch_id, csv_path, mapping, school_name=None):
    """
    Import card details from a CSV file.
    
    mapping: dict mapping CSV column names to card field names.
             Must include 'serial_no' mapping.
    """
    batch = get_batch(batch_id, school_name=school_name)
    if not batch:
        raise ValueError("Batch not found")

    updated = 0
    skipped = 0
    errors = []

    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, start=2):
            # Map CSV columns to fields
            mapped = {}
            for csv_col, field_name in mapping.items():
                if csv_col in row:
                    mapped[field_name] = row[csv_col].strip() if row[csv_col] else ''

            serial_no = mapped.pop('serial_no', None)
            if not serial_no:
                skipped += 1
                continue

            # Find matching card
            card = SerialCard.query.filter_by(
                batch_id=batch.id, serial_no=serial_no
            ).first()

            if not card:
                skipped += 1
                continue

            # Update fields
            field_names = ['name', 'father_name', 'class_name', 'dob', 'address', 'phone']
            for field in field_names:
                if field in mapped and mapped[field]:
                    setattr(card, field, mapped[field])

            if card.status == 'photo_only':
                card.status = 'details_filled'
            card.updated_at = datetime.now(timezone.utc)
            updated += 1

    db.session.commit()
    return {'updated': updated, 'skipped': skipped, 'errors': errors}


def delete_batch(batch_id, school_name=None):
    """Delete a batch and all its files."""
    batch = get_batch(batch_id, school_name=school_name)
    if not batch:
        raise ValueError("Batch not found")

    # Delete all files
    batch_dir = _batch_dir(batch.id)
    if os.path.exists(batch_dir):
        import shutil
        try:
            shutil.rmtree(batch_dir)
        except OSError as e:
            logger.warning(f"Failed to delete batch directory: {e}")

    # Delete cards (cascade) and batch
    SerialCard.query.filter_by(batch_id=batch.id).delete()
    db.session.delete(batch)
    db.session.commit()


def render_batch(batch_id, school_name=None):
    """
    Render all cards in a batch that have details filled.
    Uses the existing render pipeline.
    """
    batch = get_batch(batch_id, school_name=school_name)
    if not batch:
        raise ValueError("Batch not found")

    batch.status = 'rendering'
    db.session.commit()

    # Run render in background thread
    app = current_app._get_current_object()
    thread = Thread(target=_render_batch_worker, args=(app, batch.id))
    thread.daemon = True
    thread.start()
    return True


def _render_batch_worker(app, batch_id):
    """Background worker for batch rendering."""
    with app.app_context():
        batch = db.session.get(SerialBatch, batch_id)
        if not batch:
            return

        cards = SerialCard.query.filter(
            SerialCard.batch_id == batch_id,
            SerialCard.status == 'details_filled'
        ).order_by(SerialCard.serial_no).all()

        success_count = 0
        error_count = 0
        total = len(cards)

        for i, card in enumerate(cards):
            try:
                _render_single_card(app, batch, card)
                success_count += 1
            except Exception as e:
                logger.error(f"Render failed for card {card.id} ({card.serial_no}): {e}")
                card.status = 'error'
                card.error_message = str(e)
                error_count += 1

            # Update progress every 5 cards
            if (i + 1) % 5 == 0 or (i + 1) == total:
                logger.info(f"Batch {batch_id} render progress: {i+1}/{total}")

        # Mark remaining as rendered
        for card in cards:
            if card.status == 'details_filled':
                card.status = 'rendered'

        batch.status = 'done'
        db.session.commit()
        logger.info(f"Batch {batch_id} render complete: {success_count} success, {error_count} errors")


def _render_single_card(app, batch, card):
    """Render a single card using the existing render pipeline."""
    template = db.session.get(Template, batch.template_id)
    if not card.photo_path or not os.path.exists(card.photo_path):
        raise FileNotFoundError(f"Photo not found for card {card.serial_no}")

    # Build a student-like object for the render function
    student_like = {
        'name': card.name or '',
        'father_name': card.father_name or '',
        'class_name': card.class_name or '',
        'dob': card.dob or '',
        'address': card.address or '',
        'phone': card.phone or '',
        'photo_path': card.photo_path,
        'photo_filename': os.path.basename(card.photo_path),
        'photo_url': None,
        'image_url': None,
        'custom_data': card.custom_data or {},
        'template_id': template.id,
        'school_name': batch.school_name,
        'id': card.id,
    }

    # Render front
    result = render_student_card_side(
        template_obj=template,
        student_like=student_like,
        side='front',
        student_id=card.id,
        school_name=batch.school_name,
        render_scale=1.0,
        include_photo=True,
        include_qr=True,
        include_barcode=True,
        include_text=True,
    )

    if result:
        # Save rendered image
        rendered_dir = os.path.join(_batch_dir(batch.id), 'rendered')
        rendered_path = os.path.join(rendered_dir, f'{card.serial_no}.png')
        result.save(rendered_path, 'PNG')
        card.rendered_path = rendered_path
        card.status = 'rendered'
        db.session.commit()
    else:
        raise RuntimeError("Render returned None")


def get_render_progress(batch_id, school_name=None):
    """Get render progress for a batch."""
    batch = get_batch(batch_id, school_name=school_name)
    if not batch:
        return None

    total = SerialCard.query.filter_by(batch_id=batch.id).filter(
        SerialCard.status.in_(['details_filled', 'rendered'])
    ).count()
    rendered = SerialCard.query.filter_by(batch_id=batch.id, status='rendered').count()
    errors = SerialCard.query.filter_by(batch_id=batch.id, status='error').count()

    return {
        'status': batch.status,
        'total': total,
        'rendered': rendered,
        'errors': errors,
        'progress': round(rendered / total * 100, 1) if total > 0 else 0,
    }
