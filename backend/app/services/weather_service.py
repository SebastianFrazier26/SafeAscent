"""
Weather Service - Real-Time Weather Fetching

Fetches current and forecast weather from Open-Meteo API.
Builds WeatherPattern objects for the safety algorithm.

API: https://open-meteo.com/
- Paid plan: 1M requests/month, no rate limits
- Historical + Forecast data

Caching (Phase 8):
- Weather patterns cached for 6 hours (forecasts change slowly)
- Weather statistics cached for 24 hours (historical data is static)
- Uses Redis for fast in-memory storage
"""
import os
import requests
from datetime import date, timedelta
from math import exp, sqrt
from typing import Optional, Dict, Any, List, Tuple
import logging

from app.services.weather_similarity import WeatherPattern
from app.utils.cache import (
    cache_get,
    cache_set,
    build_weather_pattern_key,
    build_weather_stats_key,
)

# Open-Meteo API endpoint (commercial API if key provided, otherwise free)
OPEN_METEO_API_KEY = os.getenv("OPEN_METEO_API_KEY")
if OPEN_METEO_API_KEY:
    WEATHER_API_URL = "https://customer-api.open-meteo.com/v1/forecast"
    ARCHIVE_WEATHER_API_URL = "https://customer-archive-api.open-meteo.com/v1/archive"
    print(f"[WeatherService] Using COMMERCIAL Open-Meteo API (key configured: {OPEN_METEO_API_KEY[:8]}...)")
else:
    WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"
    ARCHIVE_WEATHER_API_URL = "https://archive-api.open-meteo.com/v1/archive"
    print("[WeatherService] WARNING: No API key - using FREE Open-Meteo API (rate limited!)")

# Configure logging
logger = logging.getLogger(__name__)

# Cache TTLs (Time To Live)
WEATHER_PATTERN_TTL = 6 * 3600  # 6 hours (forecasts change slowly)
WEATHER_STATS_TTL = 24 * 3600  # 24 hours (historical data is static)
WEATHER_STATS_LOOKBACK_YEARS = 5
WEATHER_STATS_LOOKBACK_DAYS = WEATHER_STATS_LOOKBACK_YEARS * 365
WEATHER_STATS_MONTH_DECAY = 2.0  # Lower = stronger emphasis on nearby months


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


def _month_distance(month_a: int, month_b: int) -> int:
    """Cyclical month distance (e.g., Jan vs Dec = 1)."""
    diff = abs(month_a - month_b)
    return min(diff, 12 - diff)


def _weighted_mean_and_std(values: List[float], weights: List[float]) -> Tuple[Optional[float], Optional[float]]:
    """Compute weighted mean/std with population-style variance."""
    if not values or not weights or len(values) != len(weights):
        return None, None

    total_weight = sum(weights)
    if total_weight <= 0:
        return None, None

    weighted_mean = sum(v * w for v, w in zip(values, weights)) / total_weight
    weighted_var = sum(w * ((v - weighted_mean) ** 2) for v, w in zip(values, weights)) / total_weight
    return weighted_mean, sqrt(max(weighted_var, 0.0))


def _fetch_archive_weather_daily(
    latitude: float,
    longitude: float,
    start_date: date,
    end_date: date,
) -> Optional[Dict[str, Any]]:
    """
    Fetch historical daily weather from Open-Meteo archive API.

    Tries commercial archive endpoint first when API key is configured.
    Falls back to public archive endpoint if commercial archive is unavailable.
    """
    base_params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": [
            "temperature_2m_mean",
            "precipitation_sum",
            "wind_speed_10m_max",
            "cloud_cover_mean",
        ],
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms",
        "precipitation_unit": "mm",
        "timezone": "auto",
    }

    archive_requests = []
    public_archive_url = "https://archive-api.open-meteo.com/v1/archive"

    if OPEN_METEO_API_KEY:
        archive_requests.append((ARCHIVE_WEATHER_API_URL, True))
        if ARCHIVE_WEATHER_API_URL != public_archive_url:
            archive_requests.append((public_archive_url, False))
    else:
        archive_requests.append((ARCHIVE_WEATHER_API_URL, False))

    for archive_url, include_api_key in archive_requests:
        request_params = dict(base_params)
        if include_api_key and OPEN_METEO_API_KEY:
            request_params["apikey"] = OPEN_METEO_API_KEY
        try:
            response = requests.get(archive_url, params=request_params, timeout=15)
            response.raise_for_status()
            payload = response.json()
            if "daily" in payload:
                return payload
            logger.warning(f"Archive weather payload missing daily data ({archive_url})")
        except requests.exceptions.RequestException as exc:
            logger.warning(f"Archive weather fetch failed ({archive_url}): {exc}")
        except ValueError as exc:
            logger.warning(f"Archive weather response parse failed ({archive_url}): {exc}")

    return None


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
        # DEBUG level - don't spam logs with every cache hit
        logger.debug(f"Weather pattern cache HIT for {latitude}, {longitude}, {target_date}")
        return _dict_to_weather_pattern(cached)

    # DEBUG level - cache misses are expected during pre-fetch, don't spam logs
    logger.debug("Weather pattern cache MISS, fetching from Open-Meteo API")

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

        # Add API key for commercial endpoint (removes rate limits)
        if OPEN_METEO_API_KEY:
            params["apikey"] = OPEN_METEO_API_KEY

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
        logger.info("Weather pattern cached for 6 hours")

        return pattern

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch weather from Open-Meteo API: {e}")
        return None
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"Failed to parse weather API response: {e}")
        return None


async def fetch_weather_statistics(
    latitude: float,
    longitude: float,
    elevation_meters: float,
    season: str,
    db=None,
    reference_date: Optional[date] = None,
) -> Optional[dict]:
    """
    Fetch weather statistics from Open-Meteo archive (CACHED).

    This replaces DB-table dependence for extreme weather detection by deriving
    statistics directly from multi-year daily weather at the route
    location. A cyclical month-based temporal decay is applied so months closer
    to the planned month contribute more to volatility estimates.

    If archive calls fail, returns None so extreme-weather amplification is
    skipped safely.

    Args:
        latitude: Location latitude
        longitude: Location longitude
        elevation_meters: Elevation in meters
        season: Season name (winter, spring, summer, fall)
        db: Unused; kept for backward compatibility
        reference_date: Date used for cyclical month weighting (defaults to today)

    Returns:
        Dictionary with weighted means/stds and volatility metadata, or None
        if unavailable.

    Example:
        >>> stats = await fetch_weather_statistics(40.25, -105.64, 4000, "summer")
        >>> print(f"Mean temp: {stats['temperature'][0]:.1f}°C")
        Mean temp: 11.3°C
    """
    if os.getenv("SKIP_WEATHER_STATISTICS", "false").lower() == "true":
        return None

    target_date = reference_date or date.today()
    reference_month = target_date.month

    cache_key = build_weather_stats_key(
        latitude=latitude,
        longitude=longitude,
        elevation_meters=elevation_meters,
        season=season,
        reference_month=reference_month,
    )
    cached = cache_get(cache_key)
    if cached:
        logger.debug(
            f"Weather stats cache HIT for {latitude}, {longitude}, {elevation_meters}m,"
            f" {season}, month={reference_month}"
        )
        return cached

    logger.debug(
        f"Weather stats cache MISS for {latitude}, {longitude}, {elevation_meters}m,"
        f" {season}, month={reference_month}"
    )

    try:
        window_end = target_date
        window_start = window_end - timedelta(days=WEATHER_STATS_LOOKBACK_DAYS - 1)
        archive_payload = _fetch_archive_weather_daily(
            latitude=latitude,
            longitude=longitude,
            start_date=window_start,
            end_date=window_end,
        )
        if archive_payload is None:
            logger.warning(
                f"No archive weather data for {latitude}, {longitude};"
                " skipping extreme-weather amplification."
            )
            return None

        daily = archive_payload.get("daily", {})
        time_values = daily.get("time", [])
        if not time_values:
            logger.warning("Archive weather response missing daily time series")
            return None

        months = []
        for day_str in time_values:
            try:
                months.append(int(str(day_str)[5:7]))
            except (TypeError, ValueError):
                months.append(reference_month)

        base_weights = [
            exp(-_month_distance(month, reference_month) / WEATHER_STATS_MONTH_DECAY)
            for month in months
        ]

        factors = {
            "temperature": daily.get("temperature_2m_mean", []),
            "precipitation": daily.get("precipitation_sum", []),
            "wind_speed": daily.get("wind_speed_10m_max", []),
        }

        weighted_stats: Dict[str, Tuple[float, float]] = {}
        volatility: Dict[str, float] = {}
        monthly_volatility: Dict[str, Dict[str, Dict[str, float]]] = {}
        minimum_points_required = 30

        for factor_name, series in factors.items():
            grouped: Dict[int, List[float]] = {month: [] for month in range(1, 13)}
            for value, month in zip(series, months):
                if value is None:
                    continue
                try:
                    grouped[month].append(float(value))
                except (TypeError, ValueError):
                    continue

            monthly_volatility[factor_name] = {}
            for month in range(1, 13):
                month_values = grouped[month]
                if len(month_values) < 2:
                    monthly_volatility[factor_name][str(month)] = {
                        "mean": None,
                        "std_dev": None,
                        "sample_count": len(month_values),
                    }
                    continue
                month_mean, month_std = _weighted_mean_and_std(month_values, [1.0] * len(month_values))
                monthly_volatility[factor_name][str(month)] = {
                    "mean": month_mean,
                    "std_dev": month_std,
                    "sample_count": len(month_values),
                }

        for factor_name, series in factors.items():
            values: List[float] = []
            weights: List[float] = []
            for value, weight in zip(series, base_weights):
                if value is None:
                    continue
                try:
                    values.append(float(value))
                    weights.append(float(weight))
                except (TypeError, ValueError):
                    continue

            if len(values) < minimum_points_required:
                logger.warning(
                    f"Insufficient archive samples for {factor_name}: {len(values)} "
                    f"(required {minimum_points_required})"
                )
                return None

            weighted_mean, weighted_std = _weighted_mean_and_std(values, weights)
            if weighted_mean is None or weighted_std is None:
                return None
            weighted_stats[factor_name] = (weighted_mean, weighted_std)
            volatility[factor_name] = weighted_std

        sample_count = min(len(factors["temperature"]), len(factors["precipitation"]), len(factors["wind_speed"]))
        stats_dict = {
            "temperature": weighted_stats["temperature"],
            "precipitation": weighted_stats["precipitation"],
            "wind_speed": weighted_stats["wind_speed"],
            "visibility": (10000.0, 0.0),  # Open-Meteo archive does not provide visibility
            "sample_count": sample_count,
            "source": "open_meteo_archive",
            "season": season,
            "reference_date": target_date.isoformat(),
            "reference_month": reference_month,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "lookback_years": WEATHER_STATS_LOOKBACK_YEARS,
            "lookback_days": WEATHER_STATS_LOOKBACK_DAYS,
            "temporal_decay": {
                "model": "cyclical_month_exponential",
                "decay_constant_months": WEATHER_STATS_MONTH_DECAY,
            },
            "volatility": volatility,
            "monthly_volatility": monthly_volatility,
        }

        cache_set(cache_key, stats_dict, ttl_seconds=WEATHER_STATS_TTL)
        logger.debug("Weather stats cached for 24 hours")
        return stats_dict
    except Exception as exc:
        logger.error(f"Failed to compute weather statistics from archive API: {exc}")
        return None
