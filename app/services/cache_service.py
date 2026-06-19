"""
Unified caching service: Redis + in-memory LRU.

Provides a two-tier caching strategy:
  - L1: In-memory LRU (fastest, per-process)
  - L2: Redis (shared across workers, survives restarts)

Falls back gracefully if Redis is unavailable.
"""
import json
import hashlib
import logging
import threading
import time
from functools import wraps

logger = logging.getLogger(__name__)

# In-memory LRU cache
_lru_cache = {}
_lru_ttl = {}
_lru_lock = threading.Lock()
_LRU_MAX_SIZE = 1024
_LRU_DEFAULT_TTL = 300  # 5 minutes


def _lru_evict():
    """Evict expired and oldest entries if cache is full."""
    now = time.monotonic()
    # Remove expired
    expired = [k for k, v in _lru_ttl.items() if v < now]
    for k in expired:
        _lru_cache.pop(k, None)
        _lru_ttl.pop(k, None)
    # If still full, remove oldest 25%
    if len(_lru_cache) >= _LRU_MAX_SIZE:
        sorted_keys = sorted(_lru_ttl.keys(), key=lambda k: _lru_ttl[k])
        for k in sorted_keys[:_LRU_MAX_SIZE // 4]:
            _lru_cache.pop(k, None)
            _lru_ttl.pop(k, None)


# ---------------------------------------------------------------------------
# Unified cache API
# ---------------------------------------------------------------------------

def cache_get(key: str):
    """
    Get from cache. Tries L1 (memory) first, then L2 (Redis).
    Returns (value, source) where source is 'l1', 'l2', or None.
    """
    # L1: in-memory
    with _lru_lock:
        if key in _lru_cache:
            if _lru_ttl.get(key, 0) > time.monotonic():
                return _lru_cache[key], 'l1'
            else:
                _lru_cache.pop(key, None)
                _lru_ttl.pop(key, None)

    # L2: Redis
    try:
        from app.services.redis_service import _redis_get
        raw = _redis_get(f"cache:{key}")
        if raw is not None:
            value = json.loads(raw) if isinstance(raw, str) else raw
            # Promote to L1
            cache_set(key, value, ttl=_LRU_DEFAULT_TTL, skip_redis=True)
            return value, 'l2'
    except Exception:
        pass

    return None, None


def cache_set(key: str, value, ttl: int = None, skip_redis: bool = False):
    """
    Set a value in both L1 and L2 cache.
    """
    if ttl is None:
        ttl = _LRU_DEFAULT_TTL

    # L1: in-memory
    with _lru_lock:
        _lru_evict()
        _lru_cache[key] = value
        _lru_ttl[key] = time.monotonic() + ttl

    # L2: Redis
    if not skip_redis:
        try:
            from app.services.redis_service import _redis_set
            serialized = json.dumps(value) if not isinstance(value, (str, bytes)) else value
            _redis_set(f"cache:{key}", serialized, ttl=ttl)
        except Exception:
            pass


def cache_delete(key: str):
    """Remove from both L1 and L2."""
    with _lru_lock:
        _lru_cache.pop(key, None)
        _lru_ttl.pop(key, None)

    try:
        from app.services.redis_service import _redis_delete
        _redis_delete(f"cache:{key}")
    except Exception:
        pass


def cache_clear():
    """Clear all caches."""
    with _lru_lock:
        _lru_cache.clear()
        _lru_ttl.clear()

    try:
        from app.services.redis_service import get_redis_client
        client = get_redis_client()
        if client:
            # Only clear our cache keys
            for key in client.scan_iter("cache:*"):
                client.delete(key)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Decorator for caching function results
# ---------------------------------------------------------------------------

def cached(ttl: int = None, key_fn=None):
    """
    Decorator to cache function results.

    Usage:
        @cached(ttl=60, key_fn=lambda template_id: f"template:{template_id}")
        def get_template(template_id):
            return Template.query.get(template_id)
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if key_fn:
                cache_key = key_fn(*args, **kwargs)
            else:
                # Auto-generate key from function name and arguments
                key_parts = [fn.__name__]
                key_parts.extend(str(a) for a in args)
                key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
                cache_key = hashlib.md5(":".join(key_parts).encode()).hexdigest()

            value, source = cache_get(cache_key)
            if value is not None:
                return value

            result = fn(*args, **kwargs)
            cache_set(cache_key, result, ttl=ttl)
            return result
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Template-specific cache helpers
# ---------------------------------------------------------------------------

def cache_template_settings(template_id: int, side: str, settings: dict):
    """Cache template settings (font, photo, QR configs)."""
    key = f"template_settings:{template_id}:{side}"
    cache_set(key, settings, ttl=600)  # 10 min TTL


def get_cached_template_settings(template_id: int, side: str):
    """Get cached template settings."""
    key = f"template_settings:{template_id}:{side}"
    value, _ = cache_get(key)
    return value


def invalidate_template_cache(template_id: int):
    """Invalidate all cached data for a template."""
    for side in ('front', 'back'):
        cache_delete(f"template_settings:{template_id}:{side}")
    from app.performance import clear_template_cache, clear_card_cache
    clear_template_cache()
    clear_card_cache()
