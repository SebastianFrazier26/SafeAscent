"""
Monitor Weather Data Collection Progress

Quick script to check current status of weather data collection.
"""

import pandas as pd
import os
from datetime import datetime

def monitor_progress():
    """Show current progress of weather collection"""

    weather_file = 'data/tables/weather.csv'

    if not os.path.exists(weather_file):
        print("❌ Weather collection not started yet (no weather.csv file)")
        return

    # Load current data
    df = pd.read_csv(weather_file)

    print("\n" + "=" * 80)
    print("WEATHER DATA COLLECTION - PROGRESS")
    print("=" * 80)

    # Basic stats
    print(f"\n📊 Current Statistics:")
    print(f"  Total weather records: {len(df):,}")
    print(f"  Records with accident link: {df['accident_id'].notna().sum():,}")
    print(f"  Baseline records: {df['accident_id'].isna().sum():,}")

    # Date range
    print(f"\n📅 Date Coverage:")
    print(f"  Earliest: {df['date'].min()}")
    print(f"  Latest: {df['date'].max()}")

    # Location coverage
    unique_locations = df.groupby(['latitude', 'longitude']).size()
    print(f"\n📍 Geographic Coverage:")
    print(f"  Unique locations: {len(unique_locations):,}")
    print(f"  Latitude range: {df['latitude'].min():.2f}° to {df['latitude'].max():.2f}°")
    print(f"  Longitude range: {df['longitude'].min():.2f}° to {df['longitude'].max():.2f}°")

    # Data quality
    print(f"\n✅ Data Completeness:")
    print(f"  Temperature: {df['temperature_avg'].notna().sum()/len(df)*100:.1f}%")
    print(f"  Wind speed: {df['wind_speed_avg'].notna().sum()/len(df)*100:.1f}%")
    print(f"  Precipitation: {df['precipitation_total'].notna().sum()/len(df)*100:.1f}%")
    print(f"  Cloud cover: {df['cloud_cover_avg'].notna().sum()/len(df)*100:.1f}%")

    # Estimate completion
    target_records = 25564  # From initial estimate
    if len(df) < target_records:
        progress_pct = (len(df) / target_records) * 100
        remaining = target_records - len(df)
        print(f"\n⏱️  Progress:")
        print(f"  Completed: {progress_pct:.1f}%")
        print(f"  Remaining: ~{remaining:,} records")
        print(f"  Estimated weeks left: ~{remaining // 7:,}")
    else:
        print(f"\n✅ Collection appears complete!")

    print("\n" + "=" * 80)

    # Show recent records
    print("\n📝 Most recent records:")
    print(df.tail(5)[['weather_id', 'accident_id', 'date', 'latitude', 'longitude',
                       'temperature_avg', 'wind_speed_avg', 'precipitation_total']])

    print(f"\n⏰ Last checked: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == '__main__':
    monitor_progress()
