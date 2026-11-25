from collections.abc import AsyncIterator
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
    # Sized above the worker threads (settings.worker_threads): requests do not queue for a
    # connection under load (SQLAlchemy's default is 5 + 10).
    return create_engine(
        url, pool_pre_ping=True, pool_recycle=300, connect_args={"prepare_threshold": None},
        pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow, pool_timeout=30,
    )


engine = make_engine(settings.database_url)


def register_models() -> None:
    from app.models import (  # noqa: F401  (registers the tables)
        notifications,
        ppms,
        security,
        snapshots,
        tracking,
    )


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
        _add_missing_sqlite_columns()
        return
    current, head = migration_status(engine)
    if current != head:
        raise RuntimeError(
            f"Database schema is at revision {current}, code expects {head}. "
            "Run: alembic upgrade head"
        )


def _add_missing_sqlite_columns() -> None:
    """SQLite has no migrations here: create_all() makes missing tables but never adds a
    column to an existing one, so add new nullable columns (e.g. from a later sprint)."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = column.type.compile(dialect=engine.dialect)
                if column.server_default is not None:
                    default = column.server_default.arg
                    if not isinstance(default, str):
                        default = default.compile(
                            dialect=engine.dialect, compile_kwargs={"literal_binds": True}
                        )
                    ddl += f" NOT NULL DEFAULT {default}" if not column.nullable else f" DEFAULT {default}"
                elif not column.nullable:
                    raise RuntimeError(
                        f"{table.name}.{column.name} is new, NOT NULL and has no server default: "
                        "recreate the SQLite database or use Postgres with Alembic."
                    )
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}'))


async def get_session() -> AsyncIterator[Session]:
    """One session per request. Async on purpose: its cleanup (which returns the connection
    to the pool) then runs on the event loop. As a sync dependency, the cleanup needs a
    worker thread, and under load every thread can be an endpoint waiting for a connection
    that only those cleanups would free: requests stall until the pool timeout. The session
    itself is still used from the endpoints' threads (one at a time)."""
    with Session(engine) as session:
        yield session
