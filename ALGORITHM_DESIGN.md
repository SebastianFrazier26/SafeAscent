# SafeAscent Safety Prediction Algorithm - Design Document

**Status**: ✅ Implemented and Tested (50/50 tests passing)
**Last Updated**: 2026-02-06
**Author**: Sebastian Frazier

> **Note**: This document captures design decisions. The algorithm is now fully implemented in `backend/app/services/`. Key tuning updates since design:
> - Weather power: Uses **cubic (27×)** for strong weather influence
> - Normalization factor: **7.0** (increased from 5.0) to surface moderate/high risk better
> - Elevation decay constants: Doubled (2×) to reduce elevation's dominance vs weather/proximity

---

## Overview

This document captures the design decisions for SafeAscent's core safety prediction algorithm. The algorithm predicts climbing route safety by analyzing historical accident data weighted by spatial proximity, temporal decay, weather pattern similarity, and route type relevance.

---

## Core Algorithm Structure

```
Safety Risk Score = Σ (accident_influence_i) for all accidents
                    normalized to 0-100 scale

where:
  accident_influence = spatial_weight × temporal_weight × weather_weight × route_type_weight × severity_weight
```

---

## Design Decisions Log

### Decision #1: Spatial Influence Strategy
**Date**: 2026-01-28
**Status**: ✅ Decided

**Question**: Should we use a hard cutoff radius or continuous decay with weather override?

**Decision**: **No hard cutoff** - Use continuous Gaussian decay with weather similarity override

**Rationale**:
- Small dataset (~4,000 accidents) means we can't afford to discard data
- Weather similarity can make distant accidents highly relevant
- Example: Accident 500km away with identical weather conditions is more relevant than accident 10km away with different weather
- Gaussian decay naturally diminishes distant accidents while allowing them to contribute if weather matches

**Formula**:
```python
spatial_weight = exp(-distance² / spatial_bandwidth²)
```

**Parameters**:
- `spatial_bandwidth`: Route-type specific (see Decision #3)
- No maximum distance cutoff

**Key Insight**:
> "If the weather conditions between Area A and Area B are identical, even if the areas are 500 km apart, the information about accidents in Area B is probably still relevant to Area A as they occurred during the same weather conditions." - Sebastian

---

### Decision #2: Route Type Weighting Strategy
**Date**: 2026-01-28
**Status**: ✅ Decided (MVP), 🔮 Future Enhancement Planned

**Question**: How should accidents of different route types influence each other?

**Decision**: **Asymmetric weighting matrix** (MVP) with future upgrade to weather-conditional weighting

**Rationale**:
- Sport climbing accidents in bad weather are a "canary in coal mine" for alpine danger
  - If easy, bolted routes are causing accidents, alpine routes are definitely dangerous
- Alpine accidents in good weather may be due to alpine-specific hazards (crevasses, altitude sickness, avalanches)
  - These don't threaten sport climbers as much
- Therefore: sport → alpine influence is STRONG, alpine → sport influence is WEAK

**MVP Implementation (Option A): Simple Asymmetric Matrix**

```python
ROUTE_TYPE_WEIGHTS = {
    # (planning_type, accident_type): weight
    ("sport", "sport"): 1.0,    # Direct match
    ("sport", "trad"): 0.7,     # Somewhat similar
    ("sport", "alpine"): 0.3,   # Alpine accidents less relevant to sport

    ("trad", "sport"): 0.6,     # Sport informs trad moderately
    ("trad", "trad"): 1.0,      # Direct match
    ("trad", "alpine"): 0.6,    # Alpine somewhat relevant

    ("alpine", "sport"): 0.9,   # Sport accidents HIGHLY relevant (canary effect!)
    ("alpine", "trad"): 0.8,    # Trad accidents quite relevant
    ("alpine", "alpine"): 1.0,  # Direct match
}
```

**Future Enhancement (Option B): Weather-Conditional Weighting**

```python
def calculate_route_type_weight(planning_type, accident_type, accident_weather_severity):
    """
    accident_weather_severity: 0.0 (perfect) to 1.0 (terrible)
    """
    if planning_type == "alpine":
        if accident_type == "sport":
            if accident_weather_severity > 0.7:  # Bad weather
                return 1.2  # AMPLIFY! Sport accident in bad weather = strong alpine danger signal
            else:  # Good weather
                return 0.4  # Sport accident in good weather = less relevant to alpine
        elif accident_type == "alpine":
            return 1.0  # Direct match

    # ... (full logic in design notes)
```

**Why defer Option B**:
- Option A is simpler to implement and explain
- Option B requires tuning "bad weather" thresholds with real data
- Code should be structured to easily swap in Option B later

**TODO**: Implement Option B after initial algorithm validation and testing

---

### Decision #3: Spatial Bandwidth by Route Type
**Date**: 2026-01-28
**Status**: ✅ Decided

**Question**: Should different route types have different spatial influence radii?

**Decision**: **Yes** - Alpine routes use larger bandwidth, sport routes use smaller

**Rationale**:
- Alpine hazards (avalanches, freeze-thaw, altitude) are more "universal" across regions
- Sport climbing hazards (rock quality, route-finding, anchor failures) are more "local"
- Different bandwidths create different decay curves while maintaining same formula

**Parameters**:
```python
SPATIAL_BANDWIDTH = {
    "alpine": 75,   # km - larger radius (universal hazards)
    "trad": 40,     # km - medium radius
    "sport": 25,    # km - smaller radius (local hazards)
    "ice": 50,      # km - medium-large (weather-dependent)
    "mixed": 60,    # km - large (alpine-like)
    "aid": 30,      # km - small-medium (local)
    "default": 50,  # km - balanced default
}
```

**Effect on weights**:
- Alpine route at 50km from accident: weight ≈ 0.33 (moderate)
- Sport route at 50km from accident: weight ≈ 0.00001 (negligible)

---

### Decision #4: Temporal Decay Strategy
**Date**: 2026-01-28
**Status**: ✅ Decided

**Question**: How quickly should accidents fade in relevance over time?

**Decision**: **Very slow, year-scale decay** with route-type-specific rates and seasonal boosting

**Rationale**:
Climbing safety is fundamentally different from financial volatility:
- **Mountains don't change**: Objective hazards (avalanche paths, crevasses, rockfall zones, exposure) are stable over decades
- **Slow equipment evolution**: Safety improvements happen on year/decade timescale (better gear, route upgrades, technique advances)
- **Persistent route characteristics**: A dangerous route in 2015 is likely still dangerous in 2025 for the same fundamental reasons
- **Seasonal patterns repeat**: Winter avalanche conditions from 10 years ago are more relevant to this winter than last summer's conditions

**Key Insight**:
> "Climbing safety updates do matter but change far far slower than any sort of financial model. I like general stability with slight decay over longer periods to account for climbing safety updates (like new gear/bolting/etc.)" - Sebastian

**Formula**:
```python
temporal_weight = (lambda_by_route_type ** days_elapsed) × seasonal_boost

where:
  days_elapsed = (current_date - accident_date).days
  seasonal_boost = 1.5 if same_season else 1.0
```

**Parameters by Route Type**:
```python
TEMPORAL_LAMBDA = {
    "alpine": 0.9998,   # Very slow decay (~9.5 year half-life)
                        # Objective hazards (avalanches, crevasses, altitude) don't change

    "ice": 0.9997,      # Slow decay (~6.6 year half-life)
                        # Ice conditions somewhat stable year-to-year

    "mixed": 0.9997,    # Slow decay (~6.6 year half-life)
                        # Similar to alpine, objective hazards persist

    "trad": 0.9995,     # Moderate-slow decay (~3.8 year half-life)
                        # Rock quality and pro placements fairly stable

    "sport": 0.999,     # Moderate decay (~1.9 year half-life)
                        # Bolt aging, route upgrades more common

    "aid": 0.9995,      # Moderate-slow decay (~3.8 year half-life)
                        # Fixed gear ages but routes don't change much

    "default": 0.9996,  # Default (~4.8 year half-life)
}
```

**Weight Examples (Alpine, λ=0.9998)**:
- 1 year ago: weight = 0.93 (93%)
- 3 years ago: weight = 0.80 (80%)
- 5 years ago: weight = 0.70 (70%)
- 10 years ago: weight = 0.49 (49%)
- 10 years ago + same season: weight = 0.49 × 1.5 = 0.74 (74%)

**Seasonal Boost Logic**:
```python
def calculate_seasonal_boost(accident_date, current_date):
    """
    Boost weight if accident occurred in same season, even years ago.

    Seasons (Northern Hemisphere):
    - Winter: Dec, Jan, Feb
    - Spring: Mar, Apr, May
    - Summer: Jun, Jul, Aug
    - Fall: Sep, Oct, Nov

    Returns: 1.5 (same season) or 1.0 (different season)
    """
    # Implementation in code
```

**Effect of Seasonal Boost**:
- A 10-year-old winter accident evaluated in winter gets weighted like a 3-year-old accident
- A 5-year-old summer accident evaluated in winter gets no boost (different season)
- This captures seasonal risk patterns that repeat year after year

**Why route-type-specific decay**:
- Alpine routes have stable objective hazards → slower decay
- Sport routes get rebolted and upgraded → faster decay to account for improvements

---

### Decision #5: Weather Similarity Metric
**Date**: 2026-01-28
**Status**: ✅ Decided (MVP implementation), 🔮 Post-MVP optimization planned

**Question**: How do we quantify weather pattern similarity between current conditions and pre-accident weather?

**Decision**: **Pattern correlation approach** (Option B) with equal weighting, extreme weather detection, and within-window temporal decay

**Rationale**:

**Why Pattern Correlation (Option B) over Simple Averages (Option A)**:

Pattern correlation captures critical information that simple averages miss:

**Example demonstrating the problem with averages:**
```
Current weather (planning to climb):
Mon  Tue  Wed  Thu  Fri  Sat  Sun
45°  47°  49°  51°  53°  55°  57°F   (warming trend - snow melting, rockfall)

Historical Accident A:
Mon  Tue  Wed  Thu  Fri  Sat  Sun
57°  55°  53°  51°  49°  47°  45°F   (cooling trend - conditions stabilizing)

Both have average temp = 51°F
```

- **Simple average approach**: Would consider these identical (distance ≈ 0)
- **Pattern correlation approach**: Detects they are opposite trends (correlation ≈ -1)
- **Reality**: These have opposite risk profiles despite identical averages

**For climbing safety**, trends matter because:
- Freeze-thaw cycles require temperature crossings → pattern matters
- Snowmelt timing depends on warming trends → trend direction matters
- Storm systems create multi-day wet periods → sequence matters
- Erratic weather creates unpredictable hazards → pattern stability matters

**Key Insight**:
> "All weather factors matter relatively equally. Temperature is certainly important but so is wind speed, precip, visibility, and everything else." - Sebastian

**Weather Data Verified**:
- ✅ Database contains 25,591 weather records
- ✅ Each accident has 7-day window (accident day + 6 surrounding days for context)
- ✅ Records from 1983-2026 covering all accident periods
- ✅ Variables: temp (avg/min/max), precipitation, wind (avg/max), visibility, cloud cover

---

### Formula Components

```python
weather_similarity_weight = (
    pattern_similarity ×
    extreme_weather_penalty ×
    within_window_temporal_weighting
)
```

---

#### Component 1: Pattern Similarity via Correlation

**Equal weighting across all 6 weather factors:**

```python
def calculate_pattern_similarity(current_7days, accident_7days):
    """
    Compare 7-day weather patterns using Pearson correlation.
    All factors weighted equally (1/6 each).

    Returns: 0.0 (opposite patterns) to 1.0 (identical patterns)
    """

    # 1. Temperature pattern correlation
    current_temps = [day.temp_avg for day in current_7days]
    accident_temps = [day.temp_avg for day in accident_7days]
    temp_corr = pearson_correlation(current_temps, accident_temps)
    temp_score = (temp_corr + 1) / 2  # Convert -1/+1 to 0/1

    # 2. Precipitation pattern correlation
    current_precip = [day.precipitation_total for day in current_7days]
    accident_precip = [day.precipitation_total for day in accident_7days]
    precip_corr = pearson_correlation(current_precip, accident_precip)
    precip_score = (precip_corr + 1) / 2

    # 3. Wind speed pattern correlation
    current_wind = [day.wind_speed_avg for day in current_7days]
    accident_wind = [day.wind_speed_avg for day in accident_7days]
    wind_corr = pearson_correlation(current_wind, accident_wind)
    wind_score = (wind_corr + 1) / 2

    # 4. Visibility pattern correlation
    current_vis = [day.visibility_avg for day in current_7days]
    accident_vis = [day.visibility_avg for day in accident_7days]
    vis_corr = pearson_correlation(current_vis, accident_vis)
    vis_score = (vis_corr + 1) / 2

    # 5. Cloud cover pattern correlation
    current_clouds = [day.cloud_cover_avg for day in current_7days]
    accident_clouds = [day.cloud_cover_avg for day in accident_7days]
    cloud_corr = pearson_correlation(current_clouds, accident_clouds)
    cloud_score = (cloud_corr + 1) / 2

    # 6. Freeze-thaw cycle matching (special binary feature)
    current_ft_cycles = count_freeze_thaw_cycles(current_7days)
    accident_ft_cycles = count_freeze_thaw_cycles(accident_7days)
    ft_match = 1.0 - (abs(current_ft_cycles - accident_ft_cycles) / 7.0)

    # EQUAL WEIGHTING: 6 factors each get 1/6 weight
    pattern_similarity = (
        temp_score +
        precip_score +
        wind_score +
        vis_score +
        cloud_score +
        ft_match
    ) / 6.0

    return pattern_similarity  # 0.0 to 1.0
```

**Freeze-thaw cycle definition:**
- Any day where temperature crosses 32°F (0°C)
- Counted across the 7-day window
- Critical for alpine/mixed climbing (rockfall, ice weakness, wet snow avalanches)

---

#### Component 2: Extreme Weather Penalty (Statistical Volatility)

**Uses 2.0 standard deviations to flag anomalous conditions:**

```python
def calculate_extreme_weather_penalty(current_7days, historical_stats):
    """
    Amplify risk when current weather exceeds normal volatility by 2+ standard deviations.

    historical_stats: Pre-computed mean/std for this location-season combination

    Returns: >= 1.0 (1.0 = normal, > 1.0 = extreme conditions amplify risk)
    """

    penalties = []

    for day in current_7days:
        # Wind speed (extreme high winds)
        wind_z_score = (day.wind_speed_avg - historical_stats.wind_mean) / historical_stats.wind_std
        if wind_z_score > 2.0:  # More than 2 SD above normal
            penalties.append(1.0 + (wind_z_score - 2.0) * 0.2)

        # Precipitation (extreme precipitation)
        precip_z_score = (day.precipitation_total - historical_stats.precip_mean) / historical_stats.precip_std
        if precip_z_score > 2.0:
            penalties.append(1.0 + (precip_z_score - 2.0) * 0.2)

        # Temperature extremes (both hot and cold)
        temp_z_score = abs((day.temp_avg - historical_stats.temp_mean) / historical_stats.temp_std)
        if temp_z_score > 2.0:
            penalties.append(1.0 + (temp_z_score - 2.0) * 0.2)

        # Visibility (extreme low visibility)
        vis_z_score = (historical_stats.vis_mean - day.visibility_avg) / historical_stats.vis_std
        if vis_z_score > 2.0:  # Much lower than normal
            penalties.append(1.0 + (vis_z_score - 2.0) * 0.25)

    # Take maximum penalty across all days in window
    extreme_penalty = max(penalties) if penalties else 1.0

    return extreme_penalty  # >= 1.0
```

**Example calculation:**
- Location historical stats: wind_mean = 15 mph, wind_std = 5 mph
- Current day: 35 mph wind
- Z-score: (35 - 15) / 5 = 4.0 (4 standard deviations above normal!)
- Penalty: 1.0 + (4.0 - 2.0) × 0.2 = **1.4× risk amplifier**

**Why 2.0 SD threshold**:
- 2.0 SD = ~97.7th percentile (extreme but not impossibly rare)
- Captures genuinely unusual conditions without triggering on normal variance
- Standard deviation already accounts for typical extremes (hot summers, cold winters)
- Consistent threshold across all weather metrics for simplicity

---

#### Component 3: Within-Window Temporal Weighting

**Recent days matter more than a week ago:**

```python
def calculate_within_window_weights(decay_factor=0.85):
    """
    Weight more recent days more heavily within 7-day window.

    decay_factor: 0.85 (MVP default) → Day 0 gets 2.5× more weight than Day -6
                  Will be optimized via backtesting post-MVP

    Returns: List of 7 normalized weights for days [-6, -5, -4, -3, -2, -1, 0]
    """

    weights = []
    for days_before_accident in range(6, -1, -1):  # 6 to 0
        weight = decay_factor ** days_before_accident
        weights.append(weight)

    # Normalize to sum to 1.0
    total = sum(weights)
    normalized_weights = [w / total for w in weights]

    return normalized_weights

# Example with decay_factor=0.85:
# Day -6: 0.08 (8%)   - A week before
# Day -5: 0.09 (9%)
# Day -4: 0.11 (11%)
# Day -3: 0.12 (12%)
# Day -2: 0.15 (15%)
# Day -1: 0.17 (17%)  - Day before accident
# Day 0:  0.20 (20%)  - Accident day (gets most weight)
```

**Application**: When computing Pearson correlations, use weighted correlation where each day gets its weight above.

**MVP Parameter**: `WITHIN_WINDOW_TEMPORAL_DECAY = 0.85`
- Reasonable middle ground (not too aggressive, not too flat)
- Makes recent weather 2.5× more important than week-old weather
- Configurable for future optimization

**Post-MVP Optimization** (planned):
Will empirically determine optimal decay factor via backtesting:

```python
def find_optimal_within_window_decay(accidents, weather_data):
    """
    Test decay factors [1.0, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70]
    to find which maximizes discrimination between nearby vs distant accidents.

    Method: Leave-one-out validation
    - For each accident, compute similarity to all others using candidate decay factor
    - Measure: Do nearby accidents (<50km) have higher weather similarity than distant (>200km)?
    - Best factor = highest discrimination score

    This is publishable climbing safety research:
    "Weather conditions X days before an accident are most predictive of risk"
    """
    # Implementation details in function comments above
```

**Why data-driven optimization**:
> "Is there any way to more scientifically determine the ratio by which we should rate the day or is it just a judgement call... I'd like a more direct method of figuring out what number to use here" - Sebastian

By designing for backtesting, we avoid overfitting while enabling empirical optimization later.

---

### Combined Weather Similarity Score

```python
def calculate_weather_similarity(current_weather, accident_weather, location_stats):
    """
    Master function combining all three components.

    Returns: weather_weight (typically 0.0 to 1.0, can exceed 1.0 if extreme conditions)
    """

    # 1. Pattern similarity (0.0 to 1.0)
    pattern_sim = calculate_pattern_similarity(current_weather, accident_weather)

    # 2. Extreme weather penalty (>= 1.0, amplifies if extreme conditions present)
    extreme_penalty = calculate_extreme_weather_penalty(current_weather, location_stats)

    # 3. Temporal weighting already applied via weighted correlation in step 1

    # Combine: Good pattern match + extreme conditions = HIGH RISK
    weather_weight = pattern_sim * extreme_penalty

    return weather_weight
```

**Interpretation examples:**
- `weather_weight = 0.2`: Very different weather patterns → low relevance
- `weather_weight = 0.8`: Similar weather patterns → moderate relevance
- `weather_weight = 1.0`: Nearly identical patterns → high relevance
- `weather_weight = 1.5`: Identical patterns + EXTREME conditions (4 SD winds) → **AMPLIFIED RISK!**

---

### Database Requirements

**New table for historical weather statistics:**

```sql
CREATE TABLE weather_statistics (
    stat_id SERIAL PRIMARY KEY,
    latitude NUMERIC(8,2),    -- Rounded to ~1km precision (matches weather table)
    longitude NUMERIC(8,2),
    season VARCHAR(10),        -- 'winter', 'spring', 'summer', 'fall'

    -- Temperature statistics
    temp_mean FLOAT,
    temp_std FLOAT,
    temp_min FLOAT,           -- Historical extremes
    temp_max FLOAT,

    -- Precipitation statistics
    precip_mean FLOAT,
    precip_std FLOAT,

    -- Wind statistics
    wind_mean FLOAT,
    wind_std FLOAT,

    -- Visibility statistics
    vis_mean FLOAT,
    vis_std FLOAT,

    -- Cloud cover statistics
    cloud_mean FLOAT,
    cloud_std FLOAT,

    -- Metadata
    sample_size INT,          -- How many days used to compute these stats
    last_updated DATE,        -- When statistics were last recomputed

    UNIQUE(latitude, longitude, season)
);

CREATE INDEX idx_weather_stats_location ON weather_statistics(latitude, longitude);
```

**Seasons definition (Northern Hemisphere):**
- Winter: December, January, February
- Spring: March, April, May
- Summer: June, July, August
- Fall: September, October, November

**Preprocessing pipeline:**
1. Group all weather records by location (lat/lon rounded to 0.01°) and season
2. Compute mean and standard deviation for each weather variable
3. Store in `weather_statistics` table
4. **Update frequency**: Yearly when new accident data is added
5. Requires ~4,000 location-season combinations (441 mountains × 4 seasons, with some overlap)

**Query optimization**:
- When calculating risk for a route, lookup historical stats for that location+season once
- Cache in memory during batch risk calculations
- Pre-join weather_statistics with routes table for faster lookups

---

### Implementation Notes

**Pearson Correlation formula:**
```
r = Σ((xi - x̄)(yi - ȳ)) / √(Σ(xi - x̄)² × Σ(yi - ȳ)²)

Where:
  xi, yi = values from the two 7-day sequences
  x̄, ȳ = means of the sequences
  r ranges from -1 (perfect negative correlation) to +1 (perfect positive correlation)
```

**Handling edge cases:**
- **Zero variance**: If all 7 days have identical values (e.g., no precipitation all week), correlation is undefined
  - Solution: Return 1.0 (perfect match) if both sequences have zero variance
  - Return 0.0 if only one has zero variance
- **Missing weather data**: Some accidents may have incomplete 7-day windows (data collection issues)
  - Solution: Require minimum 5 days to compute correlation, otherwise set weather_weight = 0.5 (uncertain)

**Performance considerations:**
- Computing correlations for 4,000 accidents per route query could be expensive
- Optimization: Pre-filter accidents by spatial radius BEFORE computing weather similarity
- Use PostGIS spatial indexes to get candidates, then apply weather filtering

---

### Decision #6: Accident Severity Weighting
**Date**: 2026-01-28
**Status**: ✅ Decided (General Risk Scoring), 🔮 Post-MVP Feature Planned (Per-Route Analytics)

**Question**: Should all accidents count equally, or should we weight by severity (fatal, serious, minor)?

**Decision**: **Subtle linear boosters** for general risk scoring + **detailed severity breakdowns** for per-route analytics

**Rationale**:

Fatal accidents carry *slightly* more signal about dangerous conditions, but the difference is marginal:

**Why fatal accidents are somewhat more informative:**
- Indicates conditions severe enough for fatal outcome
- Suggests steeper terrain (longer falls are more likely fatal)
- Correlates with larger avalanches, worse weather exposure
- Often means rescue was impossible/delayed (remoteness, bad weather)

**Why NOT to use large multipliers (5×, 10×):**
- **Luck plays enormous role**: Same fall, different landing = different outcome
- **Individual factors matter**: Experience level, gear failure, decision-making
- **Weather/conditions already weighted**: Spatial, temporal, and weather similarity already capture condition severity
- **Bias risk**: Over-emphasizing fatalities could miss important patterns in non-fatal accidents

**Key Insight**:
> "Fatal outcomes can be a little more informative (it is telling of if a route/area is steep enough for instance to cause fatal accidents) and may more strongly correlate to larger avalanches/worse weather etc. As such, I think we should use marginal weighting (nowhere near 5X) but maybe like a 'booster' linear score for fatal accidents rather than some accidental multiplier." - Sebastian

**Severity data distribution:**
```
Serious injuries: 2,153 (49.8%) - Majority of dataset
Fatal:           1,149 (26.6%) - Substantial portion
Unknown:           798 (18.5%) - Significant data gap
Minor:             219 (5.1%)  - Likely underreported
```

---

### General Risk Scoring: Severity Boosters

**Formula:**
```python
SEVERITY_BOOSTERS = {
    "fatal": 1.3,      # 30% boost - acknowledges higher signal
    "serious": 1.1,    # 10% boost - typical accident in our dataset
    "minor": 1.0,      # Baseline - still reveals dangerous conditions
    "unknown": 1.0     # Conservative - treat as minor, avoid bias
}

severity_weight = SEVERITY_BOOSTERS[accident.injury_severity]
```

**Effect on overall influence:**
```python
# Fatal accident with good weather/proximity match:
accident_influence = (spatial: 0.9) × (temporal: 0.8) × (weather: 0.95) ×
                     (route_type: 0.9) × (severity: 1.3)
                   = 0.878

# Minor injury with identical conditions:
accident_influence = (spatial: 0.9) × (temporal: 0.8) × (weather: 0.95) ×
                     (route_type: 0.9) × (severity: 1.0)
                   = 0.676

# Ratio: Fatal contributes 30% more than minor in identical conditions
```

**Why these specific values:**

**Fatal = 1.3× (30% boost):**
- Meaningful signal about severe conditions
- Not so large as to dominate other factors
- Balances informativeness with luck/individual factors

**Serious = 1.1× (10% boost):**
- Comprises 49.8% of dataset (this is "typical" accident)
- Broad category (broken arm to near-fatal)
- Small boost acknowledges medical significance

**Minor = 1.0× (baseline):**
- Only 5.1% of dataset (underreported)
- Still reveals dangerous conditions (climbers don't report trivial scrapes)
- Conservative baseline for comparison

**Unknown = 1.0× (same as minor):**
- 18.5% of data - too much to exclude
- Conservative approach: don't assume it's fatal
- Truly catastrophic accidents likely would have severity noted
- Avoids introducing bias toward higher severity

**Design principle:**
> "The booster values may be somewhat arbitrary but they are conservative enough to be relevant without overpowering our algo" - Sebastian

These subtle boosters acknowledge severity information without letting it dominate the sophisticated spatial/temporal/weather weighting system.

---

### Per-Route Analytics: Detailed Severity Breakdown (Post-MVP Feature)

For route-specific detail pages, display granular severity information:

```
Route: Longs Peak - Keyhole Route
Overall Risk Score: 68/100

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Accident History (Last 10 Years)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Fatal Accidents: 3
├─ Avg weather conditions: Temp 45°F, winds 25+ mph
├─ Common causes: Hypothermia (2), fall (1)
└─ Seasonal distribution: Winter (2), Fall (1)

Serious Injuries: 12
├─ Avg weather conditions: Temp 52°F, winds 15 mph
├─ Common causes: Altitude sickness (5), falls (4), rockfall (3)
└─ Seasonal distribution: Summer (8), Fall (4)

Minor Injuries: 2
├─ Mostly weather-related slips
└─ Summer climbing season

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Severity-Specific Risk Indicators
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Fatality Risk:        72/100 (High)
  → Steep exposure, weather-dependent

Serious Injury Risk:  65/100 (Moderate-High)
  → Altitude, rockfall hazards

Minor Injury Risk:    35/100 (Low)
  → Well-traveled route, good trail
```

**Implementation approach:**
- Compute separate risk scores filtering by severity
- Display counts, common causes, weather patterns by severity
- Allow users to understand specific risk types
- Part of route detail page UI, not core algorithm

**Why defer to post-MVP:**
- Adds UI complexity (multiple scores, charts)
- Requires accident cause analysis (not yet implemented)
- General risk score sufficient for MVP safety predictions
- Can validate general algorithm first, then add granularity

---

### Decision #7: Confidence Scoring System
**Date**: 2026-01-28
**Status**: ✅ Decided

**Question**: How do we communicate certainty/uncertainty in our risk predictions to users?

**Decision**: **Multi-factor confidence scoring** (Option C) with full transparency and detailed UI breakdowns

**Rationale**:

Confidence is **critically important** for a safety algorithm - perhaps more important than the risk score itself:

**Examples illustrating why confidence matters:**
```
Route A: Risk = 65/100, Confidence = 85% (47 nearby accidents, strong weather matches)
→ "Stay off this route - strong evidence of danger"

Route B: Risk = 65/100, Confidence = 25% (2 distant accidents, weak matches)
→ "Proceed with extreme caution - limited data but concerning signals"

Route C: Risk = 20/100, Confidence = 15% (zero nearby accidents)
→ "We don't have enough data to assess this route reliably"
```

Users need to know when to trust the algorithm vs. rely on their own judgment.

**Key Insight**:
> "Confidence (for a safety algorithm) is an EXTREMELY important aspect of our operation. Further, confidence is something that applies more to a particular route/area than it will to our general map presentation so it provides another category for analytic reporting." - Sebastian

**Why Multi-Factor (Option C) over simpler approaches:**
- Safety decisions require understanding WHAT makes us confident or uncertain
- Transparency builds trust in the algorithm
- Different quality indicators reveal different data gaps
- Enables users to make informed decisions about risk assessment validity

---

### Five Confidence Quality Indicators

Each factor scored 0.0 to 1.0, combined with weighted average:

#### **1. Sample Size (Weight: 30%)**

**Measures**: Number of accidents contributing to the risk score

```python
sample_size_score = min(n_total_accidents / 30.0, 1.0)

# 30+ accidents = maximum confidence in sample size
# 15 accidents = 50% confidence
# 5 accidents = 17% confidence
```

**Returns**:
```python
{
    'score': 0.0-1.0,
    'total_accidents': int,
    'significant_accidents': int,  # Accidents with influence > 0.3
    'interpretation': 'Excellent' | 'Good' | 'Moderate' | 'Limited'
}
```

**Why it matters**: More data points = more reliable statistical inference

---

#### **2. Match Quality (Weight: 30%)**

**Measures**: Average strength of accident matches (spatial + temporal + weather + route type)

```python
influences = [accident.total_influence for accident in all_accidents]
quality_score = mean(influences)  # Already 0-1 from normalized weights
```

**Returns**:
```python
{
    'score': 0.0-1.0,
    'avg_match_strength': float,
    'best_match_strength': float,
    'top_10_accidents': [list of highest-influence accidents],
    'interpretation': 'Excellent matches' | 'Good matches' | 'Moderate matches' | 'Weak matches'
}
```

**Why it matters**: High-quality matches (nearby, recent, similar weather) are more predictive than distant, old, different-weather accidents

---

#### **3. Spatial Coverage (Weight: 20%)**

**Measures**: Geographic distribution of accidents around the route

```python
# Bearing spread: Are accidents on all sides, or clustered on one side?
accident_bearings = [calculate_bearing(route, accident) for accident in accidents]
bearing_std = std(accident_bearings)
bearing_score = min(bearing_std / 90.0, 1.0)  # 90° spread = good coverage

# Distance variety: Mix of close and distant accidents
distance_variety = std(distances) / mean(distances)
distance_score = min(distance_variety, 1.0)

coverage_score = (bearing_score + distance_score) / 2.0
```

**Returns**:
```python
{
    'score': 0.0-1.0,
    'bearing_spread': float,  # Degrees
    'avg_distance_km': float,
    'closest_accident_km': float,
    'farthest_accident_km': float,
    'interpretation': 'Excellent coverage' | 'Good coverage' | 'Limited coverage' | 'Poor coverage (clustered)'
}
```

**Why it matters**: Accidents clustered on one side might miss hazards on other aspects of the route

---

#### **4. Temporal Recency (Weight: 10%)**

**Measures**: How recent is the most recent accident

```python
most_recent_days = min([(current_date - accident.date).days for accident in accidents])

if most_recent_days < 365:        # Within last year
    recency_score = 1.0
elif most_recent_days < 365 * 3:  # Within 3 years
    recency_score = 0.8
elif most_recent_days < 365 * 5:  # Within 5 years
    recency_score = 0.6
elif most_recent_days < 365 * 10: # Within 10 years
    recency_score = 0.4
else:                              # Older than 10 years
    recency_score = 0.2
```

**Returns**:
```python
{
    'score': 0.0-1.0,
    'most_recent_days': int,
    'most_recent_date': date,
    'avg_accident_age_years': float,
    'accidents_last_year': int,
    'accidents_last_5_years': int,
    'interpretation': 'Very recent data' | 'Recent data' | 'Moderately dated' | 'Historical data'
}
```

**Why it matters**: Recent accidents are more reliable indicators because conditions haven't changed (routes don't get rebolted, mountains don't change, but safety equipment evolves slowly)

---

#### **5. Weather Data Quality (Weight: 10%)**

**Measures**: Completeness of weather data for pattern matching

```python
accidents_with_weather = [a for a in accidents if a.has_7day_weather]
weather_coverage = len(accidents_with_weather) / len(accidents)

weather_similarities = [a.weather_weight for a in accidents_with_weather]
avg_weather_sim = mean(weather_similarities)

weather_quality_score = (weather_coverage + avg_weather_sim) / 2.0
```

**Returns**:
```python
{
    'score': 0.0-1.0,
    'accidents_with_weather': int,
    'weather_coverage_pct': float,
    'avg_weather_similarity': float,
    'interpretation': 'Excellent weather data' | 'Good weather data' | 'Limited weather data' | 'Poor weather data'
}
```

**Why it matters**: Weather similarity is a key component of our algorithm; missing weather data reduces prediction quality

---

### Overall Confidence Formula

```python
overall_confidence = (
    0.30 * sample_size_score +      # Most important: how much data do we have?
    0.30 * quality_score +           # Most important: how good are the matches?
    0.20 * coverage_score +          # Important: is coverage comprehensive?
    0.10 * recency_score +           # Moderate: are accidents recent?
    0.10 * weather_quality_score     # Moderate: do we have weather data?
)

# Returns: 0.0 to 1.0
```

**Weight rationale:**
- **Sample size + quality = 60%**: These directly determine statistical reliability
- **Coverage = 20%**: Important but less critical than having enough good matches
- **Recency + weather = 20%**: Moderate importance due to our year-scale temporal decay (old accidents still valuable)

---

### UI/UX Design: Displaying Confidence

**Key Principle**:
> "Displaying what influenced the confidence score (timelines, proximity, num incidents, similarity, etc.) is key info to the user which we can pull up on particular routes." - Sebastian

#### **Route Detail Page - Primary Display**

**Numerical + Visual (Battery-Style)**:
```
┌────────────────────────────────────────────┐
│ CONFIDENCE                                  │
│ ┌────────────────────────────────────────┐ │
│ │ ██████████████████████████████████░░ 85│ │ ⚡⚡⚡ High
│ └────────────────────────────────────────┘ │
│ ✓ High confidence - based on 34 accidents  │
└────────────────────────────────────────────┘
```

**Material Design 3 Components**:
- Linear progress indicator (filled to 85%)
- Lightning bolt icons (⚡⚡⚡ for high, ⚡⚡ for medium, ⚡ for low)
- Color coding:
  - Green: 70-100% (high confidence)
  - Yellow/Orange: 40-69% (moderate confidence)
  - Red: 0-39% (low confidence)

#### **Expandable Confidence Breakdown**

**Five-Factor Breakdown with Visual Progress Bars**:
```
┌─── WHY THIS CONFIDENCE SCORE? ────────────┐
│                                            │
│ Sample Size           [████████░] 90%     │
│ 34 total accidents                         │
│ 18 high-quality matches                    │
│                                            │
│ Match Quality         [███████░░] 72%     │
│ Strong weather & proximity matches         │
│ Top match: 0.89 similarity                 │
│                                            │
│ Geographic Coverage   [█████░░░░] 58%     │
│ Accidents spread across east face          │
│ Limited coverage on west side              │
│                                            │
│ Data Recency          [████████░] 95%     │
│ Most recent: 6 months ago                  │
│ 12 accidents in last 3 years               │
│                                            │
│ Weather Data          [███████░░] 78%     │
│ 28/34 accidents have full weather data     │
│ Avg weather similarity: 0.74               │
│                                            │
│ [View Contributing Accidents →]           │
└────────────────────────────────────────────┘
```

**Each factor shows**:
- Visual progress bar
- Percentage score
- 2-3 key statistics
- Human-readable interpretation

#### **Contributing Accidents List (Expandable)**

**Sorted by influence, color-coded by strength**:
```
┌─── CONTRIBUTING ACCIDENTS (34 total) ──────┐
│                                             │
│ #1 - Fatal (Feb 15, 2024)  ████████ 0.89  │
│ Distance: 3.2km | Weather: 92% | 6mo ago   │
│ Temp 28°F, winds 35mph, heavy snow         │
│ [View Full Report →]                       │
│                                             │
│ #2 - Serious (Jan 8, 2024) ███████░ 0.84  │
│ Distance: 5.1km | Weather: 88% | 7mo ago   │
│ Temp 32°F, winds 28mph, moderate snow      │
│ [View Full Report →]                       │
│                                             │
│ #3 - Fatal (Dec 20, 2023)  ██████░░ 0.76  │
│ Distance: 8.7km | Weather: 81% | 1yr ago   │
│ Temp 25°F, winds 42mph, blizzard           │
│ [View Full Report →]                       │
│                                             │
│ [Load more... 31 remaining]                │
│                                             │
│ Color Legend:                              │
│ ████ Dark Red (>0.7) = High influence      │
│ ████ Orange (0.4-0.7) = Moderate influence │
│ ░░░░ Gray (<0.4) = Low influence           │
└─────────────────────────────────────────────┘
```

**Features**:
- Top 10 accidents shown initially
- Each accident card clickable to full accident report
- Visual influence bars with color coding
- Key stats: distance, weather match, time ago
- Weather conditions summary

#### **Map Overview (Simplified Display)**

For general map markers/popups:
```
┌────────────────────────────┐
│ Longs Peak - Keyhole       │
├────────────────────────────┤
│ Risk: 68/100 (High) ⚠️     │
│ Confidence: 85% ⚡⚡⚡       │
│                            │
│ [View Details →]           │
└────────────────────────────┘
```

Simple, glanceable information. Details accessible via click-through.

---

### Handling Edge Cases

#### **Zero-Accident Routes**

Routes with no nearby accidents naturally get low risk + low confidence:

```python
# Example: Remote route, no accidents within 100km
# System searches wider (200km, 300km) but weights are very low

Risk Score: 15/100 (Low)
Confidence: 12% ⚡ (Very Low)

UI Display:
┌────────────────────────────────────────────┐
│ ⚠️ LIMITED DATA AVAILABLE                  │
│                                            │
│ Risk assessment is uncertain due to lack   │
│ of nearby accident history.                │
│                                            │
│ Recommendation:                            │
│ • Use standard alpine safety protocols     │
│ • Rely on your own route assessment        │
│ • Check local climbing forums/guides       │
│ • Consider weather forecasts independently │
│                                            │
│ Why low confidence?                        │
│ • Only 3 accidents within 150km            │
│ • All accidents distant (>100km)           │
│ • Limited weather pattern matches          │
└────────────────────────────────────────────┘
```

**Philosophy**: Honest uncertainty is valuable information. Users should know when the algorithm can't help them.

#### **Routes with Many Old Accidents**

```
Risk Score: 55/100 (Moderate)
Confidence: 48% ⚡⚡ (Moderate)

Confidence breakdown shows:
Sample Size:     [████████░] 85% (good - 42 accidents)
Match Quality:   [██████░░░] 65% (moderate)
Coverage:        [███████░░] 72% (good)
Data Recency:    [██░░░░░░░] 25% (poor - oldest: 15 years ago)
Weather Data:    [████████░] 88% (excellent)

Overall: Moderate confidence
→ Good amount of data, but dated. Conditions may have changed.
```

---

### Implementation Notes

**Database queries needed**:
1. Fetch all accidents within reasonable search radius (200km max)
2. Calculate individual accident influences (already needed for risk score)
3. Compute confidence metrics from accident set
4. Store breakdown for UI display

**Response format** (API endpoint):
```json
{
  "route_id": 123,
  "risk_score": 68,
  "confidence": {
    "overall": 0.85,
    "breakdown": {
      "sample_size": {
        "score": 0.90,
        "total_accidents": 34,
        "significant_accidents": 18,
        "interpretation": "Excellent"
      },
      "match_quality": { ... },
      "spatial_coverage": { ... },
      "temporal_recency": { ... },
      "weather_data_quality": { ... }
    },
    "contributing_accidents": [
      {
        "accident_id": 4215,
        "influence": 0.89,
        "distance_km": 3.2,
        "days_ago": 180,
        "weather_similarity": 0.92,
        "severity": "fatal",
        "date": "2024-02-15"
      },
      // ... top 50 accidents
    ]
  }
}
```

**Frontend implementation**:
- Material-UI LinearProgress component for bars
- Custom color mapping based on score ranges
- Expandable Accordion components for breakdowns
- List component with infinite scroll for accident history

**Performance considerations**:
- Confidence calculation adds minimal overhead (uses same accident data as risk scoring)
- Contributing accident list should be limited to top 50 for UI performance
- Full list available via pagination if user requests

---

### Post-MVP Enhancements

**Planned improvements**:

1. **Confidence-adjusted risk scores**:
   ```
   Conservative estimate = risk_score * confidence
   Example: 65/100 risk × 0.85 confidence = 55/100 conservative risk
   ```

2. **Route comparison confidence**:
   ```
   "Route A has higher confidence (85%) than Route B (42%),
    so we trust Route A's risk score more"
   ```

3. **Confidence trends over time**:
   ```
   Graph showing how confidence changes as new accidents are added
   "Confidence has increased 15% in the last year due to 8 new accidents"
   ```

4. **Regional confidence heatmaps**:
   ```
   Map overlay showing which areas have high vs low data coverage
   Helps identify data gaps for future collection efforts
   ```

---

## Additional Notes

The following parameters need to be determined in subsequent design sessions:

### Temporal Decay
- [x] **λ (lambda)**: Exponential decay factor - DECIDED (see Decision #4)
- [x] **Seasonal boost**: 1.5× multiplier for same-season accidents - DECIDED (see Decision #4)

### Weather Similarity
- [x] **Weather distance metric**: Pattern correlation approach - DECIDED (see Decision #5)
- [x] **Factor weighting**: Equal weighting across all 6 factors - DECIDED (see Decision #5)
- [x] **Extreme weather threshold**: 2.0 SD for all metrics - DECIDED (see Decision #5)
- [x] **Within-window decay**: 0.85 default (post-MVP backtesting planned) - DECIDED (see Decision #5)
- [x] **Time window**: 7-day pre-accident window confirmed - DECIDED (see Decision #5)

### Accident Severity
- [x] **Severity weighting strategy**: Subtle linear boosters - DECIDED (see Decision #6)
- [x] **Per-route analytics**: Separate severity breakdowns for route detail pages - PLANNED (post-MVP)

### Confidence Scoring
- [x] **Confidence calculation method**: Multi-factor scoring (5 quality indicators) - DECIDED (see Decision #7)
- [x] **UI display**: Numerical + visual (battery-style) with detailed breakdown - DECIDED (see Decision #7)
- [x] **Zero-accident handling**: Natural low confidence via weighting system - DECIDED (see Decision #7)

---

## Research References

### Financial Volatility Models (GARCH, EWMA)
- [Hybrid GARCH and Deep Learning for Volatility Prediction (2024)](https://onlinelibrary.wiley.com/doi/10.1155/2024/6305525)
- [GARCH Volatility Forecasting Guide](https://portfoliooptimizer.io/blog/volatility-forecasting-garch11-model/)
- [Mastering GARCH Models for Financial Time Series](https://medium.com/@sheikh.sahil12299/mastering-volatility-forecasting-with-garch-models-a-deep-dive-into-financial-market-dynamics-8df73c037b7e)

**Key Takeaway**: Exponentially weighted moving average (EWMA) naturally emphasizes recent events, which is ideal for temporal weighting.

### Spatial-Temporal Risk Prediction (Crime Hotspots)
- [Spatio-Temporal Kernel Density Estimation for Crime Hotspot Mapping](https://www.sciencedirect.com/science/article/abs/pii/S0143622818300560)
- [Temporal Network Kernel Density Estimation (2024)](https://onlinelibrary.wiley.com/doi/abs/10.1111/gean.12368)
- [Spatial Analysis for Crime Prediction Using KDE](https://www.researchgate.net/publication/380263593_Spatial_Analysis_Leveraging_Geographic_Information_System_and_Kernel_Density_Estimation_for_Crime_Prediction)

**Key Takeaway**: Gaussian kernel for spatial decay creates smooth "risk surface" where influence decreases with distance.

### Weather Pattern Matching (Time Series Similarity)
- [Angle-Distance Penalized DTW (2025)](https://www.nature.com/articles/s41598-025-29331-5)
- [Time Series Similarity Using DTW Explained](https://medium.com/walmartglobaltech/time-series-similarity-using-dynamic-time-warping-explained-9d09119e48ec)
- [Multiscale Dubuc Distance for Time Series (2024)](https://arxiv.org/html/2411.10418v1)

**Key Takeaway**: Dynamic Time Warping (DTW) allows flexible matching of weather patterns even with temporal shifts. Start with simpler Euclidean distance, upgrade to DTW if needed.

---

## Implementation Notes

### Database Queries
- Use PostGIS `ST_Distance` for spatial distance calculations
- Pre-compute accident weather severity scores during data loading
- Consider caching frequently-accessed route risk scores

### Performance Considerations
- ~4,000 accidents means computing 4,000 weights per route query
- Optimize with spatial indexing (PostGIS GIST indexes already in place)
- Consider pre-filtering accidents by bounding box before detailed calculations

### Code Structure (Proposed)
```
backend/app/services/
├── safety_algorithm.py          # Main algorithm orchestration
├── spatial_weighting.py         # Distance-based weight calculations
├── temporal_weighting.py        # Time-based decay calculations
├── weather_similarity.py        # Weather pattern matching
├── route_type_weighting.py      # Route type asymmetric weights
└── risk_scoring.py              # Final score normalization and confidence
```

---

## Testing Strategy

### Validation Approach
1. **Backtesting**: Use known accident dates to predict risk, see if high-risk scores correlate with actual accidents
2. **Geographic validation**: Routes with many accidents should score higher than routes with few
3. **Weather validation**: Same route should have different scores under different weather conditions
4. **Temporal validation**: Risk scores should decay as accidents age
5. **Edge cases**: Test with sparse data (remote routes), dense data (popular peaks), extreme weather

### Success Metrics
- **Precision**: Do high-risk predictions correspond to actual dangerous conditions?
- **Recall**: Do we catch most dangerous conditions (not too many false negatives)?
- **Calibration**: Is a "70% risk" score actually dangerous 70% of the time?
- **Explainability**: Can we show users WHY a route has a certain score (which accidents contributed)?

---

## Open Questions

Questions to address in future design sessions:

1. **Temporal decay rate**: How fast should accident relevance fade?
2. **Weather similarity metric**: Which weather factors matter most?
3. **Severity weighting**: Equal or proportional to injury severity?
4. **Confidence scoring**: How to communicate uncertainty to users?
5. **Seasonal adjustments**: Should we only compare to same-season historical data?
6. **Route popularity bias**: Popular routes have more accidents simply due to traffic - how to adjust?
7. **Zero-accident routes**: How to score routes with no nearby historical accidents?

---

### Route Database Expansion Strategy
**Date**: 2026-01-28
**Status**: ✅ Decided - Defer to Post-MVP

**Current State**:
- 622 routes in database (from initial Mountain Project scraping)
- 4,319 accidents in database
- Only 16.9% of accidents linked to specific routes (729/4,319)

**Decision**: **Defer comprehensive route scraping** until post-MVP

**Rationale**:
- Risk calculation works for ANY coordinates (user can click anywhere on map)
- Existing 622 routes sufficient for route detail pages in MVP
- Pre-indexed routes not required for core algorithm functionality
- Can expand based on user demand and usage patterns

**MVP Approach**:
- Use existing 622 routes for named route lookups
- Allow risk calculation for arbitrary lat/lon coordinates
- Display route details when available, generic location info otherwise

**Post-MVP Expansion Plan**:
- Target popular climbing areas (Yosemite, RMNP, North Cascades, etc.)
- Prioritize routes with accident history (we know these matter)
- Scrape 2,000-5,000 additional routes from Mountain Project
- Consider alternative sources (OpenBeta, PeakBagger) for non-technical routes

**Key Insight**:
> "We may need to do some additional route scraping from MP more comprehensively given this approach... we can always push that off for later though" - Sebastian

**Why this works**: Algorithm calculates risk for any location; route database is just metadata for display/search, not core functionality.

---

## Next Steps

### Algorithm Design (Complete! ✅)
- [x] ✅ Decide on spatial influence strategy (Question #1) - Decision #1
- [x] ✅ Decide on route type weighting strategy (Question #2) - Decision #2, #3
- [x] ✅ Decide on temporal decay parameters (Question #3) - Decision #4
- [x] ✅ Design weather similarity metric (Question #4) - Decision #5
- [x] ✅ Decide on severity weighting strategy (Question #5) - Decision #6
- [x] ✅ Design confidence scoring system (Question #6) - Decision #7
- [x] ✅ Decide on route database expansion strategy - Defer to post-MVP

### Implementation (Complete! ✅)
- [x] ✅ Write complete algorithm pseudocode (all components integrated)
- [x] ✅ Implement in Python (backend/app/services/)
  - [x] ✅ spatial_weighting.py
  - [x] ✅ temporal_weighting.py
  - [x] ✅ weather_similarity.py
  - [x] ✅ route_type_weighting.py
  - [x] ✅ confidence_scoring.py
  - [x] ✅ safety_algorithm.py (orchestrator)
  - [x] ✅ algorithm_config.py (all parameters)
  - [x] ✅ severity_weighting.py
- [x] ✅ Create preprocessing script for weather_statistics table
- [x] ✅ Create API endpoint: POST /api/v1/predict
- [x] ✅ Create test cases for each component (50/50 passing)
- [x] ✅ Validate with historical data

### Post-MVP Optimizations
- [ ] Optimize within-window temporal decay via backtesting (Decision #5)
- [ ] Implement weather-conditional route type weighting (Decision #2, Option B)
- [x] ✅ Expand route database (196,000+ routes scraped from Mountain Project)
- [ ] Add severity-specific risk scores for route analytics (Decision #6)
- [ ] Implement confidence-adjusted risk scores
- [ ] Create regional confidence heatmaps

---

## Algorithm Design Summary

### Complete Formula (All Components Integrated)

```python
def calculate_route_risk_and_confidence(route_location, route_type, current_weather, current_date):
    """
    Master safety prediction algorithm combining all 7 decision components.

    Returns: {
        'risk_score': 0-100,
        'confidence': 0.0-1.0,
        'confidence_breakdown': {...},
        'contributing_accidents': [...]
    }
    """

    # 1. Fetch all accidents (no hard distance cutoff - Decision #1)
    all_accidents = get_accidents_in_region(route_location, max_radius_km=300)

    accident_influences = []

    for accident in all_accidents:
        # 2. Spatial weighting (Gaussian decay, route-type-specific bandwidth - Decisions #1, #3)
        spatial_bandwidth = SPATIAL_BANDWIDTH[route_type]  # 75km alpine, 25km sport, etc.
        distance_km = haversine_distance(route_location, accident.coordinates)
        spatial_weight = exp(-distance_km² / (2 * spatial_bandwidth²))

        # 3. Temporal weighting (year-scale decay with seasonal boost - Decision #4)
        days_elapsed = (current_date - accident.date).days
        lambda_decay = TEMPORAL_LAMBDA[route_type]  # 0.9998 alpine, 0.999 sport, etc.
        temporal_weight = lambda_decay ** days_elapsed

        # Seasonal boost (1.5× if same season)
        seasonal_boost = 1.5 if same_season(accident.date, current_date) else 1.0
        temporal_weight *= seasonal_boost

        # 4. Weather similarity (pattern correlation + extreme detection - Decision #5)
        accident_7day_weather = get_7day_weather(accident)
        current_7day_weather = current_weather  # Includes forecast
        location_stats = get_weather_statistics(route_location, current_season)

        # Pattern correlation (equal weighting: temp, precip, wind, visibility, clouds, freeze-thaw)
        pattern_similarity = calculate_pattern_similarity(current_7day_weather, accident_7day_weather)

        # Extreme weather penalty (2.0 SD threshold)
        extreme_penalty = calculate_extreme_weather_penalty(current_7day_weather, location_stats)

        weather_weight = pattern_similarity * extreme_penalty  # Can exceed 1.0 if extreme

        # 5. Route type weighting (asymmetric matrix - Decision #2)
        route_type_weight = ROUTE_TYPE_WEIGHTS[(route_type, accident.route_type)]
        # e.g., (alpine, sport) = 0.9, (sport, alpine) = 0.3

        # 6. Severity weighting (subtle boosters - Decision #6)
        severity_weight = SEVERITY_BOOSTERS[accident.injury_severity]
        # fatal: 1.3, serious: 1.1, minor: 1.0, unknown: 1.0

        # COMBINE ALL WEIGHTS
        # Implementation update (2026-02-06):
        # - Weather uses CUBIC power (weather³) for 27× sunny/stormy variation
        # - Strong weather influence ensures poor matches contribute minimally

        WEATHER_EXCLUSION_THRESHOLD = 0.0  # Disabled - cubic weighting handles this
        WEATHER_POWER = 3  # Cubic for stronger weather influence

        if weather_weight < WEATHER_EXCLUSION_THRESHOLD:
            continue  # Skip accidents with very poor weather match

        weather_factor = weather_weight ** WEATHER_POWER  # Cubic amplification

        total_influence = (
            spatial_weight *
            temporal_weight *
            weather_factor *  # Now quadratic
            route_type_weight *
            severity_weight
        )

        accident_influences.append({
            'accident': accident,
            'influence': total_influence,
            'breakdown': {
                'spatial': spatial_weight,
                'temporal': temporal_weight,
                'weather': weather_factor,
                'route_type': route_type_weight,
                'severity': severity_weight
            }
        })

    # 7. Calculate risk score (sum of influences, normalized to 0-100)
    total_risk = sum([ai['influence'] for ai in accident_influences])
  risk_score = min(total_risk * 7.0, 100)  # Normalize (tuned: 7.0 surfaces moderate/high risk)

    # 8. Calculate confidence score (multi-factor - Decision #7)
    confidence_data = calculate_confidence(
        accidents=[ai['accident'] for ai in accident_influences],
        route_location=route_location,
        current_date=current_date
    )

    return {
        'risk_score': risk_score,
        'confidence': confidence_data['overall_confidence'],
        'confidence_breakdown': confidence_data['breakdown'],
        'contributing_accidents': sorted(
            accident_influences,
            key=lambda x: x['influence'],
            reverse=True
        )[:50]  # Top 50 for UI
    }
```

### Decision Summary Table

| Component | Decision | Key Parameters | Status |
|-----------|----------|----------------|--------|
| **Spatial Weighting** | Gaussian decay, no hard cutoff | bandwidth varies by route type | ✅ Complete |
| **Spatial Bandwidth** | Alpine 75km, Trad 40km, Sport 25km | Tunable per route type | ✅ Complete |
| **Route Type Matrix** | Asymmetric (alpine ← sport: 0.9, sport ← alpine: 0.3) | 9 weight combinations | ✅ Complete |
| **Temporal Decay** | Year-scale (λ=0.9998 to 0.999) | Route-type-specific | ✅ Complete |
| **Seasonal Boost** | 1.5× multiplier for same season | Winter/Spring/Summer/Fall | ✅ Complete |
| **Weather Similarity** | Pattern correlation (equal weighting) | 6 factors, 2.0 SD extreme threshold | ✅ Complete |
| **Weather Power** | **Cubic (weather³)** | 27× sunny/stormy variation | ✅ Tuned |
| **Weather Exclusion** | Disabled (cubic handles naturally) | N/A | ✅ Tuned |
| **Within-Window Decay** | 0.85 default (2.5× Day-0 vs Day-6) | Post-MVP backtesting planned | ✅ Complete |
| **Severity Weighting** | Subtle boosters (1.3×, 1.1×, 1.0×) | Fatal, Serious, Minor/Unknown | ✅ Complete |
| **Confidence Scoring** | Multi-factor (5 indicators) | Weighted 30/30/20/10/10 | ✅ Complete |
| **Normalization Factor** | **7.0** (was 5.0) | Surfaces moderate/high risk while retaining headroom | ✅ Tuned |

### Algorithm Characteristics

**Strengths**:
- ✅ No arbitrary cutoffs (all data contributes proportionally)
- ✅ Multi-dimensional similarity (space + time + weather + route type)
- ✅ Transparent confidence scoring (users know when to trust predictions)
- ✅ Adaptive to data density (works with sparse or rich data)
- ✅ Research-backed design (GARCH, STKDE, DTW methodologies)
- ✅ Tunable parameters (can optimize via backtesting)

**Limitations**:
- Requires comprehensive weather data (91% coverage achieved)
- Computationally expensive for real-time queries (4,000 calculations per route)
- Assumes historical patterns predict future risk (not guaranteed)
- Cannot account for route-specific changes (new bolts, rockfall cleaning)
- Limited by accident reporting bias (minor injuries underreported)

**Mitigation strategies**:
- Pre-compute risk scores for known routes (cache results)
- Use spatial indexing (PostGIS GIST) to pre-filter accidents
- Display confidence prominently to communicate uncertainty
- Update risk scores when conditions change significantly
- Supplement with real-time avalanche forecasts, weather alerts

### Expected Performance

**Typical query**:
```
Route: Longs Peak Keyhole Route
Current conditions: Clear, 45°F, 15mph winds
Time: June 15, 2026 (summer)

Processing:
1. Fetch accidents within 200km: ~150 accidents (PostGIS index: <50ms)
2. Calculate 150 influence scores: ~200ms
3. Calculate confidence metrics: ~50ms
4. Sort and format response: ~20ms

Total: ~320ms response time
```

**Optimization opportunities**:
- Cache weather statistics (location + season) - saves 50ms
- Pre-compute for popular routes - serves from cache
- Parallel processing of accident influences - reduces to 100ms

---

*This is a living document. Update as design decisions are made or implementation reveals new insights.*

**Design Phase Complete**: 2026-01-28
**Implementation Complete**: 2026-01-30
**Testing Complete**: 2026-01-30 (50/50 tests passing, 100%)
**Last Updated**: 2026-02-06
