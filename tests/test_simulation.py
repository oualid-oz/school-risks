"""Sprint 14: score parameters and the what-if simulation."""
import pytest

from app.core.config import settings
from app.data.store import get_store
from app.services.scoring import HAZARD_NAMES

API = "/api/v1"


def test_current_parameters(client):
    params = client.get(f"{API}/scores/parametres").json()
    assert params["seuil_moyen"] == 30 and params["seuil_eleve"] == 50
    assert params["poids"]["inondation"] == 1.5 and params["argiles_points"]["fort"] == 85


def test_same_parameters_change_nothing(client):
    result = client.post(f"{API}/scores/simulation", json={}).json()
    summary = result["resume"]
    assert summary["changements_de_niveau"] == 0 and summary["ecart_moyen"] == 0
    assert summary["par_niveau_actuel"] == summary["par_niveau_simule"]
    assert summary["entrent_dans_le_top"] == summary["sortent_du_top"] == []
    assert len(result["colleges"]) == 70
    row = next(r for r in result["colleges"] if r["uai"] == "0450003J")
    assert row["score_actuel"] == get_store().get_profile("0450003J")["score_global"]
    assert all(r["rang_actuel"] == r["rang_simule"] for r in result["colleges"])


def test_lower_clay_points_move_schools_down(client):
    result = client.post(f"{API}/scores/simulation", json={"argiles_points": {"fort": 60}}).json()
    rows = {r["uai"]: r for r in result["colleges"]}
    fort = [u for u in rows if get_store().get_college(u)["argiles_classe"] == "fort"]
    assert fort and all(rows[u]["score_simule"] < rows[u]["score_actuel"] for u in fort)
    assert result["resume"]["ecart_moyen"] < 0
    assert result["variables"]["SRT_ARGILES_POINTS"] == (
        '{"faible":15.0,"moyen":45.0,"fort":60.0,"non cartographié":0.0}'
    )


def test_thresholds_and_weights(client):
    result = client.post(f"{API}/scores/simulation", json={
        "seuil_moyen": 20, "seuil_eleve": 40, "poids": {"nucleaire": 0}, "tri": "priorite",
    }).json()
    summary = result["resume"]
    assert summary["par_niveau_simule"]["eleve"] > summary["par_niveau_actuel"]["eleve"]
    assert summary["changements_de_niveau"] > 0
    env = result["variables"]
    assert env["SRT_SEUIL_ELEVE"] == "40" and env["SRT_WEIGHT_NUCLEAIRE"] == "0"
    ranks = [r["rang_simule"] for r in result["colleges"]]
    assert ranks == sorted(ranks) and ranks[0] == 1
    assert result["colleges"][0]["indice_simule"] == max(r["indice_simule"] for r in result["colleges"])
    for uai in summary["entrent_dans_le_top"]:
        assert next(r for r in result["colleges"] if r["uai"] == uai)["rang_simule"] <= 10


@pytest.mark.parametrize("body", [
    {"seuil_moyen": 60, "seuil_eleve": 40},
    {"poids": dict.fromkeys(HAZARD_NAMES, 0)},
    {"poids": {"volcan": 1}},
    {"argiles_points": {"fort": 150}},
    {"tri": "nom"},
])
def test_invalid_parameters(client, body):
    assert client.post(f"{API}/scores/simulation", json=body).status_code == 422


def test_settings_change_the_real_levels(monkeypatch):
    from app.services.scoring import compute_risk_profile, niveau

    monkeypatch.setattr(settings, "seuil_eleve", 40.0)
    assert niveau(45) == "eleve"
    row = {"argiles_classe": "fort"}
    monkeypatch.setattr(settings, "argiles_points", {"fort": 50.0})
    assert compute_risk_profile(row)["aleas"]["argiles"] == 50


def test_invalid_threshold_settings_are_refused():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="SEUIL"):
        Settings(_env_file=None, seuil_moyen=60, seuil_eleve=50)
