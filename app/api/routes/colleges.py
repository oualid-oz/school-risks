from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlmodel import Session

from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.scope import Scope, get_scope
from app.core.security import get_current_user
from app.data.store import DataStore, get_store
from app.db import get_session
from app.models.schemas import (
    CollegeDetail,
    CollegeNearby,
    CollegeSummary,
    RiskProfile,
    YearRecord,
)
from app.models.snapshots import ScoreSnapshotRead
from app.services.ppms import status_for as ppms_status_for
from app.services.ppms import statuses as ppms_statuses
from app.services.snapshots import history_for
from app.services.spatial import colleges_near, require_postgis

router = APIRouter(
    prefix="/colleges", tags=["colleges"], dependencies=[Depends(get_current_user)]
)


@router.get("", response_model=list[CollegeSummary])
@limiter.limit(settings.rate_limit_default)
def list_colleges(
    request: Request,
    commune: str | None = None,
    statut: Literal["public", "privé"] | None = None,
    secteur_id: str | None = None,
    niveau: Literal["faible", "moyen", "eleve"] | None = None,
    q: str | None = Query(None, description="Search in name, commune or UAI"),
    tri: Literal["score", "priorite"] = Query(
        "score",
        description="score: risk of the location. priorite: pupils exposed weighted by risk "
        "(score_global × effectif_total / 100); schools without enrolment come last.",
    ),
    ppms: Literal["a_jour", "ancien", "absent"] | None = Query(
        None, description="Only schools whose PPMS has this status"
    ),
    departement: str | None = Query(None, description="Department code: 45, 2A, 971…"),
    limit: int | None = Query(None, ge=1, le=1000, description="Page size (default: everything)"),
    offset: int = Query(0, ge=0),
    response: Response = None,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
):
    """Schools of the user's area, highest first by `tri`, with the status of their PPMS.
    The total before paging is in the `X-Total-Count` header."""
    scope.check_departement(departement)
    rows = store.list_colleges(commune, statut, secteur_id, niveau, q, tri, departement, scope.departements)
    status = ppms_statuses(session, [r["uai"] for r in rows])
    session.close()  # done with the database: free the connection before serialising
    rows = [{**r, "ppms_statut": status[r["uai"]].statut} for r in rows]
    rows = [r for r in rows if ppms is None or r["ppms_statut"] == ppms]
    response.headers["X-Total-Count"] = str(len(rows))
    return rows[offset:offset + limit] if limit else rows[offset:]


@router.get("/proches", response_model=list[CollegeNearby])
@limiter.limit(settings.rate_limit_default)
def nearby_colleges(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    rayon_km: float = Query(5.0, gt=0, le=50),
    limit: int = Query(20, ge=1, le=100),
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
):
    """Schools within `rayon_km` of a point, nearest first (PostGIS `ST_DWithin`)."""
    require_postgis()
    summaries = {s["uai"]: s for s in store.list_colleges(area=scope.departements)}
    return [
        {**summaries[uai], "distance_km": km}
        for uai, km in colleges_near(session, lat, lon, rayon_km, limit)
        if uai in summaries
    ]


@router.get("/{uai}", response_model=CollegeDetail)
@limiter.limit(settings.rate_limit_default)
def get_college(
    request: Request,
    uai: str,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
):
    scope.require_visible(store, uai)
    college = store.get_college(uai)
    return {**college, "profil_risque": _with_ppms_alerts(college["profil_risque"], session, uai)}


@router.get("/{uai}/risques", response_model=RiskProfile)
@limiter.limit(settings.rate_limit_default)
def get_risk_profile(
    request: Request,
    uai: str,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
):
    scope.require_visible(store, uai)
    profile = store.get_profile(uai)
    return _with_ppms_alerts(profile, session, uai)


def _with_ppms_alerts(profile: dict, session: Session, uai: str) -> dict:
    """The stored profile plus PPMS alerts (a copy: the cached profile is shared)."""
    reasons = ppms_status_for(session, uai).raisons
    session.close()  # done with the database: free the connection before serialising
    return {**profile, "alertes": [*profile["alertes"], *reasons]}


@router.get("/{uai}/historique", response_model=list[YearRecord])
@limiter.limit(settings.rate_limit_default)
def get_history(
    request: Request, uai: str, store: DataStore = Depends(get_store), scope: Scope = Depends(get_scope)
):
    """Yearly enrolment, IPS and DNB results (2016 → 2025)."""
    scope.require_visible(store, uai)
    return store.get_history(uai)


@router.get("/{uai}/scores-annuels", response_model=list[ScoreSnapshotRead])
@limiter.limit(settings.rate_limit_default)
def get_score_history(
    request: Request,
    uai: str,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
):
    """Risk scores stored by the yearly snapshots, oldest first."""
    scope.require_visible(store, uai)
    return history_for(session, uai)
