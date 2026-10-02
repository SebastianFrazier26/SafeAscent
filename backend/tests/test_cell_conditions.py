import asyncio
import dataclasses
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines import cell_conditions
from app.pipelines.cell_conditions import DayRow, day_rows, upsert_rows
from app.pipelines.open_meteo import LocationResponse
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)
TZ = "America/Denver"


def _resp(days, tmax, tmin, gust, tz=TZ):
    n = len(days)
    return LocationResponse.model_validate({
        "latitude": 40.0, "longitude": -105.3, "elevation": 1650.0, "timezone": tz,
        "daily": {"time": days, "temperature_2m_max": tmax, "temperature_2m_min": tmin,
                  "precipitation_sum": [0.0] * n, "snowfall_sum": [0.0] * n,
                  "wind_speed_10m_max": [5.0] * n, "wind_gusts_10m_max": gust},
    })


def _archive(response, report):
    return day_rows(1, TZ, response, today=TODAY, origin="archive", model="era5_seamless",
                    source="open_meteo_archive", report=report)


def test_null_values_stay_null():
    report = ValidationReport("t")
    [row] = _archive(_resp(["2020-01-01"], [None], [-3.0], [None]), report)
    assert (row.tmax, row.gust_max_ms, row.freeze_thaw, row.record_kind) == (None, None, None, "era5")
    assert (row.tmin, row.precip_mm) == (-3.0, 0.0)
    assert report.accepted == 1


def test_freeze_thaw_and_quarantine_rules():
    report = ValidationReport("t")
    rows = _archive(
        _resp(["2020-01-01", "2020-01-02", "2020-01-03", "2026-09-29"], [2.0, 80.0, -5.0, 1.0], [-1.0, 0.0, 3.0, 0.0],
              [10.0, 10.0, 10.0, 10.0]),
        report,
    )
    assert [(r.date.day, r.freeze_thaw) for r in rows] == [(1, True)]
    assert report.quarantined == {"out_of_range": 1, "tmax_below_tmin": 1, "future": 1}
    assert report.rows_in == 4


def test_freeze_thaw_false_when_no_crossing():
    report = ValidationReport("t")
    rows = _archive(_resp(["2020-01-01", "2020-01-02"], [5.0, -1.0], [1.0, -4.0], [3.0, 3.0]), report)
    assert [r.freeze_thaw for r in rows] == [False, False]


def test_archive_today_is_not_future():
    report = ValidationReport("t")
    [row] = _archive(_resp([TODAY.isoformat()], [5.0], [-2.0], [9.0]), report)
    assert row.record_kind == "era5"


def test_forecast_api_splits_past_days_into_stopgap():
    report = ValidationReport("t")
    days = ["2026-09-26", "2026-09-27", "2026-09-28", "2026-09-30"]
    rows = day_rows(1, TZ, _resp(days, [5.0] * 4, [-2.0] * 4, [9.0] * 4), today=TODAY, origin="forecast_api",
                    model="best_match", source="open_meteo_forecast", report=report)
    assert [r.record_kind for r in rows] == ["stopgap", "stopgap", "forecast", "forecast"]
    assert {(r.model, r.source) for r in rows} == {("best_match", "open_meteo_forecast")}


def test_timezone_mismatch_quarantines_every_day():
    report = ValidationReport("t")
    assert _archive(_resp(["2020-01-01", "2020-01-02"], [1.0, 1.0], [0.0, 0.0], [1.0, 1.0], tz="GMT"), report) == []
    assert report.quarantined == {"timezone_mismatch": 2}


def test_missing_response_timezone_quarantines_every_day():
    report = ValidationReport("t")
    assert _archive(_resp(["2020-01-01"], [1.0], [0.0], [1.0], tz=None), report) == []
    assert report.quarantined == {"timezone_mismatch": 1}


def _row(tmax: float | None, kind: str, day: date = date(2026, 9, 20), bucket: int = 1) -> DayRow:
    return DayRow(bucket, TZ, day, tmax, -1.0, 0.0, 0.0, 5.0, 9.0, True, kind, "m",
                  "open_meteo_forecast" if kind != "era5" else "open_meteo_archive")


async def _run(url: str, steps, query: str):
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            counts = [await upsert_rows(conn, rows, run_id=uuid.uuid4()) for rows in steps]
            result = await conn.execute(text(query))
            return counts, [tuple(r) for r in result.all()]
    finally:
        await engine.dispose()


@requires_pg
def test_precedence_era5_over_stopgap_over_forecast():
    steps = [[_row(t, k)] for t, k in [(10.0, "forecast"), (11.0, "stopgap"), (99.0, "forecast"), (12.0, "era5"),
                                       (98.0, "stopgap"), (97.0, "forecast")]]
    with migrated_db() as name:
        counts, rows = asyncio.run(_run(sa_url(name), steps,
                                        "SELECT tmax, record_kind, is_forecast, source FROM cell_daily_conditions"))
    # The returned count is what the WHERE let through, not len(rows).
    assert counts == [1, 1, 0, 1, 0, 0]
    assert rows == [(12.0, "era5", False, "open_meteo_archive")]


@requires_pg
def test_stopgap_replaces_forecast_but_not_the_reverse():
    steps = [[_row(10.0, "forecast")], [_row(11.0, "stopgap")], [_row(99.0, "forecast")]]
    with migrated_db() as name:
        counts, rows = asyncio.run(_run(sa_url(name), steps, "SELECT tmax, record_kind FROM cell_daily_conditions"))
    assert counts == [1, 1, 0]
    assert rows == [(11.0, "stopgap")]


@requires_pg
def test_equal_rank_replaces_and_era5_nulls_are_stored_as_null():
    # A newer forecast replaces an older one; an era5 day with a missing value replaces a stopgap
    # day with that value present — the stopgap value must not survive as if ERA5 measured it.
    steps = [[_row(10.0, "forecast")], [_row(13.0, "forecast")], [_row(7.0, "stopgap", day=date(2026, 9, 21))],
             [_row(None, "era5", day=date(2026, 9, 21))]]
    with migrated_db() as name:
        counts, rows = asyncio.run(_run(sa_url(name), steps,
                                        "SELECT date, tmax, record_kind FROM cell_daily_conditions ORDER BY date"))
    assert counts == [1, 1, 1, 1]
    assert rows == [(date(2026, 9, 20), 13.0, "forecast"), (date(2026, 9, 21), None, "era5")]


@requires_pg
def test_upsert_leaves_other_plans_columns_alone():
    async def scenario(url: str):
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await upsert_rows(conn, [_row(10.0, "stopgap")], run_id=uuid.uuid4())
                await conn.execute(text("UPDATE cell_daily_conditions SET aqi = 42, nws_alert_codes = ARRAY['WSW'], "
                                        "swe_delta_mm = 3.5, snow_depth_cm = 20"))
                await upsert_rows(conn, [_row(12.0, "era5")], run_id=uuid.uuid4())
                result = await conn.execute(text(
                    "SELECT tmax, aqi, nws_alert_codes, swe_delta_mm, snow_depth_cm FROM cell_daily_conditions"))
                return [tuple(r) for r in result.all()]
        finally:
            await engine.dispose()

    with migrated_db() as name:
        assert asyncio.run(scenario(sa_url(name))) == [(12.0, 42, ["WSW"], 3.5, 20.0)]


@requires_pg
def test_large_batch_crosses_the_parameter_limit_and_rewrites_idempotently(monkeypatch):
    # 2,500 rows x 14 bind params = 35,000 > asyncpg's 32,767: one unchunked statement would fail.
    # A small chunk size also exercises the remainder chunk (2,500 = 3 x 700 + 400).
    monkeypatch.setattr(cell_conditions, "CHUNK_ROWS", 700)
    start = date(2015, 1, 1)
    rows = [
        DayRow(b, TZ, start + timedelta(days=i), None if i % 7 == 0 else 5.0, -2.0, None, 0.0, 4.0, None,
               None if i % 7 == 0 else True, "era5", "era5_seamless", "open_meteo_archive")
        for b in (1, 2) for i in range(1250)
    ]
    query = ("SELECT count(*), count(tmax), count(precip_mm), count(gust_max_ms), count(DISTINCT run_id), "
             "bool_and(NOT is_forecast) FROM cell_daily_conditions")
    with migrated_db() as name:
        counts, [summary] = asyncio.run(_run(sa_url(name), [rows, rows], query))
    # The identical re-write touches nothing, so run_id stays the first run's.
    assert counts == [2500, 0]
    assert summary == (2500, 2500 - 2 * 179, 0, 0, 1, True)


@requires_pg
def test_identical_equal_rank_rewrite_writes_nothing_but_a_changed_value_does():
    async def scenario(url: str):
        engine = create_async_engine(url)
        first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        try:
            async with engine.begin() as conn:
                base = [_row(10.0, "era5"), _row(None, "era5", day=date(2026, 9, 21))]
                counts = [await upsert_rows(conn, base, run_id=first)]
                stamp = text("SELECT date, tmax, run_id, fetched_at FROM cell_daily_conditions ORDER BY date")
                before = [tuple(r) for r in (await conn.execute(stamp)).all()]
                counts.append(await upsert_rows(conn, base, run_id=second))
                unchanged = [tuple(r) for r in (await conn.execute(stamp)).all()]
                changed = [DayRow(1, TZ, date(2026, 9, 21), None, -1.0, 0.0, 0.0, 5.0, 9.5, True, "era5", "m",
                                  "open_meteo_archive"), base[0]]
                counts.append(await upsert_rows(conn, changed, run_id=third))
                after = [tuple(r) for r in (await conn.execute(stamp)).all()]
                return counts, before, unchanged, after, (first, third)
        finally:
            await engine.dispose()

    with migrated_db() as name:
        counts, before, unchanged, after, (first, third) = asyncio.run(scenario(sa_url(name)))
    # A NULL-to-NULL value counts as identical (IS DISTINCT FROM), not as a change.
    assert counts == [2, 0, 1]
    assert unchanged == before
    assert [r[2] for r in after] == [first, third]
    assert after[0] == before[0] and after[1][1:3] == (None, third)


# One stored column is moved off the incoming row's value at equal rank; re-upserting the row must
# see the difference and write it back. record_kind and is_forecast are tied to the rank by the CHECKs
# (no CHECK-valid equal-rank change exists), so those two cases drop the tying CHECKs in the throwaway
# DB: the guard's SQL is what is under test, and it must still list them.
_CHANGE_CASES = [
    ("tmax", "era5", "11.0", ()),
    ("tmin", "era5", "-2.0", ()),
    ("precip_mm", "era5", "1.5", ()),
    ("snowfall_cm", "era5", "2.0", ()),
    ("wind_max_ms", "era5", "6.0", ()),
    ("gust_max_ms", "era5", "9.5", ()),
    ("freeze_thaw", "era5", "false", ()),
    ("model", "era5", "'m2'", ()),
    ("source", "era5", "'open_meteo_forecast'", ()),
    ("record_kind", "forecast", "'forecast_v0'",
     ("cell_daily_conditions_record_kind_check", "cell_daily_conditions_is_forecast_check")),
    ("is_forecast", "forecast", "false", ("cell_daily_conditions_is_forecast_check",)),
]


def test_change_cases_cover_every_value_column():
    assert sorted(c for c, *_ in _CHANGE_CASES) == sorted(cell_conditions._VALUE_COLUMNS)


@requires_pg
@pytest.mark.parametrize("column,kind,stored,drop_checks", _CHANGE_CASES, ids=[c[0] for c in _CHANGE_CASES])
def test_equal_rank_change_in_any_single_value_column_is_written(column, kind, stored, drop_checks):
    async def scenario(url: str):
        engine = create_async_engine(url)
        base, rewrite = uuid.uuid4(), uuid.uuid4()
        try:
            async with engine.begin() as conn:
                for check in drop_checks:
                    await conn.execute(text(f"ALTER TABLE cell_daily_conditions DROP CONSTRAINT {check}"))
                row = _row(10.0, kind)
                counts = [await upsert_rows(conn, [row], run_id=base)]
                await conn.execute(text(f"UPDATE cell_daily_conditions SET {column} = {stored}"))
                counts.append(await upsert_rows(conn, [row], run_id=rewrite))
                result = await conn.execute(text(f"SELECT {column}, run_id FROM cell_daily_conditions"))
                expected = {"is_forecast": kind == "forecast", "record_kind": row.record_kind}.get(
                    column, getattr(row, column, None))
                return counts, [tuple(r) for r in result.all()], expected, rewrite
        finally:
            await engine.dispose()

    with migrated_db() as name:
        counts, rows, expected, rewrite = asyncio.run(scenario(sa_url(name)))
    assert counts == [1, 1]
    assert rows == [(expected, rewrite)]


@requires_pg
def test_duplicate_keys_in_one_call_are_rejected_before_writing():
    async def scenario(url: str):
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                with pytest.raises(ValueError, match="duplicate"):
                    await upsert_rows(conn, [_row(1.0, "era5"), _row(2.0, "stopgap")], run_id=uuid.uuid4())
                return (await conn.execute(text("SELECT count(*) FROM cell_daily_conditions"))).scalar_one()
        finally:
            await engine.dispose()

    with migrated_db() as name:
        assert asyncio.run(scenario(sa_url(name))) == 0


def test_empty_upsert_touches_nothing():
    class NoConn:
        async def execute(self, *a, **k):
            raise AssertionError("no statement for an empty batch")

    assert asyncio.run(upsert_rows(NoConn(), [], run_id=uuid.uuid4())) == 0


@requires_pg
@pytest.mark.parametrize("column", cell_conditions._WEATHER_COLUMNS)
def test_an_equal_rank_re_fetch_never_erases_a_stored_value(column):
    async def scenario(url: str):
        engine = create_async_engine(url)
        first, nulled, changed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        other, value = ("tmax", 12.0) if column == "tmin" else ("tmin", -4.0)
        try:
            async with engine.begin() as conn:
                row = _row(10.0, "era5")
                await upsert_rows(conn, [row], run_id=first)
                stamp = text(f"SELECT {column}, {other}, run_id FROM cell_daily_conditions")
                before = tuple((await conn.execute(stamp)).one())
                n_null = await upsert_rows(conn, [dataclasses.replace(row, **{column: None, other: value})],
                                           run_id=nulled)
                kept = tuple((await conn.execute(stamp)).one())
                n_changed = await upsert_rows(conn, [dataclasses.replace(row, **{other: value})], run_id=changed)
                after = tuple((await conn.execute(stamp)).one())
                return before, (n_null, kept), (n_changed, after), (first, changed, value)
        finally:
            await engine.dispose()

    with migrated_db() as name:
        before, (n_null, kept), (n_changed, after), (first, changed, value) = asyncio.run(scenario(sa_url(name)))
    # The whole row is kept, the other column's real change included: no mixing of fetches in one row.
    assert (n_null, kept) == (0, before) and before[2] == first
    assert n_changed == 1 and after[1:] == (value, changed)
