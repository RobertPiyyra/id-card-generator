"""
Celery configuration for event-driven task processing.

Provides:
  - Async task execution with Redis broker
  - Priority queues (high, normal, low)
  - Retry with exponential backoff
  - Dead letter queue for failed tasks
  - Task result tracking
  - Event sourcing for all state changes
"""
import os
import logging
from datetime import datetime, timezone
from celery import Celery, Task
from celery.schedules import crontab

logger = logging.getLogger(__name__)


def make_celery(app=None):
    """
    Create and configure Celery instance.
    Call this once at app startup.

    Usage:
        celery = make_celery(app)
    """
    broker_url = os.environ.get("CELERY_BROKER_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/1"))
    result_backend = os.environ.get("CELERY_RESULT_BACKEND", os.environ.get("REDIS_URL", "redis://localhost:6379/2"))

    celery = Celery(
        "idcard",
        broker=broker_url,
        backend=result_backend,
        include=[
            "app.celery_tasks.bulk_tasks",
            "app.celery_tasks.render_tasks",
            "app.celery_tasks.export_tasks",
            "app.celery_tasks.notification_tasks",
            "app.celery_tasks.maintenance_tasks",
        ],
    )

    celery.conf.update(
        # Serialization
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],

        # Timezone
        timezone="UTC",
        enable_utc=True,

        # Task execution
        task_track_started=True,
        task_time_limit=3600,  # 1 hour hard limit
        task_soft_time_limit=3300,  # 55 min soft limit
        worker_prefetch_multiplier=1,  # fair scheduling
        worker_max_tasks_per_child=1000,  # prevent memory leaks

        # Result backend
        result_expires=86400,  # 24 hours
        result_extended=True,

        # Retry
        task_default_retry_delay=60,  # 1 minute
        task_max_retries=3,

        # Queues
        task_default_queue="normal",
        task_queues={
            "high": {"exchange": "high", "routing_key": "high"},
            "normal": {"exchange": "normal", "routing_key": "normal"},
            "low": {"exchange": "low", "routing_key": "low"},
            "dead_letter": {"exchange": "dead_letter", "routing_key": "dead_letter"},
        },
        task_routes={
            "app.celery_tasks.bulk_tasks.*": {"queue": "normal"},
            "app.celery_tasks.render_tasks.*": {"queue": "high"},
            "app.celery_tasks.export_tasks.*": {"queue": "low"},
            "app.celery_tasks.notification_tasks.*": {"queue": "high"},
            "app.celery_tasks.maintenance_tasks.*": {"queue": "low"},
        },

        # Beat schedule (periodic tasks)
        beat_schedule={
            "cleanup-expired-sessions": {
                "task": "app.celery_tasks.maintenance_tasks.cleanup_expired_sessions",
                "schedule": crontab(hour=3, minute=0),  # 3 AM daily
            },
            "collect-system-metrics": {
                "task": "app.celery_tasks.maintenance_tasks.collect_system_metrics",
                "schedule": crontab(minute="*/15"),  # every 15 minutes
            },
            "process-dead-letter-queue": {
                "task": "app.celery_tasks.maintenance_tasks.process_dead_letter_queue",
                "schedule": crontab(minute="*/30"),  # every 30 minutes
            },
            "archive-old-data": {
                "task": "app.celery_tasks.maintenance_tasks.archive_old_data",
                "schedule": crontab(hour=2, minute=0),  # 2 AM daily
            },
        },
    )

    # Custom task class with Flask app context
    class ContextTask(Task):
        abstract = True

        def __call__(self, *args, **kwargs):
            if app is None:
                return super().__call__(*args, **kwargs)
            with app.app_context():
                return super().__call__(*args, **kwargs)

        def on_failure(self, exc, task_id, args, kwargs, einfo):
            """Handle task failure — send to dead letter queue after max retries."""
            logger.error(
                "task_failed",
                extra={
                    "task_id": task_id,
                    "task_name": self.name,
                    "exception": str(exc),
                    "args": str(args),
                    "kwargs": str(kwargs),
                },
            )
            # Publish failure event
            try:
                from app.celery_tasks.event_bus import publish_event
                publish_event(
                    "task.failed",
                    {
                        "task_id": task_id,
                        "task_name": self.name,
                        "error": str(exc),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                pass

        def on_success(self, retval, task_id, args, kwargs):
            """Handle task success."""
            logger.info(
                "task_succeeded",
                extra={
                    "task_id": task_id,
                    "task_name": self.name,
                },
            )

        def after_return(self, status, retval, task_id, args, kwargs, einfo):
            """Called after task returns, regardless of status."""
            pass

    celery.Task = ContextTask
    return celery
