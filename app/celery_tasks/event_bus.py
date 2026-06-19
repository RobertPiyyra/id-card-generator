"""
Event Bus — publish/subscribe system for decoupled communication.

All state changes are published as events. Consumers (webhooks, analytics,
notifications, audit logs) subscribe to events they care about.

Event flow:
  Task/Service → publish_event() → Event Bus → Subscribers
                                              → Webhook deliveries
                                              → Analytics updates
                                              → Notification triggers
                                              → Audit log entries
"""
import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# In-memory subscriber registry
_subscribers: Dict[str, List[Callable]] = {}
_event_log: List[dict] = []
_EVENT_LOG_MAX = 10000


def subscribe(event_type: str, handler: Callable):
    """
    Register a handler for an event type.

    Usage:
        @subscribe("student.created")
        def on_student_created(event_data):
            send_welcome_email(event_data['student_id'])
    """
    if event_type not in _subscribers:
        _subscribers[event_type] = []
    _subscribers[event_type].append(handler)
    logger.debug("event_bus: subscribed %s → %s", event_type, handler.__name__)


def unsubscribe(event_type: str, handler: Callable):
    """Remove a handler."""
    if event_type in _subscribers:
        _subscribers[event_type] = [h for h in _subscribers[event_type] if h != handler]


def publish_event(event_type: str, data: dict, source: str = "system"):
    """
    Publish an event to all subscribers.

    Usage:
        publish_event("student.created", {"student_id": 123, "name": "John"})
        publish_event("bulk.completed", {"task_id": "abc", "count": 500})
        publish_event("template.updated", {"template_id": 5, "changes": ["font"]})
    """
    event = {
        "id": hashlib.sha256(
            f"{event_type}:{datetime.now(timezone.utc).isoformat()}:{json.dumps(data, default=str)}".encode()
        ).hexdigest()[:16],
        "type": event_type,
        "data": data,
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Store in event log
    _event_log.append(event)
    if len(_event_log) > _EVENT_LOG_MAX:
        _event_log.pop(0)

    # Notify synchronous subscribers
    handlers = _subscribers.get(event_type, []) + _subscribers.get("*", [])
    for handler in handlers:
        try:
            handler(event)
        except Exception as exc:
            logger.error(
                "event_bus: handler %s failed for %s: %s",
                handler.__name__, event_type, exc,
            )

    # Publish to Redis for cross-process subscribers
    try:
        from app.services.redis_service import _redis_set, get_redis_client
        client = get_redis_client()
        if client:
            client.publish(f"events:{event_type}", json.dumps(event, default=str))
            client.publish("events:*", json.dumps(event, default=str))
    except Exception:
        pass

    logger.info("event_bus: published %s", event_type)
    return event


def get_events(event_type: str = None, limit: int = 100, since: str = None):
    """Query recent events from the in-memory log."""
    events = _event_log
    if event_type and event_type != "*":
        events = [e for e in events if e["type"] == event_type]
    if since:
        events = [e for e in events if e["timestamp"] >= since]
    return events[-limit:]


def get_event_types():
    """Return all registered event types."""
    types = set()
    for event in _event_log:
        types.add(event["type"])
    return sorted(types)


# ---------------------------------------------------------------------------
# Built-in event handlers
# ---------------------------------------------------------------------------

def _audit_log_handler(event):
    """Write all events to the ActivityLog table."""
    try:
        from models import db, ActivityLog
        log = ActivityLog(
            actor=event.get("source", "system"),
            action=event["type"],
            target=str(event.get("data", ""))[:500],
            details=json.dumps(event, default=str)[:2000],
            timestamp=datetime.now(timezone.utc),
        )
        db.session.add(log)
        db.session.commit()
    except Exception as exc:
        logger.error("audit_log_handler failed: %s", exc)


def _webhook_handler(event):
    """Forward events to registered webhook endpoints."""
    try:
        from app.services.webhook_service import dispatch_webhook_event
        dispatch_webhook_event(event["type"], event["data"])
    except Exception as exc:
        logger.error("webhook_handler failed: %s", exc)


# Register built-in handlers
subscribe("*", _audit_log_handler)
subscribe("*", _webhook_handler)
