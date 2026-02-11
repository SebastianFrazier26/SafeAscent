"""
Temporal Weighting Module - SafeAscent Safety Algorithm

Calculates temporal influence of accidents based on time elapsed.
Uses year-scale exponential decay with damped impact and mild seasonal boosting.

Formula:
  base_decay = lambda^days
  base_weight = 1 - impact * (1 - base_decay^shape)
  weight = base_weight * mild_seasonal_multiplier
"""
from datetime import date

from app.services.algorithm_config import (
    TEMPORAL_LAMBDA,
    TEMPORAL_DECAY_IMPACT,
    TEMPORAL_DECAY_SHAPE,
    TEMPORAL_SEASONAL_IMPACT,
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
        1.0xx  # Near-neutral: recent + mild same-season boost

        >>> accident_winter = date(2023, 1, 10)  # Winter, 6 months earlier
        >>> weight = calculate_temporal_weight(current, accident_winter, "alpine")
        >>> print(f"{weight:.3f}")
        0.9xx  # Lower: no seasonal boost
    """
    # Calculate days elapsed
    days_elapsed = days_between(accident_date, current_date)

    # Get route-type-specific lambda
    lambda_value = TEMPORAL_LAMBDA.get(route_type.lower(), TEMPORAL_LAMBDA["default"])

    # Exponential decay baseline
    base_decay = lambda_value ** days_elapsed

    # Re-center around neutral weight so recency stays a modest contributor:
    # - recent accidents remain close to 1.0
    # - very old accidents still decay meaningfully
    base_weight = 1.0 - TEMPORAL_DECAY_IMPACT * (1.0 - (base_decay ** TEMPORAL_DECAY_SHAPE))

    # Apply seasonal boost if same season
    if apply_seasonal_boost and is_same_season(current_date, accident_date):
        seasonal_multiplier = 1.0 + ((SEASONAL_BOOST - 1.0) * TEMPORAL_SEASONAL_IMPACT)
        weight = base_weight * seasonal_multiplier
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
        - 'base_decay': float (raw lambda^days decay)
        - 'seasonal_boost_applied': bool
        - 'seasonal_multiplier': float
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
            'base_weight': 0.96,
            'seasonal_boost_applied': True,
            'final_weight': 1.01,
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

    # Calculate baseline decay and damped temporal effect
    base_decay = lambda_value ** days_elapsed
    base_weight = 1.0 - TEMPORAL_DECAY_IMPACT * (1.0 - (base_decay ** TEMPORAL_DECAY_SHAPE))

    # Check seasonal boost
    same_season = current_season == accident_season
    seasonal_multiplier = (
        1.0 + ((SEASONAL_BOOST - 1.0) * TEMPORAL_SEASONAL_IMPACT)
        if same_season
        else 1.0
    )
    final_weight = base_weight * seasonal_multiplier

    return {
        "days_elapsed": days_elapsed,
        "base_weight": base_weight,
        "base_decay": base_decay,
        "seasonal_boost_applied": same_season,
        "seasonal_multiplier": seasonal_multiplier,
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
