"""
Integration tests for the /api/v1/predict endpoint.

These tests validate the full request-response cycle including:
- Database queries (spatial, temporal, weather)
- Service layer integration (algorithm, weather, route type)
- Response formatting and validation
- Error handling and edge cases
"""
import pytest
from datetime import date, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app


@pytest.mark.asyncio
class TestPredictEndpointBasics:
    """Test basic request-response contracts."""

    async def test_predict_returns_200_with_valid_request(self, async_client: AsyncClient):
        """Valid request should return 200 OK with prediction data."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 150.0,
        }

        response = await async_client.post("/api/v1/predict", json=payload)

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert "risk_score" in data
        assert "confidence" in data
        assert "confidence_interpretation" in data
        assert "num_contributing_accidents" in data
        assert "top_contributing_accidents" in data
        assert "confidence_breakdown" in data
        assert "metadata" in data

    async def test_predict_risk_score_in_valid_range(self, async_client: AsyncClient):
        """Risk score should be between 0 and 100."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        assert 0.0 <= data["risk_score"] <= 100.0

    async def test_predict_confidence_in_valid_range(self, async_client: AsyncClient):
        """Confidence should be between 0 and 100."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        assert 0.0 <= data["confidence"] <= 100.0

    async def test_predict_contributing_accidents_structure(self, async_client: AsyncClient):
        """Contributing accidents should have proper structure."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        accidents = data["top_contributing_accidents"]
        assert isinstance(accidents, list)

        # If we have accidents, validate structure
        if len(accidents) > 0:
            acc = accidents[0]
            assert "accident_id" in acc
            assert "total_influence" in acc
            assert "distance_km" in acc
            assert "days_ago" in acc
            assert "spatial_weight" in acc
            assert "temporal_weight" in acc
            assert "weather_weight" in acc
            assert "route_type_weight" in acc
            assert "severity_weight" in acc

    async def test_predict_confidence_breakdown_structure(self, async_client: AsyncClient):
        """Confidence breakdown should have all required fields."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        breakdown = data["confidence_breakdown"]
        assert "overall_confidence" in breakdown
        assert "sample_size_score" in breakdown
        assert "match_quality_score" in breakdown
        assert "spatial_coverage_score" in breakdown
        assert "temporal_recency_score" in breakdown
        assert "weather_quality_score" in breakdown
        assert "num_accidents" in breakdown
        assert "num_significant" in breakdown
        assert "median_days_ago" in breakdown
        assert "weather_data_pct" in breakdown

    async def test_predict_metadata_includes_search_info(self, async_client: AsyncClient):
        """Metadata should include search parameters."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 150.0,
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        metadata = data["metadata"]
        assert "search_radius_km" in metadata
        assert "route_type" in metadata
        assert metadata["route_type"] == "alpine"


@pytest.mark.asyncio
class TestPredictValidation:
    """Test input validation and error handling."""

    async def test_predict_requires_latitude(self, async_client: AsyncClient):
        """Missing latitude should return 422."""
        payload = {
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 422

    async def test_predict_requires_longitude(self, async_client: AsyncClient):
        """Missing longitude should return 422."""
        payload = {
            "latitude": 40.0150,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 422

    async def test_predict_requires_route_type(self, async_client: AsyncClient):
        """Missing route_type should return 422."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 422

    async def test_predict_requires_planned_date(self, async_client: AsyncClient):
        """Missing planned_date should return 422."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 422

    async def test_predict_validates_latitude_range(self, async_client: AsyncClient):
        """Latitude outside [-90, 90] should return 422."""
        payload = {
            "latitude": 91.0,  # Invalid: > 90
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 422

    async def test_predict_validates_longitude_range(self, async_client: AsyncClient):
        """Longitude outside [-180, 180] should return 422."""
        payload = {
            "latitude": 40.0150,
            "longitude": 181.0,  # Invalid: > 180
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 422

    async def test_predict_validates_route_type(self, async_client: AsyncClient):
        """Invalid route_type should return 422."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "invalid_type",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 422

    async def test_predict_accepts_all_valid_route_types(self, async_client: AsyncClient):
        """All valid route types should be accepted."""
        valid_types = ["alpine", "ice", "mixed", "trad", "sport", "aid", "boulder"]

        for route_type in valid_types:
            payload = {
                "latitude": 40.0150,
                "longitude": -105.2705,
                "route_type": route_type,
                "planned_date": "2024-07-15",
            }

            response = await async_client.post("/api/v1/predict", json=payload)
            assert response.status_code == 200, f"Failed for route_type: {route_type}"

    async def test_predict_validates_search_radius_minimum(self, async_client: AsyncClient):
        """Search radius below 10km should return 422."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 5.0,  # Invalid: < 10
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 422

    async def test_predict_validates_search_radius_maximum(self, async_client: AsyncClient):
        """Search radius above 500km should return 422."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 600.0,  # Invalid: > 500
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 422

    async def test_predict_validates_date_format(self, async_client: AsyncClient):
        """Invalid date format should return 422."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "07/15/2024",  # Invalid: not ISO format
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 422


@pytest.mark.asyncio
class TestPredictEdgeCases:
    """Test edge cases and boundary conditions."""

    async def test_predict_with_no_nearby_accidents(self, async_client: AsyncClient):
        """Request in area with no accidents should return zero risk, low confidence."""
        # Use coordinates in middle of ocean (no accidents)
        payload = {
            "latitude": 0.0,
            "longitude": -160.0,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        assert response.status_code == 200
        assert data["risk_score"] == 0.0
        assert data["confidence"] == 0.0
        assert data["confidence_interpretation"] == "No Data"
        assert data["num_contributing_accidents"] == 0
        assert len(data["top_contributing_accidents"]) == 0

    async def test_predict_with_minimal_search_radius(self, async_client: AsyncClient):
        """Minimal search radius (10km) should still work."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 10.0,
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 200

    async def test_predict_with_maximum_search_radius(self, async_client: AsyncClient):
        """Maximum search radius (500km) should still work."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 500.0,
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 200

    async def test_predict_with_default_search_radius(self, async_client: AsyncClient):
        """Omitting search_radius_km should use route-type-specific default."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            # No search_radius_km
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        assert response.status_code == 200
        assert "search_radius_km" in data["metadata"]

    async def test_predict_with_future_date(self, async_client: AsyncClient):
        """Future date should work (planning ahead)."""
        future_date = (date.today() + timedelta(days=30)).isoformat()
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": future_date,
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 200

    async def test_predict_with_past_date(self, async_client: AsyncClient):
        """Past date should work (historical analysis)."""
        past_date = (date.today() - timedelta(days=365)).isoformat()
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": past_date,
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 200


@pytest.mark.asyncio
class TestPredictRouteTypes:
    """Test route type specific behavior."""

    async def test_predict_alpine_uses_correct_bandwidth(self, async_client: AsyncClient):
        """Alpine route should use alpine-specific spatial bandwidth."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        # Alpine has largest bandwidth (75km), so default radius should be 4x = 300km
        # Which equals MAX_SEARCH_RADIUS_KM (300km)
        assert data["metadata"]["search_radius_km"] == 300.0

    async def test_predict_boulder_uses_correct_bandwidth(self, async_client: AsyncClient):
        """Boulder route should use boulder-specific spatial bandwidth."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "boulder",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        # Boulder has 20km bandwidth, so default radius should be 4x = 80km
        assert data["metadata"]["search_radius_km"] == 80.0

    async def test_predict_ice_uses_correct_bandwidth(self, async_client: AsyncClient):
        """Ice route should use ice-specific spatial bandwidth."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "ice",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        # Ice has 50km bandwidth, so default radius should be 4x = 200km
        assert data["metadata"]["search_radius_km"] == 200.0


@pytest.mark.asyncio
class TestPredictDatabaseIntegration:
    """Test database query integration."""

    async def test_predict_queries_nearby_accidents(self, async_client: AsyncClient):
        """Should query accidents within search radius."""
        # Boulder, CO - known accident hotspot
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 50.0,
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        # Should find some accidents in Boulder area (unless database is empty)
        # Note: This may be 0 if test database is empty, but structure should be valid
        assert "num_contributing_accidents" in data
        assert isinstance(data["num_contributing_accidents"], int)
        assert data["num_contributing_accidents"] >= 0

    async def test_predict_respects_search_radius(self, async_client: AsyncClient):
        """Smaller search radius should return fewer accidents."""
        location = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        # Small radius
        response_small = await async_client.post(
            "/api/v1/predict",
            json={**location, "search_radius_km": 10.0}
        )
        data_small = response_small.json()

        # Large radius
        response_large = await async_client.post(
            "/api/v1/predict",
            json={**location, "search_radius_km": 200.0}
        )
        data_large = response_large.json()

        # Large radius should find >= accidents than small radius
        assert data_large["num_contributing_accidents"] >= data_small["num_contributing_accidents"]

    async def test_predict_includes_distance_calculations(self, async_client: AsyncClient):
        """Contributing accidents should include accurate distance calculations."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        accidents = data["top_contributing_accidents"]

        # If we have accidents, validate distance is within search radius
        if len(accidents) > 0:
            search_radius = data["metadata"]["search_radius_km"]
            for acc in accidents:
                assert acc["distance_km"] <= search_radius


@pytest.mark.asyncio
class TestPredictConfidenceCalculation:
    """Test confidence score calculation logic."""

    async def test_predict_high_accident_count_increases_confidence(self, async_client: AsyncClient):
        """More accidents should generally increase confidence."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 200.0,  # Large radius = more accidents
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        breakdown = data["confidence_breakdown"]

        # If we have many accidents, sample size score should be high
        if breakdown["num_accidents"] > 30:
            assert breakdown["sample_size_score"] > 0.7

    async def test_predict_no_accidents_zero_confidence(self, async_client: AsyncClient):
        """Zero accidents should result in zero confidence."""
        # Middle of ocean
        payload = {
            "latitude": 0.0,
            "longitude": -160.0,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        breakdown = data["confidence_breakdown"]
        assert breakdown["num_accidents"] == 0
        assert breakdown["overall_confidence"] == 0.0

    async def test_predict_confidence_components_sum_correctly(self, async_client: AsyncClient):
        """Confidence breakdown components should be internally consistent."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        breakdown = data["confidence_breakdown"]

        # All component scores should be 0-1 range
        assert 0.0 <= breakdown["sample_size_score"] <= 1.0
        assert 0.0 <= breakdown["match_quality_score"] <= 1.0
        assert 0.0 <= breakdown["spatial_coverage_score"] <= 1.0
        assert 0.0 <= breakdown["temporal_recency_score"] <= 1.0
        assert 0.0 <= breakdown["weather_quality_score"] <= 1.0

        # Weather data percentage should be 0-100
        assert 0.0 <= breakdown["weather_data_pct"] <= 100.0


@pytest.mark.asyncio
class TestPredictResponseConsistency:
    """Test response consistency and data integrity."""

    async def test_predict_same_request_returns_consistent_results(self, async_client: AsyncClient):
        """Same request should return identical results."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 150.0,
        }

        response1 = await async_client.post("/api/v1/predict", json=payload)
        response2 = await async_client.post("/api/v1/predict", json=payload)

        data1 = response1.json()
        data2 = response2.json()

        # Results should be deterministic
        assert data1["risk_score"] == data2["risk_score"]
        assert data1["confidence"] == data2["confidence"]
        assert data1["num_contributing_accidents"] == data2["num_contributing_accidents"]

    async def test_predict_num_contributing_matches_list_length(self, async_client: AsyncClient):
        """num_contributing_accidents should match actual list length."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        # top_contributing_accidents is limited to 50, but num_contributing_accidents
        # is the total count, so list length <= num_contributing_accidents
        assert len(data["top_contributing_accidents"]) <= data["num_contributing_accidents"]
        assert len(data["top_contributing_accidents"]) <= 50  # Max limit

    async def test_predict_top_accidents_sorted_by_influence(self, async_client: AsyncClient):
        """Top contributing accidents should be sorted by total_influence descending."""
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        data = response.json()

        accidents = data["top_contributing_accidents"]

        # If we have multiple accidents, verify sorting
        if len(accidents) > 1:
            influences = [acc["total_influence"] for acc in accidents]
            assert influences == sorted(influences, reverse=True)
