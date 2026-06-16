import sys
import os

# Add project directory to python path
sys.path.append("/home/robertpiyyra/id_project")

from app import app, db
from models import Template

with app.app_context():
    templates = Template.query.all()
    print(f"Total templates in database: {len(templates)}")
    for t in templates:
        print(f"ID: {t.id} | School: {t.school_name}")
        print(f"  Front photo_settings: {t.photo_settings}")
        print(f"  Back photo_settings: {t.back_photo_settings}")
