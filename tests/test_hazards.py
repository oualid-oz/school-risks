"""Sprint 19: hazards of part of the territory (earthquake, radon, wildfire, coast, avalanche)."""
import io
import zipfile
from datetime import UTC, datetime

import pandas as pd
import pytest

from app.core.config import settings
from app.pipeline import hazards
from app.pipeline.build import _seismic_radon, render_distribution, rolling_due
from app.services import scoring as s
from app.services.exports import colleges_csv

API = "/api/v1"
FLAT = {"dans_ppri": False, "argiles_classe": "faible", "distance_tmd_m": 5000}  # a quiet school


# ---- rules ----------------------------------------------------------------------------
@pytest.mark.parametrize(("zone", "expected"), [(None, 0), (1, 0), (2, 25), (3, 50), (4, 75), (5, 100)])
def test_seisme_follows_the_official_zone(zone, expected):
    assert s.score_seisme({"zone_sismique": zone}) == expected


@pytest.mark.parametrize(("classe", "expected"), [(None, 0), (float("nan"), 0), (1, 0), (2, 20), (3, 60)])
def test_radon_follows_the_potential_class(classe, expected):
    assert s.score_radon({"classe_radon": classe}) == expected


@pytest.mark.parametrize(("row", "expected"), [
    ({}, 0),
    ({"ddrm_feu_foret": True}, 40),
    ({"ddrm_feu_foret": True, "ppr_feu_foret": True}, 70),  # a plan outranks the listing
])
def test_feu_foret(row, expected):
    assert s.score_feu_foret(row) == expected


@pytest.mark.parametrize(("row", "expected"), [
    ({}, 0),
    ({"nb_catnat_littoral": 2}, 10),
    ({"ppr_submersion_marine": True}, 60),
    ({"ppr_submersion_marine": True, "ppr_recul_trait_cote": True, "nb_catnat_littoral": 9}, 100),
])
def test_littoral(row, expected):
    assert s.score_littoral(row) == expected


@pytest.mark.parametrize(("row", "expected"), [
    ({}, 0),
    ({"ddrm_avalanche": True}, 40),
    ({"ppr_avalanche": True, "nb_catnat_avalanche": 2}, 90),
    ({"ppr_avalanche": True, "nb_catnat_avalanche": 5}, 100),
])
def test_avalanche(row, expected):
    assert s.score_avalanche(row) == expected


# ---- aggregation ------------------------------------------------------------------------
def test_territorial_hazards_only_count_where_present_and_never_lower_a_score():
    quiet = s.compute_risk_profile(FLAT)
    assert set(quiet["aleas"]) == set(s.CORE_HAZARDS)  # nothing shown where nothing applies

    # A school whose core mean is low: a strong earthquake zone raises it…
    shaken = s.compute_risk_profile({**FLAT, "zone_sismique": 4})
    assert shaken["aleas"]["seisme"] == 75 and shaken["score_global"] > quiet["score_global"]
    assert shaken["alea_principal"] == "seisme"

    # …and a hazard scoring below the mean never pulls it down.
    high = {**FLAT, "dans_ppri": True, "nb_catnat_inondations": 5, "argiles_classe": "fort",
            "distance_seveso_km": 0.5, "nb_icpe_1km": 10}
    base = s.compute_risk_profile(high)["score_global"]
    with_radon = s.compute_risk_profile({**high, "classe_radon": 2})  # radon 20 < base
    assert base > 20 and with_radon["score_global"] == base and "radon" in with_radon["aleas"]


def test_aggregate_adds_the_highest_territorial_hazards_first():
    weights = dict.fromkeys(s.HAZARD_NAMES, 1.0)
    core = dict.fromkeys(s.CORE_HAZARDS, 40.0)
    assert s.aggregate(core, weights) == 40
    assert s.aggregate({**core, "seisme": 75}, weights) == 45  # (6×40 + 75) / 7
    # radon 20 would lower the mean: it is left out, whatever the order of the dict.
    assert s.aggregate({**core, "radon": 20, "seisme": 75}, weights) == 45
    assert s.aggregate({**core, "seisme": 75}, {**weights, "seisme": 0}) == 40  # weight 0: ignored


def test_published_loiret_scores_are_unchanged():
    # Loiret: zone 1, radon class 1, no wildfire, coast or avalanche anywhere.
    risques = pd.read_parquet(settings.data_dir / settings.risques_file)
    assert set(risques["zone_sismique"]) == {1} and set(risques["classe_radon"]) == {1}
    core = s.ScoringParams.from_settings()
    for row in risques.to_dict("records"):
        profile = s.compute_risk_profile(row, core)
        assert set(profile["aleas"]) == set(s.CORE_HAZARDS)
        weights = {h: core.weights[h] for h in s.CORE_HAZARDS}
        expected = sum(profile["aleas"][h] * w for h, w in weights.items()) / sum(weights.values())
        assert profile["score_global"] == pytest.approx(expected, abs=0.05)


def test_unknown_zone_or_radon_is_a_gap():
    gaps = s.compute_risk_profile({**FLAT, "zone_sismique": None, "classe_radon": None})["lacunes"]
    assert s.GAPS["zone_sismique"] in gaps and s.GAPS["classe_radon"] in gaps


# ---- data -------------------------------------------------------------------------------
def _gaspar_zip() -> bytes:
    def csv(header: str, *rows: str) -> str:
        return "\n".join([header, *rows]) + "\n"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("ddrm.csv", csv(
            "code_commune;libelle_commune;num_risque;libelle_risque",
            "06088;Nice;16;Feu de forêt", "06088;Nice;13;Séisme", "73054;Bourg;14;Avalanche",
        ))
        z.writestr("pprn.csv", csv(
            "code_insee_commune;libelle_risque2;libelle_risque3",
            "17300;Inondation;Par submersion marine",
            "17300;Inondation;Recul du trait de côte et de falaises",
            "73054;Avalanche;", "06088;Feu de forêt;",
        ))
        z.writestr("catnat.csv", csv(
            "code_commune;id_gaspar;libelle_risque_jo",
            "17300;A1;Chocs Mécaniques liés à l'action des Vagues", "17300;A1;Raz de Marée",
            "17300;A2;Chocs Mécaniques liés à l'action des Vagues", "73054;B1;Avalanche",
            "45234;C1;Inondations et/ou Coulées de Boue",
        ))
    return buffer.getvalue()


def test_commune_hazards_from_the_gaspar_export():
    out = hazards.commune_hazards(_gaspar_zip(), {"06088", "17300", "73054", "45234"})
    assert out["06088"]["ddrm_feu_foret"] and out["06088"]["ppr_feu_foret"]
    assert out["17300"]["ppr_submersion_marine"] and out["17300"]["ppr_recul_trait_cote"]
    assert out["17300"]["nb_catnat_littoral"] == 2  # distinct decrees (A1 counted once)
    assert out["73054"]["ddrm_avalanche"] and out["73054"]["ppr_avalanche"]
    assert out["73054"]["nb_catnat_avalanche"] == 1
    assert not any(out["45234"].values())  # listed nowhere: all False / 0


class _FakeGeorisques:
    def __init__(self, zones: dict[str, int]):
        self.zones, self.asked = zones, []

    def get_json(self, url, params):
        code = params["code_insee"]
        self.asked.append((url.rsplit("/", 1)[-1], code))
        if url.endswith("zonage_sismique"):
            return {"data": [{"code_zone": str(self.zones[code])}]}
        return {"data": []} if code == "99999" else {"data": [{"classe_potentiel": "2"}]}


def test_seismic_and_radon_are_fetched_per_commune():
    fake = _FakeGeorisques({"06088": 4, "99999": 1})
    out = hazards.fetch_seismic_radon(fake, ["06088", "06088", "99999"], workers=1)
    assert out == {"06088": {"zone_sismique": 4, "classe_radon": 2},
                   "99999": {"zone_sismique": 1, "classe_radon": None}}
    assert len(fake.asked) == 4  # each commune once, two calls each


def test_rolling_mode_only_rechecks_due_communes_and_unknown_schools():
    now = datetime(2026, 11, 25, tzinfo=UTC)
    communes = [f"{n:05d}" for n in range(10000, 10060)]
    due = {c for c in communes if rolling_due(c, now)}
    assert 0 < len(due) < len(communes)
    schools = [{"uai": f"U{c}", "code_insee": c} for c in communes] + [{"uai": "NEW", "code_insee": "10001"}]
    roster = pd.DataFrame(
        [{"uai": f"U{c}", "zone_sismique": 2, "classe_radon": 1} for c in communes]
    ).set_index("uai")
    fake = _FakeGeorisques(dict.fromkeys(communes, 3))
    notes: list[str] = []

    out = _seismic_radon(fake, schools, roster, 2, rolling=True, now=now, notes=notes)

    asked = {code for _, code in fake.asked}
    assert asked == due | {"10001"}  # the new school's commune is always fetched
    assert out["NEW"]["zone_sismique"] == 3
    kept = next(c for c in communes if c not in asked)
    assert out[f"U{kept}"] == {"zone_sismique": 2, "classe_radon": 1}  # published value kept
    assert notes and "re-checked" in notes[0]


# ---- outputs ----------------------------------------------------------------------------
def test_simulation_accepts_earthquake_and_radon_points(client):
    params = client.get(f"{API}/scores/parametres").json()
    assert params["seisme_points"]["4"] == 75 and params["radon_points"]["3"] == 60
    assert params["poids"]["seisme"] == settings.weight_seisme

    result = client.post(f"{API}/scores/simulation", json={"seisme_points": {"1": 50}}).json()
    assert result["resume"]["changements_de_niveau"] > 0  # all of Loiret is zone 1
    assert result["variables"]["SRT_SEISME_POINTS"].startswith('{"1":50.0,')

    bad = client.post(f"{API}/scores/simulation", json={"radon_points": {"4": 10}})
    assert bad.status_code == 422


def test_csv_has_a_column_per_hazard_and_distribution_report():
    profile = s.compute_risk_profile({**FLAT, "zone_sismique": 4})
    row = {"uai": "X", "nom": "N", "commune": "C", "statut": "Public", "secteur_id": None,
           "effectif_total": 100, "ips": 100.0, "score_global": profile["score_global"],
           "niveau": profile["niveau"], "alea_principal": profile["alea_principal"],
           "indice_priorite": None, "profil": profile, "alertes": [],
           "ppms": type("P", (), {"statut": "absent", "derniere_mise_a_jour": None})()}
    text = colleges_csv([row]).decode("utf-8-sig")
    header, line = text.splitlines()[:2]
    assert "Score séisme" in header and "Score avalanche" in header
    assert line.split(";")[header.split(";").index("Score séisme")] == "75"

    risques = pd.read_parquet(settings.data_dir / settings.risques_file)
    report = render_distribution(risques, datetime.now(UTC))
    assert "| **All** | 70 |" in report and "| 45 | 70 |" in report


def test_dashboard_statistics_with_schools_exposed_to_different_hazards(monkeypatch):
    from pathlib import Path

    from app.data.store import DataStore

    monkeypatch.setattr(settings, "data_dir", Path(__file__).parent / "e2e" / "fixtures" / "pilot")
    store = DataStore("files")
    everywhere = store.stats()["score_moyen_par_alea"]
    assert {"seisme", "littoral", "avalanche"} <= set(everywhere)
    assert list(store.stats("45")["score_moyen_par_alea"]) == list(s.CORE_HAZARDS)
    # Haute-Savoie: 69 schools in zone 4 (75 points), 6 in zone 3 (50).
    assert store.stats("74")["score_moyen_par_alea"]["seisme"] == round((69 * 75 + 6 * 50) / 75, 1)


def test_pdf_sheet_lists_the_data_behind_territorial_hazards(monkeypatch):
    from datetime import date
    from pathlib import Path

    from app.data.store import DataStore
    from app.models.ppms import PpmsStatus
    from app.services.fiche import FicheData, build_fiche, territorial_rows

    monkeypatch.setattr(settings, "data_dir", Path(__file__).parent / "e2e" / "fixtures" / "pilot")
    college = DataStore("files").get_college("0740001Z")  # Val d'Abondance: zone 4, avalanche
    aleas = college["profil_risque"]["aleas"]
    rows = dict(territorial_rows(college, aleas))
    assert rows["Zone de sismicité"] == "4 (moyenne)"
    assert rows["Avalanche : PPR / DDRM"] == "Oui / Oui"
    assert "Potentiel radon (catégorie 1 à 3)" not in rows  # class 1: radon not shown
    assert territorial_rows({"zone_sismique": 1}, {h: 0 for h in s.CORE_HAZARDS}) == []

    ppms = PpmsStatus(statut="absent", derniere_mise_a_jour=None, prochaine_mise_a_jour=None,
                      annee_scolaire="2026-2027", exercices_annee={}, raisons=[])
    pdf = build_fiche(FicheData(college=college, ppms=ppms, actions=[], snapshots=[],
                                generated_on=date.today()))
    assert pdf.startswith(b"%PDF-")
