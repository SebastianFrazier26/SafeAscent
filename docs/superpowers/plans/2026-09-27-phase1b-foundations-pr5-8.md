# Phase 1 Foundations, Part B (PR5–PR8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the schema under Alembic with least-privilege DB roles, make the nightly Celery job impossible to fail silently, fix the "Risk 0.0 on error" bug behind an incremental TypeScript setup, and make the repo legible to a stranger (CLAUDE.md, README, LICENSE, DATA_LICENSE, junk removal, mypy allowlist).

**Architecture:** Four PRs, each on its own branch off `main`. PR5 adds `backend/alembic/` with a baseline revision that replays a sanitized schema-only dump of prod (prod is *stamped*, never upgraded through it), a guarded drop of the empty `ascents`/`climbers` tables, and SQL-only role scripts. PR6 splits Celery beat from the worker, adds a Redis heartbeat bootstep plus `/health/worker`, expired-task accounting, and healthchecks.io dead-man pings. PR7 adds `tsconfig` (allowJs, strict for `.ts`), converts the API client and risk utils, and replaces the `|| 0` fallback with a typed `loading | error | ok` state. PR8 is docs and hygiene. Steps that touch credentials are **OWNER STEPS** with exact commands for the owner's own terminal.

**Tech Stack:** Python 3.12, uv, FastAPI, SQLAlchemy 2.0 async + asyncpg, GeoAlchemy2 0.15, Alembic 1.14, Celery 5.4 + Redis, httpx, pytest, fakeredis, ruff 0.8.4, mypy; Neon Postgres + PostGIS; React 19, Vite 7, Vitest 3, MUI 7, TypeScript, typescript-eslint; Railway; healthchecks.io.

**Spec:** `docs/superpowers/specs/2026-09-27-phase1-foundations-design.md` (rev 3). Sibling plan (PR1–4, defines the interfaces consumed here): `docs/superpowers/plans/2026-09-27-phase1a-foundations-pr1-4.md`.

## Global Constraints

- Phase 0 (`fix/phase0-security`) and Part A PR1–PR4 are merged to `main` before PR5 starts; PR6 needs PR4; PR8 can run in parallel with PR6/PR7 but rebases on PR5 (both touch `README.md`/`DEPLOYMENT.md`).
- Branches (each off an up-to-date `main`): `chore/p1-pr5-alembic`, `feat/p1-pr6-celery-liveness`, `feat/p1-pr7-ts-risk-fix`, `docs/p1-pr8-hygiene`. Never commit to `main`.
- Pushing happens only when the owner invokes `/commitandpush`. No `git push`, `gh pr create`, or any mutating `gh` command in this plan.
- Settings consumed from Part A (exact names): `app.config.Settings` fields `ENVIRONMENT: Literal["development","test","production"] = "production"`, `SQL_ECHO: bool = False`, `DATABASE_URL: str` (required), `ENABLE_ADMIN_ROUTES: bool = False`, `HEALTHCHECKS_NIGHTLY_URL: str | None = None`, `HEALTHCHECKS_BEAT_URL: str | None = None`, `WORKER_HEARTBEAT_TTL_SECONDS: int = 120`; module-level instance `app.config.settings`.
- Backend is managed by uv at `backend/pyproject.toml`; test command `cd backend && uv run pytest`; lint `uv run ruff check app/` (ruff 0.8.4, rules `E4,E7,E9,F`).
- CI jobs (from Part A): `backend`, `frontend`, `guards`, `ci-ok`. `ci-ok` is the single required check; do not rename jobs.
- DB roles are created **only via SQL as the owner** (`CREATE ROLE ... LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD ...`), never via neonctl, the Neon Console, or the Neon API (those grant `neon_superuser`).
- Passwords are generated locally straight into gitignored `backend/.env.<role>` files and are never printed, echoed, or passed through chat. Agents never read those files.
- Migrations run as an explicit step with `MIGRATOR_DATABASE_URL`, never at app startup. Prod is `alembic stamp 0001_baseline`, then `alembic upgrade head`.
- Phase 1 does not touch row contents. `routes`/`mountains` and the `accidents.route_id`/`accidents.mountain_id` FKs stay (Phase 2a drops them).
- The live weather table is `weather`; `weather_patterns` is a stale doc name only.
- healthchecks.io free tier; nightly check = 24h period + 4h grace; beat check = 15 min period + 60 min grace (owner decision 2026-09-28; was 15 + 15). When a ping URL is unset, pings are skipped with a WARNING (no outbound calls in local runs or CI).
- No scraper code in any repo (D9). No Mountain Project data in fixtures, docs, or commits; MP ice/mixed route facts may only be displayed.
- Comments are load-bearing only (why, not what). Match the density of the file being edited.
- `CHANGELOG.md` gets one dated entry per PR, in the existing Keep a Changelog format.
- TDD: failing test first, then the minimal code, then green.

## Decisions this plan makes where the spec is silent or conflicts

These are called out so the owner can overrule them at review.

1. **Ping URL names.** The spec says `HC_PING_URL`; Part A defines `HEALTHCHECKS_NIGHTLY_URL` and `HEALTHCHECKS_BEAT_URL`. This plan uses Part A's names.
2. **Heartbeat cadence.** The spec says "every 60s with a 180s TTL"; Part A defines `WORKER_HEARTBEAT_TTL_SECONDS = 120`. The TTL comes from that setting; the write interval is `TTL / 3` (40s by default).
3. **`broker_heartbeat` is not set.** Celery documents it as AMQP-only; on the Redis broker it is a no-op. `worker_cancel_long_running_tasks_on_connection_loss=True` is set as specified.
4. **Expired-task counter layout.** The spec says "increments `celery:expired:<task>`" and "reports the 7-day count". To get a real 7-day window the key is `celery:expired:<task>:<YYYY-MM-DD>` (UTC) with an 8-day TTL; `/health/worker` sums the last 7 days.
5. **Baseline mechanism.** `0001_baseline` replays a committed, sanitized `pg_dump --schema-only -n public --no-owner --no-privileges` (`backend/alembic/versions/0001_baseline.sql`) instead of hand-written `op.create_table` calls. That is the most faithful way to make "the DB wins" true. The raw dump stays in gitignored `backend/.schema/`.
6. **Migrator URL is not a Settings field.** `alembic/env.py` reads `MIGRATOR_DATABASE_URL` from the process environment. Adding it to `Settings` would put a DDL credential in the app's config surface, and Part A's `.env.example` parity test would then require it there. It is documented in `CLAUDE.md` and `DEPLOYMENT.md` instead.
7. **Password alphabet.** The spec says `openssl rand -base64 32`; this plan uses `openssl rand -hex 32` (256 bits) so passwords need no URL escaping.
8. **Transitional owner inheritance.** `create_roles.sql` grants `migrator` to the owner `WITH SET TRUE, INHERIT TRUE`. Otherwise the running services, still on `neondb_owner` until the relaunch gate, would lose access to the tables whose ownership moves to `migrator`. The relaunch gate revokes it.
9. **Runtime DDL removed.** `historical_predictions` is created at runtime with `CREATE TABLE IF NOT EXISTS` in the nightly task and the historical-trends endpoint. That statement needs `CREATE` on `public` even when the table already exists, so under the `app` role every nightly insert batch would fail (and be swallowed by an `except`). PR5 removes both call sites, deletes the dead `app/tasks/safety_computation.py` (not in Celery's `include`, imported nowhere, also contains DDL), and adds a test that forbids DDL in `app/`.
10. **Legacy table stubs.** `Accident` declares FKs to `routes`/`mountains`, which have no models. PR5 adds metadata-only `Table` stubs (PK column only) so FK targets resolve, and `env.py` excludes them from autogenerate.
11. **Irreversible downgrades.** `0001` and `0002` raise on downgrade. `0002` dropped empty tables whose models are deleted; restoring them has no use.
12. **Frontend tests for `.jsx` components stay `.jsx`.** "New files are TS" applies to modules. A `.tsx` test of a `.jsx` component would type-check against inferred-`any` props, which adds noise and no safety. New TS modules get `.test.ts` tests.
13. **Extra junk found in the tree:** `backend/tests/check_risk_scores.py` (a script with a hardcoded `/Users/sebastianfrazier/SafeAscent/backend` path, no tests) is deleted along with the spec's list. `run_celery_*.sh` are deleted in PR6, where the spec puts them.
14. **Diagrams** are regenerated from committed Mermaid sources (`docs/diagrams/*.mmd`) with `@mermaid-js/mermaid-cli`, so they can be rebuilt later.

---

## File Structure

### PR5 — `chore/p1-pr5-alembic`

| Path | Action | Responsibility |
|---|---|---|
| `backend/scripts/__init__.py` | Create | Makes `scripts` importable by tests and `python -m`. |
| `backend/scripts/sanitize_schema_dump.py` | Create | Turns a raw `pg_dump --schema-only` into replayable SQL; rejects dumps with data, owners, grants, or passwords. |
| `backend/scripts/write_role_url.py` | Create | Builds `<ROLE>_DATABASE_URL` from the owner URL plus a generated password and writes it into a 0600 env file without printing. |
| `backend/tests/test_sanitize_schema_dump.py` | Create | Unit tests for the sanitizer. |
| `backend/tests/test_write_role_url.py` | Create | Unit tests for the URL builder and env-file writer. |
| `backend/.gitignore` | Modify | Ignore `.schema/` (raw dumps). |
| `backend/alembic.ini` | Create | Alembic config; empty `sqlalchemy.url`. |
| `backend/alembic/env.py` | Create | Async env; URL from `sqlalchemy.url` or `MIGRATOR_DATABASE_URL`; GeoAlchemy2 helpers; excludes unmodelled tables. |
| `backend/alembic/script.py.mako` | Create | From the async template. |
| `backend/alembic/versions/0001_baseline.sql` | Create | Sanitized live schema. |
| `backend/alembic/versions/0001_baseline.py` | Create | Creates extensions, replays the SQL. |
| `backend/alembic/versions/0002_drop_ascents_climbers.py` | Create | Asserts both tables are empty, then drops them. |
| `backend/app/models/legacy.py` | Create | Metadata-only stubs for `routes`/`mountains` + `UNMANAGED_LEGACY_TABLES`. |
| `backend/app/models/__init__.py` | Modify | Drop `Climber`/`Ascent`, import `legacy`. |
| `backend/app/models/ascent.py`, `climber.py` | Delete | D8 stage 1. |
| `backend/app/models/accident.py`, `weather.py`, `mp_route.py`, `mp_location.py` | Modify (only as `alembic check` demands) | Match the live schema. |
| `backend/tests/test_migrations.py` | Create | Fresh-DB `upgrade head` + `alembic check`, 0002 guard, role scripts end-to-end. |
| `backend/tests/test_no_runtime_ddl.py` | Create | Forbids DDL in `backend/app/`. |
| `backend/app/tasks/safety_computation_optimized.py` | Modify | Remove runtime `CREATE TABLE`. |
| `backend/app/api/v1/mp_routes.py` | Modify | Remove runtime `CREATE TABLE`. |
| `backend/app/tasks/safety_computation.py` | Delete | Dead legacy task with runtime DDL. |
| `backend/db/roles/create_roles.sql` | Create | Creates `migrator`/`app`, transfers ownership, grants, default privileges. |
| `backend/db/roles/verify_roles.sql` | Create | Asserts memberships and privileges; exits non-zero on any failure. |
| `.github/workflows/ci.yml` | Modify | `MIGRATIONS_TEST_ADMIN_URL` env and new test files in the `backend` job. |
| `data/DATABASE_STRUCTURE.md`, `README.md`, `DEPLOYMENT.md` | Modify | `weather_patterns` → `weather`; legacy FK note. |
| `CHANGELOG.md` | Modify | PR5 entry. |

### PR6 — `feat/p1-pr6-celery-liveness`

| Path | Action | Responsibility |
|---|---|---|
| `backend/app/healthchecks.py` | Create | `ping(url, suffix)`: never raises, never logs the URL. |
| `backend/app/celery_signals.py` | Create | Heartbeat key, `HeartbeatStep` bootstep, `task_revoked` handler, expired counters, `read_worker_health`, `install(app)`. |
| `backend/app/tasks/ops.py` | Create | `beat_heartbeat` task. |
| `backend/app/celery_app.py` | Modify | Include `app.tasks.ops`, beat-heartbeat schedule, cancel-on-connection-loss, `install(celery_app)`. |
| `backend/app/tasks/safety_computation_optimized.py` | Modify | start/success/fail pings around the nightly run. |
| `backend/app/main.py` | Modify | `GET /health/worker`. |
| `backend/railway-worker.toml` | Modify | Worker without `--beat`, with `-E`. |
| `backend/railway-beat.toml` | Create | Beat service, one replica. |
| `backend/run_celery_beat.sh`, `run_celery_worker.sh` | Delete | Superseded by Railway configs. |
| `backend/tests/test_healthchecks.py`, `test_celery_signals.py`, `test_celery_ops.py`, `test_worker_health_route.py` | Create | Tests (fakeredis / MockTransport, no network). |
| `backend/pyproject.toml`, `backend/uv.lock` | Modify | Dev dependency `fakeredis`. |
| `.github/workflows/ci.yml` | Modify | New test files in the `backend` job. |
| `CHANGELOG.md` | Modify | PR6 entry. |

### PR7 — `feat/p1-pr7-ts-risk-fix`

| Path | Action | Responsibility |
|---|---|---|
| `frontend/tsconfig.json` | Create | `allowJs`, `strict`, `noEmit`, `jsx: react-jsx`, `moduleResolution: bundler`. |
| `frontend/src/vite-env.d.ts` | Create | Vite client types + typed `VITE_*` env. |
| `frontend/package.json`, `package-lock.json` | Modify | `typescript`, `typescript-eslint`; `typecheck` script. |
| `frontend/eslint.config.js` | Modify | TS files block; test globs for TS. |
| `frontend/vite.config.js` | Modify | Coverage includes `.ts/.tsx`. |
| `frontend/src/services/api.js` → `api.ts` | Convert | Typed client; `SafetyResponse`; `fetchRouteSafety` with response validation. |
| `frontend/src/utils/riskUtils.js` → `riskUtils.ts` | Convert | Typed utils; `isRiskScore`; `formatRiskScore` returns `"Unavailable"`. |
| `frontend/src/hooks/useRouteSafety.ts` | Create | `SafetyState` union + hook with `retry`. |
| `frontend/src/components/MapView.jsx` | Modify | Use the hook; no `|| 0`. |
| `frontend/src/components/RouteAnalyticsModal.jsx` | Modify | Risk chip from `SafetyState`; error Alert + Retry; no numeric fallback. |
| `frontend/src/components/PredictionResult.jsx` | Modify | Error Alert + Retry; "Unavailable" for missing score. |
| `frontend/src/App.jsx` | Modify | Retry wiring; error rendered by `PredictionResult`. |
| `frontend/src/utils/riskUtils.test.ts`, `services/api.test.ts`, `hooks/useRouteSafety.test.ts`, `components/RouteAnalyticsModal.safety.test.jsx` | Create | Tests. |
| `frontend/src/components/PredictionResult.test.jsx` | Modify | Error and missing-score cases. |
| `.github/workflows/ci.yml` | Modify | `npm run typecheck` in the `frontend` job. |
| `CHANGELOG.md` | Modify | PR7 entry. |

### PR8 — `docs/p1-pr8-hygiene`

| Path | Action | Responsibility |
|---|---|---|
| `CLAUDE.md` | Create | Agent guide: commands, topology, migration policy, data rules, conventions. |
| `README.md` | Rewrite | Point, honest status, architecture, quickstart, data sources + licenses. |
| `LICENSE` | Create | Apache-2.0 full text. |
| `DATA_LICENSE.md` | Create | OpenBeta CC0; MP facts rule; MP internal-only. |
| `DEPLOYMENT.md` | Rewrite | Railway-only; keeps Part A's CI/deploy section. |
| `frontend/README.md` | Modify | React 19. |
| `docs/diagrams/system_design.mmd`, `data_model.mmd` | Create | Diagram sources. |
| `system_design.png`, `data_model.png` | Regenerate | From the sources. |
| `backend/tests/test_prediction_integration.py.backup`, `backend/check_weather_gaps.py`, `backend/test_weather_service.py`, `backend/test_weather_stats_db.py`, `backend/test_request.json`, `backend/tests/check_risk_scores.py` | Delete | Junk. |
| `backend/tests/benchmark_*.py`, `backend/tests/profile_*.py` | Move → `backend/scripts/perf/` | Not tests. |
| `backend/pyproject.toml` | Modify | `[tool.mypy]` allowlist. |
| `.github/workflows/ci.yml` | Modify | mypy step targets `app scripts`. |
| `CHANGELOG.md` | Modify | PR8 entry. |

---

# PR5 — Alembic baseline + least-privilege roles

### Task 1: Schema-dump sanitizer

**Files:**
- Create: `backend/scripts/__init__.py`
- Create: `backend/scripts/sanitize_schema_dump.py`
- Create: `backend/tests/test_sanitize_schema_dump.py`
- Modify: `backend/.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: `scripts.sanitize_schema_dump.sanitize(dump: str) -> str` (raises `DumpRejected(ValueError)`); CLI `uv run python -m scripts.sanitize_schema_dump <raw.sql> <out.sql>`.

- [ ] **Step 1: Create the branch**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git switch main && git pull --ff-only
git switch -c chore/p1-pr5-alembic
```

- [ ] **Step 2: Write the failing tests**

`backend/scripts/__init__.py`: empty file.

`backend/tests/test_sanitize_schema_dump.py`:

```python
import pytest

from scripts.sanitize_schema_dump import DumpRejected, sanitize

RAW = """--
-- PostgreSQL database dump
--

\\restrict abc123

-- Dumped from database version 17.5
-- Dumped by pg_dump version 17.6

SET statement_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SELECT pg_catalog.set_config('search_path', '', false);
SET default_tablespace = '';

CREATE SCHEMA public;
COMMENT ON SCHEMA public IS 'standard public schema';
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;
COMMENT ON EXTENSION postgis IS 'PostGIS geometry and geography spatial types';


CREATE TABLE public.accidents (
    accident_id integer NOT NULL,
    note text DEFAULT 'SET x = 1;'::text
);

CREATE FUNCTION public.set_coords() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  SET LOCAL search_path = public;
  RETURN NEW;
END $$;

\\unrestrict abc123
"""


def test_strips_psql_meta_commands_and_session_settings():
    out = sanitize(RAW)
    assert "\\restrict" not in out
    assert "\\unrestrict" not in out
    assert "SET statement_timeout" not in out
    assert "SET transaction_timeout" not in out
    assert "set_config('search_path'" not in out
    assert "SET default_tablespace" not in out


def test_strips_schema_and_extension_statements():
    out = sanitize(RAW)
    assert "CREATE SCHEMA public;" not in out
    assert "COMMENT ON SCHEMA public" not in out
    assert "CREATE EXTENSION" not in out
    assert "COMMENT ON EXTENSION" not in out


def test_keeps_ddl_and_indented_set_inside_function_bodies():
    out = sanitize(RAW)
    assert "CREATE TABLE public.accidents (" in out
    assert "'SET x = 1;'::text" in out
    assert "  SET LOCAL search_path = public;" in out


def test_collapses_blank_runs():
    assert "\n\n\n" not in sanitize(RAW)


@pytest.mark.parametrize(
    "line",
    [
        "COPY public.accidents (accident_id) FROM stdin;",
        "INSERT INTO public.accidents VALUES (1);",
        "ALTER TABLE public.accidents OWNER TO neondb_owner;",
        "GRANT SELECT ON TABLE public.accidents TO analyst;",
        "CREATE ROLE x PASSWORD 'y';",
    ],
)
def test_rejects_data_owners_grants_and_passwords(line):
    with pytest.raises(DumpRejected):
        sanitize(RAW + line + "\n")
```

In `backend/.gitignore`, append:

```gitignore

# Raw schema dumps from prod; only the sanitized copy under alembic/versions is committed
.schema/
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `cd backend && uv run pytest tests/test_sanitize_schema_dump.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'scripts.sanitize_schema_dump'`.

- [ ] **Step 4: Implement the sanitizer**

`backend/scripts/sanitize_schema_dump.py`:

```python
"""Turn a raw `pg_dump --schema-only` into SQL that 0001_baseline can replay."""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Only column-0 statements are session settings; indented SETs live inside function bodies.
_DROP_LINE = re.compile(
    r"""^(
        \\.*                                          # psql meta-commands (\restrict, \connect)
      | SET\s+\w+\s*=.*;                              # session settings; some are version-specific
      | SELECT\s+pg_catalog\.set_config\('search_path'.*;
      | CREATE\s+SCHEMA\s+public;
      | COMMENT\s+ON\s+SCHEMA\s+public\s+IS\s+.*;
      | CREATE\s+EXTENSION\s+.*;                      # 0001_baseline creates extensions itself
      | COMMENT\s+ON\s+EXTENSION\s+.*;
    )\s*$""",
    re.VERBOSE,
)

_REJECT_LINE = re.compile(
    r"^(COPY\s|INSERT\s+INTO\s|GRANT\s|REVOKE\s|ALTER\s+DEFAULT\s+PRIVILEGES\s)|\sOWNER\s+TO\s|\bPASSWORD\b",
    re.IGNORECASE,
)


class DumpRejected(ValueError):
    """The dump holds data, ownership, privileges, or credentials and must be re-taken."""


def sanitize(dump: str) -> str:
    kept: list[str] = []
    for number, line in enumerate(dump.splitlines(), start=1):
        if _REJECT_LINE.search(line):
            raise DumpRejected(
                f"line {number} looks like data, ownership, a grant, or a password; "
                "re-dump with --schema-only --no-owner --no-privileges"
            )
        if _DROP_LINE.match(line):
            continue
        kept.append(line.rstrip())
    text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m scripts.sanitize_schema_dump RAW_DUMP OUT_SQL", file=sys.stderr)
        return 2
    raw, out = Path(argv[1]), Path(argv[2])
    out.write_text(sanitize(raw.read_text(encoding="utf-8")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd backend && uv run pytest tests/test_sanitize_schema_dump.py -v`
Expected: `9 passed`.

- [ ] **Step 6: Lint and commit**

```bash
cd backend && uv run ruff check app/ scripts/ tests/test_sanitize_schema_dump.py
cd .. && git add backend/scripts/__init__.py backend/scripts/sanitize_schema_dump.py backend/tests/test_sanitize_schema_dump.py backend/.gitignore
git commit -m "chore(db): add schema-dump sanitizer for the Alembic baseline

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: OWNER STEP — take the schema-only dump as `analyst`

Auto-mode blocks agents from reading `backend/.env.analyst`, so the owner runs this in their own terminal. The agent does not open `.env.*` files or the raw dump's connection details.

**Files:**
- Create (gitignored, never committed): `backend/.schema/prod_schema_raw.sql`, `backend/.schema/server_info.txt`

**Interfaces:**
- Produces: `backend/.schema/prod_schema_raw.sql` for Task 3; `server_info.txt` holding the Postgres major version and extension list, which set the CI/compose PostGIS image and `EXTENSIONS` in `0001_baseline.py`.

- [ ] **Step 1 (owner): Record the server version and extensions**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
mkdir -p .schema && chmod 700 .schema
(
  set -a; . ./.env.analyst; set +a
  PGURL="${ANALYST_DATABASE_URL/postgresql+asyncpg:/postgresql:}"
  PGURL="${PGURL/ssl=require/sslmode=require}"
  export PGURL
  docker run --rm -e PGURL postgres:17-alpine sh -c \
    'psql "$PGURL" -XAt -c "SHOW server_version" -c "SELECT extname || '"' '"' || extversion FROM pg_extension ORDER BY 1"'
) > .schema/server_info.txt
cat .schema/server_info.txt
```

Expected: first line is the server version (e.g. `17.5`), then one `name version` line per extension (e.g. `plpgsql 1.0`, `postgis 3.5.2`). The file contains no credentials, so the owner may paste it to the agent.

- [ ] **Step 2 (owner): Dump the schema with a matching `pg_dump`**

Use the `postgres:<major>-alpine` image that matches the first line of `server_info.txt` (shown here for 17):

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
(
  set -a; . ./.env.analyst; set +a
  PGURL="${ANALYST_DATABASE_URL/postgresql+asyncpg:/postgresql:}"
  PGURL="${PGURL/ssl=require/sslmode=require}"
  export PGURL
  docker run --rm -e PGURL postgres:17-alpine sh -c \
    'pg_dump "$PGURL" --schema-only --schema=public --no-owner --no-privileges --no-publications --no-subscriptions --no-security-labels --no-tablespaces'
) > .schema/prod_schema_raw.sql
wc -l .schema/prod_schema_raw.sql
git status --short .schema
```

Expected: a line count in the hundreds; `git status` prints nothing (ignored). If `pg_dump` reports `permission denied for table …`, `analyst` lacks SELECT on that table. Stop and decide with the agent. Do not switch to the owner role for the dump.

- [ ] **Step 3 (owner): Tell the agent the dump is ready and paste `server_info.txt`**

---

### Task 3: Alembic scaffold, `0001_baseline`, and a clean `alembic check`

**Files:**
- Create: `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`, `backend/alembic/versions/0001_baseline.py`, `backend/alembic/versions/0001_baseline.sql`
- Create: `backend/app/models/legacy.py`
- Create: `backend/tests/test_migrations.py`
- Modify: `backend/app/models/__init__.py`
- Modify (only as `alembic check` requires): `backend/app/models/accident.py`, `weather.py`, `mp_route.py`, `mp_location.py`
- Modify: `.github/workflows/ci.yml` (job `backend`)
- Modify (only if the Neon major differs from the current image): `docker-compose.yml` `db` image

**Interfaces:**
- Consumes: `scripts.sanitize_schema_dump` (Task 1); `backend/.schema/prod_schema_raw.sql` and `server_info.txt` (Task 2); `app.db.session.Base`.
- Produces: revision id `"0001_baseline"`; `app.models.legacy.UNMANAGED_LEGACY_TABLES: frozenset[str]`; test helpers in `tests/test_migrations.py`: `_alembic_cfg(dbname: str) -> Config`, `_run(dbname: str, sql: str) -> None`, `_fetch_row(dbname: str, sql: str) -> list[object]`, fixture `fresh_db -> str`; env var `MIGRATIONS_TEST_ADMIN_URL` (libpq URL to a maintenance DB on a superuser connection).

- [ ] **Step 1: Scaffold Alembic from the async template**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
uv run alembic init -t async alembic
```

Expected: `Creating directory .../backend/alembic ... done` and `Generating .../backend/alembic.ini ... done`.

In `backend/alembic.ini`, set these keys (leave the rest of the generated file as is):

```ini
script_location = %(here)s/alembic
prepend_sys_path = .
sqlalchemy.url =
```

The template ships `sqlalchemy.url = driver://user:pass@localhost/dbname`. It must be blank, or `env.py` would treat the placeholder as a real URL.

- [ ] **Step 2: Sanitize the dump into the versions directory**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
uv run python -m scripts.sanitize_schema_dump .schema/prod_schema_raw.sql alembic/versions/0001_baseline.sql
grep -nEi "password|secret|token|neondb_owner|analyst|@|^COPY |^INSERT INTO" alembic/versions/0001_baseline.sql || echo "clean"
grep -oE "CREATE TABLE public\.[a-z_0-9]+" alembic/versions/0001_baseline.sql | sort -u
```

Expected: `clean`. The table list includes at least `accidents`, `ascents`, `climbers`, `historical_predictions`, `mountains`, `mp_locations`, `mp_routes`, `routes`, `weather`. If the sanitizer raises `DumpRejected`, send the message to the owner and repeat Task 2 Step 2. Read the whole `.sql` file once. It must contain only DDL and must not contain prose from `COMMENT ON` statements that quotes MP content. If it does, stop and ask the owner.

- [ ] **Step 3: Write the legacy stubs**

`backend/app/models/legacy.py`:

```python
"""Metadata-only stand-ins for legacy tables that Phase 2a drops.

accidents.route_id and accidents.mountain_id carry real FKs to these tables, so the
targets must exist in Base.metadata for FK resolution. alembic/env.py excludes them
from autogenerate, so their full live shape never has to be modelled.
"""

from sqlalchemy import Column, Integer, Table

from app.db.session import Base

UNMANAGED_LEGACY_TABLES = frozenset({"routes", "mountains"})

routes_table = Table("routes", Base.metadata, Column("route_id", Integer, primary_key=True))
mountains_table = Table("mountains", Base.metadata, Column("mountain_id", Integer, primary_key=True))
```

In `backend/app/models/__init__.py`, add the import after the existing model imports. This task leaves the `Climber`/`Ascent` lines alone; Task 4 removes them.

```python
from app.models import legacy  # noqa: F401  (registers FK targets on Base.metadata)
```

- [ ] **Step 4: Write `env.py`**

Replace `backend/alembic/env.py` entirely:

```python
import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("MIGRATOR_DATABASE_URL")
    if not url:
        raise RuntimeError("Set MIGRATOR_DATABASE_URL (the migrator role) to run migrations")
    return url


# app.db.session builds its engine from Settings, which requires DATABASE_URL. Migrations
# must not depend on the app's runtime credentials, so reuse the migrator URL here.
os.environ.setdefault("DATABASE_URL", _database_url())

import app.models  # noqa: E402,F401  (populates Base.metadata)
from app.db.session import Base  # noqa: E402
from app.models.legacy import UNMANAGED_LEGACY_TABLES  # noqa: E402

target_metadata = Base.metadata


def include_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    if type_ == "table":
        if name in UNMANAGED_LEGACY_TABLES:
            return False
        if reflected and compare_to is None:
            # Live tables with no model (e.g. historical_predictions) are written by raw
            # SQL; autogenerate must never propose dropping them.
            return False
    return bool(alembic_helpers.include_object(obj, name, type_, reflected, compare_to))


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_object=include_object,
        render_item=alembic_helpers.render_item,
        process_revision_directives=alembic_helpers.writer,
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=_database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": _database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
```

- [ ] **Step 5: Write `0001_baseline.py`**

Set `EXTENSIONS` to every extension in `server_info.txt` except `plpgsql` (built in). For a server reporting `plpgsql` and `postgis`:

`backend/alembic/versions/0001_baseline.py`:

```python
"""Baseline: the live Neon schema as of 2026-09-27.

Prod is stamped at this revision, never upgraded through it. The SQL beside this file
is a sanitized `pg_dump --schema-only -n public` (scripts/sanitize_schema_dump.py), so
the database, not the models, is the source of truth here.
"""

from pathlib import Path

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

BASELINE_SQL = Path(__file__).with_name("0001_baseline.sql")
EXTENSIONS = ("postgis",)


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')
    sql = BASELINE_SQL.read_text(encoding="utf-8")
    # asyncpg prepares every statement it is handed; a multi-statement dump only runs
    # through the raw driver's simple-query path.
    op.get_bind().connection.dbapi_connection.run_async(lambda conn: conn.execute(sql))


def downgrade() -> None:
    raise NotImplementedError("0001_baseline is the root revision; drop the database instead")
```

- [ ] **Step 6: Write the failing migration test**

`backend/tests/test_migrations.py`:

```python
"""Migration tests against a throwaway database on a real PostGIS server.

Set MIGRATIONS_TEST_ADMIN_URL to a libpq URL for a superuser connection to a
maintenance database, e.g. postgresql://test_user:test_password@localhost:5432/postgres.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

ADMIN_URL = os.environ.get("MIGRATIONS_TEST_ADMIN_URL")
BACKEND = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="MIGRATIONS_TEST_ADMIN_URL not set")


def _db_url(dbname: str) -> str:
    assert ADMIN_URL is not None
    return ADMIN_URL.rsplit("/", 1)[0] + f"/{dbname}"


async def _execute(url: str, sql: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def _fetchrow(url: str, sql: str) -> list[object]:
    conn = await asyncpg.connect(url)
    try:
        record = await conn.fetchrow(sql)
        return list(record) if record is not None else []
    finally:
        await conn.close()


def _run(dbname: str, sql: str) -> None:
    asyncio.run(_execute(_db_url(dbname), sql))


def _fetch_row(dbname: str, sql: str) -> list[object]:
    return asyncio.run(_fetchrow(_db_url(dbname), sql))


def _alembic_cfg(dbname: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _db_url(dbname).replace("postgresql://", "postgresql+asyncpg://", 1))
    return cfg


@pytest.fixture
def fresh_db() -> Iterator[str]:
    assert ADMIN_URL is not None
    name = f"mig_{uuid.uuid4().hex[:12]}"
    asyncio.run(_execute(ADMIN_URL, f'CREATE DATABASE "{name}"'))
    try:
        yield name
    finally:
        asyncio.run(_execute(ADMIN_URL, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def test_baseline_builds_live_schema_and_models_match(fresh_db):
    cfg = _alembic_cfg(fresh_db)
    command.upgrade(cfg, "0001_baseline")
    row = _fetch_row(
        fresh_db,
        "SELECT to_regclass('public.accidents')::text, to_regclass('public.weather')::text, "
        "to_regclass('public.routes')::text, to_regclass('public.mountains')::text",
    )
    assert row == ["accidents", "weather", "routes", "mountains"]
    command.upgrade(cfg, "head")
    command.check(cfg)
```

- [ ] **Step 7: Start a local PostGIS matching Neon's major and run the test**

Pick the `postgis/postgis:<major>-<postgis>` tag matching `server_info.txt` (shown for Postgres 17 / PostGIS 3.5):

```bash
docker run -d --name sa-mig-pg -e POSTGRES_USER=test_user -e POSTGRES_PASSWORD=test_password -p 55432:5432 postgis/postgis:17-3.5
until docker exec sa-mig-pg pg_isready -U test_user >/dev/null 2>&1; do sleep 1; done
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
MIGRATIONS_TEST_ADMIN_URL=postgresql://test_user:test_password@localhost:55432/postgres \
DATABASE_URL=postgresql+asyncpg://test_user:test_password@localhost:55432/postgres \
  uv run pytest tests/test_migrations.py -v
```

Expected: either PASS, or FAIL at `command.check(cfg)` with `alembic.util.exc.AutogenerateDiffsDetected: New upgrade operations detected: [...]` listing model/DB differences. A failure inside `upgrade` (a SQL error from the baseline) means the image does not match the Neon major or an extension is missing from `EXTENSIONS`. Fix that first.

- [ ] **Step 8: Make the models match the live schema (the DB wins)**

For each operation in the `AutogenerateDiffsDetected` list, edit the model, never the SQL:

- `('remove_index', Index('ix_accidents_accident_id', ...))`: the model declares an index the DB lacks. Drop `index=True` from that column.
- `('add_index', ...)` for an index present in the DB only: add `index=True`, or a named `Index("<live name>", <col>)` in `__table_args__` when the live name is not SQLAlchemy's `ix_<table>_<col>` default.
- `('modify_nullable', ...)`: set `nullable=` to the live value.
- `('modify_type', ...)`: set the column type to the live type (e.g. `String(100)` → `String(255)`, `Float` → `Double`).
- `('add_fk'|'remove_fk', ...)`: make the model's `ForeignKey` match the live constraint. If the live DB has no `accidents.mountain_id` FK, remove `ForeignKey("mountains.mountain_id")` and keep the plain `Integer` column.
- A spatial index difference on a `Geography` column: set `spatial_index=False` on the column and declare the live GIST index with its live name: `Index("<live name>", "coordinates", postgresql_using="gist")`.
- Tables present only in the DB and not in the list above are already excluded by `include_object`. Do not add models for them.

Re-run the Step 7 command after each batch of edits.
Expected when finished: `1 passed`.

Then run the full suite so the model edits break nothing:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
DATABASE_URL=postgresql+asyncpg://test_user:test_password@localhost:55432/postgres uv run pytest -q
```

Expected: the same pass/skip counts as on `main`, plus the new tests passing.

- [ ] **Step 9: Wire the test into CI**

In `.github/workflows/ci.yml`, job `backend`:
1. The `postgres` service image must match Neon's major. If `server_info.txt` shows a different major from the image currently used, change the image to `postgis/postgis:<major>-<postgis minor>-alpine` (e.g. `postgis/postgis:17-3.5-alpine`), and make the same change to the `db` service in `docker-compose.yml`.
2. On the step that runs `uv run pytest`, add to its `env:` block:

```yaml
          MIGRATIONS_TEST_ADMIN_URL: postgresql://test_user:test_password@localhost:5432/postgres
```

3. If that step passes an explicit list of test files, append `tests/test_migrations.py tests/test_sanitize_schema_dump.py`. If it runs the whole `tests/` directory, leave the list alone.

- [ ] **Step 10: Lint and commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend && uv run ruff check app/ scripts/ alembic/ tests/test_migrations.py
cd .. && git add backend/alembic.ini backend/alembic backend/app/models backend/tests/test_migrations.py .github/workflows/ci.yml docker-compose.yml
git commit -m "feat(db): Alembic with a baseline replaying the live schema

Prod is stamped at 0001_baseline, never upgraded through it. Models were
aligned to the live schema until alembic check is clean.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `0002_drop_ascents_climbers` and model removal (D8 stage 1)

**Files:**
- Create: `backend/alembic/versions/0002_drop_ascents_climbers.py`
- Delete: `backend/app/models/ascent.py`, `backend/app/models/climber.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: `_alembic_cfg`, `_run`, `_fetch_row`, `fresh_db` (Task 3).
- Produces: revision id `"0002_drop_ascents_climbers"` (head).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_migrations.py`:

```python
def test_head_drops_ascents_and_climbers_but_keeps_legacy_tables(fresh_db):
    command.upgrade(_alembic_cfg(fresh_db), "head")
    row = _fetch_row(
        fresh_db,
        "SELECT to_regclass('public.ascents')::text, to_regclass('public.climbers')::text, "
        "to_regclass('public.routes')::text, to_regclass('public.mountains')::text, "
        "(SELECT count(*) FROM pg_constraint WHERE conrelid = 'public.accidents'::regclass "
        " AND contype = 'f' AND confrelid = 'public.routes'::regclass)",
    )
    assert row == [None, None, "routes", "mountains", 1]


def test_0002_refuses_to_drop_non_empty_tables(fresh_db):
    cfg = _alembic_cfg(fresh_db)
    command.upgrade(cfg, "0001_baseline")
    _run(fresh_db, "INSERT INTO climbers (username) VALUES ('fixture-user')")
    with pytest.raises(RuntimeError, match="refusing to drop climbers"):
        command.upgrade(cfg, "head")
    assert _fetch_row(fresh_db, "SELECT to_regclass('public.ascents')::text") == ["ascents"]


def test_models_no_longer_define_dropped_tables():
    from app.db.session import Base

    assert "ascents" not in Base.metadata.tables
    assert "climbers" not in Base.metadata.tables
```

If the live `climbers` table has another NOT NULL column without a default, add it to the INSERT with a dummy value. The baseline SQL shows the columns.

- [ ] **Step 2: Run and confirm failure**

Run (docker DB from Task 3 still up): `cd backend && MIGRATIONS_TEST_ADMIN_URL=postgresql://test_user:test_password@localhost:55432/postgres DATABASE_URL=postgresql+asyncpg://test_user:test_password@localhost:55432/postgres uv run pytest tests/test_migrations.py -v`
Expected: the three new tests FAIL (`ascents` still exists; no RuntimeError raised; `ascents` in metadata).

- [ ] **Step 3: Write the migration**

`backend/alembic/versions/0002_drop_ascents_climbers.py`:

```python
"""Drop ascents and climbers (D8 stage 1); both held 0 rows in the 2026-09-27 audit."""

import sqlalchemy as sa
from alembic import op

revision = "0002_drop_ascents_climbers"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

# ascents references climbers, so it is dropped first.
TABLES_TO_DROP = ("ascents", "climbers")


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES_TO_DROP:
        rows = bind.execute(sa.select(sa.func.count()).select_from(sa.table(name))).scalar_one()
        if rows != 0:
            raise RuntimeError(f"refusing to drop {name}: {rows} rows present (audit expected 0)")
    for name in TABLES_TO_DROP:
        op.drop_table(name)


def downgrade() -> None:
    raise NotImplementedError("0002 is one-way: the dropped tables were empty and their models are gone")
```

- [ ] **Step 4: Remove the models**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent && git rm backend/app/models/ascent.py backend/app/models/climber.py
```

`backend/app/models/__init__.py` becomes:

```python
"""
Database models export.
Import all models here to make them available for Alembic migrations.
"""
from app.models import legacy  # noqa: F401  (registers FK targets on Base.metadata)
from app.models.accident import Accident
from app.models.mp_location import MpLocation
from app.models.mp_route import MpRoute
from app.models.weather import Weather

__all__ = [
    "Accident",
    "Weather",
    "MpLocation",
    "MpRoute",
]
```

Then confirm nothing else imports them:

Run: `cd /Users/sebastianfrazier/Developer/SafeAscent && git grep -nE "Climber|Ascent\b|models\.(ascent|climber)" -- backend`
Expected: no output.

- [ ] **Step 5: Run and confirm pass**

Run the Step 2 command.
Expected: `4 passed` (Task 3's test plus these three).

- [ ] **Step 6: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add backend/alembic/versions/0002_drop_ascents_climbers.py backend/app/models/__init__.py backend/tests/test_migrations.py
git commit -m "feat(db): drop empty ascents/climbers tables and models (D8 stage 1)

The migration aborts unless both tables are empty.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Remove runtime DDL from app code

**Files:**
- Create: `backend/tests/test_no_runtime_ddl.py`
- Modify: `backend/app/tasks/safety_computation_optimized.py` (the `save_to_historical_predictions` batch loop, ~lines 861–875 on Phase 0)
- Modify: `backend/app/api/v1/mp_routes.py` (historical-trends handler, ~lines 1524–1542 on Phase 0)
- Delete: `backend/app/tasks/safety_computation.py`

**Interfaces:**
- Produces: the invariant "no DDL under `backend/app/`", which PR5's `app` role relies on.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_no_runtime_ddl.py`:

```python
"""Schema changes belong in Alembic. The app role has no CREATE on public, and even
CREATE TABLE IF NOT EXISTS checks that privilege before it checks existence."""

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
DDL = re.compile(
    r"\b(CREATE\s+(TABLE|INDEX|EXTENSION|SCHEMA|VIEW)|ALTER\s+TABLE|DROP\s+(TABLE|INDEX|VIEW))\b",
    re.IGNORECASE,
)


def test_app_code_issues_no_ddl():
    offenders = [
        f"{path.relative_to(APP_DIR.parent)}:{number}"
        for path in sorted(APP_DIR.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if DDL.search(line)
    ]
    assert offenders == []
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd backend && uv run pytest tests/test_no_runtime_ddl.py -v`
Expected: FAIL, listing `app/api/v1/mp_routes.py:<n>`, `app/tasks/safety_computation.py:<n>` (3 lines), and `app/tasks/safety_computation_optimized.py:<n>`.

- [ ] **Step 3: Remove the call sites**

In `backend/app/tasks/safety_computation_optimized.py`, inside the batch `try:` of the historical save, delete the table-creation block so the `try:` begins with the INSERT:

```python
        try:
            # Ensure table exists (idempotent, safe to run)
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS historical_predictions (
                    id SERIAL PRIMARY KEY,
                    route_id INTEGER NOT NULL,
                    prediction_date DATE NOT NULL,
                    risk_score FLOAT,
                    color_code VARCHAR(20),
                    calculated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(route_id, prediction_date)
                )
            """))
            await db.commit()

            await db.execute(text(f"""
                INSERT INTO historical_predictions
```

becomes

```python
        try:
            await db.execute(text(f"""
                INSERT INTO historical_predictions
```

In `backend/app/api/v1/mp_routes.py`, delete this whole block (the comment and the `try/except`) in the historical-trends handler:

```python
    # Ensure historical table exists so first-run routes don't hard-error.
    # If permissions prevent table creation, continue and let the normal
    # query/error path report availability.
    try:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS historical_predictions (
                id SERIAL PRIMARY KEY,
                route_id INTEGER NOT NULL,
                prediction_date DATE NOT NULL,
                risk_score FLOAT,
                color_code VARCHAR(20),
                calculated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(route_id, prediction_date)
            )
        """))
        await db.commit()
    except Exception as table_err:
        await db.rollback()
        logger.warning(f"Could not ensure historical_predictions table exists: {table_err}")

```

Delete the dead legacy task:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent && git rm backend/app/tasks/safety_computation.py
git grep -n "tasks.safety_computation\b\|tasks\.safety_computation\"" -- backend || echo "no references"
```

Expected: `no references`.

- [ ] **Step 4: Run and confirm pass**

Run: `cd backend && uv run pytest tests/test_no_runtime_ddl.py -v && uv run ruff check app/`
Expected: `1 passed`; ruff `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add backend/tests/test_no_runtime_ddl.py backend/app/tasks/safety_computation_optimized.py backend/app/api/v1/mp_routes.py
git commit -m "fix(db): remove runtime CREATE TABLE; schema lives in Alembic

Under the least-privilege app role, CREATE TABLE IF NOT EXISTS fails even when
the table exists, which silently dropped every historical_predictions batch.
Also deletes the unused legacy safety_computation task.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Role scripts (SQL only) + role URL writer

> **Superseded in part (2026-09-28, fix round 1):** the committed `create_roles.sql` takes SCRAM verifiers (`MIGRATOR_PASSWORD_SCRAM`/`APP_PASSWORD_SCRAM`) and grants `app` writes only on `historical_predictions`. The code below is the original draft; the files in `backend/db/roles/` and `backend/scripts/write_role_url.py` are authoritative.

**Files:**
- Create: `backend/db/roles/create_roles.sql`
- Create: `backend/db/roles/verify_roles.sql`
- Create: `backend/scripts/write_role_url.py`
- Create: `backend/tests/test_write_role_url.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: `fresh_db`, `_alembic_cfg`, `_run`, `_db_url` (Task 3).
- Produces: `scripts.write_role_url.build_role_url(owner_url: str, role: str, password: str) -> str`; `scripts.write_role_url.upsert_env_line(path: Path, key: str, value: str) -> None`; CLI `python -m scripts.write_role_url --role <name> --env-file <path>` (reads `OWNER_DATABASE_URL` and `<ROLE>_PASSWORD` from the environment); psql scripts reading `MIGRATOR_PASSWORD` / `APP_PASSWORD` via `\getenv`.

- [ ] **Step 1: Write the failing URL-writer tests**

`backend/tests/test_write_role_url.py`:

```python
import stat

import pytest

from scripts.write_role_url import build_role_url, main, upsert_env_line

OWNER = "postgresql://neondb_owner:ownerpw@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


def test_build_role_url_swaps_credentials_and_uses_asyncpg_ssl():
    url = build_role_url(OWNER, "app", "abc123")
    assert url == "postgresql+asyncpg://app:abc123@ep-x-123.us-east-2.aws.neon.tech/neondb?ssl=require"
    assert "ownerpw" not in url


def test_build_role_url_keeps_port_and_escapes_password():
    url = build_role_url("postgresql://o:p@localhost:5433/db", "migrator", "a/b+c")
    assert url == "postgresql+asyncpg://migrator:a%2Fb%2Bc@localhost:5433/db?ssl=require"


def test_build_role_url_rejects_hostless_url():
    with pytest.raises(ValueError):
        build_role_url("postgresql:///db", "app", "x")


def test_upsert_env_line_replaces_and_sets_0600(tmp_path):
    env = tmp_path / ".env.app"
    env.write_text("APP_PASSWORD=x\nAPP_DATABASE_URL=old\n")
    upsert_env_line(env, "APP_DATABASE_URL", "new")
    assert env.read_text() == "APP_PASSWORD=x\nAPP_DATABASE_URL=new\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_main_writes_url_without_printing_secrets(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env.app"
    env.write_text("APP_PASSWORD=s3cret\n")
    monkeypatch.setenv("OWNER_DATABASE_URL", OWNER)
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert main(["--role", "app", "--env-file", str(env)]) == 0
    assert "APP_DATABASE_URL=postgresql+asyncpg://app:s3cret@" in env.read_text()
    captured = capsys.readouterr()
    assert "s3cret" not in captured.out + captured.err
    assert "ownerpw" not in captured.out + captured.err
```

Run: `cd backend && uv run pytest tests/test_write_role_url.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.write_role_url'`.

- [ ] **Step 2: Implement the URL writer**

`backend/scripts/write_role_url.py`:

```python
"""Write <ROLE>_DATABASE_URL into a gitignored env file without echoing any secret."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import quote, urlsplit


def build_role_url(owner_url: str, role: str, password: str) -> str:
    parts = urlsplit(owner_url)
    if not parts.hostname:
        raise ValueError("owner URL has no host")
    host = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    # asyncpg spells libpq's sslmode as ssl and rejects channel_binding.
    return f"postgresql+asyncpg://{quote(role, safe='')}:{quote(password, safe='')}@{host}{parts.path}?ssl=require"


def upsert_env_line(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if not line.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args(argv)
    prefix = args.role.upper()
    owner_url = os.environ["OWNER_DATABASE_URL"]
    password = os.environ[f"{prefix}_PASSWORD"]
    upsert_env_line(args.env_file, f"{prefix}_DATABASE_URL", build_role_url(owner_url, args.role, password))
    print(f"wrote {prefix}_DATABASE_URL to {args.env_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run: `cd backend && uv run pytest tests/test_write_role_url.py -v`
Expected: `5 passed`.

- [ ] **Step 3: Write the failing end-to-end role test**

Append to `backend/tests/test_migrations.py`:

```python
import shutil
import subprocess

PSQL = shutil.which("psql")
ROLES_DIR = BACKEND / "db" / "roles"
TEST_ROLES = ("migrator", "app", "analyst")

ANALYST_FIXTURE_SQL = """
CREATE ROLE analyst LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD 'test-analyst-pw';
ALTER ROLE analyst SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA public TO analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst;
"""


def _drop_test_roles() -> None:
    assert ADMIN_URL is not None
    asyncio.run(_execute(ADMIN_URL, "DROP ROLE IF EXISTS " + ", ".join(TEST_ROLES)))


@pytest.fixture
def role_cleanup() -> Iterator[None]:
    # Requested before fresh_db, so it tears down after the database (and every object
    # these roles own in it) is gone; roles are cluster-wide.
    _drop_test_roles()
    yield
    _drop_test_roles()


def _psql(dbname: str, script: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert PSQL is not None
    return subprocess.run(
        [PSQL, _db_url(dbname), "-X", "-q", "-f", str(script)],
        env={**os.environ, **env_extra},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(PSQL is None, reason="psql (15+) not installed")
def test_role_scripts_create_least_privilege_roles(role_cleanup, fresh_db):
    command.upgrade(_alembic_cfg(fresh_db), "head")
    _run(fresh_db, ANALYST_FIXTURE_SQL)

    created = _psql(
        fresh_db,
        ROLES_DIR / "create_roles.sql",
        {"MIGRATOR_PASSWORD": "test-migrator-pw", "APP_PASSWORD": "test-app-pw"},
    )
    assert created.returncode == 0, created.stderr
    assert "test-migrator-pw" not in created.stdout + created.stderr
    assert "test-app-pw" not in created.stdout + created.stderr

    verified = _psql(fresh_db, ROLES_DIR / "verify_roles.sql", {})
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "ALL ROLE CHECKS PASSED" in verified.stdout

    base = _db_url(fresh_db).split("://", 1)[1].split("@", 1)[1]
    app_url = f"postgresql://app:test-app-pw@{base}"
    migrator_url = f"postgresql://migrator:test-migrator-pw@{base}"

    asyncio.run(_execute(app_url, "SELECT count(*) FROM accidents"))
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        asyncio.run(_execute(app_url, "CREATE TABLE app_should_not_create (x int)"))
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        asyncio.run(_execute(app_url, "TRUNCATE accidents"))

    asyncio.run(_execute(migrator_url, "CREATE TABLE new_after_roles (id serial PRIMARY KEY)"))
    asyncio.run(_execute(app_url, "INSERT INTO new_after_roles DEFAULT VALUES"))
```

Run: `cd backend && MIGRATIONS_TEST_ADMIN_URL=postgresql://test_user:test_password@localhost:55432/postgres DATABASE_URL=postgresql+asyncpg://test_user:test_password@localhost:55432/postgres uv run pytest tests/test_migrations.py::test_role_scripts_create_least_privilege_roles -v`
Expected: FAIL. `psql` reports `create_roles.sql: No such file or directory`. If `psql` is missing locally, first run `brew install libpq && export PATH="/opt/homebrew/opt/libpq/bin:$PATH"`.

- [ ] **Step 4: Write `create_roles.sql`**

`backend/db/roles/create_roles.sql`:

```sql
-- Run as the database owner. Passwords come from the environment (\getenv), never argv.
-- Create roles only through this file: neonctl / Console / API roles join neon_superuser.
\set ON_ERROR_STOP on
-- terse + no context keeps failing statements (which contain passwords) out of the output.
\set VERBOSITY terse
\set SHOW_CONTEXT never

\getenv migrator_password MIGRATOR_PASSWORD
\getenv app_password APP_PASSWORD
\if :{?migrator_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'MIGRATOR_PASSWORD is not set'; END $$;
\endif
\if :{?app_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'APP_PASSWORD is not set'; END $$;
\endif

SELECT current_setting('server_version_num')::int >= 160000 AS pg16_or_newer \gset
\if :pg16_or_newer
\else
  DO $$ BEGIN RAISE EXCEPTION 'GRANT ... WITH SET below needs PostgreSQL 16+'; END $$;
\endif

BEGIN;

CREATE ROLE migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'migrator_password';
CREATE ROLE app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'app_password';

-- ALTER ... OWNER TO migrator needs SET on migrator. INHERIT keeps services that still
-- connect as the owner working until the relaunch gate revokes this membership.
GRANT migrator TO CURRENT_USER WITH SET TRUE, INHERIT TRUE;

GRANT USAGE, CREATE ON SCHEMA public TO migrator;
GRANT USAGE ON SCHEMA public TO app;

DO $$
DECLARE
  obj record;
BEGIN
  FOR obj IN
    SELECT c.oid::regclass AS name, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('ALTER %s %s OWNER TO migrator',
      CASE obj.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END,
      obj.name);
  END LOOP;

  -- Sequences owned by a column follow their table; only standalone ones move here.
  FOR obj IN
    SELECT c.oid::regclass AS name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'S'
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype IN ('a', 'i', 'e'))
  LOOP
    EXECUTE format('ALTER SEQUENCE %s OWNER TO migrator', obj.name);
  END LOOP;

  FOR obj IN
    SELECT p.oid::regprocedure AS name
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('ALTER ROUTINE %s OWNER TO migrator', obj.name);
  END LOOP;

  FOR obj IN
    SELECT t.oid::regtype AS name
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'public'
      AND t.typtype IN ('e', 'd')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('ALTER TYPE %s OWNER TO migrator', obj.name);
  END LOOP;
END $$;

SET ROLE migrator;

DO $$
DECLARE
  obj record;
BEGIN
  FOR obj IN
    SELECT c.oid::regclass AS name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm')
      AND c.relname <> 'alembic_version'
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO app', obj.name);
  END LOOP;

  FOR obj IN
    SELECT c.oid::regclass AS name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'S'
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO app', obj.name);
  END LOOP;
END $$;

ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO analyst;

RESET ROLE;

COMMIT;

\echo 'roles migrator and app created'
```

- [ ] **Step 5: Write `verify_roles.sql`**

`backend/db/roles/verify_roles.sql`:

```sql
-- Read-only checks. Exits non-zero (ON_ERROR_STOP) if any check fails.
\set ON_ERROR_STOP on

CREATE TEMP TABLE role_checks (check_name text PRIMARY KEY, ok boolean NOT NULL);

INSERT INTO role_checks
SELECT 'role exists: ' || r, EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r)
FROM unnest(ARRAY['analyst', 'migrator', 'app']) AS r;

INSERT INTO role_checks
SELECT 'no superuser/createdb/createrole: ' || rolname, NOT (rolsuper OR rolcreatedb OR rolcreaterole)
FROM pg_roles WHERE rolname IN ('analyst', 'migrator', 'app');

INSERT INTO role_checks
SELECT 'noinherit: ' || rolname, NOT rolinherit
FROM pg_roles WHERE rolname IN ('migrator', 'app');

INSERT INTO role_checks
SELECT 'no role memberships (pg_auth_members empty): ' || r,
       NOT EXISTS (
         SELECT 1 FROM pg_auth_members m JOIN pg_roles pr ON pr.oid = m.member WHERE pr.rolname = r)
FROM unnest(ARRAY['analyst', 'migrator', 'app']) AS r;

INSERT INTO role_checks VALUES
  ('migrator can CREATE in public', has_schema_privilege('migrator', 'public', 'CREATE')),
  ('app cannot CREATE in public', NOT has_schema_privilege('app', 'public', 'CREATE')),
  ('analyst cannot CREATE in public', NOT has_schema_privilege('analyst', 'public', 'CREATE')),
  ('app cannot CREATE schemas in the database', NOT has_database_privilege('app', current_database(), 'CREATE')),
  ('app cannot write alembic_version',
     CASE WHEN to_regclass('public.alembic_version') IS NULL THEN true
          ELSE NOT has_table_privilege('app', to_regclass('public.alembic_version'), 'UPDATE') END);

WITH app_tables AS (
  SELECT c.oid, c.oid::regclass::text AS name
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public'
    AND c.relkind IN ('r', 'p')
    AND c.relname <> 'alembic_version'
    AND NOT EXISTS (
      SELECT 1 FROM pg_depend d
      WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
)
INSERT INTO role_checks
SELECT 'migrator owns ' || name, pg_get_userbyid(c.relowner) = 'migrator'
  FROM app_tables t JOIN pg_class c ON c.oid = t.oid
UNION ALL
-- has_table_privilege with a list is true if ANY is held, so each privilege is separate.
SELECT 'app has DML on ' || name,
       has_table_privilege('app', oid, 'SELECT') AND has_table_privilege('app', oid, 'INSERT')
       AND has_table_privilege('app', oid, 'UPDATE') AND has_table_privilege('app', oid, 'DELETE')
  FROM app_tables
UNION ALL
SELECT 'app lacks TRUNCATE/REFERENCES/TRIGGER on ' || name,
       NOT has_table_privilege('app', oid, 'TRUNCATE') AND NOT has_table_privilege('app', oid, 'REFERENCES')
       AND NOT has_table_privilege('app', oid, 'TRIGGER')
  FROM app_tables
UNION ALL
SELECT 'analyst cannot INSERT into ' || name, NOT has_table_privilege('analyst', oid, 'INSERT')
  FROM app_tables;

SELECT check_name, ok FROM role_checks ORDER BY ok, check_name;

DO $$
DECLARE
  failed text;
BEGIN
  SELECT string_agg(check_name, '; ') INTO failed FROM role_checks WHERE NOT ok;
  IF failed IS NOT NULL THEN
    RAISE EXCEPTION 'ROLE CHECKS FAILED: %', failed;
  END IF;
END $$;

\echo 'ALL ROLE CHECKS PASSED'
```

- [ ] **Step 6: Run and confirm pass**

Run the Step 3 command, then the whole file:
`cd backend && MIGRATIONS_TEST_ADMIN_URL=postgresql://test_user:test_password@localhost:55432/postgres DATABASE_URL=postgresql+asyncpg://test_user:test_password@localhost:55432/postgres uv run pytest tests/test_migrations.py tests/test_write_role_url.py -v`
Expected: `10 passed` (5 migration + 5 URL-writer). In CI, the GitHub `ubuntu-latest` image ships PostgreSQL 16 client tools, so `psql` ≥ 15 is present and the role test runs there too.

- [ ] **Step 7: Add the new test file to CI (if the `backend` job lists files) and commit**

If the `backend` job's pytest step passes an explicit list, append `tests/test_no_runtime_ddl.py tests/test_write_role_url.py`.

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add backend/db/roles backend/scripts/write_role_url.py backend/tests/test_write_role_url.py backend/tests/test_migrations.py .github/workflows/ci.yml
git commit -m "feat(db): SQL-only least-privilege roles (migrator, app) with verification

Roles are created as the owner via psql with passwords read from the
environment; verify_roles.sql fails on any stray membership or privilege.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Docs drift for the schema, CHANGELOG, PR5 verification

**Files:**
- Modify: `data/DATABASE_STRUCTURE.md` (§4 heading ~line 107, Key Relationships ~168–171, Coordinate table ~181, query ~215)
- Modify: `README.md:120`, `DEPLOYMENT.md:119`
- Modify: `CHANGELOG.md`

**Interfaces:** none.

- [ ] **Step 1: Fix the stale table name and FK note**

In `data/DATABASE_STRUCTURE.md`:
- `### 4. weather_patterns` → `### 4. weather`
- In Key Relationships, `    └── weather_patterns (25K)` → `    └── weather (25K)`
- Replace the note under Key Relationships with:

```markdown
**Note:** MP routes and accidents are not linked by foreign keys; the safety algorithm finds relevant accidents by spatial proximity (PostGIS). Accidents do carry legacy FKs, `accidents.route_id → routes` and `accidents.mountain_id → mountains`. Phase 2a relinks them to `mp_routes`/`mp_locations` and drops the legacy tables. `ascents` and `climbers` were dropped by migration `0002_drop_ascents_climbers`.
```

- In the Coordinate Systems table, `| weather_patterns | 2 decimals |` → `| weather | 2 decimals |`
- In Weather Pattern Matching, `SELECT * FROM weather_patterns` → `SELECT * FROM weather`

In `README.md:120`, `| **weather_patterns** |` → `| **weather** |`. In `DEPLOYMENT.md:119`, `` | `weather_patterns` | `` → `` | `weather` | ``.

Run: `cd /Users/sebastianfrazier/Developer/SafeAscent && git grep -n "weather_patterns" -- '*.md' ':!docs/superpowers'`
Expected: no output.

- [ ] **Step 2: Add the CHANGELOG entry**

Insert directly under the `The format is based on …` line of `CHANGELOG.md` (newest first; use the actual commit date if it is not 2026-09-27):

```markdown
## [Phase 1 PR5] - 2026-09-27

### Added
- Alembic (`backend/alembic/`). `0001_baseline` replays a sanitized schema-only dump of prod; prod is stamped at it, never upgraded through it. Migrations run as an explicit step with `MIGRATOR_DATABASE_URL`, never on app startup.
- `0002_drop_ascents_climbers` drops the empty `ascents` and `climbers` tables, and aborts if either has rows.
- Least-privilege Postgres roles `migrator` (DDL) and `app` (DML), created only via `backend/db/roles/create_roles.sql` and checked by `verify_roles.sql`.
- Tests: fresh-DB `upgrade head` + `alembic check`, the 0002 guard, role scripts end to end, and a ban on DDL in app code.

### Removed
- `Ascent`/`Climber` models and the unused legacy `app/tasks/safety_computation.py`.
- Runtime `CREATE TABLE IF NOT EXISTS historical_predictions`, which fails under a role without `CREATE`.

### Fixed
- Docs called the weather table `weather_patterns`; the live table is `weather`.
```

- [ ] **Step 3: Full verification**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
uv run ruff check app/ scripts/ alembic/
MIGRATIONS_TEST_ADMIN_URL=postgresql://test_user:test_password@localhost:55432/postgres \
DATABASE_URL=postgresql+asyncpg://test_user:test_password@localhost:55432/postgres uv run pytest -q
docker rm -f sa-mig-pg
```

Expected: ruff `All checks passed!`; pytest shows no failures (skips only for tests that already skipped on `main`).

- [ ] **Step 4: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add data/DATABASE_STRUCTURE.md README.md DEPLOYMENT.md CHANGELOG.md
git commit -m "docs(db): weather table name, legacy FK note, PR5 changelog

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

PR5 is ready for the owner to review and `/commitandpush`. Task 8 runs only after PR5 is merged and deployed.

---

### Task 8: OWNER STEPS — create roles, rehearse on a Neon branch, stamp prod

All commands run in the owner's terminal from `/Users/sebastianfrazier/Developer/SafeAscent/backend` on an up-to-date `main` that includes PR5. Nothing here prints a password. Agents do not run these steps.

**Files (gitignored, never committed):** `backend/.env.owner`, `backend/.env.migrator`, `backend/.env.app`.

- [ ] **Step 1 (owner): Tools**

```bash
brew install libpq
export PATH="/opt/homebrew/opt/libpq/bin:$PATH"
psql --version
```

Expected: `psql (PostgreSQL) 15` or newer (`\getenv` needs 15+).

**Revised 2026-09-28 (owner decision, PR5 review):** every `psql` call below reads its password from `PGPASSWORD` instead of the connection URL, so no owner or migrator password ever appears in `ps`/argv output visible to other users on the box. Define this helper once per shell session (it runs inside the `( … )` subshells below, since a `bash` subshell inherits the parent's functions):

```bash
# Splits a postgresql:// URL into a password-less URL on stdout and the password into
# PGPASSWORD, so callers never pass a credential as a psql argv.
split_pg_url() {
  { read -r PG_URL_NOPASS; read -r PGPASSWORD; } < <(uv run python3 -c '
import sys
from urllib.parse import urlsplit, urlunsplit, quote, unquote
u = urlsplit(sys.argv[1])
# urlsplit does not decode; u.password is still percent-encoded, and PGPASSWORD must be
# the literal characters libpq expects, not the URL-escaped form.
username = quote(unquote(u.username), safe="") if u.username else None
netloc = (f"{username}@" if username else "") + (u.hostname or "") + (f":{u.port}" if u.port else "")
print(urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment)))
print(unquote(u.password) if u.password else "")
' "$1")
  export PGPASSWORD
}
```

- [ ] **Step 2 (owner): Put the owner URL in a file, using an editor, not the shell**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
umask 077
${EDITOR:-nano} .env.owner
```

File content (a single line, with the value from the Neon Console → Connect, owner role, *direct* not pooled):
`OWNER_DATABASE_URL=postgresql://neondb_owner:<password>@<host>/neondb?sslmode=require`

Run: `git check-ignore -v .env.owner .env.migrator .env.app`
Expected: three lines, each matched by `.gitignore:…:.env.*`.

- [ ] **Step 3 (owner): Generate passwords straight into files and write role URLs**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
umask 077
printf 'MIGRATOR_PASSWORD=%s\n' "$(openssl rand -hex 32)" > .env.migrator
printf 'APP_PASSWORD=%s\n' "$(openssl rand -hex 32)" > .env.app
( set -a; . ./.env.owner; . ./.env.migrator; set +a; uv run python -m scripts.write_role_url --role migrator --env-file .env.migrator )
( set -a; . ./.env.owner; . ./.env.app; set +a; uv run python -m scripts.write_role_url --role app --env-file .env.app )
```

Expected: `wrote MIGRATOR_DATABASE_URL to .env.migrator` and `wrote APP_DATABASE_URL to .env.app`. Both files are mode 0600.

`create_roles.sql` takes SCRAM-SHA-256 verifiers (`MIGRATOR_PASSWORD_SCRAM` / `APP_PASSWORD_SCRAM`), not passwords, so no plaintext reaches Neon's logs or `pg_stat_statements`. In Steps 4 and 5, each verifier is computed inside a `$( … )` subshell that is the only place the plaintext password is loaded. The outer shell exports only the verifiers and the owner URL.

- [ ] **Step 4 (owner): Rehearse on a Neon branch before touching prod**

In the Neon Console: Branches → New branch from `main` (name `p1-pr5-rehearsal`, current data). Copy the branch's **direct** host. Neon roles are per branch, so roles created here do not exist on `main`. Then:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
BRANCH_HOST='<paste branch host, e.g. ep-foo-123.us-east-2.aws.neon.tech>'
( set -a; . ./.env.owner; set +a
  BRANCH_URL="$(printf '%s' "$OWNER_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  split_pg_url "$BRANCH_URL"
  export MIGRATOR_PASSWORD_SCRAM="$( set -a; . ./.env.migrator; uv run python -m scripts.write_role_url --role migrator --scram )"
  export APP_PASSWORD_SCRAM="$( set -a; . ./.env.app; uv run python -m scripts.write_role_url --role app --scram )"
  psql "$PG_URL_NOPASS" -X -q -f db/roles/create_roles.sql
  psql "$PG_URL_NOPASS" -X -q -f db/roles/verify_roles.sql )
( set -a; . ./.env.migrator; set +a
  export MIGRATOR_DATABASE_URL="$(printf '%s' "$MIGRATOR_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  uv run alembic stamp 0001_baseline
  uv run alembic upgrade head
  uv run alembic current
  uv run alembic check )
```

Expected: `roles migrator and app created`, a table of checks all `t`, `ALL ROLE CHECKS PASSED`, then `0002_drop_ascents_climbers (head)` and `No new upgrade operations detected.` If `verify_roles.sql` fails on `analyst` (for example, the existing `analyst` role has a membership or `rolbypassrls`), paste the failing check names (no secrets) to the agent. This rehearsal is exactly where a pre-existing owner-level default grant to `analyst` (from before `migrator` existed — see the note after Step 5) surfaces; resolve it there, not on prod. Any failure stops the rollout. Error output contains no password. Delete the branch in the Console afterwards.

- [ ] **Step 5 (owner): Create the roles on prod and verify**

Only after Step 4 passes:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.owner; set +a
  split_pg_url "$OWNER_DATABASE_URL"
  export MIGRATOR_PASSWORD_SCRAM="$( set -a; . ./.env.migrator; uv run python -m scripts.write_role_url --role migrator --scram )"
  export APP_PASSWORD_SCRAM="$( set -a; . ./.env.app; uv run python -m scripts.write_role_url --role app --scram )"
  psql "$PG_URL_NOPASS" -X -q -f db/roles/create_roles.sql
  psql "$PG_URL_NOPASS" -X -q -f db/roles/verify_roles.sql )
```

Expected: `roles migrator and app created`, a table of checks all `t`, then `ALL ROLE CHECKS PASSED`. If `verify_roles.sql` fails because `pg_default_acl` shows `analyst` with a default grant beyond `SELECT` — a pre-existing owner-level `ALTER DEFAULT PRIVILEGES ... TO analyst` from when `analyst` was set up, before `migrator` owned anything — revoke it (substitute the actual granting role if `\ddp public.*` shows something other than `neondb_owner`):

```bash
( set -a; . ./.env.owner; set +a
  split_pg_url "$OWNER_DATABASE_URL"
  psql "$PG_URL_NOPASS" -X -q -c "ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public REVOKE ALL ON TABLES FROM analyst" )
```

Then re-run `verify_roles.sql` above.

- [ ] **Step 6 (owner): Stamp and upgrade prod, then lock `alembic_version`**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.migrator; set +a
  uv run alembic stamp 0001_baseline
  uv run alembic upgrade head
  uv run alembic current
  uv run alembic check
  # psql needs libpq spelling: postgresql:// and sslmode=, not asyncpg's +asyncpg and ssl=.
  # The value (verify-full) is spelled the same in both; only the key changes. Unlike
  # app.db.ssl (which loads certifi's bundle explicitly), libpq's verify-full has no
  # built-in OS-trust fallback — if psql errors with "root certificate file ... does
  # not exist", append &sslrootcert=system to $U (libpq 14+, uses the OS/OpenSSL trust
  # store; Neon's certs are publicly trusted so this should verify cleanly).
  U="${MIGRATOR_DATABASE_URL/postgresql+asyncpg:/postgresql:}"; U="${U/ssl=verify-full/sslmode=verify-full}"
  split_pg_url "$U"
  psql "$PG_URL_NOPASS" -X -q -c "REVOKE INSERT, UPDATE, DELETE ON public.alembic_version FROM app" )
```

Expected: `0002_drop_ascents_climbers (head)`, `No new upgrade operations detected.`, and the REVOKE returns silently.

- [ ] **Step 7 (owner): Verify prod state as `analyst` and re-run the role checks**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.analyst; set +a
  U="${ANALYST_DATABASE_URL/postgresql+asyncpg:/postgresql:}"; U="${U/ssl=require/sslmode=require}"
  split_pg_url "$U"
  psql "$PG_URL_NOPASS" -XAt -c "SELECT version_num FROM alembic_version" \
    -c "SELECT to_regclass('public.ascents'), to_regclass('public.climbers'), to_regclass('public.routes'), to_regclass('public.mountains')" \
    -c "SELECT conname FROM pg_constraint WHERE conrelid = 'public.accidents'::regclass AND contype = 'f' ORDER BY 1" )
( set -a; . ./.env.owner; set +a; split_pg_url "$OWNER_DATABASE_URL"; psql "$PG_URL_NOPASS" -X -q -f db/roles/verify_roles.sql )
```

Expected: `0002_drop_ascents_climbers`; `||routes|mountains` (the first two are NULL); FK list includes `accidents_route_id_fkey`; `ALL ROLE CHECKS PASSED`.

The services still connect as `neondb_owner` at this point. They keep working through the transitional `INHERIT TRUE` membership. Switching them to `app` is part of the relaunch gate (Task 26).

---

# PR6 — Celery split, liveness, alerts

### Task 9: healthchecks.io ping helper

**Files:**
- Create: `backend/app/healthchecks.py`
- Create: `backend/tests/test_healthchecks.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app.healthchecks.PingSuffix = Literal["", "/start", "/fail"]`; `app.healthchecks.ping(url: str | None, suffix: PingSuffix = "", *, transport: httpx.BaseTransport | None = None, timeout: float = 10.0) -> bool` (never raises, never logs the URL).

- [ ] **Step 1: Create the branch**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git switch main && git pull --ff-only
git switch -c feat/p1-pr6-celery-liveness
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_healthchecks.py`:

```python
import logging

import httpx

from app.healthchecks import ping

URL = "https://hc-ping.com/secret-uuid"


def _recording_transport(seen: list[str], status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(status)

    return httpx.MockTransport(handler)


def test_ping_appends_suffix():
    seen: list[str] = []
    assert ping(URL, "/start", transport=_recording_transport(seen)) is True
    assert seen == [f"{URL}/start"]


def test_success_ping_hits_bare_url():
    seen: list[str] = []
    assert ping(URL + "/", transport=_recording_transport(seen)) is True
    assert seen == [URL]


def test_unset_url_is_skipped_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="app.healthchecks"):
        assert ping(None, "/fail") is False
    assert "skipped" in caplog.text


def test_http_error_status_returns_false():
    assert ping(URL, transport=_recording_transport([], status=500)) is False


def test_network_error_is_swallowed_and_url_never_logged(caplog):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with caplog.at_level(logging.WARNING, logger="app.healthchecks"):
        assert ping(URL, "/fail", transport=httpx.MockTransport(boom)) is False
    assert "secret-uuid" not in caplog.text
    assert "ConnectError" in caplog.text
```

Run: `cd backend && uv run pytest tests/test_healthchecks.py -v`
Expected: `ModuleNotFoundError: No module named 'app.healthchecks'`.

- [ ] **Step 3: Implement**

`backend/app/healthchecks.py`:

```python
"""healthchecks.io dead-man's-switch pings. Alerting must never break the job it watches."""

from __future__ import annotations

import logging
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

PingSuffix = Literal["", "/start", "/fail"]


def ping(
    url: str | None,
    suffix: PingSuffix = "",
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 10.0,
) -> bool:
    if not url:
        logger.warning("healthchecks ping%s skipped: no ping URL configured", suffix or " (success)")
        return False
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            client.get(f"{url.rstrip('/')}{suffix}").raise_for_status()
    except httpx.HTTPError as exc:
        # The ping URL is the check's credential, so only the error type is logged.
        logger.warning("healthchecks ping%s failed: %s", suffix or " (success)", type(exc).__name__)
        return False
    return True
```

- [ ] **Step 4: Run and confirm pass**

Run: `cd backend && uv run pytest tests/test_healthchecks.py -v`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add backend/app/healthchecks.py backend/tests/test_healthchecks.py
git commit -m "feat(ops): healthchecks.io ping helper that never raises or logs the URL

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Worker heartbeat bootstep, expired-task accounting, health reader

**Files:**
- Create: `backend/app/celery_signals.py`
- Create: `backend/tests/test_celery_signals.py`
- Modify: `backend/pyproject.toml`, `backend/uv.lock` (dev dependency `fakeredis`)

**Interfaces:**
- Consumes: `app.config.settings.WORKER_HEARTBEAT_TTL_SECONDS: int`; `app.utils.cache.get_redis_client() -> redis.Redis | None`.
- Produces (all in `app.celery_signals`):
  - `HEARTBEAT_KEY = "celery:worker:heartbeat"`, `EXPIRED_KEY_PREFIX = "celery:expired:"`, `EXPIRED_WINDOW_DAYS = 7`
  - `class WorkerHealth(TypedDict)`: `status: Literal["ok", "down"]`, `last_heartbeat_age_seconds: float | None`, `expired_tasks_7d: dict[str, int]`
  - `heartbeat_interval_seconds(ttl_seconds: int) -> float`
  - `write_heartbeat(client: redis.Redis, ttl_seconds: int, now: float) -> None`
  - `record_expired(client: redis.Redis, task_name: str, today: date) -> None`
  - `read_worker_health(client: redis.Redis | None, now: float, today: date) -> WorkerHealth`
  - `on_task_revoked(sender=None, request=None, terminated=False, signum=None, expired=False, **_) -> None`
  - `class HeartbeatStep(celery.bootsteps.StartStopStep)`
  - `install(app: celery.Celery) -> None`

- [ ] **Step 1: Add fakeredis**

Run: `cd backend && uv add --dev fakeredis`
Expected: `pyproject.toml` gains `fakeredis` in the dev group; `uv.lock` updated; resolution keeps `redis==5.2.0`.

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_celery_signals.py`:

```python
import logging
from datetime import date
from types import SimpleNamespace

import fakeredis
import pytest

import app.celery_signals as signals
from app.celery_signals import (
    EXPIRED_KEY_PREFIX,
    HEARTBEAT_KEY,
    HeartbeatStep,
    heartbeat_interval_seconds,
    read_worker_health,
    record_expired,
    write_heartbeat,
)

TODAY = date(2026, 9, 27)


@pytest.fixture
def fake() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


def test_interval_is_a_third_of_ttl_with_floor():
    assert heartbeat_interval_seconds(120) == 40.0
    assert heartbeat_interval_seconds(1) == 1.0


def test_write_heartbeat_sets_value_and_ttl(fake):
    write_heartbeat(fake, ttl_seconds=120, now=1000.5)
    assert fake.get(HEARTBEAT_KEY) == "1000.500"
    assert 0 < fake.ttl(HEARTBEAT_KEY) <= 120


def test_health_ok_with_fresh_heartbeat(fake):
    write_heartbeat(fake, ttl_seconds=120, now=1000.0)
    health = read_worker_health(fake, now=1012.34, today=TODAY)
    assert health == {"status": "ok", "last_heartbeat_age_seconds": 12.3, "expired_tasks_7d": {}}


def test_health_down_without_heartbeat(fake):
    assert read_worker_health(fake, now=1.0, today=TODAY)["status"] == "down"


def test_health_down_without_redis():
    assert read_worker_health(None, now=1.0, today=TODAY) == {
        "status": "down",
        "last_heartbeat_age_seconds": None,
        "expired_tasks_7d": {},
    }


def test_expired_counts_cover_last_seven_days_only(fake):
    task = "app.tasks.safety_computation_optimized.compute_daily_safety_scores_optimized"
    record_expired(fake, task, TODAY)
    record_expired(fake, task, TODAY)
    record_expired(fake, task, date(2026, 9, 21))
    record_expired(fake, task, date(2026, 9, 20))
    fake.set(f"{EXPIRED_KEY_PREFIX}garbage", "5")
    assert read_worker_health(fake, now=0.0, today=TODAY)["expired_tasks_7d"] == {task: 3}
    assert fake.ttl(f"{EXPIRED_KEY_PREFIX}{task}:{TODAY.isoformat()}") > 7 * 86400


def test_revoked_expired_task_logs_error_and_counts(fake, monkeypatch, caplog):
    monkeypatch.setattr(signals, "get_redis_client", lambda: fake)
    sender = SimpleNamespace(name="app.tasks.ops.beat_heartbeat")
    with caplog.at_level(logging.ERROR, logger="app.celery_signals"):
        signals.on_task_revoked(sender=sender, request=SimpleNamespace(id="abc"), expired=True)
    assert "app.tasks.ops.beat_heartbeat" in caplog.text and "abc" in caplog.text
    assert read_worker_health(fake, now=0.0, today=signals._utc_today())["expired_tasks_7d"] == {
        "app.tasks.ops.beat_heartbeat": 1
    }


def test_revoked_non_expired_task_logs_but_does_not_count(fake, monkeypatch, caplog):
    monkeypatch.setattr(signals, "get_redis_client", lambda: fake)
    with caplog.at_level(logging.ERROR, logger="app.celery_signals"):
        signals.on_task_revoked(sender=SimpleNamespace(name="t"), request=None, expired=False)
    assert "expired=False" in caplog.text
    assert list(fake.scan_iter(match=f"{EXPIRED_KEY_PREFIX}*")) == []


class _FakeTref:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _FakeTimer:
    def __init__(self) -> None:
        self.calls: list[tuple[float, object]] = []
        self.tref = _FakeTref()

    def call_repeatedly(self, secs: float, fun: object, *args: object, **kwargs: object) -> _FakeTref:
        self.calls.append((secs, fun))
        return self.tref


def test_heartbeat_step_writes_immediately_schedules_and_cancels(fake, monkeypatch):
    monkeypatch.setattr(signals, "get_redis_client", lambda: fake)
    parent = SimpleNamespace(timer=_FakeTimer())
    step = HeartbeatStep(parent)
    step.start(parent)
    assert fake.get(HEARTBEAT_KEY) is not None
    assert parent.timer.calls[0][0] == heartbeat_interval_seconds(signals.settings.WORKER_HEARTBEAT_TTL_SECONDS)
    step.stop(parent)
    assert parent.timer.tref.cancelled


def test_heartbeat_step_survives_missing_redis(monkeypatch, caplog):
    monkeypatch.setattr(signals, "get_redis_client", lambda: None)
    parent = SimpleNamespace(timer=_FakeTimer())
    with caplog.at_level(logging.ERROR, logger="app.celery_signals"):
        HeartbeatStep(parent).start(parent)
    assert "heartbeat" in caplog.text
```

Run: `cd backend && uv run pytest tests/test_celery_signals.py -v`
Expected: `ModuleNotFoundError: No module named 'app.celery_signals'`.

- [ ] **Step 3: Implement**

`backend/app/celery_signals.py`:

```python
"""Celery worker liveness (Redis heartbeat) and expired-task accounting.

The 2026-08 outage: the worker's consumer hung while embedded beat kept publishing,
and every nightly message expired and was discarded below ERROR. The heartbeat runs on
the worker's own timer, so it stops when the consumer loop stops. The beat-scheduled
healthchecks ping (app.tasks.ops) covers hangs where the loop is alive but not consuming.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, TypedDict

import redis
from celery import Celery, bootsteps
from celery.signals import task_revoked

from app.config import settings
from app.utils.cache import get_redis_client

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "celery:worker:heartbeat"
EXPIRED_KEY_PREFIX = "celery:expired:"
EXPIRED_WINDOW_DAYS = 7
# One spare day so the oldest bucket in the window still exists when it is read.
EXPIRED_KEY_TTL_SECONDS = (EXPIRED_WINDOW_DAYS + 1) * 24 * 60 * 60


class WorkerHealth(TypedDict):
    status: Literal["ok", "down"]
    last_heartbeat_age_seconds: float | None
    expired_tasks_7d: dict[str, int]


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def heartbeat_interval_seconds(ttl_seconds: int) -> float:
    # Three writes per TTL window, so one missed write does not flip /health/worker to 503.
    return max(ttl_seconds / 3, 1.0)


def write_heartbeat(client: redis.Redis, ttl_seconds: int, now: float) -> None:
    client.set(HEARTBEAT_KEY, f"{now:.3f}", ex=ttl_seconds)


def record_expired(client: redis.Redis, task_name: str, today: date) -> None:
    key = f"{EXPIRED_KEY_PREFIX}{task_name}:{today.isoformat()}"
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, EXPIRED_KEY_TTL_SECONDS)
    pipe.execute()


def _expired_counts(client: redis.Redis, today: date) -> dict[str, int]:
    window_start = today - timedelta(days=EXPIRED_WINDOW_DAYS - 1)
    counts: dict[str, int] = {}
    for raw_key in client.scan_iter(match=f"{EXPIRED_KEY_PREFIX}*"):
        key = str(raw_key)
        task_name, _, day = key[len(EXPIRED_KEY_PREFIX):].rpartition(":")
        try:
            bucket = date.fromisoformat(day)
        except ValueError:
            continue
        if not task_name or bucket < window_start or bucket > today:
            continue
        counts[task_name] = counts.get(task_name, 0) + int(client.get(key) or 0)
    return counts


def read_worker_health(client: redis.Redis | None, now: float, today: date) -> WorkerHealth:
    down: WorkerHealth = {"status": "down", "last_heartbeat_age_seconds": None, "expired_tasks_7d": {}}
    if client is None:
        return down
    try:
        raw = client.get(HEARTBEAT_KEY)
        expired = _expired_counts(client, today)
    except redis.RedisError:
        logger.exception("worker health read failed")
        return down
    if raw is None:
        return {"status": "down", "last_heartbeat_age_seconds": None, "expired_tasks_7d": expired}
    return {
        "status": "ok",
        "last_heartbeat_age_seconds": round(now - float(raw), 1),
        "expired_tasks_7d": expired,
    }


def on_task_revoked(
    sender: Any = None,
    request: Any = None,
    terminated: bool = False,
    signum: Any = None,
    expired: bool = False,
    **_: Any,
) -> None:
    task_name = getattr(sender, "name", None) or "unknown"
    logger.error(
        "celery task revoked: name=%s id=%s expired=%s terminated=%s",
        task_name,
        getattr(request, "id", None),
        expired,
        terminated,
    )
    if not expired:
        return
    client = get_redis_client()
    if client is None:
        return
    try:
        record_expired(client, task_name, _utc_today())
    except redis.RedisError:
        logger.exception("could not count expired task %s", task_name)


class HeartbeatStep(bootsteps.StartStopStep):
    requires = {"celery.worker.components:Timer"}

    def __init__(self, parent: Any, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        self._tref: Any = None

    def start(self, parent: Any) -> None:
        self._beat()
        self._tref = parent.timer.call_repeatedly(
            heartbeat_interval_seconds(settings.WORKER_HEARTBEAT_TTL_SECONDS), self._beat
        )

    def stop(self, parent: Any) -> None:
        if self._tref is not None:
            self._tref.cancel()
            self._tref = None

    def _beat(self) -> None:
        client = get_redis_client()
        if client is None:
            logger.error("worker heartbeat skipped: Redis unavailable")
            return
        try:
            write_heartbeat(client, settings.WORKER_HEARTBEAT_TTL_SECONDS, time.time())
        except redis.RedisError:
            logger.exception("worker heartbeat write failed")


def install(app: Celery) -> None:
    app.steps["worker"].add(HeartbeatStep)
    task_revoked.connect(on_task_revoked, weak=False)
```

- [ ] **Step 4: Run and confirm pass**

Run: `cd backend && uv run pytest tests/test_celery_signals.py -v`
Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add backend/app/celery_signals.py backend/tests/test_celery_signals.py backend/pyproject.toml backend/uv.lock
git commit -m "feat(celery): worker heartbeat bootstep and expired-task accounting

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Wire Celery — install signals, beat heartbeat task, nightly pings

**Files:**
- Create: `backend/app/tasks/ops.py`
- Create: `backend/tests/test_celery_ops.py`
- Modify: `backend/app/celery_app.py`
- Modify: `backend/app/tasks/safety_computation_optimized.py` (imports; task body ~lines 908–948 on Phase 0)

**Interfaces:**
- Consumes: `app.healthchecks.ping` (Task 9); `app.celery_signals.install`, `HeartbeatStep` (Task 10); `settings.HEALTHCHECKS_NIGHTLY_URL`, `settings.HEALTHCHECKS_BEAT_URL`.
- Produces: task `"app.tasks.ops.beat_heartbeat"` (returns `bool`); beat entry `"beat-heartbeat"` (every 900s, `expires` 600); nightly task pings `/start`, success, `/fail`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_celery_ops.py`:

```python
import tomllib
from pathlib import Path

import pytest

import app.tasks.ops as ops
import app.tasks.safety_computation_optimized as nightly
from app.celery_app import celery_app
from app.celery_signals import HeartbeatStep

BACKEND = Path(__file__).resolve().parents[1]
NIGHTLY_URL = "https://hc-ping.example/nightly"


def test_beat_schedule_has_fifteen_minute_heartbeat():
    entry = celery_app.conf.beat_schedule["beat-heartbeat"]
    assert entry["task"] == "app.tasks.ops.beat_heartbeat"
    assert entry["schedule"] == 900.0
    assert entry["options"] == {"expires": 600}


def test_nightly_schedule_unchanged():
    entry = celery_app.conf.beat_schedule["compute-daily-safety-scores"]
    assert entry["task"] == nightly.OPTIMIZED_TASK_NAME


def test_ops_module_included_and_worker_hardened():
    assert "app.tasks.ops" in celery_app.conf.include
    assert celery_app.conf.worker_cancel_long_running_tasks_on_connection_loss is True
    assert HeartbeatStep in celery_app.steps["worker"]


def test_beat_heartbeat_pings_beat_check(monkeypatch):
    calls: list[tuple[str | None, str]] = []
    monkeypatch.setattr(ops, "ping", lambda url, suffix="": calls.append((url, suffix)) or True)
    monkeypatch.setattr(ops.settings, "HEALTHCHECKS_BEAT_URL", "https://hc-ping.example/beat")
    assert ops.beat_heartbeat() is True
    assert calls == [("https://hc-ping.example/beat", "")]


@pytest.fixture
def nightly_pings(monkeypatch) -> list[tuple[str | None, str]]:
    calls: list[tuple[str | None, str]] = []
    monkeypatch.setattr(nightly, "ping", lambda url, suffix="": calls.append((url, suffix)) or True)
    monkeypatch.setattr(nightly.settings, "HEALTHCHECKS_NIGHTLY_URL", NIGHTLY_URL)
    monkeypatch.setattr(nightly, "_acquire_population_lock", lambda task_id: (True, "token"))
    monkeypatch.setattr(nightly, "_release_population_lock", lambda token: None)
    return calls


def test_nightly_success_pings_start_then_success(nightly_pings, monkeypatch):
    async def ok() -> dict[str, str]:
        return {"status": "completed"}

    monkeypatch.setattr(nightly, "_compute_all_dates_async", ok)
    assert nightly.compute_daily_safety_scores_optimized() == {"status": "completed"}
    assert nightly_pings == [(NIGHTLY_URL, "/start"), (NIGHTLY_URL, "")]


def test_nightly_failure_pings_fail_and_reraises(nightly_pings, monkeypatch):
    async def broken() -> dict[str, str]:
        raise RuntimeError("Failed dates during optimized cache computation: 2026-09-27")

    monkeypatch.setattr(nightly, "_compute_all_dates_async", broken)
    with pytest.raises(RuntimeError):
        nightly.compute_daily_safety_scores_optimized()
    assert nightly_pings == [(NIGHTLY_URL, "/start"), (NIGHTLY_URL, "/fail")]


def test_nightly_skipped_run_sends_no_pings(nightly_pings, monkeypatch):
    monkeypatch.setattr(nightly, "_acquire_population_lock", lambda task_id: (False, None))
    assert nightly.compute_daily_safety_scores_optimized()["status"] == "skipped"
    assert nightly_pings == []


def test_worker_service_does_not_embed_beat():
    deploy = tomllib.loads((BACKEND / "railway-worker.toml").read_text())["deploy"]
    command = deploy["startCommand"].split()
    assert command[:4] == ["celery", "-A", "app.celery_app", "worker"]
    assert "--beat" not in command and "-B" not in command
    assert "-E" in command and "--concurrency=2" in command


def test_beat_service_is_single_replica():
    deploy = tomllib.loads((BACKEND / "railway-beat.toml").read_text())["deploy"]
    assert deploy["numReplicas"] == 1
    assert deploy["startCommand"].split()[:4] == ["celery", "-A", "app.celery_app", "beat"]
```

Run: `cd backend && uv run pytest tests/test_celery_ops.py -v`
Expected: `ModuleNotFoundError: No module named 'app.tasks.ops'`.

- [ ] **Step 2: Create the ops task**

`backend/app/tasks/ops.py`:

```python
"""Operational tasks. beat_heartbeat runs on the worker, so its ping proves beat
scheduled it *and* the worker consumed it within 15 minutes."""

from app.celery_app import celery_app
from app.config import settings
from app.healthchecks import ping


@celery_app.task(name="app.tasks.ops.beat_heartbeat")
def beat_heartbeat() -> bool:
    return ping(settings.HEALTHCHECKS_BEAT_URL)
```

- [ ] **Step 3: Wire `celery_app.py`**

In `backend/app/celery_app.py`:

Replace the `include=[...]` list with:

```python
    include=[
        "app.tasks.safety_computation_optimized",  # Active location-level task
        "app.tasks.ops",
    ],
```

In `celery_app.conf.update(...)`, after `task_reject_on_worker_lost=True,` add:

```python
    # A lost broker connection cancels in-flight work so acks_late redelivers it,
    # instead of the task finishing against a dead channel.
    worker_cancel_long_running_tasks_on_connection_loss=True,
```

In `celery_app.conf.beat_schedule`, after the `"compute-daily-safety-scores"` entry add:

```python
    # Dead-man's switch for the consumer: an alert fires within 75 min (15 min period +
    # 60 min grace on healthchecks.io) instead of at the next nightly run.
    "beat-heartbeat": {
        "task": "app.tasks.ops.beat_heartbeat",
        "schedule": 900.0,
        "options": {"expires": 600},
    },
```

At the end of the file add:

```python
from app.celery_signals import install as install_ops_signals  # noqa: E402

install_ops_signals(celery_app)
```

- [ ] **Step 4: Wrap the nightly task with pings**

In `backend/app/tasks/safety_computation_optimized.py`, add to the imports:

```python
from app.config import settings
from app.healthchecks import ping
```

In `compute_daily_safety_scores_optimized`, replace the block from `    try:` (right after the lock-skipped `return`) through the end of the `finally:` with:

```python
    try:
        ping(settings.HEALTHCHECKS_NIGHTLY_URL, "/start")
        logger.warning("Creating event loop...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.warning("Event loop created, starting async computation...")
        try:
            result = loop.run_until_complete(_compute_all_dates_async())
        finally:
            loop.close()
            logger.warning("Event loop closed")

        logger.info("=" * 60)
        logger.info(f"OPTIMIZED COMPUTATION COMPLETE: {result}")
        logger.info("=" * 60)
        ping(settings.HEALTHCHECKS_NIGHTLY_URL)
        return result
    except Exception as e:
        logger.error(f"Optimized computation failed: {e}", exc_info=True)
        ping(settings.HEALTHCHECKS_NIGHTLY_URL, "/fail")
        raise
    finally:
        _release_population_lock(lock_token)
```

A run skipped by the lock sends no pings: the run that holds the lock owns the check.

- [ ] **Step 5: Update the Railway worker config and add the beat config**

`backend/railway-worker.toml` (full content):

```toml
# Railway: Celery worker. Beat runs as its own service (railway-beat.toml); embedding it
# here let beat keep publishing while the consumer was hung (2026-08 outage).

[build]
builder = "dockerfile"
dockerfilePath = "Dockerfile"

[deploy]
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 10
numReplicas = 1
startCommand = "celery -A app.celery_app worker --loglevel=info --concurrency=2 -E"
```

`backend/railway-beat.toml`:

```toml
# Railway: Celery beat. Exactly one replica; a second would double-schedule every task.

[build]
builder = "dockerfile"
dockerfilePath = "Dockerfile"

[deploy]
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 10
numReplicas = 1
startCommand = "celery -A app.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule"
```

Delete the superseded scripts:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent && git rm backend/run_celery_beat.sh backend/run_celery_worker.sh
git grep -n "run_celery_" || echo "no references"
grep -n "\-\-beat" docker-compose.yml || echo "compose worker has no --beat"
```

Expected: `no references` and `compose worker has no --beat`. If the second grep finds `--beat`, remove it from the compose `worker` command; Part A's compose defines a separate `beat` service.

- [ ] **Step 6: Run and confirm pass**

Run: `cd backend && uv run pytest tests/test_celery_ops.py tests/test_celery_signals.py tests/test_healthchecks.py -v && uv run ruff check app/`
Expected: `24 passed` (9 ops + 10 signals + 5 healthchecks); ruff `All checks passed!`.

- [ ] **Step 7: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add backend/app/tasks/ops.py backend/app/celery_app.py backend/app/tasks/safety_computation_optimized.py backend/railway-worker.toml backend/railway-beat.toml backend/tests/test_celery_ops.py docker-compose.yml
git commit -m "feat(celery): split beat from worker; dead-man pings for nightly and beat

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: `GET /health/worker`

**Files:**
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_worker_health_route.py`

**Interfaces:**
- Consumes: `app.celery_signals.read_worker_health`, `HEARTBEAT_KEY`; `app.utils.cache.get_redis_client`.
- Produces: `GET /health/worker` → 200 `{"status":"ok",...}` or 503 `{"status":"down",...}` (body is `WorkerHealth`).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_worker_health_route.py`:

```python
import time

import fakeredis
from fastapi.testclient import TestClient

from app.celery_signals import HEARTBEAT_KEY


def _client(monkeypatch, redis_client):
    import app.main as main

    monkeypatch.setattr(main, "get_redis_client", lambda: redis_client)
    return TestClient(main.app)


def test_worker_health_200_with_fresh_heartbeat(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    fake.set(HEARTBEAT_KEY, f"{time.time():.3f}", ex=120)
    response = _client(monkeypatch, fake).get("/health/worker")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["last_heartbeat_age_seconds"] < 5
    assert body["expired_tasks_7d"] == {}


def test_worker_health_503_without_heartbeat(monkeypatch):
    response = _client(monkeypatch, fakeredis.FakeRedis(decode_responses=True)).get("/health/worker")
    assert response.status_code == 503
    assert response.json()["status"] == "down"


def test_worker_health_503_without_redis(monkeypatch):
    response = _client(monkeypatch, None).get("/health/worker")
    assert response.status_code == 503
```

Run: `cd backend && uv run pytest tests/test_worker_health_route.py -v`
Expected: FAIL with `AttributeError: <module 'app.main'> has no attribute 'get_redis_client'` (monkeypatch requires the attribute).

- [ ] **Step 2: Implement the route**

In `backend/app/main.py`:

Change `from fastapi import FastAPI` to `from fastapi import FastAPI, Response` and add imports after `from app.config import settings`:

```python
import time
from datetime import datetime, timezone

from app.celery_signals import WorkerHealth, read_worker_health
from app.utils.cache import get_redis_client
```

After the `/health` root endpoint add:

```python
@app.get("/health/worker")
def worker_health(response: Response) -> WorkerHealth:
    """503 when no worker heartbeat is in Redis; reports 7-day expired-task counts."""
    health = read_worker_health(
        get_redis_client(), now=time.time(), today=datetime.now(timezone.utc).date()
    )
    if health["status"] != "ok":
        response.status_code = 503
    return health
```

- [ ] **Step 3: Run and confirm pass**

Run: `cd backend && uv run pytest tests/test_worker_health_route.py tests/test_config_and_admin_routes.py -v`
Expected: all pass (3 new + the existing Phase 0/Part A config tests).

- [ ] **Step 4: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add backend/app/main.py backend/tests/test_worker_health_route.py
git commit -m "feat(api): /health/worker reports heartbeat and expired-task counts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: PR6 CI wiring, CHANGELOG, verification

**Files:**
- Modify: `.github/workflows/ci.yml` (job `backend`)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: CI**

If the `backend` job's pytest step lists files explicitly, append:
`tests/test_healthchecks.py tests/test_celery_signals.py tests/test_celery_ops.py tests/test_worker_health_route.py`.
Do not set `HEALTHCHECKS_*` in CI; unset URLs skip pings, and the tests patch them.

- [ ] **Step 2: CHANGELOG**

Insert above the PR5 entry:

```markdown
## [Phase 1 PR6] - 2026-09-27

### Changed
- Celery beat now runs as its own Railway service (`backend/railway-beat.toml`, one replica). The worker no longer embeds `--beat`, and runs with `-E`. Root cause of the 2026-08-27 → 09 silent failure: the worker's consumer hung while the embedded beat kept publishing, and every nightly message expired (`expires=28800`) and was discarded without an ERROR.
- `worker_cancel_long_running_tasks_on_connection_loss=True`.

### Added
- Worker heartbeat bootstep writes `celery:worker:heartbeat` to Redis with TTL `WORKER_HEARTBEAT_TTL_SECONDS`. `GET /health/worker` returns 503 when the key is missing and reports 7-day expired-task counts.
- Revoked and expired tasks are logged at ERROR. Expired ones are counted in `celery:expired:<task>:<date>`.
- healthchecks.io dead-man pings: the nightly task pings start/success/fail (`HEALTHCHECKS_NIGHTLY_URL`), and a beat-scheduled `beat_heartbeat` pings every 15 min (`HEALTHCHECKS_BEAT_URL`). When a URL is unset, pings are skipped with a WARNING.

### Removed
- `backend/run_celery_worker.sh`, `backend/run_celery_beat.sh`.
```

- [ ] **Step 3: Verify and commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend && uv run ruff check app/ && uv run pytest -q
cd .. && git add .github/workflows/ci.yml CHANGELOG.md
git commit -m "chore(ci): run PR6 tests; changelog

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: ruff clean; pytest with 0 failed.

PR6 is ready for review and `/commitandpush`. Task 14 follows the merge.

---

### Task 14: OWNER STEPS — Railway beat service, healthchecks.io, drills

- [ ] **Step 1 (owner): healthchecks.io checks** (dashboard at healthchecks.io)
  - Check `safeascent-nightly`: Simple schedule, **Period 1 day, Grace 4 hours**. Copy its ping URL.
  - Check `safeascent-beat`: **Period 15 minutes, Grace 60 minutes**. Copy its ping URL.
  - Integrations: email (and phone push if wanted) to the owner.

- [ ] **Step 2 (owner): Railway variables** (dashboard; not the CLI, so the URLs stay out of shell history). On the `worker` service set `HEALTHCHECKS_NIGHTLY_URL` and `HEALTHCHECKS_BEAT_URL` to the two ping URLs. The tasks run on the worker; `beat` and `api` do not need them.

- [ ] **Step 3 (owner): Create the `beat` service.** Railway → New → GitHub repo (same repo) → name `beat`; Settings → Root Directory `backend`, Config-as-code path `/backend/railway-beat.toml`, Wait for CI on, branch `main`. Copy the worker's variables (`DATABASE_URL`, `REDIS_URL`/`CELERY_*`, `ENVIRONMENT`) to `beat`. Confirm the `worker` service's config path is `/backend/railway-worker.toml`, and that after deploy its start command has no `--beat`.

- [ ] **Step 4 (owner): Confirm liveness**

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://api.safeascent.us/health/worker
curl -s https://api.safeascent.us/health/worker
```

Expected: `200` and `"status":"ok"`, with `last_heartbeat_age_seconds` under 40. The beat check on healthchecks.io turns green within 15 min.

- [ ] **Step 5 (owner): Staging drills (spec)**
  1. Stop the `worker` service in Railway. Within ~2 min, `/health/worker` returns `503`. Within 75 min, healthchecks.io emails that `safeascent-beat` is down. Restart the worker; both recover.
  2. Pause the worker (stop it), then send a short-lived task from a local shell pointed at the prod broker. `CELERY_BROKER_URL` comes from the Railway dashboard, pasted into a gitignored `backend/.env.broker`:
     ```bash
     cd /Users/sebastianfrazier/Developer/SafeAscent/backend
     ( set -a; . ./.env.broker; set +a
       DATABASE_URL=postgresql+asyncpg://unused@localhost/unused uv run python -c \
       "from app.celery_app import celery_app; celery_app.send_task('app.tasks.ops.beat_heartbeat', expires=1)" )
     ```
     Wait 5 s, start the worker. Expected: the worker log shows an ERROR `celery task revoked: name=app.tasks.ops.beat_heartbeat ... expired=True`, and `/health/worker` shows `"expired_tasks_7d": {"app.tasks.ops.beat_heartbeat": 1}`.
  3. Observe one full nightly run: `safeascent-nightly` shows a start ping around 02:00 UTC and a success ping afterwards. This is relaunch-gate item 3.

---

# PR7 — TypeScript (incremental) + the Risk 0.0 fix

### Task 15: TypeScript toolchain

**Files:**
- Create: `frontend/tsconfig.json`, `frontend/src/vite-env.d.ts`
- Modify: `frontend/package.json`, `frontend/package-lock.json`, `frontend/eslint.config.js`, `frontend/vite.config.js`
- Modify: `.github/workflows/ci.yml` (job `frontend`)

**Interfaces:**
- Produces: `npm run typecheck` (`tsc --noEmit`); typed `import.meta.env.VITE_API_BASE_URL?: string`, `VITE_MAPBOX_TOKEN?: string`.

- [ ] **Step 1: Branch and install**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git switch main && git pull --ff-only
git switch -c feat/p1-pr7-ts-risk-fix
cd frontend && npm install --save-dev typescript typescript-eslint
```

Expected: both land in `devDependencies`; `package-lock.json` updated; `npm audit --omit=dev --audit-level=high` still clean (dev-only additions).

- [ ] **Step 2: Write the configs**

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "allowJs": true,
    "checkJs": false,
    "strict": true,
    "noEmit": true,
    "isolatedModules": true,
    "resolveJsonModule": true,
    "skipLibCheck": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"]
}
```

`frontend/src/vite-env.d.ts`:

```ts
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_MAPBOX_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
```

In `frontend/package.json` `scripts`, add after `"lint"`:

```json
    "typecheck": "tsc --noEmit",
```

`frontend/eslint.config.js` (full content):

```js
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

const testGlobals = {
  ...globals.browser,
  vi: 'readonly',
  describe: 'readonly',
  it: 'readonly',
  expect: 'readonly',
  beforeEach: 'readonly',
  afterEach: 'readonly',
  beforeAll: 'readonly',
  afterAll: 'readonly',
  global: 'readonly',
}

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]', argsIgnorePattern: '^_' }],
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
  {
    files: ['**/*.test.{js,jsx,ts,tsx}', '**/test/**/*.{js,jsx,ts,tsx}'],
    languageOptions: { globals: testGlobals },
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
])
```

In `frontend/vite.config.js`, change the coverage `include` to:

```js
      include: ['src/**/*.{js,jsx,ts,tsx}'],
```

- [ ] **Step 3: Verify the toolchain**

Run: `cd frontend && npm run typecheck && npm run lint && npm run test:run`
Expected: `tsc` exits 0 with no output (no `.ts` files yet besides `vite-env.d.ts`); lint clean; existing Vitest suite passes.

- [ ] **Step 4: Add the CI step**

In `.github/workflows/ci.yml`, job `frontend`, add after the lint step:

```yaml
      - name: Type check
        working-directory: frontend
        run: npm run typecheck
```

- [ ] **Step 5: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add frontend/tsconfig.json frontend/src/vite-env.d.ts frontend/package.json frontend/package-lock.json frontend/eslint.config.js frontend/vite.config.js .github/workflows/ci.yml
git commit -m "chore(frontend): incremental TypeScript (allowJs, strict .ts) with CI typecheck

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Convert `riskUtils` and the API client to TypeScript

**Files:**
- Delete: `frontend/src/utils/riskUtils.js`, `frontend/src/services/api.js`
- Create: `frontend/src/utils/riskUtils.ts`, `frontend/src/utils/riskUtils.test.ts`
- Create: `frontend/src/services/api.ts`, `frontend/src/services/api.test.ts`

**Interfaces:**
- Produces:
  - `riskUtils.ts`: `type RiskLevel = 'low'|'moderate'|'high'|'extreme'`; `isRiskScore(value: unknown): value is number`; `getRiskLevel(riskScore: number): RiskLevel`; `getRiskColor(riskScore: number): string`; `getRiskDescription(riskScore: number): string`; `interface ConfidenceInfo { level: string; description: string; color: string }`; `getConfidenceInfo(confidence: number): ConfidenceInfo`; `formatRiskScore(riskScore: number | null | undefined): string` (`"NN/100"` or `"Unavailable"`); `formatConfidence(confidence: number): string`; `getMarkerColor(riskScore: number): string`.
  - `api.ts`: `type RiskColorCode = 'green'|'yellow'|'orange'|'red'`; `interface SafetyResponse { route_id: number; route_name: string; target_date: string; risk_score: number; color_code: RiskColorCode }`; `interface PredictionParams`; `interface ContributingAccident`; `interface PredictionResponse`; `isSafetyResponse(value: unknown): value is SafetyResponse`; `fetchRouteSafety(routeId: number, targetDate: string): Promise<SafetyResponse>`; `predictRouteSafety(params: PredictionParams): Promise<PredictionResponse>`; `fetchNearbyAccidents(latitude: number, longitude: number, radiusKm?: number): Promise<unknown[]>`; `healthCheck(): Promise<boolean>`; `default api` (AxiosInstance).

- [ ] **Step 1: Write the failing tests**

`frontend/src/utils/riskUtils.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { formatRiskScore, getRiskLevel, isRiskScore } from './riskUtils';

describe('isRiskScore', () => {
  it('accepts finite numbers including 0', () => {
    expect(isRiskScore(0)).toBe(true);
    expect(isRiskScore(42.3)).toBe(true);
  });

  it('rejects missing and non-numeric values', () => {
    for (const value of [undefined, null, NaN, Infinity, '42', {}]) {
      expect(isRiskScore(value)).toBe(false);
    }
  });
});

describe('formatRiskScore', () => {
  it('formats a real score, including zero', () => {
    expect(formatRiskScore(35.5)).toBe('36/100');
    expect(formatRiskScore(0)).toBe('0/100');
  });

  it('never invents a number for a missing score', () => {
    expect(formatRiskScore(undefined)).toBe('Unavailable');
    expect(formatRiskScore(null)).toBe('Unavailable');
    expect(formatRiskScore(NaN)).toBe('Unavailable');
  });
});

describe('getRiskLevel', () => {
  it('uses the documented bands', () => {
    expect(getRiskLevel(24.9)).toBe('low');
    expect(getRiskLevel(25)).toBe('moderate');
    expect(getRiskLevel(50)).toBe('high');
    expect(getRiskLevel(75)).toBe('extreme');
  });
});
```

`frontend/src/services/api.test.ts`:

```ts
import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import api, { fetchRouteSafety, isSafetyResponse } from './api';

const OK = {
  route_id: 42,
  route_name: 'Test Route',
  target_date: '2026-09-27',
  risk_score: 42.3,
  color_code: 'yellow',
};

afterEach(() => vi.restoreAllMocks());

describe('isSafetyResponse', () => {
  it('accepts a well-formed payload', () => {
    expect(isSafetyResponse(OK)).toBe(true);
  });

  it('rejects a payload without a finite risk_score', () => {
    expect(isSafetyResponse({ ...OK, risk_score: null })).toBe(false);
    expect(isSafetyResponse({ error: 'boom' })).toBe(false);
  });
});

describe('fetchRouteSafety', () => {
  it('posts to the safety endpoint and returns the validated body', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: OK } as never);
    await expect(fetchRouteSafety(42, '2026-09-27')).resolves.toEqual(OK);
    expect(post).toHaveBeenCalledWith('/mp-routes/42/safety', null, {
      params: { target_date: '2026-09-27', bypass_cache: true },
    });
  });

  it('rejects a malformed body instead of returning it', async () => {
    vi.spyOn(api, 'post').mockResolvedValue({ data: { ...OK, risk_score: undefined } } as never);
    await expect(fetchRouteSafety(42, '2026-09-27')).rejects.toThrow('Malformed safety response');
  });

  it('turns a network failure into a readable error', async () => {
    const error = new AxiosError('Network Error', 'ERR_NETWORK', { headers: new AxiosHeaders() });
    vi.spyOn(api, 'post').mockRejectedValue(error);
    await expect(fetchRouteSafety(42, '2026-09-27')).rejects.toThrow('Cannot connect to SafeAscent API');
  });
});
```

Run: `cd frontend && npx vitest run src/utils/riskUtils.test.ts src/services/api.test.ts`
Expected: FAIL. `isRiskScore`, `isSafetyResponse`, and `fetchRouteSafety` are not exported (the `.js` modules lack them).

- [ ] **Step 2: Replace `riskUtils.js` with `riskUtils.ts`**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent && git mv frontend/src/utils/riskUtils.js frontend/src/utils/riskUtils.ts
```

`frontend/src/utils/riskUtils.ts` (full content):

```ts
/**
 * Risk interpretation utilities. A missing score is never coerced to a number:
 * callers check isRiskScore and show "Unavailable" instead.
 */

export type RiskLevel = 'low' | 'moderate' | 'high' | 'extreme';

export interface ConfidenceInfo {
  level: string;
  description: string;
  color: string;
}

export const isRiskScore = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

export const getRiskLevel = (riskScore: number): RiskLevel => {
  if (riskScore < 25) return 'low';
  if (riskScore < 50) return 'moderate';
  if (riskScore < 75) return 'high';
  return 'extreme';
};

const RISK_BG_CLASS: Record<RiskLevel, string> = {
  low: 'bg-risk-low',
  moderate: 'bg-risk-moderate',
  high: 'bg-risk-high',
  extreme: 'bg-risk-extreme',
};

export const getRiskColor = (riskScore: number): string => RISK_BG_CLASS[getRiskLevel(riskScore)];

const RISK_DESCRIPTIONS: Record<RiskLevel, string> = {
  low: 'Low Risk - Favorable conditions based on historical data',
  moderate: 'Moderate Risk - Exercise caution and prepare accordingly',
  high: 'High Risk - Significant hazards present, reconsider route',
  extreme: 'Extreme Risk - Dangerous conditions, strongly advise against',
};

export const getRiskDescription = (riskScore: number): string =>
  RISK_DESCRIPTIONS[getRiskLevel(riskScore)];

export const getConfidenceInfo = (confidence: number): ConfidenceInfo => {
  if (confidence >= 75) {
    return {
      level: 'High',
      description: 'Prediction based on substantial accident data in this region',
      color: 'text-green-600',
    };
  }
  if (confidence >= 50) {
    return {
      level: 'Medium',
      description: 'Moderate amount of accident data available for this area',
      color: 'text-yellow-600',
    };
  }
  if (confidence >= 25) {
    return {
      level: 'Low',
      description: 'Limited accident data in this region - use caution',
      color: 'text-orange-600',
    };
  }
  return {
    level: 'Very Low',
    description: 'Very limited data - prediction may be unreliable',
    color: 'text-red-600',
  };
};

export const formatRiskScore = (riskScore: number | null | undefined): string =>
  isRiskScore(riskScore) ? `${Math.round(riskScore)}/100` : 'Unavailable';

export const formatConfidence = (confidence: number): string => `${Math.round(confidence)}%`;

const MARKER_COLORS: Record<RiskLevel, string> = {
  low: '#10b981',
  moderate: '#f59e0b',
  high: '#ef4444',
  extreme: '#7c2d12',
};

export const getMarkerColor = (riskScore: number): string => MARKER_COLORS[getRiskLevel(riskScore)];
```

- [ ] **Step 3: Replace `api.js` with `api.ts`**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent && git mv frontend/src/services/api.js frontend/src/services/api.ts
```

`frontend/src/services/api.ts` (full content):

```ts
/**
 * API client for the SafeAscent backend. Responses that drive a risk number are
 * validated here, so a malformed body becomes an error, never a default score.
 */
import axios, { type AxiosInstance } from 'axios';

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

api.interceptors.request.use(
  (config) => {
    if (import.meta.env.DEV) {
      console.log('🚀 API Request:', config.method?.toUpperCase(), config.url, config.data);
    }
    return config;
  },
  (error: unknown) => {
    console.error('❌ API Request Error:', error);
    return Promise.reject(error);
  },
);

api.interceptors.response.use(
  (response) => {
    if (import.meta.env.DEV) {
      console.log('✅ API Response:', response.config.url, response.data);
    }
    return response;
  },
  (error: unknown) => {
    const detail = axios.isAxiosError(error) ? error.response?.data ?? error.message : error;
    console.error('❌ API Response Error:', detail);
    return Promise.reject(error);
  },
);

export type RiskColorCode = 'green' | 'yellow' | 'orange' | 'red';

export interface SafetyResponse {
  route_id: number;
  route_name: string;
  target_date: string;
  risk_score: number;
  color_code: RiskColorCode;
}

export interface PredictionParams {
  latitude: number;
  longitude: number;
  route_type: string;
  planned_date: string;
  elevation_meters?: number;
  search_radius_km?: number;
  route_grade?: string;
}

export interface ContributingAccident {
  accident_id: number;
  total_influence: number;
  distance_km: number;
  days_ago: number;
  spatial_weight: number;
  temporal_weight: number;
  elevation_weight: number;
  weather_weight: number;
  route_type_weight: number;
  severity_weight: number;
  grade_weight?: number;
}

export interface PredictionResponse {
  risk_score: number;
  num_contributing_accidents: number;
  top_contributing_accidents: ContributingAccident[];
  metadata: Record<string, unknown>;
}

const COLOR_CODES: readonly string[] = ['green', 'yellow', 'orange', 'red'];

export const isSafetyResponse = (value: unknown): value is SafetyResponse => {
  if (typeof value !== 'object' || value === null) return false;
  const body = value as Record<string, unknown>;
  return (
    typeof body.route_id === 'number' &&
    typeof body.route_name === 'string' &&
    typeof body.target_date === 'string' &&
    typeof body.risk_score === 'number' &&
    Number.isFinite(body.risk_score) &&
    typeof body.color_code === 'string' &&
    COLOR_CODES.includes(body.color_code)
  );
};

const toReadableError = (error: unknown): Error => {
  if (axios.isAxiosError(error)) {
    if (error.code === 'ECONNABORTED') {
      return new Error('Request timed out. The server may be slow or unavailable.');
    }
    if (!error.response) {
      return new Error('Cannot connect to SafeAscent API. Please check your connection.');
    }
    return new Error(`SafeAscent API error (HTTP ${error.response.status}).`);
  }
  return error instanceof Error ? error : new Error('Unexpected error.');
};

export const fetchRouteSafety = async (routeId: number, targetDate: string): Promise<SafetyResponse> => {
  let data: unknown;
  try {
    const response = await api.post(`/mp-routes/${routeId}/safety`, null, {
      params: { target_date: targetDate, bypass_cache: true },
    });
    data = response.data;
  } catch (error) {
    throw toReadableError(error);
  }
  if (!isSafetyResponse(data)) {
    throw new Error('Malformed safety response from the API.');
  }
  return data;
};

export const predictRouteSafety = async (params: PredictionParams): Promise<PredictionResponse> => {
  try {
    const response = await api.post<PredictionResponse>('/predict', params);
    return response.data;
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 422) {
      throw new Error('Invalid prediction parameters. Please check your input.');
    }
    throw toReadableError(error);
  }
};

export const fetchNearbyAccidents = async (
  latitude: number,
  longitude: number,
  radiusKm = 50,
): Promise<unknown[]> => {
  try {
    const response = await api.get<unknown[]>('/accidents', {
      params: { latitude, longitude, radius_km: radiusKm, limit: 100 },
    });
    return response.data;
  } catch (error) {
    console.error('Failed to fetch nearby accidents:', error);
    return [];
  }
};

export const healthCheck = async (): Promise<boolean> => {
  try {
    const response = await api.get('/health');
    return response.status === 200;
  } catch {
    return false;
  }
};

export default api;
```

`predictRouteSafety` used to rethrow the raw AxiosError for non-422 HTTP errors. It now throws a readable `Error`. `App.jsx` only reads `err.message`, so behaviour there is unchanged apart from the wording.

- [ ] **Step 4: Run and confirm pass**

Run: `cd frontend && npx vitest run src/utils/riskUtils.test.ts src/services/api.test.ts && npm run typecheck && npm run lint`
Expected: `10 passed`; typecheck and lint clean.

- [ ] **Step 5: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add frontend/src/utils frontend/src/services
git commit -m "refactor(frontend): typed API client and risk utils with response validation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: `useRouteSafety` hook with a typed state union

**Files:**
- Create: `frontend/src/hooks/useRouteSafety.ts`
- Create: `frontend/src/hooks/useRouteSafety.test.ts`

**Interfaces:**
- Consumes: `fetchRouteSafety`, `SafetyResponse` (Task 16).
- Produces: `type SafetyState = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ok'; data: SafetyResponse }`; `useRouteSafety(routeId: number | null, targetDate: string): { state: SafetyState | null; retry: () => void }` (`state` is `null` when `routeId` is `null`).

- [ ] **Step 1: Write the failing test**

`frontend/src/hooks/useRouteSafety.test.ts`:

```ts
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchRouteSafety, type SafetyResponse } from '../services/api';
import { useRouteSafety } from './useRouteSafety';

vi.mock('../services/api', () => ({ fetchRouteSafety: vi.fn() }));

const OK: SafetyResponse = {
  route_id: 42,
  route_name: 'Test Route',
  target_date: '2026-09-27',
  risk_score: 42.3,
  color_code: 'yellow',
};

beforeEach(() => vi.mocked(fetchRouteSafety).mockReset());

describe('useRouteSafety', () => {
  it('is null when no route is selected and does not fetch', () => {
    const { result } = renderHook(() => useRouteSafety(null, '2026-09-27'));
    expect(result.current.state).toBeNull();
    expect(fetchRouteSafety).not.toHaveBeenCalled();
  });

  it('goes loading → ok', async () => {
    vi.mocked(fetchRouteSafety).mockResolvedValue(OK);
    const { result } = renderHook(() => useRouteSafety(42, '2026-09-27'));
    expect(result.current.state).toEqual({ status: 'loading' });
    await waitFor(() => expect(result.current.state).toEqual({ status: 'ok', data: OK }));
    expect(fetchRouteSafety).toHaveBeenCalledWith(42, '2026-09-27');
  });

  it('goes loading → error with the message, and retry refetches', async () => {
    vi.mocked(fetchRouteSafety).mockRejectedValueOnce(new Error('Network down')).mockResolvedValueOnce(OK);
    const { result } = renderHook(() => useRouteSafety(42, '2026-09-27'));
    await waitFor(() => expect(result.current.state).toEqual({ status: 'error', message: 'Network down' }));
    act(() => result.current.retry());
    expect(result.current.state).toEqual({ status: 'loading' });
    await waitFor(() => expect(result.current.state).toEqual({ status: 'ok', data: OK }));
    expect(fetchRouteSafety).toHaveBeenCalledTimes(2);
  });

  it('ignores a stale response after the route changes', async () => {
    let resolveFirst: (value: SafetyResponse) => void = () => {};
    vi.mocked(fetchRouteSafety)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({ ...OK, route_id: 7, risk_score: 10 });
    const { result, rerender } = renderHook(({ id }) => useRouteSafety(id, '2026-09-27'), {
      initialProps: { id: 42 },
    });
    rerender({ id: 7 });
    await waitFor(() => expect(result.current.state).toMatchObject({ status: 'ok', data: { route_id: 7 } }));
    act(() => resolveFirst(OK));
    expect(result.current.state).toMatchObject({ status: 'ok', data: { route_id: 7 } });
  });
});
```

Run: `cd frontend && npx vitest run src/hooks/useRouteSafety.test.ts`
Expected: FAIL: `Failed to resolve import "./useRouteSafety"`.

- [ ] **Step 2: Implement**

`frontend/src/hooks/useRouteSafety.ts`:

```ts
import { useCallback, useEffect, useState } from 'react';
import { fetchRouteSafety, type SafetyResponse } from '../services/api';

export type SafetyState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ok'; data: SafetyResponse };

interface Settled {
  key: string;
  state: SafetyState;
}

export function useRouteSafety(
  routeId: number | null,
  targetDate: string,
): { state: SafetyState | null; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [settled, setSettled] = useState<Settled | null>(null);
  const key = routeId === null ? null : `${routeId}|${targetDate}|${attempt}`;

  useEffect(() => {
    if (routeId === null) return;
    const requestKey = `${routeId}|${targetDate}|${attempt}`;
    let ignore = false;
    fetchRouteSafety(routeId, targetDate).then(
      (data) => {
        if (!ignore) setSettled({ key: requestKey, state: { status: 'ok', data } });
      },
      (error: unknown) => {
        if (!ignore) {
          const message = error instanceof Error ? error.message : 'Unknown error';
          setSettled({ key: requestKey, state: { status: 'error', message } });
        }
      },
    );
    return () => {
      ignore = true;
    };
  }, [routeId, targetDate, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  // Loading is derived from "no settled result for this request key" rather than set
  // synchronously in the effect, so a previous route's score can never flash.
  const state: SafetyState | null =
    key === null ? null : settled?.key === key ? settled.state : { status: 'loading' };
  return { state, retry };
}
```

- [ ] **Step 3: Run and confirm pass**

Run: `cd frontend && npx vitest run src/hooks/useRouteSafety.test.ts && npm run typecheck && npm run lint`
Expected: `4 passed`; typecheck and lint clean.

- [ ] **Step 4: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add frontend/src/hooks
git commit -m "feat(frontend): useRouteSafety hook with typed loading/error/ok state

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Route modal + MapView: error Alert with Retry, no numeric fallback

**Files:**
- Create: `frontend/src/components/RouteAnalyticsModal.safety.test.jsx`
- Modify: `frontend/src/components/RouteAnalyticsModal.jsx` (`formatRiskScore` ~391–395; component signature ~412; `formattedRouteRiskScore` ~427; CSV ~624; header Chip ~681–694; DialogContent error area ~757–761; `RiskBreakdownTab` ~1470–1480, ~1492, ~1550, ~1583)
- Modify: `frontend/src/components/MapView.jsx` (imports ~18; state ~58–59; fetch effect ~340–386; modal props ~1245–1263)

**Interfaces:**
- Consumes: `useRouteSafety`, `SafetyState` (Task 17).
- Produces: `RouteAnalyticsModal` props `{ open, onClose, routeData, selectedDate, safety: SafetyState | null, onRetrySafety: () => void }`. `routeData.risk_score` and `routeData.color_code` are `number | null` and `string | null`.

- [ ] **Step 1: Write the failing test (the spec's Vitest case)**

`frontend/src/components/RouteAnalyticsModal.safety.test.jsx`:

```jsx
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../test/utils';
import RouteAnalyticsModal from './RouteAnalyticsModal';
import { useRouteSafety } from '../hooks/useRouteSafety';
import { fetchRouteSafety } from '../services/api';

vi.mock('../services/api', () => ({ fetchRouteSafety: vi.fn() }));

function Harness() {
  const { state, retry } = useRouteSafety(42, '2026-09-27');
  const ok = state?.status === 'ok' ? state.data : null;
  return (
    <RouteAnalyticsModal
      open
      onClose={() => {}}
      selectedDate="2026-09-27"
      routeData={{
        route_id: 42,
        name: 'Test Route',
        mountain_name: 'Test Crag',
        type: 'Ice',
        grade: 'WI3',
        latitude: 44.1,
        longitude: -73.9,
        elevation_meters: null,
        risk_score: ok ? ok.risk_score : null,
        color_code: ok ? ok.color_code : null,
        mp_route_id: 42,
      }}
      safety={state}
      onRetrySafety={retry}
    />
  );
}

beforeEach(() => {
  // Tab data requests stay pending so only the safety state drives what renders.
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
  vi.mocked(fetchRouteSafety).mockReset();
});

afterEach(() => vi.unstubAllGlobals());

describe('RouteAnalyticsModal risk display', () => {
  it('shows an error Alert with Retry and never a numeric risk when the safety fetch rejects', async () => {
    vi.mocked(fetchRouteSafety).mockRejectedValueOnce(new Error('Cannot connect to SafeAscent API.'));
    render(<Harness />);

    expect(await screen.findByText(/couldn.t load the risk score/i)).toBeInTheDocument();
    expect(screen.getByText(/Cannot connect to SafeAscent API\./)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
    expect(screen.getByText('Risk: Unavailable')).toBeInTheDocument();
    expect(screen.queryByText(/0\.0/)).toBeNull();
    expect(screen.queryByText(/Risk:\s*\d/)).toBeNull();
  });

  it('Retry refetches and then shows the real score', async () => {
    vi.mocked(fetchRouteSafety)
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({
        route_id: 42,
        route_name: 'Test Route',
        target_date: '2026-09-27',
        risk_score: 42.3,
        color_code: 'yellow',
      });
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(await screen.findByRole('button', { name: /retry/i }));
    expect(await screen.findByText('Risk: 42.3/100')).toBeInTheDocument();
    expect(fetchRouteSafety).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/couldn.t load the risk score/i)).toBeNull();
  });

  it('shows a loading label, not a number, while the score is in flight', () => {
    vi.mocked(fetchRouteSafety).mockReturnValueOnce(new Promise(() => {}));
    render(<Harness />);
    expect(screen.getByText('Risk: loading…')).toBeInTheDocument();
    expect(screen.queryByText(/Risk:\s*\d/)).toBeNull();
  });
});
```

Run: `cd frontend && npx vitest run src/components/RouteAnalyticsModal.safety.test.jsx`
Expected: FAIL. The first test cannot find `/couldn.t load the risk score/i` (the chip shows `Risk: 0.0/100` today). The third cannot find `Risk: loading…`.

- [ ] **Step 2: Edit `RouteAnalyticsModal.jsx`**

Replace `formatRiskScore`:

```jsx
function formatRiskScore(score) {
  const numericScore = Number(score);
  if (!Number.isFinite(numericScore)) return '0.0';
  return numericScore.toFixed(1);
}
```

with

```jsx
// Returns null for a missing score; callers render "Unavailable", never a stand-in number.
function formatRiskScore(score) {
  if (typeof score !== 'number' || !Number.isFinite(score)) return null;
  return score.toFixed(1);
}

function colorCodeToBg(colorCode) {
  if (colorCode === 'green') return 'success.main';
  if (colorCode === 'yellow') return 'warning.main';
  if (colorCode === 'orange') return 'warning.dark';
  return 'error.main';
}

function riskChip(safety) {
  if (!safety || safety.status === 'loading') return { label: 'Risk: loading…', bgcolor: 'grey.600' };
  if (safety.status === 'error') return { label: 'Risk: Unavailable', bgcolor: 'grey.600' };
  const formatted = formatRiskScore(safety.data.risk_score);
  if (formatted === null) return { label: 'Risk: Unavailable', bgcolor: 'grey.600' };
  return { label: `Risk: ${formatted}/100`, bgcolor: colorCodeToBg(safety.data.color_code) };
}
```

Change the component signature:

```jsx
export default function RouteAnalyticsModal({ open, onClose, routeData, selectedDate }) {
```

to

```jsx
export default function RouteAnalyticsModal({ open, onClose, routeData, selectedDate, safety, onRetrySafety }) {
```

Replace `  const formattedRouteRiskScore = formatRiskScore(routeData?.risk_score);` with:

```jsx
  const chip = riskChip(safety);
```

In `exportAsCSV`, replace `    csv += \`Risk Score,${routeData.risk_score}\n\`;` with:

```jsx
    csv += `Risk Score,${formatRiskScore(routeData.risk_score) ?? 'Unavailable'}\n`;
```

Replace the header `<Chip ... />` block:

```jsx
          <Chip
            label={`Risk: ${formattedRouteRiskScore}/100`}
            sx={{
              bgcolor: routeData.color_code === 'green' ? 'success.main' :
                       routeData.color_code === 'yellow' ? 'warning.main' :
                       routeData.color_code === 'orange' ? 'warning.dark' : 'error.main',
              color: 'white',
              fontWeight: 600,
              fontSize: '1rem',
              mr: 2,
            }}
          />
```

with

```jsx
          <Chip
            label={chip.label}
            sx={{
              bgcolor: chip.bgcolor,
              color: 'white',
              fontWeight: 600,
              fontSize: '1rem',
              mr: 2,
            }}
          />
```

In `<DialogContent ...>`, directly before `{error && (`, add:

```jsx
        {safety?.status === 'error' && (
          <Alert
            severity="error"
            sx={{ mb: 2 }}
            action={
              <Button color="inherit" size="small" onClick={onRetrySafety}>
                Retry
              </Button>
            }
          >
            Couldn&apos;t load the risk score for this route. {safety.message}
          </Alert>
        )}
```

In `RiskBreakdownTab`, replace:

```jsx
  const effectiveRiskScore = data.risk_score ?? routeData.risk_score ?? 0;
```

with

```jsx
  const effectiveRiskScore = formatRiskScore(data.risk_score) !== null
    ? data.risk_score
    : formatRiskScore(routeData.risk_score) !== null ? routeData.risk_score : null;
```

and

```jsx
    scorePoints: Number(((effectiveRiskScore * factor.contribution) / 100).toFixed(1)),
```

with

```jsx
    scorePoints: effectiveRiskScore === null
      ? null
      : Number(((effectiveRiskScore * factor.contribution) / 100).toFixed(1)),
```

and

```jsx
              📊 Risk Score: {formatRiskScore(effectiveRiskScore)}/100
```

with

```jsx
              📊 Risk Score: {effectiveRiskScore === null ? 'Unavailable' : `${formatRiskScore(effectiveRiskScore)}/100`}
```

and the tooltip line

```jsx
                    `${value}% (~${item?.payload?.scorePoints ?? 0} pts)`,
```

with

```jsx
                    item?.payload?.scorePoints == null ? `${value}%` : `${value}% (~${item.payload.scorePoints} pts)`,
```

and the points chip label

```jsx
                              label={`+${((effectiveRiskScore * factor.contribution) / 100).toFixed(1)} pts`}
```

with

```jsx
                              label={effectiveRiskScore === null
                                ? 'pts unavailable'
                                : `+${((effectiveRiskScore * factor.contribution) / 100).toFixed(1)} pts`}
```

Then confirm no numeric fallback remains on a risk field:

Run: `cd frontend && grep -nE "risk[_a-zA-Z]*[^\n]*(\|\||\?\?) *0\b" src/components/RouteAnalyticsModal.jsx || echo "none"`
Expected: `none`.

- [ ] **Step 3: Edit `MapView.jsx`**

Add after `import RouteAnalyticsModal from './RouteAnalyticsModal';`:

```jsx
import { useRouteSafety } from '../hooks/useRouteSafety';
```

Replace:

```jsx
  const [safetyData, setSafetyData] = useState(null);
  const [_loadingSafety, setLoadingSafety] = useState(false);
```

with

```jsx
  const { state: safetyState, retry: retrySafety } = useRouteSafety(
    selectedRoute?.properties?.id ?? null,
    format(selectedDate, 'yyyy-MM-dd'),
  );
```

`selectedDate` is declared above this point (line ~48), so the hook can read it.

Delete the whole block from the doc comment `  /**\n   * Fetch safety score for selected route\n   */` through its closing `  }, [selectedRoute, selectedDate]);` (the effect that calls `fetch(.../safety?...)` and sets `{ error: err.message }`).

Replace the modal's `routeData` prop block:

```jsx
        routeData={selectedRoute && safetyData ? {
          route_id: selectedRoute.properties.id,
          name: selectedRoute.properties.name,
          mountain_name: selectedRoute.properties.mountain_name || 'Unknown Mountain',
          type: selectedRoute.properties.type,
          grade: selectedRoute.properties.grade,
          latitude: selectedRoute.geometry.coordinates[1],
          longitude: selectedRoute.geometry.coordinates[0],
          elevation_meters: null,
          risk_score: safetyData.risk_score || 0,
          color_code: safetyData.color_code || 'gray',
          mp_route_id: selectedRoute.properties.mp_route_id,
        } : null}
        selectedDate={format(selectedDate, 'yyyy-MM-dd')}
```

with

```jsx
        routeData={selectedRoute ? {
          route_id: selectedRoute.properties.id,
          name: selectedRoute.properties.name,
          mountain_name: selectedRoute.properties.mountain_name || 'Unknown Mountain',
          type: selectedRoute.properties.type,
          grade: selectedRoute.properties.grade,
          latitude: selectedRoute.geometry.coordinates[1],
          longitude: selectedRoute.geometry.coordinates[0],
          elevation_meters: null,
          risk_score: safetyState?.status === 'ok' ? safetyState.data.risk_score : null,
          color_code: safetyState?.status === 'ok' ? safetyState.data.color_code : null,
          mp_route_id: selectedRoute.properties.mp_route_id,
        } : null}
        selectedDate={format(selectedDate, 'yyyy-MM-dd')}
        safety={safetyState}
        onRetrySafety={retrySafety}
```

Run: `cd frontend && grep -nE "safetyData|setLoadingSafety|risk_score: safetyData" src/components/MapView.jsx || echo "none"`
Expected: `none`.

- [ ] **Step 4: Run and confirm pass**

Run: `cd frontend && npx vitest run src/components/RouteAnalyticsModal.safety.test.jsx && npm run test:run && npm run lint && npm run typecheck && npm run build`
Expected: `3 passed`; the full suite passes; lint, typecheck, and build clean (`VITE_*` unset is fine for a local build).

- [ ] **Step 5: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add frontend/src/components/RouteAnalyticsModal.jsx frontend/src/components/MapView.jsx frontend/src/components/RouteAnalyticsModal.safety.test.jsx
git commit -m "fix(frontend): failed safety fetch shows an error with Retry, never Risk 0.0

MapView passed safetyData.risk_score || 0 to the modal, so {error} rendered
as 0.0. The modal now renders from a loading/error/ok state.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 19: PredictionResult error state + App retry, CHANGELOG

**Files:**
- Modify: `frontend/src/components/PredictionResult.jsx` (full rewrite below)
- Modify: `frontend/src/components/PredictionResult.test.jsx`
- Modify: `frontend/src/App.jsx` (state ~33–36; submit handler ~41–56; error display in the sidebar)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `isRiskScore`, `getRiskLevel`, `getRiskDescription` (Task 16).
- Produces: `PredictionResult` props `{ prediction, onReset, error?: string | null, onRetry?: () => void }`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/PredictionResult.test.jsx`, change the first import line to:

```jsx
import { describe, it, expect, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
```

and append inside `describe('PredictionResult', () => { ... })`:

```jsx
  it('renders an error Alert with Retry and no score when the request failed', async () => {
    const onRetry = vi.fn();
    render(<PredictionResult prediction={null} error="Cannot connect to SafeAscent API." onRetry={onRetry} />);

    expect(screen.getByRole('alert')).toHaveTextContent('Cannot connect to SafeAscent API.');
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
    expect(screen.queryByText(/^\d+$/)).toBeNull();
    expect(screen.queryByText(/RISK$/)).toBeNull();
  });

  it('shows Unavailable instead of a number when risk_score is missing', () => {
    render(<PredictionResult prediction={{ ...mockPrediction, risk_score: undefined }} />);

    expect(screen.getByText('Unavailable')).toBeInTheDocument();
    expect(screen.queryByText(/^\d+$/)).toBeNull();
    expect(screen.queryByText(/RISK$/)).toBeNull();
  });
```

Run: `cd frontend && npx vitest run src/components/PredictionResult.test.jsx`
Expected: the two new tests FAIL (no alert rendered for `prediction={null}`; `NaN` rendered instead of `Unavailable`); the five existing tests pass.

- [ ] **Step 2: Rewrite `PredictionResult.jsx`**

`frontend/src/components/PredictionResult.jsx` (full content):

```jsx
/**
 * PredictionResult Component - Material Design
 *
 * Displays the safety prediction, or the request error with a Retry action.
 */
import {
  Alert,
  AlertTitle,
  Card,
  CardContent,
  Typography,
  Box,
  Paper,
  Chip,
  Button,
  Divider,
  Stack,
} from '@mui/material';
import {
  Warning as WarningIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Print as PrintIcon,
  Refresh as RefreshIcon,
} from '@mui/icons-material';
import {
  getRiskLevel,
  getRiskDescription,
  isRiskScore,
} from '../utils/riskUtils';

const RISK_CHIP_COLOR = { low: 'success', moderate: 'warning', high: 'error', extreme: 'error' };

function RiskIcon({ level }) {
  if (level === 'low') return <CheckCircleIcon sx={{ fontSize: 40 }} />;
  if (level === 'moderate') return <WarningIcon sx={{ fontSize: 40 }} />;
  return <ErrorIcon sx={{ fontSize: 40 }} />;
}

/**
 * @param {Object|null} prediction - Prediction result from API
 * @param {Function} onReset - Start a new prediction
 * @param {string|null} [error] - Request error message; takes precedence over prediction
 * @param {Function} [onRetry] - Re-send the last request
 */
export default function PredictionResult({ prediction, onReset, error, onRetry }) {
  if (error) {
    return (
      <Alert
        severity="error"
        sx={{ mt: 3, mb: 3 }}
        action={onRetry ? (
          <Button color="inherit" size="small" onClick={onRetry}>
            Retry
          </Button>
        ) : undefined}
      >
        <AlertTitle>Prediction failed</AlertTitle>
        {error}
      </Alert>
    );
  }

  if (!prediction) return null;

  const riskScore = isRiskScore(prediction.risk_score) ? prediction.risk_score : null;
  const riskLevel = riskScore === null ? null : getRiskLevel(riskScore);

  return (
    <Card elevation={3}>
      <CardContent>
        <Typography variant="h5" component="h2" gutterBottom fontWeight={500} textAlign="center">
          Route Safety Prediction
        </Typography>

        <Box sx={{ textAlign: 'center', my: 4 }}>
          {riskLevel && (
            <Box sx={{ color: `${RISK_CHIP_COLOR[riskLevel]}.main`, mb: 2 }}>
              <RiskIcon level={riskLevel} />
            </Box>
          )}

          <Typography variant="h2" component="div" fontWeight={700} gutterBottom>
            {riskScore === null ? 'Unavailable' : Math.round(riskScore)}
          </Typography>
          {riskScore !== null && (
            <Typography variant="subtitle1" color="text.secondary" gutterBottom>
              out of 100
            </Typography>
          )}

          {riskLevel && (
            <>
              <Chip
                label={`${riskLevel.toUpperCase()} RISK`}
                color={RISK_CHIP_COLOR[riskLevel]}
                sx={{
                  mt: 2,
                  px: 2,
                  py: 1,
                  fontSize: '1rem',
                  fontWeight: 600,
                }}
              />
              <Typography variant="body2" color="text.secondary" sx={{ mt: 2, maxWidth: 400, mx: 'auto' }}>
                {getRiskDescription(riskScore)}
              </Typography>
            </>
          )}
        </Box>

        <Divider sx={{ my: 3 }} />

        {prediction.top_contributing_accidents && prediction.top_contributing_accidents.length > 0 && (
          <Box sx={{ mt: 3 }}>
            <Typography variant="subtitle1" fontWeight={500} gutterBottom>
              Top Contributing Factors
            </Typography>
            <Stack spacing={1} sx={{ mt: 1.5 }}>
              {prediction.top_contributing_accidents.slice(0, 3).map((accident, idx) => (
                <Paper
                  key={accident.accident_id}
                  elevation={0}
                  sx={{
                    p: 1.5,
                    border: 1,
                    borderColor: 'grey.300',
                    borderRadius: 2,
                  }}
                >
                  <Stack direction="row" justifyContent="space-between" alignItems="flex-start" sx={{ mb: 0.5 }}>
                    <Typography variant="body2" fontWeight={500} color="text.primary">
                      Accident #{idx + 1}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {accident.distance_km.toFixed(1)} km away
                    </Typography>
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    {accident.days_ago} days ago • Influence: {(accident.total_influence * 100).toFixed(1)}%
                  </Typography>
                </Paper>
              ))}
            </Stack>
          </Box>
        )}

        {prediction.metadata && (
          <Paper elevation={0} sx={{ p: 2, mt: 3, bgcolor: 'grey.50' }}>
            <Typography variant="caption" fontWeight={500} color="text.secondary" display="block" gutterBottom>
              Prediction Details
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block">
              Route type: {prediction.metadata.route_type || 'N/A'}
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block">
              Search date: {prediction.metadata.search_date || 'N/A'}
            </Typography>
            {prediction.metadata.vectorized && (
              <Typography variant="caption" color="primary.main" display="block" sx={{ mt: 0.5 }}>
                ⚡ Optimized computation
              </Typography>
            )}
          </Paper>
        )}

        <Stack direction="row" spacing={2} sx={{ mt: 3 }}>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={onReset}
            fullWidth
          >
            New Prediction
          </Button>
          <Button
            variant="contained"
            startIcon={<PrintIcon />}
            onClick={() => window.print()}
            fullWidth
          >
            Print Report
          </Button>
        </Stack>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Wire retry in `App.jsx`**

After `  const [error, setError] = useState(null);` add:

```jsx
  const [lastParams, setLastParams] = useState(null);
```

In `handlePredictionSubmit`, after `    setPrediction(null);` add:

```jsx
    setLastParams(params);
```

After `handleReset`, add:

```jsx
  const handleRetry = () => {
    if (lastParams) handlePredictionSubmit(lastParams);
  };
```

Replace the sidebar's error block:

```jsx
            {/* Error Display */}
            {error && (
              <Alert
                severity="error"
                onClose={() => setError(null)}
                sx={{ mt: 3, mb: 3 }}
              >
                <AlertTitle>Error</AlertTitle>
                {error}
              </Alert>
            )}
```

with

```jsx
            {error && (
              <PredictionResult
                prediction={null}
                error={error}
                onRetry={lastParams ? handleRetry : undefined}
                onReset={handleReset}
              />
            )}
```

Run: `cd frontend && grep -nE "\bAlert(Title)?\b" src/App.jsx`
Remove `Alert` and/or `AlertTitle` from the `@mui/material` import list if the only remaining matches are in that import.

- [ ] **Step 4: Run and confirm pass**

Run: `cd frontend && npm run test:run && npm run lint && npm run typecheck && npm run build`
Expected: all Vitest files pass (7 in `PredictionResult.test.jsx`); lint, typecheck, and build clean.

- [ ] **Step 5: CHANGELOG and commit**

Insert above the PR6 entry:

```markdown
## [Phase 1 PR7] - 2026-09-27

### Fixed
- The route modal showed "Risk 0.0" when the safety request failed (`MapView.jsx` passed `safetyData.risk_score || 0`). The modal now renders from a typed `loading | error | ok` state: an error shows an Alert with Retry, and a missing score reads "Unavailable". The same holds for the sidebar prediction.

### Added
- Incremental TypeScript: `tsconfig.json` (`allowJs`, strict for `.ts`), `npm run typecheck` in CI, typed `services/api.ts` (validated `SafetyResponse`), `utils/riskUtils.ts`, and a `useRouteSafety` hook.
```

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add frontend/src/components/PredictionResult.jsx frontend/src/components/PredictionResult.test.jsx frontend/src/App.jsx CHANGELOG.md
git commit -m "fix(frontend): prediction errors show Retry; missing score reads Unavailable

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

PR7 is ready for review and `/commitandpush`.

---

# PR8 — Hygiene and docs

### Task 20: Remove tracked junk, move perf scripts

**Files:**
- Delete: `backend/tests/test_prediction_integration.py.backup`, `backend/check_weather_gaps.py`, `backend/test_weather_service.py`, `backend/test_weather_stats_db.py`, `backend/test_request.json`, `backend/tests/check_risk_scores.py`
- Move: `backend/tests/benchmark_all_accidents.py`, `benchmark_all_accidents_simple.py`, `benchmark_bulk_query.py`, `benchmark_vectorized_algorithm.py`, `profile_algorithm_direct.py`, `profile_algorithm_performance.py`, `profile_database_queries.py` → `backend/scripts/perf/`

- [ ] **Step 1: Branch and confirm the list against the tree**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git switch main && git pull --ff-only
git switch -c docs/p1-pr8-hygiene
git ls-files backend | grep -vE "^backend/(app|tests|alembic|scripts|db)/" 
git ls-files backend/tests | grep -E "\.backup$|/(benchmark|profile|check)_"
```

Expected first list: `.dockerignore`, `.gitignore`, `Dockerfile`, `alembic.ini`, `pyproject.toml`, `railway*.toml`, `uv.lock`, plus the four junk files (`check_weather_gaps.py`, `test_request.json`, `test_weather_service.py`, `test_weather_stats_db.py`). Anything else unexpected: stop and ask the owner. The second list is the `.backup`, `check_risk_scores.py`, 4 `benchmark_*`, and 3 `profile_*` files.

- [ ] **Step 2: Delete and move**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git rm backend/tests/test_prediction_integration.py.backup backend/check_weather_gaps.py backend/test_weather_service.py backend/test_weather_stats_db.py backend/test_request.json backend/tests/check_risk_scores.py
mkdir -p backend/scripts/perf
for f in backend/tests/benchmark_*.py backend/tests/profile_*.py; do git mv "$f" backend/scripts/perf/; done
git grep -n "tests/benchmark_\|tests/profile_\|--ignore=tests/benchmark\|--ignore=tests/profile" || echo "no references"
```

Expected: `no references`. If CI or `pyproject.toml` still has `--ignore=tests/benchmark_*`/`--ignore=tests/profile_*`, delete those flags; the files are no longer under `tests/`.

- [ ] **Step 3: Verify ignores and that the suite still runs**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git check-ignore -v backend/htmlcov/x frontend/dist/x backend/app/__pycache__/x
cd backend && uv run pytest -q
```

Expected: three lines, each matched by root `.gitignore` (`htmlcov/`, `dist/`, `__pycache__/`); pytest shows 0 failed. No `.gitignore` edit is needed if all three match. If one does not match, add that pattern to the root `.gitignore`.

- [ ] **Step 4: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add -A backend/scripts/perf backend/tests
git commit -m "chore: remove tracked junk; move benchmark/profile scripts to scripts/perf

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 21: LICENSE (Apache-2.0) and DATA_LICENSE.md

**Files:**
- Create: `LICENSE`, `DATA_LICENSE.md`

- [ ] **Step 1: Fetch the canonical Apache-2.0 text**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
curl -fsSL https://www.apache.org/licenses/LICENSE-2.0.txt -o LICENSE
head -3 LICENSE | sed -n '2p'
grep -c "END OF TERMS AND CONDITIONS" LICENSE
wc -l LICENSE
```

Expected: `                                 Apache License` (leading spaces), `1`, about `202` lines. Do not edit the appendix boilerplate.

- [ ] **Step 2: Write `DATA_LICENSE.md`**

```markdown
# Data licensing

The code in this repository is licensed under Apache-2.0 (see `LICENSE`). That license covers **code only**. Data follows the rules below.

## OpenBeta

OpenBeta climb and area data is published under **CC0 1.0** (public domain dedication). SafeAscent may store, display, and redistribute it. We credit OpenBeta as a courtesy, though CC0 does not require it.

## Mountain Project

- **Ice and mixed route facts** (route name, grade, location as area name and coordinates, and route type) may be **displayed** in SafeAscent as facts. They are not bulk-redistributed: no exports, downloads, or public datasets.
- **All other Mountain Project-derived data** (every `mp_*` table, including rock routes, descriptions, and tick aggregates) is **internal only**. It is never displayed, exported, sent to third parties, or committed to any repository.
- No Mountain Project prose (descriptions, comments, beta) is ever displayed or committed.
- Mountain Project data never appears in fixtures, tests, docs, screenshots, or commits.

## Accident and weather sources

Accident records (AAC, Avalanche.org/CAIC, NPS) and weather data (Open-Meteo) live in the production database. They are not committed to this repository. Each source's own terms govern reuse; nothing here grants rights to them.

## No scrapers

No scraper code is committed to any GitHub repository. Open-API clients (OpenBeta GraphQL, Open-Meteo, NOAA, USGS, Macrostrat, NWS, AirNow, SNOTEL) are fine. CI's `no-scrapers` guard enforces this.
```

- [ ] **Step 3: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add LICENSE DATA_LICENSE.md
git commit -m "docs: Apache-2.0 LICENSE and DATA_LICENSE (OpenBeta CC0, MP facts rule)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 22: Root `CLAUDE.md`

**Files:**
- Create: `CLAUDE.md`

- [ ] **Step 1: Write the file**

```markdown
# SafeAscent: agent guide

Climbing-route risk forecasts from historical accident data and weather. FastAPI + Celery backend on Railway, Postgres/PostGIS on Neon, and a React frontend. Read `README.md` for the point of the project and `DATA_LICENSE.md` before touching data.

## Commands

Backend (run from `backend/`):
- `uv sync`: install (the lockfile is committed; CI runs `uv sync --frozen`)
- `uv run pytest`: tests. Migration tests need `MIGRATIONS_TEST_ADMIN_URL` (see `tests/test_migrations.py`).
- `uv run ruff check app/`: lint
- `uv run mypy app scripts`: types (strict on the allowlist in `pyproject.toml`)
- `uv run alembic upgrade head`: migrations. Needs `MIGRATOR_DATABASE_URL`. Never runs on app startup.
- `uv run uvicorn app.main:app --reload`: API
- `uv run celery -A app.celery_app worker --concurrency=2 -E` and `uv run celery -A app.celery_app beat`: background jobs

Frontend (run from `frontend/`): `npm ci`, `npm run dev`, `npm test` (watch), `npm run test:run`, `npm run lint`, `npm run typecheck`, `npm run build`.

Local stack: `docker compose up` (db, redis, api, worker, beat; same commands as Railway).

## Service topology

- Railway services: `api` (`backend/railway.toml`), `worker` (`backend/railway-worker.toml`), `beat` (`backend/railway-beat.toml`, exactly one replica), and `frontend`. One Redis. Postgres on Neon.
- Deploys: Railway auto-deploys `main` only after the `ci-ok` check is green.
- Alerting: healthchecks.io dead-man checks for the nightly job (`HEALTHCHECKS_NIGHTLY_URL`) and a 15-minute beat heartbeat (`HEALTHCHECKS_BEAT_URL`). `GET /health/worker` returns 503 when the worker heartbeat is missing.

## Database and migrations

- Alembic lives in `backend/alembic/`. `0001_baseline` replays the live schema. Prod was stamped at it, never upgraded through it.
- Every schema change is a new revision. Rehearse it on a Neon branch, then run it as an explicit step with `MIGRATOR_DATABASE_URL`.
- No DDL in app code; `tests/test_no_runtime_ddl.py` enforces this.
- Roles: `analyst` (read-only), `migrator` (DDL), `app` (DML); later `ingest`, `trainer`, `triage_worker`. Create roles **only via SQL** as the owner (`backend/db/roles/`), never via neonctl, the Neon Console, or the API: those add `neon_superuser`. Credentials live in gitignored `backend/.env.<role>` files. Never print or read them in an agent session.
- The legacy tables `routes`/`mountains` and the FKs from `accidents` stay until Phase 2a.

## Data rules

- **No scraper code is ever committed or published to GitHub, in any repo.** Scrapers live in `~/Developer/safeascent-private/` (local only, no remote). Clients for open APIs (OpenBeta GraphQL, Open-Meteo, NOAA, USGS, Macrostrat, NWS, AirNow, SNOTEL) belong in the repo. CI's `no-scrapers` guard enforces this.
- Mountain Project: only ice/mixed route facts (name, grade, location, type) may be displayed. No MP prose, ever. No MP data in fixtures, docs, or commits. Everything else from MP is internal.
- OpenBeta data is CC0. See `DATA_LICENSE.md`.

## Conventions

- Python 3.12, uv, ruff, pytest, mypy. Frontend: React 19 + Vite. New frontend modules are TypeScript; convert a `.js`/`.jsx` file when you substantially change it. Tests stay in the language of the component under test.
- Comments are load-bearing only: why, not what. No banner or narration comments.
- `CHANGELOG.md`: one dated entry per merged PR.
- Branch per change off `main`. PR plus green `ci-ok` required. No direct pushes or force-pushes to `main`.
- Secrets never go in the repo. `.env.example` lists exactly the `Settings` fields plus the `VITE_*` build args; a parity test enforces this.
```

- [ ] **Step 2: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add CLAUDE.md
git commit -m "docs: root CLAUDE.md with commands, topology, migration and data rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 23: README rewrite, DEPLOYMENT rewrite, docs drift, diagrams

**Files:**
- Rewrite: `README.md`, `DEPLOYMENT.md`
- Modify: `frontend/README.md:9`
- Create: `docs/diagrams/system_design.mmd`, `docs/diagrams/data_model.mmd`
- Regenerate: `system_design.png`, `data_model.png`

- [ ] **Step 1: Rewrite `README.md`**

```markdown
# SafeAscent

Route-level risk forecasts for climbers, built from historical accident records and the weather forecast.

> **Status (September 2026): offline for a rebuild.** safeascent.us shows a maintenance page while the data pipeline, model, and operations are rebuilt. The code here is the current state of that work, not a running service.

## What it does

For a climbing route and a date in the next few days, SafeAscent scores risk from 0 to 100. Nearby historical accidents are weighted by distance, how recent they were, elevation, route type, severity, and how closely the weather before each accident matches the coming forecast. Scores for all routes are precomputed nightly. See `ALGORITHM_DESIGN.md` for the method.

## Architecture

![System design](system_design.png)

- **Backend:** FastAPI (async SQLAlchemy + asyncpg) serving `/api/v1`.
- **Jobs:** a Celery worker and a separate beat service. The nightly job precomputes scores into Redis and `historical_predictions`.
- **Data:** Postgres + PostGIS on Neon, with schema managed by Alembic (`backend/alembic/`). See `data/DATABASE_STRUCTURE.md`.
- **Frontend:** React 19 + Vite + MUI + Mapbox GL, moving to TypeScript incrementally.
- **Hosting:** Railway. Deploys run only after CI is green. healthchecks.io alerts on missed nightly runs.

## Local quickstart

Requires Docker, [uv](https://docs.astral.sh/uv/), and Node 22.

```bash
cp .env.example .env            # fill in VITE_MAPBOX_TOKEN at minimum
docker compose up -d db redis
cd backend
uv sync
export DATABASE_URL=postgresql+asyncpg://safeascent:safeascent@localhost:5432/safeascent
MIGRATOR_DATABASE_URL="$DATABASE_URL" uv run alembic upgrade head
uv run uvicorn app.main:app --reload
# in another terminal
cd frontend && npm ci && npm run dev
```

A fresh database has the schema but no data. The accident, route, and weather data are not distributed with this repository (see Data sources).

Tests: `cd backend && uv run pytest` and `cd frontend && npm run test:run`.

## Data sources and licenses

| Source | Used for | License / terms |
|---|---|---|
| OpenBeta | Rock route catalog (planned, Phase 2) | CC0 |
| Mountain Project | Ice/mixed route facts (display); other data internal only | See `DATA_LICENSE.md` |
| American Alpine Club, Avalanche.org/CAIC, NPS | Historical accidents | Source terms; not redistributed |
| Open-Meteo | Forecasts and historical weather | Open-Meteo terms |

## Repository guide

- `CLAUDE.md`: commands, conventions, and data rules (for contributors and coding agents)
- `DEPLOYMENT.md`: Railway, Neon, CI/deploy pipeline
- `ALGORITHM_DESIGN.md`: scoring method
- `CHANGELOG.md`: dated changes

## License

Code: Apache-2.0 (`LICENSE`). Data: see `DATA_LICENSE.md`.
```

Check that the quickstart `DATABASE_URL` matches Part A's `docker-compose.yml` `db` credentials:

Run: `cd /Users/sebastianfrazier/Developer/SafeAscent && grep -nA6 "^  db:" docker-compose.yml`
Update the user, password, and database in the README line to match the compose `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` defaults.

Then run the quickstart from a fresh clone:

```bash
rm -rf /private/tmp/sa-fresh && git clone --branch docs/p1-pr8-hygiene /Users/sebastianfrazier/Developer/SafeAscent /private/tmp/sa-fresh
cd /private/tmp/sa-fresh && cp .env.example .env && docker compose up -d db redis
cd backend && uv sync && export DATABASE_URL="$(grep -oE 'postgresql\+asyncpg://[^ ]+' ../README.md | head -1)" && export MIGRATOR_DATABASE_URL="$DATABASE_URL" && uv run alembic upgrade head && uv run alembic current
cd /private/tmp/sa-fresh && docker compose down -v && rm -rf /private/tmp/sa-fresh
```

Expected: `alembic current` prints `0002_drop_ascents_climbers (head)`.

- [ ] **Step 2: Rewrite `DEPLOYMENT.md` for Railway only**

Keep Part A's CI / "Wait for CI" / branch-protection section **verbatim** (it was added in PR3). Replace everything else with:

```markdown
# Deployment

**Stack:** Railway (hosting) + Neon (Postgres 16+/PostGIS) + one Railway Redis + Porkbun DNS + healthchecks.io (alerts).

## Services (Railway, one repo, auto-deploy from `main` after green `ci-ok`)

| Service | Config | Start command | Notes |
|---|---|---|---|
| `api` | `backend/railway.toml` | `uvicorn app.main:app` (Dockerfile default) | Health: `/health`; worker liveness: `/health/worker` |
| `worker` | `backend/railway-worker.toml` | `celery -A app.celery_app worker --concurrency=2 -E` | Runs the nightly job and the beat heartbeat |
| `beat` | `backend/railway-beat.toml` | `celery -A app.celery_app beat` | Exactly one replica |
| `frontend` | `frontend/railway.toml` | nginx serving the Vite build | `MAINTENANCE_MODE` build arg serves the maintenance page |

All backend services build from `backend/Dockerfile` (Python 3.12, uv). Domains: `safeascent.us` (frontend), `www.safeascent.us` (301 to apex), `api.safeascent.us` (api).

## Environment variables

The full list is `.env.example` (kept in parity with `app.config.Settings`). Beyond that list:
- `DATABASE_URL` on `api`, `worker`, and `beat` uses the least-privilege `app` role.
- `HEALTHCHECKS_NIGHTLY_URL`, `HEALTHCHECKS_BEAT_URL`: on `worker` only.
- `MIGRATOR_DATABASE_URL` is **not** set on any service. It exists only in the owner's gitignored `backend/.env.migrator`, for the explicit migration step.

## Migrations

1. Rehearse on a Neon branch: `MIGRATOR_DATABASE_URL=<branch URL> uv run alembic upgrade head && uv run alembic check`.
2. Apply to prod the same way from the owner's machine. Never on app startup, never from a Railway pre-deploy hook.

## Database roles

Created only via `backend/db/roles/create_roles.sql` as the owner and verified with `verify_roles.sql`. Never create roles with neonctl, the Console, or the API (they grant `neon_superuser`).

## Nightly job

At 02:00 UTC, beat enqueues `compute_daily_safety_scores_optimized` (expires after 8h). The worker computes today plus the next 2 days, writes Redis keys (2-day TTL) and `historical_predictions`, and pings healthchecks.io at start, success, or fail. Expired or revoked tasks are logged at ERROR and counted; `/health/worker` shows 7-day counts.

## Alerts

- `safeascent-nightly`: 24h period, 4h grace.
- `safeascent-beat`: 15 min period, 60 min grace. A dead consumer alerts within 75 min.
```

- [ ] **Step 3: Frontend README drift**

In `frontend/README.md`, `- **React 18** - UI framework` → `- **React 19** - UI framework`.

Run: `cd /Users/sebastianfrazier/Developer/SafeAscent && git grep -nE "React 18|Python 3\.11" -- '*.md' ':!docs/superpowers' ':!CHANGELOG.md' || echo "clean"`
Expected: `clean`.

- [ ] **Step 4: Diagram sources**

`docs/diagrams/system_design.mmd`:

```text
flowchart LR
  user([Browser]) --> fe[frontend<br/>nginx + React 19]
  user --> api[api<br/>FastAPI]
  fe -->|/api/v1| api
  beat[beat<br/>Celery beat, 1 replica] -->|enqueue| redis[(Redis<br/>broker + cache)]
  redis --> worker[worker<br/>Celery, concurrency 2]
  worker -->|scores| redis
  worker -->|historical_predictions| neon[(Neon Postgres + PostGIS)]
  api --> redis
  api --> neon
  worker -->|start / success / fail| hc[[healthchecks.io]]
  worker -->|beat heartbeat, 15 min| hc
  worker -->|heartbeat key| redis
  api -->|/health/worker reads| redis
  worker --> meteo[[Open-Meteo]]
  api --> meteo
```

`docs/diagrams/data_model.mmd` (post-`0002` live tables; add an entity with its primary key for any other table printed by the `grep` below):

```text
erDiagram
  mp_locations ||--o{ mp_routes : "location_id"
  mp_routes ||--o{ historical_predictions : "route_id"
  accidents ||--o{ weather : "accident_id"
  routes ||--o{ accidents : "route_id (legacy FK, Phase 2a)"
  mountains ||--o{ accidents : "mountain_id (legacy, Phase 2a)"

  mp_locations { int mp_id PK }
  mp_routes { int mp_route_id PK }
  historical_predictions { int id PK }
  accidents { int accident_id PK }
  weather { int weather_id PK }
  routes { int route_id PK }
  mountains { int mountain_id PK }
```

Run: `cd /Users/sebastianfrazier/Developer/SafeAscent && grep -oE "CREATE TABLE public\.[a-z_0-9]+" backend/alembic/versions/0001_baseline.sql | sed 's/CREATE TABLE public\.//' | sort -u | grep -vxE "ascents|climbers|mp_locations|mp_routes|historical_predictions|accidents|weather|routes|mountains" || echo "no extra tables"`
Expected: either `no extra tables`, or names to add as `<name> { <pk type> <pk column> PK }` blocks (PK read from the table's `PRIMARY KEY` constraint in the baseline SQL).

- [ ] **Step 5: Render the PNGs**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
npx -y @mermaid-js/mermaid-cli@11 -i docs/diagrams/system_design.mmd -o system_design.png -b white -w 1600
npx -y @mermaid-js/mermaid-cli@11 -i docs/diagrams/data_model.mmd -o data_model.png -b white -w 1600
file system_design.png data_model.png
```

Expected: both `PNG image data, 1600 x …`. Open both and check the split worker/beat, the single Redis, and no `ascents`/`climbers`.

- [ ] **Step 6: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add README.md DEPLOYMENT.md frontend/README.md docs/diagrams system_design.png data_model.png
git commit -m "docs: honest README, Railway-only DEPLOYMENT, regenerated diagrams

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 24: mypy on an allowlist of typed modules

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `.github/workflows/ci.yml` (job `backend`, mypy step)

**Interfaces:**
- Consumes: typed modules `app.config` (Part A), `app.celery_signals`, `app.healthchecks` (PR6), `scripts.sanitize_schema_dump`, `scripts.write_role_url` (PR5).

- [ ] **Step 1: Write the config**

In `backend/pyproject.toml`, replace any existing `[tool.mypy]` section and its overrides with:

```toml
[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
# Untyped legacy code is skipped until it is touched; typed modules opt in below.
ignore_errors = true

[[tool.mypy.overrides]]
module = [
  "app.config",
  "app.celery_signals",
  "app.healthchecks",
  "scripts.sanitize_schema_dump",
  "scripts.write_role_url",
]
ignore_errors = false
# mypy's `strict` is global-only; these are its per-module equivalents. Subclassing-any
# and untyped-decorator checks stay off because celery ships no type information.
disallow_untyped_defs = true
disallow_incomplete_defs = true
disallow_untyped_calls = true
disallow_any_generics = true
check_untyped_defs = true
no_implicit_reexport = true
strict_equality = true
warn_return_any = true
warn_unused_ignores = true
```

- [ ] **Step 2: Run mypy and fix only the allowlist**

Run: `cd backend && uv run mypy app scripts`
Expected: `Success: no issues found in N source files`. If errors appear, they are in allowlisted modules only. Fix them with annotations, not `# type: ignore`. For example, `redis.Redis.get` returns `Any`, so wrap the value as `str(raw)` before `float()` in `read_worker_health`.

- [ ] **Step 3: CI**

In `.github/workflows/ci.yml`, job `backend`, set the mypy step's command to `uv run mypy app scripts` (add the step after the ruff step if Part A did not create one):

```yaml
      - name: Type check (mypy allowlist)
        working-directory: backend
        run: uv run mypy app scripts
```

- [ ] **Step 4: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add backend/pyproject.toml .github/workflows/ci.yml
git commit -m "chore(backend): mypy strict flags on an allowlist of typed modules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 25: PR8 CHANGELOG and final verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: CHANGELOG**

Insert above the newest entry:

```markdown
## [Phase 1 PR8] - 2026-09-27

### Added
- Root `CLAUDE.md` (commands, topology, migration policy, data rules including the no-scraper rule), `LICENSE` (Apache-2.0), `DATA_LICENSE.md`.
- Mermaid sources for `system_design.png` and `data_model.png` in `docs/diagrams/`.
- mypy strict-equivalent flags on an allowlist (`app.config`, `app.celery_signals`, `app.healthchecks`, `scripts.*`).

### Changed
- README rewritten with an honest status (offline for rebuild, Sept 2026). `DEPLOYMENT.md` now covers Railway only. Docs updated to React 19 and Python 3.12. The README previously said MIT; the code license is Apache-2.0.
- Benchmark and profile scripts moved from `backend/tests/` to `backend/scripts/perf/`.

### Removed
- Tracked junk: `backend/tests/test_prediction_integration.py.backup`, `backend/tests/check_risk_scores.py`, `backend/check_weather_gaps.py`, `backend/test_weather_service.py`, `backend/test_weather_stats_db.py`, `backend/test_request.json`.
```

- [ ] **Step 2: Verify everything**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend && uv run ruff check app/ && uv run mypy app scripts && uv run pytest -q
cd ../frontend && npm run lint && npm run typecheck && npm run test:run
cd .. && git ls-files | grep -E "\.backup$|^backend/(check_|test_)[^/]*$|^backend/tests/(benchmark|profile|check)_" || echo "no junk tracked"
```

Expected: all green; `no junk tracked`.

- [ ] **Step 3: Commit**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent
git add CHANGELOG.md
git commit -m "docs: PR8 changelog

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

PR8 is ready for review and `/commitandpush`.

---

# Relaunch gate (not executed in Phase 1 unless the owner says so)

### Task 26: OWNER CHECKLIST — relaunch

Every item must hold before maintenance mode is turned off. Agents do not perform these steps.

- [ ] **Step 1 (owner): Switch services to the `app` role.** In the Railway dashboard, set `DATABASE_URL` on `api`, `worker`, and `beat` to the value of `APP_DATABASE_URL` from `backend/.env.app` (open the file in an editor and copy the value; do not `cat` it into a shared terminal). Confirm `MIGRATOR_DATABASE_URL` is set on no service. Redeploy the three services. Then:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://api.safeascent.us/health
curl -s https://api.safeascent.us/health/worker
```

Expected: `200`, and `"status":"ok"`.

- [ ] **Step 2 (owner): Rotate the `neondb_owner` password** (it leaked in public git history). Use the Neon Console → Branch `main` → Roles → `neondb_owner` → Reset password. Put the new URL in `backend/.env.owner` with an editor. Confirm the old password no longer authenticates, by pasting the old URL from the leaked history into a gitignored `backend/.env.oldowner` as `OLD_OWNER_DATABASE_URL=...`:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.oldowner; set +a; psql "$OLD_OWNER_DATABASE_URL" -XAtc 'select 1' ) ; echo "exit=$?"
rm -f .env.oldowner
```

Expected: `password authentication failed for user "neondb_owner"` and `exit=2`.

- [ ] **Step 3 (owner): Drop the transitional membership** (services no longer use the owner role):

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.owner; set +a
  psql "$OWNER_DATABASE_URL" -X -q -c "REVOKE migrator FROM CURRENT_USER"
  psql "$OWNER_DATABASE_URL" -X -q -f db/roles/verify_roles.sql )
```

Expected: `ALL ROLE CHECKS PASSED`. The owner no longer inherits table access, which is intended.

- [ ] **Step 4 (owner): Re-enable deploys.** In Railway, un-pause auto-deploy (if paused) on `api`, `worker`, `beat`, and `frontend`, with "Wait for CI" on and branch `main`.

- [ ] **Step 5 (owner): Observe one clean nightly run** under the `app` role. healthchecks.io `safeascent-nightly` shows start + success. `/health/worker` shows `"expired_tasks_7d": {}`. `historical_predictions` has rows for today:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.analyst; set +a
  U="${ANALYST_DATABASE_URL/postgresql+asyncpg:/postgresql:}"; U="${U/ssl=require/sslmode=require}"
  psql "$U" -XAtc "SELECT count(*) FROM historical_predictions WHERE prediction_date = CURRENT_DATE" )
```

Expected: a count in the six figures (about 168K routes).

- [ ] **Step 6 (owner): Turn maintenance off.** Set `MAINTENANCE_MODE=false` on the Railway `frontend` service and redeploy. Check:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://safeascent.us/
curl -sI https://www.safeascent.us | head -3
```

Expected: `200`; `HTTP/2 301` with `location: https://safeascent.us/`.

---

## Self-review (completed while writing)

- **Spec coverage.**
  - §2: roles (Task 6, Task 8); dump and baseline (Tasks 1–3); drift (Task 3 Step 8); weather docs (Task 7); `0002` + model removal (Task 4); stamp/upgrade and verification (Task 8).
  - §3: split (Task 11 Step 5); liveness (Tasks 10, 12); dead-man pings (Tasks 9, 11); expired tasks (Task 10); drills (Task 14).
  - §6 frontend TS + Risk 0.0 (Tasks 15–19).
  - §7 hygiene (Tasks 20–25).
  - Relaunch gate (Task 26).
  - Testing section: Alembic (`test_migrations.py`); roles (`verify_roles.sql` plus the CI role test); Celery (unit tests plus Task 14 drills); frontend Vitest reject case (Task 18); hygiene (`git ls-files`, Task 25); README fresh clone (Task 23 Step 1).
  - §6 items owned by Part A (www TLS, maintenance build) are out of scope here.
- **Placeholders.** Generated content (the sanitized baseline SQL, model drift edits, the extra ER entities) comes from explicit commands and rules, with a defined end state (`alembic check` clean; `grep` shows no extra tables).
- **Name consistency.**
  - `SafetyState`, `useRouteSafety`, `fetchRouteSafety`, `SafetyResponse`, `isRiskScore`, `formatRiskScore` match across Tasks 16–19.
  - `HEARTBEAT_KEY`, `EXPIRED_KEY_PREFIX`, `read_worker_health`, `HeartbeatStep`, `install`, `ping`, `beat_heartbeat` match across Tasks 9–13.
  - `0001_baseline`, `0002_drop_ascents_climbers`, `UNMANAGED_LEGACY_TABLES`, `MIGRATOR_DATABASE_URL`, and `MIGRATIONS_TEST_ADMIN_URL` match across Tasks 3–8 and 23.
