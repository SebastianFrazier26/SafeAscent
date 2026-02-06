# SafeAscent Infrastructure Architecture

**Stack:** Railway (hosting) + Neon (PostgreSQL + PostGIS) + Redis (caching) + Porkbun (domain)

**Monthly Cost:** ~$11/month
- Railway: ~$10/mo (backend, frontend, Redis)
- Neon: $0 (free tier)
- Domain: ~$1/mo ($10.87/year)

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
                 │      Redis       │          │ PostgreSQL+PostGIS│
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
- **Tier:** Free (0.5 GB storage, 100 hours compute/month)
- **Connection:** SSL required, async via asyncpg driver

**PostGIS Functions Used:**
- `ST_DWithin()` - Proximity queries for nearby accidents
- `ST_MakePoint()` - Coordinate point creation
- `ST_SetSRID()` - Coordinate system assignment (WGS84 / SRID 4326)

### Cache (Railway Redis)
- **Purpose:** Safety score caching, weather data caching
- **TTL:** 24 hours for safety scores, 6 hours for weather
- **Pattern:** `safety:{route_id}:{date}` for pre-computed scores

---

## Data Flow

### Safety Score Calculation
```
1. User requests route safety → Frontend
2. Frontend calls /api/v1/mp-routes/{id}/safety → Backend
3. Backend checks Redis cache for pre-computed score
4. If cache miss: calculates fresh using algorithm services
5. Algorithm queries Neon for accidents within spatial bandwidth
6. Weather similarity computed against current forecast
7. Score returned and cached for 24 hours
```

### Nightly Pre-computation (Celery Beat)
```
1. 2:00 AM UTC: Celery Beat triggers compute task
2. Backend fetches all ~168K routes from Neon
3. For each route: calculates safety score for next 3 days
4. Scores stored in Redis cache with 48-hour TTL
5. Scores also saved to historical_predictions table in Neon
6. Total runtime: ~2-3 hours
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
- **Rate Limits:** 10,000 requests/day (free), unlimited (commercial)
- **Used For:** Real-time forecasts, historical accident weather

### Mapbox GL JS
- **Purpose:** Interactive 3D terrain maps
- **Tier:** Free (50,000 map loads/month)
- **Features:** Clustering, heatmaps, custom styles

---

## SSL/TLS

- **Provider:** Railway (automatic via Let's Encrypt)
- **Renewal:** Automatic
- **Coverage:** All custom domains (safeascent.us, api.safeascent.us)

---

## Monitoring

### Railway Dashboard
- CPU/Memory usage per service
- Request logs and error tracking
- Deployment history

### Application Logs
- Backend: Structured logging via Python logging module
- Celery: Task execution logs with timing
- Errors: Stack traces for failed requests

---

## Cost Breakdown

| Service | Provider | Monthly Cost |
|---------|----------|--------------|
| Frontend | Railway | ~$2 |
| Backend | Railway | ~$5 |
| Redis | Railway | ~$3 |
| PostgreSQL + PostGIS | Neon | $0 (free tier) |
| Domain | Porkbun | ~$0.90 |
| **Total** | | **~$11/month** |

---

## Scaling Considerations

### Current Limitations (Hobby Tier)
- Single backend worker (no horizontal scaling)
- 512MB memory limit per service
- Neon free tier: 100 compute hours/month

### Future Scaling Options
- Railway Pro: Multiple workers, more memory
- Neon Pro: Autoscaling compute, more storage
- CDN: CloudFlare for static assets and caching

---

*Last Updated: February 2026*
