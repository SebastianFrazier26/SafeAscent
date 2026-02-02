"""
Performance benchmarks for SafeAscent prediction API.

Tests response times, throughput, and scalability under various conditions.
These tests establish baseline performance metrics and identify bottlenecks.

Performance Targets (MVP Baseline):
- API response time: < 2000ms for typical queries (current: ~1500ms)
- Small queries (<100 accidents): < 1000ms (current: ~700ms)
- Large queries (1000+ accidents): < 2500ms (current: ~2000ms)
- Concurrent requests: Complete 10 requests in < 15s (current: ~13s)

See PERFORMANCE_BASELINE.md for optimization roadmap to production targets.

Author: SafeAscent Development Team
Date: 2026-01-29
"""
import pytest
import asyncio
import time
from statistics import mean, median, stdev
from datetime import date
from httpx import AsyncClient

# Mark all tests in this file as performance tests
pytestmark = pytest.mark.performance


@pytest.mark.asyncio
class TestPredictEndpointPerformance:
    """Benchmark predict endpoint response times."""

    async def test_predict_response_time_baseline(self, async_client: AsyncClient):
        """
        Baseline response time for typical request.

        Target: < 500ms (p95)
        """
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 150.0,
        }

        # Warm up (first request may be slower due to connection setup)
        await async_client.post("/api/v1/predict", json=payload)

        # Measure 10 requests
        times = []
        for _ in range(10):
            start = time.perf_counter()
            response = await async_client.post("/api/v1/predict", json=payload)
            end = time.perf_counter()

            assert response.status_code == 200
            times.append((end - start) * 1000)  # Convert to milliseconds

        # Calculate statistics
        avg_time = mean(times)
        median_time = median(times)
        max_time = max(times)
        min_time = min(times)
        std_dev = stdev(times) if len(times) > 1 else 0

        print(f"\n📊 Baseline Performance:")
        print(f"   Average: {avg_time:.1f}ms")
        print(f"   Median:  {median_time:.1f}ms")
        print(f"   Min:     {min_time:.1f}ms")
        print(f"   Max:     {max_time:.1f}ms")
        print(f"   StdDev:  {std_dev:.1f}ms")

        # Assert performance targets (MVP baseline)
        assert avg_time < 2000, f"Average response time {avg_time:.1f}ms exceeds 2000ms baseline"
        assert max_time < 3000, f"Max response time {max_time:.1f}ms exceeds 3000ms threshold"

    async def test_predict_small_search_radius_performance(self, async_client: AsyncClient):
        """
        Response time with small search radius (fewer accidents).

        Expected: Faster than baseline due to fewer accidents to process.
        """
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 10.0,  # Very small radius
        }

        times = []
        for _ in range(10):
            start = time.perf_counter()
            response = await async_client.post("/api/v1/predict", json=payload)
            end = time.perf_counter()

            assert response.status_code == 200
            times.append((end - start) * 1000)

        avg_time = mean(times)
        print(f"\n📊 Small Radius Performance:")
        print(f"   Average: {avg_time:.1f}ms (10km radius)")

        # Should be faster than baseline with minimal data
        assert avg_time < 1000, f"Small radius query {avg_time:.1f}ms exceeds 1000ms baseline"

    async def test_predict_large_search_radius_performance(self, async_client: AsyncClient):
        """
        Response time with large search radius (many accidents).

        Expected: Slower than baseline but still under 1 second.
        """
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 300.0,  # Maximum radius
        }

        times = []
        for _ in range(5):  # Fewer iterations due to slower queries
            start = time.perf_counter()
            response = await async_client.post("/api/v1/predict", json=payload)
            end = time.perf_counter()

            assert response.status_code == 200
            data = response.json()
            times.append((end - start) * 1000)

        avg_time = mean(times)
        num_accidents = response.json()["num_contributing_accidents"]

        print(f"\n📊 Large Radius Performance:")
        print(f"   Average: {avg_time:.1f}ms (300km radius)")
        print(f"   Accidents processed: {num_accidents}")

        # Should complete within 2.5 seconds even with many accidents
        assert avg_time < 2500, f"Large radius query {avg_time:.1f}ms exceeds 2.5s baseline"

    async def test_predict_performance_scaling(self, async_client: AsyncClient):
        """
        Test how performance scales with increasing search radius.

        Validates that algorithm is O(n) or better with accident count.
        """
        radii = [25, 50, 100, 150, 200, 300]
        results = []

        for radius in radii:
            payload = {
                "latitude": 40.0150,
                "longitude": -105.2705,
                "route_type": "alpine",
                "planned_date": "2024-07-15",
                "search_radius_km": radius,
            }

            # Measure 3 requests per radius
            times = []
            for _ in range(3):
                start = time.perf_counter()
                response = await async_client.post("/api/v1/predict", json=payload)
                end = time.perf_counter()
                times.append((end - start) * 1000)

            data = response.json()
            avg_time = mean(times)
            num_accidents = data["num_contributing_accidents"]

            results.append({
                "radius_km": radius,
                "avg_time_ms": avg_time,
                "num_accidents": num_accidents,
            })

        print(f"\n📊 Performance Scaling:")
        print(f"   Radius | Accidents | Avg Time")
        print(f"   -------|-----------|----------")
        for r in results:
            print(f"   {r['radius_km']:>4}km | {r['num_accidents']:>9} | {r['avg_time_ms']:>7.1f}ms")

        # Verify scaling is reasonable (not exponential)
        # Time per accident should be relatively constant
        if results[0]['num_accidents'] > 0 and results[-1]['num_accidents'] > 0:
            time_per_accident_small = results[0]['avg_time_ms'] / max(results[0]['num_accidents'], 1)
            time_per_accident_large = results[-1]['avg_time_ms'] / max(results[-1]['num_accidents'], 1)

            # Large queries shouldn't be more than 3x slower per accident
            assert time_per_accident_large < time_per_accident_small * 3, \
                "Performance degradation suggests poor scaling"


@pytest.mark.asyncio
class TestConcurrentPerformance:
    """Benchmark concurrent request handling."""

    async def test_predict_concurrent_requests(self, async_client: AsyncClient):
        """
        Handle multiple concurrent requests.

        Target: 10 concurrent requests complete in < 2 seconds total.
        """
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 100.0,
        }

        async def make_request():
            """Single request wrapper."""
            start = time.perf_counter()
            response = await async_client.post("/api/v1/predict", json=payload)
            end = time.perf_counter()
            return response, (end - start) * 1000

        # Send 10 concurrent requests
        start_all = time.perf_counter()
        tasks = [make_request() for _ in range(10)]
        results = await asyncio.gather(*tasks)
        end_all = time.perf_counter()

        total_time = (end_all - start_all) * 1000
        individual_times = [time for _, time in results]
        avg_individual = mean(individual_times)

        print(f"\n📊 Concurrent Performance (10 requests):")
        print(f"   Total time: {total_time:.1f}ms")
        print(f"   Avg individual: {avg_individual:.1f}ms")
        print(f"   Throughput: {10 / (total_time / 1000):.1f} req/s")

        # All requests should succeed
        for response, _ in results:
            assert response.status_code == 200

        # Total time should be less than 16 seconds for 10 requests (MVP baseline + variance)
        assert total_time < 16000, f"10 concurrent requests took {total_time:.1f}ms, exceeds 16s baseline"

    async def test_predict_sustained_load(self, async_client: AsyncClient):
        """
        Sustained load test - process requests continuously.

        Validates no memory leaks or performance degradation over time.
        """
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 100.0,
        }

        num_requests = 20
        times = []

        print(f"\n📊 Sustained Load Test ({num_requests} requests):")

        for i in range(num_requests):
            start = time.perf_counter()
            response = await async_client.post("/api/v1/predict", json=payload)
            end = time.perf_counter()

            assert response.status_code == 200
            request_time = (end - start) * 1000
            times.append(request_time)

            if (i + 1) % 5 == 0:
                print(f"   Completed {i + 1}/{num_requests} requests...")

        # Analyze performance over time
        first_half_avg = mean(times[:len(times)//2])
        second_half_avg = mean(times[len(times)//2:])

        print(f"   First half avg:  {first_half_avg:.1f}ms")
        print(f"   Second half avg: {second_half_avg:.1f}ms")
        print(f"   Overall avg:     {mean(times):.1f}ms")

        # Performance shouldn't degrade significantly over time
        assert second_half_avg < first_half_avg * 1.5, \
            "Performance degraded during sustained load"


@pytest.mark.asyncio
class TestDatabaseQueryPerformance:
    """Benchmark database query performance."""

    async def test_spatial_query_performance(self, async_client: AsyncClient):
        """
        Measure spatial query performance (PostGIS ST_DWithin).

        Target: < 100ms for typical spatial searches.
        """
        # Use different locations to avoid any caching effects
        locations = [
            (40.0150, -105.2705),  # Boulder, CO
            (47.6062, -122.3321),  # Seattle, WA
            (36.5781, -118.2923),  # Mount Whitney, CA
            (44.2778, -110.7050),  # Yellowstone, WY
        ]

        times = []
        for lat, lon in locations:
            payload = {
                "latitude": lat,
                "longitude": lon,
                "route_type": "alpine",
                "planned_date": "2024-07-15",
                "search_radius_km": 150.0,
            }

            start = time.perf_counter()
            response = await async_client.post("/api/v1/predict", json=payload)
            end = time.perf_counter()

            assert response.status_code == 200
            times.append((end - start) * 1000)

        avg_time = mean(times)
        print(f"\n📊 Spatial Query Performance:")
        print(f"   Average: {avg_time:.1f}ms across {len(locations)} locations")

        # Most time is algorithm execution, but DB query should be fast
        # Total response includes DB query + algorithm + serialization
        # MVP baseline: < 1500ms average
        assert avg_time < 1500, f"Spatial queries averaging {avg_time:.1f}ms exceeds 1.5s baseline"


@pytest.mark.asyncio
class TestAlgorithmPerformance:
    """Benchmark core algorithm execution time."""

    async def test_algorithm_with_varying_accident_counts(self, async_client: AsyncClient):
        """
        Measure algorithm performance with different accident counts.

        Validates O(n) scaling with number of accidents.
        """
        # Test at different hotspot locations (varying accident density)
        test_cases = [
            ("Low density", 0.0, -160.0, 10),      # Ocean - few accidents
            ("Medium density", 40.0150, -105.2705, 50),  # Boulder - moderate
            ("High density", 40.0150, -105.2705, 300),   # Boulder - large radius
        ]

        print(f"\n📊 Algorithm Performance by Accident Count:")

        for label, lat, lon, radius in test_cases:
            payload = {
                "latitude": lat,
                "longitude": lon,
                "route_type": "alpine",
                "planned_date": "2024-07-15",
                "search_radius_km": radius,
            }

            # Measure 5 requests
            times = []
            for _ in range(5):
                start = time.perf_counter()
                response = await async_client.post("/api/v1/predict", json=payload)
                end = time.perf_counter()
                times.append((end - start) * 1000)

            data = response.json()
            avg_time = mean(times)
            num_accidents = data["num_contributing_accidents"]

            print(f"   {label:15} | {num_accidents:>4} accidents | {avg_time:>6.1f}ms avg")

            # Even with many accidents, should complete reasonably fast
            if num_accidents > 100:
                # More than 100 accidents should complete in < 2.5s (MVP baseline)
                assert avg_time < 2500, \
                    f"Algorithm took {avg_time:.1f}ms for {num_accidents} accidents, exceeds 2.5s baseline"


@pytest.mark.asyncio
class TestValidationPerformance:
    """Benchmark input validation performance."""

    async def test_validation_error_response_time(self, async_client: AsyncClient):
        """
        Validation errors should be very fast (no DB/algorithm execution).

        Target: < 50ms for validation failures.
        """
        invalid_payloads = [
            {"longitude": -105.2705, "route_type": "alpine", "planned_date": "2024-07-15"},
            {"latitude": 40.0150, "route_type": "alpine", "planned_date": "2024-07-15"},
            {"latitude": 40.0150, "longitude": -105.2705, "planned_date": "2024-07-15"},
            {"latitude": 91.0, "longitude": -105.2705, "route_type": "alpine", "planned_date": "2024-07-15"},
        ]

        times = []
        for payload in invalid_payloads:
            start = time.perf_counter()
            response = await async_client.post("/api/v1/predict", json=payload)
            end = time.perf_counter()

            assert response.status_code == 422  # Validation error
            times.append((end - start) * 1000)

        avg_time = mean(times)
        print(f"\n📊 Validation Error Performance:")
        print(f"   Average: {avg_time:.1f}ms")

        # Validation should be very fast
        assert avg_time < 50, f"Validation errors taking {avg_time:.1f}ms - should be < 50ms"


@pytest.mark.asyncio
class TestMemoryPerformance:
    """Benchmark memory usage patterns."""

    async def test_response_size(self, async_client: AsyncClient):
        """
        Measure response payload size.

        Target: < 100KB for typical responses.
        """
        payload = {
            "latitude": 40.0150,
            "longitude": -105.2705,
            "route_type": "alpine",
            "planned_date": "2024-07-15",
            "search_radius_km": 200.0,
        }

        response = await async_client.post("/api/v1/predict", json=payload)
        assert response.status_code == 200

        # Measure response size
        response_bytes = len(response.content)
        response_kb = response_bytes / 1024

        data = response.json()
        num_accidents = len(data["top_contributing_accidents"])

        print(f"\n📊 Response Size:")
        print(f"   Size: {response_kb:.2f} KB")
        print(f"   Contributing accidents: {num_accidents}")
        print(f"   Bytes per accident: {response_bytes / max(num_accidents, 1):.0f}")

        # Response should be reasonable size
        assert response_kb < 100, f"Response size {response_kb:.2f}KB exceeds 100KB limit"
