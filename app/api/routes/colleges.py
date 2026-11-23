from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session

from app.api.routes.auth import limiter
from app.core.config import settings
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
    store: DataStore = Depends(get_store),
):
    """All schools, highest first by `tri`."""
    return store.list_colleges(commune, statut, secteur_id, niveau, q, tri)


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
):
    """Schools within `rayon_km` of a point, nearest first (PostGIS `ST_DWithin`)."""
    require_postgis()
    summaries = {s["uai"]: s for s in store.list_colleges()}
    return [
        {**summaries[uai], "distance_km": km}
        for uai, km in colleges_near(session, lat, lon, rayon_km, limit)
        if uai in summaries
    ]


@router.get("/{uai}", response_model=CollegeDetail)
@limiter.limit(settings.rate_limit_default)
def get_college(request: Request, uai: str, store: DataStore = Depends(get_store)):
    college = store.get_college(uai)
    if college is None:
        raise HTTPException(404, f"College {uai} not found")
    return college


@router.get("/{uai}/risques", response_model=RiskProfile)
@limiter.limit(settings.rate_limit_default)
def get_risk_profile(request: Request, uai: str, store: DataStore = Depends(get_store)):
    profile = store.get_profile(uai)
    if profile is None:
        raise HTTPException(404, f"College {uai} not found")
    return profile


@router.get("/{uai}/historique", response_model=list[YearRecord])
@limiter.limit(settings.rate_limit_default)
def get_history(request: Request, uai: str, store: DataStore = Depends(get_store)):
    """Yearly enrolment, IPS and DNB results (2016 → 2025)."""
    if not store.exists(uai):
        raise HTTPException(404, f"College {uai} not found")
    return store.get_history(uai)


@router.get("/{uai}/scores-annuels", response_model=list[ScoreSnapshotRead])
@limiter.limit(settings.rate_limit_default)
def get_score_history(
    request: Request,
    uai: str,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
):
    """Risk scores stored by the yearly snapshots, oldest first."""
    if not store.exists(uai):
        raise HTTPException(404, f"College {uai} not found")
    return history_for(session, uai)
