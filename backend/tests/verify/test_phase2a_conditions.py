"""Acceptance cells for plan 3 (conditions refresh), as analyst: VERIFY_DATABASE_URL=<analyst
url> uv run pytest -m db tests/verify."""

import pytest

from app.pipelines.localday import INCIDENT_FROM_WHERE_SQL, INCIDENT_START
from app.pipelines.r6_verify import R6_SQL
from tests.verify._db import fetch

pytestmark = pytest.mark.db

# Owner decision DP1 (2026-10-01): incident ERA5 starts at localday.INCIDENT_START (1990), not
# the archive's 1940 start. Eligibility is localday's own INCIDENT_FROM_WHERE_SQL (trusted point,
# both coordinates, a zone, a known day, on or after INCIDENT_START), so this cell can never
# disagree with registration or the accident_conditions view. ERA5 is bought only inside the
# yearly window, so an incident counts only up to its series' last_era5_date; an incident whose
# series was never filled stays counted (COALESCE), so it fails as missing rather than vanishing.
SERIES_LAST = (
    "(SELECT s.last_era5_date FROM grid_bucket_series s "
    "WHERE s.grid_bucket = grid_bucket_key(a.latitude, a.longitude) AND s.tz = t.tz)"
)
ELIGIBLE = f"{INCIDENT_FROM_WHERE_SQL} AND a.date <= COALESCE({SERIES_LAST}, a.date)"
TRUSTED = (
    "FROM accidents_clean_daily a JOIN accidents t ON t.accident_id = a.accident_id "
    "WHERE a.point_trusted AND a.latitude IS NOT NULL AND a.longitude IS NOT NULL"
)


def test_every_clean_day_incident_has_a_seven_day_window():
    # An all-NULL era5 day is not a day of conditions: tmax must be observed.
    [row] = fetch(
        "SELECT count(*) FILTER (WHERE (SELECT count(*) FROM accident_conditions c WHERE c.accident_id = a.accident_id "
        "AND c.record_kind = 'era5' AND c.tmax IS NOT NULL) < 7) AS missing, count(*) AS eligible "
        f"{ELIGIBLE}"
    )
    [info] = fetch(
        f"SELECT count(*) FILTER (WHERE a.date < DATE '{INCIDENT_START.isoformat()}') AS conditions_missing_pre_era5, "
        f"count(*) FILTER (WHERE t.tz IS NULL) AS no_timezone {TRUSTED}"
    )
    print(dict(row), dict(info))
    assert row["missing"] == 0


def test_r6_new_temperatures_agree_with_correctly_linked_legacy_rows():
    [row] = fetch(R6_SQL)
    print(dict(row))
    assert row["n"] > 100
    # Missing is never 0: a NULL correlation/percentile (too few or no era5 rows) must fail
    # loudly here rather than compare false against the thresholds below.
    assert row["r_tmax"] is not None
    assert row["p99_gust"] is not None
    assert row["r_tmax"] > 0.95
    assert row["p99_gust"] < 60


def test_r7_no_positive_longitudes():
    [row] = fetch("SELECT count(*) AS n FROM area_weekly_weather WHERE longitude > 0")
    assert row["n"] == 0


def test_no_visibility_in_new_conditions():
    rows = fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'cell_daily_conditions' AND column_name LIKE '%visib%'")
    assert rows == []


def test_r11_refresh_window_has_incidents():
    [row] = fetch(
        "SELECT count(*) AS n FROM accidents_clean WHERE date BETWEEN DATE '2026-02-05' AND DATE '2026-07-02'"
    )
    print({"feb5_jul2_2026_clean_incidents": row["n"]})
    assert row["n"] > 0
    [latest] = fetch("SELECT max(date) AS d FROM accidents WHERE date <= (now() AT TIME ZONE 'UTC')::date")
    assert latest["d"] is not None and latest["d"].year >= 2025


def test_r11_backtest_inputs_exist():
    [row] = fetch(
        "SELECT count(*) AS incidents, count(*) FILTER (WHERE EXISTS (SELECT 1 FROM historical_predictions h "
        "WHERE h.route_id = a.mp_route_id AND h.prediction_date = a.date)) AS with_prediction "
        "FROM accidents_clean_daily a WHERE a.date BETWEEN DATE '2026-02-05' AND DATE '2026-07-02' "
        "AND a.mp_route_id IS NOT NULL"
    )
    print({"backtest_incidents": row["incidents"], "backtest_with_prediction": row["with_prediction"]})
    assert row["incidents"] > 0 and row["with_prediction"] > 0


def test_normals_status_matches_the_normals_rows():
    [row] = fetch(
        "SELECT count(*) FILTER (WHERE st.status <> 'pending' AND (SELECT count(*) FROM cell_climate_normals n "
        "WHERE n.grid_bucket = st.grid_bucket) <> 12) AS broken, "
        "count(*) FILTER (WHERE st.status = 'complete' AND EXISTS (SELECT 1 FROM cell_climate_normals n "
        "WHERE n.grid_bucket = st.grid_bucket AND (n.tmax_mean IS NULL OR n.precip_mm IS NULL))) AS complete_with_nulls, "
        "count(*) FILTER (WHERE st.status = 'pending') AS pending "
        "FROM cell_normals_status st"
    )
    print(dict(row))
    assert (row["broken"], row["complete_with_nulls"]) == (0, 0)
