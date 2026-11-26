"""Risk scoring: turns the raw columns of colleges_risques into 0–100 hazard scores.

These thresholds are a starting heuristic for prioritising follow-up, NOT an official
classification. Tune them with the people who own the risk data (Sprint 2 review).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.config import settings

# Scored everywhere (the Loiret calibration).
CORE_HAZARDS = ("inondation", "argiles", "mouvements_terrain", "industriel", "tmd", "nucleaire")
# Hazards of part of the territory: they only count where present (score > 0), so that the
# zeros of a school in Beauce do not dilute its average with earthquakes and avalanches.
TERRITORIAL_HAZARDS = ("seisme", "radon", "feu_foret", "littoral", "avalanche")
HAZARD_NAMES = CORE_HAZARDS + TERRITORIAL_HAZARDS
POINT_SCALES = ("argiles_points", "seisme_points", "radon_points")


@dataclass(frozen=True)
class ScoringParams:
    """What the risk referent can tune: hazard weights, level thresholds, clay class points.
    The defaults come from the settings (SRT_WEIGHT_*, SRT_SEUIL_*, SRT_ARGILES_POINTS)."""

    weights: dict[str, float] = field(default_factory=dict)
    seuil_moyen: float = 30.0
    seuil_eleve: float = 50.0
    argiles_points: dict[str, float] = field(default_factory=dict)
    seisme_points: dict[str, float] = field(default_factory=dict)
    radon_points: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_settings(cls) -> ScoringParams:
        return cls(
            weights={h: getattr(settings, f"weight_{h}") for h in HAZARD_NAMES},
            seuil_moyen=settings.seuil_moyen,
            seuil_eleve=settings.seuil_eleve,
            **{scale: dict(getattr(settings, scale)) for scale in POINT_SCALES},
        )

    def as_env(self) -> dict[str, str]:
        """The settings that would apply these parameters (to copy into the environment)."""
        import json

        env = {f"SRT_WEIGHT_{h.upper()}": f"{w:g}" for h, w in self.weights.items()}
        env["SRT_SEUIL_MOYEN"] = f"{self.seuil_moyen:g}"
        env["SRT_SEUIL_ELEVE"] = f"{self.seuil_eleve:g}"
        # Compact JSON (no spaces): it can be pasted into .env or after `export` as is.
        for scale in POINT_SCALES:
            points = {k: round(v, 2) for k, v in getattr(self, scale).items()}
            env[f"SRT_{scale.upper()}"] = json.dumps(points, ensure_ascii=False, separators=(",", ":"))
        return env


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


def score_argiles(r: dict[str, Any], points: dict[str, float] | None = None) -> float:
    points = settings.argiles_points if points is None else points
    base = points.get(r.get("argiles_classe") or "", 0)
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
    meters = r.get("distance_tmd_m")
    if meters is None:  # unknown distance: no score (not "0 m away", which scored 100)
        return 0.0
    s = _decay(meters / 1000, 0.1, 1.0)
    if r.get("type_tmd_proche") in {"Autoroute", "Voies ferrées"}:
        s *= 1.2
    return _clamp(s)


def score_nucleaire(r: dict[str, Any]) -> float:
    # The PPI radius around French nuclear plants is 20 km.
    return _clamp(_decay(r.get("distance_centrale_km"), 5.0, 40.0))


def _class_points(value: Any, points: dict[str, float]) -> float:
    if value is None or value != value:  # None or NaN: not evaluated
        return 0.0
    return points.get(str(int(value)), 0.0)


def score_seisme(r: dict[str, Any], points: dict[str, float] | None = None) -> float:
    """Official earthquake zone of the commune (1 very low … 5 strong)."""
    return _clamp(_class_points(r.get("zone_sismique"), settings.seisme_points if points is None else points))


def score_radon(r: dict[str, Any], points: dict[str, float] | None = None) -> float:
    """Radon potential class of the commune (3: measurements are compulsory in schools)."""
    return _clamp(_class_points(r.get("classe_radon"), settings.radon_points if points is None else points))


def score_feu_foret(r: dict[str, Any]) -> float:
    """A wildfire prevention plan (PPRIF) on the commune, else its listing in the DDRM."""
    if r.get("ppr_feu_foret"):
        return 70.0
    return 40.0 if r.get("ddrm_feu_foret") else 0.0


def score_littoral(r: dict[str, Any]) -> float:
    """Marine flooding and coastline retreat plans, and CatNat decrees for waves."""
    s = 60.0 if r.get("ppr_submersion_marine") else 0.0
    s += 25.0 if r.get("ppr_recul_trait_cote") else 0.0
    s += min(25.0, 5.0 * (r.get("nb_catnat_littoral") or 0))
    return _clamp(s)


def score_avalanche(r: dict[str, Any]) -> float:
    """An avalanche prevention plan on the commune, else its listing in the DDRM."""
    s = 70.0 if r.get("ppr_avalanche") else 40.0 if r.get("ddrm_avalanche") else 0.0
    s += min(30.0, 10.0 * (r.get("nb_catnat_avalanche") or 0))
    return _clamp(s)


HAZARDS = {
    "inondation": (score_inondation, "weight_inondation"),
    "argiles": (score_argiles, "weight_argiles"),
    "mouvements_terrain": (score_mouvements_terrain, "weight_mouvements_terrain"),
    "industriel": (score_industriel, "weight_industriel"),
    "tmd": (score_tmd, "weight_tmd"),
    "nucleaire": (score_nucleaire, "weight_nucleaire"),
    "seisme": (score_seisme, "weight_seisme"),
    "radon": (score_radon, "weight_radon"),
    "feu_foret": (score_feu_foret, "weight_feu_foret"),
    "littoral": (score_littoral, "weight_littoral"),
    "avalanche": (score_avalanche, "weight_avalanche"),
}


def aggregate(aleas: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted mean of the core hazards. A territorial hazard present at the school joins
    the mean when its score is above it (highest first): adding a hazard never lowers a
    score, and a school with no territorial hazard (all of Loiret) keeps its calibrated one."""
    num = sum(v * weights.get(h, 0.0) for h, v in aleas.items() if h in CORE_HAZARDS)
    den = sum(weights.get(h, 0.0) for h in aleas if h in CORE_HAZARDS)
    mean = num / den if den else 0.0
    territorial = sorted(((v, h) for h, v in aleas.items() if h not in CORE_HAZARDS), reverse=True)
    for value, hazard in territorial:
        weight = weights.get(hazard, 0.0)
        if value <= mean or weight <= 0:
            continue
        num, den = num + value * weight, den + weight
        mean = num / den
    return round(mean, 1)


def niveau(score: float, params: ScoringParams | None = None) -> str:
    source = params or settings
    moyen, eleve = source.seuil_moyen, source.seuil_eleve
    if score >= eleve:
        return "eleve"
    if score >= moyen:
        return "moyen"
    return "faible"


# DICRIMs exist since the decree of 1990; earlier years in GASPAR are placeholders (1900).
DICRIM_FIRST_YEAR = 1990
EFFECTIF_INCONNU = "Effectif inconnu : indice de priorité non calculé"


def indice_priorite(score_global: float, effectif_total: int | None) -> float | None:
    """Pupils exposed, weighted by risk: score_global × effectif_total / 100.

    The score says how dangerous the location is; this index says how many pupils that
    danger concerns, so a large school with a medium score can outrank a small school
    with a high one. Unknown enrolment gives no index (never 0, which would hide the school).
    """
    if not effectif_total or effectif_total < 0:
        return None
    return round(score_global * effectif_total / 100, 1)


def compute_risk_profile(r: dict[str, Any], params: ScoringParams | None = None) -> dict[str, Any]:
    params = params or ScoringParams.from_settings()
    with_points = {"argiles": params.argiles_points, "seisme": params.seisme_points,
                   "radon": params.radon_points}
    scores = {
        name: round(fn(r, with_points[name]) if name in with_points else fn(r), 1)
        for name, (fn, _) in HAZARDS.items()
    }
    # Territorial hazards are shown and counted only where present.
    aleas = {h: v for h, v in scores.items() if h in CORE_HAZARDS or v > 0}
    global_score = aggregate(aleas, params.weights)

    alertes = []
    annee = r.get("annee_dicrim")
    if annee is None:
        alertes.append("DICRIM absent pour la commune")
    elif annee < DICRIM_FIRST_YEAR:  # GASPAR's placeholder date (1900): published, date unknown
        alertes.append("DICRIM : date de publication inconnue")
    elif date.today().year - annee > settings.dicrim_max_age_years:
        alertes.append(f"DICRIM ancien ({annee})")
    indice = indice_priorite(global_score, r.get("effectif_total"))
    if indice is None:
        alertes.append(EFFECTIF_INCONNU)

    return {
        "score_global": global_score,
        "niveau": niveau(global_score, params),
        "alea_principal": max(aleas, key=aleas.get),
        "aleas": aleas,
        "indice_priorite": indice,
        "alertes": alertes,
        "lacunes": data_gaps(r),
    }


# Parts of the evaluation a school's data cannot support (no source for its department):
# the score is then a lower bound for that hazard, and the interface says so.
GAPS = {
    "distance_tmd_m": "Transport de matières dangereuses non évalué : pas de données pour ce territoire",
    "distance_repere_crue_km": (
        "Repères de crue non pris en compte dans le score inondation : pas de données pour ce territoire"
    ),
    "zone_sismique": "Zone sismique de la commune inconnue : séisme non évalué",
    "classe_radon": "Potentiel radon de la commune inconnu : radon non évalué",
}


def data_gaps(r: dict[str, Any]) -> list[str]:
    return [text for column, text in GAPS.items() if column in r and r[column] is None]
