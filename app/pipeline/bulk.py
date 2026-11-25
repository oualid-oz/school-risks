"""National bulk sources, loaded into PostGIS: the risk indicators are computed in SQL for
all schools at once, instead of one API call per school and indicator (which does not scale
to the ~7,000 collèges of France: paging caps, throttling, hours of requests).

Sources (Géorisques download service, open licence):
- GASPAR national export: CatNat decrees and DICRIM per commune (~5 MB zip).
- ICPE national CSV: every classified installation, its regime and Seveso status (~40 MB).
  Coordinates come in each territory's projection (EPSG code per row).
- Cavities and ground movements: one CSV per department.

The clay exposure map (600 MB) stays a per-school API call (re-checked on a rolling basis,
see build.clay_due); PPRI zones are looked up in batches (georisques.fetch_ppr_batched).
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)

GASPAR_URL = "https://files.georisques.fr/GASPAR/DOCUMENTS/EXPORTS/gaspar_national.zip"
ICPE_URL = (
    "https://mapsref.brgm.fr/wxs/georisques/georisques_dl?&service=wfs&version=2.0.0"
    "&request=getfeature&typename=InstallationsClassees&outputformat=CSVTEXT"
)
CAVITES_URL = "https://www.georisques.gouv.fr/webappReport/ws/cavites/departements/{dep}/fiches.csv"
MVT_URL = "https://www.georisques.gouv.fr/webappReport/ws/mvmt/departements/{dep}/fichecommunes.csv"
SCHEMA = "bulk"


# ---- download ---------------------------------------------------------------------------
class Downloader:
    """Files cached on disk for the day: a re-run (or --offline) does not download again."""

    def __init__(self, cache: Path, offline: bool = False) -> None:
        self.cache = cache
        self.offline = offline
        self.client = httpx.Client(
            timeout=600, follow_redirects=True, headers={"User-Agent": "school-risk-tracker-pipeline"}
        )

    def get(self, name: str, url: str) -> bytes:
        path = self.cache / name
        if path.exists():
            return path.read_bytes()
        if self.offline:
            raise FileNotFoundError(f"Not in cache (offline mode): {name}")
        logger.info("downloading %s", name)
        response = self.client.get(url)
        response.raise_for_status()
        self.cache.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(response.content)
        tmp.replace(path)
        return response.content

    def close(self) -> None:
        self.client.close()


def _csv_rows(data: bytes, delimiter: str = ";") -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig", errors="replace")), delimiter=delimiter))


# ---- loading -----------------------------------------------------------------------------
TABLES = {
    "icpe": "code_aiot text, x double precision, y double precision, epsg integer, num_dep text, "
            "regime text, seveso text",
    "catnat": "code_commune text, id_gaspar text, libelle text",
    "dicrim": "code_commune text, annee integer",
    "cavites": "id text, x double precision, y double precision, epsg integer, departement text",
    "mvt": "id text, lon double precision, lat double precision, departement text",
}


def _copy(conn, table: str, columns: list[str], rows: list[tuple]) -> None:
    raw = conn.connection.dbapi_connection
    statement = f"COPY {SCHEMA}.{table} ({', '.join(columns)}) FROM STDIN"
    with raw.cursor() as cursor, cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row(row)


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def reset_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        for table, columns in TABLES.items():
            conn.execute(text(f"CREATE TABLE {SCHEMA}.{table} ({columns})"))


def load_national(engine: Engine, dl: Downloader) -> dict[str, int]:
    """GASPAR (CatNat, DICRIM) and ICPE, for all of France."""
    gaspar = zipfile.ZipFile(io.BytesIO(dl.get("gaspar_national.zip", GASPAR_URL)))
    catnat = _csv_rows(gaspar.read("catnat.csv"))
    dicrim = _csv_rows(gaspar.read("dicrim.csv"))
    icpe = _csv_rows(dl.get("icpe.csv", ICPE_URL))
    with engine.begin() as conn:
        _copy(conn, "catnat", ["code_commune", "id_gaspar", "libelle"],
              [(r["code_commune"], r["id_gaspar"], r["libelle_risque_jo"]) for r in catnat])
        _copy(conn, "dicrim", ["code_commune", "annee"],
              [(r["code_commune"], int(r["date_publi_dicrim"][:4]))
               for r in dicrim if r.get("date_publi_dicrim")])
        _copy(conn, "icpe", ["code_aiot", "x", "y", "epsg", "num_dep", "regime", "seveso"],
              [(r["code_aiot"], _float(r["x"]), _float(r["y"]), int(r["code_epsg"] or 0) or None,
                r["num_dep"], r["lib_regime"], r["lib_seveso"] or None)
               for r in icpe if r.get("x") and r.get("y")])
        conn.execute(text(f"""
            ALTER TABLE {SCHEMA}.icpe ADD COLUMN geom geometry(Point, 4326);
            UPDATE {SCHEMA}.icpe SET geom = ST_Transform(ST_SetSRID(ST_MakePoint(x, y), epsg), 4326)
            WHERE epsg IS NOT NULL;
            CREATE INDEX ON {SCHEMA}.icpe USING gist (geom);
            -- Indicator inputs as small indexed tables (classified sites; Seveso sites).
            CREATE TABLE {SCHEMA}.sites AS SELECT code_aiot, seveso, geom FROM {SCHEMA}.icpe
              WHERE geom IS NOT NULL AND regime IS DISTINCT FROM 'Non ICPE';
            CREATE INDEX ON {SCHEMA}.sites USING gist (geom);
            CREATE TABLE {SCHEMA}.seveso AS SELECT * FROM {SCHEMA}.sites WHERE seveso LIKE 'Seveso%';
            CREATE INDEX ON {SCHEMA}.seveso USING gist (geom);
            CREATE INDEX ON {SCHEMA}.catnat (code_commune);
            CREATE INDEX ON {SCHEMA}.dicrim (code_commune);
        """))
    return {"catnat": len(catnat), "dicrim": len(dicrim), "icpe": len(icpe)}


def departments_near(engine: Engine, schools: list[dict[str, Any]], margin_km: float = 1.0) -> list[str]:
    """Departments whose extent (from the ICPE points) comes within `margin_km` of a school:
    a school near a border needs the cavities of the neighbouring department too."""
    _load_schools(engine, schools)
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            WITH extent AS (
                SELECT num_dep, ST_Expand(ST_Extent(geom)::geometry, 0.05) AS box
                FROM {SCHEMA}.icpe
                WHERE geom IS NOT NULL AND num_dep ~ '^([0-9]{{2}}|2[AB])$'  -- mainland, Corsica
                GROUP BY num_dep
            )
            SELECT DISTINCT e.num_dep FROM extent e JOIN {SCHEMA}.schools s
              ON ST_DWithin(e.box::geography, s.geom::geography, :margin)
        """), {"margin": margin_km * 1000}).scalars().all()
    return sorted(rows)


def load_departmental(engine: Engine, dl: Downloader, departements: list[str]) -> dict[str, int]:
    """Cavities and ground movements of these departments."""
    # ~200 small files: fetched in parallel, then parsed in order.
    with ThreadPoolExecutor(max_workers=8) as pool:
        files = list(pool.map(
            lambda dep: (dl.get(f"cavites_{dep}.csv", CAVITES_URL.format(dep=dep)),
                         dl.get(f"mvt_{dep}.csv", MVT_URL.format(dep=dep))),
            departements,
        ))
    cavites, mvt = [], []
    for dep, (cavites_csv, mvt_csv) in zip(departements, files, strict=True):
        for row in _csv_rows(cavites_csv):
            if (row.get("confidentialite") or "public") != "public":
                continue  # not published by the API either
            x, y = _float(row.get("xouvl2e")), _float(row.get("youvl2e"))
            if x and y:
                cavites.append((row["id"], x, y, 27572, dep))  # Lambert II étendu
        for row in _csv_rows(mvt_csv):
            lon, lat = _float(row.get("longitudeDoublePrec")), _float(row.get("latitudeDoublePrec"))
            if lon and lat:
                mvt.append((row["idMvt"], lon, lat, dep))
    with engine.begin() as conn:
        _copy(conn, "cavites", ["id", "x", "y", "epsg", "departement"], cavites)
        _copy(conn, "mvt", ["id", "lon", "lat", "departement"], mvt)
        conn.execute(text(f"""
            ALTER TABLE {SCHEMA}.cavites ADD COLUMN geom geometry(Point, 4326);
            UPDATE {SCHEMA}.cavites SET geom = ST_Transform(ST_SetSRID(ST_MakePoint(x, y), epsg), 4326);
            ALTER TABLE {SCHEMA}.mvt ADD COLUMN geom geometry(Point, 4326);
            UPDATE {SCHEMA}.mvt SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326);
            CREATE INDEX ON {SCHEMA}.cavites USING gist (geom);
            CREATE INDEX ON {SCHEMA}.mvt USING gist (geom);
        """))
    return {"cavites": len(cavites), "mvt": len(mvt), "departements": len(departements)}


def _load_schools(engine: Engine, schools: list[dict[str, Any]]) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"""
            DROP TABLE IF EXISTS {SCHEMA}.schools;
            CREATE TABLE {SCHEMA}.schools (uai text PRIMARY KEY, code_insee text, lat double precision,
                                           lon double precision);
        """))
        _copy(conn, "schools", ["uai", "code_insee", "lat", "lon"],
              [(s["uai"], s["code_insee"], s["lat"], s["lon"]) for s in schools])
        conn.execute(text(f"""
            ALTER TABLE {SCHEMA}.schools ADD COLUMN geom geometry(Point, 4326);
            UPDATE {SCHEMA}.schools SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326);
            CREATE INDEX ON {SCHEMA}.schools USING gist (geom);
        """))


# ---- indicators ---------------------------------------------------------------------------
# Same definitions as the API-based pipeline (app/pipeline/georisques.py): ICPE excluding
# "Non ICPE"; Seveso = upper or lower tier; counts within 500 m / 1 km; CatNat counted as
# distinct decrees; DICRIM = latest year. Distances are true metres (geography).
# Each spatial test first uses the GiST index on plain geometry (a box in degrees that is
# wider than the radius anywhere in mainland France and Corsica), then checks the true
# distance in metres. Casting both sides to geography alone would skip the index.
DEG_500M, DEG_1KM = 0.01, 0.02
INDICATORS_SQL = f"""
SELECT s.uai,
  (SELECT count(*) FROM {SCHEMA}.cavites c
     WHERE ST_DWithin(c.geom, s.geom, {DEG_500M})
       AND ST_DWithin(c.geom::geography, s.geom::geography, 500)) AS nb_cavites_500m,
  (SELECT count(*) FROM {SCHEMA}.mvt m
     WHERE ST_DWithin(m.geom, s.geom, {DEG_500M})
       AND ST_DWithin(m.geom::geography, s.geom::geography, 500)) AS nb_mouvements_terrain_500m,
  (SELECT count(*) FROM {SCHEMA}.sites i
     WHERE ST_DWithin(i.geom, s.geom, {DEG_1KM})
       AND ST_DWithin(i.geom::geography, s.geom::geography, 1000)) AS nb_icpe_1km,
  (SELECT min(ST_Distance(n.geom::geography, s.geom::geography)) FROM
     (SELECT geom FROM {SCHEMA}.sites ORDER BY geom <-> s.geom LIMIT 5) n) AS icpe_m,
  (SELECT min(ST_Distance(n.geom::geography, s.geom::geography)) FROM
     (SELECT geom FROM {SCHEMA}.seveso ORDER BY geom <-> s.geom LIMIT 5) n) AS seveso_m,
  cn.nb_catnat, cn.nb_catnat_inondations, cn.nb_catnat_secheresse, cn.nb_catnat_mouvement_terrain,
  (SELECT max(annee) FROM {SCHEMA}.dicrim WHERE code_commune = s.code_insee) AS annee_dicrim
FROM {SCHEMA}.schools s
LEFT JOIN LATERAL (
  SELECT count(DISTINCT id_gaspar) AS nb_catnat,
         count(DISTINCT id_gaspar) FILTER (WHERE lower(libelle) LIKE '%inondation%')
           AS nb_catnat_inondations,
         count(DISTINCT id_gaspar) FILTER (WHERE lower(libelle) LIKE '%sécheresse%')
           AS nb_catnat_secheresse,
         count(DISTINCT id_gaspar) FILTER (WHERE lower(libelle) LIKE '%mouvement de terrain%')
           AS nb_catnat_mouvement_terrain
  FROM {SCHEMA}.catnat WHERE code_commune = s.code_insee
) cn ON true
"""


def indicators(engine: Engine, schools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    _load_schools(engine, schools)
    started = datetime.now(UTC)
    with engine.connect() as conn:
        rows = conn.execute(text(INDICATORS_SQL)).mappings().all()
    elapsed = (datetime.now(UTC) - started).total_seconds()
    logger.info("indicators for %d schools in %.1f s", len(rows), elapsed)
    out = {}
    for r in rows:
        out[r["uai"]] = {
            "nb_cavites_500m": int(r["nb_cavites_500m"]),
            "nb_mouvements_terrain_500m": int(r["nb_mouvements_terrain_500m"]),
            "nb_icpe_1km": int(r["nb_icpe_1km"]),
            "distance_icpe_km": round(r["icpe_m"] / 1000, 2) if r["icpe_m"] is not None else None,
            "distance_seveso_km": round(r["seveso_m"] / 1000, 2) if r["seveso_m"] is not None else None,
            "nb_catnat": int(r["nb_catnat"]),
            "nb_catnat_inondations": int(r["nb_catnat_inondations"]),
            "nb_catnat_secheresse": int(r["nb_catnat_secheresse"]),
            "nb_catnat_mouvement_terrain": int(r["nb_catnat_mouvement_terrain"]),
            "annee_dicrim": int(r["annee_dicrim"]) if r["annee_dicrim"] is not None else None,
        }
    return out
