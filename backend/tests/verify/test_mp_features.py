import json

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db


# Final review A6 (2026-10-14): 3DEP has no value over some coastal, border and Alaska points, and
# the pipeline stores those as NULL (never 0), so 100% coverage is unreachable without fabricating.
# Acceptance is bounded instead: nothing pending, and terminal nodata + no_tile at most 2% of the
# points scorable routes read.
MAX_TERMINAL_SHARE = 0.02
SCORABLE_POINTS = ("SELECT DISTINCT a.point_key FROM canonical_routes r JOIN canonical_areas a ON a.area_id = r.area_id "
                   "WHERE r.scorable AND a.point_key IS NOT NULL")


def test_no_point_is_pending_and_terminal_misses_are_bounded():
    [row] = fetch("SELECT count(*) FILTER (WHERE p.dem_source IS NULL) AS pending, "
                  "count(*) FILTER (WHERE p.dem_source IN ('nodata', 'no_tile')) AS terminal, "
                  "count(*) FILTER (WHERE p.elevation_m IS NULL AND p.dem_source IN ('3dep_13', '3dep_2')) AS inconsistent, "
                  f"count(*) AS n FROM ({SCORABLE_POINTS}) s JOIN feature_points p USING (point_key)")
    print(row)
    assert row["n"] > 0
    assert row["pending"] == 0, "points still awaiting a DEM read (transient failures); rerun the elevation step"
    assert row["inconsistent"] == 0
    assert row["terminal"] <= MAX_TERMINAL_SHARE * row["n"], "nodata + no_tile above 2% of scorable routes' points"


def test_every_point_has_a_timezone():
    [row] = fetch("SELECT count(*) AS n FROM feature_points WHERE tz IS NULL")
    assert row["n"] == 0


def test_epqs_check_passed_on_enough_points():
    [row] = fetch("SELECT validation_report->'problems'->>0 AS result FROM source_ingest_log "
                  "WHERE source = 'static_features:epqs-check' AND status = 'ok' ORDER BY finished_at DESC LIMIT 1")
    assert row["result"] is not None, "no passing epqs-check run logged; run the epqs-check step"
    result = json.loads(row["result"])
    print(result["classes"])
    assert result["compared"] >= 180 and result["gross"] == 0 and result["passed"] == 1


def test_every_scorable_route_point_is_a_stored_area_key():
    [row] = fetch("SELECT count(*) AS n FROM canonical_areas a WHERE a.point_key IS NOT NULL "
                  "AND EXISTS (SELECT 1 FROM canonical_routes r WHERE r.area_id = a.area_id AND r.scorable) "
                  "AND NOT EXISTS (SELECT 1 FROM feature_points p WHERE p.point_key = a.point_key)")
    assert row["n"] == 0


def test_every_scorable_route_has_features_and_elevation_is_bounded():
    [row] = fetch("SELECT count(*) FILTER (WHERE f.route_id IS NULL) AS no_row, "
                  "count(*) FILTER (WHERE f.elevation_m IS NULL) AS no_elevation, "
                  "count(*) FILTER (WHERE f.coord_precision = 'inherited') AS inherited, count(*) AS n "
                  "FROM scorable_routes s LEFT JOIN route_static_features f USING (route_id)")
    print(row)
    assert row["no_row"] == 0 and row["no_elevation"] <= MAX_TERMINAL_SHARE * row["n"]


def test_scoring_unit_features_has_twelve_months_per_scorable_route():
    [row] = fetch("SELECT (SELECT count(*) FROM scorable_routes) AS routes, "
                  "(SELECT count(DISTINCT unit_id) FROM scoring_unit_features) AS units, "
                  "(SELECT count(*) FROM (SELECT unit_id FROM scoring_unit_features GROUP BY unit_id "
                  " HAVING count(*) <> 12) x) AS bad")
    assert row["routes"] == row["units"] and row["bad"] == 0


def test_every_point_bucket_has_a_normals_status_and_complete_means_twelve_real_months():
    [missing] = fetch("SELECT count(*) AS n FROM (SELECT DISTINCT grid_bucket FROM feature_points) b "
                      "LEFT JOIN cell_normals_status s USING (grid_bucket) WHERE s.grid_bucket IS NULL")
    assert missing["n"] == 0
    [bad] = fetch("SELECT count(*) AS n FROM cell_normals_status s WHERE s.status = 'complete' AND "
                  "(SELECT count(*) FROM cell_climate_normals c WHERE c.grid_bucket = s.grid_bucket "
                  " AND c.tmax_mean IS NOT NULL AND c.tmin_mean IS NOT NULL) <> 12")
    assert bad["n"] == 0
    [pending] = fetch("SELECT count(*) FILTER (WHERE s.status = 'pending') AS pending, count(*) AS n "
                      "FROM (SELECT DISTINCT grid_bucket FROM feature_points) b JOIN cell_normals_status s USING (grid_bucket)")
    print(pending, "pending buckets read as missing normals until the next Open-Meteo Professional window")


def test_identity_links_exist_and_every_llm_link_was_offered():
    [row] = fetch("SELECT count(*) FILTER (WHERE method = 'mp_route_id') AS identity FROM accident_route_links")
    assert row["identity"] > 0
    [bad] = fetch("SELECT count(*) AS n FROM accident_legacy_links l LEFT JOIN internal.r10_llm_calls c "
                  "ON c.call_id = l.llm_call_id WHERE l.method = 'llm' AND (c.call_id IS NULL "
                  "OR c.pick_id IS DISTINCT FROM coalesce(l.route_id, l.area_id) OR NOT (c.pick_id = ANY(c.candidate_ids)))")
    assert bad["n"] == 0, "an llm link whose picked id was not among the offered candidates"
