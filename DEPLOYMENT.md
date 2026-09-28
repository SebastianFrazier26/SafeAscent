# SafeAscent Infrastructure Architecture

**Stack:** Railway (hosting) + Neon (PostgreSQL + PostGIS) + Redis (caching) + Porkbun (domain)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         INTERNET                                │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PORKBUN DNS                                  │
│  safeascent.us     → frontend.railway.app                       │
│  api.safeascent.us → backend.railway.app                        │
└─────────────────────────┬───────────────────────────────────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
┌──────────────────┐            ┌──────────────────┐
│     RAILWAY      │            │     RAILWAY      │
│    Frontend      │            │     Backend      │
│  (Nginx + React) │            │    (FastAPI)     │
│    Port 80       │            │    Port 8000     │
└──────────────────┘            └────────┬─────────┘
                                         │
                          ┌──────────────┴──────────────┐
                          ▼                              ▼
                 ┌──────────────────┐          ┌──────────────────┐
                 │     RAILWAY      │          │      NEON        │
                 │      Redis       │          │    PostgreSQL    │
                 │   (Caching)      │          │   (Database)     │
                 └──────────────────┘          └──────────────────┘
```

---

## Service Components

### Frontend (Railway)
- **Framework:** React 18 + Vite
- **Container:** Nginx serving static build
- **Domain:** safeascent.us, www.safeascent.us
- **Build:** Multi-stage Dockerfile (node build → nginx serve)

**Key Environment Variables:**
- `VITE_API_BASE_URL` - Backend API endpoint (https://api.safeascent.us/api/v1)
- `VITE_MAPBOX_TOKEN` - Mapbox GL JS access token

### Backend (Railway)
- **Framework:** FastAPI (async Python)
- **Container:** Python 3.11 + uvicorn
- **Domain:** api.safeascent.us
- **Workers:** Single process (Railway hobby tier)

**Key Environment Variables:**
- `DATABASE_URL` - Neon PostgreSQL connection (postgresql+asyncpg://...)
- `REDIS_URL` - Fallback Redis URL (used if specific URLs below are unset)
- `CACHE_REDIS_URL` - Redis URL for API/cache keys (recommended dedicated instance)
- `CELERY_BROKER_URL` - Redis URL for Celery broker (recommended dedicated instance)
- `CELERY_RESULT_BACKEND` - Redis URL for Celery task results
- `CORS_ORIGINS` - Allowed frontend origins
- `OPEN_METEO_API_KEY` - Commercial weather API key (optional)

### Database (Neon)
- **Engine:** PostgreSQL 16 with PostGIS extension
- **Region:** US East (Ohio) - us-east-2
- **Connection:** SSL required, async via asyncpg driver

**PostGIS Functions Used:**
- `ST_DWithin()` - Proximity queries for nearby accidents
- `ST_MakePoint()` - Coordinate point creation
- `ST_SetSRID()` - Coordinate system assignment (WGS84 / SRID 4326)

### Cache (Railway Redis)
- **Purpose:** Safety score caching, weather data caching
- **TTL:** 2 days for bulk precomputed safety keys, 1 hour for on-demand single-route safety responses, 6 hours for weather patterns
- **Pattern:** `safety:route:{route_id}:date:{date}` for route safety scores

---

## Data Flow

### Safety Score Calculation
```
1. User requests route safety → Frontend
2. Frontend calls /api/v1/mp-routes/{id}/safety → Backend
3. Backend checks Redis cache for pre-computed score (unless `bypass_cache=true`)
4. If cache miss or bypass requested: calculates fresh using algorithm services
5. Algorithm queries Neon for accidents within spatial bandwidth
6. Weather similarity computed against current forecast
7. Score returned (and cached for 1 hour when cache bypass is not used)
```

### Nightly Pre-computation (Celery Beat)
```
1. 2:00 AM UTC: Celery Beat triggers compute task
2. Task clears stale `safety:route:*` keys outside active computation dates (targeted cleanup only)
3. Backend computes location-level batch scores and fans out to routes
4. Calculates safety scores for today + next 2 days
5. Scores stored in Redis cache with 2-day TTL
6. Scores also saved to historical_predictions table in Neon
7. Runtime depends on worker resources and weather API responsiveness
```

---

## Database Tables (Neon)

### Core Tables
| Table | Records | Purpose |
|-------|---------|---------|
| `mp_routes` | ~168,000 | Mountain Project climbing routes |
| `mp_locations` | ~45,000 | Location hierarchy (areas → crags) |
| `accidents` | ~6,900 | Historical climbing accidents |
| `weather` | ~25,000 | 7-day weather windows for accidents |

### Cache Tables
| Table | Purpose |
|-------|---------|
| `historical_predictions` | Daily safety scores for trend analysis |

### Key Indexes
- `accidents`: Spatial index on coordinates, date index
- `mp_routes`: Index on location_id, type
- `mp_locations`: Index on parent_id, spatial index on coordinates

---

## External APIs

### Open-Meteo Weather API
- **Endpoint:** api.open-meteo.com (free) or customer-api.open-meteo.com (commercial)
- **Data:** Hourly forecasts, historical weather
- **Used For:** Real-time forecasts, historical accident weather

### Mapbox GL JS
- **Purpose:** Interactive 3D terrain maps
- **Features:** Clustering, heatmaps, custom styles

---

## SSL/TLS

- **Provider:** Railway (automatic via Let's Encrypt)
- **Renewal:** Automatic
- **Coverage:** All custom domains (safeascent.us, api.safeascent.us)

*Last Updated: February 2026*

---

## Maintenance mode

The frontend image takes a `MAINTENANCE_MODE` build arg (Railway service variable on `frontend`).

- `MAINTENANCE_MODE=true`: every path returns `503` with `Retry-After: 3600` and `Cache-Control: no-store`, serving `frontend/maintenance/index.html`. `/health` still returns `200` for the Railway healthcheck.
- `MAINTENANCE_MODE=false` (default): the React app.
- Both modes redirect `www.safeascent.us` to `https://safeascent.us` with a 301.

To flip: set the variable on the `frontend` service and redeploy. Locally: `frontend/docker-tests/test_images.sh all` checks both modes.

---

## Deploy pipeline

- CI (`.github/workflows/ci.yml`) runs on every PR and every push to `main`. Jobs: `backend` (uv sync, ruff, mypy, pip-audit, pytest, image build), `frontend` (npm ci, lint, npm audit, vitest, build, image tests), `guards` (`scripts/check_no_scrapers.py`, plus a check that `docker compose config --services` matches the Railway topology) and `ci-ok`.
- `ci-ok` is the one required check. It fails unless every other job succeeded. Keep its name stable, because Railway "Wait for CI" and branch protection both key on it.
- Railway builds its own images from GitHub `main`. "Wait for CI" (after owner setup — not yet enabled) will make a red commit on `main` never deploy. No image registry and no deploy token are involved.
- `main` branch protection (a PR and a green `ci-ok` required, admins included, force-pushes blocked) is after owner setup — not yet enabled.
- Scraper code (anything fetching and parsing HTML pages) is never committed (D9). The `guards` job enforces this.

## Insufficient-data rollout (2026-09-28)

1. Run `alembic upgrade head` (migration `0003_hist_insufficient_data`) before the new backend/worker code runs. Without it the nightly history insert hits `risk_score NOT NULL` for any batch containing an insufficient route, and the task now fails on that.
2. Deploy backend and frontend together (the frontend requires `data_status`).
3. Right after the deploy, trigger the nightly population task (`compute_daily_safety_scores_optimized`) once, e.g. via `GET /api/v1/mp-routes/admin/trigger-cache-population` (only mounted when `ENABLE_ADMIN_ROUTES` is set) or by calling the Celery task from a worker shell. Redis still holds scores from before the deploy; reads already map a stale `0.0` to insufficient, but a fresh run rewrites every route under the new rule.
