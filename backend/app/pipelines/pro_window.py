"""The Open-Meteo Professional window (owner decision 2026-09-28). The archive API needs
Professional; the owner buys one 30-day Professional month per year, started on or after
10 January so the whole previous year is past ERA5's lag. The first window (the initial
backfill, owner OQ1) may start on any day. --max-units is the window's cumulative cap, read
back from source_ingest_log, so reruns and slices can never add up past it.

A paid month is 5M units. The plan gate stops above PLAN_STOP_UNITS before anything is bought.
The cap is per database: a rehearsal branch's spend is logged only in the branch, so the
runbook records it and the prod cap is 4.8M minus that number.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

PRO_WINDOW_DAYS = 30
JANUARY_EARLIEST_DAY = 10
PAID_SOURCES = ("era5_fill", "era5_normals")
WINDOW_UNITS = 5_000_000
PLAN_STOP_UNITS = 4_500_000

# Arbitrary but fixed: every paid-window job in every process contends for this one key.
WINDOW_LOCK_KEY = 7_202_609_280_005

Clock = Callable[[], datetime]

_lock_held: ContextVar[bool] = ContextVar("pro_window_lock_held", default=False)


@dataclass(frozen=True)
class ProWindow:
    start: date
    initial: bool = False

    def __post_init__(self) -> None:
        if not self.initial and not (self.start.month == 1 and self.start.day >= JANUARY_EARLIEST_DAY):
            raise ValueError(f"a yearly window starts in January on or after the {JANUARY_EARLIEST_DAY}th, got {self.start}")

    @property
    def end(self) -> date:
        return self.start + timedelta(days=PRO_WINDOW_DAYS - 1)

    def contains(self, today: date) -> bool:
        return self.start <= today <= self.end


def require_open(window: ProWindow, today: date) -> None:
    if not window.contains(today):
        raise SystemExit(f"today {today} is outside the Professional window {window.start}..{window.end}; "
                         "the archive API needs Professional, so nothing is fetched")


@contextlib.asynccontextmanager
async def window_lock(engine: AsyncEngine) -> AsyncIterator[None]:
    """One paid-window job per database at a time (I1): two runs that each read the spend and
    then buy would together pass the cap. Every paid job takes this lock — era5_fill.run does,
    and normals.run and era5_window.run_window must too — and re-reads units_spent only after
    holding it. Re-entrant within one task context, so run_window can hold it around the fill.

    A session-level advisory lock on a dedicated connection kept for the whole run: it needs a
    direct (or session-pooled) connection, not a transaction-mode pooler, which would hand the
    session to someone else between statements."""
    if _lock_held.get():
        yield
        return
    async with engine.connect() as conn:
        got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": WINDOW_LOCK_KEY})).scalar_one()
        # Ends the implicit transaction; the session-level lock outlives it, and no snapshot stays open for hours.
        await conn.commit()
        if not got:
            raise SystemExit("another paid-window job holds the window lock on this database; "
                             "wait for it to finish rather than run two against one cap")
        token = _lock_held.set(True)
        try:
            yield
        finally:
            _lock_held.reset(token)
            # A dropped connection has already released the lock with its session.
            with contextlib.suppress(Exception):
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": WINDOW_LOCK_KEY})
                await conn.commit()


async def require_initial_allowed(conn: AsyncConnection) -> None:
    """The initial window is a one-off (owner OQ1: now, then January only). Once anything has
    been spent, a second initial=True window is a mistake that would open a fresh cap."""
    spent = (await conn.execute(
        text("SELECT EXISTS (SELECT 1 FROM source_ingest_log WHERE source = ANY(:sources) AND cost_units > 0)"),
        {"sources": list(PAID_SOURCES)},
    )).scalar_one()
    if spent:
        raise SystemExit("an initial window was already used (paid spend is logged); "
                         "later windows start in January on or after the 10th")


async def record_spend(conn: AsyncConnection, run_id: uuid.UUID, units: float, *, at: datetime | None) -> None:
    """Stamp a run's cost before its request goes out (D5), committed with start_run, so a run
    killed after a billed request still counts. `at` replaces the DB's started_at when the caller
    runs under an injected clock (D1): units_spent places spend by started_at, and a frozen
    January run must land in its January window, not wherever the DB's real clock is."""
    if at is not None and at.tzinfo is None:
        raise ValueError("record_spend needs a timezone-aware timestamp")
    await conn.execute(
        text("UPDATE source_ingest_log SET cost_units = :units, started_at = COALESCE(:at, started_at) "
             "WHERE run_id = :run_id"),
        {"units": units, "at": at, "run_id": run_id},
    )


async def units_spent(conn: AsyncConnection, window: ProWindow) -> float:
    """Every status counts: a failed request may still have been billed, and a 'running' row is
    a run that died after its request went out. The sum also reaches PRO_WINDOW_DAYS - 1 days
    before window.start (I2): a window re-declared with a later start in the same paid month
    must still see what that month has already spent."""
    start = datetime.combine(window.start - timedelta(days=PRO_WINDOW_DAYS - 1), time.min, tzinfo=timezone.utc)
    stop = datetime.combine(window.end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    value = (await conn.execute(
        text("SELECT COALESCE(sum(cost_units), 0) FROM source_ingest_log WHERE source = ANY(:sources) "
             "AND started_at >= :start AND started_at < :stop"),
        {"sources": list(PAID_SOURCES), "start": start, "stop": stop},
    )).scalar_one()
    return float(value)


@dataclass
class Budget:
    max_units: float
    spent: float

    def __post_init__(self) -> None:
        if self.max_units < 0 or self.spent < 0:
            raise ValueError(f"budget values cannot be negative: max_units {self.max_units}, spent {self.spent}")
        if self.max_units > WINDOW_UNITS:
            raise ValueError(f"max_units {self.max_units} exceeds the paid month's {WINDOW_UNITS} units")

    @staticmethod
    def _check(units: float) -> None:
        if units < 0:
            raise ValueError(f"units cannot be negative, got {units}")

    def fits(self, units: float) -> bool:
        self._check(units)
        return self.spent + units <= self.max_units

    def spend(self, units: float) -> None:
        self._check(units)
        self.spent += units
