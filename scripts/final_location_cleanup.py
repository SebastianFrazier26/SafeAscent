"""
Final location and coordinate cleanup
- Extract states from descriptions
- Clean bad route/location data
- Add coordinates for smaller climbing areas
"""

import pandas as pd
import re

# Extended coordinate database for smaller areas
ADDITIONAL_COORDINATES = {
    # US State/Regional climbing areas
    'Pilot Mountain': (36.3378, -80.4697),  # NC
    "Crowders Mountain": (35.2095, -81.2981),  # NC
    "Moore's Wall": (36.3956, -80.2628),  # NC, Hanging Rock
    "Hanging Rock State Park": (36.3956, -80.2628),  # NC
    "Shortoff Mountain": (35.9517, -81.8517),  # NC
    "Table Mountain": (35.9833, -81.8833),  # NC
    "Stone Mountain": (36.4000, -81.0333),  # NC
    "Looking Glass Rock": (35.2944, -82.7836),  # NC

    # PA
    "Ralph Stover State Park": (40.4717, -75.1353),  # PA
    "Mount Minsi": (40.9686, -75.1281),  # PA
    "Mount Tammany": (40.9847, -75.1244),  # NJ

    # WV
    "Cooper's Rock State Forest": (39.6639, -79.7850),  # WV
    "Seneca Rocks": (38.8346, -79.3764),  # WV

    # TN
    "Obed Wild and Scenic River": (36.1200, -84.7100),  # TN
    "Tennessee Wall": (35.0500, -85.3500),  # TN

    # New Mexico
    "Cochiti Mesa": (35.6500, -106.3167),  # NM
    "Sandia Mountains": (35.2050, -106.4792),  # NM

    # South Dakota
    "Needles": (43.8333, -103.5000),  # SD
    "Black Hills": (43.8333, -103.5000),  # SD

    # Idaho
    "Sawtooth Mountains": (43.9667, -114.9667),  # ID
    "Baron Peak": (44.0833, -115.0333),  # ID

    # Canadian areas
    "Gatineau Park": (45.5089, -75.8928),  # QC
    "Buffalo Crag": (45.3333, -76.1667),  # ON
    "Charlevoix Mountains": (47.6500, -70.4500),  # QC
    "Ha Ling Peak": (51.0667, -115.4000),  # AB
    "Chinaman's Peak": (51.0667, -115.4000),  # AB (same as Ha Ling)
    "Crandell Mountain": (49.0667, -113.9333),  # AB
    "Indefatigable Mountain": (50.0167, -115.1333),  # AB

    # BC mountains
    "Waddington": (51.3731, -125.2631),  # BC
    "Tantalus": (49.8167, -123.2167),  # BC

    # More US mountains
    "Logan": (41.7369, -111.8225),  # UT
    "Ragged Mountain": (41.6333, -72.7833),  # CT
    "Endless Wall": (38.0667, -81.0667),  # WV, New River Gorge

    # Illinois
    "Crab Orchard": (37.7333, -89.0333),  # IL
}

US_STATES = [
    'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado',
    'Connecticut', 'Delaware', 'Florida', 'Georgia', 'Idaho', 'Illinois',
    'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana', 'Maine',
    'Massachusetts', 'Michigan', 'Minnesota', 'Mississippi', 'Missouri',
    'Montana', 'Nebraska', 'Nevada', 'New Hampshire', 'New Jersey',
    'New Mexico', 'New York', 'North Carolina', 'North Dakota', 'Ohio',
    'Oklahoma', 'Oregon', 'Pennsylvania', 'Rhode Island', 'South Carolina',
    'South Dakota', 'Tennessee', 'Texas', 'Utah', 'Vermont', 'Virginia',
    'Washington', 'West Virginia', 'Wisconsin', 'Wyoming'
]

CANADIAN_PROVINCES = [
    'Alberta', 'British Columbia', 'Manitoba', 'New Brunswick',
    'Newfoundland and Labrador', 'Nova Scotia', 'Ontario', 'Prince Edward Island',
    'Quebec', 'Saskatchewan'
]

def extract_state_from_description(description):
    """Extract state/province from description text"""
    if pd.isna(description):
        return None

    # Check first 200 chars for state name
    text = description[:200]

    # Check US states
    for state in US_STATES:
        if state in text:
            return state

    # Check Canadian provinces
    for province in CANADIAN_PROVINCES:
        if province in text:
            return province

    return None

def is_bad_route_name(route):
    """Identify route names that are actually sentences/bad data"""
    if pd.isna(route):
        return False

    route_str = str(route)

    # If it's a long sentence, it's not a route name
    if len(route_str) > 60:
        return True

    # If it has multiple sentences or questions
    if '. ' in route_str or '? ' in route_str:
        return True

    # Common bad patterns
    bad_patterns = [
        "you are here", "you'll have", "let's have",
        "on belay", "and somehow", "came down",
        "after climbing", "I could not",
    ]

    for pattern in bad_patterns:
        if pattern.lower() in route_str.lower():
            return True

    return False

def extract_better_location(description, current_location):
    """Extract a better location from description if current is bad"""
    if pd.isna(description):
        return current_location

    # If current location is bad, try to extract
    if pd.isna(current_location) or len(str(current_location)) < 5:
        # Look for specific location patterns
        patterns = [
            r'([\w\s]+State Park)',
            r'([\w\s]+State Forest)',
            r'([\w\s]+National Park)',
            r'([\w\s]+National Forest)',
            r'([\w\s]+Mountains)',
            r'([\w\s]+Wall)',
            r'([\w\s]+Crag)',
        ]

        for pattern in patterns:
            match = re.search(pattern, description[:300])
            if match:
                location = match.group(1).strip()
                if len(location) > 5:
                    return location

    return current_location

def get_additional_coordinates(row):
    """Get coordinates from additional database"""
    # Check mountain
    if pd.notna(row['mountain']):
        if row['mountain'] in ADDITIONAL_COORDINATES:
            return ADDITIONAL_COORDINATES[row['mountain']]

    # Check location
    if pd.notna(row['location']):
        location = row['location']
        # Exact match
        if location in ADDITIONAL_COORDINATES:
            return ADDITIONAL_COORDINATES[location]
        # Partial match
        for place, coords in ADDITIONAL_COORDINATES.items():
            if place.lower() in location.lower():
                return coords

    return None, None

def main():
    print("=" * 80)
    print("FINAL LOCATION & COORDINATE CLEANUP")
    print("=" * 80)

    df = pd.read_csv('data/accidents.csv')
    aac_df = pd.read_excel('data/aac_accidents.xlsx')

    updates = {
        'states_extracted': 0,
        'locations_improved': 0,
        'bad_routes_cleaned': 0,
        'coordinates_added': 0,
    }

    print("\nPhase 1: Extract missing states from descriptions...")
    print("-" * 80)

    for idx, row in df.iterrows():
        if pd.isna(row['state']):
            # Try to extract from description
            new_state = extract_state_from_description(row['description'])
            if new_state:
                df.at[idx, 'state'] = new_state
                updates['states_extracted'] += 1

    print(f"  ✓ Extracted {updates['states_extracted']} states from descriptions")

    print("\nPhase 2: Clean bad route names...")
    print("-" * 80)

    for idx, row in df.iterrows():
        if is_bad_route_name(row['route']):
            print(f"  Removing bad route: {row['accident_id']} - '{str(row['route'])[:60]}...'")
            df.at[idx, 'route'] = None
            updates['bad_routes_cleaned'] += 1

    print(f"  ✓ Cleaned {updates['bad_routes_cleaned']} bad route names")

    print("\nPhase 3: Improve location data...")
    print("-" * 80)

    for idx, row in df.iterrows():
        better_location = extract_better_location(row['description'], row['location'])
        if better_location != row['location'] and pd.notna(better_location):
            df.at[idx, 'location'] = better_location
            updates['locations_improved'] += 1

    print(f"  ✓ Improved {updates['locations_improved']} locations")

    print("\nPhase 4: Add remaining coordinates...")
    print("-" * 80)

    for idx, row in df.iterrows():
        # Skip if already has coordinates
        if pd.notna(row['latitude']) and pd.notna(row['longitude']):
            continue

        lat, lon = get_additional_coordinates(row)
        if lat is not None and lon is not None:
            df.at[idx, 'latitude'] = lat
            df.at[idx, 'longitude'] = lon
            updates['coordinates_added'] += 1

    print(f"  ✓ Added {updates['coordinates_added']} more coordinates")

    # Save
    df.to_csv('data/accidents.csv', index=False)

    # Final stats
    print("\n" + "=" * 80)
    print("FINAL STATISTICS")
    print("=" * 80)

    total_with_state = df['state'].notna().sum()
    total_with_location = df['location'].notna().sum()
    total_with_coords = (df['latitude'].notna() & df['longitude'].notna()).sum()

    print(f"\nState coverage:    {total_with_state} / {len(df)} ({total_with_state/len(df)*100:.1f}%)")
    print(f"Location coverage: {total_with_location} / {len(df)} ({total_with_location/len(df)*100:.1f}%)")
    print(f"Coordinate coverage: {total_with_coords} / {len(df)} ({total_with_coords/len(df)*100:.1f}%)")

    print("\nRemaining records without coordinates:")
    missing = df[(df['latitude'].isna()) | (df['longitude'].isna())]
    print(f"  Total: {len(missing)}")
    if len(missing) > 0:
        print(f"  Sample locations: {missing['location'].value_counts().head(5).to_dict()}")

    print("\n" + "=" * 80)
    print("SUCCESS! Final dataset saved to data/accidents.csv")
    print("=" * 80)

if __name__ == '__main__':
    main()
