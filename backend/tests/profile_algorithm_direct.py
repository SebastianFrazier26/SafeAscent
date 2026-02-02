"""
Direct Algorithm Profiling

Profiles the safety_algorithm.calculate_safety_score function directly
without FastAPI/async overhead to see true computational bottlenecks.
"""
import cProfile
import pstats
import io
from datetime import date
import sys
import os
import asyncio
import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.safety_algorithm import calculate_safety_score, AccidentData
from app.services.weather_similarity import WeatherPattern
from app.models.accident import Accident
from app.models.weather import Weather
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, and_

load_dotenv()

print("=" * 80)
print("DIRECT ALGORITHM PROFILING")
print("=" * 80)
print()

# Database connection
DATABASE_URL = f"postgresql+asyncpg://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def fetch_test_data():
    """Fetch accidents for testing."""
    async with AsyncSessionLocal() as db:
        # Fetch all accidents
        stmt = select(Accident).where(
            and_(
                Accident.coordinates.isnot(None),
                Accident.date.isnot(None),
                Accident.latitude.isnot(None),
                Accident.longitude.isnot(None),
            )
        )
        result = await db.execute(stmt)
        accidents = result.scalars().all()

        # Convert to AccidentData objects
        accident_data_list = []
        for acc in accidents[:2500]:  # Take first 2500 for consistent testing
            accident_data = AccidentData(
                accident_id=acc.accident_id,
                latitude=acc.latitude,
                longitude=acc.longitude,
                elevation_meters=acc.elevation_meters,
                accident_date=acc.date,
                route_type="alpine",  # Simplified
                severity=acc.injury_severity or "unknown",
                weather_pattern=None,  # Simplified - no weather
            )
            accident_data_list.append(accident_data)

        return accident_data_list


async def main():
    print("📦 Fetching test data...")
    accident_data = await fetch_test_data()
    print(f"✓ Loaded {len(accident_data)} accidents")
    print()

    # Create dummy weather pattern
    current_weather = WeatherPattern(
        temperature=[10, 12, 14, 15, 16, 15, 14],
        precipitation=[0, 0, 5, 10, 5, 0, 0],
        wind_speed=[5, 6, 8, 10, 8, 6, 5],
        visibility=[10000] * 7,
        cloud_cover=[50, 60, 70, 80, 70, 60, 50],
        daily_temps=[(8, 10, 12), (10, 12, 14), (12, 14, 16), (13, 15, 17),
                     (14, 16, 18), (13, 15, 17), (12, 14, 16)],
    )

    # Longs Peak coordinates
    route_lat = 40.255
    route_lon = -105.615
    route_elevation = 4346.0
    route_type = "alpine"
    current_date = date(2026, 7, 15)

    print("⏱️  Profiling algorithm computation...")
    print(f"   Location: {route_lat}, {route_lon}")
    print(f"   Elevation: {route_elevation}m")
    print(f"   Accidents to process: {len(accident_data)}")
    print()

    # Profile the algorithm
    profiler = cProfile.Profile()
    profiler.enable()

    prediction = calculate_safety_score(
        route_lat=route_lat,
        route_lon=route_lon,
        route_elevation_m=route_elevation,
        route_type=route_type,
        current_date=current_date,
        current_weather=current_weather,
        accidents=accident_data,
        historical_weather_stats=None,
    )

    profiler.disable()

    print(f"✓ Prediction complete:")
    print(f"  Risk Score: {prediction.risk_score:.1f}/100")
    print(f"  Confidence: {prediction.confidence:.0f}%")
    print(f"  Contributing accidents: {prediction.num_contributing_accidents}")
    print()

    # Analyze results
    print("=" * 80)
    print("TOP 40 FUNCTIONS BY CUMULATIVE TIME")
    print("=" * 80)
    print()

    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s)
    ps.strip_dirs()
    ps.sort_stats('cumulative')
    ps.print_stats(40)
    print(s.getvalue())

    print()
    print("=" * 80)
    print("TOP 40 FUNCTIONS BY INTERNAL TIME (tottime)")
    print("=" * 80)
    print()

    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s)
    ps.strip_dirs()
    ps.sort_stats('time')
    ps.print_stats(40)
    print(s.getvalue())

    print()
    print("=" * 80)
    print("KEY OPTIMIZATION TARGETS")
    print("=" * 80)
    print()
    print("Focus on functions with:")
    print("  1. High tottime (internal computation)")
    print("  2. High ncalls (called many times)")
    print("  3. ncalls ~= number of accidents (2500)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
