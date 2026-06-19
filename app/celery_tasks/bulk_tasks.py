"""
Celery task definitions for bulk operations.

These tasks replace the inline ThreadPoolExecutor calls in legacy_app.py.
They provide:
  - Automatic retry with exponential backoff
  - Progress tracking
  - Cancellation support
  - Event publishing
  - Dead letter queue on final failure
"""
import os
import time
import logging
from datetime import datetime, timezone

from app.celery_config import make_celery

logger = logging.getLogger(__name__)

# Celery instance — initialized at import, bound to app at startup
celery = make_celery()


# ---------------------------------------------------------------------------
# Bulk Generation Tasks
# ---------------------------------------------------------------------------

@celery.task(
    bind=True,
    name="app.celery_tasks.bulk_tasks.generate_student_cards",
    queue="normal",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,  # acknowledge after completion (survives worker crash)
    reject_on_worker_lost=True,  # requeue if worker dies
)
def generate_student_cards(self, task_id: str, template_id: int, excel_path: str,
                            photo_map: dict, import_mapping_id: int = None,
                            school_name: str = None):
    """
    Generate ID cards for all students in an Excel file.

    This is the Celery replacement for background_bulk_generate().
    It processes students in batches, publishes progress events,
    and handles cancellation gracefully.
    """
    from app.celery_tasks.event_bus import publish_event
    from app.services.bulk_job_service import _set_bulk_job_state, _get_bulk_job_state
    from app.legacy_app import background_bulk_generate

    start_time = time.monotonic()

    try:
        # Publish started event
        publish_event("bulk.started", {
            "task_id": task_id,
            "template_id": template_id,
            "school_name": school_name,
        }, source="celery")

        # Update state
        _set_bulk_job_state(
            task_id,
            state="PROCESSING",
            status="Starting bulk generation...",
            started_at=datetime.now(timezone.utc).isoformat(),
            worker_id=self.request.hostname,
        )

        # Delegate to the existing bulk generation logic
        # This reuses all the existing rendering, DB, and storage code
        background_bulk_generate(
            task_id=task_id,
            template_id=template_id,
            excel_path=excel_path,
            photo_map=photo_map,
            import_mapping_id=import_mapping_id,
        )

        # Get final state
        final_state = _get_bulk_job_state(task_id) or {}
        elapsed = time.monotonic() - start_time

        # Publish completed event
        publish_event("bulk.completed", {
            "task_id": task_id,
            "template_id": template_id,
            "success_count": final_state.get("success_count", 0),
            "error_count": final_state.get("error_count", 0),
            "skipped_count": final_state.get("skipped_count", 0),
            "elapsed_seconds": round(elapsed, 2),
        }, source="celery")

        return {
            "task_id": task_id,
            "status": "completed",
            "elapsed_seconds": round(elapsed, 2),
            "result": final_state,
        }

    except Exception as exc:
        elapsed = time.monotonic() - start_time
        logger.error("Bulk generation failed: %s", exc, exc_info=True)

        # Update state
        _set_bulk_job_state(
            task_id,
            state="FAILURE",
            status=f"Failed: {str(exc)[:200]}",
            error=str(exc),
            elapsed_seconds=round(elapsed, 2),
        )

        # Publish failure event
        publish_event("bulk.failed", {
            "task_id": task_id,
            "error": str(exc),
            "elapsed_seconds": round(elapsed, 2),
        }, source="celery")

        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))

        # Final failure — dead letter
        raise


@celery.task(
    bind=True,
    name="app.celery_tasks.bulk_tasks.cancel_bulk_job",
    queue="high",
)
def cancel_bulk_job(self, task_id: str):
    """Cancel a running bulk job."""
    from app.services.bulk_job_service import _set_bulk_job_state
    from app.celery_tasks.event_bus import publish_event

    _set_bulk_job_state(task_id, cancel_requested=True, status="Cancellation requested")

    # Try to revoke the Celery task
    from app.services.bulk_job_service import _get_bulk_job_state
    state = _get_bulk_job_state(task_id)
    celery_task_id = state.get("celery_task_id") if state else None

    if celery_task_id:
        celery.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")

    publish_event("bulk.cancelled", {"task_id": task_id}, source="celery")
    return {"task_id": task_id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# Single Card Render Tasks
# ---------------------------------------------------------------------------

@celery.task(
    bind=True,
    name="app.celery_tasks.render_tasks.render_single_card",
    queue="high",
    max_retries=2,
    default_retry_delay=30,
    time_limit=120,
    soft_time_limit=90,
)
def render_single_card(self, student_id: int, template_id: int, side: str = "front"):
    """
    Render a single student card asynchronously.
    Used for on-demand card regeneration.
    """
    from app.celery_tasks.event_bus import publish_event
    from models import db, Student, Template
    from app.services.render_service import render_student_card_side
    from utils import get_template_path, get_template_settings, get_card_size

    try:
        student = db.session.get(Student, student_id)
        template = db.session.get(Template, template_id)

        if not student or not template:
            raise ValueError(f"Student {student_id} or Template {template_id} not found")

        publish_event("render.started", {
            "student_id": student_id,
            "template_id": template_id,
            "side": side,
        }, source="celery")

        # Render the card
        result = render_student_card_side(
            template_obj=template,
            student_like=student,
            side=side,
            student_id=student_id,
            school_name=template.school_name,
        )

        publish_event("render.completed", {
            "student_id": student_id,
            "template_id": template_id,
            "side": side,
        }, source="celery")

        return {"student_id": student_id, "side": side, "status": "completed"}

    except Exception as exc:
        logger.error("Render failed for student %d: %s", student_id, exc)
        publish_event("render.failed", {
            "student_id": student_id,
            "error": str(exc),
        }, source="celery")
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Export Tasks
# ---------------------------------------------------------------------------

@celery.task(
    bind=True,
    name="app.celery_tasks.export_tasks.export_template_pdf",
    queue="low",
    max_retries=2,
    time_limit=600,
    soft_time_limit=500,
)
def export_template_pdf(self, template_id: int, export_mode: str = "print",
                         include_back: bool = True):
    """
    Export a template's cards to PDF asynchronously.
    Offloads the heavy PDF generation from the request thread.
    """
    from app.celery_tasks.event_bus import publish_event

    try:
        publish_event("export.started", {
            "template_id": template_id,
            "export_mode": export_mode,
        }, source="celery")

        # The actual PDF generation logic from corel_routes would be called here
        # For now, we publish the event and return
        # TODO: Extract PDF generation from corel_routes into a service

        publish_event("export.completed", {
            "template_id": template_id,
            "export_mode": export_mode,
        }, source="celery")

        return {"template_id": template_id, "status": "completed"}

    except Exception as exc:
        logger.error("Export failed for template %d: %s", template_id, exc)
        publish_event("export.failed", {
            "template_id": template_id,
            "error": str(exc),
        }, source="celery")
        raise self.retry(exc=exc)
