"""Single risk-band definition for every colour code the backend emits.

Owner decision 2026-09-28: 25/50/75 everywhere until Phase 3. Edges are
lower-inclusive (25.0 is yellow), matching the nightly job's historical `<`
semantics. Mirrored by RISK_BAND_THRESHOLDS in frontend/src/utils/riskUtils.ts;
tests/test_risk_bands.py fails if the two drift.
"""
import math
from typing import Literal

RiskColorCode = Literal["green", "yellow", "orange", "red"]

RISK_BAND_THRESHOLDS: tuple[float, float, float] = (25.0, 50.0, 75.0)


def valid_risk_score(value: object) -> float | None:
    """Return value as a float if it is a real 0-100 score, else None (never a default)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    if not math.isfinite(score) or score < 0.0 or score > 100.0:
        return None
    return score


def color_code_for(score: float) -> RiskColorCode:
    checked = valid_risk_score(score)
    if checked is None:
        raise ValueError(f"not a 0-100 risk score: {score!r}")
    low, moderate, high = RISK_BAND_THRESHOLDS
    if checked < low:
        return "green"
    if checked < moderate:
        return "yellow"
    if checked < high:
        return "orange"
    return "red"
