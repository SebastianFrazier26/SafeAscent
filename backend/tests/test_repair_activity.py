import argparse
import asyncio
import csv
import json
from collections import Counter

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair import activity, review
from app.data.repair.__main__ import STEPS
from app.data.repair.activity import (
    ActivityRow,
    R4Input,
    classify_activity,
    decided_elsewhere,
    is_blank,
    needs_review,
)
from tests.pgtest import migrated_db, requires_pg, sa_url

SUMMIT_TEXT = "they were on a summit attempt and switched to crampons"


def test_nps_only_rock_climbing_counts():
    assert classify_activity("nps", "rock_climbing", "", False) == ("climbing", None)
    assert classify_activity("nps", "Rock Climbing", "", False) == ("climbing", None)
    assert classify_activity("nps", "hiking", "", False) == ("non_climbing", None)


def test_avalanche_ice_climbing_is_climbing():
    assert classify_activity("avalanche", "ice_climbing", "", False) == ("climbing", None)


@pytest.mark.parametrize(
    "text,near,expected",
    [
        (SUMMIT_TEXT, True, ("climbing_approach", "ski_approach_best_effort")),
        (SUMMIT_TEXT, False, ("non_climbing", None)),
        ("climbing the skin track to the ridge", True, ("non_climbing", None)),
        ("a day of lift-served skiing", True, ("non_climbing", None)),
        # D1: a lexicon hit next to uphill-skinning phrasing is skinning, not climbing.
        ("summit attempt, skinned up the skin track", True, ("non_climbing", None)),
        # D5: phrases match from a word start, so "nice climb" is not "ice climb"...
        ("a nice climb to the col", True, ("non_climbing", None)),
        # ...while inflected forms still count.
        ("they rappelled into the couloir", True, ("climbing_approach", "ski_approach_best_effort")),
    ],
)
def test_ski_rows_need_the_lexicon_and_nearby_climbing_terrain(text, near, expected):
    assert classify_activity("avalanche", "backcountry skiing", text, near) == expected


def test_unmapped_activity_returns_none():
    assert classify_activity("aac", "paragliding", "", False) is None


@pytest.mark.parametrize("value", [None, "", "   ", "\t"])
def test_blank_activity_is_never_classified_even_for_nps(value):
    # O1: a blank activity goes to owner review; the NPS "everything else is non_climbing"
    # default would otherwise decide it with no evidence.
    assert is_blank(value)
    assert classify_activity("nps", value, "", False) is None
    assert classify_activity("aac", value, SUMMIT_TEXT, True) is None


@pytest.mark.parametrize(
    "family,value",
    [("avalanche", "unknown"), ("aac", "other"), ("aac", "Other"), ("nps", "UNKNOWN"), ("aac", "ski_mountaineering")],
)
def test_placeholder_values_and_aac_ski_mountaineering_go_to_owner_review(family, value):
    # Review round 1 (C1/I1/M1): placeholders carry no evidence either way, and an AAC
    # ski-mountaineering report is often a climbing objective, so the owner reads them.
    assert needs_review(family, value)
    assert classify_activity(family, value, SUMMIT_TEXT, True) is None


def test_ski_rows_outside_aac_keep_the_rule():
    assert not needs_review("avalanche", "backcountry_skiing")
    assert classify_activity("avalanche", "ski_mountaineering", "", False) == ("non_climbing", None)
    assert classify_activity("avalanche", "snowmobiling", "", False) == ("non_climbing", None)


def test_decided_elsewhere_lets_r4_redecide_only_its_own_rows():
    assert decided_elsewhere(None, []) is None
    assert decided_elsewhere("climbing", ["r4-v1"]) is None
    assert decided_elsewhere("climbing", ["r4-v1", "r4-v2"]) is None
    assert decided_elsewhere("climbing", ["r4-v1", "r4-review-v1"]) == "owner_decided"
    assert decided_elsewhere(None, ["r4-review-v1"]) == "owner_decided"
    # Set by hand (no revision) or last by another rule: not R4's to overwrite.
    assert decided_elsewhere("non_climbing", []) == "labelled_elsewhere"
    assert decided_elsewhere("climbing", ["r4-v1", "r11-v1"]) == "labelled_elsewhere"


def _input(*rows: tuple[int, str, str | None, str, bool], version: str = activity.R4_VERSION) -> R4Input:
    return R4Input([ActivityRow(*r) for r in rows], version, Counter())  # type: ignore[arg-type]


def test_unmapped_activity_blocks_apply(monkeypatch):
    async def fake_rows(conn):
        return _input((1, "aac", "paragliding", "", False))

    monkeypatch.setattr(activity, "_rows", fake_rows)
    with pytest.raises(SystemExit, match="paragliding"):
        asyncio.run(activity.run_r4(None, argparse.Namespace(apply=True)))  # type: ignore[arg-type]


def test_rule_version_follows_the_terrain_set(monkeypatch):
    seen = {}

    async def fake_rows(conn):
        return _input((1, "aac", "rock climbing", "", False), version=activity.R4_OBJECTIVES_VERSION)

    async def fake_run_step(conn, *, step, rule_version, changes, report, apply, metrics=None):
        seen["v"] = rule_version
        seen["fields"] = {c.field: c.new_value for c in changes}
        return {}

    monkeypatch.setattr(activity, "_rows", fake_rows)
    monkeypatch.setattr(activity, "run_step", fake_run_step)
    asyncio.run(activity.run_r4(None, argparse.Namespace(apply=False)))  # type: ignore[arg-type]
    assert seen["v"] == "r4-v2" and seen["fields"]["activity_rule_version"] == "r4-v2"


def test_blank_activity_rows_wait_for_review_and_never_block_apply(monkeypatch):
    seen = {}

    async def fake_rows(conn):
        return _input((1, "aac", None, SUMMIT_TEXT, True), (2, "nps", " ", "", False), (3, "aac", "ice climbing", "", False))

    async def fake_run_step(conn, *, step, rule_version, changes, report, apply, metrics=None):
        seen["ids"] = {c.accident_id for c in changes}
        seen["metrics"] = metrics
        seen["quarantined"] = dict(report.quarantined)
        return {"mode": "apply"}

    monkeypatch.setattr(activity, "_rows", fake_rows)
    monkeypatch.setattr(activity, "run_step", fake_run_step)
    out = asyncio.run(activity.run_r4(None, argparse.Namespace(apply=True)))  # type: ignore[arg-type]
    assert seen["ids"] == {3}
    assert seen["metrics"] == {"review_pending": 2}
    assert seen["quarantined"] == {"activity_pending_review": 2}
    assert out["review_pending"] == 2 and out["unmapped"] == {}


def test_placeholder_rows_are_pending_and_never_block_apply(monkeypatch):
    seen = {}

    async def fake_rows(conn):
        return _input((1, "avalanche", "unknown", "", False), (2, "aac", "other", "", False), (3, "aac", "ski_mountaineering", "", False))

    async def fake_run_step(conn, *, step, rule_version, changes, report, apply, metrics=None):
        seen["changes"] = list(changes)
        return {"mode": "apply"}

    monkeypatch.setattr(activity, "_rows", fake_rows)
    monkeypatch.setattr(activity, "run_step", fake_run_step)
    out = asyncio.run(activity.run_r4(None, argparse.Namespace(apply=True)))  # type: ignore[arg-type]
    assert seen["changes"] == [] and out["review_pending"] == 3 and out["unmapped"] == {}


def test_unmapped_values_are_reported_on_a_dry_run(monkeypatch):
    async def fake_rows(conn):
        return _input((1, "aac", "Paragliding", "", False), (2, "aac", "paragliding", "", False))

    async def fake_run_step(conn, **kwargs):
        return {"mode": "dry_run"}

    monkeypatch.setattr(activity, "_rows", fake_rows)
    monkeypatch.setattr(activity, "run_step", fake_run_step)
    out = asyncio.run(activity.run_r4(None, argparse.Namespace(apply=False)))  # type: ignore[arg-type]
    assert out["unmapped"] == {"aac:paragliding": 2}


def test_golden_strata_separate_the_rows_where_rules_fire():
    assert activity.golden_stratum({"family": "avalanche", "activity": "backcountry skiing", "outcome": "climbing_approach"}) == "ski_approach"
    assert activity.golden_stratum({"family": "avalanche", "activity": "snowmobiling", "outcome": "non_climbing"}) == "ski_other"
    assert activity.golden_stratum({"family": "nps", "activity": "hiking", "outcome": "non_climbing"}) == "nps"
    assert activity.golden_stratum({"family": "aac", "activity": "ice climbing", "outcome": "climbing"}) == "climbing"
    assert activity.golden_stratum({"family": "aac", "activity": "hiking", "outcome": "non_climbing"}) == "non_climbing"


def test_cli_registers_the_activity_steps():
    assert {"r4", "r4-golden-export", "r4-review-export", "r4-review-import"} <= set(STEPS)


# Terrain: one ice route at (40.0, -105.0). Row 1 is ~0.5 km from it, row 2 ~50 km away.
ACTIVITY_SEED = """
INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES
  (500, 'Ice Area', 40.0, -105.0), (501, 'Sport Area', 45.0, -110.0);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES
  (5000, 'Ice Line', 500, 'Ice, Mixed'), (5001, 'Sport Line', 501, 'Sport');
INSERT INTO accidents (accident_id, source, source_id, date, activity, description, latitude, longitude, geocode_precision) VALUES
  (1, 'avalanche.org', NULL, '2020-01-05', 'Backcountry Skiing', 'summit attempt, they put on crampons', 40.004, -105.0, 'exact'),
  (2, 'avalanche.org', NULL, '2020-01-06', 'Backcountry Skiing', 'summit attempt, they put on crampons', 40.45, -105.0, 'exact'),
  (3, 'NPS', NULL, '2015-07-01', 'rock_climbing', NULL, 36.5, -118.3, 'park_centroid'),
  (4, 'AAC', '101', '2012-06-01', NULL, 'narrative four', 46.8, -121.7, 'area'),
  (5, 'AAC', '102', '2013-02-01', 'Ice Climbing', 'narrative five', 44.0, -71.3, 'area'),
  (6, 'AAC', '103', '2014-02-01', 'Climbing', NULL, 44.0, -71.3, 'area'),
  -- Untrusted point at the ice area: never counts as near terrain.
  (8, 'avalanche.org', NULL, '2020-01-07', 'Backcountry Skiing', 'summit attempt, they put on crampons', 40.004, -105.0, 'region_fallback');
INSERT INTO internal.accidents_raw (accident_id, source, source_id, date, activity, latitude, longitude)
  SELECT accident_id, source, source_id, date, activity, latitude, longitude FROM accidents;
-- After the snapshot: a hand edit of row 5's live activity and a hand-set class on row 6,
-- plus a row loaded later (like plan 3's R11 rows).
UPDATE accidents SET activity = 'hiking' WHERE accident_id = 5;
UPDATE accidents SET activity_class = 'non_climbing' WHERE accident_id = 6;
INSERT INTO accidents (accident_id, source, activity) VALUES (7, 'AAC', 'Ice Climbing');
INSERT INTO source_ingest_log (run_id, source, status) VALUES (gen_random_uuid(), 'repair:r3', 'ok');
"""


async def _all(conn, sql: str) -> list[tuple]:
    return [tuple(r) for r in await conn.execute(text(sql))]


CLASSES_SQL = "SELECT accident_id, activity_class, activity_rule_version, inclusion_flag FROM accidents ORDER BY accident_id"


@requires_pg
def test_r4_classifies_from_the_snapshot_and_leaves_blank_activity_pending():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                dry = await activity.run_r4(conn, argparse.Namespace(apply=False))
                assert dry["mode"] == "dry_run"
                assert await _all(conn, "SELECT count(*) FROM accidents WHERE activity_class IS NOT NULL") == [(1,)]
            async with engine.begin() as conn:
                first = await activity.run_r4(conn, argparse.Namespace(apply=True))
            async with engine.begin() as conn:
                again = await activity.run_r4(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                got = await _all(conn, CLASSES_SQL)
                logged = await _all(
                    conn,
                    "SELECT validation_report->>'review_pending', validation_report->>'rule_version' "
                    "FROM source_ingest_log WHERE source = 'repair:r4' ORDER BY started_at",
                )
            assert got == [
                (1, "climbing_approach", "r4-v1", "ski_approach_best_effort"),
                (2, "non_climbing", "r4-v1", None),
                (3, "climbing", "r4-v1", None),
                (4, None, None, None),
                (5, "climbing", "r4-v1", None),
                (6, "non_climbing", None, None),
                (7, None, None, None),
                (8, "non_climbing", "r4-v1", None),
            ]
            assert first["terrain_version"] == "r4-v1" and first["review_pending"] == 1
            assert first["skipped"] == {"labelled_elsewhere": 1, "not_in_snapshot": 1}
            assert first["classes"] == {"climbing": 2, "climbing_approach": 1, "non_climbing": 2}
            assert (again["applied"], again["unchanged"], again["already_recorded"]) == (0, first["unchanged"], first["applied"])
            assert logged == [("1", "r4-v1"), ("1", "r4-v1")]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=ACTIVITY_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_r4_refuses_before_r3_has_labelled_the_points():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("DELETE FROM source_ingest_log"))
                with pytest.raises(SystemExit, match="apply r3 first"):
                    await activity.run_r4(conn, argparse.Namespace(apply=False))
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=ACTIVITY_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_objective_points_switch_the_rule_version_to_r4_v2():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                # Stand-in for plan 6's table: only the columns R4 reads. Row 2 sits ~0.4 km from it.
                await conn.execute(text("CREATE TABLE public.objectives (lat double precision, lon double precision)"))
                await conn.execute(text("INSERT INTO objectives VALUES (40.4465, -105.0), (NULL, NULL)"))
                out = await activity.run_r4(conn, argparse.Namespace(apply=False))
            assert out["terrain_version"] == "r4-v2" and out["rule_version"] == "r4-v2"
            assert out["classes"] == {"climbing": 2, "climbing_approach": 2, "non_climbing": 1}
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=ACTIVITY_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


def _fill(path, column: str, values: dict[str, str]) -> None:
    # Saved back with a BOM, as Excel's "CSV UTF-8" does.
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r[column] = values[r["accident_id"]]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _export_args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**({"out": None, "overwrite": False} | kw))


@requires_pg
def test_owner_review_classifies_blank_activity_rows_and_r4_never_redecides_them(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await activity.run_r4(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                exported = await activity.run_r4_review_export(conn, _export_args())
                with pytest.raises(FileExistsError):
                    await activity.run_r4_review_export(conn, _export_args())
            path = tmp_path / "r4_activity_review.csv"
            with path.open(newline="", encoding="utf-8") as fh:
                header = next(csv.reader(fh))
            assert exported["exported"] == 1
            assert "description" not in header and header[-1] == "activity_class"
            assert {"accident_id", "source", "source_id", "date"} <= set(header)

            stray = tmp_path / "stray.csv"
            stray.write_text("accident_id,activity_class\n5,climbing\n", encoding="utf-8")
            async with engine.connect() as conn:
                with pytest.raises(ValueError, match="outside the hand-check pool"):
                    await activity.run_r4_review_import(conn, argparse.Namespace(review_file=str(stray), apply=False))

            _fill(path, "activity_class", {"4": "Climbing"})
            filled = tmp_path / "r4_filled.csv"
            filled.write_bytes(path.read_bytes())
            async with engine.connect() as conn:
                dry = await activity.run_r4_review_import(conn, argparse.Namespace(review_file=str(path), apply=False))
                assert dry["mode"] == "dry_run"
                assert await _all(conn, "SELECT activity_class FROM accidents WHERE accident_id = 4") == [(None,)]
            async with engine.begin() as conn:
                applied = await activity.run_r4_review_import(conn, argparse.Namespace(review_file=str(path), apply=True))
            async with engine.begin() as conn:
                again = await activity.run_r4(conn, argparse.Namespace(apply=True))
            monkeypatch.setattr(activity, "R4_VERSION", "r4-v9")
            async with engine.connect() as conn:
                later = await activity.run_r4(conn, argparse.Namespace(apply=False))
                got = await _all(conn, CLASSES_SQL.replace("ORDER BY", "WHERE accident_id = 4 ORDER BY"))
                left = await activity.run_r4_review_export(conn, _export_args(overwrite=True))
            assert applied["applied"] == 2
            # M2: re-importing the same file is a no-op; changing a recorded decision is refused.
            async with engine.begin() as conn:
                reimport = await activity.run_r4_review_import(conn, argparse.Namespace(review_file=str(filled), apply=True))
            assert (reimport["applied"], reimport["already_decided"]) == (0, 1)
            _fill(filled, "activity_class", {"4": "non_climbing"})
            async with engine.connect() as conn:
                with pytest.raises(ValueError, match="already decided"):
                    await activity.run_r4_review_import(conn, argparse.Namespace(review_file=str(filled), apply=False))
            assert got == [(4, "climbing", "r4-review-v1", None)]
            assert again["review_pending"] == 0 and again["skipped"]["owner_decided"] == 1
            assert later["skipped"]["owner_decided"] == 1
            assert left["exported"] == 0
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=ACTIVITY_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_owner_none_leaves_the_row_pending_and_unknown_choices_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                await activity.run_r4_review_export(conn, _export_args())
            path = tmp_path / "r4_activity_review.csv"
            _fill(path, "activity_class", {"4": "maybe"})
            async with engine.connect() as conn:
                with pytest.raises(ValueError, match="maybe"):
                    await activity.run_r4_review_import(conn, argparse.Namespace(review_file=str(path), apply=True))
            _fill(path, "activity_class", {"4": "none"})
            async with engine.begin() as conn:
                out = await activity.run_r4_review_import(conn, argparse.Namespace(review_file=str(path), apply=True))
            async with engine.connect() as conn:
                pending = await activity.run_r4_review_export(conn, _export_args(overwrite=True))
            assert out["applied"] == 0 and out["review_pending"] == 1
            assert pending["exported"] == 1
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=ACTIVITY_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_golden_export_hides_the_decision_and_refuses_unmapped_vocabulary(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                out = await activity.run_r4_golden_export(conn, _export_args())
            path = tmp_path / "activity_golden_candidates.csv"
            with path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            # Rows 1, 2, 3, 5 and 8: the pending row 4, hand-set row 6 and post-snapshot row 7 stay out.
            assert sorted(int(r["accident_id"]) for r in rows) == [1, 2, 3, 5, 8]
            assert list(rows[0]) == ["accident_id", "source", "source_id", "date", "activity", "label"]
            assert all(r["label"] == "" for r in rows)
            assert out["strata"]["ski_approach"] == 1 and out["strata"]["nps"] == 1
            assert "description" not in json.dumps(out)
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE internal.accidents_raw SET activity = 'paragliding' WHERE accident_id = 5"))
            async with engine.connect() as conn:
                with pytest.raises(SystemExit, match="paragliding"):
                    await activity.run_r4_golden_export(conn, _export_args(overwrite=True))
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=ACTIVITY_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


PLACEHOLDER_SEED = ACTIVITY_SEED + """
INSERT INTO accidents (accident_id, source, source_id, activity, latitude, longitude, geocode_precision) VALUES
  (9, 'avalanche.org', NULL, 'unknown', 40.004, -105.0, 'exact'),
  (10, 'AAC', '110', 'Other', 44.0, -71.3, 'area'),
  (11, 'AAC', '111', 'Ski Mountaineering', 40.004, -105.0, 'area');
INSERT INTO internal.accidents_raw (accident_id, source, source_id, activity)
  SELECT accident_id, source, source_id, activity FROM accidents WHERE accident_id IN (9, 10, 11);
"""


@requires_pg
def test_placeholder_and_aac_ski_mountaineering_rows_are_pending_and_exported(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                out = await activity.run_r4(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                exported = await activity.run_r4_review_export(conn, _export_args())
                golden = await activity.run_r4_golden_export(conn, _export_args())
                got = await _all(conn, "SELECT activity_class FROM accidents WHERE accident_id IN (9, 10, 11)")
            with (tmp_path / "r4_activity_review.csv").open(newline="", encoding="utf-8") as fh:
                ids = sorted(int(r["accident_id"]) for r in csv.DictReader(fh))
            with (tmp_path / "activity_golden_candidates.csv").open(newline="", encoding="utf-8") as fh:
                drawn = {int(r["accident_id"]) for r in csv.DictReader(fh)}
            assert out["review_pending"] == 4 and out["unmapped"] == {}
            assert got == [(None,), (None,), (None,)]
            assert exported["exported"] == 4 and ids == [4, 9, 10, 11]
            assert not drawn & {4, 9, 10, 11} and sum(golden["strata"].values()) == 5
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=PLACEHOLDER_SEED) as name:
        asyncio.run(scenario(sa_url(name)))
