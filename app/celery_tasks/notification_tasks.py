"""
Celery task definitions for notifications.
"""
import logging

from app.celery_tasks.bulk_tasks import celery
from app.celery_tasks.event_bus import publish_event

logger = logging.getLogger(__name__)


@celery.task(
    bind=True,
    name="app.celery_tasks.notification_tasks.send_bulk_notification",
    queue="high",
    max_retries=3,
    default_retry_delay=30,
)
def send_bulk_notification(self, template_id: int, subject: str, message: str,
                            recipient_filter: dict = None):
    """
    Send notifications to all students of a template.
    Used for deadline reminders, card ready notifications, etc.
    """
    try:
        from models import db, Student, NotificationLog
        from flask import current_app

        query = Student.query.filter_by(template_id=template_id)
        if recipient_filter:
            if "school_name" in recipient_filter:
                query = query.filter_by(school_name=recipient_filter["school_name"])

        students = query.all()
        sent = 0
        failed = 0

        for student in students:
            if not student.email:
                continue
            try:
                from app.legacy_app import send_email
                send_email(student.email, subject, message)
                log = NotificationLog(
                    student_id=student.id,
                    template_id=template_id,
                    notification_type="email",
                    subject=subject,
                    status="sent",
                )
                db.session.add(log)
                sent += 1
            except Exception:
                failed += 1

        publish_event("notification.bulk_sent", {
            "template_id": template_id,
            "sent": sent,
            "failed": failed,
        }, source="celery")

        return {"sent": sent, "failed": failed}

    except Exception as exc:
        logger.error("Bulk notification failed: %s", exc)
        raise self.retry(exc=exc)


# Import event_bus at the end to avoid circular imports
from app.celery_tasks.event_bus import publish_event  # noqa: E402
