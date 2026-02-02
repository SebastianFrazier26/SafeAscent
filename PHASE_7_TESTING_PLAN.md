# Phase 7: Testing & Validation Plan

**Status**: 🟡 In Progress
**Date Started**: 2026-01-29
**Goal**: Verify algorithm correctness, test weather integration, ensure prediction quality

---

## Objectives

1. **Verify Weather Integration**: Confirm real-time weather is being used correctly
2. **Test Safety Algorithm Components**: Validate each weighting calculation
3. **Integration Testing**: Test complete prediction flow end-to-end
4. **Confidence Score Validation**: Ensure confidence metrics are meaningful
5. **Edge Case Testing**: Handle missing data, extreme conditions, boundary values

---

## Test Categories

### 1. Unit Tests (Component-Level)

#### 1.1 Weather Service Tests
**File**: `backend/tests/test_weather_service.py`

Test Cases:
- [ ] `test_fetch_current_weather_success()` - Valid coordinates return weather data
- [ ] `test_fetch_current_weather_invalid_coords()` - Handle bad coordinates gracefully
- [ ] `test_fetch_current_weather_api_failure()` - Network errors return None
- [ ] `test_fetch_weather_statistics_found()` - Statistics found for valid bucket
- [ ] `test_fetch_weather_statistics_not_found()` - Missing bucket returns None
- [ ] `test_weather_pattern_construction()` - WeatherPattern object built correctly

#### 1.2 Route Type Mapper Tests
**File**: `backend/tests/test_route_type_mapper.py`

Test Cases:
- [ ] `test_route_type_priority()` - Tags > accident_type > activity priority
- [ ] `test_alpine_detection()` - Alpine/mountaineering keywords
- [ ] `test_ice_detection()` - Ice climbing keywords
- [ ] `test_default_fallback()` - Unknown types default to "alpine"

#### 1.3 Safety Algorithm Tests
**File**: `backend/tests/test_safety_algorithm.py`

Test Cases:
- [ ] `test_spatial_weight_decay()` - Gaussian decay with distance
- [ ] `test_temporal_weight_decay()` - Exponential decay over time
- [ ] `test_route_type_weighting()` - Asymmetric similarity matrix
- [ ] `test_severity_weighting()` - Fatal > injury > incident
- [ ] `test_weather_similarity()` - Pattern correlation calculation
- [ ] `test_extreme_weather_detection()` - Z-score amplification
- [ ] `test_confidence_calculation()` - Multi-factor confidence scoring

#### 1.4 Time Utilities Tests
**File**: `backend/tests/test_time_utils.py`

Test Cases:
- [ ] `test_get_season()` - Correct season for each month
- [ ] `test_days_between()` - Date difference calculation
- [ ] `test_seasonal_boost()` - Same-season accidents weighted higher

---

### 2. Integration Tests (End-to-End)

#### 2.1 Prediction Endpoint Tests
**File**: `backend/tests/test_predict_endpoint.py`

Test Cases:
- [ ] `test_prediction_with_weather()` - Full prediction with real-time weather
- [ ] `test_prediction_without_weather()` - Handles weather API failure
- [ ] `test_prediction_no_accidents()` - Zero accidents returns low confidence
- [ ] `test_prediction_many_accidents()` - Popular area (e.g., Longs Peak)
- [ ] `test_prediction_extreme_weather()` - Detects severe conditions
- [ ] `test_prediction_normal_weather()` - Normal conditions don't trigger alerts

#### 2.2 Database Query Tests
**File**: `backend/tests/test_database_queries.py`

Test Cases:
- [ ] `test_fetch_nearby_accidents()` - Spatial query correctness
- [ ] `test_accident_weather_patterns()` - 7-day weather windows
- [ ] `test_weather_statistics_lookup()` - Bucket matching

---

### 3. Algorithm Validation Tests

#### 3.1 Known Outcomes Testing
**Approach**: Test algorithm against documented accident scenarios

Test Scenarios:
- [ ] **Longs Peak, CO (High Risk)**: Many recent accidents, severe weather → High risk
- [ ] **Florida Climbing (Low Risk)**: Few accidents, warm weather → Low risk
- [ ] **Winter Denali (Extreme Risk)**: Extreme conditions, many accidents → Maximum risk

#### 3.2 Weather Similarity Validation
**Goal**: Verify weather patterns are being compared correctly

Test Cases:
- [ ] Similar weather (same temp, wind, precip) → High weight
- [ ] Dissimilar weather → Low weight
- [ ] Missing historical weather → Neutral weight (0.5)
- [ ] Extreme current weather + normal historical → Amplified risk

#### 3.3 Confidence Score Validation
**Goal**: Ensure confidence metrics are meaningful

Validation Checks:
- [ ] High accident count → High sample_size_score
- [ ] Close matches (distance, route type) → High match_quality_score
- [ ] Tight spatial clustering → High spatial_coverage_score
- [ ] Recent accidents → High temporal_recency_score
- [ ] Weather data present → High weather_quality_score

---

### 4. Performance Tests

#### 4.1 Response Time Tests
**Targets**:
- Prediction endpoint: < 500ms (acceptable)
- Prediction endpoint: < 200ms (ideal)

Test Cases:
- [ ] Single prediction request timing
- [ ] 10 concurrent predictions (load test)
- [ ] Large search radius (100km) timing

#### 4.2 Database Query Performance
**Tool**: `EXPLAIN ANALYZE` in PostgreSQL

Test Cases:
- [ ] Spatial queries use GIST indexes
- [ ] Weather pattern queries use date indexes
- [ ] No sequential scans on large tables

---

### 5. Edge Case Tests

#### 5.1 Missing Data Handling
Test Cases:
- [ ] No accidents found within radius
- [ ] Accident missing coordinates
- [ ] Accident missing date
- [ ] Weather API down
- [ ] Statistics bucket not found
- [ ] Invalid elevation value

#### 5.2 Boundary Value Tests
Test Cases:
- [ ] Latitude extremes (Alaska, Hawaii)
- [ ] Longitude wraparound (180° boundary)
- [ ] Date boundaries (leap years, month ends)
- [ ] Zero elevation (sea level)
- [ ] Maximum elevation (>8000m)

#### 5.3 Data Validation Tests
Test Cases:
- [ ] Invalid coordinates (lat > 90, lon > 180)
- [ ] Invalid route_type
- [ ] Future dates (past planned_date)
- [ ] Negative search radius

---

## Test Data Setup

### Required Test Fixtures
1. **Sample Accidents** (10-20 records covering various scenarios)
2. **Sample Weather Patterns** (5-10 patterns)
3. **Sample Weather Statistics** (representative buckets)
4. **Known Prediction Results** (for regression testing)

### Test Database
- **Option A**: Use production database with read-only tests
- **Option B**: Create test database with subset of data
- **Recommended**: Option A (real data, real conditions)

---

## Testing Tools & Setup

### Tools
- **pytest**: Testing framework
- **pytest-asyncio**: Async test support
- **httpx**: Async HTTP client for endpoint tests
- **freezegun**: Mock dates/times for temporal tests

### Installation
```bash
cd backend
pip install pytest pytest-asyncio pytest-cov httpx freezegun
```

### Test Configuration
**File**: `backend/pytest.ini`
```ini
[pytest]
testpaths = tests
asyncio_mode = auto
addopts = --verbose --cov=app --cov-report=term-missing
```

---

## Implementation Priority

### Phase 7A: Critical Tests (Start Here)
1. ✅ Manual prediction endpoint tests (already done)
2. Weather service unit tests
3. Prediction endpoint integration tests
4. Known outcomes validation

### Phase 7B: Algorithm Validation
1. Safety algorithm unit tests
2. Weather similarity tests
3. Confidence score validation

### Phase 7C: Edge Cases & Performance
1. Edge case tests
2. Performance benchmarks
3. Database query optimization

---

## Success Criteria

Phase 7 is complete when:
- [ ] All critical tests pass
- [ ] Weather integration verified working
- [ ] Algorithm produces reasonable predictions for known scenarios
- [ ] Confidence scores correlate with prediction quality
- [ ] Edge cases handled gracefully
- [ ] Response times meet targets (< 500ms)

---

## Next Steps After Phase 7

Once testing is complete, proceed to:
- **Phase 8**: Frontend Development (map visualization)
- **Phase 9**: Advanced Features (analytics dashboard, user auth)
- **Phase 10**: Production Deployment

---

*Created: 2026-01-29*
*Last Updated: 2026-01-29*
