"""
WebSocket server for real-time notifications.
Handles live bulk job progress, system alerts, and user notifications.
"""
import json
import logging
import functools
from flask import request, session
from flask_socketio import SocketIO, emit, join_room, leave_room

logger = logging.getLogger(__name__)
socketio = SocketIO()


def init_websocket(app):
    """Initialize WebSocket with the Flask app."""
    socketio.init_app(
        app,
        cors_allowed_origins="*",
        async_mode="threading",
        logger=False,
        engineio_logger=False,
        ping_interval=25,
        ping_timeout=60,
        max_http_buffer_size=10 * 1024 * 1024,  # 10MB
    )
    return socketio


def authenticated_only(f):
    """Decorator: require authenticated WebSocket connection."""
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id") and not session.get("student_email"):
            emit("error", {"message": "Authentication required"})
            return
        return f(*args, **kwargs)
    return wrapped


@socketio.on("connect")
def handle_connect():
    """Handle new WebSocket connection."""
    if session.get("admin_id"):
        room = f"admin_{session['admin_id']}"
        join_room(room)
        logger.info("Admin %s connected via WebSocket", session["admin_id"])
        emit("connected", {"status": "connected", "room": room})
    elif session.get("student_email"):
        room = f"student_{session['student_email']}"
        join_room(room)
        logger.info("Student %s connected via WebSocket", session["student_email"])
        emit("connected", {"status": "connected", "room": room})
    else:
        emit("connected", {"status": "connected", "room": "public"})


@socketio.on("disconnect")
def handle_disconnect():
    """Handle WebSocket disconnection."""
    logger.info("Client disconnected")


@socketio.on("subscribe_bulk_job")
@authenticated_only
def handle_subscribe_bulk_job(data):
    """Subscribe to real-time updates for a specific bulk job."""
    job_id = data.get("job_id")
    if job_id:
        room = f"bulk_job_{job_id}"
        join_room(room)
        emit("subscribed", {"job_id": job_id, "room": room})

        # Send current status from Redis if available
        try:
            from app.services.redis_service import get_redis_client
            redis = get_redis_client()
            if redis:
                status = redis.get(f"bulk_status:{job_id}")
                if status:
                    emit("bulk_progress", json.loads(status))
        except Exception as e:
            logger.warning("Failed to fetch bulk status: %s", e)


@socketio.on("unsubscribe_bulk_job")
def handle_unsubscribe_bulk_job(data):
    """Unsubscribe from bulk job updates."""
    job_id = data.get("job_id")
    if job_id:
        room = f"bulk_job_{job_id}"
        leave_room(room)
        emit("unsubscribed", {"job_id": job_id})


def notify_bulk_progress(job_id, data):
    """Send bulk job progress update to all subscribers."""
    try:
        socketio.emit("bulk_progress", data, room=f"bulk_job_{job_id}")
    except Exception as e:
        logger.error("Failed to emit bulk progress: %s", e)


def notify_user(user_id, event, data):
    """Send notification to a specific user."""
    try:
        socketio.emit(event, data, room=f"admin_{user_id}")
    except Exception as e:
        logger.error("Failed to notify user %s: %s", user_id, e)


def broadcast_system_alert(message, level="info"):
    """Broadcast a system-wide alert to all connected clients."""
    try:
        socketio.emit("system_alert", {
            "message": message,
            "level": level,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.error("Failed to broadcast alert: %s", e)


# Import at bottom to avoid circular imports
from datetime import datetime, timezone
