# Weather Data Collection - SafeAscent

**Started:** 2026-01-25
**Status:** In Progress

---

## Overview

Collecting historical weather data for climbing accident analysis using the Open-Meteo Historical Weather API.

## Data Collection Strategy

### Avoiding Sampling Bias

**Problem:** If we only collect weather data for accident days, we introduce sampling bias - we can't distinguish whether certain weather conditions *cause* accidents or are just normal for that time/location.

**Solution:** For each accident, we collect weather data for the **entire week** when the accident occurred (Monday-Sunday). This gives us:
- Weather conditions during accidents (for analysis)
- Baseline weather data from the same weeks (for comparison)
- Context to identify truly anomalous conditions

### Coverage Statistics

- **Total accidents:** 4,319
- **Accidents with date + coordinates:** 3,955 (91.6%)
- **Unique week+location combinations:** 3,652
- **Expected weather records:** ~25,564 (7 days × 3,652 weeks)
- **Estimated collection time:** ~2 hours

### Geographic & Temporal Range

- **Date range:** 1983-2026 (36 years)
- **Latitude:** 32.75° to 64.75° N
- **Longitude:** -166.53° to -68.27° W
- **Primary region:** North America

---

## Data Source: Open-Meteo Historical Weather API

**Why Open-Meteo:**
- ✅ Free, no API key required
- ✅ Historical data from 1940-present
- ✅ Global coverage
- ✅ High-quality ERA5 reanalysis data
- ✅ Hourly resolution

**API Endpoint:** `https://archive-api.open-meteo.com/v1/archive`

**Documentation:** https://open-meteo.com/en/docs/historical-weather-api

---

## Weather Variables Collected

### Hourly Data → Daily Summaries

For each day, we collect hourly data and calculate daily summaries:

| Variable | Description | Aggregation |
|----------|-------------|-------------|
| **temperature_avg** | Average daily temperature (°C) | Mean of hourly values |
| **temperature_min** | Minimum daily temperature (°C) | Min of hourly values |
| **temperature_max** | Maximum daily temperature (°C) | Max of hourly values |
| **wind_speed_avg** | Average wind speed (km/h) | Mean of hourly values |
| **wind_speed_max** | Maximum wind gust (km/h) | Max of hourly values |
| **precipitation_total** | Total daily precipitation (mm) | Sum of hourly values |
| **cloud_cover_avg** | Average cloud cover (%) | Mean of hourly values |
| **visibility_avg** | Average visibility (meters) | Mean of hourly values |

---

## Database Schema

### Weather Table Structure

```
weather_id (int) - Primary key, sequential ID
accident_id (int, nullable) - Foreign key to accidents table
  - NULL/empty = baseline weather (no accident this day)
  - Non-null = weather during an accident
date (YYYY-MM-DD) - Date of weather observation
latitude (float) - Location latitude (rounded to ~1km precision)
longitude (float) - Location longitude (rounded to ~1km precision)
temperature_avg (float) - Average temperature (°C)
temperature_min (float) - Minimum temperature (°C)
temperature_max (float) - Maximum temperature (°C)
wind_speed_avg (float) - Average wind speed (km/h)
wind_speed_max (float) - Maximum wind gust (km/h)
precipitation_total (float) - Total precipitation (mm)
visibility_avg (float) - Average visibility (meters)
cloud_cover_avg (float) - Average cloud cover (%)
```

### Record Types

1. **Accident Weather Records** (`accident_id` populated)
   - Weather conditions during specific accidents
   - Directly linked to accident in database
   - Used for analyzing accident conditions

2. **Baseline Weather Records** (`accident_id` NULL)
   - Weather from other days in accident weeks
   - Not linked to specific accidents
   - Used for comparison and establishing normal conditions

---

## Collection Process

### Script: `scripts/collect_weather_data.py`

**Steps:**
1. Load accidents with valid dates and coordinates
2. Identify unique week+location combinations
3. For each week:
   - Query Open-Meteo API for 7 days of hourly weather
   - Calculate daily summaries
   - Link records to accidents on matching dates
   - Mark other days as baseline data
4. Save progress every 50 weeks
5. Final save with comprehensive statistics

**Rate Limiting:**
- 1 API request per 2 seconds (respectful to free service)
- Auto-save every 50 weeks (prevents data loss)
- Handles API errors gracefully

**Testing:**
```bash
# Test with first 10 accidents
python scripts/collect_weather_data.py --test

# Full collection
python scripts/collect_weather_data.py
```

### Monitoring Progress

**Script:** `scripts/monitor_weather_collection.py`

```bash
python scripts/monitor_weather_collection.py
```

Shows:
- Total records collected
- Accident vs baseline record counts
- Geographic and temporal coverage
- Data completeness percentages
- Estimated completion time

---

## Data Quality Fixes

### Date Cleanup

Fixed issues in `accidents.csv`:
1. **Malformed years:** 2,021 dates had format "1990.0-04-04" → Fixed to "1990-04-04"
2. **Invalid leap day:** 1993-02-29 (not a leap year) → Fixed to 1993-02-28
3. **Missing year:** "nan-10-22" → Set to NULL

All dates now validated as proper YYYY-MM-DD format.

---

## Expected Outcomes

### Analysis Capabilities

With this data, we can:

1. **Compare weather during accidents vs baseline**
   - Is accident weather significantly different from normal?
   - Which weather variables correlate with accidents?

2. **Identify dangerous conditions**
   - Temperature thresholds
   - Wind speed limits
   - Precipitation impact
   - Cloud cover effects

3. **Build predictive models**
   - Risk scoring based on weather forecasts
   - Warning systems for dangerous conditions
   - Route-specific weather sensitivity

4. **Seasonal patterns**
   - Which months have highest accident rates?
   - How does weather vary by season?
   - Optimal climbing windows

---

## File Outputs

### Primary Data
- `data/tables/weather.csv` - Complete weather dataset (~25,564 records)

### Scripts
- `scripts/collect_weather_data.py` - Main collection script
- `scripts/monitor_weather_collection.py` - Progress monitoring
- `scripts/fix_accident_dates.py` - Date cleanup utility

### Documentation
- `data/WEATHER_COLLECTION.md` (this file)

---

## Next Steps After Collection

1. **Validate data quality**
   - Check for missing values
   - Verify date ranges
   - Confirm accident linkages

2. **Exploratory analysis**
   - Weather distributions
   - Accident vs baseline comparisons
   - Correlation analysis

3. **Feature engineering**
   - Weather severity scores
   - Anomaly detection
   - Composite risk indicators

4. **Database integration**
   - Load into SQL database
   - Create views/indexes for analysis
   - Join with accidents for queries

---

## Technical Notes

### API Response Format

Open-Meteo returns JSON with hourly arrays:
```json
{
  "hourly": {
    "time": ["2023-01-01T00:00", "2023-01-01T01:00", ...],
    "temperature_2m": [5.2, 4.8, ...],
    "wind_speed_10m": [12.5, 11.8, ...],
    ...
  }
}
```

Our script:
- Parses hourly data
- Groups by date
- Calculates daily aggregates
- Links to accidents on matching dates

### Location Precision

Coordinates rounded to 0.01° (~1km precision):
- Reduces duplicate API calls for nearby accidents
- Balances geographic precision with efficiency
- Still sufficient for weather analysis

### Error Handling

- API timeout: 30 seconds per request
- Failed requests: Logged but don't stop collection
- Progress saves: Every 50 weeks
- Resumable: Can restart from last save point

---

*Collection started: 2026-01-25*
*Expected completion: ~2 hours from start*
*SafeAscent Project - Climbing Safety Through Data*
