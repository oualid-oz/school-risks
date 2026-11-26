from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, func, select

from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.scope import Scope, get_scope
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
    scope: Scope = Depends(get_scope),
):
    """Dashboard numbers for the user's area: risk distribution plus follow-up progress."""
    scope.check_departement(departement)
    uais = [c["uai"] for c in store.list_colleges(departement=departement, area=scope.departements)]
    stmt = select(Action.statut, func.count()).group_by(Action.statut)
    if departement or not scope.national:
        stmt = stmt.where(Action.uai.in_(uais))
    rows = session.exec(stmt).all()
    ppms = Counter(s.statut for s in ppms_statuses(session, uais).values())
    session.close()  # done with the database: free the connection before serialising
    return {
        **store.stats(departement, scope.departements),
        "actions_par_statut": {s.value: n for s, n in rows},
        "ppms_par_statut": {k: ppms.get(k, 0) for k in ("a_jour", "ancien", "absent")},
    }


territories_router = APIRouter(tags=["stats"], dependencies=[Depends(get_current_user)])


@territories_router.get("/qualite", response_model=dict[str, Any])
@limiter.limit(settings.rate_limit_default)
def data_quality(
    request: Request,
    departement: str | None = None,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
):
    """Data quality of the user's area: per department, the coverage of each source, where
    the sectors come from and the open data reports; then the schools missing data (of
    `departement`, or of the whole area)."""
    from app.models.feedback import DataReport, StatutSignalement
    from app.services import quality

    scope.check_departement(departement)
    stmt = select(DataReport.departement, func.count()).where(
        DataReport.statut == StatutSignalement.ouvert
    ).group_by(DataReport.departement)
    if not scope.national:
        stmt = stmt.where(DataReport.departement.in_(scope.departements))
    open_reports = dict(session.exec(stmt).all())
    session.close()
    return {
        "departements": quality.by_department(store, scope.departements, open_reports),
        "colleges_incomplets": quality.schools_with_gaps(store, scope.departements, departement),
    }


@territories_router.get("/territoires", response_model=list[dict[str, Any]])
@limiter.limit(settings.rate_limit_default)
def territories(
    request: Request, store: DataStore = Depends(get_store), scope: Scope = Depends(get_scope)
):
    """Departments of the user's area: code, name, académie, number of schools, how many
    have parts of the evaluation missing, mean score, high-risk schools, sector sources."""
    return store.territories(scope.departements)
