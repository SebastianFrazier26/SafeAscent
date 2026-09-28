import json
import logging
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

import fakeredis
import pytest
from celery.exceptions import SoftTimeLimitExceeded

import app.tasks.ops as ops
import app.tasks.safety_computation_optimized as nightly
from app.celery_app import celery_app
from app.celery_signals import HeartbeatStep

BACKEND = Path(__file__).resolve().parents[1]
NIGHTLY_URL = "https://hc-ping.example/nightly"


def test_beat_schedule_has_fifteen_minute_heartbeat():
    entry = celery_app.conf.beat_schedule["beat-heartbeat"]
    assert entry["task"] == "app.tasks.ops.beat_heartbeat"
    assert entry["schedule"] == 900.0
    assert entry["options"] == {"expires": 600}


def test_nightly_schedule_unchanged():
    entry = celery_app.conf.beat_schedule["compute-daily-safety-scores"]
    assert entry["task"] == nightly.OPTIMIZED_TASK_NAME


def test_ops_module_included_and_worker_hardened():
    assert "app.tasks.ops" in celery_app.conf.include
    assert celery_app.conf.worker_cancel_long_running_tasks_on_connection_loss is True
    assert HeartbeatStep in celery_app.steps["consumer"]


def test_beat_heartbeat_pings_beat_check(monkeypatch):
    calls: list[tuple[str | None, str]] = []
    monkeypatch.setattr(ops, "ping", lambda url, suffix="": calls.append((url, suffix)) or True)
    monkeypatch.setattr(ops.settings, "HEALTHCHECKS_BEAT_URL", "https://hc-ping.example/beat")
    assert ops.beat_heartbeat() is True
    assert calls == [("https://hc-ping.example/beat", "")]


@pytest.fixture
def nightly_pings(monkeypatch) -> list[tuple[str | None, str]]:
    calls: list[tuple[str | None, str]] = []
    monkeypatch.setattr(nightly, "ping", lambda url, suffix="": calls.append((url, suffix)) or True)
    monkeypatch.setattr(nightly.settings, "HEALTHCHECKS_NIGHTLY_URL", NIGHTLY_URL)
    monkeypatch.setattr(nightly, "_acquire_population_lock", lambda task_id: (True, "token"))
    monkeypatch.setattr(nightly, "_release_population_lock", lambda token: None)
    return calls


def test_nightly_success_pings_start_then_success(nightly_pings, monkeypatch):
    async def ok() -> dict[str, str]:
        return {"status": "completed"}

    monkeypatch.setattr(nightly, "_compute_all_dates_async", ok)
    assert nightly.compute_daily_safety_scores_optimized() == {"status": "completed"}
    assert nightly_pings == [(NIGHTLY_URL, "/start"), (NIGHTLY_URL, "")]


def test_nightly_failure_pings_fail_and_reraises(nightly_pings, monkeypatch):
    async def broken() -> dict[str, str]:
        raise RuntimeError("Failed dates during optimized cache computation: 2026-09-27")

    monkeypatch.setattr(nightly, "_compute_all_dates_async", broken)
    with pytest.raises(RuntimeError):
        nightly.compute_daily_safety_scores_optimized()
    assert nightly_pings == [(NIGHTLY_URL, "/start"), (NIGHTLY_URL, "/fail")]


def test_nightly_skipped_run_sends_no_pings(nightly_pings, monkeypatch):
    monkeypatch.setattr(nightly, "_acquire_population_lock", lambda task_id: (False, None))
    assert nightly.compute_daily_safety_scores_optimized()["status"] == "skipped"
    assert nightly_pings == []


def test_nightly_time_limits_are_below_lock_ttl_and_visibility_timeout():
    task = celery_app.tasks[nightly.OPTIMIZED_TASK_NAME]
    assert task.soft_time_limit == nightly.NIGHTLY_SOFT_TIME_LIMIT_SECONDS
    assert task.time_limit == nightly.NIGHTLY_TIME_LIMIT_SECONDS
    assert task.soft_time_limit < task.time_limit
    assert task.time_limit < nightly.CACHE_POPULATION_LOCK_TTL_SECONDS
    visibility_timeout = celery_app.conf.broker_transport_options["visibility_timeout"]
    assert task.time_limit < visibility_timeout


def test_nightly_soft_time_limit_exceeded_sends_fail_releases_lock_and_reraises(
    nightly_pings, monkeypatch
):
    released: list[str | None] = []
    monkeypatch.setattr(nightly, "_release_population_lock", lambda token: released.append(token))

    async def hangs() -> dict[str, str]:
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(nightly, "_compute_all_dates_async", hangs)
    with pytest.raises(SoftTimeLimitExceeded):
        nightly.compute_daily_safety_scores_optimized()
    assert nightly_pings == [(NIGHTLY_URL, "/start"), (NIGHTLY_URL, "/fail")]
    assert released == ["token"]


def test_worker_service_does_not_embed_beat():
    deploy = tomllib.loads((BACKEND / "railway-worker.toml").read_text())["deploy"]
    command = deploy["startCommand"].split()
    assert command[:4] == ["celery", "-A", "app.celery_app", "worker"]
    assert "--beat" not in command and "-B" not in command
    assert "-E" in command and "--concurrency=2" in command


def test_beat_service_is_single_replica():
    deploy = tomllib.loads((BACKEND / "railway-beat.toml").read_text())["deploy"]
    assert deploy["numReplicas"] == 1
    assert deploy["startCommand"].split()[:4] == ["celery", "-A", "app.celery_app", "beat"]


def test_nightly_still_returns_result_when_ping_internals_raise(monkeypatch):
    def broken_client(*args, **kwargs):
        raise RuntimeError("unexpected client failure")

    monkeypatch.setattr("app.healthchecks.httpx.Client", broken_client)
    monkeypatch.setattr(nightly.settings, "HEALTHCHECKS_NIGHTLY_URL", NIGHTLY_URL)
    monkeypatch.setattr(nightly, "_acquire_population_lock", lambda task_id: (True, "token"))
    monkeypatch.setattr(nightly, "_release_population_lock", lambda token: None)

    async def ok() -> dict[str, str]:
        return {"status": "completed"}

    monkeypatch.setattr(nightly, "_compute_all_dates_async", ok)
    assert nightly.compute_daily_safety_scores_optimized() == {"status": "completed"}


def test_population_lock_blocks_a_second_concurrent_nightly_run(monkeypatch):
    # The worker runs two slots, so a second nightly message can start while the
    # first is mid-run; the Redis SET NX lock must turn it into a skip.
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(nightly, "get_redis_client", lambda: client)
    monkeypatch.setattr(nightly, "_get_active_optimized_task_ids", lambda: {"first"})

    assert nightly._acquire_population_lock("first")[0] is True
    assert nightly._acquire_population_lock("second") == (False, None)


class _Inspect:
    def __init__(self, active: object) -> None:
        self._active = active

    def active(self) -> object:
        if isinstance(self._active, Exception):
            raise self._active
        return self._active


@pytest.fixture
def stale_lock(monkeypatch) -> fakeredis.FakeRedis:
    client = fakeredis.FakeRedis(decode_responses=True)
    stale = json.dumps({"task_id": "dead-owner", "token": "t", "acquired_at": int(time.time()) - 3600})
    client.set(nightly.CACHE_POPULATION_LOCK_KEY, stale, ex=nightly.CACHE_POPULATION_LOCK_TTL_SECONDS)
    monkeypatch.setattr(nightly, "get_redis_client", lambda: client)
    monkeypatch.setattr(nightly.celery_app, "AsyncResult", lambda task_id: SimpleNamespace(state="PENDING"))
    # fakeredis has no Lua; the compare-and-delete is replaced by a plain delete.
    monkeypatch.setattr(
        nightly, "_delete_lock_if_unchanged", lambda value: bool(client.delete(nightly.CACHE_POPULATION_LOCK_KEY))
    )
    return client


def _inspect_returns(monkeypatch, active: object) -> None:
    monkeypatch.setattr(nightly.celery_app.control, "inspect", lambda timeout=1: _Inspect(active))


def _nightly(task_id: str) -> dict[str, str]:
    return {"name": nightly.OPTIMIZED_TASK_NAME, "id": task_id}


@pytest.mark.parametrize(
    "active",
    [RuntimeError("broker unreachable"), None, {}],
    ids=["inspect-raises", "no-reply", "empty-reply"],
)
def test_stale_lock_is_not_recovered_when_workers_cannot_be_inspected(stale_lock, monkeypatch, caplog, active):
    _inspect_returns(monkeypatch, active)
    with caplog.at_level(logging.WARNING, logger=nightly.logger.name):
        assert nightly._acquire_population_lock("second") == (False, None)
    assert "dead-owner" in stale_lock.get(nightly.CACHE_POPULATION_LOCK_KEY)
    assert "could not confirm" in caplog.text


def test_stale_lock_is_not_recovered_while_another_nightly_is_active(stale_lock, monkeypatch):
    _inspect_returns(monkeypatch, {"worker@a": [_nightly("second"), _nightly("other-run")]})
    assert nightly._acquire_population_lock("second") == (False, None)


def test_stale_lock_is_recovered_when_inspect_shows_no_other_nightly(stale_lock, monkeypatch):
    _inspect_returns(monkeypatch, {"worker@a": [_nightly("second")], "worker@b": []})
    acquired, token = nightly._acquire_population_lock("second")
    assert acquired is True
    assert stale_lock.get(nightly.CACHE_POPULATION_LOCK_KEY) == token
