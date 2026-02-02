# Mountain Project Scraping Progress

## Overview

Successfully built comprehensive Mountain Project scraping system to extract ascents (ticks) and climber data for SafeAscent database.

---

## Completed Components

### 1. Tick Scraper (`test_mp_ticks_v2.py`)

**Status:** ✓ Working

Successfully scrapes tick data from Mountain Project route stats pages.

**Features:**
- Uses Selenium + Chrome WebDriver for JavaScript-rendered content
- Navigates to `/route/stats/{id}/{name}` pages
- Identifies tick table (largest table with 10+ rows)
- Parses tick format: "Username\nDate · Pitches. Style. Notes..."

**Data Extracted:**
- Climber username
- Date of ascent
- Climbing style (Lead, Follow, TR, Solo, Aid)
- Number of pitches
- Detailed notes/comments (up to 500 chars)
- Auto-generated IDs (ascent_id, climber_id)

**Test Results:**
- ✓ The Nose (El Capitan): 250+ ticks extracted successfully
- ✓ Casual Route (Longs Peak): 5 ticks with detailed notes

### 2. Mountain Project ID Mapper (`add_mp_route_ids.py`)

**Status:** ✓ Working

Searches Mountain Project for each route and adds MP route ID to routes table.

**Process:**
1. Sorts routes by accident count (prioritizes important routes)
2. Searches MP for "route_name mountain_name"
3. Extracts MP route ID from search result URLs
4. Saves progress incrementally (every 10 routes)
5. Resumes from where it left off if interrupted

**Results:**
- Processed top 50 routes by accident count
- Found MP IDs for 32 routes (64% success rate)
- Not found: 18 routes (Alaska routes, generic names, etc.)

**Routes with MP IDs:**
1. Redgarden Wall - 202008095
2. East Ridge (Temple) - 106997654
3. Liberty Ridge (Rainier) - 106459197
4. East Face (Longs) - 105998005
5. South Side Route (Hood) - 105792904
6. Aemmer Couloir (Temple) - 126226596
7. Red Gully (Crestone) - 116363975
8. East Face (Teewinot) - 105845113
9. Casual Route (Longs) - 105748496
10. North Face (Longs) - 113281662
... (32 total)

### 3. Production Ascents Scraper (`scrape_mp_ascents.py`)

**Status:** ✓ Running

Processes all routes with MP IDs and saves ascents to CSV.

**Features:**
- Incremental progress saving (every 10 routes)
- Resumable if interrupted (tracks processed routes)
- Respectful rate limiting (2s delay between requests)
- Handles errors gracefully
- Tracks unique climbers

**Output Files:**
- `data/tables/ascents.csv` - All ascents extracted
- `data/tables/ascents_progress.txt` - Progress tracking

**Current Status:**
- Processing 32 routes with MP IDs
- Running in background

### 4. Test Scripts

**`test_ascents_scraper.py`**
- Tests end-to-end flow with real routes from our table
- Validates data extraction and CSV output
- ✓ Successfully tested with Casual Route (5 ascents)

---

## Data Schema

### Ascents Table

| Field | Type | Description |
|-------|------|-------------|
| `ascent_id` | string | Unique ID (as_XXXXXXXX) |
| `route_id` | string | Our internal route ID |
| `mp_route_id` | string | Mountain Project route ID |
| `climber_id` | string | Unique climber ID (cl_XXXXXXXX) |
| `climber_username` | string | MP username |
| `date` | string | Date of ascent (e.g., "Nov 16, 2025") |
| `style` | string | Lead, Follow, TR, Solo, Aid |
| `pitches` | integer | Number of pitches climbed |
| `notes` | string | Climber's notes/comments (500 char max) |

### Routes Table Enhancement

Added new column:
- `mp_route_id` - Mountain Project route ID for scraping

**Coverage:**
- Total routes: 622
- With MP IDs: 32 (5.1%)
- Target: 100+ (top routes by accident count + popular routes)

---

## Technical Implementation

### Page Structure Discovery

Mountain Project ticks are NOT on main route pages. They are on separate stats pages:

**URL Pattern:**
```
https://www.mountainproject.com/route/stats/{mp_route_id}/{route-name-slug}
```

**Example:**
```
https://www.mountainproject.com/route/stats/105748496/casual-route
```

### Tick Table Identification

Ticks are in the **largest table on the stats page** (typically 100-250+ rows for popular routes).

**Table Format:**
Each row contains:
```
Username
Date · Pitches. Style. Notes...
```

**Example:**
```
Ethan Lamb
Sep 8, 2025 · Lead. Lead P1, Adam rope gunned the rest...
```

### Parsing Strategy

1. Find all tables on page
2. Select table with most rows (>10 rows = likely tick table)
3. For each row:
   - Split by newline to separate username and tick info
   - Extract date using regex: `([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})`
   - Extract style using regex: `\.\s*(Lead|Follow|TR|Solo|Top Rope|Aid)`
   - Extract pitches using regex: `(\d+)\s*pitches?`
   - Extract notes as remainder of string after style

### ID Generation

**Climber ID:**
```python
climber_id = f"cl_{hashlib.md5(username.lower().encode()).hexdigest()[:8]}"
```

**Ascent ID:**
```python
combined = f"{mp_route_id}_{climber_id}_{date}"
ascent_id = f"as_{hashlib.md5(combined.encode()).hexdigest()[:8]}"
```

---

## Next Steps

### Phase 1: Complete Route ID Mapping (In Progress)

**Goal:** Get MP IDs for 100+ most important routes

**Approach:**
1. ✓ Completed: Top 50 routes by accident count (32 found)
2. Next: Process next 100 routes (prioritize popular/classic routes)
3. Manually add IDs for key routes that don't appear in search

**Estimated Coverage:**
- After 150 routes: ~75-90 MP IDs (50-60% success rate)
- Focus on routes with 1+ accidents = higher value data

### Phase 2: Initial Ascents Scraping (In Progress)

**Current:** Scraping 32 routes with MP IDs

**Expected Results:**
- Estimated 1,000-3,000 ascents from 32 routes
- Estimated 500-1,500 unique climbers
- Data will show which routes have most community activity

### Phase 3: Climber Profile Scraping (Next)

**Goal:** Extract climber data and full tick lists

**Script to Build:** `scrape_mp_climbers.py`

**Process:**
1. Read unique climber_ids from ascents table
2. For each climber:
   - Search MP for username
   - Navigate to profile page
   - Extract: location, years climbing, style preferences
   - Click "Ticks" tab
   - Scrape entire tick list (all routes climbed)
3. For each tick in climber's list:
   - Check if route exists in our routes table
   - If not, add new route to routes table
   - Add to ascents table (if not duplicate)

**Data Schema - Climbers Table:**
```python
{
    'climber_id': 'cl_XXXXXXXX',
    'username': 'string',
    'location': 'string',
    'years_climbing': 'integer',
    'personal_info': 'text',
    'tick_count': 'integer'
}
```

### Phase 4: Recursive Route Discovery

**Goal:** Expand routes table through climber tick lists

**Process:**
1. From initial 32 routes → ~1,000 ascents → ~500 climbers
2. Scrape those 500 climbers' full tick lists
3. Discover ~5,000-10,000 new routes from their ticks
4. Add new routes to routes table
5. Scrape ticks from newly discovered routes
6. Continue until convergence or desired coverage reached

**Expected Final Dataset:**
- Routes: 5,000-20,000 (North American climbing routes)
- Climbers: 5,000-20,000 (active MP users)
- Ascents: 100,000-500,000 (tick records)

---

## Challenges & Solutions

### Challenge 1: MP API Deprecated

**Problem:** Mountain Project's public API was deprecated in 2020

**Solution:** Built Selenium-based scraper for web pages

### Challenge 2: JavaScript-Rendered Content

**Problem:** Tick data loads dynamically via JavaScript/AJAX

**Solution:**
- Use Selenium WebDriver (not simple HTTP requests)
- Add 3-5 second wait after page load for JS execution

### Challenge 3: Ticks Not on Main Route Page

**Problem:** Main route pages don't show tick lists

**Solution:**
- Discovered separate `/route/stats/{id}/{name}` pages
- These pages have full tick history tables

### Challenge 4: Finding MP Route IDs

**Problem:** Our routes table doesn't have MP IDs

**Solution:**
- Built search function to find routes on MP
- Extract ID from search result URLs
- Save incrementally to routes table
- Prioritize high-value routes (by accident count)

### Challenge 5: Route Name Variations

**Problem:** Route names don't always match between our data and MP

**Solution:**
- Search with "route_name + mountain_name" for context
- Verify match by checking if route name appears in link text
- Manual review of not-found routes may be needed

---

## Rate Limiting & Ethics

### Respectful Scraping Practices

1. **Rate Limiting:**
   - 2-3 second delay between requests
   - Incremental saves (every 10 routes)
   - Resumable if interrupted

2. **User Agent:**
   - Identifies as standard browser
   - Not disguising as different user agent repeatedly

3. **Headless Browser:**
   - Uses Chrome headless mode
   - Renders pages as intended
   - Respects JavaScript/dynamic content

4. **Data Usage:**
   - For safety/educational purposes (SafeAscent app)
   - Not commercializing MP data
   - Citing MP as data source

5. **Server Load:**
   - Sequential requests (not parallel)
   - Reasonable delays between pages
   - Resumable to avoid re-scraping on failures

---

## Files Created

### Scripts
- `scripts/test_mp_ticks_v2.py` - Tick scraper test/demo
- `scripts/add_mp_route_ids.py` - MP ID mapper
- `scripts/scrape_mp_ascents.py` - Production ascents scraper
- `scripts/test_ascents_scraper.py` - End-to-end test

### Data Files (Generated)
- `data/tables/ascents.csv` - Ascents table (in progress)
- `data/tables/ascents_progress.txt` - Scraping progress tracker
- `data/test_ascents.csv` - Test output

### Documentation
- `data/MP_SCRAPING_SUMMARY.md` - This file

---

## Usage Examples

### Scrape Ascents from Routes with MP IDs

```bash
python scripts/scrape_mp_ascents.py
```

**Output:**
- Saves to `data/tables/ascents.csv`
- Incremental progress saved every 10 routes
- Resumable if interrupted

### Add MP IDs to More Routes

```bash
# Edit add_mp_route_ids.py to process different routes
# Change limit and start_from parameters
python scripts/add_mp_route_ids.py
```

### Test with Specific Route

```bash
python scripts/test_ascents_scraper.py
```

### Load Ascents Data

```python
import pandas as pd

ascents = pd.read_csv('data/tables/ascents.csv')
print(f"Total ascents: {len(ascents)}")
print(f"Unique climbers: {ascents['climber_id'].nunique()}")
print(f"Unique routes: {ascents['route_id'].nunique()}")

# Find most active climbers
top_climbers = ascents.groupby('climber_username').size().sort_values(ascending=False).head(10)
print("\nMost active climbers:")
print(top_climbers)
```

---

## Statistics Summary

### Current Status

**Routes Table:**
- Total routes: 622
- With MP IDs: 32 (5.1%)
- Sorted by accident count (prioritized scraping)

**Ascents Scraping:**
- Status: In progress
- Routes being processed: 32
- Expected ascents: 1,000-3,000
- Expected climbers: 500-1,500

### Scraping Efficiency

**MP ID Search (first 50 routes):**
- Success rate: 64% (32/50)
- Time per route: ~2-3 seconds
- Total time: ~2-3 minutes for 50 routes

**Tick Scraping:**
- Time per route: 3-5 seconds
- Average ticks per route: 50-150 (varies widely)
- Popular routes (The Nose): 250+ ticks

**Estimated Full Scraping Times:**
- 100 routes: ~10 minutes
- 500 routes: ~50 minutes
- 2,000 routes: ~3 hours

---

## Integration with Existing Data

### Foreign Key Relationships

```
ascents.route_id → routes.route_id
ascents.climber_id → climbers.climber_id (to be created)
routes.mountain_id → mountains.mountain_id
```

### Linking to Accidents

```sql
-- Find all ascents on routes with accidents
SELECT a.*, r.name as route_name, m.name as mountain_name
FROM ascents a
JOIN routes r ON a.route_id = r.route_id
JOIN mountains m ON r.mountain_id = m.mountain_id
WHERE r.accident_count > 0
ORDER BY r.accident_count DESC;

-- Analyze climbing styles on routes with accidents
SELECT r.name, a.style, COUNT(*) as count
FROM ascents a
JOIN routes r ON a.route_id = r.route_id
WHERE r.accident_count > 0 AND a.style IS NOT NULL
GROUP BY r.name, a.style
ORDER BY r.accident_count DESC, count DESC;
```

---

## Success Metrics

### Completed ✓

- [x] Built working tick scraper (Selenium-based)
- [x] Discovered MP stats page structure
- [x] Implemented tick data parsing
- [x] Created MP ID search function
- [x] Mapped 32 route MP IDs
- [x] Built production ascents scraper
- [x] Implemented incremental saving
- [x] Made scraper resumable
- [x] Validated with test routes

### In Progress ⏳

- [ ] Complete initial ascents scraping (32 routes)
- [ ] Analyze ascents data quality
- [ ] Build climber profile scraper
- [ ] Map more route MP IDs (target: 100+)

### Planned 📋

- [ ] Scrape climber profiles and tick lists
- [ ] Recursive route discovery
- [ ] Expand routes table with discovered routes
- [ ] Build comprehensive ascents dataset
- [ ] Create climbers table
- [ ] Link all data through foreign keys

---

*Last Updated: 2026-01-24*

*Status: Phase 2 in progress - scraping ascents from 32 routes with MP IDs*
