"""
Application configuration settings.
Reads from environment variables and .env file.
"""
import json
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "https://safeascent.us",
    "https://www.safeascent.us",
]


class Settings(BaseSettings):
    """Application settings."""

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://sebastianfrazier@localhost:5432/safeascent"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_REDIS_URL: str | None = None
    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None

    # API
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "SafeAscent"

    # Environment
    ENVIRONMENT: str = "development"

    # Admin routes (queue/cache management) - off by default, gated behind flag
    ENABLE_ADMIN_ROUTES: bool = False

    # CORS - accepts comma-separated (documented in .env.example) or JSON array.
    # NoDecode: pydantic-settings otherwise JSON-decodes list-typed env vars
    # before validators run, which raises SettingsError on comma-separated input.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = DEFAULT_CORS_ORIGINS

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
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
            return json.loads(stripped)
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


# Global settings instance
settings = Settings()
