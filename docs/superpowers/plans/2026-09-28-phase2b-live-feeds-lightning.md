# Phase 2b Live Feeds and Lightning (PRs 2b-3a, 2b-3b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep `cell_daily_conditions` current for every route-bearing series (grid bucket × crag timezone): the nightly Open-Meteo forecast before scoring, whose `past_days` rows are a clearly flagged non-ERA5 stopgap until the January ERA5 window replaces them; SNOTEL snowpack; NWS alerts (live, and archived storm-based polygons, VTEC-coded); AirNow AQI; GOES GLM lightning (live 10-minute banner data and local-day totals from 2018-02-13); the NLDN tile history from 1989 with a monthly append and the NLDN–GLM overlap calibration; lightning-day climatology; and a `/health/data` staleness endpoint that alerts when any feed goes stale.

**Architecture:** Time-critical feeds run as Celery beat tasks routed to a dedicated `ingest` queue, consumed only by a new `ingest-worker` Railway service that alone holds `INGEST_DATABASE_URL` (D14); the general worker never sees the ingest credential. Batch feeds run as GitHub Actions workflows (`data-daily.yml`, `data-weekly.yml`, dispatch-only `data-backfill-lightning.yml`) (P2-6). Every client is a typed, validated, recorded-response-tested module under `app/pipelines/`; weather writes go through plan 3's `cell_conditions.upsert_rows` (precedence era5 > stopgap > forecast); alert/AQI/SNOTEL values go through `feed_values.write_feed_values`, which stages a value whose weather row does not exist yet instead of losing it. Lightning is stored as non-zero series-days only (`lightning_daily`) plus a ledger of which satellite-hours were complete (`glm_hours`) and NLDN coverage periods; the SQL functions `lightning_glm_count` / `lightning_nldn_count` return a count, 0 (covered, no flash) or NULL (not covered or incomplete). Missing is never zero.

**Tech Stack:** Python 3.12, httpx, pydantic 2, h5py (GLM netCDF4/HDF5; main dependency, the ingest worker needs it), stdlib `xml.etree` (S3 listings, not HTML), stdlib `zoneinfo`, Celery 5.4 beat, SQLAlchemy async, PostGIS, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` §Dynamic features, §Lightning (P2-7), §Pipelines (scheduling P2-6, monitoring), milestone 2b-3; P3:151 columns; decisions D8, D14 and the Defaults pending owner confirmation (DP3) in `2026-09-28-phase2a-foundations.md`.

**Prerequisites:** Plans 1–6 merged and applied (Alembic history is linear: `0013`'s `down_revision` is plan 6's `0012_drop_legacy_routes`). Content dependencies: plan 3 (`cell_daily_conditions` with `tz`/`record_kind`, `grid_bucket_series`, `open_meteo`, `cell_conditions`, `localday`, `normals.window`, `accidents.tz`), plan 2 (`accidents_clean`, `accidents_clean_daily`), plan 5 (`feature_points` with `tz`), plan 6 (the chain head `0012_drop_legacy_routes`).

**Two PRs:** 2b-3a = Tasks 1–8 + runbook Task 14 (`feat/p2b-live-feeds`); 2b-3b = Tasks 9–13 + runbook Task 15 (`feat/p2b-lightning`).

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- Beat runs as exactly one replica (DEPLOYMENT.md); new beat entries carry `expires` so a stalled worker never runs a backlog of stale fetches. Every `app.tasks.data_feeds.*` task is routed to the `ingest` queue; only `ingest-worker` consumes it.
- Every feed logs every run (success or failure) to `source_ingest_log`. Beat-driven feeds also ping their healthchecks.io slug (start/success/fail) via `jobping.job_ping`; Actions jobs exit non-zero and open an issue on failure. `/health/data` freshness is always "last `ok` run in `source_ingest_log`", never "last row with data" (a quiet day with no lightning is fresh; a dead feed is stale).
- Weather day = the crag's **local calendar day** in the series timezone (owner decision 3). Open-Meteo requests use `timezone=<tz>`; GLM flash times convert to local dates per series; AirNow hours and alert intervals map to local days per series; NLDN tiles are UTC days (`day_basis='utc'`) and are read for a local day L as UTC days {L, L+1} (`lightning_nldn_count`). "Future" is still "after the current UTC day at run time"; US local days never run ahead of the UTC day.
- Open-Meteo in this plan is the **Forecast API only** (Standard plan, as the spec budgets for the nightly forecast). `past_days` rows are stored `record_kind='stopgap'` (non-ERA5) and are replaced by ERA5 in plan 3's January Professional window (`era5_window`). There is **no weekly ERA5 append**: spec P2-5's "then Standard, weekly append" is wrong because Standard excludes the historical API (Open-Meteo pricing FAQ, read 2026-09-28); the spec is not edited here, plan 3 and the foundations plan record the correction.
- Forecast/stopgap rows never overwrite ERA5 rows (plan 3 precedence). Alert/AQI/SNOTEL writes touch only their own columns and never drop a value: when the day's weather row is missing, the value waits in `cell_feed_pending` and is applied when the row appears.
- Lightning: NULL (missing) outside a satellite's field of view, north of 54°N, before 2018-02-13 for GLM, before 1989 for NLDN `[default pending: DP3]`, in any satellite-hour whose file set was incomplete, and after the last contiguously loaded NLDN day. Absence of a flash reads as 0 only inside recorded coverage.
- User-Agent with the public repository URL on api.weather.gov and WDQS-style services (their policy); never an email address.
- No HTML parsing: S3 listings are XML (`xml.etree`), AirNow/SNOTEL/NLDN are CSV/JSON, GLM is HDF5.
- Route series are the distinct `(grid_bucket, tz)` of `feature_points` (plan 5); incident series follow plan 3's `accident_conditions` filter (`point_trusted`).

### Cadence (this plan's feeds; the full table is D8 in the foundations plan)

| Feed | Runner | Schedule (UTC) | Writes | Stale after (`/health/data`) |
|---|---|---|---|---|
| Open-Meteo forecast + `past_days=3` stopgap | beat → `ingest-worker` | daily 01:00 | `cell_daily_conditions` (`forecast`, `stopgap`) | 26 h |
| NWS active alerts | beat → `ingest-worker` | hourly :05 | `nws_alert_codes`, `nws_alert_coverage` | 2 h |
| AirNow AQI | beat → `ingest-worker` | hourly :35 | `aqi` | 3 h |
| GLM live | beat → `ingest-worker` | every 10 min | `lightning_recent`, `glm_files_seen` | 30 min |
| SNOTEL | Actions `data-daily` | daily 09:10 | `swe_delta_mm`, `snow_depth_cm` | 50 h |
| GLM daily (local day = UTC today − 2) | Actions `data-daily` | daily 09:10 | `lightning_daily`, `glm_hours` | 50 h |
| NLDN monthly append, NLDN–GLM overlap, lightning-day climatology | Actions `data-weekly` (monthly job) | 3rd of month 10:30 | `lightning_daily` (NLDN), coverage end, `cell_climatology.lightning_day_freq` | 40 d (`nldn_monthly`) |
| ERA5 archive (append, stopgap replacement, normals, new series, 3-year prune of non-incident series) | plan 3 `era5_window` | once a year, the January Professional window (on/after Jan 10) | `cell_daily_conditions` (`era5`) | — |
| IEM storm-based warning archive | owner dispatch | once (runbook), resumable | `nws_alert_codes` on incident series | — |

## Decisions this plan makes where the spec is silent (owner may overrule)

1. **Satellite per bucket (live and history):** `satellite_for(lat, lon, day)` picks GOES-West (GOES-18, operational West from `WEST_START = 2023-01-04`) for bucket centres west of −105° inside the West field of view (lon −180…−105), otherwise GOES-East (GOES-16 until 2025-04-06, GOES-19 from 2025-04-07) inside the East field of view (lon −135…−20); nothing north of 54°N, nothing east of 180° (outer Aleutians), nothing before 2018-02-13. Hawaii is therefore NULL before `WEST_START` and West after it. A flash is counted only for a bucket whose chosen satellite reported it, so a flash seen by both is counted once. The same rule exists in SQL (`glm_satellite`) with a parity test. The field-of-view boxes and `WEST_START` are conservative constants the agent checks against live flash extents and the S3 listing (Task 11 Step 4).
2. **GLM completeness:** GLM L2 files are 20 s long (180 per hour). A satellite-hour is complete when its listing has ≥171 files (95%) and every file downloaded; `glm_hours` records every processed hour. A local day is covered only if every UTC hour it spans is complete for the chosen satellite; otherwise the count is NULL, never 0.
3. **GLM daily storage:** only non-zero series-days go to `lightning_daily` (source `glm`, `day_basis='local'`); absence inside complete hours is 0 through `lightning_glm_count`. Targets: every route series and every trusted-point incident bucket + 8 neighbours (in the incident's timezone), from 2018-02-13. Lightning lives only in this plan's tables: plan 3's `cell_daily_conditions` has no lightning columns (a column there would force a row per bucket-day). Phase 3 reads `lightning_glm_count(grid_bucket, tz, date)` and `lightning_nldn_count(grid_bucket, utc_date)`; the P3 spec (lines 151 and M12) was updated to match on 2026-09-28.
4. **NLDN:** non-zero UTC tile-days in `lightning_daily` (source `nldn`, `tz='UTC'`, `day_basis='utc'`); coverage is the CONUS box 24–50°N, −126…−66° (NLDN detection outside CONUS is not network-grade, so Alaska and Hawaii stay NULL) from 1989-01-01 to the last contiguously loaded day `[default pending: DP3]`. For a local day L the count is the sum over UTC days L and L+1 (an upper bound; flagged `utc_pair` in docs).
5. **NWS codes:** live and archive store the same thing, VTEC `phenomena.significance` codes (e.g. `WS.W`, `SV.W`); alerts without a VTEC string (e.g. Special Weather Statements) are counted (`no_vtec`) and not stored. `nws_alert_codes` NULL = not collected, `'{}'` = collected and no alert; `nws_alert_coverage(date, scope)` says what a non-NULL array covers (`live_all`, or `archive_polygon_only` for IEM storm-based polygons, which excludes zone-based warnings such as winter storm or avalanche). The archive starts at `ARCHIVE_START = 2007-10-01` (storm-based warnings became the NWS warning format); earlier days stay NULL.
6. **AQI per series-day:** the maximum over the local day's hours of the maximum AQI among AirNow sites within 50 km of the series' mean route point; no site within 50 km → NULL.
7. **SNOTEL attachment:** nearest active SNOTEL station (triplet `*:*:SNTL`) within 30 km whose elevation is within ±500 m of the series' mean route-point elevation; `swe_delta_mm` = SWE(local yesterday) − SWE(3 days earlier).
8. **Retention:** this plan deletes nothing from `cell_daily_conditions`. The 3-year prune of non-incident series is plan 3's `era5_fill.prune`, run inside `era5_window` (it also keeps `grid_bucket_series` coverage bookkeeping consistent, which a second pruner here would break); stopgap rows are replaced by ERA5 in January, not pruned. Lightning tables are never pruned, so a GLM or NLDN backfill can never be deleted while `find_completed` blocks its refetch.
9. **GLM live banner freshness:** `recent_lightning` returns NULL (banner: "lightning data unavailable") unless the last `ok` `glm_live` run is ≤30 min old and the bucket has satellite coverage; a live run whose 30-minute file window is under 95% complete for any needed satellite is logged `failed`.

## Review Focus

1. **A GLM file whose `flash_lat` is packed int16 with `scale_factor`/`add_offset`** — expect real degrees, not raw integers (Task 9 `test_packed_coordinates_are_unpacked`).
2. **The same flash reported by GOES-East and GOES-West** — expect one count (Task 10 `test_bucket_takes_one_satellite`).
3. **A quiet 30 minutes with a complete file set, then a window with missing files** — expect run `ok` and banner 0, then run `failed` and banner NULL (Task 10 `test_quiet_complete_window_is_ok_and_reads_zero`, `test_incomplete_window_fails_and_reads_null`).
4. **A Hawaii bucket in 2020, an Aleutian bucket at +175°, a missing S3 hour** — expect NULL, never 0 (Task 9 `test_satellite_rules`, Task 11 `test_incomplete_hour_reads_null_not_zero`).
5. **An NLDN file with lon before lat, and a garbage row** — expect correct buckets and the bad row quarantined (Task 12 `test_lon_lat_column_order`, `test_bad_rows_are_quarantined`).
6. **An AQI value for a series whose weather row does not exist yet** — expect it staged and applied when the row appears, never lost (Task 3 `test_value_without_a_row_waits_and_is_applied_later`).
7. **The 01:00 UTC run for a Denver series** — expect the previous local evening's day stored as `forecast`, the three days before it as `stopgap`, and an existing ERA5 row untouched (Task 2 `test_past_days_are_stopgap_future_days_forecast_and_era5_survives`).
8. **`/health/data` when a feed has never run** — expect `never_run`, not 503; 503 once it has run and gone stale (Task 7 `test_never_run_is_not_stale`).
9. **An AirNow hour file missing** — expect a logged `failed` run and AQI left NULL (Task 6 `test_missing_file_is_logged_failed_and_leaves_aqi_null`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/alembic/versions/0013_live_feeds.py` | Create | Live-feed and lightning tables, lightning read functions, `scope_lightning_coverage` view, drops `area_weekly_weather`. |
| `backend/app/models/feeds.py` | Create | Models. |
| `backend/app/pipelines/open_meteo.py` | Modify | `ForecastClient` (Forecast API, `past_days`, per-timezone). |
| `backend/app/pipelines/forecast.py` | Create | Nightly forecast + stopgap job. |
| `backend/app/pipelines/feed_values.py` | Create | Column-specific writes for NWS/AQI/SNOTEL with a pending stage. |
| `backend/app/pipelines/snotel.py` | Create | Stations, attachment, daily SWE/depth. |
| `backend/app/pipelines/nws.py` | Create | Active alerts, zone geometries, IEM archive, VTEC codes. |
| `backend/app/pipelines/airnow.py` | Create | Hourly AQI files → local-day max per series. |
| `backend/app/pipelines/glm.py` | Create | S3 listing, HDF5 parsing, satellite rules, live window, daily totals, CLI. |
| `backend/app/pipelines/nldn.py` | Create | Yearly tile files, backfill, monthly append, coverage end, overlap. |
| `backend/app/pipelines/lightning_climatology.py` | Create | Lightning-day frequency into `cell_climatology`. |
| `backend/app/tasks/data_feeds.py` | Create | Celery tasks for forecast, alerts, AQI, GLM live. |
| `backend/app/celery_app.py` | Modify | `include`, `task_routes` (queue `ingest`), beat entries. |
| `backend/app/main.py`, `backend/app/data_health.py` | Modify/Create | `GET /health/data`, `recent_lightning`. |
| `backend/railway-ingest.toml`, `docker-compose.yml`, `scripts/check_compose_matches_railway.py` | Create/Modify | `ingest-worker` service (D14). |
| `.github/workflows/data-daily.yml`, `data-weekly.yml`, `data-backfill-lightning.yml` | Create | Batch schedules. |
| tests: `test_migration_0013.py`, `test_forecast.py`, `test_feed_values.py`, `test_snotel.py`, `test_nws.py`, `test_airnow.py`, `test_data_health.py`, `test_glm.py`, `test_nldn.py`, `test_lightning_climatology.py`, `test_data_feeds_tasks.py`; fixtures under `backend/tests/fixtures/` | Create | Tests (recorded/synthetic responses, no network). |
| `backend/pyproject.toml`, `backend/uv.lock`, grants/verify SQL, docs | Modify | `h5py`, mypy, grants, DEPLOYMENT/CLAUDE/CHANGELOG. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 2–13 | tables and functions of `0013` | Task 1 first. |
| plan 3 | 2, 3 | `open_meteo.Location(grid_bucket, lat, lon, tz)`, `LocationResponse` (with `timezone`), `ArchiveClient`, `cell_conditions.DayRow`, `day_rows(grid_bucket, tz, response, *, today, origin, model, source, report)` (`origin="forecast_api"`: days before `today` → `stopgap`, `today` on → `forecast`), `upsert_rows` precedence era5 > stopgap > forecast, `localday.local_date`/`tz_for_point`, `grid_bucket_series`, `normals.window` | Consumed exactly as plan 3 (revised 2026-09-28) defines them. Plan 3 has no shared `_get`; Task 2 splits the retry loop out of `ArchiveClient.fetch` without changing `fetch`'s behaviour. |
| plans 4, 6 | 1 | `canonical_areas(lat, lon)`, `objectives(lat, lon)` | `scope_lightning_coverage` view reads them; plan 6 stores no lightning flag of its own. |
| plan 3 (`era5_fill.prune`) | all | ERA5 retention | Plan 3 owns it; this plan adds no pruning of `cell_daily_conditions` and grants no `DELETE` on it. |
| 2 | 3 | `forecast.run_forecast` calls `feed_values.drain_pending` | Task 3 adds the call. |
| 3 | 4, 5, 6 | `feed_values.write_feed_values`, `FeedValue` | Frozen in Task 3. |
| 2, 5, 6, 10 | 8 | `app/tasks/data_feeds.py`, `app/celery_app.py` | Task 8 wires tasks 2/5/6; Task 10 adds GLM live. |
| 7 | 10 | `app/data_health.py` | Task 10 appends `recent_lightning`. |
| 11 | 12 | `glm.neighbours`, `glm.target_series` | Task 12 imports them. |
| 9 | 1 | `glm.satellite_for` ↔ SQL `glm_satellite` | Parity test in Task 9. |
| 11, 12 | 13 | `lightning_daily`, `glm_hours` | Task 13 only reads them. |
| 13 | plan 3 | `cell_climatology.lightning_day_freq` | Plan 3's normals/climatology upsert leaves this column out; only Task 13 writes it. |
| all | each other | grants/verify SQL, `pyproject.toml`, workflows | Serial. |

---

# PR 2b-3a — `feat/p2b-live-feeds`

### Task 1: Migration `0013` and models

**Files:**
- Create: `backend/alembic/versions/0013_live_feeds.py`, `backend/app/models/feeds.py`, `backend/tests/test_migration_0013.py`
- Modify: `backend/app/models/__init__.py`, `backend/pyproject.toml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`, `backend/tests/test_area_weekly.py`

**Interfaces:**
- Produces (DB):
  - `lightning_recent(grid_bucket integer, window_end timestamptz, flashes integer NOT NULL CHECK >= 0, satellite text NOT NULL CHECK IN ('east','west'), PK (grid_bucket, window_end))` — 10-minute slots, 24 h retention.
  - `glm_files_seen(s3_key text PK, satellite text NOT NULL, file_start timestamptz NOT NULL, processed_at timestamptz NOT NULL DEFAULT now())`, index `(satellite, file_start)`.
  - `glm_hours(satellite text CHECK IN ('east','west'), hour_start timestamptz, files integer NOT NULL CHECK >= 0, complete boolean NOT NULL, run_id uuid, PK (satellite, hour_start))`.
  - `lightning_daily(grid_bucket integer, tz text, date date, source text CHECK IN ('glm','nldn'), flashes integer NOT NULL CHECK > 0, day_basis text NOT NULL CHECK IN ('local','utc'), satellite text, run_id uuid, PK (grid_bucket, tz, date, source))`, CHECK `(source = 'nldn') = (day_basis = 'utc' AND tz = 'UTC')`.
  - `lightning_coverage_periods(source text, satellite text NOT NULL DEFAULT '', start_date date, end_date date NULL, min_lat real, max_lat real, min_lon real, max_lon real NOT NULL, day_basis text NOT NULL, notes text NOT NULL, PK (source, satellite, start_date))`, seeded with `nldn` (1989-01-01, end NULL until loaded, 24–50°N, −126…−66, `utc`), `glm`/`east` (2018-02-13, 0–54°N, −135…−20, `local`), `glm`/`west` (2023-01-04, 0–54°N, −180…−105, `local`).
  - `snotel_stations(triplet text PK, name text, lat double precision NOT NULL, lon double precision NOT NULL, elevation_m real, active boolean NOT NULL, updated_at timestamptz DEFAULT now())`.
  - `nws_zone_geoms(zone_id text PK, geom geography NOT NULL, fetched_at timestamptz DEFAULT now())`.
  - `nws_alert_coverage(date date PK, scope text NOT NULL CHECK IN ('live_all','archive_polygon_only'), runs_ok smallint NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT now())`.
  - `cell_feed_pending(feed text CHECK IN ('nws','aqi','snotel'), grid_bucket integer, tz text, date date, payload jsonb NOT NULL, first_seen_at timestamptz NOT NULL DEFAULT now(), PK (feed, grid_bucket, tz, date))`.
  - Functions `glm_satellite(lat float8, lon float8, d date) RETURNS text` (IMMUTABLE), `lightning_glm_count(b integer, zone text, d date) RETURNS integer` (STABLE), `lightning_nldn_count(b integer, d date) RETURNS integer` (STABLE).
  - `area_weekly_weather` dropped (refuses if any longitude > 0 remains, proving R7 ran);
  - View `scope_lightning_coverage(scope_kind, scope_id, satellite, lightning_coverage)` over `canonical_areas` and `objectives`: `satellite` = `glm_satellite(lat, lon, current_date)`, `lightning_coverage` = `'satellite'` or `'none'` (NULL coordinates → `'none'`). This is the badge/UI lightning flag for areas and objectives (plan 6 leaves it to this plan); it is always current, so no refresh job exists.
- Produces (Python): models `LightningRecent`, `GlmFileSeen`, `GlmHour`, `LightningDaily`, `LightningCoveragePeriod`, `SnotelStation`, `NwsZoneGeom`, `NwsAlertCoverage`, `CellFeedPending`.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0013.py`:

```python
import asyncio

import pytest
from alembic import command

from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migration_0007 import _fetch
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg

NY = "America/New_York"


def _one(name: str, sql: str) -> object:
    return asyncio.run(_fetch(pg_url(name), sql))[0][0]


def test_0013_seeds_coverage_periods_and_drops_retired_objects():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))
        rows = asyncio.run(_fetch(pg_url(name),
            "SELECT source, satellite, start_date::text, end_date, day_basis FROM lightning_coverage_periods ORDER BY 1, 2"))
        assert [tuple(r) for r in rows] == [
            ("glm", "east", "2018-02-13", None, "local"),
            ("glm", "west", "2023-01-04", None, "local"),
            ("nldn", "", "1989-01-01", None, "utc"),
        ]
        assert _one(name, "SELECT to_regclass('public.area_weekly_weather')") is None

AREA = ("INSERT INTO canonical_areas (area_id, name, path, lat, lon, coord_precision, source, redistributable) VALUES "
        "('00000000-0000-0000-0000-00000000000{i}', 'Fixture {i}', 'a{i}', {lat}, {lon}, 'crag', 'openbeta', true);")


def test_scope_lightning_coverage_uses_the_satellite_rule():
    seed = AREA.format(i=1, lat=21.3, lon=-157.8) + AREA.format(i=2, lat=60.0, lon=-150.0) + AREA.format(i=3, lat=40.0, lon=-80.0)
    with migrated_db(seed_sql=seed) as name:
        rows = asyncio.run(_fetch(pg_url(name),
            "SELECT scope_id::text, satellite, lightning_coverage FROM scope_lightning_coverage WHERE scope_kind = 'area' ORDER BY 1"))
    assert [tuple(r)[1:] for r in rows] == [("west", "satellite"), (None, "none"), ("east", "satellite")]


def test_0013_refuses_while_r7_is_unapplied():
    with migrated_db("0012_drop_legacy_routes") as name:
        run_sql(name, "INSERT INTO area_weekly_weather (latitude, longitude, week_start, week_end) "
                      "VALUES (40.1, 113.2, '2020-01-06', '2020-01-12')")
        with pytest.raises(RuntimeError, match="R7"):
            command.upgrade(_alembic_cfg(name), "head")


def test_glm_count_is_null_without_complete_hours_zero_when_complete_and_counts_rows():
    b = 400 * 10000 - 800 + 5000
    hours = ("INSERT INTO glm_hours (satellite, hour_start, files, complete) SELECT 'east', h, 180, true FROM "
             "generate_series(TIMESTAMPTZ '2024-07-01 04:00+00', TIMESTAMPTZ '2024-07-02 03:00+00', interval '1 hour') h;")
    with migrated_db() as name:
        assert _one(name, f"SELECT lightning_glm_count({b}, '{NY}', DATE '2024-07-01')") is None
        run_sql(name, hours)
        assert _one(name, f"SELECT lightning_glm_count({b}, '{NY}', DATE '2024-07-01')") == 0
        run_sql(name, f"INSERT INTO lightning_daily (grid_bucket, tz, date, source, flashes, day_basis, satellite) "
                      f"VALUES ({b}, '{NY}', '2024-07-01', 'glm', 7, 'local', 'east');")
        assert _one(name, f"SELECT lightning_glm_count({b}, '{NY}', DATE '2024-07-01')") == 7
        run_sql(name, "UPDATE glm_hours SET complete = false WHERE hour_start = TIMESTAMPTZ '2024-07-01 12:00+00';")
        assert _one(name, f"SELECT lightning_glm_count({b}, '{NY}', DATE '2024-07-01')") is None
        assert _one(name, "SELECT lightning_glm_count(600 * 10000 - 1500 + 5000, 'America/Anchorage', DATE '2024-07-01')") is None


def test_nldn_count_needs_loaded_coverage_and_sums_the_utc_pair():
    b = 400 * 10000 - 1053 + 5000
    with migrated_db() as name:
        run_sql(name, f"INSERT INTO lightning_daily (grid_bucket, tz, date, source, flashes, day_basis) VALUES "
                      f"({b}, 'UTC', '2017-07-03', 'nldn', 2, 'utc'), ({b}, 'UTC', '2017-07-04', 'nldn', 5, 'utc');")
        assert _one(name, f"SELECT lightning_nldn_count({b}, DATE '2017-07-03')") is None
        run_sql(name, "UPDATE lightning_coverage_periods SET end_date = '2017-12-31' WHERE source = 'nldn';")
        assert _one(name, f"SELECT lightning_nldn_count({b}, DATE '2017-07-03')") == 7
        assert _one(name, f"SELECT lightning_nldn_count({b}, DATE '2017-08-01')") == 0
        assert _one(name, f"SELECT lightning_nldn_count({b}, DATE '1988-07-03')") is None
        assert _one(name, "SELECT lightning_nldn_count(213 * 10000 - 1578 + 5000, DATE '2017-07-03')") is None
```

(Bucket literals follow the D4 formula `lat_i * 10000 + lon_i + 5000`: 40.0/−80.0, 60.0/−150.0, 40.0/−105.3, Honolulu 21.3/−157.8.)

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_migration_0013.py -q` → FAIL (no revision `0013_live_feeds`).

- [ ] **Step 3: Implement** `backend/alembic/versions/0013_live_feeds.py`:

```python
"""Live feeds (P2-6/P2-7): recent lightning, GLM file and hour ledgers, non-zero daily
lightning with coverage periods and read functions, SNOTEL stations, NWS zones and alert
coverage, pending feed values, the area/objective lightning-coverage view; area_weekly_weather
retired (spec 2b-3). Lightning is stored only here, never on cell_daily_conditions."""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision = "0013_live_feeds"
down_revision = "0012_drop_legacy_routes"
branch_labels = None
depends_on = None

# Mirrors app.pipelines.glm.satellite_for; tests/test_glm.py checks parity.
GLM_SATELLITE_SQL = """
CREATE FUNCTION glm_satellite(lat double precision, lon double precision, d date) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN lat < 0 OR lat > 54 OR d < DATE '2018-02-13' THEN NULL
    WHEN lon >= -180 AND lon < -105 AND d >= DATE '2023-01-04' THEN 'west'
    WHEN lon >= -135 AND lon <= -20 THEN 'east'
    ELSE NULL
  END
$$
"""

GLM_COUNT_SQL = """
CREATE FUNCTION lightning_glm_count(b integer, zone text, d date) RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  WITH c AS (
    SELECT glm_satellite(floor(b / 10000.0)::float8 / 10,
                         (b - floor(b / 10000.0) * 10000 - 5000)::float8 / 10, d) AS sat,
           (d::timestamp AT TIME ZONE zone) AS lo,
           ((d + 1)::timestamp AT TIME ZONE zone) AS hi
  ), need AS (
    SELECT c.sat, h FROM c, generate_series(date_trunc('hour', c.lo), c.hi - interval '1 second', interval '1 hour') AS h
  )
  SELECT CASE
    WHEN (SELECT sat FROM c) IS NULL THEN NULL
    WHEN EXISTS (SELECT 1 FROM need n LEFT JOIN glm_hours g ON g.satellite = n.sat AND g.hour_start = n.h
                 WHERE g.complete IS NOT TRUE) THEN NULL
    ELSE coalesce((SELECT l.flashes FROM lightning_daily l WHERE l.grid_bucket = b AND l.tz = zone
                   AND l.date = d AND l.source = 'glm'), 0)
  END
$$
"""

# NLDN tiles are UTC days; a local day L (every US zone is west of UTC) lies inside UTC days L and L+1.
NLDN_COUNT_SQL = """
CREATE FUNCTION lightning_nldn_count(b integer, d date) RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  WITH c AS (SELECT floor(b / 10000.0)::float8 / 10 AS lat, (b - floor(b / 10000.0) * 10000 - 5000)::float8 / 10 AS lon)
  SELECT CASE
    WHEN NOT EXISTS (SELECT 1 FROM lightning_coverage_periods p, c WHERE p.source = 'nldn' AND p.end_date IS NOT NULL
                     AND d >= p.start_date AND d + 1 <= p.end_date
                     AND c.lat BETWEEN p.min_lat AND p.max_lat AND c.lon BETWEEN p.min_lon AND p.max_lon) THEN NULL
    ELSE coalesce((SELECT sum(l.flashes) FROM lightning_daily l WHERE l.grid_bucket = b AND l.tz = 'UTC'
                   AND l.source = 'nldn' AND l.date IN (d, d + 1)), 0)::integer
  END
$$
"""

# current_date, not a stored flag: coverage changes with the calendar (West from 2023-01-04), so a view never goes stale.
SCOPE_COVERAGE_SQL = """
CREATE VIEW scope_lightning_coverage AS
SELECT 'area'::text AS scope_kind, area_id AS scope_id, glm_satellite(lat, lon, current_date) AS satellite,
       CASE WHEN glm_satellite(lat, lon, current_date) IS NULL THEN 'none' ELSE 'satellite' END AS lightning_coverage
FROM canonical_areas
UNION ALL
SELECT 'objective'::text, objective_id, glm_satellite(lat, lon, current_date),
       CASE WHEN glm_satellite(lat, lon, current_date) IS NULL THEN 'none' ELSE 'satellite' END
FROM objectives
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT to_regclass('public.area_weekly_weather')").scalar() is not None:
        bad = bind.exec_driver_sql("SELECT count(*) FROM area_weekly_weather WHERE longitude > 0").scalar_one()
        if bad:
            raise RuntimeError(f"refusing 0013: {bad} area_weekly_weather rows with longitude > 0; run repair step R7 first")
        op.drop_table("area_weekly_weather")
    op.create_table(
        "lightning_recent",
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("flashes", sa.Integer(), nullable=False),
        sa.Column("satellite", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("grid_bucket", "window_end"),
        sa.CheckConstraint("flashes >= 0", name="lightning_recent_flashes_check"),
        sa.CheckConstraint("satellite IN ('east', 'west')", name="lightning_recent_satellite_check"),
    )
    op.create_table(
        "glm_files_seen",
        sa.Column("s3_key", sa.Text(), primary_key=True),
        sa.Column("satellite", sa.Text(), nullable=False),
        sa.Column("file_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_glm_files_seen_sat_start", "glm_files_seen", ["satellite", "file_start"])
    op.create_table(
        "glm_hours",
        sa.Column("satellite", sa.Text(), nullable=False),
        sa.Column("hour_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("files", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("satellite", "hour_start"),
        sa.CheckConstraint("files >= 0", name="glm_hours_files_check"),
        sa.CheckConstraint("satellite IN ('east', 'west')", name="glm_hours_satellite_check"),
    )
    op.create_table(
        "lightning_daily",
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("flashes", sa.Integer(), nullable=False),
        sa.Column("day_basis", sa.Text(), nullable=False),
        sa.Column("satellite", sa.Text(), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("grid_bucket", "tz", "date", "source"),
        sa.CheckConstraint("source IN ('glm', 'nldn')", name="lightning_daily_source_check"),
        sa.CheckConstraint("flashes > 0", name="lightning_daily_flashes_check"),
        sa.CheckConstraint("day_basis IN ('local', 'utc')", name="lightning_daily_basis_check"),
        sa.CheckConstraint("(source = 'nldn') = (day_basis = 'utc' AND tz = 'UTC')", name="lightning_daily_nldn_utc_check"),
    )
    op.create_table(
        "lightning_coverage_periods",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("satellite", sa.Text(), server_default="", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("min_lat", sa.REAL(), nullable=False),
        sa.Column("max_lat", sa.REAL(), nullable=False),
        sa.Column("min_lon", sa.REAL(), nullable=False),
        sa.Column("max_lon", sa.REAL(), nullable=False),
        sa.Column("day_basis", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("source", "satellite", "start_date"),
    )
    op.execute(
        "INSERT INTO lightning_coverage_periods (source, satellite, start_date, end_date, min_lat, max_lat, min_lon, max_lon, "
        "day_basis, notes) VALUES "
        "('nldn', '', '1989-01-01', NULL, 24, 50, -126, -66, 'utc', 'SWDI NLDN 0.1 deg UTC-day tiles, CONUS only; "
        "pre-1989 western coverage sparse (spec); end_date = last contiguously loaded day'), "
        "('glm', 'east', '2018-02-13', NULL, 0, 54, -135, -20, 'local', 'GOES-16 to 2025-04-06, GOES-19 after; "
        "a local day counts only if every spanned hour is complete in glm_hours'), "
        "('glm', 'west', '2023-01-04', NULL, 0, 54, -180, -105, 'local', 'GOES-18 as GOES-West; used for bucket "
        "centres west of -105; Hawaii NULL before this date')"
    )
    op.create_table(
        "snotel_stations",
        sa.Column("triplet", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("elevation_m", sa.REAL(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_table(
        "nws_zone_geoms",
        sa.Column("zone_id", sa.Text(), primary_key=True),
        sa.Column("geom", Geography(spatial_index=False), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_nws_zone_geoms_geom", "nws_zone_geoms", ["geom"], postgresql_using="gist")
    op.create_table(
        "nws_alert_coverage",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("runs_ok", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("scope IN ('live_all', 'archive_polygon_only')", name="nws_alert_coverage_scope_check"),
    )
    op.create_table(
        "cell_feed_pending",
        sa.Column("feed", sa.Text(), nullable=False),
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("feed", "grid_bucket", "tz", "date"),
        sa.CheckConstraint("feed IN ('nws', 'aqi', 'snotel')", name="cell_feed_pending_feed_check"),
    )
    op.execute(GLM_SATELLITE_SQL)
    op.execute(GLM_COUNT_SQL)
    op.execute(NLDN_COUNT_SQL)
    op.execute(SCOPE_COVERAGE_SQL)


def downgrade() -> None:
    raise NotImplementedError("0013 is one-way: area_weekly_weather was dropped (its pre-2a dump is the backup)")
```

`backend/app/models/feeds.py`:

```python
"""Live-feed and lightning tables (0013). Read lightning through the SQL functions
lightning_glm_count / lightning_nldn_count: a missing lightning_daily row means 0 only
inside recorded coverage."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import REAL, Boolean, Date, DateTime, Float, Index, Integer, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LightningRecent(Base):
    __tablename__ = "lightning_recent"
    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    flashes: Mapped[int] = mapped_column(Integer)
    satellite: Mapped[str] = mapped_column(Text)


class GlmFileSeen(Base):
    __tablename__ = "glm_files_seen"
    __table_args__ = (Index("ix_glm_files_seen_sat_start", "satellite", "file_start"),)
    s3_key: Mapped[str] = mapped_column(Text, primary_key=True)
    satellite: Mapped[str] = mapped_column(Text)
    file_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GlmHour(Base):
    __tablename__ = "glm_hours"
    satellite: Mapped[str] = mapped_column(Text, primary_key=True)
    hour_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    files: Mapped[int] = mapped_column(Integer)
    complete: Mapped[bool] = mapped_column(Boolean)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class LightningDaily(Base):
    __tablename__ = "lightning_daily"
    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True)
    tz: Mapped[str] = mapped_column(Text, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    flashes: Mapped[int] = mapped_column(Integer)
    day_basis: Mapped[str] = mapped_column(Text)
    satellite: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class LightningCoveragePeriod(Base):
    __tablename__ = "lightning_coverage_periods"
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    satellite: Mapped[str] = mapped_column(Text, primary_key=True, server_default="")
    start_date: Mapped[date] = mapped_column(Date, primary_key=True)
    end_date: Mapped[date | None] = mapped_column(Date)
    min_lat: Mapped[float] = mapped_column(REAL)
    max_lat: Mapped[float] = mapped_column(REAL)
    min_lon: Mapped[float] = mapped_column(REAL)
    max_lon: Mapped[float] = mapped_column(REAL)
    day_basis: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text)


class SnotelStation(Base):
    __tablename__ = "snotel_stations"
    triplet: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    elevation_m: Mapped[float | None] = mapped_column(REAL)
    active: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NwsZoneGeom(Base):
    __tablename__ = "nws_zone_geoms"
    __table_args__ = (Index("ix_nws_zone_geoms_geom", "geom", postgresql_using="gist"),)
    zone_id: Mapped[str] = mapped_column(Text, primary_key=True)
    geom: Mapped[Any] = mapped_column(Geography(spatial_index=False))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NwsAlertCoverage(Base):
    __tablename__ = "nws_alert_coverage"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    scope: Mapped[str] = mapped_column(Text)
    runs_ok: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CellFeedPending(Base):
    __tablename__ = "cell_feed_pending"
    feed: Mapped[str] = mapped_column(Text, primary_key=True)
    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True)
    tz: Mapped[str] = mapped_column(Text, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

(Match the `Base` import to the one `app/models/conditions.py` uses; the CHECK constraints live in the migration, as in plan 3's models.) Register `feeds` in `app/models/__init__.py`; add `"app.models.feeds"` to the models mypy block.

Grants (append a "Plan 7 (0013)" block to `grants_phase2.sql`):

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON public.lightning_recent, public.glm_files_seen, public.glm_hours,
  public.lightning_daily, public.snotel_stations, public.nws_zone_geoms, public.nws_alert_coverage,
  public.cell_feed_pending TO ingest;
GRANT SELECT, UPDATE (end_date) ON public.lightning_coverage_periods TO ingest;
```

No `trainer` grants: `trainer` stays NOLOGIN with no privileges until Phase 3 (D13). `app` reads the new public tables through the existing default privileges and writes none of them. Remove the plan 3 line `GRANT SELECT, UPDATE (longitude) ON public.area_weekly_weather TO ingest;` (the table is gone). In `verify_roles_phase2.sql` add one `ingest_writes` row per (table, privilege) granted above in the file's existing VALUES format, and delete the `area_weekly_weather` row.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_migration_0013.py -q && uv run mypy` → PASS. `test_area_weekly.py` (plan 3) migrates to `head`; change its fixture to `migrated_db("0012_drop_legacy_routes", …)` in this commit so R7's tests still find the table.
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0013_live_feeds.py backend/app/models/ backend/tests/test_migration_0013.py backend/tests/test_area_weekly.py backend/pyproject.toml backend/db/roles/ && git commit -m "feat(db): 0013 live-feed and lightning tables, lightning read functions; retire area_weekly_weather"`

---

### Task 2: Nightly forecast with the `past_days` stopgap

**Files:**
- Modify: `backend/app/pipelines/open_meteo.py`, `backend/tests/test_open_meteo.py`
- Create: `backend/app/pipelines/forecast.py`, `backend/tests/test_forecast.py`

**Interfaces:**
- Consumes (plan 3): `Location(grid_bucket, lat, lon, tz)`, `LocationResponse`, `DAILY_VARS`, `ArchiveClient`, `day_rows(grid_bucket, tz, response, *, today, origin, model, source, report)`, `upsert_rows`, `localday.local_date(ts_utc, tz)`.
- Produces: `FORECAST_PUBLIC_URL`, `FORECAST_CUSTOMER_URL`, `FORECAST_DAYS = 4`, `PAST_DAYS = 3`, `ArchiveClient._get(params: dict[str, str], expected: int) -> list[LocationResponse]` (the retry loop, split out of `fetch`), `class ForecastClient(ArchiveClient)` with `fetch_forecast(locations: Sequence[Location], *, days: int = FORECAST_DAYS, past_days: int = PAST_DAYS) -> list[LocationResponse]` (one timezone per request, like `fetch`); in `forecast.py`: `SOURCE = "open_meteo_forecast"`, `MODEL = "best_match"`, `async route_series(conn) -> list[tuple[int, str]]`, `async run_forecast(engine_factory, client, *, now: datetime, batch: int = 100) -> dict[str, object]` (calls `day_rows(..., today=<the series' local today>, origin="forecast_api")`, so days before the local today are stored `stopgap` and the rest `forecast`).

Four forecast days because the 01:00 UTC run falls on the previous local evening across the US: local today + 3 still covers UTC today + 2. `past_days=3` re-fetches the last three local days every night; they are model analysis, not ERA5, so they are stored as `stopgap` and plan 3's January window overwrites them with ERA5.

- [ ] **Step 1: Failing tests** — append to `test_open_meteo.py` (merge the import into the file's top import block; ruff's `E402` rejects mid-file imports):

```python
from app.pipelines.open_meteo import ForecastClient


def test_forecast_parameters():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params), path=request.url.path, host=request.url.host)
        return httpx.Response(200, json=[_payload(40.0, -105.3, ["2026-09-28", "2026-09-29", "2026-09-30"], [1.0, 2.0, 3.0])])

    out = ForecastClient(None, transport=httpx.MockTransport(handler)).fetch_forecast(LOC[:1])
    assert len(out[0].daily.time) == 3
    assert seen["forecast_days"] == "4" and seen["past_days"] == "3" and seen["timezone"] == "America/Denver"
    assert seen["wind_speed_unit"] == "ms" and seen["host"] == "api.open-meteo.com"
    assert "start_date" not in seen and "models" not in seen
```

`backend/tests/test_forecast.py`:

```python
import asyncio
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.forecast import run_forecast
from app.pipelines.grid import grid_bucket
from app.pipelines.open_meteo import DAILY_VARS, ForecastClient
from tests.pgtest import migrated_db, requires_pg, sa_url

NOW = datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)
DENVER = "America/Denver"
DAYS = [f"2026-09-{d}" for d in range(24, 31)]


def handler(request: httpx.Request) -> httpx.Response:
    assert request.url.params["timezone"] == DENVER
    lats = request.url.params["latitude"].split(",")
    body = [{"latitude": float(la), "longitude": 0.0,
             "daily": {"time": DAYS, **{v: [1.0] * len(DAYS) for v in DAILY_VARS}}} for la in lats]
    return httpx.Response(200, json=body)


def _seed(b: int) -> str:
    return (f"INSERT INTO feature_points (point_key, lat, lon, grid_bucket, tz, h3_r5, h3_r7, feature_version) "
            f"VALUES ('40.00000:-105.30000', 40.0, -105.3, {b}, '{DENVER}', 'x', 'y', 'f-v1');"
            f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, record_kind, is_forecast, source) "
            f"VALUES ({b}, '{DENVER}', '2026-09-25', 5.0, 'era5', false, 'open_meteo_archive');")


async def _scenario(url: str) -> list[tuple[object, ...]]:
    result = await run_forecast(lambda: create_async_engine(url), ForecastClient(None, transport=httpx.MockTransport(handler)), now=NOW)
    assert result["status"] == "ok"
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return [tuple(r) for r in (await conn.execute(text(
                "SELECT date, record_kind, is_forecast, tmax FROM cell_daily_conditions ORDER BY date"))).all()]
    finally:
        await engine.dispose()


@requires_pg
def test_past_days_are_stopgap_future_days_forecast_and_era5_survives():
    b = grid_bucket(40.0, -105.3)
    with migrated_db(seed_sql=_seed(b)) as name:
        rows = asyncio.run(_scenario(sa_url(name)))
    # 01:00 UTC on 09-28 is the evening of 09-27 in Denver: 24-26 are past local days.
    assert rows == [
        (date(2026, 9, 24), "stopgap", False, 1.0),
        (date(2026, 9, 25), "era5", False, 5.0),
        (date(2026, 9, 26), "stopgap", False, 1.0),
        *[(date(2026, 9, d), "forecast", True, 1.0) for d in (27, 28, 29, 30)],
    ]
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_open_meteo.py tests/test_forecast.py -q` → FAIL (`ForecastClient` / `app.pipelines.forecast` missing).

- [ ] **Step 3: Implement** — in `open_meteo.py`:

```python
FORECAST_PUBLIC_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_CUSTOMER_URL = "https://customer-api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 4
PAST_DAYS = 3


class ForecastClient(ArchiveClient):
    def __init__(self, api_key: str | None, **kwargs: Any) -> None:
        super().__init__(api_key, **kwargs)
        self._url = FORECAST_CUSTOMER_URL if api_key else FORECAST_PUBLIC_URL

    def fetch_forecast(
        self, locations: Sequence[Location], *, days: int = FORECAST_DAYS, past_days: int = PAST_DAYS
    ) -> list[LocationResponse]:
        zones = {loc.tz for loc in locations}
        if len(zones) != 1:
            raise ValueError(f"one request covers exactly one timezone, got {sorted(zones)}")
        params = {
            "latitude": ",".join(f"{loc.lat:.1f}" for loc in locations),
            "longitude": ",".join(f"{loc.lon:.1f}" for loc in locations),
            "daily": ",".join(DAILY_VARS),
            "forecast_days": str(days),
            "past_days": str(past_days),
            "timezone": zones.pop(),
            "wind_speed_unit": "ms",
        }
        return self._get(params, len(locations))
```

Move plan 3's retry loop out of `ArchiveClient.fetch` into `_get(self, params: dict[str, str], expected: int) -> list[LocationResponse]` (it adds `apikey` when a key is set, keeps the no-key-in-errors rule and returns `self._parse(response, expected)`); `fetch` builds its params and calls `_get`, and plan 3's `test_open_meteo.py` stays green unchanged. Add `from typing import Any`.

`backend/app/pipelines/forecast.py`:

```python
"""Nightly Open-Meteo forecast for every route series (grid bucket x crag timezone), before
the 02:00 scoring run (P2-6). past_days rows are a flagged non-ERA5 stopgap; plan 3's
January window replaces them with ERA5, and neither kind ever replaces an ERA5 row."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.cell_conditions import day_rows, upsert_rows
from app.pipelines.grid import bucket_center
from app.pipelines.ingest_log import finish_run, start_run, write_quarantine
from app.pipelines.localday import local_date
from app.pipelines.open_meteo import FORECAST_DAYS, PAST_DAYS, ForecastClient, Location, OpenMeteoError
from app.pipelines.validate import ValidationReport

SOURCE = "open_meteo_forecast"
MODEL = "best_match"


async def route_series(conn: AsyncConnection) -> list[tuple[int, str]]:
    rows = (await conn.execute(text(
        "SELECT DISTINCT grid_bucket, tz FROM feature_points WHERE tz IS NOT NULL ORDER BY 1, 2"))).all()
    return [(int(b), str(tz)) for b, tz in rows]


async def run_forecast(
    engine_factory: Callable[[], AsyncEngine], client: ForecastClient, *, now: datetime, batch: int = 100
) -> dict[str, object]:
    today = now.date()
    engine = engine_factory()
    report = ValidationReport(SOURCE)
    problems: list[str] = []
    written = 0
    try:
        async with engine.begin() as conn:
            series = await route_series(conn)
            run_id: uuid.UUID = await start_run(conn, source=SOURCE, window_start=today - timedelta(days=PAST_DAYS),
                                                window_end=today + timedelta(days=FORECAST_DAYS), content_sha256=None)
        by_tz: dict[str, list[int]] = defaultdict(list)
        for b, tz in series:
            by_tz[tz].append(b)
        for tz, buckets in sorted(by_tz.items()):
            local_today = local_date(now, tz)
            for i in range(0, len(buckets), batch):
                group = buckets[i : i + batch]
                try:
                    responses = client.fetch_forecast([Location(b, *bucket_center(b), tz) for b in group])
                except OpenMeteoError as exc:
                    problems.append(f"{tz}: {exc}")
                    continue
                rows = [r for b, resp in zip(group, responses)
                        for r in day_rows(b, tz, resp, today=local_today, origin="forecast_api", model=MODEL,
                                          source=SOURCE, report=report)]
                async with engine.begin() as conn:
                    written += await upsert_rows(conn, rows, run_id=run_id)
        status = "ok" if not problems else "failed"
        async with engine.begin() as conn:
            await write_quarantine(conn, run_id, report)
            await finish_run(conn, run_id, status=status, report=report, rows_upserted=written, problems=problems)
    finally:
        await engine.dispose()
    return {"status": status, "series": len(series), "rows": written, "problems": len(problems)}
```

`today` passed to `day_rows` is the series' **local** today, so the 01:00 UTC run (the previous evening across the US) stores that evening's local day as `forecast`, never as stopgap. A partial failure marks the run `failed` (so `/health/data` and healthchecks see it) while keeping every batch that did succeed. Append `"app.pipelines.forecast"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_open_meteo.py tests/test_forecast.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/open_meteo.py backend/app/pipelines/forecast.py backend/tests/test_open_meteo.py backend/tests/test_forecast.py backend/pyproject.toml && git commit -m "feat(pipelines): nightly forecast per route series with flagged past_days stopgap"`

---

### Task 3: Feed values that are never lost

**Files:**
- Create: `backend/app/pipelines/feed_values.py`, `backend/tests/test_feed_values.py`
- Modify: `backend/app/pipelines/forecast.py`, `backend/pyproject.toml`

**Interfaces:**
- Produces (`feed_values.py`): `Feed = Literal["nws", "aqi", "snotel"]`, `@dataclass(frozen=True) FeedValue(grid_bucket: int, tz: str, date: date, payload: dict[str, object])`, `async write_feed_values(conn, feed: Feed, values: Sequence[FeedValue]) -> dict[str, int]` (`{"applied": n, "staged": m}`), `async drain_pending(conn) -> dict[str, int]` (`{"drained": n, "waiting": m}`).

Merge rules per feed: `aqi` keeps the larger value (`GREATEST` ignores NULL, so NULL never becomes 0); `nws` takes the union of codes and writes `'{}'` when a collected day had none; `snotel` replaces.

- [ ] **Step 1: Failing tests** — `backend/tests/test_feed_values.py`:

```python
import asyncio
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.feed_values import FeedValue, drain_pending, write_feed_values
from tests.pgtest import migrated_db, requires_pg, sa_url

pytestmark = requires_pg

TZ = "America/Denver"
DAY = date(2026, 9, 27)
ROW = ("INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, is_forecast, source) "
       "VALUES ({b}, 'America/Denver', '2026-09-27', 'forecast', true, 'open_meteo_forecast');")


async def _go(url: str, steps) -> object:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            return await steps(conn)
    finally:
        await engine.dispose()


def test_value_without_a_row_waits_and_is_applied_later():
    async def steps(conn):
        out = await write_feed_values(conn, "aqi", [FeedValue(1, TZ, DAY, {"aqi": 61})])
        assert out == {"applied": 0, "staged": 1}
        await write_feed_values(conn, "aqi", [FeedValue(1, TZ, DAY, {"aqi": 40})])
        await conn.execute(text(ROW.format(b=1)))
        assert await drain_pending(conn) == {"drained": 1, "waiting": 0}
        return (await conn.execute(text("SELECT aqi FROM cell_daily_conditions"))).scalar_one()

    with migrated_db() as name:
        assert asyncio.run(_go(sa_url(name), steps)) == 61


def test_aqi_keeps_the_max_and_never_turns_null_into_zero():
    async def steps(conn):
        await conn.execute(text(ROW.format(b=1) + ROW.format(b=2)))
        await write_feed_values(conn, "aqi", [FeedValue(1, TZ, DAY, {"aqi": 30}), FeedValue(1, TZ, DAY, {"aqi": 20})])
        return [tuple(r) for r in (await conn.execute(text("SELECT grid_bucket, aqi FROM cell_daily_conditions ORDER BY 1"))).all()]

    with migrated_db() as name:
        assert asyncio.run(_go(sa_url(name), steps)) == [(1, 30), (2, None)]


def test_collected_day_without_alerts_is_empty_array_not_null():
    async def steps(conn):
        await conn.execute(text(ROW.format(b=1) + ROW.format(b=2)))
        await write_feed_values(conn, "nws", [FeedValue(1, TZ, DAY, {"codes": []}), FeedValue(2, TZ, DAY, {"codes": ["WS.W"]})])
        await write_feed_values(conn, "nws", [FeedValue(2, TZ, DAY, {"codes": ["SV.W", "WS.W"]})])
        return [tuple(r) for r in (await conn.execute(text(
            "SELECT grid_bucket, nws_alert_codes FROM cell_daily_conditions ORDER BY 1"))).all()]

    with migrated_db() as name:
        assert asyncio.run(_go(sa_url(name), steps)) == [(1, []), (2, ["SV.W", "WS.W"])]
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_feed_values.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** `backend/app/pipelines/feed_values.py`:

```python
"""Column-specific writes for NWS alerts, AQI and SNOTEL on cell_daily_conditions. The weather
row is created only by the forecast/archive writers; a value that arrives before its row waits
in cell_feed_pending and is applied by drain_pending (called after every forecast run), so a
missing forecast never silently loses an observation."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

Feed = Literal["nws", "aqi", "snotel"]

_WHERE = " WHERE grid_bucket = :b AND tz = :tz AND date = :d"

_APPLY: dict[str, str] = {
    "aqi": "UPDATE cell_daily_conditions SET aqi = GREATEST(aqi, CAST(CAST(:p AS jsonb) ->> 'aqi' AS smallint))" + _WHERE,
    "nws": (
        "UPDATE cell_daily_conditions SET nws_alert_codes = ARRAY(SELECT DISTINCT c FROM unnest("
        "coalesce(nws_alert_codes, '{}'::text[]) || ARRAY(SELECT jsonb_array_elements_text(CAST(:p AS jsonb) -> 'codes'))"
        ") AS c ORDER BY c)" + _WHERE
    ),
    "snotel": (
        "UPDATE cell_daily_conditions SET swe_delta_mm = CAST(CAST(:p AS jsonb) ->> 'swe_delta_mm' AS real), "
        "snow_depth_cm = CAST(CAST(:p AS jsonb) ->> 'snow_depth_cm' AS real)" + _WHERE
    ),
}

_MERGE: dict[str, str] = {
    "aqi": "jsonb_build_object('aqi', GREATEST((cell_feed_pending.payload ->> 'aqi')::int, (EXCLUDED.payload ->> 'aqi')::int))",
    "nws": (
        "jsonb_build_object('codes', (SELECT coalesce(jsonb_agg(DISTINCT x ORDER BY x), '[]'::jsonb) FROM "
        "jsonb_array_elements_text((cell_feed_pending.payload -> 'codes') || (EXCLUDED.payload -> 'codes')) AS x))"
    ),
    "snotel": "EXCLUDED.payload",
}

_EXISTING = text(
    "SELECT grid_bucket, tz, date FROM cell_daily_conditions WHERE (grid_bucket, tz, date) IN ("
    "SELECT * FROM unnest(CAST(:b AS integer[]), CAST(:tz AS text[]), CAST(:d AS date[])))"
)


@dataclass(frozen=True)
class FeedValue:
    grid_bucket: int
    tz: str
    date: date
    payload: dict[str, object]


def _params(v: FeedValue) -> dict[str, object]:
    return {"b": v.grid_bucket, "tz": v.tz, "d": v.date, "p": json.dumps(v.payload, sort_keys=True)}


async def write_feed_values(conn: AsyncConnection, feed: Feed, values: Sequence[FeedValue]) -> dict[str, int]:
    if not values:
        return {"applied": 0, "staged": 0}
    have = {(int(b), str(tz), d) for b, tz, d in (await conn.execute(_EXISTING, {
        "b": [v.grid_bucket for v in values], "tz": [v.tz for v in values], "d": [v.date for v in values]})).all()}
    applied = [v for v in values if (v.grid_bucket, v.tz, v.date) in have]
    staged = [v for v in values if (v.grid_bucket, v.tz, v.date) not in have]
    for v in applied:
        await conn.execute(text(_APPLY[feed]), _params(v))
    for v in staged:
        await conn.execute(text(
            "INSERT INTO cell_feed_pending (feed, grid_bucket, tz, date, payload) VALUES (:f, :b, :tz, :d, CAST(:p AS jsonb)) "
            f"ON CONFLICT (feed, grid_bucket, tz, date) DO UPDATE SET payload = {_MERGE[feed]}"), {"f": feed} | _params(v))
    return {"applied": len(applied), "staged": len(staged)}


async def drain_pending(conn: AsyncConnection) -> dict[str, int]:
    ready = (await conn.execute(text(
        "SELECT p.feed, p.grid_bucket, p.tz, p.date, p.payload FROM cell_feed_pending p "
        "JOIN cell_daily_conditions c ON c.grid_bucket = p.grid_bucket AND c.tz = p.tz AND c.date = p.date "
        "ORDER BY p.first_seen_at"))).all()
    for feed, b, tz, d, payload in ready:
        v = FeedValue(int(b), str(tz), d, payload if isinstance(payload, dict) else json.loads(payload))
        await conn.execute(text(_APPLY[str(feed)]), _params(v))
        await conn.execute(text(
            "DELETE FROM cell_feed_pending WHERE feed = :f AND grid_bucket = :b AND tz = :tz AND date = :d"),
            {"f": feed, "b": b, "tz": tz, "d": d})
    waiting = int((await conn.execute(text("SELECT count(*) FROM cell_feed_pending"))).scalar_one())
    return {"drained": len(ready), "waiting": waiting}
```

In `forecast.run_forecast`, drain pending feed values in the same final transaction, before `finish_run`:

```python
        async with engine.begin() as conn:
            drained = await drain_pending(conn)
            await write_quarantine(conn, run_id, report)
            await finish_run(conn, run_id, status=status, report=report, rows_upserted=written,
                             problems=problems + [f"pending_waiting={drained['waiting']}"] if drained["waiting"] else problems)
```

(import `drain_pending` from `app.pipelines.feed_values`; values still waiting are reported every night in the run log rather than dropped). Append `"app.pipelines.feed_values"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_feed_values.py tests/test_forecast.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/feed_values.py backend/app/pipelines/forecast.py backend/tests/test_feed_values.py backend/pyproject.toml && git commit -m "feat(pipelines): feed values never lost when the weather row is missing"`

---

### Task 4: SNOTEL snowpack

**Files:**
- Create: `backend/app/pipelines/snotel.py`, `backend/tests/test_snotel.py`, `backend/tests/fixtures/snotel_stations.json`, `backend/tests/fixtures/snotel_data.json`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"`, `SNOTEL_TRIPLETS = "*:*:SNTL"`, `SOURCE = "snotel"`, `ATTACH_KM = 30.0`, `ATTACH_DZ_M = 500.0`, `IN_TO_MM = 25.4`, `IN_TO_CM = 2.54`, `@dataclass(frozen=True) Station(triplet, name, lat, lon, elevation_m)`, `class SnotelClient(transport=None)` with `stations() -> list[Station]` (SNOTEL only) and `daily(triplets: Sequence[str], start: date, end: date) -> dict[str, dict[date, tuple[float | None, float | None]]]` (SWE mm, depth cm), `attach(points: Mapping[K, tuple[float, float, float | None]], stations: Sequence[Station]) -> dict[K, str]`, `swe_delta(series, day) -> float | None`, `async run_snotel(engine_factory, client, *, now: datetime) -> dict[str, object]` (per route series, the series' local yesterday, written through `write_feed_values("snotel", …)`), CLI `python -m app.pipelines.snotel`.

`networkCds=SNTL` is ignored by AWDB (it returned 4,397 mixed stations when checked 2026-09-28); `stationTriplets=*:*:SNTL` returns the 919 SNOTEL stations, and the client also drops any triplet not ending in `:SNTL`, so snow courses and stream gauges are never attached.

- [ ] **Step 1: Record fixtures (agent)** — `curl -s "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations?stationTriplets=*:*:SNTL&activeOnly=true" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d)); json.dump(d[:3], open("backend/tests/fixtures/snotel_stations.json","w"))'` (expect a count near 919; confirm fields `stationTriplet`, `name`, `latitude`, `longitude`, `elevation` in feet); take the first triplet `T` from the fixture and run `curl -s "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data?stationTriplets=$T&elements=WTEQ,SNWD&duration=DAILY&beginDate=2026-01-01&endDate=2026-01-05" > backend/tests/fixtures/snotel_data.json`. If field names differ, change only the key lookups in `stations()`/`daily()`.

- [ ] **Step 2: Failing tests** — `backend/tests/test_snotel.py`:

```python
import json
from datetime import date
from pathlib import Path

import httpx

from app.pipelines.snotel import SnotelClient, Station, attach, swe_delta

FIX = Path(__file__).parent / "fixtures"


def test_attach_requires_distance_and_elevation_match():
    stations = [Station("1:CO:SNTL", "Near High", 40.05, -105.3, 3200.0), Station("2:CO:SNTL", "Near Low", 40.02, -105.3, 1800.0)]
    points = {1: (40.0, -105.3, 3000.0), 2: (41.0, -105.3, 3000.0), 3: (40.0, -105.3, None)}
    assert attach(points, stations) == {1: "1:CO:SNTL"}


def test_swe_delta_is_three_day_change_and_null_when_missing():
    s = {date(2026, 1, 1): (100.0, 50.0), date(2026, 1, 4): (130.0, 60.0), date(2026, 1, 5): (None, 60.0)}
    assert swe_delta(s, date(2026, 1, 4)) == 30.0
    assert swe_delta(s, date(2026, 1, 5)) is None


def test_station_query_uses_sntl_triplets_and_drops_other_networks():
    seen: dict[str, str] = {}
    body = json.loads((FIX / "snotel_stations.json").read_text())
    body.append(dict(body[0], stationTriplet="9999:CO:SCAN"))

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=body)

    stations = SnotelClient(transport=httpx.MockTransport(handler)).stations()
    assert seen["stationTriplets"] == "*:*:SNTL" and "networkCds" not in seen
    assert stations and all(s.triplet.endswith(":SNTL") for s in stations)


def test_daily_parses_the_recorded_response():
    body = json.loads((FIX / "snotel_data.json").read_text())
    client = SnotelClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    out = client.daily([body[0]["stationTriplet"]], date(2026, 1, 1), date(2026, 1, 5))
    series = out[body[0]["stationTriplet"]]
    assert set(series) <= {date(2026, 1, d) for d in range(1, 6)}
    assert all(v is None or v >= 0 for pair in series.values() for v in pair)
```

- [ ] **Step 3: Implement** `backend/app/pipelines/snotel.py`:

```python
"""NRCS SNOTEL (public domain) snow water equivalent and depth, attached to a route series
only when a SNOTEL station is within 30 km and +-500 m of the series' mean route elevation
(spec). Values land on the series' local yesterday through feed_values (never lost)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import TypeVar

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.pipelines.feed_values import FeedValue, write_feed_values
from app.pipelines.geo import haversine_km
from app.pipelines.ingest_log import finish_run, start_run, write_quarantine
from app.pipelines.localday import local_date
from app.pipelines.validate import ValidationReport, range_problem

AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
SNOTEL_TRIPLETS = "*:*:SNTL"
SOURCE = "snotel"
ATTACH_KM = 30.0
ATTACH_DZ_M = 500.0
IN_TO_MM = 25.4
IN_TO_CM = 2.54
FT_TO_M = 0.3048

K = TypeVar("K")


@dataclass(frozen=True)
class Station:
    triplet: str
    name: str
    lat: float
    lon: float
    elevation_m: float | None


class SnotelClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(transport=transport, timeout=60.0)

    def stations(self) -> list[Station]:
        response = self._client.get(f"{AWDB}/stations", params={"stationTriplets": SNOTEL_TRIPLETS, "activeOnly": "true"})
        response.raise_for_status()
        return [Station(s["stationTriplet"], s.get("name") or "", float(s["latitude"]), float(s["longitude"]),
                        float(s["elevation"]) * FT_TO_M if s.get("elevation") is not None else None)
                for s in response.json() if str(s.get("stationTriplet", "")).endswith(":SNTL")]

    def daily(self, triplets: Sequence[str], start: date, end: date) -> dict[str, dict[date, tuple[float | None, float | None]]]:
        response = self._client.get(f"{AWDB}/data", params={
            "stationTriplets": ",".join(triplets), "elements": "WTEQ,SNWD", "duration": "DAILY",
            "beginDate": start.isoformat(), "endDate": end.isoformat()})
        response.raise_for_status()
        out: dict[str, dict[date, tuple[float | None, float | None]]] = {}
        for station in response.json():
            series: dict[date, list[float | None]] = {}
            for element in station.get("data", []):
                code = element["stationElement"]["elementCode"]
                for v in element.get("values", []):
                    day = date.fromisoformat(v["date"][:10])
                    slot = series.setdefault(day, [None, None])
                    raw = v.get("value")
                    if raw is None:
                        continue
                    if code == "WTEQ" and range_problem(float(raw) * IN_TO_MM, 0, 5000) is None:
                        slot[0] = float(raw) * IN_TO_MM
                    elif code == "SNWD" and range_problem(float(raw) * IN_TO_CM, 0, 2000) is None:
                        slot[1] = float(raw) * IN_TO_CM
            out[station["stationTriplet"]] = {d: (s[0], s[1]) for d, s in series.items()}
        return out


def attach(points: Mapping[K, tuple[float, float, float | None]], stations: Sequence[Station]) -> dict[K, str]:
    chosen: dict[K, str] = {}
    for key, (lat, lon, elev) in points.items():
        if elev is None:
            continue
        best: tuple[float, str] | None = None
        for s in stations:
            if s.elevation_m is None or abs(s.elevation_m - elev) > ATTACH_DZ_M:
                continue
            d = haversine_km(lat, lon, s.lat, s.lon)
            if d <= ATTACH_KM and (best is None or d < best[0]):
                best = (d, s.triplet)
        if best is not None:
            chosen[key] = best[1]
    return chosen


def swe_delta(series: Mapping[date, tuple[float | None, float | None]], day: date) -> float | None:
    now, before = series.get(day, (None, None))[0], series.get(day - timedelta(days=3), (None, None))[0]
    return None if now is None or before is None else now - before


async def run_snotel(engine_factory: Callable[[], AsyncEngine], client: SnotelClient, *, now: datetime) -> dict[str, object]:
    engine = engine_factory()
    report = ValidationReport(SOURCE)
    yesterday = now.date() - timedelta(days=1)
    try:
        async with engine.begin() as conn:
            points = {(int(b), str(tz)): (float(la), float(lo), float(e) if e is not None else None)
                      for b, tz, la, lo, e in (await conn.execute(text(
                          "SELECT grid_bucket, tz, avg(lat), avg(lon), avg(elevation_m) FROM feature_points "
                          "WHERE tz IS NOT NULL GROUP BY grid_bucket, tz"))).all()}
            run_id: uuid.UUID = await start_run(conn, source=SOURCE, window_start=yesterday, window_end=yesterday,
                                                content_sha256=None)
        try:
            mapping = attach(points, client.stations())
            days = {key: local_date(now, key[1]) - timedelta(days=1) for key in mapping}
            data: dict[str, dict[date, tuple[float | None, float | None]]] = {}
            triplets = sorted(set(mapping.values()))
            if triplets:
                first, last = min(days.values()) - timedelta(days=3), max(days.values())
                for i in range(0, len(triplets), 100):
                    data |= client.daily(triplets[i : i + 100], first, last)
        except httpx.HTTPError as exc:
            async with engine.begin() as conn:
                await finish_run(conn, run_id, status="failed", report=report, rows_upserted=0,
                                 problems=[f"awdb: {type(exc).__name__}"])
            return {"status": "failed"}
        values: list[FeedValue] = []
        for (b, tz), triplet in mapping.items():
            day = days[(b, tz)]
            series = data.get(triplet, {})
            depth = series.get(day, (None, None))[1]
            delta = swe_delta(series, day)
            if depth is None and delta is None:
                report.quarantine(f"{b}:{tz}:{day.isoformat()}", "no_station_value", station=triplet)
                continue
            report.accept()
            values.append(FeedValue(b, tz, day, {"swe_delta_mm": delta, "snow_depth_cm": depth}))
        async with engine.begin() as conn:
            out = await write_feed_values(conn, "snotel", values)
            await write_quarantine(conn, run_id, report)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=out["applied"],
                             problems=[f"staged={out['staged']}"] if out["staged"] else None)
    finally:
        await engine.dispose()
    return {"status": "ok", "attached": len(mapping), "values": len(values)}


async def _main() -> dict[str, object]:
    from app.pipelines.db import ingest_engine

    return await run_snotel(ingest_engine, SnotelClient(), now=datetime.now(timezone.utc))


if __name__ == "__main__":
    result = asyncio.run(_main())
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["status"] == "ok" else 1)
```

A series without a station inside both limits gets no value (NULL stays NULL); a station without data that day is quarantined `no_station_value` with counts in the run log. Append `"app.pipelines.snotel"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_snotel.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/snotel.py backend/tests/test_snotel.py backend/tests/fixtures/snotel_*.json backend/pyproject.toml && git commit -m "feat(pipelines): SNOTEL (SNTL triplets only) SWE change and depth per route series"`

---

### Task 5: NWS alerts (live) and IEM polygon-warning archive

**Files:**
- Create: `backend/app/pipelines/nws.py`, `backend/tests/test_nws.py`, `backend/tests/fixtures/nws_active.json`, `backend/tests/fixtures/iem_sbw.json`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: `forecast.route_series`, `feed_values.write_feed_values`, `localday.local_date`, plan 3's `accident_conditions` view columns `grid_bucket`, `tz`, `date`.
- Produces: `NWS_URL`, `IEM_SBW`, `USER_AGENT`, `ZONES_PREFIX = "https://api.weather.gov/zones/"`, `ARCHIVE_START = date(2007, 10, 1)`, `SOURCE_LIVE = "nws_alerts"`, `SOURCE_ARCHIVE = "iem_sbw"`, `parse_vtec(values: Iterable[str]) -> tuple[str, ...]`, `@dataclass(frozen=True) Alert(alert_id: str, codes: tuple[str, ...], geometry: dict[str, object] | None, zones: tuple[str, ...], onset: datetime | None, ends: datetime | None)`, `active_on(alert, day: date, tz: str) -> bool`, `class NwsClient(transport=None)` with `active() -> tuple[list[Alert], int]` (alerts with VTEC codes, count skipped for no VTEC), `zone_geometry(zone_url) -> dict[str, object] | None`, `iem_polygons(start, end) -> list[Alert]`; `async ensure_zone_geoms(conn, client, alerts) -> int`, `async alert_values(conn, alerts, *, series: Sequence[tuple[int, str]], days: Mapping[str, date], match_on_centres: bool) -> list[FeedValue]`, `async mark_coverage(conn, days: Iterable[date], scope: Literal["live_all", "archive_polygon_only"]) -> None`, `async run_live(engine_factory=None, client=None, *, now=None) -> dict[str, object]`, `async run_archive(engine_factory, client, *, start: date, end: date, today: date) -> dict[str, object]`, CLI `python -m app.pipelines.nws live | archive --start YYYY-MM-DD --end YYYY-MM-DD`.

Every live run writes a value for **every** route series on its local today (an empty list when no alert), so NULL keeps meaning "not collected". Archive days before `ARCHIVE_START` are not fetched and stay NULL.

- [ ] **Step 1: Record fixtures (agent)** — public-domain responses, trimmed to three features:

```bash
curl -s -H "User-Agent: SafeAscent-data/1.0 (https://github.com/SebastianFrazier26/SafeAscent)" \
  "https://api.weather.gov/alerts/active?status=actual&message_type=alert" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); d["features"]=[f for f in d["features"] if f["properties"].get("parameters",{}).get("VTEC")][:3]; print(json.dumps(d))' \
  > backend/tests/fixtures/nws_active.json
curl -s "https://mesonet.agron.iastate.edu/geojson/sbw.geojson?sts=2024-07-01T00:00Z&ets=2024-07-01T06:00Z" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); d["features"]=d["features"][:3]; print(json.dumps(d))' \
  > backend/tests/fixtures/iem_sbw.json
```

If the NWS fixture comes back with no features (no VTEC alert active nationally), re-run later. Confirm property names (`id`, `parameters.VTEC`, `affectedZones`, `onset`, `ends`/`expires` for NWS; `phenomena`, `significance`, `issue`, `expire` for IEM) and adjust only the lookups in `active()`/`iem_polygons()`. Confirm on the IEM site that storm-based polygons begin with the 2007-10-01 NWS change; if IEM documents a different first date, change only `ARCHIVE_START` and its test.

- [ ] **Step 2: Failing tests** — `backend/tests/test_nws.py`:

```python
import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.feed_values import write_feed_values
from app.pipelines.grid import grid_bucket
from app.pipelines.nws import ARCHIVE_START, Alert, NwsClient, active_on, alert_values, parse_vtec
from tests.pgtest import migrated_db, requires_pg, sa_url

FIX = Path(__file__).parent / "fixtures"
DENVER = "America/Denver"


def test_parse_vtec_codes():
    assert parse_vtec(["/O.NEW.KBOU.WS.W.0012.260928T1800Z-260929T1200Z/"]) == ("WS.W",)
    assert parse_vtec(["/O.CON.KBOU.WS.W.0012.000000T0000Z-260929T1200Z/", "/O.EXA.KBOU.SV.A.0003.260928T1800Z-260929T0000Z/"]) == ("SV.A", "WS.W")
    assert parse_vtec(["not vtec"]) == ()


def test_active_alerts_parse_vtec_and_send_a_user_agent():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json=json.loads((FIX / "nws_active.json").read_text()))

    alerts, no_vtec = NwsClient(transport=httpx.MockTransport(handler)).active()
    assert alerts and all(a.codes for a in alerts) and no_vtec == 0
    assert "SafeAscent" in seen[0]


def test_iem_polygons_use_the_same_code_form():
    client = NwsClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=json.loads((FIX / "iem_sbw.json").read_text()))))
    alerts = client.iem_polygons(datetime(2024, 7, 1, tzinfo=timezone.utc), datetime(2024, 7, 1, 6, tzinfo=timezone.utc))
    assert alerts and all(len(c) == 4 and c[2] == "." for a in alerts for c in a.codes)


def test_alert_is_assigned_to_the_local_day():
    alert = Alert("a", ("SV.W",), None, (), datetime(2026, 9, 28, 2, tzinfo=timezone.utc), datetime(2026, 9, 28, 3, tzinfo=timezone.utc))
    assert active_on(alert, date(2026, 9, 27), DENVER)
    assert not active_on(alert, date(2026, 9, 28), DENVER)


def test_archive_start():
    assert ARCHIVE_START == date(2007, 10, 1)


@requires_pg
def test_polygon_alert_marks_the_series_and_others_get_empty_arrays():
    inside, outside = grid_bucket(40.0, -105.3), grid_bucket(41.0, -106.3)
    seed = "".join(
        f"INSERT INTO feature_points (point_key, lat, lon, grid_bucket, tz, h3_r5, h3_r7, feature_version) VALUES "
        f"('{la:.5f}:{lo:.5f}', {la}, {lo}, {b}, '{DENVER}', 'x', 'y', 'f-v1');"
        f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, is_forecast, source) "
        f"VALUES ({b}, '{DENVER}', '2026-09-27', 'forecast', true, 'open_meteo_forecast');"
        for b, la, lo in ((inside, 40.0, -105.3), (outside, 41.0, -106.3)))
    square = {"type": "Polygon", "coordinates": [[[-105.5, 39.8], [-105.0, 39.8], [-105.0, 40.2], [-105.5, 40.2], [-105.5, 39.8]]]}
    alert = Alert("a1", ("SV.W",), square, (), None, None)

    async def go(url: str) -> list[tuple[object, ...]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                values = await alert_values(conn, [alert], series=[(inside, DENVER), (outside, DENVER)],
                                            days={DENVER: date(2026, 9, 27)}, match_on_centres=False)
                await write_feed_values(conn, "nws", values)
                return [tuple(r) for r in (await conn.execute(text(
                    "SELECT grid_bucket, nws_alert_codes FROM cell_daily_conditions ORDER BY 1"))).all()]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        rows = asyncio.run(go(sa_url(name)))
    assert dict(rows) == {inside: ["SV.W"], outside: []}
```

- [ ] **Step 3: Implement** `backend/app/pipelines/nws.py`:

```python
"""NWS active alerts (hourly) and IEM storm-based-warning polygons (history), stored as VTEC
phenomena.significance codes on each route/incident series' local day. NULL = not collected,
'{}' = collected with no alert; nws_alert_coverage records whether a day covers all live
alerts or archived polygons only (zone-based warnings have no archived polygons)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.feed_values import FeedValue, write_feed_values
from app.pipelines.grid import bucket_center
from app.pipelines.ingest_log import find_completed, finish_run, sha256_rows, start_run
from app.pipelines.localday import local_date
from app.pipelines.validate import ValidationReport

NWS_URL = "https://api.weather.gov/alerts/active"
IEM_SBW = "https://mesonet.agron.iastate.edu/geojson/sbw.geojson"
USER_AGENT = "SafeAscent-data/1.0 (https://github.com/SebastianFrazier26/SafeAscent)"
ZONES_PREFIX = "https://api.weather.gov/zones/"
ARCHIVE_START = date(2007, 10, 1)
SOURCE_LIVE = "nws_alerts"
SOURCE_ARCHIVE = "iem_sbw"
_VTEC = re.compile(r"^/[OTEX]\.[A-Z]{3}\.[A-Z0-9]{4}\.([A-Z]{2})\.([A-Z])\.")

_POINT = "ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography"
_ROUTE_POLY = ("SELECT DISTINCT p.grid_bucket, p.tz FROM feature_points p WHERE p.tz IS NOT NULL AND "
               "ST_Intersects(ST_GeomFromGeoJSON(:g)::geography, " + _POINT.format(lon="p.lon", lat="p.lat") + ")")
_ROUTE_ZONE = ("SELECT DISTINCT p.grid_bucket, p.tz FROM feature_points p JOIN nws_zone_geoms z ON z.zone_id = ANY(:ids) "
               "AND ST_Intersects(z.geom, " + _POINT.format(lon="p.lon", lat="p.lat") + ") WHERE p.tz IS NOT NULL")
_SERIES = "unnest(CAST(:b AS integer[]), CAST(:tz AS text[]), CAST(:lat AS float8[]), CAST(:lon AS float8[])) AS s(b, tz, lat, lon)"
_SERIES_POLY = (f"SELECT DISTINCT s.b, s.tz FROM {_SERIES} WHERE "
                "ST_Intersects(ST_GeomFromGeoJSON(:g)::geography, " + _POINT.format(lon="s.lon", lat="s.lat") + ")")
_SERIES_ZONE = (f"SELECT DISTINCT s.b, s.tz FROM {_SERIES} JOIN nws_zone_geoms z ON z.zone_id = ANY(:ids) "
                "AND ST_Intersects(z.geom, " + _POINT.format(lon="s.lon", lat="s.lat") + ")")


def parse_vtec(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({f"{m.group(1)}.{m.group(2)}" for v in values if (m := _VTEC.match(v.strip()))}))


@dataclass(frozen=True)
class Alert:
    alert_id: str
    codes: tuple[str, ...]
    geometry: dict[str, object] | None
    zones: tuple[str, ...]
    onset: datetime | None
    ends: datetime | None


def _dt(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None


def active_on(alert: Alert, day: date, tz: str) -> bool:
    zone = ZoneInfo(tz)
    lo = datetime.combine(day, time(), zone)
    hi = datetime.combine(day + timedelta(days=1), time(), zone)
    return (alert.onset is None or alert.onset < hi) and (alert.ends is None or alert.ends > lo)


class NwsClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(transport=transport, timeout=60.0,
                                    headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})

    def active(self) -> tuple[list[Alert], int]:
        response = self._client.get(NWS_URL, params={"status": "actual", "message_type": "alert"})
        response.raise_for_status()
        out: list[Alert] = []
        no_vtec = 0
        for f in response.json().get("features", []):
            p = f.get("properties", {})
            codes = parse_vtec((p.get("parameters") or {}).get("VTEC") or [])
            if not codes:
                no_vtec += 1
                continue
            out.append(Alert(str(p.get("id") or f.get("id")), codes, f.get("geometry"),
                             tuple(p.get("affectedZones") or ()), _dt(p.get("onset")), _dt(p.get("ends") or p.get("expires"))))
        return out, no_vtec

    def zone_geometry(self, zone_url: str) -> dict[str, object] | None:
        if not zone_url.startswith(ZONES_PREFIX):
            return None
        response = self._client.get(zone_url)
        if response.status_code != 200:
            return None
        geometry = response.json().get("geometry")
        return geometry if isinstance(geometry, dict) else None

    def iem_polygons(self, start: datetime, end: datetime) -> list[Alert]:
        response = self._client.get(IEM_SBW, params={"sts": start.strftime("%Y-%m-%dT%H:%MZ"), "ets": end.strftime("%Y-%m-%dT%H:%MZ")})
        response.raise_for_status()
        out: list[Alert] = []
        for f in response.json().get("features", []):
            p = f.get("properties", {})
            phenomena, significance = str(p.get("phenomena") or ""), str(p.get("significance") or "")
            if len(phenomena) != 2 or len(significance) != 1 or not f.get("geometry"):
                continue
            out.append(Alert(str(f.get("id") or p.get("product_id")), (f"{phenomena}.{significance}",), f.get("geometry"),
                             (), _dt(p.get("issue")), _dt(p.get("expire"))))
        return out


def _series_params(series: Sequence[tuple[int, str]]) -> dict[str, object]:
    centres = [bucket_center(b) for b, _ in series]
    return {"b": [b for b, _ in series], "tz": [tz for _, tz in series],
            "lat": [c[0] for c in centres], "lon": [c[1] for c in centres]}


async def _matched(conn: AsyncConnection, alert: Alert, series: Sequence[tuple[int, str]] | None) -> set[tuple[int, str]]:
    if alert.geometry is not None:
        params: dict[str, object] = {"g": json.dumps(alert.geometry)}
        sql = _ROUTE_POLY if series is None else _SERIES_POLY
    elif alert.zones:
        params = {"ids": [z.rsplit("/", 1)[-1] for z in alert.zones]}
        sql = _ROUTE_ZONE if series is None else _SERIES_ZONE
    else:
        return set()
    if series is not None:
        params |= _series_params(series)
    return {(int(b), str(tz)) for b, tz in (await conn.execute(text(sql), params)).all()}


async def ensure_zone_geoms(conn: AsyncConnection, client: NwsClient, alerts: Sequence[Alert]) -> int:
    urls = sorted({z for a in alerts if a.geometry is None for z in a.zones if z.startswith(ZONES_PREFIX)})
    if not urls:
        return 0
    have = {str(z) for (z,) in (await conn.execute(text("SELECT zone_id FROM nws_zone_geoms WHERE zone_id = ANY(:ids)"),
                                                       {"ids": [u.rsplit("/", 1)[-1] for u in urls]})).all()}
    added = 0
    for url in urls:
        zone_id = url.rsplit("/", 1)[-1]
        if zone_id in have:
            continue
        geometry = client.zone_geometry(url)
        if geometry is None:
            continue
        await conn.execute(text("INSERT INTO nws_zone_geoms (zone_id, geom) VALUES (:z, ST_GeomFromGeoJSON(:g)::geography) "
                                "ON CONFLICT DO NOTHING"), {"z": zone_id, "g": json.dumps(geometry)})
        added += 1
    return added


async def alert_values(
    conn: AsyncConnection,
    alerts: Sequence[Alert],
    *,
    series: Sequence[tuple[int, str]],
    days: Mapping[str, date],
    match_on_centres: bool,
) -> list[FeedValue]:
    codes: dict[tuple[int, str], set[str]] = {s: set() for s in series}
    for alert in alerts:
        for s in await _matched(conn, alert, series if match_on_centres else None):
            if s in codes and active_on(alert, days[s[1]], s[1]):
                codes[s].update(alert.codes)
    return [FeedValue(b, tz, days[tz], {"codes": sorted(c)}) for (b, tz), c in sorted(codes.items())]


async def mark_coverage(conn: AsyncConnection, days: Iterable[date], scope: Literal["live_all", "archive_polygon_only"]) -> None:
    for day in sorted(set(days)):
        await conn.execute(text(
            "INSERT INTO nws_alert_coverage (date, scope, runs_ok) VALUES (:d, :s, 1) ON CONFLICT (date) DO UPDATE SET "
            "runs_ok = nws_alert_coverage.runs_ok + 1, updated_at = now(), "
            "scope = CASE WHEN EXCLUDED.scope = 'live_all' THEN 'live_all' ELSE nws_alert_coverage.scope END"),
            {"d": day, "s": scope})


async def run_live(
    engine_factory: Callable[[], AsyncEngine] | None = None, client: NwsClient | None = None, *, now: datetime | None = None
) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.pipelines.forecast import route_series

    moment = now or datetime.now(timezone.utc)
    nws = client or NwsClient()
    engine = (engine_factory or ingest_engine)()
    report = ValidationReport(SOURCE_LIVE)
    try:
        async with engine.begin() as conn:
            series = await route_series(conn)
            run_id: uuid.UUID = await start_run(conn, source=SOURCE_LIVE, window_start=moment.date(),
                                                window_end=moment.date(), content_sha256=None)
        try:
            alerts, no_vtec = nws.active()
        except httpx.HTTPError as exc:
            async with engine.begin() as conn:
                await finish_run(conn, run_id, status="failed", report=report, rows_upserted=0,
                                 problems=[f"nws: {type(exc).__name__}"])
            return {"status": "failed"}
        days = {tz: local_date(moment, tz) for _, tz in series}
        async with engine.begin() as conn:
            await ensure_zone_geoms(conn, nws, alerts)
            values = await alert_values(conn, alerts, series=series, days=days, match_on_centres=False)
            out = await write_feed_values(conn, "nws", values)
            await mark_coverage(conn, days.values(), "live_all")
            report.rows_in = report.accepted = len(alerts)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=out["applied"],
                             problems=[f"no_vtec={no_vtec}", f"staged={out['staged']}"])
    finally:
        await engine.dispose()
    return {"status": "ok", "alerts": len(alerts), "series": len(series)}


async def run_archive(
    engine_factory: Callable[[], AsyncEngine], client: NwsClient, *, start: date, end: date, today: date
) -> dict[str, object]:
    first, last = max(start, ARCHIVE_START), min(end, today - timedelta(days=1))
    engine = engine_factory()
    done = skipped = failed = 0
    try:
        async with engine.connect() as conn:
            days = [d for (d,) in (await conn.execute(text(
                "SELECT DISTINCT date FROM accident_conditions WHERE date BETWEEN :s AND :e ORDER BY 1"),
                {"s": first, "e": last})).all()]
        for day in days:
            report = ValidationReport(SOURCE_ARCHIVE)
            async with engine.begin() as conn:
                series = [(int(b), str(tz)) for b, tz in (await conn.execute(text(
                    "SELECT DISTINCT grid_bucket, tz FROM accident_conditions WHERE date = :d AND tz IS NOT NULL ORDER BY 1, 2"),
                    {"d": day})).all()]
                sha = sha256_rows(series)
                if await find_completed(conn, source=SOURCE_ARCHIVE, window_start=day, window_end=day, content_sha256=sha):
                    skipped += 1
                    continue
                run_id = await start_run(conn, source=SOURCE_ARCHIVE, window_start=day, window_end=day, content_sha256=sha)
            # 04:00Z on the day to 11:00Z the next day spans the local day in every US zone (EDT to HST).
            lo = datetime.combine(day, time(4), timezone.utc)
            try:
                alerts = client.iem_polygons(lo, lo + timedelta(hours=31))
            except httpx.HTTPError as exc:
                async with engine.begin() as conn:
                    await finish_run(conn, run_id, status="failed", report=report, rows_upserted=0,
                                     problems=[f"iem: {type(exc).__name__}"])
                failed += 1
                continue
            async with engine.begin() as conn:
                values = await alert_values(conn, alerts, series=series, days={tz: day for _, tz in series}, match_on_centres=True)
                out = await write_feed_values(conn, "nws", values)
                await mark_coverage(conn, [day], "archive_polygon_only")
                report.rows_in = report.accepted = len(alerts)
                await finish_run(conn, run_id, status="ok", report=report, rows_upserted=out["applied"],
                                 problems=[f"staged={out['staged']}"] if out["staged"] else None)
            done += 1
    finally:
        await engine.dispose()
    return {"status": "ok" if not failed else "failed", "done": done, "skipped": skipped, "failed": failed,
            "not_collected_before": ARCHIVE_START.isoformat()}


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    if args.step == "live":
        return await run_live()
    return await run_archive(ingest_engine, NwsClient(), start=args.start, end=args.end, today=utc_today())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="step", required=True)
    sub.add_parser("live")
    archive = sub.add_parser("archive")
    archive.add_argument("--start", type=date.fromisoformat, required=True)
    archive.add_argument("--end", type=date.fromisoformat, required=True)
    result = asyncio.run(_main(parser.parse_args()))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["status"] == "ok" else 1)
```

Append `"app.pipelines.nws"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_nws.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/nws.py backend/tests/test_nws.py backend/tests/fixtures/nws_active.json backend/tests/fixtures/iem_sbw.json backend/pyproject.toml && git commit -m "feat(pipelines): NWS alerts and IEM polygon archive as VTEC codes per series local day"`

---

### Task 6: AirNow hourly AQI

**Files:**
- Create: `backend/app/pipelines/airnow.py`, `backend/tests/test_airnow.py`, `backend/tests/fixtures/airnow_hourly.dat`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `FILES = "https://files.airnowtech.org/airnow"`, `SOURCE = "airnow"`, `SITE_RADIUS_KM = 50.0`, `AQI_COLUMNS`, `hour_url(ts: datetime) -> str`, `@dataclass(frozen=True) Site(lat, lon, aqi: int | None)`, `parse_hourly(body: str, report: ValidationReport | None = None) -> list[Site]` (AQI = max of the `*_AQI` columns present; rows with bad coordinates are quarantined `bad_coordinates`), `bucket_aqi(points: Mapping[K, tuple[float, float]], sites) -> dict[K, int]`, `async run_airnow(engine_factory, client: httpx.Client, *, now: datetime) -> dict[str, object]` (last complete UTC hour, every run logged, values on each series' local day of that hour via `write_feed_values("aqi", …)`).

- [ ] **Step 1: Record the fixture (agent)** — `curl -s "https://files.airnowtech.org/airnow/2026/20260927/HourlyAQObs_2026092718.dat" | head -5 > backend/tests/fixtures/airnow_hourly.dat`; confirm the header names (`Latitude`, `Longitude`, `OZONE_AQI`, `PM25_AQI`, `PM10_AQI`) and update `AQI_COLUMNS` if they differ; confirm `hour_url` matches the real path.

- [ ] **Step 2: Failing tests** — `backend/tests/test_airnow.py`:

```python
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.airnow import Site, bucket_aqi, hour_url, parse_hourly, run_airnow
from app.pipelines.grid import grid_bucket
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url

TEXT = ('"AQSID","SiteName","Latitude","Longitude","OZONE_AQI","PM25_AQI","PM10_AQI"\n'
        '"1","A","40.01","-105.31","35","61",""\n"2","B","41.5","-105.3","","",""\n"3","C","x","-105.3","10","",""\n')
DENVER = "America/Denver"
FIX = Path(__file__).parent / "fixtures"


def test_parse_takes_the_max_available_aqi_keeps_missing_as_none_and_quarantines_bad_rows():
    report = ValidationReport("airnow")
    assert parse_hourly(TEXT, report) == [Site(40.01, -105.31, 61), Site(41.5, -105.3, None)]
    assert report.quarantined["bad_coordinates"] == 1


def test_recorded_file_parses():
    assert parse_hourly((FIX / "airnow_hourly.dat").read_text())


def test_bucket_aqi_uses_sites_within_50_km():
    assert bucket_aqi({1: (40.0, -105.3), 2: (38.0, -105.3)}, parse_hourly(TEXT)) == {1: 61}


def test_hour_url():
    assert hour_url(datetime(2026, 9, 27, 18, tzinfo=timezone.utc)).endswith("/2026/20260927/HourlyAQObs_2026092718.dat")


def _seed(b: int, day: str) -> str:
    return (f"INSERT INTO feature_points (point_key, lat, lon, grid_bucket, tz, h3_r5, h3_r7, feature_version) "
            f"VALUES ('40.00000:-105.30000', 40.0, -105.3, {b}, '{DENVER}', 'x', 'y', 'f-v1');"
            f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, is_forecast, source) "
            f"VALUES ({b}, '{DENVER}', '{day}', 'forecast', true, 'open_meteo_forecast');")


async def _run(url: str, client: httpx.Client, now: datetime) -> tuple[dict[str, object], list[tuple[object, ...]]]:
    result = await run_airnow(lambda: create_async_engine(url), client, now=now)
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            log = (await conn.execute(text("SELECT status FROM source_ingest_log WHERE source = 'airnow'"))).all()
            aqi = (await conn.execute(text("SELECT date::text, aqi FROM cell_daily_conditions"))).all()
        return result, [tuple(r) for r in log] + [tuple(r) for r in aqi]
    finally:
        await engine.dispose()


@requires_pg
def test_missing_file_is_logged_failed_and_leaves_aqi_null():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with migrated_db(seed_sql=_seed(grid_bucket(40.0, -105.3), "2026-09-27")) as name:
        result, rows = asyncio.run(_run(sa_url(name), client, datetime(2026, 9, 27, 19, 5, tzinfo=timezone.utc)))
    assert result == {"status": "missing_file", "hour": "2026092718"}
    assert rows == [("failed",), ("2026-09-27", None)]


@requires_pg
def test_hour_lands_on_the_series_local_day():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=TEXT)))
    # 03:00Z on 09-28 is the evening of 09-27 in Denver.
    with migrated_db(seed_sql=_seed(grid_bucket(40.0, -105.3), "2026-09-27")) as name:
        result, rows = asyncio.run(_run(sa_url(name), client, datetime(2026, 9, 28, 4, 5, tzinfo=timezone.utc)))
    assert result["status"] == "ok"
    assert rows == [("ok",), ("2026-09-27", 61)]
```

- [ ] **Step 3: Implement** `backend/app/pipelines/airnow.py`:

```python
"""AirNow hourly observation files (public) -> daily maximum AQI per route series, on the
series' local day of the observation hour. Every run is logged; a missing file is a failed
run and leaves AQI NULL."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypeVar

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.pipelines.feed_values import FeedValue, write_feed_values
from app.pipelines.geo import haversine_km
from app.pipelines.ingest_log import finish_run, start_run, write_quarantine
from app.pipelines.localday import local_date
from app.pipelines.validate import ValidationReport, coord_problem

FILES = "https://files.airnowtech.org/airnow"
SOURCE = "airnow"
SITE_RADIUS_KM = 50.0
AQI_COLUMNS = ("OZONE_AQI", "PM25_AQI", "PM10_AQI", "NO2_AQI", "CO_AQI", "SO2_AQI")

K = TypeVar("K")


@dataclass(frozen=True)
class Site:
    lat: float
    lon: float
    aqi: int | None


def hour_url(ts: datetime) -> str:
    return f"{FILES}/{ts:%Y}/{ts:%Y%m%d}/HourlyAQObs_{ts:%Y%m%d%H}.dat"


def parse_hourly(body: str, report: ValidationReport | None = None) -> list[Site]:
    sites = []
    for i, row in enumerate(csv.DictReader(io.StringIO(body))):
        try:
            lat, lon = float(row["Latitude"]), float(row["Longitude"])
        except (KeyError, TypeError, ValueError):
            lat = lon = float("nan")
        if lat != lat or coord_problem(lat, lon) is not None:
            if report is not None:
                report.quarantine(f"row:{i}", "bad_coordinates")
            continue
        values = [int(float(row[c])) for c in AQI_COLUMNS if (row.get(c) or "").strip() not in ("", "-999")]
        values = [v for v in values if 0 <= v <= 999]
        if report is not None:
            report.accept()
        sites.append(Site(lat, lon, max(values) if values else None))
    return sites


def bucket_aqi(points: Mapping[K, tuple[float, float]], sites: Sequence[Site]) -> dict[K, int]:
    out: dict[K, int] = {}
    for key, (lat, lon) in points.items():
        near = [s.aqi for s in sites if s.aqi is not None and haversine_km(lat, lon, s.lat, s.lon) <= SITE_RADIUS_KM]
        if near:
            out[key] = max(near)
    return out


async def run_airnow(engine_factory: Callable[[], AsyncEngine], client: httpx.Client, *, now: datetime) -> dict[str, object]:
    hour = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    label = f"{hour:%Y%m%d%H}"
    engine = engine_factory()
    report = ValidationReport(SOURCE)
    try:
        async with engine.begin() as conn:
            run_id: uuid.UUID = await start_run(conn, source=SOURCE, window_start=hour.date(), window_end=hour.date(),
                                                content_sha256=None)
        try:
            response: httpx.Response | None = client.get(hour_url(hour))
        except httpx.HTTPError:
            response = None
        if response is None or response.status_code != 200:
            async with engine.begin() as conn:
                await finish_run(conn, run_id, status="failed", report=report, rows_upserted=0,
                                 problems=[f"missing_file:{label}"])
            return {"status": "missing_file", "hour": label}
        sites = parse_hourly(response.text, report)
        async with engine.begin() as conn:
            points = {(int(b), str(tz)): (float(la), float(lo)) for b, tz, la, lo in (await conn.execute(text(
                "SELECT grid_bucket, tz, avg(lat), avg(lon) FROM feature_points WHERE tz IS NOT NULL GROUP BY grid_bucket, tz"))).all()}
            values = bucket_aqi(points, sites)
            out = await write_feed_values(conn, "aqi", [FeedValue(b, tz, local_date(hour, tz), {"aqi": v})
                                                        for (b, tz), v in values.items()])
            await write_quarantine(conn, run_id, report)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=out["applied"],
                             problems=[f"staged={out['staged']}"] if out["staged"] else None)
    finally:
        await engine.dispose()
    return {"status": "ok", "hour": label, "series": len(values)}
```

The Celery wrapper (Task 8) pings `/fail` on `missing_file`, so repeated outages alert, and the `failed` run log is what `/health/data` reads. Append `"app.pipelines.airnow"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_airnow.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/airnow.py backend/tests/test_airnow.py backend/tests/fixtures/airnow_hourly.dat backend/pyproject.toml && git commit -m "feat(pipelines): AirNow hourly AQI to local-day max per route series, every run logged"`

---

### Task 7: `GET /health/data`

**Files:**
- Create: `backend/app/data_health.py`, `backend/tests/test_data_health.py`
- Modify: `backend/app/main.py`, `backend/pyproject.toml`

**Interfaces:**
- Produces: `THRESHOLDS: dict[str, timedelta]` = `open_meteo_forecast` 26 h, `nws_alerts` 2 h, `airnow` 3 h, `glm_live` 30 min, `glm_daily` 50 h, `snotel` 50 h, `nldn_monthly` 40 d, `openbeta_weekly` 9 d (the cadence table's "stale after" column); `evaluate(last_ok: Mapping[str, datetime | None], now: datetime) -> dict[str, dict[str, object]]` (`status` ∈ `ok|stale|never_run`); `async read_last_ok(db: AsyncSession) -> dict[str, datetime | None]` (every source from `source_ingest_log`, last `ok` `finished_at`); route `GET /health/data` → 200 or 503 with the per-source JSON (no counts of personal data, no URLs).

Freshness comes only from the run log: a feed that ran and found nothing (no lightning, no alerts) is fresh; a feed that did not run is stale. Sources whose job lands in PR 2b-3b report `never_run` until then, which does not page.

- [ ] **Step 1: Failing tests** — `backend/tests/test_data_health.py`:

```python
import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.data_health import THRESHOLDS, evaluate
from app.db.session import get_db
from app.main import app
from tests.pgtest import migrated_db, requires_pg, sa_url

NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def test_never_run_is_not_stale():
    result = evaluate({"open_meteo_forecast": None, "nws_alerts": NOW - timedelta(minutes=30)}, NOW)
    assert result["open_meteo_forecast"]["status"] == "never_run"
    assert set(result) == set(THRESHOLDS)
    assert all(v["status"] != "stale" for v in result.values())


def test_stale_after_threshold():
    last = {"open_meteo_forecast": NOW - timedelta(hours=27), "nws_alerts": NOW, "glm_live": NOW - timedelta(minutes=31),
            "openbeta_weekly": NOW - timedelta(days=8), "airnow": NOW - timedelta(hours=2)}
    result = {k: v["status"] for k, v in evaluate(last, NOW).items()}
    assert result["open_meteo_forecast"] == "stale" and result["glm_live"] == "stale"
    assert result["nws_alerts"] == "ok" and result["openbeta_weekly"] == "ok" and result["airnow"] == "ok"
    assert result["snotel"] == "never_run"


@requires_pg
def test_route_is_200_when_never_run_and_503_once_stale():
    async def go(url: str) -> tuple[int, int, str]:
        engine = create_async_engine(url)

        async def override() -> AsyncIterator[AsyncSession]:
            async with AsyncSession(engine) as session:
                yield session

        app.dependency_overrides[get_db] = override
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                first = await client.get("/health/data")
                async with engine.begin() as conn:
                    await conn.execute(text(
                        "INSERT INTO source_ingest_log (run_id, source, status, finished_at) "
                        "VALUES (gen_random_uuid(), 'open_meteo_forecast', 'ok', now() - interval '27 hours')"))
                second = await client.get("/health/data")
            return first.status_code, second.status_code, second.json()["sources"]["open_meteo_forecast"]["status"]
        finally:
            app.dependency_overrides.pop(get_db, None)
            await engine.dispose()

    with migrated_db() as name:
        assert asyncio.run(go(sa_url(name))) == (200, 503, "stale")
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_data_health.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** `backend/app/data_health.py`:

```python
"""Data freshness for GET /health/data (spec monitoring). Freshness is the last ok run in
source_ingest_log for every feed, so a quiet feed (no lightning, no alerts) stays fresh and a
dead one goes stale. A source that has never run is reported, not stale, so a fresh
deployment does not page; once it has run, missing its threshold returns 503."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

THRESHOLDS: dict[str, timedelta] = {
    "open_meteo_forecast": timedelta(hours=26),
    "nws_alerts": timedelta(hours=2),
    "airnow": timedelta(hours=3),
    "glm_live": timedelta(minutes=30),
    "glm_daily": timedelta(hours=50),
    "snotel": timedelta(hours=50),
    "nldn_monthly": timedelta(days=40),
    "openbeta_weekly": timedelta(days=9),
}


def evaluate(last_ok: Mapping[str, datetime | None], now: datetime) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for source, limit in THRESHOLDS.items():
        seen = last_ok.get(source)
        if seen is None:
            out[source] = {"status": "never_run"}
        else:
            age = now - seen
            out[source] = {"status": "stale" if age > limit else "ok", "age_minutes": int(age.total_seconds() // 60)}
    return out


async def read_last_ok(db: AsyncSession) -> dict[str, datetime | None]:
    rows = (await db.execute(text(
        "SELECT source, max(finished_at) FROM source_ingest_log WHERE status = 'ok' AND source = ANY(:s) GROUP BY source"),
        {"s": list(THRESHOLDS)})).all()
    last: dict[str, datetime | None] = {s: None for s in THRESHOLDS}
    last |= {str(s): t for s, t in rows}
    return last
```

In `backend/app/main.py`, beside `/health/worker`:

```python
@app.get("/health/data")
async def data_health(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    report = evaluate(await read_last_ok(db), datetime.now(timezone.utc))
    stale = any(v["status"] == "stale" for v in report.values())
    return JSONResponse(status_code=503 if stale else 200, content={"sources": report})
```

with imports `from fastapi import Depends`, `from fastapi.responses import JSONResponse`, `from sqlalchemy.ext.asyncio import AsyncSession`, `from app.data_health import evaluate, read_last_ok`, `from app.db.session import get_db` (merge into existing import lines; `datetime`/`timezone` are already imported for `/health/worker`). Append `"app.data_health"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_data_health.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/data_health.py backend/app/main.py backend/tests/test_data_health.py backend/pyproject.toml && git commit -m "feat(api): /health/data freshness from the run log, per-source thresholds"`

---

### Task 8: Celery wiring on a dedicated ingest worker, batch workflows, PR 2b-3a docs

**Files:**
- Create: `backend/app/tasks/data_feeds.py`, `backend/tests/test_data_feeds_tasks.py`, `backend/railway-ingest.toml`, `.github/workflows/data-daily.yml`, `.github/workflows/data-weekly.yml`
- Modify: `backend/app/celery_app.py`, `docker-compose.yml`, `scripts/check_compose_matches_railway.py`, `backend/pyproject.toml`, `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`

**Interfaces:**
- Produces: Celery tasks `app.tasks.data_feeds.fetch_forecast`, `.fetch_alerts`, `.fetch_airnow` (and `.glm_live` in Task 10), each `job_ping(slug, "/start")` → run → `job_ping(slug)` or `"/fail"` (also `/fail` when the job returns a status other than `ok`); `celery_app.conf.task_routes = {"app.tasks.data_feeds.*": {"queue": "ingest"}}`; beat entries `forecast-nightly` (01:00 UTC, `expires` 3600), `nws-alerts` (hourly at :05, `expires` 1800), `airnow` (hourly at :35, `expires` 1800); Railway service `ingest-worker` (`celery -A app.celery_app worker -Q ingest --loglevel=info --concurrency=1 -E`), the only service holding `INGEST_DATABASE_URL` (D14).

- [ ] **Step 1: Failing test** — `backend/tests/test_data_feeds_tasks.py`:

```python
import pytest
from celery.schedules import crontab

from app.celery_app import celery_app


def test_beat_schedules_the_feeds_with_expiry():
    schedule = celery_app.conf.beat_schedule
    assert schedule["forecast-nightly"]["schedule"] == crontab(minute=0, hour=1)
    for key in ("forecast-nightly", "nws-alerts", "airnow"):
        assert schedule[key]["options"]["expires"] <= 3600
        assert schedule[key]["task"].startswith("app.tasks.data_feeds.")


def test_feed_tasks_go_to_the_ingest_queue_only():
    assert celery_app.conf.task_routes["app.tasks.data_feeds.*"] == {"queue": "ingest"}


def test_tasks_are_registered():
    import app.tasks.data_feeds  # noqa: F401

    for name in ("fetch_forecast", "fetch_alerts", "fetch_airnow"):
        assert f"app.tasks.data_feeds.{name}" in celery_app.tasks


def test_non_ok_status_pings_fail(monkeypatch: pytest.MonkeyPatch):
    import app.tasks.data_feeds as feeds

    pings: list[tuple[str, str]] = []
    monkeypatch.setattr(feeds, "job_ping", lambda slug, suffix="": pings.append((slug, suffix)) or True)

    async def job() -> dict[str, object]:
        return {"status": "missing_file"}

    assert feeds._run("airnow", job) == {"status": "missing_file"}
    assert pings == [("airnow", "/start"), ("airnow", "/fail")]
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_data_feeds_tasks.py -q` → FAIL.

- [ ] **Step 3: Implement** `backend/app/tasks/data_feeds.py`:

```python
"""Time-critical data feeds (P2-6), routed to the `ingest` queue so only the ingest-worker
service (the one holding INGEST_DATABASE_URL, D14) runs them. Each task pings its
healthchecks.io slug; a failure re-raises after the /fail ping."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from app.celery_app import celery_app
from app.pipelines.jobping import job_ping


def _run(slug: str, job: Callable[[], Awaitable[dict[str, object]]]) -> dict[str, object]:
    job_ping(slug, "/start")
    try:
        result = asyncio.run(job())
    except Exception:
        job_ping(slug, "/fail")
        raise
    job_ping(slug, "" if result.get("status", "ok") == "ok" else "/fail")
    return result


@celery_app.task(name="app.tasks.data_feeds.fetch_forecast")
def fetch_forecast() -> dict[str, object]:
    from app.config import settings
    from app.pipelines.db import ingest_engine
    from app.pipelines.forecast import run_forecast
    from app.pipelines.open_meteo import ForecastClient

    return _run("forecast-nightly", lambda: run_forecast(
        ingest_engine, ForecastClient(settings.OPEN_METEO_API_KEY), now=datetime.now(timezone.utc)))


@celery_app.task(name="app.tasks.data_feeds.fetch_alerts")
def fetch_alerts() -> dict[str, object]:
    from app.pipelines.nws import run_live

    return _run("nws-alerts", run_live)


@celery_app.task(name="app.tasks.data_feeds.fetch_airnow")
def fetch_airnow() -> dict[str, object]:
    import httpx

    from app.pipelines.airnow import run_airnow
    from app.pipelines.db import ingest_engine

    return _run("airnow", lambda: run_airnow(ingest_engine, httpx.Client(timeout=60.0), now=datetime.now(timezone.utc)))
```

In `celery_app.py`: add `"app.tasks.data_feeds"` to `include`, set `celery_app.conf.task_routes = {"app.tasks.data_feeds.*": {"queue": "ingest"}}` next to the other `conf` settings, and add to the beat schedule:

```python
    # Before the 02:00 scoring run; forecast and stopgap rows never replace ERA5 rows.
    "forecast-nightly": {
        "task": "app.tasks.data_feeds.fetch_forecast",
        "schedule": crontab(minute=0, hour=1),
        "options": {"expires": 3600},
    },
    "nws-alerts": {"task": "app.tasks.data_feeds.fetch_alerts", "schedule": crontab(minute=5), "options": {"expires": 1800}},
    "airnow": {"task": "app.tasks.data_feeds.fetch_airnow", "schedule": crontab(minute=35), "options": {"expires": 1800}},
```

Add `"app.tasks.data_feeds"` to the celery-adjacent mypy block.

`backend/railway-ingest.toml`:

```toml
# Railway: Celery worker for data feeds only (queue `ingest`). The only service with
# INGEST_DATABASE_URL (D14); the general worker never consumes this queue.

[build]
builder = "dockerfile"
dockerfilePath = "Dockerfile"

[deploy]
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 10
numReplicas = 1
startCommand = "celery -A app.celery_app worker -Q ingest --loglevel=info --concurrency=1 -E"
```

`docker-compose.yml`, after the `worker` service:

```yaml
  ingest-worker:
    <<: *backend
    command: celery -A app.celery_app worker -Q ingest --loglevel=info --concurrency=1 -E
```

`scripts/check_compose_matches_railway.py`: add `"ingest-worker": "backend/railway-ingest.toml",` to `RAILWAY_CONFIGS`. The existing `worker` start command has no `-Q`, so it consumes only the default `celery` queue and never runs a feed.

`.github/workflows/data-daily.yml`:

```yaml
name: data-daily

on:
  schedule:
    - cron: "10 9 * * *"   # daily 09:10 UTC
  workflow_dispatch:

permissions:
  contents: read
  issues: write

jobs:
  daily:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    defaults:
      run:
        working-directory: backend
    env:
      # Settings requires DATABASE_URL; the job never opens it (D14).
      DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      INGEST_DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      HEALTHCHECKS_PING_KEY: ${{ secrets.HEALTHCHECKS_PING_KEY }}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          version: "0.11.3"
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: backend/uv.lock
      - run: uv sync --frozen --group pipelines
      - name: SNOTEL (series local yesterday)
        run: uv run python -m app.pipelines.snotel
      - name: Open an issue on failure
        if: failure()
        working-directory: .
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh issue create --title "data-daily failed ($(date -u +%F))" --body "Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

`.github/workflows/data-weekly.yml`:

```yaml
name: data-weekly

on:
  schedule:
    - cron: "20 7 * * 2"   # Tuesdays 07:20 UTC
  workflow_dispatch:

permissions:
  contents: read
  issues: write

jobs:
  weekly:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    defaults:
      run:
        working-directory: backend
    env:
      DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      INGEST_DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      HEALTHCHECKS_PING_KEY: ${{ secrets.HEALTHCHECKS_PING_KEY }}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          version: "0.11.3"
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: backend/uv.lock
      - run: uv sync --frozen --group pipelines
      - name: Tick quarantine re-evaluation
        run: uv run python -m app.pipelines.mp_ticks_quarantine
      - name: Public tick counts
        run: uv run python -m app.pipelines.mp_tick_counts
      - name: Open an issue on failure
        if: failure()
        working-directory: .
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh issue create --title "data-weekly failed ($(date -u +%F))" --body "Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

(ERA5 archive fetching and the 3-year ERA5 prune are not in any workflow: both run only inside plan 3's January Professional window via `era5_window`.)

Docs: CHANGELOG.md gets one entry headed `Phase 2b live feeds (PR 2b-3a)`, dated with the commit day from `date -u +%F`, listing: forecast with flagged `past_days` stopgap, pending-safe feed values, SNOTEL, NWS VTEC alerts and archive, AirNow, `/health/data`, `ingest-worker`. DEPLOYMENT.md: new `ingest-worker` service (`railway-ingest.toml`, env `INGEST_DATABASE_URL`, `HEALTHCHECKS_PING_KEY`, `OPEN_METEO_API_KEY` — the Standard plan key; the general `worker` gets none of these), the `ingest` queue, beat entries and their slugs (`forecast-nightly` 24 h / 2 h grace, `nws-alerts` 1 h / 1 h, `airnow` 1 h / 2 h), `/health/data`, the two workflows and the cadence table from this plan. CLAUDE.md: Service topology lists `ingest-worker` and `/health/data`; commands for the new jobs.

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd .. && python scripts/check_compose_matches_railway.py` → green.
- [ ] **Step 5: Commit** — `git add backend/app/tasks/data_feeds.py backend/app/celery_app.py backend/tests/test_data_feeds_tasks.py backend/railway-ingest.toml docker-compose.yml scripts/check_compose_matches_railway.py .github/workflows/data-daily.yml .github/workflows/data-weekly.yml backend/pyproject.toml CHANGELOG.md CLAUDE.md DEPLOYMENT.md && git commit -m "feat(ops): ingest-worker service and queue for feeds; daily and weekly data workflows"`

---

# PR 2b-3b — `feat/p2b-lightning`

### Task 9: GLM listing, parsing and the satellite rule

**Files:**
- Create: `backend/app/pipelines/glm.py`, `backend/tests/test_glm.py`
- Modify: `backend/pyproject.toml`, `backend/uv.lock`

**Interfaces:**
- Produces: `Satellite = Literal["east", "west"]`, `PREFIX = "GLM-L2-LCFA"`, `GLM_START = date(2018, 2, 13)`, `WEST_START = date(2023, 1, 4)`, `EAST_SWITCH = date(2025, 4, 7)`, `MAX_LAT = 54.0`, `WEST_OF_LON = -105.0`, `WEST_FOV_MIN_LON = -180.0`, `EAST_FOV_LON = (-135.0, -20.0)`, `S3 = {"east_history": "noaa-goes16", "east": "noaa-goes19", "west": "noaa-goes18"}`, `s3_bucket(satellite, day) -> str`, `satellite_for(lat, lon, day) -> Satellite | None`, `list_keys(client, bucket, hour) -> list[str]` (S3 ListObjectsV2 XML, paginated), `file_start(key) -> datetime`, `read_flashes(data: bytes) -> tuple[NDArray[np.float64], NDArray[np.float64]]` (lat, lon of good-quality flashes), `floor_hour(ts) -> datetime`.

- [ ] **Step 1: Add the dependency** — `cd backend && uv add 'h5py>=3.11,<4'` (main dependency: the ingest worker parses GLM files).

- [ ] **Step 2: Failing tests** — `backend/tests/test_glm.py`:

```python
import asyncio
import io
from datetime import date, datetime, timezone

import h5py
import httpx
import numpy as np
import pytest

from app.pipelines.glm import file_start, list_keys, read_flashes, s3_bucket, satellite_for
from tests.pgtest import migrated_db, pg_url, requires_pg
from tests.test_migration_0007 import _fetch

XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<IsTruncated>false</IsTruncated>
<Contents><Key>GLM-L2-LCFA/2026/271/14/OR_GLM-L2-LCFA_G19_s20262711400000_e20262711400200_c20262711400227.nc</Key></Contents>
</ListBucketResult>"""


def _h5(lat, lon, quality, packed=False) -> bytes:
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        if packed:
            f.create_dataset("flash_lat", data=np.round(np.array(lat) / 0.00203128).astype("int16"))
            f["flash_lat"].attrs["scale_factor"] = np.float32(0.00203128)
            f["flash_lat"].attrs["add_offset"] = np.float32(0.0)
            f.create_dataset("flash_lon", data=np.round(np.array(lon) / 0.00203128).astype("int16"))
            f["flash_lon"].attrs["scale_factor"] = np.float32(0.00203128)
            f["flash_lon"].attrs["add_offset"] = np.float32(0.0)
        else:
            f.create_dataset("flash_lat", data=np.array(lat, dtype="float32"))
            f.create_dataset("flash_lon", data=np.array(lon, dtype="float32"))
        f.create_dataset("flash_quality_flag", data=np.array(quality, dtype="int16"))
    return buf.getvalue()


def test_list_keys_parses_s3_xml():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=XML)))
    keys = list_keys(client, "noaa-goes19", datetime(2026, 9, 28, 14, tzinfo=timezone.utc))
    assert keys == ["GLM-L2-LCFA/2026/271/14/OR_GLM-L2-LCFA_G19_s20262711400000_e20262711400200_c20262711400227.nc"]
    assert file_start(keys[0]) == datetime(2026, 9, 28, 14, 0, 0, tzinfo=timezone.utc)


def test_quality_filter():
    lat, lon = read_flashes(_h5([40.0, 41.0], [-105.0, -106.0], [0, 1]))
    assert lat.tolist() == [40.0] and lon.tolist() == [-105.0]


def test_packed_coordinates_are_unpacked():
    lat, lon = read_flashes(_h5([40.0], [-60.0], [0], packed=True))
    assert abs(lat[0] - 40.0) < 0.01 and abs(lon[0] + 60.0) < 0.01


CASES = [
    ((40.0, -120.0, date(2026, 9, 28)), "west"),
    ((40.0, -80.0, date(2026, 9, 28)), "east"),
    ((40.0, -120.0, date(2020, 7, 1)), "east"),
    ((21.3, -157.8, date(2020, 7, 1)), None),
    ((21.3, -157.8, date(2024, 7, 1)), "west"),
    ((52.0, 175.0, date(2026, 9, 28)), None),
    ((60.0, -150.0, date(2026, 9, 28)), None),
    ((40.0, -80.0, date(2018, 2, 12)), None),
    ((40.0, -80.0, date(2018, 2, 13)), "east"),
]


@pytest.mark.parametrize(("args", "expected"), CASES)
def test_satellite_rules(args, expected):
    assert satellite_for(*args) == expected


def test_east_history_uses_goes16_before_the_switch():
    assert s3_bucket("east", date(2025, 4, 6)) == "noaa-goes16"
    assert s3_bucket("east", date(2025, 4, 7)) == "noaa-goes19"
    assert s3_bucket("west", date(2024, 7, 1)) == "noaa-goes18"


@requires_pg
def test_sql_satellite_rule_matches_python():
    values = ", ".join(f"({la}, {lo}, DATE '{d.isoformat()}')" for (la, lo, d), _ in CASES)
    with migrated_db() as name:
        rows = asyncio.run(_fetch(pg_url(name), f"SELECT glm_satellite(la, lo, d) FROM (VALUES {values}) AS v(la, lo, d)"))
    assert [r[0] for r in rows] == [expected for _, expected in CASES]
```

- [ ] **Step 3: Implement** `backend/app/pipelines/glm.py`:

```python
"""GOES GLM L2 LCFA (NOAA, public on AWS): S3 listing over plain HTTPS (XML, not HTML), HDF5
parsing of flash centroids (good-quality flashes only; packed int16 coordinates unpacked with
their scale_factor/add_offset), and the single satellite rule every GLM consumer uses. The
same rule is the SQL function glm_satellite (0013); tests keep them equal."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Literal

import h5py
import httpx
import numpy as np
from numpy.typing import NDArray

Satellite = Literal["east", "west"]
PREFIX = "GLM-L2-LCFA"
GLM_START = date(2018, 2, 13)
WEST_START = date(2023, 1, 4)
EAST_SWITCH = date(2025, 4, 7)
MAX_LAT = 54.0
WEST_OF_LON = -105.0
WEST_FOV_MIN_LON = -180.0
EAST_FOV_LON = (-135.0, -20.0)
S3 = {"east_history": "noaa-goes16", "east": "noaa-goes19", "west": "noaa-goes18"}
_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
_START = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})\d_")


def s3_bucket(satellite: Satellite, day: date) -> str:
    if satellite == "west":
        return S3["west"]
    return S3["east"] if day >= EAST_SWITCH else S3["east_history"]


def satellite_for(lat: float, lon: float, day: date) -> Satellite | None:
    if lat < 0 or lat > MAX_LAT or day < GLM_START:
        return None
    if WEST_FOV_MIN_LON <= lon < WEST_OF_LON and day >= WEST_START:
        return "west"
    if EAST_FOV_LON[0] <= lon <= EAST_FOV_LON[1]:
        return "east"
    return None


def floor_hour(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def list_keys(client: httpx.Client, bucket: str, hour: datetime) -> list[str]:
    prefix = f"{PREFIX}/{hour:%Y}/{hour.timetuple().tm_yday:03d}/{hour:%H}/"
    keys: list[str] = []
    token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": prefix} | ({"continuation-token": token} if token else {})
        response = client.get(f"https://{bucket}.s3.amazonaws.com/", params=params)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        keys += [k.text for k in root.findall("s3:Contents/s3:Key", _NS) if k.text]
        if root.findtext("s3:IsTruncated", default="false", namespaces=_NS) != "true":
            return keys
        token = root.findtext("s3:NextContinuationToken", namespaces=_NS)


def file_start(key: str) -> datetime:
    m = _START.search(key)
    if m is None:
        raise ValueError(f"not a GLM file name: {key}")
    year, doy, hh, mm, ss = (int(g) for g in m.groups())
    return datetime.strptime(f"{year} {doy} {hh} {mm} {ss}", "%Y %j %H %M %S").replace(tzinfo=timezone.utc)


def _unpack(ds: h5py.Dataset) -> NDArray[np.float64]:
    raw = ds[()]
    if str(ds.attrs.get("_Unsigned", b"false")).lower() in ("true", "b'true'"):
        raw = raw.view(np.dtype(raw.dtype.str.replace("i", "u")))
    values = np.asarray(raw, dtype=np.float64)
    scale, offset = ds.attrs.get("scale_factor"), ds.attrs.get("add_offset")
    if scale is not None:
        values = values * float(np.asarray(scale).ravel()[0]) + (float(np.asarray(offset).ravel()[0]) if offset is not None else 0.0)
    return values


def read_flashes(data: bytes) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    with h5py.File(io.BytesIO(data), "r") as f:
        lat, lon = _unpack(f["flash_lat"]), _unpack(f["flash_lon"])
        good = np.asarray(f["flash_quality_flag"][()]) == 0 if "flash_quality_flag" in f else np.ones(lat.shape, dtype=bool)
    ok = good & np.isfinite(lat) & np.isfinite(lon)
    return lat[ok], lon[ok]
```

Append `"app.pipelines.glm"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_glm.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/glm.py backend/tests/test_glm.py backend/pyproject.toml backend/uv.lock && git commit -m "feat(pipelines): GLM S3 listing, HDF5 flash parsing, one satellite rule with SQL parity"`

---

### Task 10: GLM live (10 minutes) → `lightning_recent`, banner read

**Files:**
- Modify: `backend/app/pipelines/glm.py`, `backend/tests/test_glm.py`, `backend/app/data_health.py`, `backend/tests/test_data_health.py`, `backend/app/tasks/data_feeds.py`, `backend/app/celery_app.py`, `backend/tests/test_data_feeds_tasks.py`

**Interfaces:**
- Produces (`glm.py`): `SOURCE_LIVE = "glm_live"`, `SLOT = timedelta(minutes=10)`, `WINDOW = timedelta(minutes=30)`, `SETTLE = timedelta(minutes=2)`, `FILE_SECONDS = 20`, `MIN_COMPLETE = 0.95`, `RETENTION = timedelta(hours=24)`, `slot_end(ts) -> datetime`, `count_by_bucket(lat, lon, *, satellite: Satellite, chosen: Mapping[int, Satellite | None]) -> dict[int, int]`, `async run_live(engine_factory, client, *, now: datetime) -> dict[str, object]` (every run logged as `glm_live`; `failed` when a needed satellite's files in `[now − 32 min, now − 2 min)` are under 95% of the expected 90, or on any transport/parse error).
- Produces (`data_health.py`): `async recent_lightning(db, grid_bucket: int, *, now: datetime) -> int | None` — flashes in the last 30 minutes, `0` for a covered quiet bucket, `None` when the bucket has no satellite coverage or the last `ok` `glm_live` run is older than 30 minutes.
- Celery task `app.tasks.data_feeds.glm_live` every 10 minutes (`expires` 540).

- [ ] **Step 1: Failing tests** — append to `test_glm.py`, merging these imports into its top import block (ruff `E402`):

```python
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.data_health import recent_lightning
from app.pipelines.glm import count_by_bucket, run_live
from app.pipelines.grid import bucket_center, grid_bucket
from tests.pgtest import sa_url

LIVE_NOW = datetime(2026, 9, 28, 14, 37, tzinfo=timezone.utc)
EMPTY = _h5([], [], [])


def _key(ts: datetime, sat: str = "G19") -> str:
    stamp = f"{ts:%Y}{ts.timetuple().tm_yday:03d}{ts:%H%M%S}0"
    return f"GLM-L2-LCFA/{ts:%Y}/{ts.timetuple().tm_yday:03d}/{ts:%H}/OR_GLM-L2-LCFA_{sat}_s{stamp}_e{stamp}_c{stamp}.nc"


def _listing(keys: list[str]) -> str:
    body = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
    return ('<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"<IsTruncated>false</IsTruncated>{body}</ListBucketResult>")


def _served(keys: list[str], payload: dict[str, bytes] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            prefix = request.url.params["prefix"]
            return httpx.Response(200, text=_listing([k for k in keys if k.startswith(prefix)]))
        key = request.url.path.lstrip("/")
        return httpx.Response(200, content=(payload or {}).get(key, EMPTY))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_bucket_takes_one_satellite():
    b_west, b_east, b_north = grid_bucket(40.0, -110.0), grid_bucket(40.0, -100.0), grid_bucket(60.0, -150.0)
    chosen = {b: satellite_for(*bucket_center(b), date(2026, 9, 28)) for b in (b_west, b_east, b_north)}
    lat, lon = np.array([40.0, 40.0, 60.0]), np.array([-110.0, -100.0, -150.0])
    assert count_by_bucket(lat, lon, satellite="west", chosen=chosen) == {b_west: 1}
    assert count_by_bucket(lat, lon, satellite="east", chosen=chosen) == {b_east: 1}


def _seed_route() -> str:
    b = grid_bucket(40.0, -80.0)
    return (f"INSERT INTO feature_points (point_key, lat, lon, grid_bucket, tz, h3_r5, h3_r7, feature_version) "
            f"VALUES ('40.00000:-80.00000', 40.0, -80.0, {b}, 'America/New_York', 'x', 'y', 'f-v1');")


async def _live(url: str, client: httpx.Client) -> tuple[dict[str, object], int | None, str]:
    result = await run_live(lambda: create_async_engine(url), client, now=LIVE_NOW)
    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine) as db:
            banner = await recent_lightning(db, grid_bucket(40.0, -80.0), now=LIVE_NOW)
            status = (await db.execute(text("SELECT status FROM source_ingest_log WHERE source = 'glm_live'"))).scalar_one()
        return result, banner, str(status)
    finally:
        await engine.dispose()


@requires_pg
def test_quiet_complete_window_is_ok_and_reads_zero():
    start = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
    keys = [_key(start + timedelta(seconds=20 * i)) for i in range(111)]
    with migrated_db(seed_sql=_seed_route()) as name:
        result, banner, status = asyncio.run(_live(sa_url(name), _served(keys)))
    assert result["status"] == "ok" and status == "ok" and banner == 0


@requires_pg
def test_incomplete_window_fails_and_reads_null():
    start = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
    keys = [_key(start + timedelta(seconds=60 * i)) for i in range(37)]
    with migrated_db(seed_sql=_seed_route()) as name:
        result, banner, status = asyncio.run(_live(sa_url(name), _served(keys)))
    assert result["status"] == "failed" and status == "failed" and banner is None


@requires_pg
def test_flashes_are_counted_into_their_slot():
    start = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
    keys = [_key(start + timedelta(seconds=20 * i)) for i in range(111)]
    payload = {keys[60]: _h5([40.0, 40.02], [-80.0, -80.01], [0, 0])}
    with migrated_db(seed_sql=_seed_route()) as name:
        result, banner, _ = asyncio.run(_live(sa_url(name), _served(keys, payload)))
    assert result["status"] == "ok" and banner == 2
```

(`keys[60]` starts at 14:20:00, inside the last 30 minutes before 14:37; 111 keys cover 14:00:00–14:36:40, 90 of them inside the completeness window 14:05–14:35.)

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_glm.py -q` → FAIL (`count_by_bucket`, `run_live`, `recent_lightning` missing).

- [ ] **Step 3: Implement** — append to `glm.py` (merge the new imports into the module's import block):

```python
import math
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.pipelines.grid import bucket_center, grid_bucket
from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

SOURCE_LIVE = "glm_live"
SLOT = timedelta(minutes=10)
WINDOW = timedelta(minutes=30)
SETTLE = timedelta(minutes=2)
FILE_SECONDS = 20
MIN_COMPLETE = 0.95
RETENTION = timedelta(hours=24)


def slot_end(ts: datetime) -> datetime:
    return ts.replace(minute=ts.minute - ts.minute % 10, second=0, microsecond=0) + SLOT


def count_by_bucket(
    lat: NDArray[np.float64], lon: NDArray[np.float64], *, satellite: Satellite, chosen: Mapping[int, Satellite | None]
) -> dict[int, int]:
    counts: Counter[int] = Counter()
    for la, lo in zip(lat.tolist(), lon.tolist()):
        if not (0.0 <= la <= 90.0 and -180.0 <= lo <= 180.0):
            continue
        b = grid_bucket(la, lo)
        if chosen.get(b) == satellite:
            counts[b] += 1
    return dict(counts)


async def run_live(engine_factory: Callable[[], AsyncEngine], client: httpx.Client, *, now: datetime) -> dict[str, object]:
    day = now.date()
    lo, hi = now - SETTLE - WINDOW, now - SETTLE
    report = ValidationReport(SOURCE_LIVE)
    problems: list[str] = []
    new: list[tuple[str, Satellite, datetime]] = []
    counts: Counter[tuple[int, Satellite, datetime]] = Counter()
    engine = engine_factory()
    try:
        async with engine.begin() as conn:
            buckets = {int(b) for (b,) in (await conn.execute(text("SELECT DISTINCT grid_bucket FROM feature_points"))).all()}
            seen = {str(k) for (k,) in (await conn.execute(text(
                "SELECT s3_key FROM glm_files_seen WHERE file_start > :since"), {"since": now - timedelta(hours=3)})).all()}
            run_id = await start_run(conn, source=SOURCE_LIVE, window_start=day, window_end=day, content_sha256=None)
        chosen = {b: satellite_for(*bucket_center(b), day) for b in buckets}
        needed: list[Satellite] = sorted({s for s in chosen.values() if s is not None})
        for sat in needed:
            bucket = s3_bucket(sat, day)
            try:
                for hour in sorted({floor_hour(lo), floor_hour(now)}):
                    for key in list_keys(client, bucket, hour):
                        start = file_start(key)
                        if key in seen or start < lo - WINDOW or start > now:
                            continue
                        lat, lon = read_flashes(client.get(f"https://{bucket}.s3.amazonaws.com/{key}").raise_for_status().content)
                        for b, n in count_by_bucket(lat, lon, satellite=sat, chosen=chosen).items():
                            counts[(b, sat, slot_end(start))] += n
                        new.append((key, sat, start))
            except (httpx.HTTPError, OSError, KeyError, ValueError) as exc:
                problems.append(f"{sat}: {type(exc).__name__}")
        async with engine.begin() as conn:
            if counts:
                await conn.execute(text(
                    "INSERT INTO lightning_recent (grid_bucket, window_end, flashes, satellite) VALUES (:b, :w, :n, :s) "
                    "ON CONFLICT (grid_bucket, window_end) DO UPDATE SET flashes = lightning_recent.flashes + EXCLUDED.flashes"),
                    [{"b": b, "w": w, "n": n, "s": s} for (b, s, w), n in counts.items()])
            if new:
                await conn.execute(text(
                    "INSERT INTO glm_files_seen (s3_key, satellite, file_start) VALUES (:k, :s, :t) ON CONFLICT DO NOTHING"),
                    [{"k": k, "s": s, "t": t} for k, s, t in new])
            have = {str(s): int(n) for s, n in (await conn.execute(text(
                "SELECT satellite, count(*) FROM glm_files_seen WHERE file_start >= :lo AND file_start < :hi GROUP BY satellite"),
                {"lo": lo, "hi": hi})).all()}
            expected = int(WINDOW.total_seconds() // FILE_SECONDS)
            for sat in needed:
                if have.get(sat, 0) < math.ceil(MIN_COMPLETE * expected):
                    problems.append(f"incomplete:{sat}:{have.get(sat, 0)}/{expected}")
            await conn.execute(text("DELETE FROM lightning_recent WHERE window_end < :cut"), {"cut": now - RETENTION})
            await conn.execute(text("DELETE FROM glm_files_seen WHERE file_start < :cut"), {"cut": now - timedelta(days=2)})
            report.rows_in = report.accepted = len(new)
            status = "ok" if not problems else "failed"
            await finish_run(conn, run_id, status=status, report=report, rows_upserted=len(counts), problems=problems)
    finally:
        await engine.dispose()
    return {"status": status, "files": len(new), "buckets": len({b for b, _, _ in counts})}
```

`raise_for_status()` returns the response in httpx 0.28, so the chained `.content` is valid. Each file is counted once (`glm_files_seen`) into the 10-minute slot its start falls in, so no window double counts. A failed run still keeps what it counted, but `recent_lightning` only trusts data while the last `ok` run is fresh.

Append to `backend/app/data_health.py`:

```python
from app.pipelines.glm import WINDOW, satellite_for
from app.pipelines.grid import bucket_center


async def recent_lightning(db: AsyncSession, grid_bucket: int, *, now: datetime) -> int | None:
    lat, lon = bucket_center(grid_bucket)
    if satellite_for(lat, lon, now.date()) is None:
        return None
    last = (await db.execute(text(
        "SELECT max(finished_at) FROM source_ingest_log WHERE source = 'glm_live' AND status = 'ok'"))).scalar_one_or_none()
    if last is None or now - last > THRESHOLDS["glm_live"]:
        return None
    flashes = (await db.execute(text(
        "SELECT coalesce(sum(flashes), 0) FROM lightning_recent WHERE grid_bucket = :b AND window_end > :since AND window_end <= :until"),
        {"b": grid_bucket, "since": now - WINDOW, "until": now + SLOT_GRACE})).scalar_one()
    return int(flashes)
```

with `SLOT_GRACE = timedelta(minutes=10)` defined beside `THRESHOLDS` (a slot's end can lie up to one slot after `now`); move the imports to the top of the module. Phase 3's banner reads only this function, so a stale or dead feed shows "lightning data unavailable", never "no lightning".

Celery: in `data_feeds.py` add

```python
@celery_app.task(name="app.tasks.data_feeds.glm_live")
def glm_live() -> dict[str, object]:
    import httpx

    from app.pipelines.db import ingest_engine
    from app.pipelines.glm import run_live

    return _run("glm-live", lambda: run_live(ingest_engine, httpx.Client(timeout=60.0), now=datetime.now(timezone.utc)))
```

and in `celery_app.py`: `"glm-live": {"task": "app.tasks.data_feeds.glm_live", "schedule": 600.0, "options": {"expires": 540}},`. In `test_data_feeds_tasks.py` add `"glm-live"` to the expiry loop and `"glm_live"` to the registered-task loop.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_glm.py tests/test_data_health.py tests/test_data_feeds_tasks.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/glm.py backend/tests/test_glm.py backend/app/data_health.py backend/tests/test_data_health.py backend/app/tasks/data_feeds.py backend/app/celery_app.py backend/tests/test_data_feeds_tasks.py && git commit -m "feat(pipelines): GLM live with completeness check; banner reads NULL when stale or uncovered"`

---

### Task 11: GLM local-day totals and the 2018-02-13+ backfill

**Files:**
- Modify: `backend/app/pipelines/glm.py`, `backend/tests/test_glm.py`, `.github/workflows/data-daily.yml`
- Create: `.github/workflows/data-backfill-lightning.yml`

**Interfaces:**
- Produces: `SOURCE_DAILY = "glm_daily"`, `DAY_LAG = 2`, `MIN_FILES_PER_HOUR = 171`, `neighbours(bucket: int) -> set[int]` (the bucket and its 8 neighbours), `local_day_hours(day: date, tz: str) -> list[datetime]` (UTC hour starts intersecting the local day; 23 or 25 on DST days), `async target_series(conn) -> list[tuple[int, str]]` (route series ∪ trusted-point incident buckets + 8 neighbours in the incident's timezone, same incident filter as plan 3's `accident_conditions`), `async run_daily(engine_factory, client, *, day: date, series: Sequence[tuple[int, str]], min_files_per_hour: int = MIN_FILES_PER_HOUR) -> dict[str, object]`, `flash_extent(client, satellite, day, *, hours: int = 3, files_per_hour: int = 10) -> dict[str, float | int]`, CLI `python -m app.pipelines.glm daily [--day YYYY-MM-DD] | backfill --start YYYY-MM-DD --end YYYY-MM-DD | extent --satellite east|west --day YYYY-MM-DD`.

For one local day D, `run_daily` lists and reads every UTC hour that any target series' local day spans (about 31 hours across US zones), records each satellite-hour in `glm_hours` (files seen, complete or not), and replaces that day's `lightning_daily` GLM rows for the target series with the non-zero counts of flashes whose file start falls on D in the series' timezone. Transport or parse errors mark the run `failed` (it is retried); a short listing is a permanent gap, recorded as an incomplete hour, and makes `lightning_glm_count` NULL for every local day it touches. `daily` runs for UTC today − 2, so every US local day is over and S3 is settled; `backfill` goes newest first from the requested end to `max(start, 2018-02-13)` and is resumable through `find_completed`.

- [ ] **Step 1: Failing test** — append to `test_glm.py`, merging the import into its top import block (ruff `E402`):

```python
from app.pipelines.glm import local_day_hours, run_daily

NY, HNL, ANC = "America/New_York", "Pacific/Honolulu", "America/Anchorage"
DAY = date(2024, 7, 1)


def test_local_day_hours_follow_dst():
    assert len(local_day_hours(DAY, NY)) == 24
    assert local_day_hours(DAY, NY)[0] == datetime(2024, 7, 1, 4, tzinfo=timezone.utc)
    assert len(local_day_hours(date(2024, 3, 10), "America/Denver")) == 23
    assert len(local_day_hours(date(2024, 11, 3), "America/Denver")) == 25


def _day_keys(sat: str, per_hour: int, short_hour: datetime | None = None) -> list[str]:
    keys = []
    for h in range(0, 36):
        hour = datetime(2024, 7, 1, tzinfo=timezone.utc) + timedelta(hours=h)
        n = 1 if hour == short_hour else per_hour
        keys += [_key(hour + timedelta(seconds=20 * i), sat) for i in range(n)]
    return keys


def _two_satellites(east: list[str], west: list[str], payload: dict[str, bytes]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        keys = west if request.url.host.startswith("noaa-goes18") else east
        if request.url.path == "/":
            return httpx.Response(200, text=_listing([k for k in keys if k.startswith(request.url.params["prefix"])]))
        return httpx.Response(200, content=payload.get(request.url.path.lstrip("/"), EMPTY))

    return httpx.Client(transport=httpx.MockTransport(handler))


async def _daily(url: str, client: httpx.Client, series: list[tuple[int, str]]) -> list[object]:
    await run_daily(lambda: create_async_engine(url), client, day=DAY, series=series, min_files_per_hour=2)
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return [(await conn.execute(text("SELECT lightning_glm_count(:b, :tz, :d)"), {"b": b, "tz": tz, "d": DAY})).scalar_one()
                    for b, tz in series]
    finally:
        await engine.dispose()


def _flash_payload(east: list[str]) -> dict[str, bytes]:
    at_14 = next(k for k in east if "_s20241831400000_" in k)
    return {at_14: _h5([40.0, 40.02], [-80.0, -80.01], [0, 0])}


@requires_pg
def test_daily_counts_local_days_and_leaves_uncovered_buckets_null():
    south, hawaii, north = grid_bucket(40.0, -80.0), grid_bucket(21.3, -157.8), grid_bucket(60.0, -150.0)
    east, west = _day_keys("G16", 2), _day_keys("G18", 2)
    with migrated_db() as name:
        got = asyncio.run(_daily(sa_url(name), _two_satellites(east, west, _flash_payload(east)),
                                 [(south, NY), (hawaii, HNL), (north, ANC)]))
    assert got == [2, 0, None]


@requires_pg
def test_incomplete_hour_reads_null_not_zero():
    south, hawaii = grid_bucket(40.0, -80.0), grid_bucket(21.3, -157.8)
    east = _day_keys("G16", 2)
    west = _day_keys("G18", 2, short_hour=datetime(2024, 7, 1, 20, tzinfo=timezone.utc))
    with migrated_db() as name:
        got = asyncio.run(_daily(sa_url(name), _two_satellites(east, west, _flash_payload(east)), [(south, NY), (hawaii, HNL)]))
    assert got == [2, None]
```

(2024-07-01 is before `EAST_SWITCH`, so East is served from `noaa-goes16`; Hawaii is after `WEST_START`, so West. The flash file starts 14:00Z, 10:00 local in New York. Anchorage at 60°N has no satellite and triggers no download.)

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_glm.py -q` → FAIL (`local_day_hours`, `run_daily` missing).

- [ ] **Step 3: Implement** — append to `glm.py` (imports merged at the top):

```python
import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import time
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.grid import LAT_STRIDE, grid_bucket_sql
from app.pipelines.ingest_log import find_completed, sha256_rows
from app.pipelines.localday import local_date

SOURCE_DAILY = "glm_daily"
DAY_LAG = 2
MIN_FILES_PER_HOUR = 171


def neighbours(bucket: int) -> set[int]:
    return {bucket + dlat * LAT_STRIDE + dlon for dlat in (-1, 0, 1) for dlon in (-1, 0, 1)}


def local_day_hours(day: date, tz: str) -> list[datetime]:
    zone = ZoneInfo(tz)
    lo = datetime.combine(day, time(), zone).astimezone(timezone.utc)
    hi = datetime.combine(day + timedelta(days=1), time(), zone).astimezone(timezone.utc)
    hours: list[datetime] = []
    h = floor_hour(lo)
    while h < hi:
        hours.append(h)
        h += timedelta(hours=1)
    return hours


async def target_series(conn: AsyncConnection) -> list[tuple[int, str]]:
    route = {(int(b), str(tz)) for b, tz in (await conn.execute(text(
        "SELECT DISTINCT grid_bucket, tz FROM feature_points WHERE tz IS NOT NULL"))).all()}
    grid = grid_bucket_sql("a.latitude", "a.longitude")
    incidents = (await conn.execute(text(
        f"SELECT DISTINCT {grid}, t.tz FROM accidents_clean_daily a JOIN accidents t ON t.accident_id = a.accident_id "
        "WHERE a.point_trusted AND t.tz IS NOT NULL AND a.latitude IS NOT NULL AND a.longitude IS NOT NULL"))).all()
    return sorted(route | {(n, str(tz)) for b, tz in incidents for n in neighbours(int(b))})


async def run_daily(
    engine_factory: Callable[[], AsyncEngine],
    client: httpx.Client,
    *,
    day: date,
    series: Sequence[tuple[int, str]],
    min_files_per_hour: int = MIN_FILES_PER_HOUR,
) -> dict[str, object]:
    sha = sha256_rows(sorted(series))
    report = ValidationReport(SOURCE_DAILY)
    engine = engine_factory()
    try:
        async with engine.begin() as conn:
            if await find_completed(conn, source=SOURCE_DAILY, window_start=day, window_end=day, content_sha256=sha):
                return {"status": "skipped", "day": day.isoformat()}
            run_id = await start_run(conn, source=SOURCE_DAILY, window_start=day, window_end=day, content_sha256=sha)
        by_sat: dict[Satellite, dict[int, set[str]]] = {}
        uncovered = 0
        for b, tz in series:
            sat = satellite_for(*bucket_center(b), day)
            if sat is None:
                uncovered += 1
                continue
            by_sat.setdefault(sat, {}).setdefault(b, set()).add(tz)
        acc: Counter[tuple[int, str]] = Counter()
        sat_of: dict[int, Satellite] = {}
        hour_rows: list[dict[str, object]] = []
        problems: list[str] = [f"no_satellite_series={uncovered}"] if uncovered else []
        transport_failed = False
        for sat, buckets in sorted(by_sat.items()):
            chosen: dict[int, Satellite | None] = {b: sat for b in buckets}
            sat_of |= {b: sat for b in buckets}
            hours = sorted({h for zones in buckets.values() for tz in zones for h in local_day_hours(day, tz)})
            for hour in hours:
                bucket = s3_bucket(sat, hour.date())
                try:
                    keys = list_keys(client, bucket, hour)
                except httpx.HTTPError as exc:
                    transport_failed = True
                    problems.append(f"{sat}:{hour:%Y-%m-%dT%H}Z:list:{type(exc).__name__}")
                    hour_rows.append({"s": sat, "h": hour, "n": 0, "c": False})
                    continue
                read_ok = True
                for key in keys:
                    try:
                        start = file_start(key)
                        lat, lon = read_flashes(client.get(f"https://{bucket}.s3.amazonaws.com/{key}").raise_for_status().content)
                    except (httpx.HTTPError, OSError, KeyError, ValueError) as exc:
                        read_ok = False
                        transport_failed = True
                        problems.append(f"{sat}:{key.rsplit('/', 1)[-1]}:{type(exc).__name__}")
                        continue
                    for b, n in count_by_bucket(lat, lon, satellite=sat, chosen=chosen).items():
                        for tz in buckets[b]:
                            if local_date(start, tz) == day:
                                acc[(b, tz)] += n
                complete = read_ok and len(keys) >= min_files_per_hour
                if not complete:
                    problems.append(f"incomplete:{sat}:{hour:%Y-%m-%dT%H}Z:{len(keys)}")
                hour_rows.append({"s": sat, "h": hour, "n": len(keys), "c": complete})
        async with engine.begin() as conn:
            if hour_rows:
                await conn.execute(text(
                    "INSERT INTO glm_hours (satellite, hour_start, files, complete, run_id) VALUES (:s, :h, :n, :c, :run) "
                    "ON CONFLICT (satellite, hour_start) DO UPDATE SET files = GREATEST(glm_hours.files, EXCLUDED.files), "
                    "complete = glm_hours.complete OR EXCLUDED.complete, run_id = EXCLUDED.run_id"),
                    [r | {"run": run_id} for r in hour_rows])
            await conn.execute(text(
                "DELETE FROM lightning_daily WHERE source = 'glm' AND date = :d AND (grid_bucket, tz) IN ("
                "SELECT * FROM unnest(CAST(:b AS integer[]), CAST(:tz AS text[])))"),
                {"d": day, "b": [b for b, _ in series], "tz": [tz for _, tz in series]})
            rows = [{"b": b, "tz": tz, "d": day, "n": n, "s": sat_of[b], "run": run_id} for (b, tz), n in acc.items() if n > 0]
            if rows:
                await conn.execute(text(
                    "INSERT INTO lightning_daily (grid_bucket, tz, date, source, flashes, day_basis, satellite, run_id) "
                    "VALUES (:b, :tz, :d, 'glm', :n, 'local', :s, :run)"), rows)
            report.rows_in = report.accepted = len(rows)
            status = "failed" if transport_failed else "ok"
            await finish_run(conn, run_id, status=status, report=report, rows_upserted=len(rows), problems=problems)
    finally:
        await engine.dispose()
    return {"status": status, "day": day.isoformat(), "flashes": sum(acc.values()),
            "incomplete_hours": sum(1 for r in hour_rows if not r["c"])}


def flash_extent(client: httpx.Client, satellite: Satellite, day: date, *, hours: int = 3, files_per_hour: int = 10) -> dict[str, float | int]:
    lats: list[float] = []
    lons: list[float] = []
    bucket = s3_bucket(satellite, day)
    for h in range(12, 12 + hours):
        for key in list_keys(client, bucket, datetime.combine(day, time(h), timezone.utc))[:files_per_hour]:
            lat, lon = read_flashes(client.get(f"https://{bucket}.s3.amazonaws.com/{key}").raise_for_status().content)
            lats += lat.tolist()
            lons += lon.tolist()
    if not lats:
        return {"flashes": 0}
    return {"flashes": len(lats), "min_lat": min(lats), "max_lat": max(lats), "min_lon": min(lons), "max_lon": max(lons)}


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    client = httpx.Client(timeout=120.0)
    if args.step == "extent":
        return dict(flash_extent(client, args.satellite, args.day))
    today = utc_today()
    latest = today - timedelta(days=DAY_LAG)
    engine = ingest_engine()
    try:
        async with engine.connect() as conn:
            series = await target_series(conn)
    finally:
        await engine.dispose()
    if args.step == "daily":
        day = args.day or latest
        if day > latest:
            return {"status": "rejected", "reason": f"{day} is not yet {DAY_LAG} days old (UTC)"}
        return await run_daily(ingest_engine, client, day=day, series=series)
    first, last = max(args.start, GLM_START), min(args.end, latest)
    tally: Counter[str] = Counter()
    day = last
    while day >= first:
        tally[str((await run_daily(ingest_engine, client, day=day, series=series))["status"])] += 1
        day -= timedelta(days=1)
    return {"status": "failed" if tally["failed"] else "ok", "days": dict(tally)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="step", required=True)
    daily = sub.add_parser("daily")
    daily.add_argument("--day", type=date.fromisoformat)
    backfill = sub.add_parser("backfill")
    backfill.add_argument("--start", type=date.fromisoformat, required=True)
    backfill.add_argument("--end", type=date.fromisoformat, required=True)
    extent = sub.add_parser("extent")
    extent.add_argument("--satellite", choices=["east", "west"], required=True)
    extent.add_argument("--day", type=date.fromisoformat, required=True)
    result = asyncio.run(_main(parser.parse_args()))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result.get("status", "ok") in ("ok", "skipped") else 1)
```

In `.github/workflows/data-daily.yml`, add after the SNOTEL step:

```yaml
      - name: GLM local-day totals (UTC today - 2)
        run: uv run python -m app.pipelines.glm daily
```

`.github/workflows/data-backfill-lightning.yml` (inputs reach the shell only through `env:`):

```yaml
name: data-backfill-lightning

on:
  workflow_dispatch:
    inputs:
      start:
        description: "First GLM day (YYYY-MM-DD, not before 2018-02-13)"
        required: true
        default: "2018-02-13"
      end:
        description: "Last GLM day (YYYY-MM-DD, at most UTC today - 2)"
        required: true
      nldn_end_year:
        description: "Last complete NLDN year to backfill (the current year is loaded by the monthly job)"
        required: true

permissions:
  contents: read
  issues: write

jobs:
  slices:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.plan.outputs.matrix }}
    steps:
      - id: plan
        env:
          START: ${{ inputs.start }}
          END: ${{ inputs.end }}
        run: |
          python3 - <<'PY' >> "$GITHUB_OUTPUT"
          import json
          import os
          from datetime import date, timedelta
          start, end = date.fromisoformat(os.environ["START"]), date.fromisoformat(os.environ["END"])
          if start < date(2018, 2, 13) or end < start:
              raise SystemExit("start must be >= 2018-02-13 and <= end")
          step = max(1, -(-((end - start).days + 1) // 12))
          slices, cur = [], start
          while cur <= end:
              stop = min(cur + timedelta(days=step - 1), end)
              slices.append({"start": cur.isoformat(), "end": stop.isoformat()})
              cur = stop + timedelta(days=1)
          print("matrix=" + json.dumps({"include": slices}))
          PY

  glm:
    needs: slices
    runs-on: ubuntu-latest
    timeout-minutes: 360
    strategy:
      fail-fast: false
      max-parallel: 12
      matrix: ${{ fromJSON(needs.slices.outputs.matrix) }}
    defaults:
      run:
        working-directory: backend
    env:
      DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      INGEST_DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      SLICE_START: ${{ matrix.start }}
      SLICE_END: ${{ matrix.end }}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          version: "0.11.3"
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: backend/uv.lock
      - run: uv sync --frozen --group pipelines
      - run: uv run python -m app.pipelines.glm backfill --start "$SLICE_START" --end "$SLICE_END"

  nldn:
    runs-on: ubuntu-latest
    timeout-minutes: 360
    defaults:
      run:
        working-directory: backend
    env:
      DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      INGEST_DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      NLDN_END_YEAR: ${{ inputs.nldn_end_year }}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          version: "0.11.3"
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: backend/uv.lock
      - run: uv sync --frozen --group pipelines
      - run: uv run python -m app.pipelines.nldn backfill --start-year 1989 --end-year "$NLDN_END_YEAR"

  report:
    needs: [glm, nldn]
    if: failure()
    runs-on: ubuntu-latest
    steps:
      - env:
          GH_TOKEN: ${{ github.token }}
        run: gh issue create --repo "${{ github.repository }}" --title "data-backfill-lightning failed ($(date -u +%F))" --body "Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

(The `nldn` job's module lands in Task 12, in the same PR, before the workflow can be dispatched.)

- [ ] **Step 4 (agent): Check the satellite constants against live data** — before the PR is opened: `cd backend && uv run python -m app.pipelines.glm extent --satellite east --day 2026-07-15` and `--satellite west`; record both extents in the PR. The East box (−135…−20) and the West box (−180…−105), each capped at 54°N, must lie inside the observed flash extents of a convective day; list `https://noaa-goes18.s3.amazonaws.com/?list-type=2&prefix=GLM-L2-LCFA/2023/004/00/` and compare with NOAA's GOES-18 "operational as GOES-West" notice for `WEST_START`. If either check disagrees, stop: the constants live in `0013`'s `glm_satellite` (already merged with PR 2b-3a), so the fix is a new revision after the current head that `CREATE OR REPLACE`s `glm_satellite`, plus the same change to `glm.py` and `CASES`, reviewed as its own PR before this one.

- [ ] **Step 5: Run** — `cd backend && uv run pytest tests/test_glm.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 6: Commit** — `git add backend/app/pipelines/glm.py backend/tests/test_glm.py .github/workflows/data-daily.yml .github/workflows/data-backfill-lightning.yml && git commit -m "feat(pipelines): GLM local-day totals with hour completeness; resumable 2018-02-13+ backfill"`

---

### Task 12: NLDN tiles from 1989, monthly append, and the NLDN–GLM overlap `[default pending: DP3]`

**Files:**
- Create: `backend/app/pipelines/nldn.py`, `backend/tests/test_nldn.py`
- Modify: `.github/workflows/data-weekly.yml`, `backend/pyproject.toml`

**Interfaces:**
- Produces: `NLDN_URL = "https://www.ncei.noaa.gov/pub/data/swdi/database-csv/v2/nldn-tiles-{year}.csv.gz"`, `SOURCE_YEAR = "nldn"`, `SOURCE_MONTHLY = "nldn_monthly"`, `SOURCE_OVERLAP = "nldn_glm_overlap"`, `FIRST_YEAR = 1989`, `OVERLAP_FIRST_YEAR = 2018`, `NLDN_BOX = (24.0, 50.0, -126.0, -66.0)` (min lat, max lat, min lon, max lon; equal to the `0013` seed), `parse_tiles(lines, report: ValidationReport | None = None) -> Iterator[tuple[date, int, int]]` (day, grid bucket, count; column order `ZDAY,CENTERLON,CENTERLAT,TOTAL_COUNT`; unparsable or out-of-range rows quarantined), `center_alignment(lines) -> dict[str, int]`, `year_bytes(client, year) -> bytes`, `async load_year(conn, year: int, parsed: Sequence[tuple[date, int, int]], *, targets: set[int], run_id) -> int`, `contiguous_end(windows: Iterable[tuple[date, date]], *, first: date = date(FIRST_YEAR, 1, 1)) -> date | None`, `async set_coverage_end(conn) -> date | None`, `async load_one(engine, client, year, *, targets, today) -> dict[str, object]`, `async run_backfill(engine_factory, client, *, start_year, end_year, today)`, `async run_monthly(engine_factory, client, *, today)`, `correlate(pairs) -> dict[str, float | int]`, `async overlap(conn, *, first_year, last_year) -> dict[str, object]`, CLI `python -m app.pipelines.nldn backfill [--start-year 1989] [--end-year Y] | monthly | overlap [--first-year 2018] [--last-year Y] | alignment --year Y`.

Tiles exist from 1986 and through the current month (checked 2026-09-28). Loading starts at 1989 because western coverage before it is sparse (spec §Lightning), so pre-1989 stays NULL rather than reading as few strikes; the alternative (1989–2017 only, GLM gap left NULL) is listed under DP3 in the foundations plan. Loading every year through the current one also fills GLM's 2018-01-01…02-12 gap and gives the overlap every full year from 2018, not one. The monthly job reloads only the current year's file (plus the previous year's in January and February, to pick up its final version); overlap and climatology read the database, so no historical file is re-downloaded.

- [ ] **Step 1: Failing tests** — `backend/tests/test_nldn.py`:

```python
import asyncio
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.grid import grid_bucket
from app.pipelines.nldn import center_alignment, contiguous_end, correlate, load_year, parse_tiles, set_coverage_end
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url

LINES = [
    "# the count of Vaisala NLDN lightning strikes within a 0.1 degree grid cell for the specified day",
    "ZDAY,CENTERLON,CENTERLAT,TOTAL_COUNT",
    "20170704,-105.25,40.05,12",
    "20170704,-80.15,25.75,3",
]
BAD = ["20170704,abc,40.05,3", "20170704,-80.15,25.75,0", "20170704,-80.15,25.75"]


def test_lon_lat_column_order():
    rows = list(parse_tiles(LINES))
    assert rows[0] == (date(2017, 7, 4), grid_bucket(40.05, -105.25), 12)
    assert len(rows) == 2


def test_bad_rows_are_quarantined():
    report = ValidationReport("nldn")
    assert list(parse_tiles(LINES + BAD, report)) == list(parse_tiles(LINES))
    assert report.accepted == 2 and report.quarantined_total() == 3


def test_alignment_distribution():
    assert center_alignment(LINES) == {"5": 2}


def test_coverage_end_is_contiguous_from_1989():
    y = lambda a, b: (date(a, 1, 1), b)  # noqa: E731
    assert contiguous_end([y(1989, date(1989, 12, 31)), y(1990, date(1990, 12, 31)), y(1992, date(1992, 12, 31))]) == date(1990, 12, 31)
    assert contiguous_end([y(1990, date(1990, 12, 31))]) is None
    assert contiguous_end([y(1989, date(1989, 12, 31)), y(1990, date(1990, 9, 30)), y(1990, date(1990, 6, 30))]) == date(1990, 9, 30)


def test_correlate_needs_variation():
    assert correlate([(0, 0), (1, 1), (2, 2)])["pearson"] == 1.0
    out = correlate([(1, 1), (1, 2)])
    assert out["n"] == 2 and out["pearson"] != out["pearson"]


@requires_pg
def test_loaded_year_sets_coverage_and_reads_through_the_utc_pair():
    b = grid_bucket(40.05, -105.25)

    async def go(url: str) -> tuple[object, object, object]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO source_ingest_log (run_id, source, window_start, window_end, status, finished_at) VALUES "
                    + ", ".join(f"(gen_random_uuid(), 'nldn', '{y}-01-01', '{y}-12-31', 'ok', now())" for y in range(1989, 2018))))
                await load_year(conn, 2017, list(parse_tiles(LINES)), targets={b}, run_id=None)
                end = await set_coverage_end(conn)
                on_day = (await conn.execute(text("SELECT lightning_nldn_count(:b, DATE '2017-07-03')"), {"b": b})).scalar_one()
                before = (await conn.execute(text("SELECT lightning_nldn_count(:b, DATE '1988-07-03')"), {"b": b})).scalar_one()
            return end, on_day, before
        finally:
            await engine.dispose()

    with migrated_db() as name:
        assert asyncio.run(go(sa_url(name))) == (date(2017, 12, 31), 12, None)
```

(Local day 2017-07-03 is matched to UTC days 07-03 and 07-04; the tile on 07-04 counts.)

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_nldn.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** `backend/app/pipelines/nldn.py`:

```python
"""NOAA NCEI SWDI NLDN daily 0.1 deg tile counts (cloud-to-ground; cite the dataset) from 1989
(P2-7). Tiles are UTC days and list only non-zero tile-days: stored as lightning_daily
(source='nldn', tz='UTC', day_basis='utc'). Coverage is the CONUS box and ends at the last
contiguously loaded day (lightning_coverage_periods.end_date); outside it the count is NULL."""

from __future__ import annotations

import argparse
import asyncio
import calendar
import gzip
import hashlib
import json
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import date, datetime, timedelta

import httpx
import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.glm import satellite_for, target_series
from app.pipelines.grid import bucket_center, grid_bucket
from app.pipelines.ingest_log import find_completed, finish_run, sha256_rows, start_run, write_quarantine
from app.pipelines.validate import ValidationReport

NLDN_URL = "https://www.ncei.noaa.gov/pub/data/swdi/database-csv/v2/nldn-tiles-{year}.csv.gz"
SOURCE_YEAR = "nldn"
SOURCE_MONTHLY = "nldn_monthly"
SOURCE_OVERLAP = "nldn_glm_overlap"
FIRST_YEAR = 1989
OVERLAP_FIRST_YEAR = 2018
NLDN_BOX = (24.0, 50.0, -126.0, -66.0)


def _rows(lines: Iterable[str]) -> Iterator[tuple[int, list[str]]]:
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("#") or line.upper().startswith("ZDAY"):
            continue
        yield i, line.split(",")


def parse_tiles(lines: Iterable[str], report: ValidationReport | None = None) -> Iterator[tuple[date, int, int]]:
    for i, fields in _rows(lines):
        try:
            zday, center_lon, center_lat, count = fields
            day = datetime.strptime(zday, "%Y%m%d").date()
            lat, lon, n = float(center_lat), float(center_lon), int(count)
        except ValueError:
            if report is not None:
                report.quarantine(f"line:{i}", "unparsable")
            continue
        if not (0.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or n <= 0:
            if report is not None:
                report.quarantine(f"line:{i}", "out_of_range")
            continue
        if report is not None:
            report.accept()
        yield day, grid_bucket(lat, lon), n


def center_alignment(lines: Iterable[str]) -> dict[str, int]:
    return dict(Counter(f"{abs(float(r[2])) * 100:.0f}"[-1] for _, r in _rows(lines) if len(r) == 4))


def year_bytes(client: httpx.Client, year: int) -> bytes:
    response = client.get(NLDN_URL.format(year=year), timeout=600.0)
    response.raise_for_status()
    return response.content


async def load_year(
    conn: AsyncConnection, year: int, parsed: Sequence[tuple[date, int, int]], *, targets: set[int], run_id: uuid.UUID | None
) -> int:
    await conn.execute(text(
        "DELETE FROM lightning_daily WHERE source = 'nldn' AND date BETWEEN :s AND :e AND grid_bucket = ANY(:t)"),
        {"s": date(year, 1, 1), "e": date(year, 12, 31), "t": sorted(targets)})
    rows = [{"b": b, "d": d, "n": n, "run": run_id} for d, b, n in parsed if b in targets and d.year == year]
    if rows:
        await conn.execute(text(
            "INSERT INTO lightning_daily (grid_bucket, tz, date, source, flashes, day_basis, run_id) "
            "VALUES (:b, 'UTC', :d, 'nldn', :n, 'utc', :run) ON CONFLICT (grid_bucket, tz, date, source) "
            "DO UPDATE SET flashes = lightning_daily.flashes + EXCLUDED.flashes"), rows)
    return len(rows)


def contiguous_end(windows: Iterable[tuple[date, date]], *, first: date = date(FIRST_YEAR, 1, 1)) -> date | None:
    cursor = first
    for start, end in sorted(windows):
        if start > cursor:
            break
        cursor = max(cursor, end + timedelta(days=1))
    return cursor - timedelta(days=1) if cursor > first else None


async def set_coverage_end(conn: AsyncConnection) -> date | None:
    windows = [(s, e) for s, e in (await conn.execute(text(
        "SELECT window_start, window_end FROM source_ingest_log WHERE source = :s AND status = 'ok' "
        "AND window_start IS NOT NULL AND window_end IS NOT NULL"), {"s": SOURCE_YEAR})).all()]
    end = contiguous_end(windows)
    await conn.execute(text("UPDATE lightning_coverage_periods SET end_date = :e WHERE source = 'nldn'"), {"e": end})
    return end


async def target_buckets(conn: AsyncConnection) -> set[int]:
    return {b for b, _ in await target_series(conn)}


async def load_one(engine: AsyncEngine, client: httpx.Client, year: int, *, targets: set[int], today: date) -> dict[str, object]:
    targets_sha = sha256_rows([(b,) for b in sorted(targets)])
    complete_year = year < today.year
    if complete_year:
        async with engine.begin() as conn:
            if await find_completed(conn, source=SOURCE_YEAR, window_start=date(year, 1, 1), window_end=date(year, 12, 31),
                                    content_sha256=targets_sha):
                return {"year": year, "status": "skipped"}
    try:
        raw = year_bytes(client, year)
    except httpx.HTTPError as exc:
        return {"year": year, "status": "failed", "problem": type(exc).__name__}
    report = ValidationReport(SOURCE_YEAR)
    parsed = list(parse_tiles(gzip.decompress(raw).decode("utf-8").splitlines(), report))
    last = max((d for d, _, _ in parsed), default=None)
    if last is None:
        return {"year": year, "status": "failed", "problem": "no rows"}
    window_end = date(year, 12, 31) if complete_year else last
    sha = targets_sha if complete_year else sha256_rows([(targets_sha,), (hashlib.sha256(raw).hexdigest(),)])
    async with engine.begin() as conn:
        if not complete_year and await find_completed(conn, source=SOURCE_YEAR, window_start=date(year, 1, 1),
                                                      window_end=window_end, content_sha256=sha):
            return {"year": year, "status": "skipped"}
        run_id = await start_run(conn, source=SOURCE_YEAR, window_start=date(year, 1, 1), window_end=window_end, content_sha256=sha)
        n = await load_year(conn, year, parsed, targets=targets, run_id=run_id)
        await write_quarantine(conn, run_id, report)
        await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n)
        end = await set_coverage_end(conn)
    return {"year": year, "status": "ok", "rows": n, "coverage_end": end.isoformat() if end else None}


async def run_backfill(
    engine_factory: Callable[[], AsyncEngine], client: httpx.Client, *, start_year: int, end_year: int, today: date
) -> dict[str, object]:
    if start_year < FIRST_YEAR:
        return {"status": "rejected", "reason": f"NLDN loads start at {FIRST_YEAR} (pre-1989 stays NULL, DP3)"}
    engine = engine_factory()
    results: list[dict[str, object]] = []
    try:
        async with engine.connect() as conn:
            targets = await target_buckets(conn)
        for year in range(start_year, min(end_year, today.year) + 1):
            result = await load_one(engine, client, year, targets=targets, today=today)
            results.append(result)
            if result["status"] == "failed":
                break
    finally:
        await engine.dispose()
    return {"status": "failed" if any(r["status"] == "failed" for r in results) else "ok", "years": results}


async def run_monthly(engine_factory: Callable[[], AsyncEngine], client: httpx.Client, *, today: date) -> dict[str, object]:
    years = ([today.year - 1] if today.month <= 2 else []) + [today.year]
    engine = engine_factory()
    report = ValidationReport(SOURCE_MONTHLY)
    try:
        async with engine.begin() as conn:
            targets = await target_buckets(conn)
            run_id = await start_run(conn, source=SOURCE_MONTHLY, window_start=today, window_end=today, content_sha256=None)
        results = [await load_one(engine, client, y, targets=targets, today=today) for y in years]
        status = "failed" if any(r["status"] == "failed" for r in results) else "ok"
        async with engine.begin() as conn:
            await finish_run(conn, run_id, status=status, report=report, rows_upserted=0,
                             problems=[json.dumps(r, sort_keys=True) for r in results])
    finally:
        await engine.dispose()
    return {"status": status, "years": results}


def _ranks(x: np.ndarray) -> np.ndarray:
    order = x.argsort()
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x))
    for value in np.unique(x):
        tied = x == value
        ranks[tied] = ranks[tied].mean()
    return ranks


def correlate(pairs: Sequence[tuple[float, float]]) -> dict[str, float | int]:
    x = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)
    if len(pairs) < 3 or x.std() == 0 or y.std() == 0:
        return {"n": len(pairs), "pearson": float("nan"), "spearman": float("nan")}
    return {"n": len(pairs), "pearson": round(float(np.corrcoef(x, y)[0, 1]), 6),
            "spearman": round(float(np.corrcoef(_ranks(x), _ranks(y))[0, 1]), 6)}


def _month_end(m: date) -> date:
    return date(m.year, m.month, calendar.monthrange(m.year, m.month)[1])


async def overlap(conn: AsyncConnection, *, first_year: int, last_year: int) -> dict[str, object]:
    lo, hi = date(first_year, 1, 1), date(last_year, 12, 31)
    full = {(str(s), m) for s, m, n in (await conn.execute(text(
        "SELECT satellite, date_trunc('month', hour_start AT TIME ZONE 'UTC')::date, count(*) FILTER (WHERE complete) "
        "FROM glm_hours WHERE hour_start >= :lo AND hour_start < :hi GROUP BY 1, 2"),
        {"lo": datetime(first_year, 1, 1), "hi": datetime(last_year + 1, 1, 1)})).all()
        if int(n) == calendar.monthrange(m.year, m.month)[1] * 24}
    sums: dict[str, dict[tuple[int, date], int]] = {"glm": {}, "nldn": {}}
    for source, b, m, n in (await conn.execute(text(
            "SELECT source, grid_bucket, date_trunc('month', date)::date, sum(flashes) FROM lightning_daily "
            "WHERE date BETWEEN :lo AND :hi GROUP BY 1, 2, 3"), {"lo": lo, "hi": hi})).all():
        sums[str(source)][(int(b), m)] = int(n)
    end = (await conn.execute(text("SELECT end_date FROM lightning_coverage_periods WHERE source = 'nldn'"))).scalar_one_or_none()
    months = [date(y, mo, 1) for y in range(first_year, last_year + 1) for mo in range(1, 13)]
    by_year: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for b in sorted({b for b, _ in sums["glm"]} | {b for b, _ in sums["nldn"]}):
        lat, lon = bucket_center(b)
        if not (NLDN_BOX[0] <= lat <= NLDN_BOX[1] and NLDN_BOX[2] <= lon <= NLDN_BOX[3]):
            continue
        for m in months:
            if end is None or _month_end(m) > end:
                continue
            sat = satellite_for(lat, lon, m)
            if sat is None or sat != satellite_for(lat, lon, _month_end(m)) or (sat, m) not in full:
                continue
            by_year[m.year].append((float(sums["nldn"].get((b, m), 0)), float(sums["glm"].get((b, m), 0))))
    everything = [p for pairs in by_year.values() for p in pairs]
    return dict(correlate(everything)) | {"by_year": {str(y): correlate(p) for y, p in sorted(by_year.items())}}


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    today = utc_today()
    client = httpx.Client(timeout=600.0)
    if args.step == "alignment":
        return {"status": "ok", "alignment": center_alignment(gzip.decompress(year_bytes(client, args.year)).decode("utf-8").splitlines())}
    if args.step == "backfill":
        return await run_backfill(ingest_engine, client, start_year=args.start_year, end_year=args.end_year or today.year - 1, today=today)
    if args.step == "monthly":
        return await run_monthly(ingest_engine, client, today=today)
    last_full = today.year - 1
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source=SOURCE_OVERLAP, window_start=date(args.first_year, 1, 1),
                                     window_end=date(args.last_year or last_full, 12, 31), content_sha256=None)
            result = await overlap(conn, first_year=args.first_year, last_year=args.last_year or last_full)
            await finish_run(conn, run_id, status="ok", report=ValidationReport(SOURCE_OVERLAP), rows_upserted=0,
                             problems=[json.dumps(result, sort_keys=True, default=str)])
    finally:
        await engine.dispose()
    return {"status": "ok"} | result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="step", required=True)
    backfill = sub.add_parser("backfill")
    backfill.add_argument("--start-year", type=int, default=FIRST_YEAR)
    backfill.add_argument("--end-year", type=int)
    sub.add_parser("monthly")
    ov = sub.add_parser("overlap")
    ov.add_argument("--first-year", type=int, default=OVERLAP_FIRST_YEAR)
    ov.add_argument("--last-year", type=int)
    al = sub.add_parser("alignment")
    al.add_argument("--year", type=int, required=True)
    result = asyncio.run(_main(parser.parse_args()))
    print(json.dumps(result, sort_keys=True, default=str))
    raise SystemExit(0 if result.get("status") == "ok" else 1)
```

The overlap compares bucket-months inside the NLDN box whose GLM satellite hours were all complete and whose month lies inside NLDN coverage; a bucket-month with no tile inside coverage counts as 0 NLDN strikes, and one with no GLM row inside complete hours counts as 0 GLM flashes. Buckets with no lightning in either source over the whole period do not appear (they carry no calibration information). Append `"app.pipelines.nldn"` to strict mypy.

- [ ] **Step 4 (agent): Alignment** — after the backfill has loaded 2017 (Task 15), `INGMOD app.pipelines.nldn alignment --year 2017`. If centres end in `5`, NLDN tiles straddle two of our buckets: record it in the PR and append it to the `nldn` row's `notes` in `lightning_coverage_periods` through a one-line owner SQL update in the runbook; the tile-centre mapping stays deterministic, and Phase 3 uses NLDN as a within-cell anomaly, which a consistent half-cell offset does not bias.

`.github/workflows/data-weekly.yml` gains the monthly job; the whole file becomes:

```yaml
name: data-weekly

on:
  schedule:
    - cron: "20 7 * * 2"    # weekly, Tuesdays 07:20 UTC
    - cron: "30 10 3 * *"   # monthly, 3rd of the month 10:30 UTC
  workflow_dispatch:

permissions:
  contents: read
  issues: write

jobs:
  weekly:
    if: github.event_name == 'workflow_dispatch' || github.event.schedule == '20 7 * * 2'
    runs-on: ubuntu-latest
    timeout-minutes: 120
    defaults:
      run:
        working-directory: backend
    env:
      DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      INGEST_DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      HEALTHCHECKS_PING_KEY: ${{ secrets.HEALTHCHECKS_PING_KEY }}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          version: "0.11.3"
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: backend/uv.lock
      - run: uv sync --frozen --group pipelines
      - name: Tick quarantine re-evaluation
        run: uv run python -m app.pipelines.mp_ticks_quarantine
      - name: Public tick counts
        run: uv run python -m app.pipelines.mp_tick_counts
      - name: Open an issue on failure
        if: failure()
        working-directory: .
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh issue create --title "data-weekly failed ($(date -u +%F))" --body "Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"

  monthly:
    if: github.event_name == 'workflow_dispatch' || github.event.schedule == '30 10 3 * *'
    runs-on: ubuntu-latest
    timeout-minutes: 240
    defaults:
      run:
        working-directory: backend
    env:
      DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      INGEST_DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      HEALTHCHECKS_PING_KEY: ${{ secrets.HEALTHCHECKS_PING_KEY }}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          version: "0.11.3"
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: backend/uv.lock
      - run: uv sync --frozen --group pipelines
      - name: NLDN current-year append
        run: uv run python -m app.pipelines.nldn monthly
      - name: NLDN-GLM overlap (every full year from 2018)
        run: uv run python -m app.pipelines.nldn overlap
      - name: Open an issue on failure
        if: failure()
        working-directory: .
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh issue create --title "data-weekly (monthly) failed ($(date -u +%F))" --body "Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

- [ ] **Step 5: Run** — `cd backend && uv run pytest tests/test_nldn.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 6: Commit** — `git add backend/app/pipelines/nldn.py backend/tests/test_nldn.py .github/workflows/data-weekly.yml backend/pyproject.toml && git commit -m "feat(pipelines): NLDN tiles from 1989 with monthly append, coverage end, multi-year NLDN-GLM overlap"`

---

### Task 13: Lightning-day frequency in `cell_climatology`, PR 2b-3b docs

**Files:**
- Create: `backend/app/pipelines/lightning_climatology.py`, `backend/tests/test_lightning_climatology.py`
- Modify: `.github/workflows/data-weekly.yml`, `backend/pyproject.toml`, `CHANGELOG.md`, `DEPLOYMENT.md`, `CLAUDE.md`, `data/DATABASE_STRUCTURE.md`

**Interfaces:**
- Consumes: `normals.window(today) -> tuple[int, int]` (plan 3), `glm.satellite_for`, `glm.local_day_hours`, `glm.GLM_START`, `glm.DAY_LAG`, `localday.tz_for_point`, `grid_bucket_series`.
- Produces: `SOURCE = "lightning_climatology"`, `HALF_WINDOW = 7`, `freq_by_doy(days: Sequence[tuple[date, float | None]], half_window: int = HALF_WINDOW) -> dict[int, float | None]`, `lightning_day_freq(days, doy, half_window=HALF_WINDOW) -> float | None` (share of covered days with ≥1 flash in the pooled ±7-day window; NULL when no covered day), `local_day_covered(complete_hours: set[datetime], day: date, tz: str) -> bool`, `async run(conn, *, today: date) -> int` (writes only `cell_climatology.lightning_day_freq`), CLI `python -m app.pipelines.lightning_climatology`.

A bucket's frequency is computed on its primary series: the timezone of the bucket centre when that series is registered, otherwise the alphabetically first registered timezone of the bucket (the same bucket-level convention as plan 3's normals, one row per bucket). A day enters the frequency only when its satellite hours were all complete; days with no satellite or incomplete hours are missing, so a bucket outside coverage gets NULL, never 0.

- [ ] **Step 1: Failing test** — `backend/tests/test_lightning_climatology.py`:

```python
from datetime import date, datetime, timedelta, timezone

from app.pipelines.glm import local_day_hours
from app.pipelines.lightning_climatology import freq_by_doy, lightning_day_freq, local_day_covered


def test_share_of_covered_days_with_flashes():
    days = [(date(2024, 7, 1) + timedelta(d), 1.0 if d % 2 == 0 else 0.0) for d in range(15)]
    assert lightning_day_freq(days, date(2024, 7, 8).timetuple().tm_yday) == 8 / 15


def test_no_covered_days_is_none():
    assert lightning_day_freq([(date(2024, 7, 1), None)], 183) is None


def test_window_wraps_the_year_end():
    days = [(date(2024, 12, 31), 1.0), (date(2025, 1, 2), 0.0)]
    assert freq_by_doy(days)[1] == 0.5


def test_a_day_is_covered_only_when_every_hour_is_complete():
    hours = set(local_day_hours(date(2024, 11, 3), "America/Denver"))
    assert len(hours) == 25
    assert local_day_covered(hours, date(2024, 11, 3), "America/Denver")
    hours.discard(datetime(2024, 11, 3, 18, tzinfo=timezone.utc))
    assert not local_day_covered(hours, date(2024, 11, 3), "America/Denver")
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_lightning_climatology.py -q` → FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/lightning_climatology.py`:

```python
"""Lightning-day frequency per bucket and day of year (spec: part of cell_climatology), from
GLM local-day counts. A day counts only when every hour it spans was complete for the bucket's
satellite; otherwise it is missing, never zero, and a bucket with no covered day gets NULL."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.glm import DAY_LAG, GLM_START, local_day_hours, satellite_for
from app.pipelines.grid import bucket_center
from app.pipelines.localday import tz_for_point
from app.pipelines.normals import window

SOURCE = "lightning_climatology"
HALF_WINDOW = 7


def freq_by_doy(days: Sequence[tuple[date, float | None]], half_window: int = HALF_WINDOW) -> dict[int, float | None]:
    hits = [0] * 367
    total = [0] * 367
    for d, n in days:
        if n is None:
            continue
        k = d.timetuple().tm_yday
        total[k] += 1
        hits[k] += n > 0
    out: dict[int, float | None] = {}
    for doy in range(1, 367):
        h = t = 0
        for offset in range(-half_window, half_window + 1):
            k = (doy - 1 + offset) % 366 + 1
            h += hits[k]
            t += total[k]
        out[doy] = h / t if t else None
    return out


def lightning_day_freq(days: Sequence[tuple[date, float | None]], doy: int, half_window: int = HALF_WINDOW) -> float | None:
    return freq_by_doy(days, half_window)[doy]


def local_day_covered(complete_hours: set[datetime], day: date, tz: str) -> bool:
    return all(h in complete_hours for h in local_day_hours(day, tz))


async def run(conn: AsyncConnection, *, today: date) -> int:
    first_year, last_year = window(today)
    start, end = max(GLM_START, date(first_year, 1, 1)), min(date(last_year, 12, 31), today - timedelta(days=DAY_LAG))
    if start > end:
        return 0
    complete: dict[str, set[datetime]] = {"east": set(), "west": set()}
    for sat, hour in (await conn.execute(text(
            "SELECT satellite, hour_start FROM glm_hours WHERE complete AND hour_start >= :lo AND hour_start < :hi"),
            {"lo": datetime.combine(start - timedelta(days=1), time(), timezone.utc),
             "hi": datetime.combine(end + timedelta(days=2), time(), timezone.utc)})).all():
        complete[str(sat)].add(hour)
    zones: dict[int, set[str]] = defaultdict(set)
    for b, tz in (await conn.execute(text("SELECT grid_bucket, tz FROM grid_bucket_series"))).all():
        zones[int(b)].add(str(tz))
    primary: dict[int, str] = {}
    for b, tzs in zones.items():
        centre = tz_for_point(*bucket_center(b))
        primary[b] = centre if centre in tzs else min(tzs)
    counts = {(int(b), str(tz), d): int(n) for b, tz, d, n in (await conn.execute(text(
        "SELECT grid_bucket, tz, date, flashes FROM lightning_daily WHERE source = 'glm' AND date BETWEEN :s AND :e"),
        {"s": start, "e": end})).all()}
    span = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    covered: dict[tuple[str, str, date], bool] = {}
    updates: list[dict[str, object]] = []
    for b, tz in sorted(primary.items()):
        lat, lon = bucket_center(b)
        days: list[tuple[date, float | None]] = []
        for d in span:
            sat = satellite_for(lat, lon, d)
            if sat is None:
                days.append((d, None))
                continue
            key = (sat, tz, d)
            if key not in covered:
                covered[key] = local_day_covered(complete[sat], d, tz)
            days.append((d, float(counts.get((b, tz, d), 0)) if covered[key] else None))
        updates += [{"b": b, "doy": doy, "f": f} for doy, f in freq_by_doy(days).items()]
    if updates:
        await conn.execute(text("UPDATE cell_climatology SET lightning_day_freq = :f WHERE grid_bucket = :b AND doy = :doy"), updates)
    return len(primary)


async def _main() -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.pipelines.ingest_log import finish_run, start_run
    from app.pipelines.validate import ValidationReport
    from app.services.temporal_weighting import utc_today

    today = utc_today()
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=today, content_sha256=None)
            n = await run(conn, today=today)
            await finish_run(conn, run_id, status="ok", report=ValidationReport(SOURCE), rows_upserted=n)
    finally:
        await engine.dispose()
    return {"status": "ok", "buckets": n}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_main()), sort_keys=True))
```

In `data-weekly.yml`'s `monthly` job, add after the overlap step:

```yaml
      - name: Lightning-day climatology
        run: uv run python -m app.pipelines.lightning_climatology
```

(It is cheap and idempotent, so running it monthly also restores the column after plan 3's January normals rebuild.) Append `"app.pipelines.lightning_climatology"` to strict mypy.

Docs: CHANGELOG.md gets one entry headed `Phase 2b lightning (PR 2b-3b)`, dated with the commit day from `date -u +%F`. DEPLOYMENT.md: `glm-live` beat entry on `ingest-worker` (slug `glm-live`, period 10 min, grace 20 min), `h5py` in the image, the backfill workflow, its inputs and expected run time, the monthly NLDN job. DATABASE_STRUCTURE.md: `lightning_daily`, `glm_hours`, `lightning_recent`, `lightning_coverage_periods`, `nws_alert_coverage`, `cell_feed_pending`; how to read lightning (`lightning_glm_count(grid_bucket, tz, date)` and `lightning_nldn_count(grid_bucket, date)`: a count, 0 inside coverage, NULL outside); `scope_lightning_coverage` for area/objective badges; NULL vs `'{}'` in `nws_alert_codes`; lightning is never stored on `cell_daily_conditions`. `DATA_LICENSE.md` is **not** edited (pending legal review) — list "cite SWDI NLDN in DATA_LICENSE.md" in the PR description as an owner follow-up (the P3 lightning contract was already updated on 2026-09-28).

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/` → green.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/lightning_climatology.py backend/tests/test_lightning_climatology.py .github/workflows/data-weekly.yml CHANGELOG.md DEPLOYMENT.md CLAUDE.md data/DATABASE_STRUCTURE.md backend/pyproject.toml && git commit -m "feat(pipelines): lightning-day frequency climatology from complete GLM days"`

---

### Task 14: OWNER/AGENT RUNBOOK — PR 2b-3a

- [ ] **Step 1 (owner/agent): Shell helpers** — `cd /Users/sebastianfrazier/Developer/SafeAscent/backend && . scripts/runbook_helpers.sh` (plan 2's committed helpers: `TARGET_HOST`, `INGMOD`, `OWNER_PSQL`, `ANALYST_PSQL`, `VERIFY`; TLS verify-full through plan 1's `verify_full_url`). Never redefine them here.
- [ ] **Step 2 (owner/agent): Migrate** — on a rehearsal branch (`export TARGET_HOST=` the branch host from the Neon Console) then prod (`unset TARGET_HOST`): `0013` as `migrator` (refuses if R7 was not applied), then `OWNER_PSQL -f db/roles/grants_phase2.sql`, `OWNER_PSQL -f db/roles/verify_roles_phase2.sql`.
- [ ] **Step 3 (owner): Railway** — create the `ingest-worker` service from `backend/railway-ingest.toml`; set on it only: `INGEST_DATABASE_URL` (pasted by the owner from `.env.ingest` via the Railway UI; agents never read it), `DATABASE_URL` (same value, satisfies `Settings`), `HEALTHCHECKS_PING_KEY`, `OPEN_METEO_API_KEY` (the Standard plan key: this service calls only the Forecast API). Confirm the general `worker` service has no `INGEST_DATABASE_URL`. Create healthchecks checks `forecast-nightly` (24 h / 2 h), `nws-alerts` (1 h / 1 h), `airnow` (1 h / 2 h). Deploy `beat` and `ingest-worker` together (the beat schedule changed); `/health/worker` stays 200.
- [ ] **Step 4 (owner/agent): NWS archive** — `INGMOD app.pipelines.nws archive --start 2007-10-01 --end "$(date -u -v-1d +%F)"` (IEM, free, resumable; days before 2007-10-01 stay NULL).
- [ ] **Step 5 (owner/agent): Acceptance** — after 7 nights: `ANALYST_PSQL -At -c "SELECT date(finished_at), status FROM source_ingest_log WHERE source='open_meteo_forecast' ORDER BY 1 DESC LIMIT 7"` → seven `ok`; `ANALYST_PSQL -At -c "SELECT record_kind, count(*) FROM cell_daily_conditions WHERE date >= current_date - 3 GROUP BY 1"` → `stopgap` and `forecast` rows present; `ANALYST_PSQL -At -c "SELECT count(*) FROM cell_feed_pending WHERE first_seen_at < now() - interval '2 days'"` → 0 (anything older is investigated, never deleted). Staleness drill: pause `ingest-worker` for 3 hours → `/health/data` returns 503 with `nws_alerts: stale` and the `nws-alerts` check alerts → resume → 200 (spec: "staleness alert drilled").

### Task 15: OWNER/AGENT RUNBOOK — PR 2b-3b

- [ ] **Step 1 (owner/agent)**: deploy `ingest-worker`/`beat` with `h5py`; add healthchecks `glm-live` (10 min / 20 min); `/health/data` shows `glm_live: ok` within 30 minutes; Task 11 Step 4's extents and `WEST_START` check are recorded in the PR.
- [ ] **Step 2 (owner)**: dispatch `data-backfill-lightning` with `start=2018-02-13`, `end=$(date -u -v-2d +%F)`, `nldn_end_year` = last complete year; expect roughly two days of wall time; re-dispatch until every GLM slice reports only `skipped` days and the NLDN job only `skipped` years. Then `INGMOD app.pipelines.nldn monthly` once to load the current year.
- [ ] **Step 3 (owner/agent)**: `ANALYST_PSQL -At -c "SELECT end_date FROM lightning_coverage_periods WHERE source='nldn'"` → within the last two months; Task 12 Step 4 alignment recorded; `INGMOD app.pipelines.nldn overlap` → report overall and per-year `pearson`/`spearman` for every full year from 2018 in the PR (spec 2b-3 acceptance: "NLDN–GLM overlap correlation reported"); `INGMOD app.pipelines.lightning_climatology`. Spot checks, all expected NULL: `ANALYST_PSQL -At -c "SELECT lightning_glm_count(6000000 - 1500 + 5000, 'America/Anchorage', DATE '2024-07-01'), lightning_glm_count(2130000 - 1578 + 5000, 'Pacific/Honolulu', DATE '2020-07-01'), lightning_nldn_count(2130000 - 1578 + 5000, DATE '2017-07-01'), lightning_nldn_count(4000000 - 1053 + 5000, DATE '1988-07-01')"`.

---

## Self-review

- Spec coverage (2b-3): forecast + flagged stopgap (Task 2), feed values never lost (Task 3), SNOTEL (Task 4), NWS alerts + IEM archive (Task 5), AirNow (Task 6), `/health/data` (Tasks 7, 10), GLM live + local-day totals + 2018-02-13 backfill (Tasks 9–11), NLDN 1989→current with monthly append and multi-year overlap (Task 12), lightning-day climatology (Task 13), scheduling split beat/Actions with a dedicated ingest worker (Task 8, workflows), monitoring (run log for every feed, healthchecks, failure issues), `area_weekly_weather` dropped (Task 1), acceptance "7 nightly ok, staleness drilled, overlap reported" (Tasks 14–15). ERA5 archive work (append, stopgap replacement, normals, 3-year prune) is plan 3's January window, not here.
- Missing is never zero: lightning NULL outside satellite field of view, north of 54°N, before 2018-02-13 (GLM) / 1989 (NLDN), after NLDN's loaded end, and on any local day touching an incomplete satellite-hour; AQI/alerts/SNOTEL NULL when not collected, `'{}'` only for a collected day with no alert; the live banner is NULL when the feed is stale.
- Placeholders: none. Runbook values the owner supplies are the Neon branch host and Railway UI entries; dates come from `date -u`.
- Types and names: `Location(grid_bucket, lat, lon, tz)`, `day_rows(grid_bucket, tz, response, *, today, origin, …)`, `upsert_rows`, `FeedValue`/`write_feed_values`/`drain_pending`, `satellite_for(lat, lon, day)` = SQL `glm_satellite`, `count_by_bucket(…, chosen=…)`, `run_live`, `run_daily`, `target_series`, `neighbours`, `parse_tiles`, `load_year(conn, year, parsed, …)`, `lightning_glm_count`, `lightning_nldn_count`, `recent_lightning` are used with the signatures their tasks define. Alembic: `0013_live_feeds` → `down_revision = "0012_drop_legacy_routes"`.
- Local day: forecast/stopgap (Open-Meteo `timezone=<tz>`, `today` = series local today), GLM (file start → series local date), AirNow (hour → series local date), NWS (alert interval vs series local day), SNOTEL (series local yesterday); NLDN is UTC-day by construction and read as the UTC pair.
