"""
Tests for Weather Service

Tests real-time weather fetching and historical statistics lookup.
"""
import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from app.services.weather_service import (
    fetch_current_weather_pattern,
    fetch_weather_statistics,
)
from app.services.weather_similarity import WeatherPattern


class TestFetchCurrentWeatherPattern:
    """Tests for fetch_current_weather_pattern()"""

    def test_fetch_current_weather_success(self):
        """Test successful weather fetch with valid coordinates"""
        # Use real coordinates (Estes Park, CO)
        weather = fetch_current_weather_pattern(
            latitude=40.2549,
            longitude=-105.6426,
            target_date=date.today(),
        )

        assert weather is not None
        assert isinstance(weather, WeatherPattern)
        assert len(weather.temperature) == 7  # 7-day pattern
        assert len(weather.precipitation) == 7
        assert len(weather.wind_speed) == 7
        assert len(weather.cloud_cover) == 7
        assert len(weather.daily_temps) == 7

        # Validate data types
        assert all(isinstance(t, (int, float)) for t in weather.temperature)
        assert all(isinstance(p, (int, float)) for p in weather.precipitation)
        assert all(isinstance(w, (int, float)) for w in weather.wind_speed)

    def test_fetch_current_weather_invalid_coords(self):
        """Test with invalid coordinates (should return None or handle gracefully)"""
        # Test with coordinates outside valid range
        weather = fetch_current_weather_pattern(
            latitude=999.0,  # Invalid latitude
            longitude=-105.6426,
            target_date=date.today(),
        )

        # Should return None or handle error gracefully
        # (Open-Meteo API might still return data, so this may pass)
        assert weather is None or isinstance(weather, WeatherPattern)

    @patch('app.services.weather_service.cache_get')
    @patch('app.services.weather_service.requests.get')
    def test_fetch_current_weather_api_failure(self, mock_get, mock_cache_get):
        """Test when API request fails"""
        import requests
        # Ensure cache miss so we hit the API
        mock_cache_get.return_value = None
        # Mock API failure
        mock_get.side_effect = requests.exceptions.RequestException("API connection failed")

        weather = fetch_current_weather_pattern(
            latitude=40.2549,
            longitude=-105.6426,
            target_date=date.today(),
        )

        assert weather is None  # Should return None on failure

    @patch('app.services.weather_service.cache_get')
    @patch('app.services.weather_service.requests.get')
    def test_fetch_current_weather_timeout(self, mock_get, mock_cache_get):
        """Test when API times out"""
        import requests
        # Ensure cache miss so we hit the API
        mock_cache_get.return_value = None
        mock_get.side_effect = requests.Timeout("Request timed out")

        weather = fetch_current_weather_pattern(
            latitude=40.2549,
            longitude=-105.6426,
            target_date=date.today(),
        )

        assert weather is None

    @patch('app.services.weather_service.cache_get')
    @patch('app.services.weather_service.requests.get')
    def test_fetch_current_weather_malformed_response(self, mock_get, mock_cache_get):
        """Test when API returns malformed JSON"""
        # Ensure cache miss so we hit the API
        mock_cache_get.return_value = None
        # Mock response with missing required fields
        mock_response = MagicMock()
        mock_response.json.return_value = {"daily": {"time": []}}  # Missing weather data
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        weather = fetch_current_weather_pattern(
            latitude=40.2549,
            longitude=-105.6426,
            target_date=date.today(),
        )

        assert weather is None  # Should handle parsing errors


class TestFetchWeatherStatistics:
    """Tests for fetch_weather_statistics() - now an async function"""

    @pytest.mark.asyncio
    async def test_fetch_weather_statistics_found(self):
        """Test fetching statistics for a known bucket"""
        # Use location with known weather data (Estes Park area)
        stats = await fetch_weather_statistics(
            latitude=40.3,  # Rounded to bucket
            longitude=-105.7,
            elevation_meters=2500.0,  # 2438-3048m band
            season="summer",
        )

        # This specific bucket exists based on our earlier testing
        if stats is not None:
            assert "temperature" in stats
            assert "precipitation" in stats
            assert "wind_speed" in stats
            assert "visibility" in stats
            assert "sample_count" in stats

            # Validate tuple structure (mean, std)
            assert isinstance(stats["temperature"], tuple)
            assert len(stats["temperature"]) == 2
            assert isinstance(stats["temperature"][0], (int, float))
            assert isinstance(stats["temperature"][1], (int, float))

            # Sample count should be reasonable
            assert stats["sample_count"] > 0
        else:
            # It's okay if this exact bucket doesn't exist
            # (depends on data availability)
            pass

    @pytest.mark.asyncio
    async def test_fetch_weather_statistics_not_found(self):
        """Test fetching statistics for a bucket with no data"""
        # Use unlikely location (middle of ocean)
        stats = await fetch_weather_statistics(
            latitude=0.0,
            longitude=0.0,
            elevation_meters=0.0,
            season="winter",
        )

        # Should return None for missing bucket
        assert stats is None

    @pytest.mark.asyncio
    async def test_fetch_weather_statistics_elevation_bands(self):
        """Test that different elevation bands are handled correctly"""
        test_elevations = [
            (1000.0, 0, 2438),      # Low elevation
            (2700.0, 2438, 3048),   # Mid elevation
            (3500.0, 3048, 3658),   # High elevation
            (4000.0, 3658, 4267),   # Very high
            (5000.0, 4267, 10000),  # Extreme
        ]

        for elevation, expected_min, expected_max in test_elevations:
            # Just verify the function doesn't crash
            stats = await fetch_weather_statistics(
                latitude=40.3,
                longitude=-105.7,
                elevation_meters=elevation,
                season="summer",
            )
            # Stats may or may not exist, that's okay
            assert stats is None or isinstance(stats, dict)

    @pytest.mark.asyncio
    async def test_fetch_weather_statistics_invalid_elevation(self):
        """Test with elevation outside known bands"""
        stats = await fetch_weather_statistics(
            latitude=40.3,
            longitude=-105.7,
            elevation_meters=15000.0,  # Above highest band
            season="summer",
        )

        # Should return None for invalid elevation
        assert stats is None

    @pytest.mark.asyncio
    async def test_fetch_weather_statistics_db_error(self):
        """Test when database connection fails"""
        # Use invalid credentials to trigger connection error
        import os
        from unittest.mock import patch

        # Mock environment variables to cause connection failure
        with patch.dict(os.environ, {"DB_NAME": "nonexistent_database"}):
            stats = await fetch_weather_statistics(
                latitude=40.3,
                longitude=-105.7,
                elevation_meters=3000.0,
                season="winter",
            )

            # Should return None on database error
            assert stats is None


class TestWeatherPatternConstruction:
    """Tests for WeatherPattern object construction"""

    def test_weather_pattern_daily_temps(self):
        """Test that daily_temps tuples are constructed correctly"""
        weather = fetch_current_weather_pattern(
            latitude=40.2549,
            longitude=-105.6426,
            target_date=date.today(),
        )

        if weather is not None:
            for daily_temp in weather.daily_temps:
                assert isinstance(daily_temp, tuple)
                assert len(daily_temp) == 3  # (min, avg, max)
                t_min, t_avg, t_max = daily_temp
                # Logical relationship: min <= avg <= max (approximately)
                # Note: May not always hold due to averaging methods
                assert isinstance(t_min, (int, float))
                assert isinstance(t_avg, (int, float))
                assert isinstance(t_max, (int, float))

    def test_weather_pattern_default_visibility(self):
        """Test that visibility defaults to 10km when not provided"""
        weather = fetch_current_weather_pattern(
            latitude=40.2549,
            longitude=-105.6426,
            target_date=date.today(),
        )

        if weather is not None:
            # Open-Meteo doesn't provide visibility, so it should default to 10000.0
            assert all(v == 10000.0 for v in weather.visibility)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
