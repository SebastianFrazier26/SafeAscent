import asyncio

import pytest

from app.pipelines.catalog_cycle import ping_suffix, require_direct_host, run_cycle
from app.pipelines.mp_catalog import RefreshResult


def _refresh(status):
    async def refresh(engine, *, dry_run):
        assert dry_run is False
        return RefreshResult(status, {"routes_scorable": 3}, [] if status == "ok" else ["scorable fell"])
    return refresh


def _steps(elevation_rounds, *, epqs_passed=1):
    calls = []
    rounds = iter(elevation_rounds)

    async def step(engine, name, *, limit):
        calls.append(name)
        if name == "elevation":
            return next(rounds)
        if name == "epqs-check":
            return {"passed": epqs_passed}
        return {name: 1}
    return step, calls


def test_cycle_stops_when_the_refresh_is_rejected():
    step, calls = _steps([])
    result = asyncio.run(run_cycle(None, refresh=_refresh("rejected"), step=step))
    assert result.status == "rejected" and calls == []


def test_elevation_runs_until_nothing_is_left_then_the_gate_then_routes():
    step, calls = _steps([{"sampled": 5000, "null": 0, "retry": 0}, {"sampled": 12, "null": 1, "retry": 0},
                          {"sampled": 0, "null": 0, "retry": 0}])
    result = asyncio.run(run_cycle(None, refresh=_refresh("ok"), step=step))
    assert result.status == "ok"
    assert calls == ["points", "elevation", "elevation", "elevation", "epqs-check", "routes"]


def test_points_still_awaiting_3dep_leave_the_cycle_incomplete_not_failed():
    step, calls = _steps([{"sampled": 0, "null": 0, "retry": 7}] * 3)
    result = asyncio.run(run_cycle(None, refresh=_refresh("ok"), step=step, max_rounds=3, epqs=False))
    assert result.status == "incomplete" and calls[-1] == "routes"
    assert result.steps["elevation_retry_left"] == 7


def test_a_failed_epqs_gate_fails_the_cycle_before_routes_are_rebuilt():
    step, calls = _steps([{"sampled": 0, "null": 0, "retry": 0}], epqs_passed=0)
    assert asyncio.run(run_cycle(None, refresh=_refresh("ok"), step=step)).status == "failed"
    assert "routes" not in calls


def test_points_awaiting_3dep_skip_the_gate_and_routes_when_the_gate_is_on():
    step, calls = _steps([{"sampled": 0, "null": 0, "retry": 7}])
    result = asyncio.run(run_cycle(None, refresh=_refresh("ok"), step=step))
    assert result.status == "incomplete" and calls == ["points", "elevation"]


def test_running_out_of_rounds_with_points_left_is_incomplete_not_ok():
    step, calls = _steps([{"sampled": 5000, "null": 0, "retry": 0}] * 2)
    result = asyncio.run(run_cycle(None, refresh=_refresh("ok"), step=step, max_rounds=2))
    assert result.status == "incomplete"
    assert calls == ["points", "elevation", "elevation"]


def test_max_rounds_below_one_is_refused_before_anything_runs():
    step, calls = _steps([])
    with pytest.raises(ValueError, match="max_rounds"):
        asyncio.run(run_cycle(None, refresh=_refresh("ok"), step=step, max_rounds=0))
    assert calls == []


def test_a_malformed_url_is_refused_without_echoing_it():
    url = "not a url :: hunter2-secret"
    with pytest.raises(SystemExit) as exited:
        require_direct_host(url)
    assert str(exited.value) == "INGEST_DATABASE_URL is not a valid URL"
    assert exited.value.__cause__ is None and exited.value.__suppress_context__


def test_the_neon_pooler_host_is_refused_and_the_direct_host_is_not():
    with pytest.raises(SystemExit, match="pooler"):
        require_direct_host("postgresql+asyncpg://ingest:x@ep-fixture-123-pooler.us-east-2.aws.neon.tech/db")
    require_direct_host("postgresql+asyncpg://ingest:x@ep-fixture-123.us-east-2.aws.neon.tech/db")


def test_only_an_ok_cycle_pings_success():
    assert ping_suffix("ok") == ""
    assert {ping_suffix(s) for s in ("rejected", "incomplete", "failed")} == {"/fail"}


def test_a_step_that_raises_stops_the_cycle_before_the_next_step():
    # routes refuses (RuntimeError) while any point is missing, so a failed points step must
    # end the cycle there; the CLI turns the exception into a /fail ping and a non-zero exit.
    calls = []

    async def step(engine, name, *, limit):
        calls.append(name)
        if name == "points":
            raise RuntimeError("points failed")
        return {"sampled": 0, "retry": 0}

    with pytest.raises(RuntimeError, match="points failed"):
        asyncio.run(run_cycle(None, refresh=_refresh("ok"), step=step))
    assert calls == ["points"]


@pytest.mark.parametrize(("status", "code", "last_ping"),
                         [("ok", 0, ""), ("incomplete", 2, "/fail"), ("rejected", 1, "/fail"), ("failed", 1, "/fail")])
def test_cli_exit_code_and_ping_follow_the_cycle_status(monkeypatch, status, code, last_ping):
    from app import healthchecks
    from app.config import settings
    from app.pipelines import catalog_cycle, db

    pings = []

    class _Engine:
        async def dispose(self):
            pass

    async def fake_cycle(engine, **kwargs):
        return catalog_cycle.CycleResult(status)

    monkeypatch.setattr(settings, "INGEST_DATABASE_URL", "postgresql+asyncpg://ingest:x@ep-fixture-123.example/db")
    monkeypatch.setattr(settings, "HEALTHCHECKS_CATALOG_URL", "https://hc.example/catalog")
    monkeypatch.setattr(healthchecks, "ping", lambda url, suffix="": pings.append(suffix) or True)
    monkeypatch.setattr(db, "ingest_engine", _Engine)
    monkeypatch.setattr(catalog_cycle, "run_cycle", fake_cycle)
    monkeypatch.setattr("sys.argv", ["catalog_cycle", "run"])
    with pytest.raises(SystemExit) as exited:
        catalog_cycle.cli()
    assert exited.value.code == code and pings == ["/start", last_ping]


def test_cli_refuses_the_pooler_host_before_connecting_and_pings_fail(monkeypatch):
    from app import healthchecks
    from app.config import settings
    from app.pipelines import catalog_cycle, db

    def no_engine():
        raise AssertionError("connected despite a pooler host")

    monkeypatch.setattr(settings, "INGEST_DATABASE_URL",
                        "postgresql+asyncpg://ingest:x@ep-fixture-123-pooler.us-east-2.aws.neon.tech/db")
    pings = []
    monkeypatch.setattr(settings, "HEALTHCHECKS_CATALOG_URL", "https://hc.example/catalog")
    monkeypatch.setattr(healthchecks, "ping", lambda url, suffix="": pings.append(suffix) or True)
    monkeypatch.setattr(db, "ingest_engine", no_engine)
    monkeypatch.setattr("sys.argv", ["catalog_cycle", "run"])
    with pytest.raises(SystemExit, match="pooler"):
        catalog_cycle.cli()
    assert pings == ["/start", "/fail"]


def test_cli_max_rounds_below_one_is_a_usage_error(monkeypatch):
    from app.pipelines import catalog_cycle

    monkeypatch.setattr("sys.argv", ["catalog_cycle", "run", "--max-rounds", "0"])
    with pytest.raises(SystemExit) as exited:
        catalog_cycle.cli()
    assert exited.value.code == 2


def test_cli_malformed_url_pings_fail_and_never_prints_the_url(monkeypatch, capsys):
    from app import healthchecks
    from app.config import settings
    from app.pipelines import catalog_cycle, db

    pings = []
    monkeypatch.setattr(settings, "INGEST_DATABASE_URL", "not a url :: hunter2-secret")
    monkeypatch.setattr(settings, "HEALTHCHECKS_CATALOG_URL", "https://hc.example/catalog")
    monkeypatch.setattr(healthchecks, "ping", lambda url, suffix="": pings.append(suffix) or True)
    monkeypatch.setattr(db, "ingest_engine", lambda: pytest.fail("connected with a malformed URL"))
    monkeypatch.setattr("sys.argv", ["catalog_cycle", "run"])
    with pytest.raises(SystemExit) as exited:
        catalog_cycle.cli()
    out = capsys.readouterr()
    assert "hunter2" not in str(exited.value) + out.out + out.err
    assert pings == ["/start", "/fail"]
