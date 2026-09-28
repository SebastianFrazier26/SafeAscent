# Phase 2b Catalog (PRs 2b-1a, 2b-1b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the public route catalog: OpenBeta (CC0) areas and climbs as the primary rock source, `type_group` from a versioned mapper, an in-house matcher linking internal MP routes and areas to it, MP ice/mixed route facts promoted where OpenBeta has no match, CI guards against MP data leaking into the repo, and the content split of MP tables (raw ticks with climber names move to `internal`; the Ascents tab reads aggregate counts).

**Architecture:** `app/pipelines/openbeta.py` (GraphQL client, pydantic-validated) feeds `catalog.py` (validated upsert of `canonical_areas`/`canonical_routes` with an `ltree` path, retirement of vanished rows, OpenBeta `mp_id` cross-references written only to `internal`). `route_types.py` maps flags/grades to `type_group`. `match.py` scores area and climb candidates (H3 r7 neighbourhoods, in-house Jaro-Winkler) and auto-decides outside the 0.80–0.90 band. `mp_facts.py` is the only path from MP data to public catalog tables, writing name/grade/type/location only. A weekly GitHub Actions workflow runs the OpenBeta load and pings healthchecks.io.

**Tech Stack:** Python 3.12, httpx, pydantic 2, SQLAlchemy async, Alembic, Postgres `ltree` + PostGIS, `h3` (v4, new `pipelines` dependency group), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` §Route catalog (P2-1), §Route types (P2-3, P2-4), §Tables, milestone 2b-1; owner decisions D2, D5, D11 in `2026-09-28-phase2a-foundations.md`; amendment D5 (MP rock routes and tick aggregates may be displayed; no MP prose ever).

**Prerequisite:** Plan 1 merged and applied. Plan 2's `textsim.py`/`geo.py` (Task 5 of plan 2) merged — this plan imports them. `0008`'s `down_revision` is `0007` (plan 3); if this plan merges before plan 3, point it at the then-current head and renumber (Alembic history is linear).

**Two PRs:** PR 2b-1a = Tasks 1–8 (`feat/p2b-catalog`) + runbook Task 11; PR 2b-1b = Tasks 9–10 (`feat/p2b-mp-split`) + runbook Task 12 `[assumes D2]`.

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- OpenBeta queries never request media/photos. OpenBeta data is CC0 and may appear in committed test fixtures; MP data may not (no MP names, ids outside the synthetic range ≥900000000, or MP route/area URLs anywhere tracked).
- `canonical_routes` has no MP ids and no prose columns. MP links live only in `internal.mp_route_links` / `internal.mp_area_links`, which `app` cannot read.
- The `mp_facts` job is the only code path from MP tables to public catalog tables; it writes only name, grade, type group, and area name/lat/lon, and only `ice`/`mixed` rows. Rock MP routes are never promoted into the catalog; they stay displayed through the existing `mp_routes` map path (amendment D5).
- Matching thresholds (P2-10): ≥0.90 and unique → auto link; <0.80 → auto no-link; 0.80–0.90 → owner review CSV. Auto-link precision must reach ≥0.98 on 300 hand-labelled pairs, whose file lives in `~/Developer/safeascent-private/golden/` (it holds MP ids, so it cannot be committed).
- Weekly load: reject the run if the US climb count drops more than 3% against the previous successful load.
- Missing coordinates stay NULL; an area without coordinates makes its routes `scored = false` rather than inventing a location.

## Decisions this plan makes where the spec is silent (owner may overrule)

1. **OpenBeta ids are reused as primary keys** (`area_id = ob_area_uuid`, `route_id = ob_climb_uuid`); `mp_facts` rows get deterministic `uuid5` ids so reloads are stable.
2. **Weekly full reload by state replaces GraphQL `updatedAt` deltas** (D11: GraphQL is the bulk source). Rows not seen in a successful full load get `retired_at` and drop out of `scorable_routes`.
3. **`ltree` labels are the area UUID's 32 hex characters** (ltree labels cannot contain `-`).
4. **Toprope/aid-only climbs with no bolt information map to `unknown`** (spec rule 6 says "gear → trad", but OpenBeta has no gear flag; guessing trad would score a route under the wrong type).
5. **Grade similarity** for climb matching: identical normalized grade 1.0, same base grade ignoring letter/sign (`5.10a` vs `5.10c`, `WI4` vs `WI4+`) 0.5, else 0.
6. **One healthchecks.io ping key** (`HEALTHCHECKS_PING_KEY`) with a slug per data job (`https://hc-ping.com/<key>/<slug>`), instead of one URL setting per job.
7. **`check_no_mp_data.py` flags MP route/area URLs** (`mountainproject.com/route/<digits>`, `/area/<digits>`), not bare domain mentions, so specs and Phase 1 guard fixtures that name the domain still pass.

## Review Focus

1. **Two OpenBeta climbs in the same area both score ≥0.90 against one MP route** — expect review, never an arbitrary auto-link (Task 5 `test_two_strong_candidates_go_to_review`).
2. **A mixed route whose MP type string is "Ice, Mixed, Alpine"** — expect `mixed` (precedence), not `ice` or `alpine` (Task 2 `test_precedence_mixed_beats_ice_beats_alpine`).
3. **A weekly load where one state query fails** — expect the run `failed`, nothing retired, previous rows intact (Task 4 `test_partial_failure_retires_nothing`).
4. **An OpenBeta area whose centroid is in Canada** — expect quarantine `outside_us` and its climbs not loaded (Task 4 `test_foreign_areas_are_quarantined_with_their_climbs`).
5. **The Ascents tab for a route whose only ticks are dated in a future month** — expect zero ascents and "no tick data", never a count including them (Task 9 `test_future_periods_are_not_counted`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/db/owner/extensions_phase2b.sql` | Create | Owner creates `ltree` (migrator cannot create extensions). |
| `backend/alembic/versions/0008_catalog.py` | Create | Catalog tables, links, `scorable_routes`, `mp_tick_counts`. |
| `backend/alembic/versions/0009_mp_ticks_internal.py` | Create | Move `mp_ticks` to `internal`, revoke `app`. |
| `backend/app/models/catalog.py` | Create | `CanonicalArea`, `CanonicalRoute`, `AccidentRouteLink`, `MpRouteLink`, `MpAreaLink`, `MatchDecision`, `MpTickCount`. |
| `backend/app/pipelines/route_types.py` | Create | `type_group` mapper for OpenBeta and MP inputs. |
| `backend/app/pipelines/openbeta.py` | Create | GraphQL client + response models. |
| `backend/app/pipelines/catalog.py` | Create | Validated catalog upsert, retirement, OB `mp_id` cross-refs, weekly CLI. |
| `backend/app/pipelines/jobping.py` | Create | Per-job healthchecks slugs. |
| `backend/app/pipelines/match.py` | Create | Scoring, candidates, decisions (pure) + matcher job, review CSV, precision eval. |
| `backend/app/pipelines/mp_facts.py` | Create | Ice/mixed MP facts → catalog. |
| `backend/app/pipelines/mp_tick_counts.py` | Create | Public aggregate for the Ascents tab. |
| `backend/app/api/v1/mp_routes.py` | Modify | `get_ascent_analytics` reads `mp_tick_counts`. |
| `backend/app/pipelines/mp_ticks_quarantine.py` | Modify | Table name `internal.mp_ticks` (PR 2b-1b). |
| `scripts/check_no_mp_data.py`, `backend/tests/test_check_no_mp_data.py` | Create | Repo guard + tests. |
| `backend/tests/test_no_mp_prose_in_schemas.py` | Create | API schemas carry no MP prose fields. |
| `backend/tests/fixtures/openbeta_bulk_sample.json` | Create | Recorded CC0 OpenBeta response (no media). |
| `.github/workflows/data-openbeta.yml` | Create | Weekly load; issue on failure. |
| `.github/workflows/ci.yml` | Modify | `--group pipelines`; audit includes it; `check_no_mp_data.py` in `guards`. |
| `backend/pyproject.toml`, `backend/uv.lock` | Modify | `pipelines` group (`h3`); mypy; |
| `backend/app/config.py`, `.env.example` | Modify | `HEALTHCHECKS_PING_KEY`. |
| `backend/db/roles/grants_phase2.sql`, `verify_roles_phase2.sql` | Modify | Grants. |
| tests: `test_migration_0008.py`, `test_route_types.py`, `test_openbeta.py`, `test_catalog.py`, `test_match.py`, `test_mp_facts.py`, `test_mp_tick_counts.py`, `test_migration_0009.py`; `test_ascent_analytics.py` (modify) | Create/Modify | Tests. |
| `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md` | Modify | Docs. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 3–10 | catalog tables, `mp_tick_counts` (0008) | Task 1 first. |
| 2 | 4, 7, plan 5 | `route_types.map_type_group(flags, grades, bolts)`, `flags_from_mp_type(type_str)` | Frozen in Task 2. |
| 3 | 4 | `openbeta.OpenBetaClient.us_states()`, `.bulk_areas(state_uuid)`, `ObArea`, `ObClimb` | Frozen in Task 3. |
| 4 | 5, 6, 7 | `internal.mp_route_links`/`mp_area_links` rows with method `ob_mp_id` | Task 4 writes them; the matcher never overwrites `ob_mp_id` links. |
| 5 | 6 | `match.area_score`, `climb_score`, `decide` | Task 5 pure; Task 6 job. |
| 6 | 7 | matched `mp_area_links` | `mp_facts` needs area links; Task 6 before 7. |
| 4 | 8 | `backend/tests/fixtures/openbeta_bulk_sample.json` vs `check_no_mp_data.py` | Fixture holds OpenBeta `mp_id` values → Task 3 strips `mp_id` to synthetic values ≥900000000 before committing. |
| 4, 9 | each other | `backend/pyproject.toml` (group, mypy), `.github/workflows/*` | Serial. |
| 9 | 10 | `get_ascent_analytics` must read `mp_tick_counts` before `mp_ticks` moves | PR 2b-1b ships Task 9's code and Task 10's migration together; runbook Task 12 orders: build counts → deploy code → migrate `0009`. |
| 10 | plan 1 Task 5, Task 9 | `mp_ticks_quarantine.QUARANTINE_SQL`, `mp_tick_counts.BUILD_SQL` table name; grants/verify rows on `mp_ticks` | Task 10 adds `mp_tables.ticks_table()` and edits all of them in one commit, so every job works on both sides of `0009`. |
| 1, 4, 6, 7, 9, 10 | each other | `grants_phase2.sql`, `verify_roles_phase2.sql` | Append-only; serial commits. |

---

# PR 2b-1a — `feat/p2b-catalog`

### Task 1: `ltree` owner script, migration `0008`, models, `pipelines` dependency group

**Files:**
- Create: `backend/db/owner/extensions_phase2b.sql`, `backend/alembic/versions/0008_catalog.py`, `backend/app/models/catalog.py`, `backend/tests/test_migration_0008.py`
- Modify: `backend/app/models/__init__.py`, `backend/pyproject.toml`, `backend/uv.lock`, `.github/workflows/ci.yml`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`

**Interfaces:**
- Produces (DB, public): `canonical_areas(area_id uuid PK, name text NOT NULL, parent_id uuid NULL REFERENCES canonical_areas, path ltree NOT NULL, lat double precision NULL, lon double precision NULL, geom geography(Point,4326) NULL, ob_area_uuid uuid UNIQUE NULL, objective_id uuid NULL, coord_precision text NOT NULL CHECK IN ('area_centroid','crag','none'), source text NOT NULL CHECK IN ('openbeta','mp_facts','safeascent_curated'), redistributable boolean NOT NULL, is_boulder_area boolean NOT NULL DEFAULT false, retired_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(), run_id uuid)`, GiST on `path` and `geom`; `canonical_routes(route_id uuid PK, area_id uuid NOT NULL REFERENCES canonical_areas, name text NOT NULL, grade text, disciplines text[] NOT NULL, type_group text NULL CHECK IN ('sport','trad','alpine','ice','mixed','unknown'), type_rule_version text NOT NULL, is_boulder boolean NOT NULL, scored boolean NOT NULL, pitches smallint, length_m real, bolts smallint, ob_climb_uuid uuid UNIQUE NULL, source text NOT NULL CHECK (same three), redistributable boolean NOT NULL, retired_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(), run_id uuid)`, index on `area_id`, CHECK `(is_boulder AND type_group IS NULL AND NOT scored) OR (NOT is_boulder AND type_group IS NOT NULL)`; `accident_route_links(accident_id integer PK REFERENCES accidents, canonical_route_id uuid NULL REFERENCES canonical_routes, canonical_area_id uuid NULL REFERENCES canonical_areas, objective_id uuid NULL, method text NOT NULL, score real, CHECK (num_nonnulls(canonical_route_id, canonical_area_id, objective_id) >= 1))`; `mp_tick_counts(mp_route_id bigint, period text CHECK (period = 'undated' OR period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'), tick_count integer NOT NULL CHECK >= 0, built_run_id uuid, PK (mp_route_id, period))`; view `scorable_routes` = `SELECT * FROM canonical_routes WHERE scored AND type_group <> 'unknown' AND retired_at IS NULL`.
- Produces (DB, internal): `mp_route_links(route_id uuid REFERENCES public.canonical_routes, mp_route_id bigint, match_score real NOT NULL, match_method text NOT NULL CHECK IN ('ob_mp_id','auto','owner','mp_facts'), PK (route_id, mp_route_id))` + index on `mp_route_id`; `mp_area_links(area_id uuid REFERENCES public.canonical_areas, mp_location_id bigint, match_score real NOT NULL, match_method text NOT NULL CHECK IN ('ob_mp_id','auto','owner','mp_facts'), PK (area_id, mp_location_id))` + index on `mp_location_id`; `match_decisions(kind text CHECK IN ('area','climb'), mp_id bigint, ob_uuid uuid, score real NOT NULL, decision text CHECK IN ('link','no_link'), decided_by text CHECK IN ('auto','owner'), decided_at timestamptz DEFAULT now(), PK (kind, mp_id, ob_uuid))`.
- Produces (Python): models in `app.models.catalog`; dependency group `pipelines = ["h3>=4.1,<5"]`.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0008.py`:

```python
import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
AREA = "INSERT INTO canonical_areas (area_id, name, path, coord_precision, source, redistributable) VALUES ('{id}', 'A', '{path}', 'none', 'openbeta', true);"
A1 = "00000000-0000-0000-0000-00000000000a"


def test_0008_checks_clean_and_boulder_rule():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))
        run_sql(name, AREA.format(id=A1, path="a" * 32))
        base = ("INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, "
                "is_boulder, scored, source, redistributable) VALUES ")
        run_sql(name, base + f"(gen_random_uuid(), '{A1}', 'R', '{{bouldering}}', NULL, 'rt-v1', true, false, 'openbeta', true)")
        run_sql(name, base + f"(gen_random_uuid(), '{A1}', 'R', '{{trad}}', 'trad', 'rt-v1', false, true, 'openbeta', true)")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, base + f"(gen_random_uuid(), '{A1}', 'R', '{{bouldering}}', NULL, 'rt-v1', true, true, 'openbeta', true)")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO accident_route_links (accident_id, method) VALUES (1, 'x')")


def test_0008_refuses_without_ltree_when_unprivileged():
    with migrated_db("0007_cell_daily_conditions") as name:
        run_sql(name, "CREATE ROLE p2_noext LOGIN PASSWORD 'pw'; GRANT ALL ON SCHEMA public TO p2_noext;"
                      "GRANT ALL ON ALL TABLES IN SCHEMA public TO p2_noext;")
        cfg = _alembic_cfg(name)
        base = cfg.get_main_option("sqlalchemy.url").split("@", 1)[1]
        cfg.set_main_option("sqlalchemy.url", f"postgresql+asyncpg://p2_noext:pw@{base}")
        try:
            with pytest.raises(RuntimeError, match="extensions_phase2b.sql"):
                command.upgrade(cfg, "head")
        finally:
            run_sql(name, "DROP OWNED BY p2_noext; DROP ROLE p2_noext;")
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Owner script** `backend/db/owner/extensions_phase2b.sql`:

```sql
-- Run as the database owner before migration 0008. ltree is a trusted extension, but
-- creating it still needs CREATE on the database, which migrator deliberately lacks.
\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS ltree;
\echo 'ltree present'
```

- [ ] **Step 4: Migration** `backend/alembic/versions/0008_catalog.py`:

```python
"""Route catalog (P2-1): canonical areas/routes, internal MP links, match decisions,
accident links, the scorable view, and the public tick-count aggregate (D2)."""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

revision = "0008_catalog"
down_revision = "0007_cell_daily_conditions"
branch_labels = None
depends_on = None

SOURCES = "('openbeta', 'mp_facts', 'safeascent_curated')"
METHODS = "('ob_mp_id', 'auto', 'owner', 'mp_facts')"


class Ltree(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "LTREE"


def _ensure_ltree() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT 1 FROM pg_extension WHERE extname = 'ltree'").first():
        return
    if not bind.exec_driver_sql("SELECT has_database_privilege(current_user, current_database(), 'CREATE')").scalar():
        raise RuntimeError("extension ltree is missing: run backend/db/owner/extensions_phase2b.sql as the owner first")
    op.execute("CREATE EXTENSION ltree")


def upgrade() -> None:
    _ensure_ltree()
    op.create_table(
        "canonical_areas",
        sa.Column("area_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("canonical_areas.area_id"), nullable=True),
        sa.Column("path", Ltree(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("geom", Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True),
        sa.Column("ob_area_uuid", postgresql.UUID(as_uuid=True), nullable=True, unique=True),
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("coord_precision", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("redistributable", sa.Boolean(), nullable=False),
        sa.Column("is_boulder_area", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("coord_precision IN ('area_centroid', 'crag', 'none')", name="canonical_areas_coord_precision_check"),
        sa.CheckConstraint(f"source IN {SOURCES}", name="canonical_areas_source_check"),
    )
    op.create_index("ix_canonical_areas_path", "canonical_areas", ["path"], postgresql_using="gist")
    op.create_index("ix_canonical_areas_geom", "canonical_areas", ["geom"], postgresql_using="gist")
    op.create_table(
        "canonical_routes",
        sa.Column("route_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("area_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("canonical_areas.area_id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("grade", sa.Text(), nullable=True),
        sa.Column("disciplines", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("type_group", sa.Text(), nullable=True),
        sa.Column("type_rule_version", sa.Text(), nullable=False),
        sa.Column("is_boulder", sa.Boolean(), nullable=False),
        sa.Column("scored", sa.Boolean(), nullable=False),
        sa.Column("pitches", sa.SmallInteger(), nullable=True),
        sa.Column("length_m", sa.REAL(), nullable=True),
        sa.Column("bolts", sa.SmallInteger(), nullable=True),
        sa.Column("ob_climb_uuid", postgresql.UUID(as_uuid=True), nullable=True, unique=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("redistributable", sa.Boolean(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "type_group IS NULL OR type_group IN ('sport', 'trad', 'alpine', 'ice', 'mixed', 'unknown')",
            name="canonical_routes_type_group_check",
        ),
        sa.CheckConstraint(f"source IN {SOURCES}", name="canonical_routes_source_check"),
        sa.CheckConstraint(
            "(is_boulder AND type_group IS NULL AND NOT scored) OR (NOT is_boulder AND type_group IS NOT NULL)",
            name="canonical_routes_boulder_check",
        ),
    )
    op.create_index("ix_canonical_routes_area", "canonical_routes", ["area_id"])
    op.execute(
        "CREATE VIEW scorable_routes AS SELECT * FROM canonical_routes "
        "WHERE scored AND type_group <> 'unknown' AND retired_at IS NULL"
    )
    op.create_table(
        "accident_route_links",
        sa.Column("accident_id", sa.Integer(), sa.ForeignKey("accidents.accident_id"), primary_key=True, autoincrement=False),
        sa.Column("canonical_route_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("canonical_routes.route_id"), nullable=True),
        sa.Column("canonical_area_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("canonical_areas.area_id"), nullable=True),
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("score", sa.REAL(), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(canonical_route_id, canonical_area_id, objective_id) >= 1",
            name="accident_route_links_target_check",
        ),
    )
    op.create_table(
        "mp_tick_counts",
        sa.Column("mp_route_id", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("tick_count", sa.Integer(), nullable=False),
        sa.Column("built_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("mp_route_id", "period"),
        sa.CheckConstraint(
            "period = 'undated' OR period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="mp_tick_counts_period_check"
        ),
        sa.CheckConstraint("tick_count >= 0", name="mp_tick_counts_count_check"),
    )
    for table, key, ref in (
        ("mp_route_links", "route_id", "public.canonical_routes.route_id"),
        ("mp_area_links", "area_id", "public.canonical_areas.area_id"),
    ):
        mp_col = "mp_route_id" if table == "mp_route_links" else "mp_location_id"
        op.create_table(
            table,
            sa.Column(key, postgresql.UUID(as_uuid=True), sa.ForeignKey(ref), nullable=False),
            sa.Column(mp_col, sa.BigInteger(), nullable=False),
            sa.Column("match_score", sa.REAL(), nullable=False),
            sa.Column("match_method", sa.Text(), nullable=False),
            sa.PrimaryKeyConstraint(key, mp_col),
            sa.CheckConstraint(f"match_method IN {METHODS}", name=f"{table}_method_check"),
            schema="internal",
        )
        op.create_index(f"ix_{table}_{mp_col}", table, [mp_col], schema="internal")
    op.create_table(
        "match_decisions",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("mp_id", sa.BigInteger(), nullable=False),
        sa.Column("ob_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.REAL(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("kind", "mp_id", "ob_uuid"),
        sa.CheckConstraint("kind IN ('area', 'climb')", name="match_decisions_kind_check"),
        sa.CheckConstraint("decision IN ('link', 'no_link')", name="match_decisions_decision_check"),
        sa.CheckConstraint("decided_by IN ('auto', 'owner')", name="match_decisions_by_check"),
        schema="internal",
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("internal.match_decisions", "public.accident_route_links", "public.canonical_routes"):
        n = bind.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
        if n:
            raise RuntimeError(f"refusing to downgrade 0008: {table} has {n} rows")
    op.drop_table("match_decisions", schema="internal")
    op.drop_table("mp_area_links", schema="internal")
    op.drop_table("mp_route_links", schema="internal")
    op.drop_table("mp_tick_counts")
    op.drop_table("accident_route_links")
    op.execute("DROP VIEW scorable_routes")
    op.drop_table("canonical_routes")
    op.drop_table("canonical_areas")
```

The migration test's unprivileged case works because the guard runs before any DDL. The existing `create_roles`-based role test is unaffected (it upgrades as a superuser-owned DB owner who may create extensions).

`backend/app/models/catalog.py` (models mirror the migration; `alembic check` pins them). Use the same `Ltree` `UserDefinedType` defined in a small shared module `backend/app/models/types.py`:

```python
"""Column types SQLAlchemy does not ship."""

from __future__ import annotations

from sqlalchemy.types import UserDefinedType


class Ltree(UserDefinedType[str]):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "LTREE"
```

(the migration keeps its own copy: migrations must not import app code that may change later.)

```python
"""Route catalog tables (migration 0008)."""

from __future__ import annotations

import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import REAL, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.types import Ltree


class CanonicalArea(Base):
    __tablename__ = "canonical_areas"
    __table_args__ = (
        Index("ix_canonical_areas_path", "path", postgresql_using="gist"),
        Index("ix_canonical_areas_geom", "geom", postgresql_using="gist"),
    )

    area_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_areas.area_id"))
    path: Mapped[str] = mapped_column(Ltree())
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    geom: Mapped[object | None] = mapped_column(Geography(geometry_type="POINT", srid=4326, spatial_index=False))
    ob_area_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    objective_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    coord_precision: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    redistributable: Mapped[bool] = mapped_column(Boolean)
    is_boulder_area: Mapped[bool] = mapped_column(Boolean, server_default="false")
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class CanonicalRoute(Base):
    __tablename__ = "canonical_routes"
    __table_args__ = (Index("ix_canonical_routes_area", "area_id"),)

    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    area_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_areas.area_id"))
    name: Mapped[str] = mapped_column(Text)
    grade: Mapped[str | None] = mapped_column(Text)
    disciplines: Mapped[list[str]] = mapped_column(ARRAY(Text))
    type_group: Mapped[str | None] = mapped_column(Text)
    type_rule_version: Mapped[str] = mapped_column(Text)
    is_boulder: Mapped[bool] = mapped_column(Boolean)
    scored: Mapped[bool] = mapped_column(Boolean)
    pitches: Mapped[int | None] = mapped_column(SmallInteger)
    length_m: Mapped[float | None] = mapped_column(REAL)
    bolts: Mapped[int | None] = mapped_column(SmallInteger)
    ob_climb_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    source: Mapped[str] = mapped_column(Text)
    redistributable: Mapped[bool] = mapped_column(Boolean)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class AccidentRouteLink(Base):
    __tablename__ = "accident_route_links"

    accident_id: Mapped[int] = mapped_column(Integer, ForeignKey("accidents.accident_id"), primary_key=True, autoincrement=False)
    canonical_route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_routes.route_id"))
    canonical_area_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_areas.area_id"))
    objective_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    method: Mapped[str] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(REAL)


class MpTickCount(Base):
    __tablename__ = "mp_tick_counts"

    mp_route_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    period: Mapped[str] = mapped_column(Text, primary_key=True)
    tick_count: Mapped[int] = mapped_column(Integer)
    built_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class MpRouteLink(Base):
    __tablename__ = "mp_route_links"
    __table_args__ = (Index("ix_mp_route_links_mp_route_id", "mp_route_id"), {"schema": "internal"})

    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_routes.route_id"), primary_key=True)
    mp_route_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    match_score: Mapped[float] = mapped_column(REAL)
    match_method: Mapped[str] = mapped_column(Text)


class MpAreaLink(Base):
    __tablename__ = "mp_area_links"
    __table_args__ = (Index("ix_mp_area_links_mp_location_id", "mp_location_id"), {"schema": "internal"})

    area_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_areas.area_id"), primary_key=True)
    mp_location_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    match_score: Mapped[float] = mapped_column(REAL)
    match_method: Mapped[str] = mapped_column(Text)


class MatchDecision(Base):
    __tablename__ = "match_decisions"
    __table_args__ = {"schema": "internal"}

    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    mp_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    ob_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    score: Mapped[float] = mapped_column(REAL)
    decision: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

Register `from app.models import catalog  # noqa: F401` in `app/models/__init__.py`; add `"app.models.catalog", "app.models.types"` to the models mypy block. If `alembic check` reports a type difference for `path` (reflection returns `NullType` for `ltree`), add to `alembic/env.py`'s `_configure` a `compare_type` callable that returns `False` when either side's type is `Ltree`/`NullType` for a column named `path`; this is the only acceptable model/DB exemption.

Dependency group:

```bash
cd backend && uv add --group pipelines 'h3>=4.1,<5'
```

In `.github/workflows/ci.yml` `backend` job: `run: uv sync --frozen --group pipelines`; the audit step becomes `uv export --frozen --no-dev --group pipelines --no-emit-project --format requirements.txt | uv run pip-audit -r /dev/stdin --require-hashes --disable-pip`. The Dockerfile keeps `uv sync --frozen --no-dev --no-install-project` (the `pipelines` group is not a default group, so the API image stays without `h3`).

Grants (`grants_phase2.sql`, "Plan 4 (0008)"):

```sql
GRANT SELECT, INSERT, UPDATE ON public.canonical_areas, public.canonical_routes TO ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.accident_route_links, public.mp_tick_counts TO ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON internal.mp_route_links, internal.mp_area_links TO ingest;
GRANT SELECT, INSERT, UPDATE ON internal.match_decisions TO ingest;
GRANT SELECT ON public.canonical_areas, public.canonical_routes, public.scorable_routes, public.accident_route_links TO trainer;
```

`verify_roles_phase2.sql` `ingest_writes`: add INSERT/UPDATE rows for `public.canonical_areas`, `public.canonical_routes`, `internal.match_decisions`; INSERT/UPDATE/DELETE rows for `public.accident_route_links`, `public.mp_tick_counts`, `internal.mp_route_links`, `internal.mp_area_links`.

- [ ] **Step 5: Run** — `cd backend && uv sync --group pipelines && uv run pytest tests/test_migration_0008.py tests/test_migrations.py tests/test_roles_phase2.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 6: Commit** — `git add backend/db/owner/ backend/alembic/versions/0008_catalog.py backend/app/models/ backend/tests/test_migration_0008.py backend/pyproject.toml backend/uv.lock .github/workflows/ci.yml backend/db/roles/ && git commit -m "feat(db): 0008 route catalog, internal MP links, tick-count aggregate; pipelines dependency group"`

---

### Task 2: Route type mapper

**Files:**
- Create: `backend/app/pipelines/route_types.py`, `backend/tests/test_route_types.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `TYPE_RULE_VERSION = "rt-v1"`, `TypeGroup = Literal["sport","trad","alpine","ice","mixed","unknown"]`, `FLAGS = frozenset({"trad","sport","tr","aid","alpine","snow","ice","mixed","bouldering","deepwatersolo"})`, `@dataclass(frozen=True) TypeResult(type_group: TypeGroup | None, is_boulder: bool, disciplines: tuple[str, ...])`, `map_type_group(flags: Iterable[str], grades: Iterable[str | None], bolts: int | None) -> TypeResult`, `flags_from_mp_type(type_str: str | None) -> set[str]`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_route_types.py`:

```python
import pytest

from app.pipelines.route_types import flags_from_mp_type, map_type_group


def tg(flags, grades=(), bolts=None):
    return map_type_group(flags, grades, bolts).type_group


def test_precedence_mixed_beats_ice_beats_alpine():
    assert tg(flags_from_mp_type("Ice, Mixed, Alpine")) == "mixed"
    assert tg({"ice", "alpine"}) == "ice"
    assert tg({"trad"}, ["M6"]) == "mixed"
    assert tg({"trad"}, ["WI4"]) == "ice"
    assert tg({"trad"}, ["AI3"]) == "ice"


def test_alpine_from_flag_snow_or_commitment_grade():
    assert tg({"snow"}) == "alpine"
    assert tg({"trad"}, ["5.9 IV"]) == "alpine"
    assert tg({"trad"}, ["5.9 III"]) == "trad"


def test_rock_rules_and_toprope_aid():
    assert tg({"trad", "sport"}) == "trad"
    assert tg({"sport"}) == "sport"
    assert tg(set(), ["5.10a"], bolts=6) == "sport"
    assert tg({"tr"}, bolts=3) == "sport"
    assert tg({"aid"}) == "unknown"
    assert tg(set()) == "unknown"


def test_boulders_are_unscored_and_have_no_type_group():
    result = map_type_group({"bouldering"}, ["V4"], None)
    assert (result.type_group, result.is_boulder) == (None, True)
    assert map_type_group({"bouldering", "trad"}, [], None).is_boulder is False


@pytest.mark.parametrize("raw,flags", [("Trad, Sport", {"trad", "sport"}), ("TR, Aid", {"tr", "aid"}), ("Boulder", {"bouldering"}), (None, set())])
def test_mp_type_strings(raw, flags):
    assert flags_from_mp_type(raw) == flags
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/route_types.py`:

```python
"""P2-3/P2-4 type groups, first match wins: mixed, ice, alpine, trad, sport, then
toprope/aid by their underlying rock type, else unknown (stored, never scored).
Boulder-only climbs get no type group and are never scored."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

TYPE_RULE_VERSION = "rt-v1"
TypeGroup = Literal["sport", "trad", "alpine", "ice", "mixed", "unknown"]
FLAGS = frozenset({"trad", "sport", "tr", "aid", "alpine", "snow", "ice", "mixed", "bouldering", "deepwatersolo"})
ROPED = FLAGS - {"bouldering", "deepwatersolo"}
_MP_ALIASES = {"boulder": "bouldering", "top rope": "tr", "toprope": "tr"}
_MIXED = re.compile(r"(?<![A-Za-z])M\d", re.I)
_ICE = re.compile(r"\b(?:WI|AI)\s?\d", re.I)
_COMMITMENT = re.compile(r"(?<![A-Za-z])(IV|V|VI|VII)(?![A-Za-z])")


@dataclass(frozen=True)
class TypeResult:
    type_group: TypeGroup | None
    is_boulder: bool
    disciplines: tuple[str, ...]


def flags_from_mp_type(type_str: str | None) -> set[str]:
    parts = (p.strip().lower() for p in (type_str or "").split(","))
    return {_MP_ALIASES.get(p, p) for p in parts if _MP_ALIASES.get(p, p) in FLAGS}


def map_type_group(flags: Iterable[str], grades: Iterable[str | None], bolts: int | None) -> TypeResult:
    f = {x.lower() for x in flags} & FLAGS
    text = " ".join(g for g in grades if g)
    disciplines = tuple(sorted(f))
    if "bouldering" in f and not (f & ROPED):
        return TypeResult(None, True, disciplines)
    group: TypeGroup
    if "mixed" in f or _MIXED.search(text):
        group = "mixed"
    elif "ice" in f or _ICE.search(text):
        group = "ice"
    elif "alpine" in f or "snow" in f or _COMMITMENT.search(text):
        group = "alpine"
    elif "trad" in f:
        group = "trad"
    elif "sport" in f or (bolts or 0) > 0:
        group = "sport"
    else:
        group = "unknown"
    return TypeResult(group, False, disciplines)
```

Rule 5's "rock with bolts" and rule 6's "toprope/aid with bolts → sport" collapse into the same `bolts > 0` branch; toprope/aid with no bolts fall to `unknown` (plan decision 4).

Append `"app.pipelines.route_types"` to strict mypy.

- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/route_types.py backend/tests/test_route_types.py backend/pyproject.toml && git commit -m "feat(pipelines): versioned route type-group mapper"`

---

### Task 3: OpenBeta GraphQL client (verify the live schema first)

**Files:**
- Create: `backend/app/pipelines/openbeta.py`, `backend/tests/test_openbeta.py`, `backend/tests/fixtures/openbeta_bulk_sample.json`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `OPENBETA_URL = "https://api.openbeta.io/"`, `class ObClimbType(BaseModel)` (booleans `trad, sport, bouldering, deepwatersolo, alpine, snow, ice, mixed, aid, tr`), `class ObGrades(BaseModel)` (`yds, vscale, wi, french, uiaa, ewbank: str | None`), `class ObClimb(BaseModel)` (`uuid: uuid.UUID`, `name: str`, `length: float | None`, `boltsCount: int | None`, `type: ObClimbType`, `grades: ObGrades | None`, `mp_id: int | None`, `pitch_count: int | None`), `class ObArea(BaseModel)` (`uuid`, `name`, `path_tokens: list[str]`, `ancestors: list[uuid.UUID]`, `lat`, `lng: float | None`, `leaf: bool`, `is_boulder: bool`, `mp_id: int | None`, `climbs: list[ObClimb]`), `class OpenBetaClient(transport=None, page_size=500)` with `us_states() -> list[tuple[uuid.UUID, str]]` and `bulk_areas(state_uuid: uuid.UUID) -> list[ObArea]`, `class OpenBetaError(Exception)`.

- [ ] **Step 1: Verify the live API shape (agent, read-only, no credentials)**

```bash
curl -s https://api.openbeta.io/ -H 'content-type: application/json' \
  -d '{"query":"{ __type(name: \"Query\") { fields { name args { name type { name kind ofType { name } } } } } }"}' \
  | python3 -m json.tool | grep -E '"name": "(area|areas|bulkAreas|ancestors|filter|limit|offset)"'
curl -s https://api.openbeta.io/ -H 'content-type: application/json' \
  -d '{"query":"{ __type(name: \"Area\") { fields { name } } }"}' | python3 -m json.tool | grep '"name"'
```

Expected: `bulkAreas` with an `ancestors` argument, and `Area` fields including `uuid`, `areaName`, `pathTokens`, `ancestors`, `metadata`, `climbs`. If `bulkAreas` has no `limit`/`offset`, drop them from `BULK_QUERY` below and fetch each state in one call; if a field name differs, change only the query text and the matching `Field(alias=…)` below, and note the change in the PR.

- [ ] **Step 2: Record the fixture** (CC0; media never requested)

Run `BULK_QUERY` (below) for one small state area (e.g. the first child of Delaware from `us_states()`), save as `backend/tests/fixtures/openbeta_bulk_sample.json`, then **replace every `mp_id` value with a synthetic id ≥ 900000000** (the fixture must not carry real MP ids; Task 8's guard enforces it). Keep 2–3 areas and ≤10 climbs.

- [ ] **Step 3: Failing tests** — `backend/tests/test_openbeta.py`:

```python
import json
from pathlib import Path

import httpx
import pytest

from app.pipelines.openbeta import OpenBetaClient, OpenBetaError

FIXTURE = Path(__file__).parent / "fixtures" / "openbeta_bulk_sample.json"


def test_fixture_parses_into_areas_and_climbs():
    body = json.loads(FIXTURE.read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    areas = OpenBetaClient(transport=httpx.MockTransport(handler), page_size=10_000).bulk_areas(
        __import__("uuid").UUID(int=1)
    )
    assert areas and all(a.name for a in areas)
    assert any(a.climbs for a in areas)
    assert all(a.lat is None or -90 <= a.lat <= 90 for a in areas)


def test_query_never_requests_media():
    from app.pipelines import openbeta

    assert "media" not in openbeta.BULK_QUERY.lower()


def test_graphql_errors_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "bad field"}]})

    with pytest.raises(OpenBetaError, match="bad field"):
        OpenBetaClient(transport=httpx.MockTransport(handler)).us_states()


def test_ancestors_accept_comma_string_or_list():
    from app.pipelines.openbeta import ObArea

    base = {"uuid": "00000000-0000-0000-0000-000000000001", "areaName": "A", "pathTokens": ["USA", "A"],
            "metadata": {"lat": 40.0, "lng": -105.0, "leaf": True, "isBoulder": False, "mp_id": None}, "climbs": []}
    ids = "00000000-0000-0000-0000-000000000009,00000000-0000-0000-0000-000000000001"
    assert len(ObArea.model_validate(base | {"ancestors": ids}).ancestors) == 2
    assert len(ObArea.model_validate(base | {"ancestors": ids.split(",")}).ancestors) == 2
```

- [ ] **Step 4: Run to verify failure** — FAIL.

- [ ] **Step 5: Implement** `backend/app/pipelines/openbeta.py`:

```python
"""OpenBeta GraphQL (CC0). Weekly bulk load by US state (D11). Never requests media."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
from pydantic import AliasPath, BaseModel, ConfigDict, Field, ValidationError, field_validator

OPENBETA_URL = "https://api.openbeta.io/"
STATES_QUERY = """
query { areas(filter: {area_name: {match: "USA", exactMatch: true}}) { uuid children { uuid areaName } } }
"""
BULK_QUERY = """
query Bulk($ancestors: [String!]!, $limit: Int, $offset: Int) {
  bulkAreas(ancestors: $ancestors, limit: $limit, offset: $offset) {
    uuid areaName pathTokens ancestors
    metadata { lat lng leaf isBoulder mp_id }
    climbs {
      uuid name length boltsCount
      type { trad sport bouldering deepwatersolo alpine snow ice mixed aid tr }
      grades { yds vscale wi french uiaa ewbank }
      metadata { mp_id }
      pitches { pitchNumber }
    }
  }
}
"""


class OpenBetaError(Exception):
    pass


class ObClimbType(BaseModel):
    model_config = ConfigDict(extra="ignore")
    trad: bool = False
    sport: bool = False
    bouldering: bool = False
    deepwatersolo: bool = False
    alpine: bool = False
    snow: bool = False
    ice: bool = False
    mixed: bool = False
    aid: bool = False
    tr: bool = False

    def flags(self) -> set[str]:
        return {k for k, v in self.model_dump().items() if v}


class ObGrades(BaseModel):
    model_config = ConfigDict(extra="ignore")
    yds: str | None = None
    vscale: str | None = None
    wi: str | None = None
    french: str | None = None
    uiaa: str | None = None
    ewbank: str | None = None

    def all(self) -> list[str | None]:
        return [self.yds, self.wi, self.vscale, self.french, self.uiaa, self.ewbank]


class ObClimb(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    uuid: uuid.UUID
    name: str
    length: float | None = None
    bolts_count: int | None = Field(default=None, alias="boltsCount")
    type: ObClimbType = Field(default_factory=ObClimbType)
    grades: ObGrades | None = None
    mp_id: int | None = Field(default=None, validation_alias=AliasPath("metadata", "mp_id"))
    pitches: list[dict[str, Any]] | None = None

    @field_validator("mp_id", mode="before")
    @classmethod
    def _mp_id(cls, value: object) -> object:
        return int(value) if isinstance(value, str) and value.isdigit() else (None if isinstance(value, str) else value)

    @property
    def pitch_count(self) -> int | None:
        return len(self.pitches) if self.pitches else None


class ObArea(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    uuid: uuid.UUID
    name: str = Field(alias="areaName")
    path_tokens: list[str] = Field(default_factory=list, alias="pathTokens")
    ancestors: list[uuid.UUID] = Field(default_factory=list)
    lat: float | None = Field(default=None, validation_alias=AliasPath("metadata", "lat"))
    lng: float | None = Field(default=None, validation_alias=AliasPath("metadata", "lng"))
    leaf: bool = Field(default=False, validation_alias=AliasPath("metadata", "leaf"))
    is_boulder: bool = Field(default=False, validation_alias=AliasPath("metadata", "isBoulder"))
    mp_id: int | None = Field(default=None, validation_alias=AliasPath("metadata", "mp_id"))
    climbs: list[ObClimb] = Field(default_factory=list)

    @field_validator("ancestors", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        return [v for v in value.split(",") if v] if isinstance(value, str) else value

    @field_validator("mp_id", mode="before")
    @classmethod
    def _mp_id(cls, value: object) -> object:
        return int(value) if isinstance(value, str) and value.isdigit() else (None if isinstance(value, str) else value)


class OpenBetaClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, page_size: int = 500, pause_s: float = 0.5) -> None:
        self._client = httpx.Client(transport=transport, timeout=120.0, headers={"User-Agent": "SafeAscent data pipeline"})
        self._page = page_size
        self._pause = pause_s if transport is None else 0.0

    def _post(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._client.post(OPENBETA_URL, json={"query": query, "variables": variables or {}})
        except httpx.HTTPError as exc:
            raise OpenBetaError(f"transport error: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise OpenBetaError(f"HTTP {response.status_code}")
        body = response.json()
        if body.get("errors"):
            raise OpenBetaError("; ".join(str(e.get("message")) for e in body["errors"]))
        data: dict[str, Any] = body["data"]
        return data

    def us_states(self) -> list[tuple[uuid.UUID, str]]:
        data = self._post(STATES_QUERY)
        roots = data.get("areas") or []
        if len(roots) != 1:
            raise OpenBetaError(f"expected one USA root area, got {len(roots)}")
        return [(uuid.UUID(c["uuid"]), str(c["areaName"])) for c in roots[0]["children"]]

    def bulk_areas(self, state_uuid: uuid.UUID) -> list[ObArea]:
        out: list[ObArea] = []
        offset = 0
        while True:
            data = self._post(BULK_QUERY, {"ancestors": [str(state_uuid)], "limit": self._page, "offset": offset})
            page = data.get("bulkAreas") or []
            try:
                out += [ObArea.model_validate(a) for a in page]
            except ValidationError as exc:
                raise OpenBetaError(f"invalid area: {exc.errors()[0]['msg']}") from None
            if len(page) < self._page:
                return out
            offset += self._page
            time.sleep(self._pause)
```

Append `"app.pipelines.openbeta"` to strict mypy.

- [ ] **Step 6: Run** — `cd backend && uv run pytest tests/test_openbeta.py -q && uv run mypy` → PASS.
- [ ] **Step 7: Commit** — `git add backend/app/pipelines/openbeta.py backend/tests/test_openbeta.py backend/tests/fixtures/openbeta_bulk_sample.json backend/pyproject.toml && git commit -m "feat(pipelines): OpenBeta GraphQL client (CC0, no media) with recorded fixture"`

---

### Task 4: Catalog loader, weekly workflow, job pings

**Files:**
- Create: `backend/app/pipelines/catalog.py`, `backend/app/pipelines/jobping.py`, `backend/tests/test_catalog.py`, `.github/workflows/data-openbeta.yml`
- Modify: `backend/app/config.py`, `.env.example`, `backend/pyproject.toml`

**Interfaces:**
- Consumes: Tasks 1–3; `validate`, `ingest_log` (plan 1).
- Produces: `SOURCE = "openbeta_weekly"`, `MAX_DROP = 0.03`, `ltree_label(u: uuid.UUID) -> str`, `@dataclass CatalogBatch(areas: list[AreaRow], routes: list[RouteRow], area_mp_links: list[tuple[uuid.UUID, int]], route_mp_links: list[tuple[uuid.UUID, int]])`, `build_batch(areas: list[ObArea], report: ValidationReport) -> CatalogBatch`, `async upsert_batch(conn, batch, *, run_id) -> int`, `async retire_unseen(conn, *, run_id) -> int`, `async run_weekly(engine_factory, client, *, max_drop: float = MAX_DROP) -> dict[str, object]`; `jobping.job_ping(slug: str, suffix: PingSuffix = "") -> bool`; `Settings.HEALTHCHECKS_PING_KEY: str | None = None`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_catalog.py`:

```python
import asyncio
import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.catalog import build_batch, ltree_label, run_weekly
from app.pipelines.openbeta import ObArea, OpenBetaClient
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url

STATE = uuid.UUID(int=100)
CRAG = uuid.UUID(int=101)
FOREIGN = uuid.UUID(int=102)


def area(u, name, lat, lng, ancestors, climbs=(), mp_id=None):
    return ObArea.model_validate({
        "uuid": str(u), "areaName": name, "pathTokens": ["USA", name], "ancestors": [str(a) for a in ancestors],
        "metadata": {"lat": lat, "lng": lng, "leaf": bool(climbs), "isBoulder": False, "mp_id": mp_id},
        "climbs": list(climbs),
    })


def climb(u, name, flags, grade=None, mp_id=None):
    return {"uuid": str(u), "name": name, "boltsCount": 0, "type": {f: True for f in flags},
            "grades": {"yds": grade}, "metadata": {"mp_id": mp_id}}


def test_ltree_label_is_hex():
    assert ltree_label(uuid.UUID(int=1)) == "0" * 31 + "1"


def test_foreign_areas_are_quarantined_with_their_climbs():
    report = ValidationReport("t")
    batch = build_batch([
        area(STATE, "Colorado", None, None, [STATE]),
        area(CRAG, "Fixture Crag", 40.0, -105.3, [STATE, CRAG], [climb(uuid.UUID(int=201), "Fixture Route", ["trad"], "5.9", 900000001)]),
        area(FOREIGN, "Over The Line", 50.5, -115.0, [STATE, FOREIGN], [climb(uuid.UUID(int=202), "Far", ["sport"])]),
    ], report)
    assert [a.area_id for a in batch.areas] == [STATE, CRAG]
    assert [r.route_id for r in batch.routes] == [uuid.UUID(int=201)]
    assert batch.routes[0].type_group == "trad" and batch.routes[0].scored is True
    assert batch.route_mp_links == [(uuid.UUID(int=201), 900000001)]
    assert report.quarantined == {"outside_us": 1, "parent_quarantined": 1}
    assert batch.areas[1].path == f"{ltree_label(STATE)}.{ltree_label(CRAG)}"


def test_routes_in_areas_without_coordinates_are_not_scored():
    report = ValidationReport("t")
    batch = build_batch([area(STATE, "Colorado", None, None, [STATE], [climb(uuid.UUID(int=203), "R", ["sport"])])], report)
    assert batch.routes[0].scored is False


def _client(states_ok: bool) -> OpenBetaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        if "USA" in body:
            return httpx.Response(200, json={"data": {"areas": [{"uuid": str(uuid.UUID(int=99)), "children": [
                {"uuid": str(STATE), "areaName": "Colorado"}, {"uuid": str(uuid.UUID(int=98)), "areaName": "Utah"}]}]}})
        if str(uuid.UUID(int=98)) in body and not states_ok:
            return httpx.Response(502)
        bulk = [
            {"uuid": str(STATE), "areaName": "Colorado", "pathTokens": ["USA", "Colorado"], "ancestors": [str(STATE)],
             "metadata": {"lat": None, "lng": None, "leaf": False, "isBoulder": False}, "climbs": []},
            {"uuid": str(CRAG), "areaName": "Crag", "pathTokens": ["USA", "Colorado", "Crag"],
             "ancestors": [str(STATE), str(CRAG)], "metadata": {"lat": 40.0, "lng": -105.3, "leaf": True, "isBoulder": False},
             "climbs": [climb(uuid.UUID(int=201), "R", ["trad"])]},
        ] if str(STATE) in body else []
        return httpx.Response(200, json={"data": {"bulkAreas": bulk}})

    return OpenBetaClient(transport=httpx.MockTransport(handler), page_size=500)


@requires_pg
def test_partial_failure_retires_nothing():
    async def scenario(url: str) -> tuple[dict[str, object], dict[str, object], int]:
        def factory():
            return create_async_engine(url)

        ok = await run_weekly(factory, _client(states_ok=True))
        failed = await run_weekly(factory, _client(states_ok=False))
        engine = factory()
        try:
            async with engine.connect() as conn:
                retired = (await conn.execute(text("SELECT count(*) FROM canonical_routes WHERE retired_at IS NOT NULL"))).scalar_one()
        finally:
            await engine.dispose()
        return ok, failed, int(retired)

    with migrated_db() as name:
        ok, failed, retired = asyncio.run(scenario(sa_url(name)))
    assert ok["status"] == "ok" and ok["routes"] == 1
    assert failed["status"] == "failed"
    assert retired == 0
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement**

`backend/app/pipelines/jobping.py`:

```python
"""healthchecks.io pings by slug under one ping key, one check per data job."""

from __future__ import annotations

from app.config import settings
from app.healthchecks import PingSuffix, ping


def job_ping(slug: str, suffix: PingSuffix = "") -> bool:
    key = settings.HEALTHCHECKS_PING_KEY
    return ping(f"https://hc-ping.com/{key}/{slug}" if key else None, suffix)
```

`backend/app/config.py`: add `HEALTHCHECKS_PING_KEY: str | None = None` after `HEALTHCHECKS_BEAT_URL` (comment: `# One key for all data-job checks; each job pings /<key>/<slug> (plan 4).`). `.env.example`: `HEALTHCHECKS_PING_KEY=` in the monitoring section.

`backend/app/pipelines/catalog.py`:

```python
"""OpenBeta → canonical_areas / canonical_routes. A full weekly reload by state: any state
failing makes the run `failed` and retires nothing; a >3% drop in US climbs rejects it.
OpenBeta mp_id cross-references go only to internal link tables."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.ingest_log import finish_run, last_ok_rows_in, start_run, write_quarantine
from app.pipelines.openbeta import ObArea, OpenBetaClient, OpenBetaError
from app.pipelines.route_types import TYPE_RULE_VERSION, map_type_group
from app.pipelines.validate import ValidationReport, coord_problem

SOURCE = "openbeta_weekly"
MAX_DROP = 0.03


def ltree_label(u: uuid.UUID) -> str:
    return u.hex


@dataclass(frozen=True)
class AreaRow:
    area_id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    path: str
    lat: float | None
    lon: float | None
    coord_precision: str
    is_boulder_area: bool


@dataclass(frozen=True)
class RouteRow:
    route_id: uuid.UUID
    area_id: uuid.UUID
    name: str
    grade: str | None
    disciplines: list[str]
    type_group: str | None
    is_boulder: bool
    scored: bool
    pitches: int | None
    length_m: float | None
    bolts: int | None


@dataclass
class CatalogBatch:
    areas: list[AreaRow] = field(default_factory=list)
    routes: list[RouteRow] = field(default_factory=list)
    area_mp_links: list[tuple[uuid.UUID, int]] = field(default_factory=list)
    route_mp_links: list[tuple[uuid.UUID, int]] = field(default_factory=list)


def build_batch(areas: list[ObArea], report: ValidationReport) -> CatalogBatch:
    batch = CatalogBatch()
    kept: set[uuid.UUID] = set()
    dropped: set[uuid.UUID] = set()
    for a in sorted(areas, key=lambda x: len(x.ancestors)):
        chain = [u for u in a.ancestors if u != a.uuid]
        parent = chain[-1] if chain else None
        if parent is not None and parent in dropped:
            report.quarantine(str(a.uuid), "parent_quarantined")
            dropped.add(a.uuid)
            continue
        if a.lat is not None or a.lng is not None:
            problem = coord_problem(a.lat, a.lng)
            if problem is not None:
                report.quarantine(str(a.uuid), problem)
                dropped.add(a.uuid)
                continue
        if not a.name.strip():
            report.quarantine(str(a.uuid), "blank_name")
            dropped.add(a.uuid)
            continue
        report.accept()
        kept.add(a.uuid)
        path = ".".join(ltree_label(u) for u in [*chain, a.uuid])
        has_coords = a.lat is not None and a.lng is not None
        batch.areas.append(AreaRow(a.uuid, a.name.strip(), parent if parent in kept else None, path, a.lat, a.lng,
                                   "area_centroid" if has_coords else "none", a.is_boulder))
        if a.mp_id is not None:
            batch.area_mp_links.append((a.uuid, a.mp_id))
        for c in a.climbs:
            result = map_type_group(c.type.flags(), c.grades.all() if c.grades else [], c.bolts_count)
            grade = next((g for g in (c.grades.all() if c.grades else []) if g), None)
            batch.routes.append(RouteRow(
                c.uuid, a.uuid, c.name.strip(), grade, list(result.disciplines), result.type_group, result.is_boulder,
                (not result.is_boulder) and has_coords, c.pitch_count,
                c.length if c.length is not None and c.length > 0 else None,
                c.bolts_count if c.bolts_count is not None and c.bolts_count >= 0 else None,
            ))
            if c.mp_id is not None:
                batch.route_mp_links.append((c.uuid, c.mp_id))
    return batch


AREA_UPSERT = text(
    "INSERT INTO canonical_areas (area_id, name, parent_id, path, lat, lon, geom, ob_area_uuid, coord_precision, "
    "source, redistributable, is_boulder_area, retired_at, updated_at, run_id) VALUES (:area_id, :name, :parent_id, "
    "CAST(:path AS ltree), :lat, :lon, CASE WHEN CAST(:lat AS float8) IS NULL THEN NULL ELSE "
    "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography END, :area_id, :coord_precision, 'openbeta', true, "
    ":is_boulder_area, NULL, now(), :run_id) ON CONFLICT (area_id) DO UPDATE SET name = EXCLUDED.name, "
    "parent_id = EXCLUDED.parent_id, path = EXCLUDED.path, lat = EXCLUDED.lat, lon = EXCLUDED.lon, geom = EXCLUDED.geom, "
    "coord_precision = EXCLUDED.coord_precision, is_boulder_area = EXCLUDED.is_boulder_area, retired_at = NULL, "
    "updated_at = now(), run_id = EXCLUDED.run_id"
)
ROUTE_UPSERT = text(
    "INSERT INTO canonical_routes (route_id, area_id, name, grade, disciplines, type_group, type_rule_version, "
    "is_boulder, scored, pitches, length_m, bolts, ob_climb_uuid, source, redistributable, retired_at, updated_at, run_id) "
    "VALUES (:route_id, :area_id, :name, :grade, :disciplines, :type_group, :trv, :is_boulder, :scored, :pitches, "
    ":length_m, :bolts, :route_id, 'openbeta', true, NULL, now(), :run_id) ON CONFLICT (route_id) DO UPDATE SET "
    "area_id = EXCLUDED.area_id, name = EXCLUDED.name, grade = EXCLUDED.grade, disciplines = EXCLUDED.disciplines, "
    "type_group = EXCLUDED.type_group, type_rule_version = EXCLUDED.type_rule_version, is_boulder = EXCLUDED.is_boulder, "
    "scored = EXCLUDED.scored, pitches = EXCLUDED.pitches, length_m = EXCLUDED.length_m, bolts = EXCLUDED.bolts, "
    "retired_at = NULL, updated_at = now(), run_id = EXCLUDED.run_id"
)


async def upsert_batch(conn: AsyncConnection, batch: CatalogBatch, *, run_id: uuid.UUID) -> int:
    if batch.areas:
        await conn.execute(AREA_UPSERT, [asdict(a) | {"run_id": run_id} for a in batch.areas])
    if batch.routes:
        await conn.execute(ROUTE_UPSERT, [asdict(r) | {"run_id": run_id, "trv": TYPE_RULE_VERSION} for r in batch.routes])
    for table, key, mp_col, links in (
        ("internal.mp_area_links", "area_id", "mp_location_id", batch.area_mp_links),
        ("internal.mp_route_links", "route_id", "mp_route_id", batch.route_mp_links),
    ):
        if links:
            await conn.execute(
                text(f"INSERT INTO {table} ({key}, {mp_col}, match_score, match_method) VALUES (:k, :m, 1.0, 'ob_mp_id') "
                     f"ON CONFLICT ({key}, {mp_col}) DO UPDATE SET match_score = 1.0, match_method = 'ob_mp_id'"),
                [{"k": k, "m": m} for k, m in links],
            )
    return len(batch.areas) + len(batch.routes)


async def retire_unseen(conn: AsyncConnection, *, run_id: uuid.UUID) -> int:
    routes = await conn.execute(text(
        "UPDATE canonical_routes SET retired_at = now() WHERE source = 'openbeta' AND retired_at IS NULL "
        "AND run_id IS DISTINCT FROM :r"), {"r": run_id})
    areas = await conn.execute(text(
        "UPDATE canonical_areas SET retired_at = now() WHERE source = 'openbeta' AND retired_at IS NULL "
        "AND run_id IS DISTINCT FROM :r"), {"r": run_id})
    return routes.rowcount + areas.rowcount


async def run_weekly(
    engine_factory: Callable[[], AsyncEngine], client: OpenBetaClient, *, max_drop: float = MAX_DROP
) -> dict[str, object]:
    report = ValidationReport(SOURCE)
    all_areas: list[ObArea] = []
    problems: list[str] = []
    try:
        for state_uuid, state_name in client.us_states():
            try:
                all_areas += client.bulk_areas(state_uuid)
            except OpenBetaError as exc:
                problems.append(f"{state_name}: {exc}")
    except OpenBetaError as exc:
        problems.append(f"states: {exc}")
    batch = build_batch(all_areas, report) if not problems else CatalogBatch()
    engine = engine_factory()
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=None)
            if problems:
                await finish_run(conn, run_id, status="failed", report=report, rows_upserted=0, problems=problems)
                return {"status": "failed", "problems": problems}
            previous = await last_ok_rows_in(conn, SOURCE)
            climbs = len(batch.routes)
            if previous and climbs < previous * (1 - max_drop):
                msg = f"US climbs {climbs} dropped more than {max_drop:.0%} from {previous}"
                await finish_run(conn, run_id, status="rejected", report=report, rows_upserted=0, problems=[msg])
                return {"status": "rejected", "problems": [msg]}
            await write_quarantine(conn, run_id, report)
            n = await upsert_batch(conn, batch, run_id=run_id)
            retired = await retire_unseen(conn, run_id=run_id)
            # rows_in carries the climb count so the next run's drop check compares like with like.
            report.rows_in = climbs
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n)
            return {"status": "ok", "areas": len(batch.areas), "routes": climbs, "retired": retired,
                    "quarantined": report.quarantined_total()}
    finally:
        await engine.dispose()


async def _main() -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.pipelines.jobping import job_ping

    job_ping("openbeta-weekly", "/start")
    try:
        result = await run_weekly(ingest_engine, OpenBetaClient())
    except Exception:
        job_ping("openbeta-weekly", "/fail")
        raise
    job_ping("openbeta-weekly", "" if result["status"] == "ok" else "/fail")
    return result


if __name__ == "__main__":
    result = asyncio.run(_main())
    print(json.dumps(result, sort_keys=True, default=str))
    raise SystemExit(0 if result["status"] == "ok" else 1)
```

`.github/workflows/data-openbeta.yml`:

```yaml
name: data-openbeta

on:
  schedule:
    - cron: "17 6 * * 1"   # Mondays 06:17 UTC
  workflow_dispatch:

permissions:
  contents: read
  issues: write

jobs:
  load:
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
      - name: Weekly OpenBeta load (counts only in logs)
        run: uv run python -m app.pipelines.catalog
      - name: Open an issue on failure
        if: failure()
        working-directory: .
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh issue create --title "data-openbeta failed ($(date -u +%F))" --body "Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

Append `"app.pipelines.catalog", "app.pipelines.jobping"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_catalog.py tests/test_env_example_parity.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/catalog.py backend/app/pipelines/jobping.py backend/app/config.py .env.example backend/tests/test_catalog.py .github/workflows/data-openbeta.yml backend/pyproject.toml && git commit -m "feat(pipelines): weekly OpenBeta catalog load with drop guard, retirement, job pings"`

---

### Task 5: Matcher scoring and decisions (pure)

**Files:**
- Create: `backend/app/pipelines/match.py`, `backend/tests/test_match.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: `textsim.jaro_winkler`, `textsim.place_key`, `textsim.normalize_text`, `geo.haversine_km` (plan 2 Task 5).
- Produces: `AUTO_LINK = 0.90`, `NO_LINK_BELOW = 0.80`, `AREA_RADIUS_KM = 2.0`, `area_score(mp_name: str, mp_tokens: Sequence[str], mp_lat: float, mp_lon: float, ob_name: str, ob_tokens: Sequence[str], ob_lat: float, ob_lon: float) -> float`, `grade_similarity(a: str | None, b: str | None) -> float`, `climb_score(mp_name: str, mp_grade: str | None, mp_type: str | None, ob_name: str, ob_grade: str | None, ob_type: str | None) -> float`, `Decision = Literal["link", "no_link", "review"]`, `decide(scores: Sequence[tuple[uuid.UUID, float]]) -> tuple[Decision, uuid.UUID | None, float]`, `h3_neighbourhood(lat: float, lon: float, res: int = 7) -> set[str]`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_match.py`:

```python
import uuid

import pytest

from app.pipelines.match import area_score, climb_score, decide, grade_similarity, h3_neighbourhood

A, B = uuid.UUID(int=1), uuid.UUID(int=2)


def test_area_score_components():
    # "USA" is dropped from path tokens, so identical paths overlap fully.
    same = area_score("Lumpy Ridge", ["Colorado", "RMNP", "Lumpy Ridge"], 40.39, -105.51,
                      "Lumpy Ridge", ["USA", "Colorado", "RMNP", "Lumpy Ridge"], 40.39, -105.51)
    assert same == pytest.approx(1.0)
    far = area_score("Lumpy Ridge", ["Lumpy Ridge"], 40.39, -105.51, "Lumpy Ridge", ["Lumpy Ridge"], 40.50, -105.51)
    assert far == pytest.approx(0.8)


def test_grade_similarity():
    assert grade_similarity("5.10a", "5.10a") == 1.0
    assert grade_similarity("5.10a", "5.10c") == 0.5
    assert grade_similarity("WI4", "WI4+") == 0.5
    assert grade_similarity("5.9", "5.11a") == 0.0
    assert grade_similarity(None, "5.9") == 0.0


def test_climb_score():
    assert climb_score("The Book", "5.7", "trad", "The Book", "5.7", "trad") == pytest.approx(1.0)
    assert climb_score("The Book", "5.7", "trad", "Book", "5.8", "sport") < 0.8


def test_decisions():
    assert decide([(A, 0.95), (B, 0.60)]) == ("link", A, 0.95)
    assert decide([(A, 0.70)]) == ("no_link", None, 0.70)
    assert decide([(A, 0.85)]) == ("review", A, 0.85)
    assert decide([]) == ("no_link", None, 0.0)


def test_two_strong_candidates_go_to_review():
    assert decide([(A, 0.95), (B, 0.93)]) == ("review", A, 0.95)


def test_h3_neighbourhood_has_the_cell_and_its_ring():
    cells = h3_neighbourhood(40.0, -105.3)
    assert len(cells) == 7
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/match.py` (pure part; Task 6 appends the job):

```python
"""In-house matcher: MP areas/routes → canonical OpenBeta records (spec §Matching).

Areas: 0.5·Jaro-Winkler(name) + 0.3·path-token overlap + 0.2·distance decay to 0 at 2 km,
among areas in the same or a neighbouring H3 r7 cell. Climbs within matched areas:
0.6·name + 0.25·grade + 0.15·type group. ≥0.90 and unique links; <0.80 does not; the band
between goes to the owner.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Literal

import h3

from app.pipelines.geo import haversine_km
from app.pipelines.textsim import jaro_winkler, normalize_text, place_key

AUTO_LINK = 0.90
NO_LINK_BELOW = 0.80
AREA_RADIUS_KM = 2.0
Decision = Literal["link", "no_link", "review"]
_GRADE_BASE = re.compile(r"^(.*?\d+)[abcd+\-/]*$")


def _tokens(tokens: Sequence[str]) -> set[str]:
    return {normalize_text(t) for t in tokens if t and normalize_text(t) not in ("usa", "united states")}


def area_score(
    mp_name: str, mp_tokens: Sequence[str], mp_lat: float, mp_lon: float,
    ob_name: str, ob_tokens: Sequence[str], ob_lat: float, ob_lon: float,
) -> float:
    name = jaro_winkler(place_key(mp_name), place_key(ob_name))
    a, b = _tokens(mp_tokens), _tokens(ob_tokens)
    overlap = len(a & b) / len(a | b) if a | b else 0.0
    decay = max(0.0, 1.0 - haversine_km(mp_lat, mp_lon, ob_lat, ob_lon) / AREA_RADIUS_KM)
    return 0.5 * name + 0.3 * overlap + 0.2 * decay


def _norm_grade(g: str | None) -> str:
    return re.sub(r"\s+", "", (g or "").lower())


def grade_similarity(a: str | None, b: str | None) -> float:
    x, y = _norm_grade(a), _norm_grade(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    bx, by = _GRADE_BASE.match(x), _GRADE_BASE.match(y)
    return 0.5 if bx and by and bx.group(1) == by.group(1) else 0.0


def climb_score(
    mp_name: str, mp_grade: str | None, mp_type: str | None, ob_name: str, ob_grade: str | None, ob_type: str | None
) -> float:
    name = jaro_winkler(normalize_text(mp_name), normalize_text(ob_name))
    same_type = 1.0 if mp_type and ob_type and mp_type == ob_type else 0.0
    return 0.6 * name + 0.25 * grade_similarity(mp_grade, ob_grade) + 0.15 * same_type


def decide(scores: Sequence[tuple[uuid.UUID, float]]) -> tuple[Decision, uuid.UUID | None, float]:
    if not scores:
        return "no_link", None, 0.0
    ranked = sorted(scores, key=lambda s: s[1], reverse=True)
    best_id, best = ranked[0]
    if best < NO_LINK_BELOW:
        return "no_link", None, best
    strong = [s for s in ranked if s[1] >= AUTO_LINK]
    if best >= AUTO_LINK and len(strong) == 1:
        return "link", best_id, best
    return "review", best_id, best


def h3_neighbourhood(lat: float, lon: float, res: int = 7) -> set[str]:
    return set(h3.grid_disk(h3.latlng_to_cell(lat, lon, res), 1))
```

The second test case: same name, same tokens, 12 km apart → `0.5 + 0.3 + 0 = 0.8`.

Append `"app.pipelines.match"` to strict mypy.

- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/match.py backend/tests/test_match.py backend/pyproject.toml && git commit -m "feat(pipelines): matcher scoring and auto-decision bands"`

---

### Task 6: Matcher job, owner review round-trip, precision evaluation

**Files:**
- Modify: `backend/app/pipelines/match.py`, `backend/tests/test_match.py`

**Interfaces:**
- Produces: `async run_match(conn, *, apply: bool, review_out: Path) -> dict[str, object]` (areas first, then climbs within matched areas; writes `internal.match_decisions` for auto decisions, `internal.mp_area_links`/`mp_route_links` for links with method `auto`/`owner`, never touching `ob_mp_id` rows), `async import_review(conn, path: Path) -> int`, `precision_at_auto(predicted: Mapping[tuple[int, uuid.UUID], Decision], golden: Mapping[tuple[int, uuid.UUID], bool]) -> float | None`, CLI `python -m app.pipelines.match run [--apply] [--out PATH] | import --review-file PATH | eval --golden PATH`.

- [ ] **Step 1: Failing tests** — append to `backend/tests/test_match.py`:

```python
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.match import precision_at_auto, run_match
from tests.pgtest import migrated_db, requires_pg, sa_url


def test_precision_counts_only_auto_links():
    predicted = {(1, A): "link", (2, B): "link", (3, A): "review", (4, B): "no_link"}
    golden = {(1, A): True, (2, B): False, (3, A): True, (4, B): True}
    assert precision_at_auto(predicted, golden) == 0.5
    assert precision_at_auto({(9, A): "review"}, golden) is None


SEED = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), (900000101, 'Fixture Crag', 900000100, 40.0, -105.3);
INSERT INTO mp_routes (mp_route_id, name, location_id, grade, type) VALUES
  (900000001, 'Fixture Route', 900000101, '5.9', 'Trad'),
  (900000002, 'Unmatched Line', 900000101, '5.12a', 'Sport');
INSERT INTO canonical_areas (area_id, name, path, lat, lon, coord_precision, source, redistributable) VALUES
  ('00000000-0000-0000-0000-00000000000a', 'Fixture Crag', '0000000000000000000000000000000a', 40.0, -105.3, 'area_centroid', 'openbeta', true);
INSERT INTO canonical_routes (route_id, area_id, name, grade, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable) VALUES
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-00000000000a', 'Fixture Route', '5.9', '{trad}', 'trad', 'rt-v1', false, true, 'openbeta', true);
"""


@requires_pg
def test_run_links_areas_then_climbs_and_leaves_no_match_alone(tmp_path):
    async def scenario(url: str) -> tuple[dict[str, object], list[tuple[int, str]]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                result = await run_match(conn, apply=True, review_out=tmp_path / "review.csv")
                links = [(int(m), str(meth)) for m, meth in (await conn.execute(text(
                    "SELECT mp_route_id, match_method FROM internal.mp_route_links ORDER BY 1"))).all()]
            return result, links
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        result, links = asyncio.run(scenario(sa_url(name)))
    assert links == [(900000001, "auto")]
    assert result["climbs"] == {"link": 1, "no_link": 1, "review": 0}
```

- [ ] **Step 2: Run to verify failure** — FAIL (`ImportError: precision_at_auto`).

- [ ] **Step 3: Implement** — append to `backend/app/pipelines/match.py` (add imports `argparse, asyncio, csv, json`, `from collections import Counter, defaultdict`, `from collections.abc import Mapping`, `from pathlib import Path`, `from sqlalchemy import text`, `from sqlalchemy.ext.asyncio import AsyncConnection`, `from app.pipelines.route_types import flags_from_mp_type, map_type_group` at the top):

```python
def precision_at_auto(
    predicted: Mapping[tuple[int, uuid.UUID], Decision], golden: Mapping[tuple[int, uuid.UUID], bool]
) -> float | None:
    judged = [golden[k] for k, d in predicted.items() if d == "link" and k in golden]
    return sum(judged) / len(judged) if judged else None


async def _mp_areas(conn: AsyncConnection) -> dict[int, tuple[str, list[str], float, float]]:
    rows = {int(i): (str(n), p, la, lo) for i, n, p, la, lo in (await conn.execute(text(
        "SELECT mp_id, name, parent_id, latitude, longitude FROM mp_locations"))).all()}
    out: dict[int, tuple[str, list[str], float, float]] = {}
    for mp_id, (name, _, lat, lon) in rows.items():
        if lat is None or lon is None:
            continue
        chain, cursor, seen = [], mp_id, set()
        while cursor is not None and cursor in rows and cursor not in seen:
            seen.add(cursor)
            chain.append(rows[cursor][0])
            cursor = rows[cursor][1]
        out[mp_id] = (name, list(reversed(chain)), float(lat), float(lon))
    return out


async def run_match(conn: AsyncConnection, *, apply: bool, review_out: Path) -> dict[str, object]:
    mp_areas = await _mp_areas(conn)
    ob_areas = {
        uuid.UUID(str(a)): (str(n), float(la), float(lo))
        for a, n, la, lo in (await conn.execute(text(
            "SELECT area_id, name, lat, lon FROM canonical_areas WHERE lat IS NOT NULL AND retired_at IS NULL"))).all()
    }
    by_cell: dict[str, list[uuid.UUID]] = defaultdict(list)
    for area_id, (_, lat, lon) in ob_areas.items():
        by_cell[h3.latlng_to_cell(lat, lon, 7)].append(area_id)
    ob_paths = {uuid.UUID(str(a)): [str(t) for t in p] for a, p in (await conn.execute(text(
        "SELECT c.area_id, array_agg(p.name ORDER BY nlevel(p.path)) FROM canonical_areas c "
        "JOIN canonical_areas p ON p.path @> c.path GROUP BY c.area_id"))).all()}
    fixed_areas = {int(m) for (m,) in (await conn.execute(text(
        "SELECT mp_location_id FROM internal.mp_area_links WHERE match_method IN ('ob_mp_id', 'owner')"))).all()}
    area_link: dict[int, uuid.UUID] = {}
    counts: dict[str, Counter[str]] = {"areas": Counter(), "climbs": Counter()}
    review: list[list[object]] = []
    for mp_id, (name, tokens, lat, lon) in mp_areas.items():
        if mp_id in fixed_areas:
            continue
        candidates = [a for cell in h3_neighbourhood(lat, lon) for a in by_cell.get(cell, [])]
        scores = [(a, area_score(name, tokens, lat, lon, ob_areas[a][0], ob_paths.get(a, []), ob_areas[a][1], ob_areas[a][2]))
                  for a in candidates]
        verdict, best, score = decide(scores)
        counts["areas"][verdict] += 1
        if verdict == "link" and best is not None:
            area_link[mp_id] = best
        elif verdict == "review" and best is not None:
            review.append(["area", mp_id, best, round(score, 4), name, ob_areas[best][0], ""])
    area_link |= {int(m): uuid.UUID(str(a)) for m, a in (await conn.execute(text(
        "SELECT mp_location_id, area_id FROM internal.mp_area_links WHERE match_method IN ('ob_mp_id', 'owner')"))).all()}
    fixed_routes = {int(m) for (m,) in (await conn.execute(text(
        "SELECT mp_route_id FROM internal.mp_route_links WHERE match_method IN ('ob_mp_id', 'owner', 'mp_facts')"))).all()}
    ob_routes: dict[uuid.UUID, list[tuple[uuid.UUID, str, str | None, str | None]]] = defaultdict(list)
    for r, a, n, g, t in (await conn.execute(text(
            "SELECT route_id, area_id, name, grade, type_group FROM canonical_routes WHERE source = 'openbeta' AND retired_at IS NULL"))).all():
        ob_routes[uuid.UUID(str(a))].append((uuid.UUID(str(r)), str(n), g, t))
    route_link: dict[int, tuple[uuid.UUID, float]] = {}
    for mp_route_id, name, location_id, grade, mp_type in (await conn.execute(text(
            "SELECT mp_route_id, name, location_id, grade, type FROM mp_routes"))).all():
        if int(mp_route_id) in fixed_routes or location_id is None or int(location_id) not in area_link:
            continue
        mp_group = map_type_group(flags_from_mp_type(mp_type), [grade], None).type_group
        scores = [(r, climb_score(str(name), grade, mp_group, n, g, t)) for r, n, g, t in ob_routes[area_link[int(location_id)]]]
        verdict, best, score = decide(scores)
        counts["climbs"][verdict] += 1
        if verdict == "link" and best is not None:
            route_link[int(mp_route_id)] = (best, score)
        elif verdict == "review" and best is not None:
            review.append(["climb", int(mp_route_id), best, round(score, 4), str(name), "", ""])
    if apply:
        await conn.execute(text("DELETE FROM internal.mp_area_links WHERE match_method = 'auto'"))
        await conn.execute(text("DELETE FROM internal.mp_route_links WHERE match_method = 'auto'"))
        if area_link:
            await conn.execute(text(
                "INSERT INTO internal.mp_area_links (area_id, mp_location_id, match_score, match_method) "
                "VALUES (:a, :m, 1.0, 'auto') ON CONFLICT DO NOTHING"),
                [{"a": a, "m": m} for m, a in area_link.items() if m not in fixed_areas])
        if route_link:
            await conn.execute(text(
                "INSERT INTO internal.mp_route_links (route_id, mp_route_id, match_score, match_method) "
                "VALUES (:r, :m, :s, 'auto') ON CONFLICT DO NOTHING"),
                [{"r": r, "m": m, "s": s} for m, (r, s) in route_link.items()])
    if review:
        review_out.parent.mkdir(parents=True, exist_ok=True)
        with review_out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["kind", "mp_id", "ob_uuid", "score", "mp_name", "ob_name", "decision"])
            writer.writerows(review)
    return {
        "areas": {k: counts["areas"][k] for k in ("link", "no_link", "review")},
        "climbs": {k: counts["climbs"][k] for k in ("link", "no_link", "review")},
        "review_rows": len(review),
    }


async def import_review(conn: AsyncConnection, path: Path) -> int:
    n = 0
    with path.open(newline="", encoding="utf-8") as fh:
        for number, row in enumerate(csv.DictReader(fh), start=2):
            decision = (row.get("decision") or "").strip().lower()
            if decision not in ("link", "no_link"):
                raise ValueError(f"{path.name} row {number}: decision must be link or no_link")
            kind, mp_id, ob = row["kind"], int(row["mp_id"]), uuid.UUID(row["ob_uuid"])
            await conn.execute(text(
                "INSERT INTO internal.match_decisions (kind, mp_id, ob_uuid, score, decision, decided_by) "
                "VALUES (:k, :m, :o, :s, :d, 'owner') ON CONFLICT (kind, mp_id, ob_uuid) DO UPDATE SET "
                "decision = EXCLUDED.decision, decided_by = 'owner', decided_at = now()"),
                {"k": kind, "m": mp_id, "o": ob, "s": float(row["score"]), "d": decision})
            if decision == "link":
                table, key, col = (("internal.mp_area_links", "area_id", "mp_location_id") if kind == "area"
                                   else ("internal.mp_route_links", "route_id", "mp_route_id"))
                await conn.execute(text(
                    f"INSERT INTO {table} ({key}, {col}, match_score, match_method) VALUES (:o, :m, :s, 'owner') "
                    f"ON CONFLICT ({key}, {col}) DO UPDATE SET match_method = 'owner', match_score = EXCLUDED.match_score"),
                    {"o": ob, "m": mp_id, "s": float(row["score"])})
            n += 1
    return n


async def _cli(args: argparse.Namespace) -> dict[str, object]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            if args.command == "run":
                return await run_match(conn, apply=args.apply, review_out=Path(args.out))
            if args.command == "import":
                return {"imported": await import_review(conn, Path(args.review_file))}
            golden: dict[tuple[int, uuid.UUID], bool] = {}
            with Path(args.golden).open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    golden[(int(row["mp_route_id"]), uuid.UUID(row["ob_climb_uuid"]))] = row["label"].strip().lower() == "yes"
            links = {(int(m), uuid.UUID(str(r))): "link" for r, m in (await conn.execute(text(
                "SELECT route_id, mp_route_id FROM internal.mp_route_links WHERE match_method = 'auto'"))).all()}
            predicted: dict[tuple[int, uuid.UUID], Decision] = {k: "link" for k in links}
            predicted |= {k: "no_link" for k in golden if k not in links}
            return {"labelled": len(golden), "precision_at_auto": precision_at_auto(predicted, golden)}
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m app.pipelines.match")
    parser.add_argument("command", choices=["run", "import", "eval"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out", default="../data/review/matches.csv")
    parser.add_argument("--review-file")
    parser.add_argument("--golden")
    print(json.dumps(asyncio.run(_cli(parser.parse_args())), sort_keys=True, default=str))
```

`internal.match_decisions` holds owner decisions (and is the audit trail for them); auto links are recomputed on every run from the current catalog, which is why auto rows are deleted and re-inserted while `ob_mp_id`, `owner` and `mp_facts` rows are never touched.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_match.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/match.py backend/tests/test_match.py && git commit -m "feat(pipelines): matcher job with owner review round-trip and precision evaluation"`

---

### Task 7: `mp_facts` — the only MP → public catalog path

**Files:**
- Create: `backend/app/pipelines/mp_facts.py`, `backend/tests/test_mp_facts.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `FACT_ROUTE_COLUMNS = ("name", "grade", "type_group")`, `FACT_AREA_COLUMNS = ("name", "lat", "lon")`, `PROMOTABLE = frozenset({"ice", "mixed"})`, `fact_route_id(mp_route_id: int) -> uuid.UUID`, `fact_area_id(mp_location_id: int) -> uuid.UUID`, `async promote(conn, *, run_id: uuid.UUID) -> dict[str, int]`, CLI `python -m app.pipelines.mp_facts`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_mp_facts.py`:

```python
import asyncio
import re
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.pipelines.mp_facts as mp_facts
from app.pipelines.mp_facts import fact_route_id, promote
from tests.pgtest import migrated_db, requires_pg, sa_url


def test_insert_statements_copy_only_fact_columns():
    source = Path(mp_facts.__file__).read_text()
    selects = re.findall(r"SELECT (.*?) FROM mp_", source, flags=re.S)
    mp_columns = {c.strip().split(".")[-1] for s in selects for c in s.split(",")}
    assert mp_columns <= {"mp_route_id", "mp_id", "name", "grade", "type", "location_id", "parent_id", "latitude", "longitude"}
    assert "description" not in source and "url" not in source.replace("run_id", "")


SEED = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Fixture Region', NULL, 44.0, -71.3), (900000101, 'Fixture Ice Crag', 900000100, 44.1, -71.3);
INSERT INTO mp_routes (mp_route_id, name, location_id, grade, type) VALUES
  (900000001, 'Fixture Ice Line', 900000101, 'WI4', 'Ice'),
  (900000002, 'Fixture Rock Line', 900000101, '5.9', 'Trad'),
  (900000003, 'Fixture Mixed Line', 900000101, 'M5', 'Mixed, Ice'),
  (900000004, 'Linked Ice Line', 900000101, 'WI3', 'Ice');
INSERT INTO canonical_areas (area_id, name, path, lat, lon, coord_precision, source, redistributable) VALUES
  ('00000000-0000-0000-0000-00000000000a', 'OB Region', '0000000000000000000000000000000a', 44.0, -71.3, 'area_centroid', 'openbeta', true);
INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable) VALUES
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-00000000000a', 'OB Ice', '{ice}', 'ice', 'rt-v1', false, true, 'openbeta', true);
INSERT INTO internal.mp_area_links (area_id, mp_location_id, match_score, match_method) VALUES
  ('00000000-0000-0000-0000-00000000000a', 900000100, 1.0, 'auto');
INSERT INTO internal.mp_route_links (route_id, mp_route_id, match_score, match_method) VALUES
  ('00000000-0000-0000-0000-0000000000b1', 900000004, 0.95, 'auto');
"""


@requires_pg
def test_only_unmatched_ice_and_mixed_are_promoted_non_redistributable():
    async def scenario(url: str) -> tuple[dict[str, int], list[tuple[object, ...]]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                counts = await promote(conn, run_id=uuid.uuid4())
                again = await promote(conn, run_id=uuid.uuid4())
                rows = [tuple(r) for r in (await conn.execute(text(
                    "SELECT r.name, r.type_group, r.redistributable, r.source, a.name, a.parent_id IS NOT NULL "
                    "FROM canonical_routes r JOIN canonical_areas a USING (area_id) WHERE r.source = 'mp_facts' ORDER BY r.name"))).all()]
            assert again["routes"] == 0
            return counts, rows
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        counts, rows = asyncio.run(scenario(sa_url(name)))
    assert counts == {"routes": 2, "areas": 1}
    assert rows == [
        ("Fixture Ice Line", "ice", False, "mp_facts", "Fixture Ice Crag", True),
        ("Fixture Mixed Line", "mixed", False, "mp_facts", "Fixture Ice Crag", True),
    ]
    assert fact_route_id(900000001) == fact_route_id(900000001)
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/mp_facts.py`:

```python
"""P2-1: the only path from MP tables into the public catalog. Copies route facts (name,
grade, type group) and area facts (name, lat, lon) for ice/mixed routes that OpenBeta does
not cover; rows are marked redistributable=false and excluded from any bulk export."""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.catalog import ltree_label
from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.route_types import TYPE_RULE_VERSION, flags_from_mp_type, map_type_group
from app.pipelines.validate import ValidationReport, coord_problem

FACT_ROUTE_COLUMNS = ("name", "grade", "type_group")
FACT_AREA_COLUMNS = ("name", "lat", "lon")
PROMOTABLE = frozenset({"ice", "mixed"})
_NS = uuid.UUID("5f3c2c7e-7b0e-4d7b-9a55-7a1d4f5b2c11")


def fact_route_id(mp_route_id: int) -> uuid.UUID:
    return uuid.uuid5(_NS, f"mp_facts:route:{mp_route_id}")


def fact_area_id(mp_location_id: int) -> uuid.UUID:
    return uuid.uuid5(_NS, f"mp_facts:area:{mp_location_id}")


async def promote(conn: AsyncConnection, *, run_id: uuid.UUID) -> dict[str, int]:
    locations = {int(i): (str(n), p, la, lo) for i, n, p, la, lo in (await conn.execute(text(
        "SELECT mp_id, name, parent_id, latitude, longitude FROM mp_locations"))).all()}
    area_links = {int(m): uuid.UUID(str(a)) for m, a in (await conn.execute(text(
        "SELECT mp_location_id, area_id FROM internal.mp_area_links"))).all()}
    paths = {uuid.UUID(str(a)): str(p) for a, p in (await conn.execute(text(
        "SELECT area_id, path::text FROM canonical_areas"))).all()}
    linked_routes = {int(m) for (m,) in (await conn.execute(text("SELECT mp_route_id FROM internal.mp_route_links"))).all()}
    created_areas = 0
    routes = 0
    for mp_route_id, name, location_id, grade, mp_type in (await conn.execute(text(
            "SELECT mp_route_id, name, location_id, grade, type FROM mp_routes"))).all():
        if int(mp_route_id) in linked_routes or location_id is None or int(location_id) not in locations:
            continue
        group = map_type_group(flags_from_mp_type(mp_type), [grade], None).type_group
        if group not in PROMOTABLE:
            continue
        loc_id = int(location_id)
        area_id = area_links.get(loc_id)
        if area_id is None:
            loc_name, parent, lat, lon = locations[loc_id]
            if lat is None or lon is None or coord_problem(float(lat), float(lon)) is not None:
                continue
            cursor, parent_area = parent, None
            while cursor is not None and int(cursor) in locations:
                if int(cursor) in area_links:
                    parent_area = area_links[int(cursor)]
                    break
                cursor = locations[int(cursor)][1]
            area_id = fact_area_id(loc_id)
            path = f"{paths[parent_area]}.{ltree_label(area_id)}" if parent_area in paths else ltree_label(area_id)
            inserted = await conn.execute(text(
                "INSERT INTO canonical_areas (area_id, name, parent_id, path, lat, lon, geom, coord_precision, source, "
                "redistributable, updated_at, run_id) VALUES (:a, :name, :parent, CAST(:path AS ltree), :lat, :lon, "
                "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, 'crag', 'mp_facts', false, now(), :run) "
                "ON CONFLICT (area_id) DO NOTHING"),
                {"a": area_id, "name": loc_name, "parent": parent_area, "path": path, "lat": float(lat), "lon": float(lon), "run": run_id})
            created_areas += inserted.rowcount
            area_links[loc_id] = area_id
            paths[area_id] = path
        route_id = fact_route_id(int(mp_route_id))
        await conn.execute(text(
            "INSERT INTO canonical_routes (route_id, area_id, name, grade, disciplines, type_group, type_rule_version, "
            "is_boulder, scored, source, redistributable, updated_at, run_id) VALUES (:r, :a, :name, :grade, "
            "ARRAY[:tg], :tg, :trv, false, true, 'mp_facts', false, now(), :run) ON CONFLICT (route_id) DO NOTHING"),
            {"r": route_id, "a": area_id, "name": str(name), "grade": grade, "tg": group, "trv": TYPE_RULE_VERSION, "run": run_id})
        await conn.execute(text(
            "INSERT INTO internal.mp_route_links (route_id, mp_route_id, match_score, match_method) "
            "VALUES (:r, :m, 1.0, 'mp_facts') ON CONFLICT DO NOTHING"), {"r": route_id, "m": int(mp_route_id)})
        linked_routes.add(int(mp_route_id))
        routes += 1
    return {"routes": routes, "areas": created_areas}


async def _main() -> dict[str, object]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source="mp_facts", window_start=None, window_end=None, content_sha256=None)
            counts = await promote(conn, run_id=run_id)
            report = ValidationReport("mp_facts")
            report.rows_in = report.accepted = counts["routes"]
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=counts["routes"] + counts["areas"])
    finally:
        await engine.dispose()
    return dict(counts)


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_main()), sort_keys=True))
```

The static test in Step 1 scans every `SELECT … FROM mp_` in this file and fails if any MP column outside the fact whitelist (plus ids and keys) is read, which is the spec's "writes only the fact columns" guard in executable form; the DB test proves rock and already-matched routes are never promoted.

Append `"app.pipelines.mp_facts"` to strict mypy.

- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/mp_facts.py backend/tests/test_mp_facts.py backend/pyproject.toml && git commit -m "feat(pipelines): mp_facts promotes unmatched ice/mixed route facts only"`

---

### Task 8: MP-data guard, schema prose check, PR 2b-1a docs

**Files:**
- Create: `scripts/check_no_mp_data.py`, `backend/tests/test_check_no_mp_data.py`, `backend/tests/test_no_mp_prose_in_schemas.py`
- Modify: `.github/workflows/ci.yml` (`guards`), `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`

**Interfaces:**
- Produces: `scripts/check_no_mp_data.py` with `violations(paths: Iterable[str], read: Callable[[str], str]) -> list[str]`, exit 1 on any; scope: tracked files under `data/`, `docs/`, `backend/tests/fixtures/`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_check_no_mp_data.py`:

```python
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("check_no_mp_data", Path(__file__).resolve().parents[2] / "scripts" / "check_no_mp_data.py")
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def run(files: dict[str, str]) -> list[str]:
    return guard.violations(files, files.__getitem__)


def test_route_and_area_urls_are_flagged_but_domain_mentions_are_not():
    assert run({"docs/a.md": "see https://www.mountainproject.com/route/105748391/the-book"})
    assert run({"data/b.json": '"url": "mountainproject.com/area/105744222"'})
    assert run({"docs/c.md": "no mountainproject.com URLs in fixtures"}) == []


def test_real_mp_ids_flagged_synthetic_range_allowed():
    assert run({"backend/tests/fixtures/x.json": '{"mp_id": 105748391}'})
    assert run({"data/golden/y.csv": "mp_route_id,label\n105748391,yes\n"})
    assert run({"backend/tests/fixtures/x.json": '{"mp_id": 900000001}'}) == []


def test_out_of_scope_paths_are_ignored():
    assert run({"backend/app/x.py": "mp_route_id = 105748391"}) == []
```

`backend/tests/test_no_mp_prose_in_schemas.py`:

```python
import inspect

from pydantic import BaseModel

import app.schemas as schemas_pkg
from app.schemas import accident, mp_location, mp_route, prediction

FORBIDDEN = {"description", "beta", "protection", "comment", "comments", "photos", "photo", "stars", "user", "username"}


def test_no_api_schema_exposes_an_mp_prose_field():
    offenders = []
    for module in (accident, mp_location, mp_route, prediction):
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, BaseModel) and cls.__module__ == module.__name__ and module is not accident:
                offenders += [f"{name}.{f}" for f in cls.model_fields if f in FORBIDDEN]
    assert schemas_pkg and offenders == []
```

(`accident` is excluded: accident `description` is accident narrative, not MP content; it is covered by R11's facts-only rule.)

- [ ] **Step 2: Run to verify failure** — FAIL (`scripts/check_no_mp_data.py` missing).

- [ ] **Step 3: Implement** `scripts/check_no_mp_data.py`:

```python
#!/usr/bin/env python3
"""Fail CI if tracked data, docs or test fixtures carry Mountain Project route/area URLs or
real MP ids (P2-1). Synthetic test ids (>= 900000000) are allowed. Stdlib only."""
from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

SCOPES = ("data/", "docs/", "backend/tests/fixtures/")
URL = re.compile(r"mountainproject\.com/(?:route|area|v)/\d+", re.IGNORECASE)
ID_KEY = re.compile(r"""["']?mp_(?:route_|location_)?id["']?\s*[:=]\s*["']?(\d{6,})""", re.IGNORECASE)
SYNTHETIC_FLOOR = 900_000_000


def _csv_ids(text: str) -> list[int]:
    lines = text.splitlines()
    if not lines:
        return []
    header = [h.strip().lower() for h in lines[0].split(",")]
    cols = [i for i, h in enumerate(header) if h in ("mp_id", "mp_route_id", "mp_location_id")]
    out = []
    for line in lines[1:]:
        cells = line.split(",")
        out += [int(cells[i]) for i in cols if i < len(cells) and cells[i].strip().isdigit()]
    return out


def violations(paths: Iterable[str], read: Callable[[str], str]) -> list[str]:
    found = []
    for path in paths:
        if not path.startswith(SCOPES):
            continue
        text = read(path)
        if URL.search(text):
            found.append(f"{path}: Mountain Project route/area URL")
        ids = [int(m) for m in ID_KEY.findall(text)] + (_csv_ids(text) if path.endswith(".csv") else [])
        real = [i for i in ids if i < SYNTHETIC_FLOOR]
        if real:
            found.append(f"{path}: {len(real)} real MP id(s)")
    return found


def main() -> int:
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    found = violations(tracked, lambda p: Path(p).read_text(encoding="utf-8", errors="ignore"))
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
```

`.github/workflows/ci.yml` `guards` job, after the scraper step:

```yaml
      - name: No Mountain Project data in data/docs/fixtures (P2-1)
        run: python scripts/check_no_mp_data.py
```

Run `python scripts/check_no_mp_data.py` on the tree; if it flags an existing file, fix the file (replace the id with a synthetic one) rather than widening the guard.

Docs: `CHANGELOG.md` "Phase 2b catalog (PR 2b-1a)": catalog tables (0008, `ltree` owner script), type mapper, OpenBeta weekly load + workflow, matcher with review, `mp_facts`, MP-data guard, `pipelines` dependency group, `HEALTHCHECKS_PING_KEY`. `CLAUDE.md` Data rules: add "`scripts/check_no_mp_data.py` (CI `guards`) fails on MP route/area URLs or real MP ids in `data/`, `docs/`, `backend/tests/fixtures/`." and Commands: `uv sync --group pipelines` for data jobs. `DEPLOYMENT.md`: new "Data workflows" section: `data-openbeta.yml` (weekly), secrets `INGEST_DATABASE_URL`, `HEALTHCHECKS_PING_KEY`, healthchecks check `openbeta-weekly` (period 7 d, grace 1 d), failure opens an issue. `data/DATABASE_STRUCTURE.md`: catalog tables, internal link tables, `scorable_routes`.

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd .. && python scripts/check_no_scrapers.py && python scripts/check_no_mp_data.py` → green.
- [ ] **Step 5: Commit** — `git add scripts/check_no_mp_data.py backend/tests/test_check_no_mp_data.py backend/tests/test_no_mp_prose_in_schemas.py .github/workflows/ci.yml CHANGELOG.md CLAUDE.md DEPLOYMENT.md data/DATABASE_STRUCTURE.md && git commit -m "feat(ci): guard against MP data in data/docs/fixtures; no MP prose in API schemas"`

---

# PR 2b-1b — `feat/p2b-mp-split` `[assumes D2]`

### Task 9: `mp_tick_counts` builder and the Ascents tab switch

**Files:**
- Create: `backend/app/pipelines/mp_tick_counts.py`, `backend/tests/test_mp_tick_counts.py`
- Modify: `backend/app/api/v1/mp_routes.py` (`get_ascent_analytics`), `backend/tests/test_ascent_analytics.py`, `backend/pyproject.toml`

**Interfaces:**
- Consumes: `mp_ticks.quarantine_reason` (plan 1 R8), `mp_tick_counts` (Task 1).
- Produces: `BUILD_SQL` (rebuild from clean ticks), `async build(conn, *, run_id) -> int`, CLI `python -m app.pipelines.mp_tick_counts`; `get_ascent_analytics` response shape unchanged.

- [ ] **Step 1: Failing tests** — `backend/tests/test_mp_tick_counts.py`:

```python
import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.mp_tick_counts import build
from tests.pgtest import migrated_db, requires_pg, sa_url

SEED = """
INSERT INTO mp_ticks (tick_id, route_id, climber_name, tick_date, quarantine_reason) VALUES
  (1, '900000001', 'c', '2025-01-04', NULL), (2, '900000001', 'c', '2025-01-11', NULL),
  (3, '900000001', 'c', NULL, NULL), (4, '900000001', 'c', '3901-01-15', 'future'),
  (5, 'abc', 'c', '2025-01-04', 'orphan_route');
"""


@requires_pg
def test_counts_exclude_quarantined_and_keep_undated():
    async def scenario(url: str) -> list[tuple[int, str, int]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await build(conn, run_id=uuid.uuid4())
                await build(conn, run_id=uuid.uuid4())
                return [tuple(r) for r in (await conn.execute(text(
                    "SELECT mp_route_id, period, tick_count FROM mp_tick_counts ORDER BY 1, 2"))).all()]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        assert asyncio.run(scenario(sa_url(name))) == [(900000001, "2025-01", 2), (900000001, "undated", 1)]
```

In `backend/tests/test_ascent_analytics.py`: replace the `INSERT INTO mp_ticks …` block in `SEED_SQL` with

```sql
INSERT INTO mp_tick_counts (mp_route_id, period, tick_count) VALUES
    ({ROUTE}, '2025-01', 2),
    ({ROUTE}, '2025-07', 1),
    ({ROUTE}, '3901-01', 1);
```

(the `3901-01` row stands in for a future period that reached the table; the endpoint must still ignore it), and in `test_undated_records_are_counted_separately` replace the `mp_ticks` insert with `INSERT INTO mp_tick_counts (mp_route_id, period, tick_count) VALUES ({ROUTE}, 'undated', 1);`. Add:

```python
def test_future_periods_are_not_counted(seeded_db):
    _seed(seeded_db, f"INSERT INTO mp_tick_counts (mp_route_id, period, tick_count) VALUES ({OTHER_ROUTE}, '3901-01', 5);")
    data = asyncio.run(_analytics(seeded_db, OTHER_ROUTE))
    assert data["total_ascents"] == 0 and data["has_data"] is False
```

Every other existing assertion in the file stays unchanged.

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run pytest tests/test_mp_tick_counts.py tests/test_ascent_analytics.py -q` → FAIL.

- [ ] **Step 3: Implement**

`backend/app/pipelines/mp_tick_counts.py`:

```python
"""The Ascents tab's only view of MP ticks (D2): counts per route and month, built from
R8-clean rows. Raw ticks (with climber names) stay unreadable to the app role."""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

TICKS = "mp_ticks"
BUILD_SQL = f"""
INSERT INTO mp_tick_counts (mp_route_id, period, tick_count, built_run_id)
SELECT route_id::bigint, coalesce(to_char(tick_date, 'YYYY-MM'), 'undated'), count(*), :run_id
FROM {TICKS}
WHERE quarantine_reason IS NULL AND route_id ~ '^[0-9]{{1,18}}$'
GROUP BY 1, 2
"""


async def build(conn: AsyncConnection, *, run_id: uuid.UUID) -> int:
    await conn.execute(text("DELETE FROM mp_tick_counts"))
    return (await conn.execute(text(BUILD_SQL), {"run_id": run_id})).rowcount


async def _main() -> dict[str, int]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source="mp_tick_counts", window_start=None, window_end=None, content_sha256=None)
            n = await build(conn, run_id=run_id)
            report = ValidationReport("mp_tick_counts")
            report.rows_in = report.accepted = n
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n)
    finally:
        await engine.dispose()
    return {"rows": n}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_main())))
```

The rebuild runs in one transaction, so readers never see an empty table.

In `backend/app/api/v1/mp_routes.py`, `get_ascent_analytics`: delete `route_id_str`, `tick_params`, `total_ascents_query`, `monthly_ascents_query` and `ascent_years_query` and their executions, and insert after `accident_params = …`:

```python
    # Ticks come only as per-month counts built from R8-clean rows (D2); raw ticks carry
    # climber names and are not readable by this role. Months after the current one are
    # data errors that slipped past the builder, so they are ignored here too.
    this_month = today.strftime("%Y-%m")
    tick_rows = (
        await db.execute(
            text(
                "SELECT period, tick_count FROM mp_tick_counts "
                "WHERE mp_route_id = :mp_route_id AND (period = 'undated' OR period <= :this_month)"
            ),
            {"mp_route_id": mp_route_id, "this_month": this_month},
        )
    ).all()
    undated_ascents = sum(int(n) for p, n in tick_rows if p == "undated")
    dated = [(str(p), int(n)) for p, n in tick_rows if p != "undated"]
    total_ascents = undated_ascents + sum(n for _, n in dated)
    monthly_ascent_dict: dict[int, int] = {}
    for period, n in dated:
        month = int(period[5:7])
        monthly_ascent_dict[month] = monthly_ascent_dict.get(month, 0) + n
    years = [int(p[:4]) for p, _ in dated]
    ascent_years = {"first": min(years), "last": max(years)} if years else None
```

The accident queries, `monthly_stats`, `peak_month`, and the response dict are unchanged.

Append `"app.pipelines.mp_tick_counts"` to strict mypy. `grants_phase2.sql` already grants `ingest` DELETE/INSERT on `mp_tick_counts` (Task 1).

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_mp_tick_counts.py tests/test_ascent_analytics.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS (every existing ascent-analytics test passes against the aggregate).
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/mp_tick_counts.py backend/app/api/v1/mp_routes.py backend/tests/test_mp_tick_counts.py backend/tests/test_ascent_analytics.py backend/pyproject.toml && git commit -m "feat(api): Ascents tab reads per-month tick counts built from R8-clean ticks"`

---

### Task 10: Migration `0009` — raw ticks to `internal`

**Files:**
- Create: `backend/alembic/versions/0009_mp_ticks_internal.py`, `backend/tests/test_migration_0009.py`, `backend/app/pipelines/mp_tables.py`, `backend/tests/test_mp_tables.py`
- Modify: `backend/app/pipelines/mp_ticks_quarantine.py`, `backend/app/pipelines/mp_tick_counts.py`, `backend/tests/test_mp_ticks_quarantine.py`, `backend/tests/test_mp_tick_counts.py`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`, `CHANGELOG.md`, `data/DATABASE_STRUCTURE.md`

**Interfaces:**
- Produces: `internal.mp_ticks` (same columns); `app` holds nothing on it; `analyst` keeps SELECT; `ingest` keeps SELECT + column UPDATE.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0009.py`:

```python
import asyncio

import asyncpg

from tests.pgtest import migrated_db, pg_url, requires_pg

pytestmark = requires_pg


def test_0009_moves_ticks_and_keeps_rows():
    seed = "INSERT INTO mp_ticks (tick_id, route_id, climber_name) VALUES (1, '900000001', 'c');"
    with migrated_db("0008_catalog", seed) as name:
        from alembic import command

        from tests.test_migrations import _alembic_cfg

        command.upgrade(_alembic_cfg(name), "head")

        async def check() -> list[object]:
            conn = await asyncpg.connect(pg_url(name))
            try:
                return [await conn.fetchval("SELECT to_regclass('public.mp_ticks')"),
                        await conn.fetchval("SELECT count(*) FROM internal.mp_ticks")]
            finally:
                await conn.close()

        assert asyncio.run(check()) == [None, 1]
```

In `backend/tests/test_mp_ticks_quarantine.py` and `test_mp_tick_counts.py`, change the seed inserts' target to `internal.mp_ticks`.

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/alembic/versions/0009_mp_ticks_internal.py`:

```python
"""D2: raw MP ticks (climber names) move to schema internal; the app reads mp_tick_counts.
Apply only after the build job has filled mp_tick_counts and the switched API is deployed."""

from alembic import op

revision = "0009_mp_ticks_internal"
down_revision = "0008_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    empty_counts = bind.exec_driver_sql("SELECT NOT EXISTS (SELECT 1 FROM mp_tick_counts)").scalar()
    has_ticks = bind.exec_driver_sql("SELECT EXISTS (SELECT 1 FROM mp_ticks)").scalar()
    if empty_counts and has_ticks:
        raise RuntimeError("refusing 0009: mp_tick_counts is empty; run python -m app.pipelines.mp_tick_counts first")
    op.execute("ALTER TABLE public.mp_ticks SET SCHEMA internal")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app') THEN "
        "REVOKE ALL ON internal.mp_ticks FROM app; END IF; END $$"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE internal.mp_ticks SET SCHEMA public")
```

The grant revoke is conditional because local/CI databases have no `app` role; prod does.

Both jobs must work before and after `0009` (the runbook runs them on each side of it), so they resolve the table at run time. Create `backend/app/pipelines/mp_tables.py`:

```python
"""Where raw MP ticks live: public.mp_ticks before migration 0009, internal.mp_ticks after.
Only these two literal names are ever interpolated into SQL."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

CANDIDATES = ("internal.mp_ticks", "public.mp_ticks")


async def ticks_table(conn: AsyncConnection) -> str:
    for name in CANDIDATES:
        if (await conn.execute(text("SELECT to_regclass(:n)"), {"n": name})).scalar() is not None:
            return name
    raise SystemExit("mp_ticks not found in internal or public")
```

`mp_ticks_quarantine.py`: turn `QUARANTINE_SQL` and `COUNTS_SQL` into templates with `{ticks}` in place of the three table references (`FROM {ticks} t`, `UPDATE {ticks} m`, `FROM {ticks} GROUP BY`); in `run()`, `ticks = await ticks_table(conn)` and `text(QUARANTINE_SQL.format(ticks=ticks))` / `text(COUNTS_SQL.format(ticks=ticks))`. `mp_tick_counts.py`: `BUILD_SQL` uses `FROM {ticks}` (drop the `TICKS` constant and the `f`-string; keep the regex as `'^[0-9]{{1,18}}$'` so `.format` leaves `{1,18}`), and `build()` formats it with `await ticks_table(conn)`. The seeds of `test_mp_ticks_quarantine.py` and `test_mp_tick_counts.py` keep inserting into `mp_ticks` (resolved to `public` at head-before-0009 and to `internal` after): insert into whichever `ticks_table` returns by running the seed after migrating, i.e. change those seeds to `INSERT INTO internal.mp_ticks …` now that head includes `0009`. Add `"app.pipelines.mp_tables"` to strict mypy and a unit test `backend/tests/test_mp_tables.py`:

```python
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.mp_tables import ticks_table
from tests.pgtest import migrated_db, requires_pg, sa_url


@requires_pg
def test_resolves_both_sides_of_0009():
    async def resolve(url: str) -> str:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return await ticks_table(conn)
        finally:
            await engine.dispose()

    with migrated_db("0008_catalog") as name:
        assert asyncio.run(resolve(sa_url(name))) == "public.mp_ticks"
    with migrated_db("head") as name:
        assert asyncio.run(resolve(sa_url(name))) == "internal.mp_ticks"
```

`grants_phase2.sql`: replace the Plan 1 lines naming `public.mp_ticks` with a block that targets whichever exists:

```sql
DO $$
DECLARE t text := coalesce(to_regclass('internal.mp_ticks')::text, to_regclass('public.mp_ticks')::text);
BEGIN
  EXECUTE format('GRANT SELECT ON %s TO ingest', t);
  EXECUTE format('GRANT UPDATE (quarantine_reason, quarantine_rule_version) ON %s TO ingest', t);
END $$;
```

(keep `public.accidents, public.mp_routes, public.mp_locations` in the plain SELECT grant). `verify_roles_phase2.sql`: in the column check, replace `'public.mp_ticks'` with `coalesce(to_regclass('internal.mp_ticks'), to_regclass('public.mp_ticks'))` (three places).

Earlier tests that touch `mp_ticks` at `head` must follow the move (they would otherwise fail with "relation does not exist"):
- `backend/tests/test_migration_0004.py::test_0004_tick_quarantine_reason_check`: migrate to `"0004_phase2a_foundation"` instead of `"head"`.
- `backend/tests/test_roles_phase2.py`: the two ingest statements become `UPDATE internal.mp_ticks SET quarantine_reason = NULL WHERE false` (allowed) and `UPDATE internal.mp_ticks SET climber_name = 'x' WHERE false` (denied); add `_denied(app, "SELECT count(*) FROM internal.mp_ticks")`.
- `backend/tests/verify/test_phase2a_foundation.py`: `FROM mp_ticks` → `FROM internal.mp_ticks` in both cells.

`CHANGELOG.md` "MP split (PR 2b-1b)": `mp_tick_counts` builder, Ascents tab reads counts, raw ticks move to `internal` (0009), `app` loses all access to raw ticks. `data/DATABASE_STRUCTURE.md`: move `mp_ticks` under "Schema internal"; add `mp_tick_counts`.

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/` → green (the role test now checks `internal.mp_ticks`).
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0009_mp_ticks_internal.py backend/app/pipelines/ backend/tests/ backend/db/roles/ CHANGELOG.md data/DATABASE_STRUCTURE.md && git commit -m "feat(db): 0009 raw MP ticks move to internal; app keeps only aggregate counts"`

---

### Task 11: OWNER/AGENT RUNBOOK — PR 2b-1a

- [ ] **Step 1 (owner/agent): Rehearsal branch `p2b-1-rehearsal`; extension; migrate; grants**

```bash
( set -a; . ./.env.owner; set +a
  split_pg_url "$(printf '%s' "$OWNER_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  psql "$PG_URL_NOPASS" -X -q -f db/owner/extensions_phase2b.sql )
```

Then `alembic upgrade head` as migrator (→ `0008_catalog (head)`; `0009` is not in this PR), `alembic check`, `grants_phase2.sql`, both verify scripts.

- [ ] **Step 2 (owner/agent): First OpenBeta load on the branch**

```bash
( set -a; . ./.env.ingest; set +a
  export INGEST_DATABASE_URL="$(printf '%s' "$INGEST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  uv sync --group pipelines
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.catalog )
```

Expected: `"status": "ok"`, `routes` near the verified 206K US climbs (spec), `quarantined` small. Report the `unknown` share of roped routes as analyst: `SELECT round(avg((type_group = 'unknown')::int), 3) FROM canonical_routes WHERE NOT is_boulder` — target ≤0.25.

- [ ] **Step 3 (owner/agent): Match, review, evaluate**

`uv run python -m app.pipelines.match run --out ../data/review/matches.csv` (dry run) → counts; then `--apply`. Owner fills `decision` (`link|no_link`) in `data/review/matches.csv`, then `… match import --review-file ../data/review/matches.csv`. Owner labels 300 random MP↔OpenBeta pairs into `~/Developer/safeascent-private/golden/match_v1.csv` (`mp_route_id,ob_climb_uuid,label`, label `yes|no`), then `… match eval --golden ~/Developer/safeascent-private/golden/match_v1.csv` → `precision_at_auto ≥ 0.98`. If lower, stop: the thresholds or weights change in a reviewed commit (never tuned to pass on the same labels without recording it in the PR).

- [ ] **Step 4 (owner/agent): Promote MP ice/mixed facts** — `uv run python -m app.pipelines.mp_facts` → `routes` in the thousands (spec ~4.8K MP ice+mixed minus OpenBeta matches). As analyst: `SELECT type_group, count(*) FROM canonical_routes WHERE source = 'mp_facts' GROUP BY 1` shows only `ice` and `mixed`.

- [ ] **Step 5 (owner): Secrets and schedule** — GitHub Actions secret `HEALTHCHECKS_PING_KEY` (healthchecks.io → project settings → ping key); create check `openbeta-weekly` (period 7 d, grace 1 d). Run `data-openbeta` once via "Run workflow"; it must pass.

- [ ] **Step 6 (owner/agent): Prod** — repeat Steps 1–4 on prod. Delete the branch.

---

### Task 12: OWNER/AGENT RUNBOOK — PR 2b-1b (order matters)

All commands from this PR's head; the jobs resolve `mp_ticks` on either side of `0009`.

- [ ] **Step 1 (owner/agent): Rehearse the order on a branch**: `python -m app.pipelines.mp_ticks_quarantine` → `python -m app.pipelines.mp_tick_counts` (both as ingest; they read `public.mp_ticks`) → `alembic upgrade head` as migrator (`0009`; it refuses if the counts are empty) → `grants_phase2.sql` + both verify scripts → run the quarantine and counts jobs again (now `internal.mp_ticks`; counts identical).
- [ ] **Step 2 (owner): Prod, with the deploy in the middle**: (1) run R8 and the counts builder against prod; (2) deploy the backend from this PR (the API reads `mp_tick_counts`; `mp_ticks` still exists, so nothing breaks either way); (3) open the Ascents tab for a few routes and confirm counts match the previous display (routes with pre-1970 ticks may drop those, by design); (4) `alembic upgrade head` (`0009`), grants, verify; (5) as analyst: `SELECT has_table_privilege('app', 'internal.mp_ticks', 'SELECT'), has_schema_privilege('app', 'internal', 'USAGE')` → `f|f`.
- [ ] **Step 3 (owner): Schedule** — the weekly tick-count rebuild runs after R8 in the same workflow in plan 7 (`data-weekly.yml`); until then run both by hand after any tick load.

---

## Self-review

- Spec coverage (2b-1): OpenBeta catalog (Tasks 3–4), `internal` schema move (Tasks 9–10, per D2 rather than all `mp_*`), matcher with thresholds and 300-pair precision (Tasks 5–6, 11), route types and `unknown` ≤25% report (Tasks 2, 11), `mp_facts` ice/mixed load with column guard (Task 7), `mp_tick_aggregates` load (plan 1), guards `check_no_mp_data.py`, no MP prose in schemas, `app` has no privilege on `internal` (Task 8, plan 1 verify), weekly bulk + 3% rule (Task 4), GitHub Actions batch + failure issue + healthchecks (Task 4).
- Types: `ObArea`/`ObClimb` fields are used identically in `catalog.build_batch`; `decide` returns `(Decision, UUID | None, float)` everywhere; `ltree_label` is shared by `catalog` and `mp_facts`.
