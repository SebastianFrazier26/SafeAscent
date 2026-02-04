#!/usr/bin/env python3
"""
Migrate Mountain Project Data to PostgreSQL
============================================
Loads routes and locations from CSVs into the PostgreSQL database.

Supports both local PostgreSQL and Neon (serverless PostgreSQL).

Usage:
    # Local PostgreSQL
    python scripts/migrate_mp_data_to_postgres.py

    # Neon (specify connection string)
    DATABASE_URL="postgresql://user:pass@host/db?sslmode=require" python scripts/migrate_mp_data_to_postgres.py

    # Or with explicit argument
    python scripts/migrate_mp_data_to_postgres.py --db-url "postgresql://..."

Options:
    --db-url       Database connection URL (overrides DATABASE_URL env var)
    --batch-size   Number of records per batch insert (default: 1000)
    --skip-locations  Skip locations import (routes only)
    --skip-routes     Skip routes import (locations only)
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
        logging.FileHandler('data/mp_migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# File paths
ROUTES_CSV = Path("data/mp_routes_v2.csv")
LOCATIONS_CSV = Path("data/mp_locations_v2.csv")


def get_connection(db_url: str):
    """Create database connection with SSL support for Neon."""
    # Convert asyncpg URL to psycopg2 format if needed
    if '+asyncpg' in db_url:
        db_url = db_url.replace('+asyncpg', '')

    # Handle SSL for Neon
    if 'neon.tech' in db_url and 'sslmode' not in db_url:
        separator = '&' if '?' in db_url else '?'
        db_url = f"{db_url}{separator}sslmode=require"

    logger.info(f"Connecting to database...")
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    return conn


def create_mp_locations_table(conn):
    """Create mp_locations table if it doesn't exist."""
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mp_locations (
            mp_id BIGINT PRIMARY KEY,
            name VARCHAR(500) NOT NULL,
            parent_id BIGINT,
            url VARCHAR(500),
            latitude FLOAT,
            longitude FLOAT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Create indexes
        CREATE INDEX IF NOT EXISTS idx_mp_locations_name ON mp_locations(name);
        CREATE INDEX IF NOT EXISTS idx_mp_locations_parent ON mp_locations(parent_id);
        CREATE INDEX IF NOT EXISTS idx_mp_locations_coords ON mp_locations(latitude, longitude);
    """)

    conn.commit()
    logger.info("Created/verified mp_locations table")


def create_mp_routes_table(conn):
    """Create mp_routes table if it doesn't exist."""
    cur = conn.cursor()

    cur.execute("""
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

        -- Create indexes
        CREATE INDEX IF NOT EXISTS idx_mp_routes_name ON mp_routes(name);
        CREATE INDEX IF NOT EXISTS idx_mp_routes_location ON mp_routes(location_id);
        CREATE INDEX IF NOT EXISTS idx_mp_routes_type ON mp_routes(type);
        CREATE INDEX IF NOT EXISTS idx_mp_routes_coords ON mp_routes(latitude, longitude);
        CREATE INDEX IF NOT EXISTS idx_mp_routes_grade ON mp_routes(grade);
    """)

    conn.commit()
    logger.info("Created/verified mp_routes table")


def load_locations(conn, batch_size: int = 1000):
    """Load locations from CSV into mp_locations table."""
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
    logger.info("Cleared existing mp_locations and mp_routes data")

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
            ON CONFLICT (mp_id) DO UPDATE SET
                name = EXCLUDED.name,
                parent_id = EXCLUDED.parent_id,
                url = EXCLUDED.url,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude
            """,
            values
        )

        total_inserted += len(batch)
        if total_inserted % 10000 == 0:
            logger.info(f"  Inserted {total_inserted:,}/{len(df):,} locations")

        conn.commit()

    logger.info(f"Loaded {total_inserted:,} locations into mp_locations")
    return total_inserted


def load_routes(conn, batch_size: int = 1000):
    """Load routes from CSV into mp_routes table."""
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

            # Skip if location_id doesn't exist (FK constraint)
            if loc_id is not None and loc_id not in valid_location_ids:
                skipped_fk += 1
                loc_id = None  # Set to NULL instead of skipping

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
                ON CONFLICT (mp_route_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    url = EXCLUDED.url,
                    location_id = EXCLUDED.location_id,
                    grade = EXCLUDED.grade,
                    type = EXCLUDED.type,
                    length_ft = EXCLUDED.length_ft,
                    pitches = EXCLUDED.pitches,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude
                """,
                values
            )

        total_inserted += len(values)
        if total_inserted % 50000 == 0:
            logger.info(f"  Inserted {total_inserted:,}/{len(df):,} routes")

        conn.commit()

    logger.info(f"Loaded {total_inserted:,} routes into mp_routes")
    if skipped_fk > 0:
        logger.info(f"  ({skipped_fk:,} routes had NULL location_id due to missing FK)")

    return total_inserted


def verify_migration(conn):
    """Verify the migration by checking record counts."""
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM mp_locations")
    loc_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM mp_routes")
    route_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM mp_routes WHERE latitude IS NOT NULL")
    routes_with_coords = cur.fetchone()[0]

    cur.execute("SELECT type, COUNT(*) FROM mp_routes GROUP BY type ORDER BY COUNT(*) DESC LIMIT 10")
    type_counts = cur.fetchall()

    logger.info("\n=== MIGRATION SUMMARY ===")
    logger.info(f"mp_locations: {loc_count:,} records")
    logger.info(f"mp_routes: {route_count:,} records")
    logger.info(f"  - with coordinates: {routes_with_coords:,}")
    logger.info(f"\nRoutes by type:")
    for route_type, count in type_counts:
        logger.info(f"  {route_type or 'NULL'}: {count:,}")


def main():
    parser = argparse.ArgumentParser(description="Migrate MP data to PostgreSQL")
    parser.add_argument("--db-url", help="Database URL (overrides DATABASE_URL env)")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for inserts")
    parser.add_argument("--skip-locations", action="store_true", help="Skip locations import")
    parser.add_argument("--skip-routes", action="store_true", help="Skip routes import")
    args = parser.parse_args()

    # Get database URL
    db_url = args.db_url or os.environ.get('DATABASE_URL')
    if not db_url:
        logger.error("No database URL provided. Use --db-url or set DATABASE_URL env var")
        sys.exit(1)

    # Verify CSV files exist
    if not args.skip_locations and not LOCATIONS_CSV.exists():
        logger.error(f"Locations CSV not found: {LOCATIONS_CSV}")
        sys.exit(1)

    if not args.skip_routes and not ROUTES_CSV.exists():
        logger.error(f"Routes CSV not found: {ROUTES_CSV}")
        sys.exit(1)

    start_time = datetime.now()
    logger.info(f"Starting migration at {start_time}")

    try:
        conn = get_connection(db_url)

        # Create tables
        create_mp_locations_table(conn)
        create_mp_routes_table(conn)

        # Load data
        if not args.skip_locations:
            load_locations(conn, args.batch_size)

        if not args.skip_routes:
            load_routes(conn, args.batch_size)

        # Verify
        verify_migration(conn)

        conn.close()

        elapsed = datetime.now() - start_time
        logger.info(f"\nMigration completed in {elapsed}")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise


if __name__ == "__main__":
    main()
