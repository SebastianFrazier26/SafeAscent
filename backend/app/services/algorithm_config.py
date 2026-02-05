"""
SafeAscent Safety Prediction Algorithm - Configuration

This module contains all tunable parameters for the safety prediction algorithm.
These values were determined through the design process documented in ALGORITHM_DESIGN.md.

All parameters are configurable to enable future optimization via backtesting.
"""

# =============================================================================
# SPATIAL WEIGHTING PARAMETERS
# =============================================================================

# Spatial bandwidth by route type (kilometers)
# Controls how quickly spatial influence decays with distance
# Larger bandwidth = wider influence radius
SPATIAL_BANDWIDTH = {
    "alpine": 75.0,    # Alpine routes: 75km bandwidth (~200km effective range)
    "ice": 50.0,       # Ice climbing: 50km bandwidth
    "mixed": 60.0,     # Mixed climbing: 60km bandwidth
    "trad": 40.0,      # Traditional climbing: 40km bandwidth
    "sport": 25.0,     # Sport climbing: 25km bandwidth (local hazards)
    "aid": 30.0,       # Aid climbing: 30km bandwidth
    "boulder": 20.0,   # Bouldering: 20km bandwidth (very local)
    "default": 50.0,   # Default for unknown types
}

# Maximum search radius for accidents (kilometers)
# No hard cutoff, but we stop searching beyond this distance for performance
MAX_SEARCH_RADIUS_KM = 300.0


# =============================================================================
# TEMPORAL WEIGHTING PARAMETERS
# =============================================================================

# Temporal decay lambda by route type
# Controls how quickly accident relevance fades over time
# Higher lambda = slower decay = longer memory
TEMPORAL_LAMBDA = {
    "alpine": 0.9998,   # Very slow decay (~9.5 year half-life) - objective hazards stable
    "ice": 0.9997,      # Slow decay (~6.6 year half-life) - ice conditions somewhat stable
    "mixed": 0.9997,    # Slow decay (~6.6 year half-life) - similar to alpine
    "trad": 0.9995,     # Moderate-slow decay (~3.8 year half-life) - rock quality stable
    "sport": 0.999,     # Moderate decay (~1.9 year half-life) - bolts age, routes upgraded
    "aid": 0.9995,      # Moderate-slow decay (~3.8 year half-life) - fixed gear ages slowly
    "boulder": 0.999,   # Moderate decay (~1.9 year half-life) - holds break, routes change
    "default": 0.9996,  # Default (~4.8 year half-life)
}

# Seasonal boost multiplier
# Applied when accident occurred in same season as current date
SEASONAL_BOOST = 1.5  # 50% boost for same-season accidents

# Season definitions (Northern Hemisphere, by month)
SEASONS = {
    "winter": [12, 1, 2],   # December, January, February
    "spring": [3, 4, 5],    # March, April, May
    "summer": [6, 7, 8],    # June, July, August
    "fall": [9, 10, 11],    # September, October, November
}


# =============================================================================
# ROUTE TYPE WEIGHTING PARAMETERS
# =============================================================================

# Asymmetric route type similarity matrix
# Key: (planning_route_type, accident_route_type) -> weight
# Higher weight = accident is more relevant to the planned route
ROUTE_TYPE_WEIGHTS = {
    # Sport climbing
    ("sport", "sport"): 1.0,    # Direct match
    ("sport", "trad"): 0.7,     # Trad somewhat similar
    ("sport", "boulder"): 0.6,  # Bouldering somewhat similar
    ("sport", "alpine"): 0.3,   # Alpine less relevant (different hazards)
    ("sport", "ice"): 0.2,      # Ice very different
    ("sport", "mixed"): 0.3,    # Mixed less relevant
    ("sport", "aid"): 0.5,      # Aid somewhat similar (bolted terrain)

    # Traditional climbing
    ("trad", "sport"): 0.6,     # Sport informs trad moderately
    ("trad", "trad"): 1.0,      # Direct match
    ("trad", "boulder"): 0.4,   # Less relevant
    ("trad", "alpine"): 0.6,    # Alpine somewhat relevant
    ("trad", "ice"): 0.3,       # Ice different
    ("trad", "mixed"): 0.5,     # Mixed somewhat relevant
    ("trad", "aid"): 0.8,       # Aid very similar

    # Alpine climbing
    ("alpine", "sport"): 0.9,   # CANARY EFFECT: sport accidents highly relevant!
    ("alpine", "trad"): 0.8,    # Trad quite relevant
    ("alpine", "boulder"): 0.3, # Less relevant
    ("alpine", "alpine"): 1.0,  # Direct match
    ("alpine", "ice"): 0.8,     # Ice very relevant
    ("alpine", "mixed"): 0.9,   # Mixed very relevant
    ("alpine", "aid"): 0.6,     # Aid moderately relevant

    # Ice climbing
    ("ice", "sport"): 0.4,      # Sport less relevant
    ("ice", "trad"): 0.5,       # Trad somewhat relevant
    ("ice", "boulder"): 0.2,    # Very different
    ("ice", "alpine"): 0.9,     # Alpine very relevant
    ("ice", "ice"): 1.0,        # Direct match
    ("ice", "mixed"): 0.9,      # Mixed very relevant
    ("ice", "aid"): 0.4,        # Aid less relevant

    # Mixed climbing
    ("mixed", "sport"): 0.5,    # Sport moderately relevant
    ("mixed", "trad"): 0.6,     # Trad moderately relevant
    ("mixed", "boulder"): 0.3,  # Less relevant
    ("mixed", "alpine"): 0.9,   # Alpine very relevant
    ("mixed", "ice"): 0.9,      # Ice very relevant
    ("mixed", "mixed"): 1.0,    # Direct match
    ("mixed", "aid"): 0.5,      # Aid moderately relevant

    # Aid climbing
    ("aid", "sport"): 0.5,      # Sport moderately relevant
    ("aid", "trad"): 0.8,       # Trad very similar
    ("aid", "boulder"): 0.2,    # Very different
    ("aid", "alpine"): 0.6,     # Alpine moderately relevant
    ("aid", "ice"): 0.3,        # Ice different
    ("aid", "mixed"): 0.5,      # Mixed moderately relevant
    ("aid", "aid"): 1.0,        # Direct match

    # Bouldering
    ("boulder", "sport"): 0.7,  # Sport quite similar
    ("boulder", "trad"): 0.4,   # Trad less relevant
    ("boulder", "boulder"): 1.0,# Direct match
    ("boulder", "alpine"): 0.2, # Alpine very different
    ("boulder", "ice"): 0.2,    # Ice very different
    ("boulder", "mixed"): 0.3,  # Mixed less relevant
    ("boulder", "aid"): 0.3,    # Aid less relevant
}

# Default weight for unknown route type combinations
DEFAULT_ROUTE_TYPE_WEIGHT = 0.5


# =============================================================================
# WEATHER SIMILARITY PARAMETERS
# =============================================================================

# Weather factor weights (equal weighting as designed)
# All 6 factors weighted equally at 1/6 each
WEATHER_FACTOR_WEIGHTS = {
    "temperature": 1/6,
    "precipitation": 1/6,
    "wind_speed": 1/6,
    "visibility": 1/6,
    "cloud_cover": 1/6,
    "freeze_thaw": 1/6,
}

# Extreme weather detection threshold (standard deviations)
EXTREME_WEATHER_SD_THRESHOLD = 2.0  # 2.0 SD = ~97.7th percentile

# Extreme weather penalty multipliers (per SD above threshold)
EXTREME_PENALTY_MULTIPLIERS = {
    "wind_speed": 0.20,      # 20% increase per SD for extreme winds
    "precipitation": 0.20,   # 20% increase per SD for extreme precip
    "temperature": 0.20,     # 20% increase per SD for temp extremes
    "visibility": 0.25,      # 25% increase per SD for low visibility (higher impact)
}

# Within-window temporal decay factor
# Controls how much more recent days matter within the 7-day weather window
WITHIN_WINDOW_TEMPORAL_DECAY = 0.85  # Day 0 gets 2.5× more weight than Day -6
# TODO: Optimize via backtesting post-MVP

# Freeze-thaw cycle threshold temperature (Celsius)
FREEZE_THAW_TEMP_C = 0.0  # 32°F = 0°C


# =============================================================================
# ELEVATION WEIGHTING PARAMETERS
# =============================================================================

# Elevation decay constants by route type (meters)
# Controls how quickly influence decays for accidents at HIGHER elevations
# Accidents at same/lower elevation always get full weight (1.0)
ELEVATION_DECAY_CONSTANT = {
    "alpine": 800,    # Most sensitive to altitude effects
    "ice": 800,       # Same as alpine
    "mixed": 800,     # Same as alpine
    "trad": 1200,     # Medium sensitivity
    "aid": 1200,      # Same as trad
    "sport": 1800,    # Less sensitive to elevation
    "boulder": 3000,  # Barely affected by elevation
    "default": 1200,  # Default for unknown types
}


# =============================================================================
# GRADE WEIGHTING PARAMETERS
# =============================================================================

# Grade difference that results in 50% weight (in normalized difficulty units)
# ~3 YDS grades apart = half weight (e.g., 5.10a vs 5.13a)
# Larger value = more forgiving grade matching
GRADE_HALF_WEIGHT_DIFF = 3.0

# Minimum grade weight (floor)
# Ensures accidents are never fully excluded based on grade alone
GRADE_MIN_WEIGHT = 0.25


# =============================================================================
# SEVERITY WEIGHTING PARAMETERS
# =============================================================================

# Severity boost multipliers (subtle linear boosters, not exponential)
SEVERITY_BOOSTERS = {
    "fatal": 1.3,      # 30% boost - acknowledges higher signal
    "serious": 1.1,    # 10% boost - typical accident in dataset
    "minor": 1.0,      # Baseline - still reveals dangerous conditions
    "unknown": 1.0,    # Conservative - treat as minor, avoid bias
}

# Default severity weight for unrecognized severity levels
DEFAULT_SEVERITY_WEIGHT = 1.0


# =============================================================================
# RISK SCORE NORMALIZATION PARAMETERS
# =============================================================================

# Risk score normalization constant
# Empirically determined: sum of influences → 0-100 scale
# Adjusted 2026-01-30: Reduced from 10.0 to 5.0 to provide headroom for
# high-density areas (476+ accidents) with quadratic weather weighting.
# NOTE: Further reduction to 3.0 was considered but tabled. Future calibration
# should use dynamic normalization based on ascent data (accidents per 1000 ascents)
# to distinguish high-traffic vs. high-danger routes.
# TODO: Calibrate via backtesting with real data + ascent density analysis
RISK_NORMALIZATION_FACTOR = 5.0  # influence_sum * 5 ≈ risk_score

# Maximum risk score (capped)
MAX_RISK_SCORE = 100


# =============================================================================
# PERFORMANCE OPTIMIZATION PARAMETERS
# =============================================================================

# Maximum accidents to return in contributing accidents list
MAX_CONTRIBUTING_ACCIDENTS_UI = 50  # Top 50 for performance

# Minimum weather data completeness for pattern matching
MIN_WEATHER_DAYS_REQUIRED = 5  # Need at least 5 of 7 days


# =============================================================================
# UTILITY CONSTANTS
# =============================================================================

# Earth radius for Haversine distance calculations (kilometers)
EARTH_RADIUS_KM = 6371.0
