"""Record of every email digest, one row per user, kind and period (idempotency)."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class NotificationLog(SQLModel, table=True):
    """The unique constraint is what stops a second run (or two overlapping runs) from
    emailing the same person twice in the same week: a run claims the row before sending."""

    __tablename__ = "notification_log"
    __table_args__ = (UniqueConstraint("user_id", "kind", "period"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="app_user.id", index=True)
    kind: str = Field(max_length=40)  # "digest_hebdo"
    period: str = Field(max_length=20, index=True)  # ISO week, e.g. "2026-W48"
    status: str = Field(max_length=20)  # sending | sent | failed
    recipient: str = Field(max_length=254)
    error: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
