import sys
import os

# Add project directory to python path
sys.path.append("/home/robertpiyyra/id_project")

from app import app, db
from models import Template, Student
from app.services.render_service import render_student_card_side

with app.app_context():
    template = db.session.get(Template, 5)
    student = Student.query.filter_by(template_id=5).first()
    print("Found template:", template)
    print("Found student:", student)
    
    if template and student:
        # Render front side
        img = render_student_card_side(template, student, side="front", render_scale=1.0)
        if img:
            img.save("test_card_front.png")
            print("Successfully rendered and saved test_card_front.png")
            # Let's inspect some pixels where text is drawn.
            # We can find the center of the image or look at its colors.
            print("Image mode:", img.mode, "Size:", img.size)
        else:
            print("Failed to render card side.")
