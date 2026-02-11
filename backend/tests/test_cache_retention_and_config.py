"""
Tests for Redis cache/celery URL separation and safety-key retention cleanup.
"""

from app.config import Settings
from app.utils import cache as cache_utils


class FakeRedis:
    """Minimal Redis stub for safety key cleanup tests."""

    def __init__(self, keys):
        self.keys = set(keys)

    def scan_iter(self, match=None, count=None):
        for key in list(self.keys):
            if match == "safety:route:*:date:*":
                if key.startswith("safety:route:") and ":date:" in key:
                    yield key
                continue
            yield key

    def delete(self, *keys):
        deleted = 0
        for key in keys:
            if key in self.keys:
                self.keys.remove(key)
                deleted += 1
        return deleted


def test_settings_redis_url_fallbacks():
    settings = Settings(REDIS_URL="redis://base:6379/0")
    assert settings.cache_redis_url == "redis://base:6379/0"
    assert settings.celery_broker_url == "redis://base:6379/0"
    assert settings.celery_result_backend == "redis://base:6379/0"


def test_settings_redis_url_overrides():
    settings = Settings(
        REDIS_URL="redis://base:6379/0",
        CACHE_REDIS_URL="redis://cache:6379/1",
        CELERY_BROKER_URL="redis://broker:6379/2",
        CELERY_RESULT_BACKEND="redis://result:6379/3",
    )
    assert settings.cache_redis_url == "redis://cache:6379/1"
    assert settings.celery_broker_url == "redis://broker:6379/2"
    assert settings.celery_result_backend == "redis://result:6379/3"


def test_safety_score_ttl_is_two_days():
    assert cache_utils.SAFETY_SCORE_TTL == 2 * 24 * 60 * 60


def test_clear_stale_safety_score_keys(monkeypatch):
    fake = FakeRedis(
        keys=[
            "safety:route:1:date:2026-02-11",
            "safety:route:2:date:2026-02-12",
            "safety:route:3:date:2026-02-13",
            "safety:route:4:date:2026-02-09",
            "weather:pattern:40.0:-105.0:2026-02-11",
        ]
    )
    monkeypatch.setattr(cache_utils, "get_redis_client", lambda: fake)

    deleted = cache_utils.clear_stale_safety_score_keys(
        keep_dates=["2026-02-11", "2026-02-12", "2026-02-13"]
    )

    assert deleted == 1
    assert "safety:route:4:date:2026-02-09" not in fake.keys
    assert "weather:pattern:40.0:-105.0:2026-02-11" in fake.keys


def test_clear_stale_safety_score_keys_redis_unavailable(monkeypatch):
    monkeypatch.setattr(cache_utils, "get_redis_client", lambda: None)
    deleted = cache_utils.clear_stale_safety_score_keys(keep_dates=["2026-02-11"])
    assert deleted == 0
