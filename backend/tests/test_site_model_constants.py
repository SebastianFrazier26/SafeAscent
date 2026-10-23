"""The project page's illustrative scorer (site/index.html) must use the backend's real constants."""
import json
import re
from pathlib import Path

from app.services import algorithm_config as cfg
from app.services.risk_bands import MIN_ESTIMABLE_SCORE, RISK_BAND_THRESHOLDS

SITE_PAGE = Path(__file__).resolve().parents[2] / "site" / "index.html"


def _site_constants() -> dict:
    match = re.search(
        r'<script type="application/json" id="model-constants">(.*?)</script>',
        SITE_PAGE.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert match, "model-constants block missing from site/index.html"
    return json.loads(match.group(1))


def test_site_constants_match_backend():
    site = _site_constants()
    types = site["spatial_bandwidth_km"].keys()
    assert set(types) == set(site["temporal_lambda"]) == set(site["route_type_weights"])

    for t in types:
        assert site["spatial_bandwidth_km"][t] == cfg.SPATIAL_BANDWIDTH[t]
        assert site["temporal_lambda"][t] == cfg.TEMPORAL_LAMBDA[t]
        for a in types:
            assert site["route_type_weights"][t][a] == cfg.ROUTE_TYPE_WEIGHTS[(t, a)], (t, a)

    assert site["temporal_decay_impact"] == cfg.TEMPORAL_DECAY_IMPACT
    assert site["temporal_decay_shape"] == cfg.TEMPORAL_DECAY_SHAPE
    assert site["temporal_seasonal_impact"] == cfg.TEMPORAL_SEASONAL_IMPACT
    assert site["seasonal_boost"] == cfg.SEASONAL_BOOST
    for severity, boost in site["severity_boosters"].items():
        assert boost == cfg.SEVERITY_BOOSTERS[severity]
    assert site["risk_normalization_factor"] == cfg.RISK_NORMALIZATION_FACTOR
    assert site["max_risk_score"] == cfg.MAX_RISK_SCORE
    assert tuple(site["risk_band_thresholds"]) == RISK_BAND_THRESHOLDS
    assert site["min_estimable_score"] == MIN_ESTIMABLE_SCORE


def test_site_weather_power_matches_scorer():
    """WEATHER_POWER and the exclusion threshold are locals in the scorer, not config."""
    site = _site_constants()
    source = (Path(cfg.__file__).parent / "safety_algorithm.py").read_text(encoding="utf-8")
    assert re.search(rf"^\s*WEATHER_POWER = {site['weather_power']}\b", source, re.MULTILINE)
    assert re.search(
        rf"^\s*WEATHER_EXCLUSION_THRESHOLD = {site['weather_exclusion_threshold']}\b", source, re.MULTILINE
    )
