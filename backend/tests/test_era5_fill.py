import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.era5_fill import (
    INCIDENT_START_DEFAULT,
    Chunk,
    Gap,
    PruneRefused,
    SeriesCoverage,
    load_coverage,
    log_prune,
    plan_chunks,
    prune,
    run,
    series_gaps,
)
from app.pipelines.grid import grid_bucket
from app.pipelines.localday import INCIDENT_START
from app.pipelines.open_meteo import DAILY_VARS, ArchiveClient
from app.pipelines.pro_window import Budget, ProWindow, units_spent
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)
TZ = "America/Denver"


def test_incident_series_start_six_days_before_the_owner_1990_start():
    # The first 1990 incident's 7-day window (offsets -6..0) must be complete.
    assert INCIDENT_START_DEFAULT == INCIDENT_START - timedelta(days=6) == date(1989, 12, 26)


def test_gaps_for_new_extended_and_complete_series():
    coverage = [
        SeriesCoverage(1, TZ, None, None, incident=False),
        SeriesCoverage(2, TZ, date(2023, 1, 1), date(2026, 1, 5), incident=True),
        SeriesCoverage(3, TZ, date(2023, 1, 1), date(2026, 9, 23), incident=False),
        SeriesCoverage(4, TZ, None, None, incident=True),
        # A former incident series (its point moved): treated like any other, no back-fill.
        SeriesCoverage(5, TZ, date(1989, 12, 26), date(2026, 9, 23), incident=False),
    ]
    gaps = series_gaps(coverage, today=TODAY, incident_start=date(1940, 1, 1))
    assert gaps == [
        Gap(1, TZ, date(2023, 1, 1), date(2026, 9, 23), "forward"),
        Gap(2, TZ, date(1940, 1, 1), date(2022, 12, 31), "backward"),
        Gap(2, TZ, date(2026, 1, 6), date(2026, 9, 23), "forward"),
        # D14: a new incident series buys its recent 3 years first, then walks back from them.
        Gap(4, TZ, date(2023, 1, 1), date(2026, 9, 23), "forward"),
        Gap(4, TZ, date(1940, 1, 1), date(2022, 12, 31), "backward"),
    ]


def test_chunks_cover_every_day_once_in_fill_order():
    forward = plan_chunks([Gap(b, TZ, date(2020, 1, 1), date(2026, 9, 23), "forward") for b in (1, 2, 3)],
                          batch=2, years_per_chunk=3)
    assert [(c.buckets, c.start, c.end) for c in forward] == [
        ((1, 2), date(2020, 1, 1), date(2022, 12, 31)),
        ((1, 2), date(2023, 1, 1), date(2025, 12, 31)),
        ((1, 2), date(2026, 1, 1), date(2026, 9, 23)),
        ((3,), date(2020, 1, 1), date(2022, 12, 31)),
        ((3,), date(2023, 1, 1), date(2025, 12, 31)),
        ((3,), date(2026, 1, 1), date(2026, 9, 23)),
    ]
    backward = plan_chunks([Gap(1, TZ, date(2015, 1, 1), date(2022, 12, 31), "backward")], batch=50, years_per_chunk=3)
    assert [(c.start, c.end) for c in backward] == [
        (date(2020, 1, 1), date(2022, 12, 31)), (date(2017, 1, 1), date(2019, 12, 31)), (date(2015, 1, 1), date(2016, 12, 31)),
    ]
    assert len({c.group for c in backward}) == 1
    assert Chunk(TZ, (1, 2), date(2020, 1, 1), date(2020, 1, 14), "g").units() == 2


def test_every_forward_group_is_bought_before_any_backward_group():
    gaps = series_gaps([SeriesCoverage(1, "America/Anchorage", None, None, incident=True),
                        SeriesCoverage(2, TZ, None, None, incident=False)],
                       today=TODAY, incident_start=date(2015, 1, 1))
    chunks = plan_chunks(gaps, batch=50, years_per_chunk=5)
    kinds = [c.group.split("|")[3] for c in chunks]
    assert kinds == sorted(kinds, key=lambda k: k != "forward") and kinds[-1] == "backward"


def test_different_timezones_never_share_a_chunk():
    chunks = plan_chunks([Gap(1, TZ, date(2020, 1, 1), date(2020, 1, 5), "forward"),
                          Gap(2, "America/Los_Angeles", date(2020, 1, 1), date(2020, 1, 5), "forward")],
                         batch=50, years_per_chunk=1)
    assert sorted((c.tz, c.buckets) for c in chunks) == [("America/Denver", (1,)), ("America/Los_Angeles", (2,))]


def _handler_with(value: Callable[[int], float | None], *, drop_last_day: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
        n = (end - start).days + 1 - (1 if drop_last_day else 0)
        days = [(start + timedelta(d)).isoformat() for d in range(n)]
        body = []
        for lat, lon in zip(params["latitude"].split(","), params["longitude"].split(",")):
            daily: dict[str, object] = {"time": days}
            for var in DAILY_VARS:
                daily[var] = [value(i) for i in range(n)]
            body.append({"latitude": float(lat), "longitude": float(lon), "timezone": params["timezone"],
                         "daily": daily})
        return httpx.Response(200, json=body)

    return handler


_handler = _handler_with(lambda i: 1.0)


def _failing(request: httpx.Request) -> httpx.Response:
    return httpx.Response(400, json={"error": True, "reason": "bad"})


async def _coverage_and_rows(url: str) -> tuple[list[SeriesCoverage], int]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            cov = await load_coverage(conn)
            n = (await conn.execute(text("SELECT count(*) FROM cell_daily_conditions WHERE record_kind = 'era5'"))).scalar_one()
        return cov, int(n)
    finally:
        await engine.dispose()


async def _log(url: str) -> list[tuple[object, ...]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT status, cost_units, rows_upserted, rows_quarantined FROM source_ingest_log "
                "WHERE source = 'era5_fill' ORDER BY window_start"))).all()
        return [(s, float(c) if c is not None else None, u, q) for s, c, u, q in rows]
    finally:
        await engine.dispose()


B = grid_bucket(40.0, -105.3)
SERIES_SEED = f"INSERT INTO grid_bucket_series (grid_bucket, tz) VALUES ({B}, '{TZ}');"
GAP = Gap(B, TZ, date(2019, 12, 22), date(2020, 1, 10), "forward")


@requires_pg
def test_resume_fills_only_the_remaining_gap():
    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(_handler))
        capped = await run(lambda: create_async_engine(url), client, plan_chunks([GAP], batch=50, years_per_chunk=1),
                           today=TODAY, budget=Budget(max_units=1, spent=0))
        cov_after_cap, _ = await _coverage_and_rows(url)
        remaining = Gap(B, TZ, cov_after_cap[0].last + timedelta(days=1), GAP.end, "forward")
        full = await run(lambda: create_async_engine(url), client, plan_chunks([remaining], batch=50, years_per_chunk=1),
                         today=TODAY, budget=Budget(max_units=100, spent=0))
        cov, rows = await _coverage_and_rows(url)
        return capped, cov_after_cap, full, cov, rows

    with migrated_db(seed_sql=SERIES_SEED) as name:
        capped, cov_after_cap, full, cov, rows = asyncio.run(scenario(sa_url(name)))
        log = asyncio.run(_log(sa_url(name)))
    assert (capped["done"], capped["stopped_for_budget"]) == (1, True)
    assert (cov_after_cap[0].first, cov_after_cap[0].last) == (date(2019, 12, 22), date(2019, 12, 31))
    assert (full["done"], full["stopped_for_budget"]) == (1, False)
    assert (cov[0].first, cov[0].last, rows) == (date(2019, 12, 22), date(2020, 1, 10), 20)
    assert log == [("ok", 1.0, 10, 0), ("ok", 1.0, 10, 0)]


@requires_pg
def test_failed_chunk_blocks_later_chunks_of_its_group():
    chunks = plan_chunks([GAP], batch=50, years_per_chunk=1)

    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(_failing))
        budget = Budget(max_units=100, spent=0)
        result = await run(lambda: create_async_engine(url), client, chunks, today=TODAY, budget=budget)
        cov, rows = await _coverage_and_rows(url)
        return result, budget.spent, cov, rows

    with migrated_db(seed_sql=SERIES_SEED) as name:
        result, spent, cov, rows = asyncio.run(scenario(sa_url(name)))
        log = asyncio.run(_log(sa_url(name)))
    assert (result["failed"], result["blocked_after_failure"], result["done"]) == (1, 1, 0)
    assert spent == 1
    assert (cov[0].first, cov[0].last, rows) == (None, None, 0)
    assert log == [("failed", 1.0, 0, 0)]


class _Killed(BaseException):
    """Stands in for SIGKILL/KeyboardInterrupt: nothing in run() may catch it."""


@requires_pg
def test_a_run_killed_mid_chunk_keeps_every_billed_unit_in_the_window_total():
    # D5: chunk 2's request reached the server (billed) and the process died before its writes.
    calls = {"n": 0}

    def dies_on_second(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            raise _Killed()
        return _handler(request)

    window = ProWindow(datetime.now(timezone.utc).date() - timedelta(days=1), initial=True)

    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(dies_on_second))
        with pytest.raises(_Killed):
            await run(lambda: create_async_engine(url), client, plan_chunks([GAP], batch=50, years_per_chunk=1),
                      today=TODAY, budget=Budget(max_units=100, spent=0))
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                spent = await units_spent(conn, window)
        finally:
            await engine.dispose()
        cov, rows = await _coverage_and_rows(url)
        return spent, cov, rows

    with migrated_db(seed_sql=SERIES_SEED) as name:
        spent, cov, rows = asyncio.run(scenario(sa_url(name)))
        log = asyncio.run(_log(sa_url(name)))
    assert spent == 2.0
    assert log == [("ok", 1.0, 10, 0), ("running", 1.0, None, None)]
    assert (cov[0].first, cov[0].last, rows) == (date(2019, 12, 22), date(2019, 12, 31), 10)


@requires_pg
def test_a_january_run_under_a_frozen_clock_counts_in_its_window_and_a_rerun_buys_nothing():
    # D1: the DB's real clock is months before this window; spend follows the injected clock.
    frozen = datetime(2027, 1, 15, 12, tzinfo=timezone.utc)
    today = frozen.date()
    window = ProWindow(date(2027, 1, 10))

    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(_handler))
        engine = create_async_engine(url)
        spent = []
        try:
            for _ in range(2):
                async with engine.connect() as conn:
                    before = await units_spent(conn, window)
                    gaps = series_gaps(await load_coverage(conn), today=today, incident_start=INCIDENT_START_DEFAULT)
                await run(lambda: create_async_engine(url), client, plan_chunks(gaps, batch=50, years_per_chunk=5),
                          today=today, budget=Budget(max_units=1000, spent=before), clock=lambda: frozen)
                async with engine.connect() as conn:
                    spent.append((before, await units_spent(conn, window)))
        finally:
            await engine.dispose()
        return spent

    with migrated_db(seed_sql=SERIES_SEED) as name:
        (first_before, first_after), (second_before, second_after) = asyncio.run(scenario(sa_url(name)))
    # 2024-01-01..2027-01-10 in 5-year chunks: 2024-2027-01-10 is one chunk of 1106 days = 79 x 14 -> 79 units.
    assert (first_before, first_after) == (0.0, 79.0)
    assert second_before == second_after == 79.0


@requires_pg
def test_missing_values_are_stored_as_null_never_zero():
    handler = _handler_with(lambda i: None if i == 0 else 1.0)

    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(handler))
        await run(lambda: create_async_engine(url), client, plan_chunks([GAP], batch=50, years_per_chunk=1),
                  today=TODAY, budget=Budget(max_units=100, spent=0))
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return (await conn.execute(text(
                    "SELECT date, tmax, precip_mm, freeze_thaw FROM cell_daily_conditions "
                    "WHERE tmax IS NULL ORDER BY date"))).all()
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SERIES_SEED) as name:
        nulls = asyncio.run(scenario(sa_url(name)))
    assert [tuple(r) for r in nulls] == [(date(2019, 12, 22), None, None, None), (date(2020, 1, 1), None, None, None)]


@requires_pg
def test_a_chunk_over_the_quarantine_share_is_rejected_whole_and_blocks_its_group():
    handler = _handler_with(lambda i: 999.0 if i < 3 else 1.0)

    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(handler))
        result = await run(lambda: create_async_engine(url), client, plan_chunks([GAP], batch=50, years_per_chunk=1),
                           today=TODAY, budget=Budget(max_units=100, spent=0))
        cov, rows = await _coverage_and_rows(url)
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                quarantined = (await conn.execute(text(
                    "SELECT source, reason, count(*) FROM internal.ingest_quarantine GROUP BY 1, 2"))).all()
        finally:
            await engine.dispose()
        return result, cov, rows, [tuple(r) for r in quarantined]

    with migrated_db(seed_sql=SERIES_SEED) as name:
        result, cov, rows, quarantined = asyncio.run(scenario(sa_url(name)))
        log = asyncio.run(_log(sa_url(name)))
    assert (result["rejected"], result["blocked_after_failure"], result["done"]) == (1, 1, 0)
    assert (cov[0].first, cov[0].last, rows) == (None, None, 0)
    assert quarantined == [("era5_fill", "out_of_range", 3)]
    assert log == [("rejected", 1.0, 0, 3)]


@requires_pg
def test_a_response_missing_days_fails_rather_than_claiming_them():
    handler = _handler_with(lambda i: 1.0, drop_last_day=True)

    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(handler))
        result = await run(lambda: create_async_engine(url), client, plan_chunks([GAP], batch=50, years_per_chunk=1),
                           today=TODAY, budget=Budget(max_units=100, spent=0))
        return result, await _coverage_and_rows(url)

    with migrated_db(seed_sql=SERIES_SEED) as name:
        result, (cov, rows) = asyncio.run(scenario(sa_url(name)))
    assert (result["failed"], result["blocked_after_failure"]) == (1, 1)
    assert (cov[0].first, cov[0].last, rows) == (None, None, 0)


_ACCIDENT_COLS = ("accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, "
                  "date_precision, geocode_precision, tz, year_source, year_lo, year_hi")


def _accident(i: int, day: str, lat: float, lon: float, year_range: str = "NULL, NULL, NULL") -> str:
    return (f"INSERT INTO accidents ({_ACCIDENT_COLS}) VALUES ({i}, 'AAC', '{day}', {lat}, {lon}, true, 'climbing', "
            f"'US', 'day', 'crag', '{TZ}', {year_range});")


@requires_pg
def test_incident_flag_follows_the_same_rules_as_registration():
    keys = {name: grid_bucket(lat, -105.25) for name, lat in
            [("incident", 40.05), ("pre1990", 41.05), ("range", 42.05), ("orphan", 43.05)]}
    seed = (
        _accident(1, "2020-06-01", 40.05, -105.25)
        + _accident(2, "1985-06-01", 41.05, -105.25)
        + _accident(3, "2005-01-01", 42.05, -105.25, "'aac_source_id_neighbours', 2004, 2006")
        + "".join(f"INSERT INTO grid_bucket_series (grid_bucket, tz) VALUES ({b}, '{TZ}');" for b in keys.values())
    )
    with migrated_db(seed_sql=seed) as name:
        cov, _ = asyncio.run(_coverage_and_rows(sa_url(name)))
    flags = {c.grid_bucket: c.incident for c in cov}
    assert flags == {keys["incident"]: True, keys["pre1990"]: False, keys["range"]: False, keys["orphan"]: False}


@requires_pg
def test_prune_keeps_incident_rows_and_refuses_an_empty_or_shrunken_incident_set():
    inc_b, other_b = grid_bucket(40.05, -105.25), grid_bucket(41.05, -105.25)
    seed = (
        _accident(1, "2020-06-01", 40.05, -105.25)
        + "".join(
            f"INSERT INTO grid_bucket_series (grid_bucket, tz, first_era5_date, last_era5_date) VALUES ({b}, '{TZ}', '2019-01-01', '2026-09-23');"
            f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, source) VALUES ({b}, '{TZ}', '2019-06-01', 'era5', 'open_meteo_archive');"
            for b in (inc_b, other_b)
        )
    )

    async def scenario(url: str):
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                kept, deleted = await prune(conn, today=TODAY)
                await log_prune(conn, status="ok", kept=10, deleted=deleted, problem=None)
            async with engine.connect() as conn:
                left = (await conn.execute(text("SELECT grid_bucket FROM cell_daily_conditions"))).scalars().all()
                spans = dict((await conn.execute(text("SELECT grid_bucket, first_era5_date FROM grid_bucket_series"))).all())
            refusals = []
            async with engine.connect() as conn:
                try:
                    await prune(conn, today=TODAY)
                except PruneRefused as exc:
                    refusals.append(str(exc))
                await conn.execute(text("UPDATE accidents SET tz = NULL"))
                try:
                    await prune(conn, today=TODAY)
                except PruneRefused as exc:
                    refusals.append(str(exc))
                await conn.rollback()
            return kept, deleted, left, spans, refusals
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        kept, deleted, left, spans, refusals = asyncio.run(scenario(sa_url(name)))
    assert (kept, deleted, left) == (1, 1, [inc_b])
    assert spans == {inc_b: date(2019, 1, 1), other_b: date(2023, 1, 1)}
    assert "dropped from 10 to 1" in refusals[0] and "no incident series" in refusals[1]


@requires_pg
def test_a_former_incident_series_is_pruned_to_three_years_and_never_back_filled():
    old_b, new_b = grid_bucket(40.05, -105.25), grid_bucket(41.05, -105.25)
    # Accident 1's point was reviewed and moved from old_b to new_b after old_b was bought from 1989.
    seed = (
        _accident(1, "2020-06-01", 41.05, -105.25)
        + f"INSERT INTO grid_bucket_series (grid_bucket, tz, first_era5_date, last_era5_date) VALUES "
          f"({old_b}, '{TZ}', '1989-12-26', '2026-09-23'), ({new_b}, '{TZ}', '2023-01-01', '2026-09-23');"
        + "".join(
            f"INSERT INTO cell_daily_conditions (grid_bucket, tz, date, record_kind, source) VALUES "
            f"({old_b}, '{TZ}', '{d}', 'era5', 'open_meteo_archive');"
            for d in ("1989-12-26", "2010-01-01", "2023-01-01")
        )
    )

    async def scenario(url: str):
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                kept, deleted = await prune(conn, today=TODAY)
            async with engine.connect() as conn:
                cov = await load_coverage(conn)
                left = (await conn.execute(text("SELECT date FROM cell_daily_conditions"))).scalars().all()
            return kept, deleted, cov, left
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=seed) as name:
        kept, deleted, cov, left = asyncio.run(scenario(sa_url(name)))
    assert (kept, deleted, left) == (1, 2, [date(2023, 1, 1)])
    by_bucket = {c.grid_bucket: c for c in cov}
    assert (by_bucket[old_b].incident, by_bucket[old_b].first) == (False, date(2023, 1, 1))
    gaps = series_gaps(cov, today=TODAY, incident_start=INCIDENT_START_DEFAULT)
    assert [(g.grid_bucket, g.start, g.direction) for g in gaps] == [(new_b, INCIDENT_START_DEFAULT, "backward")]
