"""
Severity Weighting Module - SafeAscent Safety Algorithm

Applies subtle boosts based on accident severity.
Philosophy: Fatal outcomes slightly more informative, but luck matters enormously.

Boosters are intentionally subtle (1.0× to 1.3×) to avoid over-weighting
rare catastrophic events vs. patterns of consistent hazards.
"""
from typing import Optional

from app.services.algorithm_config import (
    SEVERITY_BOOSTERS,
    DEFAULT_SEVERITY_WEIGHT,
)


def calculate_severity_weight(severity: str) -> float:
    """
    Calculate severity boost multiplier for an accident.

    Applies subtle linear boosters (not exponential) to avoid over-weighting
    fatal accidents. The pattern of accidents matters more than any single
    catastrophic event.

    Args:
        severity: Severity level ("fatal", "serious", "minor", "unknown")

    Returns:
        Weight multiplier from 1.0 to 1.3
        - fatal: 1.3× (30% boost)
        - serious: 1.1× (10% boost)
        - minor: 1.0× (baseline)
        - unknown: 1.0× (conservative)

    Example:
        >>> weight = calculate_severity_weight("fatal")
        >>> print(f"{weight}×")
        1.3×

        >>> weight = calculate_severity_weight("serious")
        >>> print(f"{weight}×")
        1.1×

        >>> weight = calculate_severity_weight("minor")
        >>> print(f"{weight}×")
        1.0×
    """
    # Normalize to lowercase
    severity_normalized = severity.lower() if severity else "unknown"

    # Look up booster
    weight = SEVERITY_BOOSTERS.get(severity_normalized, DEFAULT_SEVERITY_WEIGHT)

    return weight


def get_severity_explanation(severity: str) -> str:
    """
    Get human-readable explanation of severity boosting.

    Args:
        severity: Severity level

    Returns:
        String explanation of how severity affects weighting

    Example:
        >>> explanation = get_severity_explanation("fatal")
        >>> print(explanation)
        "Fatal: 1.3× boost - Higher signal about conditions"

        >>> explanation = get_severity_explanation("minor")
        >>> print(explanation)
        "Minor: 1.0× baseline - Still reveals dangerous conditions"
    """
    weight = calculate_severity_weight(severity)
    severity_normalized = severity.lower() if severity else "unknown"

    explanations = {
        "fatal": "Fatal: 1.3× boost - Higher signal about conditions",
        "serious": "Serious: 1.1× boost - Typical accident in dataset",
        "minor": "Minor: 1.0× baseline - Still reveals dangerous conditions",
        "unknown": "Unknown: 1.0× baseline - Conservative (avoid bias)",
    }

    return explanations.get(
        severity_normalized,
        f"{severity.title()}: {weight}× - Custom severity level",
    )


def normalize_severity(severity_raw: Optional[str]) -> str:
    """
    Normalize raw severity strings to standard levels.

    Handles common variations and maps them to standard categories.

    Args:
        severity_raw: Raw severity string from accident report

    Returns:
        Normalized severity: "fatal", "serious", "minor", or "unknown"

    Example:
        >>> normalized = normalize_severity("FATAL")
        >>> print(normalized)
        "fatal"

        >>> normalized = normalize_severity("death")
        >>> print(normalized)
        "fatal"

        >>> normalized = normalize_severity("injured")
        >>> print(normalized)
        "serious"

        >>> normalized = normalize_severity(None)
        >>> print(normalized)
        "unknown"
    """
    if not severity_raw:
        return "unknown"

    severity_lower = severity_raw.lower().strip()

    # Map common variations to standard levels
    fatal_terms = {"fatal", "death", "fatality", "died", "killed"}
    serious_terms = {
        "serious",
        "severe",
        "critical",
        "injured",
        "injury",
        "hospitalized",
    }
    minor_terms = {"minor", "slight", "light", "uninjured", "no injury"}

    # Check each category
    if any(term in severity_lower for term in fatal_terms):
        return "fatal"
    elif any(term in severity_lower for term in serious_terms):
        return "serious"
    elif any(term in severity_lower for term in minor_terms):
        return "minor"
    else:
        # Unknown or unrecognized
        return "unknown"


def get_all_severity_weights() -> dict:
    """
    Get all severity weights for reference.

    Returns:
        Dictionary mapping severity levels to their weights

    Example:
        >>> weights = get_all_severity_weights()
        >>> for level, weight in sorted(weights.items()):
        ...     print(f"{level}: {weight}×")
        fatal: 1.3×
        minor: 1.0×
        serious: 1.1×
        unknown: 1.0×
    """
    return SEVERITY_BOOSTERS.copy()
