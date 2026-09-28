# Phase 1a Foundations (PR1–PR4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put an honest maintenance page back up on `safeascent.us` (plus a `www` redirect), move the backend to uv, rebuild CI around a single `ci-ok` gate with a no-scraper guard, and make configuration explicit and fail-loud. These are rollout PRs 1–4 of Phase 1.

**Architecture:** Four sequential PRs, each on its own branch off `main`. PR1 adds a `MAINTENANCE_MODE` build arg to the frontend image that selects between the app and a static 503 page. Both nginx configs 301 `www` to the apex. PR2 replaces `requirements.txt`/`pytest.ini`/`ruff.toml` with `backend/pyproject.toml` plus `uv.lock`, and one backend `Dockerfile`. PR3 rewrites CI into `backend`, `frontend`, `guards` and `ci-ok` jobs so Railway ("Wait for CI") and branch protection key on one check. PR4 makes `app.config.Settings` the only reader of env vars, adds a `.env.example` parity test, and deletes stale deploy files. Railway dashboard, DNS and GitHub settings changes are **OWNER STEPS**. Agents never perform them.

**Tech Stack:** Python 3.12, FastAPI, Celery, pydantic-settings 2.15, uv 0.11.3, pytest 9.1.1, ruff 0.8.4, mypy 2.3.1, pip-audit 2.10.1; React 19 + Vite 7 served by `nginx:alpine`; Docker (BuildKit); GitHub Actions; Railway; Neon.

**Spec:** `docs/superpowers/specs/2026-09-27-phase1-foundations-design.md` (rev 3). Read it before starting. The sibling plan `docs/superpowers/plans/2026-09-27-phase1b-foundations-pr5-8.md` covers PR5–PR8 and consumes the interfaces this plan defines.

## Global Constraints

- **Base:** Phase 0 (`fix/phase0-security`, commit `2c30c8b`) must be merged into `main` before PR1 branches. Every task below is written against Phase 0's files, not the older `main`.
- **Task 0 before any merge:** no merge to `main` (Phase 0's included) until Task 0's owner steps are done. Railway auto-deploys `main`, and the site has been intentionally down since 2026-09-24.
- **Branches:** each PR gets its own branch off up-to-date `main`: `feat/p1-pr1-maintenance`, `chore/p1-pr2-uv`, `ci/p1-pr3-ci`, `chore/p1-pr4-config`. Never commit to `main`. PR3 needs PR2 merged. PR4 needs PR3 merged. PR1 and PR2 are independent of each other.
- **Push/PR:** agents commit locally only. Pushing happens only when the owner invokes `/commitandpush`. The owner opens and merges PRs. Agents never run `git push`, `gh pr create`, `gh pr merge` or `gh api -X`.
- **Secrets:** never commit secrets. `.env` files stay gitignored. `.env.example` holds dummy values only. Never print Railway/Neon variable values in a terminal or log.
- **No scraper code (D9):** no file that fetches and parses HTML pages, anywhere in the repo, ever. Open-API clients are fine.
- **Comments:** load-bearing only. Explain *why*, never narrate *what*. Match surrounding density.
- **CHANGELOG:** each PR adds one dated entry under `## [Unreleased]` in `CHANGELOG.md`, newest first. Use the day's date from `date +%F`. The text below uses `2026-09-27`; replace it if you execute on a later day.
- **Shared interfaces (consumed by part B, names are fixed):**
  - `app.config.Settings` fields: `ENVIRONMENT: Literal["development","test","production"] = "production"`, `SQL_ECHO: bool = False`, `DATABASE_URL: str` (required), `ENABLE_ADMIN_ROUTES: bool = False`, `HEALTHCHECKS_NIGHTLY_URL: str | None = None`, `HEALTHCHECKS_BEAT_URL: str | None = None`, `WORKER_HEARTBEAT_TTL_SECONDS: int = 120`.
  - The backend is managed by uv at `backend/pyproject.toml`, with dependency group `dev` = pytest, pytest-asyncio, pytest-cov, ruff==0.8.4, mypy, pip-audit.
  - The one test command is `cd backend && uv run pytest`.
  - CI job names are `backend`, `frontend`, `guards` and `ci-ok`. `ci-ok` is the only required check.
  - The mypy strict allowlist is the `[[tool.mypy.overrides]]` block with `strict = true` in `backend/pyproject.toml`. Part B appends modules to its `module` list.
  - Pytest marker `needs_data` marks tests that need a populated DB or live services. They are deselected by default.
- **Tooling pins:** uv `0.11.3` (Docker `ghcr.io/astral-sh/uv:0.11.3`, CI `astral-sh/setup-uv@v10` with `version: "0.11.3"`), Python `3.12`, Node `22`.

## File Structure

| Path | PR | Responsibility |
|---|---|---|
| `frontend/Dockerfile` | 1 | Multi-stage image. `ARG MAINTENANCE_MODE` selects the final stage `site-false` (app) or `site-true` (maintenance). |
| `frontend/maintenance/index.html` | 1 | Self-contained maintenance page (no external assets). |
| `frontend/maintenance/nginx.conf` | 1 | 503 + `Retry-After` + `Cache-Control: no-store` for every path. `/health` returns 200. `www` 301s to the apex. |
| `frontend/nginx.conf` | 1 | App config, plus the `www` → apex 301 server block and an exact-match `/health`. |
| `frontend/docker-tests/test_images.sh` | 1 | Builds both image modes and asserts the HTTP contract with curl. |
| `frontend/railway.toml` | 1 | Healthcheck path moves to `/health`, because `/` is 503 in maintenance. |
| `frontend/.dockerignore` | 1 | Keeps `docker-tests/` out of the build context. |
| `DEPLOYMENT.md` | 1, 3 | Appends "Maintenance mode" (PR1) and "Deploy pipeline" (PR3) sections. Part B rewrites the rest. |
| `backend/pyproject.toml`, `backend/uv.lock` | 2 | Dependencies, dev group, pytest/ruff/mypy config. |
| `backend/Dockerfile` | 2 | One uv-built image for the api, worker and beat. |
| `backend/railway-worker.toml` | 2 | Points at `Dockerfile`. |
| `backend/tests/test_*.py` (8 data-dependent modules) | 3 | Marked `needs_data`. |
| `scripts/check_no_scrapers.py` | 3 | Stdlib-only D9 guard over `git ls-files`. |
| `backend/tests/test_check_no_scrapers.py` | 3 | Guard tests, run by the normal backend suite. |
| `.gitignore` | 3 | Un-ignores `scripts/check_no_scrapers.py`. |
| `.github/workflows/ci.yml` | 2, 3 | PR2: switch installs to uv. PR3: rewrite into `backend`/`frontend`/`guards`/`ci-ok`. |
| `backend/app/config.py` | 4 | `Settings` holds every env var. `DATABASE_URL` is required. Safe defaults. |
| `backend/app/db/session.py` | 4 | `echo=settings.SQL_ECHO`. |
| `backend/app/api/v1/predict.py`, `backend/app/services/weather_service.py` | 4 | Read `settings` instead of `os.getenv`. |
| `backend/tests/conftest.py` | 4 | Sets a test `DATABASE_URL`/`ENVIRONMENT` before `app` imports. |
| `backend/tests/test_settings.py` | 4 | Defaults, required field, fail-loud import, no-`os.getenv` rule. |
| `backend/tests/test_env_example_parity.py` | 4 | `.env.example` keys == `Settings` fields + `VITE_*`. |
| `.env.example` | 4 | Exactly the Settings fields plus the frontend build args. |
| `docker-compose.yml` | 4 | `db`, `redis`, `api`, `worker`, `beat`, `frontend`, with Railway's start commands. |
| Deleted: `backend/requirements.txt`, `backend/pytest.ini`, `backend/ruff.toml`, `backend/Dockerfile.worker` | 2 | Folded into pyproject/Dockerfile. |
| Deleted: `.do/`, `docker-compose.prod.yml`, root `railway.toml` | 4 | Stale deploy definitions. |

---

## Task 0: OWNER — freeze Railway auto-deploy before any merge to `main`

**Why:** the Railway services `frontend`, `backend` and `celery-worker` have had no active deployment since 2026-09-24 (site intentionally down). Railway auto-deploys from GitHub `main`, so merging anything (even Phase 0) would silently rebuild and relaunch the old app against the leaked-credential database. Nothing below may merge until this task is checked off.

**Files:** none (dashboard/CLI only).

- [ ] **Step 1 (OWNER STEP): Record current service settings.** For each of `frontend`, `backend` and `celery-worker`, open Railway dashboard → project → service → **Settings**. Screenshot or note: Source (repo, branch), Root Directory, Config-as-code file path (Railway Config File), Watch Paths, Builder, Networking (custom domains, target port). You need these to reconnect later and to answer Task 12 Step 1.

- [ ] **Step 2 (OWNER STEP): Link the CLI (read-only use).**

Run from the repo root: `railway link` (interactive: pick the SafeAscent project and the production environment).
Then: `railway deployment list --service frontend --limit 5`, and the same for `backend` and `celery-worker`.
Expected: no deployment with status `SUCCESS`/`DEPLOYING` is active. Write down the newest deployment ID per service.

- [ ] **Step 3 (OWNER STEP): Disconnect the GitHub source on all three services.**

Either in the dashboard (service → Settings → Source → **Disconnect**) or:

```bash
railway service source disconnect --service backend
railway service source disconnect --service celery-worker
railway service source disconnect --service frontend
```

This is the recommended option. The alternative (switching each service's trigger branch to a branch that will never exist) leaves a live trigger behind. PR1's Task 3 reconnects `frontend` only once the maintenance image is on `main`. `backend` and `celery-worker` stay disconnected until the relaunch gate (part B).

- [ ] **Step 4 (OWNER STEP): Verify after the first merge.** After the next merge to `main` (Phase 0), wait 3 minutes, then re-run Step 2's three `railway deployment list` commands.
Expected: the newest deployment ID per service is unchanged. If a new deployment appears, remove it immediately in the dashboard (service → Deployments → the new deployment → ⋮ → **Remove**) and re-check Step 3.

- [ ] **Step 5 (OWNER STEP): Merge Phase 0.** Merge the `fix/phase0-security` PR, then repeat Step 4. From here on every PR in this plan branches off the updated `main`:

```bash
git switch main && git pull --ff-only && git log --oneline -1
```

Expected: the log shows the Phase 0 merge (or `2c30c8b` if fast-forwarded).

---

## PR1 — Maintenance mode + www domain (`feat/p1-pr1-maintenance`)

### Task 1: Maintenance-mode frontend image

**Files:**
- Create: `frontend/docker-tests/test_images.sh`
- Create: `frontend/maintenance/index.html`
- Create: `frontend/maintenance/nginx.conf`
- Modify: `frontend/Dockerfile` (full rewrite)
- Modify: `frontend/.dockerignore`

**Interfaces:**
- Consumes: the existing `frontend/nginx.conf` (served in app mode, unchanged in this task).
- Produces: the build arg `MAINTENANCE_MODE` (`true`|`false`, default `false`, any other value fails the build). The `/health` → 200 contract holds in both modes. Test entrypoint: `frontend/docker-tests/test_images.sh [true|false|all]`, exit 0 = pass. It needs Docker and curl and uses host port `18080` (override with `IMAGE_TEST_PORT`).

- [ ] **Step 1: Create the branch**

```bash
git switch main && git pull --ff-only && git switch -c feat/p1-pr1-maintenance
```

- [ ] **Step 2: Write the failing image test**

Create `frontend/docker-tests/test_images.sh`:

```bash
#!/usr/bin/env bash
# Builds the frontend image in both MAINTENANCE_MODE settings and asserts the
# nginx contract Railway relies on. Usage: frontend/docker-tests/test_images.sh [true|false|all]
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${IMAGE_TEST_PORT:-18080}"
NAME=safeascent-frontend-image-test
fail=0

check() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "$actual" == "$expected" ]]; then
    echo "PASS $desc"
  else
    echo "FAIL $desc: expected '$expected', got '$actual'"
    fail=1
  fi
}
status() { curl --silent --output /dev/null --write-out '%{http_code}' "$@"; }
header() {
  local name="$1"; shift
  curl --silent --output /dev/null --dump-header - "$@" | tr -d '\r' \
    | awk -v h="$name" 'tolower($0) ~ "^"tolower(h)":" {sub(/^[^:]*: /, ""); print; exit}'
}

start() {
  local mode="$1" image="safeascent-frontend-test:$1"
  docker build --quiet \
    --build-arg MAINTENANCE_MODE="$mode" \
    --build-arg VITE_API_BASE_URL=http://localhost:8000/api/v1 \
    --build-arg VITE_MAPBOX_TOKEN=pk.test_token_for_ci \
    -t "$image" . >/dev/null
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" -p "$PORT:80" "$image" >/dev/null
  for _ in $(seq 1 30); do
    curl --silent --output /dev/null "http://localhost:$PORT/health" && return 0
    sleep 0.5
  done
  echo "FAIL container never answered /health"; exit 1
}
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

base="http://localhost:$PORT"

test_common() {
  check "[$1] /health is 200" 200 "$(status "$base/health")"
  check "[$1] www redirects 301" 301 "$(status -H 'Host: www.safeascent.us' "$base/x?y=1")"
  check "[$1] www redirect target" "https://safeascent.us/x?y=1" \
    "$(header Location -H 'Host: www.safeascent.us' "$base/x?y=1")"
}

test_maintenance() {
  start true
  test_common maintenance
  for path in / /index.html /__maintenance/index.html /routes/123 /api/v1/predict /assets/app.js; do
    check "[maintenance] GET $path is 503" 503 "$(status "$base$path")"
  done
  check "[maintenance] POST is 503" 503 "$(status -X POST "$base/api/v1/predict")"
  check "[maintenance] HEAD is 503" 503 "$(status --head "$base/")"
  check "[maintenance] Retry-After" 3600 "$(header Retry-After "$base/some/path")"
  check "[maintenance] Cache-Control" no-store "$(header Cache-Control "$base/")"
  check "[maintenance] body is the maintenance page" yes \
    "$(curl --silent "$base/" | grep -q 'Offline for rebuild' && echo yes || echo no)"
}

test_app() {
  start false
  test_common app
  check "[app] / is 200" 200 "$(status "$base/")"
  check "[app] SPA deep link is 200" 200 "$(status "$base/routes/123")"
  check "[app] no Retry-After" "" "$(header Retry-After "$base/")"
}

case "${1:-all}" in
  true) test_maintenance ;;
  false) test_app ;;
  all) test_maintenance; test_app ;;
  *) echo "usage: $0 [true|false|all]"; exit 2 ;;
esac

exit "$fail"
```

Then: `chmod +x frontend/docker-tests/test_images.sh`

- [ ] **Step 3: Run it to verify it fails**

Run: `frontend/docker-tests/test_images.sh true`
Expected: exit 1. The old Dockerfile ignores the arg and builds the app, so the output includes `FAIL [maintenance] GET / is 503: expected '503', got '200'` and `FAIL [maintenance] www redirects 301: expected '301', got '200'`.

- [ ] **Step 4: Create the maintenance page**

Create `frontend/maintenance/index.html`. It is ported from the prepared `maint2` page, with the copy updated: no time promise, and an honest status line.

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="robots" content="noindex">
    <title>SafeAscent - Offline for rebuild</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 8px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            padding: 60px 40px;
            max-width: 500px;
            text-align: center;
        }
        h1 {
            color: #333;
            margin-bottom: 20px;
            font-size: 32px;
        }
        .maintenance-icon {
            font-size: 64px;
            margin-bottom: 30px;
        }
        p {
            color: #666;
            line-height: 1.6;
            margin-bottom: 20px;
            font-size: 16px;
        }
        .status {
            background: #fff3cd;
            border: 1px solid #ffc107;
            border-radius: 4px;
            padding: 15px;
            margin-top: 30px;
            font-size: 14px;
            color: #856404;
        }
        @media (max-width: 600px) {
            .container {
                padding: 40px 25px;
            }
            h1 {
                font-size: 24px;
            }
            .maintenance-icon {
                font-size: 48px;
            }
        }
    </style>
</head>
<body>
    <main class="container">
        <div class="maintenance-icon" aria-hidden="true">🔧</div>
        <h1>SafeAscent</h1>
        <p>SafeAscent is offline while it is rebuilt to improve the accuracy of its safety scores. Do not rely on any previously displayed scores.</p>
        <div class="status">Offline for rebuild, September 2026</div>
    </main>
</body>
</html>
```

- [ ] **Step 5: Create the maintenance nginx config**

Create `frontend/maintenance/nginx.conf`:

```nginx
server {
    listen 80;
    server_name www.safeascent.us;
    return 301 https://safeascent.us$request_uri;
}

server {
    listen 80 default_server;
    server_name _;
    root /usr/share/nginx/html;

    location = /health {
        access_log off;
        default_type text/plain;
        return 200 "healthy\n";
    }

    location / {
        return 503;
    }

    # A URI error_page (not a named location) makes nginx re-issue the request
    # as GET, so POST/PUT get the page too instead of a 405 from the static
    # handler.
    error_page 503 /__maintenance/index.html;

    location ^~ /__maintenance/ {
        internal;
        alias /usr/share/nginx/maintenance/;
        # A direct request hits `internal` and would 404; send it the page as 503.
        error_page 404 =503 /__maintenance/index.html;
        add_header Retry-After "3600" always;
        add_header Cache-Control "no-store" always;
        add_header X-Content-Type-Options "nosniff" always;
    }
}
```

- [ ] **Step 6: Rewrite the frontend Dockerfile**

Replace `frontend/Dockerfile` entirely:

```dockerfile
# MAINTENANCE_MODE picks the final stage: "false" serves the app, "true" serves
# the static 503 page. It is a Railway service variable, so flipping it and
# redeploying switches modes. Any other value fails the build on purpose.
ARG MAINTENANCE_MODE=false

FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL
ARG VITE_MAPBOX_TOKEN
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_MAPBOX_TOKEN=$VITE_MAPBOX_TOKEN
RUN npm run build

FROM nginx:alpine AS nginx-base
RUN apk add --no-cache curl && \
    chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx /etc/nginx/conf.d && \
    touch /var/run/nginx.pid && \
    chown nginx:nginx /var/run/nginx.pid
EXPOSE 80
# /health, not /: in maintenance mode / is a deliberate 503.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl --fail --silent http://localhost:80/health || exit 1
CMD ["nginx", "-g", "daemon off;"]

FROM nginx-base AS site-false
COPY --chown=nginx:nginx nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=nginx:nginx --from=builder /app/dist /usr/share/nginx/html
USER nginx

FROM nginx-base AS site-true
COPY --chown=nginx:nginx maintenance/nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=nginx:nginx maintenance/index.html /usr/share/nginx/maintenance/index.html
USER nginx

FROM site-${MAINTENANCE_MODE}
```

- [ ] **Step 7: Keep test scripts out of the build context**

Append to `frontend/.dockerignore`:

```
# Image tests run on the host against the built image
docker-tests/
```

- [ ] **Step 8: Run the maintenance test to verify it passes**

Run: `frontend/docker-tests/test_images.sh true`
Expected: exit 0, and all 14 lines start with `PASS` (3 common + 6 GET paths + POST + HEAD + Retry-After + Cache-Control + body).

- [ ] **Step 9: Confirm an invalid mode fails the build**

Run: `docker build --quiet --build-arg MAINTENANCE_MODE=TRUE -t sa-bad frontend; echo "exit $?"`
Expected: a non-zero exit, with an error that names `site-TRUE`.

- [ ] **Step 10: Commit**

```bash
git add frontend/docker-tests/test_images.sh frontend/maintenance/index.html frontend/maintenance/nginx.conf frontend/Dockerfile frontend/.dockerignore
git commit -m "feat(frontend): MAINTENANCE_MODE build arg serving a 503 maintenance page"
```

### Task 2: www → apex redirect in app mode, Railway healthcheck, CI, docs

**Files:**
- Modify: `frontend/nginx.conf:1-4` (server header) and `:47-52` (`/health` block)
- Modify: `frontend/railway.toml`
- Modify: `.github/workflows/ci.yml` (`frontend-test` job, after "Build frontend")
- Modify: `DEPLOYMENT.md` (append a section)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `frontend/docker-tests/test_images.sh` from Task 1.
- Produces: in both modes, `Host: www.safeascent.us` → `301 https://safeascent.us$request_uri`. The Railway frontend healthcheck path is `/health`.

- [ ] **Step 1: Run the app-mode test to verify it fails**

Run: `frontend/docker-tests/test_images.sh false`
Expected: exit 1, with `FAIL [app] www redirects 301: expected '301', got '200'` and `FAIL [app] www redirect target`.

- [ ] **Step 2: Add the www server block and an exact `/health` match**

In `frontend/nginx.conf`, replace the first three lines:

```nginx
server {
    listen 80;
    server_name localhost;
```

with:

```nginx
server {
    listen 80;
    server_name www.safeascent.us;
    return 301 https://safeascent.us$request_uri;
}

server {
    listen 80 default_server;
    server_name _;
```

Replace the health block at the end of the file:

```nginx
    # Health check endpoint
    location /health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }
```

with:

```nginx
    location = /health {
        access_log off;
        default_type text/plain;
        return 200 "healthy\n";
    }
```

- [ ] **Step 3: Point the Railway healthcheck at /health**

Replace `frontend/railway.toml` with:

```toml
# Railway config for the frontend service (nginx). /health answers 200 in both
# app and maintenance mode; / is a deliberate 503 in maintenance mode.

[build]
builder = "dockerfile"
dockerfilePath = "Dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 10
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 3
numReplicas = 1
```

- [ ] **Step 4: Run both modes to verify they pass**

Run: `frontend/docker-tests/test_images.sh all`
Expected: exit 0, and 20 `PASS` lines (14 maintenance + 6 app).

- [ ] **Step 5: Run the image tests in CI**

In `.github/workflows/ci.yml`, in job `frontend-test`, add this step directly after the `Build frontend` step:

```yaml
      - name: Image tests (app and maintenance mode)
        working-directory: frontend
        run: ./docker-tests/test_images.sh all
```

Validate the workflow: `docker run --rm -v "$PWD:/repo" --workdir /repo rhysd/actionlint:1.7.7 -color; echo "exit $?"`
Expected: `exit 0` with no findings.

- [ ] **Step 6: Document maintenance mode**

Append to `DEPLOYMENT.md`:

```markdown

---

## Maintenance mode

The frontend image takes a `MAINTENANCE_MODE` build arg (Railway service variable on `frontend`).

- `MAINTENANCE_MODE=true`: every path returns `503` with `Retry-After: 3600` and `Cache-Control: no-store`, serving `frontend/maintenance/index.html`. `/health` still returns `200` for the Railway healthcheck.
- `MAINTENANCE_MODE=false` (default): the React app.
- Both modes redirect `www.safeascent.us` to `https://safeascent.us` with a 301.

To flip: set the variable on the `frontend` service and redeploy. Locally: `frontend/docker-tests/test_images.sh all` checks both modes.
```

- [ ] **Step 7: CHANGELOG entry**

In `CHANGELOG.md`, replace the line `## [Unreleased] - 2026-09-24` with `## [Unreleased]`. Replace `### Security (Phase 0)` with `### Security (Phase 0, 2026-09-24)`. Then insert directly under `## [Unreleased]`:

```markdown

### Phase 1 PR1: maintenance mode and www redirect (2026-09-27)

- Frontend image takes a `MAINTENANCE_MODE` build arg. When `true` it serves a static page with HTTP 503, `Retry-After` and `Cache-Control: no-store` for every path, while `/health` stays 200.
- nginx redirects `www.safeascent.us` to `https://safeascent.us` (301) in both modes.
- Railway frontend healthcheck moved from `/` to `/health`.
- CI builds both image modes and checks the HTTP contract (`frontend/docker-tests/test_images.sh`).
```

- [ ] **Step 8: Commit**

```bash
git add frontend/nginx.conf frontend/railway.toml .github/workflows/ci.yml DEPLOYMENT.md CHANGELOG.md
git commit -m "feat(frontend): 301 www to apex, /health healthcheck, image tests in CI"
```

- [ ] **Step 9: Hand off**

Tell the owner that PR1 is ready for `/commitandpush` and a PR titled "Phase 1 PR1: maintenance mode + www redirect". Do not push.

### Task 3: OWNER — ship the maintenance page and the www domain

**Files:** none.

- [ ] **Step 1 (OWNER STEP): Merge PR1** once its CI is green. Re-run Task 0 Step 4 and confirm nothing deployed.

- [ ] **Step 2 (OWNER STEP): Set maintenance mode without triggering a deploy.**

```bash
railway variable set MAINTENANCE_MODE=true --service frontend --skip-deploys
```

- [ ] **Step 3 (OWNER STEP): Check the frontend service settings recorded in Task 0 Step 1.** Root Directory must be `frontend` (so `frontend/railway.toml` and `frontend/Dockerfile` apply). Networking target port must be `80`. Fix either in the dashboard if not.

- [ ] **Step 4 (OWNER STEP): Reconnect `frontend` only.**

```bash
railway service source connect --repo SebastianFrazier26/SafeAscent --branch main --service frontend
```

If no deployment starts within 2 minutes, go to the dashboard → `frontend` → Deployments and deploy the latest `main` commit. Leave `backend` and `celery-worker` disconnected.

- [ ] **Step 5 (OWNER STEP): Verify the apex.**

Run: `curl -sI https://safeascent.us/ | grep -iE '^(HTTP|retry-after|cache-control)'`
Expected: `HTTP/2 503`, `retry-after: 3600`, `cache-control: no-store`.
Run: `curl -s -o /dev/null -w '%{http_code}\n' https://safeascent.us/health`
Expected: `200`.
If `/` returns 200 with the app, the build arg did not reach the build. Check the variable name and redeploy.

- [ ] **Step 6 (OWNER STEP): Add the www custom domain.**

Run: `railway domain list --service frontend`. If `www.safeascent.us` is not listed, run `railway domain www.safeascent.us --service frontend`. It prints the DNS records Railway needs: a CNAME, and possibly a TXT verification record.

At Porkbun (current registrar/DNS), create exactly the records printed. Paste the CNAME target into the PR1 description, so the Phase 4 Cloudflare migration can recreate it. If DNS is already on Cloudflare when you do this, set the record's proxy status to **DNS only**. Railway must terminate TLS for its certificate to issue.

Check: `railway domain status www.safeascent.us --service frontend` until it reports the certificate as issued/active.

- [ ] **Step 7 (OWNER STEP): Verify the redirect and the cert.**

Run: `curl -sI https://www.safeascent.us/some/path?x=1 | grep -iE '^(HTTP|location)'`
Expected: `HTTP/2 301` and `location: https://safeascent.us/some/path?x=1`. A TLS error means the certificate is not issued yet.

---

## PR2 — uv migration (`chore/p1-pr2-uv`)

### Task 4: pyproject.toml + uv.lock replace requirements.txt, pytest.ini and ruff.toml

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/uv.lock` (generated)
- Delete: `backend/requirements.txt`, `backend/pytest.ini`, `backend/ruff.toml`
- Modify: `README.md` (backend quickstart block)

**Interfaces:**
- Consumes: the Phase 0 pins in `backend/requirements.txt`.
- Produces: `backend/pyproject.toml` with `[dependency-groups] dev`, `[tool.pytest.ini_options]`, `[tool.ruff]` and `[tool.mypy]`. The mypy strict allowlist override block exists with `module = []`; PR4 adds `app.config` and part B appends more. Commands: `uv sync --frozen`, `uv run pytest`, `uv run ruff check app/`, `uv run mypy`.

- [ ] **Step 1: Create the branch and record the baseline**

```bash
git switch main && git pull --ff-only && git switch -c chore/p1-pr2-uv
cd backend && uv venv -q --python 3.12 /tmp/sa-pip-venv && uv pip install -q --python /tmp/sa-pip-venv/bin/python -r requirements.txt
DATABASE_URL=postgresql+asyncpg://test_user:test_password@localhost:5432/safeascent_test /tmp/sa-pip-venv/bin/pytest tests/test_api_endpoints.py tests/test_weather_service.py tests/test_route_type_mapper.py tests/test_temporal_weighting.py tests/test_daily_variation.py tests/test_safety_algorithm.py -q --no-cov -p no:cacheprovider | tail -1
```

Expected: `120 passed, 19 skipped` (warnings count may differ). This is the pip-installed baseline the uv build must match.

- [ ] **Step 2: Write pyproject.toml**

Create `backend/pyproject.toml`:

```toml
[project]
name = "safeascent-backend"
version = "1.0.0"
description = "SafeAscent climbing-safety API, Celery worker and beat"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi==0.141.1",
    "uvicorn[standard]==0.53.0",
    "sqlalchemy==2.0.36",
    "asyncpg==0.30.0",
    "geoalchemy2==0.15.2",
    "greenlet==3.3.1",
    "alembic==1.14.0",
    "redis==5.2.0",
    "celery[redis]==5.4.0",
    "httpx==0.28.1",
    "requests==2.34.2",
    "numpy==2.2.1",
    "python-dotenv==1.2.3",
    "pydantic-settings==2.15.0",
    "python-multipart==0.0.32",
]

[dependency-groups]
dev = [
    "pytest==9.1.1",
    "pytest-asyncio==1.4.0",
    "pytest-cov==7.0.0",
    "ruff==0.8.4",
    "mypy==2.3.1",
    "pip-audit==2.10.1",
]

[tool.uv]
# An application, not a library: run from backend/ with `app` on the path.
package = false

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
addopts = "--verbose --cov=app --cov-report=term-missing --cov-report=html"
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
markers = [
    "performance: marks tests as performance benchmarks (deselect with '-m \"not performance\"')",
]

[tool.ruff]
target-version = "py312"

[tool.ruff.lint]
# Explicit so a ruff upgrade cannot silently widen the rule set; ruff itself is
# pinned in the dev group.
select = ["E4", "E7", "E9", "F"]

[tool.mypy]
python_version = "3.12"
plugins = ["pydantic.mypy"]
ignore_missing_imports = true
files = ["app"]

# Untyped legacy code is ignored until touched; modules graduate into the
# strict allowlist below as they are typed.
[[tool.mypy.overrides]]
module = ["app.*"]
ignore_errors = true

# Strict allowlist. Append modules here as they become strict-clean.
[[tool.mypy.overrides]]
module = []
ignore_errors = false
strict = true
```

- [ ] **Step 3: Lock, sync, and delete the replaced files**

```bash
cd backend
uv lock
uv sync --frozen
git rm -q requirements.txt pytest.ini ruff.toml
```

Expected: `uv lock` prints `Resolved 86 packages` (the count may drift if PyPI metadata changes; the pins above must not). `uv sync` creates `backend/.venv`, which is already gitignored. Then `rm -rf /tmp/sa-pip-venv`.

- [ ] **Step 4: Run the same subset under uv and compare**

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://test_user:test_password@localhost:5432/safeascent_test uv run pytest tests/test_api_endpoints.py tests/test_weather_service.py tests/test_route_type_mapper.py tests/test_temporal_weighting.py tests/test_daily_variation.py tests/test_safety_algorithm.py -q --no-cov -p no:cacheprovider | tail -1
uv run ruff check app/
uv run mypy
```

Expected: `120 passed, 19 skipped` (identical to Step 1), then `All checks passed!`, then `Success: no issues found in 49 source files`. (mypy 2.3.1 accepts the empty `module = []` strict block; verified 2026-09-27.)

- [ ] **Step 5: Audit the locked runtime dependencies**

Run from `backend/`:

```bash
uv export --frozen --no-dev --no-emit-project --format requirements.txt | uv run pip-audit -r /dev/stdin --require-hashes --disable-pip
```

Expected: `No known vulnerabilities found`.

- [ ] **Step 6: Update the README backend quickstart**

In `README.md`, replace:

````markdown
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```
````

with:

````markdown
```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
uv run pytest
```
````

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock README.md
git commit -m "chore(backend): manage dependencies with uv (pyproject + uv.lock)"
```

### Task 5: One uv-built backend Dockerfile, worker config, CI on uv

**Files:**
- Modify: `backend/Dockerfile` (full rewrite)
- Delete: `backend/Dockerfile.worker`
- Modify: `backend/railway-worker.toml:6`
- Modify: `.github/workflows/ci.yml` (`backend-test` job steps)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `backend/pyproject.toml` and `backend/uv.lock` from Task 4.
- Produces: one image. `CMD` = the api (`uvicorn app.main:app --host 0.0.0.0 --port 8000`). The worker and beat override the start command. The venv lives at `/opt/venv` and is on `PATH`. The image has no Docker `HEALTHCHECK`, because Railway uses `healthcheckPath` and compose defines its own.

- [ ] **Step 1: Write the failing smoke check**

Run from the repo root:

```bash
docker build -q -t safeascent-backend:uvtest backend && docker run --rm safeascent-backend:uvtest python -c "import pytest" ; echo "exit $?"
```

Expected: the build fails (`requirements.txt: not found`), because Task 4 deleted it.

- [ ] **Step 2: Rewrite the Dockerfile**

Replace `backend/Dockerfile` entirely:

```dockerfile
# One image for the api, the Celery worker and beat; Railway and
# docker-compose pick the process via the start command.

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# UV_PYTHON_DOWNLOADS=never: the venv must use the image's Python, which the
# runtime stage also has at the same path.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS production

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . .

RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Delete the worker Dockerfile and repoint the worker config**

```bash
git rm -q backend/Dockerfile.worker
```

In `backend/railway-worker.toml`, change `dockerfilePath = "Dockerfile.worker"` to `dockerfilePath = "Dockerfile"`. Leave `startCommand` alone: the worker/beat split is part B (PR6).

- [ ] **Step 4: Run the smoke checks to verify they pass**

```bash
docker build -q -t safeascent-backend:uvtest backend
docker run --rm -e DATABASE_URL=postgresql+asyncpg://u:p@localhost:5432/db safeascent-backend:uvtest python -c "import app.main, app.celery_app; print('app imports ok')"
docker run --rm safeascent-backend:uvtest python -c "import pytest"; echo "exit $?"
docker run --rm safeascent-backend:uvtest id -un
docker run --rm -e DATABASE_URL=postgresql+asyncpg://u:p@localhost:5432/db safeascent-backend:uvtest celery -A app.celery_app inspect --help >/dev/null && echo celery-cli-ok
```

Expected, in order: an image digest; `app imports ok`; `ModuleNotFoundError: No module named 'pytest'` then `exit 1` (dev deps are excluded); `appuser`; `celery-cli-ok`.

- [ ] **Step 5: Switch the CI backend job to uv**

In `.github/workflows/ci.yml`, job `backend-test`, replace everything from the `- name: Set up Python` step through the end of the `- name: Dependency audit` step with:

```yaml
      - uses: astral-sh/setup-uv@v10
        with:
          version: "0.11.3"
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: backend/uv.lock

      - name: Install dependencies
        working-directory: backend
        run: uv sync --frozen

      - name: Run linting
        working-directory: backend
        run: uv run ruff check app/

      - name: Dependency audit
        working-directory: backend
        run: |
          uv export --frozen --no-dev --no-emit-project --format requirements.txt \
            | uv run pip-audit -r /dev/stdin --require-hashes --disable-pip
```

In the `- name: Run tests` step, change the command's first word from `pytest` to `uv run pytest` and keep the file list. PR3 replaces this job wholesale.

Validate: `docker run --rm -v "$PWD:/repo" --workdir /repo rhysd/actionlint:1.7.7 -color; echo "exit $?"`
Expected: `exit 0`.

- [ ] **Step 6: CHANGELOG entry**

Insert directly under `## [Unreleased]` in `CHANGELOG.md`:

```markdown

### Phase 1 PR2: uv migration (2026-09-27)

- Backend dependencies moved from `requirements.txt` to `backend/pyproject.toml` + committed `uv.lock`, keeping every Phase 0 pin. Dev tools (pytest, pytest-asyncio, pytest-cov, ruff 0.8.4, mypy 2.3.1, pip-audit 2.10.1) are in the `dev` dependency group.
- `pytest.ini` and `ruff.toml` folded into `pyproject.toml`; mypy config added with an (initially empty) strict allowlist.
- One `backend/Dockerfile` (uv-built, non-root) serves the api, worker and beat; `Dockerfile.worker` removed.
- CI installs with `uv sync --frozen` and audits `uv export --no-dev` output with pip-audit.
- Commands: `cd backend && uv sync`, `uv run pytest`, `uv run ruff check app/`, `uv run mypy`.
```

- [ ] **Step 7: Commit**

```bash
git add backend/Dockerfile backend/railway-worker.toml .github/workflows/ci.yml CHANGELOG.md
git commit -m "chore(backend): single uv-built Dockerfile for api/worker/beat; CI on uv"
```

- [ ] **Step 8: Hand off.** PR2 is ready for the owner's `/commitandpush`. Merging does not deploy, because the backend services stay disconnected (Task 0).

---

## PR3 — CI rework (`ci/p1-pr3-ci`)

### Task 6: `needs_data` marker so `uv run pytest` is the whole CI suite

**Files:**
- Modify: `backend/pyproject.toml` (`[tool.pytest.ini_options]`)
- Modify: `backend/tests/test_daily_variation_yosemite.py`, `test_edge_cases_performance.py`, `test_known_outcomes_validation.py`, `test_longs_peak_daily.py`, `test_predict_integration.py`, `test_prediction_integration.py`, `test_weather_caching.py` (append), `test_performance.py:23`

**Interfaces:**
- Produces: pytest marker `needs_data`, deselected by default through `addopts`. `uv run pytest -m needs_data` runs only those tests (a later `-m` on the command line overrides the one in `addopts`).

- [ ] **Step 1: Create the branch and show the failure**

```bash
git switch main && git pull --ff-only && git switch -c ci/p1-pr3-ci
cd backend && uv sync --frozen && uv run pytest -q -p no:cacheprovider --no-cov | tail -1
```

Expected: the summary reports roughly `79 failed` (production-data, Redis and network tests). This is why CI used a hand-picked file list.

- [ ] **Step 2: Register the marker and deselect it by default**

In `backend/pyproject.toml` under `[tool.pytest.ini_options]`, set:

```toml
addopts = "--verbose --cov=app --cov-report=term-missing --cov-report=html -m 'not needs_data'"
```

and replace the `markers` list with:

```toml
markers = [
    "performance: marks tests as performance benchmarks (deselect with '-m \"not performance\"')",
    "needs_data: needs a populated database or live services; run with -m needs_data",
]
```

- [ ] **Step 3: Mark the data-dependent modules**

Append to the end of each of these seven files: `backend/tests/test_daily_variation_yosemite.py`, `test_edge_cases_performance.py`, `test_known_outcomes_validation.py`, `test_longs_peak_daily.py`, `test_predict_integration.py`, `test_prediction_integration.py`, `test_weather_caching.py`. Each already has `import pytest`.

```python

# Needs a populated database or live Redis/network; deselected by default (pyproject addopts).
pytestmark = pytest.mark.needs_data
```

In `backend/tests/test_performance.py`, replace line 23, `pytestmark = pytest.mark.performance`, with:

```python
pytestmark = [pytest.mark.performance, pytest.mark.needs_data]
```

- [ ] **Step 4: Run the default suite to verify it is green**

Run: `cd backend && uv run pytest -q -p no:cacheprovider --no-cov | tail -1`
Expected: `133 passed, 19 skipped, 104 deselected` and 0 failed.
Run: `uv run pytest -q -p no:cacheprovider --no-cov -m needs_data --co | tail -1`
Expected: `104/256 tests collected (152 deselected)`.

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/tests/
git commit -m "test(backend): needs_data marker so uv run pytest is the CI suite"
```

### Task 7: No-scraper guard `scripts/check_no_scrapers.py` (D9)

**Files:**
- Modify: `.gitignore` (the `scripts/` rule)
- Create: `backend/tests/test_check_no_scrapers.py`
- Create: `scripts/check_no_scrapers.py`

**Interfaces:**
- Produces: `python scripts/check_no_scrapers.py [repo_root]` exits 0 when clean, and exits 1 after printing one `path: [rule] detail` line per violation to stderr. Rules are `name`, `host`, `import` and `lockfile`. The module API used by the tests:
  - `check_file(path: str, text: str | None) -> list[Violation]`
  - `find_violations(repo_root: Path, paths: list[str]) -> list[Violation]`
  - `tracked_files(repo_root: Path) -> list[str]`
  - `main(argv: list[str] | None = None) -> int`
  - `EXEMPT_PATHS` and `ALLOWLIST` (both `frozenset[str]`, allowlist empty)
  - `Violation(path, rule, detail)`

- [ ] **Step 1: Let git see the script path**

`.gitignore` currently ignores every `scripts/` directory, which would silently drop the guard from commits. In `.gitignore`, replace:

```
scripts/
```

with:

```
/scripts/*
!/scripts/check_no_scrapers.py
```

Verify: `git check-ignore -v scripts/check_no_scrapers.py; echo "exit $?"`
Expected: no output and `exit 1` (not ignored). Note for part B: nested `*/scripts/` directories (for example `backend/scripts/perf/`) are no longer ignored.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_check_no_scrapers.py`:

```python
"""Tests for scripts/check_no_scrapers.py, the D9 no-scraper CI guard."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_no_scrapers", REPO_ROOT / "scripts" / "check_no_scrapers.py"
)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
sys.modules["check_no_scrapers"] = guard
_spec.loader.exec_module(guard)


def rules(path: str, text: str | None = "") -> list[str]:
    return [v.rule for v in guard.check_file(path, text)]


@pytest.mark.parametrize(
    "path",
    ["tools/route_scraper.py", "scrape_routes.ts", "data/Scraper.ipynb", "x/SCRAPE.sh", "notes/scraper-notes.md"],
)
def test_name_rule_flags_scraper_file_names(path):
    assert "name" in rules(path, "")


@pytest.mark.parametrize("path", ["backend/app/services/openbeta_client.py", "frontend/src/escape.js"])
def test_name_rule_ignores_innocent_names(path):
    assert rules(path, "") == []


@pytest.mark.parametrize("suffix", [".py", ".ts", ".tsx", ".js", ".jsx", ".sh"])
def test_host_rule_flags_mountainproject_in_source(suffix):
    assert rules(f"src/thing{suffix}", 'URL = "https://www.MountainProject.com/route/1"') == ["host"]


def test_host_rule_ignores_non_source_files():
    assert rules("docs/sources.md", "Data from mountainproject.com is not redistributed.") == []


def test_host_rule_reads_notebook_cells():
    nb = json.dumps({"cells": [{"source": ["url = 'https://mountainproject.com/x'\n"]}]})
    assert rules("analysis/explore.ipynb", nb) == ["host"]


@pytest.mark.parametrize(
    "line",
    [
        "import bs4",
        "from bs4 import BeautifulSoup",
        "import lxml.html",
        "from lxml import etree, html",
        "import html5lib",
        "from selectolax.parser import HTMLParser",
        "import parsel",
        "import scrapy",
        "from selenium import webdriver",
        "from playwright.sync_api import sync_playwright",
        "    import bs4  # inside a function",
    ],
)
def test_import_rule_flags_python_html_and_browser_libs(line):
    assert rules("backend/app/x.py", f"{line}\n") == ["import"]


@pytest.mark.parametrize("line", ["import lxml.etree", "from lxml import etree", "import httpx", "# import bs4"])
def test_import_rule_ignores_allowed_python_imports(line):
    assert rules("backend/app/x.py", f"{line}\n") == []


@pytest.mark.parametrize(
    "line",
    [
        "import { chromium } from 'playwright';",
        'import { test } from "@playwright/test";',
        "const { Builder } = require('selenium-webdriver');",
        "const pw = await import('playwright-core');",
        "import 'playwright';",
    ],
)
def test_import_rule_flags_js_browser_automation(line):
    assert rules("frontend/src/x.ts", f"{line}\n") == ["import"]


def test_import_rule_ignores_ordinary_js_imports():
    assert rules("frontend/src/x.jsx", "import axios from 'axios';\nimport React from 'react';\n") == []


def test_import_rule_reads_notebook_cells():
    nb = json.dumps({"cells": [{"source": "from bs4 import BeautifulSoup"}]})
    assert rules("analysis/explore.ipynb", nb) == ["import"]


def test_lockfile_rule_flags_banned_python_dist():
    lock = 'version = 1\n\n[[package]]\nname = "beautifulsoup4"\nversion = "4.12.3"\n'
    assert rules("backend/uv.lock", lock) == ["lockfile"]


def test_lockfile_rule_allows_lxml_in_uv_lock():
    lock = 'version = 1\n\n[[package]]\nname = "lxml"\nversion = "5.3.0"\n'
    assert rules("backend/uv.lock", lock) == []


def test_lockfile_rule_flags_banned_npm_package():
    lock = json.dumps({"packages": {"": {}, "node_modules/@playwright/test": {}, "node_modules/react": {}}})
    assert rules("frontend/package-lock.json", lock) == ["lockfile"]


def test_exempt_paths_are_exact():
    text = "import bs4\nURL = 'mountainproject.com'\n"
    assert rules("scripts/check_no_scrapers.py", text) == []
    assert rules("backend/tests/test_check_no_scrapers.py", text) == []
    assert set(rules("other/check_no_scrapers.py", text)) == {"name", "host", "import"}


def test_allowlist_is_empty():
    assert guard.ALLOWLIST == frozenset()


def test_binary_file_only_gets_name_rule():
    assert rules("assets/scraper.png", None) == ["name"]


def _git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, content in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_main_fails_on_repo_with_scraper(tmp_path, capsys):
    repo = _git_repo(tmp_path, {"tools/route_scraper.py": "print('hi')\n", "README.md": "ok\n"})
    assert guard.main([str(repo)]) == 1
    assert "tools/route_scraper.py" in capsys.readouterr().err


def test_main_only_checks_tracked_files(tmp_path):
    repo = _git_repo(tmp_path, {"README.md": "ok\n"})
    (repo / "untracked_scraper.py").write_text("import bs4\n")
    assert guard.main([str(repo)]) == 0


def test_this_repository_is_clean():
    assert guard.find_violations(REPO_ROOT, guard.tracked_files(REPO_ROOT)) == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_check_no_scrapers.py -q --no-cov -p no:cacheprovider`
Expected: a collection error, `FileNotFoundError` for `scripts/check_no_scrapers.py`.

- [ ] **Step 4: Write the guard**

Create `scripts/check_no_scrapers.py`:

```python
#!/usr/bin/env python3
"""Fail CI if any tracked file looks like scraper code (Phase 1 decision D9).

Scrapers live only in local, remote-less directories. Open-API clients
(OpenBeta, Open-Meteo, NOAA, ...) are fine and are not matched here.
Stdlib only, so the guard job needs no dependency install.
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# These two files must name every banned pattern to test it, so they are exempt
# from all rules by exact path. Nothing else may be added here without an
# owner-approved PR.
EXEMPT_PATHS = frozenset(
    {
        "scripts/check_no_scrapers.py",
        "backend/tests/test_check_no_scrapers.py",
    }
)
ALLOWLIST: frozenset[str] = frozenset()

NAME_GLOBS = ("scrape*", "*scraper*")
SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".ipynb", ".sh"})
BANNED_HOST = "mountainproject.com"

BANNED_PY_MODULES = ("bs4", "lxml.html", "html5lib", "selectolax", "parsel", "scrapy", "selenium", "playwright")
BANNED_JS_MODULES = ("playwright", "playwright-core", "@playwright/test", "selenium-webdriver")
# Distribution names as they appear in lockfiles. lxml is deliberately absent:
# it is a common transitive dependency, and lxml.html use is caught by the
# import rule instead.
BANNED_PY_DISTS = frozenset({"beautifulsoup4", "bs4", "html5lib", "selectolax", "parsel", "scrapy", "selenium", "playwright"})
BANNED_NPM_PACKAGES = frozenset(BANNED_JS_MODULES)

_PY_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(" + "|".join(re.escape(m) for m in BANNED_PY_MODULES) + r")\b"
    r"|^\s*from\s+lxml\s+import\s+(?:[^\n]*\b)?html\b",
    re.MULTILINE,
)
_JS_IMPORT = re.compile(
    r"""(?:\bfrom\s+|\brequire\(\s*|\bimport\(\s*|^\s*import\s+)['"]("""
    + "|".join(re.escape(m) for m in BANNED_JS_MODULES)
    + r""")(?:/[^'"]*)?['"]""",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Violation:
    path: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: [{self.rule}] {self.detail}"


def _notebook_source(text: str) -> str:
    try:
        cells = json.loads(text).get("cells", [])
    except (json.JSONDecodeError, AttributeError):
        return text
    parts: list[str] = []
    for cell in cells:
        src = cell.get("source", "")
        parts.append("".join(src) if isinstance(src, list) else str(src))
    return "\n".join(parts)


def _lockfile_violations(path: str, text: str) -> list[Violation]:
    name = PurePosixPath(path).name
    found: set[str] = set()
    if name == "uv.lock":
        for package in tomllib.loads(text).get("package", []):
            if str(package.get("name", "")).lower() in BANNED_PY_DISTS:
                found.add(str(package["name"]))
    elif name == "package-lock.json":
        for key in json.loads(text).get("packages", {}):
            package = key.rpartition("node_modules/")[2]
            if package in BANNED_NPM_PACKAGES:
                found.add(package)
    return [Violation(path, "lockfile", f"lists banned package {p}") for p in sorted(found)]


def check_file(path: str, text: str | None) -> list[Violation]:
    """Return violations for one tracked file. `text` is None for unreadable/binary files."""
    if path in EXEMPT_PATHS or path in ALLOWLIST:
        return []
    violations: list[Violation] = []
    pure = PurePosixPath(path)
    basename = pure.name.lower()
    if any(fnmatch.fnmatch(basename, glob) for glob in NAME_GLOBS):
        violations.append(Violation(path, "name", "file name matches scrape*/*scraper*"))
    if text is None:
        return violations
    suffix = pure.suffix.lower()
    if suffix in SOURCE_SUFFIXES:
        source = _notebook_source(text) if suffix == ".ipynb" else text
        if BANNED_HOST in source.lower():
            violations.append(Violation(path, "host", f"source mentions {BANNED_HOST}"))
        if suffix in {".py", ".ipynb"}:
            for match in _PY_IMPORT.finditer(source):
                violations.append(Violation(path, "import", match.group(0).strip()))
        elif suffix in {".ts", ".tsx", ".js", ".jsx"}:
            for match in _JS_IMPORT.finditer(source):
                violations.append(Violation(path, "import", match.group(0).strip()))
    if pure.name in {"uv.lock", "package-lock.json"}:
        violations.extend(_lockfile_violations(path, text))
    return violations


def tracked_files(repo_root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=repo_root, check=True, capture_output=True
    ).stdout
    return [p for p in out.decode().split("\0") if p]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return None


def find_violations(repo_root: Path, paths: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    for rel in paths:
        violations.extend(check_file(rel, _read(repo_root / rel)))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    repo_root = Path(args[0]) if args else Path.cwd()
    violations = find_violations(repo_root, tracked_files(repo_root))
    for violation in violations:
        print(violation, file=sys.stderr)
    if violations:
        print(
            f"\n{len(violations)} scraper-guard violation(s). Scraper code never goes in a "
            "GitHub repo (D9); keep it in ~/Developer/safeascent-private/.",
            file=sys.stderr,
        )
        return 1
    print("check_no_scrapers: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Then: `chmod +x scripts/check_no_scrapers.py`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_check_no_scrapers.py -q --no-cov -p no:cacheprovider | tail -1`
Expected: `46 passed`.
Run: `cd backend && uv run pytest -q -p no:cacheprovider --no-cov | tail -1`
Expected: `179 passed, 19 skipped, 104 deselected`.
Run from the repo root: `python3 scripts/check_no_scrapers.py; echo "exit $?"`
Expected: `check_no_scrapers: OK` and `exit 0`.
Run: `cd backend && uv run ruff check app/ ../scripts/`
Expected: `All checks passed!`

- [ ] **Step 6: Prove the guard bites on the spec's three cases (scratch branch, discarded)**

```bash
git switch -c tmp/guard-drill
mkdir -p tools && echo "print(1)" > tools/route_scraper.py
echo "URL = 'https://www.mountainproject.com/'" > backend/app/mp_link_drill.py
echo "import bs4" > backend/app/bs4_drill.py
git add tools/route_scraper.py backend/app/mp_link_drill.py backend/app/bs4_drill.py
python3 scripts/check_no_scrapers.py; echo "exit $?"
git rm -q --cached tools/route_scraper.py backend/app/mp_link_drill.py backend/app/bs4_drill.py
rm -rf tools backend/app/mp_link_drill.py backend/app/bs4_drill.py
git switch ci/p1-pr3-ci && git branch -D tmp/guard-drill
```

Expected: three stderr lines (`tools/route_scraper.py: [name] ...`, `backend/app/mp_link_drill.py: [host] ...`, `backend/app/bs4_drill.py: [import] import bs4`), then `exit 1`. Nothing is committed on the drill branch.

- [ ] **Step 7: Commit**

```bash
git add .gitignore scripts/check_no_scrapers.py backend/tests/test_check_no_scrapers.py
git commit -m "ci: no-scraper guard (D9) with tests"
```

### Task 8: CI rewrite: `backend`, `frontend`, `guards`, `ci-ok`

**Files:**
- Modify: `.github/workflows/ci.yml` (full rewrite)
- Modify: `DEPLOYMENT.md` (append a section)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `uv run pytest` (Task 6), `scripts/check_no_scrapers.py` (Task 7), `frontend/docker-tests/test_images.sh` (PR1).
- Produces: four jobs with fixed names, `backend`, `frontend`, `guards` and `ci-ok`. `ci-ok` succeeds only if every needed job's result is `success`. The `build-images` and `deploy` jobs are removed (Railway builds its own images; GHCR is unused).

- [ ] **Step 1: Rewrite the workflow**

Replace `.github/workflows/ci.yml` entirely:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend

    services:
      postgres:
        image: postgis/postgis:16-3.4-alpine
        env:
          POSTGRES_DB: safeascent_test
          POSTGRES_USER: test_user
          POSTGRES_PASSWORD: test_password
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:7-alpine
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    env:
      DATABASE_URL: postgresql+asyncpg://test_user:test_password@localhost:5432/safeascent_test
      REDIS_URL: redis://localhost:6379/0
      ENVIRONMENT: test
      MP_TABLES_AVAILABLE: "false"

    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v10
        with:
          version: "0.11.3"
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: backend/uv.lock

      - name: Install
        run: uv sync --frozen

      - name: Lint
        run: uv run ruff check app/ ../scripts/

      - name: Type check
        run: uv run mypy

      - name: Dependency audit
        run: |
          uv export --frozen --no-dev --no-emit-project --format requirements.txt \
            | uv run pip-audit -r /dev/stdin --require-hashes --disable-pip

      - name: Test
        run: uv run pytest

      - name: Build image and smoke-test imports
        run: |
          docker build -t safeascent-backend:ci .
          docker run --rm -e DATABASE_URL="$DATABASE_URL" safeascent-backend:ci \
            python -c "import app.main, app.celery_app"

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: 'npm'
          cache-dependency-path: 'frontend/package-lock.json'

      - name: Install
        run: npm ci

      - name: Lint
        run: npm run lint

      - name: Dependency audit
        run: npm audit --omit=dev --audit-level=high

      - name: Test
        run: npm run test:run

      - name: Build
        env:
          VITE_API_BASE_URL: http://localhost:8000/api/v1
          VITE_MAPBOX_TOKEN: pk.test_token_for_ci
        run: npm run build

      - name: Image tests (app and maintenance mode)
        run: ./docker-tests/test_images.sh all

  guards:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: No scraper code (D9)
        run: python scripts/check_no_scrapers.py

  # The single required status check (branch protection and Railway "Wait for
  # CI" key on this name). always() so a failed or skipped dependency reports
  # as a failure here instead of leaving the check pending forever.
  ci-ok:
    if: always()
    needs: [backend, frontend, guards]
    runs-on: ubuntu-latest
    steps:
      - name: All required jobs succeeded
        env:
          RESULTS: ${{ join(needs.*.result, ' ') }}
        run: |
          echo "job results: $RESULTS"
          for result in $RESULTS; do
            [ "$result" = "success" ] || exit 1
          done
```

- [ ] **Step 2: Lint the workflow**

Run: `docker run --rm -v "$PWD:/repo" --workdir /repo rhysd/actionlint:1.7.7 -color; echo "exit $?"`
Expected: `exit 0` with no findings.

- [ ] **Step 3: Rehearse every backend CI step locally**

```bash
cd backend
export DATABASE_URL=postgresql+asyncpg://test_user:test_password@localhost:5432/safeascent_test ENVIRONMENT=test MP_TABLES_AVAILABLE=false
uv sync --frozen && uv run ruff check app/ ../scripts/ && uv run mypy && \
uv export --frozen --no-dev --no-emit-project --format requirements.txt | uv run pip-audit -r /dev/stdin --require-hashes --disable-pip && \
uv run pytest -q -p no:cacheprovider | tail -1
```

Expected: `All checks passed!`, then `Success: no issues found in 49 source files`, then `No known vulnerabilities found`, then `179 passed, 19 skipped, 104 deselected`.

- [ ] **Step 4: Document the deploy pipeline**

Append to `DEPLOYMENT.md`:

```markdown

---

## Deploy pipeline

- CI (`.github/workflows/ci.yml`) runs on every PR and every push to `main`. Jobs: `backend` (uv sync, ruff, mypy, pip-audit, pytest, image build), `frontend` (npm ci, lint, npm audit, vitest, build, image tests), `guards` (`scripts/check_no_scrapers.py`) and `ci-ok`.
- `ci-ok` is the one required check. It fails unless every other job succeeded. Keep its name stable, because Railway "Wait for CI" and branch protection both key on it.
- Railway builds its own images from GitHub `main` with "Wait for CI" on. A red commit on `main` never deploys. No image registry and no deploy token are involved.
- `main` is protected: a PR and a green `ci-ok` are required, admins included, and force-pushes are blocked.
- Scraper code (anything fetching and parsing HTML pages) is never committed (D9). The `guards` job enforces this.
```

- [ ] **Step 5: CHANGELOG entry**

Insert directly under `## [Unreleased]`:

```markdown

### Phase 1 PR3: CI rework (2026-09-27)

- CI rebuilt into `backend`, `frontend`, `guards` and a single required `ci-ok` job; the unused GHCR `build-images` job and placeholder `deploy` job are removed (Railway builds from GitHub with "Wait for CI").
- Backend CI runs the whole default suite (`uv run pytest`); tests needing production data or live services are marked `needs_data` and deselected by default.
- New `scripts/check_no_scrapers.py` guard (D9) fails CI on scraper-like file names, `mountainproject.com` in source, HTML-parsing/browser-automation imports, or such packages in lockfiles.
- mypy runs in CI.
```

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml DEPLOYMENT.md CHANGELOG.md
git commit -m "ci: backend/frontend/guards jobs behind a single ci-ok gate"
```

- [ ] **Step 7: Hand off.** The owner runs `/commitandpush` and opens the PR. On the PR's Checks tab, confirm that the four checks `backend`, `frontend`, `guards` and `ci-ok` all appear and pass.

### Task 9: OWNER — Railway "Wait for CI" and branch protection

**Files:** none.

- [ ] **Step 1 (OWNER STEP): Decide on the history rewrite first.** The spec's open item 1 (running `git filter-repo` to remove old scraper files and the leaked Neon password) needs a force-push to `main`. If you approve it, do it *before* Step 3, because branch protection blocks force-pushes. Password rotation is required either way (relaunch gate, part B).

- [ ] **Step 2 (OWNER STEP): Merge PR3.** Then, for every service that is connected to GitHub (only `frontend` at this point, and later `backend`/`celery-worker`, or `api`/`worker`/`beat` after part B's split), open dashboard → service → Settings:
  - Turn on **Wait for CI**. Railway shows this toggle in the service's deploy/source settings once the repo has GitHub Actions workflows; confirm the exact location in the Railway UI.
  - Set **Watch Paths**: `frontend/**` for `frontend`, and `backend/**` for the backend services.
  - Confirm the trigger branch is `main`.

- [ ] **Step 3 (OWNER STEP): Protect `main`.** Save this as `/tmp/protection.json`:

```json
{
  "required_status_checks": { "strict": true, "checks": [{ "context": "ci-ok" }] },
  "enforce_admins": true,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
```

Run: `gh api -X PUT repos/SebastianFrazier26/SafeAscent/branches/main/protection --input /tmp/protection.json`
Verify: `gh api repos/SebastianFrazier26/SafeAscent/branches/main/protection --jq '.required_status_checks.checks, .enforce_admins.enabled, .allow_force_pushes.enabled'`
Expected: `[{"app_id":...,"context":"ci-ok"}]`, `true`, `false`.

- [ ] **Step 4 (OWNER STEP): Prove a red PR cannot merge.** Open a throwaway PR that adds `tools/route_scraper.py`. Expected: `guards` fails, then `ci-ok` fails, and the PR's merge button is blocked. Close the PR and delete its branch.

- [ ] **Step 5 (OWNER STEP): Prove a red `main` commit does not deploy.** You cannot push red to `main` directly any more. Instead, watch the next merge to `main`: while CI runs, the frontend deployment shows as waiting for CI, and it deploys only after `ci-ok` passes.

---

## PR4 — Config hygiene (`chore/p1-pr4-config`)

### Task 10: Settings is explicit and fail-loud

**Files:**
- Modify: `backend/app/config.py` (full rewrite)
- Modify: `backend/app/db/session.py:12`
- Modify: `backend/tests/conftest.py:1-17` (imports head)
- Modify: `backend/pyproject.toml` (the strict mypy block)
- Create: `backend/tests/test_settings.py`

**Interfaces:**
- Consumes: Phase 0's `Settings` (`CORS_ORIGINS` with `NoDecode`, `DEFAULT_CORS_ORIGINS`, the `cache_redis_url`/`celery_broker_url`/`celery_result_backend` properties). These stay unchanged.
- Produces (fixed names; part B depends on them):
  - `DATABASE_URL: str` (required)
  - `REDIS_URL: str = "redis://localhost:6379/0"`
  - `CACHE_REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND: str | None = None`
  - `API_V1_PREFIX: str = "/api/v1"`, `PROJECT_NAME: str = "SafeAscent"`
  - `ENVIRONMENT: Literal["development","test","production"] = "production"`
  - `SQL_ECHO: bool = False`
  - `ENABLE_ADMIN_ROUTES: bool = False`
  - `CORS_ORIGINS: list[str]`
  - `OPEN_METEO_API_KEY: str | None = None`
  - `USE_VECTORIZED_ALGORITHM: bool = True`
  - `SKIP_WEATHER_STATISTICS: bool = False`
  - `HEALTHCHECKS_NIGHTLY_URL: str | None = None`, `HEALTHCHECKS_BEAT_URL: str | None = None`
  - `WORKER_HEARTBEAT_TTL_SECONDS: int = 120`
  - The module-level `settings = Settings()` raises `pydantic.ValidationError` naming `DATABASE_URL` when it is unset.
  - The test harness sets `DATABASE_URL`/`ENVIRONMENT=test` via `os.environ.setdefault` in `conftest.py`.
  - `app.config` is in the mypy strict allowlist.

- [ ] **Step 1: Create the branch**

```bash
git switch main && git pull --ff-only && git switch -c chore/p1-pr4-config
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_settings.py`:

```python
"""Settings defaults, required fields, and the no-os.getenv rule (Phase 1 PR4)."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DB_URL = "postgresql+asyncpg://u:p@localhost:5432/db"


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Build Settings from exactly `env`, ignoring the caller's shell and any .env file."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_database_url_is_required(monkeypatch):
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        _settings(monkeypatch)


def test_importing_config_without_database_url_fails_loudly(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONPATH"] = str(BACKEND_DIR)
    result = subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr


def test_environment_defaults_to_production(monkeypatch):
    assert _settings(monkeypatch, DATABASE_URL=TEST_DB_URL).ENVIRONMENT == "production"


def test_environment_rejects_unknown_values(monkeypatch):
    with pytest.raises(ValidationError, match="ENVIRONMENT"):
        _settings(monkeypatch, DATABASE_URL=TEST_DB_URL, ENVIRONMENT="staging")


def test_sql_echo_off_by_default_even_in_development(monkeypatch):
    s = _settings(monkeypatch, DATABASE_URL=TEST_DB_URL, ENVIRONMENT="development")
    assert s.SQL_ECHO is False


def test_sql_echo_explicit_opt_in(monkeypatch):
    assert _settings(monkeypatch, DATABASE_URL=TEST_DB_URL, SQL_ECHO="true").SQL_ECHO is True


def test_engine_echo_follows_sql_echo():
    from app.db.session import engine

    assert engine.echo is False


def test_defaults_for_moved_env_vars(monkeypatch):
    s = _settings(monkeypatch, DATABASE_URL=TEST_DB_URL)
    assert s.OPEN_METEO_API_KEY is None
    assert s.USE_VECTORIZED_ALGORITHM is True
    assert s.SKIP_WEATHER_STATISTICS is False
    assert s.ENABLE_ADMIN_ROUTES is False
    assert s.HEALTHCHECKS_NIGHTLY_URL is None
    assert s.HEALTHCHECKS_BEAT_URL is None
    assert s.WORKER_HEARTBEAT_TTL_SECONDS == 120


def test_moved_env_vars_are_read_from_environment(monkeypatch):
    s = _settings(
        monkeypatch,
        DATABASE_URL=TEST_DB_URL,
        OPEN_METEO_API_KEY="k",
        USE_VECTORIZED_ALGORITHM="false",
        SKIP_WEATHER_STATISTICS="true",
    )
    assert s.OPEN_METEO_API_KEY == "k"
    assert s.USE_VECTORIZED_ALGORITHM is False
    assert s.SKIP_WEATHER_STATISTICS is True


def test_app_code_never_reads_environment_directly():
    pattern = re.compile(r"\bos\.(getenv|environ)\b")
    offenders = [
        f"{path.relative_to(BACKEND_DIR)}:{lineno}"
        for path in sorted((BACKEND_DIR / "app").rglob("*.py"))
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if pattern.search(line)
    ]
    assert offenders == []


async def test_skip_weather_statistics_setting_short_circuits(monkeypatch):
    from app.services import weather_service

    def _no_network(*args, **kwargs):
        raise AssertionError("network call made despite SKIP_WEATHER_STATISTICS")

    monkeypatch.setattr(weather_service.settings, "SKIP_WEATHER_STATISTICS", True)
    monkeypatch.setattr(weather_service.requests, "get", _no_network)
    assert await weather_service.fetch_weather_statistics(40.0, -105.0, 3000.0, "summer") is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_settings.py -q --no-cov -p no:cacheprovider | tail -3`
Expected: several failures, including `test_database_url_is_required` (DID NOT RAISE), `test_environment_defaults_to_production` (`'development' == 'production'`), `test_sql_echo_*` (AttributeError on `SQL_ECHO`), `test_app_code_never_reads_environment_directly` (lists `app/api/v1/predict.py:272` and two `app/services/weather_service.py` lines) and `test_skip_weather_statistics_setting_short_circuits`. The last two are fixed in Task 11.

- [ ] **Step 4: Rewrite `backend/app/config.py`**

```python
"""
Application configuration settings.

Settings is the single source of runtime configuration: application code reads
`settings.<FIELD>` and never the process environment directly. `.env.example`
at the repo root must list exactly these fields plus the frontend `VITE_*`
build args (enforced by tests/test_env_example_parity.py).
"""
import json
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "https://safeascent.us",
    "https://www.safeascent.us",
]


class Settings(BaseSettings):
    """Application settings."""

    # Required, no default: a missing value must fail startup rather than fall
    # back to a guessed local database.
    DATABASE_URL: str

    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_REDIS_URL: str | None = None
    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None

    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "SafeAscent"

    # Defaults to production so an unset variable on a deployed service never
    # enables development-only behavior.
    ENVIRONMENT: Literal["development", "test", "production"] = "production"
    SQL_ECHO: bool = False

    ENABLE_ADMIN_ROUTES: bool = False

    # CORS - accepts comma-separated or JSON array.
    # NoDecode: pydantic-settings otherwise JSON-decodes list-typed env vars
    # before validators run, which raises SettingsError on comma-separated input.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = DEFAULT_CORS_ORIGINS

    OPEN_METEO_API_KEY: str | None = None
    USE_VECTORIZED_ALGORITHM: bool = True
    SKIP_WEATHER_STATISTICS: bool = False

    HEALTHCHECKS_NIGHTLY_URL: str | None = None
    HEALTHCHECKS_BEAT_URL: str | None = None
    WORKER_HEARTBEAT_TTL_SECONDS: int = 120

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        """Parse comma-separated or JSON array CORS_ORIGINS env value."""
        if isinstance(value, list):
            return value
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            parsed: list[str] = json.loads(stripped)
            return parsed
        return [origin.strip() for origin in stripped.split(",") if origin.strip()]

    @property
    def cache_redis_url(self) -> str:
        """Redis URL for application cache operations."""
        return self.CACHE_REDIS_URL or self.REDIS_URL

    @property
    def celery_broker_url(self) -> str:
        """Redis URL for Celery broker."""
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def celery_result_backend(self) -> str:
        """Redis URL for Celery result backend."""
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL


settings = Settings()
```

- [ ] **Step 5: Make SQL echo explicit**

In `backend/app/db/session.py`, replace line 12, `    echo=settings.ENVIRONMENT == "development",  # Log SQL queries in dev`, with:

```python
    echo=settings.SQL_ECHO,
```

- [ ] **Step 6: Give the test harness a database URL before `app` imports**

In `backend/tests/conftest.py`, replace the import block (the lines from `import pytest` through `from app.db.session import get_db`, just below the module docstring) with:

```python
# ruff: noqa: E402
import os

# Settings() is built at import time and DATABASE_URL is required, so this must
# run before any `app` import. setdefault keeps CI's service URL when set.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test_user:test_password@localhost:5432/safeascent_test"
)
os.environ.setdefault("ENVIRONMENT", "test")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from typing import AsyncGenerator

from app.main import app
from app.db.session import get_db
```

- [ ] **Step 7: Put `app.config` in the strict mypy allowlist**

In `backend/pyproject.toml`, change the strict override block to:

```toml
# Strict allowlist. Append modules here as they become strict-clean.
[[tool.mypy.overrides]]
module = ["app.config"]
ignore_errors = false
strict = true
```

Run: `cd backend && uv run mypy`
Expected: `Success: no issues found in 49 source files`.

- [ ] **Step 8: Run the config tests**

Run: `cd backend && env -u DATABASE_URL uv run pytest tests/test_settings.py tests/test_config_and_admin_routes.py tests/test_cache_retention_and_config.py -q --no-cov -p no:cacheprovider | tail -1`
Expected: `2 failed, 22 passed`. The two failures are `test_app_code_never_reads_environment_directly` and `test_skip_weather_statistics_setting_short_circuits`, which Task 11 fixes.

- [ ] **Step 9: Commit**

```bash
git add backend/app/config.py backend/app/db/session.py backend/tests/conftest.py backend/tests/test_settings.py backend/pyproject.toml
git commit -m "feat(config): required DATABASE_URL, production default, explicit SQL_ECHO, new Settings fields"
```

### Task 11: Move the remaining `os.getenv` call sites into Settings

**Files:**
- Modify: `backend/app/api/v1/predict.py:6`, `:14`, `:272`
- Modify: `backend/app/services/weather_service.py:16`, `:22`, `:35`, `:327`

**Interfaces:**
- Consumes: `settings.USE_VECTORIZED_ALGORITHM`, `settings.OPEN_METEO_API_KEY` and `settings.SKIP_WEATHER_STATISTICS` from Task 10.
- Produces: `app.services.weather_service.OPEN_METEO_API_KEY` stays a module-level name, now sourced from settings. Existing tests patch it (`tests/test_weather_service.py:184`) and `app/api/v1/mp_routes.py` imports it, so it must stay.

- [ ] **Step 1: The failing tests already exist** (Task 10 Step 8: two failures). Re-run to confirm:

Run: `cd backend && uv run pytest tests/test_settings.py -q --no-cov -p no:cacheprovider -k "never_reads or short_circuits" | tail -1`
Expected: `2 failed`.

- [ ] **Step 2: predict.py**

In `backend/app/api/v1/predict.py`, delete line 6, `import os`. After the line `from app.db.session import get_db`, insert:

```python
from app.config import settings
```

Replace:

```python
    use_vectorized = os.getenv("USE_VECTORIZED_ALGORITHM", "true").lower() == "true"
```

with:

```python
    use_vectorized = settings.USE_VECTORIZED_ALGORITHM
```

- [ ] **Step 3: weather_service.py**

In `backend/app/services/weather_service.py`, delete line 16, `import os`. Directly before `from app.services.weather_similarity import WeatherPattern`, insert:

```python
from app.config import settings
```

Replace `OPEN_METEO_API_KEY = os.getenv("OPEN_METEO_API_KEY")` with:

```python
OPEN_METEO_API_KEY = settings.OPEN_METEO_API_KEY
```

Replace:

```python
    if os.getenv("SKIP_WEATHER_STATISTICS", "false").lower() == "true":
```

with:

```python
    if settings.SKIP_WEATHER_STATISTICS:
```

- [ ] **Step 4: Run the tests and lint to verify they pass**

```bash
cd backend
uv run pytest tests/test_settings.py tests/test_weather_service.py -q --no-cov -p no:cacheprovider | tail -1
uv run ruff check app/ ../scripts/
grep -rnE "\bos\.(getenv|environ)\b" app/ ; echo "grep exit $?"
```

Expected: `23 passed`, then `All checks passed!`, then `grep exit 1` (no matches).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/predict.py backend/app/services/weather_service.py
git commit -m "refactor(config): read algorithm and weather flags from Settings, not os.getenv"
```

### Task 12: `.env.example` parity with Settings

**Files:**
- Create: `backend/tests/test_env_example_parity.py`
- Modify: `.env.example` (full rewrite)

**Interfaces:**
- Consumes: `Settings.model_fields` (Task 10).
- Produces: a CI-enforced rule that the uncommented `KEY=` lines in the root `.env.example` equal `set(Settings.model_fields) | {"VITE_API_BASE_URL", "VITE_MAPBOX_TOKEN"}`, with no duplicates. Part B adding a Settings field must add it to `.env.example` too.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_env_example_parity.py`:

```python
"""`.env.example` must list exactly the Settings fields plus the frontend VITE_* build args."""
import re
from pathlib import Path

from app.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
FRONTEND_BUILD_ARGS = {"VITE_API_BASE_URL", "VITE_MAPBOX_TOKEN"}
KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def _documented_keys() -> list[str]:
    return [
        match.group(1)
        for line in ENV_EXAMPLE.read_text().splitlines()
        if (match := KEY_LINE.match(line))
    ]


def test_env_example_has_no_duplicate_keys():
    keys = _documented_keys()
    assert len(keys) == len(set(keys))


def test_env_example_matches_settings():
    documented = set(_documented_keys())
    expected = set(Settings.model_fields) | FRONTEND_BUILD_ARGS
    assert sorted(documented - expected) == [], "in .env.example but not in Settings"
    assert sorted(expected - documented) == [], "in Settings but missing from .env.example"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_env_example_parity.py -q --no-cov -p no:cacheprovider`
Expected: `test_env_example_matches_settings` fails. The "not in Settings" list includes `BACKEND_PORT`, `FRONTEND_PORT`, `POSTGRES_DB`, `POSTGRES_PASSWORD`, `POSTGRES_PORT`, `POSTGRES_USER` and `REDIS_PORT`.

- [ ] **Step 3: Rewrite `.env.example`**

```bash
# SafeAscent environment configuration.
# Backend keys mirror app.config.Settings exactly (tests/test_env_example_parity.py
# enforces it). For local runs copy to backend/.env; VITE_* go in frontend/.env.
# Values below are dummies: never commit real credentials.

# --- Backend: database -------------------------------------------------------
DATABASE_URL=postgresql+asyncpg://safeascent:change_me@localhost:5432/safeascent
SQL_ECHO=false

# --- Backend: Redis / Celery -------------------------------------------------
REDIS_URL=redis://localhost:6379/0
# Optional overrides; each falls back to REDIS_URL when empty.
CACHE_REDIS_URL=
CELERY_BROKER_URL=
CELERY_RESULT_BACKEND=

# --- Backend: API --------------------------------------------------------------
API_V1_PREFIX=/api/v1
PROJECT_NAME=SafeAscent
# One of: development, test, production. Unset means production.
ENVIRONMENT=development
ENABLE_ADMIN_ROUTES=false
# Comma-separated or JSON array.
CORS_ORIGINS=http://localhost:3000,http://localhost:5173

# --- Backend: external APIs and algorithm flags --------------------------------
# Empty uses the public Open-Meteo endpoints.
OPEN_METEO_API_KEY=
USE_VECTORIZED_ALGORITHM=true
SKIP_WEATHER_STATISTICS=false

# --- Backend: job monitoring (healthchecks.io) ---------------------------------
# Empty disables pings (local and CI make no outbound calls).
HEALTHCHECKS_NIGHTLY_URL=
HEALTHCHECKS_BEAT_URL=
WORKER_HEARTBEAT_TTL_SECONDS=120

# --- Frontend build args ---------------------------------------------------------
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_MAPBOX_TOKEN=pk.your_mapbox_token_here
```

- [ ] **Step 4: Run the parity test and the full suite**

```bash
cd backend
uv run pytest tests/test_env_example_parity.py -q --no-cov -p no:cacheprovider | tail -1
env -u DATABASE_URL uv run pytest -q -p no:cacheprovider --no-cov | tail -1
```

Expected: `2 passed`, then `192 passed, 19 skipped, 104 deselected`.

- [ ] **Step 5: Commit**

```bash
git add .env.example backend/tests/test_env_example_parity.py
git commit -m "chore(config): .env.example mirrors Settings exactly, enforced by a parity test"
```

### Task 13: Remove stale deploy files; align docker-compose with Railway

**Files:**
- Delete: `.do/app.yaml`, `.do/deploy.template.yaml`, `docker-compose.prod.yml`, `railway.toml` (repo root only; `backend/railway.toml` and `frontend/railway.toml` stay)
- Modify: `docker-compose.yml` (full rewrite)
- Modify: `.github/workflows/ci.yml` (`guards` job, one step)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: the single backend image (PR2) and the `MAINTENANCE_MODE` build arg (PR1).
- Produces: compose services `db`, `redis`, `api`, `worker`, `beat` and `frontend`. The worker and beat commands are the post-split targets part B deploys on Railway: `celery -A app.celery_app worker --loglevel=info --concurrency=1 -E` and `celery -A app.celery_app beat --loglevel=info`.

- [ ] **Step 1 (OWNER STEP): Confirm which config files Railway actually reads.** Use the notes from Task 0 Step 1. For each service, record Root Directory and "Railway Config File" in the PR description. Expected: `frontend` → root `frontend`, `frontend/railway.toml`; `backend` → root `backend`, `backend/railway.toml`; `celery-worker` → root `backend`, config `backend/railway-worker.toml`. If any service reads the root `railway.toml` (root directory `/` with no custom config path), stop. Tell the agent, who then keeps root `railway.toml` out of this task and flags it in the PR.

- [ ] **Step 2 (OWNER STEP): Check the variables PR4 makes strict.** In the dashboard (not the CLI, which prints values), open Variables on `backend` and `celery-worker`:
  - `DATABASE_URL` must be present. It is now required, and a missing value crashes startup.
  - `ENVIRONMENT` must be unset or exactly one of `development`, `test`, `production`. Any other value, such as `prod` or `Production`, now fails validation. Fix it before the relaunch.

- [ ] **Step 3: Write the failing compose check**

Run from the repo root: `docker compose config --services | sort | tr '\n' ' '; echo`
Expected (current file): `backend celery-beat celery-worker db frontend redis` (or an error about the required `POSTGRES_PASSWORD`/`VITE_MAPBOX_TOKEN`). Either way it is not the target `api beat db frontend redis worker`.

- [ ] **Step 4: Rewrite `docker-compose.yml`**

```yaml
# Local development stack mirroring the Railway topology: the same images and
# start commands as the Railway services, with a local PostGIS and Redis in
# place of Neon and Railway Redis. Production is Railway only; there is no
# production compose file.
#
#   docker compose up --build
#
# Credentials here are for throwaway local containers bound to 127.0.0.1.

x-backend: &backend
  build:
    context: ./backend
  restart: unless-stopped
  environment:
    DATABASE_URL: postgresql+asyncpg://safeascent:${POSTGRES_PASSWORD:-safeascent_dev}@db:5432/safeascent
    REDIS_URL: redis://redis:6379/0
    ENVIRONMENT: development
    OPEN_METEO_API_KEY: ${OPEN_METEO_API_KEY:-}
  depends_on:
    db:
      condition: service_healthy
    redis:
      condition: service_healthy

services:
  db:
    image: postgis/postgis:16-3.4-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: safeascent
      POSTGRES_USER: safeascent
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-safeascent_dev}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U safeascent -d safeascent"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    ports:
      - "127.0.0.1:6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  api:
    <<: *backend
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    ports:
      - "127.0.0.1:8000:8000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s

  worker:
    <<: *backend
    command: celery -A app.celery_app worker --loglevel=info --concurrency=1 -E

  beat:
    <<: *backend
    command: celery -A app.celery_app beat --loglevel=info

  frontend:
    build:
      context: ./frontend
      args:
        VITE_API_BASE_URL: ${VITE_API_BASE_URL:-http://localhost:8000/api/v1}
        VITE_MAPBOX_TOKEN: ${VITE_MAPBOX_TOKEN:-}
        MAINTENANCE_MODE: ${MAINTENANCE_MODE:-false}
    restart: unless-stopped
    ports:
      - "127.0.0.1:3000:80"
    depends_on:
      - api

volumes:
  postgres_data:
    name: safeascent_postgres_data
```

- [ ] **Step 5: Delete the stale deploy definitions**

```bash
git rm -q -r .do docker-compose.prod.yml railway.toml
```

(Skip `railway.toml` if Step 1 said a service reads it.)

- [ ] **Step 6: Verify compose and add the check to CI**

Run: `env -i PATH="$PATH" HOME="$HOME" docker compose config --services | sort | tr '\n' ' '; echo`
Expected: `api beat db frontend redis worker `. Running under `env -i` proves the file needs no variables.
Run: `env -i PATH="$PATH" HOME="$HOME" docker compose config -q && echo config-ok`
Expected: `config-ok`.

In `.github/workflows/ci.yml`, append to the `guards` job's steps:

```yaml
      - name: docker-compose mirrors the Railway services
        run: |
          services="$(docker compose config --services | sort | tr '\n' ' ')"
          echo "compose services: $services"
          [ "$services" = "api beat db frontend redis worker " ]
```

Run: `docker run --rm -v "$PWD:/repo" --workdir /repo rhysd/actionlint:1.7.7 -color; echo "exit $?"`
Expected: `exit 0`.

- [ ] **Step 7: Optional local boot** (only if Docker has ~4 GB free): `docker compose up --build -d db redis api && sleep 20 && curl -s localhost:8000/health; docker compose down`
Expected: `{"status":"healthy"}`.

- [ ] **Step 8: CHANGELOG entry**

Insert directly under `## [Unreleased]`:

```markdown

### Phase 1 PR4: config hygiene (2026-09-27)

- `app.config.Settings` is the single source of configuration: `OPEN_METEO_API_KEY`, `USE_VECTORIZED_ALGORITHM` and `SKIP_WEATHER_STATISTICS` moved in from `os.getenv`, and `HEALTHCHECKS_NIGHTLY_URL`, `HEALTHCHECKS_BEAT_URL` and `WORKER_HEARTBEAT_TTL_SECONDS` added for Phase 1 job monitoring.
- `DATABASE_URL` is now required (the hardcoded local fallback URL is gone; startup fails loudly without it).
- `ENVIRONMENT` defaults to `production` and only accepts `development`, `test` or `production`.
- SQL echo is controlled only by `SQL_ECHO` (default off), no longer implied by `ENVIRONMENT=development`.
- `.env.example` lists exactly the Settings fields plus `VITE_*` build args; a test fails CI on drift. Unused `SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS` removed.
- `app.config` is type-checked with mypy `strict`.
- Removed stale deploy definitions: `.do/` (DigitalOcean), `docker-compose.prod.yml`, and the duplicate root `railway.toml`.
- `docker-compose.yml` is local-dev only and mirrors Railway: `db`, `redis`, `api`, `worker`, `beat`, `frontend`, same image and start commands, ports bound to 127.0.0.1.
```

- [ ] **Step 9: Final verification and commit**

```bash
cd backend && env -u DATABASE_URL uv run pytest -q -p no:cacheprovider | tail -1 && uv run mypy && uv run ruff check app/ ../scripts/ && cd .. && python3 scripts/check_no_scrapers.py
git add docker-compose.yml .github/workflows/ci.yml CHANGELOG.md
git commit -m "chore: remove stale deploy files; docker-compose mirrors Railway services"
```

Expected before the commit: `192 passed, 19 skipped, 104 deselected`, `Success: no issues found in 49 source files`, `All checks passed!` and `check_no_scrapers: OK`.

- [ ] **Step 10: Hand off.** The owner runs `/commitandpush` and opens the PR. It merges only on a green `ci-ok`.

### Task 14: OWNER — remove the unused `Redis` service (D5)

**Files:** none. Do this after PR4 merges and before the relaunch.

- [ ] **Step 1 (OWNER STEP): Confirm nothing points at `Redis`.** In the dashboard, open Variables on every service (`frontend`, `backend`, `celery-worker`). `REDIS_URL`, `CACHE_REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` must each be unset or reference `Redis-mxVE` (for example `${{Redis-mxVE.REDIS_URL}}`), never `Redis`. Do not copy values into a terminal.

- [ ] **Step 2 (OWNER STEP): Confirm `Redis` has no clients and no keys.**

Run: `railway connect Redis`, which opens `redis-cli` against that service. Then run:

```
CLIENT LIST
INFO keyspace
DBSIZE
```

Expected: `CLIENT LIST` shows exactly one client (your own `cmd=client|list` connection). `INFO keyspace` prints only `# Keyspace` with no `db0:` line. `DBSIZE` returns `(integer) 0`. If any check fails, stop and investigate. Do not delete.

- [ ] **Step 3 (OWNER STEP): Snapshot, then delete.** In the dashboard, on `Redis` → its volume → Backups, create a manual backup. Confirm the location in the current Railway UI. Then go to `Redis` → Settings → Delete service. Record the date in the PR4 thread.

---

## Self-review

**Spec coverage (PR1–PR4 scope):**

| Spec item | Task |
|---|---|
| §6 Maintenance (D4): `ARG MAINTENANCE_MODE`, 503 + Retry-After, Railway variable | 1, 3 |
| §6 www TLS (D7): custom domain, CNAME, nginx 301 | 2, 3 |
| Testing: "maintenance build returns 503", "`curl -I https://www.safeascent.us` 301 with valid cert" | 1, 2, 3 |
| §1 uv: pyproject + uv.lock, dev group, folded ruff/pytest config, one Dockerfile, uv in Docker/CI, pip-audit via `uv export` | 4, 5, 8 |
| Testing: `uv sync --frozen` + docker build in CI; existing subset passes unchanged; pip-audit clean | 4, 5, 8 |
| §5 Deploy (D3): delete build-images/deploy jobs, `ci-ok`, Wait for CI, watch paths, branch protection, DEPLOYMENT.md | 8, 9 |
| §5 no-scraper guard (D9), including tests for the three drill cases | 7 |
| Testing: "red PR cannot merge", "red main commit does not deploy" | 9 |
| §4 Settings single source, getenv moves, `.env.example` exact + parity test | 10, 11, 12 |
| §4 Safer defaults: ENVIRONMENT=production, SQL_ECHO, required DATABASE_URL | 10 |
| §4 Stale files: `.do/`, compose prod, root railway.toml after dashboard check | 13 |
| §4 docker-compose (D6) | 13 |
| §4 Redis (D5) | 14 |
| Rollout: Task 0 freeze; Phase 0 first | 0 |
| CHANGELOG per PR | 2, 5, 8, 13 |

Left to part B by design: Alembic/roles (PR5), the Celery split, `/health/worker` and healthchecks.io pings (PR6, which consume the Settings fields defined here), TypeScript/Risk 0.0 (PR7), CLAUDE.md/README/LICENSE and the full DEPLOYMENT.md rewrite (PR8), and extending the mypy allowlist.

**Placeholder scan:** every code step has complete content. The only execution-time substitution is the CHANGELOG date (`date +%F`), called out in Global Constraints.

**Type/name consistency:** Settings field names and types match the Shared interfaces list. The CI job names are `backend`/`frontend`/`guards`/`ci-ok` everywhere (the spec's `no-scrapers` job name is replaced by `guards`, per the shared-interface contract). `check_file`/`find_violations`/`tracked_files`/`main`/`EXEMPT_PATHS`/`ALLOWLIST` match between Task 7's script and its tests. The test counts (120 → 133 → 179 → 192 passed) were measured on a prototype of this exact plan against Phase 0 code on 2026-09-27.
