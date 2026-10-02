import asyncio
import importlib.util
from pathlib import Path

import asyncpg
import pytest
from alembic import command

from app.data.repair import dates
from app.pipelines.grid import grid_bucket
from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg

X5_POINTS = [(40.05, -105.25), (40.15, -105.35), (64.15, -149.95), (19.45, -155.25), (51.85, -176.65), (36.55, -118.25)]
ACCIDENT = (
    "INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, "
    "date_precision, geocode_precision, tz) VALUES "
    "(1, 'AAC', '2010-07-10', 40.05, -105.25, true, 'climbing', 'US', 'day', 'crag', 'America/Denver'), "
    "(2, 'AAC', '2010-07-10', 40.05, -105.25, true, 'climbing', 'US', 'day', 'region_fallback', 'America/Denver');"
)


async def _fetch(url: str, sql: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetch(sql)
    finally:
        await conn.close()


def test_view_calls_the_sql_grid_function_not_a_copy():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))
        [row] = asyncio.run(_fetch(pg_url(name), "SELECT pg_get_viewdef('accident_conditions'::regclass) AS d"))
        assert "grid_bucket_key(" in row["d"] and "floor(" not in row["d"]


def test_sql_and_python_grid_keys_agree_at_x5_points():
    values = ", ".join(f"({i}, {lat}::float8, {lon}::float8)" for i, (lat, lon) in enumerate(X5_POINTS))
    with migrated_db() as name:
        rows = asyncio.run(_fetch(pg_url(name), f"SELECT i, grid_bucket_key(lat, lon) AS k FROM (VALUES {values}) v(i, lat, lon) ORDER BY i"))
    assert [r["k"] for r in rows] == [grid_bucket(lat, lon) for lat, lon in X5_POINTS]


def test_accident_conditions_joins_on_local_day_cell_and_tz():
    b = grid_bucket(40.05, -105.25)
    seed = ACCIDENT + "".join(
        f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, record_kind, source) "
        f"VALUES ({b}, 'America/Denver', DATE '2010-07-10' - {k}, {20 + k}, 'era5', 'open_meteo_archive');"
        for k in range(0, 8)
    ) + (
        f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, record_kind, source) "
        f"VALUES ({b}, 'UTC', DATE '2010-07-10', 99, 'era5', 'open_meteo_archive');"
    )
    with migrated_db(seed_sql=seed) as name:
        rows = asyncio.run(_fetch(pg_url(name), "SELECT accident_id, day_offset, tmax, tz, record_kind FROM accident_conditions ORDER BY day_offset"))
    assert {r["accident_id"] for r in rows} == {1}
    assert [(r["day_offset"], r["tmax"]) for r in rows] == [(-k, 20 + k) for k in range(6, -1, -1)]
    assert {(r["tz"], r["record_kind"]) for r in rows} == {("America/Denver", "era5")}


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, source) VALUES (1, 'UTC', '2020-01-01', 'guess', 't')",
        "INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, is_forecast, source) VALUES (1, 'UTC', '2020-01-01', 'era5', true, 't')",
        "INSERT INTO grid_bucket_series (grid_bucket, tz, first_era5_date) VALUES (1, 'UTC', '2020-01-01')",
        "INSERT INTO grid_bucket_series (grid_bucket, tz, first_era5_date, last_era5_date) VALUES (1, 'UTC', '2020-01-02', '2020-01-01')",
        "INSERT INTO cell_normals_status (grid_bucket, status) VALUES (1, 'done')",
        "INSERT INTO cell_climate_normals (grid_bucket, month, n_years, period_start_year, period_end_year, source, normals_version) VALUES (1, 13, 10, 2016, 2025, 't', 'n-v2')",
    ],
)
def test_checks_reject_bad_rows(sql):
    with migrated_db() as name:
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, sql)


def test_downgrade_refuses_with_paid_rows():
    b = grid_bucket(40.05, -105.25)
    seed = (f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, source) "
            f"VALUES ({b}, 'America/Denver', '2020-01-01', 'era5', 'open_meteo_archive');")
    with migrated_db(revision="0007_cell_daily_conditions", seed_sql=seed) as name:
        with pytest.raises(RuntimeError, match="refusing to downgrade 0007"):
            command.downgrade(_alembic_cfg(name), "0006_accidents_clean")


def test_range_year_source_matches_the_r1_rule():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0007_cell_daily_conditions.py"
    spec = importlib.util.spec_from_file_location("m0007", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.RANGE_YEAR_SOURCE == dates.YEAR_SOURCE


def test_revision_id_fits_alembic_version():
    assert len("0007_cell_daily_conditions") <= 32


def _seed_series(bucket: int, days: range) -> str:
    return "".join(
        f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, record_kind, source) "
        f"VALUES ({bucket}, 'America/Denver', DATE '2010-07-10' - {k}, {20 + k}, 'era5', 'open_meteo_archive');"
        for k in days
    )


def test_a_missing_day_is_an_absent_row_never_a_zero():
    b = grid_bucket(40.05, -105.25)
    seed = ACCIDENT + _seed_series(b, range(0, 7)) + "DELETE FROM cell_daily_conditions WHERE date = '2010-07-08';"
    with migrated_db(seed_sql=seed) as name:
        rows = asyncio.run(_fetch(pg_url(name), "SELECT day_offset FROM accident_conditions WHERE accident_id = 1 ORDER BY 1"))
    assert [r["day_offset"] for r in rows] == [-6, -5, -4, -3, -1, 0]


def test_unresolved_tz_gets_no_window():
    b = grid_bucket(40.05, -105.25)
    seed = ACCIDENT + _seed_series(b, range(0, 7)) + "UPDATE accidents SET tz = NULL WHERE accident_id = 1;"
    with migrated_db(seed_sql=seed) as name:
        rows = asyncio.run(_fetch(pg_url(name), "SELECT count(*) AS n FROM accident_conditions"))
    assert rows[0]["n"] == 0


def test_r1_range_year_rows_never_get_a_window():
    # D13: an AAC neighbour-interpolated year range is not a known day, even if labelled 'day'.
    b = grid_bucket(40.05, -105.25)
    seed = (
        "INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, "
        "date_precision, geocode_precision, tz, year_source, year_lo, year_hi) VALUES "
        "(3, 'AAC', '2010-07-10', 40.05, -105.25, true, 'climbing', 'US', 'day', 'crag', 'America/Denver', "
        "'aac_source_id_neighbours', 2009, 2010), "
        "(4, 'AAC', '2010-07-10', 40.05, -105.25, true, 'climbing', 'US', 'day', 'crag', 'America/Denver', "
        "'aac_source_id_neighbours', 2010, 2010);"
    ) + _seed_series(b, range(0, 7))
    with migrated_db(seed_sql=seed) as name:
        rows = asyncio.run(_fetch(pg_url(name), "SELECT DISTINCT accident_id FROM accident_conditions"))
    assert [r["accident_id"] for r in rows] == [4]


def test_downgrade_without_paid_rows_removes_everything_0007_added():
    with migrated_db(revision="0007_cell_daily_conditions") as name:
        command.downgrade(_alembic_cfg(name), "0006_accidents_clean")
        rows = asyncio.run(_fetch(pg_url(name), (
            "SELECT to_regclass('accident_conditions')::text AS v, to_regclass('cell_daily_conditions')::text AS t, "
            "to_regclass('accidents_clean_daily')::text AS d, "
            "EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'accidents' AND column_name = 'tz') AS tz"
        )))
    assert dict(rows[0]) == {"v": None, "t": None, "d": "accidents_clean_daily", "tz": False}
