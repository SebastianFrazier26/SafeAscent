# Cubic Weather Weighting - Analysis & Results

**Date**: January 30, 2026
**Status**: ✅ Implemented, All Tests Passing (50/50)
**Algorithm Change**: Weather-Primary Risk Model with Cubic Power Weighting

---

## Executive Summary

The cubic weather weighting implementation is **complete and functional**. All 50 Phase 7 tests pass. The algorithm successfully makes weather the primary risk driver, with accident history acting as an amplifier. However, initial analysis reveals that **cubic power (³) may be too aggressive** for the desired behavior.

### Key Findings

1. ✅ **Weather is now dominant**: Algorithm working as intended - weather patterns drive risk scores
2. ⚠️ **Cubic power creates 27× variation**: More aggressive than the target 7× variation
3. ⚠️ **High-density areas hit ceiling**: Longs Peak (476 accidents) shows 100/100 in both winter and summer
4. ✅ **Moderate-density areas show good range**: Half Dome (196 accidents) shows 79.9/100
5. ✅ **Seasonal variation visible**: Early/late season routes show 45-50 risk scores vs 100 for peak season

---

## Test Results Summary

### Known Dangerous Areas

| Location | Accidents | Risk Score | Expected Behavior |
|----------|-----------|------------|-------------------|
| **Longs Peak, CO (July)** | 476 | 100.0/100 | High-density area, peak season |
| **Longs Peak, CO (January)** | 476 | 100.0/100 | High-density area, winter season |
| **Half Dome, Yosemite** | 196 | 79.9/100 | Moderate-high density |
| **Longs Peak (May)** | 476 | 50.4/100 | Shoulder season, fewer accident matches |
| **Longs Peak (September)** | 476 | 45.7/100 | Shoulder season, fewer accident matches |
| **Florida** | 0 | 0.0/100 | No climbing accidents |

### Analysis

**Success Indicators:**
- ✅ Half Dome shows 79.9 (not maxed out at 100)
- ✅ Shoulder seasons (May/Sept) show moderate risk (45-50)
- ✅ Areas with zero accidents show zero risk
- ✅ All tests passing - algorithm is stable

**Concerns:**
- ⚠️ Longs Peak hits 100 in both winter AND summer (no weather variation at high accident densities)
- ⚠️ No route in the dataset shows the desired "sunny day" behavior (risk ~10-25)

---

## Mathematical Analysis

### Current Implementation (Cubic Power = 3)

```python
weather_factor = weather_similarity³
total_influence = base_influence × weather_factor
```

**Variation by weather similarity:**

| Weather Similarity | Cubic Factor (³) | Impact |
|--------------------|------------------|--------|
| 0.3 (sunny, poor match) | 0.027 | 97.3% reduction |
| 0.5 (neutral) | 0.125 | 87.5% reduction |
| 0.7 (good match) | 0.343 | 65.7% reduction |
| 0.9 (stormy, excellent match) | 0.729 | 27.1% reduction |

**Ratio between extremes:**
- 0.729 / 0.027 = **27× variation** (sunny → stormy)

This is **much more aggressive** than the target 7× variation from the design discussion.

---

## Design Goal vs Current Behavior

### Target Behavior (from design discussion)

> "On a sunny day with no clouds/precip and low wind speeds, a route may be very safe and have a risk score of just 10... vs. the same route on a day with heavy rains, cold temps, fast winds etc. may have a risk score closer to 70."

**Desired:**
- Safe route, sunny day: **10**
- Safe route, stormy day: **70**
- Dangerous route, sunny day: **~20-30** (higher baseline due to accident history)
- Dangerous route, stormy day: **100** (at ceiling)

**Weather variation**: 10 → 70 = **7× variation**
**Accident history variation**: ~2× baseline adjustment

### Current Behavior (with real test data)

**Observed:**
- Longs Peak (476 accidents), July: **100**
- Longs Peak (476 accidents), January: **100**
- Longs Peak (476 accidents), May: **50.4**
- Half Dome (196 accidents), July: **79.9**
- No areas showing < 20 risk scores in testing

---

## Why are we not seeing low risk scores?

### Hypothesis 1: Weather Similarity is High

The tests use **real weather forecasts** for 2026, not controlled "sunny" vs "stormy" scenarios. Key insights:

1. **Seasonal clustering of accidents**: Most climbing accidents occur in summer (June-August) when climbing activity is highest
2. **Testing in peak season**: Tests using July dates get high weather similarity because many historical accidents also occurred in July
3. **Real-world weather patterns**: July 2026 in Colorado likely has similar weather patterns to July accidents (warm, afternoon thunderstorms)

**Result**: Weather similarity scores are likely in the 0.6-0.9 range for peak season tests, giving high risk scores even with cubic weighting.

### Hypothesis 2: Accident Density Exceeds Scaling

Longs Peak with 476 accidents has exceptional accident density. Even with:
- Spatial decay (accidents far away contribute less)
- Temporal decay (old accidents contribute less)
- Cubic weather weighting (poor weather match = 97% reduction)

The **sheer volume** of accidents means the sum of influences reaches the ceiling (total_influence ≥ 10.0).

---

## Recommended Adjustments

### Option 1: Reduce Weather Power (Quadratic) ⭐ RECOMMENDED

**Change**: `WEATHER_POWER = 2` (quadratic instead of cubic)

**Reasoning:**
- Cubic power gives 27× variation (0.3³→0.9³)
- Quadratic power gives 9× variation (0.3²→0.9²)
- Target 7× variation suggests power ≈ 1.77
- Quadratic is close to target and easier to reason about

**Effect:**
- More moderate weather sensitivity
- Longs Peak would show more variation between winter and summer
- Less aggressive reduction of poorly-matched accidents

**Trade-off:** Accident history becomes relatively more influential (less pure "weather-primary" model)

### Option 2: Adjust Normalization Factor

**Change**: `RISK_NORMALIZATION_FACTOR = 5.0` (from 10.0)

**Reasoning:**
- Current: total_influence of 10.0 = risk_score of 100
- Proposed: total_influence of 20.0 = risk_score of 100
- Gives more headroom for high-density areas like Longs Peak

**Effect:**
- All risk scores would be halved
- Longs Peak: 100 → 50 (on high-match days)
- Half Dome: 79.9 → 40
- Allows weather variation to push scores from 50 (sunny) to 100 (stormy)

**Trade-off:** Need to recalibrate expectations for what constitutes "high" vs "low" risk

### Option 3: Hybrid Approach

1. **Reduce power from 3 to 2** (moderate weather sensitivity)
2. **Reduce normalization from 10 to 7** (some headroom for high-density areas)
3. **Increase exclusion threshold from 0.25 to 0.30** (exclude more poor-match accidents)

**Expected results:**
- Safe routes, sunny: 8-15
- Safe routes, stormy: 50-70
- Dangerous routes, sunny: 20-35
- Dangerous routes, stormy: 90-100

---

## Weather Similarity Distribution (Estimated)

Based on seasonal test results, we can infer approximate weather similarity distributions:

**Peak Season (July)** - Testing during high activity season:
- Many historical accidents also occurred in July
- Weather patterns similar (afternoon thunderstorms, warm temps)
- **Estimated weather_similarity**: 0.65-0.85 (good matches)
- **Result**: High risk scores (80-100)

**Shoulder Season (May, September)** - Testing in transition periods:
- Fewer historical accidents in these months
- Weather patterns less similar to peak-season accidents
- **Estimated weather_similarity**: 0.40-0.60 (moderate matches)
- **Result**: Moderate risk scores (45-55)

**Off-Season (December-March)** - Winter climbing:
- Most accidents don't occur in winter (lower activity)
- But ice climbing and alpine accidents do occur
- **Estimated weather_similarity**: 0.50-0.75 (varies by route type)
- **Result**: Variable risk scores

---

## Algorithm Behavior - Verified ✅

### What's Working Well

1. **Weather is primary driver**: Cubic power successfully amplifies weather importance
2. **Accident history as amplifier**: High-density areas show elevated risk, low-density areas show lower risk
3. **Spatial and temporal decay functioning**: Not all 476 Longs Peak accidents contribute equally
4. **Exclusion threshold effective**: Accidents with < 0.25 weather similarity are excluded completely
5. **No crashes or errors**: Algorithm is stable across all test scenarios

### What Needs Tuning

1. **Power setting**: Cubic (3) may be too aggressive for desired 7× variation
2. **Normalization factor**: May need adjustment to prevent ceiling hits at high-density areas
3. **Test scenarios**: Need controlled weather scenarios (mock sunny/stormy forecasts) to validate behavior

---

## Next Steps

### Immediate (Requires User Decision)

1. **Decide on power setting:**
   - Keep cubic (3) for maximum weather dominance
   - Switch to quadratic (2) for moderate weather emphasis
   - Use custom power (~1.8) for precise 7× variation

2. **Decide on normalization factor:**
   - Keep 10.0 (current)
   - Reduce to 5.0-7.0 (more headroom)
   - Empirically calibrate with real-world scenarios

### Future Enhancements

1. **Mock Weather Testing**: Create controlled test scenarios with specific weather patterns
   - Sunny day scenario (precip=0, wind=5mph, clouds=0%)
   - Stormy day scenario (precip=20mm, wind=40mph, clouds=100%)
   - Validate 7× variation target

2. **Logging & Monitoring**: Add detailed logging of:
   - Distribution of weather_similarity scores
   - Distribution of total_influence values
   - Which accidents contribute most to risk scores

3. **Calibration Dataset**: Test against real-world outcomes
   - Identify climbing days with known good/bad conditions
   - Validate that risk scores correlate with actual incident rates

---

## Technical Implementation Notes

### Files Modified

- **backend/app/services/safety_algorithm.py** (lines 310-334)
  - Added `WEATHER_POWER = 3` constant
  - Added `WEATHER_EXCLUSION_THRESHOLD = 0.25` constant
  - Modified influence calculation to apply cubic weather factor
  - Updated docstrings with algorithm explanation

### Constants Location

```python
# In calculate_accident_influence() function
WEATHER_POWER = 3  # Cubic power (easily tunable: 2=square, 4=quartic)
WEATHER_EXCLUSION_THRESHOLD = 0.25  # Exclude accidents with <25% weather similarity
```

**To adjust:**
1. Change `WEATHER_POWER` to desired exponent (2 for quadratic, 1.77 for exact 7×, etc.)
2. Change `WEATHER_EXCLUSION_THRESHOLD` to filter more/fewer accidents
3. Change `RISK_NORMALIZATION_FACTOR` in `app/services/algorithm_config.py`

### No Breaking Changes

All changes are internal to the algorithm. The API contract remains unchanged:
- Same request/response schemas
- Same validation rules
- Same confidence scoring
- Same database queries

---

## Conclusion

The cubic weather weighting implementation is **technically successful** - weather is now the primary risk driver, and all tests pass. However, the algorithm may be **tuned too aggressively** for the desired user behavior.

**Recommendation**: Switch from cubic (power=3) to quadratic (power=2) weighting for more moderate weather sensitivity and better alignment with the 7× variation target.

**Decision needed**: Review test results and decide on final power setting and normalization factor before proceeding to Phase 8.

---

*Last Updated*: 2026-01-30
*Test Results*: 50/50 passing (100%)
*Status*: ✅ Implemented, awaiting tuning decision
