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

# Owner decision 2026-09-28: with no contributing evidence the radius-based algorithm has
# nothing to say, so the route is "insufficient_data" (null score, gray), never 0.0 green.
# Interim until the Phase 3 model borrows evidence from similar routes anywhere.
INSUFFICIENT_DATA_COLOR: Literal["gray"] = "gray"
BAND_COLOR_CODES: frozenset[str] = frozenset({"green", "yellow", "orange", "red"})
# Same floor the nightly batch already uses to drop an accident's base influence as noise,
# so /predict and the batch agree on what "no evidence" means.
MIN_EVIDENCE_INFLUENCE = 1e-6

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


def has_evidence(num_contributing: int, total_influence: float | None) -> bool:
    """True if the algorithm had at least one contributing accident with non-negligible weight."""
    if num_contributing <= 0:
        return False
    if total_influence is None:
        return True
    return math.isfinite(total_influence) and total_influence > MIN_EVIDENCE_INFLUENCE


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
    # Colour is re-derived so entries written under older band sets can't disagree.
    return (score, color_code_for(score), "ok")
