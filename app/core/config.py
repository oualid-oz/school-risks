"""Application settings, overridable through environment variables or a .env file."""
from pathlib import Path

from pydantic import field_validator
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

    # Tracking database (actions / follow-ups). SQLite by default, swap for Postgres later.
    database_url: str = f"sqlite:///{BASE_DIR / 'tracker.db'}"

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


settings = Settings()
