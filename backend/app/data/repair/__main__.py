"""python -m app.data.repair <step> [--apply]. Dry-run by default; prints counts only."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import dates, severity

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
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.data.repair")
    parser.add_argument("step", choices=sorted(STEPS))
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--review-file", help="decisions CSV for *-import steps")
    parser.add_argument("--out", help="output path for *-export steps")
    parser.add_argument(
        "--overwrite", action="store_true", help="*-export: replace an existing file (it may hold filled decisions)"
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
