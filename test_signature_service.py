import pytest
from app.services.signature_service import (
    get_or_create_school_key,
    load_school_private_key,
    generate_card_manifest,
    sign_student_card,
    verify_card_signature,
)
from models import db, Student, Template, SchoolKey, ImmutableAuditEvent


def test_school_key_generation(app, db):
    with app.app_context():
        school_name = "Digital Sign Academy"
        key_rec = get_or_create_school_key(school_name)

        assert key_rec is not None
        assert key_rec.school_name == school_name
        # Verify private key is encrypted at rest (Fernet ciphertext token)
        assert key_rec.private_key_pem.startswith("gAAAAA")
        assert "BEGIN PUBLIC KEY" in key_rec.public_key_pem
        assert len(key_rec.fingerprint) == 16

        # Decrypting should return valid private key instance
        priv_key = load_school_private_key(key_rec)
        assert priv_key is not None

        # Fetching again should return the exact same key
        key_rec2 = get_or_create_school_key(school_name)
        assert key_rec2.id == key_rec.id
        assert key_rec2.fingerprint == key_rec.fingerprint


def test_card_signing_and_verification(app, db):
    with app.app_context():
        school_name = "Digital Sign Academy"
        template = Template(school_name=school_name)
        db.session.add(template)
        db.session.commit()

        student = Student(
            name="Robert Johnson",
            father_name="Edward Johnson",
            class_name="12-A",
            dob="2008-05-14",
            school_name=school_name,
            template_id=template.id
        )
        db.session.add(student)
        db.session.commit()

        # Sign student card
        sign_result = sign_student_card(student)
        assert sign_result["success"] is True
        signature = sign_result["signature"]
        manifest = sign_result["manifest"]

        assert signature is not None
        assert student.card_signature == signature
        assert student.public_key_fingerprint is not None

        # Verify authentic signature
        ver_result = verify_card_signature(
            manifest_data=manifest,
            signature_b64=signature,
            school_name=school_name
        )
        assert ver_result["is_valid"] is True
        assert ver_result["student_id"] == student.id
        assert ver_result["name"] == "Robert Johnson"

        # Tamper test: Alter name in manifest -> Verification MUST FAIL
        tampered_manifest = dict(manifest)
        tampered_manifest["name"] = "Forged Name"
        tamper_result = verify_card_signature(
            manifest_data=tampered_manifest,
            signature_b64=signature,
            school_name=school_name
        )
        assert tamper_result["is_valid"] is False
        assert "tampered" in tamper_result["error"].lower() or "failed" in tamper_result["error"].lower()


def test_signature_audit_event_logged(app, db):
    with app.app_context():
        school_name = "Audit Test School"
        template = Template(school_name=school_name)
        db.session.add(template)
        db.session.commit()

        student = Student(
            name="Samuel Green",
            school_name=school_name,
            template_id=template.id
        )
        db.session.add(student)
        db.session.commit()

        sign_student_card(student, actor="Principal Davis", actor_role="school_admin")

        audit = ImmutableAuditEvent.query.filter_by(
            entity_type="student_card_signature",
            entity_id=str(student.id)
        ).first()

        assert audit is not None
        assert audit.action == "card_digitally_signed"
        assert audit.actor == "Principal Davis"
        assert audit.payload_json["signature"] == student.card_signature
