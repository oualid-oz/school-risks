"""Data quality per department (Sprint 22): what each source covers, which schools miss
data, where the sectors come from. For the people who decide to open a department."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.data.store import DataStore


def _known(column: str) -> Callable[[dict[str, Any]], bool]:
    return lambda r: r.get(column) is not None and r.get(column) == r.get(column)  # not None/NaN


def _public(r: dict[str, Any]) -> bool:
    return str(r.get("statut") or "").lower().startswith("pub")


# key, label, applies to the school?, satisfied? (A commune without a DICRIM is not a data
# gap: the source is complete, the commune has not published one; schools show it as an alert.)
CHECKS: list[tuple[str, str, Callable[[dict], bool], Callable[[dict], bool]]] = [
    ("effectif", "Effectif", lambda r: True, _known("effectif_total")),
    ("ips", "Indice de position sociale (IPS)", lambda r: True, _known("ips")),
    ("argiles", "Carte des argiles", lambda r: True,
     lambda r: r.get("argiles_classe") not in (None, "non cartographié")),
    ("tmd", "Transport de matières dangereuses", lambda r: True, _known("distance_tmd_m")),
    ("reperes_crue", "Repères de crue", lambda r: True, _known("distance_repere_crue_km")),
    ("sismicite", "Zone sismique", lambda r: True, _known("zone_sismique")),
    ("radon", "Potentiel radon", lambda r: True, _known("classe_radon")),
    ("secteur", "Secteur de recrutement (collèges publics)", _public, lambda r: bool(r.get("secteur_id"))),
]
LABELS = {key: label for key, label, _, _ in CHECKS}


def missing(r: dict[str, Any]) -> list[str]:
    return [key for key, _, applies, ok in CHECKS if applies(r) and not ok(r)]


def by_department(
    store: DataStore, area: frozenset[str] | None, open_reports: dict[str, int]
) -> list[dict[str, Any]]:
    """One entry per department of the area: coverage of each check, sectors' origin."""
    rows: dict[str, list[dict[str, Any]]] = {}
    for r in store.rows(area):
        rows.setdefault(r.get("departement"), []).append(r)
    territories = {t["code"]: t for t in store.territories(area)}
    out = []
    for code in sorted(rows, key=lambda c: c or ""):
        schools = rows[code]
        coverage = {}
        for key, label, applies, ok in CHECKS:
            concerned = [r for r in schools if applies(r)]
            good = sum(1 for r in concerned if ok(r))
            coverage[key] = {
                "libelle": label, "renseignes": good, "total": len(concerned),
                "taux": round(100 * good / len(concerned)) if concerned else None,
            }
        t = territories.get(code, {})
        out.append({
            "code": code, "nom": t.get("nom"), "academie": t.get("academie"),
            "colleges": len(schools),
            "evaluation_incomplete": t.get("lacunes", 0),
            "couverture": coverage,
            "secteurs": t.get("secteurs", {"total": 0, "par_source": {}}),
            "signalements_ouverts": open_reports.get(code, 0),
        })
    return out


def schools_with_gaps(store: DataStore, area: frozenset[str] | None, departement: str | None) -> list[dict]:
    """Schools missing at least one item, the most incomplete first."""
    out = []
    for r in store.rows(area):
        if departement and r.get("departement") != departement:
            continue
        gaps = missing(r)
        if gaps:
            out.append({"uai": r["uai"], "nom": r["nom"], "commune": r["commune"],
                        "departement": r.get("departement"),
                        "manquants": [LABELS[k] for k in gaps]})
    return sorted(out, key=lambda s: (-len(s["manquants"]), s["departement"] or "", s["nom"]))
