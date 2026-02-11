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
import json
import time
import logging
import uuid
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple
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
from app.utils.cache import get_redis_client, set_bulk_cached_safety_scores
from app.models.weather import Weather

# Note: SQLAlchemy/httpx loggers silenced in celery_app.py at worker startup

logger = logging.getLogger(__name__)

# Performance tuning
LOCATION_BATCH_SIZE = 100  # Process locations in batches
WEATHER_CONCURRENCY = 10   # Concurrent weather fetches
LOG_INTERVAL = 1000        # Log progress every N locations
DAYS_TO_COMPUTE = 3        # Today + 2 days ahead (for trip planning)
DATE_RETRY_ATTEMPTS = 3
DATE_RETRY_BACKOFF_SECONDS = 20
CACHE_POPULATION_LOCK_KEY = "safety:cache_population:optimized:lock"
CACHE_POPULATION_LOCK_TTL_SECONDS = 6 * 60 * 60  # 6 hours
CACHE_POPULATION_STALE_AFTER_SECONDS = 30 * 60   # 30 minutes
OPTIMIZED_TASK_NAME = "app.tasks.safety_computation_optimized.compute_daily_safety_scores_optimized"


def _parse_lock_payload(raw_value: Optional[str]) -> Dict[str, Optional[object]]:
    """Parse lock payload from Redis (supports legacy task_id:uuid format)."""
    if not raw_value:
        return {"task_id": None, "acquired_at": None}

    try:
        payload = json.loads(raw_value)
        if isinstance(payload, dict):
            acquired_at = payload.get("acquired_at")
            if isinstance(acquired_at, (int, float)):
                acquired_at = int(acquired_at)
            else:
                acquired_at = None
            return {
                "task_id": payload.get("task_id"),
                "acquired_at": acquired_at,
            }
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    # Legacy payload format: "<task_id>:<uuid>"
    # Keep compatibility so stale pre-patch locks can be recovered.
    task_id = raw_value.split(":", 1)[0] if ":" in raw_value else None
    return {"task_id": task_id, "acquired_at": None}


def _get_active_optimized_task_ids() -> set[str]:
    """Return active optimized task IDs currently executing in Celery workers."""
    try:
        inspect = celery_app.control.inspect(timeout=1)
        active_workers = inspect.active() or {}
    except Exception as exc:
        logger.warning("Could not inspect Celery active tasks during lock check: %s", exc)
        return set()

    active_task_ids: set[str] = set()
    for worker_tasks in active_workers.values():
        for task in worker_tasks or []:
            if task.get("name") == OPTIMIZED_TASK_NAME and task.get("id"):
                active_task_ids.add(task["id"])
    return active_task_ids


def _delete_lock_if_unchanged(lock_value: str) -> bool:
    """Delete lock only if Redis still contains the expected lock value."""
    client = get_redis_client()
    if client is None:
        return False

    try:
        deleted = client.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """,
            1,
            CACHE_POPULATION_LOCK_KEY,
            lock_value,
        )
        return bool(deleted)
    except Exception as exc:
        logger.warning("Failed to conditionally clear optimized task lock: %s", exc)
        return False


def _try_recover_stale_lock(existing_lock_value: str) -> bool:
    """Recover lock if owner is not active and lock appears stale/terminal."""
    client = get_redis_client()
    if client is None:
        return False

    payload = _parse_lock_payload(existing_lock_value)
    owner_task_id = payload.get("task_id")
    acquired_at = payload.get("acquired_at")
    lock_ttl_seconds = client.ttl(CACHE_POPULATION_LOCK_KEY)
    now_ts = int(time.time())

    active_ids = _get_active_optimized_task_ids()
    if owner_task_id and owner_task_id in active_ids:
        return False
    if not owner_task_id and active_ids:
        return False

    stale_reason = None

    if owner_task_id:
        try:
            owner_state = str(celery_app.AsyncResult(owner_task_id).state or "").upper()
        except Exception as exc:
            logger.warning("Could not fetch owner task state (%s): %s", owner_task_id, exc)
            owner_state = "UNKNOWN"

        if owner_state in {"SUCCESS", "FAILURE", "REVOKED"}:
            stale_reason = f"owner_task_terminal:{owner_state.lower()}"

    if stale_reason is None:
        lock_age = None
        if isinstance(acquired_at, int):
            lock_age = max(0, now_ts - acquired_at)
        elif isinstance(lock_ttl_seconds, int) and lock_ttl_seconds >= 0:
            # Infer age for legacy lock payloads from known initial TTL.
            lock_age = max(0, CACHE_POPULATION_LOCK_TTL_SECONDS - lock_ttl_seconds)

        if lock_age is not None and lock_age >= CACHE_POPULATION_STALE_AFTER_SECONDS:
            stale_reason = f"lock_age:{lock_age}s"

    if stale_reason is None:
        return False

    if _delete_lock_if_unchanged(existing_lock_value):
        logger.warning(
            "Recovered stale optimized cache lock (reason=%s, owner_task_id=%s, ttl=%s)",
            stale_reason,
            owner_task_id,
            lock_ttl_seconds,
        )
        return True

    return False


def _acquire_population_lock(task_id: str) -> Tuple[bool, Optional[str]]:
    """Acquire a distributed lock so only one optimized run executes at once."""
    client = get_redis_client()
    if client is None:
        logger.warning("Redis unavailable; proceeding without optimized task lock")
        return True, None

    token_payload = {
        "task_id": task_id,
        "token": str(uuid.uuid4()),
        "acquired_at": int(time.time()),
    }
    token = json.dumps(token_payload, separators=(",", ":"))
    acquired = client.set(
        CACHE_POPULATION_LOCK_KEY,
        token,
        nx=True,
        ex=CACHE_POPULATION_LOCK_TTL_SECONDS,
    )
    if acquired:
        return True, token

    existing = client.get(CACHE_POPULATION_LOCK_KEY)
    if existing and _try_recover_stale_lock(existing):
        reacquired = client.set(
            CACHE_POPULATION_LOCK_KEY,
            token,
            nx=True,
            ex=CACHE_POPULATION_LOCK_TTL_SECONDS,
        )
        if reacquired:
            logger.warning("Re-acquired optimized cache lock after stale-lock recovery")
            return True, token
        existing = client.get(CACHE_POPULATION_LOCK_KEY)

    existing_payload = _parse_lock_payload(existing)
    existing_ttl = client.ttl(CACHE_POPULATION_LOCK_KEY)
    logger.warning(
        "Optimized cache run already in progress (owner_task_id=%s, ttl=%s, task_id=%s); skipping duplicate trigger",
        existing_payload.get("task_id"),
        existing_ttl,
        task_id,
    )
    return False, None


def _release_population_lock(lock_token: Optional[str]) -> None:
    """Release the distributed lock only if this task still owns it."""
    if lock_token is None:
        return

    client = get_redis_client()
    if client is None:
        return

    try:
        client.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """,
            1,
            CACHE_POPULATION_LOCK_KEY,
            lock_token,
        )
    except Exception as exc:
        logger.warning("Failed to release optimized task lock: %s", exc)


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
            "cached_routes": 0,
            "failed": 0,
            "weather_buckets": len(weather_cache),
            "weather_fetch_time": round(weather_elapsed, 1),
        }

        location_ids = list(locations.keys())
        all_route_scores = {} if save_to_historical else None

        compute_start = time.time()
        for batch_start in range(0, total_locations, LOCATION_BATCH_SIZE):
            batch_ids = location_ids[batch_start:batch_start + LOCATION_BATCH_SIZE]
            batch_locations = {loc_id: locations[loc_id] for loc_id in batch_ids}

            # Process batch with pre-fetched weather and accident weather patterns
            batch_scores = await _process_location_batch(
                batch_locations, accidents, target_date, weather_cache,
                accident_weather_patterns, accident_arrays
            )

            stats["locations_processed"] += len(batch_ids)
            stats["routes_computed"] += len(batch_scores)

            # Cache incrementally per batch to avoid all-or-nothing loss on late failures
            if batch_scores:
                cached_count = set_bulk_cached_safety_scores(batch_scores, date_str)
                if cached_count != len(batch_scores):
                    raise RuntimeError(
                        f"Batch cache write incomplete for {date_str}: "
                        f"{cached_count}/{len(batch_scores)} routes cached"
                    )
                stats["cached_routes"] += cached_count

            # Only keep full in-memory score map when historical save is needed (today)
            if save_to_historical and all_route_scores is not None:
                all_route_scores.update(batch_scores)

            # Progress logging
            if stats["locations_processed"] % LOG_INTERVAL == 0 or stats["locations_processed"] == total_locations:
                compute_elapsed = time.time() - compute_start
                rate = stats["locations_processed"] / compute_elapsed if compute_elapsed > 0 else 0
                pct = (stats["locations_processed"] / total_locations) * 100
                logger.info(
                        f"  Progress: {stats['locations_processed']:,}/{total_locations:,} locations "
                        f"({pct:.1f}%, {rate:.0f} loc/sec)"
                )

        if stats["routes_computed"] != total_routes:
            raise RuntimeError(
                f"Computed routes mismatch for {date_str}: "
                f"{stats['routes_computed']}/{total_routes}"
            )

        if stats["cached_routes"] != total_routes:
            raise RuntimeError(
                f"Cached routes mismatch for {date_str}: "
                f"{stats['cached_routes']}/{total_routes}"
            )

        # Save to historical if requested
        if save_to_historical and all_route_scores:
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
            # Ensure table exists (idempotent, safe to run)
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS historical_predictions (
                    id SERIAL PRIMARY KEY,
                    route_id INTEGER NOT NULL,
                    prediction_date DATE NOT NULL,
                    risk_score FLOAT,
                    color_code VARCHAR(20),
                    calculated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(route_id, prediction_date)
                )
            """))
            await db.commit()

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

    logger.warning("=" * 60)
    logger.warning("STARTING OPTIMIZED SAFETY SCORE COMPUTATION")
    logger.warning(f"Python: {sys.version}")
    logger.warning(f"Settings: LOCATION_BATCH_SIZE={LOCATION_BATCH_SIZE}")
    logger.warning("=" * 60)

    task_id = getattr(compute_daily_safety_scores_optimized.request, "id", "unknown")
    lock_acquired, lock_token = _acquire_population_lock(task_id)
    if not lock_acquired:
        return {
            "status": "skipped",
            "reason": "optimized_cache_population_already_running",
            "task_id": task_id,
        }

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
    finally:
        _release_population_lock(lock_token)


async def _compute_all_dates_async() -> Dict:
    """Compute safety scores for all dates."""
    logger.warning("_compute_all_dates_async() STARTED")
    today = date.today()
    logger.warning(f"Today: {today}, DAYS_TO_COMPUTE: {DAYS_TO_COMPUTE}")
    dates_to_compute = [today + timedelta(days=i) for i in range(DAYS_TO_COMPUTE)]

    all_stats = {
        "dates_computed": len(dates_to_compute),
        "dates_succeeded": 0,
        "total_routes": 0,
        "total_time": 0,
        "failed_dates": [],
        "retry_attempts": {},
    }

    for target_date in dates_to_compute:
        date_str = target_date.isoformat()
        is_today = (target_date == today)
        logger.info(f"Computing for {date_str}{' (saving historical)' if is_today else ''}")

        date_success = False
        for attempt in range(1, DATE_RETRY_ATTEMPTS + 1):
            all_stats["retry_attempts"][date_str] = attempt
            try:
                stats = await compute_safety_scores_optimized(
                    target_date=target_date,
                    save_to_historical=is_today,
                )
                all_stats["total_routes"] = stats.get("total_routes", 0)
                all_stats["total_time"] += stats.get("elapsed_seconds", 0)
                all_stats["dates_succeeded"] += 1
                date_success = True
                break
            except Exception as exc:
                logger.error(
                    f"Failed computing {date_str} (attempt {attempt}/{DATE_RETRY_ATTEMPTS}): {exc}",
                    exc_info=True,
                )
                if attempt < DATE_RETRY_ATTEMPTS:
                    delay_seconds = DATE_RETRY_BACKOFF_SECONDS * attempt
                    logger.warning(f"Retrying {date_str} in {delay_seconds}s")
                    await asyncio.sleep(delay_seconds)
                else:
                    all_stats["failed_dates"].append({
                        "date": date_str,
                        "error": str(exc),
                    })

        if not date_success:
            logger.error(f"All retry attempts exhausted for {date_str}")

    all_stats["total_time"] = round(all_stats["total_time"], 1)
    if all_stats["failed_dates"]:
        all_stats["status"] = "failed"
        raise RuntimeError(
            f"Failed dates during optimized cache computation: "
            f"{', '.join(item['date'] for item in all_stats['failed_dates'])}"
        )

    all_stats["status"] = "completed"
    return all_stats
