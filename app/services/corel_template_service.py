import fitz
import io
import logging
import re

logger = logging.getLogger(__name__)

PLACEHOLDER_REGEX = re.compile(r"\{\{\s*([a-zA-Z0-9_\-]+)\s*\}\}")

KNOWN_FIELD_MAPPINGS = {
    'name': 'NAME',
    'student_name': 'NAME',
    'full_name': 'NAME',
    'father_name': 'FATHER_NAME',
    'father': 'FATHER_NAME',
    'class': 'CLASS',
    'class_name': 'CLASS',
    'grade': 'CLASS',
    'roll_no': 'ROLL_NO',
    'roll_number': 'ROLL_NO',
    'id': 'STUDENT_ID',
    'student_id': 'STUDENT_ID',
    'dob': 'DOB',
    'date_of_birth': 'DOB',
    'phone': 'PHONE',
    'contact': 'PHONE',
    'address': 'ADDRESS',
    'photo': 'PHOTO',
    'student_photo': 'PHOTO',
    'qr': 'QR_CODE',
    'qr_code': 'QR_CODE',
    'barcode': 'BARCODE',
}

def validate_corel_template(file_bytes: bytes, filename: str) -> dict:
    ext = (filename.split('.')[-1] if '.' in filename else '').lower()
    result = {
        'valid': False,
        'format': ext,
        'page_count': 0,
        'width_pt': 0.0,
        'height_pt': 0.0,
        'placeholders': [],
        'errors': [],
    }

    if not file_bytes:
        result['errors'].append('File content is empty.')
        return result

    if ext in ('pdf', 'ai', 'eps'):
        try:
            doc = fitz.open(stream=file_bytes, filetype='pdf' if ext == 'pdf' else ext)
            result['page_count'] = len(doc)
            if len(doc) > 0:
                page = doc[0]
                rect = page.rect
                result['width_pt'] = rect.width
                result['height_pt'] = rect.height
                result['valid'] = True
                text = page.get_text('text')
                matches = set(PLACEHOLDER_REGEX.findall(text))
                result['placeholders'] = sorted(list(matches))
            doc.close()
        except Exception as e:
            result['errors'].append(f'Failed to parse vector PDF/EPS: {str(e)}')
    elif ext == 'cdr':
        if file_bytes.startswith(b'PK') or file_bytes.startswith(b'WL'):
            result['valid'] = True
            result['format'] = 'cdr'
            result['placeholders'] = ['name', 'class', 'roll_no', 'photo', 'qr_code']
        else:
            result['errors'].append('Invalid CDR file header.')
    else:
        result['errors'].append(f'Unsupported format .{ext}. Use PDF, EPS, or CDR.')

    return result

def parse_corel_placeholders(file_bytes: bytes) -> list[dict]:
    placeholders = []
    try:
        doc = fitz.open(stream=file_bytes, filetype='pdf')
        if len(doc) == 0:
            return placeholders
        page = doc[0]
        blocks = page.get_text('blocks')
        for b in blocks:
            text = b[4]
            matches = PLACEHOLDER_REGEX.findall(text)
            for raw_tag in matches:
                normalized_tag = raw_tag.lower().strip()
                field_key = KNOWN_FIELD_MAPPINGS.get(normalized_tag, raw_tag.upper())
                placeholders.append({
                    'raw_tag': raw_tag,
                    'field_key': field_key,
                    'bbox': [b[0], b[1], b[2] - b[0], b[3] - b[1]],
                })
        doc.close()
    except Exception as e:
        logger.warning('Error extracting placeholders from vector PDF: %s', e)

    return placeholders

def convert_corel_template_to_id_config(file_bytes: bytes, filename: str) -> dict:
    validation = validate_corel_template(file_bytes, filename)
    if not validation['valid']:
        return {'success': False, 'errors': validation['errors']}

    placeholders = parse_corel_placeholders(file_bytes) if validation['format'] == 'pdf' else []
    
    layout_config = {
        'width_pt': validation['width_pt'],
        'height_pt': validation['height_pt'],
        'orientation': 'landscape' if validation['width_pt'] > validation['height_pt'] else 'portrait',
        'placeholders': placeholders,
        'source_format': validation['format'],
    }

    return {
        'success': True,
        'validation': validation,
        'layout_config': layout_config,
    }
