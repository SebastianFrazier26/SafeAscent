"""Owner decision (2026-09-28): the hourly `is_climbable` cut-off moves from
`hourly_risk < 70` to `hourly_risk < 75`, taken from RISK_BAND_THRESHOLDS'
high-band edge rather than a new literal. See risk-bands-report.md.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import mp_routes
from app.services.risk_bands import RISK_BAND_THRESHOLDS


class _FakeWeatherResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _single_hour_weather_payload():
    # One daylight hour with no adjustment inputs, so hourly_risk == base_risk exactly.
    return {
        "hourly": {
            "time": ["2026-01-01T06:00"],
            "temperature_2m": [0.0],
            "precipitation": [0.0],
            "wind_speed_10m": [0.0],
            "wind_gusts_10m": [0.0],
            "cloud_cover": [0.0],
            "visibility": [10000.0],
        }
    }


async def _run_time_of_day(monkeypatch, base_risk: float):
    monkeypatch.setattr(
        mp_routes,
        "get_route_with_location_coords",
        AsyncMock(
            return_value=SimpleNamespace(
                mp_route_id=1, name="R", latitude=40.0, longitude=-105.0, type="sport", location_id=None,
            )
        ),
    )
    monkeypatch.setattr("app.services.elevation_service.fetch_elevation", lambda lat, lon: 2000.0)
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **kw: _FakeWeatherResponse(_single_hour_weather_payload()),
    )
    monkeypatch.setattr(
        mp_routes,
        "predict_route_safety",
        AsyncMock(return_value=SimpleNamespace(risk_score=base_risk)),
    )

    result = await mp_routes.get_time_of_day_analysis(1, date(2026, 1, 1), db=object())
    return result["hourly_data"][0]


def test_high_band_edge_is_source_of_truth():
    assert RISK_BAND_THRESHOLDS[2] == 75.0


async def test_hourly_climbable_just_below_high_band(monkeypatch):
    hour = await _run_time_of_day(monkeypatch, 74.9)
    assert hour["is_climbable"] is True


async def test_hourly_climbable_at_high_band_edge(monkeypatch):
    hour = await _run_time_of_day(monkeypatch, 75.0)
    assert hour["is_climbable"] is False
