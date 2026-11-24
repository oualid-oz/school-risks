"""CRUD for follow-up actions: the 'tracking' part of the app."""
from datetime import UTC, date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlmodel import Session, select

from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.security import (
    assigned_schools,
    get_current_user,
    record_audit,
    require_roles,
    require_school_access,
)
from app.data.store import DataStore, get_store
from app.db import get_session
from app.models.security import User, UserRole
from app.models.tracking import (
    Action,
    ActionCreate,
    ActionEvent,
    ActionEventRead,
    ActionRead,
    ActionUpdate,
    Alea,
    CommentCreate,
    Priorite,
    Statut,
)
from app.services import action_history as history

router = APIRouter(prefix="/actions", tags=["actions"])

OPEN = {Statut.a_faire, Statut.en_cours}


def _read_many(session: Session, actions: list[Action]) -> list[ActionRead]:
    """With the assignee's name and the last comment (two queries for the whole list)."""
    names = history.usernames(session, {a.responsable_user_id for a in actions if a.responsable_user_id})
    comments = history.last_comments(session, [a.id for a in actions])
    today = date.today()
    return [
        ActionRead(
            **a.model_dump(),
            en_retard=bool(a.echeance and a.echeance < today and a.statut in OPEN),
            responsable_username=names.get(a.responsable_user_id),
            dernier_commentaire=comments.get(a.id),
        )
        for a in actions
    ]


def _to_read(session: Session, a: Action) -> ActionRead:
    return _read_many(session, [a])[0]


def _check_assignee(session: Session, uai: str, user_id: int | None) -> None:
    """Actions can be assigned to an active admin or a referent of the school."""
    if user_id is None:
        return
    assignee = session.get(User, user_id)
    allowed = assignee is not None and assignee.active and (
        assignee.role == UserRole.admin
        or (assignee.role == UserRole.referent and uai in assigned_schools(session, assignee))
    )
    if not allowed:
        raise HTTPException(422, "Cette personne ne peut pas suivre les actions de ce collège")


def _notify(background: BackgroundTasks, action: Action, author: User, previous: int | None) -> None:
    if action.responsable_user_id and action.responsable_user_id not in (previous, author.id):
        background.add_task(history.notify_assignment, action.id, action.responsable_user_id, author.username)


def _get_or_404(session: Session, action_id: int) -> Action:
    action = session.get(Action, action_id)
    if action is None:
        raise HTTPException(404, f"Action {action_id} introuvable")
    return action


@router.get("", response_model=list[ActionRead])
@limiter.limit(settings.rate_limit_default)
def list_actions(
    request: Request,
    uai: str | None = None,
    alea: Alea | None = None,
    statut: Statut | None = None,
    priorite: Priorite | None = None,
    en_retard: bool | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return find_actions(session, user, uai, alea, statut, priorite, en_retard)


def find_actions(
    session: Session,
    user: User,
    uai: str | None = None,
    alea: Alea | None = None,
    statut: Statut | None = None,
    priorite: Priorite | None = None,
    en_retard: bool | None = None,
) -> list[ActionRead]:
    """Actions visible to `user` (referents: their schools only), oldest due date first.
    Shared by the list and the CSV export so both apply the same access rules."""
    stmt = select(Action)
    if user.role == UserRole.referent:
        school_uais = assigned_schools(session, user)
        if uai and uai not in school_uais:
            raise HTTPException(status_code=403, detail="Pas d’accès aux actions de ce collège")
        if not school_uais:
            return []
        stmt = stmt.where(Action.uai.in_(school_uais))
    if uai:
        stmt = stmt.where(Action.uai == uai)
    if alea:
        stmt = stmt.where(Action.alea == alea)
    if statut:
        stmt = stmt.where(Action.statut == statut)
    if priorite:
        stmt = stmt.where(Action.priorite == priorite)
    actions = _read_many(session, list(session.exec(stmt.order_by(Action.echeance)).all()))
    if en_retard is not None:
        actions = [a for a in actions if a.en_retard == en_retard]
    return actions


@router.post("", response_model=ActionRead, status_code=201)
@limiter.limit(settings.rate_limit_default)
def create_action(
    payload: ActionCreate,
    request: Request,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    store: DataStore = Depends(get_store),
    user: User = Depends(require_roles(UserRole.admin, UserRole.referent)),
):
    require_school_access(session, user, payload.uai)
    if not store.exists(payload.uai):
        raise HTTPException(422, f"UAI de collège inconnu : {payload.uai}")
    _check_assignee(session, payload.uai, payload.responsable_user_id)
    action = Action.model_validate(payload)
    session.add(action)
    session.flush()
    history.record_creation(session, action, user)
    record_audit(
        session, request, user, "action.created", "action", action.id, {"uai": action.uai}
    )
    session.commit()
    session.refresh(action)
    _notify(background, action, user, previous=None)
    return _to_read(session, action)


@router.get("/assignables")
@limiter.limit(settings.rate_limit_default)
def assignable_users(
    request: Request,
    uai: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_roles(UserRole.admin, UserRole.referent)),
):
    """People an action of this school can be assigned to (admins and its referents)."""
    require_school_access(session, user, uai)
    users = session.exec(
        select(User).where(User.active.is_(True), User.role.in_([UserRole.admin, UserRole.referent]))
        .order_by(User.username)
    ).all()
    return [
        {"id": u.id, "username": u.username, "role": u.role.value}
        for u in users
        if u.role == UserRole.admin or uai in assigned_schools(session, u)
    ]


@router.get("/{action_id}", response_model=ActionRead)
@limiter.limit(settings.rate_limit_default)
def get_action(
    action_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    action = _get_or_404(session, action_id)
    if user.role == UserRole.referent:
        require_school_access(session, user, action.uai)
    return _to_read(session, action)


@router.patch("/{action_id}", response_model=ActionRead)
@limiter.limit(settings.rate_limit_default)
def update_action(
    action_id: int,
    payload: ActionUpdate,
    request: Request,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(require_roles(UserRole.admin, UserRole.referent)),
):
    action = _get_or_404(session, action_id)
    require_school_access(session, user, action.uai)
    changes = payload.model_dump(exclude_unset=True)
    if "responsable_user_id" in changes:
        _check_assignee(session, action.uai, changes["responsable_user_id"])
    before, previous_assignee = history.snapshot(action), action.responsable_user_id
    for key, value in changes.items():
        setattr(action, key, value)
    action.updated_at = datetime.now(UTC)
    session.add(action)
    history.record_changes(session, action, before, user)
    record_audit(
        session,
        request,
        user,
        "action.updated",
        "action",
        action.id,
        {"updated_fields": sorted(changes), "uai": action.uai},
    )
    session.commit()
    session.refresh(action)
    _notify(background, action, user, previous=previous_assignee)
    return _to_read(session, action)


@router.delete("/{action_id}", status_code=204)
@limiter.limit(settings.rate_limit_default)
def delete_action(
    action_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_roles(UserRole.admin, UserRole.referent)),
):
    action = _get_or_404(session, action_id)
    require_school_access(session, user, action.uai)
    record_audit(
        session, request, user, "action.deleted", "action", action.id, {"uai": action.uai}
    )
    history.delete_events(session, action.id)
    session.delete(action)
    session.commit()
    return Response(status_code=204)


# ---- timeline and comments ------------------------------------------------------------
def _readable(session: Session, user: User, action_id: int) -> Action:
    action = _get_or_404(session, action_id)
    if user.role == UserRole.referent:
        require_school_access(session, user, action.uai)
    return action


@router.get("/{action_id}/historique", response_model=list[ActionEventRead])
@limiter.limit(settings.rate_limit_default)
def action_timeline(
    action_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Creation, changes (old → new) and comments, newest first."""
    _readable(session, user, action_id)
    return history.timeline(session, action_id, user)


@router.post("/{action_id}/commentaires", response_model=ActionEventRead, status_code=201)
@limiter.limit(settings.rate_limit_default)
def add_comment(
    action_id: int,
    payload: CommentCreate,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_roles(UserRole.admin, UserRole.referent)),
):
    """Admins and the school's referents comment (readers only read)."""
    action = _get_or_404(session, action_id)
    require_school_access(session, user, action.uai)
    if not payload.texte.strip():
        raise HTTPException(422, "Le commentaire est vide")
    event = history.add_comment(session, action, user, payload.texte)
    session.flush()
    record_audit(session, request, user, "action.commented", "action", action.id,
                 {"uai": action.uai, "event_id": event.id})
    session.commit()
    session.refresh(event)
    return history.to_read(event, user)


@router.delete("/{action_id}/commentaires/{event_id}", status_code=204)
@limiter.limit(settings.rate_limit_default)
def delete_comment(
    action_id: int,
    event_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_roles(UserRole.admin, UserRole.referent)),
):
    """Its author or an admin; the deletion (with the text) stays in the audit log."""
    action = _get_or_404(session, action_id)
    require_school_access(session, user, action.uai)
    event = session.get(ActionEvent, event_id)
    if event is None or event.action_id != action_id or event.kind != "commentaire":
        raise HTTPException(404, "Commentaire introuvable")
    if not history.can_delete(event, user):
        raise HTTPException(403, "Seuls l’auteur et les administrateurs peuvent supprimer ce commentaire")
    record_audit(session, request, user, "action.comment_deleted", "action", action.id,
                 {"uai": action.uai, "event_id": event.id, "author": event.author_username,
                  "texte": event.texte})
    session.delete(event)
    session.commit()
    return Response(status_code=204)
