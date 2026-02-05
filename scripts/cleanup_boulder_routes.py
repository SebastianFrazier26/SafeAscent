"""
Boulder Route Cleanup Script

This script:
1. Counts boulder routes in the mp_routes table
2. Creates a backup table (optional)
3. Deletes all boulder routes from mp_routes

Usage:
    # Dry run (count only):
    python scripts/cleanup_boulder_routes.py --dry-run

    # Execute deletion:
    python scripts/cleanup_boulder_routes.py --execute

    # With backup:
    python scripts/cleanup_boulder_routes.py --execute --backup

Environment:
    Set DATABASE_URL environment variable or uses default from .env
"""

import os
import sys
import argparse
from datetime import datetime

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2 import sql


def get_connection():
    """Get database connection from environment."""
    db_url = os.getenv('DATABASE_URL', '')

    # Handle asyncpg URLs by converting to psycopg2 format
    if 'asyncpg' in db_url:
        db_url = db_url.replace('postgresql+asyncpg://', 'postgresql://')

    if not db_url:
        raise ValueError("DATABASE_URL environment variable not set")

    return psycopg2.connect(db_url)


def count_boulder_routes(cursor):
    """Count routes that are boulder type."""
    cursor.execute("""
        SELECT
            type,
            COUNT(*) as count
        FROM mp_routes
        WHERE LOWER(type) LIKE '%boulder%'
        GROUP BY type
        ORDER BY count DESC
    """)
    return cursor.fetchall()


def count_total_routes(cursor):
    """Count total routes."""
    cursor.execute("SELECT COUNT(*) FROM mp_routes")
    return cursor.fetchone()[0]


def count_routes_by_type(cursor, limit=20):
    """Get top route types by count."""
    cursor.execute("""
        SELECT
            COALESCE(type, 'NULL') as route_type,
            COUNT(*) as count
        FROM mp_routes
        GROUP BY type
        ORDER BY count DESC
        LIMIT %s
    """, (limit,))
    return cursor.fetchall()


def create_backup_table(cursor, conn):
    """Create backup of boulder routes before deletion."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_table = f"mp_routes_boulders_backup_{timestamp}"

    cursor.execute(sql.SQL("""
        CREATE TABLE {} AS
        SELECT * FROM mp_routes
        WHERE LOWER(type) LIKE '%boulder%'
    """).format(sql.Identifier(backup_table)))

    conn.commit()

    cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(backup_table)))
    backup_count = cursor.fetchone()[0]

    return backup_table, backup_count


def delete_boulder_routes(cursor, conn):
    """Delete all boulder routes."""
    cursor.execute("""
        DELETE FROM mp_routes
        WHERE LOWER(type) LIKE '%boulder%'
    """)
    deleted_count = cursor.rowcount
    conn.commit()
    return deleted_count


def main():
    parser = argparse.ArgumentParser(description='Clean up boulder routes from mp_routes table')
    parser.add_argument('--dry-run', action='store_true', help='Only count, do not delete')
    parser.add_argument('--execute', action='store_true', help='Actually delete routes')
    parser.add_argument('--backup', action='store_true', help='Create backup table before deletion')
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("Please specify --dry-run or --execute")
        print("\nExamples:")
        print("  python scripts/cleanup_boulder_routes.py --dry-run")
        print("  python scripts/cleanup_boulder_routes.py --execute --backup")
        sys.exit(1)

    try:
        conn = get_connection()
        cursor = conn.cursor()

        print("=" * 60)
        print("SafeAscent Boulder Route Cleanup")
        print("=" * 60)

        # Count total routes
        total = count_total_routes(cursor)
        print(f"\nTotal routes in database: {total:,}")

        # Show top route types
        print("\nTop route types by count:")
        print("-" * 40)
        for route_type, count in count_routes_by_type(cursor):
            pct = (count / total) * 100
            print(f"  {route_type:20} {count:>8,} ({pct:5.1f}%)")

        # Count boulder routes
        print("\nBoulder routes to delete:")
        print("-" * 40)
        boulder_counts = count_boulder_routes(cursor)
        total_boulders = 0
        for route_type, count in boulder_counts:
            print(f"  {route_type:20} {count:>8,}")
            total_boulders += count

        if total_boulders == 0:
            print("  (No boulder routes found)")
            print("\nNothing to delete. Exiting.")
            return

        print(f"\n  TOTAL BOULDER ROUTES: {total_boulders:,}")
        print(f"  Routes remaining after deletion: {total - total_boulders:,}")

        if args.dry_run:
            print("\n" + "=" * 60)
            print("DRY RUN - No changes made")
            print("Run with --execute to delete these routes")
            print("=" * 60)
            return

        # Execute deletion
        print("\n" + "=" * 60)
        print("EXECUTING DELETION")
        print("=" * 60)

        if args.backup:
            print("\nCreating backup table...")
            backup_table, backup_count = create_backup_table(cursor, conn)
            print(f"  ✅ Created backup: {backup_table}")
            print(f"  ✅ Backed up {backup_count:,} rows")

        print("\nDeleting boulder routes...")
        deleted = delete_boulder_routes(cursor, conn)
        print(f"  ✅ Deleted {deleted:,} boulder routes")

        # Verify
        new_total = count_total_routes(cursor)
        print(f"\n  New total routes: {new_total:,}")
        print(f"  Reduction: {total - new_total:,} routes removed")

        # Verify no boulders remain
        remaining_boulders = count_boulder_routes(cursor)
        if remaining_boulders:
            print("\n  ⚠️ WARNING: Some boulder routes still remain!")
            for route_type, count in remaining_boulders:
                print(f"    {route_type}: {count}")
        else:
            print("\n  ✅ All boulder routes successfully removed")

        cursor.close()
        conn.close()

        print("\n" + "=" * 60)
        print("CLEANUP COMPLETE")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
