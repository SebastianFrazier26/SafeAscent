"""The scheduled catalog and features cycle (DP6): refresh the catalog from the MP tables,
register points, sample elevation until nothing is left (or the rounds run out), rebuild the
route table only after the EPQS gate passes. It stops at a rejected refresh, so a bad scrape
never feeds the feature steps, and at a failed gate or unfinished elevation, so
route_static_features never holds un-gated elevations. No paid service is called; normals for new buckets are bought by
plan 3's January window, which is why the yearly run is scheduled before it opens.

In production it runs as the Railway cron service `catalog-cycle` (owner decision DP6,
2026-10-13), the only service holding the ingest credential, on Neon's direct host."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.healthchecks import PingSuffix
from app.pipelines.mp_catalog import RefreshResult, run_logged
from app.pipelines.static_features import run_step

MAX_ELEVATION_ROUNDS = 20
ELEVATION_LIMIT = 5000
CycleStatus = Literal["ok", "rejected", "incomplete", "failed"]
Refresh = Callable[..., Awaitable[RefreshResult]]
Step = Callable[..., Awaitable[dict[str, object]]]


@dataclass
class CycleResult:
    status: CycleStatus
    steps: dict[str, object] = field(default_factory=dict)


def require_direct_host(url: str) -> None:
    # A long single-session job on the direct host (owner decision DP6), never through Neon's
    # pgbouncer endpoint, whose host carries the "-pooler" suffix.
    try:
        host = make_url(url).host or ""
    except ArgumentError:
        # SQLAlchemy's message quotes the whole URL, password included; never echo it.
        raise SystemExit("INGEST_DATABASE_URL is not a valid URL") from None
    if "-pooler." in host:
        raise SystemExit("catalog_cycle: INGEST_DATABASE_URL points at Neon's pooler host; use the direct host")


def ping_suffix(status: CycleStatus) -> PingSuffix:
    return "" if status == "ok" else "/fail"


async def run_cycle(
    engine: AsyncEngine | None,
    *,
    refresh: Refresh = run_logged,
    step: Step = run_step,
    max_rounds: int = MAX_ELEVATION_ROUNDS,
    epqs: bool = True,
) -> CycleResult:
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")
    out = CycleResult("ok")
    refreshed = await refresh(engine, dry_run=False)
    out.steps["refresh"] = {"status": refreshed.status, "problems": refreshed.problems, **refreshed.counts}
    if refreshed.status != "ok":
        out.status = "rejected"
        return out
    out.steps["points"] = await step(engine, "points", limit=ELEVATION_LIMIT)
    retry = 0
    for round_no in range(1, max_rounds + 1):
        got = await step(engine, "elevation", limit=ELEVATION_LIMIT)
        out.steps[f"elevation_{round_no}"] = got
        retry = int(str(got.get("retry", 0)))
        if int(str(got.get("sampled", 0))) == 0:
            break
    else:
        # Every round still sampled something: points remain unsampled, so neither the gate
        # nor the route table may run on a partial set. The next run carries on.
        out.steps["elevation_rounds_exhausted"] = max_rounds
        out.status = "incomplete"
        return out
    out.steps["elevation_retry_left"] = retry
    if retry:
        out.status = "incomplete"
    if epqs:
        if retry:
            # The gate samples elevations; it waits until no point is pending, and so do routes.
            return out
        gate = await step(engine, "epqs-check", limit=ELEVATION_LIMIT)
        out.steps["epqs"] = gate
        if gate.get("passed") != 1:
            out.status = "failed"
            return out
    out.steps["routes"] = await step(engine, "routes", limit=ELEVATION_LIMIT)
    return out


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def cli() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.pipelines.catalog_cycle")
    parser.add_argument("cmd", choices=["run"])
    parser.add_argument("--skip-epqs", action="store_true", help="on-demand runs: skip the ~15-minute EPQS gate")
    parser.add_argument("--max-rounds", type=_positive_int, default=MAX_ELEVATION_ROUNDS)
    args = parser.parse_args()
    from app.config import settings
    from app.healthchecks import ping
    from app.pipelines.db import ingest_engine

    ping(settings.HEALTHCHECKS_CATALOG_URL, "/start")

    async def go() -> CycleResult:
        engine = ingest_engine()
        try:
            return await run_cycle(engine, max_rounds=args.max_rounds, epqs=not args.skip_epqs)
        finally:
            await engine.dispose()

    try:
        if settings.INGEST_DATABASE_URL:
            require_direct_host(settings.INGEST_DATABASE_URL)
        result = asyncio.run(go())
    except BaseException:
        ping(settings.HEALTHCHECKS_CATALOG_URL, "/fail")
        raise
    ping(settings.HEALTHCHECKS_CATALOG_URL, ping_suffix(result.status))
    print(json.dumps({"status": result.status, **result.steps}, sort_keys=True, default=str))
    sys.exit({"ok": 0, "incomplete": 2}.get(result.status, 1))


if __name__ == "__main__":
    cli()
