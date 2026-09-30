import asyncio
import json
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair.framework import LOCATION, Change, apply_changes, fetch_accidents, run_step
from app.data.repair.sources import source_family
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url

SEED = """
INSERT INTO accidents (accident_id, source, date, latitude, longitude) VALUES
  (1, 'AAC', '2023-05-02', 40.0, -105.0),
  (2, 'NPS', '2010-07-01', 36.5, -118.3);
"""


@pytest.mark.parametrize(
    "raw,family",
    [("AAC", "aac"), ("aac_anac", "aac"), ("CAIC", "avalanche"), ("Avalanche.org", "avalanche"), ("NPS", "nps")],
)
def test_source_families(raw, family):
    assert source_family(raw) == family


def test_unknown_source_is_refused():
    with pytest.raises(ValueError, match="unmapped source"):
        source_family("Somewhere Else")


def test_unrepairable_field_is_refused():
    async def go() -> None:
        await apply_changes(None, [Change(1, "description", "x", "t")], rule_version="v", run_id=uuid.uuid4())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="not repairable"):
        asyncio.run(go())


def test_unreadable_column_is_refused():
    async def go() -> None:
        await fetch_accidents(None, ["accident_id; DROP TABLE accidents"])  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="not readable"):
        asyncio.run(go())


def test_dry_run_touches_nothing():
    report = ValidationReport(source="repair:r1")
    summary = asyncio.run(
        run_step(None, step="r1", rule_version="r1-v1", changes=[Change(1, "date", date(2003, 5, 2), "r1")],  # type: ignore[arg-type]
                 report=report, apply=False)
    )
    assert summary["mode"] == "dry_run" and "run_id" not in summary
    assert summary["proposed"] == {"date=2003-05-02": 1}


@requires_pg
def test_apply_is_audited_and_idempotent_per_rule_version():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            changes = [
                Change(1, "date", date(2003, 5, 2), "r1"),
                Change(1, "date_precision", "day", "r1"),
                Change(2, "date_precision", "day", "r2"),
            ]
            async with engine.begin() as conn:
                first = await apply_changes(conn, changes, rule_version="r1-v1", run_id=uuid.uuid4())
            async with engine.begin() as conn:
                second = await apply_changes(conn, changes, rule_version="r1-v1", run_id=uuid.uuid4())
            assert (first.applied, first.unchanged, first.already_recorded) == (3, 0, 0)
            assert (second.applied, second.already_recorded) == (0, 3)
            async with engine.connect() as conn:
                rows = await fetch_accidents(conn, ["date", "date_precision"])
                assert rows[0] == {"accident_id": 1, "date": date(2003, 5, 2), "date_precision": "day"}
                rev = (await conn.execute(text(
                    "SELECT old_value, new_value FROM internal.accident_revisions WHERE accident_id = 1 AND field = 'date'"
                ))).one()
                assert tuple(rev) == ("2023-05-02", "2003-05-02")
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_nulling_location_also_nulls_coordinates():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await apply_changes(conn, [Change(1, LOCATION, None, "r3")], rule_version="r3-v1", run_id=uuid.uuid4())
                await apply_changes(conn, [Change(2, LOCATION, (36.6, -118.29), "r3")], rule_version="r3-v1", run_id=uuid.uuid4())
            async with engine.connect() as conn:
                row1 = (await conn.execute(text(
                    "SELECT latitude, longitude, coordinates IS NULL FROM accidents WHERE accident_id = 1"))).one()
                row2 = (await conn.execute(text(
                    "SELECT round(ST_Y(coordinates::geometry)::numeric, 2) FROM accidents WHERE accident_id = 2"))).scalar_one()
            assert tuple(row1) == (None, None, True)
            assert float(row2) == 36.6
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_apply_logs_one_run_with_the_rule_version_through_the_allowlist():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            report = ValidationReport(source="repair:r1")
            report.accept()
            changes = [Change(1, "date_precision", "day", "r1")]
            async with engine.begin() as conn:
                summary = await run_step(conn, step="r1", rule_version="r1-v1", changes=changes, report=report, apply=True)
            assert summary["mode"] == "apply" and summary["applied"] == 1
            async with engine.connect() as conn:
                log = (await conn.execute(text(
                    "SELECT source, status, rows_upserted, content_sha256, validation_report::text "
                    "FROM source_ingest_log WHERE run_id = :r"), {"r": uuid.UUID(str(summary["run_id"]))})).one()
                rev_run = (await conn.execute(text(
                    "SELECT run_id FROM internal.accident_revisions WHERE accident_id = 1"))).scalar_one()
            assert (log.source, log.status, log.rows_upserted) == ("repair:r1", "ok", 1)
            assert log.content_sha256 != "r1-v1" and len(log.content_sha256) == 64
            assert json.loads(log.validation_report)["rule_version"] == "r1-v1"
            assert str(rev_run) == summary["run_id"]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        asyncio.run(scenario(sa_url(name)))
