"""
Pydantic schemas for MpRoute API requests/responses.
"""
from typing import Optional
from pydantic import BaseModel


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
    pass


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
    risk_score: float
    color_code: str  # For marker coloring: 'green', 'yellow', 'orange', 'red'

    class Config:
        json_schema_extra = {
            "example": {
                "route_id": 105748391,
                "route_name": "The Nose",
                "target_date": "2026-02-01",
                "risk_score": 45.2,
                "color_code": "yellow"
            }
        }
