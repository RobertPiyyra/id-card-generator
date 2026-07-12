"""
Redis-based caching service for frequently accessed data.
Provides automatic cache invalidation and TTL management.
"""
import json
import logging
import hashlib
import functools
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# TTL constants (in seconds)
TTL_SHORT = 60        # 1 minute
TTL_MEDIUM = 300      # 5 minutes
TTL_LONG = 3600       # 1 hour
TTL_VERY_LONG = 86400 # 24 hours


def _get_redis():
    """Get Redis client."""
    try:
        from app.services.redis_service import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def cache_get(key):
    """Get value from cache."""
    redis = _get_redis()
    if not redis:
        return None
    try:
        data = redis.get(f"cache:{key}")
        if data:
            return json.loads(data)
    except Exception as e:
        logger.warning("Cache get error for %s: %s", key, e)
    return None


def cache_set(key, value, ttl=TTL_MEDIUM):
    """Set value in cache with TTL."""
    redis = _get_redis()
    if not redis:
        return
    try:
        redis.setex(f"cache:{key}", ttl, json.dumps(value, default=str))
    except Exception as e:
        logger.warning("Cache set error for %s: %s", key, e)


def cache_delete(key):
    """Delete value from cache."""
    redis = _get_redis()
    if not redis:
        return
    try:
        redis.delete(f"cache:{key}")
    except Exception as e:
        logger.warning("Cache delete error for %s: %s", key, e)


def cache_delete_pattern(pattern):
    """Delete all cache keys matching a pattern."""
    redis = _get_redis()
    if not redis:
        return
    try:
        keys = redis.keys(f"cache:{pattern}")
        if keys:
            redis.delete(*keys)
    except Exception as e:
        logger.warning("Cache delete pattern error for %s: %s", pattern, e)


def cached(ttl=TTL_MEDIUM, key_prefix=""):
    """Decorator to cache function results."""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            # Build cache key from function name and arguments
            cache_key = f"{key_prefix}:{f.__name__}"
            if args:
                cache_key += f":{hashlib.md5(str(args).encode()).hexdigest()[:12]}"
            if kwargs:
                cache_key += f":{hashlib.md5(str(sorted(kwargs.items())).encode()).hexdigest()[:12]}"

            # Try cache first
            result = cache_get(cache_key)
            if result is not None:
                return result

            # Call function and cache result
            result = f(*args, **kwargs)
            cache_set(cache_key, result, ttl=ttl)
            return result
        return wrapper
    return decorator


def invalidate_on_change(model_name):
    """Invalidate cache when a model changes."""
    cache_delete_pattern(f"{model_name}:*")


# Template-specific cache helpers
def get_cached_template_settings(template_id):
    """Get template settings from cache or DB."""
    key = f"template_settings:{template_id}"
    result = cache_get(key)
    if result is not None:
        return result

    from app.helpers import get_template_settings
    result = get_template_settings(template_id)
    cache_set(key, result, TTL_LONG)
    return result


def invalidate_template_cache(template_id):
    """Invalidate all cached data for a template."""
    cache_delete(f"template_settings:{template_id}")
    invalidate_on_change("template")


# Student-specific cache helpers
def get_cached_student_count(school_name=None):
    """Get student count from cache or DB."""
    key = f"student_count:{school_name or 'all'}"
    result = cache_get(key)
    if result is not None:
        return result

    from models import Student
    query = Student.query
    if school_name:
        query = query.filter_by(school_name=school_name)
    result = query.count()
    cache_set(key, result, TTL_MEDIUM)
    return result


# Dashboard cache
def get_cached_dashboard_summary(days=30):
    """Get dashboard summary from cache or compute."""
    key = f"dashboard_summary:{days}"
    result = cache_get(key)
    if result is not None:
        return result

    from app.services.analytics_service import AnalyticsService
    result = AnalyticsService.get_dashboard_summary(days=days)
    cache_set(key, result, TTL_SHORT)  # Short TTL for dashboard
    return result


def _get_cached_media_image(key_prefix, buffer_bytes, generate_fn):
    """Delegate to helpers.py implementation."""
    from app.helpers import _get_cached_media_image
    return _get_cached_media_image(key_prefix, buffer_bytes, generate_fn)


def _get_cached_qr_image(payload, qr_settings, size):
    """Delegate to helpers.py implementation."""
    from app.helpers import _get_cached_qr_image
    return _get_cached_qr_image(payload, qr_settings, size)


def _get_cached_barcode_image(payload, qr_settings, width, height):
    """Delegate to helpers.py implementation."""
    from app.helpers import _get_cached_barcode_image
    return _get_cached_barcode_image(payload, qr_settings, width, height)

def with_cache_bust(key, value, ttl=None):
    """Legacy no-op — cache busting is now handled directly."""
    from app.services.cache_service import cache_set
    if ttl:
        cache_set(key, value, ttl=ttl)
    else:
        cache_set(key, value)


def invalidate_all_template_caches(template_id=None):
    """Invalidate every cache layer that depends on template data.

    Call this whenever a template's settings, fields, or source image change
    so that stale data doesn't persist across the in-memory, Redis, and
    performance-module caches.
    """
    # 1. Redis cache pattern sweep
    if template_id is not None:
        cache_delete(f"template_settings:{template_id}")
        cache_delete_pattern(f"templates:*")
    else:
        cache_delete_pattern("templates:*")
        cache_delete_pattern("template_settings:*")
    invalidate_on_change("template")

    # 2. In-memory template image cache (performance.py)
    try:
        from app.performance import clear_template_cache, clear_card_cache
        clear_template_cache()
        clear_card_cache()
    except Exception:
        pass

    # 3. In-memory render-service caches
    try:
        from app.services.render_service import _template_cache, _template_cache_lock, _template_fields_cache, _template_fields_cache_lock
        with _template_cache_lock:
            _template_cache.clear()
        with _template_fields_cache_lock:
            _template_fields_cache.clear()
    except Exception:
        pass
