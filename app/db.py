from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import BASE_DIR, settings

ALEMBIC_INI = BASE_DIR / "alembic.ini"


def is_postgres(url: str | None = None) -> bool:
    return (url or settings.database_url).startswith("postgresql")


def make_engine(url: str):
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    # prepare_threshold=None: no server-side prepared statements, so the URL may point to a
    # transaction-mode connection pooler (PgBouncer, Neon's "-pooler" host).
    return create_engine(
        url, pool_pre_ping=True, pool_recycle=300, connect_args={"prepare_threshold": None}
    )


engine = make_engine(settings.database_url)


def register_models() -> None:
    from app.models import security, snapshots, tracking  # noqa: F401  (registers the tables)


def alembic_config(url: str | None = None):
    from alembic.config import Config

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(Path(BASE_DIR) / "migrations"))
    config.set_main_option("sqlalchemy.url", (url or settings.database_url).replace("%", "%%"))
    return config


def migration_status(bind) -> tuple[str | None, str]:
    """(current revision of the database, latest revision in migrations/)."""
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if not inspect(bind).has_table("alembic_version"):
        return None, head
    with bind.connect() as conn:
        current = conn.execute(text("select version_num from alembic_version")).scalar()
    return current, head


def init_db() -> None:
    """SQLite: create missing tables. Postgres: the schema is owned by Alembic, so only
    check that it is up to date (run `alembic upgrade head` after pulling new code)."""
    register_models()
    if settings.on_vercel and not is_postgres():
        raise RuntimeError(
            "Vercel has a read-only filesystem: set DATABASE_URL (or SRT_DATABASE_URL) "
            "to a Postgres + PostGIS database."
        )
    if not is_postgres():
        SQLModel.metadata.create_all(engine)
        return
    current, head = migration_status(engine)
    if current != head:
        raise RuntimeError(
            f"Database schema is at revision {current}, code expects {head}. "
            "Run: alembic upgrade head"
        )


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
