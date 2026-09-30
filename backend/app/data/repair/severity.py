"""R9: which severity scale each row's injury_severity is on. NPS records are a mortality
dataset (every row fatal), so its severity cannot be compared with AAC's. R9 only labels the
scale; injury_severity itself is never rewritten. Also the R1/R9 hand-check round trips."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import review
from app.data.repair.checks import YES_NO, record_check
from app.data.repair.dates import YEAR_SOURCE
from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.validate import ValidationReport

R9_VERSION = "r9-v1"
# The owner finds the AAC report from these; description never leaves the database.
LOCATOR_COLUMNS = ("accident_id", "source_id", "date", "location", "mountain")


def severity_scale(family: SourceFamily, injury_severity: str | None) -> str:
    if family == "nps":
        return "fatal_only"
    return "full" if injury_severity and injury_severity.strip() else "unknown"


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


async def run_r9(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await fetch_accidents(conn, ["source", "injury_severity"])
    report = ValidationReport("repair:r9")
    changes = []
    for r in rows:
        scale = severity_scale(source_family(_str_or_none(r["source"])), _str_or_none(r["injury_severity"]))
        report.accept()
        changes.append(Change(int(str(r["accident_id"])), "severity_scale", scale, "r9"))
    return await run_step(conn, step="r9", rule_version=R9_VERSION, changes=changes, report=report, apply=args.apply)


def _out(args: argparse.Namespace, name: str) -> Path:
    # Resolved per call, so the default follows review.REVIEW_DIR wherever the CLI runs from.
    return Path(args.out) if args.out else review.REVIEW_DIR / name


async def run_r9_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = [
        r
        for r in await fetch_accidents(conn, ["source", "source_id", "location", "mountain", "injury_severity", "date"])
        if str(r["source"]).lower().startswith("aac")
    ]
    out = _out(args, "severity_check.csv")
    n = review.export_sample(
        rows, n=50, seed=9, path=out, columns=[*LOCATOR_COLUMNS, "injury_severity"], decision_column="agree"
    )
    return {"exported": n, "path": str(out), "instructions": "set agree=yes|no after reading each AAC report"}


async def run_r9_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    decisions = review.read_decisions(Path(args.review_file), key="accident_id", decision_column="agree", allowed=YES_NO)
    return await record_check(conn, "repair:r9-check", decisions)


def r1_check_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    # Only rows R1 actually re-dated can be checked "within one year"; a year_unverified
    # row kept its (corrupt) date on purpose and would fail the check by construction.
    return [
        r
        for r in rows
        if r["year_source"] == YEAR_SOURCE and isinstance(r["date"], date) and r["excluded_reason"] is None
    ]


async def run_r1_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = r1_check_rows(
        await fetch_accidents(
            conn,
            [
                "source", "source_id", "location", "mountain", "date", "year_lo", "year_hi", "year_source",
                "source_url", "excluded_reason",
            ],
        )
    )
    out = _out(args, "r1_year_check.csv")
    n = review.export_sample(
        rows,
        n=40,
        seed=1,
        path=out,
        columns=[*LOCATOR_COLUMNS, "year_lo", "year_hi", "source_url"],
        decision_column="within_one_year",
    )
    return {
        "exported": n,
        "path": str(out),
        "instructions": "look up each report in the AAC Publications archive (year and title only); within_one_year=yes|no",
    }


async def run_r1_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    decisions = review.read_decisions(
        Path(args.review_file), key="accident_id", decision_column="within_one_year", allowed=YES_NO
    )
    return await record_check(conn, "repair:r1-check", decisions)
