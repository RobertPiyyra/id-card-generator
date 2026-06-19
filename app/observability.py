"""
Production observability: health checks, metrics, timing, API versioning.
"""
import time
import logging
import functools
from collections import defaultdict

from flask import request, g, jsonify, Blueprint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request timing middleware
# ---------------------------------------------------------------------------

def init_request_timing(app):
    """Add X-Response-Time header to every response."""

    @app.after_request
    def _add_timing(response):
        start = getattr(g, "request_start", None)
        if start is not None:
            elapsed = time.monotonic() - start
            response.headers["X-Response-Time"] = f"{elapsed:.4f}s"
            # Log slow requests (>2s)
            if elapsed > 2.0:
                logger.warning(
                    "slow_request",
                    extra={
                        "path": request.path,
                        "method": request.method,
                        "duration": f"{elapsed:.4f}",
                        "status": response.status_code,
                        "request_id": getattr(g, "request_id", ""),
                    },
                )
        return response


# ---------------------------------------------------------------------------
# Prometheus-style metrics endpoint
# ---------------------------------------------------------------------------

_metrics = {
    "requests_total": defaultdict(int),
    "errors_total": defaultdict(int),
    "request_duration_seconds": defaultdict(list),
}


def init_metrics(app):
    """Track request metrics and expose /metrics endpoint."""

    @app.after_request
    def _track_metrics(response):
        endpoint = request.endpoint or "unknown"
        _metrics["requests_total"][endpoint] += 1
        if response.status_code >= 400:
            _metrics["errors_total"][f"{endpoint}:{response.status_code}"] += 1
        start = getattr(g, "request_start", None)
        if start is not None:
            elapsed = time.monotonic() - start
            _metrics["request_duration_seconds"][endpoint].append(elapsed)
            # Keep only last 1000 durations per endpoint
            if len(_metrics["request_duration_seconds"][endpoint]) > 1000:
                _metrics["request_duration_seconds"][endpoint] = \
                    _metrics["request_duration_seconds"][endpoint][-1000:]
        return response

    @app.route("/metrics")
    def _metrics_endpoint():
        """Prometheus-compatible metrics endpoint."""
        lines = []
        for endpoint, count in _metrics["requests_total"].items():
            lines.append(f'requests_total{{endpoint="{endpoint}"}} {count}')
        for key, count in _metrics["errors_total"].items():
            endpoint, status = key.rsplit(":", 1)
            lines.append(f'errors_total{{endpoint="{endpoint}",status="{status}"}} {count}')
        for endpoint, durations in _metrics["request_duration_seconds"].items():
            if durations:
                avg = sum(durations) / len(durations)
                lines.append(f'request_duration_seconds_avg{{endpoint="{endpoint}"}} {avg:.4f}')
                lines.append(f'request_duration_seconds_count{{endpoint="{endpoint}"}} {len(durations)}')
        return "\n".join(lines), 200, {"Content-Type": "text/plain"}


# ---------------------------------------------------------------------------
# Comprehensive health check
# ---------------------------------------------------------------------------

def init_health_checks(app):
    """Add detailed health check endpoints."""

    @app.route("/health/live")
    def liveness():
        """Kubernetes liveness probe — is the process running?"""
        return jsonify({"status": "alive", "timestamp": time.time()}), 200

    @app.route("/health/ready")
    def readiness():
        """Kubernetes readiness probe — can we serve traffic?"""
        checks = {}
        overall = "ready"

        # Database check
        try:
            from models import db
            db.session.execute(db.text("SELECT 1"))
            checks["database"] = "connected"
        except Exception as exc:
            checks["database"] = f"error: {exc}"
            overall = "not_ready"

        # Redis check
        try:
            from app.services.redis_service import get_redis_client
            redis_client = get_redis_client()
            if redis_client is not None:
                redis_client.ping()
                checks["redis"] = "connected"
            else:
                checks["redis"] = "disabled"
        except Exception as exc:
            checks["redis"] = f"error: {exc}"
            # Redis is optional — don't fail readiness

        # Disk space check
        try:
            import shutil
            usage = shutil.disk_usage("/")
            free_pct = (usage.free / usage.total) * 100
            checks["disk_free_pct"] = f"{free_pct:.1f}"
            if free_pct < 5:
                overall = "not_ready"
                checks["disk"] = "critical"
            elif free_pct < 15:
                checks["disk"] = "warning"
            else:
                checks["disk"] = "ok"
        except Exception:
            pass

        status_code = 200 if overall == "ready" else 503
        return jsonify({"status": overall, "checks": checks}), status_code


# ---------------------------------------------------------------------------
# API versioning blueprint
# ---------------------------------------------------------------------------

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")


def init_api_versioning(app):
    """Register versioned API blueprint."""
    app.register_blueprint(api_v1)


# ---------------------------------------------------------------------------
# Token-based API authentication
# ---------------------------------------------------------------------------

def token_required(f):
    """Decorator: require Bearer token for API access."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"success": False, "error": "Missing or invalid Authorization header"}), 401
        token = auth_header[7:]
        # Validate against stored API keys
        from models import ApiKey
        from app.services.security_service import validate_api_key
        key_record = validate_api_key(token)
        if key_record is None:
            return jsonify({"success": False, "error": "Invalid API key"}), 401
        g.api_key = key_record
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Per-user rate limiting
# ---------------------------------------------------------------------------

def get_rate_limit_key():
    """Return a rate limit key based on user identity."""
    # Use API key if present
    api_key = getattr(g, "api_key", None)
    if api_key:
        return f"apikey:{api_key.id}"
    # Use session-based admin
    from flask import session
    if session.get("admin"):
        return f"admin:{session.get('admin', 'unknown')}"
    # Use student email
    if session.get("student_email"):
        return f"student:{session['student_email']}"
    # Fall back to IP
    return f"ip:{request.remote_addr}"


# ---------------------------------------------------------------------------
# Slow query logging
# ---------------------------------------------------------------------------

def init_slow_query_logging(app, threshold=1.0):
    """Log database queries that exceed the threshold (seconds)."""
    try:
        from sqlalchemy import event
        from models import db as database

        @event.listens_for(database.engine, "before_cursor_execute")
        def _before_execute(conn, cursor, statement, parameters, context, executemany):
            conn.info.setdefault("query_start_time", []).append(time.monotonic())

        @event.listens_for(database.engine, "after_cursor_execute")
        def _after_execute(conn, cursor, statement, parameters, context, executemany):
            total = time.monotonic() - conn.info["query_start_time"].pop()
            if total > threshold:
                logger.warning(
                    "slow_query",
                    extra={
                        "duration": f"{total:.4f}",
                        "statement": statement[:500],
                        "request_id": getattr(g, "request_id", ""),
                        "path": request.path if request else "",
                    },
                )
    except Exception as exc:
        logger.warning("Slow query logging setup failed: %s", exc)


# ---------------------------------------------------------------------------
# Startup health verification
# ---------------------------------------------------------------------------

def verify_startup_dependencies(app):
    """Verify all critical dependencies at startup. Logs warnings for optional ones."""
    with app.app_context():
        # Database
        try:
            from models import db
            db.session.execute(db.text("SELECT 1"))
            logger.info("startup_check: database OK")
        except Exception as exc:
            logger.error("startup_check: database FAILED — %s", exc)

        # Redis (optional)
        try:
            from app.services.redis_service import get_redis_client
            redis_client = get_redis_client()
            if redis_client:
                redis_client.ping()
                logger.info("startup_check: Redis OK")
            else:
                logger.info("startup_check: Redis disabled (no URL configured)")
        except Exception as exc:
            logger.info("startup_check: Redis unavailable — %s", exc)

        # Fonts
        try:
            from app.legacy_app import verify_fonts_available
            verify_fonts_available()
            logger.info("startup_check: fonts OK")
        except Exception as exc:
            logger.warning("startup_check: fonts issue — %s", exc)

        # Disk space
        try:
            import shutil
            usage = shutil.disk_usage("/")
            free_pct = (usage.free / usage.total) * 100
            if free_pct < 5:
                logger.error("startup_check: disk space CRITICAL (%.1f%% free)", free_pct)
            elif free_pct < 15:
                logger.warning("startup_check: disk space low (%.1f%% free)", free_pct)
            else:
                logger.info("startup_check: disk space OK (%.1f%% free)", free_pct)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Initialise everything
# ---------------------------------------------------------------------------

def init_observability(app):
    """Register all observability features."""
    init_request_timing(app)
    init_metrics(app)
    init_health_checks(app)
    init_api_versioning(app)
    init_slow_query_logging(app)
