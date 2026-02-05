"""
Tests for Safety Algorithm

Tests the core risk calculation engine that combines multiple weighting factors:
- Spatial weighting (Gaussian decay)
- Temporal weighting (exponential decay + seasonal boost)
- Weather similarity (pattern correlation)
- Route type weighting (asymmetric matrix)
- Severity weighting (subtle boosters)

This validates the mathematical foundation of SafeAscent's predictions.
"""
import pytest
from datetime import date, datetime, timedelta
from app.services.safety_algorithm import (
    AccidentData,
    SafetyPrediction,
    calculate_safety_score,
    normalize_risk_score,
    get_top_contributing_accidents,
)
from app.services.weather_similarity import WeatherPattern


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def reference_location():
    """Test location: Longs Peak, Colorado"""
    return {
        "latitude": 40.2549,
        "longitude": -105.6426,
        "elevation": 4346.0,
        "route_type": "alpine",
        "planned_date": date(2024, 7, 15),
    }


@pytest.fixture
def reference_weather():
    """Typical summer weather pattern"""
    return WeatherPattern(
        temperature=[15.0, 16.0, 17.0, 18.0, 19.0, 18.0, 17.0],
        precipitation=[0.0, 0.0, 2.0, 1.0, 0.0, 0.0, 0.0],
        wind_speed=[5.0, 6.0, 7.0, 8.0, 6.0, 5.0, 4.0],
        visibility=[10.0, 10.0, 8.0, 9.0, 10.0, 10.0, 10.0],
        cloud_cover=[20.0, 30.0, 60.0, 50.0, 30.0, 20.0, 10.0],
        daily_temps=[
            (10.0, 15.0, 20.0),
            (11.0, 16.0, 21.0),
            (12.0, 17.0, 22.0),
            (13.0, 18.0, 23.0),
            (14.0, 19.0, 24.0),
            (13.0, 18.0, 23.0),
            (12.0, 17.0, 22.0),
        ],
    )


@pytest.fixture
def nearby_accident(reference_weather):
    """Accident 10km away, 1 year ago, same route type"""
    return AccidentData(
        accident_id=1,
        latitude=40.3549,  # ~10km north
        longitude=-105.6426,
        elevation_meters=4200.0,
        accident_date=date(2023, 7, 15),
        route_type="alpine",
        severity="Serious Injury",
        weather_pattern=reference_weather,
    )


@pytest.fixture
def distant_accident(reference_weather):
    """Accident 100km away"""
    return AccidentData(
        accident_id=2,
        latitude=41.2549,  # ~100km north
        longitude=-105.6426,
        elevation_meters=3500.0,
        accident_date=date(2023, 7, 15),
        route_type="alpine",
        severity="Minor Injury",
        weather_pattern=reference_weather,
    )


@pytest.fixture
def old_accident(reference_weather):
    """Accident 10 years ago"""
    return AccidentData(
        accident_id=3,
        latitude=40.2549,
        longitude=-105.6426,
        elevation_meters=4346.0,
        accident_date=date(2014, 7, 15),
        route_type="alpine",
        severity="Fatal",
        weather_pattern=reference_weather,
    )


@pytest.fixture
def different_route_type_accident(reference_weather):
    """Sport climbing accident at alpine location"""
    return AccidentData(
        accident_id=4,
        latitude=40.2549,
        longitude=-105.6426,
        elevation_meters=4346.0,
        accident_date=date(2023, 7, 15),
        route_type="sport",
        severity="Minor Injury",
        weather_pattern=reference_weather,
    )


# ============================================================================
# Test: Risk Score Normalization
# ============================================================================


class TestRiskScoreNormalization:
    """Tests for risk score normalization to 0-100 scale

    Formula: risk_score = total_influence * 5.0 (capped at 100)
    Note: Normalization factor changed from 10.0 to 5.0 per Decision #30
    """

    def test_zero_influence_zero_risk(self):
        """Zero total influence should give 0 risk"""
        risk = normalize_risk_score(total_influence=0.0)
        assert risk == 0.0

    def test_low_influence_low_risk(self):
        """Low influence should give low risk
        0.5 * 5 = 2.5
        """
        risk = normalize_risk_score(total_influence=0.5)
        assert risk == 2.5

    def test_moderate_influence_moderate_risk(self):
        """Moderate influence should give moderate risk
        2.0 * 5 = 10
        """
        risk = normalize_risk_score(total_influence=2.0)
        assert risk == 10.0

    def test_high_influence_high_risk(self):
        """High influence should give high risk
        5.0 * 5 = 25
        """
        risk = normalize_risk_score(total_influence=5.0)
        assert risk == 25.0

    def test_extreme_influence_capped_at_100(self):
        """Extreme influence should cap at 100
        100.0 * 5 = 500, but capped at 100
        """
        risk = normalize_risk_score(total_influence=100.0)
        assert risk == 100.0

    def test_negative_influence_returns_zero(self):
        """Negative influence (edge case) should return 0"""
        risk = normalize_risk_score(total_influence=-1.0)
        assert risk == 0.0

    def test_monotonic_increase(self):
        """Risk should increase monotonically with influence"""
        influences = [0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0]
        risks = [normalize_risk_score(i) for i in influences]
        # Each risk should be >= previous
        for i in range(1, len(risks)):
            assert risks[i] >= risks[i - 1]

    def test_reaches_max_at_20(self):
        """Risk should reach maximum (100) at influence of 20
        (with normalization factor 5.0, need influence=20 to hit 100)
        """
        risk = normalize_risk_score(total_influence=20.0)
        assert risk == 100.0


# ============================================================================
# Test: Top Contributing Accidents
# ============================================================================


class TestTopContributingAccidents:
    """Tests for selecting top contributing accidents"""

    def test_returns_top_n_accidents(self):
        """Should return top N accidents by contribution"""
        # Create influence dictionaries (as returned by calculate_accident_influence)
        influences = [
            {
                "accident_id": i,
                "total_influence": influence,
                "distance_km": 10.0 + i,
                "days_ago": 100 + i,
                "spatial_weight": 0.8,
                "temporal_weight": 0.7,
                "elevation_weight": 1.0,
                "weather_weight": 0.6,
                "route_type_weight": 1.0,
                "severity_weight": 1.0,
                "grade_weight": 1.0,
            }
            for i, influence in enumerate([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05])
        ]

        top_5 = get_top_contributing_accidents(influences, limit=5)
        assert len(top_5) == 5
        # Should be sorted by contribution descending
        assert top_5[0]["total_influence"] == 0.9
        assert top_5[4]["total_influence"] == 0.5

    def test_returns_all_if_fewer_than_n(self):
        """Should return all accidents if fewer than N"""
        influences = [
            {
                "accident_id": i,
                "total_influence": influence,
                "distance_km": 10.0,
                "days_ago": 100,
                "spatial_weight": 0.8,
                "temporal_weight": 0.7,
                "elevation_weight": 1.0,
                "weather_weight": 0.6,
                "route_type_weight": 1.0,
                "severity_weight": 1.0,
                "grade_weight": 1.0,
            }
            for i, influence in enumerate([0.8, 0.5, 0.3])
        ]

        top_10 = get_top_contributing_accidents(influences, limit=10)
        assert len(top_10) == 3

    def test_empty_list_returns_empty(self):
        """Empty accident list should return empty"""
        top = get_top_contributing_accidents([], limit=5)
        assert len(top) == 0

    def test_sorted_by_contribution_descending(self):
        """Results should be sorted by contribution (highest first)"""
        # Contributions in random order
        influences = [
            {
                "accident_id": i,
                "total_influence": influence,
                "distance_km": 10.0,
                "days_ago": 100,
                "spatial_weight": 0.8,
                "temporal_weight": 0.7,
                "elevation_weight": 1.0,
                "weather_weight": 0.6,
                "route_type_weight": 1.0,
                "severity_weight": 1.0,
                "grade_weight": 1.0,
            }
            for i, influence in enumerate([0.3, 0.8, 0.1, 0.9, 0.5])
        ]

        top = get_top_contributing_accidents(influences, limit=5)
        # Should be sorted descending
        assert top[0]["total_influence"] == 0.9
        assert top[1]["total_influence"] == 0.8
        assert top[2]["total_influence"] == 0.5
        assert top[3]["total_influence"] == 0.3
        assert top[4]["total_influence"] == 0.1


# ============================================================================
# Test: Complete Safety Score Calculation
# ============================================================================


class TestCalculateSafetyScore:
    """Tests for end-to-end safety score calculation"""

    def test_no_accidents_zero_risk(self, reference_location, reference_weather):
        """No accidents should give 0 risk score"""
        result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type=reference_location["route_type"],
            current_date=reference_location["planned_date"],
            current_weather=reference_weather,
            accidents=[],
            historical_weather_stats=None,
        )
        assert isinstance(result, SafetyPrediction)
        assert result.risk_score == 0.0
        assert len(result.top_contributing_accidents) == 0

    def test_single_accident_calculation(
        self, reference_location, reference_weather, nearby_accident
    ):
        """Single nearby accident should produce reasonable risk"""
        result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type=reference_location["route_type"],
            current_date=reference_location["planned_date"],
            current_weather=reference_weather,
            accidents=[nearby_accident],
            historical_weather_stats=None,
        )
        assert isinstance(result, SafetyPrediction)
        assert result.risk_score > 0.0
        assert result.risk_score <= 100.0
        assert len(result.top_contributing_accidents) == 1

    def test_multiple_accidents_aggregation(
        self,
        reference_location,
        reference_weather,
        nearby_accident,
        distant_accident,
        old_accident,
    ):
        """Multiple accidents should aggregate influence"""
        result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type=reference_location["route_type"],
            current_date=reference_location["planned_date"],
            current_weather=reference_weather,
            accidents=[nearby_accident, distant_accident, old_accident],
            historical_weather_stats=None,
        )
        assert result.risk_score > 0.0
        assert len(result.top_contributing_accidents) == 3

    def test_many_accidents_increases_risk(self, reference_location, reference_weather):
        """Many nearby accidents should increase risk score"""
        # Create 50 accidents
        accidents = [
            AccidentData(
                accident_id=i,
                latitude=40.2549 + (i * 0.01),  # Spread out
                longitude=-105.6426 + (i * 0.01),
                elevation_meters=4000.0 + (i * 10),
                accident_date=date(2023, 7, 15) - timedelta(days=i * 10),
                route_type="alpine",
                severity="Minor Injury",
                weather_pattern=reference_weather,
            )
            for i in range(50)
        ]

        result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type=reference_location["route_type"],
            current_date=reference_location["planned_date"],
            current_weather=reference_weather,
            accidents=accidents,
            historical_weather_stats=None,
        )
        # With 50 accidents, risk should be higher
        assert result.risk_score > 0.0
        assert result.num_contributing_accidents == 50

    def test_top_contributing_accidents_limited_to_50(
        self, reference_location, reference_weather
    ):
        """Should limit top contributing accidents to 50"""
        # Create 100 accidents
        accidents = [
            AccidentData(
                accident_id=i,
                latitude=40.2549,
                longitude=-105.6426,
                elevation_meters=4346.0,
                accident_date=date(2023, 7, 15),
                route_type="alpine",
                severity="Minor Injury",
                weather_pattern=reference_weather,
            )
            for i in range(100)
        ]

        result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type=reference_location["route_type"],
            current_date=reference_location["planned_date"],
            current_weather=reference_weather,
            accidents=accidents,
            historical_weather_stats=None,
        )
        # Should return at most 50
        assert len(result.top_contributing_accidents) <= 50

    def test_seasonal_boost_same_season(self, reference_location, reference_weather):
        """Accident in same season should have higher influence"""
        summer_accident = AccidentData(
            accident_id=1,
            latitude=reference_location["latitude"],
            longitude=reference_location["longitude"],
            elevation_meters=reference_location["elevation"],
            accident_date=date(2023, 7, 15),  # Summer
            route_type=reference_location["route_type"],
            severity="Minor Injury",
            weather_pattern=reference_weather,
        )
        winter_accident = AccidentData(
            accident_id=2,
            latitude=reference_location["latitude"],
            longitude=reference_location["longitude"],
            elevation_meters=reference_location["elevation"],
            accident_date=date(2023, 1, 15),  # Winter
            route_type=reference_location["route_type"],
            severity="Minor Injury",
            weather_pattern=reference_weather,
        )

        summer_result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type=reference_location["route_type"],
            current_date=date(2024, 7, 15),  # Summer target
            current_weather=reference_weather,
            accidents=[summer_accident],
            historical_weather_stats=None,
        )
        winter_result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type=reference_location["route_type"],
            current_date=date(2024, 7, 15),  # Summer target
            current_weather=reference_weather,
            accidents=[winter_accident],
            historical_weather_stats=None,
        )

        # Summer accident should have higher risk (seasonal boost)
        assert summer_result.risk_score > winter_result.risk_score


# ============================================================================
# Test: Edge Cases
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling"""

    def test_null_weather_handled_gracefully(self, reference_location, nearby_accident):
        """Null weather should not crash (uses neutral weight 0.5)"""
        result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type=reference_location["route_type"],
            current_date=reference_location["planned_date"],
            current_weather=None,  # No weather data
            accidents=[nearby_accident],
            historical_weather_stats=None,
        )
        # Should still work with neutral weather weight
        assert result.risk_score > 0.0

    def test_accident_without_weather_pattern(self, reference_location, reference_weather):
        """Accident without weather pattern should use neutral weight"""
        accident_no_weather = AccidentData(
            accident_id=1,
            latitude=reference_location["latitude"],
            longitude=reference_location["longitude"],
            elevation_meters=reference_location["elevation"],
            accident_date=date(2023, 7, 15),
            route_type=reference_location["route_type"],
            severity="Minor Injury",
            weather_pattern=None,  # No weather pattern
        )

        result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type=reference_location["route_type"],
            current_date=reference_location["planned_date"],
            current_weather=reference_weather,
            accidents=[accident_no_weather],
            historical_weather_stats=None,
        )
        # Should still work
        assert result.risk_score > 0.0

    def test_default_route_type_handled(self, reference_location, reference_weather):
        """Default route type should work"""
        result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type="default",
            current_date=reference_location["planned_date"],
            current_weather=reference_weather,
            accidents=[],
            historical_weather_stats=None,
        )
        assert result.risk_score == 0.0

    def test_very_old_date_handled(self, reference_location, reference_weather):
        """Very old target date should not crash"""
        result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type=reference_location["route_type"],
            current_date=date(1900, 1, 1),  # Very old
            current_weather=reference_weather,
            accidents=[],
            historical_weather_stats=None,
        )
        assert result.risk_score == 0.0

    def test_future_date_handled(self, reference_location, reference_weather):
        """Future target date should work"""
        result = calculate_safety_score(
            route_lat=reference_location["latitude"],
            route_lon=reference_location["longitude"],
            route_elevation_m=reference_location["elevation"],
            route_type=reference_location["route_type"],
            current_date=date(2030, 12, 31),  # Future
            current_weather=reference_weather,
            accidents=[],
            historical_weather_stats=None,
        )
        assert result.risk_score == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
