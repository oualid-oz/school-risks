"""Downloads: schools and actions as CSV (French Excel), and a PDF fiche per school.

Access follows the actions: referents export their own schools only (403 for another
school's fiche or actions); admins and readers export everything. Every export is audited.
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import Session

from app.api.routes.actions import find_actions
from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.security import assigned_schools, get_current_user, record_audit
from app.data.store import DataStore, get_store
from app.db import get_session
from app.models.security import User, UserRole
from app.models.tracking import Alea, Priorite, Statut
from app.services.exports import actions_csv, colleges_csv
from app.services.fiche import FicheData, build_fiche
from app.services.ppms import statuses as ppms_statuses
from app.services.snapshots import history_for

router = APIRouter(tags=["exports"], dependencies=[Depends(get_current_user)])
CSV = "text/csv; charset=utf-8"
OPEN = (Statut.a_faire, Statut.en_cours)


def _download(content: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",  # personal, access-controlled data
        },
    )


def _scope(session: Session, user: User) -> set[str] | None:
    """Schools a user may export; None means all."""
    return assigned_schools(session, user) if user.role == UserRole.referent else None


@router.get("/exports/colleges.csv")
@limiter.limit(settings.rate_limit_default)
def export_colleges(
    request: Request,
    commune: str | None = None,
    statut: Literal["public", "privé"] | None = None,
    secteur_id: str | None = None,
    niveau: Literal["faible", "moyen", "eleve"] | None = None,
    q: str | None = None,
    tri: Literal["score", "priorite"] = "score",
    ppms: Literal["a_jour", "ancien", "absent"] | None = None,
    departement: str | None = None,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Same filters and order as `GET /colleges`, plus the score of each hazard."""
    rows = store.list_colleges(commune, statut, secteur_id, niveau, q, tri, departement)
    scope = _scope(session, user)
    if scope is not None:
        rows = [r for r in rows if r["uai"] in scope]
    status = ppms_statuses(session, [r["uai"] for r in rows])
    rows = [
        {
            **r,
            "profil": store.get_profile(r["uai"]),
            "ppms": status[r["uai"]],
            "alertes": [*store.get_profile(r["uai"])["alertes"], *status[r["uai"]].raisons],
        }
        for r in rows
        if ppms is None or status[r["uai"]].statut == ppms
    ]
    filters = {k: v for k, v in {"commune": commune, "statut": statut, "secteur_id": secteur_id,
                                 "niveau": niveau, "q": q, "tri": tri, "ppms": ppms,
                                 "departement": departement}.items() if v}
    record_audit(session, request, user, "export.colleges_csv", "export", "colleges",
                 {"filters": filters, "rows": len(rows)})
    session.commit()
    return _download(colleges_csv(rows), CSV, f"colleges_{date.today():%Y-%m-%d}.csv")


@router.get("/exports/actions.csv")
@limiter.limit(settings.rate_limit_default)
def export_actions(
    request: Request,
    uai: str | None = None,
    alea: Alea | None = None,
    statut: Statut | None = None,
    priorite: Priorite | None = None,
    en_retard: bool | None = None,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Same filters and access rules as `GET /actions`."""
    actions = find_actions(session, user, uai, alea, statut, priorite, en_retard)
    names = {a.uai: (store.get_college(a.uai) or {}).get("nom", "") for a in actions}
    record_audit(session, request, user, "export.actions_csv", "export", uai or "actions",
                 {"rows": len(actions), "uai": uai})
    session.commit()
    suffix = f"_{uai}" if uai else ""
    return _download(actions_csv(actions, names), CSV, f"actions{suffix}_{date.today():%Y-%m-%d}.csv")


@router.get("/colleges/{uai}/fiche.pdf")
@limiter.limit("30/minute")
def export_fiche(
    request: Request,
    uai: str,
    store: DataStore = Depends(get_store),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """One-page (or two) risk summary of a school, to print or attach."""
    college = store.get_college(uai)
    if college is None:
        raise HTTPException(404, f"Collège {uai} introuvable")
    scope = _scope(session, user)
    if scope is not None and uai not in scope:
        raise HTTPException(403, "Pas d’accès à ce collège")
    ppms = ppms_statuses(session, [uai])[uai]
    profile = college["profil_risque"]
    data = FicheData(
        college={**college, "profil_risque": {**profile, "alertes": [*profile["alertes"], *ppms.raisons]}},
        ppms=ppms,
        actions=[a for a in find_actions(session, user, uai) if a.statut in OPEN],
        snapshots=history_for(session, uai),
        generated_on=date.today(),
    )
    pdf = build_fiche(data)
    record_audit(session, request, user, "export.fiche_pdf", "college", uai, {"bytes": len(pdf)})
    session.commit()
    return _download(pdf, "application/pdf", f"fiche_risques_{uai}_{date.today():%Y-%m-%d}.pdf")
