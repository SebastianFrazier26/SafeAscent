"""Owner decision 2026-09-28: a route with too little evidence (no contributing accident, or a
raw score < 0.05 that would display as 0.0) is "insufficient data" (null score, gray), never a
0.0 green. Interim until the Phase 3 similarity-based model,
which can borrow evidence from characteristically similar routes anywhere.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.api.v1 import mp_routes
from app.api.v1 import predict as predict_module
from app.schemas.mp_route import MpRouteSafetyResponse, SafetyScore
from app.schemas.prediction import PredictionRequest, PredictionResponse
from app.services.location_safety_computation import LocationBaseScore, compute_batch_route_scores
from app.services.risk_bands import (
    INSUFFICIENT_DATA_MESSAGE,
    MIN_ESTIMABLE_SCORE,
    cached_safety,
    estimable_score,
)
from app.services.safety_algorithm import SafetyPrediction
from app.services.weather_similarity import WeatherPattern
from app.tasks import cache_warming, safety_computation_optimized
from app.utils import cache as cache_module

INSUFFICIENT = {"risk_score": None, "color_code": "gray", "data_status": "insufficient_data"}


def _weather() -> WeatherPattern:
    return WeatherPattern(
        temperature=[12.0] * 7,
        precipitation=[0.5] * 7,
        wind_speed=[4.0] * 7,
        visibility=[10000.0] * 7,
        cloud_cover=[40.0] * 7,
        daily_temps=[(5.0, 12.0, 18.0)] * 7,
    )


def _accidents() -> list:
    return [
        predict_module.AccidentRecord(1, 40.02, -105.28, 2400.0, date(2024, 6, 1), "Rock Climbing", "fall", "Trad", "Serious"),
        predict_module.AccidentRecord(2, 40.10, -105.35, 2600.0, date(2023, 8, 15), "Rock Climbing", "fall", "Sport", "Fatal"),
    ]


def _request() -> PredictionRequest:
    return PredictionRequest(
        latitude=40.0, longitude=-105.27, route_type="trad",
        planned_date=date(2026, 7, 15), elevation_meters=2500.0,
    )


@pytest.fixture
def offline_predict(monkeypatch):
    monkeypatch.setattr(predict_module, "fetch_all_accidents", AsyncMock(return_value=_accidents()))
    monkeypatch.setattr(
        predict_module, "fetch_accident_weather_patterns", AsyncMock(return_value={1: _weather(), 2: None})
    )
    monkeypatch.setattr(predict_module, "fetch_weather_statistics", AsyncMock(return_value=None))


# --- evidence rule ---------------------------------------------------------------------

def test_owner_threshold_is_the_display_rounding_edge():
    assert MIN_ESTIMABLE_SCORE == 0.05
    assert INSUFFICIENT_DATA_MESSAGE == "Too little evidence to estimate risk yet"


@pytest.mark.parametrize(
    "count,raw,expected",
    [
        (0, 42.0, None),
        (3, None, None),
        (3, 0.0, None),
        (3, 1e-9, None),
        (3, 0.0499, None),
        (3, float("nan"), None),
        (3, 0.05, 0.05),
        (47, 68.4, 68.4),
        (1, 100.0, 100.0),
    ],
)
def test_estimable_score(count, raw, expected):
    assert estimable_score(count, raw) == expected


@pytest.mark.parametrize(
    "entry,expected",
    [
        (INSUFFICIENT, (None, "gray", "insufficient_data")),
        ({"risk_score": 27.0, "color_code": "green"}, (27.0, "yellow", "ok")),
        ({"risk_score": 27.0, "color_code": "yellow", "data_status": "ok"}, (27.0, "yellow", "ok")),
        # Pre-deploy entries stored a 0.0 green for no evidence; they read as insufficient.
        ({"risk_score": 0.0, "color_code": "green"}, (None, "gray", "insufficient_data")),
        ({"risk_score": 0.0, "color_code": "green", "data_status": "ok"}, (None, "gray", "insufficient_data")),
        ({"risk_score": 0.04, "color_code": "green"}, (None, "gray", "insufficient_data")),
        ({"risk_score": 0.1, "color_code": "green"}, (0.1, "green", "ok")),
        # No explicit status: a null score is a miss, never insufficient and never 0.
        ({"risk_score": None, "color_code": "gray"}, None),
        # Contradictions are misses.
        ({"risk_score": 12.0, "color_code": "gray", "data_status": "insufficient_data"}, None),
        ({"risk_score": None, "color_code": "gray", "data_status": "ok"}, None),
        ({"risk_score": None, "data_status": "bogus"}, None),
        (None, None),
        ("junk", None),
    ],
)
def test_cached_safety_parses_entries(entry, expected):
    assert cached_safety(entry) == expected


# --- schema contract -------------------------------------------------------------------

def _safety_response(**kw):
    return MpRouteSafetyResponse(route_id=1, route_name="R", target_date="2026-09-28", **kw)


def _prediction_response(**kw):
    return PredictionResponse(num_contributing_accidents=0, top_contributing_accidents=[], metadata={}, **kw)


@pytest.mark.parametrize("build", [_safety_response, SafetyScore, _prediction_response])
def test_schemas_accept_insufficient_shape(build):
    body = build(**INSUFFICIENT)
    assert body.risk_score is None
    assert body.color_code == "gray"
    assert body.data_status == "insufficient_data"


@pytest.mark.parametrize("build", [_safety_response, SafetyScore, _prediction_response])
def test_schemas_default_status_is_ok_for_a_real_score(build):
    body = build(risk_score=42.0, color_code="yellow")
    assert body.data_status == "ok"


@pytest.mark.parametrize("build", [_safety_response, SafetyScore, _prediction_response])
@pytest.mark.parametrize(
    "fields",
    [
        {"risk_score": 12.0, "color_code": "gray", "data_status": "insufficient_data"},
        {"risk_score": 12.0, "color_code": "gray", "data_status": "ok"},
        {"risk_score": 12.0, "color_code": "gray"},
        {"risk_score": None, "color_code": "gray", "data_status": "ok"},
        {"risk_score": None, "color_code": "green", "data_status": "insufficient_data"},
        {"risk_score": 0.0, "color_code": "green", "data_status": "insufficient_data"},
        {"risk_score": 140.0, "color_code": "red", "data_status": "ok"},
        {"risk_score": 12.0, "color_code": "purple", "data_status": "ok"},
        {"risk_score": None, "color_code": "gray", "data_status": "unknown"},
    ],
)
def test_schemas_reject_malformed_pairings(build, fields):
    with pytest.raises(ValidationError):
        build(**fields)


# --- /predict --------------------------------------------------------------------------

async def test_predict_with_no_accidents_is_insufficient(monkeypatch, offline_predict):
    monkeypatch.setattr(predict_module, "fetch_all_accidents", AsyncMock(return_value=[]))
    body = await predict_module.predict_route_safety(_request(), db=None, prefetched_weather=_weather())
    assert body.data_status == "insufficient_data"
    assert body.risk_score is None
    assert body.color_code == "gray"


@pytest.mark.parametrize("raw", [0.0, 0.0499])
async def test_predict_below_display_threshold_is_insufficient(monkeypatch, offline_predict, raw):
    zero = SafetyPrediction(
        risk_score=raw,
        num_contributing_accidents=2,
        top_contributing_accidents=[],
        metadata={"route_type": "trad", "search_date": "2026-07-15", "total_influence_sum": raw / 7},
    )
    monkeypatch.setattr(predict_module, "calculate_safety_score_vectorized", lambda **_: zero)
    body = await predict_module.predict_route_safety(_request(), db=None, prefetched_weather=_weather())
    assert body.data_status == "insufficient_data"
    assert body.risk_score is None
    assert body.color_code == "gray"
    assert body.metadata["message"] == INSUFFICIENT_DATA_MESSAGE
    # Counts stay truthful even when the score is withheld.
    assert body.num_contributing_accidents == 2


async def test_predict_far_away_accidents_are_insufficient(monkeypatch, offline_predict):
    far = [
        predict_module.AccidentRecord(9, -45.0, 170.0, 1000.0, date(2024, 6, 1), "Rock Climbing", "fall", "Trad", "Serious"),
    ]
    monkeypatch.setattr(predict_module, "fetch_all_accidents", AsyncMock(return_value=far))
    monkeypatch.setattr(predict_module, "fetch_accident_weather_patterns", AsyncMock(return_value={9: _weather()}))
    body = await predict_module.predict_route_safety(_request(), db=None, prefetched_weather=_weather())
    assert body.data_status == "insufficient_data"
    assert body.risk_score is None


async def test_predict_with_evidence_is_unchanged(offline_predict):
    body = await predict_module.predict_route_safety(_request(), db=None, prefetched_weather=_weather())
    assert body.data_status == "ok"
    # Raw value captured on 1c5a30f (before this change) with the same fixture: the formula
    # is untouched; the response carries the 1-decimal value every surface shows.
    assert body.metadata["raw_risk_score"] == 8.90009896995113
    assert body.risk_score == 8.9
    assert body.color_code == "green"
    assert body.num_contributing_accidents == 2


# --- nightly batch ---------------------------------------------------------------------

def _evidence_base() -> LocationBaseScore:
    return LocationBaseScore(
        location_id=1, latitude=40.0, longitude=-105.27, elevation_m=2500.0,
        accident_base_influences={1: 0.8, 2: 0.35},
        accident_metadata={
            1: {"route_type": "trad", "grade": "5.9", "distance_km": 2.0, "days_ago": 700},
            2: {"route_type": "sport", "grade": "5.11a", "distance_km": 12.0, "days_ago": 1000},
        },
    )


def test_batch_route_with_no_contributing_accidents_is_insufficient():
    empty = LocationBaseScore(location_id=1, latitude=64.0, longitude=-150.0, elevation_m=None)
    result = compute_batch_route_scores(empty, [{"route_id": 5, "route_type": "trad", "grade": "5.9"}])
    assert result[5] == INSUFFICIENT


def _single_accident_base(influence: float) -> LocationBaseScore:
    return LocationBaseScore(
        location_id=1, latitude=64.0, longitude=-150.0, elevation_m=None,
        accident_base_influences={1: influence},
        accident_metadata={1: {"route_type": "trad", "grade": None, "distance_km": 90.0, "days_ago": 10}},
    )


def test_batch_route_below_display_threshold_is_insufficient():
    # trad/trad weight 1.0, no grades: raw = 0.007 * 7 = 0.049 < 0.05.
    result = compute_batch_route_scores(_single_accident_base(0.007), [{"route_id": 5, "route_type": "trad", "grade": None}])
    assert result[5] == INSUFFICIENT


def test_batch_route_at_display_threshold_is_scored():
    result = compute_batch_route_scores(_single_accident_base(0.0072), [{"route_id": 5, "route_type": "trad", "grade": None}])
    assert result[5] == {"risk_score": 0.1, "color_code": "green", "data_status": "ok"}


def test_batch_route_with_evidence_is_unchanged():
    result = compute_batch_route_scores(
        _evidence_base(),
        [{"route_id": 10, "route_type": "trad", "grade": "5.10a"}, {"route_id": 11, "route_type": "alpine", "grade": None}],
    )
    # Captured on 1c5a30f (before this change) with the same fixture.
    assert result[10] == {"risk_score": 6.5, "color_code": "green", "data_status": "ok"}
    assert result[11] == {"risk_score": 6.7, "color_code": "green", "data_status": "ok"}


def _fake_redis(monkeypatch) -> MagicMock:
    pipe = MagicMock()
    client = MagicMock()
    client.pipeline.return_value = pipe
    monkeypatch.setattr(cache_module, "get_redis_client", lambda: client)
    return pipe


def test_bulk_cache_writer_stores_insufficient_explicitly(monkeypatch):
    import json

    pipe = _fake_redis(monkeypatch)
    written = cache_module.set_bulk_cached_safety_scores(
        {1: dict(INSUFFICIENT), 2: {"risk_score": 40.0, "color_code": "yellow", "data_status": "ok"}},
        "2026-09-28",
    )
    assert written == 2
    stored = {call.args[0]: json.loads(call.args[2]) for call in pipe.setex.call_args_list}
    insufficient = stored[cache_module.build_safety_score_key(1, "2026-09-28")]
    assert insufficient["risk_score"] is None
    assert insufficient["color_code"] == "gray"
    assert insufficient["data_status"] == "insufficient_data"
    ok = stored[cache_module.build_safety_score_key(2, "2026-09-28")]
    assert ok["risk_score"] == 40.0 and ok["data_status"] == "ok"


def test_bulk_cache_writer_still_skips_null_score_without_status(monkeypatch):
    pipe = _fake_redis(monkeypatch)
    written = cache_module.set_bulk_cached_safety_scores(
        {1: {"risk_score": None, "color_code": "gray"}, 2: {"risk_score": 12.0, "data_status": "insufficient_data"}},
        "2026-09-28",
    )
    assert written == 0
    pipe.setex.assert_not_called()


async def test_historical_save_raises_after_failed_batches():
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[RuntimeError("insert failed"), SimpleNamespace(rowcount=0)])
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    with pytest.raises(RuntimeError, match="1 historical_predictions batch"):
        await safety_computation_optimized._save_to_historical(db, {5: dict(INSUFFICIENT)}, date(2026, 9, 28))
    db.rollback.assert_awaited()


async def test_historical_save_writes_null_score_and_gray():
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    await safety_computation_optimized._save_to_historical(
        db, {5: dict(INSUFFICIENT), 6: {"risk_score": 30.0, "color_code": "yellow", "data_status": "ok"}}, date(2026, 9, 28)
    )
    insert_params = db.execute.await_args_list[0].args[1]
    rows = {insert_params[f"route_id_{i}"]: (insert_params[f"risk_{i}"], insert_params[f"color_{i}"]) for i in range(2)}
    assert rows == {5: (None, "gray"), 6: (30.0, "yellow")}


# --- live endpoints --------------------------------------------------------------------

def _db_returning(result: MagicMock) -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _safety_route_result() -> MagicMock:
    result = MagicMock()
    result.one_or_none.return_value = SimpleNamespace(
        mp_route_id=7, name="Route 7", type="sport", latitude=40.0, longitude=-105.0
    )
    return result


def _insufficient_prediction() -> PredictionResponse:
    return PredictionResponse(
        num_contributing_accidents=0, top_contributing_accidents=[], metadata={}, **INSUFFICIENT
    )


async def test_live_safety_insufficient_is_returned_and_cached_explicitly(monkeypatch):
    cached = []
    monkeypatch.setattr(mp_routes, "cache_get", lambda _key: None)
    monkeypatch.setattr(mp_routes, "cache_set", lambda _key, data, **_: cached.append(data))
    monkeypatch.setattr(mp_routes, "predict_route_safety", AsyncMock(return_value=_insufficient_prediction()))

    body = await mp_routes.calculate_mp_route_safety(
        7, target_date=date(2026, 9, 28), bypass_cache=False, db=_db_returning(_safety_route_result())
    )

    assert (body.risk_score, body.color_code, body.data_status) == (None, "gray", "insufficient_data")
    assert cached and cached[0]["data_status"] == "insufficient_data" and cached[0]["risk_score"] is None


async def test_live_safety_returns_the_rounded_score_and_its_colour(monkeypatch):
    monkeypatch.setattr(mp_routes, "cache_get", lambda _key: None)
    monkeypatch.setattr(mp_routes, "cache_set", lambda *a, **k: True)
    monkeypatch.setattr(mp_routes, "predict_route_safety", AsyncMock(return_value=SimpleNamespace(risk_score=24.96)))

    body = await mp_routes.calculate_mp_route_safety(
        7, target_date=date(2026, 9, 28), bypass_cache=False, db=_db_returning(_safety_route_result())
    )

    assert (body.risk_score, body.color_code, body.data_status) == (25.0, "yellow", "ok")


async def test_live_safety_serves_stale_cached_zero_as_insufficient(monkeypatch):
    monkeypatch.setattr(mp_routes, "cache_get", lambda _key: {"risk_score": 0.0, "color_code": "green", "status": "cached"})
    predict = AsyncMock()
    monkeypatch.setattr(mp_routes, "predict_route_safety", predict)

    body = await mp_routes.calculate_mp_route_safety(
        7, target_date=date(2026, 9, 28), bypass_cache=False, db=_db_returning(_safety_route_result())
    )

    predict.assert_not_awaited()
    assert (body.risk_score, body.color_code, body.data_status) == (None, "gray", "insufficient_data")


async def test_live_safety_serves_cached_insufficient_without_recompute(monkeypatch):
    monkeypatch.setattr(mp_routes, "cache_get", lambda _key: dict(INSUFFICIENT))
    predict = AsyncMock()
    monkeypatch.setattr(mp_routes, "predict_route_safety", predict)

    body = await mp_routes.calculate_mp_route_safety(
        7, target_date=date(2026, 9, 28), bypass_cache=False, db=_db_returning(_safety_route_result())
    )

    predict.assert_not_awaited()
    assert (body.risk_score, body.color_code, body.data_status) == (None, "gray", "insufficient_data")


async def test_map_with_safety_marks_insufficient_routes_gray(monkeypatch):
    result = MagicMock()
    result.fetchall.return_value = [
        SimpleNamespace(mp_route_id=i, name=f"R{i}", latitude=40.0, longitude=-105.0, grade="5.9", type="sport", location_id=1)
        for i in (1, 2, 3)
    ]
    monkeypatch.setattr(
        mp_routes,
        "get_bulk_cached_safety_scores",
        lambda ids, _date: {1: dict(INSUFFICIENT), 2: {"risk_score": 60.0, "color_code": "orange", "data_status": "ok"}, 3: None},
    )

    body = await mp_routes.get_mp_routes_with_safety(target_date=date(2026, 9, 28), season="rock", db=_db_returning(result))

    by_id = {r.mp_route_id: r for r in body.routes}
    assert by_id[1].safety is not None
    assert (by_id[1].safety.risk_score, by_id[1].safety.color_code, by_id[1].safety.data_status) == (
        None, "gray", "insufficient_data",
    )
    assert by_id[2].safety is not None and by_id[2].safety.data_status == "ok"
    assert by_id[3].safety is None
    assert body.meta.insufficient_routes == 1
    assert body.meta.missing_routes == 1


def _route_coords():
    return AsyncMock(
        return_value=SimpleNamespace(mp_route_id=1, name="R", latitude=40.0, longitude=-105.0, type="sport", location_id=None)
    )


async def test_forecast_days_without_evidence_have_no_risk_number(monkeypatch):
    monkeypatch.setattr(mp_routes, "get_route_with_location_coords", _route_coords())
    monkeypatch.setattr("app.services.elevation_service.fetch_elevation", lambda lat, lon: 2000.0)
    monkeypatch.setattr("app.services.weather_service.fetch_current_weather_pattern", lambda **_: None)
    monkeypatch.setattr(mp_routes, "predict_route_safety", AsyncMock(return_value=_insufficient_prediction()))

    body = await mp_routes.get_route_forecast(1, start_date=date(2026, 9, 28), db=object())

    assert len(body["forecast_days"]) == 7
    for day in body["forecast_days"]:
        assert day["risk_score"] is None
        assert day["color_code"] == "gray"
        assert day["data_status"] == "insufficient_data"


async def test_forecast_days_with_evidence_carry_ok_status(monkeypatch):
    monkeypatch.setattr(mp_routes, "get_route_with_location_coords", _route_coords())
    monkeypatch.setattr("app.services.elevation_service.fetch_elevation", lambda lat, lon: 2000.0)
    monkeypatch.setattr("app.services.weather_service.fetch_current_weather_pattern", lambda **_: None)
    monkeypatch.setattr(mp_routes, "predict_route_safety", AsyncMock(return_value=SimpleNamespace(risk_score=30.04)))

    body = await mp_routes.get_route_forecast(1, start_date=date(2026, 9, 28), db=object())

    day = body["forecast_days"][0]
    assert (day["risk_score"], day["color_code"], day["data_status"]) == (30.0, "yellow", "ok")


class _FakeWeatherResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {
            "hourly": {
                "time": ["2026-01-01T09:00", "2026-01-01T10:00", "2026-01-01T11:00"],
                "temperature_2m": [0.0] * 3,
                "precipitation": [0.0] * 3,
                "wind_speed_10m": [0.0] * 3,
                "wind_gusts_10m": [0.0] * 3,
                "cloud_cover": [0.0] * 3,
                "visibility": [10000.0] * 3,
            }
        }


async def test_hourly_without_evidence_has_no_risk_and_unknown_climbability(monkeypatch):
    monkeypatch.setattr(mp_routes, "get_route_with_location_coords", _route_coords())
    monkeypatch.setattr("app.services.elevation_service.fetch_elevation", lambda lat, lon: 2000.0)
    monkeypatch.setattr("requests.get", lambda *a, **kw: _FakeWeatherResponse())
    monkeypatch.setattr(mp_routes, "predict_route_safety", AsyncMock(return_value=_insufficient_prediction()))

    body = await mp_routes.get_time_of_day_analysis(1, date(2026, 1, 1), db=object())

    assert body["data_status"] == "insufficient_data"
    assert body["base_daily_risk"] is None
    assert body["climbing_windows"] == []
    assert body["best_window"] is None
    assert body["recommendation"] == INSUFFICIENT_DATA_MESSAGE + "."
    for hour in body["hourly_data"]:
        assert hour["risk_score"] is None
        assert hour["is_climbable"] is None
        assert hour["data_status"] == "insufficient_data"


async def test_risk_breakdown_without_evidence_has_no_score(monkeypatch):
    monkeypatch.setattr(mp_routes, "get_route_with_location_coords", _route_coords())
    monkeypatch.setattr("app.services.elevation_service.fetch_elevation", lambda lat, lon: 2000.0)
    monkeypatch.setattr(mp_routes, "predict_route_safety", AsyncMock(return_value=_insufficient_prediction()))

    body = await mp_routes.get_risk_breakdown(1, target_date=date(2026, 9, 28), db=object())

    assert body["risk_score"] is None
    assert body["data_status"] == "insufficient_data"
    assert body["factors"] == []
    assert body["message"] == INSUFFICIENT_DATA_MESSAGE


async def test_historical_trends_reports_insufficient_days_without_scoring_them(monkeypatch):
    monkeypatch.setattr(
        mp_routes,
        "get_route_with_location_coords",
        AsyncMock(return_value=SimpleNamespace(name="R", latitude=None, longitude=None)),
    )
    result = MagicMock()
    result.fetchall.return_value = [
        (date(2026, 9, 1), None, "gray"),
        (date(2026, 9, 2), 30.0, "yellow"),
        # Rows written before 0003 stored "no evidence" as 0.0 green: read as insufficient.
        (date(2026, 9, 3), 0.0, "green"),
    ]

    body = await mp_routes.get_historical_trends(1, days=30, target_date=date(2026, 9, 4), db=_db_returning(result))

    insufficient = {"risk_score": None, "color_code": "gray", "data_status": "insufficient_data"}
    assert body["historical_predictions"] == [
        {"date": "2026-09-01", **insufficient},
        {"date": "2026-09-02", "risk_score": 30.0, "color_code": "yellow", "data_status": "ok"},
        {"date": "2026-09-03", **insufficient},
    ]
    assert body["summary"] == {"avg_risk": 30.0, "min_risk": 30.0, "max_risk": 30.0}
    assert body["days_available"] == 1


async def test_historical_trend_compares_first_and_last_seven_scored_days(monkeypatch):
    monkeypatch.setattr(
        mp_routes,
        "get_route_with_location_coords",
        AsyncMock(return_value=SimpleNamespace(name="R", latitude=None, longitude=None)),
    )
    rows = [(date(2026, 8, d), 10.0, "green") for d in range(1, 8)]
    rows += [(date(2026, 8, 8 + d), 0.0, "green") for d in range(5)]  # legacy no-evidence days
    rows += [(date(2026, 8, 20 + d), 40.0, "yellow") for d in range(7)]
    result = MagicMock()
    result.fetchall.return_value = rows

    body = await mp_routes.get_historical_trends(1, days=60, target_date=date(2026, 8, 30), db=_db_returning(result))

    assert body["days_available"] == 14
    assert body["trend"]["direction"] == "increasing"
    assert "7 scored days" in body["trend"]["description"]


# --- cache warming ---------------------------------------------------------------------

class _FakeAsyncSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        route = SimpleNamespace(mp_route_id=1, name="Test Route", latitude=40.0, longitude=-105.0, type="sport")
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [route]))


async def test_cache_warming_stores_insufficient_explicitly(monkeypatch):
    monkeypatch.setattr(cache_warming, "AsyncSessionLocal", lambda: _FakeAsyncSession())
    monkeypatch.setattr(cache_warming.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(cache_warming, "predict_route_safety", AsyncMock(return_value=_insufficient_prediction()))
    cached = []
    monkeypatch.setattr(cache_warming, "cache_set", lambda key, data, ttl_seconds=None: cached.append(data))

    stats = await cache_warming._warm_cache_async()

    assert stats["total_failed"] == 0
    assert cached
    for data in cached:
        assert (data["risk_score"], data["color_code"], data["data_status"]) == (None, "gray", "insufficient_data")
