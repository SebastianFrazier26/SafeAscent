"""
Vectorized Safety Algorithm - NumPy Optimized

NumPy-vectorized version of safety_algorithm.py for 3-5× performance improvement.

Instead of looping through accidents one-by-one, we:
1. Convert accident data to NumPy arrays
2. Calculate all weights simultaneously using vectorized operations
3. Use NumPy's fast C implementations

Expected speedup: 2.0s → 0.4-0.7s per prediction
"""
import numpy as np
import math
from datetime import date
from typing import List, Dict, Optional, Tuple

from app.services.safety_algorithm import AccidentData, SafetyPrediction
from app.services.weather_similarity import WeatherPattern, calculate_weather_similarity
from app.services.algorithm_config import (
    RISK_NORMALIZATION_FACTOR,
    MAX_RISK_SCORE,
    MAX_CONTRIBUTING_ACCIDENTS_UI,
    SPATIAL_BANDWIDTH,
    TEMPORAL_LAMBDA,
    SEASONAL_BOOST,
    ELEVATION_DECAY_CONSTANT,
    ELEVATION_BONUS_MAX,
    ROUTE_TYPE_WEIGHTS,
    DEFAULT_ROUTE_TYPE_WEIGHT,
    SEVERITY_BOOSTERS,
    DEFAULT_SEVERITY_WEIGHT,
    SEASONS,
    EARTH_RADIUS_KM,
    GRADE_HALF_WEIGHT_DIFF,
    GRADE_MIN_WEIGHT,
)
from app.services.grade_weighting import parse_grade


def haversine_distance_vectorized(
    lat1: float,
    lon1: float,
    lat2_array: np.ndarray,
    lon2_array: np.ndarray,
) -> np.ndarray:
    """
    Vectorized haversine distance calculation.

    Calculates distance from one point to N points simultaneously.

    Args:
        lat1: Single latitude (degrees)
        lon1: Single longitude (degrees)
        lat2_array: Array of N latitudes (degrees)
        lon2_array: Array of N longitudes (degrees)

    Returns:
        Array of N distances in kilometers
    """
    # Convert to radians
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = np.radians(lat2_array)
    lon2_rad = np.radians(lon2_array)

    # Haversine formula
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = np.sin(dlat / 2) ** 2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(a))

    return EARTH_RADIUS_KM * c


def calculate_spatial_weights_vectorized(
    route_lat: float,
    route_lon: float,
    accident_lats: np.ndarray,
    accident_lons: np.ndarray,
    route_type: str,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Vectorized spatial weight calculation.

    Args:
        route_lat: Route latitude
        route_lon: Route longitude
        accident_lats: Array of accident latitudes
        accident_lons: Array of accident longitudes
        route_type: Route type for bandwidth lookup

    Returns:
        Tuple of (weights, distances)
    """
    # Calculate all distances at once
    distances = haversine_distance_vectorized(
        route_lat, route_lon, accident_lats, accident_lons
    )

    # Get bandwidth
    bandwidth = SPATIAL_BANDWIDTH.get(route_type.lower(), SPATIAL_BANDWIDTH["default"])

    # Gaussian decay: exp(-(d² / (2h²)))
    weights = np.exp(-(distances ** 2) / (2 * bandwidth ** 2))

    return weights, distances


def calculate_temporal_weights_vectorized(
    current_date: date,
    accident_dates: np.ndarray,  # Array of date objects
    route_type: str,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Vectorized temporal weight calculation.

    Args:
        current_date: Current date
        accident_dates: Array of accident dates
        route_type: Route type for lambda lookup

    Returns:
        Tuple of (weights, days_elapsed_array)
    """
    # Get lambda value
    lambda_val = TEMPORAL_LAMBDA.get(route_type.lower(), TEMPORAL_LAMBDA["default"])

    # Calculate days elapsed for each accident
    days_elapsed = np.array([(current_date - acc_date).days for acc_date in accident_dates])

    # Exponential decay: lambda^days
    base_weights = lambda_val ** days_elapsed

    # Apply seasonal boost
    current_month = current_date.month
    seasonal_boosts = np.ones(len(accident_dates))

    for i, acc_date in enumerate(accident_dates):
        acc_month = acc_date.month
        # Check if same season
        for season_months in SEASONS.values():
            if current_month in season_months and acc_month in season_months:
                seasonal_boosts[i] = SEASONAL_BOOST
                break

    weights = base_weights * seasonal_boosts

    return weights, days_elapsed


def calculate_elevation_weights_vectorized(
    route_elevation: Optional[float],
    accident_elevations: np.ndarray,
    route_type: str,
) -> np.ndarray:
    """
    Vectorized elevation weight calculation (bonus-only).

    Args:
        route_elevation: Route elevation in meters (None = all 1.0)
        accident_elevations: Array of accident elevations (may contain None/NaN)
        route_type: Route type for decay constant

    Returns:
        Array of elevation weights
    """
    if route_elevation is None:
        return np.ones(len(accident_elevations))

    # Replace None with NaN for vectorized operations
    elevations = np.array([e if e is not None else np.nan for e in accident_elevations])

    # Calculate absolute elevation differences
    elevation_diffs = np.abs(elevations - route_elevation)

    # Get decay constant
    decay_const = ELEVATION_DECAY_CONSTANT.get(
        route_type.lower(), ELEVATION_DECAY_CONSTANT["default"]
    )

    # Bonus-only weighting: neutral 1.0 plus small Gaussian bonus for close match
    bonuses = np.where(
        np.isnan(elevations),
        0.0,
        ELEVATION_BONUS_MAX * np.exp(-(elevation_diffs / decay_const) ** 2)
    )

    weights = 1.0 + bonuses

    return weights


def calculate_route_type_weights_vectorized(
    planning_route_type: str,
    accident_route_types: List[str],
) -> np.ndarray:
    """
    Vectorized route type weight lookup.

    Args:
        planning_route_type: Planned route type
        accident_route_types: List of accident route types

    Returns:
        Array of route type weights
    """
    weights = np.array([
        ROUTE_TYPE_WEIGHTS.get(
            (planning_route_type.lower(), acc_type.lower()),
            DEFAULT_ROUTE_TYPE_WEIGHT
        )
        for acc_type in accident_route_types
    ])
    return weights


def calculate_severity_weights_vectorized(
    severities: List[str],
) -> np.ndarray:
    """
    Vectorized severity weight lookup.

    Args:
        severities: List of severity levels

    Returns:
        Array of severity weights
    """
    weights = np.array([
        SEVERITY_BOOSTERS.get(sev.lower() if sev else "unknown", DEFAULT_SEVERITY_WEIGHT)
        for sev in severities
    ])
    return weights


def calculate_grade_weights_vectorized(
    route_grade: Optional[str],
    accident_grades: List[Optional[str]],
) -> np.ndarray:
    """
    Vectorized grade weight calculation.

    Args:
        route_grade: Route grade (e.g., "5.10a", None = all 1.0)
        accident_grades: List of accident grades (may contain None)

    Returns:
        Array of grade weights
    """
    # If route grade is unknown, use neutral weight for all
    route_difficulty = parse_grade(route_grade)
    if route_difficulty is None:
        return np.ones(len(accident_grades))

    # Parse accident grades
    accident_difficulties = np.array([
        parse_grade(g) if g else np.nan for g in accident_grades
    ])

    # Calculate grade differences
    grade_diffs = np.abs(accident_difficulties - route_difficulty)

    # Gaussian decay
    sigma = GRADE_HALF_WEIGHT_DIFF / 1.18

    # Handle NaN (unknown grades get weight 1.0)
    weights = np.where(
        np.isnan(accident_difficulties),
        1.0,
        np.maximum(GRADE_MIN_WEIGHT, np.exp(-(grade_diffs ** 2) / (2 * sigma ** 2)))
    )

    return weights


def calculate_safety_score_vectorized(
    route_lat: float,
    route_lon: float,
    route_elevation_m: Optional[float],
    route_type: str,
    current_date: date,
    current_weather: WeatherPattern,
    accidents: List[AccidentData],
    historical_weather_stats: Optional[Dict[str, Tuple[float, float]]] = None,
    route_grade: Optional[str] = None,
) -> SafetyPrediction:
    """
    Vectorized safety score calculation - 3-5× faster than loop-based version.

    Uses NumPy for batch operations instead of Python loops.

    Args:
        Same as calculate_safety_score() in safety_algorithm.py

    Returns:
        SafetyPrediction object
    """
    if not accidents:
        # Return zero-risk prediction
        return SafetyPrediction(
            risk_score=0.0,
            num_contributing_accidents=0,
            top_contributing_accidents=[],
            metadata={"message": "No accidents in database"},
        )

    # Convert accident data to NumPy arrays
    n_accidents = len(accidents)

    accident_ids = np.array([acc.accident_id for acc in accidents])
    accident_lats = np.array([acc.latitude for acc in accidents])
    accident_lons = np.array([acc.longitude for acc in accidents])
    accident_elevations = [acc.elevation_meters for acc in accidents]  # May contain None
    accident_dates = [acc.accident_date for acc in accidents]  # date objects
    accident_route_types = [acc.route_type for acc in accidents]
    accident_severities = [acc.severity for acc in accidents]
    accident_grades = [acc.grade for acc in accidents]  # May contain None

    # Calculate all weights vectorized
    spatial_weights, distances = calculate_spatial_weights_vectorized(
        route_lat, route_lon, accident_lats, accident_lons, route_type
    )

    temporal_weights, days_elapsed = calculate_temporal_weights_vectorized(
        current_date, accident_dates, route_type
    )

    elevation_weights = calculate_elevation_weights_vectorized(
        route_elevation_m, accident_elevations, route_type
    )

    route_type_weights = calculate_route_type_weights_vectorized(
        route_type, accident_route_types
    )

    severity_weights = calculate_severity_weights_vectorized(accident_severities)

    grade_weights = calculate_grade_weights_vectorized(route_grade, accident_grades)

    # Calculate base influence (non-weather factors)
    base_influences = (
        spatial_weights
        * temporal_weights
        * elevation_weights
        * route_type_weights
        * severity_weights
        * grade_weights
    )

    # Weather weighting (uses real similarity; exclusion for very poor matches)
    weather_weights = np.array([
        calculate_weather_similarity(current_weather, acc.weather_pattern, historical_weather_stats)
        if current_weather and acc.weather_pattern else 0.5
        for acc in accidents
    ], dtype=float)

    WEATHER_POWER = 3
    WEATHER_EXCLUSION_THRESHOLD = 0.25
    weather_factor = np.where(
        weather_weights < WEATHER_EXCLUSION_THRESHOLD,
        0.0,
        weather_weights ** WEATHER_POWER
    )

    # Total influence with weather
    total_influences = base_influences * weather_factor

    # Sum total influence
    total_influence_sum = np.sum(total_influences)

    # Normalize to risk score
    risk_score = min(MAX_RISK_SCORE, max(0.0, total_influence_sum * RISK_NORMALIZATION_FACTOR))

    # Build accident influence dicts for UI
    accident_influences = []
    for i in range(n_accidents):
        accident_influences.append({
            "accident_id": int(accident_ids[i]),
            "total_influence": float(total_influences[i]),
            "spatial_weight": float(spatial_weights[i]),
            "temporal_weight": float(temporal_weights[i]),
            "elevation_weight": float(elevation_weights[i]),
            "weather_weight": float(weather_weights[i]),
            "route_type_weight": float(route_type_weights[i]),
            "severity_weight": float(severity_weights[i]),
            "grade_weight": float(grade_weights[i]),
            "distance_km": float(distances[i]),
            "days_ago": int(days_elapsed[i]),
        })

    # Get top contributing accidents
    sorted_indices = np.argsort(total_influences)[::-1]  # Sort descending
    top_n = min(MAX_CONTRIBUTING_ACCIDENTS_UI, len(accidents))
    top_accidents = [accident_influences[i] for i in sorted_indices[:top_n]]

    # Simplify for UI
    simplified_top = []
    for acc in top_accidents:
        simplified_top.append({
            "accident_id": acc["accident_id"],
            "total_influence": round(acc["total_influence"], 4),
            "distance_km": round(acc["distance_km"], 1),
            "days_ago": acc["days_ago"],
            "spatial_weight": round(acc["spatial_weight"], 3),
            "temporal_weight": round(acc["temporal_weight"], 3),
            "elevation_weight": round(acc["elevation_weight"], 3),
            "weather_weight": round(acc["weather_weight"], 3),
            "route_type_weight": round(acc["route_type_weight"], 3),
            "severity_weight": round(acc["severity_weight"], 3),
            "grade_weight": round(acc["grade_weight"], 3),
        })

    return SafetyPrediction(
        risk_score=risk_score,
        num_contributing_accidents=len(accidents),
        top_contributing_accidents=simplified_top,
        metadata={
            "route_type": route_type,
            "search_date": current_date.isoformat(),
            "total_influence_sum": total_influence_sum,
            "normalization_factor": RISK_NORMALIZATION_FACTOR,
            "vectorized": True,  # Marker for vectorized calculation
        },
    )
