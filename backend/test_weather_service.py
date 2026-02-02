#!/usr/bin/env python3
"""
Quick test of weather service functions
"""
import sys
from datetime import date
sys.path.insert(0, '/Users/sebastianfrazier/SafeAscent/backend')

from app.services.weather_service import fetch_current_weather_pattern, fetch_weather_statistics

# Test 1: Fetch current weather pattern
print("=" * 60)
print("TEST 1: Fetch Current Weather Pattern")
print("=" * 60)
weather = fetch_current_weather_pattern(
    latitude=40.2549,
    longitude=-105.6426,
    target_date=date(2026, 1, 29),
)

if weather:
    print(f"✓ Weather fetched successfully!")
    print(f"  Temperature: {weather.temperature}")
    print(f"  Precipitation: {weather.precipitation}")
    print(f"  Wind speed: {weather.wind_speed}")
    print(f"  Cloud cover: {weather.cloud_cover}")
else:
    print("✗ Failed to fetch weather")

print()

# Test 2: Fetch weather statistics
print("=" * 60)
print("TEST 2: Fetch Weather Statistics")
print("=" * 60)
stats = fetch_weather_statistics(
    latitude=40.2549,
    longitude=-105.6426,
    elevation_meters=3000.0,
    season="winter",
)

if stats:
    print(f"✓ Statistics fetched successfully!")
    print(f"  Temperature: {stats['temperature'][0]:.1f}°C (±{stats['temperature'][1]:.1f}°C)")
    print(f"  Precipitation: {stats['precipitation'][0]:.1f}mm (±{stats['precipitation'][1]:.1f}mm)")
    print(f"  Wind speed: {stats['wind_speed'][0]:.1f}m/s (±{stats['wind_speed'][1]:.1f}m/s)")
    print(f"  Sample count: {stats['sample_count']}")
else:
    print("✗ Failed to fetch statistics")

print()
print("=" * 60)
