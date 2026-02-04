"""
Accidents API endpoints with PostGIS spatial query support.
"""
from typing import Optional
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, type_coerce
from geoalchemy2 import Geography
from geoalchemy2.functions import ST_MakePoint

from app.db.session import get_db
from app.models.accident import Accident
from app.schemas.accident import AccidentResponse, AccidentListResponse, AccidentDetail

router = APIRouter()


@router.get("/accidents", response_model=AccidentListResponse)
async def list_accidents(
    # Location filters (spatial)
    lat: Optional[float] = Query(None, description="Latitude for spatial search"),
    lon: Optional[float] = Query(None, description="Longitude for spatial search"),
    radius_km: Optional[float] = Query(None, ge=0.1, le=500, description="Search radius in kilometers"),
    # Text filters
    state: Optional[str] = Query(None, description="Filter by state"),
    mountain: Optional[str] = Query(None, description="Search mountain name"),
    route: Optional[str] = Query(None, description="Search route name"),
    # Categorical filters
    accident_type: Optional[str] = Query(None, description="Filter by accident type (fall, avalanche, etc.)"),
    injury_severity: Optional[str] = Query(None, description="Filter by severity (fatal, serious, minor)"),
    activity: Optional[str] = Query(None, description="Filter by activity (climbing, skiing, etc.)"),
    # Date filters
    start_date: Optional[date] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End date (YYYY-MM-DD)"),
    # Foreign key filters
    mountain_id: Optional[int] = Query(None, description="Filter by mountain ID"),
    route_id: Optional[int] = Query(None, description="Filter by route ID"),
    # Pagination
    limit: int = Query(100, ge=1, le=1000, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    db: AsyncSession = Depends(get_db),
):
    """
    List and search accidents with spatial and temporal filters.

    **Spatial Search:**
    - **lat**, **lon**, **radius_km**: Find accidents within X km of coordinates
      Example: `?lat=46.85&lon=-121.76&radius_km=10` (10km around Mt. Rainier)

    **Text Filters:**
    - **state**: US state
    - **mountain**: Mountain name (partial match)
    - **route**: Route name (partial match)

    **Categorical Filters:**
    - **accident_type**: fall, avalanche, rockfall, etc.
    - **injury_severity**: fatal, serious, minor
    - **activity**: climbing, skiing, mountaineering, etc.

    **Date Filters:**
    - **start_date**, **end_date**: Date range

    **Pagination:**
    - **limit**: Max 1000 results
    - **offset**: Skip N results
    """
    # Build query
    query = select(Accident)

    # Spatial filter (PostGIS ST_DWithin)
    if lat is not None and lon is not None and radius_km is not None:
        # Convert km to meters
        radius_m = radius_km * 1000
        # Create point from lat/lon and cast to Geography for Earth-surface distance
        point = ST_MakePoint(lon, lat, srid=4326)
        point_geography = type_coerce(point, Geography)
        # Use ST_DWithin for geography type (Earth-surface distance)
        query = query.where(
            func.ST_DWithin(
                Accident.coordinates,
                point_geography,
                radius_m
            )
        )

    # Text filters
    if state:
        query = query.where(Accident.state == state)
    if mountain:
        query = query.where(Accident.mountain.ilike(f"%{mountain}%"))
    if route:
        query = query.where(Accident.route.ilike(f"%{route}%"))

    # Categorical filters
    if accident_type:
        query = query.where(Accident.accident_type == accident_type)
    if injury_severity:
        query = query.where(Accident.injury_severity == injury_severity)
    if activity:
        query = query.where(Accident.activity == activity)

    # Date filters
    if start_date:
        query = query.where(Accident.date >= start_date)
    if end_date:
        query = query.where(Accident.date <= end_date)

    # Foreign key filters
    if mountain_id is not None:
        query = query.where(Accident.mountain_id == mountain_id)
    if route_id is not None:
        query = query.where(Accident.route_id == route_id)

    # Order by date descending (most recent first)
    query = query.order_by(Accident.date.desc().nullslast())

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.limit(limit).offset(offset)

    # Execute query
    result = await db.execute(query)
    accidents = result.scalars().all()

    return AccidentListResponse(
        total=total,
        data=[AccidentResponse.model_validate(a) for a in accidents]
    )


@router.get("/accidents/{accident_id}", response_model=AccidentDetail)
async def get_accident(
    accident_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed information about a specific accident.

    - **accident_id**: Accident ID
    """
    query = select(Accident).where(Accident.accident_id == accident_id)
    result = await db.execute(query)
    accident = result.scalar_one_or_none()

    if not accident:
        raise HTTPException(status_code=404, detail="Accident not found")

    return AccidentDetail.model_validate(accident)
