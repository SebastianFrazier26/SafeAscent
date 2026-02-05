"""
SafeAscent Safety Prediction Algorithm - Main Orchestrator

This is the main entry point for the safety prediction algorithm.
Coordinates all component modules to produce a risk score.

Algorithm Pipeline:
1. Fetch relevant accidents from database (spatial query)
2. For each accident, calculate:
   - Spatial weight (Gaussian decay by distance)
   - Temporal weight (exponential decay + seasonal boost)
   - Weather similarity (7-day pattern correlation)
   - Route type weight (asymmetric matrix)
   - Severity weight (subtle boosters)
3. Combine into total influence per accident (Weather-Primary Model):
   - base_influence = spatial × temporal × route_type × severity
   - weather_factor = weather_similarity^2 (quadratic for dominance)
   - total_influence = base_influence × weather_factor
   - Exclude accidents with weather_similarity < 0.25
4. Sum all influences and normalize to 0-100 risk score
5. Return prediction with detailed breakdown

Weather-Primary Risk Model (Updated 2026-01-30):
- Weather is primary risk driver (creates 5-7× variation sunny → stormy)
- Accident history acts as amplifier (distinguishes dangerous vs safe routes)
- Risk scores are volatile day-to-day based on current conditions
"""
from datetime import date
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

from app.services.spatial_weighting import calculate_spatial_weight_with_distance
from app.services.temporal_weighting import calculate_temporal_weight_detailed
from app.services.route_type_weighting import calculate_route_type_weight
from app.services.elevation_weighting import calculate_elevation_weight
from app.services.weather_similarity import (
    calculate_weather_similarity_detailed,
    WeatherPattern,
)
from app.services.severity_weighting import calculate_severity_weight
from app.services.grade_weighting import calculate_grade_weight
from app.services.algorithm_config import (
    RISK_NORMALIZATION_FACTOR,
    MAX_RISK_SCORE,
    MAX_CONTRIBUTING_ACCIDENTS_UI,
)


@dataclass
class AccidentData:
    """
    Complete accident data needed for safety calculation.

    Attributes:
        accident_id: Unique identifier
        latitude: Accident location latitude
        longitude: Accident location longitude
        elevation_meters: Accident elevation in meters above sea level (None = neutral weight)
        accident_date: Date when accident occurred
        route_type: Type of climbing route (alpine, sport, etc.)
        severity: Severity level (fatal, serious, minor, unknown)
        weather_pattern: 7-day weather pattern (or None if missing)
        grade: Climbing grade of accident route (e.g., "5.10a", None if unknown)
    """

    accident_id: int
    latitude: float
    longitude: float
    elevation_meters: Optional[float]
    accident_date: date
    route_type: str
    severity: str
    weather_pattern: Optional[WeatherPattern] = None
    grade: Optional[str] = None


@dataclass
class SafetyPrediction:
    """
    Complete safety prediction result.

    Attributes:
        risk_score: Overall risk score (0-100, higher = more dangerous)
        num_contributing_accidents: Number of accidents that influenced score
        top_contributing_accidents: List of top accidents with breakdown
        metadata: Additional information about the calculation
    """

    risk_score: float
    num_contributing_accidents: int
    top_contributing_accidents: List[Dict]
    metadata: Dict


def calculate_safety_score(
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
    Calculate safety prediction for a planned climbing route.

    Main orchestrator function that coordinates all algorithm components.

    Args:
        route_lat: Latitude of planned route (degrees)
        route_lon: Longitude of planned route (degrees)
        route_elevation_m: Elevation of planned route in meters (None = neutral weight)
        route_type: Type of climbing route (alpine, trad, sport, etc.)
        current_date: Date of planned climb
        current_weather: Current 7-day weather pattern
        accidents: List of relevant accidents from database
        historical_weather_stats: Location/season weather statistics for extreme detection
        route_grade: Grade of planned route (e.g., "5.10a", None = neutral weight)

    Returns:
        SafetyPrediction object with risk score and detailed breakdown

    Example:
        >>> from datetime import date
        >>> prediction = calculate_safety_score(
        ...     route_lat=40.0,
        ...     route_lon=-105.0,
        ...     route_type="alpine",
        ...     current_date=date(2024, 7, 15),
        ...     current_weather=WeatherPattern(...),
        ...     accidents=[AccidentData(...), ...],
        ... )
        >>> print(f"Risk: {prediction.risk_score:.0f}")
        Risk: 68
    """
    # Handle edge case: no accidents
    if not accidents:
        return SafetyPrediction(
            risk_score=0.0,
            num_contributing_accidents=0,
            top_contributing_accidents=[],
            metadata={
                "message": "No historical accidents found in search area",
                "route_type": route_type,
                "search_date": current_date.isoformat(),
            },
        )

    # Step 1: Calculate influence for each accident
    accident_influences = []

    for accident in accidents:
        # Calculate all component weights
        influence = calculate_accident_influence(
            route_lat=route_lat,
            route_lon=route_lon,
            route_elevation_m=route_elevation_m,
            route_type=route_type,
            current_date=current_date,
            current_weather=current_weather,
            accident=accident,
            historical_weather_stats=historical_weather_stats,
            route_grade=route_grade,
        )

        accident_influences.append(influence)

    # Step 2: Sum total influences
    total_influence = sum(acc["total_influence"] for acc in accident_influences)

    # Step 3: Normalize to risk score (0-100)
    risk_score = normalize_risk_score(total_influence)

    # Step 4: Get top contributing accidents for UI
    top_accidents = get_top_contributing_accidents(
        accident_influences, limit=MAX_CONTRIBUTING_ACCIDENTS_UI
    )

    # Step 5: Package and return results
    prediction = SafetyPrediction(
        risk_score=risk_score,
        num_contributing_accidents=len(accidents),
        top_contributing_accidents=top_accidents,
        metadata={
            "route_type": route_type,
            "search_date": current_date.isoformat(),
            "total_influence_sum": total_influence,
            "normalization_factor": RISK_NORMALIZATION_FACTOR,
        },
    )

    return prediction


def calculate_accident_influence(
    route_lat: float,
    route_lon: float,
    route_elevation_m: Optional[float],
    route_type: str,
    current_date: date,
    current_weather: WeatherPattern,
    accident: AccidentData,
    historical_weather_stats: Optional[Dict[str, Tuple[float, float]]] = None,
    route_grade: Optional[str] = None,
) -> Dict:
    """
    Calculate total influence of a single accident on risk score.

    NEW ALGORITHM (2026-01-30): Weather-Primary Risk Model
    Combines all weighting factors with weather as dominant signal:

    base_influence = spatial × temporal × elevation × route_type × severity
    weather_factor = weather_similarity^2  (quadratic power for dominance)
    total_influence = base_influence × weather_factor

    Excludes accidents with weather_similarity < 0.25 (very poor match).

    This makes weather the primary risk driver (5-7× variation from sunny to stormy)
    while accident history acts as amplifier (distinguishes dangerous vs safe routes).

    Args:
        route_lat: Latitude of planned route
        route_lon: Longitude of planned route
        route_elevation_m: Elevation of planned route in meters (None = neutral weight)
        route_type: Type of planned route
        current_date: Date of planned climb
        current_weather: Current weather pattern
        accident: Accident data
        historical_weather_stats: Weather statistics for extreme detection

    Returns:
        Dictionary with all influence components and metadata
    """
    from app.utils.geo_utils import calculate_bearing

    # 1. Spatial weight
    spatial_weight, distance_km = calculate_spatial_weight_with_distance(
        route_lat=route_lat,
        route_lon=route_lon,
        accident_lat=accident.latitude,
        accident_lon=accident.longitude,
        route_type=route_type,
    )

    # 2. Temporal weight
    temporal_result = calculate_temporal_weight_detailed(
        current_date=current_date,
        accident_date=accident.accident_date,
        route_type=route_type,
    )
    temporal_weight = temporal_result["final_weight"]
    days_ago = temporal_result["days_elapsed"]

    # 3. Weather similarity weight
    if accident.weather_pattern and current_weather:
        weather_result = calculate_weather_similarity_detailed(
            current_pattern=current_weather,
            accident_pattern=accident.weather_pattern,
            historical_stats=historical_weather_stats,
        )
        weather_weight = weather_result["final_similarity"]
        has_weather_data = True
    else:
        # No weather data: use neutral weight (0.5)
        weather_weight = 0.5
        weather_result = None
        has_weather_data = False

    # 4. Route type weight
    route_type_weight = calculate_route_type_weight(
        planning_route_type=route_type,
        accident_route_type=accident.route_type,
    )

    # 5. Severity weight
    severity_weight = calculate_severity_weight(accident.severity)

    # 6. Elevation weight (asymmetric: higher accidents get reduced weight)
    elevation_weight = calculate_elevation_weight(
        route_elevation_m=route_elevation_m,
        accident_elevation_m=accident.elevation_meters,
        route_type=route_type,
    )

    # 7. Grade weight (similar grades more relevant)
    grade_weight = calculate_grade_weight(
        route_grade=route_grade,
        accident_grade=accident.grade,
    )

    # Calculate total influence with quadratic weather weighting
    # NEW ALGORITHM (2026-01-30): Weather becomes primary risk driver via power weighting
    # - Calculate base influence from non-weather factors
    # - Apply weather_weight^2 to make weather dominant
    # - Exclude accidents with very poor weather match (<0.25 similarity)

    base_influence = (
        spatial_weight
        * temporal_weight
        * elevation_weight
        * route_type_weight
        * severity_weight
        * grade_weight
    )

    # Apply quadratic weather weighting for exponential weather sensitivity
    # weather=0.3 → 0.09 (91% reduction), weather=0.8 → 0.64 (36% reduction)
    WEATHER_POWER = 2  # Quadratic power (easily tunable: 2=square, 3=cubic, 1.77=7× variation)
    WEATHER_EXCLUSION_THRESHOLD = 0.25  # Exclude accidents with <25% weather similarity

    if weather_weight < WEATHER_EXCLUSION_THRESHOLD:
        # Exclude accidents with very dissimilar weather
        total_influence = 0.0
    else:
        # Apply power weighting to amplify weather importance
        weather_factor = weather_weight ** WEATHER_POWER
        total_influence = base_influence * weather_factor

    # Calculate bearing for spatial coverage
    bearing = calculate_bearing(
        lat1=route_lat,
        lon1=route_lon,
        lat2=accident.latitude,
        lon2=accident.longitude,
    )

    return {
        "accident_id": accident.accident_id,
        "total_influence": total_influence,
        "spatial_weight": spatial_weight,
        "temporal_weight": temporal_weight,
        "elevation_weight": elevation_weight,
        "weather_weight": weather_weight,
        "route_type_weight": route_type_weight,
        "severity_weight": severity_weight,
        "grade_weight": grade_weight,
        "distance_km": distance_km,
        "days_ago": days_ago,
        "bearing": bearing,
        "has_weather_data": has_weather_data,
        "temporal_breakdown": temporal_result,
        "weather_breakdown": weather_result,
    }


def normalize_risk_score(total_influence: float) -> float:
    """
    Normalize total influence sum to risk score (0-100).

    Uses empirically determined normalization factor.
    TODO: Calibrate via backtesting with real data.

    Args:
        total_influence: Sum of all accident influences

    Returns:
        Risk score from 0 to 100 (capped at MAX_RISK_SCORE)

    Example:
        >>> risk = normalize_risk_score(6.8)
        >>> print(f"{risk:.0f}")
        68  # 6.8 × 10 = 68
    """
    risk_score = total_influence * RISK_NORMALIZATION_FACTOR
    return min(MAX_RISK_SCORE, max(0.0, risk_score))


def get_top_contributing_accidents(
    accident_influences: List[Dict],
    limit: int = 50,
) -> List[Dict]:
    """
    Get top N contributing accidents, sorted by total influence.

    Returns simplified dict for UI display (not full breakdown).

    Args:
        accident_influences: List of influence dictionaries
        limit: Maximum number to return

    Returns:
        List of top accidents with key metrics
    """
    # Sort by total influence (descending)
    sorted_accidents = sorted(
        accident_influences,
        key=lambda x: x["total_influence"],
        reverse=True,
    )

    # Take top N
    top_accidents = sorted_accidents[:limit]

    # Simplify for UI
    simplified = []
    for acc in top_accidents:
        simplified.append(
            {
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
            }
        )

    return simplified
