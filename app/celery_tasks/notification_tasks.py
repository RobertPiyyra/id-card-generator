"""
Celery tasks for async notifications.
"""
import logging
from datetime import datetime, timezone, timedelta

from app.celery_config import make_celery

logger = logging.getLogger(__name__)
celery = make_celery()


@celery.task(name="app.celery_tasks.notification_tasks.send_bulk_notification")
def send_bulk_notification(notification_type, recipient_list, subject, body):
    """Send notifications to a list of recipients asynchronously."""
    from app.helpers import send_email

    results = {"sent": 0, "failed": 0, "errors": []}

    for recipient in recipient_list:
        try:
            success = send_email(recipient, subject, body)
            if success:
                results["sent"] += 1
            else:
                results["failed"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append(str(e)[:200])

    logger.info("Bulk notification complete: %s", results)
    return results


@celery.task(name="app.celery_tasks.notification_tasks.notify_approaching_deadlines")
def notify_approaching_deadlines():
    """Daily task: notify admins about deadlines approaching within 3 days."""
    from models import Template, ActivityLog
    from app.helpers import send_email, check_deadline_passed

    now = datetime.now(timezone.utc)
    three_days_from_now = now + timedelta(days=3)

    templates = Template.query.filter(
        Template.deadline.isnot(None),
        Template.deadline > now,
        Template.deadline <= three_days_from_now,
    ).all()

    notified = 0
    for template in templates:
        try:
            is_passed, deadline_str = check_deadline_passed(template.id)
            if not is_passed and deadline_str:
                # Log the notification
                log = ActivityLog(
                    actor="System",
                    action="Deadline Approaching Notification",
                    target=template.school_name,
                    details=f"Deadline: {deadline_str}",
                    ip_address="127.0.0.1",
                )
                db.session.add(log)
                notified += 1
        except Exception as e:
            logger.error("Error notifying deadline for template %s: %s", template.id, e)

    db.session.commit()
    logger.info("Deadline notifications: %d templates", notified)
    return {"notified": notified}
