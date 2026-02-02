#!/usr/bin/env python3
"""Test weather statistics database query"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

# Connect
conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "localhost"),
    port=os.getenv("DB_PORT", "5432"),
    database=os.getenv("DB_NAME", "safeascent"),
    user=os.getenv("DB_USER", "sebastianfrazier"),
    password=os.getenv("DB_PASSWORD", ""),
)
cur = conn.cursor()

# Test query
lat_bucket = round(40.2549, 1)  # 40.3
lon_bucket = round(-105.6426, 1)  # -105.6
elevation = 3000.0
season = "winter"

# Determine elevation band
elevation_bands = [
    (0, 2438),
    (2438, 3048),
    (3048, 3658),
    (3658, 4267),
    (4267, 10000),
]

for band_min, band_max in elevation_bands:
    if band_min <= elevation < band_max:
        elev_min, elev_max = band_min, band_max
        break

print(f"Looking for: lat={lat_bucket}, lon={lon_bucket}, elev={elev_min}-{elev_max}m, season={season}")

cur.execute("""
    SELECT
        temperature_mean, temperature_std,
        precipitation_mean, precipitation_std,
        wind_speed_mean, wind_speed_std,
        visibility_mean, visibility_std,
        sample_count
    FROM weather_statistics
    WHERE lat_bucket = %s
      AND lon_bucket = %s
      AND elevation_min_m = %s
      AND elevation_max_m = %s
      AND season = %s
    LIMIT 1;
""", (lat_bucket, lon_bucket, elev_min, elev_max, season))

result = cur.fetchone()

if result:
    print(f"✓ Found statistics!")
    print(f"  Temperature: {result[0]:.1f}°C (±{result[1]:.1f}°C)")
    print(f"  Sample count: {result[8]}")
else:
    print("✗ No statistics found for this bucket")

    # Check what buckets exist nearby
    print("\nNearby buckets:")
    cur.execute("""
        SELECT lat_bucket, lon_bucket, elevation_min_m, elevation_max_m, season, sample_count
        FROM weather_statistics
        WHERE lat_bucket BETWEEN %s AND %s
          AND lon_bucket BETWEEN %s AND %s
        ORDER BY sample_count DESC
        LIMIT 10;
    """, (lat_bucket - 0.5, lat_bucket + 0.5, lon_bucket - 0.5, lon_bucket + 0.5))

    for row in cur.fetchall():
        print(f"  ({row[0]}, {row[1]}) elev={row[2]}-{row[3]}m {row[4]} samples={row[5]}")

cur.close()
conn.close()
