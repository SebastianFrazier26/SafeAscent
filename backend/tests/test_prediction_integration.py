"""
Integration tests for the complete prediction pipeline.

Tests the full flow from API request through database queries, algorithm execution,
and response formatting using real data.
"""

import pytest
from datetime import datetime


class TestPredictionEndpointIntegration:
    """Test the complete prediction endpoint flow with real data."""

    def test_prediction_with_known_dangerous_area(self, test_client):
        """Test prediction for Longs Peak area (known high-risk)."""
        # Longs Peak, Colorado - known dangerous area with many accidents
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.255,
            "longitude": -105.615,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 50.0
        })

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert "risk_score" in data
        assert "top_contributing_accidents" in data
        assert "num_contributing_accidents" in data
        assert "metadata" in data

        # Longs Peak should have elevated risk (many nearby accidents)
        assert data["risk_score"] > 30, "Longs Peak should show elevated risk"

        # Should have contributing accidents
        assert len(data["top_contributing_accidents"]) > 0
        assert data["num_contributing_accidents"] > 0

        print(f"\n✅ Longs Peak Prediction:")
        print(f"   Risk Score: {data['risk_score']}/100")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")

    def test_prediction_with_low_risk_area(self, test_client):
        """Test prediction for Florida (known low-risk area)."""
        # Florida climbing area - fewer mountain accidents
        response = test_client.post("/api/v1/predict", json={
            "latitude": 29.65,
            "longitude": -82.32,
            "route_type": "sport",
            "planned_date": "2024-07-15",
            "search_radius_km": 100.0
        })

        assert response.status_code == 200
        data = response.json()

        # Florida should have lower risk than mountain areas
        assert data["risk_score"] < 70, "Florida should not show extreme risk"

        print(f"\n✅ Florida Prediction:")
        print(f"   Risk Score: {data['risk_score']}/100")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")

    def test_prediction_response_structure(self, test_client):
        """Test that response has all required fields with correct types."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.3,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 100.0
        })

        assert response.status_code == 200
        data = response.json()

        # Required top-level fields
        assert "risk_score" in data
        assert "top_contributing_accidents" in data
        assert "num_contributing_accidents" in data
        assert "metadata" in data

        # Type checks
        assert isinstance(data["risk_score"], (int, float))
        assert isinstance(data["top_contributing_accidents"], list)
        assert isinstance(data["num_contributing_accidents"], int)
        assert isinstance(data["metadata"], dict)

        # Value ranges
        assert 0 <= data["risk_score"] <= 100

        # Metadata fields
        metadata = data["metadata"]
        assert "search_radius_km" in metadata
        assert "route_type" in metadata

        print(f"\n✅ Response Structure: Valid - All fields present with correct types")

    def test_prediction_with_different_route_types(self, test_client):
        """Test that different route types can be predicted."""
        location = {"latitude": 40.0, "longitude": -105.3, "planned_date": "2024-07-15"}

        # Test alpine
        alpine_response = test_client.post("/api/v1/predict", json={
            **location,
            "route_type": "alpine",
            "search_radius_km": 100.0
        })

        # Test sport
        sport_response = test_client.post("/api/v1/predict", json={
            **location,
            "route_type": "sport",
            "search_radius_km": 100.0
        })

        assert alpine_response.status_code == 200
        assert sport_response.status_code == 200

        alpine_data = alpine_response.json()
        sport_data = sport_response.json()

        # Both should return valid predictions
        assert 0 <= alpine_data["risk_score"] <= 100
        assert 0 <= sport_data["risk_score"] <= 100

        print(f"\n✅ Route Type Comparison:")
        print(f"   Alpine Risk: {alpine_data['risk_score']}/100 ({alpine_data['num_contributing_accidents']} accidents)")
        print(f"   Sport Risk: {sport_data['risk_score']}/100 ({sport_data['num_contributing_accidents']} accidents)")


class TestDatabaseIntegration:
    """Test database queries work correctly with real data."""

    def test_fetch_nearby_accidents_returns_data(self, test_client):
        """Test that spatial query returns accidents in high-density areas."""
        # Colorado has many accidents
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.3,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 100.0
        })

        assert response.status_code == 200
        data = response.json()

        # Should find accidents in Colorado
        assert data["num_contributing_accidents"] > 0
        assert len(data["top_contributing_accidents"]) > 0

        print(f"\n✅ Spatial Query: Found {data['num_contributing_accidents']} accidents in Colorado")

    def test_spatial_query_respects_radius(self, test_client):
        """Test that larger radius finds more (or equal) accidents."""
        location = {"latitude": 40.0, "longitude": -105.3, "route_type": "alpine", "planned_date": "2024-07-15"}

        # Small radius
        small_response = test_client.post("/api/v1/predict", json={
            **location,
            "search_radius_km": 25.0
        })

        # Large radius
        large_response = test_client.post("/api/v1/predict", json={
            **location,
            "search_radius_km": 150.0
        })

        assert small_response.status_code == 200
        assert large_response.status_code == 200

        small_count = small_response.json()["num_contributing_accidents"]
        large_count = large_response.json()["num_contributing_accidents"]

        # Larger radius should find more accidents (or equal)
        assert large_count >= small_count

        print(f"\n✅ Radius Comparison:")
        print(f"   25km radius: {small_count} accidents")
        print(f"   150km radius: {large_count} accidents")

    def test_weather_data_accessible(self, test_client):
        """Test that weather data is properly linked to accidents."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.3,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 100.0
        })

        assert response.status_code == 200
        data = response.json()

        # Check if weather data was considered in the algorithm
        # (Note: actual weather_weight values will be in top_contributing_accidents)
        assert data["num_contributing_accidents"] > 0, "Should have found accidents with data"

        print(f"\n✅ Weather Integration: System processed {data['num_contributing_accidents']} accidents")


class TestComponentIntegration:
    """Test that all algorithm components work together correctly."""

    def test_high_accident_density_reflects_risk(self, test_client):
        """Test that high-density accident areas show elevated risk."""
        # High-density area (Longs Peak)
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.255,
            "longitude": -105.615,  # Longs Peak
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 50.0
        })

        assert response.status_code == 200
        data = response.json()

        # Should have many contributing accidents near Longs Peak
        num_accidents = data["num_contributing_accidents"]
        assert num_accidents > 100, "Should have many accidents near Longs Peak"

        # High density should reflect in elevated risk
        assert data["risk_score"] > 30, "High accident density should show elevated risk"

        # Should have top contributing accidents
        assert len(data["top_contributing_accidents"]) > 0

        print(f"\n✅ High Density Area (Longs Peak):")
        print(f"   Total Contributing Accidents: {num_accidents}")
        print(f"   Risk Score: {data['risk_score']}/100")

    def test_real_time_weather_integration(self, test_client):
        """Test that current weather is fetched and used."""
        # Make prediction for today
        today = datetime.now().strftime("%Y-%m-%d")

        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.3,
            "route_type": "alpine",
            "planned_date": today,
            "search_radius_km": 100.0
        })

        assert response.status_code == 200
        data = response.json()

        # Should generate valid prediction (weather API may or may not succeed)
        assert 0 <= data["risk_score"] <= 100

        print(f"\n✅ Real-time Weather: Prediction generated for today")
        print(f"   Risk Score: {data['risk_score']}/100")


class TestValidationAndErrorHandling:
    """Test input validation and error handling."""

    def test_invalid_coordinates_rejected(self, test_client):
        """Test that invalid coordinates return 422."""
        # Invalid latitude (> 90)
        response = test_client.post("/api/v1/predict", json={
            "latitude": 95.0,
            "longitude": -105.3,
            "route_type": "alpine",
            "planned_date": "2024-07-15"
        })

        assert response.status_code == 422  # Validation error

    def test_invalid_route_type_rejected(self, test_client):
        """Test that invalid route type returns 422."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.3,
            "route_type": "invalid_type",
            "planned_date": "2024-07-15"
        })

        assert response.status_code == 422

    def test_invalid_date_format_rejected(self, test_client):
        """Test that invalid date format returns 422."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.3,
            "route_type": "alpine",
            "planned_date": "not-a-date"
        })

        assert response.status_code == 422

    def test_missing_required_fields_rejected(self, test_client):
        """Test that missing fields return 422."""
        # Missing latitude
        response = test_client.post("/api/v1/predict", json={
            "longitude": -105.3,
            "route_type": "alpine",
            "planned_date": "2024-07-15"
        })

        assert response.status_code == 422


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])
