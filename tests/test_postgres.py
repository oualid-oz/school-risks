"""PostGIS integration tests. They need a disposable database, for example:

    docker compose up -d db
    psql -h 127.0.0.1 -p 5433 -U srt school_risks -c "CREATE DATABASE school_risks_test"
    export SRT_TEST_DATABASE_URL=postgresql+psycopg://srt:srt-dev-password@127.0.0.1:5433/school_risks_test
    pytest tests/test_postgres.py

The database is wiped (alembic downgrade base) at the start of the run.
"""
import os

import pandas as pd
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlmodel import Session

import app.db as app_db
from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.data.store import DataStore, get_store
from app.db import alembic_config, get_session, make_engine, migration_status
from app.main import app
from app.models.security import User, UserRole
from app.services.reference_db import load_reference
from app.services.spatial import sector_mismatches

URL = os.environ.get("SRT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="SRT_TEST_DATABASE_URL is not set")
API = "/api/v1"


@pytest.fixture(scope="module")
def pg_engine():
    config = alembic_config(URL)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = make_engine(URL)
    load_reference(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_client(pg_engine, monkeypatch):
    limiter.reset()
    monkeypatch.setattr(settings, "jwt_secret_key", "test-jwt-signing-key-with-32-bytes-min")
    monkeypatch.setattr(settings, "database_url", URL)
    monkeypatch.setattr(app_db, "engine", pg_engine)
    get_store.cache_clear()

    def _session():
        with Session(pg_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    with Session(pg_engine) as session:
        admin = session.query(User).filter_by(username="pg-admin").first()
        if admin is None:
            admin = User(
                username="pg-admin",
                hashed_password=hash_password("pg-admin-password"),
                role=UserRole.admin,
            )
            session.add(admin)
            session.commit()
            session.refresh(admin)
        token = create_access_token(admin)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {token}"
        yield c
    app.dependency_overrides.clear()
    get_store.cache_clear()


def test_schema_is_at_head(pg_engine):
    current, head = migration_status(pg_engine)
    assert current == head


def test_database_store_matches_file_store(pg_engine, monkeypatch):
    monkeypatch.setattr(app_db, "engine", pg_engine)
    from_db, from_files = DataStore("database"), DataStore("files")
    pd.testing.assert_frame_equal(from_db.risques.sort_values("uai").reset_index(drop=True),
                                  from_files.risques.sort_values("uai").reset_index(drop=True))
    pd.testing.assert_frame_equal(from_db.history.sort_values(["uai", "rentree"]).reset_index(drop=True),
                                  from_files.history.sort_values(["uai", "rentree"]).reset_index(drop=True))
    assert from_db.list_colleges() == from_files.list_colleges()
    assert len(from_db.secteurs["features"]) == len(from_files.secteurs["features"])


def test_every_school_lies_in_its_own_sector(pg_engine):
    with pg_engine.connect() as conn:
        assert sector_mismatches(conn) == []


def test_locate_sector_contains_the_school(pg_client):
    school = next(c for c in pg_client.get(f"{API}/colleges").json() if c["secteur_id"])
    point = {"lat": school["lat"], "lon": school["lon"]}
    found = pg_client.get(f"{API}/secteurs/localiser", params=point).json()
    assert found["secteur"]["secteur_id"] == school["secteur_id"]
    assert school["uai"] in {c["uai"] for c in found["colleges"]}

    nowhere = pg_client.get(f"{API}/secteurs/localiser", params={"lat": 43.3, "lon": 5.4}).json()
    assert nowhere == {"secteur": None, "colleges": []}


def test_nearby_schools_are_sorted_by_true_distance(pg_client):
    school = pg_client.get(f"{API}/colleges").json()[0]
    rows = pg_client.get(
        f"{API}/colleges/proches", params={"lat": school["lat"], "lon": school["lon"], "rayon_km": 10}
    ).json()
    assert rows[0]["uai"] == school["uai"] and rows[0]["distance_km"] == 0
    distances = [r["distance_km"] for r in rows]
    assert distances == sorted(distances) and distances[-1] <= 10
    too_far = {"lat": 48, "lon": 2, "rayon_km": 500}
    assert pg_client.get(f"{API}/colleges/proches", params=too_far).status_code == 422


def test_actions_and_snapshots_work_on_postgres(pg_client):
    school = pg_client.get(f"{API}/colleges").json()[0]["uai"]
    created = pg_client.post(f"{API}/actions", json={"uai": school, "titre": "Test PostGIS"})
    assert created.status_code == 201
    assert pg_client.get(f"{API}/actions", params={"uai": school}).json()[0]["titre"] == "Test PostGIS"
    audit = pg_client.get(f"{API}/auth/audit").json()
    assert any(r["event"] == "action.created" for r in audit)


def test_readiness_checks_the_schema_on_postgres(pg_client):
    body = pg_client.get("/health/ready").json()
    assert body["checks"] == {"database": "ok", "schema": "ok", "reference_data": "ok"}


def test_deploy_check_migrates_and_loads_an_empty_database(pg_engine, monkeypatch, capsys):
    import app.manage as manage

    command.downgrade(alembic_config(URL), "base")  # a brand-new database, e.g. a Neon preview branch
    monkeypatch.setattr(settings, "database_url", URL)
    monkeypatch.setattr(app_db, "engine", pg_engine)
    monkeypatch.setattr(manage, "engine", pg_engine)

    monkeypatch.setenv("SRT_MIGRATE_ON_BUILD", "0")  # read-only build: refuses, writes nothing
    assert manage.main(["deploy-check"]) == 1

    monkeypatch.delenv("SRT_MIGRATE_ON_BUILD")
    assert manage.main(["deploy-check"]) == 0
    assert "loaded 70 schools" in capsys.readouterr().out
    assert manage.main(["deploy-check"]) == 0  # second build: data already there
    assert migration_status(pg_engine)[0] == migration_status(pg_engine)[1]
