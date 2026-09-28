"""Single risk-band definition for every colour code the backend emits.

Owner decision 2026-09-28: 25/50/75 everywhere until Phase 3. Edges are
lower-inclusive (25.0 is yellow), matching the nightly job's historical `<`
semantics. Mirrored by RISK_BAND_THRESHOLDS in frontend/src/utils/riskUtils.ts;
tests/test_risk_bands.py fails if the two drift.
"""
import math
from typing import Literal

RiskColorCode = Literal["green", "yellow", "orange", "red"]
SafetyColorCode = Literal["green", "yellow", "orange", "red", "gray"]
DataStatus = Literal["ok", "insufficient_data"]

# Owner decisions 2026-09-28: with too little evidence the radius-based algorithm has nothing
# to say, so the route is "insufficient_data" (null score, gray), never 0.0 green. "Too little"
# is no contributing accident, or a raw score below 0.05, i.e. one that would display as 0.0.
# Interim until the Phase 3 model borrows evidence from similar routes anywhere.
INSUFFICIENT_DATA_COLOR: Literal["gray"] = "gray"
INSUFFICIENT_DATA_MESSAGE = "Too little evidence to estimate risk yet"
BAND_COLOR_CODES: frozenset[str] = frozenset({"green", "yellow", "orange", "red"})
MIN_ESTIMABLE_SCORE = 0.05

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


def estimable_score(num_contributing: int, raw_score: float | None) -> float | None:
    """The raw score if there is enough evidence to show it, else None (insufficient_data).

    The one evidence rule for /predict, live safety, the nightly batch, cache warming,
    forecast/hourly and stored/cached reads.
    """
    if num_contributing <= 0 or raw_score is None:
        return None
    if not math.isfinite(raw_score) or raw_score < MIN_ESTIMABLE_SCORE:
        return None
    return raw_score


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


CachedSafety = tuple[float | None, SafetyColorCode, DataStatus]


def displayed_risk(raw_score: float | None) -> CachedSafety:
    """(1-decimal score, its colour, "ok") for a real score, else the insufficient_data triple.

    A score below MIN_ESTIMABLE_SCORE would display as 0.0, so it is insufficient too.
    """
    if raw_score is None or estimable_score(1, raw_score) is None:
        return (None, INSUFFICIENT_DATA_COLOR, "insufficient_data")
    shown = round(raw_score, 1)
    return (shown, color_code_for(shown), "ok")


def cached_safety(entry: object) -> CachedSafety | None:
    """Parse a cached/stored safety entry; None means a miss (recompute or show nothing)."""
    if not isinstance(entry, dict):
        return None
    status = entry.get("data_status", "ok")
    if status == "insufficient_data":
        if entry.get("risk_score") is None:
            return (None, INSUFFICIENT_DATA_COLOR, "insufficient_data")
        return None
    if status != "ok":
        return None
    score = valid_risk_score(entry.get("risk_score"))
    if score is None:
        return None
    # Colour is re-derived so entries written under older band sets can't disagree, and a
    # pre-deploy 0.0 ("no evidence" before this state existed) reads as insufficient.
    return displayed_risk(score)
