# PostgreSQL + PostGIS Setup Complete ✅

**Date:** 2026-01-25

---

## What's Been Done

### 1. PostgreSQL Installation
- ✅ Installed **PostgreSQL 17** (latest stable version)
- ✅ Installed **PostGIS 3.6** (spatial extension)
- ✅ PostgreSQL service running on port 5432
- ✅ Added to PATH in `~/.zshrc`

### 2. Database Created
- ✅ Database name: **`safeascent`**
- ✅ PostGIS extension enabled
- ✅ Spatial functions ready to use

### 3. Schema Created
All 6 tables created with:
- ✅ **mountains** (441 records ready to load)
- ✅ **routes** (622 records ready to load)
- ✅ **accidents** (4,319 records ready to load)
- ✅ **weather** (25,591 records ready to load)
- ✅ **climbers** (178 records ready to load)
- ✅ **ascents** (366 records ready to load)

### 4. Special Features

**PostGIS Geography Columns:**
Each location table has a `coordinates` column (type: GEOGRAPHY) that's automatically populated from latitude/longitude:
- Enables fast distance queries: "Find accidents within 5km of coordinates"
- Uses Earth's curvature for accurate distances
- Indexed with GIST for performance

**Automatic Triggers:**
When you insert/update a row with lat/lon, the `coordinates` column auto-populates!

**Helpful Views:**
- `accidents_with_weather` - Joins accidents and weather
- `accidents_full` - Accidents with mountain/route details
- `database_summary` - Quick stats overview

**Indexes Created:**
- Spatial indexes on all coordinate columns
- Foreign key indexes
- Date indexes for temporal queries
- Composite indexes for common query patterns

---

## Files Created

### Schema Definition
**`scripts/create_database_schema.sql`**
- Complete table definitions
- Foreign keys
- Indexes
- Triggers
- Views
- Comments

### Data Loading Script
**`scripts/load_data_to_postgres.py`**
- Loads all CSV data into PostgreSQL
- Handles NULL values, dates, coordinates
- Progress bars for large datasets
- Verification queries
- **Ready to run when you are!**

---

## How to Use the Database

### Connect to Database
```bash
psql safeascent
```

### Quick Test Queries

**1. Database summary:**
```sql
SELECT * FROM database_summary;
```

**2. Find accidents near coordinates (10km radius):**
```sql
SELECT mountain, route, date, injury_severity
FROM accidents
WHERE ST_DWithin(
    coordinates,
    ST_SetSRID(ST_MakePoint(-121.76, 46.85), 4326)::geography,
    10000  -- 10km in meters
)
LIMIT 10;
```

**3. Count weather records:**
```sql
SELECT
    COUNT(*) as total,
    COUNT(accident_id) as accident_weather,
    COUNT(*) - COUNT(accident_id) as baseline_weather
FROM weather;
```

**4. Coldest accident conditions:**
```sql
SELECT a.mountain, a.route, a.date, w.temperature_min
FROM accidents a
JOIN weather w ON a.accident_id = w.accident_id
ORDER BY w.temperature_min
LIMIT 5;
```

---

## Next Step: Load Your Data

When you're ready, run:

```bash
source venv/bin/activate
python scripts/load_data_to_postgres.py
```

**This will:**
1. Load all 6 CSV tables into PostgreSQL
2. Populate geography columns automatically
3. Show progress bars for large datasets
4. Run verification queries
5. Report final statistics

**Expected time:** ~2-3 minutes

---

## PostGIS vs MariaDB - What You Gained

### Spatial Queries (Much Easier!)

**Before (MariaDB):** Manual distance calculation
```sql
SELECT *, (6371 * acos(cos(radians(46.85)) * cos(radians(latitude)) *
cos(radians(longitude) - radians(-121.76)) + sin(radians(46.85)) *
sin(radians(latitude)))) AS distance
FROM accidents
HAVING distance < 5;
```

**Now (PostGIS):** One simple function
```sql
SELECT * FROM accidents
WHERE ST_DWithin(coordinates,
    ST_MakePoint(-121.76, 46.85)::geography, 5000);
```

### Built-in Functions

PostGIS gives you hundreds of spatial functions:
- `ST_Distance()` - Calculate distance between points
- `ST_DWithin()` - Find points within radius
- `ST_Contains()` - Check if point is in polygon
- `ST_Buffer()` - Create radius around point
- `ST_Centroid()` - Find center of geometries
- And many more!

### Performance

- GIST spatial indexes (much faster than coordinate range queries)
- Optimized for geographic calculations
- Native support for coordinate systems

---

## Database Connection Info

**Host:** localhost
**Port:** 5432
**Database:** safeascent
**User:** sebastianfrazier
**Password:** (none - local trusted connection)

---

## Useful Commands

### PostgreSQL Service
```bash
# Start
brew services start postgresql@17

# Stop
brew services stop postgresql@17

# Restart
brew services restart postgresql@17

# Status
brew services list | grep postgresql
```

### Database Management
```bash
# Connect
psql safeascent

# List databases
psql -l

# Backup database
pg_dump safeascent > backup.sql

# Restore database
psql safeascent < backup.sql
```

### Inside psql
```sql
\dt              -- List tables
\d table_name    -- Describe table
\di              -- List indexes
\dv              -- List views
\df              -- List functions
\q               -- Quit
```

---

## Ready to Load Data?

Everything is set up and waiting for your data. When you're ready:

1. Review the setup (check tables with `psql safeascent` then `\dt`)
2. Run the loading script: `python scripts/load_data_to_postgres.py`
3. Start building your app!

---

*Setup completed: 2026-01-25*
*SafeAscent - Climbing Safety Through Data*
