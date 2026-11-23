from datetime import date

from app.models.security import UserRole

API = "/api/v1"
SCHOOL = "0450003J"


def create_account(client, username, role, school_uais=None):
    response = client.post(
        f"{API}/auth/users",
        json={
            "username": username,
            "password": "a-strong-test-password",
            "role": role,
            "school_uais": school_uais or [],
        },
    )
    assert response.status_code == 201
    return response.json()


def login(client, username):
    response = client.post(
        f"{API}/auth/token",
        data={"username": username, "password": "a-strong-test-password"},
    )
    assert response.status_code == 200
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


def test_api_requires_authentication_and_login_issues_token(client):
    admin_header = client.headers.pop("Authorization")
    assert client.get(f"{API}/colleges").status_code == 401
    wrong = client.post(
        f"{API}/auth/token",
        data={"username": "test-admin", "password": "wrong-password"},
    )
    assert wrong.status_code == 401
    client.headers["Authorization"] = admin_header
    assert client.get(f"{API}/auth/me").json()["role"] == UserRole.admin.value


def test_lecteur_can_read_but_cannot_change_actions_or_users(client):
    create_account(client, "reader-user", UserRole.lecteur.value)
    login(client, "reader-user")

    assert client.get(f"{API}/colleges").status_code == 200
    assert client.get(f"{API}/actions").status_code == 200
    assert client.get(f"{API}/auth/users").status_code == 403
    assert client.post(
        f"{API}/actions",
        json={"uai": SCHOOL, "titre": "Action interdite"},
    ).status_code == 403


def test_referent_is_limited_to_assigned_school_and_changes_are_audited(client):
    schools = client.get(f"{API}/colleges").json()
    other_school = next(row["uai"] for row in schools if row["uai"] != SCHOOL)
    client.post(
        f"{API}/actions",
        json={"uai": SCHOOL, "titre": "Action école assignée"},
    ).raise_for_status()
    client.post(
        f"{API}/actions",
        json={"uai": other_school, "titre": "Action autre école"},
    ).raise_for_status()

    create_account(client, "referent-user", UserRole.referent.value, [SCHOOL])
    login(client, "referent-user")

    own = client.post(
        f"{API}/actions",
        json={
            "uai": SCHOOL,
            "titre": "Mettre à jour le PPMS",
            "echeance": str(date.today()),
        },
    )
    assert own.status_code == 201
    assert client.post(
        f"{API}/actions",
        json={"uai": other_school, "titre": "Action hors périmètre"},
    ).status_code == 403
    assert all(action["uai"] == SCHOOL for action in client.get(f"{API}/actions").json())
    assert client.get(
        f"{API}/actions", params={"uai": other_school}
    ).status_code == 403

    admin_token = client.post(
        f"{API}/auth/token",
        data={"username": "test-admin", "password": "test-admin-password"},
    ).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {admin_token}"
    audit = client.get(f"{API}/auth/audit").json()
    assert any(row["event"] == "action.created" for row in audit)
    assert any(row["event"] == "user.created" for row in audit)


def test_cors_is_limited_to_configured_origins(client):
    allowed = client.options(
        f"{API}/colleges",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        },
    )
    rejected = client.options(
        f"{API}/colleges",
        headers={
            "Origin": "https://not-allowed.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:8000"
    assert "access-control-allow-origin" not in rejected.headers


def test_inactive_admin_can_be_demoted_while_another_admin_is_active(client):
    other = create_account(client, "second-admin", UserRole.admin.value)
    disabled = client.patch(f"{API}/auth/users/{other['id']}", json={"active": False})
    assert disabled.status_code == 200
    demoted = client.patch(
        f"{API}/auth/users/{other['id']}", json={"role": UserRole.lecteur.value}
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == UserRole.lecteur.value


def test_logins_are_audited(client):
    create_account(client, "audited-user", UserRole.lecteur.value)
    admin_header = client.headers["Authorization"]
    client.post(
        f"{API}/auth/token", data={"username": "audited-user", "password": "wrong-password"}
    )
    client.post(
        f"{API}/auth/token", data={"username": "nobody-here", "password": "wrong-password"}
    )
    login(client, "audited-user")
    client.headers["Authorization"] = admin_header

    audit = client.get(f"{API}/auth/audit").json()
    failed = {r["actor_username"] for r in audit if r["event"] == "auth.login_failed"}
    succeeded = {r["actor_username"] for r in audit if r["event"] == "auth.login_succeeded"}
    assert failed == {"audited-user", "nobody-here"}
    assert succeeded == {"audited-user"}


def test_password_change_revokes_existing_tokens(client):
    user = create_account(client, "rotating-user", UserRole.lecteur.value)
    admin_header = client.headers["Authorization"]
    login(client, "rotating-user")
    old_header = client.headers["Authorization"]
    assert client.get(f"{API}/auth/me").status_code == 200

    client.headers["Authorization"] = admin_header
    changed = client.patch(
        f"{API}/auth/users/{user['id']}", json={"password": "another-strong-password"}
    )
    assert changed.status_code == 200

    client.headers["Authorization"] = old_header
    assert client.get(f"{API}/auth/me").status_code == 401


def test_login_is_rate_limited(client):
    codes = [
        client.post(
            f"{API}/auth/token", data={"username": "x-user", "password": "wrong-password"}
        ).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [401] * 5
    assert codes[5] == 429
