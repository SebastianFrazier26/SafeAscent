#!/usr/bin/env python3
"""
Area Weekly Weather Collector (Hybrid Version)
===============================================
Collects weekly weather summaries for climbing areas.
Uses hybrid approach: writes to local files AND batches to Neon database.

Uses Open-Meteo Historical API (free, no key required).
Stores weekly aggregates rather than daily data for efficient querying.

Output:
    - data/tables/area_weekly_weather.csv (local file - source of truth)
    - Neon database area_weekly_weather table (synced after each area)

Usage:
    python scripts/collect_area_weekly_weather_hybrid.py [--weeks N] [--resume]

    --weeks N    Number of weeks to collect (default: 52)
    --resume     Resume from progress file
    --refresh    Only fetch the most recent complete week (for weekly cron)
"""

import pandas as pd
import requests
import json
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import logging
from collections import defaultdict
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data/area_weather_collect.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
API_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
RATE_LIMIT_SECONDS = 1.0  # Be respectful to free API
SAVE_INTERVAL = 20  # Save progress every N areas
OUTPUT_FILE = Path("data/tables/area_weekly_weather.csv")
PROGRESS_FILE = Path("data/.area_weather_progress.json")
AREAS_CACHE_FILE = Path("data/.climbing_areas_cache.json")

# Database configuration
NEON_URL = "postgresql://neondb_owner:npg_mrghaUfM78Xb@ep-billowing-bonus-ajxfhalu-pooler.c-3.us-east-2.aws.neon.tech/neondb?sslmode=require"
DB_SYNC_INTERVAL = 1  # Sync to DB after each area (52 records per area)

# Grid resolution for grouping nearby coordinates (about 10km)
COORD_PRECISION = 1  # Decimal places for grouping


def get_db_connection():
    """Get database connection with retry logic."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(NEON_URL)
            conn.autocommit = False
            return conn
        except Exception as e:
            logger.warning(f"DB connection attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return None


def batch_insert_weather(conn, weather_records):
    """
    Batch insert weather records to database using upsert.

    Args:
        conn: Database connection
        weather_records: List of weather dictionaries

    Returns:
        Number of rows inserted/updated
    """
    if not weather_records or not conn:
        return 0

    try:
        cur = conn.cursor()

        values = []
        for rec in weather_records:
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

        # Upsert query (update on conflict)
        insert_sql = """
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
        """

        execute_values(cur, insert_sql, values, page_size=100)
        inserted = cur.rowcount
        conn.commit()

        return inserted

    except Exception as e:
        logger.error(f"Database insert failed: {e}")
        try:
            conn.rollback()
        except:
            pass
        return 0


def get_week_bounds(date):
    """Get Monday-Sunday bounds for the week containing date."""
    monday = date - timedelta(days=date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def get_past_weeks(num_weeks):
    """Get list of (week_start, week_end) for past N complete weeks."""
    today = datetime.now().date()
    last_sunday = today - timedelta(days=today.weekday() + 1)

    weeks = []
    for i in range(num_weeks):
        week_end = last_sunday - timedelta(weeks=i)
        week_start = week_end - timedelta(days=6)
        weeks.append((week_start, week_end))

    return weeks[::-1]


def round_coords(lat, lon, precision=COORD_PRECISION):
    """Round coordinates to group nearby locations."""
    return (round(lat, precision), round(lon, precision))


def load_climbing_areas():
    """Load unique climbing areas from locations data."""
    cache_file = Path("data/.climbing_areas_cache.json")
    if cache_file.exists():
        logger.info("Loading areas from cache file...")
        with open(cache_file) as f:
            areas = json.load(f)
        logger.info(f"Found {len(areas)} cached areas")
        return areas

    routes_progress = Path("data/.mp_scrape_progress_v2.json")

    if routes_progress.exists():
        logger.info("Loading areas from routes scraper progress (locations)...")
        with open(routes_progress) as f:
            data = json.load(f)

        area_coords = {}
        for location in data.get('locations', {}).values():
            lat = location.get('latitude')
            lon = location.get('longitude')
            if lat and lon:
                key = round_coords(lat, lon)
                if key not in area_coords:
                    area_coords[key] = {
                        'latitude': key[0],
                        'longitude': key[1],
                        'location_count': 0,
                        'sample_name': location.get('name', 'Unknown')
                    }
                area_coords[key]['location_count'] += 1

        areas = list(area_coords.values())
        logger.info(f"Found {len(areas)} unique area coordinates from {len(data.get('locations', {}))} locations")
        return areas

    routes_csv = Path("data/tables/routes.csv")
    if routes_csv.exists():
        logger.info("Loading areas from routes.csv...")
        df = pd.read_csv(routes_csv)

        area_coords = {}
        for _, row in df.iterrows():
            lat, lon = row.get('latitude'), row.get('longitude')
            if pd.notna(lat) and pd.notna(lon):
                key = round_coords(lat, lon)
                if key not in area_coords:
                    area_coords[key] = {
                        'latitude': key[0],
                        'longitude': key[1],
                        'location_count': 0,
                        'sample_name': row.get('mountain_name', 'Unknown')
                    }
                area_coords[key]['location_count'] += 1

        areas = list(area_coords.values())
        logger.info(f"Found {len(areas)} unique area coordinates")
        return areas

    logger.error("No route data found!")
    return []


def fetch_week_weather(lat, lon, week_start, week_end, max_retries=3):
    """Fetch weather for a specific week and aggregate to weekly summary."""
    params = {
        'latitude': lat,
        'longitude': lon,
        'start_date': week_start.strftime('%Y-%m-%d'),
        'end_date': week_end.strftime('%Y-%m-%d'),
        'daily': 'temperature_2m_max,temperature_2m_min,temperature_2m_mean,'
                 'precipitation_sum,snowfall_sum,wind_speed_10m_max,'
                 'wind_gusts_10m_max,cloud_cover_mean',
        'timezone': 'auto'
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(API_BASE_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if 'daily' not in data:
                return None

            daily = data['daily']

            def safe_mean(values):
                valid = [v for v in values if v is not None]
                return sum(valid) / len(valid) if valid else None

            def safe_max(values):
                valid = [v for v in values if v is not None]
                return max(valid) if valid else None

            def safe_min(values):
                valid = [v for v in values if v is not None]
                return min(valid) if valid else None

            def safe_sum(values):
                valid = [v for v in values if v is not None]
                return sum(valid) if valid else None

            return {
                'temp_avg': round(safe_mean(daily.get('temperature_2m_mean', [])) or 0, 1),
                'temp_min': round(safe_min(daily.get('temperature_2m_min', [])) or 0, 1),
                'temp_max': round(safe_max(daily.get('temperature_2m_max', [])) or 0, 1),
                'precip_mm': round(safe_sum(daily.get('precipitation_sum', [])) or 0, 1),
                'snow_cm': round((safe_sum(daily.get('snowfall_sum', [])) or 0) / 10, 1),
                'wind_max_kmh': round(safe_max(daily.get('wind_speed_10m_max', [])) or 0, 1),
                'wind_gust_max_kmh': round(safe_max(daily.get('wind_gusts_10m_max', [])) or 0, 1),
                'cloud_cover_avg': round(safe_mean(daily.get('cloud_cover_mean', [])) or 0, 1),
            }

        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                logger.warning(f"Failed to fetch weather for ({lat}, {lon}): {e}")
                return None

    return None


def collect_area_weather(areas, weeks, resume=False):
    """Collect weekly weather for all areas with hybrid file + database writes."""
    all_records = []
    processed_keys = set()
    db_synced_count = 0

    # Load progress if resuming
    if resume and PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            progress = json.load(f)
            all_records = progress.get('records', [])
            processed_keys = set(progress.get('processed_keys', []))
            db_synced_count = progress.get('db_synced_count', 0)
        logger.info(f"Resuming: {len(processed_keys)} area-weeks processed, {db_synced_count} synced to DB")

    # Connect to database
    db_conn = get_db_connection()
    if db_conn:
        logger.info("Connected to Neon database for hybrid writes")
    else:
        logger.warning("Could not connect to database - will write to local files only")

    total_work = len(areas) * len(weeks)
    completed = len(processed_keys)

    logger.info(f"Collecting weather for {len(areas)} areas × {len(weeks)} weeks = {total_work} requests")

    pending_db_records = []  # Buffer for database writes

    try:
        for area_idx, area in enumerate(areas):
            lat, lon = area['latitude'], area['longitude']
            area_records = []  # Records for this area

            for week_start, week_end in weeks:
                key = f"{lat},{lon},{week_start}"

                if key in processed_keys:
                    continue

                weather = fetch_week_weather(lat, lon, week_start, week_end)

                if weather:
                    record = {
                        'latitude': lat,
                        'longitude': lon,
                        'week_start': week_start.strftime('%Y-%m-%d'),
                        'week_end': week_end.strftime('%Y-%m-%d'),
                        **weather
                    }
                    all_records.append(record)
                    area_records.append(record)

                processed_keys.add(key)
                completed += 1

                time.sleep(RATE_LIMIT_SECONDS)

            # Sync area's records to database after completing all weeks for this area
            if area_records and db_conn:
                inserted = batch_insert_weather(db_conn, area_records)
                if inserted > 0:
                    db_synced_count += inserted
                    logger.debug(f"DB sync: inserted {inserted} records for area ({lat}, {lon})")
                else:
                    # Reconnect if connection was lost
                    try:
                        db_conn.close()
                    except:
                        pass
                    db_conn = get_db_connection()
                    if db_conn:
                        inserted = batch_insert_weather(db_conn, area_records)
                        if inserted > 0:
                            db_synced_count += inserted

            # Progress logging and saving
            if (area_idx + 1) % SAVE_INTERVAL == 0:
                pct = (completed / total_work) * 100
                logger.info(f"Progress: {area_idx + 1}/{len(areas)} areas, {completed}/{total_work} requests ({pct:.1f}%), DB synced: {db_synced_count}")

                # Save progress to local file
                with open(PROGRESS_FILE, 'w') as f:
                    json.dump({
                        'records': all_records,
                        'processed_keys': list(processed_keys),
                        'db_synced_count': db_synced_count,
                        'last_updated': datetime.now().isoformat()
                    }, f)

    except KeyboardInterrupt:
        logger.info("Interrupted! Saving progress...")

    finally:
        if db_conn:
            try:
                db_conn.close()
            except:
                pass

    # Final save
    with open(PROGRESS_FILE, 'w') as f:
        json.dump({
            'records': all_records,
            'processed_keys': list(processed_keys),
            'db_synced_count': db_synced_count,
            'last_updated': datetime.now().isoformat()
        }, f)

    # Save to CSV
    if all_records:
        df = pd.DataFrame(all_records)
        df.to_csv(OUTPUT_FILE, index=False)
        logger.info(f"Saved {len(all_records)} weekly weather records to {OUTPUT_FILE}")

    return all_records


def refresh_weekly(areas):
    """Add most recent complete week with database sync."""
    logger.info("Running weekly refresh...")

    # Connect to database
    db_conn = get_db_connection()

    if OUTPUT_FILE.exists():
        df = pd.read_csv(OUTPUT_FILE)
        logger.info(f"Loaded {len(df)} existing records")
    else:
        df = pd.DataFrame()

    weeks = get_past_weeks(1)
    if not weeks:
        logger.error("No complete week to fetch")
        return

    week_start, week_end = weeks[0]
    logger.info(f"Fetching weather for week: {week_start} to {week_end}")

    new_records = []
    for idx, area in enumerate(areas):
        lat, lon = area['latitude'], area['longitude']
        weather = fetch_week_weather(lat, lon, week_start, week_end)

        if weather:
            record = {
                'latitude': lat,
                'longitude': lon,
                'week_start': week_start.strftime('%Y-%m-%d'),
                'week_end': week_end.strftime('%Y-%m-%d'),
                **weather
            }
            new_records.append(record)

        if (idx + 1) % 50 == 0:
            logger.info(f"Fetched {idx + 1}/{len(areas)} areas")

        time.sleep(RATE_LIMIT_SECONDS)

    # Sync to database
    if new_records and db_conn:
        inserted = batch_insert_weather(db_conn, new_records)
        logger.info(f"Synced {inserted} records to database")
        db_conn.close()

    # Add new records to local file
    if new_records:
        new_df = pd.DataFrame(new_records)
        df = pd.concat([df, new_df], ignore_index=True)

    # Remove weeks older than 52 weeks
    cutoff = (datetime.now().date() - timedelta(weeks=52)).strftime('%Y-%m-%d')
    before_count = len(df)
    df = df[df['week_start'] >= cutoff]
    removed = before_count - len(df)

    if removed > 0:
        logger.info(f"Removed {removed} records older than {cutoff}")

    df.to_csv(OUTPUT_FILE, index=False)
    logger.info(f"Saved {len(df)} total records ({len(new_records)} new)")


def main():
    parser = argparse.ArgumentParser(description="Collect area weekly weather (hybrid mode)")
    parser.add_argument("--weeks", type=int, default=52, help="Number of weeks to collect")
    parser.add_argument("--resume", action="store_true", help="Resume from progress file")
    parser.add_argument("--refresh", action="store_true", help="Weekly refresh mode")
    args = parser.parse_args()

    areas = load_climbing_areas()
    if not areas:
        return

    if args.refresh:
        refresh_weekly(areas)
    else:
        weeks = get_past_weeks(args.weeks)
        logger.info(f"Will collect {len(weeks)} weeks of data: {weeks[0][0]} to {weeks[-1][1]}")
        collect_area_weather(areas, weeks, resume=args.resume)

    if OUTPUT_FILE.exists():
        df = pd.read_csv(OUTPUT_FILE)
        print(f"\n=== AREA WEATHER SUMMARY ===")
        print(f"Total records: {len(df):,}")
        print(f"Unique areas: {df.groupby(['latitude', 'longitude']).ngroups:,}")
        print(f"Date range: {df['week_start'].min()} to {df['week_end'].max()}")
        print(f"Weeks covered: {df['week_start'].nunique()}")


if __name__ == "__main__":
    main()
