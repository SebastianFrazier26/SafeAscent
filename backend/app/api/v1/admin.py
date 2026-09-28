"""
Admin endpoints for Celery/Redis queue management.
Not web-exposed by default - only mounted when settings.ENABLE_ADMIN_ROUTES is True.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/mp-routes/admin/trigger-cache-population")
async def trigger_cache_population(
    target_date: Optional[str] = Query(None, description="Specific date (YYYY-MM-DD) or leave empty for 7-day run"),
):
    """
    Manually trigger safety score cache population via Celery background task.

    Returns immediately with task ID. Check Railway logs for progress.

    **Options:**
    - No params: Compute optimized 3-day window (today + 2)
    - `target_date` is currently ignored by the optimized task (kept for backward compatibility)

    **Returns:** Task ID and status message (task runs in background).
    """
    from app.tasks.safety_computation_optimized import (
        compute_daily_safety_scores_optimized,
    )
    from app.celery_app import celery_app

    logger.info("=" * 60)
    logger.info("MANUAL CACHE POPULATION TRIGGERED VIA API")
    logger.info("=" * 60)

    try:
        # Refuse duplicate manual triggers while optimized computation is active
        inspect = celery_app.control.inspect()
        active_workers = inspect.active() or {}
        optimized_task_name = (
            "app.tasks.safety_computation_optimized.compute_daily_safety_scores_optimized"
        )
        active_optimized_task_ids = []
        for worker_tasks in active_workers.values():
            for task in worker_tasks or []:
                if task.get("name") == optimized_task_name:
                    active_optimized_task_ids.append(task.get("id"))

        if active_optimized_task_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "status": "already_running",
                    "message": "Optimized cache population already active; refusing duplicate trigger.",
                    "active_count": len(active_optimized_task_ids),
                    "active_task_ids": active_optimized_task_ids[:10],
                },
            )

        # Use optimized location-level computation (3-day window: today + 2)
        # target_date parameter is ignored - optimized task computes configured range
        logger.info("Triggering OPTIMIZED Celery task (location-level computation)...")
        task = compute_daily_safety_scores_optimized.delay()
        return {
            "status": "started",
            "message": "Optimized cache population started (3-day window). Check Railway logs for progress.",
            "task_id": task.id,
            "estimated_time": "5-15 minutes",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to trigger cache population: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger cache population: {str(e)}"
        )


@router.get("/mp-routes/admin/task-status/{task_id}")
async def get_task_status(task_id: str):
    """
    Check the status of a Celery background task.

    **Returns:**
    - `pending`: Task is waiting to be picked up by a worker
    - `started`: Task is currently running
    - `success`: Task completed successfully (includes result)
    - `failure`: Task failed (includes error message)
    - `revoked`: Task was cancelled
    """
    from app.celery_app import celery_app

    try:
        result = celery_app.AsyncResult(task_id)

        response = {
            "task_id": task_id,
            "status": result.status.lower(),
            "ready": result.ready(),
        }

        if result.ready():
            if result.successful():
                response["result"] = result.result
            else:
                response["error"] = str(result.result)

        return response

    except Exception as e:
        logger.error(f"Failed to get task status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get task status: {str(e)}"
        )


@router.get("/mp-routes/admin/queue-info")
async def get_queue_info():
    """
    Get info about Celery queue and purge stale tasks if needed.
    """
    from app.celery_app import celery_app
    import redis
    from app.config import settings

    try:
        # Connect to Redis directly to inspect queue
        r = redis.from_url(settings.celery_broker_url)

        # Get queue length
        queue_length = r.llen("celery")

        # Get active workers
        inspect = celery_app.control.inspect()
        active_workers = inspect.active()
        registered = inspect.registered()

        return {
            "queue_length": queue_length,
            "active_workers": active_workers,
            "registered_tasks": registered,
            "redis_connected": True,
        }
    except Exception as e:
        return {
            "error": str(e),
            "redis_connected": False,
        }


@router.get("/mp-routes/admin/purge-queue")
async def purge_queue():
    """
    Purge all pending tasks from the Celery queue.
    Use with caution - this removes ALL queued tasks.
    """
    from app.celery_app import celery_app

    try:
        purged = celery_app.control.purge()
        return {
            "status": "purged",
            "tasks_removed": purged,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }


@router.get("/mp-routes/admin/redis-debug")
async def redis_debug():
    """
    Debug Redis connection and see all Celery-related keys.
    Does not return REDIS_URL or any credential-bearing value.
    """
    import redis
    from app.config import settings

    try:
        r = redis.from_url(settings.celery_broker_url)

        # Get all keys
        all_keys = [k.decode() for k in r.keys("*")]
        celery_keys = [k for k in all_keys if "celery" in k.lower()]

        # Check specific queues
        queue_lengths = {}
        for key in ["celery", "celery:default", "default"]:
            try:
                queue_lengths[key] = r.llen(key)
            except Exception:
                queue_lengths[key] = "N/A"

        return {
            "redis_configured": True,
            "total_keys": len(all_keys),
            "celery_keys": celery_keys[:20],  # First 20
            "queue_lengths": queue_lengths,
            "ping": r.ping(),
        }
    except Exception as e:
        return {"error": str(e)}
