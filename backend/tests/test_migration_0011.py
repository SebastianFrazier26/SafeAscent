import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
PIN = "0011_exposure_index"
SEED = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), (900000110, 'Fixture Crag', 900000100, 40.2, -105.3);
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  (900000100, NULL, '900000100', 1, 'us', 'CO', NULL, NULL, 'none', NULL, NULL, 'v'),
  (900000110, 900000100, '900000100.900000110', 2, 'us', 'CO', 40.2, -105.3, 'own', 900000110,
   '40.20000:-105.30000', 'v');
"""


def _row(**over: str) -> str:
    cols = {"area_id": "900000110", "type_group": "'trad'", "month": "7", "n_routes": "1", "proxy": "'{}'",
            "proxy_version": "'x-mp-v1'", "as_of": "'2026-10-15'", "data_cutoff": "'2026-10-15'",
            "tick_coverage": "NULL", "exposure_point": "NULL", "exposure_lo": "NULL", "exposure_hi": "NULL"}
    cols.update(over)
    return f"INSERT INTO internal.exposure_index ({', '.join(cols)}) VALUES ({', '.join(cols.values())})"


def test_0011_checks():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        run_sql(name, _row())
        run_sql(name, _row(month="8", tick_coverage="0", exposure_point="2", exposure_lo="0.6667", exposure_hi="6"))
        for bad in ({"type_group": "'unknown'"}, {"month": "13"}, {"n_routes": "0"},
                    {"data_cutoff": "'2026-10-16'"}, {"exposure_point": "1.0"}, {"tick_coverage": "1.5"},
                    {"proxy": "'[]'"}):
            with pytest.raises(asyncpg.CheckViolationError):
                run_sql(name, _row(**{"month": "9", **bad}))


def test_0011_models_match_at_head():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))


def test_0011_downgrade_refuses_while_rows_exist():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        run_sql(name, _row())
        with pytest.raises(RuntimeError, match="refusing to downgrade 0011"):
            command.downgrade(_alembic_cfg(name), "0010_accident_route_links")
        run_sql(name, "DELETE FROM internal.exposure_index")
        command.downgrade(_alembic_cfg(name), "0010_accident_route_links")
