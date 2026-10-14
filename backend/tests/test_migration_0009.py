import asyncpg
import pytest
from alembic import command

from tests.pgtest import fetch, migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
PIN = "0009_static_features"
KEY = "40.20000:-105.30000"
SEED = f"""
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), (900000110, 'Fixture Crag', 900000100, 40.2, -105.3);
INSERT INTO mp_routes (mp_route_id, name, location_id, type, grade) VALUES
  (900000001, 'Fixture One', 900000110, 'Trad', '5.9'), (900000002, 'Fixture Two', 900000110, 'unknown', '5.8');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  (900000100, NULL, '900000100', 1, 'us', 'CO', NULL, NULL, 'none', NULL, NULL, 'v'),
  (900000110, 900000100, '900000100.900000110', 2, 'us', 'CO', 40.2, -105.3, 'own', 900000110, '{KEY}', 'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000001, 900000110, 'trad', false, '{{trad}}', 'rt-mp-v1', true, NULL, 'h', 'v'),
  (900000002, 900000110, 'unknown', false, '{{}}', 'rt-mp-v1', false, 'unknown_type', 'h', 'v');
INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, elevation_m, dem_source, dem_res_m,
  feature_version) VALUES ('{KEY}', 40.2, -105.3, 4024952, 'a', 'b', 'America/Denver', 2400, '3dep_13', 10, 'f-v1');
INSERT INTO route_static_features (route_id, source, point_key, lat, elevation_m, grid_bucket, coord_precision,
  feature_version) VALUES
  (900000001, 'mp', '{KEY}', 40.2, 2400, 4024952, 'own', 'f-v1'),
  (900000002, 'mp', '{KEY}', 40.2, 2400, 4024952, 'own', 'f-v1');
"""


def test_0009_checks_clean_and_rejects_bad_values():
    with migrated_db(PIN) as name:
        command.check(_alembic_cfg(name))
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, dem_source, "
                          "feature_version) VALUES ('k', 40, -105, 1, 'a', 'b', 'guessed', 'f-v1')")


def test_scoring_unit_features_lists_scorable_routes_with_pending_normals_null():
    with migrated_db(PIN, seed_sql=SEED) as name:
        rows = fetch(name, "SELECT unit_id, month, tz, elevation_m, tmax_mean, normals_status "
                           "FROM scoring_unit_features ORDER BY unit_id, month")
    assert [r[1] for r in rows] == list(range(1, 13))
    assert {(r[0], r[2], r[3], r[4], r[5]) for r in rows} == {
        (900000001, "America/Denver", 2400.0, None, "pending")}


def test_complete_normals_are_read_and_pending_ones_are_not():
    with migrated_db(PIN, seed_sql=SEED) as name:
        run_sql(name, "INSERT INTO cell_normals_status (grid_bucket, status) VALUES (4024952, 'pending'); "
                      "INSERT INTO cell_climate_normals (grid_bucket, month, tmax_mean, tmin_mean, n_years, "
                      "period_start_year, period_end_year, source, normals_version) SELECT 4024952, m, 10, 0, 10, "
                      "2016, 2025, 'derived_cell_daily_conditions', 'n-v2' FROM generate_series(1, 12) m")
        pending = fetch(name, "SELECT DISTINCT tmax_mean FROM scoring_unit_features")
        run_sql(name, "UPDATE cell_normals_status SET status = 'complete' WHERE grid_bucket = 4024952")
        complete = fetch(name, "SELECT DISTINCT tmax_mean, normals_status FROM scoring_unit_features")
    assert pending == [(None,)]
    assert complete == [(10.0, "complete")]


def test_0009_downgrade_refuses_after_elevation_was_sampled():
    with migrated_db(PIN, seed_sql=SEED) as name:
        with pytest.raises(RuntimeError, match="refusing to downgrade 0009"):
            command.downgrade(_alembic_cfg(name), "0008_mp_catalog")


def test_route_static_features_type_group_matches_the_catalog():
    with migrated_db(PIN, seed_sql=SEED) as name:
        run_sql(name, "UPDATE route_static_features SET type_group = 'rock' WHERE route_id = 900000001")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "UPDATE route_static_features SET type_group = 'boulder' WHERE route_id = 900000001")


def test_0009_downgrade_refuses_with_a_pending_point_and_a_route_row():
    pending = SEED.replace("'3dep_13'", "NULL")
    with migrated_db(PIN, seed_sql=pending) as name:
        assert fetch(name, "SELECT count(*) FROM feature_points WHERE dem_source IS NOT NULL") == [(0,)]
        run_sql(name, "DELETE FROM route_static_features WHERE route_id = 900000002")
        with pytest.raises(RuntimeError, match="refusing to downgrade 0009: feature_points has 1 rows"):
            command.downgrade(_alembic_cfg(name), "0008_mp_catalog")
        run_sql(name, "DELETE FROM route_static_features; DELETE FROM feature_points")
        command.downgrade(_alembic_cfg(name), "0008_mp_catalog")


def test_0009_downgrade_refuses_with_only_a_route_row():
    with migrated_db(PIN) as name:
        run_sql(name, SEED.split("INSERT INTO feature_points")[0]
                + "INSERT INTO route_static_features (route_id, source, coord_precision, feature_version) "
                  "VALUES (900000001, 'mp', 'none', 'f-v1')")
        with pytest.raises(RuntimeError, match="refusing to downgrade 0009: route_static_features has 1 rows"):
            command.downgrade(_alembic_cfg(name), "0008_mp_catalog")
