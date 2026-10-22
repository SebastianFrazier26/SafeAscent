"""
MP Routes API endpoints.
Uses mp_routes table for Mountain Project climbing routes.
Includes all analytics and safety endpoints.
"""
from typing import Optional
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, or_, and_, not_
import logging
import calendar
import math

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
from app.ml.bundle import ModelBundle
from app.ml.dataset import STATIC_FEATURES
from app.ml.explain import explain
from app.ml.registry import BundleCorrupt, BundleMissing, load_bundle
from app.services import model_scores
from app.services.weather_service import (
    WEATHER_API_URL,
    OPEN_METEO_API_KEY,
    fetch_current_weather_pattern,
    fetch_weather_statistics,
)
from app.utils.time_utils import get_season
from app.services.risk_bands import (
    INSUFFICIENT_DATA_MESSAGE,
    color_code_for,
)

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
    return color_code_for(risk_score)


SCORE_UNAVAILABLE = "score unavailable"
HOURLY_RISK_NOTE = "Hour-by-hour risk arrives with the conditions rules (v2.1)."
ACCIDENTS_CONTEXT_NOTE = "Nearby incidents are context, not cause."
# A month with no stored row: unavailable, so no number and no colour (gray means "no evidence").
_MISSING_SCORE = {"risk_score": None, "color_code": None, "data_status": None}

# The route's point is its catalog area's (owner decision 2026-10-12), never mp_routes lat/lon.
CATALOG_POINT_SQL = text("""
    SELECT a.lat, a.lon, l.name AS location_name
    FROM canonical_routes r
    JOIN canonical_areas a ON a.area_id = r.area_id
    LEFT JOIN mp_locations l ON l.mp_id = a.area_id
    WHERE r.route_id = :route_id
""")
# Only day/month-precision rows have a month. A year-precision row (or a year span) is an
# interval [year_lo, year_hi], so its synthetic date's month is never read; it counts apart.
SEASONAL_COUNTS_SQL = text("""
    SELECT CASE WHEN a.date_precision IN ('day', 'month')
                     AND NOT (a.year_lo IS NOT NULL AND a.year_hi IS NOT NULL AND a.year_lo < a.year_hi)
                THEN EXTRACT(MONTH FROM a.date)::int END AS month,
           count(*) AS n
    FROM accidents_clean a
    WHERE a.latitude IS NOT NULL
      AND a.longitude IS NOT NULL
      AND (a.date IS NULL OR a.date <= :today)
      AND ((a.date_precision IN ('day', 'month') AND a.date IS NOT NULL) OR a.date_precision = 'year')
      AND 6371 * acos(LEAST(1.0, GREATEST(-1.0,
            cos(radians(:lat)) * cos(radians(a.latitude)) * cos(radians(a.longitude) - radians(:lon))
            + sin(radians(:lat)) * sin(radians(a.latitude))))) < 50
    GROUP BY 1
""")
# The cell's static features exactly as training read them (app.ml.dataset.STATIC_SQL): the
# area's minimum over its scorable routes, normals only when every route's normals are usable.
# Anything else would leave the difference in the evidence term.
AREA_STATIC_SQL = text("""
    SELECT min(u.elevation_m), min(u.lat), min(u.tmax_mean), min(u.tmin_mean), min(u.precip_mm),
           min(u.snowfall_cm), min(u.freeze_thaw_days),
           bool_and(u.normals_status IN ('complete', 'insufficient'))
    FROM scorable_routes s
    LEFT JOIN scoring_unit_features u ON u.unit_kind = 'route' AND u.unit_id = s.route_id AND u.month = :month
    WHERE s.area_id = (SELECT area_id FROM model.route_scores
                       WHERE model_version = :version AND route_id = :route_id AND month = :month)
""")

_bundle_cache: dict[str, ModelBundle] = {}


async def _bundle_for(db: AsyncSession, version: str) -> ModelBundle:
    # A version's bundle never changes (its sha is fixed at insert), so one cached copy per
    # process is enough; a promotion loads the new version once.
    bundle = _bundle_cache.get(version)
    if bundle is None:
        bundle = await load_bundle(db, version)
        _bundle_cache.clear()
        _bundle_cache[version] = bundle
    return bundle


def _num(v) -> Optional[float]:
    return None if v is None else float(v)


async def _explain_terms(db: AsyncSession, stored: model_scores.StoredScore) -> list[dict]:
    bundle = await _bundle_for(db, stored.model_version)
    row = (await db.execute(AREA_STATIC_SQL, {"version": stored.model_version, "route_id": stored.route_id,
                                              "month": stored.month})).one_or_none()
    raw: dict[str, Optional[float]] = {f: None for f in STATIC_FEATURES}
    if row is not None:
        normals_ok = bool(row[7])
        vals = [_num(row[0]), _num(row[1])] + [_num(v) if normals_ok else None for v in row[2:7]]
        raw = dict(zip(STATIC_FEATURES, vals))
    assert stored.rr_mean is not None and stored.n_eff_geo is not None and stored.n_eff_feature is not None
    terms = explain(bundle, type_group=stored.type_group, month=stored.month, raw_features=raw,
                    rr_mean=stored.rr_mean, n_eff_geo=stored.n_eff_geo, n_eff_feature=stored.n_eff_feature, top=3)
    return [{"name": t.name, "label": t.label, "log_effect": round(t.log_effect, 4),
             "multiplier": round(math.exp(t.log_effect), 2)} for t in terms]


def _validated_score_fields(stored: model_scores.StoredScore) -> dict:
    """score_fields through the SafetyScore validator, so every surface serves only a shape the
    contract allows (range brackets the score, ok-only fields only when ok)."""
    return SafetyScore(**model_scores.score_fields(stored)).model_dump(exclude={"status"})


def _stored_safety_response(route_id: int, route_name: str, date_str: str,
                            stored: model_scores.StoredScore) -> MpRouteSafetyResponse:
    return MpRouteSafetyResponse(route_id=route_id, route_name=route_name, target_date=date_str,
                                 **model_scores.score_fields(stored))


def _day_score_fields(stored: Optional[model_scores.StoredScore]) -> dict:
    if stored is None:
        return dict(_MISSING_SCORE)
    f = _validated_score_fields(stored)
    return {k: f[k] for k in ("risk_score", "color_code", "data_status", "confidence", "score_range")}


async def get_route_with_location_coords(db: AsyncSession, mp_route_id: int):
    """
    Fetch a route with coordinates inherited from its parent location.

    Routes don't store coordinates directly - they inherit them from their
    parent location in the MP hierarchy.

    Args:
        db: Database session
        mp_route_id: Mountain Project route ID

    Returns:
        Row with route fields + location latitude/longitude (None when the route has no
        location row or the location has no coordinates), or None if the route is missing
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
        # Outer join: a route with no location row still exists; callers that need
        # coordinates check for None themselves.
        .outerjoin(MpLocation, MpRoute.location_id == MpLocation.mp_id)
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
        .where(not_(func.lower(MpRoute.name).in_([
            "itchy bitchy",
            "rompin' with the rattlers",
        ])))
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

    # Exclude known bad-coordinate routes
    query = query.where(
        not_(func.lower(MpRoute.name).in_([
            "itchy bitchy",
            "rompin' with the rattlers",
        ]))
    )

    result = await db.execute(query)
    rows = result.fetchall()

    route_ids = [row.mp_route_id for row in rows]
    version = await model_scores.active_version(db)
    # One stored row per route for the month (CM = 1 in MVP-1). A route without a row is
    # missing (safety=None), never 0 and never gray.
    scores = await model_scores.get_scores(db, route_ids, target_date.month) if version else {}

    routes_with_safety = []
    stored_count = 0
    missing_count = 0
    insufficient_count = 0

    for row in rows:
        stored = scores.get(row.mp_route_id)
        if stored is not None:
            safety = SafetyScore(**model_scores.score_fields(stored), status="stored")
            stored_count += 1
            if safety.data_status == "insufficient_data":
                insufficient_count += 1
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

    meta = MpRouteMapWithSafetyMeta(
        total_routes=len(routes_with_safety),
        cached_routes=stored_count,
        computed_routes=0,
        missing_routes=missing_count,
        insufficient_routes=insufficient_count,
        target_date=date_str,
        season=season or "rock",
        model_version=version,
    )

    logger.info(
        f"Bulk safety endpoint: {len(routes_with_safety)} routes, "
        f"{stored_count} stored, {missing_count} missing for {date_str} (model {version})"
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
    target_date: date = Query(..., description="Target date (YYYY-MM-DD); its month selects the stored score"),
    bypass_cache: bool = Query(
        default=False,
        description="Accepted for older clients and ignored: scores are stored per model version (A:137).",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    The active model's stored score for this route and the month of target_date, with its
    range, confidence and evidence. 404 "score unavailable" when there is no active model or
    no stored row for the route: unavailable is not the same as insufficient (gray).
    """
    stored = await model_scores.get_score(db, mp_route_id, target_date.month)
    if stored is None:
        raise HTTPException(status_code=404, detail=SCORE_UNAVAILABLE)
    name = (await db.execute(select(MpRoute.name).where(MpRoute.mp_route_id == mp_route_id))).scalar_one_or_none()
    if name is None:
        raise HTTPException(status_code=404, detail="Route not found")
    return _stored_safety_response(mp_route_id, name, target_date.isoformat(), stored)


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

    forecast_days = []
    days = [start_date + timedelta(days=i) for i in range(7)]
    # Each day carries its month's stored score (CM = 1 in MVP-1): no per-day model call.
    version = await model_scores.active_version(db)
    month_scores = {m: await model_scores.get_score(db, mp_route_id, m) for m in sorted({d.month for d in days})}

    for target_date in days:
        stored = month_scores[target_date.month]
        try:
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
                **_day_score_fields(stored),
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
                "error": "Forecast unavailable for this date"
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
        "model_version": version,
    }


@router.get("/mp-routes/{mp_route_id}/accidents")
async def get_route_accidents(
    mp_route_id: int,
    limit: int = Query(50, ge=1, le=100, description="Maximum accidents to return"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get accident reports for this route and its surroundings.

    Rows linked to this route (accidents.mp_route_id) come first, whatever their distance
    and even without coordinates; then unlinked accidents within ~50 km of the route's
    parent-location coordinates, nearest first. Future-dated rows are excluded.

    - distance_km / impact_score: null when either the accident or the route has no
      coordinates (never a guessed distance)
    - same_route: True only when accidents.mp_route_id is this route
    - weather: historical conditions on the accident date, when available
    - nearby_search: False when the route has no coordinates, so only linked rows could
      be listed; an empty list then means "not searched", not "no accidents nearby"
    - total_accidents: all matching rows, not capped by limit
    """
    import requests
    import math

    # Fetch route with location coordinates
    route = await get_route_with_location_coords(db, mp_route_id)

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    has_coords = route.latitude is not None and route.longitude is not None

    # Fetch nearby accidents using geographic proximity with distance calculation
    # Using Haversine formula - also returns calculated distance for relevance scoring
    # Include ALL available fields for comprehensive accident reports
    accidents_query = text("""
        SELECT
            accident_id, date, latitude, longitude, description,
            accident_type, injury_severity, location, route as route_name,
            source, state, mountain, activity, age_range, tags, elevation_meters,
            mp_route_id,
            distance_km,
            COUNT(*) OVER () AS total_count
        FROM (
            SELECT
                *,
                -- The CASE is required: LEAST/GREATEST skip NULLs, so without it a missing
                -- coordinate (the accident's or the route's) becomes acos(-1), about
                -- 20,015 km. The CASTs let asyncpg type the parameters when they are NULL.
                -- The clamp keeps acos in its domain when rounding pushes the cosine just
                -- past 1 for coincident points.
                CASE WHEN latitude IS NULL OR longitude IS NULL
                          OR CAST(:lat AS double precision) IS NULL
                          OR CAST(:lon AS double precision) IS NULL THEN NULL
                ELSE 6371 * acos(LEAST(1.0, GREATEST(-1.0,
                    cos(radians(:lat)) * cos(radians(latitude)) *
                    cos(radians(longitude) - radians(:lon)) +
                    sin(radians(:lat)) * sin(radians(latitude))
                ))) END as distance_km
            FROM accidents
            WHERE (date IS NULL OR date <= :today)
        ) a
        -- An FK-linked accident belongs to this route even without coordinates; only
        -- unlinked nearby accidents need a distance.
        WHERE mp_route_id = :mp_route_id OR distance_km < 50
        ORDER BY (mp_route_id = :mp_route_id) IS TRUE DESC,
                 distance_km ASC NULLS LAST,
                 date DESC NULLS LAST
        LIMIT :limit
    """)

    accidents_result = await db.execute(
        accidents_query,
        {
            "lat": route.latitude if has_coords else None,
            "lon": route.longitude if has_coords else None,
            "limit": limit,
            "today": datetime.now(timezone.utc).date(),
            "mp_route_id": mp_route_id,
        }
    )
    accidents = accidents_result.fetchall()
    total_accidents = int(accidents[0].total_count) if accidents else 0

    # Format accident data with weather and relevance scoring
    accidents_data = []
    for accident in accidents:
        (accident_id, acc_date, acc_lat, acc_lon, description, acc_type, severity,
         location, route_name, source, state, mountain, activity, age_range,
         tags, elevation_m, acc_mp_route_id, distance_km, _total) = accident

        # Calculate impact score based on proximity (closer = higher score)
        # Using exponential decay: 100 * e^(-distance/10)
        # At 0km = 100, at 10km ≈ 37, at 20km ≈ 14, at 50km ≈ 0.7
        impact_score = (
            round(100 * math.exp(-distance_km / 10), 1) if distance_km is not None else None
        )

        # FK equality only: name matching labelled nearby routes with similar names
        # (e.g. "X" vs "X Direct") as this route.
        same_route = acc_mp_route_id is not None and int(acc_mp_route_id) == mp_route_id

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
            "distance_km": round(distance_km, 1) if distance_km is not None else None,
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
            "coordinates": {"lat": acc_lat, "lon": acc_lon} if acc_lat is not None and acc_lon is not None else None,
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
        "total_accidents": total_accidents,
        "nearby_search": has_coords,
        "accidents": accidents_data,
        "context_note": ACCIDENTS_CONTEXT_NOTE,
    }


@router.get("/mp-routes/{mp_route_id}/risk-breakdown")
async def get_risk_breakdown(
    mp_route_id: int,
    target_date: date = Query(..., description="Date (YYYY-MM-DD); its month selects the stored score"),
    db: AsyncSession = Depends(get_db),
):
    """
    What drives the stored score: the top terms of the model's decomposition of the route's
    relative rate (each a multiplier against the typical route of its type and month), plus the
    evidence term. Nearby incidents are context, not cause, and stay in /accidents.
    """
    stored = await model_scores.get_score(db, mp_route_id, target_date.month)
    if stored is None:
        raise HTTPException(status_code=404, detail=SCORE_UNAVAILABLE)
    name = (await db.execute(select(MpRoute.name).where(MpRoute.mp_route_id == mp_route_id))).scalar_one_or_none()
    if name is None:
        raise HTTPException(status_code=404, detail="Route not found")
    body = _stored_safety_response(mp_route_id, name, target_date.isoformat(), stored).model_dump()

    if body["data_status"] == "insufficient_data":
        # No number, so nothing to decompose: an empty term list, never zero-effect terms.
        return {**body, "message": INSUFFICIENT_DATA_MESSAGE, "terms": []}

    try:
        terms = await _explain_terms(db, stored)
    except (ValueError, BundleMissing, BundleCorrupt) as e:
        logger.error(f"risk breakdown unavailable for route {mp_route_id} ({stored.model_version}): "
                     f"{type(e).__name__}")
        return {**body, "terms": None, "message": "Breakdown unavailable for this model version."}
    return {**body, "terms": terms}


@router.get("/mp-routes/{mp_route_id}/seasonal-patterns")
async def get_seasonal_patterns(
    mp_route_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Reported-incident counts by month within ~50 km of the route's catalog point (its area's
    point, never mp_routes lat/lon). Counts only: no risk number here. A row known only to a
    year (or a span of years) has no month, so it is reported as year_known_only, never placed
    at its synthetic date.
    """
    route_name = (await db.execute(select(MpRoute.name).where(MpRoute.mp_route_id == mp_route_id))).scalar_one_or_none()
    if route_name is None:
        raise HTTPException(status_code=404, detail="Route not found")
    point = (await db.execute(CATALOG_POINT_SQL, {"route_id": mp_route_id})).one_or_none()
    if point is None or point.lat is None or point.lon is None:
        raise HTTPException(status_code=400, detail="Route has no catalog point")

    rows = (await db.execute(
        SEASONAL_COUNTS_SQL,
        {"lat": point.lat, "lon": point.lon, "today": datetime.now(timezone.utc).date()},
    )).fetchall()
    by_month = {int(r.month): int(r.n) for r in rows if r.month is not None}
    year_known_only = sum(int(r.n) for r in rows if r.month is None)

    monthly_patterns = [
        {"month": calendar.month_abbr[m], "month_num": m, "accident_count": by_month.get(m, 0)}
        for m in range(1, 13)
    ]
    # No "best months": fewest recorded accidents is not evidence of a safe month.
    worst_months = sorted((m for m in monthly_patterns if m["accident_count"] > 0),
                          key=lambda x: x["accident_count"], reverse=True)[:3]

    return {
        "route_id": mp_route_id,
        "route_name": route_name,
        "location_name": point.location_name or "Unknown Area",
        "monthly_patterns": monthly_patterns,
        "year_known_only": year_known_only,
        "worst_months": [{"name": m["month"], "accident_count": m["accident_count"]} for m in worst_months],
        "context_note": ACCIDENTS_CONTEXT_NOTE,
    }


@router.get("/mp-routes/{mp_route_id}/time-of-day")
async def get_time_of_day_analysis(
    mp_route_id: int,
    target_date: date = Query(..., description="Date to analyze (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Hourly weather for one day at the route. Hour-by-hour risk is not modelled yet (DP24): each
    hour's risk is null with a risk_note, never a number derived from the daily score.
    Coordinates are inherited from the route's parent location.
    """
    import requests

    route = await get_route_with_location_coords(db, mp_route_id)

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    if route.latitude is None or route.longitude is None:
        raise HTTPException(status_code=400, detail="Route's location missing GPS coordinates")

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

        if OPEN_METEO_API_KEY:
            params["apikey"] = OPEN_METEO_API_KEY

        response = requests.get(WEATHER_API_URL, params=params, timeout=10)
        response.raise_for_status()
        hourly = response.json()["hourly"]
    except Exception as e:
        logger.error(f"Error fetching hourly weather: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Failed to fetch hourly weather data")

    hourly_data = []
    for i, time_str in enumerate(hourly["time"]):
        hour = int(time_str.split("T")[1][:2])
        temp = hourly["temperature_2m"][i]
        precip = hourly["precipitation"][i]
        wind = hourly["wind_speed_10m"][i]
        gust = hourly["wind_gusts_10m"][i]
        cloud = hourly["cloud_cover"][i]
        visibility = hourly["visibility"][i]

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

        hourly_data.append({
            "hour": hour,
            "time": time_str,
            # risk_score stays for older clients; both are null until hourly risk is modelled.
            "risk_score": None,
            "risk": None,
            "risk_note": HOURLY_RISK_NOTE,
            "temperature": round(temp, 1) if temp is not None else None,
            "precipitation": round(precip, 2) if precip is not None else None,
            "wind_speed": round(wind, 1) if wind is not None else None,
            "wind_gusts": round(gust, 1) if gust is not None else None,
            "cloud_cover": round(cloud, 0) if cloud is not None else None,
            "visibility": round(visibility, 0) if visibility is not None else None,
            "conditions_summary": ", ".join(conditions) if conditions else "No notable weather",
            "is_daylight": 6 <= hour <= 18,
            # Climbability needs a risk number; without one it is unknown, never True.
            "is_climbable": None,
        })

    return {
        "route_id": mp_route_id,
        "route_name": route.name,
        "target_date": target_date.isoformat(),
        "base_daily_risk": None,
        "risk_note": HOURLY_RISK_NOTE,
        "hourly_data": hourly_data,
        "climbing_windows": [],
        "best_window": None,
        "recommendation": HOURLY_RISK_NOTE,
    }


@router.get("/mp-routes/{mp_route_id}/historical-trends")
async def get_historical_trends(
    mp_route_id: int,
    days: int = Query(30, ge=1, le=365, description="Accepted for older clients and ignored"),
    target_date: Optional[date] = Query(None, description="Reference date for weather volatility (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    The active model's 12-month profile for this route (score, range and confidence per
    month) plus monthly weather volatility. Replaces the kernel's daily history (DP24); the
    old series fields stay, empty, for older clients.
    """
    route = await get_route_with_location_coords(db, mp_route_id)

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    reference_date = target_date or date.today()
    weather_volatility = await _weather_volatility(route, mp_route_id, reference_date)

    version, rows = await model_scores.get_profile(db, mp_route_id)
    profile = None
    if version is not None and rows:
        profile = [
            {"month": m, "month_label": calendar.month_abbr[m],
             **(_validated_score_fields(rows[m]) if m in rows else _MISSING_SCORE)}
            for m in range(1, 13)
        ]

    return {
        "route_id": mp_route_id,
        "route_name": route.name,
        "model_version": version,
        "monthly_profile": profile,
        **({} if profile is not None else {"message": SCORE_UNAVAILABLE}),
        "days_available": 0,
        "historical_predictions": [],
        "summary": None,
        "trend": None,
        "reference_date": reference_date.isoformat(),
        "weather_volatility": weather_volatility,
    }


async def _weather_volatility(route, mp_route_id: int, reference_date: date) -> dict:
    weather_volatility = {
        "available": False,
        "reference_date": reference_date.isoformat(),
        "message": "Historical weather data unavailable right now.",
    }
    if route.latitude is None or route.longitude is None:
        return weather_volatility
    try:
        season = get_season(reference_date)
        weather_stats = await fetch_weather_statistics(
            latitude=route.latitude,
            longitude=route.longitude,
            elevation_meters=3000.0,
            season=season,
            reference_date=reference_date,
        )

        if weather_stats and weather_stats.get("monthly_volatility"):
            current_weather = fetch_current_weather_pattern(
                latitude=route.latitude,
                longitude=route.longitude,
                target_date=reference_date,
            )
            current_values = {
                "temperature": round(float(current_weather.temperature[-1]), 1)
                if current_weather and current_weather.temperature else None,
                "precipitation": round(float(current_weather.precipitation[-1]), 2)
                if current_weather and current_weather.precipitation else None,
                "wind_speed": round(float(current_weather.wind_speed[-1]), 2)
                if current_weather and current_weather.wind_speed else None,
            }

            months = []
            for month in range(1, 13):
                temp = weather_stats["monthly_volatility"]["temperature"].get(str(month), {})
                precip = weather_stats["monthly_volatility"]["precipitation"].get(str(month), {})
                wind = weather_stats["monthly_volatility"]["wind_speed"].get(str(month), {})
                months.append({
                    "month": month,
                    "month_label": calendar.month_abbr[month],
                    "temperature_mean": round(float(temp["mean"]), 2) if temp.get("mean") is not None else None,
                    "temperature_std": round(float(temp["std_dev"]), 2) if temp.get("std_dev") is not None else None,
                    "temperature_sample_count": temp.get("sample_count"),
                    "precipitation_mean": round(float(precip["mean"]), 2) if precip.get("mean") is not None else None,
                    "precipitation_std": round(float(precip["std_dev"]), 2) if precip.get("std_dev") is not None else None,
                    "precipitation_sample_count": precip.get("sample_count"),
                    "wind_speed_mean": round(float(wind["mean"]), 2) if wind.get("mean") is not None else None,
                    "wind_speed_std": round(float(wind["std_dev"]), 2) if wind.get("std_dev") is not None else None,
                    "wind_speed_sample_count": wind.get("sample_count"),
                })

            weather_volatility = {
                "available": True,
                "reference_date": reference_date.isoformat(),
                "source": weather_stats.get("source"),
                "lookback_years": weather_stats.get("lookback_years"),
                "temporal_decay": weather_stats.get("temporal_decay"),
                "current_values": current_values,
                "months": months,
            }
    except Exception as weather_err:
        logger.warning(f"Weather volatility data unavailable for route {mp_route_id}: {type(weather_err).__name__}")
    return weather_volatility


def _year_span(row) -> Optional[dict]:
    first, last = row
    return None if first is None or last is None else {"first": int(first), "last": int(last)}


@router.get("/mp-routes/{mp_route_id}/ascent-analytics")
async def get_ascent_analytics(
    mp_route_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get logged-ascent and accident counts for a route, overall and by calendar month.

    Counts only, no accidents-per-ascent rate: MP ticks are a capped, recent, self-reported
    sample, so a raw ratio misstates risk. A shrunk rate arrives with the Phase 3 model.
    """
    route = await get_route_with_location_coords(db, mp_route_id)

    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    route_type = (route.type or '').lower()
    if route_type in ['boulder', 'bouldering']:
        return {
            "route_id": mp_route_id,
            "route_name": route.name,
            "route_type": route.type,
            "total_ascents": 0,
            "total_accidents": 0,
            "undated_ascents": 0,
            "undated_accidents": 0,
            "monthly_stats": [],
            "peak_month": None,
            "accident_years": None,
            "ascent_years": None,
            "has_data": False,
            "excluded_reason": "Boulder problems are excluded from safety analytics",
        }

    # mp_ticks.route_id is text holding the MP route id.
    route_id_str = str(mp_route_id)
    # Some ticks carry impossible future dates (years up to 3901); they are data errors,
    # not ascents. Undated rows still count toward the totals. Accidents get the same cutoff.
    today = datetime.now(timezone.utc).date()
    tick_params = {"route_id": route_id_str, "today": today}
    accident_params = {"mp_route_id": mp_route_id, "today": today}
    # Undated rows are in the totals but in no month or span; the UI states them apart.
    total_ascents_query = text("""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE tick_date IS NULL) FROM mp_ticks
        WHERE route_id = :route_id
          AND (tick_date IS NULL OR tick_date <= :today)
    """)
    total_ascents, undated_ascents = (int(v) for v in (await db.execute(total_ascents_query, tick_params)).one())

    monthly_ascents_query = text("""
        SELECT
            EXTRACT(MONTH FROM tick_date) as month,
            COUNT(*) as ascent_count
        FROM mp_ticks
        WHERE route_id = :route_id
          AND tick_date IS NOT NULL
          AND tick_date <= :today
        GROUP BY EXTRACT(MONTH FROM tick_date)
        ORDER BY month
    """)
    monthly_result = await db.execute(monthly_ascents_query, tick_params)
    monthly_ascent_dict = {int(row[0]): int(row[1]) for row in monthly_result.fetchall()}

    # accidents.mp_route_id is the MP route FK; accidents.route_id is the legacy
    # routes-table FK and holds different ids (fixed 2026-09-28).
    accident_count_query = text("""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE date IS NULL)
        FROM accidents
        WHERE mp_route_id = :mp_route_id
          AND (date IS NULL OR date <= :today)
    """)
    total_accidents, undated_accidents = (
        int(v) for v in (await db.execute(accident_count_query, accident_params)).one()
    )

    monthly_accidents_query = text("""
        SELECT
            EXTRACT(MONTH FROM date) as month,
            COUNT(*) as accident_count
        FROM accidents
        WHERE mp_route_id = :mp_route_id
          AND date IS NOT NULL
          AND date <= :today
        GROUP BY EXTRACT(MONTH FROM date)
        ORDER BY month
    """)
    monthly_acc_result = await db.execute(monthly_accidents_query, accident_params)
    monthly_accident_dict = {int(row[0]): int(row[1]) for row in monthly_acc_result.fetchall()}

    # The two sides cover very different spans (accidents back decades, ticks a few
    # years), so the UI states both rather than implying they line up.
    ascent_years_query = text("""
        SELECT MIN(EXTRACT(YEAR FROM tick_date))::int, MAX(EXTRACT(YEAR FROM tick_date))::int
        FROM mp_ticks
        WHERE route_id = :route_id
          AND tick_date IS NOT NULL
          AND tick_date <= :today
    """)
    ascent_years = _year_span((await db.execute(ascent_years_query, tick_params)).one())

    accident_years_query = text("""
        SELECT MIN(EXTRACT(YEAR FROM date))::int, MAX(EXTRACT(YEAR FROM date))::int
        FROM accidents
        WHERE mp_route_id = :mp_route_id
          AND date IS NOT NULL
          AND date <= :today
    """)
    accident_years = _year_span((await db.execute(accident_years_query, accident_params)).one())

    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    monthly_stats = [
        {
            "month": month_names[i],
            "month_num": i + 1,
            "ascent_count": monthly_ascent_dict.get(i + 1, 0),
            "accident_count": monthly_accident_dict.get(i + 1, 0),
        }
        for i in range(12)
    ]

    months_with_ascents = [m for m in monthly_stats if m["ascent_count"] > 0]
    peak_month = max(months_with_ascents, key=lambda x: x["ascent_count"]) if months_with_ascents else None

    has_data = total_ascents > 0

    response = {
        "route_id": mp_route_id,
        "route_name": route.name,
        "route_type": route.type,
        "total_ascents": total_ascents,
        "total_accidents": total_accidents,
        "undated_ascents": undated_ascents,
        "undated_accidents": undated_accidents,
        "monthly_stats": monthly_stats,
        "peak_month": peak_month["month"] if peak_month else None,
        "accident_years": accident_years,
        "ascent_years": ascent_years,
        "has_data": has_data,
    }

    if not has_data:
        response["message"] = "No tick data available yet for this route."

    return response
