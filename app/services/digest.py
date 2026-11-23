"""Weekly email digest: overdue actions, actions due soon, PPMS to update.

Referents get their schools, admins the whole department. Only active users who opted in
(`alertes_email`) and gave an address receive it; readers (lecteur) never do.

Idempotency: one `notification_log` row per (user, kind, ISO week). A run claims the row
before sending, so a second run in the same week, or two overlapping runs, never send twice.
Failed sends, and claims left behind by a run that was cut short, are retried next run.
"""
from __future__ import annotations

import html
import logging
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.security import assigned_schools
from app.data.store import DataStore
from app.models.notifications import NotificationLog
from app.models.security import User, UserRole
from app.models.tracking import Action, Statut
from app.services.mailer import Email, get_mailer
from app.services.ppms import statuses as ppms_statuses

logger = logging.getLogger(__name__)

KIND = "digest_hebdo"
OPEN = (Statut.a_faire, Statut.en_cours)
STALE_CLAIM = timedelta(minutes=15)  # a "sending" row older than this was left by a dead run
MAX_LINES = 15  # per section in the admin summary; the rest is counted


def iso_week(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def digest_due(now: datetime) -> bool:
    """True from the configured weekday and hour (UTC) until the end of the ISO week."""
    return (now.weekday(), now.hour) >= (settings.digest_weekday, settings.digest_hour_utc)


# ---- content ------------------------------------------------------------------------
@dataclass
class Line:
    uai: str
    school: str
    text: str


@dataclass
class Digest:
    overdue: list[Line] = field(default_factory=list)
    due_soon: list[Line] = field(default_factory=list)
    ppms: list[Line] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.overdue or self.due_soon or self.ppms)


def collect(session: Session, store: DataStore, uais: list[str], today: date) -> Digest:
    names = {uai: (store.get_college(uai) or {}).get("nom", uai) for uai in uais}
    digest = Digest()
    soon = today + timedelta(days=settings.digest_due_soon_days)
    actions = session.exec(
        select(Action)
        .where(Action.uai.in_(uais), Action.statut.in_(OPEN), Action.echeance.is_not(None))
        .where(Action.echeance <= soon)
        .order_by(Action.echeance, Action.uai)
    ).all()
    for a in actions:
        late = a.echeance < today
        when = f"échéance {a.echeance:%d/%m/%Y}"
        if late:
            when += f", {(today - a.echeance).days} j de retard"
        line = Line(a.uai, names[a.uai], f"{a.titre} ({when})")
        (digest.overdue if late else digest.due_soon).append(line)
    for uai, status in sorted(ppms_statuses(session, uais, today).items()):
        if status.statut != "a_jour":
            digest.ppms.append(Line(uai, names[uai], " ; ".join(status.raisons)))
    return digest


SECTIONS = (
    ("overdue", "Actions en retard"),
    ("due_soon", "Actions à échéance dans les {days} jours"),
    ("ppms", "PPMS à mettre à jour"),
)


def render(user: User, digest: Digest, scope: str, today: date) -> Email:
    link = settings.public_url.rstrip("/")
    counts = [len(digest.overdue), len(digest.due_soon), len(digest.ppms)]
    subject = (
        f"[Risques collèges] Semaine du {today - timedelta(days=today.weekday()):%d/%m} : "
        f"{counts[0]} en retard, {counts[1]} à échéance, {counts[2]} PPMS"
    )
    text_parts = [f"Bonjour {user.username},", "", f"Point hebdomadaire pour {scope}.", ""]
    html_parts = [
        f"<p>Bonjour {html.escape(user.username)},</p>",
        f"<p>Point hebdomadaire pour {html.escape(scope)}.</p>",
    ]
    for attr, title in SECTIONS:
        lines: list[Line] = getattr(digest, attr)
        if not lines:
            continue
        title = title.format(days=settings.digest_due_soon_days)
        shown, hidden = lines[:MAX_LINES], len(lines) - MAX_LINES
        text_parts.append(f"{title} ({len(lines)})")
        text_parts += [f"- {ln.school} ({ln.uai}) : {ln.text}\n  {link}/#school/{ln.uai}" for ln in shown]
        html_items = "".join(
            f'<li><a href="{html.escape(link)}/#school/{html.escape(ln.uai)}">{html.escape(ln.school)}</a>'
            f" : {html.escape(ln.text)}</li>"
            for ln in shown
        )
        html_parts.append(f"<h3>{html.escape(title)} ({len(lines)})</h3><ul>{html_items}</ul>")
        if hidden > 0:
            text_parts.append(f"… et {hidden} autre(s) : voir le tableau de bord.")
            html_parts.append(f"<p>… et {hidden} autre(s) : voir le tableau de bord.</p>")
        text_parts.append("")
    footer = (
        f"Tableau de bord : {link}/\n"
        "Vous recevez ce message car les alertes e-mail sont activées sur votre compte "
        "(bouton « Mon compte » en haut de l’application pour les désactiver)."
    )
    text_parts.append(footer)
    html_parts.append(
        f'<p><a href="{html.escape(link)}/">Tableau de bord</a></p>'
        "<p style=\"color:#777;font-size:12px\">Vous recevez ce message car les alertes e-mail "
        "sont activées sur votre compte (bouton « Mon compte » en haut de l’application "
        "pour les désactiver).</p>"
    )
    return Email(to=user.email, subject=subject, text="\n".join(text_parts), html="".join(html_parts))


def digest_for(session: Session, store: DataStore, user: User, today: date) -> Email | None:
    if user.role == UserRole.admin:
        uais, scope = store.uais(), "l'ensemble des collèges du département"
    elif user.role == UserRole.referent:
        uais = sorted(assigned_schools(session, user))
        scope = f"vos {len(uais)} collège(s)" if len(uais) > 1 else "votre collège"
    else:
        return None
    if not uais:
        return None
    digest = collect(session, store, uais, today)
    return None if digest.empty() else render(user, digest, scope, today)


# ---- sending -----------------------------------------------------------------------------
def recipients(session: Session) -> list[User]:
    return list(
        session.exec(
            select(User)
            .where(
                User.active.is_(True),
                User.alertes_email.is_(True),
                User.email.is_not(None),
                User.role.in_([UserRole.admin, UserRole.referent]),
            )
            .order_by(User.id)
        ).all()
    )


def _claim(session: Session, user: User, period: str, now: datetime) -> NotificationLog | None:
    """Reserve the (user, week) slot. None if it was already sent or is being sent."""
    row = NotificationLog(
        user_id=user.id, kind=KIND, period=period, status="sending",
        recipient=user.email, created_at=now, updated_at=now,
    )
    session.add(row)
    try:
        session.commit()
        return row
    except IntegrityError:
        session.rollback()
    existing = session.exec(
        select(NotificationLog).where(
            NotificationLog.user_id == user.id,
            NotificationLog.kind == KIND,
            NotificationLog.period == period,
        )
    ).one()
    retryable = existing.status == "failed" or (
        existing.status == "sending" and _aware(existing.updated_at) < now - STALE_CLAIM
    )
    if not retryable:
        return None
    # Compare-and-set: only one concurrent run can take over the row.
    taken = session.execute(
        update(NotificationLog)
        .where(
            NotificationLog.id == existing.id,
            NotificationLog.status == existing.status,
            NotificationLog.updated_at == existing.updated_at,
        )
        .values(status="sending", updated_at=now, recipient=user.email, error=None)
    )
    session.commit()
    if taken.rowcount != 1:
        return None
    session.refresh(existing)
    return existing


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _finish(session: Session, row: NotificationLog, status: str, error: str | None = None) -> None:
    row.status, row.error, row.updated_at = status, (error or None) and error[:500], datetime.now(UTC)
    session.add(row)
    session.commit()


def send_digests(
    session: Session,
    store: DataStore,
    now: datetime | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Send this week's digests. Returns counts; safe to call as often as you like."""
    now = now or datetime.now(UTC)
    period = iso_week(now)
    result = {"period": period, "due": digest_due(now), "sent": 0, "already_sent": 0,
              "nothing_to_report": 0, "failed": 0, "deferred": 0}
    if not (result["due"] or force):
        return result

    started = time.monotonic()
    users = recipients(session)
    with ExitStack() as stack:
        mailer = None
        for index, user in enumerate(users):
            if time.monotonic() - started > settings.digest_time_budget_seconds:
                result["deferred"] = len(users) - index
                logger.warning("digest time budget used up", extra={"deferred": result["deferred"]})
                break
            email = digest_for(session, store, user, now.date())
            if email is None:
                result["nothing_to_report"] += 1
                continue
            if dry_run:
                logger.info("digest (dry run)", extra={"to": email.to, "subject": email.subject})
                result["sent"] += 1
                continue
            row = _claim(session, user, period, now)
            if row is None:
                result["already_sent"] += 1
                continue
            if mailer is None:  # connect only when there is something to send
                try:
                    mailer = stack.enter_context(get_mailer())
                except Exception as exc:  # noqa: BLE001 - server down / bad settings: stop here
                    logger.exception("mail server unavailable")
                    _finish(session, row, "failed", f"{type(exc).__name__}: {exc}")
                    result["failed"] += 1
                    result["deferred"] = len(users) - index - 1
                    break
            try:
                mailer.send(email)
            except Exception as exc:  # noqa: BLE001 - recorded, retried next run
                logger.exception("digest not sent", extra={"user_id": user.id})
                _finish(session, row, "failed", f"{type(exc).__name__}: {exc}")
                result["failed"] += 1
                continue
            _finish(session, row, "sent")
            result["sent"] += 1
    logger.info("digest run", extra=result)
    return result
