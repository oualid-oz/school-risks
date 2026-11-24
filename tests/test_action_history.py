"""Sprint 15: action timeline, comments and assignment."""
from datetime import date, timedelta

from app.db import get_session
from app.services import action_history
from tests.test_security import create_account, login

API = "/api/v1"
SCHOOL, OTHER = "0450003J", "0450004K"


def new_action(client, **extra):
    body = {"uai": SCHOOL, "titre": "Vérifier les kits", **extra}
    response = client.post(f"{API}/actions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_creation_and_changes_are_recorded_with_old_and_new_values(client):
    action = new_action(client)
    client.patch(f"{API}/actions/{action['id']}", json={
        "statut": "en_cours", "priorite": "haute", "echeance": "2026-12-15", "titre": "Vérifier les kits",
    }).raise_for_status()
    client.patch(f"{API}/actions/{action['id']}", json={"statut": "en_cours"}).raise_for_status()  # no-op

    events = client.get(f"{API}/actions/{action['id']}/historique").json()
    assert [e["kind"] for e in events] == ["modification", "creation"]  # newest first, no-op ignored
    changes = {c["champ"]: (c["avant"], c["apres"]) for c in events[0]["changes"]}
    assert changes == {"statut": ("a_faire", "en_cours"), "priorite": ("normale", "haute"),
                       "echeance": (None, "2026-12-15")}
    assert events[1]["author_username"] == "test-admin"


def test_comments_explain_why_an_action_is_late(client):
    action = new_action(client, echeance=str(date.today() - timedelta(days=3)))
    response = client.post(f"{API}/actions/{action['id']}/commentaires",
                           json={"texte": "  Fournisseur en rupture, livraison le 10/12.  "})
    assert response.status_code == 201
    assert response.json()["texte"] == "Fournisseur en rupture, livraison le 10/12."
    assert response.json()["deletable"] is True

    listed = client.get(f"{API}/actions", params={"uai": SCHOOL}).json()[0]
    assert listed["en_retard"] is True
    assert listed["dernier_commentaire"]["texte"] == "Fournisseur en rupture, livraison le 10/12."
    assert client.post(f"{API}/actions/{action['id']}/commentaires", json={"texte": "   "}).status_code == 422
    assert client.post(f"{API}/actions/{action['id']}/commentaires", json={"texte": ""}).status_code == 422


def test_who_may_read_comment_and_delete(client):
    action = new_action(client)
    admin_comment = client.post(f"{API}/actions/{action['id']}/commentaires", json={"texte": "Admin"}).json()
    create_account(client, "hist-ref", "referent", [SCHOOL])
    create_account(client, "hist-other-ref", "referent", [OTHER])
    create_account(client, "hist-reader", "lecteur")

    login(client, "hist-reader")  # reads, cannot comment
    assert client.get(f"{API}/actions/{action['id']}/historique").status_code == 200
    assert client.post(f"{API}/actions/{action['id']}/commentaires", json={"texte": "x"}).status_code == 403

    login(client, "hist-other-ref")  # another school: neither reads nor comments
    assert client.get(f"{API}/actions/{action['id']}/historique").status_code == 403
    assert client.post(f"{API}/actions/{action['id']}/commentaires", json={"texte": "x"}).status_code == 403

    login(client, "hist-ref")
    own = client.post(f"{API}/actions/{action['id']}/commentaires", json={"texte": "Référent"}).json()
    events = {e["id"]: e for e in client.get(f"{API}/actions/{action['id']}/historique").json()}
    assert events[own["id"]]["deletable"] and not events[admin_comment["id"]]["deletable"]
    url = f"{API}/actions/{action['id']}/commentaires"
    assert client.delete(f"{url}/{admin_comment['id']}").status_code == 403  # not theirs
    assert client.delete(f"{url}/{own['id']}").status_code == 204
    assert client.delete(f"{url}/{own['id']}").status_code == 404


def test_comment_deletion_is_audited_with_its_text(client):
    action = new_action(client)
    comment = client.post(f"{API}/actions/{action['id']}/commentaires", json={"texte": "À retirer"}).json()
    client.delete(f"{API}/actions/{action['id']}/commentaires/{comment['id']}").raise_for_status()
    audit = next(r for r in client.get(f"{API}/auth/audit").json() if r["event"] == "action.comment_deleted")
    assert "À retirer" in audit["details"]


def test_deleting_an_action_deletes_its_history(client, monkeypatch):
    action = new_action(client)
    client.post(f"{API}/actions/{action['id']}/commentaires", json={"texte": "x"}).raise_for_status()
    client.delete(f"{API}/actions/{action['id']}").raise_for_status()
    assert client.get(f"{API}/actions/{action['id']}/historique").status_code == 404


def test_assignment_rules_and_email(client, monkeypatch):
    calls = []
    monkeypatch.setattr(action_history, "notify_assignment", lambda *args: calls.append(args))
    ref = create_account(client, "assignee", "referent", [SCHOOL])
    other = create_account(client, "not-here", "referent", [OTHER])
    reader = create_account(client, "a-reader", "lecteur")

    people = {p["username"] for p in client.get(f"{API}/actions/assignables", params={"uai": SCHOOL}).json()}
    assert {"test-admin", "assignee"} <= people  # admins and the school's referents
    assert not people & {"not-here", "a-reader"}

    for refused in (other["id"], reader["id"], 99999):
        body = {"uai": SCHOOL, "titre": "Refus", "responsable_user_id": refused}
        assert client.post(f"{API}/actions", json=body).status_code == 422

    action = new_action(client, responsable_user_id=ref["id"])
    assert action["responsable_username"] == "assignee"
    assert calls == [(action["id"], ref["id"], "test-admin")]  # email sent after the response
    client.patch(f"{API}/actions/{action['id']}", json={"priorite": "haute"}).raise_for_status()
    assert len(calls) == 1  # not re-sent when something else changes

    client.patch(f"{API}/actions/{action['id']}", json={"responsable_user_id": None}).raise_for_status()
    change = client.get(f"{API}/actions/{action['id']}/historique").json()[0]["changes"]
    assert change == [{"champ": "assigne", "avant": "assignee", "apres": None}]


def test_assignment_email_content(client, monkeypatch):
    from app.models.security import User
    from app.services import mailer as mailer_service
    from tests.test_digest import FakeMailer

    box = FakeMailer()
    monkeypatch.setattr(mailer_service, "get_mailer", lambda: box)
    ref = create_account(client, "mail-ref", "referent", [SCHOOL])
    session = next(client.app.dependency_overrides[get_session]())
    user = session.get(User, ref["id"])
    user.email, user.alertes_email = "mail-ref@example.fr", True
    session.add(user)
    session.commit()

    new_action(client, responsable_user_id=ref["id"], echeance="2026-12-20")  # background task runs
    assert len(box.sent) == 1
    email = box.sent[0]
    assert email.to == "mail-ref@example.fr" and "Action assignée : Vérifier les kits" in email.subject
    assert "Jean Moulin (0450003J)" in email.text and "20/12/2026" in email.text

    user.alertes_email = False  # opted out: no email
    session.add(user)
    session.commit()
    new_action(client, responsable_user_id=ref["id"])
    assert len(box.sent) == 1


def test_last_comment_reaches_the_digest_and_the_pdf(client):
    from datetime import UTC, datetime

    from app.data.store import get_store
    from app.services.digest import collect

    action = new_action(client, echeance=str(date.today() - timedelta(days=2)))
    client.post(f"{API}/actions/{action['id']}/commentaires",
                json={"texte": "Devis signé, pose prévue lundi."}).raise_for_status()
    session = next(client.app.dependency_overrides[get_session]())
    digest = collect(session, get_store(), [SCHOOL], datetime.now(UTC).date())
    assert "dernier commentaire de test-admin" in digest.overdue[0].text
    assert "Devis signé, pose prévue lundi." in digest.overdue[0].text

    pdf = client.get(f"{API}/colleges/{SCHOOL}/fiche.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
