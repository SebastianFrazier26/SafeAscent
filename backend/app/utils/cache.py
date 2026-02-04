"""
Redis Cache Service

Provides caching functionality for expensive operations:
- Weather API calls (Open-Meteo)
- Database queries (weather statistics)
- Prediction results (future)

Uses Redis for fast in-memory storage with automatic expiration (TTL).
Fails gracefully if Redis is unavailable (returns None, logs warning).
"""
import redis
import json
import logging
from typing import Optional, Any

# Configure logging
logger = logging.getLogger(__name__)

# Redis connection (lazy initialization)
_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> Optional[redis.Redis]:
    """
    Get Redis client connection (singleton pattern).

    Returns:
        Redis client if connection successful, None if Redis unavailable
    """
    global _redis_client

    if _redis_client is None:
        try:
            _redis_client = redis.Redis(
                host='localhost',
                port=6379,
                db=0,
                decode_responses=True,  # Return strings instead of bytes
                socket_connect_timeout=2,  # Fail fast if Redis is down
                socket_timeout=2,
            )
            # Test connection
            _redis_client.ping()
            logger.info("Redis connection established successfully")
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
    season: str
) -> str:
    """
    Build cache key for weather statistics.

    Args:
        latitude: Latitude (will be rounded to 1 decimal for bucket matching)
        longitude: Longitude (will be rounded to 1 decimal)
        elevation_meters: Elevation in meters (will be rounded to nearest 100m)
        season: Season name (winter, spring, summer, fall)

    Returns:
        Cache key string
    """
    lat = round(latitude, 1)
    lon = round(longitude, 1)
    elev = round(elevation_meters / 100) * 100  # Round to nearest 100m
    return f"weather:stats:{lat}:{lon}:{elev}:{season}"


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
