"""Owner hand checks: yes/no decisions on a sampled review CSV become one source_ingest_log
row whose validation_report carries the agreement rate (the acceptance cells read it)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

YES_NO = frozenset({"yes", "no"})


def agreement(decisions: dict[int, str]) -> float:
    if not decisions:
        raise ValueError("no decisions")
    return sum(1 for v in decisions.values() if v == "yes") / len(decisions)


async def record_check(conn: AsyncConnection, source: str, decisions: dict[int, str]) -> dict[str, object]:
    rate = agreement(decisions)
    report = ValidationReport(source)
    for accident_id, value in decisions.items():
        if value == "yes":
            report.accept()
        else:
            report.quarantine(str(accident_id), "disagree")
    run_id = await start_run(conn, source=source, window_start=None, window_end=None, content_sha256=None)
    await finish_run(conn, run_id, status="ok", report=report, rows_upserted=0, metrics={"agreement": rate})
    return {"source": source, "n": len(decisions), "agreement": rate}
