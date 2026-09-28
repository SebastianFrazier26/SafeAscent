"""Settings defaults, required fields, and the no-os.getenv rule (Phase 1 PR4)."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DB_URL = "postgresql+asyncpg://u:p@localhost:5432/db"


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Build Settings from exactly `env`, ignoring the caller's shell and any .env file."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_database_url_is_required(monkeypatch):
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        _settings(monkeypatch)


def test_importing_config_without_database_url_fails_loudly(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONPATH"] = str(BACKEND_DIR)
    result = subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr


def test_missing_database_url_error_does_not_leak_other_secrets(tmp_path):
    """Regression: pydantic's ValidationError embeds the whole resolved input
    dict in `input_value` for a missing required field. Without
    hide_input_in_errors, a credential in any other field (REDIS_URL,
    CELERY_BROKER_URL, ...) leaks into stderr alongside the DATABASE_URL
    fail-loud message."""
    # pydantic truncates a long `input_value` repr, which would hide the
    # marker in the middle and make this test pass even without the fix.
    # Drop every other Settings field from the child's env (conftest.py's
    # ENVIRONMENT=test would otherwise pad the dict past the truncation
    # threshold) so the untruncated repr is short and the marker, if present
    # at all, can only come from REDIS_URL leaking.
    marker = "leakcanary"
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in Settings.model_fields
    }
    env["PYTHONPATH"] = str(BACKEND_DIR)
    env["REDIS_URL"] = f"redis://:{marker}@x/0"
    result = subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr
    assert marker not in result.stderr
    assert marker not in result.stdout


def test_environment_defaults_to_production(monkeypatch):
    assert _settings(monkeypatch, DATABASE_URL=TEST_DB_URL).ENVIRONMENT == "production"


def test_environment_rejects_unknown_values(monkeypatch):
    with pytest.raises(ValidationError, match="ENVIRONMENT"):
        _settings(monkeypatch, DATABASE_URL=TEST_DB_URL, ENVIRONMENT="staging")


def test_sql_echo_off_by_default_even_in_development(monkeypatch):
    s = _settings(monkeypatch, DATABASE_URL=TEST_DB_URL, ENVIRONMENT="development")
    assert s.SQL_ECHO is False


def test_sql_echo_explicit_opt_in(monkeypatch):
    assert _settings(monkeypatch, DATABASE_URL=TEST_DB_URL, SQL_ECHO="true").SQL_ECHO is True


def test_engine_echo_follows_sql_echo():
    from app.db.session import engine

    assert engine.echo is False


def test_defaults_for_moved_env_vars(monkeypatch):
    s = _settings(monkeypatch, DATABASE_URL=TEST_DB_URL)
    assert s.OPEN_METEO_API_KEY is None
    assert s.USE_VECTORIZED_ALGORITHM is True
    assert s.SKIP_WEATHER_STATISTICS is False
    assert s.ENABLE_ADMIN_ROUTES is False
    assert s.HEALTHCHECKS_NIGHTLY_URL is None
    assert s.HEALTHCHECKS_BEAT_URL is None
    assert s.WORKER_HEARTBEAT_TTL_SECONDS == 120


def test_moved_env_vars_are_read_from_environment(monkeypatch):
    s = _settings(
        monkeypatch,
        DATABASE_URL=TEST_DB_URL,
        OPEN_METEO_API_KEY="k",
        USE_VECTORIZED_ALGORITHM="false",
        SKIP_WEATHER_STATISTICS="true",
    )
    assert s.OPEN_METEO_API_KEY == "k"
    assert s.USE_VECTORIZED_ALGORITHM is False
    assert s.SKIP_WEATHER_STATISTICS is True


def test_app_code_never_reads_environment_directly():
    pattern = re.compile(r"\bos\.(getenv|environ)\b")
    offenders = [
        f"{path.relative_to(BACKEND_DIR)}:{lineno}"
        for path in sorted((BACKEND_DIR / "app").rglob("*.py"))
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if pattern.search(line)
    ]
    assert offenders == []


async def test_skip_weather_statistics_setting_short_circuits(monkeypatch):
    from app.services import weather_service

    def _no_network(*args, **kwargs):
        raise AssertionError("network call made despite SKIP_WEATHER_STATISTICS")

    monkeypatch.setattr(weather_service.settings, "SKIP_WEATHER_STATISTICS", True)
    monkeypatch.setattr(weather_service.requests, "get", _no_network)
    assert await weather_service.fetch_weather_statistics(40.0, -105.0, 3000.0, "summer") is None
