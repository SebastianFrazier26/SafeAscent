# Phase 2b Similarity Features and Legacy Cleanup (PRs 2b-2a, 2b-2b, 2b-2c) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce, for every route point, the features Phase 3 MVP-1's similarity pooling needs (amendment §3.1: type group, elevation, monthly climate normals, latitude) with 100% elevation coverage verified against USGS EPQS; then the v2.2 terrain and lithology features (Horn slope/aspect, crag aspect with confidence, Macrostrat lithology); then R10, which relinks legacy accident links into `accident_route_links` and drops `routes`, `mountains`, `accidents.route_id` and `accidents.mountain_id` behind a guarded migration.

**Architecture:** Features are computed once per distinct **point** (`feature_points`, keyed by coordinates rounded to 5 decimals) `[assumes D7]`, so MP locations get elevation and normals before the OpenBeta catalog is complete; `route_static_features` is a per-route table in the P3 shape filled from points. `dem.py` samples USGS 3DEP Cloud-Optimized GeoTIFFs remotely with rasterio (only the blocks under our points are read) `[assumes D5]`; `normals.py` reduces 10 complete years of ERA5 daily data per 0.1° bucket to monthly normals `[assumes D6]`. Everything is a resumable, budget-capped batch job.

**Tech Stack:** Python 3.12, rasterio (bundled GDAL; `pipelines` dependency group only), numpy, h3, httpx, SQLAlchemy async, Alembic, PostGIS.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` §Static features, R10, milestone 2b-2; `docs/superpowers/specs/2026-09-28-phase3-amendment-similarity-confidence.md` §3.1 (MVP-1 feature set, elevation + normals pulled forward — this plan answers its open question 5: they are Phase 2 work, PR 2b-2a); P3:150 (`route_static_features` columns). Decisions D5, D6, D7, D8, D15, D16 in `2026-09-28-phase2a-foundations.md`.

**Prerequisites:** Plans 1, 3 (Open-Meteo client), and 4 (catalog, matcher) merged and applied. PR 2b-2c also needs plan 2's repair framework.

**Three PRs:** 2b-2a = Tasks 1–6 + runbook Task 11 (`feat/p2b-mvp1-features`, the MVP-1 prerequisite); 2b-2b = Tasks 7–8 + runbook Task 12 (`feat/p2b-terrain-lithology`, v2.2); 2b-2c = Tasks 9–10 + runbook Task 13 (`feat/p2b-r10-legacy`).

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- A feature that cannot be computed is NULL with a reason (`dem_source`, `lithology_source`, `n_years`), never 0 and never a default; downstream "missing counts against confidence" (amendment §3.1).
- DEM nodata values and out-of-coverage points are NULL. Elevations are checked against EPQS on 200 random points: every comparable point within 5 m, or the batch is not accepted.
- Normals use the trailing 10 **complete** calendar years relative to the run date (never a fixed span); a month with fewer than 8 qualifying years is NULL for that month.
- No bulk DEM tile downloads into the repo or the image; rasterio reads remote COGs at run time. The Railway image does not install the `pipelines` group.
- The R10 migration drops legacy tables only after a dump exists and every legacy link is resolved; it refuses otherwise.

## Decisions this plan makes where the spec is silent (owner may overrule)

1. Point key precision is 5 decimals (~1 m); MP location coordinates carry 6 (`data/DATABASE_STRUCTURE.md`), so distinct crags never merge.
2. 3DEP coordinates are NAD83; they are treated as WGS84 (the datum shift is ≤ ~2 m horizontally, below the DEM's 10 m cell).
3. A normals month "qualifies" in a year when ≥90% of its days have non-null tmax and tmin.
4. `route_static_features.lat` is added beside the P3 columns because latitude is an MVP-1 feature (amendment §3.1).

## Review Focus

1. **A point over a DEM nodata cell (lake, tile edge)** — expect `elevation_m` NULL with `dem_source = 'nodata'`, never 0 (Task 2 `test_nodata_is_null_not_zero`).
2. **An Alaska point outside 1/3″ coverage** — expect the 2″ fallback and `dem_res_m = 60` (Task 2 `test_falls_back_to_2_arcsecond`).
3. **A normals run on 3 January** — expect the 10-year window to end two years back (the last year is not complete in ERA5 until the lag passes) (Task 3 `test_window_waits_for_era5_lag`).
4. **A route whose area has no coordinates** — expect a `route_static_features` row with NULL location features and `coord_precision = 'none'`, not a missing row (Task 5 `test_routes_without_coordinates_get_explicit_nulls`).
5. **`0011` run while one accident still has a legacy `route_id`** — expect a refusal naming the count, nothing dropped (Task 10 `test_0011_refuses_while_legacy_links_remain`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/alembic/versions/0010_static_features.py` | Create | `feature_points`, `cell_climate_normals`, `cell_climatology`, `route_static_features`. |
| `backend/app/models/features.py` | Create | Models. |
| `backend/app/pipelines/dem.py` | Create | 3DEP tile naming, remote COG point sampling, window reads. |
| `backend/app/pipelines/epqs.py` | Create | EPQS point client + comparison. |
| `backend/app/pipelines/normals.py` | Create | Normals window, monthly reduction, budgeted resumable job. |
| `backend/app/pipelines/static_features.py` | Create | Point upsert, elevation fill, route table build, CLI. |
| `backend/app/pipelines/terrain.py` | Create | Horn slope/aspect, circular mean, crag aspect. |
| `backend/app/pipelines/lithology.py` | Create | Macrostrat client + ~10-class normalizer. |
| `backend/app/data/repair/legacy_links.py` | Create | R10 relink + null legacy columns. |
| `backend/alembic/versions/0011_drop_legacy_routes.py` | Create | Guarded drop of legacy FKs, columns, tables. |
| `backend/app/models/accident.py`, `backend/app/models/legacy.py`, `backend/app/models/__init__.py`, `backend/alembic/env.py` | Modify/Delete | Legacy removal (2b-2c). |
| `backend/app/api/v1/accidents.py`, `backend/app/schemas/accident.py` | Modify | `mountain_id` filter → 422; fields removed. |
| `backend/app/data/repair/framework.py` | Modify | `route_id`, `mountain_id` repairable (R10 only). |
| tests: `test_migration_0010.py`, `test_dem.py`, `test_epqs.py`, `test_normals.py`, `test_static_features.py`, `test_terrain.py`, `test_lithology.py`, `test_legacy_links.py`, `test_migration_0011.py`; modify `test_migrations.py`, `test_ascent_analytics.py` | Create/Modify | Tests. |
| `backend/tests/verify/test_phase2b_features.py` | Create | `-m db` acceptance. |
| `.github/workflows/data-static-features.yml` | Create | Weekly point/elevation refresh + yearly normals (dispatch). |
| `backend/pyproject.toml`, `backend/uv.lock`, grants/verify SQL, docs | Modify | Deps, mypy, grants, docs. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 2–8 | `feature_points`, `cell_climate_normals`, `route_static_features` (0010) — all v2.2 columns created now | Task 1 first; Tasks 7–8 only fill columns. |
| 2 | 5, 7 | `dem.tile_url(lat, lon, res)`, `dem.sample_points(points, opener)`, `dem.read_window(...)` | Frozen in Task 2. |
| 3 | 5, plan 7 | `normals.monthly_normals`, `normals.window(today)` | Plan 7's day-of-year climatology reuses `window`. |
| 3 | plan 3 | `open_meteo.ArchiveClient.fetch` | Consumed unchanged. |
| 5 | plans 6, 8, Phase 3 | `route_static_features` column names, `feature_points.point_key` | Frozen here. |
| 9 | 10 | `accident_route_links` fully populated before `0011` | Migration guard enforces it. |
| 9 | plan 2 | `framework.REPAIRABLE_FIELDS` gains `route_id`, `mountain_id` | Task 9 edits it with a comment that R10 is the only writer. |
| 10 | plans 1–4 tests | `test_migrations.py`, `test_ascent_analytics.py` assume legacy tables at head | Task 10 updates both in the same commit as `0011`. |
| 1, 5, 7, 9 | each other | `grants_phase2.sql`, `verify_roles_phase2.sql`, `pyproject.toml` | Serial appends. |

---

# PR 2b-2a — `feat/p2b-mvp1-features`

### Task 1: Migration `0010`, models, `rasterio` in the `pipelines` group

**Files:**
- Create: `backend/alembic/versions/0010_static_features.py`, `backend/app/models/features.py`, `backend/tests/test_migration_0010.py`
- Modify: `backend/app/models/__init__.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Produces (DB):
  - `feature_points(point_key text PK /* 'lat5:lon5' */, lat double precision NOT NULL, lon double precision NOT NULL, grid_bucket integer NOT NULL, h3_r5 text NOT NULL, h3_r7 text NOT NULL, elevation_m real, dem_source text CHECK IN ('3dep_13','3dep_2','nodata','no_tile'), dem_res_m real, slope_deg real, aspect_deg real, aspect_confidence real, lithology text, lithology_source text CHECK IN ('macrostrat','none'), feature_version text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(), run_id uuid)`, index on `grid_bucket`.
  - `cell_climate_normals(grid_bucket integer, month smallint CHECK 1..12, tmax_mean real, tmin_mean real, precip_mm real, snowfall_cm real, freeze_thaw_days real, n_years smallint NOT NULL, period_start_year smallint NOT NULL, period_end_year smallint NOT NULL, ref_elevation_m real, source text NOT NULL, normals_version text NOT NULL, run_id uuid, PK (grid_bucket, month))`.
  - `cell_climatology(grid_bucket integer, doy smallint CHECK 1..366, tmax_mean real, tmax_p10 real, tmax_p90 real, tmin_mean real, tmin_p10 real, tmin_p90 real, precip_mean real, precip_p90 real, snowfall_mean real, gust_p90 real, freeze_thaw_freq real, lightning_day_freq real /* plan 7 */, n_years smallint NOT NULL, period_start_year smallint NOT NULL, period_end_year smallint NOT NULL, climatology_version text NOT NULL, run_id uuid, PK (grid_bucket, doy))` — the spec's "typical day" reference, wide instead of long `[assumes D7]`.
  - `route_static_features(route_id uuid PK REFERENCES canonical_routes, source text NOT NULL, area_path ltree, type_group text, point_key text REFERENCES feature_points, lat double precision, aspect_deg real, slope_deg real, elevation_m real, lithology text, pitches smallint, length_m real, h3_r5 text, grid_bucket integer, dem_res_m real, aspect_confidence real, coord_precision text NOT NULL, lithology_source text, feature_version text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now())`.
- Produces (Python): models `FeaturePoint`, `CellClimateNormals`, `CellClimatology`, `RouteStaticFeatures`; `pipelines` group gains `rasterio>=1.4,<2`.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0010.py`:

```python
import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg


def test_0010_checks_clean_and_rejects_bad_values():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO cell_climate_normals (grid_bucket, month, n_years, period_start_year, "
                          "period_end_year, source, normals_version) VALUES (1, 13, 10, 2016, 2025, 't', 'n-v1')")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, dem_source, "
                          "feature_version) VALUES ('k', 40, -105, 1, 'a', 'b', 'guessed', 'f-v1')")
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/alembic/versions/0010_static_features.py`:

```python
"""Static features keyed by point (D7), monthly climate normals per 0.1° bucket, and the
per-route table Phase 3 reads (P3:150 + Phase 2 + amendment §3.1 columns). v2.2 columns
(slope, aspect, lithology) exist now so later PRs only fill them."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

revision = "0010_static_features"
down_revision = "0009_mp_ticks_internal"
branch_labels = None
depends_on = None


class Ltree(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "LTREE"


def upgrade() -> None:
    op.create_table(
        "feature_points",
        sa.Column("point_key", sa.Text(), primary_key=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("h3_r5", sa.Text(), nullable=False),
        sa.Column("h3_r7", sa.Text(), nullable=False),
        sa.Column("elevation_m", sa.REAL(), nullable=True),
        sa.Column("dem_source", sa.Text(), nullable=True),
        sa.Column("dem_res_m", sa.REAL(), nullable=True),
        sa.Column("slope_deg", sa.REAL(), nullable=True),
        sa.Column("aspect_deg", sa.REAL(), nullable=True),
        sa.Column("aspect_confidence", sa.REAL(), nullable=True),
        sa.Column("lithology", sa.Text(), nullable=True),
        sa.Column("lithology_source", sa.Text(), nullable=True),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("dem_source IS NULL OR dem_source IN ('3dep_13', '3dep_2', 'nodata', 'no_tile')",
                           name="feature_points_dem_source_check"),
        sa.CheckConstraint("lithology_source IS NULL OR lithology_source IN ('macrostrat', 'none')",
                           name="feature_points_lithology_source_check"),
    )
    op.create_index("ix_feature_points_grid_bucket", "feature_points", ["grid_bucket"])
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
    op.create_table(
        "route_static_features",
        sa.Column("route_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("canonical_routes.route_id"), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("area_path", Ltree(), nullable=True),
        sa.Column("type_group", sa.Text(), nullable=True),
        sa.Column("point_key", sa.Text(), sa.ForeignKey("feature_points.point_key"), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("aspect_deg", sa.REAL(), nullable=True),
        sa.Column("slope_deg", sa.REAL(), nullable=True),
        sa.Column("elevation_m", sa.REAL(), nullable=True),
        sa.Column("lithology", sa.Text(), nullable=True),
        sa.Column("pitches", sa.SmallInteger(), nullable=True),
        sa.Column("length_m", sa.REAL(), nullable=True),
        sa.Column("h3_r5", sa.Text(), nullable=True),
        sa.Column("grid_bucket", sa.Integer(), nullable=True),
        sa.Column("dem_res_m", sa.REAL(), nullable=True),
        sa.Column("aspect_confidence", sa.REAL(), nullable=True),
        sa.Column("coord_precision", sa.Text(), nullable=False),
        sa.Column("lithology_source", sa.Text(), nullable=True),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    bind = op.get_bind()
    n = bind.exec_driver_sql("SELECT count(*) FROM cell_climate_normals").scalar_one()
    if n:
        raise RuntimeError(f"refusing to downgrade 0010: {n} paid normals rows")
    op.drop_table("route_static_features")
    op.drop_table("cell_climatology")
    op.drop_table("cell_climate_normals")
    op.drop_index("ix_feature_points_grid_bucket", "feature_points")
    op.drop_table("feature_points")
```

`backend/app/models/features.py` mirrors the four tables with `Mapped[...]` columns of the same types (`REAL` for real, `Float` for double precision, `Ltree` from `app.models.types`, FKs as in the migration, index `ix_feature_points_grid_bucket`), following the pattern of `app/models/catalog.py`. Register it in `app/models/__init__.py` and the models mypy block.

```bash
cd backend && uv add --group pipelines 'rasterio>=1.4,<2'
```

Grants ("Plan 5 (0010)"):

```sql
GRANT SELECT, INSERT, UPDATE ON public.feature_points, public.cell_climate_normals, public.cell_climatology TO ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.route_static_features TO ingest;
GRANT SELECT ON public.feature_points, public.cell_climate_normals, public.cell_climatology, public.route_static_features TO trainer;
```

`verify_roles_phase2.sql` `ingest_writes`: INSERT/UPDATE for `public.feature_points`, `public.cell_climate_normals`, `public.cell_climatology`; INSERT/UPDATE/DELETE for `public.route_static_features`.

- [ ] **Step 4: Run** — `cd backend && uv sync --group pipelines && uv run pytest tests/test_migration_0010.py tests/test_migrations.py tests/test_roles_phase2.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0010_static_features.py backend/app/models/ backend/tests/test_migration_0010.py backend/pyproject.toml backend/uv.lock backend/db/roles/ && git commit -m "feat(db): 0010 feature points, monthly climate normals, route_static_features"`

---

### Task 2: 3DEP DEM sampler (remote COG, 2″ fallback, nodata → NULL)

**Files:**
- Create: `backend/app/pipelines/dem.py`, `backend/tests/test_dem.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `TILE_BASE = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation"`, `RES = {"3dep_13": ("13", 10.0), "3dep_2": ("2", 60.0)}`, `tile_name(lat: float, lon: float) -> str` (`n41w106`), `tile_url(lat: float, lon: float, source: str) -> str`, `@dataclass(frozen=True) Elevation(elevation_m: float | None, dem_source: str, dem_res_m: float | None)`, `Opener = Callable[[str], ContextManager[DatasetReader] | None]` (None = tile missing), `sample_points(points: Sequence[tuple[float, float]], opener: Opener = open_remote) -> list[Elevation]`, `open_remote(url: str)`.

- [ ] **Step 1: Verify the tile layout (agent, read-only)**

```bash
cd backend && uv run python - <<'EOF'
import rasterio
with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
    with rasterio.open("/vsicurl/https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/n41w106/USGS_13_n41w106.tif") as src:
        print(src.crs, src.res, src.nodata, src.profile.get("tiled"), src.overviews(1)[:2])
        print(list(src.sample([(-105.6, 40.25)]))[0])
EOF
```

Expected: a geographic CRS (EPSG:4269), `res` ≈ (9.26e-05, 9.26e-05), a nodata value, `tiled=True`, and an elevation in the thousands of meters. If the path 404s, find the current naming at https://apps.nationalmap.gov/downloader and update only `tile_url`.

- [ ] **Step 2: Failing tests** — `backend/tests/test_dem.py`:

```python
from contextlib import contextmanager

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.pipelines.dem import sample_points, tile_name, tile_url


def test_tile_naming_uses_the_northwest_corner():
    assert tile_name(40.25, -105.6) == "n41w106"
    assert tile_name(63.07, -151.0) == "n64w152"
    assert tile_url(40.25, -105.6, "3dep_13").endswith("/13/TIFF/current/n41w106/USGS_13_n41w106.tif")
    assert tile_url(63.07, -151.0, "3dep_2").endswith("/2/TIFF/current/n64w152/USGS_2_n64w152.tif")


@pytest.fixture
def tile(tmp_path):
    path = tmp_path / "t.tif"
    data = np.full((100, 100), 1650.0, dtype="float32")
    data[0:10, 0:10] = -999999.0
    with rasterio.open(path, "w", driver="GTiff", height=100, width=100, count=1, dtype="float32",
                       crs="EPSG:4269", transform=from_origin(-106.0, 41.0, 0.01, 0.01), nodata=-999999.0) as dst:
        dst.write(data, 1)
    return path


def opener_for(mapping):
    @contextmanager
    def _open(path):
        with rasterio.open(path) as src:
            yield src

    def opener(url):
        for key, path in mapping.items():
            if key in url:
                return _open(path)
        return None

    return opener


def test_samples_elevation(tile):
    [e] = sample_points([(40.5, -105.5)], opener_for({"/13/": tile}))
    assert (e.elevation_m, e.dem_source, e.dem_res_m) == (1650.0, "3dep_13", 10.0)


def test_nodata_is_null_not_zero(tile):
    [e] = sample_points([(40.95, -105.95)], opener_for({"/13/": tile}))
    assert (e.elevation_m, e.dem_source) == (None, "nodata")


def test_falls_back_to_2_arcsecond(tile):
    [e] = sample_points([(40.5, -105.5)], opener_for({"/2/": tile}))
    assert (e.dem_source, e.dem_res_m) == ("3dep_2", 60.0)


def test_no_tile_at_all():
    [e] = sample_points([(40.5, -105.5)], lambda url: None)
    assert (e.elevation_m, e.dem_source, e.dem_res_m) == (None, "no_tile", None)
```

- [ ] **Step 3: Run to verify failure** — FAIL.

- [ ] **Step 4: Implement** `backend/app/pipelines/dem.py`:

```python
"""USGS 3DEP elevation (public domain) sampled from remote Cloud-Optimized GeoTIFFs: only
the blocks under our points are fetched. 1/3″ first, 2″ where 1/3″ has no tile (parts of
Alaska). Nodata and missing tiles are NULL, never 0."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass

import rasterio
from rasterio.errors import RasterioIOError
from rasterio.io import DatasetReader

TILE_BASE = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation"
RES: dict[str, tuple[str, float]] = {"3dep_13": ("13", 10.0), "3dep_2": ("2", 60.0)}
GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif", "GDAL_HTTP_MAX_RETRY": "3"}

Opener = Callable[[str], AbstractContextManager[DatasetReader] | None]


@dataclass(frozen=True)
class Elevation:
    elevation_m: float | None
    dem_source: str
    dem_res_m: float | None


def tile_name(lat: float, lon: float) -> str:
    return f"n{math.floor(lat) + 1:02d}w{abs(math.floor(lon)):03d}"


def tile_url(lat: float, lon: float, source: str) -> str:
    folder, _ = RES[source]
    name = tile_name(lat, lon)
    return f"{TILE_BASE}/{folder}/TIFF/current/{name}/USGS_{folder}_{name}.tif"


def open_remote(url: str) -> AbstractContextManager[DatasetReader] | None:
    try:
        with rasterio.Env(**GDAL_ENV):
            return rasterio.open(f"/vsicurl/{url}")
    except RasterioIOError:
        return None


def _sample(src: DatasetReader, points: Sequence[tuple[float, float]]) -> list[float | None]:
    out: list[float | None] = []
    for (value,) in src.sample([(lon, lat) for lat, lon in points]):
        v = float(value)
        out.append(None if (src.nodata is not None and v == src.nodata) or not math.isfinite(v) else v)
    return out


def sample_points(points: Sequence[tuple[float, float]], opener: Opener = open_remote) -> list[Elevation]:
    result: list[Elevation | None] = [None] * len(points)
    by_tile: dict[str, list[int]] = defaultdict(list)
    for i, (lat, lon) in enumerate(points):
        by_tile[tile_name(lat, lon)].append(i)
    for indices in by_tile.values():
        pending = list(indices)
        for source in ("3dep_13", "3dep_2"):
            if not pending:
                break
            lat0, lon0 = points[pending[0]]
            handle = opener(tile_url(lat0, lon0, source))
            if handle is None:
                continue
            with handle as src:
                values = _sample(src, [points[i] for i in pending])
            _, res_m = RES[source]
            for i, v in zip(pending, values):
                result[i] = Elevation(v, source, res_m) if v is not None else Elevation(None, "nodata", None)
            pending = []
        for i in pending:
            result[i] = Elevation(None, "no_tile", None)
    return [r if r is not None else Elevation(None, "no_tile", None) for r in result]
```

A point on nodata in the 1/3″ tile is recorded `nodata` rather than retried at 2″ (the tile exists; the hole is real, e.g. water). Append `"app.pipelines.dem"` to strict mypy.

- [ ] **Step 5: Run** — `cd backend && uv run pytest tests/test_dem.py -q && uv run mypy` → PASS.
- [ ] **Step 6: Commit** — `git add backend/app/pipelines/dem.py backend/tests/test_dem.py backend/pyproject.toml && git commit -m "feat(pipelines): 3DEP remote-COG elevation sampler with 2-arc-second fallback"`

---

### Task 3: Monthly climate normals and day-of-year climatology (one paid pass)

**Files:**
- Create: `backend/app/pipelines/normals.py`, `backend/tests/test_normals.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: `open_meteo.ArchiveClient`, `LocationResponse`, `Location`, `cost_units` (plan 3); `ingest_log` (plan 1); `grid.bucket_center`.
- Produces: `NORMALS_VERSION = "n-v1"`, `YEARS = 10`, `MIN_YEARS = 8`, `ERA5_LAG_DAYS = 5`, `window(today: date) -> tuple[int, int]`, `@dataclass(frozen=True) MonthNormal(month: int, tmax_mean, tmin_mean, precip_mm, snowfall_cm, freeze_thaw_days: float | None, n_years: int)`, `monthly_normals(response: LocationResponse) -> list[MonthNormal]` (12 entries), `CLIMATOLOGY_VERSION = "c-v1"`, `DOY_HALF_WINDOW = 7`, `doy_climatology(response: LocationResponse) -> list[dict[str, float | int | None]]` (366 rows, keys = `cell_climatology` columns without bucket/period/version), `async run(engine_factory, client, buckets: Sequence[int], *, today: date, batch: int, max_units: int) -> dict[str, object]`, CLI `python -m app.pipelines.normals --max-units N [--batch 25] [--dry-run]`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_normals.py`:

```python
from datetime import date, timedelta

import pytest

from app.pipelines.normals import monthly_normals, window
from app.pipelines.open_meteo import DAILY_VARS, LocationResponse


def test_window_is_trailing_complete_years():
    assert window(date(2026, 9, 28)) == (2016, 2025)


def test_window_waits_for_era5_lag():
    assert window(date(2027, 1, 3)) == (2016, 2025)
    assert window(date(2027, 1, 6)) == (2017, 2026)


def _response(start: date, end: date, tmax=5.0, tmin=-3.0, gaps=()):
    days = [start + timedelta(d) for d in range((end - start).days + 1)]
    daily: dict[str, object] = {"time": [d.isoformat() for d in days]}
    for var in DAILY_VARS:
        daily[var] = [1.0] * len(days)
    daily["temperature_2m_max"] = [None if d in gaps else tmax for d in days]
    daily["temperature_2m_min"] = [None if d in gaps else tmin for d in days]
    return LocationResponse.model_validate({"latitude": 40.0, "longitude": -105.3, "elevation": 2400.0, "daily": daily})


def test_monthly_values():
    normals = monthly_normals(_response(date(2016, 1, 1), date(2025, 12, 31)))
    jan = normals[0]
    assert (jan.month, jan.n_years, jan.tmax_mean, jan.tmin_mean) == (1, 10, 5.0, -3.0)
    assert jan.precip_mm == pytest.approx(31.0)
    assert jan.freeze_thaw_days == pytest.approx(31.0)


def test_doy_climatology_pools_a_two_week_window_across_years():
    from app.pipelines.normals import doy_climatology

    rows = doy_climatology(_response(date(2016, 1, 1), date(2025, 12, 31)))
    assert len(rows) == 366
    jan10 = rows[9]
    assert (jan10["doy"], jan10["n_years"], jan10["tmax_mean"], jan10["tmax_p10"], jan10["tmax_p90"]) == (10, 10, 5.0, 5.0, 5.0)
    assert jan10["freeze_thaw_freq"] == 1.0 and jan10["lightning_day_freq"] is None


def test_months_with_too_few_good_years_are_null_not_zero():
    gaps = {date(y, 2, d) for y in range(2016, 2019) for d in range(1, 10)}
    normals = monthly_normals(_response(date(2016, 1, 1), date(2025, 12, 31), gaps=gaps))
    feb = normals[1]
    assert feb.n_years == 7
    assert (feb.tmax_mean, feb.precip_mm, feb.freeze_thaw_days) == (None, None, None)
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/normals.py`:

```python
"""Monthly climate normals per 0.1° bucket (amendment §3.1) from the trailing 10 complete
years of ERA5 daily data (D6). The window moves with the run date; a year is complete once
ERA5's ~5-day lag past 31 December has passed. A month needs 8 good years or it is NULL."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.pipelines.grid import bucket_center
from app.pipelines.ingest_log import find_completed, finish_run, sha256_rows, start_run
from app.pipelines.open_meteo import DAILY_VARS, ArchiveClient, Location, LocationResponse, OpenMeteoError, cost_units
from app.pipelines.validate import ValidationReport

NORMALS_VERSION = "n-v1"
SOURCE = "era5_normals"
YEARS = 10
MIN_YEARS = 8
ERA5_LAG_DAYS = 5
MIN_DAY_SHARE = 0.9


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


def monthly_normals(response: LocationResponse) -> list[MonthNormal]:
    d = response.daily
    per: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, day in enumerate(d.time):
        per[(day.year, day.month)].append(i)
    out: list[MonthNormal] = []
    for month in range(1, 13):
        tmax_y, tmin_y, precip_y, snow_y, ft_y = [], [], [], [], []
        for (year, m), idx in per.items():
            if m != month:
                continue
            good = [i for i in idx if d.temperature_2m_max[i] is not None and d.temperature_2m_min[i] is not None]
            days_in_month = ((date(year + (month == 12), month % 12 + 1, 1)) - date(year, month, 1)).days
            if len(good) < MIN_DAY_SHARE * days_in_month:
                continue
            tmax = [v for i in good if (v := d.temperature_2m_max[i]) is not None]
            tmin = [v for i in good if (v := d.temperature_2m_min[i]) is not None]
            tmax_y.append(statistics.fmean(tmax))
            tmin_y.append(statistics.fmean(tmin))
            precip_y.append(sum(v for i in idx if (v := d.precipitation_sum[i]) is not None))
            snow_y.append(sum(v for i in idx if (v := d.snowfall_sum[i]) is not None))
            ft_y.append(sum(1 for a, b in zip(tmax, tmin) if a > 0 and b < 0))
        n = len(tmax_y)
        if n < MIN_YEARS:
            out.append(MonthNormal(month, None, None, None, None, None, n))
        else:
            out.append(MonthNormal(month, statistics.fmean(tmax_y), statistics.fmean(tmin_y), statistics.fmean(precip_y),
                                   statistics.fmean(snow_y), statistics.fmean(ft_y), n))
    return out


CLIMATOLOGY_VERSION = "c-v1"
DOY_HALF_WINDOW = 7
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
    so percentiles are stable; days with no valid sample stay NULL."""
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
        ft_sample = ft[mask & ~np.isnan(ft)]
        row["freeze_thaw_freq"] = _stat(ft_sample, "mean")
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


async def run(
    engine_factory: Callable[[], AsyncEngine],
    client: ArchiveClient,
    buckets: Sequence[int],
    *,
    today: date,
    batch: int,
    max_units: int,
) -> dict[str, object]:
    start_year, end_year = window(today)
    start, end = date(start_year, 1, 1), date(end_year, 12, 31)
    days = (end - start).days + 1
    engine = engine_factory()
    spent = done = skipped = failed = 0
    stopped = False
    try:
        ordered = sorted(set(buckets))
        for i in range(0, len(ordered), batch):
            group = ordered[i : i + batch]
            sha = sha256_rows([(b,) for b in group] + [("v", NORMALS_VERSION)])
            async with engine.begin() as conn:
                if await find_completed(conn, source=SOURCE, window_start=start, window_end=end, content_sha256=sha):
                    skipped += 1
                    continue
            units = cost_units(len(group), len(DAILY_VARS), days)
            if spent + units > max_units:
                stopped = True
                break
            report = ValidationReport(SOURCE)
            try:
                responses = client.fetch([Location(b, *bucket_center(b)) for b in group], start, end)
            except OpenMeteoError as exc:
                failed += 1
                async with engine.begin() as conn:
                    run_id = await start_run(conn, source=SOURCE, window_start=start, window_end=end, content_sha256=sha)
                    await finish_run(conn, run_id, status="failed", report=report, rows_upserted=0, problems=[str(exc)])
                continue
            spent += units
            rows = []
            clim_rows: list[dict[str, object]] = []
            for b, response in zip(group, responses):
                clim_rows += [c | {"b": b, "ps": start_year, "pe": end_year, "cv": CLIMATOLOGY_VERSION}
                              for c in doy_climatology(response)]
                for n in monthly_normals(response):
                    if n.n_years >= MIN_YEARS:
                        report.accept()
                    else:
                        report.quarantine(f"{b}:{n.month}", "too_few_years", n=n.n_years)
                    rows.append({"b": b, "month": n.month, "tmax_mean": n.tmax_mean, "tmin_mean": n.tmin_mean,
                                 "precip_mm": n.precip_mm, "snowfall_cm": n.snowfall_cm, "freeze_thaw_days": n.freeze_thaw_days,
                                 "n_years": n.n_years, "ps": start_year, "pe": end_year, "elev": response.elevation,
                                 "v": NORMALS_VERSION})
            async with engine.begin() as conn:
                run_id = await start_run(conn, source=SOURCE, window_start=start, window_end=end, content_sha256=sha)
                await conn.execute(UPSERT, [r | {"run": run_id} for r in rows])
                await conn.execute(CLIM_UPSERT, [c | {"run": run_id} for c in clim_rows])
                await finish_run(conn, run_id, status="ok", report=report, rows_upserted=len(rows), cost_units=units)
            done += 1
    finally:
        await engine.dispose()
    return {"window": [start_year, end_year], "done": done, "skipped": skipped, "failed": failed,
            "units_spent": spent, "stopped_for_budget": stopped}


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.config import settings
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    engine = ingest_engine()
    try:
        async with engine.connect() as conn:
            buckets = [int(b) for (b,) in (await conn.execute(text("SELECT DISTINCT grid_bucket FROM feature_points"))).all()]
    finally:
        await engine.dispose()
    today = utc_today()
    start_year, end_year = window(today)
    total = cost_units(len(buckets), len(DAILY_VARS), (date(end_year, 12, 31) - date(start_year, 1, 1)).days + 1)
    if args.dry_run:
        return {"mode": "dry_run", "buckets": len(buckets), "window": [start_year, end_year], "units_total": total}
    return await run(ingest_engine, ArchiveClient(settings.OPEN_METEO_API_KEY), buckets, today=today,
                     batch=args.batch, max_units=args.max_units)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-units", type=int, required=True)
    parser.add_argument("--batch", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    print(json.dumps(asyncio.run(_main(parser.parse_args())), sort_keys=True))
```

`good` already guarantees both values are present, so the two lists stay index-aligned for the freeze-thaw count. Append `"app.pipelines.normals"` to strict mypy.

- [ ] **Step 4: Run** — PASS (`test_months_with_too_few_good_years…`: 3 years × 9 missing February days leaves < 90% good days in 2016–2018, so 7 qualifying years).
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/normals.py backend/tests/test_normals.py backend/pyproject.toml && git commit -m "feat(pipelines): monthly normals and day-of-year climatology from one ERA5 pass"`

---

### Task 4: EPQS cross-check

**Files:**
- Create: `backend/app/pipelines/epqs.py`, `backend/tests/test_epqs.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `EPQS_URL = "https://epqs.nationalmap.gov/v1/json"`, `TOLERANCE_M = 5.0`, `class EpqsClient(transport=None, pause_s=0.2)` with `elevation(lat: float, lon: float) -> float | None`, `compare(pairs: Sequence[tuple[float | None, float | None]]) -> dict[str, float | int]` (`compared`, `within`, `max_abs_diff`, `passed` as 0/1).

- [ ] **Step 1: Failing tests** — `backend/tests/test_epqs.py`:

```python
import httpx

from app.pipelines.epqs import EpqsClient, compare


def test_parses_value_and_treats_sentinel_as_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        x = float(request.url.params["x"])
        value = "-1000000" if x > 0 else "1650.25"
        return httpx.Response(200, json={"value": value, "location": {"x": x}})

    client = EpqsClient(transport=httpx.MockTransport(handler))
    assert client.elevation(40.0, -105.3) == 1650.25
    assert client.elevation(40.0, 5.0) is None


def test_compare_requires_every_comparable_point_within_tolerance():
    assert compare([(1650.0, 1652.0), (None, 10.0), (100.0, None)]) == {
        "compared": 1, "within": 1, "max_abs_diff": 2.0, "passed": 1}
    assert compare([(1650.0, 1660.0), (10.0, 10.0)])["passed"] == 0
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/epqs.py`:

```python
"""USGS Elevation Point Query Service: one point per request, used only for the 200-point
cross-check of the 3DEP sampler (spec: |Δ| < 5 m). Replaces Open-Elevation as the check."""

from __future__ import annotations

import time
from collections.abc import Sequence

import httpx

EPQS_URL = "https://epqs.nationalmap.gov/v1/json"
TOLERANCE_M = 5.0
NODATA = -1_000_000.0


class EpqsClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, pause_s: float = 0.2) -> None:
        self._client = httpx.Client(transport=transport, timeout=30.0)
        self._pause = pause_s if transport is None else 0.0

    def elevation(self, lat: float, lon: float) -> float | None:
        response = self._client.get(EPQS_URL, params={"x": lon, "y": lat, "units": "Meters", "wkid": 4326, "includeDate": "false"})
        time.sleep(self._pause)
        if response.status_code != 200:
            return None
        value = response.json().get("value")
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return None if v <= NODATA else v


def compare(pairs: Sequence[tuple[float | None, float | None]]) -> dict[str, float | int]:
    diffs = [abs(a - b) for a, b in pairs if a is not None and b is not None]
    within = sum(1 for d in diffs if d < TOLERANCE_M)
    return {
        "compared": len(diffs),
        "within": within,
        "max_abs_diff": max(diffs) if diffs else 0.0,
        "passed": int(bool(diffs) and within == len(diffs)),
    }
```

Append `"app.pipelines.epqs"` to strict mypy.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/epqs.py backend/tests/test_epqs.py backend/pyproject.toml && git commit -m "feat(pipelines): EPQS cross-check client"`

---

### Task 5: Static-features job (points, elevation, route table)

**Files:**
- Create: `backend/app/pipelines/static_features.py`, `backend/tests/test_static_features.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: Tasks 1–4, `grid.grid_bucket`, `match.h3_neighbourhood` is not used; `h3.latlng_to_cell` directly.
- Produces: `FEATURE_VERSION = "f-v1"`, `point_key(lat: float, lon: float) -> str`, `async upsert_points(conn, *, run_id) -> int` (from `mp_locations` and `canonical_areas` with coordinates), `async fill_elevation(conn, *, run_id, sampler: Callable[[Sequence[tuple[float, float]]], list[Elevation]], limit: int) -> dict[str, int]`, `async build_route_features(conn) -> int`, `async epqs_check(conn, client: EpqsClient, *, n: int = 200, seed: int = 42) -> dict[str, float | int]`, CLI `python -m app.pipelines.static_features points|elevation [--limit N]|routes|epqs-check`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_static_features.py`:

```python
import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.dem import Elevation
from app.pipelines.static_features import build_route_features, fill_elevation, point_key, upsert_points
from tests.pgtest import migrated_db, requires_pg, sa_url

A_OK = "00000000-0000-0000-0000-00000000000a"
A_NONE = "00000000-0000-0000-0000-00000000000b"
SEED = f"""
INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (900000101, 'Fixture Crag', 40.123456, -105.654321);
INSERT INTO canonical_areas (area_id, name, path, lat, lon, coord_precision, source, redistributable) VALUES
  ('{A_OK}', 'OB Crag', '0000000000000000000000000000000a', 40.123456, -105.654321, 'area_centroid', 'openbeta', true),
  ('{A_NONE}', 'OB No Coords', '0000000000000000000000000000000b', NULL, NULL, 'none', 'openbeta', true);
INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable, pitches) VALUES
  ('00000000-0000-0000-0000-0000000000c1', '{A_OK}', 'R1', '{{trad}}', 'trad', 'rt-v1', false, true, 'openbeta', true, 2),
  ('00000000-0000-0000-0000-0000000000c2', '{A_NONE}', 'R2', '{{sport}}', 'sport', 'rt-v1', false, false, 'openbeta', true, NULL);
"""


def test_point_key():
    assert point_key(40.1234567, -105.6543211) == "40.12346:-105.65432"


def _run(name, fn):
    """Run fn(conn) in one transaction; a SELECT result is materialized before the connection closes."""
    async def go():
        engine = create_async_engine(sa_url(name))
        try:
            async with engine.begin() as conn:
                result = await fn(conn)
                return result.all() if hasattr(result, "all") else result
        finally:
            await engine.dispose()

    return asyncio.run(go())


@requires_pg
def test_points_are_shared_elevation_fills_and_routes_get_rows():
    with migrated_db(seed_sql=SEED) as name:
        run_id = uuid.uuid4()
        assert _run(name, lambda c: upsert_points(c, run_id=run_id)) == 1
        stats = _run(name, lambda c: fill_elevation(
            c, run_id=run_id, sampler=lambda pts: [Elevation(2400.0, "3dep_13", 10.0)] * len(pts), limit=100))
        assert stats == {"sampled": 1, "null": 0}
        assert _run(name, build_route_features) == 2
        rows = _run(name, lambda c: c.execute(text(
            "SELECT pitches, elevation_m, lat, grid_bucket IS NOT NULL, coord_precision FROM route_static_features ORDER BY route_id")))
        assert [tuple(r) for r in rows] == [(2, 2400.0, 40.12346, True, "area_centroid"), (None, None, None, False, "none")]


@requires_pg
def test_routes_without_coordinates_get_explicit_nulls():
    with migrated_db(seed_sql=SEED) as name:
        _run(name, lambda c: upsert_points(c, run_id=uuid.uuid4()))
        _run(name, build_route_features)
        rows = _run(name, lambda c: c.execute(text(
            "SELECT point_key, elevation_m, coord_precision FROM route_static_features "
            "WHERE route_id = '00000000-0000-0000-0000-0000000000c2'")))
        assert [tuple(r) for r in rows] == [(None, None, "none")]
```

The route's `lat` is the point's (5-decimal) latitude, which is why it reads `40.12346`.

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/static_features.py`:

```python
"""Static features per distinct point (D7) and the per-route table Phase 3 reads."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import uuid
from collections.abc import Callable, Sequence

import h3
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.dem import Elevation, sample_points
from app.pipelines.epqs import EpqsClient, compare
from app.pipelines.grid import grid_bucket
from app.pipelines.validate import coord_problem

FEATURE_VERSION = "f-v1"


def point_key(lat: float, lon: float) -> str:
    return f"{lat:.5f}:{lon:.5f}"


async def upsert_points(conn: AsyncConnection, *, run_id: uuid.UUID) -> int:
    coords = {
        (round(float(la), 5), round(float(lo), 5))
        for la, lo in (await conn.execute(text(
            "SELECT latitude, longitude FROM mp_locations WHERE latitude IS NOT NULL AND longitude IS NOT NULL "
            "UNION SELECT lat, lon FROM canonical_areas WHERE lat IS NOT NULL AND lon IS NOT NULL"))).all()
    }
    rows = [
        {"k": point_key(la, lo), "lat": la, "lon": lo, "b": grid_bucket(la, lo),
         "r5": h3.latlng_to_cell(la, lo, 5), "r7": h3.latlng_to_cell(la, lo, 7), "v": FEATURE_VERSION, "run": run_id}
        for la, lo in coords
        if coord_problem(la, lo) is None
    ]
    if rows:
        await conn.execute(text(
            "INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, feature_version, run_id) "
            "VALUES (:k, :lat, :lon, :b, :r5, :r7, :v, :run) ON CONFLICT (point_key) DO NOTHING"), rows)
    return len(rows)


async def fill_elevation(
    conn: AsyncConnection,
    *,
    run_id: uuid.UUID,
    sampler: Callable[[Sequence[tuple[float, float]]], list[Elevation]] = sample_points,
    limit: int,
) -> dict[str, int]:
    todo = [(str(k), float(la), float(lo)) for k, la, lo in (await conn.execute(text(
        "SELECT point_key, lat, lon FROM feature_points WHERE dem_source IS NULL ORDER BY point_key LIMIT :n"),
        {"n": limit})).all()]
    if not todo:
        return {"sampled": 0, "null": 0}
    values = sampler([(la, lo) for _, la, lo in todo])
    await conn.execute(text(
        "UPDATE feature_points SET elevation_m = :e, dem_source = :s, dem_res_m = :r, updated_at = now(), run_id = :run "
        "WHERE point_key = :k"),
        [{"e": v.elevation_m, "s": v.dem_source, "r": v.dem_res_m, "run": run_id, "k": k} for (k, _, _), v in zip(todo, values)])
    return {"sampled": len(todo), "null": sum(1 for v in values if v.elevation_m is None)}


BUILD_SQL = f"""
INSERT INTO route_static_features (route_id, source, area_path, type_group, point_key, lat, aspect_deg, slope_deg,
  elevation_m, lithology, pitches, length_m, h3_r5, grid_bucket, dem_res_m, aspect_confidence, coord_precision,
  lithology_source, feature_version, updated_at)
SELECT r.route_id, r.source, a.path, r.type_group, p.point_key, p.lat, p.aspect_deg, p.slope_deg, p.elevation_m,
       p.lithology, r.pitches, r.length_m, p.h3_r5, p.grid_bucket, p.dem_res_m, p.aspect_confidence,
       CASE WHEN p.point_key IS NULL THEN 'none' ELSE a.coord_precision END, p.lithology_source, '{FEATURE_VERSION}', now()
FROM canonical_routes r
JOIN canonical_areas a ON a.area_id = r.area_id
LEFT JOIN feature_points p ON p.point_key = to_char(a.lat, 'FM990.00000') || ':' || to_char(a.lon, 'FM9990.00000')
WHERE r.retired_at IS NULL AND NOT r.is_boulder
ON CONFLICT (route_id) DO UPDATE SET source = EXCLUDED.source, area_path = EXCLUDED.area_path,
  type_group = EXCLUDED.type_group, point_key = EXCLUDED.point_key, lat = EXCLUDED.lat, aspect_deg = EXCLUDED.aspect_deg,
  slope_deg = EXCLUDED.slope_deg, elevation_m = EXCLUDED.elevation_m, lithology = EXCLUDED.lithology,
  pitches = EXCLUDED.pitches, length_m = EXCLUDED.length_m, h3_r5 = EXCLUDED.h3_r5, grid_bucket = EXCLUDED.grid_bucket,
  dem_res_m = EXCLUDED.dem_res_m, aspect_confidence = EXCLUDED.aspect_confidence, coord_precision = EXCLUDED.coord_precision,
  lithology_source = EXCLUDED.lithology_source, feature_version = EXCLUDED.feature_version, updated_at = now()
"""


async def build_route_features(conn: AsyncConnection) -> int:
    await conn.execute(text(
        "DELETE FROM route_static_features f USING canonical_routes r WHERE r.route_id = f.route_id "
        "AND (r.retired_at IS NOT NULL OR r.is_boulder)"))
    return (await conn.execute(text(BUILD_SQL))).rowcount


async def epqs_check(conn: AsyncConnection, client: EpqsClient, *, n: int = 200, seed: int = 42) -> dict[str, float | int]:
    points = [(float(la), float(lo), float(e)) for la, lo, e in (await conn.execute(text(
        "SELECT lat, lon, elevation_m FROM feature_points WHERE elevation_m IS NOT NULL"))).all()]
    sample = random.Random(seed).sample(points, min(n, len(points)))
    return compare([(e, client.elevation(la, lo)) for la, lo, e in sample])


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.pipelines.ingest_log import finish_run, start_run
    from app.pipelines.validate import ValidationReport

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source=f"static_features:{args.step}", window_start=None, window_end=None, content_sha256=None)
            if args.step == "points":
                result: dict[str, object] = {"points": await upsert_points(conn, run_id=run_id)}
            elif args.step == "elevation":
                result = dict(await fill_elevation(conn, run_id=run_id, limit=args.limit))
            elif args.step == "routes":
                result = {"routes": await build_route_features(conn)}
            else:
                result = dict(await epqs_check(conn, EpqsClient()))
            await finish_run(conn, run_id, status="ok", report=ValidationReport(args.step), rows_upserted=0,
                             problems=[json.dumps(result, default=str)])
    finally:
        await engine.dispose()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["points", "elevation", "routes", "epqs-check"])
    parser.add_argument("--limit", type=int, default=5000)
    print(json.dumps(asyncio.run(_main(parser.parse_args())), sort_keys=True, default=str))
```

The point join formats `a.lat`/`a.lon` with the same 5-decimal rounding as `point_key` (`to_char` rounds half away from zero, as does Python's `f"{x:.5f}"` on these values in practice; the DB test pins the round trip for a 6-decimal coordinate). Append `"app.pipelines.static_features"` to strict mypy.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/static_features.py backend/tests/test_static_features.py backend/pyproject.toml && git commit -m "feat(pipelines): per-point features, elevation fill, route_static_features build, EPQS check"`

---

### Task 6: Acceptance cells, workflow, PR 2b-2a docs

**Files:**
- Create: `backend/tests/verify/test_phase2b_features.py`, `.github/workflows/data-static-features.yml`
- Modify: `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`

- [ ] **Step 1: Cells** — `backend/tests/verify/test_phase2b_features.py`:

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


def test_every_point_has_an_elevation_attempt_and_coverage_is_reported():
    [row] = fetch("SELECT count(*) FILTER (WHERE dem_source IS NULL) AS pending, "
                  "count(*) FILTER (WHERE elevation_m IS NULL) AS missing, count(*) AS n FROM feature_points")
    print(dict(row))
    assert row["pending"] == 0
    assert row["missing"] == 0, "100% elevation coverage (spec 2b-2); inspect dem_source for the misses"


def test_epqs_check_passed():
    [row] = fetch("SELECT (validation_report->'problems'->>0)::jsonb->>'passed' AS passed FROM source_ingest_log "
                  "WHERE source = 'static_features:epqs-check' AND status = 'ok' ORDER BY finished_at DESC LIMIT 1")
    assert row["passed"] == "1"


def test_every_point_bucket_has_twelve_normal_months():
    [row] = fetch("SELECT count(*) AS n FROM (SELECT DISTINCT grid_bucket FROM feature_points) b "
                  "WHERE (SELECT count(*) FROM cell_climate_normals c WHERE c.grid_bucket = b.grid_bucket) <> 12")
    assert row["n"] == 0


def test_every_point_bucket_has_a_full_year_of_climatology():
    [row] = fetch("SELECT count(*) AS n FROM (SELECT DISTINCT grid_bucket FROM feature_points) b "
                  "WHERE (SELECT count(*) FROM cell_climatology c WHERE c.grid_bucket = b.grid_bucket) <> 366")
    assert row["n"] == 0


def test_every_scorable_route_has_a_feature_row():
    [row] = fetch("SELECT count(*) AS n FROM scorable_routes s LEFT JOIN route_static_features f USING (route_id) "
                  "WHERE f.route_id IS NULL")
    assert row["n"] == 0
```

- [ ] **Step 2: Workflow** `.github/workflows/data-static-features.yml` (same header, permissions, env, checkout and uv steps as `data-openbeta.yml`; `OPEN_METEO_API_KEY: ${{ secrets.OPEN_METEO_API_KEY }}` added to `env`):

```yaml
name: data-static-features

on:
  schedule:
    - cron: "47 8 * * 1"   # Mondays, after the OpenBeta load
  workflow_dispatch:
    inputs:
      normals:
        description: "Recompute climate normals (yearly, January; costs Open-Meteo units)"
        type: boolean
        default: false
      max_units:
        description: "Open-Meteo unit cap for this run"
        default: "150000"

permissions:
  contents: read
  issues: write

jobs:
  features:
    runs-on: ubuntu-latest
    timeout-minutes: 300
    defaults:
      run:
        working-directory: backend
    env:
      DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      INGEST_DATABASE_URL: ${{ secrets.INGEST_DATABASE_URL }}
      HEALTHCHECKS_PING_KEY: ${{ secrets.HEALTHCHECKS_PING_KEY }}
      OPEN_METEO_API_KEY: ${{ secrets.OPEN_METEO_API_KEY }}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          version: "0.11.3"
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: backend/uv.lock
      - run: uv sync --frozen --group pipelines
      - run: uv run python -m app.pipelines.static_features points
      - run: uv run python -m app.pipelines.static_features elevation --limit 50000
      - if: ${{ inputs.normals }}
        run: uv run python -m app.pipelines.normals --max-units "${{ inputs.max_units }}"
      - run: uv run python -m app.pipelines.static_features routes
      - name: Open an issue on failure
        if: failure()
        working-directory: .
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh issue create --title "data-static-features failed ($(date -u +%F))" --body "Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

New points (from a new OpenBeta area) need normals only when they introduce a new grid bucket; `normals` skips completed bucket batches, so a January dispatch with `normals: true` both refreshes the window and fills new buckets `[assumes D8]`.

- [ ] **Step 3: Docs** — CHANGELOG "Phase 2b MVP-1 features (PR 2b-2a)"; CLAUDE.md commands for `static_features` and `normals`; DEPLOYMENT.md "Data workflows": `data-static-features.yml`, secret `OPEN_METEO_API_KEY`; DATABASE_STRUCTURE: the three tables. Record in `docs/superpowers/specs/2026-09-28-phase3-amendment-similarity-confidence.md` nothing (specs are not edited by this plan); instead note in the PR description that amendment question 5 is answered by this PR.
- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/` → green.
- [ ] **Step 5: Commit** — `git add backend/tests/verify/test_phase2b_features.py .github/workflows/data-static-features.yml CHANGELOG.md CLAUDE.md DEPLOYMENT.md data/DATABASE_STRUCTURE.md && git commit -m "feat(ops): static-features workflow and MVP-1 feature acceptance cells"`

---

# PR 2b-2b — `feat/p2b-terrain-lithology` (v2.2 features)

### Task 7: Horn slope/aspect and crag aspect

**Files:**
- Create: `backend/app/pipelines/terrain.py`, `backend/tests/test_terrain.py`
- Modify: `backend/app/pipelines/static_features.py` (step `terrain`), `backend/pyproject.toml`

**Interfaces:**
- Produces: `STEEP_DEG = 40.0`, `RADIUS_M = 200.0`, `MIN_CELLS = 5`, `horn(z: NDArray[np.float64], dx: float, dy: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]` (slope°, aspect° clockwise from north, interior cells), `circular_mean(deg: Sequence[float]) -> tuple[float, float]` (mean°, resultant length), `crag_aspect(z, dx, dy, center_rc: tuple[int, int]) -> tuple[float | None, float | None, float | None]` (aspect°, confidence, max slope° within radius), `async fill_terrain(conn, *, run_id, window_reader, limit) -> dict[str, int]` with `window_reader(lat, lon, radius_m) -> tuple[NDArray, float, float, tuple[int, int]] | None` defaulting to a 3DEP COG window read (`dem.read_window`).

- [ ] **Step 1: Failing tests** — `backend/tests/test_terrain.py`:

```python
import numpy as np
import pytest

from app.pipelines.terrain import circular_mean, crag_aspect, horn


def plane(rows: int, cols: int, dz_south: float, dz_east: float, cell: float) -> np.ndarray:
    r = np.arange(rows)[:, None] * dz_south * cell
    c = np.arange(cols)[None, :] * dz_east * cell
    return (r + c).astype(np.float64)


def test_45_degree_north_facing_plane():
    slope, aspect = horn(plane(7, 7, 1.0, 0.0, 10.0), 10.0, 10.0)
    assert slope[3, 3] == pytest.approx(45.0, abs=0.01)
    assert aspect[3, 3] == pytest.approx(0.0, abs=1.0)


def test_east_facing_plane():
    _, aspect = horn(plane(7, 7, 0.0, -1.0, 10.0), 10.0, 10.0)
    assert aspect[3, 3] == pytest.approx(90.0, abs=1.0)


def test_circular_mean_wraps_north():
    mean, r = circular_mean([350.0, 10.0])
    assert min(mean, 360 - mean) == pytest.approx(0.0, abs=1e-6) and r == pytest.approx(np.cos(np.radians(10)))


def test_crag_aspect_needs_five_steep_cells():
    flat = plane(41, 41, 0.1, 0.0, 10.0)
    assert crag_aspect(flat, 10.0, 10.0, (20, 20))[:2] == (None, None)
    steep = plane(41, 41, 1.5, 0.0, 10.0)
    aspect, confidence, _ = crag_aspect(steep, 10.0, 10.0, (20, 20))
    assert aspect == pytest.approx(0.0, abs=1.0) and confidence == pytest.approx(1.0, abs=1e-6)
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/terrain.py`:

```python
"""Slope and aspect by Horn's method; crag aspect is the circular mean of cells steeper
than 40° within 200 m, with the mean resultant length as confidence (spec §Static
features). Fewer than 5 steep cells → NULL (route coordinates are crag-level)."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

STEEP_DEG = 40.0
RADIUS_M = 200.0
MIN_CELLS = 5


def horn(z: NDArray[np.float64], dx: float, dy: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    # Rows run north→south (raster order), so dz/dy is "toward south" as in the ArcGIS form.
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * dx)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * dy)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    raw = np.degrees(np.arctan2(dzdy, -dzdx))
    aspect = np.where(raw < 0, 90.0 - raw, np.where(raw > 90.0, 360.0 - raw + 90.0, 90.0 - raw)) % 360.0
    pad = np.full(z.shape, np.nan)
    s, asp = pad.copy(), pad.copy()
    s[1:-1, 1:-1], asp[1:-1, 1:-1] = slope, aspect
    return s, asp


def circular_mean(deg: Sequence[float]) -> tuple[float, float]:
    rad = np.radians(np.asarray(deg, dtype=np.float64))
    sx, cx = float(np.mean(np.sin(rad))), float(np.mean(np.cos(rad)))
    return math.degrees(math.atan2(sx, cx)) % 360.0, math.hypot(sx, cx)


def crag_aspect(
    z: NDArray[np.float64], dx: float, dy: float, center_rc: tuple[int, int]
) -> tuple[float | None, float | None, float | None]:
    slope, aspect = horn(z, dx, dy)
    rows, cols = np.indices(z.shape)
    dist = np.hypot((rows - center_rc[0]) * dy, (cols - center_rc[1]) * dx)
    inside = dist <= RADIUS_M
    steep = inside & (slope > STEEP_DEG)
    max_slope = float(np.nanmax(np.where(inside, slope, np.nan))) if np.any(inside & ~np.isnan(slope)) else None
    if int(np.count_nonzero(steep)) < MIN_CELLS:
        return None, None, max_slope
    mean, r = circular_mean(aspect[steep].tolist())
    return mean, r, max_slope
```

Check of the east-facing test: `dz_east = -1` means elevation falls toward the east; `dzdx = -1`, `dzdy = 0`, `raw = atan2(0, 1) = 0` → `90 - 0 = 90` (east) ✓. North test: `dzdy = +1` → `raw = 90` → `0` ✓.

Add to `backend/app/pipelines/dem.py`:

```python
def read_window(lat: float, lon: float, radius_m: float, opener: Opener = open_remote) -> tuple[NDArray[np.float64], float, float, tuple[int, int]] | None:
    """A DEM window around a point, its cell size in meters, and the point's (row, col)."""
    for source in ("3dep_13", "3dep_2"):
        handle = opener(tile_url(lat, lon, source))
        if handle is None:
            continue
        with handle as src:
            row, col = src.index(lon, lat)
            res_x, res_y = abs(src.res[0]), abs(src.res[1])
            dx = res_x * 111_320.0 * math.cos(math.radians(lat))
            dy = res_y * 110_574.0
            half_r, half_c = int(radius_m / dy) + 2, int(radius_m / dx) + 2
            win = rasterio.windows.Window(col - half_c, row - half_r, 2 * half_c + 1, 2 * half_r + 1)
            z = src.read(1, window=win, boundless=True, fill_value=src.nodata).astype(np.float64)
            if src.nodata is not None:
                z[z == src.nodata] = np.nan
            return z, dx, dy, (half_r, half_c)
    return None
```

(with `import numpy as np`, `import rasterio.windows`, `from numpy.typing import NDArray` added to `dem.py`). In `static_features.py` add step `terrain`: select up to `--limit` points with `slope_deg IS NULL AND dem_source IN ('3dep_13','3dep_2')`, call `read_window` and `crag_aspect`, and `UPDATE feature_points SET slope_deg = :max_slope, aspect_deg = :aspect, aspect_confidence = :conf` (NaN cells from nodata are ignored by `nanmax` and fail the steep test). Append `"app.pipelines.terrain"` to strict mypy.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/terrain.py backend/app/pipelines/dem.py backend/app/pipelines/static_features.py backend/tests/test_terrain.py backend/pyproject.toml && git commit -m "feat(pipelines): Horn slope/aspect and crag aspect with confidence"`

---

### Task 8: Macrostrat lithology `[assumes D15]`

**Files:**
- Create: `backend/app/pipelines/lithology.py`, `backend/tests/test_lithology.py`, `backend/tests/fixtures/macrostrat_sample.json`
- Modify: `backend/app/pipelines/static_features.py` (step `lithology`), `backend/pyproject.toml`, `CHANGELOG.md`

**Interfaces:**
- Produces: `MACROSTRAT_URL = "https://macrostrat.org/api/v2/geologic_units/map"`, `CLASSES: tuple[str, ...]` (`granitic, volcanic, metamorphic, sandstone, carbonate, conglomerate, fine_sedimentary, unconsolidated, ice, other`), `normalize_lithology(text: str | None) -> str | None`, `class MacrostratClient(transport=None, pause_s=0.2)` with `lithology(lat: float, lon: float) -> str | None` (≤5 requests/s), fixture-driven tests.

- [ ] **Step 1: Record the fixture (agent)** — `curl -s 'https://macrostrat.org/api/v2/geologic_units/map?lat=40.37&lng=-105.52&scale=large' | python3 -m json.tool > backend/tests/fixtures/macrostrat_sample.json`; confirm the unit objects under `success.data` carry a `lith` text field (Macrostrat is CC-BY 4.0; a one-point fixture with attribution in the test docstring is fine). If the field is named differently, adjust `_lith_text` only.

- [ ] **Step 2: Failing tests** — `backend/tests/test_lithology.py`:

```python
"""Fixture: Macrostrat (CC-BY 4.0), https://macrostrat.org."""

import json
from pathlib import Path

import httpx
import pytest

from app.pipelines.lithology import MacrostratClient, normalize_lithology

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "macrostrat_sample.json").read_text())


@pytest.mark.parametrize("text,cls", [
    ("Major:{granite}, Minor:{pegmatite}", "granitic"), ("sandstone, shale", "sandstone"),
    ("limestone", "carbonate"), ("basalt flows", "volcanic"), ("biotite gneiss", "metamorphic"),
    ("alluvium", "unconsolidated"), ("glacier ice", "ice"), ("mudstone", "fine_sedimentary"), (None, None), ("", None),
])
def test_normalizer(text, cls):
    assert normalize_lithology(text) == cls


def test_client_reads_the_fixture():
    client = MacrostratClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=FIXTURE)))
    assert client.lithology(40.37, -105.52) in {"granitic", "metamorphic", "other"}


def test_empty_result_is_none_not_other():
    client = MacrostratClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"success": {"data": []}})))
    assert client.lithology(40.0, -105.0) is None
```

- [ ] **Step 3: Implement** `backend/app/pipelines/lithology.py`:

```python
"""Macrostrat point lithology (CC-BY 4.0), normalized to ~10 classes. No result is NULL
with lithology_source 'none' (D15); nothing is guessed."""

from __future__ import annotations

import time

import httpx

MACROSTRAT_URL = "https://macrostrat.org/api/v2/geologic_units/map"
CLASSES = ("granitic", "volcanic", "metamorphic", "sandstone", "carbonate", "conglomerate",
           "fine_sedimentary", "unconsolidated", "ice", "other")
# Order matters: the first class whose keyword appears wins.
KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ice", ("glacier", " ice")),
    ("granitic", ("granit", "granodiorite", "tonalite", "monzonite", "diorite", "pegmatite", "syenite")),
    ("volcanic", ("basalt", "andesite", "rhyolite", "dacite", "tuff", "volcanic")),
    ("metamorphic", ("gneiss", "schist", "quartzite", "marble", "slate", "phyllite", "metamorphic")),
    ("conglomerate", ("conglomerate", "breccia")),
    ("sandstone", ("sandstone", "arkose")),
    ("carbonate", ("limestone", "dolomite", "dolostone", "carbonate")),
    ("fine_sedimentary", ("shale", "mudstone", "siltstone", "claystone")),
    ("unconsolidated", ("alluvium", "colluvium", "till", "sand", "gravel", "unconsolidated")),
)


def normalize_lithology(text: str | None) -> str | None:
    t = f" {(text or '').lower()} "
    if not t.strip():
        return None
    for cls, words in KEYWORDS:
        if any(w in t for w in words):
            return cls
    return "other"


class MacrostratClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, pause_s: float = 0.2) -> None:
        self._client = httpx.Client(transport=transport, timeout=30.0)
        self._pause = pause_s if transport is None else 0.0

    @staticmethod
    def _lith_text(unit: dict[str, object]) -> str | None:
        value = unit.get("lith")
        return value if isinstance(value, str) else None

    def lithology(self, lat: float, lon: float) -> str | None:
        response = self._client.get(MACROSTRAT_URL, params={"lat": lat, "lng": lon, "scale": "large"})
        time.sleep(self._pause)
        if response.status_code != 200:
            return None
        units = (response.json().get("success") or {}).get("data") or []
        for unit in units:
            if isinstance(unit, dict) and (cls := normalize_lithology(self._lith_text(unit))) is not None:
                return cls
        return None
```

In `static_features.py` add step `lithology`: up to `--limit` points with `lithology_source IS NULL`; set `lithology` and `lithology_source = 'macrostrat'` when a class is found, else `lithology = NULL, lithology_source = 'none'`. CHANGELOG "Phase 2b terrain and lithology (PR 2b-2b)". Append `"app.pipelines.lithology"` to strict mypy.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/lithology.py backend/app/pipelines/static_features.py backend/tests/test_lithology.py backend/tests/fixtures/macrostrat_sample.json backend/pyproject.toml CHANGELOG.md && git commit -m "feat(pipelines): Macrostrat lithology normalized to ten classes (misses stay NULL)"`

---

# PR 2b-2c — `feat/p2b-r10-legacy` `[assumes D16]`

### Task 9: R10 relink job

**Files:**
- Create: `backend/app/data/repair/legacy_links.py`, `backend/tests/test_legacy_links.py`
- Modify: `backend/app/data/repair/framework.py`, `backend/app/data/repair/__main__.py`, `backend/db/roles/grants_phase2.sql`, `backend/pyproject.toml`

**Interfaces:**
- Consumes: `framework.apply_changes`, `match.climb_score`, `match.area_score`, `match.decide`, `internal.mp_route_links`/`mp_area_links` (plan 4).
- Produces: `R10_VERSION = "r10-v1"`, `@dataclass(frozen=True) Link(accident_id: int, route_id: uuid.UUID | None, area_id: uuid.UUID | None, method: str, score: float | None)`, `async plan_links(conn) -> tuple[list[Link], list[int]]` (links, accident ids with no target), `async run_r10(conn, args) -> dict[str, object]` registered as repair step `r10`.

Linking rules (in order, first hit wins):
1. `accidents.mp_route_id` → `internal.mp_route_links` → route (`method='mp_route_link'`, score 1.0).
2. legacy `route_id = mp_route_id` rows (the 421): same as 1 through that id.
3. legacy `routes` row with a numeric `routes.mp_route_id` → 1 through it (`legacy_mp_id`).
4. legacy `routes` row with coordinates → climb candidates in canonical areas within the H3 r7 neighbourhood: `decide` on `climb_score(name, grade, None, …)`; `link` → route (`legacy_match`); otherwise the best area by `area_score` ≥ 0.80 → area link (`legacy_area`).
5. `accidents.mp_route_id`'s MP location → `mp_area_links` → area (`mp_area_link`).
6. Nothing → no link row; the accident id is reported (it keeps its own coordinates and still counts spatially).

After linking, R10 nulls `route_id` and `mountain_id` through `apply_changes` (rule `r10-v1`), so the old values live in `internal.accident_revisions` and in the pre-drop dump.

- [ ] **Step 1: Failing tests** — `backend/tests/test_legacy_links.py`:

```python
import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair.legacy_links import run_r10
from tests.pgtest import migrated_db, requires_pg, sa_url

AREA = "00000000-0000-0000-0000-00000000000a"
ROUTE = "00000000-0000-0000-0000-0000000000c1"
SEED = f"""
INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (900000101, 'Fixture Crag', 40.0, -105.3);
INSERT INTO mp_routes (mp_route_id, name, location_id) VALUES (900000001, 'Fixture Route', 900000101), (900000002, 'Other', 900000101);
INSERT INTO canonical_areas (area_id, name, path, lat, lon, coord_precision, source, redistributable) VALUES
  ('{AREA}', 'Fixture Crag', '0000000000000000000000000000000a', 40.0, -105.3, 'area_centroid', 'openbeta', true);
INSERT INTO canonical_routes (route_id, area_id, name, grade, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable) VALUES
  ('{ROUTE}', '{AREA}', 'Fixture Route', '5.9', '{{trad}}', 'trad', 'rt-v1', false, true, 'openbeta', true);
INSERT INTO internal.mp_route_links VALUES ('{ROUTE}', 900000001, 1.0, 'ob_mp_id');
INSERT INTO internal.mp_area_links VALUES ('{AREA}', 900000101, 1.0, 'auto');
INSERT INTO mountains (mountain_id, name) VALUES (7, 'Fixture Mountain');
INSERT INTO routes (route_id, name, latitude, longitude, grade) VALUES (55, 'Fixture Route', 40.0005, -105.3, '5.9');
INSERT INTO accidents (accident_id, mp_route_id, route_id, mountain_id) VALUES
  (1, 900000001, NULL, 7), (2, NULL, 55, NULL), (3, 900000002, NULL, NULL), (4, NULL, NULL, 7);
"""


@requires_pg
def test_links_every_legacy_accident_and_nulls_legacy_columns():
    async def scenario(url: str) -> tuple[dict[str, object], list[tuple[object, ...]], int]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                result = await run_r10(conn, argparse.Namespace(apply=True))
                links = [tuple(r) for r in (await conn.execute(text(
                    "SELECT accident_id, canonical_route_id::text, canonical_area_id::text, method FROM accident_route_links ORDER BY 1"))).all()]
                legacy = (await conn.execute(text(
                    "SELECT count(*) FROM accidents WHERE route_id IS NOT NULL OR mountain_id IS NOT NULL"))).scalar_one()
            return result, links, int(legacy)
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        result, links, legacy = asyncio.run(scenario(sa_url(name)))
    assert links == [
        (1, ROUTE, None, "mp_route_link"),
        (2, ROUTE, None, "legacy_match"),
        (3, None, AREA, "mp_area_link"),
    ]
    assert result["unlinked"] == [4]
    assert legacy == 0
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** — in `framework.py`, add `"route_id", "mountain_id"` to `REPAIRABLE_FIELDS` with the comment `# route_id/mountain_id: written only by R10, to null the legacy links before 0011 drops them.`

`backend/app/data/repair/legacy_links.py`:

```python
"""R10: move every accident's route link into accident_route_links (route, area, or later
objective), then null the legacy route_id/mountain_id through the audited writer so
migration 0011 can drop them."""

from __future__ import annotations

import argparse
import uuid
from collections import defaultdict
from dataclasses import dataclass

import h3
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair.framework import Change, run_step
from app.pipelines.match import NO_LINK_BELOW, area_score, climb_score, decide, h3_neighbourhood
from app.pipelines.validate import ValidationReport

R10_VERSION = "r10-v1"


@dataclass(frozen=True)
class Link:
    accident_id: int
    route_id: uuid.UUID | None
    area_id: uuid.UUID | None
    method: str
    score: float | None


async def plan_links(conn: AsyncConnection) -> tuple[list[Link], list[int]]:
    route_by_mp = {int(m): uuid.UUID(str(r)) for r, m in (await conn.execute(text(
        "SELECT route_id, mp_route_id FROM internal.mp_route_links"))).all()}
    area_by_loc = {int(m): uuid.UUID(str(a)) for a, m in (await conn.execute(text(
        "SELECT area_id, mp_location_id FROM internal.mp_area_links"))).all()}
    loc_by_route = {int(r): int(loc) for r, loc in (await conn.execute(text(
        "SELECT mp_route_id, location_id FROM mp_routes WHERE location_id IS NOT NULL"))).all()}
    legacy = {int(i): (str(n), m, la, lo, g) for i, n, m, la, lo, g in (await conn.execute(text(
        "SELECT route_id, name, mp_route_id, latitude, longitude, grade FROM routes"))).all()}
    areas = {uuid.UUID(str(a)): (str(n), float(la), float(lo)) for a, n, la, lo in (await conn.execute(text(
        "SELECT area_id, name, lat, lon FROM canonical_areas WHERE lat IS NOT NULL AND retired_at IS NULL"))).all()}
    by_cell: dict[str, list[uuid.UUID]] = defaultdict(list)
    for a, (_, la, lo) in areas.items():
        by_cell[h3.latlng_to_cell(la, lo, 7)].append(a)
    routes_in: dict[uuid.UUID, list[tuple[uuid.UUID, str, str | None, str | None]]] = defaultdict(list)
    for r, a, n, g, t in (await conn.execute(text(
            "SELECT route_id, area_id, name, grade, type_group FROM canonical_routes WHERE retired_at IS NULL"))).all():
        routes_in[uuid.UUID(str(a))].append((uuid.UUID(str(r)), str(n), g, t))

    links: list[Link] = []
    unlinked: list[int] = []
    for accident_id, mp_route_id, route_id in (await conn.execute(text(
            "SELECT accident_id, mp_route_id, route_id FROM accidents ORDER BY accident_id"))).all():
        a = int(accident_id)
        mp = int(mp_route_id) if mp_route_id is not None else None
        if mp is not None and mp in route_by_mp:
            links.append(Link(a, route_by_mp[mp], None, "mp_route_link", 1.0))
            continue
        if route_id is not None and int(route_id) in legacy:
            name, legacy_mp, la, lo, grade = legacy[int(route_id)]
            if legacy_mp and str(legacy_mp).isdigit() and int(legacy_mp) in route_by_mp:
                links.append(Link(a, route_by_mp[int(legacy_mp)], None, "legacy_mp_id", 1.0))
                continue
            if la is not None and lo is not None:
                cand_areas = [x for cell in h3_neighbourhood(float(la), float(lo)) for x in by_cell.get(cell, [])]
                scores = [(r, climb_score(name, grade, None, n, g, None)) for x in cand_areas for r, n, g, _ in routes_in[x]]
                verdict, best, score = decide(scores)
                if verdict == "link" and best is not None:
                    links.append(Link(a, best, None, "legacy_match", score))
                    continue
                area_scores = [(x, area_score(name, [], float(la), float(lo), areas[x][0], [], areas[x][1], areas[x][2]))
                               for x in cand_areas]
                if area_scores:
                    best_area, best_score = max(area_scores, key=lambda s: s[1])
                    if best_score >= NO_LINK_BELOW:
                        links.append(Link(a, None, best_area, "legacy_area", best_score))
                        continue
        if mp is not None and mp in loc_by_route and loc_by_route[mp] in area_by_loc:
            links.append(Link(a, None, area_by_loc[loc_by_route[mp]], "mp_area_link", 1.0))
            continue
        if route_id is not None or mp is not None:
            unlinked.append(a)
        elif (await conn.execute(text("SELECT mountain_id IS NOT NULL FROM accidents WHERE accident_id = :a"), {"a": a})).scalar():
            unlinked.append(a)
    return links, unlinked


async def run_r10(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    links, unlinked = await plan_links(conn)
    legacy_ids = [int(a) for (a,) in (await conn.execute(text(
        "SELECT accident_id FROM accidents WHERE route_id IS NOT NULL OR mountain_id IS NOT NULL"))).all()]
    report = ValidationReport("repair:r10")
    for link in links:
        report.accept()
    for a in unlinked:
        report.quarantine(str(a), "no_target")
    if args.apply and links:
        await conn.execute(text(
            "INSERT INTO accident_route_links (accident_id, canonical_route_id, canonical_area_id, method, score) "
            "VALUES (:a, :r, :ar, :m, :s) ON CONFLICT (accident_id) DO UPDATE SET canonical_route_id = EXCLUDED.canonical_route_id, "
            "canonical_area_id = EXCLUDED.canonical_area_id, method = EXCLUDED.method, score = EXCLUDED.score"),
            [{"a": x.accident_id, "r": x.route_id, "ar": x.area_id, "m": x.method, "s": x.score} for x in links])
    changes = [Change(a, f, None, "r10") for a in legacy_ids for f in ("route_id", "mountain_id")]
    summary = await run_step(conn, step="r10", rule_version=R10_VERSION, changes=changes, report=report, apply=args.apply)
    methods: dict[str, int] = defaultdict(int)
    for x in links:
        methods[x.method] += 1
    return summary | {"links": dict(methods), "unlinked": unlinked}
```

The no-link case for accident 4 (only a `mountain_id`) is reported, not linked: `mountains` rows carry no catalog identity; objective links for them come with plan 6's objectives (a rerun of `r10` after plan 6 is not needed because `accident_route_links.objective_id` is filled by plan 6's own job).

Register `"r10": legacy_links.run_r10` in `app/data/repair/__main__.py`. Grants ("Plan 5 (R10)"): `GRANT SELECT ON public.routes, public.mountains TO ingest; GRANT UPDATE (route_id, mountain_id) ON public.accidents TO ingest;`. Append `"app.data.repair.legacy_links"` to strict mypy.


- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/data/repair/ backend/tests/test_legacy_links.py backend/db/roles/grants_phase2.sql backend/pyproject.toml && git commit -m "feat(repair): R10 relinks legacy accident links into accident_route_links"`

---

### Task 10: Migration `0011` — guarded legacy drop, code and test cleanup

**Files:**
- Create: `backend/alembic/versions/0011_drop_legacy_routes.py`, `backend/tests/test_migration_0011.py`
- Modify: `backend/app/models/accident.py`, `backend/app/models/__init__.py`, `backend/alembic/env.py`, `backend/app/api/v1/accidents.py`, `backend/app/schemas/accident.py`, `backend/tests/test_migrations.py`, `backend/tests/test_ascent_analytics.py`, `backend/db/roles/grants_phase2.sql`, `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`
- Delete: `backend/app/models/legacy.py`

- [ ] **Step 1: Failing tests** — `backend/tests/test_migration_0011.py`:

```python
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg


def test_0011_refuses_while_legacy_links_remain():
    with migrated_db("0010_static_features") as name:
        run_sql(name, "INSERT INTO routes (route_id, name) VALUES (55, 'R'); "
                      "INSERT INTO accidents (accident_id, route_id) VALUES (1, 55);")
        with pytest.raises(RuntimeError, match="1 accidents still carry"):
            command.upgrade(_alembic_cfg(name), "head")


def test_0011_drops_legacy_objects_when_resolved():
    with migrated_db("0010_static_features") as name:
        cfg = _alembic_cfg(name)
        command.upgrade(cfg, "head")
        command.check(cfg)
```

In `test_migrations.py`, rename `test_head_drops_ascents_and_climbers_but_keeps_legacy_tables` to `test_0010_still_has_legacy_tables` and upgrade to `"0010_static_features"` instead of `"head"` (its assertions stay). In `test_ascent_analytics.py`: remove the `INSERT INTO routes …` line and every `route_id` column from the accident inserts in `SEED_SQL`; delete `test_accidents_count_by_mp_route_id_not_legacy_route_id` and `test_seed_really_has_the_colliding_legacy_link` (D16); `test_accident_list_filters_by_mp_route_id_not_legacy_route_id` keeps only its `mp_route_id` assertions. Add to `test_ascent_analytics.py`:

```python
def test_mountain_id_filter_is_rejected(seeded_db):
    response = asyncio.run(_raw_get(seeded_db, "/api/v1/accidents", mountain_id=1))
    assert response.status_code == 422
```

(using the file's existing HTTP helper; if it only returns JSON, add `_raw_get` beside `_get` returning the `httpx.Response`).

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/alembic/versions/0011_drop_legacy_routes.py`:

```python
"""R10 (spec 2a/2b-2): drop the legacy accident FKs and columns and the routes/mountains
tables. Refuses while any accident still carries a legacy link (the R10 job nulls them
through the audited writer after relinking); the owner dumps both tables first."""

from alembic import op

revision = "0011_drop_legacy_routes"
down_revision = "0010_static_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    remaining = bind.exec_driver_sql(
        "SELECT count(*) FROM accidents WHERE route_id IS NOT NULL OR mountain_id IS NOT NULL"
    ).scalar_one()
    if remaining:
        raise RuntimeError(f"refusing 0011: {remaining} accidents still carry route_id/mountain_id; run repair step r10")
    op.drop_constraint("accidents_route_id_fkey", "accidents", type_="foreignkey")
    op.drop_constraint("accidents_mountain_id_fkey", "accidents", type_="foreignkey")
    op.drop_index("idx_accidents_route", "accidents")
    op.drop_index("idx_accidents_mountain", "accidents")
    op.drop_column("accidents", "route_id")
    op.drop_column("accidents", "mountain_id")
    op.drop_table("routes")
    op.drop_table("mountains")


def downgrade() -> None:
    raise NotImplementedError("0011 is one-way: restore routes/mountains from the pre-drop dump if ever needed")
```

`accidents_raw` (plan 1) keeps the legacy columns, so the original values also survive in-database.

Code: delete `backend/app/models/legacy.py`; remove its import from `app/models/__init__.py`; in `alembic/env.py` remove the `UNMANAGED_LEGACY_TABLES` import and its check in `include_object`; in `app/models/accident.py` delete `mountain_id`, `route_id`, their `ForeignKey`s and the two indexes from `__table_args__`; in `app/schemas/accident.py` delete `mountain_id` and `route_id`; in `app/api/v1/accidents.py` turn `mountain_id` into the same reject-only parameter as `route_id` (`Query(None, include_in_schema=False)` and `raise HTTPException(status_code=422, detail="mountain_id is not supported; use mp_route_id")`), deleting the `Accident.mountain_id` filter. `grants_phase2.sql`: delete the Plan 5 (R10) grant lines on `routes`, `mountains`, and the `route_id, mountain_id` column UPDATE (the objects no longer exist; the file must stay runnable). Docs: CHANGELOG "R10 legacy cleanup (PR 2b-2c)"; CLAUDE.md drops the "legacy tables stay until Phase 2a" line; DEPLOYMENT.md revisions list; DATABASE_STRUCTURE.md removes the legacy FK note.

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd ../frontend && npm run test:run` → green (the frontend never read the removed fields; the run proves it).
- [ ] **Step 5: Commit** — `git add -A backend/alembic/versions/0011_drop_legacy_routes.py backend/app backend/tests backend/alembic/env.py backend/db/roles/grants_phase2.sql CHANGELOG.md CLAUDE.md DEPLOYMENT.md data/DATABASE_STRUCTURE.md && git commit -m "feat(db): 0011 drops legacy routes/mountains after R10 relink"`

---

### Task 11: OWNER/AGENT RUNBOOK — PR 2b-2a (MVP-1 features)

- [ ] **Step 1 (owner/agent): Branch `p2b-2a-rehearsal`**: migrate (`0010`), `alembic check`, grants, verify scripts.
- [ ] **Step 2 (owner/agent): Points and elevation** — as ingest: `static_features points` → count ≈ 25–30K; then `static_features elevation --limit 5000` repeated until it prints `"sampled": 0`. Then `static_features epqs-check` → `"passed": 1`. If not passed, stop: print the worst points' `point_key`s (not names) for the agent.
- [ ] **Step 3 (owner/agent): Normals inside the paid Open-Meteo month** (plan 3 Task 9) — `normals --max-units 0 --dry-run` → `units_total` (expected ~1.0–1.4M for ~5K buckets; spec budgets ~1.05M). If the Professional month's remaining units cannot cover it, stop and decide with the owner (split across months). Run in slices (`--max-units 200000`) until `stopped_for_budget: false` and every batch is `skipped`.
- [ ] **Step 4 (owner/agent): Route table and acceptance** — `static_features routes`; `VERIFY_DATABASE_URL=… uv run pytest -m db tests/verify/test_phase2b_features.py -q -s` → all pass. Then prod (Steps 1–4 without the branch substitution). Enable the workflow's weekly schedule by merging; add Actions secret `OPEN_METEO_API_KEY` only for the January normals dispatch (remove it after the Professional month if the Standard key differs).

### Task 12: OWNER/AGENT RUNBOOK — PR 2b-2b (terrain, lithology)

- [ ] **Step 1**: on prod (no schema change in this PR), `static_features terrain --limit 5000` repeatedly until `sampled: 0`; then `static_features lithology --limit 2000` repeatedly (≤5 req/s is enforced in the client) until done; then `static_features routes`.
- [ ] **Step 2**: report as analyst: share of points with `aspect_deg` NULL (expected high at crag-level coordinates), and `lithology_source = 'none'` share — that number decides D15 before v2.2 trains.

### Task 13: OWNER/AGENT RUNBOOK — PR 2b-2c (R10)

- [ ] **Step 1 (owner/agent): Dump the legacy tables** — like plan 1 Task 8 Step 3, for `routes` and `mountains` into `~/Developer/safeascent-private/backups/pre-0011/`.
- [ ] **Step 2 (owner/agent): Branch rehearsal** — `grants_phase2.sql` (Plan 5 R10 grants), `ING r10` (dry run: `links` by method, `unlinked` ids), `ING r10 --apply`, then migrate (`0011`), `alembic check`, grants (post-0011 file), verify scripts. Record the method counts and the unlinked count in the PR (spec: 421 direct, 762 via matcher).
- [ ] **Step 3 (owner): Prod** — Step 1 dump, deploy order: run `r10 --apply` on prod → deploy the PR's backend (it no longer reads the legacy columns) → `alembic upgrade head` (`0011`) → grants → verify. As analyst: `SELECT to_regclass('public.routes'), to_regclass('public.mountains'), (SELECT count(*) FROM pg_constraint WHERE confrelid IN (SELECT oid FROM pg_class WHERE relname IN ('routes','mountains')))` → `||0` (spec: "No FK points at a legacy table").

---

## Self-review

- Spec coverage: static features (terrain, DEM, Horn aspect, crag aspect/confidence, EPQS check, Alaska 2″, lithology with D15, spatial keys H3 r5/r7 + grid bucket) — Tasks 1–8; amendment §3.1 MVP-1 set (type group via catalog, elevation, monthly normals, latitude) — Tasks 1, 3, 5; 2b-2 acceptance "100% elevation coverage, EPQS passes, legacy tables dropped" — Tasks 6, 10–13; R10 — Tasks 9–10, 13. Objectives and hotspot curation (rest of 2b-2) are plan 6.
- Placeholders: none beyond Console hosts.
- Types: `Elevation`, `sample_points`, `read_window`, `monthly_normals`, `window`, `point_key` are used consistently; `route_static_features` column list matches between migration and `BUILD_SQL`.
