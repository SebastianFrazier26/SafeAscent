"""
MP Routes API endpoints.
Uses mp_routes table for Mountain Project climbing routes.
Includes all analytics and safety endpoints.
"""
from typing import Optional
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, or_, and_, not_
import logging
import calendar

from app.db.session import get_db
from app.models.mp_route import MpRoute
from app.models.mp_location import MpLocation
from app.schemas.mp_route import (
    MpRouteResponse,
    MpRouteListResponse,
    MpRouteDetail,
    MpRouteMapMarker,
    MpRouteMapResponse,
    MpRouteSafetyResponse,
    MpRouteWithSafety,
    MpRouteMapWithSafetyResponse,
    MpRouteMapWithSafetyMeta,
    SafetyScore,
)
from app.api.v1.predict import predict_route_safety
from app.schemas.prediction import PredictionRequest
from app.utils.cache import (
    cache_get,
    cache_set,
    build_safety_score_key,
    get_bulk_cached_safety_scores,
)
from app.services.weather_service import WEATHER_API_URL, OPEN_METEO_API_KEY

router = APIRouter()
logger = logging.getLogger(__name__)


async def get_location_breadcrumb(db: AsyncSession, location_id: int, exclude_states: bool = True) -> str:
    """
    Build a breadcrumb path for a location by traversing the parent_id chain.

    Example output: "Yosemite National Park → Half Dome → Northwest Face"

    Args:
        db: Database session
        location_id: Starting location ID (most specific)
        exclude_states: If True, stops traversal at state level (excludes "California", "USA" etc.)

    Returns:
        Breadcrumb string with " → " separators, or empty string if location not found
    """
    # Known state/country names to exclude from breadcrumb
    STATE_NAMES = {
        'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
        'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
        'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana',
        'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota',
        'mississippi', 'missouri', 'montana', 'nebraska', 'nevada',
        'new hampshire', 'new jersey', 'new mexico', 'new york',
        'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon',
        'pennsylvania', 'rhode island', 'south carolina', 'south dakota',
        'tennessee', 'texas', 'utah', 'vermont', 'virginia', 'washington',
        'west virginia', 'wisconsin', 'wyoming', 'district of columbia',
        'united states', 'usa', 'canada', 'mexico', 'international'
    }

    path_parts = []
    current_id = location_id
    max_depth = 15  # Safety limit to prevent infinite loops

    for _ in range(max_depth):
        if current_id is None:
            break

        # Fetch location name and parent
        query = select(MpLocation.name, MpLocation.parent_id).where(MpLocation.mp_id == current_id)
        result = await db.execute(query)
        row = result.one_or_none()

        if not row:
            break

        name, parent_id = row

        # Check if this is a state/country to exclude
        if exclude_states and name.lower().strip() in STATE_NAMES:
            break

        path_parts.append(name)
        current_id = parent_id

    # Reverse to get top-down order (most general → most specific)
    path_parts.reverse()

    return " → ".join(path_parts) if path_parts else ""


def normalize_route_type(route_type: Optional[str]) -> str:
    """
    Normalize route type to one accepted by the prediction algorithm.

    Maps various route type strings to canonical types:
    alpine, ice, mixed, trad, sport, aid, boulder

    Args:
        route_type: Raw route type string from database

    Returns:
        Normalized route type (defaults to 'trad' if unknown)
    """
    if not route_type:
        return 'trad'  # Default fallback

    route_type = route_type.lower().strip()

    # Direct matches
    valid_types = ['alpine', 'ice', 'mixed', 'trad', 'sport', 'aid', 'boulder']
    if route_type in valid_types:
        return route_type

    # Mappings for common variations
    type_mapping = {
        'yds': 'trad',
        'traditional': 'trad',
        'trad climb': 'trad',
        'sport climb': 'sport',
        'bouldering': 'boulder',
        'ice climb': 'ice',
        'ice climbing': 'ice',
        'alpine climb': 'alpine',
        'mountaineering': 'alpine',
        'aid climb': 'aid',
        'big wall': 'aid',
        'snow': 'alpine',
        'rock': 'trad',
        'toprope': 'sport',  # Top rope is similar to sport
    }

    return type_mapping.get(route_type, 'trad')


def get_safety_color_code(risk_score: float) -> str:
    """
    Convert risk score to color code for map markers.

    Args:
        risk_score: Risk score from 0-100

    Returns:
        Color code string: 'green', 'yellow', 'orange', or 'red'
    """
    if risk_score < 30:
        return 'green'
    elif risk_score < 50:
        return 'yellow'
    elif risk_score < 70:
        return 'orange'
    else:
        return 'red'


async def get_route_with_location_coords(db: AsyncSession, mp_route_id: int):
    """
    Fetch a route with coordinates inherited from its parent location.

    Routes don't store coordinates directly - they inherit them from their
    parent location in the MP hierarchy.

    Args:
        db: Database session
        mp_route_id: Mountain Project route ID

    Returns:
        Row with route fields + location latitude/longitude, or None

    Raises:
        HTTPException: If route not found or has no coordinates
    """
    query = (
        select(
            MpRoute.mp_route_id,
            MpRoute.name,
            MpRoute.type,
            MpRoute.grade,
            MpRoute.url,
            MpRoute.location_id,
            MpRoute.length_ft,
            MpRoute.pitches,
            MpLocation.latitude,
            MpLocation.longitude,
            MpLocation.name.label('location_name'),
        )
        .join(MpLocation, MpRoute.location_id == MpLocation.mp_id)
        .where(MpRoute.mp_route_id == mp_route_id)
    )
    result = await db.execute(query)
    return result.one_or_none()


# ============================================================================
# BASIC CRUD ENDPOINTS
# ============================================================================

@router.get("/mp-routes", response_model=MpRouteListResponse)
async def list_mp_routes(
    search: Optional[str] = Query(None, description="Search route name"),
    location_id: Optional[int] = Query(None, description="Filter by location ID"),
    route_type: Optional[str] = Query(None, description="Filter by route type"),
    grade: Optional[str] = Query(None, description="Filter by grade (e.g., '5.10')"),
    limit: int = Query(50, ge=1, le=500, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    db: AsyncSession = Depends(get_db),
):
    """
    List and search MP routes with optional filters.

    - **search**: Search in route name
    - **location_id**: Filter by specific location
    - **route_type**: Filter by type (rock, ice, mixed, etc.)
    - **grade**: Filter by grade
    - **limit**: Number of results (max 500)
    - **offset**: Pagination offset
    """
    query = select(MpRoute)

    # Apply filters
    if search:
        query = query.where(MpRoute.name.ilike(f"%{search}%"))
    if location_id is not None:
        query = query.where(MpRoute.location_id == location_id)
    if route_type:
        query = query.where(MpRoute.type.ilike(f"%{route_type}%"))
    if grade:
        query = query.where(MpRoute.grade.ilike(f"%{grade}%"))

    # Order by name
    query = query.order_by(MpRoute.name)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.limit(limit).offset(offset)

    # Execute query
    result = await db.execute(query)
    routes = result.scalars().all()

    return MpRouteListResponse(
        total=total,
        data=[MpRouteResponse.model_validate(r) for r in routes]
    )


@router.get("/mp-routes/map", response_model=MpRouteMapResponse)
async def get_mp_routes_for_map(
    min_lat: Optional[float] = Query(None, description="Minimum latitude (bounding box)"),
    max_lat: Optional[float] = Query(None, description="Maximum latitude (bounding box)"),
    min_lon: Optional[float] = Query(None, description="Minimum longitude (bounding box)"),
    max_lon: Optional[float] = Query(None, description="Maximum longitude (bounding box)"),
    season: Optional[str] = Query(
        "rock",
        description="Filter by season: 'rock' (default) or 'winter' (ice/mixed routes)"
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all MP routes with coordinates for map display.
    Returns minimal data optimized for map markers.

    Coordinates are inherited from the route's parent location.

    - **min_lat, max_lat, min_lon, max_lon**: Optional bounding box to filter routes
    - **season**: 'rock' (default) returns all non-ice routes, 'winter' returns ice/mixed routes
    """
    # Join routes with locations to get coordinates from parent location
    # Routes inherit coordinates from their location_id
    query = (
        select(
            MpRoute.mp_route_id,
            MpRoute.name,
            MpRoute.grade,
            MpRoute.type,
            MpRoute.location_id,
            MpLocation.latitude,
            MpLocation.longitude,
        )
        .join(MpLocation, MpRoute.location_id == MpLocation.mp_id)
        .where(
            MpLocation.latitude.isnot(None),
            MpLocation.longitude.isnot(None)
        )
    )

    # Apply season filter (server-side route type filtering)
    # Winter = ice or mixed routes, Rock = everything else
    # ALWAYS exclude 'unknown' type routes from display and calculations
    if season == "winter":
        query = query.where(
            and_(
                or_(
                    func.lower(MpRoute.type).contains('ice'),
                    func.lower(MpRoute.type).contains('mixed')
                ),
                func.lower(MpRoute.type) != 'unknown'
            )
        )
    else:
        # Default: rock routes (everything except ice/mixed/unknown)
        query = query.where(
            and_(
                not_(func.lower(MpRoute.type).contains('ice')),
                not_(func.lower(MpRoute.type).contains('mixed')),
                func.lower(MpRoute.type) != 'unknown'
            )
        )

    # Apply bounding box filter if provided
    if all([min_lat, max_lat, min_lon, max_lon]):
        query = query.where(
            MpLocation.latitude >= min_lat,
            MpLocation.latitude <= max_lat,
            MpLocation.longitude >= min_lon,
            MpLocation.longitude <= max_lon
        )

    # Execute query
    result = await db.execute(query)
    rows = result.fetchall()

    # Convert to response format
    routes = [
        MpRouteMapMarker(
            mp_route_id=row.mp_route_id,
            name=row.name,
            latitude=row.latitude,
            longitude=row.longitude,
            grade=row.grade,
            type=row.type,
            location_id=row.location_id,
        )
        for row in rows
    ]

    return MpRouteMapResponse(
        total=len(routes),
        routes=routes
    )


# ============================================================================
# BULK SAFETY ENDPOINT - Returns routes with pre-computed safety scores
# ============================================================================

@router.get("/mp-routes/map-with-safety", response_model=MpRouteMapWithSafetyResponse)
async def get_mp_routes_with_safety(
    target_date: date = Query(
        default=None,
        description="Target date for safety calculation (YYYY-MM-DD). Defaults to today."
    ),
    season: Optional[str] = Query(
        "rock",
        description="Filter by season: 'rock' (default) or 'winter' (ice/mixed routes)"
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all MP routes with PRE-COMPUTED safety scores in a single request.

    This is the primary endpoint for the map view - it returns route data
    with safety scores already embedded, eliminating the need for individual
    safety API calls per route.

    Safety scores are pre-computed nightly at 2am UTC and cached in Redis.
    If a score is not cached (cache miss), it returns safety=None for that route.

    **Performance**: This endpoint returns ~168K routes with safety scores in
    a single response (~2-3 seconds) instead of requiring 168K individual
    safety API calls (~76 hours).

    - **target_date**: Date for safety calculation (default: today)
    - **season**: 'rock' (default) returns all non-ice routes, 'winter' returns ice/mixed routes
    """
    from datetime import date as date_type

    # Default to today if no date provided
    if target_date is None:
        target_date = date_type.today()

    date_str = target_date.isoformat()

    # Query routes with coordinates (same as /mp-routes/map)
    query = (
        select(
            MpRoute.mp_route_id,
            MpRoute.name,
            MpRoute.grade,
            MpRoute.type,
            MpRoute.location_id,
            MpLocation.latitude,
            MpLocation.longitude,
        )
        .join(MpLocation, MpRoute.location_id == MpLocation.mp_id)
        .where(
            MpLocation.latitude.isnot(None),
            MpLocation.longitude.isnot(None)
        )
    )

    # Apply season filter
    # ALWAYS exclude 'unknown' type routes from display and calculations
    if season == "winter":
        query = query.where(
            and_(
                or_(
                    func.lower(MpRoute.type).contains('ice'),
                    func.lower(MpRoute.type).contains('mixed')
                ),
                func.lower(MpRoute.type) != 'unknown'
            )
        )
    else:
        # Default: rock routes (everything except ice/mixed/unknown)
        query = query.where(
            and_(
                not_(func.lower(MpRoute.type).contains('ice')),
                not_(func.lower(MpRoute.type).contains('mixed')),
                func.lower(MpRoute.type) != 'unknown'
            )
        )

    result = await db.execute(query)
    rows = result.fetchall()

    # Extract route IDs for bulk cache lookup
    route_ids = [row.mp_route_id for row in rows]

    # Bulk fetch safety scores from cache (single Redis MGET call)
    cached_scores = get_bulk_cached_safety_scores(route_ids, date_str)

    # Build response with embedded safety scores
    routes_with_safety = []
    cached_count = 0
    missing_count = 0

    for row in rows:
        # Get cached safety score if available
        cached = cached_scores.get(row.mp_route_id)

        if cached:
            safety = SafetyScore(
                risk_score=cached.get("risk_score", 0),
                color_code=cached.get("color_code", "gray"),
                status="cached"
            )
            cached_count += 1
        else:
            safety = None
            missing_count += 1

        routes_with_safety.append(
            MpRouteWithSafety(
                mp_route_id=row.mp_route_id,
                name=row.name,
                latitude=row.latitude,
                longitude=row.longitude,
                grade=row.grade,
                type=row.type,
                location_id=row.location_id,
                safety=safety,
            )
        )

    # Build metadata
    meta = MpRouteMapWithSafetyMeta(
        total_routes=len(routes_with_safety),
        cached_routes=cached_count,
        computed_routes=0,  # We don't compute on-demand in bulk endpoint
        missing_routes=missing_count,
        target_date=date_str,
        season=season or "rock",
    )

    logger.info(
        f"Bulk safety endpoint: {len(routes_with_safety)} routes, "
        f"{cached_count} cached, {missing_count} missing for {date_str}"
    )

    return MpRouteMapWithSafetyResponse(
        routes=routes_with_safety,
        meta=meta,
    )


@router.get("/mp-routes/{mp_route_id}", response_model=MpRouteDetail)
async def get_mp_route(
    mp_route_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed information about a specific MP route.

    - **mp_route_id**: Mountain Project route ID

    Returns route data including full location breadcrumb path for display.
    Example: "Yosemite National Park → Half Dome → Northwest Face"
    """
    # Join with MpLocation to get location name and coordinates
    query = (
        select(
            MpRoute,
            MpLocation.name.label("location_name"),
            MpLocation.latitude.label("loc_lat"),
            MpLocation.longitude.label("loc_lon"),
            MpLocation.mp_id.label("location_mp_id"),
        )
        .outerjoin(MpLocation, MpRoute.location_id == MpLocation.mp_id)
        .where(MpRoute.mp_route_id == mp_route_id)
    )
    result = await db.execute(query)
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="Route not found")

    route, location_name, loc_lat, loc_lon, location_mp_id = row

    # Build response with location name
    route_data = MpRouteDetail.model_validate(route)

    # Get full location breadcrumb path (e.g., "Yosemite → Half Dome → Northwest Face")
    if location_mp_id:
        breadcrumb = await get_location_breadcrumb(db, location_mp_id, exclude_states=True)
        route_data.location_name = breadcrumb if breadcrumb else location_name
    else:
        route_data.location_name = location_name

    # Fetch elevation from coordinates (use location coords if route coords missing)
    lat = route.latitude or loc_lat
    lon = route.longitude or loc_lon
    if lat and lon:
        from app.services.elevation_service import fetch_elevation
        elevation = fetch_elevation(lat, lon)
        route_data.elevation_meters = elevation
        # Also ensure lat/lon are set in response
        route_data.latitude = lat
        route_data.longitude = lon

    return route_data


@router.post("/mp-routes/{mp_route_id}/safety", response_model=MpRouteSafetyResponse)
async def calculate_mp_route_safety(
    mp_route_id: int,
    target_date: date = Query(..., description="Target date for safety calculation (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Calculate safety score for an MP route on a specific date.

    Uses the SafeAscent algorithm to compute risk based on:
    - Historical accident data nearby
    - Weather conditions
    - Route characteristics
    - Seasonal patterns

    Coordinates are inherited from the route's parent location.
    Returns a risk score from 0-100 and a color code for visualization.
    """
    # Look up route with its location to get coordinates
    query = (
        select(
            MpRoute.mp_route_id,
            MpRoute.name,
            MpRoute.type,
            MpLocation.latitude,
            MpLocation.longitude,
        )
        .join(MpLocation, MpRoute.location_id == MpLocation.mp_id)
        .where(MpRoute.mp_route_id == mp_route_id)
    )
    result = await db.execute(query)
    route = result.one_or_none()

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    if not route.latitude or not route.longitude:
        raise HTTPException(
            status_code=400,
            detail="Route's location has no coordinates - cannot calculate safety score"
        )

    # Check cache first (cache_get is sync, not async)
    date_str = target_date.isoformat()
    cache_key = build_safety_score_key(mp_route_id, date_str)
    cached_result = cache_get(cache_key)
    if cached_result:
        # Merge cached safety data with route info from database
        return MpRouteSafetyResponse(
            route_id=mp_route_id,
            route_name=route.name,
            target_date=date_str,
            risk_score=cached_result.get("risk_score", 0),
            color_code=cached_result.get("color_code", "gray"),
        )

    # Build prediction request
    normalized_type = normalize_route_type(route.type)
    prediction_request = PredictionRequest(
        latitude=route.latitude,
        longitude=route.longitude,
        planned_date=target_date,
        route_type=normalized_type,
    )

    # Get prediction
    prediction_response = await predict_route_safety(prediction_request, db)

    # Build response
    response = MpRouteSafetyResponse(
        route_id=mp_route_id,
        route_name=route.name,
        target_date=date_str,
        risk_score=prediction_response.risk_score,
        color_code=get_safety_color_code(prediction_response.risk_score),
    )

    # Cache the result (1 hour TTL) - cache_set is sync, not async
    cache_set(cache_key, response.model_dump(), ttl_seconds=3600)

    return response


# ============================================================================
# ANALYTICS ENDPOINTS
# ============================================================================

@router.get("/mp-routes/{mp_route_id}/forecast")
async def get_route_forecast(
    mp_route_id: int,
    start_date: date = Query(..., description="Start date for forecast (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get 7-day safety forecast for a route starting from the specified date.

    Returns detailed weather and risk scores for each day.
    Coordinates are inherited from the route's parent location.
    """
    from app.services.weather_service import fetch_current_weather_pattern
    from app.services.elevation_service import fetch_elevation

    # Fetch route with location coordinates
    route = await get_route_with_location_coords(db, mp_route_id)

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    if route.latitude is None or route.longitude is None:
        raise HTTPException(status_code=400, detail="Route's location missing GPS coordinates")

    # Fetch elevation for the route location
    elevation_meters = None
    if route.latitude and route.longitude:
        elevation_meters = fetch_elevation(route.latitude, route.longitude)

    # Calculate forecast for 7 days
    forecast_days = []
    normalized_type = normalize_route_type(route.type)

    for day_offset in range(7):
        target_date = start_date + timedelta(days=day_offset)

        # Create prediction request (use fetched elevation)
        prediction_request = PredictionRequest(
            latitude=route.latitude,
            longitude=route.longitude,
            route_type=normalized_type,
            planned_date=target_date,
            elevation_meters=elevation_meters,
        )

        try:
            # Calculate safety
            prediction = await predict_route_safety(prediction_request, db)

            # Fetch weather for this date
            weather = fetch_current_weather_pattern(
                latitude=route.latitude,
                longitude=route.longitude,
                target_date=target_date
            )

            # Extract weather details (last day of pattern is target date)
            if weather and len(weather.temperature) > 0:
                idx = -1  # Last day (target date)
                temp_avg = weather.temperature[idx]
                temp_min, temp_max = weather.daily_temps[idx][0], weather.daily_temps[idx][2]
                precip = weather.precipitation[idx]
                wind = weather.wind_speed[idx]
                cloud = weather.cloud_cover[idx]

                weather_desc = []
                if temp_avg < -10:
                    weather_desc.append("Very Cold")
                elif temp_avg < 0:
                    weather_desc.append("Freezing")
                elif temp_avg < 10:
                    weather_desc.append("Cold")
                elif temp_avg < 20:
                    weather_desc.append("Mild")
                else:
                    weather_desc.append("Warm")

                if precip > 10:
                    weather_desc.append("Heavy Precipitation")
                elif precip > 2:
                    weather_desc.append("Precipitation")
                elif cloud > 70:
                    weather_desc.append("Cloudy")
                elif cloud < 30:
                    weather_desc.append("Clear")

                if wind > 15:
                    weather_desc.append("Very Windy")
                elif wind > 10:
                    weather_desc.append("Windy")

                weather_summary = ", ".join(weather_desc) if weather_desc else "Moderate Conditions"
            else:
                temp_avg = temp_min = temp_max = precip = wind = cloud = None
                weather_summary = "Weather data unavailable"

            forecast_days.append({
                "date": target_date.isoformat(),
                "risk_score": round(prediction.risk_score, 1),
                "weather_summary": weather_summary,
                "temp_high": round(temp_max, 1) if temp_max is not None else None,
                "temp_low": round(temp_min, 1) if temp_min is not None else None,
                "temp_avg": round(temp_avg, 1) if temp_avg is not None else None,
                "precip_mm": round(precip, 1) if precip is not None else None,
                "precip_chance": int(min(precip * 10, 100)) if precip is not None else None,
                "wind_speed": round(wind, 1) if wind is not None else None,
                "cloud_cover": round(cloud, 0) if cloud is not None else None,
            })
        except Exception as e:
            logger.error(f"Error calculating forecast for {target_date}: {e}")
            forecast_days.append({
                "date": target_date.isoformat(),
                "risk_score": None,
                "error": str(e)
            })

    # Today's detailed conditions (first day)
    today_data = forecast_days[0] if forecast_days else {}

    return {
        "route_id": mp_route_id,
        "route_name": route.name,
        "start_date": start_date.isoformat(),
        "forecast_days": forecast_days,
        "today": today_data,
        "elevation_meters": elevation_meters,
    }


@router.get("/mp-routes/{mp_route_id}/accidents")
async def get_route_accidents(
    mp_route_id: int,
    limit: int = Query(50, ge=1, le=100, description="Maximum accidents to return"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get accident reports near this route's location.

    Uses geographic proximity to find nearby accidents within ~50km radius.
    Includes weather data for accident dates when available.
    Coordinates are inherited from the route's parent location.

    Returns accidents with:
    - impact_score: Relevance based on proximity (closer = higher score)
    - same_route: True if accident occurred on the exact same route
    - weather: Historical weather conditions on accident date
    """
    import requests
    import math

    # Fetch route with location coordinates
    route = await get_route_with_location_coords(db, mp_route_id)

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    if not route.latitude or not route.longitude:
        raise HTTPException(status_code=400, detail="Route's location missing GPS coordinates")

    # Fetch nearby accidents using geographic proximity with distance calculation
    # Using Haversine formula - also returns calculated distance for relevance scoring
    # Include ALL available fields for comprehensive accident reports
    accidents_query = text("""
        SELECT
            accident_id, date, latitude, longitude, description,
            accident_type, injury_severity, location, route as route_name,
            source, state, mountain, activity, age_range, tags, elevation_meters,
            (
                6371 * acos(
                    cos(radians(:lat)) * cos(radians(latitude)) *
                    cos(radians(longitude) - radians(:lon)) +
                    sin(radians(:lat)) * sin(radians(latitude))
                )
            ) as distance_km
        FROM accidents
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          AND (
              6371 * acos(
                  cos(radians(:lat)) * cos(radians(latitude)) *
                  cos(radians(longitude) - radians(:lon)) +
                  sin(radians(:lat)) * sin(radians(latitude))
              )
          ) < 50
        ORDER BY distance_km ASC, date DESC NULLS LAST
        LIMIT :limit
    """)

    accidents_result = await db.execute(
        accidents_query,
        {"lat": route.latitude, "lon": route.longitude, "limit": limit}
    )
    accidents = accidents_result.fetchall()

    # Format accident data with weather and relevance scoring
    accidents_data = []
    for accident in accidents:
        (accident_id, acc_date, acc_lat, acc_lon, description, acc_type, severity,
         location, route_name, source, state, mountain, activity, age_range,
         tags, elevation_m, distance_km) = accident

        # Calculate impact score based on proximity (closer = higher score)
        # Using exponential decay: 100 * e^(-distance/10)
        # At 0km = 100, at 10km ≈ 37, at 20km ≈ 14, at 50km ≈ 0.7
        impact_score = round(100 * math.exp(-distance_km / 10), 1)

        # Check if accident occurred on the same route (fuzzy name matching)
        same_route = False
        if route_name and route.name:
            # Normalize both names for comparison
            route_name_lower = route_name.lower().strip()
            current_route_lower = route.name.lower().strip()
            # Check for exact match or if one contains the other
            same_route = (
                route_name_lower == current_route_lower or
                route_name_lower in current_route_lower or
                current_route_lower in route_name_lower
            )

        # Fetch historical weather for accident date
        weather_data = None
        if acc_date and acc_lat and acc_lon:
            try:
                params = {
                    "latitude": acc_lat,
                    "longitude": acc_lon,
                    "start_date": acc_date.isoformat(),
                    "end_date": acc_date.isoformat(),
                    "daily": [
                        "temperature_2m_mean",
                        "temperature_2m_min",
                        "temperature_2m_max",
                        "precipitation_sum",
                        "wind_speed_10m_max",
                    ],
                    "temperature_unit": "celsius",
                    "wind_speed_unit": "ms",
                    "precipitation_unit": "mm",
                }

                response = requests.get(
                    "https://archive-api.open-meteo.com/v1/archive",
                    params=params,
                    timeout=5
                )

                if response.status_code == 200:
                    data = response.json()
                    daily = data.get("daily", {})

                    if daily and len(daily.get("temperature_2m_mean", [])) > 0:
                        temp_avg = daily["temperature_2m_mean"][0]
                        temp_min = daily["temperature_2m_min"][0]
                        temp_max = daily["temperature_2m_max"][0]
                        precip = daily["precipitation_sum"][0]
                        wind = daily["wind_speed_10m_max"][0]

                        conditions = []
                        if temp_avg is not None:
                            if temp_avg < -10:
                                conditions.append("Extreme Cold")
                            elif temp_avg < 0:
                                conditions.append("Freezing")
                            elif temp_avg < 10:
                                conditions.append("Cold")

                        if precip and precip > 10:
                            conditions.append("Heavy Precip")
                        elif precip and precip > 2:
                            conditions.append("Rain/Snow")

                        if wind and wind > 15:
                            conditions.append("High Winds")
                        elif wind and wind > 10:
                            conditions.append("Windy")

                        weather_data = {
                            "temp": f"{round(temp_min) if temp_min else '?'}-{round(temp_max) if temp_max else '?'}°C",
                            "temp_avg": round(temp_avg, 1) if temp_avg else None,
                            "wind_speed": round(wind, 1) if wind else None,
                            "precipitation": round(precip, 1) if precip else None,
                            "conditions": ", ".join(conditions) if conditions else "Moderate",
                        }
            except Exception as e:
                logger.warning(f"Failed to fetch weather for accident {accident_id}: {e}")

        accidents_data.append({
            "accident_id": accident_id,
            "route_name": route_name or "Unknown",
            "date": acc_date.isoformat() if acc_date else None,
            "description": description if description else None,
            "accident_type": acc_type,
            "injury_severity": severity,
            "location": location,
            "weather": weather_data,
            # New relevance fields
            "distance_km": round(distance_km, 1) if distance_km else None,
            "impact_score": impact_score,
            "same_route": same_route,
            # Additional detail fields
            "source": source,
            "state": state,
            "mountain": mountain,
            "activity": activity,
            "age_range": age_range,
            "tags": tags,
            "elevation_meters": round(elevation_m) if elevation_m else None,
            "coordinates": {"lat": acc_lat, "lon": acc_lon} if acc_lat and acc_lon else None,
        })

    # Get location name if available
    location_name = None
    if route.location_id:
        loc_query = select(MpLocation.name).where(MpLocation.mp_id == route.location_id)
        loc_result = await db.execute(loc_query)
        location_name = loc_result.scalar_one_or_none()

    return {
        "route_id": mp_route_id,
        "route_name": route.name,
        "location_name": location_name or "Unknown Area",
        "total_accidents": len(accidents_data),
        "accidents": accidents_data,
    }


@router.get("/mp-routes/{mp_route_id}/risk-breakdown")
async def get_risk_breakdown(
    mp_route_id: int,
    target_date: date = Query(..., description="Date for risk calculation (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed breakdown of risk score factors.

    Shows what contributed to the risk score calculation with actual algorithm data.
    Coordinates are inherited from the route's parent location.
    """
    # Fetch route with location coordinates
    route = await get_route_with_location_coords(db, mp_route_id)

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    if route.latitude is None or route.longitude is None:
        raise HTTPException(status_code=400, detail="Route's location missing GPS coordinates")

    # Fetch elevation for the route
    from app.services.elevation_service import fetch_elevation
    elevation_meters = fetch_elevation(route.latitude, route.longitude)

    # Calculate safety score with full details
    normalized_type = normalize_route_type(route.type)
    prediction_request = PredictionRequest(
        latitude=route.latitude,
        longitude=route.longitude,
        route_type=normalized_type,
        planned_date=target_date,
        elevation_meters=elevation_meters,
    )

    prediction = await predict_route_safety(prediction_request, db)

    # Extract actual factor contributions from top contributing accidents
    spatial_weights = [acc.spatial_weight for acc in prediction.top_contributing_accidents[:10]]
    temporal_weights = [acc.temporal_weight for acc in prediction.top_contributing_accidents[:10]]
    weather_weights = [acc.weather_weight for acc in prediction.top_contributing_accidents[:10]]
    route_type_weights = [acc.route_type_weight for acc in prediction.top_contributing_accidents[:10]]
    elevation_weights = [acc.elevation_weight for acc in prediction.top_contributing_accidents[:10]]

    # Blend elevation into spatial as a micro-boost (no separate category)
    combined_spatial = [
        s * e for s, e in zip(spatial_weights, elevation_weights)
    ] if spatial_weights and elevation_weights else spatial_weights

    # Calculate average weights (normalized to percentages)
    avg_spatial = (sum(combined_spatial) / len(combined_spatial) * 100) if combined_spatial else 0
    avg_temporal = (sum(temporal_weights) / len(temporal_weights) * 100) if temporal_weights else 0
    avg_weather = (sum(weather_weights) / len(weather_weights) * 100) if weather_weights else 0
    avg_route_type = (sum(route_type_weights) / len(route_type_weights) * 100) if route_type_weights else 0

    # Normalize to 100%
    total_weight = avg_spatial + avg_temporal + avg_weather + avg_route_type
    if total_weight > 0:
        avg_spatial = (avg_spatial / total_weight) * 100
        avg_temporal = (avg_temporal / total_weight) * 100
        avg_weather = (avg_weather / total_weight) * 100
        avg_route_type = (avg_route_type / total_weight) * 100

    # Calculate median days_ago from top accidents
    days_ago_list = sorted([acc.days_ago for acc in prediction.top_contributing_accidents[:10]])
    median_days_ago = days_ago_list[len(days_ago_list) // 2] if days_ago_list else 0

    # Build factor breakdown with actual data
    factors = []

    if prediction.num_contributing_accidents > 0:
        factors.append({
            "name": "Spatial Proximity",
            "contribution": round(avg_spatial, 1),
            "description": f"{prediction.num_contributing_accidents} nearby accidents within search radius, "
                          f"weighted by distance (includes small elevation bonus for similar altitude)"
        })

        factors.append({
            "name": "Temporal Recency",
            "contribution": round(avg_temporal, 1),
            "description": f"Recent accidents weighted more heavily. "
                          f"Median: {median_days_ago} days ago"
        })

        factors.append({
            "name": "Weather Similarity",
            "contribution": round(avg_weather, 1),
            "description": "Pattern matching between forecast and accident conditions"
        })

        factors.append({
            "name": "Route Type Match",
            "contribution": round(avg_route_type, 1),
            "description": f"Climbing discipline similarity ({route.type} routes). "
                          f"Asymmetric weighting accounts for shared risk factors"
        })
    else:
        factors.append({
            "name": "No Data",
            "contribution": 0,
            "description": "Insufficient accident data for risk calculation"
        })

    return {
        "route_id": mp_route_id,
        "route_name": route.name,
        "target_date": target_date.isoformat(),
        "risk_score": round(prediction.risk_score, 1),
        "num_contributing_accidents": prediction.num_contributing_accidents,
        "factors": factors,
        "top_accidents": [
            {
                "accident_id": acc.accident_id,
                "distance_km": round(acc.distance_km, 1),
                "days_ago": acc.days_ago,
                "total_influence": round(acc.total_influence, 3),
            }
            for acc in prediction.top_contributing_accidents[:5]
        ],
    }


@router.get("/mp-routes/{mp_route_id}/seasonal-patterns")
async def get_seasonal_patterns(
    mp_route_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get seasonal accident patterns for the route's area.

    Shows accident frequency and average risk by month using geographic proximity.
    Coordinates are inherited from the route's parent location.
    """
    # Fetch route with location coordinates
    route = await get_route_with_location_coords(db, mp_route_id)

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    if not route.latitude or not route.longitude:
        raise HTTPException(status_code=400, detail="Route's location missing GPS coordinates")

    # Get monthly accident counts using geographic proximity (~50km radius)
    # Risk score calculated from injury severity:
    #   fatal=100, serious=80, moderate=60, minor=40, unknown=30
    monthly_query = text("""
        SELECT
            EXTRACT(MONTH FROM a.date) as month,
            COUNT(*) as accident_count,
            AVG(CASE
                WHEN LOWER(a.injury_severity) LIKE '%fatal%' OR LOWER(a.injury_severity) LIKE '%death%' THEN 100
                WHEN LOWER(a.injury_severity) LIKE '%serious%' OR LOWER(a.injury_severity) LIKE '%severe%' THEN 80
                WHEN LOWER(a.injury_severity) LIKE '%moderate%' THEN 60
                WHEN LOWER(a.injury_severity) LIKE '%minor%' OR LOWER(a.injury_severity) LIKE '%light%' THEN 40
                ELSE 30
            END) as avg_risk_score,
            AVG(CURRENT_DATE - a.date) as avg_days_ago
        FROM accidents a
        WHERE a.date IS NOT NULL
          AND a.latitude IS NOT NULL
          AND a.longitude IS NOT NULL
          AND (
              6371 * acos(
                  cos(radians(:lat)) * cos(radians(a.latitude)) *
                  cos(radians(a.longitude) - radians(:lon)) +
                  sin(radians(:lat)) * sin(radians(a.latitude))
              )
          ) < 50
        GROUP BY EXTRACT(MONTH FROM a.date)
        ORDER BY month
    """)
    result = await db.execute(monthly_query, {"lat": route.latitude, "lon": route.longitude})
    monthly_data = result.fetchall()

    # Build monthly patterns array (all 12 months)
    monthly_patterns = []
    month_data_dict = {int(row[0]): row for row in monthly_data}

    for month_num in range(1, 13):
        month_name = calendar.month_abbr[month_num]
        data = month_data_dict.get(month_num)

        if data:
            monthly_patterns.append({
                "month": month_name,
                "month_num": month_num,
                "accident_count": int(data[1]),
                "avg_risk_score": round(float(data[2]), 1),
                "avg_temp": None,
            })
        else:
            monthly_patterns.append({
                "month": month_name,
                "month_num": month_num,
                "accident_count": 0,
                "avg_risk_score": 0,
                "avg_temp": None,
            })

    # Find best and worst months
    months_with_data = [m for m in monthly_patterns if m["accident_count"] > 0]
    best_months = sorted(months_with_data, key=lambda x: x["accident_count"])[:3]
    worst_months = sorted(months_with_data, key=lambda x: x["accident_count"], reverse=True)[:3]

    # Get location name
    location_name = None
    if route.location_id:
        loc_query = select(MpLocation.name).where(MpLocation.mp_id == route.location_id)
        loc_result = await db.execute(loc_query)
        location_name = loc_result.scalar_one_or_none()

    return {
        "route_id": mp_route_id,
        "route_name": route.name,
        "location_name": location_name or "Unknown Area",
        "monthly_patterns": monthly_patterns,
        "best_months": [{"name": m["month"], "avg_risk": m["avg_risk_score"], "accident_count": m["accident_count"]} for m in best_months],
        "worst_months": [{"name": m["month"], "avg_risk": m["avg_risk_score"], "accident_count": m["accident_count"]} for m in worst_months],
    }


@router.get("/mp-routes/{mp_route_id}/time-of-day")
async def get_time_of_day_analysis(
    mp_route_id: int,
    target_date: date = Query(..., description="Date to analyze (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get hourly weather and risk score analysis for a specific day.

    Helps climbers identify the optimal time window for their ascent.
    Coordinates are inherited from the route's parent location.
    """
    import requests

    # Fetch route with location coordinates
    route = await get_route_with_location_coords(db, mp_route_id)

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    if route.latitude is None or route.longitude is None:
        raise HTTPException(status_code=400, detail="Route's location missing GPS coordinates")

    # Fetch elevation for the route
    from app.services.elevation_service import fetch_elevation
    elevation_meters = fetch_elevation(route.latitude, route.longitude)

    # Fetch hourly weather data from Open-Meteo (uses commercial API if configured)
    try:
        params = {
            "latitude": route.latitude,
            "longitude": route.longitude,
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
            "hourly": [
                "temperature_2m",
                "precipitation",
                "wind_speed_10m",
                "wind_gusts_10m",
                "cloud_cover",
                "visibility",
            ],
            "temperature_unit": "celsius",
            "wind_speed_unit": "ms",
            "precipitation_unit": "mm",
            "timezone": "auto",
        }

        # Add API key if using commercial API
        if OPEN_METEO_API_KEY:
            params["apikey"] = OPEN_METEO_API_KEY

        response = requests.get(WEATHER_API_URL, params=params, timeout=10)
        response.raise_for_status()
        weather_data = response.json()

        hourly = weather_data["hourly"]
        times = hourly["time"]
        temperatures = hourly["temperature_2m"]
        precipitations = hourly["precipitation"]
        wind_speeds = hourly["wind_speed_10m"]
        wind_gusts = hourly["wind_gusts_10m"]
        cloud_covers = hourly["cloud_cover"]
        visibilities = hourly["visibility"]

        # Get base daily risk (use fetched elevation)
        normalized_type = normalize_route_type(route.type)
        prediction_request = PredictionRequest(
            latitude=route.latitude,
            longitude=route.longitude,
            route_type=normalized_type,
            planned_date=target_date,
            elevation_meters=elevation_meters,
        )
        base_prediction = await predict_route_safety(prediction_request, db)
        base_risk = base_prediction.risk_score

        # Analyze each hour
        hourly_data = []
        for i in range(len(times)):
            hour = int(times[i].split("T")[1][:2])
            temp = temperatures[i]
            precip = precipitations[i]
            wind = wind_speeds[i]
            gust = wind_gusts[i]
            cloud = cloud_covers[i]
            visibility = visibilities[i]

            # Calculate hourly risk adjustment
            risk_adjustment = 0.0

            if temp is not None:
                if temp < -15:
                    risk_adjustment += 15
                elif temp < -5:
                    risk_adjustment += 8
                elif temp > 30:
                    risk_adjustment += 5

            if precip is not None:
                if precip > 5:
                    risk_adjustment += 20
                elif precip > 1:
                    risk_adjustment += 10
                elif precip > 0.2:
                    risk_adjustment += 3

            if gust is not None and gust > 20:
                risk_adjustment += 15
            elif wind is not None:
                if wind > 15:
                    risk_adjustment += 10
                elif wind > 10:
                    risk_adjustment += 5

            if visibility is not None and visibility < 1000:
                risk_adjustment += 10
            elif visibility is not None and visibility < 5000:
                risk_adjustment += 5

            hourly_risk = min(max(base_risk + risk_adjustment, 0), 100)

            # Determine condition summary
            conditions = []
            if temp is not None and temp < -10:
                conditions.append("Very Cold")
            elif temp is not None and temp > 25:
                conditions.append("Hot")
            if precip is not None and precip > 1:
                conditions.append("Rain/Snow")
            if wind is not None and wind > 10:
                conditions.append("Windy")
            if visibility is not None and visibility < 5000:
                conditions.append("Low Visibility")

            if not conditions:
                if hourly_risk < 30:
                    conditions.append("Good Conditions")
                elif hourly_risk < 50:
                    conditions.append("Moderate")
                else:
                    conditions.append("Cautious")

            is_daylight = 6 <= hour <= 18
            is_climbable = hourly_risk < 70 and (precip is None or precip < 5) and (wind is None or wind < 20)

            hourly_data.append({
                "hour": hour,
                "time": times[i],
                "risk_score": round(hourly_risk, 1),
                "temperature": round(temp, 1) if temp else None,
                "precipitation": round(precip, 2) if precip else None,
                "wind_speed": round(wind, 1) if wind else None,
                "wind_gusts": round(gust, 1) if gust else None,
                "cloud_cover": round(cloud, 0) if cloud else None,
                "visibility": round(visibility, 0) if visibility else None,
                "conditions_summary": ", ".join(conditions),
                "is_daylight": is_daylight,
                "is_climbable": is_climbable and is_daylight,
            })

        # Find best climbing windows
        windows = []
        current_window = []

        for hour_data in hourly_data:
            if hour_data["is_climbable"]:
                current_window.append(hour_data)
            else:
                if len(current_window) >= 2:
                    avg_risk = sum(h["risk_score"] for h in current_window) / len(current_window)
                    windows.append({
                        "start_hour": current_window[0]["hour"],
                        "end_hour": current_window[-1]["hour"],
                        "duration_hours": len(current_window),
                        "avg_risk": round(avg_risk, 1),
                        "conditions": current_window[len(current_window)//2]["conditions_summary"],
                    })
                current_window = []

        if len(current_window) >= 2:
            avg_risk = sum(h["risk_score"] for h in current_window) / len(current_window)
            windows.append({
                "start_hour": current_window[0]["hour"],
                "end_hour": current_window[-1]["hour"],
                "duration_hours": len(current_window),
                "avg_risk": round(avg_risk, 1),
                "conditions": current_window[len(current_window)//2]["conditions_summary"],
            })

        windows.sort(key=lambda w: w["avg_risk"])

        return {
            "route_id": mp_route_id,
            "route_name": route.name,
            "target_date": target_date.isoformat(),
            "base_daily_risk": round(base_risk, 1),
            "hourly_data": hourly_data,
            "climbing_windows": windows,
            "best_window": windows[0] if windows else None,
            "recommendation": (
                f"Best window: {windows[0]['start_hour']:02d}:00-{windows[0]['end_hour']:02d}:00 "
                f"(Risk: {windows[0]['avg_risk']}/100)"
            ) if windows else "No suitable climbing windows identified for this date",
        }

    except Exception as e:
        logger.error(f"Error fetching hourly weather: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch hourly weather data: {str(e)}")


@router.get("/mp-routes/{mp_route_id}/historical-trends")
async def get_historical_trends(
    mp_route_id: int,
    days: int = Query(30, ge=1, le=365, description="Number of days of history to return"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get historical risk score trends for this route.

    Requires historical_predictions table to be populated via backfill script.
    """
    # Fetch route
    route_query = select(MpRoute).where(MpRoute.mp_route_id == mp_route_id)
    route_result = await db.execute(route_query)
    route = route_result.scalar_one_or_none()

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    # Fetch historical predictions
    historical_query = text("""
        SELECT
            prediction_date,
            risk_score,
            color_code
        FROM historical_predictions
        WHERE route_id = :route_id
          AND prediction_date >= CURRENT_DATE - INTERVAL ':days days'
        ORDER BY prediction_date ASC
    """)

    try:
        result = await db.execute(historical_query, {"route_id": mp_route_id, "days": days})
        historical_data = result.fetchall()

        if not historical_data:
            return {
                "route_id": mp_route_id,
                "route_name": route.name,
                "historical_predictions": [],
                "summary": None,
                "trend": None,
                "message": "Historical data not yet available. Run backfill script to collect historical predictions."
            }

        predictions = [
            {
                "date": row[0].isoformat(),
                "risk_score": round(float(row[1]), 1),
                "color_code": row[2],
            }
            for row in historical_data
        ]

        risk_scores = [p["risk_score"] for p in predictions]
        summary = {
            "avg_risk": round(sum(risk_scores) / len(risk_scores), 1),
            "min_risk": round(min(risk_scores), 1),
            "max_risk": round(max(risk_scores), 1),
        }

        trend = None
        if len(predictions) >= 7:
            recent_avg = sum(risk_scores[-7:]) / 7
            older_avg = sum(risk_scores[:7]) / 7

            if recent_avg > older_avg + 5:
                trend = {"direction": "increasing", "description": "Risk has increased over the past week"}
            elif recent_avg < older_avg - 5:
                trend = {"direction": "decreasing", "description": "Risk has decreased over the past week"}
            else:
                trend = {"direction": "stable", "description": "Risk has remained relatively stable"}

        return {
            "route_id": mp_route_id,
            "route_name": route.name,
            "days_available": len(predictions),
            "historical_predictions": predictions,
            "summary": summary,
            "trend": trend,
        }

    except Exception as e:
        logger.error(f"Error fetching historical trends: {e}")
        return {
            "route_id": mp_route_id,
            "route_name": route.name,
            "historical_predictions": [],
            "summary": None,
            "trend": None,
            "error": "Historical predictions table may not exist. Run backfill script first."
        }


@router.get("/mp-routes/{mp_route_id}/ascent-analytics")
async def get_ascent_analytics(
    mp_route_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get ascent analytics for a route including monthly breakdown and accident rate.

    Queries the mp_ticks table for tick/ascent data linked via route_id (mp_route_id).
    Combines with nearby accident data to calculate accident rates.
    Coordinates are inherited from the route's parent location.
    """
    # Fetch route with location coordinates
    route = await get_route_with_location_coords(db, mp_route_id)

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    # Check if route is a boulder problem (excluded from analytics)
    route_type = (route.type or '').lower()
    if route_type in ['boulder', 'bouldering']:
        return {
            "route_id": mp_route_id,
            "route_name": route.name,
            "route_type": route.type,
            "total_ascents": 0,
            "total_accidents": 0,
            "overall_accident_rate": 0.0,
            "monthly_stats": [],
            "best_month": None,
            "worst_month": None,
            "has_data": False,
            "excluded_reason": "Boulder problems are excluded from safety analytics",
        }

    # Query mp_ticks table for ascent data
    # The mp_ticks table uses route_id which matches mp_route_id
    route_id_str = str(mp_route_id)
    total_ascents_query = text("""
        SELECT COUNT(*) FROM mp_ticks WHERE route_id = :route_id
    """)
    total_result = await db.execute(total_ascents_query, {"route_id": route_id_str})
    total_ascents = total_result.scalar() or 0

    # Get monthly ascent breakdown
    monthly_ascents_query = text("""
        SELECT
            EXTRACT(MONTH FROM tick_date) as month,
            COUNT(*) as ascent_count
        FROM mp_ticks
        WHERE route_id = :route_id
          AND tick_date IS NOT NULL
        GROUP BY EXTRACT(MONTH FROM tick_date)
        ORDER BY month
    """)
    monthly_result = await db.execute(monthly_ascents_query, {"route_id": route_id_str})
    monthly_ascent_rows = monthly_result.fetchall()

    # Build monthly ascent dict
    monthly_ascent_dict = {int(row[0]): int(row[1]) for row in monthly_ascent_rows}

    # Get total accidents within 10km of this route
    accident_count_query = text("""
        SELECT COUNT(*)
        FROM accidents
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          AND (
              6371 * acos(
                  cos(radians(:lat)) * cos(radians(latitude)) *
                  cos(radians(longitude) - radians(:lon)) +
                  sin(radians(:lat)) * sin(radians(latitude))
              )
          ) < 10
    """)

    total_accidents = 0
    if route.latitude and route.longitude:
        accident_result = await db.execute(
            accident_count_query,
            {"lat": route.latitude, "lon": route.longitude}
        )
        total_accidents = accident_result.scalar() or 0

    # Get monthly accident breakdown for this area
    monthly_accidents_query = text("""
        SELECT
            EXTRACT(MONTH FROM date) as month,
            COUNT(*) as accident_count
        FROM accidents
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          AND date IS NOT NULL
          AND (
              6371 * acos(
                  cos(radians(:lat)) * cos(radians(latitude)) *
                  cos(radians(longitude) - radians(:lon)) +
                  sin(radians(:lat)) * sin(radians(latitude))
              )
          ) < 10
        GROUP BY EXTRACT(MONTH FROM date)
        ORDER BY month
    """)

    monthly_accident_dict = {}
    if route.latitude and route.longitude:
        monthly_acc_result = await db.execute(
            monthly_accidents_query,
            {"lat": route.latitude, "lon": route.longitude}
        )
        monthly_accident_rows = monthly_acc_result.fetchall()
        monthly_accident_dict = {int(row[0]): int(row[1]) for row in monthly_accident_rows}

    # Build monthly stats array with both ascents and accidents
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    monthly_stats = []
    for i in range(12):
        month_num = i + 1
        ascent_count = monthly_ascent_dict.get(month_num, 0)
        accident_count = monthly_accident_dict.get(month_num, 0)

        # Calculate accident rate per 1000 ascents (if we have ascent data)
        if ascent_count > 0:
            accident_rate = round((accident_count / ascent_count) * 1000, 2)
        else:
            accident_rate = 0.0

        monthly_stats.append({
            "month": month_names[i],
            "month_num": month_num,
            "ascent_count": ascent_count,
            "accident_count": accident_count,
            "accident_rate": accident_rate,
        })

    # Calculate overall accident rate
    if total_ascents > 0:
        overall_accident_rate = round((total_accidents / total_ascents) * 1000, 2)
    else:
        overall_accident_rate = 0.0

    # Find best/worst months (only among months with ascent data)
    months_with_ascents = [m for m in monthly_stats if m["ascent_count"] > 0]

    best_month = None
    worst_month = None
    peak_month = None

    if months_with_ascents:
        # Best month = lowest accident rate
        best_month = min(months_with_ascents, key=lambda x: x["accident_rate"])
        # Worst month = highest accident rate
        worst_month = max(months_with_ascents, key=lambda x: x["accident_rate"])
        # Peak month = most ascents
        peak_month = max(months_with_ascents, key=lambda x: x["ascent_count"])

    has_data = total_ascents > 0

    response = {
        "route_id": mp_route_id,
        "route_name": route.name,
        "route_type": route.type,
        "total_ascents": total_ascents,
        "total_accidents": total_accidents,
        "overall_accident_rate": overall_accident_rate,
        "monthly_stats": monthly_stats,
        "best_month": best_month["month"] if best_month else None,
        "worst_month": worst_month["month"] if worst_month else None,
        "peak_month": peak_month["month"] if peak_month else None,
        "has_data": has_data,
    }

    if not has_data:
        response["message"] = "No tick data available yet for this route."

    return response


# =============================================================================
# ADMIN ENDPOINTS - Cache Management
# =============================================================================

@router.get("/mp-routes/admin/trigger-cache-population")
async def trigger_cache_population(
    target_date: Optional[str] = Query(None, description="Specific date (YYYY-MM-DD) or leave empty for 7-day run"),
):
    """
    Manually trigger safety score cache population via Celery background task.

    Returns immediately with task ID. Check Railway logs for progress.

    **Options:**
    - No params: Compute all 7 days (takes 10-20 minutes)
    - `target_date=2026-02-05`: Compute single date (takes 2-3 minutes)

    **Returns:** Task ID and status message (task runs in background).
    """
    from app.tasks.safety_computation_optimized import (
        compute_daily_safety_scores_optimized,
    )

    logger.info("=" * 60)
    logger.info("MANUAL CACHE POPULATION TRIGGERED VIA API")
    logger.info("=" * 60)

    try:
        # Use optimized location-level computation (today only)
        # target_date parameter is ignored - optimized task always computes today
        logger.info("Triggering OPTIMIZED Celery task (location-level computation)...")
        task = compute_daily_safety_scores_optimized.delay()
        return {
            "status": "started",
            "message": "Optimized cache population started (today only). Check Railway logs for progress.",
            "task_id": task.id,
            "estimated_time": "5-10 minutes",
        }

    except Exception as e:
        logger.error(f"Failed to trigger cache population: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger cache population: {str(e)}"
        )


@router.get("/mp-routes/admin/task-status/{task_id}")
async def get_task_status(task_id: str):
    """
    Check the status of a Celery background task.

    **Returns:**
    - `pending`: Task is waiting to be picked up by a worker
    - `started`: Task is currently running
    - `success`: Task completed successfully (includes result)
    - `failure`: Task failed (includes error message)
    - `revoked`: Task was cancelled
    """
    from app.celery_app import celery_app

    try:
        result = celery_app.AsyncResult(task_id)

        response = {
            "task_id": task_id,
            "status": result.status.lower(),
            "ready": result.ready(),
        }

        if result.ready():
            if result.successful():
                response["result"] = result.result
            else:
                response["error"] = str(result.result)

        return response

    except Exception as e:
        logger.error(f"Failed to get task status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get task status: {str(e)}"
        )


@router.get("/mp-routes/admin/queue-info")
async def get_queue_info():
    """
    Get info about Celery queue and purge stale tasks if needed.
    """
    from app.celery_app import celery_app
    import redis
    from app.config import settings

    try:
        # Connect to Redis directly to inspect queue
        r = redis.from_url(settings.REDIS_URL)

        # Get queue length
        queue_length = r.llen("celery")

        # Get active workers
        inspect = celery_app.control.inspect()
        active_workers = inspect.active()
        registered = inspect.registered()

        return {
            "queue_length": queue_length,
            "active_workers": active_workers,
            "registered_tasks": registered,
            "redis_connected": True,
        }
    except Exception as e:
        return {
            "error": str(e),
            "redis_connected": False,
        }


@router.get("/mp-routes/admin/purge-queue")
async def purge_queue():
    """
    Purge all pending tasks from the Celery queue.
    Use with caution - this removes ALL queued tasks.
    """
    from app.celery_app import celery_app

    try:
        purged = celery_app.control.purge()
        return {
            "status": "purged",
            "tasks_removed": purged,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }


@router.get("/mp-routes/admin/redis-debug")
async def redis_debug():
    """
    Debug Redis connection and see all Celery-related keys.
    """
    import redis
    from app.config import settings

    try:
        r = redis.from_url(settings.REDIS_URL)

        # Get all keys
        all_keys = [k.decode() for k in r.keys("*")]
        celery_keys = [k for k in all_keys if "celery" in k.lower()]

        # Check specific queues
        queue_lengths = {}
        for key in ["celery", "celery:default", "default"]:
            try:
                queue_lengths[key] = r.llen(key)
            except Exception:
                queue_lengths[key] = "N/A"

        return {
            "redis_url": settings.REDIS_URL[:50] + "...",  # Truncate for security
            "total_keys": len(all_keys),
            "celery_keys": celery_keys[:20],  # First 20
            "queue_lengths": queue_lengths,
            "ping": r.ping(),
        }
    except Exception as e:
        return {"error": str(e)}
