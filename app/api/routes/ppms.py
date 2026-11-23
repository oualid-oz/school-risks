"""PPMS of a school: status, plan updates and drills.

Everyone signed in can read. Admins and the school's referents record and delete entries;
every change is written to the audit log.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import Session, select

from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.security import get_current_user, record_audit, require_roles, require_school_access
from app.data.store import DataStore, get_store
from app.db import get_session
from app.models.ppms import (
    ExerciceCreate,
    ExerciceRead,
    PpmsDetail,
    PpmsExercice,
    PpmsRevision,
    RevisionCreate,
    RevisionRead,
)
from app.models.security import User, UserRole
from app.services.ppms import status_for

router = APIRouter(
    prefix="/colleges/{uai}/ppms", tags=["ppms"], dependencies=[Depends(get_current_user)]
)
editor = require_roles(UserRole.admin, UserRole.referent)


def _known(store: DataStore, uai: str) -> None:
    if not store.exists(uai):
        raise HTTPException(404, f"Collège {uai} introuvable")


@router.get("", response_model=PpmsDetail)
@limiter.limit(settings.rate_limit_default)
def get_ppms(
    request: Request,
    uai: str,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
):
    """Status (computed), plan updates and drills, newest first."""
    _known(store, uai)
    revisions = session.exec(
        select(PpmsRevision)
        .where(PpmsRevision.uai == uai)
        .order_by(PpmsRevision.date_mise_a_jour.desc(), PpmsRevision.id.desc())
    ).all()
    exercices = session.exec(
        select(PpmsExercice)
        .where(PpmsExercice.uai == uai)
        .order_by(PpmsExercice.date_exercice.desc(), PpmsExercice.id.desc())
    ).all()
    return PpmsDetail(
        uai=uai,
        statut=status_for(session, uai),
        revisions=[RevisionRead.model_validate(r, from_attributes=True) for r in revisions],
        exercices=[ExerciceRead.model_validate(e, from_attributes=True) for e in exercices],
    )


@router.post("/revisions", response_model=RevisionRead, status_code=201)
@limiter.limit(settings.rate_limit_default)
def add_revision(
    request: Request,
    uai: str,
    payload: RevisionCreate,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
    user: User = Depends(editor),
):
    _known(store, uai)
    require_school_access(session, user, uai)
    revision = PpmsRevision(
        uai=uai,
        **payload.model_dump(),
        created_by_user_id=user.id,
        created_by_username=user.username,
    )
    session.add(revision)
    session.flush()
    record_audit(
        session, request, user, "ppms.revision_created", "ppms_revision", revision.id,
        {"uai": uai, "date_mise_a_jour": str(revision.date_mise_a_jour)},
    )
    session.commit()
    session.refresh(revision)
    return RevisionRead.model_validate(revision, from_attributes=True)


@router.post("/exercices", response_model=ExerciceRead, status_code=201)
@limiter.limit(settings.rate_limit_default)
def add_exercice(
    request: Request,
    uai: str,
    payload: ExerciceCreate,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
    user: User = Depends(editor),
):
    _known(store, uai)
    require_school_access(session, user, uai)
    exercice = PpmsExercice(
        uai=uai,
        **payload.model_dump(),
        created_by_user_id=user.id,
        created_by_username=user.username,
    )
    session.add(exercice)
    session.flush()
    record_audit(
        session, request, user, "ppms.exercice_created", "ppms_exercice", exercice.id,
        {"uai": uai, "type": exercice.type_exercice.value, "date": str(exercice.date_exercice)},
    )
    session.commit()
    session.refresh(exercice)
    return ExerciceRead.model_validate(exercice, from_attributes=True)


def _delete(request, session, user, uai, model, entry_id, event):
    require_school_access(session, user, uai)
    entry = session.get(model, entry_id)
    if entry is None or entry.uai != uai:
        raise HTTPException(404, "Entrée introuvable")
    record_audit(session, request, user, event, model.__tablename__, entry_id, {"uai": uai})
    session.delete(entry)
    session.commit()
    return Response(status_code=204)


@router.delete("/revisions/{entry_id}", status_code=204)
@limiter.limit(settings.rate_limit_default)
def delete_revision(
    request: Request,
    uai: str,
    entry_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(editor),
):
    """Remove an entry made by mistake (it stays in the audit log)."""
    return _delete(request, session, user, uai, PpmsRevision, entry_id, "ppms.revision_deleted")


@router.delete("/exercices/{entry_id}", status_code=204)
@limiter.limit(settings.rate_limit_default)
def delete_exercice(
    request: Request,
    uai: str,
    entry_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(editor),
):
    """Remove an entry made by mistake (it stays in the audit log)."""
    return _delete(request, session, user, uai, PpmsExercice, entry_id, "ppms.exercice_deleted")
