"""source_ingest_log bookkeeping: every job run is logged with counts, a content hash and
its validation summary; a repeat of the same (source, window, hash) is a no-op."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
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
_ALLOWED_SUMMARY_KEYS = frozenset({"source", "rows_in", "accepted", "quarantined", "problems"})


def sha256_rows(rows: Iterable[Sequence[object]]) -> str:
    digest = hashlib.sha256()
    for line in sorted(json.dumps(list(row), default=str, separators=(",", ":")) for row in rows):
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _validated_summary(report: ValidationReport, problems: list[str] | None) -> dict[str, object]:
    summary = report.summary() | {"problems": problems or []}
    extra = sorted(set(summary) - _ALLOWED_SUMMARY_KEYS)
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
) -> None:
    """Close out the run. Must run on the same connection, inside the same transaction, as
    the start_run/write_quarantine calls for this run_id — rows_quarantined here and the
    quarantine rows written separately must commit together, not as two independent writes
    a partial failure could split.

    Raises ValueError if run_id has no 'running' row to close on this connection (unknown
    run_id, or a repeat finish_run on an already-finished run)."""
    summary = _validated_summary(report, problems)
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
