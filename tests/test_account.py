"""Sprint 12: own password change (the rest of the sprint is frontend)."""
from tests.test_security import create_account, login

API = "/api/v1"
OLD, NEW = "a-strong-test-password", "a-brand-new-password"


def test_password_change_keeps_this_session_and_revokes_the_others(client):
    create_account(client, "pw-user", "lecteur")
    login(client, "pw-user")
    other_device = dict(client.headers)

    response = client.post(f"{API}/auth/me/password", json={"current_password": OLD, "new_password": NEW})
    assert response.status_code == 200
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    assert client.get(f"{API}/auth/me").status_code == 200  # new token works
    assert client.get(f"{API}/auth/me", headers=other_device).status_code == 401  # old one revoked

    assert client.post(f"{API}/auth/token", data={"username": "pw-user", "password": OLD}).status_code == 401
    assert client.post(f"{API}/auth/token", data={"username": "pw-user", "password": NEW}).status_code == 200


def test_wrong_current_password_is_refused_and_audited(client):
    create_account(client, "pw-wrong", "referent")
    admin = dict(client.headers)
    login(client, "pw-wrong")
    wrong = client.post(f"{API}/auth/me/password", json={"current_password": "nope", "new_password": NEW})
    assert wrong.status_code == 400 and wrong.json()["detail"] == "Mot de passe actuel incorrect"
    assert client.get(f"{API}/auth/me").status_code == 200  # still signed in (400, not 401)

    client.headers.update(admin)
    events = [r["event"] for r in client.get(f"{API}/auth/audit").json()]
    assert "user.password_change_failed" in events


def test_new_password_rules(client):
    create_account(client, "pw-rules", "lecteur")
    login(client, "pw-rules")
    same = client.post(f"{API}/auth/me/password", json={"current_password": OLD, "new_password": OLD})
    short = client.post(f"{API}/auth/me/password", json={"current_password": OLD, "new_password": "court"})
    assert same.status_code == 422 and short.status_code == 422


def test_password_change_is_rate_limited(client):
    create_account(client, "pw-brute", "lecteur")
    login(client, "pw-brute")
    url = f"{API}/auth/me/password"
    codes = [
        client.post(url, json={"current_password": f"guess{i}", "new_password": NEW}).status_code
        for i in range(7)
    ]
    assert codes[:5] == [400] * 5 and 429 in codes[5:]
