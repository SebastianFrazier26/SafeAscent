"""
Elevation Weighting Module - SafeAscent Safety Algorithm

Applies a small elevation similarity BONUS only (no penalties).
If elevations are similar, slightly boost relevance; otherwise neutral (1.0).
"""
import math
from typing import Optional

from app.services.algorithm_config import (
    ELEVATION_DECAY_CONSTANT,
    ELEVATION_BONUS_MAX,
)


def calculate_elevation_weight(
    route_elevation_m: Optional[float],
    accident_elevation_m: Optional[float],
    route_type: str = "default",
) -> float:
    """
    Calculate elevation micro-bonus for an accident relative to planned route.
    Only boosts slightly when elevations are similar; never penalizes.

    Args:
        route_elevation_m: Planned route elevation in meters (None = neutral weight)
        accident_elevation_m: Accident elevation in meters (None = neutral weight)
        route_type: Type of climbing route (affects decay rate for bonus falloff)

    Returns:
        Elevation weight ≥1.0 (1.0 = neutral, up to ~1.15 with close match)
    """
    # Handle missing elevation data - return neutral weight
    if route_elevation_m is None or accident_elevation_m is None:
        return 1.0

    # Absolute elevation difference (meters)
    elevation_diff = abs(accident_elevation_m - route_elevation_m)

    # Route-type-specific decay constant (controls how fast bonus falls off)
    decay_constant = ELEVATION_DECAY_CONSTANT.get(
        route_type.lower(),
        ELEVATION_DECAY_CONSTANT["default"]
    )

    # Gaussian bonus centered at 0 diff; capped by ELEVATION_BONUS_MAX
    bonus = ELEVATION_BONUS_MAX * math.exp(-(elevation_diff / decay_constant) ** 2)

    # Return neutral + bonus (no penalties)
    return 1.0 + bonus


def get_elevation_decay_constant(route_type: str) -> float:
    """
    Get the elevation decay constant for a given route type.

    Useful for UI display, algorithm explanation, and testing.
    """
    return ELEVATION_DECAY_CONSTANT.get(
        route_type.lower(),
        ELEVATION_DECAY_CONSTANT["default"]
    )
