"""
Celery application configuration for background tasks.
Uses Redis as both broker and result backend.
"""
from celery import Celery
from celery.schedules import crontab

from app.config import settings

# Create Celery app
celery_app = Celery(
    "safeascent",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.cache_warming",
        "app.tasks.safety_computation",
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
    "compute-daily-safety-scores": {
        "task": "app.tasks.safety_computation.compute_daily_safety_scores",
        "schedule": crontab(minute=0, hour=2),  # 2:00 AM UTC daily
        "options": {"expires": 7200},  # Task expires after 2 hours if not picked up
    },
    # Legacy: Warm cache for popular routes (backup/supplement)
    "warm-popular-routes-cache": {
        "task": "app.tasks.cache_warming.warm_popular_routes_cache",
        "schedule": crontab(minute=0, hour="*/6"),  # Every 6 hours
        "options": {"expires": 3600},  # Task expires after 1 hour if not picked up
    },
}
