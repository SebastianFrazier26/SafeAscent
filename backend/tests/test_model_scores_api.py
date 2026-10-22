"""P3 Task 22: the API serves the active version's stored rows (model.route_scores), additively.
Missing is never 0, gray is only "no evidence", and every number carries its range and confidence."""

import math
import re
from pathlib import Path

import pytest

from app.utils.cache import build_stored_score_key
from tests.pgtest import run_sql

SAFETY = "/api/v1/mp-routes/{}/safety"
# exp(-const) for the conftest scores_bundle: const = -(intercept + a_trad [+ m_7]).
R_REF_TRAD = {7: math.exp(math.log(0.05) + 0.2 + 0.1), 1: math.exp(math.log(0.05) + 0.2)}
JULY = {"target_date": "2026-07-04"}


def test_safety_reads_the_stored_row_with_range_and_confidence(test_client, seeded_scores):
    r = test_client.post(SAFETY.format(900000001), params={**JULY, "bypass_cache": True})
    body = r.json()
    assert r.status_code == 200 and body["risk_score"] == 40.0 and body["color_code"] == "yellow"
    assert body["confidence"] == "high" and body["score_range"] == [20.0, 60.0]
    assert body["model_version"] == seeded_scores.version
    assert body["evidence"] == {"geo": 3.5, "similar": 6.0}
    # DP13: RR x r_ref(trad, July); the fixture bundle stores const = -log r_ref.
    r_ref = R_REF_TRAD[7]
    assert body["relative_rate"] == {k: pytest.approx(v * r_ref, rel=1e-3)
                                     for k, v in {"mean": 1.4, "lo": 0.9, "hi": 2.2}.items()}
    assert body["relative_rate"]["mean"] != 1.4
    assert body["per_10k_logged_ascents"] == {"mean": 2.5, "lo": 1.0, "hi": 4.0}
    # The legacy fields are still there, unchanged in name and meaning.
    assert (body["route_id"], body["route_name"], body["target_date"], body["data_status"]) == (
        900000001, "Fixture Route", "2026-07-04", "ok")


def test_low_confidence_keeps_number_and_range(test_client, seeded_scores):
    body = test_client.post(SAFETY.format(900000002), params=JULY).json()
    assert body["data_status"] == "ok" and body["confidence"] == "low" and body["risk_score"] is not None
    assert body["risk_score"] == 62.3 and body["color_code"] == "orange"
    assert body["score_range"] == [10.0, 90.0]
    assert body["per_10k_logged_ascents"] is None and body["per_10k_reason"] == "ticks_missing"


def test_insufficient_is_gray_with_reason_and_no_number(test_client, seeded_scores):
    body = test_client.post(SAFETY.format(900000003), params=JULY).json()
    assert (body["risk_score"], body["color_code"], body["data_status"], body["insufficient_reason"]) == \
        (None, "gray", "insufficient_data", "no_evidence")
    assert body["confidence"] is None and body["score_range"] is None and body["evidence"] is None
    assert body["model_version"] == seeded_scores.version


def test_missing_row_is_unavailable_not_insufficient(test_client, seeded_scores):
    r = test_client.post(SAFETY.format(900000099), params=JULY)
    assert r.status_code == 404 and r.json() == {"detail": "score unavailable"}
    # A route that exists but has no row for the month is unavailable too, never gray or 0.
    r = test_client.post(SAFETY.format(900000002), params={"target_date": "2026-01-04"})
    assert r.status_code == 404 and r.json() == {"detail": "score unavailable"}


def test_no_active_version_is_unavailable(test_client, seeded_scores_no_active):
    r = test_client.post(SAFETY.format(900000001), params=JULY)
    assert r.status_code == 404 and r.json() == {"detail": "score unavailable"}


def test_map_and_popup_agree(test_client, seeded_scores):
    resp = test_client.get("/api/v1/mp-routes/map-with-safety", params=JULY).json()
    bulk = {r["mp_route_id"]: r["safety"] for r in resp["routes"]}
    single = test_client.post(SAFETY.format(900000002), params=JULY).json()
    assert bulk[900000002]["risk_score"] == single["risk_score"]
    assert bulk[900000002]["confidence"] == single["confidence"]
    assert bulk[900000002]["score_range"] == single["score_range"]
    assert bulk[900000002]["relative_rate"] == single["relative_rate"]
    assert bulk[900000002]["relative_rate"]["mean"] == pytest.approx(2.0 * R_REF_TRAD[7], rel=1e-3)
    assert bulk[900000003]["data_status"] == "insufficient_data" and bulk[900000003]["risk_score"] is None
    assert resp["meta"]["computed_routes"] == 0 and resp["meta"]["model_version"] == seeded_scores.version
    assert resp["meta"]["insufficient_routes"] == 1


def test_map_route_without_a_row_has_null_safety(test_client, seeded_scores):
    # January: only route 1 has a row, so routes 2 and 3 are missing (null), never 0 or gray.
    resp = test_client.get("/api/v1/mp-routes/map-with-safety", params={"target_date": "2026-01-10"}).json()
    bulk = {r["mp_route_id"]: r["safety"] for r in resp["routes"]}
    assert bulk[900000001]["risk_score"] == 12.0 and bulk[900000001]["confidence"] == "medium"
    assert bulk[900000002] is None and bulk[900000003] is None
    assert resp["meta"]["missing_routes"] == 2


def test_map_without_active_version_has_no_scores(test_client, seeded_scores_no_active):
    resp = test_client.get("/api/v1/mp-routes/map-with-safety", params=JULY).json()
    assert all(r["safety"] is None for r in resp["routes"])
    assert resp["meta"]["model_version"] is None


def test_redis_key_carries_the_model_version(test_client, seeded_scores, scores_redis):
    test_client.post(SAFETY.format(900000001), params=JULY)
    key = build_stored_score_key(seeded_scores.version, 900000001, 7)
    assert key == f"safety:{seeded_scores.version}:900000001:7"
    assert scores_redis.get(key) is not None
    assert all(k.startswith(f"safety:{seeded_scores.version}:") for k in scores_redis.keys("safety:*"))


def test_a_promotion_misses_the_old_versions_cache(test_client, seeded_scores, scores_redis):
    test_client.post(SAFETY.format(900000001), params=JULY)
    run_sql(seeded_scores.db, "UPDATE model.registry SET status = 'previous' WHERE status = 'active'")
    r = test_client.post(SAFETY.format(900000001), params=JULY)
    assert r.status_code == 404  # the cached entry of the demoted version is never served


def test_risk_breakdown_explains_the_stored_row(test_client, seeded_scores):
    body = test_client.get("/api/v1/mp-routes/900000001/risk-breakdown", params=JULY).json()
    assert body["risk_score"] == 40.0 and body["model_version"] == seeded_scores.version
    assert body["evidence"] == {"geo": 3.5, "similar": 6.0}
    names = [t["name"] for t in body["terms"]]
    assert names[-1] == "evidence" and len(names) <= 5
    assert sum(t["log_effect"] for t in body["terms"]) == pytest.approx(math.log(1.4), abs=1e-3)
    # Kernel-era keys stay, empty, for older clients (F54).
    assert body["factors"] is None and body["top_accidents"] == [] and body["extreme_weather"] is None
    assert "num_contributing_accidents" in body


def test_risk_breakdown_insufficient_has_no_terms_and_missing_is_404(test_client, seeded_scores):
    body = test_client.get("/api/v1/mp-routes/900000003/risk-breakdown", params=JULY).json()
    assert body["risk_score"] is None and body["color_code"] == "gray" and body["terms"] == []
    assert {"factors", "top_accidents", "extreme_weather", "num_contributing_accidents"} <= set(body)
    r = test_client.get("/api/v1/mp-routes/900000099/risk-breakdown", params=JULY)
    assert r.status_code == 404 and r.json() == {"detail": "score unavailable"}


def test_historical_trends_is_the_twelve_month_profile(test_client, seeded_scores, monkeypatch):
    from app.api.v1 import mp_routes

    async def no_weather(*_a, **_k):
        return {"available": False}

    monkeypatch.setattr(mp_routes, "_weather_volatility", no_weather)
    body = test_client.get("/api/v1/mp-routes/900000001/historical-trends",
                           params={"target_date": "2026-07-04"}).json()
    profile = {m["month"]: m for m in body["monthly_profile"]}
    assert len(profile) == 12 and body["model_version"] == seeded_scores.version
    assert (profile[7]["risk_score"], profile[7]["score_range"], profile[7]["confidence"]) == (40.0, [20.0, 60.0], "high")
    assert profile[1]["risk_score"] == 12.0
    assert profile[1]["relative_rate"] == {k: pytest.approx(v * R_REF_TRAD[1], rel=1e-3)
                                           for k, v in {"mean": 0.6, "lo": 0.3, "hi": 1.1}.items()}
    # A month with no row is unavailable: no number, no colour, never 0 or gray.
    assert (profile[3]["risk_score"], profile[3]["color_code"], profile[3]["data_status"]) == (None, None, None)
    assert body["historical_predictions"] == [] and body["reference_date"] == "2026-07-04"


def test_forecast_days_carry_their_months_stored_score(test_client, seeded_scores, monkeypatch):
    monkeypatch.setattr("app.services.elevation_service.fetch_elevation", lambda lat, lon: None)
    monkeypatch.setattr("app.services.weather_service.fetch_current_weather_pattern", lambda **_: None)
    body = test_client.get("/api/v1/mp-routes/900000001/forecast", params={"start_date": "2026-07-29"}).json()
    by_date = {d["date"]: d for d in body["forecast_days"]}
    assert (by_date["2026-07-31"]["risk_score"], by_date["2026-07-31"]["confidence"]) == (40.0, "high")
    # August has no row: unavailable, never 0.
    assert (by_date["2026-08-01"]["risk_score"], by_date["2026-08-01"]["data_status"]) == (None, None)
    assert body["model_version"] == seeded_scores.version


ACCIDENTS = """
INSERT INTO accidents (accident_id, source, date, year, date_precision, year_lo, year_hi, state, is_canonical,
  activity_class, country, excluded_reason, geocode_precision, latitude, longitude) VALUES
  (900000901, 'AAC', '2010-07-14', 2010, 'day', NULL, NULL, 'Colorado', true, 'climbing', 'US', NULL, 'area', 40.21, -105.31),
  (900000902, 'AAC', '2004-07-01', 2004, 'year', 2003, 2005, 'Colorado', true, 'climbing', 'US', NULL, 'area', 40.21, -105.31),
  (900000903, 'AAC', '2011-07-01', 2011, 'year', 2011, 2011, 'Colorado', true, 'climbing', 'US', NULL, 'area', 40.21, -105.31);
"""


def test_seasonal_patterns_has_no_risk_and_reads_years_as_intervals(test_client, seeded_scores):
    run_sql(seeded_scores.db, ACCIDENTS)
    body = test_client.get("/api/v1/mp-routes/900000001/seasonal-patterns").json()
    by_month = {m["month_num"]: m for m in body["monthly_patterns"]}
    # Only the day-precision July row counts in July; the 2003-2005 and 2011 year rows have no month.
    assert by_month[7]["accident_count"] == 1
    assert sum(m["accident_count"] for m in body["monthly_patterns"]) == 1
    assert body["year_known_only"] == 2
    assert "avg_risk_score" not in str(body) and "avg_risk" not in str(body)


def test_predict_is_gone(test_client):
    r = test_client.post("/api/v1/predict", json={"latitude": 40.0, "longitude": -105.0,
                                                  "route_type": "trad", "planned_date": "2026-07-04"})
    assert r.status_code == 410
    assert r.json() == {"detail": "Arbitrary-point prediction was retired with the kernel model; "
                                  "select a route on the map."}


def test_time_of_day_has_no_hourly_number(test_client, seeded_scores, monkeypatch):
    class _Weather:
        def raise_for_status(self):
            pass

        def json(self):
            return {"hourly": {"time": ["2026-07-04T09:00", "2026-07-04T10:00"], "temperature_2m": [10.0, 11.0],
                               "precipitation": [0.0, 0.0], "wind_speed_10m": [1.0, 1.0],
                               "wind_gusts_10m": [2.0, 2.0], "cloud_cover": [5.0, 5.0],
                               "visibility": [20000.0, 20000.0]}}

    monkeypatch.setattr("requests.get", lambda *a, **k: _Weather())
    body = test_client.get("/api/v1/mp-routes/900000001/time-of-day", params=JULY).json()
    # Removed kernel-era keys stay present (null) so the response is additive (F54).
    assert "data_status" in body and body["data_status"] is None
    for hour in body["hourly_data"]:
        assert hour["risk"] is None and hour["risk_score"] is None and hour["is_climbable"] is None
        assert "color_code" in hour and hour["color_code"] is None
        assert "data_status" in hour and hour["data_status"] is None
        assert hour["risk_note"] == "Hour-by-hour risk arrives with the conditions rules (v2.1)."
        assert hour["temperature"] is not None
    assert body["climbing_windows"] == [] and body["best_window"] is None and body["base_daily_risk"] is None


MP_ROUTES = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "mp_routes.py"


def test_mp_routes_serves_no_kernel_seasonal_risk():
    """test_kernel_not_served-style guard (pre-flight F55): no avg_risk_score anywhere, and every
    month read off an accident date sits in a statement restricted to day/month precision."""
    source = MP_ROUTES.read_text()
    assert "avg_risk_score" not in source
    for m in re.finditer(r"EXTRACT\(MONTH FROM a\.date\)", source):
        statement = source[source.rfind('text("""', 0, m.start()):source.find('""")', m.end())]
        assert "a.date_precision IN ('day', 'month')" in statement
    assert "predict_route_safety" not in source


def test_relative_rate_is_rr_times_the_reference_rate():
    from app.services.model_scores import StoredScore, score_fields

    row = StoredScore(route_id=900000001, month=7, model_version="v", type_group="trad", insufficient=False,
                      insufficient_reason=None, rr_mean=1.4, rr_q05=0.9, rr_q95=2.2, percentile=40.0,
                      pct_q05=20.0, pct_q95=60.0, conf_level="high", n_eff_geo=1.0, n_eff_feature=1.0,
                      per10k_mean=None, per10k_q05=None, per10k_q95=None, per10k_reason="ticks_missing")
    rr = score_fields(row, 2.5e-4)["relative_rate"]
    # Significant digits, so a small national rate never rounds to 0.
    assert rr == {"mean": pytest.approx(3.5e-4), "lo": pytest.approx(2.25e-4), "hi": pytest.approx(5.5e-4)}
    assert score_fields(row, None)["relative_rate"] is None
