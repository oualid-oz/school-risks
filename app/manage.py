"""Database maintenance commands.

    python -m app.manage check                         # schema revision vs code
    python -m app.manage load-reference                # data/*.parquet + GeoJSON -> PostGIS
    python -m app.manage copy-sqlite --from tracker.db # users, actions, audit log, snapshots
    python -m app.manage deploy-check                  # Vercel build step (see pyproject.toml)

Run `alembic upgrade head` first on a new Postgres database.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import func, select, text
from sqlmodel import SQLModel

from app.db import engine, is_postgres, make_engine, migration_status, register_models

# Parent tables first, so foreign keys are satisfied.
COPY_ORDER = ["app_user", "user_college", "action", "audit_log", "score_snapshot"]


def _require_postgres() -> None:
    if not is_postgres():
        sys.exit("SRT_DATABASE_URL (or DATABASE_URL) must point to Postgres for this command.")


def cmd_check(_: argparse.Namespace) -> int:
    if not is_postgres():
        print("SQLite: tables are created at startup, no migrations to check.")
        return 0
    current, head = migration_status(engine)
    print(f"Database revision: {current}\nCode revision:     {head}")
    if current != head:
        print("Run: alembic upgrade head", file=sys.stderr)
        return 1
    return 0


def cmd_load_reference(_: argparse.Namespace) -> int:
    _require_postgres()
    from app.services.reference_db import load_reference
    from app.services.spatial import sector_mismatches

    counts = load_reference(engine)
    print(f"Loaded {counts['college']} schools, {counts['college_annee']} school years, "
          f"{counts['secteur']} sectors ({counts['invalid_sectors']} invalid geometries).")
    with engine.connect() as conn:
        mismatches = sector_mismatches(conn)
    if mismatches:
        print(f"{len(mismatches)} school(s) lie outside their own sector (check the sector data):")
        for m in mismatches:
            print(f"  {m['uai']} {m['nom']} (sector {m['secteur_id']})")
    print("Restart the API to load the new data.")
    return 0


def cmd_copy_sqlite(args: argparse.Namespace) -> int:
    _require_postgres()
    source_path: Path = args.source
    if not source_path.exists():
        sys.exit(f"{source_path} not found")
    register_models()
    tables = SQLModel.metadata.tables
    source = make_engine(f"sqlite:///{source_path.resolve()}")

    with engine.begin() as target:
        not_empty = [
            name for name in COPY_ORDER
            if target.execute(select(func.count()).select_from(tables[name])).scalar_one()
        ]
        if not_empty:
            sys.exit(f"Target tables are not empty: {not_empty}. Nothing was copied.")
        with source.connect() as src:
            for name in COPY_ORDER:
                table = tables[name]
                rows = [dict(r) for r in src.execute(select(table)).mappings()]
                if rows:
                    target.execute(table.insert(), rows)
                print(f"{name}: {len(rows)} rows")
        # Explicit ids were inserted: move each sequence past the highest id.
        for name in COPY_ORDER:
            if "id" in tables[name].c:
                target.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {name}), 0) + 1, false)"
                    )
                )
    print("Copied. The SQLite file was not modified.")
    return 0


def cmd_deploy_check(_: argparse.Namespace) -> int:
    """Build step for serverless deployments (Vercel): migrate, then make sure the reference
    data is in the database. Loading it needs pyarrow, which is not installed there to stay
    under the bundle size limit, so it is done once from a workstation (see README)."""
    import os

    from alembic import command

    from app.db import alembic_config
    from app.models.reference import college

    _require_postgres()
    # Messages go to stdout and stderr: flush each line so build logs keep their order.
    sys.stdout.reconfigure(line_buffering=True)
    if os.environ.get("SRT_MIGRATE_ON_BUILD", "1").lower() in {"0", "false", "no"}:
        print("SRT_MIGRATE_ON_BUILD is off: migrations skipped.")
    else:
        command.upgrade(alembic_config(), "head")
        print("Schema is up to date.")

    current, head = migration_status(engine)
    if current != head:
        print(f"Schema at {current}, code needs {head}: run `alembic upgrade head`.", file=sys.stderr)
        return 1
    with engine.connect() as conn:
        schools = conn.execute(select(func.count()).select_from(college)).scalar_one()
    if not schools:
        print(
            "The reference tables are empty: run `python -m app.manage load-reference` "
            "against this database from a workstation, then redeploy.",
            file=sys.stderr,
        )
        return 1
    print(f"Reference data present: {schools} schools.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Compare the database schema revision with the code")
    check.set_defaults(func=cmd_check)
    load = sub.add_parser("load-reference", help="Load the data files into PostGIS")
    load.set_defaults(func=cmd_load_reference)
    c = sub.add_parser("copy-sqlite", help="Copy users, actions, audit log and snapshots from SQLite")
    c.add_argument(
        "--from", dest="source", type=Path, default=Path("tracker.db"),
        help="SQLite file (default: tracker.db)",
    )
    c.set_defaults(func=cmd_copy_sqlite)
    deploy = sub.add_parser("deploy-check", help="Migrate and verify the database (Vercel build step)")
    deploy.set_defaults(func=cmd_deploy_check)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
