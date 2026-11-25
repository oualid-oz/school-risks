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
    seisme = "seisme"
    radon = "radon"
    feu_foret = "feu_foret"
    littoral = "littoral"
    avalanche = "avalanche"
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
    # Optional app user in charge (an admin or a referent of the school). `responsable`
    # stays free text for people without an account.
    responsable_user_id: int | None = Field(
        default=None, foreign_key="app_user.id", ondelete="SET NULL", index=True
    )
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ActionEvent(SQLModel, table=True):
    """Timeline of an action: creation, field changes (old → new) and comments."""

    __tablename__ = "action_event"

    id: int | None = Field(default=None, primary_key=True)
    action_id: int = Field(foreign_key="action.id", ondelete="CASCADE", index=True)
    kind: str = Field(max_length=20)  # creation | modification | commentaire
    changes: str | None = None  # JSON [{"champ", "avant", "apres"}]; null values = not kept
    texte: str | None = Field(default=None, max_length=2000)
    author_user_id: int | None = Field(default=None, foreign_key="app_user.id", ondelete="SET NULL")
    author_username: str = Field(max_length=100)
    created_at: datetime = Field(default_factory=_now, index=True)


class ActionCreate(ActionBase):
    uai: str = Field(min_length=8, max_length=8)
    responsable_user_id: int | None = None


class ActionUpdate(SQLModel):
    titre: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = None
    alea: Alea | None = None
    priorite: Priorite | None = None
    statut: Statut | None = None
    responsable: str | None = None
    echeance: date | None = None
    responsable_user_id: int | None = None


class CommentSummary(SQLModel):
    texte: str
    author_username: str
    created_at: datetime


class ActionRead(ActionBase):
    id: int
    uai: str
    created_at: datetime
    updated_at: datetime
    en_retard: bool = False
    responsable_user_id: int | None = None
    responsable_username: str | None = None
    dernier_commentaire: CommentSummary | None = None


class CommentCreate(SQLModel):
    texte: str = Field(min_length=1, max_length=2000)


class ChangeRead(SQLModel):
    champ: str
    avant: str | None
    apres: str | None


class ActionEventRead(SQLModel):
    id: int
    kind: str
    changes: list[ChangeRead] | None
    texte: str | None
    author_username: str
    created_at: datetime
    deletable: bool = False  # a comment the current user may delete
