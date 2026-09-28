"""
Pydantic schemas for MpRoute API requests/responses.
"""
from typing import Optional, Self
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


class MpRouteSafetyResponse(BaseModel):
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
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "route_id": 105748391,
                "route_name": "The Nose",
                "target_date": "2026-02-01",
                "risk_score": 45.2,
                "color_code": "yellow",
                "data_status": "ok"
            }
        }


# ============================================================================
# BULK SAFETY ENDPOINT SCHEMAS
# ============================================================================

class SafetyScore(BaseModel):
    """Embedded safety score for a route."""
    risk_score: Optional[float] = Field(ge=0.0, le=100.0)
    color_code: SafetyColorCode
    data_status: DataStatus = "ok"
    status: str = "cached"  # 'cached' or 'computed'

    @model_validator(mode="after")
    def _score_matches_status(self) -> Self:
        check_score_status(self.risk_score, self.color_code, self.data_status)
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


class MpRouteMapWithSafetyResponse(BaseModel):
    """Response for bulk map-with-safety endpoint."""
    routes: list[MpRouteWithSafety]
    meta: MpRouteMapWithSafetyMeta

    class Config:
        json_schema_extra = {
            "example": {
                "routes": [
                    {
                        "mp_route_id": 105748391,
                        "name": "The Nose",
                        "latitude": 37.73,
                        "longitude": -119.64,
                        "grade": "5.9",
                        "type": "Trad",
                        "location_id": 12345,
                        "safety": {
                            "risk_score": 45.2,
                            "color_code": "yellow",
                            "data_status": "ok",
                            "status": "cached"
                        }
                    }
                ],
                "meta": {
                    "total_routes": 168055,
                    "cached_routes": 167000,
                    "computed_routes": 1000,
                    "missing_routes": 55,
                    "target_date": "2026-02-04",
                    "season": "rock"
                }
            }
        }
