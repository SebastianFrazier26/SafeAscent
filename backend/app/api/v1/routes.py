"""
Routes API endpoints.
"""
from typing import Optional, List, Dict, Any
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, text
import logging
import calendar

from app.db.session import get_db
from app.models.route import Route
from app.models.mountain import Mountain
from app.models.accident import Accident
from app.schemas.route import RouteResponse, RouteListResponse, RouteDetail, RouteMapMarker, RouteMapResponse, RouteSafetyResponse
from app.schemas.prediction import PredictionRequest
from app.api.v1.predict import predict_route_safety
from app.utils.cache import cache_get, cache_set, build_safety_score_key

router = APIRouter()
logger = logging.getLogger(__name__)


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
        'yds': 'trad',  # YDS grading system typically used for trad climbing
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
        'rock': 'trad',  # Generic rock climbing -> trad
    }

    return type_mapping.get(route_type, 'trad')  # Default to trad


@router.get("/routes", response_model=RouteListResponse)
async def list_routes(
    search: Optional[str] = Query(None, description="Search route name"),
    mountain_id: Optional[int] = Query(None, description="Filter by mountain ID"),
    state: Optional[str] = Query(None, description="Filter by state"),
    grade: Optional[str] = Query(None, description="Filter by grade (e.g., '5.10')"),
    min_length: Optional[float] = Query(None, description="Minimum length in feet"),
    max_length: Optional[float] = Query(None, description="Maximum length in feet"),
    limit: int = Query(50, ge=1, le=500, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    db: AsyncSession = Depends(get_db),
):
    """
    List and search routes with optional filters.

    - **search**: Search in route name
    - **mountain_id**: Filter by specific mountain
    - **state**: Filter by state
    - **grade**: Filter by YDS grade
    - **min_length**: Minimum route length in feet
    - **max_length**: Maximum route length in feet
    - **limit**: Number of results (max 500)
    - **offset**: Pagination offset
    """
    # Build query
    query = select(Route)

    # Apply filters
    if search:
        query = query.where(Route.name.ilike(f"%{search}%"))
    if mountain_id is not None:
        query = query.where(Route.mountain_id == mountain_id)
    if state:
        # Join with mountains table to access state information
        query = query.join(Mountain, Route.mountain_id == Mountain.mountain_id)
        query = query.where(Mountain.state == state)
    if grade:
        query = query.where(or_(
            Route.grade.ilike(f"%{grade}%"),
            Route.grade_yds.ilike(f"%{grade}%")
        ))
    if min_length is not None:
        query = query.where(Route.length_ft >= min_length)
    if max_length is not None:
        query = query.where(Route.length_ft <= max_length)

    # Order by accident count descending
    query = query.order_by(Route.accident_count.desc())

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.limit(limit).offset(offset)

    # Execute query
    result = await db.execute(query)
    routes = result.scalars().all()

    return RouteListResponse(
        total=total,
        data=[RouteResponse.model_validate(r) for r in routes]
    )


@router.get("/routes/map", response_model=RouteMapResponse)
async def get_routes_for_map(
    min_lat: Optional[float] = Query(None, description="Minimum latitude (bounding box)"),
    max_lat: Optional[float] = Query(None, description="Maximum latitude (bounding box)"),
    min_lon: Optional[float] = Query(None, description="Minimum longitude (bounding box)"),
    max_lon: Optional[float] = Query(None, description="Maximum longitude (bounding box)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all routes with coordinates for map display.
    Returns minimal data optimized for map markers.

    - **min_lat, max_lat, min_lon, max_lon**: Optional bounding box to filter routes
    """
    # Build query - only routes with valid coordinates
    query = select(Route).where(
        Route.latitude.isnot(None),
        Route.longitude.isnot(None)
    )

    # Apply bounding box filter if provided
    if all([min_lat, max_lat, min_lon, max_lon]):
        query = query.where(
            Route.latitude >= min_lat,
            Route.latitude <= max_lat,
            Route.longitude >= min_lon,
            Route.longitude <= max_lon
        )

    # Execute query
    result = await db.execute(query)
    routes = result.scalars().all()

    return RouteMapResponse(
        total=len(routes),
        routes=[RouteMapMarker.model_validate(r) for r in routes]
    )


@router.get("/routes/{route_id}", response_model=RouteDetail)
async def get_route(
    route_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed information about a specific route.

    - **route_id**: Route ID
    """
    query = select(Route).where(Route.route_id == route_id)
    result = await db.execute(query)
    route = result.scalar_one_or_none()

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    return RouteDetail.model_validate(route)


@router.post("/routes/{route_id}/safety", response_model=RouteSafetyResponse)
async def calculate_route_safety(
    route_id: int,
    target_date: date = Query(..., description="Target date for safety calculation (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Calculate safety score for a specific route on a given date.

    Uses Redis caching with 6-hour TTL to avoid redundant calculations.
    Date must be within the next 7 days (weather forecast window).

    - **route_id**: Route ID from database
    - **target_date**: Date to calculate safety for (ISO format: YYYY-MM-DD)

    **Returns**:
    - `risk_score`: 0-100 (higher = more dangerous)
    - `confidence`: 0-100 (higher = more confident)
    - `color_code`: 'green', 'yellow', 'orange', 'red', or 'gray' for map marker coloring
    """
    # Validate date is within 7-day window
    today = date.today()
    max_date = today + timedelta(days=6)

    if target_date < today:
        raise HTTPException(
            status_code=400,
            detail=f"target_date must be today or in the future (today: {today.isoformat()})"
        )
    if target_date > max_date:
        raise HTTPException(
            status_code=400,
            detail=f"target_date must be within 7 days (max: {max_date.isoformat()})"
        )

    # Fetch route from database
    query = select(Route).where(Route.route_id == route_id)
    result = await db.execute(query)
    route = result.scalar_one_or_none()

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    # Validate route has required data
    if route.latitude is None or route.longitude is None:
        raise HTTPException(
            status_code=400,
            detail="Route missing GPS coordinates required for safety calculation"
        )

    # Normalize route type (handles YDS, unknown types, etc.)
    normalized_type = normalize_route_type(route.type)
    logger.info(f"Route {route_id} type: '{route.type}' -> normalized: '{normalized_type}'")

    # Check Redis cache
    cache_key = build_safety_score_key(route_id, target_date.isoformat())
    cached_result = cache_get(cache_key)

    if cached_result:
        logger.info(f"Cache HIT for route {route_id} on {target_date}")
        return RouteSafetyResponse(**cached_result)

    logger.info(f"Cache MISS for route {route_id} on {target_date} - calculating...")

    # Create prediction request
    prediction_request = PredictionRequest(
        latitude=route.latitude,
        longitude=route.longitude,
        route_type=normalized_type,
        planned_date=target_date,
        elevation_meters=None,  # Auto-detect elevation
    )

    try:
        # Calculate safety using existing prediction algorithm
        prediction = await predict_route_safety(prediction_request, db)

        # Determine color code based on risk score
        color_code = get_safety_color_code(prediction.risk_score)

        # Build response
        safety_response = RouteSafetyResponse(
            route_id=route_id,
            route_name=route.name,
            target_date=target_date.isoformat(),
            risk_score=round(prediction.risk_score, 1),
            color_code=color_code
        )

        # Cache result for 6 hours (21600 seconds)
        # TTL matches weather data refresh rate
        cache_set(cache_key, safety_response.model_dump(), ttl_seconds=21600)
        logger.info(f"Cached safety score for route {route_id} on {target_date}")

        return safety_response

    except Exception as e:
        logger.error(f"Error calculating safety for route {route_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to calculate safety score: {str(e)}"
        )


def get_safety_color_code(risk_score: float) -> str:
    """
    Determine marker color based on risk score.

    Args:
        risk_score: Risk score (0-100)

    Returns:
        Color code: 'green', 'yellow', 'orange', 'red'
    """
    if risk_score < 30:
        return 'green'  # Safe
    elif risk_score < 50:
        return 'yellow'  # Moderate caution
    elif risk_score < 70:
        return 'orange'  # High caution
    else:
        return 'red'  # Dangerous


# ============================================================================
# ANALYTICS ENDPOINTS
# ============================================================================

@router.get("/routes/{route_id}/forecast")
async def get_route_forecast(
    route_id: int,
    start_date: date = Query(..., description="Start date for forecast (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get 7-day safety forecast for a route starting from the specified date.

    Returns detailed weather and risk scores for each day.
    """
    from app.services.weather_service import fetch_current_weather_pattern

    # Fetch route
    query = select(Route).where(Route.route_id == route_id)
    result = await db.execute(query)
    route = result.scalar_one_or_none()

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    if route.latitude is None or route.longitude is None:
        raise HTTPException(status_code=400, detail="Route missing GPS coordinates")

    # Calculate forecast for 7 days
    forecast_days = []
    normalized_type = normalize_route_type(route.type)

    for day_offset in range(7):
        target_date = start_date + timedelta(days=day_offset)

        # Create prediction request
        prediction_request = PredictionRequest(
            latitude=route.latitude,
            longitude=route.longitude,
            route_type=normalized_type,
            planned_date=target_date,
            elevation_meters=None,
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
                "temp_high": round(temp_max, 1) if temp_max else None,
                "temp_low": round(temp_min, 1) if temp_min else None,
                "temp_avg": round(temp_avg, 1) if temp_avg else None,
                "precip_mm": round(precip, 1) if precip else None,
                "precip_chance": int(min(precip * 10, 100)) if precip else None,  # Rough estimate
                "wind_speed": round(wind, 1) if wind else None,
                "cloud_cover": round(cloud, 0) if cloud else None,
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
        "route_id": route_id,
        "route_name": route.name,
        "start_date": start_date.isoformat(),
        "forecast_days": forecast_days,
        "today": today_data,
    }


@router.get("/routes/{route_id}/accidents")
async def get_route_accidents(
    route_id: int,
    limit: int = Query(50, ge=1, le=100, description="Maximum accidents to return"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get accident reports for the same mountain as this route.

    Highlights accidents that occurred on the same route.
    Includes weather data for accident dates when available.
    """
    # Fetch route to get mountain_id
    route_query = select(Route).where(Route.route_id == route_id)
    route_result = await db.execute(route_query)
    route = route_result.scalar_one_or_none()

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    # Fetch accidents from the same mountain
    accidents_query = (
        select(Accident)
        .join(Route, Accident.route_id == Route.route_id)
        .where(Route.mountain_id == route.mountain_id)
        .order_by(Accident.date.desc().nullslast())
        .limit(limit)
    )

    accidents_result = await db.execute(accidents_query)
    accidents = accidents_result.scalars().all()

    # Format accident data with weather
    import requests

    accidents_data = []
    for accident in accidents:
        # Fetch historical weather for accident date
        weather_data = None
        if accident.date and accident.latitude and accident.longitude:
            try:
                # Query Open-Meteo historical API
                params = {
                    "latitude": accident.latitude,
                    "longitude": accident.longitude,
                    "start_date": accident.date.isoformat(),
                    "end_date": accident.date.isoformat(),
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

                        # Determine conditions
                        conditions = []
                        if temp_avg < -10:
                            conditions.append("Extreme Cold")
                        elif temp_avg < 0:
                            conditions.append("Freezing")
                        elif temp_avg < 10:
                            conditions.append("Cold")

                        if precip > 10:
                            conditions.append("Heavy Precip")
                        elif precip > 2:
                            conditions.append("Rain/Snow")

                        if wind > 15:
                            conditions.append("High Winds")
                        elif wind > 10:
                            conditions.append("Windy")

                        weather_data = {
                            "temp": f"{round(temp_min)}-{round(temp_max)}°C",
                            "temp_avg": round(temp_avg, 1),
                            "wind_speed": round(wind, 1),
                            "precipitation": round(precip, 1),
                            "conditions": ", ".join(conditions) if conditions else "Moderate",
                        }
            except Exception as e:
                logger.warning(f"Failed to fetch weather for accident {accident.accident_id}: {e}")

        accidents_data.append({
            "accident_id": accident.accident_id,
            "route_id": accident.route_id,
            "route_name": accident.route or "Unknown",
            "same_route": accident.route_id == route_id,
            "date": accident.date.isoformat() if accident.date else None,
            "description": accident.description[:500] if accident.description else "No description available",
            "accident_type": accident.accident_type,
            "injury_severity": accident.injury_severity,
            "impact_score": 100 if accident.route_id == route_id else 50,  # Higher score for same route
            "weather": weather_data,
        })

    return {
        "route_id": route_id,
        "route_name": route.name,
        "mountain_id": route.mountain_id,
        "mountain_name": route.mountain_name or "Unknown",
        "total_accidents": len(accidents_data),
        "same_route_count": sum(1 for a in accidents_data if a["same_route"]),
        "accidents": accidents_data,
    }


@router.get("/routes/{route_id}/risk-breakdown")
async def get_risk_breakdown(
    route_id: int,
    target_date: date = Query(..., description="Date for risk calculation (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed breakdown of risk score factors.

    Shows what contributed to the risk score calculation with actual algorithm data.
    """
    # Fetch route
    route_query = select(Route).where(Route.route_id == route_id)
    route_result = await db.execute(route_query)
    route = route_result.scalar_one_or_none()

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    if route.latitude is None or route.longitude is None:
        raise HTTPException(status_code=400, detail="Route missing GPS coordinates")

    # Calculate safety score with full details
    normalized_type = normalize_route_type(route.type)
    prediction_request = PredictionRequest(
        latitude=route.latitude,
        longitude=route.longitude,
        route_type=normalized_type,
        planned_date=target_date,
        elevation_meters=None,
    )

    prediction = await predict_route_safety(prediction_request, db)

    # Extract actual factor contributions from top contributing accidents
    # Each accident's total_influence shows its impact on the final risk score
    total_influence = sum(acc.total_influence for acc in prediction.top_contributing_accidents[:10])

    # Analyze weight distributions from top accidents
    spatial_weights = [acc.spatial_weight for acc in prediction.top_contributing_accidents[:10]]
    temporal_weights = [acc.temporal_weight for acc in prediction.top_contributing_accidents[:10]]
    weather_weights = [acc.weather_weight for acc in prediction.top_contributing_accidents[:10]]
    route_type_weights = [acc.route_type_weight for acc in prediction.top_contributing_accidents[:10]]
    elevation_weights = [acc.elevation_weight for acc in prediction.top_contributing_accidents[:10]]

    # Calculate average weights (normalized to percentages)
    avg_spatial = (sum(spatial_weights) / len(spatial_weights) * 100) if spatial_weights else 0
    avg_temporal = (sum(temporal_weights) / len(temporal_weights) * 100) if temporal_weights else 0
    avg_weather = (sum(weather_weights) / len(weather_weights) * 100) if weather_weights else 0
    avg_route_type = (sum(route_type_weights) / len(route_type_weights) * 100) if route_type_weights else 0
    avg_elevation = (sum(elevation_weights) / len(elevation_weights) * 100) if elevation_weights else 0

    # Normalize to 100%
    total_weight = avg_spatial + avg_temporal + avg_weather + avg_route_type + avg_elevation
    if total_weight > 0:
        avg_spatial = (avg_spatial / total_weight) * 100
        avg_temporal = (avg_temporal / total_weight) * 100
        avg_weather = (avg_weather / total_weight) * 100
        avg_route_type = (avg_route_type / total_weight) * 100
        avg_elevation = (avg_elevation / total_weight) * 100

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
                          f"weighted by distance using Gaussian decay"
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
            "description": f"Pattern matching between forecast and accident conditions"
        })

        factors.append({
            "name": "Route Type Match",
            "contribution": round(avg_route_type, 1),
            "description": f"Climbing discipline similarity ({route.type} routes). "
                          f"Asymmetric weighting accounts for shared risk factors"
        })

        factors.append({
            "name": "Elevation Similarity",
            "contribution": round(avg_elevation, 1),
            "description": "Elevation similarity with nearby accidents. "
                          f"Similar elevations share weather and condition patterns"
        })
    else:
        factors.append({
            "name": "No Data",
            "contribution": 0,
            "description": "Insufficient accident data for risk calculation"
        })

    return {
        "route_id": route_id,
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


@router.get("/routes/{route_id}/seasonal-patterns")
async def get_seasonal_patterns(
    route_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get seasonal accident patterns for the route's mountain.

    Shows accident frequency and average risk by month.
    """
    # Fetch route to get mountain_id
    route_query = select(Route).where(Route.route_id == route_id)
    route_result = await db.execute(route_query)
    route = route_result.scalar_one_or_none()

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    # Get monthly accident counts
    # Use mountain_id if available, otherwise use nearby location
    if route.mountain_id:
        monthly_query = text("""
            SELECT
                EXTRACT(MONTH FROM a.date) as month,
                COUNT(*) as accident_count,
                AVG(50.0) as avg_risk_score,
                AVG(CURRENT_DATE - a.date) as avg_days_ago
            FROM accidents a
            JOIN routes r ON a.route_id = r.route_id
            WHERE r.mountain_id = :mountain_id
              AND a.date IS NOT NULL
            GROUP BY EXTRACT(MONTH FROM a.date)
            ORDER BY month
        """)
        result = await db.execute(monthly_query, {"mountain_id": route.mountain_id})
    elif route.latitude and route.longitude:
        # Fallback: use accidents within ~50km radius
        monthly_query = text("""
            SELECT
                EXTRACT(MONTH FROM a.date) as month,
                COUNT(*) as accident_count,
                AVG(50.0) as avg_risk_score,
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
    else:
        # No location data available
        result = None

    monthly_data = result.fetchall() if result else []

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
                "avg_temp": None,  # TODO: Add weather data
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

    return {
        "route_id": route_id,
        "route_name": route.name,
        "mountain_id": route.mountain_id,
        "mountain_name": route.mountain_name or "Unknown",
        "monthly_patterns": monthly_patterns,
        "best_months": [{"name": m["month"], "avg_risk": m["avg_risk_score"], "accident_count": m["accident_count"]} for m in best_months],
        "worst_months": [{"name": m["month"], "avg_risk": m["avg_risk_score"], "accident_count": m["accident_count"]} for m in worst_months],
    }


@router.get("/routes/{route_id}/time-of-day")
async def get_time_of_day_analysis(
    route_id: int,
    target_date: date = Query(..., description="Date to analyze (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get hourly weather and risk score analysis for a specific day.

    Helps climbers identify the optimal time window for their ascent.
    Shows how conditions and risk vary throughout the day.
    """
    import requests

    # Fetch route
    route_query = select(Route).where(Route.route_id == route_id)
    route_result = await db.execute(route_query)
    route = route_result.scalar_one_or_none()

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    if route.latitude is None or route.longitude is None:
        raise HTTPException(status_code=400, detail="Route missing GPS coordinates")

    # Fetch hourly weather data from Open-Meteo
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

        response = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=10)
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

        # Calculate risk score for each hour
        # (Simplified - uses daily risk as baseline, adjusts for extreme hourly conditions)
        normalized_type = normalize_route_type(route.type)
        prediction_request = PredictionRequest(
            latitude=route.latitude,
            longitude=route.longitude,
            route_type=normalized_type,
            planned_date=target_date,
            elevation_meters=None,
        )

        # Get base daily risk
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

            # Calculate hourly risk adjustment (-10 to +20 points)
            risk_adjustment = 0.0

            # Temperature extremes
            if temp < -15:
                risk_adjustment += 15  # Extreme cold
            elif temp < -5:
                risk_adjustment += 8   # Very cold
            elif temp > 30:
                risk_adjustment += 5   # Heat

            # Precipitation
            if precip > 5:
                risk_adjustment += 20  # Heavy rain/snow
            elif precip > 1:
                risk_adjustment += 10  # Moderate precipitation
            elif precip > 0.2:
                risk_adjustment += 3   # Light precipitation

            # Wind
            if gust > 20:
                risk_adjustment += 15  # Dangerous gusts
            elif wind > 15:
                risk_adjustment += 10  # High winds
            elif wind > 10:
                risk_adjustment += 5   # Windy

            # Low visibility
            if visibility < 1000:
                risk_adjustment += 10  # Poor visibility
            elif visibility < 5000:
                risk_adjustment += 5   # Reduced visibility

            # Calculate hourly risk
            hourly_risk = min(max(base_risk + risk_adjustment, 0), 100)

            # Determine condition summary
            conditions = []
            if temp < -10:
                conditions.append("Very Cold")
            elif temp > 25:
                conditions.append("Hot")
            if precip > 1:
                conditions.append("Rain/Snow")
            if wind > 10:
                conditions.append("Windy")
            if visibility < 5000:
                conditions.append("Low Visibility")

            if not conditions:
                if hourly_risk < 30:
                    conditions.append("Good Conditions")
                elif hourly_risk < 50:
                    conditions.append("Moderate")
                else:
                    conditions.append("Cautious")

            # Determine if this is a climbing window (daylight hours, reasonable conditions)
            is_daylight = 6 <= hour <= 18
            is_climbable = hourly_risk < 70 and precip < 5 and wind < 20

            hourly_data.append({
                "hour": hour,
                "time": times[i],
                "risk_score": round(hourly_risk, 1),
                "temperature": round(temp, 1),
                "precipitation": round(precip, 2),
                "wind_speed": round(wind, 1),
                "wind_gusts": round(gust, 1) if gust else None,
                "cloud_cover": round(cloud, 0),
                "visibility": round(visibility, 0),
                "conditions_summary": ", ".join(conditions),
                "is_daylight": is_daylight,
                "is_climbable": is_climbable and is_daylight,
            })

        # Find best climbing windows (consecutive climbable hours)
        windows = []
        current_window = []

        for hour_data in hourly_data:
            if hour_data["is_climbable"]:
                current_window.append(hour_data)
            else:
                if len(current_window) >= 2:  # At least 2 hours
                    avg_risk = sum(h["risk_score"] for h in current_window) / len(current_window)
                    windows.append({
                        "start_hour": current_window[0]["hour"],
                        "end_hour": current_window[-1]["hour"],
                        "duration_hours": len(current_window),
                        "avg_risk": round(avg_risk, 1),
                        "conditions": current_window[len(current_window)//2]["conditions_summary"],
                    })
                current_window = []

        # Check last window
        if len(current_window) >= 2:
            avg_risk = sum(h["risk_score"] for h in current_window) / len(current_window)
            windows.append({
                "start_hour": current_window[0]["hour"],
                "end_hour": current_window[-1]["hour"],
                "duration_hours": len(current_window),
                "avg_risk": round(avg_risk, 1),
                "conditions": current_window[len(current_window)//2]["conditions_summary"],
            })

        # Sort windows by risk (best first)
        windows.sort(key=lambda w: w["avg_risk"])

        return {
            "route_id": route_id,
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


@router.get("/routes/{route_id}/historical-trends")
async def get_historical_trends(
    route_id: int,
    days: int = Query(30, ge=1, le=365, description="Number of days of history to return"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get historical risk score trends for this route.

    Requires historical_predictions table to be populated via backfill script.
    """
    # Fetch route
    route_query = select(Route).where(Route.route_id == route_id)
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
        result = await db.execute(historical_query, {"route_id": route_id, "days": days})
        historical_data = result.fetchall()

        if not historical_data:
            return {
                "route_id": route_id,
                "route_name": route.name,
                "historical_predictions": [],
                "summary": None,
                "trend": None,
                "message": "Historical data not yet available. Run backfill script to collect historical predictions."
            }

        # Format historical data
        predictions = [
            {
                "date": row[0].isoformat(),
                "risk_score": round(float(row[1]), 1),
                "color_code": row[2],
            }
            for row in historical_data
        ]

        # Calculate summary statistics
        risk_scores = [p["risk_score"] for p in predictions]
        summary = {
            "avg_risk": round(sum(risk_scores) / len(risk_scores), 1),
            "min_risk": round(min(risk_scores), 1),
            "max_risk": round(max(risk_scores), 1),
        }

        # Simple trend analysis
        if len(predictions) >= 7:
            recent_avg = sum(risk_scores[-7:]) / 7
            older_avg = sum(risk_scores[:7]) / 7

            if recent_avg > older_avg + 5:
                trend_direction = "increasing"
                trend_desc = "Risk has increased over the past week"
            elif recent_avg < older_avg - 5:
                trend_direction = "decreasing"
                trend_desc = "Risk has decreased over the past week"
            else:
                trend_direction = "stable"
                trend_desc = "Risk has remained relatively stable"

            trend = {
                "direction": trend_direction,
                "description": trend_desc,
            }
        else:
            trend = None

        return {
            "route_id": route_id,
            "route_name": route.name,
            "days_available": len(predictions),
            "historical_predictions": predictions,
            "summary": summary,
            "trend": trend,
        }

    except Exception as e:
        logger.error(f"Error fetching historical trends: {e}")
        return {
            "route_id": route_id,
            "route_name": route.name,
            "historical_predictions": [],
            "summary": None,
            "trend": None,
            "error": "Historical predictions table may not exist. Run backfill script first."
        }
