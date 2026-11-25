"""Offline tests for the data pipeline and the yearly score snapshots."""
import json
import os

import httpx
import pandas as pd
import pytest

from app.core.config import settings
from app.data.store import get_store
from app.db import get_session
from app.main import app
from app.pipeline import education, georisques
from app.pipeline.build import (
    BuildResult,
    compare,
    publish,
    validate,
    write_staging,
)
from app.pipeline.client import Fetcher, SourceError
from app.pipeline.geo import haversine_km, nearest_centrale
from app.services.snapshots import SnapshotExists, take_snapshot

API = "/api/v1"
SCHOOL = "0450003J"


# ---- HTTP client --------------------------------------------------------------
def test_fetcher_caches_retries_and_handles_empty_bodies(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/flaky" and len([c for c in calls if "flaky" in c]) == 1:
            return httpx.Response(503)
        if request.url.path == "/empty":
            return httpx.Response(200, content=b"")
        return httpx.Response(200, json={"ok": request.url.params.get("x")})

    transport = httpx.MockTransport(handler)
    with Fetcher(tmp_path, transport=transport, backoff=0) as f:
        assert f.get_json("https://api.test/flaky", {"x": "1"}) == {"ok": "1"}
        assert f.get_json("https://api.test/flaky", {"x": "1"}) == {"ok": "1"}  # cached
        assert f.get_json("https://api.test/empty") is None
    assert len([c for c in calls if "flaky" in c]) == 2

    with Fetcher(tmp_path, offline=True) as f:
        assert f.get_json("https://api.test/flaky", {"x": "1"}) == {"ok": "1"}
        with pytest.raises(SourceError):
            f.get_json("https://api.test/never-fetched")


# ---- education transforms ---------------------------------------------------------
def test_dnb_prefers_value_added_file_and_computes_mention_rate():
    dnb = [
        {"numero_d_etablissement": SCHOOL, "session": "2017", "presents": 114, "admis": 101,
         "admis_sans_mention": 31, "taux_de_reussite": "88,50%"},
        {"numero_d_etablissement": SCHOOL, "session": "2022", "presents": 1, "admis": 1,
         "admis_sans_mention": 1, "taux_de_reussite": "100%"},
    ]
    ivac = [{"uai": SCHOOL, "session": "2022", "nb_candidats_g": 104, "taux_de_reussite_g": 97.0,
             "va_du_taux_de_reussite_g": 5.0, "nb_mentions_global_g": 84}]
    out = education.dnb_by_key(dnb, ivac)
    assert out[(SCHOOL, 2017)] == {
        "dnb_candidats": 114, "dnb_taux_reussite": 88.5, "dnb_taux_mention": 61.4,
        "dnb_valeur_ajoutee": None, "source_dnb": "dnb-par-etablissement",
    }
    assert out[(SCHOOL, 2022)]["source_dnb"] == "ivac"
    assert out[(SCHOOL, 2022)]["dnb_taux_mention"] == 80.8


def test_history_starts_at_first_year_with_data_and_maps_rep_plus():
    school = {"uai": SCHOOL, "nom": "Test", "commune": "X", "code_insee": "45008",
              "statut": "public", "lat": 48.0, "lon": 1.8, "secteur_id": SCHOOL}
    raw = {
        "effectifs": [{"numero_college": SCHOOL, "rentree_scolaire": "2021",
                       "nombre_eleves_total": 400, "rep": 0, "rep0": 1}],
        "ips": [
            {"uai": SCHOOL, "rentree_scolaire": "2021-2022", "ips": 90.0, "_dataset": "fr-en-ips_colleges"},
            {"uai": SCHOOL, "rentree_scolaire": "2022-2023", "ips": 91.0,
             "_dataset": "fr-en-ips-colleges-ap2022"},
        ],
        "dnb": [],
        "ivac": [{"uai": SCHOOL, "session": "2022", "nb_candidats_g": 10,
                  "taux_de_reussite_g": 90, "nb_mentions_global_g": 5}],
    }
    rows = education.build_history([school], raw, first_year=2016)
    assert [r["rentree"] for r in rows] == [2021, 2022]
    assert rows[0]["rep_plus"] == 1 and rows[0]["effectif_total"] == 400
    assert rows[0]["session_dnb"] == 2022 and rows[0]["dnb_candidats"] == 10
    assert rows[1]["ips"] == 91.0 and rows[1]["effectif_total"] is None


# ---- Géorisques transforms --------------------------------------------------------
def test_catnat_counts_distinct_decrees():
    rows = [
        {"code_national_catnat": "A", "libelle_risque_jo": "Inondations et/ou Coulées de Boue"},
        {"code_national_catnat": "A", "libelle_risque_jo": "Mouvement de Terrain"},
        {"code_national_catnat": "B", "libelle_risque_jo": "Sécheresse"},
    ]
    assert georisques.catnat_counts(rows) == {
        "nb_catnat": 2, "nb_catnat_inondations": 1,
        "nb_catnat_secheresse": 1, "nb_catnat_mouvement_terrain": 1,
    }


def test_clay_ppri_and_dicrim_parsing():
    assert georisques.argiles_classe({"exposition": "Exposition forte"}) == "fort"
    assert georisques.argiles_classe({"exposition": "Exposition moyenne"}) == "moyen"
    assert georisques.argiles_classe(None) == "non cartographié"
    gpu = {"features": [{"properties": {"nomsuplitt": "PPRi du Val de Sully"}}]}
    assert georisques.ppri(gpu) == "PPRi du Val de Sully"
    assert georisques.ppri({"features": []}) is None
    assert georisques.annee_dicrim([{"annee_publication": "2009"}, {"annee_publication": "2015"}]) == 2015
    assert georisques.annee_dicrim([]) is None


def test_icpe_metrics_skip_non_icpe_and_closing_sites():
    lat, lon = 48.0, 2.0
    near = {"latitude": 48.001, "longitude": 2.0, "regime": "Autorisation",
            "statutSeveso": "Non Seveso", "etatActivite": "En exploitation avec titre"}
    ignored = [
        {**near, "latitude": 48.0001, "regime": "Non ICPE"},
        {**near, "latitude": 48.0002, "etatActivite": "En fin d'exploitation"},
    ]
    seveso = {**near, "latitude": 48.03, "statutSeveso": "Seveso seuil haut"}
    m = georisques.icpe_metrics(lat, lon, [near, seveso, *ignored])
    assert m["nb_icpe_1km"] == 1
    assert m["distance_icpe_km"] == pytest.approx(0.11, abs=0.01)
    assert m["distance_seveso_km"] == pytest.approx(3.34, abs=0.01)


def test_nearest_centrale():
    plants = {"Saint Laurent des Eaux": (47.7191, 1.5784), "Dampierre en Burly": (47.7334, 2.5171)}
    name, km = nearest_centrale(47.72, 1.60, plants)
    assert name == "Saint Laurent des Eaux" and km < 3
    assert haversine_km(48.0, 2.0, 48.0, 2.0) == 0


# ---- validation, comparison, publish ------------------------------------------------
def _current() -> BuildResult:
    d = settings.data_dir
    return BuildResult(
        pd.read_parquet(d / settings.risques_file), pd.read_parquet(d / settings.colleges_file)
    )


def test_published_files_pass_validation_and_compare_clean():
    result = _current()
    assert validate(result, result.risques) == []
    diff = compare(result.risques, result.risques, result.history, result.history)
    assert diff["columns"] == {}
    assert diff["history"] == {"added": [], "removed": [], "changed": {}}
    assert all(s["old"] == s["new"] for s in diff["scores"])


def test_validation_blocks_bad_data_and_compare_reports_changes():
    result = _current()
    bad = result.risques.copy()
    bad.loc[0, "lat"] = 10.0
    bad.loc[1, "nb_catnat"] = 99
    errors = validate(BuildResult(bad, result.history), result.risques)
    assert any("outside France" in e for e in errors)

    diff = compare(bad, result.risques)
    assert {c["uai"] for c in diff["columns"]["nb_catnat"]} == {bad.loc[1, "uai"]}

    # Schools come and go with the directory; losing half a department is a broken source.
    first = result.risques.uai[0]
    rest = BuildResult(result.risques.iloc[1:], result.history[result.history.uai != first])
    one_less = validate(rest, result.risques)
    assert not any("drops" in e for e in one_less)
    half = result.risques.iloc[::2]
    errors = validate(BuildResult(half, result.history[result.history.uai.isin(half.uai)]), result.risques)
    assert any("department 45 drops from 70 to 35" in e for e in errors)


def test_publish_archives_previous_files_and_swaps_atomically(tmp_path):
    result = _current()
    data_dir, staging = tmp_path / "data", tmp_path / "staging"
    data_dir.mkdir()
    for name in (settings.risques_file, settings.colleges_file):
        (data_dir / name).write_bytes(b"old")
    write_staging(result, staging)

    backup = publish(staging, data_dir, tmp_path / "archive", "20260101T000000Z")

    assert (backup / settings.risques_file).read_bytes() == b"old"
    assert json.loads((backup / "published.json").read_text())["files"]
    published = pd.read_parquet(data_dir / settings.risques_file)
    assert len(published) == len(result.risques)
    assert not os.access(data_dir / settings.risques_file, os.W_OK) or os.geteuid() == 0
    assert not list(data_dir.glob(".*.tmp"))


# ---- snapshots -----------------------------------------------------------------
def test_snapshot_is_stored_once_per_year_and_served(client):
    session = next(app.dependency_overrides[get_session]())
    store = get_store()

    assert take_snapshot(session, store, 2025) == 70
    with pytest.raises(SnapshotExists):
        take_snapshot(session, store, 2025)
    assert take_snapshot(session, store, 2025, replace=True) == 70
    take_snapshot(session, store, 2026)

    rows = client.get(f"{API}/colleges/{SCHOOL}/scores-annuels").json()
    assert [r["annee"] for r in rows] == [2025, 2026]
    assert rows[0]["score_global"] == store.get_profile(SCHOOL)["score_global"]
    assert rows[0]["indice_priorite"] == store.get_profile(SCHOOL)["indice_priorite"]
    assert rows[0]["effectif_total"] == store.get_college(SCHOOL)["effectif_total"]
    assert set(rows[0]["aleas"]) == set(store.get_profile(SCHOOL)["aleas"])
    assert client.get(f"{API}/colleges/0000000X/scores-annuels").status_code == 404
