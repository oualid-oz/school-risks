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
