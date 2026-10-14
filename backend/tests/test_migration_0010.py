import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg


def test_0010_checks_clean_and_enforces_one_target():
    with migrated_db("0010_accident_route_links") as name:
        command.check(_alembic_cfg(name))
        run_sql(name, "INSERT INTO accidents (accident_id) VALUES (1)")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO accident_legacy_links (accident_id, method, r10_version) VALUES (1, 'owner', 'v')")
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(name, "INSERT INTO internal.r10_unresolved (accident_id, reason, owner_decision) "
                          "VALUES (1, 'legacy_mountain_only', 'link')")


def test_0010_downgrade_refuses_while_owner_decisions_exist():  # any row refuses (review fix 2026-10-14)
    with migrated_db("0010_accident_route_links") as name:
        run_sql(name, "INSERT INTO accidents (accident_id) VALUES (1); INSERT INTO internal.r10_unresolved "
                      "(accident_id, reason, owner_decision) VALUES (1, 'legacy_mountain_only', 'no_link')")
        with pytest.raises(RuntimeError, match="refusing to downgrade 0010"):
            command.downgrade(_alembic_cfg(name), "0009_static_features")
