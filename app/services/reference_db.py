"""Load the reference files into PostGIS, and read them back for the data store."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, func, select, text

from app.core.config import settings
from app.data.schema import HISTORY_SCHEMA, RISQUES_SCHEMA
from app.data.store import _records
from app.models.reference import college, college_annee, secteur

SECTOR_FIELDS = ("secteur_id", "secteur", "commune", "code_insee", "departement")


def _rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    return _records(df)


def load_reference(engine: Engine, data_dir: Path | None = None) -> dict[str, int]:
    """Replace the reference tables with the content of the data files, in one transaction."""
    d = data_dir or settings.data_dir
    risques = pd.read_parquet(d / settings.risques_file)
    history = pd.read_parquet(d / settings.colleges_file)
    with open(d / settings.secteurs_file, encoding="utf-8") as f:
        sectors = json.load(f)["features"]

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE college_annee, college, secteur"))
        for f in sectors:
            conn.execute(
                secteur.insert().values(
                    **{k: f["properties"].get(k) for k in SECTOR_FIELDS},
                    geom=func.ST_Multi(
                        func.ST_SetSRID(func.ST_GeomFromGeoJSON(json.dumps(f["geometry"])), 4326)
                    ),
                )
            )
        for row in _rows(risques):
            conn.execute(
                college.insert().values(
                    **row, geom=func.ST_SetSRID(func.ST_MakePoint(row["lon"], row["lat"]), 4326)
                )
            )
        conn.execute(college_annee.insert(), _rows(history))
        invalid = conn.execute(
            select(func.count()).select_from(secteur).where(~func.ST_IsValid(secteur.c.geom))
        ).scalar_one()
    return {
        "college": len(risques),
        "college_annee": len(history),
        "secteur": len(sectors),
        "invalid_sectors": invalid,
    }


def read_reference(engine: Engine) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """(risques, history, sectors FeatureCollection), shaped exactly like the files."""
    with engine.connect() as conn:
        risques = pd.read_sql(
            select(*[college.c[c] for c in RISQUES_SCHEMA]).order_by(college.c.uai), conn
        )
        history = pd.read_sql(
            select(*[college_annee.c[c] for c in HISTORY_SCHEMA]).order_by(
                college_annee.c.uai, college_annee.c.rentree
            ),
            conn,
        )
        sector_rows = conn.execute(
            select(
                secteur.c.secteur_id, secteur.c.secteur, secteur.c.commune,
                secteur.c.code_insee, secteur.c.departement,
                func.ST_AsGeoJSON(secteur.c.geom, 6).label("geometry"),
            ).order_by(secteur.c.secteur_id)
        ).mappings().all()

    for df, schema in ((risques, RISQUES_SCHEMA), (history, HISTORY_SCHEMA)):
        for column, dtype in schema.items():
            df[column] = df[column].astype(dtype)
    features = [
        {
            "type": "Feature",
            "properties": {k: r[k] for k in SECTOR_FIELDS},
            "geometry": json.loads(r["geometry"]),
        }
        for r in sector_rows
    ]
    return risques, history, {"type": "FeatureCollection", "features": features}
