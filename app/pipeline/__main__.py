"""Command line for the data pipeline.

    python -m app.pipeline build              # download, rebuild, validate, write a report
    python -m app.pipeline build --publish    # ...and replace data/*.parquet if valid
    python -m app.pipeline build --keep argiles_classe   # refresh all but that column
    python -m app.pipeline snapshot           # store this year's scores in the database

The API loads the data files once at startup: restart it after publishing.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
from sqlmodel import Session

from app.core.config import parse_departements, settings
from app.db import is_postgres
from app.pipeline.build import build, compare, publish, render_report, validate, write_staging
from app.pipeline.client import Fetcher

logger = logging.getLogger("app.pipeline")


def cmd_build(args: argparse.Namespace) -> int:
    started = datetime.now(UTC)
    stamp = f"{started:%Y%m%dT%H%M%SZ}"
    data_dir: Path = settings.data_dir
    cache = args.cache_dir or data_dir / "raw" / f"{started:%Y-%m-%d}"
    previous = pd.read_parquet(data_dir / settings.risques_file)
    previous_history = pd.read_parquet(data_dir / settings.colleges_file)

    departements = parse_departements(args.departements) if args.departements else settings.departements
    if "ALL" in departements:
        print("All of France needs the bulk-data pipeline (Sprint 18): list departments instead, "
              "e.g. --departements 45,17,74.", file=sys.stderr)
        return 2
    with Fetcher(cache, offline=args.offline) as fetcher:
        result = build(
            fetcher, previous, departements=departements, first_year=args.first_year,
            workers=args.workers, keep=args.keep,
        )

    errors = validate(result, previous)
    diff = compare(result.risques, previous, result.history, previous_history)
    if result.secteurs is not None:
        from app.pipeline import sectors

        published = json.loads((data_dir / settings.secteurs_file).read_text(encoding="utf-8"))
        diff["secteurs"] = sectors.compare(result.secteurs, published)
    report = render_report(result, diff, errors, started)

    staging = data_dir / "staging" / stamp
    write_staging(result, staging)
    (staging / "report.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"Staged files and report: {staging}")
    print(f"Raw API responses: {cache}")

    if errors:
        print("Validation failed: nothing was published.", file=sys.stderr)
        return 1
    if not args.publish:
        print("Dry run: re-run with --publish to replace the data files.")
        return 0

    backup = publish(staging, data_dir, data_dir / "archive", stamp)
    print(f"Published. Previous files archived in {backup}.")
    if is_postgres():
        from app.db import engine
        from app.services.reference_db import load_reference

        counts = load_reference(engine)
        print(f"Loaded into PostGIS: {counts['college']} schools, {counts['college_annee']} school years.")
    print("Restart the API to load the new data.")
    if args.snapshot:
        return _snapshot(date.today().year, replace=True)
    return 0


def _snapshot(annee: int, replace: bool) -> int:
    from app.data.store import DataStore
    from app.db import engine, init_db
    from app.services.snapshots import SnapshotExists, take_snapshot

    init_db()
    with Session(engine) as session:
        try:
            store = DataStore("database" if is_postgres() else "files")
            count = take_snapshot(session, store, annee, replace=replace)
        except SnapshotExists as exc:
            print(exc, file=sys.stderr)
            return 1
    print(f"Snapshot {annee}: {count} schools stored.")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    return _snapshot(args.year, args.replace)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.pipeline")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="Rebuild the parquet files from the source APIs")
    b.add_argument("--publish", action="store_true", help="Replace the data files if valid")
    b.add_argument("--snapshot", action="store_true", help="After publishing, store this year's scores")
    b.add_argument("--offline", action="store_true", help="Only use cached API responses")
    b.add_argument("--cache-dir", type=Path, help="Where raw responses are cached")
    b.add_argument("--first-year", type=int, default=2016, help="First school year of the history")
    b.add_argument("--workers", type=int, default=4, help="Parallel API requests")
    b.add_argument(
        "--departements", metavar="CODES",
        help="Departments to build, e.g. 45,17,74 (default: SRT_DEPARTEMENTS, else 45)",
    )
    b.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="COLUMN",
        help="Copy this risk column from the published file instead of refreshing it (repeatable)",
    )
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("snapshot", help="Store the current scores for a year")
    s.add_argument("--year", type=int, default=date.today().year)
    s.add_argument("--replace", action="store_true", help="Overwrite an existing snapshot")
    s.set_defaults(func=cmd_snapshot)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    from app.core.monitoring import init_error_reporting

    init_error_reporting(f"pipeline:{args.command}")  # a failing monthly refresh is reported
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
