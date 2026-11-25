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
    # The database store reads one school's history at a time (not held in memory).
    assert from_db.history is None
    assert all(from_db.get_history(uai) == from_files.get_history(uai) for uai in from_files.uais())
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


def test_ppms_on_postgres(pg_client):
    from datetime import date

    school = pg_client.get(f"{API}/colleges").json()[0]["uai"]
    today = str(date.today())
    assert pg_client.post(f"{API}/colleges/{school}/ppms/revisions",
                          json={"date_mise_a_jour": today}).status_code == 201
    assert pg_client.post(f"{API}/colleges/{school}/ppms/exercices",
                          json={"type_exercice": "intrusion", "date_exercice": today}).status_code == 201
    assert pg_client.get(f"{API}/colleges/{school}/ppms").json()["statut"]["statut"] == "a_jour"
    assert pg_client.get(f"{API}/stats").json()["ppms_par_statut"]["a_jour"] >= 1


def test_digest_is_sent_once_per_week_on_postgres(pg_client, pg_engine, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from sqlmodel import select

    from app.models.notifications import NotificationLog
    from app.services import digest as digest_service

    sent = []

    class Box:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def send(self, email):
            sent.append(email.to)

    monkeypatch.setattr(digest_service, "get_mailer", Box)
    # a school with something to report (no PPMS recorded by earlier tests)
    school = pg_client.get(f"{API}/colleges", params={"ppms": "absent"}).json()[0]["uai"]
    pg_client.post(f"{API}/auth/users", json={
        "username": "pg-digest-ref", "password": "a-strong-test-password", "role": "referent",
        "school_uais": [school], "email": "pg-ref@example.fr", "alertes_email": True,
    }).raise_for_status()
    monday = datetime(2026, 11, 23, 8, tzinfo=UTC)
    with Session(pg_engine) as session:
        first = digest_service.send_digests(session, get_store(), now=monday)
        second = digest_service.send_digests(session, get_store(), now=monday + timedelta(hours=2))
        rows = session.exec(select(NotificationLog).where(NotificationLog.period == "2026-W48")).all()
        # a failed row is taken over by exactly one of two competing runs (compare-and-set)
        row = rows[0]
        row.status = "failed"
        session.add(row)
        session.commit()
        old_stamp = row.updated_at
    assert (first["sent"], second["already_sent"]) == (1, 1)
    assert sent == ["pg-ref@example.fr"]
    with Session(pg_engine) as a, Session(pg_engine) as b:
        user_a = a.get(digest_service.User, row.user_id)
        user_b = b.get(digest_service.User, row.user_id)
        winner = digest_service._claim(a, user_a, "2026-W48", monday + timedelta(hours=3))
        loser = digest_service._claim(b, user_b, "2026-W48", monday + timedelta(hours=3))
        assert winner is not None and winner.updated_at != old_stamp
        assert loser is None


def test_deploy_check_reloads_changed_data_files(pg_engine, monkeypatch, capsys, tmp_path):
    import shutil

    import pandas as pd
    from sqlalchemy import select as sa_select

    import app.manage as manage
    from app.models.reference import college

    monkeypatch.setattr(settings, "database_url", URL)
    monkeypatch.setattr(app_db, "engine", pg_engine)
    monkeypatch.setattr(manage, "engine", pg_engine)
    load_reference(pg_engine)  # database in step with the repository files
    assert manage.main(["deploy-check"]) == 0
    assert "loaded" not in capsys.readouterr().out  # nothing changed: no reload

    # A data refresh renames a school in the files.
    for name in (settings.risques_file, settings.colleges_file, settings.secteurs_file):
        shutil.copyfile(settings.data_dir / name, tmp_path / name)  # not the read-only mode
    risques = pd.read_parquet(tmp_path / settings.risques_file)
    risques.loc[risques["uai"] == "0450003J", "nom"] = "Jean Moulin (renommé)"
    risques.to_parquet(tmp_path / settings.risques_file, index=False)
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    monkeypatch.setenv("SRT_MIGRATE_ON_BUILD", "0")  # read-only build: reports, does not load
    assert manage.main(["deploy-check"]) == 0
    assert "differ from the database" in capsys.readouterr().err

    monkeypatch.delenv("SRT_MIGRATE_ON_BUILD")
    assert manage.main(["deploy-check"]) == 0
    assert "differ from the deployed data files" in capsys.readouterr().out
    with pg_engine.connect() as conn:
        name = conn.execute(sa_select(college.c.nom).where(college.c.uai == "0450003J")).scalar_one()
    assert name == "Jean Moulin (renommé)"
    load_reference(pg_engine, settings.data_dir)  # leave the files' version for later tests


def test_migration_0006_backfills_history_from_the_audit_log(pg_engine):
    from sqlalchemy import text as sql

    config = alembic_config(URL)
    command.downgrade(config, "0005")
    with pg_engine.begin() as conn:
        user_id = conn.execute(sql(
            "INSERT INTO app_user (username, hashed_password, role, active, created_at, alertes_email) "
            "VALUES ('backfill-ref', 'x', 'referent', true, now(), false) RETURNING id")).scalar_one()
        action_id = conn.execute(sql(
            "INSERT INTO action (titre, alea, priorite, statut, uai, created_at, updated_at) "
            "VALUES ('Ancienne action', 'general', 'normale', 'a_faire', '0450003J', now(), now()) "
            "RETURNING id")).scalar_one()
        for event, resource, details in (
            ("action.created", action_id, '{"uai": "0450003J"}'),
            ("action.updated", action_id, '{"uai": "0450003J", "updated_fields": ["priorite", "statut"]}'),
            ("action.created", 999999, '{"uai": "0450003J"}'),  # deleted since: skipped
        ):
            conn.execute(sql(
                "INSERT INTO audit_log (actor_user_id, actor_username, event, resource_type, resource_id, "
                "details, created_at) VALUES (:u, 'backfill-ref', :e, 'action', :r, :d, now())"),
                {"u": user_id, "e": event, "r": str(resource), "d": details})
    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        rows = conn.execute(sql(
            "SELECT kind, changes, author_username FROM action_event WHERE action_id = :a ORDER BY id"),
            {"a": action_id}).all()
        orphans = conn.execute(sql("SELECT count(*) FROM action_event WHERE action_id = 999999")).scalar()
    assert [r[0] for r in rows] == ["creation", "modification"] and orphans == 0
    assert rows[1][1] == '[{"champ": "priorite", "avant": null, "apres": null}, ' \
                         '{"champ": "statut", "avant": null, "apres": null}]'
    assert rows[0][2] == "backfill-ref"
    with pg_engine.begin() as conn:  # leave the database as the other tests expect
        conn.execute(sql("DELETE FROM action_event WHERE action_id = :a"), {"a": action_id})
        conn.execute(sql("DELETE FROM action WHERE id = :a"), {"a": action_id})
