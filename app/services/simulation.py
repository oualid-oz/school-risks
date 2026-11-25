"""What-if: recompute every school's score with other parameters, compared with today's.

Nothing is stored: it is a tool for the review of the thresholds and weights with the
risk referent. Applying a result means setting the SRT_* values it lists and restarting.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.data.store import DataStore
from app.services.scoring import POINT_SCALES, ScoringParams, compute_risk_profile, indice_priorite

LEVELS = ("faible", "moyen", "eleve")


def _ranks(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    ordered = sorted(rows, key=lambda r: (-(r[key] if r[key] is not None else -1), r["uai"]))
    return {r["uai"]: i + 1 for i, r in enumerate(ordered)}


def simulate(
    store: DataStore, params: ScoringParams, tri: str = "score", top: int = 10,
    uais: list[str] | None = None,
) -> dict[str, Any]:
    """`uais`: the schools to recompute (the user's area); None: all."""
    current, simulated = ScoringParams.from_settings(), params
    rows = []
    for uai in store.uais() if uais is None else uais:
        college = store.get_college(uai)
        before = compute_risk_profile(college, current)
        after = compute_risk_profile(college, simulated)
        rows.append({
            "uai": uai,
            "nom": college["nom"],
            "commune": college["commune"],
            "effectif_total": college["effectif_total"],
            "score_actuel": before["score_global"],
            "score_simule": after["score_global"],
            "niveau_actuel": before["niveau"],
            "niveau_simule": after["niveau"],
            "alea_principal_simule": after["alea_principal"],
            "indice_actuel": indice_priorite(before["score_global"], college["effectif_total"]),
            "indice_simule": indice_priorite(after["score_global"], college["effectif_total"]),
        })
    prefix = "indice" if tri == "priorite" else "score"
    key_before, key_after = f"{prefix}_actuel", f"{prefix}_simule"
    rank_before, rank_after = _ranks(rows, key_before), _ranks(rows, key_after)
    for row in rows:
        row["rang_actuel"], row["rang_simule"] = rank_before[row["uai"]], rank_after[row["uai"]]
        row["ecart"] = round(row["score_simule"] - row["score_actuel"], 1)

    top_before = {u for u, r in rank_before.items() if r <= top}
    top_after = {u for u, r in rank_after.items() if r <= top}
    count_before = Counter(r["niveau_actuel"] for r in rows)
    count_after = Counter(r["niveau_simule"] for r in rows)
    return {
        "tri": tri,
        "parametres": {
            "poids": simulated.weights, "seuil_moyen": simulated.seuil_moyen,
            "seuil_eleve": simulated.seuil_eleve,
            **{scale: getattr(simulated, scale) for scale in POINT_SCALES},
        },
        "variables": simulated.as_env(),
        "resume": {
            "changements_de_niveau": sum(r["niveau_actuel"] != r["niveau_simule"] for r in rows),
            "par_niveau_actuel": {lvl: count_before.get(lvl, 0) for lvl in LEVELS},
            "par_niveau_simule": {lvl: count_after.get(lvl, 0) for lvl in LEVELS},
            "entrent_dans_le_top": sorted(top_after - top_before, key=rank_after.get),
            "sortent_du_top": sorted(top_before - top_after, key=rank_before.get),
            "ecart_moyen": round(sum(r["ecart"] for r in rows) / len(rows), 1) if rows else 0.0,
        },
        "colleges": sorted(rows, key=lambda r: r["rang_simule"]),
    }
