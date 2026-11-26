"""Data error reports (Sprint 22): the feedback loop with the departments.

Anyone who sees a school can report a data error on it (position, enrolment, sector, a
risk value…). Administrators of its area close the report with an answer; corrections
themselves go through the data pipeline (the published files are rebuilt from the sources).
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class ChampSignale(str, Enum):
    position = "position"
    identite = "identite"  # name, status, commune
    effectif = "effectif"
    secteur = "secteur"
    risque = "risque"
    autre = "autre"


class StatutSignalement(str, Enum):
    ouvert = "ouvert"
    traite = "traite"  # corrected, or a correction requested from the source
    rejete = "rejete"  # the data was right


class DataReport(SQLModel, table=True):
    __tablename__ = "data_report"

    id: int | None = Field(default=None, primary_key=True)
    uai: str = Field(max_length=8, index=True)
    departement: str | None = Field(default=None, max_length=3, index=True)
    champ: ChampSignale = ChampSignale.autre
    message: str = Field(max_length=2000)
    statut: StatutSignalement = Field(default=StatutSignalement.ouvert, index=True)
    reponse: str | None = Field(default=None, max_length=2000)
    created_by_user_id: int | None = Field(default=None, foreign_key="app_user.id")
    created_by_username: str = Field(max_length=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    closed_by_username: str | None = Field(default=None, max_length=100)
    closed_at: datetime | None = None


class DataReportCreate(SQLModel):
    champ: ChampSignale = ChampSignale.autre
    message: str = Field(min_length=5, max_length=2000)


class DataReportClose(SQLModel):
    statut: StatutSignalement
    reponse: str | None = Field(default=None, max_length=2000)


class DataReportRead(SQLModel):
    id: int
    uai: str
    nom: str | None = None
    departement: str | None
    champ: ChampSignale
    message: str
    statut: StatutSignalement
    reponse: str | None
    created_by_username: str
    created_at: datetime
    closed_by_username: str | None
    closed_at: datetime | None
