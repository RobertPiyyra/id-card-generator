import io
import fitz
import zipfile
from types import SimpleNamespace
from app.services.corel_export_service import generate_class_wise_corel_export, _corel_safe_pdf_bytes
from app.services.corel_template_service import validate_corel_template, convert_corel_template_to_id_config

def test_generate_class_wise_corel_export():
    template = SimpleNamespace(
        id=1,
        school_name='Test Academy',
        is_double_sided=False,
        layout_config=None,
    )
    students = [
        SimpleNamespace(id=1, name='Alice', class_name='Class 10A', school_name='Test Academy'),
        SimpleNamespace(id=2, name='Bob', class_name='Class 10A', school_name='Test Academy'),
        SimpleNamespace(id=3, name='Charlie', class_name='Class 10B', school_name='Test Academy'),
    ]

    def mock_export_fn(class_students):
        doc = fitz.open()
        doc.new_page()
        b = _corel_safe_pdf_bytes(doc)
        doc.close()
        return b

    result = generate_class_wise_corel_export(template, students, mode='editable', export_fn=mock_export_fn)
    
    assert 'zip_bytes' in result
    assert result['total_classes'] == 2
    assert result['total_students'] == 3
    assert 'Class 10A' in result['summary']
    assert 'Class 10B' in result['summary']
    assert result['summary']['Class 10A']['count'] == 2
    assert result['summary']['Class 10B']['count'] == 1

    # Verify ZIP buffer contains class files
    zf = zipfile.ZipFile(io.BytesIO(result['zip_bytes']))
    namelist = zf.namelist()
    assert 'Class_Class 10A_ID_Cards.pdf' in namelist
    assert 'Class_Class 10B_ID_Cards.pdf' in namelist


def test_corel_template_validation():
    # Test invalid format
    res = validate_corel_template(b'invalid text', 'template.xyz')
    assert res['valid'] is False
    assert len(res['errors']) > 0

    # Test dummy CDR magic header
    res_cdr = validate_corel_template(b'PK123456', 'design.cdr')
    assert res_cdr['valid'] is True
    assert res_cdr['format'] == 'cdr'
