# SafeAscent Performance Baseline

**Date**: 2026-01-29
**Test Environment**: MacOS (Darwin 23.6.0), PostgreSQL 17 + PostGIS 3.6
**Database**: 3,998 accidents with coordinates, ~30k weather records (backfill in progress)

---

## Current Performance Metrics

### API Response Times (p50)

| Query Type | Accidents | Response Time | Status |
|------------|-----------|---------------|--------|
| Small radius (25km) | ~100 | **718ms** | ⚠️ Needs optimization |
| Medium radius (100km) | ~900 | **1,446ms** | ⚠️ Needs optimization |
| Large radius (300km) | ~1,300 | **1,983ms** | ⚠️ Needs optimization |
| Validation errors | N/A | **1ms** | ✅ Excellent |

### Algorithm Scaling

**Key Finding**: Algorithm scales **linearly (O(n))** as designed ✅

| Radius | Accidents | Avg Time | Time/Accident |
|--------|-----------|----------|---------------|
| 25km   | 107       | 718ms    | 6.7ms |
| 50km   | 302       | 836ms    | 2.8ms |
| 100km  | 931       | 1,446ms  | 1.6ms |
| 150km  | 1,025     | 1,543ms  | 1.5ms |
| 200km  | 1,265     | 1,727ms  | 1.4ms |
| 300km  | 1,323     | 1,983ms  | **1.5ms** |

**Analysis**: Time per accident **decreases** with scale, indicating excellent algorithm efficiency. The ~700ms base overhead dominates small queries but becomes proportionally smaller at scale.

### Concurrent Performance

- **10 concurrent requests**: 13.3s total (~1.33s per request average)
- **Throughput**: ~0.75 requests/second under load
- **Status**: ⚠️ Needs connection pooling optimization

### Memory Usage

- **Response size**: ~15KB typical, <100KB maximum ✅
- **Status**: Well within acceptable limits

---

## Performance Bottlenecks Identified

### 1. Database Connection Overhead (~300-500ms)

**Cause**: Test infrastructure disposes engine before each test to avoid event loop conflicts. In production, connection pooling will reduce this significantly.

**Impact**: High
**Priority**: Will improve naturally in production deployment

### 2. Weather Pattern Fetching (~200-400ms)

**Cause**: Fetching 7-day weather windows for each accident in the result set.

**Current**:
```python
for accident in accidents:
    weather_records = await fetch_weather_for_accident(accident_id)
```

**Impact**: Scales with number of accidents
**Optimization**: Batch fetch weather data in single query
**Priority**: High

### 3. Real-time Weather API Calls (~100-200ms)

**Cause**: Fetching current weather pattern from Open-Meteo API for each prediction request.

**Impact**: Medium
**Optimization**: Cache recent weather data (TTL: 1 hour)
**Priority**: Medium

---

## Optimization Opportunities

### Immediate (Can implement now)

1. **Batch Weather Fetching** (Expected: -200-400ms)
   ```python
   # Current: N queries (one per accident)
   weather_map = await fetch_weather_patterns_batch(accident_ids)

   # Single query with IN clause
   SELECT * FROM weather
   WHERE accident_id IN (1, 2, 3, ...)
   AND date BETWEEN ...
   ```

2. **Weather Data Caching** (Expected: -100-200ms)
   - Cache current weather patterns with 1-hour TTL
   - Reduces API calls for repeated locations

3. **Database Connection Pooling** (Expected: -300ms in production)
   - Already configured in `session.py`
   - Test environment forces disposal (event loop isolation)
   - Production will benefit from pooling automatically

### Future (Post-MVP)

4. **Query Result Caching**
   - Cache predictions for identical requests (5-minute TTL)
   - Redis-backed cache

5. **Database Indexes**
   - Already have GIST index on `coordinates`
   - Consider composite index on `(accident_id, date)` for weather fetching

6. **Algorithm Optimizations**
   - Pre-calculate spatial weights (Gaussian decay)
   - Use lookup tables for route type weights

---

## Revised Performance Targets

### MVP (Current State + Quick Wins)

| Metric | Current | Target (Post-optimization) |
|--------|---------|----------------------------|
| Small queries (<100 accidents) | 718ms | **<400ms** |
| Medium queries (500-1000 accidents) | 1,446ms | **<800ms** |
| Large queries (1000+ accidents) | 1,983ms | **<1,200ms** |
| Concurrent throughput | 0.75 req/s | **>2 req/s** |

### Production (With all optimizations)

| Metric | Target |
|--------|--------|
| p50 response time | <300ms |
| p95 response time | <800ms |
| p99 response time | <1,500ms |
| Concurrent throughput | >10 req/s |

---

## Test Environment Notes

**Important**: Current benchmarks run with:
- Engine disposal before each test (event loop isolation fix)
- No connection pooling (NullPool in tests)
- No caching layers
- Cold starts for each request

**Production will be faster** due to:
- Persistent connection pool
- Long-running process (no cold starts)
- Potential caching layers (Redis)

---

## Recommendations

### For MVP Launch

1. ✅ **Accept current performance** - Algorithm scales well
2. 🔨 **Implement batch weather fetching** - Easy 30% speedup
3. 🔨 **Add weather data caching** - Easy 15% speedup
4. 📝 **Document "processing large areas" in UI** - User expectation management

### Post-MVP

1. Add Redis caching layer
2. Implement query result caching
3. Profile and optimize hot paths
4. Consider CDN for static content

---

## Performance Test Commands

```bash
# Run all performance tests
pytest tests/test_performance.py -v -s --no-cov

# Run specific benchmark
pytest tests/test_performance.py::TestPredictEndpointPerformance::test_predict_response_time_baseline -v -s --no-cov

# Profile endpoint
pytest tests/test_performance.py::TestPredictEndpointPerformance::test_predict_performance_scaling -v -s --no-cov
```

---

**Last Updated**: 2026-01-29
**Baseline Commit**: Phase 7E - Initial performance benchmarks
**Next Review**: After implementing batch weather fetching
