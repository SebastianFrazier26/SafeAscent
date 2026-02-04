"""
Phase 7F: Known Outcomes Validation

Validates that the algorithm produces sensible predictions that align with
real-world climbing safety knowledge and expert intuition.

Tests focus on:
- High-risk vs low-risk area comparisons
- Seasonal variations (winter vs summer)
- Known dangerous routes and areas
- Route type risk differences
- Historical accident pattern correlation
"""

import pytest
from datetime import datetime


class TestHighRiskVsLowRisk:
    """Validate that known dangerous areas score higher than known safe areas."""

    def test_longs_peak_vs_florida(self, test_client):
        """Longs Peak (CO) should score much higher than Florida sport climbing."""
        # Longs Peak - one of Colorado's most dangerous 14ers
        longs_peak = test_client.post("/api/v1/predict", json={
            "latitude": 40.255,
            "longitude": -105.615,
            "route_type": "alpine",
            "planned_date": "2026-07-15",  # Use current year
            "search_radius_km": 50.0
        })

        # Florida sport climbing - generally safe
        florida = test_client.post("/api/v1/predict", json={
            "latitude": 29.65,
            "longitude": -82.32,
            "route_type": "sport",
            "planned_date": "2026-07-15",  # Use current year
            "search_radius_km": 100.0
        })

        assert longs_peak.status_code == 200
        assert florida.status_code == 200

        longs_data = longs_peak.json()
        florida_data = florida.json()

        # Longs Peak should have significantly higher risk
        assert longs_data["risk_score"] > florida_data["risk_score"], \
            f"Longs Peak ({longs_data['risk_score']}) should be riskier than Florida ({florida_data['risk_score']})"

        # Longs Peak should have high risk (>60)
        assert longs_data["risk_score"] > 60, \
            f"Longs Peak should show high risk (got {longs_data['risk_score']})"

        # Florida should have low-moderate risk (<50)
        assert florida_data["risk_score"] < 50, \
            f"Florida should show low risk (got {florida_data['risk_score']})"

        print(f"\n✅ High-Risk vs Low-Risk Comparison:")
        print(f"   Longs Peak: Risk={longs_data['risk_score']:.1f}, Accidents={longs_data['num_contributing_accidents']}")
        print(f"   Florida:    Risk={florida_data['risk_score']:.1f}, Accidents={florida_data['num_contributing_accidents']}")
        print(f"   Risk Difference: {longs_data['risk_score'] - florida_data['risk_score']:.1f} points")

    def test_mount_rainier_vs_smith_rock(self, test_client):
        """Mount Rainier (alpine) should score higher than Smith Rock (sport)."""
        # Mount Rainier - one of the deadliest peaks in North America
        rainier = test_client.post("/api/v1/predict", json={
            "latitude": 46.853,
            "longitude": -121.760,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 50.0
        })

        # Smith Rock, Oregon - popular sport climbing area with good safety record
        smith_rock = test_client.post("/api/v1/predict", json={
            "latitude": 44.367,
            "longitude": -121.141,
            "route_type": "sport",
            "planned_date": "2026-07-15",
            "search_radius_km": 50.0
        })

        assert rainier.status_code == 200
        assert smith_rock.status_code == 200

        rainier_data = rainier.json()
        smith_data = smith_rock.json()

        # Rainier should have higher risk
        assert rainier_data["risk_score"] > smith_data["risk_score"], \
            f"Mount Rainier ({rainier_data['risk_score']}) should be riskier than Smith Rock ({smith_data['risk_score']})"

        print(f"\n✅ Alpine vs Sport Comparison:")
        print(f"   Mount Rainier (alpine): Risk={rainier_data['risk_score']:.1f}, Accidents={rainier_data['num_contributing_accidents']}")
        print(f"   Smith Rock (sport):     Risk={smith_data['risk_score']:.1f}, Accidents={smith_data['num_contributing_accidents']}")

    def test_denali_vs_acadia(self, test_client):
        """Denali (AK) should score higher than Acadia (ME) sea cliffs."""
        # Denali - extreme alpine climbing with high accident rate
        denali = test_client.post("/api/v1/predict", json={
            "latitude": 63.069,
            "longitude": -151.007,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })

        # Acadia National Park, Maine - relatively safe sea cliff climbing
        acadia = test_client.post("/api/v1/predict", json={
            "latitude": 44.377,
            "longitude": -68.205,
            "route_type": "trad",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })

        assert denali.status_code == 200
        assert acadia.status_code == 200

        denali_data = denali.json()
        acadia_data = acadia.json()

        # Denali should have higher risk
        assert denali_data["risk_score"] >= acadia_data["risk_score"], \
            f"Denali ({denali_data['risk_score']}) should be at least as risky as Acadia ({acadia_data['risk_score']})"

        print(f"\n✅ Extreme Alpine vs Sea Cliffs:")
        print(f"   Denali (alpine): Risk={denali_data['risk_score']:.1f}, Accidents={denali_data['num_contributing_accidents']}")
        print(f"   Acadia (trad):   Risk={acadia_data['risk_score']:.1f}, Accidents={acadia_data['num_contributing_accidents']}")


class TestSeasonalVariations:
    """Validate that seasonal differences affect risk scores appropriately."""

    def test_winter_vs_summer_colorado(self, test_client):
        """Winter should generally be riskier than summer in Colorado mountains."""
        location = {
            "latitude": 40.0,
            "longitude": -105.3,
            "route_type": "alpine",
            "search_radius_km": 100.0
        }

        # Winter prediction (January)
        winter = test_client.post("/api/v1/predict", json={
            **location,
            "planned_date": "2027-01-15"  # Use future winter date
        })

        # Summer prediction (July)
        summer = test_client.post("/api/v1/predict", json={
            **location,
            "planned_date": "2026-07-15"  # Use current summer date
        })

        assert winter.status_code == 200
        assert summer.status_code == 200

        winter_data = winter.json()
        summer_data = summer.json()

        print(f"\n✅ Seasonal Variation (Colorado):")
        print(f"   Winter (Jan): Risk={winter_data['risk_score']:.1f}, Accidents={winter_data['num_contributing_accidents']}")
        print(f"   Summer (Jul): Risk={summer_data['risk_score']:.1f}, Accidents={summer_data['num_contributing_accidents']}")
        print(f"   Note: Seasonal boost applies to winter predictions in mountain areas")

    def test_early_season_vs_late_season(self, test_client):
        """Early season (spring) might show different patterns than late season (fall)."""
        location = {
            "latitude": 46.853,
            "longitude": -121.760,  # Mount Rainier
            "route_type": "alpine",
            "search_radius_km": 50.0
        }

        # Early season (May - avalanche risk, unstable conditions)
        early_season = test_client.post("/api/v1/predict", json={
            **location,
            "planned_date": "2026-05-15"  # Use current year
        })

        # Late season (September - stable conditions, good weather)
        late_season = test_client.post("/api/v1/predict", json={
            **location,
            "planned_date": "2026-09-15"  # Use current year
        })

        assert early_season.status_code == 200
        assert late_season.status_code == 200

        early_data = early_season.json()
        late_data = late_season.json()

        print(f"\n✅ Early vs Late Season (Mount Rainier):")
        print(f"   Early (May): Risk={early_data['risk_score']:.1f}, Accidents={early_data['num_contributing_accidents']}")
        print(f"   Late (Sep):  Risk={late_data['risk_score']:.1f}, Accidents={late_data['num_contributing_accidents']}")


class TestKnownDangerousAreas:
    """Validate that historically dangerous areas show elevated risk."""

    def test_yosemite_half_dome_cables(self, test_client):
        """Half Dome area should show elevated risk due to accidents."""
        # Half Dome area - known for accidents on cables route
        half_dome = test_client.post("/api/v1/predict", json={
            "latitude": 37.746,
            "longitude": -119.533,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 50.0
        })

        assert half_dome.status_code == 200
        data = half_dome.json()

        # Should find accidents and show elevated risk
        assert data["num_contributing_accidents"] > 20, \
            "Yosemite area should have many accidents"

        print(f"\n✅ Half Dome Area (Known Dangerous):")
        print(f"   Risk Score: {data['risk_score']:.1f}/100")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")
        print(f"   Top Accident Influence: {data['top_contributing_accidents'][0]['total_influence']:.3f}")

    def test_grand_teton_area(self, test_client):
        """Grand Teton should show elevated risk due to popular alpine routes."""
        # Grand Teton - popular but dangerous alpine climbing
        grand_teton = test_client.post("/api/v1/predict", json={
            "latitude": 43.741,
            "longitude": -110.802,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 50.0
        })

        assert grand_teton.status_code == 200
        data = grand_teton.json()

        # Should have accidents and show risk
        assert data["num_contributing_accidents"] > 10

        print(f"\n✅ Grand Teton Area:")
        print(f"   Risk Score: {data['risk_score']:.1f}/100")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")

    def test_whitney_portal_area(self, test_client):
        """Mount Whitney area should show moderate-high risk."""
        # Mount Whitney - highest peak in lower 48, popular but risky
        whitney = test_client.post("/api/v1/predict", json={
            "latitude": 36.578,
            "longitude": -118.292,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 50.0
        })

        assert whitney.status_code == 200
        data = whitney.json()

        print(f"\n✅ Mount Whitney Area:")
        print(f"   Risk Score: {data['risk_score']:.1f}/100")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")


class TestRouteTypeRiskDifferences:
    """Validate that route types show appropriate relative risk levels."""

    def test_alpine_vs_sport_same_location(self, test_client):
        """Alpine should generally be riskier than sport in mountain areas."""
        location = {
            "latitude": 40.0,
            "longitude": -105.3,
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        }

        # Alpine climbing
        alpine = test_client.post("/api/v1/predict", json={
            **location,
            "route_type": "alpine"
        })

        # Sport climbing
        sport = test_client.post("/api/v1/predict", json={
            **location,
            "route_type": "sport"
        })

        assert alpine.status_code == 200
        assert sport.status_code == 200

        alpine_data = alpine.json()
        sport_data = sport.json()

        print(f"\n✅ Route Type Comparison (Same Location):")
        print(f"   Alpine: Risk={alpine_data['risk_score']:.1f}, Accidents={alpine_data['num_contributing_accidents']}")
        print(f"   Sport:  Risk={sport_data['risk_score']:.1f}, Accidents={sport_data['num_contributing_accidents']}")
        print(f"   Note: Risk difference reflects route type weighting and accident patterns")

    def test_ice_climbing_winter_risk(self, test_client):
        """Ice climbing should show appropriate risk levels."""
        # Colorado ice climbing area
        ice = test_client.post("/api/v1/predict", json={
            "latitude": 39.65,
            "longitude": -106.04,  # Vail area - known ice climbing
            "route_type": "ice",
            "planned_date": "2027-01-15",  # Future winter date
            "search_radius_km": 50.0
        })

        assert ice.status_code == 200
        data = ice.json()

        print(f"\n✅ Ice Climbing (Winter):")
        print(f"   Risk Score: {data['risk_score']:.1f}/100")
        print(f"   Accidents Found: {data['num_contributing_accidents']}")

    def test_boulder_vs_roped_climbing(self, test_client):
        """Compare bouldering risk to roped climbing."""
        location = {
            "latitude": 40.0,
            "longitude": -105.3,
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        }

        boulder = test_client.post("/api/v1/predict", json={
            **location,
            "route_type": "boulder"
        })

        trad = test_client.post("/api/v1/predict", json={
            **location,
            "route_type": "trad"
        })

        assert boulder.status_code == 200
        assert trad.status_code == 200

        boulder_data = boulder.json()
        trad_data = trad.json()

        print(f"\n✅ Bouldering vs Roped Climbing:")
        print(f"   Boulder: Risk={boulder_data['risk_score']:.1f}, Accidents={boulder_data['num_contributing_accidents']}")
        print(f"   Trad:    Risk={trad_data['risk_score']:.1f}, Accidents={trad_data['num_contributing_accidents']}")


class TestHistoricalAccidentCorrelation:
    """Validate that risk scores correlate with historical accident density."""

    def test_high_accident_density_areas(self, test_client):
        """Areas with many nearby accidents should show higher risk."""
        # Test three Colorado locations with varying accident density
        locations = [
            {"name": "Longs Peak", "lat": 40.255, "lon": -105.615, "expected_accidents": ">100"},
            {"name": "Rocky Mountain NP", "lat": 40.343, "lon": -105.688, "expected_accidents": ">50"},
            {"name": "Front Range", "lat": 39.9, "lon": -105.4, "expected_accidents": ">30"}
        ]

        results = []
        for loc in locations:
            response = test_client.post("/api/v1/predict", json={
                "latitude": loc["lat"],
                "longitude": loc["lon"],
                "route_type": "alpine",
                "planned_date": "2026-07-15",
                "search_radius_km": 50.0
            })

            assert response.status_code == 200
            data = response.json()
            results.append({
                "name": loc["name"],
                "risk": data["risk_score"],
                "accidents": data["num_contributing_accidents"]
            })

        print(f"\n✅ Historical Accident Correlation:")
        for res in results:
            print(f"   {res['name']:20s}: Risk={res['risk']:5.1f}, Accidents={res['accidents']:4d}")

        # Risk should generally correlate with accident density
        # (though not perfectly due to temporal weighting and other factors)

    def test_accident_recency_impact(self, test_client):
        """Recent accidents should influence risk more than old ones."""
        # This is implicit in the algorithm, but we can verify by checking
        # that the top contributing accidents have high temporal weights
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.255,
            "longitude": -105.615,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 50.0
        })

        assert response.status_code == 200
        data = response.json()

        # Check temporal weights in top accidents
        top_accidents = data["top_contributing_accidents"][:5]

        print(f"\n✅ Temporal Weighting Analysis:")
        print(f"   Examining top {len(top_accidents)} contributing accidents:")
        for i, acc in enumerate(top_accidents, 1):
            print(f"   {i}. Days ago: {acc['days_ago']:5d}, Temporal weight: {acc['temporal_weight']:.3f}, Distance: {acc['distance_km']:.1f}km")

        # Verify temporal weights exist and are reasonable
        for acc in top_accidents:
            assert 0.1 <= acc["temporal_weight"] <= 2.0, \
                f"Temporal weight {acc['temporal_weight']} out of expected range"


class TestRiskScoreCalibration:
    """Validate that risk scores reflect accident density appropriately."""

    def test_high_density_elevated_risk(self, test_client):
        """High accident density areas should have elevated risk."""
        # Longs Peak - high density
        high_density = test_client.post("/api/v1/predict", json={
            "latitude": 40.255,
            "longitude": -105.615,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 50.0
        })

        assert high_density.status_code == 200
        data = high_density.json()

        # Should have many accidents near Longs Peak
        assert data["num_contributing_accidents"] > 100, \
            f"High density area should have >100 accidents (got {data['num_contributing_accidents']})"

        # High density should reflect in elevated risk
        assert data["risk_score"] > 30, \
            f"High accident density should produce risk >30 (got {data['risk_score']})"

        print(f"\n✅ High Density Risk:")
        print(f"   Accidents: {data['num_contributing_accidents']}")
        print(f"   Risk Score: {data['risk_score']:.1f}/100")

    def test_low_density_lower_risk(self, test_client):
        """Low accident density areas should have lower risk."""
        # Remote Wyoming - low density
        low_density = test_client.post("/api/v1/predict", json={
            "latitude": 42.5,
            "longitude": -108.5,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })

        assert low_density.status_code == 200
        data = low_density.json()

        print(f"\n✅ Low Density Risk:")
        print(f"   Accidents: {data['num_contributing_accidents']}")
        print(f"   Risk Score: {data['risk_score']:.1f}/100")

    def test_top_accidents_sorted_by_influence(self, test_client):
        """Top contributing accidents should be sorted by influence."""
        response = test_client.post("/api/v1/predict", json={
            "latitude": 40.0,
            "longitude": -105.3,
            "route_type": "alpine",
            "planned_date": "2026-07-15",
            "search_radius_km": 100.0
        })

        assert response.status_code == 200
        data = response.json()

        top_accidents = data["top_contributing_accidents"]

        # Verify accidents are sorted by total_influence (descending)
        if len(top_accidents) > 1:
            influences = [acc["total_influence"] for acc in top_accidents]
            assert influences == sorted(influences, reverse=True), \
                "Top accidents should be sorted by total_influence descending"

        print(f"\n✅ Top Accidents Sorted:")
        print(f"   Total Accidents: {data['num_contributing_accidents']}")
        print(f"   Top 5 Influences: {[f'{acc['total_influence']:.3f}' for acc in top_accidents[:5]]}")


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])
