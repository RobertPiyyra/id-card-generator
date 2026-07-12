import atexit
import logging

from app.extensions import scheduler

logger = logging.getLogger(__name__)


def configure_notification_scheduler(check_and_notify_approaching_deadlines):
    # ================== Initialize Notification Scheduler ==================
    if not scheduler.get_job("deadline_check_job"):
        scheduler.add_job(
            func=check_and_notify_approaching_deadlines,
            trigger="cron",
            hour="9",  # Run at 9 AM daily
            minute="0",
            id="deadline_check_job",
            name="Check and notify approaching deadlines"
        )

    try:
        if not scheduler.running:
            scheduler.start()
        logger.info("Background scheduler started for deadline notifications")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")

    # Shutdown scheduler when app exits
    def _shutdown_scheduler():
        if scheduler.running:
            # Prevent "I/O operation on closed file" warnings by silencing the apscheduler logger at exit
            logging.getLogger("apscheduler").setLevel(logging.CRITICAL)
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                pass

    atexit.register(_shutdown_scheduler)
    return scheduler

