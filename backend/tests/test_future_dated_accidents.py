"""Accidents dated after the current UTC day are data errors: every scorer gives them weight 0.

"Today" is read per call (never frozen at import), so each test injects or patches it.
"""
from datetime import date, timedelta

import numpy as np
import pytest

from app.services import (
    location_safety_computation as loc,
    safety_algorithm,
    safety_algorithm_vectorized as vec,
    temporal_weighting,
)
from app.services.safety_algorithm import AccidentData

TODAY = date(2026, 9, 28)
TOMORROW = TODAY + timedelta(days=1)


def _accident(accident_id: int, when: date) -> AccidentData:
    return AccidentData(
        accident_id=accident_id, latitude=40.0, longitude=-105.0, elevation_meters=None,
        accident_date=when, route_type="trad", severity="serious", weather_pattern=None, grade=None,
    )


def _loc_dict(accident_id: int, when: date) -> dict:
    return {"accident_id": accident_id, "latitude": 40.0, "longitude": -105.0, "elevation_m": None,
            "accident_date": when, "route_type": "trad", "severity": "serious", "grade": None}


def test_utc_today_is_read_on_every_call(monkeypatch):
    class Clock:
        now_value = TODAY

        @classmethod
        def now(cls, tz):
            from datetime import datetime
            return datetime(cls.now_value.year, cls.now_value.month, cls.now_value.day, 12, tzinfo=tz)

    monkeypatch.setattr(temporal_weighting, "datetime", Clock)
    assert temporal_weighting.utc_today() == TODAY
    Clock.now_value = TOMORROW
    assert temporal_weighting.utc_today() == TOMORROW


def test_vectorized_temporal_weights_zero_tomorrow_keep_today():
    weights, _ = vec.calculate_temporal_weights_vectorized(
        TODAY, np.array([TODAY, TOMORROW]), "trad", today=TODAY
    )
    assert weights[0] > 0
    assert weights[1] == 0.0


@pytest.mark.parametrize("scorer", [vec.calculate_safety_score_vectorized, safety_algorithm.calculate_safety_score])
def test_route_scorers_exclude_tomorrow_and_the_cutoff_moves(scorer):
    accidents = [_accident(1, TODAY), _accident(2, TOMORROW)]
    kwargs = dict(route_lat=40.0, route_lon=-105.0, route_elevation_m=None, route_type="trad",
                  current_date=TOMORROW, current_weather=None, accidents=accidents)

    today_run = scorer(**kwargs, today=TODAY)
    assert today_run.num_contributing_accidents == 1
    assert {a["accident_id"] for a in today_run.top_contributing_accidents} == {1}

    next_day_run = scorer(**kwargs, today=TOMORROW)
    assert next_day_run.num_contributing_accidents == 2


@pytest.mark.parametrize("scorer", [vec.calculate_safety_score_vectorized, safety_algorithm.calculate_safety_score])
def test_route_scorers_default_to_the_patched_utc_day(monkeypatch, scorer):
    module = vec if scorer is vec.calculate_safety_score_vectorized else safety_algorithm
    monkeypatch.setattr(module, "utc_today", lambda: TODAY)
    result = scorer(route_lat=40.0, route_lon=-105.0, route_elevation_m=None, route_type="trad",
                    current_date=TOMORROW, current_weather=None,
                    accidents=[_accident(1, TODAY), _accident(2, TOMORROW)])
    assert result.num_contributing_accidents == 1


def test_location_base_score_excludes_tomorrow_and_the_cutoff_moves(monkeypatch):
    accidents = [_loc_dict(1, TODAY), _loc_dict(2, TOMORROW)]
    args = dict(location_id=1, location_lat=40.0, location_lon=-105.0, location_elevation_m=None,
                target_date=TOMORROW, accidents=accidents, weather_similarity_map={})

    result = loc.compute_location_base_score(**args, today=TODAY)
    assert result.accident_base_influences[1] > 0
    assert result.accident_base_influences[2] == 0.0
    assert loc.compute_location_base_score(**args, today=TOMORROW).accident_base_influences[2] > 0

    monkeypatch.setattr(loc, "utc_today", lambda: TODAY)
    assert loc.compute_location_base_score(**args).accident_base_influences[2] == 0.0


def test_vectorized_location_base_score_excludes_tomorrow_and_the_cutoff_moves(monkeypatch):
    arrays = loc.prepare_accident_arrays([_loc_dict(1, TODAY), _loc_dict(2, TOMORROW)])
    args = dict(location_id=1, location_lat=40.0, location_lon=-105.0, location_elevation_m=None,
                target_date=TOMORROW, accident_arrays=arrays, weather_similarity_map={})

    result = loc.compute_location_base_score_vectorized(**args, today=TODAY)
    assert set(result.accident_base_influences) == {1}
    assert set(loc.compute_location_base_score_vectorized(**args, today=TOMORROW).accident_base_influences) == {1, 2}

    monkeypatch.setattr(loc, "utc_today", lambda: TODAY)
    assert set(loc.compute_location_base_score_vectorized(**args).accident_base_influences) == {1}
