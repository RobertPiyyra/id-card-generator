"""
Advanced analytics service for the ID card platform.
Provides real-time dashboards, usage statistics, and performance metrics.
"""
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import func, case, distinct

from app.extensions import db

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Comprehensive analytics for the ID card platform."""

    @staticmethod
    def get_dashboard_summary(days=30):
        """Get high-level dashboard summary for the given period."""
        import json as _json
        from app.services.cache_service import cache_get, cache_set

        cache_key = f"analytics:dashboard:{days}"
        try:
            cached = cache_get(cache_key)
            if cached:
                if isinstance(cached, str):
                    return _json.loads(cached)
                return cached
        except Exception:
            pass

        from models import Student, Template, BulkJob, ActivityLog, PrintQueue

        since = datetime.now(timezone.utc) - timedelta(days=days)

        # Student statistics
        total_students = Student.query.count()
        new_students = Student.query.filter(Student.created_at >= since).count()

        # Template statistics
        total_templates = Template.query.count()
        active_templates = Template.query.filter(
            Template.deadline.is_(None) | (Template.deadline > datetime.now(timezone.utc))
        ).count()

        # Bulk job statistics
        total_jobs = BulkJob.query.filter(BulkJob.created_at >= since).count()
        completed_jobs = BulkJob.query.filter(
            BulkJob.created_at >= since,
            BulkJob.status.in_(["completed", "completed_with_errors"]),
        ).count()
        failed_jobs = BulkJob.query.filter(
            BulkJob.created_at >= since,
            BulkJob.status == "failed",
        ).count()

        # Print queue statistics
        total_prints = PrintQueue.query.filter(PrintQueue.created_at >= since).count()
        successful_prints = PrintQueue.query.filter(
            PrintQueue.created_at >= since,
            PrintQueue.status == "completed",
        ).count()

        # Activity statistics
        total_activities = ActivityLog.query.filter(ActivityLog.timestamp >= since).count()

        result = {
            "period_days": days,
            "students": {
                "total": total_students,
                "new": new_students,
                "growth_pct": round(new_students / max(total_students - new_students, 1) * 100, 1),
            },
            "templates": {
                "total": total_templates,
                "active": active_templates,
            },
            "bulk_jobs": {
                "total": total_jobs,
                "completed": completed_jobs,
                "failed": failed_jobs,
                "success_rate": round(completed_jobs / max(total_jobs, 1) * 100, 1),
            },
            "prints": {
                "total": total_prints,
                "successful": successful_prints,
                "success_rate": round(successful_prints / max(total_prints, 1) * 100, 1),
            },
            "activities": total_activities,
        }

        try:
            cache_set(cache_key, _json.dumps(result, default=str), ttl=30)
        except Exception:
            pass

        return result

    @staticmethod
    def get_school_breakdown(limit=20):
        """Get statistics grouped by school."""
        from models import Student, Template, BulkJob

        results = db.session.query(
            Student.school_name,
            func.count(distinct(Student.id)).label("student_count"),
            func.count(distinct(Template.id)).label("template_count"),
        ).outerjoin(
            Template, Template.school_name == Student.school_name
        ).group_by(
            Student.school_name
        ).order_by(
            func.count(distinct(Student.id)).desc()
        ).limit(limit).all()

        return [
            {
                "school": row.school_name,
                "students": row.student_count,
                "templates": row.template_count,
            }
            for row in results
        ]

    @staticmethod
    def get_generation_trend(days=30, granularity="day"):
        """Get card generation trend over time."""
        from models import Student

        since = datetime.now(timezone.utc) - timedelta(days=days)

        if granularity == "hour":
            date_trunc = func.strftime("%Y-%m-%d %H:00", Student.created_at)
        elif granularity == "week":
            date_trunc = func.strftime("%Y-W%W", Student.created_at)
        else:  # day
            date_trunc = func.date(Student.created_at)

        results = db.session.query(
            date_trunc.label("period"),
            func.count(Student.id).label("count"),
        ).filter(
            Student.created_at >= since
        ).group_by(
            "period"
        ).order_by(
            "period"
        ).all()

        return [{"period": row.period, "count": row.count} for row in results]

    @staticmethod
    def get_bulk_job_stats(days=30):
        """Get detailed bulk job statistics."""
        from models import BulkJob, BulkJobItem

        since = datetime.now(timezone.utc) - timedelta(days=days)

        # Job status distribution
        status_dist = db.session.query(
            BulkJob.status,
            func.count(BulkJob.id).label("count"),
        ).filter(
            BulkJob.created_at >= since
        ).group_by(
            BulkJob.status
        ).all()

        # Average processing time for completed jobs
        avg_time = db.session.query(
            func.avg(
                func.julianday(BulkJob.updated_at) - func.julianday(BulkJob.created_at)
            ).label("avg_days"),
        ).filter(
            BulkJob.created_at >= since,
            BulkJob.status.in_(["completed", "completed_with_errors"]),
        ).scalar()

        # Items per job distribution
        items_per_job = db.session.query(
            func.count(BulkJobItem.id).label("item_count"),
        ).join(
            BulkJob, BulkJob.id == BulkJobItem.bulk_job_id
        ).filter(
            BulkJob.created_at >= since
        ).group_by(
            BulkJobItem.bulk_job_id
        ).all()

        avg_items = sum(row.item_count for row in items_per_job) / max(len(items_per_job), 1)

        return {
            "status_distribution": {row.status: row.count for row in status_dist},
            "avg_processing_time_days": round(avg_time or 0, 2),
            "avg_items_per_job": round(avg_items, 1),
            "total_jobs": sum(row.count for row in status_dist),
        }

    @staticmethod
    def get_performance_metrics():
        """Get system performance metrics."""
        import psutil
        import os

        process = psutil.Process(os.getpid())

        return {
            "cpu_percent": process.cpu_percent(interval=0.1),
            "memory_mb": round(process.memory_info().rss / 1024 / 1024, 1),
            "memory_percent": round(process.memory_percent(), 1),
            "threads": process.num_threads(),
            "open_files": len(process.open_files()),
            "connections": len(process.connections()),
            "system": {
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_percent": psutil.disk_usage("/").percent,
            },
        }

    @staticmethod
    def get_recent_activities(limit=50):
        """Get recent system activities."""
        from models import ActivityLog

        activities = ActivityLog.query.order_by(
            ActivityLog.timestamp.desc()
        ).limit(limit).all()

        return [
            {
                "id": a.id,
                "actor": a.actor,
                "action": a.action,
                "target": a.target,
                "details": a.details,
                "ip_address": a.ip_address,
                "timestamp": a.timestamp.isoformat() if a.timestamp else None,
            }
            for a in activities
        ]


def get_dashboard_stats(days=30):
    """Legacy alias for get_dashboard_summary."""
    return AnalyticsService.get_dashboard_summary(days=days)


def get_recent_activity(limit=50):
    """Legacy alias for get_recent_activities."""
    return AnalyticsService.get_recent_activities(limit=limit)


def get_student_analytics(school_name=None, days=30):
    """Get student-specific analytics."""
    from models import Student
    from sqlalchemy import func

    since = datetime.now(timezone.utc) - timedelta(days=days)
    query = db.session.query(
        func.count(Student.id).label("total"),
        func.count(func.nullif(Student.photo_url, "")).label("with_photos"),
        func.count(func.nullif(Student.pdf_url, "")).label("with_pdfs"),
    )
    if school_name:
        query = query.filter(Student.school_name == school_name)

    result = query.first()

    # School distribution
    school_query = db.session.query(
        Student.school_name.label("name"),
        func.count(Student.id).label("count"),
    )
    if school_name:
        school_query = school_query.filter(Student.school_name == school_name)
    school_rows = school_query.group_by(Student.school_name).order_by(func.count(Student.id).desc()).limit(10).all()
    schools = [{"name": r.name, "count": r.count} for r in school_rows]

    # Daily registrations
    daily_query = db.session.query(
        func.date(Student.created_at).label("date"),
        func.count(Student.id).label("count"),
    ).filter(
        Student.created_at >= since
    )
    if school_name:
        daily_query = daily_query.filter(Student.school_name == school_name)
    daily_rows = daily_query.group_by(func.date(Student.created_at)).order_by("date").all()
    daily = [{"date": str(r.date), "count": r.count} for r in daily_rows]

    return {
        "total_students": result.total if result else 0,
        "with_photos": result.with_photos if result else 0,
        "with_pdfs": result.with_pdfs if result else 0,
        "period_days": days,
        "schools": schools,
        "daily": daily,
    }


def get_bulk_job_analytics(days=30):
    """Legacy alias for get_bulk_job_stats."""
    return AnalyticsService.get_bulk_job_stats(days=days)


def get_verification_stats(days=30):
    """Get verification audit statistics."""
    from models import VerificationAudit
    since = datetime.now(timezone.utc) - timedelta(days=days)

    total = VerificationAudit.query.filter(VerificationAudit.created_at >= since).count()
    by_status = db.session.query(
        VerificationAudit.status,
        func.count(VerificationAudit.id),
    ).filter(
        VerificationAudit.created_at >= since
    ).group_by(VerificationAudit.status).all()

    daily_results = db.session.query(
        func.date(VerificationAudit.created_at).label('date'),
        func.count(VerificationAudit.id).label('count'),
    ).filter(
        VerificationAudit.created_at >= since
    ).group_by(
        func.date(VerificationAudit.created_at)
    ).order_by(
        func.date(VerificationAudit.created_at)
    ).all()

    return {
        "total_verifications": total,
        "by_status": {status: count for status, count in by_status},
        "daily": [{"date": str(row.date), "count": row.count} for row in daily_results],
        "period_days": days,
    }


def get_template_usage_stats(days=30):
    """Get template usage statistics."""
    from models import Template, Student
    since = datetime.now(timezone.utc) - timedelta(days=days)

    results = db.session.query(
        Template.school_name,
        func.count(Student.id).label("student_count"),
    ).join(
        Student, Student.template_id == Template.id
    ).filter(
        Student.created_at >= since
    ).group_by(
        Template.school_name
    ).order_by(
        func.count(Student.id).desc()
    ).limit(20).all()

    return {
        "schools": [
            {"school": row.school_name, "students": row.student_count}
            for row in results
        ],
        "period_days": days,
    }


def get_system_health_snapshot():
    """Get a comprehensive system health snapshot."""
    perf = AnalyticsService.get_performance_metrics()
    return {
        "performance": perf,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
