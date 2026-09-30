"""
Application configuration settings.

Settings is the single source of runtime configuration: application code reads
`settings.<FIELD>` and never the process environment directly. `.env.example`
at the repo root must list exactly these fields plus the frontend `VITE_*`
build args (enforced by tests/test_env_example_parity.py).
"""
import json
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Production only: an unset CORS_ORIGINS on a deployed service must not admit
# localhost. Local dev sets it explicitly (docker-compose.yml, .env.example).
DEFAULT_CORS_ORIGINS = [
    "https://safeascent.us",
    "https://www.safeascent.us",
]


class Settings(BaseSettings):
    """Application settings."""

    # Required, no default: a missing value must fail startup rather than fall
    # back to a guessed local database.
    DATABASE_URL: str

    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_REDIS_URL: str | None = None
    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None

    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "SafeAscent"

    # Defaults to production so an unset variable on a deployed service never
    # enables development-only behavior.
    ENVIRONMENT: Literal["development", "test", "production"] = "production"
    SQL_ECHO: bool = False

    # Admin queue/cache routes (backend/app/api/v1/admin.py) are off by
    # default and only mounted when this is set.
    ENABLE_ADMIN_ROUTES: bool = False

    # CORS - accepts comma-separated or JSON array.
    # NoDecode: pydantic-settings otherwise JSON-decodes list-typed env vars
    # before validators run, which raises SettingsError on comma-separated input.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = DEFAULT_CORS_ORIGINS

    OPEN_METEO_API_KEY: str | None = None
    USE_VECTORIZED_ALGORITHM: bool = True
    SKIP_WEATHER_STATISTICS: bool = False

    HEALTHCHECKS_NIGHTLY_URL: str | None = None
    HEALTHCHECKS_BEAT_URL: str | None = None
    WORKER_HEARTBEAT_TTL_SECONDS: int = 120

    # Phase 2 data jobs connect as the least-privilege ingest role; never set on the API.
    INGEST_DATABASE_URL: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        # A ValidationError's "missing field" message embeds the whole
        # resolved input dict, so without this, a credential in any other
        # field (REDIS_URL, CELERY_BROKER_URL, ...) leaks into the
        # DATABASE_URL fail-loud error at import time.
        hide_input_in_errors=True,
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        """Parse comma-separated or JSON array CORS_ORIGINS env value."""
        if isinstance(value, list):
            return value
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            parsed: list[str] = json.loads(stripped)
            return parsed
        return [origin.strip() for origin in stripped.split(",") if origin.strip()]

    @property
    def cache_redis_url(self) -> str:
        """Redis URL for application cache operations."""
        return self.CACHE_REDIS_URL or self.REDIS_URL

    @property
    def celery_broker_url(self) -> str:
        """Redis URL for Celery broker."""
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def celery_result_backend(self) -> str:
        """Redis URL for Celery result backend."""
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL


settings = Settings()
