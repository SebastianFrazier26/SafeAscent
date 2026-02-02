# Weather Data Backfill - Critical Data Quality Fix

**Date**: January 29, 2026
**Issue**: Missing 7-day weather windows for accidents
**Priority**: **CRITICAL** - Blocks algorithm accuracy
**Status**: Script ready, awaiting execution

---

## Problem Discovered

During Phase 7C testing, we discovered that **0% of accidents have complete 7-day weather windows**:

- **98.7% of accidents** (3,903) have only **1-2 days** of weather data
- **1.3% of accidents** (52) have **0 days** of weather data
- **0% of accidents** have the required **7-day window** (days -6 to 0)

### Root Cause

The original weather collection script (`collect_weather_data.py`) had a logical flaw:

**What it did:**
- Fetched weekly weather (Monday-Sunday)
- Only linked each day to accidents that occurred ON that specific day

**What we needed:**
- For each accident, fetch the **7 days BEFORE the accident** (days -6 to 0 relative to accident date)

**Example:**
```
Accident on Wednesday, July 12, 2023

❌ Current approach:
   - Gets only July 12 (1 day)

✅ Needed approach:
   - Gets July 6-12 (7 days: -6, -5, -4, -3, -2, -1, 0)
```

---

## Impact on Algorithm

Without proper 7-day windows:
- ❌ Weather pattern correlation is meaningless (comparing 1 day to 7 days)
- ❌ Freeze-thaw cycle detection doesn't work (needs daily min/max temps)
- ❌ Within-window temporal decay fails (needs all 7 days weighted)
- ❌ Algorithm falls back to neutral weight (0.5) for 100% of accidents
- ❌ Predictions are essentially weather-blind

**This explains why the algorithm was designed to gracefully handle "missing" weather data - we never had it properly in the first place!**

---

## Solution: Robust Backfill Script

Created: `/Users/sebastianfrazier/SafeAscent/scripts/backfill_weather_7day_windows.py`

### Features

✅ **Proper 7-day windows**: Days -6 to 0 relative to accident date
✅ **Resume-able**: Can restart if interrupted (saves progress every 50 accidents)
✅ **Incremental saves**: Won't lose work if script crashes
✅ **Smart skip logic**: Only fetches missing dates (avoids duplicate work)
✅ **Robust error handling**: Retries API calls up to 3 times
✅ **Rate limiting**: Respectful 2-second delay between requests
✅ **Progress tracking**: Shows success/partial/failed counts in real-time

### What It Does

1. **Loads existing weather data** to avoid re-fetching
2. **For each accident** (3,955 total):
   - Calculates 7-day window (accident_date - 6 days to accident_date)
   - Checks which dates are already in database
   - Fetches only missing dates from Open-Meteo API
   - Links weather records to accident_id
3. **Saves incrementally** every 50 accidents
4. **Tracks progress** so you can resume if interrupted

### Time Estimate

- **3,955 accidents** to process
- **~2 seconds per API call** (rate limiting)
- **Estimated time**: ~2.2 hours
  - Most accidents need 6 new dates (already have accident day)
  - API can fetch 7 days in one call (efficient!)

---

## How to Run

### Option 1: Full Backfill (Recommended)

```bash
cd /Users/sebastianfrazier/SafeAscent
venv/bin/python scripts/backfill_weather_7day_windows.py
```

This will:
- Show you the estimate
- Ask for confirmation
- Run the full backfill (~2.2 hours)
- Save progress incrementally
- Can be interrupted with Ctrl+C and resumed later

### Option 2: Test Run (First)

```bash
# Test with first 10 accidents to verify it works
cd /Users/sebastianfrazier/SafeAscent
venv/bin/python -c "
import pandas as pd
df = pd.read_csv('data/tables/accidents.csv')
valid = df[df['date'].notna() & df['latitude'].notna() & df['longitude'].notna()]
test_df = valid.head(10)
test_df.to_csv('data/tables/accidents_test.csv', index=False)
print('Created test file with 10 accidents')
"

# Run backfill on test file (modify script to use accidents_test.csv)
# Then inspect results before running full backfill
```

---

## Expected Results

After successful backfill:

**Before:**
- 0% of accidents with 7-day windows (0 / 3,955)
- 98.7% with only 1-2 days (3,903 / 3,955)
- Algorithm effectively weather-blind

**After:**
- **~95%+** of accidents with 7-day windows (target: 3,750+ / 3,955)
- 3-5% with 5-6 days (usable with reduced confidence)
- <2% with <5 days (exclude from predictions)

**Why not 100%?**
- Some accidents may be before 1940 (Open-Meteo historical limit)
- Some locations may have sparse weather station coverage
- API might fail for a small number of requests

---

## Post-Backfill Actions

After running the backfill script:

### 1. Verify Coverage
```bash
cd /Users/sebastianfrazier/SafeAscent/backend
venv/bin/python check_weather_gaps.py
```

Should show:
- "7_days: ~3,750+ accidents (95%+)"
- "5-6_days: ~100-150 accidents (2-4%)"
- "<5_days: <50 accidents (<2%)"

### 2. Update Algorithm Configuration

Modify `backend/app/api/v1/predict.py` line 311:

```python
# BEFORE (too strict):
if len(weather_records) >= 5:
    weather_pattern = build_weather_pattern(weather_records)
    weather_map[accident.accident_id] = weather_pattern
else:
    weather_map[accident.accident_id] = None

# AFTER (flexible, documented):
if len(weather_records) >= 7:
    # Full 7-day window - optimal
    weather_pattern = build_weather_pattern(weather_records)
    weather_map[accident.accident_id] = weather_pattern
elif len(weather_records) >= 3:
    # Partial window (3-6 days) - usable but reduced confidence
    # Note: Will adjust correlation calculation for shorter windows
    weather_pattern = build_weather_pattern(weather_records)
    weather_map[accident.accident_id] = weather_pattern
else:
    # <3 days: Exclude accident from predictions
    # Better to exclude than introduce noise with neutral weights
    weather_map[accident.accident_id] = None
```

### 3. Update Weather Similarity Module

Modify `backend/app/services/weather_similarity.py` to handle variable-length patterns:

```python
def calculate_weather_similarity(
    current_pattern: WeatherPattern,
    accident_pattern: WeatherPattern,
    ...
):
    # Adjust correlation to use min length of both patterns
    min_days = min(current_pattern.num_days, accident_pattern.num_days)

    # Truncate both to same length for fair comparison
    current_temps = current_pattern.temperature[:min_days]
    accident_temps = accident_pattern.temperature[:min_days]

    # Calculate correlation on matched lengths
    temp_corr = weighted_pearson_correlation(current_temps, accident_temps, ...)

    # ... rest of calculation
```

### 4. Re-run Tests

```bash
cd /Users/sebastianfrazier/SafeAscent/backend
venv/bin/pytest tests/ -v
```

All 106 tests should still pass, but now with **real weather data**!

### 5. Update Documentation

- Update `SESSION_SUMMARY_2026-01-29.md` with backfill completion
- Update `IMPLEMENTATION_LOG.md` Phase 6 notes
- Update `PROJECT_PLAN.md` weather coverage metrics

---

## Coordinate Enhancement (Phase 2)

After weather backfill is complete, we can enhance the **363 accidents** with missing coordinates:

**Strategy:**
1. Use location text field (e.g., "Boulder Canyon", "Mount Rainier")
2. Match to existing mountains/routes in database
3. Geocode location names using:
   - OpenStreetMap Nominatim (free, no key)
   - Or Google Geocoding API (paid but very accurate)
4. Manual review for ambiguous cases

**Potential gain:**
- +363 accidents with coordinates → ~92 more accidents with weather
- Total coverage: 91.6% → 94.6%

---

## Files Created

1. **`scripts/backfill_weather_7day_windows.py`** (470 lines)
   - Main backfill script

2. **`backend/check_weather_gaps.py`** (147 lines)
   - Analysis script to verify coverage

3. **`WEATHER_BACKFILL_2026-01-29.md`** (this file)
   - Documentation and instructions

---

## Notes

- **API Source**: Open-Meteo Historical Weather API (same as original collection)
- **No API key required**: Free tier is sufficient
- **Rate limiting**: 2 seconds between requests (respectful)
- **Resume-able**: Progress saved every 50 accidents
- **Safe**: Only adds missing data, never overwrites existing records
- **Estimated runtime**: ~2.2 hours for full backfill

---

## Questions?

If you encounter issues:
1. Check `/Users/sebastianfrazier/SafeAscent/data/tables/.weather_backfill_progress.txt`
   - Contains IDs of processed accidents
2. Script will show which accident failed if error occurs
3. Can always resume by running script again
4. Progress is saved incrementally - won't lose work

---

**Status**: ✅ Script ready to run
**Next Step**: Execute backfill script
**Estimated Impact**: Algorithm goes from weather-blind to weather-aware
