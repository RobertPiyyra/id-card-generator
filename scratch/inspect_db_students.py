import sys
import os

# Add project directory to python path
sys.path.append("/home/robertpiyyra/id_project")

from app import app, db
from models import Student

with app.app_context():
    # Query last 40 students
    students = Student.query.order_by(Student.id.desc()).limit(40).all()
    print(f"Loaded {len(students)} students from DB:")
    for s in students:
        print(f"ID: {s.id} | Name: {s.name} | Photo Filename: {s.photo_filename} | Photo URL: {s.photo_url} | Generated Filename: {s.generated_filename}")
