"""source_ingest_log bookkeeping: every job run is logged with counts, a content hash and
its validation summary; a repeat of the same (source, window, hash) is a no-op."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.validate import ValidationReport

RunStatus = Literal["ok", "rejected", "failed"]

# validation_report is a public.source_ingest_log column, readable by every role that can
# read the public log; the offending row itself belongs only in internal.ingest_quarantine
# (M7, Task 2 review). Enforced here rather than trusted to callers, so a future change to
# ValidationReport.summary() that starts including raw issues fails loudly instead of leaking.
_ALLOWED_SUMMARY_KEYS = frozenset({"source", "rows_in", "accepted", "quarantined", "problems", "rule_version"})
# Run-level numbers a job may add (hand-check agreement, rows left for owner review, R6's
# sample size/correlation/gust percentile, the exposure snapshot's counts and Spearman). A new
# metric joins this set in a reviewed change rather than riding a raw jsonb UPDATE (G13).
_ALLOWED_METRICS = frozenset({
    "agreement", "review_pending", "n", "r_tmax", "p99_gust",
    "cells", "null_points", "full_coverage_cells", "tick_coverage_mean", "spearman_point_ticks",
    # mp_tick_aggregates --scope rock: routes kept off the quarantine (exposure plan X-1, 2026-10-21).
    "routes_waiting_unknown_route", "routes_waiting_ice_mixed_route", "routes_waiting_route_incomplete",
})


def sha256_rows(rows: Iterable[Sequence[object]]) -> str:
    digest = hashlib.sha256()
    for line in sorted(json.dumps(list(row), default=str, separators=(",", ":")) for row in rows):
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _validated_metrics(metrics: Mapping[str, object]) -> dict[str, int | float]:
    extra = sorted(set(metrics) - _ALLOWED_METRICS)
    if extra:
        raise ValueError(f"validation_report: unexpected metric(s) {extra}; allowed {sorted(_ALLOWED_METRICS)}")
    for name, value in metrics.items():
        # bool is an int subclass, and NaN/inf do not survive a jsonb round trip as numbers.
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError(f"validation_report metric {name!r} must be a finite number")
    return {name: value for name, value in metrics.items() if isinstance(value, int | float)}


def _validated_summary(
    report: ValidationReport,
    problems: list[str] | None,
    rule_version: str | None = None,
    metrics: Mapping[str, object] | None = None,
) -> dict[str, object]:
    summary = report.summary() | {"problems": problems or []}
    if rule_version is not None:
        summary["rule_version"] = rule_version
    if metrics:
        summary |= _validated_metrics(metrics)
    extra = sorted(set(summary) - _ALLOWED_SUMMARY_KEYS - set(metrics or {}))
    if extra:
        raise ValueError(
            f"validation_report summary carries unexpected key(s) {extra}; only counts and "
            "summaries belong here, never a raw row"
        )
    quarantined = summary["quarantined"]
    if not isinstance(quarantined, dict) or not all(isinstance(count, int) for count in quarantined.values()):
        raise ValueError("validation_report quarantined must be reason -> count, never a raw row")
    problem_list = summary["problems"]
    if not isinstance(problem_list, list) or not all(isinstance(item, str) for item in problem_list):
        raise ValueError("validation_report problems must be plain strings, never a raw row")
    if "rule_version" in summary and not isinstance(summary["rule_version"], str):
        raise ValueError("validation_report rule_version must be a plain string")
    return summary


async def start_run(
    conn: AsyncConnection,
    *,
    source: str,
    window_start: date | None,
    window_end: date | None,
    content_sha256: str | None,
) -> uuid.UUID:
    """Insert the 'running' row. Must run on the same connection, inside the same
    transaction, as the write_quarantine/finish_run calls for this run_id — the caller
    commits once, so the log row, its quarantine rows and its final counts land together
    or not at all."""
    run_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO source_ingest_log (run_id, source, window_start, window_end, status, content_sha256) "
            "VALUES (:run_id, :source, :ws, :we, 'running', :sha)"
        ),
        {"run_id": run_id, "source": source, "ws": window_start, "we": window_end, "sha": content_sha256},
    )
    return run_id


async def finish_run(
    conn: AsyncConnection,
    run_id: uuid.UUID,
    *,
    status: RunStatus,
    report: ValidationReport,
    rows_upserted: int,
    problems: list[str] | None = None,
    cost_units: float | None = None,
    rule_version: str | None = None,
    metrics: Mapping[str, int | float] | None = None,
) -> None:
    """Close out the run. Must run on the same connection, inside the same transaction, as
    the start_run/write_quarantine calls for this run_id — rows_quarantined here and the
    quarantine rows written separately must commit together, not as two independent writes
    a partial failure could split.

    rule_version, when given, lands in validation_report: the one place a rule-driven job
    records which rule judged the rows it left untouched. metrics, when given, adds
    allowlisted run-level numbers (see _ALLOWED_METRICS) beside the counts.

    Raises ValueError if run_id has no 'running' row to close on this connection (unknown
    run_id, or a repeat finish_run on an already-finished run)."""
    summary = _validated_summary(report, problems, rule_version, metrics)
    result = await conn.execute(
        text(
            "UPDATE source_ingest_log SET finished_at = now(), status = :status, rows_in = :rows_in, "
            "rows_upserted = :up, rows_quarantined = :q, validation_report = CAST(:report AS jsonb), "
            "cost_units = :cost WHERE run_id = :run_id AND status = 'running'"
        ),
        {
            "status": status,
            "rows_in": report.rows_in,
            "up": rows_upserted,
            "q": report.quarantined_total(),
            "report": json.dumps(summary),
            "cost": cost_units,
            "run_id": run_id,
        },
    )
    if result.rowcount == 0:
        raise ValueError(f"finish_run: no 'running' source_ingest_log row for run_id {run_id}")


async def find_completed(
    conn: AsyncConnection,
    *,
    source: str,
    window_start: date | None,
    window_end: date | None,
    content_sha256: str,
) -> uuid.UUID | None:
    result = await conn.execute(
        text(
            "SELECT run_id FROM source_ingest_log WHERE source = :source AND status = 'ok' "
            "AND window_start IS NOT DISTINCT FROM :ws AND window_end IS NOT DISTINCT FROM :we "
            "AND content_sha256 = :sha ORDER BY finished_at DESC LIMIT 1"
        ),
        {"source": source, "ws": window_start, "we": window_end, "sha": content_sha256},
    )
    found: uuid.UUID | None = result.scalar_one_or_none()
    return found


async def last_ok_rows_in(conn: AsyncConnection, source: str) -> int | None:
    result = await conn.execute(
        text(
            "SELECT rows_in FROM source_ingest_log WHERE source = :source AND status = 'ok' "
            "ORDER BY finished_at DESC LIMIT 1"
        ),
        {"source": source},
    )
    rows_in: int | None = result.scalar_one_or_none()
    return rows_in


async def last_ok_sha(conn: AsyncConnection, source: str) -> str | None:
    """content_sha256 of the source's latest ok run. A whole-table snapshot source compares
    against this, not find_completed: reloading an older file after a newer one must apply it."""
    result = await conn.execute(
        text(
            "SELECT content_sha256 FROM source_ingest_log WHERE source = :source AND status = 'ok' "
            "ORDER BY finished_at DESC LIMIT 1"
        ),
        {"source": source},
    )
    sha: str | None = result.scalar_one_or_none()
    return sha


async def write_quarantine(conn: AsyncConnection, run_id: uuid.UUID, report: ValidationReport) -> int:
    """Insert one row per issue. Must run on the same connection, inside the same
    transaction, as start_run/finish_run for this run_id, so these rows and finish_run's
    rows_quarantined count commit together."""
    if not report.issues:
        return 0
    await conn.execute(
        text(
            "INSERT INTO internal.ingest_quarantine (run_id, source, row_ref, reason, detail) "
            "VALUES (:run_id, :source, :row_ref, :reason, CAST(:detail AS jsonb))"
        ),
        [
            {
                "run_id": run_id,
                "source": report.source,
                "row_ref": issue.row_ref,
                "reason": issue.reason,
                "detail": json.dumps(issue.detail, default=str),
            }
            for issue in report.issues
        ],
    )
    return len(report.issues)
