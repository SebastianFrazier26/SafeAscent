# Phase 2a Conditions and Accident Refresh (PRs 2a-3, 2a-4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mislinked per-accident `weather` rows with daily conditions per 0.1° cell and **local calendar day** (`cell_daily_conditions`, keyed by `(grid_bucket, tz, date)`, m/s, no visibility), filled from the ERA5 archive for every incident series back to 1940 and for every route series over the trailing 3 years, plus the monthly climate normals and day-of-year climatology every later plan reads. All ERA5 work happens inside one Open-Meteo Professional window per year (the first one here, then every January). Verify against the legacy table (R6), fix the `area_weekly_weather` sign error (R7), and load the 2024-08-to-now accident refresh from facts-only CSVs (R11) so Phase 3 MVP-0 is unblocked.

**Architecture:** A typed Open-Meteo archive client (`app/pipelines/open_meteo.py`, pydantic-validated responses, recorded-response tests, API key never logged, one IANA timezone per request) feeds a validating writer (`cell_conditions.py`) whose upsert enforces `era5` > `stopgap` > `forecast` precedence. `localday.py` resolves each point's IANA timezone and registers `(grid_bucket, tz)` series. `era5_fill.py` keeps one contiguous ERA5 span per series and fills its gaps in order; `normals.py` builds monthly normals and day-of-year climatology per bucket; `era5_window.py` is the only ERA5 entry point: it refuses to run outside a declared Professional window and caps the window's **cumulative** spend from `source_ingest_log`. A view `accident_conditions` joins accidents to conditions on (cell, crag timezone, local date), never on `accident_id`. The R11 loader reads facts-only CSVs (manual AAC, private CAIC/NPS exports) and inserts new rows through the same validation as every other boundary.

**Tech Stack:** Python 3.12, httpx 0.28 (existing), pydantic 2, numpy (existing), `timezonefinder` + `tzdata` (new `pipelines` dependency group, created by this plan; plan 4 appends `h3`, plan 5 `rasterio`), SQLAlchemy async, Alembic, pytest with `httpx.MockTransport`.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` R6, R7, R11, milestones 2a-3/2a-4, Dynamic features (Open-Meteo archive, P2-5); amendment `2026-09-28-phase3-amendment-similarity-confidence.md` §3.1 (normals) and §6 Q5; P3:151 (`cell_daily_conditions` columns), P3:161 (grid). Owner decisions D4, D6, D7, D8, D9, D12, D14 and the 2026-09-28 owner decisions (Open-Meteo window, local weather day) in `2026-09-28-phase2a-foundations.md`. Defaults DP1 (incident backfill start) and DP5 (first window timing) are listed at the top of that file.

**Prerequisites:** Plans 1 and 2 merged and applied (this plan reads `accidents_clean_daily`, calls the `grid_bucket_key` SQL function from `0004`, and uses `grid.py`, `ingest_log.py`, `framework.py`).

**Two PRs:** PR 2a-3 = Tasks 1–9 (`feat/p2a-conditions`) + runbook Task 12; PR 2a-4 = Tasks 10–11 (`feat/p2a-accident-refresh`) + runbook Task 13. Runbook Task 14 is the first Professional window (it runs after both PRs are merged and applied, so the refresh incidents get their ERA5 windows in the same paid month).

## Open-Meteo cost model (corrects spec P2-5)

- Prices and limits read 2026-09-28 from open-meteo.com/en/pricing; **re-check before every purchase**. Professional $99/month (5M calls); Standard $29/month (1M calls). The pricing FAQ states: "Historical, climate, ensemble, and satellite radiation APIs require the Professional API Plan or higher." Spec P2-5 ("one month of Professional for the backfill, then Standard", "weekly append after that") is therefore wrong: Standard cannot call the archive at all. The spec is not edited here; this section and the foundations plan's cadence table supersede it.
- Owner decision 2026-09-28: **one Professional month per year, in January**, started on or after 10 January (ERA5's ~5-day lag plus margin, so the whole previous year is final). The first window is the initial backfill (Task 14); `[default pending: DP5]` it is bought when this plan's runbook is ready rather than waiting for January 2027.
- Between windows, recent days come from the Forecast API `past_days` (plan 7's nightly job, on the Standard plan the spec already budgets for the forecast), written as `record_kind = 'stopgap'`: a clearly flagged, non-ERA5 stopgap. The January window replaces every stopgap row with ERA5. There is **no weekly ERA5 append** anywhere.
- Estimated spend (ceiling formula, which over-estimates because Open-Meteo counts fractionally): first window ≈ 1.6M units (incident series from 1940, `[default pending: DP1]`) + ≈ 0.36M (3-year history, ~4.5K series) + ≈ 1.2M (10-year normals) ≈ **3.2M of 5M**. Each later January ≈ 0.12M (prior year for every series) + ≈ 1.2M (normals rebuild) + new-series history.

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- Wind in m/s everywhere (`wind_speed_unit=ms` on every request); no visibility column.
- Missing values are NULL, never 0; a value outside its range quarantines the whole day-row. A monthly total is never a sum over missing days: too few observed days make it NULL; a few missing days are scaled up, not treated as 0.
- **Weather day = the crag's local calendar day** (owner decision 2026-09-28). Every series is `(grid_bucket, IANA tz)`; every archive request carries `timezone=<tz>` so Open-Meteo aggregates local days; accidents carry `accidents.tz`. "Future" is still "after the current UTC day at run time"; US local days never run ahead of the UTC day.
- Point-level weather joins use only accidents with `point_trusted` (plan 2): `region_fallback`/`unknown` points keep their coordinates for pooling but get no conditions window.
- Precedence for one `(grid_bucket, tz, date)`: `era5` beats `stopgap` beats `forecast`; a lower kind never replaces a higher one.
- The ERA5 lag is handled at run time: archive requests end at `today − 5 days`; nothing is fixed to a calendar date. **Every archive call runs inside a declared Professional window**: `era5_window` refuses otherwise.
- The Open-Meteo API key lives in `settings.OPEN_METEO_API_KEY`; it is never logged, printed, or included in an exception message (httpx errors carry the URL, so the client never re-raises them raw).
- Cost guard: `--max-units` caps the **window's cumulative** spend (sum of `source_ingest_log.cost_units` for the paid sources since the window start, plus this run), not one run.
- `weather` is **not** dropped in this plan `[assumes D9]`. Dropping it is a **hard gate of the Phase 3 MVP-1 PR** (that PR may not merge while `weather` exists); until then the live kernel still reads mislinked legacy weather, and the relaunch caveat says so (Task 9 docs).
- R11 rows are facts only: date, place, activity, type, severity, experience facts, URL, and our own one-line summary. No AAC narrative text, no scraped HTML. CAIC/NPS files come from `~/Developer/safeascent-private/`; the filled AAC CSV stays there too until legal Q6 `[assumes D12]`.

## Decisions this plan makes where the spec is silent (owner may overrule)

1. Weather values are stored as `real` (float4) to keep `cell_daily_conditions` inside the spec's ~1.3 GB estimate; float4 keeps ~7 significant digits, far beyond the data's precision.
2. Requests are made at the bucket **center** (`grid.bucket_center`), so every consumer of a series sees the same values. Open-Meteo returns the 90 m DEM height of that point and lapse-rate-downscales its output to it; that `elevation` is stored per bucket as `cell_climate_normals.ref_elevation_m`, so Phase 3 sees the route-vs-bucket elevation gap. The `elevation=` request parameter (downscale to the route's own elevation) was considered and not used: it would turn one shared series per bucket into one paid series per route point.
3. Open-Meteo cost units are estimated as `locations × ⌈vars/10⌉ × ⌈days/14⌉`. Open-Meteo counts fractionally (pricing FAQ), so the ceiling over-estimates; that is the safe direction for a budget cap. The owner confirms against the dashboard after the first slice (runbook).
4. R11 inserts new accidents only; a row whose `(source, source_id)` already exists is quarantined `already_present`, never overwritten. The own one-line summary goes into `description` (capped at 200 characters), since the table has no separate summary column and new rows carry no narrative.
5. A bucket that straddles a timezone boundary gets one series per timezone. Monthly normals and day-of-year climatology are per bucket and use the bucket's alphabetically first timezone: a one-hour shift of the day boundary does not move a monthly mean or a ±7-day pooled percentile measurably.
6. ERA5 coverage is one contiguous span `[first_era5_date, last_era5_date]` per series. Gaps before it are filled newest-first and gaps after it oldest-first; a failed chunk blocks the rest of its group, so the span never has a hole and resume needs no log lookup.
7. `[default pending: DP1]` Incident series are filled from 1940-01-01 (the start of ERA5 in the Open-Meteo archive), so every clean day-precision incident from 1940-01-07 on has a 7-day window. Days before 1979 are pre-satellite ERA5 (lower quality); Phase 3 can see this from the date. Incidents before 1940-01-07 are reported as `conditions_missing`, never given values. Alternative: start 1990 (the spec's figure, ~0.67M units) and report pre-1990 incidents as `conditions_missing`.
8. R11 `country` comes from the data: `US` when the coordinates pass the US check, or, for a row without coordinates, when `state` is a US state or DC; any other blank-coordinate row is quarantined `unknown_country` (never assumed US).

## Review Focus

1. **Open-Meteo returns a single object instead of a list** (one location in the request) — expect it parsed like a one-element list (Task 1 `test_single_location_response_is_a_list_of_one`).
2. **A daily value is `null`** — expect NULL in the row, never 0, and the row kept (Task 3 `test_null_values_stay_null`).
3. **A forecast or stopgap upsert arriving after the ERA5 row exists** — expect the ERA5 values to survive (Task 3 `test_precedence_era5_over_stopgap_over_forecast`).
4. **An evening thunderstorm at 19:30 MDT (01:30 UTC the next day)** — expect it on the crag's local day, not the UTC day (Task 4 `test_evening_storm_is_the_previous_local_day`; Task 2 `test_accident_conditions_joins_on_local_day_cell_and_tz`).
5. **A fill interrupted mid-way and restarted, or a chunk that fails** — expect no re-bought units and no hole in the ERA5 span (Task 5 `test_resume_fills_only_the_remaining_gap`, `test_failed_chunk_blocks_later_chunks_of_its_group`).
6. **A second run in the same window with the same `--max-units`** — expect it to stop before exceeding the cap already partly spent (Task 5 `test_units_spent_is_cumulative_across_runs`); **a run outside the window** — refused (Task 7 `test_run_refuses_outside_the_window`).
7. **Five missing precipitation days in a month** — expect a NULL monthly total, never a sum that treats them as 0 (Task 6 `test_missing_precip_days_are_never_summed_as_zero`).
8. **An R11 CSV row dated tomorrow, with a Canadian coordinate, or with no coordinates and state `BC`** — expect quarantine `future` / `outside_us` / `unknown_country`, never an inserted accident (Task 10 `test_future_and_foreign_rows_are_quarantined`, `test_blank_coordinates_with_a_foreign_state_are_quarantined`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/app/pipelines/open_meteo.py` | Create | Archive client (one tz per request), response models, cost estimate, `kmh_to_ms`. |
| `backend/app/pipelines/cell_conditions.py` | Create | Day-row validation, freeze-thaw, record kinds, precedence upsert. |
| `backend/app/pipelines/localday.py` | Create | `tz_for_point`, `local_date`, series registration, accident `tz`, `register` CLI. |
| `backend/app/pipelines/pro_window.py` | Create | Professional-window rules, cumulative spend, `Budget`. |
| `backend/app/pipelines/era5_fill.py` | Create | Per-series ERA5 coverage, gap planner, gap filler, 3-year prune. |
| `backend/app/pipelines/normals.py` | Create | Monthly normals and day-of-year climatology (moved here from plan 5, D6/D7). |
| `backend/app/pipelines/era5_window.py` | Create | The only ERA5 entry point: `register`, `plan`, `run` for a Professional window. |
| `backend/app/pipelines/r6_verify.py` | Create | Legacy-vs-new temperature correlation, gust p99, logged. |
| `backend/app/data/repair/area_weekly.py` | Create | R7 guarded sign fix. |
| `backend/app/pipelines/accident_facts.py` | Create | R11 facts CSV loader, post-2024 gap log. |
| `backend/alembic/versions/0007_cell_daily_conditions.py` | Create | `accidents.tz`, `grid_bucket_series`, `cell_daily_conditions`, `cell_normals_status`, `cell_climate_normals`, `cell_climatology`, `accident_conditions` view. |
| `backend/app/models/conditions.py` | Create | `CellDailyConditions`, `GridBucketSeries`, `CellNormalsStatus`, `CellClimateNormals`, `CellClimatology`. |
| `backend/app/models/accident.py` | Modify | `tz` column. |
| `data/manual/README.md`, `data/manual/accident_facts.template.csv` | Create | Facts CSV contract (header only). |
| `backend/db/roles/grants_phase2.sql`, `verify_roles_phase2.sql` | Modify | Grants. |
| `.github/workflows/ci.yml` | Modify | `backend` job installs the `pipelines` group; the audit covers it. |
| `backend/tests/test_open_meteo.py`, `test_cell_conditions.py`, `test_localday.py`, `test_pro_window.py`, `test_era5_fill.py`, `test_normals.py`, `test_era5_window.py`, `test_area_weekly.py`, `test_accident_facts.py`, `test_migration_0007.py` | Create | Tests. |
| `backend/tests/verify/test_phase2a_conditions.py` | Create | `-m db` cells for R6, R7, R11, normals, 2a-3 acceptance. |
| `backend/pyproject.toml`, `backend/uv.lock`, `CHANGELOG.md`, `CLAUDE.md`, `data/DATABASE_STRUCTURE.md`, `DEPLOYMENT.md` | Modify | `pipelines` group, mypy, docs. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 3, 5, 6, plan 7 | `open_meteo.ArchiveClient.fetch`, `Location(grid_bucket, lat, lon, tz)`, `LocationResponse`, `DAILY_VARS`, `cost_units` | Frozen in Task 1; plan 7 adds a `ForecastClient` beside it that reuses `Location` and `LocationResponse`. |
| 2 | 3–8, plans 5, 6, 7, 8 | `cell_daily_conditions` columns and PK `(grid_bucket, tz, date)`, `record_kind`, `grid_bucket_series`, `cell_normals_status`, `cell_climate_normals`, `cell_climatology`, `accidents.tz` | Task 2 creates every Phase 2 weather-row column now (AQI, alerts, SWE) so later plans add no ALTERs to a large table. Lightning is not a column here: it lives in plan 7's `lightning_daily`, `glm_hours` and NLDN tile/coverage tables, and Phase 3 reads it through plan 7's `lightning_glm_count` / `lightning_nldn_count` functions. Plan 5's `0010` no longer creates the normals tables. |
| 3 | 5, plan 7 | `cell_conditions.day_rows(..., origin=...)`, `upsert_rows(conn, rows, run_id)` precedence rule | Frozen; plan 7's nightly forecast calls `day_rows(..., origin="forecast_api")`, which writes past days as `stopgap` and today onward as `forecast`. |
| 2 | 5, 8 | `accident_conditions` view's grid expression | Calls the `grid_bucket_key` SQL function from `0004`; `test_migration_0007.py` checks SQL-vs-Python parity at `.x5` points through the view. |
| 4 | 7, plans 5, 6, 7 | `localday.tz_for_point`, `local_date`, `register_series` | Frozen; plan 5's points job and plan 6's objectives register new series through `register_series` (which also marks their buckets' normals `pending`); plan 7's GLM job uses `local_date`. |
| 5, 6 | 7 | `pro_window.Budget`, `era5_fill.run`, `normals.run` | Task 7 is the only CLI that calls them. |
| 6 | plan 7 | `cell_climatology.lightning_day_freq` | Never written by `normals.py`; plan 7 owns it. |
| 9 | plan 7 | `area_weekly_weather` | R7 fixes one row; plan 7 drops the table. |
| 12–14 | plan 2 | `backend/scripts/runbook_helpers.sh` (`TARGET_HOST`, `ING`, `INGMOD`, `VERIFY`) | Sourced, never redefined (review item P5: no hardcoded branch host). |
| 10 | plan 2 | `activity.classify_activity`, `severity.severity_scale`, R5 rerun, R1/R2 rerun | R11 reuses both functions; the runbook reruns `r5` after loading. R1/R2 only touch ids in `internal.accidents_raw` (plan 2's fix for review item A2), so R11 rows are never re-dated. |
| 2, 4, 9, 10 | each other, plans 1–2 | `grants_phase2.sql`, `verify_roles_phase2.sql` | Append-only, serial. |
| all | each other | `backend/pyproject.toml` | Serial appends. |

---

# PR 2a-3 — `feat/p2a-conditions`

### Task 1: Open-Meteo archive client

**Files:**
- Create: `backend/app/pipelines/open_meteo.py`, `backend/tests/test_open_meteo.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: `settings.OPEN_METEO_API_KEY` (existing Settings field).
- Produces:
  - `DAILY_VARS: tuple[str, ...] = ("temperature_2m_max", "temperature_2m_min", "precipitation_sum", "snowfall_sum", "wind_speed_10m_max", "wind_gusts_10m_max")`
  - `ARCHIVE_MODEL = "era5_seamless"`
  - `@dataclass(frozen=True) Location(grid_bucket: int, lat: float, lon: float, tz: str)`
  - `class DailyBlock(BaseModel)` with `time: list[date]` and one `list[float | None]` per var; `class LocationResponse(BaseModel)` with `latitude: float`, `longitude: float`, `elevation: float | None`, `timezone: str | None`, `daily: DailyBlock`
  - `class OpenMeteoError(Exception)` (message never contains the URL or key)
  - `class ArchiveClient(api_key: str | None, *, transport: httpx.BaseTransport | None = None, max_retries: int = 4, sleep: Callable[[float], None] = time.sleep)` with `fetch(locations: Sequence[Location], start: date, end: date) -> list[LocationResponse]` (all locations share one `tz`; `ValueError` otherwise)
  - `cost_units(n_locations: int, n_vars: int, n_days: int) -> int`
  - `kmh_to_ms(kmh: float) -> float`

- [ ] **Step 1: Failing tests** — `backend/tests/test_open_meteo.py`:

```python
import json
from datetime import date

import httpx
import pytest

from app.pipelines.open_meteo import (
    DAILY_VARS,
    ArchiveClient,
    Location,
    OpenMeteoError,
    cost_units,
    kmh_to_ms,
)

TZ = "America/Denver"
LOC = [Location(4003947, 40.0, -105.3, TZ), Location(4003948, 40.0, -105.2, TZ)]


def _payload(lat: float, lon: float, days: list[str], tmax: list[float | None]) -> dict[str, object]:
    daily: dict[str, object] = {"time": days}
    for var in DAILY_VARS:
        daily[var] = tmax if var == "temperature_2m_max" else [1.0] * len(days)
    return {"latitude": lat, "longitude": lon, "elevation": 1650.0, "timezone": TZ, "daily": daily}


def test_wind_conversion_36_kmh_is_10_ms():
    assert kmh_to_ms(36.0) == pytest.approx(10.0)


def test_cost_units_rule_is_a_ceiling():
    assert cost_units(1, 6, 7) == 1
    assert cost_units(50, 6, 366) == 50 * 27
    assert cost_units(2, 11, 14) == 4


def test_request_parameters_and_list_response():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        seen["host"] = request.url.host
        body = [_payload(40.0, -105.3, ["2020-01-01"], [3.0]), _payload(40.0, -105.2, ["2020-01-01"], [2.0])]
        return httpx.Response(200, json=body)

    client = ArchiveClient("secret-key", transport=httpx.MockTransport(handler))
    out = client.fetch(LOC, date(2020, 1, 1), date(2020, 1, 1))
    assert [r.daily.temperature_2m_max for r in out] == [[3.0], [2.0]]
    assert out[0].timezone == TZ
    assert seen["host"] == "customer-archive-api.open-meteo.com"
    assert seen["wind_speed_unit"] == "ms" and seen["models"] == "era5_seamless" and seen["timezone"] == TZ
    assert seen["latitude"] == "40.0,40.0" and seen["longitude"] == "-105.3,-105.2"
    assert seen["daily"] == ",".join(DAILY_VARS)


def test_one_request_never_mixes_timezones():
    mixed = [LOC[0], Location(4003948, 40.0, -105.2, "America/Los_Angeles")]
    with pytest.raises(ValueError, match="one timezone"):
        ArchiveClient(None, transport=httpx.MockTransport(lambda r: httpx.Response(500))).fetch(
            mixed, date(2020, 1, 1), date(2020, 1, 1))


def test_single_location_response_is_a_list_of_one():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(40.0, -105.3, ["2020-01-01"], [None]))

    out = ArchiveClient(None, transport=httpx.MockTransport(handler)).fetch(LOC[:1], date(2020, 1, 1), date(2020, 1, 1))
    assert len(out) == 1 and out[0].daily.temperature_2m_max == [None]


def test_ragged_arrays_are_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        bad = _payload(40.0, -105.3, ["2020-01-01", "2020-01-02"], [1.0])
        return httpx.Response(200, json=[bad])

    with pytest.raises(OpenMeteoError, match="length"):
        ArchiveClient(None, transport=httpx.MockTransport(handler)).fetch(LOC[:1], date(2020, 1, 1), date(2020, 1, 2))


def test_retries_on_429_then_succeeds_and_errors_never_leak_the_key():
    calls = {"n": 0}
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": True, "reason": "limit"})
        return httpx.Response(200, json=[_payload(40.0, -105.3, ["2020-01-01"], [1.0])])

    client = ArchiveClient("secret-key", transport=httpx.MockTransport(handler), sleep=slept.append)
    assert len(client.fetch(LOC[:1], date(2020, 1, 1), date(2020, 1, 1))) == 1
    assert len(slept) == 2

    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=json.dumps({"error": True, "reason": "bad date"}))

    with pytest.raises(OpenMeteoError) as err:
        ArchiveClient("secret-key", transport=httpx.MockTransport(bad)).fetch(LOC[:1], date(2020, 1, 1), date(2020, 1, 1))
    assert "secret-key" not in str(err.value) and "bad date" in str(err.value)
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_open_meteo.py -q` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** `backend/app/pipelines/open_meteo.py`:

```python
"""Open-Meteo historical archive (ERA5 via era5_seamless). Open, documented API; wind in m/s.

httpx exceptions carry the request URL, which holds the API key, so they are never
re-raised or logged as-is: every failure becomes an OpenMeteoError with a clean message.
The archive needs the Professional plan (Standard excludes it); callers run only inside a
declared Professional window (era5_window).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

DAILY_VARS: tuple[str, ...] = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
)
ARCHIVE_MODEL = "era5_seamless"
PUBLIC_URL = "https://archive-api.open-meteo.com/v1/archive"
CUSTOMER_URL = "https://customer-archive-api.open-meteo.com/v1/archive"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class OpenMeteoError(Exception):
    pass


@dataclass(frozen=True)
class Location:
    grid_bucket: int
    lat: float
    lon: float
    tz: str


class DailyBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")

    time: list[date]
    temperature_2m_max: list[float | None]
    temperature_2m_min: list[float | None]
    precipitation_sum: list[float | None]
    snowfall_sum: list[float | None]
    wind_speed_10m_max: list[float | None]
    wind_gusts_10m_max: list[float | None]

    @model_validator(mode="after")
    def _same_length(self) -> DailyBlock:
        n = len(self.time)
        for var in DAILY_VARS:
            if len(getattr(self, var)) != n:
                raise ValueError(f"{var} length {len(getattr(self, var))} != time length {n}")
        return self


class LocationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    latitude: float
    longitude: float
    elevation: float | None = None
    timezone: str | None = None
    daily: DailyBlock


def cost_units(n_locations: int, n_vars: int, n_days: int) -> int:
    # Open-Meteo bills fractionally; the ceiling over-estimates, so a budget cap stops early, never late.
    return n_locations * math.ceil(n_vars / 10) * math.ceil(n_days / 14)


def kmh_to_ms(kmh: float) -> float:
    return kmh / 3.6


class ArchiveClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = api_key
        self._url = CUSTOMER_URL if api_key else PUBLIC_URL
        self._client = httpx.Client(transport=transport, timeout=120.0)
        self._max_retries = max_retries
        self._sleep = sleep

    def fetch(self, locations: Sequence[Location], start: date, end: date) -> list[LocationResponse]:
        zones = {loc.tz for loc in locations}
        if len(zones) != 1:
            raise ValueError(f"one request covers exactly one timezone, got {sorted(zones)}")
        params = {
            "latitude": ",".join(f"{loc.lat:.1f}" for loc in locations),
            "longitude": ",".join(f"{loc.lon:.1f}" for loc in locations),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": ",".join(DAILY_VARS),
            "timezone": zones.pop(),
            "wind_speed_unit": "ms",
            "models": ARCHIVE_MODEL,
        }
        if self._key:
            params["apikey"] = self._key
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.get(self._url, params=params)
            except httpx.HTTPError as exc:
                raise OpenMeteoError(f"transport error: {type(exc).__name__}") from None
            if response.status_code in RETRY_STATUSES and attempt < self._max_retries:
                self._sleep(5.0 * 2**attempt)
                continue
            if response.status_code != 200:
                raise OpenMeteoError(f"HTTP {response.status_code}: {self._reason(response)}")
            return self._parse(response, len(locations))
        raise OpenMeteoError("retries exhausted")

    @staticmethod
    def _reason(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return "non-JSON error body"
        return str(body.get("reason", "no reason given")) if isinstance(body, dict) else "unexpected error body"

    @staticmethod
    def _parse(response: httpx.Response, expected: int) -> list[LocationResponse]:
        body = response.json()
        items = body if isinstance(body, list) else [body]
        if len(items) != expected:
            raise OpenMeteoError(f"expected {expected} locations, got {len(items)}")
        try:
            return [LocationResponse.model_validate(item) for item in items]
        except ValidationError as exc:
            raise OpenMeteoError(f"invalid response: {exc.errors()[0]['msg']}") from None
```

The `length` message from `_same_length` surfaces through `exc.errors()[0]['msg']` ("Value error, temperature_2m_max length 1 != time length 2"). Grouping by timezone is the caller's job (`era5_fill.plan_chunks`, `normals.run`); the client refuses a mixed request rather than guessing which zone's days to return.

Append `"app.pipelines.open_meteo"` to the strict mypy block.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_open_meteo.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/open_meteo.py backend/tests/test_open_meteo.py backend/pyproject.toml && git commit -m "feat(pipelines): Open-Meteo ERA5 archive client (m/s, local-day tz, validated, key never logged)"`

---

### Task 2: Migration `0007` — conditions, series registry, normals tables, `accident_conditions`

**Files:**
- Create: `backend/alembic/versions/0007_cell_daily_conditions.py`, `backend/app/models/conditions.py`, `backend/tests/test_migration_0007.py`
- Modify: `backend/app/models/__init__.py`, `backend/app/models/accident.py`, `backend/pyproject.toml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Consumes: SQL function `grid_bucket_key(lat double precision, lon double precision) RETURNS integer` (plan 1, `0004`).
- Produces (DB):
  - `accidents.tz text NULL` (IANA zone of the accident point; NULL = not yet resolved or unresolvable).
  - `grid_bucket_series(grid_bucket integer, tz text, first_seen_at timestamptz NOT NULL DEFAULT now(), first_era5_date date, last_era5_date date, PRIMARY KEY (grid_bucket, tz))`, CHECK both-or-neither and `first_era5_date <= last_era5_date`.
  - `cell_daily_conditions(grid_bucket integer, tz text NOT NULL, date date /* local calendar day in tz */, tmax real, tmin real, precip_mm real, snowfall_cm real, snow_depth_cm real, wind_max_ms real, gust_max_ms real, freeze_thaw boolean, swe_delta_mm real, nws_alert_codes text[], aqi smallint, record_kind text NOT NULL CHECK IN ('era5','stopgap','forecast'), is_forecast boolean NOT NULL DEFAULT false CHECK (is_forecast = (record_kind = 'forecast')), model text, source text NOT NULL /* 'open_meteo_archive' | 'open_meteo_forecast' */, fetched_at timestamptz NOT NULL DEFAULT now(), run_id uuid, PRIMARY KEY (grid_bucket, tz, date))`.
  - `cell_normals_status(grid_bucket integer PK, status text NOT NULL CHECK IN ('pending','complete','insufficient'), first_seen_at timestamptz NOT NULL DEFAULT now(), computed_at timestamptz, n_years smallint, run_id uuid)`.
  - `cell_climate_normals(grid_bucket integer, month smallint CHECK 1..12, tmax_mean real, tmin_mean real, precip_mm real, snowfall_cm real, freeze_thaw_days real, n_years smallint NOT NULL, period_start_year smallint NOT NULL, period_end_year smallint NOT NULL, ref_elevation_m real, source text NOT NULL, normals_version text NOT NULL, run_id uuid, PK (grid_bucket, month))` (moved from plan 5's `0010`).
  - `cell_climatology(grid_bucket integer, doy smallint CHECK 1..366, tmax_mean real, tmax_p10 real, tmax_p90 real, tmin_mean real, tmin_p10 real, tmin_p90 real, precip_mean real, precip_p90 real, snowfall_mean real, gust_p90 real, freeze_thaw_freq real, lightning_day_freq real /* plan 7 */, n_years smallint NOT NULL, period_start_year smallint NOT NULL, period_end_year smallint NOT NULL, climatology_version text NOT NULL, run_id uuid, PK (grid_bucket, doy))` `[assumes D7]` (moved from plan 5's `0010`).
  - View `accident_conditions(accident_id, day_offset, grid_bucket, tz, date, tmax, tmin, precip_mm, snowfall_cm, wind_max_ms, gust_max_ms, freeze_thaw, record_kind, is_forecast)` over `accidents_clean_daily` rows with `point_trusted` (plan 2: `region_fallback`/`unknown` points keep coordinates for pooling but never get a point-level join) × offsets −6..0, joined on `(grid_bucket_key(lat, lon), accidents.tz, local date)`.
- Produces (Python): `app.models.conditions.CellDailyConditions`, `GridBucketSeries`, `CellNormalsStatus`, `CellClimateNormals`, `CellClimatology`; `Accident.tz`.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0007.py`:

```python
import asyncio

import asyncpg
import pytest
from alembic import command

from app.pipelines.grid import grid_bucket
from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg

X5_POINTS = [(40.05, -105.25), (40.15, -105.35), (64.15, -149.95), (19.45, -155.25), (51.85, -176.65), (36.55, -118.25)]
ACCIDENT = (
    "INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, "
    "date_precision, geocode_precision, tz) VALUES "
    "(1, 'AAC', '2010-07-10', 40.05, -105.25, true, 'climbing', 'US', 'day', 'crag', 'America/Denver'), "
    "(2, 'AAC', '2010-07-10', 40.05, -105.25, true, 'climbing', 'US', 'day', 'region_fallback', 'America/Denver');"
)


async def _fetch(url: str, sql: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetch(sql)
    finally:
        await conn.close()


def test_view_calls_the_sql_grid_function_not_a_copy():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))
        [row] = asyncio.run(_fetch(pg_url(name), "SELECT pg_get_viewdef('accident_conditions'::regclass) AS d"))
        assert "grid_bucket_key(" in row["d"] and "floor(" not in row["d"]


def test_sql_and_python_grid_keys_agree_at_x5_points():
    values = ", ".join(f"({i}, {lat}::float8, {lon}::float8)" for i, (lat, lon) in enumerate(X5_POINTS))
    with migrated_db() as name:
        rows = asyncio.run(_fetch(pg_url(name), f"SELECT i, grid_bucket_key(lat, lon) AS k FROM (VALUES {values}) v(i, lat, lon) ORDER BY i"))
    assert [r["k"] for r in rows] == [grid_bucket(lat, lon) for lat, lon in X5_POINTS]


def test_accident_conditions_joins_on_local_day_cell_and_tz():
    b = grid_bucket(40.05, -105.25)
    seed = ACCIDENT + "".join(
        f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, record_kind, source) "
        f"VALUES ({b}, 'America/Denver', DATE '2010-07-10' - {k}, {20 + k}, 'era5', 'open_meteo_archive');"
        for k in range(0, 8)
    ) + (
        f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, record_kind, source) "
        f"VALUES ({b}, 'UTC', DATE '2010-07-10', 99, 'era5', 'open_meteo_archive');"
    )
    with migrated_db(seed_sql=seed) as name:
        rows = asyncio.run(_fetch(pg_url(name), "SELECT accident_id, day_offset, tmax, tz, record_kind FROM accident_conditions ORDER BY day_offset"))
    assert {r["accident_id"] for r in rows} == {1}
    assert [(r["day_offset"], r["tmax"]) for r in rows] == [(-k, 20 + k) for k in range(6, -1, -1)]
    assert {(r["tz"], r["record_kind"]) for r in rows} == {("America/Denver", "era5")}


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, source) VALUES (1, 'UTC', '2020-01-01', 'guess', 't')",
        "INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, is_forecast, source) VALUES (1, 'UTC', '2020-01-01', 'era5', true, 't')",
        "INSERT INTO grid_bucket_series (grid_bucket, tz, first_era5_date) VALUES (1, 'UTC', '2020-01-01')",
        "INSERT INTO grid_bucket_series (grid_bucket, tz, first_era5_date, last_era5_date) VALUES (1, 'UTC', '2020-01-02', '2020-01-01')",
        "INSERT INTO cell_normals_status (grid_bucket, status) VALUES (1, 'done')",
        "INSERT INTO cell_climate_normals (grid_bucket, month, n_years, period_start_year, period_end_year, source, normals_version) VALUES (1, 13, 10, 2016, 2025, 't', 'n-v2')",
    ],
)
def test_checks_reject_bad_rows(sql):
    with migrated_db() as name:
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, sql)


def test_downgrade_refuses_with_paid_rows():
    b = grid_bucket(40.05, -105.25)
    seed = (f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, source) "
            f"VALUES ({b}, 'America/Denver', '2020-01-01', 'era5', 'open_meteo_archive');")
    with migrated_db(revision="0007_cell_daily_conditions", seed_sql=seed) as name:
        with pytest.raises(RuntimeError, match="refusing to downgrade 0007"):
            command.downgrade(_alembic_cfg(name), "0006_accidents_clean")
```

- [ ] **Step 2: Run to verify failure** — FAIL (relation does not exist).

- [ ] **Step 3: Implement**

`backend/alembic/versions/0007_cell_daily_conditions.py`:

```python
"""cell_daily_conditions per (0.1° cell, IANA timezone, local day), the series registry,
normals tables, accidents.tz, and accident_conditions.

Every Phase 2 weather-row column is created now (AQI, alerts, SWE), so later plans never
ALTER a table of tens of millions of rows. Lightning lives in plan 7's own tables. accident_conditions joins on
(cell, crag timezone, local date) — never on accident_id — through the grid_bucket_key
function from 0004, the single SQL definition of the grid key.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_cell_daily_conditions"
down_revision = "0006_accidents_clean"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accidents", sa.Column("tz", sa.Text(), nullable=True))
    op.create_table(
        "grid_bucket_series",
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("first_era5_date", sa.Date(), nullable=True),
        sa.Column("last_era5_date", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("grid_bucket", "tz"),
        sa.CheckConstraint("(first_era5_date IS NULL) = (last_era5_date IS NULL)", name="grid_bucket_series_span_pair_check"),
        sa.CheckConstraint("first_era5_date IS NULL OR first_era5_date <= last_era5_date", name="grid_bucket_series_span_order_check"),
    )
    op.create_table(
        "cell_daily_conditions",
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("tmax", sa.REAL(), nullable=True),
        sa.Column("tmin", sa.REAL(), nullable=True),
        sa.Column("precip_mm", sa.REAL(), nullable=True),
        sa.Column("snowfall_cm", sa.REAL(), nullable=True),
        sa.Column("snow_depth_cm", sa.REAL(), nullable=True),
        sa.Column("wind_max_ms", sa.REAL(), nullable=True),
        sa.Column("gust_max_ms", sa.REAL(), nullable=True),
        sa.Column("freeze_thaw", sa.Boolean(), nullable=True),
        sa.Column("swe_delta_mm", sa.REAL(), nullable=True),
        sa.Column("nws_alert_codes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("aqi", sa.SmallInteger(), nullable=True),
        sa.Column("record_kind", sa.Text(), nullable=False),
        sa.Column("is_forecast", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("grid_bucket", "tz", "date"),
        sa.CheckConstraint("record_kind IN ('era5', 'stopgap', 'forecast')", name="cell_daily_conditions_record_kind_check"),
        sa.CheckConstraint("is_forecast = (record_kind = 'forecast')", name="cell_daily_conditions_is_forecast_check"),
    )
    op.create_table(
        "cell_normals_status",
        sa.Column("grid_bucket", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("n_years", sa.SmallInteger(), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'complete', 'insufficient')", name="cell_normals_status_status_check"),
    )
    op.create_table(
        "cell_climate_normals",
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("tmax_mean", sa.REAL(), nullable=True),
        sa.Column("tmin_mean", sa.REAL(), nullable=True),
        sa.Column("precip_mm", sa.REAL(), nullable=True),
        sa.Column("snowfall_cm", sa.REAL(), nullable=True),
        sa.Column("freeze_thaw_days", sa.REAL(), nullable=True),
        sa.Column("n_years", sa.SmallInteger(), nullable=False),
        sa.Column("period_start_year", sa.SmallInteger(), nullable=False),
        sa.Column("period_end_year", sa.SmallInteger(), nullable=False),
        sa.Column("ref_elevation_m", sa.REAL(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("normals_version", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("grid_bucket", "month"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="cell_climate_normals_month_check"),
    )
    clim_cols = [
        sa.Column(name, sa.REAL(), nullable=True)
        for name in ("tmax_mean", "tmax_p10", "tmax_p90", "tmin_mean", "tmin_p10", "tmin_p90", "precip_mean",
                     "precip_p90", "snowfall_mean", "gust_p90", "freeze_thaw_freq", "lightning_day_freq")
    ]
    op.create_table(
        "cell_climatology",
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("doy", sa.SmallInteger(), nullable=False),
        *clim_cols,
        sa.Column("n_years", sa.SmallInteger(), nullable=False),
        sa.Column("period_start_year", sa.SmallInteger(), nullable=False),
        sa.Column("period_end_year", sa.SmallInteger(), nullable=False),
        sa.Column("climatology_version", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("grid_bucket", "doy"),
        sa.CheckConstraint("doy BETWEEN 1 AND 366", name="cell_climatology_doy_check"),
    )
    # accidents_clean_daily was created before accidents.tz existed, so tz comes from accidents itself.
    # Only trusted points (plan 2's point_trusted) get a point-level weather join; region_fallback rows do not.
    op.execute(
        "CREATE VIEW accident_conditions AS "
        "SELECT a.accident_id, o.day_offset, c.grid_bucket, c.tz, c.date, c.tmax, c.tmin, c.precip_mm, c.snowfall_cm, "
        "c.wind_max_ms, c.gust_max_ms, c.freeze_thaw, c.record_kind, c.is_forecast "
        "FROM accidents_clean_daily a JOIN accidents t ON t.accident_id = a.accident_id "
        "CROSS JOIN generate_series(-6, 0) AS o(day_offset) "
        "JOIN cell_daily_conditions c ON c.grid_bucket = grid_bucket_key(a.latitude, a.longitude) "
        "AND c.tz = t.tz AND c.date = a.date + o.day_offset "
        "WHERE a.point_trusted AND a.latitude IS NOT NULL AND a.longitude IS NOT NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP VIEW accident_conditions")
    for table in ("cell_daily_conditions", "cell_climate_normals", "cell_climatology"):
        rows = bind.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
        if rows:
            raise RuntimeError(f"refusing to downgrade 0007: {table} has {rows} rows (paid ERA5 data)")
    op.drop_table("cell_climatology")
    op.drop_table("cell_climate_normals")
    op.drop_table("cell_normals_status")
    op.drop_table("cell_daily_conditions")
    op.drop_table("grid_bucket_series")
    op.drop_column("accidents", "tz")
```

The downgrade checks rows after dropping the view inside the same transaction; a refusal rolls both back.

`backend/app/models/conditions.py`:

```python
"""Daily conditions per (0.1° cell, timezone, local day), the series registry and the
per-bucket normals (migration 0007)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import REAL, Boolean, CheckConstraint, Date, DateTime, Integer, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class GridBucketSeries(Base):
    __tablename__ = "grid_bucket_series"

    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    tz: Mapped[str] = mapped_column(Text, primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_era5_date: Mapped[date | None] = mapped_column(Date)
    last_era5_date: Mapped[date | None] = mapped_column(Date)


class CellDailyConditions(Base):
    __tablename__ = "cell_daily_conditions"
    __table_args__ = (
        CheckConstraint("record_kind IN ('era5', 'stopgap', 'forecast')", name="cell_daily_conditions_record_kind_check"),
        CheckConstraint("is_forecast = (record_kind = 'forecast')", name="cell_daily_conditions_is_forecast_check"),
    )

    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    tz: Mapped[str] = mapped_column(Text, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    tmax: Mapped[float | None] = mapped_column(REAL)
    tmin: Mapped[float | None] = mapped_column(REAL)
    precip_mm: Mapped[float | None] = mapped_column(REAL)
    snowfall_cm: Mapped[float | None] = mapped_column(REAL)
    snow_depth_cm: Mapped[float | None] = mapped_column(REAL)
    wind_max_ms: Mapped[float | None] = mapped_column(REAL)
    gust_max_ms: Mapped[float | None] = mapped_column(REAL)
    freeze_thaw: Mapped[bool | None] = mapped_column(Boolean)
    swe_delta_mm: Mapped[float | None] = mapped_column(REAL)
    nws_alert_codes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    aqi: Mapped[int | None] = mapped_column(SmallInteger)
    record_kind: Mapped[str] = mapped_column(Text)
    is_forecast: Mapped[bool] = mapped_column(Boolean, server_default="false")
    model: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class CellNormalsStatus(Base):
    __tablename__ = "cell_normals_status"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'complete', 'insufficient')", name="cell_normals_status_status_check"),
    )

    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    status: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_years: Mapped[int | None] = mapped_column(SmallInteger)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class CellClimateNormals(Base):
    __tablename__ = "cell_climate_normals"
    __table_args__ = (CheckConstraint("month BETWEEN 1 AND 12", name="cell_climate_normals_month_check"),)

    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    tmax_mean: Mapped[float | None] = mapped_column(REAL)
    tmin_mean: Mapped[float | None] = mapped_column(REAL)
    precip_mm: Mapped[float | None] = mapped_column(REAL)
    snowfall_cm: Mapped[float | None] = mapped_column(REAL)
    freeze_thaw_days: Mapped[float | None] = mapped_column(REAL)
    n_years: Mapped[int] = mapped_column(SmallInteger)
    period_start_year: Mapped[int] = mapped_column(SmallInteger)
    period_end_year: Mapped[int] = mapped_column(SmallInteger)
    ref_elevation_m: Mapped[float | None] = mapped_column(REAL)
    source: Mapped[str] = mapped_column(Text)
    normals_version: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class CellClimatology(Base):
    __tablename__ = "cell_climatology"
    __table_args__ = (CheckConstraint("doy BETWEEN 1 AND 366", name="cell_climatology_doy_check"),)

    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    doy: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    tmax_mean: Mapped[float | None] = mapped_column(REAL)
    tmax_p10: Mapped[float | None] = mapped_column(REAL)
    tmax_p90: Mapped[float | None] = mapped_column(REAL)
    tmin_mean: Mapped[float | None] = mapped_column(REAL)
    tmin_p10: Mapped[float | None] = mapped_column(REAL)
    tmin_p90: Mapped[float | None] = mapped_column(REAL)
    precip_mean: Mapped[float | None] = mapped_column(REAL)
    precip_p90: Mapped[float | None] = mapped_column(REAL)
    snowfall_mean: Mapped[float | None] = mapped_column(REAL)
    gust_p90: Mapped[float | None] = mapped_column(REAL)
    freeze_thaw_freq: Mapped[float | None] = mapped_column(REAL)
    lightning_day_freq: Mapped[float | None] = mapped_column(REAL)
    n_years: Mapped[int] = mapped_column(SmallInteger)
    period_start_year: Mapped[int] = mapped_column(SmallInteger)
    period_end_year: Mapped[int] = mapped_column(SmallInteger)
    climatology_version: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
```

In `backend/app/models/accident.py` add `tz: Mapped[str | None] = mapped_column(Text)` beside the Phase 2a columns. Register `conditions` in `app/models/__init__.py` (`from app.models import conditions  # noqa: F401`) and add `"app.models.conditions"` to the models mypy block.

Grants (`grants_phase2.sql`, "Plan 3 (0007)"):

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON public.cell_daily_conditions TO ingest;
GRANT SELECT, INSERT, UPDATE ON public.grid_bucket_series, public.cell_normals_status,
  public.cell_climate_normals, public.cell_climatology TO ingest;
GRANT SELECT ON public.accidents_clean, public.accidents_clean_daily, public.accident_conditions TO ingest;
```

`trainer` gets nothing here (D13: it is created NOLOGIN with no grants; Phase 3 grants SELECT on views). `DELETE` on `cell_daily_conditions` is for the 3-year prune of non-incident series (Task 5), run only by `era5_window`.

`verify_roles_phase2.sql` `ingest_writes`: `('public.cell_daily_conditions','INSERT'), ('public.cell_daily_conditions','UPDATE'), ('public.cell_daily_conditions','DELETE'), ('public.grid_bucket_series','INSERT'), ('public.grid_bucket_series','UPDATE'), ('public.cell_normals_status','INSERT'), ('public.cell_normals_status','UPDATE'), ('public.cell_climate_normals','INSERT'), ('public.cell_climate_normals','UPDATE'), ('public.cell_climatology','INSERT'), ('public.cell_climatology','UPDATE')`.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_migration_0007.py tests/test_roles_phase2.py tests/test_migrations.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0007_cell_daily_conditions.py backend/app/models/ backend/tests/test_migration_0007.py backend/pyproject.toml backend/db/roles/ && git commit -m "feat(db): 0007 local-day cell conditions, series registry, normals tables, accident_conditions"`

---

### Task 3: Validating conditions writer

**Files:**
- Create: `backend/app/pipelines/cell_conditions.py`, `backend/tests/test_cell_conditions.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: `LocationResponse` (Task 1), `ValidationReport`, `range_problem` (plan 1), table from Task 2.
- Produces: `RANGES: dict[str, tuple[float, float]]`, `RecordKind = Literal["era5", "stopgap", "forecast"]`, `Origin = Literal["archive", "forecast_api"]`, `@dataclass(frozen=True) DayRow(grid_bucket: int, tz: str, date: date, tmax, tmin, precip_mm, snowfall_cm, wind_max_ms, gust_max_ms: float | None, freeze_thaw: bool | None, record_kind: RecordKind, model: str, source: str)`, `day_rows(grid_bucket: int, tz: str, response: LocationResponse, *, today: date, origin: Origin, model: str, source: str, report: ValidationReport) -> list[DayRow]`, `async upsert_rows(conn, rows: Sequence[DayRow], *, run_id: uuid.UUID) -> int`.
- Rules: `origin="archive"` → `record_kind='era5'`, days after `today` quarantined `future`. `origin="forecast_api"` → days before `today` are `stopgap` (non-ERA5, flagged), `today` onward `forecast`. A response whose `timezone` differs from the series `tz` quarantines every day `timezone_mismatch`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_cell_conditions.py`:

```python
import asyncio
import uuid
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.cell_conditions import DayRow, day_rows, upsert_rows
from app.pipelines.open_meteo import LocationResponse
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)
TZ = "America/Denver"


def _resp(days, tmax, tmin, gust, tz=TZ):
    n = len(days)
    return LocationResponse.model_validate({
        "latitude": 40.0, "longitude": -105.3, "elevation": 1650.0, "timezone": tz,
        "daily": {"time": days, "temperature_2m_max": tmax, "temperature_2m_min": tmin,
                  "precipitation_sum": [0.0] * n, "snowfall_sum": [0.0] * n,
                  "wind_speed_10m_max": [5.0] * n, "wind_gusts_10m_max": gust},
    })


def _archive(response, report):
    return day_rows(1, TZ, response, today=TODAY, origin="archive", model="era5_seamless",
                    source="open_meteo_archive", report=report)


def test_null_values_stay_null():
    report = ValidationReport("t")
    [row] = _archive(_resp(["2020-01-01"], [None], [-3.0], [None]), report)
    assert (row.tmax, row.gust_max_ms, row.freeze_thaw, row.record_kind) == (None, None, None, "era5")
    assert report.accepted == 1


def test_freeze_thaw_and_quarantine_rules():
    report = ValidationReport("t")
    rows = _archive(
        _resp(["2020-01-01", "2020-01-02", "2020-01-03", "2026-09-29"], [2.0, 80.0, -5.0, 1.0], [-1.0, 0.0, 3.0, 0.0],
              [10.0, 10.0, 10.0, 10.0]),
        report,
    )
    assert [(r.date.day, r.freeze_thaw) for r in rows] == [(1, True)]
    assert report.quarantined == {"out_of_range": 1, "tmax_below_tmin": 1, "future": 1}


def test_forecast_api_splits_past_days_into_stopgap():
    report = ValidationReport("t")
    days = ["2026-09-26", "2026-09-27", "2026-09-28", "2026-09-30"]
    rows = day_rows(1, TZ, _resp(days, [5.0] * 4, [-2.0] * 4, [9.0] * 4), today=TODAY, origin="forecast_api",
                    model="best_match", source="open_meteo_forecast", report=report)
    assert [r.record_kind for r in rows] == ["stopgap", "stopgap", "forecast", "forecast"]


def test_timezone_mismatch_quarantines_every_day():
    report = ValidationReport("t")
    assert _archive(_resp(["2020-01-01", "2020-01-02"], [1.0, 1.0], [0.0, 0.0], [1.0, 1.0], tz="GMT"), report) == []
    assert report.quarantined == {"timezone_mismatch": 2}


@requires_pg
def test_precedence_era5_over_stopgap_over_forecast():
    def row(tmax: float, kind: str) -> DayRow:
        return DayRow(1, TZ, date(2026, 9, 20), tmax, -1.0, 0.0, 0.0, 5.0, 9.0, True, kind, "m", "s")

    async def scenario(url: str) -> list[tuple[float, str, bool]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                for tmax, kind in [(10.0, "forecast"), (11.0, "stopgap"), (99.0, "forecast"), (12.0, "era5"),
                                   (98.0, "stopgap"), (97.0, "forecast")]:
                    await upsert_rows(conn, [row(tmax, kind)], run_id=uuid.uuid4())
                result = await conn.execute(text("SELECT tmax, record_kind, is_forecast FROM cell_daily_conditions"))
                return [(float(a), str(b), bool(c)) for a, b, c in result.all()]
        finally:
            await engine.dispose()

    with migrated_db() as name:
        assert asyncio.run(scenario(sa_url(name))) == [(12.0, "era5", False)]


@requires_pg
def test_stopgap_replaces_forecast_but_not_the_reverse():
    def row(tmax: float, kind: str) -> DayRow:
        return DayRow(1, TZ, date(2026, 9, 20), tmax, -1.0, 0.0, 0.0, 5.0, 9.0, True, kind, "m", "s")

    async def scenario(url: str) -> list[tuple[float, str]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await upsert_rows(conn, [row(10.0, "forecast")], run_id=uuid.uuid4())
                await upsert_rows(conn, [row(11.0, "stopgap")], run_id=uuid.uuid4())
                await upsert_rows(conn, [row(99.0, "forecast")], run_id=uuid.uuid4())
                result = await conn.execute(text("SELECT tmax, record_kind FROM cell_daily_conditions"))
                return [(float(a), str(b)) for a, b in result.all()]
        finally:
            await engine.dispose()

    with migrated_db() as name:
        assert asyncio.run(scenario(sa_url(name))) == [(11.0, "stopgap")]
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/cell_conditions.py`:

```python
"""Validated day rows into cell_daily_conditions. Precedence per (cell, tz, local day):
era5 beats stopgap beats forecast. Stopgap rows are Forecast-API past days written between
the yearly ERA5 windows; they are flagged so no consumer mistakes them for ERA5, and the
next window overwrites them."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.open_meteo import LocationResponse
from app.pipelines.validate import ValidationReport, range_problem

RecordKind = Literal["era5", "stopgap", "forecast"]
Origin = Literal["archive", "forecast_api"]

RANGES: dict[str, tuple[float, float]] = {
    "tmax": (-60.0, 55.0),
    "tmin": (-70.0, 50.0),
    "precip_mm": (0.0, 500.0),
    "snowfall_cm": (0.0, 300.0),
    "wind_max_ms": (0.0, 75.0),
    "gust_max_ms": (0.0, 75.0),
}


@dataclass(frozen=True)
class DayRow:
    grid_bucket: int
    tz: str
    date: date
    tmax: float | None
    tmin: float | None
    precip_mm: float | None
    snowfall_cm: float | None
    wind_max_ms: float | None
    gust_max_ms: float | None
    freeze_thaw: bool | None
    record_kind: RecordKind
    model: str
    source: str


def _kind(origin: Origin, day: date, today: date) -> RecordKind:
    if origin == "archive":
        return "era5"
    return "stopgap" if day < today else "forecast"


def day_rows(
    grid_bucket: int,
    tz: str,
    response: LocationResponse,
    *,
    today: date,
    origin: Origin,
    model: str,
    source: str,
    report: ValidationReport,
) -> list[DayRow]:
    d = response.daily
    if response.timezone is not None and response.timezone != tz:
        for day in d.time:
            report.quarantine(f"{grid_bucket}:{tz}:{day.isoformat()}", "timezone_mismatch", got=response.timezone)
        return []
    rows: list[DayRow] = []
    for i, day in enumerate(d.time):
        ref = f"{grid_bucket}:{tz}:{day.isoformat()}"
        values = {
            "tmax": d.temperature_2m_max[i],
            "tmin": d.temperature_2m_min[i],
            "precip_mm": d.precipitation_sum[i],
            "snowfall_cm": d.snowfall_sum[i],
            "wind_max_ms": d.wind_speed_10m_max[i],
            "gust_max_ms": d.wind_gusts_10m_max[i],
        }
        if origin == "archive" and day > today:
            report.quarantine(ref, "future")
            continue
        bad = next((f"{k}" for k, v in values.items() if range_problem(v, *RANGES[k]) is not None), None)
        if bad is not None:
            report.quarantine(ref, "out_of_range", field=bad, value=values[bad])
            continue
        tmax, tmin = values["tmax"], values["tmin"]
        if tmax is not None and tmin is not None and tmax < tmin:
            report.quarantine(ref, "tmax_below_tmin")
            continue
        freeze_thaw = (tmax > 0 and tmin < 0) if tmax is not None and tmin is not None else None
        report.accept()
        rows.append(DayRow(grid_bucket, tz, day, tmax, tmin, values["precip_mm"], values["snowfall_cm"],
                           values["wind_max_ms"], values["gust_max_ms"], freeze_thaw, _kind(origin, day, today),
                           model, source))
    return rows


_RANK = "(CASE {t}.record_kind WHEN 'era5' THEN 3 WHEN 'stopgap' THEN 2 ELSE 1 END)"
UPSERT = text(
    "INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, tmin, precip_mm, snowfall_cm, wind_max_ms, "
    "gust_max_ms, freeze_thaw, record_kind, is_forecast, model, source, fetched_at, run_id) VALUES (:grid_bucket, :tz, "
    ":date, :tmax, :tmin, :precip_mm, :snowfall_cm, :wind_max_ms, :gust_max_ms, :freeze_thaw, :record_kind, "
    "CAST(:record_kind AS text) = 'forecast', :model, :source, now(), :run_id) "
    "ON CONFLICT (grid_bucket, tz, date) DO UPDATE SET tmax = EXCLUDED.tmax, tmin = EXCLUDED.tmin, "
    "precip_mm = EXCLUDED.precip_mm, snowfall_cm = EXCLUDED.snowfall_cm, wind_max_ms = EXCLUDED.wind_max_ms, "
    "gust_max_ms = EXCLUDED.gust_max_ms, freeze_thaw = EXCLUDED.freeze_thaw, record_kind = EXCLUDED.record_kind, "
    "is_forecast = EXCLUDED.is_forecast, model = EXCLUDED.model, source = EXCLUDED.source, fetched_at = now(), "
    "run_id = EXCLUDED.run_id "
    f"WHERE {_RANK.format(t='cell_daily_conditions')} <= {_RANK.format(t='EXCLUDED')}"
)


async def upsert_rows(conn: AsyncConnection, rows: Sequence[DayRow], *, run_id: uuid.UUID) -> int:
    if not rows:
        return 0
    await conn.execute(UPSERT, [asdict(r) | {"run_id": run_id} for r in rows])
    return len(rows)
```

The upsert only touches the weather columns, so plan 7's alert, AQI and SWE values on the same row survive an ERA5 replacement. Equal ranks replace (a newer forecast replaces an older one). Append `"app.pipelines.cell_conditions"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_cell_conditions.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/cell_conditions.py backend/tests/test_cell_conditions.py backend/pyproject.toml && git commit -m "feat(pipelines): validated conditions writer, era5 > stopgap > forecast upsert"`

---

### Task 4: Local days, series registration, the `pipelines` dependency group

**Files:**
- Create: `backend/app/pipelines/localday.py`, `backend/tests/test_localday.py`
- Modify: `backend/pyproject.toml`, `backend/uv.lock`, `.github/workflows/ci.yml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Consumes: `grid.grid_bucket`, `validate.in_us`, `ValidationReport` (plan 1); tables from Task 2.
- Produces:
  - dependency group `pipelines = ["timezonefinder", "tzdata"]` (this plan creates the group; plan 4 appends `h3`, plan 5 `rasterio`).
  - `tz_for_point(lat: float, lon: float) -> str | None` (IANA zone; `None` for open water / `Etc/*` zones).
  - `local_date(ts_utc: datetime, tz: str) -> date` (aware timestamps only).
  - `async register_series(conn, points: Iterable[tuple[float, float]], *, report: ValidationReport) -> set[tuple[int, str]]` — inserts `(grid_bucket, tz)` into `grid_bucket_series` and the bucket into `cell_normals_status` as `pending` (both `ON CONFLICT DO NOTHING`); quarantines `outside_us` / `no_timezone`. Every later plan registers new points through this function.
  - `async assign_accident_tz(conn, *, report: ValidationReport) -> int` (clean accidents with a trusted point and `tz IS NULL`).
  - `INCIDENT_POINTS_SQL`, `ROUTE_POINTS_SQL`; CLI `python -m app.pipelines.localday register` (accident tz + incident series + MP route-point series; free, idempotent, no Open-Meteo call).

- [ ] **Step 1: Add the group and check the library's API on the installed version**

```bash
cd backend && uv add --group pipelines 'timezonefinder>=6.5' 'tzdata>=2024.1'
uv run --group pipelines python -c "from timezonefinder import TimezoneFinder; print(TimezoneFinder().timezone_at(lng=-105.3, lat=40.0))"
```

Expected: `America/Denver`. The call signature `TimezoneFinder().timezone_at(lng=..., lat=...)` is written here from the library's documentation as recalled, not re-read this session; if the printed value or the call fails, stop and adapt `tz_for_point` to the installed version's API before writing code. `uv.lock` pins the exact versions.

In `.github/workflows/ci.yml` `backend` job: `run: uv sync --frozen --group pipelines`; the audit step becomes `uv export --frozen --no-dev --group pipelines --no-emit-project --format requirements.txt | uv run pip-audit -r /dev/stdin --require-hashes --disable-pip`. The Dockerfile keeps `uv sync --frozen --no-dev --no-install-project` (the `pipelines` group is not a default group, so the API image stays without it). Add a mypy override `[[tool.mypy.overrides]] module = ["timezonefinder", "timezonefinder.*"] ignore_missing_imports = true` (harmless if the package ships `py.typed`).

- [ ] **Step 2: Failing tests** — `backend/tests/test_localday.py`:

```python
import asyncio
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.grid import grid_bucket
from app.pipelines.localday import local_date, register_series, tz_for_point
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url


@pytest.mark.parametrize(
    "lat,lon,zone",
    [
        (40.0, -105.3, "America/Denver"),
        (21.3, -157.8, "Pacific/Honolulu"),
        (61.2, -149.9, "America/Anchorage"),
        (51.88, -176.65, "America/Adak"),
        (36.58, -118.29, "America/Los_Angeles"),
        (44.27, -71.3, "America/New_York"),
    ],
)
def test_known_zones(lat, lon, zone):
    assert tz_for_point(lat, lon) == zone


def test_open_ocean_has_no_civil_zone():
    assert tz_for_point(35.0, -140.0) is None


def test_evening_storm_is_the_previous_local_day():
    storm = datetime(2020, 7, 11, 1, 30, tzinfo=timezone.utc)
    assert storm.date() == date(2020, 7, 11)
    assert local_date(storm, "America/Denver") == date(2020, 7, 10)
    assert local_date(storm, "Pacific/Honolulu") == date(2020, 7, 10)


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError, match="aware"):
        local_date(datetime(2020, 7, 11, 1, 30), "America/Denver")


@requires_pg
def test_register_series_is_idempotent_and_marks_normals_pending():
    points = [(40.01, -105.27), (40.02, -105.28), (50.0, -115.5)]

    async def scenario(url: str) -> tuple[set[tuple[int, str]], dict[str, int], list[tuple[object, ...]], list[tuple[object, ...]]]:
        engine = create_async_engine(url)
        try:
            report = ValidationReport("t")
            async with engine.begin() as conn:
                first = await register_series(conn, points, report=report)
                await register_series(conn, points, report=ValidationReport("t"))
                series = (await conn.execute(text("SELECT grid_bucket, tz, first_era5_date FROM grid_bucket_series"))).all()
                status = (await conn.execute(text("SELECT grid_bucket, status FROM cell_normals_status"))).all()
            return first, dict(report.quarantined), [tuple(r) for r in series], [tuple(r) for r in status]
        finally:
            await engine.dispose()

    b = grid_bucket(40.01, -105.27)
    with migrated_db() as name:
        first, quarantined, series, status = asyncio.run(scenario(sa_url(name)))
    assert first == {(b, "America/Denver")}
    assert quarantined == {"outside_us": 1}
    assert series == [(b, "America/Denver", None)]
    assert status == [(b, "pending")]
```

- [ ] **Step 3: Run to verify failure** — `cd backend && uv run pytest tests/test_localday.py -q` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 4: Implement** `backend/app/pipelines/localday.py`:

```python
"""Local calendar days (owner decision 2026-09-28: a weather day is the crag's local day).

Every conditions series is keyed by (grid_bucket, IANA timezone) and requested with that
timezone, so a 19:30 thunderstorm lands on the day the accident report gives, not on the
next UTC day. Points are registered here; each new bucket's normals start 'pending' and are
filled in the next Professional window (missing until then, never 0).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterable
from datetime import date, datetime
from functools import lru_cache
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.grid import grid_bucket
from app.pipelines.validate import ValidationReport, in_us

if TYPE_CHECKING:
    from timezonefinder import TimezoneFinder

INCIDENT_POINTS_SQL = (
    "SELECT DISTINCT latitude, longitude FROM accidents_clean_daily "
    "WHERE point_trusted AND latitude IS NOT NULL AND longitude IS NOT NULL"
)
# Only points that carry routes: a parent area's centroid would buy a paid series nobody scores.
ROUTE_POINTS_SQL = (
    "SELECT DISTINCT COALESCE(r.latitude, l.latitude), COALESCE(r.longitude, l.longitude) "
    "FROM mp_routes r LEFT JOIN mp_locations l ON l.mp_id = r.location_id "
    "WHERE COALESCE(r.latitude, l.latitude) IS NOT NULL AND COALESCE(r.longitude, l.longitude) IS NOT NULL"
)


@lru_cache(maxsize=1)
def _finder() -> TimezoneFinder:
    from timezonefinder import TimezoneFinder

    return TimezoneFinder()


def tz_for_point(lat: float, lon: float) -> str | None:
    name = _finder().timezone_at(lng=lon, lat=lat)
    # Open water comes back as a fixed-offset Etc/GMT±N zone, which has no civil calendar day.
    if name is None or name.startswith("Etc/"):
        return None
    return str(name)


def local_date(ts_utc: datetime, tz: str) -> date:
    if ts_utc.tzinfo is None:
        raise ValueError("local_date needs a timezone-aware UTC timestamp")
    return ts_utc.astimezone(ZoneInfo(tz)).date()


async def register_series(
    conn: AsyncConnection, points: Iterable[tuple[float, float]], *, report: ValidationReport
) -> set[tuple[int, str]]:
    series: set[tuple[int, str]] = set()
    for lat, lon in points:
        ref = f"{lat:.5f}:{lon:.5f}"
        if not in_us(lat, lon):
            report.quarantine(ref, "outside_us")
            continue
        tz = tz_for_point(lat, lon)
        if tz is None:
            report.quarantine(ref, "no_timezone")
            continue
        report.accept()
        series.add((grid_bucket(lat, lon), tz))
    if series:
        await conn.execute(
            text("INSERT INTO grid_bucket_series (grid_bucket, tz) VALUES (:b, :tz) ON CONFLICT DO NOTHING"),
            [{"b": b, "tz": tz} for b, tz in sorted(series)],
        )
        await conn.execute(
            text("INSERT INTO cell_normals_status (grid_bucket, status) VALUES (:b, 'pending') ON CONFLICT DO NOTHING"),
            [{"b": b} for b in sorted({b for b, _ in series})],
        )
    return series


async def assign_accident_tz(conn: AsyncConnection, *, report: ValidationReport) -> int:
    rows = (await conn.execute(text(
        "SELECT accident_id, latitude, longitude FROM accidents WHERE tz IS NULL AND latitude IS NOT NULL "
        "AND longitude IS NOT NULL AND accident_id IN (SELECT accident_id FROM accidents_clean WHERE point_trusted)"
    ))).all()
    updates: list[dict[str, object]] = []
    for accident_id, lat, lon in rows:
        zone = tz_for_point(float(lat), float(lon)) if in_us(float(lat), float(lon)) else None
        if zone is None:
            report.quarantine(f"accident:{accident_id}", "no_timezone")
            continue
        updates.append({"id": accident_id, "tz": zone})
    if updates:
        await conn.execute(text("UPDATE accidents SET tz = :tz WHERE accident_id = :id AND tz IS NULL"), updates)
    return len(updates)


async def register_all(conn: AsyncConnection, *, report: ValidationReport) -> dict[str, int]:
    assigned = await assign_accident_tz(conn, report=report)
    incident = [(float(a), float(b)) for a, b in (await conn.execute(text(INCIDENT_POINTS_SQL))).all()]
    routes = [(float(a), float(b)) for a, b in (await conn.execute(text(ROUTE_POINTS_SQL))).all()]
    incident_series = await register_series(conn, incident, report=report)
    route_series = await register_series(conn, routes, report=report)
    return {"accident_tz_assigned": assigned, "incident_series": len(incident_series),
            "route_series": len(route_series), "series_total": len(incident_series | route_series)}


async def _main() -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.pipelines.ingest_log import finish_run, start_run, write_quarantine

    engine = ingest_engine()
    report = ValidationReport("series_register")
    try:
        async with engine.begin() as conn:
            counts = await register_all(conn, report=report)
            run_id = await start_run(conn, source="series_register", window_start=None, window_end=None, content_sha256=None)
            await write_quarantine(conn, run_id, report)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=counts["series_total"])
    finally:
        await engine.dispose()
    return {**counts, "report": report.summary()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["register"])
    parser.parse_args()
    print(json.dumps(asyncio.run(_main()), sort_keys=True, default=str))
```

Grants (`grants_phase2.sql`, "Plan 3 (local days)"):

```sql
GRANT SELECT ON public.accidents, public.mp_routes, public.mp_locations TO ingest;
GRANT UPDATE (tz) ON public.accidents TO ingest;
```

`verify_roles_phase2.sql`: add `('public.accidents','tz','UPDATE')` to the column-privilege check (`has_column_privilege('ingest', table, column, priv)`); create that check block if plan 2 has not already. Append `"app.pipelines.localday"` to strict mypy.

- [ ] **Step 5: Run** — `cd backend && uv sync --group pipelines && uv run pytest tests/test_localday.py tests/test_roles_phase2.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 6: Commit** — `git add backend/app/pipelines/localday.py backend/tests/test_localday.py backend/pyproject.toml backend/uv.lock .github/workflows/ci.yml backend/db/roles/ && git commit -m "feat(pipelines): local-day timezones, series registry, pipelines dependency group"`

---

### Task 5: Professional-window budget and the ERA5 gap filler

**Files:**
- Create: `backend/app/pipelines/pro_window.py`, `backend/app/pipelines/era5_fill.py`, `backend/tests/test_pro_window.py`, `backend/tests/test_era5_fill.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: Tasks 1–4; `grid.bucket_center`, `grid.grid_bucket_sql`; `ingest_log.*`.
- Produces (`pro_window.py`): `PRO_WINDOW_DAYS = 30`, `JANUARY_EARLIEST_DAY = 10`, `PAID_SOURCES = ("era5_fill", "era5_normals")`, `@dataclass(frozen=True) ProWindow(start: date, initial: bool = False)` with `.end` and `.contains(today) -> bool` (a non-initial window must start in January on or after the 10th), `require_open(window: ProWindow, today: date) -> None` (`SystemExit` outside), `async units_spent(conn, window: ProWindow) -> float`, `@dataclass Budget(max_units: float, spent: float)` with `fits(units) -> bool`, `spend(units) -> None`.
- Produces (`era5_fill.py`): `SOURCE = "era5_fill"`, `ERA5_START = date(1940, 1, 1)`, `INCIDENT_START_DEFAULT = ERA5_START` `[default pending: DP1]`, `ERA5_LAG_DAYS = 5`, `HISTORY_YEARS = 3`, `SeriesCoverage`, `Gap`, `Chunk` (`.units()`), `last_era5_day(today)`, `history_start(today, history_years=3)`, `series_gaps(coverage, *, today, incident_start, history_years=3) -> list[Gap]`, `plan_chunks(gaps, *, batch, years_per_chunk) -> list[Chunk]`, `async load_coverage(conn) -> list[SeriesCoverage]`, `async run(engine_factory, client, chunks, *, today, budget) -> dict[str, object]`, `PRUNE_SOURCE = "era5_prune"`, `MAX_INCIDENT_DROP = 0.10`, `class PruneRefused(Exception)` (`.kept`), `async prune(conn, *, today, history_years=3) -> tuple[int, int]` (incident series kept, rows deleted), `async log_prune(conn, *, status, kept, deleted, problem) -> None`.
- Rules: an incident series needs ERA5 from `min(incident_start, history_start)`; any other series from `history_start` (1 January, three years back); both end at `today − 5`. A failed chunk counts as spent and blocks the rest of its group. Non-incident rows older than `history_start` are pruned (spec: 3-year window); incident series are never pruned. This is the only 3-year prune in Phase 2 (plan 7 has none). It refuses (`PruneRefused`, logged `rejected`, `era5_window` exits 1) when the incident set is empty or has shrunk by more than 10% since the last successful prune (`last_ok_rows_in('era5_prune')`).

- [ ] **Step 1: Failing tests** — `backend/tests/test_pro_window.py`:

```python
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.pro_window import Budget, ProWindow, require_open, units_spent
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url


def test_yearly_windows_start_in_january_on_or_after_the_10th():
    with pytest.raises(ValueError, match="January"):
        ProWindow(date(2027, 1, 9))
    with pytest.raises(ValueError, match="January"):
        ProWindow(date(2027, 2, 10))
    assert ProWindow(date(2027, 1, 10)).end == date(2027, 2, 8)
    assert ProWindow(date(2026, 10, 5), initial=True).contains(date(2026, 11, 3))


def test_require_open_refuses_outside_the_window():
    window = ProWindow(date(2027, 1, 10))
    require_open(window, date(2027, 2, 8))
    with pytest.raises(SystemExit, match="outside"):
        require_open(window, date(2027, 2, 9))
    with pytest.raises(SystemExit, match="outside"):
        require_open(window, date(2027, 1, 9))


def test_budget_never_exceeds_the_cap():
    budget = Budget(max_units=100, spent=90)
    assert budget.fits(10) and not budget.fits(11)
    budget.spend(10)
    assert not budget.fits(1)


@requires_pg
def test_units_spent_is_cumulative_across_runs():
    today = datetime.now(timezone.utc).date()
    window = ProWindow(today - timedelta(days=1), initial=True)

    async def scenario(url: str) -> float:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                for source, units in [("era5_fill", 100.0), ("era5_normals", 50.0), ("era5_fill", 7.0),
                                      ("openbeta", 999.0), ("era5_fill", 1000.0)]:
                    run_id = await start_run(conn, source=source, window_start=None, window_end=None, content_sha256=None)
                    await finish_run(conn, run_id, status="ok", report=ValidationReport(source), rows_upserted=0,
                                     cost_units=units)
                await conn.execute(text("UPDATE source_ingest_log SET started_at = now() - interval '40 days' "
                                        "WHERE cost_units = 1000"))
                return await units_spent(conn, window)
        finally:
            await engine.dispose()

    with migrated_db() as name:
        assert asyncio.run(scenario(sa_url(name))) == 157.0
```

`backend/tests/test_era5_fill.py`:

```python
import asyncio
from datetime import date, timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.era5_fill import (
    Chunk,
    Gap,
    PruneRefused,
    SeriesCoverage,
    load_coverage,
    log_prune,
    plan_chunks,
    prune,
    run,
    series_gaps,
)
from app.pipelines.grid import grid_bucket
from app.pipelines.open_meteo import DAILY_VARS, ArchiveClient
from app.pipelines.pro_window import Budget
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)
TZ = "America/Denver"


def test_gaps_for_new_extended_and_complete_series():
    coverage = [
        SeriesCoverage(1, TZ, None, None, incident=False),
        SeriesCoverage(2, TZ, date(2023, 1, 1), date(2026, 1, 5), incident=True),
        SeriesCoverage(3, TZ, date(2023, 1, 1), date(2026, 9, 23), incident=False),
    ]
    gaps = series_gaps(coverage, today=TODAY, incident_start=date(1940, 1, 1))
    assert gaps == [
        Gap(1, TZ, date(2023, 1, 1), date(2026, 9, 23), "forward"),
        Gap(2, TZ, date(1940, 1, 1), date(2022, 12, 31), "backward"),
        Gap(2, TZ, date(2026, 1, 6), date(2026, 9, 23), "forward"),
    ]


def test_chunks_cover_every_day_once_in_fill_order():
    forward = plan_chunks([Gap(b, TZ, date(2020, 1, 1), date(2026, 9, 23), "forward") for b in (1, 2, 3)],
                          batch=2, years_per_chunk=3)
    assert [(c.buckets, c.start, c.end) for c in forward] == [
        ((1, 2), date(2020, 1, 1), date(2022, 12, 31)),
        ((1, 2), date(2023, 1, 1), date(2025, 12, 31)),
        ((1, 2), date(2026, 1, 1), date(2026, 9, 23)),
        ((3,), date(2020, 1, 1), date(2022, 12, 31)),
        ((3,), date(2023, 1, 1), date(2025, 12, 31)),
        ((3,), date(2026, 1, 1), date(2026, 9, 23)),
    ]
    backward = plan_chunks([Gap(1, TZ, date(2015, 1, 1), date(2022, 12, 31), "backward")], batch=50, years_per_chunk=3)
    assert [(c.start, c.end) for c in backward] == [
        (date(2020, 1, 1), date(2022, 12, 31)), (date(2017, 1, 1), date(2019, 12, 31)), (date(2015, 1, 1), date(2016, 12, 31)),
    ]
    assert len({c.group for c in backward}) == 1
    assert Chunk(TZ, (1, 2), date(2020, 1, 1), date(2020, 1, 14), "g").units() == 2


def test_different_timezones_never_share_a_chunk():
    chunks = plan_chunks([Gap(1, TZ, date(2020, 1, 1), date(2020, 1, 5), "forward"),
                          Gap(2, "America/Los_Angeles", date(2020, 1, 1), date(2020, 1, 5), "forward")],
                         batch=50, years_per_chunk=1)
    assert sorted((c.tz, c.buckets) for c in chunks) == [("America/Denver", (1,)), ("America/Los_Angeles", (2,))]


def _handler(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
    days = [(start + timedelta(d)).isoformat() for d in range((end - start).days + 1)]
    body = []
    for lat, lon in zip(params["latitude"].split(","), params["longitude"].split(",")):
        daily: dict[str, object] = {"time": days}
        for var in DAILY_VARS:
            daily[var] = [1.0] * len(days)
        body.append({"latitude": float(lat), "longitude": float(lon), "timezone": params["timezone"], "daily": daily})
    return httpx.Response(200, json=body)


def _failing(request: httpx.Request) -> httpx.Response:
    return httpx.Response(400, json={"error": True, "reason": "bad"})


async def _coverage_and_rows(url: str) -> tuple[list[SeriesCoverage], int]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            cov = await load_coverage(conn)
            n = (await conn.execute(text("SELECT count(*) FROM cell_daily_conditions WHERE record_kind = 'era5'"))).scalar_one()
        return cov, int(n)
    finally:
        await engine.dispose()


@requires_pg
def test_resume_fills_only_the_remaining_gap():
    b = grid_bucket(40.0, -105.3)
    seed = f"INSERT INTO grid_bucket_series (grid_bucket, tz) VALUES ({b}, '{TZ}');"
    gap = Gap(b, TZ, date(2019, 12, 22), date(2020, 1, 10), "forward")

    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(_handler))
        capped = await run(lambda: create_async_engine(url), client, plan_chunks([gap], batch=50, years_per_chunk=1),
                           today=TODAY, budget=Budget(max_units=1, spent=0))
        cov_after_cap, _ = await _coverage_and_rows(url)
        remaining = Gap(b, TZ, cov_after_cap[0].last + timedelta(days=1), gap.end, "forward")
        full = await run(lambda: create_async_engine(url), client, plan_chunks([remaining], batch=50, years_per_chunk=1),
                         today=TODAY, budget=Budget(max_units=100, spent=0))
        cov, rows = await _coverage_and_rows(url)
        return capped, cov_after_cap, full, cov, rows

    with migrated_db(seed_sql=seed) as name:
        capped, cov_after_cap, full, cov, rows = asyncio.run(scenario(sa_url(name)))
    assert (capped["done"], capped["stopped_for_budget"]) == (1, True)
    assert (cov_after_cap[0].first, cov_after_cap[0].last) == (date(2019, 12, 22), date(2019, 12, 31))
    assert (full["done"], full["stopped_for_budget"]) == (1, False)
    assert (cov[0].first, cov[0].last, rows) == (date(2019, 12, 22), date(2020, 1, 10), 20)


@requires_pg
def test_failed_chunk_blocks_later_chunks_of_its_group():
    b = grid_bucket(40.0, -105.3)
    seed = f"INSERT INTO grid_bucket_series (grid_bucket, tz) VALUES ({b}, '{TZ}');"
    chunks = plan_chunks([Gap(b, TZ, date(2019, 12, 22), date(2020, 1, 10), "forward")], batch=50, years_per_chunk=1)

    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(_failing))
        budget = Budget(max_units=100, spent=0)
        result = await run(lambda: create_async_engine(url), client, chunks, today=TODAY, budget=budget)
        cov, rows = await _coverage_and_rows(url)
        return result, budget.spent, cov, rows

    with migrated_db(seed_sql=seed) as name:
        result, spent, cov, rows = asyncio.run(scenario(sa_url(name)))
    assert (result["failed"], result["blocked_after_failure"], result["done"]) == (1, 1, 0)
    assert spent == 1
    assert (cov[0].first, cov[0].last, rows) == (None, None, 0)


@requires_pg
def test_prune_keeps_incident_rows_and_refuses_an_empty_or_shrunken_incident_set():
    inc_b, other_b = grid_bucket(40.05, -105.25), grid_bucket(41.05, -105.25)
    seed = (
        "INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, "
        "date_precision, geocode_precision, tz) VALUES (1, 'AAC', '2020-06-01', 40.05, -105.25, true, 'climbing', 'US', "
        f"'day', 'crag', '{TZ}');"
        + "".join(
            f"INSERT INTO grid_bucket_series (grid_bucket, tz, first_era5_date, last_era5_date) VALUES ({b}, '{TZ}', '2019-01-01', '2026-09-23');"
            f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, source) VALUES ({b}, '{TZ}', '2019-06-01', 'era5', 'open_meteo_archive');"
            for b in (inc_b, other_b)
        )
    )

    async def scenario(url: str):
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                kept, deleted = await prune(conn, today=TODAY)
                await log_prune(conn, status="ok", kept=10, deleted=deleted, problem=None)
            async with engine.connect() as conn:
                left = (await conn.execute(text("SELECT grid_bucket FROM cell_daily_conditions"))).scalars().all()
                spans = dict((await conn.execute(text("SELECT grid_bucket, first_era5_date FROM grid_bucket_series"))).all())
            refusals = []
            async with engine.connect() as conn:
                try:
                    await prune(conn, today=TODAY)
                except PruneRefused as exc:
                    refusals.append(str(exc))
                await conn.execute(text("UPDATE accidents SET tz = NULL"))
                try:
                    await prune(conn, today=TODAY)
                except PruneRefused as exc:
                    refusals.append(str(exc))
                await conn.rollback()
            return kept, deleted, left, spans, refusals
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        kept, deleted, left, spans, refusals = asyncio.run(scenario(sa_url(name)))
    assert (kept, deleted, left) == (1, 1, [inc_b])
    assert spans == {inc_b: date(2019, 1, 1), other_b: date(2023, 1, 1)}
    assert "dropped from 10 to 1" in refusals[0] and "no incident series" in refusals[1]
```


- [ ] **Step 2: Run to verify failure** — FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** `backend/app/pipelines/pro_window.py`:

```python
"""The Open-Meteo Professional window (owner decision 2026-09-28). The archive API needs
Professional; the owner buys one 30-day Professional month per year, started on or after
10 January so the whole previous year is past ERA5's lag. The first window (the initial
backfill) may start on any day. --max-units is the window's cumulative cap, read back from
source_ingest_log, so reruns and slices can never add up past it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

PRO_WINDOW_DAYS = 30
JANUARY_EARLIEST_DAY = 10
PAID_SOURCES = ("era5_fill", "era5_normals")


@dataclass(frozen=True)
class ProWindow:
    start: date
    initial: bool = False

    def __post_init__(self) -> None:
        if not self.initial and not (self.start.month == 1 and self.start.day >= JANUARY_EARLIEST_DAY):
            raise ValueError(f"a yearly window starts in January on or after the {JANUARY_EARLIEST_DAY}th, got {self.start}")

    @property
    def end(self) -> date:
        return self.start + timedelta(days=PRO_WINDOW_DAYS - 1)

    def contains(self, today: date) -> bool:
        return self.start <= today <= self.end


def require_open(window: ProWindow, today: date) -> None:
    if not window.contains(today):
        raise SystemExit(f"today {today} is outside the Professional window {window.start}..{window.end}; "
                         "the archive API needs Professional, so nothing is fetched")


async def units_spent(conn: AsyncConnection, window: ProWindow) -> float:
    start = datetime.combine(window.start, time.min, tzinfo=timezone.utc)
    stop = datetime.combine(window.end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    value = (await conn.execute(
        text("SELECT COALESCE(sum(cost_units), 0) FROM source_ingest_log WHERE source = ANY(:sources) "
             "AND started_at >= :start AND started_at < :stop"),
        {"sources": list(PAID_SOURCES), "start": start, "stop": stop},
    )).scalar_one()
    return float(value)


@dataclass
class Budget:
    max_units: float
    spent: float

    def fits(self, units: float) -> bool:
        return self.spent + units <= self.max_units

    def spend(self, units: float) -> None:
        self.spent += units
```

`backend/app/pipelines/era5_fill.py`:

```python
"""ERA5 archive fill per (grid_bucket, tz) series.

Each series keeps one contiguous ERA5 span [first_era5_date, last_era5_date]. Gaps before
the span are filled newest-first and gaps after it oldest-first, so the span stays
contiguous; a failed chunk blocks the rest of its group rather than leaving a hole. Resume
needs no log lookup: the next run recomputes the gaps from the span, so a unit is never
bought twice for the same day. Replacing stopgap rows is just the forward gap.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.cell_conditions import day_rows, upsert_rows
from app.pipelines.grid import bucket_center, grid_bucket_sql
from app.pipelines.ingest_log import RunStatus, finish_run, last_ok_rows_in, sha256_rows, start_run, write_quarantine
from app.pipelines.open_meteo import ARCHIVE_MODEL, DAILY_VARS, ArchiveClient, Location, OpenMeteoError, cost_units
from app.pipelines.pro_window import Budget
from app.pipelines.validate import ValidationReport

SOURCE = "era5_fill"
PRUNE_SOURCE = "era5_prune"
MAX_INCIDENT_DROP = 0.10
ERA5_START = date(1940, 1, 1)
INCIDENT_START_DEFAULT = ERA5_START
ERA5_LAG_DAYS = 5
HISTORY_YEARS = 3

Direction = Literal["forward", "backward"]


@dataclass(frozen=True)
class SeriesCoverage:
    grid_bucket: int
    tz: str
    first: date | None
    last: date | None
    incident: bool


@dataclass(frozen=True)
class Gap:
    grid_bucket: int
    tz: str
    start: date
    end: date
    direction: Direction


@dataclass(frozen=True)
class Chunk:
    tz: str
    buckets: tuple[int, ...]
    start: date
    end: date
    group: str

    def units(self) -> int:
        return cost_units(len(self.buckets), len(DAILY_VARS), (self.end - self.start).days + 1)


def last_era5_day(today: date) -> date:
    return today - timedelta(days=ERA5_LAG_DAYS)


def history_start(today: date, history_years: int = HISTORY_YEARS) -> date:
    return date(today.year - history_years, 1, 1)


def series_gaps(
    coverage: Sequence[SeriesCoverage], *, today: date, incident_start: date, history_years: int = HISTORY_YEARS
) -> list[Gap]:
    end = last_era5_day(today)
    base = history_start(today, history_years)
    gaps: list[Gap] = []
    for s in coverage:
        need = min(incident_start, base) if s.incident else base
        if s.first is None or s.last is None:
            if need <= end:
                gaps.append(Gap(s.grid_bucket, s.tz, need, end, "forward"))
            continue
        if need < s.first:
            gaps.append(Gap(s.grid_bucket, s.tz, need, s.first - timedelta(days=1), "backward"))
        if s.last < end:
            gaps.append(Gap(s.grid_bucket, s.tz, s.last + timedelta(days=1), end, "forward"))
    return gaps


def _blocks(start: date, end: date, direction: Direction, years: int) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    if direction == "forward":
        s = start
        while s <= end:
            e = min(date(s.year + years - 1, 12, 31), end)
            out.append((s, e))
            s = e + timedelta(days=1)
    else:
        e = end
        while e >= start:
            s = max(date(e.year - years + 1, 1, 1), start)
            out.append((s, e))
            e = s - timedelta(days=1)
    return out


def plan_chunks(gaps: Sequence[Gap], *, batch: int, years_per_chunk: int) -> list[Chunk]:
    groups: dict[tuple[str, date, date, Direction], set[int]] = defaultdict(set)
    for g in gaps:
        groups[(g.tz, g.start, g.end, g.direction)].add(g.grid_bucket)
    chunks: list[Chunk] = []
    for key in sorted(groups):
        tz, start, end, direction = key
        ordered = sorted(groups[key])
        for i in range(0, len(ordered), batch):
            buckets = tuple(ordered[i : i + batch])
            group = f"{tz}|{start}|{end}|{direction}|{buckets[0]}"
            chunks.extend(Chunk(tz, buckets, s, e, group) for s, e in _blocks(start, end, direction, years_per_chunk))
    return chunks


def _incident_keys_sql() -> str:
    grid = grid_bucket_sql("a.latitude", "a.longitude")
    return (f"SELECT DISTINCT {grid} AS grid_bucket, t.tz FROM accidents_clean_daily a "
            "JOIN accidents t ON t.accident_id = a.accident_id "
            "WHERE a.point_trusted AND a.latitude IS NOT NULL AND a.longitude IS NOT NULL AND t.tz IS NOT NULL")


async def load_coverage(conn: AsyncConnection) -> list[SeriesCoverage]:
    rows = (await conn.execute(text(
        f"WITH inc AS ({_incident_keys_sql()}) "
        "SELECT s.grid_bucket, s.tz, s.first_era5_date, s.last_era5_date, "
        "EXISTS (SELECT 1 FROM inc WHERE inc.grid_bucket = s.grid_bucket AND inc.tz = s.tz) AS incident "
        "FROM grid_bucket_series s ORDER BY s.grid_bucket, s.tz"
    ))).all()
    return [SeriesCoverage(int(b), str(tz), f, last, bool(inc)) for b, tz, f, last, inc in rows]


COVERAGE_UPDATE = text(
    "UPDATE grid_bucket_series SET first_era5_date = LEAST(COALESCE(first_era5_date, :s), :s), "
    "last_era5_date = GREATEST(COALESCE(last_era5_date, :e), :e) WHERE tz = :tz AND grid_bucket = ANY(:buckets)"
)


async def run(
    engine_factory: Callable[[], AsyncEngine],
    client: ArchiveClient,
    chunks: Sequence[Chunk],
    *,
    today: date,
    budget: Budget,
) -> dict[str, object]:
    engine = engine_factory()
    done = failed = blocked = 0
    stopped = False
    failed_groups: set[str] = set()
    try:
        for chunk in chunks:
            if chunk.group in failed_groups:
                blocked += 1
                continue
            units = chunk.units()
            if not budget.fits(units):
                stopped = True
                break
            report = ValidationReport(SOURCE)
            sha = sha256_rows([(chunk.tz,)] + [(b,) for b in chunk.buckets])
            async with engine.begin() as conn:
                run_id = await start_run(conn, source=SOURCE, window_start=chunk.start, window_end=chunk.end, content_sha256=sha)
            try:
                responses = client.fetch([Location(b, *bucket_center(b), chunk.tz) for b in chunk.buckets], chunk.start, chunk.end)
                problem = None
            except OpenMeteoError as exc:
                responses, problem = [], str(exc)
            # A failed request is counted as spent: it may have been billed after reaching the server.
            budget.spend(units)
            rows = [
                row
                for b, response in zip(chunk.buckets, responses)
                for row in day_rows(b, chunk.tz, response, today=today, origin="archive", model=ARCHIVE_MODEL,
                                    source="open_meteo_archive", report=report)
            ]
            if problem is None and report.quarantined.get("timezone_mismatch"):
                problem = "response timezone differs from the series timezone"
            if problem is not None:
                failed += 1
                failed_groups.add(chunk.group)
                async with engine.begin() as conn:
                    await finish_run(conn, run_id, status="failed", report=report, rows_upserted=0, problems=[problem],
                                     cost_units=units)
                continue
            async with engine.begin() as conn:
                await write_quarantine(conn, run_id, report)
                n = await upsert_rows(conn, rows, run_id=run_id)
                await conn.execute(COVERAGE_UPDATE, {"s": chunk.start, "e": chunk.end, "tz": chunk.tz,
                                                     "buckets": list(chunk.buckets)})
                await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n, cost_units=units)
            done += 1
    finally:
        await engine.dispose()
    return {"chunks": len(chunks), "done": done, "failed": failed, "blocked_after_failure": blocked,
            "stopped_for_budget": stopped}


class PruneRefused(Exception):
    def __init__(self, message: str, kept: int) -> None:
        super().__init__(message)
        self.kept = kept


async def prune(conn: AsyncConnection, *, today: date, history_years: int = HISTORY_YEARS) -> tuple[int, int]:
    """Delete non-incident rows older than the 3-year window; return (incident series kept, rows deleted).

    The incident set decides what survives, so an empty set or one that shrank sharply since
    the last successful prune (a broken view, a lost tz backfill) would delete paid incident
    history: refuse instead, before any row is touched."""
    cutoff = history_start(today, history_years)
    inc = _incident_keys_sql()
    kept = int((await conn.execute(text(f"SELECT count(*) FROM ({inc}) k"))).scalar_one())
    previous = await last_ok_rows_in(conn, PRUNE_SOURCE)
    if kept == 0:
        raise PruneRefused("no incident series found; refusing to prune", kept)
    if previous and kept < (1 - MAX_INCIDENT_DROP) * previous:
        raise PruneRefused(f"incident series dropped from {previous} to {kept}; refusing to prune", kept)
    deleted = (await conn.execute(text(
        f"WITH inc AS ({inc}) DELETE FROM cell_daily_conditions c WHERE c.date < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM inc WHERE inc.grid_bucket = c.grid_bucket AND inc.tz = c.tz)"
    ), {"cutoff": cutoff})).rowcount
    await conn.execute(text(
        f"WITH inc AS ({inc}) UPDATE grid_bucket_series s SET "
        "first_era5_date = CASE WHEN s.last_era5_date >= :cutoff THEN :cutoff END, "
        "last_era5_date = CASE WHEN s.last_era5_date >= :cutoff THEN s.last_era5_date END "
        "WHERE s.first_era5_date < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM inc WHERE inc.grid_bucket = s.grid_bucket AND inc.tz = s.tz)"
    ), {"cutoff": cutoff})
    return kept, int(deleted)


async def log_prune(conn: AsyncConnection, *, status: RunStatus, kept: int, deleted: int, problem: str | None) -> None:
    report = ValidationReport(PRUNE_SOURCE)
    report.rows_in = kept
    run_id = await start_run(conn, source=PRUNE_SOURCE, window_start=None, window_end=None, content_sha256=None)
    await finish_run(conn, run_id, status=status, report=report, rows_upserted=deleted,
                     problems=[problem] if problem else None)
```

`INCIDENT_START_DEFAULT` is `[default pending: DP1]` (see the foundations plan's top section); passing `--incident-start 1990-01-01` to `era5_window` applies the alternative. Append `"app.pipelines.pro_window"`, `"app.pipelines.era5_fill"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_pro_window.py tests/test_era5_fill.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS (resume: the 1-unit cap buys 2019-12-22..31 and stops; the rerun buys only 2020-01-01..10; 20 rows. Failure: the first chunk fails, is counted as 1 unit, and the second chunk of the same group is not attempted, so the span stays empty. Prune: the non-incident 2019 row goes, the incident row stays, the non-incident span moves to 2023-01-01; with a logged previous count of 10 the next prune refuses, and with no incident series it refuses).
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/pro_window.py backend/app/pipelines/era5_fill.py backend/tests/test_pro_window.py backend/tests/test_era5_fill.py backend/pyproject.toml && git commit -m "feat(pipelines): Professional-window budget and contiguous per-series ERA5 fill"`

---

### Task 6: Monthly climate normals and day-of-year climatology (moved here from plan 5)

This task was plan 5's Task 3. It moves here because every archive call must fall inside a Professional window, and the first window is this plan's (review item C2): normals need only the grid buckets of registered series (MP route points and incidents now; OpenBeta, objective and feature points as later plans register them), not the catalog. This also answers amendment §6 Q5: climate normals become Phase 2a work; point elevation stays an MVP-1 task in plan 5. A bucket first seen between windows is `pending` in `cell_normals_status` and its normals are NULL (missing, never 0) until the next January window computes them.

**Files:**
- Create: `backend/app/pipelines/normals.py`, `backend/tests/test_normals.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: `open_meteo.ArchiveClient`, `LocationResponse`, `Location`, `cost_units` (Task 1); `pro_window.Budget` (Task 5); `ingest_log` (plan 1); `grid.bucket_center`; tables from Task 2.
- Produces: `NORMALS_VERSION = "n-v2"`, `CLIMATOLOGY_VERSION = "c-v1"`, `SOURCE = "era5_normals"`, `YEARS = 10`, `MIN_YEARS = 8`, `MIN_DAY_SHARE = 0.9`, `ERA5_LAG_DAYS = 5`, `DOY_HALF_WINDOW = 7`, `window(today: date) -> tuple[int, int]`, `@dataclass(frozen=True) MonthNormal(month: int, tmax_mean, tmin_mean, precip_mm, snowfall_cm, freeze_thaw_days: float | None, n_years: int)`, `monthly_normals(response: LocationResponse) -> list[MonthNormal]` (12 entries), `doy_climatology(response: LocationResponse) -> list[dict[str, float | int | None]]` (366 rows, keys = `cell_climatology` columns without bucket/period/version), `bucket_status(normals: Sequence[MonthNormal]) -> str`, `async normals_due(conn, *, today: date) -> list[tuple[int, str]]`, `async run(engine_factory, client, due: Sequence[tuple[int, str]], *, today: date, batch: int, budget: Budget) -> dict[str, object]`. No CLI of its own: `era5_window run` calls it.
- Rules (review item S2): a variable's monthly value for one year needs ≥ 90% of that month's days observed for that variable; a few missing days are scaled to the full month (`sum × days_in_month / observed_days`), too many make that year's value missing, never 0. A month's normal needs ≥ 8 usable years **per variable**, else that variable is NULL. Completion is tracked per bucket in `cell_normals_status`, so a batch interrupted or failed is re-bought only for the buckets it did not finish, and adding buckets never invalidates finished ones.

- [ ] **Step 1: Failing tests** — `backend/tests/test_normals.py`:

```python
from datetime import date, timedelta

import pytest

from app.pipelines.normals import bucket_status, doy_climatology, monthly_normals, window
from app.pipelines.open_meteo import DAILY_VARS, LocationResponse


def test_window_is_trailing_complete_years():
    assert window(date(2026, 9, 28)) == (2016, 2025)


def test_window_waits_for_era5_lag():
    assert window(date(2027, 1, 3)) == (2016, 2025)
    assert window(date(2027, 1, 6)) == (2017, 2026)


def _response(start: date, end: date, tmax=5.0, tmin=-3.0, temp_gaps=(), precip_gaps=()):
    days = [start + timedelta(d) for d in range((end - start).days + 1)]
    daily: dict[str, object] = {"time": [d.isoformat() for d in days]}
    for var in DAILY_VARS:
        daily[var] = [1.0] * len(days)
    daily["temperature_2m_max"] = [None if d in temp_gaps else tmax for d in days]
    daily["temperature_2m_min"] = [None if d in temp_gaps else tmin for d in days]
    daily["precipitation_sum"] = [None if d in precip_gaps else 1.0 for d in days]
    return LocationResponse.model_validate({"latitude": 40.0, "longitude": -105.3, "elevation": 2400.0,
                                            "timezone": "America/Denver", "daily": daily})


def test_monthly_values():
    normals = monthly_normals(_response(date(2016, 1, 1), date(2025, 12, 31)))
    jan = normals[0]
    assert (jan.month, jan.n_years, jan.tmax_mean, jan.tmin_mean) == (1, 10, 5.0, -3.0)
    assert jan.precip_mm == pytest.approx(31.0)
    assert jan.freeze_thaw_days == pytest.approx(31.0)
    assert bucket_status(normals) == "complete"


def test_missing_precip_days_are_never_summed_as_zero():
    gaps = {date(y, 1, d) for y in range(2016, 2026) for d in range(1, 6)}
    jan = monthly_normals(_response(date(2016, 1, 1), date(2025, 12, 31), precip_gaps=gaps))[0]
    assert jan.precip_mm is None
    assert jan.tmax_mean == 5.0


def test_a_few_missing_days_are_scaled_not_zeroed():
    gaps = {date(y, 1, d) for y in range(2016, 2026) for d in (1, 2)}
    jan = monthly_normals(_response(date(2016, 1, 1), date(2025, 12, 31), temp_gaps=gaps, precip_gaps=gaps))[0]
    assert jan.precip_mm == pytest.approx(31.0)
    assert jan.freeze_thaw_days == pytest.approx(31.0)


def test_months_with_too_few_good_years_are_null_not_zero():
    gaps = {date(y, 2, d) for y in range(2016, 2019) for d in range(1, 10)}
    normals = monthly_normals(_response(date(2016, 1, 1), date(2025, 12, 31), temp_gaps=gaps))
    feb = normals[1]
    assert feb.n_years == 7
    assert (feb.tmax_mean, feb.tmin_mean, feb.freeze_thaw_days) == (None, None, None)
    assert feb.precip_mm == pytest.approx(28.3)
    assert bucket_status(normals) == "insufficient"


def test_doy_climatology_pools_a_two_week_window_across_years():
    rows = doy_climatology(_response(date(2016, 1, 1), date(2025, 12, 31)))
    assert len(rows) == 366
    jan10 = rows[9]
    assert (jan10["doy"], jan10["n_years"], jan10["tmax_mean"], jan10["tmax_p10"], jan10["tmax_p90"]) == (10, 10, 5.0, 5.0, 5.0)
    assert jan10["freeze_thaw_freq"] == 1.0 and jan10["lightning_day_freq"] is None


def test_doy_climatology_ignores_missing_precip_instead_of_counting_zero():
    gaps = {date(y, 1, d) for y in range(2016, 2026) for d in range(1, 20)}
    rows = doy_climatology(_response(date(2016, 1, 1), date(2025, 12, 31), precip_gaps=gaps))
    assert rows[9]["precip_mean"] is None
    assert rows[40]["precip_mean"] == 1.0
```

February over 2016–2025 averages 28.3 days (three leap years: 2016, 2020, 2024), and precipitation is complete in every year, so its normal is present while the temperature-based values are NULL.

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/normals.py`:

```python
"""Monthly climate normals and day-of-year climatology per 0.1° bucket (amendment §3.1, D6/D7)
from the trailing 10 complete ERA5 years, rebuilt in every Professional window. The period
moves with the run date; a year is complete once ERA5's ~5-day lag past 31 December has
passed. Missing days are never summed as 0: a year-month with <90% of a variable's days is
dropped for that variable, and a variable with <8 usable years is NULL."""

from __future__ import annotations

import calendar
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.grid import bucket_center
from app.pipelines.ingest_log import finish_run, sha256_rows, start_run
from app.pipelines.open_meteo import DAILY_VARS, ArchiveClient, Location, LocationResponse, OpenMeteoError, cost_units
from app.pipelines.pro_window import Budget
from app.pipelines.validate import ValidationReport

NORMALS_VERSION = "n-v2"
CLIMATOLOGY_VERSION = "c-v1"
SOURCE = "era5_normals"
YEARS = 10
MIN_YEARS = 8
MIN_DAY_SHARE = 0.9
ERA5_LAG_DAYS = 5
DOY_HALF_WINDOW = 7


@dataclass(frozen=True)
class MonthNormal:
    month: int
    tmax_mean: float | None
    tmin_mean: float | None
    precip_mm: float | None
    snowfall_cm: float | None
    freeze_thaw_days: float | None
    n_years: int


def window(today: date) -> tuple[int, int]:
    last_complete = today.year - 1 if today - timedelta(days=ERA5_LAG_DAYS) >= date(today.year, 1, 1) else today.year - 2
    return last_complete - YEARS + 1, last_complete


def _scaled_total(values: list[float | None], days_in_month: int) -> float | None:
    present = [v for v in values if v is not None]
    if len(present) < MIN_DAY_SHARE * days_in_month:
        return None
    return sum(present) * days_in_month / len(present)


def _normal(per_year: list[float]) -> float | None:
    return statistics.fmean(per_year) if len(per_year) >= MIN_YEARS else None


def monthly_normals(response: LocationResponse) -> list[MonthNormal]:
    d = response.daily
    per: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, day in enumerate(d.time):
        per[(day.year, day.month)].append(i)
    out: list[MonthNormal] = []
    for month in range(1, 13):
        tmax_y: list[float] = []
        tmin_y: list[float] = []
        ft_y: list[float] = []
        precip_y: list[float] = []
        snow_y: list[float] = []
        for (year, m), idx in sorted(per.items()):
            if m != month:
                continue
            days_in_month = calendar.monthrange(year, month)[1]
            if len(idx) != days_in_month:
                continue
            pairs = [(a, b) for i in idx if (a := d.temperature_2m_max[i]) is not None and (b := d.temperature_2m_min[i]) is not None]
            if len(pairs) >= MIN_DAY_SHARE * days_in_month:
                tmax_y.append(statistics.fmean(a for a, _ in pairs))
                tmin_y.append(statistics.fmean(b for _, b in pairs))
                ft_y.append(sum(1 for a, b in pairs if a > 0 and b < 0) * days_in_month / len(pairs))
            if (p := _scaled_total([d.precipitation_sum[i] for i in idx], days_in_month)) is not None:
                precip_y.append(p)
            if (s := _scaled_total([d.snowfall_sum[i] for i in idx], days_in_month)) is not None:
                snow_y.append(s)
        out.append(MonthNormal(month, _normal(tmax_y), _normal(tmin_y), _normal(precip_y), _normal(snow_y),
                               _normal(ft_y), len(tmax_y)))
    return out


def bucket_status(normals: Sequence[MonthNormal]) -> str:
    complete = all(
        None not in (n.tmax_mean, n.tmin_mean, n.precip_mm, n.snowfall_cm, n.freeze_thaw_days) for n in normals
    )
    return "complete" if complete and len(normals) == 12 else "insufficient"


_CLIM_VARS = {
    "tmax": "temperature_2m_max", "tmin": "temperature_2m_min", "precip": "precipitation_sum",
    "snowfall": "snowfall_sum", "gust": "wind_gusts_10m_max",
}


def _stat(sample: NDArray[np.float64], kind: str) -> float | None:
    if sample.size == 0:
        return None
    if kind == "mean":
        return float(sample.mean())
    return float(np.percentile(sample, 10 if kind == "p10" else 90))


def doy_climatology(response: LocationResponse) -> list[dict[str, float | int | None]]:
    """Per day of year, pool that day ±7 days across every year (≈150 samples for 10 years)
    so percentiles are stable; missing days are NaN and excluded, never 0."""
    d = response.daily
    doys = np.array([t.timetuple().tm_yday for t in d.time])
    years = np.array([t.year for t in d.time])
    series = {k: np.array([np.nan if v is None else v for v in getattr(d, var)], dtype=np.float64) for k, var in _CLIM_VARS.items()}
    ft = np.where(np.isnan(series["tmax"]) | np.isnan(series["tmin"]), np.nan,
                  ((series["tmax"] > 0) & (series["tmin"] < 0)).astype(np.float64))
    rows: list[dict[str, float | int | None]] = []
    for doy in range(1, 367):
        gap = np.abs(doys - doy)
        mask = np.minimum(gap, 366 - gap) <= DOY_HALF_WINDOW
        row: dict[str, float | int | None] = {"doy": doy, "lightning_day_freq": None}
        for key, values, kinds in (
            ("tmax", series["tmax"], ("mean", "p10", "p90")),
            ("tmin", series["tmin"], ("mean", "p10", "p90")),
            ("precip", series["precip"], ("mean", "p90")),
            ("snowfall", series["snowfall"], ("mean",)),
            ("gust", series["gust"], ("p90",)),
        ):
            sample = values[mask & ~np.isnan(values)]
            for kind in kinds:
                row[f"{key}_{kind}"] = _stat(sample, kind)
        row["freeze_thaw_freq"] = _stat(ft[mask & ~np.isnan(ft)], "mean")
        row["n_years"] = len(set(years[mask & ~np.isnan(series["tmax"])].tolist()))
        rows.append(row)
    return rows


CLIM_UPSERT = text(
    "INSERT INTO cell_climatology (grid_bucket, doy, tmax_mean, tmax_p10, tmax_p90, tmin_mean, tmin_p10, tmin_p90, "
    "precip_mean, precip_p90, snowfall_mean, gust_p90, freeze_thaw_freq, n_years, period_start_year, period_end_year, "
    "climatology_version, run_id) VALUES (:b, :doy, :tmax_mean, :tmax_p10, :tmax_p90, :tmin_mean, :tmin_p10, :tmin_p90, "
    ":precip_mean, :precip_p90, :snowfall_mean, :gust_p90, :freeze_thaw_freq, :n_years, :ps, :pe, :cv, :run) "
    "ON CONFLICT (grid_bucket, doy) DO UPDATE SET tmax_mean = EXCLUDED.tmax_mean, tmax_p10 = EXCLUDED.tmax_p10, "
    "tmax_p90 = EXCLUDED.tmax_p90, tmin_mean = EXCLUDED.tmin_mean, tmin_p10 = EXCLUDED.tmin_p10, tmin_p90 = EXCLUDED.tmin_p90, "
    "precip_mean = EXCLUDED.precip_mean, precip_p90 = EXCLUDED.precip_p90, snowfall_mean = EXCLUDED.snowfall_mean, "
    "gust_p90 = EXCLUDED.gust_p90, freeze_thaw_freq = EXCLUDED.freeze_thaw_freq, n_years = EXCLUDED.n_years, "
    "period_start_year = EXCLUDED.period_start_year, period_end_year = EXCLUDED.period_end_year, "
    "climatology_version = EXCLUDED.climatology_version, run_id = EXCLUDED.run_id"
)
# lightning_day_freq is deliberately absent from the upsert: plan 7 owns that column.

UPSERT = text(
    "INSERT INTO cell_climate_normals (grid_bucket, month, tmax_mean, tmin_mean, precip_mm, snowfall_cm, "
    "freeze_thaw_days, n_years, period_start_year, period_end_year, ref_elevation_m, source, normals_version, run_id) "
    "VALUES (:b, :month, :tmax_mean, :tmin_mean, :precip_mm, :snowfall_cm, :freeze_thaw_days, :n_years, :ps, :pe, "
    ":elev, 'open_meteo_era5_seamless', :v, :run) ON CONFLICT (grid_bucket, month) DO UPDATE SET "
    "tmax_mean = EXCLUDED.tmax_mean, tmin_mean = EXCLUDED.tmin_mean, precip_mm = EXCLUDED.precip_mm, "
    "snowfall_cm = EXCLUDED.snowfall_cm, freeze_thaw_days = EXCLUDED.freeze_thaw_days, n_years = EXCLUDED.n_years, "
    "period_start_year = EXCLUDED.period_start_year, period_end_year = EXCLUDED.period_end_year, "
    "ref_elevation_m = EXCLUDED.ref_elevation_m, normals_version = EXCLUDED.normals_version, run_id = EXCLUDED.run_id"
)

STATUS_UPDATE = text(
    "UPDATE cell_normals_status SET status = :status, computed_at = now(), n_years = :n, run_id = :run "
    "WHERE grid_bucket = :b"
)


async def normals_due(conn: AsyncConnection, *, today: date) -> list[tuple[int, str]]:
    """Buckets that are pending, or whose normals predate this period or version. A bucket
    straddling a timezone boundary uses its alphabetically first zone (month-scale values are
    insensitive to a one-hour day boundary)."""
    _, end_year = window(today)
    rows = (await conn.execute(text(
        "SELECT st.grid_bucket, min(s.tz) FROM cell_normals_status st "
        "JOIN grid_bucket_series s ON s.grid_bucket = st.grid_bucket "
        "WHERE st.status = 'pending' OR NOT EXISTS (SELECT 1 FROM cell_climate_normals n "
        "WHERE n.grid_bucket = st.grid_bucket AND n.period_end_year = :pe AND n.normals_version = :v) "
        "GROUP BY st.grid_bucket ORDER BY st.grid_bucket"
    ), {"pe": end_year, "v": NORMALS_VERSION})).all()
    return [(int(b), str(tz)) for b, tz in rows]


async def run(
    engine_factory: Callable[[], AsyncEngine],
    client: ArchiveClient,
    due: Sequence[tuple[int, str]],
    *,
    today: date,
    batch: int,
    budget: Budget,
) -> dict[str, object]:
    start_year, end_year = window(today)
    start, end = date(start_year, 1, 1), date(end_year, 12, 31)
    days = (end - start).days + 1
    by_tz: dict[str, list[int]] = defaultdict(list)
    for b, tz in due:
        by_tz[tz].append(b)
    batches = [(tz, sorted(bs)[i : i + batch]) for tz, bs in sorted(by_tz.items()) for i in range(0, len(bs), batch)]
    engine = engine_factory()
    done = failed = 0
    stopped = False
    try:
        for tz, group in batches:
            units = cost_units(len(group), len(DAILY_VARS), days)
            if not budget.fits(units):
                stopped = True
                break
            report = ValidationReport(SOURCE)
            sha = sha256_rows([(tz,)] + [(b,) for b in group] + [("v", NORMALS_VERSION)])
            async with engine.begin() as conn:
                run_id = await start_run(conn, source=SOURCE, window_start=start, window_end=end, content_sha256=sha)
            try:
                responses = client.fetch([Location(b, *bucket_center(b), tz) for b in group], start, end)
            except OpenMeteoError as exc:
                budget.spend(units)
                failed += 1
                async with engine.begin() as conn:
                    await finish_run(conn, run_id, status="failed", report=report, rows_upserted=0,
                                     problems=[str(exc)], cost_units=units)
                continue
            budget.spend(units)
            rows: list[dict[str, object]] = []
            clim_rows: list[dict[str, object]] = []
            statuses: list[dict[str, object]] = []
            for b, response in zip(group, responses):
                normals = monthly_normals(response)
                clim_rows += [c | {"b": b, "ps": start_year, "pe": end_year, "cv": CLIMATOLOGY_VERSION}
                              for c in doy_climatology(response)]
                for n in normals:
                    if n.n_years >= MIN_YEARS:
                        report.accept()
                    else:
                        report.quarantine(f"{b}:{n.month}", "too_few_years", n=n.n_years)
                    rows.append({"b": b, "month": n.month, "tmax_mean": n.tmax_mean, "tmin_mean": n.tmin_mean,
                                 "precip_mm": n.precip_mm, "snowfall_cm": n.snowfall_cm,
                                 "freeze_thaw_days": n.freeze_thaw_days, "n_years": n.n_years, "ps": start_year,
                                 "pe": end_year, "elev": response.elevation, "v": NORMALS_VERSION})
                statuses.append({"b": b, "status": bucket_status(normals), "n": min(n.n_years for n in normals)})
            async with engine.begin() as conn:
                await conn.execute(UPSERT, [r | {"run": run_id} for r in rows])
                await conn.execute(CLIM_UPSERT, [c | {"run": run_id} for c in clim_rows])
                await conn.execute(STATUS_UPDATE, [s | {"run": run_id} for s in statuses])
                await finish_run(conn, run_id, status="ok", report=report, rows_upserted=len(rows), cost_units=units)
            done += 1
    finally:
        await engine.dispose()
    return {"period": [start_year, end_year], "batches": len(batches), "done": done, "failed": failed,
            "stopped_for_budget": stopped}
```

`ref_elevation_m` is the elevation Open-Meteo reports for the bucket centre (its 90 m DEM, to which it lapse-rate-downscales the series), so Phase 3 can see the route-vs-bucket gap (D6, review claim 18). Append `"app.pipelines.normals"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_normals.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/normals.py backend/tests/test_normals.py backend/pyproject.toml && git commit -m "feat(pipelines): monthly normals and day-of-year climatology, missing days never zero, per-bucket completion"`

---

### Task 7: `era5_window` — the only ERA5 entry point

**Files:**
- Create: `backend/app/pipelines/era5_window.py`, `backend/tests/test_era5_window.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: Tasks 4–6.
- Produces: `async plan(conn, *, today: date, incident_start: date, batch: int, years_per_chunk: int) -> tuple[list[Chunk], list[tuple[int, str]]]`, `async run_window(engine_factory, client, *, window: ProWindow, today: date, max_units: int, batch: int, years_per_chunk: int, incident_start: date) -> dict[str, object]`, CLI:
  - `python -m app.pipelines.era5_window register` (same as `localday register`),
  - `python -m app.pipelines.era5_window plan [--incident-start 1940-01-01] [--window-start YYYY-MM-DD [--initial-window]]` (read-only: chunks, units, buckets due, units already spent in the window),
  - `python -m app.pipelines.era5_window run --window-start YYYY-MM-DD [--initial-window] --max-units N [--batch 50] [--years-per-chunk 5] [--incident-start 1940-01-01]` (register → fill gaps → normals due → 3-year prune). Exit status 1 when any chunk or batch failed or the prune refused.

- [ ] **Step 1: Failing tests** — `backend/tests/test_era5_window.py`:

```python
import asyncio
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.era5_window import run_window
from app.pipelines.open_meteo import DAILY_VARS, ArchiveClient
from app.pipelines.pro_window import ProWindow
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2027, 1, 15)
WINDOW = ProWindow(date(2027, 1, 10))
SEED = (
    "INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (900000001, 'Fixture Area', 40.05, -105.25);"
    "INSERT INTO mp_routes (mp_route_id, name, location_id) VALUES (900000002, 'Fixture Route', 900000001);"
    "INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, "
    "date_precision, geocode_precision) VALUES (1, 'AAC', '2025-06-01', 40.05, -105.25, true, 'climbing', 'US', 'day', 'crag');"
)


def _handler(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
    days = [(start + timedelta(d)).isoformat() for d in range((end - start).days + 1)]
    body = []
    for lat, lon in zip(params["latitude"].split(","), params["longitude"].split(",")):
        daily: dict[str, object] = {"time": days}
        for var in DAILY_VARS:
            daily[var] = [1.0] * len(days)
        daily["temperature_2m_max"] = [5.0] * len(days)
        daily["temperature_2m_min"] = [-3.0] * len(days)
        body.append({"latitude": float(lat), "longitude": float(lon), "elevation": 1650.0,
                     "timezone": params["timezone"], "daily": daily})
    return httpx.Response(200, json=body)


def test_run_refuses_outside_the_window():
    client = ArchiveClient(None, transport=httpx.MockTransport(_handler))
    with pytest.raises(SystemExit, match="outside"):
        asyncio.run(run_window(lambda: None, client, window=WINDOW, today=date(2027, 2, 9), max_units=10,
                               batch=50, years_per_chunk=5, incident_start=date(1940, 1, 1)))


@requires_pg
def test_window_end_to_end_and_second_run_buys_nothing():
    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(_handler))
        kwargs = dict(window=WINDOW, today=TODAY, max_units=10_000, batch=50, years_per_chunk=5,
                      incident_start=date(2025, 1, 1))
        first = await run_window(lambda: create_async_engine(url), client, **kwargs)
        second = await run_window(lambda: create_async_engine(url), client, **kwargs)
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                tz = (await conn.execute(text("SELECT tz FROM accidents WHERE accident_id = 1"))).scalar_one()
                status = (await conn.execute(text("SELECT status FROM cell_normals_status"))).scalars().all()
                window_rows = (await conn.execute(text(
                    "SELECT count(*), min(record_kind), max(record_kind) FROM accident_conditions WHERE accident_id = 1"))).one()
                span = (await conn.execute(text("SELECT first_era5_date, last_era5_date FROM grid_bucket_series"))).one()
        finally:
            await engine.dispose()
        return first, second, tz, status, tuple(window_rows), tuple(span)

    with migrated_db(seed_sql=SEED) as name:
        first, second, tz, status, window_rows, span = asyncio.run(scenario(sa_url(name)))
    assert first["register"]["series_total"] == 1
    assert (first["fill"]["done"], first["fill"]["failed"], first["normals"]["done"]) == (1, 0, 1)
    assert tz == "America/Denver" and status == ["complete"]
    assert window_rows == (7, "era5", "era5")
    assert span == (date(2024, 1, 1), date(2027, 1, 10))
    assert (second["fill"]["chunks"], second["normals"]["batches"]) == (0, 0)
    assert second["units_spent_window"] == first["units_spent_window"]
```

The first test never reaches the database (the window check comes first), so its engine factory is a stub.

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/era5_window.py`:

```python
"""The yearly Open-Meteo Professional window: the only code path that calls the ERA5 archive.

Standard excludes the archive API, so every ERA5 call happens inside a declared 30-day
Professional window (January, on or after the 10th; the first window may start any day).
One run: register new points, fill every series' ERA5 gaps (the prior year's days replace
stopgap rows; new series get 3 years; incident series reach back to --incident-start),
rebuild normals for buckets that are pending or out of period, then prune non-incident rows
older than 3 years. --max-units caps the window's cumulative spend across runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from datetime import date

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines import era5_fill, normals
from app.pipelines.era5_fill import Chunk, load_coverage, plan_chunks, series_gaps
from app.pipelines.ingest_log import finish_run, start_run, write_quarantine
from app.pipelines.localday import register_all
from app.pipelines.open_meteo import DAILY_VARS, ArchiveClient, cost_units
from app.pipelines.pro_window import Budget, ProWindow, require_open, units_spent
from app.pipelines.validate import ValidationReport


async def plan(
    conn: AsyncConnection, *, today: date, incident_start: date, batch: int, years_per_chunk: int
) -> tuple[list[Chunk], list[tuple[int, str]]]:
    gaps = series_gaps(await load_coverage(conn), today=today, incident_start=incident_start)
    return plan_chunks(gaps, batch=batch, years_per_chunk=years_per_chunk), await normals.normals_due(conn, today=today)


def _normals_units(n_buckets: int, today: date) -> int:
    start_year, end_year = normals.window(today)
    return cost_units(n_buckets, len(DAILY_VARS), (date(end_year, 12, 31) - date(start_year, 1, 1)).days + 1)


async def _register(engine: AsyncEngine) -> dict[str, int]:
    report = ValidationReport("series_register")
    async with engine.begin() as conn:
        counts = await register_all(conn, report=report)
        run_id = await start_run(conn, source="series_register", window_start=None, window_end=None, content_sha256=None)
        await write_quarantine(conn, run_id, report)
        await finish_run(conn, run_id, status="ok", report=report, rows_upserted=counts["series_total"])
    return counts


async def run_window(
    engine_factory: Callable[[], AsyncEngine],
    client: ArchiveClient,
    *,
    window: ProWindow,
    today: date,
    max_units: int,
    batch: int,
    years_per_chunk: int,
    incident_start: date,
) -> dict[str, object]:
    require_open(window, today)
    if incident_start < era5_fill.ERA5_START:
        raise SystemExit(f"--incident-start {incident_start} is before ERA5's first day {era5_fill.ERA5_START}")
    engine = engine_factory()
    try:
        registered = await _register(engine)
        async with engine.connect() as conn:
            spent_before = await units_spent(conn, window)
            chunks, due = await plan(conn, today=today, incident_start=incident_start, batch=batch,
                                     years_per_chunk=years_per_chunk)
    finally:
        await engine.dispose()
    budget = Budget(max_units=max_units, spent=spent_before)
    fill = await era5_fill.run(engine_factory, client, chunks, today=today, budget=budget)
    built = await normals.run(engine_factory, client, due, today=today, batch=batch, budget=budget)
    pruned: dict[str, object] = {"skipped": "fill incomplete"}
    if not fill["failed"] and not fill["stopped_for_budget"]:
        engine = engine_factory()
        try:
            try:
                async with engine.begin() as conn:
                    kept, deleted = await era5_fill.prune(conn, today=today)
                    await era5_fill.log_prune(conn, status="ok", kept=kept, deleted=deleted, problem=None)
                pruned = {"incident_series_kept": kept, "deleted_rows": deleted}
            except era5_fill.PruneRefused as exc:
                async with engine.begin() as conn:
                    await era5_fill.log_prune(conn, status="rejected", kept=exc.kept, deleted=0, problem=str(exc))
                pruned = {"refused": str(exc)}
        finally:
            await engine.dispose()
    return {"window": [window.start.isoformat(), window.end.isoformat()], "register": registered, "fill": fill,
            "normals": built, "prune": pruned, "units_spent_before": spent_before,
            "units_spent_window": budget.spent, "max_units": max_units}


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.config import settings
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    today = utc_today()
    incident_start = date.fromisoformat(args.incident_start)
    window = ProWindow(date.fromisoformat(args.window_start), initial=args.initial_window) if args.window_start else None
    if args.command == "register":
        engine = ingest_engine()
        try:
            return dict(await _register(engine))
        finally:
            await engine.dispose()
    if args.command == "plan":
        engine = ingest_engine()
        try:
            async with engine.connect() as conn:
                chunks, due = await plan(conn, today=today, incident_start=incident_start, batch=args.batch,
                                         years_per_chunk=args.years_per_chunk)
                spent = await units_spent(conn, window) if window else None
        finally:
            await engine.dispose()
        return {"mode": "plan", "chunks": len(chunks), "fill_units": sum(c.units() for c in chunks),
                "normals_buckets": len(due), "normals_units": _normals_units(len(due), today),
                "window_units_spent": spent}
    if window is None:
        raise SystemExit("run needs --window-start")
    return await run_window(ingest_engine, ArchiveClient(settings.OPEN_METEO_API_KEY), window=window, today=today,
                            max_units=args.max_units, batch=args.batch, years_per_chunk=args.years_per_chunk,
                            incident_start=incident_start)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["register", "plan", "run"])
    parser.add_argument("--window-start")
    parser.add_argument("--initial-window", action="store_true")
    parser.add_argument("--max-units", type=int, default=0)
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--years-per-chunk", type=int, default=5)
    parser.add_argument("--incident-start", default=era5_fill.INCIDENT_START_DEFAULT.isoformat())
    result = asyncio.run(_main(parser.parse_args()))
    print(json.dumps(result, sort_keys=True, default=str))
    fill, built, pruned = result.get("fill"), result.get("normals"), result.get("prune")
    if (isinstance(fill, dict) and fill.get("failed")) or (isinstance(built, dict) and built.get("failed")) or (
        isinstance(pruned, dict) and "refused" in pruned
    ):
        sys.exit(1)
```

`--incident-start` defaults to 1940-01-01 `[default pending: DP1]`. Append `"app.pipelines.era5_window"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_era5_window.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS (one series: route and incident share bucket and zone; the incident makes it reach back to `min(2025-01-01, 2024-01-01)`; one 5-year chunk to 2027-01-10; one normals batch for 2017–2026; the second run finds no gaps and nothing due).
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/era5_window.py backend/tests/test_era5_window.py backend/pyproject.toml && git commit -m "feat(pipelines): era5_window, the Professional-window ERA5 entry point"`

---

### Task 8: R6 verification against the legacy `weather` table, acceptance cells

**Files:**
- Create: `backend/app/pipelines/r6_verify.py`, `backend/tests/verify/test_phase2a_conditions.py`
- Modify: `backend/pyproject.toml`, `backend/db/roles/grants_phase2.sql`

**Interfaces:**
- Consumes: `tests.verify._db.fetch` (plan 1; analyst, verify-full).
- Produces: `R6_SQL: str` (returns `n`, `r_tmax`, `p99_gust`), `async verify(conn) -> dict[str, float | int | None]`, CLI `python -m app.pipelines.r6_verify` (logs `source='r6_verify'` with the numbers in `validation_report`); `-m db` cells: 7-day window coverage from 1940-01-07 on (earlier incidents reported as `conditions_missing`), accidents without a timezone reported, r > 0.95, gust p99 < 60, no visibility column, normals rows consistent with `cell_normals_status`.

- [ ] **Step 1: Write the verification cells** — `backend/tests/verify/test_phase2a_conditions.py`:

```python
import pytest

from app.pipelines.r6_verify import R6_SQL
from tests.verify._db import fetch

pytestmark = pytest.mark.db

# ERA5 in the Open-Meteo archive starts 1940-01-01, so the first full 7-day window ends 1940-01-07.
FIRST_WINDOW_END = "DATE '1940-01-07'"
ELIGIBLE = (
    "FROM accidents_clean_daily a JOIN accidents t ON t.accident_id = a.accident_id "
    "WHERE a.point_trusted AND a.latitude IS NOT NULL AND a.date <= (now() AT TIME ZONE 'UTC')::date - 5"
)


def test_every_clean_day_incident_has_a_seven_day_window():
    [row] = fetch(
        f"SELECT count(*) FILTER (WHERE a.date >= {FIRST_WINDOW_END} AND t.tz IS NOT NULL AND "
        "(SELECT count(*) FROM accident_conditions c WHERE c.accident_id = a.accident_id AND c.record_kind = 'era5') < 7) AS missing, "
        f"count(*) FILTER (WHERE a.date < {FIRST_WINDOW_END}) AS conditions_missing_pre_era5, "
        f"count(*) FILTER (WHERE t.tz IS NULL) AS no_timezone {ELIGIBLE}"
    )
    print(dict(row))
    assert row["missing"] == 0


def test_r6_new_temperatures_agree_with_correctly_linked_legacy_rows():
    [row] = fetch(R6_SQL)
    print(dict(row))
    assert row["n"] > 100
    assert row["r_tmax"] > 0.95
    assert row["p99_gust"] < 60


def test_no_visibility_in_new_conditions():
    rows = fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'cell_daily_conditions' AND column_name LIKE '%visib%'")
    assert rows == []


def test_normals_status_matches_the_normals_rows():
    [row] = fetch(
        "SELECT count(*) FILTER (WHERE st.status <> 'pending' AND (SELECT count(*) FROM cell_climate_normals n "
        "WHERE n.grid_bucket = st.grid_bucket) <> 12) AS broken, "
        "count(*) FILTER (WHERE st.status = 'complete' AND EXISTS (SELECT 1 FROM cell_climate_normals n "
        "WHERE n.grid_bucket = st.grid_bucket AND (n.tmax_mean IS NULL OR n.precip_mm IS NULL))) AS complete_with_nulls, "
        "count(*) FILTER (WHERE st.status = 'pending') AS pending "
        "FROM cell_normals_status st"
    )
    print(dict(row))
    assert (row["broken"], row["complete_with_nulls"]) == (0, 0)
```

- [ ] **Step 2: Run to verify it fails to import** — `cd backend && uv run pytest -m db tests/verify/test_phase2a_conditions.py -q` → FAIL (`ModuleNotFoundError: app.pipelines.r6_verify`).

- [ ] **Step 3: Implement** `backend/app/pipelines/r6_verify.py`:

```python
"""R6 acceptance: ERA5 cell temperatures vs the legacy weather rows that were linked
correctly (dated inside their accident's 7-day window and within ~5 km of it)."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.grid import grid_bucket_sql
from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

GRID = grid_bucket_sql("a.latitude", "a.longitude")
R6_SQL = f"""
WITH linked AS (
  SELECT w.temperature_max AS legacy_tmax, c.tmax AS new_tmax
  FROM weather w
  JOIN accidents a ON a.accident_id = w.accident_id
  JOIN cell_daily_conditions c ON c.grid_bucket = {GRID} AND c.tz = a.tz AND c.date = w.date
  WHERE w.date BETWEEN a.date - 6 AND a.date
    AND abs(w.latitude - a.latitude) < 0.05 AND abs(w.longitude - a.longitude) < 0.05
    AND w.temperature_max IS NOT NULL AND c.tmax IS NOT NULL AND c.record_kind = 'era5'
)
SELECT (SELECT count(*) FROM linked) AS n,
       (SELECT corr(legacy_tmax, new_tmax) FROM linked) AS r_tmax,
       (SELECT percentile_cont(0.99) WITHIN GROUP (ORDER BY gust_max_ms) FROM cell_daily_conditions
         WHERE gust_max_ms IS NOT NULL AND record_kind = 'era5') AS p99_gust
"""


async def verify(conn: AsyncConnection) -> dict[str, float | int | None]:
    row = (await conn.execute(text(R6_SQL))).one()
    return {
        "n": int(row.n),
        "r_tmax": float(row.r_tmax) if row.r_tmax is not None else None,
        "p99_gust": float(row.p99_gust) if row.p99_gust is not None else None,
    }


async def _main() -> dict[str, float | int | None]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            result = await verify(conn)
            run_id = await start_run(conn, source="r6_verify", window_start=None, window_end=None, content_sha256=None)
            await finish_run(conn, run_id, status="ok", report=ValidationReport("r6_verify"), rows_upserted=0,
                             problems=[json.dumps(result)])
    finally:
        await engine.dispose()
    return result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_main()), sort_keys=True))
```

The legacy `weather.date` has no recorded day definition; R6 compares it to the local-day series, which is the day accident reports use. `grants_phase2.sql`: add `GRANT SELECT ON public.weather TO ingest;` under Plan 3 (the legacy table is read, never written). Append `"app.pipelines.r6_verify"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run pytest -m db --co -q tests/verify/test_phase2a_conditions.py | tail -1 && uv run mypy` → default suite green; 4 cells collected.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/r6_verify.py backend/tests/verify/test_phase2a_conditions.py backend/db/roles/grants_phase2.sql backend/pyproject.toml && git commit -m "feat(pipelines): R6 verification and 2a-3 acceptance cells"`

---

### Task 9: R7 `area_weekly_weather` sign fix, PR 2a-3 docs

**Files:**
- Create: `backend/app/data/repair/area_weekly.py`, `backend/tests/test_area_weekly.py`
- Modify: `backend/app/data/repair/__main__.py`, `backend/tests/verify/test_phase2a_conditions.py`, `backend/db/roles/grants_phase2.sql`, `backend/pyproject.toml`, `CHANGELOG.md`, `CLAUDE.md`, `data/DATABASE_STRUCTURE.md`, `DEPLOYMENT.md`

**Interfaces:**
- Produces: `R7_VERSION = "r7-v1"`, `async run_r7(conn, args) -> dict[str, object]` registered as CLI step `r7`. Refuses unless exactly one row has `longitude > 0` and it equals `113.2`. `-m db` cell `test_r7_no_positive_longitudes` (spec R7 acceptance).

- [ ] **Step 1: Failing test** — `backend/tests/test_area_weekly.py`:

```python
import argparse
import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair.area_weekly import run_r7
from tests.pgtest import migrated_db, requires_pg, sa_url

pytestmark = requires_pg
ROW = "INSERT INTO area_weekly_weather (latitude, longitude, week_start, week_end) VALUES ({lat}, {lon}, '2020-01-06', '2020-01-12');"


def _run(name: str, apply: bool) -> dict[str, object]:
    async def go() -> dict[str, object]:
        engine = create_async_engine(sa_url(name))
        try:
            async with engine.begin() as conn:
                return await run_r7(conn, argparse.Namespace(apply=apply))
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_the_single_sign_flipped_point_is_negated_once():
    with migrated_db(seed_sql=ROW.format(lat=40.1, lon=113.2) + ROW.format(lat=40.1, lon=-105.3)) as name:
        assert _run(name, apply=True)["updated"] == 1
        assert _run(name, apply=True)["updated"] == 0

        async def lons() -> list[float]:
            engine = create_async_engine(sa_url(name))
            try:
                async with engine.connect() as conn:
                    return sorted(float(v) for (v,) in (await conn.execute(text("SELECT longitude FROM area_weekly_weather"))).all())
            finally:
                await engine.dispose()

        assert asyncio.run(lons()) == [-113.2, -105.3]


def test_unexpected_positive_longitudes_refuse():
    with migrated_db(seed_sql=ROW.format(lat=40.1, lon=113.2) + ROW.format(lat=40.1, lon=100.0)) as name:
        with pytest.raises(SystemExit, match="expected exactly one"):
            _run(name, apply=True)
```

Add the acceptance cell to `backend/tests/verify/test_phase2a_conditions.py`:

```python
def test_r7_no_positive_longitudes():
    [row] = fetch("SELECT count(*) AS n FROM area_weekly_weather WHERE longitude > 0")
    assert row["n"] == 0
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/data/repair/area_weekly.py`:

```python
"""R7: the audit found exactly one area_weekly_weather point at longitude +113.2 (a
dropped minus sign). Anything else positive is unexplained and stops the fix. The table is
dropped at 2b-3 (plan 7)."""

from __future__ import annotations

import argparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

R7_VERSION = "r7-v1"
EXPECTED = 113.2


async def run_r7(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    positive = sorted({float(v) for (v,) in (await conn.execute(text(
        "SELECT DISTINCT longitude FROM area_weekly_weather WHERE longitude > 0"))).all()})
    if not positive:
        return {"updated": 0, "note": "no positive longitudes"}
    if positive != [EXPECTED]:
        raise SystemExit(f"expected exactly one positive longitude ({EXPECTED}), found {positive}")
    if not args.apply:
        return {"mode": "dry_run", "would_update_longitude": EXPECTED}
    updated = (await conn.execute(text(
        "UPDATE area_weekly_weather SET longitude = -longitude WHERE longitude = :v"), {"v": EXPECTED})).rowcount
    report = ValidationReport("repair:r7")
    report.accept()
    run_id = await start_run(conn, source="repair:r7", window_start=None, window_end=None, content_sha256=R7_VERSION)
    await finish_run(conn, run_id, status="ok", report=report, rows_upserted=updated,
                     problems=[f"longitude {EXPECTED} -> {-EXPECTED}"])
    return {"updated": updated}
```

Register `"r7": area_weekly.run_r7` in `app/data/repair/__main__.py`. `grants_phase2.sql`: `GRANT SELECT, UPDATE (longitude) ON public.area_weekly_weather TO ingest;` (plus `SELECT` for the WHERE). Append `"app.data.repair.area_weekly"` to strict mypy.

Docs:
- `CHANGELOG.md` entry "Phase 2a conditions (PR 2a-3)": Open-Meteo archive client (m/s, local-day timezone per request, key never logged); `cell_daily_conditions` keyed by `(grid_bucket, tz, date)` with `record_kind` (`era5`/`stopgap`/`forecast`), `grid_bucket_series`, `accidents.tz`, normals tables and `accident_conditions` (0007); validated writer with era5 > stopgap > forecast; `localday`, `pro_window`, `era5_fill`, `normals` (moved from plan 5), `era5_window`; R6 verification; R7; `pipelines` dependency group (`timezonefinder`, `tzdata`).
- `CLAUDE.md` Commands: `uv sync --group pipelines`; `uv run python -m app.pipelines.era5_window register|plan|run --window-start YYYY-MM-DD [--initial-window] --max-units N` ("the only ERA5 entry point; refuses outside a Professional window"); `uv run python -m app.pipelines.r6_verify`. Data rules: "Weather days are the crag's local calendar day: conditions are keyed by `(grid_bucket, tz, date)`; join accidents through `accidents.tz`. `record_kind = 'stopgap'` rows are Forecast-API past days, not ERA5."
- `DEPLOYMENT.md` "Open-Meteo": "The archive API needs Professional (Standard excludes it; pricing read 2026-09-28, re-check before buying). One Professional month per year, started on or after 10 January: run `era5_window run --window-start <day> --max-units <cap>` on prod until it reports no chunks and no normals due, then downgrade to Standard (which the nightly forecast and its `past_days` stopgap need). Between windows no ERA5 call is made." And "Database…": "`weather` (legacy, read-only) stays until the Phase 3 MVP-1 PR, which **must** drop it (hard gate: MVP-1 does not merge while `weather` exists). Until then the live kernel reads legacy weather that is mislinked for some accidents; the relaunch caveat lists this."
- `data/DATABASE_STRUCTURE.md`: `cell_daily_conditions` (columns, PK, units, local-day rule, `record_kind` precedence), `grid_bucket_series`, `cell_normals_status`, `cell_climate_normals`, `cell_climatology`, `accidents.tz`, `accident_conditions`; mark `weather` legacy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd .. && python scripts/check_no_scrapers.py` → green.
- [ ] **Step 5: Commit** — `git add backend/app/data/repair/area_weekly.py backend/app/data/repair/__main__.py backend/tests/test_area_weekly.py backend/tests/verify/test_phase2a_conditions.py backend/db/roles/grants_phase2.sql backend/pyproject.toml CHANGELOG.md CLAUDE.md data/DATABASE_STRUCTURE.md DEPLOYMENT.md && git commit -m "feat(repair): R7 guarded area_weekly_weather sign fix; PR 2a-3 docs"`

---

# PR 2a-4 — `feat/p2a-accident-refresh`

### Task 10: R11 facts-only accident loader

**Files:**
- Create: `backend/app/pipelines/accident_facts.py`, `backend/tests/test_accident_facts.py`, `data/manual/README.md`, `data/manual/accident_facts.template.csv`
- Modify: `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`, `backend/pyproject.toml`

**Interfaces:**
- Consumes: `activity.classify_activity`, `severity.severity_scale`, `sources.source_family` (plan 2); `validate.*`, `ingest_log.*` (plan 1); `localday.tz_for_point` (Task 4).
- Produces: `COLUMNS: tuple[str, ...]` (the CSV header), `US_STATES: frozenset[str]`, `REFRESH_SINCE = date(2024, 8, 1)`, `class FactRow(BaseModel)` (strict field types), `parse_facts(path: Path, *, today: date, report: ValidationReport) -> list[FactRow]`, `month_gaps(dates: Iterable[date], *, since: date, today: date) -> list[str]`, `async insert_new(conn, rows: list[FactRow], *, report: ValidationReport) -> int`, CLI `python -m app.pipelines.accident_facts --file PATH [--dry-run]` (the run log's `problems` carries the post-2024 month gaps per source family: spec R11 "log any post-2024 gap").

The CSV contract (`data/manual/accident_facts.template.csv`, header only):

```
source,source_id,date,date_precision,state,mountain,route,latitude,longitude,geocode_precision,activity,accident_type,injury_severity,exp_years_climbing,exp_stated_level,exp_first_season,guided,source_url,summary
```

- [ ] **Step 1: Failing tests** — `backend/tests/test_accident_facts.py`:

```python
import asyncio
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.accident_facts import COLUMNS, insert_new, month_gaps, parse_facts
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)
GOOD = "AAC,ANAC-2025-001,2025-02-10,day,CO,Fixture Peak,Fixture Route,40.25,-105.6,crag,climbing,fall,serious,,unknown,,unknown,https://publications.americanalpineclub.org/fixture,Leader fell on the crux pitch."


def _csv(tmp_path, *lines):
    path = tmp_path / "facts.csv"
    path.write_text(",".join(COLUMNS) + "\n" + "\n".join(lines) + "\n")
    return path


def test_good_row_parses(tmp_path):
    report = ValidationReport("t")
    [row] = parse_facts(_csv(tmp_path, GOOD), today=TODAY, report=report)
    assert (row.source_id, row.date, row.latitude, row.exp_years_climbing) == ("ANAC-2025-001", date(2025, 2, 10), 40.25, None)


def test_future_and_foreign_rows_are_quarantined(tmp_path):
    future = GOOD.replace("2025-02-10", "2026-09-29").replace("ANAC-2025-001", "A2")
    foreign = GOOD.replace("40.25,-105.6", "50.0,-115.5").replace("ANAC-2025-001", "A3")
    long_summary = GOOD.replace("Leader fell on the crux pitch.", "x" * 201).replace("ANAC-2025-001", "A4")
    bad_url = GOOD.replace("https://", "http://").replace("ANAC-2025-001", "A5")
    report = ValidationReport("t")
    assert parse_facts(_csv(tmp_path, future, foreign, long_summary, bad_url), today=TODAY, report=report) == []
    assert report.quarantined == {"future": 1, "outside_us": 1, "invalid": 2}


def test_blank_coordinates_with_a_us_state_are_allowed_as_unknown_geocode(tmp_path):
    row = GOOD.replace("40.25,-105.6,crag", ",,unknown")
    report = ValidationReport("t")
    [parsed] = parse_facts(_csv(tmp_path, row), today=TODAY, report=report)
    assert parsed.latitude is None and parsed.geocode_precision == "unknown"


def test_blank_coordinates_with_a_foreign_state_are_quarantined(tmp_path):
    row = GOOD.replace("40.25,-105.6,crag", ",,unknown").replace(",CO,", ",BC,")
    report = ValidationReport("t")
    assert parse_facts(_csv(tmp_path, row), today=TODAY, report=report) == []
    assert report.quarantined == {"unknown_country": 1}


def test_month_gaps_list_only_closed_empty_months():
    dates = [date(2024, 8, 3), date(2024, 10, 1), date(2024, 12, 2)]
    assert month_gaps(dates, since=date(2024, 8, 1), today=date(2024, 12, 15)) == ["2024-09", "2024-11"]


@requires_pg
def test_insert_is_new_rows_only(tmp_path):
    async def scenario(url: str) -> tuple[int, int, dict[str, int], tuple[object, ...]]:
        engine = create_async_engine(url)
        try:
            report = ValidationReport("t")
            rows = parse_facts(_csv(tmp_path, GOOD), today=TODAY, report=report)
            async with engine.begin() as conn:
                first = await insert_new(conn, rows, report=report)
            report2 = ValidationReport("t")
            async with engine.begin() as conn:
                second = await insert_new(conn, rows, report=report2)
                stored = (await conn.execute(text(
                    "SELECT activity_class, severity_scale, country, is_canonical, date_precision, tz FROM accidents"))).one()
            return first, second, dict(report2.quarantined), tuple(stored)
        finally:
            await engine.dispose()

    with migrated_db() as name:
        first, second, quarantined, stored = asyncio.run(scenario(sa_url(name)))
    assert (first, second, quarantined) == (1, 0, {"already_present": 1})
    assert stored == ("climbing", "full", "US", True, "day", "America/Denver")
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/accident_facts.py`:

```python
"""R11: facts-only accident refresh from CSV (manual AAC entries; CAIC/NPS exports produced
by private tools outside this repo). New rows only; nothing existing is overwritten.
country is taken from the data (coordinates, else a US state), never assumed."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair.activity import R4_VERSION, classify_activity
from app.data.repair.severity import severity_scale
from app.data.repair.sources import source_family
from app.pipelines.ingest_log import finish_run, sha256_rows, start_run, write_quarantine
from app.pipelines.localday import tz_for_point
from app.pipelines.validate import ValidationReport, coord_problem, date_problem

SOURCE = "accident_facts"
COLUMNS: tuple[str, ...] = (
    "source", "source_id", "date", "date_precision", "state", "mountain", "route", "latitude", "longitude",
    "geocode_precision", "activity", "accident_type", "injury_severity", "exp_years_climbing",
    "exp_stated_level", "exp_first_season", "guided", "source_url", "summary",
)
EARLIEST = date(1970, 1, 1)
REFRESH_SINCE = date(2024, 8, 1)
US_STATES: frozenset[str] = frozenset(
    {
        "al", "ak", "az", "ar", "ca", "co", "ct", "de", "dc", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky",
        "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh",
        "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
        "district of columbia", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
        "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
        "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
        "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
        "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington",
        "west virginia", "wisconsin", "wyoming",
    }
)


class FactRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1)
    source_id: str = Field(min_length=1, max_length=100)
    date: date
    date_precision: Literal["day", "month", "year"]
    state: str = Field(min_length=2, max_length=100)
    mountain: str | None = None
    route: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    geocode_precision: Literal["exact", "crag", "area", "park_centroid", "region_fallback", "unknown"]
    activity: str = Field(min_length=1)
    accident_type: str | None = None
    injury_severity: str | None = None
    exp_years_climbing: int | None = Field(default=None, ge=0, le=80)
    exp_stated_level: Literal["novice", "intermediate", "experienced", "expert", "unknown"] = "unknown"
    exp_first_season: bool | None = None
    guided: Literal["guided", "unguided", "unknown"] = "unknown"
    source_url: str = Field(pattern=r"^https://")
    summary: str = Field(min_length=1, max_length=200)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if value == "" else value


def parse_facts(path: Path, *, today: date, report: ValidationReport) -> list[FactRow]:
    rows: list[FactRow] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"{path.name}: header must be exactly {','.join(COLUMNS)}")
        for number, raw in enumerate(reader, start=2):
            ref = f"{path.name}:{number}"
            try:
                row = FactRow.model_validate(raw)
            except ValidationError as exc:
                report.quarantine(ref, "invalid", fields=sorted({str(e["loc"][0]) for e in exc.errors()}))
                continue
            if (problem := date_problem(row.date, today=today, earliest=EARLIEST)) is not None:
                report.quarantine(ref, problem)
                continue
            if row.latitude is not None or row.longitude is not None:
                if (problem := coord_problem(row.latitude, row.longitude)) is not None:
                    report.quarantine(ref, problem)
                    continue
            elif row.geocode_precision != "unknown":
                report.quarantine(ref, "precision_without_coords")
                continue
            elif row.state.strip().lower() not in US_STATES:
                report.quarantine(ref, "unknown_country")
                continue
            try:
                source_family(row.source)
            except ValueError:
                report.quarantine(ref, "unmapped_source")
                continue
            report.accept()
            rows.append(row)
    return rows


def month_gaps(dates: Iterable[date], *, since: date, today: date) -> list[str]:
    """Closed months since `since` with no incident: the spec's post-2024 gap log. The current
    month is not closed yet, so it is never reported as a gap."""
    seen = {(d.year, d.month) for d in dates}
    gaps: list[str] = []
    year, month = since.year, since.month
    while (year, month) < (today.year, today.month):
        if (year, month) not in seen:
            gaps.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return gaps


INSERT = text(
    "INSERT INTO accidents (source, source_id, date, year, date_precision, state, mountain, route, latitude, "
    "longitude, geocode_precision, geocode_method, country, tz, activity, activity_class, activity_rule_version, "
    "inclusion_flag, accident_type, injury_severity, severity_scale, exp_years_climbing, exp_stated_level, "
    "exp_first_season, guided, exp_rule_version, source_url, description, is_canonical, updated_at) VALUES "
    "(:source, :source_id, :date, :year, :date_precision, :state, :mountain, :route, :latitude, :longitude, "
    ":geocode_precision, 'facts_csv', 'US', :tz, :activity, :activity_class, :activity_rule_version, :inclusion_flag, "
    ":accident_type, :injury_severity, :severity_scale, :exp_years_climbing, :exp_stated_level, :exp_first_season, "
    ":guided, 'facts_csv', :source_url, :summary, true, now())"
)


async def insert_new(conn: AsyncConnection, rows: list[FactRow], *, report: ValidationReport) -> int:
    existing = {
        (str(s), str(i))
        for s, i in (await conn.execute(text("SELECT source, source_id FROM accidents WHERE source_id IS NOT NULL"))).all()
    }
    inserted = 0
    for row in rows:
        if (row.source, row.source_id) in existing:
            report.quarantine(f"{row.source}:{row.source_id}", "already_present")
            continue
        family = source_family(row.source)
        # No coordinates means no terrain check, so a ski row can never qualify as an approach.
        classified = classify_activity(family, row.activity, row.summary, near_climb_terrain=False)
        if classified is None:
            report.quarantine(f"{row.source}:{row.source_id}", "unmapped_activity")
            continue
        activity_class, flag = classified
        zone = tz_for_point(row.latitude, row.longitude) if row.latitude is not None and row.longitude is not None else None
        await conn.execute(INSERT, row.model_dump() | {
            "year": row.date.year,
            "tz": zone,
            "activity_class": activity_class,
            "activity_rule_version": R4_VERSION,
            "inclusion_flag": flag,
            "severity_scale": severity_scale(family, row.injury_severity),
        })
        inserted += 1
    return inserted


async def _gap_log(conn: AsyncConnection, families: set[str], *, today: date) -> list[str]:
    dated = (await conn.execute(text("SELECT source, date FROM accidents WHERE date >= :since"),
                                {"since": REFRESH_SINCE})).all()
    lines: list[str] = []
    for family in sorted(families):
        gaps = month_gaps([d for s, d in dated if source_family(str(s)) == family], since=REFRESH_SINCE, today=today)
        lines.append(f"post-2024 gap {family}: {','.join(gaps) if gaps else 'none'}")
    return lines


async def main(path: Path, *, dry_run: bool) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    today = utc_today()
    report = ValidationReport(SOURCE)
    rows = parse_facts(path, today=today, report=report)
    if dry_run:
        return {"mode": "dry_run", "report": report.summary()}
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None,
                                     content_sha256=sha256_rows([(r.source, r.source_id) for r in rows]))
            n = await insert_new(conn, rows, report=report)
            gaps = await _gap_log(conn, {source_family(r.source) for r in rows}, today=today)
            await write_quarantine(conn, run_id, report)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n, problems=gaps)
    finally:
        await engine.dispose()
    return {"inserted": n, "gaps": gaps, "report": report.summary()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main(args.file, dry_run=args.dry_run)), sort_keys=True, default=str))
```

`country` is written as `'US'` only for rows that passed either the US coordinate check or the US-state check in `parse_facts`; every other row was quarantined before insert (review item P6). `tz` is resolved from the row's coordinates at insert; rows without coordinates keep `tz = NULL` and never get a conditions window (reported by the Task 8 cell as `no_timezone`). Rows keep the ski-approach rule honest: CSV rows are classified with `near_climb_terrain=False`, so a ski row is `non_climbing` unless a later full `r4` run (which has coordinates and terrain) upgrades it.

`data/manual/README.md`:

```markdown
# Manual facts CSVs (R11)

`accident_facts.template.csv` is the only file committed here: the header of the facts-only CSV the
`app.pipelines.accident_facts` loader accepts. Filled files (AAC entries typed from ANAC/The
Prescription, CAIC and NPS exports from the private tools) live in
`~/Developer/safeascent-private/manual/` until legal question Q6 is answered (Decision D12).

Rules: facts only (date, place, activity, type, severity, experience facts, URL). `summary` is our
own one-line wording, at most 200 characters; never paste report text. `source_url` must be https.
Dates after the load day and coordinates outside the US are quarantined; a row without coordinates
needs a US state (two-letter code or full name) in `state`, or it is quarantined `unknown_country`.
```

Grants (`grants_phase2.sql`, "Plan 3 (PR 2a-4)"):

```sql
GRANT INSERT ON public.accidents TO ingest;
GRANT USAGE ON SEQUENCE public.accidents_accident_id_seq TO ingest;
```

`verify_roles_phase2.sql` `ingest_writes`: `('public.accidents','INSERT')`. In `test_roles_phase2.py` add `_as(ingest, "INSERT INTO accidents (source) VALUES ('AAC')")` after the other ingest writes, and `_denied(ingest, "DELETE FROM accidents WHERE false")`.

Append `"app.pipelines.accident_facts"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_accident_facts.py tests/test_roles_phase2.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/accident_facts.py backend/tests/test_accident_facts.py backend/tests/test_roles_phase2.py data/manual/ backend/db/roles/ backend/pyproject.toml && git commit -m "feat(pipelines): R11 facts-only accident loader (new rows only, country from data, gap log)"`

---

### Task 11: R11 verification cells and PR 2a-4 docs

**Files:**
- Modify: `backend/tests/verify/test_phase2a_conditions.py`, `CHANGELOG.md`, `CLAUDE.md`

- [ ] **Step 1: Add the cells**

```python
def test_r11_refresh_window_has_incidents():
    [row] = fetch(
        "SELECT count(*) AS n FROM accidents_clean WHERE date BETWEEN DATE '2026-02-05' AND DATE '2026-07-02'"
    )
    print({"feb5_jul2_2026_clean_incidents": row["n"]})
    assert row["n"] > 0
    [latest] = fetch("SELECT max(date) AS d FROM accidents WHERE date <= (now() AT TIME ZONE 'UTC')::date")
    assert latest["d"] is not None and latest["d"].year >= 2025


def test_r11_backtest_inputs_exist():
    [row] = fetch(
        "SELECT count(*) AS incidents, count(*) FILTER (WHERE EXISTS (SELECT 1 FROM historical_predictions h "
        "WHERE h.route_id = a.mp_route_id AND h.prediction_date = a.date)) AS with_prediction "
        "FROM accidents_clean_daily a WHERE a.date BETWEEN DATE '2026-02-05' AND DATE '2026-07-02' "
        "AND a.mp_route_id IS NOT NULL"
    )
    print({"backtest_incidents": row["incidents"], "backtest_with_prediction": row["with_prediction"]})
    assert row["incidents"] > 0 and row["with_prediction"] > 0
```

The Feb 5–Jul 2 2026 window is the spec's backtest window, a historical span, not a "future" cutoff. The spec's 2a-4 acceptance says the `historical_predictions` backtest runs; the backtest code itself is Phase 3's `ml/backtest.py`, so Phase 2 proves its inputs exist: refreshed incidents in the window that have a stored prediction for their route and day. The live writer purges `historical_predictions` after one year and plan 8 folds rows older than 7 days into its archive, so the runbook records these numbers now. Review item A2 (R1/R2 re-dating R11 rows) is fixed in plan 2, which restricts R1/R2 to ids present in `internal.accidents_raw`; Task 13 Step 3 checks it with a dry run.

- [ ] **Step 2: Docs** — `CHANGELOG.md` "Phase 2a accident refresh (PR 2a-4)": facts CSV loader, template, private-dir rule, `country` from the data (`unknown_country` quarantine), `tz` at insert, post-2024 gap log, `ingest` gains INSERT on `accidents`. `CLAUDE.md` Commands: `uv run python -m app.pipelines.accident_facts --file PATH [--dry-run]`.
- [ ] **Step 3: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/` → green.
- [ ] **Step 4: Commit** — `git add backend/tests/verify/test_phase2a_conditions.py CHANGELOG.md CLAUDE.md && git commit -m "docs: R11 refresh and backtest-input verification, commands"`

---

### Task 12: OWNER/AGENT RUNBOOK — PR 2a-3: migrate, register, plan, R7 (no purchase yet)

- [ ] **Step 1 (owner/agent): Shell helpers** — plan 2's committed `backend/scripts/runbook_helpers.sh` (Task 4 there), never a local redefinition:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
# split_pg_url and verify_full_url defined first (foundations Task 8 Step 1)
. scripts/runbook_helpers.sh
uv sync --group pipelines
# Paid calls only: load the Open-Meteo key in a subshell so it never stays in the interactive shell.
OM() { ( set -a; . ./.env.openmeteo; set +a; INGMOD "$@" ); }
```

`TARGET_HOST` set = that Neon branch; `unset TARGET_HOST` = prod (the env file's host). `INGMOD <module> [args]` runs a job as `ingest`; `ING <step>` runs a repair step; `VERIFY <pytest args>` runs `-m db` cells as `analyst`. `ingest_engine()` refuses a remote URL without certificate verification (plan 1, D14) and `tests.verify._db.fetch` connects with `connect_args_for`, so TLS is verify-full everywhere. `uv sync --group pipelines` installs `timezonefinder`/`tzdata` once; if a job then fails with `ModuleNotFoundError: timezonefinder`, `uv run` re-synced without the group, so rerun `uv sync --group pipelines`.

- [ ] **Step 2 (owner/agent): Rehearsal branch `p2a-3-rehearsal`**: migrate as `migrator` (→ `0007_cell_daily_conditions (head)`, `alembic check` clean), run `grants_phase2.sql`, both verify scripts (foundations Task 9 Step 2 pattern).

- [ ] **Step 3 (owner/agent): Register series and plan the first window on the branch** (free: no Open-Meteo call)

```bash
export TARGET_HOST=<branch host from the Neon Console>
INGMOD app.pipelines.era5_window register
INGMOD app.pipelines.era5_window plan
```

Expected: `register` reports `accident_tz_assigned` close to the number of clean accidents with coordinates, `incident_series` ≈ 700, `route_series` ≈ 4–5K, and a quarantine summary (counts only; `no_timezone` should be a handful of coastal points). `plan` reports `fill_units` ≈ 1.9–2.1M (incidents from 1940 `[default pending: DP1]` ≈ 1.6M plus 3-year history ≈ 0.36M) and `normals_units` ≈ 1.2M. If `fill_units + normals_units` is above 4.5M (Professional's 5M/month less margin), stop and review with the agent.

- [ ] **Step 4 (owner/agent): R7 on the branch** — `ING r7` (dry run shows `would_update_longitude: 113.2`), `ING r7 --apply` → `updated: 1`, rerun → `note: no positive longitudes`; `VERIFY tests/verify/test_phase2a_conditions.py -q -k r7` → pass.

- [ ] **Step 5 (owner/agent): Prod** — migrate + grants + verify scripts on prod; then `unset TARGET_HOST`, `INGMOD app.pipelines.era5_window register`, `ING r7` / `ING r7 --apply`, `VERIFY tests/verify/test_phase2a_conditions.py -q -k "r7 or visibility"` → pass. No ERA5 call happens before Task 14.

---

### Task 13: OWNER/AGENT RUNBOOK — PR 2a-4: load the refresh

- [ ] **Step 1 (owner): Prepare the CSVs** in `~/Developer/safeascent-private/manual/`: `aac_refresh.csv` (typed by hand from ANAC 2025/2026 and The Prescription, facts only, ~60–120 rows), `caic_refresh.csv` and `nps_refresh.csv` (from the private exporters, same header). Copy the header from `data/manual/accident_facts.template.csv`.

- [ ] **Step 2 (owner/agent): Dry run each file, then load on a branch** (Task 12 Step 1 helpers)

```bash
export TARGET_HOST=<branch host>
for f in aac_refresh caic_refresh nps_refresh; do
  INGMOD app.pipelines.accident_facts --file ~/Developer/safeascent-private/manual/$f.csv --dry-run
done
```

Expected: a report per file with counts only. Fix every quarantined row in the private CSV (the report names row numbers and reasons, never content) until `quarantined` is empty or each remaining entry is a deliberate exclusion. Then run without `--dry-run`; each load prints `gaps`, the post-2024 months with no incident per source family. Record them in the PR (spec R11 "log any post-2024 gap"); a gap in NPS or CAIC means the private export is incomplete, so re-pull before continuing.

- [ ] **Step 3 (owner/agent): Re-dedupe and register** — run `ING r5 --apply --out ../data/review/duplicates.csv`, decide any new review pairs, `ING r5-import …`, `ING r5 --apply`. `ING r2r1` (dry run) must propose nothing for the new rows (R1/R2 only touch ids in `internal.accidents_raw`); its `proposed` counts match those recorded in PR 2a-1. Then `INGMOD app.pipelines.era5_window register` so the new incidents get `tz` and their series (plan 7's nightly stopgap covers them from registration; Task 14 buys their ERA5 windows).

- [ ] **Step 4 (owner/agent): Verify and report** — `VERIFY tests/verify/test_phase2a_conditions.py -q -s -k r11` → pass; record the printed `feb5_jul2_2026_clean_incidents`, `backtest_incidents` and `backtest_with_prediction` in the PR (spec 2a-4 acceptance; record now, because `historical_predictions` rows older than a year are purged and plan 8 folds rows older than 7 days). Repeat Steps 2–4 after `unset TARGET_HOST` (prod). Phase 3 MVP-0 is unblocked once Task 14 has filled the windows.

---

### Task 14: OWNER/AGENT RUNBOOK — the first Professional window (then every January)

- [ ] **Step 1 (owner): Buy one month of Open-Meteo Professional** `[default pending: DP5]` — when Tasks 12 and 13 are done (not waiting for January 2027). Re-check the price, the 5M-call quota and the archive-needs-Professional rule on open-meteo.com/en/pricing first (read 2026-09-28). Put the key in a gitignored file with an editor: `backend/.env.openmeteo` containing `OPEN_METEO_API_KEY=<key>`. `git check-ignore -v .env.openmeteo` must match `.env.*`. Note the purchase date: it is `--window-start`.

- [ ] **Step 2 (owner/agent): A small slice on the branch, then check the dashboard**

```bash
export TARGET_HOST=<branch host>
OM app.pipelines.era5_window run --window-start <purchase date> --initial-window --max-units 20000
```

Expected: `fill.done` > 0, `fill.failed: 0`, `stopped_for_budget: true` (the cap is small on purpose). Compare the Open-Meteo dashboard's used calls with `units_spent_window`; the ceiling formula over-estimates, so the dashboard should show **less**. If it shows more than `units_spent_window`, stop and tell the agent (the cost estimate must be corrected before the full run). Then `INGMOD app.pipelines.r6_verify` and `VERIFY tests/verify/test_phase2a_conditions.py -q -s -k "r6 or visibility"` on the partial data: `r_tmax > 0.95`, `p99_gust < 60`. The window-coverage cell fails until the full fill; that is expected on the rehearsal.

- [ ] **Step 3 (owner/agent): Prod, in slices** — `unset TARGET_HOST` (prod). The branch's spend is logged in the branch database, so subtract it: run `OM app.pipelines.era5_window run --window-start <purchase date> --initial-window --max-units <cap>` with `<cap>` = 1000000, then 2500000, then 4800000 minus the branch's `units_spent_window`, checking the dashboard between slices. Each run resumes from the ERA5 spans; the cap is cumulative for the window, so a rerun never re-spends. Repeat the last command until the JSON shows `fill.chunks: 0` and `normals.batches: 0` (exit status 0). A non-zero exit means a chunk or batch failed: rerun (failed groups are retried from where their span ends).

- [ ] **Step 4 (owner/agent): Verify on prod** — `INGMOD app.pipelines.r6_verify`; `VERIFY tests/verify/ -q -s` → every cell passes; record the printed `conditions_missing_pre_era5`, `no_timezone` and `pending` counts in the PR. Delete the rehearsal branch.

- [ ] **Step 5 (owner): End of the window** — downgrade to Standard (the nightly forecast and its `past_days` stopgap, plan 7, need it). No ERA5 call is possible or made until the next window.

- [ ] **Step 6 (owner, every year): The January window** — on or after 10 January, buy one Professional month (re-check prices first) and run `unset TARGET_HOST; INGMOD app.pipelines.era5_window plan --window-start <day>` then `OM app.pipelines.era5_window run --window-start <day> --max-units 4800000` until `fill.chunks: 0` and `normals.batches: 0`. This appends the previous year for every series (replacing its stopgap rows), gives series first seen since the last window their 3-year history (and incident series their full span), rebuilds normals for the new 10-year period and for every `pending` bucket, and prunes non-incident rows older than three years. Then `VERIFY tests/verify/ -q` and downgrade to Standard.

---

## Self-review

- Spec coverage: R6 (Tasks 1–8, drop deferred per D9 as a hard MVP-1 gate), R7 (Task 9, with the `lon > 0` acceptance cell), R11 (Tasks 10–11, 13, including the post-2024 gap log and the backtest-input cell), 2a-3 acceptance "every clean day incident has a 7-day window" (Task 8 cell, from 1940-01-07 per DP1, earlier incidents reported as `conditions_missing`), 2a-4 acceptance (Task 11 cells). Amendment §3.1 normals and §6 Q5 (Task 6). Owner decisions 2026-09-28: one Professional window per year in January with stopgap rows between (Tasks 5, 7, 14; spec P2-5 corrected in the cost-model section), local weather day (Tasks 1–4, 8). Pipelines "recorded responses, rejection, idempotency" (Tasks 1, 3, 5, 7).
- Placeholders: none beyond Console hosts, the purchase date and the owner's key file.
- Types and names: `Location(grid_bucket, lat, lon, tz)`, `DayRow(... record_kind ...)`, `day_rows(grid_bucket, tz, response, *, today, origin, model, source, report)`, `upsert_rows`, `ArchiveClient.fetch`, `Chunk.units`, `Budget`, `ProWindow`, `register_series`, `tz_for_point`, `local_date` are used with the same signatures in Tasks 3–10 and in plans 5, 6 and 7. `grid_bucket_series`, `cell_normals_status`, `record_kind` and `accidents.tz` match the frozen contract in the foundations plan's interface ledger.
- Removed from the previous revision: `incident_weather_backfill.py` (its chunk planner and budgeted runner became `era5_fill.py`, driven only by `era5_window`), the weekly ERA5 append, and the "downgrade to Standard, then weekly append" step.
