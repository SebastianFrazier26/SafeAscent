import asyncio
from decimal import Decimal

import asyncpg
import pytest
from alembic import command

from app.pipelines.grid import grid_bucket, grid_bucket_sql
from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg

SEED = """
INSERT INTO accidents (accident_id, source, date, latitude, longitude) VALUES
  (1, 'AAC', '2001-05-02', 40.0, -105.0), (2, 'NPS', '2010-07-01', 36.5, -118.3);
"""


async def _one(url: str, sql: str) -> object:
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetchval(sql)
    finally:
        await conn.close()


def _val(name: str, sql: str) -> object:
    return asyncio.run(_one(pg_url(name), sql))


def test_0004_snapshots_accidents_before_adding_columns_and_checks_clean():
    with migrated_db("0003_hist_insufficient_data", SEED) as name:
        cfg = _alembic_cfg(name)
        command.upgrade(cfg, "head")
        command.check(cfg)
        assert _val(name, "SELECT count(*) FROM internal.accidents_raw") == 2
        assert _val(
            name,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'internal' AND table_name = 'accidents_raw' AND column_name = 'date_precision'",
        ) == 0
        assert _val(name, "SELECT bool_and(is_canonical) FROM accidents") is True
        assert _val(name, "SELECT count(*) FROM accidents WHERE exp_stated_level = 'unknown' AND guided = 'unknown'") == 2


HALF_STEPS = [
    ("40.05", "-105.25"),
    ("40.05", "-105.35"),
    ("64.15", "-149.95"),
    ("19.85", "-155.45"),
    ("52.95", "175.05"),
    ("40.04999999999999999", "-105.3"),
    ("40.1", "-105.25000000000000001"),
]


def test_grid_bucket_key_matches_python_at_half_steps():
    with migrated_db("head") as name:
        for lat, lon in HALF_STEPS:
            expr = grid_bucket_sql("'" + lat + "'::numeric", "'" + lon + "'::numeric")
            assert _val(name, f"SELECT {expr}") == grid_bucket(float(Decimal(lat)), float(Decimal(lon))), (lat, lon)
        assert _val(
            name,
            "SELECT provolatile = 'i' FROM pg_proc WHERE proname = 'grid_bucket_key'",
        ) is True


def test_0004_enum_checks_reject_unknown_values():
    with migrated_db("head", SEED) as name:
        for sql in (
            "UPDATE accidents SET date_precision = 'week' WHERE accident_id = 1",
            "UPDATE accidents SET geocode_precision = 'city' WHERE accident_id = 1",
            "UPDATE accidents SET exp_years_climbing = 200 WHERE accident_id = 1",
            "UPDATE accidents SET year_lo = 2005, year_hi = 2001 WHERE accident_id = 1",
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                run_sql(name, sql)


def test_0004_tick_quarantine_reason_check():
    with migrated_db("head") as name:
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(
                name,
                "INSERT INTO mp_ticks (tick_id, route_id, climber_name, quarantine_reason) "
                "VALUES (1, '900000001', 'x', 'bad')",
            )


def test_0004_revision_key_is_unique_per_rule_version():
    with migrated_db("head", SEED) as name:
        insert = (
            "INSERT INTO internal.accident_revisions (accident_id, field, old_value, new_value, method, rule_version, run_id) "
            "VALUES (1, 'date_precision', NULL, 'day', 'r2', 'r2-v1', gen_random_uuid())"
        )
        run_sql(name, insert)
        with pytest.raises(asyncpg.UniqueViolationError):
            run_sql(name, insert)


def test_0004_tick_period_check():
    with migrated_db("head") as name:
        run_sql(
            name,
            "INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) "
            "VALUES (900000001, '2025-01', 'lead', 3), (900000001, 'total', 'all', 3)",
        )
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(
                name,
                "INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) "
                "VALUES (900000001, '2025-13', 'lead', 1)",
            )


def test_0004_downgrade_refuses_while_revisions_exist():
    with migrated_db("head", SEED) as name:
        run_sql(
            name,
            "INSERT INTO internal.accident_revisions (accident_id, field, old_value, new_value, method, rule_version, run_id) "
            "VALUES (1, 'country', NULL, 'US', 'r3', 'r3-v1', gen_random_uuid())",
        )
        with pytest.raises(RuntimeError, match="refusing to downgrade 0004"):
            command.downgrade(_alembic_cfg(name), "0003_hist_insufficient_data")


def test_0004_downgrade_is_clean_when_empty():
    with migrated_db("head", SEED) as name:
        command.downgrade(_alembic_cfg(name), "0003_hist_insufficient_data")
        assert _val(name, "SELECT to_regclass('internal.accidents_raw')") is None
        assert _val(name, "SELECT to_regclass('public.source_ingest_log')") is None
        assert _val(name, "SELECT count(*) FROM pg_proc WHERE proname = 'grid_bucket_key'") == 0


def test_0004_refuses_without_internal_schema_when_unprivileged():
    with migrated_db("0003_hist_insufficient_data") as name:
        # A non-superuser without CREATE on the database stands in for prod's migrator.
        # The guard raises before any DDL, so table ownership never comes into play.
        run_sql(
            name,
            "CREATE ROLE p2_nocreate LOGIN PASSWORD 'pw';"
            "GRANT ALL ON SCHEMA public TO p2_nocreate;"
            "GRANT ALL ON ALL TABLES IN SCHEMA public TO p2_nocreate;",
        )
        cfg = _alembic_cfg(name)
        base = pg_url(name).split("://", 1)[1].split("@", 1)[1]
        cfg.set_main_option("sqlalchemy.url", f"postgresql+asyncpg://p2_nocreate:pw@{base}")
        try:
            with pytest.raises(RuntimeError, match="create_roles_phase2.sql"):
                command.upgrade(cfg, "head")
            assert _val(name, "SELECT to_regclass('public.source_ingest_log')") is None
            assert _val(name, "SELECT version_num FROM alembic_version") == "0003_hist_insufficient_data"
        finally:
            run_sql(name, "DROP OWNED BY p2_nocreate; DROP ROLE p2_nocreate;")
