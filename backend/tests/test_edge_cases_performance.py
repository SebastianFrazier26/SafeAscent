"""
Phase 7E: Edge Cases & Performance Testing

Tests the prediction algorithm under extreme conditions, boundary values,
missing data scenarios, and performance constraints.
"""

import pytest
from datetime import datetime, timedelta
import time
import statistics


class TestExtremeLocations:
    """Test predictions in extreme geographic locations."""

    def test_alaska_extreme_north(self, test_client):
        """Test prediction for Denali, Alaska (extreme northern latitude)."""
        # Denali - highest peak in North America
        response = test_client.post("/api/v1/predict", json={
            "latitude": 63.069,
            "longitude": -151.007,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })

        assert response.status_code == 200
        data = response.json()

        # Should return valid prediction even at extreme latitude
        assert 0 <= data["risk_score"] <= 100
        assert 0 <= data["confidence"] <= 100

        print(f"\n✅ Alaska (Denali) Prediction:")
        print(f"   Latitude: 63.069° N (extreme north)")
        print(f"   Risk Score: {data['risk_score']}/100")
        print(f"   Confidence: {data['confidence']}/100")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")

    def test_hawaii_extreme_tropical(self, test_client):
        """Test prediction for Hawaii (tropical location, few climbing accidents)."""
        # Mauna Kea, Hawaii
        response = test_client.post("/api/v1/predict", json={
            "latitude": 19.821,
            "longitude": -155.468,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })

        assert response.status_code == 200
        data = response.json()

        # Should handle sparse data gracefully
        assert 0 <= data["risk_score"] <= 100
        assert 0 <= data["confidence"] <= 100

        print(f"\n✅ Hawaii (Mauna Kea) Prediction:")
        print(f"   Risk Score: {data['risk_score']}/100")
        print(f"   Confidence: {data['confidence']}/100")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")
        print(f"   Note: Sparse data area - low confidence expected")

    def test_washington_cascades_high_activity(self, test_client):
        """Test prediction for Washington Cascades (high accident density)."""
        # Mount Rainier area
        response = test_client.post("/api/v1/predict", json={
            "latitude": 46.853,
            "longitude": -121.760,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 50.0
        })

        assert response.status_code == 200
        data = response.json()

        # Should have many accidents (Rainier is very popular and dangerous)
        assert data["num_contributing_accidents"] > 50

        print(f"\n✅ Mount Rainier Prediction:")
        print(f"   Risk Score: {data['risk_score']}/100")
        print(f"   Confidence: {data['confidence']}/100")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")


class TestSparseDataScenarios:
    """Test handling of areas with minimal accident data."""

    def test_remote_wyoming_sparse_data(self, test_client):
        """Test prediction in remote Wyoming with sparse data."""
        # Remote area in Wyoming
        response = test_client.post("/api/v1/predict", json={
            "latitude": 42.5,
            "longitude": -108.5,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })

        assert response.status_code == 200
        data = response.json()

        # Should handle gracefully even with few accidents
        assert 0 <= data["risk_score"] <= 100
        assert 0 <= data["confidence"] <= 100

        # Confidence should be low due to sparse data
        if data["num_contributing_accidents"] < 10:
            assert data["confidence"] < 60, "Low accident count should result in lower confidence"

        print(f"\n✅ Remote Wyoming Prediction:")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")
        print(f"   Confidence: {data['confidence']}/100 (low expected for sparse data)")

    def test_ocean_location_no_accidents(self, test_client):
        """Test prediction for ocean coordinates (should find zero nearby accidents)."""
        # Middle of Pacific Ocean
        response = test_client.post("/api/v1/predict", json={
            "latitude": 30.0,
            "longitude": -140.0,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })

        assert response.status_code == 200
        data = response.json()

        # Should return prediction even with zero nearby accidents
        assert 0 <= data["risk_score"] <= 100
        assert 0 <= data["confidence"] <= 100

        print(f"\n✅ Ocean Location Prediction:")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")
        print(f"   Risk Score: {data['risk_score']}/100")
        print(f"   Confidence: {data['confidence']}/100")
        print(f"   Note: Zero accidents expected - algorithm should handle gracefully")

    def test_very_large_search_radius(self, test_client):
        """Test with maximum search radius to find distant accidents."""
        # Use max allowed radius
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.0,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 500.0  # Very large radius
        })

        assert response.status_code == 200
        data = response.json()

        # Should find many accidents with such a large radius
        assert data["num_contributing_accidents"] > 100

        print(f"\n✅ Large Radius (500km) Prediction:")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")


class TestBoundaryValues:
    """Test boundary values and edge cases for all parameters."""

    def test_latitude_boundaries(self, test_client):
        """Test predictions at latitude boundaries."""
        # Extreme south (valid for US)
        south_response = test_client.post("/api/v1/predict", json={
            "latitude": 25.0,  # Southern Florida
            "longitude": -80.0,
            "route_type": "sport",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })

        # Extreme north (valid for US)
        north_response = test_client.post("/api/v1/predict", json={
            "latitude": 70.0,  # Northern Alaska
            "longitude": -150.0,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })

        assert south_response.status_code == 200
        assert north_response.status_code == 200

        print(f"\n✅ Latitude Boundaries:")
        print(f"   South (25°N): {south_response.json()['risk_score']}/100")
        print(f"   North (70°N): {north_response.json()['risk_score']}/100")

    def test_date_boundaries(self, test_client):
        """Test predictions for various dates (past, present, future)."""
        location = {"latitude": 40.0, "longitude": -105.0, "route_type": "alpine", "search_radius_km": 100.0}

        # Recent past
        past_response = test_client.post("/api/v1/predict", json={
            **location,
            "planned_date": "2025-01-15"  # Recent past
        })

        # Today
        today_response = test_client.post("/api/v1/predict", json={
            **location,
            "planned_date": datetime.now().strftime("%Y-%m-%d")
        })

        # Near future
        future_response = test_client.post("/api/v1/predict", json={
            **location,
            "planned_date": "2026-07-15"  # Future date
        })

        assert past_response.status_code == 200
        assert today_response.status_code == 200
        assert future_response.status_code == 200

        print(f"\n✅ Date Range Testing:")
        print(f"   Past (2023): Valid prediction generated")
        print(f"   Today: Valid prediction generated")
        print(f"   Future (2025): Valid prediction generated")

    def test_minimum_search_radius(self, test_client):
        """Test with minimum valid search radius (10km)."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.255,
            "longitude": -105.615,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 10.0  # Minimum valid radius
        })

        assert response.status_code == 200
        data = response.json()

        print(f"\n✅ Minimum Radius (10km) Prediction:")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")
        print(f"   Risk Score: {data['risk_score']}/100")

    def test_all_route_types(self, test_client):
        """Test prediction for all supported route types."""
        location = {"latitude": 40.0, "longitude": -105.0, "planned_date": "2024-07-15", "search_radius_km": 100.0}

        route_types = ["alpine", "sport", "trad", "ice", "mixed", "aid", "boulder"]
        results = {}

        for route_type in route_types:
            response = test_client.post("/api/v1/predict", json={
                **location,
                "route_type": route_type
            })
            assert response.status_code == 200
            data = response.json()
            results[route_type] = {
                "risk": data["risk_score"],
                "confidence": data["confidence"],
                "accidents": data["num_contributing_accidents"]
            }

        print(f"\n✅ All Route Types Tested:")
        for rt, res in results.items():
            print(f"   {rt:8s}: Risk={res['risk']:5.1f}, Conf={res['confidence']:5.1f}, Accidents={res['accidents']}")


class TestPerformanceBenchmarks:
    """Test response times and performance under load."""

    def test_single_prediction_response_time(self, test_client):
        """Benchmark response time for a single prediction."""
        # Warm-up request
        test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.0,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })

        # Timed request
        start_time = time.time()
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.0,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })
        end_time = time.time()

        response_time_ms = (end_time - start_time) * 1000

        assert response.status_code == 200

        print(f"\n✅ Single Request Performance:")
        print(f"   Response Time: {response_time_ms:.0f}ms")

        # Note: Target is <500ms for production, but with real-time weather API
        # calls and no caching, we expect 2-5 seconds currently
        if response_time_ms < 500:
            print(f"   Status: ✅ EXCELLENT (under 500ms target!)")
        elif response_time_ms < 2000:
            print(f"   Status: ✅ GOOD (under 2s)")
        elif response_time_ms < 5000:
            print(f"   Status: ⚠️  ACCEPTABLE (2-5s, will improve with caching)")
        else:
            print(f"   Status: ⚠️  SLOW (>5s, needs optimization)")

    def test_multiple_predictions_sequential(self, test_client):
        """Benchmark sequential prediction requests."""
        num_requests = 5
        times = []

        for i in range(num_requests):
            start_time = time.time()
            response = test_client.post("/api/v1/predict", json={
                "latitude": 40.0 + i * 0.1,  # Vary location slightly
                "longitude": -105.0 + i * 0.1,
                "route_type": "alpine",
                "planned_date": "2026-07-15",
                "search_radius_km": 100.0
            })
            end_time = time.time()

            assert response.status_code == 200
            times.append((end_time - start_time) * 1000)

        avg_time = statistics.mean(times)
        min_time = min(times)
        max_time = max(times)

        print(f"\n✅ Sequential Requests ({num_requests} requests):")
        print(f"   Average: {avg_time:.0f}ms")
        print(f"   Min: {min_time:.0f}ms")
        print(f"   Max: {max_time:.0f}ms")

    def test_database_query_performance(self, test_client):
        """Test database query performance with different radii."""
        radii = [25, 50, 100, 200]
        times = []
        accident_counts = []

        for radius in radii:
            start_time = time.time()
            response = test_client.post("/api/v1/predict", json={
                "latitude": 40.0,
                "longitude": -105.0,
                "route_type": "alpine",
                "planned_date": "2026-07-15",
                "search_radius_km": radius
            })
            end_time = time.time()

            assert response.status_code == 200
            times.append((end_time - start_time) * 1000)
            accident_counts.append(response.json()["num_contributing_accidents"])

        print(f"\n✅ Database Query Performance by Radius:")
        for i, radius in enumerate(radii):
            print(f"   {radius:3d}km: {times[i]:6.0f}ms ({accident_counts[i]:4d} accidents)")


class TestErrorHandlingRobustness:
    """Test error handling for various edge cases."""

    def test_invalid_latitude_too_high(self, test_client):
        """Test rejection of latitude > 90."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 95.0,
            "longitude": -105.0,
            "route_type": "alpine",
            "planned_date": "2024-07-15"
        })

        assert response.status_code == 422

    def test_invalid_latitude_too_low(self, test_client):
        """Test rejection of latitude < -90."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": -95.0,
            "longitude": -105.0,
            "route_type": "alpine",
            "planned_date": "2024-07-15"
        })

        assert response.status_code == 422

    def test_invalid_longitude_too_high(self, test_client):
        """Test rejection of longitude > 180."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": 185.0,
            "route_type": "alpine",
            "planned_date": "2024-07-15"
        })

        assert response.status_code == 422

    def test_invalid_longitude_too_low(self, test_client):
        """Test rejection of longitude < -180."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -185.0,
            "route_type": "alpine",
            "planned_date": "2024-07-15"
        })

        assert response.status_code == 422

    def test_invalid_search_radius_negative(self, test_client):
        """Test rejection of negative search radius."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.0,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": -10.0
        })

        assert response.status_code == 422

    def test_invalid_search_radius_too_large(self, test_client):
        """Test rejection of search radius > 500km."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.0,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 600.0
        })

        assert response.status_code == 422

    def test_malformed_date_format(self, test_client):
        """Test rejection of malformed date."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.0,
            "route_type": "alpine",
            "planned_date": "2024/07/15"  # Wrong format (should be YYYY-MM-DD)
        })

        assert response.status_code == 422


class TestConsistencyAndReproducibility:
    """Test that predictions are consistent and reproducible."""

    def test_same_input_same_output(self, test_client):
        """Test that identical requests produce identical results."""
        request_data = {
            "latitude": 40.0,
            "longitude": -105.0,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        }

        # Make two identical requests
        response1 = test_client.post("/api/v1/predict", json=request_data)
        response2 = test_client.post("/api/v1/predict", json=request_data)

        assert response1.status_code == 200
        assert response2.status_code == 200

        data1 = response1.json()
        data2 = response2.json()

        # Should produce identical results
        assert data1["risk_score"] == data2["risk_score"]
        assert data1["confidence"] == data2["confidence"]
        assert data1["num_contributing_accidents"] == data2["num_contributing_accidents"]

        print(f"\n✅ Consistency Test:")
        print(f"   Request 1: Risk={data1['risk_score']}, Confidence={data1['confidence']}")
        print(f"   Request 2: Risk={data2['risk_score']}, Confidence={data2['confidence']}")
        print(f"   Status: Identical results ✅")


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])
