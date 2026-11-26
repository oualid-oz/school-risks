"""Sprint 22: data quality per department, data error reports, retention of personal data."""
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

import app.db as app_db
from app.models.notifications import NotificationLog
from app.models.security import AuditLog
from app.services.retention import purge
from tests.test_scopes import API, LOIRET, SAVOIE, _as, _create, national, savoie_admin  # noqa: F401


def test_quality_page_shows_coverage_per_department(national):  # noqa: F811
    body = national.get(f"{API}/qualite").json()
    deps = {d["code"]: d for d in body["departements"]}
    assert set(deps) == {"17", "45", "74"}

    loiret, charente = deps["45"]["couverture"], deps["17"]["couverture"]
    assert loiret["tmd"] == {"libelle": "Transport de matières dangereuses", "renseignes": 70,
                             "total": 70, "taux": 100}
    assert charente["tmd"]["taux"] == 0  # no source outside Loiret
    assert loiret["secteur"]["total"] == 58  # public collèges only
    assert charente["secteur"]["renseignes"] == 1  # the school map lacks Charente-Maritime
    assert deps["74"]["secteurs"]["par_source"] == {"carte_scolaire": 24, "carte_scolaire_approchee": 28}

    gaps = body["colleges_incomplets"]
    # Loiret: complete but for 4 schools outside the clay map (central Orléans).
    loiret_gaps = [s for s in gaps if s["departement"] == "45"]
    assert len(loiret_gaps) == 4 and all(s["manquants"] == ["Carte des argiles"] for s in loiret_gaps)
    assert "Transport de matières dangereuses" in gaps[0]["manquants"]
    only_17 = national.get(f"{API}/qualite", params={"departement": "17"}).json()["colleges_incomplets"]
    assert len(only_17) == 61 and {s["departement"] for s in only_17} == {"17"}


def test_quality_follows_the_area(national, savoie_admin):  # noqa: F811
    body = national.get(f"{API}/qualite", headers=savoie_admin).json()
    assert [d["code"] for d in body["departements"]] == ["74"]
    outside = national.get(f"{API}/qualite", headers=savoie_admin, params={"departement": "45"})
    assert outside.status_code == 403


def test_data_error_reports_from_reader_to_area_admin(national, savoie_admin):  # noqa: F811
    _create(national, "lecteur-74", "lecteur", ["74"]).raise_for_status()
    reader = _as(national, "lecteur-74")
    url = f"{API}/colleges/{SAVOIE}/signalements"
    created = national.post(url, headers=reader,
                            json={"champ": "position", "message": "Le collège a déménagé en 2025."})
    assert created.status_code == 201
    report = created.json()
    assert report["statut"] == "ouvert" and report["departement"] == "74" and report["nom"]
    assert national.post(f"{API}/colleges/{LOIRET}/signalements", headers=reader,
                         json={"message": "hors périmètre"}).status_code == 404
    assert national.post(url, headers=reader, json={"message": "x"}).status_code == 422  # too short

    # The Loiret report of the national admin is not the Savoie admin's business.
    national.post(f"{API}/colleges/{LOIRET}/signalements",
                  json={"message": "Effectif faux"}).raise_for_status()
    listed = national.get(f"{API}/signalements", headers=savoie_admin)
    assert [r["uai"] for r in listed.json()] == [SAVOIE] and listed.headers["X-Total-Count"] == "1"
    assert len(national.get(f"{API}/signalements").json()) == 2
    assert [r["uai"] for r in national.get(f"{API}/signalements", headers=reader).json()] == [SAVOIE]
    quality = national.get(f"{API}/qualite", headers=savoie_admin).json()
    assert quality["departements"][0]["signalements_ouverts"] == 1

    # Readers cannot close; the area's admin can, with an answer the reader then sees.
    path = f"{API}/signalements/{report['id']}"
    assert national.patch(path, headers=reader, json={"statut": "traite"}).status_code == 403
    loiret_report = next(r for r in national.get(f"{API}/signalements").json() if r["uai"] == LOIRET)
    assert national.patch(f"{API}/signalements/{loiret_report['id']}", headers=savoie_admin,
                          json={"statut": "rejete"}).status_code == 404
    closed = national.patch(path, headers=savoie_admin,
                            json={"statut": "traite", "reponse": "Position corrigée à la source."}).json()
    assert closed["statut"] == "traite" and closed["closed_by_username"] == "admin-74"
    mine = national.get(f"{API}/signalements", headers=reader).json()[0]
    assert mine["reponse"] == "Position corrigée à la source."
    assert national.get(f"{API}/signalements", headers=savoie_admin,
                        params={"statut": "ouvert"}).json() == []
    events = [e["event"] for e in national.get(f"{API}/auth/audit", headers=savoie_admin).json()]
    assert {"signalement.created", "signalement.updated"} <= set(events)


def test_personal_data_past_retention_is_purged(client):
    now = datetime.now(UTC)
    with Session(app_db.engine) as session:
        admin_id = session.exec(select(AuditLog.actor_user_id)).first()
        for days in (10, 400):
            session.add(AuditLog(actor_username="x", event="auth.login_succeeded", resource_type="user",
                                 resource_id="1", remote_addr="10.0.0.1",
                                 created_at=now - timedelta(days=days)))
        session.add(NotificationLog(user_id=admin_id or 1, kind="digest_hebdo", period="2025-W01",
                                    status="sent", recipient="a@example.org",
                                    created_at=now - timedelta(days=200)))
        session.commit()
        assert purge(session, now) == {"audit_log": 1, "notification_log": 1}
        assert purge(session, now) == {"audit_log": 0, "notification_log": 0}  # idempotent
        assert len(session.exec(select(AuditLog).where(AuditLog.actor_username == "x")).all()) == 1
