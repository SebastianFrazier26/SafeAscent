"""
Pydantic schemas for MpRoute API requests/responses.
"""
import math
from typing import Literal, Optional, Self
from pydantic import BaseModel, Field, model_validator

from app.services.risk_bands import DataStatus, SafetyColorCode, check_score_status


class MpRouteBase(BaseModel):
    """Base route schema with common fields."""
    name: str
    url: Optional[str] = None
    location_id: Optional[int] = None
    grade: Optional[str] = None
    type: Optional[str] = None
    length_ft: Optional[float] = None
    pitches: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class MpRouteResponse(MpRouteBase):
    """Route response schema."""
    mp_route_id: int

    class Config:
        from_attributes = True


class MpRouteListResponse(BaseModel):
    """Response for list of routes."""
    total: int
    data: list[MpRouteResponse]


class MpRouteDetail(MpRouteResponse):
    """Detailed route response with additional data."""
    location_name: Optional[str] = None  # Parent location/area name from MpLocation
    elevation_meters: Optional[float] = None  # Elevation fetched from Open-Elevation API


class MpRouteMapMarker(BaseModel):
    """Minimal route data for map markers."""
    mp_route_id: int
    name: str
    latitude: float
    longitude: float
    grade: Optional[str] = None
    type: Optional[str] = None
    location_id: Optional[int] = None

    class Config:
        from_attributes = True


class MpRouteMapResponse(BaseModel):
    """Response for map markers endpoint."""
    total: int
    routes: list[MpRouteMapMarker]


class Evidence(BaseModel):
    """Effective evidence behind a stored score: incidents here (geo) and on similar routes."""
    geo: float = Field(ge=0.0, allow_inf_nan=False)
    similar: float = Field(ge=0.0, allow_inf_nan=False)


class RateInterval(BaseModel):
    mean: float = Field(ge=0.0, allow_inf_nan=False)
    lo: float = Field(ge=0.0, allow_inf_nan=False)
    hi: float = Field(ge=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.lo <= self.mean <= self.hi:
            raise ValueError("interval must satisfy lo <= mean <= hi")
        return self


class StoredScoreFields(BaseModel):
    """Additive Phase 3 fields (P3 Task 22). Every field is optional so the response stays additive
    for older clients; which ones are set is bound to data_status by check_stored_fields: an ok score
    carries confidence, score_range and evidence, an insufficient one only insufficient_reason."""
    confidence: Optional[Literal["high", "medium", "low"]] = None
    score_range: Optional[tuple[float, float]] = None
    model_version: Optional[str] = None
    evidence: Optional[Evidence] = None
    insufficient_reason: Optional[Literal["no_evidence"]] = None
    # The stored relative rate RR as it is: posterior.summarize already divides the route's rate by
    # r_ref(g, m), the national rate of the typical route of the same type group and month, so it
    # is the Ascents headline's "x the national rate" multiplier (DP13). Never multiplied by r_ref.
    relative_rate: Optional[RateInterval] = None
    per_10k_logged_ascents: Optional[RateInterval] = None
    per_10k_reason: Optional[str] = None


def check_stored_fields(m: StoredScoreFields, risk_score: Optional[float], data_status: str) -> None:
    """confidence, score_range and evidence exactly when ok; the range brackets the shown score;
    an insufficient result says why and carries no ok-only number or per-10k figure."""
    ok_only = (m.confidence, m.score_range, m.evidence)
    if data_status == "ok":
        if any(v is None for v in ok_only):
            raise ValueError("an ok score needs confidence, score_range and evidence")
        assert m.score_range is not None and risk_score is not None
        lo, hi = m.score_range
        if not (math.isfinite(lo) and math.isfinite(hi) and 0.0 <= lo <= risk_score <= hi <= 100.0):
            raise ValueError("score_range must bracket risk_score within 0-100")
        if m.insufficient_reason is not None:
            raise ValueError("an ok score has no insufficient_reason")
    else:
        if any(v is not None for v in ok_only) or m.relative_rate is not None:
            raise ValueError("insufficient_data carries no confidence, range, evidence or rate")
        if m.per_10k_logged_ascents is not None or m.per_10k_reason is not None:
            raise ValueError("insufficient_data carries no per-10k figure or reason")
        if m.insufficient_reason is None:
            raise ValueError("insufficient_data needs insufficient_reason")


class MpRouteSafetyResponse(StoredScoreFields):
    """Safety score response for a specific route and date."""
    route_id: int  # Using mp_route_id but named route_id for frontend compatibility
    route_name: str
    target_date: str
    # None only with data_status="insufficient_data" (then color_code is "gray").
    risk_score: Optional[float] = Field(ge=0.0, le=100.0)
    color_code: SafetyColorCode
    data_status: DataStatus = "ok"

    @model_validator(mode="after")
    def _score_matches_status(self) -> Self:
        check_score_status(self.risk_score, self.color_code, self.data_status)
        check_stored_fields(self, self.risk_score, self.data_status)
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "route_id": 900000001,
                "route_name": "Fixture Route",
                "target_date": "2026-07-04",
                "risk_score": 45.2,
                "color_code": "yellow",
                "data_status": "ok",
                "confidence": "medium",
                "score_range": [30.1, 61.0],
                "model_version": "eb_v1-2026-10-20",
                "evidence": {"geo": 1.4, "similar": 6.2},
                "relative_rate": {"mean": 1.3, "lo": 0.8, "hi": 2.1},
                "per_10k_logged_ascents": None,
                "per_10k_reason": "ticks_incomplete"
            }
        }


# ============================================================================
# BULK SAFETY ENDPOINT SCHEMAS
# ============================================================================

class SafetyScore(StoredScoreFields):
    """Embedded safety score for a route."""
    risk_score: Optional[float] = Field(ge=0.0, le=100.0)
    color_code: SafetyColorCode
    data_status: DataStatus = "ok"
    status: str = "stored"  # 'stored' (model.route_scores); kernel-era values were 'cached'/'computed'

    @model_validator(mode="after")
    def _score_matches_status(self) -> Self:
        check_score_status(self.risk_score, self.color_code, self.data_status)
        check_stored_fields(self, self.risk_score, self.data_status)
        return self


class MpRouteWithSafety(BaseModel):
    """Route data with embedded safety score for bulk endpoint."""
    mp_route_id: int
    name: str
    latitude: float
    longitude: float
    grade: Optional[str] = None
    type: Optional[str] = None
    location_id: Optional[int] = None
    safety: Optional[SafetyScore] = None  # None if no score available


class MpRouteMapWithSafetyMeta(BaseModel):
    """Metadata about the bulk safety response."""
    total_routes: int
    cached_routes: int
    computed_routes: int
    missing_routes: int
    insufficient_routes: int = 0
    target_date: str
    season: str
    model_version: Optional[str] = None


class MpRouteMapWithSafetyResponse(BaseModel):
    """Response for bulk map-with-safety endpoint."""
    routes: list[MpRouteWithSafety]
    meta: MpRouteMapWithSafetyMeta

    class Config:
        json_schema_extra = {
            "example": {
                "routes": [
                    {
                        "mp_route_id": 900000001,
                        "name": "Fixture Route",
                        "latitude": 40.2,
                        "longitude": -105.3,
                        "grade": "5.9",
                        "type": "Trad",
                        "location_id": 900000110,
                        "safety": {
                            "risk_score": 45.2,
                            "color_code": "yellow",
                            "data_status": "ok",
                            "status": "stored",
                            "confidence": "medium",
                            "score_range": [30.1, 61.0],
                            "model_version": "eb_v1-2026-10-20",
                            "evidence": {"geo": 1.4, "similar": 6.2}
                        }
                    }
                ],
                "meta": {
                    "total_routes": 168055,
                    "cached_routes": 167000,
                    "computed_routes": 0,
                    "missing_routes": 1055,
                    "target_date": "2026-02-04",
                    "season": "rock",
                    "model_version": "eb_v1-2026-10-20"
                }
            }
        }
