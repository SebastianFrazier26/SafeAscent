import argparse
import asyncio
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair import dates
from app.data.repair.dates import (
    AacRow,
    changes_for,
    r1_year_fixes,
    r2_precision,
    restrict_to_raw,
    run_r2r1,
    source_scheme,
    source_sort_key,
)
from tests.pgtest import migrated_db, requires_pg, sa_url


def test_aac_day_15_is_month_and_july_1_is_year():
    assert r2_precision("aac", date(2004, 3, 15), 2004) == "month"
    assert r2_precision("aac", date(2004, 7, 1), 2004) == "year"
    assert r2_precision("aac", date(2004, 3, 14), 2004) == "day"


def test_other_sources_keep_day_precision():
    assert r2_precision("nps", date(2004, 7, 1), 2004) == "day"
    assert r2_precision("avalanche", date(2004, 3, 15), 2004) == "day"


def test_year_only_and_missing():
    assert r2_precision("aac", None, 2004.0) == "year"
    assert r2_precision("nps", None, None) == "unknown"


def _trusted(start_key: int, years: list[int]) -> list[AacRow]:
    return [AacRow(1000 + i, (start_key + i, ""), date(y, 5, 2), y) for i, y in enumerate(years)]


def _suspect(key: int, d: date | None = date(2023, 5, 2), year: int = 2023) -> AacRow:
    return AacRow(1, (key, ""), d, year)


def _from_ids(ids_and_years: list[tuple[str, int]]) -> list[AacRow]:
    return [AacRow(2000 + i, source_sort_key(sid), date(y, 5, 2), y) for i, (sid, y) in enumerate(ids_and_years)]


def test_sort_key_takes_the_trailing_integer_and_suffix():
    assert source_sort_key("ANAC-2004-0123") == (123, "")
    assert source_sort_key("  88 ") == (88, "")
    assert source_sort_key("88a") == (88, "a")
    assert source_sort_key("2683T") == (2683, "t")
    assert source_sort_key("no digits") is None
    assert source_sort_key(None) is None


def test_eight_digit_ids_are_their_own_scheme_with_no_sort_key():
    assert [source_scheme(s) for s in ("1", "1204b", "20190001", "20190069", "x", None)] == [
        "sequence", "sequence", "eight_digit", "eight_digit", "unparseable", "unparseable"
    ]
    assert source_sort_key("20190001") is None


def test_suffix_variants_are_adjacent_neighbours():
    ids = [(str(n), 2003) for n in range(1, 6)] + [("5a", 2003), ("5b", 2003)]
    ids += [(f"{n}t", 2003) for n in range(7, 12)]
    rows = _from_ids(ids) + [AacRow(1, source_sort_key("6"), date(2023, 5, 2), 2023)]
    [fix] = r1_year_fixes(rows)
    assert (fix.year, fix.excluded_reason) == (2003, None)
    assert sorted(k for k in (r.sort_key for r in rows) if k is not None)[4:7] == [(5, ""), (5, "a"), (5, "b")]


def test_eight_digit_rows_are_never_neighbours_and_their_suspects_are_unverified():
    main = _from_ids([(str(n), 2003) for n in range(1, 6)] + [(str(n), 2003) for n in range(7, 11)])
    eight = _from_ids([(f"2019{n:04d}", 2018) for n in range(1, 11)])
    main_suspect = AacRow(1, source_sort_key("6"), date(2023, 5, 2), 2023)
    eight_suspect = AacRow(2, source_sort_key("20190011"), date(2020, 3, 4), 2020)
    fixes = {f.accident_id: f for f in r1_year_fixes(main + eight + [main_suspect, eight_suspect])}
    assert fixes[1].excluded_reason == "year_unverified"  # four main rows on the right, not five
    assert fixes[2].excluded_reason == "year_unverified"


def test_confident_neighbours_keep_day_and_month():
    rows = _trusted(0, [2003] * 10) + [_suspect(10)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.year, fix.year_lo, fix.year_hi, fix.date_precision, fix.new_date) == (2003, 2003, 2003, "day", date(2003, 5, 2))
    assert fix.excluded_reason is None


def test_tightest_bound_is_last_left_year_to_first_right_year():
    rows = _trusted(0, [2002] * 5 + [2003] * 5) + [_suspect(10)] + _trusted(11, [2003] * 5 + [2004] * 5)
    [fix] = r1_year_fixes(rows)
    assert (fix.year, fix.year_lo, fix.year_hi, fix.date_precision, fix.new_date) == (2003, 2003, 2003, "day", date(2003, 5, 2))


def test_spread_at_the_limit_becomes_year_precision_on_july_1():
    rows = _trusted(0, [2002] * 10) + [_suspect(10)] + _trusted(11, [2004] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.year, fix.year_lo, fix.year_hi, fix.date_precision, fix.new_date) == (2003, 2002, 2004, "year", date(2003, 7, 1))


def test_spread_one_past_the_limit_is_unverified():
    rows = _trusted(0, [2001] * 10) + [_suspect(10)] + _trusted(11, [2004] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.excluded_reason, fix.year_lo, fix.year_hi, fix.new_date) == ("year_unverified", 2001, 2004, None)


def test_contradictory_neighbours_are_unverified():
    rows = _trusted(0, [2003] * 9 + [2005]) + [_suspect(10)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.excluded_reason, fix.year_lo, fix.year_hi) == ("year_unverified", None, None)


def test_exactly_min_side_on_each_side_is_enough():
    rows = _trusted(0, [2003] * 5) + [_suspect(5)] + _trusted(6, [2003] * 5)
    [fix] = r1_year_fixes(rows)
    assert (fix.year, fix.excluded_reason) == (2003, None)
    short = _trusted(0, [2003] * 5) + [_suspect(5)] + _trusted(6, [2003] * 4)
    assert r1_year_fixes(short)[0].excluded_reason == "year_unverified"


def test_year_only_suspect_becomes_year_precision():
    rows = _trusted(0, [2003] * 10) + [_suspect(10, d=None)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.year, fix.date_precision, fix.new_date, fix.excluded_reason) == (2003, "year", date(2003, 7, 1), None)


def test_wide_spread_is_unverified_and_date_is_untouched():
    rows = _trusted(0, [1995] * 10) + [_suspect(10)] + _trusted(11, [2004] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.date_precision, fix.excluded_reason, fix.new_date) == ("unknown", "year_unverified", None)
    assert [c.field for c in changes_for(fix, old_date=date(2023, 5, 2))] == [
        "year_lo", "year_hi", "year_source", "date_precision", "excluded_reason"
    ]


def test_row_past_last_trusted_id_is_unverified():
    rows = _trusted(0, [2018] * 20) + [_suspect(500, date(2019, 6, 3), 2019)]
    [fix] = r1_year_fixes(rows)
    assert fix.excluded_reason == "year_unverified"
    assert fix.new_date is None


def test_unparseable_source_id_is_unverified():
    rows = _trusted(0, [2003] * 20) + [AacRow(1, None, date(2023, 5, 2), 2023)]
    [fix] = r1_year_fixes(rows)
    assert fix.excluded_reason == "year_unverified"


def test_feb_29_into_a_non_leap_year_falls_back_to_month_precision():
    rows = _trusted(0, [2003] * 10) + [_suspect(10, date(2024, 2, 29), 2024)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.new_date, fix.date_precision) == (date(2003, 2, 15), "month")


def test_confident_fix_changes_date_and_year():
    rows = _trusted(0, [2003] * 10) + [_suspect(10)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)
    fields = {c.field: c.new_value for c in changes_for(fix, old_date=date(2023, 5, 2))}
    assert fields["date"] == date(2003, 5, 2) and fields["year"] == 2003


def test_a_verified_year_clears_only_its_own_exclusion():
    rows = _trusted(0, [2003] * 10) + [_suspect(10)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)

    def excluded(old: str | None) -> list[object]:
        return [c.new_value for c in changes_for(fix, old_date=None, old_excluded=old) if c.field == "excluded_reason"]

    assert excluded("year_unverified") == [None]
    assert excluded(None) == []
    assert excluded("non_climbing") == []


def test_an_unverified_year_never_overwrites_another_exclusion():
    rows = _trusted(0, [1995] * 10) + [_suspect(10)] + _trusted(11, [2004] * 10)
    [fix] = r1_year_fixes(rows)

    def excluded(old: str | None) -> list[object]:
        return [c.new_value for c in changes_for(fix, old_date=None, old_excluded=old) if c.field == "excluded_reason"]

    assert excluded(None) == ["year_unverified"]
    assert excluded("year_unverified") == ["year_unverified"]
    assert excluded("non_climbing") == []


def test_restrict_to_raw_drops_rows_loaded_after_the_snapshot():
    rows = [{"accident_id": 1, "source_id": "ANAC-2003-0001"}, {"accident_id": 900000001, "source_id": "ANAC-2025-001"}]
    assert restrict_to_raw(rows, frozenset({1})) == [rows[0]]


# The R11 case: ANAC-2025-001 has sort key 1, so the neighbour rule would pull it into the oldest years.
RAW_SEED = "\n".join(
    [
        "INSERT INTO accidents (accident_id, source, source_id, date) VALUES "
        + ", ".join(f"({i}, 'AAC', 'ANAC-2003-{i:04d}', '2003-05-02')" for i in range(1, 13))
        + ";",
        "INSERT INTO internal.accidents_raw (accident_id, source, source_id, date) "
        "SELECT accident_id, source, source_id, date FROM accidents;",
        "INSERT INTO accidents (accident_id, source, source_id, date, date_precision) VALUES "
        "(900000001, 'AAC', 'ANAC-2025-001', '2025-06-10', 'month');",
    ]
)


@requires_pg
def test_r2r1_never_touches_rows_loaded_after_the_raw_snapshot():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                out = await run_r2r1(conn, argparse.Namespace(apply=True))
            assert out["r2"]["applied"] > 0
            async with engine.connect() as conn:
                revisions = (await conn.execute(text(
                    "SELECT count(*) FROM internal.accident_revisions WHERE accident_id = 900000001"))).scalar_one()
                row = (await conn.execute(text(
                    "SELECT date, date_precision, excluded_reason FROM accidents WHERE accident_id = 900000001"))).one()
            assert revisions == 0
            assert tuple(row) == (date(2025, 6, 10), "month", None)
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=RAW_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


# Real-shaped ids: row 11 is the suspect (2023 among 2003s); row 24 ("10a") is a suffix
# variant between 10 and 11; row 22 has no neighbours past it and row 23 is in the 8-digit
# scheme, so both stay unverified.
SUSPECT_SEED = "\n".join(
    [
        "INSERT INTO accidents (accident_id, source, source_id, date) VALUES "
        + ", ".join(f"({i}, 'AAC', '{i}', '{'2023-05-02' if i == 11 else '2003-05-02'}')" for i in range(1, 22))
        + ", (22, 'AAC', '500', '2021-08-09'), (23, 'AAC', '20190005', '2020-03-04'), (24, 'AAC', '10a', '2003-05-02');",
        "INSERT INTO internal.accidents_raw (accident_id, source, source_id, date) "
        "SELECT accident_id, source, source_id, date FROM accidents;",
    ]
)


async def _row(conn, accident_id: int, columns: str) -> tuple[object, ...]:
    return tuple((await conn.execute(text(f"SELECT {columns} FROM accidents WHERE accident_id = :a"), {"a": accident_id})).one())


@requires_pg
def test_r1_reads_the_raw_snapshot_so_a_later_rule_version_sees_the_same_inputs(monkeypatch):
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                v1 = await run_r2r1(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                assert await _row(conn, 11, "date, year, date_precision, excluded_reason") == (date(2003, 5, 2), 2003.0, "day", None)
                assert await _row(conn, 22, "date, date_precision, excluded_reason") == (date(2021, 8, 9), "unknown", "year_unverified")
                assert await _row(conn, 23, "date, excluded_reason") == (date(2020, 3, 4), "year_unverified")
            assert (v1["r1_year_unverified"], v1["r2_no_date_unverified"], v1["year_unverified"]) == (2, 0, 2)
            assert v1["aac_schemes"] == {
                "eight_digit": {"rows": 1, "suspects": 1, "unverified": 1},
                "sequence": {"rows": 23, "suspects": 2, "unverified": 1},
            }
            # G17: had R1 read live rows, re-dated row 11 would now be a trusted 2003 neighbour
            # and drop out of R1, so a new rule version would propose something different.
            monkeypatch.setattr(dates, "R1_VERSION", "r1-v2")
            async with engine.connect() as conn:
                v2 = await run_r2r1(conn, argparse.Namespace(apply=False))
            assert v2["r1"]["report"] == v1["r1"]["report"]
            assert v2["r1"]["proposed"]["year_source=aac_source_id_neighbours"] == 3
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SUSPECT_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_a_confident_row_already_on_its_date_still_gets_its_year():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await run_r2r1(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                assert await _row(conn, 11, "date, year, date_precision") == (date(2003, 5, 2), 2003.0, "day")
                fields = (await conn.execute(text(
                    "SELECT array_agg(field ORDER BY field) FROM internal.accident_revisions "
                    "WHERE accident_id = 11 AND field IN ('date', 'year')"))).scalar_one()
            assert fields == ["year"]
        finally:
            await engine.dispose()

    seed = SUSPECT_SEED + "\nUPDATE accidents SET date = '2003-05-02', year = 1999 WHERE accident_id = 11;"
    with migrated_db(seed_sql=seed) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_another_steps_exclusion_survives_an_r1_rerun(monkeypatch):
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await run_r2r1(conn, argparse.Namespace(apply=True))
                await conn.execute(text("UPDATE accidents SET excluded_reason = 'non_climbing' WHERE accident_id = 11"))
            monkeypatch.setattr(dates, "R1_VERSION", "r1-v2")
            async with engine.begin() as conn:
                await run_r2r1(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                assert await _row(conn, 11, "excluded_reason") == ("non_climbing",)
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SUSPECT_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


# Row 6 (2023) has five trusted 2003 rows to its left but only four to its right; row 11 was
# 2021 in the snapshot and is then hand-edited to 2003 in accidents, and row 12 arrives after 0004.
MANUAL_SEED = "\n".join(
    [
        "INSERT INTO accidents (accident_id, source, source_id, date) VALUES "
        + ", ".join(
            f"({i}, 'AAC', '{i}', '{ {6: '2023-05-02', 11: '2021-08-09'}.get(i, '2003-05-02') }')"
            for i in range(1, 12)
        )
        + ";",
        "UPDATE accidents SET year = extract(year FROM date);",
        "INSERT INTO internal.accidents_raw (accident_id, source, source_id, date, year) "
        "SELECT accident_id, source, source_id, date, year FROM accidents;",
        "UPDATE accidents SET date = '2003-05-02', year = 2003 WHERE accident_id = 11;",
        "INSERT INTO accidents (accident_id, source, source_id, date) VALUES (12, 'AAC', '12', '2003-05-02');",
    ]
)


@requires_pg
def test_a_hand_edit_to_accidents_never_makes_a_row_trusted():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                out = await run_r2r1(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                row6, row11, row12 = [await _row(conn, a, "date, year, excluded_reason") for a in (6, 11, 12)]
            # Live, rows 11 and 12 would complete row 6's right side and re-date it to 2003.
            assert row6 == (date(2023, 5, 2), 2023.0, "year_unverified")
            # D10: the unverified row goes back to its snapshot date and year, not the hand edit.
            assert row11 == (date(2021, 8, 9), 2021.0, "year_unverified")
            assert row12 == (date(2003, 5, 2), None, None)
            assert (out["year_unverified"], out["not_in_raw"]) == (2, 1)
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=MANUAL_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_an_unverified_row_keeps_another_steps_exclusion():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                out = await run_r2r1(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                assert await _row(conn, 22, "date_precision, excluded_reason") == ("unknown", "non_climbing")
                assert await _row(conn, 25, "date_precision, excluded_reason") == ("unknown", "non_climbing")
            assert (out["already_excluded_other"], out["r1_year_unverified"], out["r2_no_date_unverified"]) == (2, 2, 1)
        finally:
            await engine.dispose()

    seed = "\n".join(
        [
            SUSPECT_SEED,
            "INSERT INTO accidents (accident_id, source) VALUES (25, 'NPS');",
            "INSERT INTO internal.accidents_raw (accident_id, source) VALUES (25, 'NPS');",
            "UPDATE accidents SET excluded_reason = 'non_climbing' WHERE accident_id IN (22, 25);",
        ]
    )
    with migrated_db(seed_sql=seed) as name:
        asyncio.run(scenario(sa_url(name)))
