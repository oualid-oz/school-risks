"""Sprint 9: PPMS tracking."""
from datetime import date, timedelta

import pytest

from app.core.config import settings
from app.models.ppms import TypeExercice
from app.services.ppms import add_months, compute_status, school_year_start
from tests.test_security import create_account, login

API = "/api/v1"
SCHOOL = "0450003J"
RM, INTRUSION, INCENDIE = TypeExercice.risques_majeurs, TypeExercice.intrusion, TypeExercice.incendie


# ---- rules (fixed dates) ------------------------------------------------------------
def test_dates_helpers():
    assert school_year_start(date(2026, 11, 23)) == date(2026, 9, 1)
    assert school_year_start(date(2027, 3, 1)) == date(2026, 9, 1)
    assert school_year_start(date(2026, 9, 1)) == date(2026, 9, 1)
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2025, 11, 30), 12) == date(2026, 11, 30)


def test_no_plan_is_absent():
    status = compute_status(None, [], date(2027, 1, 10))
    assert status.statut == "absent" and status.raisons == ["PPMS non renseigné"]
    assert status.annee_scolaire == "2026-2027"


def test_recent_plan_and_a_drill_is_up_to_date():
    status = compute_status(date(2026, 10, 1), [(INTRUSION, date(2026, 10, 15))], date(2027, 1, 10))
    assert status.statut == "a_jour" and status.raisons == []
    assert status.prochaine_mise_a_jour == date(2027, 10, 1)
    assert status.exercices_annee[INTRUSION] == date(2026, 10, 15)
    assert status.exercices_annee[RM] is None


def test_plan_older_than_the_limit_is_outdated():
    status = compute_status(date(2025, 12, 1), [(RM, date(2026, 10, 1))], date(2027, 1, 10))
    assert status.statut == "ancien"
    assert status.raisons == ["PPMS non mis à jour depuis le 01/12/2025"]


def test_missing_drill_only_counts_after_the_grace_period():
    plan = date(2026, 6, 1)
    early = compute_status(plan, [], date(2026, 10, 15))  # 44 days into the school year
    late = compute_status(plan, [], date(2027, 1, 10))  # 131 days
    assert early.statut == "a_jour"
    assert late.statut == "ancien"
    assert late.raisons == ["Aucun exercice PPMS (risques majeurs ou intrusion) cette année scolaire"]


def test_fire_drills_and_last_years_drills_do_not_count():
    plan, today = date(2026, 6, 1), date(2027, 1, 10)
    drills = [(INCENDIE, date(2026, 10, 1)), (RM, date(2026, 5, 20))]  # fire; previous school year
    status = compute_status(plan, drills, today)
    assert status.statut == "ancien"
    assert status.exercices_annee[INCENDIE] == date(2026, 10, 1)
    assert status.exercices_annee[RM] is None


def test_rules_are_configurable(monkeypatch):
    monkeypatch.setattr(settings, "ppms_exercices_comptes", [])  # drills not required
    monkeypatch.setattr(settings, "ppms_max_age_months", 24)
    status = compute_status(date(2025, 6, 1), [], date(2027, 1, 10))
    assert status.statut == "a_jour"


# ---- API ---------------------------------------------------------------------------
def _today():
    return str(date.today())


def test_admin_records_plan_and_drill_and_status_follows(client):
    detail = client.get(f"{API}/colleges/{SCHOOL}/ppms").json()
    assert detail["statut"]["statut"] == "absent"
    assert "PPMS non renseigné" in client.get(f"{API}/colleges/{SCHOOL}/risques").json()["alertes"]

    rev = client.post(f"{API}/colleges/{SCHOOL}/ppms/revisions", json={"date_mise_a_jour": _today()})
    ex = client.post(
        f"{API}/colleges/{SCHOOL}/ppms/exercices",
        json={"type_exercice": "intrusion", "date_exercice": _today(), "commentaire": "Confinement"},
    )
    assert rev.status_code == 201 and ex.status_code == 201
    assert rev.json()["created_by_username"] == "test-admin"

    detail = client.get(f"{API}/colleges/{SCHOOL}/ppms").json()
    assert detail["statut"]["statut"] == "a_jour"
    assert len(detail["revisions"]) == 1 and detail["exercices"][0]["commentaire"] == "Confinement"
    alerts = client.get(f"{API}/colleges/{SCHOOL}").json()["profil_risque"]["alertes"]
    assert not any("PPMS" in a for a in alerts)

    events = {r["event"] for r in client.get(f"{API}/auth/audit").json()}
    assert {"ppms.revision_created", "ppms.exercice_created"} <= events


def test_bad_input_is_rejected(client):
    tomorrow = str(date.today() + timedelta(days=1))
    url = f"{API}/colleges/{SCHOOL}/ppms"
    assert client.post(f"{url}/revisions", json={"date_mise_a_jour": tomorrow}).status_code == 422
    bad_type = {"type_exercice": "evacuation", "date_exercice": _today()}
    assert client.post(f"{url}/exercices", json=bad_type).status_code == 422
    unknown = {"date_mise_a_jour": _today()}
    assert client.post(f"{API}/colleges/0000000X/ppms/revisions", json=unknown).status_code == 404


def test_permissions_follow_actions(client):
    other = next(c["uai"] for c in client.get(f"{API}/colleges").json() if c["uai"] != SCHOOL)
    create_account(client, "ppms-referent", "referent", [SCHOOL])
    create_account(client, "ppms-reader", "lecteur")
    body = {"date_mise_a_jour": _today()}

    login(client, "ppms-reader")
    assert client.get(f"{API}/colleges/{SCHOOL}/ppms").status_code == 200
    assert client.post(f"{API}/colleges/{SCHOOL}/ppms/revisions", json=body).status_code == 403

    login(client, "ppms-referent")
    own = client.post(f"{API}/colleges/{SCHOOL}/ppms/revisions", json=body)
    assert own.status_code == 201
    assert client.post(f"{API}/colleges/{other}/ppms/revisions", json=body).status_code == 403
    entry = own.json()["id"]
    assert client.delete(f"{API}/colleges/{other}/ppms/revisions/{entry}").status_code == 403
    assert client.delete(f"{API}/colleges/{SCHOOL}/ppms/revisions/{entry}").status_code == 204
    assert client.delete(f"{API}/colleges/{SCHOOL}/ppms/revisions/{entry}").status_code == 404


def test_list_filter_and_stats(client):
    client.post(f"{API}/colleges/{SCHOOL}/ppms/revisions", json={"date_mise_a_jour": _today()})
    client.post(f"{API}/colleges/{SCHOOL}/ppms/exercices",
                json={"type_exercice": "risques_majeurs", "date_exercice": _today()})
    rows = client.get(f"{API}/colleges").json()
    assert {r["uai"]: r["ppms_statut"] for r in rows}[SCHOOL] == "a_jour"
    up_to_date = client.get(f"{API}/colleges", params={"ppms": "a_jour"}).json()
    absent = client.get(f"{API}/colleges", params={"ppms": "absent"}).json()
    assert [r["uai"] for r in up_to_date] == [SCHOOL]
    assert len(absent) == len(rows) - 1
    assert client.get(f"{API}/stats").json()["ppms_par_statut"] == {
        "a_jour": 1, "ancien": 0, "absent": len(rows) - 1,
    }


@pytest.mark.parametrize("kind", ["revisions", "exercices"])
def test_entry_of_another_school_cannot_be_deleted_through_this_one(client, kind):
    body = (
        {"date_mise_a_jour": _today()} if kind == "revisions"
        else {"type_exercice": "intrusion", "date_exercice": _today()}
    )
    entry = client.post(f"{API}/colleges/{SCHOOL}/ppms/{kind}", json=body).json()["id"]
    other = next(c["uai"] for c in client.get(f"{API}/colleges").json() if c["uai"] != SCHOOL)
    assert client.delete(f"{API}/colleges/{other}/ppms/{kind}/{entry}").status_code == 404
