import argparse
import asyncio
import csv
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair import dedupe, review
from app.data.repair.__main__ import STEPS
from app.data.repair.dedupe import (
    DupRow,
    band,
    candidate_pairs,
    canonical,
    date_interval,
    exact_groups,
    group_changes,
    groups,
    interval_gap_days,
    next_version,
    owned_by_r5,
    pair_score,
    resolve_merges,
    row_interval,
    rule_version,
    untrusted_pairs,
)
from app.data.repair.framework import apply_changes
from tests.pgtest import migrated_db, requires_pg, sa_url


def r(i, d=date(2010, 7, 1), lat=40.0, lon=-105.0, typ="fall", sev="serious", place="Fixture Peak",
      fam="aac", dp="day", gp="area", key=None, **kw):
    return DupRow(i, fam, d, lat, lon, typ, sev, place, dp, gp, key or (i,), **kw)


def test_identical_facts_score_1_and_far_apart_rows_are_not_candidates():
    assert pair_score(r(1), r(2)) == 1.0
    rows = [r(1), r(2, d=date(2010, 7, 5)), r(3, lat=40.2)]
    assert [(a, b) for a, b, _ in candidate_pairs(rows)] == []


def test_unknown_components_score_half():
    assert pair_score(r(1, typ=None, sev=None), r(2, typ=None, sev=None)) == pytest.approx(0.85)


def test_unknown_distance_scores_half_not_zero():
    # D6: decision 7 says an unknown component scores 0.5; it matters once O2 pairs untrusted points.
    assert pair_score(r(1, gp="region_fallback"), r(2)) == pytest.approx(1.0 - 0.25 * 0.5)


def test_bands():
    assert (band(0.95), band(0.40), band(0.41), band(0.89)) == ("merge", "distinct", "review", "review")


def test_exact_groups_are_within_source_only():
    rows = [r(1, key=("k",)), r(2, key=("k",)), r(3, key=("k",), fam="nps")]
    assert exact_groups(rows) == [[1, 2]]


def test_groups_are_transitive():
    assert groups([1, 2, 3, 4], [(1, 2), (2, 3)]) == [[1, 2, 3]]


def test_canonical_precedence_day_then_geocode_then_aac_then_lowest_id():
    assert canonical([r(5, dp="month"), r(9, dp="day")]) == 9
    assert canonical([r(5, gp="region_fallback"), r(9, gp="exact")]) == 9
    assert canonical([r(5, fam="nps"), r(9, fam="aac")]) == 9
    assert canonical([r(9), r(5)]) == 5


def test_new_owner_decision_produces_a_new_rule_version():
    a = rule_version({(1, 2): "merge"})
    b = rule_version({(1, 2): "distinct"})
    assert a != b and a.startswith("r5-v1+") and rule_version({(1, 2): "merge"}) == a


def test_rule_version_follows_the_group_heads_too():
    # Same decisions, but R3 relabelled a point so the other row now heads the group.
    assert rule_version({(1, 2): "merge"}, heads={1: 1, 2: 1}) != rule_version({(1, 2): "merge"}, heads={1: 2, 2: 2})


def test_a_returning_outcome_gets_a_new_generation_and_a_rerun_reuses_the_latest():
    assert next_version("r5-v1+a", []) == "r5-v1+a"
    assert next_version("r5-v1+a", ["r5-v1+a"]) == "r5-v1+a"
    # A -> B -> A: A's revision keys already exist, so reusing "r5-v1+a" would skip every row.
    assert next_version("r5-v1+a", ["r5-v1+a", "r5-v1+b"]) == "r5-v1+a.2"
    assert next_version("r5-v1+a", ["r5-v1+a", "r5-v1+b", "r5-v1+a.2"]) == "r5-v1+a.2"
    assert next_version("r5-v1+a", ["r5-v1+a", "r5-v1+b", "r5-v1+a.2", "r5-v1+b.2"]) == "r5-v1+a.3"


def test_date_intervals_follow_precision():
    assert date_interval(date(2010, 7, 15), "month") == (date(2010, 7, 1), date(2010, 7, 31))
    assert date_interval(date(2010, 7, 1), "year") == (date(2010, 1, 1), date(2010, 12, 31))
    assert date_interval(date(2010, 7, 3), "day") == (date(2010, 7, 3), date(2010, 7, 3))
    assert date_interval(date(2010, 7, 3), "unknown") is None


def test_neighbour_year_ranges_use_year_lo_to_year_hi():
    ranged = r(1, d=date(2003, 7, 1), dp="year", year_lo=2002, year_hi=2004, year_source="aac_source_id_neighbours")
    day = r(2, d=date(2004, 11, 2), fam="nps")
    assert row_interval(ranged) == (date(2002, 1, 1), date(2004, 12, 31))
    assert interval_gap_days(row_interval(ranged), row_interval(day)) == 0
    assert [(a, b) for a, b, _ in candidate_pairs([ranged, day])] == [(1, 2)]
    # A neighbour row without its range has no usable interval: never paired on its point year.
    assert row_interval(r(3, dp="year", year_source="aac_source_id_neighbours")) is None


def test_month_precision_row_pairs_with_a_day_record_in_the_same_month():
    aac = r(1, d=date(2010, 7, 15), dp="month")
    nps = r(2, d=date(2010, 7, 3), fam="nps", dp="day", gp="park_centroid")
    [(lo, hi, score)] = candidate_pairs([aac, nps])
    assert (lo, hi) == (1, 2)
    assert score < pair_score(r(3), r(4)) and band(score) == "review"


def test_untrusted_points_never_make_a_pair():
    a = r(1, gp="region_fallback")
    b = r(2, gp="region_fallback")
    assert candidate_pairs([a, b]) == []


def test_untrusted_rows_pair_on_date_state_and_text_evidence():
    fallback = r(1, gp="region_fallback", place="sneffels", state="CO", names=("sneffels",))
    nps = r(2, d=date(2010, 7, 2), fam="nps", lat=38.0, lon=-107.8, place="sneffels", state="CO")
    other_state = r(3, gp="region_fallback", place="sneffels", state="UT")
    unrelated = r(4, gp="unknown", place="grays", state="CO", narrative="avalanche on the east face")
    far_in_time = r(5, d=date(2010, 7, 9), gp="unknown", place="sneffels", state="CO")
    got = untrusted_pairs([fallback, nps, other_state, unrelated, far_in_time])
    assert [(a, b, basis) for a, b, _, basis in got] == [(1, 2, "place_name")]


def test_untrusted_pairs_accept_a_place_mentioned_in_the_other_narrative():
    aac = r(1, gp="region_fallback", place="", state=None, names=("capitol",),
            narrative="the party was descending the knife edge")
    nps = r(2, fam="nps", place="", state="CO", narrative="a climber fell from the knife edge on capitol peak")
    [(lo, hi, _, basis)] = untrusted_pairs([aac, nps])
    assert (lo, hi, basis) == (1, 2, "place_mention")


def test_untrusted_pairs_accept_similar_narratives_but_not_generic_overlap():
    words = "ranger rescue helicopter hoisted injured scrambler below saddle loose gully"
    a = r(1, gp="unknown", place="", state="WA", narrative=words)
    b = r(2, fam="nps", gp="unknown", place="", state="WA", narrative=words + " evening")
    c = r(3, gp="unknown", place="", state="WA", narrative="the climber fell and the rope caught the fall")
    d = r(4, fam="nps", gp="unknown", place="", state="WA", narrative="a climber fell when the rope was cut")
    assert [(x, y, basis) for x, y, _, basis in untrusted_pairs([a, b])] == [(1, 2, "narrative")]
    assert untrusted_pairs([c, d]) == []


def test_trusted_pairs_stay_on_the_distance_rule():
    # Both points trusted: the 5 km rule decides alone, so a far pair is not rescued by text.
    a = r(1, place="sneffels", state="CO")
    b = r(2, lat=41.0, place="sneffels", state="CO")
    assert untrusted_pairs([a, b]) == [] and candidate_pairs([a, b]) == []


def test_group_changes_reset_rows_that_left_a_group():
    by_id = {i: r(i) for i in (1, 2, 3)}
    merged = {(c.accident_id, c.field): c.new_value for c in group_changes(by_id, [(1, 2)])}
    assert merged[(1, "incident_group_id")] == 1 and merged[(2, "is_canonical")] is False
    assert merged[(3, "incident_group_id")] is None and merged[(3, "is_canonical")] is True
    split = {(c.accident_id, c.field): c.new_value for c in group_changes(by_id, [])}
    assert all(split[(i, "incident_group_id")] is None and split[(i, "is_canonical")] is True for i in (1, 2, 3))


def test_owner_distinct_withholds_auto_merges_that_would_join_the_pair():
    decided = {(1, 2): ("merge", "auto"), (2, 3): ("merge", "auto"), (1, 3): ("distinct", "owner"), (4, 5): ("merge", "auto")}
    merges, withheld = resolve_merges([1, 2, 3, 4, 5], decided)
    assert merges == [(4, 5)] and withheld == [(1, 2), (2, 3)]


def test_auto_distinct_inside_a_transitive_auto_group_sends_the_merges_to_the_owner():
    decided = {(1, 2): ("merge", "auto"), (2, 3): ("merge", "auto"), (1, 3): ("distinct", "auto")}
    assert resolve_merges([1, 2, 3], decided) == ([], [(1, 2), (2, 3)])


def test_owner_merges_override_an_auto_distinct():
    decided = {(1, 2): ("merge", "owner"), (2, 3): ("merge", "owner"), (1, 3): ("distinct", "auto")}
    assert resolve_merges([1, 2, 3], decided) == ([(1, 2), (2, 3)], [])


def test_contradictory_owner_decisions_are_refused():
    decided = {(1, 2): ("merge", "owner"), (2, 3): ("merge", "owner"), (1, 3): ("distinct", "owner")}
    with pytest.raises(ValueError, match="owner decisions contradict"):
        resolve_merges([1, 2, 3], decided)


def test_r5_owns_only_rows_it_last_labelled():
    assert owned_by_r5(True, None, [])
    assert owned_by_r5(False, 4, ["r5-v1+abc"])
    assert not owned_by_r5(False, None, [])
    assert not owned_by_r5(True, None, ["r5-v1+abc", "r11-v1"])


def test_cli_registers_the_dedupe_steps():
    assert {"r5", "r5-import"} <= set(STEPS)


SEED = """
INSERT INTO accidents (accident_id, source, date, latitude, longitude) VALUES
  (1, 'AAC', '2010-07-02', 40.0, -105.0),
  (2, 'AAC', '2010-07-02', 40.0, -105.0);
"""


@requires_pg
def test_flip_to_distinct_restores_canonical_rows():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        by_id = {1: r(1), 2: r(2)}
        try:
            async with engine.begin() as conn:
                await apply_changes(conn, group_changes(by_id, [(1, 2)]),
                                    rule_version=rule_version({(1, 2): "merge"}), run_id=uuid.uuid4())
            async with engine.begin() as conn:
                await apply_changes(conn, group_changes(by_id, []),
                                    rule_version=rule_version({(1, 2): "distinct"}), run_id=uuid.uuid4())
            async with engine.connect() as conn:
                rows = (await conn.execute(text(
                    "SELECT accident_id, incident_group_id, is_canonical FROM accidents ORDER BY 1"))).all()
            assert [tuple(x) for x in rows] == [(1, None, True), (2, None, True)]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        asyncio.run(scenario(sa_url(name)))


# 1/2 exact duplicates; 3/4 a same-day cross-source auto merge; 5/6 a month row against a day
# record (review band); 7/8 an untrusted AAC point and an NPS record of the same summit and
# state (O2: review); 10 and 11 share the window with 7/8 but lack the evidence or the state;
# 9 was marked non-canonical outside R5.
R5_SEED = """
INSERT INTO accidents (accident_id, source, source_id, date, latitude, longitude, accident_type, injury_severity,
                       mountain, route, state, description, date_precision, geocode_precision, activity) VALUES
  (1, 'AAC', '1', '2010-07-02', 40.0, -105.0, 'fall', 'serious', 'Fixture Peak', NULL, 'CO', 'same words', 'day', 'area', 'rock_climbing'),
  (2, 'AAC', '1b', '2010-07-02', 40.0, -105.0, 'fall', 'serious', 'Fixture Peak', NULL, 'CO', 'same words', 'day', 'area', 'rock_climbing'),
  (3, 'AAC', '3', '2011-08-10', 41.0, -106.0, 'fall', 'fatal', 'Longs Peak', NULL, 'CO', 'alpha text', 'day', 'exact', 'mountaineering'),
  (4, 'NPS', NULL, '2011-08-10', 41.001, -106.0, 'fall', 'fatal', 'Longs Peak', NULL, 'CO', 'bravo text', 'day', 'park_centroid', 'hiking'),
  (5, 'AAC', '5', '2012-06-15', 42.0, -107.0, 'fall', 'serious', 'Fixture Spire', NULL, 'WY', 'charlie text', 'month', 'area', 'rock_climbing'),
  (6, 'NPS', NULL, '2012-06-03', 42.0, -107.0, 'fall', 'serious', 'Fixture Spire', NULL, 'WY', 'delta text', 'day', 'park_centroid', 'rock_climbing'),
  (7, 'AAC', '7', '2013-05-04', 39.7, -105.0, 'fall', 'serious', 'Mount Sneffels', NULL, 'Colorado', 'echo text', 'day', 'region_fallback', 'mountaineering'),
  (8, 'NPS', NULL, '2013-05-05', 38.0, -107.8, 'fall', 'serious', 'Sneffels', NULL, 'CO', 'foxtrot text', 'day', 'exact', 'hiking'),
  (9, 'AAC', '9', '2014-01-01', 40.0, -105.0, 'fall', 'serious', 'Fixture Peak', NULL, 'CO', 'golf text', 'day', 'area', 'rock_climbing'),
  (10, 'avalanche.org', NULL, '2013-05-05', 39.7, -105.0, 'avalanche', 'fatal', 'Grays Peak', NULL, 'CO', 'hotel text', 'day', 'region_fallback', 'backcountry_skiing'),
  (11, 'AAC', '11', '2013-05-04', 40.7, -111.9, 'fall', 'serious', 'Sneffels', NULL, 'UT', 'india text', 'day', 'region_fallback', 'mountaineering');
UPDATE accidents SET is_canonical = false WHERE accident_id = 9;
INSERT INTO source_ingest_log (run_id, source, status) VALUES (gen_random_uuid(), 'repair:r3', 'ok');
"""

GROUPS_SQL = "SELECT accident_id, incident_group_id, is_canonical FROM accidents ORDER BY accident_id"


async def _all(conn, sql: str) -> list[tuple]:
    return [tuple(x) for x in await conn.execute(text(sql))]


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**({"apply": False, "out": None, "overwrite": False, "review_file": None} | kw))


def _fill(path, values: dict[tuple[str, str], str]) -> None:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["decision"] = values[(row["low_id"], row["high_id"])]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _decisions_file(path, rows: list[tuple[int, int, str]]):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["low_id", "high_id", "score", "decision"])
        for lo, hi, d in rows:
            writer.writerow([lo, hi, 0.5, d])
    return path


@requires_pg
def test_r5_round_trip_auto_bands_owner_review_flip_and_flip_back(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)
    out = tmp_path / "duplicates.csv"

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                dry = await dedupe.run_r5(conn, _args())
                assert dry["mode"] == "dry_run" and dry["review_pairs"] == 2
                assert await _all(conn, "SELECT count(*) FROM internal.duplicate_decisions") == [(0,)]
            assert not out.exists()

            async with engine.begin() as conn:
                first = await dedupe.run_r5(conn, _args(apply=True))
            async with engine.begin() as conn:
                again = await dedupe.run_r5(conn, _args(apply=True, overwrite=True))
            async with engine.connect() as conn:
                after_first = await _all(conn, GROUPS_SQL)
                auto = await _all(
                    conn,
                    "SELECT low_id, high_id, decision, decided_by, run_id::text FROM internal.duplicate_decisions ORDER BY 1, 2",
                )
            assert first["review_pending"] == 2 and first["skipped"] == {"labelled_elsewhere": 1}
            assert (again["applied"], again["rule_version"]) == (0, first["rule_version"])
            assert after_first == [
                (1, 1, True), (2, 1, False), (3, 3, True), (4, 3, False), (5, None, True), (6, None, True),
                (7, None, True), (8, None, True), (9, None, False), (10, None, True), (11, None, True),
            ]
            # D13: auto decisions carry the latest run, so a spot check can sample that run alone.
            assert [a[:4] for a in auto] == [(1, 2, "merge", "auto"), (3, 4, "merge", "auto")]
            assert {a[4] for a in auto} == {again["run_id"]}

            with out.open(newline="", encoding="utf-8") as fh:
                exported = list(csv.DictReader(fh))
            assert "description" not in exported[0] and list(exported[0])[-1] == "decision"
            assert [(e["low_id"], e["high_id"], e["basis"]) for e in exported] == [("5", "6", "distance"), ("7", "8", "place_name")]
            assert {e["source_id_a"] for e in exported} == {"5", "7"}
            assert not any("text" in v for e in exported for v in e.values())

            stray = _decisions_file(tmp_path / "stray.csv", [(7, 10, "merge")])
            async with engine.connect() as conn:
                with pytest.raises(ValueError, match="not a pair R5 scores"):
                    await dedupe.run_r5_import(conn, _args(review_file=str(stray), apply=True))

            _fill(out, {("5", "6"): "Merge", ("7", "8"): "distinct"})
            async with engine.begin() as conn:
                imported = await dedupe.run_r5_import(conn, _args(review_file=str(out), apply=True))
            async with engine.begin() as conn:
                reimported = await dedupe.run_r5_import(conn, _args(review_file=str(out), apply=True))
            async with engine.begin() as conn:
                decided = await dedupe.run_r5(conn, _args(apply=True))
            async with engine.connect() as conn:
                after_owner = await _all(conn, GROUPS_SQL)
            assert imported["imported"] == 2 and reimported == imported | {"imported": 0, "unchanged": 2}
            assert decided["review_pending"] == 0 and decided["review_pairs"] == 0
            assert after_owner[4:6] == [(5, 6, False), (6, 6, True)]

            flip = _decisions_file(tmp_path / "flip.csv", [(5, 6, "distinct")])
            async with engine.connect() as conn:
                with pytest.raises(ValueError, match="already decided differently"):
                    await dedupe.run_r5_import(conn, _args(review_file=str(flip), apply=True))
            async with engine.begin() as conn:
                await dedupe.run_r5_import(conn, _args(review_file=str(flip), apply=True, overwrite=True))
                await dedupe.run_r5(conn, _args(apply=True))
            async with engine.connect() as conn:
                flipped = await _all(conn, GROUPS_SQL)
            assert flipped[4:6] == [(5, None, True), (6, None, True)]

            back = _decisions_file(tmp_path / "back.csv", [(5, 6, "merge")])
            async with engine.begin() as conn:
                await dedupe.run_r5_import(conn, _args(review_file=str(back), apply=True, overwrite=True))
                back_run = await dedupe.run_r5(conn, _args(apply=True))
            async with engine.connect() as conn:
                restored = await _all(conn, GROUPS_SQL)
                logged = await _all(
                    conn,
                    "SELECT validation_report->>'review_pending' FROM source_ingest_log "
                    "WHERE source = 'repair:r5' ORDER BY finished_at",
                )
            assert back_run["rule_version"] == decided["rule_version"] + ".2"
            assert restored == after_owner
            assert logged == [("2",), ("2",), ("0",), ("0",), ("0",)]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=R5_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_r5_refuses_before_r3_and_import_refuses_blank_or_unscored_pairs(tmp_path):
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            blank = _decisions_file(tmp_path / "blank.csv", [(5, 6, "")])
            async with engine.connect() as conn:
                with pytest.raises(ValueError, match="decision must be merge or distinct"):
                    await dedupe.run_r5_import(conn, _args(review_file=str(blank)))
            unscored = _decisions_file(
                tmp_path / "unscored.csv", [(1, 3, "distinct")]
            )
            async with engine.connect() as conn:
                with pytest.raises(ValueError, match="not a pair R5 scores"):
                    await dedupe.run_r5_import(conn, _args(review_file=str(unscored)))
                await conn.execute(text("DELETE FROM source_ingest_log"))
                with pytest.raises(SystemExit, match="apply r3 first"):
                    await dedupe.run_r5(conn, _args())
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=R5_SEED) as name:
        asyncio.run(scenario(sa_url(name)))
