import argparse
import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.data.repair.area_weekly import run_r7
from tests.pgtest import migrated_db, requires_pg, sa_url

pytestmark = requires_pg
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


def test_unexpected_positive_longitudes_refuse():
    with migrated_db(seed_sql=ROW.format(lat=40.1, lon=113.2) + ROW.format(lat=40.1, lon=100.0)) as name:
        with pytest.raises(SystemExit, match="expected exactly one"):
            _run(name, apply=True)


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


def test_no_positive_longitudes_is_a_clean_noop():
    with migrated_db(seed_sql=ROW.format(lat=40.1, lon=-105.3)) as name:
        out = _run(name, apply=True)
        assert out == {"updated": 0, "note": "no positive longitudes"}
