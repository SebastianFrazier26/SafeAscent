import argparse
import asyncio
import csv
import random
import statistics
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair import review
from app.data.repair.__main__ import STEPS
from app.data.repair.dates import (
    AacRow,
    changes_for,
    eight_digit_scheme_holds,
    r1_backtest,
    r1_year_fixes,
    r1v2_year_fixes,
    robust_bounds,
    run_r1v2,
    run_r1v2_backtest,
    run_r2r1,
    v2_bounds,
)
from app.data.repair.severity import (
    LOCATOR_COLUMNS,
    run_r1_export,
    run_r1v2_export,
    run_r1v2_import,
)
from tests.pgtest import migrated_db, requires_pg, sa_url


def _trusted(start_key: int, years: list[int]) -> list[AacRow]:
    return [AacRow(1000 + i, (start_key + i, ""), date(y, 5, 2), y) for i, y in enumerate(years)]


def _suspect(key: int, d: date | None = date(2023, 5, 2), year: int = 2023, accident_id: int = 1) -> AacRow:
    return AacRow(accident_id, (key, ""), d, year)


def _eight(n: int, years: list[int]) -> list[AacRow]:
    return [AacRow(5000 + i, None, date(y, 4, 9), y, True) for i, y in enumerate(years[:n])]


def test_bounds_are_type_7_deciles_floored_and_ceiled_and_the_median_floors():
    rng = random.Random(7)
    for _ in range(3000):
        years = [rng.randint(1985, 2018) for _ in range(rng.randint(10, 20))]
        q = statistics.quantiles(years, n=10, method="inclusive")
        lo, hi, point = robust_bounds(years)
        assert (lo, hi) == (int(q[0] // 1), int(-(-q[-1] // 1)))
        assert point == int(statistics.median(years) // 1)
        assert lo <= point <= hi


def test_bound_rounds_outwards_so_an_interpolated_decile_widens_the_range():
    # P10 sits at rank 1.9 (2002.9) and P90 at rank 17.1 (2003.1): floor and ceil give
    # 2002..2004, where rounding to nearest would claim a single year.
    years = [2002, 2002] + [2003] * 16 + [2004, 2006]
    assert robust_bounds(years) == (2002, 2004, 2003)
    assert v2_bounds(years) == (2002, 2004, 2003)


def test_range_of_exactly_two_is_accepted_and_three_is_not():
    assert v2_bounds([2002] * 10 + [2004] * 10) == (2002, 2004, 2003)
    assert v2_bounds([2001] * 10 + [2004] * 10) is None


def test_one_out_of_order_neighbour_no_longer_voids_the_row():
    rows = _trusted(0, [2003] * 9 + [2005]) + [_suspect(10)] + _trusted(11, [2003] * 10)
    [v1] = r1_year_fixes(rows)
    assert (v1.excluded_reason, v1.year_lo, v1.year_hi) == ("year_unverified", None, None)
    [fix] = r1v2_year_fixes(rows)
    assert (fix.year, fix.year_lo, fix.year_hi, fix.date_precision, fix.new_date, fix.excluded_reason) == (
        2003, 2003, 2003, "day", date(2003, 5, 2), None
    )


def test_one_stray_year_no_longer_trips_the_spread_gate():
    rows = _trusted(0, [2001, 2002] + [2003] * 8) + [_suspect(10)] + _trusted(11, [2003] * 9 + [2008])
    assert r1_year_fixes(rows)[0].excluded_reason == "year_unverified"
    [fix] = r1v2_year_fixes(rows)
    assert (fix.year, fix.year_lo, fix.year_hi, fix.date_precision) == (2003, 2002, 2003, "year")
    assert fix.new_date == date(2003, 7, 1)


def test_a_row_r1_v1_dates_is_left_to_r1_v1():
    rows = _trusted(0, [2002] * 5 + [2003] * 5) + [_suspect(10)] + _trusted(11, [2003] * 5 + [2004] * 5)
    assert r1_year_fixes(rows)[0].excluded_reason is None
    assert r1v2_year_fixes(rows) == []


def test_a_wide_neighbourhood_stays_unverified():
    rows = _trusted(0, [1995] * 10) + [_suspect(10)] + _trusted(11, [2004] * 10)
    assert r1v2_year_fixes(rows) == []


def test_min_side_still_applies():
    rows = _trusted(0, [2003] * 9 + [2005]) + [_suspect(10)] + _trusted(11, [2003] * 4)
    assert r1v2_year_fixes(rows) == []
    rows = _trusted(0, [2003] * 9 + [2005]) + [_suspect(10)] + _trusted(11, [2003] * 5)
    assert len(r1v2_year_fixes(rows)) == 1


def test_unparseable_ids_stay_unverified():
    rows = _trusted(0, [2003] * 20) + [AacRow(1, None, date(2023, 5, 2), 2023)]
    assert r1v2_year_fixes(rows) == []


def test_eight_digit_suspect_is_2018_within_2017_2018():
    rows = _trusted(0, [2003] * 20) + _eight(10, [2018] * 10) + [AacRow(2, None, date(2023, 3, 4), 2023, True)]
    assert r1_year_fixes(rows)[-1].excluded_reason == "year_unverified"
    [fix] = r1v2_year_fixes(rows)
    assert (fix.accident_id, fix.year, fix.year_lo, fix.year_hi, fix.date_precision, fix.new_date) == (
        2, 2018, 2017, 2018, "year", date(2018, 7, 1)
    )


def test_eight_digit_suspect_without_a_date_is_dated_too():
    rows = _eight(10, [2018] * 10) + [AacRow(2, None, None, 2021, True)]
    [fix] = r1v2_year_fixes(rows)
    assert (fix.year, fix.new_date) == (2018, date(2018, 7, 1))


@pytest.mark.parametrize(
    "trusted, holds",
    [([2018] * 10, True), ([2017] * 3 + [2018] * 7, True), ([2018] * 9, False),
     ([2016] + [2018] * 12, False), ([2018] * 12 + [2019 - 1, 2015], False)],
)
def test_eight_digit_rule_applies_only_while_the_snapshot_supports_it(trusted, holds):
    rows = _eight(len(trusted), trusted) + [AacRow(2, None, date(2023, 3, 4), 2023, True)]
    assert eight_digit_scheme_holds(rows) is holds
    assert len(r1v2_year_fixes(rows)) == (1 if holds else 0)


def test_eight_digit_rows_are_never_sequence_neighbours():
    main = _trusted(0, [2003] * 9 + [2005]) + _trusted(11, [2003] * 4)
    rows = main + _eight(10, [2018] * 10) + [_suspect(10)]
    assert r1v2_year_fixes(rows) == []


def test_every_fix_is_a_range_of_at_most_two_years_holding_its_point():
    rng = random.Random(11)
    fixes = []
    for n in range(400):
        base = rng.randint(1990, 2015)
        years = [base + rng.choice([0, 0, 0, 1, 1, 2, -1, 5]) for _ in range(20)]
        d = rng.choice([date(2024, 2, 29), date(2023, 5, 2), None])
        suspect = _suspect(10, d, d.year if d else 2023, accident_id=n)
        rows = _trusted(0, years[:10]) + [suspect] + _trusted(11, years[10:])
        fixes += r1v2_year_fixes(rows)
    assert len(fixes) > 50
    for f in fixes:
        assert f.year_lo is not None and f.year_hi is not None and f.year is not None and f.new_date is not None
        assert f.year_hi - f.year_lo <= 2 and f.year_lo <= f.year <= f.year_hi
        assert f.new_date.year == f.year
        # The daily views keep only day precision; that must imply a single-year range.
        if f.date_precision in ("day", "month"):
            assert f.year_lo == f.year_hi
        else:
            assert f.date_precision == "year" and (f.new_date.month, f.new_date.day) == (7, 1)


def test_changes_carry_the_method_and_lift_only_year_unverified():
    rows = _trusted(0, [2003] * 9 + [2005]) + [_suspect(10)] + _trusted(11, [2003] * 10)
    [fix] = r1v2_year_fixes(rows)
    changes = changes_for(fix, old_date=date(2023, 5, 2), old_excluded="year_unverified", method="r1v2-sequence")
    assert {c.method for c in changes} == {"r1v2-sequence"}
    assert {c.field: c.new_value for c in changes}["excluded_reason"] is None
    other = changes_for(fix, old_date=date(2023, 5, 2), old_excluded="non_climbing", method="r1v2-sequence")
    assert "excluded_reason" not in {c.field for c in other}


def test_backtest_hides_each_row_and_scores_point_and_range():
    # Years step up every 10 ids, with one stray year at id 24 that the tight bound and the
    # min/max spread cannot pass but the percentile bound can.
    years = [1990 + i // 10 for i in range(60)]
    years[24] = 1994
    rows = [AacRow(i, (i, ""), date(y, 5, 2), y) for i, y in enumerate(years)]
    rows += _eight(12, [2018] * 11 + [2017])
    out = r1_backtest(rows)
    v1, v2, shipped = out["sequence_v1"], out["sequence_v2"], out["sequence_shipped"]
    assert v1["rows"] == v2["rows"] == 60
    assert v1["dated"] < v2["dated"] == shipped["dated"]
    for s in (v1, v2, shipped):
        assert s["within_one_year"] <= s["dated"] and s["in_range"] <= s["dated"]
    assert out["eight_digit"] == {
        "rows": 12, "dated": 12, "coverage": 1.0, "within_one_year": 12, "within_one_year_rate": 1.0,
        "in_range": 12, "in_range_rate": 1.0,
    }


def test_backtest_edges_without_min_side_count_as_undated():
    rows = [AacRow(i, (i, ""), date(2003, 5, 2), 2003) for i in range(12)]
    out = r1_backtest(rows)["sequence_v2"]
    # Rows 0-4 and 7-11 lack five neighbours on one side once they are hidden.
    assert (out["rows"], out["dated"], out["within_one_year"], out["in_range"]) == (12, 2, 2, 2)


def test_cli_registers_the_r1v2_steps():
    assert {"r1v2", "r1v2-backtest", "r1v2-export", "r1v2-import"} <= set(STEPS)


# Row 11 is the suspect r1-v1 voids (row 10 is an out-of-order 2005); row 41 is an eight-digit
# suspect beside ten trusted 2018 rows; row 22 has nothing to its right and stays unverified.
R1V2_SEED = "\n".join(
    [
        "INSERT INTO accidents (accident_id, source, source_id, date, location, mountain, description) VALUES "
        + ", ".join(
            f"({i}, 'AAC', '{i}', '{ {10: '2005-05-02', 11: '2023-05-02'}.get(i, '2003-05-02') }', "
            f"'Fixture Canyon', 'Fixture Peak', 'narrative {i}')"
            for i in range(1, 22)
        )
        + ", (22, 'AAC', '500', '2021-08-09', NULL, NULL, NULL)"
        + "".join(f", ({30 + n}, 'AAC', '2019{n + 1:04d}', '2018-04-09', NULL, NULL, NULL)" for n in range(10))
        + ", (41, 'AAC', '20190011', '2023-03-04', NULL, NULL, 'narrative 41');",
        "INSERT INTO internal.accidents_raw (accident_id, source, source_id, date) "
        "SELECT accident_id, source, source_id, date FROM accidents;",
    ]
)


async def _row(conn, accident_id: int, columns: str) -> tuple[object, ...]:
    return tuple((await conn.execute(text(f"SELECT {columns} FROM accidents WHERE accident_id = :a"), {"a": accident_id})).one())


def _ns(**kw: object) -> argparse.Namespace:
    return argparse.Namespace(**({"apply": False, "out": None, "overwrite": False} | kw))


@requires_pg
def test_r1v2_refuses_before_r1_v1_is_applied():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                with pytest.raises(SystemExit, match="apply r2r1 first"):
                    await run_r1v2(conn, _ns())
                await run_r2r1(conn, _ns())  # a dry run logs nothing
                with pytest.raises(SystemExit, match="apply r2r1 first"):
                    await run_r1v2(conn, _ns())
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=R1V2_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_r1v2_dates_what_r1_v1_could_not_and_a_v1_rerun_never_undoes_it():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await run_r2r1(conn, _ns(apply=True))
            async with engine.connect() as conn:
                assert await _row(conn, 11, "date, excluded_reason") == (date(2023, 5, 2), "year_unverified")
                dry = await run_r1v2(conn, _ns())
            assert dry["r1v2"]["mode"] == "dry_run"
            assert (dry["recovered"], dry["still_unverified"]) == ({"eight_digit": 1, "sequence": 1}, 1)
            assert dry["r1v1_unverified"] == {"eight_digit": 1, "sequence": 2}
            async with engine.begin() as conn:
                first = await run_r1v2(conn, _ns(apply=True))
            async with engine.begin() as conn:
                again = await run_r1v2(conn, _ns(apply=True))
            # year_source is already r1-v1's value, so it counts as unchanged.
            assert first["r1v2"]["applied"] > 0
            assert first["r1v2"]["applied"] + first["r1v2"]["unchanged"] == first["r1v2"]["proposed_total"]
            # The rerun proposes no date or exclusion change (both already hold), and the rest
            # is either recorded under r1-v2 or already equal.
            r = again["r1v2"]
            assert r["applied"] == 0 and r["already_recorded"] > 0
            assert r["already_recorded"] + r["unchanged"] == r["proposed_total"]
            cols = "date, year, year_lo, year_hi, date_precision, year_source, excluded_reason"
            async with engine.connect() as conn:
                assert await _row(conn, 11, cols) == (
                    date(2003, 5, 2), 2003.0, 2003, 2003, "day", "aac_source_id_neighbours", None
                )
                assert await _row(conn, 41, cols) == (
                    date(2018, 7, 1), 2018.0, 2017, 2018, "year", "aac_source_id_neighbours", None
                )
                assert await _row(conn, 22, "date, excluded_reason") == (date(2021, 8, 9), "year_unverified")
                audit = (await conn.execute(text(
                    "SELECT accident_id, field, old_value, new_value, method FROM internal.accident_revisions "
                    "WHERE rule_version = 'r1-v2' AND field IN ('excluded_reason', 'date') ORDER BY 1, 2"))).all()
            assert [tuple(r) for r in audit] == [
                (11, "date", "2023-05-02", "2003-05-02", "r1v2-sequence"),
                (11, "excluded_reason", "year_unverified", None, "r1v2-sequence"),
                (41, "date", "2023-03-04", "2018-07-01", "r1v2-eight-digit"),
                (41, "excluded_reason", "year_unverified", None, "r1v2-eight-digit"),
            ]
            # Row 11's r1-v1 date key was never written (its date was already the snapshot's),
            # so without the guard this rerun would put 2023 back under a lifted exclusion.
            async with engine.begin() as conn:
                rerun = await run_r2r1(conn, _ns(apply=True))
            assert rerun["superseded_by_r1v2"] == 2
            # Superseded rows are not r1-v1's year_unverified any more; only row 22 is.
            assert rerun["r1_year_unverified"] == 1
            assert rerun["aac_schemes"]["eight_digit"]["unverified"] == 0
            assert rerun["r1"]["report"]["quarantined"] == {"year_unverified": 1}
            assert rerun["r1"]["applied"] == 0
            async with engine.connect() as conn:
                assert await _row(conn, 11, "date, excluded_reason") == (date(2003, 5, 2), None)
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=R1V2_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_r1v2_never_re_decides_a_field_another_rule_or_the_owner_set():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await run_r2r1(conn, _ns(apply=True))
                await conn.execute(text(
                    "INSERT INTO internal.accident_revisions (accident_id, field, old_value, new_value, method, "
                    "rule_version, run_id) VALUES (41, 'date', '2023-03-04', '2023-03-04', 'owner', 'owner-x', "
                    "gen_random_uuid())"))
                await conn.execute(text("UPDATE accidents SET excluded_reason = 'non_climbing' WHERE accident_id = 11"))
            async with engine.begin() as conn:
                out = await run_r1v2(conn, _ns(apply=True))
            assert (out["decided_elsewhere"], out["recovered"]) == (1, {"sequence": 1})
            async with engine.connect() as conn:
                assert await _row(conn, 41, "date, excluded_reason") == (date(2023, 3, 4), "year_unverified")
                # Dated, but another step's exclusion stands.
                assert await _row(conn, 11, "date, excluded_reason") == (date(2003, 5, 2), "non_climbing")
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=R1V2_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_r1v2_hand_check_round_trip_is_its_own_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await run_r2r1(conn, _ns(apply=True))
            async with engine.connect() as conn:
                await run_r1_export(conn, _ns(out=str(tmp_path / "r1_before.csv")))
            async with engine.begin() as conn:
                await run_r1v2(conn, _ns(apply=True))
            async with engine.connect() as conn:
                await run_r1_export(conn, _ns(out=str(tmp_path / "r1_after.csv")))
                exported = await run_r1v2_export(conn, _ns())
            # The r1 sample is unchanged by r1-v2 (Task 12 6f compares its ids across databases).
            assert (tmp_path / "r1_before.csv").read_text() == (tmp_path / "r1_after.csv").read_text()
            path = tmp_path / "r1v2_year_check.csv"
            assert exported["exported"] == 2 and exported["path"] == str(path)
            assert exported["taken"] == {"eight_digit": 1, "sequence": 1, "_topup": 0}
            text_out = path.read_text(encoding="utf-8")
            assert "narrative" not in text_out and "eight_digit" not in text_out
            with path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            assert list(rows[0]) == [*LOCATOR_COLUMNS, "year_lo", "year_hi", "source_url", "within_one_year"]
            assert sorted((r["accident_id"], r["year_lo"], r["year_hi"]) for r in rows) == [
                ("11", "2003", "2003"), ("41", "2017", "2018")
            ]
            for r in rows:
                r["within_one_year"] = {"11": "yes", "41": "no"}[r["accident_id"]]
            # Excel's "CSV UTF-8" writes a BOM; the import must still find the key column.
            with path.open("w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            filled = path.read_bytes()
            async with engine.connect() as conn:
                with pytest.raises(FileExistsError, match="--overwrite"):
                    await run_r1v2_export(conn, _ns())
            assert path.read_bytes() == filled
            stray = tmp_path / "stray.csv"
            # Row 1 was dated by r1-v1, not r1-v2: outside this pool.
            stray.write_bytes(filled + b"1,,,,,,,,yes\r\n")
            async with engine.begin() as conn:
                with pytest.raises(ValueError, match="outside the hand-check pool"):
                    await run_r1v2_import(conn, argparse.Namespace(review_file=str(stray)))
            async with engine.begin() as conn:
                out = await run_r1v2_import(conn, argparse.Namespace(review_file=str(path)))
            assert out == {"source": "repair:r1v2-check", "n": 2, "agreement": 0.5}
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=R1V2_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_r1v2_backtest_is_read_only():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                with pytest.raises(SystemExit, match="read-only"):
                    await run_r1v2_backtest(conn, _ns(apply=True))
                out = await run_r1v2_backtest(conn, _ns())
            assert out["backtest"]["eight_digit"]["rows"] == 10
            assert out["backtest"]["sequence_v2"]["rows"] == 20
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=R1V2_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_r1v2_never_lifts_an_exclusion_another_rule_or_the_owner_set():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await run_r2r1(conn, _ns(apply=True))
                # The owner confirmed row 11's exclusion, with the same value r1-v1 set.
                await conn.execute(text(
                    "INSERT INTO internal.accident_revisions (accident_id, field, old_value, new_value, method, "
                    "rule_version, run_id) VALUES (11, 'excluded_reason', 'year_unverified', 'year_unverified', "
                    "'owner', 'owner-x', gen_random_uuid())"))
            async with engine.begin() as conn:
                out = await run_r1v2(conn, _ns(apply=True))
            assert (out["decided_elsewhere"], out["recovered"]) == (1, {"eight_digit": 1})
            async with engine.connect() as conn:
                assert await _row(conn, 11, "date, excluded_reason") == (date(2003, 5, 2), "year_unverified")
                assert await _row(conn, 41, "excluded_reason") == (None,)
                fields = (await conn.execute(text(
                    "SELECT array_agg(field ORDER BY field) FROM internal.accident_revisions "
                    "WHERE accident_id = 11 AND rule_version = 'r1-v2'"))).scalar_one()
            assert "excluded_reason" not in fields and "date" in fields
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=R1V2_SEED) as name:
        asyncio.run(scenario(sa_url(name)))
