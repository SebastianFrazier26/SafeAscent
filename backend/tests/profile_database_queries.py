"""
Database Query Profiling Script

This script profiles the actual queries used in the prediction endpoint to identify
bottlenecks and optimization opportunities.

Uses PostgreSQL's EXPLAIN ANALYZE to show:
- Actual query execution time
- Index usage
- Rows scanned vs rows returned
- Cost estimates
"""
import psycopg2
import os
from dotenv import load_dotenv
from datetime import date, timedelta

load_dotenv()

# Database connection
conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "localhost"),
    port=os.getenv("DB_PORT", "5432"),
    database=os.getenv("DB_NAME", "safeascent"),
    user=os.getenv("DB_USER", "sebastianfrazier"),
    password=os.getenv("DB_PASSWORD", ""),
)
cur = conn.cursor()

print("="*80)
print("DATABASE QUERY PROFILING - Phase 8.2")
print("="*80)
print()

# =============================================================================
# QUERY 1: Spatial Query - Fetch Nearby Accidents
# =============================================================================

print("📍 QUERY 1: Spatial Query (Fetch Nearby Accidents)")
print("-" * 80)
print()

query_spatial = """
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT accident_id, date, latitude, longitude,
       accident_type, activity, injury_severity,
       mountain_id, route_id,
       ST_AsText(coordinates) as coordinates_text
FROM accidents
WHERE coordinates IS NOT NULL
  AND date IS NOT NULL
  AND latitude IS NOT NULL
  AND longitude IS NOT NULL
  AND ST_DWithin(
      coordinates,
      ST_SetSRID(ST_MakePoint(-105.615, 40.255), 4326),
      50000.0
  );
"""

print("Query:")
print("  Finding accidents within 50km of Longs Peak (40.255, -105.615)")
print()

cur.execute(query_spatial)
result = cur.fetchall()

print("Execution Plan:")
for row in result:
    print(f"  {row[0]}")
print()

# Get actual count
cur.execute("""
    SELECT COUNT(*) FROM accidents
    WHERE coordinates IS NOT NULL
      AND date IS NOT NULL
      AND latitude IS NOT NULL
      AND longitude IS NOT NULL
      AND ST_DWithin(
          coordinates,
          ST_SetSRID(ST_MakePoint(-105.615, 40.255), 4326),
          50000.0
      );
""")
count = cur.fetchone()[0]
print(f"✓ Result: Found {count} accidents within 50km")
print()

# =============================================================================
# QUERY 2: Weather Pattern Query (Single Accident)
# =============================================================================

print("🌤️  QUERY 2: Weather Pattern Query (Single Accident)")
print("-" * 80)
print()

# First, get a sample accident ID
cur.execute("""
    SELECT accident_id, date
    FROM accidents
    WHERE accident_id = 3025  -- Known accident from Longs Peak area
    LIMIT 1;
""")
sample_accident = cur.fetchone()
accident_id, accident_date = sample_accident

start_date = accident_date - timedelta(days=6)
end_date = accident_date

query_weather = f"""
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT weather_id, accident_id, date,
       temperature_avg, temperature_min, temperature_max,
       wind_speed_avg, wind_speed_max,
       precipitation_total, visibility_avg, cloud_cover_avg
FROM weather
WHERE accident_id = {accident_id}
  AND date >= '{start_date}'
  AND date <= '{end_date}'
ORDER BY date;
"""

print(f"Query:")
print(f"  Fetching 7-day weather pattern for accident {accident_id}")
print(f"  Date range: {start_date} to {end_date}")
print()

cur.execute(query_weather)
result = cur.fetchall()

print("Execution Plan:")
for row in result:
    print(f"  {row[0]}")
print()

# =============================================================================
# QUERY 3: N+1 Problem Simulation
# =============================================================================

print("⚠️  QUERY 3: N+1 Problem Simulation (476 Weather Queries)")
print("-" * 80)
print()

print("Simulating current approach: fetching weather for 476 accidents...")
print("(Running first 10 queries as sample)")
print()

import time

# Get first 10 accident IDs from Longs Peak area
cur.execute("""
    SELECT accident_id, date
    FROM accidents
    WHERE coordinates IS NOT NULL
      AND date IS NOT NULL
      AND ST_DWithin(
          coordinates,
          ST_SetSRID(ST_MakePoint(-105.615, 40.255), 4326),
          50000.0
      )
    LIMIT 10;
""")
sample_accidents = cur.fetchall()

start_time = time.time()
for i, (acc_id, acc_date) in enumerate(sample_accidents, 1):
    start_date = acc_date - timedelta(days=6)
    end_date = acc_date

    cur.execute(f"""
        SELECT weather_id, accident_id, date,
               temperature_avg, precipitation_total, wind_speed_avg
        FROM weather
        WHERE accident_id = {acc_id}
          AND date >= '{start_date}'
          AND date <= '{end_date}'
        ORDER BY date;
    """)
    weather_records = cur.fetchall()
    print(f"  Query {i:2d}: Accident {acc_id:4d} → {len(weather_records)} weather records")

elapsed = time.time() - start_time
print()
print(f"Time for 10 queries: {elapsed:.4f} seconds")
print(f"Estimated time for 476 queries: {elapsed * 47.6:.2f} seconds")
print()

# =============================================================================
# QUERY 4: Optimized Bulk Weather Fetch (Solution)
# =============================================================================

print("✅ QUERY 4: Optimized Bulk Weather Fetch (Proposed Solution)")
print("-" * 80)
print()

# Get 10 accident IDs for comparison
cur.execute("""
    SELECT accident_id FROM accidents
    WHERE coordinates IS NOT NULL
      AND date IS NOT NULL
      AND ST_DWithin(
          coordinates,
          ST_SetSRID(ST_MakePoint(-105.615, 40.255), 4326),
          50000.0
      )
    LIMIT 10;
""")
accident_ids = [row[0] for row in cur.fetchall()]

query_bulk = f"""
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT w.weather_id, w.accident_id, w.date, a.date as accident_date,
       w.temperature_avg, w.temperature_min, w.temperature_max,
       w.wind_speed_avg, w.precipitation_total
FROM weather w
INNER JOIN accidents a ON w.accident_id = a.accident_id
WHERE w.accident_id IN ({','.join(map(str, accident_ids))})
  AND w.date >= a.date - INTERVAL '6 days'
  AND w.date <= a.date
ORDER BY w.accident_id, w.date;
"""

print("Query:")
print(f"  Bulk fetching weather for {len(accident_ids)} accidents in ONE query")
print()

cur.execute(query_bulk)
result = cur.fetchall()

print("Execution Plan:")
for row in result:
    print(f"  {row[0]}")
print()

# Time the bulk query
start_time = time.time()
cur.execute(f"""
    SELECT w.weather_id, w.accident_id, w.date,
           w.temperature_avg, w.precipitation_total
    FROM weather w
    INNER JOIN accidents a ON w.accident_id = a.accident_id
    WHERE w.accident_id IN ({','.join(map(str, accident_ids))})
      AND w.date >= a.date - INTERVAL '6 days'
      AND w.date <= a.date
    ORDER BY w.accident_id, w.date;
""")
results = cur.fetchall()
elapsed_bulk = time.time() - start_time

print(f"Time for bulk query: {elapsed_bulk:.4f} seconds")
print(f"Speedup vs N+1: {elapsed / elapsed_bulk:.1f}x faster")
print()

# =============================================================================
# INDEX ANALYSIS
# =============================================================================

print("📊 INDEX ANALYSIS")
print("-" * 80)
print()

# Check what indexes exist
cur.execute("""
    SELECT
        schemaname,
        tablename,
        indexname,
        indexdef
    FROM pg_indexes
    WHERE schemaname = 'public'
      AND tablename IN ('accidents', 'weather', 'mountains', 'routes')
    ORDER BY tablename, indexname;
""")

indexes = cur.fetchall()

print("Current Indexes:")
current_table = None
for schema, table, index_name, index_def in indexes:
    if table != current_table:
        print(f"\n  {table.upper()}:")
        current_table = table
    print(f"    • {index_name}")
    print(f"      {index_def}")

print()

# Check index usage statistics
print("Index Usage Statistics (last session):")
cur.execute("""
    SELECT
        schemaname,
        tablename,
        indexname,
        idx_scan as scans,
        idx_tup_read as tuples_read,
        idx_tup_fetch as tuples_fetched
    FROM pg_stat_user_indexes
    WHERE schemaname = 'public'
      AND tablename IN ('accidents', 'weather')
    ORDER BY idx_scan DESC
    LIMIT 15;
""")

stats = cur.fetchall()
print()
for schema, table, index_name, scans, tuples_read, tuples_fetched in stats:
    print(f"  {table}.{index_name}:")
    print(f"    Scans: {scans:,} | Tuples read: {tuples_read:,} | Fetched: {tuples_fetched:,}")
print()

# =============================================================================
# TABLE STATISTICS
# =============================================================================

print("📈 TABLE STATISTICS")
print("-" * 80)
print()

# Get table sizes
cur.execute("""
    SELECT
        schemaname,
        tablename,
        pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
        n_live_tup as row_count,
        n_dead_tup as dead_rows,
        last_vacuum,
        last_autovacuum
    FROM pg_stat_user_tables
    WHERE schemaname = 'public'
      AND tablename IN ('accidents', 'weather', 'mountains', 'routes')
    ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
""")

tables = cur.fetchall()

print("Table Sizes and Health:")
for schema, table, size, rows, dead, last_vac, last_auto in tables:
    print(f"\n  {table.upper()}:")
    print(f"    Size: {size} | Rows: {rows:,} | Dead rows: {dead:,}")
    print(f"    Last vacuum: {last_vac or 'Never'}")
    print(f"    Last autovacuum: {last_auto or 'Never'}")

print()
print("="*80)
print("PROFILING COMPLETE")
print("="*80)

# Clean up
cur.close()
conn.close()
