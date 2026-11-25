"""Hazards that only exist in part of the territory (Sprint 19): earthquake, radon,
wildfire, coast (marine flooding, coastline retreat) and avalanche.

All per commune:
- GASPAR national export (already downloaded for CatNat/DICRIM): the DDRM listing of the
  hazards each commune is exposed to, the natural-risk plans (PPRN) prescribed or approved
  for it, and CatNat decrees for waves and avalanches.
- Géorisques API: the official earthquake zone (1 very low … 5 strong, decree of 2010) and
  the radon potential class (1 low … 3 significant, IRSN/ASN).

Pure Python (no PostGIS), so the API mode and the bulk mode share it.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections import defaultdict
from typing import Any

from app.pipeline.client import Fetcher
from app.pipeline.georisques import API, _parallel

logger = logging.getLogger(__name__)
COMMUNE_WORKERS = 4  # Géorisques resets connections when 8 parallel requests run for minutes

# DDRM hazard codes (GASPAR `num_risque`).
DDRM_AVALANCHE, DDRM_FEU_FORET = "14", "16"
# PPRN hazard labels (GASPAR `libelle_risque2` / `libelle_risque3`).
PPR_SUBMERSION = "Par submersion marine"
PPR_RECUL_COTE = "Recul du trait de côte et de falaises"
PPR_AVALANCHE = "Avalanche"
PPR_FEU_FORET = "Feu de forêt"
# CatNat decree labels (`libelle_risque_jo`, lower case).
CATNAT_LITTORAL = ("chocs mécaniques liés à l'action des vagues", "raz de marée")
CATNAT_AVALANCHE = ("avalanche",)

COMMUNE_COLUMNS = {
    "ddrm_feu_foret": "boolean",
    "ppr_feu_foret": "boolean",
    "ppr_submersion_marine": "boolean",
    "ppr_recul_trait_cote": "boolean",
    "nb_catnat_littoral": "Int64",
    "ddrm_avalanche": "boolean",
    "ppr_avalanche": "boolean",
    "nb_catnat_avalanche": "Int64",
}
POINT_COLUMNS = {"zone_sismique": "Int64", "classe_radon": "Int64"}


def _rows(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig")), delimiter=";"))


def commune_hazards(gaspar_zip: bytes, communes: set[str]) -> dict[str, dict[str, Any]]:
    """{code_insee: COMMUNE_COLUMNS values} for these communes (all False / 0 when unlisted)."""
    archive = zipfile.ZipFile(io.BytesIO(gaspar_zip))
    out: dict[str, dict[str, Any]] = {
        c: {k: (0 if t == "Int64" else False) for k, t in COMMUNE_COLUMNS.items()} for c in communes
    }
    for r in _rows(archive, "ddrm.csv"):
        c = out.get(r["code_commune"])
        if c is not None:
            c["ddrm_feu_foret"] |= r["num_risque"] == DDRM_FEU_FORET
            c["ddrm_avalanche"] |= r["num_risque"] == DDRM_AVALANCHE
    for r in _rows(archive, "pprn.csv"):
        c = out.get(r["code_insee_commune"])
        if c is not None:
            kinds = {r["libelle_risque2"], r["libelle_risque3"]}
            c["ppr_submersion_marine"] |= PPR_SUBMERSION in kinds
            c["ppr_recul_trait_cote"] |= PPR_RECUL_COTE in kinds
            c["ppr_avalanche"] |= PPR_AVALANCHE in kinds
            c["ppr_feu_foret"] |= PPR_FEU_FORET in kinds
    decrees: dict[tuple[str, str], set[str]] = defaultdict(set)  # distinct decrees, as nb_catnat
    for r in _rows(archive, "catnat.csv"):
        if r["code_commune"] not in out:
            continue
        label = (r["libelle_risque_jo"] or "").lower()
        if label in CATNAT_LITTORAL:
            decrees[(r["code_commune"], "nb_catnat_littoral")].add(r["id_gaspar"])
        elif label in CATNAT_AVALANCHE:
            decrees[(r["code_commune"], "nb_catnat_avalanche")].add(r["id_gaspar"])
    for (commune, column), ids in decrees.items():
        out[commune][column] = len(ids)
    return out


def _int(body: dict[str, Any] | None, key: str) -> int | None:
    data = (body or {}).get("data") or []
    try:
        return int(data[0][key]) if data and data[0].get(key) not in (None, "") else None
    except (TypeError, ValueError):
        return None


def fetch_seismic_radon(
    fetcher: Fetcher, communes: list[str], workers: int = 4
) -> dict[str, dict[str, int | None]]:
    """{code_insee: {"zone_sismique": 1–5, "classe_radon": 1–3}}; None where not published."""
    def one(code: str) -> dict[str, int | None]:
        return {
            "zone_sismique": _int(fetcher.get_json(f"{API}/zonage_sismique", {"code_insee": code}),
                                  "code_zone"),
            "classe_radon": _int(fetcher.get_json(f"{API}/radon", {"code_insee": code}),
                                 "classe_potentiel"),
        }

    logger.info("Earthquake zone and radon class: %d communes", len(communes))
    return _parallel(one, sorted(set(communes)), min(workers, COMMUNE_WORKERS))
