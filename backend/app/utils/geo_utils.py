"""
Geographic utility functions for SafeAscent algorithm.

Provides distance and bearing calculations using the Haversine formula.
"""
import math
from typing import Tuple

from app.services.algorithm_config import EARTH_RADIUS_KM


def haversine_distance(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """
    Calculate the great-circle distance between two points on Earth.

    Uses the Haversine formula to compute distance along Earth's surface.

    Args:
        lat1: Latitude of first point (degrees)
        lon1: Longitude of first point (degrees)
        lat2: Latitude of second point (degrees)
        lon2: Longitude of second point (degrees)

    Returns:
        Distance in kilometers

    Example:
        >>> distance = haversine_distance(40.0, -105.0, 40.1, -105.1)
        >>> print(f"{distance:.2f} km")
        13.69 km
    """
    # Convert degrees to radians
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    # Haversine formula
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))

    distance_km = EARTH_RADIUS_KM * c

    return distance_km


def calculate_bearing(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """
    Calculate the initial bearing (direction) from point 1 to point 2.

    Returns the bearing in degrees (0-360), where:
    - 0° = North
    - 90° = East
    - 180° = South
    - 270° = West

    Used for spatial coverage analysis in confidence scoring.

    Args:
        lat1: Latitude of first point (degrees)
        lon1: Longitude of first point (degrees)
        lat2: Latitude of second point (degrees)
        lon2: Longitude of second point (degrees)

    Returns:
        Bearing in degrees (0-360)

    Example:
        >>> bearing = calculate_bearing(40.0, -105.0, 40.1, -105.0)
        >>> print(f"{bearing:.1f}°")
        0.0°  # North
    """
    # Convert to radians
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlon_rad = math.radians(lon2 - lon1)

    # Calculate bearing
    x = math.sin(dlon_rad) * math.cos(lat2_rad)
    y = math.cos(lat1_rad) * math.sin(lat2_rad) - (
        math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dlon_rad)
    )

    initial_bearing = math.atan2(x, y)

    # Convert to degrees and normalize to 0-360
    bearing_degrees = (math.degrees(initial_bearing) + 360) % 360

    return bearing_degrees


def get_bounding_box(
    center_lat: float, center_lon: float, radius_km: float
) -> Tuple[float, float, float, float]:
    """
    Calculate a bounding box around a center point.

    Returns (min_lat, max_lat, min_lon, max_lon) for a square bounding box
    that contains all points within radius_km of the center.

    Useful for pre-filtering accidents in database queries before precise
    Haversine distance calculations.

    Args:
        center_lat: Center point latitude (degrees)
        center_lon: Center point longitude (degrees)
        radius_km: Radius in kilometers

    Returns:
        Tuple of (min_lat, max_lat, min_lon, max_lon)

    Example:
        >>> bbox = get_bounding_box(40.0, -105.0, 50.0)
        >>> min_lat, max_lat, min_lon, max_lon = bbox
        >>> print(f"Lat: {min_lat:.2f} to {max_lat:.2f}")
        Lat: 39.55 to 40.45
    """
    # Approximate degrees per kilometer
    # 1 degree latitude ≈ 111 km (constant)
    # 1 degree longitude ≈ 111 km * cos(latitude) (varies by latitude)
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * math.cos(math.radians(center_lat)))

    min_lat = center_lat - lat_delta
    max_lat = center_lat + lat_delta
    min_lon = center_lon - lon_delta
    max_lon = center_lon + lon_delta

    return (min_lat, max_lat, min_lon, max_lon)
