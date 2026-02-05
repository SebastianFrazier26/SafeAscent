# SafeAscent Performance Fix Implementation Plan

**Created**: 2026-02-04
**Status**: ✅ Complete
**Authors**: Sebastian Frazier + Claude (Opus 4.5)

---

## Problem Summary

The live site at safeascent.us is experiencing critical performance issues:

1. **Primary Issue**: Frontend attempts to fetch individual safety scores for 273,424 routes
2. **Result**: Site takes ~76 hours to fully load (completely unusable)
3. **Root Cause**: Missing pre-computation architecture - every safety score is calculated on-demand

### Observed Behavior
- `/api/v1/mp-routes/map` returns 273,424 routes successfully
- Frontend then makes individual `POST /mp-routes/{id}/safety` calls for EACH route
- After 25 seconds, only 40/273,424 routes (0.01%) had safety scores

---

## Agreed Design Decisions

### Decision 1: Pre-compute Safety Scores Nightly
- **What**: Run a nightly job (e.g., 2am) that computes safety scores for all routes for the next 7 days
- **Storage**: Redis cache with keys like `safety:{route_id}:{date}`
- **Rationale**: Transforms 273K real-time calculations into 273K cache lookups (~200ms vs 76 hours)
- **Cache Miss Handling**: If cache is cold, recalculate on-demand (graceful degradation)

### Decision 2: Split Rock vs Ice Views
- **Current UI**: "All" / "Summer ☀️" / "Winter ❄️" (loads all routes, filters client-side)
- **New UI**: "Rock 🪨" / "Ice ❄️" (loads only relevant routes from server)
- **Default View**: Rock (more common climbing)

### Decision 3: Route Type Categorization
```
WINTER/ICE routes: route.type INCLUDES "ice" OR route.type INCLUDES "mixed"
SUMMER/ROCK routes: Everything else (including alpine WITHOUT ice/mixed in type)
```

### Decision 4: Remove Boulder Routes Entirely
- Boulder routes have different risk profiles and shouldn't be in the safety prediction system
- **Action**: DELETE all boulder routes from the database
- **Benefit**: Reduces dataset size and removes irrelevant data

### Decision 5: Fix Backend Sync Blocking
- `weather_service.py:263-290` uses synchronous psycopg2 in async context
- **Action**: Convert to async SQLAlchemy query
- **Rationale**: Prevents server freezing under concurrent load

---

## Implementation Phases

### Phase 1: Database Cleanup - Remove Boulder Routes
**Effort**: 30 minutes
**Status**: ✅ Complete

**Results**:
- Deleted **105,369** boulder routes (38.5% of total)
- Database reduced from **273,424** → **168,055** routes
- 39% reduction in dataset size

**Files Changed**:
- `scripts/cleanup_boulder_routes.py` (new)
- Database: Deleted all rows where `type ILIKE '%boulder%'`

---

### Phase 2: API - Add Route Type Filter to Map Endpoint
**Effort**: 1-2 hours
**Status**: ✅ Complete

**Tasks**:
- [x] Add `season` query parameter to `/api/v1/mp-routes/map` endpoint
- [x] Implement filtering logic using SQLAlchemy `func.lower().contains()`
- [x] Default to `rock` (non-ice routes)

**Files Changed**:
- `backend/app/api/v1/mp_routes.py`

---

### Phase 3: Frontend - Update to Rock/Ice View Toggle
**Effort**: 2-3 hours
**Status**: ✅ Complete

**Tasks**:
- [x] Replace "All/Summer/Winter" toggle with "Rock/Ice" toggle
- [x] Update MapView.jsx to pass `season` parameter to API
- [x] Set default view to "Rock"
- [x] Remove client-side route type filtering (now done server-side)
- [x] Update UI labels and icons
- [x] Re-fetch routes when season changes (with reset of safety score state)

**Files Changed**:
- `frontend/src/components/MapView.jsx`

---

### Phase 4: Backend - Create Pre-computed Safety Cache Schema
**Effort**: 2-3 hours
**Status**: ✅ Complete

**Tasks**:
- [x] Fixed Redis connection to use `settings.REDIS_URL` (was hardcoded to localhost)
- [x] Added TTL constants: `SAFETY_SCORE_TTL = 7 days`
- [x] Added bulk cache functions using MGET/pipeline for performance:
  - `get_cached_safety_score(route_id, date)` - single get
  - `set_cached_safety_score(route_id, date, ...)` - single set
  - `get_bulk_cached_safety_scores(route_ids, date)` - bulk get via MGET
  - `set_bulk_cached_safety_scores(scores, date)` - bulk set via pipeline
  - `get_safety_cache_stats(date)` - monitoring

**Files Changed**:
- `backend/app/utils/cache.py`

---

### Phase 5: Backend - Build Nightly Computation Job
**Effort**: 4-6 hours
**Status**: ✅ Complete

**Tasks**:
- [x] Created Celery task: `compute_daily_safety_scores`
- [x] Processes ALL routes (via location join) for 7 days ahead
- [x] Uses bulk cache operations for efficiency
- [x] Added Celery Beat schedule (2am UTC daily)
- [x] Added progress logging every 1000 routes
- [x] Added helper task: `compute_safety_for_single_date` for manual runs

**Performance Optimizations** (added post-implementation):
- [x] **Parallelization**: `asyncio.gather` with semaphore (20 concurrent)
- [x] **Location buckets**: Routes within 0.01° share weather data
- [x] **Batch size**: Increased from 100 → 200
- [x] **Estimated runtime**: ~10-20 minutes (vs 30-60 min without optimizations)

**Tuning Constants** (in `safety_computation.py`):
```python
BATCH_SIZE = 200          # Routes per batch
CONCURRENCY_LIMIT = 20    # Max concurrent calculations
LOG_INTERVAL = 1000       # Progress logging frequency
LOCATION_BUCKET_PRECISION = 2  # 0.01° ≈ 1km buckets
```

**Files Changed**:
- `backend/app/tasks/safety_computation.py` (new)
- `backend/app/celery_app.py` (added schedule)

---

### Phase 6: Backend - New Bulk Safety Endpoint
**Effort**: 2-3 hours
**Status**: ✅ Complete

**Tasks**:
- [x] Created endpoint: `GET /api/v1/mp-routes/map-with-safety`
- [x] Query parameters: `target_date`, `season` (rock/winter)
- [x] Response includes routes with embedded safety scores + metadata
- [x] Uses bulk MGET for efficient cache lookup
- [x] Returns `safety=None` for cache misses (doesn't compute on-demand to stay fast)

**Files Changed**:
- `backend/app/api/v1/mp_routes.py` (added endpoint)
- `backend/app/schemas/mp_route.py` (added schemas: SafetyScore, MpRouteWithSafety, MpRouteMapWithSafetyResponse)

---

### Phase 7: Frontend - Use New Bulk Endpoint
**Effort**: 2-3 hours
**Status**: ✅ Complete

**Tasks**:
- [x] Changed fetch URL to `/mp-routes/map-with-safety?target_date=X&season=Y`
- [x] Removed individual safety fetching useEffects (~150 lines of code deleted!)
- [x] Extract safety scores from bulk response and embed in GeoJSON properties
- [x] Re-fetch when date or season changes (single request each time)
- [x] Route colors now display immediately from response

**Performance Impact**:
- **Before**: 1 request + 168K individual safety requests = ~76 hours
- **After**: 1 bulk request = ~2-3 seconds

**Files Changed**:
- `frontend/src/components/MapView.jsx` (major simplification)

---

### Phase 8: Backend - Fix Sync Blocking in Weather Service
**Effort**: 1-2 hours
**Status**: ✅ Complete

**Tasks**:
- [x] Converted `fetch_weather_statistics()` from sync to async
- [x] Replaced psycopg2 with async SQLAlchemy text query
- [x] Removed hardcoded credential defaults (now uses DATABASE_URL via session)
- [x] Updated caller in `predict.py` to use `await` and pass db session

**Files Changed**:
- `backend/app/services/weather_service.py`
- `backend/app/api/v1/predict.py`

---

## Testing Plan

### Unit Tests
- [ ] Test route type categorization (rock vs ice)
- [ ] Test cache get/set functions
- [ ] Test bulk safety endpoint with mock cache

### Integration Tests
- [ ] Test nightly job with small subset of routes
- [ ] Test frontend with new endpoint
- [ ] Test cache miss handling

### Load Tests
- [ ] Verify bulk endpoint responds in <500ms for 150K routes
- [ ] Verify concurrent requests don't block (after sync fix)

---

## Rollback Plan

If issues arise after deployment:

1. **Bulk endpoint failing**: Revert frontend to old individual-fetching (slow but works)
2. **Cache corruption**: Clear Redis cache, trigger manual recomputation
3. **Nightly job failing**: Manual trigger via admin endpoint

---

## Progress Log

### 2026-02-04
- [x] Diagnosed performance issue on live site
- [x] Identified root cause: individual safety score fetching
- [x] Agreed on pre-computation architecture
- [x] Agreed on Rock/Ice view split
- [x] Created this implementation plan
- [x] **Phase 1 COMPLETE**: Deleted 105,369 boulder routes (273K → 168K routes)
- [x] **Phase 2 COMPLETE**: Added `season` query parameter to `/mp-routes/map` API
- [x] **Phase 3 COMPLETE**: Frontend Rock/Ice toggle with server-side filtering
- [x] **Phase 4 COMPLETE**: Redis cache schema with bulk MGET/pipeline operations
- [x] **Phase 5 COMPLETE**: Nightly Celery job for pre-computing safety scores
- [x] **Phase 6 COMPLETE**: Bulk endpoint `/mp-routes/map-with-safety`
- [x] **Phase 7 COMPLETE**: Frontend uses bulk endpoint (168K API calls → 1)
- [x] **Phase 8 COMPLETE**: Fixed sync blocking in weather_service.py

🎉 **ALL PHASES COMPLETE!**

---

## Open Questions (Resolved)

| Question | Resolution |
|----------|------------|
| Redis vs database for cache? | Redis (volatility acceptable, recalculate nightly) |
| How to handle no nearby accidents? | Algorithm already handles via confidence scoring |
| What to do with boulder routes? | DELETE from database entirely |
| How to categorize alpine routes? | Rock unless type includes ice/mixed |
| Default view? | Rock |

---

## Notes

- This document should be updated as we complete each phase
- If session breaks, resume from the last completed phase
- All code changes should be committed incrementally with descriptive messages
