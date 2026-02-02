"""
Link Accidents to Mountains and Routes Tables

Adds mountain_id and route_id foreign keys to accidents table by matching:
- Accident mountain name → Mountains table
- Accident route name + mountain → Routes table

Uses fuzzy matching to handle slight name variations.
"""

import pandas as pd
from fuzzywuzzy import fuzz
from tqdm import tqdm

def normalize_name(name):
    """Normalize name for matching"""
    if pd.isna(name):
        return ""

    name = str(name).lower().strip()

    # Common replacements
    replacements = {
        'mount ': 'mt ',
        'mt. ': 'mt ',
        'saint ': 'st ',
        'st. ': 'st ',
    }

    for old, new in replacements.items():
        name = name.replace(old, new)

    return name

def find_best_mountain_match(mountain_name, mountains_df, threshold=85):
    """
    Find best matching mountain in mountains table.

    Args:
        mountain_name: Name from accident
        mountains_df: Mountains dataframe
        threshold: Minimum fuzzy match score (0-100)

    Returns:
        mountain_id or None
    """
    if pd.isna(mountain_name) or mountain_name == "":
        return None

    norm_name = normalize_name(mountain_name)
    best_score = 0
    best_id = None

    for idx, mountain in mountains_df.iterrows():
        # Check main name
        score1 = fuzz.ratio(norm_name, normalize_name(mountain['name']))

        # Check alt names if available
        score2 = 0
        if pd.notna(mountain['alt_names']):
            alt_names = str(mountain['alt_names']).split(',')
            for alt in alt_names:
                score2 = max(score2, fuzz.ratio(norm_name, normalize_name(alt.strip())))

        score = max(score1, score2)

        if score > best_score:
            best_score = score
            best_id = mountain['mountain_id']

    if best_score >= threshold:
        return best_id

    return None

def find_best_route_match(route_name, mountain_id, routes_df, threshold=85):
    """
    Find best matching route for a given mountain.

    Args:
        route_name: Name from accident
        mountain_id: Matched mountain ID (or None)
        routes_df: Routes dataframe
        threshold: Minimum fuzzy match score

    Returns:
        route_id or None
    """
    if pd.isna(route_name) or route_name == "":
        return None

    norm_route = normalize_name(route_name)

    # Filter routes to same mountain if we have mountain_id
    if mountain_id:
        candidate_routes = routes_df[routes_df['mountain_id'] == mountain_id]
    else:
        candidate_routes = routes_df

    if len(candidate_routes) == 0:
        return None

    best_score = 0
    best_id = None

    for idx, route in candidate_routes.iterrows():
        score = fuzz.ratio(norm_route, normalize_name(route['name']))

        if score > best_score:
            best_score = score
            best_id = route['route_id']

    if best_score >= threshold:
        return best_id

    return None

def link_accidents():
    """Main function to link accidents to mountains and routes"""

    print("\n" + "=" * 80)
    print("LINKING ACCIDENTS TO MOUNTAINS AND ROUTES")
    print("=" * 80)

    # Load tables
    print("\nLoading tables...")
    accidents = pd.read_csv('data/tables/accidents.csv')
    mountains = pd.read_csv('data/tables/mountains.csv')
    routes = pd.read_csv('data/tables/routes.csv')

    print(f"  Accidents: {len(accidents):,}")
    print(f"  Mountains: {len(mountains):,}")
    print(f"  Routes: {len(routes):,}")

    # Add new columns if they don't exist
    if 'mountain_id' not in accidents.columns:
        accidents['mountain_id'] = None
    if 'route_id' not in accidents.columns:
        accidents['route_id'] = None

    # Match mountains
    print("\n" + "=" * 80)
    print("MATCHING MOUNTAINS")
    print("=" * 80 + "\n")

    mountain_matches = 0
    mountain_misses = 0

    for idx in tqdm(accidents.index, desc="Matching mountains"):
        accident = accidents.loc[idx]

        if pd.notna(accident['mountain']):
            mountain_id = find_best_mountain_match(
                accident['mountain'],
                mountains,
                threshold=80  # Lower threshold for flexibility
            )

            if mountain_id:
                accidents.at[idx, 'mountain_id'] = mountain_id
                mountain_matches += 1
            else:
                mountain_misses += 1

    print(f"\nMountain matching results:")
    print(f"  Matched: {mountain_matches:,} ({mountain_matches/len(accidents)*100:.1f}%)")
    print(f"  Not matched: {mountain_misses:,}")
    print(f"  No mountain name: {accidents['mountain'].isna().sum():,}")

    # Match routes
    print("\n" + "=" * 80)
    print("MATCHING ROUTES")
    print("=" * 80 + "\n")

    route_matches = 0
    route_misses = 0

    for idx in tqdm(accidents.index, desc="Matching routes"):
        accident = accidents.loc[idx]

        if pd.notna(accident['route']):
            route_id = find_best_route_match(
                accident['route'],
                accident.get('mountain_id'),  # Use matched mountain_id if available
                routes,
                threshold=80
            )

            if route_id:
                accidents.at[idx, 'route_id'] = route_id
                route_matches += 1
            else:
                route_misses += 1

    print(f"\nRoute matching results:")
    print(f"  Matched: {route_matches:,} ({route_matches/len(accidents)*100:.1f}%)")
    print(f"  Not matched: {route_misses:,}")
    print(f"  No route name: {accidents['route'].isna().sum():,}")

    # Save updated accidents table
    accidents.to_csv('data/tables/accidents.csv', index=False)

    # Summary statistics
    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)

    both_matched = accidents['mountain_id'].notna() & accidents['route_id'].notna()
    only_mountain = accidents['mountain_id'].notna() & accidents['route_id'].isna()
    only_route = accidents['mountain_id'].isna() & accidents['route_id'].notna()
    neither = accidents['mountain_id'].isna() & accidents['route_id'].isna()

    print(f"\nAccident linkage summary:")
    print(f"  Both mountain + route: {both_matched.sum():,} ({both_matched.sum()/len(accidents)*100:.1f}%)")
    print(f"  Only mountain: {only_mountain.sum():,} ({only_mountain.sum()/len(accidents)*100:.1f}%)")
    print(f"  Only route: {only_route.sum():,} ({only_route.sum()/len(accidents)*100:.1f}%)")
    print(f"  Neither: {neither.sum():,} ({neither.sum()/len(accidents)*100:.1f}%)")

    # Show some examples
    print("\n" + "=" * 80)
    print("SAMPLE LINKED ACCIDENTS")
    print("=" * 80)

    linked = accidents[both_matched].head(10)
    for _, acc in linked.iterrows():
        mountain = mountains[mountains['mountain_id'] == acc['mountain_id']].iloc[0]
        route = routes[routes['route_id'] == acc['route_id']].iloc[0]

        print(f"\nAccident {int(acc['accident_id'])}:")
        print(f"  Text: {acc['mountain']} - {acc['route']}")
        print(f"  Linked: {mountain['name']} (ID {int(acc['mountain_id'])}) - {route['name']} (ID {int(acc['route_id'])})")

    # Show unmatched examples for review
    print("\n" + "=" * 80)
    print("SAMPLE UNMATCHED MOUNTAINS (for manual review)")
    print("=" * 80)

    unmatched_mountains = accidents[
        accidents['mountain'].notna() &
        accidents['mountain_id'].isna()
    ]['mountain'].value_counts().head(20)

    print("\nMost common unmatched mountain names:")
    for name, count in unmatched_mountains.items():
        print(f"  {name}: {count} accidents")

    print("\n✅ Accidents table updated with mountain_id and route_id foreign keys")
    print("   Saved to: data/tables/accidents.csv")

if __name__ == '__main__':
    link_accidents()
