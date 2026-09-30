import argparse
import asyncio
import csv
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair import review
from app.data.repair.__main__ import STEPS
from app.data.repair.checks import agreement, record_check
from app.data.repair.dates import run_r2r1
from app.data.repair.severity import (
    LOCATOR_COLUMNS,
    r1_check_rows,
    run_r1_export,
    run_r1_import,
    run_r9,
    run_r9_export,
    run_r9_import,
    severity_scale,
)
from tests.pgtest import migrated_db, requires_pg, sa_url
from tests.test_repair_dates import SUSPECT_SEED


def test_nps_is_a_mortality_source():
    assert severity_scale("nps", "Fatal") == "fatal_only"
    assert severity_scale("nps", None) == "fatal_only"


def test_other_sources_are_full_scale_when_severity_is_present():
    assert severity_scale("aac", "Serious") == "full"
    assert severity_scale("aac", "  ") == "unknown"
    assert severity_scale("avalanche", None) == "unknown"


@pytest.mark.parametrize(
    "value, scale",
    [("Unknown", "unknown"), ("unknown ", "unknown"), ("N/A", "unknown"), ("none", "unknown"), ("", "unknown"),
     ("near_miss", "full"), ("Near_Miss", "full"), (" FATAL", "full"), ("minor", "full")],
)
def test_severity_vocabulary_is_normalised(value, scale):
    assert severity_scale("avalanche", value) == scale


@pytest.mark.parametrize("family", ["aac", "avalanche", "nps"])
def test_an_unmapped_severity_refuses_rather_than_guessing(family):
    with pytest.raises(ValueError, match="unmapped injury_severity"):
        severity_scale(family, "critical")


def test_agreement_rate():
    assert agreement({1: "yes", 2: "yes", 3: "no", 4: "yes"}) == 0.75
    with pytest.raises(ValueError):
        agreement({})


def test_r1_check_excludes_unverified_rows_and_keeps_the_locator():
    base = {"source": "AAC", "date": date(2003, 5, 2), "year_lo": 2003, "year_hi": 2003,
            "year_source": "aac_source_id_neighbours", "source_url": None,
            "source_id": "ANAC-2003-0001", "location": "Fixture Canyon", "mountain": "Fixture Peak"}
    rows = [
        base | {"accident_id": 1, "excluded_reason": None},
        base | {"accident_id": 2, "excluded_reason": "year_unverified"},
        base | {"accident_id": 3, "excluded_reason": None, "year_source": None},
    ]
    out = r1_check_rows(rows)
    assert [r["accident_id"] for r in out] == [1]
    assert {"source_id", "location", "mountain"} <= set(out[0])


def test_cli_registers_the_r9_and_hand_check_steps():
    assert {"r9", "r9-export", "r9-import", "r1-export", "r1-import"} <= set(STEPS)


def test_record_check_refuses_an_empty_check_before_writing():
    class ExplodingConn:
        async def execute(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("an empty check must not reach the database")

    with pytest.raises(ValueError, match="no decisions"):
        asyncio.run(record_check(ExplodingConn(), "repair:r9-check", {}))  # type: ignore[arg-type]


SEVERITY_SEED = """
INSERT INTO accidents (accident_id, source, source_id, date, location, mountain, injury_severity, description) VALUES
  (1, 'AAC', '101', '2003-05-02', 'Fixture Canyon', 'Fixture Peak', 'Serious', 'narrative one'),
  (2, 'AAC', '102', '2004-06-03', 'Fixture Gully', NULL, '  ', 'narrative two'),
  (3, 'NPS', NULL, '2010-07-01', NULL, NULL, NULL, 'narrative three'),
  (4, 'NPS', NULL, '2011-08-02', NULL, NULL, 'Fatal', NULL),
  (5, 'avalanche.org', NULL, '2012-01-09', NULL, NULL, NULL, NULL);
"""


async def _scalars(conn, sql: str) -> list[object]:
    return list((await conn.execute(text(sql))).scalars())


@requires_pg
def test_r9_labels_every_row_and_a_rerun_changes_nothing():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                dry = await run_r9(conn, argparse.Namespace(apply=False))
                assert dry["mode"] == "dry_run"
                assert await _scalars(conn, "SELECT count(*) FROM accidents WHERE severity_scale IS NOT NULL") == [0]
            async with engine.begin() as conn:
                first = await run_r9(conn, argparse.Namespace(apply=True))
            async with engine.begin() as conn:
                again = await run_r9(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                scales = await _scalars(conn, "SELECT severity_scale FROM accidents ORDER BY accident_id")
                severities = await _scalars(conn, "SELECT injury_severity FROM accidents ORDER BY accident_id")
            assert scales == ["full", "unknown", "fatal_only", "fatal_only", "unknown"]
            # The label never rewrites the recorded severity itself.
            assert severities == ["Serious", "  ", None, "Fatal", None]
            assert first["applied"] == 5
            assert first["crosstab"] == {
                "aac|full|serious": 1, "aac|unknown|": 1, "avalanche|unknown|": 1,
                "nps|fatal_only|": 1, "nps|fatal_only|fatal": 1,
            }
            assert (again["applied"], again["already_recorded"]) == (0, 5)
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEVERITY_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


def _fill(path, column: str, values: dict[str, str]) -> None:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row[column] = values[row["accident_id"]]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@requires_pg
def test_r9_hand_check_round_trip_carries_the_locator_and_logs_the_agreement(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                exported = await run_r9_export(conn, argparse.Namespace(out=None))
            path = tmp_path / "severity_check.csv"
            assert exported["exported"] == 2 and exported["path"] == str(path)
            with path.open(newline="", encoding="utf-8") as fh:
                header = next(csv.reader(fh))
            assert header == [*LOCATOR_COLUMNS, "injury_severity", "agree"]
            assert "narrative" not in path.read_text(encoding="utf-8")
            _fill(path, "agree", {"1": "yes", "2": "no"})
            stray = tmp_path / "stray.csv"
            stray.write_text(path.read_text(encoding="utf-8") + "3,,,,,,yes\n", encoding="utf-8")
            async with engine.begin() as conn:
                with pytest.raises(ValueError, match="outside the hand-check pool"):
                    await run_r9_import(conn, argparse.Namespace(review_file=str(stray)))
            async with engine.begin() as conn:
                out = await run_r9_import(conn, argparse.Namespace(review_file=str(path)))
            assert out == {"source": "repair:r9-check", "n": 2, "agreement": 0.5}
            async with engine.connect() as conn:
                row = (await conn.execute(text(
                    "SELECT status, rows_in, (validation_report->>'agreement')::float, "
                    "validation_report->'quarantined'->>'disagree' "
                    "FROM source_ingest_log WHERE source = 'repair:r9-check'"))).one()
            assert tuple(row) == ("ok", 2, 0.5, "1")
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEVERITY_SEED) as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_r1_hand_check_exports_only_re_dated_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path)

    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await run_r2r1(conn, argparse.Namespace(apply=True))
            async with engine.connect() as conn:
                exported = await run_r1_export(conn, argparse.Namespace(out=None))
            path = tmp_path / "r1_year_check.csv"
            with path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            # Row 11 is re-dated from its neighbours; 22 and 23 are year_unverified and stay out.
            assert exported["exported"] == 1
            assert [(r["accident_id"], r["source_id"], r["year_lo"]) for r in rows] == [("11", "11", "2003")]
            _fill(path, "within_one_year", {"11": "yes"})
            stray = tmp_path / "stray.csv"
            stray.write_text(path.read_text(encoding="utf-8") + "22,,,,,,,,yes\n", encoding="utf-8")
            async with engine.begin() as conn:
                with pytest.raises(ValueError, match="outside the hand-check pool"):
                    await run_r1_import(conn, argparse.Namespace(review_file=str(stray)))
            async with engine.begin() as conn:
                out = await run_r1_import(conn, argparse.Namespace(review_file=str(path)))
            assert out["agreement"] == 1.0
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SUSPECT_SEED) as name:
        asyncio.run(scenario(sa_url(name)))
