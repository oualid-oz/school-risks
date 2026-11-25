"""In-memory data store loaded once at startup from the parquet / geojson files.

The reference data is small (70 schools, ~700 yearly rows, 59 sectors), so it is kept
in memory and scored once. It comes from the files, or from PostGIS when the database is
Postgres; routes only talk to this class. Spatial queries go to PostGIS directly
(app/services/spatial.py).
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

import pandas as pd

from app.core.config import settings
from app.services.scoring import compute_risk_profile


def department_of_insee(code_insee: str) -> str:
    """Department of a commune code: "45234" -> "45", "2A004" -> "2A", "97411" -> "974"."""
    code = str(code_insee)
    return code[:3] if code.startswith("97") else code[:2]


def _clean(value: Any) -> Any:
    """Turn pandas/numpy missing values and scalars into JSON-friendly Python values."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def _read_files() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    d = settings.data_dir
    risques = pd.read_parquet(d / settings.risques_file)
    history = pd.read_parquet(d / settings.colleges_file)
    with open(d / settings.secteurs_file, encoding="utf-8") as f:
        secteurs = json.load(f)
    return risques, history, secteurs


def _read_database() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    from app.db import engine
    from app.services.reference_db import read_reference

    risques, history, secteurs = read_reference(engine)
    if risques.empty:
        raise RuntimeError(
            "The PostGIS reference tables are empty. Run: python -m app.manage load-reference"
        )
    return risques, history, secteurs


class DataStore:
    """Reads the parquet / GeoJSON files, or the PostGIS tables when the database is
    Postgres. Both sources have the same shape, so nothing else changes."""

    def __init__(self, source: str = "files") -> None:
        reader = _read_database if source == "database" else _read_files
        self.source = source
        self.risques, self.history, self.secteurs = reader()
        self.risques["ppri"] = self.risques["ppri"].replace("", None)
        if "departement" not in self.risques.columns:  # files published before Sprint 17
            self.risques["departement"] = self.risques["code_insee"].map(department_of_insee)
            self.risques["departement_nom"] = None
            self.risques["academie"] = None

        self._risk_rows = {r["uai"]: r for r in _records(self.risques)}
        self._profiles = {uai: compute_risk_profile(r) for uai, r in self._risk_rows.items()}
        self._sector_by_id = {
            f["properties"]["secteur_id"]: f for f in self.secteurs["features"]
        }

    # ---- colleges -------------------------------------------------------
    def list_colleges(
        self,
        commune: str | None = None,
        statut: str | None = None,
        secteur_id: str | None = None,
        niveau: str | None = None,
        q: str | None = None,
        tri: str = "score",
        departement: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filtered schools, highest first by ``tri``: "score" (risk of the location) or
        "priorite" (pupils exposed, weighted by risk; schools without an index last)."""
        out = []
        for uai, r in self._risk_rows.items():
            p = self._profiles[uai]
            if departement and r.get("departement") != departement:
                continue
            if commune and r["commune"].lower() != commune.lower():
                continue
            if statut and r["statut"] != statut:
                continue
            if secteur_id and r["secteur_id"] != secteur_id:
                continue
            if niveau and p["niveau"] != niveau:
                continue
            if q and q.lower() not in f"{r['nom']} {r['commune']} {uai}".lower():
                continue
            out.append(self._summary(r, p))
        if tri == "priorite":
            return sorted(
                out,
                key=lambda x: (
                    x["indice_priorite"] is not None, x["indice_priorite"] or 0, x["score_global"]
                ),
                reverse=True,
            )
        return sorted(out, key=lambda x: x["score_global"], reverse=True)

    def get_college(self, uai: str) -> dict[str, Any] | None:
        r = self._risk_rows.get(uai)
        if r is None:
            return None
        return {**r, "profil_risque": self._profiles[uai]}

    def get_history(self, uai: str) -> list[dict[str, Any]]:
        df = self.history[self.history["uai"] == uai].sort_values("rentree")
        return _records(df)

    def get_profile(self, uai: str) -> dict[str, Any] | None:
        return self._profiles.get(uai)

    def uais(self) -> list[str]:
        return sorted(self._risk_rows)

    def all_profiles(self) -> dict[str, dict[str, Any]]:
        return dict(self._profiles)

    def exists(self, uai: str) -> bool:
        return uai in self._risk_rows

    # ---- sectors --------------------------------------------------------
    def secteurs_geojson(self, with_scores: bool = True) -> dict[str, Any]:
        if not with_scores:
            return self.secteurs
        # Attach the score of the school(s) that serve each sector.
        by_sector: dict[str, list[dict[str, Any]]] = {}
        for uai, r in self._risk_rows.items():
            if r["secteur_id"]:
                by_sector.setdefault(r["secteur_id"], []).append(
                    {"uai": uai, "nom": r["nom"], **self._score_only(uai)}
                )
        features = []
        for f in self.secteurs["features"]:
            sid = f["properties"]["secteur_id"]
            schools = by_sector.get(sid, [])
            max_score = max((s["score_global"] for s in schools), default=None)
            features.append(
                {
                    **f,
                    "properties": {
                        **f["properties"],
                        "colleges": schools,
                        "score_max": max_score,
                    },
                }
            )
        return {"type": "FeatureCollection", "features": features}

    def get_secteur(self, secteur_id: str) -> dict[str, Any] | None:
        return self._sector_by_id.get(secteur_id)

    # ---- stats ----------------------------------------------------------
    def territories(self) -> list[dict[str, Any]]:
        """Departments present in the data, with their académie and number of schools."""
        out: dict[str, dict[str, Any]] = {}
        for r in self._risk_rows.values():
            code = r.get("departement")
            entry = out.setdefault(code, {
                "code": code, "nom": r.get("departement_nom"), "academie": r.get("academie"),
                "colleges": 0, "lacunes": 0,
            })
            entry["colleges"] += 1
            entry["lacunes"] += bool(self._profiles[r["uai"]]["lacunes"])
        return sorted(out.values(), key=lambda t: t["code"] or "")

    def stats(self, departement: str | None = None) -> dict[str, Any]:
        rows = [r for r in self._risk_rows.values() if not departement or r.get("departement") == departement]
        profiles = [self._profiles[r["uai"]] for r in rows]
        niveaux: dict[str, int] = {}
        eleves: dict[str, int] = {}
        for r, p in zip(rows, profiles, strict=True):
            niveaux[p["niveau"]] = niveaux.get(p["niveau"], 0) + 1
            eleves[p["niveau"]] = eleves.get(p["niveau"], 0) + int(r["effectif_total"] or 0)
        hazards = profiles[0]["aleas"].keys() if profiles else []
        return {
            "nb_colleges": len(rows),
            "nb_eleves": int(sum(r["effectif_total"] or 0 for r in rows)),
            "par_niveau": niveaux,
            "eleves_par_niveau": eleves,
            "sans_effectif": sum(1 for p in profiles if p["indice_priorite"] is None),
            "evaluation_incomplete": sum(1 for p in profiles if p["lacunes"]),
            "dans_ppri": sum(1 for r in rows if r["dans_ppri"]),
            "dans_zone_tmd_500m": sum(1 for r in rows if r["dans_zone_tmd_500m"]),
            "argiles_fort": sum(1 for r in rows if r["argiles_classe"] == "fort"),
            "dicrim_absent_ou_ancien": sum(
                1 for p in profiles if any(a.startswith("DICRIM") for a in p["alertes"])
            ),
            "score_moyen_par_alea": {
                h: round(sum(p["aleas"][h] for p in profiles) / len(profiles), 1)
                for h in hazards
            },
        }

    # ---- helpers --------------------------------------------------------
    def _score_only(self, uai: str) -> dict[str, Any]:
        p = self._profiles[uai]
        return {"score_global": p["score_global"], "niveau": p["niveau"]}

    @staticmethod
    def _summary(r: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
        return {
            "uai": r["uai"],
            "nom": r["nom"],
            "commune": r["commune"],
            "statut": r["statut"],
            "departement": r.get("departement"),
            "departement_nom": r.get("departement_nom"),
            "lat": r["lat"],
            "lon": r["lon"],
            "secteur_id": r["secteur_id"],
            "effectif_total": r["effectif_total"],
            "ips": r["ips"],
            "score_global": p["score_global"],
            "niveau": p["niveau"],
            "alea_principal": p["alea_principal"],
            "indice_priorite": p["indice_priorite"],
        }


@lru_cache
def get_store() -> DataStore:
    from app.db import is_postgres

    return DataStore("database" if is_postgres() else "files")
