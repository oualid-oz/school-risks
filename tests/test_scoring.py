"""Unit tests for each hazard rule (Sprint 2 follow-up). The numbers here ARE the rules:
changing a threshold in scoring.py must come with a deliberate change here."""
from datetime import date

import pytest

from app.core.config import settings
from app.services import scoring as s

APPROX = pytest.approx


def test_decay_is_linear_between_near_and_far():
    assert s._decay(None, 1, 10) == 0
    assert s._decay(0.5, 1, 10) == 100 and s._decay(1, 1, 10) == 100
    assert s._decay(5.5, 1, 10) == APPROX(50)
    assert s._decay(10, 1, 10) == 0 and s._decay(30, 1, 10) == 0


@pytest.mark.parametrize(("row", "expected"), [
    ({}, 0),
    ({"dans_ppri": True}, 60),
    ({"nb_catnat_inondations": 2}, 10),
    ({"nb_catnat_inondations": 9}, 25),  # CatNat part capped at 25
    ({"distance_repere_crue_km": 0.2}, 15),  # 15 % of a full decay
    ({"distance_repere_crue_km": 2.75}, 7.5),
    ({"dans_ppri": True, "nb_catnat_inondations": 10, "distance_repere_crue_km": 0}, 100),
])
def test_inondation(row, expected):
    assert s.score_inondation(row) == APPROX(expected)


@pytest.mark.parametrize(("row", "expected"), [
    ({"argiles_classe": "faible"}, 15),
    ({"argiles_classe": "moyen"}, 45),
    ({"argiles_classe": "fort"}, 85),
    ({"argiles_classe": "non cartographié"}, 0),
    ({"argiles_classe": None}, 0),
    ({"argiles_classe": "moyen", "nb_catnat_secheresse": 4}, 53),
    ({"argiles_classe": "fort", "nb_catnat_secheresse": 20}, 100),  # clamped
])
def test_argiles(row, expected):
    assert s.score_argiles(row) == APPROX(expected)


@pytest.mark.parametrize(("row", "expected"), [
    ({}, 0),
    ({"nb_cavites_500m": 3}, 15),
    ({"nb_cavites_500m": 33}, 50),  # each part capped at 50
    ({"nb_mouvements_terrain_500m": 2}, 16),
    ({"nb_cavites_500m": 40, "nb_mouvements_terrain_500m": 9}, 100),
])
def test_mouvements_terrain(row, expected):
    assert s.score_mouvements_terrain(row) == APPROX(expected)


@pytest.mark.parametrize(("row", "expected"), [
    ({}, 0),
    ({"distance_seveso_km": 0.5}, 65),
    ({"distance_seveso_km": 5.5}, 32.5),
    ({"nb_icpe_1km": 5}, 10.5),  # 35 % of 6 points per site
    ({"nb_icpe_1km": 30}, 35),  # site part capped
    ({"distance_seveso_km": 1.0, "nb_icpe_1km": 17}, 100),
])
def test_industriel(row, expected):
    assert s.score_industriel(row) == APPROX(expected)


@pytest.mark.parametrize(("row", "expected"), [
    ({"distance_tmd_m": 50, "type_tmd_proche": "Routes"}, 100),
    ({"distance_tmd_m": 550, "type_tmd_proche": "Routes"}, 50),
    ({"distance_tmd_m": 550, "type_tmd_proche": "Autoroute"}, 60),  # × 1.2
    ({"distance_tmd_m": 550, "type_tmd_proche": "Voies ferrées"}, 60),
    ({"distance_tmd_m": 50, "type_tmd_proche": "Autoroute"}, 100),  # clamped
    ({"distance_tmd_m": 1500, "type_tmd_proche": "Autoroute"}, 0),
    ({"distance_tmd_m": None}, 0),  # regression: unknown distance used to score 100
    ({}, 0),
])
def test_tmd(row, expected):
    assert s.score_tmd(row) == APPROX(expected)


@pytest.mark.parametrize(("km", "expected"), [(None, 0), (3, 100), (5, 100), (22.5, 50), (40, 0), (60, 0)])
def test_nucleaire(km, expected):
    assert s.score_nucleaire({"distance_centrale_km": km}) == APPROX(expected)


@pytest.mark.parametrize(("score", "level"), [(0, "faible"), (29.9, "faible"), (30, "moyen"),
                                               (49.9, "moyen"), (50, "eleve"), (100, "eleve")])
def test_levels(score, level):
    assert s.niveau(score) == level


def test_global_score_is_the_weighted_mean_and_flags_dicrim(monkeypatch):
    row = {"dans_ppri": True, "argiles_classe": "fort", "distance_tmd_m": 5000,
           "annee_dicrim": date.today().year - 2, "effectif_total": 400}
    profile = s.compute_risk_profile(row)
    weights = {h: getattr(settings, w) for h, (_, w) in s.HAZARDS.items() if h in s.CORE_HAZARDS}
    expected = sum(profile["aleas"][h] * weights[h] for h in weights) / sum(weights.values())
    assert profile["score_global"] == APPROX(expected, abs=0.05)
    assert profile["alea_principal"] == "argiles" and profile["alertes"] == []

    monkeypatch.setattr(settings, "weight_argiles", 0.0)  # weights are normalised
    without_clay = 60 * 1.5 / (sum(weights.values()) - 1.0)  # only the flood score is left
    assert s.compute_risk_profile(row)["score_global"] == APPROX(without_clay, abs=0.05)

    no_dicrim = s.compute_risk_profile({**row, "annee_dicrim": None})
    assert no_dicrim["alertes"][0] == "DICRIM absent pour la commune"
    old = date.today().year - settings.dicrim_max_age_years - 1
    assert s.compute_risk_profile({**row, "annee_dicrim": old})["alertes"] == [f"DICRIM ancien ({old})"]


def test_dicrim_placeholder_year_is_an_unknown_date_not_an_old_dicrim():
    # GASPAR gives 1900 when a commune's DICRIM has no publication date.
    alerts = s.compute_risk_profile({"annee_dicrim": 1900, "effectif_total": 100})["alertes"]
    assert alerts == ["DICRIM : date de publication inconnue"]
