# Phase 2b Catalog (PRs 2b-1a, 2b-1b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the public route catalog: OpenBeta (CC0) areas and climbs as the primary rock source, `type_group` from a versioned mapper, an in-house matcher linking internal MP routes and areas to it, MP ice/mixed route facts promoted where OpenBeta has no match, CI guards against MP data leaking into the repo, and the content split of MP tables (raw ticks with climber names move to `internal`; the Ascents tab reads aggregate counts).

**Architecture:** `app/pipelines/openbeta.py` (GraphQL client, pydantic-validated, retrying) feeds `catalog.py` (validated upsert of `canonical_areas`/`canonical_routes` with an `ltree` path, retirement of vanished rows, OpenBeta `mp_id` cross-references written only to `internal`). `route_types.py` maps flags/grades to `type_group`. `match.py` scores area and climb candidates (H3 r7 neighbourhoods, in-house token-aligned name similarity, per-system grade comparison) and auto-decides outside the 0.80–0.90 band only when the best candidate is unique and clears the runner-up by a margin. `mp_facts.py` is the only path from MP data to public catalog tables, writing name/grade/type/location only, and retires its own rows once OpenBeta covers the route. `ob_tick_sample.py` measures OpenBeta tick coverage once (D17). A weekly GitHub Actions workflow runs the OpenBeta load and pings healthchecks.io.

**Tech Stack:** Python 3.12, httpx, pydantic 2, SQLAlchemy async, Alembic, Postgres `ltree` + PostGIS, `h3` (v4, appended to the `pipelines` dependency group that plan 3 creates), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` §Route catalog (P2-1), §Route types (P2-3, P2-4), §Tables, milestone 2b-1; owner decisions D2, D5, D11, D17 in `2026-09-28-phase2a-foundations.md` (and its "Defaults pending owner confirmation", DP2); amendment D5 (MP rock routes and tick aggregates may be displayed; no MP prose ever).

**Prerequisite:** Plans 1, 2 and 3 merged and applied; merge order is linear (foundations plan's split table), so `0008`'s `down_revision` is `0007_cell_daily_conditions`. Content dependencies: plan 2's `textsim.py`/`geo.py` (plan 2 Task 5) are imported by the matcher; plan 3 creates the `pipelines` dependency group (with `timezonefinder`), to which this plan only appends `h3`. Plan 5 (not this plan) adds `point_key` to `canonical_areas`/`canonical_routes` in `0010`.

**Two PRs:** PR 2b-1a = Tasks 1–8 (`feat/p2b-catalog`) + runbook Task 12; PR 2b-1b = Tasks 9–11 (`feat/p2b-mp-split`) + runbook Task 13 `[assumes D2]`. Task 11 (D17 sample) sits in PR 2b-1b because it reads `mp_tick_counts`, which that PR builds.

## Global Constraints

The foundations plan's Global Constraints apply. Also:
- OpenBeta queries never request media/photos. OpenBeta data is CC0 and may appear in committed test fixtures; MP data may not (no MP names, ids outside the synthetic range ≥900000000, or MP route/area URLs anywhere tracked).
- `canonical_routes` has no MP ids and no prose columns. MP links live only in `internal.mp_route_links` / `internal.mp_area_links`, which `app` cannot read.
- The `mp_facts` job is the only code path from MP tables to public catalog tables; it writes only name, grade, type group, and area name/lat/lon, and only `ice`/`mixed` rows. Rock MP routes are never promoted into the catalog; they stay displayed through the existing `mp_routes` map path (amendment D5).
- Matching thresholds (P2-10): ≥0.90, unique, and ≥0.05 above the runner-up → auto link; <0.80 → auto no-link; everything else → owner review CSV. A candidate pair with no comparable grade (different grade systems, or a grade missing) or no path tokens can reach review but never auto-link. Auto-link precision must reach ≥0.98 **separately for climb links and area links**, measured on hand labels of a stratified sample drawn **from the auto-links themselves** (≥300 climb links, ≥100 area links; stratified by score band and type group), whose files live in `~/Developer/safeascent-private/golden/` (they hold MP ids, so they cannot be committed).
- Weekly load: reject the run if the US climb count drops more than 3% against the previous successful load, **or** if any single state with ≥100 live climbs drops more than 10% (a state that silently returns a short list must not retire its climbs).
- OpenBeta has no documented rate limit and occasionally returns an empty body: every request retries with backoff, and a paging result is accepted only if it holds no duplicate ids (unstable ordering would otherwise skip rows silently).
- Missing coordinates stay NULL; an area without coordinates makes its routes `scored = false` rather than inventing a location. OpenBeta's `(0, 0)` placeholder is treated as missing, never as a location.
- Private MP data (owner note 2026-09-28): OpenBeta stays the primary rock catalog. A private MP crawler outside this repo (never on GitHub) runs as a backup collecting rock route facts and tick aggregates (month × style counts; no usernames, no prose), and the private ice/mixed tick job continues. Nothing in this plan reads those outputs; ingesting them is a separate owner-gated task pending legal review. No repo code in this plan names the MP host (the Task 8 guard assembles it from parts).

## Decisions this plan makes where the spec is silent (owner may overrule)

1. **OpenBeta ids are reused as primary keys** (`area_id = ob_area_uuid`, `route_id = ob_climb_uuid`); `mp_facts` rows get deterministic `uuid5` ids so reloads are stable.
2. **Weekly full reload by state replaces GraphQL `updatedAt` deltas** (D11: GraphQL is the bulk source). Rows not seen in a successful full load get `retired_at` and drop out of `scorable_routes`.
3. **`ltree` labels are the area UUID's 32 hex characters** (ltree labels cannot contain `-`).
4. **Toprope/aid-only climbs with no bolt information map to `unknown`** `[default pending: DP2]`. Spec rule 6 reads "the underlying rock type from rules 4–5 (bolts → `sport`, gear → `trad`); if neither is known → `unknown`". OpenBeta has no gear flag, so for a TR/aid-only climb with `boltsCount` NULL or 0 neither is known; guessing `trad` would score the route under the wrong type group. `unknown` rows are stored, reported (≤25% target, Task 12), and never scored. Alternative the owner may pick instead: map them to `trad`.
5. **Grade similarity** for climb matching compares grades **per system** (YDS, WI/AI, M, V, aid), after stripping protection ratings (`PG13`, `PG`, `R`, `X`) and commitment grades (`III`–`VII`): identical in every shared system 1.0, same base grade ignoring letter/sign (`5.10a` vs `5.10c`, `WI4` vs `WI4+`) 0.5, else 0. Grades with no system in common (or a missing grade) are **not comparable** (`None`), never "different": the climb score is then renormalized over name and type and capped at 0.89, so such a pair can reach owner review but never auto-link.
6. **One healthchecks.io ping key** (`HEALTHCHECKS_PING_KEY`) with a slug per data job (`https://hc-ping.com/<key>/<slug>`), instead of one URL setting per job.
7. **`check_no_mp_data.py` flags MP route/area URLs** (`<MP host>/route/<digits>`, `/area/<digits>`), not bare domain mentions, so specs and Phase 1 guard fixtures that name the domain still pass.
8. **Name similarity** is whole-name token alignment (each token matched one-to-one to its closest counterpart by Levenshtein ratio ≥0.8, unmatched tokens on either side count against the score), not prefix-weighted Jaro-Winkler, so "The Book" vs "The Book Direct" scores 0.8 on name and lands in review, not in an auto-link.
9. **`mp_facts` yields to OpenBeta.** When an MP ice/mixed route that was promoted gets a non-`mp_facts` link to a live OpenBeta route, the `mp_facts` route is retired (it leaves `scorable_routes`) and its accident links are re-pointed to the OpenBeta route; if that OpenBeta route is later retired, the `mp_facts` route is revived. A route is never counted twice.
10. **Per-state drop threshold** 10% for states with ≥100 live climbs (smaller states swing by more than 10% on ordinary edits).
11. **OpenBeta tick coverage is measured once** (D17): a one-time stratified sample of ~1,500 climbs (Task 11). `ob_ticks` stays missing in exposure v1 until a revisit trigger fires (Task 11 defines the triggers; the weekly load checks the schema one automatically).

## Review Focus

1. **Two OpenBeta climbs in the same area both score ≥0.90 against one MP route** — expect review, never an arbitrary auto-link (Task 5 `test_two_strong_candidates_go_to_review`).
2. **A mixed route whose MP type string is "Ice, Mixed, Alpine"** — expect `mixed` (precedence), not `ice` or `alpine` (Task 2 `test_precedence_mixed_beats_ice_beats_alpine`).
3. **A weekly load where one state query fails** — expect the run `failed`, nothing retired, previous rows intact (Task 4 `test_partial_failure_retires_nothing`).
4. **An OpenBeta area whose centroid is in Canada** — expect quarantine `outside_us` and its climbs not loaded (Task 4 `test_foreign_areas_are_quarantined_with_their_climbs`).
5. **The Ascents tab for a route whose only ticks are dated in a future month** — expect zero ascents and "no tick data", never a count including them (Task 9 `test_future_periods_are_not_counted`); a tick later in the **current** month than the run day is not counted either (Task 9 `test_ticks_after_today_in_current_month_are_not_counted`).
6. **A boulder graded "V4", a UIAA "VI+" rock route, or a UIAA "V" route** — expect never `alpine` (commitment grades apply only to roman-numeral tokens that stand beside a YDS/ice/mixed grade) (Task 2 `test_v_scale_and_uiaa_grades_are_never_commitment`).
7. **"The Book" vs "The Book Direct", same grade and type** — expect review, never an auto-link; **"5.9 PG13" vs "5.9"** — expect a grade match; **"5.10a" vs "WI4"** — expect "not comparable", never an auto-link (Task 5 `test_variant_names_never_auto_link`, `test_protection_ratings_are_ignored`, `test_cross_system_grades_are_not_comparable`).
8. **Best candidate 0.93, runner-up 0.89** — expect review (margin < 0.05) (Task 5 `test_runner_up_margin_sends_close_calls_to_review`).
9. **An MP ice route promoted by `mp_facts`, then matched to a new OpenBeta climb** — expect the `mp_facts` route retired, its accident links moved to the OpenBeta route, and exactly one scorable route (Task 7 `test_mp_facts_route_yields_once_openbeta_covers_it`).
10. **One state returns half its climbs while the US total barely moves** — expect `rejected`, nothing retired (Task 4 `test_single_state_drop_rejects_the_run`).
11. **OpenBeta returns an area at `(0, 0)`, an empty body once, or a page containing a duplicate id** — expect coordinates NULL, a retry, and `OpenBetaError("unstable paging")` respectively (Task 3 tests).

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
| `backend/app/pipelines/ob_tick_sample.py` | Create | One-time stratified OpenBeta tick-coverage sample (D17) + revisit triggers. |
| `backend/tests/test_ob_tick_sample.py` | Create | Stratification, allocation and trigger tests. |
| `backend/tests/verify/test_phase2b_catalog.py` | Create | `-m db` acceptance cells: `app` has no privilege on `internal.mp_ticks`/`internal`; no retired or `unknown` route in `scorable_routes`; one live route per MP route id. |
| `backend/app/api/v1/mp_routes.py` | Modify | `get_ascent_analytics` reads `mp_tick_counts`. |
| `backend/app/pipelines/mp_ticks_quarantine.py` | Modify | Table name `internal.mp_ticks` (PR 2b-1b). |
| `scripts/check_no_mp_data.py`, `backend/tests/test_check_no_mp_data.py` | Create | Repo guard + tests. |
| `backend/tests/test_no_mp_prose_in_schemas.py` | Create | API schemas carry no MP prose fields. |
| `backend/tests/fixtures/openbeta_bulk_sample.json` | Create | Recorded CC0 OpenBeta response (no media). |
| `.github/workflows/data-openbeta.yml` | Create | Weekly load; issue on failure. |
| `.github/workflows/ci.yml` | Modify | `--group pipelines`; audit includes it; `check_no_mp_data.py` in `guards`. |
| `backend/pyproject.toml`, `backend/uv.lock` | Modify | append `h3` to the existing `pipelines` group (created by plan 3); mypy. |
| `backend/app/config.py`, `.env.example` | Modify | `HEALTHCHECKS_PING_KEY`. |
| `backend/db/roles/grants_phase2.sql`, `verify_roles_phase2.sql` | Modify | Grants. |
| tests: `test_migration_0008.py`, `test_route_types.py`, `test_openbeta.py`, `test_catalog.py`, `test_match.py`, `test_mp_facts.py`, `test_mp_tick_counts.py`, `test_migration_0009.py`; `test_ascent_analytics.py` (modify: switch to counts, fixture ids raised to ≥900000000) | Create/Modify | Tests. |
| `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md` | Modify | Docs. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 3–10 | catalog tables, `mp_tick_counts` (0008) | Task 1 first. |
| 2 | 4, 7, plan 5 | `route_types.map_type_group(flags, grades, bolts)`, `flags_from_mp_type(type_str)` | Frozen in Task 2. |
| 3 | 4, 11 | `openbeta.OpenBetaClient.us_states() -> tuple[UUID, list[tuple[UUID, str]]]`, `.bulk_areas(usa_uuid, state_uuid)`, `.query_fields()`, `.climb_tick_count(climb_uuid)`, `ObArea`, `ObClimb` | Frozen in Task 3. |
| 4 | 5, 6, 7 | `internal.mp_route_links`/`mp_area_links` rows with method `ob_mp_id` | Task 4 writes them; the matcher never overwrites `ob_mp_id` links. |
| 5 | 6, plan 5 (R10), plan 6 | `match.area_score`, `climb_score`, `decide`, `name_similarity`, `grade_similarity -> float | None`, `AUTO_LINK`, `NO_LINK_BELOW`, `AUTO_MARGIN`, `REVIEW_CAP` | Task 5 pure; Task 6 job. Signatures of `area_score`/`climb_score`/`decide` unchanged from the first draft, so plan 5's R10 keeps compiling; missing grade or missing path tokens now cap at `REVIEW_CAP` (0.89) — review, never auto. |
| 6 | 7 | matched `mp_area_links`; MP routes already promoted by `mp_facts` are matched again every run | `mp_facts` needs area links; Task 6 before 7. Task 7's yield step relies on Task 6 not skipping `mp_facts`-linked routes. |
| 3, 9 | 11 | `ob_tick_sample` reads `canonical_routes`, `internal.mp_route_links`, `mp_tick_counts` | Task 11 runs after PR 2b-1b's counts exist (runbook Task 13). |
| 4 | 8 | `backend/tests/fixtures/openbeta_bulk_sample.json` vs `check_no_mp_data.py` | Fixture holds OpenBeta `mp_id` values → Task 3 strips `mp_id` to synthetic values ≥900000000 before committing. |
| 4, 9 | each other | `backend/pyproject.toml` (group, mypy), `.github/workflows/*` | Serial. |
| 9 | 10 | `get_ascent_analytics` must read `mp_tick_counts` before `mp_ticks` moves | PR 2b-1b ships Task 9's code and Task 10's migration together; runbook Task 13 orders: build counts → deploy code → migrate `0009`. |
| 10 | plan 1 Task 5, Task 9 | `mp_ticks_quarantine.QUARANTINE_SQL`, `mp_tick_counts.BUILD_SQL` table name; grants/verify rows on `mp_ticks` | Task 10 adds `mp_tables.ticks_table()` and edits all of them in one commit, so every job works on both sides of `0009`. |
| 1, 4, 6, 7, 9, 10 | each other | `grants_phase2.sql`, `verify_roles_phase2.sql` | Append-only; serial commits. No `trainer` grants here: `trainer` is NOLOGIN with no grants until Phase 3 (D13). |
| 1 | plan 5 | `scorable_routes` lists its columns explicitly | Plan 5's `0010` `CREATE OR REPLACE VIEW`s it (appending `point_key` last) if it exposes the new column. |

---

# PR 2b-1a — `feat/p2b-catalog`

### Task 1: `ltree` owner script, migration `0008`, models, `h3` in the `pipelines` group

**Files:**
- Create: `backend/db/owner/extensions_phase2b.sql`, `backend/alembic/versions/0008_catalog.py`, `backend/app/models/catalog.py`, `backend/tests/test_migration_0008.py`
- Modify: `backend/app/models/__init__.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql` (`.github/workflows/ci.yml` only if plan 3's `--group pipelines` lines are missing)

**Interfaces:**
- Produces (DB, public): `canonical_areas(area_id uuid PK, name text NOT NULL, parent_id uuid NULL REFERENCES canonical_areas, path ltree NOT NULL, lat double precision NULL, lon double precision NULL, geom geography(Point,4326) NULL, ob_area_uuid uuid UNIQUE NULL, objective_id uuid NULL, coord_precision text NOT NULL CHECK IN ('area_centroid','crag','none'), source text NOT NULL CHECK IN ('openbeta','mp_facts','safeascent_curated'), redistributable boolean NOT NULL, is_boulder_area boolean NOT NULL DEFAULT false, retired_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(), run_id uuid)`, GiST on `path` and `geom`; `canonical_routes(route_id uuid PK, area_id uuid NOT NULL REFERENCES canonical_areas, name text NOT NULL, grade text, disciplines text[] NOT NULL, type_group text NULL CHECK IN ('sport','trad','alpine','ice','mixed','unknown'), type_rule_version text NOT NULL, is_boulder boolean NOT NULL, scored boolean NOT NULL, pitches smallint, length_m real, bolts smallint, ob_climb_uuid uuid UNIQUE NULL, source text NOT NULL CHECK (same three), redistributable boolean NOT NULL, retired_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(), run_id uuid)`, index on `area_id`, CHECK `(is_boulder AND type_group IS NULL AND NOT scored) OR (NOT is_boulder AND type_group IS NOT NULL)`; `accident_route_links(accident_id integer PK REFERENCES accidents, canonical_route_id uuid NULL REFERENCES canonical_routes, canonical_area_id uuid NULL REFERENCES canonical_areas, objective_id uuid NULL, method text NOT NULL, score real, CHECK (num_nonnulls(canonical_route_id, canonical_area_id, objective_id) >= 1))`; `mp_tick_counts(mp_route_id bigint, period text CHECK (period = 'undated' OR period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'), tick_count integer NOT NULL CHECK >= 0, built_run_id uuid, PK (mp_route_id, period))`; view `scorable_routes` = the explicit column list `route_id, area_id, name, grade, disciplines, type_group, type_rule_version, pitches, length_m, bolts, ob_climb_uuid, source, redistributable, updated_at` of `canonical_routes` `WHERE scored AND type_group <> 'unknown' AND retired_at IS NULL` (never `SELECT *`, so a later column never leaks into consumers unreviewed).
- Produces (DB, internal): `mp_route_links(route_id uuid REFERENCES public.canonical_routes, mp_route_id bigint, match_score real NOT NULL, match_method text NOT NULL CHECK IN ('ob_mp_id','auto','owner','mp_facts'), PK (route_id, mp_route_id))` + index on `mp_route_id`; `mp_area_links(area_id uuid REFERENCES public.canonical_areas, mp_location_id bigint, match_score real NOT NULL, match_method text NOT NULL CHECK IN ('ob_mp_id','auto','owner','mp_facts'), PK (area_id, mp_location_id))` + index on `mp_location_id`; `match_decisions(kind text CHECK IN ('area','climb'), mp_id bigint, ob_uuid uuid, score real NOT NULL, decision text CHECK IN ('link','no_link'), decided_by text CHECK IN ('auto','owner'), decided_at timestamptz DEFAULT now(), PK (kind, mp_id, ob_uuid))`.
- Produces (Python): models in `app.models.catalog`; `h3>=4.1,<5` appended to the `pipelines` dependency group that plan 3 created.

- [ ] **Step 1: Failing test** — `backend/tests/test_migration_0008.py`:

```python
import asyncio

import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
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


def test_scorable_routes_lists_columns_and_hides_unknown_and_retired():
    with migrated_db() as name:
        run_sql(name, AREA.format(id=A1, path="a" * 32))
        base = ("INSERT INTO canonical_routes (route_id, area_id, name, disciplines, type_group, type_rule_version, "
                "is_boulder, scored, source, redistributable, retired_at) VALUES ")
        run_sql(name, base + f"('00000000-0000-0000-0000-0000000000c1', '{A1}', 'Kept', '{{trad}}', 'trad', 'rt-v1', false, true, 'openbeta', true, NULL)")
        run_sql(name, base + f"('00000000-0000-0000-0000-0000000000c2', '{A1}', 'Unknown', '{{aid}}', 'unknown', 'rt-v1', false, true, 'openbeta', true, NULL)")
        run_sql(name, base + f"('00000000-0000-0000-0000-0000000000c3', '{A1}', 'Gone', '{{trad}}', 'trad', 'rt-v1', false, true, 'openbeta', true, now())")

        async def check() -> tuple[list[str], list[str]]:
            conn = await asyncpg.connect(pg_url(name))
            try:
                cols = [r["column_name"] for r in await conn.fetch(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'scorable_routes' ORDER BY ordinal_position")]
                names = [r["name"] for r in await conn.fetch("SELECT name FROM scorable_routes")]
                return cols, names
            finally:
                await conn.close()

        cols, names = asyncio.run(check())
    assert cols == ["route_id", "area_id", "name", "grade", "disciplines", "type_group", "type_rule_version", "pitches",
                    "length_m", "bolts", "ob_climb_uuid", "source", "redistributable", "updated_at"]
    assert names == ["Kept"]


def test_0008_downgrade_refuses_while_areas_exist():
    with migrated_db("0008_catalog") as name:
        run_sql(name, AREA.format(id=A1, path="a" * 32))
        with pytest.raises(RuntimeError, match="canonical_areas has 1 rows"):
            command.downgrade(_alembic_cfg(name), "0007_cell_daily_conditions")


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
SCORABLE_COLUMNS = (
    "route_id, area_id, name, grade, disciplines, type_group, type_rule_version, pitches, length_m, bolts, "
    "ob_climb_uuid, source, redistributable, updated_at"
)


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
        f"CREATE VIEW scorable_routes AS SELECT {SCORABLE_COLUMNS} FROM canonical_routes "
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
    for table in (
        "internal.match_decisions", "internal.mp_route_links", "internal.mp_area_links", "public.accident_route_links",
        "public.mp_tick_counts", "public.canonical_routes", "public.canonical_areas",
    ):
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

Dependency (the `pipelines` group already exists; plan 3 created it with `timezonefinder`, and plan 3 already switched CI's `backend` job to `uv sync --frozen --group pipelines` and the audit to include the group):

```bash
cd backend && uv add --group pipelines 'h3>=4.1,<5'
```

Check `.github/workflows/ci.yml`: the `backend` job's sync and audit steps must name `--group pipelines` (plan 3); if a rebase lost that, restore `run: uv sync --frozen --group pipelines` and `uv export --frozen --no-dev --group pipelines --no-emit-project --format requirements.txt | uv run pip-audit -r /dev/stdin --require-hashes --disable-pip`. The Dockerfile keeps `uv sync --frozen --no-dev --no-install-project` (the `pipelines` group is not a default group, so the API image stays without `h3`).

Grants (`grants_phase2.sql`, "Plan 4 (0008)"):

```sql
GRANT SELECT, INSERT, UPDATE ON public.canonical_areas, public.canonical_routes TO ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.accident_route_links, public.mp_tick_counts TO ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON internal.mp_route_links, internal.mp_area_links TO ingest;
GRANT SELECT, INSERT, UPDATE ON internal.match_decisions TO ingest;
-- No trainer grants: trainer stays NOLOGIN with no privileges until Phase 3 (D13).
```

`verify_roles_phase2.sql` `ingest_writes`: add INSERT/UPDATE rows for `public.canonical_areas`, `public.canonical_routes`, `internal.match_decisions`; INSERT/UPDATE/DELETE rows for `public.accident_route_links`, `public.mp_tick_counts`, `internal.mp_route_links`, `internal.mp_area_links`.

- [ ] **Step 5: Run** — `cd backend && uv sync --group pipelines && uv run pytest tests/test_migration_0008.py tests/test_migrations.py tests/test_roles_phase2.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 6: Commit** — `git add backend/db/owner/ backend/alembic/versions/0008_catalog.py backend/app/models/ backend/tests/test_migration_0008.py backend/pyproject.toml backend/uv.lock backend/db/roles/ && git commit -m "feat(db): 0008 route catalog, internal MP links, tick-count aggregate; h3 in pipelines group"` (add `.github/workflows/ci.yml` only if you had to restore its `--group pipelines` lines)

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


def test_v_scale_and_uiaa_grades_are_never_commitment():
    assert tg({"trad"}, ["V4"]) == "trad"
    assert tg({"bouldering", "trad"}, ["V4"]) == "trad"
    assert tg({"sport"}, [None, "V+"]) == "sport"
    assert tg({"sport"}, ["VI+"]) == "sport"
    assert tg({"trad"}, ["5.9", "VI"]) == "trad"
    assert tg({"trad"}, ["5.10a V"]) == "alpine"
    assert tg({"trad"}, ["WI4 VI"]) == "ice"


def test_rock_rules_and_toprope_aid():
    assert tg({"trad", "sport"}) == "trad"
    assert tg({"sport"}) == "sport"
    assert tg(set(), ["5.10a"], bolts=6) == "sport"
    assert tg({"tr"}, bolts=3) == "sport"
    assert tg({"tr", "trad"}) == "trad"


def test_toprope_or_aid_without_bolt_info_is_unknown():
    # [default pending: DP2] spec rule 6: "if neither is known -> unknown"; OpenBeta has no gear flag.
    assert tg({"aid"}) == "unknown"
    assert tg({"tr"}) == "unknown"
    assert tg({"tr"}, bolts=0) == "unknown"
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
_NA_GRADE = re.compile(r"(?<![\w.])5\.\d|\b(?:WI|AI)\s?\d|(?<![A-Za-z])M\d|(?<![A-Za-z])[AC]\d", re.I)
_COMMITMENT = re.compile(r"(?:^|\s)(IV|V|VI|VII)(?=\s|$)")


def _has_commitment_grade(grade: str) -> bool:
    # A bare roman numeral is also a UIAA rock grade and "V4" is a boulder grade, so a
    # commitment grade counts only as its own token inside a YDS/ice/mixed/aid grade string.
    return bool(_NA_GRADE.search(grade)) and bool(_COMMITMENT.search(grade))


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
    grade_list = [g for g in grades if g]
    text = " ".join(grade_list)
    disciplines = tuple(sorted(f))
    if "bouldering" in f and not (f & ROPED):
        return TypeResult(None, True, disciplines)
    group: TypeGroup
    if "mixed" in f or _MIXED.search(text):
        group = "mixed"
    elif "ice" in f or _ICE.search(text):
        group = "ice"
    elif "alpine" in f or "snow" in f or any(_has_commitment_grade(g) for g in grade_list):
        group = "alpine"
    elif "trad" in f:
        group = "trad"
    elif "sport" in f or (bolts or 0) > 0:
        group = "sport"
    else:
        group = "unknown"
    return TypeResult(group, False, disciplines)
```

Rule 5's "rock with bolts" and rule 6's "toprope/aid with bolts → sport" collapse into the same `bolts > 0` branch; toprope/aid with no bolt information fall to `unknown` (plan decision 4, `[default pending: DP2]`; if the owner picks the alternative, the `else` branch returns `"trad"` when `f & {"tr", "aid"}` and the DP2 test flips). Commitment grades are checked per grade string, never on the joined text, because OpenBeta supplies YDS and UIAA in separate fields and a UIAA "VI" beside a YDS "5.9" is not a Grade VI route.

Append `"app.pipelines.route_types"` to strict mypy.

- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/route_types.py backend/tests/test_route_types.py backend/pyproject.toml && git commit -m "feat(pipelines): versioned route type-group mapper"`

---

### Task 3: OpenBeta GraphQL client (verify the live schema first)

**Files:**
- Create: `backend/app/pipelines/openbeta.py`, `backend/tests/test_openbeta.py`, `backend/tests/fixtures/openbeta_bulk_sample.json`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `OPENBETA_URL = "https://api.openbeta.io/"`, `MAX_PAGE = 2000`, `KNOWN_TICK_FIELDS = frozenset({"userTicks", "userTicksByClimbId"})`, `class ObClimbType(BaseModel)` (booleans `trad, sport, bouldering, deepwatersolo, alpine, snow, ice, mixed, aid, tr`), `class ObGrades(BaseModel)` (`yds, vscale, wi, french, uiaa, ewbank: str | None`), `class ObClimb(BaseModel)` (`uuid: uuid.UUID`, `name: str`, `length: float | None`, `boltsCount: int | None`, `type: ObClimbType`, `grades: ObGrades | None`, `mp_id: int | None`, `pitch_count: int | None`), `class ObArea(BaseModel)` (`uuid`, `name`, `path_tokens: list[str]`, `ancestors: list[uuid.UUID]`, `lat`, `lng: float | None` (`(0, 0)` → both `None`), `leaf: bool`, `is_boulder: bool`, `mp_id: int | None`, `climbs: list[ObClimb]`), `parse_mp_id(value: object) -> int | None`, `class OpenBetaClient(transport=None, page_size=MAX_PAGE, pause_s=0.5, retries=4, backoff_s=1.0)` with `us_states() -> tuple[uuid.UUID, list[tuple[uuid.UUID, str]]]` (USA root uuid, states), `bulk_areas(usa_uuid: uuid.UUID, state_uuid: uuid.UUID) -> list[ObArea]`, `query_fields() -> set[str]`, `climb_tick_count(climb_uuid: uuid.UUID) -> int`, `class OpenBetaError(Exception)`.

- [ ] **Step 1: Verify the live API shape (agent, read-only, no credentials)**

```bash
curl -s https://api.openbeta.io/ -H 'content-type: application/json' \
  -d '{"query":"{ __type(name: \"Query\") { fields { name args { name type { name kind ofType { name } } } } } }"}' \
  | python3 -m json.tool > /tmp/ob_query.json
grep -E '"name": "(area|areas|bulkAreas|ancestors|filter|limit|offset|sort|userTicks|userTicksByClimbId|climbId|userId)"' /tmp/ob_query.json
curl -s https://api.openbeta.io/ -H 'content-type: application/json' \
  -d '{"query":"{ __type(name: \"Area\") { fields { name } } }"}' | python3 -m json.tool | grep '"name"'
curl -s https://api.openbeta.io/ -H 'content-type: application/json' \
  -d '{"query":"{ __type(name: \"TickType\") { fields { name } } }"}' | python3 -m json.tool | grep '"name"'
```

Expected (introspected 2026-09-28 by the plan review): `bulkAreas(ancestors: [String!]!, limit, offset)` where `ancestors` must hold **at least two ancestor UUIDs** (the USA root and the state), default limit 500, maximum 2000; `Area` fields including `uuid`, `areaName`, `pathTokens`, `ancestors`, `metadata`, `climbs`; `metadata.mp_id` is a **String**; ticks only through `userTicks` and `userTicksByClimbId` (no bulk tick query). Adjust only the query text and the matching `Field(alias=…)` below if a name differs, and note it in the PR:
- if `bulkAreas` has a sort/order argument, add it to `BULK_QUERY` (ordered by `uuid`); the client never relies on it (see `_page_all`);
- if the tick type's date field is not `dateClimbed`, or `userTicksByClimbId` requires a `userId`, change `TICKS_QUERY` accordingly; if it cannot be queried without a user, Task 11 records `not_measurable` and exits (its revisit trigger still runs weekly).

- [ ] **Step 2: Record the fixture** (CC0; media never requested)

Run `BULK_QUERY` (below) with `ancestors = [<USA uuid>, <a small state uuid, e.g. Delaware>]` from `us_states()`, save as `backend/tests/fixtures/openbeta_bulk_sample.json`, then **replace every `mp_id` value with a synthetic id ≥ 900000000, as a string** (the fixture must not carry real MP ids; Task 8's guard enforces it). Keep 2–3 areas and ≤10 climbs.

- [ ] **Step 3: Failing tests** — `backend/tests/test_openbeta.py`:

```python
import json
import uuid
from pathlib import Path

import httpx
import pytest

from app.pipelines.openbeta import ObArea, OpenBetaClient, OpenBetaError, parse_mp_id

FIXTURE = Path(__file__).parent / "fixtures" / "openbeta_bulk_sample.json"
USA, STATE = uuid.UUID(int=1), uuid.UUID(int=2)


def _area(n: int, **metadata: object) -> dict[str, object]:
    return {"uuid": str(uuid.UUID(int=1000 + n)), "areaName": f"A{n}", "pathTokens": ["USA", f"A{n}"],
            "ancestors": [str(USA), str(STATE), str(uuid.UUID(int=1000 + n))],
            "metadata": {"lat": 40.0, "lng": -105.0, "leaf": True, "isBoulder": False, "mp_id": None} | metadata,
            "climbs": []}


def _client(handler, page_size: int = 2000) -> OpenBetaClient:
    return OpenBetaClient(transport=httpx.MockTransport(handler), page_size=page_size)


def test_fixture_parses_into_areas_and_climbs():
    body = json.loads(FIXTURE.read_text())
    areas = _client(lambda request: httpx.Response(200, json=body), page_size=10).bulk_areas(USA, STATE)
    assert areas and all(a.name for a in areas)
    assert any(a.climbs for a in areas)
    assert all(a.lat is None or -90 <= a.lat <= 90 for a in areas)


def test_bulk_query_sends_usa_and_state_ancestors():
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.read())["variables"])
        return httpx.Response(200, json={"data": {"bulkAreas": [_area(1)]}})

    _client(handler).bulk_areas(USA, STATE)
    assert seen[0]["ancestors"] == [str(USA), str(STATE)]
    assert seen[0]["limit"] == 2000


def test_page_size_above_the_server_maximum_is_refused():
    with pytest.raises(ValueError, match="2000"):
        OpenBetaClient(page_size=2001)


def test_query_never_requests_media():
    from app.pipelines import openbeta

    assert "media" not in openbeta.BULK_QUERY.lower()


def test_graphql_errors_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "bad field"}]})

    with pytest.raises(OpenBetaError, match="bad field"):
        _client(handler).us_states()


def test_empty_body_and_5xx_are_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={})
        if calls["n"] == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": {"bulkAreas": [_area(1)]}})

    assert len(_client(handler).bulk_areas(USA, STATE)) == 1
    assert calls["n"] == 3


def test_retries_are_bounded():
    with pytest.raises(OpenBetaError, match="after 4 attempts"):
        _client(lambda request: httpx.Response(502)).us_states()


def test_duplicate_ids_across_pages_mean_unstable_paging():
    def handler(request: httpx.Request) -> httpx.Response:
        offset = json.loads(request.read())["variables"]["offset"]
        page = [_area(1), _area(2)] if offset == 0 else [_area(2)]
        return httpx.Response(200, json={"data": {"bulkAreas": page}})

    with pytest.raises(OpenBetaError, match="unstable paging"):
        _client(handler, page_size=2).bulk_areas(USA, STATE)


def test_zero_zero_is_missing_not_a_location():
    area = ObArea.model_validate(_area(1, lat=0.0, lng=0.0))
    assert (area.lat, area.lng) == (None, None)


def test_mp_id_string_is_parsed_and_junk_is_dropped():
    assert parse_mp_id("900000123") == 900000123
    assert parse_mp_id(" 900000123 ") == 900000123
    assert parse_mp_id(900000123) == 900000123
    assert parse_mp_id("") is None
    assert parse_mp_id("abc") is None
    assert parse_mp_id("-5") is None
    assert parse_mp_id("0") is None
    assert parse_mp_id(True) is None
    assert ObArea.model_validate(_area(1, mp_id="900000124")).mp_id == 900000124


def test_ancestors_accept_comma_string_or_list():
    base = _area(1)
    ids = f"{USA},{STATE}"
    assert len(ObArea.model_validate(base | {"ancestors": ids}).ancestors) == 2
    assert len(ObArea.model_validate(base | {"ancestors": ids.split(",")}).ancestors) == 2


def test_query_fields_lists_the_root_fields():
    body = {"data": {"__type": {"fields": [{"name": "areas"}, {"name": "userTicks"}]}}}
    assert _client(lambda request: httpx.Response(200, json=body)).query_fields() == {"areas", "userTicks"}
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
from pydantic import AliasPath, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

OPENBETA_URL = "https://api.openbeta.io/"
MAX_PAGE = 2000
KNOWN_TICK_FIELDS = frozenset({"userTicks", "userTicksByClimbId"})
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
FIELDS_QUERY = """query { __type(name: "Query") { fields { name } } }"""
TICKS_QUERY = """query Ticks($climbId: String!) { userTicksByClimbId(climbId: $climbId) { dateClimbed } }"""


class OpenBetaError(Exception):
    pass


def parse_mp_id(value: object) -> int | None:
    # OpenBeta stores mp_id as a String; anything that is not a positive integer is missing.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


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
    def _mp_id(cls, value: object) -> int | None:
        return parse_mp_id(value)

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
    def _mp_id(cls, value: object) -> int | None:
        return parse_mp_id(value)

    @model_validator(mode="after")
    def _null_island(self) -> ObArea:
        # OpenBeta uses (0, 0) as "no coordinates"; it is never a US climbing area.
        if self.lat == 0 and self.lng == 0:
            self.lat = None
            self.lng = None
        return self


class OpenBetaClient:
    def __init__(
        self, *, transport: httpx.BaseTransport | None = None, page_size: int = MAX_PAGE, pause_s: float = 0.5,
        retries: int = 4, backoff_s: float = 1.0,
    ) -> None:
        if not 1 <= page_size <= MAX_PAGE:
            raise ValueError(f"page_size must be 1..{MAX_PAGE} (bulkAreas maximum)")
        self._client = httpx.Client(transport=transport, timeout=120.0, headers={"User-Agent": "SafeAscent data pipeline"})
        self._page = page_size
        self._pause = pause_s if transport is None else 0.0
        self._retries = retries
        self._backoff = backoff_s if transport is None else 0.0

    def _post_once(self, query: str, variables: dict[str, Any] | None) -> dict[str, Any] | None:
        """One attempt: the data dict, or None when the attempt should be retried."""
        try:
            response = self._client.post(OPENBETA_URL, json={"query": query, "variables": variables or {}})
        except httpx.HTTPError:
            return None
        if response.status_code >= 500 or response.status_code == 429:
            return None
        if response.status_code != 200:
            raise OpenBetaError(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            return None
        if body.get("errors"):
            raise OpenBetaError("; ".join(str(e.get("message")) for e in body["errors"]))
        data = body.get("data")
        return data if isinstance(data, dict) and data else None

    def _post(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(self._retries):
            data = self._post_once(query, variables)
            if data is not None:
                return data
            time.sleep(self._backoff * 2**attempt)
        raise OpenBetaError(f"no usable response after {self._retries} attempts")

    def us_states(self) -> tuple[uuid.UUID, list[tuple[uuid.UUID, str]]]:
        roots = self._post(STATES_QUERY).get("areas") or []
        if len(roots) != 1:
            raise OpenBetaError(f"expected one USA root area, got {len(roots)}")
        states = [(uuid.UUID(c["uuid"]), str(c["areaName"])) for c in roots[0]["children"]]
        if not states:
            raise OpenBetaError("USA root has no child areas")
        return uuid.UUID(roots[0]["uuid"]), states

    def _page_all(self, ancestors: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._post(BULK_QUERY, {"ancestors": ancestors, "limit": self._page, "offset": offset}).get("bulkAreas") or []
            rows += page
            if len(page) < self._page:
                return rows
            offset += self._page
            time.sleep(self._pause)

    def bulk_areas(self, usa_uuid: uuid.UUID, state_uuid: uuid.UUID) -> list[ObArea]:
        ancestors = [str(usa_uuid), str(state_uuid)]
        for _ in range(2):
            rows = self._page_all(ancestors)
            # Offset paging over an unordered result can repeat one row and skip another; with a
            # fixed total, a repeat is the only visible sign of a skip, so any repeat rejects the pass.
            if len({r.get("uuid") for r in rows}) == len(rows):
                break
        else:
            raise OpenBetaError(f"unstable paging for state {state_uuid}")
        try:
            return [ObArea.model_validate(a) for a in rows]
        except ValidationError as exc:
            raise OpenBetaError(f"invalid area: {exc.errors()[0]['msg']}") from None

    def query_fields(self) -> set[str]:
        fields = (self._post(FIELDS_QUERY).get("__type") or {}).get("fields") or []
        return {str(f["name"]) for f in fields}

    def climb_tick_count(self, climb_uuid: uuid.UUID) -> int:
        return len(self._post(TICKS_QUERY, {"climbId": str(climb_uuid)}).get("userTicksByClimbId") or [])
```

A body without `data` (seen in 2 of 12 review calls), a 429 or a 5xx is retried with exponential backoff; a well-formed empty list is accepted as-is, and Task 4's per-state drop check catches a state that silently shrinks.

Append `"app.pipelines.openbeta"` to strict mypy.

- [ ] **Step 6: Run** — `cd backend && uv run pytest tests/test_openbeta.py -q && uv run mypy` → PASS.
- [ ] **Step 7: Commit** — `git add backend/app/pipelines/openbeta.py backend/tests/test_openbeta.py backend/tests/fixtures/openbeta_bulk_sample.json backend/pyproject.toml && git commit -m "feat(pipelines): OpenBeta GraphQL client (CC0, no media) with retries, paging check, recorded fixture"`

---

### Task 4: Catalog loader, weekly workflow, job pings

**Files:**
- Create: `backend/app/pipelines/catalog.py`, `backend/app/pipelines/jobping.py`, `backend/tests/test_catalog.py`, `.github/workflows/data-openbeta.yml`
- Modify: `backend/app/config.py`, `.env.example`, `backend/pyproject.toml`

**Interfaces:**
- Consumes: Tasks 1–3; `validate`, `ingest_log` (plan 1).
- Produces: `SOURCE = "openbeta_weekly"`, `MAX_DROP = 0.03`, `MAX_STATE_DROP = 0.10`, `STATE_MIN_PREVIOUS = 100`, `ltree_label(u: uuid.UUID) -> str`, `state_drop_problems(previous: Mapping[str, int], current: Mapping[str, int], *, max_state_drop: float, state_min_previous: int) -> list[str]`, `@dataclass CatalogBatch(areas: list[AreaRow], routes: list[RouteRow], area_mp_links: list[tuple[uuid.UUID, int]], route_mp_links: list[tuple[uuid.UUID, int]])`, `build_batch(areas: list[ObArea], report: ValidationReport) -> CatalogBatch`, `async upsert_batch(conn, batch, *, run_id) -> int`, `async retire_unseen(conn, *, run_id) -> int`, `async run_weekly(engine_factory, client, *, max_drop: float = MAX_DROP, max_state_drop: float = MAX_STATE_DROP, state_min_previous: int = STATE_MIN_PREVIOUS) -> dict[str, object]` (result carries `revisit_d17`: tick query fields OpenBeta added beyond `KNOWN_TICK_FIELDS`, or `None` when the schema check failed); `jobping.job_ping(slug: str, suffix: PingSuffix = "") -> bool`; `Settings.HEALTHCHECKS_PING_KEY: str | None = None`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_catalog.py`:

```python
import asyncio
import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.catalog import build_batch, ltree_label, run_weekly, state_drop_problems
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


def _state_client(counts: dict[uuid.UUID, int], tick_fields: tuple[str, ...] = ()) -> OpenBetaClient:
    usa = uuid.UUID(int=99)

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        if "__type" in body:
            return httpx.Response(200, json={"data": {"__type": {"fields": [
                {"name": n} for n in ("areas", "userTicks", "userTicksByClimbId", *tick_fields)]}}})
        if "USA" in body:
            return httpx.Response(200, json={"data": {"areas": [{"uuid": str(usa), "children": [
                {"uuid": str(s), "areaName": f"S{s.int}"} for s in counts]}]}})
        for s, n in counts.items():
            if str(s) in body:
                crag = uuid.UUID(int=s.int * 1000)
                return httpx.Response(200, json={"data": {"bulkAreas": [
                    {"uuid": str(s), "areaName": f"S{s.int}", "pathTokens": ["USA", f"S{s.int}"],
                     "ancestors": [str(usa), str(s)],
                     "metadata": {"lat": None, "lng": None, "leaf": False, "isBoulder": False}, "climbs": []},
                    {"uuid": str(crag), "areaName": "Crag", "pathTokens": ["USA", f"S{s.int}", "Crag"],
                     "ancestors": [str(usa), str(s), str(crag)],
                     "metadata": {"lat": 40.0, "lng": -105.3, "leaf": True, "isBoulder": False},
                     "climbs": [climb(uuid.UUID(int=s.int * 1000 + i + 1), f"R{i}", ["trad"]) for i in range(n)]},
                ]}})
        return httpx.Response(200, json={"data": {"bulkAreas": []}})

    return OpenBetaClient(transport=httpx.MockTransport(handler), page_size=500)


def test_state_drop_problems_ignore_small_states_and_growth():
    previous = {"Big": 200, "Small": 20, "Grows": 150}
    current = {"Big": 170, "Small": 5, "Grows": 300}
    assert state_drop_problems(previous, current, max_state_drop=0.10, state_min_previous=100) == [
        "state Big climbs 170 dropped more than 10% from 200"]
    assert state_drop_problems({"Gone": 150}, {}, max_state_drop=0.10, state_min_previous=100) == [
        "state Gone climbs 0 dropped more than 10% from 150"]


@requires_pg
def test_single_state_drop_rejects_the_run():
    first_state, second_state = uuid.UUID(int=10), uuid.UUID(int=20)

    async def scenario(url: str) -> tuple[dict[str, object], dict[str, object], int]:
        def factory():
            return create_async_engine(url)

        first = await run_weekly(factory, _state_client({first_state: 10, second_state: 90}), state_min_previous=5)
        second = await run_weekly(factory, _state_client({first_state: 5, second_state: 95}), state_min_previous=5)
        engine = factory()
        try:
            async with engine.connect() as conn:
                retired = (await conn.execute(text("SELECT count(*) FROM canonical_routes WHERE retired_at IS NOT NULL"))).scalar_one()
        finally:
            await engine.dispose()
        return first, second, int(retired)

    with migrated_db() as name:
        first, second, retired = asyncio.run(scenario(sa_url(name)))
    assert first["status"] == "ok" and first["routes"] == 100
    assert second["status"] == "rejected"
    assert second["problems"] == ["state S10 climbs 5 dropped more than 10% from 10"]
    assert retired == 0


@requires_pg
def test_new_tick_query_field_is_reported_for_d17():
    async def scenario(url: str) -> dict[str, object]:
        return await run_weekly(lambda: create_async_engine(url), _state_client({uuid.UUID(int=10): 1}, ("bulkTicks",)))

    with migrated_db() as name:
        result = asyncio.run(scenario(sa_url(name)))
    assert result["status"] == "ok" and result["revisit_d17"] == ["bulkTicks"]


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
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.ingest_log import finish_run, last_ok_rows_in, start_run, write_quarantine
from app.pipelines.openbeta import KNOWN_TICK_FIELDS, ObArea, OpenBetaClient, OpenBetaError
from app.pipelines.route_types import TYPE_RULE_VERSION, map_type_group
from app.pipelines.validate import ValidationReport, coord_problem

SOURCE = "openbeta_weekly"
MAX_DROP = 0.03
MAX_STATE_DROP = 0.10
STATE_MIN_PREVIOUS = 100


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


def state_drop_problems(
    previous: Mapping[str, int], current: Mapping[str, int], *, max_state_drop: float, state_min_previous: int
) -> list[str]:
    return [
        f"state {name} climbs {current.get(name, 0)} dropped more than {max_state_drop:.0%} from {before}"
        for name, before in sorted(previous.items())
        if before >= state_min_previous and current.get(name, 0) < before * (1 - max_state_drop)
    ]


def _state_counts(batch: CatalogBatch, states: list[tuple[uuid.UUID, str]]) -> dict[str, int]:
    area_labels = {a.area_id: set(a.path.split(".")) for a in batch.areas}
    return {
        name: sum(1 for r in batch.routes if ltree_label(state_uuid) in area_labels.get(r.area_id, set()))
        for state_uuid, name in states
    }


async def _previous_state_counts(conn: AsyncConnection, states: list[tuple[uuid.UUID, str]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for state_uuid, name in states:
        out[name] = int((await conn.execute(text(
            "SELECT count(*) FROM canonical_routes r JOIN canonical_areas a USING (area_id) "
            "WHERE r.source = 'openbeta' AND r.retired_at IS NULL AND a.path ~ CAST(:q AS lquery)"),
            {"q": f"*.{ltree_label(state_uuid)}.*"})).scalar_one())
    return out


def _new_tick_fields(client: OpenBetaClient) -> list[str] | None:
    try:
        fields = client.query_fields()
    except OpenBetaError:
        return None
    return sorted(f for f in fields if "tick" in f.lower() and f not in KNOWN_TICK_FIELDS)


async def run_weekly(
    engine_factory: Callable[[], AsyncEngine], client: OpenBetaClient, *, max_drop: float = MAX_DROP,
    max_state_drop: float = MAX_STATE_DROP, state_min_previous: int = STATE_MIN_PREVIOUS,
) -> dict[str, object]:
    report = ValidationReport(SOURCE)
    all_areas: list[ObArea] = []
    problems: list[str] = []
    states: list[tuple[uuid.UUID, str]] = []
    try:
        usa_uuid, states = client.us_states()
        for state_uuid, state_name in states:
            try:
                all_areas += client.bulk_areas(usa_uuid, state_uuid)
            except OpenBetaError as exc:
                problems.append(f"{state_name}: {exc}")
    except OpenBetaError as exc:
        problems.append(f"states: {exc}")
    batch = build_batch(all_areas, report) if not problems else CatalogBatch()
    revisit_d17 = _new_tick_fields(client)
    engine = engine_factory()
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=None)
            if problems:
                await finish_run(conn, run_id, status="failed", report=report, rows_upserted=0, problems=problems)
                return {"status": "failed", "problems": problems}
            previous = await last_ok_rows_in(conn, SOURCE)
            climbs = len(batch.routes)
            rejections = state_drop_problems(
                await _previous_state_counts(conn, states), _state_counts(batch, states),
                max_state_drop=max_state_drop, state_min_previous=state_min_previous,
            )
            if previous and climbs < previous * (1 - max_drop):
                rejections.insert(0, f"US climbs {climbs} dropped more than {max_drop:.0%} from {previous}")
            if rejections:
                await finish_run(conn, run_id, status="rejected", report=report, rows_upserted=0, problems=rejections)
                return {"status": "rejected", "problems": rejections}
            await write_quarantine(conn, run_id, report)
            n = await upsert_batch(conn, batch, run_id=run_id)
            retired = await retire_unseen(conn, run_id=run_id)
            # rows_in carries the climb count so the next run's drop check compares like with like.
            report.rows_in = climbs
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n)
            return {"status": "ok", "areas": len(batch.areas), "routes": climbs, "retired": retired,
                    "quarantined": report.quarantined_total(), "revisit_d17": revisit_d17}
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
        run: uv run python -m app.pipelines.catalog | tee "$RUNNER_TEMP/openbeta.json"
      - name: Open an issue when OpenBeta adds a tick query (D17 revisit trigger)
        working-directory: .
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          fields="$(python3 -c 'import json,os,sys; r=json.load(open(os.environ["RUNNER_TEMP"]+"/openbeta.json")); print(",".join(r.get("revisit_d17") or []))')"
          open_issues="$(gh issue list --state open --search 'in:title "OpenBeta tick query available"' --json number --jq length)"
          if [ -n "$fields" ] && [ "$open_issues" = "0" ]; then
            gh issue create --title "OpenBeta tick query available: revisit D17/P2-9" --body "New Query fields: $fields"
          fi
      - name: Open an issue on failure
        if: failure()
        working-directory: .
        env:
          GH_TOKEN: ${{ github.token }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: gh issue create --title "data-openbeta failed ($(date -u +%F))" --body "Run: $RUN_URL"
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
- Consumes: `textsim.normalize_text`, `textsim.place_key`, `geo.haversine_km` (plan 2 Task 5). Prefix-weighted `textsim.jaro_winkler` is deliberately **not** used for names (it links "The Book" to "The Book Direct").
- Produces: `AUTO_LINK = 0.90`, `NO_LINK_BELOW = 0.80`, `AUTO_MARGIN = 0.05`, `REVIEW_CAP = 0.89`, `AREA_RADIUS_KM = 2.0`, `levenshtein_ratio(a: str, b: str) -> float`, `name_similarity(a: str, b: str) -> float`, `area_score(mp_name: str, mp_tokens: Sequence[str], mp_lat: float, mp_lon: float, ob_name: str, ob_tokens: Sequence[str], ob_lat: float, ob_lon: float) -> float`, `grade_systems(grade: str | None) -> dict[str, str]`, `grade_similarity(a: str | None, b: str | None) -> float | None`, `climb_score(mp_name: str, mp_grade: str | None, mp_type: str | None, ob_name: str, ob_grade: str | None, ob_type: str | None) -> float`, `Decision = Literal["link", "no_link", "review"]`, `decide(scores: Sequence[tuple[uuid.UUID, float]]) -> tuple[Decision, uuid.UUID | None, float]`, `h3_neighbourhood(lat: float, lon: float, res: int = 7) -> set[str]`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_match.py`:

```python
import uuid

import pytest

from app.pipelines.match import (
    AUTO_LINK,
    NO_LINK_BELOW,
    REVIEW_CAP,
    area_score,
    climb_score,
    decide,
    grade_similarity,
    grade_systems,
    h3_neighbourhood,
    levenshtein_ratio,
    name_similarity,
)

A, B = uuid.UUID(int=1), uuid.UUID(int=2)


def test_area_score_components():
    # "USA" is dropped from path tokens, so identical paths overlap fully.
    same = area_score("Fixture Ridge", ["Colorado", "Fixture Park", "Fixture Ridge"], 40.39, -105.51,
                      "Fixture Ridge", ["USA", "Colorado", "Fixture Park", "Fixture Ridge"], 40.39, -105.51)
    assert same == pytest.approx(1.0)
    far = area_score("Fixture Ridge", ["Fixture Ridge"], 40.39, -105.51, "Fixture Ridge", ["Fixture Ridge"], 40.50, -105.51)
    assert far == pytest.approx(0.8)


def test_area_without_path_tokens_can_reach_review_but_never_auto_link():
    score = area_score("Fixture Crag", [], 40.0, -105.3, "Fixture Crag", [], 40.0, -105.3)
    assert score == pytest.approx(REVIEW_CAP)
    assert NO_LINK_BELOW <= score < AUTO_LINK


def test_levenshtein_ratio():
    assert levenshtein_ratio("route", "rout") == pytest.approx(0.8)
    assert levenshtein_ratio("", "") == 1.0
    assert levenshtein_ratio("abc", "") == 0.0


def test_name_similarity_aligns_whole_tokens():
    assert name_similarity("The Book", "the book!") == 1.0
    assert name_similarity("The Book", "The Book Direct") == pytest.approx(0.8)
    assert name_similarity("Fixture Rout", "Fixture Route") == pytest.approx(0.9)
    assert name_similarity("Book", "The Book") == pytest.approx(2 / 3)
    assert name_similarity("", "Book") == 0.0


def test_grade_similarity():
    assert grade_similarity("5.10a", "5.10a") == 1.0
    assert grade_similarity("5.10a", "5.10c") == 0.5
    assert grade_similarity("WI4", "WI4+") == 0.5
    assert grade_similarity("5.9", "5.11a") == 0.0
    assert grade_similarity("WI4 M5", "WI4 M7") == 0.0
    assert grade_similarity(None, "5.9") is None


def test_protection_ratings_are_ignored():
    assert grade_similarity("5.9 PG13", "5.9") == 1.0
    assert grade_similarity("5.10a R", "5.10a") == 1.0
    assert grade_similarity("5.11 X", "5.11") == 1.0
    assert grade_similarity("5.9 IV", "5.9") == 1.0


def test_cross_system_grades_are_not_comparable():
    assert grade_systems("5.10a") == {"yds": "5.10a"}
    assert grade_systems("V4") == {"v": "v4"}
    assert grade_similarity("5.10a", "WI4") is None
    assert grade_similarity("V4", "5.10a") is None
    assert grade_similarity("6a", "5.10a") is None
    score = climb_score("Fixture Line", "5.10a", "mixed", "Fixture Line", "WI4", "mixed")
    assert score == pytest.approx(REVIEW_CAP) and score < AUTO_LINK


def test_climb_score():
    assert climb_score("The Book", "5.7", "trad", "The Book", "5.7", "trad") == pytest.approx(1.0)
    assert climb_score("The Book", "5.7", "trad", "Book", "5.8", "sport") < NO_LINK_BELOW
    assert climb_score("Fixture", "5.7", "unknown", "Fixture", "5.7", "unknown") == pytest.approx(0.85)


def test_variant_names_never_auto_link():
    score = climb_score("The Book", "5.7", "trad", "The Book Direct", "5.7", "trad")
    assert NO_LINK_BELOW <= score < AUTO_LINK


def test_decisions():
    assert decide([(A, 0.95), (B, 0.60)]) == ("link", A, 0.95)
    assert decide([(A, 0.70)]) == ("no_link", None, 0.70)
    assert decide([(A, 0.85)]) == ("review", A, 0.85)
    assert decide([]) == ("no_link", None, 0.0)


def test_two_strong_candidates_go_to_review():
    assert decide([(A, 0.95), (B, 0.93)]) == ("review", A, 0.95)
    assert decide([(A, 0.99), (B, 0.91)]) == ("review", A, 0.99)


def test_runner_up_margin_sends_close_calls_to_review():
    assert decide([(A, 0.93), (B, 0.89)]) == ("review", A, 0.93)
    assert decide([(A, 0.96), (B, 0.88)]) == ("link", A, 0.96)


def test_ties_are_ordered_deterministically():
    assert decide([(B, 0.85), (A, 0.85)]) == ("review", A, 0.85)


def test_h3_neighbourhood_has_the_cell_and_its_ring():
    cells = h3_neighbourhood(40.0, -105.3)
    assert len(cells) == 7
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** `backend/app/pipelines/match.py` (pure part; Task 6 appends the job):

```python
"""In-house matcher: MP areas/routes → canonical OpenBeta records (spec §Matching).

Areas: 0.5·name + 0.3·path-token overlap + 0.2·distance decay to 0 at 2 km, among areas in
the same or a neighbouring H3 r7 cell. Climbs within matched areas: 0.6·name + 0.25·grade +
0.15·type group. An auto-link needs ≥0.90, no second candidate ≥0.90, and a 0.05 lead over
the runner-up; <0.80 does not link; everything between goes to the owner. Missing evidence
(no comparable grade, no path tokens) renormalizes the remaining weights but caps the score
below the auto-link threshold, so it can reach review and never auto-links.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Literal

import h3

from app.pipelines.geo import haversine_km
from app.pipelines.textsim import normalize_text, place_key

AUTO_LINK = 0.90
NO_LINK_BELOW = 0.80
AUTO_MARGIN = 0.05
REVIEW_CAP = 0.89
AREA_RADIUS_KM = 2.0
TOKEN_MATCH_MIN = 0.8
Decision = Literal["link", "no_link", "review"]

_PROTECTION = re.compile(r"(?<![a-z0-9])(?:pg-?13|pg|r|x)(?![a-z0-9])")
_COMMITMENT = re.compile(r"(?<![a-z0-9])(?:grade\s+)?(?:iii|iv|v|vi|vii)(?![a-z0-9+\-])")
_SYSTEMS: dict[str, re.Pattern[str]] = {
    "yds": re.compile(r"(?<![\d.])5\.\d{1,2}(?:[abcd](?:/[abcd])?)?[+-]?"),
    "ice": re.compile(r"(?<![a-z])(?:wi|ai)\s?\d(?:\.\d)?[+-]?"),
    "mixed": re.compile(r"(?<![a-z])m\d{1,2}[+-]?"),
    "v": re.compile(r"(?<![a-z])v(?:\d{1,2}|b)(?:-\d{1,2}|[+-])?"),
    "aid": re.compile(r"(?<![a-z])[ac]\d[+-]?"),
}
_SUFFIX = re.compile(r"(?:[abcd](?:/[abcd])?)?[+-]?(?:-\d{1,2})?$")


def levenshtein_ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return 1.0 - previous[-1] / max(len(a), len(b))


def _token_alignment(x: Sequence[str], y: Sequence[str]) -> float:
    if not x or not y:
        return 0.0
    pairs = sorted(
        ((levenshtein_ratio(s, t), i, j) for i, s in enumerate(x) for j, t in enumerate(y)),
        key=lambda p: (-p[0], p[1], p[2]),
    )
    used_x: set[int] = set()
    used_y: set[int] = set()
    matched = 0.0
    for ratio, i, j in pairs:
        if ratio < TOKEN_MATCH_MIN:
            break
        if i in used_x or j in used_y:
            continue
        used_x.add(i)
        used_y.add(j)
        matched += ratio
    # Unmatched tokens on either side count against the score, so an extra "Direct" or
    # "Variation" keeps a variant out of the auto-link band.
    return 2 * matched / (len(x) + len(y))


def name_similarity(a: str, b: str) -> float:
    return _token_alignment(normalize_text(a).split(), normalize_text(b).split())


def _tokens(tokens: Sequence[str]) -> set[str]:
    return {normalize_text(t) for t in tokens if t and normalize_text(t) not in ("usa", "united states")}


def area_score(
    mp_name: str, mp_tokens: Sequence[str], mp_lat: float, mp_lon: float,
    ob_name: str, ob_tokens: Sequence[str], ob_lat: float, ob_lon: float,
) -> float:
    name = _token_alignment(place_key(mp_name).split(), place_key(ob_name).split())
    decay = max(0.0, 1.0 - haversine_km(mp_lat, mp_lon, ob_lat, ob_lon) / AREA_RADIUS_KM)
    a, b = _tokens(mp_tokens), _tokens(ob_tokens)
    if not a or not b:
        return min(REVIEW_CAP, (0.5 * name + 0.2 * decay) / 0.7)
    overlap = len(a & b) / len(a | b)
    return 0.5 * name + 0.3 * overlap + 0.2 * decay


def grade_systems(grade: str | None) -> dict[str, str]:
    cleaned = _COMMITMENT.sub(" ", _PROTECTION.sub(" ", (grade or "").lower()))
    out: dict[str, str] = {}
    for system, pattern in _SYSTEMS.items():
        found = pattern.search(cleaned)
        if found:
            out[system] = re.sub(r"\s+", "", found.group(0))
    return out


def grade_similarity(a: str | None, b: str | None) -> float | None:
    x, y = grade_systems(a), grade_systems(b)
    shared = sorted(x.keys() & y.keys())
    if not shared:
        return None
    scores = [1.0 if x[s] == y[s] else 0.5 if _SUFFIX.sub("", x[s]) == _SUFFIX.sub("", y[s]) else 0.0 for s in shared]
    return min(scores)


def climb_score(
    mp_name: str, mp_grade: str | None, mp_type: str | None, ob_name: str, ob_grade: str | None, ob_type: str | None
) -> float:
    name = name_similarity(mp_name, ob_name)
    known = {"sport", "trad", "alpine", "ice", "mixed"}
    same_type = 1.0 if mp_type in known and mp_type == ob_type else 0.0
    grade = grade_similarity(mp_grade, ob_grade)
    if grade is None:
        return min(REVIEW_CAP, (0.6 * name + 0.15 * same_type) / 0.75)
    return 0.6 * name + 0.25 * grade + 0.15 * same_type


def decide(scores: Sequence[tuple[uuid.UUID, float]]) -> tuple[Decision, uuid.UUID | None, float]:
    if not scores:
        return "no_link", None, 0.0
    ranked = sorted(scores, key=lambda s: (-s[1], str(s[0])))
    best_id, best = ranked[0]
    if best < NO_LINK_BELOW:
        return "no_link", None, best
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    unique = runner_up < AUTO_LINK
    if best >= AUTO_LINK and unique and best - runner_up >= AUTO_MARGIN - 1e-9:
        return "link", best_id, best
    return "review", best_id, best


def h3_neighbourhood(lat: float, lon: float, res: int = 7) -> set[str]:
    return set(h3.grid_disk(h3.latlng_to_cell(lat, lon, res), 1))
```

Worked values: the area test's second case (same name and tokens, 12 km apart) is `0.5 + 0.3 + 0 = 0.8`; an area pair with no path tokens (plan 5's R10 legacy fallback) scores at most `REVIEW_CAP`, and a climb pair without a comparable grade (R10 legacy routes, cross-system grades) likewise, so R10 sends such pairs to owner review rather than linking or discarding them. "The Book" vs "The Book Direct" is `0.6·0.8 + 0.25 + 0.15 = 0.88`: review.

Append `"app.pipelines.match"` to strict mypy.

- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/match.py backend/tests/test_match.py backend/pyproject.toml && git commit -m "feat(pipelines): matcher scoring with token-aligned names, per-system grades, margin-gated auto links"`

---

### Task 6: Matcher job, owner review round-trip, precision evaluation

**Files:**
- Modify: `backend/app/pipelines/match.py`, `backend/tests/test_match.py`

**Interfaces:**
- Produces: `async run_match(conn, *, apply: bool, review_out: Path) -> dict[str, object]` (areas first, then climbs within matched areas; writes `internal.mp_area_links`/`mp_route_links` for links with method `auto`, never touching `ob_mp_id`/`owner` rows; routes already promoted by `mp_facts` are matched again so Task 7 can retire them; owner `no_link` decisions exclude that pair), `async import_review(conn, path: Path) -> int`, `precision_at_auto(predicted: Mapping[tuple[int, uuid.UUID], Decision], golden: Mapping[tuple[int, uuid.UUID], bool]) -> float | None`, `@dataclass(frozen=True) AutoLink(kind: str, mp_id: int, ob_uuid: uuid.UUID, score: float, type_group: str)`, `score_band(score: float) -> str`, `stratum_of(link: AutoLink) -> str`, `stratified_sample(links: Sequence[AutoLink], *, n: int, seed: int, min_per_stratum: int = 20) -> list[AutoLink]`, `weighted_precision(labels: Mapping[str, tuple[int, int]], sizes: Mapping[str, int]) -> float | None`, `PRECISION_GATE = 0.98`, `SAMPLE_SIZES = {"climb": 300, "area": 100}`, CLI `python -m app.pipelines.match run [--apply] [--out PATH] | import --review-file PATH | sample --out PATH [--seed N] | eval --golden PATH`.

- [ ] **Step 1: Failing tests** — append to `backend/tests/test_match.py`:

```python
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.match import AutoLink, precision_at_auto, run_match, score_band, stratified_sample, stratum_of, weighted_precision
from tests.pgtest import migrated_db, requires_pg, sa_url


def test_precision_counts_only_auto_links():
    predicted = {(1, A): "link", (2, B): "link", (3, A): "review", (4, B): "no_link"}
    golden = {(1, A): True, (2, B): False, (3, A): True, (4, B): True}
    assert precision_at_auto(predicted, golden) == 0.5
    assert precision_at_auto({(9, A): "review"}, golden) is None


def _links(kind: str, n: int, score: float, group: str) -> list[AutoLink]:
    return [AutoLink(kind, 900000000 + i, uuid.UUID(int=i), score, group) for i in range(n)]


def test_sample_is_drawn_from_auto_links_and_covers_every_stratum():
    links = _links("climb", 900, 0.99, "trad") + _links("climb", 60, 0.91, "ice") + _links("area", 40, 0.95, "area")
    sample = stratified_sample(links, n=300, seed=7)
    by_stratum: dict[str, int] = {}
    for link in sample:
        by_stratum[stratum_of(link)] = by_stratum.get(stratum_of(link), 0) + 1
    assert set(by_stratum) == {"climb|>=0.97|trad", "climb|0.90-0.93|ice", "area|0.93-0.97|area"}
    assert by_stratum["climb|0.90-0.93|ice"] >= 20 and by_stratum["area|0.93-0.97|area"] >= 20
    assert len(set(sample)) == len(sample)
    assert stratified_sample(links, n=300, seed=7) == sample


def test_weighted_precision_weights_by_stratum_size_and_refuses_unjudged_strata():
    sizes = {"big": 900, "small": 100}
    assert weighted_precision({"big": (99, 100), "small": (10, 20)}, sizes) == pytest.approx(0.9 * 0.99 + 0.1 * 0.5)
    assert weighted_precision({"big": (100, 100)}, sizes) is None
    assert weighted_precision({}, {}) is None


def test_score_bands():
    assert [score_band(x) for x in (0.90, 0.929, 0.93, 0.969, 0.97, 1.0)] == [
        "0.90-0.93", "0.90-0.93", "0.93-0.97", "0.93-0.97", ">=0.97", ">=0.97"]


SEED = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), (900000101, 'Fixture Crag', 900000100, 40.0, -105.3);
INSERT INTO mp_routes (mp_route_id, name, location_id, grade, type) VALUES
  (900000001, 'Fixture Route', 900000101, '5.9', 'Trad'),
  (900000002, 'Unmatched Line', 900000101, '5.12a', 'Sport');
INSERT INTO canonical_areas (area_id, name, path, lat, lon, coord_precision, source, redistributable) VALUES
  ('00000000-0000-0000-0000-000000000009', 'Colorado', '00000000000000000000000000000009', NULL, NULL, 'none', 'openbeta', true),
  ('00000000-0000-0000-0000-00000000000a', 'Fixture Crag', '00000000000000000000000000000009.0000000000000000000000000000000a', 40.0, -105.3, 'area_centroid', 'openbeta', true);
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


@requires_pg
def test_owner_no_link_is_never_overridden_and_mp_facts_routes_are_rematched(tmp_path):
    extra = SEED + """
INSERT INTO internal.match_decisions (kind, mp_id, ob_uuid, score, decision, decided_by) VALUES
  ('climb', 900000001, '00000000-0000-0000-0000-0000000000b1', 1.0, 'no_link', 'owner');
INSERT INTO mp_routes (mp_route_id, name, location_id, grade, type) VALUES (900000003, 'Fixture Ice', 900000101, 'WI4', 'Ice');
INSERT INTO canonical_routes (route_id, area_id, name, grade, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable) VALUES
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-00000000000a', 'Fixture Ice', 'WI4', '{ice}', 'ice', 'rt-v1', false, true, 'openbeta', true),
  ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-00000000000a', 'Fixture Ice', 'WI4', '{ice}', 'ice', 'rt-v1', false, true, 'mp_facts', false);
INSERT INTO internal.mp_route_links (route_id, mp_route_id, match_score, match_method) VALUES
  ('00000000-0000-0000-0000-0000000000f1', 900000003, 1.0, 'mp_facts');
"""

    async def scenario(url: str) -> list[tuple[int, str]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await run_match(conn, apply=True, review_out=tmp_path / "review.csv")
                return [(int(m), str(meth)) for m, meth in (await conn.execute(text(
                    "SELECT mp_route_id, match_method FROM internal.mp_route_links ORDER BY 1, 2"))).all()]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=extra) as name:
        links = asyncio.run(scenario(sa_url(name)))
    assert links == [(900000003, "auto"), (900000003, "mp_facts")]
```

- [ ] **Step 2: Run to verify failure** — FAIL (`ImportError: precision_at_auto`).

- [ ] **Step 3: Implement** — append to `backend/app/pipelines/match.py` (add imports `argparse, asyncio, csv, json, random`, `from collections import Counter, defaultdict`, `from collections.abc import Mapping`, `from dataclasses import dataclass`, `from pathlib import Path`, `from sqlalchemy import text`, `from sqlalchemy.ext.asyncio import AsyncConnection`, `from app.pipelines.route_types import flags_from_mp_type, map_type_group` at the top):

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
                  for a in candidates if ("area", mp_id, a) not in refused]
        verdict, best, score = decide(scores)
        counts["areas"][verdict] += 1
        if verdict == "link" and best is not None:
            area_link[mp_id] = best
        elif verdict == "review" and best is not None:
            review.append(["area", mp_id, best, round(score, 4), name, ob_areas[best][0], ""])
    area_link |= {int(m): uuid.UUID(str(a)) for m, a in (await conn.execute(text(
        "SELECT mp_location_id, area_id FROM internal.mp_area_links WHERE match_method IN ('ob_mp_id', 'owner')"))).all()}
    # mp_facts links are not fixed: a promoted MP route keeps being matched so it can yield to
    # OpenBeta once OpenBeta covers it (Task 7).
    fixed_routes = {int(m) for (m,) in (await conn.execute(text(
        "SELECT mp_route_id FROM internal.mp_route_links WHERE match_method IN ('ob_mp_id', 'owner')"))).all()}
    refused = {(str(k), int(m), uuid.UUID(str(o))) for k, m, o in (await conn.execute(text(
        "SELECT kind, mp_id, ob_uuid FROM internal.match_decisions WHERE decision = 'no_link'"))).all()}
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
        scores = [(r, climb_score(str(name), grade, mp_group, n, g, t)) for r, n, g, t in ob_routes[area_link[int(location_id)]]
                  if ("climb", int(mp_route_id), r) not in refused]
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


PRECISION_GATE = 0.98
SAMPLE_SIZES = {"climb": 300, "area": 100}


@dataclass(frozen=True)
class AutoLink:
    kind: str
    mp_id: int
    ob_uuid: uuid.UUID
    score: float
    type_group: str


def score_band(score: float) -> str:
    return ">=0.97" if score >= 0.97 else "0.93-0.97" if score >= 0.93 else "0.90-0.93"


def stratum_of(link: AutoLink) -> str:
    return f"{link.kind}|{score_band(link.score)}|{link.type_group}"


def stratified_sample(links: Sequence[AutoLink], *, n: int, seed: int, min_per_stratum: int = 20) -> list[AutoLink]:
    """Proportional allocation with a floor per stratum, so the low-score band and rare type
    groups are judged even when they are a small share of the auto-links."""
    strata: dict[str, list[AutoLink]] = defaultdict(list)
    for link in sorted(links, key=lambda x: (x.kind, x.mp_id, str(x.ob_uuid))):
        strata[stratum_of(link)].append(link)
    rng = random.Random(seed)
    out: list[AutoLink] = []
    for key in sorted(strata):
        members = strata[key]
        take = min(len(members), max(min_per_stratum, round(n * len(members) / max(len(links), 1))))
        out += rng.sample(members, take)
    return out


def weighted_precision(labels: Mapping[str, tuple[int, int]], sizes: Mapping[str, int]) -> float | None:
    """labels: stratum -> (correct, judged); sizes: stratum -> number of auto-links. None when
    any non-empty stratum has no judged pair: an unmeasured stratum never passes the gate."""
    total = sum(sizes.values())
    if not total or any(sizes[k] and labels.get(k, (0, 0))[1] == 0 for k in sizes):
        return None
    return sum(sizes[k] / total * labels[k][0] / labels[k][1] for k in sizes if sizes[k])


async def _auto_links(conn: AsyncConnection) -> list[AutoLink]:
    climbs = [AutoLink("climb", int(m), uuid.UUID(str(r)), float(sc), str(t or "unknown")) for r, m, sc, t in (
        await conn.execute(text(
            "SELECT l.route_id, l.mp_route_id, l.match_score, r.type_group FROM internal.mp_route_links l "
            "JOIN canonical_routes r USING (route_id) WHERE l.match_method = 'auto'"))).all()]
    areas = [AutoLink("area", int(m), uuid.UUID(str(a)), float(sc), "area") for a, m, sc in (await conn.execute(text(
        "SELECT area_id, mp_location_id, match_score FROM internal.mp_area_links WHERE match_method = 'auto'"))).all()]
    return climbs + areas


async def _cli(args: argparse.Namespace) -> dict[str, object]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            if args.command == "run":
                return await run_match(conn, apply=args.apply, review_out=Path(args.out))
            if args.command == "import":
                return {"imported": await import_review(conn, Path(args.review_file))}
            links = await _auto_links(conn)
            if args.command == "sample":
                picked = [x for kind, n in SAMPLE_SIZES.items()
                          for x in stratified_sample([y for y in links if y.kind == kind], n=n, seed=args.seed)]
                out = Path(args.out).expanduser()
                out.parent.mkdir(parents=True, exist_ok=True)
                with out.open("w", newline="", encoding="utf-8") as fh:
                    writer = csv.writer(fh)
                    writer.writerow(["kind", "mp_id", "ob_uuid", "stratum", "score", "label"])
                    writer.writerows([x.kind, x.mp_id, x.ob_uuid, stratum_of(x), round(x.score, 4), ""] for x in picked)
                return {"sampled": len(picked)}
            sizes: Counter[str] = Counter(stratum_of(x) for x in links)
            by_key = {(x.kind, x.mp_id, x.ob_uuid): x for x in links}
            judged: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            with Path(args.golden).expanduser().open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    label = row["label"].strip().lower()
                    link = by_key.get((row["kind"], int(row["mp_id"]), uuid.UUID(row["ob_uuid"])))
                    if label not in ("yes", "no") or link is None:
                        continue
                    judged[stratum_of(link)][0] += label == "yes"
                    judged[stratum_of(link)][1] += 1
            result: dict[str, object] = {}
            for kind in SAMPLE_SIZES:
                kind_sizes = {k: v for k, v in sizes.items() if k.startswith(f"{kind}|")}
                labels = {k: (c, n) for k, (c, n) in judged.items() if k.startswith(f"{kind}|")}
                precision = weighted_precision(labels, kind_sizes)
                result[f"{kind}_judged"] = sum(n for _, n in labels.values())
                result[f"{kind}_precision_at_auto"] = precision
                result[f"{kind}_passes"] = precision is not None and precision >= PRECISION_GATE
            return result
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m app.pipelines.match")
    parser.add_argument("command", choices=["run", "import", "sample", "eval"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out", default="../data/review/matches.csv")
    parser.add_argument("--review-file")
    parser.add_argument("--golden")
    parser.add_argument("--seed", type=int, default=42)
    result = asyncio.run(_cli(parser.parse_args()))
    print(json.dumps(result, sort_keys=True, default=str))
    raise SystemExit(0 if all(v for k, v in result.items() if k.endswith("_passes")) else 1)
```

`internal.match_decisions` holds owner decisions (and is the audit trail for them); auto links are recomputed on every run from the current catalog, which is why auto rows are deleted and re-inserted while `ob_mp_id`, `owner` and `mp_facts` rows are never touched, and an owner `no_link` removes that pair from the candidates for good. The precision gate is measured on a sample drawn **from the auto-links** (never from random pairs, which rarely contain an auto-link and so pass vacuously), stratified by kind, score band and type group, weighted back by stratum size; climb links and area links must each reach `PRECISION_GATE`, and an unjudged stratum makes the gate fail (`None`), never pass. `eval` exits 1 unless both pass.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_match.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/match.py backend/tests/test_match.py && git commit -m "feat(pipelines): matcher job with owner review round-trip and precision evaluation"`

---

### Task 7: `mp_facts` — the only MP → public catalog path

**Files:**
- Create: `backend/app/pipelines/mp_facts.py`, `backend/tests/test_mp_facts.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `FACT_ROUTE_COLUMNS = ("name", "grade", "type_group")`, `FACT_AREA_COLUMNS = ("name", "lat", "lon")`, `PROMOTABLE = frozenset({"ice", "mixed"})`, `fact_route_id(mp_route_id: int) -> uuid.UUID`, `fact_area_id(mp_location_id: int) -> uuid.UUID`, `async promote(conn, *, run_id: uuid.UUID) -> dict[str, int]` (keys `routes`, `areas`, `yielded`, `revived`), CLI `python -m app.pipelines.mp_facts`. Run after `match run --apply` (Task 6) so fresh OpenBeta links are seen.

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
    assert counts == {"routes": 2, "areas": 1, "yielded": 0, "revived": 0, "no_state_parent": 0}
    assert rows == [
        ("Fixture Ice Line", "ice", False, "mp_facts", "Fixture Ice Crag", True),
        ("Fixture Mixed Line", "mixed", False, "mp_facts", "Fixture Ice Crag", True),
    ]
    assert fact_route_id(900000001) == fact_route_id(900000001)


@requires_pg
def test_unlinked_mp_areas_hang_under_their_state_or_are_quarantined():
    seed = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000200, 'Colorado', NULL, NULL, NULL), (900000201, 'Fixture Gully', 900000200, 39.5, -105.8),
  (900000300, 'Fixture Nowhere', NULL, NULL, NULL), (900000301, 'Fixture Couloir', 900000300, 40.5, -106.0);
INSERT INTO mp_routes (mp_route_id, name, location_id, grade, type) VALUES
  (900000011, 'Fixture Gully Ice', 900000201, 'WI3', 'Ice'),
  (900000012, 'Fixture Couloir Ice', 900000301, 'WI3', 'Ice');
INSERT INTO canonical_areas (area_id, name, path, lat, lon, coord_precision, source, redistributable) VALUES
  ('00000000-0000-0000-0000-000000000c0a', 'Colorado', '00000000000000000000000000000c0a', NULL, NULL, 'none', 'openbeta', true);
"""

    async def scenario(url: str) -> tuple[dict[str, int], list[int], list[tuple[object, ...]]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                unparented: list[int] = []
                counts = await promote(conn, run_id=uuid.uuid4(), unparented=unparented)
                rows = [tuple(r) for r in (await conn.execute(text(
                    "SELECT a.name, p.name, nlevel(a.path) FROM canonical_areas a JOIN canonical_areas p ON p.area_id = a.parent_id "
                    "WHERE a.source = 'mp_facts'"))).all()]
                roots = (await conn.execute(text(
                    "SELECT count(*) FROM canonical_areas WHERE source = 'mp_facts' AND parent_id IS NULL"))).scalar_one()
            assert roots == 0
            return counts, unparented, rows
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        counts, unparented, rows = asyncio.run(scenario(sa_url(name)))
    assert rows == [("Fixture Gully", "Colorado", 2)]
    assert unparented == [900000012]
    assert counts["routes"] == 1 and counts["no_state_parent"] == 1


@requires_pg
def test_mp_facts_route_yields_once_openbeta_covers_it():
    fact = fact_route_id(900000001)
    seed = SEED + f"""
INSERT INTO canonical_routes (route_id, area_id, name, grade, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable) VALUES
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-00000000000a', 'OB Fixture Ice Line', 'WI4', '{{ice}}', 'ice', 'rt-v1', false, true, 'openbeta', true);
INSERT INTO accidents (accident_id) VALUES (900000501);
"""

    async def scenario(url: str) -> tuple[dict[str, int], dict[str, int], list[tuple[object, ...]], object]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await promote(conn, run_id=uuid.uuid4())
                await conn.execute(text(
                    "INSERT INTO accident_route_links (accident_id, canonical_route_id, method, score) VALUES (900000501, :r, 'legacy_match', 1.0)"),
                    {"r": fact})
                await conn.execute(text(
                    "INSERT INTO internal.mp_route_links (route_id, mp_route_id, match_score, match_method) "
                    "VALUES ('00000000-0000-0000-0000-0000000000b2', 900000001, 0.97, 'auto')"))
                yielded = await promote(conn, run_id=uuid.uuid4())
                scorable = [tuple(r) for r in (await conn.execute(text(
                    "SELECT name, source FROM scorable_routes WHERE type_group = 'ice' AND name LIKE '%Fixture Ice Line' ORDER BY 1"))).all()]
                link = (await conn.execute(text(
                    "SELECT canonical_route_id FROM accident_route_links WHERE accident_id = 900000501"))).scalar_one()
                await conn.execute(text("UPDATE canonical_routes SET retired_at = now() WHERE route_id = '00000000-0000-0000-0000-0000000000b2'"))
                revived = await promote(conn, run_id=uuid.uuid4())
            return yielded, revived, scorable, link
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        yielded, revived, scorable, link = asyncio.run(scenario(sa_url(name)))
    assert yielded["yielded"] == 1
    assert scorable == [("OB Fixture Ice Line", "openbeta")]
    assert str(link) == "00000000-0000-0000-0000-0000000000b2"
    assert revived["revived"] == 1
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
from app.pipelines.ingest_log import finish_run, start_run, write_quarantine
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


# A promoted MP route and the OpenBeta route it was later matched to (best link wins).
_COVERED = """
SELECT DISTINCT ON (f.route_id) f.route_id AS fact_route, o.route_id AS ob_route
FROM internal.mp_route_links f
JOIN canonical_routes fr ON fr.route_id = f.route_id AND fr.source = 'mp_facts' AND fr.retired_at IS NULL
JOIN internal.mp_route_links o ON o.mp_route_id = f.mp_route_id AND o.match_method <> 'mp_facts'
JOIN canonical_routes ob ON ob.route_id = o.route_id AND ob.source = 'openbeta' AND ob.retired_at IS NULL
WHERE f.match_method = 'mp_facts'
ORDER BY f.route_id, o.match_score DESC, o.route_id
"""
REPOINT_SQL = f"""
WITH covered AS ({_COVERED})
UPDATE accident_route_links a SET canonical_route_id = c.ob_route
FROM covered c WHERE a.canonical_route_id = c.fact_route
"""
YIELD_SQL = f"""
WITH covered AS ({_COVERED})
UPDATE canonical_routes r SET retired_at = now(), updated_at = now(), run_id = :run
FROM covered c WHERE r.route_id = c.fact_route
"""
REVIVE_SQL = """
UPDATE canonical_routes r SET retired_at = NULL, updated_at = now(), run_id = :run
FROM internal.mp_route_links f
WHERE f.route_id = r.route_id AND f.match_method = 'mp_facts' AND r.source = 'mp_facts' AND r.retired_at IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM internal.mp_route_links o JOIN canonical_routes ob ON ob.route_id = o.route_id
    WHERE o.mp_route_id = f.mp_route_id AND o.match_method <> 'mp_facts' AND ob.retired_at IS NULL)
"""


async def promote(
    conn: AsyncConnection, *, run_id: uuid.UUID, unparented: list[int] | None = None
) -> dict[str, int]:
    # Accident links move first so no accident is left pointing at a retired route.
    await conn.execute(text(REPOINT_SQL))
    yielded = (await conn.execute(text(YIELD_SQL), {"run": run_id})).rowcount
    revived = (await conn.execute(text(REVIVE_SQL), {"run": run_id})).rowcount
    locations = {int(i): (str(n), p, la, lo) for i, n, p, la, lo in (await conn.execute(text(
        "SELECT mp_id, name, parent_id, latitude, longitude FROM mp_locations"))).all()}
    area_links = {int(m): uuid.UUID(str(a)) for m, a in (await conn.execute(text(
        "SELECT mp_location_id, area_id FROM internal.mp_area_links"))).all()}
    paths = {uuid.UUID(str(a)): str(p) for a, p in (await conn.execute(text(
        "SELECT area_id, path::text FROM canonical_areas"))).all()}
    linked_routes = {int(m) for (m,) in (await conn.execute(text(
        "SELECT l.mp_route_id FROM internal.mp_route_links l JOIN canonical_routes r USING (route_id) "
        "WHERE r.retired_at IS NULL OR l.match_method = 'mp_facts'"))).all()}
    states = {str(n).strip().lower(): uuid.UUID(str(a)) for a, n in (await conn.execute(text(
        "SELECT area_id, name FROM canonical_areas WHERE parent_id IS NULL AND source = 'openbeta' "
        "AND retired_at IS NULL"))).all()}
    no_state_parent: list[int] = []
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
            cursor, parent_area, root_name = parent, None, loc_name
            while cursor is not None and int(cursor) in locations:
                if int(cursor) in area_links:
                    parent_area = area_links[int(cursor)]
                    break
                root_name = locations[int(cursor)][0]
                cursor = locations[int(cursor)][1]
            if parent_area is None:
                # MP's top-level locations are US states; parent under the OpenBeta state area of the
                # same name so the route keeps state/region pooling (P3). Never a root path.
                parent_area = states.get(root_name.strip().lower())
            if parent_area is None or parent_area not in paths:
                no_state_parent.append(int(mp_route_id))
                continue
            area_id = fact_area_id(loc_id)
            path = f"{paths[parent_area]}.{ltree_label(area_id)}"
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
    if unparented is not None:
        unparented.extend(no_state_parent)
    return {"routes": routes, "areas": created_areas, "yielded": yielded, "revived": revived,
            "no_state_parent": len(no_state_parent)}


async def _main() -> dict[str, object]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source="mp_facts", window_start=None, window_end=None, content_sha256=None)
            unparented: list[int] = []
            counts = await promote(conn, run_id=run_id, unparented=unparented)
            report = ValidationReport("mp_facts")
            for _ in range(counts["routes"]):
                report.accept()
            for mp_route_id in unparented:
                report.quarantine(str(mp_route_id), "no_state_parent")
            await write_quarantine(conn, run_id, report)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=counts["routes"] + counts["areas"])
    finally:
        await engine.dispose()
    return dict(counts)


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_main()), sort_keys=True))
```

The static test in Step 1 scans every `SELECT … FROM mp_` in this file and fails if any MP column outside the fact whitelist (plus ids and keys) is read, which is the spec's "writes only the fact columns" guard in executable form; the DB tests prove rock and already-matched routes are never promoted, and that a promoted route yields to OpenBeta (retired, accident links moved) once Task 6 links its MP id to a live OpenBeta climb, and is revived if that OpenBeta climb is later retired. An `mp_facts` area with no area-linked MP ancestor is parented under the OpenBeta state area whose name matches its top MP location (MP's top level is the US state), so it keeps state/region pooling (P3:57); when no state matches, the route is not promoted and is quarantined `no_state_parent` (a Task 10 `-m db` cell fails until the owner resolves it). No `mp_facts` area is ever a root path. Accident links that R10/plan 6 attach to an OpenBeta route that is later retired are plan 5/6's concern (their re-run picks the live route); this task only moves links off the `mp_facts` rows it retires.

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


# Every URL and "real" id below is assembled at run time from the guard's own host parts and
# a fake id just under the synthetic floor: a literal would trip check_no_scrapers.py (host
# in a .py file) and check_no_mp_data.py (this plan lives under docs/).
HOST = guard.HOST
BELOW_FLOOR = guard.SYNTHETIC_FLOOR - 1
FAKE = guard.SYNTHETIC_FLOOR + 1


def run(files: dict[str, str]) -> list[str]:
    return guard.violations(files, files.__getitem__)


def test_route_and_area_urls_are_flagged_but_domain_mentions_are_not():
    assert run({"docs/a.md": f"see https://www.{HOST}/route/{FAKE}/fixture-route"})
    assert run({"data/b.json": f'"url": "{HOST}/area/{FAKE}"'})
    assert run({"docs/c.md": f"no {HOST} URLs in fixtures"}) == []


def test_real_mp_ids_flagged_synthetic_range_allowed():
    assert run({"backend/tests/fixtures/x.json": f'{{"mp_id": {BELOW_FLOOR}}}'})
    assert run({"backend/tests/fixtures/x.json": f'{{"mp_id": "{BELOW_FLOOR}"}}'})
    assert run({"data/golden/y.csv": f"mp_route_id,label\n{BELOW_FLOOR},yes\n"})
    assert run({"backend/tests/fixtures/x.json": f'{{"mp_id": {FAKE}}}'}) == []


def test_out_of_scope_paths_are_ignored():
    assert run({"backend/app/x.py": f"mp_route_id = {BELOW_FLOOR}"}) == []


def test_guard_source_never_spells_the_host():
    source = (Path(__file__).resolve().parents[2] / "scripts" / "check_no_mp_data.py").read_text()
    assert HOST not in source
```

`backend/tests/test_no_mp_prose_in_schemas.py`:

```python
import asyncio
import inspect
import re
from pathlib import Path

import asyncpg
from pydantic import BaseModel

import app.api.v1 as api_pkg
from app.schemas import mp_location, mp_route, prediction
from tests.pgtest import migrated_db, pg_url, requires_pg

FORBIDDEN = {"description", "beta", "protection", "comment", "comments", "photos", "photo", "stars", "user",
             "username", "climber_name", "location_description", "notes", "text"}
MP_AND_CATALOG_TABLES = ("mp_routes", "mp_locations", "mp_tick_counts", "canonical_routes", "canonical_areas",
                         "scorable_routes")


def _names(field_name: str, info: object) -> set[str]:
    names = {field_name}
    for attr in ("alias", "validation_alias", "serialization_alias"):
        value = getattr(info, attr, None)
        if isinstance(value, str):
            names.add(value)
    return {n.lower() for n in names}


def test_no_api_schema_exposes_an_mp_prose_field():
    offenders = []
    for module in (mp_location, mp_route, prediction):
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, BaseModel) and cls.__module__ == module.__name__:
                offenders += [f"{name}.{f}" for f, info in cls.model_fields.items() if _names(f, info) & FORBIDDEN]
    assert offenders == []


def test_api_sql_never_selects_prose_from_mp_or_catalog_tables():
    select = re.compile(r"SELECT\s(.*?)\sFROM\s+(\w+)", re.IGNORECASE | re.DOTALL)
    offenders = []
    for path in Path(api_pkg.__file__).parent.glob("*.py"):
        for columns, table in select.findall(path.read_text()):
            if table.lower() in MP_AND_CATALOG_TABLES:
                words = {w.lower() for w in re.findall(r"[A-Za-z_]+", columns)}
                offenders += [f"{path.name}: {table}.{w}" for w in sorted(words & FORBIDDEN)]
    assert offenders == []


@requires_pg
def test_mp_and_catalog_tables_have_no_prose_columns():
    async def columns(url: str) -> list[tuple[str, str]]:
        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ANY($1::text[])", list(MP_AND_CATALOG_TABLES))
            return [(r["table_name"], r["column_name"]) for r in rows]
        finally:
            await conn.close()

    with migrated_db() as name:
        found = asyncio.run(columns(pg_url(name)))
    assert {t for t, _ in found} == set(MP_AND_CATALOG_TABLES)
    assert [f"{t}.{c}" for t, c in found if c.lower() in FORBIDDEN] == []
```

(The `accident` schemas are out of scope: accident `description` is accident narrative, not MP content; it is covered by R11's facts-only rule. The DB check pins today's tables; a later migration that adds a prose-named column to any of them fails here.)

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
# Assembled from parts so this repo file never spells the host (check_no_scrapers.py bans it
# in source files, and the guard must not need an exemption).
HOST = "mountain" + "project" + ".com"
URL = re.compile(re.escape(HOST) + r"/(?:route|area|v)/\d+", re.IGNORECASE)
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

Docs: `CHANGELOG.md` "Phase 2b catalog (PR 2b-1a)": catalog tables (0008, `ltree` owner script), type mapper, OpenBeta weekly load + workflow (per-state drop guard, D17 schema trigger), matcher with review and stratified precision gate, `mp_facts` (yields to OpenBeta), MP-data guard, `h3` in the `pipelines` group, `HEALTHCHECKS_PING_KEY`. `CLAUDE.md` Data rules: add "`scripts/check_no_mp_data.py` (CI `guards`) fails on MP route/area URLs or real MP ids in `data/`, `docs/`, `backend/tests/fixtures/`." (the `uv sync --group pipelines` command line was added by plan 3). `DEPLOYMENT.md`: new "Data workflows" section: `data-openbeta.yml` (weekly), secrets `INGEST_DATABASE_URL`, `HEALTHCHECKS_PING_KEY`, healthchecks check `openbeta-weekly` (period 7 d, grace 1 d), failure opens an issue. `data/DATABASE_STRUCTURE.md`: catalog tables, internal link tables, `scorable_routes`.

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
- Produces: `BUILD_SQL` (rebuild from clean ticks dated on or before `today`), `async build(conn, *, run_id: uuid.UUID, today: date) -> int`, CLI `python -m app.pipelines.mp_tick_counts` (injects the current UTC day at run time); `get_ascent_analytics` response shape unchanged.

- [ ] **Step 1: Failing tests** — `backend/tests/test_mp_tick_counts.py`:

```python
import asyncio
import uuid
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.mp_tick_counts import build
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)
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
                await build(conn, run_id=uuid.uuid4(), today=TODAY)
                await build(conn, run_id=uuid.uuid4(), today=TODAY)
                return [tuple(r) for r in (await conn.execute(text(
                    "SELECT mp_route_id, period, tick_count FROM mp_tick_counts ORDER BY 1, 2"))).all()]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        assert asyncio.run(scenario(sa_url(name))) == [(900000001, "2025-01", 2), (900000001, "undated", 1)]


@requires_pg
def test_ticks_after_today_in_current_month_are_not_counted():
    seed = """
INSERT INTO mp_ticks (tick_id, route_id, climber_name, tick_date, quarantine_reason) VALUES
  (1, '900000002', 'c', '2025-01-04', NULL), (2, '900000002', 'c', '2025-01-11', NULL);
"""

    async def scenario(url: str) -> list[tuple[int, str, int]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await build(conn, run_id=uuid.uuid4(), today=date(2025, 1, 10))
                return [tuple(r) for r in (await conn.execute(text(
                    "SELECT mp_route_id, period, tick_count FROM mp_tick_counts ORDER BY 1, 2"))).all()]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        assert asyncio.run(scenario(sa_url(name))) == [(900000002, "2025-01", 1)]
```

The `quarantine_reason` filter already drops ticks R8 flagged as `future`; the `today` cut-off covers ticks that became "future" only relative to this run (R8 ran earlier, or the current month is still open), so a partial current month never counts days that have not happened yet.

In `backend/tests/test_ascent_analytics.py`: first raise the fixture ids into the synthetic range (the existing `ROUTE = 111`, `OTHER_ROUTE = 222`, `THIRD_ROUTE = 333`, `FOURTH_ROUTE = 444` and the literal location id `10` in `SEED_SQL` sit below 900000000, which the repo rule forbids): `ROUTE = 900000111`, `OTHER_ROUTE = 900000222`, `THIRD_ROUTE = 900000333`, `FOURTH_ROUTE = 900000444`, and add `LOCATION = 900000010` used in place of every literal `10` that is an `mp_locations.mp_id`/`location_id` in the file (the legacy `routes` insert keeps using `{ROUTE}`, so the collision the legacy-bug tests prove is preserved). Then replace the `INSERT INTO mp_ticks …` block in `SEED_SQL` with

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
from datetime import UTC, date, datetime

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
  AND (tick_date IS NULL OR tick_date <= :today)
GROUP BY 1, 2
"""


async def build(conn: AsyncConnection, *, run_id: uuid.UUID, today: date) -> int:
    await conn.execute(text("DELETE FROM mp_tick_counts"))
    return (await conn.execute(text(BUILD_SQL), {"run_id": run_id, "today": today})).rowcount


async def _main() -> dict[str, int]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source="mp_tick_counts", window_start=None, window_end=None, content_sha256=None)
            n = await build(conn, run_id=run_id, today=datetime.now(UTC).date())
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
- Create: `backend/alembic/versions/0009_mp_ticks_internal.py`, `backend/tests/test_migration_0009.py`, `backend/app/pipelines/mp_tables.py`, `backend/tests/test_mp_tables.py`, `backend/tests/verify/test_phase2b_catalog.py`
- Modify: `backend/app/pipelines/mp_ticks_quarantine.py`, `backend/app/pipelines/mp_tick_counts.py`, `backend/tests/test_mp_ticks_quarantine.py`, `backend/tests/test_mp_tick_counts.py`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`, `CHANGELOG.md`, `data/DATABASE_STRUCTURE.md`

**Interfaces:**
- Produces: `internal.mp_ticks` (same columns); `app` holds nothing on it; `analyst` keeps SELECT; `ingest` keeps SELECT + column UPDATE. `-m db` cells proving it on the branch and prod (D2).

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


def test_0009_downgrade_puts_the_table_back_with_its_rows():
    seed = "INSERT INTO mp_ticks (tick_id, route_id, climber_name) VALUES (1, '900000001', 'c');"
    with migrated_db("0008_catalog", seed) as name:
        from alembic import command

        from tests.test_migrations import _alembic_cfg

        cfg = _alembic_cfg(name)
        command.upgrade(cfg, "0009_mp_ticks_internal")
        command.downgrade(cfg, "0008_catalog")

        async def check() -> list[object]:
            conn = await asyncpg.connect(pg_url(name))
            try:
                return [await conn.fetchval("SELECT to_regclass('internal.mp_ticks')"),
                        await conn.fetchval("SELECT count(*) FROM public.mp_ticks")]
            finally:
                await conn.close()

        assert asyncio.run(check()) == [None, 1]
```

`backend/tests/verify/test_phase2b_catalog.py` (D2 acceptance, run as `analyst` against the branch, then prod):

```python
"""Plan 4 acceptance cells: VERIFY_DATABASE_URL=<analyst url> uv run pytest -m db tests/verify.
Connections go through tests.verify._db.fetch (verify-full, like the app)."""

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db


def _rows(sql: str) -> list[tuple[object, ...]]:
    return [tuple(r) for r in fetch(sql)]


def test_app_cannot_read_raw_ticks_or_internal():
    [(ticks, schema)] = _rows(
        "SELECT has_table_privilege('app', 'internal.mp_ticks', 'SELECT'), has_schema_privilege('app', 'internal', 'USAGE')")
    assert (ticks, schema) == (False, False)


def test_public_raw_ticks_are_gone():
    [(regclass,)] = _rows("SELECT to_regclass('public.mp_ticks')")
    assert regclass is None


def test_scorable_routes_hold_no_unknown_boulder_or_retired_route():
    [(n,)] = _rows(
        "SELECT count(*) FROM scorable_routes s JOIN canonical_routes r USING (route_id) "
        "WHERE r.type_group = 'unknown' OR r.is_boulder OR r.retired_at IS NOT NULL OR NOT r.scored")
    assert n == 0


def test_no_mp_route_counts_twice_in_scorable_routes():
    [(n,)] = _rows(
        "SELECT count(*) FROM (SELECT l.mp_route_id FROM internal.mp_route_links l JOIN scorable_routes s USING (route_id) "
        "GROUP BY l.mp_route_id HAVING count(DISTINCT s.route_id) > 1) d")
    assert n == 0


def test_mp_facts_areas_are_never_roots():
    [(n,)] = _rows("SELECT count(*) FROM canonical_areas WHERE source = 'mp_facts' AND (parent_id IS NULL OR nlevel(path) < 2)")
    assert n == 0


def test_last_mp_facts_run_left_no_route_without_a_state_parent():
    rows = _rows(
        "SELECT coalesce((validation_report -> 'quarantined' ->> 'no_state_parent')::int, 0) FROM source_ingest_log "
        "WHERE source = 'mp_facts' AND status = 'ok' ORDER BY finished_at DESC LIMIT 1")
    assert rows and rows[0][0] == 0


def test_no_coordinates_at_null_island():
    [(n,)] = _rows("SELECT count(*) FROM canonical_areas WHERE lat = 0 AND lon = 0")
    assert n == 0
```

`test_no_mp_route_counts_twice_in_scorable_routes` reads `internal.mp_route_links`, which `analyst` can SELECT (plan 1's `grants_phase2.sql` grants `analyst` USAGE on `internal` and SELECT on all its tables; re-running it after `0009` covers the moved table); `app` still cannot.

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
    # Pre-0009 API code reads public.mp_ticks directly, and default privileges do not re-apply
    # to a table moved back into a schema, so the downgrade restores the grant it revoked.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app') THEN "
        "GRANT SELECT ON public.mp_ticks TO app; END IF; END $$"
    )
```

The grant revoke (and the downgrade's re-grant) is conditional because local/CI databases have no `app` role; prod does. Downgrading is only for a failed rollout: it also needs the pre-PR-2b-1b API deployed, because the switched API never reads `mp_ticks`.

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

`CHANGELOG.md` "MP split (PR 2b-1b)": `mp_tick_counts` builder (never counts days after the run day), Ascents tab reads counts, raw ticks move to `internal` (0009), `app` loses all access to raw ticks, one-time OpenBeta tick-coverage sample (D17, Task 11). `data/DATABASE_STRUCTURE.md`: move `mp_ticks` under "Schema internal"; add `mp_tick_counts`.

- [ ] **Step 4: Run** — `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/` → green (the role test now checks `internal.mp_ticks`).
- [ ] **Step 5: Commit** — `git add backend/alembic/versions/0009_mp_ticks_internal.py backend/app/pipelines/ backend/tests/ backend/db/roles/ CHANGELOG.md data/DATABASE_STRUCTURE.md && git commit -m "feat(db): 0009 raw MP ticks move to internal; app keeps only aggregate counts"`

---

### Task 11: One-time OpenBeta tick-coverage sample (D17) and its revisit triggers

D17 keeps `ob_ticks` missing in exposure v1 because OpenBeta has no bulk tick query and looked sparse on one area. That was one area; this task measures coverage once, on a stratified national sample, so the decision rests on a number, and states exactly what reopens it (P2-9, "rock popularity from OpenBeta ticks").

**Files:**
- Create: `backend/app/pipelines/ob_tick_sample.py`, `backend/tests/test_ob_tick_sample.py`
- Modify: `backend/pyproject.toml`, `data/DATABASE_STRUCTURE.md` (one line under "Data jobs")

**Interfaces:**
- Consumes: `openbeta.OpenBetaClient.climb_tick_count` (Task 3), `canonical_routes`/`canonical_areas` (Task 4), `internal.mp_route_links` (Tasks 4, 6), `mp_tick_counts` (Task 9), `ingest_log.start_run`/`finish_run`.
- Produces: `SOURCE = "ob_tick_sample"`, `REGIONS: dict[str, str]` (state name → `northeast|southeast|midwest|mountain_southwest|pacific`), `TRIGGER_SHARE_WITH_TICKS = 0.5`, `TRIGGER_MEDIAN_RATIO = 0.10`, `@dataclass(frozen=True) SampleClimb(route_id: uuid.UUID, state: str, type_group: str, mp_ticks: int | None)`, `region_of(state: str) -> str`, `popularity_band(mp_ticks: int | None) -> str`, `stratum(c: SampleClimb) -> str`, `allocate(sizes: Mapping[str, int], n: int, floor: int = 3) -> dict[str, int]`, `draw(climbs: Sequence[SampleClimb], *, n: int, seed: int) -> list[SampleClimb]`, `summarize(measured: Sequence[tuple[SampleClimb, int]], errors: int) -> dict[str, object]`, `async load_population(conn) -> list[SampleClimb]`, `async run(conn, client, *, n: int, seed: int, out: Path, pause_s: float = 0.5) -> dict[str, object]`, CLI `python -m app.pipelines.ob_tick_sample [--n 1500] [--seed 42] --out PATH`.

Revisit triggers for P2-9 (either one reopens D17; the owner then decides whether exposure v2 gets a real `ob_ticks` component):
1. **Schema trigger (automatic, weekly):** OpenBeta's `Query` gains a tick field beyond `userTicks`/`userTicksByClimbId` — Task 4's weekly load reports it as `revisit_d17` and the workflow opens one issue.
2. **Coverage trigger (this sample):** at least `TRIGGER_SHARE_WITH_TICKS` (50%) of sampled climbs have ≥1 OpenBeta tick **and** the median OpenBeta/MP tick ratio on MP-linked climbs with MP ticks is at least `TRIGGER_MEDIAN_RATIO` (0.10). The result carries `revisit_p2_9: true|false`.
3. **Re-measure:** the owner re-runs this sample each January (beside the Open-Meteo Pro window, foundations cadence table) or when OpenBeta announces tick changes; a sample is cheap (~1,500 GraphQL calls).

- [ ] **Step 1: Failing tests** — `backend/tests/test_ob_tick_sample.py`:

```python
import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.ob_tick_sample import (
    REGIONS,
    SampleClimb,
    allocate,
    draw,
    load_population,
    popularity_band,
    region_of,
    run,
    stratum,
    summarize,
)
from app.pipelines.openbeta import OpenBetaClient
from tests.pgtest import migrated_db, requires_pg, sa_url


def climb(i: int, state: str = "Colorado", group: str = "trad", mp: int | None = None) -> SampleClimb:
    return SampleClimb(uuid.UUID(int=i), state, group, mp)


def test_every_state_and_dc_has_a_region():
    assert len(REGIONS) == 51
    assert region_of("Alaska") == region_of("Hawaii") == "pacific"
    assert region_of("Atlantis") == "unknown_region"


def test_popularity_bands():
    assert [popularity_band(x) for x in (None, 0, 1, 9, 10, 99, 100)] == [
        "unlinked", "0", "1-9", "1-9", "10-99", "10-99", "100+"]


def test_allocation_is_proportional_with_a_floor_and_never_exceeds_the_stratum():
    assert allocate({"big": 900, "small": 10, "tiny": 2}, 100) == {"big": 99, "small": 3, "tiny": 2}


def test_draw_is_deterministic_and_covers_every_stratum():
    climbs = [climb(i) for i in range(500)] + [climb(1000 + i, "Utah", "sport", 50) for i in range(20)]
    sample = draw(climbs, n=100, seed=1)
    assert sample == draw(climbs, n=100, seed=1)
    assert {stratum(c) for c in sample} == {stratum(climbs[0]), stratum(climbs[-1])}
    assert len(set(sample)) == len(sample)


def test_summary_fires_the_coverage_trigger_only_when_both_bars_are_met():
    sparse = [(climb(i, mp=20), 0) for i in range(8)] + [(climb(10 + i, mp=20), 5) for i in range(2)]
    dense = [(climb(i, mp=20), 4) for i in range(10)]
    assert summarize(sparse, errors=0)["revisit_p2_9"] is False
    result = summarize(dense, errors=0)
    assert result["revisit_p2_9"] is True
    assert result["share_with_ticks"] == 1.0 and result["median_ob_to_mp_ratio"] == pytest.approx(0.2)


def test_unlinked_climbs_do_not_enter_the_ratio_and_errors_are_reported():
    result = summarize([(climb(1), 3), (climb(2, mp=0), 1)], errors=2)
    assert result["median_ob_to_mp_ratio"] is None
    assert result["errors"] == 2 and result["measured"] == 2


SEED = """
INSERT INTO canonical_areas (area_id, name, path, lat, lon, coord_precision, source, redistributable) VALUES
  ('00000000-0000-0000-0000-00000000000a', 'Colorado', '0000000000000000000000000000000a', NULL, NULL, 'none', 'openbeta', true);
INSERT INTO canonical_areas (area_id, name, parent_id, path, lat, lon, coord_precision, source, redistributable) VALUES
  ('00000000-0000-0000-0000-00000000000b', 'Fixture Crag', '00000000-0000-0000-0000-00000000000a',
   '0000000000000000000000000000000a.0000000000000000000000000000000b', 40.0, -105.3, 'area_centroid', 'openbeta', true);
INSERT INTO canonical_routes (route_id, area_id, name, grade, disciplines, type_group, type_rule_version, is_boulder, scored, source, redistributable) VALUES
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-00000000000b', 'Linked', '5.9', '{trad}', 'trad', 'rt-v1', false, true, 'openbeta', true),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-00000000000b', 'Unlinked', '5.10a', '{sport}', 'sport', 'rt-v1', false, true, 'openbeta', true);
INSERT INTO internal.mp_route_links (route_id, mp_route_id, match_score, match_method) VALUES
  ('00000000-0000-0000-0000-0000000000b1', 900000001, 1.0, 'ob_mp_id');
INSERT INTO mp_tick_counts (mp_route_id, period, tick_count) VALUES (900000001, '2025-01', 7), (900000001, 'undated', 3);
"""


@requires_pg
def test_run_measures_the_sample_and_logs_the_summary(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        climb_id = json.loads(request.read())["variables"]["climbId"]
        ticks = [{"dateClimbed": "2025-01-01"}] * (2 if climb_id.endswith("b1") else 0)
        return httpx.Response(200, json={"data": {"userTicksByClimbId": ticks}})

    async def scenario(url: str) -> tuple[list[SampleClimb], dict[str, object]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                population = await load_population(conn)
                result = await run(conn, OpenBetaClient(transport=httpx.MockTransport(handler)), n=10, seed=1,
                                   out=tmp_path / "coverage.csv", pause_s=0.0)
            return population, result
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        population, result = asyncio.run(scenario(sa_url(name)))
    assert sorted((c.state, c.type_group, c.mp_ticks) for c in population) == [("Colorado", "sport", None), ("Colorado", "trad", 10)]
    assert result["status"] == "ok" and result["measured"] == 2 and result["share_with_ticks"] == 0.5
    assert result["median_ob_to_mp_ratio"] == pytest.approx(0.2)
    assert (tmp_path / "coverage.csv").read_text().splitlines()[0] == "route_id,state,type_group,stratum,mp_ticks,ob_ticks"
```

- [ ] **Step 2: Run to verify failure** — FAIL (`ModuleNotFoundError: app.pipelines.ob_tick_sample`).

- [ ] **Step 3: Implement** `backend/app/pipelines/ob_tick_sample.py`:

```python
"""D17: one-time, stratified measurement of OpenBeta tick coverage (region x type group x MP
popularity band). Output: a coverage summary in source_ingest_log and a per-climb CSV in the
private repo (it carries MP-derived tick counts, so it is never committed)."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import statistics
import time
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.openbeta import OpenBetaClient, OpenBetaError
from app.pipelines.validate import ValidationReport

SOURCE = "ob_tick_sample"
TRIGGER_SHARE_WITH_TICKS = 0.5
TRIGGER_MEDIAN_RATIO = 0.10
_REGION_STATES = {
    "northeast": ("Connecticut", "Maine", "Massachusetts", "New Hampshire", "New Jersey", "New York", "Pennsylvania",
                  "Rhode Island", "Vermont", "Delaware", "Maryland", "District of Columbia"),
    "southeast": ("Alabama", "Arkansas", "Florida", "Georgia", "Kentucky", "Louisiana", "Mississippi", "North Carolina",
                  "South Carolina", "Tennessee", "Virginia", "West Virginia"),
    "midwest": ("Illinois", "Indiana", "Iowa", "Kansas", "Michigan", "Minnesota", "Missouri", "Nebraska", "North Dakota",
                "Ohio", "South Dakota", "Wisconsin", "Oklahoma"),
    "mountain_southwest": ("Arizona", "Colorado", "Idaho", "Montana", "Nevada", "New Mexico", "Texas", "Utah", "Wyoming"),
    "pacific": ("Alaska", "California", "Hawaii", "Oregon", "Washington"),
}
REGIONS: dict[str, str] = {state: region for region, states in _REGION_STATES.items() for state in states}

POPULATION_SQL = """
SELECT r.route_id, st.name AS state, r.type_group,
       CASE WHEN EXISTS (SELECT 1 FROM internal.mp_route_links l WHERE l.route_id = r.route_id)
            THEN (SELECT coalesce(sum(c.tick_count), 0) FROM internal.mp_route_links l
                  JOIN mp_tick_counts c ON c.mp_route_id = l.mp_route_id WHERE l.route_id = r.route_id)
       END AS mp_ticks
FROM canonical_routes r
JOIN canonical_areas a ON a.area_id = r.area_id
JOIN canonical_areas st ON st.path @> a.path AND st.parent_id IS NULL AND st.source = 'openbeta'
WHERE r.source = 'openbeta' AND r.retired_at IS NULL AND NOT r.is_boulder
"""


@dataclass(frozen=True)
class SampleClimb:
    route_id: uuid.UUID
    state: str
    type_group: str
    mp_ticks: int | None


def region_of(state: str) -> str:
    return REGIONS.get(state, "unknown_region")


def popularity_band(mp_ticks: int | None) -> str:
    if mp_ticks is None:
        return "unlinked"
    if mp_ticks == 0:
        return "0"
    return "1-9" if mp_ticks < 10 else "10-99" if mp_ticks < 100 else "100+"


def stratum(c: SampleClimb) -> str:
    return f"{region_of(c.state)}|{c.type_group}|{popularity_band(c.mp_ticks)}"


def allocate(sizes: Mapping[str, int], n: int, floor: int = 3) -> dict[str, int]:
    total = sum(sizes.values())
    return {k: min(v, max(floor, round(n * v / total))) for k, v in sorted(sizes.items())} if total else {}


def draw(climbs: Sequence[SampleClimb], *, n: int, seed: int) -> list[SampleClimb]:
    strata: dict[str, list[SampleClimb]] = defaultdict(list)
    for c in sorted(climbs, key=lambda x: str(x.route_id)):
        strata[stratum(c)].append(c)
    rng = random.Random(seed)
    counts = allocate({k: len(v) for k, v in strata.items()}, n)
    return [c for key in sorted(strata) for c in rng.sample(strata[key], counts[key])]


def summarize(measured: Sequence[tuple[SampleClimb, int]], errors: int) -> dict[str, object]:
    with_ticks = [ob for _, ob in measured if ob > 0]
    ratios = [ob / c.mp_ticks for c, ob in measured if c.mp_ticks]
    share = len(with_ticks) / len(measured) if measured else None
    ratio = statistics.median(ratios) if ratios else None
    by_stratum: dict[str, list[int]] = defaultdict(list)
    for c, ob in measured:
        by_stratum[stratum(c)].append(ob)
    return {
        "measured": len(measured),
        "errors": errors,
        "share_with_ticks": share,
        "median_ob_ticks": statistics.median([ob for _, ob in measured]) if measured else None,
        "median_ob_to_mp_ratio": ratio,
        "share_with_ticks_by_stratum": {k: sum(1 for x in v if x > 0) / len(v) for k, v in sorted(by_stratum.items())},
        "revisit_p2_9": bool(
            share is not None and ratio is not None
            and share >= TRIGGER_SHARE_WITH_TICKS and ratio >= TRIGGER_MEDIAN_RATIO
        ),
    }


async def load_population(conn: AsyncConnection) -> list[SampleClimb]:
    return [
        SampleClimb(uuid.UUID(str(r)), str(st), str(tg), None if mp is None else int(mp))
        for r, st, tg, mp in (await conn.execute(text(POPULATION_SQL))).all()
    ]


async def run(
    conn: AsyncConnection, client: OpenBetaClient, *, n: int, seed: int, out: Path, pause_s: float = 0.5
) -> dict[str, object]:
    run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=None)
    report = ValidationReport(SOURCE)
    sample = draw(await load_population(conn), n=n, seed=seed)
    measured: list[tuple[SampleClimb, int]] = []
    errors = 0
    for c in sample:
        try:
            measured.append((c, client.climb_tick_count(c.route_id)))
            report.accept()
        except OpenBetaError:
            errors += 1
            report.quarantine(str(c.route_id), "tick_query_failed")
        time.sleep(pause_s)
    summary = summarize(measured, errors)
    # Mostly failed calls mean the tick query itself is unusable (for example it needs a user id):
    # that is "not measurable", never "zero ticks".
    status = "ok" if measured and errors <= len(sample) // 2 else "failed"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["route_id", "state", "type_group", "stratum", "mp_ticks", "ob_ticks"])
        writer.writerows([c.route_id, c.state, c.type_group, stratum(c), c.mp_ticks, ob] for c, ob in measured)
    # The run log has no free-form column; the coverage summary rides in the report's problems list.
    await finish_run(conn, run_id, status=status, report=report, rows_upserted=0,
                     problems=[f"coverage {json.dumps(summary, sort_keys=True)}"])
    return {"status": status if status == "ok" else "not_measurable", **summary}


async def _main(args: argparse.Namespace) -> dict[str, object]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            return await run(conn, OpenBetaClient(), n=args.n, seed=args.seed, out=Path(args.out).expanduser())
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m app.pipelines.ob_tick_sample")
    parser.add_argument("--n", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", required=True)
    print(json.dumps(asyncio.run(_main(parser.parse_args())), sort_keys=True, default=str))
```

Allocation is proportional with a floor of 3 per stratum, capped at the stratum size (`{"big": 900, "small": 10, "tiny": 2}` at `n = 100` → `round(98.7) = 99`, `max(3, 1) = 3`, `min(2, 3) = 2`), so the total can exceed `n` by the floors: small strata are measured, never skipped.

`data/DATABASE_STRUCTURE.md` "Data jobs": `ob_tick_sample` — one-time D17 coverage sample; summary in `source_ingest_log` (`source = 'ob_tick_sample'`); per-climb CSV stays in `~/Developer/safeascent-private/`.

Append `"app.pipelines.ob_tick_sample"` to strict mypy.

- [ ] **Step 4: Run** — `cd backend && uv run pytest tests/test_ob_tick_sample.py -q && uv run mypy && uv run ruff check . ../scripts/` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/pipelines/ob_tick_sample.py backend/tests/test_ob_tick_sample.py backend/pyproject.toml data/DATABASE_STRUCTURE.md && git commit -m "feat(pipelines): one-time stratified OpenBeta tick-coverage sample with D17 revisit triggers"`

---

### Task 12: OWNER/AGENT RUNBOOK — PR 2b-1a

- [ ] **Step 1 (owner/agent): Rehearsal branch `p2b-1-rehearsal`; extension; migrate; grants**

```bash
( set -a; . ./.env.owner; set +a
  split_pg_url "$(printf '%s' "$OWNER_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  verify_full_url
  psql "$PG_URL_NOPASS" -X -q -f db/owner/extensions_phase2b.sql )
```

(`split_pg_url` and `verify_full_url` are the foundations plan's runbook helpers; `verify_full_url` forces `sslmode=verify-full&sslrootcert=system`, never `require`.) Then `alembic upgrade head` as migrator (→ `0008_catalog (head)`; `0009` is not in this PR), `alembic check`, `grants_phase2.sql`, both verify scripts (the same helpers).

- [ ] **Step 2 (owner/agent): First OpenBeta load on the branch**

```bash
( set -a; . ./.env.ingest; set +a
  export INGEST_DATABASE_URL="$(printf '%s' "$INGEST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  uv sync --group pipelines
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.catalog )
```

Expected: `"status": "ok"`, `routes` near the verified 206K US climbs (spec), `quarantined` small. Report the `unknown` share of roped routes as analyst: `SELECT round(avg((type_group = 'unknown')::int), 3) FROM canonical_routes WHERE NOT is_boulder` — target ≤0.25.

- [ ] **Step 3 (owner/agent): Match, review, evaluate**

`uv run python -m app.pipelines.match run --out ../data/review/matches.csv` (dry run) → counts; then `--apply`. Owner fills `decision` (`link|no_link`) in `data/review/matches.csv` (gitignored, D12), then `… match import --review-file ../data/review/matches.csv` and `… match run --apply` again so owner decisions take effect. Then `… match sample --out ~/Developer/safeascent-private/golden/match_v1.csv` writes a stratified sample **of the auto-links** (300 climb links, 100 area links, every score band and type group represented; columns `kind,mp_id,ob_uuid,stratum,score,label`). The owner fills `label` (`yes|no`) for every row, then `… match eval --golden ~/Developer/safeascent-private/golden/match_v1.csv` → `climb_passes` and `area_passes` both `true` (weighted precision ≥0.98 each; the command exits 1 otherwise, including when a stratum was left unlabelled). If it fails, stop: the thresholds or weights change in a reviewed commit, and the next evaluation uses a **new** sample (`--seed` changed), never the labels the change was tuned on. Record `climb_judged`, `area_judged` and both precisions in the PR.

- [ ] **Step 4 (owner/agent): Promote MP ice/mixed facts** — `uv run python -m app.pipelines.mp_facts` → `routes` in the thousands (spec ~4.8K MP ice+mixed minus OpenBeta matches), `yielded` 0 on the first run. As analyst: `SELECT type_group, count(*) FROM canonical_routes WHERE source = 'mp_facts' GROUP BY 1` shows only `ice` and `mixed`. After every later weekly load, the order is `catalog` → `match run --apply` → `mp_facts` (plan 7's `data-weekly.yml` chains them), so a promoted route yields as soon as OpenBeta covers it.

- [ ] **Step 5 (owner): Secrets and schedule** — GitHub Actions secret `HEALTHCHECKS_PING_KEY` (healthchecks.io → project settings → ping key); create check `openbeta-weekly` (period 7 d, grace 1 d). Run `data-openbeta` once via "Run workflow"; it must pass. Record the `revisit_d17` value it prints (expected `[]`).

- [ ] **Step 6 (owner/agent): Prod** — repeat Steps 1–4 on prod, without the `BRANCH_HOST` substitution (the same `verify_full_url` helper). Delete the branch.

---

### Task 13: OWNER/AGENT RUNBOOK — PR 2b-1b (order matters)

All commands from this PR's head; the jobs resolve `mp_ticks` on either side of `0009`.

- [ ] **Step 1 (owner/agent): Rehearse the order on a branch**: `python -m app.pipelines.mp_ticks_quarantine` → `python -m app.pipelines.mp_tick_counts` (both as ingest; they read `public.mp_ticks`) → `alembic upgrade head` as migrator (`0009`; it refuses if the counts are empty) → `grants_phase2.sql` + both verify scripts → run the quarantine and counts jobs again (now `internal.mp_ticks`; counts identical).
- [ ] **Step 2 (owner): Prod, with the deploy in the middle**: (1) run R8 and the counts builder against prod; (2) deploy the backend from this PR (the API reads `mp_tick_counts`; `mp_ticks` still exists, so nothing breaks either way); (3) open the Ascents tab for a few routes and confirm counts match the previous display (routes with pre-1970 ticks may drop those, by design); (4) `alembic upgrade head` (`0009`), grants, verify; (5) as analyst: `SELECT has_table_privilege('app', 'internal.mp_ticks', 'SELECT'), has_schema_privilege('app', 'internal', 'USAGE')` → `f|f`.
- [ ] **Step 3 (owner/agent): Acceptance cells** — `VERIFY_DATABASE_URL=<analyst url> uv run pytest -m db tests/verify/test_phase2b_catalog.py -q` on the branch after Step 1, then on prod after Step 2 → `7 passed` (the D2 privilege cell is the automated form of Step 2 (5); a failing `no_state_parent` cell means some MP ice/mixed routes' top MP location matches no OpenBeta state name: the owner maps them in a reviewed fix, never by creating a root area).
- [ ] **Step 4 (owner/agent): OpenBeta tick-coverage sample (D17, once)** — on prod after Step 2, as ingest: `uv run python -m app.pipelines.ob_tick_sample --n 1500 --out ~/Developer/safeascent-private/ob_tick_coverage_$(date -u +%Y%m).csv`. Expected: `"status": "ok"` with `measured`, `share_with_ticks`, `median_ob_to_mp_ratio` and `revisit_p2_9`; or `"not_measurable"` if the tick query needs a user (Task 3 Step 1 finding). Record the summary in the PR and in the foundations plan's D17 note. `revisit_p2_9: true` means the owner reopens D17/P2-9 before exposure v2; `false` keeps `ob_ticks` missing (`no_bulk_source`) in exposure v1 (plan 8).
- [ ] **Step 5 (owner): Schedule** — the weekly tick-count rebuild runs after R8 in the same workflow in plan 7 (`data-weekly.yml`); until then run both by hand after any tick load. Re-run Step 4 each January (foundations cadence table).

---

## Self-review

- Spec coverage (2b-1): OpenBeta catalog (Tasks 3–4), `internal` schema move (Tasks 9–10, per D2 rather than all `mp_*`), matcher with thresholds and a stratified auto-link precision gate for climbs and areas (Tasks 5–6, 12), route types and `unknown` ≤25% report (Tasks 2, 12), `mp_facts` ice/mixed load with column guard and yield to OpenBeta (Task 7), `mp_tick_aggregates` load (plan 1), guards `check_no_mp_data.py`, no MP prose in schemas, API SQL or table columns (Task 8), `app` has no privilege on `internal` (plan 1 verify, Task 10 `-m db` cells), weekly bulk + 3% US rule + 10% per-state rule (Task 4), GitHub Actions batch + failure issue + healthchecks (Task 4), D17 tick-coverage sample and revisit triggers (Tasks 4, 11, 13).
- Review fixes carried here (2026-09-28 revision): K2 commitment grades (Task 2), K3 runtime-built URLs/ids (Task 8), K4 token-aligned names, protection-stripped per-system grades, runner-up margin (Task 5), K5 stratified auto-link sample for climbs and areas (Tasks 6, 12), K6 `mp_facts` yield/revive (Tasks 6–7), K7 → DP2 (Task 2, decision 4), claim 4 `mp_id` String parsing (Task 3), D11 caveats: USA+state ancestors, ≤2000 page, duplicate-id paging check, retries, per-state drop, `(0,0)` missing (Tasks 3–4), D2 `-m db` cell (Task 10), D13 no `trainer` grants (Task 1), minors: `scorable_routes` explicit columns, 0008/0009 downgrade gaps, `mp_tick_counts` cut at the run day, fixture ids ≥900000000 in `test_ascent_analytics.py` (Tasks 1, 9, 10).
- Placeholders: none. Runbook angle-bracket values (`<analyst url>`) are Console values the owner supplies, as in plan 1.
- Types: `ObArea`/`ObClimb` fields are used identically in `catalog.build_batch`; `us_states()` returns `(usa_uuid, states)` and `bulk_areas(usa_uuid, state_uuid)` everywhere; `decide` returns `(Decision, UUID | None, float)` everywhere; `grade_similarity` returns `float | None` and only `climb_score` consumes it; `area_score`/`climb_score`/`decide` keep their first-draft signatures (plan 5's R10 imports them); `ltree_label` is shared by `catalog` and `mp_facts`; `mp_tick_counts.build(conn, *, run_id, today)` in Tasks 9–10.
