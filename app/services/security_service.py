"""
Enterprise Security Service
Handles 2FA, login history, session management, and device tracking.
Isolated module - does not modify any existing code.
"""
import os
import re
import hashlib
import hmac
import base64
import struct
import time
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from flask import request, session, current_app
from models import (
    db, AdminUser, LoginHistory, UserSession, TwoFactorBackupCode
)
from user_agents import parse as parse_ua

logger = logging.getLogger(__name__)

# ================== Device Fingerprinting ==================

def _parse_user_agent(user_agent_string: str) -> dict:
    """Parse user agent string into structured device info."""
    if not user_agent_string:
        return {'device_type': 'unknown', 'browser': 'unknown', 'os': 'unknown'}
    try:
        ua = parse_ua(user_agent_string)
        device_type = 'mobile' if ua.is_mobile else ('tablet' if ua.is_tablet else 'desktop')
        return {
            'device_type': device_type,
            'browser': f"{ua.browser.family} {ua.browser.version_string}",
            'os': f"{ua.os.family} {ua.os.version_string}",
        }
    except Exception:
        return {'device_type': 'unknown', 'browser': 'unknown', 'os': 'unknown'}


def _generate_device_fingerprint(user_agent: str, ip: str) -> str:
    """Generate a semi-stable device fingerprint."""
    raw = f"{user_agent}|{ip}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ================== Login History ==================

def record_login_attempt(username: str, success: bool, failure_reason: str = None,
                         admin_id: int = None, session_token: str = None):
    """Record a login attempt to the history table."""
    try:
        ua_string = request.headers.get('User-Agent', '') if request else ''
        ip = request.remote_addr if request else None
        device_info = _parse_user_agent(ua_string)

        entry = LoginHistory(
            admin_id=admin_id,
            username=username,
            ip_address=ip,
            user_agent=ua_string[:512],
            device_type=device_info['device_type'],
            browser=device_info['browser'],
            os=device_info['os'],
            login_success=success,
            failure_reason=failure_reason,
            session_token=session_token,
        )
        db.session.add(entry)
        db.session.commit()
        return entry
    except Exception as e:
        logger.error(f"Failed to record login history: {e}")
        db.session.rollback()
        return None


def get_login_history(username: str = None, limit: int = 50, failed_only: bool = False):
    """Query login history with optional filters."""
    query = LoginHistory.query
    if username:
        query = query.filter_by(username=username)
    if failed_only:
        query = query.filter_by(login_success=False)
    return query.order_by(LoginHistory.created_at.desc()).limit(limit).all()


def get_recent_failed_attempts(username: str, minutes: int = 30) -> int:
    """Count recent failed login attempts for rate limiting / lockout."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return LoginHistory.query.filter(
        LoginHistory.username == username,
        LoginHistory.login_success == False,
        LoginHistory.created_at >= cutoff,
    ).count()


def get_active_sessions(admin_id: int):
    """Get all active sessions for a user."""
    now = datetime.now(timezone.utc)
    return UserSession.query.filter(
        UserSession.admin_id == admin_id,
        UserSession.is_active == True,
        UserSession.expires_at > now,
    ).order_by(UserSession.last_activity_at.desc()).all()


def get_login_analytics(days: int = 30) -> dict:
    """Aggregate login analytics for the security dashboard."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base_q = LoginHistory.query.filter(LoginHistory.created_at >= cutoff)

    total = base_q.count()
    successful = base_q.filter_by(login_success=True).count()
    failed = total - successful

    # Unique IPs
    unique_ips = db.session.query(
        db.func.count(db.distinct(LoginHistory.ip_address))
    ).filter(LoginHistory.created_at >= cutoff).scalar() or 0

    # Top browsers
    browser_counts = db.session.query(
        LoginHistory.browser,
        db.func.count(LoginHistory.id)
    ).filter(
        LoginHistory.created_at >= cutoff
    ).group_by(LoginHistory.browser).order_by(db.func.count(LoginHistory.id).desc()).limit(5).all()

    # Top failed usernames
    top_failed = db.session.query(
        LoginHistory.username,
        db.func.count(LoginHistory.id)
    ).filter(
        LoginHistory.created_at >= cutoff,
        LoginHistory.login_success == False
    ).group_by(LoginHistory.username).order_by(db.func.count(LoginHistory.id).desc()).limit(5).all()

    return {
        'total_logins': total,
        'successful': successful,
        'failed': failed,
        'success_rate': round(successful / total * 100, 1) if total else 0,
        'unique_ips': unique_ips,
        'top_browsers': [{'browser': b[0], 'count': b[1]} for b in browser_counts],
        'top_failed_usernames': [{'username': u[0], 'count': u[1]} for u in top_failed],
    }


# ================== Session Management ==================

def create_managed_session(admin_id: int, two_factor_verified: bool = False) -> Optional[UserSession]:
    """Create a managed session record alongside Flask session."""
    try:
        ua_string = request.headers.get('User-Agent', '') if request else ''
        ip = request.remote_addr if request else None
        fingerprint = _generate_device_fingerprint(ua_string, ip or '')
        token = secrets.token_hex(32)

        sess = UserSession(
            admin_id=admin_id,
            session_token=token,
            ip_address=ip,
            user_agent=ua_string[:512],
            device_fingerprint=fingerprint,
            two_factor_verified=two_factor_verified,
            is_active=True,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        db.session.add(sess)
        db.session.commit()
        return sess
    except Exception as e:
        logger.error(f"Failed to create managed session: {e}")
        db.session.rollback()
        return None


def invalidate_session(token: str):
    """Invalidate a specific session."""
    try:
        sess = UserSession.query.filter_by(session_token=token, is_active=True).first()
        if sess:
            sess.is_active = False
            db.session.commit()

        # Also mark login history
        LoginHistory.query.filter_by(session_token=token, logged_out_at=None).update(
            {'logged_out_at': datetime.now(timezone.utc)}
        )
        db.session.commit()
    except Exception as e:
        logger.error(f"Failed to invalidate session: {e}")
        db.session.rollback()


def invalidate_all_sessions(admin_id: int, except_token: str = None):
    """Invalidate all sessions for a user (force logout everywhere)."""
    try:
        q = UserSession.query.filter_by(admin_id=admin_id, is_active=True)
        if except_token:
            q = q.filter(UserSession.session_token != except_token)
        q.update({'is_active': False})
        db.session.commit()
    except Exception as e:
        logger.error(f"Failed to invalidate all sessions: {e}")
        db.session.rollback()


def update_session_activity(token: str):
    """Update last activity timestamp."""
    try:
        UserSession.query.filter_by(session_token=token, is_active=True).update({
            'last_activity_at': datetime.now(timezone.utc)
        })
        db.session.commit()
    except Exception:
        db.session.rollback()


def cleanup_expired_sessions():
    """Remove expired sessions (called by scheduled task)."""
    try:
        now = datetime.now(timezone.utc)
        expired = UserSession.query.filter(
            UserSession.is_active == True,
            UserSession.expires_at < now,
        ).all()
        for s in expired:
            s.is_active = False
        db.session.commit()
        logger.info(f"Cleaned up {len(expired)} expired sessions")
        return len(expired)
    except Exception as e:
        logger.error(f"Session cleanup failed: {e}")
        db.session.rollback()
        return 0


# ================== Two-Factor Authentication (TOTP-based) ==================

def _get_totp_secret(admin_id: int) -> Optional[str]:
    """Get or create TOTP secret for an admin user."""
    admin = db.session.get(AdminUser, admin_id)
    if not admin:
        return None

    # Store TOTP secret in AdminUser's existing fields.
    # We use a property-like approach: check if they have a totp_secret attribute.
    # If not, we'll store it in a JSON field or derive from existing data.
    # Since we can't modify AdminUser, we use a simple file-based store per admin.
    secret_path = os.path.join(current_app.instance_path, f'totp_secret_{admin_id}.key')
    if os.path.exists(secret_path):
        with open(secret_path, 'r') as f:
            return f.read().strip()
    return None


def _save_totp_secret(admin_id: int, secret: str):
    """Save TOTP secret for an admin user."""
    os.makedirs(current_app.instance_path, exist_ok=True)
    secret_path = os.path.join(current_app.instance_path, f'totp_secret_{admin_id}.key')
    with open(secret_path, 'w') as f:
        f.write(secret)
    os.chmod(secret_path, 0o600)


def _generate_totp_secret() -> str:
    """Generate a random base32-encoded TOTP secret."""
    return base64.b32encode(secrets.token_bytes(20)).decode('utf-8')


def _verify_totp(secret: str, code: str, window: int = 1) -> bool:
    """
    Verify a TOTP code against a secret. Allows `window` steps of drift.
    """
    try:
        code_int = int(code)
    except (ValueError, TypeError):
        return False

    key = base64.b32decode(secret.upper() + '=' * (-len(secret) % 8))
    now = int(time.time())
    for offset in range(-window, window + 1):
        counter = struct.pack('>Q', (now // 30) + offset)
        h = hmac.new(key, counter, hashlib.sha1).digest()
        o = h[19] & 0x0F
        trunc = struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF
        expected = trunc % 1000000
        if expected == code_int:
            return True
    return False


def setup_two_factor(admin_id: int) -> dict:
    """
    Initialize 2FA for an admin user.
    Returns dict with secret, provisioning_uri, and backup codes.
    """
    secret = _generate_totp_secret()
    _save_totp_secret(admin_id, secret)

    admin = db.session.get(AdminUser, admin_id)
    username = admin.username if admin else 'user'

    provisioning_uri = (
        f"otpauth://totp/IDCardGenerator:{username}"
        f"?secret={secret}&issuer=IDCardGenerator"
    )

    # Generate 8 backup codes
    backup_codes = []
    for _ in range(8):
        code = secrets.token_hex(4).upper()  # 8-char hex
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        bc = TwoFactorBackupCode(admin_id=admin_id, code_hash=code_hash)
        db.session.add(bc)
        backup_codes.append(code)

    db.session.commit()

    return {
        'secret': secret,
        'provisioning_uri': provisioning_uri,
        'backup_codes': backup_codes,
    }


def verify_two_factor(admin_id: int, code: str) -> Tuple[bool, str]:
    """
    Verify a 2FA code (TOTP or backup code).
    Returns (success, method) where method is 'totp' or 'backup'.
    """
    secret = _get_totp_secret(admin_id)

    # Try TOTP first
    if secret and _verify_totp(secret, code):
        return True, 'totp'

    # Try backup codes
    code_hash = hashlib.sha256(code.upper().encode()).hexdigest()
    bc = TwoFactorBackupCode.query.filter_by(
        admin_id=admin_id,
        code_hash=code_hash,
        used=False,
    ).first()

    if bc:
        bc.used = True
        bc.used_at = datetime.now(timezone.utc)
        db.session.commit()
        return True, 'backup'

    return False, 'none'


def is_two_factor_enabled(admin_id: int) -> bool:
    """Check if 2FA is set up for an admin."""
    secret = _get_totp_secret(admin_id)
    return secret is not None


def disable_two_factor(admin_id: int):
    """Disable 2FA for an admin user."""
    secret_path = os.path.join(current_app.instance_path, f'totp_secret_{admin_id}.key')
    if os.path.exists(secret_path):
        os.remove(secret_path)

    # Clear all backup codes
    TwoFactorBackupCode.query.filter_by(admin_id=admin_id).delete()
    db.session.commit()


def regenerate_backup_codes(admin_id: int) -> list:
    """Generate new backup codes, invalidating old ones."""
    TwoFactorBackupCode.query.filter_by(admin_id=admin_id).delete()
    codes = []
    for _ in range(8):
        code = secrets.token_hex(4).upper()
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        db.session.add(TwoFactorBackupCode(admin_id=admin_id, code_hash=code_hash))
        codes.append(code)
    db.session.commit()
    return codes
