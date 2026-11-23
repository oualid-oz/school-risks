"""Sprint 10: weekly email digests."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.core.config import settings
from app.data.store import get_store
from app.db import get_session
from app.main import app
from app.models.notifications import NotificationLog
from app.models.security import User
from app.services import digest as digest_service
from app.services import mailer as mailer_service
from app.services.digest import digest_due, digest_for, iso_week, send_digests
from tests.test_security import create_account, login

API = "/api/v1"
SCHOOL = "0450003J"
MONDAY = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)  # Monday of ISO week 2026-W48
TODAY = MONDAY.date()


class FakeMailer:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def send(self, email):
        if self.fail:
            raise ConnectionError("smtp down")
        self.sent.append(email)


@pytest.fixture
def session(client):
    return next(app.dependency_overrides[get_session]())


@pytest.fixture
def outbox(monkeypatch):
    box = FakeMailer()
    monkeypatch.setattr(digest_service, "get_mailer", lambda: box)
    return box


def action(client, uai, titre, days, statut="a_faire"):
    body = {"uai": uai, "titre": titre, "echeance": str(TODAY + timedelta(days=days)), "statut": statut}
    response = client.post(f"{API}/actions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def referent(client, session, name="digest-ref", schools=(SCHOOL,), email="ref@example.fr", opt_in=True):
    created = create_account(client, name, "referent", list(schools))
    user = session.get(User, created["id"])
    user.email, user.alertes_email = email, opt_in
    session.add(user)
    session.commit()
    return user


# ---- timing ---------------------------------------------------------------------------
def test_week_and_due_time():
    assert iso_week(MONDAY) == "2026-W48"
    assert iso_week(datetime(2027, 1, 1, tzinfo=UTC)) == "2026-W53"
    assert digest_due(MONDAY)  # Monday 08:00, after 06:00
    assert not digest_due(MONDAY.replace(hour=5))
    # Sunday closes the previous ISO week, whose digest was due since its Monday 06:00.
    assert iso_week(MONDAY - timedelta(days=1)) == "2026-W47"
    assert digest_due(MONDAY - timedelta(days=1))
    assert digest_due(MONDAY + timedelta(days=3))


# ---- content ----------------------------------------------------------------------------
def test_referent_digest_lists_only_their_schools(client, session):
    other = next(c["uai"] for c in client.get(f"{API}/colleges").json() if c["uai"] != SCHOOL)
    action(client, SCHOOL, "Réviser le plan <crue>", -3)
    action(client, SCHOOL, "Exercice de confinement", 5)
    action(client, SCHOOL, "Hors fenêtre", 30)
    action(client, SCHOOL, "Déjà terminée", -10, statut="termine")
    action(client, other, "Autre collège", -1)
    user = referent(client, session)

    email = digest_for(session, get_store(), user, TODAY)
    assert email.to == "ref@example.fr"
    assert "Actions en retard (1)" in email.text and "3 j de retard" in email.text
    assert "Actions à échéance dans les 7 jours (1)" in email.text
    assert "PPMS à mettre à jour (1)" in email.text  # no PPMS recorded for the school
    for absent in ("Hors fenêtre", "Déjà terminée", "Autre collège"):
        assert absent not in email.text
    assert "&lt;crue&gt;" in email.html and "<crue>" not in email.html  # escaped
    assert f"/#school/{SCHOOL}" in email.text
    assert email.subject.endswith("1 en retard, 1 à échéance, 1 PPMS")


def test_admin_summary_covers_the_department_and_is_capped(client, session):
    admin = session.exec(select(User).where(User.username == "test-admin")).one()
    email = digest_for(session, get_store(), admin, TODAY)
    assert "l'ensemble des collèges" in email.text
    assert "PPMS à mettre à jour (70)" in email.text
    assert "… et 55 autre(s)" in email.text  # 15 lines shown


def test_nothing_to_report_sends_nothing(client, session):
    user = referent(client, session)
    for kind, body in (("revisions", {"date_mise_a_jour": str(TODAY)}),
                       ("exercices", {"type_exercice": "intrusion", "date_exercice": str(TODAY)})):
        client.post(f"{API}/colleges/{SCHOOL}/ppms/{kind}", json=body).raise_for_status()
    assert digest_for(session, get_store(), user, TODAY) is None


# ---- sending -----------------------------------------------------------------------------
def test_one_digest_per_person_and_week(client, session, outbox):
    referent(client, session)
    first = send_digests(session, get_store(), now=MONDAY)
    again = send_digests(session, get_store(), now=MONDAY + timedelta(days=1))
    next_week = send_digests(session, get_store(), now=MONDAY + timedelta(days=7))
    assert (first["sent"], again["sent"], again["already_sent"], next_week["sent"]) == (1, 0, 1, 1)
    assert [e.to for e in outbox.sent] == ["ref@example.fr", "ref@example.fr"]
    rows = session.exec(select(NotificationLog)).all()
    assert sorted((r.period, r.status) for r in rows) == [("2026-W48", "sent"), ("2026-W49", "sent")]


def test_only_opted_in_admins_and_referents_with_an_address(client, session, outbox):
    referent(client, session, "ref-on")
    referent(client, session, "ref-off", opt_in=False)
    referent(client, session, "ref-no-mail", email=None)
    inactive = referent(client, session, "ref-inactive")
    inactive.active = False
    session.add(inactive)
    reader = session.get(User, create_account(client, "reader-x", "lecteur")["id"])
    reader.email, reader.alertes_email = "reader@example.fr", True
    session.add(reader)
    session.commit()
    send_digests(session, get_store(), now=MONDAY)
    assert [e.to for e in outbox.sent] == ["ref@example.fr"]


def test_not_before_the_configured_time_unless_forced(client, session, outbox):
    referent(client, session)
    early = send_digests(session, get_store(), now=MONDAY.replace(hour=5))
    assert early["due"] is False and early["sent"] == 0 and not outbox.sent
    forced = send_digests(session, get_store(), now=MONDAY.replace(hour=5), force=True)
    assert forced["sent"] == 1


def test_dry_run_sends_and_records_nothing(client, session, outbox):
    referent(client, session)
    result = send_digests(session, get_store(), now=MONDAY, dry_run=True)
    assert result["sent"] == 1 and not outbox.sent
    assert session.exec(select(NotificationLog)).all() == []


def test_failures_are_recorded_and_retried(client, session, monkeypatch):
    referent(client, session)
    broken = FakeMailer(fail=True)
    monkeypatch.setattr(digest_service, "get_mailer", lambda: broken)
    assert send_digests(session, get_store(), now=MONDAY)["failed"] == 1
    row = session.exec(select(NotificationLog)).one()
    assert row.status == "failed" and "smtp down" in row.error

    working = FakeMailer()
    monkeypatch.setattr(digest_service, "get_mailer", lambda: working)
    assert send_digests(session, get_store(), now=MONDAY + timedelta(hours=1))["sent"] == 1
    session.refresh(row)
    assert row.status == "sent" and row.error is None


def test_unreachable_server_stops_the_run(client, session, monkeypatch):
    referent(client, session, "ref-a")
    referent(client, session, "ref-b")

    def cannot_connect():
        raise ConnectionRefusedError("no route to smtp")

    monkeypatch.setattr(digest_service, "get_mailer", cannot_connect)
    result = send_digests(session, get_store(), now=MONDAY)
    assert (result["failed"], result["deferred"]) == (1, 1)


def test_claims_left_by_a_dead_run(client, session, outbox):
    user = referent(client, session)
    stuck = NotificationLog(user_id=user.id, kind="digest_hebdo", period="2026-W48", status="sending",
                            recipient="ref@example.fr", updated_at=MONDAY - timedelta(minutes=5))
    session.add(stuck)
    session.commit()
    assert send_digests(session, get_store(), now=MONDAY)["already_sent"] == 1  # maybe still running
    assert send_digests(session, get_store(), now=MONDAY + timedelta(minutes=30))["sent"] == 1


def test_time_budget_defers_the_rest(client, session, outbox, monkeypatch):
    referent(client, session, "ref-a")
    referent(client, session, "ref-b")
    monkeypatch.setattr(settings, "digest_time_budget_seconds", -1)
    result = send_digests(session, get_store(), now=MONDAY)
    assert result["deferred"] == 2 and not outbox.sent  # the two referents (test-admin is not opted in)


# ---- scheduler endpoint ---------------------------------------------------------------------
def test_scheduler_endpoint_needs_the_secret(client, monkeypatch, outbox):
    url = f"{API}/internal/digest"
    client.headers.pop("Authorization")
    monkeypatch.setattr(settings, "cron_secret", None)
    assert client.get(url).status_code == 503
    monkeypatch.setattr(settings, "cron_secret", "s3cret-value")
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get(url, headers={"Authorization": "Bearer s3cret-value"})
    assert response.status_code == 200 and "period" in response.json()
    assert client.post(url, headers={"Authorization": "Bearer s3cret-value"}).status_code == 200


# ---- account settings ---------------------------------------------------------------------
def test_users_manage_their_own_alerts(client):
    create_account(client, "self-ref", "referent", [SCHOOL])
    create_account(client, "self-reader", "lecteur")
    login(client, "self-ref")
    me = client.patch(f"{API}/auth/me", json={"email": "moi@example.fr", "alertes_email": True}).json()
    assert (me["email"], me["alertes_email"]) == ("moi@example.fr", True)
    assert client.patch(f"{API}/auth/me", json={"email": "pas une adresse"}).status_code == 422
    assert client.patch(f"{API}/auth/me", json={"email": None}).json()["alertes_email"] is False

    login(client, "self-reader")
    refused = client.patch(f"{API}/auth/me", json={"email": "r@example.fr", "alertes_email": True})
    assert refused.status_code == 422 and "réservées" in refused.json()["detail"]


def test_alerts_need_an_address(client):
    create_account(client, "no-mail-ref", "referent", [SCHOOL])
    login(client, "no-mail-ref")
    assert client.patch(f"{API}/auth/me", json={"alertes_email": True}).status_code == 422


# ---- SMTP ----------------------------------------------------------------------------------
def test_smtp_mailer_uses_one_connection_with_starttls(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def starttls(self, context):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, message):
            calls.append(("send", message["To"], message["Subject"], message.get_content_type()))

        def quit(self):
            calls.append(("quit",))

    monkeypatch.setattr(mailer_service.smtplib, "SMTP", FakeSMTP)
    for name, value in (("mail_backend", "smtp"), ("smtp_host", "smtp.example.fr"),
                        ("smtp_username", "bot"), ("smtp_password", "pw")):
        monkeypatch.setattr(settings, name, value)
    with mailer_service.get_mailer() as mailer:
        for to in ("a@example.fr", "b@example.fr"):
            mailer.send(mailer_service.Email(to=to, subject="Objet", text="corps", html="<p>corps</p>"))
    assert calls == [
        ("connect", "smtp.example.fr", 587), ("starttls",), ("login", "bot"),
        ("send", "a@example.fr", "Objet", "multipart/alternative"),
        ("send", "b@example.fr", "Objet", "multipart/alternative"),
        ("quit",),
    ]


def test_smtp_needs_a_host(monkeypatch):
    monkeypatch.setattr(settings, "mail_backend", "smtp")
    monkeypatch.setattr(settings, "smtp_host", None)
    with pytest.raises(RuntimeError, match="SRT_SMTP_HOST"):
        mailer_service.get_mailer()
