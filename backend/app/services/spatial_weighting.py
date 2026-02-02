"""
Spatial Weighting Module - SafeAscent Safety Algorithm

Calculates spatial influence of accidents based on distance using Gaussian decay.
Route type determines the spatial bandwidth (how quickly influence fades).

Mathematical basis: Gaussian Kernel Density Estimation
Formula: weight = exp(-(distance² / (2 × bandwidth²)))
"""
import math
from typing import Optional

from app.services.algorithm_config import (
    SPATIAL_BANDWIDTH,
    MAX_SEARCH_RADIUS_KM,
)
from app.utils.geo_utils import haversine_distance


def calculate_spatial_weight(
    route_lat: float,
    route_lon: float,
    accident_lat: float,
    accident_lon: float,
    route_type: str = "default",
) -> float:
    """
    Calculate spatial weight for an accident based on distance to route.

    Uses Gaussian decay with route-type-specific bandwidth. No hard cutoff -
    accidents far away can still contribute if weather is similar.

    Args:
        route_lat: Latitude of planned route (degrees)
        route_lon: Longitude of planned route (degrees)
        accident_lat: Latitude of accident location (degrees)
        accident_lon: Longitude of accident location (degrees)
        route_type: Type of climbing route (alpine, trad, sport, etc.)

    Returns:
        Spatial weight from 0.0 to 1.0 (1.0 at distance=0, decays with distance)

    Example:
        >>> # Alpine route, accident 50km away
        >>> weight = calculate_spatial_weight(40.0, -105.0, 40.45, -105.0, "alpine")
        >>> print(f"{weight:.3f}")
        0.607  # Still significant at 50km for alpine

        >>> # Sport route, same accident
        >>> weight = calculate_spatial_weight(40.0, -105.0, 40.45, -105.0, "sport")
        >>> print(f"{weight:.3f}")
        0.018  # Much lower influence for sport climbing
    """
    # Calculate great-circle distance
    distance_km = haversine_distance(route_lat, route_lon, accident_lat, accident_lon)

    # Get route-type-specific bandwidth
    bandwidth = SPATIAL_BANDWIDTH.get(route_type.lower(), SPATIAL_BANDWIDTH["default"])

    # Gaussian decay formula: exp(-(d² / (2 × h²)))
    # where d = distance, h = bandwidth
    weight = math.exp(-(distance_km ** 2) / (2 * bandwidth ** 2))

    return weight


def calculate_spatial_weight_with_distance(
    route_lat: float,
    route_lon: float,
    accident_lat: float,
    accident_lon: float,
    route_type: str = "default",
) -> tuple[float, float]:
    """
    Calculate spatial weight and return both weight and distance.

    Convenience function that returns both the weight and the actual distance
    in kilometers. Useful for UI display and debugging.

    Args:
        route_lat: Latitude of planned route (degrees)
        route_lon: Longitude of planned route (degrees)
        accident_lat: Latitude of accident location (degrees)
        accident_lon: Longitude of accident location (degrees)
        route_type: Type of climbing route (alpine, trad, sport, etc.)

    Returns:
        Tuple of (spatial_weight, distance_km)

    Example:
        >>> weight, distance = calculate_spatial_weight_with_distance(
        ...     40.0, -105.0, 40.45, -105.0, "alpine"
        ... )
        >>> print(f"Distance: {distance:.1f}km, Weight: {weight:.3f}")
        Distance: 50.0km, Weight: 0.607
    """
    # Calculate distance
    distance_km = haversine_distance(route_lat, route_lon, accident_lat, accident_lon)

    # Get bandwidth
    bandwidth = SPATIAL_BANDWIDTH.get(route_type.lower(), SPATIAL_BANDWIDTH["default"])

    # Calculate weight
    weight = math.exp(-(distance_km ** 2) / (2 * bandwidth ** 2))

    return (weight, distance_km)


def is_within_search_radius(
    route_lat: float,
    route_lon: float,
    accident_lat: float,
    accident_lon: float,
) -> bool:
    """
    Check if an accident is within the maximum search radius.

    This is a performance optimization for database queries. We don't use a hard
    cutoff for the spatial weight (Gaussian decay continues infinitely), but we
    stop searching beyond MAX_SEARCH_RADIUS_KM for practical reasons.

    Args:
        route_lat: Latitude of planned route (degrees)
        route_lon: Longitude of planned route (degrees)
        accident_lat: Latitude of accident location (degrees)
        accident_lon: Longitude of accident location (degrees)

    Returns:
        True if accident within search radius, False otherwise

    Example:
        >>> # Accident 100km away
        >>> within = is_within_search_radius(40.0, -105.0, 40.9, -105.0)
        >>> print(within)
        True

        >>> # Accident 400km away (beyond MAX_SEARCH_RADIUS_KM = 300)
        >>> within = is_within_search_radius(40.0, -105.0, 43.6, -105.0)
        >>> print(within)
        False
    """
    distance_km = haversine_distance(route_lat, route_lon, accident_lat, accident_lon)
    return distance_km <= MAX_SEARCH_RADIUS_KM


def get_spatial_bandwidth(route_type: str) -> float:
    """
    Get the spatial bandwidth for a given route type.

    Useful for UI display, confidence scoring, and algorithm explanation.

    Args:
        route_type: Type of climbing route (alpine, trad, sport, etc.)

    Returns:
        Spatial bandwidth in kilometers

    Example:
        >>> bandwidth = get_spatial_bandwidth("alpine")
        >>> print(f"{bandwidth}km")
        75.0km

        >>> bandwidth = get_spatial_bandwidth("sport")
        >>> print(f"{bandwidth}km")
        25.0km
    """
    return SPATIAL_BANDWIDTH.get(route_type.lower(), SPATIAL_BANDWIDTH["default"])
