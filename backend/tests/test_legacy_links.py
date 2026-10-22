import argparse
import csv
import uuid

import pytest
from sqlalchemy import text

from app.data.repair import review
from app.data.repair.legacy_links import LegacyRow, Link, plan_row, run_r10, run_r10_export, run_r10_import
from app.pipelines.mp_catalog import refresh
from tests.pgtest import fetch, migrated_db, requires_pg, run_on_conn, run_sql

PIN = "0010_accident_route_links"
SEED = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), (900000110, 'Fixture Crag', 900000100, 40.0, -105.3);
INSERT INTO mp_routes (mp_route_id, name, location_id, type, grade) VALUES
  (900000011, 'Fixture Arete', 900000110, 'Trad', '5.9'), (900000012, 'Fixture Dihedral', 900000110, 'Trad', '5.10a');
INSERT INTO mountains (mountain_id, name, latitude, longitude) VALUES (7, 'Fixture Crag', 40.01, -105.3);
INSERT INTO routes (route_id, name, latitude, longitude, grade, type, mp_route_id) VALUES
  (55, 'Fixture Dihedral', 40.0005, -105.3, '5.10a', 'Trad', '900000012'),
  (56, 'Fixture Dihedral Direct', 40.0005, -105.3, '5.10b', 'Trad', NULL),
  (57, 'Fixture Arete', 40.0005, -105.3, '5.9', 'Trad', '9000x'),
  (58, 'Fixture Gone', 40.0005, -105.3, '5.9', 'Trad', '900000099'),
  (59, 'Fixture Lonely', 45.0, -110.0, '5.8', 'Trad', NULL),
  (60, 'Fixture Nowhere', NULL, NULL, '5.8', 'Trad', NULL);
INSERT INTO accidents (accident_id, mp_route_id, route_id, mountain_id) VALUES
  (1, 900000011, 55, NULL), (2, NULL, 55, NULL), (3, NULL, 56, NULL), (4, NULL, NULL, 7),
  (5, NULL, 57, NULL), (6, NULL, 58, NULL), (7, NULL, 59, NULL), (8, NULL, NULL, NULL),
  (9, NULL, 60, NULL);
"""


def _ns(**kw):
    return argparse.Namespace(**({"apply": False, "out": None, "review_file": None, "overwrite": False} | kw))


def _prepare(name):
    assert run_on_conn(name, lambda c: refresh(c, run_id=uuid.uuid4())).status == "ok"


def test_plan_row_rules():
    catalog = frozenset({900000011, 900000012})
    base = dict(route_name=None, route_lat=None, route_lon=None, mountain_name=None, mountain_lat=None, mountain_lon=None)
    assert plan_row(LegacyRow(1, 900000011, 55, None, "900000012", **base), catalog) is None
    assert plan_row(LegacyRow(2, None, 55, None, " 900000012 ", **base), catalog) == Link(2, 900000012, None, "legacy_mp_id")
    assert plan_row(LegacyRow(3, None, 56, None, None, **base), catalog).reason == "legacy_route_no_mp_id"
    assert plan_row(LegacyRow(5, None, 57, None, "9000x", **base), catalog).reason == "legacy_route_no_mp_id"
    assert plan_row(LegacyRow(6, None, 58, None, "900000099", **base), catalog).reason == "legacy_mp_id_not_in_catalog"
    assert plan_row(LegacyRow(4, None, None, 7, None, **base), catalog).reason == "legacy_mountain_only"
    assert plan_row(LegacyRow(8, None, None, None, None, **base), catalog) is None
    assert plan_row(LegacyRow(10, 900000013, None, None, None, **base), catalog).reason == "not_in_catalog"


@requires_pg
def test_identity_and_legacy_links_through_the_view():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepare(name)
        out = run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        assert out["links"] == {"legacy_mp_id": 1} and out["legacy_conflict"] == 1
        rows = run_on_conn(name, lambda c: c.execute(text(
            "SELECT accident_id, route_id, method FROM accident_route_links ORDER BY accident_id")))
        assert rows == [(1, 900000011, "mp_route_id"), (2, 900000012, "legacy_mp_id")]


@requires_pg
def test_identity_needs_a_catalog_route():
    with migrated_db(PIN, seed_sql=SEED) as name:
        rows = run_on_conn(name, lambda c: c.execute(text("SELECT accident_id FROM accident_route_links")))
        assert rows == []


@requires_pg
def test_unresolved_links_are_recorded_never_guessed():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepare(name)
        before = run_on_conn(name, lambda c: c.execute(text("SELECT accident_id, route_id, mountain_id FROM accidents ORDER BY 1")))
        run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        rows = run_on_conn(name, lambda c: c.execute(text(
            "SELECT accident_id, reason, candidate_route_ids, candidate_area_ids, candidates_searched "
            "FROM internal.r10_unresolved ORDER BY 1")))
        # 7 was searched and found nothing within 2 km; 9 has no coordinates, so it was never searched.
        assert rows == [
            (3, "legacy_route_no_mp_id", [900000012, 900000011], [], True),
            (4, "legacy_mountain_only", [], [900000110], True),
            (5, "legacy_route_no_mp_id", [900000011, 900000012], [], True),
            (6, "legacy_mp_id_not_in_catalog", [], [], False),
            (7, "legacy_route_no_mp_id", [], [], True),
            (9, "legacy_route_no_mp_id", [], [], False),
        ]
        after = run_on_conn(name, lambda c: c.execute(text("SELECT accident_id, route_id, mountain_id FROM accidents ORDER BY 1")))
        assert after == before


@requires_pg
def test_rerun_is_idempotent_and_a_later_mp_route_id_wins():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepare(name)
        first = run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        again = run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        assert {k: first[k] for k in ("links", "unresolved")} == {k: again[k] for k in ("links", "unresolved")}
        run_sql(name, "UPDATE accidents SET mp_route_id = 900000011 WHERE accident_id = 2")
        rows = run_on_conn(name, lambda c: c.execute(text("SELECT route_id, method FROM accident_route_links WHERE accident_id = 2")))
        assert rows == [(900000011, "mp_route_id")]


@requires_pg
def test_owner_decisions_round_trip_and_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepare(name)
        run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        out = tmp_path / "r10.csv"
        assert run_on_conn(name, lambda c: run_r10_export(c, _ns(out=str(out))))["rows"] == 6
        with pytest.raises(FileExistsError):
            run_on_conn(name, lambda c: run_r10_export(c, _ns(out=str(out))))
        with out.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert "url" not in rows[0] and "description" not in rows[0]
        by_id = {row["accident_id"]: row for row in rows}
        assert (by_id["3"]["candidate_1_id"], by_id["3"]["candidate_1_coord_precision"]) == ("900000012", "own")
        assert (by_id["7"]["candidates_searched"], by_id["9"]["candidates_searched"]) == ("True", "False")
        for row in rows:
            row["decided_kind"], row["decided_id"], row["owner_decision"] = {
                "3": ("route", "900000012", "link"), "4": ("area", "900000110", "link")}.get(
                row["accident_id"], ("", "", "no_link"))
        with out.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        assert run_on_conn(name, lambda c: run_r10_import(c, _ns(review_file=str(out), apply=True))) == {
            "link": 2, "no_link": 4, "applied": True}
        run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        links = run_on_conn(name, lambda c: c.execute(text(
            "SELECT accident_id, route_id, area_id, method FROM accident_route_links WHERE method = 'owner' ORDER BY 1")))
        assert links == [(3, 900000012, None, "owner"), (4, None, 900000110, "owner")]


@requires_pg
def test_import_refuses_a_link_to_an_unknown_id(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepare(name)
        run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        out = tmp_path / "r10.csv"
        run_on_conn(name, lambda c: run_r10_export(c, _ns(out=str(out))))
        with out.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        for row in rows:
            row["decided_kind"], row["decided_id"], row["owner_decision"] = ("route", "900000098", "link")
        with out.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        with pytest.raises(ValueError, match="not in the catalog"):
            run_on_conn(name, lambda c: run_r10_import(c, _ns(review_file=str(out), apply=True)))


@requires_pg
def test_a_changed_candidate_set_clears_the_llm_pick():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepare(name)
        run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        run_sql(name, "UPDATE internal.r10_unresolved SET llm_call_id = gen_random_uuid() WHERE accident_id IN (3, 4)")
        run_sql(name, "INSERT INTO mp_routes (mp_route_id, name, location_id, type, grade) "
                      "VALUES (900000013, 'Fixture Slab', 900000110, 'Trad', '5.7')")
        _prepare(name)
        run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        rows = run_on_conn(name, lambda c: c.execute(text(
            "SELECT accident_id, llm_call_id IS NULL FROM internal.r10_unresolved WHERE accident_id IN (3, 4) ORDER BY 1")))
        assert rows == [(3, True), (4, False)]


@requires_pg
def test_an_mp_route_id_missing_from_the_catalog_is_residue_not_dropped():
    with migrated_db(PIN, seed_sql=SEED) as name:
        _prepare(name)
        run_sql(name, "INSERT INTO mp_routes (mp_route_id, name, location_id, type, grade) VALUES "
                      "(900000013, 'Fixture Late', 900000110, 'Trad', '5.8');"
                      "INSERT INTO accidents (accident_id, mp_route_id) VALUES (10, 900000013)")
        out = run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        assert out["unresolved"]["not_in_catalog"] == 1
        assert fetch(name, "SELECT reason, candidates_searched FROM internal.r10_unresolved WHERE accident_id = 10") == [
            ("not_in_catalog", False)]
        _prepare(name)
        run_on_conn(name, lambda c: run_r10(c, _ns(apply=True)))
        assert fetch(name, "SELECT count(*) FROM internal.r10_unresolved WHERE accident_id = 10") == [(0,)]
        assert fetch(name, "SELECT route_id, method FROM accident_route_links WHERE accident_id = 10") == [
            (900000013, "mp_route_id")]
