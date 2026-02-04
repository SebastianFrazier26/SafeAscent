#!/usr/bin/env python3
"""
Load Accidents and Weather to Neon Database
============================================
Loads accident and weather data from CSVs to Neon.
Skips mountains/routes tables - uses mp_locations for geographic reference.

Usage:
    python scripts/load_accidents_weather_to_neon.py
"""

import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data/neon_accidents_weather.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Neon connection
NEON_URL = "postgresql://neondb_owner:npg_mrghaUfM78Xb@ep-billowing-bonus-ajxfhalu-pooler.c-3.us-east-2.aws.neon.tech/neondb?sslmode=require"

# File paths
ACCIDENTS_CSV = Path("data/tables/accidents.csv")
WEATHER_CSV = Path("data/tables/weather.csv")


def get_connection():
    """Create database connection."""
    logger.info("Connecting to Neon database...")
    conn = psycopg2.connect(NEON_URL)
    conn.autocommit = False
    return conn


def load_accidents(conn, batch_size: int = 500):
    """Load accidents data."""
    if not ACCIDENTS_CSV.exists():
        logger.error(f"Accidents CSV not found: {ACCIDENTS_CSV}")
        return 0

    logger.info(f"Loading accidents from {ACCIDENTS_CSV}...")
    df = pd.read_csv(ACCIDENTS_CSV)
    logger.info(f"Read {len(df):,} accidents from CSV")

    cur = conn.cursor()

    # Clear existing accident and weather data (weather has FK)
    cur.execute("DELETE FROM weather")
    cur.execute("DELETE FROM accidents")
    conn.commit()
    logger.info("Cleared existing accidents and weather data")

    total_inserted = 0
    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i + batch_size]

        values = []
        for _, row in batch.iterrows():
            # Parse date
            date_val = None
            year_val = None
            if pd.notna(row.get('date')):
                try:
                    date_val = pd.to_datetime(row['date']).date()
                    year_val = date_val.year
                except:
                    pass

            # Map severity - handle both 'severity' and 'injury_severity' columns
            severity = row.get('severity') or row.get('injury_severity')
            if pd.notna(severity):
                severity = str(severity)[:50]
            else:
                severity = None

            values.append((
                int(row['accident_id']) if pd.notna(row.get('accident_id')) else None,
                str(row['source'])[:50] if pd.notna(row.get('source')) else None,
                str(row['source_id'])[:100] if pd.notna(row.get('source_id')) else None,
                date_val,
                year_val,
                str(row['state'])[:100] if pd.notna(row.get('state')) else None,
                str(row['location_name']) if pd.notna(row.get('location_name')) else None,  # location
                str(row['location_name'])[:255] if pd.notna(row.get('location_name')) else None,  # mountain (text)
                str(row['route_name'])[:255] if pd.notna(row.get('route_name')) else None,  # route (text)
                float(row['latitude']) if pd.notna(row.get('latitude')) else None,
                float(row['longitude']) if pd.notna(row.get('longitude')) else None,
                None,  # elevation_meters (not in CSV)
                str(row['accident_type'])[:100] if pd.notna(row.get('accident_type')) else None,
                str(row['activity'])[:100] if pd.notna(row.get('activity')) else None,
                severity,
                None,  # age_range
                str(row['description']) if pd.notna(row.get('description')) else None,
                None,  # tags
                None,  # mountain_id (skipping)
                None,  # route_id (skipping)
            ))

        execute_values(
            cur,
            """
            INSERT INTO accidents (accident_id, source, source_id, date, year, state, location,
                                  mountain, route, latitude, longitude, elevation_meters,
                                  accident_type, activity, injury_severity, age_range,
                                  description, tags, mountain_id, route_id)
            VALUES %s
            ON CONFLICT (accident_id) DO UPDATE SET
                source = EXCLUDED.source,
                date = EXCLUDED.date,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                injury_severity = EXCLUDED.injury_severity
            """,
            values
        )

        total_inserted += len(batch)
        if total_inserted % 1000 == 0:
            logger.info(f"  Loaded {total_inserted:,}/{len(df):,} accidents")

        conn.commit()

    logger.info(f"Loaded {total_inserted:,} accidents")
    return total_inserted


def load_weather(conn, batch_size: int = 2000):
    """Load weather data."""
    if not WEATHER_CSV.exists():
        logger.error(f"Weather CSV not found: {WEATHER_CSV}")
        return 0

    logger.info(f"Loading weather from {WEATHER_CSV}...")
    df = pd.read_csv(WEATHER_CSV)
    logger.info(f"Read {len(df):,} weather records from CSV")

    cur = conn.cursor()

    # Get valid accident IDs for FK constraint
    cur.execute("SELECT accident_id FROM accidents")
    valid_accident_ids = set(row[0] for row in cur.fetchall())
    logger.info(f"Found {len(valid_accident_ids):,} valid accident IDs for FK linking")

    total_inserted = 0
    skipped_no_date = 0
    nulled_fk = 0

    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i + batch_size]

        values = []
        for _, row in batch.iterrows():
            # Parse date - required field
            date_val = None
            if pd.notna(row.get('date')):
                try:
                    date_val = pd.to_datetime(row['date']).date()
                except:
                    pass

            if date_val is None:
                skipped_no_date += 1
                continue

            # Handle accident_id FK - set to NULL if not in accidents table
            accident_id = None
            if pd.notna(row.get('accident_id')):
                aid = int(row['accident_id'])
                if aid in valid_accident_ids:
                    accident_id = aid
                else:
                    nulled_fk += 1
                    # Keep as NULL - this is baseline weather data

            values.append((
                int(row['weather_id']) if pd.notna(row.get('weather_id')) else None,
                accident_id,
                date_val,
                float(row['latitude']) if pd.notna(row.get('latitude')) else 0.0,
                float(row['longitude']) if pd.notna(row.get('longitude')) else 0.0,
                float(row['temperature_avg']) if pd.notna(row.get('temperature_avg')) else None,
                float(row['temperature_min']) if pd.notna(row.get('temperature_min')) else None,
                float(row['temperature_max']) if pd.notna(row.get('temperature_max')) else None,
                float(row['wind_speed_avg']) if pd.notna(row.get('wind_speed_avg')) else None,
                float(row['wind_speed_max']) if pd.notna(row.get('wind_speed_max')) else None,
                float(row['precipitation_total']) if pd.notna(row.get('precipitation_total')) else None,
                float(row['visibility_avg']) if pd.notna(row.get('visibility_avg')) else None,
                float(row['cloud_cover_avg']) if pd.notna(row.get('cloud_cover_avg')) else None,
            ))

        if values:
            execute_values(
                cur,
                """
                INSERT INTO weather (weather_id, accident_id, date, latitude, longitude,
                                    temperature_avg, temperature_min, temperature_max,
                                    wind_speed_avg, wind_speed_max, precipitation_total,
                                    visibility_avg, cloud_cover_avg)
                VALUES %s
                ON CONFLICT (weather_id) DO UPDATE SET
                    temperature_avg = EXCLUDED.temperature_avg,
                    temperature_min = EXCLUDED.temperature_min,
                    temperature_max = EXCLUDED.temperature_max
                """,
                values
            )

        total_inserted += len(values)
        if total_inserted % 10000 == 0:
            logger.info(f"  Loaded {total_inserted:,}/{len(df):,} weather records")

        conn.commit()

    logger.info(f"Loaded {total_inserted:,} weather records")
    if skipped_no_date > 0:
        logger.info(f"  Skipped {skipped_no_date:,} records with invalid date")
    if nulled_fk > 0:
        logger.info(f"  {nulled_fk:,} records had accident_id set to NULL (baseline weather)")

    return total_inserted


def verify_data(conn):
    """Verify loaded data."""
    cur = conn.cursor()

    logger.info("\n" + "="*50)
    logger.info("DATA LOAD SUMMARY")
    logger.info("="*50)

    # Accidents
    cur.execute("SELECT COUNT(*) FROM accidents")
    accident_count = cur.fetchone()[0]
    logger.info(f"\nAccidents: {accident_count:,}")

    cur.execute("SELECT COUNT(*) FROM accidents WHERE latitude IS NOT NULL")
    with_coords = cur.fetchone()[0]
    logger.info(f"  With coordinates: {with_coords:,}")

    cur.execute("SELECT source, COUNT(*) FROM accidents GROUP BY source ORDER BY COUNT(*) DESC")
    logger.info("  By source:")
    for source, count in cur.fetchall():
        logger.info(f"    {source or 'NULL'}: {count:,}")

    cur.execute("SELECT injury_severity, COUNT(*) FROM accidents GROUP BY injury_severity ORDER BY COUNT(*) DESC LIMIT 10")
    logger.info("  By severity:")
    for severity, count in cur.fetchall():
        logger.info(f"    {severity or 'NULL'}: {count:,}")

    # Weather
    cur.execute("SELECT COUNT(*) FROM weather")
    weather_count = cur.fetchone()[0]
    logger.info(f"\nWeather: {weather_count:,}")

    cur.execute("SELECT COUNT(*) FROM weather WHERE accident_id IS NOT NULL")
    linked = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM weather WHERE accident_id IS NULL")
    baseline = cur.fetchone()[0]
    logger.info(f"  Linked to accidents: {linked:,}")
    logger.info(f"  Baseline (no accident): {baseline:,}")

    cur.execute("SELECT MIN(date), MAX(date) FROM weather")
    min_date, max_date = cur.fetchone()
    logger.info(f"  Date range: {min_date} to {max_date}")

    # MP data recap
    cur.execute("SELECT COUNT(*) FROM mp_locations")
    mp_loc = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mp_routes")
    mp_routes = cur.fetchone()[0]
    logger.info(f"\nMP Data (already loaded):")
    logger.info(f"  mp_locations: {mp_loc:,}")
    logger.info(f"  mp_routes: {mp_routes:,}")

    logger.info("\n" + "="*50)


def main():
    start_time = datetime.now()
    logger.info(f"Starting accident/weather load at {start_time}")

    try:
        conn = get_connection()

        # Load data
        load_accidents(conn)
        load_weather(conn)

        # Verify
        verify_data(conn)

        conn.close()

        elapsed = datetime.now() - start_time
        logger.info(f"\nCompleted in {elapsed}")

    except Exception as e:
        logger.error(f"Load failed: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
