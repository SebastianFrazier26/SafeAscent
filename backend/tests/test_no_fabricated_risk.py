"""A cached or stored entry without a real 0-100 risk_score must never surface as 0."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.api.v1 import mp_routes
from app.schemas.mp_route import MpRouteSafetyResponse, SafetyScore
from app.utils import cache as cache_module

BAD_ENTRIES = [
    {"color_code": "gray", "status": "cached"},
    {"risk_score": None, "color_code": "gray"},
    {"risk_score": "42", "color_code": "yellow"},
    {"risk_score": float("nan"), "color_code": "red"},
    {"risk_score": 140.0, "color_code": "red"},
]


def _db_returning(result: MagicMock) -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _map_row(route_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        mp_route_id=route_id, name=f"R{route_id}", latitude=40.0, longitude=-105.0,
        grade="5.9", type="sport", location_id=1,
    )


@pytest.mark.parametrize("entry", BAD_ENTRIES)
async def test_map_with_safety_treats_bad_cache_entry_as_missing(monkeypatch, entry):
    result = MagicMock()
    result.fetchall.return_value = [_map_row(1), _map_row(2)]
    monkeypatch.setattr(
        mp_routes,
        "get_bulk_cached_safety_scores",
        lambda ids, _date: {1: entry, 2: {"risk_score": 27.0, "color_code": "green"}},
    )

    body = await mp_routes.get_mp_routes_with_safety(
        target_date=date(2026, 9, 28), season="rock", db=_db_returning(result)
    )

    by_id = {r.mp_route_id: r for r in body.routes}
    assert by_id[1].safety is None
    # Stored colour from an older band set is re-derived from the score.
    assert by_id[2].safety is not None
    assert by_id[2].safety.risk_score == 27.0
    assert by_id[2].safety.color_code == "yellow"
    assert body.meta.cached_routes == 1
    assert body.meta.missing_routes == 1


def _safety_route_result() -> MagicMock:
    result = MagicMock()
    result.one_or_none.return_value = SimpleNamespace(
        mp_route_id=7, name="Route 7", type="sport", latitude=40.0, longitude=-105.0
    )
    return result


@pytest.mark.parametrize("entry", BAD_ENTRIES)
async def test_single_safety_recomputes_on_bad_cache_entry(monkeypatch, entry):
    monkeypatch.setattr(mp_routes, "cache_get", lambda _key: entry)
    monkeypatch.setattr(mp_routes, "cache_set", lambda *a, **k: True)
    predict = AsyncMock(return_value=SimpleNamespace(risk_score=61.3))
    monkeypatch.setattr(mp_routes, "predict_route_safety", predict)

    body = await mp_routes.calculate_mp_route_safety(
        7, target_date=date(2026, 9, 28), bypass_cache=False, db=_db_returning(_safety_route_result())
    )

    predict.assert_awaited_once()
    assert body.risk_score == 61.3
    assert body.color_code == "orange"


async def test_single_safety_cache_hit_rederives_colour(monkeypatch):
    monkeypatch.setattr(mp_routes, "cache_get", lambda _key: {"risk_score": 72.0, "color_code": "red"})
    predict = AsyncMock()
    monkeypatch.setattr(mp_routes, "predict_route_safety", predict)

    body = await mp_routes.calculate_mp_route_safety(
        7, target_date=date(2026, 9, 28), bypass_cache=False, db=_db_returning(_safety_route_result())
    )

    predict.assert_not_awaited()
    assert body.risk_score == 72.0
    assert body.color_code == "orange"


async def test_historical_trends_drops_rows_without_valid_score(monkeypatch):
    monkeypatch.setattr(
        mp_routes,
        "get_route_with_location_coords",
        AsyncMock(return_value=SimpleNamespace(name="R", latitude=None, longitude=None)),
    )
    result = MagicMock()
    result.fetchall.return_value = [
        (date(2026, 9, 1), None, "gray"),
        (date(2026, 9, 2), 140.0, "red"),
        (date(2026, 9, 3), 30.0, "yellow"),
    ]

    body = await mp_routes.get_historical_trends(
        1, days=30, target_date=date(2026, 9, 4), db=_db_returning(result)
    )

    assert [p["risk_score"] for p in body["historical_predictions"]] == [30.0]
    assert body["summary"]["min_risk"] == 30.0


async def test_seasonal_patterns_months_without_accidents_have_no_score(monkeypatch):
    monkeypatch.setattr(
        mp_routes,
        "get_route_with_location_coords",
        AsyncMock(return_value=SimpleNamespace(name="R", latitude=40.0, longitude=-105.0, location_id=None)),
    )
    result = MagicMock()
    result.fetchall.return_value = [(7, 3, 60.0, 100.0)]

    body = await mp_routes.get_seasonal_patterns(1, db=_db_returning(result))

    by_month = {m["month_num"]: m for m in body["monthly_patterns"]}
    assert by_month[7]["avg_risk_score"] == 60.0
    assert by_month[1]["avg_risk_score"] is None


def test_bulk_cache_writer_skips_entries_without_valid_score(monkeypatch):
    pipe = MagicMock()
    client = MagicMock()
    client.pipeline.return_value = pipe
    monkeypatch.setattr(cache_module, "get_redis_client", lambda: client)

    written = cache_module.set_bulk_cached_safety_scores(
        {1: {"color_code": "gray"}, 2: {"risk_score": None}, 3: {"risk_score": 40.0, "color_code": "yellow"}},
        "2026-09-28",
    )

    assert written == 1
    assert pipe.setex.call_count == 1
    assert pipe.setex.call_args.args[0] == cache_module.build_safety_score_key(3, "2026-09-28")


@pytest.mark.parametrize("bad", [-0.1, 100.1])
def test_safety_schemas_reject_out_of_range_scores(bad):
    with pytest.raises(ValidationError):
        MpRouteSafetyResponse(route_id=1, route_name="R", target_date="2026-09-28", risk_score=bad, color_code="red")
    with pytest.raises(ValidationError):
        SafetyScore(risk_score=bad, color_code="red")
