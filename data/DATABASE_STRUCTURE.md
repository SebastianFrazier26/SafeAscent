# SafeAscent Database Structure

**Database:** Neon PostgreSQL 16 with PostGIS
**Last Updated:** February 2026

---

## Overview

SafeAscent uses a Neon-hosted PostgreSQL database with PostGIS for spatial queries. The database links climbing routes with historical accident data, weather patterns, and safety predictions.

---

## Production Tables (Neon)

### 1. mp_routes
**Purpose:** Mountain Project climbing routes
**Records:** ~168,000

| Column | Type | Description |
|--------|------|-------------|
| mp_route_id | INTEGER | Primary key (Mountain Project ID) |
| name | VARCHAR | Route name |
| grade | VARCHAR | Climbing grade (e.g., "5.10a") |
| type | VARCHAR | Route type (trad, sport, alpine, ice, mixed, aid) |
| location_id | INTEGER | FK to mp_locations |
| pitches | INTEGER | Number of pitches |
| length_ft | INTEGER | Route length in feet |

**Indexes:**
- Primary key on `mp_route_id`
- Index on `location_id` for joins
- Index on `type` for season filtering

---

### 2. mp_locations
**Purpose:** Location hierarchy (areas → sub-areas → crags)
**Records:** ~45,000

| Column | Type | Description |
|--------|------|-------------|
| mp_id | INTEGER | Primary key (Mountain Project ID) |
| name | VARCHAR | Location name |
| parent_id | INTEGER | FK to parent location (self-referential) |
| latitude | FLOAT | Geographic latitude |
| longitude | FLOAT | Geographic longitude |
| elevation_ft | INTEGER | Elevation in feet |

**Indexes:**
- Primary key on `mp_id`
- Index on `parent_id` for hierarchy traversal
- Spatial index on `(latitude, longitude)` for proximity queries

**Hierarchy Example:**
```
Colorado (parent_id=NULL)
  └── Rocky Mountain National Park
      └── Lumpy Ridge
          └── The Book (crag)
              └── Routes inherit coordinates from here
```

---

### 3. accidents
**Purpose:** Historical climbing accidents from AAC, Avalanche.org, NPS
**Records:** ~6,900

| Column | Type | Description |
|--------|------|-------------|
| accident_id | SERIAL | Primary key |
| source | VARCHAR | Data source (AAC, CAIC, NPS) |
| date | DATE | Accident date |
| latitude | FLOAT | Geographic latitude |
| longitude | FLOAT | Geographic longitude |
| coordinates | GEOMETRY | PostGIS point (for spatial queries) |
| elevation_meters | INTEGER | Elevation in meters |
| accident_type | VARCHAR | Type (fall, avalanche, rockfall, etc.) |
| activity | VARCHAR | Activity (climbing, mountaineering, etc.) |
| injury_severity | VARCHAR | Severity (fatal, serious, minor) |
| description | TEXT | Full accident narrative |
| state | VARCHAR | US state |
| mountain | VARCHAR | Mountain/location name |
| route | VARCHAR | Route name (if known) |
| tags | VARCHAR | Comma-separated tags |

**Indexes:**
- Primary key on `accident_id`
- Spatial index on `coordinates` for ST_DWithin queries
- Index on `date` for temporal filtering
- Index on `activity` for route type inference

**PostGIS Usage:**
```sql
-- Find accidents within 50km of a route
SELECT * FROM accidents
WHERE ST_DWithin(
  coordinates,
  ST_SetSRID(ST_MakePoint(-105.27, 40.01), 4326),
  50000  -- meters
);
```

---

### 4. weather_patterns
**Purpose:** 7-day weather windows for accident dates
**Records:** ~25,000

| Column | Type | Description |
|--------|------|-------------|
| weather_id | SERIAL | Primary key |
| accident_id | INTEGER | FK to accidents (NULL for baseline) |
| date | DATE | Weather observation date |
| latitude | FLOAT | Rounded to 0.01° (~1km grid) |
| longitude | FLOAT | Rounded to 0.01° (~1km grid) |
| temperature_avg | FLOAT | Average temperature (°C) |
| temperature_min | FLOAT | Minimum temperature (°C) |
| temperature_max | FLOAT | Maximum temperature (°C) |
| wind_speed_avg | FLOAT | Average wind speed (km/h) |
| wind_speed_max | FLOAT | Maximum wind gust (km/h) |
| precipitation_total | FLOAT | Total precipitation (mm) |
| visibility_avg | FLOAT | Average visibility (m) |
| cloud_cover_avg | FLOAT | Average cloud cover (%) |

**Weather Window Structure:**
- For each accident: 7 consecutive days of weather (day -6 to day 0)
- Enables pattern matching between forecast and historical conditions

---

### 5. historical_predictions
**Purpose:** Daily safety score history for trend analysis
**Records:** Growing (~168K per day)

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| route_id | INTEGER | FK to mp_routes |
| prediction_date | DATE | Date of prediction |
| risk_score | FLOAT | Calculated risk score (0-100) |
| color_code | VARCHAR | Risk category (green/yellow/orange/red) |
| calculated_at | TIMESTAMP | When score was computed |

**Constraints:**
- UNIQUE on `(route_id, prediction_date)` - one score per route per day
- Auto-purges data older than 1 year

**Use Cases:**
- Route risk trend analysis (improving/worsening over time)
- Seasonal pattern detection
- Algorithm validation via backtesting

---

## Key Relationships

```
mp_locations (45K)
    │
    └── mp_routes (168K)
            │
            └── historical_predictions (growing daily)

accidents (6.9K)
    │
    └── weather_patterns (25K)
```

**Note:** Routes and accidents are NOT directly linked via foreign keys. The safety algorithm uses spatial proximity (PostGIS) to find relevant accidents for each route dynamically.

---

## Coordinate Systems

| Table | Precision | Notes |
|-------|-----------|-------|
| mp_locations | 6 decimals | ~0.1m precision |
| accidents | 4-6 decimals | Varies by source |
| weather_patterns | 2 decimals | ~1km grid (intentional) |

All coordinates use **WGS84 (SRID 4326)** - standard GPS coordinate system.

---

## Query Patterns

### Safety Score Calculation
```sql
-- Algorithm fetches ALL accidents (no spatial filtering)
-- Gaussian spatial weighting naturally diminishes distant accidents
SELECT accident_id, latitude, longitude, date,
       activity, accident_type, injury_severity
FROM accidents
WHERE latitude IS NOT NULL
  AND longitude IS NOT NULL
  AND date IS NOT NULL;
```

### Route Display (Map)
```sql
-- Bulk fetch for map with coordinates from location
SELECT r.mp_route_id, r.name, r.grade, r.type,
       l.latitude, l.longitude
FROM mp_routes r
JOIN mp_locations l ON r.location_id = l.mp_id
WHERE l.latitude IS NOT NULL
  AND l.longitude IS NOT NULL;
```

### Weather Pattern Matching
```sql
-- Get 7-day weather window for an accident
SELECT * FROM weather_patterns
WHERE accident_id = :id
ORDER BY date ASC;
```

---

## Data Quality

### Accident Coverage
| Source | Records | Geocoded | Date Coverage |
|--------|---------|----------|---------------|
| AAC | 2,770 | 99.9% | 1990-2019 |
| Avalanche.org | 1,372 | 100% | 1997-2026 |
| NPS | 848 | 77% | Various |

### Route Coverage
- **Total routes:** ~168,000 from Mountain Project
- **With coordinates:** 100% (inherited from locations)
- **Route types:** trad, sport, alpine, ice, mixed, aid
- **Boulder routes:** Excluded (different risk profile)

---

*Last Updated: February 2026*
*SafeAscent - Climbing Safety Through Data*
