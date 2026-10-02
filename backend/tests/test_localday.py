import asyncio
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines import localday
from app.pipelines.grid import grid_bucket
from app.pipelines.localday import (
    INCIDENT_START,
    assign_accident_tz,
    local_date,
    register_all,
    register_logged,
    register_series,
    tz_for_point,
)
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url


@pytest.mark.parametrize(
    "lat,lon,zone",
    [
        (40.0, -105.3, "America/Denver"),
        (21.3, -157.8, "Pacific/Honolulu"),
        (61.2, -149.9, "America/Anchorage"),
        (51.88, -176.65, "America/Adak"),
        (36.58, -118.29, "America/Los_Angeles"),
        (44.27, -71.3, "America/New_York"),
    ],
)
def test_known_zones(lat, lon, zone):
    assert tz_for_point(lat, lon) == zone


def test_open_ocean_has_no_civil_zone():
    assert tz_for_point(35.0, -140.0) is None


def test_evening_storm_is_the_previous_local_day():
    storm = datetime(2020, 7, 11, 1, 30, tzinfo=timezone.utc)
    assert storm.date() == date(2020, 7, 11)
    assert local_date(storm, "America/Denver") == date(2020, 7, 10)
    assert local_date(storm, "Pacific/Honolulu") == date(2020, 7, 10)


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError, match="aware"):
        local_date(datetime(2020, 7, 11, 1, 30), "America/Denver")


class _FakeFinder:
    def __init__(self, name):
        self.name = name

    def timezone_at_land(self, *, lng, lat):
        return self.name


class _NoConn:
    async def execute(self, *a, **k):
        raise AssertionError("nothing may be written")


@pytest.mark.parametrize("name,reason", [("Mars/Olympus_Mons", "unknown_timezone"), ("Etc/UTC", "no_timezone"),
                                         ("", "unknown_timezone"), (None, "no_timezone")])
def test_unusable_zone_names_are_quarantined_before_any_write(monkeypatch, name, reason):
    # The DB only checks tz <> '': a zone the tz database cannot resolve must never reach a row.
    monkeypatch.setattr(localday, "_finder", lambda: _FakeFinder(name))
    report = ValidationReport("t")
    assert asyncio.run(register_series(_NoConn(), [(40.0, -105.3)], report=report)) == set()
    assert dict(report.quarantined) == {reason: 1}


@requires_pg
def test_register_series_is_idempotent_and_marks_normals_pending():
    points = [(40.01, -105.27), (40.02, -105.28), (50.0, -115.5)]

    async def scenario(url: str):
        engine = create_async_engine(url)
        try:
            report = ValidationReport("t")
            async with engine.begin() as conn:
                first = await register_series(conn, points, report=report)
                await register_series(conn, points, report=ValidationReport("t"))
                series = (await conn.execute(text("SELECT grid_bucket, tz, first_era5_date FROM grid_bucket_series"))).all()
                status = (await conn.execute(text("SELECT grid_bucket, status FROM cell_normals_status"))).all()
            return first, dict(report.quarantined), [tuple(r) for r in series], [tuple(r) for r in status]
        finally:
            await engine.dispose()

    b = grid_bucket(40.01, -105.27)
    with migrated_db() as name:
        first, quarantined, series, status = asyncio.run(scenario(sa_url(name)))
    assert first == {(b, "America/Denver")}
    assert quarantined == {"outside_us": 1}
    assert series == [(b, "America/Denver", None)]
    assert status == [(b, "pending")]


_CLEAN = "true, 'climbing', 'US', 'day', 'crag'"
SEED = (
    "INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, "
    "date_precision, geocode_precision, tz, year_source, year_lo, year_hi) VALUES "
    f"(1, 'AAC', '2010-07-10', 40.01, -105.27, {_CLEAN}, NULL, NULL, NULL, NULL), "
    # Before the incident series start (owner OQ2): a timezone, but no paid incident series.
    f"(2, 'AAC', '1985-06-01', 37.73, -119.6, {_CLEAN}, NULL, NULL, NULL, NULL), "
    # A year range is not a known day (R1): a timezone, but no incident series.
    f"(3, 'AAC', '2005-01-01', 44.27, -71.3, {_CLEAN}, NULL, 'aac_source_id_neighbours', 2004, 2006), "
    # No longer a trusted point: a stale zone is cleared.
    "(4, 'AAC', '2010-07-10', 21.3, -157.8, true, 'climbing', 'US', 'day', 'region_fallback', 'America/Denver', "
    "NULL, NULL, NULL), "
    # NULL country stays in accidents_clean (O3); the coarse US boxes reject southern BC.
    "(5, 'AAC', '2010-07-10', 50.0, -115.5, true, 'climbing', NULL, 'day', 'crag', NULL, NULL, NULL, NULL), "
    # Merged duplicate (not canonical): out of accidents_clean, so its zone is cleared too.
    "(6, 'AAC', '2010-07-10', 40.01, -105.27, false, 'climbing', 'US', 'day', 'crag', 'America/Denver', "
    "NULL, NULL, NULL);"
    "INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (900, 'Area', 61.2, -149.9);"
    "INSERT INTO mp_routes (mp_route_id, name, location_id, latitude, longitude) VALUES "
    "(1, 'R1', 900, NULL, NULL), (2, 'R2', 900, 40.02, -105.28), (3, 'R3', NULL, NULL, NULL);"
)
TZ_SQL = "SELECT accident_id, tz FROM accidents ORDER BY accident_id"


async def _register_twice(url: str, move_sql: str | None = None):
    engine = create_async_engine(url)
    try:
        report = ValidationReport("t")
        async with engine.begin() as conn:
            first = await register_all(conn, report=report)
            tz_first = [tuple(r) for r in (await conn.execute(text(TZ_SQL))).all()]
            if move_sql:
                await conn.execute(text(move_sql))
            second = await register_all(conn, report=ValidationReport("t"))
            tz_second = [tuple(r) for r in (await conn.execute(text(TZ_SQL))).all()]
            series = [tuple(r) for r in (await conn.execute(
                text("SELECT grid_bucket, tz FROM grid_bucket_series ORDER BY grid_bucket, tz"))).all()]
        return first, dict(report.quarantined), tz_first, second, tz_second, series
    finally:
        await engine.dispose()


def test_incident_series_start_is_the_owner_1990_decision():
    assert INCIDENT_START == date(1990, 1, 1)


@requires_pg
def test_register_all_assigns_clears_and_registers_incident_and_route_series():
    boulder, anchorage = grid_bucket(40.01, -105.27), grid_bucket(61.2, -149.9)
    with migrated_db(seed_sql=SEED) as name:
        first, quarantined, tz_first, second, tz_second, series = asyncio.run(_register_twice(sa_url(name)))
    assert first == {"accident_tz_changed": 5, "accident_tz_assigned": 3, "incident_series": 1,
                     "route_series": 2, "series_total": 2}
    assert quarantined == {"outside_us": 1}
    assert tz_first == [(1, "America/Denver"), (2, "America/Los_Angeles"), (3, "America/New_York"),
                        (4, None), (5, None), (6, None)]
    # A rerun changes nothing and registers the same series.
    assert second == {**first, "accident_tz_changed": 0}
    assert tz_second == tz_first
    assert series == [(boulder, "America/Denver"), (anchorage, "America/Anchorage")]


@requires_pg
def test_a_moved_point_gets_its_new_zone_on_the_next_register():
    # D2: a review that moves a point across a zone must not leave the old zone behind.
    move = "UPDATE accidents SET latitude = 21.3, longitude = -157.8 WHERE accident_id = 1"
    with migrated_db(seed_sql=SEED) as name:
        _, _, _, second, tz_second, series = asyncio.run(_register_twice(sa_url(name), move))
    assert second["accident_tz_changed"] == 1
    assert dict(tz_second)[1] == "Pacific/Honolulu"
    assert (grid_bucket(21.3, -157.8), "Pacific/Honolulu") in series


@requires_pg
def test_assign_accident_tz_alone_counts_only_changed_rows():
    async def scenario(url: str):
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                return [await assign_accident_tz(conn, report=ValidationReport("t")) for _ in range(2)]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        assert asyncio.run(scenario(sa_url(name))) == [5, 0]


@requires_pg
def test_register_logged_writes_one_ok_run_with_its_quarantine():
    async def scenario(url: str):
        engine = create_async_engine(url)
        try:
            result = await register_logged(engine)
            async with engine.connect() as conn:
                log = (await conn.execute(text(
                    "SELECT source, status, rows_upserted, rows_quarantined FROM source_ingest_log"))).all()
                quarantine = (await conn.execute(text(
                    "SELECT source, reason FROM internal.ingest_quarantine"))).all()
            return result, [tuple(r) for r in log], [tuple(r) for r in quarantine]
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        result, log, quarantine = asyncio.run(scenario(sa_url(name)))
    assert result["series_total"] == 2
    assert result["report"]["quarantined"] == {"outside_us": 1}
    assert log == [("series_register", "ok", 2, 1)]
    assert quarantine == [("series_register", "outside_us")]
