"""Error reporting (Sentry), off unless SENTRY_DSN / SRT_SENTRY_DSN is set.

Only errors are sent (no performance tracing), and never personal data: request bodies
(passwords, comments), tokens, cookies and client IPs are removed before sending.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)
SENSITIVE_HEADERS = {"authorization", "cookie", "x-forwarded-for", "x-real-ip", "x-request-id"}


def scrub(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    request = event.get("request") or {}
    request.pop("data", None)  # body: passwords, comments, emails
    request.pop("cookies", None)
    request.pop("env", None)  # REMOTE_ADDR
    headers = request.get("headers")
    if isinstance(headers, dict):
        request["headers"] = {k: v for k, v in headers.items() if k.lower() not in SENSITIVE_HEADERS}
    if event.get("user"):
        event["user"] = {"id": event["user"].get("id")} if event["user"].get("id") else {}
    return event


def init_error_reporting(component: str = "api") -> bool:
    """Call once per process (API, CLI). Returns True when reporting is on."""
    if not settings.sentry_dsn:
        return False
    import sentry_sdk

    environment = settings.sentry_environment or (
        f"vercel-{os.environ.get('VERCEL_ENV', 'unknown')}" if settings.on_vercel else "production"
    )
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=environment,
        release=settings.build_version,
        send_default_pii=False,
        traces_sample_rate=0.0,
        before_send=scrub,
    )
    sentry_sdk.set_tag("component", component)
    logger.info("error reporting on", extra={"environment": environment, "component": component})
    return True
