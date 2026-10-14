import uuid

import asyncpg
import pytest
from sqlalchemy import text

from app.pipelines.area_tree import AreaNode
from app.pipelines.mp_catalog import RouteFacts, classify_route, refresh, run_logged
from tests.pgtest import migrated_db, requires_pg, run_on_conn, run_on_engine, run_sql

PIN = "0008_mp_catalog"
SEED = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL),
  (900000110, 'Fixture Region', 900000100, 40.2, -105.3),
  (900000111, 'Fixture Crag', 900000110, 40.2, -105.3),
  (900000112, 'Fixture Wall', 900000110, 40.25, -105.35),
  (900000113, 'Fixture Nowhere', 900000110, NULL, NULL),
  (900000200, 'Fixture Abroad', NULL, NULL, NULL), (900000201, 'Fixture Abroad Crag', 900000200, 46.5, 7.9),
  (900000300, 'Fixture Loop A', 900000301, 40.0, -105.0), (900000301, 'Fixture Loop B', 900000300, 40.0, -105.0);
INSERT INTO mp_routes (mp_route_id, name, location_id, type, grade, latitude, longitude) VALUES
  (900000001, 'Fixture One', 900000111, 'Trad', '5.9', NULL, NULL),
  (900000002, 'Fixture Two', 900000112, 'Sport, TR', '5.10a', NULL, NULL),
  (900000003, 'Fixture Three', 900000112, 'Ice', 'WI4', NULL, NULL),
  (900000004, 'Fixture Four', 900000112, 'Boulder', 'V3', NULL, NULL),
  (900000005, 'Fixture Five', 900000112, 'unknown', '5.8', NULL, NULL),
  (900000006, 'Fixture Six', 900000113, 'Trad', '5.7', 40.21, -105.31),
  (900000007, 'Fixture Seven', 900000201, 'Trad', '5.9', NULL, NULL),
  (900000008, 'Fixture Eight', 900000300, 'Trad', '5.9', NULL, NULL),
  (900000009, 'Fixture Nine', NULL, 'Trad', '5.9', NULL, NULL),
  (900000011, 'Fixture Rock', 900000112, 'Rock', '5.9', NULL, NULL);
"""


def _refresh(name, dry_run=False):
    return run_on_conn(name, lambda c: refresh(c, run_id=uuid.uuid4(), dry_run=dry_run))


def _routes(name):
    return {r[0]: r[1:] for r in run_on_conn(name, lambda c: c.execute(text(
        "SELECT route_id, type_group, scorable, unscorable_reason, area_id FROM canonical_routes")))}


def test_classify_route_reason_precedence():
    us = AreaNode(1, None, "1", 1, "us", "CO", 40.0, -105.0, "own", 1, "40.00000:-105.00000", None)
    nowhere = AreaNode(2, None, "2", 2, "us", "CO", None, None, "none", None, None, "missing_coords")
    areas = {1: us, 2: nowhere}
    assert classify_route(RouteFacts(9, 1, "Boulder", "V2"), areas).unscorable_reason == "boulder"
    assert classify_route(RouteFacts(9, None, "Trad", "5.9"), areas).unscorable_reason == "no_area"
    assert classify_route(RouteFacts(9, 2, "unknown", "5.9"), areas).unscorable_reason == "unknown_type"
    assert classify_route(RouteFacts(9, 2, "Trad", "5.9"), areas).unscorable_reason == "no_coordinates"
    ok = classify_route(RouteFacts(9, 1, "Trad", "5.9"), areas)
    assert (ok.scorable, ok.unscorable_reason, ok.type_group, ok.area_id) == (True, None, "trad", 1)


@requires_pg
def test_refresh_builds_tree_types_and_scorable():
    with migrated_db(PIN, seed_sql=SEED) as name:
        result = _refresh(name)
        assert result.status == "ok"
        assert result.report.quarantined["cycle"] == 2
        assert _routes(name) == {
            900000001: ("trad", True, None, 900000111),
            900000002: ("sport", True, None, 900000112),
            900000003: ("ice", True, None, 900000112),
            900000004: (None, False, "boulder", 900000112),
            900000005: ("unknown", False, "unknown_type", 900000112),
            900000006: ("trad", False, "no_coordinates", 900000113),
            900000007: ("trad", False, "not_us", 900000201),
            900000008: ("trad", False, "no_area", 900000300),
            900000009: ("trad", False, "no_area", None),
            900000011: ("rock", True, None, 900000112),
        }
        crag = run_on_conn(name, lambda c: c.execute(text(
            "SELECT path::text, state_code, coord_precision, coord_source_id, point_key FROM canonical_areas "
            "WHERE area_id = 900000111")))
        assert crag == [
            ("900000100.900000110.900000111", "CO", "inherited", 900000110, "40.20000:-105.30000")]


@requires_pg
def test_route_coordinates_are_never_read():
    # Route 900000006 carries mp_routes lat/lon; its area 900000113 has none. Nothing may flow upward.
    with migrated_db(PIN, seed_sql=SEED) as name:
        _refresh(name)
        area = run_on_conn(name, lambda c: c.execute(text(
            "SELECT lat, lon, point_key, coord_precision FROM canonical_areas WHERE area_id = 900000113")))
        assert area == [(None, None, None, "none")]


@requires_pg
def test_rerun_is_idempotent_and_a_fact_change_touches_one_route():
    with migrated_db(PIN, seed_sql=SEED) as name:
        first = _refresh(name)
        again = _refresh(name)
        assert (again.counts["routes_inserted"], again.counts["routes_changed"]) == (0, 0)
        assert (again.counts["areas_inserted"], again.counts["areas_changed"]) == (0, 0)
        assert first.counts["routes_inserted"] == 10
        run_sql(name, "UPDATE mp_routes SET type = 'Trad, Grade IV' WHERE mp_route_id = 900000001")
        third = _refresh(name)
        assert (third.counts["routes_inserted"], third.counts["routes_changed"]) == (0, 1)
        assert _routes(name)[900000001][0] == "alpine"


@requires_pg
def test_grade_only_change_updates_the_facts_hash():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _refresh(name)
        run_sql(name, "UPDATE mp_routes SET grade = '5.10b' WHERE mp_route_id = 900000002")
        assert _refresh(name).counts["routes_changed"] == 1


@requires_pg
def test_scorable_drop_rejects_and_writes_nothing():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _refresh(name)
        run_sql(name, "UPDATE mp_routes SET type = 'unknown' WHERE mp_route_id = 900000001")
        result = _refresh(name)
        assert result.status == "rejected" and "scorable" in result.problems[0]
        assert _routes(name)[900000001][:2] == ("trad", True)


@requires_pg
def test_dry_run_writes_nothing():
    with migrated_db(PIN, seed_sql=SEED) as name:
        result = _refresh(name, dry_run=True)
        assert result.counts["routes_scorable"] == 4
        assert run_on_conn(name, lambda c: c.execute(text("SELECT count(*) FROM canonical_routes"))) == [(0,)]


@requires_pg
def test_absent_ticks_read_missing_after_refresh():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _refresh(name)
        rows = run_on_conn(name, lambda c: c.execute(text(
            "SELECT DISTINCT tick_status, tick_total FROM internal.route_tick_coverage")))
        assert rows == [("missing", None)]


ORPHAN_SEED = SEED + """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000400, 'Fixture Orphan', 900000499, 40.1, -105.1);
INSERT INTO mp_routes (mp_route_id, name, location_id, type, grade, latitude, longitude) VALUES
  (900000010, 'Fixture Ten', 900000400, 'Trad', '5.9', NULL, NULL);
"""


def test_cycle_and_orphan_areas_are_no_area_even_when_located():
    def node(status: str) -> AreaNode:
        return AreaNode(3, 4, None, None, status, None, 40.0, -105.0, "own", 3, "40.00000:-105.00000", None)

    for status in ("cycle", "orphan_parent"):
        route = classify_route(RouteFacts(9, 3, "Trad", "5.9"), {3: node(status)})
        assert (route.scorable, route.unscorable_reason, route.area_id) == (False, "no_area", 3)


@requires_pg
def test_orphan_parent_area_route_is_no_area_and_quarantined():
    with migrated_db(PIN, seed_sql=ORPHAN_SEED) as name:
        result = _refresh(name)
        assert result.status == "ok" and result.report.quarantined["orphan_parent"] == 1
        assert _routes(name)[900000010] == ("trad", False, "no_area", 900000400)


@requires_pg
def test_refresh_never_deletes_and_mp_rows_stay_pinned():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _refresh(name)
        seen = "SELECT route_id, first_seen_at FROM canonical_routes ORDER BY route_id"
        before = run_on_conn(name, lambda c: c.execute(text(seen)))
        run_sql(name, "UPDATE mp_routes SET type = 'Sport' WHERE mp_route_id = 900000001")
        assert _refresh(name).counts["routes_changed"] == 1
        assert run_on_conn(name, lambda c: c.execute(text(seen))) == before
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            run_sql(name, "DELETE FROM mp_routes WHERE mp_route_id = 900000009")


@requires_pg
def test_run_logged_records_rejected_run_and_its_quarantine():
    with migrated_db(PIN, seed_sql=SEED) as name:
        assert run_on_engine(name, lambda e: run_logged(e, dry_run=False)).status == "ok"
        run_sql(name, "UPDATE mp_routes SET type = 'unknown' WHERE mp_route_id = 900000001")
        assert run_on_engine(name, lambda e: run_logged(e, dry_run=False)).status == "rejected"
        log = run_on_conn(name, lambda c: c.execute(text(
            "SELECT status, rows_quarantined FROM source_ingest_log WHERE source = 'mp_catalog' ORDER BY started_at")))
        assert log == [("ok", 2), ("rejected", 2)]
        assert _routes(name)[900000001][:2] == ("trad", True)
