"""Cache warming for the stored scores (P3 Task 23).

Warms the version-keyed entries the API reads (`safety:{version}:{route}:{month}`, see
utils.cache.build_stored_score_key) from the active model's model.route_scores, for the current
month. A promotion changes the version in the key, so the first warm after one fills a fresh
namespace and never overwrites what the old version served.
"""
import asyncio
import logging
from typing import Any

from sqlalchemy import select

from app.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.models.mp_route import MpRoute
from app.services import model_scores
from app.utils.time_utils import utc_today

logger = logging.getLogger(__name__)

WARM_ROUTE_LIMIT = 200


@celery_app.task(name="app.tasks.cache_warming.warm_popular_routes_cache")
def warm_popular_routes_cache() -> dict[str, Any]:
    result: dict[str, Any] = asyncio.run(_warm_cache_async())
    logger.info("Cache warming completed: %s", result)
    return result


async def _warm_cache_async() -> dict[str, Any]:
    month = utc_today().month
    async with AsyncSessionLocal() as db:
        version = await model_scores.active_version(db)
        if version is None:
            logger.warning("No active model version; nothing to warm")
            return {"status": "no_active_version", "warmed": 0}
        ids = (await db.execute(
            select(MpRoute.mp_route_id)
            .where(MpRoute.latitude.isnot(None), MpRoute.longitude.isnot(None))
            .order_by(MpRoute.mp_route_id).limit(WARM_ROUTE_LIMIT))).scalars().all()
        warmed = 0
        for route_id in ids:
            # get_score writes the cache entry itself on a miss and skips rows it cannot serve.
            if await model_scores.get_score(db, route_id, month) is not None:
                warmed += 1
    return {"status": "completed", "version": version, "month": month, "routes": len(ids), "warmed": warmed,
            "unavailable": len(ids) - warmed}
