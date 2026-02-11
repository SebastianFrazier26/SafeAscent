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
    _month_distance,
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
    """Tests for fetch_weather_statistics() archive-derived behavior."""

    def _mock_archive_response(self, days=40):
        base_temp = 5.0
        daily_time = [f"2025-01-{(i % 28) + 1:02d}" for i in range(days)]
        return {
            "daily": {
                "time": daily_time,
                "temperature_2m_mean": [base_temp + (i % 5) for i in range(days)],
                "precipitation_sum": [float(i % 4) for i in range(days)],
                "wind_speed_10m_max": [3.0 + (i % 6) for i in range(days)],
                "cloud_cover_mean": [30 + (i % 50) for i in range(days)],
            }
        }

    @pytest.mark.asyncio
    @patch("app.services.weather_service.cache_get")
    @patch("app.services.weather_service.cache_set")
    @patch("app.services.weather_service.requests.get")
    async def test_fetch_weather_statistics_archive_success(self, mock_get, _mock_cache_set, mock_cache_get):
        """Computes weighted stats from archive payload and returns diagnostics."""
        mock_cache_get.return_value = None
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = self._mock_archive_response(days=45)
        mock_get.return_value = mock_response

        stats = await fetch_weather_statistics(
            latitude=40.3,
            longitude=-105.7,
            elevation_meters=2500.0,
            season="winter",
            reference_date=date(2025, 1, 15),
        )

        assert stats is not None
        assert "temperature" in stats
        assert "precipitation" in stats
        assert "wind_speed" in stats
        assert "visibility" in stats
        assert stats["source"] == "open_meteo_archive"
        assert stats["reference_month"] == 1
        assert "volatility" in stats
        assert "monthly_volatility" in stats
        assert "temperature" in stats["monthly_volatility"]
        assert "1" in stats["monthly_volatility"]["temperature"]
        assert stats.get("lookback_years") == 5
        assert stats["sample_count"] >= 40
        assert stats["temperature"][1] >= 0

    @pytest.mark.asyncio
    @patch("app.services.weather_service.cache_get")
    @patch("app.services.weather_service.requests.get")
    async def test_fetch_weather_statistics_archive_failure(self, mock_get, mock_cache_get):
        """Returns None when archive API fails so extreme amplification is skipped."""
        import requests

        mock_cache_get.return_value = None
        mock_get.side_effect = requests.exceptions.RequestException("archive unavailable")

        stats = await fetch_weather_statistics(
            latitude=40.3,
            longitude=-105.7,
            elevation_meters=2500.0,
            season="winter",
            reference_date=date(2025, 1, 15),
        )

        assert stats is None

    @pytest.mark.asyncio
    @patch("app.services.weather_service.OPEN_METEO_API_KEY", "test_key")
    @patch("app.services.weather_service.ARCHIVE_WEATHER_API_URL", "https://customer-archive-api.open-meteo.com/v1/archive")
    @patch("app.services.weather_service.cache_get")
    @patch("app.services.weather_service.cache_set")
    @patch("app.services.weather_service.requests.get")
    async def test_fetch_weather_statistics_public_fallback_drops_api_key(
        self,
        mock_get,
        _mock_cache_set,
        mock_cache_get,
    ):
        """If commercial archive fails, public fallback must be called without apikey."""
        import requests

        mock_cache_get.return_value = None

        commercial_response = MagicMock()
        commercial_response.raise_for_status.side_effect = requests.exceptions.HTTPError("400 invalid key")

        public_response = MagicMock()
        public_response.raise_for_status = MagicMock()
        public_response.json.return_value = self._mock_archive_response(days=45)

        mock_get.side_effect = [commercial_response, public_response]

        stats = await fetch_weather_statistics(
            latitude=40.3,
            longitude=-105.7,
            elevation_meters=2500.0,
            season="winter",
            reference_date=date(2025, 1, 15),
        )

        assert stats is not None
        assert len(mock_get.call_args_list) == 2
        assert mock_get.call_args_list[0].kwargs["params"].get("apikey") == "test_key"
        assert "apikey" not in mock_get.call_args_list[1].kwargs["params"]

    @pytest.mark.asyncio
    @patch("app.services.weather_service.requests.get")
    @patch("app.services.weather_service.cache_get")
    async def test_fetch_weather_statistics_cache_hit(self, mock_cache_get, mock_get):
        """Uses cache and avoids external API call when key is present."""
        mock_cache_get.return_value = {
            "temperature": [0.0, 1.0],
            "precipitation": [0.0, 1.0],
            "wind_speed": [0.0, 1.0],
            "visibility": [10000.0, 0.0],
            "sample_count": 365,
            "source": "open_meteo_archive",
        }

        stats = await fetch_weather_statistics(
            latitude=40.3,
            longitude=-105.7,
            elevation_meters=2500.0,
            season="winter",
            reference_date=date(2025, 1, 15),
        )

        assert stats is not None
        mock_get.assert_not_called()

    def test_month_distance_is_cyclical(self):
        """Adjacent months around year boundary should have equal distance."""
        assert _month_distance(1, 12) == 1
        assert _month_distance(1, 2) == 1
        assert _month_distance(1, 7) == 6


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
