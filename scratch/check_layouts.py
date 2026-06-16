import sys
import os
import json

# Add project directory to python path
sys.path.append("/home/robertpiyyra/id_project")

from app import app
from models import Template, TemplateField

with app.app_context():
    templates = Template.query.all()
    for t in templates:
        print(f"=== Template ID: {t.id} ({t.school_name}) ===")
        print("Layout Config (Front):", t.layout_config)
        print("Layout Config (Back):", t.back_layout_config)
        fields = TemplateField.query.filter_by(template_id=t.id).all()
        print("Dynamic fields:")
        for f in fields:
            print(f"  Name: {f.field_name}, Label: {f.field_label}, Order: {f.display_order}")
        print()
