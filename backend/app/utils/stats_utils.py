"""
Statistical utility functions for SafeAscent algorithm.

Provides statistical calculations for weather similarity and confidence scoring.
"""
import math
from typing import List


def mean(values: List[float]) -> float:
    """
    Calculate the arithmetic mean of a list of values.

    Args:
        values: List of numeric values

    Returns:
        Mean value

    Raises:
        ValueError: If values list is empty
    """
    if not values:
        raise ValueError("Cannot calculate mean of empty list")
    return sum(values) / len(values)


def std(values: List[float], sample: bool = True) -> float:
    """
    Calculate the standard deviation of a list of values.

    Args:
        values: List of numeric values
        sample: If True, use sample std dev (n-1), else population (n)

    Returns:
        Standard deviation

    Raises:
        ValueError: If values list has fewer than 2 elements (for sample=True)
    """
    if not values:
        raise ValueError("Cannot calculate std of empty list")
    if sample and len(values) < 2:
        raise ValueError("Need at least 2 values for sample standard deviation")

    mu = mean(values)
    variance = sum((x - mu) ** 2 for x in values) / (len(values) - (1 if sample else 0))
    return math.sqrt(variance)


def pearson_correlation(x: List[float], y: List[float]) -> float:
    """
    Calculate Pearson correlation coefficient between two sequences.

    Returns value from -1 (perfect negative correlation) to +1 (perfect positive).
    Used for weather pattern similarity matching.

    Args:
        x: First sequence of values
        y: Second sequence of values (must be same length as x)

    Returns:
        Pearson correlation coefficient (-1 to +1)

    Raises:
        ValueError: If sequences have different lengths or < 2 elements

    Example:
        >>> temps_current = [45, 47, 49, 51, 53, 55, 57]  # Warming trend
        >>> temps_accident = [57, 55, 53, 51, 49, 47, 45]  # Cooling trend
        >>> corr = pearson_correlation(temps_current, temps_accident)
        >>> print(f"{corr:.2f}")
        -1.00  # Perfect negative correlation
    """
    if len(x) != len(y):
        raise ValueError("Sequences must have same length")
    if len(x) < 2:
        raise ValueError("Need at least 2 data points for correlation")

    # Handle edge case: zero variance in either sequence
    try:
        std_x = std(x, sample=False)
        std_y = std(y, sample=False)
    except (ValueError, ZeroDivisionError):
        # If either sequence has zero variance, correlation is undefined
        # Return 1.0 if both sequences are identical constants, else 0.0
        if len(set(x)) == 1 and len(set(y)) == 1:
            return 1.0 if x[0] == y[0] else 0.0
        return 0.0

    if std_x == 0 or std_y == 0:
        # One sequence has variance, the other doesn't
        return 0.0

    # Calculate means
    mean_x = mean(x)
    mean_y = mean(y)

    # Calculate correlation
    n = len(x)
    numerator = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    denominator = n * std_x * std_y

    correlation = numerator / denominator if denominator != 0 else 0.0

    # Clamp to [-1, 1] to handle floating point errors
    return max(-1.0, min(1.0, correlation))


def weighted_pearson_correlation(
    x: List[float],
    y: List[float],
    weights: List[float]
) -> float:
    """
    Calculate weighted Pearson correlation coefficient.

    Used for within-window temporal decay in weather similarity.
    More recent days get higher weights.

    Args:
        x: First sequence of values
        y: Second sequence of values
        weights: Weight for each corresponding pair (must be same length)

    Returns:
        Weighted Pearson correlation coefficient (-1 to +1)

    Raises:
        ValueError: If sequences have different lengths
    """
    if len(x) != len(y) or len(x) != len(weights):
        raise ValueError("All sequences must have same length")
    if len(x) < 2:
        raise ValueError("Need at least 2 data points")

    # Normalize weights to sum to 1
    weight_sum = sum(weights)
    if weight_sum == 0:
        raise ValueError("Weights cannot all be zero")
    norm_weights = [w / weight_sum for w in weights]

    # Weighted means
    mean_x = sum(x[i] * norm_weights[i] for i in range(len(x)))
    mean_y = sum(y[i] * norm_weights[i] for i in range(len(y)))

    # Weighted standard deviations
    var_x = sum(norm_weights[i] * (x[i] - mean_x) ** 2 for i in range(len(x)))
    var_y = sum(norm_weights[i] * (y[i] - mean_y) ** 2 for i in range(len(y)))

    std_x = math.sqrt(var_x)
    std_y = math.sqrt(var_y)

    if std_x == 0 or std_y == 0:
        # One or both sequences have zero variance
        if std_x == 0 and std_y == 0:
            return 1.0  # Both constant
        return 0.0

    # Weighted covariance
    cov = sum(
        norm_weights[i] * (x[i] - mean_x) * (y[i] - mean_y)
        for i in range(len(x))
    )

    correlation = cov / (std_x * std_y)

    # Clamp to [-1, 1]
    return max(-1.0, min(1.0, correlation))


def z_score(value: float, mean_val: float, std_val: float) -> float:
    """
    Calculate the z-score (number of standard deviations from mean).

    Used for extreme weather detection.

    Args:
        value: The observed value
        mean_val: Population or sample mean
        std_val: Population or sample standard deviation

    Returns:
        Z-score (number of standard deviations from mean)

    Raises:
        ValueError: If std_val is zero

    Example:
        >>> z = z_score(35, 15, 5)  # 35 mph wind, mean=15, std=5
        >>> print(f"{z:.1f} SD above mean")
        4.0 SD above mean
    """
    if std_val == 0:
        raise ValueError("Standard deviation cannot be zero")
    return (value - mean_val) / std_val
