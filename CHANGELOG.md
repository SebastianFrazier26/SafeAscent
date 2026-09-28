# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Phase 1 PR3 — 2026-09-27

- No-scraper CI guard (D9): `scripts/check_no_scrapers.py` (stdlib only) scans every `git ls-files`-tracked path for scraper code — file names matching `scrape*`/`*scraper*`, mentions of banned climbing-data host names in source, banned Python/JS HTML-parsing or browser-automation imports, and those same packages in `backend/uv.lock`/`frontend/package-lock.json` — and exits 1 with one `path: [rule] detail` line per hit. Scrapers stay in the private local workspace, never this repo. `backend/tests/test_check_no_scrapers.py` covers all four rules plus the git-tracked-files-only behavior. `.gitignore`'s blanket `scripts/` rule narrowed to `/scripts/*` with an exception for the guard file, so the script itself can be tracked; other one-off scripts stay ignored.
- Fix round 1 (review): the `import`/`host` rules now match case-insensitively (`import BS4` resolves to the real `bs4` package on macOS's case-insensitive filesystem); `cheerio` and `jsdom` added to the banned JS import list; the banned-host set expanded from just `mountainproject.com` to `mountainproject.com`, `thecrag.com`, `8a.nu`, `ukclimbing.com`, matched with word-boundary guards so `8a.nu` doesn't misfire on lookalike strings like `v8a.number`. `jsdom` is deliberately kept out of the npm-lockfile banlist (same precedent as `lxml` for Python) — it's a mainstream Vitest/Jest DOM test-environment devDependency (this repo's own `frontend/package-lock.json` has it), not a scraping tool; its actual *use* is still caught by the import rule.
- CI rebuilt into `backend`, `frontend`, `guards` and a single required `ci-ok` job; the unused GHCR `build-images` job and placeholder `deploy` job are removed (Railway builds from GitHub with "Wait for CI").
- Backend CI runs the whole default suite (`uv run pytest`); tests needing production data or live services are marked `needs_data` and deselected by default.
- The `guards` job runs the no-scraper check above in CI.
- mypy runs in CI.

### Phase 1 PR2 — 2026-09-27

- Backend dependency management moved from `requirements.txt`/`pip` to `uv`: `backend/pyproject.toml` (`[project]` deps + `[dependency-groups] dev`) and `backend/uv.lock` replace `requirements.txt`; `pytest.ini` and `ruff.toml` settings moved into `[tool.pytest.ini_options]` and `[tool.ruff]` in `pyproject.toml`. All Phase 0 pins carried forward unchanged (fastapi 0.141.1, uvicorn 0.53.0, requests 2.34.2, python-dotenv 1.2.3, pydantic-settings 2.15.0, pytest 9.1.1, pytest-asyncio 1.4.0, python-multipart 0.0.32).
- `[tool.mypy]` added with `plugins = ["pydantic.mypy"]`, an `app.*` allowlist that ignores errors for untyped legacy code, and an empty strict-allowlist override block (`module = []`) for later PRs to append to as modules are typed.
- `mypy==2.3.1` and `pip-audit==2.10.1` added to the `dev` dependency group.
- README backend quickstart updated to `uv sync` / `uv run uvicorn ...` / `uv run pytest`.
- One `backend/Dockerfile` (uv-built, non-root) serves the api, worker and beat; `Dockerfile.worker` removed. `backend/railway-worker.toml` now points at the shared `Dockerfile`; the worker/beat process split stays in `startCommand` (unchanged, part B of this PR).
- CI (`.github/workflows/ci.yml`) now installs with `uv sync --frozen`, lints and audits (`uv export --no-dev` piped to `pip-audit`) via `uv run`, matching the local toolchain instead of a separate `pip`/`pip-audit` install.
- New pytest marker `needs_data` (registered in `[tool.pytest.ini_options]`, deselected by default via `addopts -m 'not needs_data'`) marks the tests that need a populated database or live Redis/network, applied at whatever granularity is accurate — module-level in `test_daily_variation_yosemite.py`, `test_known_outcomes_validation.py`, `test_longs_peak_daily.py`; per-class or per-test in `test_edge_cases_performance.py`, `test_predict_integration.py`, `test_prediction_integration.py`, `test_weather_caching.py`, `test_performance.py`, so pure-validation and pure-function tests in those files still run by default. Plain `cd backend && uv run pytest` is now green (203 passed, 19 skipped, 80 deselected) with no DB/Redis/network required; run `uv run pytest -m needs_data` to exercise the deselected tests.
- Commands: `cd backend && uv sync`, `uv run pytest`, `uv run pytest -m needs_data`, `uv run ruff check app/`, `uv run mypy`.

### Phase 1 PR1: maintenance mode and www redirect (2026-09-27)

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
