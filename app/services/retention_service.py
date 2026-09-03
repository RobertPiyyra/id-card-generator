"""
Data Retention & Photo Consent Policy Service (GDPR / FERPA compliant).

Handles:
- Parental/Student consent tracking and logs
- Configurable retention periods (30 days, 90 days, 1 year, academic year)
- Automated image purging and student data anonymization upon consent expiration
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from models import db, Student, ConsentLog, FaceEmbedding
from utils import UPLOAD_FOLDER

logger = logging.getLogger(__name__)


def calculate_retention_expiry(policy: str = "academic_year", from_date: Optional[datetime] = None) -> Optional[datetime]:
    """Calculates consent expiration datetime based on retention policy."""
    base = from_date or datetime.now(timezone.utc)
    if policy == "30_days":
        return base + timedelta(days=30)
    elif policy == "90_days":
        return base + timedelta(days=90)
    elif policy == "1_year":
        return base + timedelta(days=365)
    elif policy == "academic_year":
        # Expire at the end of the academic cycle (default: next July 1st)
        year = base.year if base.month < 7 else base.year + 1
        return datetime(year, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    elif policy == "indefinite":
        return None
    return base + timedelta(days=365)


def record_consent(
    student: Student,
    action: str = "granted",
    granted_by: str = "Admin",
    ip_address: Optional[str] = None,
    expires_in_days: Optional[int] = None,
    policy: str = "academic_year",
    document_ref: Optional[str] = None,
    details: Optional[str] = None
) -> Optional[ConsentLog]:
    """Updates student consent state and records an immutable consent log."""
    if not student:
        return None

    try:
        now = datetime.now(timezone.utc)
        student.photo_consent_status = action
        student.data_retention_policy = policy

        if action == "granted":
            student.photo_consent_granted_at = now
            if expires_in_days:
                student.consent_expires_at = now + timedelta(days=expires_in_days)
            else:
                student.consent_expires_at = calculate_retention_expiry(policy, now)
        elif action == "revoked":
            student.consent_expires_at = now  # immediately expired

        log_entry = ConsentLog(
            student_id=student.id,
            school_name=student.school_name,
            action=action,
            granted_by=granted_by,
            ip_address=ip_address,
            document_ref=document_ref,
            details=details or f"Consent {action} under policy {policy}"
        )
        db.session.add(log_entry)
        db.session.commit()
        return log_entry
    except Exception as e:
        db.session.rollback()
        logger.error("record_consent error for student %s: %s", student.id, e)
        return None


def purge_student_media(
    student: Student,
    anonymize: bool = False,
    purged_by: str = "System",
    ip_address: Optional[str] = None
) -> bool:
    """Purges photos, rendered cards, and face embeddings for a student."""
    if not student:
        return False

    try:
        # 1. Attempt to remove local photo file if stored locally
        if student.photo_filename:
            local_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
            if os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except OSError as e:
                    logger.warning("Could not delete local photo file %s: %s", local_path, e)

        # 2. Reset media pointers
        student.photo_url = None
        student.photo_filename = None
        student.image_url = None
        student.back_image_url = None
        student.pdf_url = None
        student.photo_consent_status = "purged"

        # 3. Delete any face embedding associated with this student
        FaceEmbedding.query.filter_by(student_id=student.id).delete()

        # 4. Optional GDPR anonymization
        if anonymize:
            student.name = f"Anonymized Student #{student.id}"
            student.father_name = None
            student.phone = None
            student.dob = None
            student.address = None
            student.email = None
            student.is_anonymized = True

        # 5. Log purge event
        log_entry = ConsentLog(
            student_id=student.id,
            school_name=student.school_name,
            action="purged",
            granted_by=purged_by,
            ip_address=ip_address,
            details="Automated data retention policy image purge"
        )
        db.session.add(log_entry)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        logger.error("purge_student_media failed for student %s: %s", student.id, e)
        return False


def run_retention_cleanup(school_name: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """
    Scans students with expired consent or revoked status and purges their stored media.
    """
    now = datetime.now(timezone.utc)
    query = Student.query.filter(
        (Student.consent_expires_at <= now) |
        (Student.photo_consent_status == "revoked")
    )
    if school_name:
        query = query.filter(Student.school_name == school_name)

    expired_students = query.all()
    purged_count = 0

    if not dry_run:
        for s in expired_students:
            if s.photo_url or s.photo_filename or s.image_url:
                if purge_student_media(s, anonymize=False, purged_by="ScheduledRetentionJob"):
                    purged_count += 1
    else:
        purged_count = len(expired_students)

    return {
        "success": True,
        "school_name": school_name or "ALL_SCHOOLS",
        "dry_run": dry_run,
        "total_expired": len(expired_students),
        "purged_count": purged_count,
        "timestamp": now.isoformat()
    }
