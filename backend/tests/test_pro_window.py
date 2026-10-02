import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.pro_window import (
    WINDOW_UNITS,
    Budget,
    ProWindow,
    record_spend,
    require_open,
    units_spent,
)
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url


def test_yearly_windows_start_in_january_on_or_after_the_10th():
    with pytest.raises(ValueError, match="January"):
        ProWindow(date(2027, 1, 9))
    with pytest.raises(ValueError, match="January"):
        ProWindow(date(2027, 2, 10))
    assert ProWindow(date(2027, 1, 10)).end == date(2027, 2, 8)
    assert ProWindow(date(2026, 10, 5), initial=True).contains(date(2026, 11, 3))


def test_require_open_refuses_outside_the_window():
    window = ProWindow(date(2027, 1, 10))
    require_open(window, date(2027, 2, 8))
    with pytest.raises(SystemExit, match="outside"):
        require_open(window, date(2027, 2, 9))
    with pytest.raises(SystemExit, match="outside"):
        require_open(window, date(2027, 1, 9))


def test_budget_never_exceeds_the_cap():
    budget = Budget(max_units=100, spent=90)
    assert budget.fits(10) and not budget.fits(11)
    budget.spend(10)
    assert not budget.fits(1)


def test_budget_cap_cannot_exceed_the_paid_month():
    assert WINDOW_UNITS == 5_000_000
    Budget(max_units=WINDOW_UNITS, spent=0)
    with pytest.raises(ValueError, match="5000000"):
        Budget(max_units=WINDOW_UNITS + 1, spent=0)


@requires_pg
def test_units_spent_is_cumulative_across_runs():
    today = datetime.now(timezone.utc).date()
    window = ProWindow(today - timedelta(days=1), initial=True)

    async def scenario(url: str) -> float:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                for source, units in [("era5_fill", 100.0), ("era5_normals", 50.0), ("era5_fill", 7.0),
                                      ("openbeta", 999.0), ("era5_fill", 1000.0)]:
                    run_id = await start_run(conn, source=source, window_start=None, window_end=None, content_sha256=None)
                    await finish_run(conn, run_id, status="ok", report=ValidationReport(source), rows_upserted=0,
                                     cost_units=units)
                await conn.execute(text("UPDATE source_ingest_log SET started_at = now() - interval '40 days' "
                                        "WHERE cost_units = 1000"))
                return await units_spent(conn, window)
        finally:
            await engine.dispose()

    with migrated_db() as name:
        assert asyncio.run(scenario(sa_url(name))) == 157.0


@requires_pg
def test_spend_recorded_at_start_counts_even_if_the_run_never_finishes():
    # D5: a run killed after a billed request is left 'running'; its units must still count.
    today = datetime.now(timezone.utc).date()
    window = ProWindow(today - timedelta(days=1), initial=True)

    async def scenario(url: str) -> float:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                run_id = await start_run(conn, source="era5_fill", window_start=None, window_end=None,
                                         content_sha256=None)
                await record_spend(conn, run_id, 12, at=None)
            async with engine.connect() as conn:
                return await units_spent(conn, window)
        finally:
            await engine.dispose()

    with migrated_db() as name:
        assert asyncio.run(scenario(sa_url(name))) == 12.0


@requires_pg
def test_a_frozen_clock_places_spend_in_its_january_window_not_the_db_clock():
    # D1: tests (and a replay) run under an injected "now"; the DB's real clock is months away.
    frozen = datetime(2027, 1, 15, 12, tzinfo=timezone.utc)
    january = ProWindow(date(2027, 1, 10))
    real_today = datetime.now(timezone.utc).date()
    around_now = ProWindow(real_today - timedelta(days=1), initial=True)

    async def scenario(url: str) -> tuple[float, float, float]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                for at in (frozen, None):
                    run_id = await start_run(conn, source="era5_fill", window_start=None, window_end=None,
                                             content_sha256=None)
                    await record_spend(conn, run_id, 5, at=at)
                    await record_spend(conn, run_id, 5, at=at)
            async with engine.connect() as conn:
                return (await units_spent(conn, january), await units_spent(conn, around_now),
                        await units_spent(conn, ProWindow(date(2028, 1, 10))))
        finally:
            await engine.dispose()

    with migrated_db() as name:
        assert asyncio.run(scenario(sa_url(name))) == (5.0, 5.0, 0.0)


def test_record_spend_refuses_a_naive_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(record_spend(None, None, 1, at=datetime(2027, 1, 15)))  # type: ignore[arg-type]
