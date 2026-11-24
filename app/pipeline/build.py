"""Assemble, validate, compare and publish the reference data files."""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.config import settings
from app.data.schema import HISTORY_SCHEMA, RISQUES_SCHEMA
from app.pipeline import education, georisques, sectors
from app.pipeline.client import Fetcher
from app.pipeline.geo import haversine_km, nearest_centrale
from app.services.scoring import compute_risk_profile

logger = logging.getLogger(__name__)

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
    "uai", "nom", "commune", "code_insee", "statut", "lat", "lon", "argiles_classe",
    "nb_cavites_500m", "nb_mouvements_terrain_500m", "nb_icpe_1km", "distance_centrale_km",
    "nb_catnat", "distance_tmd_m", "distance_repere_crue_km",
]
RANGES = {
    "lat": (46.3, 48.6),
    "lon": (1.2, 3.3),
    "distance_centrale_km": (0, 150),
    "distance_icpe_km": (0, 50),
    "distance_seveso_km": (0, 100),
    "ips": (50, 170),
}


@dataclass
class BuildResult:
    risques: pd.DataFrame
    history: pd.DataFrame
    notes: list[str] = field(default_factory=list)
    secteurs: dict[str, Any] | None = None  # None: keep the published sectors file


def _typed(rows: list[dict[str, Any]], schema: dict[str, str]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for column, dtype in schema.items():
        df[column] = df[column].astype(dtype)  # "str" keeps missing values missing
    return df[list(schema)]


def build(
    fetcher: Fetcher,
    previous_risques: pd.DataFrame,
    first_year: int = 2016,
    workers: int = 4,
    keep: list[str] | None = None,
) -> BuildResult:
    """Rebuild both tables for the schools of the published file (the roster).

    ``keep`` lists extra risk columns to copy from the published file instead of
    refreshing them (for example while a source change is being reviewed).
    """
    keep = list(keep or [])
    unknown = [c for c in keep if c not in RISQUES_SCHEMA or c == "uai"]
    if unknown:
        raise ValueError(f"Unknown columns for --keep: {unknown}")
    notes: list[str] = []
    roster = previous_risques.set_index("uai")
    uais = sorted(roster.index)

    logger.info("Downloading data.education.gouv.fr datasets")
    edu = education.fetch_all(fetcher, uais)
    annuaire = education.annuaire_by_uai(edu["annuaire"])

    missing = [u for u in uais if u not in annuaire]
    if missing:
        raise ValueError(f"Schools missing from the education directory: {missing}")
    closed = [u for u in uais if annuaire[u]["etat"] != "OUVERT"]
    if closed:
        notes.append(f"Schools no longer open in the directory: {closed}")
    new = sorted(
        u for u, a in annuaire.items()
        if u not in roster.index
        and a["etat"] == "OUVERT"
        and a["libelle_nature"] == "COLLEGE"
        and not (a["nom_etablissement"] or "").lower().startswith("ecole")
    )
    if new:
        notes.append(
            f"Open collèges in the directory that are not tracked yet: {new}. "
            "Add them to the roster by hand (they also need a sector and TMD data)."
        )

    # Identity and position come from the roster, so distances stay comparable from one
    # refresh to the next. Positions that drifted in the directory are reported instead.
    schools, moved = [], []
    for uai in uais:
        prev, a = roster.loc[uai], annuaire[uai]
        if a["lat"] is not None and a["lon"] is not None:
            shift = haversine_km(prev["lat"], prev["lon"], a["lat"], a["lon"])
            if shift > COORDINATE_DRIFT_KM:
                moved.append(f"{uai} ({shift:.2f} km)")
        schools.append(
            {
                "uai": uai,
                "nom": prev["nom"],
                "commune": prev["commune"],
                "code_insee": prev["code_insee"],
                "statut": a["statut"],
                "lat": float(prev["lat"]),
                "lon": float(prev["lon"]),
                "secteur_id": prev["secteur_id"],
            }
        )
    if moved:
        notes.append(
            f"Directory position differs by more than {COORDINATE_DRIFT_KM * 1000:.0f} m "
            f"(kept the roster position; check and fix by hand if needed): {', '.join(moved)}"
        )

    history = _typed(education.build_history(schools, edu, first_year), HISTORY_SCHEMA)
    latest = history.sort_values("rentree").groupby("uai").last()

    logger.info("Downloading Géorisques data")
    geo = georisques.fetch_all(fetcher, schools, workers)

    rows = []
    for s in schools:
        uai = s["uai"]
        prev = roster.loc[uai]
        point = geo["schools"][uai]
        commune = geo["communes"][s["code_insee"]]
        plan = georisques.ppri(point["ppr"])
        centrale, centrale_km = nearest_centrale(s["lat"], s["lon"])
        rows.append(
            {
                **s,
                "effectif_total": latest.loc[uai, "effectif_total"],
                "ips": latest.loc[uai, "ips"],
                "argiles_classe": georisques.argiles_classe(point["rga"]),
                "dans_ppri": plan is not None,
                "ppri": plan or "",
                "nb_cavites_500m": len(point["cavites"]),
                "nb_mouvements_terrain_500m": len(point["mvt"]),
                **georisques.icpe_metrics(s["lat"], s["lon"], point["icpe"]),
                "distance_centrale_km": round(centrale_km, 1),
                "centrale_proche": centrale,
                **georisques.catnat_counts(commune["catnat"]),
                "annee_dicrim": georisques.annee_dicrim(commune["dicrim"]),
                **{c: prev[c] for c in CARRIED_OVER + keep},
            }
        )
    notes.append(f"Carried over from the previous file (no public source): {CARRIED_OVER}")

    logger.info("Downloading the catchment sectors (Département du Loiret)")
    secteurs = sectors.fetch(fetcher)
    notes.append(f"Sectors refreshed from the Département du Loiret ({len(secteurs['features'])} sectors).")
    if keep:
        notes.append(f"Kept from the previous file on request (--keep): {keep}")
    return BuildResult(_typed(rows, RISQUES_SCHEMA), history, notes, secteurs=secteurs)


# ---- validation ---------------------------------------------------------------
def validate(result: BuildResult, previous_risques: pd.DataFrame) -> list[str]:
    """Blocking problems. An empty list means the files can be published."""
    errors = []
    r, h = result.risques, result.history
    for name, df, schema in (("risques", r, RISQUES_SCHEMA), ("history", h, HISTORY_SCHEMA)):
        if list(df.columns) != list(schema):
            errors.append(f"{name}: columns differ from the schema")
    if set(r["uai"]) != set(previous_risques["uai"]):
        errors.append("risques: the list of schools changed")
    if set(h["uai"]) != set(r["uai"]):
        errors.append("history: schools differ from the risk table")
    if r["uai"].duplicated().any():
        errors.append("risques: duplicate UAI")
    if h.duplicated(["uai", "rentree"]).any():
        errors.append("history: duplicate (uai, rentree)")
    for column in REQUIRED:
        if r[column].isna().any():
            errors.append(f"risques: empty values in {column}: {r.loc[r[column].isna(), 'uai'].tolist()}")
    for column, (low, high) in RANGES.items():
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
    """What changed per column, and how scores and levels move."""
    new_i, old_i = new.set_index("uai"), old.set_index("uai").reindex(new["uai"])
    changes: dict[str, list[dict[str, Any]]] = {}
    for column in new_i.columns:
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
    return {"columns": changes, "scores": scores, "history": history}


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
        f"- Validation: {'**FAILED**' if errors else 'passed'}",
    ]
    lines += [f"  - {e}" for e in errors]
    lines += ["", "## Notes", ""] + [f"- {n}" for n in result.notes]

    moved = [s for s in diff["scores"] if s["old_niveau"] != s["new_niveau"]]
    lines += ["", f"## Level changes ({len(moved)})", ""]
    lines += [
        f"- {s['uai']} {s['nom']}: {s['old_niveau']} → {s['new_niveau']} ({s['old']} → {s['new']})"
        for s in moved
    ] or ["None."]

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
        lines.append(f"- Added: {len(sec['added'])} {sec['added'] or ''}")
        lines.append(f"- Removed: {len(sec['removed'])} {sec['removed'] or ''}")
        changed = sec["changed"]
        lines.append(f"- Area changed by more than 1 % or renamed: {len(changed)} {changed or ''}")

    h = diff.get("history")
    if h is not None:
        lines += ["", "## School-year history", ""]
        lines.append(f"- Rows added: {len(h['added'])} {h['added'][:10] if h['added'] else ''}")
        lines.append(f"- Rows removed: {len(h['removed'])} {h['removed'][:10] if h['removed'] else ''}")
        lines += [f"- **{c}**: {n} rows changed" for c, n in sorted(h["changed"].items())]
        if not h["changed"]:
            lines.append("- No changed values in existing rows.")
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
