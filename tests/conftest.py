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
