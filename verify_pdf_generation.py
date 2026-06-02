import sys
import os

# Add project directory to python path
sys.path.append("/home/robertpiyyra/id_project")

from app import app, db
from models import Template, Student

with app.app_context():
    template = db.session.get(Template, 5)
    students = Student.query.filter_by(template_id=5).all()
    print("Found template:", template)
    print("Found students count:", len(students))

with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['admin'] = True  # Mock admin login session
    
    print("Requesting print mode PDF...")
    response = client.get('/corel/download_compiled_vector_pdf/5?mode=print')
    print("Print PDF status code:", response.status_code)
    if response.status_code == 200:
        pdf_len = len(response.data)
        print(f"Success! Print PDF generated. Length: {pdf_len} bytes")
        with open('verify_output_print.pdf', 'wb') as f:
            f.write(response.data)
    else:
        print("Print PDF failed:", response.data)

    print("Requesting editable mode PDF...")
    response = client.get('/corel/download_compiled_vector_pdf/5?mode=editable')
    print("Editable PDF status code:", response.status_code)
    if response.status_code == 200:
        pdf_len = len(response.data)
        print(f"Success! Editable PDF generated. Length: {pdf_len} bytes")
        with open('verify_output_editable.pdf', 'wb') as f:
            f.write(response.data)
    else:
        print("Editable PDF failed:", response.data)
