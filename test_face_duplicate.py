import pytest
from PIL import Image, ImageDraw
import numpy as np

from app.services.face_service import compute_face_embedding, compute_face_phash, calculate_face_similarity
from app.services.face_duplicate_service import find_duplicate_faces, save_face_embedding_for_student
from models import db, Student, FaceEmbedding, Template


def _create_sample_face_image(draw_features=True):
    """Generates a synthetic portrait image with facial features."""
    img = Image.new("RGB", (200, 250), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    # Head oval
    draw.ellipse([50, 40, 150, 180], fill=(220, 180, 150), outline=(100, 70, 50))
    if draw_features:
        # Eyes
        draw.ellipse([70, 80, 90, 100], fill=(50, 30, 20))
        draw.ellipse([110, 80, 130, 100], fill=(50, 30, 20))
        # Nose
        draw.polygon([(100, 100), (95, 125), (105, 125)], fill=(180, 140, 110))
        # Mouth
        draw.rectangle([80, 145, 120, 155], fill=(180, 50, 50))
    return img


def test_face_phash_computation():
    img1 = _create_sample_face_image()
    phash1 = compute_face_phash(img1)
    assert phash1 is not None
    assert len(phash1) == 16  # 16-hex characters for 64-bit hash

    # Identical image gives identical hash
    phash2 = compute_face_phash(img1.copy())
    assert phash1 == phash2


def test_face_embedding_and_similarity():
    img1 = _create_sample_face_image()
    vec1, phash1 = compute_face_embedding(img1)

    assert vec1 is not None
    assert len(vec1) == 128
    assert phash1 is not None

    # Self-similarity should be 1.0
    sim_self = calculate_face_similarity(vec1, vec1)
    assert abs(sim_self - 1.0) < 1e-4

    # Different vector should have lower similarity
    diff_vec = [0.0] * 128
    diff_vec[0] = 1.0
    sim_diff = calculate_face_similarity(vec1, diff_vec)
    assert sim_diff < 0.90


def test_duplicate_face_detection_within_school(app, db):
    with app.app_context():
        school_a = "Test School Alpha"
        school_b = "Test School Beta"

        template_a = Template(school_name=school_a)
        db.session.add(template_a)
        db.session.commit()

        student1 = Student(
            name="Alice Smith",
            father_name="Bob Smith",
            class_name="10-A",
            school_name=school_a,
            template_id=template_a.id
        )
        db.session.add(student1)
        db.session.commit()

        img = _create_sample_face_image()
        rec = save_face_embedding_for_student(student1, pil_img=img)
        assert rec is not None
        assert rec.student_id == student1.id

        # Search with the same image in School A -> Duplicate Found
        matches = find_duplicate_faces(school_name=school_a, pil_img=img, threshold=0.80)
        assert len(matches) == 1
        assert matches[0]["student_id"] == student1.id
        assert matches[0]["similarity"] >= 0.80

        # Search with the same image in School B -> No match (school isolation)
        matches_b = find_duplicate_faces(school_name=school_b, pil_img=img, threshold=0.80)
        assert len(matches_b) == 0

        # Exclude self check
        matches_excluded = find_duplicate_faces(
            school_name=school_a,
            pil_img=img,
            threshold=0.80,
            exclude_student_id=student1.id
        )
        assert len(matches_excluded) == 0
