"""Assemble, validate, compare and publish the reference data files."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.data.schema import HISTORY_SCHEMA, RISQUES_SCHEMA
from app.pipeline import bulk, education, georisques, hazards, nuclear, school_map, sectors
from app.pipeline.client import Fetcher
from app.pipeline.geo import haversine_km, nearest_centrale
from app.services.scoring import compute_risk_profile

logger = logging.getLogger(__name__)

def _department_of(row: Any) -> str:
    value = row.get("departement") if hasattr(row, "get") else None
    if isinstance(value, str) and value:
        return value
    code = str(row["code_insee"])
    return code[:3] if code.startswith("97") else code[:2]


# "All of France" is mainland France and Corsica for now; overseas comes with Sprint 22.
MAINLAND = re.compile(r"^(0[1-9]|1[0-9]|2[1-9AB]|[3-8][0-9]|9[0-5])$")
ROLLING_CYCLE_MONTHS = 6


def rolling_due(key: str, now: datetime) -> bool:
    """Bulk mode re-checks slow-changing per-point data (clay map, earthquake zoning, radon
    classes) every ROLLING_CYCLE_MONTHS months: a stable sixth of the schools or communes
    per month. One call each for all of France every month would take half the run."""
    cycle = ROLLING_CYCLE_MONTHS
    return zlib.crc32(key.encode()) % cycle == (now.year * 12 + now.month) % cycle


def _seismic_radon(
    fetcher: Fetcher, schools: list[dict[str, Any]], roster: pd.DataFrame, workers: int,
    rolling: bool, now: datetime, notes: list[str],
) -> dict[str, dict[str, Any]]:
    """Earthquake zone and radon class per school, fetched per commune. With `rolling`, a
    commune is only re-checked in its month, unless one of its schools has no value yet."""
    columns = ("zone_sismique", "classe_radon")

    def known(uai: str) -> dict[str, Any] | None:
        if uai not in roster.index or not set(columns) <= set(roster.columns):
            return None
        values = {c: roster.loc[uai, c] for c in columns}
        return None if any(pd.isna(v) for v in values.values()) else {c: int(v) for c, v in values.items()}

    previous = {s["uai"]: known(s["uai"]) for s in schools}
    communes = {s["code_insee"] for s in schools}
    due = {s["code_insee"] for s in schools if previous[s["uai"]] is None} | {
        c for c in communes if not rolling or rolling_due(c, now)
    }
    fetched = hazards.fetch_seismic_radon(fetcher, sorted(due), workers)
    if len(due) < len(communes):
        notes.append(f"Earthquake zone and radon class re-checked for {len(due)} of {len(communes)} "
                     "communes (rolling sixth, and communes with a school not evaluated yet).")
    return {
        s["uai"]: fetched[s["code_insee"]] if s["code_insee"] in fetched else previous[s["uai"]]
        for s in schools
    }


# Columns with no public point API. They are copied from the published file and listed
# in the report, so whoever runs the pipeline knows they were not refreshed.
CARRIED_OVER = [
    "secteur_id",
    "distance_tmd_m",
    "type_tmd_proche",
    "dans_zone_tmd_500m",
    "distance_repere_crue_km",
]

COORDINATE_DRIFT_KM = 0.2

# Columns that must never be empty in colleges_risques.
REQUIRED = [
    "uai", "nom", "commune", "code_insee", "statut", "departement", "lat", "lon", "argiles_classe",
    "nb_cavites_500m", "nb_mouvements_terrain_500m", "nb_icpe_1km", "distance_centrale_km",
    "nb_catnat",
]
# Where a French collège can be: (lat min, lat max, lon min, lon max) per territory.
TERRITORIES = {
    "métropole et Corse": (41.3, 51.2, -5.3, 9.7),
    "Antilles": (14.3, 18.2, -63.2, -60.7),
    "Guyane": (2.0, 6.0, -54.7, -51.5),
    "La Réunion": (-21.5, -20.8, 55.1, 55.9),
    "Mayotte": (-13.1, -12.6, 44.9, 45.4),
}


def in_france(lat: float, lon: float) -> bool:
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in TERRITORIES.values())


RANGES = {
    "distance_centrale_km": (0, 20000),
    "distance_icpe_km": (0, 50),
    "distance_seveso_km": (0, 100),
    "ips": (50, 170),
    "zone_sismique": (1, 5),
    "classe_radon": (1, 3),
}


@dataclass
class BuildResult:
    risques: pd.DataFrame
    history: pd.DataFrame
    notes: list[str] = field(default_factory=list)
    secteurs: dict[str, Any] | None = None  # None: keep the published sectors file
    # Per department: where its sectors come from, how many, how many approximate.
    sector_coverage: dict[str, dict[str, Any]] = field(default_factory=dict)


def _typed(rows: list[dict[str, Any]], schema: dict[str, str]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for column, dtype in schema.items():
        df[column] = df[column].astype(dtype)  # "str" keeps missing values missing
    return df[list(schema)]


def build(
    fetcher: Fetcher,
    previous_risques: pd.DataFrame,
    departements: list[str] | None = None,
    first_year: int = 2016,
    workers: int = 4,
    keep: list[str] | None = None,
    bulk_engine: Engine | None = None,
    bulk_cache: Path | None = None,
    offline: bool = False,
    refresh_all: bool = False,
) -> BuildResult:
    """Rebuild both tables for the collèges of ``departements`` (default: settings).

    The list of schools comes from the education directory (open collèges, public or
    private under contract). Schools already in the published file keep their name,
    position and sector, so distances stay comparable from one refresh to the next.
    ``keep`` lists extra risk columns to copy from the published file instead of
    refreshing them (for example while a source change is being reviewed).
    """
    departements = sorted(departements or settings.departements)
    everything = departements == ["ALL"]  # all of France: bulk mode only
    if everything and bulk_engine is None:
        raise ValueError("All of France needs the bulk mode (SRT_PIPELINE_DATABASE_URL)")
    keep = list(keep or [])
    unknown = [c for c in keep if c not in RISQUES_SCHEMA or c == "uai"]
    if unknown:
        raise ValueError(f"Unknown columns for --keep: {unknown}")
    notes: list[str] = []
    roster = previous_risques.set_index("uai")

    logger.info("Downloading the education directory for %s", departements)
    annuaire = education.annuaire_by_uai(
        education.fetch_directory(fetcher, None if everything else departements)
    )
    if everything:  # mainland France and Corsica; overseas departments come later (Sprint 22)
        annuaire = {u: a for u, a in annuaire.items() if MAINLAND.match(a["departement"] or "")}
        departements = sorted({a["departement"] for a in annuaire.values() if a["tracked"]})
    notes.append(f"Departments ({len(departements)}): {', '.join(departements)}")
    uais = sorted(u for u, a in annuaire.items() if a["tracked"])
    if not uais:
        raise ValueError(f"No collège found in the directory for {departements}")
    added = sorted(set(uais) - set(roster.index))
    listed = set(uais)
    gone = sorted(
        u for u in roster.index if u not in listed and _department_of(roster.loc[u]) in departements
    )
    if added:
        notes.append(f"Schools added ({len(added)}): {_capped(added)}")
    if gone:
        notes.append(f"Schools no longer listed as open collèges in the directory ({len(gone)}): "
                     f"{_capped(gone)}")

    logger.info("Downloading data.education.gouv.fr datasets (%d schools)", len(uais))
    edu = education.fetch_all(fetcher, uais)

    # Identity and position come from the published file when the school is there, so
    # distances stay comparable; positions that drifted in the directory are reported.
    schools, moved = [], []
    for uai in uais:
        a = annuaire[uai]
        territory = {"departement": a["departement"], "departement_nom": a["departement_nom"],
                     "academie": a["academie"]}
        if uai in roster.index:
            prev = roster.loc[uai]
            if a["lat"] is not None and a["lon"] is not None:
                shift = haversine_km(prev["lat"], prev["lon"], a["lat"], a["lon"])
                if shift > COORDINATE_DRIFT_KM:
                    moved.append(f"{uai} ({shift:.2f} km)")
            schools.append({
                "uai": uai, "nom": prev["nom"], "commune": prev["commune"], "code_insee": prev["code_insee"],
                "statut": a["statut"], **territory, "lat": float(prev["lat"]), "lon": float(prev["lon"]),
                "secteur_id": prev["secteur_id"],
            })
        else:
            if a["lat"] is None or a["lon"] is None:
                notes.append(f"{uai}: no position in the directory, left out")
                continue
            schools.append({
                "uai": uai, "nom": education.short_name(a["nom_etablissement"] or uai),
                "commune": a["commune"], "code_insee": a["code_insee"], "statut": a["statut"], **territory,
                "lat": round(a["lat"], 5), "lon": round(a["lon"], 5), "secteur_id": None,
            })
    if moved:
        notes.append(
            f"Directory position differs by more than {COORDINATE_DRIFT_KM * 1000:.0f} m "
            f"(kept the published position; check and fix by hand if needed): {_capped(moved)}"
        )

    history = _typed(education.build_history(schools, edu, first_year), HISTORY_SCHEMA)
    latest = history.sort_values("rentree").groupby("uai").last()

    now = datetime.now(UTC)
    dl = bulk.Downloader(bulk_cache or settings.data_dir / "raw" / "bulk", offline=offline)
    try:
        if bulk_engine is not None:
            logger.info("Bulk mode: national files into PostGIS")
            bulk.reset_schema(bulk_engine)
            loaded = bulk.load_national(bulk_engine, dl)
            nearby = bulk.departments_near(bulk_engine, schools)
            loaded |= bulk.load_departmental(bulk_engine, dl, nearby)
        gaspar = dl.get("gaspar_national.zip", bulk.GASPAR_URL)  # cached when loaded above
    finally:
        dl.close()
    communal = hazards.commune_hazards(gaspar, {s["code_insee"] for s in schools})
    seismic = _seismic_radon(fetcher, schools, roster, workers,
                             rolling=bulk_engine is not None and not refresh_all, now=now, notes=notes)

    if bulk_engine is not None:
        computed = bulk.indicators(bulk_engine, schools)
        # Clay: new schools, plus a rolling sixth of the others each month (see rolling_due).
        due = [s for s in schools
               if refresh_all or s["uai"] not in roster.index or rolling_due(s["uai"], now)]
        clay = georisques.fetch_clay(fetcher, due, workers)
        ppr = georisques.fetch_ppr_batched(fetcher, schools, workers)
        if len(due) < len(schools):
            notes.append(
                f"Clay exposure re-checked for {len(due)} schools (new ones and this month's "
                f"rolling sixth); the {len(schools) - len(due)} others keep the published value."
            )
        notes.append(
            f"Bulk mode: {loaded['icpe']} ICPE, {loaded['catnat']} CatNat rows, "
            f"{loaded['cavites']} cavities, {loaded['mvt']} ground movements "
            f"({loaded['departements']} departments). ICPE counts include "
            "sites winding down (the bulk file has no activity state)."
        )
    else:
        logger.info("Downloading Géorisques data")
        geo = georisques.fetch_all(fetcher, schools, workers)
    plants, plant_problems = nuclear.fetch_plants(fetcher)
    if plant_problems:
        notes.append(f"Nuclear plants not confirmed by Géorisques: {plant_problems}")

    rows = []
    for s in schools:
        uai = s["uai"]
        prev = roster.loc[uai] if uai in roster.index else None
        if bulk_engine is not None:
            point, measured = {"ppr": ppr[uai], "rga": clay.get(uai)}, computed[uai]
        else:
            point = geo["schools"][uai]
            commune = geo["communes"][s["code_insee"]]
            measured = {
                "nb_cavites_500m": len(point["cavites"]),
                "nb_mouvements_terrain_500m": len(point["mvt"]),
                **georisques.icpe_metrics(s["lat"], s["lon"], point["icpe"]),
                **georisques.catnat_counts(commune["catnat"]),
                "annee_dicrim": georisques.annee_dicrim(commune["dicrim"]),
            }
        plan = georisques.ppri(point["ppr"])
        centrale, centrale_km = nearest_centrale(s["lat"], s["lon"], plants)
        rows.append(
            {
                **s,
                # A school just opened has no school-year figures yet: unknown, not an error.
                "effectif_total": latest.loc[uai, "effectif_total"] if uai in latest.index else None,
                "ips": latest.loc[uai, "ips"] if uai in latest.index else None,
                "argiles_classe": georisques.argiles_classe(point["rga"])
                if bulk_engine is None or uai in clay else prev["argiles_classe"],
                "dans_ppri": plan is not None,
                "ppri": plan or "",
                **measured,
                **communal[s["code_insee"]],
                **seismic[uai],
                "distance_centrale_km": round(centrale_km, 1),
                "centrale_proche": centrale,
                # No public source: copied for the schools already published, unknown otherwise.
                **{c: (prev[c] if prev is not None else None) for c in CARRIED_OVER + keep
                   if c != "secteur_id"},
            }
        )
    without = sum(1 for s in schools if s["uai"] not in roster.index)
    notes.append(f"Carried over from the previous file (no public source): {CARRIED_OVER}"
                 + (f"; unknown for the {without} new schools" if without else ""))

    secteurs, covered = sectors.fetch_for(fetcher, departements)
    coverage: dict[str, dict[str, Any]] = {
        d: {"source": "departement", "sectors": sum(1 for f in secteurs["features"]
                                                   if d in f["properties"]["departements"]),
            "approximate": 0}
        for d in covered
    }
    if bulk_engine is not None:
        dl = bulk.Downloader(bulk_cache or settings.data_dir / "raw" / "bulk", offline=offline)
        try:
            school_map.load(bulk_engine, dl, schools)
        finally:
            dl.close()
        extra, from_map = school_map.build(bulk_engine, {s["uai"]: s for s in schools}, covered)
        wanted = set(departements)
        extra = [f for f in extra if wanted & set(f["properties"]["departements"])]
        secteurs = sectors.merge(secteurs, extra)
        for d in departements:
            if d not in coverage and d in from_map:
                coverage[d] = {"source": "carte_scolaire", **from_map[d]}
    missing_sectors = [d for d in departements if d not in coverage]
    notes.append(f"Sectors: {len(secteurs['features'])}; departments with their own map: "
                 f"{covered or 'none'}" + (f"; no source for {_capped(missing_sectors)}"
                                           if missing_sectors else "") + ".")

    # A public collège gets its own sector when one exists (published schools keep theirs).
    ids = {f["properties"]["secteur_id"] for f in secteurs["features"]}
    for row in rows:
        if not row["secteur_id"] and row["statut"].lower().startswith("pub") and row["uai"] in ids:
            row["secteur_id"] = row["uai"]
    orphans: dict[str, int] = {}
    for row in rows:
        if row["statut"].lower().startswith("pub") and not row["secteur_id"]:
            orphans[row["departement"]] = orphans.get(row["departement"], 0) + 1
    if orphans:
        notes.append(f"Public collèges without a sector ({sum(orphans.values())}): "
                     + ", ".join(f"{d}: {n}" for d, n in sorted(orphans.items())))
    if keep:
        notes.append(f"Kept from the previous file on request (--keep): {keep}")
    return BuildResult(_typed(rows, RISQUES_SCHEMA), history, notes, secteurs=secteurs,
                       sector_coverage=coverage)


# ---- validation ---------------------------------------------------------------
def validate(result: BuildResult, previous_risques: pd.DataFrame) -> list[str]:
    """Blocking problems. An empty list means the files can be published."""
    errors = []
    r, h = result.risques, result.history
    for name, df, schema in (("risques", r, RISQUES_SCHEMA), ("history", h, HISTORY_SCHEMA)):
        if list(df.columns) != list(schema):
            errors.append(f"{name}: columns differ from the schema")
    # Schools come and go with the directory; a sudden drop means a broken source.
    before = previous_risques.apply(_department_of, axis=1).value_counts()
    after = r["departement"].value_counts()
    for dep, count in before.items():
        if dep in after.index and after[dep] < 0.9 * count:
            errors.append(f"risques: department {dep} drops from {count} to {after[dep]} schools")
    if not set(h["uai"]) <= set(r["uai"]):
        errors.append("history: schools missing from the risk table")
    # A school that just opened has no school-year figures yet; many of them is a broken source.
    without = sorted(set(r["uai"]) - set(h["uai"]))
    if len(without) > max(2, 0.01 * len(r)):
        errors.append(f"history: {len(without)} schools without any school-year figures: {_capped(without)}")
    if r["uai"].duplicated().any():
        errors.append("risques: duplicate UAI")
    if h.duplicated(["uai", "rentree"]).any():
        errors.append("history: duplicate (uai, rentree)")
    for column in REQUIRED:
        if r[column].isna().any():
            errors.append(f"risques: empty values in {column}: {r.loc[r[column].isna(), 'uai'].tolist()}")
    outside = r.loc[[not in_france(la, lo) for la, lo in zip(r["lat"], r["lon"], strict=True)], "uai"]
    if len(outside):
        errors.append(f"risques: position outside France for {outside.tolist()}")
    for column, (low, high) in RANGES.items():
        if column not in r:  # a file written before the column existed
            continue
        values = r[column].dropna()
        bad = r.loc[values.index[(values < low) | (values > high)], "uai"].tolist()
        if bad:
            errors.append(f"risques: {column} outside [{low}, {high}] for {bad}")
    if result.secteurs is not None:
        school_sectors = {s for s in r["secteur_id"].dropna() if s}
        errors += sectors.validate(result.secteurs, school_sectors)
    if not set(r["argiles_classe"]) <= {"faible", "moyen", "fort", "non cartographié"}:
        errors.append("risques: unknown argiles_classe value")
    return errors


def _profiles(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    from app.data.store import _records

    return {row["uai"]: compute_risk_profile(row) for row in _records(df)}


# Differences below these are rounding noise between sources and are not reported.
TOLERANCE_KM = 0.05


def _differs(column: str, a: pd.Series, b: pd.Series) -> pd.Series:
    both_missing = a.isna() & b.isna()
    if pd.api.types.is_float_dtype(b):
        tolerance = TOLERANCE_KM if column.endswith("_km") else 1e-6
        return ~(both_missing | ((a - b).abs() < tolerance))
    return ~(both_missing | (a.astype(object) == b.astype(object)))


def compare_history(new: pd.DataFrame, old: pd.DataFrame) -> dict[str, Any]:
    keys = ["uai", "rentree"]
    merged = old.merge(new, on=keys, how="outer", suffixes=("_old", ""), indicator=True)
    both = merged[merged["_merge"] == "both"]
    changed = {}
    for column in new.columns:
        if column in keys:
            continue
        n = int(_differs(column, both[f"{column}_old"], both[column]).sum())
        if n:
            changed[column] = n
    return {
        "added": merged.loc[merged["_merge"] == "right_only", keys].values.tolist(),
        "removed": merged.loc[merged["_merge"] == "left_only", keys].values.tolist(),
        "changed": changed,
    }


def compare(
    new: pd.DataFrame,
    old: pd.DataFrame,
    new_history: pd.DataFrame | None = None,
    old_history: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """What changed per column for the schools in both files, how scores and levels move,
    and which schools were added or removed."""
    common = sorted(set(new["uai"]) & set(old["uai"]))
    new_i, old_i = new.set_index("uai").loc[common], old.set_index("uai").loc[common]
    changes: dict[str, list[dict[str, Any]]] = {}
    for column in new_i.columns:
        if column not in old_i.columns:  # a column added to the schema
            continue
        a, b = old_i[column], new_i[column]
        differs = _differs(column, a, b)
        for uai in differs[differs].index:
            changes.setdefault(column, []).append(
                {"uai": uai, "old": _plain(a[uai]), "new": _plain(b[uai])}
            )

    old_p, new_p = _profiles(old), _profiles(new)
    scores = sorted(
        (
            {
                "uai": uai,
                "nom": new_i.loc[uai, "nom"],
                "old": old_p[uai]["score_global"],
                "new": new_p[uai]["score_global"],
                "old_niveau": old_p[uai]["niveau"],
                "new_niveau": new_p[uai]["niveau"],
            }
            for uai in new_p
            if uai in old_p
        ),
        key=lambda s: abs(s["new"] - s["old"]),
        reverse=True,
    )
    history = (
        compare_history(new_history, old_history)
        if new_history is not None and old_history is not None
        else None
    )
    return {
        "columns": changes, "scores": scores, "history": history,
        "added": sorted(set(new["uai"]) - set(old["uai"])),
        "removed": sorted(set(old["uai"]) - set(new["uai"])),
        "departements": _by_department(new, old, new_p, scores),
    }


def _by_department(
    new: pd.DataFrame, old: pd.DataFrame, new_p: dict[str, dict[str, Any]], scores: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """One line per department: what a reviewer of a national refresh looks at first."""
    dep_new = {r["uai"]: _department_of(r) for _, r in new.iterrows()}
    dep_old = {r["uai"]: _department_of(r) for _, r in old.iterrows()}
    moved = {s["uai"] for s in scores if s["old_niveau"] != s["new_niveau"]}
    out = []
    for dep in sorted(set(dep_new.values()) | set(dep_old.values())):
        here = [u for u, d in dep_new.items() if d == dep]
        before = {u for u, d in dep_old.items() if d == dep}
        profiles = [new_p[u] for u in here]
        out.append({
            "departement": dep,
            "schools": len(here),
            "previous": len(before),
            "added": len(set(here) - before),
            "removed": len(before - set(here)),
            "level_changes": len(moved & set(here)),
            "mean_score": round(sum(p["score_global"] for p in profiles) / len(profiles), 1)
            if profiles else None,
            "eleve": sum(1 for p in profiles if p["niveau"] == "eleve"),
            "incomplete": sum(1 for p in profiles if p.get("lacunes")),
        })
    return out


def _sector_cell(coverage: dict[str, Any] | None) -> str:
    if not coverage:
        return "none"
    if coverage["source"] == "departement":
        return f"department map ({coverage['sectors']})"
    return f"school map ({coverage['sectors']}, {coverage['approximate']} approx.)"


REPORT_MAX_LINES = 50  # a national refresh must still give a readable report


def _capped(items: list[Any], limit: int = 20) -> str:
    shown = ", ".join(str(i) for i in items[:limit])
    return shown + (f" … (+{len(items) - limit})" if len(items) > limit else "")


def _plain(value: Any) -> Any:
    if value is None or value is pd.NA or (isinstance(value, float) and pd.isna(value)):
        return None
    return value.item() if hasattr(value, "item") else value


def render_report(
    result: BuildResult, diff: dict[str, Any], errors: list[str], started: datetime
) -> str:
    lines = [
        f"# Data refresh {started:%Y-%m-%d %H:%M} UTC",
        "",
        f"- Schools: {len(result.risques)}, history rows: {len(result.history)} "
        f"(school years {result.history['rentree'].min()}–{result.history['rentree'].max()})",
        f"- Added schools: {len(diff.get('added', []))}, removed: {len(diff.get('removed', []))}"
        + (f" {_capped(diff['removed'])}" if diff.get("removed") else ""),
        f"- Validation: {'**FAILED**' if errors else 'passed'}",
    ]
    lines += [f"  - {e}" for e in errors]
    lines += ["", "## Notes", ""] + [f"- {n}" for n in result.notes]

    deps = diff.get("departements") or []
    if len(deps) > 1:
        lines += [
            "", "## Per department", "",
            "| Dept | Schools | Before | Added | Removed | Level changes | Mean score | High | Incomplete "
            "| Sectors |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {d['departement']} | {d['schools']} | {d['previous']} | {d['added']} | {d['removed']} "
            f"| {d['level_changes']} | {d['mean_score'] if d['mean_score'] is not None else '–'} "
            f"| {d['eleve']} | {d['incomplete']} "
            f"| {_sector_cell(result.sector_coverage.get(d['departement']))} |"
            for d in deps
        ]

    moved = [s for s in diff["scores"] if s["old_niveau"] != s["new_niveau"]]
    lines += ["", f"## Level changes ({len(moved)})", ""]
    lines += [
        f"- {s['uai']} {s['nom']}: {s['old_niveau']} → {s['new_niveau']} ({s['old']} → {s['new']})"
        for s in moved[:REPORT_MAX_LINES]
    ] or ["None."]
    if len(moved) > REPORT_MAX_LINES:
        lines.append(f"- … and {len(moved) - REPORT_MAX_LINES} more (largest score changes first)")

    lines += ["", "## Biggest score changes", "", "| UAI | School | Old | New |", "|---|---|---|---|"]
    lines += [
        f"| {s['uai']} | {s['nom']} | {s['old']} | {s['new']} |"
        for s in diff["scores"][:15]
        if s["old"] != s["new"]
    ]

    ignored_m = f"{TOLERANCE_KM * 1000:.0f} m"
    lines += ["", f"## Changed values per column (distance changes under {ignored_m} ignored)", ""]
    for column, rows in sorted(diff["columns"].items(), key=lambda kv: -len(kv[1])):
        examples = "; ".join(f"{r['uai']}: {r['old']!r} → {r['new']!r}" for r in rows[:3])
        lines.append(f"- **{column}**: {len(rows)} schools ({examples})")
    if not diff["columns"]:
        lines.append("No changes.")

    sec = diff.get("secteurs")
    if sec is not None:
        lines += ["", "## Catchment sectors", ""]
        lines.append(f"- Added: {len(sec['added'])} {_capped(sec['added'])}")
        lines.append(f"- Removed: {len(sec['removed'])} {_capped(sec['removed'])}")
        changed = sec["changed"]
        lines.append(f"- Area changed by more than 1 % or renamed: {len(changed)} {_capped(changed)}")

    h = diff.get("history")
    if h is not None:
        lines += ["", "## School-year history", ""]
        lines.append(f"- Rows added: {len(h['added'])} {h['added'][:10] if h['added'] else ''}")
        lines.append(f"- Rows removed: {len(h['removed'])} {h['removed'][:10] if h['removed'] else ''}")
        lines += [f"- **{c}**: {n} rows changed" for c, n in sorted(h["changed"].items())]
        if not h["changed"]:
            lines.append("- No changed values in existing rows.")
    return "\n".join(lines) + "\n"


def render_distribution(risques: pd.DataFrame, started: datetime) -> str:
    """Levels and hazards per department, for the calibration review with risk experts:
    where each hazard applies, and how it moves the levels."""
    from app.services.scoring import HAZARD_NAMES, TERRITORIAL_HAZARDS

    profiles = _profiles(risques)
    rows: dict[str, list[dict[str, Any]]] = {}
    for _, school in risques.iterrows():
        rows.setdefault(_department_of(school), []).append(profiles[school["uai"]])

    def line(name: str, items: list[dict[str, Any]]) -> str:
        levels = [sum(1 for p in items if p["niveau"] == lvl) for lvl in ("faible", "moyen", "eleve")]
        mean = sum(p["score_global"] for p in items) / len(items)
        present = [sum(1 for p in items if h in p["aleas"]) for h in TERRITORIAL_HAZARDS]
        main = {h: sum(1 for p in items if p["alea_principal"] == h) for h in HAZARD_NAMES}
        top = max(main, key=main.get)
        return (f"| {name} | {len(items)} | {levels[0]} | {levels[1]} | {levels[2]} | {mean:.1f} | "
                + " | ".join(str(n) for n in present) + f" | {top} ({main[top]}) |")

    everything = [p for items in rows.values() for p in items]
    head = " | ".join(TERRITORIAL_HAZARDS)
    lines = [
        f"# Score distribution {started:%Y-%m-%d}", "",
        "Per department: schools, schools per level, mean global score, schools where each "
        "territorial hazard applies, and the most frequent main hazard. For the calibration "
        "review; the thresholds and weights are settings (Simulation page).", "",
        f"| Dept | Schools | Faible | Moyen | Élevé | Mean | {head} | Main hazard |",
        "|" + "---|" * (7 + len(TERRITORIAL_HAZARDS)),
        line("**All**", everything),
    ]
    lines += [line(dep, items) for dep, items in sorted(rows.items())]
    return "\n".join(lines) + "\n"


# ---- publish ------------------------------------------------------------------
def write_staging(result: BuildResult, staging: Path) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    result.risques.to_parquet(staging / settings.risques_file, index=False)
    result.history.to_parquet(staging / settings.colleges_file, index=False)
    if result.secteurs is not None:
        (staging / settings.secteurs_file).write_text(
            json.dumps(result.secteurs, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )


def publish(staging: Path, data_dir: Path, archive_dir: Path, stamp: str) -> Path:
    """Archive the current files, then swap in the staged ones (atomic per file)."""
    backup = archive_dir / stamp
    backup.mkdir(parents=True, exist_ok=False)
    names = [n for n in (settings.risques_file, settings.colleges_file, settings.secteurs_file)
             if (staging / n).exists()]
    for name in names:
        if (data_dir / name).exists():
            shutil.copy2(data_dir / name, backup / name)
    for name in names:
        tmp = data_dir / f".{name}.tmp"
        shutil.copyfile(staging / name, tmp)
        os.chmod(tmp, 0o444)  # the published files are read-only, like the originals
        os.replace(tmp, data_dir / name)
    (backup / "published.json").write_text(
        json.dumps({"published_at": datetime.now(UTC).isoformat(), "files": names}),
        encoding="utf-8",
    )
    return backup
