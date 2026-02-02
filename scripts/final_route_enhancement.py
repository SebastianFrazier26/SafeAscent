"""
Final route enhancement - handle name variations and add more routes
"""

import pandas as pd
import re

# Additional routes and route name variations
ADDITIONAL_ROUTES = {
    # Handle variations in naming
    ('Hood', 'South Side Route'): {'grade': 'Grade II', 'length': 5200, 'type': 'alpine', 'pitches': None},
    ('Hood', 'Southside Route'): {'grade': 'Grade II', 'length': 5200, 'type': 'alpine', 'pitches': None},
    ('Hood', 'South Side'): {'grade': 'Grade II', 'length': 5200, 'type': 'alpine', 'pitches': None},

    ('Rainier', 'Kautz Glacier Route'): {'grade': 'Grade III', 'length': 9000, 'type': 'alpine', 'pitches': None},
    ('Rainier', 'Kautz Glacier'): {'grade': 'Grade III', 'length': 9000, 'type': 'alpine', 'pitches': None},
    ('Rainier', 'Kautz Ice Chute'): {'grade': 'Grade III', 'length': 9000, 'type': 'alpine', 'pitches': None},

    ('Shasta', 'Avalanche Gulch Route'): {'grade': 'Grade II', 'length': 7000, 'type': 'alpine', 'pitches': None},
    ('Shasta', 'Avalanche Gulch'): {'grade': 'Grade II', 'length': 7000, 'type': 'alpine', 'pitches': None},

    # More Canadian Rockies routes
    ('Temple', 'tourist route'): {'grade': '5.4', 'length': 3500, 'type': 'alpine', 'pitches': 10},
    ('Edith', 'South Ridge'): {'grade': '5.4', 'length': 2500, 'type': 'alpine', 'pitches': 8},
    ('Snowdome', 'Slipstream'): {'grade': 'WI4+', 'length': 2000, 'type': 'ice', 'pitches': 10},

    # More Colorado routes
    ('Ypsilon', 'Blitzen Ridge'): {'grade': '5.6', 'length': 1500, 'type': 'alpine', 'pitches': 8},
    ("Moore's", 'Sentinel Buttress'): {'grade': '5.7', 'length': 400, 'type': 'trad', 'pitches': 4},
    ('Crowders', 'Red Wall'): {'grade': '5.9', 'length': 300, 'type': 'trad', 'pitches': 3},

    # More Alaska
    ('McKinley', 'football field'): {'grade': 'Alaska Grade 2', 'length': 16000, 'type': 'alpine', 'pitches': None},
    ('McKinley', 'AAI 2'): {'grade': 'Alaska Grade 2', 'length': 16000, 'type': 'alpine', 'pitches': None},

    # Cathedral Peak
    ('Cathedral', 'standard'): {'grade': '5.6', 'length': 400, 'type': 'trad', 'pitches': 4},
    ('Cathedral', 'Southeast Buttress'): {'grade': '5.6', 'length': 600, 'type': 'trad', 'pitches': 6},

    # More Yosemite
    ('Salathé', 'Salathé Wall'): {'grade': '5.9 C2', 'length': 3200, 'type': 'big_wall', 'pitches': 35},

    # More Washington Cascades
    ('Shuksan', 'Price Glacier'): {'grade': 'Grade II', 'length': 4000, 'type': 'alpine', 'pitches': None},
    ('Glacier Peak', 'Cool Glacier'): {'grade': 'Grade II', 'length': 5000, 'type': 'alpine', 'pitches': None},
    ('Olympus', 'Blue Glacier'): {'grade': 'Grade II', 'length': 4000, 'type': 'alpine', 'pitches': None},

    # More Sierra routes
    ('North Palisade', 'U-Notch Couloir'): {'grade': '5.4', 'length': 2500, 'type': 'alpine', 'pitches': 10},
    ('Sill', 'Swiss Arete'): {'grade': '5.6', 'length': 2000, 'type': 'alpine', 'pitches': 8},
}

def normalize_route_name(name):
    """Normalize route name for better matching"""
    if pd.isna(name):
        return name

    # Remove common suffixes/variations
    name = str(name).strip()
    name = re.sub(r'\s+Route$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+Couloir$', ' Couloir', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+Glacier$', ' Glacier', name, flags=re.IGNORECASE)

    return name

def enhance_routes_final():
    """Final enhancement pass for routes"""
    print("=" * 80)
    print("FINAL ROUTE ENHANCEMENT")
    print("=" * 80)

    routes_df = pd.read_csv('data/tables/routes.csv')

    print(f"\nCurrent state:")
    print(f"  Total routes: {len(routes_df)}")
    print(f"  With grade data: {routes_df['grade'].notna().sum()}")
    print(f"  With length data: {routes_df['length_ft'].notna().sum()}")

    updated_count = 0

    # Try to match with additional routes
    for idx, row in routes_df.iterrows():
        if pd.notna(row['grade']):
            continue  # Skip if already has data

        route_key = (row['mountain_name'], row['name'])

        # Direct match
        if route_key in ADDITIONAL_ROUTES:
            data = ADDITIONAL_ROUTES[route_key]
            routes_df.at[idx, 'grade'] = data['grade']
            routes_df.at[idx, 'length_ft'] = data['length']
            routes_df.at[idx, 'type'] = data['type']
            routes_df.at[idx, 'pitches'] = data['pitches']
            updated_count += 1
            continue

        # Try normalized match
        normalized = normalize_route_name(row['name'])
        for (mtn, route_name), data in ADDITIONAL_ROUTES.items():
            if mtn == row['mountain_name'] and normalize_route_name(route_name) == normalized:
                routes_df.at[idx, 'grade'] = data['grade']
                routes_df.at[idx, 'length_ft'] = data['length']
                routes_df.at[idx, 'type'] = data['type']
                routes_df.at[idx, 'pitches'] = data['pitches']
                updated_count += 1
                break

    print(f"\nUpdated {updated_count} additional routes")
    print(f"  Now with grade data: {routes_df['grade'].notna().sum()}")
    print(f"  Now with length data: {routes_df['length_ft'].notna().sum()}")

    # Save
    routes_df.to_csv('data/tables/routes.csv', index=False)

    print("\n" + "=" * 80)
    print("FINAL STATISTICS")
    print("=" * 80)

    total = len(routes_df)
    with_grade = routes_df['grade'].notna().sum()
    with_length = routes_df['length_ft'].notna().sum()
    with_coords = routes_df['latitude'].notna().sum()

    print(f"\nTotal routes: {total}")
    print(f"  With grade: {with_grade} ({with_grade/total*100:.1f}%)")
    print(f"  With length: {with_length} ({with_length/total*100:.1f}%)")
    print(f"  With coordinates: {with_coords} ({with_coords/total*100:.1f}%)")

    print("\nBy route type:")
    print(routes_df['type'].value_counts())

    print("\n" + "=" * 80)
    print("Top routes by accident count (with data):")
    print("=" * 80)

    with_data = routes_df[routes_df['grade'].notna()].sort_values('accident_count', ascending=False)
    for _, row in with_data.head(20).iterrows():
        length = f"{int(row['length_ft'])}'" if pd.notna(row['length_ft']) else "?"
        pitches = f"{int(row['pitches'])}p" if pd.notna(row['pitches']) else ""
        print(f"  {row['name']:30s} {row['grade']:15s} {length:>8s} {pitches:>4s} on {row['mountain_name']:15s} ({row['accident_count']} accidents)")

    print("\n" + "=" * 80)
    print("SUCCESS! Final routes table saved")
    print("=" * 80)

    return routes_df

if __name__ == '__main__':
    routes_df = enhance_routes_final()
