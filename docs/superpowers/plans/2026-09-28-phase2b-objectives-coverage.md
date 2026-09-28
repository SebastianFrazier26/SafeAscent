# Phase 2b Objectives and Coverage (PR 2b-2d) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give mountaineering and glaciated terrain a place to attach a score where no route-level data exists: an Objective layer seeded from USGS GNIS summits (cross-linked to Wikidata), OpenBeta ice areas, and hand-curated CC0 standard routes for the top ~30 hotspots; link routes and accidents to objectives; compute versioned coverage badges (route-level, objective-level, thin data) so thin coverage is shown as insufficient, never as a low score.

**Architecture:** Migration `0012` adds `objectives`, `objective_routes`, `route_objective_links`, `objective_permit_counts`, `coverage_badges`, and wires the `objective_id` columns already reserved on `canonical_areas` and `accident_route_links`. Seeding jobs (`objectives.py`, `wikidata.py`) are open-API/public-file loaders. Curated routes and permit counts are committed CSVs in our own words (CC0) loaded by strict loaders. `coverage.py` holds the pure badge rule and the SQL job. Every objective gets a `canonical_areas` row (source `safeascent_curated`), so plan 5's feature job gives it elevation and normals automatically.

**Tech Stack:** Python 3.12, httpx, pydantic 2, SQLAlchemy async, PostGIS, Alembic.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` §Coverage strategy and objectives (P2-2), schema bullets, milestone 2b-2 (objectives part); P3 Objective-level scoring (P3:163–169). Phase 3 creates `objective_daily_scores` (P3 MVP-1), not this plan.

**Prerequisites:** Plans 2 (GNIS summits, `accidents_clean`), 4 (catalog), 5 (feature points) merged and applied.

**One PR:** `feat/p2b-objectives` = Tasks 1–8, runbook Task 9.

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- Objectives and curated routes are our own CC0 records in our own words; nothing is copied from MP (never MP text) or pasted from NPS/USFS pages beyond facts (name, grade, season, URL).
- `mp_facts` rows are never pushed to OpenBeta; the optional contribution (2b-6) is out of scope.
- OSM layers are not built (optional in the spec, legal Q5 open).
- Coverage is recomputed on every catalog or objective change; a thin-data badge must never be converted into a numeric score downstream.
- Lightning coverage flag: points north of 54°N are `none`.

## Decisions this plan makes where the spec is silent (owner may overrule)

1. **GNIS seeding scope:** a summit becomes an objective when it lies within 5 km of at least one clean accident (any activity) or appears in the curated CSV; seeding all ~70K GNIS summits would create objectives no score can use.
2. **OpenBeta ice-area objectives:** OpenBeta areas with ≥3 ice/mixed routes attached directly (OpenBeta or `mp_facts`), or whose name contains "ice" with ≥1.
3. **Badge precedence:** route-level (≥5 scorable routes of the type group) → thin (≥1 clean accident within 5 km and no curated objective route) → objective-level (an objective exists) → route-level for 1–4 routes with no nearby accident and no objective (the spec's three badges leave this case unnamed; Phase 3's confidence handles the thinness) → no badge when there are no routes, no objective and no accident.
4. **Accident → objective links:** only for clean accidents with no route or area link, within 3 km of the objective point (`method = 'objective_near'`).
5. **Wikidata:** items matched by GNIS Feature ID (property P590); elevation from P2044 (normalized metres) is a cross-check against the DEM, reported when |Δ| > 50 m, never copied over the DEM value.

## Review Focus

1. **An objective with accidents nearby but no curated route** — expect `thin`, never `objective` or a score (Task 6 `test_thin_beats_objective_without_curated_route`).
2. **A summit name shared by two states in the curated CSV** — expect the loader to require `gnis_id` or state and reject an ambiguous row (Task 4 `test_ambiguous_objective_reference_is_rejected`).
3. **Wikidata returning two items for one GNIS id** — expect no QID stored and a quarantine entry, not an arbitrary pick (Task 3 `test_duplicate_items_are_quarantined`).
4. **An objective in Alaska** — expect `lightning_coverage = 'none'` on every badge row (Task 6 `test_north_of_54_has_no_lightning_coverage`).
5. **Re-seeding after new accidents** — expect existing objectives kept (stable ids) and only new ones added (Task 2 `test_reseeding_is_stable`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/alembic/versions/0012_objectives.py` | Create | Objective tables, badges, FKs. |
| `backend/app/models/objectives.py` | Create | Models. |
| `backend/app/pipelines/objectives.py` | Create | GNIS seeding, ice-area objectives, objective area rows, links. |
| `backend/app/pipelines/wikidata.py` | Create | SPARQL client (P590 → QID, sitelinks, P2044). |
| `backend/app/pipelines/curated.py` | Create | Curated objective-route and permit-count CSV loaders. |
| `backend/app/pipelines/coverage.py` | Create | Badge rule (pure) + badge job + hotspot report. |
| `data/curated/README.md`, `data/curated/objective_routes.csv`, `data/curated/objective_permit_counts.csv` | Create | Our CC0 curated records (headers now; rows by the owner). |
| tests: `test_migration_0012.py`, `test_objectives.py`, `test_wikidata.py`, `test_curated.py`, `test_coverage.py` | Create | Tests. |
| `backend/tests/verify/test_phase2b_objectives.py` | Create | `-m db` acceptance. |
| grants/verify SQL, `backend/pyproject.toml`, docs | Modify | Grants, mypy, docs. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 2–7 | objective tables (0012) | Task 1 first. |
| 2 | 4, 5, 6 | `objectives.objective_area_id(objective_id)`; one `canonical_areas` row per objective | Frozen in Task 2. |
| 3 | 6, plan 8 | `objectives.wikidata_qid`, `objectives.sitelinks` | Plan 8 reads `sitelinks` for `objective_popularity`. |
| 4 | 6, plan 8 | `objective_routes`, `objective_permit_counts` | Plan 8 reads permit counts for `permit_attempts`. |
| 6 | Phase 3, plan 8 | `coverage.badge_for`, `coverage_badges` table | Frozen here. |
| all | each other | grants/verify SQL, `pyproject.toml` | Serial. |

---

### Task 1: Migration `0012` and models

**Files:**
- Create: `backend/alembic/versions/0012_objectives.py`, `backend/app/models/objectives.py`, `backend/tests/test_migration_0012.py`
- Modify: `backend/app/models/__init__.py`, `backend/app/models/catalog.py` (FK on `objective_id`), `backend/pyproject.toml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Produces (DB):
  - `objectives(objective_id uuid PK, kind text CHECK IN ('peak','ice_area','glacier','formation'), name text NOT NULL, lat double precision NOT NULL, lon double precision NOT NULL, geom geography(Point,4326) NOT NULL, elevation_m real, gnis_id integer UNIQUE NULL, wikidata_qid text NULL, sitelinks integer NULL, ob_area_uuid uuid UNIQUE NULL, disciplines text[] NOT NULL, coverage_level text CHECK IN ('route','objective','thin'), coverage_rule_version text, source text CHECK IN ('gnis','wikidata','openbeta','curated'), license text CHECK IN ('public_domain','CC0'), curated_by text, updated_at timestamptz NOT NULL DEFAULT now())`, GiST on `geom`.
  - `objective_routes(objective_route_id uuid PK, objective_id uuid NOT NULL REFERENCES objectives, route_id uuid UNIQUE REFERENCES canonical_routes, name text NOT NULL, type_group text NOT NULL CHECK IN ('alpine','ice','mixed','trad','sport'), grade_text text, season_months smallint[], summary text CHECK (length(summary) <= 300), source_url text NOT NULL, source_kind text CHECK IN ('nps','usfs','owner'), license text NOT NULL DEFAULT 'CC0', curated_at timestamptz NOT NULL DEFAULT now())`.
  - `route_objective_links(route_id uuid REFERENCES canonical_routes, objective_id uuid REFERENCES objectives, relation text CHECK IN ('on','approach_via','near'), method text NOT NULL, score real, PK (route_id, objective_id))`.
  - `objective_permit_counts(objective_id uuid REFERENCES objectives, year smallint NOT NULL, month smallint NOT NULL CHECK 0..12 /* 0 = whole-year total */, attempts integer CHECK >= 0, summits integer CHECK >= 0, source_url text NOT NULL, PK (objective_id, year, month))`.
  - `coverage_badges(scope_kind text CHECK IN ('area','objective'), scope_id uuid, type_group text CHECK IN ('sport','trad','alpine','ice','mixed'), level text NOT NULL CHECK IN ('route','objective','thin'), n_routes integer NOT NULL, n_clean_accidents_5km integer NOT NULL, has_curated_route boolean NOT NULL, lightning_coverage text NOT NULL CHECK IN ('satellite','none'), coverage_rule_version text NOT NULL, computed_at timestamptz NOT NULL DEFAULT now(), PK (scope_kind, scope_id, type_group))`.
  - FKs: `canonical_areas.objective_id → objectives`, `accident_route_links.objective_id → objectives`.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0012.py`:

```python
import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
OBJ = ("INSERT INTO objectives (objective_id, kind, name, lat, lon, geom, disciplines, source, license) VALUES "
       "('00000000-0000-0000-0000-0000000000f1', 'peak', 'Fixture Peak', 46.85, -121.76, "
       "ST_SetSRID(ST_MakePoint(-121.76, 46.85), 4326)::geography, '{alpine}', 'gnis', 'public_domain');")


def test_0012_checks_clean_and_constraints():
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
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/alembic/versions/0012_objectives.py`:

```python
"""Objective layer (P2-2) and versioned coverage badges."""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision = "0012_objectives"
down_revision = "0011_drop_legacy_routes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "objectives",
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("geom", Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
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
        sa.CheckConstraint("coverage_level IS NULL OR coverage_level IN ('route', 'objective', 'thin')", name="objectives_coverage_check"),
        sa.CheckConstraint("source IN ('gnis', 'wikidata', 'openbeta', 'curated')", name="objectives_source_check"),
        sa.CheckConstraint("license IN ('public_domain', 'CC0')", name="objectives_license_check"),
    )
    op.create_index("ix_objectives_geom", "objectives", ["geom"], postgresql_using="gist")
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
        sa.Column("n_routes", sa.Integer(), nullable=False),
        sa.Column("n_clean_accidents_5km", sa.Integer(), nullable=False),
        sa.Column("has_curated_route", sa.Boolean(), nullable=False),
        sa.Column("lightning_coverage", sa.Text(), nullable=False),
        sa.Column("coverage_rule_version", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("scope_kind", "scope_id", "type_group"),
        sa.CheckConstraint("scope_kind IN ('area', 'objective')", name="coverage_badges_scope_check"),
        sa.CheckConstraint("type_group IN ('sport', 'trad', 'alpine', 'ice', 'mixed')", name="coverage_badges_type_group_check"),
        sa.CheckConstraint("level IN ('route', 'objective', 'thin')", name="coverage_badges_level_check"),
        sa.CheckConstraint("lightning_coverage IN ('satellite', 'none')", name="coverage_badges_lightning_check"),
    )
    op.create_foreign_key("canonical_areas_objective_fkey", "canonical_areas", "objectives", ["objective_id"], ["objective_id"])
    op.create_foreign_key("accident_route_links_objective_fkey", "accident_route_links", "objectives", ["objective_id"], ["objective_id"])


def downgrade() -> None:
    n = op.get_bind().exec_driver_sql("SELECT count(*) FROM objective_routes").scalar_one()
    if n:
        raise RuntimeError(f"refusing to downgrade 0012: {n} curated objective routes")
    op.drop_constraint("accident_route_links_objective_fkey", "accident_route_links", type_="foreignkey")
    op.drop_constraint("canonical_areas_objective_fkey", "canonical_areas", type_="foreignkey")
    for table in ("coverage_badges", "objective_permit_counts", "route_objective_links", "objective_routes"):
        op.drop_table(table)
    op.drop_index("ix_objectives_geom", "objectives")
    op.drop_table("objectives")
```

`backend/app/models/objectives.py`: `Objective`, `ObjectiveRoute`, `RouteObjectiveLink`, `ObjectivePermitCount` (composite primary key `objective_id, year, month`; month 0 is a whole-year total), `CoverageBadge`, mirroring the migration's columns and index; in `app/models/catalog.py` change `CanonicalArea.objective_id` and `AccidentRouteLink.objective_id` to `ForeignKey("objectives.objective_id")`. Register in `__init__.py` and the models mypy block.

Grants ("Plan 6 (0012)"):

```sql
GRANT SELECT, INSERT, UPDATE ON public.objectives, public.objective_routes, public.objective_permit_counts TO ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.route_objective_links, public.coverage_badges TO ingest;
GRANT SELECT ON public.objectives, public.objective_routes, public.route_objective_links,
               public.objective_permit_counts, public.coverage_badges TO trainer;
```

`verify_roles_phase2.sql` `ingest_writes`: the matching INSERT/UPDATE(/DELETE) rows.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_migration_0012.py tests/test_migrations.py tests/test_roles_phase2.py -q && uv run mypy` → PASS.
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0012_objectives.py backend/app/models/ backend/tests/test_migration_0012.py backend/pyproject.toml backend/db/roles/ && git commit -m "feat(db): 0012 objectives, curated routes, permit counts, coverage badges"`

---

### Task 2: GNIS seeding, objective area rows, OpenBeta ice-area objectives

**Files:**
- Create: `backend/app/pipelines/objectives.py`, `backend/tests/test_objectives.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `SEED_RADIUS_M = 5000`, `objective_id_for(kind: str, key: str) -> uuid.UUID` (uuid5; stable across reseeds), `objective_area_id(objective_id: uuid.UUID) -> uuid.UUID`, `async seed_gnis(conn) -> int`, `async seed_ice_areas(conn) -> int`, `async ensure_objective_areas(conn) -> int` (one `canonical_areas` row per objective, `source='safeascent_curated'`, parent = nearest OpenBeta area within 5 km or NULL), CLI `python -m app.pipelines.objectives seed`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_objectives.py`:

```python
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.objectives import ensure_objective_areas, objective_id_for, seed_gnis, seed_ice_areas
from tests.pgtest import migrated_db, requires_pg, sa_url

SEED = """
INSERT INTO gnis_summits (gnis_id, name, name_key, state_code, lat, lon) VALUES
  (1, 'Fixture Peak', 'fixture', 'WA', 46.853, -121.760), (2, 'Far Peak', 'far', 'WA', 48.0, -120.0);
INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, date_precision)
  VALUES (1, 'AAC', '2010-07-02', 46.86, -121.75, true, 'climbing', 'US', 'day');
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
def test_seeds_only_summits_near_clean_accidents_and_ice_areas():
    with migrated_db(seed_sql=SEED) as name:
        assert _run(name, seed_gnis) == 1
        assert _run(name, seed_ice_areas) == 1
        assert _run(name, ensure_objective_areas) == 2
        rows = _run(name, lambda c: c.execute(text("SELECT kind, name, source FROM objectives ORDER BY kind")))
        assert [tuple(r) for r in rows] == [("ice_area", "Fixture Ice Park", "openbeta"), ("peak", "Fixture Peak", "gnis")]


@requires_pg
def test_reseeding_is_stable():
    with migrated_db(seed_sql=SEED) as name:
        _run(name, seed_gnis)
        _run(name, seed_gnis)
        ids = _run(name, lambda c: c.execute(text("SELECT objective_id FROM objectives WHERE gnis_id = 1")))
        assert [r[0] for r in ids] == [objective_id_for("peak", "gnis:1")]
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/objectives.py`:

```python
"""Objective seeding (P2-2): GNIS summits near clean accidents, OpenBeta ice areas, and a
canonical_areas row per objective so feature and scoring jobs treat it like any area."""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.catalog import ltree_label

SEED_RADIUS_M = 5000
_NS = uuid.UUID("7c1e9a52-2f0b-4b5e-9c9e-2d8f0f6a1b33")


def objective_id_for(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"objective:{kind}:{key}")


def objective_area_id(objective_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(_NS, f"objective-area:{objective_id}")


async def seed_gnis(conn: AsyncConnection) -> int:
    rows = (await conn.execute(text(
        "SELECT g.gnis_id, g.name, g.lat, g.lon FROM gnis_summits g WHERE EXISTS ("
        " SELECT 1 FROM accidents_clean a WHERE a.coordinates IS NOT NULL AND "
        " ST_DWithin(a.coordinates, ST_SetSRID(ST_MakePoint(g.lon, g.lat), 4326)::geography, :r))"),
        {"r": SEED_RADIUS_M})).all()
    params = [{"id": objective_id_for("peak", f"gnis:{g}"), "g": int(g), "n": str(n), "lat": float(la), "lon": float(lo)}
              for g, n, la, lo in rows]
    if params:
        await conn.execute(text(
            "INSERT INTO objectives (objective_id, kind, name, lat, lon, geom, gnis_id, disciplines, source, license) "
            "VALUES (:id, 'peak', :n, :lat, :lon, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :g, "
            "ARRAY['alpine'], 'gnis', 'public_domain') ON CONFLICT (objective_id) DO NOTHING"), params)
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
               "d": sorted(g)} for a, n, la, lo, g in rows]
    if params:
        await conn.execute(text(
            "INSERT INTO objectives (objective_id, kind, name, lat, lon, geom, ob_area_uuid, disciplines, source, license) "
            "VALUES (:id, 'ice_area', :n, :lat, :lon, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :ob, :d, "
            "'openbeta', 'CC0') ON CONFLICT (objective_id) DO UPDATE SET disciplines = EXCLUDED.disciplines, updated_at = now()"),
            params)
    return len(params)


async def ensure_objective_areas(conn: AsyncConnection) -> int:
    objectives = (await conn.execute(text(
        "SELECT o.objective_id, o.name, o.lat, o.lon, "
        " (SELECT a.area_id FROM canonical_areas a WHERE a.source = 'openbeta' AND a.geom IS NOT NULL AND a.retired_at IS NULL "
        "  AND ST_DWithin(a.geom, o.geom, 5000) ORDER BY ST_Distance(a.geom, o.geom) LIMIT 1) AS parent, "
        " (SELECT a.path::text FROM canonical_areas a WHERE a.source = 'openbeta' AND a.geom IS NOT NULL AND a.retired_at IS NULL "
        "  AND ST_DWithin(a.geom, o.geom, 5000) ORDER BY ST_Distance(a.geom, o.geom) LIMIT 1) AS parent_path "
        "FROM objectives o"))).all()
    params = []
    for oid, name, lat, lon, parent, parent_path in objectives:
        area_id = objective_area_id(uuid.UUID(str(oid)))
        path = f"{parent_path}.{ltree_label(area_id)}" if parent_path else ltree_label(area_id)
        params.append({"a": area_id, "n": str(name), "p": parent, "path": path, "lat": float(lat), "lon": float(lon), "o": oid})
    if params:
        await conn.execute(text(
            "INSERT INTO canonical_areas (area_id, name, parent_id, path, lat, lon, geom, objective_id, coord_precision, "
            "source, redistributable) VALUES (:a, :n, :p, CAST(:path AS ltree), :lat, :lon, "
            "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :o, 'area_centroid', 'safeascent_curated', true) "
            "ON CONFLICT (area_id) DO UPDATE SET name = EXCLUDED.name, parent_id = EXCLUDED.parent_id, path = EXCLUDED.path, "
            "updated_at = now()"), params)
    return len(params)


async def _main() -> dict[str, int]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            return {"gnis": await seed_gnis(conn), "ice_areas": await seed_ice_areas(conn),
                    "areas": await ensure_objective_areas(conn)}
    finally:
        await engine.dispose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_main()), sort_keys=True))
```

Grants: `ingest` already has SELECT on `accidents_clean` (plan 3) and INSERT/UPDATE on `canonical_areas` (plan 4). Append `"app.pipelines.objectives"` to strict mypy.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/objectives.py backend/tests/test_objectives.py backend/pyproject.toml && git commit -m "feat(pipelines): seed objectives from GNIS summits near accidents and OpenBeta ice areas"`

---

### Task 3: Wikidata cross-links

**Files:**
- Create: `backend/app/pipelines/wikidata.py`, `backend/tests/test_wikidata.py`
- Modify: `backend/app/pipelines/objectives.py` (CLI step `wikidata`), `backend/pyproject.toml`

**Interfaces:**
- Produces: `WDQS_URL = "https://query.wikidata.org/sparql"`, `USER_AGENT = "SafeAscent-data/1.0 (https://github.com/SebastianFrazier26/SafeAscent)"`, `@dataclass(frozen=True) WikidataHit(gnis_id: int, qid: str, sitelinks: int | None, elevation_m: float | None)`, `class WikidataClient(transport=None, batch=200)` with `lookup(gnis_ids: Sequence[int], report: ValidationReport) -> list[WikidataHit]`, `async link_objectives(conn, client) -> dict[str, int]` (sets `wikidata_qid`, `sitelinks`; reports `elevation_mismatch_50m` count against `feature_points` elevation at the objective point).

- [ ] **Step 1: Verify the property (agent)** — `curl -s -G https://query.wikidata.org/sparql -H 'Accept: application/sparql-results+json' -H "User-Agent: SafeAscent-data/1.0 (https://github.com/SebastianFrazier26/SafeAscent)" --data-urlencode 'query=SELECT ?item WHERE { ?item wdt:P31 wd:Q8502 ; wdt:P590 ?g } LIMIT 1'` returns one binding (P590 = GNIS Feature ID). If not, find the property via `https://www.wikidata.org/wiki/Special:Search?search=haswbstatement:P590` and update `QUERY`.

- [ ] **Step 2: Failing tests** — `backend/tests/test_wikidata.py`:

```python
import httpx

from app.pipelines.validate import ValidationReport
from app.pipelines.wikidata import WikidataClient

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
```

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
        "SELECT o.gnis_id, p.elevation_m FROM objectives o LEFT JOIN feature_points p "
        "ON p.point_key = to_char(o.lat, 'FM990.00000') || ':' || to_char(o.lon, 'FM9990.00000') WHERE o.gnis_id IS NOT NULL"))).all()
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

Add a CLI step `wikidata` to `objectives._main` (argument `step` in `seed|wikidata`). Append `"app.pipelines.wikidata"` to strict mypy.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git add backend/app/pipelines/wikidata.py backend/app/pipelines/objectives.py backend/tests/test_wikidata.py backend/pyproject.toml && git commit -m "feat(pipelines): Wikidata QID, sitelinks and elevation cross-check for objectives"`

---

### Task 4: Curated objective routes and permit counts (CC0, our words)

**Files:**
- Create: `backend/app/pipelines/curated.py`, `backend/tests/test_curated.py`, `data/curated/README.md`, `data/curated/objective_routes.csv`, `data/curated/objective_permit_counts.csv`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: CSV headers `OBJECTIVE_ROUTE_COLUMNS = ("objective_ref", "objective_name", "state", "lat", "lon", "kind", "route_name", "type_group", "grade_text", "season_months", "summary", "source_url", "source_kind")` where `objective_ref` is `gnis:<id>` or `curated:<slug>`; `PERMIT_COLUMNS = ("objective_ref", "year", "month", "attempts", "summits", "source_url")`; `async load_objective_routes(conn, path: Path, report) -> int`, `async load_permit_counts(conn, path: Path, report, today: date) -> int`; CLI `python -m app.pipelines.curated routes|permits`.

Behaviour: a `gnis:<id>` ref must exist in `gnis_summits` (the objective is created if not seeded yet); a `curated:<slug>` ref needs `lat`, `lon`, `state`, `kind` and creates a `source='curated'`, `license='CC0'` objective with id `objective_id_for(kind, ref)`. Each route row upserts `objective_routes` and a `canonical_routes` row (`source='safeascent_curated'`, `redistributable=true`, `type_group` from the CSV, `scored=true`, area = the objective's area), and a `route_objective_links` row (`relation='on'`, `method='curated'`). `summary` ≤300 characters. A year after the current year (or the current year with a month after the current month) in permit counts is quarantined `future`.

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
            "SELECT r.source, r.type_group, l.relation, o.kind FROM canonical_routes r "
            "JOIN route_objective_links l USING (route_id) JOIN objectives o USING (objective_id) ORDER BY o.kind")))
        assert [tuple(r) for r in rows] == [("safeascent_curated", "ice", "on", "ice_area"), ("safeascent_curated", "alpine", "on", "peak")]


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

from app.pipelines.objectives import ensure_objective_areas, objective_area_id, objective_id_for
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
    state: str | None = None
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
        g = (await conn.execute(text("SELECT name, lat, lon FROM gnis_summits WHERE gnis_id = :g"), {"g": gid})).first()
        if g is None:
            report.quarantine(ref, "unknown_gnis")
            return None
        oid = objective_id_for("peak", f"gnis:{gid}")
        await conn.execute(text(
            "INSERT INTO objectives (objective_id, kind, name, lat, lon, geom, gnis_id, disciplines, source, license) "
            "VALUES (:o, :k, :n, :lat, :lon, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :g, ARRAY[:t], 'gnis', "
            "'public_domain') ON CONFLICT (objective_id) DO UPDATE SET disciplines = "
            "(SELECT array_agg(DISTINCT d) FROM unnest(objectives.disciplines || EXCLUDED.disciplines) d)"),
            {"o": oid, "k": row.kind, "n": g.name, "lat": g.lat, "lon": g.lon, "g": gid, "t": row.type_group})
        return oid
    if row.lat is None or row.lon is None or not row.state or not row.objective_name or coord_problem(row.lat, row.lon):
        report.quarantine(ref, "curated_needs_location")
        return None
    oid = objective_id_for(row.kind, row.objective_ref)
    await conn.execute(text(
        "INSERT INTO objectives (objective_id, kind, name, lat, lon, geom, disciplines, source, license, curated_by) "
        "VALUES (:o, :k, :n, :lat, :lon, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, ARRAY[:t], 'curated', 'CC0', 'owner') "
        "ON CONFLICT (objective_id) DO UPDATE SET disciplines = "
        "(SELECT array_agg(DISTINCT d) FROM unnest(objectives.disciplines || EXCLUDED.disciplines) d)"),
        {"o": oid, "k": row.kind, "n": row.objective_name, "lat": row.lat, "lon": row.lon, "t": row.type_group})
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
        "SELECT CASE WHEN gnis_id IS NOT NULL THEN 'gnis:' || gnis_id ELSE NULL END FROM objectives WHERE gnis_id IS NOT NULL"))).all()}
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
            kind = "peak" if row.objective_ref.startswith("gnis:") else None
            oid = objective_id_for("peak", row.objective_ref) if kind else (await conn.execute(text(
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

- `objective_routes.csv` — standard mountaineering routes for hotspot objectives (spec P2-2 step 5). Facts from NPS/USFS public pages (name, grade, season) plus a `summary` of at most 300 characters written by us. Never copy text from Mountain Project or paste from source pages. `objective_ref` is `gnis:<GNIS feature id>` or `curated:<slug>` (then `objective_name`, `state`, `lat`, `lon` are required).
- `objective_permit_counts.csv` — published permit/attempt counts (e.g. NPS Denali and Rainier climbing statistics), with the https URL of the published source.

Both are loaded by `python -m app.pipelines.curated routes|permits` as the ingest role. Everything here is released CC0.
```

`data/curated/objective_routes.csv`: the header line only. `data/curated/objective_permit_counts.csv`: the header line only. Append `"app.pipelines.curated"` to strict mypy.

- [ ] **Step 4: Run** — PASS (`python ../scripts/check_no_mp_data.py` also passes: no MP URLs or ids in `data/curated`). **Step 5: Commit** — `git add backend/app/pipelines/curated.py backend/tests/test_curated.py data/curated/ backend/pyproject.toml && git commit -m "feat(pipelines): curated CC0 objective routes and permit counts loaders"`

---

### Task 5: Route and accident links to objectives

**Files:**
- Modify: `backend/app/pipelines/objectives.py`, `backend/tests/test_objectives.py`

**Interfaces:**
- Produces: `ROUTE_NEAR_M = 2000`, `ACCIDENT_NEAR_M = 3000`, `async link_routes(conn) -> int` (`near` links for scorable routes whose area point is within 2 km of an objective, method `distance`), `async link_accidents(conn) -> int` (clean accidents with no `accident_route_links` row, within 3 km → `objective_id`, method `objective_near`); CLI step `links`.

- [ ] **Step 1: Failing test** — append to `test_objectives.py`:

```python
from app.pipelines.objectives import link_accidents, link_routes


@requires_pg
def test_links_near_routes_and_unlinked_accidents_only():
    extra = SEED + """
    INSERT INTO canonical_areas (area_id, name, path, lat, lon, geom, coord_precision, source, redistributable) VALUES
      ('00000000-0000-0000-0000-00000000000b', 'Near Crag', '0000000000000000000000000000000b', 46.86, -121.76,
       ST_SetSRID(ST_MakePoint(-121.76, 46.86), 4326)::geography, 'area_centroid', 'openbeta', true);
    INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable)
      VALUES ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-00000000000b', 'Near', '{alpine}', 'alpine', 'rt-v1', false, true, 'openbeta', true);
    INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, date_precision)
      VALUES (2, 'AAC', '2011-07-02', 46.85, -121.76, true, 'climbing', 'US', 'day');
    INSERT INTO accident_route_links (accident_id, canonical_area_id, method) VALUES (2, '00000000-0000-0000-0000-00000000000b', 'x');
    """
    with migrated_db(seed_sql=extra) as name:
        _run(name, seed_gnis)
        assert _run(name, link_routes) == 1
        assert _run(name, link_accidents) == 1
        rows = _run(name, lambda c: c.execute(text("SELECT accident_id, method FROM accident_route_links ORDER BY 1")))
        assert [tuple(r) for r in rows] == [(1, "objective_near"), (2, "x")]
```

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


async def link_accidents(conn: AsyncConnection) -> int:
    result = await conn.execute(text(
        "INSERT INTO accident_route_links (accident_id, objective_id, method, score) "
        "SELECT DISTINCT ON (a.accident_id) a.accident_id, o.objective_id, 'objective_near', "
        " 1 - ST_Distance(a.coordinates, o.geom) / :d FROM accidents_clean a JOIN objectives o "
        " ON a.coordinates IS NOT NULL AND ST_DWithin(a.coordinates, o.geom, :d) "
        "WHERE NOT EXISTS (SELECT 1 FROM accident_route_links l WHERE l.accident_id = a.accident_id) "
        "ORDER BY a.accident_id, ST_Distance(a.coordinates, o.geom)"),
        {"d": ACCIDENT_NEAR_M})
    return result.rowcount
```

Add `links` to the CLI step choices (runs both). Grants: `ingest` needs `SELECT ON public.scorable_routes` — add `GRANT SELECT ON public.scorable_routes TO ingest;` to `grants_phase2.sql`.

- [ ] **Step 3: Run** — PASS. **Step 4: Commit** — `git add backend/app/pipelines/objectives.py backend/tests/test_objectives.py backend/db/roles/grants_phase2.sql && git commit -m "feat(pipelines): link nearby routes and unlinked accidents to objectives"`

---

### Task 6: Coverage badges and the hotspot report

**Files:**
- Create: `backend/app/pipelines/coverage.py`, `backend/tests/test_coverage.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `COVERAGE_RULE_VERSION = "cv-v1"`, `Level = Literal["route", "objective", "thin"]`, `ROUTE_LEVEL_MIN = 5`, `THIN_RADIUS_M = 5000`, `LIGHTNING_MAX_LAT = 54.0`, `badge_for(n_routes: int, *, has_objective: bool, has_curated_route: bool, accidents_5km: int) -> Level | None`, `lightning_coverage(lat: float) -> Literal["satellite", "none"]`, `async compute_badges(conn) -> dict[str, int]`, `async hotspots(conn, limit: int = 30) -> list[dict[str, object]]` (objectives ranked by clean ice/alpine/mountaineering accidents within 5 km where the catalog has <5 routes in the discipline), CLI `python -m app.pipelines.coverage badges|hotspots`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_coverage.py`:

```python
from app.pipelines.coverage import badge_for, lightning_coverage


def test_route_level_needs_five_routes():
    assert badge_for(5, has_objective=True, has_curated_route=False, accidents_5km=9) == "route"


def test_thin_beats_objective_without_curated_route():
    assert badge_for(2, has_objective=True, has_curated_route=False, accidents_5km=1) == "thin"
    assert badge_for(2, has_objective=True, has_curated_route=True, accidents_5km=1) == "objective"


def test_objective_level_and_fallbacks():
    assert badge_for(0, has_objective=True, has_curated_route=False, accidents_5km=0) == "objective"
    assert badge_for(3, has_objective=False, has_curated_route=False, accidents_5km=0) == "route"
    assert badge_for(0, has_objective=False, has_curated_route=False, accidents_5km=0) is None
    assert badge_for(0, has_objective=False, has_curated_route=False, accidents_5km=2) == "thin"


def test_north_of_54_has_no_lightning_coverage():
    assert lightning_coverage(63.07) == "none"
    assert lightning_coverage(46.85) == "satellite"
```

- [ ] **Step 2: Implement** `backend/app/pipelines/coverage.py`:

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

COVERAGE_RULE_VERSION = "cv-v1"
Level = Literal["route", "objective", "thin"]
ROUTE_LEVEL_MIN = 5
THIN_RADIUS_M = 5000
LIGHTNING_MAX_LAT = 54.0
TYPE_GROUPS = ("sport", "trad", "alpine", "ice", "mixed")


def badge_for(n_routes: int, *, has_objective: bool, has_curated_route: bool, accidents_5km: int) -> Level | None:
    if n_routes >= ROUTE_LEVEL_MIN:
        return "route"
    if accidents_5km >= 1 and not has_curated_route:
        return "thin"
    if has_objective:
        return "objective"
    if n_routes >= 1:
        return "route"
    return None


def lightning_coverage(lat: float) -> Literal["satellite", "none"]:
    return "none" if lat > LIGHTNING_MAX_LAT else "satellite"


AREA_FACTS = """
WITH crags AS (
  SELECT a.area_id, a.path, a.geom, a.lat, a.objective_id FROM canonical_areas a
  WHERE a.retired_at IS NULL AND a.geom IS NOT NULL
    AND (a.objective_id IS NOT NULL OR EXISTS (SELECT 1 FROM canonical_routes r WHERE r.area_id = a.area_id))
), counts AS (
  SELECT c.area_id, s.type_group, count(*) AS n FROM crags c
  JOIN canonical_areas d ON d.path <@ c.path JOIN scorable_routes s ON s.area_id = d.area_id
  GROUP BY 1, 2
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
    for area_id, lat, objective_id, tg, n_routes, n_acc, curated in rows:
        level = badge_for(int(n_routes), has_objective=objective_id is not None, has_curated_route=bool(curated),
                          accidents_5km=int(n_acc))
        if level is None:
            continue
        scope_kind, scope_id = ("objective", objective_id) if objective_id is not None else ("area", area_id)
        params.append({"k": scope_kind, "i": scope_id, "t": tg, "l": level, "n": int(n_routes), "a": int(n_acc),
                       "c": bool(curated), "lc": lightning_coverage(float(lat)), "v": COVERAGE_RULE_VERSION})
        if objective_id is not None:
            per_objective.setdefault(str(objective_id), []).append(level)
    if params:
        await conn.execute(text(
            "INSERT INTO coverage_badges (scope_kind, scope_id, type_group, level, n_routes, n_clean_accidents_5km, "
            "has_curated_route, lightning_coverage, coverage_rule_version) VALUES (:k, :i, :t, :l, :n, :a, :c, :lc, :v)"), params)
    worst = {"thin": 0, "objective": 1, "route": 2}
    for oid, levels in per_objective.items():
        await conn.execute(text(
            "UPDATE objectives SET coverage_level = :l, coverage_rule_version = :v, updated_at = now() WHERE objective_id = :o"),
            {"l": min(levels, key=worst.__getitem__), "v": COVERAGE_RULE_VERSION, "o": oid})
    counts: dict[str, int] = {"route": 0, "objective": 0, "thin": 0}
    for p in params:
        counts[str(p["l"])] += 1
    return counts


HOTSPOTS = """
SELECT o.objective_id, o.name, o.lat, o.lon, count(DISTINCT a.accident_id) AS n_acc,
       coalesce(max(b.n_routes), 0) AS routes, bool_or(coalesce(b.has_curated_route, false)) AS curated
FROM objectives o
JOIN accidents_clean a ON a.coordinates IS NOT NULL AND ST_DWithin(a.coordinates, o.geom, :r)
  AND (a.activity ILIKE ANY (ARRAY['%mountaineer%', '%ice%', '%alpine%', '%snow%']))
LEFT JOIN coverage_badges b ON b.scope_kind = 'objective' AND b.scope_id = o.objective_id AND b.type_group IN ('alpine', 'ice', 'mixed')
GROUP BY o.objective_id, o.name, o.lat, o.lon
HAVING coalesce(max(b.n_routes), 0) < 5
ORDER BY n_acc DESC LIMIT :n
"""


async def hotspots(conn: AsyncConnection, limit: int = 30) -> list[dict[str, object]]:
    rows = (await conn.execute(text(HOTSPOTS), {"r": THIN_RADIUS_M, "n": limit})).all()
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

- [ ] **Step 3: Run** — PASS. **Step 4: Commit** — `git add backend/app/pipelines/coverage.py backend/tests/test_coverage.py backend/pyproject.toml && git commit -m "feat(pipelines): versioned coverage badges and hotspot ranking"`

---

### Task 7: Acceptance cells and workflow hook

**Files:**
- Create: `backend/tests/verify/test_phase2b_objectives.py`
- Modify: `.github/workflows/data-static-features.yml` (runs `objectives seed`, `objectives links`, `coverage badges` after `static_features routes`)

- [ ] **Step 1: Cells**

```python
import asyncio
import os

import asyncpg
import pytest

pytestmark = pytest.mark.db
URL = os.environ.get("VERIFY_DATABASE_URL")
SEED_LIST = ("Denali", "Mount Rainier", "Mount Hood", "Mount Shasta", "Mount Baker", "Mount Adams",
             "Mount Washington", "Longs Peak", "Mount Whitney")


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


def test_every_objective_has_an_area_and_a_badge():
    [row] = fetch("SELECT count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM canonical_areas a WHERE a.objective_id = o.objective_id)) AS no_area, "
                  "count(*) FILTER (WHERE o.coverage_level IS NULL) AS no_badge FROM objectives o")
    assert (row["no_area"], row["no_badge"]) == (0, 0)


def test_seed_list_objectives_have_a_curated_route():
    rows = fetch("SELECT o.name FROM objectives o WHERE EXISTS (SELECT 1 FROM objective_routes r WHERE r.objective_id = o.objective_id)")
    have = {r["name"] for r in rows}
    missing = [n for n in SEED_LIST if not any(n.lower() in h.lower() or h.lower() in n.lower() for h in have)]
    assert missing == [], f"curate these seed-list objectives: {missing}"


def test_no_thin_badge_carries_a_curated_route():
    [row] = fetch("SELECT count(*) AS n FROM coverage_badges WHERE level = 'thin' AND has_curated_route")
    assert row["n"] == 0
```

(Denali's current GNIS name may be "Mount McKinley"; the cell's substring match accepts either spelling only if the curated CSV row's `objective_name`/GNIS name contains one of them; the runbook uses the GNIS id, so check the stored name and add it to `SEED_LIST` if it differs.)

- [ ] **Step 2: Workflow** — append to `data-static-features.yml` steps (before the failure step):

```yaml
      - run: uv run python -m app.pipelines.objectives seed
      - run: uv run python -m app.pipelines.objectives links
      - run: uv run python -m app.pipelines.coverage badges
```

- [ ] **Step 3: Run** — `cd backend && uv run pytest -q && uv run mypy` → green. **Step 4: Commit** — `git add backend/tests/verify/test_phase2b_objectives.py .github/workflows/data-static-features.yml && git commit -m "feat(ops): objectives and coverage in the weekly features workflow; acceptance cells"`

---

### Task 8: Docs

- [ ] **Step 1** — CHANGELOG "Phase 2b objectives and coverage (PR 2b-2d)"; CLAUDE.md Commands: `objectives seed|wikidata|links`, `curated routes|permits`, `coverage badges|hotspots`; Data rules: "Curated objective routes are CC0 in our own words; never paste source text"; DATABASE_STRUCTURE.md: the five tables.
- [ ] **Step 2** — `cd backend && uv run pytest -q && uv run ruff check . ../scripts/ && uv run mypy` → green.
- [ ] **Step 3: Commit** — `git add CHANGELOG.md CLAUDE.md data/DATABASE_STRUCTURE.md && git commit -m "docs: objectives, curated records, coverage badges"`

---

### Task 9: OWNER/AGENT RUNBOOK — seed, curate, badge

- [ ] **Step 1 (owner/agent): Branch rehearsal** — migrate (`0012`), `alembic check`, grants, verify scripts; `objectives seed` → counts; `objectives wikidata` → `linked`, `duplicates`, `elevation_mismatch_50m` (report; mismatches are investigated, not auto-fixed); `static_features points` + `elevation` (objective areas become points), `normals` for new buckets (budget as plan 5); `objectives links`; `coverage badges`; `coverage hotspots` → top 30 list.
- [ ] **Step 2 (owner): Curate** — for each of the top ~30 hotspots and every spec seed-list objective (Denali West Buttress/West Rib/Cassin, Foraker, Rainier DC/Emmons/Kautz/Liberty Ridge, Hood South Side, Shasta Avalanche Gulch/Casaval, Baker Coleman-Deming/Easton, Adams South Spur, Jefferson, North Sister, Thompson/Snoqualmie, Mt Washington Huntington/Tuckerman, Longs Peak, Whitney Mountaineers Route): add rows to `data/curated/objective_routes.csv` from NPS/USFS pages — facts plus your own ≤300-character summary; use `gnis:<id>` from `gnis_summits` (look up with analyst: `SELECT gnis_id, name, state_code FROM gnis_summits WHERE name_key = 'rainier'`). Add published permit/attempt counts (NPS Denali and Rainier statistics) to `objective_permit_counts.csv`. Commit both files on a docs branch and open a PR (owner pushes).
- [ ] **Step 3 (owner/agent): Load and verify** — `curated routes`, `curated permits` (reports name row numbers only), `static_features routes`, `objectives links`, `coverage badges`; `-m db tests/verify/test_phase2b_objectives.py` → pass. Ice venues listed in the spec (Frankenstein, Cathedral/Whitehorse, Chapel Pond, Poke-O-Moonshine, Ouray, Kelso/Stevens Gulch, Vail, Hyalite) get a curated objective only if `coverage hotspots` still lists them.
- [ ] **Step 4 (owner): Prod** — repeat Steps 1 and 3.

---

## Self-review

- Spec coverage: objectives schema and seeding (GNIS, Wikidata, OpenBeta ice areas), curated CC0 standard routes as `canonical_routes` (`safeascent_curated`), `route_objective_links`, `accident_route_links.objective_id`, permit counts, coverage badges with rule version, lightning flag, hotspot ranking and top-30 curation, milestone 2b-2 acceptance "every seed-list objective has ≥1 curated route and a coverage badge" (Tasks 1–7, 9). OSM and the OpenBeta contribution are optional and excluded (Global Constraints).
- Placeholders: none beyond Console hosts and owner-curated CSV rows.
- Types: `objective_id_for`, `objective_area_id`, `badge_for` signatures match their callers in Tasks 4–6.
