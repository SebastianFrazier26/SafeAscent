import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.ingest_log import (
    find_completed,
    finish_run,
    last_ok_rows_in,
    sha256_rows,
    start_run,
    write_quarantine,
)
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url


def test_sha256_rows_ignores_order():
    assert sha256_rows([(1, "a"), (2, "b")]) == sha256_rows([(2, "b"), (1, "a")])
    assert sha256_rows([(1, "a")]) != sha256_rows([(1, "b")])


# --- M7 (Task 2 review, parked): source_ingest_log.validation_report must hold only
# summaries/counts, never a raw row. write_quarantine puts raw offending rows in
# internal.ingest_quarantine.detail instead; finish_run must refuse to write anything else.

def test_finish_run_refuses_a_report_whose_summary_carries_raw_row_data():
    class LeakyReport(ValidationReport):
        def summary(self) -> dict[str, object]:
            return super().summary() | {"issues": [{"row_ref": "r1", "detail": {"lat": 40.1, "lon": -105.6}}]}

    report = LeakyReport("fixture")
    report.accept()

    class ExplodingConn:
        async def execute(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("must not reach the database with a raw-row summary")

    async def scenario() -> None:
        with pytest.raises(ValueError, match="raw row"):
            await finish_run(ExplodingConn(), uuid.uuid4(), status="ok", report=report, rows_upserted=1)  # type: ignore[arg-type]

    asyncio.run(scenario())


@requires_pg
def test_run_lifecycle_quarantine_and_noop_lookup():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                report = ValidationReport("fixture")
                report.accept()
                report.quarantine("r2", "future", value="2027-01-01")
                run_id = await start_run(
                    conn, source="fixture", window_start=date(2026, 9, 1), window_end=date(2026, 9, 27), content_sha256="abc"
                )
                assert await write_quarantine(conn, run_id, report) == 1
                await finish_run(conn, run_id, status="ok", report=report, rows_upserted=1)
            async with engine.connect() as conn:
                assert await find_completed(
                    conn, source="fixture", window_start=date(2026, 9, 1), window_end=date(2026, 9, 27), content_sha256="abc"
                ) == run_id
                assert await find_completed(
                    conn, source="fixture", window_start=date(2026, 9, 1), window_end=date(2026, 9, 27), content_sha256="xyz"
                ) is None
                assert await last_ok_rows_in(conn, "fixture") == 2
                row = (await conn.execute(text(
                    "SELECT status, rows_in, rows_upserted, rows_quarantined, validation_report->'quarantined'->>'future', "
                    "validation_report::text "
                    "FROM source_ingest_log WHERE run_id = :r"), {"r": run_id})).one()
                status, rows_in, rows_upserted, rows_quarantined, future_count, report_text = row
                assert (status, rows_in, rows_upserted, rows_quarantined, future_count) == ("ok", 2, 1, 1, "1")
                # M7: the raw issue detail (the offending date value) lives only in
                # internal.ingest_quarantine, never in the public log's jsonb summary.
                assert "2027-01-01" not in report_text
                reason, detail = (await conn.execute(text(
                    "SELECT reason, detail->>'value' FROM internal.ingest_quarantine"
                ))).one()
                assert (reason, detail) == ("future", "2027-01-01")
        finally:
            await engine.dispose()

    with migrated_db() as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_finish_run_raises_for_unknown_or_already_finished_run():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                report = ValidationReport("fixture")
                report.accept()
                with pytest.raises(ValueError, match="no 'running'"):
                    await finish_run(conn, uuid.uuid4(), status="ok", report=report, rows_upserted=1)

                run_id = await start_run(conn, source="fixture", window_start=None, window_end=None, content_sha256="abc")
                await finish_run(conn, run_id, status="ok", report=report, rows_upserted=1)
                with pytest.raises(ValueError, match="no 'running'"):
                    await finish_run(conn, run_id, status="ok", report=report, rows_upserted=1)
        finally:
            await engine.dispose()

    with migrated_db() as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_rejected_runs_are_not_noops_and_not_row_count_baselines():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                report = ValidationReport("fixture")
                report.accept()
                run_id = await start_run(conn, source="fixture", window_start=None, window_end=None, content_sha256="abc")
                await finish_run(conn, run_id, status="rejected", report=report, rows_upserted=0, problems=["no rows"])
            async with engine.connect() as conn:
                assert await find_completed(conn, source="fixture", window_start=None, window_end=None, content_sha256="abc") is None
                assert await last_ok_rows_in(conn, "fixture") is None
        finally:
            await engine.dispose()

    with migrated_db() as name:
        asyncio.run(scenario(sa_url(name)))
