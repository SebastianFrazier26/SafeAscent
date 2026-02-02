"""
Benchmark: Vectorized vs Loop-Based Algorithm

Compares performance of NumPy-vectorized algorithm against the original
loop-based implementation to measure actual speedup achieved.

Expected speedup: 3-5× faster for ~2,500 accidents
"""
import time
import sys
import os
import asyncio
from datetime import date
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.safety_algorithm import calculate_safety_score, AccidentData
from app.services.safety_algorithm_vectorized import calculate_safety_score_vectorized
from app.services.weather_similarity import WeatherPattern
from app.models.accident import Accident
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, and_

load_dotenv()

print("=" * 80)
print("ALGORITHM VECTORIZATION BENCHMARK")
print("=" * 80)
print()

# Database connection
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in environment variables")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def fetch_test_data():
    """Fetch accidents for benchmarking."""
    async with AsyncSessionLocal() as db:
        # Fetch all valid accidents
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
        for acc in accidents[:2500]:  # Test with 2500 for consistency
            accident_data = AccidentData(
                accident_id=acc.accident_id,
                latitude=acc.latitude,
                longitude=acc.longitude,
                elevation_meters=acc.elevation_meters,
                accident_date=acc.date,
                route_type="alpine",  # Simplified for benchmark
                severity=acc.injury_severity or "unknown",
                weather_pattern=None,  # Simplified - no weather
            )
            accident_data_list.append(accident_data)

        return accident_data_list


def run_loop_based_algorithm(
    route_lat, route_lon, route_elevation, route_type, current_date,
    current_weather, accidents, historical_weather_stats
):
    """Run the original loop-based algorithm."""
    return calculate_safety_score(
        route_lat=route_lat,
        route_lon=route_lon,
        route_elevation_m=route_elevation,
        route_type=route_type,
        current_date=current_date,
        current_weather=current_weather,
        accidents=accidents,
        historical_weather_stats=historical_weather_stats,
    )


def run_vectorized_algorithm(
    route_lat, route_lon, route_elevation, route_type, current_date,
    current_weather, accidents, historical_weather_stats
):
    """Run the NumPy-vectorized algorithm."""
    return calculate_safety_score_vectorized(
        route_lat=route_lat,
        route_lon=route_lon,
        route_elevation_m=route_elevation,
        route_type=route_type,
        current_date=current_date,
        current_weather=current_weather,
        accidents=accidents,
        historical_weather_stats=historical_weather_stats,
    )


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
    historical_weather_stats = None

    print("=" * 80)
    print("BENCHMARK PARAMETERS")
    print("=" * 80)
    print(f"  Location: {route_lat}, {route_lon}")
    print(f"  Elevation: {route_elevation}m")
    print(f"  Route Type: {route_type}")
    print(f"  Accidents: {len(accident_data)}")
    print()

    # Warmup run (JIT compilation, cache loading, etc.)
    print("🔥 Running warmup iteration...")
    _ = run_loop_based_algorithm(
        route_lat, route_lon, route_elevation, route_type, current_date,
        current_weather, accident_data, historical_weather_stats
    )
    _ = run_vectorized_algorithm(
        route_lat, route_lon, route_elevation, route_type, current_date,
        current_weather, accident_data, historical_weather_stats
    )
    print("✓ Warmup complete")
    print()

    # Benchmark: Loop-Based Algorithm
    print("=" * 80)
    print("LOOP-BASED ALGORITHM (Original)")
    print("=" * 80)
    print()

    num_iterations = 5
    loop_times = []

    for i in range(num_iterations):
        start = time.perf_counter()
        prediction_loop = run_loop_based_algorithm(
            route_lat, route_lon, route_elevation, route_type, current_date,
            current_weather, accident_data, historical_weather_stats
        )
        end = time.perf_counter()
        elapsed = end - start
        loop_times.append(elapsed)
        print(f"  Run {i+1}: {elapsed*1000:.1f}ms")

    avg_loop_time = sum(loop_times) / len(loop_times)
    min_loop_time = min(loop_times)
    max_loop_time = max(loop_times)

    print()
    print(f"  Average: {avg_loop_time*1000:.1f}ms")
    print(f"  Min: {min_loop_time*1000:.1f}ms")
    print(f"  Max: {max_loop_time*1000:.1f}ms")
    print(f"  Risk Score: {prediction_loop.risk_score:.1f}/100")
    print()

    # Benchmark: Vectorized Algorithm
    print("=" * 80)
    print("VECTORIZED ALGORITHM (NumPy)")
    print("=" * 80)
    print()

    vectorized_times = []

    for i in range(num_iterations):
        start = time.perf_counter()
        prediction_vec = run_vectorized_algorithm(
            route_lat, route_lon, route_elevation, route_type, current_date,
            current_weather, accident_data, historical_weather_stats
        )
        end = time.perf_counter()
        elapsed = end - start
        vectorized_times.append(elapsed)
        print(f"  Run {i+1}: {elapsed*1000:.1f}ms")

    avg_vec_time = sum(vectorized_times) / len(vectorized_times)
    min_vec_time = min(vectorized_times)
    max_vec_time = max(vectorized_times)

    print()
    print(f"  Average: {avg_vec_time*1000:.1f}ms")
    print(f"  Min: {min_vec_time*1000:.1f}ms")
    print(f"  Max: {max_vec_time*1000:.1f}ms")
    print(f"  Risk Score: {prediction_vec.risk_score:.1f}/100")
    print()

    # Calculate speedup
    speedup = avg_loop_time / avg_vec_time
    time_saved = avg_loop_time - avg_vec_time

    print("=" * 80)
    print("PERFORMANCE COMPARISON")
    print("=" * 80)
    print()
    print(f"  Loop-based (avg): {avg_loop_time*1000:.1f}ms")
    print(f"  Vectorized (avg): {avg_vec_time*1000:.1f}ms")
    print()
    print(f"  ⚡ Speedup: {speedup:.2f}×")
    print(f"  ⏱️  Time saved: {time_saved*1000:.1f}ms per prediction")
    print()

    # Verify results match (approximately)
    risk_diff = abs(prediction_loop.risk_score - prediction_vec.risk_score)
    print(f"  Risk score difference: {risk_diff:.4f}")
    if risk_diff < 0.1:
        print("  ✅ Results match (difference < 0.1)")
    else:
        print(f"  ⚠️  Results differ by {risk_diff:.4f} (may need investigation)")
    print()

    # Interpretation
    print("=" * 80)
    print("ANALYSIS")
    print("=" * 80)
    print()

    if speedup >= 3.0:
        print(f"  ✅ EXCELLENT: {speedup:.2f}× speedup achieved (target: 3-5×)")
    elif speedup >= 2.0:
        print(f"  ✅ GOOD: {speedup:.2f}× speedup achieved (slightly below 3× target)")
    elif speedup >= 1.5:
        print(f"  ⚠️  MODERATE: {speedup:.2f}× speedup (below 3× target)")
    else:
        print(f"  ❌ POOR: {speedup:.2f}× speedup (vectorization not effective)")

    print()
    print("  NumPy vectorization eliminates Python loop overhead by:")
    print("  1. Batch operations on arrays (one C call vs 2500 Python calls)")
    print("  2. Optimized memory access patterns (cache-friendly)")
    print("  3. SIMD instructions for parallel computation")
    print()

    # Recommendation
    print("=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)
    print()

    if speedup >= 2.0:
        print("  ✅ Use vectorized algorithm as default (USE_VECTORIZED_ALGORITHM=true)")
        print(f"  Expected production performance: ~{avg_vec_time*1000:.0f}ms per prediction")
    else:
        print("  ⚠️  Investigate why vectorization is not effective")
        print("  Consider profiling to identify remaining bottlenecks")

    print()


if __name__ == "__main__":
    asyncio.run(main())
