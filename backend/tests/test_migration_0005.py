import asyncio

import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg

REV_0004 = "0004_phase2a_foundation"
REV_0005 = "0005_gnis_duplicate_decisions"
DECISION = (
    "INSERT INTO internal.duplicate_decisions (low_id, high_id, score, decision, decided_by, rule_version) "
    "VALUES ({lo}, {hi}, 0.95, '{d}', '{by}', 'r5-v1')"
)
SUMMIT = (
    "INSERT INTO gnis_summits (gnis_id, name, name_key, state_code, lat, lon) "
    "VALUES (1414314, 'Mount McKinley', 'mckinley', 'AK', 63.0691018, -151.0062617)"
)


async def _one(url: str, sql: str) -> object:
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetchval(sql)
    finally:
        await conn.close()


def test_0005_revision_id_fits_alembic_version():
    # G1: alembic_version.version_num is VARCHAR(32); a real upgrade stores the id.
    with migrated_db(REV_0005) as name:
        assert asyncio.run(_one(pg_url(name), "SELECT version_num FROM alembic_version")) == REV_0005


def test_0005_rejects_unordered_duplicate_and_unknown_decisions():
    with migrated_db(REV_0005) as name:
        run_sql(name, DECISION.format(lo=4, hi=5, d="merge", by="auto"))
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, DECISION.format(lo=7, hi=6, d="merge", by="auto"))
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, DECISION.format(lo=6, hi=6, d="merge", by="auto"))
        with pytest.raises(asyncpg.UniqueViolationError):
            run_sql(name, DECISION.format(lo=4, hi=5, d="distinct", by="auto"))
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, DECISION.format(lo=8, hi=9, d="maybe", by="auto"))
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, DECISION.format(lo=8, hi=9, d="merge", by="model"))


def test_0005_gnis_summits_bounds_state_and_coordinates():
    with migrated_db(REV_0005) as name:
        run_sql(name, SUMMIT)
        for bad in (
            "(1, 'x', 'x', 'ak', 63.0, -151.0)",
            "(2, 'x', 'x', 'AK', 91.0, -151.0)",
            "(3, 'x', 'x', 'AK', 63.0, -181.0)",
            "(4, 'x', 'x', 'AK', 'NaN', -151.0)",
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                run_sql(name, f"INSERT INTO gnis_summits (gnis_id, name, name_key, state_code, lat, lon) VALUES {bad}")


def test_0005_downgrade_refuses_to_lose_owner_decisions():
    with migrated_db(REV_0005) as name:
        run_sql(name, DECISION.format(lo=4, hi=5, d="distinct", by="owner"))
        with pytest.raises(RuntimeError, match="owner duplicate decisions"):
            command.downgrade(_alembic_cfg(name), REV_0004)
        run_sql(name, "DELETE FROM internal.duplicate_decisions")
        command.downgrade(_alembic_cfg(name), REV_0004)
        with pytest.raises(asyncpg.UndefinedTableError):
            run_sql(name, "SELECT 1 FROM gnis_summits")


@pytest.mark.parametrize(
    ("seed", "table"),
    [(DECISION.format(lo=4, hi=5, d="merge", by="auto"), "internal.duplicate_decisions"), (SUMMIT, "gnis_summits")],
)
def test_0005_downgrade_refuses_while_the_new_tables_hold_rows(seed, table):
    with migrated_db(REV_0005) as name:
        run_sql(name, seed)
        with pytest.raises(RuntimeError, match=f"{table} has 1 rows"):
            command.downgrade(_alembic_cfg(name), REV_0004)
