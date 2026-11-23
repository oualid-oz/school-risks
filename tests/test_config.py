import pytest

from app.core.config import Settings, settings
from app.db import init_db


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("VERCEL", "VERCEL_GIT_COMMIT_SHA", "DATABASE_URL", "SRT_DATABASE_URL",
                 "SRT_LOG_FORMAT", "SRT_TRUSTED_PROXIES", "SRT_BUILD_VERSION"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_provider_urls_get_the_psycopg_driver(clean_env):
    clean_env.setenv("DATABASE_URL", "postgres://u:p@ep-1-pooler.neon.tech/db?sslmode=require")
    assert Settings(_env_file=None).database_url == (
        "postgresql+psycopg://u:p@ep-1-pooler.neon.tech/db?sslmode=require"
    )
    clean_env.setenv("SRT_DATABASE_URL", "postgresql://a:b@host/x")  # the SRT_ name wins
    assert Settings(_env_file=None).database_url == "postgresql+psycopg://a:b@host/x"


def test_vercel_defaults_do_not_override_explicit_values(clean_env):
    clean_env.setenv("VERCEL", "1")
    clean_env.setenv("VERCEL_GIT_COMMIT_SHA", "0123456789abcdef")
    s = Settings(_env_file=None)
    assert (s.trusted_proxies, s.log_format, s.build_version) == (["*"], "json", "0123456")

    clean_env.setenv("SRT_LOG_FORMAT", "text")
    clean_env.setenv("SRT_TRUSTED_PROXIES", '["10.0.0.1"]')
    s = Settings(_env_file=None)
    assert (s.trusted_proxies, s.log_format) == (["10.0.0.1"], "text")


def test_outside_vercel_nothing_changes(clean_env):
    s = Settings(_env_file=None)
    assert (s.trusted_proxies, s.log_format, s.build_version) == ([], "text", "dev")
    assert s.database_url.startswith("sqlite:///")


def test_sqlite_is_refused_on_vercel(clean_env):
    clean_env.setenv("VERCEL", "1")
    clean_env.setattr(settings, "database_url", "sqlite:///tracker.db")
    with pytest.raises(RuntimeError, match="read-only filesystem"):
        init_db()
