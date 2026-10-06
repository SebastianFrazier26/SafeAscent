"""The yearly Open-Meteo Professional window: the only code path that calls the ERA5 archive.

Standard excludes the archive API, so every ERA5 call happens inside a declared 30-day
Professional window (January, on or after the 10th; the first window may start any day).
One run, under the window lock for its whole length: register new points, prune non-incident
rows older than 3 years, fill every series' ERA5 gaps (the prior year's days replace stopgap
rows, plus a 90-day re-fetch behind each span's end; new series get 3 years; incident series
reach back to --incident-start), then rebuild normals for buckets that are pending or out of
period. --max-units caps the window's cumulative spend across runs.

Data jobs run from the CLI in the pipelines env (runbooks start them with bg_start), never
from Celery.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from collections.abc import Callable, Sequence
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines import era5_fill, normals
from app.pipelines.era5_fill import (
    Chunk,
    Gap,
    SeriesCoverage,
    history_start,
    last_era5_day,
    load_coverage,
    plan_chunks,
    series_gaps,
)
from app.pipelines.grid import grid_bucket_sql
from app.pipelines.localday import INCIDENT_FROM_WHERE_SQL, ROUTE_POINTS_SQL, register_logged
from app.pipelines.open_meteo import DAILY_VARS, ArchiveClient, cost_units
from app.pipelines.pro_window import (
    PLAN_STOP_UNITS,
    Budget,
    Clock,
    ProWindow,
    require_initial_allowed,
    require_open,
    units_spent,
    window_lock,
)

# R7 (unverified): the archive's latest months are preliminary ERA5T, replaced later by final
# ERA5. Cheap to re-buy; unchanged values are not rewritten, and a NULL never erases a stored value
# (cell_conditions' equal-rank guards).
REFRESH_DAYS = 90
# M3: consecutive failed or rejected chunks/batches before the window stops. Each failed request
# may already have been billed for every retry, so a long outage must not run down the budget.
FAILURE_BREAKER = 3

_TZ_CONDITION = "AND t.tz IS NOT NULL "
if _TZ_CONDITION not in INCIDENT_FROM_WHERE_SQL:
    raise RuntimeError("localday's incident filter changed; update the unregistered-point estimate")
_INCIDENT_POINTS_ANY_TZ_SQL = (
    f"SELECT DISTINCT a.latitude, a.longitude {INCIDENT_FROM_WHERE_SQL.replace(_TZ_CONDITION, '')}"
)


def _with_refresh(coverage: Sequence[SeriesCoverage], gaps: Sequence[Gap], window: ProWindow | None) -> list[Gap]:
    """Start each forward gap that continues a span REFRESH_DAYS back inside it (never before its
    first day), once per window (I2): only a span that ends before the window's first lag edge was
    last filled in an earlier window. A later-day rerun inside this window buys just its new days.
    No window (a plan without --window-start) refreshes every continued span, an upper bound."""
    spans = {(s.grid_bucket, s.tz): s for s in coverage}
    fresh_from = last_era5_day(window.start) if window is not None else None
    out: list[Gap] = []
    for g in gaps:
        s = spans[(g.grid_bucket, g.tz)]
        if (g.direction == "forward" and s.first is not None and s.last is not None
                and g.start == s.last + timedelta(days=1) and (fresh_from is None or s.last < fresh_from)):
            g = dataclasses.replace(g, start=max(s.first, s.last - timedelta(days=REFRESH_DAYS - 1)))
        out.append(g)
    return out


def plan_fill(
    coverage: Sequence[SeriesCoverage],
    *,
    today: date,
    incident_start: date,
    batch: int,
    years_per_chunk: int,
    window: ProWindow | None,
    refresh: bool = True,
) -> list[Chunk]:
    gaps = series_gaps(coverage, today=today, incident_start=incident_start)
    if refresh:
        gaps = _with_refresh(coverage, gaps, window)
    return plan_chunks(gaps, batch=batch, years_per_chunk=years_per_chunk)


async def plan(
    conn: AsyncConnection,
    *,
    today: date,
    incident_start: date,
    batch: int,
    years_per_chunk: int,
    window: ProWindow | None,
) -> tuple[list[Chunk], list[tuple[int, str]]]:
    chunks = plan_fill(await load_coverage(conn), today=today, incident_start=incident_start, batch=batch,
                       years_per_chunk=years_per_chunk, window=window)
    return chunks, await normals.normals_due(conn, today=today)


def _normals_units(n_buckets: int, today: date) -> int:
    # An upper bound: buckets whose stored ERA5 already covers a year derive it for free.
    start_year, end_year = normals.window(today)
    return cost_units(n_buckets, len(DAILY_VARS), (date(end_year, 12, 31) - date(start_year, 1, 1)).days + 1)


async def plan_report(
    conn: AsyncConnection,
    *,
    today: date,
    incident_start: date,
    batch: int,
    years_per_chunk: int,
    window: ProWindow | None,
) -> dict[str, object]:
    """Read-only, so run register first. Stale non-incident spans are shown as the database holds
    them; run prunes them before planning, so it never buys more fill than this reports."""
    _check_incident_start(incident_start)
    if window is not None and window.initial:
        await require_initial_allowed(conn, window)
    coverage = await load_coverage(conn)
    chunks, plain = (plan_fill(coverage, today=today, incident_start=incident_start, batch=batch,
                               years_per_chunk=years_per_chunk, window=window, refresh=r) for r in (True, False))
    due = await normals.normals_due(conn, today=today)
    spent = await units_spent(conn, window) if window is not None else None
    fill_units = sum(c.units() for c in chunks)
    normals_units = _normals_units(len(due), today)
    incident_new, route_new = await _unregistered_buckets(conn)
    end = last_era5_day(today)
    unregistered_units = (
        cost_units(incident_new, len(DAILY_VARS), max(0, (end - min(incident_start, history_start(today))).days + 1))
        + cost_units(route_new, len(DAILY_VARS), max(0, (end - history_start(today)).days + 1))
        + _normals_units(incident_new + route_new, today)
    )
    total = fill_units + normals_units + unregistered_units + (spent or 0)
    report: dict[str, object] = {
        "mode": "plan", "chunks": len(chunks), "fill_units": fill_units,
        "refresh_units": fill_units - sum(c.units() for c in plain), "normals_buckets": len(due),
        "normals_units": normals_units, "unregistered_incident_buckets": incident_new,
        "unregistered_route_buckets": route_new, "unregistered_units_estimate": unregistered_units,
        "window_units_spent": spent, "total_units": total, "plan_stop_units": PLAN_STOP_UNITS,
        "over_plan_stop": total > PLAN_STOP_UNITS,
    }
    if incident_new or route_new:
        report["warning"] = (f"{incident_new + route_new} point buckets are not registered. "
                             "total_units includes an estimate for them "
                             "(run `register` first for an exact plan)")
    return report


async def _unregistered_buckets(conn: AsyncConnection) -> tuple[int, int]:
    """(incident, route-only) buckets with no series yet. Counted before register has assigned
    accident zones, so the incident side drops the tz condition; points register will quarantine
    (no zone, outside the US) are counted too. This is an estimate (run `register` first for an
    exact plan), not a bound: a new incident in a route-only bucket, or a bucket that straddles a
    timezone, is undercounted."""
    grid = grid_bucket_sql("p.lat", "p.lon")
    row = (await conn.execute(text(
        f"WITH inc AS (SELECT DISTINCT {grid} AS b FROM ({_INCIDENT_POINTS_ANY_TZ_SQL}) p(lat, lon)), "
        f"rte AS (SELECT DISTINCT {grid} AS b FROM ({ROUTE_POINTS_SQL}) p(lat, lon)), "
        "reg AS (SELECT DISTINCT grid_bucket AS b FROM grid_bucket_series) "
        "SELECT (SELECT count(*) FROM inc WHERE b NOT IN (SELECT b FROM reg)), "
        "(SELECT count(*) FROM rte WHERE b NOT IN (SELECT b FROM reg) AND b NOT IN (SELECT b FROM inc))"
    ))).one()
    return int(row[0]), int(row[1])


def _check_incident_start(incident_start: date) -> None:
    # Incident series exist only for incidents from localday.INCIDENT_START (owner DP1), so any
    # day before INCIDENT_START_DEFAULT is bought for no incident.
    if incident_start < era5_fill.INCIDENT_START_DEFAULT:
        raise SystemExit(f"--incident-start {incident_start} is before {era5_fill.INCIDENT_START_DEFAULT}, "
                         "the earliest day an incident series needs")


def archive_client(api_key: str | None) -> ArchiveClient:
    # ArchiveClient's own refusal is an OpenMeteoError; a paid CLI run must stop cleanly before
    # touching the database.
    if not (api_key or "").strip():
        raise SystemExit("OPEN_METEO_API_KEY is not set; the archive needs the Professional key, "
                         "and the public host is non-commercial")
    return ArchiveClient(api_key)


async def _prune(engine: AsyncEngine, today: date) -> dict[str, object]:
    try:
        async with engine.begin() as conn:
            kept, deleted = await era5_fill.prune(conn, today=today)
            await era5_fill.log_prune(conn, status="ok", kept=kept, deleted=deleted, problem=None)
        return {"incident_series_kept": kept, "deleted_rows": deleted}
    except era5_fill.PruneRefused as exc:
        async with engine.begin() as conn:
            await era5_fill.log_prune(conn, status="rejected", kept=exc.kept, deleted=0, problem=str(exc))
        return {"refused": str(exc)}


async def run_window(
    engine_factory: Callable[[], AsyncEngine],
    client: ArchiveClient,
    *,
    window: ProWindow,
    today: date,
    max_units: int,
    batch: int,
    years_per_chunk: int,
    incident_start: date,
    clock: Clock | None = None,
) -> dict[str, object]:
    require_open(window, today)
    _check_incident_start(incident_start)
    engine = engine_factory()
    result: dict[str, object] = {"window": [window.start.isoformat(), window.end.isoformat()], "max_units": max_units}
    try:
        async with window_lock(engine):
            async with engine.connect() as conn:
                if window.initial:
                    await require_initial_allowed(conn, window)
                spent_before = await units_spent(conn, window)
            budget = Budget(max_units=max_units, spent=spent_before)
            result |= {"units_spent_before": spent_before, "register": await register_logged(engine)}
            # M7: before planning, so a stale non-incident span is planned as the 3 years it keeps.
            result["prune"] = pruned = await _prune(engine, today)
            if "refused" in pruned:
                # The incident set looks broken; buying against it could skip or misplace paid history.
                result |= {"fill": {"skipped": "prune refused"}, "normals": {"skipped": "prune refused"},
                           "units_spent_window": budget.spent}
                return result
            async with engine.connect() as conn:
                chunks, due = await plan(conn, today=today, incident_start=incident_start, batch=batch,
                                         years_per_chunk=years_per_chunk, window=window)
            # Awaited here, never gathered: window_lock re-enters only for this task, and a child's
            # SystemExit would escape gather. Fill first, so normals derive from fresh ERA5.
            fill = await era5_fill.run(engine_factory, client, chunks, today=today, budget=budget, window=window,
                                       clock=clock, breaker=FAILURE_BREAKER)
            result["fill"] = fill
            if fill["stopped_after_failures"]:
                result["normals"] = {"skipped": "fill stopped after consecutive failures"}
            else:
                result["normals"] = await normals.run(engine_factory, client, due, today=today, batch=batch,
                                                      budget=budget, pro_window=window, clock=clock,
                                                      breaker=FAILURE_BREAKER)
            result["units_spent_window"] = budget.spent
    finally:
        await engine.dispose()
    return result


def exit_status(result: dict[str, object]) -> int:
    """1 when any chunk or batch failed or was rejected, a breaker stopped the window, the window
    closed mid-run, or the prune refused."""
    for part in ("fill", "normals"):
        value = result.get(part)
        if isinstance(value, dict) and (value.get("failed") or value.get("rejected")
                                        or value.get("stopped_after_failures") or value.get("stopped_window_closed")):
            return 1
    pruned = result.get("prune")
    return 1 if isinstance(pruned, dict) and "refused" in pruned else 0


async def _main(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    from app.config import settings
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    today = utc_today()
    try:
        incident_start = date.fromisoformat(args.incident_start)
        window = ProWindow(date.fromisoformat(args.window_start), initial=args.initial_window) if args.window_start else None
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    if args.command == "register":
        engine = ingest_engine()
        try:
            return await register_logged(engine), 0
        finally:
            await engine.dispose()
    if args.command == "plan":
        engine = ingest_engine()
        try:
            async with engine.connect() as conn:
                report = await plan_report(conn, today=today, incident_start=incident_start, batch=args.batch,
                                           years_per_chunk=args.years_per_chunk, window=window)
        finally:
            await engine.dispose()
        return report, 1 if report["over_plan_stop"] else 0
    if window is None:
        raise SystemExit("run needs --window-start")
    require_open(window, today)
    with archive_client(settings.OPEN_METEO_API_KEY) as client:
        result = await run_window(ingest_engine, client, window=window, today=today, max_units=args.max_units,
                                  batch=args.batch, years_per_chunk=args.years_per_chunk,
                                  incident_start=incident_start)
    return result, exit_status(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["register", "plan", "run"])
    parser.add_argument("--window-start")
    parser.add_argument("--initial-window", action="store_true")
    parser.add_argument("--max-units", type=int, default=0)
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--years-per-chunk", type=int, default=5)
    parser.add_argument("--incident-start", default=era5_fill.INCIDENT_START_DEFAULT.isoformat())
    return parser


if __name__ == "__main__":
    output, status = asyncio.run(_main(build_parser().parse_args()))
    print(json.dumps(output, sort_keys=True, default=str))
    sys.exit(status)
