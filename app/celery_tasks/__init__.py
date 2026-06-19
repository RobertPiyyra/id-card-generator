"""
Celery tasks package.
"""
from app.celery_tasks.bulk_tasks import (
    celery,
    generate_student_cards,
    cancel_bulk_job,
    render_single_card,
    export_template_pdf,
)
from app.celery_tasks.event_bus import publish_event, subscribe, get_events
from app.celery_tasks.maintenance_tasks import (
    cleanup_expired_sessions,
    collect_system_metrics,
    process_dead_letter_queue,
    archive_old_data,
)
from app.celery_tasks.notification_tasks import send_bulk_notification

__all__ = [
    "celery",
    "generate_student_cards",
    "cancel_bulk_job",
    "render_single_card",
    "export_template_pdf",
    "publish_event",
    "subscribe",
    "get_events",
    "cleanup_expired_sessions",
    "collect_system_metrics",
    "process_dead_letter_queue",
    "archive_old_data",
    "send_bulk_notification",
]
