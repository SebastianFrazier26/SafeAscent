"""
Cache warming tasks for pre-calculating safety scores.
Runs every 6 hours via Celery Beat to warm Redis cache with popular routes.
"""
import asyncio
from datetime import date, timedelta
from typing import List
import logging

from sqlalchemy import select

from app.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.models.mp_route import MpRoute
from app.schemas.prediction import PredictionRequest
from app.api.v1.predict import predict_route_safety
from app.api.v1.mp_routes import normalize_route_type, get_safety_color_code
from app.schemas.mp_route import MpRouteSafetyResponse
from app.utils.cache import cache_set, build_safety_score_key

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.cache_warming.warm_popular_routes_cache")
def warm_popular_routes_cache():
    """
    Pre-calculate safety scores for popular routes for the next 7 days.

    This task:
    1. Queries the top 200 MP routes with valid coordinates
    2. Calculates safety scores for each route for the next 7 days
    3. Stores results in Redis with 6-hour TTL

    Runs every 6 hours via Celery Beat scheduler.
    """
    logger.info("Starting cache warming task for popular routes...")

    try:
        # Run the async function
        result = asyncio.run(_warm_cache_async())
        logger.info(f"Cache warming completed: {result}")
        return result
    except Exception as e:
        logger.error(f"Cache warming failed: {e}", exc_info=True)
        raise


async def _warm_cache_async() -> dict:
    """
    Async implementation of cache warming.

    Returns:
        dict: Statistics about the warming process
    """
    # Get popular routes (routes with valid coordinates)
    async with AsyncSessionLocal() as db:
        # Query mp_routes with valid coordinates
        # Limit to 200 routes to avoid excessive computation
        query = (
            select(MpRoute)
            .where(
                MpRoute.latitude.isnot(None),
                MpRoute.longitude.isnot(None),
            )
            .order_by(MpRoute.mp_route_id)
            .limit(200)
        )

        result = await db.execute(query)
        routes: List[MpRoute] = result.scalars().all()

        if not routes:
            logger.warning("No routes found for cache warming")
            return {"status": "no_routes", "warmed": 0}

        logger.info(f"Warming cache for {len(routes)} popular routes...")

        # Calculate safety for next 7 days
        today = date.today()
        dates_to_warm = [today + timedelta(days=i) for i in range(7)]

        warmed_count = 0
        failed_count = 0

        # Process routes in batches to avoid overwhelming the system
        batch_size = 10
        for i in range(0, len(routes), batch_size):
            batch = routes[i:i + batch_size]

            # Process each route in the batch
            for route in batch:
                # Process each date for this route
                for target_date in dates_to_warm:
                    try:
                        # Check if already cached (skip if so)
                        cache_key = build_safety_score_key(route.mp_route_id, target_date.isoformat())

                        # Normalize route type
                        normalized_type = normalize_route_type(route.type)

                        # Create prediction request
                        prediction_request = PredictionRequest(
                            latitude=route.latitude,
                            longitude=route.longitude,
                            route_type=normalized_type,
                            planned_date=target_date,
                            elevation_meters=None,  # Auto-detect
                        )

                        # Calculate safety score
                        prediction = await predict_route_safety(prediction_request, db)

                        # Determine color code
                        color_code = get_safety_color_code(prediction.risk_score)

                        # Build response
                        safety_response = MpRouteSafetyResponse(
                            route_id=route.mp_route_id,
                            route_name=route.name,
                            target_date=target_date.isoformat(),
                            risk_score=round(prediction.risk_score, 1),
                            color_code=color_code
                        )

                        # Cache with 6-hour TTL (matches API endpoint)
                        cache_set(
                            cache_key,
                            safety_response.model_dump(),
                            ttl_seconds=21600
                        )

                        warmed_count += 1

                    except Exception as e:
                        logger.error(
                            f"Failed to warm cache for route {route.mp_route_id} "
                            f"on {target_date}: {e}"
                        )
                        failed_count += 1

            # Small delay between batches to avoid overwhelming the system
            await asyncio.sleep(0.5)

        return {
            "status": "completed",
            "routes_processed": len(routes),
            "dates_per_route": len(dates_to_warm),
            "total_warmed": warmed_count,
            "total_failed": failed_count,
        }
