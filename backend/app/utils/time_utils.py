"""
Time and date utility functions for SafeAscent algorithm.

Provides seasonal calculations and temporal analysis functions.
"""
from datetime import date, datetime
from typing import List, Tuple

from app.services.algorithm_config import SEASONS, WITHIN_WINDOW_TEMPORAL_DECAY


def get_season(target_date: date) -> str:
    """
    Determine the season for a given date (Northern Hemisphere).

    Args:
        target_date: The date to check

    Returns:
        Season name: 'winter', 'spring', 'summer', or 'fall'

    Example:
        >>> from datetime import date
        >>> season = get_season(date(2024, 7, 15))
        >>> print(season)
        summer
    """
    month = target_date.month

    for season_name, months in SEASONS.items():
        if month in months:
            return season_name

    # Should never reach here, but default to summer
    return "summer"


def is_same_season(date1: date, date2: date) -> bool:
    """
    Check if two dates are in the same season.

    Used for seasonal boost in temporal weighting.

    Args:
        date1: First date
        date2: Second date

    Returns:
        True if both dates in same season, False otherwise

    Example:
        >>> from datetime import date
        >>> is_same_season(date(2024, 1, 15), date(2023, 2, 10))
        True  # Both winter
    """
    return get_season(date1) == get_season(date2)


def days_between(date1: date, date2: date) -> int:
    """
    Calculate the number of days between two dates.

    Args:
        date1: Earlier date
        date2: Later date

    Returns:
        Number of days (always positive)

    Example:
        >>> from datetime import date
        >>> days = days_between(date(2024, 1, 1), date(2024, 1, 15))
        >>> print(days)
        14
    """
    delta = abs((date2 - date1).days)
    return delta


def calculate_within_window_weights(
    num_days: int = 7,
    decay_factor: float = WITHIN_WINDOW_TEMPORAL_DECAY
) -> List[float]:
    """
    Calculate temporal weights for days within weather window.

    More recent days (closer to Day 0) get higher weights.
    Weights are normalized to sum to 1.0.

    Args:
        num_days: Number of days in window (default 7)
        decay_factor: Exponential decay factor (default from config)

    Returns:
        List of normalized weights, from oldest to most recent day

    Example:
        >>> weights = calculate_within_window_weights()
        >>> print(f"Day -6: {weights[0]:.3f}, Day 0: {weights[6]:.3f}")
        Day -6: 0.080, Day 0: 0.200
    """
    weights = []

    # Calculate raw weights (Day -6 to Day 0)
    for days_before in range(num_days - 1, -1, -1):
        weight = decay_factor ** days_before
        weights.append(weight)

    # Normalize to sum to 1.0
    total = sum(weights)
    normalized_weights = [w / total for w in weights]

    return normalized_weights


def count_freeze_thaw_cycles(
    daily_temps_c: List[Tuple[float, float, float]]
) -> int:
    """
    Count freeze-thaw cycles in a sequence of daily temperatures.

    A freeze-thaw cycle occurs when temperature crosses 0°C (32°F).
    Counts any day where min < 0 and max > 0.

    Args:
        daily_temps_c: List of (min_temp, avg_temp, max_temp) in Celsius

    Returns:
        Number of freeze-thaw days

    Example:
        >>> temps = [
        ...     (-5, -2, 2),   # Freeze-thaw: min < 0, max > 0
        ...     (-3, -1, 1),   # Freeze-thaw
        ...     (2, 5, 8),     # No freeze-thaw: all above 0
        ...     (-8, -5, -2),  # No freeze-thaw: all below 0
        ... ]
        >>> count_freeze_thaw_cycles(temps)
        2
    """
    freeze_thaw_count = 0

    for min_temp, avg_temp, max_temp in daily_temps_c:
        # Freeze-thaw if temperature crosses 0°C during the day
        if min_temp < 0 and max_temp > 0:
            freeze_thaw_count += 1

    return freeze_thaw_count


def celsius_to_fahrenheit(celsius: float) -> float:
    """
    Convert temperature from Celsius to Fahrenheit.

    Args:
        celsius: Temperature in Celsius

    Returns:
        Temperature in Fahrenheit
    """
    return (celsius * 9/5) + 32


def fahrenheit_to_celsius(fahrenheit: float) -> float:
    """
    Convert temperature from Fahrenheit to Celsius.

    Args:
        fahrenheit: Temperature in Fahrenheit

    Returns:
        Temperature in Celsius
    """
    return (fahrenheit - 32) * 5/9
