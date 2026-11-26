from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

import app.db as app_db
from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.data.store import get_store
from app.db import get_session
from app.main import app
from app.models.security import User, UserRole

ADMIN_PASSWORD_HASH = hash_password("test-admin-password")
# The tests run on frozen Loiret data, not on `data/`, which changes with each publication.
LOIRET = Path(__file__).parent / "fixtures" / "loiret"


@pytest.fixture(scope="session", autouse=True)
def loiret_data():
    published = settings.data_dir
    settings.data_dir = LOIRET
    get_store.cache_clear()
    yield LOIRET
    settings.data_dir = published
    get_store.cache_clear()


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    monkeypatch.setattr(settings, "jwt_secret_key", "test-jwt-signing-key-with-32-bytes-min")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    # Never touch the development database, even when .env points to Postgres.
    monkeypatch.setattr(settings, "database_url", "sqlite://")
    monkeypatch.setattr(app_db, "engine", engine)
    get_store.cache_clear()
    app_db.register_models()  # every table, including ones added later

    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    with Session(engine) as session:
        admin = User(
            username="test-admin",
            hashed_password=ADMIN_PASSWORD_HASH,
            role=UserRole.admin,
        )
        session.add(admin)
        session.commit()
        session.refresh(admin)
        admin_token = create_access_token(admin)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {admin_token}"
        yield c
    app.dependency_overrides.clear()
    get_store.cache_clear()


def wipe_database(url: str) -> None:
    """An empty database for the Postgres tests. Dropping the schema works whatever data a
    previous run left; `alembic downgrade base` fails on data the old schemas cannot hold."""
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    engine.dispose()
