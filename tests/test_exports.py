"""Sprint 11: CSV and PDF exports."""
import csv
import io
from datetime import date, timedelta

from tests.test_security import create_account, login

API = "/api/v1"
SCHOOL = "0450003J"


def read_csv(response):
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "no-store"
    raw = response.content
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM: French Excel reads UTF-8 correctly
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))


def other_school(client):
    return next(c["uai"] for c in client.get(f"{API}/colleges").json() if c["uai"] != SCHOOL)


def test_colleges_csv_opens_in_french_excel(client):
    response = client.get(f"{API}/exports/colleges.csv", params={"tri": "priorite"})
    assert 'filename="colleges_' in response.headers["content-disposition"]
    rows = read_csv(response)
    header, body = rows[0], rows[1:]
    assert header[:3] == ["UAI", "Nom", "Commune"] and "Score inondation" in header and "PPMS" in header
    assert len(body) == 70
    api_order = [c["uai"] for c in client.get(f"{API}/colleges", params={"tri": "priorite"}).json()]
    assert [r[0] for r in body] == api_order  # same order as the API
    first = dict(zip(header, body[0], strict=True))
    assert "," in first["IPS"] and "." not in first["IPS"]  # decimal comma
    assert first["Niveau"] in {"Faible", "Modéré", "Élevé"} and first["PPMS"] == "Non renseigné"
    assert response.content.count(b"\r\n") == 71


def test_colleges_csv_filters_and_is_audited(client):
    rows = read_csv(client.get(f"{API}/exports/colleges.csv", params={"niveau": "eleve"}))
    level = rows[0].index("Niveau")  # look columns up by name: the schema grows
    assert {r[level] for r in rows[1:]} == {"Élevé"}
    audit = client.get(f"{API}/auth/audit").json()
    export = next(r for r in audit if r["event"] == "export.colleges_csv")
    assert '"niveau": "eleve"' in export["details"]


def test_actions_csv_neutralises_formulas(client):
    client.post(f"{API}/actions", json={
        "uai": SCHOOL, "titre": "=HYPERLINK(\"http://x\",\"clic\")", "description": "+33 6 12",
        "echeance": str(date.today() - timedelta(days=2)),
    }).raise_for_status()
    rows = read_csv(client.get(f"{API}/exports/actions.csv", params={"uai": SCHOOL}))
    row = dict(zip(rows[0], rows[1], strict=True))
    assert row["Titre"].startswith("'=") and row["Description"] == "'+33 6 12"
    assert row["Collège"] == "Jean Moulin" and row["En retard"] == "oui"
    assert row["Échéance"] == (date.today() - timedelta(days=2)).strftime("%d/%m/%Y")


def test_fiche_pdf(client):
    response = client.get(f"{API}/colleges/{SCHOOL}/fiche.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-") and len(response.content) > 5000
    assert f'fiche_risques_{SCHOOL}_' in response.headers["content-disposition"]
    assert client.get(f"{API}/colleges/0000000X/fiche.pdf").status_code == 404
    events = [r for r in client.get(f"{API}/auth/audit").json() if r["event"] == "export.fiche_pdf"]
    assert events and events[0]["resource_id"] == SCHOOL


def test_fiche_handles_any_text_typed_by_users(client):
    client.post(f"{API}/actions", json={
        "uai": SCHOOL, "titre": "Plan d’évacuation — cœur « test » 漢字 🚸 " + "très long " * 12,
        "echeance": str(date.today()),
    }).raise_for_status()
    for _ in range(30):  # more actions than the fiche lists
        client.post(f"{API}/actions", json={"uai": SCHOOL, "titre": "Action répétée"}).raise_for_status()
    response = client.get(f"{API}/colleges/{SCHOOL}/fiche.pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF-")


def test_referent_exports_only_their_schools(client):
    other = other_school(client)
    create_account(client, "export-ref", "referent", [SCHOOL])
    create_account(client, "export-reader", "lecteur")

    login(client, "export-ref")
    assert client.get(f"{API}/colleges/{SCHOOL}/fiche.pdf").status_code == 200
    assert client.get(f"{API}/colleges/{other}/fiche.pdf").status_code == 403
    assert client.get(f"{API}/exports/actions.csv", params={"uai": other}).status_code == 403
    rows = read_csv(client.get(f"{API}/exports/colleges.csv"))
    assert [r[0] for r in rows[1:]] == [SCHOOL]

    login(client, "export-reader")  # readers read everything, so they export everything
    assert client.get(f"{API}/colleges/{other}/fiche.pdf").status_code == 200
    assert len(read_csv(client.get(f"{API}/exports/colleges.csv"))) == 71


def test_exports_need_a_login(client):
    client.headers.pop("Authorization")
    for path in ("/exports/colleges.csv", "/exports/actions.csv", f"/colleges/{SCHOOL}/fiche.pdf"):
        assert client.get(f"{API}{path}").status_code == 401
