"""Load the privately scraped MP ice/mixed tick aggregates (P2-14) into
internal.mp_tick_aggregates. Reads a local SQLite export only; it never fetches anything.

Months are the finest grain in the export, so completeness is judged per month (D3): a month
at or after the route's scrape month (cut-off min(today, scraped_at)) is partial for this
export forever, and months after the current UTC month are future. The outcome depends only
on the file, so reloading it in a later month is a no-op rather than a way to accept a
partial month. The table is INSERT-only: a stored (mp_route_id, period, style) row is never
changed by a later scrape, whether the newly exported count matches it or not (owner decision
2026-10-02). A matching key is skipped and counted in the run's stats as already_loaded_same;
a key whose count has grown or shrunk is also skipped, counted as already_loaded_changed, and
never quarantined or weighed against --max-quarantine-share — only a genuinely new key loads.
This also resolves the earlier known limitation where MP's ever-growing totals rejected most
later-scrape reloads outright (final review M9): a changed total no longer counts toward the
quarantine share, so a reload's newly closed months land even though its totals keep growing.

scraped_at is the private export's last-page time for the route, not its first (the scraper
upserts it on every page). A route whose pages straddle a month boundary could therefore
treat a month as partial when only its early days were actually unobserved; accepted for now
(owner-noted), since a route's pages are normally fetched back to back.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.db import ingest_engine
from app.pipelines.ingest_log import find_completed, finish_run, sha256_rows, start_run, write_quarantine
from app.pipelines.validate import ValidationReport, batch_gate

SOURCE = "mp_tick_aggregates"
STYLES = frozenset({"lead", "follow", "tr", "solo", "unknown"})
YEAR_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
ICE_MIXED = re.compile(r"\b(ice|mixed)\b", re.IGNORECASE)


@dataclass(frozen=True)
class RouteTotal:
    mp_route_id: int
    total_ticks: int
    complete: bool
    scraped_at: datetime


@dataclass(frozen=True)
class MonthlyRow:
    mp_route_id: int
    year_month: str
    style: str
    n: int


@dataclass(frozen=True)
class AggregateRow:
    mp_route_id: int
    period: str
    style: str
    tick_count: int
    scraped_at: datetime


def is_ice_mixed(route_type: str | None) -> bool:
    return route_type is not None and ICE_MIXED.search(route_type) is not None


def read_export(path: Path) -> tuple[list[RouteTotal], list[MonthlyRow]]:
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            totals = [
                RouteTotal(int(r), int(t), bool(c), datetime.fromisoformat(s))
                for r, t, c, s in db.execute(
                    "SELECT mp_route_id, total_ticks, complete, scraped_at FROM route_tick_totals"
                )
            ]
            monthly = [
                MonthlyRow(int(r), str(ym), str(st), int(n))
                for r, ym, st, n in db.execute("SELECT mp_route_id, year_month, style, n FROM route_tick_monthly")
            ]
        finally:
            db.close()
    except sqlite3.OperationalError as exc:
        # I1: the private scraper leaves the export in WAL mode. A mode=ro open can still need
        # to (re)create the -wal file (e.g. right after a checkpoint clears it) even for a read,
        # which fails right here if this process can't write to the export's directory — a live
        # writer alone is fine (SQLite's WAL readers don't need it). A `.backup` copy is a
        # consistent snapshot that opens read-only anywhere.
        raise SystemExit(
            f"cannot read {path} read-only ({exc}). This export is WAL-mode; make a consistent "
            f'snapshot first and load that instead: sqlite3 {path} ".backup <snapshot-path>"'
        ) from exc
    return totals, monthly


def _route_problem(route_id: int, total: RouteTotal | None, route_types: dict[int, str | None], today: date) -> str | None:
    if route_id not in route_types:
        return "unknown_route"
    if not is_ice_mixed(route_types[route_id]):
        return "not_ice_mixed"
    if total is None or not total.complete:
        return "route_incomplete"
    if total.scraped_at.tzinfo is None:
        return "scrape_time_unzoned"
    if total.scraped_at.astimezone(UTC).date() > today:
        return "scrape_after_today"
    return None


def _month_problem(year_month: str, *, today: date, cutoff: date) -> str | None:
    match = YEAR_MONTH.match(year_month)
    if match is None:
        return "bad_year_month"
    year, month = int(match.group(1)), int(match.group(2))
    if (year, month) > (today.year, today.month):
        return "future_month"
    if (year, month) >= (cutoff.year, cutoff.month):
        return "partial_month"
    if year < 1970:
        return "pre_1970"
    return None


def _accept_or_compare(
    row: AggregateRow,
    existing: dict[tuple[int, str, str], int],
    report: ValidationReport,
    accepted: list[AggregateRow],
    stats: dict[str, int],
) -> None:
    # Owner decision 2026-10-02: a stored key is never touched by a later scrape, matching
    # count or not — skip and count it, rather than quarantine (which used to reject whole
    # reloads once MP's ever-growing totals pushed count_changed past --max-quarantine-share).
    stored = existing.get((row.mp_route_id, row.period, row.style))
    report.accept()
    if stored is None:
        accepted.append(row)
    elif stored == row.tick_count:
        stats["already_loaded_same"] += 1
    else:
        stats["already_loaded_changed"] += 1


def validate(
    totals: list[RouteTotal],
    monthly: list[MonthlyRow],
    *,
    route_types: dict[int, str | None],
    existing: dict[tuple[int, str, str], int],
    today: date,
) -> tuple[list[AggregateRow], ValidationReport, dict[str, int]]:
    by_route = {t.mp_route_id: t for t in totals}
    report = ValidationReport(SOURCE)
    accepted: list[AggregateRow] = []
    stats = {
        "routes": len(by_route),
        "mp_reported_total": sum(t.total_ticks for t in totals),
        "accepted_month_ticks": 0,
        "already_loaded_same": 0,
        "already_loaded_changed": 0,
        "total_mismatch_routes": 0,
    }
    raw_sums: dict[int, int] = {}
    for row in monthly:
        raw_sums[row.mp_route_id] = raw_sums.get(row.mp_route_id, 0) + row.n
        ref = f"{row.mp_route_id}:{row.year_month}:{row.style}"
        total = by_route.get(row.mp_route_id)
        if (problem := _route_problem(row.mp_route_id, total, route_types, today)) is not None:
            report.quarantine(ref, problem)
            continue
        assert total is not None
        cutoff = min(today, total.scraped_at.astimezone(UTC).date())
        if (problem := _month_problem(row.year_month, today=today, cutoff=cutoff)) is not None:
            report.quarantine(ref, problem, n=row.n)
        elif row.style not in STYLES:
            report.quarantine(ref, "bad_style", style=row.style)
        elif row.n <= 0:
            report.quarantine(ref, "nonpositive_count", n=row.n)
        else:
            candidate = AggregateRow(row.mp_route_id, row.year_month, row.style, row.n, total.scraped_at)
            _accept_or_compare(candidate, existing, report, accepted, stats)
            stats["accepted_month_ticks"] += row.n
    for total in totals:
        ref = f"{total.mp_route_id}:total"
        if (problem := _route_problem(total.mp_route_id, total, route_types, today)) is not None:
            report.quarantine(ref, problem)
        elif total.total_ticks < 0:
            report.quarantine(ref, "bad_total", total=total.total_ticks)
        else:
            candidate = AggregateRow(total.mp_route_id, "total", "all", total.total_ticks, total.scraped_at)
            _accept_or_compare(candidate, existing, report, accepted, stats)
            if raw_sums.get(total.mp_route_id, 0) != total.total_ticks:
                stats["total_mismatch_routes"] += 1
    return accepted, report, stats


async def existing_counts(conn: AsyncConnection, route_ids: set[int]) -> dict[tuple[int, str, str], int]:
    if not route_ids:
        return {}
    result = await conn.execute(
        text(
            "SELECT mp_route_id, period, style, tick_count FROM internal.mp_tick_aggregates "
            "WHERE mp_route_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": sorted(route_ids)},
    )
    return {(int(r), str(p), str(s)): int(n) for r, p, s, n in result.all()}


SEAM_UPSERT = text(
    "INSERT INTO internal.route_tick_scrapes (mp_route_id, source, scraped_at, complete, complete_through, total_ticks, "
    "loaded_run_id) VALUES (:r, 'mp_scrape', :at, :complete, :through, "
    # Final review A4 (2026-10-14): the total is the stored months' sum over the counted range, not
    # the export's total. Stored months keep their first-scrape counts, so the export's total drifts
    # from them once MP backdates or deletes ticks; tick_total and route_tick_months must agree.
    "CASE WHEN :complete THEN (SELECT coalesce(sum(m.tick_count), 0) FROM internal.mp_tick_aggregates m "
    "WHERE m.mp_route_id = :r AND m.period <> 'total' "
    "AND to_date(m.period || '-01', 'YYYY-MM-DD') <= CAST(:through AS date)) END, :run) "
    "ON CONFLICT (mp_route_id) DO UPDATE SET scraped_at = EXCLUDED.scraped_at, complete = EXCLUDED.complete, "
    "complete_through = EXCLUDED.complete_through, total_ticks = EXCLUDED.total_ticks, "
    "loaded_run_id = EXCLUDED.loaded_run_id, updated_at = now() "
    "WHERE internal.route_tick_scrapes.scraped_at < EXCLUDED.scraped_at"
)
# Months after the cut-off are expected to be quarantined and lie outside the counted range.
OUTSIDE_COUNTED_RANGE = frozenset({"partial_month", "future_month"})


@dataclass(frozen=True)
class RouteScrape:
    mp_route_id: int
    scraped_at: datetime
    complete: bool
    total_ticks: int | None


def complete_through(scraped_at: datetime) -> date:
    """Last day of the last fully observed month: the month before the scrape's UTC month (D3)."""
    first = scraped_at.astimezone(UTC).date().replace(day=1)
    return first - timedelta(days=1)


def seam_rows(totals: list[RouteTotal], monthly: list[MonthlyRow], report: ValidationReport) -> list[RouteScrape]:
    """One seam row per route whose total validate accepted, already-loaded totals included, so a
    newer scrape advances complete_through and reveals its newly closed months. Older months stay
    at their first-scrape counts (owner decision 2026-10-02, only new keys load); advancing is
    still true for them. A route is complete only if every month inside its counted range was
    accepted and its months sum to its total: otherwise an absent month would read as a real
    zero, so it is written as scraped (never complete), with no total. A complete route's stored
    total is the sum of its stored months over the counted range (write_seam, final review A4), not
    the export's total_ticks carried here."""
    rejected_totals = {i.row_ref for i in report.issues if i.row_ref.endswith(":total")}
    gappy = {
        int(i.row_ref.split(":", 1)[0])
        for i in report.issues
        if not i.row_ref.endswith(":total") and i.reason not in OUTSIDE_COUNTED_RANGE
    }
    sums: dict[int, int] = {}
    for m in monthly:
        sums[m.mp_route_id] = sums.get(m.mp_route_id, 0) + m.n
    out = []
    for t in totals:
        if f"{t.mp_route_id}:total" in rejected_totals:
            continue
        complete = t.mp_route_id not in gappy and sums.get(t.mp_route_id, 0) == t.total_ticks
        out.append(RouteScrape(t.mp_route_id, t.scraped_at, complete, t.total_ticks if complete else None))
    return out


async def write_seam(conn: AsyncConnection, scrapes: list[RouteScrape], *, run_id: uuid.UUID) -> None:
    if scrapes:
        await conn.execute(SEAM_UPSERT, [
            {"r": s.mp_route_id, "at": s.scraped_at, "complete": s.complete,
             "through": complete_through(s.scraped_at) if s.complete else None, "run": run_id}
            for s in scrapes])


# One multi-row VALUES statement per chunk of this many rows (M3 follow-up, Task 6 review): each
# row costs 5 bind params (plus 2 shared), and a real export runs to ~35k rows, so an unchunked
# statement blew past asyncpg's ~32767-argument limit at 6,554 rows. 1000 keeps every statement
# far under that regardless of future param growth.
LOAD_CHUNK_ROWS = 1000


async def load(conn: AsyncConnection, rows: list[AggregateRow], *, run_id: uuid.UUID, scrape_run_id: str) -> int:
    """INSERT ... ON CONFLICT DO NOTHING RETURNING, counted from what came back rather than
    len(rows) (M3, Task 6 review): a stored row from an earlier run is a silent no-op here, not
    an upsert, so rows_upserted must reflect only what this call actually inserted. Chunked
    multi-row VALUES statements, not executemany — text()/asyncpg's implicit executemany doesn't
    return per-row results, only the last statement's. All chunks run on the caller's connection,
    inside the caller's transaction, so a batch either lands whole or not at all."""
    inserted = 0
    for start in range(0, len(rows), LOAD_CHUNK_ROWS):
        chunk = rows[start : start + LOAD_CHUNK_ROWS]
        values_sql = []
        params: dict[str, object] = {"scrape_run_id": scrape_run_id, "run_id": run_id}
        for i, r in enumerate(chunk):
            values_sql.append(
                f"(:mp_route_id_{i}, :period_{i}, :style_{i}, :tick_count_{i}, :scrape_run_id, :scraped_at_{i}, :run_id)"
            )
            params[f"mp_route_id_{i}"] = r.mp_route_id
            params[f"period_{i}"] = r.period
            params[f"style_{i}"] = r.style
            params[f"tick_count_{i}"] = r.tick_count
            params[f"scraped_at_{i}"] = r.scraped_at
        result = await conn.execute(
            text(
                "INSERT INTO internal.mp_tick_aggregates "
                "(mp_route_id, period, style, tick_count, scrape_run_id, scraped_at, loaded_run_id) "
                f"VALUES {', '.join(values_sql)} "
                "ON CONFLICT (mp_route_id, period, style) DO NOTHING "
                "RETURNING mp_route_id"
            ),
            params,
        )
        inserted += len(result.all())
    return inserted


async def main(path: Path, *, today: date, max_quarantine_share: float, dry_run: bool) -> dict[str, object]:
    totals, monthly = read_export(path)
    sha = sha256_rows(
        [(t.mp_route_id, t.total_ticks, t.complete, t.scraped_at.isoformat()) for t in totals]
        + [(m.mp_route_id, m.year_month, m.style, m.n) for m in monthly]
    )
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            if await find_completed(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=sha):
                return {"status": "noop", "rows_upserted": 0}
            route_types = {
                int(r): (str(t) if t is not None else None)
                for r, t in (await conn.execute(text("SELECT mp_route_id, type FROM mp_routes"))).all()
            }
            existing = await existing_counts(conn, {t.mp_route_id for t in totals})
            rows, report, stats = validate(totals, monthly, route_types=route_types, existing=existing, today=today)
            # previous_rows_in=None (F10, Task 6 review): this source is a one-time or occasional
            # load of a scrape that only grows between runs, so there is no "last run" row count a
            # swing check could reasonably compare against; only the quarantine-share gate applies.
            problems = batch_gate(
                report, previous_rows_in=None, count_tolerance=1.0, max_quarantine_share=max_quarantine_share
            )
            scrapes = seam_rows(totals, monthly, report)
            stats["seam_complete"] = sum(1 for sc in scrapes if sc.complete)
            stats["seam_scraped"] = len(scrapes) - stats["seam_complete"]
            if dry_run:
                return {"status": "dry_run", "report": report.summary(), "stats": stats, "problems": problems}
            run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=sha)
            await write_quarantine(conn, run_id, report)
            if problems:
                await finish_run(conn, run_id, status="rejected", report=report, rows_upserted=0, problems=problems)
                return {"status": "rejected", "rows_upserted": 0, "problems": problems, "report": report.summary()}
            inserted = await load(conn, rows, run_id=run_id, scrape_run_id=f"sqlite:{sha[:12]}")
            await write_seam(conn, scrapes, run_id=run_id)
            await finish_run(
                conn, run_id, status="ok", report=report, rows_upserted=inserted,
                metrics={
                    "already_loaded_same": stats["already_loaded_same"],
                    "already_loaded_changed": stats["already_loaded_changed"],
                },
            )
            return {"status": "ok", "rows_upserted": inserted, "report": report.summary(), "stats": stats}
    finally:
        await engine.dispose()


def cli() -> None:
    from app.services.temporal_weighting import utc_today

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--max-quarantine-share", type=float, default=0.10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        main(args.sqlite, today=utc_today(), max_quarantine_share=args.max_quarantine_share, dry_run=args.dry_run)
    )
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    cli()
