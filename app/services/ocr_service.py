"""
OCR Service for ID Document Analysis
Extracts text from uploaded ID cards and documents.
Isolated module - uses OcrResult model, stores results only.
"""
import io
import logging
import time as _time
from datetime import datetime, timezone
from typing import Optional

from models import db, OcrResult, Student

logger = logging.getLogger(__name__)


def extract_text_from_image(image_bytes: bytes, student_id: int = None,
                              source_url: str = None) -> dict:
    """
    Extract text from an image using available OCR engine.
    Tries pytesseract first, then falls back to basic analysis.
    Returns extracted text and structured fields.
    """
    start = _time.time()
    result = {
        'text': '',
        'fields': {},
        'confidence': 0.0,
        'model': 'none',
        'processing_time_ms': 0,
    }

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))

        # Try pytesseract
        try:
            import pytesseract
            text = pytesseract.image_to_string(img)
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            confidences = [int(c) for c in data.get('conf', []) if str(c).isdigit() and int(c) > 0]
            avg_conf = sum(confidences) / len(confidences) if confidences else 0

            result['text'] = text.strip()
            result['confidence'] = round(avg_conf, 1)
            result['model'] = 'tesseract'
        except (ImportError, Exception):
            # Fallback: try easyocr
            try:
                import easyocr
                reader = easyocr.Reader(['en'], gpu=False)
                ocr_results = reader.readtext(image_bytes)
                texts = [r[1] for r in ocr_results]
                confs = [r[2] for r in ocr_results]
                result['text'] = '\n'.join(texts).strip()
                result['confidence'] = round(sum(confs) / len(confs) * 100, 1) if confs else 0
                result['model'] = 'easyocr'
            except (ImportError, Exception):
                logger.warning("No OCR engine available")
                result['text'] = ''
                result['confidence'] = 0
                result['model'] = 'unavailable'

        # Parse common ID fields from extracted text
        result['fields'] = _parse_id_fields(result['text'])

    except Exception as e:
        logger.error(f"OCR extraction failed: {e}")
        result['confidence'] = 0

    result['processing_time_ms'] = round((_time.time() - start) * 1000, 2)

    # Store result
    try:
        ocr_record = OcrResult(
            student_id=student_id,
            source_image_url=source_url,
            extracted_text=result['text'][:10000],
            extracted_fields=result['fields'],
            confidence_score=result['confidence'],
            processing_time_ms=result['processing_time_ms'],
            model_used=result['model'],
        )
        db.session.add(ocr_record)
        db.session.commit()
        result['ocr_id'] = ocr_record.id
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to save OCR result: {e}")

    return result


def _parse_id_fields(text: str) -> dict:
    """Heuristic parsing of common ID card fields from OCR text."""
    import re
    fields = {}
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    for line in lines:
        line_lower = line.lower()

        # Name patterns
        if 'name' in line_lower and ':' in line:
            val = line.split(':', 1)[1].strip()
            if val and len(val) > 2:
                fields['name'] = val

        # Father name
        if any(kw in line_lower for kw in ['father', 'f.name', 'fname']):
            if ':' in line:
                val = line.split(':', 1)[1].strip()
                if val:
                    fields['father_name'] = val

        # DOB / Date of Birth
        if any(kw in line_lower for kw in ['dob', 'birth', 'date of birth', 'd.o.b']):
            date_match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', line)
            if date_match:
                fields['dob'] = date_match.group(1)

        # Phone
        phone_match = re.search(r'[\+]?[\d\s\-]{7,15}', line)
        if phone_match and ('phone' in line_lower or 'mobile' in line_lower or not fields.get('phone')):
            fields['phone'] = phone_match.group(0).strip()

        # ID / Roll number
        if any(kw in line_lower for kw in ['id:', 'roll', 'reg.', 'registration']):
            if ':' in line:
                val = line.split(':', 1)[1].strip()
                if val:
                    fields['id_number'] = val

        # Class
        if 'class' in line_lower and ':' in line:
            val = line.split(':', 1)[1].strip()
            if val:
                fields['class'] = val

    return fields


def get_ocr_results(student_id: int = None, verified: bool = None, limit: int = 50):
    """Query OCR results with filters."""
    query = OcrResult.query
    if student_id:
        query = query.filter_by(student_id=student_id)
    if verified is not None:
        query = query.filter_by(verified=verified)
    results = query.order_by(OcrResult.created_at.desc()).limit(limit).all()

    return [{
        'id': r.id,
        'student_id': r.student_id,
        'extracted_fields': r.extracted_fields,
        'confidence_score': r.confidence_score,
        'model_used': r.model_used,
        'verified': r.verified,
        'created_at': r.created_at.isoformat() if r.created_at else None,
    } for r in results]
