"""
Print Queue Management Service.

Manages print job queue with priority scheduling, printer status monitoring,
print history tracking, and reprint management.
"""
import logging
import time
import threading
from datetime import datetime, timezone
from collections import deque

from models import db, PrintQueue, PrintHistory, Student, Template

logger = logging.getLogger(__name__)

# In-memory queue for fast access (backed by DB)
_queue_cache = {}
_queue_lock = threading.Lock()
_printer_status = {}


def add_print_job(template_id, student_id=None, admin_id=None, job_type='single',
                  priority=5, card_side='front', copies=1, printer_name=None):
    """Add a print job to the queue."""
    job = PrintQueue(
        template_id=template_id,
        student_id=student_id,
        admin_id=admin_id,
        job_type=job_type,
        priority=priority,
        card_side=card_side,
        copies=copies,
        printer_name=printer_name,
        status='pending',
    )
    db.session.add(job)
    db.session.commit()

    with _queue_lock:
        _queue_cache[job.id] = job

    logger.info(f"Print job {job.id} added: template={template_id}, student={student_id}, priority={priority}")
    return job.id


def add_batch_print_jobs(template_id, student_ids, admin_id=None, priority=5, card_side='front'):
    """Add multiple print jobs for a batch of students."""
    job_ids = []
    for student_id in student_ids:
        job_id = add_print_job(
            template_id=template_id,
            student_id=student_id,
            admin_id=admin_id,
            job_type='batch',
            priority=priority,
            card_side=card_side,
        )
        job_ids.append(job_id)
    return job_ids


def get_next_job(printer_name=None):
    """Get the next job to process (highest priority first, then FIFO)."""
    query = PrintQueue.query.filter_by(status='pending')
    if printer_name:
        query = query.filter_by(printer_name=printer_name)
    # Order by priority (ascending = highest first), then by created_at (FIFO)
    job = query.order_by(PrintQueue.priority.asc(), PrintQueue.created_at.asc()).first()
    return job


def start_job(job_id, printer_name=None):
    """Mark a job as processing."""
    job = db.session.get(PrintQueue, job_id)
    if job and job.status == 'pending':
        job.status = 'processing'
        job.started_at = datetime.now(timezone.utc)
        job.printer_name = printer_name or job.printer_name
        db.session.commit()
        logger.info(f"Print job {job_id} started on printer {job.printer_name}")
        return True
    return False


def complete_job(job_id, success=True, error_message=None):
    """Mark a job as completed or failed."""
    job = db.session.get(PrintQueue, job_id)
    if not job:
        return False

    job.status = 'completed' if success else 'failed'
    job.completed_at = datetime.now(timezone.utc)
    job.error_message = error_message

    # Record in history
    history = PrintHistory(
        print_queue_id=job.id,
        template_id=job.template_id,
        student_id=job.student_id,
        admin_id=job.admin_id,
        printer_name=job.printer_name,
        job_type=job.job_type,
        status=job.status,
        card_side=job.card_side,
        copies=job.copies,
        error_message=error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
    db.session.add(history)
    db.session.commit()

    with _queue_lock:
        _queue_cache.pop(job_id, None)

    logger.info(f"Print job {job_id} {'completed' if success else 'failed'}")
    return True


def cancel_job(job_id):
    """Cancel a pending or processing job."""
    job = db.session.get(PrintQueue, job_id)
    if job and job.status in ('pending', 'processing'):
        job.status = 'cancelled'
        job.completed_at = datetime.now(timezone.utc)
        db.session.commit()
        with _queue_lock:
            _queue_cache.pop(job_id, None)
        logger.info(f"Print job {job_id} cancelled")
        return True
    return False


def reprint_job(original_job_id, priority=3):
    """Create a reprint job from a completed/failed job."""
    original = db.session.get(PrintQueue, original_job_id)
    if not original:
        # Try history
        history = db.session.get(PrintHistory, original_job_id)
        if history:
            return add_print_job(
                template_id=history.template_id,
                student_id=history.student_id,
                admin_id=history.admin_id,
                job_type='reprint',
                priority=priority,
                card_side=history.card_side,
                copies=history.copies,
                printer_name=history.printer_name,
            )
        return None
    return add_print_job(
        template_id=original.template_id,
        student_id=original.student_id,
        admin_id=original.admin_id,
        job_type='reprint',
        priority=priority,
        card_side=original.card_side,
        copies=original.copies,
        printer_name=original.printer_name,
    )


def get_queue_status():
    """Get current queue statistics."""
    pending = PrintQueue.query.filter_by(status='pending').count()
    processing = PrintQueue.query.filter_by(status='processing').count()
    completed_today = PrintHistory.query.filter(
        PrintHistory.status == 'completed',
        PrintHistory.completed_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
    ).count()
    failed_today = PrintHistory.query.filter(
        PrintHistory.status == 'failed',
        PrintHistory.completed_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
    ).count()

    return {
        'pending': pending,
        'processing': processing,
        'completed_today': completed_today,
        'failed_today': failed_today,
        'total_in_queue': pending + processing,
    }


def get_queue_jobs(status=None, page=1, per_page=50):
    """Get queue jobs with optional filtering."""
    query = PrintQueue.query
    if status:
        query = query.filter_by(status=status)
    query = query.order_by(PrintQueue.priority.asc(), PrintQueue.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return pagination.items, pagination.total


def get_print_history(template_id=None, student_id=None, page=1, per_page=50):
    """Get print history with optional filtering."""
    query = PrintHistory.query
    if template_id:
        query = query.filter_by(template_id=template_id)
    if student_id:
        query = query.filter_by(student_id=student_id)
    query = query.order_by(PrintHistory.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return pagination.items, pagination.total


def update_printer_status(printer_name, status, message=None):
    """Update printer status (online, offline, error, busy)."""
    _printer_status[printer_name] = {
        'status': status,
        'message': message,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }


def get_printer_status(printer_name=None):
    """Get status of one or all printers."""
    if printer_name:
        return _printer_status.get(printer_name, {'status': 'unknown', 'message': 'No status reported'})
    return _printer_status


def cleanup_old_jobs(days=30):
    """Remove old completed/failed jobs from the queue table."""
    cutoff = datetime.now(timezone.utc) - __import__('datetime').timedelta(days=days)
    old_jobs = PrintQueue.query.filter(
        PrintQueue.status.in_(['completed', 'failed', 'cancelled']),
        PrintQueue.completed_at < cutoff,
    ).all()
    count = len(old_jobs)
    for job in old_jobs:
        db.session.delete(job)
    db.session.commit()
    logger.info(f"Cleaned up {count} old print jobs")
    return count
