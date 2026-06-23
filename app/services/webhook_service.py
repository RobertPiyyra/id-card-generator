"""
Webhook service: registration, dispatch, delivery with HMAC signatures.

Provides webhook lifecycle management and asynchronous event delivery
to registered endpoints with retry logic and delivery logging.
"""

import hashlib
import hmac
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from flask import current_app

from models import (
    ApiKey,
    db,
    WebhookDelivery,
    WebhookEndpoint,
)

logger = logging.getLogger(__name__)

# Supported webhook events
WEBHOOK_EVENTS = [
    "student.created",
    "student.updated",
    "student.deleted",
    "card.generated",
    "card.printed",
    "bulk.started",
    "bulk.completed",
    "bulk.failed",
    "template.created",
    "template.updated",
]

# Thread pool for async webhook delivery
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="webhook")

# Retry configuration
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2  # seconds: 2, 4, 8

# Request timeout for webhook POST
_REQUEST_TIMEOUT = 10  # seconds


def register_webhook(
    url: str,
    events: List[str],
    secret: str,
    name: str = "",
    organization_id: int = None,
    created_by: str = None,
) -> WebhookEndpoint:
    """
    Register a new webhook endpoint.

    Args:
        url: The HTTPS URL to deliver events to.
        events: List of event types to subscribe to.
    secret: Secret key for HMAC-SHA256 signature generation.
        name: Optional human-readable name for the endpoint.
        organization_id: Organization that owns this webhook.
        created_by: Identifier of the creator.

    Returns:
        The created WebhookEndpoint instance.

    Raises:
        ValueError: If url is empty or events contains invalid types.
    """
    if not url:
        raise ValueError("Webhook URL is required")
    if not events:
        raise ValueError("At least one event is required")

    invalid = [e for e in events if e not in WEBHOOK_EVENTS]
    if invalid:
        raise ValueError(f"Invalid event types: {invalid}")

    webhook = WebhookEndpoint(
        organization_id=organization_id,
        name=name or url,
        url=url,
        secret=secret,
        events=events,
        is_active=True,
        failure_count=0,
        created_by=created_by,
    )
    db.session.add(webhook)
    db.session.commit()
    logger.info("Registered webhook %d for %s (events: %s)", webhook.id, url, events)
    return webhook


def list_webhooks(
    organization_id: int = None,
    active_only: bool = False,
) -> List[WebhookEndpoint]:
    """
    List webhook endpoints, optionally filtered by organization and active status.

    Args:
        organization_id: Filter by organization.
        active_only: If True, return only active webhooks.

    Returns:
        List of WebhookEndpoint instances.
    """
    query = WebhookEndpoint.query
    if organization_id is not None:
        query = query.filter_by(organization_id=organization_id)
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(WebhookEndpoint.created_at.desc()).all()


def delete_webhook(webhook_id: int, organization_id: int = None) -> bool:
    """
    Delete a webhook endpoint.

    Args:
        webhook_id: ID of the webhook to delete.
        organization_id: If provided, only delete if owned by this org.

    Returns:
        True if deleted, False if not found.
    """
    query = WebhookEndpoint.query.filter_by(id=webhook_id)
    if organization_id is not None:
        query = query.filter_by(organization_id=organization_id)
    webhook = query.first()
    if webhook is None:
        return False
    db.session.delete(webhook)
    db.session.commit()
    logger.info("Deleted webhook %d", webhook_id)
    return True


def get_delivery_log(
    webhook_id: int,
    limit: int = 50,
    offset: int = 0,
) -> List[WebhookDelivery]:
    """
    Get delivery history for a webhook endpoint.

    Args:
        webhook_id: ID of the webhook.
        limit: Maximum number of records to return.
        offset: Number of records to skip.

    Returns:
        List of WebhookDelivery instances, newest first.
    """
    return (
        WebhookDelivery.query.filter_by(webhook_id=webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def trigger_event(
    event_type: str,
    payload: Dict[str, Any],
    organization_id: int = None,
) -> None:
    """
    Dispatch a webhook event to all matching registered endpoints.

    Finds all active webhooks subscribed to the given event and
    dispatches them asynchronously via ThreadPoolExecutor.

    Args:
        event_type: The event type string (e.g. 'student.created').
        payload: The event payload data.
        organization_id: If provided, only dispatch to this org's webhooks.
    """
    query = WebhookEndpoint.query.filter_by(is_active=True)
    if organization_id is not None:
        query = query.filter_by(organization_id=organization_id)
    webhooks = query.all()

    matching = [wh for wh in webhooks if event_type in (wh.events or [])]
    if not matching:
        return

    logger.info(
        "Triggering event '%s' to %d webhook(s)", event_type, len(matching)
    )

    for webhook in matching:
        _executor.submit(_deliver_with_retry, webhook.id, event_type, payload)


def dispatch_webhook_event(event_type: str, data: dict) -> None:
    """
    Public entry point used by the event_bus to dispatch webhook events.

    This is the function called by the event_bus _webhook_handler.
    It delegates to trigger_event for actual processing.

    Args:
        event_type: The event type string.
        data: The event data dictionary.
    """
    try:
        trigger_event(event_type, data)
    except Exception:
        logger.exception("dispatch_webhook_event failed for %s", event_type)


def _deliver_with_retry(
    webhook_id: int,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    """
    Deliver a webhook with exponential backoff retry.

    Attempts delivery up to _MAX_RETRIES times with exponential backoff
    (2s, 4s, 8s). Each attempt is logged as a WebhookDelivery record.

    Args:
        webhook_id: ID of the webhook endpoint.
        event_type: The event type string.
        payload: The event payload data.
    """
    for attempt in range(_MAX_RETRIES + 1):
        delivery = deliver_webhook(webhook_id, event_type, payload, attempt)
        if delivery and delivery.delivered:
            return
        if attempt < _MAX_RETRIES:
            wait = _RETRY_BACKOFF_BASE ** (attempt + 1)
            logger.warning(
                "Webhook %d delivery failed (attempt %d/%d), retrying in %ds",
                webhook_id,
                attempt + 1,
                _MAX_RETRIES + 1,
                wait,
            )
            time.sleep(wait)

    logger.error(
        "Webhook %d delivery failed after %d attempts", webhook_id, _MAX_RETRIES + 1
    )


def deliver_webhook(
    webhook_id: int,
    event_type: str,
    payload: Dict[str, Any],
    retry_count: int = 0,
) -> Optional[WebhookDelivery]:
    """
    Send an HTTP POST to the webhook URL with HMAC-SHA256 signature.

    Constructs a JSON payload with event type, timestamp, and data,
    signs it with the webhook's secret using HMAC-SHA256, and sends
    it as a POST request. Creates a WebhookDelivery record for the attempt.

    Args:
        webhook_id: ID of the webhook endpoint.
        event_type: The event type string.
        payload: The event payload data.
        retry_count: Current retry attempt number.

    Returns:
        The created WebhookDelivery instance, or None if webhook not found.
    """
    # Use a new app context since this runs in a thread
    from app import create_app

    app = create_app()
    with app.app_context():
        webhook = db.session.get(WebhookEndpoint, webhook_id)
        if webhook is None:
            logger.error("Webhook %d not found", webhook_id)
            return None

        # Build the delivery envelope
        timestamp = datetime.now(timezone.utc).isoformat()
        body = json.dumps(
            {
                "event": event_type,
                "timestamp": timestamp,
                "data": payload,
            },
            default=str,
        )

        # Compute HMAC-SHA256 signature
        signature = _compute_signature(webhook.secret or "", body)

        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"sha256={signature}",
            "X-Webhook-Event": event_type,
            "X-Webhook-Timestamp": timestamp,
            "User-Agent": "IDCardGenerator-Webhook/1.0",
        }

        delivery = WebhookDelivery(
            webhook_id=webhook_id,
            event_type=event_type,
            payload_json=json.loads(body),
            retry_count=retry_count,
        )

        try:
            response = requests.post(
                webhook.url,
                data=body,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            )
            delivery.status_code = response.status_code
            delivery.response_body = response.text[:2000]  # Cap response storage
            delivery.delivered = 200 <= response.status_code < 300
            delivery.error_message = None

            # Update webhook status fields
            webhook.last_triggered_at = datetime.now(timezone.utc)
            webhook.last_status_code = response.status_code
            if not delivery.delivered:
                webhook.failure_count = (webhook.failure_count or 0) + 1
            else:
                webhook.failure_count = 0

            logger.info(
                "Webhook %d delivered: event=%s status=%d attempt=%d",
                webhook_id,
                event_type,
                response.status_code,
                retry_count,
            )

        except requests.RequestException as exc:
            delivery.status_code = None
            delivery.response_body = None
            delivery.delivered = False
            delivery.error_message = str(exc)[:1000]
            webhook.failure_count = (webhook.failure_count or 0) + 1
            logger.warning(
                "Webhook %d delivery error: event=%s attempt=%d error=%s",
                webhook_id,
                event_type,
                retry_count,
                exc,
            )

        db.session.add(delivery)
        db.session.commit()
        return delivery


def _compute_signature(secret: str, body: str) -> str:
    """
    Compute HMAC-SHA256 signature for a webhook payload.

    Args:
        secret: The webhook secret key.
        body: The raw request body string.

    Returns:
        Hex-encoded HMAC-SHA256 signature.
    """
    return hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_webhook_secret() -> str:
    """
    Generate a cryptographically random webhook secret.

    Returns:
        A 48-character hex string (256 bits of entropy).
    """
    import secrets

    return secrets.token_hex(24)
# Backward-compatible aliases (used by enterprise_routes.py)
create_webhook = register_webhook
get_delivery_stats = get_delivery_log
