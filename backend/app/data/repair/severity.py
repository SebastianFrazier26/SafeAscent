"""R9: which severity scale each row's injury_severity is on. NPS records are a mortality
dataset (every row fatal), so its severity cannot be compared with AAC's. R9 only labels the
scale; injury_severity itself is never rewritten. Also the R1/R9 hand-check round trips."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import review
from app.data.repair.checks import YES_NO, record_check, refuse_ineligible
from app.data.repair.dates import R1V2_VERSION, YEAR_SOURCE, accident_ids_with_revisions, source_scheme
from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.validate import ValidationReport

R9_VERSION = "r9-v1"
# The owner finds the AAC report from these; description never leaves the database.
LOCATOR_COLUMNS = ("accident_id", "source_id", "date", "location", "mountain")
# Prod vocabulary read 2026-09-30 (aac, avalanche: fatal/serious/minor, avalanche also
# near_miss; nps: fatal). Anything else refuses, like source_family, so a new value gets a
# reviewed mapping instead of a silent "unknown" or "full".
FULL_SCALE_SEVERITIES = frozenset({"fatal", "serious", "minor", "near_miss"})
UNKNOWN_SEVERITIES = frozenset({"", "unknown", "n/a", "none"})


def normalized_severity(injury_severity: str | None) -> str:
    return (injury_severity or "").strip().lower()


def severity_scale(family: SourceFamily, injury_severity: str | None) -> str:
    key = normalized_severity(injury_severity)
    if key not in FULL_SCALE_SEVERITIES | UNKNOWN_SEVERITIES:
        raise ValueError(f"unmapped injury_severity {injury_severity!r}: extend severity.py in a reviewed change")
    if family == "nps":
        return "fatal_only"
    return "full" if key in FULL_SCALE_SEVERITIES else "unknown"


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _id(row: dict[str, object]) -> int:
    return int(str(row["accident_id"]))


async def run_r9(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await fetch_accidents(conn, ["source", "injury_severity"])
    report = ValidationReport("repair:r9")
    changes = []
    crosstab: Counter[str] = Counter()
    for r in rows:
        family = source_family(_str_or_none(r["source"]))
        severity = _str_or_none(r["injury_severity"])
        scale = severity_scale(family, severity)
        crosstab[f"{family}|{scale}|{normalized_severity(severity)}"] += 1
        report.accept()
        changes.append(Change(_id(r), "severity_scale", scale, "r9"))
    summary = await run_step(conn, step="r9", rule_version=R9_VERSION, changes=changes, report=report, apply=args.apply)
    # CLI output only: mapped vocabulary counts, never written to validation_report.
    return summary | {"crosstab": dict(sorted(crosstab.items()))}


def _out(args: argparse.Namespace, name: str) -> Path:
    # Resolved per call, so the default follows review.REVIEW_DIR wherever the CLI runs from.
    return Path(args.out) if args.out else review.REVIEW_DIR / name


async def r9_check_pool(conn: AsyncConnection) -> list[dict[str, object]]:
    return [
        r
        for r in await fetch_accidents(conn, ["source", "source_id", "location", "mountain", "injury_severity", "date"])
        if source_family(_str_or_none(r["source"])) == "aac"
    ]


async def run_r9_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await r9_check_pool(conn)
    out = _out(args, "severity_check.csv")
    n = review.export_sample(
        rows,
        n=50,
        seed=9,
        path=out,
        columns=[*LOCATOR_COLUMNS, "injury_severity"],
        decision_column="agree",
        overwrite=args.overwrite,
    )
    return {
        "exported": n,
        "path": str(out),
        "instructions": (
            "read each AAC report; agree=yes if the stored injury_severity matches it, or if it is blank "
            "and the report states no injury outcome; otherwise agree=no"
        ),
    }


async def run_r9_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    decisions = review.read_decisions(Path(args.review_file), key="accident_id", decision_column="agree", allowed=YES_NO)
    refuse_ineligible(decisions, {_id(r) for r in await r9_check_pool(conn)})
    return await record_check(conn, "repair:r9-check", decisions)


def r1_check_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    # Only rows R1 actually re-dated can be checked "within one year"; a year_unverified
    # row kept its (corrupt) date on purpose and would fail the check by construction.
    return [
        r
        for r in rows
        if r["year_source"] == YEAR_SOURCE and isinstance(r["date"], date) and r["excluded_reason"] is None
    ]


async def _redated_rows(conn: AsyncConnection) -> list[dict[str, object]]:
    return r1_check_rows(
        await fetch_accidents(
            conn,
            [
                "source", "source_id", "location", "mountain", "date", "year_lo", "year_hi", "year_source",
                "source_url", "excluded_reason",
            ],
        )
    )


async def r1_check_pool(conn: AsyncConnection) -> list[dict[str, object]]:
    # r1-v1's pool only: r1-v2's rows have their own check, and keeping them out means the
    # r1 sample is the same whether or not r1-v2 has run (Task 12 6f compares the ids).
    v2 = await accident_ids_with_revisions(conn, R1V2_VERSION)
    return [r for r in await _redated_rows(conn) if _id(r) not in v2]


async def r1v2_check_pool(conn: AsyncConnection) -> list[dict[str, object]]:
    v2 = await accident_ids_with_revisions(conn, R1V2_VERSION)
    return [r for r in await _redated_rows(conn) if _id(r) in v2]


# The spec gate is ±1 year of the repaired point year, not of the [year_lo, year_hi] range:
# grading against the range would pass a looser test than the one R1 is accepted on.
R1_CHECK_INSTRUCTIONS = (
    "look up each report in the AAC Publications archive (year and title only); "
    "within_one_year=yes if |accident year in the report - year of `date`| <= 1, else no. "
    "The year of `date` is R1's point year; year_lo..year_hi is its range, for context only: "
    "a report year inside the range but more than 1 from the year of `date` is still no. "
    "Use the accident year, not the publication year; if the report states no accident year, set no"
)


async def run_r1_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await r1_check_pool(conn)
    out = _out(args, "r1_year_check.csv")
    n = review.export_sample(
        rows,
        n=40,
        seed=1,
        path=out,
        columns=[*LOCATOR_COLUMNS, "year_lo", "year_hi", "source_url"],
        decision_column="within_one_year",
        overwrite=args.overwrite,
    )
    return {
        "exported": n,
        "path": str(out),
        "instructions": R1_CHECK_INSTRUCTIONS,
    }


async def run_r1_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    decisions = review.read_decisions(
        Path(args.review_file), key="accident_id", decision_column="within_one_year", allowed=YES_NO
    )
    refuse_ineligible(decisions, {_id(r) for r in await r1_check_pool(conn)})
    return await record_check(conn, "repair:r1-check", decisions)


# A uniform 40 of ~185 would hold about 4 eight-digit rows; that rule dates a whole scheme
# from one assumption, so it gets enough rows to be seen failing.
R1V2_QUOTAS = {"eight_digit": 8, "sequence": 32}


async def run_r1v2_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await r1v2_check_pool(conn)
    out = _out(args, "r1v2_year_check.csv")
    taken = review.export_stratified(
        rows,
        stratum=lambda r: source_scheme(_str_or_none(r["source_id"])),
        quotas=R1V2_QUOTAS,
        seed=2,
        path=out,
        columns=[*LOCATOR_COLUMNS, "year_lo", "year_hi", "source_url"],
        decision_column="within_one_year",
        overwrite=args.overwrite,
    )
    return {
        "exported": sum(taken.values()),
        "taken": taken,
        "path": str(out),
        "instructions": R1_CHECK_INSTRUCTIONS,
    }


async def run_r1v2_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    decisions = review.read_decisions(
        Path(args.review_file), key="accident_id", decision_column="within_one_year", allowed=YES_NO
    )
    refuse_ineligible(decisions, {_id(r) for r in await r1v2_check_pool(conn)})
    return await record_check(conn, "repair:r1v2-check", decisions)
