"""Test daily variation at Longs Peak with new normalization."""
import pytest


class TestLongsPeakDaily:
    def test_longs_peak_consecutive_days(self, test_client):
        """Test consecutive days at Longs Peak (476 accidents)."""
        base_request = {
            "latitude": 40.255,
            "longitude": -105.615,
            "route_type": "alpine",
            "search_radius_km": 50.0
        }

        dates = ["2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17", "2026-07-18"]
        
        print("\n   Longs Peak - Consecutive Days (Quadratic + Normalization=5.0):")
        for date in dates:
            request = {**base_request, "planned_date": date}
            response = test_client.post("/api/v1/predict", json=request)
            
            assert response.status_code == 200
            data = response.json()
            
            print(f"   {date} → Risk: {data['risk_score']:.2f}/100")

    def test_longs_peak_different_seasons(self, test_client):
        """Test different seasons at Longs Peak."""
        base_request = {
            "latitude": 40.255,
            "longitude": -105.615,
            "route_type": "alpine",
            "search_radius_km": 50.0
        }

        dates = [
            ("2026-01-15", "Winter"),
            ("2026-05-15", "Spring"),
            ("2026-07-15", "Summer"),
            ("2026-09-15", "Fall")
        ]
        
        print("\n   Longs Peak - Seasonal Variation:")
        for date, season in dates:
            request = {**base_request, "planned_date": date}
            response = test_client.post("/api/v1/predict", json=request)
            
            assert response.status_code == 200
            data = response.json()
            
            print(f"   {season:8s} ({date}) → Risk: {data['risk_score']:.2f}/100")
