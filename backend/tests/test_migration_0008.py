import asyncpg
import pytest
from alembic import command

from tests.pgtest import fetch, migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
PIN = "0008_mp_catalog"
MP = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), (900000101, 'Fixture Crag', 900000100, 40.1, -105.6);
INSERT INTO mp_routes (mp_route_id, name, location_id, type, grade) VALUES
  (900000001, 'Fixture Route', 900000101, 'Trad', '5.9'), (900000002, 'Fixture Boulder', 900000101, 'Boulder', 'V3');
"""
AREAS = """
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  (900000100, NULL, '900000100', 1, 'us', 'CO', NULL, NULL, 'none', NULL, NULL, 'cat-mp-v1'),
  (900000101, 900000100, '900000100.900000101', 2, 'us', 'CO', 40.1, -105.6, 'own', 900000101, '40.10000:-105.60000',
   'cat-mp-v1');
"""
ROUTES = ("INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, "
          "scorable, unscorable_reason, facts_sha256, catalog_version) VALUES ")


def test_0008_checks_clean_and_enforces_the_catalog_rules():
    with migrated_db(PIN, seed_sql=MP + AREAS) as name:
        command.check(_alembic_cfg(name))
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, ROUTES + "(900000001, 900000101, 'trad', false, '{trad}', 'rt-mp-v1', true, 'unknown_type', 'h', 'v')")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, ROUTES + "(900000002, 900000101, 'trad', true, '{bouldering}', 'rt-mp-v1', false, 'boulder', 'h', 'v')")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "UPDATE canonical_areas SET point_key = NULL WHERE area_id = 900000101")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            run_sql(name, ROUTES + "(900000999, NULL, 'trad', false, '{trad}', 'rt-mp-v1', true, NULL, 'h', 'v')")


def test_scorable_routes_lists_its_columns_and_hides_unscorable():
    with migrated_db(PIN, seed_sql=MP + AREAS) as name:
        run_sql(name, ROUTES + "(900000001, 900000101, 'trad', false, '{trad}', 'rt-mp-v1', true, NULL, 'h', 'v'), "
                               "(900000002, 900000101, NULL, true, '{bouldering}', 'rt-mp-v1', false, 'boulder', 'h', 'v')")
        cols = [r[0] for r in fetch(name, "SELECT column_name FROM information_schema.columns "
                                          "WHERE table_name = 'scorable_routes' ORDER BY ordinal_position")]
        rows = fetch(name, "SELECT route_id, area_path::text AS p, state_code FROM scorable_routes")
    assert cols == ["route_id", "area_id", "type_group", "type_rule_version", "area_path", "state_code", "point_key",
                    "coord_precision", "updated_at"]
    assert rows == [(900000001, "900000100.900000101", "CO")]


MP3 = "INSERT INTO mp_routes (mp_route_id, name, location_id, type, grade) VALUES (900000003, 'Fixture Three', 900000101, 'Sport', '5.11a');"
ROUTES3 = (ROUTES + "(900000001, 900000101, 'trad', false, '{trad}', 'rt-mp-v1', true, NULL, 'h', 'v'), "
                    "(900000002, 900000101, NULL, true, '{bouldering}', 'rt-mp-v1', false, 'boulder', 'h', 'v'), "
                    "(900000003, 900000101, 'sport', false, '{sport}', 'rt-mp-v1', true, NULL, 'h', 'v')")


def test_tick_coverage_has_three_states_and_never_zero():
    with migrated_db(PIN, seed_sql=MP + MP3 + AREAS) as name:
        run_sql(name, ROUTES3)
        run_sql(name, "INSERT INTO internal.route_tick_scrapes (mp_route_id, source, scraped_at, complete, complete_through, "
                      "total_ticks) VALUES (900000001, 'mp_scrape', '2024-03-15T00:00:00Z', true, '2024-02-29', 42), "
                      "(900000003, 'mp_scrape', '2024-03-15T00:00:00Z', false, NULL, NULL)")
        run_sql(name, "INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) VALUES "
                      "(900000001, '2024-02', 'lead', 5), (900000001, '2024-03', 'lead', 2), "
                      "(900000003, '2024-01', 'lead', 3), (900000001, 'total', 'all', 42)")
        coverage = fetch(name, "SELECT route_id, tick_status, complete_through::text AS ct, tick_total "
                               "FROM internal.route_tick_coverage ORDER BY route_id")
        months = fetch(name, "SELECT route_id, period, tick_count FROM internal.route_tick_months ORDER BY 1, 2")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "UPDATE internal.route_tick_scrapes SET complete_through = NULL WHERE mp_route_id = 900000001")
    assert coverage == [
        (900000001, "complete", "2024-02-29", 42), (900000002, "missing", None, None), (900000003, "scraped", None, None)]
    assert months == [(900000001, "2024-02", 5)]


def test_existing_accepted_totals_are_backfilled_as_complete():
    with migrated_db("0007_cell_daily_conditions", seed_sql=MP) as name:
        run_sql(name, "INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count, scraped_at) VALUES "
                      "(900000001, 'total', 'all', 42, '2024-03-15T12:00:00Z'), (900000002, '2024-01', 'lead', 3, "
                      "'2024-03-15T12:00:00Z')")
        command.upgrade(_alembic_cfg(name), PIN)
        rows = fetch(name, "SELECT mp_route_id, complete, complete_through::text AS ct, total_ticks "
                           "FROM internal.route_tick_scrapes ORDER BY 1")
    assert rows == [(900000001, True, "2024-02-29", 42)]


def test_0008_downgrade_refuses_while_routes_exist():
    with migrated_db(PIN, seed_sql=MP + AREAS) as name:
        run_sql(name, ROUTES + "(900000001, 900000101, 'trad', false, '{trad}', 'rt-mp-v1', true, NULL, 'h', 'v')")
        with pytest.raises(RuntimeError, match="refusing to downgrade 0008"):
            command.downgrade(_alembic_cfg(name), "0007_cell_daily_conditions")


def test_0008_refuses_without_ltree_when_unprivileged():
    with migrated_db("0007_cell_daily_conditions") as name:
        run_sql(name, "CREATE ROLE p_noext LOGIN PASSWORD 'pw'; GRANT ALL ON SCHEMA public, internal TO p_noext; "
                      "GRANT ALL ON ALL TABLES IN SCHEMA public, internal TO p_noext;")
        cfg = _alembic_cfg(name)
        base = cfg.get_main_option("sqlalchemy.url").split("@", 1)[1]
        cfg.set_main_option("sqlalchemy.url", f"postgresql+asyncpg://p_noext:pw@{base}")
        try:
            with pytest.raises(RuntimeError, match="extensions_mp_catalog.sql"):
                command.upgrade(cfg, PIN)
        finally:
            run_sql(name, "DROP OWNED BY p_noext; DROP ROLE p_noext;")
