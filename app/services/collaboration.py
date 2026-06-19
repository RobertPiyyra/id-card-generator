"""
Real-Time Collaboration Service.

Provides:
  - WebSocket-based live editing for the visual editor
  - Presence tracking (who's online, what they're editing)
  - Operational Transform for conflict-free concurrent edits
  - Live preview synchronization across browsers
  - Field/object locking to prevent edit conflicts
  - Cursor position broadcasting
  - Chat/comments within the editor

Usage:
    from app.services.collaboration import init_socketio, get_room_state
    socketio = init_socketio(app)
"""
import json
import time
import logging
import threading
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory state (use Redis for multi-worker deployments)
# ---------------------------------------------------------------------------

# Room state: template_id → { users, locks, operations }
_rooms: Dict[int, dict] = {}
_rooms_lock = threading.Lock()

# User sessions: sid → { user_id, template_id, cursor_pos }
_sessions: Dict[str, dict] = {}

# Operational Transform operation log per template
_op_log: Dict[int, list] = defaultdict(list)
_OP_LOG_MAX = 1000


# ---------------------------------------------------------------------------
# Room Management
# ---------------------------------------------------------------------------

def get_room_state(template_id: int) -> dict:
    """Get the current state of a collaboration room."""
    with _rooms_lock:
        room = _rooms.get(template_id)
        if not room:
            return {
                "template_id": template_id,
                "users": {},
                "locks": {},
                "user_count": 0,
            }
        return {
            "template_id": template_id,
            "users": dict(room["users"]),
            "locks": dict(room["locks"]),
            "user_count": len(room["users"]),
        }


def _get_or_create_room(template_id: int) -> dict:
    """Get or initialize a room."""
    with _rooms_lock:
        if template_id not in _rooms:
            _rooms[template_id] = {
                "users": {},
                "locks": {},
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        return _rooms[template_id]


# ---------------------------------------------------------------------------
# Presence Tracking
# ---------------------------------------------------------------------------

def join_room(sid: str, template_id: int, user_info: dict):
    """Register a user as present in a template's editing room."""
    room = _get_or_create_room(template_id)
    room["users"][sid] = {
        "user_id": user_info.get("user_id"),
        "username": user_info.get("username", "Anonymous"),
        "color": user_info.get("color", _assign_user_color(template_id)),
        "cursor_x": 0,
        "cursor_y": 0,
        "active_field": None,
        "joined_at": datetime.now(timezone.utc).isoformat(),
    }
    _sessions[sid] = {"template_id": template_id, "user_info": user_info}
    logger.info("collab: user %s joined template %d (%d users online)",
                user_info.get("username"), template_id, len(room["users"]))
    return room["users"][sid]


def leave_room(sid: str):
    """Remove a user from their current room."""
    session = _sessions.pop(sid, None)
    if not session:
        return None

    template_id = session["template_id"]
    with _rooms_lock:
        room = _rooms.get(template_id)
        if room:
            user_data = room["users"].pop(sid, None)
            # Release any locks held by this user
            locks_to_release = [
                field for field, locker in room["locks"].items()
                if locker == sid
            ]
            for field in locks_to_release:
                del room["locks"][field]

            # Clean up empty rooms
            if not room["users"]:
                _rooms.pop(template_id, None)

    logger.info("collab: user left template %d", template_id)
    return {"template_id": template_id, "user_data": user_data}


def update_cursor(sid: str, x: int, y: int, active_field: str = None):
    """Update a user's cursor position."""
    session = _sessions.get(sid)
    if not session:
        return

    template_id = session["template_id"]
    with _rooms_lock:
        room = _rooms.get(template_id)
        if room and sid in room["users"]:
            room["users"][sid]["cursor_x"] = x
            room["users"][sid]["cursor_y"] = y
            room["users"][sid]["active_field"] = active_field


def _assign_user_color(template_id: int) -> str:
    """Assign a unique color to a user in a room."""
    colors = [
        "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4",
        "#FFEAA7", "#DDA0DD", "#98D8C8", "#F7DC6F",
        "#BB8FCE", "#85C1E9", "#F0B27A", "#82E0AA",
    ]
    room = _get_or_create_room(template_id)
    used = {u.get("color") for u in room["users"].values()}
    for color in colors:
        if color not in used:
            return color
    return colors[len(room["users"]) % len(colors)]


# ---------------------------------------------------------------------------
# Field/Object Locking
# ---------------------------------------------------------------------------

def acquire_lock(template_id: int, field_id: str, sid: str) -> dict:
    """
    Try to lock a field for editing by a specific user.
    Returns {"success": bool, "locked_by": str|None}.
    """
    with _rooms_lock:
        room = _get_or_create_room(template_id)
        current_locker = room["locks"].get(field_id)

        if current_locker and current_locker != sid:
            locker_name = room["users"].get(current_locker, {}).get("username", "Unknown")
            return {"success": False, "locked_by": locker_name, "locker_sid": current_locker}

        room["locks"][field_id] = sid
        locker_name = room["users"].get(sid, {}).get("username", "Unknown")
        logger.debug("collab: %s locked field %s in template %d", locker_name, field_id, template_id)
        return {"success": True, "locked_by": locker_name}


def release_lock(template_id: int, field_id: str, sid: str) -> bool:
    """Release a lock on a field."""
    with _rooms_lock:
        room = _get_or_create_room(template_id)
        if room["locks"].get(field_id) == sid:
            del room["locks"][field_id]
            return True
        return False


def release_all_locks(template_id: int, sid: str):
    """Release all locks held by a user (called on disconnect)."""
    with _rooms_lock:
        room = _rooms.get(template_id)
        if room:
            to_release = [f for f, locker in room["locks"].items() if locker == sid]
            for field in to_release:
                del room["locks"][field]
            return len(to_release)
    return 0


# ---------------------------------------------------------------------------
# Operational Transform
# ---------------------------------------------------------------------------

def apply_operation(template_id: int, operation: dict, sid: str) -> dict:
    """
    Apply an editing operation with Operational Transform.

    Operation format:
        {"type": "move_field"|"resize_field"|"update_style"|"add_field"|"delete_field",
         "target": "field_id",
         "data": {...},
         "base_version": <int>}

    Returns the transformed operation that should be applied.
    """
    with _rooms_lock:
        room = _get_or_create_room(template_id)

        # Check lock
        target = operation.get("target")
        if target and room["locks"].get(target) and room["locks"][target] != sid:
            return {"success": False, "error": "Field is locked by another user"}

        # Transform against concurrent operations
        transformed = _transform_operation(template_id, operation)

        # Log the operation
        op_record = {
            "id": f"{template_id}:{_op_log_counter(template_id)}",
            "sid": sid,
            "operation": transformed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _op_log[template_id].append(op_record)
        if len(_op_log[template_id]) > _OP_LOG_MAX:
            _op_log[template_id] = _op_log[template_id][-_OP_LOG_MAX:]

        return {"success": True, "operation": transformed, "version": len(_op_log[template_id])}


def _op_log_counter(template_id: int) -> int:
    """Get the next operation sequence number."""
    return len(_op_log[template_id]) + 1


def _transform_operation(template_id: int, operation: dict) -> dict:
    """
    Transform an operation against all concurrent operations.
    This is a simplified OT — for production, use a full OT library.
    """
    concurrent = _op_log[template_id][-50:]  # last 50 ops

    transformed = dict(operation)

    for record in concurrent:
        other = record["operation"]
        if other.get("target") == operation.get("target"):
            # Same target — need to transform
            if other.get("type") == "move_field" and operation.get("type") == "move_field":
                # Last-write-wins for moves (simplified)
                pass
            elif other.get("type") == "delete_field" and operation.get("type") != "delete_field":
                # Field was deleted — reject
                transformed["_conflict"] = True
                transformed["_conflict_reason"] = "Field was deleted by another user"

    return transformed


def get_operation_history(template_id: int, since_version: int = 0) -> list:
    """Get operations since a specific version (for reconnection sync)."""
    ops = _op_log.get(template_id, [])
    if since_version > 0:
        ops = ops[since_version:]
    return ops[-100:]  # return last 100 ops max


# ---------------------------------------------------------------------------
# Live Preview Sync
# ---------------------------------------------------------------------------

def broadcast_preview_update(template_id: int, preview_data: dict, exclude_sid: str = None):
    """
    Broadcast a preview update to all users in a room.
    Called when a user makes a change that affects the card preview.
    """
    # This is called from the SocketIO event handlers
    # The actual emit happens in the socket handlers
    room = _get_or_create_room(template_id)
    recipients = [sid for sid in room["users"] if sid != exclude_sid]
    return recipients


# ---------------------------------------------------------------------------
# SocketIO Initialization
# ---------------------------------------------------------------------------

def init_socketio(app):
    """
    Initialize Flask-SocketIO with the Flask app.
    Returns the SocketIO instance.

    Usage:
        from app.services.collaboration import init_socketio
        socketio = init_socketio(app)
        socketio.run(app)
    """
    try:
        from flask_socketio import SocketIO, emit, join_room as sio_join_room, leave_room as sio_leave_room
    except ImportError:
        logger.warning("flask-socketio not installed — real-time collaboration disabled")
        return None

    socketio = SocketIO(
        app,
        cors_allowed_origins="*",
        async_mode="threading",
        logger=False,
        engineio_logger=False,
        ping_interval=25,
        ping_timeout=60,
    )

    # ---------------------------------------------------------------------------
    # Socket Event Handlers
    # ---------------------------------------------------------------------------

    @socketio.on("connect")
    def handle_connect():
        """Handle new WebSocket connection."""
        from flask import request
        logger.info("socketio: client connected (sid=%s)", request.sid)
        emit("connected", {"status": "ok", "sid": request.sid})

    @socketio.on("disconnect")
    def handle_disconnect():
        """Handle disconnection — clean up presence and locks."""
        from flask import request
        result = leave_room(request.sid)
        if result:
            # Notify other users
            emit("user_left", {
                "sid": request.sid,
                "template_id": result["template_id"],
            }, room=f"template_{result['template_id']}", namespace="/editor")
        logger.info("socketio: client disconnected (sid=%s)", request.sid)

    @socketio.on("join_editor")
    def handle_join_editor(data):
        """User joins a template editing session."""
        from flask import request

        template_id = data.get("template_id")
        user_info = data.get("user", {})

        if not template_id:
            emit("error", {"message": "template_id is required"})
            return

        sio_join_room(f"template_{template_id}")
        user_data = join_room(request.sid, int(template_id), user_info)

        # Notify all users in the room
        emit("user_joined", {
            "sid": request.sid,
            "user": user_data,
            "room": get_room_state(int(template_id)),
        }, room=f"template_{template_id}")

        # Send current room state to the joining user
        emit("room_state", get_room_state(int(template_id)))

    @socketio.on("leave_editor")
    def handle_leave_editor(data):
        """User leaves a template editing session."""
        from flask import request

        template_id = data.get("template_id")
        if template_id:
            release_all_locks(int(template_id), request.sid)
            sio_leave_room(f"template_{template_id}")

        result = leave_room(request.sid)
        if result:
            emit("user_left", {
                "sid": request.sid,
                "template_id": result["template_id"],
            }, room=f"template_{result['template_id']}")

    @socketio.on("cursor_move")
    def handle_cursor_move(data):
        """Broadcast cursor position to other users."""
        from flask import request

        template_id = data.get("template_id")
        if template_id:
            update_cursor(request.sid, data.get("x", 0), data.get("y", 0),
                          data.get("active_field"))
            emit("cursor_update", {
                "sid": request.sid,
                "x": data.get("x", 0),
                "y": data.get("y", 0),
                "active_field": data.get("active_field"),
            }, room=f"template_{template_id}", include_self=False)

    @socketio.on("edit_operation")
    def handle_edit_operation(data):
        """Handle an editing operation from a user."""
        from flask import request

        template_id = data.get("template_id")
        operation = data.get("operation", {})

        if not template_id or not operation:
            emit("error", {"message": "template_id and operation are required"})
            return

        result = apply_operation(int(template_id), operation, request.sid)

        if result.get("success"):
            # Broadcast the transformed operation to all other users
            emit("operation_applied", {
                "operation": result["operation"],
                "version": result["version"],
                "sid": request.sid,
            }, room=f"template_{template_id}", include_self=False)

            # Acknowledge to the sender
            emit("operation_ack", {
                "operation_id": operation.get("id"),
                "version": result["version"],
            })
        else:
            emit("operation_rejected", {
                "error": result.get("error", "Unknown error"),
                "operation": operation,
            })

    @socketio.on("acquire_lock")
    def handle_acquire_lock(data):
        """Try to lock a field for editing."""
        from flask import request

        template_id = data.get("template_id")
        field_id = data.get("field_id")

        if not template_id or not field_id:
            emit("error", {"message": "template_id and field_id are required"})
            return

        result = acquire_lock(int(template_id), field_id, request.sid)

        if result["success"]:
            emit("lock_acquired", {
                "field_id": field_id,
                "template_id": template_id,
            })
            # Notify others
            emit("lock_changed", {
                "field_id": field_id,
                "locked_by": result["locked_by"],
                "sid": request.sid,
                "action": "acquired",
            }, room=f"template_{template_id}", include_self=False)
        else:
            emit("lock_denied", {
                "field_id": field_id,
                "locked_by": result["locked_by"],
            })

    @socketio.on("release_lock")
    def handle_release_lock(data):
        """Release a lock on a field."""
        from flask import request

        template_id = data.get("template_id")
        field_id = data.get("field_id")

        if template_id and field_id:
            release_lock(int(template_id), field_id, request.sid)
            emit("lock_changed", {
                "field_id": field_id,
                "action": "released",
            }, room=f"template_{template_id}")

    @socketio.on("request_sync")
    def handle_request_sync(data):
        """Client requests sync after reconnection."""
        from flask import request

        template_id = data.get("template_id")
        since_version = data.get("since_version", 0)

        if template_id:
            ops = get_operation_history(int(template_id), since_version)
            room = get_room_state(int(template_id))
            emit("sync_response", {
                "operations": ops,
                "room": room,
                "current_version": len(_op_log.get(int(template_id), [])),
            })

    @socketio.on("chat_message")
    def handle_chat_message(data):
        """Handle in-editor chat messages."""
        from flask import request

        template_id = data.get("template_id")
        message = data.get("message", "").strip()

        if template_id and message:
            session = _sessions.get(request.sid, {})
            user_info = session.get("user_info", {})
            emit("chat_message", {
                "sid": request.sid,
                "username": user_info.get("username", "Anonymous"),
                "message": message[:500],  # limit message length
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, room=f"template_{template_id}")

    logger.info("SocketIO initialized with real-time collaboration handlers")
    return socketio
