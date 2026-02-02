"""
Safety Prediction API Endpoint

POST /api/v1/predict - Calculate safety prediction for a planned climbing route
"""
import os
import logging
from datetime import date, timedelta
from typing import List, Optional, Dict, Tuple
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from geoalchemy2.functions import ST_DWithin, ST_MakePoint

from app.db.session import get_db
from app.models.accident import Accident
from app.models.weather import Weather
from app.schemas.prediction import (
    PredictionRequest,
    PredictionResponse,
    ContributingAccident,
)
from app.services.safety_algorithm import (
    calculate_safety_score,
    AccidentData,
)
from app.services.safety_algorithm_vectorized import calculate_safety_score_vectorized
from app.services.weather_similarity import WeatherPattern
from app.services.algorithm_config import MAX_SEARCH_RADIUS_KM, SPATIAL_BANDWIDTH
from app.services.route_type_mapper import infer_route_type_from_accident
from app.services.weather_service import (
    fetch_current_weather_pattern,
    fetch_weather_statistics,
)
from app.services.elevation_service import fetch_elevation
from app.utils.time_utils import get_season
from app.utils.geo_utils import haversine_distance

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/predict", response_model=PredictionResponse)
async def predict_route_safety(
    request: PredictionRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Calculate safety prediction for a planned climbing route.

    Uses historical accident data, weather patterns, and the SafeAscent algorithm
    to predict risk level and confidence.

    **Algorithm Components**:
    - Spatial weighting: Gaussian decay by distance (route-type-specific bandwidth)
    - Temporal weighting: Exponential decay + seasonal boost
    - Weather similarity: Pattern correlation (if weather data available)
    - Route type weighting: Asymmetric similarity matrix
    - Severity weighting: Subtle boosters (1.0× to 1.3×)

    **Returns**:
    - `risk_score`: 0-100 (higher = more dangerous)
    - `confidence`: 0-100 (higher = more confident in prediction)
    - `top_contributing_accidents`: Detailed breakdown of influential accidents
    - `confidence_breakdown`: Component scores for transparency

    **Note**: Current weather pattern is not yet integrated (MVP).
    Weather similarity uses neutral weight (0.5) until real-time API is added.
    """
    # Step 1: Query ALL accidents from database
    # NOTE: We fetch all accidents and let the Gaussian spatial weighting
    # naturally down-weight distant ones. This allows weather similarity
    # to override spatial distance, which is critical for:
    # - Remote areas with sparse local accident data
    # - Maximizing signal from small dataset (~10k accidents)
    # - Weather-pattern-based risk assessment across regions
    accidents = await fetch_all_accidents(db=db)

    # Store search radius in metadata for reference (no longer used for filtering)
    if request.search_radius_km is None:
        bandwidth = SPATIAL_BANDWIDTH.get(request.route_type, SPATIAL_BANDWIDTH["default"])
        search_radius_km = min(bandwidth * 4, MAX_SEARCH_RADIUS_KM)
    else:
        search_radius_km = min(request.search_radius_km, MAX_SEARCH_RADIUS_KM)

    # Step 1.5: Fetch route elevation (auto-detect if not provided)
    if request.elevation_meters is not None:
        route_elevation = request.elevation_meters
        logger.info(f"Using provided elevation: {route_elevation}m")
    else:
        # Auto-fetch elevation from coordinates
        route_elevation = fetch_elevation(request.latitude, request.longitude)
        if route_elevation is not None:
            logger.info(f"Auto-detected elevation: {route_elevation}m")
        else:
            logger.warning("Failed to fetch elevation, using None (neutral weight)")

    if not accidents:
        # No accidents found - return zero risk
        return PredictionResponse(
            risk_score=0.0,
            num_contributing_accidents=0,
            top_contributing_accidents=[],
            metadata={
                "message": f"No historical accidents found within {search_radius_km:.0f}km",
                "route_type": request.route_type,
                "search_date": request.planned_date.isoformat(),
                "search_radius_km": search_radius_km,
            },
        )

    # Step 1.6: Distance-based route type filtering
    # Within 50km: Accept all route types (local context, canary effect)
    # Beyond 50km: Only accept close matches (strict_threshold >= 0.85)
    LOCAL_RADIUS_KM = 50.0
    STRICT_ROUTE_TYPE_THRESHOLD = 0.85  # ice ↔ alpine (0.95), exact matches (1.0)

    filtered_accidents = []
    logger.info(f"Applying distance-based route type filtering ({len(accidents)} total accidents)")

    for accident in accidents:
        # Calculate distance
        distance = haversine_distance(
            request.latitude, request.longitude,
            accident.latitude, accident.longitude
        )

        # Infer accident route type
        from app.services.route_type_mapper import infer_route_type_from_accident
        accident_route_type = infer_route_type_from_accident(
            activity=accident.activity,
            accident_type=accident.accident_type,
            tags=accident.tags,
        )

        # Get route type weight
        from app.services.route_type_weighting import calculate_route_type_weight
        route_type_weight = calculate_route_type_weight(
            planning_route_type=request.route_type,
            accident_route_type=accident_route_type,
        )

        # Apply distance-based filtering
        if distance <= LOCAL_RADIUS_KM:
            # Local accident: accept all route types
            filtered_accidents.append(accident)
        elif route_type_weight >= STRICT_ROUTE_TYPE_THRESHOLD:
            # Distant accident: only accept close matches
            filtered_accidents.append(accident)
        # else: filtered out (distant + incompatible route type)

    logger.info(f"After route type filtering: {len(filtered_accidents)} accidents")
    accidents = filtered_accidents

    if not accidents:
        return PredictionResponse(
            risk_score=0.0,
            num_contributing_accidents=0,
            top_contributing_accidents=[],
            metadata={
                "message": f"No compatible accidents found after route type filtering",
                "route_type": request.route_type,
                "search_date": request.planned_date.isoformat(),
            },
        )

    # Step 2: Fetch weather data for each accident (7-day window)
    accident_weather_map = await fetch_accident_weather_patterns(db, accidents)

    # Step 3: Build AccidentData objects
    accident_data_list = []
    for accident in accidents:
        # Get weather pattern (or None if missing)
        weather_pattern = accident_weather_map.get(accident.accident_id)

        # Infer route type from accident fields (activity, accident_type, tags)
        inferred_route_type = infer_route_type_from_accident(
            activity=accident.activity,
            accident_type=accident.accident_type,
            tags=accident.tags,
        )

        accident_data = AccidentData(
            accident_id=accident.accident_id,
            latitude=accident.latitude,
            longitude=accident.longitude,
            elevation_meters=accident.elevation_meters,  # From database
            accident_date=accident.date,
            route_type=inferred_route_type,
            severity=accident.injury_severity or "unknown",
            weather_pattern=weather_pattern,
        )
        accident_data_list.append(accident_data)

    # Step 4: Fetch current weather pattern from Open-Meteo API
    current_weather = fetch_current_weather_pattern(
        latitude=request.latitude,
        longitude=request.longitude,
        target_date=request.planned_date,
    )

    # Log if weather fetch failed
    if current_weather is None:
        logger.warning(
            f"Failed to fetch current weather for {request.latitude}, {request.longitude}. "
            "Using neutral weather weight (0.5)."
        )

    # Step 5: Fetch historical weather statistics for extreme detection
    route_season = get_season(request.planned_date)

    # Use route_elevation for weather stats (default to 3000m if unavailable)
    weather_stats_elevation = route_elevation if route_elevation is not None else 3000.0

    historical_weather_stats = fetch_weather_statistics(
        latitude=request.latitude,
        longitude=request.longitude,
        elevation_meters=weather_stats_elevation,
        season=route_season,
    )

    # Step 6: Calculate safety prediction
    # Feature flag: Use vectorized algorithm if enabled (default: True)
    use_vectorized = os.getenv("USE_VECTORIZED_ALGORITHM", "true").lower() == "true"

    if use_vectorized:
        logger.info(f"Using VECTORIZED algorithm for {len(accident_data_list)} accidents")
        prediction = calculate_safety_score_vectorized(
            route_lat=request.latitude,
            route_lon=request.longitude,
            route_elevation_m=route_elevation,  # Auto-detected or provided
            route_type=request.route_type,
            current_date=request.planned_date,
            current_weather=current_weather,  # Real weather from API!
            accidents=accident_data_list,
            historical_weather_stats=historical_weather_stats,  # For extreme detection
        )
    else:
        logger.info(f"Using LOOP-BASED algorithm for {len(accident_data_list)} accidents")
        prediction = calculate_safety_score(
            route_lat=request.latitude,
            route_lon=request.longitude,
            route_elevation_m=route_elevation,  # Auto-detected or provided
            route_type=request.route_type,
            current_date=request.planned_date,
            current_weather=current_weather,  # Real weather from API!
            accidents=accident_data_list,
            historical_weather_stats=historical_weather_stats,  # For extreme detection
        )

    # Step 6: Convert to API response format
    # Convert top contributing accidents
    contributing_accidents = [
        ContributingAccident(
            accident_id=acc["accident_id"],
            total_influence=acc["total_influence"],
            distance_km=acc["distance_km"],
            days_ago=acc["days_ago"],
            spatial_weight=acc["spatial_weight"],
            temporal_weight=acc["temporal_weight"],
            elevation_weight=acc["elevation_weight"],
            weather_weight=acc["weather_weight"],
            route_type_weight=acc["route_type_weight"],
            severity_weight=acc["severity_weight"],
        )
        for acc in prediction.top_contributing_accidents
    ]

    # Add search radius to metadata
    metadata = prediction.metadata.copy()
    metadata["search_radius_km"] = search_radius_km

    response = PredictionResponse(
        risk_score=prediction.risk_score,
        num_contributing_accidents=prediction.num_contributing_accidents,
        top_contributing_accidents=contributing_accidents,
        metadata=metadata,
    )

    return response


async def fetch_all_accidents(db: AsyncSession) -> List[Accident]:
    """
    Fetch ALL accidents from database with required fields for prediction.

    No spatial filtering - lets Gaussian spatial weighting naturally down-weight
    distant accidents. This maximizes weather similarity signal across regions.

    Filters to only include accidents with valid coordinates and dates needed
    for spatial/temporal calculations.

    Performance: ~50-100ms for 10,000 accidents (acceptable for MVP)
    Note: Result caching at prediction level (Phase 8.6) will eliminate this entirely

    Args:
        db: Database session

    Returns:
        List of all valid Accident objects
    """
    logger.info("Fetching all accidents from database (no spatial filter)")

    # Query: Find all accidents with required fields for prediction
    stmt = select(Accident).where(
        and_(
            Accident.coordinates.isnot(None),
            Accident.date.isnot(None),
            Accident.latitude.isnot(None),
            Accident.longitude.isnot(None),
        )
    )

    result = await db.execute(stmt)
    accidents = result.scalars().all()

    logger.info(f"Loaded {len(accidents)} valid accidents")

    return list(accidents)


async def fetch_nearby_accidents(
    db: AsyncSession,
    latitude: float,
    longitude: float,
    radius_km: float,
) -> List[Accident]:
    """
    Fetch accidents within radius of a point using PostGIS spatial query.

    NOTE: This function is deprecated in favor of fetch_all_accidents() which
    allows weather similarity to override spatial distance. Kept for backward
    compatibility and testing.

    Args:
        db: Database session
        latitude: Center point latitude
        longitude: Center point longitude
        radius_km: Search radius in kilometers

    Returns:
        List of Accident objects within radius
    """
    # Create a point geometry
    point = ST_MakePoint(longitude, latitude)

    # PostGIS ST_DWithin query (radius in meters)
    radius_meters = radius_km * 1000

    # Query: Find accidents within radius that have coordinates and dates
    stmt = select(Accident).where(
        and_(
            Accident.coordinates.isnot(None),
            Accident.date.isnot(None),
            Accident.latitude.isnot(None),
            Accident.longitude.isnot(None),
            ST_DWithin(
                Accident.coordinates,
                func.ST_SetSRID(point, 4326),
                radius_meters,
            ),
        )
    )

    result = await db.execute(stmt)
    accidents = result.scalars().all()

    return list(accidents)


async def fetch_accident_weather_patterns(
    db: AsyncSession,
    accidents: List[Accident],
) -> Dict[int, Optional[WeatherPattern]]:
    """
    Fetch 7-day weather patterns for a list of accidents using BULK query.

    OPTIMIZATION: Instead of N+1 queries (one per accident), we use a single
    bulk query with a JOIN to fetch all weather data at once, then group by
    accident_id in Python. This is 4-5× faster for large result sets.

    For each accident, fetches weather data for days -6 to 0 (7 days total)
    relative to the accident date.

    Args:
        db: Database session
        accidents: List of accidents to fetch weather for

    Returns:
        Dictionary mapping accident_id to WeatherPattern (or None if insufficient data)

    Performance:
        - Old approach: 476 queries × 0.0002s = 0.10s
        - New approach: 1 query = 0.024s (4.2× faster)
    """
    weather_map = {}

    # Handle empty list
    if not accidents:
        return weather_map

    # Filter out accidents without dates and build accident_id list
    valid_accidents = [acc for acc in accidents if acc.date is not None]
    accident_ids = [acc.accident_id for acc in valid_accidents]

    # Initialize weather_map for all accidents (including those without dates)
    for accident in accidents:
        if accident.date is None:
            weather_map[accident.accident_id] = None

    # If no valid accidents, return early
    if not accident_ids:
        return weather_map

    # BULK QUERY: Fetch ALL weather data in ONE query using JOIN
    # This joins weather table with accidents to get accident dates,
    # then filters for 7-day window around each accident
    stmt = (
        select(Weather, Accident.date.label('accident_date'))
        .join(Accident, Weather.accident_id == Accident.accident_id)
        .where(
            and_(
                Weather.accident_id.in_(accident_ids),
                # Weather date must be within 7-day window before accident
                Weather.date >= Accident.date - timedelta(days=6),
                Weather.date <= Accident.date,
            )
        )
        .order_by(Weather.accident_id, Weather.date)
    )

    result = await db.execute(stmt)
    rows = result.all()

    # Group weather records by accident_id in Python
    # This is fast (O(n)) compared to 476 database queries
    from collections import defaultdict
    weather_by_accident = defaultdict(list)

    for weather_record, accident_date in rows:
        weather_by_accident[weather_record.accident_id].append(weather_record)

    # Build WeatherPattern for each accident with sufficient data
    for accident in valid_accidents:
        weather_records = weather_by_accident.get(accident.accident_id, [])

        # Build WeatherPattern if we have enough data (at least 5 of 7 days)
        if len(weather_records) >= 5:
            weather_pattern = build_weather_pattern(weather_records)
            weather_map[accident.accident_id] = weather_pattern
        else:
            # Insufficient weather data
            weather_map[accident.accident_id] = None

    return weather_map


def build_weather_pattern(weather_records: List[Weather]) -> WeatherPattern:
    """
    Build a WeatherPattern object from database weather records.

    Args:
        weather_records: List of Weather objects (should be 5-7 days)

    Returns:
        WeatherPattern object for algorithm
    """
    # Extract data from records (in chronological order)
    temperature = []
    precipitation = []
    wind_speed = []
    visibility = []
    cloud_cover = []
    daily_temps = []

    for record in weather_records:
        # Use available data, default to reasonable values if missing
        temperature.append(record.temperature_avg or 10.0)  # Default 10°C
        precipitation.append(record.precipitation_total or 0.0)  # Default 0mm
        wind_speed.append(record.wind_speed_avg or 5.0)  # Default 5 m/s
        visibility.append(record.visibility_avg or 10000.0)  # Default 10km
        cloud_cover.append(record.cloud_cover_avg or 50.0)  # Default 50%

        # Daily temps tuple for freeze-thaw calculation
        temp_min = record.temperature_min or record.temperature_avg or 10.0
        temp_avg = record.temperature_avg or 10.0
        temp_max = record.temperature_max or record.temperature_avg or 10.0
        daily_temps.append((temp_min, temp_avg, temp_max))

    return WeatherPattern(
        temperature=temperature,
        precipitation=precipitation,
        wind_speed=wind_speed,
        visibility=visibility,
        cloud_cover=cloud_cover,
        daily_temps=daily_temps,
    )
