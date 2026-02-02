"""
Enhanced Location and Coordinate Extraction
- Extract missing locations from accident descriptions
- Fix questionable location data
- Add latitude/longitude for all records using mountain/park coordinates
"""

import pandas as pd
import numpy as np
import re

# Comprehensive mountain and climbing area coordinates
MOUNTAIN_COORDINATES = {
    # Alaska
    'McKinley': (63.0695, -151.0074),
    'Denali': (63.0695, -151.0074),
    'Foraker': (62.9604, -151.3998),
    'Hunter': (62.9804, -151.0984),
    'Russell': (61.4500, -148.3667),
    'Sanford': (62.2144, -144.1283),
    'Blackburn': (61.7333, -143.4167),

    # Washington
    'Rainier': (46.8523, -121.7603),
    'Baker': (48.7767, -121.8144),
    'Adams': (46.2024, -121.4909),
    'Glacier Peak': (48.1122, -121.1131),
    'Stuart': (47.4753, -120.9025),
    'Shuksan': (48.8098, -121.6065),
    'Olympus': (47.8013, -123.7108),
    'Index': (47.8215, -121.5562),
    'Washington': (44.2705, -71.3033),  # NH

    # California
    'Whitney': (36.5785, -118.2923),
    'Shasta': (41.4093, -122.1949),
    'Muir': (37.2151, -118.5437),
    'Williamson': (36.6560, -118.3106),

    # Colorado
    'Longs': (40.2549, -105.6151),
    'Elbert': (39.1178, -106.4454),
    'Massive': (39.1875, -106.4757),
    'Capitol': (39.1502, -107.0831),
    'Maroon': (39.0708, -106.9890),
    'Sneffels': (38.0038, -107.7923),
    'Crestone': (37.9669, -105.5853),

    # Wyoming
    'Grand Teton': (43.7411, -110.8024),
    'Teewinot': (43.7763, -110.8219),
    'Owen': (43.7429, -110.7958),
    'Gannett': (43.1842, -109.6542),

    # Oregon
    'Hood': (45.3736, -121.6960),
    'Jefferson': (44.6744, -121.7990),
    'Three Fingered Jack': (44.4915, -121.8431),
    'South Sister': (44.1034, -121.7690),
    'North Sister': (44.1687, -121.7697),

    # Montana
    'Granite': (46.8306, -113.4051),

    # Utah
    'Kings': (40.6333, -111.7833),

    # Canadian Rockies
    'Assiniboine': (50.8667, -115.6500),
    'Robson': (53.1097, -119.1564),
    'Temple': (51.3500, -116.2167),
    'Athabasca': (52.6942, -117.2133),
    'Rundle': (51.1333, -115.4333),
    'Yamnuska': (51.1300, -114.9800),
    'Edith Cavell': (52.7058, -118.0569),
    'Victoria': (51.3994, -116.3186),
    'Andromeda': (52.2000, -117.2833),
    'Columbia': (52.1472, -117.4411),
    'Kitchener': (52.6333, -117.7833),
    'Fay': (51.3333, -116.2667),
    'Alberta': (52.1167, -117.2833),
    'Lefroy': (51.3667, -116.3333),
    'Stephen': (51.3167, -116.4500),
    'Deltaform': (51.3167, -116.2000),
    'Hungabee': (51.3167, -116.2667),
    'Brussels': (51.3167, -116.1833),
    'Howse': (51.7833, -116.9667),
    'Forbes': (51.8333, -116.9500),
    'Lyell': (51.6667, -116.5333),
    'Clemenceau': (52.1333, -117.6500),
    'Goodsir': (51.2333, -116.4833),
    'Chephren': (51.6833, -116.5000),
    'Murchison': (51.9167, -116.7833),
    'Sarbach': (51.8667, -116.5833),
    'Wilson': (51.4500, -116.0667),
    'Deltaform': (51.3167, -116.2000),
    'Neptuak': (51.3167, -116.2333),
    'Babel': (51.2667, -116.1833),
    'Tuzo': (51.3167, -116.2000),
    'Deltaform': (51.3167, -116.2000),
    'Quadra': (51.2667, -116.4333),
    'Balfour': (51.5667, -116.4333),
    'Huber': (51.5000, -116.4333),
    'des Poilus': (51.7667, -116.7167),
    'Deltaform': (51.3167, -116.2000),
    'Hungabee': (51.3167, -116.2667),
}

# Park and area coordinates
PARK_COORDINATES = {
    # US National Parks
    'Yosemite National Park': (37.8651, -119.5383),
    'Rocky Mountain National Park': (40.3428, -105.6836),
    'Denali National Park': (63.1148, -151.1926),
    'Mount Rainier National Park': (46.8800, -121.7269),
    'Grand Teton National Park': (43.7904, -110.6818),
    'Glacier National Park': (48.7596, -113.7870),
    'North Cascades National Park': (48.7718, -121.2985),
    'Sequoia National Park': (36.4864, -118.5658),
    'Olympic National Park': (47.8021, -123.6044),
    'Zion National Park': (37.2982, -113.0263),
    'Joshua Tree National Monument': (33.8734, -115.9010),
    'Baxter State Park': (45.9187, -68.8597),
    'Adirondack Park': (43.8654, -74.3576),

    # Canadian Parks
    'Banff National Park': (51.4968, -115.9281),
    'Jasper National Park': (52.8734, -117.9543),
    'Yoho National Park': (51.4500, -116.5000),
    'Kootenay National Park': (50.7333, -116.0500),

    # Climbing Areas
    'Eldorado Canyon': (39.9311, -105.2928),
    'Boulder Canyon': (40.0150, -105.4050),
    'Clear Creek Canyon': (39.7528, -105.3614),
    'Red Rock Canyon': (36.1357, -115.4274),
    'City of Rocks': (42.0667, -113.7167),
    'Devils Tower': (44.5902, -104.7147),
    'Seneca Rocks': (38.8346, -79.3764),
    'New River Gorge': (38.0731, -81.0779),
    'Gunks': (41.7333, -74.1833),
    'Tahquitz Rock': (33.7500, -116.7333),
    'Suicide Rock': (33.7500, -116.7500),
    'Lovers Leap': (38.8167, -120.1167),
    'Tuolumne Meadows': (37.8747, -119.3506),
    'Indian Creek': (38.0333, -109.5333),
    'Moab': (38.5733, -109.5498),
    'Smith Rock': (44.3672, -121.1406),
    'Squamish': (49.7016, -123.1558),
    'Bugaboos': (50.7333, -116.7500),
    'Cirque of the Unclimbables': (61.5667, -127.5333),
}

# State/region default coordinates (fallback)
STATE_COORDINATES = {
    'Alaska': (64.2008, -149.4937),
    'Washington': (47.7511, -120.7401),
    'California': (36.7783, -119.4179),
    'Colorado': (39.5501, -105.7821),
    'Wyoming': (43.0760, -107.2903),
    'Oregon': (43.8041, -120.5542),
    'Montana': (46.8797, -110.3626),
    'Utah': (39.3210, -111.0937),
    'Idaho': (44.0682, -114.7420),
    'New Hampshire': (43.1939, -71.5724),
    'Vermont': (44.5588, -72.5778),
    'New York': (43.2994, -74.2179),
    'Maine': (45.2538, -69.4455),
    'North Carolina': (35.7596, -79.0193),
    'South Dakota': (43.9695, -99.9018),
    'Nevada': (38.8026, -116.4194),
    'Arizona': (34.0489, -111.0937),
    'New Mexico': (34.5199, -105.8701),
    'Alberta': (53.9333, -116.5765),
    'British Columbia': (53.7267, -127.6476),
    'Quebec': (52.9399, -73.5491),
    'Ontario': (51.2538, -85.3232),
}

def extract_location_from_description(description, title=''):
    """Extract location/park from accident description"""
    if pd.isna(description):
        return None

    text = f"{title} {description}" if title else description

    # National Parks
    park_patterns = [
        r'([\w\s]+National Park)',
        r'([\w\s]+State Park)',
        r'([\w\s]+National Monument)',
        r'([\w\s]+National Forest)',
        r'([\w\s]+Wilderness)',
        r'([\w\s]+Provincial Park)',
    ]

    for pattern in park_patterns:
        match = re.search(pattern, text[:500])
        if match:
            park = match.group(1).strip()
            # Validate it's not too generic
            if len(park) > 5 and park.split()[0] not in ['the', 'this', 'that']:
                return park

    # Specific climbing areas
    areas = [
        'Eldorado Canyon', 'Boulder Canyon', 'Clear Creek Canyon',
        'Red Rock Canyon', 'Joshua Tree', 'City of Rocks',
        'Seneca Rocks', 'New River Gorge', 'Shawangunks', 'Gunks',
        'Tahquitz', 'Suicide Rock', 'Lovers Leap', 'Tuolumne',
        'Indian Creek', 'Smith Rock', 'Squamish', 'Bugaboos',
        'Adirondacks', 'White Mountains', 'Cascades', 'Sierra Nevada',
        'Rocky Mountains', 'Coast Mountains', 'Tetons', 'Sawtooths',
        'Glacier', 'Icicle Creek', 'Leavenworth', 'Index Town Wall',
    ]

    for area in areas:
        if area in text[:500]:
            return area

    return None

def get_coordinates_for_record(row):
    """Get best available coordinates for a record"""
    # Priority 1: If already has coordinates, keep them (avalanche data)
    if pd.notna(row['latitude']) and pd.notna(row['longitude']):
        return row['latitude'], row['longitude']

    # Priority 2: Mountain name
    if pd.notna(row['mountain']):
        mountain = row['mountain']
        if mountain in MOUNTAIN_COORDINATES:
            return MOUNTAIN_COORDINATES[mountain]

    # Priority 3: Location (park/area)
    if pd.notna(row['location']):
        location = row['location']
        # Try exact match
        if location in PARK_COORDINATES:
            return PARK_COORDINATES[location]
        # Try partial match
        for park_name, coords in PARK_COORDINATES.items():
            if park_name.lower() in location.lower() or location.lower() in park_name.lower():
                return coords

    # Priority 4: State/Province
    if pd.notna(row['state']):
        state = row['state']
        if state in STATE_COORDINATES:
            return STATE_COORDINATES[state]

    return None, None

def clean_questionable_location(location_text):
    """Clean up questionable location strings"""
    if pd.isna(location_text):
        return None

    # If it's a full sentence or contains verbs, it's not a location
    if any(word in location_text.lower() for word in [' was ', ' were ', ' to the ', ' at the ', ' from the ']):
        # Try to extract just the location part
        # Example: "at the base of Liberty Wall" -> extract park name instead
        return None

    # If it's too short, remove it
    if len(location_text) < 5:
        return None

    return location_text

def main():
    print("=" * 80)
    print("LOCATION & COORDINATE ENHANCEMENT")
    print("=" * 80)

    # Load data
    df = pd.read_csv('data/accidents.csv')
    aac_df = pd.read_excel('data/aac_accidents.xlsx')

    updates = {
        'locations_extracted': 0,
        'locations_cleaned': 0,
        'coordinates_added': 0,
    }

    print("\nPhase 1: Extracting missing locations from AAC descriptions...")
    print("-" * 80)

    for idx, row in df.iterrows():
        # Skip if already has a good location
        if pd.notna(row['location']) and len(str(row['location'])) > 5:
            # But check if it's questionable
            if any(word in str(row['location']).lower() for word in ['was', 'were', 'to the', 'at the']):
                cleaned = clean_questionable_location(row['location'])
                if cleaned != row['location']:
                    df.at[idx, 'location'] = cleaned
                    updates['locations_cleaned'] += 1
            continue

        # Try to extract location for AAC records
        if row['source'] == 'AAC':
            try:
                source_id = int(row['source_id'])
            except (ValueError, TypeError):
                continue
            original_match = aac_df[aac_df['ID'] == source_id]

            if len(original_match) > 0:
                original = original_match.iloc[0]
                new_location = extract_location_from_description(
                    original['Text'],
                    original['Accident Title']
                )

                if new_location:
                    df.at[idx, 'location'] = new_location
                    updates['locations_extracted'] += 1

        if (idx + 1) % 500 == 0:
            print(f"  Processed {idx + 1} records... ({updates['locations_extracted']} locations extracted)")

    print(f"\n  ✓ Extracted {updates['locations_extracted']} new locations")
    print(f"  ✓ Cleaned {updates['locations_cleaned']} questionable locations")

    print("\nPhase 2: Adding coordinates for all records...")
    print("-" * 80)

    for idx, row in df.iterrows():
        # Skip if already has coordinates
        if pd.notna(row['latitude']) and pd.notna(row['longitude']):
            continue

        lat, lon = get_coordinates_for_record(row)

        if lat is not None and lon is not None:
            df.at[idx, 'latitude'] = lat
            df.at[idx, 'longitude'] = lon
            updates['coordinates_added'] += 1

        if (idx + 1) % 500 == 0:
            print(f"  Processed {idx + 1} records... ({updates['coordinates_added']} coordinates added)")

    print(f"\n  ✓ Added coordinates to {updates['coordinates_added']} records")

    # Save updated data
    df.to_csv('data/accidents.csv', index=False)

    # Final statistics
    print("\n" + "=" * 80)
    print("FINAL STATISTICS")
    print("=" * 80)

    total_with_location = df['location'].notna().sum()
    total_with_coords = (df['latitude'].notna() & df['longitude'].notna()).sum()

    print(f"\nLocation coverage: {total_with_location} / {len(df)} ({total_with_location/len(df)*100:.1f}%)")
    print(f"Coordinate coverage: {total_with_coords} / {len(df)} ({total_with_coords/len(df)*100:.1f}%)")

    print("\nBy source:")
    for source in ['AAC', 'Avalanche', 'NPS']:
        source_df = df[df['source'] == source]
        with_loc = source_df['location'].notna().sum()
        with_coords = (source_df['latitude'].notna() & source_df['longitude'].notna()).sum()
        print(f"  {source}:")
        print(f"    Location: {with_loc} / {len(source_df)} ({with_loc/len(source_df)*100:.1f}%)")
        print(f"    Coords:   {with_coords} / {len(source_df)} ({with_coords/len(source_df)*100:.1f}%)")

    print("\n" + "=" * 80)
    print("SUCCESS! Enhanced dataset saved to data/accidents.csv")
    print("=" * 80)

if __name__ == '__main__':
    main()
