#!/usr/bin/env python3
"""
Setup Neon Database for SafeAscent
===================================
Creates all required tables and loads Mountain Project data.

This script:
1. Creates all tables (mountains, routes, accidents, weather, climbers, ascents)
2. Creates MP-specific tables (mp_locations, mp_routes)
3. Creates PostGIS triggers for coordinate columns
4. Loads MP routes and locations data

Usage:
    python scripts/setup_neon_database.py

    # Or with explicit connection string
    python scripts/setup_neon_database.py --db-url "postgresql://..."
"""

import os
import sys
import argparse
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
        logging.FileHandler('data/neon_setup.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Default Neon connection (can be overridden)
DEFAULT_NEON_URL = "postgresql://neondb_owner:npg_mrghaUfM78Xb@ep-billowing-bonus-ajxfhalu-pooler.c-3.us-east-2.aws.neon.tech/neondb?sslmode=require"

# File paths
ROUTES_CSV = Path("data/mp_routes_v2.csv")
LOCATIONS_CSV = Path("data/mp_locations_v2.csv")


def get_connection(db_url: str):
    """Create database connection."""
    # Remove channel_binding parameter if present (not supported by psycopg2)
    if 'channel_binding' in db_url:
        db_url = db_url.replace('&channel_binding=require', '').replace('?channel_binding=require', '?')
        db_url = db_url.rstrip('?').rstrip('&')

    logger.info(f"Connecting to Neon database...")
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    return conn


def create_core_tables(conn):
    """Create all core SafeAscent tables."""
    cur = conn.cursor()

    logger.info("Creating core tables...")

    # Create tables in order (respecting foreign key dependencies)
    cur.execute("""
        -- Mountains table
        CREATE TABLE IF NOT EXISTS mountains (
            mountain_id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            alt_names TEXT,
            elevation_ft FLOAT,
            prominence_ft FLOAT,
            type VARCHAR(50),
            range VARCHAR(255),
            state VARCHAR(100),
            latitude FLOAT,
            longitude FLOAT,
            location TEXT,
            accident_count INTEGER DEFAULT 0,
            coordinates GEOGRAPHY(POINT, 4326)
        );
        CREATE INDEX IF NOT EXISTS idx_mountains_name ON mountains(name);
        CREATE INDEX IF NOT EXISTS idx_mountains_state ON mountains(state);
        CREATE INDEX IF NOT EXISTS idx_mountains_coords ON mountains USING GIST(coordinates);

        -- Climbers table
        CREATE TABLE IF NOT EXISTS climbers (
            climber_id SERIAL PRIMARY KEY,
            username VARCHAR(255) NOT NULL UNIQUE,
            mp_user_id VARCHAR(50)
        );
        CREATE INDEX IF NOT EXISTS idx_climbers_username ON climbers(username);
        CREATE INDEX IF NOT EXISTS idx_climbers_mp_id ON climbers(mp_user_id);

        -- Routes table
        CREATE TABLE IF NOT EXISTS routes (
            route_id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            mountain_id INTEGER REFERENCES mountains(mountain_id),
            mountain_name VARCHAR(255),
            grade VARCHAR(50),
            grade_yds VARCHAR(50),
            length_ft FLOAT,
            pitches INTEGER,
            type VARCHAR(100),
            first_ascent_year INTEGER,
            latitude FLOAT,
            longitude FLOAT,
            accident_count INTEGER DEFAULT 0,
            mp_route_id VARCHAR(50),
            coordinates GEOGRAPHY(POINT, 4326)
        );
        CREATE INDEX IF NOT EXISTS idx_routes_name ON routes(name);
        CREATE INDEX IF NOT EXISTS idx_routes_mountain ON routes(mountain_id);
        CREATE INDEX IF NOT EXISTS idx_routes_mp_id ON routes(mp_route_id);
        CREATE INDEX IF NOT EXISTS idx_routes_coords ON routes USING GIST(coordinates);

        -- Accidents table
        CREATE TABLE IF NOT EXISTS accidents (
            accident_id SERIAL PRIMARY KEY,
            source VARCHAR(50),
            source_id VARCHAR(100),
            date DATE,
            year FLOAT,
            state VARCHAR(100),
            location TEXT,
            mountain VARCHAR(255),
            route VARCHAR(255),
            latitude FLOAT,
            longitude FLOAT,
            elevation_meters FLOAT,
            accident_type VARCHAR(100),
            activity VARCHAR(100),
            injury_severity VARCHAR(50),
            age_range VARCHAR(50),
            description TEXT,
            tags TEXT,
            mountain_id INTEGER REFERENCES mountains(mountain_id),
            route_id INTEGER REFERENCES routes(route_id),
            coordinates GEOGRAPHY(POINT, 4326)
        );
        CREATE INDEX IF NOT EXISTS idx_accidents_date ON accidents(date);
        CREATE INDEX IF NOT EXISTS idx_accidents_state ON accidents(state);
        CREATE INDEX IF NOT EXISTS idx_accidents_type ON accidents(accident_type);
        CREATE INDEX IF NOT EXISTS idx_accidents_severity ON accidents(injury_severity);
        CREATE INDEX IF NOT EXISTS idx_accidents_mountain ON accidents(mountain_id);
        CREATE INDEX IF NOT EXISTS idx_accidents_route ON accidents(route_id);
        CREATE INDEX IF NOT EXISTS idx_accidents_coords ON accidents USING GIST(coordinates);

        -- Weather table
        CREATE TABLE IF NOT EXISTS weather (
            weather_id SERIAL PRIMARY KEY,
            accident_id INTEGER REFERENCES accidents(accident_id),
            date DATE NOT NULL,
            latitude FLOAT NOT NULL,
            longitude FLOAT NOT NULL,
            temperature_avg FLOAT,
            temperature_min FLOAT,
            temperature_max FLOAT,
            wind_speed_avg FLOAT,
            wind_speed_max FLOAT,
            precipitation_total FLOAT,
            visibility_avg FLOAT,
            cloud_cover_avg FLOAT,
            coordinates GEOGRAPHY(POINT, 4326)
        );
        CREATE INDEX IF NOT EXISTS idx_weather_date ON weather(date);
        CREATE INDEX IF NOT EXISTS idx_weather_accident ON weather(accident_id);
        CREATE INDEX IF NOT EXISTS idx_weather_coords ON weather USING GIST(coordinates);

        -- Ascents table
        CREATE TABLE IF NOT EXISTS ascents (
            ascent_id SERIAL PRIMARY KEY,
            route_id INTEGER REFERENCES routes(route_id),
            climber_id INTEGER REFERENCES climbers(climber_id),
            date DATE,
            style VARCHAR(100),
            lead_style VARCHAR(100),
            pitches INTEGER,
            notes TEXT,
            mp_tick_id VARCHAR(50)
        );
        CREATE INDEX IF NOT EXISTS idx_ascents_route ON ascents(route_id);
        CREATE INDEX IF NOT EXISTS idx_ascents_climber ON ascents(climber_id);
        CREATE INDEX IF NOT EXISTS idx_ascents_date ON ascents(date);
    """)

    conn.commit()
    logger.info("Core tables created successfully")


def create_mp_tables(conn):
    """Create Mountain Project specific tables."""
    cur = conn.cursor()

    logger.info("Creating MP tables...")

    cur.execute("""
        -- MP Locations (climbing areas hierarchy)
        CREATE TABLE IF NOT EXISTS mp_locations (
            mp_id BIGINT PRIMARY KEY,
            name VARCHAR(500) NOT NULL,
            parent_id BIGINT,
            url VARCHAR(500),
            latitude FLOAT,
            longitude FLOAT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_mp_locations_name ON mp_locations(name);
        CREATE INDEX IF NOT EXISTS idx_mp_locations_parent ON mp_locations(parent_id);
        CREATE INDEX IF NOT EXISTS idx_mp_locations_coords ON mp_locations(latitude, longitude);

        -- MP Routes (detailed route data)
        CREATE TABLE IF NOT EXISTS mp_routes (
            mp_route_id BIGINT PRIMARY KEY,
            name VARCHAR(500) NOT NULL,
            url VARCHAR(500),
            location_id BIGINT REFERENCES mp_locations(mp_id),
            grade VARCHAR(100),
            type VARCHAR(100),
            length_ft FLOAT,
            pitches FLOAT,
            latitude FLOAT,
            longitude FLOAT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_mp_routes_name ON mp_routes(name);
        CREATE INDEX IF NOT EXISTS idx_mp_routes_location ON mp_routes(location_id);
        CREATE INDEX IF NOT EXISTS idx_mp_routes_type ON mp_routes(type);
        CREATE INDEX IF NOT EXISTS idx_mp_routes_grade ON mp_routes(grade);
        CREATE INDEX IF NOT EXISTS idx_mp_routes_coords ON mp_routes(latitude, longitude);
    """)

    conn.commit()
    logger.info("MP tables created successfully")


def create_coordinate_triggers(conn):
    """Create triggers to auto-populate PostGIS coordinates from lat/lon."""
    cur = conn.cursor()

    logger.info("Creating coordinate triggers...")

    cur.execute("""
        -- Function to update coordinates from lat/lon
        CREATE OR REPLACE FUNCTION update_coordinates()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.latitude IS NOT NULL AND NEW.longitude IS NOT NULL THEN
                NEW.coordinates = ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326)::geography;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        -- Drop existing triggers if they exist
        DROP TRIGGER IF EXISTS mountains_coords_trigger ON mountains;
        DROP TRIGGER IF EXISTS routes_coords_trigger ON routes;
        DROP TRIGGER IF EXISTS accidents_coords_trigger ON accidents;
        DROP TRIGGER IF EXISTS weather_coords_trigger ON weather;

        -- Create triggers for each table
        CREATE TRIGGER mountains_coords_trigger
            BEFORE INSERT OR UPDATE ON mountains
            FOR EACH ROW EXECUTE FUNCTION update_coordinates();

        CREATE TRIGGER routes_coords_trigger
            BEFORE INSERT OR UPDATE ON routes
            FOR EACH ROW EXECUTE FUNCTION update_coordinates();

        CREATE TRIGGER accidents_coords_trigger
            BEFORE INSERT OR UPDATE ON accidents
            FOR EACH ROW EXECUTE FUNCTION update_coordinates();

        CREATE TRIGGER weather_coords_trigger
            BEFORE INSERT OR UPDATE ON weather
            FOR EACH ROW EXECUTE FUNCTION update_coordinates();
    """)

    conn.commit()
    logger.info("Coordinate triggers created successfully")


def load_mp_locations(conn, batch_size: int = 2000):
    """Load MP locations from CSV."""
    if not LOCATIONS_CSV.exists():
        logger.warning(f"Locations CSV not found: {LOCATIONS_CSV}")
        return 0

    logger.info(f"Loading locations from {LOCATIONS_CSV}...")

    df = pd.read_csv(LOCATIONS_CSV)
    logger.info(f"Read {len(df):,} locations from CSV")

    # Clean data
    df['mp_id'] = df['mp_id'].astype('Int64')
    df['parent_id'] = df['parent_id'].astype('Int64')
    df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
    df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')

    cur = conn.cursor()

    # Clear existing data
    cur.execute("DELETE FROM mp_routes")  # Clear routes first (FK constraint)
    cur.execute("DELETE FROM mp_locations")
    conn.commit()

    # Insert in batches
    total_inserted = 0
    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i + batch_size]

        values = [
            (
                row['mp_id'],
                row['name'][:500] if pd.notna(row['name']) else 'Unknown',
                row['parent_id'] if pd.notna(row['parent_id']) else None,
                row['url'][:500] if pd.notna(row['url']) else None,
                row['latitude'] if pd.notna(row['latitude']) else None,
                row['longitude'] if pd.notna(row['longitude']) else None,
            )
            for _, row in batch.iterrows()
        ]

        execute_values(
            cur,
            """
            INSERT INTO mp_locations (mp_id, name, parent_id, url, latitude, longitude)
            VALUES %s
            ON CONFLICT (mp_id) DO NOTHING
            """,
            values
        )

        total_inserted += len(batch)
        if total_inserted % 10000 == 0:
            logger.info(f"  Loaded {total_inserted:,}/{len(df):,} locations")

        conn.commit()

    logger.info(f"Loaded {total_inserted:,} locations into mp_locations")
    return total_inserted


def load_mp_routes(conn, batch_size: int = 5000):
    """Load MP routes from CSV."""
    if not ROUTES_CSV.exists():
        logger.warning(f"Routes CSV not found: {ROUTES_CSV}")
        return 0

    logger.info(f"Loading routes from {ROUTES_CSV}...")

    df = pd.read_csv(ROUTES_CSV)
    logger.info(f"Read {len(df):,} routes from CSV")

    # Clean data
    df['mp_route_id'] = df['mp_route_id'].astype('Int64')
    df['location_id'] = df['location_id'].astype('Int64')
    df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
    df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
    df['length_ft'] = pd.to_numeric(df['length_ft'], errors='coerce')
    df['pitches'] = pd.to_numeric(df['pitches'], errors='coerce')

    cur = conn.cursor()

    # Get valid location IDs for FK constraint
    cur.execute("SELECT mp_id FROM mp_locations")
    valid_location_ids = set(row[0] for row in cur.fetchall())
    logger.info(f"Found {len(valid_location_ids):,} valid location IDs")

    # Insert in batches
    total_inserted = 0
    skipped_fk = 0

    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i + batch_size]

        values = []
        for _, row in batch.iterrows():
            loc_id = row['location_id'] if pd.notna(row['location_id']) else None

            # Set to NULL if location_id doesn't exist
            if loc_id is not None and loc_id not in valid_location_ids:
                skipped_fk += 1
                loc_id = None

            values.append((
                row['mp_route_id'],
                row['name'][:500] if pd.notna(row['name']) else 'Unknown',
                row['url'][:500] if pd.notna(row['url']) else None,
                loc_id,
                row['grade'][:100] if pd.notna(row['grade']) else None,
                row['type'][:100] if pd.notna(row['type']) else None,
                row['length_ft'] if pd.notna(row['length_ft']) else None,
                row['pitches'] if pd.notna(row['pitches']) else None,
                row['latitude'] if pd.notna(row['latitude']) else None,
                row['longitude'] if pd.notna(row['longitude']) else None,
            ))

        if values:
            execute_values(
                cur,
                """
                INSERT INTO mp_routes (mp_route_id, name, url, location_id, grade, type, length_ft, pitches, latitude, longitude)
                VALUES %s
                ON CONFLICT (mp_route_id) DO NOTHING
                """,
                values
            )

        total_inserted += len(values)
        if total_inserted % 50000 == 0:
            logger.info(f"  Loaded {total_inserted:,}/{len(df):,} routes")

        conn.commit()

    logger.info(f"Loaded {total_inserted:,} routes into mp_routes")
    if skipped_fk > 0:
        logger.info(f"  ({skipped_fk:,} routes had NULL location_id due to missing FK)")

    return total_inserted


def verify_setup(conn):
    """Verify the database setup."""
    cur = conn.cursor()

    # Check tables
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = [row[0] for row in cur.fetchall()]

    logger.info("\n=== DATABASE SETUP SUMMARY ===")
    logger.info(f"Tables created: {', '.join(tables)}")

    # Check record counts
    for table in ['mountains', 'routes', 'accidents', 'weather', 'climbers', 'ascents', 'mp_locations', 'mp_routes']:
        if table in tables:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            logger.info(f"  {table}: {count:,} records")

    # Check PostGIS
    cur.execute("SELECT PostGIS_Version()")
    postgis_version = cur.fetchone()[0]
    logger.info(f"PostGIS version: {postgis_version}")

    # Check MP routes by type
    if 'mp_routes' in tables:
        cur.execute("SELECT type, COUNT(*) FROM mp_routes GROUP BY type ORDER BY COUNT(*) DESC LIMIT 10")
        logger.info("\nMP Routes by type:")
        for route_type, count in cur.fetchall():
            logger.info(f"  {route_type or 'NULL'}: {count:,}")


def main():
    parser = argparse.ArgumentParser(description="Setup Neon database for SafeAscent")
    parser.add_argument("--db-url", default=DEFAULT_NEON_URL, help="Database URL")
    parser.add_argument("--skip-mp-data", action="store_true", help="Skip loading MP data")
    args = parser.parse_args()

    start_time = datetime.now()
    logger.info(f"Starting Neon database setup at {start_time}")

    try:
        conn = get_connection(args.db_url)

        # Create all tables
        create_core_tables(conn)
        create_mp_tables(conn)
        create_coordinate_triggers(conn)

        # Load MP data
        if not args.skip_mp_data:
            load_mp_locations(conn)
            load_mp_routes(conn)

        # Verify
        verify_setup(conn)

        conn.close()

        elapsed = datetime.now() - start_time
        logger.info(f"\nSetup completed in {elapsed}")

    except Exception as e:
        logger.error(f"Setup failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
