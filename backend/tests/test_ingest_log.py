import asyncio
import json
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.ingest_log import (
    find_completed,
    finish_run,
    last_ok_rows_in,
    last_ok_sha,
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



class _RecordingConn:
    def __init__(self) -> None:
        self.params: dict[str, object] = {}

    async def execute(self, _stmt: object, params: dict[str, object]) -> object:
        self.params = params
        return type("Result", (), {"rowcount": 1})()


def test_finish_run_records_the_rule_version_once_in_the_summary():
    report = ValidationReport("mp_ticks_quarantine")
    report.accept()
    conn = _RecordingConn()
    asyncio.run(finish_run(conn, uuid.uuid4(), status="ok", report=report, rows_upserted=0, rule_version="r8-v1"))  # type: ignore[arg-type]
    assert json.loads(str(conn.params["report"]))["rule_version"] == "r8-v1"

    plain = _RecordingConn()
    asyncio.run(finish_run(plain, uuid.uuid4(), status="ok", report=report, rows_upserted=0))  # type: ignore[arg-type]
    assert "rule_version" not in json.loads(str(plain.params["report"]))


def test_finish_run_refuses_a_rule_version_that_is_not_a_plain_string():
    class LeakyReport(ValidationReport):
        def summary(self) -> dict[str, object]:
            return super().summary() | {"rule_version": {"row_ref": "r1", "lat": 40.1}}

    report = LeakyReport("fixture")
    report.accept()
    with pytest.raises(ValueError, match="rule_version must be a plain string"):
        asyncio.run(finish_run(_RecordingConn(), uuid.uuid4(), status="ok", report=report, rows_upserted=0))  # type: ignore[arg-type]

def test_finish_run_records_allowlisted_metrics_in_the_summary():
    report = ValidationReport("repair:r1-check")
    report.accept()
    conn = _RecordingConn()
    asyncio.run(finish_run(conn, uuid.uuid4(), status="ok", report=report, rows_upserted=0, metrics={"agreement": 0.75, "review_pending": 3}))  # type: ignore[arg-type]
    summary = json.loads(str(conn.params["report"]))
    assert (summary["agreement"], summary["review_pending"]) == (0.75, 3)


def test_finish_run_records_r6_metrics_in_the_summary():
    # G12: r6_verify logs its sample size, correlation and gust percentile through metrics
    # rather than a raw jsonb problems string; a None value (no linked rows yet) is never sent.
    report = ValidationReport("r6_verify")
    report.accept()
    conn = _RecordingConn()
    asyncio.run(finish_run(conn, uuid.uuid4(), status="ok", report=report, rows_upserted=0, metrics={"n": 150, "r_tmax": 0.97, "p99_gust": 42.5}))  # type: ignore[arg-type]
    summary = json.loads(str(conn.params["report"]))
    assert (summary["n"], summary["r_tmax"], summary["p99_gust"]) == (150, 0.97, 42.5)


def test_finish_run_records_tick_reload_metrics_in_the_summary():
    report = ValidationReport("mp_tick_aggregates")
    report.accept()
    conn = _RecordingConn()
    asyncio.run(finish_run(conn, uuid.uuid4(), status="ok", report=report, rows_upserted=0, metrics={"already_loaded_same": 4, "already_loaded_changed": 2}))  # type: ignore[arg-type]
    summary = json.loads(str(conn.params["report"]))
    assert (summary["already_loaded_same"], summary["already_loaded_changed"]) == (4, 2)


@pytest.mark.parametrize(
    "metrics, message",
    [
        ({"lat": 40.1}, "unexpected metric"),
        ({"source": 1}, "unexpected metric"),
        ({"agreement": "0.9"}, "must be a finite number"),
        ({"agreement": True}, "must be a finite number"),
        ({"agreement": float("nan")}, "must be a finite number"),
        ({"review_pending": {"row_ref": "r1"}}, "must be a finite number"),
    ],
)
def test_finish_run_refuses_metrics_outside_the_allowlist(metrics, message):
    report = ValidationReport("fixture")
    report.accept()
    with pytest.raises(ValueError, match=message):
        asyncio.run(finish_run(_RecordingConn(), uuid.uuid4(), status="ok", report=report, rows_upserted=0, metrics=metrics))  # type: ignore[arg-type]


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


@requires_pg
def test_last_ok_sha_is_the_latest_ok_run_only():
    async def finish(conn, sha: str, status: str) -> None:
        report = ValidationReport("fixture")
        report.accept()
        run_id = await start_run(conn, source="fixture", window_start=None, window_end=None, content_sha256=sha)
        await finish_run(conn, run_id, status=status, report=report, rows_upserted=0)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                assert await last_ok_sha(conn, "fixture") is None
            for sha, status in (("a", "ok"), ("b", "ok"), ("c", "rejected")):
                async with engine.begin() as conn:
                    await finish(conn, sha, status)
            async with engine.connect() as conn:
                assert await last_ok_sha(conn, "fixture") == "b"
                assert await last_ok_sha(conn, "other") is None
        finally:
            await engine.dispose()

    with migrated_db() as name:
        asyncio.run(scenario(sa_url(name)))
