"""
Advanced Search Service
Provides smart filtering and search across all major entities.
Isolated - reads from existing models only.
"""
import logging
from datetime import datetime, timezone
from sqlalchemy import or_, and_, func

from models import db, Student, Template, ActivityLog, BulkJob

logger = logging.getLogger(__name__)


def search_students(query: str = None, school_name: str = None,
                    class_name: str = None, has_photo: bool = None,
                    has_card: bool = None, date_from: str = None,
                    date_to: str = None, page: int = 1,
                    per_page: int = 25) -> dict:
    """
    Advanced student search with multiple filters.
    Returns paginated results.
    """
    q = Student.query

    if query:
        search = f"%{query}%"
        q = q.filter(
            or_(
                Student.name.ilike(search),
                Student.father_name.ilike(search),
                Student.email.ilike(search),
                Student.phone.ilike(search),
                Student.address.ilike(search),
            )
        )

    if school_name:
        q = q.filter(Student.school_name.ilike(f"%{school_name}%"))

    if class_name:
        q = q.filter(Student.class_name.ilike(f"%{class_name}%"))

    if has_photo is not None:
        if has_photo:
            q = q.filter(
                or_(Student.photo_url.isnot(None), Student.photo_filename.isnot(None))
            )
        else:
            q = q.filter(
                and_(Student.photo_url.is_(None), Student.photo_filename.is_(None))
            )

    if has_card is not None:
        if has_card:
            q = q.filter(Student.image_url.isnot(None))
        else:
            q = q.filter(Student.image_url.is_(None))

    if date_from:
        try:
            df = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            q = q.filter(Student.created_at >= df)
        except ValueError:
            pass

    if date_to:
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            q = q.filter(Student.created_at <= dt)
        except ValueError:
            pass

    total = q.count()
    results = q.order_by(Student.created_at.desc()).offset(
        (page - 1) * per_page
    ).limit(per_page).all()

    return {
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page,
        'results': [{
            'id': s.id,
            'name': s.name,
            'father_name': s.father_name,
            'class_name': s.class_name,
            'school_name': s.school_name,
            'email': s.email,
            'phone': s.phone,
            'has_photo': bool(s.photo_url or s.photo_filename),
            'has_card': bool(s.image_url),
            'created_at': s.created_at.isoformat() if s.created_at else None,
        } for s in results],
    }


def search_templates(query: str = None, orientation: str = None,
                     language: str = None, is_double_sided: bool = None,
                     page: int = 1, per_page: int = 25) -> dict:
    """Advanced template search."""
    q = Template.query

    if query:
        search = f"%{query}%"
        q = q.filter(
            or_(
                Template.school_name.ilike(search),
                Template.filename.ilike(search),
            )
        )

    if orientation:
        q = q.filter_by(card_orientation=orientation)

    if language:
        q = q.filter_by(language=language)

    if is_double_sided is not None:
        q = q.filter_by(is_double_sided=is_double_sided)

    total = q.count()
    results = q.order_by(Template.created_at.desc()).offset(
        (page - 1) * per_page
    ).limit(per_page).all()

    return {
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page,
        'results': [{
            'id': t.id,
            'school_name': t.school_name,
            'filename': t.filename,
            'orientation': t.card_orientation,
            'language': t.language,
            'is_double_sided': t.is_double_sided,
            'deadline': t.deadline.isoformat() if t.deadline else None,
            'student_count': t.students.count(),
            'created_at': t.created_at.isoformat() if t.created_at else None,
        } for t in results],
    }


def search_activity_logs(query: str = None, actor: str = None,
                         action: str = None, date_from: str = None,
                         page: int = 1, per_page: int = 50) -> dict:
    """Search activity logs with filters."""
    q = ActivityLog.query

    if query:
        search = f"%{query}%"
        q = q.filter(
            or_(
                ActivityLog.action.ilike(search),
                ActivityLog.details.ilike(search),
                ActivityLog.target.ilike(search),
            )
        )

    if actor:
        q = q.filter(ActivityLog.actor.ilike(f"%{actor}%"))

    if action:
        q = q.filter(ActivityLog.action.ilike(f"%{action}%"))

    if date_from:
        try:
            df = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            q = q.filter(ActivityLog.timestamp >= df)
        except ValueError:
            pass

    total = q.count()
    results = q.order_by(ActivityLog.timestamp.desc()).offset(
        (page - 1) * per_page
    ).limit(per_page).all()

    return {
        'total': total,
        'page': page,
        'per_page': per_page,
        'results': [{
            'id': a.id,
            'actor': a.actor,
            'action': a.action,
            'target': a.target,
            'details': a.details,
            'ip_address': a.ip_address,
            'timestamp': a.timestamp.isoformat() if a.timestamp else None,
        } for a in results],
    }
