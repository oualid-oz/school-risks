"""CRUD for follow-up actions: the 'tracking' part of the app."""
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
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
    ActionRead,
    ActionUpdate,
    Alea,
    Priorite,
    Statut,
)

router = APIRouter(prefix="/actions", tags=["actions"])

OPEN = {Statut.a_faire, Statut.en_cours}


def _to_read(a: Action) -> ActionRead:
    late = bool(a.echeance and a.echeance < date.today() and a.statut in OPEN)
    return ActionRead(**a.model_dump(), en_retard=late)


def _get_or_404(session: Session, action_id: int) -> Action:
    action = session.get(Action, action_id)
    if action is None:
        raise HTTPException(404, f"Action {action_id} not found")
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
    stmt = select(Action)
    if user.role == UserRole.referent:
        school_uais = assigned_schools(session, user)
        if uai and uai not in school_uais:
            raise HTTPException(status_code=403, detail="No access to actions for this school")
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
    actions = [_to_read(a) for a in session.exec(stmt.order_by(Action.echeance)).all()]
    if en_retard is not None:
        actions = [a for a in actions if a.en_retard == en_retard]
    return actions


@router.post("", response_model=ActionRead, status_code=201)
@limiter.limit(settings.rate_limit_default)
def create_action(
    payload: ActionCreate,
    request: Request,
    session: Session = Depends(get_session),
    store: DataStore = Depends(get_store),
    user: User = Depends(require_roles(UserRole.admin, UserRole.referent)),
):
    require_school_access(session, user, payload.uai)
    if not store.exists(payload.uai):
        raise HTTPException(422, f"Unknown college UAI {payload.uai}")
    action = Action.model_validate(payload)
    session.add(action)
    session.flush()
    record_audit(
        session, request, user, "action.created", "action", action.id, {"uai": action.uai}
    )
    session.commit()
    session.refresh(action)
    return _to_read(action)


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
    return _to_read(action)


@router.patch("/{action_id}", response_model=ActionRead)
@limiter.limit(settings.rate_limit_default)
def update_action(
    action_id: int,
    payload: ActionUpdate,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_roles(UserRole.admin, UserRole.referent)),
):
    action = _get_or_404(session, action_id)
    require_school_access(session, user, action.uai)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(action, key, value)
    action.updated_at = datetime.now(UTC)
    session.add(action)
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
    return _to_read(action)


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
    session.delete(action)
    session.commit()
    return Response(status_code=204)
