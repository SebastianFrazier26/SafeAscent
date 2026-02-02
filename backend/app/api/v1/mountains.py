"""
Mountains API endpoints.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.db.session import get_db
from app.models.mountain import Mountain
from app.models.route import Route
from app.schemas.mountain import MountainResponse, MountainListResponse, MountainDetail

router = APIRouter()


@router.get("/mountains", response_model=MountainListResponse)
async def list_mountains(
    search: Optional[str] = Query(None, description="Search mountain name"),
    state: Optional[str] = Query(None, description="Filter by state"),
    min_elevation: Optional[float] = Query(None, description="Minimum elevation in feet"),
    max_elevation: Optional[float] = Query(None, description="Maximum elevation in feet"),
    min_accidents: Optional[int] = Query(None, description="Minimum accident count"),
    limit: int = Query(50, ge=1, le=500, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    db: AsyncSession = Depends(get_db),
):
    """
    List mountains with optional filters.

    - **search**: Search in mountain name (case-insensitive)
    - **state**: Filter by US state (e.g., "Washington", "Colorado")
    - **min_elevation**: Minimum elevation in feet
    - **max_elevation**: Maximum elevation in feet
    - **min_accidents**: Minimum number of historical accidents
    - **limit**: Number of results (max 500)
    - **offset**: Pagination offset
    """
    # Build query with route count
    from sqlalchemy.orm import selectinload

    query = select(
        Mountain,
        func.count(Route.route_id).label('route_count')
    ).outerjoin(
        Route, Mountain.mountain_id == Route.mountain_id
    ).group_by(Mountain.mountain_id)

    # Apply filters
    if search:
        query = query.where(Mountain.name.ilike(f"%{search}%"))
    if state:
        query = query.where(Mountain.state == state)
    if min_elevation is not None:
        query = query.where(Mountain.elevation_ft >= min_elevation)
    if max_elevation is not None:
        query = query.where(Mountain.elevation_ft <= max_elevation)
    if min_accidents is not None:
        query = query.where(Mountain.accident_count >= min_accidents)

    # Order by route count descending (most popular first) or accident count
    if search:
        # When searching, prioritize mountains with more routes
        query = query.order_by(func.count(Route.route_id).desc())
    else:
        # Default: most dangerous first
        query = query.order_by(Mountain.accident_count.desc())

    # Apply pagination
    query = query.limit(limit).offset(offset)

    # Execute query
    result = await db.execute(query)
    rows = result.all()

    # Build response with route counts
    mountains_with_counts = []
    for mountain, route_count in rows:
        mountain_dict = MountainResponse.model_validate(mountain).model_dump()
        mountain_dict['route_count'] = route_count
        mountains_with_counts.append(mountain_dict)

    return MountainListResponse(
        total=len(mountains_with_counts),
        data=mountains_with_counts
    )


@router.get("/mountains/{mountain_id}", response_model=MountainDetail)
async def get_mountain(
    mountain_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed information about a specific mountain.

    - **mountain_id**: Mountain ID
    """
    query = select(Mountain).where(Mountain.mountain_id == mountain_id)
    result = await db.execute(query)
    mountain = result.scalar_one_or_none()

    if not mountain:
        raise HTTPException(status_code=404, detail="Mountain not found")

    return MountainDetail.model_validate(mountain)
