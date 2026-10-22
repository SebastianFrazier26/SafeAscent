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

# compute_batch_route_scores rounds to 1 decimal before deriving colour (2026-09-28
# fix: colour must match the stored rounded value), so an epsilon-below-boundary raw
# score like 24.99 rounds *up* to the boundary itself. Cases already at 1-decimal
# precision round-trip unchanged and still exercise both sides of each edge.
ROUNDED_BOUNDARY_CASES = [
    (0.0, "green"),
    (24.9, "green"),
    (25.0, "yellow"),
    (49.9, "yellow"),
    (50.0, "orange"),
    (74.9, "orange"),
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


@pytest.mark.parametrize("score,expected", ROUNDED_BOUNDARY_CASES)
def test_nightly_batch_path_uses_shared_bands(monkeypatch, score, expected):
    monkeypatch.setattr(
        "app.services.location_safety_computation.compute_route_risk_score",
        lambda **_: (score, {}),
    )
    result = compute_batch_route_scores({}, [{"route_id": 1, "route_type": "sport", "grade": None}])
    assert result[1]["color_code"] == expected


def test_nightly_batch_colour_matches_stored_rounded_score(monkeypatch):
    # Raw 24.96 rounds (for storage) to 25.0, which is yellow. The colour must
    # agree with what's actually stored, not with the unrounded raw value (green).
    monkeypatch.setattr(
        "app.services.location_safety_computation.compute_route_risk_score",
        lambda **_: (24.96, {}),
    )
    result = compute_batch_route_scores({}, [{"route_id": 1, "route_type": "sport", "grade": None}])
    assert result[1]["risk_score"] == 25.0
    assert result[1]["color_code"] == "yellow"


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


ROUNDED_PERCENTILE_CASES = [(24.94, 24.9, "green"), (24.96, 25.0, "yellow"), (49.96, 50.0, "orange"),
                            (74.96, 75.0, "red"), (100.0, 100.0, "red")]


@pytest.mark.parametrize("percentile,shown,expected", ROUNDED_PERCENTILE_CASES)
def test_stored_score_path_uses_shared_bands_on_the_shown_value(percentile, shown, expected):
    # The stored percentile is shown at 1 decimal; its colour is the band of that shown value.
    from app.services.model_scores import StoredScore, to_risk

    row = StoredScore(route_id=900000001, month=7, model_version="v", type_group="trad", insufficient=False,
                      insufficient_reason=None, rr_mean=1.0, rr_q05=0.5, rr_q95=2.0, percentile=percentile,
                      pct_q05=0.0, pct_q95=100.0, conf_level="medium", n_eff_geo=1.0, n_eff_feature=1.0,
                      per10k_mean=None, per10k_q05=None, per10k_q95=None, per10k_reason="ticks_missing")
    assert to_risk(row) == (shown, expected, "ok")
