"""
Weather Similarity Module - SafeAscent Safety Algorithm

Calculates weather pattern similarity using correlation-based approach.
Compares 7-day weather patterns between current conditions and historical accidents.

Six factors weighted equally:
- Temperature pattern
- Precipitation pattern
- Wind speed pattern
- Visibility pattern
- Cloud cover pattern
- Freeze-thaw cycles

Mathematical basis: Pearson correlation + extreme weather amplification
"""
from typing import List, Tuple, Optional, Dict

from app.services.algorithm_config import (
    WEATHER_FACTOR_WEIGHTS,
    EXTREME_WEATHER_SD_THRESHOLD,
    EXTREME_PENALTY_MULTIPLIERS,
    WITHIN_WINDOW_TEMPORAL_DECAY,
    MIN_WEATHER_DAYS_REQUIRED,
)
from app.utils.stats_utils import (
    weighted_pearson_correlation,
    z_score,
    mean,
)
from app.utils.time_utils import (
    calculate_within_window_weights,
    count_freeze_thaw_cycles,
)


class WeatherPattern:
    """
    Represents a 7-day weather pattern for correlation matching.

    Attributes:
        temperature: List of 7 daily average temperatures (Celsius)
        precipitation: List of 7 daily precipitation amounts (mm)
        wind_speed: List of 7 daily average wind speeds (m/s)
        visibility: List of 7 daily visibility values (meters)
        cloud_cover: List of 7 daily cloud cover percentages (0-100)
        daily_temps: List of 7 tuples of (min, avg, max) temps for freeze-thaw
        num_days: Number of valid days (< 7 if some data missing)
    """

    def __init__(
        self,
        temperature: List[float],
        precipitation: List[float],
        wind_speed: List[float],
        visibility: List[float],
        cloud_cover: List[float],
        daily_temps: List[Tuple[float, float, float]],
    ):
        """
        Initialize weather pattern.

        Args:
            temperature: 7 daily avg temps (Celsius)
            precipitation: 7 daily precip amounts (mm)
            wind_speed: 7 daily avg wind speeds (m/s)
            visibility: 7 daily visibility (meters)
            cloud_cover: 7 daily cloud cover (0-100%)
            daily_temps: 7 tuples of (min, avg, max) temps for freeze-thaw
        """
        self.temperature = temperature
        self.precipitation = precipitation
        self.wind_speed = wind_speed
        self.visibility = visibility
        self.cloud_cover = cloud_cover
        self.daily_temps = daily_temps
        self.num_days = len(temperature)

    def is_valid(self) -> bool:
        """Check if pattern has enough data for matching."""
        return self.num_days >= MIN_WEATHER_DAYS_REQUIRED

    def get_freeze_thaw_count(self) -> int:
        """Calculate freeze-thaw cycles in this pattern."""
        return count_freeze_thaw_cycles(self.daily_temps)


def calculate_weather_similarity(
    current_pattern: WeatherPattern,
    accident_pattern: WeatherPattern,
    historical_stats: Optional[Dict[str, Tuple[float, float]]] = None,
) -> float:
    """
    Calculate weather similarity between current conditions and accident pattern.

    Uses weighted Pearson correlation for pattern matching (values closer to Day 0
    get higher weight). Detects extreme weather and amplifies risk accordingly.

    Args:
        current_pattern: Current 7-day weather pattern
        accident_pattern: Historical 7-day pattern from accident
        historical_stats: Dict of {factor: (mean, std)} for extreme detection
                         If None, extreme weather detection is skipped

    Returns:
        Weather similarity weight from 0.0 to ~2.0+
        - Base correlation: 0.0 to 1.0 (normalized from -1 to +1 Pearson)
        - Extreme weather can amplify beyond 1.0

    Example:
        >>> current = WeatherPattern(
        ...     temperature=[45, 47, 49, 51, 53, 55, 57],
        ...     precipitation=[0, 0, 0, 5, 10, 0, 0],
        ...     wind_speed=[5, 6, 8, 12, 10, 7, 5],
        ...     visibility=[10000] * 7,
        ...     cloud_cover=[20, 30, 50, 80, 70, 40, 30],
        ...     daily_temps=[(40, 45, 50), ...],  # 7 tuples
        ... )
        >>> accident = WeatherPattern(...similar pattern...)
        >>> similarity = calculate_weather_similarity(current, accident)
        >>> print(f"{similarity:.3f}")
        0.847  # High similarity
    """
    # Validate both patterns
    if not current_pattern.is_valid() or not accident_pattern.is_valid():
        return 0.0

    # Calculate within-window temporal weights (more recent days weighted higher)
    num_days = min(current_pattern.num_days, accident_pattern.num_days)
    weights = calculate_within_window_weights(
        num_days=num_days,
        decay_factor=WITHIN_WINDOW_TEMPORAL_DECAY,
    )

    # Calculate correlation for each of the 6 factors
    factor_scores = {}

    # 1. Temperature correlation
    try:
        temp_corr = weighted_pearson_correlation(
            current_pattern.temperature[:num_days],
            accident_pattern.temperature[:num_days],
            weights,
        )
        # Normalize from [-1, 1] to [0, 1]
        factor_scores["temperature"] = (temp_corr + 1) / 2
    except (ValueError, ZeroDivisionError):
        factor_scores["temperature"] = 0.0

    # 2. Precipitation correlation
    try:
        precip_corr = weighted_pearson_correlation(
            current_pattern.precipitation[:num_days],
            accident_pattern.precipitation[:num_days],
            weights,
        )
        factor_scores["precipitation"] = (precip_corr + 1) / 2
    except (ValueError, ZeroDivisionError):
        factor_scores["precipitation"] = 0.0

    # 3. Wind speed correlation
    try:
        wind_corr = weighted_pearson_correlation(
            current_pattern.wind_speed[:num_days],
            accident_pattern.wind_speed[:num_days],
            weights,
        )
        factor_scores["wind_speed"] = (wind_corr + 1) / 2
    except (ValueError, ZeroDivisionError):
        factor_scores["wind_speed"] = 0.0

    # 4. Visibility correlation
    try:
        vis_corr = weighted_pearson_correlation(
            current_pattern.visibility[:num_days],
            accident_pattern.visibility[:num_days],
            weights,
        )
        factor_scores["visibility"] = (vis_corr + 1) / 2
    except (ValueError, ZeroDivisionError):
        factor_scores["visibility"] = 0.0

    # 5. Cloud cover correlation
    try:
        cloud_corr = weighted_pearson_correlation(
            current_pattern.cloud_cover[:num_days],
            accident_pattern.cloud_cover[:num_days],
            weights,
        )
        factor_scores["cloud_cover"] = (cloud_corr + 1) / 2
    except (ValueError, ZeroDivisionError):
        factor_scores["cloud_cover"] = 0.0

    # 6. Freeze-thaw cycle similarity
    current_ft = current_pattern.get_freeze_thaw_count()
    accident_ft = accident_pattern.get_freeze_thaw_count()

    # Normalize freeze-thaw similarity: 1.0 if match, decays with difference
    max_ft = max(current_ft, accident_ft, 1)  # Avoid division by zero
    ft_similarity = 1.0 - abs(current_ft - accident_ft) / (max_ft + 7)  # +7 to smooth
    factor_scores["freeze_thaw"] = max(0.0, ft_similarity)

    # Calculate weighted average of all 6 factors (equal weighting)
    base_similarity = sum(
        factor_scores[factor] * WEATHER_FACTOR_WEIGHTS[factor]
        for factor in WEATHER_FACTOR_WEIGHTS.keys()
    )

    # Apply extreme weather amplification if historical stats provided
    if historical_stats is not None:
        extreme_multiplier = calculate_extreme_weather_multiplier(
            current_pattern, historical_stats
        )
        final_similarity = base_similarity * extreme_multiplier
    else:
        final_similarity = base_similarity

    return final_similarity


def calculate_extreme_weather_multiplier(
    current_pattern: WeatherPattern,
    historical_stats: Dict[str, Tuple[float, float]],
) -> float:
    """
    Calculate risk multiplier based on extreme weather conditions.

    If current weather is >2.0 SD above mean, amplify risk proportionally.

    Args:
        current_pattern: Current weather pattern
        historical_stats: Dict of {factor: (mean, std)} for location/season

    Returns:
        Multiplier >= 1.0 (1.0 = normal, >1.0 = extreme weather amplification)

    Example:
        >>> # Normal weather
        >>> multiplier = calculate_extreme_weather_multiplier(
        ...     pattern, {"wind_speed": (10.0, 5.0), ...}
        ... )
        >>> print(multiplier)
        1.0  # No amplification

        >>> # Extreme winds: current=30, mean=10, std=5 → 4.0 SD
        >>> multiplier = calculate_extreme_weather_multiplier(
        ...     extreme_pattern, {"wind_speed": (10.0, 5.0), ...}
        ... )
        >>> print(multiplier)
        1.4  # 40% amplification (2.0 SD × 20% per SD)
    """
    multiplier = 1.0

    # Check each factor for extremes
    factors_to_check = {
        "wind_speed": mean(current_pattern.wind_speed),
        "precipitation": mean(current_pattern.precipitation),
        "temperature": mean(current_pattern.temperature),
        "visibility": mean(current_pattern.visibility),
    }

    for factor, current_value in factors_to_check.items():
        if factor not in historical_stats:
            continue

        mean_val, std_val = historical_stats[factor]

        # Skip if no variance
        if std_val == 0:
            continue

        # Calculate z-score
        try:
            z = z_score(current_value, mean_val, std_val)
        except ValueError:
            continue

        # Check if extreme (beyond threshold)
        if abs(z) > EXTREME_WEATHER_SD_THRESHOLD:
            # Calculate how many SDs beyond threshold
            sds_beyond = abs(z) - EXTREME_WEATHER_SD_THRESHOLD

            # Get penalty multiplier for this factor
            penalty_per_sd = EXTREME_PENALTY_MULTIPLIERS.get(factor, 0.20)

            # Add to multiplier
            multiplier += sds_beyond * penalty_per_sd

    return multiplier


def calculate_weather_similarity_detailed(
    current_pattern: WeatherPattern,
    accident_pattern: WeatherPattern,
    historical_stats: Optional[Dict[str, Tuple[float, float]]] = None,
) -> Dict:
    """
    Calculate weather similarity with detailed breakdown.

    Returns dictionary with all components for UI display and debugging.

    Args:
        current_pattern: Current 7-day weather pattern
        accident_pattern: Historical 7-day pattern from accident
        historical_stats: Dict of {factor: (mean, std)} for extreme detection

    Returns:
        Dictionary with keys:
        - 'final_similarity': float (overall weather weight)
        - 'base_similarity': float (before extreme amplification)
        - 'extreme_multiplier': float (>= 1.0)
        - 'factor_scores': dict of {factor: normalized_correlation}
        - 'freeze_thaw_current': int
        - 'freeze_thaw_accident': int
        - 'num_days_compared': int

    Example:
        >>> result = calculate_weather_similarity_detailed(current, accident)
        >>> print(result['final_similarity'])
        0.847
        >>> print(result['factor_scores'])
        {'temperature': 0.92, 'precipitation': 0.78, ...}
    """
    # Validate patterns
    if not current_pattern.is_valid() or not accident_pattern.is_valid():
        return {
            "final_similarity": 0.0,
            "base_similarity": 0.0,
            "extreme_multiplier": 1.0,
            "factor_scores": {},
            "freeze_thaw_current": 0,
            "freeze_thaw_accident": 0,
            "num_days_compared": 0,
        }

    num_days = min(current_pattern.num_days, accident_pattern.num_days)
    weights = calculate_within_window_weights(num_days, WITHIN_WINDOW_TEMPORAL_DECAY)

    # Calculate all factor scores (same as main function)
    factor_scores = {}

    # Temperature
    try:
        temp_corr = weighted_pearson_correlation(
            current_pattern.temperature[:num_days],
            accident_pattern.temperature[:num_days],
            weights,
        )
        factor_scores["temperature"] = (temp_corr + 1) / 2
    except Exception:
        factor_scores["temperature"] = 0.0

    # Precipitation
    try:
        precip_corr = weighted_pearson_correlation(
            current_pattern.precipitation[:num_days],
            accident_pattern.precipitation[:num_days],
            weights,
        )
        factor_scores["precipitation"] = (precip_corr + 1) / 2
    except Exception:
        factor_scores["precipitation"] = 0.0

    # Wind
    try:
        wind_corr = weighted_pearson_correlation(
            current_pattern.wind_speed[:num_days],
            accident_pattern.wind_speed[:num_days],
            weights,
        )
        factor_scores["wind_speed"] = (wind_corr + 1) / 2
    except Exception:
        factor_scores["wind_speed"] = 0.0

    # Visibility
    try:
        vis_corr = weighted_pearson_correlation(
            current_pattern.visibility[:num_days],
            accident_pattern.visibility[:num_days],
            weights,
        )
        factor_scores["visibility"] = (vis_corr + 1) / 2
    except Exception:
        factor_scores["visibility"] = 0.0

    # Cloud cover
    try:
        cloud_corr = weighted_pearson_correlation(
            current_pattern.cloud_cover[:num_days],
            accident_pattern.cloud_cover[:num_days],
            weights,
        )
        factor_scores["cloud_cover"] = (cloud_corr + 1) / 2
    except Exception:
        factor_scores["cloud_cover"] = 0.0

    # Freeze-thaw
    current_ft = current_pattern.get_freeze_thaw_count()
    accident_ft = accident_pattern.get_freeze_thaw_count()
    max_ft = max(current_ft, accident_ft, 1)
    ft_similarity = 1.0 - abs(current_ft - accident_ft) / (max_ft + 7)
    factor_scores["freeze_thaw"] = max(0.0, ft_similarity)

    # Base similarity
    base_similarity = sum(
        factor_scores[factor] * WEATHER_FACTOR_WEIGHTS[factor]
        for factor in WEATHER_FACTOR_WEIGHTS.keys()
    )

    # Extreme multiplier
    if historical_stats is not None:
        extreme_multiplier = calculate_extreme_weather_multiplier(
            current_pattern, historical_stats
        )
    else:
        extreme_multiplier = 1.0

    final_similarity = base_similarity * extreme_multiplier

    return {
        "final_similarity": final_similarity,
        "base_similarity": base_similarity,
        "extreme_multiplier": extreme_multiplier,
        "factor_scores": factor_scores,
        "freeze_thaw_current": current_ft,
        "freeze_thaw_accident": accident_ft,
        "num_days_compared": num_days,
    }
