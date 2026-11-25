"""Catchment sectors for every department, from the national school map (Sprint 20).

No national file of sector polygons exists. The ministry's school map ("carte scolaire des
collèges publics", data.education.gouv.fr) says which public collège serves each commune;
communes split between several collèges are described street by street, which cannot be
drawn without street geometry. So, in PostGIS:
- a commune served by one collège is a part of its sector (exact, at the 50 m outline);
- a split commune is shared between its collèges by proximity (Voronoi cells of their
  positions, clipped to the commune): such sectors are flagged approximate.
Commune outlines: Etalab "contours administratifs" (IGN Admin Express, generalised to 50 m).
Departments that publish their own sector map (sectors.SOURCES, e.g. Loiret) keep it.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import logging
from typing import Any

from sqlalchemy import Engine, text

from app.pipeline.bulk import SCHEMA, Downloader, _copy

logger = logging.getLogger(__name__)

SCHOOL_MAP_URL = (
    "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/"
    "fr-en-carte-scolaire-colleges-publics/exports/csv?select=code_insee,code_rne"
    "&group_by=code_insee,code_rne&delimiter=%3B"
)
COMMUNES_URL = (
    "https://etalab-datasets.geo.data.gouv.fr/contours-administratifs/2025/geojson/"
    "communes-50m.geojson.gz"
)
SIMPLIFY_DEG = 0.0005  # ≈ 50 m, the generalisation of the outlines: ~12 MB for all of France
DECIMALS = 4  # ≈ 10 m


def load(engine: Engine, dl: Downloader, schools: list[dict[str, Any]]) -> dict[str, int]:
    """Commune outlines, the school map and the collèges' positions, into the bulk schema."""
    rows = list(csv.DictReader(io.StringIO(
        dl.get("carte_scolaire.csv", SCHOOL_MAP_URL).decode("utf-8-sig")), delimiter=";"))
    communes = json.loads(gzip.decompress(dl.get("communes-50m.geojson.gz", COMMUNES_URL)))["features"]
    with engine.begin() as conn:
        conn.execute(text(f"""
            DROP TABLE IF EXISTS {SCHEMA}.communes, {SCHEMA}.school_map, {SCHEMA}.colleges;
            CREATE TABLE {SCHEMA}.communes (code text PRIMARY KEY, departement text, geojson text);
            CREATE TABLE {SCHEMA}.school_map (code_insee text, uai text);
            CREATE TABLE {SCHEMA}.colleges (uai text PRIMARY KEY, lat double precision,
                                            lon double precision);
        """))
        _copy(conn, "communes", ["code", "departement", "geojson"], [
            (f["properties"]["code"], f["properties"]["departement"], json.dumps(f["geometry"]))
            for f in communes
        ])
        _copy(conn, "school_map", ["code_insee", "uai"],
              [(r["code_insee"], r["code_rne"]) for r in rows if r["code_insee"] and r["code_rne"]])
        _copy(conn, "colleges", ["uai", "lat", "lon"],
              [(s["uai"], s["lat"], s["lon"]) for s in schools])
        conn.execute(text(f"""
            ALTER TABLE {SCHEMA}.communes ADD COLUMN geom geometry(MultiPolygon, 4326);
            UPDATE {SCHEMA}.communes
               SET geom = ST_Multi(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(geojson), 4326)));
            ALTER TABLE {SCHEMA}.communes DROP COLUMN geojson;
            CREATE INDEX ON {SCHEMA}.school_map (code_insee);
        """))
    return {"school_map": len(rows), "communes": len(communes)}


# Parts of each sector: whole communes, and Voronoi cells of split communes. A split
# commune whose collèges have no known position goes whole to each of them (approximate).
SECTORS_SQL = f"""
WITH listed AS (
    SELECT DISTINCT m.code_insee, m.uai FROM {SCHEMA}.school_map m
    JOIN {SCHEMA}.communes c ON c.code = m.code_insee
    WHERE NOT (c.departement = ANY(:excluded))
),
n AS (SELECT code_insee, count(*) AS colleges FROM listed GROUP BY code_insee),
whole AS (
    SELECT l.uai, c.geom, c.departement, false AS approx
    FROM listed l JOIN n USING (code_insee) JOIN {SCHEMA}.communes c ON c.code = l.code_insee
    WHERE n.colleges = 1
),
split AS (
    SELECT l.code_insee, l.uai, ST_SetSRID(ST_MakePoint(k.lon, k.lat), 4326) AS pt
    FROM listed l JOIN n USING (code_insee)
    LEFT JOIN {SCHEMA}.colleges k ON k.uai = l.uai
    WHERE n.colleges > 1
),
located AS (
    SELECT code_insee, count(pt) AS points, ST_Collect(pt) AS pts FROM split GROUP BY code_insee
),
cells AS (
    SELECT s.uai, ST_Intersection(c.geom, cell.geom) AS geom, c.departement, true AS approx
    FROM located o
    JOIN {SCHEMA}.communes c ON c.code = o.code_insee
    CROSS JOIN LATERAL ST_Dump(ST_VoronoiPolygons(o.pts, 0, ST_Expand(c.geom, 0.1))) AS cell
    JOIN split s ON s.code_insee = o.code_insee AND s.pt IS NOT NULL
                AND ST_Contains(cell.geom, s.pt)
    WHERE o.points > 1
),
no_position AS (  -- fewer than two collèges with a position: the commune goes whole to each
    SELECT s.uai, c.geom, c.departement, true AS approx
    FROM split s JOIN located o USING (code_insee) JOIN {SCHEMA}.communes c ON c.code = s.code_insee
    WHERE o.points <= 1
),
parts AS (
    SELECT * FROM whole UNION ALL SELECT * FROM cells UNION ALL SELECT * FROM no_position
)
SELECT uai,
       ST_AsGeoJSON(ST_Multi(ST_CollectionExtract(ST_MakeValid(
           ST_SimplifyPreserveTopology(ST_Union(geom), :simplify)), 3)), :decimals) AS geometry,
       bool_or(approx) AS approx,
       array_agg(DISTINCT departement ORDER BY departement) AS departements
FROM parts
WHERE NOT ST_IsEmpty(geom)
GROUP BY uai
ORDER BY uai
"""


def build(
    engine: Engine, names: dict[str, dict[str, Any]], excluded: list[str]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Sector features (one per public collège) for the communes outside `excluded`
    departments, and per-department coverage: sectors, approximate ones, communes listed."""
    with engine.connect() as conn:
        rows = conn.execute(text(SECTORS_SQL), {
            "excluded": list(excluded), "simplify": SIMPLIFY_DEG, "decimals": DECIMALS,
        }).mappings().all()
        listed = dict(conn.execute(text(f"""
            SELECT c.departement, count(DISTINCT m.code_insee) FROM {SCHEMA}.school_map m
            JOIN {SCHEMA}.communes c ON c.code = m.code_insee GROUP BY c.departement
        """)).all())
    features, coverage = [], {}
    for r in rows:
        school = names.get(r["uai"], {})
        features.append({
            "type": "Feature",
            "properties": {
                "secteur_id": r["uai"],
                "secteur": f"{school['nom']} ({school['commune']})" if school else f"Collège {r['uai']}",
                "commune": school.get("commune"),
                "code_insee": school.get("code_insee"),
                "departement": school.get("departement_nom"),
                "departements": list(r["departements"]),
                "source": "carte_scolaire_approchee" if r["approx"] else "carte_scolaire",
            },
            "geometry": json.loads(r["geometry"]),
        })
        for dep in r["departements"]:
            c = coverage.setdefault(dep, {"sectors": 0, "approximate": 0, "communes": listed.get(dep, 0)})
            c["sectors"] += 1
            c["approximate"] += int(r["approx"])
    logger.info("School-map sectors: %d (%d approximate)", len(features),
                sum(1 for f in features if f["properties"]["source"] != "carte_scolaire"))
    return features, coverage
