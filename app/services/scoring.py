"""Risk scoring: turns the raw columns of colleges_risques into 0–100 hazard scores.

These thresholds are a starting heuristic for prioritising follow-up, NOT an official
classification. Tune them with the people who own the risk data (Sprint 2 review).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.core.config import settings

ARGILES = {"faible": 15, "moyen": 45, "fort": 85, "non cartographié": 0}


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def _decay(distance: float | None, near: float, far: float) -> float:
    """100 at `near` or closer, 0 at `far` or beyond, linear in between."""
    if distance is None:
        return 0.0
    if distance <= near:
        return 100.0
    if distance >= far:
        return 0.0
    return 100.0 * (far - distance) / (far - near)


def score_inondation(r: dict[str, Any]) -> float:
    s = 60.0 if r.get("dans_ppri") else 0.0
    s += min(25.0, 5.0 * (r.get("nb_catnat_inondations") or 0))
    s += 0.15 * _decay(r.get("distance_repere_crue_km"), 0.5, 5.0)
    return _clamp(s)


def score_argiles(r: dict[str, Any]) -> float:
    base = ARGILES.get(r.get("argiles_classe") or "", 0)
    return _clamp(base + 2.0 * (r.get("nb_catnat_secheresse") or 0))


def score_mouvements_terrain(r: dict[str, Any]) -> float:
    cav = r.get("nb_cavites_500m") or 0
    mvt = r.get("nb_mouvements_terrain_500m") or 0
    return _clamp(min(50.0, 5.0 * cav) + min(50.0, 8.0 * mvt))


def score_industriel(r: dict[str, Any]) -> float:
    seveso = _decay(r.get("distance_seveso_km"), 1.0, 10.0)
    icpe = min(100.0, 6.0 * (r.get("nb_icpe_1km") or 0))
    return _clamp(0.65 * seveso + 0.35 * icpe)


def score_tmd(r: dict[str, Any]) -> float:
    dist_km = (r.get("distance_tmd_m") or 0) / 1000
    s = _decay(dist_km, 0.1, 1.0)
    if r.get("type_tmd_proche") in {"Autoroute", "Voies ferrées"}:
        s *= 1.2
    return _clamp(s)


def score_nucleaire(r: dict[str, Any]) -> float:
    # The PPI radius around French nuclear plants is 20 km.
    return _clamp(_decay(r.get("distance_centrale_km"), 5.0, 40.0))


HAZARDS = {
    "inondation": (score_inondation, "weight_inondation"),
    "argiles": (score_argiles, "weight_argiles"),
    "mouvements_terrain": (score_mouvements_terrain, "weight_mouvements_terrain"),
    "industriel": (score_industriel, "weight_industriel"),
    "tmd": (score_tmd, "weight_tmd"),
    "nucleaire": (score_nucleaire, "weight_nucleaire"),
}


def niveau(score: float) -> str:
    if score >= 50:
        return "eleve"
    if score >= 30:
        return "moyen"
    return "faible"


def compute_risk_profile(r: dict[str, Any]) -> dict[str, Any]:
    aleas = {name: round(fn(r), 1) for name, (fn, _) in HAZARDS.items()}
    weights = {name: getattr(settings, w) for name, (_, w) in HAZARDS.items()}
    total_w = sum(weights.values())
    global_score = round(sum(aleas[h] * weights[h] for h in aleas) / total_w, 1)

    alertes = []
    annee = r.get("annee_dicrim")
    if annee is None:
        alertes.append("DICRIM absent pour la commune")
    elif date.today().year - annee > settings.dicrim_max_age_years:
        alertes.append(f"DICRIM ancien ({annee})")

    return {
        "score_global": global_score,
        "niveau": niveau(global_score),
        "alea_principal": max(aleas, key=aleas.get),
        "aleas": aleas,
        "alertes": alertes,
    }
