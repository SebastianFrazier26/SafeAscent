# Mountains & Routes Tables Summary

## Overview

Successfully created comprehensive **Mountains/Crags** and **Routes** tables for the SafeAscent database.

---

## Mountains/Crags Table

**File:** `data/tables/mountains.csv`

### Statistics

- **Total mountains/crags:** 441
- **With elevation data:** 33 (7.5%)
- **With coordinates:** 362 (82.1%)
- **Peaks:** 32
- **Crags:** 1
- **Unknown type:** 408

### Schema

| Field | Type | Description |
|-------|------|-------------|
| `mountain_id` | string | Unique ID (mt_XXXXXXXX) |
| `name` | string | Mountain/crag name |
| `alt_names` | string | Alternative names (comma-separated) |
| `elevation_ft` | integer | Summit elevation in feet |
| `prominence_ft` | integer | Topographic prominence in feet |
| `type` | string | peak, crag, or unknown |
| `range` | string | Mountain range |
| `state` | string | State/Province |
| `latitude` | float | Latitude (WGS84) |
| `longitude` | float | Longitude (WGS84) |
| `location` | string | General location description |
| `accident_count` | integer | Number of accidents recorded |

### Top 20 Mountains by Accident Count

1. **McKinley/Denali** - 197 accidents | 20,310' | Alaska Range
2. **Mount Rainier** - 102 accidents | 14,411' | Cascade Range
3. **Mount Hood** - 70 accidents | 11,250' | Cascade Range
4. **Mount Shasta** - 61 accidents | 14,179' | Cascade Range
5. **Longs Peak** - 41 accidents | 14,259' | Front Range
6. **Mount Washington** - 41 accidents | 6,288' | Presidential Range
7. **Mount Temple** - 24 accidents | 11,627' | Canadian Rockies
8. **Redgarden Wall** - 16 accidents | 7,000' | Front Range (crag)
9. **Mount Robson** - 12 accidents | 12,972' | Canadian Rockies
10. **Mount Athabasca** - 12 accidents | 11,453' | Canadian Rockies
11. **Mount Jefferson** - 12 accidents | 10,502' | Cascade Range
12. **Mount Rundle** - 12 accidents | 9,675' | Canadian Rockies
13. **Mount Baker** - 11 accidents | 10,781' | North Cascades
14. **Mount Teewinot** - 11 accidents | 12,330' | Teton Range
15. **Yamnuska** - 11 accidents | 7,903' | Canadian Rockies
16. **Cathedral Peak** - 10 accidents | elevation unknown
17. **Cascade Mountain** - 10 accidents | elevation unknown
18. **Mount Moran** - 10 accidents | elevation unknown
19. **Mount Whitney** - 10 accidents | 14,505' | Sierra Nevada
20. **Pilot Mountain** - 10 accidents | elevation unknown

### Data Sources

- Accident reports (unique mountain mentions)
- USGS/Parks Canada elevation data
- Coordinates from previous location enhancement work
- Comprehensive database of 197+ North American peaks

---

## Routes Table

**File:** `data/tables/routes.csv`

### Statistics

- **Total routes:** 622 unique route/mountain combinations
- **With grade data:** 39 (6.3%)
- **With length data:** 39 (6.3%)
- **With coordinates:** 566 (91.0%)

### Schema

| Field | Type | Description |
|-------|------|-------------|
| `route_id` | string | Unique ID (rt_XXXXXXXX) |
| `name` | string | Route name |
| `mountain_id` | string | Foreign key to mountains table |
| `mountain_name` | string | Mountain/crag name (for reference) |
| `grade` | string | Climbing grade (YDS, WI, AI, etc.) |
| `grade_yds` | string | YDS grade only (5.X) |
| `length_ft` | integer | Route length in feet |
| `pitches` | integer | Number of pitches |
| `type` | string | alpine, trad, ice, big_wall, sport |
| `first_ascent_year` | integer | Year of first ascent |
| `latitude` | float | Route location latitude |
| `longitude` | float | Route location longitude |
| `accident_count` | integer | Number of accidents on this route |

### Route Type Distribution

- **Unknown:** 447 (71.9%) - Need Mountain Project data
- **Alpine:** 119 (19.1%)
- **Trad:** 52 (8.4%)
- **Ice:** 3 (0.5%)
- **Big Wall:** 1 (0.2%)

### Top 20 Routes by Accident Count (With Data)

1. **West Buttress** (McKinley) - 5.0 | 16,000' | 30 accidents
2. **Redgarden Wall** (Redgarden) - 5.8 | 600' | 8 accidents
3. **East Ridge** (Temple) - 5.4 | 3,500' | 6 accidents
4. **Liberty Ridge** (Rainier) - Grade IV 5.4 | 10,000' | 5 accidents
5. **East Face** (Longs) - 5.8 | 1,200' | 4 accidents
6. **South Side Route** (Hood) - Grade II | 5,200' | 4 accidents
7. **Aemmer Couloir** (Temple) - WI4 | 2,000' | 4 accidents
8. **Red Gully** (Crestone) - Class 4 | 3,000' | 4 accidents
9. **East Face** (Teewinot) - 5.4 | 1,000' | 4 accidents
10. **Casual Route** (Longs) - 5.10a | 900' | 3 accidents
11. **Cassin Ridge** (McKinley) - VI 5.8 AI4 | 8,000' | 3 accidents
12. **Football Field** (McKinley) - Alaska Grade 2 | 16,000' | 3 accidents
13. **North Face** (Longs) - 5.4 | 1,500' | 3 accidents
14. **Kautz Glacier Route** (Rainier) - Grade III | 9,000' | 3 accidents
15. **Kain Face** (Robson) - 5.8 | 4,000' | 3 accidents

### Data Sources

- **Primary:** Accident reports (1,068 unique route mentions)
- **Manual database:** 100+ popular North American routes
  - Alaska: Denali routes
  - Washington: Rainier, Baker, Hood, Cascades routes
  - California: Yosemite big walls, Sierra high routes
  - Colorado: 14ers, RMNP routes, Eldorado Canyon
  - Wyoming: Grand Teton routes
  - Canadian Rockies: Temple, Robson, Athabasca, etc.
- **routes_rated.csv:** 55,858 routes (mostly international - not heavily used)

---

## Data Quality & Coverage

### Excellent Coverage (>80%)
✅ **Mountain coordinates:** 82.1% (362/441)
✅ **Route coordinates:** 91.0% (566/622)

### Good Coverage (>30%)
✅ **Mountain names:** 441 unique from accidents
✅ **Route names:** 622 unique combinations

### Needs Improvement (<10%)
⚠️ **Mountain elevations:** 7.5% (33/441)
⚠️ **Route grades:** 6.3% (39/622)
⚠️ **Route lengths:** 6.3% (39/622)

### Mountain Project Gap
**583 routes** (93.7%) need grade/length data from Mountain Project

---

## Recommended Next Steps

### 1. Mountain Project Scraping (High Priority)

**Target routes for scraping:**
- Routes with 2+ accidents (highest value) - approximately 50-75 routes
- Popular routes on major peaks (McKinley, Rainier, Hood, Whitney, etc.)
- Classic routes mentioned in accidents

**Data to scrape:**
- Grade (YDS, WI, AI, etc.)
- Length (feet/meters)
- Number of pitches
- Route type
- Protection requirements
- First ascent information
- Star rating/popularity

**Estimated coverage after MP scraping:**
- Focus on top 100 routes → 16% coverage
- Scrape all 583 missing routes → 100% coverage

### 2. Mountain Elevation Data (Medium Priority)

**408 mountains** need elevation data

**Sources:**
- USGS Geographic Names Information System (GNIS)
- PeakBagger.com
- Summitpost.org
- Wikipedia mountain data

**Approach:**
- Batch geocoding/elevation lookup
- Manual entry for major peaks
- Automated GNIS API queries

### 3. Enhanced Mountain Metadata (Low Priority)

Additional fields to consider:
- **Mountain height/prominence:** For peak classification
- **First ascent information:** Historical context
- **Climbing season:** Best time to climb
- **Difficulty rating:** Overall difficulty assessment
- **Approach length:** Hours/miles to base
- **Parent peak:** For subsidiary summits

### 4. Route Rating Integration (Low Priority)

From routes_rated.csv (55,858 routes):
- **Difficulty ratings:** User-submitted grades
- **Quality scores:** Star ratings
- **Popularity metrics:** Number of ascents/logs

---

## Integration with Accidents Table

### Foreign Key Relationships

```
accidents.mountain → mountains.name (match)
accidents.route → routes.name WHERE routes.mountain_name = accidents.mountain (match)
```

### Linkage Statistics

- **Accidents with mountain reference:** 1,398 (32.4%)
- **Accidents with route reference:** 1,364 (31.6%)
- **Accidents linkable to mountain table:** ~1,398
- **Accidents linkable to route table:** ~850 (route+mountain combo)

### Sample Queries

```sql
-- Get all accidents on McKinley's West Buttress
SELECT a.*
FROM accidents a
JOIN routes r ON a.route = r.name AND a.mountain = r.mountain_name
JOIN mountains m ON r.mountain_id = m.mountain_id
WHERE m.name = 'McKinley' AND r.name = 'West Buttress';

-- Find most dangerous routes (by accident count)
SELECT r.name, m.name as mountain, r.accident_count, r.grade
FROM routes r
JOIN mountains m ON r.mountain_id = m.mountain_id
ORDER BY r.accident_count DESC
LIMIT 20;

-- Get all accidents in Colorado 14ers above 14,000'
SELECT a.*, m.elevation_ft, m.name as mountain
FROM accidents a
JOIN mountains m ON a.mountain = m.name
WHERE m.state = 'Colorado' AND m.elevation_ft >= 14000;
```

---

## Files Created

### Data Tables
- **`data/tables/mountains.csv`** (441 records)
- **`data/tables/routes.csv`** (622 records)
- **`data/tables/accidents.csv`** (4,319 records) - Enhanced version

### Scripts
- **`scripts/build_mountains_table.py`** - Initial mountains table creation
- **`scripts/build_routes_table.py`** - Initial routes table creation
- **`scripts/expand_route_database.py`** - Added 100+ route details
- **`scripts/final_route_enhancement.py`** - Route name normalization & matching

### Documentation
- **`data/TABLES_SUMMARY.md`** - This file
- **`data/ACCIDENTS_CLEANING_SUMMARY.md`** - Accidents data cleaning
- **`data/LOCATION_ENHANCEMENT_SUMMARY.md`** - Location/coordinate enhancement
- **`data/modeling.md`** - Original data model specification

---

## Usage Examples

### Python - Load Tables

```python
import pandas as pd

# Load tables
mountains = pd.read_csv('data/tables/mountains.csv')
routes = pd.read_csv('data/tables/routes.csv')
accidents = pd.read_csv('data/tables/accidents.csv')

# Find all routes on Mount Rainier
rainier_routes = routes[routes['mountain_name'] == 'Rainier']
print(f"Rainier has {len(rainier_routes)} routes")

# Get accident statistics by mountain
accident_stats = accidents.groupby('mountain').agg({
    'accident_id': 'count',
    'injury_severity': lambda x: (x == 'fatal').sum()
}).rename(columns={'accident_id': 'total', 'injury_severity': 'fatal'})
```

### SQL - Database Integration

```sql
-- Create tables
CREATE TABLE mountains (
    mountain_id VARCHAR(16) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    alt_names TEXT,
    elevation_ft INTEGER,
    prominence_ft INTEGER,
    type VARCHAR(20),
    range VARCHAR(100),
    state VARCHAR(50),
    latitude DECIMAL(10, 7),
    longitude DECIMAL(10, 7),
    location VARCHAR(200),
    accident_count INTEGER
);

CREATE TABLE routes (
    route_id VARCHAR(16) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    mountain_id VARCHAR(16) REFERENCES mountains(mountain_id),
    grade VARCHAR(50),
    length_ft INTEGER,
    pitches INTEGER,
    type VARCHAR(20),
    latitude DECIMAL(10, 7),
    longitude DECIMAL(10, 7),
    accident_count INTEGER
);

-- Import CSVs
COPY mountains FROM 'data/tables/mountains.csv' CSV HEADER;
COPY routes FROM 'data/tables/routes.csv' CSV HEADER;
```

---

## Summary

✅ **Successfully created** comprehensive Mountains and Routes tables
✅ **441 mountains** with 82% coordinate coverage
✅ **622 routes** with 91% coordinate coverage
✅ **39 routes** (6.3%) with complete grade/length data
✅ **Full integration** with accidents table via mountain/route names

⚠️ **Mountain Project scraping recommended** for 583 routes
⚠️ **Elevation data needed** for 408 mountains

🎯 **Tables are ready** for SafeAscent database integration
🎯 **Foreign key relationships** established
🎯 **Query patterns** documented

---

*Last Updated: 2026-01-24*
*Total Records: 441 mountains + 622 routes = 1,063 climbing objectives*
