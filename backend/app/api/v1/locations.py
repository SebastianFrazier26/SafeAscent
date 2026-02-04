"""
Locations API endpoints.
Uses mp_locations table for Mountain Project climbing areas.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.db.session import get_db
from app.models.mp_location import MpLocation
from app.models.mp_route import MpRoute
from app.schemas.mp_location import MpLocationResponse, MpLocationListResponse, MpLocationDetail

router = APIRouter()


@router.get("/locations", response_model=MpLocationListResponse)
async def list_locations(
    search: Optional[str] = Query(None, description="Search location name"),
    limit: int = Query(50, ge=1, le=500, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    db: AsyncSession = Depends(get_db),
):
    """
    List and search climbing locations/areas.

    - **search**: Search in location name (case-insensitive)
    - **limit**: Number of results (max 500)
    - **offset**: Pagination offset
    """
    # Build query with route count
    query = select(
        MpLocation,
        func.count(MpRoute.mp_route_id).label('route_count')
    ).outerjoin(
        MpRoute, MpLocation.mp_id == MpRoute.location_id
    ).group_by(MpLocation.mp_id)

    # Apply search filter
    if search:
        query = query.where(MpLocation.name.ilike(f"%{search}%"))

    # Order by route count descending (most popular areas first)
    query = query.order_by(func.count(MpRoute.mp_route_id).desc())

    # Apply pagination
    query = query.limit(limit).offset(offset)

    # Execute query
    result = await db.execute(query)
    rows = result.all()

    # Build response with route counts
    locations_with_counts = []
    for location, route_count in rows:
        location_dict = MpLocationResponse.model_validate(location).model_dump()
        location_dict['route_count'] = route_count
        locations_with_counts.append(location_dict)

    return MpLocationListResponse(
        total=len(locations_with_counts),
        data=locations_with_counts
    )


@router.get("/locations/{mp_id}", response_model=MpLocationDetail)
async def get_location(
    mp_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed information about a specific location.

    - **mp_id**: Mountain Project location ID
    """
    query = select(MpLocation).where(MpLocation.mp_id == mp_id)
    result = await db.execute(query)
    location = result.scalar_one_or_none()

    if not location:
        raise HTTPException(status_code=404, detail="Location not found")

    return MpLocationDetail.model_validate(location)
