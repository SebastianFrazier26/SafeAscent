"""
SafeAscent Utility Functions

This module provides utility functions for the safety prediction algorithm:
- Geographic calculations (Haversine distance, bearings)
- Statistical calculations (correlation, z-scores)
- Time and date operations (seasons, freeze-thaw cycles)
"""

# Geographic utilities
from .geo_utils import (
    haversine_distance,
    calculate_bearing,
    get_bounding_box,
)

# Statistical utilities
from .stats_utils import (
    mean,
    std,
    pearson_correlation,
    weighted_pearson_correlation,
    z_score,
)

# Time and date utilities
from .time_utils import (
    get_season,
    is_same_season,
    days_between,
    calculate_within_window_weights,
    count_freeze_thaw_cycles,
    celsius_to_fahrenheit,
    fahrenheit_to_celsius,
)

__all__ = [
    # Geographic
    "haversine_distance",
    "calculate_bearing",
    "get_bounding_box",
    # Statistical
    "mean",
    "std",
    "pearson_correlation",
    "weighted_pearson_correlation",
    "z_score",
    # Time/Date
    "get_season",
    "is_same_season",
    "days_between",
    "calculate_within_window_weights",
    "count_freeze_thaw_cycles",
    "celsius_to_fahrenheit",
    "fahrenheit_to_celsius",
]
