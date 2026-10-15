import asyncpg
import pytest
from alembic import command

from tests.pgtest import fetch, migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg


def test_0010_checks_clean_and_enforces_one_target():
    # At head, not PIN: alembic check compares every model to the DB and refuses a DB behind head.
    with migrated_db() as name:
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


def test_0010_owner_decisions_cannot_be_changed_or_deleted():
    with migrated_db("0010_accident_route_links") as name:
        run_sql(name, "INSERT INTO accidents (accident_id) VALUES (1), (2); INSERT INTO internal.r10_unresolved "
                      "(accident_id, reason, owner_decision) VALUES (1, 'legacy_mountain_only', 'no_link'), "
                      "(2, 'legacy_mountain_only', NULL)")
        for sql in ("UPDATE internal.r10_unresolved SET owner_decision = NULL WHERE accident_id = 1",
                    "UPDATE internal.r10_unresolved SET candidate_area_ids = '{1}' WHERE accident_id = 1",
                    "DELETE FROM internal.r10_unresolved WHERE accident_id = 1"):
            with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="owner decision"):
                run_sql(name, sql)
        run_sql(name, "UPDATE internal.r10_unresolved SET candidate_area_ids = '{1}' WHERE accident_id = 2")
        run_sql(name, "UPDATE internal.r10_unresolved SET owner_decision = 'no_link' WHERE accident_id = 2")
        run_sql(name, "INSERT INTO accidents (accident_id) VALUES (3); INSERT INTO internal.r10_unresolved "
                      "(accident_id, reason) VALUES (3, 'legacy_mountain_only')")
        run_sql(name, "DELETE FROM internal.r10_unresolved WHERE accident_id = 3")
        assert fetch(name, "SELECT accident_id FROM internal.r10_unresolved ORDER BY 1") == [(1,), (2,)]
