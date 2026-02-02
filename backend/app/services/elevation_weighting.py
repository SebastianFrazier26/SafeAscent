"""
Elevation Weighting Module - SafeAscent Safety Algorithm

Calculates elevation influence using asymmetric weighting:
- Accidents at same or LOWER elevation: Full weight (1.0)
- Accidents at HIGHER elevation: Reduced weight (Gaussian decay)

Rationale: If conditions are dangerous at lower elevation, they're relevant
at higher elevation. But altitude-specific hazards (thin air, altitude sickness)
at higher elevations don't apply at lower elevations.

Route-type-specific decay constants reflect varying sensitivity to altitude effects.
"""
import math
from typing import Optional

from app.services.algorithm_config import (
    ELEVATION_DECAY_CONSTANT,
)


def calculate_elevation_weight(
    route_elevation_m: Optional[float],
    accident_elevation_m: Optional[float],
    route_type: str = "default",
) -> float:
    """
    Calculate elevation weight for an accident relative to planned route.

    Asymmetric weighting:
    - Accident at same/lower elevation: weight = 1.0
    - Accident at higher elevation: weight decays with elevation difference

    Args:
        route_elevation_m: Planned route elevation in meters (None = neutral weight)
        accident_elevation_m: Accident elevation in meters (None = neutral weight)
        route_type: Type of climbing route (affects decay rate)

    Returns:
        Elevation weight from 0.0 to 1.0 (1.0 = same relevance)

    Example:
        >>> # Alpine route at 4000m, accident at 3000m (lower)
        >>> weight = calculate_elevation_weight(4000, 3000, "alpine")
        >>> print(f"{weight:.2f}")
        1.00  # Full weight - dangerous lower = relevant higher

        >>> # Alpine route at 3000m, accident at 4000m (1000m higher)
        >>> weight = calculate_elevation_weight(3000, 4000, "alpine")
        >>> print(f"{weight:.2f}")
        0.29  # Reduced weight - altitude effects don't apply lower

        >>> # Sport route at 3000m, accident at 4000m (less sensitive)
        >>> weight = calculate_elevation_weight(3000, 4000, "sport")
        >>> print(f"{weight:.2f}")
        0.76  # Sport less affected by elevation
    """
    # Handle missing elevation data - return neutral weight
    if route_elevation_m is None or accident_elevation_m is None:
        return 1.0

    # Calculate elevation difference
    elevation_diff = accident_elevation_m - route_elevation_m

    # Accident at same or lower elevation: full weight
    if elevation_diff <= 0:
        return 1.0

    # Accident at higher elevation: apply decay
    # Get route-type-specific decay constant
    decay_constant = ELEVATION_DECAY_CONSTANT.get(
        route_type.lower(),
        ELEVATION_DECAY_CONSTANT["default"]
    )

    # Gaussian decay formula: exp(-(diff / decay_constant)²)
    weight = math.exp(-(elevation_diff / decay_constant) ** 2)

    return weight


def get_elevation_decay_constant(route_type: str) -> float:
    """
    Get the elevation decay constant for a given route type.

    Useful for UI display, algorithm explanation, and testing.

    Args:
        route_type: Type of climbing route

    Returns:
        Elevation decay constant in meters

    Example:
        >>> decay = get_elevation_decay_constant("alpine")
        >>> print(f"{decay}m")
        800m

        >>> decay = get_elevation_decay_constant("boulder")
        >>> print(f"{decay}m")
        3000m
    """
    return ELEVATION_DECAY_CONSTANT.get(
        route_type.lower(),
        ELEVATION_DECAY_CONSTANT["default"]
    )
