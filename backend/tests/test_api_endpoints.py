"""
API Endpoint Integration Tests

Tests the FastAPI endpoints for proper request/response handling,
error cases, and data validation. These tests use the test client
to make actual HTTP requests to the API.

Note: Tests for MP-related endpoints (locations, mp-routes) require
the database to have mp_locations and mp_routes tables populated.
These tests will be skipped if the tables don't exist.
"""
import os
import pytest
from datetime import date, timedelta


# Check if we're running against a database with tables
# In CI/production, set DB_TABLES_AVAILABLE=true
DB_TABLES_AVAILABLE = os.environ.get("DB_TABLES_AVAILABLE", "false").lower() == "true"

# Backward compat: also check MP_TABLES_AVAILABLE
MP_TABLES_AVAILABLE = os.environ.get("MP_TABLES_AVAILABLE", "false").lower() == "true" or DB_TABLES_AVAILABLE

# Skip marker for tests requiring MP tables
requires_mp_tables = pytest.mark.skipif(
    not MP_TABLES_AVAILABLE,
    reason="MP tables (mp_locations, mp_routes) not available in test database. Set DB_TABLES_AVAILABLE=true to run."
)

# Skip marker for tests requiring database tables (accidents, predict with data)
requires_database = pytest.mark.skipif(
    not DB_TABLES_AVAILABLE,
    reason="Database tables not available in test database. Set DB_TABLES_AVAILABLE=true to run."
)


# ============================================================================
# Health Check Tests
# ============================================================================


class TestHealthEndpoints:
    """Tests for health check endpoints"""

    def test_root_health_check(self, test_client):
        """Root health endpoint should return healthy status"""
        response = test_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_api_health_check(self, test_client):
        """API version health endpoint should return healthy status"""
        response = test_client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_root_endpoint(self, test_client):
        """Root endpoint should return API info"""
        response = test_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "SafeAscent" in data["message"]


# ============================================================================
# Prediction Endpoint Tests
# ============================================================================


class TestPredictEndpoint:
    """Tests for the /predict endpoint"""

    @requires_database
    def test_predict_valid_request(self, test_client):
        """Valid prediction request should return risk score"""
        request_data = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": str(date.today() + timedelta(days=1)),
        }
        response = test_client.post("/api/v1/predict", json=request_data)

        # Should return 200 with risk score
        assert response.status_code == 200
        data = response.json()
        assert "risk_score" in data
        assert 0 <= data["risk_score"] <= 100
        assert "num_contributing_accidents" in data
        assert "top_contributing_accidents" in data

    def test_predict_missing_latitude(self, test_client):
        """Request without latitude should fail validation"""
        request_data = {
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": str(date.today()),
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        assert response.status_code == 422  # Validation error

    def test_predict_missing_longitude(self, test_client):
        """Request without longitude should fail validation"""
        request_data = {
            "latitude": 40.0150,
            "route_type": "alpine",
            "planned_date": str(date.today()),
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        assert response.status_code == 422  # Validation error

    def test_predict_missing_route_type(self, test_client):
        """Request without route_type should fail validation"""
        request_data = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "planned_date": str(date.today()),
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        assert response.status_code == 422  # Validation error

    def test_predict_missing_planned_date(self, test_client):
        """Request without planned_date should fail validation"""
        request_data = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        assert response.status_code == 422  # Validation error

    def test_predict_invalid_date_format(self, test_client):
        """Invalid date format should fail validation"""
        request_data = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "not-a-date",
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        assert response.status_code == 422

    def test_predict_invalid_route_type(self, test_client):
        """Invalid route_type should fail validation"""
        request_data = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "invalid_type",
            "planned_date": str(date.today()),
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        assert response.status_code == 422

    def test_predict_invalid_latitude(self, test_client):
        """Latitude outside valid range should fail validation"""
        request_data = {
            "latitude": 95.0,  # Invalid: > 90
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": str(date.today()),
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        assert response.status_code == 422

    def test_predict_invalid_longitude(self, test_client):
        """Longitude outside valid range should fail validation"""
        request_data = {
            "latitude": 40.0150,
            "longitude": -200.0,  # Invalid: < -180
            "route_type": "alpine",
            "planned_date": str(date.today()),
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        assert response.status_code == 422


# ============================================================================
# Locations Endpoint Tests (requires MP tables)
# ============================================================================


@requires_mp_tables
class TestLocationsEndpoint:
    """Tests for the /locations endpoint (MP climbing areas)"""

    def test_list_locations(self, test_client):
        """Should return list of locations with pagination wrapper"""
        response = test_client.get("/api/v1/locations")
        assert response.status_code == 200
        data = response.json()
        # Response is wrapped with {total, data}
        assert "total" in data
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_list_locations_with_limit(self, test_client):
        """Should respect limit parameter"""
        response = test_client.get("/api/v1/locations?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) <= 5

    def test_list_locations_with_search(self, test_client):
        """Should filter locations by search term"""
        response = test_client.get("/api/v1/locations?search=Yosemite")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_get_location_not_found(self, test_client):
        """Non-existent location should return 404"""
        response = test_client.get("/api/v1/locations/99999999999")
        assert response.status_code == 404


# ============================================================================
# MP Routes Endpoint Tests (requires MP tables)
# ============================================================================


@requires_mp_tables
class TestMpRoutesEndpoint:
    """Tests for the /mp-routes endpoint (Mountain Project routes)"""

    def test_list_routes(self, test_client):
        """Should return list of routes with pagination wrapper"""
        response = test_client.get("/api/v1/mp-routes")
        assert response.status_code == 200
        data = response.json()
        # Response is wrapped with {total, data}
        assert "total" in data
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_list_routes_with_limit(self, test_client):
        """Should respect limit parameter"""
        response = test_client.get("/api/v1/mp-routes?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) <= 5

    def test_list_routes_by_location(self, test_client):
        """Should filter routes by location_id"""
        response = test_client.get("/api/v1/mp-routes?location_id=1")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_list_routes_with_search(self, test_client):
        """Should filter routes by search term"""
        response = test_client.get("/api/v1/mp-routes?search=crack")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_get_route_not_found(self, test_client):
        """Non-existent route should return 404"""
        response = test_client.get("/api/v1/mp-routes/99999999999")
        assert response.status_code == 404

    def test_get_route_safety(self, test_client):
        """Route safety endpoint should work or return 404 (POST method)"""
        response = test_client.post(
            "/api/v1/mp-routes/1/safety",
            params={"target_date": str(date.today())}
        )
        # May work or return 404 depending on whether route_id=1 exists
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            assert "risk_score" in data
            assert "color_code" in data

    def test_routes_map_endpoint(self, test_client):
        """Map endpoint should return routes with coordinates"""
        response = test_client.get("/api/v1/mp-routes/map?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert isinstance(data["data"], list)


# ============================================================================
# Accidents Endpoint Tests
# ============================================================================


@requires_database
class TestAccidentsEndpoint:
    """Tests for the /accidents endpoint"""

    def test_list_accidents(self, test_client):
        """Should return list of accidents with pagination wrapper"""
        response = test_client.get("/api/v1/accidents")
        assert response.status_code == 200
        data = response.json()
        # Response is wrapped with {total, data}
        assert "total" in data
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_list_accidents_with_limit(self, test_client):
        """Should respect limit parameter"""
        response = test_client.get("/api/v1/accidents?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) <= 10

    def test_accidents_with_offset(self, test_client):
        """Should support pagination with offset"""
        response = test_client.get("/api/v1/accidents?limit=5&offset=5")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_get_accident_not_found(self, test_client):
        """Non-existent accident should return 404"""
        response = test_client.get("/api/v1/accidents/99999999")
        assert response.status_code == 404


# ============================================================================
# Error Handling Tests
# ============================================================================


class TestErrorHandling:
    """Tests for API error handling"""

    def test_404_for_unknown_route(self, test_client):
        """Unknown API route should return 404"""
        response = test_client.get("/api/v1/unknown-endpoint")
        assert response.status_code == 404

    def test_method_not_allowed(self, test_client):
        """Wrong HTTP method should return 405"""
        response = test_client.delete("/api/v1/health")
        assert response.status_code == 405

    def test_invalid_json_body(self, test_client):
        """Invalid JSON should return 422"""
        response = test_client.post(
            "/api/v1/predict",
            content="not valid json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
