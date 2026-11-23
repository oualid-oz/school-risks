"""PPMS (Plan particulier de mise en sûreté): plan updates and drills, entered in the app.

There is no open dataset for PPMS, so referents record them. Every entry is kept (history);
the status is computed from the latest ones (app/services/ppms.py).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, field_validator
from sqlmodel import Field, SQLModel


class TypeExercice(str, Enum):
    risques_majeurs = "risques_majeurs"
    intrusion = "intrusion"
    incendie = "incendie"


def _now() -> datetime:
    return datetime.now(UTC)


class PpmsRevision(SQLModel, table=True):
    """One update of a school's PPMS document."""

    __tablename__ = "ppms_revision"

    id: int | None = Field(default=None, primary_key=True)
    uai: str = Field(index=True, max_length=8)
    date_mise_a_jour: date = Field(index=True)
    commentaire: str | None = Field(default=None, max_length=1000)
    created_by_user_id: int | None = Field(default=None, foreign_key="app_user.id")
    created_by_username: str = Field(max_length=100)
    created_at: datetime = Field(default_factory=_now)


class PpmsExercice(SQLModel, table=True):
    """One drill held at a school."""

    __tablename__ = "ppms_exercice"

    id: int | None = Field(default=None, primary_key=True)
    uai: str = Field(index=True, max_length=8)
    type_exercice: TypeExercice
    date_exercice: date = Field(index=True)
    commentaire: str | None = Field(default=None, max_length=1000)
    created_by_user_id: int | None = Field(default=None, foreign_key="app_user.id")
    created_by_username: str = Field(max_length=100)
    created_at: datetime = Field(default_factory=_now)


# ---- API schemas -----------------------------------------------------------------
EARLIEST = date(2000, 1, 1)


def _check_past(value: date) -> date:
    if value > date.today():
        raise ValueError("la date ne peut pas être dans le futur")
    if value < EARLIEST:
        raise ValueError(f"la date doit être postérieure au {EARLIEST:%d/%m/%Y}")
    return value


class RevisionCreate(SQLModel):
    date_mise_a_jour: date
    commentaire: str | None = Field(default=None, max_length=1000)

    _past = field_validator("date_mise_a_jour")(_check_past)


class ExerciceCreate(SQLModel):
    type_exercice: TypeExercice
    date_exercice: date
    commentaire: str | None = Field(default=None, max_length=1000)

    _past = field_validator("date_exercice")(_check_past)


class RevisionRead(BaseModel):
    id: int
    date_mise_a_jour: date
    commentaire: str | None
    created_by_username: str
    created_at: datetime


class ExerciceRead(BaseModel):
    id: int
    type_exercice: TypeExercice
    date_exercice: date
    commentaire: str | None
    created_by_username: str
    created_at: datetime


StatutPpms = Literal["a_jour", "ancien", "absent"]


class PpmsStatus(BaseModel):
    statut: StatutPpms
    derniere_mise_a_jour: date | None
    prochaine_mise_a_jour: date | None  # last update + SRT_PPMS_MAX_AGE_MONTHS
    annee_scolaire: str  # e.g. "2026-2027"
    exercices_annee: dict[TypeExercice, date | None]  # latest drill of each type this school year
    raisons: list[str]  # why it is not "a_jour" (French, shown as alerts)


class PpmsDetail(BaseModel):
    uai: str
    statut: PpmsStatus
    revisions: list[RevisionRead]
    exercices: list[ExerciceRead]
