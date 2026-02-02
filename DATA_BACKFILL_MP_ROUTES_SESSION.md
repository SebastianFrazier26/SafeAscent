# Mountain Project Routes Data Backfill - Session Log

**Date**: January 30, 2026 (Evening)
**Status**: 🚧 In Progress
**Goal**: Expand routes table from ~622 routes to thousands of US climbing routes

---

## Objective

Scrape comprehensive route data from Mountain Project to significantly expand SafeAscent's route database. This will enable:
- Better route search functionality in frontend
- More accurate accident-to-route linking
- Comprehensive route markers on the map
- Improved predictions with more route context

---

## Current State

**Routes Table Before**:
- Total routes: 622
- Routes with MP IDs: ~60 (9.6%)
- Coverage: Limited to routes found through accident reports and manual curation

**Data Gaps**:
- Missing thousands of popular US climbing routes
- Limited coverage of sport/trad routes (focused on alpine/mountaineering)
- Need comprehensive data for frontend route search

---

## Mountain Project Structure (Research)

### Site Organization
- **Hierarchical geographic system**: US States → Regions → Sub-Areas → Routes
- **Route counts**: 346,677+ routes total, significant US coverage
- **URL patterns**:
  - State areas: `/area/[ID]/[state-name]`
  - Routes: `/route/[ID]/[route-name]`

### Data Available Per Route
- Route name, grade (YDS + other systems)
- Type (trad, sport, boulder, aid, ice, mixed, alpine)
- Coordinates (latitude/longitude)
- Elevation
- Length (feet/meters)
- Pitches
- Commitment grade (I-VI)
- Protection rating
- First ascent information
- Detailed description
- **JSON-LD structured data** (makes scraping easier!)

### US State Area IDs (Major Climbing States)
Selected 12 major states to start:
- California: 105708959 (Joshua Tree, Yosemite, etc.)
- Colorado: 105708956 (Rocky Mountain climbing)
- Washington: 105708945 (Cascades)
- Utah: 105708961 (Indian Creek, Moab)
- Wyoming: 105708958 (Grand Tetons)
- New York: 105800424 (Gunks, Adirondacks)
- North Carolina: 105873282 (Looking Glass, etc.)
- New Hampshire: 105872225 (White Mountains)
- Arizona: 105708962 (Sedona, etc.)
- Nevada: 105708964 (Red Rocks)
- Oregon: 105708943 (Smith Rock)
- Texas: 105708953 (Hueco Tanks)

---

## Implementation Plan

### Phase 1: Script Development ✅ IN PROGRESS
**Task #8**: Build comprehensive scraping script
**Agent**: a17f8ef (running in background)

**Script Requirements**:
- Selenium with headless Chrome
- Recursive area traversal (state → sub-areas → routes)
- JSON-LD structured data parsing
- Rate limiting (2-3 seconds between requests)
- Incremental saves (every 100 routes)
- Resume capability (track processed areas)
- Error logging
- Progress tracking with tqdm

**Output**: `/Users/sebastianfrazier/SafeAscent/scripts/scrape_mp_routes_comprehensive.py`

### Phase 2: Testing 🔲 PENDING
**Task #9**: Test scraping script

**Test Plan**:
- Run on small subset (1 state or limited areas)
- Verify data extraction accuracy
- Check CSV output format
- Validate error handling
- Confirm rate limiting works

### Phase 3: Full Scraping 🔲 PENDING
**Task #10**: Execute full US states scraping

**Expectations**:
- **Runtime**: 6-12+ hours (thousands of routes)
- **Output**: Several thousand US climbing routes
- **Monitoring**: Check progress periodically, handle errors
- **Data Quality**: Validate coordinates, grades, route types

### Phase 4: Data Integration 🔲 PENDING
**Task #11**: Integrate scraped data into database

**Integration Steps**:
1. **Deduplication**: Check against existing 622 routes
2. **Mountain Matching**: Link to existing mountains table
3. **New Mountains**: Handle areas not yet in mountains table
4. **Data Validation**:
   - Coordinates within US bounds
   - Valid grades and route types
   - Reasonable elevations/lengths
5. **Database Load**: Insert into PostgreSQL routes table
6. **Quality Report**: Document additions and any issues

---

## Data Schema

### Scraped CSV Format
```
mp_route_id          - Mountain Project route ID (from URL)
name                 - Route name
grade_yds            - YDS grade (5.0-5.15)
grade                - Full grade string (includes aid, commitment, etc.)
type                 - Comma-separated: trad, sport, boulder, aid, ice, mixed, alpine
latitude             - Decimal degrees
longitude            - Decimal degrees
elevation_ft         - Elevation in feet
pitches              - Number of pitches
length_ft            - Total length in feet
description          - Route description text
protection_rating    - Protection quality (PG, PG13, R, X)
commitment_grade     - Roman numeral (I-VI)
first_ascent         - First ascent information
area_name            - Parent area name
state                - US state (extracted from location hierarchy)
url                  - Full Mountain Project URL
```

### Integration with Routes Table
Map to existing schema:
- `mp_route_id` → `mp_route_id` (existing field)
- `name` → `name`
- `grade_yds` → `grade_yds`
- `type` → `type`
- `latitude`, `longitude` → `latitude`, `longitude`
- `pitches` → `pitches`
- `length_ft` → `length_ft`
- Link to `mountains` table via coordinates/area name matching

---

## Expected Outcomes

### Quantitative Goals
- **Current**: 622 routes
- **Target**: 5,000-10,000 US routes (10-15× increase)
- **MP ID Coverage**: 100% for scraped routes (vs current 9.6%)
- **Coordinate Coverage**: 100% (MP has coordinates for all routes)

### Qualitative Improvements
1. **Frontend Route Search**: Comprehensive autocomplete with thousands of routes
2. **Map Visualization**: Dense route markers showing climbing areas
3. **Accident Linking**: Better fuzzy matching with larger route database
4. **Predictions**: More context for route-based risk assessment

---

## Technical Considerations

### Rate Limiting & Ethics
- **Delay**: 2-3 seconds between requests
- **User Agent**: Identify as SafeAscent research project
- **Respect robots.txt**: Follow Mountain Project's crawling guidelines
- **Time of Day**: Run during off-peak hours if possible
- **Incremental Saves**: Avoid data loss from interruptions

### Error Handling
- **Network Errors**: Retry with exponential backoff
- **Missing Data**: Graceful handling, log for review
- **JSON-LD Parsing**: Fallback to HTML parsing if structured data missing
- **Area Traversal**: Skip inaccessible/private areas
- **Resume Logic**: Track processed area IDs to avoid re-scraping

### Performance Optimization
- **Headless Browser**: Faster than visible Chrome
- **Connection Pooling**: Reuse browser session
- **Parallel Processing**: Could parallelize states (but respects rate limits)
- **Progress Checkpoints**: Save every 100 routes to enable resume

---

## Risk Mitigation

### Potential Issues
1. **Site Structure Changes**: MP could update HTML structure
   - **Mitigation**: Use JSON-LD when available (more stable)
   - **Fallback**: Manual review and script updates

2. **Rate Limiting/Blocking**: MP could detect and block scraper
   - **Mitigation**: Conservative rate limits, proper user agent
   - **Recovery**: Resume from last checkpoint

3. **Data Quality**: Inconsistent formatting, missing fields
   - **Mitigation**: Robust parsing, validation checks
   - **Cleanup**: Post-processing to fix issues

4. **Long Runtime**: Script could fail mid-execution
   - **Mitigation**: Incremental saves, resume capability
   - **Monitoring**: Regular progress checks

---

## Session Progress

### 2026-01-30 Evening

**19:26 UTC** - Started data backfill session
- Created task tracking (#8-11)
- Launched agent to build scraping script (a17f8ef)
- Researched Mountain Project structure
- Documented session plan

**Status**: Agent building comprehensive scraping script
**Next**: Wait for script completion, then test and execute

---

## Documentation & Reporting

### Files to Create
1. **Scraping Script**: `scripts/scrape_mp_routes_comprehensive.py`
2. **Output Data**: `data/mp_routes_scraped.csv`
3. **Error Log**: `data/mp_routes_scraping_errors.log`
4. **Progress Log**: `data/mp_routes_scraping_progress.json`
5. **Integration Report**: `data/MP_ROUTES_INTEGRATION_REPORT.md`

### Metrics to Track
- Routes scraped per state
- Total runtime
- Success/error rates
- Data quality statistics
- Database integration results

---

## Future Enhancements (Post-Backfill)

### Phase 5: International Routes
- Expand to Canada, Europe, South America
- Adjust state filtering logic

### Phase 6: Route Updates
- Periodic re-scraping to catch new routes
- Update existing routes with new data

### Phase 7: Additional Data
- User ratings and comments
- Recent tick counts
- Seasonal information
- Weather historical data per route

---

**Last Updated**: 2026-01-30 19:30 UTC
**Status**: Script development in progress
**Next Update**: When script testing begins
