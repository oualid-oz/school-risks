"""Application settings, overridable through environment variables or a .env file."""
import os
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # extra="ignore": .env also holds variables for docker compose (e.g. SRT_DB_PASSWORD).
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SRT_", extra="ignore")

    app_name: str = "School Risk Tracker"
    api_prefix: str = "/api/v1"

    data_dir: Path = BASE_DIR / "data"
    colleges_file: str = "colleges.parquet"
    risques_file: str = "colleges_risques.parquet"
    secteurs_file: str = "colleges_secteurs.geojson"

    # SQLite by default (tests, quick local runs); Postgres + PostGIS in production.
    # DATABASE_URL is accepted too: it is what hosting integrations such as Neon on Vercel set.
    database_url: str = Field(
        default=f"sqlite:///{BASE_DIR / 'tracker.db'}",
        validation_alias=AliasChoices("SRT_DATABASE_URL", "DATABASE_URL"),
    )

    # Authentication and browser access.
    jwt_secret_key: str = ""
    access_token_expire_minutes: int = 30
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None
    cors_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]
    rate_limit_default: str = "120/minute"
    rate_limit_login: str = "5/minute"
    # "memory://" is per process and reset on restart; use e.g. "redis://host:6379"
    # (needs the `redis` package) to share limits across workers and restarts.
    rate_limit_storage_uri: str = "memory://"
    # IPs of reverse proxies allowed to set X-Forwarded-For / X-Forwarded-Proto.
    # Leave empty when the API is exposed directly.
    trusted_proxies: list[str] = []

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, url: str) -> str:
        # Providers hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        return url

    @field_validator("bootstrap_admin_username", "bootstrap_admin_password", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: str | None) -> str | None:
        # docker compose passes unset variables as empty strings.
        return value or None

    # Logging: "text" for development, "json" (one object per line) in containers.
    log_format: str = "text"
    log_level: str = "INFO"
    # Set by the Docker build (git commit or tag) and shown by /health.
    build_version: str = "dev"

    # Weights of each hazard in the global risk score (normalised automatically).
    weight_inondation: float = 1.5
    weight_argiles: float = 1.0
    weight_mouvements_terrain: float = 1.0
    weight_industriel: float = 1.2
    weight_tmd: float = 1.0
    weight_nucleaire: float = 1.2

    # A DICRIM older than this (years) is flagged as outdated.
    dicrim_max_age_years: int = 10

    # PPMS (safety plan). Outdated when the last update is older than this many months, or
    # when no drill of a counted type was held this school year once the grace period at the
    # start of the year (from 1 September) is over. Fire drills are recorded but follow other
    # rules, so they do not count by default.
    ppms_max_age_months: int = 12
    ppms_exercices_comptes: list[str] = ["risques_majeurs", "intrusion"]
    ppms_delai_exercice_jours: int = 90

    # Email digests (Sprint 10). mail_backend "log" writes the emails to the logs instead of
    # sending them (default, safe for development); "smtp" sends them.
    mail_backend: str = "log"
    mail_from: str = "School Risk Tracker <no-reply@localhost>"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_security: str = "starttls"  # starttls | ssl | none
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_timeout_seconds: float = 10.0
    # Base URL of the app, for the links in the emails.
    public_url: str = "http://localhost:8000"
    # Weekly digest: sent from this weekday (0 = Monday) and hour (UTC) on, once per ISO
    # week and per person. A run that is cut short is finished by the next one.
    digest_weekday: int = 0
    digest_hour_utc: int = 6
    digest_due_soon_days: int = 7
    # Stop sending before the serverless time limit (Vercel: 30 s here); the rest goes out
    # at the next run.
    digest_time_budget_seconds: float = 20.0
    # Protects the scheduler endpoint. Vercel Cron sends it as "Authorization: Bearer …"
    # when the project has a CRON_SECRET variable.
    cron_secret: str | None = Field(
        default=None, validation_alias=AliasChoices("SRT_CRON_SECRET", "CRON_SECRET")
    )

    @property
    def on_vercel(self) -> bool:
        return os.environ.get("VERCEL") == "1"

    @model_validator(mode="after")
    def _vercel_defaults(self) -> "Settings":
        """Defaults for Vercel; any value set explicitly still wins."""
        if not self.on_vercel:
            return self
        if "trusted_proxies" not in self.model_fields_set:
            # Vercel's edge overwrites X-Forwarded-For with the real client IP, so it can be
            # trusted from any peer: rate limits and the audit log then see real clients.
            self.trusted_proxies = ["*"]
        if "log_format" not in self.model_fields_set:
            self.log_format = "json"
        sha = os.environ.get("VERCEL_GIT_COMMIT_SHA")
        if "build_version" not in self.model_fields_set and sha:
            self.build_version = sha[:7]
        return self


settings = Settings()
