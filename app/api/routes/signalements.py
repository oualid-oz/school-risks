"""Data error reports: anyone who sees a school reports; administrators of its area close."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlmodel import Session, select

from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.scope import Scope, get_scope
from app.core.security import record_audit
from app.data.store import DataStore, get_store
from app.db import get_session
from app.models.feedback import (
    DataReport,
    DataReportClose,
    DataReportCreate,
    DataReportRead,
    StatutSignalement,
)
from app.models.security import UserRole

router = APIRouter(tags=["signalements"])


def _read(store: DataStore, report: DataReport) -> DataReportRead:
    college = store.get_college(report.uai) or {}
    return DataReportRead(**report.model_dump(), nom=college.get("nom"))


@router.post("/colleges/{uai}/signalements", response_model=DataReportRead, status_code=201)
@limiter.limit("20/hour")
def report_error(
    uai: str,
    payload: DataReportCreate,
    request: Request,
    session: Session = Depends(get_session),
    store: DataStore = Depends(get_store),
    scope: Scope = Depends(get_scope),
):
    """Report a data error on a school (any role, readers included)."""
    scope.require_visible(store, uai)
    report = DataReport(
        uai=uai,
        departement=(store.get_college(uai) or {}).get("departement"),
        champ=payload.champ,
        message=payload.message.strip(),
        created_by_user_id=scope.user.id,
        created_by_username=scope.user.username,
    )
    session.add(report)
    session.flush()
    record_audit(session, request, scope.user, "signalement.created", "data_report", report.id,
                 {"uai": uai, "champ": report.champ.value})
    session.commit()
    session.refresh(report)
    return _read(store, report)


@router.get("/signalements", response_model=list[DataReportRead])
@limiter.limit(settings.rate_limit_default)
def list_reports(
    request: Request,
    statut: StatutSignalement | None = None,
    departement: str | None = Query(None, max_length=3),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    response: Response = None,
    session: Session = Depends(get_session),
    store: DataStore = Depends(get_store),
    scope: Scope = Depends(get_scope),
):
    """Newest first. Administrators: their area; referents: their schools; readers: their own
    reports. The total before paging is in the `X-Total-Count` header."""
    scope.check_departement(departement)
    stmt = select(DataReport)
    if scope.user.role == UserRole.referent:
        stmt = stmt.where(DataReport.uai.in_(scope.managed or []))
    elif scope.user.role == UserRole.lecteur:
        stmt = stmt.where(DataReport.created_by_user_id == scope.user.id)
    elif not scope.national:
        stmt = stmt.where(DataReport.departement.in_(scope.departements))
    if statut:
        stmt = stmt.where(DataReport.statut == statut)
    if departement:
        stmt = stmt.where(DataReport.departement == departement)
    reports = session.exec(stmt.order_by(DataReport.created_at.desc(), DataReport.id.desc())).all()
    session.close()
    response.headers["X-Total-Count"] = str(len(reports))
    return [_read(store, r) for r in reports[offset:offset + limit]]


@router.patch("/signalements/{report_id}", response_model=DataReportRead)
@limiter.limit(settings.rate_limit_default)
def close_report(
    report_id: int,
    payload: DataReportClose,
    request: Request,
    session: Session = Depends(get_session),
    store: DataStore = Depends(get_store),
    scope: Scope = Depends(get_scope),
):
    """Administrators of the school's area: mark it handled (`traite`) or rejected (`rejete`,
    the data was right), with an answer the reporter sees; or reopen it (`ouvert`)."""
    report = session.get(DataReport, report_id)
    if report is None or not scope.sees(store, report.uai):
        raise HTTPException(404, "Signalement introuvable")
    if scope.user.role != UserRole.admin:
        raise HTTPException(403, "Seuls les administrateurs traitent les signalements")
    report.statut = payload.statut
    report.reponse = payload.reponse.strip() if payload.reponse else report.reponse
    closing = payload.statut != StatutSignalement.ouvert
    report.closed_by_username = scope.user.username if closing else None
    report.closed_at = datetime.now(UTC) if closing else None
    session.add(report)
    record_audit(session, request, scope.user, "signalement.updated", "data_report", report.id,
                 {"uai": report.uai, "statut": report.statut.value})
    session.commit()
    session.refresh(report)
    return _read(store, report)
