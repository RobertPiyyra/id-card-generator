"""
Sentry error tracking integration.
Call init_sentry() once at application startup.
Requires: pip install sentry-sdk[flask]
"""
import logging
import os

logger = logging.getLogger(__name__)


def init_sentry(app):
    """
    Initialize Sentry SDK for error tracking.

    Environment variables:
        SENTRY_DSN          — Sentry DSN (required to enable)
        SENTRY_ENVIRONMENT  — environment name (default: production)
        SENTRY_TRACES_SAMPLE_RATE — performance trace sample rate (default: 0.1)
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("Sentry not configured (SENTRY_DSN not set)")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.redis import RedisIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            integrations=[
                FlaskIntegration(),
                SqlalchemyIntegration(),
                RedisIntegration(),
            ],
            send_default_pii=False,
            max_breadcrumbs=50,
            attach_stacktrace=True,
        )
        logger.info("Sentry initialized (env=%s)", os.environ.get("SENTRY_ENVIRONMENT", "production"))
    except ImportError:
        logger.warning("sentry-sdk not installed — error tracking disabled")
    except Exception as exc:
        logger.error("Sentry initialization failed: %s", exc)
