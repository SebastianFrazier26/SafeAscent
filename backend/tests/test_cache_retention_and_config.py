"""
Tests for Redis cache/celery URL separation and the safety-score TTL.
"""

from app.config import Settings
from app.utils import cache as cache_utils


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
