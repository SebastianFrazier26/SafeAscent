"""
Application configuration settings.
Reads from environment variables and .env file.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # CORS
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",  # React dev server
        "http://localhost:5173",  # Vite dev server
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )


# Global settings instance
settings = Settings()
