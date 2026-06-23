"""
Caching service for media images (QR, barcodes, templates).

Provides Redis-backed caching with stampede protection.
Extracted from legacy_app.py.
"""

import io
import logging
import time

from PIL import Image

from app.services.redis_service import (
    _redis_cache_key,
    _redis_acquire_lock,
    _redis_delete,
    _redis_get,
    _redis_set,
)
from app.services.qr_service import generate_qr_code
from app.services.barcode_service import generate_barcode_code128

logger = logging.getLogger(__name__)


def _get_cached_media_image(key_prefix, buffer_bytes, generate_fn):
    """Get or generate a cached media image with stampede protection."""
    cache_key = _redis_cache_key(key_prefix, buffer_bytes)
    cached = _redis_get(cache_key)
    if cached is not None:
        try:
            img = Image.open(io.BytesIO(cached))
            img.load()
            return img.convert("RGBA")
        except Exception as e:
            logger.warning("Media cache decode failed for %s: %s", cache_key, e)
            _redis_delete(cache_key)

    lock_key = cache_key + ":lock"
    if not _redis_acquire_lock(lock_key, ttl=5):
        time.sleep(0.05)
        cached = _redis_get(cache_key)
        if cached is not None:
            try:
                img = Image.open(io.BytesIO(cached))
                img.load()
                return img.convert("RGBA")
            except Exception:
                pass

    try:
        img = generate_fn()
        if img is not None:
            try:
                out = io.BytesIO()
                img.save(out, format="PNG")
                _redis_set(cache_key, out.getvalue())
            except Exception as exc:
                logger.warning("Failed to cache media image %s: %s", cache_key, exc)
        return img
    finally:
        _redis_delete(lock_key)


def _get_cached_qr_image(payload, qr_settings, size):
    """Get a cached QR code image."""
    logo_key = f"{qr_settings.get('qr_include_logo', False)}:{qr_settings.get('qr_logo_path', '')}"
    return _get_cached_media_image(
        "qr",
        f"{payload}:{size}:{qr_settings.get('qr_data_type','default')}:{logo_key}".encode("utf-8", "ignore"),
        lambda: generate_qr_code(payload, qr_settings, size),
    )


def _get_cached_barcode_image(payload, qr_settings, width, height):
    """Get a cached barcode image."""
    return _get_cached_media_image(
        "barcode",
        f"{payload}:{width}:{height}:{qr_settings.get('barcode_data_type','default')}".encode("utf-8", "ignore"),
        lambda: generate_barcode_code128(payload, qr_settings, width=width, height=height),
    )


def with_cache_bust(url):
    """Append a cache-busting query param for preview images."""
    import time
    if not url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}v={int(time.time() * 1000)}"
