"""
Build comprehensive Mountains/Crags table
Combines accident data with comprehensive mountain/crag information
"""

import pandas as pd
import numpy as np
import hashlib

# Comprehensive mountain/crag data with elevations and details
MOUNTAIN_DATA = {
    # Alaska Mountains
    'McKinley': {'alt_names': ['Denali'], 'elevation': 20310, 'prominence': 20156, 'type': 'peak', 'range': 'Alaska Range', 'state': 'Alaska'},
    'Denali': {'alt_names': ['McKinley'], 'elevation': 20310, 'prominence': 20156, 'type': 'peak', 'range': 'Alaska Range', 'state': 'Alaska'},
    'Foraker': {'elevation': 17400, 'prominence': 7250, 'type': 'peak', 'range': 'Alaska Range', 'state': 'Alaska'},
    'Hunter': {'elevation': 14573, 'prominence': 3966, 'type': 'peak', 'range': 'Alaska Range', 'state': 'Alaska'},
    'Russell': {'elevation': 11670, 'prominence': 3895, 'type': 'peak', 'range': 'Alaska Range', 'state': 'Alaska'},
    'Sanford': {'elevation': 16237, 'prominence': 8156, 'type': 'peak', 'range': 'Wrangell Mountains', 'state': 'Alaska'},
    'Blackburn': {'elevation': 16390, 'prominence': 9042, 'type': 'peak', 'range': 'Wrangell Mountains', 'state': 'Alaska'},

    # Washington Cascades
    'Rainier': {'elevation': 14411, 'prominence': 13211, 'type': 'peak', 'range': 'Cascade Range', 'state': 'Washington'},
    'Baker': {'elevation': 10781, 'prominence': 8812, 'type': 'peak', 'range': 'North Cascades', 'state': 'Washington'},
    'Adams': {'elevation': 12281, 'prominence': 8116, 'type': 'peak', 'range': 'Cascade Range', 'state': 'Washington'},
    'Glacier Peak': {'elevation': 10541, 'prominence': 7146, 'type': 'peak', 'range': 'North Cascades', 'state': 'Washington'},
    'Stuart': {'elevation': 9415, 'prominence': 5330, 'type': 'peak', 'range': 'Cascade Range', 'state': 'Washington'},
    'Shuksan': {'elevation': 9131, 'prominence': 4426, 'type': 'peak', 'range': 'North Cascades', 'state': 'Washington'},
    'Olympus': {'elevation': 7980, 'prominence': 7818, 'type': 'peak', 'range': 'Olympic Mountains', 'state': 'Washington'},
    'Index': {'elevation': 5979, 'prominence': 3179, 'type': 'peak', 'range': 'Cascade Range', 'state': 'Washington'},
    'Washington': {'elevation': 6288, 'prominence': 6148, 'type': 'peak', 'range': 'Presidential Range', 'state': 'New Hampshire'},

    # California Mountains
    'Whitney': {'elevation': 14505, 'prominence': 10080, 'type': 'peak', 'range': 'Sierra Nevada', 'state': 'California'},
    'Shasta': {'elevation': 14179, 'prominence': 9832, 'type': 'peak', 'range': 'Cascade Range', 'state': 'California'},
    'Half Dome': {'elevation': 8842, 'prominence': 1460, 'type': 'peak', 'range': 'Sierra Nevada', 'state': 'California'},
    'El Capitan': {'elevation': 7569, 'prominence': 1000, 'type': 'peak', 'range': 'Sierra Nevada', 'state': 'California'},
    'Williamson': {'elevation': 14379, 'prominence': 1677, 'type': 'peak', 'range': 'Sierra Nevada', 'state': 'California'},
    'North Palisade': {'elevation': 14248, 'prominence': 2895, 'type': 'peak', 'range': 'Sierra Nevada', 'state': 'California'},
    'Sill': {'elevation': 14159, 'prominence': 597, 'type': 'peak', 'range': 'Sierra Nevada', 'state': 'California'},
    'Russell': {'elevation': 14094, 'prominence': 1142, 'type': 'peak', 'range': 'Sierra Nevada', 'state': 'California'},

    # Colorado 14ers
    'Longs': {'elevation': 14259, 'prominence': 2942, 'type': 'peak', 'range': 'Front Range', 'state': 'Colorado'},
    'Elbert': {'elevation': 14440, 'prominence': 9093, 'type': 'peak', 'range': 'Sawatch Range', 'state': 'Colorado'},
    'Massive': {'elevation': 14428, 'prominence': 1961, 'type': 'peak', 'range': 'Sawatch Range', 'state': 'Colorado'},
    'Harvard': {'elevation': 14421, 'prominence': 2360, 'type': 'peak', 'range': 'Sawatch Range', 'state': 'Colorado'},
    'Blanca': {'elevation': 14351, 'prominence': 5326, 'type': 'peak', 'range': 'Sangre de Cristo', 'state': 'Colorado'},
    'La Plata': {'elevation': 14361, 'prominence': 3952, 'type': 'peak', 'range': 'Sawatch Range', 'state': 'Colorado'},
    'Uncompahgre': {'elevation': 14321, 'prominence': 4442, 'type': 'peak', 'range': 'San Juan Mountains', 'state': 'Colorado'},
    'Crestone': {'elevation': 14300, 'prominence': 1920, 'type': 'peak', 'range': 'Sangre de Cristo', 'state': 'Colorado'},
    'Capitol': {'elevation': 14137, 'prominence': 2331, 'type': 'peak', 'range': 'Elk Mountains', 'state': 'Colorado'},
    'Maroon': {'elevation': 14163, 'prominence': 2463, 'type': 'peak', 'range': 'Elk Mountains', 'state': 'Colorado'},
    'Sneffels': {'elevation': 14158, 'prominence': 3033, 'type': 'peak', 'range': 'San Juan Mountains', 'state': 'Colorado'},

    # Wyoming
    'Grand Teton': {'elevation': 13775, 'prominence': 6530, 'type': 'peak', 'range': 'Teton Range', 'state': 'Wyoming'},
    'Teewinot': {'elevation': 12330, 'prominence': 1230, 'type': 'peak', 'range': 'Teton Range', 'state': 'Wyoming'},
    'Owen': {'elevation': 12928, 'prominence': 648, 'type': 'peak', 'range': 'Teton Range', 'state': 'Wyoming'},
    'Gannett': {'elevation': 13810, 'prominence': 7076, 'type': 'peak', 'range': 'Wind River Range', 'state': 'Wyoming'},

    # Oregon Cascades
    'Hood': {'elevation': 11250, 'prominence': 7706, 'type': 'peak', 'range': 'Cascade Range', 'state': 'Oregon'},
    'Jefferson': {'elevation': 10502, 'prominence': 4810, 'type': 'peak', 'range': 'Cascade Range', 'state': 'Oregon'},
    'South Sister': {'elevation': 10363, 'prominence': 2263, 'type': 'peak', 'range': 'Cascade Range', 'state': 'Oregon'},
    'North Sister': {'elevation': 10085, 'prominence': 1785, 'type': 'peak', 'range': 'Cascade Range', 'state': 'Oregon'},

    # Canadian Rockies - Alberta
    'Robson': {'elevation': 12972, 'prominence': 9908, 'type': 'peak', 'range': 'Canadian Rockies', 'state': 'British Columbia'},
    'Temple': {'elevation': 11627, 'prominence': 2090, 'type': 'peak', 'range': 'Canadian Rockies', 'state': 'Alberta'},
    'Assiniboine': {'elevation': 11870, 'prominence': 4941, 'type': 'peak', 'range': 'Canadian Rockies', 'state': 'British Columbia'},
    'Athabasca': {'elevation': 11453, 'prominence': 2979, 'type': 'peak', 'range': 'Canadian Rockies', 'state': 'Alberta'},
    'Rundle': {'elevation': 9675, 'prominence': 2775, 'type': 'peak', 'range': 'Canadian Rockies', 'state': 'Alberta'},
    'Yamnuska': {'elevation': 7903, 'prominence': 1079, 'type': 'peak', 'range': 'Canadian Rockies', 'state': 'Alberta'},
    'Edith Cavell': {'elevation': 11033, 'prominence': 1821, 'type': 'peak', 'range': 'Canadian Rockies', 'state': 'Alberta'},
    'Columbia': {'elevation': 12294, 'prominence': 8806, 'type': 'peak', 'range': 'Canadian Rockies', 'state': 'Alberta'},
    'Alberta': {'elevation': 11874, 'prominence': 1929, 'type': 'peak', 'range': 'Canadian Rockies', 'state': 'Alberta'},
    'Andromeda': {'elevation': 11319, 'prominence': 1460, 'type': 'peak', 'range': 'Canadian Rockies', 'state': 'Alberta'},

    # More Canadian Mountains
    'Waddington': {'elevation': 13186, 'prominence': 9515, 'type': 'peak', 'range': 'Coast Mountains', 'state': 'British Columbia'},
    'Tantalus': {'elevation': 8540, 'prominence': 5810, 'type': 'peak', 'range': 'Coast Mountains', 'state': 'British Columbia'},

    # Climbing Areas (crags/walls)
    'Redgarden': {'elevation': 7000, 'prominence': None, 'type': 'crag', 'range': 'Front Range', 'state': 'Colorado'},
    'Lover\'s Leap': {'elevation': 6800, 'prominence': None, 'type': 'crag', 'range': 'Sierra Nevada', 'state': 'California'},
    'Tahquitz': {'elevation': 8846, 'prominence': None, 'type': 'crag', 'range': 'San Jacinto Mountains', 'state': 'California'},
    'Suicide Rock': {'elevation': 7528, 'prominence': None, 'type': 'crag', 'range': 'San Jacinto Mountains', 'state': 'California'},
}

def generate_mountain_id(name):
    """Generate unique mountain ID"""
    return f"mt_{hashlib.md5(name.lower().encode()).hexdigest()[:8]}"

def create_mountains_table():
    """Create comprehensive mountains/crags table"""
    print("=" * 80)
    print("BUILDING MOUNTAINS/CRAGS TABLE")
    print("=" * 80)

    # Load accidents to get all referenced mountains
    accidents = pd.read_csv('data/tables/accidents.csv')

    # Get unique mountains from accidents
    accident_mountains = accidents[accidents['mountain'].notna()][['mountain', 'state', 'location', 'latitude', 'longitude']].copy()
    accident_mountains = accident_mountains.groupby('mountain').first().reset_index()

    print(f"\nFound {len(accident_mountains)} unique mountains in accidents data")

    # Build comprehensive mountain records
    mountains = []

    for _, row in accident_mountains.iterrows():
        mountain_name = row['mountain']
        mountain_id = generate_mountain_id(mountain_name)

        # Check if we have detailed data for this mountain
        if mountain_name in MOUNTAIN_DATA:
            data = MOUNTAIN_DATA[mountain_name]

            mountains.append({
                'mountain_id': mountain_id,
                'name': mountain_name,
                'alt_names': ','.join(data.get('alt_names', [])) if data.get('alt_names') else None,
                'elevation_ft': data.get('elevation'),
                'prominence_ft': data.get('prominence'),
                'type': data.get('type'),
                'range': data.get('range'),
                'state': data.get('state', row['state']),
                'latitude': row['latitude'],
                'longitude': row['longitude'],
                'location': row['location'],
                'accident_count': len(accidents[accidents['mountain'] == mountain_name])
            })
        else:
            # Use what we have from accidents
            mountains.append({
                'mountain_id': mountain_id,
                'name': mountain_name,
                'alt_names': None,
                'elevation_ft': None,
                'prominence_ft': None,
                'type': 'unknown',
                'range': None,
                'state': row['state'],
                'latitude': row['latitude'],
                'longitude': row['longitude'],
                'location': row['location'],
                'accident_count': len(accidents[accidents['mountain'] == mountain_name])
            })

    df = pd.DataFrame(mountains)

    # Sort by accident count
    df = df.sort_values('accident_count', ascending=False)

    print(f"\nCreated {len(df)} mountain records")
    print(f"  With elevation data: {df['elevation_ft'].notna().sum()}")
    print(f"  With coordinates: {df['latitude'].notna().sum()}")
    print(f"  Peaks: {(df['type'] == 'peak').sum()}")
    print(f"  Crags: {(df['type'] == 'crag').sum()}")

    # Save
    df.to_csv('data/tables/mountains.csv', index=False)

    print("\n" + "=" * 80)
    print("Top 20 mountains by accident count:")
    print("=" * 80)
    for _, row in df.head(20).iterrows():
        elev = f"{row['elevation_ft']:,}'" if pd.notna(row['elevation_ft']) else "unknown"
        print(f"  {row['name']:20s} {row['accident_count']:3d} accidents | {elev:>10s} | {row['state']}")

    print("\n" + "=" * 80)
    print(f"SUCCESS! Mountains table saved to data/tables/mountains.csv")
    print("=" * 80)

    return df

if __name__ == '__main__':
    mountains_df = create_mountains_table()
