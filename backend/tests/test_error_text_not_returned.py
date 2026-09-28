"""Exception text stays in server logs; returned stats and task results carry only the type."""

import asyncio
import logging

import pytest
import redis

from app.tasks import safety_computation_optimized as nightly
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


def test_nightly_failed_date_keeps_exception_text_out_of_the_task_result(monkeypatch, caplog):
    async def broken(**kwargs):
        raise RuntimeError(f"connection to {SECRET} refused")

    monkeypatch.setattr(nightly, "DAYS_TO_COMPUTE", 1)
    monkeypatch.setattr(nightly, "DATE_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(nightly, "clear_stale_safety_score_keys", lambda keep: 0)
    monkeypatch.setattr(nightly, "compute_safety_scores_optimized", broken)

    with caplog.at_level(logging.ERROR, logger=nightly.logger.name):
        with pytest.raises(RuntimeError) as raised:
            asyncio.run(nightly._compute_all_dates_async())

    assert SECRET not in str(raised.value)
    assert "Failed dates during optimized cache computation" in str(raised.value)
    assert SECRET in caplog.text
