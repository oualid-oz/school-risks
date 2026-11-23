"""Spatial queries, answered by PostGIS. They are unavailable on SQLite."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from geoalchemy2 import Geography
from sqlalchemy import Connection, cast, func, select
from sqlmodel import Session

from app.db import is_postgres
from app.models.reference import college, secteur


def require_postgis() -> None:
    if not is_postgres():
        raise HTTPException(
            status_code=501, detail="Spatial queries need the PostGIS database (SRT_DATABASE_URL)"
        )


def _point(lat: float, lon: float):
    return func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)


def locate_sector(session: Session, lat: float, lon: float) -> dict[str, Any] | None:
    """The catchment sector that contains the point (ST_Contains), if any."""
    row = session.execute(
        select(secteur.c.secteur_id, secteur.c.secteur, secteur.c.commune)
        .where(func.ST_Contains(secteur.c.geom, _point(lat, lon)))
        .limit(1)
    ).mappings().first()
    return dict(row) if row else None


def colleges_near(
    session: Session, lat: float, lon: float, radius_km: float, limit: int
) -> list[tuple[str, float]]:
    """(uai, distance in km) of the schools within ``radius_km``, nearest first.

    Distances are computed on the geography type, so they are true metres on the ellipsoid.
    """
    here = cast(_point(lat, lon), Geography)
    there = cast(college.c.geom, Geography)
    distance = func.ST_Distance(there, here)
    rows = session.execute(
        select(college.c.uai, distance.label("m"))
        .where(func.ST_DWithin(there, here, radius_km * 1000))
        .order_by(distance)
        .limit(limit)
    ).all()
    return [(uai, round(m / 1000, 3)) for uai, m in rows]


def sector_mismatches(conn: Connection) -> list[dict[str, Any]]:
    """Schools whose position is not inside the sector they are assigned to."""
    rows = conn.execute(
        select(college.c.uai, college.c.nom, college.c.secteur_id)
        .join(secteur, secteur.c.secteur_id == college.c.secteur_id)
        .where(~func.ST_Contains(secteur.c.geom, college.c.geom))
        .order_by(college.c.uai)
    ).mappings().all()
    return [dict(r) for r in rows]
