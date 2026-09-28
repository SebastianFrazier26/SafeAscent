"""Operational tasks. beat_heartbeat runs on the worker, so its ping proves beat
scheduled it *and* the worker consumed it within 15 minutes."""

from app.celery_app import celery_app
from app.config import settings
from app.healthchecks import ping


@celery_app.task(name="app.tasks.ops.beat_heartbeat")
def beat_heartbeat() -> bool:
    return ping(settings.HEALTHCHECKS_BEAT_URL)
