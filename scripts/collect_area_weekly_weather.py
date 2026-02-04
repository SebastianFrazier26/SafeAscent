#!/usr/bin/env python3
"""
Area Weekly Weather Collector
=============================
Collects weekly weather summaries for climbing areas over the past year.

Uses Open-Meteo Historical API (free, no key required).
Stores weekly aggregates rather than daily data for efficient querying.

Output: data/tables/area_weekly_weather.csv

Usage:
    python scripts/collect_area_weekly_weather.py [--weeks N] [--resume]

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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
API_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
RATE_LIMIT_SECONDS = 1.0  # Be respectful to free API
SAVE_INTERVAL = 20  # Save progress every N areas
OUTPUT_FILE = Path("data/tables/area_weekly_weather.csv")
PROGRESS_FILE = Path("data/.area_weather_progress.json")
AREAS_CACHE_FILE = Path("data/.climbing_areas_cache.json")

# Grid resolution for grouping nearby coordinates (about 10km)
COORD_PRECISION = 1  # Decimal places for grouping


def get_week_bounds(date):
    """Get Monday-Sunday bounds for the week containing date."""
    # Find Monday of this week
    monday = date - timedelta(days=date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def get_past_weeks(num_weeks):
    """Get list of (week_start, week_end) for past N complete weeks."""
    today = datetime.now().date()
    # Start from last complete week (last Sunday)
    last_sunday = today - timedelta(days=today.weekday() + 1)

    weeks = []
    for i in range(num_weeks):
        week_end = last_sunday - timedelta(weeks=i)
        week_start = week_end - timedelta(days=6)
        weeks.append((week_start, week_end))

    return weeks[::-1]  # Chronological order


def round_coords(lat, lon, precision=COORD_PRECISION):
    """Round coordinates to group nearby locations."""
    return (round(lat, precision), round(lon, precision))


def load_climbing_areas():
    """Load unique climbing areas from locations data."""
    # Try cached areas file first (avoids conflict with running routes scraper)
    cache_file = Path("data/.climbing_areas_cache.json")
    if cache_file.exists():
        logger.info("Loading areas from cache file...")
        with open(cache_file) as f:
            areas = json.load(f)
        logger.info(f"Found {len(areas)} cached areas")
        return areas

    # Try routes progress file (has locations with coordinates)
    routes_progress = Path("data/.mp_scrape_progress_v2.json")

    if routes_progress.exists():
        logger.info("Loading areas from routes scraper progress (locations)...")
        with open(routes_progress) as f:
            data = json.load(f)

        # Group locations by rounded coordinates
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

    # Fallback to routes CSV
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

            # Aggregate daily data to weekly summary
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
                'snow_cm': round((safe_sum(daily.get('snowfall_sum', [])) or 0) / 10, 1),  # mm to cm
                'wind_max_kmh': round(safe_max(daily.get('wind_speed_10m_max', [])) or 0, 1),
                'wind_gust_max_kmh': round(safe_max(daily.get('wind_gusts_10m_max', [])) or 0, 1),
                'cloud_cover_avg': round(safe_mean(daily.get('cloud_cover_mean', [])) or 0, 1),
            }

        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                logger.warning(f"Failed to fetch weather for ({lat}, {lon}): {e}")
                return None

    return None


def collect_area_weather(areas, weeks, resume=False):
    """Collect weekly weather for all areas."""
    all_records = []
    processed_keys = set()

    # Load progress if resuming
    if resume and PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            progress = json.load(f)
            all_records = progress.get('records', [])
            processed_keys = set(progress.get('processed_keys', []))
        logger.info(f"Resuming: {len(processed_keys)} area-weeks already processed")

    total_work = len(areas) * len(weeks)
    completed = len(processed_keys)

    logger.info(f"Collecting weather for {len(areas)} areas × {len(weeks)} weeks = {total_work} requests")

    try:
        for area_idx, area in enumerate(areas):
            lat, lon = area['latitude'], area['longitude']

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

                processed_keys.add(key)
                completed += 1

                # Rate limiting
                time.sleep(RATE_LIMIT_SECONDS)

            # Progress logging and saving
            if (area_idx + 1) % SAVE_INTERVAL == 0:
                pct = (completed / total_work) * 100
                logger.info(f"Progress: {area_idx + 1}/{len(areas)} areas, {completed}/{total_work} requests ({pct:.1f}%)")

                # Save progress
                with open(PROGRESS_FILE, 'w') as f:
                    json.dump({
                        'records': all_records,
                        'processed_keys': list(processed_keys),
                        'last_updated': datetime.now().isoformat()
                    }, f)

    except KeyboardInterrupt:
        logger.info("Interrupted! Saving progress...")

    # Final save
    with open(PROGRESS_FILE, 'w') as f:
        json.dump({
            'records': all_records,
            'processed_keys': list(processed_keys),
            'last_updated': datetime.now().isoformat()
        }, f)

    # Save to CSV
    if all_records:
        df = pd.DataFrame(all_records)
        df.to_csv(OUTPUT_FILE, index=False)
        logger.info(f"Saved {len(all_records)} weekly weather records to {OUTPUT_FILE}")

    return all_records


def refresh_weekly(areas):
    """Add most recent complete week, remove weeks older than 52."""
    logger.info("Running weekly refresh...")

    # Load existing data
    if OUTPUT_FILE.exists():
        df = pd.read_csv(OUTPUT_FILE)
        logger.info(f"Loaded {len(df)} existing records")
    else:
        df = pd.DataFrame()

    # Get the most recent complete week
    weeks = get_past_weeks(1)
    if not weeks:
        logger.error("No complete week to fetch")
        return

    week_start, week_end = weeks[0]
    logger.info(f"Fetching weather for week: {week_start} to {week_end}")

    # Collect weather for all areas for this week
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

    # Add new records
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

    # Save
    df.to_csv(OUTPUT_FILE, index=False)
    logger.info(f"Saved {len(df)} total records ({len(new_records)} new)")


def main():
    parser = argparse.ArgumentParser(description="Collect area weekly weather")
    parser.add_argument("--weeks", type=int, default=52, help="Number of weeks to collect")
    parser.add_argument("--resume", action="store_true", help="Resume from progress file")
    parser.add_argument("--refresh", action="store_true", help="Weekly refresh mode")
    args = parser.parse_args()

    # Load climbing areas
    areas = load_climbing_areas()
    if not areas:
        return

    if args.refresh:
        refresh_weekly(areas)
    else:
        weeks = get_past_weeks(args.weeks)
        logger.info(f"Will collect {len(weeks)} weeks of data: {weeks[0][0]} to {weeks[-1][1]}")
        collect_area_weather(areas, weeks, resume=args.resume)

    # Print summary
    if OUTPUT_FILE.exists():
        df = pd.read_csv(OUTPUT_FILE)
        print(f"\n=== AREA WEATHER SUMMARY ===")
        print(f"Total records: {len(df):,}")
        print(f"Unique areas: {df.groupby(['latitude', 'longitude']).ngroups:,}")
        print(f"Date range: {df['week_start'].min()} to {df['week_end'].max()}")
        print(f"Weeks covered: {df['week_start'].nunique()}")


if __name__ == "__main__":
    main()
