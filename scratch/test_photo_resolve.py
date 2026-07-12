import os
import sys

# Add app to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.legacy_app import app
from models import db, SerialCard, Template
from app.services.photo_service import resolve_student_photo_reference, load_student_photo_rgba
from app.routes.serial_batch_routes import _card_to_student_dict

with app.app_context():
    card = SerialCard.query.first()
    if not card:
        print("No cards found in DB")
        sys.exit(0)
    
    print(f"Card ID: {card.id}")
    print(f"Card photo_path: {card.photo_path}")
    print(f"Card photo_thumbnail: {card.photo_thumbnail}")
    
    student_data = _card_to_student_dict(card, card.batch.template_id)
    student_like = type('StudentLike', (), student_data)()
    
    print(f"StudentLike photo_url: {getattr(student_like, 'photo_url', None)}")
    print(f"StudentLike photo_filename: {getattr(student_like, 'photo_filename', None)}")
    
    photo_url, local_path = resolve_student_photo_reference(student_like)
    print(f"Resolved: photo_url={photo_url}, local_path={local_path}")
    if local_path:
        print(f"local_path exists? {os.path.exists(local_path)}")
    
    # Try loading it
    img = load_student_photo_rgba(student_like, 100, 100)
    if img:
        print(f"Successfully loaded image of size: {img.size}")
    else:
        print("Failed to load image (returned None)")
