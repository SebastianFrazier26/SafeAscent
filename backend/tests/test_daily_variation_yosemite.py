"""Test daily variation in a moderate-density area (Yosemite)."""
import pytest


class TestDailyVariationYosemite:
    """Test that risk scores vary day-by-day in Yosemite (196 accidents)."""

    def test_yosemite_consecutive_days(self, test_client):
        """Test consecutive days in Yosemite to see if we get variation."""
        base_request = {
            "latitude": 37.7459,
            "longitude": -119.5332,
            "route_type": "alpine",
            "search_radius_km": 50.0
        }

        dates = ["2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17", "2026-07-18"]
        
        print("\n   Yosemite Half Dome - Consecutive Days:")
        for date in dates:
            request = {**base_request, "planned_date": date}
            response = test_client.post("/api/v1/predict", json=request)
            
            assert response.status_code == 200
            data = response.json()
            
            print(f"   {date} → Risk: {data['risk_score']:.2f}/100 | Accidents: {data['num_contributing_accidents']}")

    def test_yosemite_different_weeks(self, test_client):
        """Test dates 2 weeks apart in Yosemite."""
        base_request = {
            "latitude": 37.7459,
            "longitude": -119.5332,
            "route_type": "alpine",
            "search_radius_km": 50.0
        }

        dates = ["2026-07-01", "2026-07-15", "2026-07-30"]
        
        print("\n   Yosemite Half Dome - Different Weeks:")
        for date in dates:
            request = {**base_request, "planned_date": date}
            response = test_client.post("/api/v1/predict", json=request)
            
            assert response.status_code == 200
            data = response.json()
            
            print(f"   {date} → Risk: {data['risk_score']:.2f}/100")
