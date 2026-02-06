"""
Celery application configuration for background tasks.
Uses Redis as both broker and result backend.
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
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.safety_computation",           # Original computation task
        "app.tasks.safety_computation_optimized", # Location-level optimized task
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
    # Only acknowledge tasks after they complete
    task_acks_late=True,
    # Don't retry failed tasks by default
    task_reject_on_worker_lost=True,
)

# Celery Beat schedule - periodic tasks
celery_app.conf.beat_schedule = {
    # Nightly pre-computation of ALL safety scores (runs at 2am UTC)
    # Uses optimized location-level task (~2 hours for 3 days)
    "compute-daily-safety-scores": {
        "task": "app.tasks.safety_computation_optimized.compute_daily_safety_scores_optimized",
        "schedule": crontab(minute=0, hour=2),  # 2:00 AM UTC daily
        "options": {"expires": 14400},  # Task expires after 4 hours if not picked up
    },
}
