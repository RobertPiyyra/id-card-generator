"""
Shared requests.Session with connection pooling.

All outbound HTTP (Cloudinary downloads, Google Translate, photo fetches)
should go through this session to reuse TCP connections instead of creating
a new one per request.

Usage:
    from app.services.http_pool import http_get, http_post
    resp = http_get("https://example.com/photo.jpg", timeout=10)
"""
import requests
from requests.adapters import HTTPAdapter

_session = requests.Session()
_session.mount(
    "https://",
    HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=2),
)
_session.mount(
    "http://",
    HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=2),
)


def http_get(url, **kwargs):
    """GET request using a pooled connection."""
    return _session.get(url, **kwargs)


def http_post(url, **kwargs):
    """POST request using a pooled connection."""
    return _session.post(url, **kwargs)


def get_session():
    """Return the shared requests.Session for advanced usage."""
    return _session
