import os
import json
import logging
from datetime import datetime, timezone
from app.services.redis_service import _redis_get, _redis_set, _redis_cache_key

logger = logging.getLogger(__name__)

jobs = {}

def _set_bulk_job_state(task_id, **updates):
    task = jobs.setdefault(task_id, {"task_id": task_id})
    task.update(updates)
    try:
        _redis_set(
            _redis_cache_key("bulk_job", task_id),
            json.dumps(task, default=str).encode("utf-8"),
            ttl=86400,
        )
    except Exception as exc:
        logger.warning("Failed to publish bulk job state for %s: %s", task_id, exc)

    try:
        os.makedirs("instance", exist_ok=True)
        filepath = os.path.join("instance", "bulk_jobs.json")
        disk_jobs = {}
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    disk_jobs = json.load(f)
            except Exception:
                pass
        disk_jobs[task_id] = task
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(disk_jobs, f, default=str)
    except Exception as e:
        logger.warning(f"Failed to persist bulk job state to JSON file: {e}")

def _get_bulk_job_state(task_id):
    cached = _redis_get(_redis_cache_key("bulk_job", task_id))
    if cached:
        try:
            if isinstance(cached, bytes):
                cached = cached.decode("utf-8")
            return json.loads(cached)
        except Exception as exc:
            logger.warning("Failed to decode cached bulk job state for %s: %s", task_id, exc)

    try:
        filepath = os.path.join("instance", "bulk_jobs.json")
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                disk_jobs = json.load(f)
                if task_id in disk_jobs:
                    jobs[task_id] = disk_jobs[task_id]
                    return disk_jobs[task_id]
    except Exception as e:
        logger.warning(f"Failed to read bulk job state from JSON file: {e}")

    return jobs.get(task_id)

def _list_bulk_job_states(limit=100):
    aggregated = {}
    try:
        filepath = os.path.join("instance", "bulk_jobs.json")
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                disk_jobs = json.load(f) or {}
                if isinstance(disk_jobs, dict):
                    aggregated.update(disk_jobs)
    except Exception as e:
        logger.warning(f"Failed to read bulk job list from JSON file: {e}")

    try:
        aggregated.update(jobs or {})
    except Exception:
        pass

    rows = []
    for task_id, payload in aggregated.items():
        if not isinstance(payload, dict):
            continue
        row = dict(payload)
        row.setdefault("task_id", task_id)
        rows.append(row)

    def _sort_key(item):
        for k in ("updated_at", "started_at", "created_at"):
            v = item.get(k)
            if isinstance(v, str) and v:
                return v
        return ""

    rows.sort(key=_sort_key, reverse=True)
    return rows[: max(1, int(limit or 100))]

def _publish_bulk_job_errors(task_id, errors):
    _set_bulk_job_state(
        task_id,
        errors=list(errors[:10]),
        error_count=len(errors),
        first_error=(errors[0] if errors else None),
    )

def _format_bulk_generation_error(exc):
    if exc is None:
        return "Unknown bulk generation error"
    if isinstance(exc, KeyError):
        missing = str(exc).strip("'\" ")
        return f"Excel column missing: '{missing}'"
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    lowered = message.lower()
    if "name 'template' is not defined" in lowered or "name 'side' is not defined" in lowered:
        return "Bulk generation worker hit an internal layout error. Please retry after updating the server."
    if "cannot identify image file" in lowered:
        return "One of the uploaded photos is not a valid image file."
    if "template not found" in lowered:
        return "Selected template was not found."
    if "failed to load front template" in lowered:
        return message
    return message