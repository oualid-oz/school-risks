from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, func, select

from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.security import get_current_user
from app.data.store import DataStore, get_store
from app.db import get_session
from app.models.tracking import Action
from app.services.ppms import statuses as ppms_statuses

router = APIRouter(
    prefix="/stats", tags=["stats"], dependencies=[Depends(get_current_user)]
)


@router.get("", response_model=dict[str, Any])
@limiter.limit(settings.rate_limit_default)
def overview(
    request: Request,
    departement: str | None = None,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
):
    """Dashboard numbers: risk distribution plus follow-up progress."""
    uais = [c["uai"] for c in store.list_colleges(departement=departement)]
    stmt = select(Action.statut, func.count()).group_by(Action.statut)
    if departement:
        stmt = stmt.where(Action.uai.in_(uais))
    rows = session.exec(stmt).all()
    ppms = Counter(s.statut for s in ppms_statuses(session, uais).values())
    return {
        **store.stats(departement),
        "actions_par_statut": {s.value: n for s, n in rows},
        "ppms_par_statut": {k: ppms.get(k, 0) for k in ("a_jour", "ancien", "absent")},
    }


territories_router = APIRouter(tags=["stats"], dependencies=[Depends(get_current_user)])


@territories_router.get("/territoires", response_model=list[dict[str, Any]])
@limiter.limit(settings.rate_limit_default)
def territories(request: Request, store: DataStore = Depends(get_store)):
    """Departments in the data: code, name, académie, number of schools, and how many of them
    have parts of the evaluation missing (no data for the territory)."""
    return store.territories()
