# Phase 1: Foundations Design

- **Date:** 2026-09-27
- **Status:** Draft — decisions recorded 2026-09-27 (rev 3)
- **Builds on:** Phase 0 (`fix/phase0-security`, unmerged): admin routes gated, CORS parsing, dependency bumps, ruff 0.8.4 pin, pip-audit and npm audit in CI.

## Goal

Make the repo healthy and operations reliable before data or model work. Concretely: the toolchain follows the owner's norms, the schema is under migration control, the nightly job cannot fail silently, config is explicit, deploys are gated on CI, the site is back up (maintenance page, then the app), and a stranger can understand the repo.

## Non-goals

- Algorithm, data-model, or retraining changes.
- Converting the whole frontend to TypeScript at once.
- Leaving Railway or Neon.
- New features.

### Out of scope → Phase 2a (data repair)

The 2026-09-27 data audit found data-quality problems. Phase 1 does not touch row contents; Phase 2a owns all of these:

- Corrupted years in AAC accident records.
- Misgeocoded accidents.
- Weather rows linked to the wrong accident or location.
- Relinking accidents from legacy `routes`/`mountains` to `mp_routes`/`mp_locations`, and the migration that then drops `routes` and `mountains` (D8).

Phase 1 schema work is limited to the baseline, fixing the stale `weather_patterns` doc references, and dropping the empty `ascents`/`climbers` tables.

## Decisions (recorded 2026-09-27)

All nine are **DECIDED** by the owner.

| # | Decision | Rationale |
|---|---|---|
| D1 | **Apache-2.0** for code, plus `DATA_LICENSE.md` | Permissive, with an explicit patent grant. It covers code only: `DATA_LICENSE.md` states OpenBeta data is CC0; Mountain Project (MP) ice and mixed route *facts* (name, grade, location, type) are displayed as facts but not bulk-redistributed; all other MP-derived data (`mp_*`, including rock data and tick aggregates) is internal-only and never committed (Phase 2 P2-1). |
| D2 | **healthchecks.io, free tier**, for nightly-job alerting | A dead-man's switch alerts when a ping does *not* arrive, which is the failure we had. |
| D3 | **Railway GitHub auto-deploy with "Wait for CI"** | No deploy token to store, Railway builds the commit CI tested, and the placeholder deploy job goes away. |
| D4 | **`MAINTENANCE_MODE` build arg** in the frontend image | Ships through the GitHub path (avoiding the `railway up` TLS BadRecordMac failure) and flips back with one variable. |
| D5 | **Remove the unused `Redis` service**, only after verifying no clients and no keys | Procedure in §4. |
| D6 | **Keep `docker-compose.yml`** for local dev, mirroring the Railway topology; **delete `docker-compose.prod.yml`** | Local parity without a second, stale prod definition. |
| D7 | **`www` as a Railway custom domain** plus an nginx 301 to the apex | Works with any registrar. |
| D8 | **Delete the outdated legacy models and tables**, in two stages | Read-only audit (2026-09-27): `ascents` and `climbers` have 0 rows, so Phase 1 drops both tables and their ORM models. `routes` (778 rows) and `mountains` (441 rows) are legacy but still referenced: `accidents.route_id` → `routes` via `accidents_route_id_fkey` (1,183 accidents linked), and `accidents.mountain_id` → `mountains` (declared in `app/models/accident.py`). Their drop is deferred to Phase 2a's migration, after it relinks accidents to `mp_routes`/`mp_locations`. See §2. |
| D9 | **No scraper code in any GitHub repo, ever** | Scrapers (anything fetching and parsing HTML pages, not a documented API) live only in local private directories with no git remote, e.g. `~/Developer/safeascent-private/`. Clients for open APIs (OpenBeta GraphQL, Open-Meteo, NOAA, USGS, Macrostrat, NWS, AirNow, SNOTEL) are not scrapers and live in the repo. Enforced by the §5 CI guard. |

## Design

### 1. Python toolchain: uv

- **pyproject:** `backend/pyproject.toml` plus a committed `uv.lock` replace `requirements.txt`, keeping the Phase 0 pins. The dev group holds pytest, pytest-asyncio, ruff==0.8.4, mypy, and pip-audit. `ruff.toml` and `pytest.ini` fold into `[tool.*]` sections.
- **Docker:** a single `backend/Dockerfile` serves the api, worker, and beat, which differ only by start command, so `Dockerfile.worker` is deleted. The builder copies the pinned `ghcr.io/astral-sh/uv` binary and runs `uv sync --frozen --no-dev --no-install-project`. The runtime stays `python:3.12-slim` running as a non-root user.
- **CI:** `astral-sh/setup-uv`, then `uv sync --frozen`, then `uv run` for ruff, mypy, and pytest. The audit step is `uv export --no-dev | pip-audit -r /dev/stdin --no-deps`.
- **Files:** `backend/{pyproject.toml,uv.lock,Dockerfile}` and `ci.yml`. Deleted: `Dockerfile.worker`, `requirements.txt`, `pytest.ini`, `ruff.toml`.

### 2. Alembic baseline

1. **Create the Neon roles via SQL, never via neonctl, the Console, or the API.** Neon auto-grants any role created through those paths membership in `neon_superuser` (CREATEROLE, CREATEDB, broad privileges); verified 2026-09-27.
   - `analyst` (SELECT only, `default_transaction_read_only=on`) already exists, created this way. An INSERT was verified denied. Its credentials live in the gitignored `backend/.env.analyst`.
   - `migrator` (DDL, used only for migrations) and `app` (read-mostly DML) follow the same procedure:
     1. Generate each password locally (`openssl rand -hex 32`, 256 bits — no URL escaping needed) straight into a gitignored env file (`backend/.env.migrator`, `backend/.env.app`). Never print or echo it.
     2. Connected as the owner, run `create_roles.sql`, which reads each password's SCRAM-SHA-256 verifier (not the plaintext) from `MIGRATOR_PASSWORD_SCRAM`/`APP_PASSWORD_SCRAM` via psql `\getenv` and refuses any value that is not a verifier. The verifier is computed locally (`scripts/write_role_url.py --scram`, stdlib PBKDF2/HMAC) in the same subshell that loads the plaintext password, so the plaintext itself is never sent to the server, never appears in `pg_stat_statements`, and never reaches Neon's logs.
     3. Grant: `migrator` gets `CREATE` on schema `public` and ownership of every non-extension table/view/sequence/routine/enum/domain in `public` (transferred with `ALTER ... OWNER TO migrator`). `app` is read-mostly, narrower than originally planned: `SELECT` on every table except `alembic_version`, plus `INSERT, UPDATE, DELETE` only on `historical_predictions` and `USAGE` on its id sequence — the nightly upsert/purge are `app`'s only writes. `ALTER DEFAULT PRIVILEGES FOR ROLE migrator` makes tables `migrator` creates later `SELECT`-only for both `app` and `analyst`; a migration that needs `app` writes on a new table grants them per table.
     4. Verify (`verify_roles.sql`, exits non-zero on any miss): no superuser/createdb/createrole/replication/bypassrls; `NOINHERIT` on `migrator`/`app` and no memberships; only `CURRENT_USER` is a member of `migrator`; nobody can `SET`/inherit `app`; schema/database `CREATE`, ownership, and exact per-table `app` DML; `analyst` has no `INSERT/UPDATE/DELETE/TRUNCATE`; sequence grants; `pg_default_acl` is exactly `SELECT` to `app` and `analyst`.

   > **Revised 2026-09-28 (owner decision, PR5 review):** the two paragraphs above replace the original design, which planned a wider `app` grant (`SELECT, INSERT, UPDATE, DELETE` on all app tables) and plaintext `CREATE ROLE ... PASSWORD` over a psql variable. As built, `app` writes only `historical_predictions`, and no plaintext password ever reaches the server — only its SCRAM-SHA-256 verifier does.
   - Every later role uses this same SQL procedure, never neonctl: `ingest` (Phase 2 pipelines), `trainer` (Phase 3 training) and `triage_worker` (Phase 4). The full role set is `analyst`, `migrator`, `app`, `ingest`, `trainer`, `triage_worker`.
   - The app's `DATABASE_URL` on the Railway `api`, `worker`, and `beat` services switches from `neondb_owner` to `app` (see the relaunch gate in Rollout).
2. As `analyst`, run `pg_dump --schema-only` on prod. Autogenerate from the models into a scratch DB and diff the two.
3. Write `0001_baseline` so it reproduces the **live** schema, including PostGIS, indexes, and the legacy `routes`/`mountains` tables with their FKs from `accidents`. Where models and DB disagree, the DB wins.
4. Resolve known drift:
   - The `Weather` model's `__tablename__ = "weather"` matches the live table (verified 2026-09-27; `weather_patterns` exists only in stale docs). Fix `data/DATABASE_STRUCTURE.md` and other docs that say `weather_patterns`; no model or data change.
   - **D8, stage 1:** migration `0002_drop_ascents_climbers` drops the `ascents` and `climbers` tables (0 rows each, per the audit; the migration asserts `count(*) = 0` before dropping and aborts otherwise). Delete `app/models/ascent.py` and `app/models/climber.py` and their exports in `app/models/__init__.py`.
   - **D8, stage 2 (Phase 2a, not here):** `routes` and `mountains` stay, with `accidents.route_id` and `accidents.mountain_id` and their FKs untouched. Phase 2a's migration first preserves the link (maps accidents to `mp_routes`/`mp_locations`, or copies the legacy ids into plain columns), then drops the FKs, then drops the tables.
5. On prod, run `alembic stamp 0001_baseline` as `migrator`, then `uv run alembic upgrade head` to apply `0002`. Migrations always run as an explicit step with `MIGRATOR_DATABASE_URL`, never on API boot, to avoid replica races.

**Files:** `backend/alembic.ini`, `backend/alembic/**` (async `env.py`), `backend/app/models/*.py`.

### 3. Celery ops

**Root cause.** The worker ran `worker --beat --concurrency=1`. Its consumer hung silently from 2026-08-27, while the embedded beat kept publishing the 02:00 UTC task. Each message passed `expires=28800` and was discarded with nothing logged above INFO.

- **Split.** Two Railway services run from one image:
  - `worker`: `celery -A app.celery_app worker --concurrency=1 -E`.
  - `beat`: `celery -A app.celery_app beat`, one replica only, configured by the new `backend/railway-beat.toml`.

  Delete `run_celery_*.sh`.
- **Liveness.** A heartbeat bootstep writes `celery:worker:heartbeat` to Redis every 60s with a 180s TTL. A new `/health/worker` API route returns 503 when the key is missing. Set `broker_heartbeat` and `worker_cancel_long_running_tasks_on_connection_loss=True`.
- **Dead-man's switch (D2).**
  - The nightly task pings `HC_PING_URL` at `/start`, on success, and at `/fail`. The check allows 24h plus 4h grace.
  - A beat-scheduled `heartbeat` task pings a second check every 15 min (60 min grace), so a dead consumer alerts within 75 min instead of the next night.
  - When `HC_PING_URL` is unset, pings are skipped with a WARNING, so local runs and CI make no outbound calls.
- **Expired tasks.** A `task_revoked` handler logs at ERROR (name, id, expired flag) and increments `celery:expired:<task>`. `/health/worker` reports the 7-day count.
- **Files:** `backend/app/{celery_app.py,celery_signals.py (new, typed),main.py}`, `tasks/safety_computation_optimized.py`, `railway-{worker,beat}.toml`, `tests/test_celery_signals.py`.

### 4. Config hygiene

- **Settings is the single source.**
  - `OPEN_METEO_API_KEY`, `USE_VECTORIZED_ALGORITHM`, `SKIP_WEATHER_STATISTICS`, and `HC_PING_URL` move into `Settings`, replacing the direct `os.getenv` calls.
  - `.env.example` lists exactly the `Settings` fields plus the `VITE_*` args. It drops the unused `SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS` and adds `CACHE_REDIS_URL`, `CELERY_*`, `ENABLE_ADMIN_ROUTES`, and `ENVIRONMENT`.
  - A parity test fails CI on drift.
- **Safer defaults.** `ENVIRONMENT` defaults to `"production"`. SQL echo comes from an explicit `SQL_ECHO: bool = False`. `DATABASE_URL` has no default: it is required and startup fails loudly without it, which removes the hardcoded `sebastianfrazier@localhost` URL.
- **Stale files.** Delete `.do/`, `docker-compose.prod.yml`, and the root `railway.toml` (a duplicate of `backend/railway.toml`), after confirming in the dashboard which config path each service uses.
- **docker-compose (D6).** Services: `db` (postgis), `redis`, `api`, `worker`, and `beat`, with the same commands as Railway.
- **Redis (D5).**
  1. Confirm every service's `REDIS_URL`, `CACHE_REDIS_URL`, and `CELERY_*` point to `Redis-mxVE`.
  2. Check `CLIENT LIST` and `INFO keyspace` on `Redis` for zero clients and zero keys.
  3. Snapshot `Redis`, then delete it.
- **Files:** `backend/app/{config.py,db/session.py}`, the getenv call sites, `.env.example`, `docker-compose.yml`, `tests/test_config_and_admin_routes.py`.

### 5. Deploy pipeline (D3)

- **Railway:** enable "Wait for CI" on the `api`, `worker`, `beat`, and `frontend` services, tracking `main`. Watch paths are `backend/**` and `frontend/**`.
- **CI:** delete the `build-images` job (Railway builds its own images, so GHCR is unused) and the placeholder `deploy` job. Add a `ci-ok` job that `needs:` every job and serves as the one required check.
- **No-scraper guard (D9):** a `no-scrapers` job (in `ci-ok`'s `needs:`) runs `scripts/check_no_scrapers.py`, which fails when any tracked file:
  - has a basename matching `scrape*` or `*scraper*` (case-insensitive);
  - is source (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.ipynb`, `.sh`) containing the string `mountainproject.com`;
  - imports an HTML-parsing or browser-automation library (`bs4`, `lxml.html`, `html5lib`, `selectolax`, `parsel`, `scrapy`, `selenium`, `playwright`), or `uv.lock`/`package-lock.json` lists one of them.
  The allowlist is empty; adding an entry needs an owner-approved PR. The script itself and its test are exempt from the name rule by exact path.
- **Branch protection on `main`:** require a PR and a green `ci-ok`, allow no force-push, and include admins. The owner applies it with `gh api -X PUT .../branches/main/protection`, since that is a mutating command.
- **Docs:** document all of the above in `DEPLOYMENT.md`.

### 6. Frontend

- **TypeScript, incrementally.**
  - Add `typescript` and `typescript-eslint`.
  - `tsconfig.json`: `allowJs`, `strict`, `noEmit`, `jsx: react-jsx`, `moduleResolution: bundler`.
  - `src/vite-env.d.ts` types the `VITE_*` env vars.
  - A `typecheck` script (`tsc --noEmit`) runs in CI.
  - New files are TS, and touched files get converted. First up: `services/api.js` (becomes `api.ts` with a typed `SafetyResponse`) and `utils/riskUtils.js`.
- **The "Risk 0.0" bug.** The cause is `frontend/src/components/MapView.jsx:1257`, `risk_score: safetyData.risk_score || 0`. On a fetch failure `safetyData` is `{ error }`, so the modal shows 0.0.
  - Replace it with a typed union: `loading | error(message) | ok(data)`.
  - `RouteAnalyticsModal` and `PredictionResult` render an error Alert with a Retry button when the state is `error`.
  - Remove every `|| 0` on risk fields. A missing score shows "Unavailable".
- **www TLS (D7).** Add `www.safeascent.us` as a custom domain on the Railway frontend service and create the CNAME Railway provides. In nginx, a `server_name www.safeascent.us` block returns `301 https://safeascent.us$request_uri`.
- **Maintenance (D4).** `frontend/Dockerfile` gets `ARG MAINTENANCE_MODE=false`. When it is `true`, the image serves `frontend/maintenance/index.html` (ported from the prepared `maint2` files) with an nginx config that returns 503 plus `Retry-After`. The switch is a Railway variable.
- **Files:** `frontend/{package.json,tsconfig.json,eslint.config.js,Dockerfile,nginx.conf}`, `frontend/maintenance/*`, `src/{vite-env.d.ts,services/api.ts,utils/riskUtils.ts}`, `src/components/{MapView,RouteAnalyticsModal,PredictionResult}.jsx`.

### 7. Repo hygiene

- **`CLAUDE.md` (root):** commands (`uv sync`, `uv run pytest|ruff check|mypy|alembic upgrade head`, `npm ci`, `npm test`, `npm run typecheck`), service topology, migration policy, the TS-as-touched rule, the comment and changelog norms, and the data rules:
  - **No scraper code is ever committed or published to GitHub (any repo)** (D9). Scrapers live in `~/Developer/safeascent-private/` (local only, no remote); open-API clients live in the repo.
  - MP data rules per Phase 2 P2-1: only ice/mixed route facts are displayable; no MP prose ever; no MP data in fixtures, docs or commits.
  - DB roles are created only via SQL (§2).
- **README rewrite:** the point of the project, honest status ("offline for rebuild, Sept 2026"), architecture, local quickstart, and data sources with their licenses. Add `LICENSE` (D1) and `DATA_LICENSE.md`.
- **Junk removal.**
  - Delete `backend/tests/test_prediction_integration.py.backup`, `backend/check_weather_gaps.py`, `backend/test_weather_service.py`, `backend/test_weather_stats_db.py`, and `backend/test_request.json`.
  - Move `tests/benchmark_*` and `tests/profile_*` to `backend/scripts/perf/`.
  - Make `.gitignore` cover `htmlcov/`, `dist/`, and `__pycache__/`.
- **Docs drift.** Update React 18 → 19 (README:43, DEPLOYMENT.md:44) and Python 3.11 → 3.12 (DEPLOYMENT.md:55). Regenerate `system_design.png` (split worker/beat, one Redis) and `data_model.png` (after the baseline). Rewrite `DEPLOYMENT.md` to cover Railway only.
- **mypy:** `strict` applies to an allowlist (`app.config`, `app.celery_signals`, and new modules), with `ignore_errors` everywhere else. The list grows as files are touched.
- **CHANGELOG:** one dated entry per merged PR.

## Rollout order

1. **PR1: maintenance mode + www domain.** Requires Phase 0 to be merged. Puts an honest page back up.
2. **PR2: uv migration.**
3. **PR3: CI rework.** Needs PR2. Branch protection turns on immediately after it merges.
4. **PR4: config hygiene.** Redis and `.do/` removal follow the dashboard checks.
5. **PR5: Alembic + DB roles.** Needs PR4, for the required `DATABASE_URL`. Includes `0002_drop_ascents_climbers`; `routes`/`mountains` wait for Phase 2a.
6. **PR6: Celery split + liveness + alerts.** Needs PR4. Watch one clean nightly run before turning maintenance off.
7. **PR7: TypeScript setup + the Risk 0.0 fix.**
8. **PR8: hygiene and docs.** Can run in parallel with PR6 and PR7. Adds the Apache-2.0 `LICENSE` and `DATA_LICENSE.md` (D1).

The `no-scrapers` guard (D9) lands in PR3 with the CI rework, so it is required from the moment branch protection turns on.

**Relaunch gate** (all must hold before maintenance mode is turned off):

1. The owner has rotated the `neondb_owner` password, which leaked in public git history.
2. `DATABASE_URL` on the Railway `api`, `worker`, and `beat` services is updated and points at the least-privilege `app` role, not `neondb_owner`. `MIGRATOR_DATABASE_URL` is set only where migrations run.
3. PR6's clean nightly run has been observed.

## Testing and verification

- **uv:**
  - `uv sync --frozen` and the api/worker `docker build` succeed in CI.
  - The existing pytest subset passes unchanged.
  - pip-audit is clean.
- **Alembic:**
  - `upgrade head` on an empty postgis DB produces a schema dump that matches prod's normalized dump, with any residual diffs listed in the PR.
  - `alembic check` is clean.
  - Prod `alembic_version` is `0002_drop_ascents_climbers`, and `ascents`/`climbers` no longer exist while `routes`/`mountains` and the `accidents` FKs are intact.
- **Roles:**
  - For `analyst`, `migrator`, and `app`, `pg_auth_members` shows no memberships (in particular none in `neon_superuser`).
  - `has_table_privilege` checks match the intended grants; `analyst` INSERT and `app` DDL/TRUNCATE are denied.
  - The old owner password no longer authenticates after rotation.
- **Celery:**
  - Unit tests cover the heartbeat writer, `/health/worker` returning 200 or 503, and the expired handler (ERROR log plus counter).
  - Staging drills: stopping the worker triggers an alert within 75 min and a 503; a task sent with `expires=1` to a paused worker is logged and counted.
- **Config:**
  - The parity test passes.
  - Importing config without `DATABASE_URL` raises.
  - Echo is off by default.
- **Deploy:**
  - A red PR cannot merge.
  - A red `main` commit does not deploy (Railway shows it waiting for CI).
- **Frontend:**
  - `npm run typecheck` passes.
  - A Vitest case rejects the safety fetch and asserts the error Alert renders and no `0.0` text appears.
  - `curl -I https://www.safeascent.us` returns a 301 with a valid cert.
  - The maintenance build returns 503.
- **Hygiene:**
  - `git ls-files` shows no junk.
  - `check_no_scrapers.py` fails on a test branch that adds `tools/route_scraper.py`, a file containing `mountainproject.com`, or a `bs4` import, and passes on `main`.
  - The README quickstart works from a fresh clone.

## Risks

- **Baseline mismatches prod.** *Mitigation:* the baseline is only stamped, never applied, on prod, and later revisions are rehearsed on a Neon branch first.
- **A role created through neonctl/Console/API silently gains `neon_superuser`.** *Mitigation:* roles are created only via SQL, and the `pg_auth_members` check is part of verification.
- **The least-privilege `app` role misses a write path.** *Mitigation:* derive grants from `pg_stat_statements` and test on a Neon branch.
- **"Wait for CI" hangs if check names change.** *Mitigation:* keep a single stable `ci-ok` check.
- **The wrong Redis gets deleted.** *Mitigation:* the §4 checks plus a snapshot. The broker holds nothing durable.
- **healthchecks.io goes down.** It is on the alert path only; the app never depends on it.
- **The uv image differs from the pip image.** *Mitigation:* a CI docker build plus a staging deploy before `main`.

## Acceptance criteria

- **Toolchain:** the backend installs via `uv sync --frozen` from a committed lockfile, `requirements.txt` is gone, and all backend services build from `backend/Dockerfile`.
- **Database:**
  - Prod is at `0002_drop_ascents_climbers` and `alembic check` is clean.
  - The models match the live tables (including `weather`); `ascents`/`climbers` tables and models are gone (D8 stage 1).
  - The `app`, `migrator`, and `analyst` roles exist, were created via SQL, and have no role memberships.
- **Relaunch gate:** the `neondb_owner` password is rotated, and the Railway `api`, `worker`, and `beat` services connect as `app`.
- **Celery:**
  - `worker` and `beat` are separate services, and `/health/worker` reports the heartbeat.
  - Expired tasks log at ERROR and are counted.
  - A stopped worker alerts the owner within 75 min.
  - One nightly run completes with a success ping.
- **Config:** no hardcoded DB URL, echo is off by default, `.env.example` parity is enforced, and `.do/`, `docker-compose.prod.yml`, the root `railway.toml`, and the second Redis are gone.
- **Deploy:** Railway deploys only after green CI, `main` requires a PR plus `ci-ok`, and there is no placeholder deploy job.
- **Frontend:**
  - `tsc --noEmit` runs in CI.
  - A failed fetch shows an error, never a number.
  - `www` serves a valid cert and redirects.
  - `MAINTENANCE_MODE=true` builds the maintenance page.
- **Repo:**
  - `CLAUDE.md`, the README, `LICENSE`, `DATA_LICENSE.md`, and CHANGELOG entries are present.
  - The junk is removed and the docs match React 19 and Python 3.12.
  - mypy strict passes on the allowlist.
  - The `no-scrapers` guard is part of `ci-ok`, and `CLAUDE.md` states the no-scraper rule.

## Open items (owner decisions pending)

1. **Git history rewrite.** The public repo's history already contains 18 old scraper files and a leaked Neon password. Rewriting it (`git filter-repo`, then a force push to `main`) is an owner decision and is **not** planned as done here. Password rotation (relaunch gate 1) is required regardless, because a rewrite cannot un-publish clones or forks. If the owner approves a rewrite, it runs before branch protection's no-force-push rule is applied, or with that rule lifted temporarily by the owner.
