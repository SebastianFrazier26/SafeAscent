#!/usr/bin/env python3
"""
Process NPS (National Park Service) Mortality Data
===================================================
Filters and processes NPS mortality data for SafeAscent database.

Input: data/nps_mortality.xlsx
Output: data/processed_nps_accidents.csv

Filtering:
- Include: Hiking, Climbing, Skiing, Walking, Camping activities
- Include: Fall, Avalanche, Hypothermia, Lightning, Rock fall causes
- Exclude: Suicide, Homicide, Driving, Vessel, Illegal activity
"""

import pandas as pd
from pathlib import Path
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Activities to include (mountaineering/outdoor related)
INCLUDE_ACTIVITIES = {
    'Hiking', 'Climbing', 'Skiing', 'Walking', 'Camping',
    'Mountaineering', 'Snowshoeing', 'Ice Climbing', 'Backpacking',
    'Rock Climbing', 'Scrambling', 'Trail Running'
}

# Activities to explicitly exclude
EXCLUDE_ACTIVITIES = {
    'Suicide', 'Homicide', 'Driving', 'Vessel Related',
    'Illegal activity', 'Flying', 'Diving - Scuba', 'Swimming',
    'Bicycling', 'Sitting', 'Sleeping', 'Photographing',
    'Fishing', 'Snorkeling', 'Other', 'Not Reported'
}

# Causes of death relevant to mountaineering
RELEVANT_CAUSES = {
    'Fall', 'Avalanche', 'Hypothermia', 'Exposure', 'Lightning',
    'Rock fall', 'Rockfall', 'Ice fall', 'Icefall', 'Crevasse',
    'Drowning',  # Can occur during river crossings
    'Heart Attack', 'Cardiac',  # Common on strenuous hikes
    'Medical', 'Medical Emergency',
    'Environmental', 'Weather',
}

# Park coordinates (major climbing/hiking parks)
PARK_COORDINATES = {
    'Mount Rainier National Park': (46.8800, -121.7269),
    'Yosemite National Park': (37.8651, -119.5383),
    'Rocky Mountain National Park': (40.3428, -105.6836),
    'Grand Teton National Park': (43.7904, -110.6818),
    'Denali National Park and Preserve': (63.1148, -151.1926),
    'Glacier National Park': (48.7596, -113.7870),
    'Zion National Park': (37.2982, -113.0263),
    'Grand Canyon National Park': (36.1070, -112.1130),
    'Yellowstone National Park': (44.4280, -110.5885),
    'Olympic National Park': (47.8021, -123.6044),
    'North Cascades National Park': (48.7718, -121.2985),
    'Sequoia National Park': (36.4864, -118.5658),
    'Kings Canyon National Park': (36.8879, -118.5551),
    'Joshua Tree National Park': (33.8734, -115.9010),
    'Great Smoky Mountains National Park': (35.6532, -83.5070),
    'Acadia National Park': (44.3386, -68.2733),
    'Shenandoah National Park': (38.2928, -78.6796),
    'Black Canyon Of The Gunnison National Park': (38.5754, -107.7416),
    'Capitol Reef National Park': (38.2830, -111.2473),
    'Arches National Park': (38.7331, -109.5925),
    'Canyonlands National Park': (38.3269, -109.8783),
    'Bryce Canyon National Park': (37.5930, -112.1871),
    'Mesa Verde National Park': (37.1853, -108.4862),
    'Crater Lake National Park': (42.8684, -122.1685),
    'Lassen Volcanic National Park': (40.4977, -121.5078),
    'Big Bend National Park': (29.2500, -103.2500),
    'Guadalupe Mountains National Park': (31.8913, -104.8609),
    'Haleakala National Park': (20.7204, -156.1552),
    'Hawaii Volcanoes National Park': (19.4194, -155.2885),
    'Carlsbad Caverns National Park': (32.1479, -104.5567),
    'Death Valley National Park': (36.5054, -117.0794),
    'White Sands National Park': (32.7872, -106.3257),
    'Petrified Forest National Park': (34.9100, -109.8068),
    'Badlands National Park': (43.8554, -102.3397),
    'Theodore Roosevelt National Park': (46.9790, -103.5387),
    'Voyageurs National Park': (48.5000, -93.0000),
    'Isle Royale National Park': (48.0000, -88.9000),
    'Mammoth Cave National Park': (37.1870, -86.1005),
    'Everglades National Park': (25.2866, -80.8987),
    'Biscayne National Park': (25.4824, -80.2083),
    'Dry Tortugas National Park': (24.6285, -82.8732),
    'Virgin Islands National Park': (18.3358, -64.7505),
    'Hot Springs National Park': (34.5217, -93.0424),
    'Congaree National Park': (33.7948, -80.7821),
    'Cuyahoga Valley National Park': (41.2808, -81.5678),
    'Saguaro National Park': (32.2967, -111.1666),
    'Pinnacles National Park': (36.4906, -121.1825),
    'Redwood National Park': (41.2132, -124.0046),
    'Channel Islands National Park': (34.0069, -119.7785),
    'Wrangell - St Elias National Park and Preserve': (61.4182, -142.6028),
    'Gates Of The Arctic National Park and Preserve': (67.7863, -153.3018),
    'Katmai National Park and Preserve': (58.5970, -155.0631),
    'Lake Clark National Park and Preserve': (60.4127, -153.4240),
    'Kenai Fjords National Park': (59.9227, -149.6525),
    'Kobuk Valley National Park': (67.3556, -159.2847),
    'Glacier Bay National Park and Preserve': (58.5000, -136.0000),
    # Recreation Areas
    'Glen Canyon National Recreation Area': (37.0683, -111.2433),
    'Golden Gate National Recreation Area': (37.8199, -122.4783),
    'Gateway National Recreation Area': (40.5684, -73.9293),
    'Lake Mead National Recreation Area': (36.0159, -114.7378),
    'Delaware Water Gap National Recreation Area': (41.0872, -74.9593),
    'Big South Fork National River and Recreation Area': (36.4983, -84.6989),
    'Chattahoochee River National Recreation Area': (33.9939, -84.4122),
    'Chickasaw National Recreation Area': (34.4573, -97.0128),
    'Curecanti National Recreation Area': (38.4540, -107.3289),
    'Santa Monica Mountains National Recreation Area': (34.1000, -118.7500),
    'Whiskeytown National Recreation Area': (40.6170, -122.5397),
    # Parkways and other units
    'Natchez Trace Parkway': (34.2551, -88.7034),
    'Blue Ridge Parkway': (35.7796, -82.2655),
    'Colorado National Monument': (39.0367, -108.7319),
    'Devils Tower National Monument': (44.5902, -104.7146),
}


def get_park_coordinates(park_name):
    """Get coordinates for a park, or None if not found."""
    # Direct match
    if park_name in PARK_COORDINATES:
        return PARK_COORDINATES[park_name]

    # Try partial match
    for known_park, coords in PARK_COORDINATES.items():
        if known_park.lower() in park_name.lower() or park_name.lower() in known_park.lower():
            return coords

    return (None, None)


def map_cause_to_type(cause):
    """Map NPS cause of death to accident type."""
    if pd.isna(cause):
        return 'unknown'

    cause_lower = cause.lower()

    if 'fall' in cause_lower:
        return 'fall'
    elif 'avalanche' in cause_lower:
        return 'avalanche'
    elif 'hypothermia' in cause_lower or 'exposure' in cause_lower:
        return 'exposure'
    elif 'lightning' in cause_lower:
        return 'lightning'
    elif 'rock' in cause_lower or 'ice' in cause_lower:
        return 'rockfall'
    elif 'drown' in cause_lower:
        return 'drowning'
    elif 'heart' in cause_lower or 'cardiac' in cause_lower:
        return 'medical'
    elif 'medical' in cause_lower:
        return 'medical'
    else:
        return 'other'


def map_activity(activity):
    """Map NPS activity to SafeAscent categories."""
    if pd.isna(activity):
        return 'unknown'

    activity_lower = activity.lower()

    if 'climb' in activity_lower:
        return 'rock_climbing'
    elif 'ski' in activity_lower:
        return 'backcountry_skiing'
    elif 'hik' in activity_lower or 'walk' in activity_lower or 'backpack' in activity_lower:
        return 'hiking'
    elif 'camp' in activity_lower:
        return 'camping'
    elif 'mountain' in activity_lower:
        return 'mountaineering'
    else:
        return 'hiking'  # Default


def process_nps_data(input_file, output_file):
    """Process NPS mortality data."""
    logger.info(f"Reading {input_file}")
    df = pd.read_excel(input_file, sheet_name='CY2007-Present Q2')
    logger.info(f"Loaded {len(df)} records")

    # Clean column names
    df.columns = [col.strip().replace('\n', ' ') for col in df.columns]

    # Filter to included activities
    initial_count = len(df)
    activity_mask = df['Activity'].apply(
        lambda x: any(inc.lower() in str(x).lower() for inc in INCLUDE_ACTIVITIES) if pd.notna(x) else False
    )
    df_filtered = df[activity_mask]
    logger.info(f"Filtered to {len(df_filtered)} records with relevant activities (from {initial_count})")

    # Exclude explicit exclusions
    exclude_mask = df_filtered['Activity'].apply(
        lambda x: any(exc.lower() in str(x).lower() for exc in EXCLUDE_ACTIVITIES) if pd.notna(x) else False
    )
    df_filtered = df_filtered[~exclude_mask]
    logger.info(f"After exclusions: {len(df_filtered)} records")

    # Get coordinates for parks
    df_filtered = df_filtered.copy()
    coords = df_filtered['Park Name'].apply(get_park_coordinates)
    df_filtered['latitude'] = coords.apply(lambda x: x[0])
    df_filtered['longitude'] = coords.apply(lambda x: x[1])

    # Count parks without coordinates
    missing_coords = df_filtered['latitude'].isna().sum()
    if missing_coords > 0:
        missing_parks = df_filtered[df_filtered['latitude'].isna()]['Park Name'].unique()
        logger.warning(f"{missing_coords} records missing coordinates. Parks: {list(missing_parks)[:10]}...")

    # Create processed dataframe
    processed = pd.DataFrame({
        'source_id': range(1, len(df_filtered) + 1),
        'source': 'nps',
        'date': pd.to_datetime(df_filtered['Incident Date']).dt.date,
        'state': None,  # Will be derived from park or coordinates
        'latitude': df_filtered['latitude'],
        'longitude': df_filtered['longitude'],
        'location_name': df_filtered['Park Name'],
        'activity': df_filtered['Activity'].apply(map_activity),
        'accident_type': df_filtered['Cause of Death'].apply(map_cause_to_type),
        'severity': 'fatal',  # All NPS data is mortality data
        'fatalities': 1,
        'injuries': 0,
        'description': df_filtered.apply(
            lambda row: f"{row['Activity']} - {row['Cause of Death']}" if pd.notna(row['Activity']) else str(row['Cause of Death']),
            axis=1
        ),
        'elevation_ft': None,
        'aspect': None,
        'angle': None,
    })

    # Remove records without coordinates
    processed_with_coords = processed[processed['latitude'].notna()].copy()
    logger.info(f"Records with coordinates: {len(processed_with_coords)}")

    # Sort by date
    processed_with_coords = processed_with_coords.sort_values('date', ascending=False)

    # Save both versions
    processed.to_csv(output_file, index=False)
    logger.info(f"Saved {len(processed)} processed records to {output_file}")

    # Save version with coordinates only
    coords_file = output_file.replace('.csv', '_geocoded.csv')
    processed_with_coords.to_csv(coords_file, index=False)
    logger.info(f"Saved {len(processed_with_coords)} geocoded records to {coords_file}")

    # Print statistics
    print("\n=== Processing Statistics ===")
    print(f"Total records: {len(processed)}")
    print(f"With coordinates: {len(processed_with_coords)}")
    print(f"\nBy accident type:")
    print(processed['accident_type'].value_counts().to_string())
    print(f"\nBy activity:")
    print(processed['activity'].value_counts().to_string())
    print(f"\nBy park (top 15):")
    print(processed['location_name'].value_counts().head(15).to_string())
    print(f"\nDate range: {processed['date'].min()} to {processed['date'].max()}")

    return processed


if __name__ == "__main__":
    input_file = Path("data/nps_mortality.xlsx")
    output_file = Path("data/processed_nps_accidents.csv")

    if not input_file.exists():
        logger.error(f"Input file not found: {input_file}")
        exit(1)

    process_nps_data(str(input_file), str(output_file))
