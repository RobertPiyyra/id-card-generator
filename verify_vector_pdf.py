import sys
import os

# Add project directory to python path
sys.path.append("/home/robertpiyyra/id_project")

from app import app, db
from models import Template, Student

with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['admin'] = True  # Mock admin login session
    
    print("Testing editable PDF generation...")
    response = client.get('/corel/download_compiled_vector_pdf/5?mode=editable')
    print("Editable mode status:", response.status_code)
    if response.status_code == 200:
        print("Success! Generated editable PDF with size:", len(response.data), "bytes")
    else:
        print("Failed! Response:", response.data)

    print("Testing print PDF generation...")
    response = client.get('/corel/download_compiled_vector_pdf/5?mode=print')
    print("Print mode status:", response.status_code)
    if response.status_code == 200:
        print("Success! Generated print PDF with size:", len(response.data), "bytes")
    else:
        print("Failed! Response:", response.data)
