"""
Weather Service - Real-Time Weather Fetching

Fetches current and forecast weather from Open-Meteo API.
Builds WeatherPattern objects for the safety algorithm.

API: https://open-meteo.com/
- Free, no API key required
- 10,000 requests/day limit
- Historical + Forecast data

Caching (Phase 8):
- Weather patterns cached for 6 hours (forecasts change slowly)
- Weather statistics cached for 24 hours (historical data is static)
- Uses Redis for fast in-memory storage
"""
import requests
from datetime import date, timedelta
from typing import Optional, Tuple, List, Dict, Any
import logging

from app.services.weather_similarity import WeatherPattern
from app.utils.cache import (
    cache_get,
    cache_set,
    build_weather_pattern_key,
    build_weather_stats_key,
)

# Open-Meteo API endpoint
WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"

# Configure logging
logger = logging.getLogger(__name__)

# Cache TTLs (Time To Live)
WEATHER_PATTERN_TTL = 6 * 3600  # 6 hours (forecasts change slowly)
WEATHER_STATS_TTL = 24 * 3600  # 24 hours (historical data is static)


def _weather_pattern_to_dict(pattern: WeatherPattern) -> Dict[str, Any]:
    """
    Serialize WeatherPattern to dict for JSON caching.

    Args:
        pattern: WeatherPattern object

    Returns:
        Dictionary representation
    """
    return {
        "temperature": pattern.temperature,
        "precipitation": pattern.precipitation,
        "wind_speed": pattern.wind_speed,
        "visibility": pattern.visibility,
        "cloud_cover": pattern.cloud_cover,
        "daily_temps": pattern.daily_temps,  # List of tuples serializes fine
    }


def _dict_to_weather_pattern(data: Dict[str, Any]) -> WeatherPattern:
    """
    Deserialize dict back to WeatherPattern object.

    Args:
        data: Dictionary from cache

    Returns:
        WeatherPattern object
    """
    return WeatherPattern(
        temperature=data["temperature"],
        precipitation=data["precipitation"],
        wind_speed=data["wind_speed"],
        visibility=data["visibility"],
        cloud_cover=data["cloud_cover"],
        daily_temps=[tuple(t) for t in data["daily_temps"]],  # Convert lists back to tuples
    )


def fetch_current_weather_pattern(
    latitude: float,
    longitude: float,
    target_date: date,
) -> Optional[WeatherPattern]:
    """
    Fetch 7-day weather pattern centered on target date (CACHED).

    Uses Open-Meteo API to get historical + forecast weather.
    Builds WeatherPattern object compatible with safety algorithm.

    **Caching**: Results cached for 6 hours to reduce API calls.
    Cache key: weather:pattern:{lat}:{lon}:{date}

    Args:
        latitude: Location latitude (degrees)
        longitude: Location longitude (degrees)
        target_date: Center date for weather pattern (typically today)

    Returns:
        WeatherPattern object with 7 days of data, or None if fetch fails

    Example:
        >>> from datetime import date
        >>> pattern = fetch_current_weather_pattern(40.2549, -105.6426, date.today())
        >>> print(f"Temperature range: {min(pattern.temperature)}-{max(pattern.temperature)}°C")
        Temperature range: 8-15°C
    """
    # Check cache first
    cache_key = build_weather_pattern_key(latitude, longitude, target_date.isoformat())
    cached = cache_get(cache_key)
    if cached:
        logger.info(f"Weather pattern cache HIT for {latitude}, {longitude}, {target_date}")
        return _dict_to_weather_pattern(cached)

    logger.info(f"Weather pattern cache MISS, fetching from Open-Meteo API")

    try:
        # Calculate date range (6 days before target, up to target)
        start_date = target_date - timedelta(days=6)
        end_date = target_date

        # API parameters
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": [
                "temperature_2m_mean",
                "temperature_2m_min",
                "temperature_2m_max",
                "precipitation_sum",
                "wind_speed_10m_max",
                "cloud_cover_mean",
            ],
            "temperature_unit": "celsius",
            "wind_speed_unit": "ms",
            "precipitation_unit": "mm",
            "timezone": "auto",
        }

        # Make API request
        response = requests.get(WEATHER_API_URL, params=params, timeout=10)
        response.raise_for_status()

        # Parse response
        data = response.json()
        daily = data["daily"]

        # Extract weather factors
        temperature = daily["temperature_2m_mean"]
        precipitation = daily["precipitation_sum"]
        wind_speed = daily["wind_speed_10m_max"]
        cloud_cover = daily["cloud_cover_mean"]

        # Open-Meteo doesn't provide visibility, use default
        visibility = [10000.0] * len(temperature)  # Default 10km visibility

        # Build daily temps tuples for freeze-thaw calculation
        daily_temps = [
            (
                daily["temperature_2m_min"][i],
                daily["temperature_2m_mean"][i],
                daily["temperature_2m_max"][i],
            )
            for i in range(len(temperature))
        ]

        # Create WeatherPattern
        pattern = WeatherPattern(
            temperature=temperature,
            precipitation=precipitation,
            wind_speed=wind_speed,
            visibility=visibility,
            cloud_cover=cloud_cover,
            daily_temps=daily_temps,
        )

        # Cache the result
        pattern_dict = _weather_pattern_to_dict(pattern)
        cache_set(cache_key, pattern_dict, ttl_seconds=WEATHER_PATTERN_TTL)
        logger.info(f"Weather pattern cached for 6 hours")

        return pattern

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch weather from Open-Meteo API: {e}")
        return None
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"Failed to parse weather API response: {e}")
        return None


def fetch_weather_statistics(
    latitude: float,
    longitude: float,
    elevation_meters: float,
    season: str,
) -> Optional[dict]:
    """
    Fetch historical weather statistics for a location/elevation/season (CACHED).

    Queries the weather_statistics table computed in Phase 6.

    **Caching**: Results cached for 24 hours (historical data is static).
    Cache key: weather:stats:{lat}:{lon}:{elevation}:{season}

    Args:
        latitude: Location latitude
        longitude: Location longitude
        elevation_meters: Elevation in meters
        season: Season name (winter, spring, summer, fall)

    Returns:
        Dictionary with statistical means/stds, or None if not found

    Example:
        >>> stats = fetch_weather_statistics(40.25, -105.64, 4000, "summer")
        >>> print(f"Mean temp: {stats['temperature_mean']:.1f}°C")
        Mean temp: 12.8°C
    """
    # Check cache first
    cache_key = build_weather_stats_key(latitude, longitude, elevation_meters, season)
    cached = cache_get(cache_key)
    if cached:
        logger.info(f"Weather stats cache HIT for {latitude}, {longitude}, {elevation_meters}m, {season}")
        return cached

    logger.info(f"Weather stats cache MISS, querying database")

    # Round lat/lon to 0.1° bucket
    lat_bucket = round(latitude, 1)
    lon_bucket = round(longitude, 1)

    # Determine elevation band
    elevation_bands = [
        (0, 2438),
        (2438, 3048),
        (3048, 3658),
        (3658, 4267),
        (4267, 10000),
    ]

    elev_min, elev_max = None, None
    for band_min, band_max in elevation_bands:
        if band_min <= elevation_meters < band_max:
            elev_min, elev_max = band_min, band_max
            break

    if elev_min is None:
        logger.warning(f"Elevation {elevation_meters}m outside known bands")
        return None

    # Query database using psycopg2 (sync) for simple lookup
    try:
        import psycopg2
        import os
        from dotenv import load_dotenv

        load_dotenv()

        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            database=os.getenv("DB_NAME", "safeascent"),
            user=os.getenv("DB_USER", "sebastianfrazier"),
            password=os.getenv("DB_PASSWORD", ""),
        )
        cur = conn.cursor()

        cur.execute("""
            SELECT
                temperature_mean, temperature_std,
                precipitation_mean, precipitation_std,
                wind_speed_mean, wind_speed_std,
                visibility_mean, visibility_std,
                sample_count
            FROM weather_statistics
            WHERE lat_bucket = %s
              AND lon_bucket = %s
              AND elevation_min_m = %s
              AND elevation_max_m = %s
              AND season = %s
            LIMIT 1;
        """, (lat_bucket, lon_bucket, elev_min, elev_max, season))

        result = cur.fetchone()
        cur.close()
        conn.close()

        if result:
            stats_dict = {
                "temperature": (result[0], result[1]),
                "precipitation": (result[2], result[3]),
                "wind_speed": (result[4], result[5]),
                "visibility": (result[6], result[7]),
                "sample_count": result[8],
            }
            # Cache the result
            cache_set(cache_key, stats_dict, ttl_seconds=WEATHER_STATS_TTL)
            logger.info(f"Weather stats cached for 24 hours")
            return stats_dict
        else:
            logger.info(f"No statistics found for bucket: {lat_bucket}, {lon_bucket}, {elev_min}-{elev_max}m, {season}")
            return None

    except Exception as e:
        logger.error(f"Failed to fetch weather statistics: {e}")
        return None
