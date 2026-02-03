#!/usr/bin/env python3
"""
Process Avalanche Accident Data
===============================
Cleans and standardizes avalanche accident data for SafeAscent database.

Input: data/avalanche_accidents.csv
Output: data/processed_avalanche_accidents.csv

The avalanche data is already well-structured with lat/lon, so this script
mainly standardizes the fields to match our database schema.
"""

import pandas as pd
from datetime import datetime
from pathlib import Path
import logging
import re

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def map_activity(activity_str, travel_activities_str):
    """Map avalanche activity types to SafeAscent categories."""
    if pd.isna(activity_str) and pd.isna(travel_activities_str):
        return "unknown"

    combined = f"{activity_str or ''} {travel_activities_str or ''}".lower()

    if 'ski' in combined or 'snowboard' in combined:
        return "backcountry_skiing"
    elif 'snowmobil' in combined or 'snowbik' in combined or 'motor' in combined:
        return "snowmobiling"
    elif 'climb' in combined or 'ice climb' in combined:
        return "ice_climbing"
    elif 'hik' in combined or 'snowshoe' in combined:
        return "hiking"
    else:
        return "backcountry_skiing"  # Default for avalanche incidents


def extract_severity(killed, buried, caught):
    """Determine accident severity from involvement data."""
    killed = int(killed) if pd.notna(killed) else 0
    buried = int(buried) if pd.notna(buried) else 0
    caught = int(caught) if pd.notna(caught) else 0

    if killed > 0:
        return "fatal"
    elif buried > 0:
        return "serious"
    elif caught > 0:
        return "minor"
    else:
        return "near_miss"


def clean_location_name(location):
    """Clean up location name."""
    if pd.isna(location):
        return None
    # Remove common suffixes/prefixes
    location = re.sub(r',\s*(east|west|north|south)\s+of\s+.*$', '', location, flags=re.IGNORECASE)
    return location.strip()


def process_avalanche_data(input_file, output_file):
    """Process avalanche accident data."""
    logger.info(f"Reading {input_file}")
    df = pd.read_csv(input_file)
    logger.info(f"Loaded {len(df)} records")

    # Filter to only approved/completed reports
    initial_count = len(df)
    df = df[df['status'] == 'approved']
    logger.info(f"Filtered to {len(df)} approved records (removed {initial_count - len(df)})")

    # Create processed dataframe
    processed = pd.DataFrame({
        'source_id': df['id'],
        'source': 'avalanche_org',
        'date': pd.to_datetime(df['observed_at']).dt.date,
        'state': df['state'],
        'latitude': df['latitude'],
        'longitude': df['longitude'],
        'location_name': df['location'].apply(clean_location_name),
        'activity': df.apply(lambda row: map_activity(row['activity'], row.get('travel_activities')), axis=1),
        'accident_type': 'avalanche',
        'severity': df.apply(lambda row: extract_severity(
            row['killed_count'], row['buried_count'], row.get('involved_count', 0)
        ), axis=1),
        'fatalities': df['killed_count'].fillna(0).astype(int),
        'injuries': (df['buried_count'].fillna(0) + df['involved_count'].fillna(0) - df['killed_count'].fillna(0)).clip(lower=0).astype(int),
        'description': df['accident_summary'],
        'elevation_ft': df['avalanche_elevation_feet'],
        'aspect': df['avalanche_aspect'],
        'angle': df['avalanche_angle_average'],
    })

    # Remove records with invalid coordinates
    valid_coords = (
        processed['latitude'].notna() &
        processed['longitude'].notna() &
        (processed['latitude'] != 0) &
        (processed['longitude'] != 0) &
        (processed['latitude'].between(18, 72)) &  # Valid US latitudes
        (processed['longitude'].between(-180, -60))  # Valid US longitudes
    )
    removed = len(processed) - valid_coords.sum()
    processed = processed[valid_coords]
    logger.info(f"Removed {removed} records with invalid coordinates, {len(processed)} remaining")

    # Remove duplicates
    before_dedup = len(processed)
    processed = processed.drop_duplicates(subset=['source_id'])
    logger.info(f"Removed {before_dedup - len(processed)} duplicates")

    # Sort by date
    processed = processed.sort_values('date', ascending=False)

    # Save
    processed.to_csv(output_file, index=False)
    logger.info(f"Saved {len(processed)} processed records to {output_file}")

    # Print statistics
    print("\n=== Processing Statistics ===")
    print(f"Total records: {len(processed)}")
    print(f"\nBy severity:")
    print(processed['severity'].value_counts().to_string())
    print(f"\nBy activity:")
    print(processed['activity'].value_counts().to_string())
    print(f"\nBy state (top 10):")
    print(processed['state'].value_counts().head(10).to_string())
    print(f"\nDate range: {processed['date'].min()} to {processed['date'].max()}")
    print(f"Total fatalities: {processed['fatalities'].sum()}")

    return processed


if __name__ == "__main__":
    input_file = Path("data/avalanche_accidents.csv")
    output_file = Path("data/processed_avalanche_accidents.csv")

    if not input_file.exists():
        logger.error(f"Input file not found: {input_file}")
        exit(1)

    process_avalanche_data(input_file, output_file)
