"""Acceptance cells for plan 3 (conditions refresh), as analyst: VERIFY_DATABASE_URL=<analyst
url> uv run pytest -m db tests/verify."""

import pytest

from app.pipelines.localday import INCIDENT_START
from app.pipelines.r6_verify import R6_SQL
from tests.verify._db import fetch

pytestmark = pytest.mark.db

# Owner decision DP1 (2026-10-01): incident ERA5 starts at localday.INCIDENT_START (1990), not
# the archive's 1940 start. Incident series are filled from INCIDENT_START - 6 days, so the
# first full 7-day window ends on INCIDENT_START itself; incidents before it get no incident
# series at all (localday's INCIDENT_FROM_WHERE_SQL) and must read as missing, never a value.
FIRST_WINDOW_END = f"DATE '{INCIDENT_START.isoformat()}'"
ELIGIBLE = (
    "FROM accidents_clean_daily a JOIN accidents t ON t.accident_id = a.accident_id "
    "WHERE a.point_trusted AND a.latitude IS NOT NULL AND a.date <= (now() AT TIME ZONE 'UTC')::date - 5"
)


def test_every_clean_day_incident_has_a_seven_day_window():
    [row] = fetch(
        f"SELECT count(*) FILTER (WHERE a.date >= {FIRST_WINDOW_END} AND t.tz IS NOT NULL AND "
        "(SELECT count(*) FROM accident_conditions c WHERE c.accident_id = a.accident_id AND c.record_kind = 'era5') < 7) AS missing, "
        f"count(*) FILTER (WHERE a.date < {FIRST_WINDOW_END}) AS conditions_missing_pre_era5, "
        f"count(*) FILTER (WHERE t.tz IS NULL) AS no_timezone {ELIGIBLE}"
    )
    print(dict(row))
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
