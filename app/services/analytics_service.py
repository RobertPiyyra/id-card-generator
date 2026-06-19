"""
Enterprise Analytics Service
Aggregates statistics for dashboard widgets.
Reads from existing models, does not modify any existing logic.
"""
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import func

from models import (
    db, Student, Template, ActivityLog, BulkJob,
    LoginHistory, VerificationAudit
)

logger = logging.getLogger(__name__)


def get_dashboard_stats() -> dict:
    """High-level counts for the main dashboard."""
    return {
        'total_templates': Template.query.count(),
        'total_students': Student.query.count(),
        'total_cards_generated': Student.query.filter(
            Student.image_url.isnot(None)
        ).count(),
        'active_templates': Template.query.filter(
            Template.deadline.is_(None) | (Template.deadline > datetime.now(timezone.utc))
        ).count(),
    }


def get_recent_activity(limit: int = 20) -> list:
    """Recent activity across all models for the activity feed."""
    activities = ActivityLog.query.order_by(
        ActivityLog.timestamp.desc()
    ).limit(limit).all()

    return [{
        'actor': a.actor,
        'action': a.action,
        'target': a.target,
        'details': a.details,
        'ip_address': a.ip_address,
        'timestamp': a.timestamp.isoformat() if a.timestamp else None,
    } for a in activities]


def get_student_analytics(days: int = 30) -> dict:
    """Student registration and card generation analytics."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    new_students = Student.query.filter(Student.created_at >= cutoff).count()
    total_students = Student.query.count()

    # Cards generated in period
    generated = Student.query.filter(
        Student.created_at >= cutoff,
        Student.image_url.isnot(None)
    ).count()

    # Templates created in period
    templates_created = Template.query.filter(
        Template.created_at >= cutoff
    ).count()

    # Per-school breakdown
    school_breakdown = db.session.query(
        Student.school_name,
        func.count(Student.id)
    ).group_by(Student.school_name).order_by(
        func.count(Student.id).desc()
    ).limit(10).all()

    return {
        'new_students': new_students,
        'total_students': total_students,
        'cards_generated': generated,
        'templates_created': templates_created,
        'schools': [{'name': s[0] or 'Unknown', 'count': s[1]} for s in school_breakdown],
    }


def get_bulk_job_analytics() -> dict:
    """Bulk job execution analytics."""
    total_jobs = BulkJob.query.count()
    completed = BulkJob.query.filter_by(status='completed').count()
    failed = BulkJob.query.filter_by(status='failed').count()
    processing = BulkJob.query.filter_by(status='processing').count()

    total_items = db.session.query(func.sum(BulkJob.total_items)).scalar() or 0
    processed_items = db.session.query(func.sum(BulkJob.processed_items)).scalar() or 0

    return {
        'total_jobs': total_jobs,
        'completed': completed,
        'failed': failed,
        'processing': processing,
        'total_items': int(total_items),
        'processed_items': int(processed_items),
        'success_rate': round(completed / total_jobs * 100, 1) if total_jobs else 0,
    }


def get_verification_stats(days: int = 7) -> dict:
    """QR code verification scan statistics."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base_q = VerificationAudit.query.filter(VerificationAudit.created_at >= cutoff)

    total = base_q.count()
    valid = base_q.filter_by(status='ok').count()
    invalid = base_q.filter(VerificationAudit.status.in_(['invalid', 'tampered', 'expired', 'revoked'])).count()

    # Daily breakdown
    daily = db.session.query(
        func.date(VerificationAudit.created_at),
        func.count(VerificationAudit.id)
    ).filter(
        VerificationAudit.created_at >= cutoff
    ).group_by(
        func.date(VerificationAudit.created_at)
    ).order_by(
        func.date(VerificationAudit.created_at)
    ).all()

    return {
        'total_scans': total,
        'valid': valid,
        'invalid': invalid,
        'daily': [{'date': str(d[0]), 'count': d[1]} for d in daily],
    }


def get_template_usage_stats() -> dict:
    """Which templates have the most students."""
    results = db.session.query(
        Template.school_name,
        func.count(Student.id)
    ).join(
        Student, Student.template_id == Template.id
    ).group_by(
        Template.id, Template.school_name
    ).order_by(
        func.count(Student.id).desc()
    ).limit(10).all()

    return {
        'templates': [{'school': r[0], 'student_count': r[1]} for r in results],
    }


def get_system_health_snapshot() -> dict:
    """Quick system health indicators."""
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)

    recent_errors = ActivityLog.query.filter(
        ActivityLog.timestamp >= one_hour_ago,
        ActivityLog.action.ilike('%error%')
    ).count()

    recent_logins = LoginHistory.query.filter(
        LoginHistory.created_at >= one_hour_ago,
        LoginHistory.login_success == True
    ).count()

    recent_failed_logins = LoginHistory.query.filter(
        LoginHistory.created_at >= one_hour_ago,
        LoginHistory.login_success == False
    ).count()

    return {
        'status': 'healthy' if recent_errors < 10 else 'degraded',
        'recent_errors': recent_errors,
        'recent_logins': recent_logins,
        'recent_failed_logins': recent_failed_logins,
        'timestamp': now.isoformat(),
    }
