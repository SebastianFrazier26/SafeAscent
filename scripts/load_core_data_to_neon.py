#!/usr/bin/env python3
"""
Load Core Data to Neon Database
================================
Loads accidents, weather, and mountains data from CSVs to Neon.

Usage:
    python scripts/load_core_data_to_neon.py
"""

import os
import sys
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
        logging.FileHandler('data/neon_data_load.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Neon connection
NEON_URL = "postgresql://neondb_owner:npg_mrghaUfM78Xb@ep-billowing-bonus-ajxfhalu-pooler.c-3.us-east-2.aws.neon.tech/neondb?sslmode=require"

# File paths
ACCIDENTS_CSV = Path("data/tables/accidents.csv")
WEATHER_CSV = Path("data/tables/weather.csv")
MOUNTAINS_CSV = Path("data/tables/mountains.csv")
ROUTES_CSV = Path("data/tables/routes.csv")
CLIMBERS_CSV = Path("data/tables/climbers.csv")
ASCENTS_CSV = Path("data/tables/ascents.csv")


def get_connection():
    """Create database connection."""
    logger.info("Connecting to Neon database...")
    conn = psycopg2.connect(NEON_URL)
    conn.autocommit = False
    return conn


def load_mountains(conn, batch_size: int = 100):
    """Load mountains data."""
    if not MOUNTAINS_CSV.exists():
        logger.warning(f"Mountains CSV not found: {MOUNTAINS_CSV}")
        return 0

    logger.info(f"Loading mountains from {MOUNTAINS_CSV}...")
    df = pd.read_csv(MOUNTAINS_CSV)
    logger.info(f"Read {len(df):,} mountains from CSV")

    cur = conn.cursor()

    # Clear existing data
    cur.execute("DELETE FROM mountains")
    conn.commit()

    values = []
    for _, row in df.iterrows():
        values.append((
            row['mountain_id'] if pd.notna(row.get('mountain_id')) else None,
            row['name'][:255] if pd.notna(row.get('name')) else 'Unknown',
            row['alt_names'] if pd.notna(row.get('alt_names')) else None,
            row['elevation_ft'] if pd.notna(row.get('elevation_ft')) else None,
            row['prominence_ft'] if pd.notna(row.get('prominence_ft')) else None,
            row['type'] if pd.notna(row.get('type')) else None,
            row['range'] if pd.notna(row.get('range')) else None,
            row['state'] if pd.notna(row.get('state')) else None,
            row['latitude'] if pd.notna(row.get('latitude')) else None,
            row['longitude'] if pd.notna(row.get('longitude')) else None,
            row['location'] if pd.notna(row.get('location')) else None,
            int(row['accident_count']) if pd.notna(row.get('accident_count')) else 0,
        ))

    execute_values(
        cur,
        """
        INSERT INTO mountains (mountain_id, name, alt_names, elevation_ft, prominence_ft,
                              type, range, state, latitude, longitude, location, accident_count)
        VALUES %s
        ON CONFLICT (mountain_id) DO UPDATE SET
            name = EXCLUDED.name,
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            accident_count = EXCLUDED.accident_count
        """,
        values
    )
    conn.commit()

    logger.info(f"Loaded {len(values):,} mountains")
    return len(values)


def load_accidents(conn, batch_size: int = 500):
    """Load accidents data."""
    if not ACCIDENTS_CSV.exists():
        logger.warning(f"Accidents CSV not found: {ACCIDENTS_CSV}")
        return 0

    logger.info(f"Loading accidents from {ACCIDENTS_CSV}...")
    df = pd.read_csv(ACCIDENTS_CSV)
    logger.info(f"Read {len(df):,} accidents from CSV")

    cur = conn.cursor()

    # Clear existing data (weather has FK, so clear it first)
    cur.execute("DELETE FROM weather")
    cur.execute("DELETE FROM accidents")
    conn.commit()

    total_inserted = 0
    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i + batch_size]

        values = []
        for _, row in batch.iterrows():
            # Parse date
            date_val = None
            if pd.notna(row.get('date')):
                try:
                    date_val = pd.to_datetime(row['date']).date()
                except:
                    pass

            # Map severity
            severity = row.get('severity', None)
            if pd.notna(severity):
                severity = str(severity)[:50]

            values.append((
                row['accident_id'] if pd.notna(row.get('accident_id')) else None,
                row['source'][:50] if pd.notna(row.get('source')) else None,
                str(row['source_id'])[:100] if pd.notna(row.get('source_id')) else None,
                date_val,
                date_val.year if date_val else None,  # year
                row['state'][:100] if pd.notna(row.get('state')) else None,
                row['location_name'] if pd.notna(row.get('location_name')) else None,  # location
                row['location_name'][:255] if pd.notna(row.get('location_name')) else None,  # mountain
                row['route_name'][:255] if pd.notna(row.get('route_name')) else None,  # route
                row['latitude'] if pd.notna(row.get('latitude')) else None,
                row['longitude'] if pd.notna(row.get('longitude')) else None,
                None,  # elevation_meters
                row['accident_type'][:100] if pd.notna(row.get('accident_type')) else None,
                row['activity'][:100] if pd.notna(row.get('activity')) else None,
                severity,  # injury_severity
                None,  # age_range
                row['description'] if pd.notna(row.get('description')) else None,
                None,  # tags
            ))

        execute_values(
            cur,
            """
            INSERT INTO accidents (accident_id, source, source_id, date, year, state, location,
                                  mountain, route, latitude, longitude, elevation_meters,
                                  accident_type, activity, injury_severity, age_range, description, tags)
            VALUES %s
            ON CONFLICT (accident_id) DO UPDATE SET
                source = EXCLUDED.source,
                date = EXCLUDED.date,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude
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
        logger.warning(f"Weather CSV not found: {WEATHER_CSV}")
        return 0

    logger.info(f"Loading weather from {WEATHER_CSV}...")
    df = pd.read_csv(WEATHER_CSV)
    logger.info(f"Read {len(df):,} weather records from CSV")

    cur = conn.cursor()

    # Get valid accident IDs
    cur.execute("SELECT accident_id FROM accidents")
    valid_accident_ids = set(row[0] for row in cur.fetchall())
    logger.info(f"Found {len(valid_accident_ids):,} valid accident IDs")

    total_inserted = 0
    skipped_fk = 0

    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i + batch_size]

        values = []
        for _, row in batch.iterrows():
            # Parse date
            date_val = None
            if pd.notna(row.get('date')):
                try:
                    date_val = pd.to_datetime(row['date']).date()
                except:
                    continue  # Skip records without valid date

            if date_val is None:
                continue

            # Handle accident_id FK
            accident_id = None
            if pd.notna(row.get('accident_id')):
                aid = int(row['accident_id'])
                if aid in valid_accident_ids:
                    accident_id = aid
                else:
                    skipped_fk += 1

            values.append((
                row['weather_id'] if pd.notna(row.get('weather_id')) else None,
                accident_id,
                date_val,
                row['latitude'] if pd.notna(row.get('latitude')) else 0,
                row['longitude'] if pd.notna(row.get('longitude')) else 0,
                row['temperature_avg'] if pd.notna(row.get('temperature_avg')) else None,
                row['temperature_min'] if pd.notna(row.get('temperature_min')) else None,
                row['temperature_max'] if pd.notna(row.get('temperature_max')) else None,
                row['wind_speed_avg'] if pd.notna(row.get('wind_speed_avg')) else None,
                row['wind_speed_max'] if pd.notna(row.get('wind_speed_max')) else None,
                row['precipitation_total'] if pd.notna(row.get('precipitation_total')) else None,
                row['visibility_avg'] if pd.notna(row.get('visibility_avg')) else None,
                row['cloud_cover_avg'] if pd.notna(row.get('cloud_cover_avg')) else None,
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
    if skipped_fk > 0:
        logger.info(f"  ({skipped_fk:,} had invalid accident_id, set to NULL)")

    return total_inserted


def load_routes(conn):
    """Load core routes data (not MP routes)."""
    if not ROUTES_CSV.exists():
        logger.warning(f"Routes CSV not found: {ROUTES_CSV}")
        return 0

    logger.info(f"Loading routes from {ROUTES_CSV}...")
    df = pd.read_csv(ROUTES_CSV)
    logger.info(f"Read {len(df):,} routes from CSV")

    cur = conn.cursor()

    # Clear existing data
    cur.execute("DELETE FROM ascents")  # FK dependency
    cur.execute("DELETE FROM routes")
    conn.commit()

    values = []
    for _, row in df.iterrows():
        values.append((
            row['route_id'] if pd.notna(row.get('route_id')) else None,
            row['name'][:255] if pd.notna(row.get('name')) else 'Unknown',
            row['mountain_id'] if pd.notna(row.get('mountain_id')) else None,
            row['mountain_name'][:255] if pd.notna(row.get('mountain_name')) else None,
            row['grade'][:50] if pd.notna(row.get('grade')) else None,
            row['grade_yds'][:50] if pd.notna(row.get('grade_yds')) else None,
            row['length_ft'] if pd.notna(row.get('length_ft')) else None,
            int(row['pitches']) if pd.notna(row.get('pitches')) else None,
            row['type'][:100] if pd.notna(row.get('type')) else None,
            int(row['first_ascent_year']) if pd.notna(row.get('first_ascent_year')) else None,
            row['latitude'] if pd.notna(row.get('latitude')) else None,
            row['longitude'] if pd.notna(row.get('longitude')) else None,
            int(row['accident_count']) if pd.notna(row.get('accident_count')) else 0,
            str(row['mp_route_id']) if pd.notna(row.get('mp_route_id')) else None,
        ))

    execute_values(
        cur,
        """
        INSERT INTO routes (route_id, name, mountain_id, mountain_name, grade, grade_yds,
                           length_ft, pitches, type, first_ascent_year, latitude, longitude,
                           accident_count, mp_route_id)
        VALUES %s
        ON CONFLICT (route_id) DO UPDATE SET
            name = EXCLUDED.name,
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude
        """,
        values
    )
    conn.commit()

    logger.info(f"Loaded {len(values):,} routes")
    return len(values)


def load_climbers(conn):
    """Load climbers data."""
    if not CLIMBERS_CSV.exists():
        logger.warning(f"Climbers CSV not found: {CLIMBERS_CSV}")
        return 0

    logger.info(f"Loading climbers from {CLIMBERS_CSV}...")
    df = pd.read_csv(CLIMBERS_CSV)
    logger.info(f"Read {len(df):,} climbers from CSV")

    cur = conn.cursor()

    # Clear existing
    cur.execute("DELETE FROM ascents")
    cur.execute("DELETE FROM climbers")
    conn.commit()

    values = []
    for _, row in df.iterrows():
        values.append((
            row['climber_id'] if pd.notna(row.get('climber_id')) else None,
            row['username'][:255] if pd.notna(row.get('username')) else 'Unknown',
            str(row['mp_user_id'])[:50] if pd.notna(row.get('mp_user_id')) else None,
        ))

    execute_values(
        cur,
        """
        INSERT INTO climbers (climber_id, username, mp_user_id)
        VALUES %s
        ON CONFLICT (climber_id) DO NOTHING
        """,
        values
    )
    conn.commit()

    logger.info(f"Loaded {len(values):,} climbers")
    return len(values)


def load_ascents(conn):
    """Load ascents data."""
    if not ASCENTS_CSV.exists():
        logger.warning(f"Ascents CSV not found: {ASCENTS_CSV}")
        return 0

    logger.info(f"Loading ascents from {ASCENTS_CSV}...")
    df = pd.read_csv(ASCENTS_CSV)
    logger.info(f"Read {len(df):,} ascents from CSV")

    cur = conn.cursor()

    values = []
    for _, row in df.iterrows():
        date_val = None
        if pd.notna(row.get('date')):
            try:
                date_val = pd.to_datetime(row['date']).date()
            except:
                pass

        values.append((
            row['ascent_id'] if pd.notna(row.get('ascent_id')) else None,
            int(row['route_id']) if pd.notna(row.get('route_id')) else None,
            int(row['climber_id']) if pd.notna(row.get('climber_id')) else None,
            date_val,
            row['style'][:100] if pd.notna(row.get('style')) else None,
            row['lead_style'][:100] if pd.notna(row.get('lead_style')) else None,
            int(row['pitches']) if pd.notna(row.get('pitches')) else None,
            row['notes'] if pd.notna(row.get('notes')) else None,
            str(row['mp_tick_id'])[:50] if pd.notna(row.get('mp_tick_id')) else None,
        ))

    execute_values(
        cur,
        """
        INSERT INTO ascents (ascent_id, route_id, climber_id, date, style, lead_style,
                            pitches, notes, mp_tick_id)
        VALUES %s
        ON CONFLICT (ascent_id) DO NOTHING
        """,
        values
    )
    conn.commit()

    logger.info(f"Loaded {len(values):,} ascents")
    return len(values)


def verify_data(conn):
    """Verify loaded data."""
    cur = conn.cursor()

    logger.info("\n=== DATA LOAD SUMMARY ===")

    tables = ['mountains', 'routes', 'accidents', 'weather', 'climbers', 'ascents']
    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        logger.info(f"  {table}: {count:,} records")

    # Check accidents by source
    cur.execute("SELECT source, COUNT(*) FROM accidents GROUP BY source ORDER BY COUNT(*) DESC")
    logger.info("\nAccidents by source:")
    for source, count in cur.fetchall():
        logger.info(f"  {source or 'NULL'}: {count:,}")

    # Check accidents with coordinates
    cur.execute("SELECT COUNT(*) FROM accidents WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    with_coords = cur.fetchone()[0]
    logger.info(f"\nAccidents with coordinates: {with_coords:,}")

    # Check weather with accident links
    cur.execute("SELECT COUNT(*) FROM weather WHERE accident_id IS NOT NULL")
    linked_weather = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM weather WHERE accident_id IS NULL")
    baseline_weather = cur.fetchone()[0]
    logger.info(f"Weather linked to accidents: {linked_weather:,}")
    logger.info(f"Weather baseline records: {baseline_weather:,}")


def main():
    start_time = datetime.now()
    logger.info(f"Starting data load at {start_time}")

    try:
        conn = get_connection()

        # Load in order (respecting FKs)
        load_mountains(conn)
        load_routes(conn)
        load_climbers(conn)
        load_ascents(conn)
        load_accidents(conn)
        load_weather(conn)

        # Verify
        verify_data(conn)

        conn.close()

        elapsed = datetime.now() - start_time
        logger.info(f"\nData load completed in {elapsed}")

    except Exception as e:
        logger.error(f"Data load failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
