"""Alembic environment. The database URL comes from the app settings (SRT_DATABASE_URL)."""
from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from app.core.config import settings
from app.db import register_models
from app.models.reference import reference_metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

register_models()
target_metadata = [SQLModel.metadata, reference_metadata]

def include_object(obj, name, type_, reflected, compare_to):
    # The PostGIS image ships its own tables (spatial_ref_sys, TIGER geocoder, topology).
    # Only manage tables that the app defines; never drop anything else.
    if reflected and compare_to is None:
        if type_ == "table":
            return False
        table = getattr(obj, "table", None)
        if table is not None and table.name not in _our_tables():
            return False
    return alembic_helpers.include_object(obj, name, type_, reflected, compare_to)


def _our_tables() -> set[str]:
    return {t for metadata in target_metadata for t in metadata.tables}


def _configure(**kwargs):
    context.configure(
        target_metadata=target_metadata,
        include_object=include_object,
        process_revision_directives=alembic_helpers.writer,
        render_item=alembic_helpers.render_item,
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
