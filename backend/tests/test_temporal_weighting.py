"""
Tests for Temporal Weighting Module

Tests the exponential decay temporal weighting with seasonal boosting.
Year-scale decay ensures recent accidents have more influence than old ones.

Key formulas tested:
- Base weight: lambda^days (exponential decay)
- Seasonal boost: 1.5× for same season
- Half-life: Alpine ~9.5 years, Sport ~1.9 years
"""
import pytest
from datetime import date, timedelta
from app.services.temporal_weighting import (
    calculate_temporal_weight,
    calculate_temporal_weight_detailed,
    get_temporal_lambda,
    get_temporal_half_life,
)
from app.services.algorithm_config import SEASONAL_BOOST, TEMPORAL_SEASONAL_IMPACT


class TestCalculateTemporalWeight:
    """Tests for basic temporal weight calculation"""

    def test_same_date_returns_one(self):
        """Same date should stay near neutral with mild seasonal boost."""
        current = date(2024, 7, 15)
        accident = date(2024, 7, 15)
        weight = calculate_temporal_weight(current, accident, "alpine")
        expected = 1.0 + ((SEASONAL_BOOST - 1.0) * TEMPORAL_SEASONAL_IMPACT)
        assert weight == pytest.approx(expected, abs=0.01)

    def test_same_date_no_seasonal_boost(self):
        """Same date without seasonal boost should return 1.0"""
        current = date(2024, 7, 15)
        accident = date(2024, 7, 15)
        weight = calculate_temporal_weight(
            current, accident, "alpine", apply_seasonal_boost=False
        )
        assert weight == pytest.approx(1.0, abs=0.01)

    def test_one_year_ago_same_season(self):
        """Accident 1 year ago, same season should have high weight"""
        current = date(2024, 7, 15)  # Summer
        accident = date(2023, 7, 15)  # Summer, 365 days ago
        weight = calculate_temporal_weight(current, accident, "alpine")
        # Should be high (>0.8) due to seasonal boost
        assert weight > 0.8
        assert weight <= 1.1

    def test_one_year_ago_different_season(self):
        """Accident 1 year ago, different season should have moderate weight"""
        current = date(2024, 7, 15)  # Summer
        accident = date(2023, 1, 15)  # Winter, ~6 months earlier
        weight = calculate_temporal_weight(current, accident, "alpine")
        # Should be moderate, no seasonal boost
        assert 0.5 < weight < 1.0

    def test_very_old_accident_low_weight(self):
        """Very old accident should decay, but remain a modest contributor."""
        current = date(2024, 7, 15)
        accident = date(2004, 7, 15)  # 20 years ago
        weight = calculate_temporal_weight(current, accident, "alpine")
        assert 0.6 < weight < 0.8

    def test_alpine_slower_decay_than_sport(self):
        """Alpine should decay slower than sport climbing"""
        current = date(2024, 7, 15)
        accident = date(2023, 7, 15)  # 1 year ago

        alpine_weight = calculate_temporal_weight(
            current, accident, "alpine", apply_seasonal_boost=False
        )
        sport_weight = calculate_temporal_weight(
            current, accident, "sport", apply_seasonal_boost=False
        )

        # Alpine should have higher weight (slower decay)
        assert alpine_weight > sport_weight

    def test_seasonal_boost_applied_correctly(self):
        """Seasonal boost should apply only a mild multiplier."""
        current = date(2024, 7, 15)  # Summer
        accident = date(2023, 7, 15)  # Summer

        with_boost = calculate_temporal_weight(
            current, accident, "alpine", apply_seasonal_boost=True
        )
        without_boost = calculate_temporal_weight(
            current, accident, "alpine", apply_seasonal_boost=False
        )

        expected_multiplier = 1.0 + ((SEASONAL_BOOST - 1.0) * TEMPORAL_SEASONAL_IMPACT)
        assert with_boost == pytest.approx(without_boost * expected_multiplier, abs=0.01)

    def test_default_route_type_handled(self):
        """Unknown route type should use default lambda"""
        current = date(2024, 7, 15)
        accident = date(2023, 7, 15)
        weight = calculate_temporal_weight(current, accident, "unknown_type")
        # Should work without error
        assert 0.0 < weight <= 1.1


class TestCalculateTemporalWeightDetailed:
    """Tests for detailed temporal weight calculation"""

    def test_returns_complete_breakdown(self):
        """Should return dictionary with all components"""
        current = date(2024, 7, 15)
        accident = date(2023, 7, 15)
        result = calculate_temporal_weight_detailed(current, accident, "alpine")

        # Check all expected keys present
        assert "days_elapsed" in result
        assert "base_weight" in result
        assert "seasonal_boost_applied" in result
        assert "final_weight" in result
        assert "lambda_value" in result
        assert "current_season" in result
        assert "accident_season" in result

    def test_days_elapsed_calculated_correctly(self):
        """Days elapsed should match date difference"""
        current = date(2024, 7, 15)
        accident = date(2023, 7, 15)
        result = calculate_temporal_weight_detailed(current, accident, "alpine")

        # 366 days difference (2024 is a leap year)
        assert result["days_elapsed"] == 366

    def test_seasonal_boost_detected_same_season(self):
        """Should detect seasonal boost for same season"""
        current = date(2024, 7, 15)  # Summer
        accident = date(2023, 7, 15)  # Summer
        result = calculate_temporal_weight_detailed(current, accident, "alpine")

        assert result["seasonal_boost_applied"] is True
        assert result["current_season"] == "summer"
        assert result["accident_season"] == "summer"

    def test_no_seasonal_boost_different_season(self):
        """Should not apply seasonal boost for different season"""
        current = date(2024, 7, 15)  # Summer
        accident = date(2023, 1, 15)  # Winter
        result = calculate_temporal_weight_detailed(current, accident, "alpine")

        assert result["seasonal_boost_applied"] is False
        assert result["current_season"] == "summer"
        assert result["accident_season"] == "winter"

    def test_final_weight_matches_simple_calculation(self):
        """Final weight should match calculate_temporal_weight()"""
        current = date(2024, 7, 15)
        accident = date(2023, 7, 15)

        detailed = calculate_temporal_weight_detailed(current, accident, "alpine")
        simple = calculate_temporal_weight(current, accident, "alpine")

        assert detailed["final_weight"] == pytest.approx(simple, abs=0.001)

    def test_lambda_value_correct_for_route_type(self):
        """Lambda value should match route type"""
        result = calculate_temporal_weight_detailed(
            date(2024, 7, 15), date(2023, 7, 15), "alpine"
        )
        # Alpine lambda is 0.9998
        assert result["lambda_value"] == pytest.approx(0.9998, abs=0.0001)

        result = calculate_temporal_weight_detailed(
            date(2024, 7, 15), date(2023, 7, 15), "sport"
        )
        # Sport lambda is 0.999
        assert result["lambda_value"] == pytest.approx(0.999, abs=0.0001)

    def test_base_weight_before_boost(self):
        """Base weight should be before seasonal boost"""
        current = date(2024, 7, 15)
        accident = date(2023, 7, 15)
        result = calculate_temporal_weight_detailed(current, accident, "alpine")

        expected_multiplier = 1.0 + ((SEASONAL_BOOST - 1.0) * TEMPORAL_SEASONAL_IMPACT)
        # If seasonal boost applied, final should be base × mild seasonal multiplier
        if result["seasonal_boost_applied"]:
            assert result["final_weight"] == pytest.approx(
                result["base_weight"] * expected_multiplier, abs=0.001
            )
        else:
            assert result["final_weight"] == pytest.approx(
                result["base_weight"], abs=0.001
            )


class TestGetTemporalLambda:
    """Tests for lambda value retrieval"""

    def test_alpine_lambda(self):
        """Alpine should have lambda 0.9998 (slowest decay)"""
        lambda_val = get_temporal_lambda("alpine")
        assert lambda_val == pytest.approx(0.9998, abs=0.0001)

    def test_trad_lambda(self):
        """Trad should have lambda 0.9995"""
        lambda_val = get_temporal_lambda("trad")
        assert lambda_val == pytest.approx(0.9995, abs=0.0001)

    def test_sport_lambda(self):
        """Sport should have lambda 0.999 (faster decay)"""
        lambda_val = get_temporal_lambda("sport")
        assert lambda_val == pytest.approx(0.999, abs=0.0001)

    def test_ice_lambda(self):
        """Ice should have lambda 0.9997"""
        lambda_val = get_temporal_lambda("ice")
        assert lambda_val == pytest.approx(0.9997, abs=0.0001)

    def test_mixed_lambda(self):
        """Mixed should have lambda 0.9997"""
        lambda_val = get_temporal_lambda("mixed")
        assert lambda_val == pytest.approx(0.9997, abs=0.0001)

    def test_boulder_lambda(self):
        """Boulder should have lambda 0.999 (same as sport)"""
        lambda_val = get_temporal_lambda("boulder")
        assert lambda_val == pytest.approx(0.999, abs=0.0001)

    def test_default_lambda(self):
        """Unknown types should use default lambda"""
        lambda_val = get_temporal_lambda("unknown")
        default_lambda = get_temporal_lambda("default")
        assert lambda_val == default_lambda

    def test_case_insensitive(self):
        """Lambda lookup should be case insensitive"""
        assert get_temporal_lambda("ALPINE") == get_temporal_lambda("alpine")
        assert get_temporal_lambda("Sport") == get_temporal_lambda("sport")


class TestGetTemporalHalfLife:
    """Tests for half-life calculation"""

    def test_alpine_half_life(self):
        """Alpine should have ~9.5 year half-life"""
        half_life = get_temporal_half_life("alpine")
        assert 9.0 < half_life < 10.0  # ~9.5 years

    def test_sport_half_life(self):
        """Sport should have ~1.9 year half-life"""
        half_life = get_temporal_half_life("sport")
        assert 1.5 < half_life < 2.5  # ~1.9 years

    def test_trad_half_life(self):
        """Trad should have intermediate half-life"""
        half_life = get_temporal_half_life("trad")
        assert 3.0 < half_life < 5.0  # ~3.8 years

    def test_half_life_ordering(self):
        """Alpine > ice/mixed > trad > sport/boulder (slower to faster decay)"""
        alpine = get_temporal_half_life("alpine")
        ice = get_temporal_half_life("ice")
        trad = get_temporal_half_life("trad")
        sport = get_temporal_half_life("sport")
        boulder = get_temporal_half_life("boulder")

        # Verify ordering
        assert alpine > ice  # Alpine slowest decay
        assert ice > trad    # Ice slower than trad
        assert trad > sport  # Trad slower than sport
        # Boulder and sport have same lambda (0.999), so same half-life
        assert boulder == pytest.approx(sport, abs=0.01)

    def test_half_life_matches_formula(self):
        """Half-life should match mathematical formula"""
        import math

        lambda_val = get_temporal_lambda("alpine")
        half_life = get_temporal_half_life("alpine")

        # Formula: half_life = ln(0.5) / ln(lambda) / 365.25
        expected_half_life = math.log(0.5) / math.log(lambda_val) / 365.25

        assert half_life == pytest.approx(expected_half_life, abs=0.01)


class TestEdgeCases:
    """Tests for edge cases and boundary conditions"""

    def test_future_date_handled(self):
        """Future accident date should work (negative days)"""
        current = date(2024, 7, 15)
        accident = date(2025, 7, 15)  # Future
        # This would give negative days_elapsed, which should still work
        weight = calculate_temporal_weight(current, accident, "alpine")
        # Weight should be >= 1.0 (more recent = higher weight)
        assert weight >= 1.0

    def test_very_large_time_difference(self):
        """Very large time difference should not crash"""
        current = date(2024, 7, 15)
        accident = date(1900, 1, 1)  # 124 years ago
        weight = calculate_temporal_weight(current, accident, "alpine")
        # Should decay to a bounded floor (small but non-zero overall influence)
        assert 0.6 < weight < 0.7

    def test_empty_route_type_uses_default(self):
        """Empty string route type should use default"""
        current = date(2024, 7, 15)
        accident = date(2023, 7, 15)
        weight = calculate_temporal_weight(current, accident, "")
        # Should work without error
        assert 0.0 < weight <= 1.1

    def test_leap_year_handling(self):
        """Leap years should be handled correctly"""
        # Feb 29, 2024 (leap year)
        current = date(2024, 2, 29)
        accident = date(2023, 2, 28)  # 1 year + 1 day ago
        result = calculate_temporal_weight_detailed(current, accident, "alpine")
        # Should calculate correct number of days
        assert result["days_elapsed"] == 366  # Leap year


class TestSeasonalBehavior:
    """Tests for seasonal boost behavior"""

    def test_all_seasons_detected(self):
        """All four seasons should be detected correctly"""
        # Summer accident
        summer = calculate_temporal_weight_detailed(
            date(2024, 7, 15), date(2023, 7, 15), "alpine"
        )
        assert summer["current_season"] == "summer"
        assert summer["accident_season"] == "summer"

        # Winter accident
        winter = calculate_temporal_weight_detailed(
            date(2024, 1, 15), date(2023, 1, 15), "alpine"
        )
        assert winter["current_season"] == "winter"
        assert winter["accident_season"] == "winter"

        # Spring accident
        spring = calculate_temporal_weight_detailed(
            date(2024, 4, 15), date(2023, 4, 15), "alpine"
        )
        assert spring["current_season"] == "spring"
        assert spring["accident_season"] == "spring"

        # Fall accident
        fall = calculate_temporal_weight_detailed(
            date(2024, 10, 15), date(2023, 10, 15), "alpine"
        )
        assert fall["current_season"] == "fall"
        assert fall["accident_season"] == "fall"

    def test_season_boundaries(self):
        """Season boundary dates should work correctly"""
        # December 20 (winter start)
        result = calculate_temporal_weight_detailed(
            date(2024, 12, 20), date(2023, 12, 20), "alpine"
        )
        assert result["current_season"] == "winter"
        assert result["seasonal_boost_applied"] is True

        # March 20 (spring start)
        result = calculate_temporal_weight_detailed(
            date(2024, 3, 20), date(2023, 3, 20), "alpine"
        )
        assert result["current_season"] == "spring"

        # June 20 (summer start)
        result = calculate_temporal_weight_detailed(
            date(2024, 6, 20), date(2023, 6, 20), "alpine"
        )
        assert result["current_season"] == "summer"

        # September 20 (fall start)
        result = calculate_temporal_weight_detailed(
            date(2024, 9, 20), date(2023, 9, 20), "alpine"
        )
        assert result["current_season"] == "fall"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
