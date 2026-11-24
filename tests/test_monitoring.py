import json
import logging

from app.core.logging import JsonFormatter, request_id_var
from app.data.store import get_store


def test_readiness_reports_each_check(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "reference_data": "ok"}


def test_readiness_fails_when_reference_data_is_missing(client, monkeypatch):
    monkeypatch.setattr(get_store(), "list_colleges", lambda: [])
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["reference_data"] == "empty"


def test_request_id_is_returned_and_propagated(client, caplog):
    generated = client.get("/health").headers["x-request-id"]
    assert len(generated) == 32

    with caplog.at_level(logging.INFO, logger="app.request"):
        response = client.get("/api/v1/stats", headers={"X-Request-ID": "proxy-abc.123"})
    assert response.headers["x-request-id"] == "proxy-abc.123"
    record = next(r for r in caplog.records if r.name == "app.request")
    assert (record.path, record.status, record.method) == ("/api/v1/stats", 200, "GET")

    unsafe = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert unsafe.headers["x-request-id"] != "bad id\nwith newline"


def test_json_formatter_includes_extras_and_request_id():
    record = logging.makeLogRecord(
        {"name": "app.request", "levelname": "INFO", "msg": "request", "status": 201, "path": "/x"}
    )
    token = request_id_var.set("req-1")
    try:
        entry = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert entry["message"] == "request" and entry["request_id"] == "req-1"
    assert entry["status"] == 201 and entry["path"] == "/x"


def test_error_reports_never_carry_personal_data():
    from app.core.monitoring import scrub

    event = {
        "request": {
            "url": "https://example.fr/api/v1/auth/me/password",
            "data": {"current_password": "secret", "new_password": "secret2"},
            "cookies": {"session": "x"},
            "env": {"REMOTE_ADDR": "203.0.113.9"},
            "headers": {"Authorization": "Bearer abc", "X-Forwarded-For": "203.0.113.9", "Accept": "json"},
        },
        "user": {"id": 7, "email": "a@b.fr", "ip_address": "203.0.113.9"},
    }
    cleaned = scrub(event)
    assert "data" not in cleaned["request"] and "cookies" not in cleaned["request"]
    assert "env" not in cleaned["request"]
    assert cleaned["request"]["headers"] == {"Accept": "json"}
    assert cleaned["user"] == {"id": 7}


def test_error_reporting_is_off_without_a_dsn(monkeypatch):
    from app.core.config import settings
    from app.core.monitoring import init_error_reporting

    monkeypatch.setattr(settings, "sentry_dsn", None)
    assert init_error_reporting() is False


def test_redis_url_from_the_vercel_integration_is_used(monkeypatch):
    from app.core.config import Settings

    for name in ("SRT_RATE_LIMIT_STORAGE_URI", "REDIS_URL", "KV_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KV_URL", "rediss://default:token@eu1-xx.upstash.io:6379")
    assert Settings(_env_file=None).rate_limit_storage_uri.startswith("rediss://")
    monkeypatch.setenv("SRT_RATE_LIMIT_STORAGE_URI", "redis://localhost:6379/1")  # explicit wins
    assert Settings(_env_file=None).rate_limit_storage_uri == "redis://localhost:6379/1"


def test_the_api_keeps_answering_when_redis_is_down(client, monkeypatch):
    """Regression: with an unreachable Redis every request answered 500."""
    from limits.storage import storage_from_string

    from app.api.routes.auth import limiter

    monkeypatch.setattr(limiter, "_storage", storage_from_string("redis://127.0.0.1:1/0"))
    monkeypatch.setattr(limiter, "_limiter", type(limiter._limiter)(limiter._storage))
    assert client.get("/api/v1/stats").status_code == 200
