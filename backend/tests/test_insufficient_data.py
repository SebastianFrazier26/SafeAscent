"""Owner decision 2026-09-28: a route with too little evidence is "insufficient data" (null
score, gray), never a 0.0 green. For the kernel (still in /predict's internals, the nightly batch
and the warmer until Task 23) that means no contributing accident or a raw score < 0.05. For the
stored Phase 3 scores the API serves (Task 22) it means the stored row has no evidence at all;
the 0.05 floor does not apply to a stored percentile.
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
from app.services import model_scores
from app.services.model_scores import StoredScore, to_risk
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
# The stored-score contract (P3 Task 22): an insufficient result says why and carries no
# ok-only field; an ok result carries confidence, a range bracketing the score, and evidence.

STORED_INSUFFICIENT = {**INSUFFICIENT, "insufficient_reason": "no_evidence", "model_version": "eb_v1-test"}
STORED_OK = {"risk_score": 42.0, "color_code": "yellow", "data_status": "ok", "confidence": "low",
             "score_range": (30.0, 55.0), "evidence": {"geo": 0.5, "similar": 2.0}, "model_version": "eb_v1-test"}


def _safety_response(**kw):
    return MpRouteSafetyResponse(route_id=1, route_name="R", target_date="2026-09-28", **kw)


def _prediction_response(**kw):
    return PredictionResponse(num_contributing_accidents=0, top_contributing_accidents=[], metadata={}, **kw)


@pytest.mark.parametrize("build", [_safety_response, SafetyScore])
def test_schemas_accept_insufficient_shape(build):
    body = build(**STORED_INSUFFICIENT)
    assert body.risk_score is None
    assert body.color_code == "gray"
    assert body.data_status == "insufficient_data"
    assert body.insufficient_reason == "no_evidence"


@pytest.mark.parametrize("build", [_safety_response, SafetyScore])
def test_schemas_default_status_is_ok_for_a_real_score(build):
    body = build(**{k: v for k, v in STORED_OK.items() if k != "data_status"})
    assert body.data_status == "ok"


def test_kernel_prediction_schema_keeps_its_two_shapes():
    assert _prediction_response(**INSUFFICIENT).risk_score is None
    assert _prediction_response(risk_score=42.0, color_code="yellow").data_status == "ok"


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


@pytest.mark.parametrize("build", [_safety_response, SafetyScore])
@pytest.mark.parametrize(
    "over",
    [
        {"confidence": None},
        {"score_range": None},
        {"evidence": None},
        {"score_range": (43.0, 55.0)},            # range above the score
        {"score_range": (30.0, 41.9)},            # range below the score
        {"score_range": (-1.0, 55.0)},
        {"score_range": (30.0, 100.5)},
        {"score_range": (float("nan"), 55.0)},
        {"insufficient_reason": "no_evidence"},
        {"confidence": "insufficient"},
        {"evidence": {"geo": -0.1, "similar": 2.0}},
        {"evidence": {"geo": float("inf"), "similar": 2.0}},
        {"relative_rate": {"mean": 1.0, "lo": 1.5, "hi": 2.0}},
    ],
)
def test_stored_ok_score_rejects_a_broken_contract(build, over):
    with pytest.raises(ValidationError):
        build(**{**STORED_OK, **over})


@pytest.mark.parametrize("build", [_safety_response, SafetyScore])
@pytest.mark.parametrize(
    "over",
    [
        {"insufficient_reason": None},
        {"confidence": "low"},
        {"score_range": (0.0, 10.0)},
        {"evidence": {"geo": 0.0, "similar": 0.0}},
        {"relative_rate": {"mean": 1.0, "lo": 0.5, "hi": 2.0}},
        {"risk_score": 0.0},
    ],
)
def test_stored_insufficient_rejects_any_number(build, over):
    with pytest.raises(ValidationError):
        build(**{**STORED_INSUFFICIENT, **over})


@pytest.mark.parametrize("build", [_safety_response, SafetyScore])
def test_range_may_touch_the_score(build):
    body = build(**{**STORED_OK, "score_range": (42.0, 42.0)})
    assert body.score_range == (42.0, 42.0)


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


# --- live endpoints (stored scores, P3 Task 22) -----------------------------------------

def _stored(route_id: int = 7, *, insufficient: bool = False, percentile: float = 30.04, month: int = 9):
    return StoredScore(
        route_id=route_id, month=month, model_version="eb_v1-test", type_group="trad", insufficient=insufficient,
        insufficient_reason="no_evidence" if insufficient else None,
        rr_mean=None if insufficient else 1.1, rr_q05=None if insufficient else 0.6,
        rr_q95=None if insufficient else 1.9, percentile=None if insufficient else percentile,
        pct_q05=None if insufficient else max(0.0, percentile - 10.04),
        pct_q95=None if insufficient else min(100.0, percentile + 14.96),
        conf_level="insufficient" if insufficient else "medium", n_eff_geo=0.0 if insufficient else 1.0,
        n_eff_feature=0.0 if insufficient else 2.0, per10k_mean=None, per10k_q05=None, per10k_q95=None,
        per10k_reason=None if insufficient else "ticks_incomplete",
    )


def _db_returning(result: MagicMock) -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _named(name: str = "Route 7") -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = name
    return _db_returning(result)


def test_stored_insufficient_row_is_gray_with_no_number():
    assert to_risk(_stored(insufficient=True)) == (None, "gray", "insufficient_data")


@pytest.mark.parametrize("percentile,expected", [(0.0, (0.0, "green", "ok")), (0.04, (0.0, "green", "ok")),
                                                 (24.96, (25.0, "yellow", "ok"))])
def test_stored_ok_row_shows_its_rank_without_the_kernel_floor(percentile, expected):
    # MIN_ESTIMABLE_SCORE (0.05) was a kernel rule (A:144): a stored percentile of 0.04 is the
    # route's real rank among its type, not "too little evidence". Only an insufficient row is gray.
    assert to_risk(_stored(percentile=percentile)) == expected


async def test_live_safety_insufficient_row_is_served_as_insufficient(monkeypatch):
    monkeypatch.setattr(model_scores, "get_score", AsyncMock(return_value=_stored(insufficient=True)))

    body = await mp_routes.calculate_mp_route_safety(7, target_date=date(2026, 9, 28), bypass_cache=False, db=_named())

    assert (body.risk_score, body.color_code, body.data_status) == (None, "gray", "insufficient_data")
    assert body.insufficient_reason == "no_evidence" and body.confidence is None


async def test_live_safety_returns_the_rounded_score_and_its_colour(monkeypatch):
    monkeypatch.setattr(model_scores, "get_score", AsyncMock(return_value=_stored(percentile=24.96)))

    body = await mp_routes.calculate_mp_route_safety(7, target_date=date(2026, 9, 28), bypass_cache=False, db=_named())

    assert (body.risk_score, body.color_code, body.data_status) == (25.0, "yellow", "ok")
    assert body.score_range == (14.9, 39.9) and body.confidence == "medium"


async def test_live_safety_bypass_cache_is_ignored(monkeypatch):
    get = AsyncMock(return_value=_stored())
    monkeypatch.setattr(model_scores, "get_score", get)

    a = await mp_routes.calculate_mp_route_safety(7, target_date=date(2026, 9, 28), bypass_cache=True, db=_named())
    b = await mp_routes.calculate_mp_route_safety(7, target_date=date(2026, 9, 28), bypass_cache=False, db=_named())

    assert a == b
    assert [c.args[1:] for c in get.await_args_list] == [(7, 9), (7, 9)]


async def test_map_with_safety_marks_insufficient_routes_gray(monkeypatch):
    result = MagicMock()
    result.fetchall.return_value = [
        SimpleNamespace(mp_route_id=i, name=f"R{i}", latitude=40.0, longitude=-105.0, grade="5.9", type="sport", location_id=1)
        for i in (1, 2, 3)
    ]
    monkeypatch.setattr(model_scores, "active_version", AsyncMock(return_value="eb_v1-test"))
    monkeypatch.setattr(model_scores, "get_scores", AsyncMock(
        return_value={1: _stored(1, insufficient=True), 2: _stored(2, percentile=60.0)}))

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
    assert body.meta.computed_routes == 0 and body.meta.model_version == "eb_v1-test"


def _insufficient_prediction() -> PredictionResponse:
    return PredictionResponse(
        num_contributing_accidents=0, top_contributing_accidents=[], metadata={}, **INSUFFICIENT
    )


def _route_coords():
    return AsyncMock(
        return_value=SimpleNamespace(mp_route_id=1, name="R", latitude=40.0, longitude=-105.0, type="sport", location_id=None)
    )


async def test_forecast_days_without_evidence_have_no_risk_number(monkeypatch):
    monkeypatch.setattr(mp_routes, "get_route_with_location_coords", _route_coords())
    monkeypatch.setattr("app.services.elevation_service.fetch_elevation", lambda lat, lon: 2000.0)
    monkeypatch.setattr("app.services.weather_service.fetch_current_weather_pattern", lambda **_: None)
    monkeypatch.setattr(model_scores, "active_version", AsyncMock(return_value="eb_v1-test"))
    monkeypatch.setattr(model_scores, "get_score", AsyncMock(return_value=_stored(insufficient=True)))

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
    monkeypatch.setattr(model_scores, "active_version", AsyncMock(return_value="eb_v1-test"))
    monkeypatch.setattr(model_scores, "get_score", AsyncMock(return_value=_stored(percentile=30.04)))

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


async def test_hourly_has_no_risk_and_unknown_climbability(monkeypatch):
    monkeypatch.setattr(mp_routes, "get_route_with_location_coords", _route_coords())
    monkeypatch.setattr("requests.get", lambda *a, **kw: _FakeWeatherResponse())
    get = AsyncMock()
    monkeypatch.setattr(model_scores, "get_score", get)

    body = await mp_routes.get_time_of_day_analysis(1, date(2026, 1, 1), db=object())

    get.assert_not_awaited()  # never a number derived from the daily score
    assert body["base_daily_risk"] is None
    assert body["climbing_windows"] == []
    assert body["best_window"] is None
    for hour in body["hourly_data"]:
        assert hour["risk_score"] is None and hour["risk"] is None
        assert hour["is_climbable"] is None
        assert hour["risk_note"]


async def test_risk_breakdown_without_evidence_has_no_score(monkeypatch):
    monkeypatch.setattr(model_scores, "get_score", AsyncMock(return_value=_stored(insufficient=True)))

    body = await mp_routes.get_risk_breakdown(1, target_date=date(2026, 9, 28), db=_named("R"))

    assert body["risk_score"] is None
    assert body["data_status"] == "insufficient_data"
    assert body["terms"] == []
    assert body["message"] == INSUFFICIENT_DATA_MESSAGE


async def test_historical_profile_reports_insufficient_months_without_scoring_them(monkeypatch):
    monkeypatch.setattr(
        mp_routes,
        "get_route_with_location_coords",
        AsyncMock(return_value=SimpleNamespace(name="R", latitude=None, longitude=None)),
    )
    monkeypatch.setattr(model_scores, "get_profile", AsyncMock(return_value=(
        "eb_v1-test", {1: _stored(month=1, insufficient=True), 2: _stored(month=2, percentile=30.0)})))

    body = await mp_routes.get_historical_trends(1, days=30, target_date=date(2026, 9, 4), db=MagicMock())

    profile = {m["month"]: m for m in body["monthly_profile"]}
    assert (profile[1]["risk_score"], profile[1]["color_code"], profile[1]["data_status"]) == (
        None, "gray", "insufficient_data")
    assert profile[1]["insufficient_reason"] == "no_evidence" and profile[1]["score_range"] is None
    assert (profile[2]["risk_score"], profile[2]["color_code"], profile[2]["data_status"]) == (30.0, "yellow", "ok")
    assert body["summary"] is None and body["trend"] is None


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
