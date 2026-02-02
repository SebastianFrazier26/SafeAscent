"""
Pydantic schemas for Accident API requests/responses.
"""
from typing import Optional
from datetime import date as date_type
from pydantic import BaseModel, ConfigDict


class AccidentBase(BaseModel):
    """Base accident schema with common fields."""
    source: Optional[str] = None
    source_id: Optional[str] = None
    date: Optional[date_type] = None
    year: Optional[float] = None
    state: Optional[str] = None
    location: Optional[str] = None
    mountain: Optional[str] = None
    route: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accident_type: Optional[str] = None
    activity: Optional[str] = None
    injury_severity: Optional[str] = None
    age_range: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    mountain_id: Optional[int] = None
    route_id: Optional[int] = None


class AccidentResponse(AccidentBase):
    """Accident response schema."""
    accident_id: int

    model_config = ConfigDict(
        from_attributes=True,
        # Exclude the PostGIS coordinates field (binary data)
        # We already provide latitude/longitude for display
        protected_namespaces=(),
    )


class AccidentListResponse(BaseModel):
    """Response for list of accidents."""
    total: int
    data: list[AccidentResponse]


class AccidentDetail(AccidentResponse):
    """Detailed accident response with additional data."""
    # Will add weather data later
    pass
