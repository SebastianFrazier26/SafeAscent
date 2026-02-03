#!/usr/bin/env python3
"""
Enhance Accident Coordinates

Adds coordinates to accidents that have dates but missing lat/lon.
Uses multiple strategies in priority order:
  1. Exact match to existing mountains in database
  2. Exact match to existing routes in database
  3. Fuzzy match to mountains (with manual review)
  4. Geocoding via OpenStreetMap Nominatim (free, no API key)
  5. Manual review for ambiguous cases
"""

import pandas as pd
import requests
import time
from fuzzywuzzy import fuzz, process
from tqdm import tqdm
import sys

# Configuration
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
RATE_LIMIT_SECONDS = 1.5  # Nominatim requires 1 request per second max
FUZZY_THRESHOLD = 85  # Minimum score for fuzzy matching (0-100)
OUTPUT_FILE = 'data/tables/accidents.csv'
ENHANCED_LOG = 'data/tables/coordinate_enhancement_log.csv'


def load_existing_locations():
    """Load existing mountains and routes with coordinates."""
    print("Loading existing locations from database...")

    mountains_df = pd.read_csv('data/tables/mountains.csv')
    routes_df = pd.read_csv('data/tables/routes.csv')

    # Create lookup dictionaries
    mountains_lookup = {}
    for _, mtn in mountains_df.iterrows():
        if pd.notna(mtn['latitude']) and pd.notna(mtn['longitude']):
            name = mtn['name'].lower().strip()
            mountains_lookup[name] = {
                'latitude': mtn['latitude'],
                'longitude': mtn['longitude'],
                'name': mtn['name'],
                'state': mtn['state'],
                'source': 'mountains_db'
            }

    routes_lookup = {}
    for _, route in routes_df.iterrows():
        if pd.notna(route['latitude']) and pd.notna(route['longitude']):
            name = route['name'].lower().strip()
            routes_lookup[name] = {
                'latitude': route['latitude'],
                'longitude': route['longitude'],
                'name': route['name'],
                'mountain_name': route.get('mountain_name', 'Unknown'),
                'source': 'routes_db'
            }

    print(f"  Mountains with coordinates: {len(mountains_lookup):,}")
    print(f"  Routes with coordinates: {len(routes_lookup):,}")

    return mountains_lookup, routes_lookup


def extract_location_from_accident(accident):
    """
    Extract potential location name from accident fields.

    Priority: mountain > route > location
    """
    candidates = []

    if pd.notna(accident.get('mountain')) and accident['mountain'].strip():
        candidates.append(('mountain', accident['mountain'].strip()))

    if pd.notna(accident.get('route')) and accident['route'].strip():
        candidates.append(('route', accident['route'].strip()))

    if pd.notna(accident.get('location')) and accident['location'].strip():
        candidates.append(('location', accident['location'].strip()))

    return candidates


def exact_match_lookup(location_text, mountains_lookup, routes_lookup):
    """Try exact match against existing locations."""
    location_lower = location_text.lower().strip()

    # Try mountains first
    if location_lower in mountains_lookup:
        return mountains_lookup[location_lower]

    # Try routes
    if location_lower in routes_lookup:
        return routes_lookup[location_lower]

    return None


def fuzzy_match_mountains(location_text, mountains_lookup, threshold=FUZZY_THRESHOLD):
    """
    Fuzzy match against mountain names.
    Returns list of candidates above threshold.
    """
    mountain_names = list(mountains_lookup.keys())

    # Get top matches
    matches = process.extract(location_text.lower(), mountain_names, scorer=fuzz.token_sort_ratio, limit=5)

    # Filter by threshold
    candidates = []
    for match_name, score in matches:
        if score >= threshold:
            candidates.append({
                'name': mountains_lookup[match_name]['name'],
                'score': score,
                'latitude': mountains_lookup[match_name]['latitude'],
                'longitude': mountains_lookup[match_name]['longitude'],
                'state': mountains_lookup[match_name]['state']
            })

    return candidates


def geocode_location(location_text, state=None):
    """
    Geocode location using OpenStreetMap Nominatim.

    Args:
        location_text: Location name to geocode
        state: Optional state to narrow search (e.g., "Colorado")

    Returns:
        dict with latitude, longitude, display_name or None
    """
    # Build query
    query = location_text
    if state and pd.notna(state):
        query = f"{location_text}, {state}"

    params = {
        'q': query,
        'format': 'json',
        'limit': 1,
        'addressdetails': 1
    }

    headers = {
        'User-Agent': 'SafeAscent/1.0 (climbing safety research)'
    }

    try:
        response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        results = response.json()

        if results and len(results) > 0:
            result = results[0]
            return {
                'latitude': float(result['lat']),
                'longitude': float(result['lon']),
                'display_name': result.get('display_name', ''),
                'source': 'nominatim_geocode'
            }

        return None

    except Exception as e:
        print(f"\n⚠️  Geocoding error for '{location_text}': {e}")
        return None


def interactive_review(accident_id, location_text, candidates, method):
    """
    Interactive review for ambiguous matches.

    Returns: selected candidate or None to skip
    """
    print("\n" + "=" * 70)
    print(f"Accident ID: {accident_id}")
    print(f"Location text: {location_text}")
    print(f"Method: {method}")
    print("=" * 70)

    for i, candidate in enumerate(candidates, 1):
        if method == 'fuzzy':
            print(f"\n{i}. {candidate['name']} ({candidate['state']})")
            print(f"   Match score: {candidate['score']}/100")
            print(f"   Coordinates: ({candidate['latitude']:.4f}, {candidate['longitude']:.4f})")
        else:
            print(f"\n{i}. {candidate['display_name']}")
            print(f"   Coordinates: ({candidate['latitude']:.4f}, {candidate['longitude']:.4f})")

    print(f"\n0. Skip this accident")

    while True:
        try:
            choice = input(f"\nSelect option (0-{len(candidates)}): ").strip()
            choice_num = int(choice)

            if choice_num == 0:
                return None
            elif 1 <= choice_num <= len(candidates):
                return candidates[choice_num - 1]
            else:
                print(f"Invalid choice. Enter 0-{len(candidates)}")
        except ValueError:
            print("Invalid input. Enter a number.")


def enhance_coordinates(interactive_mode=True, auto_accept_exact=True, auto_accept_fuzzy_high=True):
    """
    Main function to enhance accident coordinates.

    Args:
        interactive_mode: If True, prompts for ambiguous matches
        auto_accept_exact: If True, automatically accepts exact matches
        auto_accept_fuzzy_high: If True, auto-accepts fuzzy matches >95 score
    """
    print("\n" + "=" * 80)
    print("ACCIDENT COORDINATE ENHANCEMENT")
    print("=" * 80)
    print("\nStrategy:")
    print("  1. Exact match to existing mountains in database")
    print("  2. Exact match to existing routes in database")
    print("  3. Fuzzy match to mountains (score ≥85)")
    print("  4. Geocoding via OpenStreetMap Nominatim")
    print("  5. Manual review for ambiguous cases")
    print()

    # Load data
    print("Loading accident data...")
    accidents_df = pd.read_csv(OUTPUT_FILE)

    # Find accidents missing coordinates
    missing_coords = accidents_df[
        accidents_df['date'].notna() &
        (accidents_df['latitude'].isna() | accidents_df['longitude'].isna())
    ].copy()

    print(f"\nTotal accidents: {len(accidents_df):,}")
    print(f"Missing coordinates (but have date): {len(missing_coords):,}")

    if len(missing_coords) == 0:
        print("\n✅ All accidents with dates already have coordinates!")
        return

    # Load reference locations
    mountains_lookup, routes_lookup = load_existing_locations()

    # Statistics
    stats = {
        'exact_mountain': 0,
        'exact_route': 0,
        'fuzzy_mountain': 0,
        'geocoded': 0,
        'manual_approved': 0,
        'skipped': 0,
        'failed': 0
    }

    enhancement_log = []

    print("\n" + "=" * 80)
    print("PROCESSING ACCIDENTS")
    print("=" * 80 + "\n")

    if interactive_mode:
        print("Running in INTERACTIVE mode - you'll review ambiguous matches")
        print("Press Ctrl+C at any time to stop and save progress\n")
    else:
        print("Running in AUTO mode - only exact matches will be used\n")

    pbar = tqdm(missing_coords.iterrows(), total=len(missing_coords), desc="Processing")

    for idx, accident in pbar:
        accident_id = accident['accident_id']
        state = accident.get('state', None)

        # Extract location candidates
        location_candidates = extract_location_from_accident(accident)

        if not location_candidates:
            stats['skipped'] += 1
            enhancement_log.append({
                'accident_id': accident_id,
                'original_location': accident.get('location', ''),
                'method': 'skipped',
                'reason': 'no location text',
                'latitude': None,
                'longitude': None
            })
            continue

        # Try each location candidate
        matched = False

        for field_name, location_text in location_candidates:
            if matched:
                break

            # Strategy 1: Exact match
            exact_match = exact_match_lookup(location_text, mountains_lookup, routes_lookup)

            if exact_match and auto_accept_exact:
                # Exact match - use it
                accidents_df.loc[accidents_df['accident_id'] == accident_id, 'latitude'] = exact_match['latitude']
                accidents_df.loc[accidents_df['accident_id'] == accident_id, 'longitude'] = exact_match['longitude']

                if exact_match['source'] == 'mountains_db':
                    stats['exact_mountain'] += 1
                else:
                    stats['exact_route'] += 1

                enhancement_log.append({
                    'accident_id': accident_id,
                    'original_location': location_text,
                    'matched_name': exact_match['name'],
                    'method': exact_match['source'],
                    'latitude': exact_match['latitude'],
                    'longitude': exact_match['longitude']
                })

                matched = True
                continue

            # Strategy 2: Fuzzy match
            fuzzy_candidates = fuzzy_match_mountains(location_text, mountains_lookup)

            if fuzzy_candidates:
                # High confidence fuzzy match (>95)
                if fuzzy_candidates[0]['score'] >= 95 and auto_accept_fuzzy_high:
                    best = fuzzy_candidates[0]
                    accidents_df.loc[accidents_df['accident_id'] == accident_id, 'latitude'] = best['latitude']
                    accidents_df.loc[accidents_df['accident_id'] == accident_id, 'longitude'] = best['longitude']

                    stats['fuzzy_mountain'] += 1

                    enhancement_log.append({
                        'accident_id': accident_id,
                        'original_location': location_text,
                        'matched_name': best['name'],
                        'method': f'fuzzy_match_score_{best["score"]}',
                        'latitude': best['latitude'],
                        'longitude': best['longitude']
                    })

                    matched = True
                    continue

                # Medium confidence - needs review
                elif interactive_mode:
                    pbar.close()
                    selected = interactive_review(accident_id, location_text, fuzzy_candidates, 'fuzzy')
                    pbar = tqdm(missing_coords.iterrows(), total=len(missing_coords), desc="Processing", initial=stats['exact_mountain']+stats['exact_route']+stats['fuzzy_mountain']+stats['geocoded']+stats['skipped'])

                    if selected:
                        accidents_df.loc[accidents_df['accident_id'] == accident_id, 'latitude'] = selected['latitude']
                        accidents_df.loc[accidents_df['accident_id'] == accident_id, 'longitude'] = selected['longitude']

                        stats['manual_approved'] += 1

                        enhancement_log.append({
                            'accident_id': accident_id,
                            'original_location': location_text,
                            'matched_name': selected['name'],
                            'method': f'manual_fuzzy_score_{selected["score"]}',
                            'latitude': selected['latitude'],
                            'longitude': selected['longitude']
                        })

                        matched = True
                        continue

            # Strategy 3: Geocoding
            geocoded = geocode_location(location_text, state)

            if geocoded:
                # Check if result looks reasonable (within US climbing areas)
                lat = geocoded['latitude']
                lon = geocoded['longitude']

                # Rough US bounding box (continental US + Alaska)
                if (-180 <= lon <= -60) and (20 <= lat <= 75):
                    if interactive_mode:
                        # Show for review
                        pbar.close()
                        selected = interactive_review(accident_id, location_text, [geocoded], 'geocode')
                        pbar = tqdm(missing_coords.iterrows(), total=len(missing_coords), desc="Processing", initial=stats['exact_mountain']+stats['exact_route']+stats['fuzzy_mountain']+stats['geocoded']+stats['skipped'])

                        if selected:
                            accidents_df.loc[accidents_df['accident_id'] == accident_id, 'latitude'] = selected['latitude']
                            accidents_df.loc[accidents_df['accident_id'] == accident_id, 'longitude'] = selected['longitude']

                            stats['geocoded'] += 1

                            enhancement_log.append({
                                'accident_id': accident_id,
                                'original_location': location_text,
                                'matched_name': geocoded['display_name'],
                                'method': 'nominatim_manual',
                                'latitude': geocoded['latitude'],
                                'longitude': geocoded['longitude']
                            })

                            matched = True
                            continue
                    else:
                        # Auto mode - skip geocoded results (less reliable)
                        pass

            # Rate limiting for geocoding
            if geocoded:
                time.sleep(RATE_LIMIT_SECONDS)

        if not matched:
            stats['failed'] += 1
            enhancement_log.append({
                'accident_id': accident_id,
                'original_location': accident.get('location', ''),
                'method': 'no_match',
                'reason': 'no suitable match found',
                'latitude': None,
                'longitude': None
            })

        # Update progress
        pbar.set_postfix({
            'exact': stats['exact_mountain'] + stats['exact_route'],
            'fuzzy': stats['fuzzy_mountain'],
            'geo': stats['geocoded'],
            'manual': stats['manual_approved'],
            'failed': stats['failed']
        })

    pbar.close()

    # Save results
    print("\nSaving enhanced accident data...")
    accidents_df.to_csv(OUTPUT_FILE, index=False)

    print("Saving enhancement log...")
    log_df = pd.DataFrame(enhancement_log)
    log_df.to_csv(ENHANCED_LOG, index=False)

    # Summary
    print("\n" + "=" * 80)
    print("ENHANCEMENT COMPLETE")
    print("=" * 80)
    print(f"\nAccidents processed: {len(missing_coords):,}")
    print(f"\n✅ Successfully enhanced:")
    print(f"  Exact mountain match: {stats['exact_mountain']:,}")
    print(f"  Exact route match: {stats['exact_route']:,}")
    print(f"  Fuzzy mountain match: {stats['fuzzy_mountain']:,}")
    print(f"  Geocoded: {stats['geocoded']:,}")
    print(f"  Manual approved: {stats['manual_approved']:,}")
    total_enhanced = stats['exact_mountain'] + stats['exact_route'] + stats['fuzzy_mountain'] + stats['geocoded'] + stats['manual_approved']
    print(f"  TOTAL: {total_enhanced:,} ({total_enhanced/len(missing_coords)*100:.1f}%)")
    print(f"\n❌ Not enhanced:")
    print(f"  Skipped (no location): {stats['skipped']:,}")
    print(f"  Failed (no match): {stats['failed']:,}")

    # Final coverage
    final_with_coords = len(accidents_df[
        accidents_df['date'].notna() &
        accidents_df['latitude'].notna() &
        accidents_df['longitude'].notna()
    ])
    total_with_dates = len(accidents_df[accidents_df['date'].notna()])

    print(f"\n📊 Final coverage:")
    print(f"  Accidents with date + coordinates: {final_with_coords:,} / {total_with_dates:,} ({final_with_coords/total_with_dates*100:.1f}%)")
    print(f"  Improvement: +{total_enhanced:,} accidents")
    print(f"\n✅ Enhanced data saved to: {OUTPUT_FILE}")
    print(f"✅ Enhancement log saved to: {ENHANCED_LOG}")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Enhance accident coordinates')
    parser.add_argument('--auto', action='store_true', help='Auto mode (no interactive review)')
    parser.add_argument('--no-fuzzy-auto', action='store_true', help='Require manual review for all fuzzy matches')

    args = parser.parse_args()

    try:
        enhance_coordinates(
            interactive_mode=not args.auto,
            auto_accept_exact=True,
            auto_accept_fuzzy_high=not args.no_fuzzy_auto
        )
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user. Progress has been saved.")
        print("    Run this script again to continue from where you left off.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        raise
