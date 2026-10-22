"""scores_heartbeat._load_facts against a migrated database holding a materialized toy model."""

import asyncio

import pytest

from app.config import settings
from app.tasks import scores_heartbeat as hb
from tests.ml.test_materialize import VERSION, _catalog_sql, _scored_toy, _write
from tests.pgtest import fetch, migrated_db, requires_pg, run_on_conn, run_sql, sa_url
from tests.test_migration_0012 import SEED

pytestmark = requires_pg
MONTH = 7


@pytest.fixture(scope="module")
def db():
    toy, summary = _scored_toy()
    with migrated_db(seed_sql=SEED + _catalog_sql(toy)) as name:
        run_on_conn(name, lambda c: _write(c, toy, summary))
        yield name


def _facts(name: str, monkeypatch: pytest.MonkeyPatch) -> hb.Facts:
    monkeypatch.setattr(settings, "DATABASE_URL", sa_url(name))
    return asyncio.run(hb._load_facts(MONTH))


def _unscored(name: str) -> int:
    return int(fetch(name, "SELECT count(*) FROM scorable_routes r WHERE NOT EXISTS (SELECT 1 FROM model.route_scores s "
                           f"WHERE s.route_id = r.route_id AND s.month = {MONTH})")[0][0])


def test_a_candidate_is_not_an_active_version(db, monkeypatch):
    assert _facts(db, monkeypatch) == hb.Facts(active=[], missing_routes=0)


def test_an_active_version_reports_bundle_and_missing_rows(db, monkeypatch):
    run_sql(db, f"UPDATE model.registry SET status = 'active', promoted_at = now(), promoted_by = 'owner', "
                f"scores_rows = 1 WHERE version = '{VERSION}'")
    baseline = _unscored(db)
    facts = _facts(db, monkeypatch)
    assert facts.active == [hb.ActiveRow(VERSION, True, True)]
    assert facts.missing_routes == baseline

    run_sql(db, f"DELETE FROM model.route_scores WHERE ctid IN (SELECT ctid FROM model.route_scores "
                f"WHERE month = {MONTH} LIMIT 1)")
    assert _facts(db, monkeypatch).missing_routes == baseline + 1


def test_a_tampered_bundle_fails_the_digest(db, monkeypatch):
    run_sql(db, f"UPDATE model.registry SET bundle = '\\x00'::bytea WHERE version = '{VERSION}'")
    assert _facts(db, monkeypatch).active == [hb.ActiveRow(VERSION, True, False)]
