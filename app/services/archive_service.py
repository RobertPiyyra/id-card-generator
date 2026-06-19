"""
Data Archiving Service
Handles archival and restoration of old records.
Isolated module - creates DataArchive records.
"""
import os
import json
import gzip
import shutil
import logging
from datetime import datetime, timezone, timedelta

from models import db, DataArchive, Student, Template, ActivityLog

logger = logging.getLogger(__name__)

ARCHIVE_DIR = 'instance/archives'


def archive_old_students(older_than_days: int = 365, created_by: str = 'system') -> dict:
    """
    Archive students older than specified days.
    Returns summary of archived records.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    old_students = Student.query.filter(Student.created_at < cutoff).all()

    if not old_students:
        return {'archived': 0, 'message': 'No records to archive'}

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    filename = f'students_archive_{timestamp}.json.gz'
    filepath = os.path.join(ARCHIVE_DIR, filename)

    records = []
    student_ids = []
    for s in old_students:
        records.append({
            'id': s.id,
            'name': s.name,
            'father_name': s.father_name,
            'class_name': s.class_name,
            'dob': s.dob,
            'school_name': s.school_name,
            'phone': s.phone,
            'address': s.address,
            'email': s.email,
            'photo_url': s.photo_url,
            'image_url': s.image_url,
            'created_at': s.created_at.isoformat() if s.created_at else None,
        })
        student_ids.append(s.id)

    with gzip.open(filepath, 'wt', encoding='utf-8') as f:
        json.dump(records, f, default=str)

    file_size = os.path.getsize(filepath)

    archive = DataArchive(
        archive_name=f'Students older than {older_than_days} days',
        entity_type='student',
        entity_ids_json=student_ids,
        file_path=filepath,
        file_size_bytes=file_size,
        record_count=len(records),
        compressed=True,
        created_by=created_by,
    )
    db.session.add(archive)
    db.session.commit()

    logger.info(f"Archived {len(records)} students to {filepath}")
    return {
        'archived': len(records),
        'file': filepath,
        'size_bytes': file_size,
        'archive_id': archive.id,
    }


def archive_old_activities(older_than_days: int = 90, created_by: str = 'system') -> dict:
    """Archive old activity log entries."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    old_logs = ActivityLog.query.filter(ActivityLog.timestamp < cutoff).all()

    if not old_logs:
        return {'archived': 0, 'message': 'No records to archive'}

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    filename = f'activities_archive_{timestamp}.json.gz'
    filepath = os.path.join(ARCHIVE_DIR, filename)

    records = []
    ids = []
    for a in old_logs:
        records.append({
            'id': a.id,
            'actor': a.actor,
            'action': a.action,
            'target': a.target,
            'details': a.details,
            'ip_address': a.ip_address,
            'timestamp': a.timestamp.isoformat() if a.timestamp else None,
        })
        ids.append(a.id)

    with gzip.open(filepath, 'wt', encoding='utf-8') as f:
        json.dump(records, f, default=str)

    file_size = os.path.getsize(filepath)

    archive = DataArchive(
        archive_name=f'Activity logs older than {older_than_days} days',
        entity_type='activity_log',
        entity_ids_json=ids,
        file_path=filepath,
        file_size_bytes=file_size,
        record_count=len(records),
        compressed=True,
        created_by=created_by,
    )
    db.session.add(archive)
    db.session.commit()

    # Delete archived records from source table
    for a in old_logs:
        db.session.delete(a)
    db.session.commit()

    logger.info(f"Archived and deleted {len(records)} activity logs")
    return {
        'archived': len(records),
        'file': filepath,
        'size_bytes': file_size,
        'archive_id': archive.id,
    }


def list_archives(entity_type: str = None, limit: int = 50) -> list:
    """List existing archives."""
    query = DataArchive.query
    if entity_type:
        query = query.filter_by(entity_type=entity_type)
    archives = query.order_by(DataArchive.created_at.desc()).limit(limit).all()

    return [{
        'id': a.id,
        'name': a.archive_name,
        'entity_type': a.entity_type,
        'record_count': a.record_count,
        'file_size_bytes': a.file_size_bytes,
        'compressed': a.compressed,
        'created_by': a.created_by,
        'created_at': a.created_at.isoformat() if a.created_at else None,
    } for a in archives]


def restore_archive(archive_id: int) -> dict:
    """Restore records from an archive file."""
    archive = db.session.get(DataArchive, archive_id)
    if not archive:
        return {'restored': 0, 'error': 'Archive not found'}

    if not os.path.exists(archive.file_path):
        return {'restored': 0, 'error': 'Archive file not found'}

    try:
        opener = gzip.open if archive.compressed else open
        mode = 'rt' if archive.compressed else 'r'
        with opener(archive.file_path, mode, encoding='utf-8') as f:
            records = json.load(f)

        restored = 0
        if archive.entity_type == 'student':
            for rec in records:
                # Check if student already exists
                existing = db.session.get(Student, rec['id'])
                if not existing:
                    s = Student(
                        id=rec['id'],
                        name=rec.get('name'),
                        father_name=rec.get('father_name'),
                        class_name=rec.get('class_name'),
                        dob=rec.get('dob'),
                        school_name=rec.get('school_name'),
                        phone=rec.get('phone'),
                        address=rec.get('address'),
                        email=rec.get('email'),
                        photo_url=rec.get('photo_url'),
                        image_url=rec.get('image_url'),
                    )
                    db.session.add(s)
                    restored += 1

        db.session.commit()
        archive.restored_at = datetime.now(timezone.utc)
        db.session.commit()

        return {'restored': restored, 'total_in_archive': len(records)}
    except Exception as e:
        db.session.rollback()
        logger.error(f"Restore failed: {e}")
        return {'restored': 0, 'error': str(e)}
