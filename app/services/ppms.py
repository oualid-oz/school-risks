"""PPMS status: computed from the recorded plan updates and drills."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlmodel import Session, func, select

from app.core.config import settings
from app.models.ppms import PpmsExercice, PpmsRevision, PpmsStatus, TypeExercice

SCHOOL_YEAR_START_MONTH = 9  # rentrée: 1 September

TYPE_LABELS = {
    TypeExercice.risques_majeurs: "risques majeurs",
    TypeExercice.intrusion: "intrusion",
    TypeExercice.incendie: "incendie",
}


def school_year_start(today: date) -> date:
    year = today.year if today.month >= SCHOOL_YEAR_START_MONTH else today.year - 1
    return date(year, SCHOOL_YEAR_START_MONTH, 1)


def add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    # 31 January + 1 month -> 28/29 February
    for candidate in (day.day, 30, 29, 28):
        try:
            return date(year, month, candidate)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def compute_status(
    last_update: date | None,
    drills: list[tuple[TypeExercice, date]],
    today: date,
) -> PpmsStatus:
    """Pure rule, so it is easy to test. ``drills``: (type, date) of this school year."""
    start = school_year_start(today)
    latest: dict[TypeExercice, date | None] = dict.fromkeys(TypeExercice)
    for kind, day in drills:
        if start <= day <= today and (latest[kind] is None or day > latest[kind]):
            latest[kind] = day
    year_label = f"{start.year}-{start.year + 1}"

    if last_update is None:
        return PpmsStatus(
            statut="absent", derniere_mise_a_jour=None, prochaine_mise_a_jour=None,
            annee_scolaire=year_label, exercices_annee=latest, raisons=["PPMS non renseigné"],
        )

    reasons = []
    due = add_months(last_update, settings.ppms_max_age_months)
    if due < today:
        reasons.append(f"PPMS non mis à jour depuis le {last_update:%d/%m/%Y}")
    counted = [TypeExercice(t) for t in settings.ppms_exercices_comptes]
    grace_over = (today - start).days >= settings.ppms_delai_exercice_jours
    if counted and grace_over and not any(latest[t] for t in counted):
        kinds = " ou ".join(TYPE_LABELS[t] for t in counted)
        reasons.append(f"Aucun exercice PPMS ({kinds}) cette année scolaire")
    return PpmsStatus(
        statut="ancien" if reasons else "a_jour",
        derniere_mise_a_jour=last_update,
        prochaine_mise_a_jour=due,
        annee_scolaire=year_label,
        exercices_annee=latest,
        raisons=reasons,
    )


def statuses(session: Session, uais: list[str], today: date | None = None) -> dict[str, PpmsStatus]:
    """Status of many schools with two queries (not one per school)."""
    today = today or date.today()
    last = dict(
        session.exec(
            select(PpmsRevision.uai, func.max(PpmsRevision.date_mise_a_jour))
            .where(PpmsRevision.uai.in_(uais))
            .group_by(PpmsRevision.uai)
        ).all()
    )
    drills: dict[str, list[tuple[TypeExercice, date]]] = defaultdict(list)
    for uai, kind, day in session.exec(
        select(PpmsExercice.uai, PpmsExercice.type_exercice, PpmsExercice.date_exercice).where(
            PpmsExercice.uai.in_(uais),
            PpmsExercice.date_exercice >= school_year_start(today),
        )
    ).all():
        drills[uai].append((TypeExercice(kind), day))
    return {uai: compute_status(last.get(uai), drills[uai], today) for uai in uais}


def status_for(session: Session, uai: str, today: date | None = None) -> PpmsStatus:
    return statuses(session, [uai], today)[uai]
