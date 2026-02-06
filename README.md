# SafeAscent

**Climbing safety predictions powered by historical accident analysis and real-time weather data.**

*Last Updated: February 2026*

---

## Project Overview

SafeAscent is a web application that predicts climbing route safety by analyzing ~5,000 historical accidents weighted by:
- **Spatial proximity** - Gaussian decay by distance (route-type-specific bandwidths)
- **Weather similarity** - 7-day pattern matching with cubic weighting
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
| **Data Pipeline** | ✅ Complete | ~168K routes, ~6.9K accidents, ~25K weather records |

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
- **Dark mode UI** with Material Design 3
- Search by route/mountain name
- Date picker for 7-day forecast
- **Season filter**: All / Summer (rock) / Winter (ice/mixed)
- **Season-specific map styles**: Warm outdoors / Cool winter theme
- Hover tooltips with route details
- Tight grid clustering for overlapping routes
- 8-tab route analytics dashboard
- Custom climbing-themed favicon
- Boulder routes excluded (different risk profile)

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

**Core**
- `POST /api/v1/predict` - Get safety prediction
- `GET /api/v1/routes` - List routes with filters
- `GET /api/v1/routes/map` - Routes optimized for map display
- `GET /api/v1/mountains` - List mountains

**Route Analytics**
- `GET /api/v1/routes/{id}/safety` - Safety score for date
- `GET /api/v1/routes/{id}/forecast` - 7-day forecast
- `GET /api/v1/routes/{id}/accidents` - Accident history
- `GET /api/v1/routes/{id}/risk-breakdown` - Factor analysis
- `GET /api/v1/routes/{id}/seasonal-patterns` - Monthly patterns
- `GET /api/v1/routes/{id}/time-of-day` - Hourly analysis
- `GET /api/v1/routes/{id}/ascent-analytics` - Monthly ascent/accident rates

---

## Database Schema

For detailed database documentation, see [data/DATABASE_STRUCTURE.md](./data/DATABASE_STRUCTURE.md).

### Core Tables
| Table | Records | Description |
|-------|---------|-------------|
| **accidents** | ~6,900 | Combined from AAC, Avalanche.org, NPS |
| **weather_patterns** | ~25,000 | 7-day windows for each accident |
| **mp_routes** | ~168,000 | Mountain Project climbing routes |
| **mp_locations** | ~45,000 | Location hierarchy (areas → crags) |
| **historical_predictions** | Growing | Daily safety score history |

---

## Data Sources

| Source | Records | Coverage |
|--------|---------|----------|
| **AAC** (American Alpine Club) | 2,770 | 1990-2019 |
| **Avalanche.org** | 1,372 | 1997-2026 |
| **NPS** (National Park Service) | 848 | Various |
| **Mountain Project** | ~168,000 routes | Complete |

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
- [DEPLOYMENT.md](./DEPLOYMENT.md) - Infrastructure architecture
- [data/DATABASE_STRUCTURE.md](./data/DATABASE_STRUCTURE.md) - Database schema
- [data/README.md](./data/README.md) - Data sources and collection

---

## License

MIT
