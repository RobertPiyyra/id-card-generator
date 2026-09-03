"""
Cryptographic Card Signing & Tamper Verification Service.

Uses Ed25519 asymmetric cryptography to:
- Generate and manage per-school cryptographic signing keypairs
- Sign canonical student ID card manifests & hashes
- Verify signatures to detect any altered data or forged cards
- Link signatures with the Immutable Audit Log
"""

import base64
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

from models import db, Student, SchoolKey
from app.services.template_lifecycle_service import log_immutable_audit_event

logger = logging.getLogger(__name__)


import os
from flask import current_app
from cryptography.fernet import Fernet

def _get_master_cipher() -> Fernet:
    """Derives a deterministic 32-byte Fernet key from application SECRET_KEY or MASTER_SIGNING_KEY."""
    secret = (
        os.getenv("MASTER_SIGNING_KEY") or
        (current_app.config.get("SECRET_KEY") if current_app else None) or
        "fallback-signing-master-secret-32b"
    ).encode("utf-8")
    key_bytes = hashlib.sha256(secret).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def get_or_create_school_key(school_name: str) -> SchoolKey:
    """Retrieves existing school signing keypair or generates a new Ed25519 keypair with encrypted private key."""
    key_rec = SchoolKey.query.filter_by(school_name=school_name).first()
    if key_rec:
        return key_rec

    # Generate fresh Ed25519 keypair
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')

    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')

    raw_pub_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    fingerprint = hashlib.sha256(raw_pub_bytes).hexdigest()[:16].upper()

    # Encrypt private key at rest
    cipher = _get_master_cipher()
    encrypted_priv_pem = cipher.encrypt(priv_pem.encode('utf-8')).decode('utf-8')

    key_rec = SchoolKey(
        school_name=school_name,
        private_key_pem=encrypted_priv_pem,
        public_key_pem=pub_pem,
        key_type='ed25519',
        fingerprint=fingerprint
    )
    db.session.add(key_rec)
    db.session.commit()
    return key_rec


def load_school_private_key(key_rec: SchoolKey):
    """Decrypts and deserializes school Ed25519 private key from database record."""
    cipher = _get_master_cipher()
    try:
        priv_pem = cipher.decrypt(key_rec.private_key_pem.encode('utf-8')).decode('utf-8')
    except Exception:
        # Fallback for unencrypted legacy keys
        priv_pem = key_rec.private_key_pem

    return serialization.load_pem_private_key(
        priv_pem.encode('utf-8'),
        password=None
    )


def generate_card_manifest(
    student: Student,
    card_bytes: Optional[bytes] = None,
    photo_bytes: Optional[bytes] = None
) -> Dict[str, Any]:
    """Builds a canonical, reproducible metadata dictionary for a student card."""
    now_iso = datetime.now(timezone.utc).isoformat()
    manifest = {
        "version": "1.0",
        "student_id": student.id,
        "name": student.name,
        "father_name": student.father_name,
        "class_name": student.class_name,
        "dob": student.dob,
        "school_name": student.school_name,
        "template_id": student.template_id,
        "issued_at": student.signed_at.isoformat() if student.signed_at else now_iso,
    }
    if card_bytes:
        manifest["card_sha256"] = hashlib.sha256(card_bytes).hexdigest()
    if photo_bytes:
        manifest["photo_sha256"] = hashlib.sha256(photo_bytes).hexdigest()

    return manifest


def canonical_manifest_bytes(manifest: Dict[str, Any]) -> bytes:
    """Serializes manifest to sorted, compact UTF-8 JSON bytes for deterministic signing."""
    return json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8')


def sign_student_card(
    student: Student,
    card_bytes: Optional[bytes] = None,
    photo_bytes: Optional[bytes] = None,
    actor: str = "System",
    actor_role: str = "school_admin",
    ip_address: Optional[str] = None
) -> Dict[str, Any]:
    """Signs student card manifest using the school's Ed25519 private key."""
    if not student or not student.school_name:
        return {"success": False, "error": "Invalid student record"}

    try:
        key_rec = get_or_create_school_key(student.school_name)
        private_key = load_school_private_key(key_rec)

        student.signed_at = datetime.now(timezone.utc)
        manifest = generate_card_manifest(student, card_bytes=card_bytes, photo_bytes=photo_bytes)
        manifest_bytes = canonical_manifest_bytes(manifest)

        signature_raw = private_key.sign(manifest_bytes)
        signature_b64 = base64.urlsafe_b64encode(signature_raw).decode('utf-8')

        student.card_signature = signature_b64
        student.card_manifest_json = manifest
        student.public_key_fingerprint = key_rec.fingerprint
        db.session.commit()

        # Log to Immutable Audit Event Chain
        log_immutable_audit_event(
            entity_type="student_card_signature",
            entity_id=str(student.id),
            action="card_digitally_signed",
            actor=actor,
            actor_role=actor_role,
            payload={
                "student_id": student.id,
                "school_name": student.school_name,
                "signature": signature_b64,
                "fingerprint": key_rec.fingerprint,
                "manifest": manifest
            }
        )

        return {
            "success": True,
            "signature": signature_b64,
            "fingerprint": key_rec.fingerprint,
            "manifest": manifest
        }

    except Exception as e:
        db.session.rollback()
        logger.error("sign_student_card failed for student %s: %s", student.id, e)
        return {"success": False, "error": str(e)}


def verify_card_signature(
    manifest_data: Any,
    signature_b64: str,
    school_name: Optional[str] = None,
    public_key_pem: Optional[str] = None
) -> Dict[str, Any]:
    """
    Verifies an Ed25519 signature against the card manifest and school public key.
    """
    if not manifest_data or not signature_b64:
        return {"is_valid": False, "error": "Missing manifest or signature"}

    try:
        # 1. Parse manifest dict
        if isinstance(manifest_data, str):
            manifest_dict = json.loads(manifest_data)
        elif isinstance(manifest_data, dict):
            manifest_dict = manifest_data
        else:
            return {"is_valid": False, "error": "Invalid manifest format"}

        target_school = school_name or manifest_dict.get("school_name")
        if not target_school and not public_key_pem:
            return {"is_valid": False, "error": "School name or public key required"}

        # 2. Obtain Public Key
        if not public_key_pem:
            key_rec = SchoolKey.query.filter_by(school_name=target_school).first()
            if not key_rec:
                return {"is_valid": False, "error": f"No public key found for school '{target_school}'"}
            public_key_pem = key_rec.public_key_pem

        public_key = serialization.load_pem_public_key(public_key_pem.encode('utf-8'))

        # 3. Decode signature & canonical manifest bytes
        signature_raw = base64.urlsafe_b64decode(signature_b64.encode('utf-8'))
        manifest_bytes = canonical_manifest_bytes(manifest_dict)

        # 4. Verify Cryptographic Signature
        public_key.verify(signature_raw, manifest_bytes)
        return {
            "is_valid": True,
            "school_name": target_school,
            "student_id": manifest_dict.get("student_id"),
            "name": manifest_dict.get("name"),
            "issued_at": manifest_dict.get("issued_at"),
            "error": None
        }

    except InvalidSignature:
        return {"is_valid": False, "error": "Signature verification failed — card data has been tampered with"}
    except Exception as e:
        logger.warning("verify_card_signature error: %s", e)
        return {"is_valid": False, "error": f"Verification error: {e}"}
