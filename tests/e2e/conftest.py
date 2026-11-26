"""Browser tests (Playwright). They start a real server on a disposable Postgres + PostGIS
database, migrated from scratch and filled with the reference data at every run.

    export SRT_E2E_DATABASE_URL=postgresql+psycopg://srt:srt-dev-password@127.0.0.1:5433/school_risks_e2e
    pip install -e ".[dev]" && python -m playwright install chromium-headless-shell
    pytest tests/e2e

Skipped when SRT_E2E_DATABASE_URL is not set. The database is wiped at the start.
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

URL = os.environ.get("SRT_E2E_DATABASE_URL")
if not URL:
    pytest.skip("SRT_E2E_DATABASE_URL is not set", allow_module_level=True)
playwright_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
LOIRET = ROOT / "tests" / "fixtures" / "loiret"  # frozen test data (not the published files)
ARTIFACTS = ROOT / "tests" / "e2e" / "artifacts"
ADMIN = ("e2e-admin", "e2e-admin-password-" + secrets.token_hex(4))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    from alembic import command

    from app.db import alembic_config, make_engine
    from app.services.reference_db import load_reference
    from tests.conftest import wipe_database

    wipe_database(URL)
    command.upgrade(alembic_config(URL), "head")
    engine = make_engine(URL)
    load_reference(engine, LOIRET)
    engine.dispose()

    port = _free_port()
    env = {
        **os.environ,
        "SRT_DATABASE_URL": URL,
        "SRT_DATA_DIR": str(LOIRET),  # frozen test data, not the published files
        "SRT_JWT_SECRET_KEY": secrets.token_urlsafe(48),
        "SRT_BOOTSTRAP_ADMIN_USERNAME": ADMIN[0],
        "SRT_BOOTSTRAP_ADMIN_PASSWORD": ADMIN[1],
        # Tests log in often: keep the limits out of the way (they have their own unit tests).
        "SRT_RATE_LIMIT_LOGIN": "1000/minute",
        "SRT_RATE_LIMIT_DEFAULT": "10000/minute",
        "SRT_MAIL_BACKEND": "log",
        "SRT_LOG_FORMAT": "text",
    }
    log = open(ARTIFACTS.parent / "server.log", "w")  # noqa: SIM115 - closed below
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--no-access-log"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base}/health/ready", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if server.poll() is not None:
                raise RuntimeError(f"server exited, see {log.name}")
            time.sleep(0.2)
        else:
            raise RuntimeError(f"server not ready, see {log.name}")
        yield base
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()


@pytest.fixture(scope="session")
def pilot_server(tmp_path_factory):
    """A second server on the pilot data (3 departments), in SQLite mode: it reads the data
    files directly. Map point queries need PostGIS and are not available there."""
    port = _free_port()
    env = {
        **os.environ,
        "SRT_DATABASE_URL": f"sqlite:///{tmp_path_factory.mktemp('pilot') / 'pilot.db'}",
        "SRT_DATA_DIR": str(ROOT / "tests" / "e2e" / "fixtures" / "pilot"),
        "SRT_JWT_SECRET_KEY": secrets.token_urlsafe(48),
        "SRT_BOOTSTRAP_ADMIN_USERNAME": ADMIN[0],
        "SRT_BOOTSTRAP_ADMIN_PASSWORD": ADMIN[1],
        "SRT_RATE_LIMIT_LOGIN": "1000/minute",
        "SRT_RATE_LIMIT_DEFAULT": "10000/minute",
        "SRT_LOG_FORMAT": "text",
    }
    env.pop("SRT_E2E_DATABASE_URL", None)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--no-access-log"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base}/health/ready", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("pilot server not ready")
        yield base
    finally:
        server.terminate()
        server.wait(timeout=10)


@pytest.fixture
def pilot_page(browser, pilot_server):
    context = browser.new_context(
        base_url=pilot_server, locale="fr-FR", viewport={"width": 1280, "height": 900}
    )
    page = context.new_page()
    page.set_default_timeout(15_000)
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    login(page, *ADMIN)
    yield page
    context.close()
    assert not errors, f"JavaScript errors: {errors}"


@pytest.fixture(scope="session")
def api(live_server):
    """httpx client signed in as the admin, to set up data without the interface."""
    token = httpx.post(
        f"{live_server}/api/v1/auth/token", data={"username": ADMIN[0], "password": ADMIN[1]}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=f"{live_server}/api/v1", headers=headers) as client:
        yield client


@pytest.fixture
def make_user(api):
    """Create a user through the API; returns (username, password)."""
    def create(role: str, schools: list[str] | None = None, **extra) -> tuple[str, str]:
        username = f"{role}-{secrets.token_hex(3)}"
        password = "e2e-password-" + secrets.token_hex(4)
        response = api.post("/auth/users", json={
            "username": username, "password": password, "role": role, "school_uais": schools or [], **extra,
        })
        response.raise_for_status()
        return username, password
    return create


@pytest.fixture(scope="session")
def browser():
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture
def page(browser, request, live_server):
    context = browser.new_context(
        base_url=live_server, locale="fr-FR", viewport={"width": 1280, "height": 900}
    )
    page = context.new_page()
    page.set_default_timeout(15_000)
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("dialog", lambda dialog: dialog.accept())  # confirm() before deletions
    yield page
    failed = getattr(request.node, "rep_call", None) is not None and request.node.rep_call.failed
    if failed:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(ARTIFACTS / f"{request.node.name}.png"), full_page=True)
    context.close()
    assert not errors, f"JavaScript errors: {errors}"


def login(page, username: str, password: str) -> None:
    page.goto("/")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#login-form button[type=submit]")
    playwright_api.expect(page.locator("#school-list .school-row").first).to_be_visible()


@pytest.fixture
def admin_page(page):
    login(page, *ADMIN)
    return page
