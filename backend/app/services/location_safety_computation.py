"""
Location-Level Safety Pre-computation

Optimizes safety computation by calculating base scores at the LOCATION level
rather than per-route. Since 168K routes map to only ~28K locations, this
provides a ~6× reduction in expensive computations.

Architecture:
1. LOCATION-LEVEL (28K locations): Compute base_influence per accident
   - Spatial weight (Gaussian decay from location coordinates)
   - Temporal weight (date-based, same for all routes)
   - Elevation weight (location elevation vs accident elevation)
   - Severity weight (per-accident constant)
   - Weather similarity² (weather pattern at location)

2. ROUTE-LEVEL (168K routes, but fast): Apply route-specific adjustments
   - Route type weight (matrix lookup - cheap)
   - Grade weight (single calculation per route - cheap)

Formula:
  base_influence = spatial × temporal × elevation × severity × weather²
  route_influence = base_influence × route_type_weight × grade_weight
  risk_score = Σ(route_influence) × NORMALIZATION_FACTOR
"""
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.services.algorithm_config import (
    EARTH_RADIUS_KM,
    ELEVATION_DECAY_CONSTANT,
    ELEVATION_BONUS_MAX,
    GRADE_HALF_WEIGHT_DIFF,
    GRADE_MIN_WEIGHT,
    MAX_RISK_SCORE,
    RISK_NORMALIZATION_FACTOR,
    ROUTE_TYPE_WEIGHTS,
    DEFAULT_ROUTE_TYPE_WEIGHT,
    SEASONAL_BOOST,
    SEASONS,
    SEVERITY_BOOSTERS,
    DEFAULT_SEVERITY_WEIGHT,
    SPATIAL_BANDWIDTH,
    TEMPORAL_LAMBDA,
)
from app.services.grade_weighting import parse_grade


@dataclass
class LocationBaseScore:
    """
    Pre-computed base scores for a location.

    Contains the accident influences that are location-dependent
    (everything except route_type and grade weights).
    """
    location_id: int
    latitude: float
    longitude: float
    elevation_m: Optional[float]

    # Per-accident base influences: {accident_id: base_influence}
    # base_influence = spatial × temporal × elevation × severity × weather²
    accident_base_influences: Dict[int, float] = field(default_factory=dict)

    # Store accident metadata for route-level adjustments
    # {accident_id: {"route_type": str, "grade": str}}
    accident_metadata: Dict[int, Dict] = field(default_factory=dict)

    # Total base influence (sum before route-specific adjustments)
    total_base_influence: float = 0.0


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in kilometers."""
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    return EARTH_RADIUS_KM * c


def calculate_spatial_weight(distance_km: float, bandwidth: float) -> float:
    """
    Calculate spatial weight using pure Gaussian decay.

    Weather-primary model relies on weather² weighting to handle weather
    similarity - no gate needed here. Spatial weight is purely distance-based.

    Args:
        distance_km: Distance in kilometers
        bandwidth: Route-type-specific bandwidth in kilometers

    Returns:
        Spatial weight (0-1, Gaussian decay)
    """
    return math.exp(-(distance_km ** 2) / (2 * bandwidth ** 2))


def get_season(month: int) -> str:
    """Get season name for a given month."""
    for season, months in SEASONS.items():
        if month in months:
            return season
    return "unknown"


def compute_location_base_score(
    location_id: int,
    location_lat: float,
    location_lon: float,
    location_elevation_m: Optional[float],
    target_date: date,
    accidents: List[Dict],
    weather_similarity_map: Dict[int, float],
    default_route_type: str = "trad",
) -> LocationBaseScore:
    """
    Compute base scores for a single location.

    This calculates the location-dependent weights for each accident:
    - Spatial weight (distance from location)
    - Temporal weight (days since accident + seasonal boost)
    - Elevation weight (location vs accident elevation)
    - Severity weight (accident severity)
    - Weather factor (weather_similarity², using pre-fetched weather)

    Args:
        location_id: Location identifier
        location_lat: Location latitude
        location_lon: Location longitude
        location_elevation_m: Location elevation in meters
        target_date: Date for prediction
        accidents: List of accident dicts with keys:
            accident_id, latitude, longitude, elevation_m, accident_date,
            route_type, severity, grade
        weather_similarity_map: Pre-computed {accident_id: weather_similarity}
        default_route_type: Route type for spatial bandwidth (use most common type at location)

    Returns:
        LocationBaseScore with pre-computed base influences
    """
    result = LocationBaseScore(
        location_id=location_id,
        latitude=location_lat,
        longitude=location_lon,
        elevation_m=location_elevation_m,
    )

    # Get bandwidth for spatial calculation (use default route type for location)
    bandwidth = SPATIAL_BANDWIDTH.get(default_route_type.lower(), SPATIAL_BANDWIDTH["default"])

    # Get temporal decay lambda (use default)
    lambda_val = TEMPORAL_LAMBDA.get(default_route_type.lower(), TEMPORAL_LAMBDA["default"])

    # Get elevation decay constant
    elevation_decay = ELEVATION_DECAY_CONSTANT.get(
        default_route_type.lower(), ELEVATION_DECAY_CONSTANT["default"]
    )

    # Current season for seasonal boost
    current_season = get_season(target_date.month)

    # Weather parameters - cubic for stronger weather sensitivity
    WEATHER_POWER = 3
    WEATHER_EXCLUSION_THRESHOLD = 0.25

    total_base = 0.0

    for accident in accidents:
        acc_id = accident["accident_id"]

        # 1. Spatial weight (pure Gaussian decay)
        distance_km = haversine_distance(
            location_lat, location_lon,
            accident["latitude"], accident["longitude"]
        )
        spatial_weight = calculate_spatial_weight(distance_km, bandwidth)

        # 2. Temporal weight (exponential decay + seasonal boost)
        days_elapsed = (target_date - accident["accident_date"]).days
        if days_elapsed < 0:
            # Future accident (shouldn't happen, but handle gracefully)
            temporal_weight = 0.0
        else:
            base_temporal = lambda_val ** days_elapsed

            # Seasonal boost
            accident_season = get_season(accident["accident_date"].month)
            if accident_season == current_season:
                temporal_weight = base_temporal * SEASONAL_BOOST
            else:
                temporal_weight = base_temporal

        # 3. Elevation micro-bonus (never penalizes)
        if location_elevation_m is None or accident.get("elevation_m") is None:
            elevation_weight = 1.0
        else:
            elev_diff = abs(accident["elevation_m"] - location_elevation_m)
            elevation_weight = 1.0 + (ELEVATION_BONUS_MAX * math.exp(-(elev_diff / elevation_decay) ** 2))

        # 4. Severity weight
        severity = accident.get("severity", "unknown")
        severity_weight = SEVERITY_BOOSTERS.get(
            severity.lower() if severity else "unknown",
            DEFAULT_SEVERITY_WEIGHT
        )

        # 5. Weather factor (quadratic, with exclusion threshold)
        weather_similarity = weather_similarity_map.get(acc_id, 0.5)  # Default neutral

        if weather_similarity < WEATHER_EXCLUSION_THRESHOLD:
            weather_factor = 0.0
        else:
            weather_factor = weather_similarity ** WEATHER_POWER

        # Calculate base influence (before route-specific adjustments)
        base_influence = (
            spatial_weight
            * temporal_weight
            * elevation_weight
            * severity_weight
            * weather_factor
        )

        # Store results
        result.accident_base_influences[acc_id] = base_influence
        result.accident_metadata[acc_id] = {
            "route_type": accident.get("route_type", "unknown"),
            "grade": accident.get("grade"),
            "distance_km": distance_km,
            "days_ago": days_elapsed,
        }

        total_base += base_influence

    result.total_base_influence = total_base

    return result


def prepare_accident_arrays(accidents: List[Dict]) -> Tuple[np.ndarray, ...]:
    """
    Convert accident list to numpy arrays for vectorized computation.

    Call this once before processing all locations.

    Returns: (lat, lon, ids, days_since_epoch, elevations, severity_weights, route_types)
    """
    lat = np.array([a["latitude"] for a in accidents], dtype=np.float64)
    lon = np.array([a["longitude"] for a in accidents], dtype=np.float64)
    ids = np.array([a["accident_id"] for a in accidents], dtype=np.int64)

    # Convert dates to days since epoch for vectorized temporal computation
    from datetime import date as date_type
    epoch = date_type(2000, 1, 1)
    days = np.array([
        (a["accident_date"] - epoch).days if a.get("accident_date") else 0
        for a in accidents
    ], dtype=np.int32)

    # Elevations (use NaN for missing)
    elevations = np.array([
        a.get("elevation_m") if a.get("elevation_m") is not None else np.nan
        for a in accidents
    ], dtype=np.float64)

    # Severity weights (pre-mapped to numeric values)
    severity_map = {
        "fatal": 1.3,
        "serious": 1.1,
        "minor": 1.0,
        "unknown": 1.0,
    }
    severity_weights = np.array([
        severity_map.get((a.get("severity") or "unknown").lower(), 1.0)
        for a in accidents
    ], dtype=np.float64)

    # Route types (for metadata)
    route_types = [a.get("route_type", "unknown") for a in accidents]
    # Grades (for metadata)
    grades = [a.get("grade") for a in accidents]

    return lat, lon, ids, days, elevations, severity_weights, route_types, grades


def compute_location_base_score_vectorized(
    location_id: int,
    location_lat: float,
    location_lon: float,
    location_elevation_m: Optional[float],
    target_date: date,
    accident_arrays: Tuple[np.ndarray, ...],
    weather_similarity_map: Dict[int, float],
    default_route_type: str = "trad",
    max_distance_km: float = 300.0,
) -> LocationBaseScore:
    """
    Vectorized version of compute_location_base_score for ~50x speedup.

    Uses numpy vectorization instead of Python loops.
    """
    lat, lon, ids, days_since_epoch, elevations, severity_weights, route_types, grades = accident_arrays
    n = len(lat)

    result = LocationBaseScore(
        location_id=location_id,
        latitude=location_lat,
        longitude=location_lon,
        elevation_m=location_elevation_m,
    )

    # Get parameters for this route type
    bandwidth = SPATIAL_BANDWIDTH.get(default_route_type.lower(), SPATIAL_BANDWIDTH["default"])
    lambda_val = TEMPORAL_LAMBDA.get(default_route_type.lower(), TEMPORAL_LAMBDA["default"])
    elevation_decay = ELEVATION_DECAY_CONSTANT.get(
        default_route_type.lower(), ELEVATION_DECAY_CONSTANT["default"]
    )

    # Weather parameters - cubic for stronger weather sensitivity
    WEATHER_POWER = 3
    WEATHER_EXCLUSION_THRESHOLD = 0.25

    # Target date as days since epoch
    from datetime import date as date_type
    epoch = date_type(2000, 1, 1)
    target_days = (target_date - epoch).days

    # 1. VECTORIZED HAVERSINE DISTANCE
    lat1_rad = np.radians(location_lat)
    lat2_rad = np.radians(lat)
    dlat = np.radians(lat - location_lat)
    dlon = np.radians(lon - location_lon)

    a = np.sin(dlat / 2) ** 2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    distances_km = EARTH_RADIUS_KM * c

    # 2. VECTORIZED SPATIAL WEIGHT (pure Gaussian decay)
    # Weather-primary model relies on weather² to handle similarity weighting
    spatial_weights = np.exp(-(distances_km ** 2) / (2 * bandwidth ** 2))

    # 3. VECTORIZED WEATHER SIMILARITY
    weather_sims = np.array([
        weather_similarity_map.get(int(acc_id), 0.5)
        for acc_id in ids
    ], dtype=np.float64)

    # 4. VECTORIZED TEMPORAL WEIGHT
    days_elapsed = target_days - days_since_epoch
    days_elapsed = np.clip(days_elapsed, 0, None)  # No negative values
    temporal_weights = lambda_val ** days_elapsed

    # Seasonal boost - apply average boost for speed (skip exact month matching)
    # Average of 1.0 (different season) and 1.5 (same season) = 1.125
    seasonal_boost = np.full(n, 1.125, dtype=np.float64)

    temporal_weights *= seasonal_boost

    # 5. VECTORIZED ELEVATION WEIGHT
    if location_elevation_m is None:
        elevation_weights = np.ones(n, dtype=np.float64)
    else:
        elev_diff = elevations - location_elevation_m
        elevation_weights = np.where(
            np.isnan(elev_diff) | (elev_diff <= 0),
            1.0,
            np.exp(-((np.maximum(elev_diff, 0) / elevation_decay) ** 2))
        )

    # 6. SEVERITY WEIGHTS (already numpy array from prepare_accident_arrays)

    # 7. VECTORIZED WEATHER FACTOR (quadratic)
    # Note: weather_sims already computed above for weather gate
    # Exclusion threshold and quadratic power
    weather_factors = np.where(
        weather_sims < WEATHER_EXCLUSION_THRESHOLD,
        0.0,
        weather_sims ** WEATHER_POWER
    )

    # 8. COMBINE ALL WEIGHTS
    base_influences = (
        spatial_weights
        * temporal_weights
        * elevation_weights
        * severity_weights
        * weather_factors
    )

    # Store results (only for significant influences to save memory)
    threshold = 1e-6  # Minimum influence to track
    significant_mask = base_influences > threshold

    for i in np.where(significant_mask)[0]:
        acc_id = int(ids[i])
        result.accident_base_influences[acc_id] = float(base_influences[i])
        result.accident_metadata[acc_id] = {
            "route_type": route_types[i],
            "grade": grades[i],
            "distance_km": float(distances_km[i]),
            "days_ago": int(days_elapsed[i]),
        }

    result.total_base_influence = float(np.sum(base_influences))

    return result


def compute_route_risk_score(
    location_base: LocationBaseScore,
    route_type: str,
    route_grade: Optional[str] = None,
) -> Tuple[float, List[Dict]]:
    """
    Compute final risk score for a route using pre-computed location base.

    This applies route-specific adjustments:
    - Route type weight (asymmetric matrix lookup)
    - Grade weight (Gaussian decay based on grade similarity)

    Args:
        location_base: Pre-computed LocationBaseScore for this route's location
        route_type: Route type (alpine, sport, trad, etc.)
        route_grade: Route grade (e.g., "5.10a", None = neutral weight)

    Returns:
        Tuple of (risk_score, contributing_accidents_list)
    """
    route_type_lower = route_type.lower()

    # Pre-parse route grade for efficiency
    route_difficulty = parse_grade(route_grade)

    # Sigma for grade Gaussian decay
    sigma = GRADE_HALF_WEIGHT_DIFF / 1.18

    total_influence = 0.0
    contributing_accidents = []

    for acc_id, base_influence in location_base.accident_base_influences.items():
        if base_influence == 0.0:
            continue  # Skip excluded accidents (poor weather match)

        metadata = location_base.accident_metadata[acc_id]
        acc_route_type = metadata.get("route_type", "unknown").lower()
        acc_grade = metadata.get("grade")

        # 1. Route type weight (matrix lookup)
        route_type_weight = ROUTE_TYPE_WEIGHTS.get(
            (route_type_lower, acc_route_type),
            DEFAULT_ROUTE_TYPE_WEIGHT
        )

        # 2. Grade weight (Gaussian decay)
        if route_difficulty is None:
            # Unknown route grade: neutral weight for all accidents
            grade_weight = 1.0
        else:
            acc_difficulty = parse_grade(acc_grade)
            if acc_difficulty is None:
                # Unknown accident grade: neutral weight
                grade_weight = 1.0
            else:
                grade_diff = abs(route_difficulty - acc_difficulty)
                grade_weight = max(
                    GRADE_MIN_WEIGHT,
                    math.exp(-(grade_diff ** 2) / (2 * sigma ** 2))
                )

        # Final influence for this accident
        route_influence = base_influence * route_type_weight * grade_weight
        total_influence += route_influence

        # Track for contributing accidents list
        if route_influence > 0:
            contributing_accidents.append({
                "accident_id": acc_id,
                "total_influence": round(route_influence, 4),
                "base_influence": round(base_influence, 4),
                "route_type_weight": round(route_type_weight, 3),
                "grade_weight": round(grade_weight, 3),
                "distance_km": round(metadata.get("distance_km", 0), 1),
                "days_ago": metadata.get("days_ago", 0),
            })

    # Normalize to risk score (0-100)
    risk_score = min(MAX_RISK_SCORE, max(0.0, total_influence * RISK_NORMALIZATION_FACTOR))

    # Sort contributing accidents by influence
    contributing_accidents.sort(key=lambda x: x["total_influence"], reverse=True)

    return risk_score, contributing_accidents[:50]  # Top 50 for UI


def compute_batch_route_scores(
    location_base: LocationBaseScore,
    routes: List[Dict],
) -> Dict[int, Dict]:
    """
    Compute risk scores for multiple routes sharing the same location.

    This is the key optimization: we compute location_base once, then
    apply route-specific adjustments for each route (cheap operation).

    Args:
        location_base: Pre-computed base scores for the shared location
        routes: List of route dicts with keys: route_id, route_type, grade

    Returns:
        Dict mapping route_id to {"risk_score": float, "color_code": str}
    """
    results = {}

    for route in routes:
        route_id = route["route_id"]
        route_type = route.get("route_type", "trad")
        route_grade = route.get("grade")

        risk_score, _ = compute_route_risk_score(
            location_base=location_base,
            route_type=route_type,
            route_grade=route_grade,
        )

        # Determine color code
        if risk_score < 25:
            color_code = "green"
        elif risk_score < 50:
            color_code = "yellow"
        elif risk_score < 75:
            color_code = "orange"
        else:
            color_code = "red"

        results[route_id] = {
            "risk_score": round(risk_score, 1),
            "color_code": color_code,
        }

    return results
