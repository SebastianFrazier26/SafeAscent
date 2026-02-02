"""
Benchmark: All Accidents Performance Test

Measures the performance impact of querying all accidents instead of
spatial filtering to 50km radius.

Compares:
- OLD: 476 accidents within 50km of Longs Peak
- NEW: ~10,000 accidents (all with valid coordinates/dates)

Expected performance impact:
- Database query: 37ms → 50-100ms
- Weather queries: 15ms → 15ms (bulk query handles any count)
- Algorithm: 500ms → ~10 seconds (needs optimization)
"""
import time
import asyncio
from datetime import date
from fastapi.testclient import TestClient
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.main import app

client = TestClient(app)


def test_prediction_performance():
    """Benchmark prediction endpoint with all accidents."""

    print("=" * 80)
    print("ALL ACCIDENTS PERFORMANCE BENCHMARK")
    print("=" * 80)
    print()

    # Test location: Longs Peak (high-density area)
    request_data = {
        "latitude": 40.255,
        "longitude": -105.615,
        "route_type": "alpine",
        "planned_date": "2026-07-15",
    }

    print("📍 Location: Longs Peak (40.255, -105.615)")
    print("🏔️  Route type: Alpine")
    print("📅 Date: 2026-07-15")
    print()

    # Warm up
    print("🔥 Warming up (first request always slower)...")
    response = client.post("/api/v1/predict", json=request_data)
    print(f"   Warm-up complete: {response.status_code}")
    print()

    # Benchmark multiple requests
    print("⏱️  Running benchmark (5 predictions)...")
    print("-" * 80)

    times = []
    for i in range(5):
        start_time = time.time()
        response = client.post("/api/v1/predict", json=request_data)
        elapsed = time.time() - start_time

        if response.status_code == 200:
            data = response.json()
            risk_score = data['risk_score']
            confidence = data['confidence']
            num_accidents = data['num_contributing_accidents']

            print(f"  Request {i+1}: {elapsed:.2f}s "
                  f"(Risk: {risk_score:.1f}, Confidence: {confidence:.0f}%, "
                  f"Accidents: {num_accidents})")
            times.append(elapsed)
        else:
            print(f"  Request {i+1}: ERROR {response.status_code}")

    print()

    # Calculate statistics
    if times:
        avg_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)

        print("=" * 80)
        print("📊 RESULTS")
        print("=" * 80)
        print(f"Average time: {avg_time:.2f}s")
        print(f"Min time:     {min_time:.2f}s")
        print(f"Max time:     {max_time:.2f}s")
        print()

        # Compare to expected baseline
        baseline_time = 0.55  # ~550ms with spatial filtering (476 accidents)
        slowdown = avg_time / baseline_time

        print("COMPARISON TO BASELINE (spatial filter = 50km)")
        print("-" * 80)
        print(f"Baseline (476 accidents):     ~{baseline_time:.2f}s")
        print(f"Current (all accidents):      ~{avg_time:.2f}s")
        print(f"Slowdown factor:              {slowdown:.1f}×")
        print()

        if avg_time < 2.0:
            print("✅ Performance: EXCELLENT (< 2 seconds)")
        elif avg_time < 5.0:
            print("⚠️  Performance: ACCEPTABLE (2-5 seconds, optimization recommended)")
        else:
            print("❌ Performance: SLOW (> 5 seconds, optimization required)")

        print()

    print("=" * 80)
    print("BENCHMARK COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    test_prediction_performance()
