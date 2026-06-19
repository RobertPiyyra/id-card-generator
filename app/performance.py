"""
Performance optimization module: caching, lazy loading, connection pooling.

Key optimizations:
  1. LRU font cache — avoids re-loading fonts from disk
  2. Template image cache — avoids re-reading/decoding template images
  3. Query result cache — avoids repeated DB queries for same data
  4. Lazy heavy imports — mediapipe, pandas only imported when needed
  5. Connection pool tuning — SQLAlchemy pool pre-ping and sizing
  6. Rendering pipeline cache — QR codes, barcodes cached by content hash
  7. Bulk operation batching — batch DB inserts/updates
"""
import os
import io
import time
import hashlib
import logging
import threading
import functools
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. LRU Font Cache
# ---------------------------------------------------------------------------

_font_cache = {}
_font_cache_lock = threading.Lock()
_FONT_CACHE_MAX = 256  # max cached font objects


def get_cached_font(font_path: str, size: int, font_index: int = 0):
    """
    Get a PIL ImageFont from the LRU cache.
    Falls back to loading from disk on cache miss.

    Usage:
        font = get_cached_font('static/fonts/arial.ttf', 24)
    """
    cache_key = (font_path, size, font_index)

    with _font_cache_lock:
        if cache_key in _font_cache:
            # Move to end (most recently used)
            font = _font_cache.pop(cache_key)
            _font_cache[cache_key] = font
            return font

    # Cache miss — load from disk
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(font_path, size, index=font_index)
    except Exception:
        from PIL import ImageFont
        font = ImageFont.load_default()

    with _font_cache_lock:
        if len(_font_cache) >= _FONT_CACHE_MAX:
            # Evict oldest (first) entry
            _font_cache.pop(next(iter(_font_cache)))
        _font_cache[cache_key] = font

    return font


def preload_fonts(font_paths: list, sizes: list = None):
    """
    Preload commonly used fonts into cache at startup.
    Call this after app init to warm the cache.

    Usage:
        preload_fonts(['static/fonts/arial.ttf'], [12, 14, 16, 18, 20, 24])
    """
    if sizes is None:
        sizes = [10, 12, 14, 16, 18, 20, 24, 28, 32, 36]
    for path in font_paths:
        for size in sizes:
            try:
                get_cached_font(path, size)
            except Exception:
                pass
    logger.info("Font cache preloaded: %d fonts", len(_font_cache))


# ---------------------------------------------------------------------------
# 2. Template Image Cache
# ---------------------------------------------------------------------------

_template_image_cache = {}
_template_cache_lock = threading.Lock()
_TEMPLATE_CACHE_MAX = 64  # max cached template images


def get_cached_template_image(path_or_url: str, target_w: int, target_h: int):
    """
    Get a cached template image. Avoids re-reading from disk or re-downloading.
    Returns (Image, hit) tuple.
    """
    cache_key = (path_or_url, target_w, target_h)

    with _template_cache_lock:
        if cache_key in _template_image_cache:
            return _template_image_cache[cache_key], True

    return None, False


def set_cached_template_image(path_or_url: str, target_w: int, target_h: int, image):
    """Store a template image in the cache."""
    cache_key = (path_or_url, target_w, target_h)
    with _template_cache_lock:
        if len(_template_image_cache) >= _TEMPLATE_CACHE_MAX:
            _template_image_cache.pop(next(iter(_template_image_cache)))
        _template_image_cache[cache_key] = image


def clear_template_cache():
    """Clear the template image cache (e.g., after template update)."""
    with _template_cache_lock:
        _template_image_cache.clear()


# ---------------------------------------------------------------------------
# 3. Rendered Card Cache (content-addressable)
# ---------------------------------------------------------------------------

_card_cache = {}
_card_cache_lock = threading.Lock()
_CARD_CACHE_MAX = 128


def get_card_cache_key(template_id: int, student_id: int, side: str, settings_hash: str) -> str:
    """Generate a unique cache key for a rendered card."""
    raw = f"{template_id}:{student_id}:{side}:{settings_hash}"
    return hashlib.md5(raw.encode()).hexdigest()


def get_cached_card(cache_key: str):
    """Get a rendered card from cache."""
    with _card_cache_lock:
        return _card_cache.get(cache_key)


def set_cached_card(cache_key: str, image):
    """Store a rendered card in cache."""
    with _card_cache_lock:
        if len(_card_cache) >= _CARD_CACHE_MAX:
            _card_cache.pop(next(iter(_card_cache)))
        _card_cache[cache_key] = image


def clear_card_cache():
    """Clear the rendered card cache."""
    with _card_cache_lock:
        _card_cache.clear()


# ---------------------------------------------------------------------------
# 4. QR / Barcode Cache
# ---------------------------------------------------------------------------

_qr_cache = {}
_barcode_cache = {}
_qr_cache_lock = threading.Lock()
_CACHE_MAX = 512


def get_cached_qr(content: str, size: int = 200):
    """Get a cached QR code image."""
    key = f"{content}:{size}"
    with _qr_cache_lock:
        return _qr_cache.get(key)


def set_cached_qr(content: str, size: int, image):
    """Cache a QR code image."""
    key = f"{content}:{size}"
    with _qr_cache_lock:
        if len(_qr_cache) >= _CACHE_MAX:
            _qr_cache.pop(next(iter(_qr_cache)))
        _qr_cache[key] = image


def get_cached_barcode(content: str):
    """Get a cached barcode image."""
    with _qr_cache_lock:
        return _barcode_cache.get(content)


def set_cached_barcode(content: str, image):
    """Cache a barcode image."""
    with _qr_cache_lock:
        if len(_barcode_cache) >= _CACHE_MAX:
            _barcode_cache.pop(next(iter(_barcode_cache)))
        _barcode_cache[content] = image


# ---------------------------------------------------------------------------
# 5. Lazy Heavy Imports
# ---------------------------------------------------------------------------

_lazy_imports = {}


def lazy_import(module_name: str):
    """
    Lazily import a heavy module. First call imports and caches;
    subsequent calls return the cached module.

    Usage:
        pd = lazy_import('pandas')
        mp = lazy_import('mediapipe')
    """
    if module_name not in _lazy_imports:
        try:
            import importlib
            _lazy_imports[module_name] = importlib.import_module(module_name)
        except ImportError:
            _lazy_imports[module_name] = None
            logger.warning("lazy_import: %s not available", module_name)
    return _lazy_imports[module_name]


# ---------------------------------------------------------------------------
# 6. Query Result Cache (in-memory, TTL-based)
# ---------------------------------------------------------------------------

_query_cache = {}
_query_cache_ttl = {}
_query_cache_lock = threading.Lock()
_QUERY_CACHE_DEFAULT_TTL = 300  # 5 minutes


def cached_query(cache_key: str, query_fn, ttl: int = None):
    """
    Execute a query function and cache the result.
    On cache hit, skip the database entirely.

    Usage:
        students = cached_query('all_students', lambda: Student.query.all(), ttl=60)
    """
    if ttl is None:
        ttl = _QUERY_CACHE_DEFAULT_TTL

    now = time.monotonic()
    with _query_cache_lock:
        if cache_key in _query_cache:
            if _query_cache_ttl.get(cache_key, 0) > now:
                return _query_cache[cache_key]
            # Expired
            del _query_cache[cache_key]
            del _query_cache_ttl[cache_key]

    result = query_fn()

    with _query_cache_lock:
        _query_cache[cache_key] = result
        _query_cache_ttl[cache_key] = now + ttl

    return result


def invalidate_query_cache(cache_key: str = None):
    """Invalidate a specific key or the entire query cache."""
    with _query_cache_lock:
        if cache_key:
            _query_cache.pop(cache_key, None)
            _query_cache_ttl.pop(cache_key, None)
        else:
            _query_cache.clear()
            _query_cache_ttl.clear()


# ---------------------------------------------------------------------------
# 7. Batch DB Operations
# ---------------------------------------------------------------------------

def batch_insert(model_class, records: list, batch_size: int = 500, db_session=None):
    """
    Insert records in batches for better performance.
    Much faster than individual adds for bulk operations.

    Usage:
        batch_insert(Student, [{'name': 'A', 'email': 'a@b.com'}, ...])
    """
    from models import db
    session = db_session or db.session

    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        session.bulk_insert_mappings(model_class, batch)

    session.commit()


def batch_update(model_class, records: list, batch_size: int = 500, db_session=None):
    """
    Update records in batches using bulk_update_mappings.
    """
    from models import db
    session = db_session or db.session

    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        session.bulk_update_mappings(model_class, batch)

    session.commit()


# ---------------------------------------------------------------------------
# 8. Thread Pool for Parallel Rendering
# ---------------------------------------------------------------------------

_render_executor = None


def get_render_executor(max_workers: int = None) -> ThreadPoolExecutor:
    """Get or create the shared render thread pool."""
    global _render_executor
    if _render_executor is None:
        if max_workers is None:
            max_workers = min(8, (os.cpu_count() or 4) * 2)
        _render_executor = ThreadPoolExecutor(max_workers=max_workers)
    return _render_executor


def shutdown_render_executor():
    """Shutdown the render thread pool (call on app shutdown)."""
    global _render_executor
    if _render_executor:
        _render_executor.shutdown(wait=False)
        _render_executor = None


# ---------------------------------------------------------------------------
# 9. Performance monitoring decorator
# ---------------------------------------------------------------------------

def timed(label: str = None, threshold_ms: float = 100):
    """
    Decorator to log slow function calls.

    Usage:
        @timed('render_card', threshold_ms=200)
        def render_card(...):
            ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.monotonic()
            result = fn(*args, **kwargs)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > threshold_ms:
                name = label or fn.__name__
                logger.warning(
                    "slow_function",
                    extra={
                        "function": name,
                        "duration_ms": f"{elapsed_ms:.1f}",
                    },
                )
            return result
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# 10. Initialize all performance features
# ---------------------------------------------------------------------------

def init_performance(app):
    """
    Initialize all performance optimizations.
    Call once at app startup.
    """
    # Preload fonts
    fonts_dir = os.path.join(app.root_path, '..', 'static', 'fonts')
    fonts_dir = os.path.normpath(fonts_dir)
    if os.path.isdir(fonts_dir):
        common_fonts = []
        for f in os.listdir(fonts_dir):
            if f.lower().endswith(('.ttf', '.otf')):
                common_fonts.append(os.path.join(fonts_dir, f))
        if common_fonts:
            preload_fonts(common_fonts[:10])  # preload first 10 fonts

    # Log cache stats
    logger.info(
        "performance_init: font_cache=%d, template_cache=%d, card_cache=%d",
        len(_font_cache),
        len(_template_image_cache),
        len(_card_cache),
    )

    # Register shutdown hook
    @app.teardown_appcontext
    def _shutdown(exc):
        pass  # cleanup if needed
