"""
Admin endpoints for Celery/Redis queue management.
Not web-exposed by default - only mounted when settings.ENABLE_ADMIN_ROUTES is True.
"""
from fastapi import APIRouter, HTTPException
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


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
