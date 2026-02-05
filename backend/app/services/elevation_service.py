"""
Elevation Service - Fetch elevation from coordinates

Uses Open-Elevation API (free, no API key required) to get elevation data
from latitude/longitude coordinates.

API: https://open-elevation.com/
- Free and open source
- No rate limits or API keys
- Returns elevation in meters above sea level

Fallback: Returns None if API unavailable (algorithm uses neutral weight)

**OPTIMIZATION**: In-memory cache reduces API calls during batch processing.
"""
import requests
import logging
import time
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)

# Open-Elevation API endpoint
ELEVATION_API_URL = "https://api.open-elevation.com/api/v1/lookup"

# In-memory elevation cache: (lat_rounded, lon_rounded) -> (elevation, timestamp)
# Coordinates rounded to 3 decimal places (~111m precision)
_elevation_cache: Dict[Tuple[float, float], Tuple[Optional[float], float]] = {}
ELEVATION_CACHE_TTL = 3600  # 1 hour
ELEVATION_COORD_PRECISION = 3  # Decimal places for coordinate rounding


def fetch_elevation(latitude: float, longitude: float) -> Optional[float]:
    """
    Fetch elevation in meters for given coordinates.

    Uses Open-Elevation API to get accurate elevation data.
    Returns None if API fails (algorithm will use neutral weight).

    **OPTIMIZATION**: Results are cached in memory for 1 hour, keyed by
    coordinates rounded to 3 decimal places (~111m precision). This
    dramatically reduces API calls during batch processing.

    Args:
        latitude: Latitude in degrees
        longitude: Longitude in degrees

    Returns:
        Elevation in meters above sea level, or None if unavailable
    """
    global _elevation_cache

    # Round coordinates for cache key (3 decimals = ~111m precision)
    lat_key = round(latitude, ELEVATION_COORD_PRECISION)
    lon_key = round(longitude, ELEVATION_COORD_PRECISION)
    cache_key = (lat_key, lon_key)

    # Check cache
    current_time = time.time()
    if cache_key in _elevation_cache:
        cached_elevation, cached_time = _elevation_cache[cache_key]
        if (current_time - cached_time) < ELEVATION_CACHE_TTL:
            # Cache hit - don't log every hit to avoid spam
            return cached_elevation

    try:
        # API expects JSON with locations array
        payload = {
            "locations": [
                {"latitude": latitude, "longitude": longitude}
            ]
        }

        response = requests.post(
            ELEVATION_API_URL,
            json=payload,
            timeout=5  # 5 second timeout (API is usually fast)
        )
        response.raise_for_status()

        data = response.json()
        results = data.get("results", [])

        elevation = None
        if results and len(results) > 0:
            elevation = results[0].get("elevation")
            if elevation is not None:
                elevation = float(elevation)
                logger.debug(f"Fetched elevation for ({latitude}, {longitude}): {elevation}m")

        # Cache the result (even None to avoid repeated failed lookups)
        _elevation_cache[cache_key] = (elevation, current_time)

        return elevation

    except requests.exceptions.RequestException as e:
        logger.warning(f"Elevation API failed for ({latitude}, {longitude}): {e}")
        # Cache None to avoid hammering failed API
        _elevation_cache[cache_key] = (None, current_time)
        return None
    except (KeyError, IndexError, ValueError) as e:
        logger.warning(f"Failed to parse elevation response: {e}")
        _elevation_cache[cache_key] = (None, current_time)
        return None


def fetch_elevations_batch(coordinates: list[tuple[float, float]]) -> list[Optional[float]]:
    """
    Fetch elevations for multiple coordinates in a single API call.

    More efficient than calling fetch_elevation() repeatedly.
    Open-Elevation supports batch requests up to 100 locations.

    Args:
        coordinates: List of (latitude, longitude) tuples

    Returns:
        List of elevations (in same order as input), None for failed lookups

    Example:
        >>> coords = [(40.255, -105.615), (46.852, -121.760)]  # Longs Peak, Mt Rainier
        >>> elevations = fetch_elevations_batch(coords)
        >>> print(elevations)
        [4346.0, 4392.0]
    """
    if not coordinates:
        return []

    # Limit to 100 locations per request (API limit)
    if len(coordinates) > 100:
        logger.warning(f"Batch request for {len(coordinates)} locations exceeds API limit of 100, truncating")
        coordinates = coordinates[:100]

    try:
        # Build locations payload
        locations = [
            {"latitude": lat, "longitude": lon}
            for lat, lon in coordinates
        ]

        payload = {"locations": locations}

        response = requests.post(
            ELEVATION_API_URL,
            json=payload,
            timeout=10  # Longer timeout for batch requests
        )
        response.raise_for_status()

        data = response.json()
        results = data.get("results", [])

        # Extract elevations (in order)
        elevations = []
        for i, result in enumerate(results):
            elevation = result.get("elevation")
            if elevation is not None:
                elevations.append(float(elevation))
            else:
                logger.warning(f"No elevation for coordinate {i}: {coordinates[i]}")
                elevations.append(None)

        logger.info(f"Fetched {len(elevations)} elevations in batch request")
        return elevations

    except requests.exceptions.RequestException as e:
        logger.error(f"Batch elevation fetch failed: {e}")
        return [None] * len(coordinates)
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"Failed to parse batch elevation response: {e}")
        return [None] * len(coordinates)
