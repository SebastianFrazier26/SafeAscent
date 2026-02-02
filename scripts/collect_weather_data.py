"""
Weather Data Collection for SafeAscent

Collects historical weather data for entire weeks during which accidents occurred.
This avoids sampling bias by including baseline weather data, not just accident days.

Data Source: Open-Meteo Historical Weather API (1940-present, free, no API key)
API Docs: https://open-meteo.com/en/docs/historical-weather-api
"""

import pandas as pd
import requests
from datetime import datetime, timedelta
import time
from tqdm import tqdm
import os

def get_week_dates(date_str):
    """
    Given a date, return the Monday-Sunday range for that week.

    Args:
        date_str: Date string in format 'YYYY-MM-DD'

    Returns:
        tuple: (monday_date, sunday_date) as datetime objects
    """
    date = datetime.strptime(date_str, '%Y-%m-%d')

    # Find Monday of that week (weekday 0 = Monday)
    monday = date - timedelta(days=date.weekday())
    sunday = monday + timedelta(days=6)

    return monday, sunday

def fetch_weather_data(latitude, longitude, start_date, end_date):
    """
    Fetch weather data from Open-Meteo Historical Weather API.

    Args:
        latitude: Location latitude
        longitude: Location longitude
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)

    Returns:
        list: Weather records (one per day) or None if API fails
    """
    # Open-Meteo Historical Weather API
    url = "https://archive-api.open-meteo.com/v1/archive"

    params = {
        'latitude': latitude,
        'longitude': longitude,
        'start_date': start_date,
        'end_date': end_date,
        'hourly': 'temperature_2m,wind_speed_10m,precipitation,visibility,cloud_cover',
        'timezone': 'auto'
    }

    try:
        response = requests.get(url, params=params, timeout=30)
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

        # Group by date and create daily summaries
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

            # Collect hourly values
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

        # Calculate daily averages/totals
        weather_records = []
        for date_key, values in daily_records.items():
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

    except requests.exceptions.RequestException as e:
        print(f"\nAPI Error: {e}")
        return None
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        return None

def collect_accident_week_weather(output_file='data/tables/weather.csv', limit=None):
    """
    Collect weather data for all weeks when accidents occurred.

    Args:
        output_file: Path to save weather CSV
        limit: Optional limit on number of accidents to process (for testing)
    """
    print("\n" + "=" * 80)
    print("WEATHER DATA COLLECTION - ACCIDENT WEEKS")
    print("=" * 80)

    # Load accidents data
    accidents_df = pd.read_csv('data/tables/accidents.csv')

    # Filter to accidents with both date and coordinates
    valid_accidents = accidents_df[
        accidents_df['date'].notna() &
        accidents_df['latitude'].notna() &
        accidents_df['longitude'].notna()
    ].copy()

    print(f"\nTotal accidents: {len(accidents_df):,}")
    print(f"Accidents with date + coordinates: {len(valid_accidents):,}")

    if limit:
        valid_accidents = valid_accidents.head(limit)
        print(f"Processing: {limit} accidents (testing mode)")

    # Identify unique week + location combinations
    print("\nIdentifying unique weeks and locations...")

    week_locations = set()
    accident_weeks = []

    for idx, accident in valid_accidents.iterrows():
        date_str = accident['date']
        lat = round(accident['latitude'], 2)  # Round to ~1km precision
        lon = round(accident['longitude'], 2)

        # Get week range
        monday, sunday = get_week_dates(date_str)

        # Create unique key for this week+location
        week_key = (monday.strftime('%Y-%m-%d'), lat, lon)

        if week_key not in week_locations:
            week_locations.add(week_key)
            accident_weeks.append({
                'week_start': monday.strftime('%Y-%m-%d'),
                'week_end': sunday.strftime('%Y-%m-%d'),
                'latitude': lat,
                'longitude': lon,
                'accident_ids': [accident['accident_id']]
            })
        else:
            # Add accident ID to existing week
            for week in accident_weeks:
                if (week['week_start'], week['latitude'], week['longitude']) == week_key:
                    week['accident_ids'].append(accident['accident_id'])
                    break

    print(f"Unique week+location combinations: {len(accident_weeks):,}")

    # Estimate API calls and time
    total_days = len(accident_weeks) * 7  # 7 days per week
    estimated_time_seconds = len(accident_weeks) * 2  # ~2 seconds per API call
    estimated_minutes = estimated_time_seconds / 60

    print(f"Expected weather records: ~{total_days:,} (7 days × {len(accident_weeks):,} weeks)")
    print(f"Estimated time: ~{estimated_minutes:.1f} minutes")
    print(f"API rate: 1 request per 2 seconds (respectful)")

    # Check if output file exists
    if os.path.exists(output_file):
        print(f"\n⚠️  Warning: {output_file} already exists")
        existing_df = pd.read_csv(output_file)
        print(f"Existing records: {len(existing_df):,}")
        response = input("Append to existing file? (y/n): ")
        if response.lower() != 'y':
            print("Aborted.")
            return
        weather_records = existing_df.to_dict('records')
        next_weather_id = existing_df['weather_id'].max() + 1
    else:
        weather_records = []
        next_weather_id = 1

    # Collect weather data
    print("\n" + "=" * 80)
    print("COLLECTING WEATHER DATA")
    print("=" * 80 + "\n")

    successful_weeks = 0
    failed_weeks = 0

    pbar = tqdm(accident_weeks, desc="Fetching weather")

    for week_data in pbar:
        week_str = week_data['week_start'][:10]
        pbar.set_description(f"Week of {week_str}")

        # Fetch weather for entire week
        daily_weather = fetch_weather_data(
            latitude=week_data['latitude'],
            longitude=week_data['longitude'],
            start_date=week_data['week_start'],
            end_date=week_data['week_end']
        )

        if daily_weather:
            # Add weather records with IDs
            for day_record in daily_weather:
                # Check which accidents (if any) occurred on this specific date
                matching_accidents = valid_accidents[
                    (valid_accidents['date'] == day_record['date']) &
                    (round(valid_accidents['latitude'], 2) == week_data['latitude']) &
                    (round(valid_accidents['longitude'], 2) == week_data['longitude'])
                ]

                # If multiple accidents on same day/location, create separate records
                if len(matching_accidents) > 0:
                    for _, accident in matching_accidents.iterrows():
                        record = {
                            'weather_id': next_weather_id,
                            'accident_id': accident['accident_id'],
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
                else:
                    # Baseline weather (no accident on this day)
                    record = {
                        'weather_id': next_weather_id,
                        'accident_id': None,  # No specific accident
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

            successful_weeks += 1
        else:
            failed_weeks += 1

        pbar.set_postfix({'success': successful_weeks, 'failed': failed_weeks})

        # Save progress every 50 weeks
        if successful_weeks % 50 == 0:
            weather_df = pd.DataFrame(weather_records)
            weather_df.to_csv(output_file, index=False)

        # Respectful delay
        time.sleep(2)

    pbar.close()

    # Final save
    weather_df = pd.DataFrame(weather_records)
    weather_df.to_csv(output_file, index=False)

    # Summary statistics
    print("\n" + "=" * 80)
    print("COLLECTION COMPLETE")
    print("=" * 80)
    print(f"\nWeeks processed: {len(accident_weeks):,}")
    print(f"Successful API calls: {successful_weeks:,}")
    print(f"Failed API calls: {failed_weeks:,}")
    print(f"\nTotal weather records: {len(weather_records):,}")
    print(f"Records with accident link: {weather_df['accident_id'].notna().sum():,}")
    print(f"Baseline records (no accident): {weather_df['accident_id'].isna().sum():,}")

    # Data quality
    print(f"\nData completeness:")
    print(f"  Temperature: {weather_df['temperature_avg'].notna().sum()/len(weather_df)*100:.1f}%")
    print(f"  Wind speed: {weather_df['wind_speed_avg'].notna().sum()/len(weather_df)*100:.1f}%")
    print(f"  Precipitation: {weather_df['precipitation_total'].notna().sum()/len(weather_df)*100:.1f}%")
    print(f"  Visibility: {weather_df['visibility_avg'].notna().sum()/len(weather_df)*100:.1f}%")
    print(f"  Cloud cover: {weather_df['cloud_cover_avg'].notna().sum()/len(weather_df)*100:.1f}%")

    print(f"\n✅ Weather data saved to: {output_file}")

    # Show sample
    print("\nSample weather records:")
    print(weather_df.head(10).to_string())

if __name__ == '__main__':
    import sys

    # Check for test mode
    if len(sys.argv) > 1 and sys.argv[1] == '--test':
        print("\n🧪 TEST MODE: Processing first 10 accidents only")
        collect_accident_week_weather(limit=10)
    else:
        collect_accident_week_weather()
