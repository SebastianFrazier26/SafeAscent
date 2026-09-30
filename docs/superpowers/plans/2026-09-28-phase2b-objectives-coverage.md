# Phase 2b Objectives, Coverage and Legacy Drop (PRs 2b-2d, 2b-2e) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give mountaineering and glaciated terrain a place to attach a score where no route-level data exists: an Objective layer seeded from USGS GNIS summits (cross-linked to Wikidata), OpenBeta ice areas, and hand-curated CC0 standard routes for the top ~30 hotspots; link routes and accidents to objectives; compute versioned coverage badges (route-level, objective-level, thin data) so thin coverage is shown as insufficient, never as a low score. Then, with objectives in place, re-run R10 and drop the legacy `routes`/`mountains` objects behind a guard that refuses while any formerly linked accident has neither a link nor an owner decision.

**Architecture:** Migration `0011_objectives` adds `objectives`, `objective_routes`, `route_objective_links`, `objective_permit_counts`, `coverage_badges`, wires the `objective_id` columns already reserved on `canonical_areas` and `accident_route_links`, and replaces plan 5's routes-only `scoring_unit_features` view with one that also carries objective rows (review S5). Seeding jobs (`objectives.py`, `wikidata.py`) are open-API/public-file loaders; every objective stores its `point_key` (computed in Python by `static_features.point_key`, never re-derived in SQL) and its IANA `tz`, and registers its grid bucket as `pending` for normals (contract §2–3). Curated routes and permit counts are committed CSVs in our own words (CC0) loaded by strict loaders. `coverage.py` holds the pure badge rule and the SQL job. Every objective gets a `canonical_areas` row (source `safeascent_curated`) under a region parent, so plan 5's feature job gives it elevation automatically. Migration `0012_drop_legacy_routes` (moved here from plan 5, review K1/D16) runs only after the R10 re-run and the owner's decisions on `internal.r10_unresolved`.

**Tech Stack:** Python 3.12, httpx, pydantic 2, SQLAlchemy async, PostGIS, Alembic.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` §Coverage strategy and objectives (P2-2), schema bullets, milestone 2b-2 (objectives part and the legacy-drop acceptance); P3 Objective-level scoring (P3:163–169). Phase 3 creates `objective_daily_scores` (P3 MVP-1), not this plan.

**Prerequisites:** Plans 2 (GNIS summits, `accidents_clean`, R4 activity classes), 4 (catalog, `route_types`), 5 (feature points, `route_static_features`, `scoring_unit_features`, R10 job and `internal.r10_unresolved`) merged and applied; plan 3's `grid_bucket_series`, `cell_normals_status` and `app.pipelines.localday` exist. Alembic head is `0010_static_features`.

**Two PRs:**
- `feat/p2b-objectives` (PR 2b-2d) = Tasks 1–8, runbook Task 9 (migration `0011_objectives`).
- `feat/p2b-legacy-drop` (PR 2b-2e) = Task 10, runbook Task 11 (migration `0012_drop_legacy_routes`). Opened only after Task 9 has run on prod.

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- Objectives and curated routes are our own CC0 records in our own words; nothing is copied from MP (never MP text) or pasted from NPS/USFS pages beyond facts (name, grade, season, URL).
- `mp_facts` rows are never pushed to OpenBeta; the optional contribution (2b-6) is out of scope.
- OSM layers are not built (optional in the spec, legal Q5 open).
- Coverage is recomputed on every catalog or objective change; a thin-data badge must never be converted into a numeric score downstream.
- No lightning-coverage flag is stored here. Coverage depends on the day (GOES-West only from `WEST_START`, GLM from 2018-02-13), so a per-badge constant would be wrong; consumers call plan 7's `glm_satellite(lat, lon, day)` (SQL) / `glm.satellite_for` (Python) and read `lightning_coverage_periods`. Plan 7 merges after this plan (`0013` > `0011`), and nothing here needs lightning, so the rule is never re-implemented.
- `trainer` gets **no** grants in this plan (D13: NOLOGIN, no grants until Phase 3).
- A legacy accident link is never nulled or dropped while unresolved: `0012` refuses until every formerly linked accident has an `accident_route_links` row or an owner decision in `internal.r10_unresolved` (contract §6).
- Runbook database access goes only through plan 2's `backend/scripts/runbook_helpers.sh` (`ING`, `INGMOD`, `OWNER_PSQL`, `ANALYST_PSQL`, `VERIFY`; verify-full TLS); `TARGET_HOST` selects a Neon branch and is unset for prod. This plan defines no helpers.
- Missing normals are `pending`/`insufficient` in `cell_normals_status` and read as missing, never 0.

## Decisions this plan makes where the spec is silent (owner may overrule)

1. **GNIS seeding scope:** a summit becomes an objective when it lies within 5 km of at least one clean accident that is objective-eligible (Decision 4's activity rule) or appears in the curated CSV; seeding all ~70K GNIS summits would create objectives no score can use.
2. **OpenBeta ice-area objectives:** OpenBeta areas with ≥3 ice/mixed routes attached directly (OpenBeta or `mp_facts`), or whose name contains "ice" with ≥1.
3. **Badge precedence** `[default pending: DP4]`: route-level (≥5 scorable routes of the type group, reason `route_min`) → thin (≥1 clean accident within 5 km and no curated objective route, reason `accidents_no_curated`) → objective-level (an objective exists, reason `objective`) → **thin for 1–4 routes with no nearby accident and no objective (reason `few_routes`)** → no badge when there are no routes, no objective and no accident. The spec's three badges leave the 1–4-routes case unnamed; the earlier draft called it `route`, which is optimistic against amendment D1 and the spec's ≥5 rule (review O4). Alternative the owner may pick: `route` for 1–4 routes.
4. **Accident → objective links (review O1):** only for clean accidents with no route or area link, within 3 km of the objective point (`method = 'objective_near'`), and only when the accident is objective-eligible: its `activity` matches the alpine lexicon (mountaineer, alpine, ice, snow, glacier, summit) or its `activity_class` is `climbing_approach`; otherwise it must not match the rock lexicon (rock, sport, boulder, trad, crag, gym) **and** no rock crag (an area with scorable sport/trad routes) may lie closer to the accident than the objective. Crag rock accidents therefore stay crag evidence and never become alpine evidence. Skipped rows are counted (`skipped_rock_context`), never silently dropped.
5. **Wikidata:** items matched by GNIS Feature ID (property P590); elevation from P2044 (normalized metres) is a cross-check against the DEM, reported when |Δ| > 50 m, never copied over the DEM value.
6. **Objective `n_routes` (review O2):** counts distinct scorable routes under the objective area's path **plus** routes linked through `route_objective_links` (curated `on` and distance `near` links), so an objective whose routes live in a neighbouring catalog area (Longs Peak) is not read as thin.
7. **Region parent (review O3):** an objective area's parent is the nearest non-objective catalog area within 5 km; otherwise the depth-2 catalog area named for the objective's state (`STATE_NAMES`); only when neither exists is it a root path, and that case is counted (`no_region_parent`) and fails the acceptance cell, so state/region pooling (P3:57) is never lost silently. (The same fix for `mp_facts` areas is plan 4's.)
8. **Legacy drop lives here, not in plan 5:** R10 must re-run after objectives exist (review D16), and Alembic is linear, so the guarded drop is `0012`, after `0011_objectives`.

## Review Focus

1. **An objective with accidents nearby but no curated route** — expect `thin`, never `objective` or a score (Task 6 `test_thin_beats_objective_without_curated_route`).
2. **A crag with 1–4 routes, no objective, no accident** — expect `thin` with reason `few_routes`, never `route` (Task 6 `test_one_to_four_routes_are_thin_few_routes`, and the DB test `test_compute_badges_against_a_database`).
3. **A rock-climbing accident 1 km from a summit and 100 m from a trad crag** — expect no objective link and a `skipped_rock_context` count (Task 5 `test_links_near_routes_and_objective_eligible_accidents_only`).
4. **Longs-Peak shape: an objective with no routes under its own area but 5 `near`-linked routes** — expect `route`, not `thin` (Task 6 `test_compute_badges_against_a_database`).
5. **`0012` with an `internal.r10_unresolved` row the owner has not decided, or with a link that survives only in `internal.accidents_raw`** — expect refusal naming the count (Task 10 `test_0012_refuses_undecided_unresolved_rows`, `test_0012_refuses_when_only_accidents_raw_remembers_the_link`).
6. **Wikidata returning two items for one GNIS id** — expect no QID stored and a quarantine entry, not an arbitrary pick (Task 3 `test_duplicate_items_are_quarantined`).
7. **A summit with no nearby catalog area** — expect a parent under its state area, not a root path (Task 2 `test_objective_area_falls_back_to_state_parent`).
8. **Re-seeding after new accidents** — expect existing objectives kept (stable ids) and only new ones added (Task 2 `test_reseeding_is_stable`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/alembic/versions/0011_objectives.py` | Create | Objective tables, badges, FKs, objective rows in `scoring_unit_features`. |
| `backend/alembic/versions/0012_drop_legacy_routes.py` | Create (PR 2b-2e) | Guarded legacy drop (moved from plan 5). |
| `backend/app/models/objectives.py` | Create | Models. |
| `backend/app/pipelines/objectives.py` | Create | GNIS seeding, ice-area objectives, objective area rows with region parents, bucket registration, links. |
| `backend/app/pipelines/wikidata.py` | Create | SPARQL client (P590 → QID, sitelinks, P2044). |
| `backend/app/pipelines/curated.py` | Create | Curated objective-route and permit-count CSV loaders. |
| `backend/app/pipelines/coverage.py` | Create | Badge rule with reasons (pure) + badge job + hotspot report. |
| `data/curated/README.md`, `data/curated/objective_routes.csv`, `data/curated/objective_permit_counts.csv` | Create | Our CC0 curated records (headers now; rows by the owner). |
| tests: `test_migration_0011.py`, `test_objectives.py`, `test_wikidata.py`, `test_curated.py`, `test_coverage.py`, `test_coverage_db.py`, `test_migration_0012.py` | Create | Tests. |
| `backend/tests/verify/test_phase2b_objectives.py`, `backend/tests/verify/test_phase2b_legacy_drop.py` | Create | `-m db` acceptance. |
| `backend/app/models/accident.py`, `app/models/__init__.py`, `alembic/env.py`, `app/api/v1/accidents.py`, `app/schemas/accident.py`, `tests/test_migrations.py`, `tests/test_ascent_analytics.py` | Modify (PR 2b-2e) | Legacy column removal. |
| `backend/app/models/legacy.py`, `backend/app/data/repair/legacy_links.py`, `backend/tests/test_legacy_links.py` | Delete (PR 2b-2e) | Legacy models and the one-shot R10 job. |
| grants/verify SQL, `backend/pyproject.toml`, docs | Modify | Grants, mypy, docs. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 2–7 | objective tables (`0011_objectives`) | Task 1 first. |
| 1 | plan 5 | `scoring_unit_features` view | Plan 5's `0010` defines the 19-column, 12-rows-per-unit view; `0011` `CREATE OR REPLACE`s it with objective rows (same columns); its downgrade restores plan 5's definition. |
| 2 | 4, 5, 6 | `objectives.objective_area_id(objective_id)`; one `canonical_areas` row per objective | Frozen in Task 2. |
| 9 | plan 2 | R4/R5 re-run with objectives (plan 2 Task 13 Step 9, deferred), `runbook_helpers.sh` | Task 1 grants `ingest` SELECT on `objectives`; Task 9 Step 2 runs `ING r4`, `ING r4 --apply`, `ING r5 --apply`; runbooks use plan 2's helpers verbatim and define none. |
| 2 | plan 3 | `grid_bucket_series`, `cell_normals_status`, `localday.tz_for_point` | Plan 3 owns the tables and grants `ingest` INSERT/UPDATE on them; Task 2 only inserts `ON CONFLICT DO NOTHING`. |
| 2 | plan 5 | `static_features.point_key` | Imported, never re-implemented; objectives store the key. |
| 3 | 6, plan 8 | `objectives.wikidata_qid`, `objectives.sitelinks` | Plan 8 reads `sitelinks` for `objective_popularity`. |
| 4 | 6, plan 8 | `objective_routes`, `objective_permit_counts` | Plan 8 reads permit counts for `permit_attempts`. |
| 6 | Phase 3, plan 8 | `coverage.badge_for`, `coverage_badges` table (with `reason`) | Frozen here; `badge_for` keeps its signature. |
| 5, 10 | plan 5 | R10 job (`python -m app.data.repair r10`, review CLI `python -m app.data.repair.legacy_links export|import`), `legacy_links.EXPORT_SQL`, `internal.r10_unresolved` | Plan 5 creates them (legacy mountains stay `awaiting_objectives` until objectives exist); Task 5 swaps the export's placeholder join for a real `objectives` join; Task 11 re-runs R10 on plan 6 data, which resolves `awaiting_objectives`; Task 10 deletes the job and removes only the `SELECT` grant on `routes`/`mountains`. |
| 10 | plan 7 | `0013_live_feeds.down_revision = "0012_drop_legacy_routes"` | Frozen chain (contract §1). |
| all | each other | grants/verify SQL, `pyproject.toml` | Serial. |

---

# PR 2b-2d — `feat/p2b-objectives`

### Task 1: Migration `0011_objectives` and models

**Files:**
- Create: `backend/alembic/versions/0011_objectives.py`, `backend/app/models/objectives.py`, `backend/tests/test_migration_0011.py`
- Modify: `backend/app/models/__init__.py`, `backend/app/models/catalog.py` (FK on `objective_id`), `backend/pyproject.toml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Produces (DB):
  - `objectives(objective_id uuid PK, kind text CHECK IN ('peak','ice_area','glacier','formation'), name text NOT NULL, state_code text NULL CHECK (state_code ~ '^[A-Z]{2}$'), lat double precision NOT NULL, lon double precision NOT NULL, geom geography(Point,4326) NOT NULL, point_key text NOT NULL, tz text NULL, elevation_m real, gnis_id integer UNIQUE NULL, wikidata_qid text NULL, sitelinks integer NULL, ob_area_uuid uuid UNIQUE NULL, disciplines text[] NOT NULL, coverage_level text CHECK IN ('route','objective','thin'), coverage_rule_version text, source text CHECK IN ('gnis','wikidata','openbeta','curated'), license text CHECK IN ('public_domain','CC0'), curated_by text, updated_at timestamptz NOT NULL DEFAULT now())`, GiST on `geom`, index on `point_key`.
  - `objective_routes(objective_route_id uuid PK, objective_id uuid NOT NULL REFERENCES objectives, route_id uuid UNIQUE REFERENCES canonical_routes, name text NOT NULL, type_group text NOT NULL CHECK IN ('alpine','ice','mixed','trad','sport'), grade_text text, season_months smallint[], summary text CHECK (length(summary) <= 300), source_url text NOT NULL, source_kind text CHECK IN ('nps','usfs','owner'), license text NOT NULL DEFAULT 'CC0', curated_at timestamptz NOT NULL DEFAULT now())`.
  - `route_objective_links(route_id uuid REFERENCES canonical_routes, objective_id uuid REFERENCES objectives, relation text CHECK IN ('on','approach_via','near'), method text NOT NULL, score real, PK (route_id, objective_id))`.
  - `objective_permit_counts(objective_id uuid REFERENCES objectives, year smallint NOT NULL, month smallint NOT NULL CHECK 0..12 /* 0 = whole-year total */, attempts integer CHECK >= 0, summits integer CHECK >= 0, source_url text NOT NULL, PK (objective_id, year, month))`.
  - `coverage_badges(scope_kind text CHECK IN ('area','objective'), scope_id uuid, type_group text CHECK IN ('sport','trad','alpine','ice','mixed'), level text NOT NULL CHECK IN ('route','objective','thin'), reason text NOT NULL CHECK IN ('route_min','accidents_no_curated','objective','few_routes'), n_routes integer NOT NULL, n_clean_accidents_5km integer NOT NULL, has_curated_route boolean NOT NULL, coverage_rule_version text NOT NULL, computed_at timestamptz NOT NULL DEFAULT now(), PK (scope_kind, scope_id, type_group))`.
  - FKs: `canonical_areas.objective_id → objectives`, `accident_route_links.objective_id → objectives`.
  - View `scoring_unit_features` replaced (`CREATE OR REPLACE VIEW`, plan 5's 19 columns unchanged: `unit_kind, unit_id, type_group, area_path, point_key, tz, lat, elevation_m, dem_res_m, grid_bucket, coord_precision, month, tmax_mean, tmin_mean, precip_mm, snowfall_cm, freeze_thaw_days, normals_status, feature_version`): plan 5's route rows plus 12 rows (one per month) per objective × discipline (`unit_kind='objective'`). Normals are NULL unless the bucket's status is `complete` or `insufficient`; `normals_status` is `pending` for a bucket with no status row. An objective whose point has no feature row yet takes its bucket from `grid_bucket_key(o.lat, o.lon)` (plan 1's SQL function), so it reads `pending`, never `no_location` (review S5).

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0011.py`:

```python
import asyncio

import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
OBJ = ("INSERT INTO objectives (objective_id, kind, name, state_code, lat, lon, geom, point_key, tz, disciplines, source, license) "
       "VALUES ('00000000-0000-0000-0000-0000000000f1', 'peak', 'Fixture Peak', 'WA', 46.85, -121.76, "
       "ST_SetSRID(ST_MakePoint(-121.76, 46.85), 4326)::geography, '46.85000:-121.76000', 'America/Los_Angeles', "
       "'{alpine,ice}', 'gnis', 'public_domain');")
OBJ_AREA = ("INSERT INTO canonical_areas (area_id, name, path, lat, lon, geom, objective_id, coord_precision, source, redistributable) "
            "VALUES ('00000000-0000-0000-0000-0000000000f2', 'Fixture Peak', '000000000000000000000000000000f2', 46.85, -121.76, "
            "ST_SetSRID(ST_MakePoint(-121.76, 46.85), 4326)::geography, '00000000-0000-0000-0000-0000000000f1', "
            "'area_centroid', 'safeascent_curated', true);")


def _rows(name: str, sql: str) -> list[tuple[object, ...]]:
    async def go() -> list[tuple[object, ...]]:
        conn = await asyncpg.connect(pg_url(name))
        try:
            return [tuple(r) for r in await conn.fetch(sql)]
        finally:
            await conn.close()

    return asyncio.run(go())


def test_0011_checks_clean_and_constraints():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))
        run_sql(name, OBJ)
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO objective_routes (objective_route_id, objective_id, name, type_group, source_url) "
                          "VALUES (gen_random_uuid(), '00000000-0000-0000-0000-0000000000f1', 'R', 'bouldering', 'https://x')")
        run_sql(name, "INSERT INTO objective_permit_counts (objective_id, year, month, attempts, source_url) VALUES "
                      "('00000000-0000-0000-0000-0000000000f1', 2025, 0, 1000, 'https://x')")
        with pytest.raises(asyncpg.UniqueViolationError):
            run_sql(name, "INSERT INTO objective_permit_counts (objective_id, year, month, attempts, source_url) VALUES "
                          "('00000000-0000-0000-0000-0000000000f1', 2025, 0, 999, 'https://y')")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO coverage_badges (scope_kind, scope_id, type_group, level, reason, n_routes, "
                          "n_clean_accidents_5km, has_curated_route, coverage_rule_version) VALUES "
                          "('area', gen_random_uuid(), 'trad', 'thin', 'guess', 2, 0, false, 'cv-v1')")


def test_scoring_unit_features_carries_objective_rows_with_pending_normals():
    with migrated_db() as name:
        run_sql(name, OBJ + OBJ_AREA)
        rows = _rows(name, "SELECT type_group, count(*), min(month), max(month), min(tz), min(normals_status), "
                           "count(tmax_mean) FROM scoring_unit_features WHERE unit_kind = 'objective' "
                           "AND unit_id = '00000000-0000-0000-0000-0000000000f1' GROUP BY type_group ORDER BY type_group")
    assert rows == [
        ("alpine", 12, 1, 12, "America/Los_Angeles", "pending", 0),
        ("ice", 12, 1, 12, "America/Los_Angeles", "pending", 0),
    ]


def test_0011_downgrade_restores_plan_5s_view():
    with migrated_db("0011_objectives") as name:
        cfg = _alembic_cfg(name)
        command.downgrade(cfg, "0010_static_features")
        rows = _rows(name, "SELECT count(*) FROM information_schema.views WHERE table_name = 'scoring_unit_features'")
        assert rows == [(1,)]
        cols = _rows(name, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'scoring_unit_features'")
        assert cols == [(19,)]
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_migration_0011.py -q` → FAIL (no revision `0011_objectives`).

- [ ] **Step 3: Implement** `backend/alembic/versions/0011_objectives.py`:

```python
"""Objective layer (P2-2), versioned coverage badges, and objective rows in the
scoring-unit features view."""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision = "0011_objectives"
down_revision = "0010_static_features"
branch_labels = None
depends_on = None

# Plan 5's route half, verbatim from 0010_static_features.SCORING_UNIT_FEATURES; the downgrade
# restores it alone.
ROUTE_UNITS = """
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

# Objective rows join on the objective's stored point_key (review S3: no SQL re-derivation); until
# the point has a feature row the bucket comes from the objective's own coordinates, so the row
# reads 'pending' rather than 'no_location'.
OBJECTIVE_UNITS = """
SELECT 'objective'::text, o.objective_id, d.tg, a.path, o.point_key, COALESCE(p.tz, o.tz), o.lat,
       p.elevation_m, p.dem_res_m, u.grid_bucket, a.coord_precision, m.month::smallint,
       n.tmax_mean, n.tmin_mean, n.precip_mm, n.snowfall_cm, n.freeze_thaw_days,
       COALESCE(s.status, 'pending'), p.feature_version
FROM objectives o
JOIN canonical_areas a ON a.objective_id = o.objective_id
CROSS JOIN LATERAL unnest(o.disciplines) AS d(tg)
LEFT JOIN feature_points p ON p.point_key = o.point_key
CROSS JOIN LATERAL (SELECT COALESCE(p.grid_bucket, grid_bucket_key(o.lat, o.lon)) AS grid_bucket) u
CROSS JOIN generate_series(1, 12) AS m(month)
LEFT JOIN cell_normals_status s ON s.grid_bucket = u.grid_bucket
LEFT JOIN cell_climate_normals n
  ON n.grid_bucket = u.grid_bucket AND n.month = m.month AND s.status IN ('complete', 'insufficient')
WHERE d.tg IN ('sport', 'trad', 'alpine', 'ice', 'mixed')
"""


def upgrade() -> None:
    op.create_table(
        "objectives",
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("state_code", sa.Text(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("geom", Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("point_key", sa.Text(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=True),
        sa.Column("elevation_m", sa.REAL(), nullable=True),
        sa.Column("gnis_id", sa.Integer(), nullable=True, unique=True),
        sa.Column("wikidata_qid", sa.Text(), nullable=True),
        sa.Column("sitelinks", sa.Integer(), nullable=True),
        sa.Column("ob_area_uuid", postgresql.UUID(as_uuid=True), nullable=True, unique=True),
        sa.Column("disciplines", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("coverage_level", sa.Text(), nullable=True),
        sa.Column("coverage_rule_version", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("license", sa.Text(), nullable=False),
        sa.Column("curated_by", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('peak', 'ice_area', 'glacier', 'formation')", name="objectives_kind_check"),
        sa.CheckConstraint("state_code IS NULL OR state_code ~ '^[A-Z]{2}$'", name="objectives_state_code_check"),
        sa.CheckConstraint("coverage_level IS NULL OR coverage_level IN ('route', 'objective', 'thin')", name="objectives_coverage_check"),
        sa.CheckConstraint("source IN ('gnis', 'wikidata', 'openbeta', 'curated')", name="objectives_source_check"),
        sa.CheckConstraint("license IN ('public_domain', 'CC0')", name="objectives_license_check"),
    )
    op.create_index("ix_objectives_geom", "objectives", ["geom"], postgresql_using="gist")
    op.create_index("ix_objectives_point_key", "objectives", ["point_key"])
    op.create_table(
        "objective_routes",
        sa.Column("objective_route_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("objectives.objective_id"), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("canonical_routes.route_id"), nullable=True, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type_group", sa.Text(), nullable=False),
        sa.Column("grade_text", sa.Text(), nullable=True),
        sa.Column("season_months", postgresql.ARRAY(sa.SmallInteger()), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=True),
        sa.Column("license", sa.Text(), server_default="CC0", nullable=False),
        sa.Column("curated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("type_group IN ('alpine', 'ice', 'mixed', 'trad', 'sport')", name="objective_routes_type_group_check"),
        sa.CheckConstraint("summary IS NULL OR length(summary) <= 300", name="objective_routes_summary_check"),
        sa.CheckConstraint("source_kind IS NULL OR source_kind IN ('nps', 'usfs', 'owner')", name="objective_routes_source_kind_check"),
    )
    op.create_table(
        "route_objective_links",
        sa.Column("route_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("canonical_routes.route_id"), nullable=False),
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("objectives.objective_id"), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("score", sa.REAL(), nullable=True),
        sa.PrimaryKeyConstraint("route_id", "objective_id"),
        sa.CheckConstraint("relation IN ('on', 'approach_via', 'near')", name="route_objective_links_relation_check"),
    )
    op.create_table(
        "objective_permit_counts",
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("objectives.objective_id"), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column("summits", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("objective_id", "year", "month", name="objective_permit_counts_pkey"),
        sa.CheckConstraint("month BETWEEN 0 AND 12", name="objective_permit_counts_month_check"),
        sa.CheckConstraint("(attempts IS NULL OR attempts >= 0) AND (summits IS NULL OR summits >= 0)", name="objective_permit_counts_nonneg_check"),
    )
    op.create_table(
        "coverage_badges",
        sa.Column("scope_kind", sa.Text(), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type_group", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("n_routes", sa.Integer(), nullable=False),
        sa.Column("n_clean_accidents_5km", sa.Integer(), nullable=False),
        sa.Column("has_curated_route", sa.Boolean(), nullable=False),
        sa.Column("coverage_rule_version", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("scope_kind", "scope_id", "type_group"),
        sa.CheckConstraint("scope_kind IN ('area', 'objective')", name="coverage_badges_scope_check"),
        sa.CheckConstraint("type_group IN ('sport', 'trad', 'alpine', 'ice', 'mixed')", name="coverage_badges_type_group_check"),
        sa.CheckConstraint("level IN ('route', 'objective', 'thin')", name="coverage_badges_level_check"),
        sa.CheckConstraint("reason IN ('route_min', 'accidents_no_curated', 'objective', 'few_routes')", name="coverage_badges_reason_check"),
    )
    op.create_foreign_key("canonical_areas_objective_fkey", "canonical_areas", "objectives", ["objective_id"], ["objective_id"])
    op.create_foreign_key("accident_route_links_objective_fkey", "accident_route_links", "objectives", ["objective_id"], ["objective_id"])
    op.execute(f"CREATE OR REPLACE VIEW scoring_unit_features AS {ROUTE_UNITS} UNION ALL {OBJECTIVE_UNITS}")


def downgrade() -> None:
    n = op.get_bind().exec_driver_sql("SELECT count(*) FROM objective_routes").scalar_one()
    if n:
        raise RuntimeError(f"refusing to downgrade 0011: {n} curated objective routes")
    op.execute(f"CREATE OR REPLACE VIEW scoring_unit_features AS {ROUTE_UNITS}")
    op.drop_constraint("accident_route_links_objective_fkey", "accident_route_links", type_="foreignkey")
    op.drop_constraint("canonical_areas_objective_fkey", "canonical_areas", type_="foreignkey")
    for table in ("coverage_badges", "objective_permit_counts", "route_objective_links", "objective_routes"):
        op.drop_table(table)
    op.drop_index("ix_objectives_point_key", "objectives")
    op.drop_index("ix_objectives_geom", "objectives")
    op.drop_table("objectives")
```

`ROUTE_UNITS` must stay textually identical to plan 5's `SCORING_UNIT_FEATURES` body (same 19 columns, same order), which `CREATE OR REPLACE VIEW` requires; `test_0011_downgrade_restores_plan_5s_view` pins the column count. The constraint tests run at head (not pinned to `0011`) because `alembic check` compares against the current models, which lose the legacy columns in PR 2b-2e.

`backend/app/models/objectives.py`:

```python
"""Objective layer (P2-2) and coverage badges."""

from __future__ import annotations

import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, PrimaryKeyConstraint,
                        REAL, SmallInteger, Text, func)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Objective(Base):
    __tablename__ = "objectives"
    __table_args__ = (
        CheckConstraint("kind IN ('peak', 'ice_area', 'glacier', 'formation')", name="objectives_kind_check"),
        CheckConstraint("state_code IS NULL OR state_code ~ '^[A-Z]{2}$'", name="objectives_state_code_check"),
        CheckConstraint("coverage_level IS NULL OR coverage_level IN ('route', 'objective', 'thin')", name="objectives_coverage_check"),
        CheckConstraint("source IN ('gnis', 'wikidata', 'openbeta', 'curated')", name="objectives_source_check"),
        CheckConstraint("license IN ('public_domain', 'CC0')", name="objectives_license_check"),
        Index("ix_objectives_geom", "geom", postgresql_using="gist"),
        Index("ix_objectives_point_key", "point_key"),
    )

    objective_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    state_code: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    geom: Mapped[object] = mapped_column(Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False)
    point_key: Mapped[str] = mapped_column(Text, nullable=False)
    tz: Mapped[str | None] = mapped_column(Text)
    elevation_m: Mapped[float | None] = mapped_column(REAL)
    gnis_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    wikidata_qid: Mapped[str | None] = mapped_column(Text)
    sitelinks: Mapped[int | None] = mapped_column(Integer)
    ob_area_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    disciplines: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    coverage_level: Mapped[str | None] = mapped_column(Text)
    coverage_rule_version: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    license: Mapped[str] = mapped_column(Text, nullable=False)
    curated_by: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ObjectiveRoute(Base):
    __tablename__ = "objective_routes"
    __table_args__ = (
        CheckConstraint("type_group IN ('alpine', 'ice', 'mixed', 'trad', 'sport')", name="objective_routes_type_group_check"),
        CheckConstraint("summary IS NULL OR length(summary) <= 300", name="objective_routes_summary_check"),
        CheckConstraint("source_kind IS NULL OR source_kind IN ('nps', 'usfs', 'owner')", name="objective_routes_source_kind_check"),
    )

    objective_route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    objective_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("objectives.objective_id"), nullable=False)
    route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_routes.route_id"), unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type_group: Mapped[str] = mapped_column(Text, nullable=False)
    grade_text: Mapped[str | None] = mapped_column(Text)
    season_months: Mapped[list[int] | None] = mapped_column(ARRAY(SmallInteger))
    summary: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str] = mapped_column(Text, server_default="CC0", nullable=False)
    curated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RouteObjectiveLink(Base):
    __tablename__ = "route_objective_links"
    __table_args__ = (
        PrimaryKeyConstraint("route_id", "objective_id"),
        CheckConstraint("relation IN ('on', 'approach_via', 'near')", name="route_objective_links_relation_check"),
    )

    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_routes.route_id"), nullable=False)
    objective_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("objectives.objective_id"), nullable=False)
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(REAL)


class ObjectivePermitCount(Base):
    """Month 0 is a whole-year total."""

    __tablename__ = "objective_permit_counts"
    __table_args__ = (
        PrimaryKeyConstraint("objective_id", "year", "month", name="objective_permit_counts_pkey"),
        CheckConstraint("month BETWEEN 0 AND 12", name="objective_permit_counts_month_check"),
        CheckConstraint("(attempts IS NULL OR attempts >= 0) AND (summits IS NULL OR summits >= 0)",
                        name="objective_permit_counts_nonneg_check"),
    )

    objective_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("objectives.objective_id"), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    attempts: Mapped[int | None] = mapped_column(Integer)
    summits: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)


class CoverageBadge(Base):
    __tablename__ = "coverage_badges"
    __table_args__ = (
        PrimaryKeyConstraint("scope_kind", "scope_id", "type_group"),
        CheckConstraint("scope_kind IN ('area', 'objective')", name="coverage_badges_scope_check"),
        CheckConstraint("type_group IN ('sport', 'trad', 'alpine', 'ice', 'mixed')", name="coverage_badges_type_group_check"),
        CheckConstraint("level IN ('route', 'objective', 'thin')", name="coverage_badges_level_check"),
        CheckConstraint("reason IN ('route_min', 'accidents_no_curated', 'objective', 'few_routes')", name="coverage_badges_reason_check"),
    )

    scope_kind: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    type_group: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    n_routes: Mapped[int] = mapped_column(Integer, nullable=False)
    n_clean_accidents_5km: Mapped[int] = mapped_column(Integer, nullable=False)
    has_curated_route: Mapped[bool] = mapped_column(Boolean, nullable=False)
    coverage_rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

`Base` is the declarative base the other models import (`from app.db.base import Base`, the same import `app/models/catalog.py` uses — match that file if its import differs). The view is not modelled (Alembic's `include_object` already skips views). In `app/models/catalog.py` change `CanonicalArea.objective_id` and `AccidentRouteLink.objective_id` to `ForeignKey("objectives.objective_id")`. Register `objectives` in `app/models/__init__.py` and append `"app.models.objectives"` to the models mypy block.

Grants — append to `grants_phase2.sql` under `-- Plan 6 (0011)`:

```sql
GRANT SELECT, INSERT, UPDATE ON public.objectives, public.objective_routes, public.objective_permit_counts TO ingest;
-- SELECT on objectives is also what plan 2's R4 (r4-v2) and plan 5's R10 read.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.route_objective_links, public.coverage_badges TO ingest;
GRANT SELECT ON public.scoring_unit_features TO ingest;
```

No `trainer` grant (D13). `grid_bucket_series` and `cell_normals_status` INSERT/UPDATE for `ingest` come from plan 3 (contract §2); nothing is added for them here.

Append to `verify_roles_phase2.sql`, directly after the existing `INSERT INTO ingest_writes VALUES …` statements:

```sql
-- Plan 6 (0011)
INSERT INTO ingest_writes VALUES
  ('public.objectives', 'INSERT'), ('public.objectives', 'UPDATE'),
  ('public.objective_routes', 'INSERT'), ('public.objective_routes', 'UPDATE'),
  ('public.objective_permit_counts', 'INSERT'), ('public.objective_permit_counts', 'UPDATE'),
  ('public.route_objective_links', 'INSERT'), ('public.route_objective_links', 'UPDATE'), ('public.route_objective_links', 'DELETE'),
  ('public.coverage_badges', 'INSERT'), ('public.coverage_badges', 'UPDATE'), ('public.coverage_badges', 'DELETE');
INSERT INTO role_checks VALUES
  ('trainer holds nothing on objectives', NOT has_table_privilege('trainer', 'public.objectives', 'SELECT')),
  ('trainer holds nothing on coverage_badges', NOT has_table_privilege('trainer', 'public.coverage_badges', 'SELECT')),
  ('ingest can read objectives (R4 r4-v2, R10)', has_table_privilege('ingest', 'public.objectives', 'SELECT')),
  ('app cannot write objectives', NOT has_table_privilege('app', 'public.objectives', 'INSERT'));
```

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_migration_0011.py tests/test_migrations.py tests/test_roles_phase2.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0011_objectives.py backend/app/models/ backend/tests/test_migration_0011.py backend/pyproject.toml backend/db/roles/ && git commit -m "feat(db): 0011 objectives, curated routes, permit counts, coverage badges, objective feature rows"`

---

### Task 2: GNIS seeding, OpenBeta ice-area objectives, objective area rows with region parents, bucket registration

**Files:**
- Create: `backend/app/pipelines/objectives.py`, `backend/tests/test_objectives.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: `static_features.point_key` (plan 5), `localday.tz_for_point` (plan 3), `grid.grid_bucket` (plan 1), `catalog.ltree_label` (plan 4).
- Produces: `SEED_RADIUS_M = 5000`, `ALPINE_TERMS`, `ROCK_TERMS`, `STATE_NAMES: dict[str, str]`, `objective_eligible_sql(acc: str, point: str) -> str`, `objective_id_for(kind: str, key: str) -> uuid.UUID` (uuid5; stable across reseeds), `objective_area_id(objective_id: uuid.UUID) -> uuid.UUID`, `point_fields(lat: float, lon: float) -> dict[str, str | None]` (`point_key`, `tz`), `async seed_gnis(conn) -> int`, `async seed_ice_areas(conn) -> int`, `async ensure_objective_areas(conn) -> dict[str, int]` (`areas`, `no_region_parent`), `async register_objective_buckets(conn) -> int`, CLI `python -m app.pipelines.objectives seed|wikidata|links`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_objectives.py`:

```python
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.grid import grid_bucket
from app.pipelines.objectives import (ensure_objective_areas, objective_id_for, register_objective_buckets, seed_gnis,
                                      seed_ice_areas)
from tests.pgtest import migrated_db, requires_pg, sa_url

SEED = """
INSERT INTO gnis_summits (gnis_id, name, name_key, state_code, lat, lon) VALUES
  (1, 'Fixture Peak', 'fixture', 'WA', 46.853, -121.760), (2, 'Far Peak', 'far', 'WA', 48.0, -120.0);
INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity, activity_class, country, date_precision)
  VALUES (1, 'AAC', '2010-07-02', 46.86, -121.75, true, 'Mountaineering', 'climbing', 'US', 'day');
INSERT INTO canonical_areas (area_id, name, path, coord_precision, source, redistributable) VALUES
  ('00000000-0000-0000-0000-0000000000a1', 'USA', '000000000000000000000000000000a1', 'none', 'openbeta', true);
INSERT INTO canonical_areas (area_id, name, parent_id, path, coord_precision, source, redistributable) VALUES
  ('00000000-0000-0000-0000-0000000000a2', 'Washington', '00000000-0000-0000-0000-0000000000a1',
   '000000000000000000000000000000a1.000000000000000000000000000000a2', 'none', 'openbeta', true);
INSERT INTO canonical_areas (area_id, name, path, lat, lon, geom, ob_area_uuid, coord_precision, source, redistributable) VALUES
  ('00000000-0000-0000-0000-00000000000a', 'Fixture Ice Park', '0000000000000000000000000000000a', 44.0, -71.3,
   ST_SetSRID(ST_MakePoint(-71.3, 44.0), 4326)::geography, '00000000-0000-0000-0000-00000000000a', 'area_centroid', 'openbeta', true);
INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable)
  SELECT gen_random_uuid(), '00000000-0000-0000-0000-00000000000a', 'I' || g, '{ice}', 'ice', 'rt-v1', false, true, 'openbeta', true
  FROM generate_series(1, 3) g;
"""


def _run(name, fn):
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
def test_seeds_only_summits_near_eligible_accidents_and_ice_areas():
    with migrated_db(seed_sql=SEED) as name:
        assert _run(name, seed_gnis) == 1
        assert _run(name, seed_ice_areas) == 1
        assert _run(name, ensure_objective_areas) == {"areas": 2, "no_region_parent": 0}
        rows = _run(name, lambda c: c.execute(text(
            "SELECT kind, name, source, state_code, point_key, tz FROM objectives ORDER BY kind")))
        assert [tuple(r) for r in rows] == [
            ("ice_area", "Fixture Ice Park", "openbeta", None, "44.00000:-71.30000", "America/New_York"),
            ("peak", "Fixture Peak", "gnis", "WA", "46.85300:-121.76000", "America/Los_Angeles"),
        ]


@requires_pg
def test_objective_area_falls_back_to_state_parent():
    with migrated_db(seed_sql=SEED) as name:
        _run(name, seed_gnis)
        _run(name, ensure_objective_areas)
        rows = _run(name, lambda c: c.execute(text(
            "SELECT a.parent_id::text, a.path::text FROM canonical_areas a JOIN objectives o USING (objective_id) "
            "WHERE o.gnis_id = 1")))
        [(parent, path)] = [tuple(r) for r in rows]
        assert parent == "00000000-0000-0000-0000-0000000000a2"
        assert path.startswith("000000000000000000000000000000a1.000000000000000000000000000000a2.")


@requires_pg
def test_new_objective_buckets_are_registered_pending():
    with migrated_db(seed_sql=SEED) as name:
        _run(name, seed_gnis)
        assert _run(name, register_objective_buckets) == 1
        series = _run(name, lambda c: c.execute(text("SELECT grid_bucket, tz FROM grid_bucket_series")))
        status = _run(name, lambda c: c.execute(text("SELECT grid_bucket, status FROM cell_normals_status")))
        b = grid_bucket(46.853, -121.760)
        assert [tuple(r) for r in series] == [(b, "America/Los_Angeles")]
        assert [tuple(r) for r in status] == [(b, "pending")]
        assert _run(name, register_objective_buckets) == 1
        again = _run(name, lambda c: c.execute(text("SELECT count(*) FROM cell_normals_status")))
        assert [tuple(r) for r in again] == [(1,)]


@requires_pg
def test_reseeding_is_stable():
    with migrated_db(seed_sql=SEED) as name:
        _run(name, seed_gnis)
        _run(name, seed_gnis)
        ids = _run(name, lambda c: c.execute(text("SELECT objective_id FROM objectives WHERE gnis_id = 1")))
        assert [r[0] for r in ids] == [objective_id_for("peak", "gnis:1")]
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_objectives.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** `backend/app/pipelines/objectives.py`:

```python
"""Objective seeding (P2-2): GNIS summits near objective-eligible clean accidents, OpenBeta ice
areas, and a canonical_areas row per objective (under a region parent) so feature and scoring
jobs treat it like any area. Objectives store point_key and tz computed here in Python."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.catalog import ltree_label
from app.pipelines.grid import grid_bucket
from app.pipelines.localday import tz_for_point
from app.pipelines.static_features import point_key

SEED_RADIUS_M = 5000
_NS = uuid.UUID("7c1e9a52-2f0b-4b5e-9c9e-2d8f0f6a1b33")
ALPINE_TERMS = ["%mountaineer%", "%alpine%", "%ice%", "%snow%", "%glacier%", "%summit%"]
ROCK_TERMS = ["%rock%", "%sport%", "%boulder%", "%trad%", "%crag%", "%gym%"]
STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
TERMS = {"alpine_terms": ALPINE_TERMS, "rock_terms": ROCK_TERMS}


def objective_eligible_sql(acc: str, point: str) -> str:
    """SQL predicate: accident `acc` may count as evidence for the objective at geography `point`.
    Binds :alpine_terms and :rock_terms (pass TERMS)."""
    return (
        f"({acc}.activity_class = 'climbing_approach' "
        f" OR coalesce({acc}.activity ILIKE ANY (CAST(:alpine_terms AS text[])), false) "
        f" OR (NOT coalesce({acc}.activity ILIKE ANY (CAST(:rock_terms AS text[])), false) "
        f"     AND NOT EXISTS (SELECT 1 FROM canonical_areas rc WHERE rc.objective_id IS NULL AND rc.retired_at IS NULL "
        f"       AND rc.geom IS NOT NULL AND ST_DWithin(rc.geom, {acc}.coordinates, ST_Distance({acc}.coordinates, {point})) "
        f"       AND EXISTS (SELECT 1 FROM scorable_routes rs WHERE rs.area_id = rc.area_id AND rs.type_group IN ('sport', 'trad')))))"
    )


def objective_id_for(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"objective:{kind}:{key}")


def objective_area_id(objective_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(_NS, f"objective-area:{objective_id}")


def point_fields(lat: float, lon: float) -> dict[str, str | None]:
    return {"pk": point_key(lat, lon), "tz": tz_for_point(lat, lon)}


async def seed_gnis(conn: AsyncConnection) -> int:
    summit = "ST_SetSRID(ST_MakePoint(g.lon, g.lat), 4326)::geography"
    rows = (await conn.execute(text(
        "SELECT g.gnis_id, g.name, g.state_code, g.lat, g.lon FROM gnis_summits g WHERE EXISTS ("
        " SELECT 1 FROM accidents_clean a WHERE a.coordinates IS NOT NULL AND "
        f" ST_DWithin(a.coordinates, {summit}, :r) AND {objective_eligible_sql('a', summit)})"),
        {"r": SEED_RADIUS_M, **TERMS})).all()
    params = [{"id": objective_id_for("peak", f"gnis:{g}"), "g": int(g), "n": str(n), "s": st,
               "lat": float(la), "lon": float(lo), **point_fields(float(la), float(lo))}
              for g, n, st, la, lo in rows]
    if params:
        await conn.execute(text(
            "INSERT INTO objectives (objective_id, kind, name, state_code, lat, lon, geom, point_key, tz, gnis_id, "
            "disciplines, source, license) VALUES (:id, 'peak', :n, :s, :lat, :lon, "
            "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :pk, :tz, :g, ARRAY['alpine'], 'gnis', 'public_domain') "
            "ON CONFLICT (objective_id) DO NOTHING"), params)
    return len(params)


async def seed_ice_areas(conn: AsyncConnection) -> int:
    rows = (await conn.execute(text(
        "SELECT a.area_id, a.name, a.lat, a.lon, "
        " array_agg(DISTINCT r.type_group) FILTER (WHERE r.type_group IN ('ice','mixed')) AS groups "
        "FROM canonical_areas a JOIN canonical_routes r ON r.area_id = a.area_id "
        "WHERE a.source = 'openbeta' AND a.lat IS NOT NULL AND a.retired_at IS NULL AND r.retired_at IS NULL "
        "GROUP BY a.area_id, a.name, a.lat, a.lon "
        "HAVING count(*) FILTER (WHERE r.type_group IN ('ice','mixed')) >= 3 "
        "   OR (a.name ILIKE '%ice%' AND count(*) FILTER (WHERE r.type_group IN ('ice','mixed')) >= 1)"))).all()
    params = [{"id": objective_id_for("ice_area", str(a)), "ob": a, "n": str(n), "lat": float(la), "lon": float(lo),
               "d": sorted(g), **point_fields(float(la), float(lo))} for a, n, la, lo, g in rows]
    if params:
        await conn.execute(text(
            "INSERT INTO objectives (objective_id, kind, name, lat, lon, geom, point_key, tz, ob_area_uuid, disciplines, "
            "source, license) VALUES (:id, 'ice_area', :n, :lat, :lon, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, "
            ":pk, :tz, :ob, :d, 'openbeta', 'CC0') "
            "ON CONFLICT (objective_id) DO UPDATE SET disciplines = EXCLUDED.disciplines, updated_at = now()"),
            params)
    return len(params)


async def ensure_objective_areas(conn: AsyncConnection) -> dict[str, int]:
    nearest = ("FROM canonical_areas a WHERE a.objective_id IS NULL AND a.source <> 'safeascent_curated' "
               "AND a.geom IS NOT NULL AND a.retired_at IS NULL AND ST_DWithin(a.geom, o.geom, 5000) "
               "ORDER BY ST_Distance(a.geom, o.geom) LIMIT 1")
    objectives = (await conn.execute(text(
        f"SELECT o.objective_id, o.name, o.state_code, o.lat, o.lon, (SELECT a.area_id {nearest}) AS parent, "
        f"(SELECT a.path::text {nearest}) AS parent_path FROM objectives o"))).all()
    states = {str(n): (s, str(p)) for n, s, p in (await conn.execute(text(
        "SELECT name, area_id, path::text FROM canonical_areas WHERE nlevel(path) = 2 AND retired_at IS NULL "
        "AND objective_id IS NULL"))).all()}
    params = []
    no_parent = 0
    for oid, name, state_code, lat, lon, parent, parent_path in objectives:
        if parent is None and state_code is not None and STATE_NAMES.get(str(state_code)) in states:
            parent, parent_path = states[STATE_NAMES[str(state_code)]]
        area_id = objective_area_id(uuid.UUID(str(oid)))
        if parent_path:
            path = f"{parent_path}.{ltree_label(area_id)}"
        else:
            no_parent += 1
            path = ltree_label(area_id)
        params.append({"a": area_id, "n": str(name), "p": parent, "path": path, "lat": float(lat), "lon": float(lon), "o": oid})
    if params:
        await conn.execute(text(
            "INSERT INTO canonical_areas (area_id, name, parent_id, path, lat, lon, geom, objective_id, coord_precision, "
            "source, redistributable) VALUES (:a, :n, :p, CAST(:path AS ltree), :lat, :lon, "
            "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :o, 'area_centroid', 'safeascent_curated', true) "
            "ON CONFLICT (area_id) DO UPDATE SET name = EXCLUDED.name, parent_id = EXCLUDED.parent_id, path = EXCLUDED.path, "
            "updated_at = now()"), params)
    return {"areas": len(params), "no_region_parent": no_parent}


async def register_objective_buckets(conn: AsyncConnection) -> int:
    rows = (await conn.execute(text("SELECT lat, lon, tz FROM objectives WHERE tz IS NOT NULL"))).all()
    series = sorted({(grid_bucket(float(la), float(lo)), str(tz)) for la, lo, tz in rows})
    if series:
        await conn.execute(text(
            "INSERT INTO grid_bucket_series (grid_bucket, tz) VALUES (:b, :tz) ON CONFLICT (grid_bucket, tz) DO NOTHING"),
            [{"b": b, "tz": tz} for b, tz in series])
        await conn.execute(text(
            "INSERT INTO cell_normals_status (grid_bucket, status) VALUES (:b, 'pending') ON CONFLICT (grid_bucket) DO NOTHING"),
            [{"b": b} for b in sorted({b for b, _ in series})])
    return len(series)


async def seed_all(conn: AsyncConnection) -> dict[str, int]:
    seeded = {"gnis": await seed_gnis(conn), "ice_areas": await seed_ice_areas(conn)}
    areas = await ensure_objective_areas(conn)
    registered = await register_objective_buckets(conn)
    no_tz = int((await conn.execute(text("SELECT count(*) FROM objectives WHERE tz IS NULL"))).scalar_one())
    return seeded | areas | {"series_registered": registered, "no_timezone": no_tz}


async def _main(args: argparse.Namespace) -> dict[str, int]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            return await seed_all(conn)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["seed"])
    print(json.dumps(asyncio.run(_main(parser.parse_args())), sort_keys=True))
```

Task 5 extends the CLI with `wikidata` and `links`. A `no_timezone` count above 0 is investigated in the runbook, never defaulted.

Grants: `ingest` already has SELECT on `accidents_clean` (plan 3), INSERT/UPDATE on `canonical_areas` (plan 4) and INSERT on `grid_bucket_series`/`cell_normals_status` (plan 3). Append `"app.pipelines.objectives"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_objectives.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS. **Step 5: Commit** — `git add backend/app/pipelines/objectives.py backend/tests/test_objectives.py backend/pyproject.toml && git commit -m "feat(pipelines): seed objectives with point keys, time zones, region parents and pending normals buckets"`

---

### Task 3: Wikidata cross-links

**Files:**
- Create: `backend/app/pipelines/wikidata.py`, `backend/tests/test_wikidata.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `WDQS_URL = "https://query.wikidata.org/sparql"`, `USER_AGENT = "SafeAscent-data/1.0 (https://github.com/SebastianFrazier26/SafeAscent)"`, `@dataclass(frozen=True) WikidataHit(gnis_id: int, qid: str, sitelinks: int | None, elevation_m: float | None)`, `class WikidataClient(transport=None, batch=200)` with `lookup(gnis_ids: Sequence[int], report: ValidationReport) -> list[WikidataHit]`, `async link_objectives(conn, client) -> dict[str, int]` (sets `wikidata_qid`, `sitelinks`; reports `elevation_mismatch_50m` against `feature_points` elevation joined on the objective's **stored** `point_key`). The CLI step `wikidata` is wired in Task 5.

- [ ] **Step 1: Verify the property (agent)** — `curl -s -G https://query.wikidata.org/sparql -H 'Accept: application/sparql-results+json' -H "User-Agent: SafeAscent-data/1.0 (https://github.com/SebastianFrazier26/SafeAscent)" --data-urlencode 'query=SELECT ?item WHERE { ?item wdt:P31 wd:Q8502 ; wdt:P590 ?g } LIMIT 1'` returns one binding (P590 = GNIS Feature ID). If not, find the property via `https://www.wikidata.org/wiki/Special:Search?search=haswbstatement:P590` and update `QUERY`.

- [ ] **Step 2: Failing tests** — `backend/tests/test_wikidata.py`:

```python
import asyncio

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.validate import ValidationReport
from app.pipelines.wikidata import WikidataClient, link_objectives
from tests.pgtest import migrated_db, requires_pg, sa_url

BODY = {"results": {"bindings": [
    {"gnis": {"value": "1"}, "item": {"value": "http://www.wikidata.org/entity/Q100"}, "sitelinks": {"value": "42"}, "elev": {"value": "4392"}},
    {"gnis": {"value": "2"}, "item": {"value": "http://www.wikidata.org/entity/Q200"}},
    {"gnis": {"value": "3"}, "item": {"value": "http://www.wikidata.org/entity/Q300"}},
    {"gnis": {"value": "3"}, "item": {"value": "http://www.wikidata.org/entity/Q301"}},
]}}


def _client(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=BODY)

    return WikidataClient(transport=httpx.MockTransport(handler))


def test_parses_hits_and_sends_a_user_agent():
    seen = []
    hits = _client(seen).lookup([1, 2, 3], ValidationReport("wd"))
    assert [(h.gnis_id, h.qid, h.sitelinks, h.elevation_m) for h in hits] == [(1, "Q100", 42, 4392.0), (2, "Q200", None, None)]
    assert "SafeAscent" in seen[0].headers["user-agent"]


def test_duplicate_items_are_quarantined():
    report = ValidationReport("wd")
    _client([]).lookup([3], report)
    assert report.quarantined == {"duplicate_items": 1}


@requires_pg
def test_elevation_check_joins_on_the_stored_point_key():
    seed = (
        "INSERT INTO objectives (objective_id, kind, name, lat, lon, geom, point_key, gnis_id, disciplines, source, license) "
        "VALUES ('00000000-0000-0000-0000-0000000000f1', 'peak', 'Fixture Peak', 46.853, -121.76, "
        "ST_SetSRID(ST_MakePoint(-121.76, 46.853), 4326)::geography, '46.85300:-121.76000', 1, '{alpine}', 'gnis', 'public_domain');"
        "INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, elevation_m, dem_source, feature_version) "
        "VALUES ('46.85300:-121.76000', 46.853, -121.76, 1, 'x', 'y', 'America/Los_Angeles', 4300, '3dep_13', 'f-v1');"
    )

    async def go(url: str) -> dict[str, int]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                out = await link_objectives(conn, _client([]))
                qid = (await conn.execute(text("SELECT wikidata_qid FROM objectives"))).scalar_one()
                assert qid == "Q100"
                return out
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        assert asyncio.run(go(sa_url(name))) == {"linked": 1, "duplicates": 0, "elevation_mismatch_50m": 1}
```

(The `feature_points` insert uses plan 5's column set plus `tz` from contract §3; if plan 5 made further columns NOT NULL, add them to this insert with fixture values.)

- [ ] **Step 3: Implement** `backend/app/pipelines/wikidata.py`:

```python
"""Wikidata (CC0) cross-links for GNIS-seeded objectives via the Wikidata Query Service.
Ids are integers validated before they reach the query text."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.validate import ValidationReport

WDQS_URL = "https://query.wikidata.org/sparql"
USER_AGENT = "SafeAscent-data/1.0 (https://github.com/SebastianFrazier26/SafeAscent)"
QUERY = (
    "SELECT ?gnis ?item ?sitelinks ?elev WHERE {{ VALUES ?gnis {{ {values} }} ?item wdt:P590 ?gnis . "
    "OPTIONAL {{ ?item wikibase:sitelinks ?sitelinks }} "
    "OPTIONAL {{ ?item p:P2044/psn:P2044/wikibase:quantityAmount ?elev }} }}"
)


@dataclass(frozen=True)
class WikidataHit:
    gnis_id: int
    qid: str
    sitelinks: int | None
    elevation_m: float | None


class WikidataClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, batch: int = 200) -> None:
        self._client = httpx.Client(transport=transport, timeout=60.0,
                                    headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
        self._batch = batch
        self._pause = 1.0 if transport is None else 0.0

    def lookup(self, gnis_ids: Sequence[int], report: ValidationReport) -> list[WikidataHit]:
        found: dict[int, list[WikidataHit]] = defaultdict(list)
        ids = [int(i) for i in gnis_ids]
        for i in range(0, len(ids), self._batch):
            values = " ".join(f'"{g}"' for g in ids[i : i + self._batch])
            response = self._client.get(WDQS_URL, params={"query": QUERY.format(values=values)})
            response.raise_for_status()
            for b in response.json()["results"]["bindings"]:
                g = int(b["gnis"]["value"])
                found[g].append(WikidataHit(
                    g, b["item"]["value"].rsplit("/", 1)[-1],
                    int(b["sitelinks"]["value"]) if "sitelinks" in b else None,
                    float(b["elev"]["value"]) if "elev" in b else None,
                ))
            time.sleep(self._pause)
        hits: list[WikidataHit] = []
        for g, items in sorted(found.items()):
            if len({h.qid for h in items}) > 1:
                report.quarantine(str(g), "duplicate_items", qids=sorted({h.qid for h in items}))
            else:
                report.accept()
                hits.append(items[0])
        return hits


async def link_objectives(conn: AsyncConnection, client: WikidataClient) -> dict[str, int]:
    rows = (await conn.execute(text(
        "SELECT o.gnis_id, p.elevation_m FROM objectives o LEFT JOIN feature_points p ON p.point_key = o.point_key "
        "WHERE o.gnis_id IS NOT NULL"))).all()
    dem = {int(g): (float(e) if e is not None else None) for g, e in rows}
    report = ValidationReport("wikidata")
    hits = client.lookup(list(dem), report)
    mismatch = 0
    for h in hits:
        d = dem.get(h.gnis_id)
        if h.elevation_m is not None and d is not None and abs(h.elevation_m - d) > 50:
            mismatch += 1
    if hits:
        await conn.execute(text("UPDATE objectives SET wikidata_qid = :q, sitelinks = :s, updated_at = now() WHERE gnis_id = :g"),
                           [{"q": h.qid, "s": h.sitelinks, "g": h.gnis_id} for h in hits])
    return {"linked": len(hits), "duplicates": report.quarantined_total(), "elevation_mismatch_50m": mismatch}
```

Append `"app.pipelines.wikidata"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_wikidata.py -q && uv run mypy` → PASS. **Step 5: Commit** — `git add backend/app/pipelines/wikidata.py backend/tests/test_wikidata.py backend/pyproject.toml && git commit -m "feat(pipelines): Wikidata QID, sitelinks and elevation cross-check for objectives"`

---

### Task 4: Curated objective routes and permit counts (CC0, our words)

**Files:**
- Create: `backend/app/pipelines/curated.py`, `backend/tests/test_curated.py`, `data/curated/README.md`, `data/curated/objective_routes.csv`, `data/curated/objective_permit_counts.csv`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: CSV headers `OBJECTIVE_ROUTE_COLUMNS = ("objective_ref", "objective_name", "state", "lat", "lon", "kind", "route_name", "type_group", "grade_text", "season_months", "summary", "source_url", "source_kind")` where `objective_ref` is `gnis:<id>` or `curated:<slug>`; `PERMIT_COLUMNS = ("objective_ref", "year", "month", "attempts", "summits", "source_url")`; `async load_objective_routes(conn, path: Path, report) -> int`, `async load_permit_counts(conn, path: Path, report, today: date) -> int`; CLI `python -m app.pipelines.curated routes|permits`.

Behaviour: a `gnis:<id>` ref must exist in `gnis_summits` (the objective is created if not seeded yet, with the summit's `state_code`); a `curated:<slug>` ref needs `lat`, `lon`, `state`, `kind` and creates a `source='curated'`, `license='CC0'` objective with id `objective_id_for(kind, ref)`. Both paths store `point_key` and `tz` via `objectives.point_fields`. Each route row upserts `objective_routes` and a `canonical_routes` row (`source='safeascent_curated'`, `redistributable=true`, `type_group` from the CSV, `scored=true`, area = the objective's area), and a `route_objective_links` row (`relation='on'`, `method='curated'`); then `register_objective_buckets` runs so a new curated objective's bucket is `pending`. `summary` ≤300 characters. A year after the current year (or the current year with a month after the current month, or a current-year whole-year total) in permit counts is quarantined `future`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_curated.py`:

```python
import asyncio
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.curated import OBJECTIVE_ROUTE_COLUMNS, PERMIT_COLUMNS, load_objective_routes, load_permit_counts
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url

SEED = "INSERT INTO gnis_summits (gnis_id, name, name_key, state_code, lat, lon) VALUES (1, 'Fixture Peak', 'fixture', 'WA', 46.853, -121.760);"


def _write(tmp_path, name, header, rows):
    path = tmp_path / name
    path.write_text(",".join(header) + "\n" + "\n".join(rows) + "\n")
    return path


def _go(name, fn):
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
def test_routes_create_objective_area_route_and_link(tmp_path):
    path = _write(tmp_path, "r.csv", OBJECTIVE_ROUTE_COLUMNS, [
        'gnis:1,,,,,peak,Standard Route,alpine,II,"6,7,8",Glaciated walk-up in our own words.,https://www.nps.gov/fixture,nps',
        'curated:fixture-ice,Fixture Ice Venue,NH,44.1,-71.3,ice_area,Main Flow,ice,WI3,"12,1,2",Our own words.,https://www.fs.usda.gov/fixture,usfs',
    ])
    with migrated_db(seed_sql=SEED) as name:
        report = ValidationReport("t")
        assert _go(name, lambda c: load_objective_routes(c, path, report)) == 2
        rows = _go(name, lambda c: c.execute(text(
            "SELECT r.source, r.type_group, l.relation, o.kind, o.state_code, o.tz FROM canonical_routes r "
            "JOIN route_objective_links l USING (route_id) JOIN objectives o USING (objective_id) ORDER BY o.kind")))
        assert [tuple(r) for r in rows] == [
            ("safeascent_curated", "ice", "on", "ice_area", "NH", "America/New_York"),
            ("safeascent_curated", "alpine", "on", "peak", "WA", "America/Los_Angeles"),
        ]
        pending = _go(name, lambda c: c.execute(text("SELECT count(*) FROM cell_normals_status WHERE status = 'pending'")))
        assert [tuple(r) for r in pending] == [(2,)]


@requires_pg
def test_ambiguous_objective_reference_is_rejected(tmp_path):
    path = _write(tmp_path, "r.csv", OBJECTIVE_ROUTE_COLUMNS, [
        'curated:no-coords,Somewhere,,,,peak,R,alpine,,,Words.,https://www.nps.gov/x,nps',
        'gnis:999,,,,,peak,R,alpine,,,Words.,https://www.nps.gov/x,nps',
    ])
    with migrated_db(seed_sql=SEED) as name:
        report = ValidationReport("t")
        assert _go(name, lambda c: load_objective_routes(c, path, report)) == 0
        assert report.quarantined == {"curated_needs_location": 1, "unknown_gnis": 1}


@requires_pg
def test_future_permit_years_are_quarantined(tmp_path):
    routes = _write(tmp_path, "r.csv", OBJECTIVE_ROUTE_COLUMNS, ['gnis:1,,,,,peak,R,alpine,,,Words.,https://www.nps.gov/x,nps'])
    permits = _write(tmp_path, "p.csv", PERMIT_COLUMNS, ["gnis:1,2025,,1200,600,https://www.nps.gov/p", "gnis:1,2027,,5,1,https://www.nps.gov/p"])
    with migrated_db(seed_sql=SEED) as name:
        _go(name, lambda c: load_objective_routes(c, routes, ValidationReport("t")))
        report = ValidationReport("t")
        assert _go(name, lambda c: load_permit_counts(c, permits, report, date(2026, 9, 28))) == 1
        assert report.quarantined == {"future": 1}
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/curated.py`:

```python
"""Hand-curated CC0 objective routes and published permit counts, in our own words."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.objectives import (ensure_objective_areas, objective_area_id, objective_id_for, point_fields,
                                      register_objective_buckets)
from app.pipelines.route_types import TYPE_RULE_VERSION
from app.pipelines.validate import ValidationReport, coord_problem

OBJECTIVE_ROUTE_COLUMNS = ("objective_ref", "objective_name", "state", "lat", "lon", "kind", "route_name", "type_group",
                           "grade_text", "season_months", "summary", "source_url", "source_kind")
PERMIT_COLUMNS = ("objective_ref", "year", "month", "attempts", "summits", "source_url")
_NS = uuid.UUID("0b2d7e61-5a4c-4e57-8f0e-6c4a9d2b7e19")


class RouteRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    objective_ref: str = Field(pattern=r"^(gnis:\d+|curated:[a-z0-9-]+)$")
    objective_name: str | None = None
    state: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    lat: float | None = None
    lon: float | None = None
    kind: Literal["peak", "ice_area", "glacier", "formation"]
    route_name: str = Field(min_length=1)
    type_group: Literal["alpine", "ice", "mixed", "trad", "sport"]
    grade_text: str | None = None
    season_months: list[int] | None = None
    summary: str = Field(min_length=1, max_length=300)
    source_url: str = Field(pattern=r"^https://")
    source_kind: Literal["nps", "usfs", "owner"]

    @field_validator("*", mode="before")
    @classmethod
    def _blank(cls, v: object) -> object:
        return None if v == "" else v

    @field_validator("season_months", mode="before")
    @classmethod
    def _months(cls, v: object) -> object:
        if isinstance(v, str):
            months = [int(x) for x in v.split(",") if x.strip()]
            if any(not 1 <= m <= 12 for m in months):
                raise ValueError("months must be 1..12")
            return months
        return v


async def _objective_for(conn: AsyncConnection, row: RouteRow, report: ValidationReport, ref: str) -> uuid.UUID | None:
    if row.objective_ref.startswith("gnis:"):
        gid = int(row.objective_ref.split(":", 1)[1])
        g = (await conn.execute(text("SELECT name, state_code, lat, lon FROM gnis_summits WHERE gnis_id = :g"), {"g": gid})).first()
        if g is None:
            report.quarantine(ref, "unknown_gnis")
            return None
        oid = objective_id_for("peak", f"gnis:{gid}")
        await conn.execute(text(
            "INSERT INTO objectives (objective_id, kind, name, state_code, lat, lon, geom, point_key, tz, gnis_id, disciplines, "
            "source, license) VALUES (:o, :k, :n, :s, :lat, :lon, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, "
            ":pk, :tz, :g, ARRAY[:t], 'gnis', 'public_domain') ON CONFLICT (objective_id) DO UPDATE SET disciplines = "
            "(SELECT array_agg(DISTINCT d) FROM unnest(objectives.disciplines || EXCLUDED.disciplines) d)"),
            {"o": oid, "k": row.kind, "n": g.name, "s": g.state_code, "lat": g.lat, "lon": g.lon, "g": gid,
             "t": row.type_group, **point_fields(float(g.lat), float(g.lon))})
        return oid
    if row.lat is None or row.lon is None or not row.state or not row.objective_name or coord_problem(row.lat, row.lon):
        report.quarantine(ref, "curated_needs_location")
        return None
    oid = objective_id_for(row.kind, row.objective_ref)
    await conn.execute(text(
        "INSERT INTO objectives (objective_id, kind, name, state_code, lat, lon, geom, point_key, tz, disciplines, source, "
        "license, curated_by) VALUES (:o, :k, :n, :s, :lat, :lon, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, "
        ":pk, :tz, ARRAY[:t], 'curated', 'CC0', 'owner') ON CONFLICT (objective_id) DO UPDATE SET disciplines = "
        "(SELECT array_agg(DISTINCT d) FROM unnest(objectives.disciplines || EXCLUDED.disciplines) d)"),
        {"o": oid, "k": row.kind, "n": row.objective_name, "s": row.state, "lat": row.lat, "lon": row.lon,
         "t": row.type_group, **point_fields(row.lat, row.lon)})
    return oid


async def load_objective_routes(conn: AsyncConnection, path: Path, report: ValidationReport) -> int:
    loaded = 0
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != OBJECTIVE_ROUTE_COLUMNS:
            raise ValueError(f"{path.name}: header must be {','.join(OBJECTIVE_ROUTE_COLUMNS)}")
        rows = list(enumerate(reader, start=2))
    pending: list[tuple[RouteRow, uuid.UUID]] = []
    for number, raw in rows:
        ref = f"{path.name}:{number}"
        try:
            row = RouteRow.model_validate(raw)
        except ValidationError as exc:
            report.quarantine(ref, "invalid", fields=sorted({str(e["loc"][0]) for e in exc.errors()}))
            continue
        oid = await _objective_for(conn, row, report, ref)
        if oid is not None:
            pending.append((row, oid))
    await ensure_objective_areas(conn)
    for row, oid in pending:
        route_id = uuid.uuid5(_NS, f"{row.objective_ref}:{row.route_name.lower()}")
        await conn.execute(text(
            "INSERT INTO canonical_routes (route_id, area_id, name, grade, disciplines, type_group, type_rule_version, "
            "is_boulder, scored, source, redistributable) VALUES (:r, :a, :n, :g, ARRAY[:t], :t, :trv, false, true, "
            "'safeascent_curated', true) ON CONFLICT (route_id) DO UPDATE SET name = EXCLUDED.name, grade = EXCLUDED.grade, "
            "type_group = EXCLUDED.type_group, updated_at = now()"),
            {"r": route_id, "a": objective_area_id(oid), "n": row.route_name, "g": row.grade_text, "t": row.type_group,
             "trv": TYPE_RULE_VERSION})
        await conn.execute(text(
            "INSERT INTO objective_routes (objective_route_id, objective_id, route_id, name, type_group, grade_text, "
            "season_months, summary, source_url, source_kind) VALUES (:r, :o, :r, :n, :t, :g, :m, :s, :u, :k) "
            "ON CONFLICT (objective_route_id) DO UPDATE SET grade_text = EXCLUDED.grade_text, season_months = EXCLUDED.season_months, "
            "summary = EXCLUDED.summary, source_url = EXCLUDED.source_url, curated_at = now()"),
            {"r": route_id, "o": oid, "n": row.route_name, "t": row.type_group, "g": row.grade_text,
             "m": row.season_months, "s": row.summary, "u": row.source_url, "k": row.source_kind})
        await conn.execute(text(
            "INSERT INTO route_objective_links (route_id, objective_id, relation, method, score) VALUES (:r, :o, 'on', 'curated', 1.0) "
            "ON CONFLICT DO NOTHING"), {"r": route_id, "o": oid})
        report.accept()
        loaded += 1
    await register_objective_buckets(conn)
    return loaded


class PermitRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    objective_ref: str = Field(pattern=r"^(gnis:\d+|curated:[a-z0-9-]+)$")
    year: int = Field(ge=1950)
    month: int = Field(default=0, ge=0, le=12)
    attempts: int | None = Field(default=None, ge=0)
    summits: int | None = Field(default=None, ge=0)
    source_url: str = Field(pattern=r"^https://")

    @field_validator("*", mode="before")
    @classmethod
    def _blank(cls, v: object) -> object:
        return None if v == "" else v

    @field_validator("month", mode="before")
    @classmethod
    def _annual(cls, v: object) -> object:
        return 0 if v in ("", None) else v


async def load_permit_counts(conn: AsyncConnection, path: Path, report: ValidationReport, today: date) -> int:
    known = {str(r) for (r,) in (await conn.execute(text(
        "SELECT 'gnis:' || gnis_id FROM objectives WHERE gnis_id IS NOT NULL"))).all()}
    loaded = 0
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != PERMIT_COLUMNS:
            raise ValueError(f"{path.name}: header must be {','.join(PERMIT_COLUMNS)}")
        for number, raw in enumerate(reader, start=2):
            ref = f"{path.name}:{number}"
            try:
                row = PermitRow.model_validate(raw)
            except ValidationError:
                report.quarantine(ref, "invalid")
                continue
            if row.year > today.year or (row.year == today.year and (row.month == 0 or row.month > today.month)):
                report.quarantine(ref, "future")
                continue
            if row.objective_ref.startswith("gnis:") and row.objective_ref not in known:
                report.quarantine(ref, "unknown_objective")
                continue
            if row.objective_ref.startswith("gnis:"):
                oid = objective_id_for("peak", row.objective_ref)
            else:
                oid = (await conn.execute(text(
                    "SELECT objective_id FROM objectives WHERE objective_id = ANY(:ids)"),
                    {"ids": [objective_id_for(k, row.objective_ref) for k in ("peak", "ice_area", "glacier", "formation")]})).scalar()
            if oid is None:
                report.quarantine(ref, "unknown_objective")
                continue
            await conn.execute(text(
                "INSERT INTO objective_permit_counts (objective_id, year, month, attempts, summits, source_url) "
                "VALUES (:o, :y, :m, :a, :s, :u) ON CONFLICT (objective_id, year, month) DO UPDATE SET "
                "attempts = EXCLUDED.attempts, summits = EXCLUDED.summits, source_url = EXCLUDED.source_url"),
                {"o": oid, "y": row.year, "m": row.month, "a": row.attempts, "s": row.summits, "u": row.source_url})
            report.accept()
            loaded += 1
    return loaded


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    repo = Path(__file__).resolve().parents[3]
    report = ValidationReport(f"curated_{args.step}")
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            if args.step == "routes":
                n = await load_objective_routes(conn, repo / "data/curated/objective_routes.csv", report)
            else:
                n = await load_permit_counts(conn, repo / "data/curated/objective_permit_counts.csv", report, utc_today())
    finally:
        await engine.dispose()
    return {"loaded": n, "report": report.summary()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["routes", "permits"])
    print(json.dumps(asyncio.run(_main(parser.parse_args())), sort_keys=True))
```

A current-year whole-year row (month blank → 0) is treated as future (the year is not over); monthly rows up to the current month are allowed. The curated objective's permit lookup tries each kind because the id is `uuid5(kind, ref)`.

`data/curated/README.md`:

```markdown
# Curated records (CC0, our own words)

- `objective_routes.csv` — standard mountaineering routes for hotspot objectives (spec P2-2 step 5). Facts from NPS/USFS public pages (name, grade, season) plus a `summary` of at most 300 characters written by us. Never copy text from Mountain Project or paste from source pages. `objective_ref` is `gnis:<GNIS feature id>` or `curated:<slug>` (then `objective_name`, `state` (two-letter code), `lat`, `lon` are required).
- `objective_permit_counts.csv` — published permit/attempt counts (e.g. NPS Denali and Rainier climbing statistics), with the https URL of the published source.

Both are loaded by `python -m app.pipelines.curated routes|permits` as the ingest role. Everything here is released CC0.
```

`data/curated/objective_routes.csv`: the header line only. `data/curated/objective_permit_counts.csv`: the header line only. Append `"app.pipelines.curated"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_curated.py -q && uv run mypy && python ../scripts/check_no_mp_data.py` → PASS (no MP URLs or ids in `data/curated`). **Step 5: Commit** — `git add backend/app/pipelines/curated.py backend/tests/test_curated.py data/curated/ backend/pyproject.toml && git commit -m "feat(pipelines): curated CC0 objective routes and permit counts loaders"`

---

### Task 5: Route and accident links to objectives

**Files:**
- Modify: `backend/app/pipelines/objectives.py`, `backend/tests/test_objectives.py`, `backend/db/roles/grants_phase2.sql`, `backend/app/data/repair/legacy_links.py` (plan 5's R10 review export), `backend/tests/test_legacy_links.py`

**Interfaces:**
- Produces: `ROUTE_NEAR_M = 2000`, `ACCIDENT_NEAR_M = 3000`, `async link_routes(conn) -> int` (`near` links for scorable routes whose area point is within 2 km of an objective, method `distance`), `async link_accidents(conn) -> dict[str, int]` (`linked`: clean, objective-eligible accidents with no `accident_route_links` row within 3 km → `objective_id`, method `objective_near`; `skipped_rock_context`: unlinked clean accidents within 3 km that fail `objective_eligible_sql`), `async link_all(conn) -> dict[str, int]`; CLI step `links`.

- [ ] **Step 1: Failing test** — append to `test_objectives.py`:

```python
from app.pipelines.objectives import link_accidents, link_routes


@requires_pg
def test_links_near_routes_and_objective_eligible_accidents_only():
    extra = SEED + """
    INSERT INTO canonical_areas (area_id, name, path, lat, lon, geom, coord_precision, source, redistributable) VALUES
      ('00000000-0000-0000-0000-00000000000b', 'Near Snowfield', '0000000000000000000000000000000b', 46.86, -121.76,
       ST_SetSRID(ST_MakePoint(-121.76, 46.86), 4326)::geography, 'area_centroid', 'openbeta', true),
      ('00000000-0000-0000-0000-00000000000c', 'Rock Crag', '0000000000000000000000000000000c', 46.845, -121.765,
       ST_SetSRID(ST_MakePoint(-121.765, 46.845), 4326)::geography, 'area_centroid', 'openbeta', true);
    INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable) VALUES
      ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-00000000000b', 'Near', '{alpine}', 'alpine', 'rt-v1', false, true, 'openbeta', true),
      ('00000000-0000-0000-0000-0000000000d2', '00000000-0000-0000-0000-00000000000c', 'Crack', '{trad}', 'trad', 'rt-v1', false, true, 'openbeta', true);
    INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity, activity_class, country, date_precision) VALUES
      (2, 'AAC', '2011-07-02', 46.85, -121.76, true, 'Mountaineering', 'climbing', 'US', 'day'),
      (3, 'AAC', '2012-07-02', 46.852, -121.761, true, 'Rock Climbing', 'climbing', 'US', 'day'),
      (5, 'AAC', '2013-07-02', 46.846, -121.765, true, 'Climbing', 'climbing', 'US', 'day');
    INSERT INTO accident_route_links (accident_id, canonical_area_id, method) VALUES (2, '00000000-0000-0000-0000-00000000000b', 'x');
    """
    with migrated_db(seed_sql=extra) as name:
        _run(name, seed_gnis)
        assert _run(name, link_routes) == 2
        assert _run(name, link_accidents) == {"linked": 1, "skipped_rock_context": 2}
        rows = _run(name, lambda c: c.execute(text("SELECT accident_id, method FROM accident_route_links ORDER BY 1")))
        assert [tuple(r) for r in rows] == [(1, "objective_near"), (2, "x")]
```

Accident 3 matches the rock lexicon; accident 5 is 100 m from a trad crag and ~1 km from the summit. Both stay unlinked and are counted.

- [ ] **Step 2: Implement** — append to `objectives.py`:

```python
ROUTE_NEAR_M = 2000
ACCIDENT_NEAR_M = 3000


async def link_routes(conn: AsyncConnection) -> int:
    result = await conn.execute(text(
        "INSERT INTO route_objective_links (route_id, objective_id, relation, method, score) "
        "SELECT r.route_id, o.objective_id, 'near', 'distance', 1 - ST_Distance(a.geom, o.geom) / :d "
        "FROM scorable_routes r JOIN canonical_areas a ON a.area_id = r.area_id JOIN objectives o "
        "ON a.geom IS NOT NULL AND ST_DWithin(a.geom, o.geom, :d) ON CONFLICT (route_id, objective_id) DO NOTHING"),
        {"d": ROUTE_NEAR_M})
    return result.rowcount


_UNLINKED_NEAR = (
    "FROM accidents_clean a JOIN objectives o ON a.coordinates IS NOT NULL AND ST_DWithin(a.coordinates, o.geom, :d) "
    "WHERE NOT EXISTS (SELECT 1 FROM accident_route_links l WHERE l.accident_id = a.accident_id)"
)


async def link_accidents(conn: AsyncConnection) -> dict[str, int]:
    eligible = objective_eligible_sql("a", "o.geom")
    skipped = (await conn.execute(text(
        f"SELECT count(DISTINCT a.accident_id) {_UNLINKED_NEAR} AND NOT EXISTS ("
        f" SELECT 1 FROM objectives o2 WHERE ST_DWithin(a.coordinates, o2.geom, :d) "
        f" AND {objective_eligible_sql('a', 'o2.geom')})"),
        {"d": ACCIDENT_NEAR_M, **TERMS})).scalar_one()
    result = await conn.execute(text(
        "INSERT INTO accident_route_links (accident_id, objective_id, method, score) "
        "SELECT DISTINCT ON (a.accident_id) a.accident_id, o.objective_id, 'objective_near', "
        f" 1 - ST_Distance(a.coordinates, o.geom) / :d {_UNLINKED_NEAR} AND {eligible} "
        "ORDER BY a.accident_id, ST_Distance(a.coordinates, o.geom)"),
        {"d": ACCIDENT_NEAR_M, **TERMS})
    return {"linked": result.rowcount, "skipped_rock_context": int(skipped)}


async def link_all(conn: AsyncConnection) -> dict[str, int]:
    return {"routes": await link_routes(conn)} | await link_accidents(conn)
```

Replace `_main` and the `__main__` block from Task 2 with the final CLI:

```python
async def _main(args: argparse.Namespace) -> dict[str, int]:
    from app.pipelines.db import ingest_engine
    from app.pipelines.wikidata import WikidataClient, link_objectives

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            if args.step == "seed":
                return await seed_all(conn)
            if args.step == "wikidata":
                return await link_objectives(conn, WikidataClient())
            return await link_all(conn)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["seed", "wikidata", "links"])
    print(json.dumps(asyncio.run(_main(parser.parse_args())), sort_keys=True))
```

Grants: `ingest` needs `SELECT ON public.scorable_routes` — append `GRANT SELECT ON public.scorable_routes TO ingest;` to `grants_phase2.sql` under `-- Plan 6 (0011)`.

- [ ] **Step 3: Objective names in the R10 review export** — plan 5's `legacy_links.EXPORT_SQL` carries a placeholder join (`LEFT JOIN LATERAL (SELECT NULL::text AS name) o ON true`) because `objectives` did not exist at `0010`. Now it does, so the owner's Task 11 review shows objective candidates by name. Failing test first — append to `backend/tests/test_legacy_links.py`:

```python
@requires_pg
def test_export_names_objective_candidates(tmp_path: Path):
    seed = (
        "INSERT INTO mountains (mountain_id, name) VALUES (7, 'Fixture Mountain');"
        "INSERT INTO accidents (accident_id, mountain_id) VALUES (900000007, 7);"
        "INSERT INTO objectives (objective_id, kind, name, lat, lon, geom, point_key, disciplines, source, license) VALUES "
        "('00000000-0000-0000-0000-0000000000f1', 'peak', 'Fixture Peak', 40.2, -105.6, "
        "ST_SetSRID(ST_MakePoint(-105.6, 40.2), 4326)::geography, '40.20000:-105.60000', '{alpine}', 'gnis', 'public_domain');"
        "INSERT INTO internal.r10_unresolved (accident_id, legacy_mountain_id, candidate_kind, best_candidate_id, best_score, reason) "
        "VALUES (900000007, 7, 'objective', '00000000-0000-0000-0000-0000000000f1', 0.85, 'objective_candidate');"
    )

    async def go(url: str) -> int:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                return await export_review(conn, tmp_path / "r10.csv")
        finally:
            await engine.dispose()

    with migrated_db("0011_objectives", seed_sql=seed) as name:
        assert asyncio.run(go(sa_url(name))) == 1
    row = (tmp_path / "r10.csv").read_text().splitlines()[1].split(",")
    assert row[1] == "objective_candidate" and row[4] == "objective" and row[6] == "Fixture Peak"
```

Then in `legacy_links.py` replace the placeholder line of `EXPORT_SQL` with:

```python
    "LEFT JOIN objectives o ON u.candidate_kind = 'objective' AND o.objective_id = u.best_candidate_id "
```

(and delete plan 5's note that names the placeholder). Plan 5's `test_owner_decisions_are_applied_and_survive_reruns` calls `export_review` at `PIN = "0010_static_features"`, where `objectives` does not exist; change its `migrated_db(PIN, …)` to `migrated_db("0011_objectives", …)` (its assertions hold there: accident 4 is still undecided, three rows export). `test_rerun_after_objectives_links_the_mountain` creates its own `objectives` table and stays at `PIN`. Run `uv run pytest tests/test_legacy_links.py -q` → PASS.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_objectives.py tests/test_legacy_links.py -q && uv run mypy` → PASS. **Step 5: Commit** — `git add backend/app/pipelines/objectives.py backend/tests/test_objectives.py backend/db/roles/grants_phase2.sql backend/app/data/repair/legacy_links.py backend/tests/test_legacy_links.py && git commit -m "feat(pipelines): link nearby routes and objective-eligible accidents to objectives; name objective candidates in the R10 export"`

---

### Task 6: Coverage badges and the hotspot report

**Files:**
- Create: `backend/app/pipelines/coverage.py`, `backend/tests/test_coverage.py`, `backend/tests/test_coverage_db.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `COVERAGE_RULE_VERSION = "cv-v1"`, `Level = Literal["route", "objective", "thin"]`, `Reason = Literal["route_min", "accidents_no_curated", "objective", "few_routes"]`, `ROUTE_LEVEL_MIN = 5`, `THIN_RADIUS_M = 5000`, `badge_with_reason(n_routes: int, *, has_objective: bool, has_curated_route: bool, accidents_5km: int) -> tuple[Level, Reason] | None`, `badge_for(...)` (same arguments) `-> Level | None` (unchanged frozen signature, delegates), `async compute_badges(conn) -> dict[str, int]`, `async hotspots(conn, limit: int = 30) -> list[dict[str, object]]` (objectives ranked by clean objective-eligible accidents within 5 km where the objective has <5 routes in alpine/ice/mixed), CLI `python -m app.pipelines.coverage badges|hotspots`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_coverage.py`:

```python
from app.pipelines.coverage import badge_for, badge_with_reason


def test_route_level_needs_five_routes():
    assert badge_with_reason(5, has_objective=True, has_curated_route=False, accidents_5km=9) == ("route", "route_min")


def test_thin_beats_objective_without_curated_route():
    assert badge_for(2, has_objective=True, has_curated_route=False, accidents_5km=1) == "thin"
    assert badge_with_reason(2, has_objective=True, has_curated_route=True, accidents_5km=1) == ("objective", "objective")


def test_one_to_four_routes_are_thin_few_routes():
    for n in (1, 2, 3, 4):
        assert badge_with_reason(n, has_objective=False, has_curated_route=False, accidents_5km=0) == ("thin", "few_routes")


def test_objective_level_and_fallbacks():
    assert badge_for(0, has_objective=True, has_curated_route=False, accidents_5km=0) == "objective"
    assert badge_for(0, has_objective=False, has_curated_route=False, accidents_5km=0) is None
    assert badge_with_reason(0, has_objective=False, has_curated_route=False, accidents_5km=2) == ("thin", "accidents_no_curated")

```

`backend/tests/test_coverage_db.py`:

```python
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.coverage import compute_badges
from tests.pgtest import migrated_db, requires_pg, sa_url

AREA = ("INSERT INTO canonical_areas (area_id, name, path, lat, lon, geom, objective_id, coord_precision, source, redistributable) "
        "VALUES ('{id}', '{name}', '{label}', {lat}, {lon}, ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography, {obj}, "
        "'area_centroid', '{src}', true);")
ROUTES = ("INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, is_boulder, scored, "
          "source, redistributable) SELECT ('00000000-0000-0000-00{p}-' || lpad(g::text, 12, '0'))::uuid, '{area}', 'R' || g, "
          "ARRAY['{tg}'], '{tg}', 'rt-v1', false, true, 'openbeta', true FROM generate_series(1, {n}) g;")
SEED = "\n".join([
    AREA.format(id="00000000-0000-0000-0000-0000000000b1", name="Sport Crag", label="000000000000000000000000000000b1",
                lat=40.0, lon=-105.3, obj="NULL", src="openbeta"),
    ROUTES.format(p="01", area="00000000-0000-0000-0000-0000000000b1", tg="sport", n=5),
    AREA.format(id="00000000-0000-0000-0000-0000000000b2", name="Small Crag", label="000000000000000000000000000000b2",
                lat=38.0, lon=-109.5, obj="NULL", src="openbeta"),
    ROUTES.format(p="02", area="00000000-0000-0000-0000-0000000000b2", tg="trad", n=2),
    "INSERT INTO objectives (objective_id, kind, name, state_code, lat, lon, geom, point_key, tz, disciplines, source, license) "
    "VALUES ('00000000-0000-0000-0000-0000000000f1', 'peak', 'Fixture Peak', 'CO', 40.255, -105.615, "
    "ST_SetSRID(ST_MakePoint(-105.615, 40.255), 4326)::geography, '40.25500:-105.61500', 'America/Denver', '{alpine}', 'gnis', 'public_domain');",
    AREA.format(id="00000000-0000-0000-0000-0000000000f2", name="Fixture Peak", label="000000000000000000000000000000f2",
                lat=40.255, lon=-105.615, obj="'00000000-0000-0000-0000-0000000000f1'", src="safeascent_curated"),
    AREA.format(id="00000000-0000-0000-0000-0000000000b3", name="Fixture Face", label="000000000000000000000000000000b3",
                lat=40.256, lon=-105.617, obj="NULL", src="openbeta"),
    ROUTES.format(p="03", area="00000000-0000-0000-0000-0000000000b3", tg="alpine", n=5),
    "INSERT INTO route_objective_links (route_id, objective_id, relation, method, score) SELECT route_id, "
    "'00000000-0000-0000-0000-0000000000f1', 'near', 'distance', 0.9 FROM canonical_routes "
    "WHERE area_id = '00000000-0000-0000-0000-0000000000b3';",
])


@requires_pg
def test_compute_badges_against_a_database():
    async def go(url: str) -> tuple[dict[str, int], list[tuple[object, ...]], str]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                counts = await compute_badges(conn)
                rows = [tuple(r) for r in (await conn.execute(text(
                    "SELECT scope_kind, type_group, level, reason, n_routes FROM coverage_badges ORDER BY 1, 2, 3"))).all()]
                level = (await conn.execute(text("SELECT coverage_level FROM objectives"))).scalar_one()
            return counts, rows, str(level)
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        counts, rows, level = asyncio.run(go(sa_url(name)))
    assert rows == [
        ("area", "alpine", "route", "route_min", 5),
        ("area", "sport", "route", "route_min", 5),
        ("area", "trad", "thin", "few_routes", 2),
        ("objective", "alpine", "route", "route_min", 5),
    ]
    assert counts == {"route": 3, "objective": 0, "thin": 1}
    assert level == "route"
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/coverage.py`:

```python
"""Coverage badges (P2-2 step 8), versioned. Thin data shows Phase 3's insufficient chip
with a coverage warning and never a low score."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.objectives import TERMS, objective_eligible_sql

COVERAGE_RULE_VERSION = "cv-v1"
Level = Literal["route", "objective", "thin"]
Reason = Literal["route_min", "accidents_no_curated", "objective", "few_routes"]
ROUTE_LEVEL_MIN = 5
THIN_RADIUS_M = 5000
TYPE_GROUPS = ("sport", "trad", "alpine", "ice", "mixed")


def badge_with_reason(n_routes: int, *, has_objective: bool, has_curated_route: bool,
                      accidents_5km: int) -> tuple[Level, Reason] | None:
    if n_routes >= ROUTE_LEVEL_MIN:
        return "route", "route_min"
    if accidents_5km >= 1 and not has_curated_route:
        return "thin", "accidents_no_curated"
    if has_objective:
        return "objective", "objective"
    if n_routes >= 1:
        # [default pending: DP4] 1–4 routes are thin, never route-level (spec ≥5; amendment D1).
        return "thin", "few_routes"
    return None


def badge_for(n_routes: int, *, has_objective: bool, has_curated_route: bool, accidents_5km: int) -> Level | None:
    badge = badge_with_reason(n_routes, has_objective=has_objective, has_curated_route=has_curated_route,
                              accidents_5km=accidents_5km)
    return None if badge is None else badge[0]


AREA_FACTS = """
WITH crags AS (
  SELECT a.area_id, a.path, a.geom, a.lat, a.objective_id FROM canonical_areas a
  WHERE a.retired_at IS NULL AND a.geom IS NOT NULL
    AND (a.objective_id IS NOT NULL OR EXISTS (SELECT 1 FROM canonical_routes r WHERE r.area_id = a.area_id))
), unit_routes AS (
  SELECT c.area_id, s.route_id, s.type_group FROM crags c
  JOIN canonical_areas d ON d.path <@ c.path JOIN scorable_routes s ON s.area_id = d.area_id
  UNION
  SELECT c.area_id, s.route_id, s.type_group FROM crags c
  JOIN route_objective_links l ON l.objective_id = c.objective_id JOIN scorable_routes s ON s.route_id = l.route_id
), counts AS (
  SELECT area_id, type_group, count(*) AS n FROM unit_routes GROUP BY 1, 2
), acc AS (
  SELECT c.area_id, count(x.accident_id) AS n FROM crags c
  LEFT JOIN accidents_clean x ON x.coordinates IS NOT NULL AND ST_DWithin(x.coordinates, c.geom, :r)
  GROUP BY 1
)
SELECT c.area_id, c.lat, c.objective_id, t.tg, coalesce(k.n, 0) AS n_routes, coalesce(acc.n, 0) AS n_acc,
       EXISTS (SELECT 1 FROM objective_routes o WHERE o.objective_id = c.objective_id AND o.type_group = t.tg) AS curated
FROM crags c CROSS JOIN unnest(ARRAY['sport','trad','alpine','ice','mixed']) AS t(tg)
LEFT JOIN counts k ON k.area_id = c.area_id AND k.type_group = t.tg
LEFT JOIN acc ON acc.area_id = c.area_id
WHERE coalesce(k.n, 0) > 0
   OR (c.objective_id IS NOT NULL AND t.tg = ANY ((SELECT disciplines FROM objectives WHERE objective_id = c.objective_id)::text[]))
"""


async def compute_badges(conn: AsyncConnection) -> dict[str, int]:
    rows = (await conn.execute(text(AREA_FACTS), {"r": THIN_RADIUS_M})).all()
    await conn.execute(text("DELETE FROM coverage_badges"))
    params = []
    per_objective: dict[str, list[str]] = {}
    for area_id, _lat, objective_id, tg, n_routes, n_acc, curated in rows:
        badge = badge_with_reason(int(n_routes), has_objective=objective_id is not None, has_curated_route=bool(curated),
                                  accidents_5km=int(n_acc))
        if badge is None:
            continue
        level, reason = badge
        scope_kind, scope_id = ("objective", objective_id) if objective_id is not None else ("area", area_id)
        params.append({"k": scope_kind, "i": scope_id, "t": tg, "l": level, "why": reason, "n": int(n_routes),
                       "a": int(n_acc), "c": bool(curated), "v": COVERAGE_RULE_VERSION})
        if objective_id is not None:
            per_objective.setdefault(str(objective_id), []).append(level)
    if params:
        await conn.execute(text(
            "INSERT INTO coverage_badges (scope_kind, scope_id, type_group, level, reason, n_routes, n_clean_accidents_5km, "
            "has_curated_route, coverage_rule_version) VALUES (:k, :i, :t, :l, :why, :n, :a, :c, :v)"),
            params)
    worst = {"thin": 0, "objective": 1, "route": 2}
    for oid, levels in per_objective.items():
        await conn.execute(text(
            "UPDATE objectives SET coverage_level = :l, coverage_rule_version = :v, updated_at = now() WHERE objective_id = :o"),
            {"l": min(levels, key=worst.__getitem__), "v": COVERAGE_RULE_VERSION, "o": oid})
    counts: dict[str, int] = {"route": 0, "objective": 0, "thin": 0}
    for p in params:
        counts[str(p["l"])] += 1
    return counts


HOTSPOTS = f"""
SELECT o.objective_id, o.name, o.lat, o.lon, count(DISTINCT a.accident_id) AS n_acc,
       coalesce(max(b.n_routes), 0) AS routes, bool_or(coalesce(b.has_curated_route, false)) AS curated
FROM objectives o
JOIN accidents_clean a ON a.coordinates IS NOT NULL AND ST_DWithin(a.coordinates, o.geom, :r)
  AND {objective_eligible_sql('a', 'o.geom')}
LEFT JOIN coverage_badges b ON b.scope_kind = 'objective' AND b.scope_id = o.objective_id AND b.type_group IN ('alpine', 'ice', 'mixed')
GROUP BY o.objective_id, o.name, o.lat, o.lon
HAVING coalesce(max(b.n_routes), 0) < 5
ORDER BY n_acc DESC LIMIT :n
"""


async def hotspots(conn: AsyncConnection, limit: int = 30) -> list[dict[str, object]]:
    rows = (await conn.execute(text(HOTSPOTS), {"r": THIN_RADIUS_M, "n": limit, **TERMS})).all()
    return [{"objective_id": str(r.objective_id), "name": r.name, "lat": r.lat, "lon": r.lon,
             "clean_accidents_5km": int(r.n_acc), "catalog_routes": int(r.routes), "curated": bool(r.curated)} for r in rows]


async def _main(args: argparse.Namespace) -> object:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            return await compute_badges(conn) if args.step == "badges" else await hotspots(conn)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["badges", "hotspots"])
    print(json.dumps(asyncio.run(_main(parser.parse_args())), sort_keys=True, default=str))
```

Area rows carrying an `objective_id` are the objectives' own areas, so objective badges are keyed by the objective and ordinary crags by their area. Append `"app.pipelines.coverage"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_coverage.py tests/test_coverage_db.py -q && uv run mypy` → PASS. **Step 5: Commit** — `git add backend/app/pipelines/coverage.py backend/tests/test_coverage.py backend/tests/test_coverage_db.py backend/pyproject.toml && git commit -m "feat(pipelines): versioned coverage badges with reasons and hotspot ranking"`

---

### Task 7: Acceptance cells and workflow hook

**Files:**
- Create: `backend/tests/verify/test_phase2b_objectives.py`
- Modify: `.github/workflows/data-static-features.yml` (runs `objectives seed`, `objectives links`, `coverage badges` after `static_features routes`)

- [ ] **Step 1: Cells**

```python
import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db
# Exact GNIS names with state; each tuple lists accepted spellings (Denali's GNIS name may still be "Mount McKinley").
SEED_LIST: tuple[tuple[tuple[str, ...], str], ...] = (
    (("Denali", "Mount McKinley"), "AK"), (("Mount Foraker",), "AK"), (("Mount Rainier",), "WA"),
    (("Mount Hood",), "OR"), (("Mount Shasta",), "CA"), (("Mount Baker",), "WA"), (("Mount Adams",), "WA"),
    (("Mount Jefferson",), "OR"), (("North Sister",), "OR"), (("Mount Thompson", "Snoqualmie Mountain"), "WA"),
    (("Mount Washington",), "NH"), (("Longs Peak",), "CO"), (("Mount Whitney",), "CA"),
)



def test_every_objective_has_an_area_a_badge_a_point_key_and_a_time_zone():
    [row] = fetch("SELECT count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM canonical_areas a WHERE a.objective_id = o.objective_id)) AS no_area, "
                  "count(*) FILTER (WHERE o.coverage_level IS NULL) AS no_badge, "
                  "count(*) FILTER (WHERE o.tz IS NULL) AS no_tz FROM objectives o")
    assert (row["no_area"], row["no_badge"], row["no_tz"]) == (0, 0, 0)


def test_no_objective_area_is_a_root_path():
    [row] = fetch("SELECT count(*) AS n FROM canonical_areas WHERE objective_id IS NOT NULL AND nlevel(path) = 1")
    assert row["n"] == 0


def test_every_objective_bucket_is_registered_for_normals():
    [row] = fetch("SELECT count(*) AS n FROM scoring_unit_features u WHERE u.unit_kind = 'objective' AND u.grid_bucket IS NOT NULL "
                  "AND NOT EXISTS (SELECT 1 FROM cell_normals_status s WHERE s.grid_bucket = u.grid_bucket)")
    assert row["n"] == 0


def test_seed_list_objectives_have_a_curated_route():
    rows = fetch("SELECT o.name, o.state_code FROM objectives o "
                 "WHERE EXISTS (SELECT 1 FROM objective_routes r WHERE r.objective_id = o.objective_id)")
    have = {(r["name"], r["state_code"]) for r in rows}
    missing = [names[0] for names, state in SEED_LIST if not any((n, state) in have for n in names)]
    assert missing == [], f"curate these seed-list objectives: {missing}"


def test_no_thin_badge_carries_a_curated_route_reason():
    [row] = fetch("SELECT count(*) AS n FROM coverage_badges WHERE level = 'thin' AND reason = 'accidents_no_curated' AND has_curated_route")
    assert row["n"] == 0


def test_no_badge_below_five_routes_is_route_level():
    [row] = fetch("SELECT count(*) AS n FROM coverage_badges WHERE level = 'route' AND n_routes < 5")
    assert row["n"] == 0
```

Matching is exact on `(name, state_code)`. If the stored GNIS name differs from every spelling listed (the runbook prints `missing`), the agent adds the stored spelling to that tuple in a reviewed commit; substring matching is not used.

- [ ] **Step 2: Workflow** — append to `data-static-features.yml` steps (before the failure step):

```yaml
      - run: uv run python -m app.pipelines.objectives seed
      - run: uv run python -m app.pipelines.objectives links
      - run: uv run python -m app.pipelines.coverage badges
```

- [ ] **Step 3: Run** — `cd backend && uv run pytest -q && uv run mypy` → green. **Step 4: Commit** — `git add backend/tests/verify/test_phase2b_objectives.py .github/workflows/data-static-features.yml && git commit -m "feat(ops): objectives and coverage in the weekly features workflow; acceptance cells"`

---

### Task 8: Docs

- [ ] **Step 1** — CHANGELOG under `## [Unreleased]`, heading `### Phase 2b objectives and coverage (PR 2b-2d) — YYYY-MM-DD`, the date being the UTC day the entry is written (`date -u +%F`): bullets for migration `0011_objectives`, objective seeding with region parents and pending normals buckets, objective-eligible accident links, coverage badges with reasons (1–4 routes are `thin`), curated loaders. CLAUDE.md Commands: `objectives seed|wikidata|links`, `curated routes|permits`, `coverage badges|hotspots`; Data rules: "Curated objective routes are CC0 in our own words; never paste source text". DATABASE_STRUCTURE.md: the five tables and the `scoring_unit_features` view (route and objective rows; `normals_status`).
- [ ] **Step 2** — `cd backend && uv run pytest -q && uv run ruff check . ../scripts/ && uv run mypy` → green.
- [ ] **Step 3: Commit** — `git add CHANGELOG.md CLAUDE.md data/DATABASE_STRUCTURE.md && git commit -m "docs: objectives, curated records, coverage badges"`

---

### Task 9: OWNER/AGENT RUNBOOK — seed, curate, badge, R4/R5 re-run (PR 2b-2d)

All database access goes through plan 2's committed `backend/scripts/runbook_helpers.sh` (`ING`, `INGMOD`, `OWNER_PSQL`, `ANALYST_PSQL`, `VERIFY`; verify-full TLS). From `backend/`, in a shell that already defines plan 1 Task 8 Step 1's `split_pg_url` and `verify_full_url`: `. scripts/runbook_helpers.sh`. `TARGET_HOST` set = that Neon branch; `unset TARGET_HOST` before every prod step. This plan defines no helpers of its own.

- [ ] **Step 1 (owner/agent): Branch rehearsal** — create Neon branch `p2b-2d-rehearsal` from `main` (current data) and set `TARGET_HOST='<p2b-2d-rehearsal direct host>'`. Migrate to `0011_objectives` as `migrator` exactly as plan 1 Task 9 Step 2 does (same `TARGET_HOST` substitution), then `uv run alembic check`. Then:

```bash
OWNER_PSQL -q -f db/roles/grants_phase2.sql
OWNER_PSQL -q -f db/roles/verify_roles.sql
OWNER_PSQL -q -f db/roles/verify_roles_phase2.sql
INGMOD app.pipelines.objectives seed
INGMOD app.pipelines.objectives wikidata
INGMOD app.pipelines.static_features points
INGMOD app.pipelines.static_features elevation --limit 5000
INGMOD app.pipelines.objectives links
INGMOD app.pipelines.coverage badges
INGMOD app.pipelines.coverage hotspots
```

Expected: both verify scripts report no failing check; `seed` prints `no_region_parent: 0` and `no_timezone: 0` (either above 0: stop and investigate with the agent — never default a region or a time zone); `wikidata` prints `linked`, `duplicates`, `elevation_mismatch_50m` (mismatches are investigated, not auto-fixed); `elevation` repeats until `"sampled": 0`; `links` prints `routes`, `linked`, `skipped_rock_context` (record all three in the PR); `badges` prints counts by level; `hotspots` → top 30 list. Normals for new objective buckets are **not** bought now: `seed` registers them `pending`, and the next January Pro window (plan 3's `era5_window`) fills them; until then `scoring_unit_features.normals_status = 'pending'` and Phase 3 treats normals as missing.
- [ ] **Step 2 (owner/agent): Re-run R4 and R5 with objectives** (plan 2 Task 13 Step 9, deferred here) — the spec's ski-approach radius counts objectives, which exist now (`ingest` has SELECT on `objectives`, Task 1):

```bash
ING r4
ING r4 --apply
ING r5 --apply
VERIFY tests/verify/test_phase2a_repair.py -q -k "r4 or r5"
```

Expected: the dry run prints `terrain_version: r4-v2` and `unmapped` empty; the apply writes the changed activity classes (record the count of rows that moved to `climbing_approach` in the PR); `r5 --apply` regroups under the new classes (rows that leave a group are reset, plan 2); the cells pass. Then `INGMOD app.pipelines.objectives links` and `INGMOD app.pipelines.coverage badges` again, because objective links and badges read `accidents_clean`.
- [ ] **Step 3 (owner): Curate** — for each of the top ~30 hotspots and every spec seed-list objective (Denali West Buttress/West Rib/Cassin, Foraker, Rainier DC/Emmons/Kautz/Liberty Ridge, Hood South Side, Shasta Avalanche Gulch/Casaval, Baker Coleman-Deming/Easton, Adams South Spur, Jefferson, North Sister, Thompson/Snoqualmie, Mt Washington Huntington/Tuckerman, Longs Peak, Whitney Mountaineers Route): add rows to `data/curated/objective_routes.csv` from NPS/USFS pages — facts plus your own ≤300-character summary; use `gnis:<id>` from `gnis_summits` (look up with `ANALYST_PSQL -XAt -c "SELECT gnis_id, name, state_code FROM gnis_summits WHERE name_key = 'rainier'"`). Add published permit/attempt counts (NPS Denali and Rainier statistics) to `objective_permit_counts.csv`. Commit both files on a docs branch and open a PR (owner pushes).
- [ ] **Step 4 (owner/agent): Load and verify** —

```bash
INGMOD app.pipelines.curated routes
INGMOD app.pipelines.curated permits
INGMOD app.pipelines.static_features routes
INGMOD app.pipelines.objectives links
INGMOD app.pipelines.coverage badges
VERIFY tests/verify/test_phase2b_objectives.py -q
```

Reports name row numbers only. All cells pass. Ice venues listed in the spec (Frankenstein, Cathedral/Whitehorse, Chapel Pond, Poke-O-Moonshine, Ouray, Kelso/Stevens Gulch, Vail, Hyalite) get a curated objective only if `coverage hotspots` still lists them.
- [ ] **Step 5 (owner): Prod** — `unset TARGET_HOST`; repeat Steps 1 (prod migration as plan 1 Task 9 Step 4 describes), 2 and 4 against prod. Delete the `p2b-2d-rehearsal` branch.

---

# PR 2b-2e — `feat/p2b-legacy-drop`

### Task 10: Migration `0012_drop_legacy_routes` — guarded legacy drop, code and test cleanup

Moved from plan 5 (review K1/D16): the drop must follow the R10 re-run on plan 6 data (Task 11), so it is numbered after `0011_objectives`.

**Files:**
- Create: `backend/alembic/versions/0012_drop_legacy_routes.py`, `backend/tests/test_migration_0012.py`, `backend/tests/verify/test_phase2b_legacy_drop.py`
- Modify: `backend/app/models/accident.py`, `backend/app/models/__init__.py`, `backend/alembic/env.py`, `backend/app/api/v1/accidents.py`, `backend/app/schemas/accident.py`, `backend/app/data/repair/__main__.py`, `backend/tests/test_migrations.py`, `backend/tests/test_ascent_analytics.py`, `backend/db/roles/grants_phase2.sql`, `backend/pyproject.toml`, `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`
- Delete: `backend/app/models/legacy.py`, `backend/app/data/repair/legacy_links.py`, `backend/tests/test_legacy_links.py`

**Interfaces:**
- Consumes: `internal.r10_unresolved(accident_id, legacy_route_id, legacy_mountain_id, best_candidate_id, best_score, reason, owner_decision, decided_route_id, decided_at, run_id)` (plan 5, `0010`), `internal.accidents_raw` (plan 1).
- Produces: revision `0012_drop_legacy_routes` (down_revision `0011_objectives`); plan 7's `0013_live_feeds` chains on it.

- [ ] **Step 1: Failing tests** — `backend/tests/test_migration_0012.py`:

```python
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
TARGET = "0012_drop_legacy_routes"
LEGACY = "INSERT INTO routes (route_id, name) VALUES (55, 'R'); INSERT INTO mountains (mountain_id, name) VALUES (7, 'M');"


def test_0012_refuses_while_a_legacy_link_is_unresolved():
    with migrated_db("0011_objectives") as name:
        run_sql(name, LEGACY + "INSERT INTO accidents (accident_id, route_id) VALUES (1, 55);")
        with pytest.raises(RuntimeError, match="1 formerly linked accidents"):
            command.upgrade(_alembic_cfg(name), TARGET)


def test_0012_refuses_undecided_unresolved_rows():
    with migrated_db("0011_objectives") as name:
        run_sql(name, LEGACY + "INSERT INTO accidents (accident_id, mountain_id) VALUES (1, 7);"
                      "INSERT INTO internal.r10_unresolved (accident_id, legacy_mountain_id, reason) VALUES (1, 7, 'no_candidate');")
        with pytest.raises(RuntimeError, match="1 formerly linked accidents"):
            command.upgrade(_alembic_cfg(name), TARGET)


def test_0012_refuses_when_only_accidents_raw_remembers_the_link():
    with migrated_db("0011_objectives") as name:
        run_sql(name, LEGACY + "INSERT INTO accidents (accident_id) VALUES (2);"
                      "INSERT INTO internal.accidents_raw (accident_id, route_id) VALUES (2, 55);")
        with pytest.raises(RuntimeError, match="1 formerly linked accidents"):
            command.upgrade(_alembic_cfg(name), TARGET)


def test_0012_drops_legacy_objects_when_every_link_is_resolved_or_decided():
    with migrated_db("0011_objectives") as name:
        run_sql(name, LEGACY
                + "INSERT INTO accidents (accident_id, route_id, mountain_id) VALUES (1, 55, NULL), (2, NULL, 7);"
                + "INSERT INTO canonical_areas (area_id, name, path, coord_precision, source, redistributable) VALUES "
                  "('00000000-0000-0000-0000-00000000000a', 'A', '0000000000000000000000000000000a', 'none', 'openbeta', true);"
                + "INSERT INTO accident_route_links (accident_id, canonical_area_id, method) "
                  "VALUES (1, '00000000-0000-0000-0000-00000000000a', 'legacy_area');"
                + "INSERT INTO internal.r10_unresolved (accident_id, legacy_mountain_id, reason, owner_decision, decided_at) "
                  "VALUES (2, 7, 'no_candidate', 'no_link', now());")
        cfg = _alembic_cfg(name)
        command.upgrade(cfg, TARGET)
        command.check(cfg)


def test_0012_is_one_way():
    with migrated_db(TARGET) as name:
        with pytest.raises(NotImplementedError):
            command.downgrade(_alembic_cfg(name), "0011_objectives")
```

In `test_migrations.py`, rename `test_head_drops_ascents_and_climbers_but_keeps_legacy_tables` to `test_0011_still_has_legacy_tables` and upgrade to `"0011_objectives"` instead of `"head"` (its assertions stay). In `test_ascent_analytics.py`: renumber the fixture ids to synthetic ids ≥ 900000000 (`ROUTE = 900000111`, `OTHER_ROUTE = 900000222`, `THIRD_ROUTE = 900000333`, `FOURTH_ROUTE = 900000444`, and the `mp_locations` id `10` → `900000010` in both places it appears); remove the `INSERT INTO routes …` line and the `route_id` column (and its values) from the first accident insert in `SEED_SQL`, which becomes `INSERT INTO accidents (accident_id, date, mp_route_id) VALUES (1, '2020-01-05', {OTHER_ROUTE}), (2, '2019-07-10', {ROUTE}), (4, '2008-03-02', {ROUTE}), (5, '3901-01-20', {ROUTE}), (3, NULL, {ROUTE});`; delete the comment above `SEED_SQL` about the legacy mislink, `test_accidents_count_by_mp_route_id_not_legacy_route_id` and `test_seed_really_has_the_colliding_legacy_link` (D16); `test_accident_list_filters_by_mp_route_id_not_legacy_route_id` keeps only its `mp_route_id` assertions. Add:

```python
def test_mountain_id_filter_is_rejected(seeded_db):
    body = asyncio.run(_get(seeded_db, "/api/v1/accidents", expect=422, mountain_id=900000007))
    assert "mp_route_id" in body["detail"]
```

`backend/tests/verify/test_phase2b_legacy_drop.py`:

```python
import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db



def test_no_legacy_table_or_fk_remains():
    [row] = fetch("SELECT to_regclass('public.routes') AS r, to_regclass('public.mountains') AS m, "
                  "(SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid = c.confrelid "
                  " WHERE t.relname IN ('routes', 'mountains') AND t.relnamespace = 'public'::regnamespace) AS fks")
    assert (row["r"], row["m"], row["fks"]) == (None, None, 0)


def test_every_formerly_linked_accident_is_linked_or_decided():
    [row] = fetch("SELECT count(*) AS n FROM internal.accidents_raw r WHERE (r.route_id IS NOT NULL OR r.mountain_id IS NOT NULL) "
                  "AND NOT EXISTS (SELECT 1 FROM accident_route_links l WHERE l.accident_id = r.accident_id) "
                  "AND NOT EXISTS (SELECT 1 FROM internal.r10_unresolved u WHERE u.accident_id = r.accident_id "
                  "AND u.owner_decision IS NOT NULL)")
    assert row["n"] == 0
```

(Runs as `analyst`, which holds USAGE and SELECT on schema `internal` per plan 1's grants.)

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_migration_0012.py -q` → FAIL (no revision).

- [ ] **Step 3: Implement** `backend/alembic/versions/0012_drop_legacy_routes.py`:

```python
"""R10 (spec 2a/2b-2): drop the legacy accident FKs and columns and the routes/mountains
tables. Refuses while any formerly linked accident has neither an accident_route_links row
nor an owner decision in internal.r10_unresolved; "formerly linked" also reads the frozen
internal.accidents_raw copy, so a link R10 already nulled in the live table still counts."""

from alembic import op

revision = "0012_drop_legacy_routes"
down_revision = "0011_objectives"
branch_labels = None
depends_on = None

UNRESOLVED = """
SELECT count(*) FROM (
  SELECT accident_id FROM accidents WHERE route_id IS NOT NULL OR mountain_id IS NOT NULL
  UNION
  SELECT accident_id FROM internal.accidents_raw WHERE route_id IS NOT NULL OR mountain_id IS NOT NULL
) f
WHERE NOT EXISTS (SELECT 1 FROM accident_route_links l WHERE l.accident_id = f.accident_id)
  AND NOT EXISTS (SELECT 1 FROM internal.r10_unresolved u WHERE u.accident_id = f.accident_id AND u.owner_decision IS NOT NULL)
"""


def upgrade() -> None:
    remaining = op.get_bind().exec_driver_sql(UNRESOLVED).scalar_one()
    if remaining:
        raise RuntimeError(
            f"refusing 0012: {remaining} formerly linked accidents have neither an accident_route_links row nor an owner "
            "decision in internal.r10_unresolved; re-run R10 and record the owner's decisions first")
    op.drop_constraint("accidents_route_id_fkey", "accidents", type_="foreignkey")
    op.drop_constraint("accidents_mountain_id_fkey", "accidents", type_="foreignkey")
    op.drop_index("idx_accidents_route", "accidents")
    op.drop_index("idx_accidents_mountain", "accidents")
    op.drop_column("accidents", "route_id")
    op.drop_column("accidents", "mountain_id")
    op.drop_table("routes")
    op.drop_table("mountains")


def downgrade() -> None:
    raise NotImplementedError("0012 is one-way: restore routes/mountains from the pre-drop dump if ever needed")
```

`internal.accidents_raw` (plan 1) and `internal.r10_unresolved` keep the original legacy values in-database; the pre-drop dump (Task 11 Step 1) keeps the tables.

Code:
- Delete `backend/app/models/legacy.py`; remove its import from `app/models/__init__.py`; in `alembic/env.py` remove the `UNMANAGED_LEGACY_TABLES` import and its check in `include_object`.
- `app/models/accident.py`: delete `mountain_id`, `route_id`, their `ForeignKey`s and the two indexes from `__table_args__`.
- `app/schemas/accident.py`: delete `mountain_id` and `route_id` from `AccidentResponse`.
- `app/api/v1/accidents.py`: make `mountain_id` the same reject-only parameter as `route_id` — `mountain_id: Optional[int] = Query(None, include_in_schema=False)` and, beside the `route_id` check, `if mountain_id is not None: raise HTTPException(status_code=422, detail="mountain_id is not supported; use mp_route_id")`; delete the `Accident.mountain_id` filter.
- R10 was a one-shot that already ran (Task 11 Steps 2–3 run it from `main` before this PR deploys): delete `backend/app/data/repair/legacy_links.py` (the `r10` step and its `export|import` CLI), `backend/tests/test_legacy_links.py`, the `r10` registration in `app/data/repair/__main__.py`, and `"app.data.repair.legacy_links"` from the mypy block. `framework.py` is untouched (R10 never writes the legacy columns, plan 5). `internal.r10_unresolved` stays as the audit record.
- `grants_phase2.sql`: delete exactly the Plan 5 (R10) line `GRANT SELECT ON public.routes, public.mountains TO ingest;` (plan 5 grants no UPDATE on `accidents`; the tables no longer exist and the file must stay runnable). Keep the `internal.r10_unresolved` grants.
- Docs: CHANGELOG `### R10 legacy cleanup (PR 2b-2e) — YYYY-MM-DD` (UTC day the entry is written, `date -u +%F`); CLAUDE.md drops the "legacy `routes`/`mountains` tables … stay until Phase 2a" line; DEPLOYMENT.md revisions list gains `0011_objectives`, `0012_drop_legacy_routes`; DATABASE_STRUCTURE.md removes the legacy FK note and documents `internal.r10_unresolved` as the decision record.

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd ../frontend && npm run test:run` → green (the frontend never read the removed fields; the run proves it).
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0012_drop_legacy_routes.py backend/tests/test_migration_0012.py backend/tests/verify/test_phase2b_legacy_drop.py backend/app backend/tests backend/alembic/env.py backend/db/roles/grants_phase2.sql backend/pyproject.toml CHANGELOG.md CLAUDE.md DEPLOYMENT.md data/DATABASE_STRUCTURE.md && git rm backend/app/models/legacy.py backend/app/data/repair/legacy_links.py backend/tests/test_legacy_links.py && git commit -m "feat(db): 0012 drops legacy routes/mountains once every legacy link is resolved or decided"`

---

### Task 11: OWNER/AGENT RUNBOOK — R10 re-run, owner decisions, legacy drop (PR 2b-2e)

Order matters: R10 runs from `main` (which still has the job) **before** PR 2b-2e deploys. Helpers: `. scripts/runbook_helpers.sh` as in Task 9 (`TARGET_HOST` set for the branch, unset for prod).

- [ ] **Step 1 (owner/agent): Dump the legacy tables** — like plan 1 Task 8 Step 3 (owner, `split_pg_url` + `verify_full_url`, prod host), for `public.routes`, `public.mountains` and `internal.r10_unresolved` into `~/Developer/safeascent-private/backups/pre-0012/`.
- [ ] **Step 2 (owner/agent): Re-run R10 on plan 6 data (branch)** — new branch `p2b-2e-rehearsal` of prod (plan 6 data loaded by Task 9), `TARGET_HOST='<p2b-2e-rehearsal direct host>'`, working tree on `main`:

```bash
ING r10
ING r10 --apply
ANALYST_PSQL -XAt -c "SELECT reason, count(*) FROM internal.r10_unresolved GROUP BY 1 ORDER BY 1"
```

Expected: the dry run prints `links` by method and `unresolved` by reason; `--apply` writes new links (legacy mountains now resolve to objectives, `legacy_mountain_objective`) and refreshes `internal.r10_unresolved`; the legacy columns are untouched (R10 never writes them). With objectives loaded, **no row may still read `awaiting_objectives`** — if the query lists any, stop: plan 6's objectives did not load. Record method and reason counts in the PR (spec: 421 direct, 762 via matcher).
- [ ] **Step 3 (owner): Decide every unresolved row** — with plan 5's review round-trip (the CSV stays under the gitignored `data/review/`, D12; objective candidates show their names, Task 5 Step 3):

```bash
INGMOD app.data.repair.legacy_links export ../data/review/r10_unresolved.csv
# owner fills owner_decision (link/no_link) and, for link, decided_id (and candidate_kind if linking another kind)
INGMOD app.data.repair.legacy_links import ../data/review/r10_unresolved.csv
ING r10 --apply
ANALYST_PSQL -XAt -c "SELECT count(*) FROM internal.r10_unresolved WHERE owner_decision IS NULL"
```

Expected: import prints `rejected: 0`; the second `--apply` writes each `link` decision as an `owner_r10` link; a `no_link` accident keeps its own coordinates and still counts spatially; the last query prints `0`.
- [ ] **Step 4 (owner/agent): Rehearse the drop on the branch** — check out PR 2b-2e, migrate to `0012_drop_legacy_routes` as `migrator` (plan 1 Task 9 Step 2 pattern), `uv run alembic check`, then `OWNER_PSQL -q -f db/roles/grants_phase2.sql` (post-0012 file), `OWNER_PSQL -q -f db/roles/verify_roles_phase2.sql`, `VERIFY tests/verify/test_phase2b_legacy_drop.py -q` → pass. Delete the branch.
- [ ] **Step 5 (owner): Prod** — `unset TARGET_HOST`; Step 1 dump; Steps 2–3 against prod; then deploy order: merge PR 2b-2e → Railway deploys the backend that no longer reads the legacy columns → `alembic upgrade 0012_drop_legacy_routes` as `migrator` → `OWNER_PSQL -q -f db/roles/grants_phase2.sql` → both verify scripts → `VERIFY tests/verify/test_phase2b_legacy_drop.py -q` (spec: "No FK points at a legacy table"). If `0012` refuses, it names the count: return to Step 3; never edit the guard.

---

## Self-review

- Spec coverage: objectives schema and seeding (GNIS, Wikidata, OpenBeta ice areas), curated CC0 standard routes as `canonical_routes` (`safeascent_curated`), `route_objective_links`, `accident_route_links.objective_id` (activity-filtered), permit counts, coverage badges with rule version and reason (lightning coverage is read from plan 7's `glm_satellite`, not stored), hotspot ranking and top-30 curation, milestone 2b-2 acceptance "every seed-list objective has ≥1 curated route and a coverage badge" (Tasks 1–7, 9); 2b-2 "legacy tables dropped, no FK points at a legacy table" and R10 (Tasks 10–11, moved from plan 5). Objective feature rows for Phase 3 (S5) in `scoring_unit_features`. OSM and the OpenBeta contribution are optional and excluded (Global Constraints).
- Runbooks: plan 2's helpers used verbatim; R4/R5 re-run with objectives (plan 2's deferred step) in Task 9 Step 2.
- Contract: chain `0010_static_features → 0011_objectives → 0012_drop_legacy_routes → 0013_live_feeds`; `r10_unresolved` refusal per contract §6; objectives carry `point_key`/`tz` and register `grid_bucket_series`/`cell_normals_status` rows (§2–3); no `trainer` grants (§5); DP4 applied and marked.
- Placeholders: none beyond Console hosts, analyst URLs the owner supplies, owner-curated CSV rows, and CHANGELOG dates taken from `date -u +%F` when written.
- Types: `objective_id_for`, `objective_area_id`, `point_fields`, `objective_eligible_sql`, `TERMS`, `badge_for`/`badge_with_reason`, `ensure_objective_areas -> dict[str, int]`, `seed_all`, `link_accidents -> dict[str, int]`, `link_all` match their callers in Tasks 2–7; `scoring_unit_features` column list matches between `ROUTE_UNITS`, `OBJECTIVE_UNITS` and the downgrade.
