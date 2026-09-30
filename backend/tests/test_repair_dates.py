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
    source_sort_key,
    with_original_values,
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
    return [AacRow(1000 + i, start_key + i, date(y, 5, 2), y) for i, y in enumerate(years)]


def test_sort_key_takes_the_trailing_integer():
    assert source_sort_key("ANAC-2004-0123") == 123
    assert source_sort_key("  88 ") == 88
    assert source_sort_key("no digits") is None
    assert source_sort_key(None) is None


def test_confident_neighbours_keep_day_and_month():
    rows = _trusted(0, [2003] * 10) + [AacRow(1, 10, date(2023, 5, 2), 2023)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.year, fix.year_lo, fix.year_hi, fix.date_precision, fix.new_date) == (2003, 2003, 2003, "day", date(2003, 5, 2))
    assert fix.excluded_reason is None


def test_narrow_spread_becomes_year_precision_on_july_1():
    rows = _trusted(0, [2002] * 5 + [2003] * 5) + [AacRow(1, 10, date(2023, 5, 2), 2023)] + _trusted(11, [2003] * 5 + [2004] * 5)
    [fix] = r1_year_fixes(rows)
    assert (fix.year, fix.year_lo, fix.year_hi, fix.date_precision, fix.new_date) == (2003, 2002, 2004, "year", date(2003, 7, 1))


def test_wide_spread_is_unverified_and_date_is_untouched():
    rows = _trusted(0, [1995] * 10) + [AacRow(1, 10, date(2023, 5, 2), 2023)] + _trusted(11, [2004] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.date_precision, fix.excluded_reason, fix.new_date) == ("unknown", "year_unverified", None)
    assert [c.field for c in changes_for(fix, old_date=date(2023, 5, 2))] == [
        "year_lo", "year_hi", "year_source", "date_precision", "excluded_reason"
    ]


def test_row_past_last_trusted_id_is_unverified():
    rows = _trusted(0, [2018] * 20) + [AacRow(1, 500, date(2019, 6, 3), 2019)]
    [fix] = r1_year_fixes(rows)
    assert fix.excluded_reason == "year_unverified"
    assert fix.new_date is None


def test_unparseable_source_id_is_unverified():
    rows = _trusted(0, [2003] * 20) + [AacRow(1, None, date(2023, 5, 2), 2023)]
    [fix] = r1_year_fixes(rows)
    assert fix.excluded_reason == "year_unverified"


def test_feb_29_into_a_non_leap_year_falls_back_to_month_precision():
    rows = _trusted(0, [2003] * 10) + [AacRow(1, 10, date(2024, 2, 29), 2024)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)
    assert (fix.new_date, fix.date_precision) == (date(2003, 2, 15), "month")


def test_confident_fix_changes_date_and_year():
    rows = _trusted(0, [2003] * 10) + [AacRow(1, 10, date(2023, 5, 2), 2023)] + _trusted(11, [2003] * 10)
    [fix] = r1_year_fixes(rows)
    fields = {c.field: c.new_value for c in changes_for(fix, old_date=date(2023, 5, 2))}
    assert fields["date"] == date(2003, 5, 2) and fields["year"] == 2003


def test_restrict_to_raw_drops_rows_loaded_after_the_snapshot():
    rows = [{"accident_id": 1, "source_id": "ANAC-2003-0001"}, {"accident_id": 900000001, "source_id": "ANAC-2025-001"}]
    assert restrict_to_raw(rows, frozenset({1})) == [rows[0]]


def test_original_values_replace_repaired_date_and_year():
    rows = [
        {"accident_id": 1, "source": "AAC", "date": date(2003, 5, 2), "year": 2003.0},
        {"accident_id": 2, "source": "AAC", "date": date(2003, 5, 2), "year": 2003.0},
        {"accident_id": 3, "source": "AAC", "date": date(2010, 1, 1), "year": 2010.0},
    ]
    originals = {(1, "date"): "2023-05-02", (1, "year"): "2023", (2, "date"): None, (2, "year"): None}
    out = with_original_values(rows, originals)
    assert [(r["date"], r["year"]) for r in out] == [
        (date(2023, 5, 2), 2023.0), (None, None), (date(2010, 1, 1), 2010.0)
    ]
    assert rows[0]["date"] == date(2003, 5, 2)


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


# Row 11 is the suspect (2023 among 2003s); row 22 has no neighbours past it, so it stays unverified.
SUSPECT_SEED = "\n".join(
    [
        "INSERT INTO accidents (accident_id, source, source_id, date) VALUES "
        + ", ".join(
            f"({i}, 'AAC', 'ANAC-{i:04d}', '{'2023-05-02' if i == 11 else '2003-05-02'}')" for i in range(1, 22)
        )
        + ", (22, 'AAC', 'ANAC-0500', '2021-08-09');",
        "INSERT INTO internal.accidents_raw (accident_id, source, source_id, date) "
        "SELECT accident_id, source, source_id, date FROM accidents;",
    ]
)


@requires_pg
def test_r1_reads_original_values_so_a_later_rule_version_sees_the_same_inputs(monkeypatch):
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                v1 = await run_r2r1(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                row11 = (await conn.execute(text(
                    "SELECT date, year, date_precision, excluded_reason FROM accidents WHERE accident_id = 11"))).one()
                row22 = (await conn.execute(text(
                    "SELECT date, date_precision, excluded_reason FROM accidents WHERE accident_id = 22"))).one()
            assert tuple(row11) == (date(2003, 5, 2), 2003.0, "day", None)
            assert tuple(row22) == (date(2021, 8, 9), "unknown", "year_unverified")
            assert v1["year_unverified"] == 1
            # G17: had R1 read live rows, re-dated row 11 would now be a trusted 2003 neighbour
            # and drop out of R1, so a new rule version would propose something different.
            monkeypatch.setattr(dates, "R1_VERSION", "r1-v2")
            async with engine.connect() as conn:
                v2 = await run_r2r1(conn, argparse.Namespace(apply=False))
            assert v2["r1"]["report"] == v1["r1"]["report"]
            assert v2["r1"]["proposed"]["year_source=aac_source_id_neighbours"] == 2
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SUSPECT_SEED) as name:
        asyncio.run(scenario(sa_url(name)))
