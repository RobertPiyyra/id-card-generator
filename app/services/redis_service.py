import hashlib
import logging
import re
import time
from urllib.parse import urlparse

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue

from app.config import Config

logger = logging.getLogger(__name__)

REDIS_URL = Config.REDIS_URL
REDIS_PUBLIC_URL = Config.REDIS_PUBLIC_URL
REDIS_CACHE_TTL = Config.REDIS_CACHE_TTL
REDIS_CONNECT_TIMEOUT = Config.REDIS_CONNECT_TIMEOUT
REDIS_SOCKET_TIMEOUT = Config.REDIS_SOCKET_TIMEOUT
REDIS_RETRY_SECONDS = Config.REDIS_RETRY_SECONDS
redis_client = None
task_queue = None
_redis_last_error_at = 0
_redis_warned_missing_url = False
_active_redis_url = None


def _redis_url_hostname(redis_url=None):
    try:
        return urlparse(redis_url or REDIS_URL).hostname or ""
    except Exception:
        return ""


def _redis_connection_hint(redis_url=None):
    hostname = _redis_url_hostname(redis_url)
    if hostname.endswith(".railway.internal"):
        extra = ""
        if REDIS_PUBLIC_URL:
            extra = " Falling back to REDIS_PUBLIC_URL when available."
        return (
            " Railway private Redis hosts are reachable only from Railway services "
            "in the same project/environment. Use REDIS_PUBLIC_URL for local tests."
            + extra
        )
    return ""


def _redis_candidate_urls():
    candidates = []
    for value in (REDIS_URL, REDIS_PUBLIC_URL):
        value = (value or "").strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def get_redis_client():
    """
    Lazily connect to Redis using Railway's REDIS_URL.

    Redis is optional: if the service is not attached, down, or temporarily
    unreachable, the app skips caching/queueing and continues rendering.
    """
    global redis_client, _redis_last_error_at, _redis_warned_missing_url, _active_redis_url

    candidates = _redis_candidate_urls()
    if not candidates:
        if not _redis_warned_missing_url:
            logger.warning("REDIS_URL / REDIS_PUBLIC_URL are not set; Redis cache and RQ queue are disabled.")
            _redis_warned_missing_url = True
        return None

    now = time.time()
    if redis_client is None and _redis_last_error_at and now - _redis_last_error_at < REDIS_RETRY_SECONDS:
        return None

    if redis_client is None:
        failures = []
        for redis_url in candidates:
            try:
                candidate = Redis.from_url(
                    redis_url,
                    decode_responses=False,
                    socket_connect_timeout=REDIS_CONNECT_TIMEOUT,
                    socket_timeout=REDIS_SOCKET_TIMEOUT,
                    health_check_interval=30,
                    retry_on_timeout=True,
                )
                candidate.ping()
                redis_client = candidate
                _active_redis_url = redis_url
                if redis_url == REDIS_URL:
                    logger.info("Connected to Redis using REDIS_URL.")
                else:
                    for label, failure_exc, failure_hint in failures:
                        logger.info(
                            "Redis connection attempt failed for %s but fallback recovered: %s%s",
                            label,
                            failure_exc,
                            failure_hint,
                        )
                    logger.info("Connected to Redis using REDIS_PUBLIC_URL fallback.")
                break
            except Exception as exc:
                failures.append((
                    "REDIS_URL" if redis_url == REDIS_URL else "REDIS_PUBLIC_URL",
                    exc,
                    _redis_connection_hint(redis_url),
                ))
        if redis_client is None:
            _redis_last_error_at = now
            _active_redis_url = None
            for label, failure_exc, failure_hint in failures:
                logger.warning(
                    "Redis connection attempt failed for %s: %s%s",
                    label,
                    failure_exc,
                    failure_hint,
                )
            logger.warning("Redis unavailable; continuing without Redis cache/queue.")

    return redis_client


def _mark_redis_unavailable(exc):
    global redis_client, task_queue, _redis_last_error_at, _active_redis_url
    _redis_last_error_at = time.time()
    redis_client = None
    task_queue = None
    _active_redis_url = None
    logger.warning("Redis operation failed; continuing without Redis temporarily: %s", exc)


def get_task_queue():
    global task_queue
    client = get_redis_client()
    if client is None:
        return None
    if task_queue is None:
        task_queue = Queue("id_card_bulk", connection=client)
    return task_queue


def _redis_cache_key(*parts):
    normalized = []
    for part in parts:
        if part is None:
            normalized.append("none")
            continue
        if isinstance(part, bytes):
            normalized.append(hashlib.sha256(part).hexdigest())
            continue
        text = str(part)
        if len(text) > 256:
            text = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
        normalized.append(re.sub(r"[^0-9A-Za-z_.-]", "_", text))
    return "id_card:" + ":".join(normalized)


def _redis_get(key):
    client = get_redis_client()
    if client is None:
        return None
    try:
        return client.get(key)
    except RedisError as exc:
        logger.warning("Redis cache read failed for %s: %s", key, exc)
        _mark_redis_unavailable(exc)
        return None


def _redis_set(key, value, ttl=REDIS_CACHE_TTL):
    client = get_redis_client()
    if client is None:
        return False
    try:
        return bool(client.set(key, value, ex=ttl))
    except RedisError as exc:
        logger.warning("Redis cache write failed for %s: %s", key, exc)
        _mark_redis_unavailable(exc)
        return False


def _redis_delete(key):
    client = get_redis_client()
    if client is None:
        return False
    try:
        client.delete(key)
        return True
    except RedisError as exc:
        _mark_redis_unavailable(exc)
        return False


def _redis_acquire_lock(lock_key, ttl=5):
    client = get_redis_client()
    if client is None:
        return True
    try:
        return bool(client.set(lock_key, b"1", nx=True, ex=ttl))
    except RedisError as exc:
        _mark_redis_unavailable(exc)
        return True


def redis_connection_mode():
    if _active_redis_url == REDIS_URL:
        return "private"
    if _active_redis_url == REDIS_PUBLIC_URL:
        return "public_fallback"
    return None
