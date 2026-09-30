"""R1 (AAC year repair from source_id neighbours) and R2 (date precision)."""

from __future__ import annotations

import argparse
from datetime import date

from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.validate import ValidationReport

R2_VERSION = "r2-v1"


def r2_precision(family: SourceFamily, d: date | None, year: float | None) -> str:
    if d is None:
        return "year" if year is not None else "unknown"
    if family == "aac" and d.day == 15:
        return "month"
    if family == "aac" and (d.month, d.day) == (7, 1):
        return "year"
    return "day"


async def run_r2r1(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await fetch_accidents(conn, ["source", "date", "year"])
    report = ValidationReport("repair:r2")
    changes: list[Change] = []
    for r in rows:
        d = r["date"] if isinstance(r["date"], date) else None
        year = float(str(r["year"])) if r["year"] is not None else None
        precision = r2_precision(source_family(r["source"] if isinstance(r["source"], str) else None), d, year)
        report.accept()
        changes.append(Change(int(str(r["accident_id"])), "date_precision", precision, "r2"))
    return await run_step(conn, step="r2", rule_version=R2_VERSION, changes=changes, report=report, apply=args.apply)
