"""Bulk mode (national files + PostGIS), offline: small synthetic files in the download
cache, with known distances. Needs a disposable PostGIS database (only the `bulk` schema is
used), for example the one of tests/test_postgres.py:

    export SRT_TEST_DATABASE_URL=postgresql+psycopg://srt:srt-dev-password@127.0.0.1:5433/school_risks_test
    pytest tests/test_bulk.py
"""
import io
import os
import zipfile

import pytest
from sqlalchemy import create_engine, text

from app.pipeline import bulk, georisques

URL = os.environ.get("SRT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="SRT_TEST_DATABASE_URL is not set")

SCHOOL = {"uai": "0450001A", "code_insee": "45234", "lat": 47.9, "lon": 1.9}
FAR = {"uai": "0450002B", "code_insee": "45999", "lat": 47.5, "lon": 2.5}  # nothing around
NORTH_300M = 300 / 111_320  # degrees of latitude


def _csv(header: list[str], rows: list[list[object]]) -> bytes:
    lines = [";".join(header)] + [";".join("" if v is None else str(v) for v in r) for r in rows]
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(URL)
    yield eng
    with eng.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {bulk.SCHEMA} CASCADE"))
    eng.dispose()


@pytest.fixture(scope="module")
def cache(tmp_path_factory, engine):
    path = tmp_path_factory.mktemp("bulk")
    gaspar = io.BytesIO()
    with zipfile.ZipFile(gaspar, "w") as z:
        z.writestr("catnat.csv", _csv(
            ["code_commune", "id_gaspar", "libelle_risque_jo"],
            [
                # One decree covering floods and ground movement counts once in the total.
                ["45234", "D1", "Inondations et/ou Coulées de Boue"],
                ["45234", "D1", "Mouvement de terrain"],
                ["45234", "D2", "Sécheresse"],
                ["45999", "D9", "Sécheresse"],
            ],
        ))
        z.writestr("dicrim.csv", _csv(
            ["code_commune", "date_publi_dicrim"], [["45234", "2012-03-01"], ["45234", "2019-06-30"]]
        ))
    (path / "gaspar_national.zip").write_bytes(gaspar.getvalue())

    lat, lon = SCHOOL["lat"], SCHOOL["lon"]
    (path / "icpe.csv").write_bytes(_csv(
        ["code_aiot", "x", "y", "code_epsg", "num_dep", "lib_regime", "lib_seveso"],
        [
            ["A1", lon, lat + NORTH_300M, 4326, "45", "Autorisation", None],
            ["A2", lon, lat + NORTH_300M / 3, 4326, "45", "Non ICPE", None],  # not counted
            ["A3", lon, lat + 5000 / 111_320, 4326, "45", "Autorisation", "Seveso seuil haut"],
            ["A4", None, None, 4326, "45", "Autorisation", None],  # no position: skipped
            # No department code (557 such rows nationally): never a department to download.
            ["A5", lon + 0.001, lat, 4326, "", "Non ICPE", None],
        ],
    ))
    # Cavities come in Lambert II étendu: let PostGIS project the test points.
    with engine.connect() as conn:
        def l2e(dlat: float) -> tuple[float, float]:
            row = conn.execute(text(
                "SELECT ST_X(p), ST_Y(p) FROM ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),"
                " 27572) p"), {"lon": lon, "lat": lat + dlat}).one()
            return row[0], row[1]
        near, hidden, far = l2e(200 / 111_320), l2e(100 / 111_320), l2e(800 / 111_320)
    (path / "cavites_45.csv").write_bytes(_csv(
        ["id", "xouvl2e", "youvl2e", "confidentialite"],
        [["C1", *near, "public"], ["C2", *hidden, "confidentiel"], ["C3", *far, "public"]],
    ))
    (path / "mvt_45.csv").write_bytes(_csv(
        ["idMvt", "longitudeDoublePrec", "latitudeDoublePrec"],
        [["M1", lon, lat + NORTH_300M], ["M2", lon, lat + 0.01]],
    ))
    return path


def test_bulk_indicators_match_the_api_definitions(engine, cache):
    dl = bulk.Downloader(cache, offline=True)
    bulk.reset_schema(engine)
    loaded = bulk.load_national(engine, dl)
    assert (loaded["catnat"], loaded["dicrim"]) == (4, 2)

    nearby = bulk.departments_near(engine, [SCHOOL, FAR])
    assert nearby == ["45"]
    assert bulk.load_departmental(engine, dl, nearby)["cavites"] == 2  # the confidential one is left out

    out = bulk.indicators(engine, [SCHOOL, FAR])
    near = out[SCHOOL["uai"]]
    assert near["nb_icpe_1km"] == 1  # "Non ICPE" ignored
    assert near["distance_icpe_km"] == pytest.approx(0.30, abs=0.01)
    assert near["distance_seveso_km"] == pytest.approx(5.0, abs=0.02)
    assert (near["nb_cavites_500m"], near["nb_mouvements_terrain_500m"]) == (1, 1)
    assert near["annee_dicrim"] == 2019
    # Same counting as the API pipeline: distinct decrees.
    assert near | georisques.catnat_counts([
        {"code_national_catnat": "D1", "libelle_risque_jo": "Inondations et/ou Coulées de Boue"},
        {"code_national_catnat": "D1", "libelle_risque_jo": "Mouvement de terrain"},
        {"code_national_catnat": "D2", "libelle_risque_jo": "Sécheresse"},
    ]) == near

    far = out[FAR["uai"]]
    assert (far["nb_icpe_1km"], far["nb_cavites_500m"], far["nb_mouvements_terrain_500m"]) == (0, 0, 0)
    assert far["annee_dicrim"] is None
    assert (far["nb_catnat"], far["nb_catnat_secheresse"]) == (1, 1)


def test_offline_mode_never_downloads(tmp_path):
    with pytest.raises(FileNotFoundError, match="offline"):
        bulk.Downloader(tmp_path, offline=True).get("icpe.csv", bulk.ICPE_URL)


# ---- sectors from the national school map (Sprint 20) ----------------------------------
def test_school_map_sectors_whole_split_and_unlocated_communes(engine, tmp_path):
    import gzip
    import json

    from app.pipeline import school_map

    def square(x0: float, y0: float) -> dict:
        ring = [[x0, y0], [x0 + 0.1, y0], [x0 + 0.1, y0 + 0.1], [x0, y0 + 0.1], [x0, y0]]
        return {"type": "Polygon", "coordinates": [ring]}

    communes = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"code": code, "departement": dep}, "geometry": square(x, 47.0)}
        for code, dep, x in (("74001", "74", 6.0), ("74002", "74", 6.1), ("74003", "74", 6.2),
                             ("45001", "45", 6.3))
    ]}
    (tmp_path / "communes-50m.geojson.gz").write_bytes(gzip.compress(json.dumps(communes).encode()))
    (tmp_path / "carte_scolaire.csv").write_text(
        "code_insee;code_rne\n"
        "74001;0740001A\n"                      # whole commune: exact
        "74002;0740001A\n74002;0740002B\n"     # split, both located: Voronoi halves
        "74003;0740002B\n74003;0740099Z\n"     # split, one without position: whole to each
        "45001;0740001A\n",                    # department with its own map: left out
        encoding="utf-8",
    )
    schools = [
        {"uai": "0740001A", "lat": 47.05, "lon": 6.12, "nom": "Alpha", "commune": "Un",
         "code_insee": "74002", "departement_nom": "Haute-Savoie"},
        {"uai": "0740002B", "lat": 47.05, "lon": 6.18, "nom": "Bravo", "commune": "Deux",
         "code_insee": "74002", "departement_nom": "Haute-Savoie"},
    ]
    dl = bulk.Downloader(tmp_path, offline=True)
    bulk.reset_schema(engine)
    assert school_map.load(engine, dl, schools)["communes"] == 4

    features, coverage = school_map.build(engine, {s["uai"]: s for s in schools}, excluded=["45"])
    by_id = {f["properties"]["secteur_id"]: f for f in features}
    assert set(by_id) == {"0740001A", "0740002B", "0740099Z"}
    alpha = by_id["0740001A"]["properties"]
    assert alpha["secteur"] == "Alpha (Un)" and alpha["departements"] == ["74"]
    assert alpha["source"] == "carte_scolaire_approchee"  # it shares 74002
    assert by_id["0740099Z"]["properties"]["secteur"] == "Collège 0740099Z"  # not in our roster

    with engine.connect() as conn:
        def area(uai: str) -> float:
            geometry = json.dumps(by_id[uai]["geometry"])
            return conn.execute(text("SELECT ST_Area(ST_GeomFromGeoJSON(:g))"), {"g": geometry}).scalar()
        # Alpha: all of 74001 + the western half of 74002 (closer to Alpha) = 1.5 squares.
        assert area("0740001A") == pytest.approx(0.015, rel=0.02)
        # Bravo: the eastern half of 74002 + all of 74003 (its partner has no position).
        assert area("0740002B") == pytest.approx(0.015, rel=0.02)
        assert area("0740099Z") == pytest.approx(0.010, rel=0.02)
    assert coverage["74"] == {"sectors": 3, "approximate": 3, "communes": 3}
    assert "45" not in coverage
