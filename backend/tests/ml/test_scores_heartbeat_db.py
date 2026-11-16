"""scores_heartbeat._load_facts against a migrated database holding a materialized toy model. Each test
gets its own database, so none depends on what another one changed."""

import asyncio

import pytest

from app.config import settings
from app.tasks import scores_heartbeat as hb
from tests.ml.test_materialize import VERSION, _catalog_sql, _scored_toy, _write
from tests.pgtest import fetch, migrated_db, requires_pg, run_on_conn, run_sql, sa_url
from tests.test_migration_0012 import SEED

pytestmark = requires_pg
MONTH = 7
ACTIVATE = (f"UPDATE model.registry SET status = 'active', promoted_at = now(), promoted_by = 'owner', "
            f"scores_rows = 1 WHERE version = '{VERSION}'")
# Routes the trainer would cell (app.ml.dataset.AREAS_SQL) that the version has no row for at all.
NEVER_SCORED = ("SELECT count(*) FROM scorable_routes r JOIN canonical_areas a ON a.area_id = r.area_id "
                "WHERE r.type_group <> 'unknown' AND a.lat IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "
                f"model.route_scores s WHERE s.model_version = '{VERSION}' AND s.route_id = r.route_id)")


@pytest.fixture(scope="module")
def toy():
    return _scored_toy()


@pytest.fixture
def db(toy):
    t, summary = toy
    with migrated_db(seed_sql=SEED + _catalog_sql(t)) as name:
        run_on_conn(name, lambda c: _write(c, t, summary))
        yield name


def _facts(name: str, monkeypatch: pytest.MonkeyPatch) -> hb.Facts:
    monkeypatch.setattr(settings, "DATABASE_URL", sa_url(name))
    return asyncio.run(hb._load_facts(MONTH))


def _scored_route(name: str) -> int:
    return int(fetch(name, f"SELECT min(route_id) FROM model.route_scores WHERE model_version = '{VERSION}' "
                           f"AND month = {MONTH}")[0][0])


def _add_route(name: str, route_id: int, area_id: int, type_group: str) -> None:
    run_sql(name, f"INSERT INTO mp_routes (mp_route_id, name, location_id) VALUES ({route_id}, 'Fixture New', {area_id}); "
                  "INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, "
                  f"type_rule_version, scorable, facts_sha256, catalog_version) VALUES ({route_id}, {area_id}, "
                  f"'{type_group}', false, '{{trad}}', 'rt-mp-v3', true, 'x', 'v')")


def test_a_candidate_is_not_an_active_version(db, monkeypatch):
    assert _facts(db, monkeypatch) == hb.Facts(active=[], missing_routes=0, unscored_new_routes=0)


def test_a_complete_promotion_has_nothing_missing(db, monkeypatch):
    run_sql(db, ACTIVATE)
    facts = _facts(db, monkeypatch)
    assert facts.active == [hb.ActiveRow(VERSION, "eb_v1", True, True)]
    assert facts.missing_routes == 0
    # The seed's own route was never in the toy's training snapshot: reported, not missing.
    never = int(fetch(db, NEVER_SCORED)[0][0])
    assert never >= 1 and facts.unscored_new_routes == never


def test_a_scored_route_missing_this_month_fails(db, monkeypatch):
    run_sql(db, ACTIVATE)
    before = _facts(db, monkeypatch)
    run_sql(db, f"DELETE FROM model.route_scores WHERE route_id = {_scored_route(db)} AND month = {MONTH}")
    after = _facts(db, monkeypatch)
    assert (after.missing_routes, after.unscored_new_routes) == (1, before.unscored_new_routes)


def test_a_route_added_after_the_snapshot_is_counted_apart(db, monkeypatch):
    run_sql(db, ACTIVATE)
    before = _facts(db, monkeypatch)
    _add_route(db, 900000777, 900000110, "trad")      # area with coordinates: the trainer would cell it
    _add_route(db, 900000778, 900000110, "unknown")   # unknown type group: never a cell
    _add_route(db, 900000779, 900000100, "trad")      # area without coordinates: never a cell
    after = _facts(db, monkeypatch)
    assert after.missing_routes == 0
    assert after.unscored_new_routes == before.unscored_new_routes + 1


def test_an_active_kernel_reports_its_family_and_skips_the_count(db, monkeypatch):
    run_sql(db, "UPDATE model.registry SET status = 'active' WHERE version = 'k0'")
    facts = _facts(db, monkeypatch)
    assert facts == hb.Facts(active=[hb.ActiveRow("k0", "kernel", False, False)], missing_routes=0)
    assert hb._verdict(facts)["reason"] == "kernel_active_nothing_servable"


def test_a_tampered_bundle_fails_the_digest(db, monkeypatch):
    run_sql(db, ACTIVATE)
    run_sql(db, f"UPDATE model.registry SET bundle = '\\x00'::bytea WHERE version = '{VERSION}'")
    assert _facts(db, monkeypatch).active == [hb.ActiveRow(VERSION, "eb_v1", True, False)]
