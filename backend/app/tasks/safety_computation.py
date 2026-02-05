"""
Nightly Safety Score Pre-computation Task

This task runs at 2am daily to pre-compute safety scores for ALL routes
for the next 7 days. Results are stored in Redis cache for fast retrieval
by the bulk safety endpoint.

Performance Optimizations:
- PARALLELIZATION: Uses asyncio.gather to process routes concurrently within batches
- LOCATION BUCKETS: Routes within 0.01° (~1km) share weather data to reduce API calls
- BATCH SIZE: 200 routes per batch (increased from 100)
- BULK CACHING: Uses Redis pipeline for efficient cache writes

Estimated runtime: ~10-20 minutes for 168K routes (vs 30-60 min without optimizations)
Progress logged every 1000 routes for monitoring.
"""
import asyncio
from datetime import date, timedelta
from typing import List, Dict, Optional, Tuple
import logging
import time

from sqlalchemy import select, text

from app.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.models.mp_route import MpRoute
from app.models.mp_location import MpLocation
from app.schemas.prediction import PredictionRequest
from app.api.v1.predict import predict_route_safety
from app.api.v1.mp_routes import normalize_route_type, get_safety_color_code
from app.utils.cache import (
    set_bulk_cached_safety_scores,
    get_safety_cache_stats,
)
from app.services.weather_service import fetch_current_weather_pattern
from app.services.weather_similarity import WeatherPattern

logger = logging.getLogger(__name__)

# Module-level weather cache for batch processing
# Populated once per date, keyed by location bucket
_prefetched_weather: Dict[str, Optional[WeatherPattern]] = {}

# ============================================================================
# PERFORMANCE TUNING CONSTANTS
# ============================================================================
BATCH_SIZE = 200          # Routes per batch (increased from 100)
CONCURRENCY_LIMIT = 20    # Max concurrent safety calculations per batch
LOG_INTERVAL = 1000       # Log progress every N routes
LOCATION_BUCKET_PRECISION = 2  # Decimal places for location bucketing (0.01° ≈ 1km)
WEATHER_PREFETCH_BATCH_SIZE = 50  # Fetch weather for N locations at a time
WEATHER_PREFETCH_CONCURRENCY = 10  # Parallel weather fetches (paid API has no rate limits)


async def _fetch_single_weather(
    bucket_key: str,
    target_date: date,
    semaphore: asyncio.Semaphore,
) -> Tuple[str, Optional[WeatherPattern]]:
    """
    Fetch weather for a single location bucket with semaphore-limited concurrency.

    Uses asyncio.to_thread() to run the synchronous requests-based weather
    fetch in a thread pool, allowing parallel execution.
    """
    async with semaphore:
        # Parse lat/lon from bucket key (format: "40.01:-105.27")
        lat_str, lon_str = bucket_key.split(":")
        lat = float(lat_str)
        lon = float(lon_str)

        try:
            # Run sync function in thread pool for parallelization
            weather = await asyncio.to_thread(
                fetch_current_weather_pattern, lat, lon, target_date
            )
            return (bucket_key, weather)
        except Exception as e:
            logger.warning(f"Weather fetch failed for {bucket_key}: {e}")
            return (bucket_key, None)


async def _prefetch_weather_for_locations(
    location_buckets: Dict[str, List],
    target_date: date,
) -> Dict[str, Optional[WeatherPattern]]:
    """
    Pre-fetch weather patterns for all unique location buckets IN PARALLEL.

    Instead of fetching weather per-route (168K API calls), we fetch
    per-location-bucket (much fewer). Routes in the same bucket share weather.

    PARALLELIZATION: Uses asyncio.gather with a semaphore to fetch multiple
    locations concurrently (10 at a time with paid Open-Meteo API).

    Args:
        location_buckets: Dict mapping bucket keys to lists of routes
        target_date: Date to fetch weather for

    Returns:
        Dict mapping bucket_key to WeatherPattern (or None if fetch failed)
    """
    bucket_keys = list(location_buckets.keys())
    total_buckets = len(bucket_keys)

    logger.info(f"Pre-fetching weather for {total_buckets:,} unique location buckets (PARALLEL: {WEATHER_PREFETCH_CONCURRENCY} concurrent)")
    start_time = time.time()

    # Semaphore limits concurrent API requests
    semaphore = asyncio.Semaphore(WEATHER_PREFETCH_CONCURRENCY)

    # Process in batches for progress logging, but parallelize within each batch
    weather_map: Dict[str, Optional[WeatherPattern]] = {}

    for batch_start in range(0, total_buckets, WEATHER_PREFETCH_BATCH_SIZE):
        batch_keys = bucket_keys[batch_start:batch_start + WEATHER_PREFETCH_BATCH_SIZE]

        # PARALLEL: Fetch all locations in this batch concurrently (limited by semaphore)
        results = await asyncio.gather(
            *[_fetch_single_weather(key, target_date, semaphore) for key in batch_keys],
            return_exceptions=True
        )

        # Collect results
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Weather fetch exception: {result}")
                continue
            bucket_key, weather = result
            weather_map[bucket_key] = weather

        # Log progress every few batches
        processed = min(batch_start + WEATHER_PREFETCH_BATCH_SIZE, total_buckets)
        if processed % 500 == 0 or processed == total_buckets:
            elapsed = time.time() - start_time
            rate = processed / elapsed if elapsed > 0 else 0
            logger.info(f"  Weather pre-fetch: {processed:,}/{total_buckets:,} locations ({elapsed:.1f}s, {rate:.0f}/sec)")

    # Count successes
    successful = sum(1 for w in weather_map.values() if w is not None)
    elapsed = time.time() - start_time
    rate = total_buckets / elapsed if elapsed > 0 else 0
    logger.info(f"Weather pre-fetch complete: {successful:,}/{total_buckets:,} successful in {elapsed:.1f}s ({rate:.0f} locations/sec)")

    return weather_map


@celery_app.task(
    name="app.tasks.safety_computation.compute_daily_safety_scores",
    bind=True,
    max_retries=3,
    default_retry_delay=300,  # 5 minutes
)
def compute_daily_safety_scores(self):
    """
    Pre-compute safety scores for ALL routes for the next 7 days.

    This task:
    1. Queries all MP routes with valid coordinates (via location join)
    2. Groups routes by location bucket to share weather data
    3. Calculates safety scores in parallel using asyncio.gather
    4. Stores results in Redis cache with 7-day TTL

    Runs at 2am daily via Celery Beat scheduler.
    """
    logger.info("=" * 60)
    logger.info("STARTING NIGHTLY SAFETY SCORE PRE-COMPUTATION")
    logger.info(f"Settings: BATCH_SIZE={BATCH_SIZE}, CONCURRENCY={CONCURRENCY_LIMIT}")
    logger.info("FIX APPLIED: Each route uses its own DB session (v2)")
    logger.info("=" * 60)

    try:
        # Create a fresh event loop for each task execution
        # (Celery fork pool reuses workers, so asyncio.run() can leave loops closed)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_compute_all_safety_scores_async())
        finally:
            loop.close()

        logger.info("=" * 60)
        logger.info(f"NIGHTLY COMPUTATION COMPLETE: {result}")
        logger.info("=" * 60)
        return result
    except Exception as e:
        logger.error(f"Nightly safety computation failed: {e}", exc_info=True)
        # Retry on failure
        raise self.retry(exc=e)


def _get_location_bucket(latitude: float, longitude: float) -> str:
    """
    Create a location bucket key for weather caching.

    Routes within the same bucket (~1km) share weather data,
    reducing redundant API calls significantly.

    Args:
        latitude: Route latitude
        longitude: Route longitude

    Returns:
        Bucket key like "40.01:-105.27"
    """
    lat_bucket = round(latitude, LOCATION_BUCKET_PRECISION)
    lon_bucket = round(longitude, LOCATION_BUCKET_PRECISION)
    return f"{lat_bucket}:{lon_bucket}"


async def _compute_all_safety_scores_async() -> dict:
    """
    Async implementation of safety score pre-computation.

    Returns:
        dict: Statistics about the computation process
    """
    start_time = time.time()

    async with AsyncSessionLocal() as db:
        # Query all routes with coordinates from their parent location
        query = (
            select(
                MpRoute.mp_route_id,
                MpRoute.name,
                MpRoute.type,
                MpLocation.latitude,
                MpLocation.longitude,
            )
            .join(MpLocation, MpRoute.location_id == MpLocation.mp_id)
            .where(
                MpLocation.latitude.isnot(None),
                MpLocation.longitude.isnot(None)
            )
        )

        result = await db.execute(query)
        routes = result.fetchall()

        if not routes:
            logger.warning("No routes found for safety computation")
            return {"status": "no_routes", "computed": 0}

        total_routes = len(routes)

        # Group routes by location bucket for weather sharing
        location_buckets = {}
        for route in routes:
            bucket = _get_location_bucket(route.latitude, route.longitude)
            if bucket not in location_buckets:
                location_buckets[bucket] = []
            location_buckets[bucket].append(route)

        logger.info(f"Found {total_routes:,} routes in {len(location_buckets):,} location buckets")
        logger.info(f"Average routes per bucket: {total_routes / len(location_buckets):.1f}")

        # Calculate safety for next 7 days
        today = date.today()
        dates_to_compute = [today + timedelta(days=i) for i in range(7)]

        stats = {
            "total_routes": total_routes,
            "location_buckets": len(location_buckets),
            "dates_computed": len(dates_to_compute),
            "successful": 0,
            "failed": 0,
            "cached": 0,
            "historical_saved": 0,
        }

        # Ensure historical_predictions table exists (for today's save)
        await _ensure_historical_predictions_table(db)

        # Process each date
        for target_date in dates_to_compute:
            date_str = target_date.isoformat()
            date_start = time.time()
            is_today = (target_date == today)
            logger.info(f"Processing date: {date_str}{' (saving to historical)' if is_today else ''}")

            # Pre-fetch weather for all unique location buckets (once per date)
            # This reduces 168K API calls to ~X calls (one per unique bucket)
            weather_map = await _prefetch_weather_for_locations(location_buckets, target_date)

            date_stats = await _compute_safety_for_date_parallel(
                db, routes, target_date, total_routes,
                save_to_historical=is_today,  # Only save today's predictions for trends
                weather_map=weather_map,  # Pass pre-fetched weather
            )

            stats["successful"] += date_stats["successful"]
            stats["failed"] += date_stats["failed"]
            stats["cached"] += date_stats["cached"]

            # Log cache stats and timing for this date
            date_elapsed = time.time() - date_start
            cache_stats = get_safety_cache_stats(date_str)
            logger.info(
                f"  ✓ {date_str}: {cache_stats.get('cached_count', 0):,} cached "
                f"in {date_elapsed:.1f}s ({total_routes / date_elapsed:.0f} routes/sec)"
            )

        # Calculate elapsed time
        elapsed = time.time() - start_time
        stats["elapsed_seconds"] = round(elapsed, 1)
        stats["elapsed_human"] = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
        stats["routes_per_second"] = round((total_routes * 7) / elapsed, 1)
        stats["status"] = "completed"

        return stats


async def _compute_single_route_safety(
    route: Tuple,
    target_date: date,
    prefetched_weather: Optional[WeatherPattern] = None,
) -> Tuple[int, Optional[Dict]]:
    """
    Compute safety score for a single route.

    IMPORTANT: Each call creates its own database session to avoid
    asyncpg "another operation is in progress" errors when running
    concurrent coroutines. asyncpg connections can only handle one
    query at a time, so shared sessions don't work with asyncio.gather.

    Args:
        route: Tuple of (mp_route_id, name, type, latitude, longitude)
        target_date: Date to compute safety for
        prefetched_weather: Pre-fetched weather pattern for this location bucket

    Returns:
        Tuple of (route_id, score_dict or None if failed)
    """
    mp_route_id, name, route_type, latitude, longitude = route

    try:
        # Normalize route type
        normalized_type = normalize_route_type(route_type)

        # Create prediction request
        prediction_request = PredictionRequest(
            latitude=latitude,
            longitude=longitude,
            route_type=normalized_type,
            planned_date=target_date,
            elevation_meters=None,  # Auto-detect from location
        )

        # CRITICAL: Create a NEW session for each concurrent route calculation
        # asyncpg only allows one operation per connection at a time, so we
        # cannot share a session across concurrent coroutines
        async with AsyncSessionLocal() as route_db:
            # Calculate safety score with dedicated session
            # Pass pre-fetched weather to avoid API calls during batch processing
            prediction = await predict_route_safety(
                prediction_request, route_db, prefetched_weather=prefetched_weather
            )

            # Determine color code
            color_code = get_safety_color_code(prediction.risk_score)

            return (mp_route_id, {
                "risk_score": round(prediction.risk_score, 1),
                "color_code": color_code,
            })

    except Exception as e:
        logger.warning(f"Failed to compute safety for route {mp_route_id}: {e}")
        return (mp_route_id, None)


async def _compute_safety_for_date_parallel(
    db,
    routes: List,
    target_date: date,
    total_routes: int,
    save_to_historical: bool = False,
    weather_map: Optional[Dict[str, Optional[WeatherPattern]]] = None,
) -> Dict:
    """
    Compute safety scores for all routes for a single date using PARALLEL processing.

    Uses asyncio.gather with a semaphore to limit concurrency and avoid
    overwhelming the database/API.

    Args:
        db: Database session
        routes: List of route tuples (mp_route_id, name, type, latitude, longitude)
        target_date: The date to compute scores for
        total_routes: Total route count for progress logging
        weather_map: Pre-fetched weather by location bucket (reduces API calls)

    Returns:
        Dict with successful/failed counts
    """
    date_str = target_date.isoformat()
    successful = 0
    failed = 0

    # Semaphore to limit concurrent operations
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async def compute_with_semaphore(route):
        async with semaphore:
            # Look up pre-fetched weather for this route's location bucket
            prefetched_weather = None
            if weather_map:
                bucket_key = _get_location_bucket(route.latitude, route.longitude)
                prefetched_weather = weather_map.get(bucket_key)

            # NOTE: Each coroutine creates its own db session inside
            # _compute_single_route_safety to avoid asyncpg conflicts
            return await _compute_single_route_safety(route, target_date, prefetched_weather)

    # Process in batches
    logger.info(f"Starting batch processing: {len(routes)} routes in batches of {BATCH_SIZE}")
    for batch_start in range(0, len(routes), BATCH_SIZE):
        batch = routes[batch_start:batch_start + BATCH_SIZE]

        # PARALLEL: Compute all routes in batch concurrently
        try:
            results = await asyncio.gather(
                *[compute_with_semaphore(route) for route in batch],
                return_exceptions=True
            )
        except Exception as e:
            logger.error(f"Batch {batch_start} asyncio.gather failed: {e}")
            continue

        # Collect successful results for bulk caching
        batch_scores = {}
        for result in results:
            if isinstance(result, Exception):
                failed += 1
                continue
            route_id, score = result
            if score is not None:
                batch_scores[route_id] = score
                successful += 1
            else:
                failed += 1

        # Log batch results
        batch_success = len(batch_scores)
        batch_failed = len(batch) - batch_success
        if batch_start == 0:  # Log first batch in detail
            logger.info(f"First batch: {batch_success} succeeded, {batch_failed} failed")

        # Bulk cache this batch
        if batch_scores:
            set_bulk_cached_safety_scores(batch_scores, date_str)

            # Also save to historical_predictions for trend analysis
            if save_to_historical:
                await _save_batch_to_historical_predictions(db, batch_scores, target_date)

        # Progress logging
        processed = batch_start + len(batch)
        if processed % LOG_INTERVAL == 0 or processed == total_routes:
            pct = (processed / total_routes) * 100
            logger.info(f"  Progress for {date_str}: {processed:,}/{total_routes:,} ({pct:.1f}%)")

    return {
        "successful": successful,
        "failed": failed,
        "cached": successful,
    }


async def _ensure_historical_predictions_table(db):
    """Create historical_predictions table if it doesn't exist."""
    try:
        # Check if table already exists first
        result = await db.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'historical_predictions'
            )
        """))
        exists = result.scalar()

        if not exists:
            await db.execute(text("""
                CREATE TABLE historical_predictions (
                    id SERIAL PRIMARY KEY,
                    route_id INTEGER NOT NULL,
                    prediction_date DATE NOT NULL,
                    risk_score FLOAT NOT NULL,
                    color_code VARCHAR(10) NOT NULL,
                    calculated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(route_id, prediction_date)
                )
            """))
            await db.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_historical_predictions_route
                    ON historical_predictions(route_id)
            """))
            await db.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_historical_predictions_date
                    ON historical_predictions(prediction_date)
            """))
            await db.commit()
            logger.info("Created historical_predictions table")
        else:
            logger.info("historical_predictions table already exists")
    except Exception as e:
        # Table might already exist from concurrent creation attempts
        logger.warning(f"Table creation skipped (may already exist): {e}")
        await db.rollback()


async def _save_batch_to_historical_predictions(
    db,
    batch_scores: Dict[int, Dict],
    target_date: date,
) -> int:
    """
    Save a batch of predictions to historical_predictions table.

    Uses UPSERT to handle duplicates (updates if exists).
    Only saves for TODAY's date to build historical trends.

    Returns count of saved records.
    """
    if not batch_scores:
        return 0

    # Build batch insert with ON CONFLICT UPDATE
    values_list = []
    params = {}
    for i, (route_id, score) in enumerate(batch_scores.items()):
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
        return len(batch_scores)
    except Exception as e:
        logger.error(f"Failed to save to historical_predictions: {e}")
        await db.rollback()
        return 0


@celery_app.task(name="app.tasks.safety_computation.compute_safety_for_single_date")
def compute_safety_for_single_date(date_str: str):
    """
    Compute safety scores for all routes for a single specific date.

    Useful for manual triggers or catch-up processing.

    Args:
        date_str: Date string in ISO format (YYYY-MM-DD)
    """
    logger.info(f"Computing safety scores for single date: {date_str}")
    logger.info(f"Settings: BATCH_SIZE={BATCH_SIZE}, CONCURRENCY={CONCURRENCY_LIMIT}")

    try:
        # Create a fresh event loop for each task execution
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_compute_single_date_async(date_str))
        finally:
            loop.close()

        logger.info(f"Single date computation complete: {result}")
        return result
    except Exception as e:
        logger.error(f"Single date computation failed: {e}", exc_info=True)
        raise


async def _compute_single_date_async(date_str: str) -> dict:
    """Compute safety scores for a single date."""
    from datetime import datetime

    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    start_time = time.time()

    async with AsyncSessionLocal() as db:
        query = (
            select(
                MpRoute.mp_route_id,
                MpRoute.name,
                MpRoute.type,
                MpLocation.latitude,
                MpLocation.longitude,
            )
            .join(MpLocation, MpRoute.location_id == MpLocation.mp_id)
            .where(
                MpLocation.latitude.isnot(None),
                MpLocation.longitude.isnot(None)
            )
        )

        result = await db.execute(query)
        routes = result.fetchall()

        total_routes = len(routes)

        # Save to historical if processing today's date
        is_today = (target_date == date.today())
        if is_today:
            await _ensure_historical_predictions_table(db)

        logger.info(f"Processing {total_routes:,} routes for {date_str}{' (saving to historical)' if is_today else ''}")

        stats = await _compute_safety_for_date_parallel(
            db, routes, target_date, total_routes,
            save_to_historical=is_today,
        )

        elapsed = time.time() - start_time
        cache_stats = get_safety_cache_stats(date_str)

        return {
            "date": date_str,
            "routes_processed": total_routes,
            "successful": stats["successful"],
            "failed": stats["failed"],
            "cached_total": cache_stats.get("cached_count", 0),
            "elapsed_seconds": round(elapsed, 1),
            "routes_per_second": round(total_routes / elapsed, 1) if elapsed > 0 else 0,
        }
