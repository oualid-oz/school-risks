"""Retention of personal data (GDPR): records past their retention period are deleted.

- Audit log (usernames, IP addresses, logins, the text of deleted comments):
  SRT_AUDIT_RETENTION_MONTHS (default 12, the usual length for access logs).
- Email notification log (recipient addresses): SRT_NOTIFICATION_RETENTION_MONTHS (default 6;
  it only prevents sending the same weekly digest twice).

Accounts, actions and their comments are working records: they stay as long as the
account or the action exists (an administrator deactivates accounts that are no longer used).
Run daily or hourly (`python -m app.manage purge`, the Docker scheduler, the Vercel cron).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlmodel import Session

from app.core.config import settings
from app.models.notifications import NotificationLog
from app.models.security import AuditLog

logger = logging.getLogger(__name__)


def _months_ago(now: datetime, months: int) -> datetime:
    return now - timedelta(days=round(months * 30.44))


def purge(session: Session, now: datetime | None = None) -> dict[str, int]:
    """Delete what is past its retention period. Returns the number of rows deleted."""
    now = now or datetime.now(UTC)
    audit = session.execute(
        delete(AuditLog).where(AuditLog.created_at < _months_ago(now, settings.audit_retention_months))
    ).rowcount
    notifications = session.execute(
        delete(NotificationLog).where(
            NotificationLog.created_at < _months_ago(now, settings.notification_retention_months)
        )
    ).rowcount
    session.commit()
    result = {"audit_log": audit, "notification_log": notifications}
    if audit or notifications:
        logger.info("retention purge", extra=result)
    return result
