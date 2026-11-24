"""Action timeline (creation, field changes, comments) and assignment emails."""
from __future__ import annotations

import json
import logging
from datetime import date
from enum import Enum
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import settings
from app.models.security import User, UserRole
from app.models.tracking import (
    Action,
    ActionEvent,
    ActionEventRead,
    ChangeRead,
    CommentSummary,
)

logger = logging.getLogger(__name__)

TRACKED_FIELDS = ("titre", "description", "alea", "priorite", "statut", "responsable", "echeance")


def _plain(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def snapshot(action: Action) -> dict[str, Any]:
    """Values compared before and after an update."""
    return {**{f: getattr(action, f) for f in TRACKED_FIELDS}, "assigne": action.responsable_user_id}


def _username(session: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    user = session.get(User, user_id)
    return user.username if user else f"#{user_id}"


def record_creation(session: Session, action: Action, author: User) -> None:
    session.add(ActionEvent(
        action_id=action.id, kind="creation", author_user_id=author.id, author_username=author.username,
    ))


def record_changes(session: Session, action: Action, before: dict[str, Any], author: User) -> list[dict]:
    """One "modification" event listing the fields whose value really changed."""
    after = snapshot(action)
    changes = []
    for field, old in before.items():
        new = after[field]
        if _plain(old) == _plain(new):
            continue
        if field == "assigne":
            old, new = _username(session, old), _username(session, new)
        changes.append({"champ": field, "avant": _plain(old), "apres": _plain(new)})
    if changes:
        session.add(ActionEvent(
            action_id=action.id, kind="modification", changes=json.dumps(changes, ensure_ascii=False),
            author_user_id=author.id, author_username=author.username,
        ))
    return changes


def add_comment(session: Session, action: Action, author: User, texte: str) -> ActionEvent:
    event = ActionEvent(
        action_id=action.id, kind="commentaire", texte=texte.strip(),
        author_user_id=author.id, author_username=author.username,
    )
    session.add(event)
    return event


def can_delete(event: ActionEvent, user: User) -> bool:
    return event.kind == "commentaire" and (user.role == UserRole.admin or event.author_user_id == user.id)


def to_read(event: ActionEvent, user: User) -> ActionEventRead:
    changes = json.loads(event.changes) if event.changes else None
    return ActionEventRead(
        id=event.id, kind=event.kind, texte=event.texte, author_username=event.author_username,
        created_at=event.created_at, deletable=can_delete(event, user),
        changes=[ChangeRead(**c) for c in changes] if changes is not None else None,
    )


def timeline(session: Session, action_id: int, user: User) -> list[ActionEventRead]:
    events = session.exec(
        select(ActionEvent).where(ActionEvent.action_id == action_id)
        .order_by(ActionEvent.created_at.desc(), ActionEvent.id.desc())
    ).all()
    return [to_read(e, user) for e in events]


def last_comments(session: Session, action_ids: list[int]) -> dict[int, CommentSummary]:
    """Latest comment of each action, in one query."""
    if not action_ids:
        return {}
    latest = (
        select(ActionEvent.action_id, func.max(ActionEvent.id).label("id"))
        .where(ActionEvent.action_id.in_(action_ids), ActionEvent.kind == "commentaire")
        .group_by(ActionEvent.action_id)
        .subquery()
    )
    rows = session.exec(select(ActionEvent).join(latest, ActionEvent.id == latest.c.id)).all()
    return {
        e.action_id: CommentSummary(texte=e.texte, author_username=e.author_username, created_at=e.created_at)
        for e in rows
    }


def usernames(session: Session, user_ids: set[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    return dict(session.exec(select(User.id, User.username).where(User.id.in_(user_ids))).all())


def delete_events(session: Session, action_id: int) -> None:
    """With the action (SQLite does not enforce ON DELETE CASCADE)."""
    for event in session.exec(select(ActionEvent).where(ActionEvent.action_id == action_id)).all():
        session.delete(event)


# ---- assignment email --------------------------------------------------------------
def notify_assignment(action_id: int, assignee_id: int, assigned_by: str) -> None:
    """Background task: email the person an action was just assigned to, if they have an
    address and email alerts on (same opt-in as the weekly digest)."""
    from app import db
    from app.data.store import get_store
    from app.services.mailer import Email, get_mailer

    try:
        with Session(db.engine) as session:
            action, user = session.get(Action, action_id), session.get(User, assignee_id)
            if not (action and user and user.active and user.email and user.alertes_email):
                return
            school = (get_store().get_college(action.uai) or {}).get("nom", action.uai)
            link = f"{settings.public_url.rstrip('/')}/#school/{action.uai}"
            due = f"Échéance : {action.echeance:%d/%m/%Y}\n" if action.echeance else ""
            email = Email(
                to=user.email,
                subject=f"[Risques collèges] Action assignée : {action.titre}",
                text=(
                    f"Bonjour {user.username},\n\n{assigned_by} vous a assigné une action de suivi.\n\n"
                    f"{action.titre}\nCollège : {school} ({action.uai})\n{due}"
                    f"Priorité : {action.priorite.value}\n\n{link}\n\n"
                    "Vous recevez ce message car les alertes e-mail sont activées sur votre compte "
                    "(« Mon compte » pour les désactiver)."
                ),
            )
        with get_mailer() as mailer:
            mailer.send(email)
    except Exception:  # noqa: BLE001 - never fail the request that triggered it
        logger.exception("assignment email not sent", extra={"action_id": action_id})
