# SafeAscent

**Climbing safety predictions powered by historical accident analysis and real-time weather data.**

*Last Updated: February 2026*

---

## Project Overview

SafeAscent is a web application that predicts climbing route safety by analyzing ~5,000 historical accidents weighted by:
- **Spatial proximity** - Gaussian decay by distance (route-type-specific bandwidths)
- **Weather similarity** - 7-day pattern matching with quadratic weighting
- **Temporal relevance** - Year-scale decay with seasonal boosting
- **Route type** - Asymmetric cross-type influence matrix

The algorithm provides daily risk scores (0-100) with confidence metrics, helping climbers make informed decisions about route conditions.

For detailed algorithm documentation, see [ALGORITHM_DESIGN.md](./ALGORITHM_DESIGN.md).

---

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Backend API** | ✅ Production-ready | FastAPI + PostgreSQL |
| **Safety Algorithm** | ✅ Complete | 7 modular services, 50/50 tests passing |
| **Frontend Map** | ✅ Complete | Two-view system with stratified heatmaps |
| **Data Pipeline** | 🚧 Rebuilding | Routes/ticks/weather collection in progress |

---

## Tech Stack

### Backend
- **FastAPI** - Async Python API framework
- **PostgreSQL** - Primary database with PostGIS
- **Redis** - Weather caching (6-hour TTL)
- **Open-Meteo** - Weather API (free, no key required)

### Frontend
- **React 18** + **Vite** - UI framework and build tool
- **Material-UI** - Component library (Material Design 3)
- **Mapbox GL JS** - Interactive 3D terrain maps
- **React-Map-GL** - React wrapper for Mapbox

### Data Pipeline
- **Pandas** - Data processing
- **Google Maps API** - Geocoding (primary)
- **Gemini 2.0 Flash** - AAC extraction + geocoding fallback

---

## Frontend Features

### Two-View Map System
| View | Purpose | Implementation |
|------|---------|----------------|
| **Cluster View** | Navigation | Mapbox native clustering, color-coded by avg risk |
| **Risk Coverage** | Safety Analysis | 5 stratified heatmap layers |

### Key Features
- Search by route/mountain name
- Date picker for 7-day forecast
- Hover tooltips with route details
- Progress bar during loading

---

## Backend Architecture

### Algorithm Services (`backend/app/services/`)
- `algorithm_config.py` - All tunable parameters
- `spatial_weighting.py` - Gaussian decay by distance
- `temporal_weighting.py` - Exponential decay + seasonal boost
- `weather_similarity.py` - 7-day pattern matching
- `route_type_weighting.py` - Asymmetric type matrix
- `severity_weighting.py` - Fatality/injury weighting
- `confidence_scoring.py` - Confidence calculation
- `safety_algorithm.py` - Main orchestrator

### API Endpoints
- `POST /api/v1/predict` - Get safety prediction
- `GET /api/v1/routes` - List routes
- `GET /api/v1/mountains` - List mountains
- `GET /api/v1/accidents` - Query accidents

---

## Database Schema

For detailed database documentation, see [data/DATABASE_STRUCTURE.md](./data/DATABASE_STRUCTURE.md).

### Core Tables
| Table | Records | Description |
|-------|---------|-------------|
| **accidents** | ~6,900 | Combined from AAC, Avalanche.org, NPS |
| **weather** | ~70,000+ | 7-day windows for each accident |
| **routes** | 622 (base) | Mountain Project routes |
| **mountains** | 442 | Peaks and crags |
| **ascents** | Growing | MP tick data (being collected) |

---

## Data Sources

| Source | Records | Coverage |
|--------|---------|----------|
| **AAC** (American Alpine Club) | 2,770 | 1990-2019 |
| **Avalanche.org** | 1,372 | 1997-2026 |
| **NPS** (National Park Service) | 848 | Various |
| **Mountain Project** | 196,000+ routes | Ongoing scrape |

---

## Quick Start

### Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend
```bash
cd frontend
npm install
# Add VITE_MAPBOX_TOKEN to .env
npm run dev
```

---

## Documentation

- [ALGORITHM_DESIGN.md](./ALGORITHM_DESIGN.md) - Detailed algorithm decisions
- [DEVELOPMENT_LOG.md](./DEVELOPMENT_LOG.md) - Implementation details
- [SESSION_HISTORY.md](./SESSION_HISTORY.md) - Development timeline
- [data/DATABASE_STRUCTURE.md](./data/DATABASE_STRUCTURE.md) - Database schema
- [data/README.md](./data/README.md) - Data sources and collection

---

## License

MIT
