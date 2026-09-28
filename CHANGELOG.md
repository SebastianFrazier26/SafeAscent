# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Phase 1 PR5 — 2026-09-27

- Alembic added (`backend/alembic.ini`, async `backend/alembic/env.py`). Migrations read `MIGRATOR_DATABASE_URL` from the environment, never the app's `DATABASE_URL`/`Settings`, and run as an explicit step, never at app startup.
- Revision `0001_baseline` replays `backend/alembic/versions/0001_baseline.sql`, a sanitized `pg_dump --schema-only -n public` of the live Neon schema (Postgres 16, PostGIS; no roles, grants, owners or data). Production is `alembic stamp 0001_baseline`, never upgraded through it: the upgrade refuses a database that already has `public.accidents` and points to `stamp`, and refuses offline `--sql` rendering. Downgrade raises. `env.py` skips `fileConfig` when the caller sets `config.attributes["configure_logger"] = False` (the migration tests do), so running Alembic in-process no longer disables existing `app.*` loggers.
- Models aligned to the live schema until `alembic check` is clean: `index=True` flags replaced with the live `idx_*` index names (including the GIST indexes on `accidents.coordinates`/`weather.coordinates`), `accidents.mp_route_id` (live column + FK to `mp_routes`) added to `Accident`, and `climbers.username`'s live unique constraint named. No column types, nullability, or row contents changed.
- `app/models/legacy.py` declares PK-only stubs for `routes`/`mountains` so the live `accidents` FKs resolve; `env.py` keeps them, and live tables with no model (`historical_predictions`, `area_weekly_weather`, `mp_ticks`), out of autogenerate.
- New `backend/tests/test_migrations.py` builds a throwaway database, upgrades to head, and runs `alembic check`. It runs when `MIGRATIONS_TEST_ADMIN_URL` is set, which CI's `backend` job now does against its PostGIS service.
- Revision `0002_drop_ascents_climbers` (D8 stage 1): drops `ascents` and `climbers`, both empty in the 2026-09-27 audit. The migration counts rows on each table first and raises `RuntimeError` instead of dropping if either is non-empty; downgrade raises (the tables were empty and their models are gone). `Ascent`/`Climber` models removed along with them.
- Runtime DDL removed from `app/` (D9/least-privilege prep): the nightly `historical_predictions` batch save (`app/tasks/safety_computation_optimized.py`) and the historical-trends endpoint (`app/api/v1/mp_routes.py`) both ran `CREATE TABLE IF NOT EXISTS historical_predictions ...` before their queries. Under the least-privilege `app` role that statement needs `CREATE` on `public` even when the table already exists, so it would have failed every batch (silently, inside a swallowing `except`). The table, its sequence, PK/unique constraints, and both indexes are already replayed by `0001_baseline.sql`, so the calls were redundant even today. Also deletes the dead `app/tasks/safety_computation.py` (665 lines, 3 more DDL statements; not in Celery's `include`, imported nowhere). New `backend/tests/test_no_runtime_ddl.py` greps `app/` for `CREATE`/`ALTER TABLE`/`DROP` and fails the suite if any reappear.
- Least-privilege DB roles, created only through SQL run as the owner (never neonctl/Console/API, which add `neon_superuser`). `backend/db/roles/create_roles.sql` creates `migrator` and `app` (`LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT`) and requires PostgreSQL 16+. It moves ownership of every non-extension table/view/sequence/routine/enum/domain in `public` to `migrator`, the only role with `CREATE` on `public`. The owner keeps `migrator` membership `WITH SET, INHERIT` until the relaunch gate revokes it, so services still on the owner URL keep working.
- `app` is read-mostly: `SELECT` on every table except `alembic_version`, plus `INSERT, UPDATE, DELETE` only on `historical_predictions` and `USAGE` on its id sequence (the nightly upsert and purge in `app/tasks/safety_computation_optimized.py` are the app's only writes; `app/` was re-grepped for others). Default privileges make tables `migrator` creates later `SELECT`-only for `app` and `analyst`; a migration that needs app writes grants them per table.
- Passwords never reach the server: `create_roles.sql` reads SCRAM-SHA-256 verifiers from `MIGRATOR_PASSWORD_SCRAM`/`APP_PASSWORD_SCRAM` via psql `\getenv` and refuses values that are not verifiers, since Postgres would otherwise store a pasted plaintext as the password. The role test confirms the stored `rolpassword` is the verifier, the right password logs in and a wrong one is rejected, and no plaintext appears in the SQL psql sends or in `pg_stat_statements`.
- `backend/db/roles/verify_roles.sql` checks the following and exits non-zero on any miss:
  - no superuser/createdb/createrole/replication/bypassrls;
  - `NOINHERIT` on `migrator`/`app`, and the three roles hold no memberships;
  - only `CURRENT_USER` is a member of `migrator`;
  - nobody can `SET` or inherit `app` (PG16's automatic ADMIN-only grant to the creating owner is allowed);
  - schema/database `CREATE`, ownership, and exact per-table `app` DML (writes only on `historical_predictions`);
  - `analyst` has no `INSERT/UPDATE/DELETE/TRUNCATE`;
  - sequence grants, and that `pg_default_acl` is exactly `SELECT` to `app` and `analyst`.
- New `backend/scripts/write_role_url.py`, with `--role` limited to `migrator`/`app`:
  - `--env-file <path>` builds `<ROLE>_DATABASE_URL` (asyncpg, `ssl=require`, password URL-escaped) from `OWNER_DATABASE_URL`'s host/db and `<ROLE>_PASSWORD`, and writes it via `mkstemp` (0600) plus atomic `os.replace`, so an existing 0644 file never holds the secret at a looser mode.
  - `--scram` prints the role's SCRAM-SHA-256 verifier (`scram_verifier()`, stdlib PBKDF2/HMAC, checked against the RFC 7677 example exchange), reading the password from `<ROLE>_PASSWORD` or `--password-stdin`, never argv.
  - Neither mode prints a password.
- The end-to-end role test in `test_migrations.py` runs `create_roles.sql` as a non-superuser `CREATEROLE` owner, as on Neon. It fails instead of skipping when `psql` is missing and `CI` is set, and its role cleanup refuses to `DROP ROLE` unless `MIGRATIONS_TEST_ADMIN_URL` points at localhost.
- The plan's owner runbook (Task 8) now computes each verifier inside a subshell that alone loads the plaintext, and rehearses `create_roles.sql` plus the Alembic stamp/upgrade on a Neon branch before running anything on prod.

### Phase 1 PR4 — 2026-09-27

- `Settings` is now the single source of runtime config: `DATABASE_URL` is required (module import raises `pydantic.ValidationError` and fails loudly if unset, instead of silently falling back to a guessed local database), `ENVIRONMENT` defaults to `"production"` (was `"development"`) so an unset variable on a deployed service never enables dev-only behavior, and SQL echo is now the explicit `SQL_ECHO` flag (default off) rather than implied by `ENVIRONMENT == "development"`. New fields land on `Settings`: `OPEN_METEO_API_KEY`, `USE_VECTORIZED_ALGORITHM`, `SKIP_WEATHER_STATISTICS`, `HEALTHCHECKS_NIGHTLY_URL`, `HEALTHCHECKS_BEAT_URL`, `WORKER_HEARTBEAT_TTL_SECONDS` (default `120`).
- Security fix: `Settings` now sets `hide_input_in_errors=True`. Pydantic's "missing field" error for the required `DATABASE_URL` was embedding the whole resolved config dict as `input_value`, so any credential already present in another field (`REDIS_URL`, `CELERY_BROKER_URL`, ...) leaked into stderr at import time alongside the fail-loud `DATABASE_URL` message; the fix drops `input_value` from validation errors entirely while keeping the field name that failed.
- `backend/tests/conftest.py` sets a dummy `DATABASE_URL`/`ENVIRONMENT=test` via `os.environ.setdefault` before importing `app`, so the test suite runs without `.env` or a real database URL.
- `app.config` added to the mypy strict allowlist in `backend/pyproject.toml`.
- New `backend/tests/test_settings.py` covers the required field, the production default, explicit `SQL_ECHO`, the new fields' defaults, and a repo-wide rule that `app/` never calls `os.getenv`/`os.environ` directly.
- The last three `os.getenv` call sites in `app/` now read from `Settings`: `backend/app/api/v1/predict.py`'s vectorized-algorithm flag, and `backend/app/services/weather_service.py`'s `OPEN_METEO_API_KEY` module constant and `SKIP_WEATHER_STATISTICS` short-circuit. No behavior change — same defaults, now enforced by the `test_settings.py` rule above.
- Root `.env.example` rewritten to mirror `Settings.model_fields` exactly (plus the frontend `VITE_API_BASE_URL`/`VITE_MAPBOX_TOKEN` build args): dropped the docker-compose-only `POSTGRES_*`, `REDIS_PORT`, `BACKEND_PORT`, `FRONTEND_PORT` keys that never backed a `Settings` field, and added the fields introduced above. New `backend/tests/test_env_example_parity.py` enforces this as a standing CI rule — a `Settings` field added without a matching `.env.example` line now fails the suite.
- Removed stale deploy definitions: `.do/` (DigitalOcean) and `docker-compose.prod.yml`. Root `railway.toml` removed (unused: every Railway service resolves config from its rootDirectory).
- `docker-compose.yml` is local-dev only and now mirrors Railway exactly: services `db`, `redis`, `api`, `worker`, `beat`, `frontend`, same start commands as the Railway services, ports bound to `127.0.0.1`, `DATABASE_URL`/`ENVIRONMENT=development` set explicitly per Task 10's now-required/defaulted `Settings` fields. CI's `guards` job asserts `docker compose config --services` matches the Railway topology.

### Phase 1 PR3 — 2026-09-27

- No-scraper CI guard (D9): `scripts/check_no_scrapers.py` (stdlib only) scans every `git ls-files`-tracked path for scraper code — file names matching `scrape*`/`*scraper*`, mentions of banned climbing-data host names in source, banned Python/JS HTML-parsing or browser-automation imports, and those same packages in `backend/uv.lock`/`frontend/package-lock.json` — and exits 1 with one `path: [rule] detail` line per hit. Scrapers stay in the private local workspace, never this repo. `backend/tests/test_check_no_scrapers.py` covers all four rules plus the git-tracked-files-only behavior. `.gitignore`'s blanket `scripts/` rule narrowed to `/scripts/*` with an exception for the guard file, so the script itself can be tracked; other one-off scripts stay ignored.
- Fix round 1 (review): the `import`/`host` rules now match case-insensitively (`import BS4` resolves to the real `bs4` package on macOS's case-insensitive filesystem); `cheerio` and `jsdom` added to the banned JS import list; the banned-host set expanded from just `mountainproject.com` to `mountainproject.com`, `thecrag.com`, `8a.nu`, `ukclimbing.com`, matched with word-boundary guards so `8a.nu` doesn't misfire on lookalike strings like `v8a.number`. `jsdom` is deliberately kept out of the npm-lockfile banlist (same precedent as `lxml` for Python) — it's a mainstream Vitest/Jest DOM test-environment devDependency (this repo's own `frontend/package-lock.json` has it), not a scraping tool; its actual *use* is still caught by the import rule.
- CI rebuilt into `backend`, `frontend`, `guards` and a single required `ci-ok` job; the unused GHCR `build-images` job and placeholder `deploy` job are removed (Railway will build from GitHub with "Wait for CI" once the owner enables it).
- Third-party actions in `.github/workflows/ci.yml` (`actions/checkout`, `astral-sh/setup-uv`, `actions/setup-node`, `actions/setup-python`) are pinned by commit SHA with a `# vX.Y.Z` comment, not a floating major tag — `astral-sh/setup-uv@v10` doesn't resolve to any published ref, so this also fixes CI going red on the first real run.
- Backend CI runs the whole default suite (`uv run pytest`); tests needing production data or live services are marked `needs_data` and deselected by default.
- The `guards` job runs the no-scraper check above in CI.
- mypy runs in CI.

### Phase 1 PR2 — 2026-09-27

- Backend dependency management moved from `requirements.txt`/`pip` to `uv`: `backend/pyproject.toml` (`[project]` deps + `[dependency-groups] dev`) and `backend/uv.lock` replace `requirements.txt`; `pytest.ini` and `ruff.toml` settings moved into `[tool.pytest.ini_options]` and `[tool.ruff]` in `pyproject.toml`. All Phase 0 pins carried forward unchanged (fastapi 0.141.1, uvicorn 0.53.0, requests 2.34.2, python-dotenv 1.2.3, pydantic-settings 2.15.0, pytest 9.1.1, pytest-asyncio 1.4.0, python-multipart 0.0.32).
- `[tool.mypy]` added with `plugins = ["pydantic.mypy"]`, an `app.*` allowlist that ignores errors for untyped legacy code, and an empty strict-allowlist override block (`module = []`) for later PRs to append to as modules are typed.
- `mypy==2.3.1` and `pip-audit==2.10.1` added to the `dev` dependency group.
- README backend quickstart updated to `uv sync` / `uv run uvicorn ...` / `uv run pytest`.
- One `backend/Dockerfile` (uv-built, non-root) serves the api, worker and beat; `Dockerfile.worker` removed. `backend/railway-worker.toml` now points at the shared `Dockerfile`; the Railway worker still runs embedded `--beat` until PR6, which splits it out via `startCommand`.
- CI (`.github/workflows/ci.yml`) now installs with `uv sync --frozen`, lints and audits (`uv export --no-dev` piped to `pip-audit`) via `uv run`, matching the local toolchain instead of a separate `pip`/`pip-audit` install.
- New pytest marker `needs_data` (registered in `[tool.pytest.ini_options]`, deselected by default via `addopts -m 'not needs_data'`) marks the tests that need a populated database or live Redis/network, applied at whatever granularity is accurate — module-level in `test_daily_variation_yosemite.py`, `test_known_outcomes_validation.py`, `test_longs_peak_daily.py`; per-class or per-test in `test_edge_cases_performance.py`, `test_predict_integration.py`, `test_prediction_integration.py`, `test_weather_caching.py`, `test_performance.py`, so pure-validation and pure-function tests in those files still run by default. Plain `cd backend && uv run pytest` is now green (at-PR2 counts; grows as later PRs add tests) with no DB/Redis/network required; run `uv run pytest -m needs_data` to exercise the deselected tests.
- Commands: `cd backend && uv sync`, `uv run pytest`, `uv run pytest -m needs_data`, `uv run ruff check app/`, `uv run mypy`.

### Phase 1 PR1 — 2026-09-27

- Frontend image takes a `MAINTENANCE_MODE` build arg. When `true` it serves a static page with HTTP 503, `Retry-After` and `Cache-Control: no-store` for every path, while `/health` stays 200.
- nginx redirects `www.safeascent.us` to `https://safeascent.us` (301) in both modes.
- Railway frontend healthcheck moved from `/` to `/health`.
- CI builds both image modes and checks the HTTP contract (`frontend/docker-tests/test_images.sh`).

### Security (Phase 0, 2026-09-24)

- Admin routes (`backend/app/api/v1/admin.py`) are now disabled in production unless the `ENABLE_ADMIN_ROUTES` flag is set.
- The redis-debug endpoint no longer leaks `REDIS_URL` in its response.
- `CORS_ORIGINS` now accepts either a comma-separated string or a JSON array.
- Dependency security bumps in `backend/requirements.txt`:
  - `python-multipart` 0.0.18 → 0.0.32
  - `requests` 2.32.3 → 2.34.2
  - `python-dotenv` 1.0.1 → 1.2.3
  - `pydantic-settings` 2.6.1 → 2.15.0 (required by the `CORS_ORIGINS` change above, which uses `NoDecode`, added in pydantic-settings 2.7)
  - `pytest` 8.3.4 → 9.1.1 and `pytest-asyncio` 0.24.0 → 1.4.0 (verified against the full CI test subset before bumping)
  - `fastapi` 0.115.0 → 0.141.1 and `uvicorn` 0.32.0 → 0.53.0, which resolves `starlette` to 1.7.0 (was transitively pinned to the vulnerable 0.38.6), closing 7 open advisories. No app code changes were required.
- `ruff` pinned to `0.8.4` in CI (was unpinned `pip install ruff`) and `backend/ruff.toml` added to make the enforced rule set (`E4`, `E7`, `E9`, `F`) explicit instead of implicit in tool defaults.
- CI now runs a dependency audit on every backend and frontend build: `pip-audit` for `backend/requirements.txt` and `npm audit --omit=dev --audit-level=high` for the frontend.
- Frontend npm audit findings fixed (see frontend changes for detail).
