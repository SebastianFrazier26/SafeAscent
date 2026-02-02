"""
Build comprehensive Routes table
Combines accident route data with known route information
"""

import pandas as pd
import numpy as np
import hashlib
import re

# Comprehensive route data for popular routes
ROUTE_DATA = {
    # Mount McKinley/Denali
    ('McKinley', 'West Buttress'): {'grade': '5.0', 'length': 16000, 'type': 'alpine', 'pitches': None, 'first_ascent': 1951},
    ('McKinley', 'West Rib'): {'grade': '5.2', 'length': 14000, 'type': 'alpine', 'pitches': None, 'first_ascent': 1959},
    ('McKinley', 'Cassin Ridge'): {'grade': 'VI 5.8 AI4', 'length': 8000, 'type': 'alpine', 'pitches': 40, 'first_ascent': 1961},

    # Mount Rainier
    ('Rainier', 'Disappointment Cleaver'): {'grade': 'Grade II', 'length': 9000, 'type': 'alpine', 'pitches': None, 'first_ascent': 1909},
    ('Rainier', 'Ingraham Glacier'): {'grade': 'Grade II', 'length': 9000, 'type': 'alpine', 'pitches': None, 'first_ascent': 1920},
    ('Rainier', 'Kautz Glacier'): {'grade': 'Grade III', 'length': 9000, 'type': 'alpine', 'pitches': None, 'first_ascent': 1920},
    ('Rainier', 'Liberty Ridge'): {'grade': 'Grade IV 5.4', 'length': 10000, 'type': 'alpine', 'pitches': None, 'first_ascent': 1935},
    ('Rainier', 'Emmons Glacier'): {'grade': 'Grade II', 'length': 9000, 'type': 'alpine', 'pitches': None, 'first_ascent': 1884},

    # Mount Hood
    ('Hood', 'South Side'): {'grade': 'Grade II', 'length': 5200, 'type': 'alpine', 'pitches': None, 'first_ascent': 1857},
    ('Hood', 'Pearly Gates'): {'grade': 'Grade II+', 'length': 5200, 'type': 'alpine', 'pitches': None, 'first_ascent': 1940},
    ('Hood', 'Leuthold Couloir'): {'grade': 'Grade III AI3', 'length': 5000, 'type': 'alpine', 'pitches': None, 'first_ascent': 1959},

    # Mount Shasta
    ('Shasta', 'Avalanche Gulch'): {'grade': 'Grade II', 'length': 7000, 'type': 'alpine', 'pitches': None, 'first_ascent': 1854},
    ('Shasta', 'Casaval Ridge'): {'grade': 'Grade III 5.4', 'length': 8000, 'type': 'alpine', 'pitches': None, 'first_ascent': 1937},

    # Longs Peak
    ('Longs', 'Keyhole Route'): {'grade': 'Grade II 5.2', 'length': 2800, 'type': 'alpine', 'pitches': None, 'first_ascent': 1868},
    ('Longs', 'Diamond East Face'): {'grade': 'V 5.10', 'length': 1800, 'type': 'alpine', 'pitches': 15, 'first_ascent': 1960},

    # Grand Teton
    ('Grand Teton', 'Owen-Spalding'): {'grade': 'III 5.4', 'length': 2400, 'type': 'alpine', 'pitches': 8, 'first_ascent': 1898},
    ('Grand Teton', 'Exum Ridge'): {'grade': 'III 5.5', 'length': 2600, 'type': 'alpine', 'pitches': 10, 'first_ascent': 1931},
    ('Grand Teton', 'Direct Exum'): {'grade': 'III 5.7', 'length': 2800, 'type': 'alpine', 'pitches': 12, 'first_ascent': 1936},

    # Mount Whitney
    ('Whitney', 'East Face'): {'grade': 'III 5.7', 'length': 1000, 'type': 'alpine', 'pitches': 11, 'first_ascent': 1931},
    ('Whitney', 'Mountaineers Route'): {'grade': 'Grade II 5.4', 'length': 6000, 'type': 'alpine', 'pitches': None, 'first_ascent': 1930},

    # Yosemite
    ('El Capitan', 'The Nose'): {'grade': 'VI 5.9 C2', 'length': 3000, 'type': 'big_wall', 'pitches': 31, 'first_ascent': 1958},
    ('El Capitan', 'Salathe Wall'): {'grade': 'VI 5.9 C2', 'length': 3200, 'type': 'big_wall', 'pitches': 35, 'first_ascent': 1961},
    ('Half Dome', 'Regular Northwest Face'): {'grade': 'VI 5.9 C1', 'length': 2000, 'type': 'big_wall', 'pitches': 23, 'first_ascent': 1957},
}

def generate_route_id(name, mountain):
    """Generate unique route ID"""
    combined = f"{mountain}_{name}".lower()
    return f"rt_{hashlib.md5(combined.encode()).hexdigest()[:8]}"

def parse_grade(grade_str):
    """Extract YDS grade from various formats"""
    if pd.isna(grade_str):
        return None

    # Look for 5.X pattern
    match = re.search(r'5\.(\d+[a-d]?)', str(grade_str))
    if match:
        return f"5.{match.group(1)}"

    return None

def classify_route_type(route_name, mountain_type):
    """Guess route type from name and mountain type"""
    name_lower = str(route_name).lower()

    if any(word in name_lower for word in ['buttress', 'ridge', 'face', 'couloir', 'glacier']):
        return 'alpine'
    elif any(word in name_lower for word in ['wall', 'crack', 'corner', 'arete']):
        return 'trad'
    elif mountain_type == 'crag':
        return 'trad'
    else:
        return 'unknown'

def create_routes_table():
    """Create comprehensive routes table"""
    print("=" * 80)
    print("BUILDING ROUTES TABLE")
    print("=" * 80)

    # Load data
    accidents = pd.read_csv('data/tables/accidents.csv')
    mountains_df = pd.read_csv('data/tables/mountains.csv')
    routes_rated = pd.read_csv('data/routes_rated.csv')

    # Get unique route/mountain combinations from accidents
    accident_routes = accidents[accidents['route'].notna() & accidents['mountain'].notna()][
        ['route', 'mountain', 'latitude', 'longitude']
    ].copy()

    # Group by route and mountain to get unique combinations
    route_mountain = accident_routes.groupby(['route', 'mountain']).first().reset_index()

    print(f"\nFound {len(route_mountain)} unique route/mountain combinations in accidents")

    # Build route records
    routes = []
    routes_with_data = 0
    routes_from_rated = 0

    for _, row in route_mountain.iterrows():
        route_name = row['route']
        mountain_name = row['mountain']

        # Get mountain info
        mountain_info = mountains_df[mountains_df['name'] == mountain_name]
        if len(mountain_info) == 0:
            continue

        mountain_info = mountain_info.iloc[0]
        mountain_id = mountain_info['mountain_id']
        mountain_type = mountain_info['type']

        route_id = generate_route_id(route_name, mountain_name)

        # Check if we have detailed data for this route
        route_key = (mountain_name, route_name)
        if route_key in ROUTE_DATA:
            data = ROUTE_DATA[route_key]
            routes_with_data += 1

            routes.append({
                'route_id': route_id,
                'name': route_name,
                'mountain_id': mountain_id,
                'mountain_name': mountain_name,
                'grade': data['grade'],
                'grade_yds': parse_grade(data['grade']),
                'length_ft': data['length'],
                'pitches': data['pitches'],
                'type': data['type'],
                'first_ascent_year': data['first_ascent'],
                'latitude': row['latitude'],
                'longitude': row['longitude'],
                'accident_count': len(accidents[(accidents['route'] == route_name) & (accidents['mountain'] == mountain_name)])
            })
        else:
            # Try to match with routes_rated
            matched = False
            if not matched:
                # Create basic record
                routes.append({
                    'route_id': route_id,
                    'name': route_name,
                    'mountain_id': mountain_id,
                    'mountain_name': mountain_name,
                    'grade': None,
                    'grade_yds': None,
                    'length_ft': None,
                    'pitches': None,
                    'type': classify_route_type(route_name, mountain_type),
                    'first_ascent_year': None,
                    'latitude': row['latitude'],
                    'longitude': row['longitude'],
                    'accident_count': len(accidents[(accidents['route'] == route_name) & (accidents['mountain'] == mountain_name)])
                })

    df = pd.DataFrame(routes)

    # Sort by accident count
    df = df.sort_values('accident_count', ascending=False)

    print(f"\nCreated {len(df)} route records")
    print(f"  With detailed data: {routes_with_data}")
    print(f"  With grade info: {df['grade'].notna().sum()}")
    print(f"  With length info: {df['length_ft'].notna().sum()}")
    print(f"  With coordinates: {df['latitude'].notna().sum()}")

    # Save
    df.to_csv('data/tables/routes.csv', index=False)

    print("\n" + "=" * 80)
    print("Top 20 routes by accident count:")
    print("=" * 80)
    for _, row in df.head(20).iterrows():
        grade = row['grade'] if pd.notna(row['grade']) else "unknown"
        mountain = row['mountain_name']
        print(f"  {row['name']:30s} on {mountain:15s} | {grade:15s} | {row['accident_count']:2d} accidents")

    print("\n" + "=" * 80)
    print("Routes needing additional data (Mountain Project scraping):")
    print("=" * 80)
    needs_data = df[df['grade'].isna()]
    print(f"  {len(needs_data)} routes without grade information")
    print(f"\n  Sample routes needing data:")
    for _, row in needs_data.head(10).iterrows():
        print(f"    {row['name']:30s} on {row['mountain_name']}")

    print("\n" + "=" * 80)
    print(f"SUCCESS! Routes table saved to data/tables/routes.csv")
    print("=" * 80)

    return df

if __name__ == '__main__':
    routes_df = create_routes_table()
