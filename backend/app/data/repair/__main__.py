"""python -m app.data.repair <step> [--apply]. Dry-run by default; prints counts only."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import activity, area_weekly, dates, dedupe, experience, geocode, legacy_links, severity

Step = Callable[[AsyncConnection, argparse.Namespace], Awaitable[dict[str, object]]]
STEPS: dict[str, Step] = {
    "r2r1": dates.run_r2r1,
    "r9": severity.run_r9,
    "r9-export": severity.run_r9_export,
    "r9-import": severity.run_r9_import,
    "r1-export": severity.run_r1_export,
    "r1-import": severity.run_r1_import,
    "r1v2": dates.run_r1v2,
    "r1v2-backtest": dates.run_r1v2_backtest,
    "r1v2-export": severity.run_r1v2_export,
    "r1v2-import": severity.run_r1v2_import,
    "r3": geocode.run_r3,
    "r3-clusters": geocode.run_r3_clusters,
    "r3-states": geocode.run_r3_states,
    "r3a-export": geocode.run_r3a_export,
    "r3a-import": geocode.run_r3a_import,
    "r3-plot-export": geocode.run_r3_plot_export,
    "r3-plot-import": geocode.run_r3_plot_import,
    "r3-ambiguous-export": geocode.run_r3_ambiguous_export,
    "r3-ambiguous-import": geocode.run_r3_ambiguous_import,
    "r4": activity.run_r4,
    "r4-golden-export": activity.run_r4_golden_export,
    "r4-review-export": activity.run_r4_review_export,
    "r4-review-import": activity.run_r4_review_import,
    "r5": dedupe.run_r5,
    "r5-import": dedupe.run_r5_import,
    "r12": experience.run_r12,
    "r12-golden-export": experience.run_r12_golden_export,
    "r7": area_weekly.run_r7,
    "r10": legacy_links.run_r10,
    "r10-export": legacy_links.run_r10_export,
    "r10-import": legacy_links.run_r10_import,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.data.repair")
    parser.add_argument("step", choices=sorted(STEPS))
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--review-file", help="decisions CSV for *-import steps")
    parser.add_argument("--out", help="output path for *-export steps")
    parser.add_argument(
        "--overwrite", action="store_true", help="*-export (and r5 --apply): replace an existing file (it may hold filled decisions); r5-import: replace earlier owner decisions"
    )
    return parser


async def _main(args: argparse.Namespace) -> None:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            result = await STEPS[args.step](conn, args)
    finally:
        await engine.dispose()
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    asyncio.run(_main(build_parser().parse_args()))
