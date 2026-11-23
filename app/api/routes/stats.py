from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, func, select

from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.security import get_current_user
from app.data.store import DataStore, get_store
from app.db import get_session
from app.models.tracking import Action
from app.services.ppms import statuses as ppms_statuses

router = APIRouter(
    prefix="/stats", tags=["stats"], dependencies=[Depends(get_current_user)]
)


@router.get("", response_model=dict[str, Any])
@limiter.limit(settings.rate_limit_default)
def overview(
    request: Request,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
):
    """Dashboard numbers: risk distribution plus follow-up progress."""
    rows = session.exec(select(Action.statut, func.count()).group_by(Action.statut)).all()
    ppms = Counter(s.statut for s in ppms_statuses(session, store.uais()).values())
    return {
        **store.stats(),
        "actions_par_statut": {s.value: n for s, n in rows},
        "ppms_par_statut": {k: ppms.get(k, 0) for k in ("a_jour", "ancien", "absent")},
    }
