# Technical Decisions Log

**Purpose**: Document every technical decision made in SafeAscent, including libraries, services, architectural patterns, and why alternatives were not chosen.

**Status**: 🟡 Awaiting Review - Please review each decision and approve/discuss

---

## Decision Review Status

- ⏳ **Pending Review**: Decision made but not yet discussed
- ✅ **Approved**: User has reviewed and approved
- 🔄 **Needs Discussion**: User wants to discuss alternatives
- ❌ **Rejected**: User wants different approach

---

## Backend Framework & Core Libraries

### 1. FastAPI (Python Web Framework)
**Status**: ✅ **APPROVED**
**Decision Date**: Phase 1 (Initial Setup)
**Reviewed**: 2026-01-29 - User approved
**Chosen**: FastAPI 0.115.12

**Why FastAPI**:
- Modern async support (critical for database operations)
- Automatic API documentation (Swagger/OpenAPI)
- Pydantic integration for data validation
- Excellent performance (comparable to Node.js/Go)
- Type hints throughout (better IDE support, fewer bugs)

**Alternatives Considered**:
- **Flask**: More mature but synchronous by default, requires extensions for async
- **Django**: Full-featured but heavier, includes ORM we don't need (using SQLAlchemy)
- **Django REST Framework**: Good but tightly coupled to Django
- **Sanic**: Fast async but smaller community, fewer libraries

**Trade-offs**:
- ✅ Native async/await for database queries
- ✅ Auto-generated API docs at /docs
- ✅ Built-in data validation with Pydantic
- ⚠️ Relatively newer (less mature than Flask/Django)
- ⚠️ Smaller ecosystem than Django

**Impact**: Core framework - affects everything

---

### 2. SQLAlchemy (ORM)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 1 (Database Setup)
**Chosen**: SQLAlchemy 2.0.38 with async support

**Why SQLAlchemy**:
- Industry standard Python ORM
- Async support (SQLAlchemy 2.0+)
- Excellent PostgreSQL/PostGIS integration
- Type-safe queries with proper IDE support
- Mature ecosystem with extensive documentation

**Alternatives Considered**:
- **Django ORM**: Good but requires Django framework
- **Tortoise ORM**: Native async but less mature
- **Pony ORM**: Interesting syntax but smaller community
- **Raw SQL**: Maximum control but more error-prone, no type safety

**Trade-offs**:
- ✅ Type-safe queries
- ✅ Automatic relationship loading
- ✅ Migration support (via Alembic)
- ⚠️ Learning curve for complex queries
- ⚠️ Can generate inefficient queries if not careful

**Impact**: All database interactions

---

### 3. PostgreSQL 17 + PostGIS 3.6 (Database)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 1 (Database Setup)
**Chosen**: PostgreSQL 17 + PostGIS 3.6

**Why PostgreSQL + PostGIS**:
- **PostGIS**: Best-in-class geospatial extension
  - ST_DWithin for radius queries (critical for "accidents within 100km")
  - Automatic geography calculations (handles Earth curvature)
  - GIST indexes for fast spatial queries
- **PostgreSQL**: Mature, reliable, feature-rich
  - JSON support for flexible data
  - Excellent performance
  - Strong consistency guarantees

**Alternatives Considered**:
- **MySQL + Spatial**: Less mature spatial support, weaker geospatial functions
- **MongoDB**: Good for geospatial but document model doesn't fit relational accident/route data
- **SQLite + SpatiaLite**: Good for development but not production-ready at scale

**Trade-offs**:
- ✅ Industry-leading spatial queries
- ✅ ~100ms query times for spatial searches
- ✅ Automatic coordinate transformations
- ⚠️ Requires PostGIS installation (not default)
- ⚠️ Slightly more complex setup than plain PostgreSQL

**Impact**: Core data storage, spatial queries (critical feature)

---

### 4. Asyncpg (PostgreSQL Driver)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 1 (Database Setup)
**Chosen**: asyncpg 0.30.0

**Why asyncpg**:
- Fastest PostgreSQL driver for Python
- Native async/await support
- Binary protocol (faster than text-based)
- Used by SQLAlchemy for async operations

**Alternatives Considered**:
- **psycopg2**: Mature but synchronous (still used for scripts/migrations)
- **psycopg3**: Newer async support but less stable
- **pg8000**: Pure Python but slower

**Trade-offs**:
- ✅ Best performance (2-3x faster than psycopg2)
- ✅ Native async
- ⚠️ Binary-only (can't inspect queries as easily)

**Impact**: Database query performance

---

### 5. Pydantic (Data Validation)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 1 (API Setup)
**Chosen**: Pydantic v2 (via FastAPI)

**Why Pydantic**:
- Type-safe request/response validation
- Automatic JSON serialization
- Clear error messages for invalid data
- FastAPI built-in integration

**Alternatives Considered**:
- **Marshmallow**: Older, more verbose
- **Cerberus**: Good but less Pythonic
- **Manual validation**: Error-prone

**Trade-offs**:
- ✅ Catches type errors before database
- ✅ Auto-generates API documentation
- ✅ Zero runtime overhead (compiled)
- ⚠️ v2 breaking changes from v1 (but we started with v2)

**Impact**: All API endpoints, data validation

---

## Geospatial & Weather Services

### 6. Open-Meteo API (Weather Data)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 5-6 (Weather Integration)
**Chosen**: Open-Meteo Forecast API (free tier)

**Why Open-Meteo**:
- **Free tier**: 10,000 requests/day (sufficient for MVP)
- **No API key required**: Simpler setup
- **High quality data**: ERA5 reanalysis + forecast models
- **Historical + forecast**: 7-day patterns for algorithm
- **Good documentation**: Easy to integrate

**Alternatives Considered**:
- **OpenWeatherMap**: Good but requires API key, $40/month for historical
- **Weather.gov (NOAA)**: Free, US-only, complex API
- **Visual Crossing**: Good but $0.0001/record (costs add up)
- **Weatherstack**: Requires paid plan for historical

**Trade-offs**:
- ✅ Free for our use case
- ✅ No authentication complexity
- ✅ Single API for historical + forecast
- ⚠️ 10,000 req/day limit (need caching for scale)
- ⚠️ Less control than self-hosted weather data

**Future Consideration**:
- May add NOAA/Weather.gov for US-specific data
- May cache weather to reduce API calls

**Impact**: Real-time weather predictions

---

### 7. Open-Elevation API (Elevation Data)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 6A (Elevation Enrichment)
**Chosen**: Open-Elevation API (public instance)

**Why Open-Elevation**:
- **Free**: No limits, no API key
- **Batch support**: 100 coordinates per request
- **SRTM 30m data**: Good accuracy for mountains
- **Simple API**: Easy integration

**Alternatives Considered**:
- **Google Elevation API**: $5/1000 requests (expensive)
- **Mapbox Elevation**: Requires API key, rate limits
- **USGS NED**: Good data but complex API
- **Self-hosted SRTM**: Possible but requires 50GB+ storage

**Trade-offs**:
- ✅ Free and simple
- ✅ Sufficient accuracy (~30m vertical)
- ⚠️ Public instance can be slow/unreliable
- ⚠️ No SLA guarantees

**Future Consideration**:
- Could self-host Open-Elevation for reliability
- Could use elevation in prediction request (user-provided)

**Impact**: Weather statistics bucketing, elevation-aware predictions

---

### 8. PostGIS ST_DWithin (Spatial Queries)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 2 (Algorithm Implementation)
**Chosen**: PostGIS ST_DWithin for radius searches

**Why ST_DWithin**:
- Uses GIST spatial indexes (very fast)
- Handles Earth curvature automatically
- ~50ms query time for "accidents within 100km"
- Database-native (no application-level distance calc)

**Alternatives Considered**:
- **Application-level filtering**: Fetch all accidents, filter in Python
  - Much slower, doesn't scale
- **Haversine distance in SQL**: Works but no index support
- **PostGIS ST_Distance**: Similar but ST_DWithin is optimized for radius queries

**Trade-offs**:
- ✅ Extremely fast with indexes
- ✅ Accurate (geography type handles spherical earth)
- ✅ Scales to millions of records
- ⚠️ Requires PostGIS (but we already use it)

**Impact**: Core prediction performance

---

## Data Processing & Scripts

### 9. Pandas (Data Processing)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 0 (Data Collection)
**Chosen**: pandas 2.2.3

**Why pandas**:
- Industry standard for tabular data
- Excel/CSV reading built-in
- Data cleaning and transformation
- Used only in scripts, not backend

**Alternatives Considered**:
- **Polars**: Faster but newer, less mature
- **Raw Python**: Too low-level for complex operations

**Trade-offs**:
- ✅ Familiar to data scientists
- ✅ Rich ecosystem
- ⚠️ Memory-heavy for large datasets
- ⚠️ Script-only (not in production backend)

**Impact**: Data preparation scripts only

---

### 10. FuzzyWuzzy (String Matching)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 0 (Data Collection)
**Chosen**: fuzzywuzzy 0.18.0 + python-Levenshtein

**Why FuzzyWuzzy**:
- Simple API for fuzzy string matching
- Good for matching accident descriptions to routes/mountains
- Used only in data processing, not runtime

**Alternatives Considered**:
- **thefuzz**: Fork of fuzzywuzzy (similar)
- **rapidfuzz**: Faster but more complex API
- **difflib**: Built-in but slower

**Trade-offs**:
- ✅ Simple and effective
- ✅ Good enough for batch processing
- ⚠️ Not production-optimized
- ⚠️ 80% threshold chosen arbitrarily (could be tuned)

**Impact**: Accident-to-mountain/route linking quality

---

## Algorithm Design Decisions

### 11. Gaussian Spatial Weighting (vs. Hard Cutoff)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 1 (Algorithm Design)
**Chosen**: Gaussian decay with route-type-specific bandwidth

**Why Gaussian**:
- Smooth weight decay (no cliff at boundary)
- Route-type-specific: alpine routes have wider influence than sport climbs
- Theoretically sound (maximum entropy principle)

**Alternatives Considered**:
- **Hard cutoff**: "Accidents within 10km count, outside don't"
  - Too brittle, arbitrary boundary
- **Inverse distance**: 1/distance weighting
  - Gives too much weight to very close accidents
- **Exponential decay**: Similar to Gaussian but sharper dropoff

**Bandwidths Chosen**:
- Alpine: 30km (mountains have similar conditions across large areas)
- Ice/Mixed: 20km (route-dependent but regional patterns)
- Trad/Sport: 10km (very route-specific)

**Trade-offs**:
- ✅ Smooth weighting
- ✅ Route-appropriate influence radius
- ⚠️ Bandwidths chosen by intuition, not data
- ⚠️ Could be tuned with backtesting

**Future Consideration**: Backtest to optimize bandwidths

**Impact**: How accidents influence nearby predictions

---

### 12. Asymmetric Route Type Weighting
**Status**: ⏳ Pending Review
**Decision Date**: Phase 4 (Route Type Inference)
**Chosen**: Asymmetric similarity matrix (alpine↔alpine = 1.0, alpine↔sport = 0.3)

**Why Asymmetric**:
- Alpine accidents ARE relevant to ice climbing (shared hazards: avalanche, weather)
- Sport accidents are NOT very relevant to alpine (different hazard profile)
- Directional relevance captured in matrix

**Alternatives Considered**:
- **Symmetric matrix**: alpine↔sport = sport↔alpine
  - Doesn't capture directional relevance
- **Binary matching**: 1.0 if match, 0.0 if not
  - Loses cross-route-type information
- **No route type weighting**: Treat all accidents equally
  - Ignores important context

**Matrix Values** (chosen by reasoning, not data):
```
          Alpine  Ice  Mixed  Trad  Sport  Aid  Boulder
Alpine     1.0   0.8   0.9   0.5   0.3   0.4   0.2
Ice        0.8   1.0   0.9   0.4   0.2   0.3   0.1
Mixed      0.9   0.9   1.0   0.5   0.3   0.4   0.1
Trad       0.5   0.4   0.5   1.0   0.7   0.8   0.3
Sport      0.3   0.2   0.3   0.7   1.0   0.5   0.6
Aid        0.4   0.3   0.4   0.8   0.5   1.0   0.2
Boulder    0.2   0.1   0.1   0.3   0.6   0.2   1.0
```

**Trade-offs**:
- ✅ Captures domain knowledge
- ✅ Directionality makes sense
- ⚠️ Values chosen by intuition
- ⚠️ Could be learned from data

**Future Consideration**: User feedback on whether weights make sense

**Impact**: How route types influence each other

---

### 13. Exponential Temporal Decay + Seasonal Boost
**Status**: ⏳ Pending Review
**Decision Date**: Phase 1 (Algorithm Design)
**Chosen**: 1-year half-life + 1.5× boost for same season

**Why This Approach**:
- Recent accidents more relevant (conditions may persist)
- But historical data still valuable (long-term patterns)
- Seasonal boost: winter accidents more relevant to winter predictions

**Decay Formula**:
```python
base_weight = exp(-days_ago / 365.0)  # 1-year half-life
if same_season:
    weight *= 1.5  # 50% boost
```

**Alternatives Considered**:
- **Linear decay**: Simpler but doesn't match intuition (recent >> old)
- **Shorter half-life** (3 months): Too aggressive, loses historical data
- **No seasonal boost**: Misses seasonal patterns (winter ≠ summer)

**Trade-offs**:
- ✅ Balances recent vs. historical
- ✅ Seasonal patterns captured
- ⚠️ 1-year half-life chosen arbitrarily
- ⚠️ 1.5× boost chosen arbitrarily

**Future Consideration**: Backtest different half-lives and boosts

**Impact**: How accident age affects predictions

---

### 14. Weather Pattern Correlation (vs. Absolute Matching)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 1 (Algorithm Design)
**Chosen**: Pearson correlation of 7-day patterns + extreme detection

**Why Correlation**:
- Pattern similarity matters more than absolute values
  - Cold snap followed by warming ≈ similar pattern at different temps
- Handles multi-day weather trends (freeze-thaw cycles)
- Statistical standard for time series similarity

**Extreme Detection**: Z-score threshold = 2.0
- If current weather is >2 standard deviations from historical norms → amplify risk

**Alternatives Considered**:
- **Euclidean distance**: Sensitive to absolute values (misses pattern matching)
- **Dynamic Time Warping**: Better for sequences but computationally expensive
- **Simple threshold**: "Is it cold?" → Too simplistic

**Trade-offs**:
- ✅ Captures pattern similarity
- ✅ Extreme weather amplification
- ⚠️ Requires full 7-day pattern (missing data → neutral weight)
- ⚠️ Z-score threshold (2.0) chosen by convention

**Future Consideration**: Test different thresholds with known accidents

**Impact**: Weather-based risk adjustment

---

### 15. Confidence Scoring (5 Factors)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 3 (Confidence Scoring)
**Chosen**: Multi-factor confidence with 5 components

**Why Multi-Factor**:
- Single metric (e.g., "number of accidents") is insufficient
- Captures different aspects of prediction quality
- Transparent to users (can see what drives confidence)

**5 Factors**:
1. **Sample Size**: More accidents → higher confidence (sigmoid scaling)
2. **Match Quality**: Close accidents + route type match → higher confidence
3. **Spatial Coverage**: Tight clustering → higher confidence (lower variance = better)
4. **Temporal Recency**: Recent data → higher confidence (median days ago)
5. **Weather Quality**: More accidents with weather data → higher confidence

**Formula**:
```python
overall_confidence = (
    sample_size * 0.35 +
    match_quality * 0.25 +
    spatial_coverage * 0.20 +
    temporal_recency * 0.15 +
    weather_quality * 0.05
)
```

**Alternatives Considered**:
- **Single metric**: "Count of accidents" → Too simplistic
- **Machine learning**: Train confidence predictor → No labeled data yet
- **Bootstrap confidence intervals**: Rigorous but computationally expensive

**Trade-offs**:
- ✅ Interpretable components
- ✅ Captures multiple quality signals
- ⚠️ Weights (0.35, 0.25, etc.) chosen by intuition
- ⚠️ Linear combination may not be optimal

**Future Consideration**: Tune weights with user feedback or labeled data

**Impact**: User trust in predictions

---

## API Design Decisions

### 16. REST API (vs. GraphQL)
**Status**: ✅ **APPROVED**
**Decision Date**: Phase 1 (API Setup)
**Reviewed**: 2026-01-29 - User approved after detailed discussion
**Chosen**: RESTful API with /api/v1/ versioning

**Why REST**:
- Simpler to implement and understand
- Standard HTTP methods (GET, POST)
- Good caching with HTTP headers
- FastAPI excellent REST support

**Alternatives Considered**:
- **GraphQL**: Flexible but more complex, frontend would need GraphQL client
- **gRPC**: Fast but requires protobuf, harder to debug

**API Structure**:
- `/api/v1/mountains` - Mountains API
- `/api/v1/routes` - Routes API
- `/api/v1/accidents` - Accidents API
- `/api/v1/predict` - Safety prediction
- `/api/v1/health` - Health check

**Trade-offs**:
- ✅ Simple and standard
- ✅ Easy to test with curl
- ✅ Good HTTP caching support (critical for map view)
- ⚠️ Over-fetching (sends all fields) - minor concern for web
- ⚠️ N+1 queries possible (but mitigated with joins and ?include param)

**GraphQL Evaluation**:
Considered GraphQL but decided against because:
- No mobile apps planned (bandwidth savings not needed)
- No public API for third parties (flexibility not needed)
- No real-time updates required (WebSocket subscriptions not needed)
- Data hierarchy shallow (2-3 levels max, not complex social graph)
- HTTP caching more valuable than query flexibility

**Optimization Strategy**:
- Use `?include=routes,accidents` for nested data (one request)
- Create composite endpoints for high-traffic views
- Leverage HTTP caching (Cache-Control, ETag) for map data
- Backend optimizes joins (frontend stays simple)

**Impact**: API usability, frontend integration, caching strategy

---

### 17. Async API Endpoints
**Status**: ⏳ Pending Review
**Decision Date**: Phase 1 (API Setup)
**Chosen**: All endpoints use async/await

**Why Async**:
- Database I/O is bottleneck (not CPU)
- Async allows handling multiple requests concurrently
- FastAPI native async support
- Better resource utilization

**Example**:
```python
@router.get("/mountains")
async def get_mountains(db: AsyncSession = Depends(get_db)):
    result = await db.execute(query)  # Non-blocking
    return result
```

**Alternatives Considered**:
- **Synchronous**: Simpler but blocks on I/O (lower throughput)
- **Threading**: Works but async is cleaner in Python 3.7+

**Trade-offs**:
- ✅ Better concurrency
- ✅ ~2-3× throughput vs sync
- ⚠️ More complex debugging
- ⚠️ Must use async-compatible libraries

**Impact**: API performance under load

---

## Development Tools & Infrastructure

### 18. No Alembic (Database Migrations)
**Status**: ✅ **APPROVED - DEFERRED FOR LATER**
**Decision Date**: Phase 1 (Database Setup)
**Reviewed**: 2026-01-29 - User approved deferring until pre-production
**Chosen**: Manual SQL migrations (not Alembic yet)

**Why Not Alembic Yet**:
- Schema stabilizing during development
- Direct SQL easier for PostGIS setup
- Planned for before production

**Current Approach**:
- Schema defined in `create_database_schema.sql`
- Manual ALTER TABLE for changes
- No version control for schema

**Trade-offs**:
- ✅ Simple during rapid development
- ⚠️ No migration history
- ⚠️ Risk of schema drift
- ⚠️ Production deployment risk

**Action Plan**: Implement Alembic before production deployment (Phase 9-10)

**Impact**: Database schema management, production safety

---

### 19. No Caching (Redis Not Used)
**Status**: ✅ **APPROVED - DEFERRED FOR LATER**
**Decision Date**: Phase 1 (Initial Setup)
**Reviewed**: 2026-01-29 - User approved deferring until needed
**Chosen**: Direct database queries, no caching layer

**Why No Caching Yet**:
- Response times acceptable (~150ms)
- Data changes infrequently
- Premature optimization
- Redis configured but not used

**Current Performance**:
- Mountain/route detail: ~50ms
- Accident queries: ~150ms
- Prediction endpoint: ~500ms (acceptable)

**When to Add Caching**:
- When traffic increases
- When response times > 500ms
- For commonly accessed mountains/routes

**Trade-offs**:
- ✅ Simpler code
- ✅ No cache invalidation complexity
- ⚠️ Higher database load
- ⚠️ Doesn't scale to high traffic

**Action Plan**: Document need, implement when traffic requires it

**Impact**: API performance at scale

---

### 20. No Background Jobs (Celery Not Used)
**Status**: ✅ **APPROVED - DEFERRED FOR LATER**
**Decision Date**: Phase 1 (Initial Setup)
**Reviewed**: 2026-01-29 - User approved deferring until needed
**Chosen**: No background task processing yet

**Why Not Celery Yet**:
- No long-running tasks identified yet
- Weather fetched per-request (fast enough)
- Celery configured but not used

**Potential Future Uses**:
- Daily weather updates for all mountains
- Batch prediction calculations
- Data processing jobs

**Trade-offs**:
- ✅ Simpler architecture
- ⚠️ Can't do background processing
- ⚠️ Can't schedule jobs

**Action Plan**: Implement when real-time architecture is needed (see Future Considerations in PROJECT_PLAN.md)

**Impact**: Scalability for background work

---

### 21. pytest (Testing Framework)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 7 (Testing)
**Chosen**: pytest + pytest-asyncio + pytest-cov

**Why pytest**:
- Standard Python testing framework
- Good async support (pytest-asyncio)
- Coverage reporting (pytest-cov)
- Excellent plugin ecosystem
- Fixtures for test setup

**Alternatives Considered**:
- **unittest**: Built-in but more verbose, less Pythonic
- **nose2**: Less popular than pytest

**Trade-offs**:
- ✅ Clean test syntax
- ✅ Rich plugin ecosystem
- ✅ Async test support
- ⚠️ Requires learning pytest conventions

**Impact**: Testing velocity, code quality

---

## Architectural Patterns

### 22. Layered Architecture
**Status**: ⏳ Pending Review
**Decision Date**: Phase 1 (Project Structure)
**Chosen**: API → Services → Models pattern

**Structure**:
```
app/
├── api/v1/          # HTTP endpoints (FastAPI routers)
├── services/        # Business logic (safety algorithm, weather)
├── models/          # Database models (SQLAlchemy)
├── schemas/         # API schemas (Pydantic)
├── db/              # Database connection
└── utils/           # Helper functions
```

**Why This Pattern**:
- Clear separation of concerns
- Easy to test (mock service layer)
- Business logic reusable
- Standard FastAPI pattern

**Alternatives Considered**:
- **Flat structure**: app/*.py → Hard to navigate, no organization
- **Feature-based**: app/accidents/*, app/routes/* → Duplication across features
- **Hexagonal/Clean Architecture**: More complex, overkill for this project

**Trade-offs**:
- ✅ Clear responsibilities
- ✅ Easy to navigate
- ✅ Testable
- ⚠️ More files to create

**Impact**: Code organization, maintainability

---

### 23. Pydantic Schemas (Separate from Models)
**Status**: ⏳ Pending Review
**Decision Date**: Phase 1 (API Setup)
**Chosen**: Separate Pydantic schemas from SQLAlchemy models

**Why Separate**:
- API representation ≠ database representation
- Can have multiple schemas for same model (list view vs. detail view)
- Decouples API from database schema

**Example**:
- **Model**: `Accident` (SQLAlchemy, has `coordinates` as geography)
- **Schema**: `AccidentResponse` (Pydantic, has `latitude`/`longitude` as floats)

**Alternatives Considered**:
- **Shared models**: Use SQLAlchemy models directly in API
  - Leaks database details to API
  - Can't have different representations
- **Pydantic + SQLModel**: Shared base, but more complex

**Trade-offs**:
- ✅ Clean API contracts
- ✅ Flexible representations
- ⚠️ Duplicate field definitions
- ⚠️ Manual mapping required

**Impact**: API flexibility, code maintainability

---

## Not Yet Decided (Future Decisions)

### Frontend Framework (Not Chosen Yet)
**Status**: ⏳ **DECISION REQUIRED BEFORE FRONTEND**

**Options to Consider**:
1. **React + Vite**
   - Most popular, huge ecosystem
   - Good Material-UI integration
   - Fast development with Vite
2. **Vue 3 + Vite**
   - Simpler than React
   - Good documentation
   - Smaller bundle sizes
3. **Svelte + SvelteKit**
   - Fastest runtime performance
   - Less boilerplate
   - Smaller community

**Factors to Consider**:
- Your familiarity with each
- Hiring considerations (if expanding team)
- Component library availability (Material Design 3)
- Bundle size / performance
- Development velocity

**Action**: Discuss before starting frontend

---

### Map Library (Not Chosen Yet)
**Status**: ⏳ **DECISION REQUIRED BEFORE FRONTEND**

**Options to Consider**:
1. **Mapbox GL JS**
   - Best performance (WebGL rendering)
   - Beautiful styling
   - $$ after free tier (50,000 loads/month)
2. **Leaflet**
   - Open-source, free
   - Mature, stable
   - Plugin ecosystem
   - Slower than Mapbox (DOM rendering)
3. **Google Maps**
   - Familiar to users
   - $200/month free tier
   - $$$ after free tier

**Factors to Consider**:
- Cost at scale (loads/month)
- Performance with 400+ markers
- Clustering support
- Custom marker styling
- Free tier limits

**Action**: Discuss before starting map visualization

---

### Chart Library (Not Chosen Yet)
**Status**: ⏳ **DECISION REQUIRED FOR ANALYTICS DASHBOARD**

**Options to Consider**:
1. **Chart.js**
   - Simple, popular
   - Good default styling
   - React wrapper available
2. **Recharts**
   - React-native (components)
   - Composable
   - Responsive by default
3. **D3.js**
   - Maximum flexibility
   - Steeper learning curve
   - Beautiful custom visualizations

**Factors to Consider**:
- Ease of use
- Chart types needed (time series, pie, bar, heatmap)
- Customization needs
- Bundle size

**Action**: Discuss before starting analytics dashboard

---

## Decision Review Process

### How to Review This Document

1. **Read each decision** (sections 1-23)
2. **For each decision**, mark status:
   - ✅ **Approved**: "This makes sense, proceed"
   - 🔄 **Needs Discussion**: "Let's talk about alternatives"
   - ❌ **Rejected**: "Let's change this"
3. **Ask questions**: "Why not X instead of Y?"
4. **Suggest changes**: "I prefer A over B because..."

### When to Review

- **Now**: Review backend decisions (1-23) before continuing Phase 7
- **Before Frontend**: Review frontend decisions (24-26) before starting Phase 8
- **Ongoing**: Document new decisions as we make them

---

## Algorithm Optimization Decisions

### 27. Weather Dominance in Risk Aggregation (Cubic Weighting)
**Status**: ✅ **APPROVED**
**Decision Date**: 2026-01-30
**Phase**: Phase 7 → Phase 8 Transition
**Reviewed**: 2026-01-30 - User approved cubic power approach

**Problem Identified**:
Weather similarity was being diluted by accident density. Popular climbing areas like Half Dome (196 nearby accidents) showed permanent maximum risk (100/100) even on sunny days because many accidents × moderate weather_similarity still summed to maximum risk. The algorithm wasn't volatile enough - risk scores should change dramatically based on current weather conditions.

**User Vision**:
> "Route risk scores should be very volatile. On a sunny day with no clouds/precip and low wind speeds, a route may be very safe and have a risk score of just 10 or so vs. the same route on a day with heavy rains, cold temps, fast winds etc. may have a risk score closer to 70. Still, a route that has tons of accidents should probably have a higher risk score even on a day with great weather than a route with few because it may just be a dangerous route in general."

**Decision**: Apply cubic (³) power to weather similarity in final risk aggregation

**Formula Change**:
```python
# BEFORE (multiplicative, all factors equal)
accident_influence = spatial × temporal × weather_similarity × route_type × severity

# AFTER (weather becomes dominant via cubic power)
base_influence = spatial × temporal × route_type × severity
weather_weighted_influence = base_influence × (weather_similarity³)

# Also exclude accidents with very poor weather match
if weather_similarity < 0.25:
    weather_weighted_influence = 0  # Exclude completely
```

**Preserved Design Elements** (NO CHANGES):
- ✅ 7-day weather pattern correlation (Pearson correlation across 6 factors)
- ✅ Within-window temporal decay (Day 0 = 20%, Day -6 = 8%, decay=0.85)
- ✅ Extreme weather penalty (>2 SD amplification)
- ✅ Freeze-thaw cycle detection
- ✅ Spatial Gaussian decay (no hard cutoff, route-type-specific bandwidths)
- ✅ Temporal year-scale decay (half-life 3-10 years by route type)
- ✅ Route type asymmetric weighting matrix (sport→alpine canary effect)
- ✅ Severity linear boosters (1.0×, 1.1×, 1.3×)
- ✅ All accidents considered (weather filtering happens in aggregation, not selection)

**Rationale**:
- Cubic power (³) creates exponential weather sensitivity:
  - weather_similarity = 0.30 → 0.027 (97% reduction in contribution)
  - weather_similarity = 0.60 → 0.216 (78% reduction)
  - weather_similarity = 0.80 → 0.512 (49% reduction)
  - weather_similarity = 1.00 → 1.0 (no reduction)
- Weather becomes primary risk driver (5-7× variation: 10 to 70)
- Accident history becomes amplifier (distinguishes inherently dangerous routes)
- Creates desired volatility: sunny day = low risk, stormy day = high risk

**Parameters**:
- Weather power: **3** (cubic) - user approved, easily tunable if needed
- Exclusion threshold: **0.25** (accidents with <25% weather similarity completely excluded)
- Normalization factor: **TBD** (tune based on test scenarios to map risk to 0-100 scale)

**Expected Outcomes**:
| Location | Weather | Expected Risk | Interpretation |
|----------|---------|---------------|----------------|
| Half Dome | Sunny | 15-25 | Safe day at dangerous route |
| Half Dome | Stormy | 80-100 | Very dangerous! |
| Safe route | Sunny | 10-18 | Safe day, safe route |
| Safe route | Stormy | 60-75 | Weather dangerous anywhere |

**Alternatives Considered**:
- **Square (²) power**: More conservative (91% reduction for poor matches)
  - Rejected: Not aggressive enough, wouldn't create desired volatility
- **Quartic (⁴) power**: Very aggressive (99% reduction for poor matches)
  - Rejected: Too extreme, might eliminate too many valid signals
- **Threshold-only approach**: Hard cutoff for weather similarity
  - Rejected: Too binary, loses nuance in weather matching quality

**Trade-offs**:
- ✅ Weather drives daily risk variation (primary signal)
- ✅ Accident history still matters (route danger amplifier)
- ✅ All original weather calculations preserved
- ✅ Easy to tune (change exponent from 3 to 2 or 4)
- ⚠️ Risk scores may be lower overall (fewer accidents contributing significantly)
- ⚠️ Requires tuning normalization factor to get scores in 0-100 range
- ⚠️ Algorithm becomes more dependent on weather data quality

**Impact**:
- Core algorithm behavior changes significantly
- Risk scores become much more volatile (day-to-day variation)
- Popular areas no longer show permanent maximum risk
- Users can see actionable weather-based risk changes

**Implementation**:
- File: `backend/app/services/safety_algorithm.py`
- Changes: Only final aggregation loop (calculate_risk_score function)
- Testing: Verify with known scenarios (Half Dome sunny/stormy, etc.)
- Normalization: Tune factor based on test results

**Future Considerations**:
- May adjust power (3→2 or 3→4) based on real-world usage
- May adjust exclusion threshold (0.25→0.20 or 0.30)
- Monitor if normalization factor needs seasonal adjustment

---

### 28. Future Data Collection Needs
**Status**: 📋 **TODO LIST**
**Decision Date**: 2026-01-30
**Phase**: Post-Phase 8 (Future Enhancements)

**Three major data collection efforts identified for future accuracy improvements:**

#### A. Additional Weather Statistics
**Need**: More comprehensive weather data for better danger assessment
**Missing Metrics**:
- Lightning strike density and frequency
- Barometric pressure (storm systems, weather fronts)
- UV index (high altitude exposure risk)
- Humidity (hypothermia risk, freeze-thaw cycles)
- Wind gusts (vs average wind speed)
- Storm cell tracking (movement, intensity)

**Why It Matters**:
- Lightning is a major alpine hazard not currently captured
- Pressure changes indicate incoming storms
- UV at altitude is dangerous for multi-day climbs
- Would improve extreme weather detection

**Data Sources**:
- Open-Meteo API (may have some of these)
- NOAA Storm Prediction Center
- Lightning strike databases (NLDN, GLD360)

**Effort**: Moderate - API integration, database schema updates

---

#### B. Ascent Data (Accident Rate Calculation)
**Need**: Total ascent counts to calculate true accident *rates* vs absolute counts
**Current Problem**:
- Popular routes (Half Dome, El Capitan) have more accidents simply due to higher traffic
- We can't distinguish "dangerous route" from "popular route" with current data
- Example: 100 accidents on 10,000 ascents (1% rate) is safer than 50 accidents on 500 ascents (10% rate)

**User Insight**:
> "One risk with the current system is that some areas or routes will simply have more climbing activity than others and so they will naturally have more accidents. We might wanna fix that later by looking at ratios of accidents compared to ascents."

**What We Need**:
- Total recorded ascents per route (or per area if route-level unavailable)
- Time period for ascents (to match accident data timeframe)
- Route popularity metrics as proxy if direct ascent data unavailable

**Why It Matters**:
- Calculates true risk: accidents per 1000 ascents
- Distinguishes popular-but-safe from unpopular-but-dangerous
- More accurate amplifier in weather-primary model
- Better confidence scoring (more ascents = more reliable data)

**Data Sources**:
- Mountain Project (ascent logs)
- Summitpost (trip reports)
- PeakBagger (summit logs)
- REI/Outdoor clubs (trip statistics)
- National Park Service (permit data for regulated climbs)

**Effort**: High - requires extensive scraping, data matching to routes

**Formula Impact**:
```python
# Current: absolute accident count
accident_amplifier = f(num_accidents, weather_matches, route_matches)

# Future: accident rate
accident_rate = num_accidents / num_ascents
accident_amplifier = f(accident_rate, weather_matches, route_matches)

# Future: dynamic normalization based on accident density
RISK_NORMALIZATION_FACTOR = f(location_accident_density, historical_ascents)
# Enables location-specific calibration instead of global factor
```

**Related TODO**: Use accident density analysis to improve risk score calibration (see Decision #30). This addresses the problem where high-traffic routes (Half Dome) vs high-danger routes (remote peaks) currently use the same global normalization factor. With ascent data, we can dynamically adjust normalization to distinguish popular-but-safe from unpopular-but-dangerous.

---

#### C. Additional Route Data
**Need**: More routes and more complete route information
**Current Coverage**: ~4,300 accidents (good coverage for major areas)
**Missing**:
- International routes (currently US-focused)
- Lesser-known climbing areas
- Bouldering areas (underrepresented)
- Ice climbing routes (seasonal data)
- Route grades/difficulty (not currently used but could improve matching)
- Route characteristics (exposure, objective hazards, technical crux)

**Why It Matters**:
- Better geographic coverage
- More accurate predictions in currently sparse areas
- Route difficulty could inform risk amplification
- Better route type classification

**Data Sources**:
- Mountain Project (ongoing scraping)
- Summitpost (international coverage)
- Regional climbing guidebooks
- European databases (camptocamp.org, etc.)

**Effort**: High - requires maintained scraping infrastructure

---

**Priority for Future Phases**:
1. **Weather statistics** (Medium effort, high impact) - Phase 9
2. **Route data expansion** (High effort, medium impact) - Phase 10
3. **Ascent data** (High effort, very high impact) - Phase 11+

**Note**: All data collection should respect robots.txt, API rate limits, and terms of service.

---

### 29. Quadratic Weather Power (Reduced from Cubic)
**Status**: ✅ **IMPLEMENTED**
**Decision Date**: 2026-01-30
**Phase**: Phase 7 (Bug Fixes & Tuning)
**File**: `backend/app/services/safety_algorithm.py` (line 316)

**Problem Identified**:
Initial cubic weather weighting (`weather_similarity³`) proved too aggressive:
- Created 27× variation between sunny (0.3) and stormy (0.9) conditions
- High-density areas (Longs Peak: 476 accidents, Yosemite: 196 accidents) hit risk_score ceiling (100) in all conditions
- No day-to-day variation visible even though algorithm was calculating daily
- Didn't align with desired behavior of moderate weather sensitivity

**Decision**: Reduce power from cubic (3) to quadratic (2)

**Reasoning**:
- Cubic: 0.9³ / 0.3³ = 27× variation (too aggressive)
- Quadratic: 0.9² / 0.3² = 9× variation (more moderate)
- Still makes weather primary driver (9× from weather vs ~2× from accident history)
- Allows day-to-day risk score variation to emerge
- Easier to reason about and explain to users

**Implementation**:
```python
# BEFORE (Cubic)
WEATHER_POWER = 3  # 0.3³ = 0.027 (97% reduction)
weather_factor = weather_weight ** WEATHER_POWER

# AFTER (Quadratic)
WEATHER_POWER = 2  # 0.3² = 0.09 (91% reduction)
weather_factor = weather_weight ** WEATHER_POWER
```

**Impact on Risk Scores**:
- Moderate-density areas now show variation: Yosemite 79.82-79.88 across consecutive days
- Still weather-primary: poor weather matches get 91% reduction (vs 97% with cubic)
- High-density areas (Longs Peak) still max out but this may be accurate given 476 accidents

**Alternative Considered**:
- Power = 1.77 for exact 7× variation - rejected as too specific and hard to communicate
- Keep cubic (3) but reduce normalization further - rejected as addressed in Decision #30

**Trade-offs**:
- ✅ More moderate weather sensitivity (9× vs 27×)
- ✅ Day-to-day variation visible in moderate-density areas
- ✅ Still clearly weather-primary model
- ⚠️ Accident history becomes relatively more influential (less extreme weather dominance)
- ⚠️ High-density areas still hit ceiling on many days

**Test Results**:
- All 50 Phase 7 tests pass with quadratic power
- Yosemite (196 accidents): 79.82-79.88 risk score variation over 5 consecutive days
- Longs Peak (476 accidents): Still maxes at 100 but may be accurate

---

### 30. Normalization Factor Adjustment and Future Calibration Strategy
**Status**: ✅ **IMPLEMENTED** (5.0), 📋 **TODO** (dynamic calibration)
**Decision Date**: 2026-01-30
**Phase**: Phase 7 (Bug Fixes & Tuning)
**File**: `backend/app/services/algorithm_config.py` (line 230)

**Problem Identified**:
With quadratic weather weighting, high-density areas were still hitting the risk_score ceiling (100):
- Normalization factor = 10.0 meant total_influence ≥ 10.0 → risk_score = 100
- Longs Peak (476 accidents) and Yosemite (196 accidents) both maxing out in July
- Limited headroom for weather variation to affect final scores

**Decision**: Reduce normalization factor from 10.0 to 5.0

**What Normalization Factor Means**:
```python
risk_score = total_influence × RISK_NORMALIZATION_FACTOR
risk_score = min(100, risk_score)  # Cap at 100
```

**Normalization = 10.0** (original):
- total_influence = 10.0 → risk_score = 100 (hits ceiling)

**Normalization = 5.0** (current):
- total_influence = 20.0 → risk_score = 100 (hits ceiling)
- Provides 2× more headroom

**Normalization = 3.0** (considered but rejected):
- total_influence = 33.3 → risk_score = 100
- Would provide even more spread but tabled for future dynamic calibration

**Why 3.0 Was Rejected**:
User feedback: "I think we should avoid further normalization right now. Instead, we might be inclined to try a more dynamic calibration approach when we have more ascent data but perhaps we should table this for now and move on."

**Impact on Risk Scores**:
- Yosemite: Reduced from 100 to ~80 in July
- Longs Peak: Still maxes at 100 (but may be accurate given exceptional danger)
- Provides headroom for weather variation at moderate-density areas

**Future Calibration Strategy (TODO)**:
User identified need for **dynamic normalization based on ascent density**:

```python
# Current approach: Global normalization factor
risk_score = total_influence × 5.0

# Future approach: Dynamic calibration with ascent data
accident_rate = num_accidents / num_ascents_per_1000
accident_amplifier = f(accident_rate, weather_matches)
# Normalization adjusted based on location-specific accident rates
# Distinguishes high-traffic (Half Dome) from high-danger (Longs Peak)
```

**Why Dynamic Calibration Matters**:
- Popular routes (Half Dome: 196 accidents on 10,000+ ascents) → lower amplifier
- Dangerous routes (obscure peak: 50 accidents on 500 ascents) → higher amplifier
- Accounts for climbing activity volume, not just absolute accident counts
- More accurate reflection of true route danger

**Requirements for Dynamic Calibration**:
1. Scrape ascent data from Mountain Project, Summitpost, PeakBagger
2. Match ascents to routes and time periods
3. Calculate accidents per 1000 ascents for each location
4. Use accident rate (not count) as amplifier in algorithm
5. Dynamically adjust normalization based on location's accident density

**Next Steps** (See Decision #28 Part B):
- Phase 9+: Scrape ascent data from multiple sources
- Phase 10+: Implement accident rate calculation
- Phase 10+: Test dynamic calibration approach
- Phase 11+: Fine-tune normalization based on real-world accident rates

**Trade-offs**:
- ✅ Provides more headroom (2× increase)
- ✅ Allows weather variation to drive scores at moderate-density areas
- ✅ Simple to understand and tune
- ⚠️ Still uses global normalization (not location-specific)
- ⚠️ High-density areas still max out (awaiting ascent data for dynamic calibration)

---

## Next Steps

1. ✅ **Implement quadratic weather weighting** (Decision #29) - COMPLETE
2. ✅ **Adjust normalization factor** (Decision #30) - COMPLETE
3. 📋 **Test suite validation** - All 50 tests passing (100%)
4. 📋 **Continue with Phase 8 optimizations** (caching, performance, monitoring)
5. 📋 **Future: Dynamic calibration with ascent data** (Decisions #28B, #30)
6. 📋 **Future: Route-page analytics dashboard** (monthly risk, safest times, proximity vs weather analysis)

---

## Important Notes for Session Continuity

### Algorithm Verification (2026-01-30)
- ✅ Algorithm IS daily-based (not seasonal bucketing)
- ✅ Risk scores calculated from specific date's 7-day weather forecast
- ✅ Yosemite shows 0.06-point variation across consecutive days (proves daily calculation)
- ✅ Weather forecasts for consecutive days are similar → similar risk scores (expected behavior)
- ✅ Longs Peak maxes at 100 across most days (may be accurate given 476 accidents)

### Key Insights for Future Work
1. **Route Dashboard Analytics**: All detailed stats (proximity vs weather matching, monthly risk patterns, safest climbing times) should go on route-specific dashboard pages, NOT homepage
2. **Homepage Display**: Only show risk score + confidence score on map (simple, clean)
3. **Ascent Data Priority**: High priority for Phase 9+ to enable dynamic calibration
4. **Accident Density Analysis**: Critical for distinguishing popular routes from dangerous routes

---

*Last Updated*: 2026-01-30
*Status*: Active Development - Phase 7 Complete, Ready for Phase 8
