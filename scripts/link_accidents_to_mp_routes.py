#!/usr/bin/env python3
"""
Link Accidents to MP Routes - One-Time Database Migration

This script links accidents to Mountain Project routes via fuzzy name matching:
1. Adds mp_route_id column to accidents table (if not exists)
2. Queries AAC accidents with route names
3. Fuzzy matches to mp_routes.name (exact or Levenshtein ≤ 1)
4. If multiple matches, takes closest by coordinates
5. Updates accident lat/lon from matched route's location
6. For unmatched accidents, clears the route name (treats as routeless)

Run this once after populating mp_routes table.

Usage:
    python scripts/link_accidents_to_mp_routes.py [--dry-run]
"""
import os
import sys
import argparse
from typing import Optional, List, Tuple, Set
from collections import defaultdict

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'backend', '.env'))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import math


def levenshtein_distance(s1: str, s2: str) -> int:
    """
    Calculate Levenshtein distance between two strings.

    O(m*n) time complexity, O(min(m,n)) space complexity.
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in kilometers."""
    R = 6371  # Earth radius in km

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    return R * c


def normalize_route_name(name: str) -> str:
    """
    Normalize route name for matching.

    - Lowercase
    - Remove common prefixes/suffixes
    - Normalize whitespace
    """
    if not name:
        return ""

    name = name.strip().lower()

    # Remove common prefixes
    prefixes = ["the ", "mt. ", "mt ", "mount ", "peak "]
    for prefix in prefixes:
        if name.startswith(prefix):
            name = name[len(prefix):]

    # Normalize whitespace
    name = " ".join(name.split())

    return name


def get_sync_engine():
    """Create synchronous engine for database operations with keepalive settings."""
    db_url = os.getenv("DATABASE_URL", "")
    # Convert asyncpg URL to psycopg2 for sync queries
    if "asyncpg" in db_url:
        db_url = db_url.replace("postgresql+asyncpg", "postgresql")
    return create_engine(
        db_url,
        pool_pre_ping=True,  # Check connection health before use
        pool_recycle=120,    # Recycle connections after 2 minutes
        connect_args={
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        }
    )


def ensure_mp_route_id_column(conn) -> bool:
    """
    Ensure mp_route_id column exists in accidents table.

    Returns True if column was created, False if it already existed.
    """
    # Check if column exists
    result = conn.execute(text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'accidents' AND column_name = 'mp_route_id'
    """))

    if result.fetchone():
        print("✓ mp_route_id column already exists")
        return False

    # Add column
    print("Adding mp_route_id column to accidents table...")
    conn.execute(text("""
        ALTER TABLE accidents
        ADD COLUMN mp_route_id BIGINT REFERENCES mp_routes(mp_route_id)
    """))

    # Add index
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_accidents_mp_route_id
        ON accidents(mp_route_id)
    """))

    conn.commit()
    print("✓ mp_route_id column added with index")
    return True


def load_mp_routes(conn) -> Tuple[dict, dict]:
    """
    Load all MP routes into memory for fast matching.

    Returns:
        routes_by_name: normalized_name -> list of (mp_route_id, name, lat, lon)
        routes_by_length: name_length -> set of normalized_names
    """
    print("Loading MP routes...")

    result = conn.execute(text("""
        SELECT r.mp_route_id, r.name, r.grade, l.latitude, l.longitude
        FROM mp_routes r
        JOIN mp_locations l ON r.location_id = l.mp_id
        WHERE l.latitude IS NOT NULL AND l.longitude IS NOT NULL
    """))

    routes_by_name = defaultdict(list)
    routes_by_length = defaultdict(set)
    total = 0

    for row in result:
        mp_route_id, name, grade, lat, lon = row
        normalized = normalize_route_name(name)
        routes_by_name[normalized].append({
            "mp_route_id": mp_route_id,
            "name": name,
            "grade": grade,
            "latitude": lat,
            "longitude": lon,
        })
        routes_by_length[len(normalized)].add(normalized)
        total += 1

    print(f"✓ Loaded {total:,} routes into {len(routes_by_name):,} unique name buckets")
    return routes_by_name, routes_by_length


def find_best_match(
    accident_route_name: str,
    accident_lat: float,
    accident_lon: float,
    routes_by_name: dict,
    routes_by_length: dict,
    max_levenshtein: int = 1,
) -> Optional[dict]:
    """
    Find best matching MP route for an accident.

    Strategy:
    1. Try exact match first
    2. Try Levenshtein distance ≤ max_levenshtein (optimized: only check similar-length names)
    3. If multiple matches, take closest by coordinates

    Returns matched route dict or None.
    """
    normalized = normalize_route_name(accident_route_name)

    if not normalized:
        return None

    # Try exact match first
    candidates = []
    if normalized in routes_by_name:
        candidates = routes_by_name[normalized]
    else:
        # Try fuzzy match - only check names with similar length (optimization)
        name_len = len(normalized)
        for length in range(max(1, name_len - max_levenshtein), name_len + max_levenshtein + 1):
            if length in routes_by_length:
                for route_name in routes_by_length[length]:
                    if levenshtein_distance(normalized, route_name) <= max_levenshtein:
                        candidates.extend(routes_by_name[route_name])

    if not candidates:
        return None

    # If only one match, return it
    if len(candidates) == 1:
        return candidates[0]

    # Multiple matches - take closest by coordinates
    best_match = None
    best_distance = float('inf')

    for route in candidates:
        if route["latitude"] and route["longitude"]:
            dist = haversine_distance(
                accident_lat, accident_lon,
                route["latitude"], route["longitude"]
            )
            if dist < best_distance:
                best_distance = dist
                best_match = route

    return best_match


def link_accidents_to_routes(conn, routes_by_name: dict, routes_by_length: dict, dry_run: bool = False) -> dict:
    """
    Link AAC accidents to MP routes.

    Returns statistics dict.
    """
    print("\nLinking accidents to routes...", flush=True)

    # Get AAC accidents with route names (case-insensitive source check)
    result = conn.execute(text("""
        SELECT accident_id, route, latitude, longitude
        FROM accidents
        WHERE LOWER(source) = 'aac'
          AND route IS NOT NULL
          AND route != ''
          AND mp_route_id IS NULL
          AND latitude IS NOT NULL
          AND longitude IS NOT NULL
    """))

    accidents = result.fetchall()
    print(f"Found {len(accidents):,} AAC accidents with route names to process", flush=True)

    stats = {
        "total": len(accidents),
        "matched": 0,
        "unmatched": 0,
        "cleared": 0,
        "coord_updated": 0,
    }

    updates = []
    clears = []

    for i, (accident_id, route_name, lat, lon) in enumerate(accidents):
        if (i + 1) % 100 == 0:
            print(f"  Processing {i + 1:,}/{len(accidents):,}...", flush=True)

        match = find_best_match(route_name, lat, lon, routes_by_name, routes_by_length)

        if match:
            stats["matched"] += 1
            updates.append({
                "accident_id": accident_id,
                "mp_route_id": match["mp_route_id"],
                "new_lat": match["latitude"],
                "new_lon": match["longitude"],
                "route_name": route_name,
                "matched_name": match["name"],
            })
        else:
            stats["unmatched"] += 1
            clears.append({
                "accident_id": accident_id,
                "route_name": route_name,
            })

    print(f"\nMatching results:")
    if stats['total'] > 0:
        print(f"  Matched: {stats['matched']:,} ({stats['matched']/stats['total']*100:.1f}%)")
        print(f"  Unmatched: {stats['unmatched']:,} ({stats['unmatched']/stats['total']*100:.1f}%)")
    else:
        print("  No accidents with route names found to process.")

    if dry_run:
        print("\n[DRY RUN] Would update/clear the following:")
        print(f"  - Link {len(updates)} accidents to MP routes")
        print(f"  - Clear route name for {len(clears)} unmatched accidents")

        # Show sample matches
        print("\nSample matches:")
        for u in updates[:5]:
            print(f"  '{u['route_name']}' -> '{u['matched_name']}' (ID: {u['mp_route_id']})")

        print("\nSample unmatched (route name will be cleared):")
        for c in clears[:5]:
            print(f"  '{c['route_name']}'")

        return stats

    # Execute updates
    print("\nUpdating database...", flush=True)

    # Update matched accidents in batches
    BATCH_SIZE = 50
    for i, u in enumerate(updates):
        conn.execute(text("""
            UPDATE accidents
            SET mp_route_id = :mp_route_id,
                latitude = :new_lat,
                longitude = :new_lon
            WHERE accident_id = :accident_id
        """), {
            "mp_route_id": u["mp_route_id"],
            "new_lat": u["new_lat"],
            "new_lon": u["new_lon"],
            "accident_id": u["accident_id"],
        })
        stats["coord_updated"] += 1

        # Commit in batches to avoid connection timeout
        if (i + 1) % BATCH_SIZE == 0:
            conn.commit()
            print(f"  Updated {i + 1:,}/{len(updates):,} matched accidents...", flush=True)

    # Commit remaining
    conn.commit()
    print(f"  Updated {len(updates):,}/{len(updates):,} matched accidents.", flush=True)

    # Clear unmatched route names in batches
    for i, c in enumerate(clears):
        conn.execute(text("""
            UPDATE accidents
            SET route = NULL
            WHERE accident_id = :accident_id
        """), {"accident_id": c["accident_id"]})
        stats["cleared"] += 1

        if (i + 1) % BATCH_SIZE == 0:
            conn.commit()

    conn.commit()

    print(f"\n✓ Linked {stats['matched']:,} accidents to MP routes")
    print(f"✓ Updated coordinates for {stats['coord_updated']:,} accidents")
    print(f"✓ Cleared route name for {stats['cleared']:,} unmatched accidents")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Link accidents to MP routes")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Link Accidents to MP Routes - Migration Script")
    print("=" * 60)

    if args.dry_run:
        print("[DRY RUN MODE - No changes will be made]")

    engine = get_sync_engine()

    # Step 1: Ensure mp_route_id column exists (fresh connection)
    with engine.connect() as conn:
        ensure_mp_route_id_column(conn)

    # Step 2: Load MP routes (fresh connection, then release)
    with engine.connect() as conn:
        routes_by_name, routes_by_length = load_mp_routes(conn)

    # Step 3: Load accidents (fresh connection, then release)
    print("\nLoading AAC accidents...", flush=True)
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT accident_id, route, latitude, longitude
            FROM accidents
            WHERE LOWER(source) = 'aac'
              AND route IS NOT NULL
              AND route != ''
              AND mp_route_id IS NULL
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
        """))
        accidents = result.fetchall()
    print(f"✓ Loaded {len(accidents):,} AAC accidents with route names", flush=True)

    # Step 4: Match accidents to routes (OFFLINE - no connection needed!)
    print("\nMatching accidents to routes (offline)...", flush=True)
    updates = []
    clears = []
    for i, (accident_id, route_name, lat, lon) in enumerate(accidents):
        if (i + 1) % 200 == 0:
            print(f"  Matching {i + 1:,}/{len(accidents):,}...", flush=True)

        match = find_best_match(route_name, lat, lon, routes_by_name, routes_by_length)
        if match:
            updates.append({
                "accident_id": accident_id,
                "mp_route_id": match["mp_route_id"],
                "new_lat": match["latitude"],
                "new_lon": match["longitude"],
                "route_name": route_name,
                "matched_name": match["name"],
            })
        else:
            clears.append({
                "accident_id": accident_id,
                "route_name": route_name,
            })

    print(f"\nMatching results:", flush=True)
    print(f"  Matched: {len(updates):,} ({len(updates)/len(accidents)*100:.1f}%)", flush=True)
    print(f"  Unmatched: {len(clears):,} ({len(clears)/len(accidents)*100:.1f}%)", flush=True)

    if args.dry_run:
        print("\n[DRY RUN] Would update/clear the following:")
        print(f"  - Link {len(updates)} accidents to MP routes")
        print(f"  - Clear route name for {len(clears)} unmatched accidents")
        print("\nSample matches:")
        for u in updates[:5]:
            print(f"  '{u['route_name']}' -> '{u['matched_name']}' (ID: {u['mp_route_id']})")
        print("\nSample unmatched (route name will be cleared):")
        for c in clears[:5]:
            print(f"  '{c['route_name']}'")
        stats = {"total": len(accidents), "matched": len(updates), "unmatched": len(clears)}
    else:
        # Step 5: Apply updates (fresh connection with batching)
        print("\nUpdating database...", flush=True)
        BATCH_SIZE = 50
        with engine.connect() as conn:
            for i, u in enumerate(updates):
                conn.execute(text("""
                    UPDATE accidents
                    SET mp_route_id = :mp_route_id,
                        latitude = :new_lat,
                        longitude = :new_lon
                    WHERE accident_id = :accident_id
                """), {
                    "mp_route_id": u["mp_route_id"],
                    "new_lat": u["new_lat"],
                    "new_lon": u["new_lon"],
                    "accident_id": u["accident_id"],
                })
                if (i + 1) % BATCH_SIZE == 0:
                    conn.commit()
                    print(f"  Updated {i + 1:,}/{len(updates):,} matched accidents...", flush=True)
            conn.commit()
            print(f"  ✓ Linked {len(updates):,} accidents to MP routes", flush=True)

            # Clear unmatched route names
            for i, c in enumerate(clears):
                conn.execute(text("""
                    UPDATE accidents
                    SET route = NULL
                    WHERE accident_id = :accident_id
                """), {"accident_id": c["accident_id"]})
                if (i + 1) % BATCH_SIZE == 0:
                    conn.commit()
            conn.commit()
            print(f"  ✓ Cleared route name for {len(clears):,} unmatched accidents", flush=True)

        stats = {"total": len(accidents), "matched": len(updates), "unmatched": len(clears), "coord_updated": len(updates), "cleared": len(clears)}

    print("\n" + "=" * 60)
    print("Migration complete!")
    print("=" * 60)

    return stats


if __name__ == "__main__":
    main()
