"""Quick script to check actual risk scores with cubic weather weighting."""
import sys
sys.path.insert(0, '/Users/sebastianfrazier/SafeAscent/backend')

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# Test scenarios
test_scenarios = [
    {
        "name": "Half Dome (Yosemite) - Summer",
        "data": {
            "latitude": 37.7459,
            "longitude": -119.5332,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 50.0
        }
    },
    {
        "name": "Longs Peak (Colorado) - Summer",
        "data": {
            "latitude": 40.255,
            "longitude": -105.615,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 50.0
        }
    },
    {
        "name": "Smith Rock (Oregon) - Low Risk Sport Climbing",
        "data": {
            "latitude": 44.3672,
            "longitude": -121.1408,
            "route_type": "sport",
            "planned_date": "2026-06-15",
            "search_radius_km": 50.0
        }
    },
    {
        "name": "Florida - Very Low Risk",
        "data": {
            "latitude": 28.0,
            "longitude": -81.0,
            "route_type": "sport",
            "planned_date": "2026-03-15",
            "search_radius_km": 50.0
        }
    }
]

print("\n" + "="*80)
print("RISK SCORE ANALYSIS - Cubic Weather Weighting")
print("="*80 + "\n")

for scenario in test_scenarios:
    response = client.post("/api/v1/predict/safety-score", json=scenario["data"])

    if response.status_code == 200:
        data = response.json()
        print(f"📍 {scenario['name']}")
        print(f"   Risk Score: {data['risk_score']:.1f}/100")
        print(f"   Confidence: {data['confidence']:.1f}/100 ({data['confidence_interpretation']})")
        print(f"   Contributing Accidents: {data['num_contributing_accidents']}")

        if data['top_contributing_accidents']:
            print(f"   Top Accident Influence: {data['top_contributing_accidents'][0]['total_influence']:.4f}")
            print(f"   Top Accident Distance: {data['top_contributing_accidents'][0]['distance_km']:.1f} km")
        print()
    else:
        print(f"❌ {scenario['name']}: Error {response.status_code}")
        print()

print("="*80)
print("EXPECTED RANGES (from design discussion):")
print("  - Half Dome, sunny day: 15-25")
print("  - Half Dome, stormy day: ~100")
print("  - Safe route, sunny: 10-18")
print("  - Safe route, stormy: ~70")
print("="*80 + "\n")
