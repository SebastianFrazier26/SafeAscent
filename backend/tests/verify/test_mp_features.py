import json

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db


def test_every_point_has_an_elevation_and_a_consistent_source():
    [row] = fetch("SELECT count(*) FILTER (WHERE dem_source IS NULL) AS pending, "
                  "count(*) FILTER (WHERE elevation_m IS NULL) AS missing, "
                  "count(*) FILTER (WHERE elevation_m IS NULL AND dem_source IN ('3dep_13', '3dep_2')) AS inconsistent, "
                  "count(*) AS n FROM feature_points")
    print(row)
    assert row["n"] > 0
    assert row["pending"] == 0, "points still awaiting a DEM read (transient failures); rerun the elevation step"
    assert row["inconsistent"] == 0
    assert row["missing"] == 0, "100% elevation coverage; inspect dem_source for the misses"


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


def test_every_route_bearing_point_is_a_stored_area_key():
    [row] = fetch("SELECT count(*) AS n FROM canonical_areas a WHERE a.point_key IS NOT NULL "
                  "AND EXISTS (SELECT 1 FROM canonical_routes r WHERE r.area_id = a.area_id AND NOT r.is_boulder) "
                  "AND NOT EXISTS (SELECT 1 FROM feature_points p WHERE p.point_key = a.point_key)")
    assert row["n"] == 0


def test_every_scorable_route_has_features_with_elevation():
    [row] = fetch("SELECT count(*) FILTER (WHERE f.route_id IS NULL) AS no_row, "
                  "count(*) FILTER (WHERE f.elevation_m IS NULL) AS no_elevation, "
                  "count(*) FILTER (WHERE f.coord_precision = 'inherited') AS inherited, count(*) AS n "
                  "FROM scorable_routes s LEFT JOIN route_static_features f USING (route_id)")
    print(row)
    assert row["no_row"] == 0 and row["no_elevation"] == 0


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
