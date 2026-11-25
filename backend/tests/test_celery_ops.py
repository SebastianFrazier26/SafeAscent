import importlib.util
from pathlib import Path

import app.tasks.ops as ops
from app.celery_app import celery_app
from app.celery_signals import HeartbeatStep

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "railway_guard", REPO_ROOT / "scripts" / "check_compose_matches_railway.py"
)
assert _spec is not None and _spec.loader is not None
railway_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(railway_guard)


def test_beat_schedule_has_fifteen_minute_heartbeat():
    entry = celery_app.conf.beat_schedule["beat-heartbeat"]
    assert entry["task"] == "app.tasks.ops.beat_heartbeat"
    assert entry["schedule"] == 900.0
    assert entry["options"] == {"expires": 600}


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


def test_worker_service_does_not_embed_beat():
    command = railway_guard.iac_start_command(railway_guard.iac_services(REPO_ROOT)["celery-worker"])
    assert command[:4] == ["celery", "-A", "app.celery_app", "worker"]
    assert "--beat" not in command and "-B" not in command
    assert "-E" in command and "--concurrency=2" in command


def test_beat_service_is_single_replica():
    block = railway_guard.iac_services(REPO_ROOT)["beat"]
    assert railway_guard.iac_replicas(block) == 1
    assert railway_guard.iac_start_command(block)[:4] == ["celery", "-A", "app.celery_app", "beat"]
