from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session

from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.security import get_current_user
from app.data.store import DataStore, get_store
from app.db import get_session
from app.services.spatial import locate_sector, require_postgis

router = APIRouter(
    prefix="/secteurs", tags=["secteurs"], dependencies=[Depends(get_current_user)]
)


@router.get("", response_model=dict[str, Any])
@limiter.limit(settings.rate_limit_default)
def secteurs_geojson(
    request: Request,
    with_scores: bool = True,
    store: DataStore = Depends(get_store),
):
    """GeoJSON FeatureCollection of catchment areas, ready for Leaflet/MapLibre."""
    return store.secteurs_geojson(with_scores)


@router.get("/localiser", response_model=dict[str, Any])
@limiter.limit(settings.rate_limit_default)
def locate(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
):
    """The sector containing a point (PostGIS `ST_Contains`) and the schools serving it."""
    require_postgis()
    found = locate_sector(session, lat, lon)
    colleges = (
        [c for c in store.list_colleges(secteur_id=found["secteur_id"])] if found else []
    )
    return {"secteur": found, "colleges": colleges}


@router.get("/{secteur_id}", response_model=dict[str, Any])
@limiter.limit(settings.rate_limit_default)
def get_secteur(
    request: Request, secteur_id: str, store: DataStore = Depends(get_store)
):
    feature = store.get_secteur(secteur_id)
    if feature is None:
        raise HTTPException(404, f"Secteur {secteur_id} introuvable")
    return feature
