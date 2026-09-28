"""Celery worker liveness (Redis heartbeat) and expired-task accounting.

The 2026-08 outage: the worker's consumer hung while embedded beat kept publishing,
and every nightly message expired and was discarded below ERROR. The heartbeat runs on
the worker's own timer, so it stops when the consumer loop stops. The beat-scheduled
healthchecks ping (app.tasks.ops) covers hangs where the loop is alive but not consuming.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, TypedDict

import redis
from celery import Celery, bootsteps
from celery.signals import task_revoked

from app.config import settings
from app.utils.cache import get_redis_client

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "celery:worker:heartbeat"
EXPIRED_KEY_PREFIX = "celery:expired:"
EXPIRED_WINDOW_DAYS = 7
# One spare day so the oldest bucket in the window still exists when it is read.
EXPIRED_KEY_TTL_SECONDS = (EXPIRED_WINDOW_DAYS + 1) * 24 * 60 * 60


class WorkerHealth(TypedDict):
    status: Literal["ok", "down"]
    last_heartbeat_age_seconds: float | None
    expired_tasks_7d: dict[str, int]


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def heartbeat_interval_seconds(ttl_seconds: int) -> float:
    # Three writes per TTL window, so one missed write does not flip /health/worker to 503.
    return max(ttl_seconds / 3, 1.0)


def write_heartbeat(client: redis.Redis, ttl_seconds: int, now: float) -> None:
    client.set(HEARTBEAT_KEY, f"{now:.3f}", ex=ttl_seconds)


def record_expired(client: redis.Redis, task_name: str, today: date) -> None:
    key = f"{EXPIRED_KEY_PREFIX}{task_name}:{today.isoformat()}"
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, EXPIRED_KEY_TTL_SECONDS)
    pipe.execute()


def _expired_counts(client: redis.Redis, today: date) -> dict[str, int]:
    window_start = today - timedelta(days=EXPIRED_WINDOW_DAYS - 1)
    counts: dict[str, int] = {}
    for raw_key in client.scan_iter(match=f"{EXPIRED_KEY_PREFIX}*"):
        key = str(raw_key)
        task_name, _, day = key[len(EXPIRED_KEY_PREFIX):].rpartition(":")
        try:
            bucket = date.fromisoformat(day)
        except ValueError:
            continue
        if not task_name or bucket < window_start or bucket > today:
            continue
        counts[task_name] = counts.get(task_name, 0) + int(client.get(key) or 0)
    return counts


def read_worker_health(client: redis.Redis | None, now: float, today: date) -> WorkerHealth:
    down: WorkerHealth = {"status": "down", "last_heartbeat_age_seconds": None, "expired_tasks_7d": {}}
    if client is None:
        return down
    try:
        raw = client.get(HEARTBEAT_KEY)
        expired = _expired_counts(client, today)
    except redis.RedisError:
        logger.exception("worker health read failed")
        return down
    if raw is None:
        return {"status": "down", "last_heartbeat_age_seconds": None, "expired_tasks_7d": expired}
    return {
        "status": "ok",
        "last_heartbeat_age_seconds": round(now - float(raw), 1),
        "expired_tasks_7d": expired,
    }


def on_task_revoked(
    sender: Any = None,
    request: Any = None,
    terminated: bool = False,
    signum: Any = None,
    expired: bool = False,
    **_: Any,
) -> None:
    task_name = getattr(sender, "name", None) or "unknown"
    logger.error(
        "celery task revoked: name=%s id=%s expired=%s terminated=%s",
        task_name,
        getattr(request, "id", None),
        expired,
        terminated,
    )
    if not expired:
        return
    client = get_redis_client()
    if client is None:
        return
    try:
        record_expired(client, task_name, _utc_today())
    except redis.RedisError:
        logger.exception("could not count expired task %s", task_name)


class HeartbeatStep(bootsteps.StartStopStep):
    requires = {"celery.worker.components:Timer"}

    def __init__(self, parent: Any, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        self._tref: Any = None

    def start(self, parent: Any) -> None:
        self._beat()
        self._tref = parent.timer.call_repeatedly(
            heartbeat_interval_seconds(settings.WORKER_HEARTBEAT_TTL_SECONDS), self._beat
        )

    def stop(self, parent: Any) -> None:
        if self._tref is not None:
            self._tref.cancel()
            self._tref = None

    def _beat(self) -> None:
        client = get_redis_client()
        if client is None:
            logger.error("worker heartbeat skipped: Redis unavailable")
            return
        try:
            write_heartbeat(client, settings.WORKER_HEARTBEAT_TTL_SECONDS, time.time())
        except redis.RedisError:
            logger.exception("worker heartbeat write failed")


def install(app: Celery) -> None:
    app.steps["worker"].add(HeartbeatStep)
    task_revoked.connect(on_task_revoked, weak=False)
