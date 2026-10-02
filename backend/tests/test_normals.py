import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.grid import grid_bucket
from app.pipelines.ingest_log import start_run
from app.pipelines.normals import (
    BucketPlan,
    bucket_status,
    calendar_doy,
    doy_climatology,
    monthly_normals,
    normals_due,
    plan_buckets,
    run,
    window,
)
from app.pipelines.open_meteo import DAILY_VARS, ArchiveClient, LocationResponse
from app.pipelines.pro_window import WINDOW_LOCK_KEY, Budget, ProWindow, record_spend, units_spent
from tests.pgtest import migrated_db, requires_pg, sa_url


def test_window_is_trailing_complete_years():
    assert window(date(2026, 9, 28)) == (2016, 2025)


def test_window_waits_for_era5_lag():
    assert window(date(2027, 1, 3)) == (2016, 2025)
    assert window(date(2027, 1, 6)) == (2017, 2026)


def _response(start: date, end: date, tmax=5.0, tmin=-3.0, temp_gaps=(), precip_gaps=(),
              tmax_of: Callable[[date], float] | None = None):
    days = [start + timedelta(d) for d in range((end - start).days + 1)]
    daily: dict[str, object] = {"time": [d.isoformat() for d in days]}
    for var in DAILY_VARS:
        daily[var] = [1.0] * len(days)
    daily["temperature_2m_max"] = [None if d in temp_gaps else (tmax_of(d) if tmax_of else tmax) for d in days]
    daily["temperature_2m_min"] = [None if d in temp_gaps else tmin for d in days]
    daily["precipitation_sum"] = [None if d in precip_gaps else 1.0 for d in days]
    return LocationResponse.model_validate({"latitude": 40.0, "longitude": -105.3, "elevation": 2400.0,
                                            "timezone": "America/Denver", "daily": daily})


DECADE = (date(2016, 1, 1), date(2025, 12, 31))


def test_monthly_values():
    normals = monthly_normals(_response(*DECADE))
    jan = normals[0]
    assert (jan.month, jan.n_years, jan.tmax_mean, jan.tmin_mean) == (1, 10, 5.0, -3.0)
    assert jan.precip_mm == pytest.approx(31.0)
    assert jan.freeze_thaw_days == pytest.approx(31.0)
    assert bucket_status(normals) == "complete"


def test_monthly_mean_is_the_mean_of_yearly_means():
    # Year k (2016 -> 0 ... 2025 -> 9) has tmax k all month: mean of 0..9 = 4.5.
    jan = monthly_normals(_response(*DECADE, tmax_of=lambda d: float(d.year - 2016)))[0]
    assert jan.tmax_mean == pytest.approx(4.5)
    # tmax 0 is not above freezing: only years 1..9 have freeze-thaw days.
    assert jan.freeze_thaw_days == pytest.approx(31.0 * 9 / 10)


def test_missing_precip_days_are_never_summed_as_zero():
    gaps = {date(y, 1, d) for y in range(2016, 2026) for d in range(1, 6)}
    jan = monthly_normals(_response(*DECADE, precip_gaps=gaps))[0]
    assert jan.precip_mm is None
    assert jan.tmax_mean == 5.0


def test_a_few_missing_days_are_scaled_not_zeroed():
    gaps = {date(y, 1, d) for y in range(2016, 2026) for d in (1, 2)}
    jan = monthly_normals(_response(*DECADE, temp_gaps=gaps, precip_gaps=gaps))[0]
    assert jan.precip_mm == pytest.approx(31.0)
    assert jan.freeze_thaw_days == pytest.approx(31.0)


def test_months_with_too_few_good_years_are_null_not_zero():
    gaps = {date(y, 2, d) for y in range(2016, 2019) for d in range(1, 10)}
    normals = monthly_normals(_response(*DECADE, temp_gaps=gaps))
    feb = normals[1]
    assert feb.n_years == 7
    assert (feb.tmax_mean, feb.tmin_mean, feb.freeze_thaw_days) == (None, None, None)
    # 2016, 2020 and 2024 are leap years: (3 x 29 + 7 x 28) / 10.
    assert feb.precip_mm == pytest.approx(28.3)
    assert bucket_status(normals) == "insufficient"


def test_days_absent_from_the_response_count_as_missing():
    # A response that skips days entirely (not even a null) must not look complete.
    full = _response(*DECADE)
    keep = [i for i, d in enumerate(full.daily.time) if not (d.month == 1 and d.day <= 5)]
    daily = {var: [getattr(full.daily, var)[i] for i in keep] for var in ("time", *DAILY_VARS)}
    sparse = LocationResponse.model_validate(full.model_dump() | {"daily": daily})
    jan = monthly_normals(sparse)[0]
    assert (jan.precip_mm, jan.tmax_mean) == (None, None)
    assert monthly_normals(sparse)[1].tmax_mean == 5.0


def test_a_response_starting_after_1_january_does_not_count_that_january():
    jan = monthly_normals(_response(date(2016, 1, 3), DECADE[1]))[0]
    assert jan.n_years == 9


@pytest.mark.parametrize("gap_days,usable", [(2, False), (1, True)])
def test_a_doy_year_needs_ninety_percent_of_its_window_days(gap_days, usable):
    # doy 100 pools doy 93-107 = 2-16 April. 13 of 15 days is under 90% (13.5), 14 is not;
    # three years dropped leave seven, under MIN_YEARS.
    gaps = {date(y, 4, d) for y in (2016, 2017, 2018) for d in range(2, 2 + gap_days)}
    row = doy_climatology(_response(*DECADE, precip_gaps=gaps))[99]
    assert (row["precip_mean"] is not None) is usable
    if usable:
        assert row["precip_mean"] == 1.0


def test_calendar_doy_keys_every_date_to_one_row_in_leap_and_common_years():
    assert calendar_doy(date(2021, 3, 1)) == calendar_doy(date(2020, 3, 1)) == 61
    assert calendar_doy(date(2020, 2, 29)) == 60
    assert calendar_doy(date(2021, 12, 31)) == calendar_doy(date(2020, 12, 31)) == 366
    assert calendar_doy(date(2021, 1, 10)) == 10


def test_doy_climatology_pools_a_two_week_window_across_years():
    rows = doy_climatology(_response(*DECADE))
    assert len(rows) == 366
    jan10 = rows[9]
    assert (jan10["doy"], jan10["n_years"], jan10["tmax_mean"], jan10["tmax_p10"], jan10["tmax_p90"]) == (10, 10, 5.0, 5.0, 5.0)
    assert jan10["freeze_thaw_freq"] == 1.0 and jan10["lightning_day_freq"] is None


def test_doy_percentiles_match_hand_computed_values():
    # doy 100 pools 15 days x 10 years = 150 values: 15 each of 0..9. Linear interpolation:
    # p10 at rank 0.1 x 149 = 14.9 -> 0 + 0.9 x (1 - 0) = 0.9; p90 at rank 134.1 -> 8.1.
    row = doy_climatology(_response(*DECADE, tmax_of=lambda d: float(d.year - 2016)))[99]
    assert row["tmax_mean"] == pytest.approx(4.5)
    assert row["tmax_p10"] == pytest.approx(0.9)
    assert row["tmax_p90"] == pytest.approx(8.1)


def test_doy_window_wraps_the_new_year_and_keeps_partial_edge_years():
    # doy 1 pools Dec 25-31 (doy 360-366) of the previous year and Jan 1-8. 2016's instance has
    # only Jan 1-8 in the data (8 of 8 expected days), 2017-2025 have 15 each:
    # (9 x 7 x 10) / (9 x 15 + 8) = 630 / 143.
    row = doy_climatology(_response(*DECADE, tmax_of=lambda d: 10.0 if d.month == 12 else 0.0))[0]
    assert row["n_years"] == 10
    assert row["tmax_mean"] == pytest.approx(630 / 143)


def test_doy_climatology_ignores_missing_precip_instead_of_counting_zero():
    gaps = {date(y, 1, d) for y in range(2016, 2026) for d in range(1, 20)}
    rows = doy_climatology(_response(*DECADE, precip_gaps=gaps))
    assert rows[9]["precip_mean"] is None
    assert rows[40]["precip_mean"] == 1.0


def test_a_doy_variable_needs_eight_usable_years():
    # Jan 3-17 is doy 10's whole window; three years without precipitation there leave seven.
    gaps = {date(y, 1, d) for y in (2016, 2017, 2018) for d in range(3, 18)}
    row = doy_climatology(_response(*DECADE, precip_gaps=gaps))[9]
    assert (row["precip_mean"], row["precip_p90"]) == (None, None)
    assert row["tmax_mean"] == 5.0 and row["n_years"] == 10


def test_plan_derives_complete_buckets_and_buys_only_the_missing_years():
    due = [(1, "A"), (2, "A"), (3, "A")]
    complete = {1: set(range(2016, 2026)), 2: set(range(2023, 2026)), 3: set(range(2016, 2025))}
    assert plan_buckets(due, complete, 2016, 2025) == [
        BucketPlan(1, "A", None), BucketPlan(2, "A", (2016, 2022)), BucketPlan(3, "A", (2025, 2025)),
    ]
    assert plan_buckets([(4, "A")], {}, 2016, 2025) == [BucketPlan(4, "A", (2016, 2025))]


TODAY = date(2026, 9, 28)
TZ = "America/Denver"
WIN = ProWindow(datetime.now(timezone.utc).date() - timedelta(days=1), initial=True)
B1, B2 = grid_bucket(40.0, -105.3), grid_bucket(40.1, -105.3)


def _stored(b: int, start: str, end: str, *, kind: str = "era5", tmax: str = "5") -> str:
    return (
        "INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, tmin, precip_mm, snowfall_cm, wind_max_ms, "
        f"gust_max_ms, freeze_thaw, record_kind, is_forecast, source) SELECT {b}, '{TZ}', d::date, {tmax}, -3, 1, 1, 1, 1, "
        f"true, '{kind}', false, 'open_meteo_archive' FROM generate_series('{start}'::date, '{end}'::date, '1 day') d "
        "ON CONFLICT (grid_bucket, tz, date) DO UPDATE SET record_kind = EXCLUDED.record_kind, tmax = EXCLUDED.tmax;"
    )


def _registered(*buckets: int) -> str:
    return "".join(
        f"INSERT INTO grid_bucket_series (grid_bucket, tz) VALUES ({b}, '{TZ}');"
        f"INSERT INTO cell_normals_status (grid_bucket, status) VALUES ({b}, 'pending');"
        for b in buckets
    )


def _handler_with(value: Callable[[date], float | None], requests: list[httpx.Request] | None = None,
                  zone: str | None = None, drop_last_day: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        params = request.url.params
        start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
        days = [start + timedelta(d) for d in range((end - start).days + (0 if drop_last_day else 1))]
        body = []
        for lat, lon in zip(params["latitude"].split(","), params["longitude"].split(",")):
            daily: dict[str, object] = {"time": [d.isoformat() for d in days]}
            for var in DAILY_VARS:
                daily[var] = [1.0] * len(days)
            daily["temperature_2m_max"] = [value(d) for d in days]
            daily["temperature_2m_min"] = [-3.0] * len(days)
            body.append({"latitude": float(lat), "longitude": float(lon), "elevation": 2400.0,
                         "timezone": zone or params["timezone"], "daily": daily})
        return httpx.Response(200, json=body)

    return handler


def _no_requests(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected paid request: {request.url.params}")


async def _run(url: str, handler, *, max_units: float = 10_000, batch: int = 50, due=None, sleep=None):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            due = due if due is not None else await normals_due(conn, today=TODAY)
    finally:
        await engine.dispose()
    kwargs = {"sleep": sleep} if sleep else {}
    client = ArchiveClient(None, transport=httpx.MockTransport(handler), **kwargs)
    return await run(lambda: create_async_engine(url), client, due, today=TODAY, batch=batch,
                     budget=Budget(max_units=max_units, spent=0), pro_window=WIN)


async def _fetch(url: str, sql: str) -> list[tuple[object, ...]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return [tuple(r) for r in (await conn.execute(text(sql))).all()]
    finally:
        await engine.dispose()


async def _spent(url: str) -> float:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await units_spent(conn, WIN)
    finally:
        await engine.dispose()


LOG_SQL = ("SELECT status, cost_units, rows_upserted, rows_quarantined FROM source_ingest_log "
           "WHERE source = 'era5_normals' ORDER BY started_at")


@requires_pg
def test_normals_due_takes_pending_and_out_of_period_buckets_and_the_longest_stored_series():
    b3 = grid_bucket(40.2, -105.3)
    seed = (
        _registered(B1, B2, b3)
        # B2 straddles a zone line: its Pacific series has the longer stored history.
        + f"INSERT INTO grid_bucket_series (grid_bucket, tz, first_era5_date, last_era5_date) VALUES "
          f"({B2}, 'America/Los_Angeles', '2016-01-01', '2026-09-23');"
        + f"UPDATE grid_bucket_series SET first_era5_date = '2023-01-01', last_era5_date = '2026-09-23' WHERE grid_bucket = {B2} AND tz = '{TZ}';"
        + f"UPDATE cell_normals_status SET status = 'complete' WHERE grid_bucket IN ({B1}, {b3});"
        + f"INSERT INTO cell_climate_normals (grid_bucket, month, n_years, period_start_year, period_end_year, source, "
          f"normals_version) VALUES ({B1}, 1, 10, 2016, 2025, 'open_meteo_era5_seamless', 'n-v2'), "
          f"({b3}, 1, 10, 2015, 2024, 'open_meteo_era5_seamless', 'n-v2');"
        + _climatology(B1, "c-v1")
    )

    with migrated_db(seed_sql=seed) as name:
        due = asyncio.run(_due(sa_url(name)))
    assert due == [(B2, "America/Los_Angeles"), (b3, TZ)]


def _climatology(b: int, version: str) -> str:
    return (f"INSERT INTO cell_climatology (grid_bucket, doy, n_years, period_start_year, period_end_year, "
            f"climatology_version) VALUES ({b}, 1, 10, 2016, 2025, '{version}');")


async def _due(url: str) -> list[tuple[int, str]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await normals_due(conn, today=TODAY)
    finally:
        await engine.dispose()


@requires_pg
@pytest.mark.parametrize("version,due", [("c-v1", False), ("c-v0", True)])
def test_a_climatology_version_bump_alone_makes_a_bucket_due(version, due):
    seed = (
        _registered(B1)
        + f"UPDATE cell_normals_status SET status = 'complete' WHERE grid_bucket = {B1};"
        + f"INSERT INTO cell_climate_normals (grid_bucket, month, n_years, period_start_year, period_end_year, source, "
          f"normals_version) VALUES ({B1}, 1, 10, 2016, 2025, 'open_meteo_era5_seamless', 'n-v2');"
        + _climatology(B1, version)
    )
    with migrated_db(seed_sql=seed) as name:
        assert asyncio.run(_due(sa_url(name))) == ([(B1, TZ)] if due else [])


@requires_pg
def test_a_bucket_with_ten_stored_era5_years_is_derived_and_buys_nothing():
    seed = _registered(B1) + _stored(B1, "2016-01-01", "2025-12-31")

    with migrated_db(seed_sql=seed) as name:
        url = sa_url(name)
        result = asyncio.run(_run(url, _no_requests))
        normals = asyncio.run(_fetch(url, "SELECT month, tmax_mean, precip_mm, source, period_start_year, "
                                          "period_end_year, ref_elevation_m FROM cell_climate_normals ORDER BY month"))
        clim = asyncio.run(_fetch(url, "SELECT count(*), min(n_years) FROM cell_climatology"))
        status = asyncio.run(_fetch(url, "SELECT status, n_years FROM cell_normals_status"))
        log = asyncio.run(_fetch(url, LOG_SQL))
        spent = asyncio.run(_spent(url))
    assert (result["derived_buckets"], result["bought_buckets"], result["units_billed"]) == (1, 0, 0)
    assert len(normals) == 12
    assert normals[0][:2] == (1, 5.0) and normals[0][2] == pytest.approx(31.0)
    assert {n[3:] for n in normals} == {("derived_cell_daily_conditions", 2016, 2025, None)}
    assert clim == [(366, 10)]
    assert status == [("complete", 10)]
    assert log == [("ok", 0, 12, 0)]
    assert spent == 0.0


@requires_pg
def test_stopgap_rows_never_count_as_coverage_and_only_the_missing_year_is_bought():
    seed = _registered(B1) + _stored(B1, "2016-01-01", "2025-12-31") + _stored(B1, "2025-12-31", "2025-12-31", kind="stopgap")
    requests: list[httpx.Request] = []

    with migrated_db(seed_sql=seed) as name:
        url = sa_url(name)
        result = asyncio.run(_run(url, _handler_with(lambda d: 7.0, requests)))
        jan = asyncio.run(_fetch(url, "SELECT tmax_mean, source, ref_elevation_m FROM cell_climate_normals WHERE month = 1"))
        log = asyncio.run(_fetch(url, LOG_SQL))
        stored_after = asyncio.run(_fetch(url, "SELECT count(*) FROM cell_daily_conditions WHERE tmax = 7"))
    assert [(r.url.params["start_date"], r.url.params["end_date"]) for r in requests] == [("2025-01-01", "2025-12-31")]
    # 2016-2024 stored at 5, 2025 bought at 7: (9 x 5 + 7) / 10.
    assert jan[0][0] == pytest.approx(5.2)
    assert jan[0][1:] == ("open_meteo_era5_seamless", 2400.0)
    # 365 days -> ceil(365 / 14) = 27 units.
    assert (result["bought_buckets"], result["units_billed"]) == (1, 27)
    assert log == [("ok", 27, 12, 0)]
    # Bought normals years are not written into the conditions table.
    assert stored_after == [(0,)]


@requires_pg
def test_a_three_year_series_buys_only_the_seven_years_before_it():
    seed = _registered(B1) + _stored(B1, "2023-01-01", "2026-09-23")
    requests: list[httpx.Request] = []

    with migrated_db(seed_sql=seed) as name:
        url = sa_url(name)
        result = asyncio.run(_run(url, _handler_with(lambda d: 5.0, requests)))
        spent = asyncio.run(_spent(url))
    assert [(r.url.params["start_date"], r.url.params["end_date"]) for r in requests] == [("2016-01-01", "2022-12-31")]
    # 2016-2022: 7 x 365 + 2 leap days = 2557 days -> ceil(2557 / 14) = 183 units.
    assert result["units_billed"] == 183 and spent == 183.0


@requires_pg
def test_derived_buckets_are_finished_even_when_the_paid_batch_does_not_fit():
    seed = _registered(B1, B2) + _stored(B1, "2016-01-01", "2025-12-31")

    with migrated_db(seed_sql=seed) as name:
        url = sa_url(name)
        result = asyncio.run(_run(url, _no_requests, max_units=100))
        status = dict(asyncio.run(_fetch(url, "SELECT grid_bucket, status FROM cell_normals_status")))
        spent = asyncio.run(_spent(url))
    assert (result["derived_buckets"], result["stopped_for_budget"]) == (1, True)
    assert status == {B1: "complete", B2: "pending"}
    assert spent == 0.0


@requires_pg
def test_months_with_too_few_years_are_null_and_quarantined_with_the_run():
    # Stored rows with no tmax for Feb 1-9 in 2016-2018 (missing values, not missing rows).
    seed = (_registered(B1) + _stored(B1, "2016-01-01", "2025-12-31")
            + "".join(_stored(B1, f"{y}-02-01", f"{y}-02-09", tmax="NULL") for y in (2016, 2017, 2018)))

    with migrated_db(seed_sql=seed) as name:
        url = sa_url(name)
        asyncio.run(_run(url, _no_requests))
        feb = asyncio.run(_fetch(url, "SELECT tmax_mean, freeze_thaw_days, n_years FROM cell_climate_normals WHERE month = 2"))
        status = asyncio.run(_fetch(url, "SELECT status, n_years FROM cell_normals_status"))
        quarantined = asyncio.run(_fetch(url, "SELECT source, row_ref, reason FROM internal.ingest_quarantine"))
        log = asyncio.run(_fetch(url, LOG_SQL))
    assert feb == [(None, None, 7)]
    assert status == [("insufficient", 7)]
    assert quarantined == [("era5_normals", f"{B1}:2", "too_few_years")]
    assert log == [("ok", 0, 12, 1)]


@requires_pg
@pytest.mark.parametrize("statuses,cost", [((503,), 54.0), ((429,), 27.0)])
def test_each_billable_normals_attempt_is_recorded_and_rate_limits_are_not(statuses, cost):
    seed = _registered(B1) + _stored(B1, "2016-01-01", "2024-12-31")
    calls = {"n": 0}
    ok = _handler_with(lambda d: 5.0)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(statuses[calls["n"] - 1]) if calls["n"] <= len(statuses) else ok(request)

    with migrated_db(seed_sql=seed) as name:
        url = sa_url(name)
        result = asyncio.run(_run(url, handler, sleep=lambda s: None))
        log = asyncio.run(_fetch(url, LOG_SQL))
    assert result["bought_buckets"] == 1
    assert log == [("ok", cost, 12, 0)]


class _Killed(BaseException):
    """Stands in for SIGKILL/KeyboardInterrupt: nothing in run() may catch it."""


@requires_pg
def test_a_normals_request_killed_in_flight_is_already_counted():
    seed = _registered(B1)

    def dies(request: httpx.Request) -> httpx.Response:
        raise _Killed()

    async def scenario(url: str) -> float:
        with pytest.raises(_Killed):
            await _run(url, dies)
        return await _spent(url)

    with migrated_db(seed_sql=seed) as name:
        # 2016-2025: 3653 days -> 261 units.
        assert asyncio.run(scenario(sa_url(name))) == 261.0


@requires_pg
def test_normals_refuse_to_run_while_another_job_holds_the_window_lock():
    async def scenario(url: str):
        holder = create_async_engine(url)
        try:
            async with holder.connect() as conn:
                await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": WINDOW_LOCK_KEY})
                await conn.commit()
                with pytest.raises(SystemExit, match="window lock"):
                    await _run(url, _no_requests)
        finally:
            await holder.dispose()

    with migrated_db(seed_sql=_registered(B1)) as name:
        asyncio.run(scenario(sa_url(name)))


async def _log_spend(url: str, units: float) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source="era5_fill", window_start=None, window_end=None, content_sha256=None)
            await record_spend(conn, run_id, units, at=None)
    finally:
        await engine.dispose()


@requires_pg
def test_spend_is_re_read_before_every_paid_batch():
    requests: list[httpx.Request] = []
    ok = _handler_with(lambda d: 5.0, requests)

    async def scenario(url: str):
        def other_job_spends(request: httpx.Request) -> httpx.Response:
            response = ok(request)
            if len(requests) == 1:
                asyncio.run(_log_spend(url, 1000))
            return response

        return await _run(url, other_job_spends, max_units=1261, batch=1)

    with migrated_db(seed_sql=_registered(B1, B2)) as name:
        result = asyncio.run(scenario(sa_url(name)))
    assert len(requests) == 1
    assert (result["bought_buckets"], result["stopped_for_budget"]) == (1, True)


@requires_pg
def test_spend_already_in_the_window_counts_against_the_cap():
    async def scenario(url: str):
        await _log_spend(url, 9_800)
        return await _run(url, _no_requests, max_units=10_000)

    with migrated_db(seed_sql=_registered(B1)) as name:
        result = asyncio.run(scenario(sa_url(name)))
    assert (result["bought_buckets"], result["stopped_for_budget"]) == (0, True)


@requires_pg
@pytest.mark.parametrize("handler,reason", [
    (_handler_with(lambda d: 5.0, zone="America/Chicago"), "timezone"),
    (_handler_with(lambda d: 99.0), "quarantined share"),
    (_handler_with(lambda d: 5.0, drop_last_day=True), "every day"),
])
def test_a_bad_response_writes_nothing_and_leaves_the_bucket_pending(handler, reason):
    with migrated_db(seed_sql=_registered(B1)) as name:
        url = sa_url(name)
        result = asyncio.run(_run(url, handler))
        normals = asyncio.run(_fetch(url, "SELECT count(*) FROM cell_climate_normals"))
        status = asyncio.run(_fetch(url, "SELECT status FROM cell_normals_status"))
        log = asyncio.run(_fetch(url, "SELECT status, cost_units, validation_report->>'problems' FROM source_ingest_log "
                                      "WHERE source = 'era5_normals'"))
    assert result["done"] == 0 and result["failed"] + result["rejected"] == 1
    assert normals == [(0,)] and status == [("pending",)]
    assert log[0][:2] in {("failed", 261), ("rejected", 261)} and reason in str(log[0][2])


@requires_pg
def test_a_quarantined_bought_day_is_missing_and_scaled_never_zero():
    # 1 bad day of 365 (0.3%) passes the 1% gate; that day is dropped whole, precipitation included.
    seed = _registered(B1) + _stored(B1, "2016-01-01", "2024-12-31")
    bad = date(2025, 1, 5)

    with migrated_db(seed_sql=seed) as name:
        url = sa_url(name)
        result = asyncio.run(_run(url, _handler_with(lambda d: 99.0 if d == bad else 5.0)))
        jan = asyncio.run(_fetch(url, "SELECT precip_mm, tmax_mean FROM cell_climate_normals WHERE month = 1"))
        quarantined = asyncio.run(_fetch(url, "SELECT row_ref, reason FROM internal.ingest_quarantine"))
    assert result["bought_buckets"] == 1
    # 2025's January: 30 observed days of 1 mm scaled to 31, so every year is 31; zero-filled it would be 30.9.
    assert jan[0][0] == pytest.approx(31.0) and jan[0][1] == pytest.approx(5.0)
    assert quarantined == [(f"{B1}:{TZ}:2025-01-05", "out_of_range")]
