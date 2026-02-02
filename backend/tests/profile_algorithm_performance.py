"""
Profile Algorithm Performance

Identifies bottlenecks in the safety prediction algorithm using cProfile.
Focuses on where time is actually spent during prediction.
"""
import cProfile
import pstats
import io
from datetime import date
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.main import app

client = TestClient(app)

print("=" * 80)
print("ALGORITHM PERFORMANCE PROFILING")
print("=" * 80)
print()

# Longs Peak prediction request
request_data = {
    "latitude": 40.255,
    "longitude": -105.615,
    "route_type": "alpine",
    "planned_date": "2026-07-15",
}

print("📍 Test Location: Longs Peak (40.255, -105.615)")
print("🏔️  Route Type: Alpine")
print("📅 Date: 2026-07-15")
print()

# Create profiler
profiler = cProfile.Profile()

print("⏱️  Running profiled prediction request...")
print()

# Profile the prediction
profiler.enable()
response = client.post("/api/v1/predict", json=request_data)
profiler.disable()

# Check response
if response.status_code == 200:
    data = response.json()
    print(f"✓ Prediction successful")
    print(f"  Risk Score: {data['risk_score']:.1f}/100")
    print(f"  Confidence: {data['confidence']:.0f}%")
    print(f"  Accidents: {data['num_contributing_accidents']}")
    print()
else:
    print(f"✗ Error: {response.status_code}")
    print(response.json())
    sys.exit(1)

# Analyze profiling results
print("=" * 80)
print("PROFILING RESULTS - TOP 30 TIME CONSUMERS")
print("=" * 80)
print()

s = io.StringIO()
ps = pstats.Stats(profiler, stream=s)
ps.strip_dirs()
ps.sort_stats('cumulative')
ps.print_stats(30)

output = s.getvalue()
print(output)

print()
print("=" * 80)
print("PROFILING RESULTS - TOP 30 BY INTERNAL TIME")
print("=" * 80)
print()

s = io.StringIO()
ps = pstats.Stats(profiler, stream=s)
ps.strip_dirs()
ps.sort_stats('time')
ps.print_stats(30)

output = s.getvalue()
print(output)

print()
print("=" * 80)
print("KEY FINDINGS")
print("=" * 80)
print()
print("Look for:")
print("  1. Functions with high 'tottime' (internal computation time)")
print("  2. Functions called many times (ncalls >> 1)")
print("  3. Our algorithm functions (calculate_spatial_weight, etc.)")
print()
print("Next steps:")
print("  - Vectorize functions called 2000+ times")
print("  - Consider NumPy for batch operations")
print("  - Cache expensive calculations")
print()
