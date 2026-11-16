# SafeAscent Database Structure

**Database:** Neon PostgreSQL 16 with PostGIS
**Last Updated:** October 2026

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
| tz | TEXT | Added by `0007`. IANA timezone of the accident's crag (check `accidents_tz_check`: not empty); the zone its weather day and its `cell_daily_conditions`/`accident_conditions` join use. NULL until `localday`/`era5_window` registers the point |

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

### 4. weather (legacy)
**Purpose:** 7-day weather windows for accident dates. Superseded by `cell_daily_conditions` (table 10) as of `0007`/plan 3; read-only from here on, kept only as the comparison input to `r6_verify`'s one-time ERA5-vs-legacy agreement check (`app/pipelines/r6_verify.py`). Nothing in `app/` writes it. It stays until the Phase 3 MVP-1 PR, which **must** drop it (hard gate: MVP-1 does not merge while `weather` exists); see `DEPLOYMENT.md`.
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

### 8. gnis_summits
**Purpose:** USGS GNIS summits (public-domain reference data), used by R3a/R3 to check and fix accident locations. Added by `0005`; loaded by `python -m app.pipelines.gnis` as `ingest`.

| Column | Type | Description |
|--------|------|-------------|
| gnis_id | INTEGER | Primary key (GNIS feature id) |
| name | TEXT | GNIS feature name |
| name_key | TEXT | Normalised name for matching (`textsim.place_key`), indexed |
| state_code | CHAR(2) | US state (check: two capital letters) |
| lat, lon | FLOAT | Summit point (check: in range, never NaN) |
| loaded_run_id | UUID | The `source_ingest_log` run that loaded it |

---

### 9. accidents_clean and accidents_clean_daily (views)
**Purpose:** Phase 3's incident definition, added by `0006`. Phase 3 reads these, never `accidents`.

`accidents_clean` keeps an `accidents` row when all hold: `is_canonical`; `activity_class IN ('climbing', 'climbing_approach')`; `country = 'US' OR country IS NULL` (owner decision O3: only a known non-US country drops a row); `excluded_reason IS NULL` (an `unknown` date precision always carries `year_unverified`). `accidents_clean_daily` is the same set with `date_precision = 'day'`, the set the conditions model trains on.

Columns: `accident_id, source, source_id, date, year, date_precision, year_source, year_lo, year_hi, state, country, mountain, route, latitude, longitude, coordinates, elevation_meters, geocode_precision, accident_type, activity, activity_class, inclusion_flag, injury_severity, severity_scale, incident_group_id, mp_route_id, exp_years_climbing, exp_stated_level, exp_first_season, guided, source_url, point_trusted`. No narrative column (`description`, `tags`, `age_range`, `location`).

`point_trusted` is true only for `geocode_precision` `exact`, `crag`, `area` or `park_centroid` (false when unlabelled). `region_fallback` and `unknown` rows keep their coordinates for state or region pooling, but no point-level join (plan 3's `accident_conditions`, Phase 3 spatial features) may use them. A row whose year is a range (`year_source = 'aac_source_id_neighbours'`, `date_precision = 'year'`) must be counted over `[year_lo, year_hi]` in yearly totals (owner rule); the views carry `year_source` for this.

Grants: `app` and `analyst` read both views through `migrator`'s default privileges; `ingest` from plan 3; `trainer` from Phase 3 (D13).

---

### 10. cell_daily_conditions
**Purpose:** Daily weather per 0.1° grid cell, local timezone and local calendar day — the replacement for `weather` (table 4). Added by `0007`; written by `app/pipelines/cell_conditions.py`'s validated upsert. A missing day is an absent row, never a zero.

| Column | Type | Description |
|--------|------|-------------|
| grid_bucket | INTEGER | Part of the PK. The packed 0.1° grid key (`app/pipelines/grid.py`'s `grid_bucket_key()`), shared with SQL |
| tz | TEXT | Part of the PK. IANA timezone the row's `date` is a local calendar day in (check: not empty) |
| date | DATE | Part of the PK. The local day this row covers |
| tmax, tmin | REAL | Daily max/min temperature, °C |
| precip_mm | REAL | Daily precipitation, mm |
| snowfall_cm, snow_depth_cm | REAL | Daily snowfall and standing snow depth, cm |
| wind_max_ms, gust_max_ms | REAL | Daily max sustained wind and gust, m/s (Open-Meteo's native unit; never converted to km/h) |
| freeze_thaw | BOOLEAN | Whether the day crossed freezing |
| swe_delta_mm | REAL | Day-over-day snow-water-equivalent change, mm |
| nws_alert_codes | TEXT[] | NWS alert codes active that day, if any |
| aqi | SMALLINT | Air quality index |
| record_kind | TEXT | `era5` (final, paid archive), `stopgap` (Forecast-API past day, not ERA5) or `forecast` (future day); check `cell_daily_conditions_record_kind_check` |
| is_forecast | BOOLEAN | NOT NULL; exactly `record_kind = 'forecast'` (check) |
| model | TEXT | Open-Meteo model name, if applicable |
| source | TEXT | `open_meteo_archive` or `open_meteo_forecast` (check) |
| fetched_at | TIMESTAMPTZ | When the row was written |
| run_id | UUID | The `source_ingest_log` run that wrote it |

**Precedence (owner rule):** `era5 > stopgap > forecast`, enforced in the upsert's `WHERE` (`_RANK` in `cell_conditions.py`), not by application order. A re-fetch at the same rank that comes back NULL never erases an already-stored value; an `era5` row always replaces a lower-kind row whole. Constraints: PK `(grid_bucket, tz, date)`.

---

### 11. grid_bucket_series
**Purpose:** Registry of every `(grid_bucket, tz)` series that has ever been registered (from an accident, a route point, or a nearby one sharing the bucket), and the ERA5 span bought for it so far. Added by `0007`; written by `localday`/`era5_window`.

| Column | Type | Description |
|--------|------|-------------|
| grid_bucket, tz | INTEGER, TEXT | Primary key |
| first_seen_at | TIMESTAMPTZ | When the series was first registered |
| first_era5_date, last_era5_date | DATE | The bought ERA5 span so far; both NULL or both set (check), `first <= last` (check) |

---

### 12. cell_normals_status
**Purpose:** Per-bucket state machine for the monthly climate-normals computation. Added by `0007`; written by `app/pipelines/normals.py`.

| Column | Type | Description |
|--------|------|-------------|
| grid_bucket | INTEGER | Primary key |
| status | TEXT | `pending`, `complete`, or `insufficient` (check) |
| first_seen_at | TIMESTAMPTZ | When the bucket first became due for normals |
| computed_at | TIMESTAMPTZ | When normals were last computed, if ever |
| n_years | SMALLINT | Years of ERA5 history the computation used |
| run_id | UUID | The `source_ingest_log` run that computed it |

A bucket stays `pending` (never 0 or an interpolated value) until enough ERA5 years are stored; `test_normals_status_matches_the_normals_rows` (`tests/verify/test_phase2a_conditions.py`) checks a `complete` bucket always has exactly 12 `cell_climate_normals` rows with no NULL `tmax_mean`/`precip_mm`.

---

### 13. cell_climate_normals
**Purpose:** Monthly climate normals per grid cell, derived from stored ERA5 history. Added by `0007`; written by `app/pipelines/normals.py`.

| Column | Type | Description |
|--------|------|-------------|
| grid_bucket, month | INTEGER, SMALLINT | Primary key (`month` 1–12, check) |
| tmax_mean, tmin_mean | REAL | Mean daily max/min temperature for the month, °C |
| precip_mm | REAL | Mean monthly precipitation, mm |
| snowfall_cm | REAL | Mean monthly snowfall, cm |
| freeze_thaw_days | REAL | Mean count of freeze-thaw days in the month |
| n_years | SMALLINT | Years of history behind the mean |
| period_start_year, period_end_year | SMALLINT | The ERA5 year range used |
| ref_elevation_m | REAL | Reference elevation for the cell, if known |
| source | TEXT | `open_meteo_era5_seamless` (bought) or `derived_cell_daily_conditions` (computed from rows already stored) (check) |
| normals_version | TEXT | The normals rule version that computed the row |
| run_id | UUID | The `source_ingest_log` run that wrote it |

---

### 14. cell_climatology
**Purpose:** Day-of-year climatology (percentiles, not just means) per grid cell, for comparing a given day's conditions against its historical spread. Added by `0007`; written by `app/pipelines/normals.py`.

| Column | Type | Description |
|--------|------|-------------|
| grid_bucket, doy | INTEGER, SMALLINT | Primary key (`doy` 1–366, check) |
| tmax_mean, tmax_p10, tmax_p90 | REAL | Max temperature mean and 10th/90th percentile |
| tmin_mean, tmin_p10, tmin_p90 | REAL | Min temperature mean and 10th/90th percentile |
| precip_mean, precip_p90 | REAL | Precipitation mean and 90th percentile |
| snowfall_mean | REAL | Mean snowfall |
| gust_p90 | REAL | 90th-percentile gust |
| freeze_thaw_freq | REAL | Fraction of years with a freeze-thaw crossing on this day-of-year |
| lightning_day_freq | REAL | Reserved for plan 7's lightning tables; unused by plan 3 |
| n_years | SMALLINT | Years of history behind the row |
| period_start_year, period_end_year | SMALLINT | The ERA5 year range used |
| climatology_version | TEXT | The climatology rule version that computed the row |
| run_id | UUID | The `source_ingest_log` run that wrote it |

No visibility column exists anywhere in `cell_daily_conditions` or here (`test_no_visibility_in_new_conditions`); Open-Meteo's visibility field was deliberately left out of plan 3.

---

### 15. accident_conditions (view)
**Purpose:** The 7-day (`day_offset` -6..0) weather window per clean-daily incident, joined by cell + crag timezone + local day — never by `accident_id` alone. Added by `0007`.

Columns: `accident_id, day_offset, grid_bucket, tz, date, tmax, tmin, precip_mm, snowfall_cm, wind_max_ms, gust_max_ms, freeze_thaw, record_kind, is_forecast`.

Joins `accidents_clean_daily` to `accidents` (for `tz`) to `cell_daily_conditions` on `grid_bucket = grid_bucket_key(latitude, longitude)`, `tz` and `date = accident date + day_offset`; restricted to `point_trusted` rows with known coordinates and a single-year date (`year_lo IS NOT DISTINCT FROM year_hi`, i.e. not an AAC year-range row). Incident ERA5 history starts 1990-01-01 (owner decision DP1; `localday.INCIDENT_START`); an incident before that date gets no row here at all for any `day_offset` and must read as missing (`conditions_missing`), never a zero — `test_every_clean_day_incident_has_a_seven_day_window` in `tests/verify/test_phase2a_conditions.py` checks every eligible post-1990 incident has all 7 `era5` days.

Grants: `app`, `analyst` and `ingest` read it through `migrator`'s default privileges (`accident_conditions`); `trainer` from Phase 3 (D13).

---

### 16. canonical_areas
**Purpose:** The MP area tree as the catalog uses it, one row per `mp_locations` row. Added by `0008_mp_catalog`; written only by `python -m app.pipelines.mp_catalog refresh` (`app/pipelines/area_tree.py`). Derived columns only: names stay in `mp_locations`, read by the same id. Upserted, never deleted.

| Column | Type | Description |
|--------|------|-------------|
| area_id | BIGINT | Primary key; FK to `mp_locations.mp_id` (`ON DELETE RESTRICT`) |
| parent_id | BIGINT | The MP parent, copied (no FK) |
| path, depth | LTREE, SMALLINT | Area ids root-first along `parent_id` (GiST index); set only when `tree_status` is `us` or `foreign_root` (check) |
| tree_status | TEXT | `us` (root is a US state), `foreign_root` (any other root), `cycle` or `orphan_parent` (no path; quarantined by the refresh) |
| state_code | TEXT | Two-letter state from the root; set exactly when `tree_status = 'us'` (check) |
| lat, lon | FLOAT | The area's point, or both NULL |
| coord_precision | TEXT | `own`: the area's own pair; `inherited`: its pair equals its parent's, so it was copied from the topmost area carrying that pair (`coord_source_id`); `none`: no usable point (missing, invalid, or only the state-level root's pair). `none` exactly when `lat` is NULL (check) |
| coord_source_id | BIGINT | The area whose pair this is; NULL exactly when `coord_precision = 'none'` |
| point_key | TEXT | `grid.point_key(lat, lon)` (5 decimals), indexed; NULL exactly when `lat` is NULL |
| catalog_version, first_seen_at, updated_at, run_id | TEXT, TIMESTAMPTZ, TIMESTAMPTZ, UUID | `cat-mp-v1`; first insert; last change (an unchanged row is not rewritten); the `source_ingest_log` run that last wrote it |

The refresh reads `mp_locations (mp_id, parent_id, latitude, longitude)` for every area and `name` only for the roots, to recognise US states. US areas with out-of-range, non-finite or non-US coordinates are quarantined (`internal.ingest_quarantine`) and stored with `coord_precision = 'none'`.

---

### 17. canonical_routes
**Purpose:** Each MP route's type group and whether it can be scored. Added by `0008_mp_catalog`; written only by `mp_catalog refresh`. Derived columns only: names and grades stay in `mp_routes`. Upserted, never deleted. A route's point is its area's: `mp_routes` coordinates are never read (owner decision 2026-10-12).

| Column | Type | Description |
|--------|------|-------------|
| route_id | BIGINT | Primary key; FK to `mp_routes.mp_route_id` (`ON DELETE RESTRICT`) |
| area_id | BIGINT | FK to `canonical_areas` (the route's `location_id`); NULL when the location is unknown |
| type_group | TEXT | `sport`, `trad`, `alpine`, `ice`, `mixed`, `rock` or `unknown` (`app/pipelines/route_types.py`); NULL only for boulders (check). `rock` is sport or trad, unknown: an MP route typed only "rock", and since `rt-mp-v3` (2026-10-14) a route whose only discipline evidence is toprope or aid |
| is_boulder | BOOLEAN | Boulder-only, or any route with a V grade (incl. `VB`, `V-easy`; since `rt-mp-v3` a V grade means a boulder whatever the type flags, not only on a "rock" route); never scorable (check) |
| disciplines | TEXT[] | The MP discipline flags parsed from `type` |
| type_rule_version | TEXT | `rt-mp-v3` (2026-10-14; earlier rows read `rt-mp-v2` until the next refresh rewrites them) |
| scorable, unscorable_reason | BOOLEAN, TEXT | `scorable` exactly when the reason is NULL (check). Reasons, first match wins: `boulder`; `no_area` (no area, or a `cycle`/`orphan_parent` one); `not_us` (`foreign_root`); `unknown_type`; `no_coordinates` (area `coord_precision = 'none'`) |
| facts_sha256 | TEXT | Hash of the catalog and rule versions plus the route's location, type and grade, so a fact change is visible |
| catalog_version, first_seen_at, updated_at, run_id | | As in `canonical_areas` |

A refresh whose scorable count falls more than 3% (`mp_catalog.MAX_SCORABLE_DROP`) below the stored count is rejected and writes nothing but its `source_ingest_log` row (`source = 'mp_catalog'`, status `rejected`).

---

### 18. scorable_routes (view)
**Purpose:** The routes the model may score: `canonical_routes` joined to `canonical_areas` where `scorable`. Added by `0008`.

Columns: `route_id, area_id, type_group, type_rule_version, area_path, state_code, point_key, coord_precision, updated_at`.

Grants: `app` and `analyst` read the catalog tables and this view through `migrator`'s default privileges; `ingest` reads `mp_locations (parent_id, name)`, upserts both tables and reads this view (`grants_phase2.sql`); `trainer` gets nothing (D13).

---

### 19. feature_points
**Purpose:** One row per distinct point (`canonical_areas.point_key` of an area carrying a scorable route; since 2026-10-15 areas with only boulder or otherwise unscorable routes get no point), so every route sharing a location sees the same sampled terrain. Added by `0009_static_features`; written by `python -m app.pipelines.static_features points|elevation` (`app/pipelines/static_features.py`, `dem.py`).

| Column | Type | Description |
|--------|------|-------------|
| point_key | TEXT | Primary key; `lat:lon` at 5 decimals (also `canonical_areas.point_key`). The `points` run's result (in its `source_ingest_log.problems` JSON) includes `scorable_routes_no_tz`: scorable routes whose point has no `tz`, which daily conditions (keyed by tz) cannot serve |
| lat, lon | FLOAT | Parsed back from `point_key` |
| grid_bucket | INTEGER | Indexed |
| h3_r5, h3_r7 | TEXT | H3 cells at resolution 5 and 7 |
| tz | TEXT | IANA timezone (`localday.tz_for_point`); the `points` upsert never overwrites an already-stored value, only fills a NULL one |
| elevation_m | REAL | 3DEP elevation, metres; NULL until sampled, and NULL forever for a masked cell, a fill value, or a point with no tile at either resolution (never 0) |
| dem_source | TEXT | `3dep_13` (1/3″, 10 m) or `3dep_2` (2″, 60 m, decided per point: where 1/3″ has no tile or has no data at that point); `nodata` or `no_tile`; or NULL — pending, retried on the next `elevation` run, oldest-attempted first (check). `nodata`/`no_tile` are terminal: a point holding one is never resampled again. Re-queue one (e.g. once USGS publishes a missing tile) as `ingest`: `UPDATE feature_points SET dem_source = NULL, elevation_m = NULL, dem_res_m = NULL WHERE dem_source = 'no_tile'` |
| dem_res_m | REAL | 10 or 60, matching `dem_source`; NULL alongside a NULL/`nodata`/`no_tile` `dem_source` |
| slope_deg | REAL | Maximum Horn slope (deg) within 200 m of the point (crag steepness, not the slope at the point); reserved, not yet populated |
| aspect_deg, aspect_confidence | REAL | Reserved, not yet populated |
| terrain_version | TEXT | Reserved, not yet populated |
| lithology, lithology_source | TEXT, TEXT | Reserved (`lithology_source` check allows `macrostrat`/`none`); not yet populated |
| feature_version | TEXT | `f-v1` |
| updated_at, run_id | TIMESTAMPTZ, UUID | Bumped by every upsert, including a transient-failure retry touch; the `source_ingest_log` run that last wrote the row |

A transient DEM read error (a stalled S3 fetch, a mid-read failure — `dem.TransientTileError`) leaves `dem_source` NULL rather than storing a wrong or guessed value; the point is retried by a later `elevation` run. `points`/`elevation` never hold a DB transaction while sampling or calling the timezone lookup: each reads in one short transaction, does its I/O with none open, then writes and logs in a second, so a crash mid-sample writes nothing.

The EPQS gate (`static_features epqs-check`, `app/pipelines/epqs.py`) reads only sampled points (`dem_source` `3dep_13` or `3dep_2`) and judges each against its own source's bounds (2026-10-14): median/p90 |Δ| `flat` 2/5 m, `moderate` 4/10 m, `steep` 8/20 m and gross 100 m for `3dep_13`; `flat` 5/12.5 m, `moderate` 10/25 m, `steep` 20/50 m and gross 200 m for `3dep_2`.

---

### 20. route_static_features
**Purpose:** Per-route static terrain features, source `mp`, Phase 3's static input alongside weather. Added by `0009_static_features`; written only by `python -m app.pipelines.static_features routes`, which refuses to run while any scorable route's area has no `feature_points` row (run `points`, and `elevation`, first). One row per non-boulder `canonical_routes` row; rebuilt (deleted and reinserted) each run for boulders, upserted for the rest.

| Column | Type | Description |
|--------|------|-------------|
| route_id | BIGINT | Primary key; FK to `canonical_routes.route_id` |
| source | TEXT | `mp` (check) |
| area_path | LTREE | Copied from `canonical_areas.path` |
| type_group | TEXT | `sport`, `trad`, `alpine`, `ice`, `mixed`, `rock` or `unknown`, or NULL (check; same set as `canonical_routes.type_group`) |
| point_key | TEXT | FK to `feature_points.point_key`; NULL when the route's area has no point |
| lat | FLOAT | The point's latitude only (not `lon`) — the full location is reached through `point_key` → `feature_points` |
| aspect_deg, slope_deg | REAL, REAL | Copied from `feature_points`; `slope_deg` carries the same "maximum Horn slope within 200 m" comment. Reserved, not yet populated (see table 19) |
| elevation_m | REAL | Copied from `feature_points.elevation_m` |
| lithology | TEXT | Copied from `feature_points`; reserved, not yet populated |
| pitches | SMALLINT | From `mp_routes.pitches`, kept only when it's a whole number in `[1, 1000)`; otherwise NULL |
| length_m | REAL | `mp_routes.length_ft` converted to metres, kept only when positive and at most `static_features.MAX_LENGTH_M` (3000 m); a longer value is a likely typo (feet entered as metres, or extra digits) and is stored NULL, quarantined `implausible_length` rather than kept as a wrong number |
| h3_r5, grid_bucket, dem_res_m | TEXT, INTEGER, REAL | Copied from `feature_points` |
| aspect_confidence | REAL | Copied from `feature_points`; reserved, not yet populated |
| coord_precision | TEXT | `own`/`inherited`, copied from the route's `canonical_areas.coord_precision`; `none` when the route has no point (check: `own`, `inherited` or `none`) |
| lithology_source | TEXT | Copied from `feature_points`; reserved, not yet populated |
| feature_version | TEXT | `f-v1` |
| updated_at | TIMESTAMPTZ | Bumped on every rebuild that changes the row |

Grants (`ingest`, `grants_phase2.sql`): `SELECT, INSERT, UPDATE` on `feature_points`; `SELECT, INSERT, UPDATE, DELETE` on `route_static_features` (`DELETE` only for the boulder rows a `routes` run clears); `SELECT` on `scoring_unit_features`.

---

### 21. scoring_unit_features (view)
**Purpose:** The Phase 3 MVP-1 read contract — scorable routes only, 12 rows each (one per calendar month), joining static terrain to monthly climate normals. Added by `0009_static_features`.

Columns: `unit_kind` (`'route'`), `unit_id` (= `route_static_features.route_id`), `type_group`, `area_path`, `point_key`, `tz`, `lat`, `elevation_m`, `dem_res_m`, `grid_bucket`, `coord_precision`, `month` (1–12), `tmax_mean`, `tmin_mean`, `precip_mm`, `snowfall_cm`, `freeze_thaw_days`, `normals_status`, `feature_version`.

Built from `route_static_features` joined to `canonical_routes` (restricted to `scorable`), `feature_points` (for `tz`), a `generate_series(1, 12)` month spine, and `cell_climate_normals` only where `cell_normals_status.status` is `complete` or `insufficient` — otherwise every normals column reads NULL, never an interpolated or zero value. `normals_status` is `no_location` when the route has no `grid_bucket`, else the bucket's `cell_normals_status.status`, defaulting to `pending` when the bucket isn't registered there yet (a route in a brand-new grid bucket). A bucket stays `pending` until the next Open-Meteo Professional window's normals run fills it (`DEPLOYMENT.md`, Data jobs). This view only reads normals computed by plan 3's `app/pipelines/normals.py`; it does not compute or trigger them. Scoring itself is unchanged by this PR — "zero local accidents never means safe" (characteristically-similar-route borrowing) stays a Phase 3 matter.

Grants: `ingest` reads it (`grants_phase2.sql`); `app`/`analyst`/`trainer` grants are a Phase 3 decision, not made by this PR.

---

### 22. accident_legacy_links
**Purpose:** R10's links for accidents whose `accidents.mp_route_id` is NULL, or set to an MP id the catalog does not hold yet (2026-10-14). Added by `0010_accident_route_links`; written by the `r10` repair step (`app/data/repair/legacy_links.py`) and, for `method = 'llm'`, by `r10_llm apply`; neither writes `accidents`.

Columns: `accident_id` (PK, FK `accidents`), `route_id` (FK `canonical_routes`) or `area_id` (FK `canonical_areas`) — exactly one is set, `method` (`legacy_mp_id`: the legacy `routes.mp_route_id` was all digits and named a catalog route; `owner`: an owner decision from `internal.r10_unresolved`; `llm`: the R10 LLM pass (`app.data.repair.r10_llm apply`) — a candidate picked at confidence ≥ 0.80 under a passing gate, which must carry `llm_call_id`; the owner's `no_link` removes it on the next `r10 --apply`), `r10_version` (`r10-mp-v1`), `run_id`, `updated_at`, `llm_call_id`. Nothing is ever linked by name matching.

Grants (`ingest`): `SELECT, INSERT, UPDATE, DELETE` (`legacy_mp_id` rows are rebuilt each run; an owner `no_link` removes a link). `app`/`analyst` read it.

---

### 23. accident_route_links (view)
**Purpose:** The one place to read an accident's MP link. Added by `0010_accident_route_links`.

Columns: `accident_id`, `route_id`, `area_id`, `method`. Every accident whose `mp_route_id` has a `canonical_routes` row, as `method = 'mp_route_id'` (the identity: R11 rows and later promotions appear with no job run), plus `accident_legacy_links` rows for accidents whose `mp_route_id` is NULL or has no `canonical_routes` row (since 2026-10-14: an accident whose MP id the catalog has not picked up yet shows its legacy or owner link instead of nothing) — an `mp_route_id` that is in the catalog always wins over a legacy, owner or LLM link. Accidents with no row here are still pooled incidents, placed by their own coordinates. An `internal.r10_unresolved` accident usually has no row here, but not always: an accident `r10_llm apply` linked (`method = 'llm'`) keeps its undecided `r10_unresolved` row, so the owner can still review it through `r10-export`, and an owner decision there replaces the LLM link.

Grants: `ingest`, `app` and `analyst` read it.

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

`canonical_areas` and `canonical_routes` (`0008`) hang one-to-one off `mp_locations` and `mp_routes` by the same ids, with `ON DELETE RESTRICT` FKs; `internal.route_tick_scrapes` hangs off `mp_routes` the same way.

**Note:** `accidents.mp_route_id` is a nullable FK to `mp_routes`, but the safety algorithm does not rely on it; it finds relevant accidents by spatial proximity (PostGIS). `historical_predictions.route_id` holds an MP route id without an FK constraint. Accidents do carry legacy FKs, `accidents.route_id → routes` and `accidents.mountain_id → mountains`. Phase 2a relinks them to `mp_routes`/`mp_locations` and drops the legacy tables. `ascents` and `climbers` were dropped by migration `0002_drop_ascents_climbers`.

---

## Schema `internal`

Added by `0004_phase2a_foundation`. Owned by `migrator`; `app` has no `USAGE` on the schema at all (`backend/db/roles/verify_roles_phase2.sql`). `analyst` has read-only `SELECT`; `ingest` has exactly the grants Phase 2a jobs need (`backend/db/roles/grants_phase2.sql`), nothing more.

| Table | Purpose |
|-------|---------|
| `accidents_raw` | Frozen snapshot of `public.accidents` taken by `0004` (primary key on `accident_id`, no other index) before Phase 2a's repair columns existed — the pre-2a shape, for comparison/rollback reference only |
| `accident_revisions` | One row per field an accident-repair step changed on an `accidents` row (old value, new value, method, rule version, run id); keyed on `(accident_id, field, rule_version)` so a re-run records nothing twice |
| `duplicate_decisions` | Added by `0005`. One R5 decision per row pair (`low_id < high_id`): `merge` or `distinct`, `decided_by` `auto` (score band) or `owner` (review CSV), with score, rule version and the R5 run that wrote it. Owner decisions are never re-decided; `ingest` cannot delete one |
| `ingest_quarantine` | One row per input row an ingest job rejected rather than wrote, with a reason and optional JSON detail — never deleted |
| `mp_tick_aggregates` | MP-reported tick counts (ice/mixed and rock) by route/period/style, loaded from the private scraper's aggregate-only export (`app/pipelines/mp_tick_aggregates.py`, `--scope ice_mixed` or `--scope rock`); INSERT-only: a stored `(mp_route_id, period, style)` row is never changed, and a later export's different count is skipped and counted as `already_loaded_changed` in the run's stats, never quarantined (owner decision 2026-10-02) |
| `route_tick_scrapes` | Added by `0008`. The tick-source seam (DP9): one row per MP route whose ticks were scraped (`source = 'mp_scrape'`), with `scraped_at`, `complete`, `complete_through` (the last fully observed month's last day; set exactly when `complete`) and `total_ticks` (only when `complete`; the sum of the stored months over the counted range, not the newest export's own total, so it always equals that route's `route_tick_months` sum even after a rescrape left older months at their first-scrape counts). FK to `mp_routes`; loaders upsert, never delete. `0008` backfilled it from `mp_tick_aggregates` only for routes whose stored months (plus that run's quarantined partial/future months) sum to the stored total. Written by both export scopes; `source_ingest_log.source` is `mp_tick_aggregates` (ice/mixed) or `mp_tick_aggregates_rock` |
| `route_tick_coverage` (view) | Every `canonical_routes` route with its `tick_status`: `complete`, `scraped` (incomplete), or `missing` (never scraped). `tick_total` is set only for `complete` routes, so an unscraped route never reads as zero ticks; it is the sum of the route's stored months (`route_tick_scrapes.total_ticks`) |
| `route_tick_months` (view) | Monthly tick counts (`route_id, period, style, tick_count`) only for completely scraped routes, and only for months up to their `complete_through` |
| `r10_unresolved` | Added by `0010`. Accidents with a legacy `route_id`/`mountain_id` that R10 could not link: `reason` (`legacy_route_no_mp_id`, `legacy_mp_id_not_in_catalog`, `legacy_mountain_only`, or `not_in_catalog`: the accident's own `accidents.mp_route_id` has no `canonical_routes` row yet, so the identity link cannot exist; listed rather than silently dropped until a catalog refresh picks the route up), up to 5 ranked `candidate_route_ids` (non-boulder routes in areas within 2 km, by name similarity then distance) or `candidate_area_ids` (located areas within 3 km), `candidates_searched` (false when no coordinate search ran — no legacy coordinates, or `legacy_mp_id_not_in_catalog` — as opposed to a search that found nothing), `llm_call_id` (the later LLM pass's suggestion, cleared whenever a rerun changes the candidate arrays), and the owner's optional `owner_decision` (`link` with exactly one `decided_route_id`/`decided_area_id`, or `no_link`) from `r10-export`/`r10-import`. Candidates tie-break on equal name similarity by `own` coordinates before `inherited` ones; the export carries each candidate's `coord_precision`, and an `inherited` candidate's distance is approximate (its point is a parent area's). Decided rows are never refreshed or deleted by `r10`, and the trigger `r10_unresolved_keep_decisions` refuses any UPDATE or DELETE of one; undecided rows are refreshed each run and removed once a rule resolves them. Unresolved accidents remain pooled incidents placed by their own coordinates |
| `r10_llm_calls` | Added by `0010`, written by `app.data.repair.r10_llm` (`sample`, `run`): one append-only audit row per call (pinned `model` and `model_reported`, `prompt_version`, `payload_fields` and `request_sha256` — never the payload or narrative text, offered `candidate_ids`, `status` `ok`/`invalid`/`refusal`/`max_tokens`/`error`, `pick_id` or `pick_none`, `confidence`, `reason_code`, tokens, `cost_usd`, `is_sample`). The database refuses a pick that was not offered. `apply` uses only the latest `ok` call whose `candidate_ids` still equal the row's current candidates. Each run's spend, including the in-flight call's worst case recorded before it is sent, is in `source_ingest_log` under source `r10_llm` (`cost_units` in USD, `content_sha256` = sha256 of `model|prompt_version`); the `--max-usd` cap is checked against the sum of every such row for the model and prompt (failed and running rows included), so it is cumulative. At most 2 rows per accident, model and prompt. Known gap: if the process dies after the API answered but before the audit row commits, that call's answer and tokens are lost; its worst-case reservation stays in the ledger (counted against the cap) and a later run calls the case again |
| `r10_llm_gates` | Added by `0010`: one append-only gate decision per (model, prompt version) sample run, from `r10_llm gate` (`n_checked`, `n_agree`, `n_auto_band`, `n_auto_band_agree`, `auto_threshold` 0.80, `passed`: ≥ 100 checked, ≥ 30 in the auto band, ≥ 95% auto-band precision). `apply` links nothing, and `run` refuses to call the API, unless the latest gate row for its model and `PROMPT_VERSION` passed (a later failing gate revokes an earlier pass). `gate` also saves each spot-check answer as an owner decision on the accident's `r10_unresolved` row (`link` with `decided_route_id` or `decided_area_id` per the candidate kind, or `no_link` for `none`), whether or not the gate passes; a row the owner already decided keeps its decision (reported as `owner_decisions_skipped`), and `apply` never overrides an owner decision |
| `exposure_index` | Added by `0011`. Exposure snapshots, written by `app.pipelines.exposure` (`build`/`drop`), never served. PK `(area_id, type_group, month, as_of, data_cutoff)`; `n_routes`, `proxy` (JSONB), `proxy_version` (`x-mp-v1`), `tick_coverage`, `exposure_point`/`exposure_lo`/`exposure_hi`, `run_id`. A production snapshot has `data_cutoff = as_of`; a backtest sets an earlier cutoff. The point is route density x season share (no floor: a missing season gives a NULL point), bounds are point ÷3 and x3; CHECKs enforce order, >= 0, no NaN/inf and all-or-none bounds. `proxy` holds three components, each `{value, missing, reason?, ...detail}`: `route_density` (routes in the cell), `season_share` and `mp_ticks`. Missing reasons: `no_point`, `no_tz`, `no_era5`, `insufficient_history` (season: fewer than 28 known days for the calendar month across the 3-year window), `no_complete_routes` (ticks), `disabled` (`EXPOSURE_ENABLE_MP_TICKS=false`), `table_dropped` (tick views gone). **Ticks:** MP tick periods (believed to be climb dates; unverified) for the cell's calendar month over the 60 complete months ending at the earlier of `complete_through` and the last day of the month before the cutoff, summed over complete routes (never scaled), averaged per year of the 60-month window; `tick_coverage` = complete routes / routes. Rock coverage is biased by scrape order (accident areas first), so a partial sum is not low traffic. Counts are frozen at scrape time: ticks logged after a cutoff on older months are counted. **Season share:** the share of known days meeting the rock/ice/alpine rule over the 3 complete calendar years ending at the last Dec 31 on or before the series' `last_era5_date` (per series, so areas may sit on different windows); it is time-invariant within a window and uses climate (ERA5) dates. `--cutoff` bounds only ticks. Backtest snapshots use the current season window and the current catalog (DP4). A build before the January ERA5 window leaves some seasons `insufficient_history`. Access: `app` none, `analyst` SELECT, `ingest` SELECT/INSERT/DELETE (no UPDATE), `trainer` none until Phase 3 grants it. Downgrade refuses while rows exist |

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
