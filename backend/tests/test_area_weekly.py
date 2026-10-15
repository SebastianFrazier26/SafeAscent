import argparse
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair.area_weekly import run_r7
from tests.pgtest import migrated_db, requires_pg, sa_url

ROW = "INSERT INTO area_weekly_weather (latitude, longitude, week_start, week_end) VALUES ({lat}, {lon}, '2020-01-06', '2020-01-12');"


def _run(name: str, apply: bool) -> dict[str, object]:
    async def go() -> dict[str, object]:
        engine = create_async_engine(sa_url(name))
        try:
            async with engine.begin() as conn:
                return await run_r7(conn, argparse.Namespace(apply=apply))
        finally:
            await engine.dispose()

    return asyncio.run(go())


@requires_pg
def test_the_single_sign_flipped_point_is_negated_once():
    with migrated_db(seed_sql=ROW.format(lat=40.1, lon=113.2) + ROW.format(lat=40.1, lon=-105.3)) as name:
        assert _run(name, apply=True)["updated"] == 1
        assert _run(name, apply=True)["updated"] == 0

        async def lons() -> list[float]:
            engine = create_async_engine(sa_url(name))
            try:
                async with engine.connect() as conn:
                    return sorted(float(v) for (v,) in (await conn.execute(text("SELECT longitude FROM area_weekly_weather"))).all())
            finally:
                await engine.dispose()

        assert asyncio.run(lons()) == [-113.2, -105.3]


@requires_pg
def test_unexpected_positive_longitudes_refuse():
    with migrated_db(seed_sql=ROW.format(lat=40.1, lon=113.2) + ROW.format(lat=40.1, lon=100.0)) as name:
        with pytest.raises(SystemExit, match="expected exactly one"):
            _run(name, apply=True)


@requires_pg
def test_dry_run_makes_no_change():
    with migrated_db(seed_sql=ROW.format(lat=40.1, lon=113.2)) as name:
        out = _run(name, apply=False)
        assert out == {"mode": "dry_run", "would_update_longitude": 113.2}

        async def lon() -> float:
            engine = create_async_engine(sa_url(name))
            try:
                async with engine.connect() as conn:
                    return float((await conn.execute(text("SELECT longitude FROM area_weekly_weather"))).scalar_one())
            finally:
                await engine.dispose()

        assert asyncio.run(lon()) == 113.2


@requires_pg
def test_no_positive_longitudes_is_a_clean_noop():
    with migrated_db(seed_sql=ROW.format(lat=40.1, lon=-105.3)) as name:
        out = _run(name, apply=True)
        assert out == {"updated": 0, "note": "no positive longitudes"}


def test_rowcount_mismatch_refuses_without_committing():
    """No real DB can produce this race (the guard count and the update see the same snapshot
    inside one transaction); a stub connection exercises the `updated != 1` refusal directly."""
    positive_result = MagicMock()
    positive_result.scalars.return_value.all.return_value = [Decimal("113.2")]
    update_result = MagicMock()
    update_result.rowcount = 2
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=[positive_result, update_result])
    with pytest.raises(SystemExit, match="expected to update exactly 1 row, updated 2"):
        asyncio.run(run_r7(conn, argparse.Namespace(apply=True)))
    assert conn.execute.await_count == 2
