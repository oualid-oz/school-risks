"""Health endpoints for container orchestration and monitoring (no authentication)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.core.config import settings
from app.data.store import get_store

router = APIRouter(tags=["meta"])
logger = logging.getLogger(__name__)


@router.get("/health")
def health():
    """Liveness: the process is up and serving requests."""
    return {"status": "ok", "version": settings.build_version}


@router.get("/health/ready")
def ready(response: Response):
    """Readiness: database reachable, schema up to date, reference data loaded.

    Answers 503 when a check fails. Details stay in the logs, not in the response.
    """
    from app import db

    checks: dict[str, str] = {}
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.exception("readiness: database check failed")
        checks["database"] = "error"

    if db.is_postgres() and checks["database"] == "ok":
        try:
            current, head = db.migration_status(db.engine)
            checks["schema"] = "ok" if current == head else "outdated"
        except Exception:
            logger.exception("readiness: schema check failed")
            checks["schema"] = "error"

    try:
        checks["reference_data"] = "ok" if get_store().list_colleges() else "empty"
    except Exception:
        logger.exception("readiness: reference data check failed")
        checks["reference_data"] = "error"

    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "unavailable", "version": settings.build_version, "checks": checks}
