"""A cached or stored entry without a real 0-100 risk_score must never surface as 0; a route
without a stored row is unavailable, never 0."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from dataclasses import asdict

from fastapi import HTTPException

from app.api.v1 import mp_routes
from app.schemas.mp_route import MpRouteSafetyResponse, SafetyScore
from app.services import model_scores
from app.services.model_scores import StoredScore

BAD_ENTRIES = [
    {"color_code": "gray", "status": "cached"},
    {"risk_score": None, "color_code": "gray"},
    {"risk_score": "42", "color_code": "yellow"},
    {"risk_score": float("nan"), "color_code": "red"},
    {"risk_score": 140.0, "color_code": "red"},
]

VERSION = "eb_v1-test"


def stored(route_id: int = 7, *, month: int = 9, percentile: float | None = 61.3, insufficient: bool = False,
           **over) -> StoredScore:
    fields = dict(
        route_id=route_id, month=month, model_version=VERSION, type_group="trad", insufficient=insufficient,
        insufficient_reason="no_evidence" if insufficient else None,
        rr_mean=None if insufficient else 1.2, rr_q05=None if insufficient else 0.7,
        rr_q95=None if insufficient else 2.0, percentile=None if insufficient else percentile,
        pct_q05=None if insufficient else max(0.0, (percentile or 0.0) - 10),
        pct_q95=None if insufficient else min(100.0, (percentile or 0.0) + 10),
        conf_level="insufficient" if insufficient else "medium", n_eff_geo=0.0 if insufficient else 1.5,
        n_eff_feature=0.0 if insufficient else 3.0, per10k_mean=None, per10k_q05=None, per10k_q95=None,
        per10k_reason=None if insufficient else "ticks_incomplete",
    )
    fields.update(over)
    return StoredScore(**fields)


def _db_returning(result: MagicMock) -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _map_row(route_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        mp_route_id=route_id, name=f"R{route_id}", latitude=40.0, longitude=-105.0,
        grade="5.9", type="sport", location_id=1,
    )


async def test_map_with_safety_route_without_stored_row_is_missing_not_zero(monkeypatch):
    result = MagicMock()
    result.fetchall.return_value = [_map_row(1), _map_row(2)]
    monkeypatch.setattr(model_scores, "active_version", AsyncMock(return_value=VERSION))
    monkeypatch.setattr(model_scores, "get_scores", AsyncMock(return_value={2: stored(2, percentile=24.96)}))

    body = await mp_routes.get_mp_routes_with_safety(
        target_date=date(2026, 9, 28), season="rock", db=_db_returning(result)
    )

    by_id = {r.mp_route_id: r for r in body.routes}
    assert by_id[1].safety is None
    # Colour is derived from the shown (rounded) score: 24.96 shows as 25.0, which is yellow.
    assert by_id[2].safety is not None
    assert (by_id[2].safety.risk_score, by_id[2].safety.color_code) == (25.0, "yellow")
    assert body.meta.cached_routes == 1
    assert body.meta.missing_routes == 1


@pytest.mark.parametrize("entry", BAD_ENTRIES + [{**asdict(stored()), "percentile": float("nan")},
                                                 {**asdict(stored()), "percentile": "61.3"},
                                                 {k: v for k, v in asdict(stored()).items() if k != "pct_q95"}])
async def test_bad_cache_entry_is_a_miss_and_the_row_is_read(monkeypatch, entry):
    monkeypatch.setattr(model_scores, "cache_get", lambda _key: entry)
    monkeypatch.setattr(model_scores, "cache_set", lambda *a, **k: True)
    row = MagicMock()
    row.one_or_none.return_value = tuple(getattr(stored(), c) for c in model_scores._SCORE_COLUMNS)
    version = MagicMock()
    version.scalar_one_or_none.return_value = VERSION
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[version, row])

    got = await model_scores.get_score(db, 7, 9)

    assert got == stored()
    assert db.execute.await_count == 2


async def test_single_safety_without_stored_row_is_404_never_zero(monkeypatch):
    monkeypatch.setattr(model_scores, "get_score", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await mp_routes.calculate_mp_route_safety(7, target_date=date(2026, 9, 28), bypass_cache=False, db=MagicMock())
    assert exc.value.status_code == 404 and exc.value.detail == "score unavailable"


async def test_single_safety_colour_is_rederived_from_the_shown_score(monkeypatch):
    monkeypatch.setattr(model_scores, "get_score", AsyncMock(return_value=stored(percentile=74.96)))
    name = MagicMock()
    name.scalar_one_or_none.return_value = "Route 7"

    body = await mp_routes.calculate_mp_route_safety(
        7, target_date=date(2026, 9, 28), bypass_cache=False, db=_db_returning(name)
    )

    assert (body.risk_score, body.color_code) == (75.0, "red")


async def test_historical_profile_month_without_row_has_no_number(monkeypatch):
    monkeypatch.setattr(
        mp_routes,
        "get_route_with_location_coords",
        AsyncMock(return_value=SimpleNamespace(name="R", latitude=None, longitude=None)),
    )
    monkeypatch.setattr(model_scores, "get_profile", AsyncMock(return_value=(VERSION, {9: stored(month=9)})))

    body = await mp_routes.get_historical_trends(1, days=30, target_date=date(2026, 9, 4), db=MagicMock())

    profile = {m["month"]: m for m in body["monthly_profile"]}
    assert profile[9]["risk_score"] == 61.3
    assert (profile[1]["risk_score"], profile[1]["color_code"], profile[1]["data_status"]) == (None, None, None)


async def test_historical_without_active_version_has_no_profile(monkeypatch):
    monkeypatch.setattr(
        mp_routes,
        "get_route_with_location_coords",
        AsyncMock(return_value=SimpleNamespace(name="R", latitude=None, longitude=None)),
    )
    monkeypatch.setattr(model_scores, "get_profile", AsyncMock(return_value=(None, {})))

    body = await mp_routes.get_historical_trends(1, days=30, target_date=date(2026, 9, 4), db=MagicMock())

    assert body["monthly_profile"] is None and body["message"] == "score unavailable"
    assert body["summary"] is None and body["historical_predictions"] == []


async def test_seasonal_patterns_are_counts_without_a_score(monkeypatch):
    route = MagicMock()
    route.scalar_one_or_none.return_value = "R"
    point = MagicMock()
    point.one_or_none.return_value = SimpleNamespace(lat=40.0, lon=-105.0, location_name="Crag")
    counts = MagicMock()
    counts.fetchall.return_value = [SimpleNamespace(month=7, n=3), SimpleNamespace(month=None, n=2)]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[route, point, counts])

    body = await mp_routes.get_seasonal_patterns(1, db=db)

    by_month = {m["month_num"]: m for m in body["monthly_patterns"]}
    assert by_month[7]["accident_count"] == 3
    assert by_month[1]["accident_count"] == 0
    assert body["year_known_only"] == 2
    assert all(set(m) == {"month", "month_num", "accident_count"} for m in body["monthly_patterns"])


@pytest.mark.parametrize("bad", [-0.1, 100.1])
def test_safety_schemas_reject_out_of_range_scores(bad):
    with pytest.raises(ValidationError):
        MpRouteSafetyResponse(route_id=1, route_name="R", target_date="2026-09-28", risk_score=bad, color_code="red")
    with pytest.raises(ValidationError):
        SafetyScore(risk_score=bad, color_code="red")
