"""One risk-band definition shared by every colour/label path, and parity with the frontend."""
import re
from pathlib import Path

import pytest

from app.api.v1.mp_routes import get_safety_color_code
from app.services.location_safety_computation import compute_batch_route_scores
from app.services.risk_bands import RISK_BAND_THRESHOLDS, color_code_for, valid_risk_score

FRONTEND_RISK_UTILS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "utils" / "riskUtils.ts"

BOUNDARY_CASES = [
    (0.0, "green"),
    (24.99, "green"),
    (25.0, "yellow"),
    (49.99, "yellow"),
    (50.0, "orange"),
    (74.99, "orange"),
    (75.0, "red"),
    (100.0, "red"),
]


def test_owner_decided_bands():
    assert RISK_BAND_THRESHOLDS == (25.0, 50.0, 75.0)


@pytest.mark.parametrize("score,expected", BOUNDARY_CASES)
def test_color_code_for_boundaries(score, expected):
    assert color_code_for(score) == expected


@pytest.mark.parametrize("score,expected", BOUNDARY_CASES)
def test_live_safety_path_uses_shared_bands(score, expected):
    assert get_safety_color_code(score) == expected


@pytest.mark.parametrize("score,expected", BOUNDARY_CASES)
def test_nightly_batch_path_uses_shared_bands(monkeypatch, score, expected):
    monkeypatch.setattr(
        "app.services.location_safety_computation.compute_route_risk_score",
        lambda **_: (score, {}),
    )
    result = compute_batch_route_scores({}, [{"route_id": 1, "route_type": "sport", "grade": None}])
    assert result[1]["color_code"] == expected


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 100.1])
def test_color_code_for_rejects_invalid_scores(bad):
    with pytest.raises(ValueError):
        color_code_for(bad)


@pytest.mark.parametrize(
    "value,expected",
    [(0, 0.0), (42.5, 42.5), (100, 100.0), (None, None), ("42", None), (True, None),
     (float("nan"), None), (-1, None), (101, None)],
)
def test_valid_risk_score(value, expected):
    assert valid_risk_score(value) == expected


def test_frontend_bands_match_backend():
    # Fails when frontend and backend bands drift. Parsing the TS source keeps both
    # deploy units self-contained (Vercel builds frontend/ only, the Docker image
    # copies backend/ only), which a repo-root shared JSON file would break.
    if not FRONTEND_RISK_UTILS.exists():
        pytest.skip("frontend source not present (backend-only checkout)")
    source = FRONTEND_RISK_UTILS.read_text()
    match = re.search(r"export const RISK_BAND_THRESHOLDS\s*=\s*\[([^\]]*)\]", source)
    assert match, "RISK_BAND_THRESHOLDS not found in frontend/src/utils/riskUtils.ts"
    frontend = tuple(float(v) for v in match.group(1).split(",") if v.strip())
    assert frontend == RISK_BAND_THRESHOLDS


async def test_historical_trends_rederives_stored_colour(monkeypatch):
    # A row written under the old 30/50/70 live bands stored "green" for 27.0.
    from datetime import date
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.api.v1 import mp_routes

    monkeypatch.setattr(
        mp_routes,
        "get_route_with_location_coords",
        AsyncMock(return_value=SimpleNamespace(name="R", latitude=None, longitude=None)),
    )
    result = MagicMock()
    result.fetchall.return_value = [(date(2026, 9, 1), 27.0, "green")]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    body = await mp_routes.get_historical_trends(1, days=30, target_date=date(2026, 9, 2), db=db)

    assert body["historical_predictions"][0]["color_code"] == "yellow"
