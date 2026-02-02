#!/usr/bin/env python3
"""
Weather Statistics Computation Script

Computes historical weather statistics (mean, std) grouped by:
- Location (0.1° latitude/longitude buckets)
- Elevation band (altitude ranges)
- Season (winter, spring, summer, fall)

Creates weather_statistics table for extreme weather detection in safety algorithm.

Usage:
    python scripts/compute_weather_statistics.py
"""
import os
from typing import Dict, Tuple
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_batch

# Load environment variables
load_dotenv()

# Database connection
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "safeascent")
DB_USER = os.getenv("DB_USER", "sebastianfrazier")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Elevation bands (in meters, converted from feet)
ELEVATION_BANDS = [
    (0, 2438),          # < 8000ft
    (2438, 3048),       # 8000-10000ft
    (3048, 3658),       # 10000-12000ft
    (3658, 4267),       # 12000-14000ft
    (4267, 10000),      # > 14000ft
]

# Season definitions (Northern Hemisphere, by month)
SEASONS = {
    "winter": [12, 1, 2],
    "spring": [3, 4, 5],
    "summer": [6, 7, 8],
    "fall": [9, 10, 11],
}


def get_db_connection():
    """Create database connection."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def create_weather_statistics_table():
    """Create weather_statistics table if it doesn't exist."""
    print("Creating weather_statistics table...")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS weather_statistics (
            stat_id SERIAL PRIMARY KEY,

            -- Location bucket (0.1° precision)
            lat_bucket REAL NOT NULL,
            lon_bucket REAL NOT NULL,

            -- Elevation band
            elevation_min_m REAL NOT NULL,
            elevation_max_m REAL NOT NULL,

            -- Season
            season VARCHAR(10) NOT NULL,

            -- Statistics for each weather factor
            temperature_mean REAL,
            temperature_std REAL,
            precipitation_mean REAL,
            precipitation_std REAL,
            wind_speed_mean REAL,
            wind_speed_std REAL,
            visibility_mean REAL,
            visibility_std REAL,

            -- Sample size (for confidence assessment)
            sample_count INTEGER NOT NULL,

            -- Computed timestamp
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            -- Unique constraint on bucket combination
            UNIQUE (lat_bucket, lon_bucket, elevation_min_m, elevation_max_m, season)
        );

        -- Index for fast lookups
        CREATE INDEX IF NOT EXISTS idx_weather_stats_location
        ON weather_statistics(lat_bucket, lon_bucket, elevation_min_m, elevation_max_m, season);
    """)

    conn.commit()
    cur.close()
    conn.close()

    print("✓ weather_statistics table created!\n")


def get_season_from_month(month: int) -> str:
    """Get season name from month number (1-12)."""
    for season, months in SEASONS.items():
        if month in months:
            return season
    return "summer"  # Default


def compute_statistics():
    """
    Compute weather statistics for all location/elevation/season buckets.

    Groups weather data by:
    - Latitude bucket (rounded to 0.1°)
    - Longitude bucket (rounded to 0.1°)
    - Elevation band
    - Season

    Computes mean and standard deviation for:
    - Temperature, precipitation, wind speed, visibility
    """
    print("Computing weather statistics...")
    print("This may take a few minutes...\n")

    conn = get_db_connection()
    cur = conn.cursor()

    # For each elevation band, compute statistics
    for elev_min, elev_max in ELEVATION_BANDS:
        elev_label = f"{int(elev_min)}-{int(elev_max)}m"
        print(f"Processing elevation band: {elev_label}")

        for season, months in SEASONS.items():
            print(f"  Season: {season}")

            # SQL query to compute statistics
            # Groups by 0.1° lat/lon buckets within this elevation band and season
            query = """
                INSERT INTO weather_statistics (
                    lat_bucket,
                    lon_bucket,
                    elevation_min_m,
                    elevation_max_m,
                    season,
                    temperature_mean,
                    temperature_std,
                    precipitation_mean,
                    precipitation_std,
                    wind_speed_mean,
                    wind_speed_std,
                    visibility_mean,
                    visibility_std,
                    sample_count
                )
                SELECT
                    ROUND(CAST(latitude AS NUMERIC), 1) AS lat_bucket,
                    ROUND(CAST(longitude AS NUMERIC), 1) AS lon_bucket,
                    %s AS elevation_min_m,
                    %s AS elevation_max_m,
                    %s AS season,

                    -- Temperature statistics
                    AVG(temperature_avg) AS temperature_mean,
                    STDDEV_SAMP(temperature_avg) AS temperature_std,

                    -- Precipitation statistics
                    AVG(precipitation_total) AS precipitation_mean,
                    STDDEV_SAMP(precipitation_total) AS precipitation_std,

                    -- Wind speed statistics
                    AVG(wind_speed_avg) AS wind_speed_mean,
                    STDDEV_SAMP(wind_speed_avg) AS wind_speed_std,

                    -- Visibility statistics
                    AVG(visibility_avg) AS visibility_mean,
                    STDDEV_SAMP(visibility_avg) AS visibility_std,

                    -- Sample count
                    COUNT(*) AS sample_count

                FROM weather
                WHERE elevation_meters IS NOT NULL
                  AND elevation_meters >= %s
                  AND elevation_meters < %s
                  AND EXTRACT(MONTH FROM date) = ANY(%s)
                  AND temperature_avg IS NOT NULL

                GROUP BY lat_bucket, lon_bucket
                HAVING COUNT(*) >= 3  -- Require at least 3 samples per bucket

                ON CONFLICT (lat_bucket, lon_bucket, elevation_min_m, elevation_max_m, season)
                DO UPDATE SET
                    temperature_mean = EXCLUDED.temperature_mean,
                    temperature_std = EXCLUDED.temperature_std,
                    precipitation_mean = EXCLUDED.precipitation_mean,
                    precipitation_std = EXCLUDED.precipitation_std,
                    wind_speed_mean = EXCLUDED.wind_speed_mean,
                    wind_speed_std = EXCLUDED.wind_speed_std,
                    visibility_mean = EXCLUDED.visibility_mean,
                    visibility_std = EXCLUDED.visibility_std,
                    sample_count = EXCLUDED.sample_count,
                    computed_at = CURRENT_TIMESTAMP;
            """

            # Execute with parameters
            cur.execute(query, (
                elev_min, elev_max, season, elev_min, elev_max, months
            ))

            conn.commit()

    cur.close()
    conn.close()

    print("\n✓ Weather statistics computed successfully!")


def print_statistics_summary():
    """Print summary of computed statistics."""
    print("\n" + "="*60)
    print("WEATHER STATISTICS SUMMARY")
    print("="*60)

    conn = get_db_connection()
    cur = conn.cursor()

    # Total buckets
    cur.execute("SELECT COUNT(*) FROM weather_statistics;")
    total_buckets = cur.fetchone()[0]
    print(f"Total statistical buckets: {total_buckets}")

    # By season
    print("\nBy Season:")
    cur.execute("""
        SELECT season, COUNT(*) as bucket_count, SUM(sample_count) as total_samples
        FROM weather_statistics
        GROUP BY season
        ORDER BY season;
    """)
    for season, bucket_count, total_samples in cur.fetchall():
        print(f"  {season.capitalize():8} {bucket_count:5} buckets ({total_samples:6} samples)")

    # By elevation band
    print("\nBy Elevation Band:")
    cur.execute("""
        SELECT
            CONCAT(elevation_min_m, '-', elevation_max_m, 'm') as band,
            COUNT(*) as bucket_count,
            SUM(sample_count) as total_samples
        FROM weather_statistics
        GROUP BY elevation_min_m, elevation_max_m
        ORDER BY elevation_min_m;
    """)
    for band, bucket_count, total_samples in cur.fetchall():
        print(f"  {band:20} {bucket_count:5} buckets ({total_samples:6} samples)")

    # Sample statistics bucket
    print("\nExample Statistics Bucket:")
    cur.execute("""
        SELECT
            lat_bucket, lon_bucket, elevation_min_m, elevation_max_m, season,
            temperature_mean, temperature_std,
            wind_speed_mean, wind_speed_std,
            sample_count
        FROM weather_statistics
        WHERE sample_count > 50
        ORDER BY sample_count DESC
        LIMIT 1;
    """)
    result = cur.fetchone()
    if result:
        lat, lon, elev_min, elev_max, season, temp_mean, temp_std, wind_mean, wind_std, samples = result
        print(f"  Location: {lat}°N, {lon}°W")
        print(f"  Elevation: {int(elev_min)}-{int(elev_max)}m")
        print(f"  Season: {season}")
        print(f"  Temperature: {temp_mean:.1f}°C ± {temp_std:.1f}°C")
        print(f"  Wind: {wind_mean:.1f} m/s ± {wind_std:.1f} m/s")
        print(f"  Samples: {samples}")

    cur.close()
    conn.close()

    print("="*60 + "\n")


def main():
    """Main execution function."""
    print("\n" + "="*60)
    print("SAFEASCENT WEATHER STATISTICS COMPUTATION")
    print("="*60 + "\n")

    # Step 1: Create table
    create_weather_statistics_table()

    # Step 2: Compute statistics
    compute_statistics()

    # Step 3: Print summary
    print_statistics_summary()

    print("✓ Weather statistics computation complete!\n")
    print("The weather_statistics table is now ready for use in the")
    print("safety prediction algorithm (extreme weather detection).\n")


if __name__ == "__main__":
    main()
