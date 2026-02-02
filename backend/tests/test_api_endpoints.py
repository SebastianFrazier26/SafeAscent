"""
API Endpoint Integration Tests

Tests the FastAPI endpoints for proper request/response handling,
error cases, and data validation. These tests use the test client
to make actual HTTP requests to the API.
"""
import pytest
from datetime import date, timedelta


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

    def test_predict_valid_request(self, test_client):
        """Valid prediction request should return risk score"""
        request_data = {
            "route_id": 1,
            "date": str(date.today() + timedelta(days=1)),
        }
        response = test_client.post("/api/v1/predict", json=request_data)

        # May return 200 (success) or 404 (route not found) depending on DB state
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            assert "risk_score" in data
            assert 0 <= data["risk_score"] <= 100
            assert "num_contributing_accidents" in data
            assert "top_contributing_accidents" in data

    def test_predict_missing_route_id(self, test_client):
        """Request without route_id should fail validation"""
        request_data = {
            "date": str(date.today()),
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        assert response.status_code == 422  # Validation error

    def test_predict_missing_date(self, test_client):
        """Request without date should fail validation"""
        request_data = {
            "route_id": 1,
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        assert response.status_code == 422  # Validation error

    def test_predict_invalid_date_format(self, test_client):
        """Invalid date format should fail validation"""
        request_data = {
            "route_id": 1,
            "date": "not-a-date",
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        assert response.status_code == 422

    def test_predict_negative_route_id(self, test_client):
        """Negative route_id should be handled"""
        request_data = {
            "route_id": -1,
            "date": str(date.today()),
        }
        response = test_client.post("/api/v1/predict", json=request_data)
        # Should either fail validation or return 404
        assert response.status_code in [404, 422]


# ============================================================================
# Mountains Endpoint Tests
# ============================================================================


class TestMountainsEndpoint:
    """Tests for the /mountains endpoint"""

    def test_list_mountains(self, test_client):
        """Should return list of mountains"""
        response = test_client.get("/api/v1/mountains")
        assert response.status_code == 200
        data = response.json()
        # Should be a list (may be empty if no data)
        assert isinstance(data, list)

    def test_list_mountains_with_limit(self, test_client):
        """Should respect limit parameter"""
        response = test_client.get("/api/v1/mountains?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert len(data) <= 5

    def test_get_mountain_not_found(self, test_client):
        """Non-existent mountain should return 404"""
        response = test_client.get("/api/v1/mountains/99999")
        assert response.status_code == 404


# ============================================================================
# Routes Endpoint Tests
# ============================================================================


class TestRoutesEndpoint:
    """Tests for the /routes endpoint"""

    def test_list_routes(self, test_client):
        """Should return list of routes"""
        response = test_client.get("/api/v1/routes")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_routes_by_mountain(self, test_client):
        """Should filter routes by mountain_id"""
        response = test_client.get("/api/v1/routes?mountain_id=1")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_route_not_found(self, test_client):
        """Non-existent route should return 404"""
        response = test_client.get("/api/v1/routes/99999")
        assert response.status_code == 404

    def test_get_route_safety(self, test_client):
        """Route safety endpoint should work or return 404"""
        response = test_client.get(
            "/api/v1/routes/1/safety",
            params={"date": str(date.today())}
        )
        # May work or return 404 depending on DB state
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            assert "risk_score" in data
            assert "color_code" in data


# ============================================================================
# Accidents Endpoint Tests
# ============================================================================


class TestAccidentsEndpoint:
    """Tests for the /accidents endpoint"""

    def test_list_accidents(self, test_client):
        """Should return list of accidents"""
        response = test_client.get("/api/v1/accidents")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_accidents_with_limit(self, test_client):
        """Should respect limit parameter"""
        response = test_client.get("/api/v1/accidents?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert len(data) <= 10

    def test_accidents_by_severity(self, test_client):
        """Should filter accidents by severity"""
        response = test_client.get("/api/v1/accidents?severity=Fatal")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_accident_not_found(self, test_client):
        """Non-existent accident should return 404"""
        response = test_client.get("/api/v1/accidents/99999")
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
        response = test_client.delete("/api/v1/mountains")
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
