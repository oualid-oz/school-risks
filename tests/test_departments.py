"""Sprint 17: departments as a dimension."""
import pytest

from app.core.config import parse_departements
from app.data.store import department_of_insee
from app.pipeline import education
from app.pipeline.build import in_france
from app.services.scoring import GAPS, compute_risk_profile

API = "/api/v1"


def directory_row(**extra):
    return {"identifiant_de_l_etablissement": "0450003J", "etat": "OUVERT", "libelle_nature": "COLLEGE",
            "statut_public_prive": "Public", "type_contrat_prive": "SANS OBJET", **extra}


@pytest.mark.parametrize(("row", "tracked"), [
    (directory_row(), True),
    (directory_row(statut_public_prive="Privé", type_contrat_prive="CONTRAT D'ASSOCIATION"), True),
    (directory_row(statut_public_prive="Privé", type_contrat_prive="HORS CONTRAT"), False),
    (directory_row(libelle_nature="SECTION ENSEIGNT GEN. ET PROF. ADAPTE"), False),  # SEGPA
    (directory_row(etat="FERME"), False),
])
def test_which_directory_rows_are_tracked(row, tracked):
    assert education.is_tracked_college(row) is tracked


def test_codes_and_names():
    assert parse_departements("045, 17;2a , 971") == ["45", "17", "2A", "971"]
    assert [education.directory_code(d) for d in ("45", "2A", "971")] == ["045", "02A", "971"]
    assert [education.department_code(d) for d in ("045", "02A", "971")] == ["45", "2A", "971"]
    assert [department_of_insee(c) for c in ("45234", "2A004", "97411")] == ["45", "2A", "974"]
    assert education.short_name("Collège privé Charles de Foucauld") == "Charles de Foucauld"
    assert education.short_name("Collège Jean Moulin") == "Jean Moulin"
    assert education.short_name("Maîtrise Notre-Dame") == "Maîtrise Notre-Dame"


@pytest.mark.parametrize(("lat", "lon", "inside"), [
    (47.9, 1.9, True), (41.9, 8.7, True),  # Orléans, Ajaccio
    (16.2, -61.5, True), (4.9, -52.3, True), (-21.1, 55.5, True), (-12.8, 45.2, True),  # overseas
    (10.0, 1.9, False), (52.5, 13.4, False),
])
def test_positions_must_be_in_a_french_territory(lat, lon, inside):
    assert in_france(lat, lon) is inside


def test_missing_tmd_and_flood_markers_are_gaps_not_scores():
    row = {"distance_tmd_m": None, "distance_repere_crue_km": None, "argiles_classe": "moyen"}
    profile = compute_risk_profile(row)
    assert profile["aleas"]["tmd"] == 0 and profile["lacunes"] == list(GAPS.values())
    assert compute_risk_profile({"distance_tmd_m": 5000, "distance_repere_crue_km": 30.0})["lacunes"] == []


def test_api_filters_by_department_and_pages(client):
    everything = client.get(f"{API}/colleges").json()
    loiret = client.get(f"{API}/colleges", params={"departement": "45"})
    assert len(loiret.json()) == len(everything) == 70
    assert all(c["departement"] == "45" and c["departement_nom"] == "Loiret" for c in loiret.json())
    assert client.get(f"{API}/colleges", params={"departement": "17"}).json() == []

    page = client.get(f"{API}/colleges", params={"limit": 20, "offset": 60})
    assert page.headers["x-total-count"] == "70" and len(page.json()) == 10
    assert [c["uai"] for c in page.json()] == [c["uai"] for c in everything[60:]]

    assert client.get(f"{API}/territoires").json() == [
        {"code": "45", "nom": "Loiret", "academie": "Orléans-Tours", "colleges": 70, "lacunes": 0},
    ]
    assert client.get(f"{API}/stats", params={"departement": "45"}).json()["nb_colleges"] == 70
    assert client.get(f"{API}/stats", params={"departement": "17"}).json()["nb_colleges"] == 0


def test_school_detail_carries_its_territory(client):
    college = client.get(f"{API}/colleges/0450003J").json()
    assert (college["departement"], college["academie"]) == ("45", "Orléans-Tours")
    assert college["profil_risque"]["lacunes"] == []
