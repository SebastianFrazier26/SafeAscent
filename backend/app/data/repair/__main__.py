"""python -m app.data.repair <step> [--apply]. Dry-run by default; prints counts only."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import dates

Step = Callable[[AsyncConnection, argparse.Namespace], Awaitable[dict[str, object]]]
STEPS: dict[str, Step] = {"r2r1": dates.run_r2r1}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.data.repair")
    parser.add_argument("step", choices=sorted(STEPS))
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--review-file", help="decisions CSV for *-import steps")
    parser.add_argument("--out", help="output path for *-export steps")
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
