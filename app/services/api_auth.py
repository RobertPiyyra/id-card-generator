"""
API Key Authentication Service.

Provides API key generation, validation, rate limiting, and access logging
for the REST API. API keys are hashed before storage.
"""
import os
import hmac
import hashlib
import secrets
import logging
import time
from datetime import datetime, timezone
from functools import wraps

from flask import request, jsonify, g

from models import db, ApiKey, ApiKeyLog, AdminUser

logger = logging.getLogger(__name__)

# All valid API scopes
VALID_SCOPES = [
    'students:read', 'students:write',
    'templates:read', 'templates:write',
    'bulk:read', 'bulk:write',
    'webhooks:manage',
    'print:read', 'print:write',
    'nfc:read', 'nfc:write',
    'admin',  # Full access
]


def has_scope(api_key, scope):
    """Check if an API key has a specific scope."""
    if not api_key or not api_key.scopes:
        return False
    key_scopes = api_key.scopes
    if 'admin' in key_scopes:
        return True
    return scope in key_scopes


def generate_key_pair():
    """Generate a new API key pair. Returns (raw_key, key_hash, key_prefix)."""
    raw_key = 'idcard_' + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:12]
    return raw_key, key_hash, key_prefix


def create_api_key(admin_id, name, scopes=None, rate_limit=None):
    """Create a new API key for an admin user."""
    raw_key, key_hash, key_prefix = generate_key_pair()

    api_key = ApiKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=name,
        admin_id=admin_id,
        scopes=scopes or ['students:read', 'templates:read'],
        is_active=True,
        rate_limit=rate_limit or _RATE_LIMIT_MAX,
    )
    db.session.add(api_key)
    db.session.commit()

    logger.info(f"API key created: {key_prefix}... for admin {admin_id}")
    return raw_key, api_key


def validate_api_key(raw_key):
    """Validate an API key and return the ApiKey object or None."""
    if not raw_key:
        return None

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key = ApiKey.query.filter_by(key_hash=key_hash, is_active=True).first()

    if api_key:
        # Update last used
        api_key.last_used_at = datetime.now(timezone.utc)
        api_key.request_count = (api_key.request_count or 0) + 1
        db.session.commit()

    return api_key


def check_rate_limit(api_key):
    """Check if the API key has exceeded its rate limit."""
    cache_key = f"ratelimit:{api_key.id}"
    now = time.time()

    if cache_key not in _rate_limit_cache:
        _rate_limit_cache[cache_key] = []

    # Clean old entries
    window_start = now - _RATE_LIMIT_WINDOW
    _rate_limit_cache[cache_key] = [
        t for t in _rate_limit_cache[cache_key] if t > window_start
    ]

    # Check limit
    if len(_rate_limit_cache[cache_key]) >= (api_key.rate_limit or _RATE_LIMIT_MAX):
        return False

    _rate_limit_cache[cache_key].append(now)
    return True


def log_api_access(api_key, method, path, status_code, ip_address):
    """Log an API access to ApiKeyLog."""
    try:
        log = ApiKeyLog(
            api_key_id=api_key.id,
            method=method,
            path=path,
            status_code=status_code,
            ip_address=ip_address,
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logger.warning(f"Failed to log API access: {e}")
        db.session.rollback()


def require_api_key(*required_scopes):
    """
    Decorator to require API key authentication on a route.

    Usage:
        @require_api_key()
        def my_route(): ...

        @require_api_key('students:write')
        def create_student(): ...

    The API key can be provided via:
    - X-API-Key header
    - ?api_key= query parameter
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            raw_key = request.headers.get('X-API-Key') or request.args.get('api_key')

            if not raw_key:
                return jsonify({
                    'success': False,
                    'error': 'API key required. Provide via X-API-Key header or api_key query param.'
                }), 401

            api_key = validate_api_key(raw_key)

            if not api_key:
                return jsonify({'success': False, 'error': 'Invalid or revoked API key'}), 401

            # Check scopes
            if required_scopes:
                key_scopes = api_key.scopes or []
                for scope in required_scopes:
                    if scope not in key_scopes and 'admin' not in key_scopes:
                        return jsonify({
                            'success': False,
                            'error': f'Insufficient scope. Required: {scope}'
                        }), 403

            # Check rate limit
            if not check_rate_limit(api_key):
                return jsonify({
                    'success': False,
                    'error': 'Rate limit exceeded. Try again later.'
                }), 429

            # Store in g for use in the route
            g.api_key = api_key
            g.api_admin = db.session.get(AdminUser, api_key.admin_id) if api_key.admin_id else None

            response = f(*args, **kwargs)

            # Log the access
            status_code = 200
            if hasattr(response, 'status_code'):
                status_code = response.status_code
            elif isinstance(response, tuple):
                status_code = response[1] if len(response) > 1 else 200

            log_api_access(
                api_key=api_key,
                method=request.method,
                path=request.path,
                status_code=status_code,
                ip_address=request.remote_addr,
            )

            return response
        return decorated
    return decorator


def list_api_keys(admin_id=None, page=1, per_page=50):
    """List API keys, optionally filtered by admin."""
    query = ApiKey.query
    if admin_id:
        query = query.filter_by(admin_id=admin_id)
    query = query.order_by(ApiKey.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return pagination.items, pagination.total


def revoke_api_key(key_id):
    """Revoke (deactivate) an API key."""
    api_key = db.session.get(ApiKey, key_id)
    if api_key:
        api_key.is_active = False
        db.session.commit()
        logger.info(f"API key {api_key.key_prefix}... revoked")
        return True
    return False


def get_api_key_logs(key_id, page=1, per_page=50):
    """Get access logs for an API key."""
    query = ApiKeyLog.query.filter_by(api_key_id=key_id).order_by(ApiKeyLog.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return pagination.items, pagination.total
