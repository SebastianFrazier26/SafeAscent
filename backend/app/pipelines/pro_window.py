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

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

PRO_WINDOW_DAYS = 30
JANUARY_EARLIEST_DAY = 10
PAID_SOURCES = ("era5_fill", "era5_normals")
WINDOW_UNITS = 5_000_000
PLAN_STOP_UNITS = 4_500_000

Clock = Callable[[], datetime]


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
    a run that died after its request went out."""
    start = datetime.combine(window.start, time.min, tzinfo=timezone.utc)
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
        if self.max_units > WINDOW_UNITS:
            raise ValueError(f"max_units {self.max_units} exceeds the paid month's {WINDOW_UNITS} units")

    def fits(self, units: float) -> bool:
        return self.spent + units <= self.max_units

    def spend(self, units: float) -> None:
        self.spent += units
