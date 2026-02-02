"""
Pydantic schemas for Route API requests/responses.
"""
from typing import Optional
from pydantic import BaseModel


class RouteBase(BaseModel):
    """Base route schema with common fields."""
    name: str
    mountain_id: Optional[int] = None
    mountain_name: Optional[str] = None
    grade: Optional[str] = None
    grade_yds: Optional[str] = None
    length_ft: Optional[float] = None
    pitches: Optional[int] = None
    type: Optional[str] = None
    first_ascent_year: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accident_count: int = 0
    mp_route_id: Optional[str] = None


class RouteResponse(RouteBase):
    """Route response schema."""
    route_id: int

    class Config:
        from_attributes = True


class RouteListResponse(BaseModel):
    """Response for list of routes."""
    total: int
    data: list[RouteResponse]


class RouteDetail(RouteResponse):
    """Detailed route response with additional data."""
    # Will add recent accidents, weather, safety rating later
    pass


class RouteMapMarker(BaseModel):
    """Minimal route data for map markers."""
    route_id: int
    name: str
    latitude: float
    longitude: float
    grade_yds: Optional[str] = None
    type: Optional[str] = None
    mp_route_id: Optional[str] = None

    class Config:
        from_attributes = True


class RouteMapResponse(BaseModel):
    """Response for map markers endpoint."""
    total: int
    routes: list[RouteMapMarker]


class RouteSafetyResponse(BaseModel):
    """Safety score response for a specific route and date."""
    route_id: int
    route_name: str
    target_date: str
    risk_score: float
    color_code: str  # For marker coloring: 'green', 'yellow', 'orange', 'red'

    class Config:
        json_schema_extra = {
            "example": {
                "route_id": 1234,
                "route_name": "The Nose",
                "target_date": "2026-02-01",
                "risk_score": 45.2,
                "color_code": "yellow"
            }
        }
