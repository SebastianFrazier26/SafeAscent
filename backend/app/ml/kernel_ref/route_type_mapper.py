"""
Route Type Mapping Utility

Maps accident database fields (activity, accident_type, tags) to standardized
route types used by the safety algorithm (alpine, ice, mixed, trad, sport, aid, boulder).

This is necessary because the accident database uses different terminology than
our algorithm's route type classification.
"""
from typing import Optional


def infer_route_type_from_accident(
    activity: Optional[str],
    accident_type: Optional[str],
    tags: Optional[str],
) -> str:
    """
    Infer route type from accident database fields.

    Uses a priority system:
    1. Tags field (most specific) - e.g., "Alpine/Mountaineering", "Ice Climbing"
    2. Accident type - e.g., "ice_climbing", "avalanche"
    3. Activity field - e.g., "Climbing", "Backcountry Tourer"
    4. Default to "default" if no match

    Args:
        activity: Activity field from accidents table
        accident_type: Accident type field
        tags: Comma-separated tags field

    Returns:
        Standardized route type: alpine, ice, mixed, trad, sport, aid, boulder, or default

    Example:
        >>> infer_route_type_from_accident(
        ...     activity="Climbing",
        ...     accident_type="ice_climbing",
        ...     tags="Ice Climbing, Alpine/Mountaineering"
        ... )
        'ice'

        >>> infer_route_type_from_accident(
        ...     activity="Climbing",
        ...     accident_type="fall",
        ...     tags="Sport Climbing, grade:5.11"
        ... )
        'sport'
    """
    # Normalize inputs to lowercase for matching
    tags_lower = (tags or "").lower()
    accident_type_lower = (accident_type or "").lower()
    activity_lower = (activity or "").lower()

    # Priority 1: Check tags (most specific information)
    if tags_lower:
        # Ice climbing
        if "ice climbing" in tags_lower or "ice climb" in tags_lower:
            return "ice"

        # Mixed climbing
        if "mixed climbing" in tags_lower or "mixed climb" in tags_lower:
            return "mixed"

        # Alpine/Mountaineering
        if "alpine" in tags_lower or "mountaineering" in tags_lower:
            return "alpine"

        # Sport climbing
        if "sport climbing" in tags_lower or "sport climb" in tags_lower:
            return "sport"

        # Traditional climbing
        if "trad" in tags_lower or "traditional climbing" in tags_lower:
            return "trad"

        # Aid climbing
        if "aid climbing" in tags_lower or "aid climb" in tags_lower:
            return "aid"

        # Bouldering
        if "boulder" in tags_lower:
            return "boulder"

        # Check for climbing grades to infer type
        # 5.10+ grades are often sport, lower grades often trad (rough heuristic)
        if "grade:" in tags_lower:
            # Extract grade if possible
            if any(f"5.{i}" in tags_lower for i in range(11, 16)):
                # High grades (5.11-5.15) → likely sport
                # But if explicitly mentions "trad", override
                if "trad" not in tags_lower:
                    return "sport"
            elif any(f"5.{i}" in tags_lower for i in range(1, 11)):
                # Lower grades (5.1-5.10) → could be trad or sport
                # Default to trad unless sport is mentioned
                if "sport" not in tags_lower:
                    return "trad"

        # Check for "Roped" - suggests trad or sport climbing
        if "roped" in tags_lower:
            # If no other specifics, default to trad (conservative)
            return "trad"

        # Unroped could mean bouldering or soloing
        if "unroped" in tags_lower and "solo" in tags_lower:
            # Solo climbing, likely alpine or trad
            return "alpine"

    # Priority 2: Check accident_type
    if accident_type_lower:
        if "ice_climbing" in accident_type_lower or "ice" in accident_type_lower:
            return "ice"

        if "avalanche" in accident_type_lower:
            # Avalanches are typically alpine/backcountry
            return "alpine"

        if "rockfall" in accident_type_lower:
            # Rockfall is common in alpine terrain
            return "alpine"

        if "roped_climbing" in accident_type_lower:
            # Generic roped climbing - default to trad
            return "trad"

        if "rappel" in accident_type_lower:
            # Rappelling happens in all disciplines, default to trad
            return "trad"

        if "solo" in accident_type_lower:
            # Soloing could be alpine or sport/trad
            return "alpine"

    # Priority 3: Check activity field
    if activity_lower:
        if "backcountry" in activity_lower:
            # Backcountry touring → alpine
            return "alpine"

        if "climber" in activity_lower or "climbing" in activity_lower:
            # Generic "climbing" - default to trad (most common)
            return "trad"

        if "mountaineer" in activity_lower:
            return "alpine"

        if "canyoneering" in activity_lower:
            # Canyoneering shares some characteristics with trad
            return "trad"

        # Non-climbing activities → default
        if any(
            term in activity_lower
            for term in ["hiker", "motorist", "rescuer", "ski", "rider"]
        ):
            # These aren't climbing accidents, but they're in our database
            # Default to "alpine" for backcountry/mountain activities
            if "backcountry" in activity_lower or "ski" in activity_lower:
                return "alpine"
            else:
                return "default"

    # No matches - return default
    return "default"


def get_route_type_confidence(
    activity: Optional[str],
    accident_type: Optional[str],
    tags: Optional[str],
) -> float:
    """
    Get confidence level for route type inference (0.0 to 1.0).

    Higher confidence when we have specific tags, lower when inferring from
    generic fields.

    Args:
        activity: Activity field
        accident_type: Accident type field
        tags: Tags field

    Returns:
        Confidence from 0.0 (pure guess) to 1.0 (explicit match)

    Example:
        >>> get_route_type_confidence(
        ...     activity="Climbing",
        ...     accident_type="ice_climbing",
        ...     tags="Ice Climbing, Alpine/Mountaineering"
        ... )
        0.95  # High confidence from explicit tags

        >>> get_route_type_confidence(
        ...     activity="Climbing",
        ...     accident_type="fall",
        ...     tags=None
        ... )
        0.3  # Low confidence from generic fields
    """
    tags_lower = (tags or "").lower()
    accident_type_lower = (accident_type or "").lower()
    activity_lower = (activity or "").lower()

    # High confidence: Explicit route type in tags
    explicit_types = [
        "ice climbing",
        "sport climbing",
        "trad",
        "alpine",
        "mountaineering",
        "mixed climbing",
        "aid climbing",
        "boulder",
    ]
    if any(t in tags_lower for t in explicit_types):
        return 0.95

    # Medium-high confidence: Specific accident type
    if "ice_climbing" in accident_type_lower or "ice" in accident_type_lower:
        return 0.85
    if "avalanche" in accident_type_lower:
        return 0.80

    # Medium confidence: Grade information in tags
    if "grade:" in tags_lower or "roped" in tags_lower:
        return 0.60

    # Low-medium confidence: Generic accident type
    if accident_type_lower and accident_type_lower != "unknown":
        return 0.50

    # Low confidence: Only activity field
    if "backcountry" in activity_lower or "mountaineer" in activity_lower:
        return 0.60
    if "climber" in activity_lower or "climbing" in activity_lower:
        return 0.40

    # Very low confidence: No useful information
    return 0.20


# Quick reference for common mappings (for documentation)
ROUTE_TYPE_EXAMPLES = {
    "alpine": [
        "Alpine/Mountaineering",
        "Backcountry Tourer",
        "avalanche",
        "High altitude",
    ],
    "ice": ["Ice Climbing", "ice_climbing", "Ice climb"],
    "mixed": ["Mixed Climbing", "Mixed climb"],
    "trad": [
        "Traditional Climbing",
        "Trad",
        "roped_climbing",
        "Roped",
        "grade:5.8",
        "Climber",
    ],
    "sport": ["Sport Climbing", "Sport climb", "grade:5.12"],
    "aid": ["Aid Climbing", "Aid climb"],
    "boulder": ["Boulder", "Bouldering", "Unroped + not solo"],
    "default": ["Unknown", "No clear indicators"],
}
