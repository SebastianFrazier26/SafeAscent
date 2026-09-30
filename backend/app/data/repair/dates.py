"""R1 (AAC year repair from source_id neighbours) and R2 (date precision).

AAC source_ids form one running sequence (1-2683 on prod, read 2026-09-30; some ids carry
a/b/t suffix variants of one base) whose order follows accident year, so the trusted
(pre-2019) rows on either side of a suspect row bound its real year: no earlier than the
latest year on its left, no later than the earliest on its right. A separate 8-digit
scheme (20190001...) is not a year-ordered sequence and is never a neighbour. No LLM, no
guessing: a row with a contradictory or wider-than-two-year bound, too few neighbours on
either side, or no place in the sequence keeps its snapshot date and is excluded as
year_unverified (D10). Inputs come from the frozen internal.accidents_raw snapshot.
"""

from __future__ import annotations

import argparse
import re
from bisect import bisect_left
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

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
YEAR_UNVERIFIED = "year_unverified"
_EIGHT_DIGIT = re.compile(r"^\d{8}$")
_TRAILING_ID = re.compile(r"(\d+)([a-z]*)$")

Scheme = Literal["sequence", "eight_digit", "unparseable"]
SortKey = tuple[int, str]


def r2_precision(family: SourceFamily, d: date | None, year: float | None) -> str:
    if d is None:
        return "year" if year is not None else "unknown"
    if family == "aac" and d.day == 15:
        return "month"
    if family == "aac" and (d.month, d.day) == (7, 1):
        return "year"
    return "day"


def source_scheme(source_id: str | None) -> Scheme:
    key = (source_id or "").strip().lower()
    if _EIGHT_DIGIT.match(key):
        return "eight_digit"
    return "sequence" if _TRAILING_ID.search(key) else "unparseable"


def source_sort_key(source_id: str | None) -> SortKey | None:
    """(base, suffix), so "88a" sorts right after "88". None off the running sequence."""
    key = (source_id or "").strip().lower()
    match = None if _EIGHT_DIGIT.match(key) else _TRAILING_ID.search(key)
    return (int(match.group(1)), match.group(2)) if match else None


@dataclass(frozen=True)
class AacRow:
    accident_id: int
    sort_key: SortKey | None
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
    return YearFix(row.accident_id, None, lo, hi, "unknown", None, YEAR_UNVERIFIED)


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
        lo, hi = max(left), min(right)
        if lo > hi:
            # The neighbours themselves are out of order here, so they bound nothing (and a
            # year_lo above year_hi would break the year_bounds check).
            fixes.append(_unverified(row))
        elif hi - lo > MAX_SPREAD:
            fixes.append(_unverified(row, lo, hi))
        elif lo == hi and row.date is not None:
            new_date, precision = _same_day_in(lo, row.date)
            fixes.append(YearFix(row.accident_id, lo, lo, hi, precision, new_date, None))
        else:
            year = (lo + hi) // 2
            fixes.append(YearFix(row.accident_id, year, lo, hi, "year", date(year, 7, 1), None))
    return fixes


def changes_for(fix: YearFix, *, old_date: date | None, old_excluded: str | None = None) -> list[Change]:
    a = fix.accident_id
    changes = [
        Change(a, "year_lo", fix.year_lo, "r1"),
        Change(a, "year_hi", fix.year_hi, "r1"),
        Change(a, "year_source", YEAR_SOURCE, "r1"),
        Change(a, "date_precision", fix.date_precision, "r1"),
    ]
    # R1 sets or lifts only its own exclusion; another step's reason stands either way (the
    # row is already excluded, and overwriting would lose why).
    if old_excluded in (None, YEAR_UNVERIFIED) and (fix.excluded_reason is not None or old_excluded is not None):
        changes.append(Change(a, "excluded_reason", fix.excluded_reason, "r1"))
    if fix.new_date is not None and fix.new_date != old_date:
        changes += [Change(a, "date", fix.new_date, "r1"), Change(a, "year", fix.year, "r1")]
    return changes


def restrict_to_raw(rows: Sequence[dict[str, object]], raw_ids: frozenset[int]) -> list[dict[str, object]]:
    return [r for r in rows if int(str(r["accident_id"])) in raw_ids]


async def raw_rows(conn: AsyncConnection) -> list[dict[str, object]]:
    result = await conn.execute(
        text("SELECT accident_id, source, source_id, date, year FROM internal.accidents_raw ORDER BY accident_id")
    )
    rows = [dict(row._mapping) for row in result]
    if not rows:
        raise SystemExit("internal.accidents_raw is empty: 0004 snapshots accidents before any repair")
    return rows


def _as_date(value: object) -> date | None:
    return value if isinstance(value, date) else None


def _as_year(value: object) -> float | None:
    return float(str(value)) if value is not None else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


async def run_r2r1(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    # R1's neighbour rule and R2's day-15/Jul-1 rule describe the pre-2a AAC export only, so
    # both read the frozen snapshot (G17): a row R1 re-dated, or a hand edit to accidents,
    # can never become a trusted neighbour. Rows loaded after 0004 (plan 3's R11 facts CSV)
    # are absent from it: never trusted, never touched, only counted.
    live = await fetch_accidents(conn, ["date", "year", "excluded_reason"])
    rows = restrict_to_raw(await raw_rows(conn), frozenset(int(str(r["accident_id"])) for r in live))
    raw_id_set = frozenset(int(str(r["accident_id"])) for r in rows)
    current = {int(str(r["accident_id"])): r for r in restrict_to_raw(live, raw_id_set)}
    families = {int(str(r["accident_id"])): source_family(_as_str(r["source"])) for r in rows}
    schemes: dict[int, Scheme] = {}
    aac: list[AacRow] = []
    for r in rows:
        a = int(str(r["accident_id"]))
        if families[a] != "aac":
            continue
        schemes[a] = source_scheme(_as_str(r["source_id"]))
        d, year = _as_date(r["date"]), _as_year(r["year"])
        aac.append(AacRow(a, source_sort_key(_as_str(r["source_id"])), d, d.year if d else (int(year) if year is not None else None)))
    fixes = {f.accident_id: f for f in r1_year_fixes(aac)}
    r1_changes: list[Change] = []
    r2_changes: list[Change] = []
    r1_report, r2_report = ValidationReport("repair:r1"), ValidationReport("repair:r2")
    already_excluded_other = 0
    for r in rows:
        a = int(str(r["accident_id"]))
        d = _as_date(r["date"])
        year = _as_year(r["year"])
        live_excluded = _as_str(current[a]["excluded_reason"])
        excluded_elsewhere = live_excluded not in (None, YEAR_UNVERIFIED)
        if a in fixes:
            fix = fixes[a]
            live_date = _as_date(current[a]["date"])
            live_year = _as_year(current[a]["year"])
            r1_changes += changes_for(fix, old_date=live_date, old_excluded=live_excluded)
            if fix.excluded_reason:
                already_excluded_other += excluded_elsewhere
                # D10: an unverified row keeps its original date, so one an earlier rule
                # version or a hand edit moved is put back, with a revision showing the undo.
                if (live_date, live_year) != (d, year):
                    r1_changes += [Change(a, "date", d, "r1"), Change(a, "year", year, "r1")]
                r1_report.quarantine(str(a), fix.excluded_reason, scheme=schemes[a])
            else:
                if fix.new_date == live_date and live_year != fix.year:
                    r1_changes.append(Change(a, "year", fix.year, "r1"))
                r1_report.accept()
            continue
        precision = r2_precision(families[a], d, year)
        r2_changes.append(Change(a, "date_precision", precision, "r2"))
        if precision == "unknown":
            if excluded_elsewhere:
                already_excluded_other += 1
            else:
                r2_changes.append(Change(a, "excluded_reason", YEAR_UNVERIFIED, "r2"))
            r2_report.quarantine(str(a), "no_date")
        else:
            r2_report.accept()
    r1 = await run_step(conn, step="r1", rule_version=R1_VERSION, changes=r1_changes, report=r1_report, apply=args.apply)
    r2 = await run_step(conn, step="r2", rule_version=R2_VERSION, changes=r2_changes, report=r2_report, apply=args.apply)
    unverified_by_scheme = Counter(schemes[f.accident_id] for f in fixes.values() if f.excluded_reason)
    # D10: excluded, never deleted; the runbook reports these as a Phase 3 recency-bias note.
    r1_unverified = r1_report.quarantined[YEAR_UNVERIFIED]
    r2_no_date = r2_report.quarantined["no_date"]
    return {
        "r1": r1,
        "r2": r2,
        "r1_year_unverified": r1_unverified,
        "r2_no_date_unverified": r2_no_date,
        "year_unverified": r1_unverified + r2_no_date,
        "aac_schemes": {
            s: {
                "rows": n,
                "suspects": sum(1 for a in fixes if schemes[a] == s),
                "unverified": unverified_by_scheme[s],
            }
            for s, n in sorted(Counter(schemes.values()).items())
        },
        "not_in_raw": len(live) - len(rows),
        # Counted in the unverified totals above, but keeping the other step's reason.
        "already_excluded_other": already_excluded_other,
    }
