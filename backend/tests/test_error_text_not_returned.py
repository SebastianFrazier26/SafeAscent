"""Exception text stays in server logs; returned stats carry only the type."""

import logging

import pytest
import redis

from app.utils import cache as cache_module

SECRET = "redis://default:hunter2@internal-host:6379"


class RaisingRedis:
    def info(self, *args, **kwargs):
        raise redis.ConnectionError(f"Error connecting to {SECRET}")

    def keys(self, *args, **kwargs):
        raise redis.ConnectionError(f"Error connecting to {SECRET}")


@pytest.fixture
def raising_redis(monkeypatch):
    monkeypatch.setattr(cache_module, "get_redis_client", lambda: RaisingRedis())


def test_cache_stats_error_returns_type_not_text(raising_redis, caplog):
    with caplog.at_level(logging.ERROR, logger=cache_module.logger.name):
        stats = cache_module.get_cache_stats()
    assert stats == {"status": "error", "error": "Redis error (ConnectionError)"}
    assert SECRET in caplog.text


def test_safety_cache_stats_error_returns_type_not_text(raising_redis, caplog):
    with caplog.at_level(logging.ERROR, logger=cache_module.logger.name):
        stats = cache_module.get_safety_cache_stats("2026-09-28")
    assert stats == {"status": "error", "error": "Redis error (ConnectionError)", "cached_count": 0}
    assert SECRET in caplog.text
