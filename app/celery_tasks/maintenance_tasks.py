"""
Celery tasks for system maintenance.
"""
import logging
import os
import glob
from datetime import datetime, timezone, timedelta

from app.celery_config import make_celery

logger = logging.getLogger(__name__)
celery = make_celery()


@celery.task(name="app.celery_tasks.maintenance_tasks.cleanup_expired_sessions")
def cleanup_expired_sessions():
    """Remove expired user sessions from the database."""
    from models import UserSession

    now = datetime.now(timezone.utc)
    expired = UserSession.query.filter(
        UserSession.expires_at < now,
        UserSession.is_active == True,
    ).all()

    count = 0
    for session in expired:
        session.is_active = False
        count += 1

    from app.extensions import db
    db.session.commit()
    logger.info("Cleaned up %d expired sessions", count)
    return {"cleaned": count}


@celery.task(name="app.celery_tasks.maintenance_tasks.cleanup_old_bulk_jobs")
def cleanup_old_bulk_jobs(days=30):
    """Archive and cleanup bulk jobs older than N days."""
    from models import BulkJob, BulkJobItem

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old_jobs = BulkJob.query.filter(
        BulkJob.created_at < cutoff,
        BulkJob.status.in_(["completed", "failed", "cancelled"]),
    ).all()

    count = 0
    for job in old_jobs:
        try:
            # Delete items first
            BulkJobItem.query.filter_by(bulk_job_id=job.id).delete()
            db.session.delete(job)
            count += 1
        except Exception as e:
            logger.error("Error cleaning job %s: %s", job.id, e)

    from app.extensions import db
    db.session.commit()
    logger.info("Cleaned up %d old bulk jobs", count)
    return {"cleaned": count}


@celery.task(name="app.celery_tasks.maintenance_tasks.cleanup_temp_files")
def cleanup_temp_files(max_age_hours=24):
    """Remove temporary generated files older than N hours."""
    import shutil

    temp_dirs = [
        os.path.join("static", "generated"),
        os.path.join("static", "Uploads"),
    ]

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    cleaned = 0

    for temp_dir in temp_dirs:
        if not os.path.exists(temp_dir):
            continue
        for item in glob.glob(os.path.join(temp_dir, "*")):
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(item), tz=timezone.utc)
                if mtime < cutoff:
                    if os.path.isfile(item):
                        os.remove(item)
                    elif os.path.isdir(item):
                        shutil.rmtree(item)
                    cleaned += 1
            except Exception as e:
                logger.warning("Failed to cleanup %s: %s", item, e)

    logger.info("Cleaned up %d temp files", cleaned)
    return {"cleaned": cleaned}


@celery.task(name="app.celery_tasks.maintenance_tasks.optimize_database")
def optimize_database():
    """Run database maintenance (VACUUM for SQLite, ANALYZE for PostgreSQL)."""
    from app.extensions import db

    try:
        dialect = db.engine.dialect.name
        if dialect == "sqlite":
            db.session.execute(db.text("VACUUM"))
        elif dialect == "postgresql":
            db.session.execute(db.text("ANALYZE"))
        db.session.commit()
        logger.info("Database optimization complete (dialect: %s)", dialect)
        return {"status": "optimized", "dialect": dialect}
    except Exception as e:
        logger.error("Database optimization failed: %s", e)
        return {"status": "error", "error": str(e)}
