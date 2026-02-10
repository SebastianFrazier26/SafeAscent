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
- `REDIS_URL` - Railway Redis internal URL
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
- **TTL:** 7 days for bulk precomputed safety keys, 1 hour for on-demand single-route safety responses, 6 hours for weather patterns
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
2. Backend computes location-level batch scores and fans out to routes
3. Calculates safety scores for today + next 2 days
4. Scores stored in Redis cache with 7-day TTL
5. Scores also saved to historical_predictions table in Neon
6. Runtime depends on worker resources and weather API responsiveness
```

---

## Database Tables (Neon)

### Core Tables
| Table | Records | Purpose |
|-------|---------|---------|
| `mp_routes` | ~168,000 | Mountain Project climbing routes |
| `mp_locations` | ~45,000 | Location hierarchy (areas → crags) |
| `accidents` | ~6,900 | Historical climbing accidents |
| `weather_patterns` | ~25,000 | 7-day weather windows for accidents |

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
