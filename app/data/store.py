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
from app.services.scoring import HAZARD_NAMES, compute_risk_profile


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

    # ~67,000 school-year rows for all of France, only ever read one school at a time:
    # read on demand (get_history) instead of held in memory.
    risques, history, secteurs = read_reference(engine, with_history=False)
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
        # The reference data does not change while the process runs: lists and statistics
        # are computed once per set of arguments (all of France: ~100 ms each otherwise).
        self._cache: dict[tuple, Any] = {}
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
        area: frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Filtered schools, highest first by ``tri``: "score" (risk of the location) or
        "priorite" (pupils exposed, weighted by risk; schools without an index last).
        ``area``: the departments the user may see (None: all). Read-only rows."""
        args = (commune, statut, secteur_id, niveau, q, tri, departement, area)
        if q or commune:  # free text: not cached (the cache would grow without bound)
            return self._list_colleges(*args)
        key = ("list", *args)
        if key not in self._cache:
            self._cache[key] = self._list_colleges(*args)
        return list(self._cache[key])

    def _list_colleges(self, commune, statut, secteur_id, niveau, q, tri, departement, area):
        out = []
        for uai, r in self._risk_rows.items():
            p = self._profiles[uai]
            if departement and r.get("departement") != departement:
                continue
            if area is not None and r.get("departement") not in area:
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
        if self.history is None:  # database mode: one school's rows
            from app.db import engine
            from app.services.reference_db import read_history

            return _records(read_history(engine, uai))
        df = self.history[self.history["uai"] == uai].sort_values("rentree")
        return _records(df)

    def get_profile(self, uai: str) -> dict[str, Any] | None:
        return self._profiles.get(uai)

    def rows(self, area: frozenset[str] | None = None) -> list[dict[str, Any]]:
        """Reference rows of the schools (of an area), read-only."""
        return [r for r in self._risk_rows.values() if area is None or r.get("departement") in area]

    def uais(self) -> list[str]:
        return sorted(self._risk_rows)

    def all_profiles(self) -> dict[str, dict[str, Any]]:
        return dict(self._profiles)

    def exists(self, uai: str) -> bool:
        return uai in self._risk_rows

    # ---- sectors --------------------------------------------------------
    @staticmethod
    def _in_department(feature: dict[str, Any], departement: str | None) -> bool:
        if departement is None:
            return True
        covered = feature["properties"].get("departements")
        return covered is None or departement in covered  # files before Sprint 20: one department

    def secteurs_geojson(
        self, with_scores: bool = True, departement: str | None = None, area: frozenset[str] | None = None
    ) -> dict[str, Any]:
        """Sectors (of one department: those covering part of it), with the scores of the
        schools they serve. All of France is ~12 MB: the map asks for one department."""
        selected = [
            f for f in self.secteurs["features"]
            if self._in_department(f, departement)
            and (area is None or any(self._in_department(f, d) for d in area))
        ]
        if not with_scores:
            return {"type": "FeatureCollection", "features": selected}
        # Attach the score of the school(s) that serve each sector.
        by_sector: dict[str, list[dict[str, Any]]] = {}
        for uai, r in self._risk_rows.items():
            if r["secteur_id"]:
                by_sector.setdefault(r["secteur_id"], []).append(
                    {"uai": uai, "nom": r["nom"], **self._score_only(uai)}
                )
        features = []
        for f in selected:
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
    def territories(self, area: frozenset[str] | None = None) -> list[dict[str, Any]]:
        """Departments present in the data: académie, schools, incomplete evaluations, mean
        score and high-risk schools (the national map's colours), and where the sectors
        come from (each department shows the origin of its outlines)."""
        out: dict[str, dict[str, Any]] = {}
        for r in self._risk_rows.values():
            code = r.get("departement")
            if area is not None and code not in area:
                continue
            profile = self._profiles[r["uai"]]
            entry = out.setdefault(code, {
                "code": code, "nom": r.get("departement_nom"), "academie": r.get("academie"),
                "colleges": 0, "lacunes": 0, "eleve": 0, "score_moyen": 0.0,
                "secteurs": {"total": 0, "par_source": {}},
            })
            entry["colleges"] += 1
            entry["lacunes"] += bool(profile["lacunes"])
            entry["eleve"] += profile["niveau"] == "eleve"
            entry["score_moyen"] += profile["score_global"]
        for f in self.secteurs["features"]:
            props = f["properties"]
            for code in props.get("departements") or list(out)[:1]:  # old files: one department
                if code in out:
                    sectors = out[code]["secteurs"]
                    source = props.get("source") or "departement"
                    sectors["total"] += 1
                    sectors["par_source"][source] = sectors["par_source"].get(source, 0) + 1
        for entry in out.values():
            entry["score_moyen"] = round(entry["score_moyen"] / entry["colleges"], 1)
        return sorted(out.values(), key=lambda t: t["code"] or "")

    def stats(self, departement: str | None = None, area: frozenset[str] | None = None) -> dict[str, Any]:
        key = ("stats", departement, area)
        if key not in self._cache:
            self._cache[key] = self._stats(departement, area)
        return dict(self._cache[key])

    def _stats(self, departement: str | None, area: frozenset[str] | None) -> dict[str, Any]:
        rows = [
            r for r in self._risk_rows.values()
            if (not departement or r.get("departement") == departement)
            and (area is None or r.get("departement") in area)
        ]
        profiles = [self._profiles[r["uai"]] for r in rows]
        niveaux: dict[str, int] = {}
        eleves: dict[str, int] = {}
        for r, p in zip(rows, profiles, strict=True):
            niveaux[p["niveau"]] = niveaux.get(p["niveau"], 0) + 1
            eleves[p["niveau"]] = eleves.get(p["niveau"], 0) + int(r["effectif_total"] or 0)
        # Core hazards, then the territorial ones present at one school at least (absent: 0).
        present = {h for p in profiles for h in p["aleas"]}
        hazards = [h for h in HAZARD_NAMES if h in present]
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
                h: round(sum(p["aleas"].get(h, 0.0) for p in profiles) / len(profiles), 1)
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
