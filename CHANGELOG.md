# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Phase 1 PR2 — 2026-09-27

- Backend dependency management moved from `requirements.txt`/`pip` to `uv`: `backend/pyproject.toml` (`[project]` deps + `[dependency-groups] dev`) and `backend/uv.lock` replace `requirements.txt`; `pytest.ini` and `ruff.toml` settings moved into `[tool.pytest.ini_options]` and `[tool.ruff]` in `pyproject.toml`. All Phase 0 pins carried forward unchanged (fastapi 0.141.1, uvicorn 0.53.0, requests 2.34.2, python-dotenv 1.2.3, pydantic-settings 2.15.0, pytest 9.1.1, pytest-asyncio 1.4.0, python-multipart 0.0.32).
- `[tool.mypy]` added with `plugins = ["pydantic.mypy"]`, an `app.*` allowlist that ignores errors for untyped legacy code, and an empty strict-allowlist override block (`module = []`) for later PRs to append to as modules are typed.
- `mypy==2.3.1` and `pip-audit==2.10.1` added to the `dev` dependency group.
- README backend quickstart updated to `uv sync` / `uv run uvicorn ...` / `uv run pytest`.
- One `backend/Dockerfile` (uv-built, non-root) serves the api, worker and beat; `Dockerfile.worker` removed. `backend/railway-worker.toml` now points at the shared `Dockerfile`; the worker/beat process split stays in `startCommand` (unchanged, part B of this PR).
- CI (`.github/workflows/ci.yml`) now installs with `uv sync --frozen`, lints and audits (`uv export --no-dev` piped to `pip-audit`) via `uv run`, matching the local toolchain instead of a separate `pip`/`pip-audit` install.
- Commands: `cd backend && uv sync`, `uv run pytest`, `uv run ruff check app/`, `uv run mypy`.

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
