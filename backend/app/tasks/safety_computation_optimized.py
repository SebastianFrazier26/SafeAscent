"""
Optimized Safety Score Computation - Location-Level Pre-computation

This module provides a 6× faster safety computation by pre-computing
at the LOCATION level (~28K locations) instead of per-route (168K routes).

Architecture:
1. Load all accidents once
2. Load all locations with their routes (grouped by location_id)
3. For each location:
   - Compute base_influence for each accident (spatial × temporal × elevation × severity × weather²)
   - For each route at that location, apply route_type × grade adjustments
   - Cache results

This avoids redundant calculations when multiple routes share a location.
"""
import asyncio
import time
import logging
from datetime import date, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.mp_route import MpRoute
from app.models.mp_location import MpLocation
from app.models.accident import Accident
from app.celery_app import celery_app
from app.services.location_safety_computation import (
    compute_location_base_score_vectorized,
    compute_batch_route_scores,
    prepare_accident_arrays,
)
from app.services.weather_service import fetch_current_weather_pattern
from app.services.weather_similarity import (
    WeatherPattern,
    calculate_weather_similarity,
)
from app.services.route_type_mapper import infer_route_type_from_accident
from app.utils.cache import set_bulk_cached_safety_scores
from app.models.weather import Weather

# Note: SQLAlchemy/httpx loggers silenced in celery_app.py at worker startup

logger = logging.getLogger(__name__)

# Performance tuning
LOCATION_BATCH_SIZE = 100  # Process locations in batches
WEATHER_CONCURRENCY = 10   # Concurrent weather fetches
LOG_INTERVAL = 1000        # Log progress every N locations
DAYS_TO_COMPUTE = 3        # Today + 2 days ahead (for trip planning)


async def load_all_accidents(db: AsyncSession) -> List[Dict]:
    """
    Load all accidents into memory for batch processing.

    Returns list of accident dicts with all fields needed for computation.
    """
    logger.info("Loading all accidents...")

    result = await db.execute(
        select(
            Accident.accident_id,
            Accident.latitude,
            Accident.longitude,
            Accident.elevation_meters,
            Accident.date,
            Accident.activity,
            Accident.accident_type,
            Accident.tags,
            Accident.injury_severity,
            Accident.route,  # For grade inference if linked
        ).where(
            Accident.latitude.isnot(None),
            Accident.longitude.isnot(None),
        )
    )

    accidents = []
    for row in result:
        # Infer route type from accident data
        route_type = infer_route_type_from_accident(
            activity=row.activity,
            accident_type=row.accident_type,
            tags=row.tags,
        )

        accidents.append({
            "accident_id": row.accident_id,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "elevation_m": row.elevation_meters,
            "accident_date": row.date,
            "route_type": route_type,
            "severity": row.injury_severity or "unknown",
            "grade": None,  # TODO: Link accident grades if available
        })

    logger.info(f"✓ Loaded {len(accidents):,} accidents")
    return accidents


async def load_locations_with_routes(db: AsyncSession) -> Dict[int, Dict]:
    """
    Load all locations with their associated routes.

    Returns dict: {location_id: {
        "latitude": float,
        "longitude": float,
        "elevation_m": float or None,
        "routes": [{"route_id": int, "route_type": str, "grade": str}, ...]
    }}
    """
    logger.info("Loading locations with routes...")

    # Query locations and their routes
    # Exclude 'unknown' type routes from calculations
    result = await db.execute(
        select(
            MpLocation.mp_id,
            MpLocation.latitude,
            MpLocation.longitude,
            MpRoute.mp_route_id,
            MpRoute.type,
            MpRoute.grade,
        )
        .join(MpRoute, MpRoute.location_id == MpLocation.mp_id)
        .where(
            MpLocation.latitude.isnot(None),
            MpLocation.longitude.isnot(None),
            func.lower(MpRoute.type) != 'unknown',  # Exclude unknown routes
        )
    )

    locations = {}
    route_count = 0

    for row in result:
        loc_id = row.mp_id

        if loc_id not in locations:
            locations[loc_id] = {
                "latitude": row.latitude,
                "longitude": row.longitude,
                "elevation_m": None,  # Will fetch later if needed
                "routes": [],
            }

        # Normalize route type
        route_type = _normalize_route_type(row.type)

        locations[loc_id]["routes"].append({
            "route_id": row.mp_route_id,
            "route_type": route_type,
            "grade": row.grade,
        })
        route_count += 1

    logger.info(f"✓ Loaded {len(locations):,} locations with {route_count:,} routes")
    logger.info(f"  Average routes per location: {route_count / len(locations):.1f}")

    return locations


def _normalize_route_type(route_type: Optional[str]) -> str:
    """Normalize route type to standard categories."""
    if not route_type:
        return "trad"

    route_type_lower = route_type.lower()

    # Map common variations to standard types
    if "sport" in route_type_lower:
        return "sport"
    elif "trad" in route_type_lower:
        return "trad"
    elif "boulder" in route_type_lower:
        return "boulder"
    elif "alpine" in route_type_lower:
        return "alpine"
    elif "ice" in route_type_lower:
        return "ice"
    elif "mixed" in route_type_lower:
        return "mixed"
    elif "aid" in route_type_lower:
        return "aid"
    else:
        return "trad"  # Default


async def fetch_weather_for_location(
    lat: float,
    lon: float,
    target_date: date,
) -> Optional[WeatherPattern]:
    """Fetch weather pattern for a location."""
    try:
        return await asyncio.to_thread(
            fetch_current_weather_pattern, lat, lon, target_date
        )
    except Exception as e:
        logger.warning(f"Weather fetch failed for ({lat}, {lon}): {e}")
        return None


async def load_all_accident_weather_patterns(
    db: AsyncSession,
    accidents: List[Dict],
) -> Dict[int, Optional[WeatherPattern]]:
    """
    Load weather patterns for all accidents in a single bulk query.

    This is critical for optimization - instead of N queries, we do 1 query
    and group results in Python.

    Returns {accident_id: WeatherPattern or None}
    """
    logger.info("Loading accident weather patterns...")

    # Get accident IDs and dates
    accident_dates = {
        acc["accident_id"]: acc["accident_date"]
        for acc in accidents
        if acc.get("accident_date") is not None
    }

    if not accident_dates:
        return {}

    # Bulk query: fetch all weather records for all accidents

    result = await db.execute(
        select(
            Weather.accident_id,
            Weather.date,
            Weather.temperature_avg,
            Weather.temperature_min,
            Weather.temperature_max,
            Weather.precipitation_total,
            Weather.wind_speed_avg,
            Weather.visibility_avg,
            Weather.cloud_cover_avg,
        )
        .where(Weather.accident_id.in_(list(accident_dates.keys())))
        .order_by(Weather.accident_id, Weather.date)
    )

    # Group by accident_id
    weather_by_accident = defaultdict(list)
    for row in result:
        acc_date = accident_dates.get(row.accident_id)
        if acc_date is None:
            continue
        # Filter to 7-day window before accident
        days_before = (acc_date - row.date).days
        if 0 <= days_before <= 6:
            weather_by_accident[row.accident_id].append(row)

    # Build WeatherPattern for each accident
    patterns = {}
    for acc_id, records in weather_by_accident.items():
        if len(records) >= 5:  # Need at least 5 days
            # Sort by date and build pattern
            records.sort(key=lambda r: r.date)

            temperature = []
            precipitation = []
            wind_speed = []
            visibility = []
            cloud_cover = []
            daily_temps = []

            for r in records:
                temp_avg = r.temperature_avg or 10.0
                temp_min = r.temperature_min or temp_avg
                temp_max = r.temperature_max or temp_avg

                temperature.append(temp_avg)
                precipitation.append(r.precipitation_total or 0.0)
                wind_speed.append(r.wind_speed_avg or 5.0)
                visibility.append(r.visibility_avg or 10000.0)
                cloud_cover.append(r.cloud_cover_avg or 50.0)
                daily_temps.append((temp_min, temp_avg, temp_max))

            patterns[acc_id] = WeatherPattern(
                temperature=temperature,
                precipitation=precipitation,
                wind_speed=wind_speed,
                visibility=visibility,
                cloud_cover=cloud_cover,
                daily_temps=daily_temps,
            )
        else:
            patterns[acc_id] = None

    # Log coverage
    with_pattern = sum(1 for p in patterns.values() if p is not None)
    logger.info(f"✓ Loaded weather patterns for {with_pattern:,}/{len(accidents):,} accidents ({with_pattern*100/len(accidents):.1f}%)")

    return patterns


# Module-level cache for weather similarities by bucket
# Key: weather_bucket_key, Value: {accident_id: similarity}
_weather_similarity_cache: Dict[str, Dict[int, float]] = {}


def compute_weather_similarities_cached(
    weather_bucket_key: str,
    accidents: List[Dict],
    current_weather: Optional[WeatherPattern],
    accident_weather_patterns: Dict[int, Optional[WeatherPattern]],
) -> Dict[int, float]:
    """
    Compute weather similarity for all accidents against current weather.

    OPTIMIZATION: Results are cached by weather_bucket_key because locations
    in the same weather bucket share identical current weather, so their
    similarities to all accidents are the same.

    Returns {accident_id: similarity_score}
    """
    global _weather_similarity_cache

    # Check cache first
    if weather_bucket_key in _weather_similarity_cache:
        return _weather_similarity_cache[weather_bucket_key]

    similarities = {}

    # If no current weather, use neutral 0.5 for all
    if current_weather is None:
        for accident in accidents:
            similarities[accident["accident_id"]] = 0.5
        _weather_similarity_cache[weather_bucket_key] = similarities
        return similarities

    for accident in accidents:
        acc_id = accident["accident_id"]
        accident_pattern = accident_weather_patterns.get(acc_id)

        if accident_pattern is None:
            # No weather data for this accident - use neutral 0.5
            similarities[acc_id] = 0.5
        else:
            # Compute actual weather similarity
            similarity = calculate_weather_similarity(
                current_pattern=current_weather,
                accident_pattern=accident_pattern,
                historical_stats=None,  # Skip extreme detection for batch processing
            )
            similarities[acc_id] = similarity

    # Cache result
    _weather_similarity_cache[weather_bucket_key] = similarities

    return similarities


def clear_weather_similarity_cache():
    """Clear the weather similarity cache (call between dates)."""
    global _weather_similarity_cache
    _weather_similarity_cache = {}


async def compute_safety_scores_optimized(
    target_date: date,
    save_to_historical: bool = False,
) -> Dict:
    """
    Optimized safety score computation using location-level pre-computation.

    This is ~6× faster than per-route computation.

    Args:
        target_date: Date to compute scores for
        save_to_historical: Whether to save to historical_predictions table

    Returns:
        Statistics dict
    """
    start_time = time.time()
    date_str = target_date.isoformat()
    logger.warning(f"compute_safety_scores_optimized STARTED for {date_str}")

    # Clear weather similarity cache (weather changes by date)
    clear_weather_similarity_cache()
    logger.warning("Weather cache cleared")

    logger.warning("Opening database session...")
    async with AsyncSessionLocal() as db:
        logger.warning("Database session opened")

        # Step 1: Load all data
        logger.warning("Loading accidents...")
        accidents = await load_all_accidents(db)
        logger.warning(f"Loaded {len(accidents)} accidents")

        logger.warning("Loading locations with routes...")
        locations = await load_locations_with_routes(db)
        logger.warning(f"Loaded {len(locations)} locations")

        if not locations:
            return {"status": "no_locations", "computed": 0}

        total_locations = len(locations)
        total_routes = sum(len(loc["routes"]) for loc in locations.values())

        logger.info(f"Processing {total_locations:,} locations, {total_routes:,} routes for {date_str}")

        # Step 1.5: Load all accident weather patterns (one bulk query)
        accident_weather_patterns = await load_all_accident_weather_patterns(db, accidents)

        # Step 1.6: Prepare accident arrays for vectorized computation
        logger.info("Preparing accident arrays for vectorized computation...")
        accident_arrays = prepare_accident_arrays(accidents)
        logger.info(f"✓ Prepared {len(accidents):,} accidents for vectorized processing")

        # Step 2: Pre-fetch all weather in parallel (one API call per unique bucket)
        logger.info("Pre-fetching weather for all unique location buckets...")
        weather_start = time.time()
        weather_cache = await _prefetch_weather_bulk(locations, target_date)
        weather_elapsed = time.time() - weather_start
        logger.info(f"✓ Weather pre-fetch complete in {weather_elapsed:.1f}s ({len(weather_cache)} buckets)")

        # Step 3: Process locations in batches (no more API calls!)
        stats = {
            "total_locations": total_locations,
            "total_routes": total_routes,
            "locations_processed": 0,
            "routes_computed": 0,
            "failed": 0,
            "weather_buckets": len(weather_cache),
            "weather_fetch_time": round(weather_elapsed, 1),
        }

        location_ids = list(locations.keys())
        all_route_scores = {}

        compute_start = time.time()
        for batch_start in range(0, total_locations, LOCATION_BATCH_SIZE):
            batch_ids = location_ids[batch_start:batch_start + LOCATION_BATCH_SIZE]
            batch_locations = {loc_id: locations[loc_id] for loc_id in batch_ids}

            # Process batch with pre-fetched weather and accident weather patterns
            batch_scores = await _process_location_batch(
                batch_locations, accidents, target_date, weather_cache,
                accident_weather_patterns, accident_arrays
            )

            all_route_scores.update(batch_scores)
            stats["locations_processed"] += len(batch_ids)
            stats["routes_computed"] += len(batch_scores)

            # Progress logging
            if stats["locations_processed"] % LOG_INTERVAL == 0 or stats["locations_processed"] == total_locations:
                compute_elapsed = time.time() - compute_start
                rate = stats["locations_processed"] / compute_elapsed if compute_elapsed > 0 else 0
                pct = (stats["locations_processed"] / total_locations) * 100
                logger.info(
                    f"  Progress: {stats['locations_processed']:,}/{total_locations:,} locations "
                    f"({pct:.1f}%, {rate:.0f} loc/sec)"
                )

        # Step 3: Cache all results
        if all_route_scores:
            set_bulk_cached_safety_scores(all_route_scores, date_str)
            logger.info(f"✓ Cached {len(all_route_scores):,} route scores for {date_str}")

            # Save to historical if requested
            if save_to_historical:
                await _save_to_historical(db, all_route_scores, target_date)

        # Final stats
        elapsed = time.time() - start_time
        stats["elapsed_seconds"] = round(elapsed, 1)
        stats["locations_per_second"] = round(total_locations / elapsed, 1) if elapsed > 0 else 0
        stats["routes_per_second"] = round(total_routes / elapsed, 1) if elapsed > 0 else 0
        stats["status"] = "completed"

        return stats


async def _prefetch_weather_bulk(
    locations: Dict[int, Dict],
    target_date: date,
) -> Dict[str, Optional[WeatherPattern]]:
    """
    Pre-fetch weather for all unique location buckets in parallel.

    Returns {bucket_key: WeatherPattern or None}
    """
    # Group locations by weather bucket (0.01° ≈ 1km)
    unique_buckets = set()
    for loc_data in locations.values():
        lat, lon = loc_data["latitude"], loc_data["longitude"]
        bucket_key = f"{round(lat, 2)}:{round(lon, 2)}"
        unique_buckets.add((bucket_key, lat, lon))

    logger.info(f"  Pre-fetching weather for {len(unique_buckets)} unique buckets...")

    # Fetch in parallel with semaphore
    semaphore = asyncio.Semaphore(WEATHER_CONCURRENCY)

    async def fetch_one(bucket_key: str, lat: float, lon: float):
        async with semaphore:
            weather = await fetch_weather_for_location(lat, lon, target_date)
            return (bucket_key, weather)

    results = await asyncio.gather(
        *[fetch_one(bk, lat, lon) for bk, lat, lon in unique_buckets],
        return_exceptions=True
    )

    weather_cache = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        bucket_key, weather = result
        weather_cache[bucket_key] = weather

    return weather_cache


async def _process_location_batch(
    locations: Dict[int, Dict],
    accidents: List[Dict],
    target_date: date,
    weather_cache: Optional[Dict[str, Optional[WeatherPattern]]] = None,
    accident_weather_patterns: Optional[Dict[int, Optional[WeatherPattern]]] = None,
    accident_arrays: Optional[tuple] = None,
) -> Dict[int, Dict]:
    """
    Process a batch of locations and return route scores.

    For each location:
    1. Look up pre-fetched weather (or use neutral if missing)
    2. Compute weather similarities using pre-loaded accident weather patterns
    3. Compute base scores for all accidents (VECTORIZED for speed)
    4. Apply route-specific adjustments for all routes at this location

    Returns {route_id: {"risk_score": float, "color_code": str}}
    """
    all_scores = {}

    # Pre-fetch weather if not provided
    if weather_cache is None:
        weather_cache = await _prefetch_weather_bulk(locations, target_date)

    # Default to empty dict if no accident weather patterns
    if accident_weather_patterns is None:
        accident_weather_patterns = {}

    # Prepare accident arrays for vectorized computation (once per batch)
    if accident_arrays is None:
        accident_arrays = prepare_accident_arrays(accidents)

    for loc_id, loc_data in locations.items():
        lat, lon = loc_data["latitude"], loc_data["longitude"]
        weather_key = f"{round(lat, 2)}:{round(lon, 2)}"

        current_weather = weather_cache.get(weather_key)

        # Compute weather similarities for accidents (cached by bucket)
        weather_similarities = compute_weather_similarities_cached(
            weather_key, accidents, current_weather, accident_weather_patterns
        )

        # Determine most common route type at this location (for bandwidth)
        route_types = [r["route_type"] for r in loc_data["routes"]]
        default_route_type = max(set(route_types), key=route_types.count) if route_types else "trad"

        # Compute location base score (VECTORIZED for ~50x speedup)
        location_base = compute_location_base_score_vectorized(
            location_id=loc_id,
            location_lat=lat,
            location_lon=lon,
            location_elevation_m=loc_data.get("elevation_m"),
            target_date=target_date,
            accident_arrays=accident_arrays,
            weather_similarity_map=weather_similarities,
            default_route_type=default_route_type,
        )

        # Apply route-specific adjustments for all routes at this location
        route_scores = compute_batch_route_scores(
            location_base=location_base,
            routes=loc_data["routes"],
        )

        all_scores.update(route_scores)

    return all_scores


async def _save_to_historical(
    db: AsyncSession,
    scores: Dict[int, Dict],
    target_date: date,
) -> None:
    """
    Save scores to historical_predictions table.

    Uses batched inserts to avoid PostgreSQL parameter limits.
    Also purges data older than 1 year for storage efficiency.
    """
    if not scores:
        return

    BATCH_SIZE = 5000  # Stay well under PostgreSQL's 65535 param limit (4 params per row)
    total_saved = 0
    score_items = list(scores.items())

    for batch_start in range(0, len(score_items), BATCH_SIZE):
        batch = score_items[batch_start:batch_start + BATCH_SIZE]

        # Build batch insert
        values_list = []
        params = {}

        for i, (route_id, score) in enumerate(batch):
            values_list.append(f"(:route_id_{i}, :date_{i}, :risk_{i}, :color_{i})")
            params[f"route_id_{i}"] = route_id
            params[f"date_{i}"] = target_date
            params[f"risk_{i}"] = score["risk_score"]
            params[f"color_{i}"] = score["color_code"]

        values_sql = ", ".join(values_list)

        try:
            await db.execute(text(f"""
                INSERT INTO historical_predictions
                    (route_id, prediction_date, risk_score, color_code)
                VALUES {values_sql}
                ON CONFLICT (route_id, prediction_date)
                DO UPDATE SET
                    risk_score = EXCLUDED.risk_score,
                    color_code = EXCLUDED.color_code,
                    calculated_at = NOW()
            """), params)
            await db.commit()
            total_saved += len(batch)
        except Exception as e:
            logger.error(f"Failed to save batch to historical_predictions: {e}")
            await db.rollback()

    logger.info(f"✓ Saved {total_saved:,} to historical_predictions for {target_date}")

    # Purge data older than 1 year (run occasionally)
    try:
        result = await db.execute(text("""
            DELETE FROM historical_predictions
            WHERE prediction_date < CURRENT_DATE - INTERVAL '1 year'
        """))
        await db.commit()
        if result.rowcount > 0:
            logger.info(f"✓ Purged {result.rowcount:,} historical records older than 1 year")
    except Exception as e:
        logger.error(f"Failed to purge old historical data: {e}")
        await db.rollback()


@celery_app.task(name="app.tasks.safety_computation_optimized.compute_daily_safety_scores_optimized")
def compute_daily_safety_scores_optimized():
    """
    Celery task for optimized daily safety score computation.

    Uses location-level pre-computation for ~6× speedup.
    """
    import sys
    print("=" * 60, flush=True)
    print("OPTIMIZED TASK STARTING", flush=True)
    print(f"Python: {sys.version}", flush=True)
    print("=" * 60, flush=True)

    logger.warning("=" * 60)
    logger.warning("STARTING OPTIMIZED SAFETY SCORE COMPUTATION")
    logger.warning(f"Settings: LOCATION_BATCH_SIZE={LOCATION_BATCH_SIZE}")
    logger.warning("=" * 60)

    try:
        logger.warning("Creating event loop...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.warning("Event loop created, starting async computation...")
        try:
            result = loop.run_until_complete(_compute_all_dates_async())
        finally:
            loop.close()
            logger.warning("Event loop closed")

        logger.info("=" * 60)
        logger.info(f"OPTIMIZED COMPUTATION COMPLETE: {result}")
        logger.info("=" * 60)
        return result
    except Exception as e:
        logger.error(f"Optimized computation failed: {e}", exc_info=True)
        raise


async def _compute_all_dates_async() -> Dict:
    """Compute safety scores for all dates."""
    logger.warning("_compute_all_dates_async() STARTED")
    today = date.today()
    logger.warning(f"Today: {today}, DAYS_TO_COMPUTE: {DAYS_TO_COMPUTE}")
    dates_to_compute = [today + timedelta(days=i) for i in range(DAYS_TO_COMPUTE)]

    all_stats = {
        "dates_computed": len(dates_to_compute),
        "total_routes": 0,
        "total_time": 0,
    }

    for target_date in dates_to_compute:
        is_today = (target_date == today)
        logger.info(f"Computing for {target_date.isoformat()}{' (saving historical)' if is_today else ''}")

        stats = await compute_safety_scores_optimized(
            target_date=target_date,
            save_to_historical=is_today,
        )

        all_stats["total_routes"] = stats.get("total_routes", 0)
        all_stats["total_time"] += stats.get("elapsed_seconds", 0)

    all_stats["total_time"] = round(all_stats["total_time"], 1)
    return all_stats
