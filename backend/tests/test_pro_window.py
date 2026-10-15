import asyncio
import threading
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
    require_initial_allowed,
    require_open,
    units_spent,
    window_lock,
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


@pytest.mark.parametrize("kwargs", [dict(max_units=-1, spent=0), dict(max_units=10, spent=-1)])
def test_budget_rejects_negative_values(kwargs):
    with pytest.raises(ValueError, match="negative"):
        Budget(**kwargs)


def test_budget_rejects_negative_units():
    budget = Budget(max_units=10, spent=0)
    with pytest.raises(ValueError, match="negative"):
        budget.fits(-1)
    with pytest.raises(ValueError, match="negative"):
        budget.spend(-1)


async def _spend_at(conn, source: str, units: float, at: datetime | None) -> None:
    run_id = await start_run(conn, source=source, window_start=None, window_end=None, content_sha256=None)
    await record_spend(conn, run_id, units, at=at)


@requires_pg
def test_a_window_whose_start_moved_later_still_sees_the_month_already_spent():
    # I2: one paid month bought on 10 Jan; a run declaring the window from 25 Jan must not get a fresh 5M.
    async def scenario(url: str) -> tuple[float, float, float]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await _spend_at(conn, "era5_fill", 100, datetime(2027, 1, 12, tzinfo=timezone.utc))
                await _spend_at(conn, "era5_normals", 7, datetime(2026, 12, 11, 23, tzinfo=timezone.utc))
                await _spend_at(conn, "era5_fill", 3, datetime(2026, 12, 12, tzinfo=timezone.utc))
            async with engine.connect() as conn:
                return (await units_spent(conn, ProWindow(date(2027, 1, 25))),
                        await units_spent(conn, ProWindow(date(2027, 1, 10))),
                        await units_spent(conn, ProWindow(date(2027, 3, 11), initial=True)))
        finally:
            await engine.dispose()

    with migrated_db() as name:
        # 2027-01-10 minus 29 days is 2026-12-12: the 11 December row is outside, the 12th inside.
        assert asyncio.run(scenario(sa_url(name))) == (100.0, 103.0, 0.0)


@requires_pg
def test_initial_window_is_refused_only_after_spend_from_an_earlier_month():
    # Reruns and slices of the same initial window are fine; a later initial window is a fresh cap.
    window = ProWindow(date(2026, 10, 5), initial=True)
    earliest_same_month = datetime(2026, 9, 6, tzinfo=timezone.utc)  # window.start - 29 days

    async def scenario(url: str) -> list[str]:
        engine = create_async_engine(url)
        outcomes = []
        try:
            async with engine.begin() as conn:
                for source, at in [(None, None), ("era5_fill", datetime(2026, 10, 6, tzinfo=timezone.utc)),
                                   ("era5_normals", earliest_same_month),
                                   ("openbeta", datetime(2026, 1, 1, tzinfo=timezone.utc)),
                                   ("era5_fill", earliest_same_month - timedelta(microseconds=1))]:
                    if source:
                        await _spend_at(conn, source, 5, at)
                    try:
                        await require_initial_allowed(conn, window)
                        outcomes.append("allowed")
                    except SystemExit as exc:
                        outcomes.append(str(exc))
        finally:
            await engine.dispose()
        return outcomes

    with migrated_db() as name:
        outcomes = asyncio.run(scenario(sa_url(name)))
    assert outcomes[:4] == ["allowed"] * 4
    assert "initial" in outcomes[4]


def _other_process(url: str) -> str:
    """Try the window lock from a plain thread: an empty context, its own loop and connection, so
    to the lock it looks like another process."""
    out: list[str] = []

    async def go() -> None:
        engine = create_async_engine(url)
        try:
            async with window_lock(engine):
                out.append("acquired")
        finally:
            await engine.dispose()

    def target() -> None:
        try:
            asyncio.run(go())
        except SystemExit as exc:
            out.append(str(exc))

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    return out[0]


@requires_pg
def test_window_lock_refuses_a_second_holder_and_is_reentrant_for_its_own():
    async def scenario(url: str) -> str:
        engine = create_async_engine(url)
        try:
            async with window_lock(engine):
                async with window_lock(engine):
                    pass
                return await asyncio.to_thread(_other_process, url)
        finally:
            await engine.dispose()

    with migrated_db() as name:
        held = asyncio.run(scenario(sa_url(name)))
        after = _other_process(sa_url(name))
    assert "window lock" in held
    assert after == "acquired"


@requires_pg
def test_leaving_the_lock_releases_it_and_a_later_entry_in_the_same_task_really_locks():
    async def scenario(url: str) -> tuple[str, str]:
        engine = create_async_engine(url)
        try:
            async with window_lock(engine):
                pass
            between = await asyncio.to_thread(_other_process, url)
            async with window_lock(engine):
                during_second = await asyncio.to_thread(_other_process, url)
            return between, during_second
        finally:
            await engine.dispose()

    with migrated_db() as name:
        between, during_second = asyncio.run(scenario(sa_url(name)))
    assert between == "acquired"
    assert "window lock" in during_second


@requires_pg
def test_a_child_task_does_not_inherit_the_lock():
    # N1: gathered children copy the owner's context; only the owning task may re-enter.
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with window_lock(engine):
                async def child() -> None:
                    async with window_lock(engine):
                        pass

                await asyncio.gather(child())
        finally:
            await engine.dispose()

    with migrated_db() as name:
        # asyncio re-raises a task's SystemExit out of the loop rather than into gather.
        with pytest.raises(SystemExit, match="window lock"):
            asyncio.run(scenario(sa_url(name)))
