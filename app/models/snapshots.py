"""Yearly snapshot of each school's risk scores, to follow how they change over time."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ScoreSnapshot(SQLModel, table=True):
    __tablename__ = "score_snapshot"
    __table_args__ = (UniqueConstraint("annee", "uai"),)

    id: int | None = Field(default=None, primary_key=True)
    annee: int = Field(index=True)
    uai: str = Field(index=True, max_length=8)
    score_global: float
    niveau: str = Field(max_length=10)
    alea_principal: str = Field(max_length=30)
    # Pupils and priority index at snapshot time (empty for snapshots taken before Sprint 8).
    effectif_total: int | None = None
    indice_priorite: float | None = None
    aleas: str  # JSON {hazard: score}
    poids: str  # JSON {hazard: weight} used for the global score
    source_sha256: str = Field(max_length=64)  # hash of the risk file the scores came from
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ScoreSnapshotRead(SQLModel):
    annee: int
    score_global: float
    niveau: str
    alea_principal: str
    effectif_total: int | None
    indice_priorite: float | None
    aleas: dict[str, float]
    created_at: datetime
