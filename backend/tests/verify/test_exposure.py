"""Read-only exposure cells (exposure plan X-2), run with VERIFY against a branch or prod."""

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db
LATEST = "(SELECT max(as_of) FROM internal.exposure_index WHERE data_cutoff = as_of)"


def test_latest_production_snapshot_is_consistent():
    [row] = fetch(
        "SELECT count(*) AS n, "
        "count(*) FILTER (WHERE (exposure_point IS NULL) <> (exposure_lo IS NULL OR exposure_hi IS NULL)) AS bounds_bad, "
        "count(*) FILTER (WHERE (proxy->'season_share'->>'missing')::boolean AND exposure_point IS NOT NULL) AS floored, "
        "count(*) FILTER (WHERE (proxy->'mp_ticks'->>'missing')::boolean AND proxy->'mp_ticks'->>'value' IS NOT NULL) "
        "  AS tick_value_on_missing, "
        "count(*) FILTER (WHERE NOT (proxy->'mp_ticks'->>'missing')::boolean "
        "  AND (proxy->'mp_ticks'->>'n_complete')::float < 1) AS present_without_complete, "
        "count(*) FILTER (WHERE tick_coverage IS DISTINCT FROM (proxy->'mp_ticks'->>'coverage')::real) AS coverage_drift "
        f"FROM internal.exposure_index WHERE data_cutoff = as_of AND as_of = {LATEST}")
    assert row["n"] is not None
    assert row["n"] > 0
    for k in ("bounds_bad", "floored", "tick_value_on_missing", "present_without_complete", "coverage_drift"):
        assert row[k] is not None, k
        assert row[k] == 0, k


def test_cells_match_the_scorable_catalog():
    [row] = fetch(
        "SELECT (SELECT count(*) * 12 FROM (SELECT DISTINCT area_id, type_group FROM scorable_routes) s) AS expected, "
        f"(SELECT count(*) FROM internal.exposure_index WHERE data_cutoff = as_of AND as_of = {LATEST}) AS got")
    assert row["expected"] is not None
    assert row["got"] is not None
    assert row["expected"] == row["got"]


def test_app_cannot_read_the_exposure_index():
    [row] = fetch("SELECT has_table_privilege('app', 'internal.exposure_index', 'SELECT') AS can")
    assert row["can"] is not None
    assert row["can"] is False


def test_coverage_by_type_group_is_reported():
    rows = fetch(
        "SELECT type_group, count(*) AS cells, avg(tick_coverage) AS mean_cov, "
        "count(*) FILTER (WHERE tick_coverage = 1) AS full, count(*) FILTER (WHERE exposure_point IS NULL) AS null_points "
        f"FROM internal.exposure_index WHERE data_cutoff = as_of AND as_of = {LATEST} AND month = 1 GROUP BY 1 ORDER BY 1")
    assert rows
    pairs = fetch(
        "SELECT type_group, count(*) AS n FROM (SELECT DISTINCT area_id, type_group FROM scorable_routes) s GROUP BY 1")
    expected = {p["type_group"]: p["n"] for p in pairs}
    for r in rows:
        assert r["cells"] == expected[r["type_group"]]
        if r["mean_cov"] is not None:
            assert 0.0 <= r["mean_cov"] <= 1.0
        print(r["type_group"], r["cells"], r["mean_cov"], r["full"], r["null_points"])
