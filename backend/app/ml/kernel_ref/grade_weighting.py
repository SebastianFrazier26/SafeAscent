"""
Grade Weighting Module for SafeAscent Safety Algorithm

Calculates similarity weight between route grades.
Routes with similar difficulty to accident routes get higher weight.

Grade Systems Supported:
- YDS (Yosemite Decimal System): 5.0 - 5.15d
- V-scale (Bouldering): V0 - V17
- Ice/Alpine: WI1-WI7, AI1-AI6, M1-M15
- Aid: A0-A5, C0-C5

Algorithm:
1. Parse grades into normalized difficulty score (0-20 scale)
2. Calculate difference between route and accident grades
3. Apply Gaussian decay based on grade difference
4. Unknown grades get neutral weight (1.0)
"""
import re
from typing import Optional, Tuple


# =============================================================================
# GRADE PARSING - Convert various grade systems to normalized difficulty
# =============================================================================

# YDS grade mapping (5.0 = 0, 5.15d = 20)
YDS_GRADES = {
    "5.0": 0, "5.1": 1, "5.2": 2, "5.3": 3, "5.4": 4,
    "5.5": 5, "5.6": 6, "5.7": 7, "5.8": 8, "5.9": 9,
    "5.10a": 10.0, "5.10b": 10.25, "5.10c": 10.5, "5.10d": 10.75,
    "5.10": 10.5,  # Generic 5.10
    "5.11a": 11.0, "5.11b": 11.25, "5.11c": 11.5, "5.11d": 11.75,
    "5.11": 11.5,
    "5.12a": 12.0, "5.12b": 12.25, "5.12c": 12.5, "5.12d": 12.75,
    "5.12": 12.5,
    "5.13a": 13.0, "5.13b": 13.25, "5.13c": 13.5, "5.13d": 13.75,
    "5.13": 13.5,
    "5.14a": 14.0, "5.14b": 14.25, "5.14c": 14.5, "5.14d": 14.75,
    "5.14": 14.5,
    "5.15a": 15.0, "5.15b": 15.25, "5.15c": 15.5, "5.15d": 15.75,
    "5.15": 15.5,
}

# V-scale mapping (V0 ≈ 5.10, V10 ≈ 5.14)
# Offset to align with YDS scale
V_SCALE_OFFSET = 10.0  # V0 ≈ difficulty 10
V_SCALE_MULTIPLIER = 0.4  # Each V-grade ≈ 0.4 difficulty points


def parse_yds_grade(grade: str) -> Optional[float]:
    """Parse YDS grade (5.x format) to normalized difficulty."""
    grade = grade.strip().lower()

    # Direct lookup
    if grade in YDS_GRADES:
        return YDS_GRADES[grade]

    # Handle +/- modifiers (5.9+, 5.10-)
    if grade.endswith('+'):
        base = grade[:-1]
        if base in YDS_GRADES:
            return YDS_GRADES[base] + 0.25
    if grade.endswith('-'):
        base = grade[:-1]
        if base in YDS_GRADES:
            return YDS_GRADES[base] - 0.25

    # Handle slash grades (5.10a/b)
    if '/' in grade:
        parts = grade.split('/')
        if len(parts) == 2:
            # Take the average
            first = parse_yds_grade(parts[0])
            # Second part is just the letter (a/b -> 5.10b)
            base_match = re.match(r'(5\.\d+)', parts[0])
            if base_match and first is not None:
                second = parse_yds_grade(base_match.group(1) + parts[1])
                if second is not None:
                    return (first + second) / 2

    return None


def parse_v_grade(grade: str) -> Optional[float]:
    """Parse V-scale bouldering grade to normalized difficulty."""
    grade = grade.strip().upper()

    # Match V0, V1, ..., V17
    match = re.match(r'V(\d+)', grade)
    if match:
        v_num = int(match.group(1))
        return V_SCALE_OFFSET + (v_num * V_SCALE_MULTIPLIER)

    # Handle VB (easier than V0)
    if grade == 'VB':
        return V_SCALE_OFFSET - 0.5

    return None


def parse_ice_grade(grade: str) -> Optional[float]:
    """Parse ice/alpine grade (WI, AI, M) to normalized difficulty."""
    grade = grade.strip().upper()

    # WI (Water Ice): WI1-WI7
    match = re.match(r'WI(\d+)', grade)
    if match:
        wi_num = int(match.group(1))
        # WI1 ≈ 5.6, WI7 ≈ 5.13
        return 6 + (wi_num - 1) * 1.2

    # AI (Alpine Ice): AI1-AI6
    match = re.match(r'AI(\d+)', grade)
    if match:
        ai_num = int(match.group(1))
        # AI1 ≈ 5.7, AI6 ≈ 5.14
        return 7 + (ai_num - 1) * 1.4

    # M (Mixed): M1-M15
    match = re.match(r'M(\d+)', grade)
    if match:
        m_num = int(match.group(1))
        # M1 ≈ 5.7, M15 ≈ 5.15
        return 7 + (m_num - 1) * 0.6

    return None


def parse_aid_grade(grade: str) -> Optional[float]:
    """Parse aid climbing grade (A0-A5, C0-C5) to normalized difficulty."""
    grade = grade.strip().upper()

    # A-scale (traditional aid)
    match = re.match(r'A(\d+)', grade)
    if match:
        a_num = int(match.group(1))
        # A0 ≈ 5.8 (french free), A5 ≈ 5.14 (extreme aid)
        return 8 + a_num * 1.2

    # C-scale (clean aid)
    match = re.match(r'C(\d+)', grade)
    if match:
        c_num = int(match.group(1))
        return 8 + c_num * 1.2

    return None


def parse_grade(grade: Optional[str]) -> Optional[float]:
    """
    Parse any climbing grade to normalized difficulty (0-20 scale).

    Args:
        grade: Grade string (e.g., "5.10a", "V5", "WI4", "5.11b/c")

    Returns:
        Normalized difficulty (0-20) or None if unparseable
    """
    if not grade:
        return None

    grade = grade.strip()
    if not grade:
        return None

    # Try each grade system in order
    # YDS is most common, try first
    if grade.lower().startswith('5.'):
        result = parse_yds_grade(grade)
        if result is not None:
            return result

    # V-scale bouldering
    if grade.upper().startswith('V'):
        result = parse_v_grade(grade)
        if result is not None:
            return result

    # Ice grades
    for prefix in ['WI', 'AI', 'M']:
        if grade.upper().startswith(prefix):
            result = parse_ice_grade(grade)
            if result is not None:
                return result

    # Aid grades
    for prefix in ['A', 'C']:
        if grade.upper().startswith(prefix) and len(grade) >= 2 and grade[1].isdigit():
            result = parse_aid_grade(grade)
            if result is not None:
                return result

    # Try YDS as fallback (some grades stored without 5. prefix)
    result = parse_yds_grade('5.' + grade)
    if result is not None:
        return result

    return None


# =============================================================================
# GRADE WEIGHTING CALCULATION
# =============================================================================

# Grade difference that results in 50% weight (in difficulty units)
GRADE_HALF_WEIGHT_DIFF = 3.0  # ~3 YDS grades difference = 50% weight


def calculate_grade_weight(
    route_grade: Optional[str],
    accident_grade: Optional[str],
) -> float:
    """
    Calculate grade similarity weight between route and accident grades.

    Uses Gaussian decay based on grade difference:
    - Same grade: 1.0 (full weight)
    - 3 grades apart: 0.5 (half weight)
    - 6+ grades apart: < 0.25

    Unknown grades get neutral weight (1.0) to avoid penalizing
    missing data.

    Args:
        route_grade: Grade of planned route (e.g., "5.10a")
        accident_grade: Grade of accident route (e.g., "5.11c")

    Returns:
        Weight from 0.25 to 1.0 (floor at 0.25 to never fully exclude)

    Examples:
        >>> calculate_grade_weight("5.10a", "5.10a")  # Same grade
        1.0
        >>> calculate_grade_weight("5.10a", "5.11a")  # 1 grade apart
        0.89
        >>> calculate_grade_weight("5.10a", "5.12a")  # 2 grades apart
        0.63
        >>> calculate_grade_weight("5.10a", "5.13a")  # 3 grades apart
        0.5
        >>> calculate_grade_weight("5.10a", None)     # Unknown accident grade
        1.0
    """
    import math

    # Parse grades
    route_difficulty = parse_grade(route_grade)
    accident_difficulty = parse_grade(accident_grade)

    # If either grade is unknown, use neutral weight
    # This avoids penalizing accidents with missing grade data
    if route_difficulty is None or accident_difficulty is None:
        return 1.0

    # Calculate grade difference
    grade_diff = abs(route_difficulty - accident_difficulty)

    # Gaussian decay: weight = exp(-(diff²) / (2 * σ²))
    # σ chosen so that diff=3 → weight≈0.5
    # For exp(-x²/2σ²) = 0.5, x = σ * sqrt(2 * ln(2)) ≈ 1.18σ
    # So σ = 3 / 1.18 ≈ 2.54
    sigma = GRADE_HALF_WEIGHT_DIFF / 1.18

    weight = math.exp(-(grade_diff ** 2) / (2 * sigma ** 2))

    # Floor at 0.25 - never fully exclude based on grade alone
    return max(0.25, weight)


def get_grade_info(grade: Optional[str]) -> Tuple[Optional[float], str]:
    """
    Get normalized difficulty and grade system for a grade string.

    Args:
        grade: Grade string

    Returns:
        Tuple of (normalized_difficulty, grade_system)
        grade_system is one of: "yds", "v-scale", "ice", "aid", "unknown"
    """
    if not grade:
        return (None, "unknown")

    grade = grade.strip()

    # Detect system
    if grade.lower().startswith('5.'):
        return (parse_yds_grade(grade), "yds")
    if grade.upper().startswith('V'):
        return (parse_v_grade(grade), "v-scale")
    for prefix in ['WI', 'AI', 'M']:
        if grade.upper().startswith(prefix):
            return (parse_ice_grade(grade), "ice")
    for prefix in ['A', 'C']:
        if grade.upper().startswith(prefix) and len(grade) >= 2 and grade[1].isdigit():
            return (parse_aid_grade(grade), "aid")

    return (None, "unknown")
