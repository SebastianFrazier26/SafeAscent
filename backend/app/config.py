"""
Application configuration settings.
Reads from environment variables and .env file.
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors_origins() -> list[str]:
    """
    Parse CORS_ORIGINS from environment variable.
    Supports comma-separated string format for Railway/production.
    Falls back to default origins including production domains.
    """
    cors_env = os.environ.get("CORS_ORIGINS", "")
    if cors_env:
        # Parse comma-separated origins
        return [origin.strip() for origin in cors_env.split(",") if origin.strip()]
    # Default origins - includes both development and production
    return [
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

    # API
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "SafeAscent"

    # Environment
    ENVIRONMENT: str = "development"

    # CORS - parsed from environment variable
    CORS_ORIGINS: list[str] = parse_cors_origins()

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )


# Global settings instance
settings = Settings()
