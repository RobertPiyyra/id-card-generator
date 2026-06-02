import sys
import os

# Add project directory to python path
sys.path.append("/home/robertpiyyra/id_project")

from app import app, db
from models import Template, Student
from utils import get_template_settings

with app.app_context():
    template = db.session.get(Template, 5)
    print("Template:", template)
    if template:
        print("School name:", template.school_name)
        print("Double sided:", getattr(template, "is_double_sided", False))
        
        # Front side settings
        font_settings, photo_settings, qr_settings, orientation = get_template_settings(5, side="front")
        print("\n--- FRONT SIDE SETTINGS ---")
        print("enable_label_gradient:", font_settings.get("enable_label_gradient"))
        print("label_font_color:", font_settings.get("label_font_color"))
        print("label_font_color_bottom:", font_settings.get("label_font_color_bottom"))
        print("enable_value_gradient:", font_settings.get("enable_value_gradient"))
        print("value_font_color:", font_settings.get("value_font_color"))
        print("value_font_color_bottom:", font_settings.get("value_font_color_bottom"))
        print("enable_colon_gradient:", font_settings.get("enable_colon_gradient"))
        print("colon_font_color:", font_settings.get("colon_font_color"))
        print("colon_font_color_bottom:", font_settings.get("colon_font_color_bottom"))
        
        # Back side settings
        font_settings_b, photo_settings_b, qr_settings_b, orientation_b = get_template_settings(5, side="back")
        print("\n--- BACK SIDE SETTINGS ---")
        print("enable_label_gradient:", font_settings_b.get("enable_label_gradient"))
        print("enable_value_gradient:", font_settings_b.get("enable_value_gradient"))
