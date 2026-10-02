import asyncio
import threading
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
from app.pipelines.ingest_log import start_run
from app.pipelines.pro_window import WINDOW_LOCK_KEY, Budget, ProWindow, record_spend, units_spent, window_lock
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)
TZ = "America/Denver"
# Around the DB's real clock: runs without an injected clock stamp spend at the DB's now().
WIN = ProWindow(datetime.now(timezone.utc).date() - timedelta(days=1), initial=True)


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
                           today=TODAY, budget=Budget(max_units=1, spent=0), window=WIN)
        cov_after_cap, _ = await _coverage_and_rows(url)
        remaining = Gap(B, TZ, cov_after_cap[0].last + timedelta(days=1), GAP.end, "forward")
        full = await run(lambda: create_async_engine(url), client, plan_chunks([remaining], batch=50, years_per_chunk=1),
                         today=TODAY, budget=Budget(max_units=100, spent=0), window=WIN)
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
        result = await run(lambda: create_async_engine(url), client, chunks, today=TODAY, budget=budget, window=WIN)
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

    window = WIN

    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(dies_on_second))
        with pytest.raises(_Killed):
            await run(lambda: create_async_engine(url), client, plan_chunks([GAP], batch=50, years_per_chunk=1),
                      today=TODAY, budget=Budget(max_units=100, spent=0), window=WIN)
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
                          today=today, budget=Budget(max_units=1000, spent=before), window=window,
                          clock=lambda: frozen)
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
                  today=TODAY, budget=Budget(max_units=100, spent=0), window=WIN)
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
                           today=TODAY, budget=Budget(max_units=100, spent=0), window=WIN)
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
                           today=TODAY, budget=Budget(max_units=100, spent=0), window=WIN)
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


def _status_then_ok(*statuses: int):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= len(statuses):
            return httpx.Response(statuses[calls["n"] - 1])
        return _handler(request)

    return handler


async def _one_chunk_run(url: str, handler, *, max_units: float = 100, gap: Gap = GAP, years: int = 1):
    client = ArchiveClient(None, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    chunks = plan_chunks([gap], batch=50, years_per_chunk=years)[:1]
    return await run(lambda: create_async_engine(url), client, chunks, today=TODAY,
                     budget=Budget(max_units=max_units, spent=0), window=WIN)


@requires_pg
@pytest.mark.parametrize("statuses,cost", [((503,), 2.0), ((429,), 1.0), ((503, 429, 502), 3.0)])
def test_each_billable_attempt_is_recorded_and_rate_limits_are_not(statuses, cost):
    # I3: a 5xx (or a timeout) may have been computed and billed; a 429 was refused before any work.
    with migrated_db(seed_sql=SERIES_SEED) as name:
        result = asyncio.run(_one_chunk_run(sa_url(name), _status_then_ok(*statuses)))
        log = asyncio.run(_log(sa_url(name)))
    assert result["done"] == 1
    assert log == [("ok", cost, 10, 0)]


@requires_pg
def test_a_retry_that_would_pass_the_cap_is_never_sent():
    sent = {"n": 0}

    def always_503(request: httpx.Request) -> httpx.Response:
        sent["n"] += 1
        return httpx.Response(503)

    with migrated_db(seed_sql=SERIES_SEED) as name:
        result = asyncio.run(_one_chunk_run(sa_url(name), always_503, max_units=2))
        log = asyncio.run(_log(sa_url(name)))
    assert sent["n"] == 2
    assert (result["failed"], result["stopped_for_budget"]) == (1, True)
    assert log[0][:2] == ("failed", 2.0)


@requires_pg
def test_a_second_window_run_is_refused_while_the_lock_is_held():
    async def scenario(url: str):
        holder = create_async_engine(url)
        try:
            async with holder.connect() as conn:
                await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": WINDOW_LOCK_KEY})
                await conn.commit()
                with pytest.raises(SystemExit, match="window lock"):
                    await _one_chunk_run(url, _handler)
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": WINDOW_LOCK_KEY})
                await conn.commit()
            # A caller already holding the lock (Task 7's run_window) can run the fill inside it.
            async with window_lock(holder):
                return await _one_chunk_run(url, _handler)
        finally:
            await holder.dispose()

    with migrated_db(seed_sql=SERIES_SEED) as name:
        result = asyncio.run(scenario(sa_url(name)))
        log = asyncio.run(_log(sa_url(name)))
    assert result["done"] == 1
    assert log == [("ok", 1.0, 10, 0)]


@requires_pg
def test_spend_is_re_read_after_the_lock_so_a_stale_budget_cannot_overspend():
    async def scenario(url: str):
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                run_id = await start_run(conn, source="era5_normals", window_start=None, window_end=None,
                                         content_sha256=None)
                await record_spend(conn, run_id, 99, at=None)
        finally:
            await engine.dispose()
        client = ArchiveClient(None, transport=httpx.MockTransport(_handler))
        return await run(lambda: create_async_engine(url), client, plan_chunks([GAP], batch=50, years_per_chunk=1),
                         today=TODAY, budget=Budget(max_units=100, spent=0), window=WIN)

    with migrated_db(seed_sql=SERIES_SEED) as name:
        result = asyncio.run(scenario(sa_url(name)))
    assert (result["done"], result["stopped_for_budget"]) == (1, True)


@requires_pg
def test_one_bad_location_rejects_the_chunk_even_when_the_pooled_share_is_low():
    # M4: 1 bad day of 60 at one location (1.7%) is 0.8% of the chunk's 120 days.
    other = grid_bucket(40.1, -105.3)
    seed = SERIES_SEED + f"INSERT INTO grid_bucket_series (grid_bucket, tz) VALUES ({other}, '{TZ}');"

    def one_bad(request: httpx.Request) -> httpx.Response:
        body = _handler(request).json()
        body[0]["daily"]["temperature_2m_max"][0] = 999.0
        return httpx.Response(200, json=body)

    async def scenario(url: str):
        client = ArchiveClient(None, transport=httpx.MockTransport(one_bad))
        chunks = plan_chunks([Gap(b, TZ, date(2019, 11, 2), date(2019, 12, 31), "forward") for b in (B, other)],
                             batch=50, years_per_chunk=1)
        return await run(lambda: create_async_engine(url), client, chunks, today=TODAY,
                         budget=Budget(max_units=100, spent=0), window=WIN)

    with migrated_db(seed_sql=seed) as name:
        result = asyncio.run(scenario(sa_url(name)))
        log = asyncio.run(_log(sa_url(name)))
    assert (result["rejected"], result["done"]) == (1, 0)
    assert log == [("rejected", 10.0, 0, 1)]


@requires_pg
def test_a_killed_retry_keeps_its_pre_send_record():
    # I3: the retry's units are durable before it goes out, whatever happens to the process next.
    calls = {"n": 0}

    def fail_then_die(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        raise _Killed()

    async def scenario(url: str) -> float:
        with pytest.raises(_Killed):
            await _one_chunk_run(url, fail_then_die)
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return await units_spent(conn, WIN)
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SERIES_SEED) as name:
        assert asyncio.run(scenario(sa_url(name))) == 2.0


@requires_pg
def test_two_gathered_fills_under_one_lock_cannot_both_spend():
    # N1: the second fill is a child task; it must contend for the real lock, not inherit it.
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with window_lock(engine):
                await asyncio.gather(_one_chunk_run(url, _handler, max_units=1),
                                     _one_chunk_run(url, _handler, max_units=1))
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SERIES_SEED) as name:
        with pytest.raises(SystemExit, match="window lock"):
            asyncio.run(scenario(sa_url(name)))
        log = asyncio.run(_log(sa_url(name)))
    assert sum(cost for _, cost, _, _ in log) == 0.0


@requires_pg
def test_no_attempt_is_sent_after_the_run_is_cancelled():
    # N2: cancelling (Ctrl-C) releases the lock, but the fetch thread lives on until its next attempt.
    sent = {"n": 0}
    first_sent, release = threading.Event(), threading.Event()

    def always_503(request: httpx.Request) -> httpx.Response:
        sent["n"] += 1
        first_sent.set()
        return httpx.Response(503)

    def backoff(seconds: float) -> None:
        release.wait(10)

    async def scenario(url: str) -> None:
        client = ArchiveClient(None, transport=httpx.MockTransport(always_503), sleep=backoff)
        task = asyncio.create_task(run(lambda: create_async_engine(url), client,
                                       plan_chunks([GAP], batch=50, years_per_chunk=1)[:1], today=TODAY,
                                       budget=Budget(max_units=100, spent=0), window=WIN))
        while not first_sent.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        # Leave the loop running so an unguarded hook could still record and send its retry.
        await asyncio.sleep(1.0)

    with migrated_db(seed_sql=SERIES_SEED) as name:
        asyncio.run(scenario(sa_url(name)))
    assert sent["n"] == 1


async def _log_spend(url: str, units: float) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source="era5_normals", window_start=None, window_end=None,
                                     content_sha256=None)
            await record_spend(conn, run_id, units, at=None)
    finally:
        await engine.dispose()


@requires_pg
def test_spend_logged_by_another_job_mid_run_stops_the_next_chunk():
    # A lost lock session lets a second job in; the per-chunk re-read still sees its spend.
    calls = {"n": 0}

    async def scenario(url: str):
        def other_job_spends_after_first(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                # The fetch runs in a worker thread, which has no loop of its own.
                asyncio.run(_log_spend(url, 99))
            return _handler(request)

        client = ArchiveClient(None, transport=httpx.MockTransport(other_job_spends_after_first))
        return await run(lambda: create_async_engine(url), client, plan_chunks([GAP], batch=50, years_per_chunk=1),
                         today=TODAY, budget=Budget(max_units=100, spent=0), window=WIN)

    with migrated_db(seed_sql=SERIES_SEED) as name:
        result = asyncio.run(scenario(sa_url(name)))
    assert calls["n"] == 1
    assert (result["done"], result["stopped_for_budget"]) == (1, True)


@requires_pg
def test_a_cancel_while_the_retry_is_being_recorded_never_sends_it(monkeypatch):
    # The retry's record waits on the loop; a cancel landing in that wait must still stop the send.
    from app.pipelines import pro_window

    sent = {"n": 0}
    recording = threading.Event()
    real_record = pro_window.record_spend

    async def slow_record(conn, run_id, units, *, at):
        if units > 1:
            recording.set()
            await asyncio.sleep(0.5)
        await real_record(conn, run_id, units, at=at)

    monkeypatch.setattr(pro_window, "record_spend", slow_record)

    def always_503(request: httpx.Request) -> httpx.Response:
        sent["n"] += 1
        return httpx.Response(503)

    async def scenario(url: str) -> None:
        client = ArchiveClient(None, transport=httpx.MockTransport(always_503), sleep=lambda s: None)
        task = asyncio.create_task(run(lambda: create_async_engine(url), client,
                                       plan_chunks([GAP], batch=50, years_per_chunk=1)[:1], today=TODAY,
                                       budget=Budget(max_units=100, spent=0), window=WIN))
        while not recording.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(1.0)

    with migrated_db(seed_sql=SERIES_SEED) as name:
        asyncio.run(scenario(sa_url(name)))
    assert sent["n"] == 1


@requires_pg
def test_a_cancel_during_a_rate_limit_backoff_never_sends_the_retry():
    # A 429 retry records nothing, so only the pre-attempt stop check can catch the cancel.
    sent = {"n": 0}
    first_sent, release = threading.Event(), threading.Event()

    def always_429(request: httpx.Request) -> httpx.Response:
        sent["n"] += 1
        first_sent.set()
        return httpx.Response(429)

    async def scenario(url: str) -> None:
        client = ArchiveClient(None, transport=httpx.MockTransport(always_429), sleep=lambda s: release.wait(10))
        task = asyncio.create_task(run(lambda: create_async_engine(url), client,
                                       plan_chunks([GAP], batch=50, years_per_chunk=1)[:1], today=TODAY,
                                       budget=Budget(max_units=100, spent=0), window=WIN))
        while not first_sent.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await asyncio.sleep(1.0)

    with migrated_db(seed_sql=SERIES_SEED) as name:
        asyncio.run(scenario(sa_url(name)))
    assert sent["n"] == 1
