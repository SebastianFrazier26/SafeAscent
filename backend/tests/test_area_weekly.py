import argparse
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair.area_weekly import R7_VERSION, run_r7
from tests.pgtest import fetch, migrated_db, requires_pg, sa_url

ROW = "INSERT INTO area_weekly_weather (latitude, longitude, week_start, week_end) VALUES ({lat}, {lon}, '{start}', '{end}');"
WEEKS = [("2025-02-03", "2025-02-09"), ("2025-02-10", "2025-02-16"), ("2025-02-17", "2025-02-23")]


def _point(lat: float, lon: float, weeks: list[tuple[str, str]] = WEEKS) -> str:
    return "".join(ROW.format(lat=lat, lon=lon, start=s, end=e) for s, e in weeks)


def _run(name: str, apply: bool) -> dict[str, object]:
    async def go() -> dict[str, object]:
        engine = create_async_engine(sa_url(name))
        try:
            async with engine.begin() as conn:
                return await run_r7(conn, argparse.Namespace(apply=apply))
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _lons(name: str) -> list[float]:
    async def go() -> list[float]:
        engine = create_async_engine(sa_url(name))
        try:
            async with engine.connect() as conn:
                return sorted(float(v) for (v,) in (await conn.execute(text("SELECT longitude FROM area_weekly_weather"))).all())
        finally:
            await engine.dispose()

    return asyncio.run(go())


@requires_pg
def test_every_week_of_the_single_flipped_point_is_negated_once():
    with migrated_db(seed_sql=_point(37.1, 113.2) + _point(40.1, -105.3)) as name:
        assert _run(name, apply=True)["updated"] == 3
        assert _run(name, apply=True)["updated"] == 0
        assert _lons(name) == [-113.2] * 3 + [-105.3] * 3
        [(status, rows_in, rows, report)] = fetch(
            name, "SELECT status, rows_in, rows_upserted, validation_report::text FROM source_ingest_log WHERE source = 'repair:r7'"
        )
        assert (status, rows_in, rows) == ("ok", 3, 3)
        assert R7_VERSION in report and "37.1" in report and "113.2" in report


@requires_pg
def test_a_second_positive_point_refuses():
    with migrated_db(seed_sql=_point(37.1, 113.2) + _point(40.1, 100.0, WEEKS[:1])) as name:
        with pytest.raises(SystemExit, match="expected exactly one positive point"):
            _run(name, apply=True)
        assert _lons(name) == [100.0] + [113.2] * 3


@requires_pg
def test_the_positive_longitude_at_another_latitude_refuses():
    with migrated_db(seed_sql=_point(40.1, 113.2)) as name:
        with pytest.raises(SystemExit, match="expected exactly one positive point"):
            _run(name, apply=False)


@requires_pg
def test_a_row_already_at_the_negated_point_refuses():
    seed = _point(37.1, 113.2, WEEKS[:2]) + _point(37.1, -113.2, WEEKS[2:])
    with migrated_db(seed_sql=seed) as name:
        with pytest.raises(SystemExit, match="already holds rows"):
            _run(name, apply=True)
        assert _lons(name) == [-113.2, 113.2, 113.2]


@requires_pg
def test_dry_run_reports_the_rows_and_makes_no_change():
    with migrated_db(seed_sql=_point(37.1, 113.2)) as name:
        out = _run(name, apply=False)
        assert out == {"mode": "dry_run", "latitude": 37.1, "would_update_longitude": 113.2, "would_update_rows": 3}
        assert _lons(name) == [113.2] * 3


@requires_pg
def test_no_positive_longitudes_is_a_clean_noop():
    with migrated_db(seed_sql=_point(40.1, -105.3)) as name:
        out = _run(name, apply=True)
        assert out == {"updated": 0, "note": "no positive longitudes"}


def _guard_result(rows: list[tuple[Decimal, Decimal, int, int]]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


def _stub(*results: MagicMock) -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=list(results))
    return conn


POINT = (Decimal("37.10"), Decimal("113.20"))


def test_stub_dry_run_counts_rows_from_the_guard():
    conn = _stub(_guard_result([(*POINT, 52, 0)]))
    out = asyncio.run(run_r7(conn, argparse.Namespace(apply=False)))
    assert out == {"mode": "dry_run", "latitude": 37.1, "would_update_longitude": 113.2, "would_update_rows": 52}
    assert conn.execute.await_count == 1


@pytest.mark.parametrize(
    "rows",
    [
        [(*POINT, 52, 0), (Decimal("40.10"), Decimal("100.00"), 1, 0)],
        [(Decimal("40.10"), Decimal("113.20"), 52, 0)],
        [(Decimal("37.10"), Decimal("113.30"), 52, 0)],
    ],
)
def test_stub_anything_but_the_one_expected_point_refuses(rows):
    conn = _stub(_guard_result(rows))
    with pytest.raises(SystemExit, match="expected exactly one positive point"):
        asyncio.run(run_r7(conn, argparse.Namespace(apply=True)))
    assert conn.execute.await_count == 1


def test_stub_collision_at_the_negated_point_refuses():
    conn = _stub(_guard_result([(*POINT, 52, 1)]))
    with pytest.raises(SystemExit, match="already holds rows"):
        asyncio.run(run_r7(conn, argparse.Namespace(apply=True)))
    assert conn.execute.await_count == 1


def test_rowcount_mismatch_refuses_without_committing():
    """No real DB can produce this race (the guard count and the update see the same snapshot
    inside one transaction); a stub connection exercises the rowcount refusal directly."""
    update_result = MagicMock()
    update_result.rowcount = 51
    conn = _stub(_guard_result([(*POINT, 52, 0)]), update_result)
    with pytest.raises(SystemExit, match="expected to update exactly 52 rows, updated 51"):
        asyncio.run(run_r7(conn, argparse.Namespace(apply=True)))
    assert conn.execute.await_count == 2
