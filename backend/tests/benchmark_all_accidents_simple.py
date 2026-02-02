"""
Simple benchmark: Single prediction request with all accidents
"""
import time
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.main import app

client = TestClient(app)

print("=" * 80)
print("ALL ACCIDENTS - SINGLE REQUEST BENCHMARK")
print("=" * 80)
print()

request_data = {
    "latitude": 40.255,
    "longitude": -105.615,
    "route_type": "alpine",
    "planned_date": "2026-07-15",
}

print("📍 Location: Longs Peak")
print("🏔️  Route: Alpine")
print()

print("⏱️  Making prediction request...")
start_time = time.time()
response = client.post("/api/v1/predict", json=request_data)
elapsed = time.time() - start_time

print(f"✓ Response code: {response.status_code}")
print(f"✓ Time: {elapsed:.2f} seconds")
print()

if response.status_code == 200:
    data = response.json()
    print("📊 RESULTS:")
    print(f"  Risk score: {data['risk_score']:.1f}/100")
    print(f"  Confidence: {data['confidence']:.0f}%")
    print(f"  Contributing accidents: {data['num_contributing_accidents']}")
    print()

    print("COMPARISON:")
    print(f"  OLD (476 accidents, 50km filter):  ~0.55s")
    print(f"  NEW (all accidents, no filter):     {elapsed:.2f}s")
    print(f"  Slowdown: {elapsed / 0.55:.1f}×")
    print()

    if elapsed < 3.0:
        print("✅ ACCEPTABLE performance (< 3 seconds)")
    elif elapsed < 10.0:
        print("⚠️  SLOW but usable (3-10 seconds)")
    else:
        print("❌ TOO SLOW (> 10 seconds) - needs optimization")
else:
    print(f"❌ ERROR: {response.status_code}")
    print(response.json())

print("=" * 80)
