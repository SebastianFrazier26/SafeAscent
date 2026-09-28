# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] - 2026-09-24

### Security (Phase 0)

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

### Phase 1 PR1 — 2026-09-27

- `frontend/Dockerfile` gains a `MAINTENANCE_MODE` build arg (`true`|`false`, default `false`; any other value fails the build). `false` builds and serves the app as before; `true` serves a static 503 page (`frontend/maintenance/index.html`, `frontend/maintenance/nginx.conf`) instead, so Railway can flip a service variable and redeploy to take the frontend offline without touching DNS.
- New image-level test entrypoint `frontend/docker-tests/test_images.sh [true|false|all]` builds the image and asserts the nginx contract (health check, www→apex redirect, maintenance-mode 503s with `Retry-After`/`Cache-Control`, app-mode 200s). Only maintenance mode (`... true`) is exercised by this PR's build/test steps; the `false`/app-mode www-redirect assertion currently fails because `frontend/nginx.conf` (unchanged in this PR) doesn't implement it yet.
