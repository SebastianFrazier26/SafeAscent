#!/usr/bin/env python3
"""
Backfill Weather Data - 7-Day Windows for All Accidents

Collects the proper 7-day weather window for each accident:
- Days -6 to 0 relative to accident date (7 days total)
- Uses Open-Meteo Historical Weather API (same source as original collection)
- Only fetches missing dates to avoid duplicate work
- Resume-able: Can restart if interrupted
- Saves progress every 50 accidents
"""

import pandas as pd
import requests
from datetime import datetime, timedelta
import time
from tqdm import tqdm
import os
import sys
from pathlib import Path

# Configuration
API_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
RATE_LIMIT_SECONDS = 2  # Respectful rate limiting
SAVE_INTERVAL = 50  # Save progress every N accidents
OUTPUT_FILE = 'data/tables/weather.csv'
PROGRESS_FILE = 'data/tables/.weather_backfill_progress.txt'


def get_7day_window(accident_date_str):
    """
    Get 7-day window before accident (days -6 to 0).

    Args:
        accident_date_str: Accident date in 'YYYY-MM-DD' format

    Returns:
        tuple: (start_date, end_date) as datetime objects
    """
    accident_date = datetime.strptime(accident_date_str, '%Y-%m-%d')
    start_date = accident_date - timedelta(days=6)  # 6 days before
    end_date = accident_date  # Day of accident (day 0)
    return start_date, end_date


def fetch_weather_data_robust(latitude, longitude, start_date, end_date, max_retries=3):
    """
    Fetch weather data from Open-Meteo with retry logic.

    Args:
        latitude: Location latitude
        longitude: Location longitude
        start_date: Start date (YYYY-MM-DD string)
        end_date: End date (YYYY-MM-DD string)
        max_retries: Maximum number of retry attempts

    Returns:
        list: Daily weather records or None if all retries fail
    """
    params = {
        'latitude': latitude,
        'longitude': longitude,
        'start_date': start_date,
        'end_date': end_date,
        'hourly': 'temperature_2m,wind_speed_10m,precipitation,visibility,cloud_cover',
        'timezone': 'auto'
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(API_BASE_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            # Parse hourly data into daily summaries
            if 'hourly' not in data:
                return None

            hourly = data['hourly']
            times = hourly['time']
            temps = hourly['temperature_2m']
            winds = hourly['wind_speed_10m']
            precips = hourly['precipitation']
            visibility = hourly['visibility']
            clouds = hourly['cloud_cover']

            # Group by date
            daily_records = {}

            for i, time_str in enumerate(times):
                dt = datetime.fromisoformat(time_str)
                date_key = dt.date()

                if date_key not in daily_records:
                    daily_records[date_key] = {
                        'date': date_key,
                        'temps': [],
                        'winds': [],
                        'precips': [],
                        'visibility': [],
                        'clouds': []
                    }

                # Collect hourly values (handle None)
                if temps[i] is not None:
                    daily_records[date_key]['temps'].append(temps[i])
                if winds[i] is not None:
                    daily_records[date_key]['winds'].append(winds[i])
                if precips[i] is not None:
                    daily_records[date_key]['precips'].append(precips[i])
                if visibility[i] is not None:
                    daily_records[date_key]['visibility'].append(visibility[i])
                if clouds[i] is not None:
                    daily_records[date_key]['clouds'].append(clouds[i])

            # Calculate daily statistics
            weather_records = []
            for date_key, values in sorted(daily_records.items()):
                record = {
                    'date': date_key.strftime('%Y-%m-%d'),
                    'latitude': latitude,
                    'longitude': longitude,
                    'temperature_avg': round(sum(values['temps']) / len(values['temps']), 2) if values['temps'] else None,
                    'temperature_min': round(min(values['temps']), 2) if values['temps'] else None,
                    'temperature_max': round(max(values['temps']), 2) if values['temps'] else None,
                    'wind_speed_avg': round(sum(values['winds']) / len(values['winds']), 2) if values['winds'] else None,
                    'wind_speed_max': round(max(values['winds']), 2) if values['winds'] else None,
                    'precipitation_total': round(sum(values['precips']), 2) if values['precips'] else None,
                    'visibility_avg': round(sum(values['visibility']) / len(values['visibility']), 2) if values['visibility'] else None,
                    'cloud_cover_avg': round(sum(values['clouds']) / len(values['clouds']), 2) if values['clouds'] else None
                }
                weather_records.append(record)

            return weather_records

        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                time.sleep(5)  # Wait before retry
                continue
            return None
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(5)
                continue
            print(f"\n⚠️  API Error (after {max_retries} retries): {e}")
            return None
        except Exception as e:
            print(f"\n⚠️  Unexpected error: {e}")
            return None

    return None


def get_existing_weather_dates(existing_df, accident_id):
    """
    Get dates that already have weather data for this accident.

    Args:
        existing_df: DataFrame of existing weather records
        accident_id: Accident ID to check

    Returns:
        set: Set of dates (as strings) that already exist
    """
    if existing_df is None or len(existing_df) == 0:
        return set()

    accident_weather = existing_df[existing_df['accident_id'] == accident_id]
    return set(accident_weather['date'].astype(str).values)


def load_progress():
    """Load progress from previous run."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            return set(int(line.strip()) for line in f if line.strip())
    return set()


def save_progress(processed_ids):
    """Save progress to file."""
    with open(PROGRESS_FILE, 'w') as f:
        for accident_id in sorted(processed_ids):
            f.write(f"{accident_id}\n")


def backfill_weather_7day_windows():
    """
    Main function to backfill 7-day weather windows for all accidents.
    """
    print("\n" + "=" * 80)
    print("WEATHER DATA BACKFILL - 7-DAY WINDOWS")
    print("=" * 80)
    print("\nThis script will:")
    print("  1. Load all accidents with dates + coordinates")
    print("  2. For each accident, collect weather for days -6 to 0")
    print("  3. Skip dates that already exist in the database")
    print("  4. Save progress incrementally (resume-able)")
    print()

    # Load accidents
    print("Loading accidents data...")
    accidents_df = pd.read_csv('data/tables/accidents.csv')

    # Filter to usable accidents
    valid_accidents = accidents_df[
        accidents_df['date'].notna() &
        accidents_df['latitude'].notna() &
        accidents_df['longitude'].notna()
    ].copy()

    print(f"Total accidents: {len(accidents_df):,}")
    print(f"With date + coordinates: {len(valid_accidents):,}")

    # Load existing weather data
    if os.path.exists(OUTPUT_FILE):
        print(f"\nLoading existing weather data from {OUTPUT_FILE}...")
        existing_weather_df = pd.read_csv(OUTPUT_FILE)
        print(f"Existing weather records: {len(existing_weather_df):,}")

        # Check current coverage
        accidents_with_weather = existing_weather_df['accident_id'].notna().sum()
        print(f"Weather records linked to accidents: {accidents_with_weather:,}")

        # Start with existing records
        weather_records = existing_weather_df.to_dict('records')
        next_weather_id = existing_weather_df['weather_id'].max() + 1
    else:
        print(f"\nNo existing weather file found. Will create new one.")
        existing_weather_df = None
        weather_records = []
        next_weather_id = 1

    # Load progress from previous run
    processed_ids = load_progress()
    if processed_ids:
        print(f"\n✓ Found progress file: {len(processed_ids):,} accidents already processed")
        remaining = valid_accidents[~valid_accidents['accident_id'].isin(processed_ids)]
        print(f"  Remaining: {len(remaining):,} accidents")
    else:
        remaining = valid_accidents
        print(f"\n→ Starting fresh: {len(remaining):,} accidents to process")

    if len(remaining) == 0:
        print("\n✅ All accidents already processed!")
        return

    # Estimate work
    print("\nEstimating work:")
    total_accidents = len(remaining)
    max_api_calls = total_accidents  # 1 API call per accident (7 days at once)
    estimated_minutes = (max_api_calls * RATE_LIMIT_SECONDS) / 60
    print(f"  Accidents to process: {total_accidents:,}")
    print(f"  Max API calls needed: {max_api_calls:,}")
    print(f"  Estimated time: {estimated_minutes:.1f} minutes ({estimated_minutes/60:.1f} hours)")
    print(f"  Rate limit: 1 request per {RATE_LIMIT_SECONDS} seconds")

    # Auto-proceed (for background execution)
    print("\nProceeding with backfill...")

    # Process accidents
    print("\n" + "=" * 80)
    print("COLLECTING WEATHER DATA")
    print("=" * 80 + "\n")

    stats = {
        'processed': 0,
        'success': 0,
        'partial': 0,  # Got some days but not all 7
        'failed': 0,
        'skipped': 0,  # Already had all 7 days
        'total_records_added': 0
    }

    pbar = tqdm(remaining.iterrows(), total=len(remaining), desc="Processing accidents")

    for idx, accident in pbar:
        accident_id = accident['accident_id']
        accident_date = accident['date']
        lat = accident['latitude']
        lon = accident['longitude']

        # Get 7-day window
        start_date, end_date = get_7day_window(accident_date)

        # Check existing weather dates
        existing_dates = get_existing_weather_dates(existing_weather_df, accident_id)

        # Generate all dates we need (7 days)
        needed_dates = set()
        current = start_date
        while current <= end_date:
            needed_dates.add(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)

        # Dates we need to fetch
        missing_dates = needed_dates - existing_dates

        if not missing_dates:
            # Already have all 7 days
            stats['skipped'] += 1
            processed_ids.add(accident_id)
            stats['processed'] += 1
            pbar.set_postfix({
                'success': stats['success'],
                'partial': stats['partial'],
                'failed': stats['failed'],
                'skipped': stats['skipped']
            })
            continue

        # Fetch weather data for entire 7-day window
        daily_weather = fetch_weather_data_robust(
            latitude=lat,
            longitude=lon,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d')
        )

        if daily_weather:
            records_added = 0

            for day_record in daily_weather:
                # Only add if this date was missing
                if day_record['date'] in missing_dates:
                    record = {
                        'weather_id': next_weather_id,
                        'accident_id': accident_id,
                        'date': day_record['date'],
                        'latitude': day_record['latitude'],
                        'longitude': day_record['longitude'],
                        'temperature_avg': day_record['temperature_avg'],
                        'temperature_min': day_record['temperature_min'],
                        'temperature_max': day_record['temperature_max'],
                        'wind_speed_avg': day_record['wind_speed_avg'],
                        'wind_speed_max': day_record['wind_speed_max'],
                        'precipitation_total': day_record['precipitation_total'],
                        'visibility_avg': day_record['visibility_avg'],
                        'cloud_cover_avg': day_record['cloud_cover_avg']
                    }
                    weather_records.append(record)
                    next_weather_id += 1
                    records_added += 1

            stats['total_records_added'] += records_added

            # Check if we got all days
            days_retrieved = len(daily_weather)
            if days_retrieved == 7:
                stats['success'] += 1
            else:
                stats['partial'] += 1
        else:
            stats['failed'] += 1

        processed_ids.add(accident_id)
        stats['processed'] += 1

        # Update progress bar
        pbar.set_postfix({
            'success': stats['success'],
            'partial': stats['partial'],
            'failed': stats['failed'],
            'added': stats['total_records_added']
        })

        # Save progress incrementally
        if stats['processed'] % SAVE_INTERVAL == 0:
            # Save weather data
            weather_df = pd.DataFrame(weather_records)
            weather_df.to_csv(OUTPUT_FILE, index=False)

            # Save progress
            save_progress(processed_ids)

            # Update existing_weather_df for next iteration
            existing_weather_df = weather_df

        # Rate limiting
        time.sleep(RATE_LIMIT_SECONDS)

    pbar.close()

    # Final save
    print("\nSaving final data...")
    weather_df = pd.DataFrame(weather_records)
    weather_df.to_csv(OUTPUT_FILE, index=False)
    save_progress(processed_ids)

    # Remove progress file (completed)
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

    # Summary
    print("\n" + "=" * 80)
    print("BACKFILL COMPLETE")
    print("=" * 80)
    print(f"\nAccidents processed: {stats['processed']:,}")
    print(f"  ✅ Success (7 days): {stats['success']:,}")
    print(f"  🟨 Partial (<7 days): {stats['partial']:,}")
    print(f"  ❌ Failed (API error): {stats['failed']:,}")
    print(f"  ⏭️  Skipped (already complete): {stats['skipped']:,}")
    print(f"\nTotal weather records: {len(weather_records):,}")
    print(f"New records added: {stats['total_records_added']:,}")
    print(f"\n✅ Weather data saved to: {OUTPUT_FILE}")

    # Final coverage check
    final_df = pd.DataFrame(weather_records)
    accidents_with_weather = final_df[final_df['accident_id'].notna()].groupby('accident_id').size()
    full_week = (accidents_with_weather == 7).sum()
    partial_week = ((accidents_with_weather >= 5) & (accidents_with_weather < 7)).sum()
    insufficient = ((accidents_with_weather > 0) & (accidents_with_weather < 5)).sum()

    print("\n" + "=" * 80)
    print("FINAL COVERAGE REPORT")
    print("=" * 80)
    print(f"\nAccidents with full 7-day window: {full_week:,} ({full_week/total_accidents*100:.1f}%)")
    print(f"Accidents with 5-6 days (usable): {partial_week:,} ({partial_week/total_accidents*100:.1f}%)")
    print(f"Accidents with <5 days: {insufficient:,} ({insufficient/total_accidents*100:.1f}%)")


if __name__ == '__main__':
    try:
        backfill_weather_7day_windows()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user. Progress has been saved.")
        print("    Run this script again to resume from where you left off.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        print("    Progress has been saved. You can resume by running this script again.")
        raise
