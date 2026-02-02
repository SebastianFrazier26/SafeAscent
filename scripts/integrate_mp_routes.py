#!/usr/bin/env python3
"""
Integrate scraped Mountain Project routes into the SafeAscent database.

This script:
1. Loads scraped routes from CSV
2. Deduplicates against existing database routes
3. Matches routes to mountains using coordinates
4. Validates and inserts new routes
5. Generates a detailed integration report
"""

import csv
import sys
import psycopg2
from psycopg2.extras import execute_values
from typing import List, Dict, Tuple, Optional
import re
from datetime import datetime


# Database configuration
DB_CONFIG = {
    'dbname': 'safeascent',
    'user': 'sebastianfrazier',
    'host': 'localhost',
    'port': 5432
}

# File paths
SCRAPED_CSV = "/Users/sebastianfrazier/SafeAscent/data/mp_routes_scraped.csv"
REPORT_FILE = "/Users/sebastianfrazier/SafeAscent/data/mp_integration_report.txt"


def connect_db():
    """Connect to PostgreSQL database."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        sys.exit(1)


def get_existing_mp_route_ids(conn) -> set:
    """Get all existing Mountain Project route IDs from database."""
    cur = conn.cursor()
    cur.execute("SELECT mp_route_id FROM routes WHERE mp_route_id IS NOT NULL")
    existing_ids = {str(row[0]) for row in cur.fetchall()}
    cur.close()
    return existing_ids


def get_mountains(conn) -> List[Tuple]:
    """Get all mountains with their coordinates."""
    cur = conn.cursor()
    cur.execute("""
        SELECT mountain_id, name, latitude, longitude, elevation_ft
        FROM mountains
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """)
    mountains = cur.fetchall()
    cur.close()
    return mountains


def parse_first_ascent_year(first_ascent: Optional[str]) -> Optional[int]:
    """Extract year from first ascent string."""
    if not first_ascent:
        return None

    # Look for 4-digit year
    match = re.search(r'\b(19|20)\d{2}\b', first_ascent)
    if match:
        return int(match.group(0))
    return None


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate approximate distance in miles between two lat/lon points."""
    from math import radians, sin, cos, sqrt, atan2

    R = 3959  # Earth radius in miles

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))

    return R * c


def find_matching_mountain(route_lat: float, route_lon: float, mountains: List[Tuple],
                          max_distance: float = 5.0) -> Optional[int]:
    """
    Find the closest mountain to a route within max_distance miles.

    Returns:
        mountain_id if found within distance, None otherwise
    """
    if not route_lat or not route_lon:
        return None

    closest_mountain = None
    closest_distance = float('inf')

    for mountain_id, name, m_lat, m_lon, elevation in mountains:
        if m_lat and m_lon:
            distance = calculate_distance(route_lat, route_lon, m_lat, m_lon)
            if distance < closest_distance and distance <= max_distance:
                closest_distance = distance
                closest_mountain = (mountain_id, name, distance)

    return closest_mountain


def load_scraped_routes(csv_path: str) -> List[Dict]:
    """Load routes from scraped CSV file."""
    routes = []

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            routes.append(row)

    return routes


def validate_route(route: Dict) -> Tuple[bool, Optional[str]]:
    """
    Validate a route has minimum required data.

    Returns:
        (is_valid, error_message)
    """
    if not route.get('name'):
        return False, "Missing route name"

    if not route.get('mp_route_id'):
        return False, "Missing MP route ID"

    # At least one of grade fields should be present
    if not route.get('grade') and not route.get('grade_yds'):
        return False, "Missing grade information"

    return True, None


def prepare_route_for_insert(route: Dict, mountain_match: Optional[Tuple]) -> Dict:
    """Prepare route data for database insertion."""

    # Parse numeric fields
    latitude = float(route['latitude']) if route.get('latitude') else None
    longitude = float(route['longitude']) if route.get('longitude') else None
    elevation_ft = float(route['elevation_ft']) if route.get('elevation_ft') else None
    length_ft = float(route['length_ft']) if route.get('length_ft') else None
    pitches = int(route['pitches']) if route.get('pitches') and route['pitches'].strip() else None

    # Parse first ascent year
    first_ascent_year = parse_first_ascent_year(route.get('first_ascent'))

    # Get mountain info if matched
    mountain_id = mountain_match[0] if mountain_match else None
    mountain_name = mountain_match[1] if mountain_match else route.get('area_name')

    return {
        'name': route['name'],
        'mountain_id': mountain_id,
        'mountain_name': mountain_name,
        'grade': route.get('grade'),
        'grade_yds': route.get('grade_yds'),
        'length_ft': length_ft,
        'pitches': pitches,
        'type': route.get('type'),
        'first_ascent_year': first_ascent_year,
        'latitude': latitude,
        'longitude': longitude,
        'mp_route_id': route['mp_route_id']
    }


def insert_routes(conn, routes: List[Dict]) -> int:
    """Insert routes into database using batch insert."""
    if not routes:
        return 0

    cur = conn.cursor()

    # Get current max route_id to generate new IDs
    cur.execute("SELECT COALESCE(MAX(route_id), 0) FROM routes")
    max_id = cur.fetchone()[0]
    next_id = max_id + 1

    insert_query = """
        INSERT INTO routes (
            route_id, name, mountain_id, mountain_name, grade, grade_yds,
            length_ft, pitches, type, first_ascent_year,
            latitude, longitude, mp_route_id, accident_count
        ) VALUES %s
    """

    # Prepare values for batch insert with generated route_ids
    values = [
        (
            next_id + i,  # Generate sequential route_id
            r['name'], r['mountain_id'], r['mountain_name'],
            r['grade'], r['grade_yds'], r['length_ft'],
            r['pitches'], r['type'], r['first_ascent_year'],
            r['latitude'], r['longitude'], r['mp_route_id'], 0  # accident_count defaults to 0
        )
        for i, r in enumerate(routes)
    ]

    execute_values(cur, insert_query, values)
    conn.commit()

    inserted_count = len(routes)
    cur.close()

    return inserted_count


def generate_report(
    total_scraped: int,
    duplicates: int,
    invalid: int,
    with_mountain: int,
    without_mountain: int,
    inserted: int,
    invalid_reasons: List[str]
) -> str:
    """Generate integration report."""

    report = f"""
{'='*80}
MOUNTAIN PROJECT ROUTES INTEGRATION REPORT
{'='*80}
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

SUMMARY
-------
Total routes in scraped CSV:           {total_scraped:>6}
Duplicate MP IDs (already in DB):      {duplicates:>6}
Invalid routes (missing required data): {invalid:>6}
New routes ready for insertion:        {inserted:>6}

MOUNTAIN MATCHING
-----------------
Routes matched to mountains:           {with_mountain:>6}
Routes without mountain match:         {without_mountain:>6}

VALIDATION ERRORS
-----------------
"""

    if invalid_reasons:
        from collections import Counter
        error_counts = Counter(invalid_reasons)
        for error, count in error_counts.most_common():
            report += f"  {error}: {count}\n"
    else:
        report += "  None\n"

    report += f"\n{'='*80}\n"
    report += "INTEGRATION STATUS: ✅ COMPLETE\n"
    report += f"{'='*80}\n"

    return report


def main():
    """Main integration process."""
    print("=" * 80)
    print("MOUNTAIN PROJECT ROUTES INTEGRATION")
    print("=" * 80)

    # Connect to database
    print("\n1. Connecting to database...")
    conn = connect_db()
    print("   ✅ Connected")

    # Get existing data
    print("\n2. Loading existing database state...")
    existing_ids = get_existing_mp_route_ids(conn)
    mountains = get_mountains(conn)
    print(f"   ✅ Found {len(existing_ids)} existing MP routes")
    print(f"   ✅ Found {len(mountains)} mountains for matching")

    # Load scraped routes
    print("\n3. Loading scraped routes...")
    scraped_routes = load_scraped_routes(SCRAPED_CSV)
    print(f"   ✅ Loaded {len(scraped_routes)} scraped routes")

    # Process routes
    print("\n4. Processing and validating routes...")

    duplicates = 0
    invalid = 0
    invalid_reasons = []
    with_mountain = 0
    without_mountain = 0
    routes_to_insert = []

    for i, route in enumerate(scraped_routes):
        if (i + 1) % 100 == 0:
            print(f"   Processing route {i+1}/{len(scraped_routes)}...")

        # Check for duplicates
        if route['mp_route_id'] in existing_ids:
            duplicates += 1
            continue

        # Validate route
        is_valid, error_msg = validate_route(route)
        if not is_valid:
            invalid += 1
            invalid_reasons.append(error_msg)
            continue

        # Try to match to a mountain
        latitude = float(route['latitude']) if route.get('latitude') else None
        longitude = float(route['longitude']) if route.get('longitude') else None

        mountain_match = find_matching_mountain(latitude, longitude, mountains)

        if mountain_match:
            with_mountain += 1
        else:
            without_mountain += 1

        # Prepare for insertion
        prepared_route = prepare_route_for_insert(route, mountain_match)
        routes_to_insert.append(prepared_route)

    print(f"   ✅ Processed all routes")
    print(f"      - Duplicates: {duplicates}")
    print(f"      - Invalid: {invalid}")
    print(f"      - Ready to insert: {len(routes_to_insert)}")

    # Insert new routes
    if routes_to_insert:
        print(f"\n5. Inserting {len(routes_to_insert)} new routes into database...")
        inserted_count = insert_routes(conn, routes_to_insert)
        print(f"   ✅ Inserted {inserted_count} routes")
    else:
        print("\n5. No new routes to insert")
        inserted_count = 0

    # Generate report
    print("\n6. Generating integration report...")
    report = generate_report(
        total_scraped=len(scraped_routes),
        duplicates=duplicates,
        invalid=invalid,
        with_mountain=with_mountain,
        without_mountain=without_mountain,
        inserted=inserted_count,
        invalid_reasons=invalid_reasons
    )

    with open(REPORT_FILE, 'w') as f:
        f.write(report)

    print(f"   ✅ Report saved to {REPORT_FILE}")

    # Print report summary
    print("\n" + report)

    # Close connection
    conn.close()

    print("✅ Integration complete!")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Integration interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Integration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
