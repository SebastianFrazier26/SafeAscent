"""
Tests for Phase 0 security fixes:
- CORS_ORIGINS parsing accepts both comma-separated and JSON array env formats.
- Admin routes (mp-routes/admin/*) are gated behind ENABLE_ADMIN_ROUTES and off by default.
- redis-debug never returns REDIS_URL or other connection-string material.
"""
import importlib

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, DEFAULT_CORS_ORIGINS


# ============================================================================
# CORS_ORIGINS parsing
# ============================================================================

def test_cors_origins_comma_separated(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a.com,http://b.com")
    assert Settings().CORS_ORIGINS == ["http://a.com", "http://b.com"]


def test_cors_origins_comma_separated_with_spaces(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a.com, http://b.com")
    assert Settings().CORS_ORIGINS == ["http://a.com", "http://b.com"]


def test_cors_origins_json_array(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", '["http://a.com", "http://b.com"]')
    assert Settings().CORS_ORIGINS == ["http://a.com", "http://b.com"]


def test_cors_origins_empty_string_is_empty_list(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "")
    assert Settings().CORS_ORIGINS == []


def test_cors_origins_unset_uses_default(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert Settings().CORS_ORIGINS == DEFAULT_CORS_ORIGINS


# ============================================================================
# Admin route gating
# ============================================================================

def _build_app_with_admin_flag(monkeypatch: pytest.MonkeyPatch, enabled: bool):
    """Reload app.config/app.main under a patched ENABLE_ADMIN_ROUTES env var.

    Router inclusion in main.py happens at import time, so exercising both
    states requires a real reload rather than mutating settings in place.
    """
    monkeypatch.setenv("ENABLE_ADMIN_ROUTES", "true" if enabled else "false")
    import app.config as config
    importlib.reload(config)
    import app.main as main
    importlib.reload(main)
    return main.app


@pytest.fixture
def _restore_app_modules():
    """Reload config/main back to baseline after the test's monkeypatch env is undone."""
    yield
    import app.config as config
    import app.main as main
    importlib.reload(config)
    importlib.reload(main)


def test_admin_routes_absent_by_default(monkeypatch, _restore_app_modules):
    application = _build_app_with_admin_flag(monkeypatch, enabled=False)
    client = TestClient(application)
    for path in [
        "/api/v1/mp-routes/admin/queue-info",
        "/api/v1/mp-routes/admin/purge-queue",
        "/api/v1/mp-routes/admin/redis-debug",
        "/api/v1/mp-routes/admin/trigger-cache-population",
    ]:
        assert client.get(path).status_code == 404


def test_admin_routes_present_when_enabled(monkeypatch, _restore_app_modules):
    application = _build_app_with_admin_flag(monkeypatch, enabled=True)
    client = TestClient(application)
    assert client.get("/api/v1/mp-routes/admin/queue-info").status_code != 404
    assert client.get("/api/v1/mp-routes/admin/purge-queue").status_code != 404


def test_redis_debug_does_not_leak_connection_url(monkeypatch, _restore_app_modules):
    application = _build_app_with_admin_flag(monkeypatch, enabled=True)
    client = TestClient(application)
    response = client.get("/api/v1/mp-routes/admin/redis-debug")

    assert response.status_code == 200
    body = response.json()
    assert "redis_url" not in body

    serialized = str(body)
    assert "redis://" not in serialized
    assert "REDIS_URL" not in serialized
