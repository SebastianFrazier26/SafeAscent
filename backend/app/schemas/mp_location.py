"""
Pydantic schemas for MpLocation API requests/responses.
"""
from typing import Optional
from pydantic import BaseModel


class MpLocationBase(BaseModel):
    """Base location schema with common fields."""
    name: str
    parent_id: Optional[int] = None
    url: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class MpLocationResponse(MpLocationBase):
    """Location response schema."""
    mp_id: int
    route_count: Optional[int] = 0  # Number of routes at this location

    class Config:
        from_attributes = True


class MpLocationListResponse(BaseModel):
    """Response for list of locations."""
    total: int
    data: list[MpLocationResponse]


class MpLocationDetail(MpLocationResponse):
    """Detailed location response with additional data."""
    pass
