#!/usr/bin/env python3
"""
Manual Safety Cache Population Script

Runs the safety score pre-computation directly (without Celery).
Use this to populate the cache immediately instead of waiting for the nightly job.

Usage:
    # Set environment variables first
    export DATABASE_URL="postgresql+asyncpg://..."
    export REDIS_URL="redis://..."

    # Run for all 7 days
    python scripts/populate_safety_cache.py

    # Run for specific date only
    python scripts/populate_safety_cache.py --date 2026-02-04

    # Run for just today
    python scripts/populate_safety_cache.py --today-only
"""
import os
import sys
import asyncio
import argparse
from datetime import date, timedelta
import time

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

# Load environment
from dotenv import load_dotenv
load_dotenv()


def main():
    parser = argparse.ArgumentParser(description='Populate safety score cache')
    parser.add_argument('--date', type=str, help='Specific date (YYYY-MM-DD)')
    parser.add_argument('--today-only', action='store_true', help='Only compute for today')
    parser.add_argument('--days', type=int, default=7, help='Number of days to compute (default: 7)')
    args = parser.parse_args()

    print("=" * 60)
    print("SafeAscent Safety Cache Population")
    print("=" * 60)

    # Verify environment
    db_url = os.getenv('DATABASE_URL', '')
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

    if not db_url:
        print("❌ DATABASE_URL not set!")
        print("   Set it to your Neon connection string:")
        print("   export DATABASE_URL='postgresql+asyncpg://...'")
        sys.exit(1)

    print(f"Database: {db_url[:50]}...")
    print(f"Redis: {redis_url}")
    print()

    # Import after environment is loaded
    from app.tasks.safety_computation import (
        _compute_all_safety_scores_async,
        _compute_single_date_async,
        BATCH_SIZE,
        CONCURRENCY_LIMIT,
    )

    print(f"Settings: BATCH_SIZE={BATCH_SIZE}, CONCURRENCY={CONCURRENCY_LIMIT}")
    print()

    if args.date:
        # Single date mode
        print(f"Computing safety scores for: {args.date}")
        result = asyncio.run(_compute_single_date_async(args.date))
    elif args.today_only:
        # Today only mode
        today_str = date.today().isoformat()
        print(f"Computing safety scores for today: {today_str}")
        result = asyncio.run(_compute_single_date_async(today_str))
    else:
        # Full 7-day mode
        print(f"Computing safety scores for next {args.days} days...")
        result = asyncio.run(_compute_all_safety_scores_async())

    print()
    print("=" * 60)
    print("RESULT:", result)
    print("=" * 60)


if __name__ == '__main__':
    main()
