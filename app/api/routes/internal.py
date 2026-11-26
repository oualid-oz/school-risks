"""Endpoints for schedulers, not for people: protected by a shared secret, not a login."""
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlmodel import Session

from app.api.routes.auth import limiter
from app.core.config import settings
from app.data.store import DataStore, get_store
from app.db import get_session
from app.services.digest import send_digests

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


def require_cron_secret(authorization: str | None = Header(default=None)) -> None:
    if not settings.cron_secret:
        raise HTTPException(503, "Scheduler endpoints are disabled: set CRON_SECRET")
    expected = f"Bearer {settings.cron_secret}"
    if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(401, "Invalid scheduler secret")


# GET because that is what Vercel Cron calls; POST for other schedulers.
@router.api_route("/digest", methods=["GET", "POST"], dependencies=[Depends(require_cron_secret)])
@limiter.limit("10/minute")
def run_digest(
    request: Request,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
):
    """Send this week's email digests (from the configured weekday and hour on), then delete
    personal data past its retention period (the only daily job on Vercel).

    Safe to call daily or hourly: each person gets at most one digest per week.
    """
    from app.services.retention import purge

    return {**send_digests(session, store), "purge": purge(session)}
