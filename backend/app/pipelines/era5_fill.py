"""ERA5 archive fill per (grid_bucket, tz) series.

Each series keeps one contiguous ERA5 span [first_era5_date, last_era5_date]. Gaps before
the span are filled newest-first and gaps after it oldest-first, so the span stays
contiguous; a failed chunk blocks the rest of its group rather than leaving a hole. Resume
needs no log lookup: the next run recomputes the gaps from the span, so a unit is never
bought twice for the same day. Replacing stopgap rows is just the forward gap.

Whether a series is an incident series is recomputed from the accidents every run: a series
whose incident point moved away is an ordinary 3-year series again (pruned, never back-filled).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.cell_conditions import DayRow, day_rows, upsert_rows
from app.pipelines.grid import bucket_center, grid_bucket_sql
from app.pipelines.ingest_log import RunStatus, finish_run, last_ok_rows_in, sha256_rows, start_run, write_quarantine
from app.pipelines.localday import INCIDENT_FROM_WHERE_SQL, INCIDENT_START
from app.pipelines.open_meteo import (
    ARCHIVE_MODEL,
    DAILY_VARS,
    ArchiveClient,
    AttemptOutcome,
    Location,
    LocationResponse,
    OpenMeteoError,
    cost_units,
)
from app.pipelines.pro_window import Budget, Clock, ProWindow, record_spend, units_spent, window_lock
from app.pipelines.validate import ValidationReport, batch_gate

SOURCE = "era5_fill"
PRUNE_SOURCE = "era5_prune"
MAX_INCIDENT_DROP = 0.10
# The range checks are physical limits, so more than 1% of one location's days in a chunk failing
# them points at a broken response rather than weather: the whole chunk is written nowhere.
MAX_QUARANTINE_SHARE = 0.01
ERA5_START = date(1940, 1, 1)
# Owner OQ2 (2026-10-01): incidents from 1990, and day offsets -6..0 make the first 1990
# incident's 7-day window complete only if the series starts six days earlier.
INCIDENT_START_DEFAULT = INCIDENT_START - timedelta(days=6)
ERA5_LAG_DAYS = 5
HISTORY_YEARS = 3

Direction = Literal["forward", "backward"]


@dataclass(frozen=True)
class SeriesCoverage:
    grid_bucket: int
    tz: str
    first: date | None
    last: date | None
    incident: bool


@dataclass(frozen=True)
class Gap:
    grid_bucket: int
    tz: str
    start: date
    end: date
    direction: Direction


@dataclass(frozen=True)
class Chunk:
    tz: str
    buckets: tuple[int, ...]
    start: date
    end: date
    group: str

    def units(self) -> int:
        return cost_units(len(self.buckets), len(DAILY_VARS), (self.end - self.start).days + 1)


def last_era5_day(today: date) -> date:
    return today - timedelta(days=ERA5_LAG_DAYS)


def history_start(today: date, history_years: int = HISTORY_YEARS) -> date:
    return date(today.year - history_years, 1, 1)


def series_gaps(
    coverage: Sequence[SeriesCoverage], *, today: date, incident_start: date, history_years: int = HISTORY_YEARS
) -> list[Gap]:
    end = last_era5_day(today)
    base = history_start(today, history_years)
    gaps: list[Gap] = []
    for s in coverage:
        need = min(incident_start, base) if s.incident else base
        if s.first is None or s.last is None:
            # D14: recent years first, then back from them; both halves stay adjacent to the
            # span whichever completes first, so the span stays contiguous.
            if base <= end:
                gaps.append(Gap(s.grid_bucket, s.tz, base, end, "forward"))
            if need < base:
                gaps.append(Gap(s.grid_bucket, s.tz, need, base - timedelta(days=1), "backward"))
            continue
        if need < s.first:
            gaps.append(Gap(s.grid_bucket, s.tz, need, s.first - timedelta(days=1), "backward"))
        if s.last < end:
            gaps.append(Gap(s.grid_bucket, s.tz, s.last + timedelta(days=1), end, "forward"))
    return gaps


def _blocks(start: date, end: date, direction: Direction, years: int) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    if direction == "forward":
        s = start
        while s <= end:
            e = min(date(s.year + years - 1, 12, 31), end)
            out.append((s, e))
            s = e + timedelta(days=1)
    else:
        e = end
        while e >= start:
            s = max(date(e.year - years + 1, 1, 1), start)
            out.append((s, e))
            e = s - timedelta(days=1)
    return out


def plan_chunks(gaps: Sequence[Gap], *, batch: int, years_per_chunk: int) -> list[Chunk]:
    groups: dict[tuple[str, date, date, Direction], set[int]] = defaultdict(set)
    for g in gaps:
        groups[(g.tz, g.start, g.end, g.direction)].add(g.grid_bucket)
    chunks: list[Chunk] = []
    # D14: every forward group before any backward one, so a budget stop leaves recent years bought.
    for key in sorted(groups, key=lambda k: (k[3] != "forward", k)):
        tz, start, end, direction = key
        ordered = sorted(groups[key])
        for i in range(0, len(ordered), batch):
            buckets = tuple(ordered[i : i + batch])
            group = f"{tz}|{start}|{end}|{direction}|{buckets[0]}"
            chunks.extend(Chunk(tz, buckets, s, e, group) for s, e in _blocks(start, end, direction, years_per_chunk))
    return chunks


def _incident_keys_sql() -> str:
    grid = grid_bucket_sql("a.latitude", "a.longitude")
    return f"SELECT DISTINCT {grid} AS grid_bucket, t.tz {INCIDENT_FROM_WHERE_SQL}"


async def load_coverage(conn: AsyncConnection) -> list[SeriesCoverage]:
    rows = (await conn.execute(text(
        f"WITH inc AS ({_incident_keys_sql()}) "
        "SELECT s.grid_bucket, s.tz, s.first_era5_date, s.last_era5_date, "
        "EXISTS (SELECT 1 FROM inc WHERE inc.grid_bucket = s.grid_bucket AND inc.tz = s.tz) AS incident "
        "FROM grid_bucket_series s ORDER BY s.grid_bucket, s.tz"
    ))).all()
    return [SeriesCoverage(int(b), str(tz), f, last, bool(inc)) for b, tz, f, last, inc in rows]


COVERAGE_UPDATE = text(
    "UPDATE grid_bucket_series SET first_era5_date = LEAST(COALESCE(first_era5_date, :s), :s), "
    "last_era5_date = GREATEST(COALESCE(last_era5_date, :e), :e) WHERE tz = :tz AND grid_bucket = ANY(:buckets)"
)


def _incomplete(responses: Sequence[LocationResponse], chunk: Chunk) -> bool:
    # The span is extended to chunk.end, so a day the response left out would never be bought.
    want = (chunk.end - chunk.start).days + 1
    return any(len(r.daily.time) != want for r in responses)


class _BudgetExhausted(Exception):
    pass


class _Abandoned(Exception):
    pass


# Bounds the fetch thread's wait for its pre-send record; an unrecorded retry is never sent.
RECORD_TIMEOUT_S = 60.0


async def _write_chunk(
    engine: AsyncEngine,
    chunk: Chunk,
    run_id: uuid.UUID,
    responses: Sequence[LocationResponse],
    *,
    today: date,
    units: float,
    problem: str | None,
) -> RunStatus:
    report = ValidationReport(SOURCE)
    rows: list[DayRow] = []
    failed = problem is not None
    problems: list[str] = [problem] if problem else []
    if problem is None:
        for b, response in zip(chunk.buckets, responses):
            location = ValidationReport(SOURCE)
            rows.extend(day_rows(b, chunk.tz, response, today=today, origin="archive", model=ARCHIVE_MODEL,
                                 source="open_meteo_archive", report=location))
            if location.quarantined.get("timezone_mismatch"):
                failed = True
                problems.append(f"bucket {b}: response timezone differs from the series timezone")
            else:
                # M4: per location, so one broken series cannot hide inside a large batch. Chunk
                # sizes vary with the gap, so there is no row-count baseline to compare against.
                problems.extend(f"bucket {b}: {p}" for p in batch_gate(
                    location, previous_rows_in=None, count_tolerance=0.0, max_quarantine_share=MAX_QUARANTINE_SHARE))
            report.rows_in += location.rows_in
            report.accepted += location.accepted
            report.quarantined.update(location.quarantined)
            report.issues.extend(location.issues)
    status: RunStatus = "failed" if failed else "rejected" if problems else "ok"
    async with engine.begin() as conn:
        await write_quarantine(conn, run_id, report)
        n = 0
        if status == "ok":
            n = await upsert_rows(conn, rows, run_id=run_id)
            # Quarantined days (at most 1% per location) stay inside the span: they are missing for
            # good, never zero and never re-bought, which the quarantine rows record.
            await conn.execute(COVERAGE_UPDATE, {"s": chunk.start, "e": chunk.end, "tz": chunk.tz,
                                                 "buckets": list(chunk.buckets)})
        await finish_run(conn, run_id, status=status, report=report, rows_upserted=n,
                         problems=problems or None, cost_units=units)
    return status


async def run(
    engine_factory: Callable[[], AsyncEngine],
    client: ArchiveClient,
    chunks: Sequence[Chunk],
    *,
    today: date,
    budget: Budget,
    window: ProWindow,
    clock: Clock | None = None,
) -> dict[str, object]:
    """Buy chunks in order until the budget would be exceeded, under the window lock with the
    window's spend re-read once the lock is held. Each chunk's writes, quarantine rows and final
    log status commit in one transaction; a failed or rejected chunk writes no conditions rows
    and blocks the rest of its group. `clock` stamps the spend (see pro_window.record_spend);
    None keeps the DB's own time."""
    engine = engine_factory()
    loop = asyncio.get_running_loop()
    done = failed = rejected = blocked = 0
    stopped = False
    failed_groups: set[str] = set()
    try:
        async with window_lock(engine):
            async with engine.connect() as conn:
                budget.spent = max(budget.spent, await units_spent(conn, window))
            for chunk in chunks:
                if chunk.group in failed_groups:
                    blocked += 1
                    continue
                # The lock's session can drop silently mid-run, releasing it to another job; re-reading
                # the logged spend before every chunk still holds both jobs to the one cap.
                async with engine.connect() as conn:
                    budget.spent = max(budget.spent, await units_spent(conn, window))
                units = chunk.units()
                if not budget.fits(units):
                    stopped = True
                    break
                at = clock() if clock else None
                sha = sha256_rows([(chunk.tz,)] + [(b,) for b in chunk.buckets])
                # Deliberately not one transaction with finish_run (plan 1's contract): the run row and
                # its cost commit before the billed request, so a kill mid-chunk still counts (D5).
                async with engine.begin() as conn:
                    run_id = await start_run(conn, source=SOURCE, window_start=chunk.start, window_end=chunk.end,
                                             content_sha256=sha)
                    await record_spend(conn, run_id, units, at=at)
                budget.spend(units)
                billed: float = units
                stop = threading.Event()

                async def _record(total: float, run_id: uuid.UUID = run_id, at: datetime | None = at) -> None:
                    async with engine.begin() as conn:
                        await record_spend(conn, run_id, total, at=at)

                def on_attempt(attempt: int, previous: AttemptOutcome | None, units: float = units,
                               stop: threading.Event = stop) -> None:
                    # I3: a 5xx or a timeout may have been computed and billed, so the retry is paid for
                    # (and recorded) before it is sent. A 429 was refused before any work: the attempt
                    # already counted for it carries over to its retry.
                    nonlocal billed
                    # N2: once the awaiting coroutine is gone (cancelled, Ctrl-C) the lock is released, so
                    # this orphaned thread must not send anything more.
                    if stop.is_set():
                        raise _Abandoned()
                    if attempt == 0 or previous == "rate_limited":
                        return
                    if not budget.fits(units):
                        raise _BudgetExhausted()
                    budget.spend(units)
                    billed += units
                    asyncio.run_coroutine_threadsafe(_record(billed), loop).result(RECORD_TIMEOUT_S)
                    # Recording waits on the loop; a cancel can land meanwhile.
                    if stop.is_set():
                        raise _Abandoned()

                problem: str | None = None
                responses: list[LocationResponse] = []
                try:
                    # In a thread: the client is synchronous, and on_attempt's DB write needs the loop free.
                    responses = await asyncio.to_thread(
                        client.fetch, [Location(b, *bucket_center(b), chunk.tz) for b in chunk.buckets],
                        chunk.start, chunk.end, on_attempt=on_attempt,
                    )
                except OpenMeteoError as exc:
                    problem = str(exc)
                except _BudgetExhausted:
                    problem, stopped = "budget exhausted before a retry could be sent", True
                finally:
                    stop.set()
                if problem is None and _incomplete(responses, chunk):
                    problem = f"response does not cover every day of {chunk.start}..{chunk.end}"
                status = await _write_chunk(engine, chunk, run_id, responses, today=today, units=billed,
                                            problem=problem)
                if status == "ok":
                    done += 1
                else:
                    failed_groups.add(chunk.group)
                    if status == "failed":
                        failed += 1
                    else:
                        rejected += 1
                if stopped:
                    break
    finally:
        await engine.dispose()
    return {"chunks": len(chunks), "done": done, "failed": failed, "rejected": rejected,
            "blocked_after_failure": blocked, "stopped_for_budget": stopped}


class PruneRefused(Exception):
    def __init__(self, message: str, kept: int) -> None:
        super().__init__(message)
        self.kept = kept


async def prune(conn: AsyncConnection, *, today: date, history_years: int = HISTORY_YEARS) -> tuple[int, int]:
    """Delete non-incident rows older than the 3-year window; return (incident series kept, rows deleted).

    The incident set decides what survives, so an empty set or one that shrank sharply since
    the last successful prune (a broken view, a lost tz backfill) would delete paid incident
    history: refuse instead, before any row is touched. Runs on the caller's transaction; the
    caller logs the outcome with log_prune in that same transaction."""
    cutoff = history_start(today, history_years)
    inc = _incident_keys_sql()
    kept = int((await conn.execute(text(f"SELECT count(*) FROM ({inc}) k"))).scalar_one())
    previous = await last_ok_rows_in(conn, PRUNE_SOURCE)
    if kept == 0:
        raise PruneRefused("no incident series found; refusing to prune", kept)
    if previous and kept < (1 - MAX_INCIDENT_DROP) * previous:
        raise PruneRefused(f"incident series dropped from {previous} to {kept}; refusing to prune", kept)
    deleted = (await conn.execute(text(
        f"WITH inc AS ({inc}) DELETE FROM cell_daily_conditions c WHERE c.date < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM inc WHERE inc.grid_bucket = c.grid_bucket AND inc.tz = c.tz)"
    ), {"cutoff": cutoff})).rowcount
    await conn.execute(text(
        f"WITH inc AS ({inc}) UPDATE grid_bucket_series s SET "
        "first_era5_date = CASE WHEN s.last_era5_date >= :cutoff THEN :cutoff END, "
        "last_era5_date = CASE WHEN s.last_era5_date >= :cutoff THEN s.last_era5_date END "
        "WHERE s.first_era5_date < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM inc WHERE inc.grid_bucket = s.grid_bucket AND inc.tz = s.tz)"
    ), {"cutoff": cutoff})
    return kept, int(deleted)


async def log_prune(conn: AsyncConnection, *, status: RunStatus, kept: int, deleted: int, problem: str | None) -> None:
    report = ValidationReport(PRUNE_SOURCE)
    report.rows_in = kept
    run_id = await start_run(conn, source=PRUNE_SOURCE, window_start=None, window_end=None, content_sha256=None)
    await finish_run(conn, run_id, status=status, report=report, rows_upserted=deleted,
                     problems=[problem] if problem else None)
