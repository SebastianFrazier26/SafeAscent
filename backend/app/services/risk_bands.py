"""Single risk-band definition for every colour code the backend emits.

Owner decision 2026-09-28: 25/50/75 everywhere until Phase 3. Edges are
lower-inclusive (25.0 is yellow), matching the nightly job's historical `<`
semantics. Mirrored by RISK_BAND_THRESHOLDS in frontend/src/utils/riskUtils.ts;
tests/test_risk_bands.py fails if the two drift.

Phase 3 MVP-1 (2026-10-15): the interim raw<0.05 rule retired; insufficient is a stored model
state (model.route_scores.insufficient), never a threshold on a score. A stored percentile of 0.0
is a real rank and shows as 0.0.
"""
import math
from typing import Literal

RiskColorCode = Literal["green", "yellow", "orange", "red"]
SafetyColorCode = Literal["green", "yellow", "orange", "red", "gray"]
DataStatus = Literal["ok", "insufficient_data"]

INSUFFICIENT_DATA_COLOR: Literal["gray"] = "gray"
INSUFFICIENT_DATA_MESSAGE = "Too little evidence to estimate risk yet"
BAND_COLOR_CODES: frozenset[str] = frozenset({"green", "yellow", "orange", "red"})

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


def check_score_status(risk_score: float | None, color_code: str, data_status: str) -> None:
    """Raise ValueError unless (score, colour, status) is one of the two legal shapes."""
    if data_status == "insufficient_data":
        if risk_score is not None or color_code != INSUFFICIENT_DATA_COLOR:
            raise ValueError("insufficient_data requires risk_score=None and color_code='gray'")
    elif data_status == "ok":
        if risk_score is None or color_code not in BAND_COLOR_CODES:
            raise ValueError("ok requires a 0-100 risk_score and a band color_code (never gray)")
    else:
        raise ValueError(f"unknown data_status: {data_status!r}")
