"""Cache-warming must store a colour that agrees with the rounded, stored score.

Owner decision 2026-09-28 (see risk-bands-report.md): `_warm_cache_async` derived
`color_code` from the raw (unrounded) prediction before rounding the score it
actually cached, so a raw 24.96 stored as 25.0 (yellow) could be tagged green.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.tasks import cache_warming


class _FakeAsyncSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        route = SimpleNamespace(
            mp_route_id=1,
            name="Test Route",
            latitude=40.0,
            longitude=-105.0,
            type="sport",
        )
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [route]))


async def test_warm_cache_colour_matches_stored_rounded_score(monkeypatch):
    monkeypatch.setattr(cache_warming, "AsyncSessionLocal", lambda: _FakeAsyncSession())
    monkeypatch.setattr(cache_warming.asyncio, "sleep", AsyncMock())

    # Raw 24.96 rounds (for storage) to 25.0, which is yellow.
    monkeypatch.setattr(
        cache_warming,
        "predict_route_safety",
        AsyncMock(return_value=SimpleNamespace(risk_score=24.96)),
    )

    cached_calls = []
    monkeypatch.setattr(
        cache_warming,
        "cache_set",
        lambda key, data, ttl_seconds=None: cached_calls.append(data),
    )

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 28)

    monkeypatch.setattr(cache_warming, "date", _FixedDate)

    await cache_warming._warm_cache_async()

    assert cached_calls, "expected at least one cache_set call"
    for data in cached_calls:
        assert data["risk_score"] == 25.0
        assert data["color_code"] == "yellow"
