"""Sprint 8: priority index = pupils exposed, weighted by risk."""
import pytest

from app.data.store import DataStore, get_store
from app.services.scoring import EFFECTIF_INCONNU, compute_risk_profile, indice_priorite

API = "/api/v1"


def test_index_formula_and_unknown_enrolment():
    assert indice_priorite(60.0, 800) == 480.0
    assert indice_priorite(70.0, 150) == 105.0  # higher score, far fewer pupils: lower priority
    for unknown in (None, 0, -5):
        assert indice_priorite(50.0, unknown) is None  # never 0, which would hide the school


def test_profile_flags_unknown_enrolment_without_touching_the_score():
    store = DataStore("files")  # no fixture here: never follow .env to a real database
    row = dict(store.get_college("0450003J"))
    known = compute_risk_profile(row)
    row["effectif_total"] = None
    unknown = compute_risk_profile(row)
    assert unknown["score_global"] == known["score_global"]
    assert unknown["indice_priorite"] is None and EFFECTIF_INCONNU in unknown["alertes"]
    assert known["indice_priorite"] == round(known["score_global"] * known_effectif(store) / 100, 1)


def known_effectif(store):
    return store.get_college("0450003J")["effectif_total"]


def test_list_sorted_by_priority(client):
    by_score = client.get(f"{API}/colleges").json()
    by_priority = client.get(f"{API}/colleges", params={"tri": "priorite"}).json()
    assert {c["uai"] for c in by_score} == {c["uai"] for c in by_priority}
    indices = [c["indice_priorite"] for c in by_priority]
    assert indices == sorted(indices, reverse=True)
    scores = [c["score_global"] for c in by_score]
    assert scores == sorted(scores, reverse=True)
    for c in by_priority:
        assert c["indice_priorite"] == pytest.approx(c["score_global"] * c["effectif_total"] / 100, abs=0.06)
    assert client.get(f"{API}/colleges", params={"tri": "nimporte"}).status_code == 422


def test_schools_without_enrolment_come_last(client, monkeypatch):
    store = get_store()
    uai = client.get(f"{API}/colleges", params={"tri": "priorite"}).json()[0]["uai"]
    monkeypatch.setitem(store._profiles[uai], "indice_priorite", None)
    store._cache.clear()  # the store caches its lists (its data never changes in production)
    rows = client.get(f"{API}/colleges", params={"tri": "priorite"}).json()
    assert rows[-1]["uai"] == uai and rows[-1]["indice_priorite"] is None


def test_stats_count_pupils_per_level_and_keep_the_dicrim_count(client):
    stats = client.get(f"{API}/stats").json()
    assert sum(stats["eleves_par_niveau"].values()) == stats["nb_eleves"]
    assert set(stats["eleves_par_niveau"]) == set(stats["par_niveau"])
    assert stats["sans_effectif"] == 0
    profiles = get_store().all_profiles().values()
    assert stats["dicrim_absent_ou_ancien"] == sum(
        1 for p in profiles if any(a.startswith(("DICRIM absent", "DICRIM ancien")) for a in p["alertes"])
    )


def test_profile_endpoint_returns_the_index(client):
    profile = client.get(f"{API}/colleges/0450003J/risques").json()
    assert profile["indice_priorite"] == get_store().get_profile("0450003J")["indice_priorite"]
