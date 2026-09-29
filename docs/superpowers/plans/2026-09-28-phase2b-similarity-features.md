# Phase 2b Similarity Features and R10 Relink (PRs 2b-2a, 2b-2b, 2b-2c) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce, for every route point, the features Phase 3 MVP-1's similarity pooling needs (amendment §3.1: type group, elevation, monthly climate normals, latitude) with 100% elevation coverage checked against USGS EPQS; expose them per scoring unit in one view; then the v2.2 terrain and lithology features (Horn slope/aspect, crag aspect with confidence, Macrostrat lithology); then R10, which moves legacy accident links into `accident_route_links` and sends every link it cannot resolve to owner review. The guarded drop of `routes`, `mountains`, `accidents.route_id` and `accidents.mountain_id` is **plan 6's** migration `0012_drop_legacy_routes`, after R10 re-runs with objectives loaded.

**Architecture:** Features are computed once per distinct **point** (`feature_points`, keyed by coordinates rounded to 5 decimals) `[assumes D7]`; `route_static_features` is a per-route table in the P3 shape filled from points through `canonical_areas.point_key` (stored, computed in Python only). Monthly normals and day-of-year climatology are **plan 3's** tables and job (`cell_climate_normals`, `cell_climatology`, `cell_normals_status`, filled in the yearly Pro window); this plan registers every new bucket as `pending` in `cell_normals_status` and every new `(grid_bucket, tz)` in `grid_bucket_series`, and reads normals only through the `scoring_unit_features` view. `dem.py` samples USGS 3DEP Cloud-Optimized GeoTIFFs remotely with rasterio (masked reads; only the blocks under our points) `[assumes D5]`. Everything is a resumable batch job; transient network failures leave a value unfilled for the next run, never recorded as missing data.

**Tech Stack:** Python 3.12, rasterio (bundled GDAL; `pipelines` dependency group only), numpy, h3, httpx, SQLAlchemy async, Alembic, PostGIS.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` §Static features, R10, milestone 2b-2; `docs/superpowers/specs/2026-09-28-phase3-amendment-similarity-confidence.md` §3.1 (MVP-1 feature set; this plan answers its open question 5 together with plan 3: normals are filled in plan 3's paid window, elevation here, PR 2b-2a); P3:150 (`route_static_features` columns). Decisions D5, D6, D7, D8, D15, D16 and the revision contract in `2026-09-28-phase2a-foundations.md`.

**Prerequisites:** Plans 1–4 merged and applied, in migration order. Real dependencies: plan 3 (`cell_climate_normals`, `cell_normals_status`, `grid_bucket_series`, `app.pipelines.localday` and the `pipelines` dependency group, all in `0007`/plan 3 code) and plan 4 (`canonical_routes`, which `0010` references by FK; `canonical_areas`, which the points job reads; `match`, `route_types`). PR 2b-2c also needs plan 2's `textsim`/`geo`. The earlier claim that points could be computed "before the catalog" is withdrawn: `0010` has an FK to `canonical_routes`.

**Three PRs:** 2b-2a = Tasks 1–5 + runbook Task 9 (`feat/p2b-mvp1-features`, the MVP-1 prerequisite); 2b-2b = Tasks 6–7 + runbook Task 10 (`feat/p2b-terrain-lithology`, v2.2); 2b-2c = Task 8 + runbook Task 11 (`feat/p2b-r10-relink`). Former Task 3 (normals) moved to plan 3; former Task 10 (legacy drop) moved to plan 6 as `0012_drop_legacy_routes`.

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- A feature that cannot be computed is NULL with a reason (`dem_source`, `lithology_source`, `cell_normals_status.status`, `terrain_version`), never 0 and never a default; downstream "missing counts against confidence" (amendment §3.1). Normals for a bucket whose status is not `complete`/`insufficient` read as NULL through `scoring_unit_features`.
- A transient failure (network error, timeout, 5xx, rate limit) is never recorded as missing data: the value stays unfilled (`dem_source IS NULL`, `terrain_version IS NULL`, `lithology_source IS NULL`) and the next run retries it. Only an authoritative "not there" (tile 404/403, masked DEM cell, a 200 with no geologic unit) is recorded as missing.
- DEM nodata values, masked cells and out-of-coverage points are NULL. The EPQS check compares against EPQS's best available DEM (often 1 m lidar), so it uses median and p90 |Δ| tolerances per slope class on ≥180 compared points, not a per-point 5 m bound (review claim 11).
- No bulk DEM tile downloads into the repo or the image; rasterio reads remote COGs at run time. The Railway image does not install the `pipelines` group.
- `point_key` is computed only in Python (`static_features.point_key`) and stored (`feature_points.point_key`, `canonical_areas.point_key`); SQL joins on stored columns, never on `to_char` formatting.
- R10 never nulls a legacy link. Unresolved links are rows in `internal.r10_unresolved`; plan 6's `0012` refuses to drop the legacy columns until every legacy-carrying accident has a link row or an owner decision.
- GitHub Actions inputs reach shell only through `env:`, never `${{ … }}` inside `run:`.

## Decisions this plan makes where the spec is silent (owner may overrule)

1. Point key precision is 5 decimals (~1 m); MP location coordinates carry 6 (`data/DATABASE_STRUCTURE.md`), so distinct crags never merge.
2. 3DEP coordinates are NAD83; they are treated as WGS84 (the datum shift is ≤ ~2 m horizontally, below the DEM's 10 m cell).
3. `route_static_features.lat` is added beside the P3 columns because latitude is an MVP-1 feature (amendment §3.1).
4. Points come only from MP locations and canonical areas **that have routes directly attached**; parent-area centroids never create points (they would add grid buckets, and therefore paid normals, that no route uses).
5. `slope_deg` keeps its P3 column name but holds the **maximum** Horn slope within 200 m of the point (crag steepness), not the slope at the point; route coordinates are crag-level, so a point slope would describe the approach. A column comment and `data/DATABASE_STRUCTURE.md` say so.
6. EPQS gate tolerances vs EPQS's best-available DEM, per slope class of the 1/3″ DEM around the point (flat < 10°, moderate 10–30°, steep ≥ 30°): median |Δ| ≤ 2 / 4 / 8 m and p90 |Δ| ≤ 5 / 10 / 20 m; ≥ 30 compared points per class and ≥ 180 overall; any |Δ| > 100 m fails the batch (a tile or datum error, not resolution). Reason: a 10 m DEM smooths cliffs that 1 m lidar resolves, so error grows with slope; a single 5 m bound fails on gentle ground already (claim 11).
7. R10's area fallback is never automatic: legacy routes carry no area tokens, so plan 4's `area_score` renormalises and caps at `REVIEW_CAP` 0.89 (review K1); the best area candidate goes to owner review instead. Likewise a legacy route without a grade comparable to the candidate's caps at 0.89 in `climb_score` and lands in review, never auto-link.
8. Legacy `mountains` resolve to plan 6 objectives within 3 km by plan 4's `name_similarity` on `place_key` names, decided by plan 4's `decide` (≥ 0.90, unique, and `AUTO_MARGIN` 0.05 over the runner-up → link; 0.80–0.90 or no margin → review); before plan 6 they are `awaiting_objectives`.

## Review Focus

1. **A point over a DEM nodata cell, or a tile that declares no nodata value** — expect `elevation_m` NULL with `dem_source = 'nodata'`, never 0 (Task 2 `test_nodata_is_null_not_zero`, `test_tile_without_nodata_never_returns_zero`).
2. **A tile request that times out or returns 503** — expect the point left unfilled (`dem_source IS NULL`) for the next run, not `no_tile` (Task 2 `test_transient_tile_error_leaves_points_unfilled`, Task 4 `test_transient_elevation_is_retried_not_recorded`).
3. **A point in the western Aleutians (lon +175)** — expect tile `n53e175`, not a `w` tile (Task 2 `test_tile_naming_east_of_180`).
4. **An EPQS run with only 100 comparable points, or a steep class with p90 |Δ| of 25 m** — expect `passed: 0` (Task 3 `test_gate_needs_180_points_and_every_class`).
5. **A legacy link R10 cannot resolve** — expect an `internal.r10_unresolved` row and the legacy columns untouched, never nulled (Task 8 `test_unresolved_links_are_recorded_never_nulled`).
6. **A route whose area has no coordinates** — expect a `route_static_features` row with NULL location features and `coord_precision = 'none'`, and 12 `scoring_unit_features` rows with `normals_status = 'no_location'` (Task 4 `test_routes_without_coordinates_get_explicit_nulls`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/alembic/versions/0010_static_features.py` | Create | `feature_points` (incl. `tz`, `terrain_version`), `route_static_features`, `canonical_areas.point_key`, `internal.r10_unresolved`, view `scoring_unit_features`. |
| `backend/app/models/features.py` | Create | `FeaturePoint`, `RouteStaticFeatures`, `R10Unresolved`. |
| `backend/app/models/catalog.py` | Modify | `CanonicalArea.point_key`. |
| `backend/app/pipelines/dem.py` | Create | 3DEP tile naming (both hemispheres of 180°), existence probe, masked remote COG sampling, window reads. |
| `backend/app/pipelines/epqs.py` | Create | EPQS client (slow, retried), local slope from the DEM, stratified comparison gate. |
| `backend/app/pipelines/static_features.py` | Create | Point upsert (+ tz, bucket registration), elevation fill, route table build, EPQS check, terrain and lithology steps, CLI. |
| `backend/app/pipelines/terrain.py` | Create | Horn slope/aspect, circular mean, crag aspect. |
| `backend/app/pipelines/lithology.py` | Create | Macrostrat client (retried) + ~10-class normalizer. |
| `backend/app/data/repair/legacy_links.py` | Create | R10 relink, unresolved → review table, owner round-trip CLI. |
| `backend/app/data/repair/__main__.py` | Modify | Register `r10`. |
| tests: `test_migration_0010.py`, `test_dem.py`, `test_epqs.py`, `test_static_features.py`, `test_terrain.py`, `test_lithology.py`, `test_legacy_links.py` | Create | Tests. |
| `backend/tests/verify/test_phase2b_features.py` | Create | `-m db` acceptance. |
| `.github/workflows/data-static-features.yml` | Create | Weekly point/elevation/terrain/lithology/route refresh (no Open-Meteo spend). |
| `backend/pyproject.toml`, `backend/uv.lock`, grants/verify SQL, docs | Modify | Deps, mypy, grants, docs. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 2–8 | `feature_points`, `route_static_features`, `internal.r10_unresolved`, `scoring_unit_features` (0010) — all v2.2 columns created now | Task 1 first; Tasks 6–7 only fill columns. |
| 1 | plan 3 | `cell_climate_normals`, `cell_normals_status`, `grid_bucket_series` (0007) and their `ingest` grants | Consumed unchanged; this plan inserts `pending` status rows and series rows only (`ON CONFLICT DO NOTHING`). |
| 1 | plan 6 | `scoring_unit_features` | Plan 6 replaces the view (`CREATE OR REPLACE VIEW`, same leading columns) to add objective rows. |
| 2 | 3, 4, 6 | `dem.tile_url(lat, lon, source)`, `dem.sample_points(points, opener) -> list[Elevation | None]`, `dem.read_window(...)`, `dem.TransientTileError` | Frozen in Task 2. |
| 4 | plans 6, 8, Phase 3 | `route_static_features` column names, `feature_points.point_key`, `static_features.point_key`, `scoring_unit_features` | Frozen here. Plan 6 stores objective `point_key` with the same function. |
| 4 | plan 3 | `localday.tz_for_point` | Consumed unchanged; injected in tests. |
| 8 | plan 4 | `match.climb_score`, `match.area_score`, `match.decide`, `match.name_similarity`, `REVIEW_CAP`, `AUTO_MARGIN`, `route_types.map_type_group`, `flags_from_mp_type` | Consumed by signature (plan 4 final). Task 8's fixtures are derived from plan 4 Task 5's formulas: weights 0.6/0.25/0.15, same-base grade 0.5, no comparable grade or no path tokens → renormalised and capped at 0.89 (review), auto-link needs unique ≥ 0.90 and a 0.05 margin. |
| 8 | plan 6 | `internal.r10_unresolved` decisions, `accident_route_links.objective_id`, R10 re-run, `0012_drop_legacy_routes` guard | Plan 6 re-runs `r10` after loading objectives and owns the drop migration and legacy code/test cleanup. R10 takes no UPDATE grant on `accidents`, so plan 6's grant cleanup removes only the SELECT on `routes`/`mountains`. |
| 1, 4, 6, 7, 8 | each other | `grants_phase2.sql`, `verify_roles_phase2.sql`, `pyproject.toml`, the workflow | Serial appends. |

---

# PR 2b-2a — `feat/p2b-mvp1-features`

### Task 1: Migration `0010`, models, `rasterio` in the `pipelines` group

**Files:**
- Create: `backend/alembic/versions/0010_static_features.py`, `backend/app/models/features.py`, `backend/tests/test_migration_0010.py`
- Modify: `backend/app/models/__init__.py`, `backend/app/models/catalog.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Produces (DB):
  - `feature_points(point_key text PK /* 'lat5:lon5' */, lat double precision NOT NULL, lon double precision NOT NULL, grid_bucket integer NOT NULL, h3_r5 text NOT NULL, h3_r7 text NOT NULL, tz text /* IANA; NULL only when unresolvable */, elevation_m real, dem_source text CHECK IN ('3dep_13','3dep_2','nodata','no_tile'), dem_res_m real, slope_deg real /* max within 200 m */, aspect_deg real, aspect_confidence real, terrain_version text, lithology text, lithology_source text CHECK IN ('macrostrat','none'), feature_version text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(), run_id uuid)`, index on `grid_bucket`.
  - `canonical_areas.point_key text NULL` + index.
  - `route_static_features(route_id uuid PK REFERENCES canonical_routes, source text NOT NULL, area_path ltree, type_group text, point_key text REFERENCES feature_points, lat double precision, aspect_deg real, slope_deg real, elevation_m real, lithology text, pitches smallint, length_m real, h3_r5 text, grid_bucket integer, dem_res_m real, aspect_confidence real, coord_precision text NOT NULL, lithology_source text, feature_version text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now())`.
  - `internal.r10_unresolved(accident_id integer PK REFERENCES accidents, legacy_route_id integer, legacy_mountain_id integer, candidate_kind text CHECK IN ('route','area','objective'), best_candidate_id uuid, best_score real, reason text NOT NULL CHECK IN ('review_band','area_candidate','objective_candidate','awaiting_objectives','no_candidate','no_coordinates'), owner_decision text CHECK IN ('link','no_link'), decided_route_id uuid, decided_area_id uuid, decided_objective_id uuid, decided_at timestamptz, run_id uuid, CHECK (owner_decision IS DISTINCT FROM 'link' OR num_nonnulls(decided_route_id, decided_area_id, decided_objective_id) = 1))`.
  - View `scoring_unit_features(unit_kind, unit_id, type_group, area_path, point_key, tz, lat, elevation_m, dem_res_m, grid_bucket, coord_precision, month, tmax_mean, tmin_mean, precip_mm, snowfall_cm, freeze_thaw_days, normals_status, feature_version)`: 12 rows per scoring unit (one per month), normals NULL unless the bucket's status is `complete` or `insufficient`; `normals_status` is `no_location` for a unit without a bucket and `pending` for a bucket without a status row. This is the Phase 3 MVP-1 read contract (amendment §3.1, P3:150); plan 6 adds `unit_kind = 'objective'` rows.
- Produces (Python): models `FeaturePoint`, `RouteStaticFeatures`, `R10Unresolved`; `CanonicalArea.point_key`; `pipelines` group gains `rasterio>=1.4,<2` (the group itself is created by plan 3).

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0010.py`:

```python
import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg

AREA = "00000000-0000-0000-0000-00000000000a"
ROUTE = "00000000-0000-0000-0000-0000000000c1"
SEED = f"""
INSERT INTO canonical_areas (area_id, name, path, lat, lon, coord_precision, source, redistributable, point_key) VALUES
  ('{AREA}', 'Fixture Crag', '0000000000000000000000000000000a', 40.12346, -105.65432, 'area_centroid', 'openbeta', true,
   '40.12346:-105.65432');
INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, is_boulder, scored,
  source, redistributable) VALUES ('{ROUTE}', '{AREA}', 'R1', '{{trad}}', 'trad', 'rt-v1', false, true, 'openbeta', true);
INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, elevation_m, dem_source, dem_res_m,
  feature_version) VALUES ('40.12346:-105.65432', 40.12346, -105.65432, 4012345, 'a', 'b', 'America/Denver', 2400,
  '3dep_13', 10, 'f-v1');
INSERT INTO route_static_features (route_id, source, point_key, lat, elevation_m, grid_bucket, coord_precision,
  feature_version) VALUES ('{ROUTE}', 'openbeta', '40.12346:-105.65432', 40.12346, 2400, 4012345, 'area_centroid', 'f-v1');
"""


def test_0010_checks_clean_and_rejects_bad_values():
    with migrated_db("0010_static_features") as name:
        command.check(_alembic_cfg(name))
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, dem_source, "
                          "feature_version) VALUES ('k', 40, -105, 1, 'a', 'b', 'guessed', 'f-v1')")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO accidents (accident_id) VALUES (1); "
                          "INSERT INTO internal.r10_unresolved (accident_id, reason, owner_decision) "
                          "VALUES (1, 'no_candidate', 'link')")


def test_scoring_unit_features_has_twelve_months_and_pending_normals_are_null():
    with migrated_db("0010_static_features", seed_sql=SEED) as name:
        import asyncio

        async def go() -> list[asyncpg.Record]:
            from tests.pgtest import pg_url

            conn = await asyncpg.connect(pg_url(name))
            try:
                return await conn.fetch(
                    "SELECT month, tz, elevation_m, tmax_mean, normals_status FROM scoring_unit_features ORDER BY month")
            finally:
                await conn.close()

        rows = asyncio.run(go())
    assert [r["month"] for r in rows] == list(range(1, 13))
    assert {(r["tz"], r["elevation_m"], r["tmax_mean"], r["normals_status"]) for r in rows} == {
        ("America/Denver", 2400.0, None, "pending")}


def test_0010_downgrade_refuses_after_elevation_was_sampled():
    with migrated_db("0010_static_features", seed_sql=SEED) as name:
        with pytest.raises(RuntimeError, match="refusing to downgrade 0010"):
            command.downgrade(_alembic_cfg(name), "0009_mp_ticks_internal")
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_migration_0010.py -q` → FAIL (revision missing).

- [ ] **Step 3: Implement** `backend/alembic/versions/0010_static_features.py`:

```python
"""Static features keyed by point (D7), the per-route table Phase 3 reads (P3:150 +
amendment §3.1), the scoring-unit features view (the MVP-1 read contract), and the R10
review table. Normals and day-of-year climatology are 0007's (plan 3): they must fill
inside the paid Open-Meteo window, which this plan does not wait for."""

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


SCORING_UNIT_FEATURES = """
CREATE VIEW scoring_unit_features AS
SELECT 'route'::text AS unit_kind, f.route_id AS unit_id, f.type_group, f.area_path, f.point_key, p.tz, f.lat,
       f.elevation_m, f.dem_res_m, f.grid_bucket, f.coord_precision, m.month::smallint AS month,
       n.tmax_mean, n.tmin_mean, n.precip_mm, n.snowfall_cm, n.freeze_thaw_days,
       CASE WHEN f.grid_bucket IS NULL THEN 'no_location' ELSE COALESCE(s.status, 'pending') END AS normals_status,
       f.feature_version
FROM route_static_features f
LEFT JOIN feature_points p ON p.point_key = f.point_key
CROSS JOIN generate_series(1, 12) AS m(month)
LEFT JOIN cell_normals_status s ON s.grid_bucket = f.grid_bucket
LEFT JOIN cell_climate_normals n
  ON n.grid_bucket = f.grid_bucket AND n.month = m.month AND s.status IN ('complete', 'insufficient')
"""


def upgrade() -> None:
    op.create_table(
        "feature_points",
        sa.Column("point_key", sa.Text(), primary_key=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("grid_bucket", sa.Integer(), nullable=False),
        sa.Column("h3_r5", sa.Text(), nullable=False),
        sa.Column("h3_r7", sa.Text(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=True),
        sa.Column("elevation_m", sa.REAL(), nullable=True),
        sa.Column("dem_source", sa.Text(), nullable=True),
        sa.Column("dem_res_m", sa.REAL(), nullable=True),
        sa.Column("slope_deg", sa.REAL(), nullable=True,
                  comment="maximum Horn slope (deg) within 200 m of the point (crag steepness), not the slope at the point"),
        sa.Column("aspect_deg", sa.REAL(), nullable=True),
        sa.Column("aspect_confidence", sa.REAL(), nullable=True),
        sa.Column("terrain_version", sa.Text(), nullable=True),
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
    op.add_column("canonical_areas", sa.Column("point_key", sa.Text(), nullable=True))
    op.create_index("ix_canonical_areas_point_key", "canonical_areas", ["point_key"])
    op.create_table(
        "route_static_features",
        sa.Column("route_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("canonical_routes.route_id"), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("area_path", Ltree(), nullable=True),
        sa.Column("type_group", sa.Text(), nullable=True),
        sa.Column("point_key", sa.Text(), sa.ForeignKey("feature_points.point_key"), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("aspect_deg", sa.REAL(), nullable=True),
        sa.Column("slope_deg", sa.REAL(), nullable=True,
                  comment="maximum Horn slope (deg) within 200 m of the point (crag steepness), not the slope at the point"),
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
    op.create_table(
        "r10_unresolved",
        sa.Column("accident_id", sa.Integer(), sa.ForeignKey("accidents.accident_id"), primary_key=True),
        sa.Column("legacy_route_id", sa.Integer(), nullable=True),
        sa.Column("legacy_mountain_id", sa.Integer(), nullable=True),
        sa.Column("candidate_kind", sa.Text(), nullable=True),
        sa.Column("best_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("best_score", sa.REAL(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("owner_decision", sa.Text(), nullable=True),
        sa.Column("decided_route_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_area_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_objective_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("candidate_kind IS NULL OR candidate_kind IN ('route', 'area', 'objective')",
                           name="r10_unresolved_candidate_kind_check"),
        sa.CheckConstraint("reason IN ('review_band', 'area_candidate', 'objective_candidate', 'awaiting_objectives', "
                           "'no_candidate', 'no_coordinates')", name="r10_unresolved_reason_check"),
        sa.CheckConstraint("owner_decision IS NULL OR owner_decision IN ('link', 'no_link')",
                           name="r10_unresolved_owner_decision_check"),
        sa.CheckConstraint("owner_decision IS DISTINCT FROM 'link' OR "
                           "num_nonnulls(decided_route_id, decided_area_id, decided_objective_id) = 1",
                           name="r10_unresolved_link_target_check"),
        schema="internal",
    )
    op.execute(SCORING_UNIT_FEATURES)


def downgrade() -> None:
    bind = op.get_bind()
    sampled = bind.exec_driver_sql("SELECT count(*) FROM feature_points WHERE dem_source IS NOT NULL").scalar_one()
    decided = bind.exec_driver_sql(
        "SELECT count(*) FROM internal.r10_unresolved WHERE owner_decision IS NOT NULL").scalar_one()
    if sampled or decided:
        raise RuntimeError(f"refusing to downgrade 0010: {sampled} sampled points, {decided} owner R10 decisions")
    op.execute("DROP VIEW scoring_unit_features")
    op.drop_table("r10_unresolved", schema="internal")
    op.drop_table("route_static_features")
    op.drop_index("ix_canonical_areas_point_key", "canonical_areas")
    op.drop_column("canonical_areas", "point_key")
    op.drop_index("ix_feature_points_grid_bucket", "feature_points")
    op.drop_table("feature_points")
```

`alembic check` does not compare views; the view is covered by the DB test instead. `backend/app/models/features.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import REAL, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.types import Ltree

_SLOPE_COMMENT = "maximum Horn slope (deg) within 200 m of the point (crag steepness), not the slope at the point"


class FeaturePoint(Base):
    __tablename__ = "feature_points"
    __table_args__ = (
        CheckConstraint("dem_source IS NULL OR dem_source IN ('3dep_13', '3dep_2', 'nodata', 'no_tile')",
                        name="feature_points_dem_source_check"),
        CheckConstraint("lithology_source IS NULL OR lithology_source IN ('macrostrat', 'none')",
                        name="feature_points_lithology_source_check"),
        Index("ix_feature_points_grid_bucket", "grid_bucket"),
    )

    point_key: Mapped[str] = mapped_column(Text, primary_key=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    grid_bucket: Mapped[int] = mapped_column(Integer)
    h3_r5: Mapped[str] = mapped_column(Text)
    h3_r7: Mapped[str] = mapped_column(Text)
    tz: Mapped[str | None] = mapped_column(Text)
    elevation_m: Mapped[float | None] = mapped_column(REAL)
    dem_source: Mapped[str | None] = mapped_column(Text)
    dem_res_m: Mapped[float | None] = mapped_column(REAL)
    slope_deg: Mapped[float | None] = mapped_column(REAL, comment=_SLOPE_COMMENT)
    aspect_deg: Mapped[float | None] = mapped_column(REAL)
    aspect_confidence: Mapped[float | None] = mapped_column(REAL)
    terrain_version: Mapped[str | None] = mapped_column(Text)
    lithology: Mapped[str | None] = mapped_column(Text)
    lithology_source: Mapped[str | None] = mapped_column(Text)
    feature_version: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class RouteStaticFeatures(Base):
    __tablename__ = "route_static_features"

    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_routes.route_id"), primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    area_path: Mapped[str | None] = mapped_column(Ltree)
    type_group: Mapped[str | None] = mapped_column(Text)
    point_key: Mapped[str | None] = mapped_column(Text, ForeignKey("feature_points.point_key"))
    lat: Mapped[float | None] = mapped_column(Float)
    aspect_deg: Mapped[float | None] = mapped_column(REAL)
    slope_deg: Mapped[float | None] = mapped_column(REAL, comment=_SLOPE_COMMENT)
    elevation_m: Mapped[float | None] = mapped_column(REAL)
    lithology: Mapped[str | None] = mapped_column(Text)
    pitches: Mapped[int | None] = mapped_column(SmallInteger)
    length_m: Mapped[float | None] = mapped_column(REAL)
    h3_r5: Mapped[str | None] = mapped_column(Text)
    grid_bucket: Mapped[int | None] = mapped_column(Integer)
    dem_res_m: Mapped[float | None] = mapped_column(REAL)
    aspect_confidence: Mapped[float | None] = mapped_column(REAL)
    coord_precision: Mapped[str] = mapped_column(Text)
    lithology_source: Mapped[str | None] = mapped_column(Text)
    feature_version: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class R10Unresolved(Base):
    __tablename__ = "r10_unresolved"
    __table_args__ = (
        CheckConstraint("candidate_kind IS NULL OR candidate_kind IN ('route', 'area', 'objective')",
                        name="r10_unresolved_candidate_kind_check"),
        CheckConstraint("reason IN ('review_band', 'area_candidate', 'objective_candidate', 'awaiting_objectives', "
                        "'no_candidate', 'no_coordinates')", name="r10_unresolved_reason_check"),
        CheckConstraint("owner_decision IS NULL OR owner_decision IN ('link', 'no_link')",
                        name="r10_unresolved_owner_decision_check"),
        CheckConstraint("owner_decision IS DISTINCT FROM 'link' OR "
                        "num_nonnulls(decided_route_id, decided_area_id, decided_objective_id) = 1",
                        name="r10_unresolved_link_target_check"),
        {"schema": "internal"},
    )

    accident_id: Mapped[int] = mapped_column(Integer, ForeignKey("accidents.accident_id"), primary_key=True)
    legacy_route_id: Mapped[int | None] = mapped_column(Integer)
    legacy_mountain_id: Mapped[int | None] = mapped_column(Integer)
    candidate_kind: Mapped[str | None] = mapped_column(Text)
    best_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    best_score: Mapped[float | None] = mapped_column(REAL)
    reason: Mapped[str] = mapped_column(Text)
    owner_decision: Mapped[str | None] = mapped_column(Text)
    decided_route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_area_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_objective_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
```

Match `app.db.base.Base` and `app.models.types.Ltree` to the import paths `app/models/catalog.py` (plan 4) actually uses. In `app/models/catalog.py` add to `CanonicalArea`: `point_key: Mapped[str | None] = mapped_column(Text, index=True)` with `Index("ix_canonical_areas_point_key", "point_key")` in its `__table_args__` (not `index=True`, so the index name matches the migration). Register `features` in `app/models/__init__.py` and append `"app.models.features"` to the models mypy block.

```bash
cd backend && uv add --group pipelines 'rasterio>=1.4,<2'
```

Grants ("Plan 5 (0010)"); `trainer` gets nothing now (D13):

```sql
GRANT SELECT, INSERT, UPDATE ON public.feature_points TO ingest;
GRANT UPDATE (point_key) ON public.canonical_areas TO ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.route_static_features TO ingest;
GRANT SELECT ON public.scoring_unit_features TO ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON internal.r10_unresolved TO ingest;
```

`verify_roles_phase2.sql` `ingest_writes`: INSERT/UPDATE for `public.feature_points`; INSERT/UPDATE/DELETE for `public.route_static_features` and `internal.r10_unresolved`; and a row asserting `has_table_privilege('app', 'internal.r10_unresolved', 'SELECT')` is false.

- [ ] **Step 4: Run** — `cd backend && uv sync --group pipelines && uv run pytest tests/test_migration_0010.py tests/test_migrations.py tests/test_roles_phase2.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0010_static_features.py backend/app/models/ backend/tests/test_migration_0010.py backend/pyproject.toml backend/uv.lock backend/db/roles/ && git commit -m "feat(db): 0010 feature points, route_static_features, scoring_unit_features, R10 review table"`

---

### Task 2: 3DEP DEM sampler (remote COG, masked reads, 2″ fallback, transient ≠ missing)

**Files:**
- Create: `backend/app/pipelines/dem.py`, `backend/tests/test_dem.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `TILE_BASE = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation"`, `RES = {"3dep_13": ("13", 10.0), "3dep_2": ("2", 60.0)}`, `LOW_SENTINEL = -1.0e4`, `class TransientTileError(Exception)`, `tile_name(lat: float, lon: float) -> str` (`n41w106`, `n53e175`), `tile_url(lat: float, lon: float, source: str) -> str`, `@dataclass(frozen=True) Elevation(elevation_m: float | None, dem_source: str, dem_res_m: float | None)`, `Opener = Callable[[str], AbstractContextManager[DatasetReader] | None]` (None = tile does not exist; raises `TransientTileError` when unknown), `sample_points(points: Sequence[tuple[float, float]], opener: Opener = open_remote) -> list[Elevation | None]` (None = transient, retry later), `open_remote(url: str)`.

- [ ] **Step 1: Verify the tile layout and the masked-sample API (agent, read-only)**

```bash
cd backend && uv run python - <<'EOF'
import httpx
import rasterio
base = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation"
for url in (f"{base}/13/TIFF/current/n41w106/USGS_13_n41w106.tif",
            f"{base}/2/TIFF/current/n53e175/USGS_2_n53e175.tif",
            f"{base}/13/TIFF/current/n01w001/USGS_13_n01w001.tif"):
    print(url.rsplit("/", 1)[1], httpx.head(url, timeout=20).status_code)
with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
    with rasterio.open(f"/vsicurl/{base}/13/TIFF/current/n41w106/USGS_13_n41w106.tif") as src:
        print(src.crs, src.res, src.nodata, src.profile.get("tiled"), src.overviews(1)[:2])
        print(list(src.sample([(-105.6, 40.25)], masked=True))[0])
EOF
```

Expected: `n41w106` 200; the `e175` tile 200 (if it is 404/403, find the western-Aleutian naming at https://apps.nationalmap.gov/downloader and change only `tile_name`'s east-hemisphere branch and its test); the ocean tile 404 or 403 (S3's "does not exist"); a geographic CRS (EPSG:4269), `res` ≈ (9.26e-05, 9.26e-05), `tiled=True`, and a masked-array sample in the thousands of meters. 1/3″ covers most of Alaska; the 2″ fallback only serves the remaining gaps (review claim 9).

- [ ] **Step 2: Failing tests** — `backend/tests/test_dem.py`:

```python
from contextlib import contextmanager

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.pipelines.dem import TransientTileError, sample_points, tile_name, tile_url


def test_tile_naming_uses_the_northwest_corner():
    assert tile_name(40.25, -105.6) == "n41w106"
    assert tile_name(63.07, -151.5) == "n64w152"
    assert tile_url(40.25, -105.6, "3dep_13").endswith("/13/TIFF/current/n41w106/USGS_13_n41w106.tif")
    assert tile_url(63.07, -151.5, "3dep_2").endswith("/2/TIFF/current/n64w152/USGS_2_n64w152.tif")


def test_tile_naming_east_of_180():
    assert tile_name(52.9, 175.3) == "n53e175"
    assert tile_name(51.9, -179.5) == "n52w180"


def _write(path, *, nodata):
    data = np.full((100, 100), 1650.0, dtype="float32")
    data[0:10, 0:10] = -999999.0 if nodata is not None else -3.4028235e38
    with rasterio.open(path, "w", driver="GTiff", height=100, width=100, count=1, dtype="float32",
                       crs="EPSG:4269", transform=from_origin(-106.0, 41.0, 0.01, 0.01), nodata=nodata) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture
def tile(tmp_path):
    return _write(tmp_path / "t.tif", nodata=-999999.0)


@pytest.fixture
def tile_without_nodata(tmp_path):
    return _write(tmp_path / "n.tif", nodata=None)


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
    assert e is not None and (e.elevation_m, e.dem_source, e.dem_res_m) == (1650.0, "3dep_13", 10.0)


def test_nodata_is_null_not_zero(tile):
    [e] = sample_points([(40.95, -105.95)], opener_for({"/13/": tile}))
    assert e is not None and (e.elevation_m, e.dem_source) == (None, "nodata")


def test_tile_without_nodata_never_returns_zero(tile_without_nodata):
    fill, outside, ok = sample_points([(40.95, -105.95), (39.5, -105.5), (40.5, -105.5)],
                                      opener_for({"/13/": tile_without_nodata}))
    assert fill is not None and (fill.elevation_m, fill.dem_source) == (None, "nodata")
    assert outside is not None and (outside.elevation_m, outside.dem_source) == (None, "nodata")
    assert ok is not None and ok.elevation_m == 1650.0


def test_falls_back_to_2_arcsecond(tile):
    [e] = sample_points([(40.5, -105.5)], opener_for({"/2/": tile}))
    assert e is not None and (e.dem_source, e.dem_res_m) == ("3dep_2", 60.0)


def test_no_tile_at_all():
    [e] = sample_points([(40.5, -105.5)], lambda url: None)
    assert e is not None and (e.elevation_m, e.dem_source, e.dem_res_m) == (None, "no_tile", None)


def test_transient_tile_error_leaves_points_unfilled(tile):
    def flaky(url):
        raise TransientTileError("HTTP 503")

    assert sample_points([(40.5, -105.5), (40.6, -105.4)], flaky) == [None, None]
```

`-151.5` replaces the old `-151.0` case: a whole-degree longitude sits on the tile edge (`floor(-151.0) = -151` → `w151`), which the old expectation `w152` contradicted.

- [ ] **Step 3: Run to verify failure** — FAIL (module missing).

- [ ] **Step 4: Implement** `backend/app/pipelines/dem.py`:

```python
"""USGS 3DEP elevation (public domain) sampled from remote Cloud-Optimized GeoTIFFs: only
the blocks under our points are fetched. 1/3″ first, 2″ where 1/3″ has no tile (the
remaining Alaska gaps). Masked cells, fill values and missing tiles are NULL, never 0.
A tile that cannot be read for any reason other than 'does not exist' raises
TransientTileError, and its points stay unfilled for the next run."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

import httpx
import numpy as np
import rasterio
import rasterio.windows
from numpy.typing import NDArray
from rasterio.errors import RasterioIOError
from rasterio.io import DatasetReader

TILE_BASE = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation"
RES: dict[str, tuple[str, float]] = {"3dep_13": ("13", 10.0), "3dep_2": ("2", 60.0)}
GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
            "GDAL_HTTP_MAX_RETRY": "3", "GDAL_HTTP_RETRY_DELAY": "2"}
# S3 answers 403 instead of 404 for a missing key when listing is not public.
MISSING_STATUS = frozenset({403, 404})
# Below any US elevation; catches float32 fill values in tiles that declare no nodata.
LOW_SENTINEL = -1.0e4

Opener = Callable[[str], AbstractContextManager[DatasetReader] | None]


class TransientTileError(Exception):
    """The tile may exist but could not be read now; retry on a later run."""


@dataclass(frozen=True)
class Elevation:
    elevation_m: float | None
    dem_source: str
    dem_res_m: float | None


def tile_name(lat: float, lon: float) -> str:
    west = math.floor(lon)
    hemi = "w" if west < 0 else "e"
    return f"n{math.floor(lat) + 1:02d}{hemi}{abs(west):03d}"


def tile_url(lat: float, lon: float, source: str) -> str:
    folder, _ = RES[source]
    name = tile_name(lat, lon)
    return f"{TILE_BASE}/{folder}/TIFF/current/{name}/USGS_{folder}_{name}.tif"


def _exists(url: str) -> bool:
    try:
        status = httpx.head(url, timeout=20.0, follow_redirects=True).status_code
    except httpx.HTTPError as exc:
        raise TransientTileError(type(exc).__name__) from None
    if status == 200:
        return True
    if status in MISSING_STATUS:
        return False
    raise TransientTileError(f"HTTP {status}")


@contextmanager
def _opened(url: str) -> Iterator[DatasetReader]:
    with rasterio.Env(**GDAL_ENV):
        try:
            src = rasterio.open(f"/vsicurl/{url}")
        except RasterioIOError as exc:
            raise TransientTileError(str(exc)[:200]) from None
        with src:
            yield src


def open_remote(url: str) -> AbstractContextManager[DatasetReader] | None:
    return _opened(url) if _exists(url) else None


def _sample(src: DatasetReader, points: Sequence[tuple[float, float]]) -> list[float | None]:
    left, bottom, right, top = src.bounds
    coords = [(lon, lat) for lat, lon in points]
    out: list[float | None] = []
    for (lon, lat), value in zip(coords, src.sample(coords, masked=True)):
        if not (left <= lon <= right and bottom <= lat <= top) or np.ma.is_masked(value[0]):
            out.append(None)
            continue
        v = float(value[0])
        out.append(v if math.isfinite(v) and v > LOW_SENTINEL else None)
    return out


def sample_points(points: Sequence[tuple[float, float]], opener: Opener = open_remote) -> list[Elevation | None]:
    result: list[Elevation | None] = [None] * len(points)
    by_tile: dict[str, list[int]] = defaultdict(list)
    for i, (lat, lon) in enumerate(points):
        by_tile[tile_name(lat, lon)].append(i)
    for indices in by_tile.values():
        lat0, lon0 = points[indices[0]]
        try:
            for source in ("3dep_13", "3dep_2"):
                handle = opener(tile_url(lat0, lon0, source))
                if handle is None:
                    continue
                with handle as src:
                    values = _sample(src, [points[i] for i in indices])
                _, res_m = RES[source]
                for i, v in zip(indices, values):
                    result[i] = Elevation(v, source, res_m) if v is not None else Elevation(None, "nodata", None)
                break
            else:
                for i in indices:
                    result[i] = Elevation(None, "no_tile", None)
        except (TransientTileError, RasterioIOError):
            for i in indices:
                result[i] = None
    return result


def read_window(
    lat: float, lon: float, radius_m: float, opener: Opener = open_remote
) -> tuple[NDArray[np.float64], float, float, tuple[int, int]] | None:
    """A DEM window around a point (masked and fill cells as NaN), its cell size in meters,
    and the point's (row, col). None when no tile exists; TransientTileError otherwise."""
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
            raw = src.read(1, window=win, boundless=True, masked=True)
            z = np.ma.filled(raw.astype(np.float64), np.nan)
            z[~np.isfinite(z) | (z <= LOW_SENTINEL)] = np.nan
            return z, dx, dy, (half_r, half_c)
    return None
```

A point on a masked cell in an existing 1/3″ tile is recorded `nodata` rather than retried at 2″ (the tile exists; the hole is real, e.g. water). `read_window` lives here now (it was added by the terrain task before) so every DEM read goes through the same masked, transient-aware path. Append `"app.pipelines.dem"` to strict mypy.

- [ ] **Step 5: Run** — `cd backend && uv run pytest tests/test_dem.py -q && uv run mypy` → PASS.
- [ ] **Step 6: Commit** — `git add backend/app/pipelines/dem.py backend/tests/test_dem.py backend/pyproject.toml && git commit -m "feat(pipelines): 3DEP remote-COG elevation sampler, masked reads, transient errors retried"`

---

### Monthly climate normals — moved to plan 3

Former Task 3 (monthly normals and day-of-year climatology) is now plan 3 (`app/pipelines/normals.py`, tables in `0007`), because the normals must be fetched inside the Professional Open-Meteo window (review C2; owner decision 1). This plan only:
- registers every new point's bucket as `cell_normals_status(status='pending')` and every new `(grid_bucket, tz)` in `grid_bucket_series` (Task 4 `upsert_points`), so plan 3's next January window (or the current one, if still open) fills them;
- reads normals only through `scoring_unit_features` (Task 1), which returns NULL for any bucket that is not `complete`/`insufficient`, so a pending bucket is missing, never zero.

---

### Task 3: EPQS cross-check (stratified by slope, median/p90 gate)

**Files:**
- Create: `backend/app/pipelines/epqs.py`, `backend/tests/test_epqs.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: `dem.sample_points`, `dem.Elevation`.
- Produces: `EPQS_URL = "https://epqs.nationalmap.gov/v1/json"`, `MIN_COMPARED = 180`, `MIN_PER_CLASS = 30`, `PER_CLASS_TARGET = 80`, `SLOPE_CLASSES`, `TOLERANCE_M`, `GROSS_M = 100.0`, `class EpqsClient(*, transport=None, pause_s=1.0, timeout_s=20.0, retries=3, sleep=time.sleep)` with `elevation(lat: float, lon: float) -> float | None` and `failed_calls: int`, `slope_class(deg: float) -> str`, `local_slopes(points: Sequence[tuple[float, float]], sampler) -> list[float | None]`, `compare(rows: Sequence[tuple[str, float | None, float | None]]) -> dict[str, object]` (`compared`, `gross`, `classes`, `passed` as 0/1).

- [ ] **Step 1: Failing tests** — `backend/tests/test_epqs.py`:

```python
import math

import httpx
import pytest

from app.pipelines.dem import Elevation
from app.pipelines.epqs import EpqsClient, compare, local_slopes, slope_class


def _client(handler):
    return EpqsClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_parses_string_value_and_treats_sentinel_as_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        x = float(request.url.params["x"])
        return httpx.Response(200, json={"value": "-1000000" if x > 0 else "1650.25", "location": {"x": x}})

    client = _client(handler)
    assert client.elevation(40.0, -105.3) == 1650.25
    assert client.elevation(40.0, 5.0) is None


def test_retries_transient_failures_then_gives_up_without_a_value():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"value": "1650.0"})

    client = _client(handler)
    assert client.elevation(40.0, -105.3) == 1650.0
    dead = _client(lambda r: httpx.Response(503))
    assert dead.elevation(40.0, -105.3) is None and dead.failed_calls == 1


def test_slope_classes():
    assert [slope_class(d) for d in (0.0, 9.9, 10.0, 29.9, 30.0, 80.0)] == [
        "flat", "flat", "moderate", "moderate", "steep", "steep"]


def test_local_slope_of_a_30_degree_plane():
    def plane(points):
        return [Elevation(1000.0 + (lat - 40.0) * 110_574.0 * math.tan(math.radians(30.0)), "3dep_13", 10.0)
                for lat, _ in points]

    [s] = local_slopes([(40.0, -105.3)], plane)
    assert s == pytest.approx(30.0, abs=0.5)
    assert local_slopes([(40.0, -105.3)], lambda pts: [None] * len(pts)) == [None]


def _rows(cls, n, diff):
    return [(cls, 1000.0, 1000.0 + diff) for _ in range(n)]


def test_gate_passes_within_class_tolerances():
    result = compare(_rows("flat", 60, 1.0) + _rows("moderate", 60, 3.0) + _rows("steep", 60, 7.0))
    assert (result["compared"], result["gross"], result["passed"]) == (180, 0, 1)


def test_gate_needs_180_points_and_every_class():
    assert compare(_rows("flat", 40, 1.0) + _rows("moderate", 30, 1.0) + _rows("steep", 30, 1.0))["passed"] == 0
    assert compare(_rows("flat", 60, 1.0) + _rows("moderate", 120, 1.0))["passed"] == 0
    steep = _rows("steep", 54, 5.0) + _rows("steep", 6, 25.0)
    assert compare(_rows("flat", 60, 1.0) + _rows("moderate", 60, 1.0) + steep)["passed"] == 0


def test_one_gross_error_fails_and_missing_pairs_are_ignored():
    rows = _rows("flat", 60, 1.0) + _rows("moderate", 60, 1.0) + _rows("steep", 59, 1.0) + [("steep", 1000.0, 1200.0)]
    assert compare(rows)["passed"] == 0
    assert compare(rows[:-1] + [("steep", None, 5.0), ("steep", 1000.0, 1001.0)])["compared"] == 180
```

In the third `test_gate_needs_180_points_and_every_class` case the steep p90 is 25 m (6 of 60 points), above the 20 m bound.

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/epqs.py`:

```python
"""USGS Elevation Point Query Service cross-check of the 3DEP sampler. EPQS answers from
the best available DEM (often 1 m lidar), not the 1/3″ product we sample (review claim 11),
so the gate compares median and p90 |Δ| per slope class instead of a per-point bound.
EPQS stalls under load: calls are slow, time out, and retry with backoff."""

from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Sequence

import httpx
import numpy as np

from app.pipelines.dem import Elevation

EPQS_URL = "https://epqs.nationalmap.gov/v1/json"
NODATA = -1_000_000.0
MIN_COMPARED = 180
MIN_PER_CLASS = 30
PER_CLASS_TARGET = 80
GROSS_M = 100.0
SLOPE_CLASSES: tuple[tuple[str, float, float], ...] = (("flat", 0.0, 10.0), ("moderate", 10.0, 30.0), ("steep", 30.0, 91.0))
TOLERANCE_M: dict[str, tuple[float, float]] = {"flat": (2.0, 5.0), "moderate": (4.0, 10.0), "steep": (8.0, 20.0)}
NEIGHBOUR_M = 15.0

Sampler = Callable[[Sequence[tuple[float, float]]], list[Elevation | None]]


class EpqsClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        pause_s: float = 1.0,
        timeout_s: float = 20.0,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(transport=transport, timeout=timeout_s)
        self._pause = pause_s
        self._retries = retries
        self._sleep = sleep
        self.failed_calls = 0

    def elevation(self, lat: float, lon: float) -> float | None:
        params = {"x": lon, "y": lat, "units": "Meters", "wkid": 4326, "includeDate": "false"}
        for attempt in range(self._retries + 1):
            try:
                response: httpx.Response | None = self._client.get(EPQS_URL, params=params)
            except httpx.HTTPError:
                response = None
            self._sleep(self._pause)
            if response is not None and response.status_code == 200:
                try:
                    v = float(response.json().get("value"))
                except (TypeError, ValueError):
                    return None
                return None if v <= NODATA else v
            if response is not None and response.status_code < 500 and response.status_code != 429:
                return None
            self._sleep(2.0 ** (attempt + 1))
        self.failed_calls += 1
        return None


def slope_class(deg: float) -> str:
    for name, lo, hi in SLOPE_CLASSES:
        if lo <= deg < hi:
            return name
    return "steep"


def local_slopes(points: Sequence[tuple[float, float]], sampler: Sampler) -> list[float | None]:
    """Slope (deg) of the sampled DEM at each point from four neighbours 15 m away."""
    probes: list[tuple[float, float]] = []
    for lat, lon in points:
        dlat = NEIGHBOUR_M / 110_574.0
        dlon = NEIGHBOUR_M / (111_320.0 * math.cos(math.radians(lat)))
        probes += [(lat + dlat, lon), (lat - dlat, lon), (lat, lon + dlon), (lat, lon - dlon)]
    values = sampler(probes)
    out: list[float | None] = []
    for i in range(len(points)):
        quad = values[4 * i : 4 * i + 4]
        z = [e.elevation_m if e is not None else None for e in quad]
        if any(v is None for v in z):
            out.append(None)
            continue
        n, s, e, w = (float(v) for v in z if v is not None)
        out.append(math.degrees(math.atan(math.hypot((n - s) / (2 * NEIGHBOUR_M), (e - w) / (2 * NEIGHBOUR_M)))))
    return out


def compare(rows: Sequence[tuple[str, float | None, float | None]]) -> dict[str, object]:
    by: dict[str, list[float]] = defaultdict(list)
    gross = 0
    for cls, dem, ref in rows:
        if dem is None or ref is None:
            continue
        d = abs(dem - ref)
        by[cls].append(d)
        gross += int(d > GROSS_M)
    compared = sum(len(v) for v in by.values())
    ok = compared >= MIN_COMPARED and gross == 0
    classes: dict[str, dict[str, float | int | None]] = {}
    for cls, _, _ in SLOPE_CLASSES:
        diffs = by.get(cls, [])
        if len(diffs) < MIN_PER_CLASS:
            classes[cls] = {"n": len(diffs), "median": None, "p90": None, "passed": 0}
            ok = False
            continue
        median = statistics.median(diffs)
        p90 = float(np.percentile(diffs, 90))
        max_median, max_p90 = TOLERANCE_M[cls]
        passed = median <= max_median and p90 <= max_p90
        classes[cls] = {"n": len(diffs), "median": round(median, 2), "p90": round(p90, 2), "passed": int(passed)}
        ok = ok and passed
    return {"compared": compared, "gross": gross, "classes": classes, "passed": int(ok)}
```

Append `"app.pipelines.epqs"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_epqs.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/epqs.py backend/tests/test_epqs.py backend/pyproject.toml && git commit -m "feat(pipelines): EPQS cross-check stratified by slope with median/p90 gate"`

---

### Task 4: Static-features job (points + tz + bucket registration, elevation, route table, EPQS check)

**Files:**
- Create: `backend/app/pipelines/static_features.py`, `backend/tests/test_static_features.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: Tasks 1–3, `grid.grid_bucket`, `validate.coord_problem`, `validate.ValidationReport`, `localday.tz_for_point` (plan 3), `ingest_log.start_run`/`finish_run`/`write_quarantine`, `h3.latlng_to_cell`.
- Produces: `FEATURE_VERSION = "f-v1"`, `point_key(lat: float, lon: float) -> str`, `async upsert_points(conn, *, run_id, report: ValidationReport, tz_lookup: Callable[[float, float], str | None] = tz_for_point) -> int`, `async fill_elevation(conn, *, run_id, sampler=sample_points, limit: int) -> dict[str, int]` (`sampled`, `null`, `retry`), `async build_route_features(conn) -> int`, `async epqs_check(conn, client: EpqsClient, *, sampler=sample_points, seed: int = 42, per_class: int = PER_CLASS_TARGET, probe_batch: int = 500, max_probe: int = 6000) -> dict[str, object]`, CLI `python -m app.pipelines.static_features points|elevation [--limit N]|routes|epqs-check` (Tasks 6–7 add `terrain` and `lithology`). `epqs-check` exits 1 when the gate fails.

- [ ] **Step 1: Failing tests** — `backend/tests/test_static_features.py`:

```python
import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.dem import Elevation
from app.pipelines.static_features import build_route_features, fill_elevation, point_key, upsert_points
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url

A_OK = "00000000-0000-0000-0000-00000000000a"
A_NONE = "00000000-0000-0000-0000-00000000000b"
A_PARENT = "00000000-0000-0000-0000-00000000000d"
SEED = f"""
INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES
  (900000101, 'Fixture Crag', 40.123456, -105.654321), (900000102, 'Fixture Parent', 40.5, -105.9);
INSERT INTO mp_routes (mp_route_id, name, location_id) VALUES (900000001, 'Fixture Route', 900000101);
INSERT INTO canonical_areas (area_id, name, path, lat, lon, coord_precision, source, redistributable) VALUES
  ('{A_OK}', 'Fixture Crag', '0000000000000000000000000000000a', 40.123456, -105.654321, 'area_centroid', 'openbeta', true),
  ('{A_NONE}', 'Fixture No Coords', '0000000000000000000000000000000b', NULL, NULL, 'none', 'openbeta', true),
  ('{A_PARENT}', 'Fixture Region', '0000000000000000000000000000000d', 40.6, -105.8, 'area_centroid', 'openbeta', true);
INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable, pitches) VALUES
  ('00000000-0000-0000-0000-0000000000c1', '{A_OK}', 'R1', '{{trad}}', 'trad', 'rt-v1', false, true, 'openbeta', true, 2),
  ('00000000-0000-0000-0000-0000000000c2', '{A_NONE}', 'R2', '{{sport}}', 'sport', 'rt-v1', false, false, 'openbeta', true, NULL);
"""
DENVER = "America/Denver"


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


def _points(conn, run_id, tz=DENVER, report=None):
    return upsert_points(conn, run_id=run_id, report=report or ValidationReport("t"), tz_lookup=lambda la, lo: tz)


@requires_pg
def test_points_come_only_from_areas_with_routes_and_register_buckets():
    with migrated_db("0010_static_features", seed_sql=SEED) as name:
        assert _run(name, lambda c: _points(c, uuid.uuid4())) == 1
        keys = _run(name, lambda c: c.execute(text(
            "SELECT area_id::text, point_key FROM canonical_areas ORDER BY area_id")))
        assert [tuple(r) for r in keys] == [(A_OK, "40.12346:-105.65432"), (A_NONE, None), (A_PARENT, None)]
        status = _run(name, lambda c: c.execute(text("SELECT status FROM cell_normals_status")))
        series = _run(name, lambda c: c.execute(text("SELECT tz FROM grid_bucket_series")))
        assert [tuple(r) for r in status] == [("pending",)] and [tuple(r) for r in series] == [(DENVER,)]


@requires_pg
def test_unresolvable_timezone_is_quarantined_and_not_registered():
    with migrated_db("0010_static_features", seed_sql=SEED) as name:
        report = ValidationReport("t")
        _run(name, lambda c: _points(c, uuid.uuid4(), tz=None, report=report))
        assert report.quarantined["no_timezone"] == 1
        rows = _run(name, lambda c: c.execute(text(
            "SELECT (SELECT count(*) FROM feature_points WHERE tz IS NULL), (SELECT count(*) FROM grid_bucket_series)")))
        assert [tuple(r) for r in rows] == [(1, 0)]


@requires_pg
def test_points_are_shared_elevation_fills_and_routes_get_rows():
    with migrated_db("0010_static_features", seed_sql=SEED) as name:
        run_id = uuid.uuid4()
        _run(name, lambda c: _points(c, run_id))
        stats = _run(name, lambda c: fill_elevation(
            c, run_id=run_id, sampler=lambda pts: [Elevation(2400.0, "3dep_13", 10.0)] * len(pts), limit=100))
        assert stats == {"sampled": 1, "null": 0, "retry": 0}
        assert _run(name, build_route_features) == 2
        rows = _run(name, lambda c: c.execute(text(
            "SELECT pitches, elevation_m, lat, grid_bucket IS NOT NULL, coord_precision FROM route_static_features ORDER BY route_id")))
        assert [tuple(r) for r in rows] == [(2, 2400.0, 40.12346, True, "area_centroid"), (None, None, None, False, "none")]


@requires_pg
def test_transient_elevation_is_retried_not_recorded():
    with migrated_db("0010_static_features", seed_sql=SEED) as name:
        run_id = uuid.uuid4()
        _run(name, lambda c: _points(c, run_id))
        stats = _run(name, lambda c: fill_elevation(c, run_id=run_id, sampler=lambda pts: [None] * len(pts), limit=100))
        assert stats == {"sampled": 0, "null": 0, "retry": 1}
        pending = _run(name, lambda c: c.execute(text("SELECT count(*) FROM feature_points WHERE dem_source IS NULL")))
        assert [tuple(r) for r in pending] == [(1,)]


@requires_pg
def test_routes_without_coordinates_get_explicit_nulls():
    with migrated_db("0010_static_features", seed_sql=SEED) as name:
        _run(name, lambda c: _points(c, uuid.uuid4()))
        _run(name, build_route_features)
        rows = _run(name, lambda c: c.execute(text(
            "SELECT point_key, elevation_m, coord_precision FROM route_static_features "
            "WHERE route_id = '00000000-0000-0000-0000-0000000000c2'")))
        assert [tuple(r) for r in rows] == [(None, None, "none")]
        units = _run(name, lambda c: c.execute(text(
            "SELECT count(*), min(normals_status), max(normals_status) FROM scoring_unit_features "
            "WHERE unit_id = '00000000-0000-0000-0000-0000000000c2'")))
        assert [tuple(r) for r in units] == [(12, "no_location", "no_location")]
```

The route's `lat` is the point's (5-decimal) latitude, which is why it reads `40.12346`. The parent mp_location (no routes) and the parent canonical area (no routes attached) create no point.

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/static_features.py`:

```python
"""Static features per distinct point (D7) and the per-route table Phase 3 reads.
Catalog-source agnostic: reads canonical_areas/canonical_routes and mp_locations only."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import uuid
from collections.abc import Callable, Sequence

import h3
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.dem import Elevation, sample_points
from app.pipelines.epqs import PER_CLASS_TARGET, SLOPE_CLASSES, EpqsClient, compare, local_slopes, slope_class
from app.pipelines.grid import grid_bucket
from app.pipelines.localday import tz_for_point
from app.pipelines.validate import ValidationReport, coord_problem

FEATURE_VERSION = "f-v1"

Sampler = Callable[[Sequence[tuple[float, float]]], list[Elevation | None]]

LOCATION_SQL = (
    "SELECT l.latitude, l.longitude FROM mp_locations l WHERE l.latitude IS NOT NULL AND l.longitude IS NOT NULL "
    "AND EXISTS (SELECT 1 FROM mp_routes r WHERE r.location_id = l.mp_id)"
)
AREA_SQL = (
    "SELECT a.area_id, a.lat, a.lon FROM canonical_areas a WHERE a.lat IS NOT NULL AND a.lon IS NOT NULL "
    "AND a.retired_at IS NULL "
    "AND EXISTS (SELECT 1 FROM canonical_routes r WHERE r.area_id = a.area_id AND r.retired_at IS NULL)"
)


def point_key(lat: float, lon: float) -> str:
    return f"{lat:.5f}:{lon:.5f}"


async def upsert_points(
    conn: AsyncConnection,
    *,
    run_id: uuid.UUID,
    report: ValidationReport,
    tz_lookup: Callable[[float, float], str | None] = tz_for_point,
) -> int:
    coords = {(round(float(la), 5), round(float(lo), 5)) for la, lo in (await conn.execute(text(LOCATION_SQL))).all()}
    area_points: list[tuple[str, float, float]] = []
    for area_id, la, lo in (await conn.execute(text(AREA_SQL))).all():
        lat, lon = round(float(la), 5), round(float(lo), 5)
        coords.add((lat, lon))
        area_points.append((str(area_id), lat, lon))
    rows: list[dict[str, object]] = []
    for lat, lon in sorted(coords):
        key = point_key(lat, lon)
        problem = coord_problem(lat, lon)
        if problem is not None:
            report.quarantine(key, problem)
            continue
        tz = tz_lookup(lat, lon)
        if tz is None:
            report.quarantine(key, "no_timezone")
        else:
            report.accept()
        rows.append({"k": key, "lat": lat, "lon": lon, "b": grid_bucket(lat, lon), "r5": h3.latlng_to_cell(lat, lon, 5),
                     "r7": h3.latlng_to_cell(lat, lon, 7), "tz": tz, "v": FEATURE_VERSION, "run": run_id})
    if rows:
        await conn.execute(text(
            "INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, feature_version, run_id) "
            "VALUES (:k, :lat, :lon, :b, :r5, :r7, :tz, :v, :run) "
            "ON CONFLICT (point_key) DO UPDATE SET tz = COALESCE(feature_points.tz, EXCLUDED.tz)"), rows)
    kept = {str(r["k"]) for r in rows}
    area_keys = [{"a": a, "k": point_key(la, lo)} for a, la, lo in area_points if point_key(la, lo) in kept]
    if area_keys:
        await conn.execute(text(
            "UPDATE canonical_areas SET point_key = :k WHERE area_id = CAST(:a AS uuid) AND point_key IS DISTINCT FROM :k"),
            area_keys)
    await conn.execute(text(
        "UPDATE canonical_areas SET point_key = NULL WHERE point_key IS NOT NULL "
        "AND NOT (point_key = ANY(CAST(:keys AS text[])))"), {"keys": sorted(k["k"] for k in area_keys)})
    await conn.execute(text(
        "INSERT INTO cell_normals_status (grid_bucket, status) SELECT DISTINCT grid_bucket, 'pending' FROM feature_points "
        "ON CONFLICT (grid_bucket) DO NOTHING"))
    await conn.execute(text(
        "INSERT INTO grid_bucket_series (grid_bucket, tz) SELECT DISTINCT grid_bucket, tz FROM feature_points "
        "WHERE tz IS NOT NULL ON CONFLICT (grid_bucket, tz) DO NOTHING"))
    return len(rows)


async def fill_elevation(
    conn: AsyncConnection,
    *,
    run_id: uuid.UUID,
    sampler: Sampler = sample_points,
    limit: int,
) -> dict[str, int]:
    todo = [(str(k), float(la), float(lo)) for k, la, lo in (await conn.execute(text(
        "SELECT point_key, lat, lon FROM feature_points WHERE dem_source IS NULL ORDER BY point_key LIMIT :n"),
        {"n": limit})).all()]
    if not todo:
        return {"sampled": 0, "null": 0, "retry": 0}
    values = sampler([(la, lo) for _, la, lo in todo])
    done = [(k, v) for (k, _, _), v in zip(todo, values) if v is not None]
    if done:
        await conn.execute(text(
            "UPDATE feature_points SET elevation_m = :e, dem_source = :s, dem_res_m = :r, updated_at = now(), run_id = :run "
            "WHERE point_key = :k"),
            [{"e": v.elevation_m, "s": v.dem_source, "r": v.dem_res_m, "run": run_id, "k": k} for k, v in done])
    return {"sampled": len(done), "null": sum(1 for _, v in done if v.elevation_m is None), "retry": len(todo) - len(done)}


BUILD_SQL = f"""
INSERT INTO route_static_features (route_id, source, area_path, type_group, point_key, lat, aspect_deg, slope_deg,
  elevation_m, lithology, pitches, length_m, h3_r5, grid_bucket, dem_res_m, aspect_confidence, coord_precision,
  lithology_source, feature_version, updated_at)
SELECT r.route_id, r.source, a.path, r.type_group, p.point_key, p.lat, p.aspect_deg, p.slope_deg, p.elevation_m,
       p.lithology, r.pitches, r.length_m, p.h3_r5, p.grid_bucket, p.dem_res_m, p.aspect_confidence,
       CASE WHEN p.point_key IS NULL THEN 'none' ELSE a.coord_precision END, p.lithology_source, '{FEATURE_VERSION}', now()
FROM canonical_routes r
JOIN canonical_areas a ON a.area_id = r.area_id
LEFT JOIN feature_points p ON p.point_key = a.point_key
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


async def epqs_check(
    conn: AsyncConnection,
    client: EpqsClient,
    *,
    sampler: Sampler = sample_points,
    seed: int = 42,
    per_class: int = PER_CLASS_TARGET,
    probe_batch: int = 500,
    max_probe: int = 6000,
) -> dict[str, object]:
    points = [(float(la), float(lo), float(e)) for la, lo, e in (await conn.execute(text(
        "SELECT lat, lon, elevation_m FROM feature_points WHERE elevation_m IS NOT NULL ORDER BY point_key"))).all()]
    random.Random(seed).shuffle(points)
    chosen: dict[str, list[tuple[float, float, float]]] = {name: [] for name, _, _ in SLOPE_CLASSES}
    probed = 0
    for i in range(0, min(len(points), max_probe), probe_batch):
        batch = points[i : i + probe_batch]
        probed += len(batch)
        for (la, lo, e), slope in zip(batch, local_slopes([(la, lo) for la, lo, _ in batch], sampler)):
            if slope is not None and len(chosen[slope_class(slope)]) < per_class:
                chosen[slope_class(slope)].append((la, lo, e))
        if all(len(v) >= per_class for v in chosen.values()):
            break
    rows = [(cls, e, client.elevation(la, lo)) for cls, pts in chosen.items() for la, lo, e in pts]
    return compare(rows) | {"probed": probed, "failed_calls": client.failed_calls}


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.pipelines.ingest_log import finish_run, start_run, write_quarantine

    engine = ingest_engine()
    report = ValidationReport(f"static_features:{args.step}")
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source=f"static_features:{args.step}", window_start=None, window_end=None,
                                     content_sha256=None)
            if args.step == "points":
                result: dict[str, object] = {"points": await upsert_points(conn, run_id=run_id, report=report)}
            elif args.step == "elevation":
                result = dict(await fill_elevation(conn, run_id=run_id, limit=args.limit))
            elif args.step == "routes":
                result = {"routes": await build_route_features(conn)}
            else:
                result = await epqs_check(conn, EpqsClient())
            if report.quarantined_total():
                await write_quarantine(conn, run_id, report)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=0,
                             problems=[json.dumps(result, default=str)])
    finally:
        await engine.dispose()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["points", "elevation", "routes", "epqs-check"])
    parser.add_argument("--limit", type=int, default=5000)
    parsed = parser.parse_args()
    out = asyncio.run(_main(parsed))
    print(json.dumps(out, sort_keys=True, default=str))
    if parsed.step == "epqs-check" and out.get("passed") != 1:
        sys.exit(1)
```

The join is on stored `canonical_areas.point_key`, written in Python by `upsert_points`, so no SQL string formatting has to agree with Python rounding (review S3). Weekly catalog reloads keep `point_key` (the catalog upsert does not list the column) and the next `points` step recomputes it. Append `"app.pipelines.static_features"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_static_features.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/static_features.py backend/tests/test_static_features.py backend/pyproject.toml && git commit -m "feat(pipelines): per-point features with timezone and bucket registration, elevation fill, route table, EPQS check"`

---

### Task 5: Acceptance cells, workflow, PR 2b-2a docs

**Files:**
- Create: `backend/tests/verify/test_phase2b_features.py`, `.github/workflows/data-static-features.yml`
- Modify: `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`

- [ ] **Step 1: Cells** — `backend/tests/verify/test_phase2b_features.py` (plan 1's `tests.verify._db.fetch`: analyst, verify-full via `connect_args_for`, skips without `VERIFY_DATABASE_URL`):

```python
import json

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db


def test_every_point_has_an_elevation_and_consistent_source():
    [row] = fetch("SELECT count(*) FILTER (WHERE dem_source IS NULL) AS pending, "
                  "count(*) FILTER (WHERE elevation_m IS NULL) AS missing, "
                  "count(*) FILTER (WHERE elevation_m IS NULL AND dem_source IN ('3dep_13', '3dep_2')) AS inconsistent, "
                  "count(*) AS n FROM feature_points")
    print(row)
    assert row["n"] > 0
    assert row["pending"] == 0, "points still awaiting a DEM read (transient failures); rerun the elevation step"
    assert row["inconsistent"] == 0
    assert row["missing"] == 0, "100% elevation coverage (spec 2b-2); inspect dem_source for the misses"


def test_every_point_has_a_timezone():
    [row] = fetch("SELECT count(*) AS n FROM feature_points WHERE tz IS NULL")
    assert row["n"] == 0


def test_epqs_check_passed_on_enough_points():
    [row] = fetch("SELECT validation_report->'problems'->>0 AS result FROM source_ingest_log "
                  "WHERE source = 'static_features:epqs-check' AND status = 'ok' ORDER BY finished_at DESC LIMIT 1")
    result = json.loads(row["result"])
    print(result["classes"])
    assert result["compared"] >= 180 and result["gross"] == 0 and result["passed"] == 1


def test_every_point_bucket_has_a_normals_status_and_complete_means_twelve_real_months():
    [missing] = fetch("SELECT count(*) AS n FROM (SELECT DISTINCT grid_bucket FROM feature_points) b "
                      "LEFT JOIN cell_normals_status s USING (grid_bucket) WHERE s.grid_bucket IS NULL")
    assert missing["n"] == 0
    [bad] = fetch("SELECT count(*) AS n FROM cell_normals_status s WHERE s.status = 'complete' AND "
                  "(SELECT count(*) FROM cell_climate_normals c WHERE c.grid_bucket = s.grid_bucket "
                  " AND c.tmax_mean IS NOT NULL AND c.tmin_mean IS NOT NULL) <> 12")
    assert bad["n"] == 0
    [pending] = fetch("SELECT count(*) FILTER (WHERE s.status = 'pending') AS pending, count(*) AS n "
                      "FROM (SELECT DISTINCT grid_bucket FROM feature_points) b JOIN cell_normals_status s USING (grid_bucket)")
    print(pending, "pending buckets read as missing normals until the next Open-Meteo Pro window")


def test_every_scorable_route_has_features_with_elevation_unless_it_has_no_location():
    [row] = fetch("SELECT count(*) FILTER (WHERE f.route_id IS NULL) AS no_row, "
                  "count(*) FILTER (WHERE f.coord_precision <> 'none' AND f.elevation_m IS NULL) AS no_elevation, "
                  "count(*) FILTER (WHERE f.coord_precision = 'none') AS no_location "
                  "FROM scorable_routes s LEFT JOIN route_static_features f USING (route_id)")
    print(row)
    assert row["no_row"] == 0 and row["no_elevation"] == 0


def test_scoring_unit_features_has_twelve_months_per_route():
    [row] = fetch("SELECT count(*) AS n FROM (SELECT unit_id FROM scoring_unit_features WHERE unit_kind = 'route' "
                  "GROUP BY unit_id HAVING count(*) <> 12) x")
    assert row["n"] == 0


def test_every_area_with_routes_and_coordinates_points_at_a_feature_point():
    [row] = fetch("SELECT count(*) AS n FROM canonical_areas a WHERE a.lat IS NOT NULL AND a.retired_at IS NULL "
                  "AND EXISTS (SELECT 1 FROM canonical_routes r WHERE r.area_id = a.area_id AND r.retired_at IS NULL) "
                  "AND NOT EXISTS (SELECT 1 FROM feature_points p WHERE p.point_key = a.point_key)")
    assert row["n"] == 0
```

`fetch` returns `asyncpg.Record`s, read by key; `print(row)` shows the counts under `-s`.

- [ ] **Step 2: Workflow** `.github/workflows/data-static-features.yml`. No Open-Meteo key and no spend: normals are plan 3's January job. There are no dispatch inputs; any future input is passed to shell through `env:` only.

```yaml
name: data-static-features

on:
  schedule:
    - cron: "47 8 * * 1"   # Mondays, after the weekly catalog load
  workflow_dispatch:

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
      - run: uv run python -m app.pipelines.static_features routes
      - name: Open an issue on failure
        if: failure()
        working-directory: .
        env:
          GH_TOKEN: ${{ github.token }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: gh issue create --title "data-static-features failed ($(date -u +%F))" --body "Run: $RUN_URL"
```

New points from a new catalog area get `pending` normals status and a `grid_bucket_series` row in the `points` step; plan 3's January `era5_window` job fills them `[assumes D8]`. Until then Phase 3 reads their normals as missing.

- [ ] **Step 3: Docs** — CHANGELOG "Phase 2b MVP-1 features (PR 2b-2a)"; CLAUDE.md commands for `static_features`; DEPLOYMENT.md "Data workflows": `data-static-features.yml` (no Open-Meteo secret; normals come from plan 3's window); DATABASE_STRUCTURE: `feature_points` (incl. the `slope_deg` definition and `tz`), `route_static_features`, `canonical_areas.point_key`, `internal.r10_unresolved`, view `scoring_unit_features` as the Phase 3 MVP-1 read contract (12 rows per unit, normals NULL unless `complete`/`insufficient`, `normals_status` values). Specs are not edited; the PR description notes that amendment question 5 is answered by plan 3 (normals) and this PR (elevation, view).
- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/` → green.
- [ ] **Step 5: Commit** — `git add backend/tests/verify/test_phase2b_features.py .github/workflows/data-static-features.yml CHANGELOG.md CLAUDE.md DEPLOYMENT.md data/DATABASE_STRUCTURE.md && git commit -m "feat(ops): static-features workflow and MVP-1 feature acceptance cells"`

---

# PR 2b-2b — `feat/p2b-terrain-lithology` (v2.2 features)

### Task 6: Horn slope/aspect and crag aspect

**Files:**
- Create: `backend/app/pipelines/terrain.py`, `backend/tests/test_terrain.py`
- Modify: `backend/app/pipelines/static_features.py` (step `terrain`), `backend/tests/test_static_features.py`, `backend/pyproject.toml`

**Interfaces:**
- Produces: `TERRAIN_VERSION = "t-v1"`, `STEEP_DEG = 40.0`, `RADIUS_M = 200.0`, `MIN_CELLS = 5`, `horn(z, dx, dy) -> tuple[NDArray, NDArray]` (slope°, aspect° clockwise from north, interior cells, NaN border), `circular_mean(deg: Sequence[float]) -> tuple[float, float]` (mean°, resultant length), `crag_aspect(z, dx, dy, center_rc) -> tuple[float | None, float | None, float | None]` (aspect°, confidence, max slope° within radius); in `static_features`: `async fill_terrain(conn, *, run_id, window_reader=dem.read_window, limit: int) -> dict[str, int]` (`done`, `aspect_null`, `retry`).

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


def test_all_nan_window_gives_no_slope():
    assert crag_aspect(np.full((41, 41), np.nan), 10.0, 10.0, (20, 20)) == (None, None, None)
```

Append to `backend/tests/test_static_features.py`:

```python
import numpy as np

from app.pipelines.dem import TransientTileError
from app.pipelines.static_features import fill_terrain


@requires_pg
def test_terrain_fills_and_transient_reads_stay_unfilled():
    with migrated_db("0010_static_features", seed_sql=SEED) as name:
        run_id = uuid.uuid4()
        _run(name, lambda c: _points(c, run_id))
        _run(name, lambda c: fill_elevation(
            c, run_id=run_id, sampler=lambda pts: [Elevation(2400.0, "3dep_13", 10.0)] * len(pts), limit=100))

        def flaky(lat, lon, radius_m):
            raise TransientTileError("timeout")

        assert _run(name, lambda c: fill_terrain(c, run_id=run_id, window_reader=flaky, limit=10)) == {
            "done": 0, "aspect_null": 0, "retry": 1}
        steep = (np.arange(41)[:, None] * 15.0 + np.zeros((1, 41))).astype(np.float64)
        stats = _run(name, lambda c: fill_terrain(
            c, run_id=run_id, window_reader=lambda la, lo, r: (steep, 10.0, 10.0, (20, 20)), limit=10))
        assert stats == {"done": 1, "aspect_null": 0, "retry": 0}
        rows = _run(name, lambda c: c.execute(text("SELECT round(aspect_deg), terrain_version FROM feature_points")))
        assert [tuple(r) for r in rows] == [(0.0, "t-v1")]
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/terrain.py`:

```python
"""Slope and aspect by Horn's method; crag aspect is the circular mean of cells steeper
than 40° within 200 m, with the mean resultant length as confidence (spec §Static
features). Fewer than 5 steep cells → NULL aspect (route coordinates are crag-level).
The slope reported is the maximum within the radius (crag steepness), not the point slope."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

TERRAIN_VERSION = "t-v1"
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
    valid = inside & ~np.isnan(slope)
    max_slope = float(np.max(slope[valid])) if np.any(valid) else None
    steep = valid & (slope > STEEP_DEG)
    if int(np.count_nonzero(steep)) < MIN_CELLS:
        return None, None, max_slope
    mean, r = circular_mean(aspect[steep].tolist())
    return mean, r, max_slope
```

Check of the east-facing test: `dz_east = -1` means elevation falls toward the east; `dzdx = -1`, `dzdy = 0`, `raw = atan2(0, 1) = 0` → `90 - 0 = 90` (east) ✓. North test: `dzdy = +1` → `raw = 90` → `0` ✓. The static-features steep fixture rises 15 m per 10 m row southward (56°, north-facing).

In `static_features.py` add (imports `from app.pipelines.dem import TransientTileError, read_window`, `from rasterio.errors import RasterioIOError`, `from app.pipelines.terrain import RADIUS_M, TERRAIN_VERSION, crag_aspect`, and `import numpy as np`, `from numpy.typing import NDArray`):

```python
WindowReader = Callable[[float, float, float], "tuple[NDArray[np.float64], float, float, tuple[int, int]] | None"]


async def fill_terrain(
    conn: AsyncConnection,
    *,
    run_id: uuid.UUID,
    window_reader: WindowReader = read_window,
    limit: int,
) -> dict[str, int]:
    todo = [(str(k), float(la), float(lo)) for k, la, lo in (await conn.execute(text(
        "SELECT point_key, lat, lon FROM feature_points WHERE terrain_version IS NULL "
        "AND dem_source IN ('3dep_13', '3dep_2') ORDER BY point_key LIMIT :n"), {"n": limit})).all()]
    updates: list[dict[str, object]] = []
    retry = 0
    for key, lat, lon in todo:
        try:
            window = window_reader(lat, lon, RADIUS_M)
        except (TransientTileError, RasterioIOError):
            retry += 1
            continue
        aspect, confidence, max_slope = crag_aspect(*window) if window is not None else (None, None, None)
        updates.append({"k": key, "s": max_slope, "a": aspect, "c": confidence, "tv": TERRAIN_VERSION, "run": run_id})
    if updates:
        await conn.execute(text(
            "UPDATE feature_points SET slope_deg = :s, aspect_deg = :a, aspect_confidence = :c, terrain_version = :tv, "
            "updated_at = now(), run_id = :run WHERE point_key = :k"), updates)
    return {"done": len(updates), "aspect_null": sum(1 for u in updates if u["a"] is None), "retry": retry}
```

and extend `_main` and the CLI: `choices=["points", "elevation", "routes", "epqs-check", "terrain"]`, with the branch `elif args.step == "terrain": result = dict(await fill_terrain(conn, run_id=run_id, limit=args.limit))` placed before the final `else`. Append `"app.pipelines.terrain"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_terrain.py tests/test_static_features.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/terrain.py backend/app/pipelines/static_features.py backend/tests/test_terrain.py backend/tests/test_static_features.py backend/pyproject.toml && git commit -m "feat(pipelines): Horn slope/aspect and crag aspect with confidence"`

---

### Task 7: Macrostrat lithology `[assumes D15]`

D15 keeps Macrostrat only. Neon has no storage cap (review claim 30); loading SGMC polygons (~0.5–1 GB) would cost about $0.35/GB-month, so the choice is cost against the measured miss rate (runbook Task 10), not a ceiling.

**Files:**
- Create: `backend/app/pipelines/lithology.py`, `backend/tests/test_lithology.py`, `backend/tests/fixtures/macrostrat_sample.json`
- Modify: `backend/app/pipelines/static_features.py` (step `lithology`), `backend/tests/test_static_features.py`, `.github/workflows/data-static-features.yml`, `backend/pyproject.toml`, `CHANGELOG.md`

**Interfaces:**
- Produces: `MACROSTRAT_URL = "https://macrostrat.org/api/v2/geologic_units/map"`, `CLASSES: tuple[str, ...]`, `normalize_lithology(text: str | None) -> str | None`, `class MacrostratUnavailable(Exception)`, `class MacrostratClient(*, transport=None, pause_s=0.2, retries=3, sleep=time.sleep)` with `lithology(lat: float, lon: float) -> str | None` (None only for a 200 with no classifiable unit; raises `MacrostratUnavailable` after retries), in `static_features`: `async fill_lithology(conn, *, run_id, client, limit: int) -> dict[str, int]` (`done`, `none`, `retry`).

- [ ] **Step 1: Record the fixture (agent)** — `curl -s 'https://macrostrat.org/api/v2/geologic_units/map?lat=40.37&lng=-105.52&scale=large' | python3 -m json.tool > backend/tests/fixtures/macrostrat_sample.json`; confirm the unit objects under `success.data` carry a `lith` text field (Macrostrat is CC-BY 4.0; a one-point fixture with attribution in the test docstring is fine). If the field is named differently, adjust `_lith_text` only.

- [ ] **Step 2: Failing tests** — `backend/tests/test_lithology.py`:

```python
"""Fixture: Macrostrat (CC-BY 4.0), https://macrostrat.org."""

import json
from pathlib import Path

import httpx
import pytest

from app.pipelines.lithology import MacrostratClient, MacrostratUnavailable, normalize_lithology

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "macrostrat_sample.json").read_text())


def _client(handler):
    return MacrostratClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


@pytest.mark.parametrize("text,cls", [
    ("Major:{granite}, Minor:{pegmatite}", "granitic"), ("sandstone, shale", "sandstone"),
    ("limestone", "carbonate"), ("basalt flows", "volcanic"), ("biotite gneiss", "metamorphic"),
    ("alluvium", "unconsolidated"), ("glacier ice", "ice"), ("mudstone", "fine_sedimentary"), (None, None), ("", None),
])
def test_normalizer(text, cls):
    assert normalize_lithology(text) == cls


def test_client_reads_the_fixture():
    assert _client(lambda r: httpx.Response(200, json=FIXTURE)).lithology(40.37, -105.52) in {
        "granitic", "metamorphic", "other"}


def test_empty_result_is_none_not_other():
    assert _client(lambda r: httpx.Response(200, json={"success": {"data": []}})).lithology(40.0, -105.0) is None


def test_server_errors_are_retried_then_raised_never_stored_as_none():
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 2 else httpx.Response(200, json={"success": {"data": [{"lith": "granite"}]}})

    assert _client(flaky).lithology(40.0, -105.0) == "granitic"
    with pytest.raises(MacrostratUnavailable):
        _client(lambda r: httpx.Response(503)).lithology(40.0, -105.0)
```

Append to `backend/tests/test_static_features.py`:

```python
from app.pipelines.lithology import MacrostratUnavailable
from app.pipelines.static_features import fill_lithology


class _Litho:
    def __init__(self, answer):
        self.answer = answer

    def lithology(self, lat, lon):
        if self.answer == "down":
            raise MacrostratUnavailable("503")
        return self.answer


@requires_pg
def test_lithology_misses_are_none_and_outages_are_retried():
    with migrated_db("0010_static_features", seed_sql=SEED) as name:
        run_id = uuid.uuid4()
        _run(name, lambda c: _points(c, run_id))
        assert _run(name, lambda c: fill_lithology(c, run_id=run_id, client=_Litho("down"), limit=10)) == {
            "done": 0, "none": 0, "retry": 1}
        assert _run(name, lambda c: fill_lithology(c, run_id=run_id, client=_Litho(None), limit=10)) == {
            "done": 1, "none": 1, "retry": 0}
        rows = _run(name, lambda c: c.execute(text("SELECT lithology, lithology_source FROM feature_points")))
        assert [tuple(r) for r in rows] == [(None, "none")]
```

- [ ] **Step 3: Implement** `backend/app/pipelines/lithology.py`:

```python
"""Macrostrat point lithology (CC-BY 4.0), normalized to ~10 classes. A 200 with no
classifiable unit is NULL with lithology_source 'none' (D15); an outage is retried and,
if it persists, raises so the point stays unfilled for the next run. Nothing is guessed."""

from __future__ import annotations

import time
from collections.abc import Callable

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


class MacrostratUnavailable(Exception):
    """No 200 response after retries; the point is left for the next run."""


def normalize_lithology(text: str | None) -> str | None:
    t = f" {(text or '').lower()} "
    if not t.strip():
        return None
    for cls, words in KEYWORDS:
        if any(w in t for w in words):
            return cls
    return "other"


class MacrostratClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        pause_s: float = 0.2,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(transport=transport, timeout=30.0)
        self._pause = pause_s
        self._retries = retries
        self._sleep = sleep

    @staticmethod
    def _lith_text(unit: dict[str, object]) -> str | None:
        value = unit.get("lith")
        return value if isinstance(value, str) else None

    def lithology(self, lat: float, lon: float) -> str | None:
        for attempt in range(self._retries + 1):
            try:
                response: httpx.Response | None = self._client.get(
                    MACROSTRAT_URL, params={"lat": lat, "lng": lon, "scale": "large"})
            except httpx.HTTPError:
                response = None
            self._sleep(self._pause)
            if response is not None and response.status_code == 200:
                units = (response.json().get("success") or {}).get("data") or []
                for unit in units:
                    if isinstance(unit, dict) and (cls := normalize_lithology(self._lith_text(unit))) is not None:
                        return cls
                return None
            self._sleep(2.0 ** (attempt + 1))
        raise MacrostratUnavailable(f"no 200 after {self._retries + 1} attempts")
```

In `static_features.py` add (import `from app.pipelines.lithology import MacrostratClient, MacrostratUnavailable` and `from typing import Protocol`):

```python
class LithologySource(Protocol):
    def lithology(self, lat: float, lon: float) -> str | None: ...


async def fill_lithology(conn: AsyncConnection, *, run_id: uuid.UUID, client: LithologySource, limit: int) -> dict[str, int]:
    todo = [(str(k), float(la), float(lo)) for k, la, lo in (await conn.execute(text(
        "SELECT point_key, lat, lon FROM feature_points WHERE lithology_source IS NULL ORDER BY point_key LIMIT :n"),
        {"n": limit})).all()]
    updates: list[dict[str, object]] = []
    retry = 0
    for key, lat, lon in todo:
        try:
            cls = client.lithology(lat, lon)
        except MacrostratUnavailable:
            retry += 1
            continue
        updates.append({"k": key, "l": cls, "s": "macrostrat" if cls is not None else "none", "run": run_id})
    if updates:
        await conn.execute(text(
            "UPDATE feature_points SET lithology = :l, lithology_source = :s, updated_at = now(), run_id = :run "
            "WHERE point_key = :k"), updates)
    return {"done": len(updates), "none": sum(1 for u in updates if u["l"] is None), "retry": retry}
```

Extend the CLI `choices` with `"lithology"` and `_main` with `elif args.step == "lithology": result = dict(await fill_lithology(conn, run_id=run_id, client=MacrostratClient(), limit=args.limit))` before the final `else`. In `.github/workflows/data-static-features.yml` insert, between the `elevation` and `routes` steps:

```yaml
      - run: uv run python -m app.pipelines.static_features terrain --limit 5000
      - run: uv run python -m app.pipelines.static_features lithology --limit 2000
```

CHANGELOG "Phase 2b terrain and lithology (PR 2b-2b)". Append `"app.pipelines.lithology"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_lithology.py tests/test_static_features.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/lithology.py backend/app/pipelines/static_features.py backend/tests/test_lithology.py backend/tests/test_static_features.py backend/tests/fixtures/macrostrat_sample.json .github/workflows/data-static-features.yml backend/pyproject.toml CHANGELOG.md && git commit -m "feat(pipelines): Macrostrat lithology normalized to ten classes (misses NULL, outages retried)"`

---

# PR 2b-2c — `feat/p2b-r10-relink` `[assumes D16]`

### Task 8: R10 relink job and owner review round-trip

**Files:**
- Create: `backend/app/data/repair/legacy_links.py`, `backend/tests/test_legacy_links.py`
- Modify: `backend/app/data/repair/__main__.py`, `backend/db/roles/grants_phase2.sql`, `backend/pyproject.toml`

**Interfaces:**
- Consumes: `match.climb_score`, `match.decide`, `match.name_similarity`, `match.h3_neighbourhood`, `match.area_score` (plan 4 final: `AUTO_LINK` 0.90, `NO_LINK_BELOW` 0.80, `AUTO_MARGIN` 0.05, `REVIEW_CAP` 0.89), `route_types.map_type_group`, `route_types.flags_from_mp_type` (plan 4); `textsim.place_key`, `geo.haversine_km` (plan 2); `ingest_log.start_run`/`finish_run`; `internal.mp_route_links`/`mp_area_links` (plan 4); `internal.r10_unresolved` (Task 1); `objectives(objective_id, name, lat, lon)` when it exists (plan 6).
- Produces: `R10_VERSION = "r10-v2"`, `R10_METHODS`, `OBJECTIVE_RADIUS_KM = 3.0`, `@dataclass(frozen=True) Link(accident_id, route_id, area_id, objective_id, method, score)`, `@dataclass(frozen=True) Unresolved(accident_id, legacy_route_id, legacy_mountain_id, candidate_kind, best_candidate_id, best_score, reason)`, `async plan_links(conn) -> tuple[list[Link], list[Unresolved]]`, `async run_r10(conn, args) -> dict[str, object]` registered as repair step `r10`, `async export_review(conn, path: Path) -> int`, `async import_decisions(conn, path: Path) -> dict[str, int]`, CLI `python -m app.data.repair.legacy_links export|import PATH`.

Linking rules for each accident (first hit wins). R10 **never** writes `accidents.route_id`/`mountain_id`; plan 6's `0012` drops them after every legacy-carrying accident is linked or owner-decided.
1. `accidents.mp_route_id` → `internal.mp_route_links` → route (`mp_route_link`, 1.0).
2. Legacy `routes` row with a numeric `routes.mp_route_id` → rule 1 through it (`legacy_mp_id`). (The old rule "legacy `route_id` equal to an `mp_route_id`" is subsumed by rule 1, which reads `accidents.mp_route_id` directly.)
3. Legacy `routes` row with coordinates → climb candidates in canonical areas within its H3 r7 neighbourhood. The legacy row's type group comes from `map_type_group(flags_from_mp_type(routes.type), [routes.grade], None)`, so `climb_score` can reach 1.0 when the grades are comparable (review K1: the old `None` type capped it below `AUTO_LINK`). Without a comparable grade (legacy grade missing, or a different grade system) plan 4 renormalises and caps the score at `REVIEW_CAP` 0.89, so the pair goes to review, never auto-link. `decide` → `link` (≥ 0.90, unique, ≥ 0.05 over the runner-up) → route (`legacy_match`); `review` (0.80–0.90, capped, or no margin) → unresolved `review_band` with the best route; `no_link` → next rule.
4. `accidents.mp_route_id`'s MP location → `mp_area_links` → area (`mp_area_link`).
5. Legacy mountain (`accidents.mountain_id`, else the legacy route's `mountain_id`) with coordinates: before plan 6 (no `objectives` table) → unresolved `awaiting_objectives`; after, objectives within 3 km scored by `name_similarity(place_key(mountain), place_key(objective))` and decided by `decide`: `link` → objective (`legacy_mountain_objective`); `review` → unresolved `objective_candidate`.
6. Legacy route with coordinates and area candidates → unresolved `area_candidate` with the best `area_score` area (never automatic: legacy rows carry no area tokens, so `area_score` is capped at `REVIEW_CAP` 0.89).
7. Otherwise a legacy-carrying accident → unresolved `no_candidate` (it had coordinates to search from) or `no_coordinates`. An accident without legacy links and without a rule 1/4 hit is simply not linked here (it keeps its own coordinates).

Owner decisions in `internal.r10_unresolved` (`link` with exactly one decided id, or `no_link`) are applied on every run as `owner_r10` links and are never overwritten by the rules; undecided rows are refreshed each run and deleted once a rule resolves them.

- [ ] **Step 1: Failing tests** — `backend/tests/test_legacy_links.py`:

```python
import argparse
import asyncio
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair.legacy_links import export_review, import_decisions, run_r10
from tests.pgtest import migrated_db, requires_pg, sa_url

PIN = "0010_static_features"
AREA = "00000000-0000-0000-0000-00000000000a"
ROUTE = "00000000-0000-0000-0000-0000000000c1"
OBJECTIVE = "00000000-0000-0000-0000-0000000000e1"
SEED = f"""
INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (900000101, 'Fixture Crag', 40.0, -105.3);
INSERT INTO mp_routes (mp_route_id, name, location_id) VALUES (900000001, 'Fixture Route', 900000101), (900000002, 'Other', 900000101);
INSERT INTO canonical_areas (area_id, name, path, lat, lon, coord_precision, source, redistributable) VALUES
  ('{AREA}', 'Fixture Crag', '0000000000000000000000000000000a', 40.0, -105.3, 'area_centroid', 'openbeta', true);
INSERT INTO canonical_routes (route_id, area_id, name, grade, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable) VALUES
  ('{ROUTE}', '{AREA}', 'Fixture Route', '5.9', '{{trad}}', 'trad', 'rt-v1', false, true, 'openbeta', true);
INSERT INTO internal.mp_route_links VALUES ('{ROUTE}', 900000001, 1.0, 'ob_mp_id');
INSERT INTO internal.mp_area_links VALUES ('{AREA}', 900000101, 1.0, 'auto');
INSERT INTO mountains (mountain_id, name, latitude, longitude) VALUES (7, 'Fixture Mountain', 40.2, -105.6);
INSERT INTO routes (route_id, name, latitude, longitude, grade, type) VALUES
  (55, 'Fixture Route', 40.0005, -105.3, '5.9', 'Trad'),
  (56, 'Fixture Route', 40.0005, -105.3, '5.9+', 'Trad'),
  (57, 'Fixture Lonely', 45.0, -110.0, '5.8', 'Trad'),
  (58, 'Fixture Route', 40.0005, -105.3, NULL, 'Trad');
INSERT INTO accidents (accident_id, mp_route_id, route_id, mountain_id) VALUES
  (1, 900000001, NULL, 7), (2, NULL, 55, NULL), (3, 900000002, NULL, NULL), (4, NULL, NULL, 7),
  (5, NULL, 56, NULL), (6, NULL, 57, NULL), (7, NULL, 58, NULL);
"""
OBJECTIVES = f"""
CREATE TABLE objectives (objective_id uuid PRIMARY KEY, name text, lat double precision, lon double precision);
INSERT INTO objectives VALUES ('{OBJECTIVE}', 'Fixture Mountain', 40.201, -105.601);
"""


def _scenario(name, *, sql_before=None):
    async def go():
        engine = create_async_engine(sa_url(name))
        try:
            async with engine.begin() as conn:
                if sql_before:
                    await conn.execute(text(sql_before))
                result = await run_r10(conn, argparse.Namespace(apply=True))
                links = [tuple(r) for r in (await conn.execute(text(
                    "SELECT accident_id, canonical_route_id::text, canonical_area_id::text, objective_id::text, method "
                    "FROM accident_route_links ORDER BY 1"))).all()]
                unresolved = [tuple(r) for r in (await conn.execute(text(
                    "SELECT accident_id, reason, candidate_kind, best_candidate_id::text, owner_decision "
                    "FROM internal.r10_unresolved ORDER BY 1"))).all()]
                legacy = (await conn.execute(text(
                    "SELECT count(*) FROM accidents WHERE route_id IS NOT NULL OR mountain_id IS NOT NULL"))).scalar_one()
            return result, links, unresolved, int(legacy)
        finally:
            await engine.dispose()

    return asyncio.run(go())


@requires_pg
def test_unresolved_links_are_recorded_never_nulled():
    with migrated_db(PIN, seed_sql=SEED) as name:
        result, links, unresolved, legacy = _scenario(name)
    assert links == [
        (1, ROUTE, None, None, "mp_route_link"),
        (2, ROUTE, None, None, "legacy_match"),
        (3, None, AREA, None, "mp_area_link"),
    ]
    assert unresolved == [
        (4, "awaiting_objectives", None, None, None),
        (5, "review_band", "route", ROUTE, None),
        (6, "no_candidate", None, None, None),
        (7, "review_band", "route", ROUTE, None),
    ]
    assert legacy == 6
    assert result["unresolved"] == {"awaiting_objectives": 1, "no_candidate": 1, "review_band": 2}


@requires_pg
def test_owner_decisions_are_applied_and_survive_reruns(tmp_path: Path):
    with migrated_db(PIN, seed_sql=SEED) as name:
        _scenario(name)

        async def round_trip():
            engine = create_async_engine(sa_url(name))
            try:
                async with engine.begin() as conn:
                    exported = await export_review(conn, tmp_path / "r10.csv")
                    lines = (tmp_path / "r10.csv").read_text().splitlines()
                    header, rows = lines[0], lines[1:]
                    decided = []
                    for row in rows:
                        cells = row.split(",")
                        if cells[0] == "5":
                            cells[-2], cells[-1] = "link", ROUTE
                        if cells[0] == "6":
                            cells[-2] = "no_link"
                        decided.append(",".join(cells))
                    (tmp_path / "r10.csv").write_text("\n".join([header, *decided]) + "\n")
                    imported = await import_decisions(conn, tmp_path / "r10.csv")
                return exported, imported
            finally:
                await engine.dispose()

        exported, imported = asyncio.run(round_trip())
        assert exported == 4 and imported == {"link": 1, "no_link": 1, "rejected": 0}
        _, links, unresolved, _ = _scenario(name)
        _, links_again, unresolved_again, _ = _scenario(name)
    assert (5, ROUTE, None, None, "owner_r10") in links
    assert [(u[0], u[4]) for u in unresolved] == [(4, None), (5, "link"), (6, "no_link"), (7, None)]
    assert (links, unresolved) == (links_again, unresolved_again)


@requires_pg
def test_rerun_after_objectives_links_the_mountain():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _scenario(name)
        _, links, unresolved, _ = _scenario(name, sql_before=OBJECTIVES)
    assert (4, None, None, OBJECTIVE, "legacy_mountain_objective") in links
    assert 4 not in [u[0] for u in unresolved]
```

Scores behind the fixture (plan 4 final matcher, `match.py` Task 5): accident 2's legacy route 55 is `Trad`/`5.9` → `trad`, same name and grade as `ROUTE` → 0.6 + 0.25 + 0.15 = 1.0, the only candidate (runner-up 0, margin ≥ 0.05) → `link`. Route 56 (`5.9+`) shares the YDS base grade → 0.6 + 0.125 + 0.15 = 0.875 → `review`. Route 58 has no grade, so the grade is not comparable and plan 4 renormalises and caps the score: min(0.89, (0.6 + 0.15) / 0.75) = 0.89 → `review`, never auto-link. Route 57 is ~700 km from any area → `no_candidate`. Accident 1 has both an MP route link and a mountain; rule 1 resolves it. The mountain → objective case scores `name_similarity` 1.0 for identical names, a single candidate → `link`. The tests pin to `0010_static_features` (review P2): plan 6's `0012` drops the legacy columns, and this job must be testable on the schema it runs against. Plan 6 owns the objective-name export test (pinned to `0011_objectives`); the export test here checks only the round-trip at `0010`.

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/data/repair/legacy_links.py`:

```python
"""R10 (D16): move every legacy accident link into accident_route_links. A link the rules
cannot resolve is never nulled: it goes to internal.r10_unresolved for owner review, and
plan 6's 0012 refuses to drop the legacy columns until every legacy-carrying accident is
linked or owner-decided. Re-run after plan 6 loads objectives (legacy mountains)."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import h3
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.geo import haversine_km
from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.match import area_score, climb_score, decide, h3_neighbourhood, name_similarity
from app.pipelines.route_types import flags_from_mp_type, map_type_group
from app.pipelines.textsim import place_key
from app.pipelines.validate import ValidationReport

R10_VERSION = "r10-v2"
R10_METHODS = ("mp_route_link", "legacy_mp_id", "legacy_match", "mp_area_link", "legacy_mountain_objective", "owner_r10")
OBJECTIVE_RADIUS_KM = 3.0
REVIEW_COLUMNS = ("accident_id", "reason", "legacy_route_name", "legacy_mountain_name", "candidate_kind",
                  "best_candidate_id", "best_candidate_name", "best_score", "owner_decision", "decided_id")


@dataclass(frozen=True)
class Link:
    accident_id: int
    route_id: uuid.UUID | None
    area_id: uuid.UUID | None
    objective_id: uuid.UUID | None
    method: str
    score: float | None


@dataclass(frozen=True)
class Unresolved:
    accident_id: int
    legacy_route_id: int | None
    legacy_mountain_id: int | None
    candidate_kind: str | None
    best_candidate_id: uuid.UUID | None
    best_score: float | None
    reason: str


def _uuid(v: object) -> uuid.UUID:
    return uuid.UUID(str(v))


async def _objectives(conn: AsyncConnection) -> list[tuple[uuid.UUID, str, float, float]] | None:
    if (await conn.execute(text("SELECT to_regclass('public.objectives')"))).scalar() is None:
        return None
    return [(_uuid(o), str(n), float(la), float(lo)) for o, n, la, lo in (await conn.execute(text(
        "SELECT objective_id, name, lat, lon FROM objectives WHERE lat IS NOT NULL AND lon IS NOT NULL"))).all()]


async def plan_links(conn: AsyncConnection) -> tuple[list[Link], list[Unresolved]]:
    route_by_mp = {int(m): _uuid(r) for r, m in (await conn.execute(text(
        "SELECT route_id, mp_route_id FROM internal.mp_route_links"))).all()}
    area_by_loc = {int(m): _uuid(a) for a, m in (await conn.execute(text(
        "SELECT area_id, mp_location_id FROM internal.mp_area_links"))).all()}
    loc_by_route = {int(r): int(loc) for r, loc in (await conn.execute(text(
        "SELECT mp_route_id, location_id FROM mp_routes WHERE location_id IS NOT NULL"))).all()}
    legacy = {int(i): (str(n), m, la, lo, g, t, mt) for i, n, m, la, lo, g, t, mt in (await conn.execute(text(
        "SELECT route_id, name, mp_route_id, latitude, longitude, grade, type, mountain_id FROM routes"))).all()}
    mountains = {int(i): (str(n), la, lo) for i, n, la, lo in (await conn.execute(text(
        "SELECT mountain_id, name, latitude, longitude FROM mountains"))).all()}
    areas = {_uuid(a): (str(n), float(la), float(lo)) for a, n, la, lo in (await conn.execute(text(
        "SELECT area_id, name, lat, lon FROM canonical_areas WHERE lat IS NOT NULL AND retired_at IS NULL"))).all()}
    by_cell: dict[str, list[uuid.UUID]] = defaultdict(list)
    for a, (_, la, lo) in areas.items():
        by_cell[h3.latlng_to_cell(la, lo, 7)].append(a)
    routes_in: dict[uuid.UUID, list[tuple[uuid.UUID, str, str | None, str | None]]] = defaultdict(list)
    for r, a, n, g, t in (await conn.execute(text(
            "SELECT route_id, area_id, name, grade, type_group FROM canonical_routes WHERE retired_at IS NULL"))).all():
        routes_in[_uuid(a)].append((_uuid(r), str(n), g, t))
    objectives = await _objectives(conn)

    links: list[Link] = []
    unresolved: list[Unresolved] = []
    rows = (await conn.execute(text(
        "SELECT accident_id, mp_route_id, route_id, mountain_id FROM accidents ORDER BY accident_id"))).all()
    for accident_id, mp_route_id, route_id, mountain_id in rows:
        a = int(accident_id)
        mp = int(mp_route_id) if mp_route_id is not None else None
        lr = int(route_id) if route_id is not None and int(route_id) in legacy else None
        mountain = int(mountain_id) if mountain_id is not None else (
            int(legacy[lr][6]) if lr is not None and legacy[lr][6] is not None else None)
        carries_legacy = route_id is not None or mountain_id is not None

        def unresolved_row(reason: str, kind: str | None = None, best: uuid.UUID | None = None,
                           score: float | None = None) -> Unresolved:
            return Unresolved(a, int(route_id) if route_id is not None else None,
                              int(mountain_id) if mountain_id is not None else None, kind, best, score, reason)

        if mp is not None and mp in route_by_mp:
            links.append(Link(a, route_by_mp[mp], None, None, "mp_route_link", 1.0))
            continue
        pending: Unresolved | None = None
        cand_areas: list[uuid.UUID] = []
        if lr is not None:
            name, legacy_mp, la, lo, grade, rtype, _ = legacy[lr]
            if legacy_mp and str(legacy_mp).isdigit() and int(legacy_mp) in route_by_mp:
                links.append(Link(a, route_by_mp[int(legacy_mp)], None, None, "legacy_mp_id", 1.0))
                continue
            if la is not None and lo is not None:
                group = map_type_group(flags_from_mp_type(rtype), [grade], None).type_group
                cand_areas = [x for cell in h3_neighbourhood(float(la), float(lo)) for x in by_cell.get(cell, [])]
                scores = [(r, climb_score(name, grade, group, n, g, t)) for x in cand_areas for r, n, g, t in routes_in[x]]
                verdict, best, score = decide(scores)
                if verdict == "link" and best is not None:
                    links.append(Link(a, best, None, None, "legacy_match", score))
                    continue
                if verdict == "review" and best is not None:
                    pending = unresolved_row("review_band", "route", best, score)
        if pending is None and mp is not None and mp in loc_by_route and loc_by_route[mp] in area_by_loc:
            links.append(Link(a, None, area_by_loc[loc_by_route[mp]], None, "mp_area_link", 1.0))
            continue
        if pending is None and mountain is not None and mountain in mountains:
            m_name, m_la, m_lo = mountains[mountain]
            if m_la is not None and m_lo is not None:
                if objectives is None:
                    pending = unresolved_row("awaiting_objectives")
                else:
                    near = [(o, name_similarity(place_key(m_name), place_key(n))) for o, n, la, lo in objectives
                            if haversine_km(float(m_la), float(m_lo), la, lo) <= OBJECTIVE_RADIUS_KM]
                    verdict, best, score = decide(near)
                    if verdict == "link" and best is not None:
                        links.append(Link(a, None, None, best, "legacy_mountain_objective", score))
                        continue
                    if verdict == "review" and best is not None:
                        pending = unresolved_row("objective_candidate", "objective", best, score)
        if pending is None and lr is not None and cand_areas:
            name, _, la, lo, _, _, _ = legacy[lr]
            best_area, best_score = max(
                ((x, area_score(name, [], float(la), float(lo), areas[x][0], [], areas[x][1], areas[x][2]))
                 for x in cand_areas), key=lambda s: s[1])
            pending = unresolved_row("area_candidate", "area", best_area, best_score)
        if pending is not None:
            unresolved.append(pending)
        elif carries_legacy:
            has_coords = (lr is not None and legacy[lr][2] is not None) or (
                mountain is not None and mountain in mountains and mountains[mountain][1] is not None)
            unresolved.append(unresolved_row("no_candidate" if has_coords else "no_coordinates"))
    return links, unresolved


LINK_UPSERT = text(
    "INSERT INTO accident_route_links (accident_id, canonical_route_id, canonical_area_id, objective_id, method, score) "
    "VALUES (:a, :r, :ar, :o, :m, :s) ON CONFLICT (accident_id) DO UPDATE SET "
    "canonical_route_id = EXCLUDED.canonical_route_id, canonical_area_id = EXCLUDED.canonical_area_id, "
    "objective_id = EXCLUDED.objective_id, method = EXCLUDED.method, score = EXCLUDED.score "
    "WHERE accident_route_links.method IN ('mp_route_link', 'legacy_mp_id', 'legacy_match', 'mp_area_link', "
    "'legacy_mountain_objective') AND EXCLUDED.method <> 'owner_r10' OR EXCLUDED.method = 'owner_r10'"
)
UNRESOLVED_UPSERT = text(
    "INSERT INTO internal.r10_unresolved (accident_id, legacy_route_id, legacy_mountain_id, candidate_kind, "
    "best_candidate_id, best_score, reason, run_id) VALUES (:a, :lr, :lm, :k, :b, :s, :reason, :run) "
    "ON CONFLICT (accident_id) DO UPDATE SET legacy_route_id = EXCLUDED.legacy_route_id, "
    "legacy_mountain_id = EXCLUDED.legacy_mountain_id, candidate_kind = EXCLUDED.candidate_kind, "
    "best_candidate_id = EXCLUDED.best_candidate_id, best_score = EXCLUDED.best_score, reason = EXCLUDED.reason, "
    "run_id = EXCLUDED.run_id WHERE internal.r10_unresolved.owner_decision IS NULL"
)
OWNER_LINKS = text(
    "SELECT accident_id, decided_route_id, decided_area_id, decided_objective_id FROM internal.r10_unresolved "
    "WHERE owner_decision = 'link'"
)


async def run_r10(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    links, unresolved = await plan_links(conn)
    decided = {int(a) for (a,) in (await conn.execute(text(
        "SELECT accident_id FROM internal.r10_unresolved WHERE owner_decision IS NOT NULL"))).all()}
    links = [x for x in links if x.accident_id not in decided]
    unresolved = [u for u in unresolved if u.accident_id not in decided]
    owner = [Link(int(a), r, ar, o, "owner_r10", None) for a, r, ar, o in (await conn.execute(OWNER_LINKS)).all()]
    report = ValidationReport("repair:r10")
    for _ in links + owner:
        report.accept()
    for u in unresolved:
        report.quarantine(str(u.accident_id), f"r10_{u.reason}")
    if args.apply:
        run_id = await start_run(conn, source="repair:r10", window_start=None, window_end=None, content_sha256=None)
        if links or owner:
            await conn.execute(LINK_UPSERT, [{"a": x.accident_id, "r": x.route_id, "ar": x.area_id, "o": x.objective_id,
                                              "m": x.method, "s": x.score} for x in links + owner])
        if unresolved:
            await conn.execute(UNRESOLVED_UPSERT, [
                {"a": u.accident_id, "lr": u.legacy_route_id, "lm": u.legacy_mountain_id, "k": u.candidate_kind,
                 "b": u.best_candidate_id, "s": u.best_score, "reason": u.reason, "run": run_id} for u in unresolved])
        await conn.execute(text(
            "DELETE FROM internal.r10_unresolved WHERE owner_decision IS NULL AND NOT (accident_id = ANY(:keep))"),
            {"keep": [u.accident_id for u in unresolved]})
        await finish_run(conn, run_id, status="ok", report=report, rows_upserted=len(links) + len(owner))
    return {
        "rule_version": R10_VERSION,
        "mode": "apply" if args.apply else "dry_run",
        "links": dict(Counter(x.method for x in links + owner)),
        "unresolved": dict(Counter(u.reason for u in unresolved)),
        "unresolved_ids": [u.accident_id for u in unresolved],
        "owner_decided": len(decided),
    }


EXPORT_SQL = text(
    "SELECT u.accident_id, u.reason, r.name, m.name, u.candidate_kind, u.best_candidate_id, "
    "COALESCE(cr.name, ca.name, o.name), u.best_score FROM internal.r10_unresolved u "
    "LEFT JOIN routes r ON r.route_id = u.legacy_route_id "
    "LEFT JOIN mountains m ON m.mountain_id = u.legacy_mountain_id "
    "LEFT JOIN canonical_routes cr ON u.candidate_kind = 'route' AND cr.route_id = u.best_candidate_id "
    "LEFT JOIN canonical_areas ca ON u.candidate_kind = 'area' AND ca.area_id = u.best_candidate_id "
    "LEFT JOIN LATERAL (SELECT NULL::text AS name) o ON true "
    "WHERE u.owner_decision IS NULL ORDER BY u.accident_id"
)


async def export_review(conn: AsyncConnection, path: Path) -> int:
    rows = (await conn.execute(EXPORT_SQL)).all()
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(REVIEW_COLUMNS)
        for a, reason, rname, mname, kind, best, bname, score in rows:
            writer.writerow([a, reason, rname or "", mname or "", kind or "", best or "", bname or "",
                             "" if score is None else f"{float(score):.3f}", "", ""])
    return len(rows)


_DECIDED_COLUMN = {"route": "decided_route_id", "area": "decided_area_id", "objective": "decided_objective_id"}
_TARGET_TABLE = {"route": ("canonical_routes", "route_id"), "area": ("canonical_areas", "area_id"),
                 "objective": ("objectives", "objective_id")}


async def import_decisions(conn: AsyncConnection, path: Path) -> dict[str, int]:
    counts: Counter[str] = Counter({"link": 0, "no_link": 0, "rejected": 0})
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            decision = (row.get("owner_decision") or "").strip()
            if not decision:
                continue
            accident_id = int(row["accident_id"])
            if decision == "no_link":
                await conn.execute(text(
                    "UPDATE internal.r10_unresolved SET owner_decision = 'no_link', decided_at = now() "
                    "WHERE accident_id = :a"), {"a": accident_id})
                counts["no_link"] += 1
                continue
            kind = (row.get("candidate_kind") or "").strip()
            raw_id = (row.get("decided_id") or "").strip()
            if decision != "link" or kind not in _DECIDED_COLUMN or not raw_id:
                counts["rejected"] += 1
                continue
            table, key = _TARGET_TABLE[kind]
            target = uuid.UUID(raw_id)
            exists = (await conn.execute(text(f"SELECT 1 FROM {table} WHERE {key} = :t"), {"t": target})).scalar()
            if not exists:
                counts["rejected"] += 1
                continue
            await conn.execute(text(
                f"UPDATE internal.r10_unresolved SET owner_decision = 'link', {_DECIDED_COLUMN[kind]} = :t, "
                "decided_at = now() WHERE accident_id = :a"), {"t": target, "a": accident_id})
            counts["link"] += 1
    return dict(counts)


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            if args.action == "export":
                return {"exported": await export_review(conn, Path(args.path))}
            return await import_decisions(conn, Path(args.path))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="R10 owner review round-trip")
    parser.add_argument("action", choices=["export", "import"])
    parser.add_argument("path")
    print(json.dumps(asyncio.run(_main(parser.parse_args())), sort_keys=True))
```

`_TARGET_TABLE`/`_DECIDED_COLUMN` are fixed dictionaries, so the f-string SQL never carries CSV input; the id is a bound parameter. `export_review` leaves objective names blank until plan 6 replaces the `LATERAL` placeholder join with `LEFT JOIN objectives o ON u.candidate_kind = 'objective' AND o.objective_id = u.best_candidate_id` (it cannot reference a table that does not exist at `0010`). The review CSV names legacy routes and mountains, so it lives only in `data/review/` (gitignored, D12). `LINK_UPSERT`'s `WHERE` lets an owner link replace anything and lets rule links replace only earlier rule links, never a plan 4/6 matcher link with another method. Register `"r10": legacy_links.run_r10` in `app/data/repair/__main__.py`. Grants ("Plan 5 (R10)"): `GRANT SELECT ON public.routes, public.mountains TO ingest;` — no UPDATE on `accidents` (R10 never writes the legacy columns; plan 6 removes this SELECT line with the tables). Append `"app.data.repair.legacy_links"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_legacy_links.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/data/repair/ backend/tests/test_legacy_links.py backend/db/roles/grants_phase2.sql backend/pyproject.toml && git commit -m "feat(repair): R10 relinks legacy accident links, unresolved links go to owner review"`

Docs in the same PR: CHANGELOG "R10 relink (PR 2b-2c)"; DATABASE_STRUCTURE `internal.r10_unresolved`; CLAUDE.md the `r10` step and the export/import commands. Commit them with the task (`git add CHANGELOG.md CLAUDE.md data/DATABASE_STRUCTURE.md`).

---

## Runbook shell setup (Tasks 9–11)

From `backend/`, with `split_pg_url` and `verify_full_url` defined (foundations Task 8 Step 1), load plan 2's committed helpers; this plan defines no helper of its own:

```bash
. scripts/runbook_helpers.sh
```

`TARGET_HOST` set → every helper talks to that Neon branch; unset → prod. `ING <repair step>` runs `app.data.repair`, `INGMOD <module>` runs any pipeline module as `ingest`, `ANALYST_PSQL` and `VERIFY <pytest args>` connect as `analyst`; all use verify-full TLS (review P5, SEC1).

### Task 9: OWNER/AGENT RUNBOOK — PR 2b-2a (MVP-1 features)

- [ ] **Step 1 (owner/agent): Branch `p2b-2a-rehearsal`**: migrate (`0010`) as migrator, `alembic check`, `grants_phase2.sql`, `verify_roles_phase2.sql`. `export TARGET_HOST=<branch host>`.
- [ ] **Step 2 (owner/agent): Points and elevation** — `INGMOD app.pipelines.static_features points` → count ≈ 25–30K (fewer than before: parent-area centroids no longer make points); `quarantined` should list only `no_timezone`/coordinate reasons with small counts. Then `INGMOD app.pipelines.static_features elevation --limit 5000` repeated until it prints `"sampled": 0, "retry": 0`. If `retry` stays above 0 across three runs an hour apart, stop and report the counts (a 3DEP outage, not missing data).
- [ ] **Step 3 (owner/agent): EPQS gate** — `INGMOD app.pipelines.static_features epqs-check` (≈ 240 EPQS calls at ≥ 1 s each plus retries; allow 15 minutes). Expected: `"passed": 1`, `compared ≥ 180`, `gross: 0`, each class `passed: 1`. If it fails, stop: the JSON shows which class and by how much; share counts and `point_key`s (never names) with the agent. A class with `n < 30` means too few points of that slope class were found in `probed`; rerun with a larger sample only after the agent raises `max_probe`.
- [ ] **Step 4 (owner/agent): Normals status** — normals are filled by plan 3's `era5_window` job, not here. Report: `ANALYST_PSQL -c "SELECT status, count(*) FROM cell_normals_status s WHERE s.grid_bucket IN (SELECT grid_bucket FROM feature_points) GROUP BY status"`. Buckets first seen by this step (catalog areas outside `mp_locations`) are `pending`. If the Professional window is still open (plan 3 runbook), run `INGMOD app.pipelines.era5_window run --window-start <window start> --max-units <remaining budget>` now; otherwise they fill in the next January window and read as missing until then (the owner decides whether MVP-1 waits for them).
- [ ] **Step 5 (owner/agent): Route table and acceptance** — `INGMOD app.pipelines.static_features routes`; `VERIFY tests/verify/test_phase2b_features.py -q -s` → all pass (the normals cell prints the pending count). Then prod: `unset TARGET_HOST` and Steps 1–5 on prod. The weekly schedule starts on merge; the workflow needs only `INGEST_DATABASE_URL` and `HEALTHCHECKS_PING_KEY`.

### Task 10: OWNER/AGENT RUNBOOK — PR 2b-2b (terrain, lithology)

- [ ] **Step 1**: on prod (no schema change in this PR), `INGMOD app.pipelines.static_features terrain --limit 5000` repeatedly until `done: 0, retry: 0`; then `INGMOD app.pipelines.static_features lithology --limit 2000` repeatedly (the client paces itself) until `done: 0, retry: 0`; then `INGMOD app.pipelines.static_features routes`.
- [ ] **Step 2**: report with `ANALYST_PSQL`: share of points with `aspect_deg` NULL (expected high at crag-level coordinates), and `lithology_source = 'none'` share — that number decides D15 (SGMC at ~$0.35/GB-month vs the miss rate) before v2.2 trains.

### Task 11: OWNER/AGENT RUNBOOK — PR 2b-2c (R10 first run)

- [ ] **Step 1 (owner/agent): Dump the legacy tables** — like plan 1 Task 8, for `routes` and `mountains` into `~/Developer/safeascent-private/backups/pre-0012/` (plan 6 drops them).
- [ ] **Step 2 (owner/agent): Branch rehearsal** — `grants_phase2.sql` (Plan 5 R10 grants); `ING r10` (dry run: `links` by method, `unresolved` by reason); `ING r10 --apply`; again `--apply` → identical counts (idempotent). `ANALYST_PSQL -XAt -c "SELECT count(*) FROM accidents WHERE route_id IS NOT NULL OR mountain_id IS NOT NULL"` is unchanged from before the run (R10 never nulls). Record method and reason counts in the PR (spec: 421 direct, 762 via matcher).
- [ ] **Step 3 (owner): Review** — `INGMOD app.data.repair.legacy_links export ../data/review/r10_unresolved.csv`; fill `owner_decision` (`link`/`no_link`) and, for `link`, `decided_id` (and `candidate_kind` if linking a different kind than proposed); `INGMOD app.data.repair.legacy_links import ../data/review/r10_unresolved.csv` → `rejected: 0`; `ING r10 --apply`. `awaiting_objectives` rows need no decision yet.
- [ ] **Step 4 (owner): Prod** — `unset TARGET_HOST`; Step 1 dump, then Steps 2–3 on prod. The legacy columns and tables stay; plan 6 re-runs `r10` after loading objectives, repeats the review for the remaining rows, and applies `0012_drop_legacy_routes`, which refuses while any legacy-carrying accident lacks a link row or an owner decision.

---

## Self-review

- Spec coverage: static features (DEM, Horn slope/aspect, crag aspect/confidence, EPQS check, Alaska 2″, lithology with D15, spatial keys H3 r5/r7 + grid bucket, per-point timezone) — Tasks 1–7; amendment §3.1 MVP-1 set (type group via catalog, elevation, monthly normals via plan 3 read through `scoring_unit_features`, latitude) — Tasks 1, 4, 5; 2b-2 acceptance "100% elevation coverage, EPQS passes" — Tasks 5, 9; R10 relink with owner review — Tasks 8, 11; the legacy drop ("no FK points at a legacy table") is plan 6's `0012`. Objectives and hotspot curation (rest of 2b-2) are plan 6.
- Review items: K1/D16 (Task 8: typed rule 4, review band, no nulling, `r10_unresolved`, re-run after plan 6), P2 (R10 tests pinned to `0010_static_features`), S1 (Task 2 masked sampling, transient vs missing, east-of-180 naming), S3 (stored `point_key`), S4 (Task 5 cells), S5 (`scoring_unit_features`), D5 (Task 3 gate), D7 (prerequisites), D15 (Task 7 retries, Neon cost), SEC4 (workflow has no interpolated inputs), minors (slope definition, parent centroids, 0010 downgrade guard, claim 9 wording), placeholders (`features.py`, terrain and lithology steps in full code).
- Placeholders: none beyond runbook hosts and the owner's review decisions.
- Types: `Elevation`, `sample_points -> list[Elevation | None]`, `read_window`, `TransientTileError`, `point_key`, `tz_for_point`, `fill_terrain`, `fill_lithology`, `plan_links -> (list[Link], list[Unresolved])` are used consistently; `route_static_features` column list matches between migration, model and `BUILD_SQL`; `scoring_unit_features` columns match the Interfaces block and the verify cells.
