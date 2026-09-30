"""R1 (AAC year repair from source_id neighbours) and R2 (date precision).

AAC source_id rises monotonically with publication year (audit 2026-09-27), so the years
of the trusted rows around a suspect row bound its real year. No LLM, no guessing: a row
whose neighbours disagree by more than two years, or that has too few neighbours on
either side, is left dated as it is and excluded as year_unverified (D10).
"""

from __future__ import annotations

import argparse
import re
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median_low

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.validate import ValidationReport

R1_VERSION = "r1-v1"
R2_VERSION = "r2-v1"
SUSPECT_FROM_YEAR = 2019
NEIGHBOURS = 10
MIN_SIDE = 5
MAX_SPREAD = 2
YEAR_SOURCE = "aac_source_id_neighbours"
_TRAILING_INT = re.compile(r"(\d+)\s*$")


def r2_precision(family: SourceFamily, d: date | None, year: float | None) -> str:
    if d is None:
        return "year" if year is not None else "unknown"
    if family == "aac" and d.day == 15:
        return "month"
    if family == "aac" and (d.month, d.day) == (7, 1):
        return "year"
    return "day"


def source_sort_key(source_id: str | None) -> int | None:
    match = _TRAILING_INT.search(source_id or "")
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class AacRow:
    accident_id: int
    sort_key: int | None
    date: date | None
    year: int | None


@dataclass(frozen=True)
class YearFix:
    accident_id: int
    year: int | None
    year_lo: int | None
    year_hi: int | None
    date_precision: str
    new_date: date | None
    excluded_reason: str | None


def _unverified(row: AacRow, lo: int | None = None, hi: int | None = None) -> YearFix:
    return YearFix(row.accident_id, None, lo, hi, "unknown", None, "year_unverified")


def _same_day_in(year: int, d: date) -> tuple[date, str]:
    try:
        moved = d.replace(year=year)
    except ValueError:
        return date(year, 2, 15), "month"
    return moved, r2_precision("aac", moved, year)


def r1_year_fixes(rows: Sequence[AacRow]) -> list[YearFix]:
    trusted = sorted(
        (r.sort_key, r.year)
        for r in rows
        if r.sort_key is not None and r.year is not None and r.year < SUSPECT_FROM_YEAR
    )
    keys = [k for k, _ in trusted]
    fixes: list[YearFix] = []
    for row in rows:
        if row.year is None or row.year < SUSPECT_FROM_YEAR:
            continue
        if row.sort_key is None:
            fixes.append(_unverified(row))
            continue
        i = bisect_left(keys, row.sort_key)
        left = [y for _, y in trusted[max(0, i - NEIGHBOURS) : i]]
        right = [y for _, y in trusted[i : i + NEIGHBOURS]]
        if len(left) < MIN_SIDE or len(right) < MIN_SIDE:
            fixes.append(_unverified(row))
            continue
        years = left + right
        lo, hi, year = min(years), max(years), median_low(years)
        if hi - lo > MAX_SPREAD:
            fixes.append(_unverified(row, lo, hi))
        elif lo == hi and row.date is not None:
            new_date, precision = _same_day_in(year, row.date)
            fixes.append(YearFix(row.accident_id, year, lo, hi, precision, new_date, None))
        else:
            fixes.append(YearFix(row.accident_id, year, lo, hi, "year", date(year, 7, 1), None))
    return fixes


def changes_for(fix: YearFix, *, old_date: date | None) -> list[Change]:
    a = fix.accident_id
    changes = [
        Change(a, "year_lo", fix.year_lo, "r1"),
        Change(a, "year_hi", fix.year_hi, "r1"),
        Change(a, "year_source", YEAR_SOURCE, "r1"),
        Change(a, "date_precision", fix.date_precision, "r1"),
        Change(a, "excluded_reason", fix.excluded_reason, "r1"),
    ]
    if fix.new_date is not None and fix.new_date != old_date:
        changes += [Change(a, "date", fix.new_date, "r1"), Change(a, "year", fix.year, "r1")]
    return changes


def restrict_to_raw(rows: Sequence[dict[str, object]], raw_ids: frozenset[int]) -> list[dict[str, object]]:
    return [r for r in rows if int(str(r["accident_id"])) in raw_ids]


def with_original_values(
    rows: Sequence[dict[str, object]], originals: Mapping[tuple[int, str], str | None]
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for r in rows:
        a = int(str(r["accident_id"]))
        row = dict(r)
        if (a, "date") in originals:
            old = originals[(a, "date")]
            row["date"] = date.fromisoformat(old) if old is not None else None
        if (a, "year") in originals:
            old = originals[(a, "year")]
            row["year"] = float(old) if old is not None else None
        out.append(row)
    return out


async def original_values(conn: AsyncConnection) -> dict[tuple[int, str], str | None]:
    # G17: a row R1 re-dated must not count as a trusted neighbour (or stop being a suspect)
    # under a later rule version. The first revision's old_value is the pre-repair value;
    # reading it here avoids widening ingest's column grant on internal.accidents_raw.
    result = await conn.execute(
        text(
            "SELECT DISTINCT ON (accident_id, field) accident_id, field, old_value "
            "FROM internal.accident_revisions WHERE field IN ('date', 'year') "
            "ORDER BY accident_id, field, id"
        )
    )
    return {(int(row.accident_id), str(row.field)): row.old_value for row in result}


async def raw_ids(conn: AsyncConnection) -> frozenset[int]:
    ids = frozenset(int(i) for i in (await conn.execute(text("SELECT accident_id FROM internal.accidents_raw"))).scalars())
    if not ids:
        raise SystemExit("internal.accidents_raw is empty: 0004 snapshots accidents before any repair")
    return ids


async def run_r2r1(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    # R1's neighbour rule and R2's day-15/Jul-1 rule describe the pre-2a AAC export only.
    # Rows loaded later (plan 3's R11 facts CSV) set their own precision; re-dating them
    # from source_id neighbours would move a 2025 record into the oldest years.
    live = restrict_to_raw(await fetch_accidents(conn, ["source", "source_id", "date", "year"]), await raw_ids(conn))
    live_dates = {int(str(r["accident_id"])): r["date"] if isinstance(r["date"], date) else None for r in live}
    rows = with_original_values(live, await original_values(conn))
    families = {int(str(r["accident_id"])): source_family(r["source"] if isinstance(r["source"], str) else None) for r in rows}
    aac = [
        AacRow(
            int(str(r["accident_id"])),
            source_sort_key(r["source_id"] if isinstance(r["source_id"], str) else None),
            r["date"] if isinstance(r["date"], date) else None,
            r["date"].year if isinstance(r["date"], date) else (int(float(str(r["year"]))) if r["year"] is not None else None),
        )
        for r in rows
        if families[int(str(r["accident_id"]))] == "aac"
    ]
    fixes = {f.accident_id: f for f in r1_year_fixes(aac)}
    r1_changes: list[Change] = []
    r2_changes: list[Change] = []
    r1_report, r2_report = ValidationReport("repair:r1"), ValidationReport("repair:r2")
    for r in rows:
        a = int(str(r["accident_id"]))
        d = r["date"] if isinstance(r["date"], date) else None
        if a in fixes:
            fix = fixes[a]
            r1_changes += changes_for(fix, old_date=live_dates[a])
            if fix.excluded_reason:
                r1_report.quarantine(str(a), fix.excluded_reason)
            else:
                r1_report.accept()
            continue
        year = float(str(r["year"])) if r["year"] is not None else None
        precision = r2_precision(families[a], d, year)
        r2_changes.append(Change(a, "date_precision", precision, "r2"))
        if precision == "unknown":
            r2_changes.append(Change(a, "excluded_reason", "year_unverified", "r2"))
            r2_report.quarantine(str(a), "no_date")
        else:
            r2_report.accept()
    r1 = await run_step(conn, step="r1", rule_version=R1_VERSION, changes=r1_changes, report=r1_report, apply=args.apply)
    r2 = await run_step(conn, step="r2", rule_version=R2_VERSION, changes=r2_changes, report=r2_report, apply=args.apply)
    # D10: excluded, never deleted; the runbook reports this count as a Phase 3 recency-bias note.
    unverified = r1_report.quarantined["year_unverified"] + r2_report.quarantined["no_date"]
    return {"r1": r1, "r2": r2, "year_unverified": unverified}
