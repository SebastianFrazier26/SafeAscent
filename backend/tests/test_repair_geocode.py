import argparse
import asyncio
import csv
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair import review
from app.data.repair.__main__ import STEPS
from app.data.repair.framework import LOCATION
from app.data.repair.geocode import (
    DENALI_GNIS_ID,
    KEEP,
    TRUSTED_POINT_PRECISIONS,
    GeoRow,
    SummitIndex,
    changes_for,
    find_fallback_points,
    foreign_country,
    geocode_fix,
    owner_review_changes,
    plot_stratum,
    review_candidates,
    run_r3,
    run_r3_ambiguous_export,
    run_r3_ambiguous_import,
    run_r3_clusters,
    run_r3_plot_export,
    run_r3_plot_import,
    run_r3a_export,
    run_r3a_import,
)
from app.pipelines.gnis import Summit
from tests.pgtest import migrated_db, requires_pg, sa_url

RAINIER = Summit(10, "Mount Rainier", "rainier", "WA", 46.853, -121.760)
# GNIS lists the Alaska summit under its official name; "Denali" reaches it only through the
# reviewed alias keyed by this feature id (Task 6 ruling).
MCKINLEY = Summit(DENALI_GNIS_ID, "Mount McKinley", "mckinley", "AK", 63.069, -151.007)
MCKINLEY_PEAK = Summit(1423626, "McKinley Peak", "mckinley", "AK", 61.0, -147.0)
BALDY_CO = Summit(12, "Bald Mountain", "bald", "CO", 39.4, -105.9)
BALDY_OR = Summit(13, "Bald Mountain", "bald", "OR", 44.6, -122.1)
INDEX = SummitIndex([RAINIER, MCKINLEY, BALDY_CO, BALDY_OR])


def row(i=1, family="aac", mountain=None, state=None, lat=None, lon=None, mp=None):
    return GeoRow(i, family, mountain, state, lat, lon, mp)


def test_r3a_moves_a_city_centroid_to_its_summit():
    fix = geocode_fix(row(mountain="Mt. Rainier", state="WA", lat=47.6, lon=-122.2), INDEX, set())
    assert (fix.location, fix.precision, fix.method, fix.country) == ((46.853, -121.760), "area", "gnis_summit", "US")


def test_mckinley_resolves_to_the_alaska_summit_by_name():
    fix = geocode_fix(row(mountain="Mount McKinley", lat=37.8, lon=-122.0), INDEX, set())
    assert fix.location == (63.069, -151.007)


def test_denali_reaches_the_summit_through_the_gnis_id_alias():
    fix = geocode_fix(row(mountain="Denali", lat=37.8, lon=-122.0), INDEX, set())
    assert (fix.location, fix.method) == ((63.069, -151.007), "gnis_summit")


def test_the_alias_needs_its_gnis_feature_loaded():
    other = SummitIndex([RAINIER, Summit(99, "Mount McKinley", "mckinley", "AK", 63.069, -151.007)])
    fix = geocode_fix(row(mountain="Denali", lat=37.8, lon=-122.0), other, set())
    assert (fix.location, fix.method) == (KEEP, "source_geocode")


def test_a_name_with_two_summits_in_its_state_is_ambiguous_and_never_moved():
    index = SummitIndex([MCKINLEY, MCKINLEY_PEAK])
    far = geocode_fix(row(mountain="Mount McKinley", state="AK", lat=61.2, lon=-149.9), index, set())
    assert (far.location, far.precision, far.method, far.country) == (KEEP, "unknown", "gnis_ambiguous", "US")
    near = geocode_fix(row(mountain="Mount McKinley", state="AK", lat=63.1, lon=-151.0), index, set())
    assert (near.location, near.precision, near.method) == (KEEP, "area", "source_geocode")
    # The alias names one feature, so "Denali" stays unique where "McKinley" is not.
    denali = geocode_fix(row(mountain="Denali", state="AK", lat=61.2, lon=-149.9), index, set())
    assert (denali.location, denali.method) == ((63.069, -151.007), "gnis_summit")


def test_r3a_leaves_a_close_row_to_r3():
    fix = geocode_fix(row(mountain="Mount Rainier", state="WA", lat=46.85, lon=-121.75), INDEX, set())
    assert (fix.location, fix.precision, fix.method) == (KEEP, "area", "source_geocode")


def test_ambiguous_name_far_from_all_candidates_is_nulled():
    # Spec R3a nulls this case explicitly; it is the one non-foreign null.
    fix = geocode_fix(row(mountain="Bald Mountain", lat=34.0, lon=-118.2), INDEX, set())
    assert (fix.location, fix.precision, fix.method, fix.country) == (None, "unknown", "gnis_ambiguous", None)


def test_state_from_a_bad_geocode_falls_back_to_a_unique_national_match():
    fix = geocode_fix(row(mountain="Mount McKinley", state="CA", lat=37.8, lon=-122.0), INDEX, set())
    assert (fix.location, fix.precision, fix.method) == ((63.069, -151.007), "area", "gnis_summit_state_mismatch")


def test_state_fallback_never_uses_a_non_unique_national_match():
    fix = geocode_fix(row(mountain="Bald Mountain", state="WA", lat=47.0, lon=-120.0), INDEX, set())
    assert (fix.location, fix.method) == (KEEP, "source_geocode")


def test_ambiguous_name_near_one_candidate_is_kept():
    fix = geocode_fix(row(mountain="Bald Mountain", lat=39.41, lon=-105.9), INDEX, set())
    assert fix.location is KEEP


def test_source_measured_points_are_never_moved_by_name():
    avalanche = geocode_fix(row(family="avalanche", mountain="Mount Rainier", state="WA", lat=47.6, lon=-122.2), INDEX, set())
    assert (avalanche.location, avalanche.precision) == (KEEP, "exact")
    linked = geocode_fix(row(mountain="Mount Rainier", state="WA", lat=47.6, lon=-122.2, mp=900000001), INDEX, set())
    assert (linked.location, linked.precision) == (KEEP, "crag")


def test_us_names_that_contain_foreign_words_stay_us():
    assert foreign_country("Organ Mountains", "New Mexico") is None
    assert foreign_country("Indian Peaks", "CO") is None
    # D5: also US summit names, so they never null a US row.
    assert foreign_country("Peru Peak", "VT") is None
    assert foreign_country("Matterhorn Peak", "California") is None
    assert foreign_country("Scotland", None) is None


def test_foreign_places_get_a_country_and_no_coordinates():
    assert foreign_country("Aconcagua", None) == "AR"
    fix = geocode_fix(row(mountain="Mount Robson, British Columbia", lat=53.1, lon=-119.2), INDEX, set())
    assert (fix.location, fix.precision, fix.country) == (None, "unknown", "CA")


def test_fallback_cluster_rows_become_region_fallback_with_state():
    rows = [row(i, mountain=f"Place {i}", state="CO", lat=39.739, lon=-104.990) for i in range(6)]
    points = find_fallback_points(rows)
    assert points == {(39.739, -104.99)}
    fix = geocode_fix(rows[0], INDEX, points)
    assert (fix.location, fix.precision, fix.method, fix.country) == (KEEP, "region_fallback", "fallback_state", "US")
    assert "region_fallback" not in TRUSTED_POINT_PRECISIONS


def test_a_point_needs_enough_rows_and_names_to_be_a_fallback():
    same_name = [row(i, mountain="Longs Peak", lat=40.255, lon=-105.615) for i in range(9)]
    few = [row(i, mountain=f"Place {i}", lat=39.0, lon=-105.0) for i in range(4)]
    assert find_fallback_points([*same_name, *few, row(99)]) == set()


def test_fallback_without_a_state_keeps_coordinates_and_is_unknown():
    rows = [row(i, mountain=f"Place {i}", lat=39.739, lon=-104.990) for i in range(6)]
    fix = geocode_fix(rows[0], INDEX, find_fallback_points(rows))
    assert (fix.location, fix.precision, fix.method, fix.country) == (KEEP, "unknown", "fallback_no_state", None)


def test_alaska_text_below_55n_is_fallback_and_keeps_its_point():
    fix = geocode_fix(row(mountain="Some Glacier", state="AK", lat=47.6, lon=-122.3), INDEX, set())
    assert (fix.precision, fix.location) == ("region_fallback", KEEP)


def test_only_foreign_rows_lose_coordinates():
    fix = geocode_fix(row(mountain="Fixture Crag", lat=55.0, lon=-100.0), INDEX, set())
    assert (fix.location, fix.method) == (None, "outside_us")


def test_plot_strata_follow_the_precision_label():
    assert plot_stratum({"geocode_precision": "region_fallback", "geocode_method": "fallback_state"}) == "region_fallback"
    assert plot_stratum({"geocode_precision": "area", "geocode_method": "gnis_summit_state_mismatch"}) == "gnis_move"


def test_source_defaults():
    assert geocode_fix(row(family="nps", lat=36.5, lon=-118.3), INDEX, set()).precision == "park_centroid"
    assert geocode_fix(row(family="avalanche", lat=39.6, lon=-105.9), INDEX, set()).precision == "exact"
    assert geocode_fix(row(mp=900000001, lat=40.0, lon=-105.3), INDEX, set()).precision == "crag"
    assert geocode_fix(row(lat=None, lon=None), INDEX, set()).method == "no_coords"


def test_changes_skip_location_when_kept():
    fix = geocode_fix(row(family="avalanche", lat=39.6, lon=-105.9), INDEX, set())
    assert [c.field for c in changes_for(fix)] == ["geocode_precision", "geocode_method", "country"]


def test_a_move_changes_the_location_first():
    fix = geocode_fix(row(mountain="Rainier", state="WA", lat=47.6, lon=-122.2), INDEX, set())
    assert [(c.field, c.new_value) for c in changes_for(fix)][0] == (LOCATION, (46.853, -121.760))


def test_review_candidates_are_the_summits_that_made_the_row_ambiguous():
    index = SummitIndex([MCKINLEY, MCKINLEY_PEAK, BALDY_CO, BALDY_OR])
    assert [s.gnis_id for s in review_candidates(row(mountain="McKinley", state="Alaska"), index)] == [
        DENALI_GNIS_ID, 1423626,
    ]
    assert [s.gnis_id for s in review_candidates(row(mountain="Bald Mountain"), index)] == [12, 13]


def test_owner_review_moves_only_to_a_listed_candidate():
    candidates = [MCKINLEY, MCKINLEY_PEAK]
    changes = owner_review_changes(5, str(DENALI_GNIS_ID), candidates)
    assert [(c.field, c.new_value, c.method) for c in changes] == [
        (LOCATION, (63.069, -151.007), "owner_summit"),
        ("geocode_precision", "area", "owner_summit"),
        ("geocode_method", "owner_summit", "owner_summit"),
        ("country", "US", "owner_summit"),
    ]
    assert owner_review_changes(5, "none", candidates) == []
    with pytest.raises(ValueError, match="not a candidate"):
        owner_review_changes(5, "12", candidates)


def test_cli_registers_the_geocode_steps():
    assert {
        "r3", "r3-clusters", "r3a-export", "r3a-import", "r3-plot-export", "r3-plot-import",
        "r3-ambiguous-export", "r3-ambiguous-import",
    } <= set(STEPS)


GEO_SEED = f"""
INSERT INTO gnis_summits (gnis_id, name, name_key, state_code, lat, lon) VALUES
  (10, 'Mount Rainier', 'rainier', 'WA', 46.853, -121.76),
  ({DENALI_GNIS_ID}, 'Mount McKinley', 'mckinley', 'AK', 63.069, -151.007),
  (1423626, 'McKinley Peak', 'mckinley', 'AK', 61.0, -147.0);
INSERT INTO accidents (accident_id, source, source_id, mountain, state, latitude, longitude, description) VALUES
  (1, 'AAC', 'a1', 'Mt. Rainier', 'WA', 47.6, -122.2, 'narrative one'),
  (2, 'AAC', 'a2', 'Aconcagua', NULL, 40.0, -105.0, 'narrative two'),
  (3, 'NPS', NULL, NULL, NULL, 36.5, -118.3, NULL),
  (4, 'AAC', 'a4', 'Mount McKinley', 'Alaska', 61.2, -149.9, NULL),
  (5, 'avalanche.org', NULL, 'Mount Rainier', 'WA', 47.6, -122.2, NULL),
  (6, 'AAC', 'a6', 'Denali', 'AK', 61.2, -149.9, NULL);
"""


async def _rows(conn, sql: str) -> list[tuple]:
    return [tuple(r) for r in await conn.execute(text(sql))]


@requires_pg
def test_r3_labels_moves_and_nulls_with_an_audit_and_a_rerun_changes_nothing():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                dry = await run_r3(conn, argparse.Namespace(apply=False))
                assert dry["mode"] == "dry_run" and dry["missing_alias_ids"] == []
                assert await _rows(conn, "SELECT count(*) FROM accidents WHERE geocode_precision IS NOT NULL") == [(0,)]
                clusters = await run_r3_clusters(conn, argparse.Namespace())
                assert clusters["fallback_points"] == 0
            async with engine.begin() as conn:
                first = await run_r3(conn, argparse.Namespace(apply=True))
            async with engine.begin() as conn:
                again = await run_r3(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                got = await _rows(
                    conn,
                    "SELECT accident_id, latitude, longitude, geocode_precision, geocode_method, country, "
                    "coordinates IS NULL FROM accidents ORDER BY accident_id",
                )
                audit = await _rows(
                    conn,
                    "SELECT accident_id, old_value, new_value FROM internal.accident_revisions "
                    "WHERE field = 'latlon' AND rule_version = 'r3-v1' ORDER BY accident_id",
                )
            assert got == [
                (1, 46.853, -121.76, "area", "gnis_summit", "US", False),
                (2, None, None, "unknown", "foreign_place", "AR", True),
                (3, 36.5, -118.3, "park_centroid", "nps_source", "US", False),
                (4, 61.2, -149.9, "unknown", "gnis_ambiguous", "US", False),
                (5, 47.6, -122.2, "exact", "source_coords", "US", False),
                (6, 63.069, -151.007, "area", "gnis_summit", "US", False),
            ]
            assert audit == [
                (1, "47.600000,-122.200000", "46.853000,-121.760000"),
                (2, "40.000000,-105.000000", None),
                (6, "61.200000,-149.900000", "63.069000,-151.007000"),
            ]
            assert first["methods"]["gnis_ambiguous"] == 1
            # Rows 1 and 6 now sit on their summits, so the rerun proposes no move for them, and
            # every label it proposes is already recorded under r3-v1: nothing changes.
            assert (first["applied"], again["applied"], again["unchanged"], again["already_recorded"]) == (21, 0, 0, 19)
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=GEO_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_r3_refuses_without_gnis_summits():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("DELETE FROM gnis_summits"))
                with pytest.raises(SystemExit, match="gnis_summits is empty"):
                    await run_r3(conn, argparse.Namespace(apply=False))
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=GEO_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


def _fill(path, column: str, values: dict[str, str]) -> None:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r[column] = values[r["accident_id"]]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@requires_pg
def test_r3a_check_exports_every_move_with_its_summit_and_records_the_agreement(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await run_r3(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                exported = await run_r3a_export(conn, argparse.Namespace(out=None, overwrite=False))
                with pytest.raises(FileExistsError, match="--overwrite"):
                    await run_r3a_export(conn, argparse.Namespace(out=None, overwrite=False))
            path = tmp_path / "r3a_moves.csv"
            assert exported["exported"] == 2
            with path.open(newline="", encoding="utf-8") as fh:
                rows = {r["accident_id"]: r for r in csv.DictReader(fh)}
            assert set(rows) == {"1", "6"}
            assert rows["6"]["summit"] == f"Mount McKinley (AK, GNIS {DENALI_GNIS_ID})"
            assert rows["1"]["previous_location"] == "47.600000,-122.200000"
            assert "narrative" not in path.read_text(encoding="utf-8")
            _fill(path, "correct", {"1": "yes", "6": "yes"})
            stray = tmp_path / "stray.csv"
            with path.open(newline="", encoding="utf-8") as fh:
                header = next(csv.reader(fh))
            stray.write_text(path.read_text(encoding="utf-8") + "4" + "," * (len(header) - 1) + "yes\n")
            async with engine.begin() as conn:
                with pytest.raises(ValueError, match="outside the hand-check pool"):
                    await run_r3a_import(conn, argparse.Namespace(review_file=str(stray)))
            async with engine.begin() as conn:
                out = await run_r3a_import(conn, argparse.Namespace(review_file=str(path)))
            assert out == {"source": "repair:r3a-check", "n": 2, "agreement": 1.0}
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=GEO_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_r3_plot_check_writes_matching_csv_and_geojson(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await run_r3(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                exported = await run_r3_plot_export(conn, argparse.Namespace(out=None, overwrite=False))
            csv_path, geojson_path = tmp_path / "r3_spot_check.csv", tmp_path / "r3_spot_check.geojson"
            assert (exported["csv"], exported["geojson"]) == (str(csv_path), str(geojson_path))
            with csv_path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            features = json.loads(geojson_path.read_text())["features"]
            # Every row with a point is in the pool (5 of 6; the foreign row was nulled).
            assert sorted(r["accident_id"] for r in rows) == ["1", "3", "4", "5", "6"]
            assert sorted(str(f["properties"]["accident_id"]) for f in features) == sorted(r["accident_id"] for r in rows)
            moved = next(f for f in features if f["properties"]["accident_id"] == 6)
            assert moved["geometry"]["coordinates"] == [-151.007, 63.069]
            assert "narrative" not in geojson_path.read_text()
            # The stale GeoJSON alone is enough to refuse: it may be what the owner has open.
            csv_path.unlink()
            async with engine.connect() as conn:
                with pytest.raises(FileExistsError, match="--overwrite"):
                    await run_r3_plot_export(conn, argparse.Namespace(out=None, overwrite=False))
                again = await run_r3_plot_export(conn, argparse.Namespace(out=None, overwrite=True))
            assert again["csv"] == str(csv_path)
            _fill(csv_path, "plausible", {r: "yes" if r != "4" else "no" for r in ("1", "3", "4", "5", "6")})
            async with engine.begin() as conn:
                out = await run_r3_plot_import(conn, argparse.Namespace(review_file=str(csv_path)))
            assert out == {"source": "repair:r3-plot-check", "n": 5, "agreement": 0.8}
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=GEO_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_ambiguous_rows_go_to_owner_review_and_only_a_listed_summit_is_applied(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await run_r3(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                exported = await run_r3_ambiguous_export(conn, argparse.Namespace(out=None, overwrite=False))
            path = tmp_path / "r3_ambiguous.csv"
            assert exported["exported"] == 1
            with path.open(newline="", encoding="utf-8") as fh:
                [only] = list(csv.DictReader(fh))
            assert only["accident_id"] == "4"
            assert only["candidates"] == (
                f"{DENALI_GNIS_ID} Mount McKinley AK 63.069,-151.007; 1423626 McKinley Peak AK 61.0,-147.0"
            )
            _fill(path, "gnis_id", {"4": "10"})
            async with engine.connect() as conn:
                with pytest.raises(ValueError, match="not a candidate"):
                    await run_r3_ambiguous_import(conn, argparse.Namespace(review_file=str(path), apply=False))
            _fill(path, "gnis_id", {"4": str(DENALI_GNIS_ID)})
            async with engine.connect() as conn:
                dry = await run_r3_ambiguous_import(conn, argparse.Namespace(review_file=str(path), apply=False))
            assert dry["mode"] == "dry_run"
            async with engine.begin() as conn:
                applied = await run_r3_ambiguous_import(conn, argparse.Namespace(review_file=str(path), apply=True))
            assert (applied["applied"], applied["unchanged"]) == (3, 1)
            async with engine.connect() as conn:
                got = await _rows(
                    conn,
                    "SELECT latitude, longitude, geocode_precision, geocode_method FROM accidents WHERE accident_id = 4",
                )
                with pytest.raises(ValueError, match="not pending review"):
                    await run_r3_ambiguous_import(conn, argparse.Namespace(review_file=str(path), apply=False))
            assert got == [(63.069, -151.007, "area", "owner_summit")]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=GEO_SEED) as name:
        asyncio.run(scenario(sa_url(name)))
