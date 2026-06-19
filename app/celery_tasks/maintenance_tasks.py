"""
Celery task definitions for maintenance and periodic tasks.
"""
import logging
from datetime import datetime, timezone, timedelta

from app.celery_tasks.bulk_tasks import celery
from app.celery_tasks.event_bus import publish_event

logger = logging.getLogger(__name__)


@celery.task(name="app.celery_tasks.maintenance_tasks.cleanup_expired_sessions", queue="low")
def cleanup_expired_sessions():
    """Remove expired user sessions from the database."""
    try:
        from models import db, UserSession
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        expired = UserSession.query.filter(UserSession.last_active < cutoff).count()
        UserSession.query.filter(UserSession.last_active < cutoff).delete()
        db.session.commit()
        logger.info("Cleaned up %d expired sessions", expired)
        publish_event("maintenance.sessions_cleaned", {"count": expired}, source="celery")
        return {"cleaned": expired}
    except Exception as exc:
        logger.error("Session cleanup failed: %s", exc)
        raise


@celery.task(name="app.celery_tasks.maintenance_tasks.collect_system_metrics", queue="low")
def collect_system_metrics():
    """Collect and store system health metrics."""
    try:
        from app.services.monitoring_service import collect_system_metrics as collect
        metrics = collect()
        publish_event("maintenance.metrics_collected", metrics, source="celery")
        return metrics
    except Exception as exc:
        logger.error("Metrics collection failed: %s", exc)
        raise


@celery.task(name="app.celery_tasks.maintenance_tasks.process_dead_letter_queue", queue="low")
def process_dead_letter_queue():
    """
    Process tasks in the dead letter queue.
    Attempts retry for transient failures, logs permanent failures.
    """
    try:
        from app.services.redis_service import get_redis_client
        client = get_redis_client()
        if not client:
            return {"processed": 0, "reason": "redis unavailable"}

        # Get dead letter queue items
        dead_items = client.lrange("dead_letter_queue", 0, 99)
        processed = 0
        permanent_failures = []

        for item in dead_items:
            try:
                import json
                task_data = json.loads(item)
                retries = task_data.get("_dlq_retries", 0)

                if retries < 3:
                    # Re-queue with incremented retry count
                    task_data["_dlq_retries"] = retries + 1
                    client.rpush("dead_letter_queue", json.dumps(task_data))
                else:
                    permanent_failures.append(task_data)
                    logger.error(
                        "permanent_task_failure",
                        extra={"task": task_data.get("name", "unknown"), "retries": retries},
                    )
                processed += 1
            except Exception:
                pass

        # Remove processed items
        if dead_items:
            client.ltrim("dead_letter_queue", len(dead_items), -1)

        result = {"processed": processed, "permanent_failures": len(permanent_failures)}
        publish_event("maintenance.dead_letter_processed", result, source="celery")
        return result

    except Exception as exc:
        logger.error("Dead letter processing failed: %s", exc)
        raise


@celery.task(name="app.celery_tasks.maintenance_tasks.archive_old_data", queue="low")
def archive_old_data(days_old: int = 90):
    """Archive data older than the specified number of days."""
    try:
        from app.services.archive_service import archive_old_students, archive_old_activities
        students_archived = archive_old_students(days_old=days_old)
        activities_archived = archive_old_activities(days_old=days_old)
        result = {
            "students_archived": students_archived,
            "activities_archived": activities_archived,
            "cutoff_days": days_old,
        }
        publish_event("maintenance.data_archived", result, source="celery")
        return result
    except Exception as exc:
        logger.error("Data archival failed: %s", exc)
        raise
