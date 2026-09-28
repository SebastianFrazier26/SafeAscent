# Phase 2b Exposure v1 and Prediction Compaction (PRs 2b-4, 2b-5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (2b-4) Build `exposure_index` v1: per area × type group × month, the composite proxy of partial climbing-traffic signals (route density, season share, NPS visits, permits, objective popularity, OpenBeta/in-app ticks as missing, and the optional, removable internal MP tick components), each stored raw with a missing flag, plus an unfitted exposure with ×/÷3 (×/÷1.5 with permits) bounds and a Spearman validation report. (2b-5) Compact `historical_predictions` from ~3.37 GB to a monthly score-array archive without changing the nightly cadence, verified row-for-row, with the trends endpoint reading both.

**Architecture:** `app/pipelines/exposure.py` holds pure component math (season-share rules, Spearman, bounds, proxy assembly) and one set-based job per component, assembled into a new `as_of` snapshot; the last three snapshots are kept. MP components run only when `EXPOSURE_ENABLE_MP_TICKS` is true and are missing-flagged otherwise, which is the removal drill. `app/pipelines/prediction_archive.py` folds rows older than 7 days into `prediction_archive_v1(route_id, month_start, scores smallint[31])` inside one transaction per month, verifying every folded row before deleting it `[assumes D18]`.

**Tech Stack:** Python 3.12, numpy, SQLAlchemy async, Alembic, PostGIS/ltree, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` §Exposure (P2-9), §MP ice/mixed tick aggregates (P2-14), §Storage (P2-8), milestones 2b-4, 2b-5; P3:152 (`exposure_index` shape), P3 M5. Decisions D17, D18 in `2026-09-28-phase2a-foundations.md`.

**Prerequisites:** Plans 4 (catalog), 5 (features, normals), 6 (objectives, permits), 7 (3-year daily history) merged and applied; plan 1's tick aggregates loaded.

**Two PRs:** 2b-4 = Tasks 1–6 + runbook Task 10 (`feat/p2b-exposure`); 2b-5 = Tasks 7–9 + runbook Task 11 (`feat/p2b-prediction-compaction`).

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- `exposure_index.proxy` is never serialized by any API endpoint (a test greps `app/api/`).
- Every component is stored raw with `missing: true|false` and, when missing, a `reason`; nothing missing is written as 0.
- MP components (`mp_tick_count`, `mp_ice_mixed_ticks`) are internal-only covariates, computed only when `EXPOSURE_ENABLE_MP_TICKS=true` (default true); with the flag false, or the tables dropped, they are missing-flagged and everything else is unchanged.
- The validation Spearman correlations are reported, never tuned to.
- Compaction never loses a prediction: a month is deleted from `historical_predictions` only after every one of its rows is verified in the archive in the same transaction; `app` stays SELECT-only except `historical_predictions`.

## Decisions this plan makes where the spec is silent (owner may overrule)

1. **Scope of rows:** areas with at least one scorable route attached directly (crags) plus every objective's area; the five scored type groups present there; 12 months.
2. **Season share rules** (from the rolling 3-year daily history of the area's bucket, averaged per calendar month): rock (sport/trad) day climbable when tmax 5–32 °C and precip < 2 mm; ice/mixed when tmax < 0 °C on ≥7 of the prior 10 days; alpine when either window holds (the spec says "both snow and dry-day windows"; read as the union of the two).
3. **Unfitted exposure point** for the bounds: `route_density × max(season_share, 0.05)`; Phase 3 fits the real combination (M5), this number only anchors `exposure_lo`/`exposure_hi`.
4. **NPS visits:** a hand-curated list of climbing parks (`data/curated/nps_units.csv`: unit code, centre, radius) and monthly recreation visits entered from NPS IRMA Stats (`data/curated/nps_visits.csv`, public domain), attached to areas within the park radius, month-of-year mean over available years.
5. **Snapshots:** exposure is recomputed monthly (and on demand), and only the three most recent `as_of` snapshots are kept (storage).
6. **Archive encoding:** `scores[day]` holds the rounded 0–100 score, `-1` an insufficient (gray) day, NULL no prediction; archived days display at integer precision.

## Review Focus

1. **Exposure recomputed with `EXPOSURE_ENABLE_MP_TICKS=false`** — expect both MP keys present with `missing: true, reason: "disabled"` and every other component byte-identical to the enabled run (Task 5 `test_removal_drill_changes_only_mp_components`).
2. **A route whose monthly MP ticks hit the 16-per-route cap** — expect the component flagged `censored: true`, not treated as an exact count (Task 3 `test_capped_routes_are_flagged_censored`).
3. **Folding a month that holds a gray (NULL score) day** — expect `-1` in the slot and the trends endpoint to show that day as insufficient, never 0 (Task 8 `test_gray_day_round_trips_as_insufficient`).
4. **A fold interrupted after insert but before delete** — expect a rerun to finish without duplicating or losing rows (Task 8 `test_rerun_after_partial_fold_is_idempotent`).
5. **The trends endpoint for a window spanning archive and recent rows** — expect one entry per day, recent rows winning on overlap (Task 9 `test_trends_merge_archive_and_recent_without_duplicates`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/alembic/versions/0014_exposure.py` | Create | `exposure_index`, `nps_units`, `nps_monthly_visits`. |
| `backend/alembic/versions/0015_prediction_archive.py` | Create | `prediction_archive_v1`. |
| `backend/app/models/exposure.py` | Create | Models. |
| `backend/app/config.py`, `.env.example` | Modify | `EXPOSURE_ENABLE_MP_TICKS`. |
| `backend/app/pipelines/exposure.py` | Create | Components, assembly, snapshot job, validation. |
| `backend/app/pipelines/curated.py` | Modify | `nps_units` / `nps_visits` loaders. |
| `backend/app/pipelines/prediction_archive.py` | Create | Fold, verify, delete; CLI. |
| `backend/app/api/v1/mp_routes.py` | Modify | Trends read archive + recent. |
| `data/curated/nps_units.csv`, `data/curated/nps_visits.csv` | Create | Headers (owner fills). |
| tests: `test_migration_0014.py`, `test_exposure.py`, `test_exposure_job.py`, `test_no_proxy_in_api.py`, `test_migration_0015.py`, `test_prediction_archive.py`, `test_historical_trends_archive.py` | Create | Tests. |
| `backend/tests/verify/test_phase2b_exposure_storage.py` | Create | `-m db` acceptance. |
| `.github/workflows/data-weekly.yml`, `data-daily.yml` | Modify | Monthly exposure; daily fold. |
| grants/verify SQL, `pyproject.toml`, docs | Modify | Grants, mypy, docs. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 2–6 | `exposure_index`, NPS tables (0014) | Task 1 first. |
| 2 | 3–5 | `season_share`, `spearman`, `bounds`, `component`, `PROXY_VERSION` | Pure; frozen in Task 2. |
| 3 | 4, 5 | `async component_rows(conn, name, *, settings_flag) -> dict[Key, Component]` | Frozen in Task 3. |
| 6 | plan 6 | `curated.py` gains NPS loaders | Additive functions + CLI choices. |
| 7 | 8, 9 | `prediction_archive_v1` (0015) | Task 7 first. |
| 8 | 9 | `ARCHIVE_GRAY = -1`, `FOLD_AFTER_DAYS = 7` | Frozen in Task 8. |
| 9 | existing trends tests | `app/api/v1/mp_routes.py` historical query | Task 9 keeps the response shape; new test covers the merge. |
| 1, 7 | each other, plans 1–7 | grants/verify SQL; `verify_roles.sql` (app SELECT-only) unchanged | Append-only. |

---

# PR 2b-4 — `feat/p2b-exposure`

### Task 1: Migration `0014`, models, setting

**Files:**
- Create: `backend/alembic/versions/0014_exposure.py`, `backend/app/models/exposure.py`, `backend/tests/test_migration_0014.py`
- Modify: `backend/app/models/__init__.py`, `backend/app/config.py`, `.env.example`, `backend/pyproject.toml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Produces (DB): `exposure_index(area_id uuid REFERENCES canonical_areas, type_group text CHECK IN ('sport','trad','alpine','ice','mixed'), month smallint CHECK 1..12, n_routes integer NOT NULL, proxy jsonb NOT NULL, proxy_version text NOT NULL, as_of date NOT NULL, exposure_point real, exposure_lo real, exposure_hi real, PK (area_id, type_group, month, as_of))`; `nps_units(unit_code text PK, name text NOT NULL, lat double precision NOT NULL, lon double precision NOT NULL, radius_km real NOT NULL CHECK > 0)`; `nps_monthly_visits(unit_code text REFERENCES nps_units, year smallint, month smallint CHECK 1..12, visits integer NOT NULL CHECK >= 0, source_url text NOT NULL, PK (unit_code, year, month))`.
- Produces (Python): `Settings.EXPOSURE_ENABLE_MP_TICKS: bool = True`.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0014.py`:

```python
import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg


def test_0014_checks_clean_and_rejects_bad_months():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))
        run_sql(name, "INSERT INTO nps_units VALUES ('YOSE', 'Yosemite', 37.75, -119.6, 25)")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO nps_monthly_visits VALUES ('YOSE', 2025, 13, 1, 'https://irma.nps.gov/Stats/')")
```

- [ ] **Step 2: Implement** `backend/alembic/versions/0014_exposure.py`:

```python
"""Exposure v1 (P2-9): exposure_index (P3:152 + bounds) and the curated NPS visitation tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_exposure"
down_revision = "0013_live_feeds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exposure_index",
        sa.Column("area_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("canonical_areas.area_id"), nullable=False),
        sa.Column("type_group", sa.Text(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("n_routes", sa.Integer(), nullable=False),
        sa.Column("proxy", postgresql.JSONB(), nullable=False),
        sa.Column("proxy_version", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("exposure_point", sa.REAL(), nullable=True),
        sa.Column("exposure_lo", sa.REAL(), nullable=True),
        sa.Column("exposure_hi", sa.REAL(), nullable=True),
        sa.PrimaryKeyConstraint("area_id", "type_group", "month", "as_of"),
        sa.CheckConstraint("type_group IN ('sport', 'trad', 'alpine', 'ice', 'mixed')", name="exposure_index_type_group_check"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="exposure_index_month_check"),
    )
    op.create_table(
        "nps_units",
        sa.Column("unit_code", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("radius_km", sa.REAL(), nullable=False),
        sa.CheckConstraint("radius_km > 0", name="nps_units_radius_check"),
    )
    op.create_table(
        "nps_monthly_visits",
        sa.Column("unit_code", sa.Text(), sa.ForeignKey("nps_units.unit_code"), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("visits", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("unit_code", "year", "month"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="nps_monthly_visits_month_check"),
        sa.CheckConstraint("visits >= 0", name="nps_monthly_visits_visits_check"),
    )


def downgrade() -> None:
    op.drop_table("nps_monthly_visits")
    op.drop_table("nps_units")
    op.drop_table("exposure_index")
```

Models in `backend/app/models/exposure.py` (`ExposureIndex`, `NpsUnit`, `NpsMonthlyVisits`) mirror it; register; models mypy block. `config.py`: `EXPOSURE_ENABLE_MP_TICKS: bool = True` with comment `# P2-9: false drops the internal MP tick components from exposure (the removal drill).`; `.env.example`: `EXPOSURE_ENABLE_MP_TICKS=true`.

Grants ("Plan 8 (0014)"): `GRANT SELECT, INSERT, UPDATE, DELETE ON public.exposure_index TO ingest; GRANT SELECT, INSERT, UPDATE ON public.nps_units, public.nps_monthly_visits TO ingest; GRANT SELECT ON public.exposure_index TO trainer; GRANT SELECT ON internal.mp_tick_aggregates TO ingest;` (the last is already present from plan 1; keep one copy). `verify_roles_phase2.sql`: matching `ingest_writes` rows.

- [ ] **Step 3: Run** — PASS. **Step 4: Commit** — `git add backend/alembic/versions/0014_exposure.py backend/app/models/ backend/app/config.py .env.example backend/tests/test_migration_0014.py backend/pyproject.toml backend/db/roles/ && git commit -m "feat(db): 0014 exposure_index and curated NPS visitation tables"`

---

### Task 2: Pure exposure math

**Files:**
- Create: `backend/app/pipelines/exposure.py` (pure part), `backend/tests/test_exposure.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `PROXY_VERSION = "x-v1"`, `COMPONENTS = ("route_density", "season_share", "ob_ticks", "nps_visits", "permit_attempts", "objective_popularity", "in_app_ticks", "mp_tick_count", "mp_ice_mixed_ticks")`, `MP_COMPONENTS = ("mp_tick_count", "mp_ice_mixed_ticks")`, `@dataclass(frozen=True) Component(value: float | None, missing: bool, reason: str | None = None, censored: bool = False)` with `present(v, *, censored=False)` and `absent(reason)` constructors and `to_json() -> dict[str, object]`, `rock_day(tmax, precip) -> bool | None`, `ice_day(prior10_tmax: Sequence[float | None]) -> bool | None`, `season_share(days: Sequence[tuple[date, float | None, float | None]], type_group: str) -> dict[int, float | None]` (month → share), `spearman(x: Sequence[float], y: Sequence[float]) -> float | None`, `bounds(point: float | None, *, has_permits: bool) -> tuple[float | None, float | None]`, `exposure_point(route_density: float, season: float | None) -> float`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_exposure.py`:

```python
from datetime import date, timedelta

import pytest

from app.pipelines.exposure import Component, bounds, exposure_point, ice_day, rock_day, season_share, spearman


def test_rock_and_ice_day_rules():
    assert rock_day(20.0, 0.0) is True and rock_day(40.0, 0.0) is False and rock_day(20.0, 5.0) is False
    assert rock_day(None, 0.0) is None
    assert ice_day([-5.0] * 7 + [2.0] * 3) is True and ice_day([-5.0] * 6 + [2.0] * 4) is False
    assert ice_day([None] * 10) is None


def _days(tmax: float, precip: float, n: int = 400):
    start = date(2024, 1, 1)
    return [(start + timedelta(d), tmax, precip) for d in range(n)]


def test_season_share_by_type_group():
    rock = season_share(_days(20.0, 0.0), "trad")
    assert rock[1] == 1.0 and rock[7] == 1.0
    ice = season_share(_days(-5.0, 0.0), "ice")
    assert ice[1] == 1.0
    alpine = season_share(_days(-5.0, 0.0), "alpine")
    assert alpine[1] == 1.0
    assert season_share([], "trad")[1] is None


def test_component_json_and_missing():
    assert Component.present(3.0).to_json() == {"value": 3.0, "missing": False}
    assert Component.present(16.0, censored=True).to_json() == {"value": 16.0, "missing": False, "censored": True}
    assert Component.absent("disabled").to_json() == {"value": None, "missing": True, "reason": "disabled"}


def test_bounds_and_point():
    assert bounds(9.0, has_permits=False) == (3.0, 27.0)
    assert bounds(9.0, has_permits=True) == pytest.approx((6.0, 13.5))
    assert bounds(None, has_permits=False) == (None, None)
    assert exposure_point(10.0, 0.0) == 0.5


def test_spearman_handles_ties_and_degenerate_input():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 2, 3], [3, 2, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None
```

- [ ] **Step 2: Implement** `backend/app/pipelines/exposure.py` (pure part):

```python
"""Exposure v1 (P2-9): partial signals of climbing traffic per area × type group × month,
each stored raw with a missing flag; Phase 3 fits their weights (M5). The unfitted point
estimate here only anchors the ×/÷3 (×/÷1.5 with permits) bounds."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

PROXY_VERSION = "x-v1"
COMPONENTS = ("route_density", "season_share", "ob_ticks", "nps_visits", "permit_attempts",
              "objective_popularity", "in_app_ticks", "mp_tick_count", "mp_ice_mixed_ticks")
MP_COMPONENTS = ("mp_tick_count", "mp_ice_mixed_ticks")
ROCK_TMAX = (5.0, 32.0)
ROCK_MAX_PRECIP = 2.0
ICE_WINDOW, ICE_MIN_FROZEN = 10, 7
MIN_SEASON = 0.05


@dataclass(frozen=True)
class Component:
    value: float | None
    missing: bool
    reason: str | None = None
    censored: bool = False

    @classmethod
    def present(cls, value: float, *, censored: bool = False) -> Component:
        return cls(float(value), False, None, censored)

    @classmethod
    def absent(cls, reason: str) -> Component:
        return cls(None, True, reason)

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"value": self.value, "missing": self.missing}
        if self.reason:
            out["reason"] = self.reason
        if self.censored:
            out["censored"] = True
        return out


def rock_day(tmax: float | None, precip: float | None) -> bool | None:
    if tmax is None or precip is None:
        return None
    return ROCK_TMAX[0] <= tmax <= ROCK_TMAX[1] and precip < ROCK_MAX_PRECIP


def ice_day(prior10_tmax: Sequence[float | None]) -> bool | None:
    known = [t for t in prior10_tmax if t is not None]
    if len(known) < ICE_MIN_FROZEN:
        return None
    return sum(1 for t in known if t < 0) >= ICE_MIN_FROZEN


def season_share(days: Sequence[tuple[date, float | None, float | None]], type_group: str) -> dict[int, float | None]:
    ordered = sorted(days)
    tmax_series = [t for _, t, _ in ordered]
    per_month: dict[int, list[bool]] = defaultdict(list)
    for i, (d, tmax, precip) in enumerate(ordered):
        rock = rock_day(tmax, precip)
        ice = ice_day(tmax_series[max(0, i - ICE_WINDOW + 1) : i + 1]) if i >= ICE_WINDOW - 1 else None
        if type_group in ("sport", "trad"):
            ok = rock
        elif type_group in ("ice", "mixed"):
            ok = ice
        else:
            ok = None if rock is None and ice is None else bool(rock) or bool(ice)
        if ok is not None:
            per_month[d.month].append(ok)
    return {m: (sum(per_month[m]) / len(per_month[m]) if per_month.get(m) else None) for m in range(1, 13)}


def _ranks(values: Sequence[float]) -> np.ndarray:
    x = np.asarray(values, dtype=np.float64)
    order = x.argsort(kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x), dtype=np.float64)
    for v in np.unique(x):
        tied = x == v
        ranks[tied] = ranks[tied].mean()
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def exposure_point(route_density: float, season: float | None) -> float:
    return route_density * max(season if season is not None else MIN_SEASON, MIN_SEASON)


def bounds(point: float | None, *, has_permits: bool) -> tuple[float | None, float | None]:
    if point is None or not math.isfinite(point):
        return None, None
    factor = 1.5 if has_permits else 3.0
    return point / factor, point * factor
```

Append `"app.pipelines.exposure"` to strict mypy.

- [ ] **Step 3: Run** — PASS. **Step 4: Commit** — `git add backend/app/pipelines/exposure.py backend/tests/test_exposure.py backend/pyproject.toml && git commit -m "feat(pipelines): exposure v1 component math, bounds, Spearman"`

---

### Task 3: Component queries

**Files:**
- Modify: `backend/app/pipelines/exposure.py`
- Create: `backend/tests/test_exposure_job.py`

**Interfaces:**
- Produces: `Key = tuple[uuid.UUID, str, int]` (area, type group, month), `async scope(conn) -> dict[tuple[uuid.UUID, str], int]` (area × type group → scorable routes in subtree; Decision 1), `async route_density(conn, scope) -> dict[Key, Component]`, `async season(conn, scope, *, today: date) -> dict[Key, Component]`, `async nps_visits(conn, scope) -> dict[Key, Component]`, `async permits(conn, scope) -> dict[Key, Component]`, `async popularity(conn, scope) -> dict[Key, Component]`, `async mp_tick_count(conn, scope, *, enabled: bool) -> dict[Key, Component]`, `async mp_ice_mixed_ticks(conn, scope, *, enabled: bool) -> dict[Key, Component]`; `ob_ticks` → `Component.absent("no_bulk_source")` everywhere `[assumes D17]`; `in_app_ticks` → `Component.absent("phase4")`.

Component definitions:
- `route_density`: scorable routes of the type group in the area's subtree (`path <@`); always present.
- `season_share`: `season_share()` over the area bucket's `cell_daily_conditions` for the last 3 years (archive rows only); missing `no_history` when no month value.
- `nps_visits`: mean monthly visits over available years for units whose centre is within `radius_km` of the area point; missing `outside_parks`.
- `permit_attempts`: for objective areas, monthly attempts (month 0 rows spread evenly as `attempts/12`); missing `no_permits`.
- `objective_popularity`: objective `sitelinks`; missing `no_wikidata` / `not_objective`.
- `mp_tick_count`: clean rock ticks (`quarantine_reason IS NULL`) per area via `internal.mp_area_links` and `mp_routes.location_id`, month of year, only `sport`/`trad` rows, only areas in CA/NV (the tick sample's coverage, by `ST_Within` of the state bounding boxes CA 32.5–42.0 N, 124.5–114.1 W; NV 35.0–42.0 N, 120.0–114.0 W); `censored` when any contributing route has ≥16 ticks in total; missing `outside_ca_nv` elsewhere, `disabled` when the flag is off, `table_dropped` when `internal.mp_ticks` does not exist.
- `mp_ice_mixed_ticks`: `internal.mp_tick_aggregates` monthly rows summed by month of year over years, joined to canonical routes through `internal.mp_route_links`, only `ice`/`mixed`; missing `no_ticks`, `disabled`, or `table_dropped` (`to_regclass('internal.mp_tick_aggregates') IS NULL`).

- [ ] **Step 1: Failing test** — `backend/tests/test_exposure_job.py`:

```python
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.exposure import mp_ice_mixed_ticks, mp_tick_count, route_density, scope
from tests.pgtest import migrated_db, requires_pg, sa_url

AREA = "00000000-0000-0000-0000-00000000000a"
ICE = "00000000-0000-0000-0000-00000000000b"
SEED = f"""
INSERT INTO canonical_areas (area_id, name, path, lat, lon, geom, coord_precision, source, redistributable) VALUES
  ('{AREA}', 'Fixture Crag', '0000000000000000000000000000000a', 37.7, -119.6, ST_SetSRID(ST_MakePoint(-119.6, 37.7), 4326)::geography, 'area_centroid', 'openbeta', true),
  ('{ICE}', 'Fixture Ice', '0000000000000000000000000000000b', 44.1, -71.3, ST_SetSRID(ST_MakePoint(-71.3, 44.1), 4326)::geography, 'area_centroid', 'openbeta', true);
INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable) VALUES
  ('00000000-0000-0000-0000-0000000000c1', '{AREA}', 'R1', '{{trad}}', 'trad', 'rt-v1', false, true, 'openbeta', true),
  ('00000000-0000-0000-0000-0000000000c2', '{ICE}', 'I1', '{{ice}}', 'ice', 'rt-v1', false, true, 'mp_facts', false);
INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (900000101, 'Fixture Crag', 37.7, -119.6);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES (900000001, 'R1', 900000101, 'Trad');
INSERT INTO internal.mp_area_links VALUES ('{AREA}', 900000101, 1.0, 'auto');
INSERT INTO internal.mp_route_links VALUES ('00000000-0000-0000-0000-0000000000c2', 900000002, 1.0, 'mp_facts');
INSERT INTO internal.mp_ticks (tick_id, route_id, climber_name, tick_date)
  SELECT g, '900000001', 'c', DATE '2025-07-01' + g FROM generate_series(1, 16) g;
INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) VALUES
  (900000002, '2025-01', 'lead', 4), (900000002, '2024-01', 'lead', 2), (900000002, 'total', 'all', 6);
"""


def _go(name, fn):
    async def go():
        engine = create_async_engine(sa_url(name))
        try:
            async with engine.connect() as conn:
                return await fn(conn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


@requires_pg
def test_capped_routes_are_flagged_censored():
    with migrated_db(seed_sql=SEED) as name:
        async def run(conn):
            s = await scope(conn)
            return await mp_tick_count(conn, s, enabled=True)

        comps = _go(name, run)
        july = comps[(uuid.UUID(AREA), "trad", 7)]
        assert july.value == 16.0 and july.censored is True


@requires_pg
def test_ice_aggregates_and_disabled_flag():
    with migrated_db(seed_sql=SEED) as name:
        async def run(conn):
            s = await scope(conn)
            return (await route_density(conn, s), await mp_ice_mixed_ticks(conn, s, enabled=True),
                    await mp_ice_mixed_ticks(conn, s, enabled=False))

        density, on, off = _go(name, run)
        assert density[(uuid.UUID(ICE), "ice", 1)].value == 1.0
        assert on[(uuid.UUID(ICE), "ice", 1)].value == 3.0
        assert off[(uuid.UUID(ICE), "ice", 1)].to_json() == {"value": None, "missing": True, "reason": "disabled"}
```

(The ice value is the mean over the two years with January ticks, (4+2)/2 = 3; the MP rock ticks sit in July 2025, 16 of them on one route, which hits the 16-per-route cap.)

- [ ] **Step 2: Implement** — append to `exposure.py` (add `import uuid`, `from sqlalchemy import text`, `from sqlalchemy.ext.asyncio import AsyncConnection`, `from datetime import timedelta` at the top):

```python
Key = tuple[uuid.UUID, str, int]
TYPE_GROUPS = ("sport", "trad", "alpine", "ice", "mixed")
TICK_CAP = 16
CA_NV = ("ST_MakeEnvelope(-124.5, 32.5, -114.1, 42.0, 4326)", "ST_MakeEnvelope(-120.0, 35.0, -114.0, 42.0, 4326)")


def _months(scope_: dict[tuple[uuid.UUID, str], int]) -> list[Key]:
    return [(a, tg, m) for (a, tg) in scope_ for m in range(1, 13)]


async def scope(conn: AsyncConnection) -> dict[tuple[uuid.UUID, str], int]:
    rows = (await conn.execute(text(
        "SELECT a.area_id, s.type_group, count(*) FROM canonical_areas a "
        "JOIN canonical_areas d ON d.path <@ a.path JOIN scorable_routes s ON s.area_id = d.area_id "
        "WHERE a.retired_at IS NULL AND (a.objective_id IS NOT NULL OR EXISTS "
        " (SELECT 1 FROM canonical_routes r WHERE r.area_id = a.area_id AND r.retired_at IS NULL)) "
        "GROUP BY 1, 2"))).all()
    return {(uuid.UUID(str(a)), str(tg)): int(n) for a, tg, n in rows}


async def route_density(conn: AsyncConnection, scope_: dict[tuple[uuid.UUID, str], int]) -> dict[Key, Component]:
    return {(a, tg, m): Component.present(scope_[(a, tg)]) for a, tg, m in _months(scope_)}


async def season(conn: AsyncConnection, scope_: dict[tuple[uuid.UUID, str], int], *, today: date) -> dict[Key, Component]:
    from app.pipelines.grid import grid_bucket

    points = {uuid.UUID(str(a)): (float(la), float(lo)) for a, la, lo in (await conn.execute(text(
        "SELECT area_id, lat, lon FROM canonical_areas WHERE lat IS NOT NULL"))).all()}
    buckets = {a: grid_bucket(*points[a]) for a, _ in scope_ if a in points}
    since = today - timedelta(days=3 * 365)
    history: dict[int, list[tuple[date, float | None, float | None]]] = defaultdict(list)
    for b, d, tmax, precip in (await conn.execute(text(
            "SELECT grid_bucket, date, tmax, precip_mm FROM cell_daily_conditions WHERE NOT is_forecast "
            "AND date >= :since AND grid_bucket = ANY(:b)"), {"since": since, "b": sorted(set(buckets.values()))})).all():
        history[int(b)].append((d, float(tmax) if tmax is not None else None, float(precip) if precip is not None else None))
    out: dict[Key, Component] = {}
    for a, tg in scope_:
        shares = season_share(history.get(buckets.get(a, -1), []), tg)
        for m in range(1, 13):
            v = shares[m]
            out[(a, tg, m)] = Component.present(v) if v is not None else Component.absent("no_history")
    return out


async def nps_visits(conn: AsyncConnection, scope_: dict[tuple[uuid.UUID, str], int]) -> dict[Key, Component]:
    rows = (await conn.execute(text(
        "SELECT a.area_id, v.month, avg(v.visits) FROM canonical_areas a JOIN nps_units u "
        "ON a.geom IS NOT NULL AND ST_DWithin(a.geom, ST_SetSRID(ST_MakePoint(u.lon, u.lat), 4326)::geography, u.radius_km * 1000) "
        "JOIN nps_monthly_visits v ON v.unit_code = u.unit_code GROUP BY 1, 2"))).all()
    visits = {(uuid.UUID(str(a)), int(m)): float(n) for a, m, n in rows}
    return {(a, tg, m): (Component.present(visits[(a, m)]) if (a, m) in visits else Component.absent("outside_parks"))
            for a, tg, m in _months(scope_)}


async def permits(conn: AsyncConnection, scope_: dict[tuple[uuid.UUID, str], int]) -> dict[Key, Component]:
    rows = (await conn.execute(text(
        "SELECT a.area_id, p.month, avg(p.attempts) FROM canonical_areas a JOIN objective_permit_counts p "
        "ON p.objective_id = a.objective_id WHERE p.attempts IS NOT NULL GROUP BY 1, 2"))).all()
    monthly: dict[tuple[uuid.UUID, int], float] = {}
    for a, m, n in rows:
        area = uuid.UUID(str(a))
        if int(m) == 0:
            for mm in range(1, 13):
                monthly.setdefault((area, mm), float(n) / 12)
        else:
            monthly[(area, int(m))] = float(n)
    return {(a, tg, m): (Component.present(monthly[(a, m)]) if (a, m) in monthly else Component.absent("no_permits"))
            for a, tg, m in _months(scope_)}


async def popularity(conn: AsyncConnection, scope_: dict[tuple[uuid.UUID, str], int]) -> dict[Key, Component]:
    rows = (await conn.execute(text(
        "SELECT a.area_id, o.sitelinks FROM canonical_areas a JOIN objectives o ON o.objective_id = a.objective_id"))).all()
    links = {uuid.UUID(str(a)): s for a, s in rows}
    out: dict[Key, Component] = {}
    for a, tg, m in _months(scope_):
        s = links.get(a, "not_objective")
        out[(a, tg, m)] = (Component.absent("not_objective") if s == "not_objective"
                           else Component.absent("no_wikidata") if s is None else Component.present(float(s)))
    return out


async def _exists(conn: AsyncConnection, name: str) -> bool:
    return (await conn.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name})).scalar() is True


async def mp_tick_count(conn: AsyncConnection, scope_: dict[tuple[uuid.UUID, str], int], *, enabled: bool) -> dict[Key, Component]:
    keys = [k for k in _months(scope_) if k[1] in ("sport", "trad")]
    if not enabled:
        return {k: Component.absent("disabled") for k in keys}
    if not await _exists(conn, "internal.mp_ticks"):
        return {k: Component.absent("table_dropped") for k in keys}
    in_scope = {uuid.UUID(str(a)) for (a,) in (await conn.execute(text(
        f"SELECT area_id FROM canonical_areas WHERE geom IS NOT NULL AND (ST_Within(geom::geometry, {CA_NV[0]}) "
        f"OR ST_Within(geom::geometry, {CA_NV[1]}))"))).all()}
    rows = (await conn.execute(text(
        "SELECT l.area_id, extract(month FROM t.tick_date)::int AS m, count(*) AS n, "
        " bool_or(per_route.total >= :cap) AS capped "
        "FROM internal.mp_ticks t JOIN mp_routes r ON t.route_id ~ '^[0-9]{1,18}$' AND r.mp_route_id = t.route_id::bigint "
        "JOIN internal.mp_area_links l ON l.mp_location_id = r.location_id "
        "JOIN (SELECT route_id, count(*) AS total FROM internal.mp_ticks WHERE quarantine_reason IS NULL GROUP BY route_id) per_route "
        " ON per_route.route_id = t.route_id "
        "WHERE t.quarantine_reason IS NULL AND t.tick_date IS NOT NULL GROUP BY 1, 2"), {"cap": TICK_CAP})).all()
    counts = {(uuid.UUID(str(a)), int(m)): (float(n), bool(c)) for a, m, n, c in rows}
    out: dict[Key, Component] = {}
    for a, tg, m in keys:
        if a not in in_scope:
            out[(a, tg, m)] = Component.absent("outside_ca_nv")
        else:
            n, capped = counts.get((a, m), (0.0, False))
            out[(a, tg, m)] = Component.present(n, censored=capped)
    return out


async def mp_ice_mixed_ticks(conn: AsyncConnection, scope_: dict[tuple[uuid.UUID, str], int], *, enabled: bool) -> dict[Key, Component]:
    keys = [k for k in _months(scope_) if k[1] in ("ice", "mixed")]
    if not enabled:
        return {k: Component.absent("disabled") for k in keys}
    if not await _exists(conn, "internal.mp_tick_aggregates"):
        return {k: Component.absent("table_dropped") for k in keys}
    rows = (await conn.execute(text(
        "SELECT c.area_id, c.type_group, substr(g.period, 6, 2)::int AS m, "
        " sum(g.tick_count)::float / count(DISTINCT substr(g.period, 1, 4)) AS mean_per_year "
        "FROM internal.mp_tick_aggregates g JOIN internal.mp_route_links l ON l.mp_route_id = g.mp_route_id "
        "JOIN canonical_routes c ON c.route_id = l.route_id "
        "WHERE g.period <> 'total' AND c.type_group IN ('ice', 'mixed') GROUP BY 1, 2, 3"))).all()
    values = {(uuid.UUID(str(a)), str(tg), int(m)): float(v) for a, tg, m, v in rows}
    return {k: (Component.present(values[k]) if k in values else Component.absent("no_ticks")) for k in keys}
```

`in_scope` for the CA/NV test uses the area's point; the 16-tick cap check counts clean ticks per route across all time, which is how the MP sample was censored.

- [ ] **Step 3: Run** — PASS. **Step 4: Commit** — `git add backend/app/pipelines/exposure.py backend/tests/test_exposure_job.py && git commit -m "feat(pipelines): exposure components incl. removable internal MP tick components"`

---

### Task 4: Snapshot job and validation report

**Files:**
- Modify: `backend/app/pipelines/exposure.py`, `backend/tests/test_exposure_job.py`

**Interfaces:**
- Produces: `KEEP_SNAPSHOTS = 3`, `async build(conn, *, today: date, mp_enabled: bool) -> dict[str, object]` (assembles every component per key into `proxy` JSON with all nine component names, writes rows with `as_of = today`, `exposure_point`, bounds with `has_permits = not proxy['permit_attempts'].missing`, deletes snapshots beyond the newest three), `async validate(conn, *, as_of: date) -> dict[str, float | None]` (Spearman of `exposure_point` vs permit attempts over objective rows; vs `mp_tick_count` over CA/NV rows where not missing), CLI `python -m app.pipelines.exposure` (reads `settings.EXPOSURE_ENABLE_MP_TICKS`, logs `source='exposure'` with the validation numbers in `validation_report`).

- [ ] **Step 1: Failing test** — append to `test_exposure_job.py`:

```python
from datetime import date

from sqlalchemy import text

from app.pipelines.exposure import build


@requires_pg
def test_snapshot_has_every_component_and_bounds():
    with migrated_db(seed_sql=SEED) as name:
        async def run(conn):
            await build(conn, today=date(2026, 9, 28), mp_enabled=True)
            return (await conn.execute(text(
                "SELECT proxy, exposure_point, exposure_lo, exposure_hi FROM exposure_index "
                "WHERE type_group = 'trad' AND month = 7"))).one()

        async def go():
            engine = create_async_engine(sa_url(name))
            try:
                async with engine.begin() as conn:
                    return await run(conn)
            finally:
                await engine.dispose()

        proxy, point, lo, hi = asyncio.run(go())
    assert set(proxy) == {"route_density", "season_share", "ob_ticks", "nps_visits", "permit_attempts",
                          "objective_popularity", "in_app_ticks", "mp_tick_count", "mp_ice_mixed_ticks"}
    assert proxy["ob_ticks"] == {"value": None, "missing": True, "reason": "no_bulk_source"}
    assert (lo, hi) == (point / 3, point * 3)
```

- [ ] **Step 2: Implement** — append to `exposure.py` (imports `json` at the top):

```python
KEEP_SNAPSHOTS = 3


def _not_applicable(key: Key, name: str) -> Component:
    return Component.absent("not_applicable_type_group") if name in MP_COMPONENTS else Component.absent("missing")


async def build(conn: AsyncConnection, *, today: date, mp_enabled: bool) -> dict[str, object]:
    s = await scope(conn)
    parts: dict[str, dict[Key, Component]] = {
        "route_density": await route_density(conn, s),
        "season_share": await season(conn, s, today=today),
        "nps_visits": await nps_visits(conn, s),
        "permit_attempts": await permits(conn, s),
        "objective_popularity": await popularity(conn, s),
        "mp_tick_count": await mp_tick_count(conn, s, enabled=mp_enabled),
        "mp_ice_mixed_ticks": await mp_ice_mixed_ticks(conn, s, enabled=mp_enabled),
    }
    rows = []
    for key in _months(s):
        area, tg, month = key
        proxy = {name: (parts[name].get(key) or _not_applicable(key, name)).to_json() for name in parts}
        proxy["ob_ticks"] = Component.absent("no_bulk_source").to_json()
        proxy["in_app_ticks"] = Component.absent("phase4").to_json()
        season_v = proxy["season_share"]["value"]
        point = exposure_point(float(s[(area, tg)]), float(season_v) if isinstance(season_v, (int, float)) else None)
        lo, hi = bounds(point, has_permits=not proxy["permit_attempts"]["missing"])
        rows.append({"a": area, "tg": tg, "m": month, "n": s[(area, tg)], "p": json.dumps(proxy, sort_keys=True),
                     "v": PROXY_VERSION, "d": today, "pt": point, "lo": lo, "hi": hi})
    if rows:
        await conn.execute(text(
            "INSERT INTO exposure_index (area_id, type_group, month, n_routes, proxy, proxy_version, as_of, exposure_point, "
            "exposure_lo, exposure_hi) VALUES (:a, :tg, :m, :n, CAST(:p AS jsonb), :v, :d, :pt, :lo, :hi) "
            "ON CONFLICT (area_id, type_group, month, as_of) DO UPDATE SET n_routes = EXCLUDED.n_routes, proxy = EXCLUDED.proxy, "
            "proxy_version = EXCLUDED.proxy_version, exposure_point = EXCLUDED.exposure_point, exposure_lo = EXCLUDED.exposure_lo, "
            "exposure_hi = EXCLUDED.exposure_hi"), rows)
    await conn.execute(text(
        "DELETE FROM exposure_index WHERE as_of NOT IN (SELECT DISTINCT as_of FROM exposure_index ORDER BY as_of DESC LIMIT :k)"),
        {"k": KEEP_SNAPSHOTS})
    return {"rows": len(rows), "as_of": today.isoformat(), "mp_enabled": mp_enabled}


async def validate(conn: AsyncConnection, *, as_of: date) -> dict[str, float | None]:
    rows = (await conn.execute(text(
        "SELECT exposure_point, (proxy->'permit_attempts'->>'value')::float, (proxy->'mp_tick_count'->>'value')::float "
        "FROM exposure_index WHERE as_of = :d"), {"d": as_of})).all()
    permit_pairs = [(float(p), float(a)) for p, a, _ in rows if p is not None and a is not None]
    tick_pairs = [(float(p), float(t)) for p, _, t in rows if p is not None and t is not None]
    return {
        "spearman_vs_permits": spearman([p for p, _ in permit_pairs], [a for _, a in permit_pairs]),
        "spearman_vs_mp_ticks": spearman([p for p, _ in tick_pairs], [t for _, t in tick_pairs]),
        "n_permit_rows": float(len(permit_pairs)),
        "n_tick_rows": float(len(tick_pairs)),
    }
```

The MP components exist only for their type groups (rock for `mp_tick_count`, ice/mixed for `mp_ice_mixed_ticks`); other type groups carry them as `missing: true, reason: "not_applicable_type_group"` so every row has all nine keys. The CLI calls `build` then `validate` in one transaction and records both in `source_ingest_log` (`source='exposure'`).

- [ ] **Step 3: Run** — PASS. **Step 4: Commit** — `git add backend/app/pipelines/exposure.py backend/tests/test_exposure_job.py && git commit -m "feat(pipelines): exposure snapshots with bounds and Spearman validation"`

---

### Task 5: Guards — removal drill, proxy never served

**Files:**
- Create: `backend/tests/test_no_proxy_in_api.py`
- Modify: `backend/tests/test_exposure_job.py`

- [ ] **Step 1: Tests**

`backend/tests/test_no_proxy_in_api.py`:

```python
import re
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "app" / "api"


def test_no_endpoint_reads_the_exposure_proxy():
    offenders = [str(p) for p in API.rglob("*.py") if re.search(r"exposure_index|\bproxy\b", p.read_text())]
    assert offenders == []
```

Append to `test_exposure_job.py`:

```python
@requires_pg
def test_removal_drill_changes_only_mp_components():
    with migrated_db(seed_sql=SEED) as name:
        async def snapshot(conn, enabled, day):
            await build(conn, today=day, mp_enabled=enabled)
            return {(str(a), tg, m): p for a, tg, m, p in (await conn.execute(text(
                "SELECT area_id, type_group, month, proxy FROM exposure_index WHERE as_of = :d"), {"d": day})).all()}

        async def go():
            engine = create_async_engine(sa_url(name))
            try:
                async with engine.begin() as conn:
                    return await snapshot(conn, True, date(2026, 9, 1)), await snapshot(conn, False, date(2026, 9, 2))
            finally:
                await engine.dispose()

        on, off = asyncio.run(go())
    assert on.keys() == off.keys()
    for key in on:
        for name in on[key]:
            if name in ("mp_tick_count", "mp_ice_mixed_ticks"):
                assert off[key][name]["missing"] is True
            else:
                assert on[key][name] == off[key][name]
```

- [ ] **Step 2: Run** — PASS. **Step 3: Commit** — `git add backend/tests/test_no_proxy_in_api.py backend/tests/test_exposure_job.py && git commit -m "test: exposure removal drill and no proxy in the API"`

---

### Task 6: NPS curated loaders, workflow, PR 2b-4 docs

**Files:**
- Modify: `backend/app/pipelines/curated.py`, `backend/tests/test_curated.py`, `data/curated/README.md`, `.github/workflows/data-weekly.yml`, `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`
- Create: `data/curated/nps_units.csv`, `data/curated/nps_visits.csv`, `backend/tests/verify/test_phase2b_exposure_storage.py`

**Interfaces:**
- Produces: `NPS_UNIT_COLUMNS = ("unit_code", "name", "lat", "lon", "radius_km")`, `NPS_VISIT_COLUMNS = ("unit_code", "year", "month", "recreation_visits", "source_url")`, `async load_nps_units(conn, path, report) -> int`, `async load_nps_visits(conn, path, report, today) -> int` (future months quarantined as in permits), CLI choices `nps-units`, `nps-visits`.

- [ ] **Step 1: Failing test** — append to `test_curated.py`:

```python
from app.pipelines.curated import NPS_UNIT_COLUMNS, NPS_VISIT_COLUMNS, load_nps_units, load_nps_visits


@requires_pg
def test_nps_loaders_validate_and_reject_future_months(tmp_path):
    units = _write(tmp_path, "u.csv", NPS_UNIT_COLUMNS, ["YOSE,Yosemite,37.75,-119.6,25", "BAD,Nowhere,95,-119.6,25"])
    visits = _write(tmp_path, "v.csv", NPS_VISIT_COLUMNS, [
        "YOSE,2025,7,600000,https://irma.nps.gov/Stats/", "YOSE,2026,10,1,https://irma.nps.gov/Stats/"])
    with migrated_db() as name:
        r1, r2 = ValidationReport("u"), ValidationReport("v")
        assert _go(name, lambda c: load_nps_units(c, units, r1)) == 1
        assert _go(name, lambda c: load_nps_visits(c, visits, r2, date(2026, 9, 28))) == 1
        assert r1.quarantined == {"out_of_range": 1} and r2.quarantined == {"future": 1}
```

- [ ] **Step 2: Implement** — append to `curated.py`:

```python
NPS_UNIT_COLUMNS = ("unit_code", "name", "lat", "lon", "radius_km")
NPS_VISIT_COLUMNS = ("unit_code", "year", "month", "recreation_visits", "source_url")


def _reader(path: Path, header: tuple[str, ...]) -> list[tuple[int, dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != header:
            raise ValueError(f"{path.name}: header must be {','.join(header)}")
        return list(enumerate(reader, start=2))


async def load_nps_units(conn: AsyncConnection, path: Path, report: ValidationReport) -> int:
    rows = []
    for number, raw in _reader(path, NPS_UNIT_COLUMNS):
        ref = f"{path.name}:{number}"
        try:
            lat, lon, radius = float(raw["lat"]), float(raw["lon"]), float(raw["radius_km"])
        except ValueError:
            report.quarantine(ref, "invalid")
            continue
        problem = coord_problem(lat, lon)
        if problem or radius <= 0 or not raw["unit_code"].isalpha():
            report.quarantine(ref, problem or "invalid")
            continue
        report.accept()
        rows.append({"u": raw["unit_code"].upper(), "n": raw["name"], "lat": lat, "lon": lon, "r": radius})
    if rows:
        await conn.execute(text(
            "INSERT INTO nps_units (unit_code, name, lat, lon, radius_km) VALUES (:u, :n, :lat, :lon, :r) "
            "ON CONFLICT (unit_code) DO UPDATE SET name = EXCLUDED.name, lat = EXCLUDED.lat, lon = EXCLUDED.lon, radius_km = EXCLUDED.radius_km"),
            rows)
    return len(rows)


async def load_nps_visits(conn: AsyncConnection, path: Path, report: ValidationReport, today: date) -> int:
    units = {str(u) for (u,) in (await conn.execute(text("SELECT unit_code FROM nps_units"))).all()}
    rows = []
    for number, raw in _reader(path, NPS_VISIT_COLUMNS):
        ref = f"{path.name}:{number}"
        try:
            year, month, visits = int(raw["year"]), int(raw["month"]), int(raw["recreation_visits"])
        except ValueError:
            report.quarantine(ref, "invalid")
            continue
        if not 1 <= month <= 12 or visits < 0 or not raw["source_url"].startswith("https://"):
            report.quarantine(ref, "invalid")
            continue
        if (year, month) >= (today.year, today.month):
            report.quarantine(ref, "future")
            continue
        if raw["unit_code"].upper() not in units:
            report.quarantine(ref, "unknown_unit")
            continue
        report.accept()
        rows.append({"u": raw["unit_code"].upper(), "y": year, "m": month, "v": visits, "s": raw["source_url"]})
    if rows:
        await conn.execute(text(
            "INSERT INTO nps_monthly_visits (unit_code, year, month, visits, source_url) VALUES (:u, :y, :m, :v, :s) "
            "ON CONFLICT (unit_code, year, month) DO UPDATE SET visits = EXCLUDED.visits, source_url = EXCLUDED.source_url"), rows)
    return len(rows)
```

The current month is quarantined as `future` because its visitation total is not final. Extend `curated._main` with `nps-units` and `nps-visits`. `data/curated/nps_units.csv` and `nps_visits.csv`: header lines only; README gains a paragraph on them (NPS IRMA Stats, public domain, monthly recreation visits; the unit list is the owner's hand-picked climbing parks).

`backend/tests/verify/test_phase2b_exposure_storage.py`:

```python
import asyncio
import os

import asyncpg
import pytest

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


def test_exposure_bounds_populated_and_spearman_reported():
    [row] = fetch("SELECT count(*) FILTER (WHERE exposure_lo IS NULL OR exposure_hi IS NULL) AS missing, count(*) AS n "
                  "FROM exposure_index WHERE as_of = (SELECT max(as_of) FROM exposure_index)")
    assert row["n"] > 0 and row["missing"] == 0
    [rep] = fetch("SELECT validation_report FROM source_ingest_log WHERE source = 'exposure' AND status = 'ok' "
                  "ORDER BY finished_at DESC LIMIT 1")
    print(rep["validation_report"])
```

Workflow: add `uv run python -m app.pipelines.exposure` to `data-weekly.yml`'s `monthly` job (after the lightning steps). Docs: CHANGELOG "Phase 2b exposure v1 (PR 2b-4)"; CLAUDE.md: `EXPOSURE_ENABLE_MP_TICKS` and the removal drill in one line; DEPLOYMENT.md: none needed beyond the monthly job; DATABASE_STRUCTURE.md: `exposure_index` (and "never served by the API"), NPS tables.

- [ ] **Step 3: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd .. && python scripts/check_no_mp_data.py` → green.
- [ ] **Step 4: Commit** — `git add backend/app/pipelines/curated.py backend/tests/ data/curated/ .github/workflows/data-weekly.yml CHANGELOG.md CLAUDE.md data/DATABASE_STRUCTURE.md && git commit -m "feat(pipelines): NPS visitation loaders; monthly exposure; acceptance cells"`

---

# PR 2b-5 — `feat/p2b-prediction-compaction` `[assumes D18]`

### Task 7: Migration `0015` — `prediction_archive_v1`

**Files:**
- Create: `backend/alembic/versions/0015_prediction_archive.py`, `backend/tests/test_migration_0015.py`
- Modify: `backend/app/models/exposure.py` (add `PredictionArchive`), `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Produces (DB): `prediction_archive_v1(route_id bigint, month_start date CHECK (extract(day FROM month_start) = 1), scores smallint[] NOT NULL CHECK (cardinality(scores) = 31), PK (route_id, month_start))`. `app` gets SELECT through `migrator`'s default privileges (unchanged); `ingest` gets SELECT/INSERT/UPDATE on it and SELECT/DELETE on `historical_predictions`.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0015.py`:

```python
import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg


def test_0015_shape_checks():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))
        run_sql(name, "INSERT INTO prediction_archive_v1 VALUES (900000001, '2026-08-01', array_fill(NULL::smallint, ARRAY[31]))")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO prediction_archive_v1 VALUES (900000001, '2026-08-02', array_fill(NULL::smallint, ARRAY[31]))")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO prediction_archive_v1 VALUES (900000002, '2026-08-01', ARRAY[1,2]::smallint[])")
```

- [ ] **Step 2: Implement** `backend/alembic/versions/0015_prediction_archive.py`:

```python
"""P2-8 compaction target: one row per route per month, scores[day] (rounded 0–100),
-1 = insufficient (gray), NULL = no prediction. Public (MP ids may be displayed, amendment
D5); the app role only reads it (D18)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_prediction_archive"
down_revision = "0014_exposure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prediction_archive_v1",
        sa.Column("route_id", sa.BigInteger(), nullable=False),
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("scores", postgresql.ARRAY(sa.SmallInteger()), nullable=False),
        sa.PrimaryKeyConstraint("route_id", "month_start"),
        sa.CheckConstraint("extract(day FROM month_start) = 1", name="prediction_archive_v1_month_start_check"),
        sa.CheckConstraint("cardinality(scores) = 31", name="prediction_archive_v1_scores_check"),
    )


def downgrade() -> None:
    n = op.get_bind().exec_driver_sql("SELECT count(*) FROM prediction_archive_v1").scalar_one()
    if n:
        raise RuntimeError(f"refusing to downgrade 0015: {n} archived route-months (their source rows are deleted)")
    op.drop_table("prediction_archive_v1")
```

Grants ("Plan 8 (0015)"): `GRANT SELECT, INSERT, UPDATE ON public.prediction_archive_v1 TO ingest; GRANT SELECT, DELETE ON public.historical_predictions TO ingest; GRANT SELECT ON public.prediction_archive_v1 TO trainer;`. `verify_roles_phase2.sql` `ingest_writes`: `('public.prediction_archive_v1','INSERT'), ('public.prediction_archive_v1','UPDATE'), ('public.historical_predictions','DELETE')`. Phase 1's `verify_roles.sql` still passes: `app` holds only SELECT on the new table.

- [ ] **Step 3: Run** — PASS (including `tests/test_migrations.py::test_role_scripts_create_least_privilege_roles`). **Step 4: Commit** — `git add backend/alembic/versions/0015_prediction_archive.py backend/app/models/exposure.py backend/tests/test_migration_0015.py backend/db/roles/ && git commit -m "feat(db): 0015 prediction_archive_v1 (read-only to app)"`

---

### Task 8: Fold, verify, delete

**Files:**
- Create: `backend/app/pipelines/prediction_archive.py`, `backend/tests/test_prediction_archive.py`
- Modify: `backend/pyproject.toml`, `.github/workflows/data-daily.yml`

**Interfaces:**
- Produces: `ARCHIVE_GRAY = -1`, `FOLD_AFTER_DAYS = 7`, `FOLD_SQL`, `VERIFY_SQL`, `async fold_month(conn, month_start: date, *, fold_before: date) -> dict[str, int]` (insert/merge, verify, delete — one transaction; raises and rolls back on any mismatch), `async fold_all(engine_factory, *, today: date) -> dict[str, object]` (every month with rows older than `today − 7 d`, oldest first, logged per month as `prediction_archive`), CLI `python -m app.pipelines.prediction_archive [--dry-run]`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_prediction_archive.py`:

```python
import asyncio
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.prediction_archive import fold_month
from tests.pgtest import migrated_db, requires_pg, sa_url

SEED = """
INSERT INTO historical_predictions (route_id, prediction_date, risk_score, color_code) VALUES
  (900000001, '2026-08-01', 23.4, 'green'), (900000001, '2026-08-02', NULL, 'gray'),
  (900000001, '2026-08-31', 80.6, 'red'), (900000001, '2026-09-25', 50.0, 'orange');
"""


def _go(name, fn):
    async def go():
        engine = create_async_engine(sa_url(name))
        try:
            async with engine.begin() as conn:
                return await fn(conn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


@requires_pg
def test_gray_day_round_trips_as_insufficient():
    with migrated_db(seed_sql=SEED) as name:
        result = _go(name, lambda c: fold_month(c, date(2026, 8, 1), fold_before=date(2026, 9, 21)))
        assert result == {"folded": 3, "deleted": 3}
        rows = _go(name, _scores)
        assert rows[0] == 23 and rows[1] == -1 and rows[30] == 81 and rows[2] is None
        left = _go(name, _count)
        assert left == 1


async def _scores(conn):
    return list((await conn.execute(text("SELECT scores FROM prediction_archive_v1"))).scalar_one())


async def _count(conn):
    return (await conn.execute(text("SELECT count(*) FROM historical_predictions"))).scalar_one()


@requires_pg
def test_rerun_after_partial_fold_is_idempotent():
    with migrated_db(seed_sql=SEED) as name:
        # Simulate a crash after the insert: archive row present, source rows still there.
        _go(name, lambda c: c.execute(text(
            "INSERT INTO prediction_archive_v1 VALUES (900000001, '2026-08-01', "
            "(SELECT array_agg(CASE WHEN g = 1 THEN 23 ELSE NULL END ORDER BY g)::smallint[] FROM generate_series(1, 31) g))")))
        result = _go(name, lambda c: fold_month(c, date(2026, 8, 1), fold_before=date(2026, 9, 21)))
        assert result == {"folded": 3, "deleted": 3}
        assert _go(name, _scores)[1] == -1
```


- [ ] **Step 2: Implement** `backend/app/pipelines/prediction_archive.py`:

```python
"""P2-8 compaction (D18): fold historical_predictions rows older than 7 days into one
array per route-month, verify every folded row, then delete it — all in one transaction per
month, so a crash leaves either nothing changed or a complete, verified month."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

ARCHIVE_GRAY = -1
FOLD_AFTER_DAYS = 7

SRC = (
    "SELECT route_id, date_trunc('month', prediction_date)::date AS m, extract(day FROM prediction_date)::int AS d, "
    f"CASE WHEN risk_score IS NULL THEN {ARCHIVE_GRAY} ELSE round(risk_score)::int END AS v "
    "FROM historical_predictions WHERE prediction_date >= :m AND prediction_date < (:m + interval '1 month') "
    "AND prediction_date < :before"
)
FOLD_SQL = f"""
WITH src AS ({SRC}),
agg AS (SELECT route_id, m, jsonb_object_agg(d::text, v) AS days FROM src GROUP BY route_id, m)
INSERT INTO prediction_archive_v1 (route_id, month_start, scores)
SELECT route_id, m, ARRAY(SELECT (days ->> g::text)::smallint FROM generate_series(1, 31) g ORDER BY g) FROM agg
ON CONFLICT (route_id, month_start) DO UPDATE SET scores = (
  SELECT array_agg(coalesce(n.v, o.v) ORDER BY n.i)
  FROM unnest(EXCLUDED.scores) WITH ORDINALITY n(v, i)
  JOIN unnest(prediction_archive_v1.scores) WITH ORDINALITY o(v, i) USING (i))
"""
VERIFY_SQL = f"""
WITH src AS ({SRC})
SELECT count(*) AS n, count(*) FILTER (WHERE a.scores[s.d] IS DISTINCT FROM s.v) AS bad
FROM src s LEFT JOIN prediction_archive_v1 a ON a.route_id = s.route_id AND a.month_start = s.m
"""
DELETE_SQL = (
    "DELETE FROM historical_predictions WHERE prediction_date >= :m AND prediction_date < (:m + interval '1 month') "
    "AND prediction_date < :before"
)


async def fold_month(conn: AsyncConnection, month_start: date, *, fold_before: date) -> dict[str, int]:
    params = {"m": month_start, "before": fold_before}
    await conn.execute(text(FOLD_SQL), params)
    check = (await conn.execute(text(VERIFY_SQL), params)).one()
    if int(check.bad):
        raise RuntimeError(f"archive verification failed for {month_start:%Y-%m}: {int(check.bad)} of {int(check.n)} rows differ")
    deleted = (await conn.execute(text(DELETE_SQL), params)).rowcount
    if deleted != int(check.n):
        raise RuntimeError(f"deleted {deleted} rows but verified {int(check.n)} for {month_start:%Y-%m}")
    return {"folded": int(check.n), "deleted": deleted}


async def fold_all(engine_factory: Callable[[], AsyncEngine], *, today: date, dry_run: bool = False) -> dict[str, object]:
    before = today - timedelta(days=FOLD_AFTER_DAYS)
    engine = engine_factory()
    done: dict[str, int] = {}
    try:
        async with engine.connect() as conn:
            months = [m for (m,) in (await conn.execute(text(
                "SELECT DISTINCT date_trunc('month', prediction_date)::date FROM historical_predictions "
                "WHERE prediction_date < :b ORDER BY 1"), {"b": before})).all()]
        if dry_run:
            return {"mode": "dry_run", "months": [m.isoformat() for m in months], "fold_before": before.isoformat()}
        for m in months:
            async with engine.begin() as conn:
                result = await fold_month(conn, m, fold_before=before)
                report = ValidationReport("prediction_archive")
                report.rows_in = report.accepted = result["folded"]
                run_id = await start_run(conn, source="prediction_archive", window_start=m, window_end=before, content_sha256=None)
                await finish_run(conn, run_id, status="ok", report=report, rows_upserted=result["deleted"])
            done[m.isoformat()] = result["folded"]
    finally:
        await engine.dispose()
    return {"months": done, "fold_before": before.isoformat()}


if __name__ == "__main__":
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(fold_all(ingest_engine, today=utc_today(), dry_run=args.dry_run)), sort_keys=True))
```

`round()` on a Postgres `double precision` value rounds half to even on some platforms and half away from zero on others; `VERIFY_SQL` uses the same expression, so verification compares like with like, and the display tolerance is the integer step either way. The nightly run's future-dated rows (today + 1, + 2) are never folded (they are newer than the cutoff). Append `"app.pipelines.prediction_archive"` to strict mypy. `data-daily.yml`: add `uv run python -m app.pipelines.prediction_archive` after the GLM step.

- [ ] **Step 3: Run** — PASS. **Step 4: Commit** — `git add backend/app/pipelines/prediction_archive.py backend/tests/test_prediction_archive.py backend/pyproject.toml .github/workflows/data-daily.yml && git commit -m "feat(pipelines): verified monthly compaction of historical_predictions"`

---

### Task 9: Trends endpoint reads archive + recent rows; PR 2b-5 docs

**Files:**
- Modify: `backend/app/api/v1/mp_routes.py` (historical trends query, around line 1570), `CHANGELOG.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`
- Create: `backend/tests/test_historical_trends_archive.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_historical_trends_archive.py` (same fixture pattern as `test_ascent_analytics.py`: a migrated database, `get_db` overridden, the weather fetchers patched out like its `no_weather_calls` fixture):

```python
import asyncio
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.session import get_db
from app.main import app
from tests.pgtest import migrated_db, requires_pg, sa_url

pytestmark = requires_pg


@pytest.fixture
def no_weather(monkeypatch):
    # Same as test_ascent_analytics.no_weather_calls: the weather-volatility block catches this.
    import requests

    def refuse(*args, **kwargs):
        raise requests.ConnectionError("network disabled in tests")

    monkeypatch.setattr(requests, "get", refuse)


def test_trends_merge_archive_and_recent_without_duplicates(no_weather):
    today = date.today()
    first = (today - timedelta(days=40)).replace(day=1)
    seed = f"""
    INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (900000101, 'Fixture Crag', 40.0, -105.0);
    INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES (900000001, 'Fixture', 900000101, 'Trad');
    INSERT INTO prediction_archive_v1 VALUES (900000001, '{first}',
      (SELECT array_agg(CASE WHEN g = 1 THEN 30 WHEN g = 2 THEN -1 ELSE NULL END ORDER BY g)::smallint[] FROM generate_series(1, 31) g));
    INSERT INTO historical_predictions (route_id, prediction_date, risk_score, color_code) VALUES
      (900000001, '{first}', 31.0, 'yellow'), (900000001, '{today}', 40.0, 'yellow');
    """
    with migrated_db(seed_sql=seed) as name:
        engine = create_async_engine(sa_url(name))

        async def db():
            async with AsyncSession(engine) as session:
                yield session

        app.dependency_overrides[get_db] = db
        try:
            async def call():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
                    return (await client.get("/api/v1/mp-routes/900000001/historical-trends", params={"days": 60})).json()

            data = asyncio.run(call())
        finally:
            app.dependency_overrides.clear()
            asyncio.run(engine.dispose())
    by_date = {p["date"]: p for p in data["historical_predictions"]}
    assert by_date[first.isoformat()]["risk_score"] == 31.0
    assert by_date[(first + timedelta(days=1)).isoformat()]["data_status"] == "insufficient_data"
    assert len(by_date) == len(data["historical_predictions"])
```

(The route is `GET /api/v1/mp-routes/{mp_route_id}/historical-trends` with `days` 1–365, `app/api/v1/mp_routes.py:1483`.)

- [ ] **Step 2: Implement** — replace `historical_query` in the trends handler with:

```python
    # Days older than a week live in prediction_archive_v1 (D18): -1 is an insufficient day,
    # NULL no prediction. A day still present in historical_predictions wins.
    historical_query = text("""
        SELECT prediction_date, risk_score, color_code
        FROM historical_predictions
        WHERE route_id = :route_id AND prediction_date >= CURRENT_DATE - (:days || ' days')::interval
        UNION ALL
        SELECT (a.month_start + (u.i - 1)::int)::date,
               CASE WHEN u.v = -1 THEN NULL ELSE u.v::float END,
               CASE WHEN u.v = -1 THEN 'gray' ELSE 'archived' END
        FROM prediction_archive_v1 a CROSS JOIN LATERAL unnest(a.scores) WITH ORDINALITY u(v, i)
        WHERE a.route_id = :route_id AND u.v IS NOT NULL
          AND (a.month_start + (u.i - 1)::int) >= CURRENT_DATE - (:days || ' days')::interval
          AND NOT EXISTS (SELECT 1 FROM historical_predictions h
                          WHERE h.route_id = a.route_id AND h.prediction_date = a.month_start + (u.i - 1)::int)
        ORDER BY 1 ASC
    """)
```

The existing loop already maps `(None, 'gray')` to insufficient and re-derives colours from the score for every other row, so `'archived'` never reaches the response. Docs: CHANGELOG "Prediction compaction (PR 2b-5)"; DEPLOYMENT.md nightly-job section: "rows older than 7 days are folded daily into `prediction_archive_v1` by `data-daily.yml`; archived days show integer precision"; DATABASE_STRUCTURE.md: the archive table and encoding.

- [ ] **Step 3: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/` → green.
- [ ] **Step 4: Commit** — `git add backend/app/api/v1/mp_routes.py backend/tests/test_historical_trends_archive.py CHANGELOG.md DEPLOYMENT.md data/DATABASE_STRUCTURE.md && git commit -m "feat(api): historical trends read the compacted archive plus recent rows"`

---

### Task 10: OWNER/AGENT RUNBOOK — PR 2b-4 (exposure)

- [ ] **Step 1 (owner/agent)**: migrate (`0014`) on a branch, then prod; grants; verify scripts.
- [ ] **Step 2 (owner)**: fill `data/curated/nps_units.csv` (Yosemite, Joshua Tree, Rocky Mountain, Grand Teton, Zion, Red Rock is BLM so not NPS, Acadia, New River Gorge, Denali, Mount Rainier, Black Canyon of the Gunnison, Devils Tower, North Cascades, Sequoia/Kings Canyon, Pinnacles, …) with centre coordinates and a radius covering the climbing areas, and `nps_visits.csv` with monthly recreation visits from NPS IRMA Stats for the last complete years; commit on a docs branch; load with `curated nps-units` then `curated nps-visits`.
- [ ] **Step 3 (owner/agent)**: `python -m app.pipelines.exposure` (flag on) → rows and the Spearman report; then the removal drill on the branch: run with `EXPOSURE_ENABLE_MP_TICKS=false` in the job's environment, confirm the verify cell passes and both MP keys are missing-flagged, then rerun with the flag on. Record `spearman_vs_permits` and `spearman_vs_mp_ticks` in the PR (spec 2b-4 acceptance).

### Task 11: OWNER/AGENT RUNBOOK — PR 2b-5 (compaction)

- [ ] **Step 1 (owner/agent): Dump first** — `pg_dump -Fc -t public.historical_predictions` into `~/Developer/safeascent-private/backups/pre-2b5/` (plan 1 Task 8 pattern). Note the current size: as analyst `SELECT pg_size_pretty(pg_total_relation_size('historical_predictions')), pg_size_pretty(pg_database_size(current_database()))`.
- [ ] **Step 2 (owner/agent): Branch rehearsal** — migrate (`0015`), grants, verify; `prediction_archive --dry-run` lists months; run for real (each month is verified before deletion; any mismatch aborts that month untouched); run it again → no months left except the last week.
- [ ] **Step 3 (owner): Deploy and prod** — deploy the backend (trends endpoint reads both tables) → migrate prod → run the fold outside the 01:00–05:00 UTC nightly window → as owner, in a quiet window, `VACUUM (FULL, ANALYZE) historical_predictions` via `SET ROLE migrator` (exclusive lock for minutes; the nightly insert would block, so never during 01:00–05:00 UTC). Storage in Neon drops after its history-retention window.
- [ ] **Step 4 (owner/agent): Acceptance** — `pg_database_size` ≤ 4.5 GB (spec 2b-5); trends endpoint shows a route's last 60 days identically before and after (compare JSON for 5 routes, allowing integer rounding on archived days); `data-daily.yml` folds nightly thereafter.

---

## Self-review

- Spec coverage: 2b-4 — every component in the P2-9 table (route density, season share, OpenBeta ticks per D17, NPS visits, permits, objective popularity, in-app ticks, both optional MP components with the flag, censoring and CA/NV scope), `log(1+x)` is Phase 3's transform (the raw values are stored), bounds ×/÷3 and ×/÷1.5, Spearman validation, removal drill, proxy never served (Tasks 1–6, 10). 2b-5 — compact layout, row-for-row verification, cadence unchanged, DB ≤4.5 GB (Tasks 7–9, 11), with the storage placement and writer adjusted per D18.
- Placeholders: none beyond owner-curated CSV rows and Console values.
- Types: `Component`, `Key`, `build`, `validate`, `fold_month`, `ARCHIVE_GRAY` are used consistently; the archive encoding matches between `FOLD_SQL`, `VERIFY_SQL` and the endpoint.
