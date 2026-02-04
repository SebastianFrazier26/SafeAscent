"""
Route Type Weighting Module - SafeAscent Safety Algorithm

Calculates route type similarity using an asymmetric matrix.
Key insight: Alpine climbers care about sport accidents in bad weather (canary effect).

Example: If planning alpine route, sport accident with bad weather → 0.9× weight
         If planning sport route, alpine accident → 0.3× weight (less relevant)
"""

from app.services.algorithm_config import (
    ROUTE_TYPE_WEIGHTS,
    DEFAULT_ROUTE_TYPE_WEIGHT,
)


def calculate_route_type_weight(
    planning_route_type: str,
    accident_route_type: str,
) -> float:
    """
    Calculate route type similarity weight (asymmetric).

    Uses asymmetric matrix where weight depends on both the planned route type
    AND the accident route type. Direction matters!

    Args:
        planning_route_type: Type of route being planned
        accident_route_type: Type of route where accident occurred

    Returns:
        Weight from 0.0 to 1.0 (1.0 = direct match, lower = less relevant)

    Example:
        >>> # Planning alpine, accident on sport route
        >>> weight = calculate_route_type_weight("alpine", "sport")
        >>> print(f"{weight}")
        0.9  # High relevance: canary effect

        >>> # Planning sport, accident on alpine route
        >>> weight = calculate_route_type_weight("sport", "alpine")
        >>> print(f"{weight}")
        0.3  # Low relevance: different hazards

        >>> # Direct match
        >>> weight = calculate_route_type_weight("alpine", "alpine")
        >>> print(f"{weight}")
        1.0  # Perfect match
    """
    # Normalize to lowercase
    planning_type = planning_route_type.lower() if planning_route_type else "default"
    accident_type = accident_route_type.lower() if accident_route_type else "default"

    # Look up in asymmetric matrix
    key = (planning_type, accident_type)
    weight = ROUTE_TYPE_WEIGHTS.get(key, DEFAULT_ROUTE_TYPE_WEIGHT)

    return weight


def get_route_type_relevance_explanation(
    planning_route_type: str,
    accident_route_type: str,
) -> str:
    """
    Get a human-readable explanation of route type relevance.

    Useful for UI display and algorithm transparency.

    Args:
        planning_route_type: Type of route being planned
        accident_route_type: Type of route where accident occurred

    Returns:
        String explanation of relevance level

    Example:
        >>> explanation = get_route_type_relevance_explanation("alpine", "sport")
        >>> print(explanation)
        "Very High: Sport accidents strongly signal alpine conditions (canary effect)"

        >>> explanation = get_route_type_relevance_explanation("sport", "alpine")
        >>> print(explanation)
        "Low: Alpine hazards less relevant to sport climbing"
    """
    weight = calculate_route_type_weight(planning_route_type, accident_route_type)

    # Generate explanation based on weight
    if weight >= 0.9:
        level = "Very High"
    elif weight >= 0.7:
        level = "High"
    elif weight >= 0.5:
        level = "Moderate"
    elif weight >= 0.3:
        level = "Low"
    else:
        level = "Very Low"

    # Special case explanations
    planning_type = planning_route_type.lower() if planning_route_type else "default"
    accident_type = accident_route_type.lower() if accident_route_type else "default"

    if planning_type == accident_type:
        reason = "Direct route type match"
    elif planning_type == "alpine" and accident_type == "sport" and weight >= 0.9:
        reason = "Sport accidents strongly signal alpine conditions (canary effect)"
    elif planning_type == "alpine" and accident_type in ["ice", "mixed"]:
        reason = "Ice/mixed climbing shares alpine hazards"
    elif planning_type in ["trad", "aid"] and accident_type in ["trad", "aid"]:
        reason = "Similar protection and hazard profiles"
    elif weight >= 0.7:
        reason = "Similar climbing hazards and conditions"
    elif weight >= 0.5:
        reason = "Some shared hazards and conditions"
    elif weight >= 0.3:
        reason = "Different hazard profiles, some overlap"
    else:
        reason = "Very different climbing disciplines"

    return f"{level}: {reason}"


def get_all_route_type_weights(planning_route_type: str) -> dict:
    """
    Get all route type weights for a given planning route type.

    Returns a dictionary of accident_type → weight for all possible accident types.
    Useful for UI display and algorithm explanation.

    Args:
        planning_route_type: Type of route being planned

    Returns:
        Dictionary mapping accident route types to their weights

    Example:
        >>> weights = get_all_route_type_weights("alpine")
        >>> for accident_type, weight in sorted(weights.items()):
        ...     print(f"{accident_type}: {weight}")
        aid: 0.6
        alpine: 1.0
        boulder: 0.3
        ice: 0.8
        mixed: 0.9
        sport: 0.9
        trad: 0.8
    """
    planning_type = planning_route_type.lower() if planning_route_type else "default"

    # All possible route types
    route_types = ["alpine", "ice", "mixed", "trad", "sport", "aid", "boulder"]

    weights = {}
    for accident_type in route_types:
        key = (planning_type, accident_type)
        weight = ROUTE_TYPE_WEIGHTS.get(key, DEFAULT_ROUTE_TYPE_WEIGHT)
        weights[accident_type] = weight

    return weights


def is_canary_effect_applicable(
    planning_route_type: str,
    accident_route_type: str,
) -> bool:
    """
    Check if the "canary effect" applies to this route type pair.

    The canary effect is when less exposed climbing (sport, trad) reveals
    dangerous conditions for more exposed climbing (alpine, ice, mixed).

    Args:
        planning_route_type: Type of route being planned
        accident_route_type: Type of route where accident occurred

    Returns:
        True if canary effect applies, False otherwise

    Example:
        >>> is_canary = is_canary_effect_applicable("alpine", "sport")
        >>> print(is_canary)
        True

        >>> is_canary = is_canary_effect_applicable("sport", "alpine")
        >>> print(is_canary)
        False
    """
    planning_type = planning_route_type.lower() if planning_route_type else ""
    accident_type = accident_route_type.lower() if accident_route_type else ""

    # Canary effect: planning exposed routes, accident on less exposed routes
    exposed_routes = {"alpine", "ice", "mixed"}
    less_exposed_routes = {"sport", "trad", "aid"}

    return (
        planning_type in exposed_routes
        and accident_type in less_exposed_routes
        and calculate_route_type_weight(planning_type, accident_type) >= 0.7
    )
