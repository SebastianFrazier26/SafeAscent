"""R1 (AAC year repair from source_id neighbours) and R2 (date precision).

AAC source_ids form one running sequence (1-2685 (incl. a/b/t suffixes) on prod, read 2026-09-30; some ids carry
a/b/t suffix variants of one base) whose order follows accident year, so the trusted
(pre-2019) rows on either side of a suspect row bound its real year: no earlier than the
latest year on its left, no later than the earliest on its right. The point year is the
median of all neighbour years clamped into that bound; consumers must read a
year-precision row as the range [year_lo, year_hi]. A separate 8-digit scheme
(20190001...) is not a year-ordered sequence and is never a neighbour. No LLM, no
guessing: a row with a contradictory bound, neighbours spanning more than two years, too
few neighbours on either side, or no place in the sequence keeps its snapshot date and is
excluded as year_unverified (D10). Inputs come from the frozen internal.accidents_raw
snapshot.

r1-v2 (owner, 2026-10-13) is a second pass over the rows r1-v1 leaves unverified; r1-v1's
own fixes stand. A sequence row takes its bound from the 10th..90th percentile of the same
neighbours instead of the tight bound, so one out-of-order neighbour no longer voids it, and
is accepted when that range spans at most two years. An eight-digit row is dated 2018 within
2017..2018: every trusted row in that scheme is from those years (49 rows, all 2018, read
2026-10-13), and the rule refuses if the snapshot ever says otherwise.
"""

from __future__ import annotations

import argparse
import re
import statistics
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
R1V2_VERSION = "r1-v2"
R2_VERSION = "r2-v1"
SUSPECT_FROM_YEAR = 2019
NEIGHBOURS = 10
MIN_SIDE = 5
MAX_SPREAD = 2
YEAR_SOURCE = "aac_source_id_neighbours"
YEAR_UNVERIFIED = "year_unverified"
EIGHT_DIGIT_YEAR, EIGHT_DIGIT_LO, EIGHT_DIGIT_HI = 2018, 2017, 2018
EIGHT_DIGIT_MIN_TRUSTED = 10
# re.ASCII: without it \d matches e.g. Arabic-Indic digits, which int() then accepts.
_EIGHT_DIGIT = re.compile(r"^\d{8}$", re.ASCII)
_TRAILING_ID = re.compile(r"(\d+)([a-z]*)$", re.ASCII)

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
    eight_digit: bool = False


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


Trusted = list[tuple[SortKey, int]]


def _trusted_sequence(rows: Sequence[AacRow]) -> Trusted:
    return sorted(
        (r.sort_key, r.year)
        for r in rows
        if r.sort_key is not None and r.year is not None and r.year < SUSPECT_FROM_YEAR
    )


def _neighbours(trusted: Trusted, i: int, *, skip_self: bool = False) -> tuple[list[int], list[int]]:
    """Up to NEIGHBOURS trusted years either side of position i. skip_self is the backtest's
    leave-one-out: trusted[i] is the row being predicted, not a neighbour."""
    left = [y for _, y in trusted[max(0, i - NEIGHBOURS) : i]]
    start = i + 1 if skip_self else i
    right = [y for _, y in trusted[start : start + NEIGHBOURS]]
    return left, right


def v1_bounds(left: Sequence[int], right: Sequence[int]) -> tuple[int | None, int | None, int | None]:
    """(year_lo, year_hi, point); point None means unverified, with the bound r1-v1 stores."""
    lo, hi = max(left), min(right)
    if lo > hi:
        # The neighbours themselves are out of order here, so they bound nothing (and a
        # year_lo above year_hi would break the year_bounds check).
        return None, None, None
    years = [*left, *right]
    if max(years) - min(years) > MAX_SPREAD:
        # The spec's spread gate on the whole neighbourhood, on top of the tight bound: one
        # stray year on either side means the order is too noisy to trust here.
        return lo, hi, None
    # Median of all neighbours rather than lo or the midpoint, so a width-1 range leans
    # where the data does; an even-count tie on x.5 floors.
    return lo, hi, max(lo, min(hi, int(statistics.median(years))))


def _decile_x10(ys: Sequence[int], k: int) -> int:
    """10 x the k-th decile of sorted ys: linear interpolation between closest ranks at
    position (n-1)*k/10 (Hyndman-Fan type 7, numpy's and R's default; equal to
    statistics.quantiles(method="inclusive")). Integer arithmetic, so no float rounding can
    move a bound across a year boundary."""
    j, rem = divmod((len(ys) - 1) * k, 10)
    return 10 * ys[j] if rem == 0 else ys[j] * (10 - rem) + ys[j + 1] * rem


def robust_bounds(years: Sequence[int]) -> tuple[int, int, int]:
    """r1-v2: (floor(P10), ceil(P90), floor(median) clamped into them). Rounding the bound
    outwards keeps it a superset of the interpolated range. The clamp never binds (floor of
    the median lies between those roundings) but makes the invariant explicit."""
    ys = sorted(years)
    lo = _decile_x10(ys, 1) // 10
    hi = -(-_decile_x10(ys, 9) // 10)
    twice_median = ys[(len(ys) - 1) // 2] + ys[len(ys) // 2]
    return lo, hi, max(lo, min(hi, twice_median // 2))


def v2_bounds(years: Sequence[int]) -> tuple[int, int, int] | None:
    lo, hi, point = robust_bounds(years)
    return (lo, hi, point) if hi - lo <= MAX_SPREAD else None


def _dated(row: AacRow, lo: int, hi: int, point: int) -> YearFix:
    if lo == hi and row.date is not None:
        new_date, precision = _same_day_in(lo, row.date)
        return YearFix(row.accident_id, lo, lo, hi, precision, new_date, None)
    return YearFix(row.accident_id, point, lo, hi, "year", date(point, 7, 1), None)


def r1_year_fixes(rows: Sequence[AacRow]) -> list[YearFix]:
    trusted = _trusted_sequence(rows)
    keys = [k for k, _ in trusted]
    fixes: list[YearFix] = []
    for row in rows:
        if row.year is None or row.year < SUSPECT_FROM_YEAR:
            continue
        if row.sort_key is None:
            fixes.append(_unverified(row))
            continue
        left, right = _neighbours(trusted, bisect_left(keys, row.sort_key))
        if len(left) < MIN_SIDE or len(right) < MIN_SIDE:
            fixes.append(_unverified(row))
            continue
        lo, hi, point = v1_bounds(left, right)
        if lo is None or hi is None or point is None:
            fixes.append(_unverified(row, lo, hi))
        else:
            fixes.append(_dated(row, lo, hi, point))
    return fixes


def eight_digit_scheme_holds(rows: Sequence[AacRow]) -> bool:
    """The eight-digit rule is a fact about the snapshot, not a law: it applies only while
    enough trusted rows exist in that scheme and every one lies in 2017..2018."""
    years = [r.year for r in rows if r.eight_digit and r.year is not None and r.year < SUSPECT_FROM_YEAR]
    return len(years) >= EIGHT_DIGIT_MIN_TRUSTED and all(EIGHT_DIGIT_LO <= y <= EIGHT_DIGIT_HI for y in years)


def r1v2_year_fixes(rows: Sequence[AacRow]) -> list[YearFix]:
    """Fixes for the suspects r1-v1 leaves unverified and r1-v2 can date; nothing else.
    r1-v1 acceptance implies r1-v2 acceptance (its spread gate bounds P10..P90), so this is
    the whole of what r1-v2 adds."""
    unverified = {f.accident_id for f in r1_year_fixes(rows) if f.excluded_reason}
    trusted = _trusted_sequence(rows)
    keys = [k for k, _ in trusted]
    eight_ok = eight_digit_scheme_holds(rows)
    fixes: list[YearFix] = []
    for row in rows:
        if row.accident_id not in unverified:
            continue
        if row.eight_digit:
            if eight_ok:
                fixes.append(_dated(row, EIGHT_DIGIT_LO, EIGHT_DIGIT_HI, EIGHT_DIGIT_YEAR))
            continue
        if row.sort_key is None:
            continue
        left, right = _neighbours(trusted, bisect_left(keys, row.sort_key))
        if len(left) < MIN_SIDE or len(right) < MIN_SIDE:
            continue
        bounds = v2_bounds([*left, *right])
        if bounds is not None:
            fixes.append(_dated(row, *bounds))
    return fixes


@dataclass
class BacktestScore:
    rows: int = 0
    dated: int = 0
    within_one_year: int = 0
    in_range: int = 0

    def add(self, true_year: int, bounds: tuple[int, int, int] | None) -> None:
        self.rows += 1
        if bounds is None:
            return
        lo, hi, point = bounds
        self.dated += 1
        self.within_one_year += abs(point - true_year) <= 1
        self.in_range += lo <= true_year <= hi

    def summary(self) -> dict[str, object]:
        def rate(n: int) -> float | None:
            return round(n / self.dated, 4) if self.dated else None

        return {
            "rows": self.rows,
            "dated": self.dated,
            "coverage": round(self.dated / self.rows, 4) if self.rows else None,
            "within_one_year": self.within_one_year,
            "within_one_year_rate": rate(self.within_one_year),
            "in_range": self.in_range,
            "in_range_rate": rate(self.in_range),
        }


def r1_backtest(rows: Sequence[AacRow]) -> dict[str, dict[str, object]]:
    """Leave-one-out on trusted rows: hide each row's year, date it from the rest, compare.
    `shipped` is what r1-v1 then r1-v2 store (v1's bound where v1 accepts, else v2's)."""
    trusted = _trusted_sequence(rows)
    v1, v2, shipped = BacktestScore(), BacktestScore(), BacktestScore()
    for i, (_, true_year) in enumerate(trusted):
        left, right = _neighbours(trusted, i, skip_self=True)
        if len(left) < MIN_SIDE or len(right) < MIN_SIDE:
            for score in (v1, v2, shipped):
                score.add(true_year, None)
            continue
        lo, hi, point = v1_bounds(left, right)
        first = None if lo is None or hi is None or point is None else (lo, hi, point)
        second = v2_bounds([*left, *right])
        v1.add(true_year, first)
        v2.add(true_year, second)
        shipped.add(true_year, first or second)
    eight = BacktestScore()
    eight_years = [r.year for r in rows if r.eight_digit and r.year is not None and r.year < SUSPECT_FROM_YEAR]
    for i, true_year in enumerate(eight_years):
        others = [AacRow(0, None, None, y, True) for y in eight_years[:i] + eight_years[i + 1 :]]
        holds = eight_digit_scheme_holds(others)
        eight.add(true_year, (EIGHT_DIGIT_LO, EIGHT_DIGIT_HI, EIGHT_DIGIT_YEAR) if holds else None)
    return {"sequence_v1": v1.summary(), "sequence_v2": v2.summary(), "sequence_shipped": shipped.summary(),
            "eight_digit": eight.summary()}


def changes_for(
    fix: YearFix, *, old_date: date | None, old_excluded: str | None = None, method: str = "r1"
) -> list[Change]:
    a = fix.accident_id
    changes = [
        Change(a, "year_lo", fix.year_lo, method),
        Change(a, "year_hi", fix.year_hi, method),
        Change(a, "year_source", YEAR_SOURCE, method),
        Change(a, "date_precision", fix.date_precision, method),
    ]
    # R1 sets or lifts only its own exclusion; another step's reason stands either way (the
    # row is already excluded, and overwriting would lose why).
    if old_excluded in (None, YEAR_UNVERIFIED) and (fix.excluded_reason is not None or old_excluded is not None):
        changes.append(Change(a, "excluded_reason", fix.excluded_reason, method))
    if fix.new_date is not None and fix.new_date != old_date:
        changes += [Change(a, "date", fix.new_date, method), Change(a, "year", fix.year, method)]
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


@dataclass(frozen=True)
class Snapshot:
    rows: list[dict[str, object]]
    live_count: int
    current: dict[int, dict[str, object]]
    families: dict[int, SourceFamily]
    schemes: dict[int, Scheme]
    aac: list[AacRow]


async def load_snapshot(conn: AsyncConnection) -> Snapshot:
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
        aac.append(
            AacRow(
                a,
                source_sort_key(_as_str(r["source_id"])),
                d,
                d.year if d else (int(year) if year is not None else None),
                schemes[a] == "eight_digit",
            )
        )
    return Snapshot(rows, len(live), current, families, schemes, aac)


async def accident_ids_with_revisions(conn: AsyncConnection, rule_version: str) -> set[int]:
    result = await conn.execute(
        text("SELECT DISTINCT accident_id FROM internal.accident_revisions WHERE rule_version = :v"),
        {"v": rule_version},
    )
    return {int(a) for a in result.scalars()}


async def run_r2r1(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    snap = await load_snapshot(conn)
    rows, current, families, schemes, aac = snap.rows, snap.current, snap.families, snap.schemes, snap.aac
    fixes = {f.accident_id: f for f in r1_year_fixes(aac)}
    # A row r1-v2 has dated is r1-v2's now. An r1-v1 rerun would put its snapshot date back
    # (that key was never written when the date was already right) under a lifted exclusion.
    superseded = await accident_ids_with_revisions(conn, R1V2_VERSION) & fixes.keys()
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
            row_changes = changes_for(fix, old_date=live_date, old_excluded=live_excluded)
            if fix.excluded_reason:
                already_excluded_other += excluded_elsewhere
                # D10: an unverified row keeps its original date, so one an earlier rule
                # version or a hand edit moved is put back, with a revision showing the undo.
                if (live_date, live_year) != (d, year):
                    row_changes += [Change(a, "date", d, "r1"), Change(a, "year", year, "r1")]
                r1_report.quarantine(str(a), fix.excluded_reason, scheme=schemes[a])
            else:
                if fix.new_date == live_date and live_year != fix.year:
                    row_changes.append(Change(a, "year", fix.year, "r1"))
                r1_report.accept()
            if a not in superseded:
                r1_changes += row_changes
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
        "not_in_raw": snap.live_count - len(rows),
        # Counted in the unverified totals above, but keeping the other step's reason.
        "already_excluded_other": already_excluded_other,
        # Only when non-zero: Task 12's prod dry run must equal branch evidence saved before
        # r1-v2 existed, key for key, and before r1-v2 is applied this is always 0.
        **({"superseded_by_r1v2": len(superseded)} if superseded else {}),
    }


# The fields r1-v2 writes besides excluded_reason (which changes_for already guards).
R1V2_FIELDS = ("date", "year", "year_lo", "year_hi", "year_source", "date_precision")


async def require_r1v1(conn: AsyncConnection) -> None:
    applied = await conn.execute(
        text(
            "SELECT 1 FROM source_ingest_log WHERE source = 'repair:r1' AND status = 'ok' "
            "AND validation_report->>'rule_version' = :v LIMIT 1"
        ),
        {"v": R1_VERSION},
    )
    if applied.first() is None:
        raise SystemExit(f"r1v2 runs after {R1_VERSION}: apply r2r1 first")


async def decided_elsewhere(conn: AsyncConnection) -> set[int]:
    """Rows where any rule outside R1/R2, or an owner decision, has written one of r1-v2's
    fields: that decision is never re-made by r1-v2."""
    result = await conn.execute(
        text(
            "SELECT DISTINCT accident_id FROM internal.accident_revisions "
            "WHERE field = ANY(CAST(:fields AS text[])) AND rule_version NOT LIKE 'r1-%' AND rule_version NOT LIKE 'r2-%'"
        ),
        {"fields": list(R1V2_FIELDS)},
    )
    return {int(a) for a in result.scalars()}


async def run_r1v2(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    await require_r1v1(conn)
    snap = await load_snapshot(conn)
    fixes = r1v2_year_fixes(snap.aac)
    blocked = await decided_elsewhere(conn)
    report = ValidationReport("repair:r1v2")
    changes: list[Change] = []
    recovered: Counter[str] = Counter()
    for fix in fixes:
        a = fix.accident_id
        if a in blocked:
            report.quarantine(str(a), "decided_elsewhere")
            continue
        live = snap.current[a]
        live_date, live_year = _as_date(live["date"]), _as_year(live["year"])
        method = "r1v2-eight-digit" if snap.schemes[a] == "eight_digit" else "r1v2-sequence"
        changes += changes_for(fix, old_date=live_date, old_excluded=_as_str(live["excluded_reason"]), method=method)
        if fix.new_date == live_date and live_year != fix.year:
            changes.append(Change(a, "year", fix.year, method))
        recovered[snap.schemes[a]] += 1
        report.accept()
    result = await run_step(
        conn, step="r1v2", rule_version=R1V2_VERSION, changes=changes, report=report, apply=args.apply
    )
    v1_unverified = Counter(snap.schemes[f.accident_id] for f in r1_year_fixes(snap.aac) if f.excluded_reason)
    return {
        "r1v2": result,
        "r1v1_unverified": dict(sorted(v1_unverified.items())),
        "recovered": dict(sorted(recovered.items())),
        "still_unverified": sum(v1_unverified.values()) - sum(recovered.values()),
        "decided_elsewhere": report.quarantined["decided_elsewhere"],
        "eight_digit_scheme_holds": eight_digit_scheme_holds(snap.aac),
    }


async def run_r1v2_backtest(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    if args.apply:
        raise SystemExit("r1v2-backtest is read-only: drop --apply")
    snap = await load_snapshot(conn)
    return {"backtest": r1_backtest(snap.aac), "neighbours_per_side": NEIGHBOURS, "min_side": MIN_SIDE}
