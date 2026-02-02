"""
Pydantic schemas for Mountain API requests/responses.
"""
from typing import Optional
from pydantic import BaseModel, Field


class MountainBase(BaseModel):
    """Base mountain schema with common fields."""
    name: str
    alt_names: Optional[str] = None
    elevation_ft: Optional[float] = None
    prominence_ft: Optional[float] = None
    type: Optional[str] = None
    range: Optional[str] = None
    state: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location: Optional[str] = None
    accident_count: int = 0


class MountainResponse(MountainBase):
    """Mountain response schema."""
    mountain_id: int
    route_count: Optional[int] = 0  # Number of routes on this mountain

    class Config:
        from_attributes = True  # Allows creating from ORM models


class MountainListResponse(BaseModel):
    """Response for list of mountains."""
    total: int
    data: list[MountainResponse]


class MountainDetail(MountainResponse):
    """Detailed mountain response with additional data."""
    # Will add route count, recent accidents, etc. later
    pass
