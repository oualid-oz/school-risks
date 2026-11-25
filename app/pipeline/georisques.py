"""Géorisques (georisques.gouv.fr) and IGN GPU (apicarto.ign.fr) sources.

Per school: clay exposure, cavities, ground movements, flood-plan (PPRI) zone.
Per commune: CatNat declarations, DICRIM year.
ICPE (classified installations): one 10 km query per school. Paging through a whole
department is not used because the API returns overlapping pages (duplicates and gaps).
"""
from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.pipeline.client import Fetcher, SourceError
from app.pipeline.geo import haversine_km

logger = logging.getLogger(__name__)

API = "https://georisques.gouv.fr/api/v1"
GPU_API = "https://apicarto.ign.fr/api/gpu/assiette-sup-s"
ICPE_RADIUS_M = 10_000
ICPE_PAGE_SIZE = 500  # the max is 1000, but 1000-row pages get their connection reset
# When no Seveso site lies within 10 km, search 12 circles centred 12 km away:
# together they cover up to ~21 km, enough to show the distance in the UI
# (beyond 10 km the Seveso part of the score is 0 anyway).
SEVESO_RING_KM = 12.0
SEVESO_RING_POINTS = 12

def _parallel[K, T](fn: Callable[[K], T], keys: Iterable[K], workers: int) -> dict[K, T]:
    keys = list(keys)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(keys, pool.map(fn, keys), strict=True))


def _latlon(school: dict[str, Any]) -> str:
    return f"{school['lon']},{school['lat']}"


def _icpe_around(fetcher: Fetcher, lat: float, lon: float) -> list[dict[str, Any]]:
    params = {"latlon": f"{lon},{lat}", "rayon": ICPE_RADIUS_M, "page_size": ICPE_PAGE_SIZE}
    first = fetcher.get_json(f"{API}/installations_classees", {**params, "page": 1}) or {}
    rows = list(first.get("data") or [])
    if (first.get("total_pages") or 1) > 1:
        # Should not happen around Loiret (max ~380 sites in 10 km); pages may overlap.
        logger.warning("ICPE query around %s,%s needs paging; deduplicating", lat, lon)
        rows += _paged(fetcher, "installations_classees", params)[len(rows):]
    unique = {r.get("codeAIOT") or id(r): r for r in rows}
    return list(unique.values())


def _ring(lat: float, lon: float, radius_km: float, points: int) -> list[tuple[float, float]]:
    out = []
    for i in range(points):
        angle = 2 * math.pi * i / points
        dlat = radius_km / 111.32 * math.cos(angle)
        dlon = radius_km / (111.32 * math.cos(math.radians(lat))) * math.sin(angle)
        out.append((round(lat + dlat, 5), round(lon + dlon, 5)))
    return out


def _paged(fetcher: Fetcher, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        body = fetcher.get_json(f"{API}/{path}", {**params, "page": page})
        if not body or not body.get("data"):
            return rows
        rows.extend(body["data"])
        if page >= (body.get("total_pages") or 1):
            return rows
        page += 1


# ---- fetch ------------------------------------------------------------------
def fetch_all(
    fetcher: Fetcher, schools: list[dict[str, Any]], workers: int = 4
) -> dict[str, Any]:
    by_uai = {s["uai"]: s for s in schools}
    communes = sorted({s["code_insee"] for s in schools})

    def per_school(uai: str) -> dict[str, Any]:
        s = by_uai[uai]
        point = json.dumps({"type": "Point", "coordinates": [s["lon"], s["lat"]]})
        icpe = _icpe_around(fetcher, s["lat"], s["lon"])
        if not any(is_seveso(i) for i in icpe):
            for lat, lon in _ring(s["lat"], s["lon"], SEVESO_RING_KM, SEVESO_RING_POINTS):
                icpe += [i for i in _icpe_around(fetcher, lat, lon) if is_seveso(i)]
        return {
            "icpe": icpe,
            "rga": fetcher.get_json(f"{API}/rga", {"latlon": _latlon(s)}),
            "cavites": _paged(fetcher, "cavites", {"latlon": _latlon(s), "rayon": 500, "page_size": 100}),
            "mvt": _paged(fetcher, "mvt", {"latlon": _latlon(s), "rayon": 500, "page_size": 100}),
            "ppr": fetcher.get_json(GPU_API, {"geom": point, "categorie": "PM1"}),
        }

    def per_commune(code_insee: str) -> dict[str, Any]:
        return {
            "catnat": _paged(fetcher, "gaspar/catnat", {"code_insee": code_insee, "page_size": 100}),
            "dicrim": _paged(fetcher, "gaspar/dicrim", {"code_insee": code_insee}),
        }

    logger.info("Géorisques: %d schools, %d communes", len(schools), len(communes))
    return {
        "schools": _parallel(per_school, by_uai, workers),
        "communes": _parallel(per_commune, communes, workers),
    }


def fetch_clay(
    fetcher: Fetcher, schools: list[dict[str, Any]], workers: int = 4
) -> dict[str, dict[str, Any] | None]:
    """Clay exposure per school (no bulk file of usable size: the national map is 600 MB)."""
    by_uai = {s["uai"]: s for s in schools}
    logger.info("Clay exposure: %d schools", len(schools))
    return _parallel(lambda uai: fetcher.get_json(f"{API}/rga", {"latlon": _latlon(by_uai[uai])}),
                     by_uai, workers)


# The same easements as GPU_API, from IGN's WFS: it answers "which flood-plan zones contain
# any of these points" for a whole batch in ~0.1 s, where GPU_API takes seconds per point
# and times out under parallel load. Most schools are outside any zone, so a batch is only
# split (in halves, down to single schools) when it contains a zone.
WFS_SUP = "https://data.geopf.fr/wfs/ows"
PPR_BATCH = 64
PPR_WORKERS = 2  # IGN's gateway rate-limits (HTTP 429) bursts of parallel queries


def _zones_containing(fetcher: Fetcher, schools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = ",".join(f"({s['lat']} {s['lon']})" for s in schools)  # EPSG:4326 axis order
    body = fetcher.get_json(WFS_SUP, {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "wfs_sup:assiette_sup_s", "outputFormat": "application/json", "count": 1000,
        "propertyName": "gid,nomass,nomsuplitt",
        "CQL_FILTER": f"suptype='pm1' AND INTERSECTS(the_geom,MULTIPOINT({points}))",
    })
    if body is None or "features" not in body:
        raise SourceError(f"Unexpected answer from the easements WFS: {str(body)[:200]}")
    return body["features"]


def fetch_ppr_batched(
    fetcher: Fetcher, schools: list[dict[str, Any]], workers: int = 4
) -> dict[str, dict[str, Any]]:
    """{uai: {"features": [...]}} — the shape of GPU_API answers, for `ppri()`."""
    def resolve(batch: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        features = _zones_containing(fetcher, batch)
        if not features or len(batch) == 1:
            return {s["uai"]: {"features": features} for s in batch}
        half = len(batch) // 2
        return resolve(batch[:half]) | resolve(batch[half:])

    ordered = sorted(schools, key=lambda s: (round(s["lat"], 1), s["lon"]))  # neighbours together
    batches = [ordered[i:i + PPR_BATCH] for i in range(0, len(ordered), PPR_BATCH)]
    logger.info("Flood-plan zones: %d schools in %d batches", len(schools), len(batches))
    out: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(workers, PPR_WORKERS)) as pool:
        for part in pool.map(resolve, batches):
            out |= part
    return out


# ---- transforms -------------------------------------------------------------
_RGA = {"forte": "fort", "moyenne": "moyen", "faible": "faible"}


def argiles_classe(rga: dict[str, Any] | None) -> str:
    label = ((rga or {}).get("exposition") or "").lower()
    for word, classe in _RGA.items():
        if word in label:
            return classe
    return "non cartographié"


def ppri(gpu: dict[str, Any] | None) -> str | None:
    """Name of the flood-risk plan (PPRI) covering the point, or None."""
    for feature in (gpu or {}).get("features", []):
        props = feature.get("properties", {})
        name = props.get("nomsuplitt") or props.get("nomass") or ""
        if "ppri" in name.lower() or "inond" in name.lower():
            return name
    return None


def catnat_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Counts distinct decrees: one decree can cover both floods and ground movement."""
    def codes(keyword: str | None) -> int:
        return len(
            {
                r["code_national_catnat"]
                for r in rows
                if keyword is None or keyword in (r.get("libelle_risque_jo") or "").lower()
            }
        )

    return {
        "nb_catnat": codes(None),
        "nb_catnat_inondations": codes("inondation"),
        "nb_catnat_secheresse": codes("sécheresse"),
        "nb_catnat_mouvement_terrain": codes("mouvement de terrain"),
    }


def annee_dicrim(rows: list[dict[str, Any]]) -> int | None:
    years = [int(r["annee_publication"]) for r in rows if r.get("annee_publication")]
    return max(years) if years else None


def is_seveso(installation: dict[str, Any]) -> bool:
    return (installation.get("statutSeveso") or "").startswith("Seveso")


def icpe_metrics(lat: float, lon: float, icpe: list[dict[str, Any]]) -> dict[str, Any]:
    """Nearest active classified site, sites within 1 km, nearest Seveso site."""
    installations = [
        i for i in icpe
        if i.get("latitude") is not None
        and i.get("longitude") is not None
        and i.get("regime") != "Non ICPE"
        and i.get("etatActivite") != "En fin d'exploitation"
    ]
    distances = [haversine_km(lat, lon, i["latitude"], i["longitude"]) for i in installations]
    seveso = [d for d, i in zip(distances, installations, strict=True) if is_seveso(i)]
    return {
        "distance_icpe_km": round(min(distances), 2) if distances else None,
        "nb_icpe_1km": sum(1 for d in distances if d <= 1.0),
        "distance_seveso_km": round(min(seveso), 2) if seveso else None,
    }
