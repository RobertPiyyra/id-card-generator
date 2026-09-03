import pytest
from datetime import datetime, timezone, timedelta

from app.services.retention_service import (
    calculate_retention_expiry,
    record_consent,
    purge_student_media,
    run_retention_cleanup,
)
from models import db, Student, Template, ConsentLog, FaceEmbedding


def test_calculate_retention_expiry():
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    
    # 30 days
    exp_30 = calculate_retention_expiry("30_days", from_date=base)
    assert (exp_30 - base).days == 30

    # 90 days
    exp_90 = calculate_retention_expiry("90_days", from_date=base)
    assert (exp_90 - base).days == 90

    # 1 year
    exp_1y = calculate_retention_expiry("1_year", from_date=base)
    assert (exp_1y - base).days == 365

    # Indefinite
    exp_indef = calculate_retention_expiry("indefinite", from_date=base)
    assert exp_indef is None


def test_record_consent_workflow(app, db):
    with app.app_context():
        school_name = "Lincoln High"
        template = Template(school_name=school_name)
        db.session.add(template)
        db.session.commit()

        student = Student(
            name="John Doe",
            school_name=school_name,
            template_id=template.id
        )
        db.session.add(student)
        db.session.commit()

        # Grant consent for 90 days
        log_entry = record_consent(
            student=student,
            action="granted",
            granted_by="Parent (Mrs. Doe)",
            policy="90_days",
            document_ref="FORM-2026-001"
        )
        assert log_entry is not None
        assert student.photo_consent_status == "granted"
        assert student.consent_expires_at is not None

        # Revoke consent
        log_revoke = record_consent(
            student=student,
            action="revoked",
            granted_by="Admin"
        )
        assert log_revoke is not None
        assert student.photo_consent_status == "revoked"


def test_purge_student_media_and_anonymization(app, db):
    with app.app_context():
        school_name = "Lincoln High"
        template = Template(school_name=school_name)
        db.session.add(template)
        db.session.commit()

        student = Student(
            name="Jane Doe",
            father_name="Richard Doe",
            school_name=school_name,
            template_id=template.id,
            photo_url="https://res.cloudinary.com/demo/image/upload/sample.jpg",
            image_url="https://res.cloudinary.com/demo/image/upload/card.jpg"
        )
        db.session.add(student)
        db.session.commit()

        # Add face embedding
        emb = FaceEmbedding(
            student_id=student.id,
            school_name=school_name,
            embedding_vector=[0.1] * 128,
            face_phash="1234567890abcdef"
        )
        db.session.add(emb)
        db.session.commit()

        # Execute media purge with anonymization
        success = purge_student_media(student, anonymize=True, purged_by="ComplianceAdmin")
        assert success is True

        assert student.photo_url is None
        assert student.image_url is None
        assert student.photo_consent_status == "purged"
        assert student.is_anonymized is True
        assert "Anonymized Student" in student.name
        assert student.father_name is None

        # Face embedding should be deleted
        remaining_emb = FaceEmbedding.query.filter_by(student_id=student.id).first()
        assert remaining_emb is None


def test_run_retention_cleanup(app, db):
    with app.app_context():
        school_name = "Retention Test Academy"
        template = Template(school_name=school_name)
        db.session.add(template)
        db.session.commit()

        # Expired student
        past_date = datetime.now(timezone.utc) - timedelta(days=10)
        s_expired = Student(
            name="Expired Student",
            school_name=school_name,
            template_id=template.id,
            photo_url="https://example.com/photo.jpg",
            consent_expires_at=past_date,
            photo_consent_status="granted"
        )

        # Active student
        future_date = datetime.now(timezone.utc) + timedelta(days=100)
        s_active = Student(
            name="Active Student",
            school_name=school_name,
            template_id=template.id,
            photo_url="https://example.com/photo_active.jpg",
            consent_expires_at=future_date,
            photo_consent_status="granted"
        )

        db.session.add_all([s_expired, s_active])
        db.session.commit()

        result = run_retention_cleanup(school_name=school_name, dry_run=False)
        assert result["success"] is True
        assert result["purged_count"] == 1

        # Check DB states
        assert s_expired.photo_url is None
        assert s_expired.photo_consent_status == "purged"
        assert s_active.photo_url is not None
        assert s_active.photo_consent_status == "granted"
