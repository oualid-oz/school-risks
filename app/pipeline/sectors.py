"""Catchment sectors of the collèges, from the Département du Loiret (open licence).

ArcGIS service of the department's GIS, also listed on data.gouv.fr as "Secteurs des
collèges - Département du Loiret". Each sector carries the UAI of the public collège that
serves it. The service generalises the outlines (MAX_OFFSET): with ~0.0001° (≈10 m) it gives
the same ~16 000 vertices (≈320 KB) as the original file, instead of ~94 000 (3.7 MB) that
every dashboard would download.
"""
from __future__ import annotations

from typing import Any

from app.pipeline.client import Fetcher

SERVICE = (
    "https://geoloiret.loiret-numerique.fr/ags25/rest/services/III_11_Zones_Gestion/"
    "SECTORISATION_EDUCATION/MapServer"
)
LAYER_NAME = "COLLEGES_LIMITES_SECTEURS"
MAX_OFFSET = 0.0001  # degrees
DECIMALS = 7  # ~1 cm


def _text(value: Any) -> str | None:
    """Trimmed text; blank values (the service sends " ") become None."""
    value = str(value).strip() if value is not None else ""
    return value or None


def _round(coords: Any) -> Any:
    if isinstance(coords, (int, float)):
        return round(coords, DECIMALS)
    return [_round(c) for c in coords]


def fetch(fetcher: Fetcher) -> dict[str, Any]:
    info = fetcher.get_json(SERVICE, {"f": "json"}) or {}
    layer = next((lyr["id"] for lyr in info.get("layers", []) if lyr.get("name") == LAYER_NAME), None)
    if layer is None:
        raise ValueError(f"Layer {LAYER_NAME} not found in the sectors service")
    raw = fetcher.get_json(f"{SERVICE}/{layer}/query", {
        "where": "1=1", "outFields": "clg_rne,clg_sec,clg_ville,clg_insee,clg_dep",
        "returnGeometry": "true", "outSR": 4326, "maxAllowableOffset": MAX_OFFSET, "f": "geojson",
    })
    if not raw or not raw.get("features"):
        raise ValueError("The sectors service returned no feature")
    return to_feature_collection(raw["features"])


def to_feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    """Same properties as the published file (secteur_id, secteur, commune, code_insee,
    departement), sorted by sector id so the file only changes when the data does."""
    out = []
    for f in features:
        p = f["properties"]
        out.append({
            "type": "Feature",
            "properties": {
                "secteur_id": _text(p["clg_rne"]),
                "secteur": _text(p["clg_sec"]),
                "commune": (_text(p.get("clg_ville")) or "").title() or None,
                "code_insee": _text(p.get("clg_insee")),
                "departement": (_text(p.get("clg_dep")) or "").title() or None,
            },
            "geometry": {"type": f["geometry"]["type"], "coordinates": _round(f["geometry"]["coordinates"])},
        })
    out.sort(key=lambda f: f["properties"]["secteur_id"])
    return {"type": "FeatureCollection", "features": out}


def _area(feature: dict[str, Any]) -> float:
    """Planar area in degree², only to compare two versions of the same sector."""
    geom = feature["geometry"]
    polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    total = 0.0
    for polygon in polygons:
        for i, ring in enumerate(polygon):
            pairs = zip(ring, ring[1:], strict=False)
            ring_area = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in pairs)) / 2
            total += -ring_area if i else ring_area  # holes count negative
    return total


def validate(collection: dict[str, Any], school_sectors: set[str]) -> list[str]:
    errors = []
    ids = [f["properties"]["secteur_id"] for f in collection["features"]]
    if len(ids) != len(set(ids)):
        errors.append("secteurs: duplicate sector id")
    if any(f["geometry"]["type"] not in ("Polygon", "MultiPolygon") for f in collection["features"]):
        errors.append("secteurs: non-polygon geometry")
    missing = sorted(school_sectors - set(ids))
    if missing:
        errors.append(f"secteurs: sectors of tracked schools missing from the source: {missing}")
    return errors


def compare(new: dict[str, Any], old: dict[str, Any], tolerance: float = 0.01) -> dict[str, list[str]]:
    new_by = {f["properties"]["secteur_id"]: f for f in new["features"]}
    old_by = {f["properties"]["secteur_id"]: f for f in old["features"]}
    changed = []
    for sid in sorted(set(new_by) & set(old_by)):
        before, after = _area(old_by[sid]), _area(new_by[sid])
        if before and abs(after - before) / before > tolerance:
            changed.append(f"{sid} ({(after - before) / before:+.1%})")
        elif new_by[sid]["properties"]["secteur"] != old_by[sid]["properties"]["secteur"]:
            changed.append(f"{sid} (renamed)")
    return {
        "added": sorted(set(new_by) - set(old_by)),
        "removed": sorted(set(old_by) - set(new_by)),
        "changed": changed,
    }
