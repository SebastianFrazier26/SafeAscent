import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.grid import grid_bucket
from app.pipelines.r6_verify import verify
from tests.pgtest import migrated_db, requires_pg, sa_url

COLS = ("accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, "
        "date_precision, geocode_precision, tz, year_source, year_lo, year_hi")


def _accident(i: int, precision: str, years: str = "NULL, NULL, NULL") -> str:
    return (f"INSERT INTO accidents ({COLS}) VALUES ({i}, 'AAC', '2010-07-10', 40.05, -105.25, true, 'climbing', "
            f"'US', '{precision}', 'crag', 'America/Denver', {years});"
            f"INSERT INTO weather (weather_id, accident_id, date, latitude, longitude, temperature_max) "
            f"VALUES ({i}, {i}, '2010-07-09', 40.05, -105.25, {20 + i});")


@requires_pg
def test_r6_reads_only_known_day_trusted_accidents():
    b = grid_bucket(40.05, -105.25)
    seed = (
        _accident(1, "day")
        + _accident(2, "month")
        + _accident(3, "day", "'aac_source_id_neighbours', 2009, 2010")
        + f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, record_kind, source) "
          f"VALUES ({b}, 'America/Denver', '2010-07-09', 21, 'era5', 'open_meteo_archive');"
    )

    async def go(url: str) -> dict[str, float | int | None]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return await verify(conn)
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        result = asyncio.run(go(sa_url(name)))
    assert result["n"] == 1
