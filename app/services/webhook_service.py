"""
Webhook Service
Manages webhook endpoints, event triggering, and delivery tracking.
Fully isolated - uses WebhookEndpoint and WebhookDelivery models from models.py.
"""
import os
import json
import hmac
import hashlib
import logging
import time as _time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import requests

from models import db, WebhookEndpoint, WebhookDelivery

logger = logging.getLogger(__name__)

# Available webhook events
WEBHOOK_EVENTS = [
    'student.created',
    'student.updated',
    'student.deleted',
    'student.card_generated',
    'template.created',
    'template.updated',
    'template.approved',
    'bulk_job.started',
    'bulk_job.completed',
    'bulk_job.failed',
    'verification.scan',
    'verification.failed',
    'admin.login',
    'admin.failed_login',
]

_executor = ThreadPoolExecutor(max_workers=4)


def _sign_payload(payload: str, secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload."""
    return hmac.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()


def create_webhook(organization_id: int, name: str, url: str,
                   events: list, secret: str = None, created_by: str = None) -> WebhookEndpoint:
    """Register a new webhook endpoint."""
    if not secret:
        secret = os.urandom(32).hex()

    webhook = WebhookEndpoint(
        organization_id=organization_id,
        name=name,
        url=url,
        secret=secret,
        events=events,
        created_by=created_by,
    )
    db.session.add(webhook)
    db.session.commit()
    return webhook


def trigger_event(organization_id: int, event_type: str, payload: dict):
    """
    Trigger a webhook event to all registered endpoints for an organization.
    Queues delivery via thread pool for non-blocking operation.
    """
    endpoints = WebhookEndpoint.query.filter_by(
        organization_id=organization_id,
        is_active=True,
    ).all()

    matching = [e for e in endpoints if event_type in (e.events or [])]

    for endpoint in matching:
        _executor.submit(_deliver_webhook, endpoint.id, event_type, payload)


def _deliver_webhook(webhook_id: int, event_type: str, payload: dict):
    """Deliver a webhook payload with retry logic."""
    endpoint = db.session.get(WebhookEndpoint, webhook_id)
    if not endpoint or not endpoint.is_active:
        return

    payload_str = json.dumps(payload, default=str, separators=(',', ':'))
    signature = _sign_payload(payload_str, endpoint.secret or '')

    headers = {
        'Content-Type': 'application/json',
        'X-Webhook-Signature': f'sha256={signature}',
        'X-Webhook-Event': event_type,
        'X-Webhook-Timestamp': datetime.now(timezone.utc).isoformat(),
    }

    delivery = WebhookDelivery(
        webhook_id=webhook_id,
        event_type=event_type,
        payload_json=payload,
    )
    db.session.add(delivery)

    start = _time.time()
    try:
        resp = requests.post(
            endpoint.url,
            data=payload_str,
            headers=headers,
            timeout=10,
        )
        delivery.status_code = resp.status_code
        delivery.response_body = resp.text[:1000]
        delivery.delivered = 200 <= resp.status_code < 300

        if not delivery.delivered:
            delivery.retry_count += 1
            endpoint.failure_count += 1
        else:
            endpoint.last_triggered_at = datetime.now(timezone.utc)
            endpoint.last_status_code = resp.status_code

    except Exception as e:
        delivery.error_message = str(e)[:500]
        delivery.retry_count += 1
        endpoint.failure_count += 1

    delivery.response_time_ms = round((_time.time() - start) * 1000, 2)
    db.session.commit()


def get_delivery_stats(webhook_id: int) -> dict:
    """Get delivery statistics for a webhook endpoint."""
    total = WebhookDelivery.query.filter_by(webhook_id=webhook_id).count()
    delivered = WebhookDelivery.query.filter_by(webhook_id=webhook_id, delivered=True).count()
    failed = total - delivered

    avg_time = db.session.query(
        db.func.avg(WebhookDelivery.response_time_ms)
    ).filter_by(webhook_id=webhook_id).scalar()

    return {
        'total_deliveries': total,
        'delivered': delivered,
        'failed': failed,
        'success_rate': round(delivered / total * 100, 1) if total else 0,
        'avg_response_time_ms': round(float(avg_time or 0), 2),
    }
