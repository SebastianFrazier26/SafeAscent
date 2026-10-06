import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.era5_fill import INCIDENT_START_DEFAULT, SeriesCoverage
from app.pipelines.era5_window import (
    FAILURE_BREAKER,
    archive_client,
    build_parser,
    exit_status,
    plan_fill,
    plan_report,
    run_window,
)
from app.pipelines.grid import grid_bucket
from app.pipelines.ingest_log import start_run
from app.pipelines.localday import INCIDENT_START
from app.pipelines.open_meteo import DAILY_VARS, ArchiveClient
from app.pipelines.pro_window import WINDOW_LOCK_KEY, ProWindow, record_spend
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2027, 1, 15)
WINDOW = ProWindow(date(2027, 1, 10))
TZ = "America/Denver"
SEED = (
    "INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES (900000001, 'Fixture Area', 40.05, -105.25);"
    "INSERT INTO mp_routes (mp_route_id, name, location_id) VALUES (900000002, 'Fixture Route', 900000001);"
    "INSERT INTO accidents (accident_id, source, date, latitude, longitude, is_canonical, activity_class, country, "
    "date_precision, geocode_precision) VALUES (1, 'AAC', '2025-06-01', 40.05, -105.25, true, 'climbing', 'US', 'day', 'crag');"
)


def _clock(day: date) -> Callable[[], datetime]:
    # Spend is placed in its window by started_at (pro_window.record_spend), so a frozen 2027 run stamps 2027.
    return lambda: datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)


def _respond(request: httpx.Request, tmax: float = 5.0) -> httpx.Response:
    params = request.url.params
    start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
    days = [(start + timedelta(d)).isoformat() for d in range((end - start).days + 1)]
    body = []
    for lat, lon in zip(params["latitude"].split(","), params["longitude"].split(",")):
        daily: dict[str, object] = {"time": days}
        for var in DAILY_VARS:
            daily[var] = [1.0] * len(days)
        daily["temperature_2m_max"] = [tmax] * len(days)
        daily["temperature_2m_min"] = [-3.0] * len(days)
        body.append({"latitude": float(lat), "longitude": float(lon), "elevation": 1650.0,
                     "timezone": params["timezone"], "daily": daily})
    return httpx.Response(200, json=body)


def _handler(request: httpx.Request) -> httpx.Response:
    return _respond(request)


class _Recorder:
    def __init__(self, respond: Callable[[httpx.Request], httpx.Response] = _handler) -> None:
        self.requests: list[tuple[str, str, str]] = []
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        p = request.url.params
        self.requests.append((p["latitude"], p["start_date"], p["end_date"]))
        return self._respond(request)

    def client(self) -> ArchiveClient:
        return ArchiveClient(None, transport=httpx.MockTransport(self), sleep=lambda _: None)


def _kwargs(**over: object) -> dict[str, object]:
    base: dict[str, object] = dict(window=WINDOW, today=TODAY, max_units=10_000, batch=50, years_per_chunk=5,
                                   incident_start=date(2025, 1, 1), clock=_clock(TODAY))
    return base | over


def test_run_refuses_outside_the_window():
    rec = _Recorder()
    with pytest.raises(SystemExit, match="outside"):
        asyncio.run(run_window(lambda: None, rec.client(), **_kwargs(today=date(2027, 2, 9))))  # type: ignore[arg-type,return-value]
    assert rec.requests == []


def test_incident_start_defaults_to_the_owner_1990_start_not_1940():
    # DP1 = 1990; the series reach six days earlier so the first 1990 incident's 7-day window is whole.
    for command in (["plan"], ["run", "--window-start", "2027-01-10", "--max-units", "1"]):
        assert date.fromisoformat(build_parser().parse_args(command).incident_start) == INCIDENT_START_DEFAULT
    assert INCIDENT_START_DEFAULT == INCIDENT_START - timedelta(days=6)


def test_an_incident_start_before_the_owner_start_is_refused():
    with pytest.raises(SystemExit, match="incident-start"):
        asyncio.run(run_window(lambda: None, _Recorder().client(),  # type: ignore[arg-type,return-value]
                               **_kwargs(incident_start=date(1940, 1, 1))))


@pytest.mark.parametrize("environ", [{}, {"OPEN_METEO_API_KEY": ""}, {"OPEN_METEO_API_KEY": "   "}])
def test_a_missing_api_key_stops_the_real_client_before_anything_runs(environ):
    with pytest.raises(SystemExit, match="OPEN_METEO_API_KEY"):
        archive_client(environ)


def test_a_key_exported_into_the_environment_builds_the_customer_client(monkeypatch):
    monkeypatch.setenv("OPEN_METEO_API_KEY", "test-key")
    with archive_client() as client:
        assert isinstance(client, ArchiveClient)


def test_a_key_only_in_the_env_file_is_refused(monkeypatch):
    # B8, fail closed: settings has the key (as pydantic would read it from backend/.env), the
    # process environment does not, so the paid run must stop before any engine or request.
    from app.config import settings
    from app.pipelines import era5_window
    from app.services import temporal_weighting

    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)
    monkeypatch.setattr(settings, "OPEN_METEO_API_KEY", "from-env-file")
    monkeypatch.setattr(temporal_weighting, "utc_today", lambda: WINDOW.start)
    monkeypatch.setattr("app.pipelines.db.ingest_engine", lambda: pytest.fail("engine created"))
    args = build_parser().parse_args(["run", "--window-start", WINDOW.start.isoformat(), "--max-units", "1"])
    with pytest.raises(SystemExit, match="process environment") as refused:
        asyncio.run(era5_window._main(args))
    assert "from-env-file" not in str(refused.value)


@pytest.mark.parametrize("part, value", [
    ("fill", {"failed": 1, "rejected": 0}),
    ("fill", {"failed": 0, "rejected": 1}),
    ("normals", {"failed": 1, "rejected": 0}),
    ("normals", {"failed": 0, "rejected": 1}),
    ("fill", {"failed": 0, "rejected": 0, "stopped_after_failures": True}),
    ("prune", {"refused": "no incident series found"}),
])
def test_exit_status_is_1_for_any_failed_or_rejected_part(part, value):
    ok: dict[str, object] = {"fill": {"failed": 0, "rejected": 0}, "normals": {"failed": 0, "rejected": 0},
                             "prune": {"deleted_rows": 0}}
    assert exit_status(ok) == 0
    assert exit_status(ok | {part: value}) == 1


def test_trailing_90_days_are_re_fetched_with_each_forward_gap():
    coverage = [
        SeriesCoverage(1, TZ, date(2024, 1, 1), date(2026, 1, 10), incident=False),
        # Already at the lag edge: no forward gap, so nothing is re-fetched (a rerun in the window buys nothing).
        SeriesCoverage(2, TZ, date(2024, 1, 1), date(2027, 1, 10), incident=False),
        # A span shorter than 90 days is re-fetched from its first day, never before it.
        SeriesCoverage(3, TZ, date(2025, 12, 1), date(2026, 1, 10), incident=False),
    ]
    chunks = plan_fill(coverage, today=TODAY, incident_start=date(2025, 1, 1), batch=1, years_per_chunk=5,
                       window=WINDOW)
    assert [(c.buckets, c.start, c.end) for c in chunks] == [
        ((1,), date(2025, 10, 13), date(2027, 1, 10)),
        ((3,), date(2025, 12, 1), date(2027, 1, 10)),
        ((3,), date(2024, 1, 1), date(2025, 11, 30)),
    ]
    plain = plan_fill(coverage, today=TODAY, incident_start=date(2025, 1, 1), batch=1, years_per_chunk=5,
                      window=WINDOW, refresh=False)
    assert [c.start for c in plain] == [date(2026, 1, 11), date(2026, 1, 11), date(2024, 1, 1)]


async def _one(url: str, sql: str) -> object:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql))).one()
    finally:
        await engine.dispose()


@requires_pg
def test_window_end_to_end_and_second_run_buys_nothing():
    async def scenario(url: str):
        client = _Recorder().client()
        first = await run_window(lambda: create_async_engine(url), client, **_kwargs())  # type: ignore[arg-type]
        second = await run_window(lambda: create_async_engine(url), client, **_kwargs())  # type: ignore[arg-type]
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                tz = (await conn.execute(text("SELECT tz FROM accidents WHERE accident_id = 1"))).scalar_one()
                status = (await conn.execute(text("SELECT status FROM cell_normals_status"))).scalars().all()
                window_rows = (await conn.execute(text(
                    "SELECT count(*), min(record_kind), max(record_kind) FROM accident_conditions WHERE accident_id = 1"))).one()
                span = (await conn.execute(text("SELECT first_era5_date, last_era5_date FROM grid_bucket_series"))).one()
        finally:
            await engine.dispose()
        return first, second, tz, status, tuple(window_rows), tuple(span)

    with migrated_db(seed_sql=SEED) as name:
        first, second, tz, status, window_rows, span = asyncio.run(scenario(sa_url(name)))
    assert first["register"]["series_total"] == 1  # type: ignore[index]
    assert (first["fill"]["done"], first["fill"]["failed"], first["normals"]["done"]) == (1, 0, 1)  # type: ignore[index]
    assert tz == TZ and status == ["complete"]
    assert window_rows == (7, "era5", "era5")
    assert span == (date(2024, 1, 1), date(2027, 1, 10))
    assert (second["fill"]["chunks"], second["normals"]["batches"]) == (0, 0)  # type: ignore[index]
    assert second["units_spent_window"] == first["units_spent_window"] > 0
    assert exit_status(first) == exit_status(second) == 0


@requires_pg
def test_the_fill_runs_inside_the_window_lock_run_window_holds():
    # The fills are awaited in run_window's own task: gathered children would be refused the lock.
    seen: list[bool] = []

    async def other_session_can_lock(url: str) -> bool:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                got = bool((await conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                               {"k": WINDOW_LOCK_KEY})).scalar_one())
                if got:
                    await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": WINDOW_LOCK_KEY})
                return got
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        url = sa_url(name)

        def respond(request: httpx.Request) -> httpx.Response:
            # The fetch runs in a worker thread with no loop of its own.
            seen.append(asyncio.run(other_session_can_lock(url)))
            return _respond(request)

        result = asyncio.run(run_window(lambda: create_async_engine(url), _Recorder(respond).client(),
                                        **_kwargs()))  # type: ignore[arg-type]
    assert result["fill"]["done"] == 1  # type: ignore[index]
    assert seen and not any(seen)


def _routes_seed(lats: list[float]) -> str:
    rows = "".join(
        f"INSERT INTO mp_locations (mp_id, name, latitude, longitude) VALUES ({910000000 + i}, 'A{i}', {lat}, -105.25);"
        f"INSERT INTO mp_routes (mp_route_id, name, location_id) VALUES ({920000000 + i}, 'R{i}', {910000000 + i});"
        for i, lat in enumerate(lats)
    )
    return SEED + rows


@requires_pg
@pytest.mark.parametrize("respond", [
    lambda request: httpx.Response(400, json={"error": True, "reason": "bad"}),
    # Out of the physical range on every day: each chunk is rejected, not failed.
    lambda request: _respond(request, tmax=99.0),
], ids=["failed", "rejected"])
def test_consecutive_failures_stop_the_window_before_it_spends_the_budget(respond):
    rec = _Recorder(respond)
    with migrated_db(seed_sql=_routes_seed([40.15, 40.25, 40.35, 40.45])) as name:
        url = sa_url(name)
        result = asyncio.run(run_window(lambda: create_async_engine(url), rec.client(), **_kwargs(batch=1)))  # type: ignore[arg-type]
    fill = result["fill"]
    assert fill["chunks"] == 5 and fill["stopped_after_failures"] is True  # type: ignore[index]
    assert len(rec.requests) == FAILURE_BREAKER == 3
    assert result["normals"] == {"skipped": "fill stopped after consecutive failures"}
    assert exit_status(result) == 1


@requires_pg
def test_a_stale_non_incident_span_is_pruned_before_planning():
    # A route series last filled in 2020 (before the 2024 cutoff) must buy a fresh 3 years, not 2021 onward.
    stale = grid_bucket(40.15, -105.25)
    seed = _routes_seed([40.15]) + (
        "INSERT INTO grid_bucket_series (grid_bucket, tz, first_era5_date, last_era5_date) "
        f"VALUES ({stale}, '{TZ}', '2015-01-01', '2020-12-31');"
    )
    rec = _Recorder()
    with migrated_db(seed_sql=seed) as name:
        url = sa_url(name)
        result = asyncio.run(run_window(lambda: create_async_engine(url), rec.client(), **_kwargs()))  # type: ignore[arg-type]
        span = asyncio.run(_one(url, f"SELECT first_era5_date, last_era5_date FROM grid_bucket_series "
                                     f"WHERE grid_bucket = {stale}"))
    starts = {start for _, start, _ in rec.requests}
    assert "2021-01-01" not in starts and "2024-01-01" in starts
    assert result["fill"]["chunks"] == 1  # type: ignore[index]
    assert tuple(span) == (date(2024, 1, 1), date(2027, 1, 10))  # type: ignore[arg-type]


@requires_pg
def test_a_refused_prune_buys_nothing():
    # No incident series at all: the prune refuses, so the window stops before planning or buying.
    seed = SEED.split("INSERT INTO accidents")[0]
    rec = _Recorder()
    with migrated_db(seed_sql=seed) as name:
        url = sa_url(name)
        result = asyncio.run(run_window(lambda: create_async_engine(url), rec.client(), **_kwargs()))  # type: ignore[arg-type]
    assert "refused" in result["prune"]  # type: ignore[operator]
    assert rec.requests == [] and result["units_spent_window"] == 0
    assert exit_status(result) == 1


@requires_pg
def test_next_window_re_fetches_the_trailing_90_days_without_rewriting_unchanged_rows():
    first_window, second_window = ProWindow(date(2026, 1, 10)), WINDOW
    with migrated_db(seed_sql=SEED) as name:
        url = sa_url(name)
        asyncio.run(run_window(lambda: create_async_engine(url), _Recorder().client(),
                               **_kwargs(window=first_window, today=date(2026, 1, 15),
                                         clock=_clock(date(2026, 1, 15)))))  # type: ignore[arg-type]
        q = ("SELECT count(*), count(DISTINCT run_id) FROM cell_daily_conditions "
             "WHERE date BETWEEN '2025-10-13' AND '2026-01-10'")
        before = asyncio.run(_one(url, q))
        rec = _Recorder()
        result = asyncio.run(run_window(lambda: create_async_engine(url), rec.client(), **_kwargs(window=second_window)))  # type: ignore[arg-type]
        after = asyncio.run(_one(url, q))
        same_window = asyncio.run(_plan_report(url, today=date(2027, 1, 20)))
        no_window = asyncio.run(_plan_report(url, today=date(2027, 1, 20), window=None))
    assert any(r[1:] == ("2025-10-13", "2027-01-10") for r in rec.requests)
    assert result["fill"]["done"] == 1  # type: ignore[index]
    # Identical values: the IS DISTINCT FROM guard leaves the first window's rows (and run_id) alone.
    assert tuple(before) == tuple(after) == (90, 1)  # type: ignore[arg-type]
    # Five days later in the same window only the new days are planned; without a window the plan
    # counts the re-fetch as an upper bound.
    assert same_window["chunks"] == 1 and same_window["refresh_units"] == 0
    assert no_window["chunks"] == 1 and no_window["refresh_units"] > 0  # type: ignore[operator]


async def _plan_report(url: str, *, today: date, window: ProWindow | None = WINDOW) -> dict[str, object]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await plan_report(conn, today=today, incident_start=date(2025, 1, 1), batch=50,
                                     years_per_chunk=5, window=window)
    finally:
        await engine.dispose()


async def _log_old_spend(url: str, at: datetime) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            run_id = await start_run(conn, source="era5_fill", window_start=None, window_end=None, content_sha256=None)
            await record_spend(conn, run_id, 10, at=at)
    finally:
        await engine.dispose()


@requires_pg
def test_a_second_initial_window_is_refused_before_anything_is_bought():
    initial = ProWindow(date(2027, 3, 1), initial=True)
    rec = _Recorder()
    with migrated_db(seed_sql=SEED) as name:
        url = sa_url(name)
        # A month bought well before this window's reach: an earlier initial window was used.
        asyncio.run(_log_old_spend(url, datetime(2026, 11, 1, tzinfo=timezone.utc)))
        with pytest.raises(SystemExit, match="initial window was already used"):
            asyncio.run(run_window(lambda: create_async_engine(url), rec.client(),
                                   **_kwargs(window=initial, today=date(2027, 3, 2),
                                             clock=_clock(date(2027, 3, 2)))))  # type: ignore[arg-type]
    assert rec.requests == []


@requires_pg
def test_the_cap_stops_the_window_before_any_request():
    rec = _Recorder()
    with migrated_db(seed_sql=SEED) as name:
        url = sa_url(name)
        result = asyncio.run(run_window(lambda: create_async_engine(url), rec.client(), **_kwargs(max_units=1)))  # type: ignore[arg-type]
    assert result["fill"]["stopped_for_budget"] is True and rec.requests == []  # type: ignore[index]


@requires_pg
@pytest.mark.parametrize("bad", [
    lambda request: httpx.Response(400, json={"error": True, "reason": "bad"}),
    lambda request: _respond(request, tmax=99.0),
], ids=["failed", "rejected"])
def test_consecutive_normals_failures_stop_the_window_too(bad):
    # Fill requests end at the lag edge; the normals' bought ranges end on 31 December.
    rec = _Recorder(lambda request: bad(request) if request.url.params["end_date"].endswith("-12-31")
                    else _respond(request))
    with migrated_db(seed_sql=_routes_seed([40.15, 40.25, 40.35, 40.45])) as name:
        url = sa_url(name)
        result = asyncio.run(run_window(lambda: create_async_engine(url), rec.client(), **_kwargs(batch=1)))  # type: ignore[arg-type]
    assert result["fill"]["done"] == 5  # type: ignore[index]
    assert result["normals"]["stopped_after_failures"] is True  # type: ignore[index]
    assert sum(1 for _, _, end in rec.requests if end.endswith("-12-31")) == FAILURE_BREAKER
    assert exit_status(result) == 1


@requires_pg
def test_the_whole_window_waits_for_the_lock_not_just_the_fill():
    # Registration, prune and planning belong to one holder: a second job is refused before any of them.
    async def scenario(url: str) -> None:
        holder = create_async_engine(url)
        try:
            async with holder.connect() as conn:
                await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": WINDOW_LOCK_KEY})
                await conn.commit()
                await run_window(lambda: create_async_engine(url), _Recorder().client(), **_kwargs())  # type: ignore[arg-type]
        finally:
            await holder.dispose()

    with migrated_db(seed_sql=SEED) as name:
        url = sa_url(name)
        with pytest.raises(SystemExit, match="window lock"):
            asyncio.run(scenario(url))
        touched = asyncio.run(_one(url, "SELECT (SELECT count(*) FROM grid_bucket_series), "
                                        "(SELECT count(*) FROM source_ingest_log)"))
    assert tuple(touched) == (0, 0)  # type: ignore[arg-type]


def test_the_refresh_is_bought_once_per_window():
    # Filled on 2027-01-15 (lag edge 2027-01-10): a rerun on the 16th inside the same window buys one day.
    this_window = [SeriesCoverage(1, TZ, date(2024, 1, 1), date(2027, 1, 10), incident=False)]
    rerun = plan_fill(this_window, today=TODAY + timedelta(days=1), incident_start=date(2025, 1, 1), batch=1,
                      years_per_chunk=5, window=WINDOW)
    assert [(c.start, c.end) for c in rerun] == [(date(2027, 1, 11), date(2027, 1, 11))]
    # Last filled in the previous window (before this window's first lag edge, 2027-01-05): refreshed.
    earlier = [SeriesCoverage(1, TZ, date(2024, 1, 1), date(2027, 1, 4), incident=False)]
    chunks = plan_fill(earlier, today=TODAY, incident_start=date(2025, 1, 1), batch=1, years_per_chunk=5,
                       window=WINDOW)
    assert [(c.start, c.end) for c in chunks] == [(date(2026, 10, 7), date(2027, 1, 10))]
    # No window given (plan without --window-start): the refresh is counted as an upper bound.
    bound = plan_fill(this_window, today=TODAY + timedelta(days=1), incident_start=date(2025, 1, 1), batch=1,
                      years_per_chunk=5, window=None)
    assert bound[0].start == date(2026, 10, 13)


@requires_pg
def test_a_next_day_rerun_requests_one_new_day_not_ninety():
    rec = _Recorder()
    with migrated_db(seed_sql=SEED) as name:
        url = sa_url(name)
        asyncio.run(run_window(lambda: create_async_engine(url), _Recorder().client(), **_kwargs()))  # type: ignore[arg-type]
        nxt = TODAY + timedelta(days=1)
        result = asyncio.run(run_window(lambda: create_async_engine(url), rec.client(),
                                        **_kwargs(today=nxt, clock=_clock(nxt))))  # type: ignore[arg-type]
    assert [r[1:] for r in rec.requests] == [("2027-01-11", "2027-01-11")]
    assert result["fill"]["done"] == 1  # type: ignore[index]


@requires_pg
def test_the_window_closing_mid_run_stops_buying_cleanly():
    # The run starts on the window's last day; after the first request the clock passes its end.
    closed = {"after": False}

    def respond(request: httpx.Request) -> httpx.Response:
        closed["after"] = True
        return _respond(request)

    last_day = WINDOW.end
    later = _clock(last_day + timedelta(days=1))
    rec = _Recorder(respond)
    with migrated_db(seed_sql=_routes_seed([40.15, 40.25])) as name:
        url = sa_url(name)
        result = asyncio.run(run_window(
            lambda: create_async_engine(url), rec.client(),
            **_kwargs(batch=1, today=last_day,
                      clock=lambda: later() if closed["after"] else _clock(last_day)())))  # type: ignore[arg-type]
        logged = asyncio.run(_one(url, "SELECT count(*), count(*) FILTER (WHERE status = 'running') "
                                       "FROM source_ingest_log WHERE source IN ('era5_fill', 'era5_normals')"))
    assert len(rec.requests) == 1
    assert result["fill"]["stopped_window_closed"] is True and result["fill"]["done"] == 1  # type: ignore[index]
    assert result["normals"]["stopped_window_closed"] is True  # type: ignore[index]
    # Nothing was left half-written: the one paid run finished and no further run row was opened.
    assert tuple(logged) == (1, 0)  # type: ignore[arg-type]
    assert exit_status(result) == 1


@requires_pg
def test_plan_counts_unregistered_points_and_warns():
    with migrated_db(seed_sql=_routes_seed([40.15])) as name:
        url = sa_url(name)
        before = asyncio.run(_plan_report(url, today=TODAY))
        asyncio.run(run_window(lambda: create_async_engine(url), _Recorder().client(), **_kwargs()))  # type: ignore[arg-type]
        after = asyncio.run(_plan_report(url, today=TODAY))
    assert (before["unregistered_incident_buckets"], before["unregistered_route_buckets"]) == (1, 1)
    assert before["unregistered_units_estimate"] > 0 and "register" in str(before["warning"])
    assert before["total_units"] >= before["unregistered_units_estimate"]  # type: ignore[operator]
    assert (after["unregistered_incident_buckets"], after["unregistered_route_buckets"]) == (0, 0)
    assert "warning" not in after


def test_a_bad_incident_start_is_a_clean_exit(monkeypatch):
    from app.pipelines import era5_window

    args = build_parser().parse_args(["plan", "--incident-start", "1990-13-01"])
    with pytest.raises(SystemExit):
        asyncio.run(era5_window._main(args))


@pytest.mark.parametrize("command", [["plan"], ["run", "--window-start", "2027-01-10", "--max-units", "1"], ["register"]])
@pytest.mark.parametrize("start", ["1989-12-25", "1989-12-27", "1990-01-01", "2025-01-01"])
def test_the_cli_refuses_any_incident_start_but_the_owner_start(command, start):
    from app.pipelines import era5_window

    args = build_parser().parse_args([*command, "--incident-start", start])
    with pytest.raises(SystemExit, match="must be 1989-12-26"):
        asyncio.run(era5_window._main(args))


def test_a_dotenv_file_in_the_working_directory_is_never_read_for_the_key(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("OPEN_METEO_API_KEY=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="process environment") as refused:
        archive_client()
    assert "from-dotenv" not in str(refused.value)
