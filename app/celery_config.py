"""
Celery configuration for async task processing.
Handles bulk card generation, image processing, and email notifications
without blocking the web server.
"""
import os
from celery import Celery
from celery.schedules import crontab


def make_celery(app=None):
    """Create and configure Celery instance."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    celery = Celery(
        "idcard",
        broker=redis_url,
        backend=redis_url,
        include=[
            "app.celery_tasks.bulk_tasks",
            "app.celery_tasks.notification_tasks",
            "app.celery_tasks.maintenance_tasks",
        ],
    )

    celery.conf.update(
        # Task execution
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,

        # Task routing — separate queues for different workloads
        task_routes={
            "app.celery_tasks.bulk_tasks.*": {"queue": "bulk"},
            "app.celery_tasks.notification_tasks.*": {"queue": "notifications"},
            "app.celery_tasks.maintenance_tasks.*": {"queue": "maintenance"},
        },

        # Result backend
        result_expires=3600 * 24 * 7,  # Keep results for 7 days
        result_extended=True,

        # Worker settings
        worker_prefetch_multiplier=1,  # One task at a time per worker (fair scheduling)
        worker_max_tasks_per_child=100,  # Restart worker after 100 tasks (prevent memory leaks)
        worker_concurrency=int(os.environ.get("CELERY_CONCURRENCY", "4")),

        # Task time limits
        task_time_limit=600,  # 10 minutes hard limit
        task_soft_time_limit=540,  # 9 minutes soft limit (raises SoftTimeLimitExceeded)

        # Rate limiting
        task_default_rate_limit="10/m",  # Default: 10 tasks per minute

        # Beat schedule for periodic tasks
        beat_schedule={
            "cleanup-expired-sessions": {
                "task": "app.celery_tasks.maintenance_tasks.cleanup_expired_sessions",
                "schedule": crontab(hour=3, minute=0),  # 3 AM daily
            },
            "cleanup-old-bulk-jobs": {
                "task": "app.celery_tasks.maintenance_tasks.cleanup_old_bulk_jobs",
                "schedule": crontab(hour=4, minute=0),  # 4 AM daily
            },
            "notify-approaching-deadlines": {
                "task": "app.celery_tasks.notification_tasks.notify_approaching_deadlines",
                "schedule": crontab(hour=9, minute=0),  # 9 AM daily
            },
        },

        # Retry policy
        task_default_retry_delay=60,  # 1 minute
        task_max_retries=3,
    )

    if app:
        celery.conf.update(app.config)

        class ContextTask(celery.Task):
            """Run tasks inside Flask app context."""
            def __call__(self, *args, **kwargs):
                with app.app_context():
                    return self.run(*args, **kwargs)

        celery.Task = ContextTask

    return celery
