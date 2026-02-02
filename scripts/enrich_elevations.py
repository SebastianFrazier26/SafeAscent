#!/usr/bin/env python3
"""
Elevation Enrichment Script

Fetches elevation data for all coordinates in the database using Open-Elevation API.
Adds elevation_meters column to routes, accidents, and weather tables.

Usage:
    python scripts/enrich_elevations.py

API: https://open-elevation.com/
- Free, no API key required
- Supports bulk requests (up to 100 coordinates per request)
- Uses SRTM 30m DEM (Shuttle Radar Topography Mission)
"""
import os
import time
import requests
from typing import List, Tuple, Dict
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_batch
from tqdm import tqdm

# Load environment variables
load_dotenv()

# Database connection
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "safeascent")
DB_USER = os.getenv("DB_USER", "sebastianfrazier")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Open-Elevation API endpoint
ELEVATION_API_URL = "https://api.open-elevation.com/api/v1/lookup"

# Batch size for API requests
BATCH_SIZE = 100


def get_db_connection():
    """Create database connection."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def add_elevation_columns():
    """Add elevation_meters column to routes, accidents, and weather tables."""
    print("Adding elevation_meters columns to tables...")

    conn = get_db_connection()
    cur = conn.cursor()

    tables = ["routes", "accidents", "weather"]

    for table in tables:
        try:
            cur.execute(f"""
                ALTER TABLE {table}
                ADD COLUMN IF NOT EXISTS elevation_meters REAL;
            """)
            conn.commit()
            print(f"  ✓ Added elevation_meters to {table}")
        except Exception as e:
            print(f"  ⚠ Error adding column to {table}: {e}")
            conn.rollback()

    cur.close()
    conn.close()
    print("Columns added successfully!\n")


def fetch_unique_coordinates() -> Dict[str, List[Tuple[int, float, float]]]:
    """
    Fetch unique coordinates from routes, accidents, and weather tables.

    Returns:
        Dictionary mapping table_name -> [(id, latitude, longitude), ...]
    """
    print("Fetching unique coordinates from database...")

    conn = get_db_connection()
    cur = conn.cursor()

    coordinates = {}

    # Routes
    cur.execute("""
        SELECT route_id, latitude, longitude
        FROM routes
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
          AND elevation_meters IS NULL
        ORDER BY route_id;
    """)
    coordinates["routes"] = cur.fetchall()
    print(f"  Routes: {len(coordinates['routes'])} coordinates")

    # Accidents
    cur.execute("""
        SELECT accident_id, latitude, longitude
        FROM accidents
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
          AND elevation_meters IS NULL
        ORDER BY accident_id;
    """)
    coordinates["accidents"] = cur.fetchall()
    print(f"  Accidents: {len(coordinates['accidents'])} coordinates")

    # Weather
    cur.execute("""
        SELECT weather_id, latitude, longitude
        FROM weather
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
          AND elevation_meters IS NULL
        ORDER BY weather_id;
    """)
    coordinates["weather"] = cur.fetchall()
    print(f"  Weather: {len(coordinates['weather'])} coordinates")

    cur.close()
    conn.close()

    total = sum(len(coords) for coords in coordinates.values())
    print(f"Total coordinates to fetch: {total}\n")

    return coordinates


def fetch_elevations_batch(coords: List[Tuple[float, float]]) -> List[float]:
    """
    Fetch elevations for a batch of coordinates using Open-Elevation API.

    Args:
        coords: List of (latitude, longitude) tuples

    Returns:
        List of elevations in meters (same order as input)

    Raises:
        Exception if API request fails
    """
    # Prepare request payload
    locations = [{"latitude": lat, "longitude": lon} for lat, lon in coords]

    payload = {"locations": locations}

    # Make API request with retry logic
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(
                ELEVATION_API_URL,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()

            # Parse response
            data = response.json()
            elevations = [result["elevation"] for result in data["results"]]

            return elevations

        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff
                print(f"  ⚠ API request failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise Exception(f"Failed to fetch elevations after {max_retries} attempts: {e}")


def update_elevations(table: str, updates: List[Tuple[float, int]]):
    """
    Update elevation_meters for records in a table.

    Args:
        table: Table name (routes, accidents, weather)
        updates: List of (elevation_meters, id) tuples
    """
    conn = get_db_connection()
    cur = conn.cursor()

    # Determine ID column name
    id_column = f"{table.rstrip('s')}_id"  # routes->route_id, accidents->accident_id, weather->weather_id

    # Batch update
    execute_batch(
        cur,
        f"UPDATE {table} SET elevation_meters = %s WHERE {id_column} = %s",
        updates,
        page_size=1000,
    )

    conn.commit()
    cur.close()
    conn.close()


def enrich_table_elevations(table: str, coordinates: List[Tuple[int, float, float]]):
    """
    Fetch and update elevations for all coordinates in a table.

    Args:
        table: Table name
        coordinates: List of (id, latitude, longitude) tuples
    """
    if not coordinates:
        print(f"No coordinates to process for {table}")
        return

    print(f"\nProcessing {table} ({len(coordinates)} coordinates)...")

    # Process in batches
    updates = []

    with tqdm(total=len(coordinates), desc=f"  {table}") as pbar:
        for i in range(0, len(coordinates), BATCH_SIZE):
            batch = coordinates[i:i + BATCH_SIZE]

            # Extract (lat, lon) for API
            coords = [(lat, lon) for id_, lat, lon in batch]

            # Fetch elevations
            try:
                elevations = fetch_elevations_batch(coords)

                # Pair elevations with IDs
                for (id_, lat, lon), elevation in zip(batch, elevations):
                    updates.append((elevation, id_))

                pbar.update(len(batch))

                # Rate limiting: small delay between batches
                if i + BATCH_SIZE < len(coordinates):
                    time.sleep(0.5)  # 500ms delay

            except Exception as e:
                print(f"\n  ⚠ Error processing batch {i//BATCH_SIZE + 1}: {e}")
                print(f"  Skipping {len(batch)} coordinates...")
                pbar.update(len(batch))
                continue

    # Update database
    if updates:
        print(f"  Updating {len(updates)} records in database...")
        update_elevations(table, updates)
        print(f"  ✓ {table} updated successfully!")
    else:
        print(f"  ⚠ No elevations fetched for {table}")


def print_coverage_summary():
    """Print elevation coverage summary after enrichment."""
    print("\n" + "="*60)
    print("ELEVATION COVERAGE SUMMARY")
    print("="*60)

    conn = get_db_connection()
    cur = conn.cursor()

    tables = [
        ("routes", "route_id"),
        ("accidents", "accident_id"),
        ("weather", "weather_id"),
    ]

    for table, id_col in tables:
        cur.execute(f"""
            SELECT
                COUNT(*) as total,
                COUNT(elevation_meters) as with_elevation,
                ROUND(100.0 * COUNT(elevation_meters) / NULLIF(COUNT(*), 0), 1) as pct
            FROM {table}
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL;
        """)
        total, with_elev, pct = cur.fetchone()
        print(f"{table.capitalize():12} {with_elev:5}/{total:5} ({pct:5.1f}%)")

    cur.close()
    conn.close()
    print("="*60 + "\n")


def main():
    """Main execution function."""
    print("\n" + "="*60)
    print("SAFEASCENT ELEVATION ENRICHMENT")
    print("="*60 + "\n")

    # Step 1: Add elevation columns if not exist
    add_elevation_columns()

    # Step 2: Fetch unique coordinates
    coordinates_by_table = fetch_unique_coordinates()

    # Step 3: Fetch and update elevations for each table
    for table in ["routes", "accidents", "weather"]:
        enrich_table_elevations(table, coordinates_by_table[table])

    # Step 4: Print summary
    print_coverage_summary()

    print("✓ Elevation enrichment complete!\n")


if __name__ == "__main__":
    main()
