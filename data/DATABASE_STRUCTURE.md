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

**Correction (2026-09-29):** this table has no `elevation_ft` column; an earlier version of this doc listed one. Elevation is not tracked for `mp_locations`.

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

**Phase 2a repair columns (added by `0004`; written by plan 2's repair jobs, empty until then).** NULL means "not yet classified", never a default guess.

| Column | Type | Description |
|--------|------|-------------|
| date_precision | TEXT | `day`, `month`, `year`, `unknown` (check `accidents_date_precision_check`) |
| year_source | TEXT | Where the year came from |
| year_lo, year_hi | SMALLINT | Year bounds when only a range is known (check `accidents_year_bounds_check`: `year_lo <= year_hi`) |
| geocode_precision | TEXT | `exact`, `crag`, `area`, `park_centroid`, `region_fallback`, `unknown` (check) |
| geocode_method | TEXT | How the coordinates were derived |
| country | TEXT | Country of the accident |
| activity_class | TEXT | `climbing`, `climbing_approach`, `non_climbing` (check) |
| activity_rule_version | TEXT | Rule version that set `activity_class` |
| inclusion_flag | TEXT | Whether the row is used for modeling, and why |
| incident_group_id | INTEGER | Groups duplicate reports of one incident (index `idx_accidents_incident_group`) |
| is_canonical | BOOLEAN | NOT NULL, default `true`; false for a non-canonical duplicate |
| severity_scale | TEXT | `full`, `fatal_only`, `unknown` (check) |
| excluded_reason | TEXT | Why a row is excluded, if it is |
| source_url | TEXT | Link to the source report |
| updated_at | TIMESTAMPTZ | Last repair write |
| exp_years_climbing | SMALLINT | Stated years of experience, 0–80 (check `accidents_exp_years_climbing_check`) |
| exp_stated_level | TEXT | NOT NULL, default `unknown`; `novice`, `intermediate`, `experienced`, `expert`, `unknown` (check) |
| exp_first_season | BOOLEAN | First season climbing, if stated |
| guided | TEXT | NOT NULL, default `unknown`; `guided`, `unguided`, `unknown` (check) |
| exp_rule_version | TEXT | Rule version that set the experience columns |

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

### 4. weather
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
| route_id | INTEGER | MP route id (no FK constraint) |
| prediction_date | DATE | Date of prediction |
| risk_score | FLOAT | Calculated risk score (0-100); NULL when there is too little evidence (since `0003`) |
| color_code | VARCHAR | Risk band (green/yellow/orange/red), or gray with a NULL score |
| calculated_at | TIMESTAMP | When score was computed |

**Constraints:**
- UNIQUE on `(route_id, prediction_date)` - one score per route per day
- CHECK (`NOT VALID`, from `0003`): `risk_score IS NULL` exactly when `color_code = 'gray'`
- Auto-purges data older than 1 year

**Use Cases:**
- Route risk trend analysis (improving/worsening over time)
- Seasonal pattern detection
- Algorithm validation via backtesting

---

### 6. mp_ticks
**Purpose:** Mountain Project ascent log ("ticks") per route, read with raw SQL (no SQLAlchemy model) by the ascent-analytics endpoint and by Phase 2a's R8 quarantine job
**Records:** growing (private scrape import)

| Column | Type | Description |
|--------|------|-------------|
| tick_id | INTEGER | Primary key |
| route_id | VARCHAR(20) | Mountain Project route id, text (no FK — orphan rows are possible and expected) |
| route_name | VARCHAR(255) | Route name as logged by the climber |
| climber_name | VARCHAR(255) | NOT NULL. `ingest`'s grant on this table is column-level and excludes this column (nothing in Phase 2a needs it); `analyst` and `app` can still read it |
| tick_date | DATE | Date of the ascent |
| style | VARCHAR(50) | Ascent style as logged (lead, follow, tr, solo, etc.) |
| created_at | TIMESTAMP | Row insert time, `timestamp without time zone` (writer's local clock, not UTC) |
| quarantine_reason | TEXT | Added by `0004`. NULL (clean) or one of `future`, `orphan_route`, `pre_1970` — set by R8 (`app/pipelines/mp_ticks_quarantine.py`), never deletes the row |
| quarantine_rule_version | TEXT | Added by `0004`. The R8 rule version that flagged the row (currently `r8-v1`); set only on flagged rows, NULL on clean rows. The version that judged the clean rows is recorded per run in `source_ingest_log.validation_report.rule_version` |

**Constraints:**
- `mp_ticks_quarantine_reason_check` (`0004`, `NOT VALID` on creation to avoid a full-table scan during the migration; validated separately against prod as a runbook step): `quarantine_reason IS NULL OR quarantine_reason IN ('future', 'orphan_route', 'pre_1970')`

---

### 7. source_ingest_log
**Purpose:** One row per Phase 2 ingest run (R8, the tick-aggregate loader, and future Phase 2 jobs); audit trail for what ran, when, and with what result — never deleted
**Records:** growing (one row per job run)

| Column | Type | Description |
|--------|------|-------------|
| run_id | UUID | Primary key |
| source | TEXT | Job name, e.g. `mp_ticks_quarantine`, `mp_tick_aggregates` |
| window_start | DATE | Nullable; the run's input window start, if the source has one |
| window_end | DATE | Nullable; the run's input window end, if the source has one |
| started_at | TIMESTAMP | Defaults to `now()` |
| finished_at | TIMESTAMP | Nullable until the run completes |
| status | TEXT | `running`, `ok`, `rejected`, or `failed` |
| rows_in | INTEGER | Rows the run considered |
| rows_upserted | INTEGER | Rows actually written |
| rows_quarantined | INTEGER | Rows flagged rather than written |
| content_sha256 | TEXT | Hash of the input content, used to detect a repeat load (`mp_tick_aggregates`'s no-op case) |
| validation_report | JSONB | `ValidationReport.summary()` for the run: counts and short strings only (`source`, `rows_in`, `accepted`, `quarantined`, `problems`, and `rule_version` for rule-driven jobs such as R8), never a raw row |
| cost_units | NUMERIC | Reserved for future API-cost accounting; unused by Phase 2a jobs |

---

## Key Relationships

Full diagram: [`data_model.png`](../data_model.png) (source `docs/diagrams/data_model.mmd`). The schema is owned by Alembic (`backend/alembic/`). Two more live tables have no SQLAlchemy model: `mp_ticks` (read with raw SQL by the ascent-analytics endpoint) and `area_weekly_weather` (not referenced by `app/`).

```
mp_locations (45K)
    │
    └── mp_routes (168K)
            │
            └── historical_predictions (growing daily)

accidents (6.9K)
    │
    └── weather (25K)
```

**Note:** `accidents.mp_route_id` is a nullable FK to `mp_routes`, but the safety algorithm does not rely on it; it finds relevant accidents by spatial proximity (PostGIS). `historical_predictions.route_id` holds an MP route id without an FK constraint. Accidents do carry legacy FKs, `accidents.route_id → routes` and `accidents.mountain_id → mountains`. Phase 2a relinks them to `mp_routes`/`mp_locations` and drops the legacy tables. `ascents` and `climbers` were dropped by migration `0002_drop_ascents_climbers`.

---

## Schema `internal`

Added by `0004_phase2a_foundation`. Owned by `migrator`; `app` has no `USAGE` on the schema at all (`backend/db/roles/verify_roles_phase2.sql`). `analyst` has read-only `SELECT`; `ingest` has exactly the grants Phase 2a jobs need (`backend/db/roles/grants_phase2.sql`), nothing more.

| Table | Purpose |
|-------|---------|
| `accidents_raw` | Frozen snapshot of `public.accidents` taken by `0004` (primary key on `accident_id`, no other index) before Phase 2a's repair columns existed — the pre-2a shape, for comparison/rollback reference only |
| `accident_revisions` | One row per field a future accident-repair job would change on an `accidents` row (old value, new value, method, rule version) — the table and model exist from `0004`, but no Phase 2a job writes to it yet |
| `ingest_quarantine` | One row per input row an ingest job rejected rather than wrote, with a reason and optional JSON detail — never deleted |
| `mp_tick_aggregates` | MP-reported ice/mixed tick counts by route/period/style, loaded from the private scraper's export (`app/pipelines/mp_tick_aggregates.py`); INSERT-only, a changed count is quarantined rather than overwritten |

---

## Coordinate Systems

| Table | Precision | Notes |
|-------|-----------|-------|
| mp_locations | 6 decimals | ~0.1m precision |
| accidents | 4-6 decimals | Varies by source |
| weather | 2 decimals | ~1km grid (intentional) |

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
SELECT * FROM weather
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
