"""
NFC/Chip Card Encoding Service.

Handles NFC tag reading/writing, chip encoding workflows, and encoding verification.
Supports MIFARE Classic, MIFARE DESFire, and NTAG chip types.
"""
import logging
import json
import hashlib
from datetime import datetime, timezone

from models import db, NfcEncoding, Student, Template

logger = logging.getLogger(__name__)

# Chip type configurations
CHIP_CONFIGS = {
    'MIFARE_1K': {
        'capacity_bytes': 1024,
        'sectors': 16,
        'block_size': 16,
        'readable': True,
        'writable': True,
    },
    'MIFARE_4K': {
        'capacity_bytes': 4096,
        'sectors': 40,
        'block_size': 16,
        'readable': True,
        'writable': True,
    },
    'DESFIRE_EV1': {
        'capacity_bytes': 8192,
        'readable': True,
        'writable': True,
    },
    'DESFIRE_EV2': {
        'capacity_bytes': 8192,
        'readable': True,
        'writable': True,
    },
    'NTAG213': {
        'capacity_bytes': 144,
        'readable': True,
        'writable': True,
    },
    'NTAG215': {
        'capacity_bytes': 504,
        'readable': True,
        'writable': True,
    },
    'NTAG216': {
        'capacity_bytes': 888,
        'readable': True,
        'writable': True,
    },
}


def prepare_encoding_data(student, template, extra_fields=None):
    """Prepare the data payload to encode on an NFC chip."""
    data = {
        'student_id': student.id,
        'name': student.name or '',
        'template_id': template.id,
    }
    if student.father_name:
        data['father_name'] = student.father_name
    if student.class_name:
        data['class_name'] = student.class_name
    if student.dob:
        data['dob'] = student.dob
    if student.phone:
        data['phone'] = student.phone
    if student.school_name:
        data['school'] = student.school_name
    if student.email:
        data['email'] = student.email
    if extra_fields:
        data.update(extra_fields)

    return data


def validate_encoding_data(data, chip_type='MIFARE_1K'):
    """Validate that the encoding data fits within the chip capacity."""
    config = CHIP_CONFIGS.get(chip_type)
    if not config:
        return False, f'Unknown chip type: {chip_type}'

    payload_size = len(json.dumps(data).encode('utf-8'))
    if payload_size > config['capacity_bytes']:
        return False, f'Data size ({payload_size}B) exceeds chip capacity ({config["capacity_bytes"]}B)'

    return True, 'OK'


def create_encoding_job(student_id, template_id, chip_type='MIFARE_1K', encoding_data=None, extra_fields=None):
    """Create an NFC encoding job."""
    student = db.session.get(Student, student_id)
    if not student:
        return None, 'Student not found'

    template = db.session.get(Template, template_id)
    if not template:
        return None, 'Template not found'

    if encoding_data is None:
        encoding_data = prepare_encoding_data(student, template, extra_fields)

    valid, message = validate_encoding_data(encoding_data, chip_type)
    if not valid:
        return None, message

    encoding = NfcEncoding(
        student_id=student_id,
        template_id=template_id,
        chip_type=chip_type,
        encoding_data=encoding_data,
        status='pending',
    )
    db.session.add(encoding)
    db.session.commit()

    logger.info(f"NFC encoding job {encoding.id} created for student {student_id}")
    return encoding.id, 'OK'


def batch_create_encoding_jobs(template_id, student_ids=None, chip_type='MIFARE_1K', extra_fields=None):
    """Create NFC encoding jobs for multiple students."""
    template = db.session.get(Template, template_id)
    if not template:
        return [], 'Template not found'

    if student_ids is None:
        # Get all students for this template
        students = Student.query.filter_by(template_id=template_id).all()
        student_ids = [s.id for s in students]

    job_ids = []
    errors = []
    for student_id in student_ids:
        job_id, message = create_encoding_job(student_id, template_id, chip_type, extra_fields=extra_fields)
        if job_id:
            job_ids.append(job_id)
        else:
            errors.append(f'Student {student_id}: {message}')

    return job_ids, errors


def encode_chip(encoding_id, uid=None):
    """
    Execute NFC chip encoding.

    In production, this would interface with actual NFC hardware via USB/serial.
    For now, it simulates the encoding and records the result.
    """
    encoding = db.session.get(NfcEncoding, encoding_id)
    if not encoding:
        return False, 'Encoding job not found'

    if encoding.status not in ('pending', 'failed'):
        return False, f'Cannot encode: status is {encoding.status}'

    try:
        # In production, this would:
        # 1. Detect NFC reader/writer hardware
        # 2. Wait for chip to be presented
        # 3. Authenticate with the chip
        # 4. Write data blocks
        # 5. Verify written data

        encoding.status = 'encoded'
        encoding.encoded_at = datetime.now(timezone.utc)
        if uid:
            encoding.uid = uid
        db.session.commit()

        logger.info(f"NFC encoding {encoding_id} completed")
        return True, 'OK'
    except Exception as e:
        encoding.status = 'failed'
        encoding.error_message = str(e)
        db.session.commit()
        logger.error(f"NFC encoding {encoding_id} failed: {e}")
        return False, str(e)


def verify_encoding(encoding_id):
    """Verify an NFC encoding by reading back the chip data."""
    encoding = db.session.get(NfcEncoding, encoding_id)
    if not encoding:
        return False, 'Encoding job not found'

    if encoding.status != 'encoded':
        return False, f'Cannot verify: status is {encoding.status}'

    try:
        # In production, this would:
        # 1. Read the chip data
        # 2. Compare with expected encoding_data
        # 3. Report any mismatches

        encoding.status = 'verified'
        encoding.verified_at = datetime.now(timezone.utc)
        db.session.commit()

        logger.info(f"NFC encoding {encoding_id} verified")
        return True, 'OK'
    except Exception as e:
        encoding.status = 'failed'
        encoding.error_message = str(e)
        db.session.commit()
        logger.error(f"NFC verification {encoding_id} failed: {e}")
        return False, str(e)


def get_encoding_status(encoding_id):
    """Get the status of an encoding job."""
    encoding = db.session.get(NfcEncoding, encoding_id)
    if not encoding:
        return None

    return {
        'id': encoding.id,
        'student_id': encoding.student_id,
        'template_id': encoding.template_id,
        'chip_type': encoding.chip_type,
        'status': encoding.status,
        'uid': encoding.uid,
        'encoded_at': encoding.encoded_at.isoformat() if encoding.encoded_at else None,
        'verified_at': encoding.verified_at.isoformat() if encoding.verified_at else None,
        'error_message': encoding.error_message,
        'created_at': encoding.created_at.isoformat() if encoding.created_at else None,
    }


def list_encodings(student_id=None, template_id=None, status=None, page=1, per_page=50):
    """List NFC encoding jobs with filtering."""
    query = NfcEncoding.query
    if student_id:
        query = query.filter_by(student_id=student_id)
    if template_id:
        query = query.filter_by(template_id=template_id)
    if status:
        query = query.filter_by(status=status)
    query = query.order_by(NfcEncoding.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return pagination.items, pagination.total


def get_encoding_stats():
    """Get encoding statistics."""
    total = NfcEncoding.query.count()
    pending = NfcEncoding.query.filter_by(status='pending').count()
    encoded = NfcEncoding.query.filter_by(status='encoded').count()
    verified = NfcEncoding.query.filter_by(status='verified').count()
    failed = NfcEncoding.query.filter_by(status='failed').count()

    # Chip type breakdown
    chip_types = {}
    for chip_type in CHIP_CONFIGS:
        count = NfcEncoding.query.filter_by(chip_type=chip_type).count()
        if count > 0:
            chip_types[chip_type] = count

    return {
        'total': total,
        'pending': pending,
        'encoded': encoded,
        'verified': verified,
        'failed': failed,
        'chip_types': chip_types,
    }
