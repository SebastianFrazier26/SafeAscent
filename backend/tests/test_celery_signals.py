import logging
from datetime import date
from types import SimpleNamespace

import fakeredis
import pytest
from celery import Celery
from kombu.asynchronous.timer import Timer

import app.celery_signals as signals
from app.celery_signals import (
    EXPIRED_KEY_PREFIX,
    HEARTBEAT_KEY,
    HeartbeatStep,
    install,
    heartbeat_interval_seconds,
    read_worker_health,
    record_expired,
    write_heartbeat,
)

TODAY = date(2026, 9, 27)


@pytest.fixture
def fake() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


def test_interval_is_a_third_of_ttl_with_floor():
    assert heartbeat_interval_seconds(120) == 40.0
    assert heartbeat_interval_seconds(1) == 1.0


def test_write_heartbeat_sets_value_and_ttl(fake):
    write_heartbeat(fake, ttl_seconds=120, now=1000.5)
    assert fake.get(HEARTBEAT_KEY) == "1000.500"
    assert 0 < fake.ttl(HEARTBEAT_KEY) <= 120


def test_health_ok_with_fresh_heartbeat(fake):
    write_heartbeat(fake, ttl_seconds=120, now=1000.0)
    health = read_worker_health(fake, now=1012.34, today=TODAY)
    assert health == {"status": "ok", "last_heartbeat_age_seconds": 12.3, "expired_tasks_7d": {}}


def test_health_down_without_heartbeat(fake):
    assert read_worker_health(fake, now=1.0, today=TODAY)["status"] == "down"


def test_health_down_without_redis():
    assert read_worker_health(None, now=1.0, today=TODAY) == {
        "status": "down",
        "last_heartbeat_age_seconds": None,
        "expired_tasks_7d": {},
    }


def test_expired_counts_cover_last_seven_days_only(fake):
    task = "app.tasks.safety_computation_optimized.compute_daily_safety_scores_optimized"
    record_expired(fake, task, TODAY)
    record_expired(fake, task, TODAY)
    record_expired(fake, task, date(2026, 9, 21))
    record_expired(fake, task, date(2026, 9, 20))
    fake.set(f"{EXPIRED_KEY_PREFIX}garbage", "5")
    assert read_worker_health(fake, now=0.0, today=TODAY)["expired_tasks_7d"] == {task: 3}
    assert fake.ttl(f"{EXPIRED_KEY_PREFIX}{task}:{TODAY.isoformat()}") > 7 * 86400


def test_revoked_expired_task_logs_error_and_counts(fake, monkeypatch, caplog):
    monkeypatch.setattr(signals, "get_redis_client", lambda: fake)
    sender = SimpleNamespace(name="app.tasks.ops.beat_heartbeat")
    with caplog.at_level(logging.ERROR, logger="app.celery_signals"):
        signals.on_task_revoked(sender=sender, request=SimpleNamespace(id="abc"), expired=True)
    assert "app.tasks.ops.beat_heartbeat" in caplog.text and "abc" in caplog.text
    assert read_worker_health(fake, now=0.0, today=signals._utc_today())["expired_tasks_7d"] == {
        "app.tasks.ops.beat_heartbeat": 1
    }


def test_revoked_non_expired_task_logs_but_does_not_count(fake, monkeypatch, caplog):
    monkeypatch.setattr(signals, "get_redis_client", lambda: fake)
    with caplog.at_level(logging.ERROR, logger="app.celery_signals"):
        signals.on_task_revoked(sender=SimpleNamespace(name="t"), request=None, expired=False)
    assert "expired=False" in caplog.text
    assert list(fake.scan_iter(match=f"{EXPIRED_KEY_PREFIX}*")) == []


class _FakeTref:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _FakeTimer:
    def __init__(self) -> None:
        self.calls: list[tuple[float, object]] = []
        self.tref = _FakeTref()

    def call_repeatedly(self, secs: float, fun: object, *args: object, **kwargs: object) -> _FakeTref:
        self.calls.append((secs, fun))
        return self.tref


def test_heartbeat_step_writes_immediately_schedules_and_cancels(fake, monkeypatch):
    monkeypatch.setattr(signals, "get_redis_client", lambda: fake)
    parent = SimpleNamespace(timer=_FakeTimer())
    step = HeartbeatStep(parent)
    step.start(parent)
    assert fake.get(HEARTBEAT_KEY) is not None
    assert parent.timer.calls[0][0] == heartbeat_interval_seconds(signals.settings.WORKER_HEARTBEAT_TTL_SECONDS)
    step.stop(parent)
    assert parent.timer.tref.cancelled


def test_heartbeat_step_survives_missing_redis(monkeypatch, caplog):
    monkeypatch.setattr(signals, "get_redis_client", lambda: None)
    parent = SimpleNamespace(timer=_FakeTimer())
    with caplog.at_level(logging.ERROR, logger="app.celery_signals"):
        HeartbeatStep(parent).start(parent)
    assert "heartbeat" in caplog.text


@pytest.mark.parametrize(
    ("key", "value"),
    [(HEARTBEAT_KEY, "not-a-float"), (f"{EXPIRED_KEY_PREFIX}t:2026-09-27", "not-an-int")],
)
def test_health_down_on_garbage_values(fake, key, value):
    fake.set(key, value)
    assert read_worker_health(fake, now=1.0, today=TODAY) == {
        "status": "down",
        "last_heartbeat_age_seconds": None,
        "expired_tasks_7d": {},
    }


def _live_heartbeat_entries(timer: Timer) -> list[object]:
    return [s.entry for s in timer.queue if not s.entry.canceled]


def test_heartbeat_rescheduled_exactly_once_after_consumer_restart(fake, monkeypatch):
    # Consumer.on_close() clears the shared timer, then blueprint.restart() stops and
    # restarts every consumer step (celery/worker/consumer/consumer.py).
    monkeypatch.setattr(signals, "get_redis_client", lambda: fake)
    parent = SimpleNamespace(timer=Timer())
    step = HeartbeatStep(parent)
    step.start(parent)
    assert len(_live_heartbeat_entries(parent.timer)) == 1

    parent.timer.clear()
    fake.delete(HEARTBEAT_KEY)
    step.stop(parent)
    step.start(parent)
    assert len(_live_heartbeat_entries(parent.timer)) == 1
    assert fake.get(HEARTBEAT_KEY) is not None

    step.start(parent)
    assert len(_live_heartbeat_entries(parent.timer)) == 1

    step.shutdown(parent)
    assert _live_heartbeat_entries(parent.timer) == []


def test_install_registers_step_in_consumer_blueprint():
    celery_app = Celery("t")
    install(celery_app)
    assert HeartbeatStep in celery_app.steps["consumer"]
    assert HeartbeatStep not in celery_app.steps["worker"]
