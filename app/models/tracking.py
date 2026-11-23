"""Tracking tables: the follow-up actions people create for each school and hazard."""
from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class Alea(str, Enum):
    inondation = "inondation"
    argiles = "argiles"
    mouvements_terrain = "mouvements_terrain"
    industriel = "industriel"
    tmd = "tmd"
    nucleaire = "nucleaire"
    general = "general"


class Statut(str, Enum):
    a_faire = "a_faire"
    en_cours = "en_cours"
    termine = "termine"
    annule = "annule"


class Priorite(str, Enum):
    basse = "basse"
    normale = "normale"
    haute = "haute"
    critique = "critique"


def _now() -> datetime:
    return datetime.now(UTC)


class ActionBase(SQLModel):
    titre: str = Field(min_length=3, max_length=200)
    description: str | None = None
    alea: Alea = Alea.general
    priorite: Priorite = Priorite.normale
    statut: Statut = Statut.a_faire
    responsable: str | None = None
    echeance: date | None = None


class Action(ActionBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    uai: str = Field(index=True, max_length=8)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ActionCreate(ActionBase):
    uai: str = Field(min_length=8, max_length=8)


class ActionUpdate(SQLModel):
    titre: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = None
    alea: Alea | None = None
    priorite: Priorite | None = None
    statut: Statut | None = None
    responsable: str | None = None
    echeance: date | None = None


class ActionRead(ActionBase):
    id: int
    uai: str
    created_at: datetime
    updated_at: datetime
    en_retard: bool = False
