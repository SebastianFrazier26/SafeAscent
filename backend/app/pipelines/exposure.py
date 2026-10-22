"""Exposure snapshots (P2-9 on the MP catalog): assemble the components per area x type group x
month, write one snapshot keyed (as_of, data_cutoff), keep the newest KEEP_SNAPSHOTS production
snapshots (data_cutoff = as_of) and every backtest snapshot until dropped. Runs as ingest from the
owner's Mac after tick loads (DP8); never served by the API."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.exposure_components import blackout_series, keys, mp_ticks, route_density, scope, season
from app.pipelines.exposure_math import COMPONENTS, PROXY_VERSION, Component, bounds, exposure_point

SOURCE = "exposure"
KEEP_SNAPSHOTS = 3
INSERT_CHUNK = 5000

# F16: a plain INSERT, not ON CONFLICT DO UPDATE — build always runs CLEAR_SQL for this
# (as_of, data_cutoff) before inserting, in the same transaction, so the conflict branch could
# never fire. A leftover row now fails loudly instead of being silently merged.
INSERT_SQL = text(
    "INSERT INTO internal.exposure_index (area_id, type_group, month, n_routes, proxy, proxy_version, as_of, "
    "data_cutoff, tick_coverage, exposure_point, exposure_lo, exposure_hi, run_id) VALUES (:a, :tg, :m, :n, "
    "CAST(:p AS jsonb), :v, :d, :c, :cov, :pt, :lo, :hi, :run)")
# A rebuild of the same snapshot must not keep cells whose area left the scope.
CLEAR_SQL = text("DELETE FROM internal.exposure_index WHERE as_of = :d AND data_cutoff = :c")
PRUNE_SQL = text(
    "DELETE FROM internal.exposure_index WHERE data_cutoff = as_of AND as_of NOT IN "
    "(SELECT DISTINCT as_of FROM internal.exposure_index WHERE data_cutoff = as_of ORDER BY as_of DESC LIMIT :k)")
# Set-based so it stays cheap at prod size: Spearman is the Pearson correlation of tie-averaged
# ranks, the same 0-based ranks exposure_math._ranks gives (test_exposure pins the two together).
VALIDATE_SQL = text("""
WITH s AS (
  SELECT exposure_point AS p, tick_coverage AS c, (proxy->'mp_ticks'->>'value')::float8 AS t
  FROM internal.exposure_index WHERE as_of = :d AND data_cutoff = :c
), f AS (
  SELECT p, t FROM s WHERE p IS NOT NULL AND t IS NOT NULL AND c = 1
), r AS (
  SELECT (rank() OVER (ORDER BY p) - 1 + (count(*) OVER (PARTITION BY p) - 1) / 2.0)::float8 AS rp,
         (rank() OVER (ORDER BY t) - 1 + (count(*) OVER (PARTITION BY t) - 1) / 2.0)::float8 AS rt
  FROM f
)
SELECT (SELECT count(*) FROM s), (SELECT count(*) FROM s WHERE p IS NULL), (SELECT count(*) FROM s WHERE c = 1),
       (SELECT avg(c) FROM s), (SELECT CASE WHEN count(*) >= 3 THEN corr(rp, rt) END FROM r)
""")
_LOOSE_ISOLATION = ("read uncommitted", "read committed")


def _digest(part: dict[tuple[int, str, int], Component]) -> str:
    h = hashlib.sha256()
    for key in sorted(part):
        h.update(json.dumps([list(key), part[key].to_json()], sort_keys=True, allow_nan=False).encode())
    return h.hexdigest()


async def _one_snapshot(conn: AsyncConnection) -> None:
    # Scope and every component must read one snapshot: under READ COMMITTED each statement sees
    # its own, so a catalog write between the scope read and the tick read makes them disagree.
    # SHOW takes no snapshot, so SET still works after it; if the caller already ran a query in a
    # READ COMMITTED transaction, Postgres refuses the SET and the build fails loudly.
    level = (await conn.execute(text("SHOW transaction_isolation"))).scalar()
    if level in _LOOSE_ISOLATION:
        await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))


async def build(conn: AsyncConnection, *, today: date, cutoff: date | None = None, mp_enabled: bool,
                run_id: uuid.UUID | None = None, write: bool = True) -> dict[str, object]:
    cut = cutoff or today
    if cut > today:
        raise ValueError(f"data cutoff {cut} is after today {today}")
    await _one_snapshot(conn)
    s = await scope(conn)
    parts: dict[str, dict[tuple[int, str, int], Component]] = {
        "route_density": route_density(s),
        "season_share": await season(conn, s, today=today),
        "mp_ticks": await mp_ticks(conn, s, enabled=mp_enabled, cutoff=cut),
    }
    waiting = await blackout_series(conn, s, today=today)
    rows: list[dict[str, object]] = []
    for key in keys(s):
        area, tg, month = key
        comps = {name: parts[name][key] for name in COMPONENTS}
        point = exposure_point(comps["route_density"].value, comps["season_share"].value)
        lo, hi = bounds(point)
        rows.append({"a": area, "tg": tg, "m": month, "n": s[(area, tg)],
                     "p": json.dumps({name: c.to_json() for name, c in comps.items()}, sort_keys=True,
                                     allow_nan=False),
                     "v": PROXY_VERSION, "d": today, "c": cut, "cov": comps["mp_ticks"].get("coverage"),
                     "pt": point, "lo": lo, "hi": hi, "run": run_id})
    if write:
        await conn.execute(CLEAR_SQL, {"d": today, "c": cut})
        for i in range(0, len(rows), INSERT_CHUNK):
            await conn.execute(INSERT_SQL, rows[i : i + INSERT_CHUNK])
        await conn.execute(PRUNE_SQL, {"k": KEEP_SNAPSHOTS})
    return {"cells": len(rows), "null_points": sum(1 for r in rows if r["pt"] is None), "as_of": today.isoformat(),
            "data_cutoff": cut.isoformat(), "mp_enabled": mp_enabled, "proxy_version": PROXY_VERSION,
            "season_blackout_series": waiting, "digests": {name: _digest(parts[name]) for name in COMPONENTS}}


async def validate(conn: AsyncConnection, *, as_of: date, data_cutoff: date) -> dict[str, float]:
    cells, nulls, full, mean, rho = (await conn.execute(VALIDATE_SQL, {"d": as_of, "c": data_cutoff})).one()
    out = {"cells": float(cells), "null_points": float(nulls), "full_coverage_cells": float(full)}
    if mean is not None:
        out["tick_coverage_mean"] = float(mean)
    if rho is not None:
        out["spearman_point_ticks"] = float(rho)
    return out


async def drop_snapshot(conn: AsyncConnection, *, as_of: date, data_cutoff: date) -> int:
    result = await conn.execute(CLEAR_SQL, {"d": as_of, "c": data_cutoff})
    return int(result.rowcount or 0)


def _blackout_problem(summary: dict[str, object]) -> str | None:
    waiting = summary["season_blackout_series"]
    if not isinstance(waiting, int) or waiting == 0:
        return None
    return (f"season_blackout: {waiting} ERA5 series await this January's window, so their seasons read "
            "missing (insufficient_history); rebuild after the January ERA5 window completes")


def _warn(problem: str | None) -> None:
    if problem is not None:
        print(f"\n{'!' * 72}\nWARNING (January blackout): {problem}\n{'!' * 72}\n", file=sys.stderr)


def _snapshot_engine(engine: AsyncEngine) -> AsyncEngine:
    # start_run writes before build reads, so the transaction must begin REPEATABLE READ.
    return engine.execution_options(isolation_level="REPEATABLE READ")


async def _run(args: argparse.Namespace, *, today: date, mp_enabled: bool) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.pipelines.ingest_log import finish_run, start_run
    from app.pipelines.validate import ValidationReport

    base = ingest_engine()
    engine = _snapshot_engine(base)
    try:
        if args.cmd == "drop":
            async with engine.begin() as conn:
                return {"dropped_rows": await drop_snapshot(conn, as_of=args.as_of, data_cutoff=args.cutoff)}
        if args.dry_run:
            async with engine.connect() as conn:
                summary = await build(conn, today=today, cutoff=args.cutoff, mp_enabled=mp_enabled, write=False)
                await conn.rollback()
            _warn(_blackout_problem(summary))
            return {"status": "dry_run", **summary}
        async with engine.begin() as conn:
            cut = args.cutoff or today
            run_id = await start_run(conn, source=SOURCE, window_start=cut, window_end=today, content_sha256=None)
            summary = await build(conn, today=today, cutoff=args.cutoff, mp_enabled=mp_enabled, run_id=run_id)
            checks = await validate(conn, as_of=today, data_cutoff=cut)
            problem = _blackout_problem(summary)
            report = ValidationReport(SOURCE)
            report.rows_in = report.accepted = int(checks["cells"])
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=report.accepted,
                             problems=[problem] if problem else None, metrics=checks)
        _warn(problem)
        return {"status": "ok", **summary, **checks}
    finally:
        await base.dispose()


def cli() -> None:
    from app.config import settings
    from app.services.temporal_weighting import utc_today

    parser = argparse.ArgumentParser(prog="python -m app.pipelines.exposure")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write one exposure snapshot")
    b.add_argument("--cutoff", type=date.fromisoformat, default=None,
                   help="count tick months only before this date (Phase 3 backtests); default today (UTC)")
    b.add_argument("--dry-run", action="store_true", help="compute and print the summary and digests; write nothing")
    d = sub.add_parser("drop", help="delete one snapshot")
    d.add_argument("--as-of", type=date.fromisoformat, required=True)
    d.add_argument("--cutoff", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    result = asyncio.run(_run(args, today=utc_today(), mp_enabled=settings.EXPOSURE_ENABLE_MP_TICKS))
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    cli()
