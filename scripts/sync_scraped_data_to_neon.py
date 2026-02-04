#!/usr/bin/env python3
"""
Sync Scraped Data to Neon
=========================
One-time sync of existing scraped data (ticks and weather) to Neon database.
Run this before switching to hybrid scrapers to preserve existing progress.

Usage:
    python scripts/sync_scraped_data_to_neon.py [--ticks] [--weather] [--all]
"""

import json
import argparse
from pathlib import Path
import logging
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

NEON_URL = "postgresql://neondb_owner:npg_mrghaUfM78Xb@ep-billowing-bonus-ajxfhalu-pooler.c-3.us-east-2.aws.neon.tech/neondb?sslmode=require"

TICKS_PROGRESS_FILE = Path("data/.mp_ticks_progress.json")
WEATHER_PROGRESS_FILE = Path("data/.area_weather_progress.json")


def get_connection():
    """Get database connection."""
    conn = psycopg2.connect(NEON_URL)
    conn.autocommit = False
    return conn


def sync_ticks():
    """Sync existing ticks data to database."""
    if not TICKS_PROGRESS_FILE.exists():
        logger.warning(f"Ticks progress file not found: {TICKS_PROGRESS_FILE}")
        return 0

    logger.info(f"Loading ticks from {TICKS_PROGRESS_FILE}...")
    with open(TICKS_PROGRESS_FILE) as f:
        data = json.load(f)

    ticks = data.get('ticks', [])
    if not ticks:
        logger.info("No ticks to sync")
        return 0

    logger.info(f"Found {len(ticks):,} ticks to sync")

    conn = get_connection()
    cur = conn.cursor()

    # Check existing count
    cur.execute("SELECT COUNT(*) FROM mp_ticks")
    existing = cur.fetchone()[0]
    logger.info(f"Existing ticks in database: {existing:,}")

    # Batch insert
    batch_size = 1000
    total_inserted = 0

    for i in range(0, len(ticks), batch_size):
        batch = ticks[i:i + batch_size]
        values = []

        for tick in batch:
            values.append((
                tick['route_id'],
                tick.get('route_name', '')[:255] if tick.get('route_name') else None,
                tick['climber_name'][:255],
                tick.get('date'),
                tick.get('style', '')[:50] if tick.get('style') else None,
            ))

        try:
            execute_values(
                cur,
                """
                INSERT INTO mp_ticks (route_id, route_name, climber_name, tick_date, style)
                VALUES %s
                ON CONFLICT (route_id, climber_name, tick_date) DO NOTHING
                """,
                values,
                page_size=100
            )
            inserted = cur.rowcount
            total_inserted += inserted
            conn.commit()

            if (i + batch_size) % 10000 == 0:
                logger.info(f"Progress: {i + batch_size:,}/{len(ticks):,} processed, {total_inserted:,} inserted")

        except Exception as e:
            logger.error(f"Error inserting batch: {e}")
            conn.rollback()

    # Verify
    cur.execute("SELECT COUNT(*) FROM mp_ticks")
    final_count = cur.fetchone()[0]

    conn.close()

    logger.info(f"Sync complete: {total_inserted:,} new ticks inserted")
    logger.info(f"Total ticks in database: {final_count:,}")

    return total_inserted


def sync_weather():
    """Sync existing weather data to database."""
    if not WEATHER_PROGRESS_FILE.exists():
        logger.warning(f"Weather progress file not found: {WEATHER_PROGRESS_FILE}")
        return 0

    logger.info(f"Loading weather from {WEATHER_PROGRESS_FILE}...")
    with open(WEATHER_PROGRESS_FILE) as f:
        data = json.load(f)

    records = data.get('records', [])
    if not records:
        logger.info("No weather records to sync")
        return 0

    logger.info(f"Found {len(records):,} weather records to sync")

    conn = get_connection()
    cur = conn.cursor()

    # Check existing count
    cur.execute("SELECT COUNT(*) FROM area_weekly_weather")
    existing = cur.fetchone()[0]
    logger.info(f"Existing weather records in database: {existing:,}")

    # Batch insert
    batch_size = 500
    total_inserted = 0

    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        values = []

        for rec in batch:
            values.append((
                rec['latitude'],
                rec['longitude'],
                rec['week_start'],
                rec['week_end'],
                rec.get('temp_avg'),
                rec.get('temp_min'),
                rec.get('temp_max'),
                rec.get('precip_mm'),
                rec.get('snow_cm'),
                rec.get('wind_max_kmh'),
                rec.get('wind_gust_max_kmh'),
                rec.get('cloud_cover_avg'),
            ))

        try:
            execute_values(
                cur,
                """
                INSERT INTO area_weekly_weather
                    (latitude, longitude, week_start, week_end, temp_avg, temp_min, temp_max,
                     precip_mm, snow_cm, wind_max_kmh, wind_gust_max_kmh, cloud_cover_avg)
                VALUES %s
                ON CONFLICT (latitude, longitude, week_start)
                DO UPDATE SET
                    temp_avg = EXCLUDED.temp_avg,
                    temp_min = EXCLUDED.temp_min,
                    temp_max = EXCLUDED.temp_max,
                    precip_mm = EXCLUDED.precip_mm,
                    snow_cm = EXCLUDED.snow_cm,
                    wind_max_kmh = EXCLUDED.wind_max_kmh,
                    wind_gust_max_kmh = EXCLUDED.wind_gust_max_kmh,
                    cloud_cover_avg = EXCLUDED.cloud_cover_avg
                """,
                values,
                page_size=100
            )
            inserted = cur.rowcount
            total_inserted += inserted
            conn.commit()

            if (i + batch_size) % 5000 == 0:
                logger.info(f"Progress: {i + batch_size:,}/{len(records):,} processed")

        except Exception as e:
            logger.error(f"Error inserting batch: {e}")
            conn.rollback()

    # Verify
    cur.execute("SELECT COUNT(*) FROM area_weekly_weather")
    final_count = cur.fetchone()[0]

    conn.close()

    logger.info(f"Sync complete: {total_inserted:,} weather records synced")
    logger.info(f"Total weather records in database: {final_count:,}")

    return total_inserted


def main():
    parser = argparse.ArgumentParser(description="Sync scraped data to Neon")
    parser.add_argument("--ticks", action="store_true", help="Sync ticks data")
    parser.add_argument("--weather", action="store_true", help="Sync weather data")
    parser.add_argument("--all", action="store_true", help="Sync all data")
    args = parser.parse_args()

    if not (args.ticks or args.weather or args.all):
        args.all = True  # Default to syncing all

    print("=== SYNCING SCRAPED DATA TO NEON ===\n")

    if args.ticks or args.all:
        print("--- Syncing Ticks ---")
        sync_ticks()
        print()

    if args.weather or args.all:
        print("--- Syncing Weather ---")
        sync_weather()
        print()

    print("=== SYNC COMPLETE ===")


if __name__ == "__main__":
    main()
