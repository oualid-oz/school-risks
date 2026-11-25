"""Sprint 21: department and academy administrators, area readers, referents' reach.

Runs on the 3-department pilot data (45 Loiret, 17 Charente-Maritime, 74 Haute-Savoie)."""
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session, select

import app.db as app_db
from app.core.config import settings
from app.core.security import create_access_token
from app.data.store import get_store
from app.models.security import User

API = "/api/v1"
PILOT = Path(__file__).parent / "e2e" / "fixtures" / "pilot"
LOIRET, SAVOIE = "0450003J", "0740001Z"
PASSWORD = "a-long-enough-password"


@pytest.fixture
def national(client, monkeypatch):
    """The client of the fixture is a national admin; the data has three departments."""
    monkeypatch.setattr(settings, "data_dir", PILOT)
    get_store.cache_clear()
    yield client
    get_store.cache_clear()


def _as(client, username: str) -> dict[str, str]:
    with Session(app_db.engine) as session:
        user = session.exec(select(User).where(User.username == username)).one()
        return {"Authorization": f"Bearer {create_access_token(user)}"}


def _create(client, username: str, role: str, departements=(), schools=(), as_=None):
    return client.post(f"{API}/auth/users", headers=as_, json={
        "username": username, "password": PASSWORD, "role": role,
        "departements": list(departements), "school_uais": list(schools),
    })


@pytest.fixture
def savoie_admin(national):
    _create(national, "admin-74", "admin", ["74"]).raise_for_status()
    return _as(national, "admin-74")


def test_department_admin_sees_only_their_department(national, savoie_admin):
    h = savoie_admin
    schools = national.get(f"{API}/colleges", headers=h)
    assert schools.headers["X-Total-Count"] == "75"
    assert {s["departement"] for s in schools.json()} == {"74"}
    assert national.get(f"{API}/colleges", headers=h, params={"departement": "45"}).status_code == 403
    for path in (f"/colleges/{LOIRET}", f"/colleges/{LOIRET}/historique", f"/colleges/{LOIRET}/fiche.pdf",
                 f"/ppms?uai={LOIRET}"):
        assert national.get(f"{API}{path}", headers=h).status_code == 404, path
    assert national.get(f"{API}/colleges/{SAVOIE}", headers=h).status_code == 200

    assert [t["code"] for t in national.get(f"{API}/territoires", headers=h).json()] == ["74"]
    assert national.get(f"{API}/stats", headers=h).json()["nb_colleges"] == 75
    sectors = national.get(f"{API}/secteurs", headers=h).json()["features"]
    assert sectors and all("74" in f["properties"]["departements"] for f in sectors)
    assert national.get(f"{API}/secteurs/{LOIRET}", headers=h).status_code == 404

    csv = national.get(f"{API}/exports/colleges.csv", headers=h).content.decode("utf-8-sig")
    assert len(csv.strip().splitlines()) == 1 + 75
    simulation = national.post(f"{API}/scores/simulation", headers=h, json={}).json()
    assert len(simulation["colleges"]) == 75

    # The national admin still sees everything.
    assert national.get(f"{API}/colleges").headers["X-Total-Count"] == "206"


def test_department_admin_manages_actions_of_their_department_only(national, savoie_admin):
    h = savoie_admin
    loiret_action = national.post(f"{API}/actions", json={"uai": LOIRET, "titre": "Exercice Loiret"}).json()
    assert national.post(f"{API}/actions", headers=h, json={"uai": LOIRET, "titre": "Non"}).status_code == 403
    mine = national.post(f"{API}/actions", headers=h, json={"uai": SAVOIE, "titre": "Plan avalanche"})
    assert mine.status_code == 201

    listed = national.get(f"{API}/actions", headers=h)
    assert [a["uai"] for a in listed.json()] == [SAVOIE] and listed.headers["X-Total-Count"] == "1"
    assert national.get(f"{API}/actions/{loiret_action['id']}", headers=h).status_code == 404
    assert national.patch(f"{API}/actions/{loiret_action['id']}", headers=h,
                          json={"statut": "termine"}).status_code == 403
    assert national.get(f"{API}/actions", headers=h, params={"uai": LOIRET}).status_code == 404

    # Assignable people: the area's admin and the national one, for a Savoie school only.
    names = {u["username"] for u in national.get(f"{API}/actions/assignables", params={"uai": SAVOIE}).json()}
    assert {"test-admin", "admin-74"} <= names  # (plus any bootstrap admin from .env)
    names = {u["username"] for u in national.get(f"{API}/actions/assignables", params={"uai": LOIRET}).json()}
    assert "test-admin" in names and "admin-74" not in names
    with Session(app_db.engine) as session:
        admin_74 = session.exec(select(User).where(User.username == "admin-74")).one().id
    assert national.patch(f"{API}/actions/{loiret_action['id']}",
                          json={"responsable_user_id": admin_74}).status_code == 422


def test_department_admin_manages_users_inside_their_area_only(national, savoie_admin):
    h = savoie_admin
    assert _create(national, "lecteur-74", "lecteur", ["74"], as_=h).status_code == 201
    assert _create(national, "referent-74", "referent", schools=[SAVOIE], as_=h).status_code == 201
    for username, role, deps, schools in (
        ("lecteur-national", "lecteur", [], []),  # all of France: national admins only
        ("lecteur-45", "lecteur", ["45"], []),
        ("lecteur-mixte", "lecteur", ["45", "74"], []),
        ("referent-45", "referent", [], [LOIRET]),
        ("admin-national", "admin", [], []),
    ):
        assert _create(national, username, role, deps, schools, as_=h).status_code == 403, username
    assert _create(national, "referent-zone", "referent", ["74"], [SAVOIE], as_=h).status_code == 422
    assert _create(national, "lecteur-99", "lecteur", ["99"], as_=h).status_code == 422  # no school there

    users = {u["username"]: u for u in national.get(f"{API}/auth/users", headers=h).json()}
    assert set(users) == {"admin-74", "lecteur-74", "referent-74"}
    assert users["lecteur-74"]["departements"] == ["74"]
    everyone = {u["username"] for u in national.get(f"{API}/auth/users").json()}
    assert {"test-admin", "admin-74"} <= everyone

    all_users = national.get(f"{API}/auth/users").json()
    national_id = next(u["id"] for u in all_users if u["username"] == "test-admin")
    assert national.patch(f"{API}/auth/users/{national_id}", headers=h,
                          json={"active": False}).status_code == 403
    reader = users["lecteur-74"]["id"]
    assert national.patch(f"{API}/auth/users/{reader}", headers=h,
                          json={"departements": []}).status_code == 403
    assert national.patch(f"{API}/auth/users/{reader}", headers=h,
                          json={"departements": ["45"]}).status_code == 403
    referent = users["referent-74"]["id"]
    assert national.patch(f"{API}/auth/users/{referent}", headers=h,
                          json={"school_uais": [SAVOIE, LOIRET]}).status_code == 403
    # Nobody widens (or changes) their own area.
    me = users["admin-74"]["id"]
    assert national.patch(f"{API}/auth/users/{me}", headers=h, json={"departements": []}).status_code == 422
    assert national.patch(f"{API}/auth/users/{me}", headers=h,
                          json={"departements": ["45", "74"]}).status_code == 422


def test_academy_admin_reader_and_referent_reach(national):
    _create(national, "admin-academie", "admin", ["17", "74"]).raise_for_status()
    _create(national, "lecteur-17", "lecteur", ["17"]).raise_for_status()
    _create(national, "referent-loiret", "referent", schools=[LOIRET]).raise_for_status()

    academy = _as(national, "admin-academie")
    codes = [t["code"] for t in national.get(f"{API}/territoires", headers=academy).json()]
    assert codes == ["17", "74"]
    assert national.get(f"{API}/colleges", headers=academy).headers["X-Total-Count"] == str(61 + 75)

    reader = _as(national, "lecteur-17")
    assert national.get(f"{API}/stats", headers=reader).json()["nb_colleges"] == 61
    assert national.post(f"{API}/actions", headers=reader,
                         json={"uai": SAVOIE, "titre": "x"}).status_code == 403
    assert national.get(f"{API}/colleges/{SAVOIE}", headers=reader).status_code == 404

    # A referent sees the department of their schools, and manages only those schools.
    referent = _as(national, "referent-loiret")
    assert [t["code"] for t in national.get(f"{API}/territoires", headers=referent).json()] == ["45"]
    other_loiret = "0450006M"
    assert national.get(f"{API}/colleges/{other_loiret}", headers=referent).status_code == 200
    assert national.post(f"{API}/actions", headers=referent,
                         json={"uai": other_loiret, "titre": "Non"}).status_code == 403
    assert national.post(f"{API}/actions", headers=referent,
                         json={"uai": LOIRET, "titre": "Oui"}).status_code == 201


def test_audit_log_and_digest_follow_the_area(national, savoie_admin):
    from app.services.digest import digest_for

    national.post(f"{API}/actions", json={"uai": LOIRET, "titre": "Loiret", "echeance": str(date.today())})
    national.post(f"{API}/actions", json={"uai": SAVOIE, "titre": "Savoie", "echeance": str(date.today())})
    _create(national, "lecteur-45", "lecteur", ["45"]).raise_for_status()

    events = national.get(f"{API}/auth/audit", headers=savoie_admin).json()
    created = [e for e in events if e["event"] == "action.created"]
    assert [e["departement"] for e in created] == ["74"]  # not the Loiret one
    assert not any(e["event"] == "user.created" and '"45"' in (e["details"] or "") for e in events)
    everything = national.get(f"{API}/auth/audit").json()
    assert {e["departement"] for e in everything if e["event"] == "action.created"} == {"45", "74"}

    with Session(app_db.engine) as session:
        admin = session.exec(select(User).where(User.username == "admin-74")).one()
        email = digest_for(session, get_store(), admin, date.today())
    assert email is not None and "Savoie" in email.text and "Loiret" not in email.text
    assert "votre périmètre (74)" in email.text
