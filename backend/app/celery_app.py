"""
Celery application configuration for background tasks.
Supports dedicated Redis endpoints for broker/result backend.
"""
import logging

from celery import Celery
from celery.schedules import crontab

from app.config import settings

# Silence verbose loggers BEFORE any other imports
# This ensures SQLAlchemy doesn't spam logs with query parameters
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.dialects").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Create Celery app
celery_app = Celery(
    "safeascent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.scores_heartbeat",
        "app.tasks.ops",
    ],
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Task results expire after 24 hours
    result_expires=86400,
    # Keep startup broker retry behavior explicit (Celery 6 compatibility)
    broker_connection_retry_on_startup=True,
    # Prevent long-running tasks from being redelivered mid-run (Redis broker)
    broker_transport_options={"visibility_timeout": 21600},  # 6 hours
    result_backend_transport_options={"visibility_timeout": 21600},
    # Only acknowledge tasks after they complete
    task_acks_late=True,
    # Don't retry failed tasks by default
    task_reject_on_worker_lost=True,
    # A lost broker connection cancels in-flight work instead of letting it finish
    # against a dead channel. The unacked message is not redelivered at once: the Redis
    # transport restores it only after visibility_timeout (6h), so a cancelled 02:00 task
    # reruns around 08:00, or never if that passes its `expires`.
    worker_cancel_long_running_tasks_on_connection_loss=True,
)

# Celery Beat schedule - periodic tasks
celery_app.conf.beat_schedule = {
    # Stored-score health at 02:00 UTC, where the kernel nightly used to run. It computes nothing:
    # scores are written by the monthly training run. `expires` stays under the 6h visibility
    # timeout so a redelivered stale heartbeat is dropped instead of masking a missed day.
    "scores-heartbeat": {
        "task": "app.tasks.scores_heartbeat.scores_heartbeat",
        "schedule": crontab(minute=0, hour=2),
        "options": {"expires": 3600},
    },
    # Dead-man's switch for the consumer: an alert fires within 75 min (15 min period +
    # 60 min grace on healthchecks.io) instead of at the next nightly run.
    "beat-heartbeat": {
        "task": "app.tasks.ops.beat_heartbeat",
        "schedule": 900.0,
        "options": {"expires": 600},
    },
}

from app.celery_signals import install as install_ops_signals  # noqa: E402

install_ops_signals(celery_app)
