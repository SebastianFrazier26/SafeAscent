"""
Redis Cache Service

Provides caching functionality for expensive operations:
- Weather API calls (Open-Meteo)
- Database queries (weather statistics)
- Pre-computed safety scores (nightly batch job)

Uses Redis for fast in-memory storage with automatic expiration (TTL).
Fails gracefully if Redis is unavailable (returns None, logs warning).
"""
import redis
import json
import logging
from typing import Optional, Any, Dict, List
from urllib.parse import urlparse

# Configure logging
logger = logging.getLogger(__name__)

# Redis connection (lazy initialization)
_redis_client: Optional[redis.Redis] = None

# Cache TTL constants
SAFETY_SCORE_TTL = 7 * 24 * 60 * 60  # 7 days in seconds (we compute 7 days ahead)
WEATHER_PATTERN_TTL = 6 * 60 * 60    # 6 hours for weather data
WEATHER_STATS_TTL = 24 * 60 * 60     # 24 hours for weather statistics


def get_redis_client() -> Optional[redis.Redis]:
    """
    Get Redis client connection (singleton pattern).
    Uses REDIS_URL from settings/environment for production compatibility.

    Returns:
        Redis client if connection successful, None if Redis unavailable
    """
    global _redis_client

    if _redis_client is None:
        try:
            # Import settings here to avoid circular imports
            from app.config import settings

            # Parse Redis URL (supports both localhost and Railway format)
            redis_url = settings.REDIS_URL
            parsed = urlparse(redis_url)

            _redis_client = redis.Redis(
                host=parsed.hostname or 'localhost',
                port=parsed.port or 6379,
                db=int(parsed.path.lstrip('/') or 0) if parsed.path else 0,
                password=parsed.password,
                decode_responses=True,  # Return strings instead of bytes
                socket_connect_timeout=2,  # Fail fast if Redis is down
                socket_timeout=2,
            )
            # Test connection
            _redis_client.ping()
            logger.info(f"Redis connection established: {parsed.hostname}:{parsed.port}")
        except (redis.ConnectionError, redis.TimeoutError) as e:
            logger.warning(f"Redis unavailable, caching disabled: {e}")
            _redis_client = None

    return _redis_client


def cache_get(key: str) -> Optional[Any]:
    """
    Get value from cache.

    Args:
        key: Cache key

    Returns:
        Cached value (deserialized from JSON), or None if not found or Redis unavailable

    Example:
        >>> value = cache_get("weather:40.25:-105.64:2026-01-30")
        >>> if value:
        ...     print("Cache hit!")
    """
    client = get_redis_client()
    if client is None:
        return None

    try:
        cached = client.get(key)
        if cached:
            logger.debug(f"Cache HIT: {key}")
            return json.loads(cached)
        else:
            logger.debug(f"Cache MISS: {key}")
            return None
    except (redis.RedisError, json.JSONDecodeError) as e:
        logger.error(f"Cache get error for key '{key}': {e}")
        return None


def cache_set(key: str, value: Any, ttl_seconds: int = 3600) -> bool:
    """
    Set value in cache with expiration.

    Args:
        key: Cache key
        value: Value to cache (will be JSON serialized)
        ttl_seconds: Time to live in seconds (default: 1 hour)

    Returns:
        True if cached successfully, False if Redis unavailable or error

    Example:
        >>> weather_data = {"temperature": [10, 12, 15], "precipitation": [0, 0, 5]}
        >>> cache_set("weather:40.25:-105.64:2026-01-30", weather_data, ttl_seconds=21600)
        True
    """
    client = get_redis_client()
    if client is None:
        return False

    try:
        serialized = json.dumps(value)
        client.setex(key, ttl_seconds, serialized)
        logger.debug(f"Cache SET: {key} (TTL: {ttl_seconds}s)")
        return True
    except (redis.RedisError, TypeError, ValueError) as e:
        logger.error(f"Cache set error for key '{key}': {e}")
        return False


def cache_delete(key: str) -> bool:
    """
    Delete value from cache.

    Args:
        key: Cache key to delete

    Returns:
        True if deleted, False if not found or Redis unavailable
    """
    client = get_redis_client()
    if client is None:
        return False

    try:
        result = client.delete(key)
        if result:
            logger.debug(f"Cache DELETE: {key}")
        return bool(result)
    except redis.RedisError as e:
        logger.error(f"Cache delete error for key '{key}': {e}")
        return False


def cache_clear_pattern(pattern: str) -> int:
    """
    Delete all keys matching a pattern.

    Args:
        pattern: Redis key pattern (e.g., "weather:*")

    Returns:
        Number of keys deleted, or 0 if Redis unavailable

    Example:
        >>> # Clear all weather caches
        >>> deleted = cache_clear_pattern("weather:*")
        >>> print(f"Cleared {deleted} weather caches")
    """
    client = get_redis_client()
    if client is None:
        return 0

    try:
        keys = client.keys(pattern)
        if keys:
            deleted = client.delete(*keys)
            logger.info(f"Cache CLEAR: {deleted} keys matching '{pattern}'")
            return deleted
        return 0
    except redis.RedisError as e:
        logger.error(f"Cache clear pattern error for '{pattern}': {e}")
        return 0


def get_cache_stats() -> dict:
    """
    Get Redis cache statistics.

    Returns:
        Dictionary with cache stats, or empty dict if Redis unavailable

    Example:
        >>> stats = get_cache_stats()
        >>> print(f"Memory used: {stats.get('used_memory_human', 'N/A')}")
        Memory used: 2.5M
    """
    client = get_redis_client()
    if client is None:
        return {"status": "unavailable"}

    try:
        info = client.info("stats")
        memory = client.info("memory")
        return {
            "status": "connected",
            "total_connections_received": info.get("total_connections_received"),
            "total_commands_processed": info.get("total_commands_processed"),
            "keyspace_hits": info.get("keyspace_hits", 0),
            "keyspace_misses": info.get("keyspace_misses", 0),
            "used_memory_human": memory.get("used_memory_human"),
            "used_memory_peak_human": memory.get("used_memory_peak_human"),
        }
    except redis.RedisError as e:
        logger.error(f"Cache stats error: {e}")
        return {"status": "error", "error": str(e)}


# Cache key builders for common use cases

def build_weather_pattern_key(latitude: float, longitude: float, target_date: str) -> str:
    """
    Build cache key for weather pattern.

    Args:
        latitude: Latitude (will be rounded to 2 decimals for ~1km precision)
        longitude: Longitude (will be rounded to 2 decimals)
        target_date: Date string (ISO format: YYYY-MM-DD)

    Returns:
        Cache key string
    """
    lat = round(latitude, 2)
    lon = round(longitude, 2)
    return f"weather:pattern:{lat}:{lon}:{target_date}"


def build_weather_stats_key(
    latitude: float,
    longitude: float,
    elevation_meters: float,
    season: str,
    reference_month: Optional[int] = None,
) -> str:
    """
    Build cache key for weather statistics.

    Args:
        latitude: Latitude (will be rounded to 1 decimal for bucket matching)
        longitude: Longitude (will be rounded to 1 decimal)
        elevation_meters: Elevation in meters (will be rounded to nearest 100m)
        season: Season name (winter, spring, summer, fall)
        reference_month: Optional reference month (1-12) for cyclical temporal weighting

    Returns:
        Cache key string
    """
    lat = round(latitude, 1)
    lon = round(longitude, 1)
    elev = round(elevation_meters / 100) * 100  # Round to nearest 100m
    if reference_month is None:
        return f"weather:stats:{lat}:{lon}:{elev}:{season}"
    return f"weather:stats:{lat}:{lon}:{elev}:{season}:m{int(reference_month)}"


def build_safety_score_key(route_id: int, target_date: str) -> str:
    """
    Build cache key for route safety score.

    Args:
        route_id: Route ID from database
        target_date: Date string (ISO format: YYYY-MM-DD)

    Returns:
        Cache key string

    Example:
        >>> key = build_safety_score_key(1234, "2026-02-01")
        >>> print(key)
        safety:route:1234:date:2026-02-01
    """
    return f"safety:route:{route_id}:date:{target_date}"


# ============================================================================
# BULK SAFETY SCORE OPERATIONS
# ============================================================================
# These functions are optimized for the pre-computed safety score system.
# Instead of making 168K individual Redis calls, we use MGET/pipeline for
# bulk operations which reduces network round trips significantly.


def get_cached_safety_score(route_id: int, target_date: str) -> Optional[Dict]:
    """
    Get a single cached safety score.

    Args:
        route_id: Route ID
        target_date: Date string (YYYY-MM-DD)

    Returns:
        Safety score dict with risk_score, color_code, confidence, or None if not cached
    """
    key = build_safety_score_key(route_id, target_date)
    return cache_get(key)


def set_cached_safety_score(
    route_id: int,
    target_date: str,
    risk_score: float,
    color_code: str,
    confidence: float = 1.0,
    computed_at: Optional[str] = None
) -> bool:
    """
    Cache a single safety score.

    Args:
        route_id: Route ID
        target_date: Date string (YYYY-MM-DD)
        risk_score: Risk score 0-100
        color_code: 'green', 'yellow', 'orange', 'red', or 'gray'
        confidence: Confidence level 0-1 (default: 1.0)
        computed_at: ISO timestamp when computed (default: now)

    Returns:
        True if cached successfully
    """
    from datetime import datetime

    key = build_safety_score_key(route_id, target_date)
    data = {
        "risk_score": risk_score,
        "color_code": color_code,
        "confidence": confidence,
        "computed_at": computed_at or datetime.utcnow().isoformat(),
        "status": "cached"
    }
    return cache_set(key, data, ttl_seconds=SAFETY_SCORE_TTL)


def get_bulk_cached_safety_scores(
    route_ids: List[int],
    target_date: str
) -> Dict[int, Optional[Dict]]:
    """
    Get multiple safety scores in a single Redis call using MGET.

    This is much faster than individual cache_get calls:
    - Individual: ~168K round trips = ~30 seconds
    - Bulk MGET: 1 round trip = ~1-2 seconds

    Args:
        route_ids: List of route IDs
        target_date: Date string (YYYY-MM-DD)

    Returns:
        Dict mapping route_id -> safety data (or None if not cached)
        Example: {123: {"risk_score": 45, "color_code": "yellow"}, 456: None}
    """
    client = get_redis_client()
    if client is None or not route_ids:
        return {route_id: None for route_id in route_ids}

    try:
        # Build all cache keys
        keys = [build_safety_score_key(route_id, target_date) for route_id in route_ids]

        # Bulk get using MGET
        values = client.mget(keys)

        # Map results back to route IDs
        result = {}
        for route_id, cached_value in zip(route_ids, values):
            if cached_value:
                try:
                    result[route_id] = json.loads(cached_value)
                except json.JSONDecodeError:
                    result[route_id] = None
            else:
                result[route_id] = None

        hit_count = sum(1 for v in result.values() if v is not None)
        logger.debug(f"Bulk cache GET: {hit_count}/{len(route_ids)} hits for {target_date}")
        return result

    except redis.RedisError as e:
        logger.error(f"Bulk cache get error: {e}")
        return {route_id: None for route_id in route_ids}


def set_bulk_cached_safety_scores(
    scores: Dict[int, Dict],
    target_date: str
) -> int:
    """
    Cache multiple safety scores using Redis pipeline.

    Pipeline batches multiple SET commands into a single network round trip.

    Args:
        scores: Dict mapping route_id -> safety data
                Each safety data should have: risk_score, color_code, confidence
        target_date: Date string (YYYY-MM-DD)

    Returns:
        Number of scores successfully cached
    """
    client = get_redis_client()
    if client is None or not scores:
        return 0

    try:
        from datetime import datetime
        computed_at = datetime.utcnow().isoformat()

        # Use pipeline for efficient bulk SET
        pipe = client.pipeline()

        for route_id, data in scores.items():
            key = build_safety_score_key(route_id, target_date)
            cache_data = {
                "risk_score": data.get("risk_score", 0),
                "color_code": data.get("color_code", "gray"),
                "confidence": data.get("confidence", 1.0),
                "computed_at": computed_at,
                "status": "cached"
            }
            pipe.setex(key, SAFETY_SCORE_TTL, json.dumps(cache_data))

        # Execute all commands in one round trip
        pipe.execute()

        logger.info(f"Bulk cache SET: {len(scores)} safety scores for {target_date}")
        return len(scores)

    except redis.RedisError as e:
        logger.error(f"Bulk cache set error: {e}")
        return 0


def get_safety_cache_stats(target_date: str) -> Dict:
    """
    Get statistics about cached safety scores for a specific date.

    Useful for monitoring the nightly pre-computation job.

    Args:
        target_date: Date string (YYYY-MM-DD)

    Returns:
        Dict with count of cached scores for this date
    """
    client = get_redis_client()
    if client is None:
        return {"status": "unavailable", "cached_count": 0}

    try:
        # Count keys matching the pattern for this date
        pattern = f"safety:route:*:date:{target_date}"
        keys = client.keys(pattern)
        return {
            "status": "connected",
            "target_date": target_date,
            "cached_count": len(keys)
        }
    except redis.RedisError as e:
        logger.error(f"Safety cache stats error: {e}")
        return {"status": "error", "error": str(e), "cached_count": 0}
