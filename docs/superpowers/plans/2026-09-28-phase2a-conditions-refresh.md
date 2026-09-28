# Phase 2a Conditions and Accident Refresh (PRs 2a-3, 2a-4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mislinked per-accident `weather` rows with daily conditions per 0.1° cell (`cell_daily_conditions`, m/s, no visibility) backfilled from the ERA5 archive for every incident cell, verify them against the legacy table (R6), fix the `area_weekly_weather` sign error (R7), and load the 2024-08-to-now accident refresh from facts-only CSVs (R11) so Phase 3 MVP-0 is unblocked.

**Architecture:** A typed Open-Meteo archive client (`app/pipelines/open_meteo.py`, pydantic-validated responses, recorded-response tests, API key never logged) feeds a validating writer (`cell_conditions.py`) that upserts with "archive beats forecast" precedence. A chunked, resumable, budget-capped backfill job selects incident cells from `accidents_clean_daily`. A view `accident_conditions` joins accidents to conditions on (cell, date), never on `accident_id`. The R11 loader reads facts-only CSVs (manual AAC, private CAIC/NPS exports) and inserts new rows through the same validation as every other boundary.

**Tech Stack:** Python 3.12, httpx 0.28 (existing), pydantic 2, SQLAlchemy async, Alembic, pytest with `httpx.MockTransport`.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` R6, R7, R11, milestones 2a-3/2a-4, Dynamic features (Open-Meteo archive, P2-5); P3:151 (`cell_daily_conditions` columns), P3:161 (grid). Owner decisions D4, D6, D8, D9, D12, D14 in `2026-09-28-phase2a-foundations.md`.

**Prerequisites:** Plans 1 and 2 merged and applied (this plan reads `accidents_clean_daily` and uses `grid.py`, `ingest_log.py`, `framework.py`).

**Two PRs:** PR 2a-3 = Tasks 1–6 (`feat/p2a-conditions`) + runbook Task 9; PR 2a-4 = Tasks 7–8 (`feat/p2a-accident-refresh`) + runbook Task 10.

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- Wind in m/s everywhere (`wind_speed_unit=ms` on every request); no visibility column.
- Missing values are NULL, never 0; a value outside its range quarantines the whole day-row.
- An archive row always replaces a forecast row for the same (cell, date); a forecast never replaces an archive row.
- The ERA5 lag is handled at run time: archive requests end at `today − 5 days`; nothing is fixed to a calendar date.
- The Open-Meteo API key lives in `settings.OPEN_METEO_API_KEY`; it is never logged, printed, or included in an exception message (httpx errors carry the URL, so the client never re-raises them raw).
- Cost guard: every backfill run takes `--max-units` and stops before exceeding it; run logs record `cost_units`.
- `weather` is **not** dropped in this plan `[assumes D9]`; its drop is a Phase 3 MVP-1 prerequisite recorded in Task 8's docs.
- R11 rows are facts only: date, place, activity, type, severity, experience facts, URL, and our own one-line summary. No AAC narrative text, no scraped HTML. CAIC/NPS files come from `~/Developer/safeascent-private/`; the filled AAC CSV stays there too until legal Q6 `[assumes D12]`.

## Decisions this plan makes where the spec is silent (owner may overrule)

1. Weather values are stored as `real` (float4) to keep `cell_daily_conditions` inside the spec's ~1.3 GB estimate; float4 keeps ~7 significant digits, far beyond the data's precision.
2. Requests are made at the bucket **center** (`grid.bucket_center`), so every consumer of a bucket sees the same series; Open-Meteo's reported `elevation` for that point is stored by plan 5 (D6).
3. Open-Meteo cost units are estimated as `locations × ⌈vars/10⌉ × ⌈days/14⌉` from the rule in the spec's Verified section; the owner confirms against the dashboard after the first chunk (runbook).
4. R11 inserts new accidents only; a row whose `(source, source_id)` already exists is quarantined `already_present`, never overwritten. The own one-line summary goes into `description` (capped at 200 characters), since the table has no separate summary column and new rows carry no narrative.

## Review Focus

1. **Open-Meteo returns a single object instead of a list** (one location in the request) — expect it parsed like a one-element list (Task 1 `test_single_location_response_is_a_list_of_one`).
2. **A daily value is `null`** — expect NULL in the row, never 0, and the row kept (Task 3 `test_null_values_stay_null`).
3. **A forecast upsert arriving after the archive row exists** — expect the archive values to survive (Task 3 `test_forecast_never_overwrites_archive`).
4. **A backfill interrupted mid-way and restarted** — expect completed chunks skipped and no double spend (Task 4 `test_completed_chunks_are_skipped_on_resume`).
5. **An R11 CSV row dated tomorrow, or with a Canadian coordinate** — expect quarantine `future` / `outside_us`, never an inserted accident (Task 7 `test_future_and_foreign_rows_are_quarantined`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/app/pipelines/open_meteo.py` | Create | Archive client, response models, cost estimate, `kmh_to_ms`. |
| `backend/app/pipelines/cell_conditions.py` | Create | Day-row validation, freeze-thaw, precedence upsert. |
| `backend/app/pipelines/incident_weather_backfill.py` | Create | Incident-cell chunk planner + resumable, budgeted runner. |
| `backend/app/pipelines/r6_verify.py` | Create | Legacy-vs-new temperature correlation, gust p99, logged. |
| `backend/app/data/repair/area_weekly.py` | Create | R7 guarded sign fix. |
| `backend/app/pipelines/accident_facts.py` | Create | R11 facts CSV loader. |
| `backend/alembic/versions/0007_cell_daily_conditions.py` | Create | Table + `accident_conditions` view. |
| `backend/app/models/conditions.py` | Create | `CellDailyConditions` model. |
| `data/manual/README.md`, `data/manual/accident_facts.template.csv` | Create | Facts CSV contract (header only). |
| `backend/db/roles/grants_phase2.sql`, `verify_roles_phase2.sql` | Modify | Grants. |
| `backend/tests/test_open_meteo.py`, `test_cell_conditions.py`, `test_incident_backfill.py`, `test_area_weekly.py`, `test_accident_facts.py`, `test_migration_0007.py` | Create | Tests. |
| `backend/tests/verify/test_phase2a_conditions.py` | Create | `-m db` cells for R6, R7, R11, 2a-3 acceptance. |
| `backend/pyproject.toml`, `CHANGELOG.md`, `CLAUDE.md`, `data/DATABASE_STRUCTURE.md`, `DEPLOYMENT.md` | Modify | mypy, docs. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 3, 4, plans 5 and 7 | `open_meteo.ArchiveClient.fetch`, `LocationResponse`, `DAILY_VARS`, `cost_units` | Frozen in Task 1; plan 7 adds a `ForecastClient` beside it without changing these. |
| 2 | 3, 4, 5, plans 5, 7, 8 | `cell_daily_conditions` columns and PK | Task 2 creates every Phase 2 column now (lightning, AQI, alerts, SWE) so plans 7/8 add no ALTERs to a large table. |
| 3 | 4, plan 7 | `cell_conditions.upsert_rows(conn, rows, run_id)` precedence rule | Frozen; plan 7's forecast writer calls it with `is_forecast=True`. |
| 2 | 5 | `accident_conditions` view's grid expression | Must equal `grid.grid_bucket_sql`; `test_migration_0007.py` pins it. |
| 6 | plan 7 | `area_weekly_weather` | R7 fixes one row; plan 7 drops the table. |
| 7 | plan 2 | `activity.classify_activity`, `severity.severity_scale`, R5 rerun | R11 reuses both functions; the runbook reruns `r5` after loading. |
| 2, 6, 7 | each other, plans 1–2 | `grants_phase2.sql`, `verify_roles_phase2.sql` | Append-only, serial. |
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
  - `@dataclass(frozen=True) Location(grid_bucket: int, lat: float, lon: float)`
  - `class DailyBlock(BaseModel)` with `time: list[date]` and one `list[float | None]` per var; `class LocationResponse(BaseModel)` with `latitude: float`, `longitude: float`, `elevation: float | None`, `daily: DailyBlock`
  - `class OpenMeteoError(Exception)` (message never contains the URL or key)
  - `class ArchiveClient(api_key: str | None, *, transport: httpx.BaseTransport | None = None, max_retries: int = 4, sleep: Callable[[float], None] = time.sleep)` with `fetch(locations: Sequence[Location], start: date, end: date) -> list[LocationResponse]`
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

LOC = [Location(4003947, 40.0, -105.3), Location(4003948, 40.0, -105.2)]


def _payload(lat: float, lon: float, days: list[str], tmax: list[float | None]) -> dict[str, object]:
    daily: dict[str, object] = {"time": days}
    for var in DAILY_VARS:
        daily[var] = tmax if var == "temperature_2m_max" else [1.0] * len(days)
    return {"latitude": lat, "longitude": lon, "elevation": 1650.0, "daily": daily}


def test_wind_conversion_36_kmh_is_10_ms():
    assert kmh_to_ms(36.0) == pytest.approx(10.0)


def test_cost_units_rule():
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
    assert seen["host"] == "customer-archive-api.open-meteo.com"
    assert seen["wind_speed_unit"] == "ms" and seen["models"] == "era5_seamless" and seen["timezone"] == "GMT"
    assert seen["latitude"] == "40.0,40.0" and seen["longitude"] == "-105.3,-105.2"
    assert seen["daily"] == ",".join(DAILY_VARS)


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
    daily: DailyBlock


def cost_units(n_locations: int, n_vars: int, n_days: int) -> int:
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
        params = {
            "latitude": ",".join(f"{loc.lat:.1f}" for loc in locations),
            "longitude": ",".join(f"{loc.lon:.1f}" for loc in locations),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": ",".join(DAILY_VARS),
            "timezone": "GMT",
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

The `length` message from `_same_length` surfaces through `exc.errors()[0]['msg']` ("Value error, temperature_2m_max length 1 != time length 2").

Append `"app.pipelines.open_meteo"` to the strict mypy block.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_open_meteo.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/open_meteo.py backend/tests/test_open_meteo.py backend/pyproject.toml && git commit -m "feat(pipelines): Open-Meteo ERA5 archive client (m/s, validated, key never logged)"`

---

### Task 2: Migration `0007` — `cell_daily_conditions` and `accident_conditions`

**Files:**
- Create: `backend/alembic/versions/0007_cell_daily_conditions.py`, `backend/app/models/conditions.py`, `backend/tests/test_migration_0007.py`
- Modify: `backend/app/models/__init__.py`, `backend/pyproject.toml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Produces (DB): `cell_daily_conditions(grid_bucket integer, date date, tmax real, tmin real, precip_mm real, snowfall_cm real, snow_depth_cm real, wind_max_ms real, gust_max_ms real, freeze_thaw boolean, swe_delta_mm real, nws_alert_codes text[], aqi smallint, lightning_density real, lightning_cg_nldn integer, lightning_source text, lightning_coverage text CHECK IN ('glm','nldn','none'), is_forecast boolean NOT NULL DEFAULT false, model text, source text NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now(), run_id uuid, PRIMARY KEY (grid_bucket, date))`; view `accident_conditions(accident_id, day_offset, grid_bucket, date, tmax, tmin, precip_mm, snowfall_cm, wind_max_ms, gust_max_ms, freeze_thaw, is_forecast)` over `accidents_clean_daily` × offsets −6..0.
- Produces (Python): `app.models.conditions.CellDailyConditions`.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0007.py`:

```python
import asyncio

import asyncpg
import pytest
from alembic import command

from app.pipelines.grid import grid_bucket, grid_bucket_sql
from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg


async def _fetch(url: str, sql: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetch(sql)
    finally:
        await conn.close()


def test_view_uses_the_frozen_grid_expression():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))
        [row] = asyncio.run(_fetch(pg_url(name), "SELECT pg_get_viewdef('accident_conditions'::regclass) AS d"))
        assert "floor" in row["d"] and "10000" in row["d"] and "5000" in row["d"]
        assert grid_bucket_sql("a", "b").count("floor") == 2


def test_accident_conditions_joins_on_cell_and_date_with_seven_offsets():
    b = grid_bucket(40.01, -105.27)
    seed = (
        "INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, date_precision) "
        "VALUES (1, 'AAC', '2010-07-10', 40.01, -105.27, true, 'climbing', 'US', 'day');"
        + "".join(
            f"INSERT INTO cell_daily_conditions (grid_bucket, date, tmax, source) VALUES ({b}, DATE '2010-07-10' - {k}, {20 + k}, 'test');"
            for k in range(0, 8)
        )
    )
    with migrated_db(seed_sql=seed) as name:
        rows = asyncio.run(_fetch(pg_url(name), "SELECT day_offset, tmax FROM accident_conditions ORDER BY day_offset"))
        assert [(r["day_offset"], r["tmax"]) for r in rows] == [(-k, 20 + k) for k in range(6, -1, -1)]


def test_lightning_coverage_check():
    with migrated_db() as name:
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO cell_daily_conditions (grid_bucket, date, source, lightning_coverage) "
                          "VALUES (1, '2020-01-01', 't', 'radar')")
```

- [ ] **Step 2: Run to verify failure** — FAIL (relation does not exist).

- [ ] **Step 3: Implement**

`backend/alembic/versions/0007_cell_daily_conditions.py`:

```python
"""cell_daily_conditions (P3:151 + Phase 2 columns) and accident_conditions.

Every Phase 2 column is created now, including lightning, AQI, alerts and SWE, so later
plans never ALTER a table of tens of millions of rows. accident_conditions joins on
(cell, date) — never on accident_id — using the frozen grid expression from
app/pipelines/grid.py (test_migration_0007 pins it).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_cell_daily_conditions"
down_revision = "0006_accidents_clean"
branch_labels = None
depends_on = None

GRID = "(floor(a.latitude * 10 + 0.5)::int * 10000 + floor(a.longitude * 10 + 0.5)::int + 5000)"


def upgrade() -> None:
    op.create_table(
        "cell_daily_conditions",
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
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
        sa.Column("lightning_density", sa.REAL(), nullable=True),
        sa.Column("lightning_cg_nldn", sa.Integer(), nullable=True),
        sa.Column("lightning_source", sa.Text(), nullable=True),
        sa.Column("lightning_coverage", sa.Text(), nullable=True),
        sa.Column("is_forecast", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("grid_bucket", "date"),
        sa.CheckConstraint(
            "lightning_coverage IS NULL OR lightning_coverage IN ('glm', 'nldn', 'none')",
            name="cell_daily_conditions_lightning_coverage_check",
        ),
    )
    op.execute(
        "CREATE VIEW accident_conditions AS "
        "SELECT a.accident_id, o.day_offset, c.grid_bucket, c.date, c.tmax, c.tmin, c.precip_mm, c.snowfall_cm, "
        "c.wind_max_ms, c.gust_max_ms, c.freeze_thaw, c.is_forecast "
        "FROM accidents_clean_daily a CROSS JOIN generate_series(-6, 0) AS o(day_offset) "
        f"JOIN cell_daily_conditions c ON c.grid_bucket = {GRID} AND c.date = a.date + o.day_offset "
        "WHERE a.latitude IS NOT NULL AND a.longitude IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP VIEW accident_conditions")
    rows = op.get_bind().exec_driver_sql("SELECT count(*) FROM cell_daily_conditions").scalar_one()
    if rows:
        raise RuntimeError(f"refusing to downgrade 0007: cell_daily_conditions has {rows} rows (paid backfill)")
    op.drop_table("cell_daily_conditions")
```

The downgrade checks rows after dropping the view inside the same transaction; a refusal rolls both back.

`backend/app/models/conditions.py`:

```python
"""Daily conditions per 0.1° cell (migration 0007)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import REAL, Boolean, Date, DateTime, Integer, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CellDailyConditions(Base):
    __tablename__ = "cell_daily_conditions"

    grid_bucket: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
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
    lightning_density: Mapped[float | None] = mapped_column(REAL)
    lightning_cg_nldn: Mapped[int | None] = mapped_column(Integer)
    lightning_source: Mapped[str | None] = mapped_column(Text)
    lightning_coverage: Mapped[str | None] = mapped_column(Text)
    is_forecast: Mapped[bool] = mapped_column(Boolean, server_default="false")
    model: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
```

Register in `app/models/__init__.py` (`from app.models import conditions  # noqa: F401`) and add `"app.models.conditions"` to the models mypy block.

Grants (`grants_phase2.sql`):

```sql
-- Plan 3 (0007)
GRANT SELECT, INSERT, UPDATE ON public.cell_daily_conditions TO ingest;
GRANT SELECT ON public.accidents_clean, public.accidents_clean_daily TO ingest;
GRANT SELECT ON public.cell_daily_conditions, public.accident_conditions TO trainer;
```

`verify_roles_phase2.sql` `ingest_writes`: `('public.cell_daily_conditions','INSERT'), ('public.cell_daily_conditions','UPDATE')`.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_migration_0007.py tests/test_roles_phase2.py tests/test_migrations.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0007_cell_daily_conditions.py backend/app/models/ backend/tests/test_migration_0007.py backend/pyproject.toml backend/db/roles/ && git commit -m "feat(db): 0007 cell_daily_conditions and accident_conditions (cell+date join)"`

---

### Task 3: Validating conditions writer

**Files:**
- Create: `backend/app/pipelines/cell_conditions.py`, `backend/tests/test_cell_conditions.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: `LocationResponse` (Task 1), `ValidationReport`, `range_problem` (plan 1), table from Task 2.
- Produces: `RANGES: dict[str, tuple[float, float]]`, `@dataclass(frozen=True) DayRow(grid_bucket: int, date: date, tmax, tmin, precip_mm, snowfall_cm, wind_max_ms, gust_max_ms: float | None, freeze_thaw: bool | None, is_forecast: bool, model: str, source: str)`, `day_rows(grid_bucket: int, response: LocationResponse, *, today: date, is_forecast: bool, model: str, source: str, report: ValidationReport) -> list[DayRow]`, `async upsert_rows(conn, rows: Sequence[DayRow], *, run_id: uuid.UUID) -> int`.

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


def _resp(days, tmax, tmin, gust):
    n = len(days)
    return LocationResponse.model_validate({
        "latitude": 40.0, "longitude": -105.3, "elevation": 1650.0,
        "daily": {"time": days, "temperature_2m_max": tmax, "temperature_2m_min": tmin,
                  "precipitation_sum": [0.0] * n, "snowfall_sum": [0.0] * n,
                  "wind_speed_10m_max": [5.0] * n, "wind_gusts_10m_max": gust},
    })


def test_null_values_stay_null():
    report = ValidationReport("t")
    [row] = day_rows(1, _resp(["2020-01-01"], [None], [-3.0], [None]), today=TODAY, is_forecast=False,
                     model="era5_seamless", source="open_meteo_archive", report=report)
    assert (row.tmax, row.gust_max_ms, row.freeze_thaw) == (None, None, None)
    assert report.accepted == 1


def test_freeze_thaw_and_quarantine_rules():
    report = ValidationReport("t")
    rows = day_rows(
        1,
        _resp(["2020-01-01", "2020-01-02", "2020-01-03", "2026-09-29"], [2.0, 80.0, -5.0, 1.0], [-1.0, 0.0, 3.0, 0.0], [10.0, 10.0, 10.0, 10.0]),
        today=TODAY, is_forecast=False, model="era5_seamless", source="open_meteo_archive", report=report,
    )
    assert [(r.date.day, r.freeze_thaw) for r in rows] == [(1, True)]
    assert report.quarantined == {"out_of_range": 1, "tmax_below_tmin": 1, "future": 1}


def test_forecast_rows_may_be_in_the_future():
    report = ValidationReport("t")
    rows = day_rows(1, _resp(["2026-09-30"], [5.0], [-2.0], [9.0]), today=TODAY, is_forecast=True,
                    model="best_match", source="open_meteo_forecast", report=report)
    assert len(rows) == 1


@requires_pg
def test_forecast_never_overwrites_archive():
    def row(tmax: float, forecast: bool) -> DayRow:
        return DayRow(1, date(2026, 9, 20), tmax, -1.0, 0.0, 0.0, 5.0, 9.0, True, forecast, "m", "s")

    async def scenario(url: str) -> list[tuple[float, bool]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await upsert_rows(conn, [row(10.0, True)], run_id=uuid.uuid4())
                await upsert_rows(conn, [row(12.0, False)], run_id=uuid.uuid4())
                await upsert_rows(conn, [row(99.0, True)], run_id=uuid.uuid4())
                result = await conn.execute(text("SELECT tmax, is_forecast FROM cell_daily_conditions"))
                return [(float(a), bool(b)) for a, b in result.all()]
        finally:
            await engine.dispose()

    with migrated_db() as name:
        assert asyncio.run(scenario(sa_url(name))) == [(12.0, False)]
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/cell_conditions.py`:

```python
"""Validated day rows into cell_daily_conditions. Archive beats forecast: a forecast row
never replaces an archive row for the same (cell, date)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.open_meteo import LocationResponse
from app.pipelines.validate import ValidationReport, range_problem

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
    date: date
    tmax: float | None
    tmin: float | None
    precip_mm: float | None
    snowfall_cm: float | None
    wind_max_ms: float | None
    gust_max_ms: float | None
    freeze_thaw: bool | None
    is_forecast: bool
    model: str
    source: str


def day_rows(
    grid_bucket: int,
    response: LocationResponse,
    *,
    today: date,
    is_forecast: bool,
    model: str,
    source: str,
    report: ValidationReport,
) -> list[DayRow]:
    d = response.daily
    rows: list[DayRow] = []
    for i, day in enumerate(d.time):
        ref = f"{grid_bucket}:{day.isoformat()}"
        values = {
            "tmax": d.temperature_2m_max[i],
            "tmin": d.temperature_2m_min[i],
            "precip_mm": d.precipitation_sum[i],
            "snowfall_cm": d.snowfall_sum[i],
            "wind_max_ms": d.wind_speed_10m_max[i],
            "gust_max_ms": d.wind_gusts_10m_max[i],
        }
        if not is_forecast and day > today:
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
        rows.append(DayRow(grid_bucket, day, tmax, tmin, values["precip_mm"], values["snowfall_cm"],
                           values["wind_max_ms"], values["gust_max_ms"], freeze_thaw, is_forecast, model, source))
    return rows


UPSERT = text(
    "INSERT INTO cell_daily_conditions (grid_bucket, date, tmax, tmin, precip_mm, snowfall_cm, wind_max_ms, "
    "gust_max_ms, freeze_thaw, is_forecast, model, source, fetched_at, run_id) VALUES (:grid_bucket, :date, :tmax, "
    ":tmin, :precip_mm, :snowfall_cm, :wind_max_ms, :gust_max_ms, :freeze_thaw, :is_forecast, :model, :source, "
    "now(), :run_id) ON CONFLICT (grid_bucket, date) DO UPDATE SET tmax = EXCLUDED.tmax, tmin = EXCLUDED.tmin, "
    "precip_mm = EXCLUDED.precip_mm, snowfall_cm = EXCLUDED.snowfall_cm, wind_max_ms = EXCLUDED.wind_max_ms, "
    "gust_max_ms = EXCLUDED.gust_max_ms, freeze_thaw = EXCLUDED.freeze_thaw, is_forecast = EXCLUDED.is_forecast, "
    "model = EXCLUDED.model, source = EXCLUDED.source, fetched_at = now(), run_id = EXCLUDED.run_id "
    "WHERE cell_daily_conditions.is_forecast OR NOT EXCLUDED.is_forecast"
)


async def upsert_rows(conn: AsyncConnection, rows: Sequence[DayRow], *, run_id: uuid.UUID) -> int:
    if not rows:
        return 0
    await conn.execute(UPSERT, [asdict(r) | {"run_id": run_id} for r in rows])
    return len(rows)
```

Append `"app.pipelines.cell_conditions"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_cell_conditions.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/cell_conditions.py backend/tests/test_cell_conditions.py backend/pyproject.toml && git commit -m "feat(pipelines): validated conditions writer, archive-beats-forecast upsert"`

---

### Task 4: Incident-cell ERA5 backfill (chunked, resumable, budgeted)

**Files:**
- Create: `backend/app/pipelines/incident_weather_backfill.py`, `backend/tests/test_incident_backfill.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: Tasks 1–3; `grid.bucket_center`, `grid.grid_bucket_sql`; `ingest_log.*`.
- Produces: `SOURCE = "era5_incident_backfill"`, `ERA5_LAG_DAYS = 5`, `@dataclass(frozen=True) Chunk(buckets: tuple[int, ...], start: date, end: date)` with `.units() -> int`, `plan_chunks(buckets: Sequence[int], *, start_year: int, last_day: date, batch: int, years_per_chunk: int) -> list[Chunk]`, `async incident_buckets(conn) -> list[int]`, `async run(conn_factory, client: ArchiveClient, chunks: Sequence[Chunk], *, today: date, max_units: int) -> dict[str, object]`, CLI `python -m app.pipelines.incident_weather_backfill [--start-year 1990] [--batch 50] [--years-per-chunk 5] --max-units N [--dry-run]`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_incident_backfill.py`:

```python
import asyncio
from datetime import date

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.grid import grid_bucket
from app.pipelines.incident_weather_backfill import Chunk, plan_chunks, run
from app.pipelines.open_meteo import DAILY_VARS, ArchiveClient
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)


def test_chunks_cover_every_day_once_and_stop_at_the_lag():
    chunks = plan_chunks([1, 2, 3], start_year=2020, last_day=date(2026, 9, 23), batch=2, years_per_chunk=3)
    assert [(c.buckets, c.start, c.end) for c in chunks] == [
        ((1, 2), date(2020, 1, 1), date(2022, 12, 31)),
        ((1, 2), date(2023, 1, 1), date(2025, 12, 31)),
        ((1, 2), date(2026, 1, 1), date(2026, 9, 23)),
        ((3,), date(2020, 1, 1), date(2022, 12, 31)),
        ((3,), date(2023, 1, 1), date(2025, 12, 31)),
        ((3,), date(2026, 1, 1), date(2026, 9, 23)),
    ]
    assert Chunk((1, 2), date(2020, 1, 1), date(2020, 1, 14)).units() == 2


def _handler(request: httpx.Request) -> httpx.Response:
    lats = request.url.params["latitude"].split(",")
    start, end = date.fromisoformat(request.url.params["start_date"]), date.fromisoformat(request.url.params["end_date"])
    days = [date.fromordinal(o).isoformat() for o in range(start.toordinal(), end.toordinal() + 1)]
    body = []
    for lat, lon in zip(lats, request.url.params["longitude"].split(",")):
        daily: dict[str, object] = {"time": days}
        for var in DAILY_VARS:
            daily[var] = [1.0] * len(days)
        body.append({"latitude": float(lat), "longitude": float(lon), "daily": daily})
    return httpx.Response(200, json=body)


@requires_pg
def test_completed_chunks_are_skipped_on_resume():
    b = grid_bucket(40.0, -105.3)
    chunks = [Chunk((b,), date(2020, 1, 1), date(2020, 1, 10)), Chunk((b,), date(2020, 1, 11), date(2020, 1, 20))]

    async def scenario(url: str) -> tuple[dict[str, object], dict[str, object], dict[str, object], int]:
        def factory():
            return create_async_engine(url)

        client = ArchiveClient(None, transport=httpx.MockTransport(_handler))
        capped = await run(factory, client, chunks, today=TODAY, max_units=1)
        full = await run(factory, client, chunks, today=TODAY, max_units=100)
        again = await run(factory, client, chunks, today=TODAY, max_units=100)
        engine = factory()
        try:
            async with engine.connect() as conn:
                n = (await conn.execute(text("SELECT count(*) FROM cell_daily_conditions"))).scalar_one()
        finally:
            await engine.dispose()
        return capped, full, again, int(n)

    with migrated_db() as name:
        capped, full, again, n = asyncio.run(scenario(sa_url(name)))
    assert (capped["done"], capped["stopped_for_budget"]) == (1, True)
    assert (full["done"], full["skipped"]) == (1, 1)
    assert (again["done"], again["skipped"]) == (0, 2)
    assert n == 20
```

The capped first run also covers the budget stop.

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/incident_weather_backfill.py`:

```python
"""One-time ERA5 backfill for every cell holding a clean day-precision incident (spec R6,
~0.67M units). Chunked by bucket batch × years, resumable from source_ingest_log, and
stopped before --max-units is exceeded so a run can never overspend the paid month."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.cell_conditions import day_rows, upsert_rows
from app.pipelines.grid import bucket_center, grid_bucket_sql
from app.pipelines.ingest_log import find_completed, finish_run, sha256_rows, start_run, write_quarantine
from app.pipelines.open_meteo import ARCHIVE_MODEL, DAILY_VARS, ArchiveClient, Location, OpenMeteoError, cost_units
from app.pipelines.validate import ValidationReport

SOURCE = "era5_incident_backfill"
ERA5_LAG_DAYS = 5


@dataclass(frozen=True)
class Chunk:
    buckets: tuple[int, ...]
    start: date
    end: date

    def units(self) -> int:
        return cost_units(len(self.buckets), len(DAILY_VARS), (self.end - self.start).days + 1)


def plan_chunks(
    buckets: Sequence[int], *, start_year: int, last_day: date, batch: int, years_per_chunk: int
) -> list[Chunk]:
    ordered = sorted(set(buckets))
    chunks: list[Chunk] = []
    for i in range(0, len(ordered), batch):
        group = tuple(ordered[i : i + batch])
        year = start_year
        while year <= last_day.year:
            end = min(date(year + years_per_chunk - 1, 12, 31), last_day)
            chunks.append(Chunk(group, date(year, 1, 1), end))
            year += years_per_chunk
    return chunks


async def incident_buckets(conn: AsyncConnection) -> list[int]:
    grid = grid_bucket_sql("latitude", "longitude")
    result = await conn.execute(
        text(f"SELECT DISTINCT {grid} FROM accidents_clean_daily WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    )
    return sorted(int(b) for (b,) in result.all())


async def run(
    engine_factory: Callable[[], AsyncEngine],
    client: ArchiveClient,
    chunks: Sequence[Chunk],
    *,
    today: date,
    max_units: int,
) -> dict[str, object]:
    engine = engine_factory()
    spent = done = skipped = failed = 0
    stopped = False
    try:
        for chunk in chunks:
            sha = sha256_rows([(b,) for b in chunk.buckets])
            async with engine.begin() as conn:
                if await find_completed(conn, source=SOURCE, window_start=chunk.start, window_end=chunk.end, content_sha256=sha):
                    skipped += 1
                    continue
            if spent + chunk.units() > max_units:
                stopped = True
                break
            report = ValidationReport(SOURCE)
            async with engine.begin() as conn:
                run_id = await start_run(conn, source=SOURCE, window_start=chunk.start, window_end=chunk.end, content_sha256=sha)
            try:
                responses = client.fetch([Location(b, *bucket_center(b)) for b in chunk.buckets], chunk.start, chunk.end)
            except OpenMeteoError as exc:
                failed += 1
                async with engine.begin() as conn:
                    await finish_run(conn, run_id, status="failed", report=report, rows_upserted=0, problems=[str(exc)])
                continue
            spent += chunk.units()
            rows = [
                row
                for b, response in zip(chunk.buckets, responses)
                for row in day_rows(b, response, today=today, is_forecast=False, model=ARCHIVE_MODEL,
                                    source="open_meteo_archive", report=report)
            ]
            async with engine.begin() as conn:
                await write_quarantine(conn, run_id, report)
                n = await upsert_rows(conn, rows, run_id=run_id)
                await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n, cost_units=chunk.units())
            done += 1
    finally:
        await engine.dispose()
    return {"done": done, "skipped": skipped, "failed": failed, "units_spent": spent, "stopped_for_budget": stopped}


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.config import settings
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    today = utc_today()
    engine = ingest_engine()
    try:
        async with engine.connect() as conn:
            buckets = await incident_buckets(conn)
    finally:
        await engine.dispose()
    chunks = plan_chunks(buckets, start_year=args.start_year, last_day=today - timedelta(days=ERA5_LAG_DAYS),
                         batch=args.batch, years_per_chunk=args.years_per_chunk)
    plan = {"buckets": len(buckets), "chunks": len(chunks), "units_total": sum(c.units() for c in chunks)}
    if args.dry_run:
        return plan | {"mode": "dry_run"}
    client = ArchiveClient(settings.OPEN_METEO_API_KEY)
    return plan | await run(ingest_engine, client, chunks, today=today, max_units=args.max_units)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=1990)
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--years-per-chunk", type=int, default=5)
    parser.add_argument("--max-units", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    print(json.dumps(asyncio.run(_main(parser.parse_args())), sort_keys=True))
```

`--start-year 1990` is the spec's backfill floor (ERA5 itself starts in 1940); the end is always today minus the ERA5 lag.

Append `"app.pipelines.incident_weather_backfill"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_incident_backfill.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS (the capped run does chunk 1 = 1 unit, then stops before chunk 2; the full run skips chunk 1 and does chunk 2; the third run skips both; 20 rows).
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/incident_weather_backfill.py backend/tests/test_incident_backfill.py backend/pyproject.toml && git commit -m "feat(pipelines): resumable, budget-capped ERA5 backfill for incident cells"`

---

### Task 5: R6 verification against the legacy `weather` table

**Files:**
- Create: `backend/app/pipelines/r6_verify.py`, `backend/tests/verify/test_phase2a_conditions.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `R6_SQL: str` (returns `n`, `r_tmax`, `p99_gust`), `async verify(conn) -> dict[str, float | int | None]`, CLI `python -m app.pipelines.r6_verify` (logs `source='r6_verify'` with the numbers in `validation_report`); `-m db` cells: 7-day window coverage, r > 0.95, gust p99 < 60, no visibility column.

- [ ] **Step 1: Write the verification cells** — `backend/tests/verify/test_phase2a_conditions.py`:

```python
import asyncio
import os

import asyncpg
import pytest

from app.pipelines.r6_verify import R6_SQL

pytestmark = pytest.mark.db
URL = os.environ.get("VERIFY_DATABASE_URL")


def fetch(sql: str) -> list[asyncpg.Record]:
    if not URL:
        pytest.skip("VERIFY_DATABASE_URL not set")

    async def go() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(URL)
        try:
            return await conn.fetch(sql)
        finally:
            await conn.close()

    return asyncio.run(go())


def test_every_clean_day_incident_has_a_seven_day_window():
    [row] = fetch(
        "SELECT count(*) AS missing FROM accidents_clean_daily a "
        "WHERE a.latitude IS NOT NULL AND a.date <= (now() AT TIME ZONE 'UTC')::date - 5 "
        "AND (SELECT count(*) FROM accident_conditions c WHERE c.accident_id = a.accident_id) < 7"
    )
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
  JOIN cell_daily_conditions c ON c.grid_bucket = {GRID} AND c.date = w.date
  WHERE w.date BETWEEN a.date - 6 AND a.date
    AND abs(w.latitude - a.latitude) < 0.05 AND abs(w.longitude - a.longitude) < 0.05
    AND w.temperature_max IS NOT NULL AND c.tmax IS NOT NULL AND NOT c.is_forecast
)
SELECT (SELECT count(*) FROM linked) AS n,
       (SELECT corr(legacy_tmax, new_tmax) FROM linked) AS r_tmax,
       (SELECT percentile_cont(0.99) WITHIN GROUP (ORDER BY gust_max_ms) FROM cell_daily_conditions
         WHERE gust_max_ms IS NOT NULL) AS p99_gust
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

`grants_phase2.sql`: add `GRANT SELECT ON public.weather TO ingest;` under Plan 3 (the legacy table is read, never written). Append `"app.pipelines.r6_verify"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run pytest -m db --co -q tests/verify/test_phase2a_conditions.py | tail -1 && uv run mypy` → default suite green; 3 cells collected.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/r6_verify.py backend/tests/verify/test_phase2a_conditions.py backend/db/roles/grants_phase2.sql backend/pyproject.toml && git commit -m "feat(pipelines): R6 verification against correctly linked legacy weather"`

---

### Task 6: R7 `area_weekly_weather` sign fix, PR 2a-3 docs

**Files:**
- Create: `backend/app/data/repair/area_weekly.py`, `backend/tests/test_area_weekly.py`
- Modify: `backend/app/data/repair/__main__.py`, `backend/db/roles/grants_phase2.sql`, `backend/pyproject.toml`, `CHANGELOG.md`, `CLAUDE.md`, `data/DATABASE_STRUCTURE.md`, `DEPLOYMENT.md`

**Interfaces:**
- Produces: `R7_VERSION = "r7-v1"`, `async run_r7(conn, args) -> dict[str, object]` registered as CLI step `r7`. Refuses unless exactly one row has `longitude > 0` and it equals `113.2`.

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

Docs: `CHANGELOG.md` entry "Phase 2a conditions (PR 2a-3)": Open-Meteo archive client (m/s, key never logged), `cell_daily_conditions` + `accident_conditions` (0007), validated writer with archive-beats-forecast, resumable budget-capped incident backfill, R6 verification (the legacy `weather` table stays read-only for the live kernel until Phase 3 MVP-1 deletes it), R7. `CLAUDE.md` Commands: `uv run python -m app.pipelines.incident_weather_backfill --max-units N [--dry-run]`. `DEPLOYMENT.md` "Database…": "`weather` (legacy, read-only) is dropped by the Phase 3 MVP-1 PR that deletes the kernel scorer; Phase 2 does not drop it." `data/DATABASE_STRUCTURE.md`: `cell_daily_conditions` (columns, PK, units) and `accident_conditions`; mark `weather` legacy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd .. && python scripts/check_no_scrapers.py` → green.
- [ ] **Step 5: Commit** — `git add backend/app/data/repair/area_weekly.py backend/app/data/repair/__main__.py backend/tests/test_area_weekly.py backend/db/roles/grants_phase2.sql backend/pyproject.toml CHANGELOG.md CLAUDE.md data/DATABASE_STRUCTURE.md DEPLOYMENT.md && git commit -m "feat(repair): R7 guarded area_weekly_weather sign fix; PR 2a-3 docs"`

---

# PR 2a-4 — `feat/p2a-accident-refresh`

### Task 7: R11 facts-only accident loader

**Files:**
- Create: `backend/app/pipelines/accident_facts.py`, `backend/tests/test_accident_facts.py`, `data/manual/README.md`, `data/manual/accident_facts.template.csv`
- Modify: `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`, `backend/pyproject.toml`

**Interfaces:**
- Consumes: `activity.classify_activity`, `severity.severity_scale`, `sources.source_family` (plan 2); `validate.*`, `ingest_log.*` (plan 1).
- Produces: `COLUMNS: tuple[str, ...]` (the CSV header), `class FactRow(BaseModel)` (strict field types), `parse_facts(path: Path, *, today: date, report: ValidationReport) -> list[FactRow]`, `async insert_new(conn, rows: list[FactRow], *, report: ValidationReport) -> int`, CLI `python -m app.pipelines.accident_facts --file PATH [--dry-run]`.

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

from app.pipelines.accident_facts import COLUMNS, insert_new, parse_facts
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


def test_blank_coordinates_are_allowed_as_unknown_geocode(tmp_path):
    row = GOOD.replace("40.25,-105.6,crag", ",,unknown")
    report = ValidationReport("t")
    [parsed] = parse_facts(_csv(tmp_path, row), today=TODAY, report=report)
    assert parsed.latitude is None and parsed.geocode_precision == "unknown"


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
                    "SELECT activity_class, severity_scale, country, is_canonical, date_precision FROM accidents"))).one()
            return first, second, dict(report2.quarantined), tuple(stored)
        finally:
            await engine.dispose()

    with migrated_db() as name:
        first, second, quarantined, stored = asyncio.run(scenario(sa_url(name)))
    assert (first, second, quarantined) == (1, 0, {"already_present": 1})
    assert stored == ("climbing", "full", "US", True, "day")
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/accident_facts.py`:

```python
"""R11: facts-only accident refresh from CSV (manual AAC entries; CAIC/NPS exports produced
by private tools outside this repo). New rows only; nothing existing is overwritten."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
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
from app.pipelines.validate import ValidationReport, coord_problem, date_problem

SOURCE = "accident_facts"
COLUMNS: tuple[str, ...] = (
    "source", "source_id", "date", "date_precision", "state", "mountain", "route", "latitude", "longitude",
    "geocode_precision", "activity", "accident_type", "injury_severity", "exp_years_climbing",
    "exp_stated_level", "exp_first_season", "guided", "source_url", "summary",
)
EARLIEST = date(1970, 1, 1)


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
            try:
                source_family(row.source)
            except ValueError:
                report.quarantine(ref, "unmapped_source")
                continue
            report.accept()
            rows.append(row)
    return rows


INSERT = text(
    "INSERT INTO accidents (source, source_id, date, year, date_precision, state, mountain, route, latitude, "
    "longitude, geocode_precision, geocode_method, country, activity, activity_class, activity_rule_version, "
    "inclusion_flag, accident_type, injury_severity, severity_scale, exp_years_climbing, exp_stated_level, "
    "exp_first_season, guided, exp_rule_version, source_url, description, is_canonical, updated_at) VALUES "
    "(:source, :source_id, :date, :year, :date_precision, :state, :mountain, :route, :latitude, :longitude, "
    ":geocode_precision, 'facts_csv', 'US', :activity, :activity_class, :activity_rule_version, :inclusion_flag, "
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
        await conn.execute(INSERT, row.model_dump() | {
            "year": row.date.year,
            "activity_class": activity_class,
            "activity_rule_version": R4_VERSION,
            "inclusion_flag": flag,
            "severity_scale": severity_scale(family, row.injury_severity),
        })
        inserted += 1
    return inserted


async def main(path: Path, *, dry_run: bool) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    report = ValidationReport(SOURCE)
    rows = parse_facts(path, today=utc_today(), report=report)
    if dry_run:
        return {"mode": "dry_run", "report": report.summary()}
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None,
                                     content_sha256=sha256_rows([(r.source, r.source_id) for r in rows]))
            n = await insert_new(conn, rows, report=report)
            await write_quarantine(conn, run_id, report)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n)
    finally:
        await engine.dispose()
    return {"inserted": n, "report": report.summary()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main(args.file, dry_run=args.dry_run)), sort_keys=True, default=str))
```

Rows keep the ski-approach rule honest: CSV rows are classified with `near_climb_terrain=False`, so a ski row is `non_climbing` unless a later full `r4` run (which has coordinates and terrain) upgrades it.

`data/manual/README.md`:

```markdown
# Manual facts CSVs (R11)

`accident_facts.template.csv` is the only file committed here: the header of the facts-only CSV the
`app.pipelines.accident_facts` loader accepts. Filled files (AAC entries typed from ANAC/The
Prescription, CAIC and NPS exports from the private tools) live in
`~/Developer/safeascent-private/manual/` until legal question Q6 is answered (Decision D12).

Rules: facts only (date, place, activity, type, severity, experience facts, URL). `summary` is our
own one-line wording, at most 200 characters; never paste report text. `source_url` must be https.
Dates after the load day and coordinates outside the US are quarantined.
```

Grants (`grants_phase2.sql`, "Plan 3 (PR 2a-4)"):

```sql
GRANT INSERT ON public.accidents TO ingest;
GRANT USAGE ON SEQUENCE public.accidents_accident_id_seq TO ingest;
```

`verify_roles_phase2.sql` `ingest_writes`: `('public.accidents','INSERT')`. In `test_roles_phase2.py` add `_as(ingest, "INSERT INTO accidents (source) VALUES ('AAC')")` after the other ingest writes, and `_denied(ingest, "DELETE FROM accidents WHERE false")`.

Append `"app.pipelines.accident_facts"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_accident_facts.py tests/test_roles_phase2.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/accident_facts.py backend/tests/test_accident_facts.py backend/tests/test_roles_phase2.py data/manual/ backend/db/roles/ backend/pyproject.toml && git commit -m "feat(pipelines): R11 facts-only accident loader (new rows only, validated)"`

---

### Task 8: R11 verification cell and PR 2a-4 docs

**Files:**
- Modify: `backend/tests/verify/test_phase2a_conditions.py`, `CHANGELOG.md`, `CLAUDE.md`

- [ ] **Step 1: Add the cell**

```python
def test_r11_refresh_window_is_reported():
    [row] = fetch(
        "SELECT count(*) AS n FROM accidents_clean WHERE date BETWEEN DATE '2026-02-05' AND DATE '2026-07-02'"
    )
    print({"feb5_jul2_2026_clean_incidents": row["n"]})
    [latest] = fetch("SELECT max(date) AS d FROM accidents WHERE date <= (now() AT TIME ZONE 'UTC')::date")
    assert latest["d"] is not None and latest["d"].year >= 2025
```

(The Feb 5–Jul 2 2026 window is the spec's backtest window, a historical span, not a "future" cutoff.)

- [ ] **Step 2: Docs** — `CHANGELOG.md` "Phase 2a accident refresh (PR 2a-4)": facts CSV loader, template, private-dir rule, `ingest` gains INSERT on `accidents`. `CLAUDE.md` Commands: `uv run python -m app.pipelines.accident_facts --file PATH [--dry-run]`.
- [ ] **Step 3: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/` → green.
- [ ] **Step 4: Commit** — `git add backend/tests/verify/test_phase2a_conditions.py CHANGELOG.md CLAUDE.md && git commit -m "docs: R11 refresh verification and commands"`

---

### Task 9: OWNER/AGENT RUNBOOK — PR 2a-3: migrate, backfill, verify R6, R7

- [ ] **Step 1 (owner): Buy one month of Open-Meteo Professional** (P2-5, $99). Put the key in a gitignored file with an editor: `backend/.env.openmeteo` containing `OPEN_METEO_API_KEY=<key>`. `git check-ignore -v .env.openmeteo` must match `.env.*`.

- [ ] **Step 2 (owner/agent): Rehearsal branch `p2a-3-rehearsal`**: migrate as `migrator` (→ `0007_cell_daily_conditions (head)`, `alembic check` clean), run `grants_phase2.sql`, both verify scripts (foundations Task 9 Step 2 pattern).

- [ ] **Step 3 (owner/agent): Dry run the backfill plan**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.ingest; . ./.env.openmeteo; set +a
  export INGEST_DATABASE_URL="$(printf '%s' "$INGEST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.incident_weather_backfill --max-units 0 --dry-run )
```

Expected: `buckets` ≈ 700, `units_total` ≈ 0.6–0.7M (spec: ~0.67M). If `units_total` is above 0.8M, stop and review with the agent (budget).

- [ ] **Step 4 (owner/agent): Run a small first slice on the branch and check the dashboard**

Same command with `--max-units 20000` (no `--dry-run`). Expected: `done` > 0, `failed: 0`. Compare the Open-Meteo dashboard's used calls with `units_spent`; if the dashboard reports more than 1.2× `units_spent`, tell the agent (the cost estimate in `cost_units` must be corrected before the full run).

- [ ] **Step 5 (owner/agent): Verify on the branch** — `uv run python -m app.pipelines.r6_verify` as ingest, then `VERIFY_DATABASE_URL=… uv run pytest -m db tests/verify/test_phase2a_conditions.py -q -s -k "r6 or visibility"` on the partial data: `r_tmax > 0.95`, `p99_gust < 60`. The window-coverage cell will fail until the full backfill; that is expected on the rehearsal.

- [ ] **Step 6 (owner/agent): Prod** — migrate + grants + verify scripts on prod; run the backfill on prod in slices (`--max-units 150000`), rerunning until the JSON shows `stopped_for_budget: false` and `done: 0, skipped: <chunks>`; each rerun resumes where the last stopped. Then R6 verify and all `-m db` cells in `test_phase2a_conditions.py` except `r11` → pass. Then R7: `ING r7` (dry run shows `would_update_longitude: 113.2`), `ING r7 --apply` → `updated: 1`, rerun → `note: no positive longitudes`.

- [ ] **Step 7 (owner): End of the paid month** — after the backfill (and plan 5's normals backfill, which should run in the same paid month), downgrade Open-Meteo to Standard ($29/mo). Delete the rehearsal branch.

---

### Task 10: OWNER/AGENT RUNBOOK — PR 2a-4: load the refresh

- [ ] **Step 1 (owner): Prepare the CSVs** in `~/Developer/safeascent-private/manual/`: `aac_refresh.csv` (typed by hand from ANAC 2025/2026 and The Prescription, facts only, ~60–120 rows), `caic_refresh.csv` and `nps_refresh.csv` (from the private exporters, same header). Copy the header from `data/manual/accident_facts.template.csv`.

- [ ] **Step 2 (owner/agent): Dry run each file, then load on a branch**

```bash
for f in aac_refresh caic_refresh nps_refresh; do
  ( set -a; . ./.env.ingest; set +a
    export INGEST_DATABASE_URL="$(printf '%s' "$INGEST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
    DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.accident_facts \
      --file ~/Developer/safeascent-private/manual/$f.csv --dry-run )
done
```

Expected: a report per file with counts only. Fix every quarantined row in the private CSV (the report names row numbers and reasons, never content) until `quarantined` is empty or each remaining entry is a deliberate exclusion. Then run without `--dry-run`.

- [ ] **Step 3 (owner/agent): Re-dedupe and backfill the new cells** — `ING r3 --apply` is not rerun (new rows carry their own precision); run `ING r5 --apply --out ../data/review/duplicates.csv`, decide any new review pairs, `ING r5-import …`, `ING r5 --apply`. Then rerun the backfill (`--max-units 20000`) so new incident cells get their windows (completed chunks are skipped; only new bucket sets fetch).

- [ ] **Step 4 (owner/agent): Verify and report** — all `-m db` cells in `tests/verify/` pass; record the printed `feb5_jul2_2026_clean_incidents` N in the PR (spec 2a-4 acceptance). Repeat on prod. Phase 3 MVP-0 is unblocked.

---

## Self-review

- Spec coverage: R6 (Tasks 1–5, drop deferred per D9), R7 (Task 6), R11 (Tasks 7–8, 10), 2a-3 acceptance "every clean day incident has a 7-day window" (Task 5 cell), "`weather` dropped" consciously replaced by D9 and documented, 2a-4 acceptance (Task 8 cell). P2-5 budget (Task 9). Pipelines "recorded responses, rejection, idempotency" (Tasks 1, 3, 4).
- Placeholders: none beyond Console hosts and the owner's key file.
- Types: `DayRow`, `day_rows`, `upsert_rows`, `ArchiveClient.fetch`, `Chunk.units` are used with the same signatures in Tasks 3–5 and in plans 5 and 7.
