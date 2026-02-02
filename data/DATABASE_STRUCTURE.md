# SafeAscent Database Structure

**Updated:** 2026-01-25

---

## Overview

The SafeAscent database links climbing accidents with mountains, routes, climbers, ascents, and weather data for safety analysis.

## Table Relationships

```
Mountains (441)
    ↓ mountain_id
Routes (622)
    ↓ mountain_id & route_id
Accidents (4,319)
    ↓ accident_id
Weather (~25,564)
    ↓
Ascents (366) → Climbers (178)
```

---

## Table Schemas

### 1. Mountains
**Primary Key:** `mountain_id` (int)

| Column | Type | Description |
|--------|------|-------------|
| mountain_id | int | Primary key |
| name | string | Mountain/crag name |
| alt_names | string | Alternative names (comma-separated) |
| elevation_ft | float | Elevation in feet |
| prominence_ft | float | Topographic prominence |
| type | string | peak, crag, etc. |
| range | string | Mountain range |
| state | string | State/province |
| **latitude** | float | Geographic latitude |
| **longitude** | float | Geographic longitude |
| location | string | Text description |
| accident_count | int | Number of accidents |

**Count:** 441 mountains

---

### 2. Routes
**Primary Key:** `route_id` (int)
**Foreign Keys:** `mountain_id` → Mountains

| Column | Type | Description |
|--------|------|-------------|
| route_id | int | Primary key |
| name | string | Route name |
| **mountain_id** | int | FK to mountains |
| mountain_name | string | Denormalized name |
| grade | string | Overall grade |
| grade_yds | string | YDS grade |
| length_ft | float | Route length in feet |
| pitches | int | Number of pitches |
| type | string | Route type (trad, sport, etc.) |
| first_ascent_year | int | Year of first ascent |
| **latitude** | float | Geographic latitude |
| **longitude** | float | Geographic longitude |
| accident_count | int | Number of accidents |
| mp_route_id | string | Mountain Project ID |

**Count:** 622 routes
**Mountain linkage:** 100% have mountain_id

---

### 3. Accidents
**Primary Key:** `accident_id` (int)
**Foreign Keys:** `mountain_id` → Mountains, `route_id` → Routes

| Column | Type | Description |
|--------|------|-------------|
| accident_id | int | Primary key |
| source | string | AAC, CAIC, NPS |
| source_id | string | Source's original ID |
| **date** | string | YYYY-MM-DD |
| year | float | Year (for incomplete dates) |
| state | string | State/province |
| location | string | Text location description |
| mountain | string | Mountain name (text) |
| route | string | Route name (text) |
| **latitude** | float | Geographic latitude |
| **longitude** | float | Geographic longitude |
| accident_type | string | fall, avalanche, etc. |
| activity | string | climbing, skiing, etc. |
| injury_severity | string | fatal, serious, minor |
| age_range | string | Age range of victim |
| description | text | Full accident description |
| tags | string | Comma-separated tags |
| **mountain_id** | int | FK to mountains (nullable) |
| **route_id** | int | FK to routes (nullable) |

**Count:** 4,319 accidents
**Linkage stats:**
- mountain_id populated: 1,398 (32.4%)
- route_id populated: 729 (16.9%)
- Both linked: 592 (13.7%)
- Date + coordinates: 3,955 (91.6%)

**Why not 100% linked?**
- Many accidents have text names that don't match mountains/routes tables exactly
- Some locations are backcountry/unnamed areas
- Fuzzy matching used threshold of 80% similarity

---

### 4. Weather
**Primary Key:** `weather_id` (int)
**Foreign Keys:** `accident_id` → Accidents (nullable)

| Column | Type | Description |
|--------|------|-------------|
| weather_id | int | Primary key |
| **accident_id** | int | FK to accidents (NULL = baseline) |
| **date** | string | YYYY-MM-DD |
| **latitude** | float | Rounded to 0.01° (~1km) |
| **longitude** | float | Rounded to 0.01° (~1km) |
| temperature_avg | float | Average daily temp (°C) |
| temperature_min | float | Minimum daily temp (°C) |
| temperature_max | float | Maximum daily temp (°C) |
| wind_speed_avg | float | Average wind (km/h) |
| wind_speed_max | float | Max wind gust (km/h) |
| precipitation_total | float | Total precipitation (mm) |
| visibility_avg | float | Average visibility (m) |
| cloud_cover_avg | float | Average cloud cover (%) |

**Expected Count:** ~25,564 records (collecting...)
**Record Types:**
- Accident weather: `accident_id` populated (links to specific accident)
- Baseline weather: `accident_id` = NULL (other days in accident weeks)

**Why rounded coordinates?**
- Multiple accidents near same location share weather data
- 0.01° precision ≈ 1km grid (sufficient for weather patterns)
- Reduces duplicate API calls (efficiency)

---

### 5. Ascents
**Primary Key:** `ascent_id` (int)
**Foreign Keys:** `route_id` → Routes, `climber_id` → Climbers

| Column | Type | Description |
|--------|------|-------------|
| ascent_id | int | Primary key |
| **route_id** | int | FK to routes |
| **climber_id** | int | FK to climbers |
| date | string | YYYY-MM-DD |
| style | string | Climbing style |
| lead_style | string | Lead style |
| pitches | int | Pitches climbed |
| notes | text | Climber's notes |
| mp_tick_id | string | Mountain Project tick ID |

**Count:** 366 ascents
**Coverage:** 60 routes with MP IDs have tick data

---

### 6. Climbers
**Primary Key:** `climber_id` (int)

| Column | Type | Description |
|--------|------|-------------|
| climber_id | int | Primary key |
| username | string | Mountain Project username |
| mp_user_id | string | Mountain Project user ID |

**Count:** 178 climbers

---

## Geographic Data Structure

### Coordinate Storage Strategy

**Direct Storage (Not Normalized)**

All location tables store coordinates directly:
```
Mountains → latitude, longitude (source of truth)
Routes → latitude, longitude (inherited from mountain)
Accidents → latitude, longitude (from accident report)
Weather → latitude, longitude (rounded from accidents)
```

**Advantages:**
- ✅ Simple queries (no complex joins for location)
- ✅ Accidents can have locations not in mountains table
- ✅ Weather data portable and self-contained
- ✅ Fast coordinate-based searches

**Disadvantages:**
- ❌ Some coordinate redundancy (routes/mountains)
- ❌ No single source of truth for locations

---

## Coordinate-Based Search Strategies

### 1. Find Weather for a Mountain

**Strategy:** Search by coordinate range

```python
# Example: Find all weather near Mt. Rainier
mountain = mountains[mountains['name'] == 'Rainier'].iloc[0]
lat, lon = mountain['latitude'], mountain['longitude']

# Define search radius (0.1° ≈ 10km)
radius = 0.1

weather_near_rainier = weather[
    (weather['latitude'] >= lat - radius) &
    (weather['latitude'] <= lat + radius) &
    (weather['longitude'] >= lon - radius) &
    (weather['longitude'] <= lon + radius)
]
```

**SQL Version:**
```sql
SELECT w.*
FROM weather w
WHERE w.latitude BETWEEN 46.75 AND 46.95
  AND w.longitude BETWEEN -121.86 AND -121.66
```

### 2. Find Accidents Near a Location

```python
def find_accidents_near(lat, lon, radius_deg=0.05):
    """
    Find accidents within radius of coordinates.

    radius_deg=0.05 ≈ 5km radius
    """
    return accidents[
        (accidents['latitude'] >= lat - radius_deg) &
        (accidents['latitude'] <= lat + radius_deg) &
        (accidents['longitude'] >= lon - radius_deg) &
        (accidents['longitude'] <= lon + radius_deg)
    ]
```

### 3. Link Weather to Accidents

**Two methods:**

**Method A: Using accident_id (direct link)**
```python
# Weather during specific accident
accident_weather = weather[weather['accident_id'] == accident_id]
```

**Method B: Using coordinates + date (spatial-temporal join)**
```python
# Find all accidents with weather data
accidents_with_weather = accidents.merge(
    weather[weather['accident_id'].notna()],
    left_on='accident_id',
    right_on='accident_id'
)
```

**Method C: Spatial search for nearby weather**
```python
def get_weather_near_accident(accident, days_before=7, days_after=0):
    """Get weather near accident location and time"""
    from datetime import datetime, timedelta

    acc_date = datetime.strptime(accident['date'], '%Y-%m-%d')
    start_date = acc_date - timedelta(days=days_before)
    end_date = acc_date + timedelta(days=days_after)

    # Round coordinates to match weather precision
    lat = round(accident['latitude'], 2)
    lon = round(accident['longitude'], 2)

    return weather[
        (weather['latitude'] == lat) &
        (weather['longitude'] == lon) &
        (weather['date'] >= start_date.strftime('%Y-%m-%d')) &
        (weather['date'] <= end_date.strftime('%Y-%m-%d'))
    ]
```

### 4. Calculate Distance Between Coordinates

For more precise proximity matching:

```python
import numpy as np

def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate distance between two points in kilometers.
    Uses Haversine formula.
    """
    R = 6371  # Earth radius in km

    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = np.sin(dlat/2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2)**2
    c = 2 * np.arcsin(np.sqrt(a))

    return R * c

# Find accidents within 5km of coordinates
def accidents_within_radius(lat, lon, radius_km=5):
    distances = accidents.apply(
        lambda row: haversine_distance(
            lat, lon,
            row['latitude'], row['longitude']
        ),
        axis=1
    )
    return accidents[distances <= radius_km]
```

---

## Example Queries

### Query 1: Accidents on Mt. Rainier with Weather

```python
# Get Mt. Rainier
rainier = mountains[mountains['name'] == 'Rainier'].iloc[0]

# Get accidents on Rainier (by mountain_id)
rainier_accidents = accidents[accidents['mountain_id'] == rainier['mountain_id']]

# Get weather for those accidents
rainier_accident_weather = weather[
    weather['accident_id'].isin(rainier_accidents['accident_id'])
]

print(f"{len(rainier_accidents)} accidents on Mt. Rainier")
print(f"{len(rainier_accident_weather)} with weather data")
```

### Query 2: Coldest Accident Day

```python
# Find coldest accident
coldest = weather[weather['accident_id'].notna()].nsmallest(1, 'temperature_min')

# Get accident details
accident = accidents[accidents['accident_id'] == coldest['accident_id'].iloc[0]].iloc[0]

print(f"Coldest accident: {accident['mountain']} - {accident['route']}")
print(f"Date: {accident['date']}")
print(f"Temperature: {coldest['temperature_min'].iloc[0]}°C")
```

### Query 3: High-Wind Accidents

```python
# Find accidents with high winds (>50 km/h avg)
high_wind_weather = weather[
    (weather['accident_id'].notna()) &
    (weather['wind_speed_avg'] > 50)
]

high_wind_accidents = accidents[
    accidents['accident_id'].isin(high_wind_weather['accident_id'])
]

print(f"{len(high_wind_accidents)} accidents in high-wind conditions")
```

### Query 4: Weather Comparison (Accident Days vs Baseline)

```python
# Compare accident days vs non-accident days
accident_days = weather[weather['accident_id'].notna()]
baseline_days = weather[weather['accident_id'].isna()]

print("Average Temperature:")
print(f"  Accident days: {accident_days['temperature_avg'].mean():.1f}°C")
print(f"  Baseline days: {baseline_days['temperature_avg'].mean():.1f}°C")

print("\nAverage Wind Speed:")
print(f"  Accident days: {accident_days['wind_speed_avg'].mean():.1f} km/h")
print(f"  Baseline days: {baseline_days['wind_speed_avg'].mean():.1f} km/h")
```

---

## Data Quality Notes

### Coordinate Precision

| Table | Precision | Notes |
|-------|-----------|-------|
| Mountains | 4 decimals | ~11m precision |
| Routes | 4 decimals | ~11m precision |
| Accidents | 4 decimals | ~11m precision |
| **Weather** | **2 decimals** | **~1km precision (rounded)** |

### Foreign Key Coverage

| Relationship | Coverage | Notes |
|--------------|----------|-------|
| Routes → Mountains | 100% | All routes linked |
| Accidents → Mountains | 32.4% | Text name matching, threshold 80% |
| Accidents → Routes | 16.9% | Text name matching, threshold 80% |
| Weather → Accidents | ~3,955 | Based on accidents with date+coords |

### Why Low Foreign Key Coverage for Accidents?

1. **Text Matching Challenges:**
   - "Mt. Rainier" vs "Rainier" vs "Mount Rainier"
   - Misspellings in source data
   - Unnamed/backcountry locations

2. **Missing from Reference Tables:**
   - Mountains table has 441 entries
   - Many accidents at unlisted locations
   - Some crags/areas not cataloged

3. **Partial Success is Expected:**
   - 32.4% mountain linking is reasonable
   - Focus on high-accident areas (well-represented)
   - Coordinates provide fallback for unlinkable accidents

---

## Scripts for Database Operations

### Link Accidents to Mountains/Routes
```bash
python scripts/link_accidents_to_mountains_routes.py
```

### Collect Weather Data
```bash
python scripts/collect_weather_data.py
```

### Monitor Weather Collection
```bash
python scripts/monitor_weather_collection.py
```

### Fix Date Formatting
```bash
python scripts/fix_accident_dates.py
```

---

## Future Enhancements

### Potential Additions

1. **Mountain/Route Aliases Table:**
   - Store common name variations
   - Improve fuzzy matching coverage
   - Track historical name changes

2. **Location Hierarchy:**
   - Regions → Ranges → Mountains → Routes
   - Enable regional queries
   - Better geographic organization

3. **Weather Stations Table:**
   - Track which weather stations contributed data
   - Data provenance
   - Quality indicators

4. **Accident Conditions Table:**
   - Link accidents to specific conditions
   - Many-to-many relationship
   - Better categorical analysis

---

*Last Updated: 2026-01-25*
*SafeAscent Project - Climbing Safety Through Data*
