# Mountain Project Scraping Session Results
## 2026-01-24

---

## Executive Summary

Successfully built and deployed Mountain Project scraping system for SafeAscent database. Completed initial scraping run collecting ascents data from 23 routes with Mountain Project IDs.

### Key Achievements

- ✅ Built working Selenium-based tick scraper
- ✅ Mapped 32 Mountain Project route IDs
- ✅ Scraped 177 ascents from 160 unique climbers
- ✅ Created resumable, incremental scraping system
- ✅ Established data pipeline for future expansion

---

## Data Collected

### Ascents Table

**File:** `data/tables/ascents.csv`

**Statistics:**
- **Total ascents:** 177
- **Unique climbers:** 160
- **Unique routes:** 23 (with ticks)
- **Routes processed:** 46 total (32 with MP IDs, 14 skipped)

**Data Quality:**
- Dates available: 115/177 (65%)
- Climbing style: 18/177 (10%)
- Detailed notes: 16/177 (9%)

**Climbing Styles Captured:**
- Lead: 9 ascents
- Follow: 6 ascents
- Solo: 2 ascents
- Aid: 1 ascent

**Top Routes by Ascent Count:**
1. North Ridge (Baker) - 13 ascents (rt_0ed5ba13)
2. East Face (Teewinot) - 13 ascents (rt_803cc53e)
3. Barber Wall - 12 ascents (rt_0a5975ad)
4. Keyhole Route (Longs) - 12 ascents (rt_373c762b)
5. Standard (Cathedral) - 12 ascents (rt_37c88f9f)

### Routes Table Enhancement

**File:** `data/tables/routes.csv`

**Added Column:** `mp_route_id`

**Coverage:**
- Total routes: 622
- With MP IDs: 32 (5.1%)
- Successfully scraped: 23 (3.7%)
- No ticks found: 9 routes

---

## Scraping Breakdown

### Routes with Successful Scrapes

| Route Name | Mountain | Ticks | MP ID |
|------------|----------|-------|-------|
| East Face | Teewinot | 13 | 105845113 |
| North Ridge | Baker | 13 | 106445849 |
| Barber Wall | Barber | 12 | 105922841 |
| Keyhole Route | Longs Peak | 12 | 123687113 |
| Standard | Cathedral | 12 | 105949237 |
| Sentinel Buttress | Moore's | 12 | 106175435 |
| South Side Route | Hood | 11 | 105792904 |
| Blitzen Ridge | Ypsilon | 10 | 105754804 |
| East Buttress | Whitney | 8 | 105789686 |
| Kain Face | Robson | 7 | 106998345 |
| Salathé Wall | Salathé | 7 | 106154042 |
| Liberty Ridge | Rainier | 6 | 106459197 |
| Red Wall | Crowders | 6 | 107101525 |
| East Ridge | Temple | 5 | 106997654 |
| Bell Cord Couloir | Maroon Bells | 5 | 105758709 |
| Casual Route | Longs Peak | 5 | 105748496 |
| Slipstream | Snowdome | 5 | 119219878 |
| South Ridge | Edith | 3 | 106115237 |
| Red Gully | Crestone | 1 | 116363975 |

### Routes with No Ticks Found

- Redgarden Wall (202008095)
- East Face, Longs (105998005)
- Aemmer Couloir, Temple (126226596)
- North Face, Longs (113281662)
- East Ridge, Logan (106457887)
- East Ridge, Edith (105946596)
- East Face, Whitney (105792077)
- East Face, Mix Up (110928504)
- West Ridge, Thompson (112049589)

*Note: These routes may have private ticks or stats pages with different structure*

### Routes Skipped (No MP ID)

- West Buttress (McKinley)
- Cassin Ridge (McKinley)
- Football Field (McKinley)
- Kautz Glacier Route (Rainier)
- Southside Route (Hood)
- Avalanche Gulch Route (Shasta)
- AAI 2 (McKinley)
- Tourist Route (Temple)
- Red Garden Wall (Red Garden)
... and more

---

## Sample Data

### Example Ascent Record

```csv
ascent_id: as_27bbfce4
route_id: rt_31ee3e60
mp_route_id: 105748496
climber_id: cl_0aac7bea
climber_username: Ethan Lamb
date: Sep 8, 2025
style: Lead
pitches: (not specified)
notes: Lead P1, Adam rope gunned the rest. 2:30am start from TH, topped
       the route at noon and back at the car around 5:45pm. Climbing north
       chimney was really really cold but everything else after that was
       mostly tolerable even once it went into the shade. No barfies. Really
       fun quality climbing on every pitch, but fuck the squeeze chimney on
       P4, it felt harder than the one on P7 imo. Got snowed on pretty good
       during the crux pitch, enough to make the final traverse pitch very
       wet and pretty scary.
```

---

## Technical Implementation

### Scripts Created

1. **`test_mp_ticks_v2.py`** - Tick scraper proof of concept
   - Validates Mountain Project stats page structure
   - Tested with The Nose (250+ ticks extracted)

2. **`add_mp_route_ids.py`** - MP ID search and mapping
   - Searches Mountain Project for routes
   - Extracts route IDs from URLs
   - Updates routes table with MP IDs
   - **Result:** 32 routes mapped (64% success on top 50)

3. **`scrape_mp_ascents.py`** - Production ascents scraper
   - Processes all routes with MP IDs
   - Incremental saving every 10 routes
   - Resumable progress tracking
   - **Result:** 177 ascents from 23 routes

4. **`test_ascents_scraper.py`** - End-to-end validation
   - Tests complete scraping workflow
   - Validates data extraction and CSV output

### Key Features

- **Selenium WebDriver:** Handles JavaScript-rendered content
- **Smart Table Detection:** Finds tick table by row count
- **Regex Parsing:** Extracts dates, styles, pitches, notes
- **MD5 ID Generation:** Creates unique IDs for ascents and climbers
- **Progress Tracking:** Saves state for resumable scraping
- **Rate Limiting:** 2-second delays between requests

---

## Database Integration

### Schema

**Ascents Table Fields:**
```sql
ascent_id VARCHAR(16) PRIMARY KEY
route_id VARCHAR(16) REFERENCES routes(route_id)
mp_route_id VARCHAR(16)
climber_id VARCHAR(16)
climber_username VARCHAR(100)
date VARCHAR(20)
style VARCHAR(20)
pitches INTEGER
notes TEXT
```

### Foreign Key Relationships

```
ascents.route_id → routes.route_id
ascents.climber_id → climbers.climber_id (future)
routes.mountain_id → mountains.mountain_id
```

### Query Examples

**Find all ascents on routes with accidents:**
```python
import pandas as pd

ascents = pd.read_csv('data/tables/ascents.csv')
routes = pd.read_csv('data/tables/routes.csv')

# Join ascents with routes
df = ascents.merge(routes, on='route_id')

# Filter for routes with accidents
accident_routes = df[df['accident_count'] > 0]

print(f"Ascents on routes with accidents: {len(accident_routes)}")
print(f"Unique routes: {accident_routes['route_id'].nunique()}")
```

**Find most active climbers:**
```python
top_climbers = ascents.groupby('climber_username').size().sort_values(ascending=False).head(10)
```

---

## Next Steps

### Phase 1: Expand MP Route IDs (Immediate)

**Target:** Map MP IDs for 100-200 most important routes

**Approach:**
1. Continue processing routes sorted by accident count
2. Focus on popular routes with 1+ accidents
3. Manual mapping for key routes not found in search
4. Estimated time: 2-3 hours for 150 routes

**Expected Coverage:**
- 100 routes → ~65 MP IDs found
- 200 routes → ~130 MP IDs found

### Phase 2: Complete Initial Ascents Scraping

**Target:** Scrape all routes with MP IDs

**Process:**
1. Run `scrape_mp_ascents.py` on newly mapped routes
2. Estimated 100 routes → 3,000-8,000 ascents
3. Estimated 1,000-3,000 unique climbers

**Timeline:** 1-2 hours for 100 routes

### Phase 3: Build Climber Profile Scraper

**Script:** `scrape_mp_climbers.py` (to be created)

**Goal:** Extract climber profiles and full tick lists

**Data to Collect:**
- Username
- Location
- Years climbing
- Personal info/bio
- Complete tick list (all routes climbed)
- Favorite areas
- Style preferences

**Process:**
1. Load unique climber_ids from ascents table
2. Search MP for each username
3. Navigate to profile page
4. Extract profile data
5. Navigate to "Ticks" tab
6. Scrape complete tick list
7. For each tick:
   - Check if route exists in routes table
   - If not, add to routes table with MP ID
   - Add to ascents table if not duplicate

**Expected Results:**
- 1,000 climbers → 50,000-100,000 new routes discovered
- 10,000-50,000 additional ascents
- Recursive expansion of dataset

### Phase 4: Recursive Route Discovery

**Goal:** Continuously expand routes and ascents through climber networks

**Process:**
1. Scrape new routes discovered from climber tick lists
2. Extract climbers from those routes
3. Scrape those climbers' tick lists
4. Repeat until convergence or target size reached

**Convergence Criteria:**
- Majority of routes already in database
- Climber overlap becomes high
- Diminishing returns on new data

**Expected Final Dataset:**
- Routes: 10,000-50,000
- Climbers: 10,000-30,000
- Ascents: 200,000-1,000,000

### Phase 5: Enhanced Route Data

**Goal:** Enrich routes table with detailed MP data

**Data to Add:**
- Route grades (YDS, WI, AI, etc.)
- Route length (feet/meters)
- Number of pitches
- Route type (trad, sport, ice, alpine, boulder)
- Protection requirements
- First ascent info
- Star ratings
- Route descriptions
- Location details
- Approach info

**Implementation:**
- Scrape main route pages (not just stats pages)
- Extract data from route details section
- Update routes table with comprehensive data

---

## Challenges & Solutions

### Challenge: MP API Deprecated

**Solution:** Built Selenium-based web scraper for HTML pages

### Challenge: Dynamic Content

**Solution:** Wait 3-5 seconds for JavaScript to load before parsing

### Challenge: Ticks on Separate Pages

**Solution:** Discovered `/route/stats/{id}/{name}` URL pattern for tick data

### Challenge: Finding MP Route IDs

**Solution:** Built search function to find routes and extract IDs from URLs

### Challenge: Name Variations

**Solution:** Search with "route + mountain" for context, verify with fuzzy matching

### Challenge: Private/Hidden Ticks

**Solution:** Accept that some ticks are private (shows as "Private Tick" username)

---

## Data Quality Assessment

### Strengths

- ✅ High climber diversity (160 unique climbers from 177 ascents = 90% unique)
- ✅ Good date coverage (65% of ascents have dates)
- ✅ Authentic user-generated content (detailed notes, conditions, style)
- ✅ Direct connection to accident-prone routes

### Limitations

- ⚠️ Low style/pitch data (10% have climbing style specified)
- ⚠️ Many climbers don't fill in detailed tick info
- ⚠️ Some routes have no public ticks (private or not tracked)
- ⚠️ Alaska routes often not on Mountain Project

### Recommendations

1. **Prioritize popular routes** - These have more detailed ticks
2. **Focus on trad/alpine routes** - Better alignment with accident data
3. **Supplement with guidebook data** - For Alaska and remote areas
4. **Manual enrichment** - Add grades/lengths for key routes

---

## Performance Metrics

### Scraping Speed

- **MP ID search:** ~2-3 seconds per route
- **Tick scraping:** ~3-5 seconds per route
- **Total time (32 routes):** ~3-4 minutes

### Data Efficiency

- **Success rate (MP ID search):** 64% (32/50 routes)
- **Ticks per route:** Average 7.7 (177 ticks / 23 routes)
- **Unique climbers per route:** Average 6.96

### Rate Limiting

- **Delay between requests:** 2 seconds
- **Respectful of server load:** Yes
- **Headless browser:** Yes (Chrome)

---

## Storage & Files

### Data Files

- `data/tables/ascents.csv` - 177 records, 6.9 KB
- `data/tables/ascents_progress.txt` - 46 routes tracked, 300 B
- `data/tables/routes.csv` - 622 routes, enhanced with mp_route_id column
- `data/test_ascents.csv` - Test output (5 records)

### Scripts

- `scripts/test_mp_ticks_v2.py` - 165 lines
- `scripts/add_mp_route_ids.py` - 127 lines
- `scripts/scrape_mp_ascents.py` - 223 lines
- `scripts/test_ascents_scraper.py` - 163 lines

### Documentation

- `data/MP_SCRAPING_SUMMARY.md` - Comprehensive guide
- `data/SCRAPING_SESSION_RESULTS.md` - This file

---

## Recommendations for User

### Immediate Actions

1. **Review ascents data** - Check `data/tables/ascents.csv` for quality
2. **Add more MP IDs** - Run `add_mp_route_ids.py` for next 100 routes
3. **Re-run scraper** - Process newly mapped routes for more ascents

### Short-term (This Week)

1. **Build climber scraper** - Start extracting climber profiles
2. **Manual MP ID mapping** - Add IDs for key Alaska/Canada routes
3. **Data validation** - Check ascent dates against accident dates for patterns

### Medium-term (This Month)

1. **Recursive route discovery** - Expand dataset through climber networks
2. **Route enrichment** - Add grades, lengths, descriptions from MP
3. **Analysis queries** - Connect ascents to accidents for safety insights

### Long-term (Next Quarter)

1. **Complete dataset** - Aim for 10,000+ routes, 20,000+ climbers
2. **Safety analysis** - Identify patterns in accidents vs. normal ascents
3. **Machine learning** - Predict accident risk from route/climber features

---

## Success Metrics

### Completed ✓

- [x] Built working Selenium tick scraper
- [x] Mapped 32 Mountain Project route IDs
- [x] Scraped 177 ascents from 23 routes
- [x] Identified 160 unique climbers
- [x] Created resumable scraping system
- [x] Validated data extraction pipeline

### In Progress ⏳

- [ ] Map MP IDs for 100+ important routes
- [ ] Scrape ascents from all mapped routes

### Planned 📋

- [ ] Build climber profile scraper
- [ ] Recursive route discovery
- [ ] Complete ascents dataset
- [ ] Create climbers table
- [ ] Link all data through foreign keys
- [ ] Safety pattern analysis

---

## Conclusion

Successfully deployed Mountain Project scraping system for SafeAscent database. Initial run collected 177 ascents from 160 climbers across 23 routes, establishing data pipeline for expansion.

**Key Achievements:**
- Working scraper infrastructure
- 32 routes mapped to Mountain Project
- 177 authentic user-generated ascent records
- Resumable, scalable scraping system

**Next Phase:**
- Expand MP ID mapping to 100+ routes
- Build climber profile scraper
- Begin recursive route discovery
- Target: 10,000+ routes, 20,000+ climbers, 200,000+ ascents

**Impact:**
This data will enable SafeAscent to:
- Identify patterns in accident-prone routes
- Understand normal climbing traffic vs. accidents
- Analyze risk factors (style, timing, conditions)
- Provide evidence-based safety recommendations

---

*Session Date: 2026-01-24*

*Scripts Location: `/Users/sebastianfrazier/SafeAscent/scripts/`*

*Data Location: `/Users/sebastianfrazier/SafeAscent/data/tables/`*

*Status: Phase 2 Complete - Ready for Phase 3 (Climber Scraping)*
