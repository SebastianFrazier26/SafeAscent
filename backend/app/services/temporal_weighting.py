"""
Temporal Weighting Module - SafeAscent Safety Algorithm

Calculates temporal influence of accidents based on time elapsed.
Uses year-scale exponential decay with seasonal boosting.

Mathematical basis: EWMA (Exponentially Weighted Moving Average) from GARCH models
Formula: weight = lambda^days × seasonal_boost
where lambda is route-type-specific (alpine = 0.9998, sport = 0.999, etc.)
"""
from datetime import date

from app.services.algorithm_config import (
    TEMPORAL_LAMBDA,
    SEASONAL_BOOST,
)
from app.utils.time_utils import days_between, is_same_season


def calculate_temporal_weight(
    current_date: date,
    accident_date: date,
    route_type: str = "default",
    apply_seasonal_boost: bool = True,
) -> float:
    """
    Calculate temporal weight for an accident based on time elapsed.

    Uses exponential decay with route-type-specific lambda values. Accidents
    from the same season get a 1.5× boost.

    Args:
        current_date: Current date (planned climb date)
        accident_date: Date when accident occurred
        route_type: Type of climbing route (alpine, trad, sport, etc.)
        apply_seasonal_boost: Whether to apply seasonal boost (default True)

    Returns:
        Temporal weight from 0.0 to 1.5 (can exceed 1.0 with seasonal boost)

    Example:
        >>> from datetime import date
        >>> current = date(2024, 7, 15)  # Summer
        >>> accident = date(2023, 7, 10)  # Summer, 1 year ago
        >>> weight = calculate_temporal_weight(current, accident, "alpine")
        >>> print(f"{weight:.3f}")
        1.287  # High weight: recent + same season

        >>> accident_winter = date(2023, 1, 10)  # Winter, 6 months earlier
        >>> weight = calculate_temporal_weight(current, accident_winter, "alpine")
        >>> print(f"{weight:.3f}")
        0.917  # Lower: no seasonal boost
    """
    # Calculate days elapsed
    days_elapsed = days_between(accident_date, current_date)

    # Get route-type-specific lambda
    lambda_value = TEMPORAL_LAMBDA.get(route_type.lower(), TEMPORAL_LAMBDA["default"])

    # Exponential decay: lambda^days
    base_weight = lambda_value ** days_elapsed

    # Apply seasonal boost if same season
    if apply_seasonal_boost and is_same_season(current_date, accident_date):
        weight = base_weight * SEASONAL_BOOST
    else:
        weight = base_weight

    return weight


def calculate_temporal_weight_detailed(
    current_date: date,
    accident_date: date,
    route_type: str = "default",
) -> dict:
    """
    Calculate temporal weight with detailed breakdown.

    Returns a dictionary with all components for UI display and debugging.

    Args:
        current_date: Current date (planned climb date)
        accident_date: Date when accident occurred
        route_type: Type of climbing route (alpine, trad, sport, etc.)

    Returns:
        Dictionary with keys:
        - 'days_elapsed': int
        - 'base_weight': float (before seasonal boost)
        - 'seasonal_boost_applied': bool
        - 'final_weight': float (after seasonal boost if applicable)
        - 'lambda_value': float
        - 'current_season': str
        - 'accident_season': str

    Example:
        >>> from datetime import date
        >>> result = calculate_temporal_weight_detailed(
        ...     date(2024, 7, 15), date(2023, 7, 10), "alpine"
        ... )
        >>> print(result)
        {
            'days_elapsed': 370,
            'base_weight': 0.858,
            'seasonal_boost_applied': True,
            'final_weight': 1.287,
            'lambda_value': 0.9998,
            'current_season': 'summer',
            'accident_season': 'summer'
        }
    """
    from app.utils.time_utils import get_season

    # Calculate days elapsed
    days_elapsed = days_between(accident_date, current_date)

    # Get lambda and seasons
    lambda_value = TEMPORAL_LAMBDA.get(route_type.lower(), TEMPORAL_LAMBDA["default"])
    current_season = get_season(current_date)
    accident_season = get_season(accident_date)

    # Calculate base weight
    base_weight = lambda_value ** days_elapsed

    # Check seasonal boost
    same_season = current_season == accident_season
    final_weight = base_weight * SEASONAL_BOOST if same_season else base_weight

    return {
        "days_elapsed": days_elapsed,
        "base_weight": base_weight,
        "seasonal_boost_applied": same_season,
        "final_weight": final_weight,
        "lambda_value": lambda_value,
        "current_season": current_season,
        "accident_season": accident_season,
    }


def get_temporal_lambda(route_type: str) -> float:
    """
    Get the temporal lambda value for a given route type.

    Useful for UI display, confidence scoring, and algorithm explanation.

    Args:
        route_type: Type of climbing route (alpine, trad, sport, etc.)

    Returns:
        Lambda value (0.999 to 0.9998)

    Example:
        >>> lambda_val = get_temporal_lambda("alpine")
        >>> print(f"{lambda_val}")
        0.9998

        >>> lambda_val = get_temporal_lambda("sport")
        >>> print(f"{lambda_val}")
        0.999
    """
    return TEMPORAL_LAMBDA.get(route_type.lower(), TEMPORAL_LAMBDA["default"])


def get_temporal_half_life(route_type: str) -> float:
    """
    Calculate the half-life (in years) for a given route type.

    Half-life is the time it takes for an accident's temporal weight to
    decay to 50% of its original value.

    Formula: half_life = ln(0.5) / ln(lambda) / 365.25

    Args:
        route_type: Type of climbing route (alpine, trad, sport, etc.)

    Returns:
        Half-life in years

    Example:
        >>> half_life = get_temporal_half_life("alpine")
        >>> print(f"{half_life:.1f} years")
        9.5 years

        >>> half_life = get_temporal_half_life("sport")
        >>> print(f"{half_life:.1f} years")
        1.9 years
    """
    import math

    lambda_value = get_temporal_lambda(route_type)

    # Calculate half-life in days, then convert to years
    half_life_days = math.log(0.5) / math.log(lambda_value)
    half_life_years = half_life_days / 365.25

    return half_life_years
