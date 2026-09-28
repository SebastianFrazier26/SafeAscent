# Phase 2b Live Feeds and Lightning (PRs 2b-3a, 2b-3b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep `cell_daily_conditions` current for every route-bearing cell: the nightly Open-Meteo forecast before scoring, a rolling 3-year ERA5 history with weekly append, SNOTEL snowpack, NWS alerts (live and archived polygon warnings), AirNow AQI, GOES GLM lightning (live 10-minute banner data and daily totals, 2018 onward), the NLDN 1989–2017 lightning backfill with its GLM overlap check, lightning-day climatology, and a `/health/data` staleness endpoint that alerts when any feed goes stale.

**Architecture:** Time-critical feeds run as Celery beat tasks on the existing worker (`app/tasks/data_feeds.py`, connecting as `ingest`); batch feeds run as GitHub Actions workflows (`data-daily.yml`, `data-weekly.yml`, dispatch-only backfills) (P2-6). Every client is a typed, validated, recorded-response-tested module under `app/pipelines/`; all writes go through `cell_conditions.upsert_rows` (weather) or column-specific upserts that never overwrite weather with nulls. Missing is never zero: lightning outside satellite coverage or before 1989 stays NULL, and coverage periods are recorded so absence inside coverage can be read as zero.

**Tech Stack:** Python 3.12, httpx, pydantic 2, h5py (GLM netCDF4/HDF5; main dependency, the worker needs it), stdlib `xml.etree` (S3 listings, not HTML), Celery 5.4 beat, SQLAlchemy async, PostGIS, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` §Dynamic features, §Lightning (P2-7), §Pipelines (scheduling P2-6, monitoring), milestone 2b-3; P3:151 columns; decisions D8, D14 in `2026-09-28-phase2a-foundations.md`.

**Prerequisites:** Plans 3 (conditions table, Open-Meteo client), 5 (feature points, climatology) merged and applied.

**Two PRs:** 2b-3a = Tasks 1–8 + runbook Task 14 (`feat/p2b-live-feeds`); 2b-3b = Tasks 9–13 + runbook Task 15 (`feat/p2b-lightning`).

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- Beat runs as exactly one replica (DEPLOYMENT.md); new beat entries carry `expires` so a stalled worker never runs a backlog of stale fetches.
- Every feed pings its healthchecks.io slug (start/success/fail) via `jobping.job_ping`; `/health/data` thresholds: forecast >26 h, alerts >2 h, GLM live >30 min, OpenBeta >9 d → 503.
- Forecast rows never overwrite archive rows (plan 3 rule); alert/AQI/SNOTEL/lightning upserts touch only their own columns.
- Lightning: NULL (missing) before 1989 and north of 54°N; GLM and NLDN stored in separate columns with `lightning_source`; `lightning_coverage` is `glm|nldn|none`.
- User-Agent with the public repository URL on api.weather.gov and WDQS-style services (their policy); never an email address.
- No HTML parsing: S3 listings are XML (`xml.etree`), AirNow/SNOTEL/NLDN are CSV/JSON, GLM is HDF5.

## Decisions this plan makes where the spec is silent (owner may overrule)

1. **Satellite per bucket for live GLM:** buckets west of −105° use GOES-West (18), others GOES-East (19), so a flash seen by both is counted once; the chosen satellite is stored in `lightning_recent.satellite`. History 2018→2025-04 is GOES-16 East only (spec).
2. **GLM daily storage:** flash counts per bucket-day in `lightning_density` (a count per 0.1° cell, the P3 column name kept); rows written for route-bearing buckets over the rolling 3-year window and for incident buckets + 8 neighbours from 2018.
3. **NLDN absence = zero inside coverage:** only non-zero tile-days are stored (spec ~1M rows); `lightning_coverage_periods` records where absence means zero (1989-01-01 → 2017-12-31, ≤54°N).
4. **NWS history** covers polygon (storm-based) warnings from the IEM archive only; zone-based products (e.g. winter storm warnings) have no archived polygons in that endpoint and are NULL before live collection began.
5. **AQI per bucket-day** is the daily maximum over hours of the maximum AQI among AirNow sites within 50 km; no site within 50 km → NULL.
6. **SNOTEL attachment:** nearest active station within 30 km whose elevation is within ±500 m of the bucket's mean route-point elevation; `swe_delta_mm` is SWE(today) − SWE(today − 3 days).
7. **Rolling 3-year window pruning:** rows older than 3 years + 30 days are deleted only for buckets that hold no incident (and are not an incident neighbour); incident history since 1990 is kept.

## Review Focus

1. **A GLM file whose `flash_lat` is packed int16 with `scale_factor`/`add_offset`** — expect real degrees, not raw integers (Task 9 `test_packed_coordinates_are_unpacked`).
2. **The same flash reported by GOES-East and GOES-West** — expect one count (Task 10 `test_bucket_takes_one_satellite`).
3. **An NLDN file with lon before lat** — expect correct bucket assignment (Task 12 `test_lon_lat_column_order`).
4. **`/health/data` when a feed has never run** — expect it reported as `never_run`, not stale-503 (a new deployment must not page), and 503 once it has run and then gone stale (Task 7 `test_never_run_is_not_stale`).
5. **An AirNow hour file missing** (404 during their outages) — expect the task to log a failed run and leave AQI NULL, never 0 (Task 6 `test_missing_file_leaves_aqi_null`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/alembic/versions/0013_live_feeds.py` | Create | `lightning_recent`, `glm_files_seen`, `lightning_coverage_periods`, `snotel_stations`, `nws_zone_geoms`; drop `area_weekly_weather`. |
| `backend/app/models/feeds.py` | Create | Models. |
| `backend/app/pipelines/open_meteo.py` | Modify | `ForecastClient`. |
| `backend/app/pipelines/forecast.py` | Create | Nightly forecast job. |
| `backend/app/pipelines/era5_rolling.py` | Create | 3-year route-cell backfill, weekly append, pruning. |
| `backend/app/pipelines/incident_weather_backfill.py` | Modify | `run(..., source=...)` parameter. |
| `backend/app/pipelines/snotel.py` | Create | Stations, attachment, daily SWE/depth. |
| `backend/app/pipelines/nws.py` | Create | Active alerts, zone geometries, IEM archive. |
| `backend/app/pipelines/airnow.py` | Create | Hourly AQI files → daily max per bucket. |
| `backend/app/pipelines/glm.py` | Create | S3 listing, HDF5 parsing, live window, daily totals. |
| `backend/app/pipelines/nldn.py` | Create | Yearly tile files, backfill, overlap correlation. |
| `backend/app/pipelines/lightning_climatology.py` | Create | Lightning-day frequency into `cell_climatology`. |
| `backend/app/tasks/data_feeds.py` | Create | Celery tasks for forecast, alerts, AQI, GLM live. |
| `backend/app/celery_app.py` | Modify | `include` + beat entries. |
| `backend/app/main.py`, `backend/app/data_health.py` | Modify/Create | `GET /health/data`. |
| `.github/workflows/data-daily.yml`, `data-weekly.yml`, `data-backfill-lightning.yml` | Create | Batch schedules. |
| tests: `test_migration_0013.py`, `test_forecast.py`, `test_era5_rolling.py`, `test_snotel.py`, `test_nws.py`, `test_airnow.py`, `test_data_health.py`, `test_glm.py`, `test_nldn.py`, `test_lightning_climatology.py`, `test_data_feeds_tasks.py`; fixtures under `backend/tests/fixtures/` | Create | Tests (recorded/synthetic responses, no network). |
| `backend/pyproject.toml`, `backend/uv.lock`, grants/verify SQL, docs | Modify | `h5py`, mypy, grants, DEPLOYMENT/CLAUDE/CHANGELOG. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 2–13 | tables of `0013` | Task 1 first. |
| 2 | 3, 10 | `open_meteo.ForecastClient` next to `ArchiveClient` | Added without touching `ArchiveClient`. |
| 3 | plan 3 | `incident_weather_backfill.run` gains `source: str = SOURCE` | Backward-compatible keyword; plan 3's tests unchanged. |
| 2, 5, 6, 10 | 8 | `app/tasks/data_feeds.py`, `app/celery_app.py` beat schedule | Task 8 wires all tasks in one edit; earlier tasks expose plain async job functions only. |
| 7 | 10 | `/health/data` reads `lightning_recent` | Task 7 treats the GLM source as `never_run` until Task 10 lands. |
| 11, 12 | 13 | `cell_daily_conditions.lightning_*` | Task 13 only reads them. |
| 13 | plan 5 | `cell_climatology.lightning_day_freq` | Plan 5 leaves the column out of its upsert; only Task 13 writes it. |
| all | each other | grants/verify SQL, `pyproject.toml`, workflows | Serial. |

---

# PR 2b-3a — `feat/p2b-live-feeds`

### Task 1: Migration `0013` and models

**Files:**
- Create: `backend/alembic/versions/0013_live_feeds.py`, `backend/app/models/feeds.py`, `backend/tests/test_migration_0013.py`
- Modify: `backend/app/models/__init__.py`, `backend/pyproject.toml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Produces (DB): `lightning_recent(grid_bucket integer, window_end timestamptz, flashes integer NOT NULL CHECK >= 0, satellite text NOT NULL, PK (grid_bucket, window_end))`; `glm_files_seen(s3_key text PK, processed_at timestamptz NOT NULL DEFAULT now())`; `lightning_coverage_periods(source text, start_date date, end_date date NULL, max_lat real NOT NULL, PK (source, start_date))` seeded with `('nldn','1989-01-01','2017-12-31',54)` and `('glm','2018-01-01',NULL,54)`; `snotel_stations(triplet text PK, name text, lat double precision NOT NULL, lon double precision NOT NULL, elevation_m real, active boolean NOT NULL, updated_at timestamptz DEFAULT now())`; `nws_zone_geoms(zone_id text PK, geom geography NOT NULL, fetched_at timestamptz DEFAULT now())`; `area_weekly_weather` dropped (guarded: refuses if any longitude > 0 remains, proving R7 ran).

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0013.py`:

```python
import asyncio

import pytest
from alembic import command

from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migration_0007 import _fetch
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg


def test_0013_seeds_coverage_periods_and_drops_area_weekly():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))
        rows = asyncio.run(_fetch(pg_url(name), "SELECT source, max_lat FROM lightning_coverage_periods ORDER BY 1"))
        assert [(r["source"], r["max_lat"]) for r in rows] == [("glm", 54.0), ("nldn", 54.0)]
        rows = asyncio.run(_fetch(pg_url(name), "SELECT to_regclass('public.area_weekly_weather') AS t"))
        assert rows[0]["t"] is None


def test_0013_refuses_while_r7_is_unapplied():
    with migrated_db("0012_objectives") as name:
        run_sql(name, "INSERT INTO area_weekly_weather (latitude, longitude, week_start, week_end) VALUES (40.1, 113.2, '2020-01-06', '2020-01-12')")
        with pytest.raises(RuntimeError, match="R7"):
            command.upgrade(_alembic_cfg(name), "head")
```


- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/alembic/versions/0013_live_feeds.py`:

```python
"""Live feeds (P2-6/P2-7): recent lightning, processed GLM files, lightning coverage
periods, SNOTEL stations, NWS zone geometries; area_weekly_weather retired (spec 2b-3)."""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision = "0013_live_feeds"
down_revision = "0012_objectives"
branch_labels = None
depends_on = None


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
    )
    op.create_table(
        "glm_files_seen",
        sa.Column("s3_key", sa.Text(), primary_key=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "lightning_coverage_periods",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("max_lat", sa.REAL(), nullable=False),
        sa.PrimaryKeyConstraint("source", "start_date"),
    )
    op.execute(
        "INSERT INTO lightning_coverage_periods (source, start_date, end_date, max_lat) VALUES "
        "('nldn', '1989-01-01', '2017-12-31', 54), ('glm', '2018-01-01', NULL, 54)"
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


def downgrade() -> None:
    raise NotImplementedError("0013 is one-way: area_weekly_weather was dropped (its pre-2a dump is the backup)")
```

`backend/app/models/feeds.py`: `LightningRecent`, `GlmFileSeen`, `LightningCoveragePeriod`, `SnotelStation`, `NwsZoneGeom` mirroring the migration (and the GiST index). Register; models mypy block.

Grants ("Plan 7 (0013)"):

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON public.lightning_recent, public.glm_files_seen, public.snotel_stations, public.nws_zone_geoms TO ingest;
GRANT DELETE ON public.cell_daily_conditions TO ingest;
GRANT SELECT ON public.lightning_recent, public.lightning_coverage_periods TO trainer;
```

(the `DELETE` is for Decision 7's pruning). Remove the Plan 3 line `GRANT SELECT, UPDATE (longitude) ON public.area_weekly_weather TO ingest;` (the table is gone). `verify_roles_phase2.sql`: matching `ingest_writes` rows, plus `('public.cell_daily_conditions','DELETE')`.

- [ ] **Step 4: Run** — PASS (`test_area_weekly.py` from plan 3 migrates to `head`; change its fixture to `migrated_db("0012_objectives", …)` in this commit so R7's tests still find the table).
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0013_live_feeds.py backend/app/models/ backend/tests/test_migration_0013.py backend/tests/test_area_weekly.py backend/pyproject.toml backend/db/roles/ && git commit -m "feat(db): 0013 live-feed tables; retire area_weekly_weather after R7"`

---

### Task 2: Nightly forecast

**Files:**
- Modify: `backend/app/pipelines/open_meteo.py`, `backend/tests/test_open_meteo.py`
- Create: `backend/app/pipelines/forecast.py`, `backend/tests/test_forecast.py`

**Interfaces:**
- Produces: `FORECAST_PUBLIC_URL = "https://api.open-meteo.com/v1/forecast"`, `FORECAST_CUSTOMER_URL = "https://customer-api.open-meteo.com/v1/forecast"`, `class ForecastClient(ArchiveClient)` with `fetch_forecast(locations: Sequence[Location], days: int = 3) -> list[LocationResponse]`; `FORECAST_DAYS = 3`, `async route_buckets(conn) -> list[int]`, `async run_forecast(engine_factory, client, *, today: date, batch: int = 100) -> dict[str, object]` (writes `is_forecast=True`, `model='best_match'`, `source='open_meteo_forecast'` via `upsert_rows`; logs `source_ingest_log` source `open_meteo_forecast` with window today..today+2).

- [ ] **Step 1: Failing tests** — append to `test_open_meteo.py`:

```python
from app.pipelines.open_meteo import ForecastClient


def test_forecast_parameters():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params), path=request.url.path, host=request.url.host)
        return httpx.Response(200, json=[_payload(40.0, -105.3, ["2026-09-28", "2026-09-29", "2026-09-30"], [1.0, 2.0, 3.0])])

    out = ForecastClient(None, transport=httpx.MockTransport(handler)).fetch_forecast(LOC[:1])
    assert len(out[0].daily.time) == 3
    assert seen["forecast_days"] == "3" and seen["wind_speed_unit"] == "ms" and seen["host"] == "api.open-meteo.com"
    assert "start_date" not in seen and "models" not in seen
```

`backend/tests/test_forecast.py`:

```python
import asyncio
from datetime import date

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.forecast import run_forecast
from app.pipelines.grid import grid_bucket
from app.pipelines.open_meteo import DAILY_VARS, ForecastClient
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)


def handler(request: httpx.Request) -> httpx.Response:
    lats = request.url.params["latitude"].split(",")
    days = ["2026-09-28", "2026-09-29", "2026-09-30"]
    body = [{"latitude": float(la), "longitude": 0.0,
             "daily": {"time": days, **{v: [1.0, 2.0, 3.0] for v in DAILY_VARS}}} for la in lats]
    return httpx.Response(200, json=body)


@requires_pg
def test_forecast_rows_are_written_for_route_buckets():
    b = grid_bucket(40.0, -105.3)
    seed = (f"INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, feature_version) "
            f"VALUES ('40.00000:-105.30000', 40.0, -105.3, {b}, 'x', 'y', 'f-v1');")

    async def scenario(url: str) -> list[tuple[object, ...]]:
        result = await run_forecast(lambda: create_async_engine(url), ForecastClient(None, transport=httpx.MockTransport(handler)), today=TODAY)
        assert result["status"] == "ok"
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return [tuple(r) for r in (await conn.execute(text(
                    "SELECT date, is_forecast, source FROM cell_daily_conditions ORDER BY date"))).all()]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        rows = asyncio.run(scenario(sa_url(name)))
    assert rows == [(date(2026, 9, d), True, "open_meteo_forecast") for d in (28, 29, 30)]
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** — in `open_meteo.py`:

```python
FORECAST_PUBLIC_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_CUSTOMER_URL = "https://customer-api.open-meteo.com/v1/forecast"


class ForecastClient(ArchiveClient):
    def __init__(self, api_key: str | None, **kwargs: Any) -> None:
        super().__init__(api_key, **kwargs)
        self._url = FORECAST_CUSTOMER_URL if api_key else FORECAST_PUBLIC_URL

    def fetch_forecast(self, locations: Sequence[Location], days: int = 3) -> list[LocationResponse]:
        params = {
            "latitude": ",".join(f"{loc.lat:.1f}" for loc in locations),
            "longitude": ",".join(f"{loc.lon:.1f}" for loc in locations),
            "daily": ",".join(DAILY_VARS),
            "forecast_days": str(days),
            "timezone": "GMT",
            "wind_speed_unit": "ms",
        }
        return self._get(params, len(locations))
```

and refactor `ArchiveClient.fetch` so its retry loop lives in a shared `_get(self, params: dict[str, str], expected: int) -> list[LocationResponse]` (it adds `apikey` when set); `fetch` builds its params and calls `_get`. Add `from typing import Any`.

`backend/app/pipelines/forecast.py`:

```python
"""Nightly Open-Meteo forecast for every route-bearing cell, before the 02:00 scoring run
(P2-6). Forecast rows never replace archive rows (cell_conditions.upsert_rows)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.cell_conditions import day_rows, upsert_rows
from app.pipelines.grid import bucket_center
from app.pipelines.ingest_log import finish_run, start_run, write_quarantine
from app.pipelines.open_meteo import ForecastClient, Location, OpenMeteoError
from app.pipelines.validate import ValidationReport

SOURCE = "open_meteo_forecast"
FORECAST_DAYS = 3


async def route_buckets(conn: AsyncConnection) -> list[int]:
    return sorted(int(b) for (b,) in (await conn.execute(text("SELECT DISTINCT grid_bucket FROM feature_points"))).all())


async def run_forecast(
    engine_factory: Callable[[], AsyncEngine], client: ForecastClient, *, today: date, batch: int = 100
) -> dict[str, object]:
    engine = engine_factory()
    report = ValidationReport(SOURCE)
    problems: list[str] = []
    written = 0
    try:
        async with engine.begin() as conn:
            buckets = await route_buckets(conn)
            run_id: uuid.UUID = await start_run(conn, source=SOURCE, window_start=today,
                                                window_end=today + timedelta(days=FORECAST_DAYS - 1), content_sha256=None)
        for i in range(0, len(buckets), batch):
            group = buckets[i : i + batch]
            try:
                responses = client.fetch_forecast([Location(b, *bucket_center(b)) for b in group], FORECAST_DAYS)
            except OpenMeteoError as exc:
                problems.append(str(exc))
                continue
            rows = [r for b, resp in zip(group, responses)
                    for r in day_rows(b, resp, today=today, is_forecast=True, model="best_match", source=SOURCE, report=report)]
            async with engine.begin() as conn:
                written += await upsert_rows(conn, rows, run_id=run_id)
        status = "ok" if not problems else "failed"
        async with engine.begin() as conn:
            await write_quarantine(conn, run_id, report)
            await finish_run(conn, run_id, status=status, report=report, rows_upserted=written, problems=problems)
    finally:
        await engine.dispose()
    return {"status": status, "buckets": len(buckets), "rows": written, "problems": len(problems)}
```

A partial failure marks the run `failed` (so `/health/data` and healthchecks see it) while keeping every batch that did succeed. Append `"app.pipelines.forecast"` to strict mypy.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/open_meteo.py backend/app/pipelines/forecast.py backend/tests/test_open_meteo.py backend/tests/test_forecast.py backend/pyproject.toml && git commit -m "feat(pipelines): nightly Open-Meteo forecast for route cells"`

---

### Task 3: Rolling 3-year ERA5 history, weekly append, pruning

**Files:**
- Create: `backend/app/pipelines/era5_rolling.py`, `backend/tests/test_era5_rolling.py`
- Modify: `backend/app/pipelines/incident_weather_backfill.py` (`run(..., source: str = SOURCE)`), `backend/pyproject.toml`

**Interfaces:**
- Produces: `SOURCE = "era5_rolling"`, `YEARS = 3`, `window_chunks(buckets: Sequence[int], *, start: date, end: date, batch: int, days_per_chunk: int = 366) -> list[Chunk]`, `async incident_and_neighbour_buckets(conn) -> set[int]`, `async prune(conn, *, today: date) -> int`, CLI `python -m app.pipelines.era5_rolling backfill --max-units N | append --max-units N | prune`.

`append` covers `[max(date) of ok era5 rows per bucket set + 1, today − 5 d]`; to stay simple and resumable it asks for the last 14 days ending at `today − 5 d` every week (overlap is an idempotent upsert and costs one unit per location).

- [ ] **Step 1: Failing tests** — `backend/tests/test_era5_rolling.py`:

```python
from datetime import date

from app.pipelines.era5_rolling import window_chunks
from app.pipelines.grid import grid_bucket


def test_window_chunks_split_by_days_and_batch():
    chunks = window_chunks([1, 2, 3], start=date(2023, 9, 23), end=date(2026, 9, 23), batch=2, days_per_chunk=366)
    assert chunks[0].buckets == (1, 2) and chunks[0].start == date(2023, 9, 23)
    assert chunks[-1].buckets == (3,) and chunks[-1].end == date(2026, 9, 23)
    covered = {(c.buckets, c.start) for c in chunks}
    assert len(covered) == len(chunks)


def test_neighbour_math_matches_grid():
    from app.pipelines.era5_rolling import neighbours

    b = grid_bucket(40.0, -105.3)
    assert grid_bucket(40.1, -105.2) in neighbours(b) and len(neighbours(b)) == 9
```

- [ ] **Step 2: Implement** `backend/app/pipelines/era5_rolling.py`:

```python
"""Rolling 3-year ERA5 history for every route cell (spec: ~0.32M units once), a weekly
append of the days past the ERA5 lag, and pruning outside the window for cells that hold no
incident (incident history since 1990 is kept)."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.grid import LAT_STRIDE, grid_bucket_sql
from app.pipelines.incident_weather_backfill import ERA5_LAG_DAYS, Chunk, run

SOURCE = "era5_rolling"
YEARS = 3


def neighbours(bucket: int) -> set[int]:
    return {bucket + dlat * LAT_STRIDE + dlon for dlat in (-1, 0, 1) for dlon in (-1, 0, 1)}


def window_chunks(buckets: Sequence[int], *, start: date, end: date, batch: int, days_per_chunk: int = 366) -> list[Chunk]:
    ordered = sorted(set(buckets))
    chunks: list[Chunk] = []
    for i in range(0, len(ordered), batch):
        group = tuple(ordered[i : i + batch])
        cursor = start
        while cursor <= end:
            stop = min(cursor + timedelta(days=days_per_chunk - 1), end)
            chunks.append(Chunk(group, cursor, stop))
            cursor = stop + timedelta(days=1)
    return chunks


async def incident_and_neighbour_buckets(conn: AsyncConnection) -> set[int]:
    grid = grid_bucket_sql("latitude", "longitude")
    rows = (await conn.execute(text(
        f"SELECT DISTINCT {grid} FROM accidents_clean WHERE latitude IS NOT NULL AND longitude IS NOT NULL"))).all()
    return {n for (b,) in rows for n in neighbours(int(b))}


async def prune(conn: AsyncConnection, *, today: date) -> int:
    keep = sorted(await incident_and_neighbour_buckets(conn))
    cutoff = today - timedelta(days=YEARS * 365 + 30)
    result = await conn.execute(text(
        "DELETE FROM cell_daily_conditions WHERE date < :cutoff AND grid_bucket <> ALL(:keep)"),
        {"cutoff": cutoff, "keep": keep})
    return result.rowcount


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.config import settings
    from app.pipelines.db import ingest_engine
    from app.pipelines.forecast import route_buckets
    from app.pipelines.open_meteo import ArchiveClient
    from app.services.temporal_weighting import utc_today

    today = utc_today()
    end = today - timedelta(days=ERA5_LAG_DAYS)
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            if args.step == "prune":
                return {"pruned": await prune(conn, today=today)}
            buckets = await route_buckets(conn)
    finally:
        await engine.dispose()
    start = end - timedelta(days=YEARS * 365) if args.step == "backfill" else end - timedelta(days=13)
    chunks = window_chunks(buckets, start=start, end=end, batch=50, days_per_chunk=366 if args.step == "backfill" else 14)
    client = ArchiveClient(settings.OPEN_METEO_API_KEY)
    return await run(ingest_engine, client, chunks, today=today, max_units=args.max_units, source=f"{SOURCE}:{args.step}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["backfill", "append", "prune"])
    parser.add_argument("--max-units", type=int, default=0)
    print(json.dumps(asyncio.run(_main(parser.parse_args())), sort_keys=True))
```

In `incident_weather_backfill.run`, add `source: str = SOURCE` as a keyword parameter and use it for `find_completed` and `start_run`. Append `"app.pipelines.era5_rolling"` to strict mypy.

- [ ] **Step 3: Run** — PASS (plan 3's backfill tests unchanged). **Step 4: Commit** — `git add backend/app/pipelines/era5_rolling.py backend/app/pipelines/incident_weather_backfill.py backend/tests/test_era5_rolling.py backend/pyproject.toml && git commit -m "feat(pipelines): rolling 3-year ERA5 route-cell history, weekly append, pruning"`

---

### Task 4: SNOTEL snowpack

**Files:**
- Create: `backend/app/pipelines/snotel.py`, `backend/tests/test_snotel.py`, `backend/tests/fixtures/snotel_stations.json`, `backend/tests/fixtures/snotel_data.json`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"`, `ATTACH_KM = 30.0`, `ATTACH_DZ_M = 500.0`, `IN_TO_MM = 25.4`, `IN_TO_CM = 2.54`, `class SnotelClient(transport=None)` with `stations() -> list[Station]` and `daily(triplets: Sequence[str], start: date, end: date) -> dict[str, dict[date, tuple[float | None, float | None]]]` (SWE mm, depth cm), `attach(buckets: Mapping[int, tuple[float, float, float | None]], stations: Sequence[Station]) -> dict[int, str]`, `swe_delta(series: Mapping[date, tuple[float | None, float | None]], day: date) -> float | None`, `async run_snotel(engine_factory, client, *, today: date) -> dict[str, object]` (updates `swe_delta_mm`, `snow_depth_cm` on existing rows for today − 1).

- [ ] **Step 1: Record fixtures (agent)** — `curl -s "$AWDB/stations?networkCds=SNTL&activeOnly=true" | head -c 2000` to confirm fields (`stationTriplet`, `name`, `latitude`, `longitude`, `elevation` in feet); save a 3-station excerpt to `snotel_stations.json`; `curl -s "$AWDB/data?stationTriplets=<t>&elements=WTEQ,SNWD&duration=DAILY&beginDate=2026-01-01&endDate=2026-01-05"` → `snotel_data.json`. If field names differ, change only the pydantic models' aliases.

- [ ] **Step 2: Failing tests** — `backend/tests/test_snotel.py`:

```python
from datetime import date

from app.pipelines.snotel import Station, attach, swe_delta


def test_attach_requires_distance_and_elevation_match():
    stations = [Station("1:CO:SNTL", "Near High", 40.05, -105.3, 3200.0), Station("2:CO:SNTL", "Near Low", 40.02, -105.3, 1800.0)]
    buckets = {1: (40.0, -105.3, 3000.0), 2: (41.0, -105.3, 3000.0), 3: (40.0, -105.3, None)}
    assert attach(buckets, stations) == {1: "1:CO:SNTL"}


def test_swe_delta_is_three_day_change_and_null_when_missing():
    s = {date(2026, 1, 1): (100.0, 50.0), date(2026, 1, 4): (130.0, 60.0), date(2026, 1, 5): (None, 60.0)}
    assert swe_delta(s, date(2026, 1, 4)) == 30.0
    assert swe_delta(s, date(2026, 1, 5)) is None
```

(Buckets with unknown elevation are not attached: the elevation rule cannot be checked.)

- [ ] **Step 3: Implement** `backend/app/pipelines/snotel.py`:

```python
"""NRCS SNOTEL (public domain) snow water equivalent and depth, attached to a bucket only
when a station is within 30 km and ±500 m of the bucket's route elevation (spec)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.pipelines.geo import haversine_km
from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport, range_problem

AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
ATTACH_KM = 30.0
ATTACH_DZ_M = 500.0
IN_TO_MM = 25.4
IN_TO_CM = 2.54
FT_TO_M = 0.3048


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
        response = self._client.get(f"{AWDB}/stations", params={"networkCds": "SNTL", "activeOnly": "true"})
        response.raise_for_status()
        return [Station(s["stationTriplet"], s.get("name") or "", float(s["latitude"]), float(s["longitude"]),
                        float(s["elevation"]) * FT_TO_M if s.get("elevation") is not None else None)
                for s in response.json()]

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


def attach(buckets: Mapping[int, tuple[float, float, float | None]], stations: Sequence[Station]) -> dict[int, str]:
    chosen: dict[int, str] = {}
    for b, (lat, lon, elev) in buckets.items():
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
            chosen[b] = best[1]
    return chosen


def swe_delta(series: Mapping[date, tuple[float | None, float | None]], day: date) -> float | None:
    now, before = series.get(day, (None, None))[0], series.get(day - timedelta(days=3), (None, None))[0]
    return None if now is None or before is None else now - before


async def run_snotel(engine_factory: Callable[[], AsyncEngine], client: SnotelClient, *, today: date) -> dict[str, object]:
    day = today - timedelta(days=1)
    engine = engine_factory()
    report = ValidationReport("snotel")
    try:
        async with engine.begin() as conn:
            buckets = {int(b): (float(la), float(lo), float(e) if e is not None else None) for b, la, lo, e in (await conn.execute(text(
                "SELECT grid_bucket, avg(lat), avg(lon), avg(elevation_m) FROM feature_points GROUP BY grid_bucket"))).all()}
            run_id: uuid.UUID = await start_run(conn, source="snotel", window_start=day, window_end=day, content_sha256=None)
        mapping = attach(buckets, client.stations())
        triplets = sorted(set(mapping.values()))
        data: dict[str, dict[date, tuple[float | None, float | None]]] = {}
        for i in range(0, len(triplets), 100):
            data |= client.daily(triplets[i : i + 100], day - timedelta(days=3), day)
        updates = []
        for b, t in mapping.items():
            series = data.get(t, {})
            depth = series.get(day, (None, None))[1]
            delta = swe_delta(series, day)
            if depth is None and delta is None:
                report.quarantine(str(b), "no_station_value")
                continue
            report.accept()
            updates.append({"b": b, "d": day, "swe": delta, "depth": depth})
        async with engine.begin() as conn:
            if updates:
                await conn.execute(text(
                    "UPDATE cell_daily_conditions SET swe_delta_mm = :swe, snow_depth_cm = :depth WHERE grid_bucket = :b AND date = :d"),
                    updates)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=len(updates))
    finally:
        await engine.dispose()
    return {"attached": len(mapping), "updated": len(updates)}
```

Only existing rows (yesterday's forecast or archive row) are updated; a bucket without a row that day gets nothing rather than a row with only snow columns. Add a CLI (`python -m app.pipelines.snotel`) that calls `run_snotel(ingest_engine, SnotelClient(), today=utc_today())` and prints the JSON. Append `"app.pipelines.snotel"` to strict mypy.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/snotel.py backend/tests/test_snotel.py backend/tests/fixtures/snotel_*.json backend/pyproject.toml && git commit -m "feat(pipelines): SNOTEL SWE change and snow depth per attached bucket"`

---

### Task 5: NWS alerts (live) and IEM polygon-warning archive

**Files:**
- Create: `backend/app/pipelines/nws.py`, `backend/tests/test_nws.py`, `backend/tests/fixtures/nws_active.json`, `backend/tests/fixtures/iem_sbw.json`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `NWS_URL = "https://api.weather.gov/alerts/active"`, `IEM_SBW = "https://mesonet.agron.iastate.edu/geojson/sbw.geojson"`, `USER_AGENT = "SafeAscent-data/1.0 (https://github.com/SebastianFrazier26/SafeAscent)"`, `@dataclass(frozen=True) Alert(alert_id: str, event: str, geometry: dict[str, object] | None, zones: tuple[str, ...], onset: datetime | None, ends: datetime | None)`, `class NwsClient(transport=None)` with `active() -> list[Alert]`, `zone_geometry(zone_url: str) -> dict[str, object] | None`, `iem_polygons(start: datetime, end: datetime) -> list[Alert]`; `async apply_alerts(conn, alerts: Sequence[Alert], *, day: date) -> int` (writes the sorted distinct event names into `nws_alert_codes` of each route bucket whose center lies in an alert area, on that day's existing row); CLI `python -m app.pipelines.nws live | archive --start YYYY-MM-DD --end YYYY-MM-DD`.

- [ ] **Step 1: Record fixtures (agent)** — `curl -s -H "User-Agent: SafeAscent-data/1.0 (https://github.com/SebastianFrazier26/SafeAscent)" https://api.weather.gov/alerts/active?status=actual | python3 -c 'import json,sys; d=json.load(sys.stdin); d["features"]=d["features"][:3]; print(json.dumps(d))' > backend/tests/fixtures/nws_active.json` (public domain); `curl -s "https://mesonet.agron.iastate.edu/geojson/sbw.geojson?sts=2024-07-01T00:00Z&ets=2024-07-01T06:00Z" | python3 -c '…same trim…' > backend/tests/fixtures/iem_sbw.json`; confirm property names (`id`, `event`, `affectedZones`, `onset`, `ends` for NWS; `phenomena`, `significance`, `issue`, `expire` for IEM) and adjust the two `_to_alert` functions only.

- [ ] **Step 2: Failing tests** — `backend/tests/test_nws.py`:

```python
import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.grid import grid_bucket
from app.pipelines.nws import Alert, NwsClient, apply_alerts
from tests.pgtest import migrated_db, requires_pg, sa_url

FIX = Path(__file__).parent / "fixtures"


def test_active_alerts_parse_and_send_a_user_agent():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json=json.loads((FIX / "nws_active.json").read_text()))

    alerts = NwsClient(transport=httpx.MockTransport(handler)).active()
    assert alerts and all(a.event for a in alerts)
    assert "SafeAscent" in seen[0]


@requires_pg
def test_polygon_alert_marks_the_bucket_row():
    b = grid_bucket(40.0, -105.3)
    seed = (f"INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, feature_version) VALUES "
            f"('40.00000:-105.30000', 40.0, -105.3, {b}, 'x', 'y', 'f-v1');"
            f"INSERT INTO cell_daily_conditions (grid_bucket, date, source) VALUES ({b}, '2026-09-28', 'open_meteo_forecast');")
    square = {"type": "Polygon", "coordinates": [[[-105.5, 39.8], [-105.0, 39.8], [-105.0, 40.2], [-105.5, 40.2], [-105.5, 39.8]]]}
    alert = Alert("a1", "Winter Storm Warning", square, (), None, None)

    async def go(url: str) -> list[object]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                assert await apply_alerts(conn, [alert], day=date(2026, 9, 28)) == 1
                return list((await conn.execute(text("SELECT nws_alert_codes FROM cell_daily_conditions"))).scalar_one())
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        assert asyncio.run(go(sa_url(name))) == ["Winter Storm Warning"]
```

- [ ] **Step 3: Implement** `backend/app/pipelines/nws.py`:

```python
"""NWS active alerts (hourly) and IEM storm-based-warning polygons (history), mapped to
route cells by bucket center. Zone-only alerts use cached zone geometries."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

NWS_URL = "https://api.weather.gov/alerts/active"
IEM_SBW = "https://mesonet.agron.iastate.edu/geojson/sbw.geojson"
USER_AGENT = "SafeAscent-data/1.0 (https://github.com/SebastianFrazier26/SafeAscent)"


@dataclass(frozen=True)
class Alert:
    alert_id: str
    event: str
    geometry: dict[str, object] | None
    zones: tuple[str, ...]
    onset: datetime | None
    ends: datetime | None


def _dt(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None


class NwsClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(transport=transport, timeout=60.0,
                                    headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})

    def active(self) -> list[Alert]:
        response = self._client.get(NWS_URL, params={"status": "actual", "message_type": "alert"})
        response.raise_for_status()
        out = []
        for f in response.json().get("features", []):
            p = f.get("properties", {})
            out.append(Alert(str(p.get("id") or f.get("id")), str(p.get("event") or ""), f.get("geometry"),
                             tuple(p.get("affectedZones") or ()), _dt(p.get("onset")), _dt(p.get("ends") or p.get("expires"))))
        return [a for a in out if a.event]

    def zone_geometry(self, zone_url: str) -> dict[str, object] | None:
        if not zone_url.startswith("https://api.weather.gov/zones/"):
            return None
        response = self._client.get(zone_url)
        if response.status_code != 200:
            return None
        geometry = response.json().get("geometry")
        return geometry if isinstance(geometry, dict) else None

    def iem_polygons(self, start: datetime, end: datetime) -> list[Alert]:
        response = self._client.get(IEM_SBW, params={"sts": start.strftime("%Y-%m-%dT%H:%MZ"), "ets": end.strftime("%Y-%m-%dT%H:%MZ")})
        response.raise_for_status()
        out = []
        for f in response.json().get("features", []):
            p = f.get("properties", {})
            event = f"{p.get('phenomena', '')}.{p.get('significance', '')}".strip(".")
            out.append(Alert(str(f.get("id") or p.get("product_id")), event, f.get("geometry"), (), _dt(p.get("issue")), _dt(p.get("expire"))))
        return [a for a in out if a.event and a.geometry]


async def apply_alerts(conn: AsyncConnection, alerts: Sequence[Alert], *, day: date) -> int:
    touched: dict[int, set[str]] = {}
    for alert in alerts:
        if alert.geometry is not None:
            sql = (f"SELECT DISTINCT p.grid_bucket FROM feature_points p WHERE ST_Intersects("
                   f"ST_GeomFromGeoJSON(:g)::geography, ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326)::geography)")
            buckets = [int(b) for (b,) in (await conn.execute(text(sql), {"g": json.dumps(alert.geometry)})).all()]
        elif alert.zones:
            ids = [z.rsplit("/", 1)[-1] for z in alert.zones]
            buckets = [int(b) for (b,) in (await conn.execute(text(
                "SELECT DISTINCT p.grid_bucket FROM feature_points p JOIN nws_zone_geoms z ON z.zone_id = ANY(:ids) "
                "AND ST_Intersects(z.geom, ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326)::geography)"), {"ids": ids})).all()]
        else:
            buckets = []
        for b in buckets:
            touched.setdefault(b, set()).add(alert.event)
    if touched:
        await conn.execute(text(
            "UPDATE cell_daily_conditions SET nws_alert_codes = (SELECT array_agg(DISTINCT c ORDER BY c) FROM "
            "unnest(coalesce(nws_alert_codes, '{}') || CAST(:codes AS text[])) c) WHERE grid_bucket = :b AND date = :d"),
            [{"b": b, "d": day, "codes": sorted(codes)} for b, codes in touched.items()])
    return len(touched)
```

Add `async ensure_zone_geoms(conn, client, alerts) -> int` that inserts missing `nws_zone_geoms` rows for every zone URL of geometry-less alerts (`ST_GeomFromGeoJSON(:g)::geography`, `ON CONFLICT DO NOTHING`), and the CLI: `live` = `ensure_zone_geoms` + `apply_alerts(…, day=utc_today())` with a `source_ingest_log` row `nws_alerts`; `archive` = for each day in `[--start, --end]` that has incident rows in `accident_conditions`, `iem_polygons(day 00Z, day+1 00Z)` → `apply_alerts(day=…)`, logged per day as `iem_sbw` (skip completed days via `find_completed`). Append `"app.pipelines.nws"` to strict mypy.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/nws.py backend/tests/test_nws.py backend/tests/fixtures/nws_active.json backend/tests/fixtures/iem_sbw.json backend/pyproject.toml && git commit -m "feat(pipelines): NWS active alerts and IEM polygon-warning archive per route cell"`

---

### Task 6: AirNow hourly AQI

**Files:**
- Create: `backend/app/pipelines/airnow.py`, `backend/tests/test_airnow.py`, `backend/tests/fixtures/airnow_hourly.dat`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `FILES = "https://files.airnowtech.org/airnow"`, `SITE_RADIUS_KM = 50.0`, `hour_url(ts: datetime) -> str`, `parse_hourly(text: str) -> list[Site]` (`Site(lat, lon, aqi: int | None)`; AQI = max of the `*_AQI` columns present), `bucket_aqi(buckets: Mapping[int, tuple[float, float]], sites: Sequence[Site]) -> dict[int, int]`, `async run_airnow(engine_factory, client: httpx.Client, *, now: datetime) -> dict[str, object]` (fetches the last complete hour, raises today's `aqi` to `GREATEST(aqi, new)` on existing rows).

- [ ] **Step 1: Record the fixture (agent)** — download one file, e.g. `curl -s "https://files.airnowtech.org/airnow/2026/20260927/HourlyAQObs_2026092718.dat" | head -5 > backend/tests/fixtures/airnow_hourly.dat`; confirm the header names (`Latitude`, `Longitude`, `OZONE_AQI`, `PM25_AQI`, `PM10_AQI`) and update `AQI_COLUMNS` if they differ; confirm `hour_url` matches the real path.

- [ ] **Step 2: Failing tests** — `backend/tests/test_airnow.py`:

```python
import asyncio
from datetime import datetime, timezone

import httpx

from app.pipelines.airnow import Site, bucket_aqi, hour_url, parse_hourly, run_airnow

TEXT = ('"AQSID","SiteName","Latitude","Longitude","OZONE_AQI","PM25_AQI","PM10_AQI"\n'
        '"1","A","40.01","-105.31","35","61",""\n"2","B","41.5","-105.3","","",""\n')


def test_parse_takes_the_max_available_aqi_and_keeps_missing_as_none():
    assert parse_hourly(TEXT) == [Site(40.01, -105.31, 61), Site(41.5, -105.3, None)]


def test_bucket_aqi_uses_sites_within_50_km():
    assert bucket_aqi({1: (40.0, -105.3), 2: (38.0, -105.3)}, parse_hourly(TEXT)) == {1: 61}


def test_hour_url():
    assert hour_url(datetime(2026, 9, 27, 18, tzinfo=timezone.utc)).endswith("/2026/20260927/HourlyAQObs_2026092718.dat")


def test_missing_file_leaves_aqi_null():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    result = asyncio.run(run_airnow(lambda: (_ for _ in ()).throw(AssertionError("no DB write on a missing file")), client,
                                    now=datetime(2026, 9, 27, 19, 5, tzinfo=timezone.utc)))
    assert result == {"status": "missing_file", "hour": "2026092718"}
```

- [ ] **Step 3: Implement** `backend/app/pipelines/airnow.py`:

```python
"""AirNow hourly observation files (public) → daily maximum AQI per route cell."""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.pipelines.geo import haversine_km
from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport, coord_problem

FILES = "https://files.airnowtech.org/airnow"
SITE_RADIUS_KM = 50.0
AQI_COLUMNS = ("OZONE_AQI", "PM25_AQI", "PM10_AQI", "NO2_AQI", "CO_AQI", "SO2_AQI")


@dataclass(frozen=True)
class Site:
    lat: float
    lon: float
    aqi: int | None


def hour_url(ts: datetime) -> str:
    return f"{FILES}/{ts:%Y}/{ts:%Y%m%d}/HourlyAQObs_{ts:%Y%m%d%H}.dat"


def parse_hourly(body: str) -> list[Site]:
    sites = []
    for row in csv.DictReader(io.StringIO(body)):
        try:
            lat, lon = float(row["Latitude"]), float(row["Longitude"])
        except (KeyError, ValueError):
            continue
        if coord_problem(lat, lon) is not None:
            continue
        values = [int(float(row[c])) for c in AQI_COLUMNS if (row.get(c) or "").strip() not in ("", "-999")]
        values = [v for v in values if 0 <= v <= 999]
        sites.append(Site(lat, lon, max(values) if values else None))
    return sites


def bucket_aqi(buckets: Mapping[int, tuple[float, float]], sites: Sequence[Site]) -> dict[int, int]:
    out: dict[int, int] = {}
    for b, (lat, lon) in buckets.items():
        near = [s.aqi for s in sites if s.aqi is not None and haversine_km(lat, lon, s.lat, s.lon) <= SITE_RADIUS_KM]
        if near:
            out[b] = max(near)
    return out


async def run_airnow(engine_factory: Callable[[], AsyncEngine], client: httpx.Client, *, now: datetime) -> dict[str, object]:
    hour = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    response = client.get(hour_url(hour))
    if response.status_code != 200:
        return {"status": "missing_file", "hour": f"{hour:%Y%m%d%H}"}
    sites = parse_hourly(response.text)
    engine = engine_factory()
    report = ValidationReport("airnow")
    try:
        async with engine.begin() as conn:
            centers = {int(b): (float(la), float(lo)) for b, la, lo in (await conn.execute(text(
                "SELECT grid_bucket, avg(lat), avg(lon) FROM feature_points GROUP BY grid_bucket"))).all()}
            values = bucket_aqi(centers, sites)
            run_id = await start_run(conn, source="airnow", window_start=hour.date(), window_end=hour.date(), content_sha256=None)
            if values:
                await conn.execute(text(
                    "UPDATE cell_daily_conditions SET aqi = GREATEST(coalesce(aqi, 0), :aqi) WHERE grid_bucket = :b AND date = :d"),
                    [{"b": b, "aqi": v, "d": hour.date()} for b, v in values.items()])
            report.rows_in = report.accepted = len(values)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=len(values))
    finally:
        await engine.dispose()
    return {"status": "ok", "hour": f"{hour:%Y%m%d%H}", "buckets": len(values)}
```

`GREATEST(coalesce(aqi, 0), :aqi)` only runs where a site value exists, so it never turns NULL into 0 for a bucket without a site. A missing file returns before any database work; the Celery wrapper (Task 8) pings `/fail` on `missing_file` so repeated outages alert. Append `"app.pipelines.airnow"` to strict mypy.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/airnow.py backend/tests/test_airnow.py backend/tests/fixtures/airnow_hourly.dat backend/pyproject.toml && git commit -m "feat(pipelines): AirNow hourly AQI to daily max per route cell"`

---

### Task 7: `GET /health/data`

**Files:**
- Create: `backend/app/data_health.py`, `backend/tests/test_data_health.py`
- Modify: `backend/app/main.py`, `backend/pyproject.toml`

**Interfaces:**
- Produces: `THRESHOLDS: dict[str, timedelta] = {"open_meteo_forecast": 26h, "nws_alerts": 2h, "glm_live": 30min, "openbeta_weekly": 9d}`, `evaluate(last_ok: Mapping[str, datetime | None], now: datetime) -> dict[str, dict[str, object]]` (`status` ∈ `ok|stale|never_run`), `async read_last_ok(db: AsyncSession) -> dict[str, datetime | None]` (GLM from `max(lightning_recent.window_end)`, others from `source_ingest_log`), route `GET /health/data` → 200 or 503 with the per-source JSON (no counts of personal data, no URLs).

- [ ] **Step 1: Failing tests** — `backend/tests/test_data_health.py`:

```python
from datetime import datetime, timedelta, timezone

from app.data_health import evaluate

NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def test_never_run_is_not_stale():
    result = evaluate({"open_meteo_forecast": None, "nws_alerts": NOW - timedelta(minutes=30), "glm_live": None, "openbeta_weekly": None}, NOW)
    assert result["open_meteo_forecast"]["status"] == "never_run"
    assert all(v["status"] != "stale" for v in result.values())


def test_stale_after_threshold():
    result = evaluate({"open_meteo_forecast": NOW - timedelta(hours=27), "nws_alerts": NOW, "glm_live": NOW - timedelta(minutes=31),
                       "openbeta_weekly": NOW - timedelta(days=8)}, NOW)
    assert {k: v["status"] for k, v in result.items()} == {
        "open_meteo_forecast": "stale", "nws_alerts": "ok", "glm_live": "stale", "openbeta_weekly": "ok"}
```

- [ ] **Step 2: Implement** `backend/app/data_health.py`:

```python
"""Data freshness for GET /health/data (spec monitoring). A source that has never run is
reported, not treated as stale, so a fresh deployment does not page; once it has run,
missing its threshold returns 503."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

THRESHOLDS: dict[str, timedelta] = {
    "open_meteo_forecast": timedelta(hours=26),
    "nws_alerts": timedelta(hours=2),
    "glm_live": timedelta(minutes=30),
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
        {"s": [s for s in THRESHOLDS if s != "glm_live"]})).all()
    last: dict[str, datetime | None] = {s: None for s in THRESHOLDS}
    last |= {str(s): t for s, t in rows}
    last["glm_live"] = (await db.execute(text(
        "SELECT max(window_end) FROM lightning_recent"))).scalar_one_or_none()
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

(import `evaluate`, `read_last_ok` from `app.data_health`, and `JSONResponse`, `Depends`, `AsyncSession`, `get_db`, `datetime`, `timezone` as the file needs). Add a DB-backed route test in `test_data_health.py` using `tests.pgtest.migrated_db` and the app's dependency override pattern from `test_ascent_analytics.py`, asserting 200 with all `never_run` on an empty database and 503 after inserting an `ok` `open_meteo_forecast` row finished 27 hours ago. Append `"app.data_health"` to strict mypy.

- [ ] **Step 3: Run** — PASS. **Step 4: Commit** — `git add backend/app/data_health.py backend/app/main.py backend/tests/test_data_health.py backend/pyproject.toml && git commit -m "feat(api): /health/data freshness endpoint with per-source thresholds"`

---

### Task 8: Celery wiring, batch workflows, PR 2b-3a docs

**Files:**
- Create: `backend/app/tasks/data_feeds.py`, `backend/tests/test_data_feeds_tasks.py`, `.github/workflows/data-daily.yml`, `.github/workflows/data-weekly.yml`
- Modify: `backend/app/celery_app.py`, `backend/pyproject.toml`, `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`

**Interfaces:**
- Produces: Celery tasks `app.tasks.data_feeds.fetch_forecast`, `.fetch_alerts`, `.fetch_airnow` (and `.glm_live` in Task 10), each: `job_ping(slug, "/start")` → run → `job_ping(slug)` or `"/fail"`; beat entries `forecast-nightly` (01:00 UTC, `expires` 3600), `nws-alerts` (hourly at :05, `expires` 1800), `airnow` (hourly at :35, `expires` 1800).

- [ ] **Step 1: Failing test** — `backend/tests/test_data_feeds_tasks.py`:

```python
from celery.schedules import crontab

from app.celery_app import celery_app


def test_beat_schedules_the_feeds_with_expiry():
    schedule = celery_app.conf.beat_schedule
    assert schedule["forecast-nightly"]["schedule"] == crontab(minute=0, hour=1)
    for key in ("forecast-nightly", "nws-alerts", "airnow"):
        assert schedule[key]["options"]["expires"] <= 3600
        assert schedule[key]["task"].startswith("app.tasks.data_feeds.")


def test_tasks_are_registered():
    import app.tasks.data_feeds  # noqa: F401

    for name in ("fetch_forecast", "fetch_alerts", "fetch_airnow"):
        assert f"app.tasks.data_feeds.{name}" in celery_app.tasks
```

- [ ] **Step 2: Implement** `backend/app/tasks/data_feeds.py`:

```python
"""Time-critical data feeds on the worker (P2-6). Each task pings its healthchecks.io slug
and connects as the ingest role; a failure re-raises after the /fail ping."""

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
    from app.services.temporal_weighting import utc_today

    return _run("forecast-nightly", lambda: run_forecast(ingest_engine, ForecastClient(settings.OPEN_METEO_API_KEY), today=utc_today()))


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

(`nws.run_live() -> dict[str, object]` is the async function behind the `live` CLI from Task 5; export it under that name.) In `celery_app.py`: add `"app.tasks.data_feeds"` to `include` and:

```python
    # Before the 02:00 scoring run; forecast rows never replace archive rows.
    "forecast-nightly": {
        "task": "app.tasks.data_feeds.fetch_forecast",
        "schedule": crontab(minute=0, hour=1),
        "options": {"expires": 3600},
    },
    "nws-alerts": {"task": "app.tasks.data_feeds.fetch_alerts", "schedule": crontab(minute=5), "options": {"expires": 1800}},
    "airnow": {"task": "app.tasks.data_feeds.fetch_airnow", "schedule": crontab(minute=35), "options": {"expires": 1800}},
```

Add `"app.tasks.data_feeds"` to the celery-adjacent mypy block.

`.github/workflows/data-daily.yml` (daily 09:10 UTC; same header/env/steps pattern as `data-openbeta.yml`) runs: `python -m app.pipelines.snotel`. `.github/workflows/data-weekly.yml` (Tuesdays 07:20 UTC) runs: `python -m app.pipelines.era5_rolling append --max-units 50000`, `python -m app.pipelines.era5_rolling prune`, `python -m app.pipelines.mp_ticks_quarantine`, `python -m app.pipelines.mp_tick_counts`. Both open an issue on failure (same step as `data-openbeta.yml`).

Docs: CHANGELOG "Phase 2b live feeds (PR 2b-3a)"; DEPLOYMENT.md: worker env `INGEST_DATABASE_URL`, `HEALTHCHECKS_PING_KEY`, `OPEN_METEO_API_KEY`; beat entries and their slugs (`forecast-nightly` 24 h/2 h grace, `nws-alerts` 1 h/1 h, `airnow` 1 h/2 h); `/health/data`; new workflows. CLAUDE.md: Service topology mentions `/health/data`; commands for the new jobs.

- [ ] **Step 3: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd .. && python scripts/check_compose_matches_railway.py` → green.
- [ ] **Step 4: Commit** — `git add backend/app/tasks/data_feeds.py backend/app/celery_app.py backend/tests/test_data_feeds_tasks.py backend/app/pipelines/nws.py .github/workflows/data-daily.yml .github/workflows/data-weekly.yml backend/pyproject.toml CHANGELOG.md CLAUDE.md DEPLOYMENT.md && git commit -m "feat(ops): beat-driven forecast/alerts/AQI, daily and weekly data workflows"`

---

# PR 2b-3b — `feat/p2b-lightning`

### Task 9: GLM listing and parsing

**Files:**
- Create: `backend/app/pipelines/glm.py`, `backend/tests/test_glm.py`
- Modify: `backend/pyproject.toml`, `backend/uv.lock`

**Interfaces:**
- Produces: `BUCKETS = {"east": "noaa-goes19", "west": "noaa-goes18", "east_history": "noaa-goes16"}`, `PREFIX = "GLM-L2-LCFA"`, `WEST_OF_LON = -105.0`, `list_keys(client: httpx.Client, bucket: str, hour: datetime) -> list[str]` (S3 ListObjectsV2 XML, paginated), `file_start(key: str) -> datetime` (from `_sYYYYJJJHHMMSSt`), `read_flashes(data: bytes) -> tuple[NDArray[np.float64], NDArray[np.float64]]` (lat, lon of good-quality flashes), `satellite_for(lon: float) -> Literal["east", "west"]`.

- [ ] **Step 1: Add the dependency** — `cd backend && uv add 'h5py>=3.11,<4'` (main dependency: the worker parses GLM files).

- [ ] **Step 2: Failing tests** — `backend/tests/test_glm.py`:

```python
import io
from datetime import datetime, timezone

import h5py
import httpx
import numpy as np

from app.pipelines.glm import file_start, list_keys, read_flashes, satellite_for

XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<IsTruncated>false</IsTruncated>
<Contents><Key>GLM-L2-LCFA/2026/271/14/OR_GLM-L2-LCFA_G19_s20262711400000_e20262711400200_c20262711400227.nc</Key></Contents>
</ListBucketResult>"""


def _h5(lat, lon, quality, packed=False) -> bytes:
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        if packed:
            f.create_dataset("flash_lat", data=np.round((np.array(lat) - 0.0) / 0.00203128).astype("int16"))
            f["flash_lat"].attrs["scale_factor"] = np.float32(0.00203128)
            f["flash_lat"].attrs["add_offset"] = np.float32(0.0)
            f.create_dataset("flash_lon", data=np.round((np.array(lon) + 0.0) / 0.00203128).astype("int16"))
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


def test_satellite_split():
    assert (satellite_for(-120.0), satellite_for(-80.0)) == ("west", "east")
```

- [ ] **Step 3: Implement** `backend/app/pipelines/glm.py`:

```python
"""GOES GLM L2 LCFA (NOAA, public on AWS): S3 listing over plain HTTPS (XML, not HTML),
HDF5 parsing of flash centroids, good-quality flashes only. Packed int16 coordinates in some
file versions are unpacked with their scale_factor/add_offset."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Literal

import h5py
import httpx
import numpy as np
from numpy.typing import NDArray

BUCKETS = {"east": "noaa-goes19", "west": "noaa-goes18", "east_history": "noaa-goes16"}
PREFIX = "GLM-L2-LCFA"
WEST_OF_LON = -105.0
_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
_START = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})\d_")


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
        if (root.findtext("s3:IsTruncated", default="false", namespaces=_NS)) != "true":
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


def satellite_for(lon: float) -> Literal["east", "west"]:
    return "west" if lon < WEST_OF_LON else "east"
```

Append `"app.pipelines.glm"` to strict mypy.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/glm.py backend/tests/test_glm.py backend/pyproject.toml backend/uv.lock && git commit -m "feat(pipelines): GLM S3 listing and HDF5 flash parsing"`

---

### Task 10: GLM live (10 minutes) → `lightning_recent`

**Files:**
- Modify: `backend/app/pipelines/glm.py`, `backend/tests/test_glm.py`, `backend/app/tasks/data_feeds.py`, `backend/app/celery_app.py`, `backend/tests/test_data_feeds_tasks.py`

**Interfaces:**
- Produces: `WINDOW = timedelta(minutes=30)`, `RETENTION = timedelta(hours=24)`, `count_by_bucket(lat, lon, *, satellite: Literal["east","west"], route_buckets: set[int]) -> dict[int, int]` (only buckets whose longitude side matches the satellite and whose latitude ≤54), `async run_live(engine_factory, client, *, now: datetime) -> dict[str, object]` (lists the last two hour prefixes on both satellites, skips keys in `glm_files_seen`, counts flashes with file start in `[now − 30 min, now]`, upserts `lightning_recent(bucket, window_end=now rounded down to 10 min)`, records processed keys, prunes rows older than 24 h and seen-keys older than 2 days); Celery task `app.tasks.data_feeds.glm_live` every 10 minutes (`expires` 540).

- [ ] **Step 1: Failing test** — append to `test_glm.py`:

```python
from app.pipelines.glm import count_by_bucket
from app.pipelines.grid import grid_bucket


def test_bucket_takes_one_satellite():
    b_west, b_east = grid_bucket(40.0, -110.0), grid_bucket(40.0, -100.0)
    lat = np.array([40.0, 40.0, 60.0]); lon = np.array([-110.0, -100.0, -150.0])
    route = {b_west, b_east, grid_bucket(60.0, -150.0)}
    assert count_by_bucket(lat, lon, satellite="west", route_buckets=route) == {b_west: 1}
    assert count_by_bucket(lat, lon, satellite="east", route_buckets=route) == {b_east: 1}
```

- [ ] **Step 2: Implement** — append to `glm.py`:

```python
from collections import Counter
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.pipelines.grid import bucket_center, grid_bucket

WINDOW = timedelta(minutes=30)
RETENTION = timedelta(hours=24)
MAX_LAT = 54.0


def count_by_bucket(
    lat: NDArray[np.float64], lon: NDArray[np.float64], *, satellite: Literal["east", "west"], route_buckets: set[int]
) -> dict[int, int]:
    counts: Counter[int] = Counter()
    for la, lo in zip(lat.tolist(), lon.tolist()):
        if la > MAX_LAT or la < 0 or satellite_for(lo) != satellite:
            continue
        b = grid_bucket(la, lo)
        if b in route_buckets:
            counts[b] += 1
    return dict(counts)


async def run_live(engine_factory: Callable[[], AsyncEngine], client: httpx.Client, *, now: datetime) -> dict[str, object]:
    window_end = now.replace(minute=now.minute - now.minute % 10, second=0, microsecond=0)
    engine = engine_factory()
    try:
        async with engine.begin() as conn:
            route = {int(b) for (b,) in (await conn.execute(text("SELECT DISTINCT grid_bucket FROM feature_points"))).all()}
            seen = {str(k) for (k,) in (await conn.execute(text(
                "SELECT s3_key FROM glm_files_seen WHERE processed_at > now() - interval '2 days'"))).all()}
        totals: Counter[int] = Counter()
        new_keys: list[str] = []
        sats: tuple[Literal["east", "west"], ...] = ("east", "west")
        for sat in sats:
            bucket = BUCKETS[sat]
            for hour in {window_end - timedelta(hours=1), window_end}:
                for key in list_keys(client, bucket, hour):
                    if key in seen or not (window_end - WINDOW <= file_start(key) <= window_end):
                        continue
                    data = client.get(f"https://{bucket}.s3.amazonaws.com/{key}").raise_for_status().content
                    lat, lon = read_flashes(data)
                    totals.update(count_by_bucket(lat, lon, satellite=sat, route_buckets=route))
                    new_keys.append(key)
        async with engine.begin() as conn:
            if totals:
                await conn.execute(text(
                    "INSERT INTO lightning_recent (grid_bucket, window_end, flashes, satellite) VALUES (:b, :w, :n, :s) "
                    "ON CONFLICT (grid_bucket, window_end) DO UPDATE SET flashes = lightning_recent.flashes + EXCLUDED.flashes"),
                    [{"b": b, "w": window_end, "n": n, "s": satellite_for(bucket_center(b)[1])} for b, n in totals.items()])
            if new_keys:
                await conn.execute(text("INSERT INTO glm_files_seen (s3_key) VALUES (:k) ON CONFLICT DO NOTHING"),
                                   [{"k": k} for k in new_keys])
            await conn.execute(text("DELETE FROM lightning_recent WHERE window_end < :cut"), {"cut": now - RETENTION})
            await conn.execute(text("DELETE FROM glm_files_seen WHERE processed_at < now() - interval '2 days'"))
    finally:
        await engine.dispose()
    return {"status": "ok", "files": len(new_keys), "buckets": len(totals)}
```

`raise_for_status()` returns the response in httpx 0.28, so the chained `.content` is valid. Since files are counted once (via `glm_files_seen`) into the window they fall in, a 30-minute window row accumulates across the three 10-minute runs; the banner (Phase 3) reads `sum(flashes) WHERE window_end > now() - 30 min`. Imports go to the top of the module.

Celery: in `data_feeds.py` add

```python
@celery_app.task(name="app.tasks.data_feeds.glm_live")
def glm_live() -> dict[str, object]:
    import httpx

    from app.pipelines.db import ingest_engine
    from app.pipelines.glm import run_live

    return _run("glm-live", lambda: run_live(ingest_engine, httpx.Client(timeout=60.0), now=datetime.now(timezone.utc)))
```

and in `celery_app.py`: `"glm-live": {"task": "app.tasks.data_feeds.glm_live", "schedule": 600.0, "options": {"expires": 540}},`. Extend `test_data_feeds_tasks.py` to include `glm-live`/`glm_live`.

- [ ] **Step 3: Run** — PASS. **Step 4: Commit** — `git add backend/app/pipelines/glm.py backend/tests/test_glm.py backend/app/tasks/data_feeds.py backend/app/celery_app.py backend/tests/test_data_feeds_tasks.py && git commit -m "feat(pipelines): GLM live 10-minute lightning into lightning_recent"`

---

### Task 11: GLM daily totals and 2018+ backfill

**Files:**
- Modify: `backend/app/pipelines/glm.py`, `backend/tests/test_glm.py`
- Create: `.github/workflows/data-backfill-lightning.yml`
- Modify: `.github/workflows/data-daily.yml`

**Interfaces:**
- Produces: `EAST_SWITCH = date(2025, 4, 7)` (GOES-19 became East; earlier days read GOES-16), `east_bucket_for(day: date) -> str`, `async run_daily(engine_factory, client, *, day: date, target_buckets: set[int]) -> dict[str, object]` (all files of the UTC day from the East satellite for that date — history is East-only per spec — flash counts per target bucket → `UPDATE`/`INSERT … ON CONFLICT` setting only `lightning_density`, `lightning_source='glm'`, `lightning_coverage` (`glm` ≤54°N, `none` above, with NULL density); logged per day as `glm_daily` and skipped when already ok), CLI `python -m app.pipelines.glm daily [--day YYYY-MM-DD] | backfill --start YYYY-MM-DD --end YYYY-MM-DD`.

Target buckets: route buckets for days within the rolling 3-year window; incident buckets + 8 neighbours for every day from 2018 (Decision 2). Rows that do not exist yet are inserted with `source = 'goes_glm'` and weather columns NULL.

- [ ] **Step 1: Failing test** — append to `test_glm.py`:

```python
from datetime import date

from app.pipelines.glm import east_bucket_for


def test_history_uses_goes16_before_the_switch():
    assert east_bucket_for(date(2025, 4, 6)) == "noaa-goes16"
    assert east_bucket_for(date(2025, 4, 7)) == "noaa-goes19"
```

and a DB test (move its imports to the top of `test_glm.py`):

```python
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.glm import run_daily
from tests.pgtest import migrated_db, requires_pg, sa_url

DAY_XML = XML.replace("2026/271/14", "2024/183/00").replace("s20262711400000", "s20241830000000")


@requires_pg
def test_daily_totals_write_only_lightning_columns():
    south, north = grid_bucket(40.0, -80.0), grid_bucket(60.0, -150.0)
    seed = f"INSERT INTO cell_daily_conditions (grid_bucket, date, tmax, source) VALUES ({south}, '2024-07-01', 12.0, 'open_meteo_archive');"
    nc = _h5([40.0, 40.02], [-80.0, -80.01], [0, 0])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/" and request.url.params["prefix"].endswith("/00/"):
            return httpx.Response(200, text=DAY_XML)
        if request.url.path == "/":
            return httpx.Response(200, text=XML.replace("<Contents>", "<!--").replace("</Contents>", "-->"))
        return httpx.Response(200, content=nc)

    async def go(url: str) -> list[tuple[object, ...]]:
        await run_daily(lambda: create_async_engine(url), httpx.Client(transport=httpx.MockTransport(handler)),
                        day=date(2024, 7, 1), target_buckets={south, north})
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return [tuple(r) for r in (await conn.execute(text(
                    "SELECT grid_bucket, tmax, lightning_density, lightning_coverage FROM cell_daily_conditions ORDER BY grid_bucket"))).all()]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        rows = asyncio.run(go(sa_url(name)))
    assert rows == [(south, 12.0, 2.0, "glm"), (north, None, None, "none")]
```

(The mock serves one file in hour 00 and empty listings for the other 23 hours; `grid_bucket(40.0, -80.0) < grid_bucket(60.0, -150.0)`, so the ORDER BY puts the southern bucket first.)

- [ ] **Step 2: Implement** — append to `glm.py`:

```python
EAST_SWITCH = date(2025, 4, 7)


def east_bucket_for(day: date) -> str:
    return BUCKETS["east"] if day >= EAST_SWITCH else BUCKETS["east_history"]


async def run_daily(
    engine_factory: Callable[[], AsyncEngine], client: httpx.Client, *, day: date, target_buckets: set[int]
) -> dict[str, object]:
    from app.pipelines.ingest_log import find_completed, finish_run, sha256_rows, start_run
    from app.pipelines.validate import ValidationReport

    sha = sha256_rows([(b,) for b in sorted(target_buckets)])
    engine = engine_factory()
    try:
        async with engine.begin() as conn:
            if await find_completed(conn, source="glm_daily", window_start=day, window_end=day, content_sha256=sha):
                return {"status": "skipped", "day": day.isoformat()}
        bucket = east_bucket_for(day)
        counts: Counter[int] = Counter()
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        for h in range(24):
            for key in list_keys(client, bucket, start + timedelta(hours=h)):
                lat, lon = read_flashes(client.get(f"https://{bucket}.s3.amazonaws.com/{key}").raise_for_status().content)
                for la, lo in zip(lat.tolist(), lon.tolist()):
                    if 0 <= la <= MAX_LAT:
                        b = grid_bucket(la, lo)
                        if b in target_buckets:
                            counts[b] += 1
        rows = []
        for b in target_buckets:
            covered = bucket_center(b)[0] <= MAX_LAT
            rows.append({"b": b, "d": day, "n": counts.get(b, 0) if covered else None, "c": "glm" if covered else "none"})
        report = ValidationReport("glm_daily")
        report.rows_in = report.accepted = len(rows)
        async with engine.begin() as conn:
            run_id = await start_run(conn, source="glm_daily", window_start=day, window_end=day, content_sha256=sha)
            await conn.execute(text(
                "INSERT INTO cell_daily_conditions (grid_bucket, date, lightning_density, lightning_source, lightning_coverage, source) "
                "VALUES (:b, :d, :n, 'glm', :c, 'goes_glm') ON CONFLICT (grid_bucket, date) DO UPDATE SET "
                "lightning_density = EXCLUDED.lightning_density, lightning_source = 'glm', lightning_coverage = EXCLUDED.lightning_coverage"),
                rows)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=len(rows))
    finally:
        await engine.dispose()
    return {"status": "ok", "day": day.isoformat(), "flashes": sum(counts.values())}
```

(`date` imported from `datetime` at the top.) Inside coverage, a bucket-day with no flash is written as 0 (GLM saw that bucket all day); above 54°N it is NULL with `none`. The CLI resolves `target_buckets` as `route_buckets ∪ incident_and_neighbour_buckets` (from `era5_rolling`), `daily` defaults to yesterday (UTC), `backfill` iterates days (newest first) and is resumable through `find_completed`.

`data-daily.yml`: add `python -m app.pipelines.glm daily`. `data-backfill-lightning.yml`: `workflow_dispatch` with inputs `start`, `end`, and a `strategy.matrix` of 12 month slices computed by a first job (`python -c` printing a JSON list of `[start, end]` month pairs to `$GITHUB_OUTPUT`), each slice running `python -m app.pipelines.glm backfill --start … --end …` with `timeout-minutes: 360`, `max-parallel: 12`, plus an NLDN job (Task 12). Public-repo Actions minutes are free; each slice resumes where the last stopped.

- [ ] **Step 3: Run** — PASS. **Step 4: Commit** — `git add backend/app/pipelines/glm.py backend/tests/test_glm.py .github/workflows/data-daily.yml .github/workflows/data-backfill-lightning.yml && git commit -m "feat(pipelines): GLM daily flash counts and resumable 2018+ backfill"`

---

### Task 12: NLDN 1989–2017 tiles and the GLM overlap check

**Files:**
- Create: `backend/app/pipelines/nldn.py`, `backend/tests/test_nldn.py`
- Modify: `.github/workflows/data-backfill-lightning.yml`, `backend/pyproject.toml`

**Interfaces:**
- Produces: `NLDN_URL = "https://www.ncei.noaa.gov/pub/data/swdi/database-csv/v2/nldn-tiles-{year}.csv.gz"`, `FIRST_YEAR = 1989`, `LAST_YEAR = 2017`, `parse_tiles(lines: Iterable[str]) -> Iterator[tuple[date, int, int]]` (day, grid bucket, count; skips `#` lines; column order `ZDAY,CENTERLON,CENTERLAT,TOTAL_COUNT`), `center_alignment(lines) -> dict[str, int]` (distribution of the hundredths digit of `CENTERLAT` — `{"0": n, "5": m}` — to show whether tiles are centred on our bucket centres), `async load_year(conn, year: int, lines, *, targets: set[int], run_id) -> int` (non-zero tile-days for target buckets only: `lightning_cg_nldn`, `lightning_source='nldn'`, `lightning_coverage='nldn'`), `async overlap(conn, years: range) -> dict[str, float | int]` (Pearson and Spearman of NLDN tile counts vs GLM daily counts for the same bucket-days, 2018–2025), CLI `python -m app.pipelines.nldn backfill [--start-year 1989 --end-year 2017] | overlap | alignment --year 2017`.

- [ ] **Step 1: Verify the alignment (agent)** — `uv run python -m app.pipelines.nldn alignment --year 2017` (after Step 3). If centres end in `.x5`, NLDN tiles straddle two of our buckets: record the result in the PR and in `lightning_coverage_periods` notes; the mapping (bucket of the tile centre) stays deterministic, and Phase 3 uses NLDN only as a within-cell anomaly, which a consistent half-cell offset does not bias.

- [ ] **Step 2: Failing tests** — `backend/tests/test_nldn.py`:

```python
from datetime import date

from app.pipelines.grid import grid_bucket
from app.pipelines.nldn import center_alignment, parse_tiles

LINES = [
    "# the count of Vaisala NLDN lightning strikes within a 0.1 degree grid cell for the specified day",
    "ZDAY,CENTERLON,CENTERLAT,TOTAL_COUNT",
    "20170704,-105.25,40.05,12",
    "20170704,-80.15,25.75,3",
]


def test_lon_lat_column_order():
    rows = list(parse_tiles(LINES))
    assert rows[0] == (date(2017, 7, 4), grid_bucket(40.05, -105.25), 12)
    assert len(rows) == 2


def test_alignment_distribution():
    assert center_alignment(LINES) == {"5": 2}
```

- [ ] **Step 3: Implement** `backend/app/pipelines/nldn.py`:

```python
"""NOAA NCEI SWDI NLDN daily 0.1° tile counts (cloud-to-ground, cite dataset), 1989–2017
(P2-7). Only non-zero tile-days are listed; lightning_coverage_periods records that absence
inside 1989–2017 and ≤54°N means zero. Stored separately from GLM (lightning_cg_nldn)."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import io
import json
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import date, datetime

import httpx
import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.grid import grid_bucket

NLDN_URL = "https://www.ncei.noaa.gov/pub/data/swdi/database-csv/v2/nldn-tiles-{year}.csv.gz"
FIRST_YEAR = 1989
LAST_YEAR = 2017


def _rows(lines: Iterable[str]) -> Iterator[list[str]]:
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line.upper().startswith("ZDAY"):
            continue
        yield line.split(",")


def parse_tiles(lines: Iterable[str]) -> Iterator[tuple[date, int, int]]:
    for zday, center_lon, center_lat, count in _rows(lines):
        lat, lon = float(center_lat), float(center_lon)
        if not (0 <= lat <= 90):
            continue
        yield datetime.strptime(zday, "%Y%m%d").date(), grid_bucket(lat, lon), int(count)


def center_alignment(lines: Iterable[str]) -> dict[str, int]:
    return dict(Counter(f"{abs(float(r[2])) * 100:.0f}"[-1] for r in _rows(lines)))


def year_lines(client: httpx.Client, year: int) -> list[str]:
    response = client.get(NLDN_URL.format(year=year), timeout=600.0)
    response.raise_for_status()
    return gzip.GzipFile(fileobj=io.BytesIO(response.content)).read().decode("utf-8").splitlines()


async def load_year(conn: AsyncConnection, year: int, lines: Iterable[str], *, targets: set[int], run_id: uuid.UUID) -> int:
    rows = [{"b": b, "d": d, "n": n, "run": run_id} for d, b, n in parse_tiles(lines) if b in targets and n > 0]
    if rows:
        await conn.execute(text(
            "INSERT INTO cell_daily_conditions (grid_bucket, date, lightning_cg_nldn, lightning_source, lightning_coverage, source, run_id) "
            "VALUES (:b, :d, :n, 'nldn', 'nldn', 'swdi_nldn', :run) ON CONFLICT (grid_bucket, date) DO UPDATE SET "
            "lightning_cg_nldn = EXCLUDED.lightning_cg_nldn, lightning_source = coalesce(cell_daily_conditions.lightning_source, 'nldn'), "
            "lightning_coverage = coalesce(cell_daily_conditions.lightning_coverage, 'nldn')"), rows)
    return len(rows)


def _ranks(x: np.ndarray) -> np.ndarray:
    order = x.argsort()
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x))
    for value in np.unique(x):
        tied = x == value
        ranks[tied] = ranks[tied].mean()
    return ranks


async def overlap(conn: AsyncConnection, client: httpx.Client, years: range) -> dict[str, float | int]:
    targets = {int(b) for (b,) in (await conn.execute(text(
        "SELECT DISTINCT grid_bucket FROM cell_daily_conditions WHERE lightning_density IS NOT NULL"))).all()}
    glm = {(int(b), d): float(n) for b, d, n in (await conn.execute(text(
        "SELECT grid_bucket, date, lightning_density FROM cell_daily_conditions "
        "WHERE lightning_source = 'glm' AND lightning_density IS NOT NULL AND date BETWEEN :s AND :e"),
        {"s": date(years.start, 1, 1), "e": date(years.stop - 1, 12, 31)})).all()}
    nldn: dict[tuple[int, date], float] = {}
    for year in years:
        for d, b, n in parse_tiles(year_lines(client, year)):
            if b in targets:
                nldn[(b, d)] = float(n)
    keys = sorted(glm)
    x = np.array([nldn.get(k, 0.0) for k in keys])
    y = np.array([glm[k] for k in keys])
    if len(keys) < 3 or x.std() == 0 or y.std() == 0:
        return {"n": len(keys), "pearson": float("nan"), "spearman": float("nan")}
    return {"n": len(keys), "pearson": float(np.corrcoef(x, y)[0, 1]),
            "spearman": float(np.corrcoef(_ranks(x), _ranks(y))[0, 1])}
```

The overlap treats a GLM-covered bucket-day with no NLDN tile as 0 (absence inside NLDN coverage is zero). CLI `backfill` loads each year from `--start-year` to `--end-year` for `targets = incident_and_neighbour_buckets` (resumable via `find_completed(source='nldn', window=(Jan 1, Dec 31))`); `overlap` runs 2018–2025 and logs `source='nldn_glm_overlap'` with the numbers; `alignment` prints `center_alignment` for one year. Append `"app.pipelines.nldn"` to strict mypy. Add an `nldn` job to `data-backfill-lightning.yml` (single job, `timeout-minutes: 360`, `python -m app.pipelines.nldn backfill`).

`data-weekly.yml` gets a second schedule for monthly work; each job is guarded by the cron that triggered it:

```yaml
on:
  schedule:
    - cron: "20 7 * * 2"    # weekly
    - cron: "30 10 3 * *"   # monthly, 3rd of the month
  workflow_dispatch:

jobs:
  weekly:
    if: github.event_name == 'workflow_dispatch' || github.event.schedule == '20 7 * * 2'
    # ... the weekly steps from Task 8 ...
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
      - run: uv run python -m app.pipelines.nldn overlap
      - name: Open an issue on failure
        if: failure()
        working-directory: .
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh issue create --title "data-weekly (monthly) failed ($(date -u +%F))" --body "Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

(the `weekly` job's body is exactly Task 8's steps; only the `if:` line is new).

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/nldn.py backend/tests/test_nldn.py .github/workflows/ backend/pyproject.toml && git commit -m "feat(pipelines): NLDN 1989-2017 tile backfill and NLDN-GLM overlap check"`

---

### Task 13: Lightning-day frequency in `cell_climatology`, PR 2b-3b docs

**Files:**
- Create: `backend/app/pipelines/lightning_climatology.py`, `backend/tests/test_lightning_climatology.py`
- Modify: `.github/workflows/data-weekly.yml`, `CHANGELOG.md`, `DEPLOYMENT.md`, `CLAUDE.md`, `data/DATABASE_STRUCTURE.md`

**Interfaces:**
- Produces: `lightning_day_freq(days: Sequence[tuple[date, float | None]], doy: int, half_window: int = 7) -> float | None` (share of covered days with ≥1 flash in the pooled window; NULL when no covered day), `async run(conn, *, start: date, end: date) -> int` (GLM era = the normals window from `normals.window(today)` intersected with ≥2018; writes only `cell_climatology.lightning_day_freq` for buckets ≤54°N; NULL above).

- [ ] **Step 1: Failing test**

```python
from datetime import date, timedelta

from app.pipelines.lightning_climatology import lightning_day_freq


def test_share_of_covered_days_with_flashes():
    days = [(date(2024, 7, 1) + timedelta(d), 1.0 if d % 2 == 0 else 0.0) for d in range(15)]
    assert lightning_day_freq(days, date(2024, 7, 8).timetuple().tm_yday) == 8 / 15


def test_no_covered_days_is_none():
    assert lightning_day_freq([(date(2024, 7, 1), None)], 183) is None
```

- [ ] **Step 2: Implement** `backend/app/pipelines/lightning_climatology.py`:

```python
"""Lightning-day frequency per bucket and day of year (spec: part of cell_climatology),
from GLM daily counts; missing (NULL) outside satellite coverage, never zero."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

HALF_WINDOW = 7


def lightning_day_freq(days: Sequence[tuple[date, float | None]], doy: int, half_window: int = HALF_WINDOW) -> float | None:
    hits = total = 0
    for d, n in days:
        gap = abs(d.timetuple().tm_yday - doy)
        if min(gap, 366 - gap) > half_window or n is None:
            continue
        total += 1
        hits += n > 0
    return hits / total if total else None


async def run(conn: AsyncConnection, *, start: date, end: date) -> int:
    rows = (await conn.execute(text(
        "SELECT grid_bucket, date, lightning_density FROM cell_daily_conditions "
        "WHERE lightning_source = 'glm' AND date BETWEEN :s AND :e"), {"s": start, "e": end})).all()
    series: dict[int, list[tuple[date, float | None]]] = defaultdict(list)
    for b, d, n in rows:
        series[int(b)].append((d, float(n) if n is not None else None))
    updates = [{"b": b, "doy": doy, "f": lightning_day_freq(days, doy)} for b, days in series.items() for doy in range(1, 367)]
    if updates:
        await conn.execute(text("UPDATE cell_climatology SET lightning_day_freq = :f WHERE grid_bucket = :b AND doy = :doy"), updates)
    return len(series)
```

Add a CLI (`start = max(date(2018, 1, 1), date(window(today)[0], 1, 1))`, `end = date(window(today)[1], 12, 31)`, using `normals.window`) and the step `uv run python -m app.pipelines.lightning_climatology` after `nldn overlap` in `data-weekly.yml`'s `monthly` job. Append `"app.pipelines.lightning_climatology"` to strict mypy.

Docs: CHANGELOG "Phase 2b lightning (PR 2b-3b)"; DEPLOYMENT.md: `glm-live` beat entry (slug `glm-live`, period 10 min, grace 20 min), `h5py` in the image, backfill workflow and its expected run time; DATABASE_STRUCTURE.md: lightning columns, `lightning_recent`, `lightning_coverage_periods` and how to read absence; `DATA_LICENSE.md` is **not** edited (pending legal review) — list "cite SWDI NLDN in DATA_LICENSE.md" in the PR description as an owner follow-up.

- [ ] **Step 3: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/` → green. **Step 4: Commit** — `git add backend/app/pipelines/lightning_climatology.py backend/tests/test_lightning_climatology.py .github/workflows/data-weekly.yml CHANGELOG.md DEPLOYMENT.md CLAUDE.md data/DATABASE_STRUCTURE.md backend/pyproject.toml && git commit -m "feat(pipelines): lightning-day frequency climatology from GLM"`

---

### Task 14: OWNER/AGENT RUNBOOK — PR 2b-3a

- [ ] **Step 1 (owner/agent): Migrate** — rehearsal branch then prod: `0013` (refuses if R7 was not applied), grants, verify scripts.
- [ ] **Step 2 (owner): Railway worker env** — add `INGEST_DATABASE_URL` (from `.env.ingest`, pasted via the Railway UI), `HEALTHCHECKS_PING_KEY`, and confirm `OPEN_METEO_API_KEY` (Standard plan). Create healthchecks checks `forecast-nightly` (24 h / 2 h), `nws-alerts` (1 h / 1 h), `airnow` (1 h / 2 h), `snotel-daily`, `era5-weekly`. Deploy worker and beat together (the beat schedule changed); watch `/health/worker` stay 200.
- [ ] **Step 3 (owner/agent): History backfill** — `era5_rolling backfill --max-units 150000`, repeated inside a paid Open-Meteo month until every chunk reports `skipped` (spec ~0.32M units; the cap stops each run before overspending); then `nws archive --start 1990-01-01 --end <today−1>` (IEM, free; resumable).
- [ ] **Step 4 (owner/agent): Acceptance** — 7 consecutive nightly runs `ok` (`SELECT date(finished_at), status FROM source_ingest_log WHERE source='open_meteo_forecast' ORDER BY 1 DESC LIMIT 7`); staleness drill: pause the beat service for 3 hours → `/health/data` returns 503 with `nws_alerts: stale` and the `nws-alerts` check alerts → resume → 200 (spec: "staleness alert drilled").

### Task 15: OWNER/AGENT RUNBOOK — PR 2b-3b

- [ ] **Step 1 (owner/agent)**: deploy worker/beat with `h5py`; add healthchecks `glm-live` (10 min / 20 min) and `glm-daily`; confirm `/health/data` shows `glm_live: ok` within 30 minutes.
- [ ] **Step 2 (owner)**: dispatch `data-backfill-lightning` for 2018-01-01 → yesterday (GLM, 12 parallel slices; expect roughly two days of wall time; rerun until every slice reports only `skipped`), and the NLDN job 1989–2017.
- [ ] **Step 3 (owner/agent)**: `nldn alignment --year 2017` result recorded; `nldn overlap` → report `pearson`/`spearman` for 2018–2025 in the PR (spec 2b-3 acceptance: "NLDN–GLM overlap correlation reported"); run the lightning climatology step.

---

## Self-review

- Spec coverage (2b-3): Open-Meteo forecast (Task 2), ERA5 3-year + append (Task 3), SNOTEL (Task 4), NWS alerts + IEM archive (Task 5), AirNow (Task 6), GLM live + daily + 2018+ backfill (Tasks 9–11), NLDN 1989–2017 + overlap (Task 12), climatology incl. lightning-day frequency (plan 5 + Task 13), scheduling split beat/Actions (Task 8, workflows), monitoring (`/health/data`, healthchecks, failure issues — Tasks 7–8), `area_weekly_weather` dropped (Task 1), acceptance "7 nightly ok, staleness drilled, overlap reported" (Tasks 14–15).
- Placeholders: none beyond Console/Railway values the owner supplies.
- Types: `upsert_rows`, `day_rows`, `Location`, `ForecastClient.fetch_forecast`, `count_by_bucket`, `run_daily`, `parse_tiles` are used with the signatures their tasks define.
