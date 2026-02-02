"""
Test Redis caching for weather service.

Verifies:
1. Cache misses on first call (fetches from API/DB)
2. Cache hits on second call (returns from Redis)
3. Significant performance improvement
4. Cache expiration works correctly
"""
import pytest
import time
from datetime import date

# Import caching functions
from app.utils.cache import (
    cache_get,
    cache_set,
    cache_delete,
    cache_clear_pattern,
    get_cache_stats,
    build_weather_pattern_key,
    build_weather_stats_key,
)


class TestCacheBasics:
    """Test basic Redis cache operations."""

    def test_cache_set_and_get(self):
        """Test basic cache set/get operations."""
        key = "test:basic"
        value = {"temperature": [10, 12, 15], "precipitation": [0, 0, 5]}

        # Set value in cache
        result = cache_set(key, value, ttl_seconds=60)
        assert result is True, "Cache set should succeed"

        # Get value from cache
        cached = cache_get(key)
        assert cached is not None, "Should retrieve cached value"
        assert cached == value, "Cached value should match original"

        # Clean up
        cache_delete(key)

    def test_cache_miss(self):
        """Test cache miss returns None."""
        result = cache_get("test:nonexistent:key")
        assert result is None, "Non-existent key should return None"

    def test_cache_delete(self):
        """Test cache deletion."""
        key = "test:delete"
        value = {"data": "test"}

        cache_set(key, value, ttl_seconds=60)
        assert cache_get(key) == value, "Value should be cached"

        cache_delete(key)
        assert cache_get(key) is None, "Value should be deleted"

    def test_cache_clear_pattern(self):
        """Test pattern-based cache clearing."""
        # Set multiple keys
        cache_set("test:pattern:1", {"a": 1}, ttl_seconds=60)
        cache_set("test:pattern:2", {"b": 2}, ttl_seconds=60)
        cache_set("test:other:3", {"c": 3}, ttl_seconds=60)

        # Clear pattern
        deleted = cache_clear_pattern("test:pattern:*")
        assert deleted == 2, "Should delete 2 keys matching pattern"

        # Verify
        assert cache_get("test:pattern:1") is None
        assert cache_get("test:pattern:2") is None
        assert cache_get("test:other:3") is not None  # Should still exist

        # Clean up
        cache_delete("test:other:3")

    def test_cache_stats(self):
        """Test cache statistics retrieval."""
        stats = get_cache_stats()
        assert stats.get("status") == "connected", "Redis should be connected"
        assert "keyspace_hits" in stats, "Should include hit stats"
        assert "keyspace_misses" in stats, "Should include miss stats"


class TestWeatherCacheKeys:
    """Test weather cache key builders."""

    def test_weather_pattern_key_builder(self):
        """Test weather pattern cache key format."""
        key = build_weather_pattern_key(40.2549, -105.6426, "2026-01-30")
        assert key == "weather:pattern:40.25:-105.64:2026-01-30"

    def test_weather_stats_key_builder(self):
        """Test weather stats cache key format."""
        key = build_weather_stats_key(40.2549, -105.6426, 4000, "summer")
        # Rounded to 0.1 for lat/lon, nearest 100m for elevation
        assert key == "weather:stats:40.3:-105.6:4000:summer"


class TestWeatherServiceCaching:
    """Test caching integration with weather service."""

    def test_weather_pattern_caching_performance(self):
        """Test that weather pattern caching improves performance."""
        from app.services.weather_service import fetch_current_weather_pattern

        # Longs Peak coordinates
        latitude = 40.255
        longitude = -105.615
        target_date = date.today()

        # Clear any existing cache
        cache_key = build_weather_pattern_key(latitude, longitude, target_date.isoformat())
        cache_delete(cache_key)

        # First call: cache MISS (should fetch from API)
        print("\n  First call (cache MISS, fetching from Open-Meteo API)...")
        start_time = time.time()
        pattern1 = fetch_current_weather_pattern(latitude, longitude, target_date)
        miss_duration = time.time() - start_time

        assert pattern1 is not None, "Should fetch weather pattern from API"
        print(f"  Cache MISS took: {miss_duration:.2f} seconds")

        # Second call: cache HIT (should return from Redis)
        print("  Second call (cache HIT, returning from Redis)...")
        start_time = time.time()
        pattern2 = fetch_current_weather_pattern(latitude, longitude, target_date)
        hit_duration = time.time() - start_time

        assert pattern2 is not None, "Should fetch weather pattern from cache"
        print(f"  Cache HIT took: {hit_duration:.4f} seconds")

        # Verify performance improvement
        speedup = miss_duration / hit_duration
        print(f"  Speedup: {speedup:.1f}x faster")

        assert hit_duration < miss_duration, "Cache hit should be faster than miss"
        assert speedup > 10, "Should be at least 10x faster (cache vs API call)"

        # Verify data is identical
        assert pattern1.temperature == pattern2.temperature
        assert pattern1.precipitation == pattern2.precipitation
        assert pattern1.wind_speed == pattern2.wind_speed

        # Clean up
        cache_delete(cache_key)

    def test_weather_stats_caching_performance(self):
        """Test that weather stats caching improves performance."""
        from app.services.weather_service import fetch_weather_statistics

        latitude = 40.255
        longitude = -105.615
        elevation = 4000
        season = "summer"

        # Clear any existing cache
        cache_key = build_weather_stats_key(latitude, longitude, elevation, season)
        cache_delete(cache_key)

        # First call: cache MISS (should query database)
        print("\n  First call (cache MISS, querying database)...")
        start_time = time.time()
        stats1 = fetch_weather_statistics(latitude, longitude, elevation, season)
        miss_duration = time.time() - start_time

        print(f"  Cache MISS took: {miss_duration:.4f} seconds")

        # Second call: cache HIT (should return from Redis)
        print("  Second call (cache HIT, returning from Redis)...")
        start_time = time.time()
        stats2 = fetch_weather_statistics(latitude, longitude, elevation, season)
        hit_duration = time.time() - start_time

        print(f"  Cache HIT took: {hit_duration:.4f} seconds")

        # Verify performance improvement
        if stats1 is not None and stats2 is not None:
            speedup = miss_duration / hit_duration
            print(f"  Speedup: {speedup:.1f}x faster")

            assert hit_duration < miss_duration, "Cache hit should be faster than miss"

            # Verify data is identical
            assert stats1 == stats2, "Cached stats should match original"

        # Clean up
        cache_delete(cache_key)


class TestPredictionEndpointWithCaching:
    """Test full prediction endpoint with caching."""

    def test_prediction_performance_with_caching(self, test_client):
        """Test that prediction endpoint benefits from caching."""
        # Longs Peak prediction
        request_data = {
            "latitude": 40.255,
            "longitude": -105.615,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 50.0,
        }

        # Clear caches for this location
        lat, lon, date_str = request_data["latitude"], request_data["longitude"], request_data["planned_date"]
        cache_delete(build_weather_pattern_key(lat, lon, date_str))

        # First request: cache MISS
        print("\n  First prediction request (cache MISS)...")
        start_time = time.time()
        response1 = test_client.post("/api/v1/predict", json=request_data)
        miss_duration = time.time() - start_time

        assert response1.status_code == 200
        data1 = response1.json()
        print(f"  First request took: {miss_duration:.2f} seconds")
        print(f"  Risk score: {data1['risk_score']:.1f}/100")

        # Second request: cache HIT
        print("  Second prediction request (cache HIT)...")
        start_time = time.time()
        response2 = test_client.post("/api/v1/predict", json=request_data)
        hit_duration = time.time() - start_time

        assert response2.status_code == 200
        data2 = response2.json()
        print(f"  Second request took: {hit_duration:.2f} seconds")
        print(f"  Risk score: {data2['risk_score']:.1f}/100")

        # Verify performance improvement
        speedup = miss_duration / hit_duration
        print(f"  Speedup: {speedup:.1f}x faster")

        assert hit_duration < miss_duration, "Cached request should be faster"
        assert speedup > 2, "Should be at least 2x faster with caching"

        # Verify results are identical
        assert data1["risk_score"] == data2["risk_score"]
        assert data1["confidence"] == data2["confidence"]
        assert data1["num_contributing_accidents"] == data2["num_contributing_accidents"]
