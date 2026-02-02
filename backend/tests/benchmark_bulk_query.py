"""
Benchmark: Bulk Query Optimization Performance

Measures the performance improvement from switching from N+1 queries
to bulk fetching in fetch_accident_weather_patterns().

Compares:
- OLD: Loop through accidents, query weather for each (N+1 problem)
- NEW: Single bulk query with JOIN, group in Python

Expected improvement: 4-5× faster for high-density areas like Longs Peak
"""
import time
import asyncio
from datetime import date, timedelta
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

# Import models
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.models.accident import Accident
from app.models.weather import Weather

load_dotenv()

# Database connection
DB_USER = os.getenv('DB_USER', 'sebastianfrazier')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'safeascent')

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def fetch_weather_old_way(db: AsyncSession, accidents: list) -> dict:
    """
    OLD APPROACH: N+1 queries (one query per accident).

    This simulates the old implementation before optimization.
    """
    weather_map = {}

    for accident in accidents:
        if accident.date is None:
            weather_map[accident.accident_id] = None
            continue

        # Calculate date range
        end_date = accident.date
        start_date = end_date - timedelta(days=6)

        # Separate query for each accident
        stmt = select(Weather).where(
            and_(
                Weather.accident_id == accident.accident_id,
                Weather.date >= start_date,
                Weather.date <= end_date,
            )
        ).order_by(Weather.date)

        result = await db.execute(stmt)
        weather_records = result.scalars().all()

        weather_map[accident.accident_id] = list(weather_records)

    return weather_map


async def fetch_weather_new_way(db: AsyncSession, accidents: list) -> dict:
    """
    NEW APPROACH: Bulk query with JOIN.

    This is the optimized implementation using a single query.
    """
    weather_map = {}

    if not accidents:
        return weather_map

    # Filter out accidents without dates
    valid_accidents = [acc for acc in accidents if acc.date is not None]
    accident_ids = [acc.accident_id for acc in valid_accidents]

    for accident in accidents:
        if accident.date is None:
            weather_map[accident.accident_id] = None

    if not accident_ids:
        return weather_map

    # BULK QUERY: Single query with JOIN
    stmt = (
        select(Weather, Accident.date.label('accident_date'))
        .join(Accident, Weather.accident_id == Accident.accident_id)
        .where(
            and_(
                Weather.accident_id.in_(accident_ids),
                Weather.date >= Accident.date - timedelta(days=6),
                Weather.date <= Accident.date,
            )
        )
        .order_by(Weather.accident_id, Weather.date)
    )

    result = await db.execute(stmt)
    rows = result.all()

    # Group in Python
    from collections import defaultdict
    weather_by_accident = defaultdict(list)

    for weather_record, accident_date in rows:
        weather_by_accident[weather_record.accident_id].append(weather_record)

    for accident in valid_accidents:
        weather_records = weather_by_accident.get(accident.accident_id, [])
        weather_map[accident.accident_id] = weather_records

    return weather_map


async def run_benchmark():
    """Run performance benchmark comparing old vs new approaches."""

    async with AsyncSessionLocal() as db:
        # Fetch accidents from Longs Peak area (high density = worst case for N+1)
        from geoalchemy2.functions import ST_DWithin, ST_MakePoint
        from sqlalchemy import func

        print("="*80)
        print("BULK QUERY OPTIMIZATION BENCHMARK")
        print("="*80)
        print()

        # Longs Peak coordinates
        latitude, longitude = 40.255, -105.615
        radius_km = 50.0

        print(f"📍 Location: Longs Peak ({latitude}, {longitude})")
        print(f"🔍 Search radius: {radius_km}km")
        print()

        # Fetch accidents within radius
        point = ST_MakePoint(longitude, latitude)
        radius_meters = radius_km * 1000

        stmt = select(Accident).where(
            and_(
                Accident.coordinates.isnot(None),
                Accident.date.isnot(None),
                ST_DWithin(
                    Accident.coordinates,
                    func.ST_SetSRID(point, 4326),
                    radius_meters,
                ),
            )
        )

        result = await db.execute(stmt)
        accidents = result.scalars().all()

        print(f"✓ Found {len(accidents)} accidents")
        print()

        # Warm up (first query is always slower due to connection setup)
        print("🔥 Warming up database connection...")
        _ = await fetch_weather_new_way(db, accidents[:10])
        print("✓ Warmed up")
        print()

        # Benchmark OLD approach
        print("⏱️  OLD APPROACH: N+1 Queries (one per accident)")
        print("-" * 80)
        start_time = time.time()
        old_results = await fetch_weather_old_way(db, accidents)
        old_duration = time.time() - start_time
        print(f"Time: {old_duration:.4f} seconds")
        print(f"Queries: {len(accidents)} separate database queries")
        print(f"Weather records fetched: {sum(len(v) if v else 0 for v in old_results.values())}")
        print()

        # Benchmark NEW approach
        print("⚡ NEW APPROACH: Bulk Query with JOIN")
        print("-" * 80)
        start_time = time.time()
        new_results = await fetch_weather_new_way(db, accidents)
        new_duration = time.time() - start_time
        print(f"Time: {new_duration:.4f} seconds")
        print(f"Queries: 1 bulk query with JOIN")
        print(f"Weather records fetched: {sum(len(v) if v else 0 for v in new_results.values())}")
        print()

        # Calculate speedup
        speedup = old_duration / new_duration if new_duration > 0 else float('inf')
        time_saved = old_duration - new_duration

        print("="*80)
        print("📊 RESULTS")
        print("="*80)
        print(f"Old approach: {old_duration:.4f}s ({len(accidents)} queries)")
        print(f"New approach: {new_duration:.4f}s (1 query)")
        print(f"Time saved:   {time_saved:.4f}s ({time_saved * 1000:.1f}ms)")
        print(f"Speedup:      {speedup:.2f}× faster")
        print()

        # Verify correctness
        print("🔍 CORRECTNESS CHECK")
        print("-" * 80)

        # Check if results are identical
        if old_results.keys() == new_results.keys():
            print("✓ Both approaches returned same accident IDs")
        else:
            print("✗ Different accident IDs returned!")

        # Check record counts match
        mismatches = 0
        for acc_id in old_results.keys():
            old_count = len(old_results[acc_id]) if old_results[acc_id] else 0
            new_count = len(new_results[acc_id]) if new_results[acc_id] else 0
            if old_count != new_count:
                mismatches += 1

        if mismatches == 0:
            print("✓ Both approaches returned same number of weather records")
        else:
            print(f"✗ {mismatches} accidents have different record counts!")

        print()
        print("="*80)
        print("BENCHMARK COMPLETE")
        print("="*80)


if __name__ == "__main__":
    asyncio.run(run_benchmark())
