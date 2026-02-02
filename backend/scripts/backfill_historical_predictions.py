"""
Backfill Historical Predictions Script

Calculates and stores historical risk scores for routes to enable historical trend analysis.

Usage:
    # Past 30 days for all routes (~12 hours)
    python scripts/backfill_historical_predictions.py --days 30

    # Past year for top 200 routes (~24 hours)
    python scripts/backfill_historical_predictions.py --days 365 --limit 200

    # Continue from where it left off (uses progress file)
    python scripts/backfill_historical_predictions.py --days 30 --resume

Output:
    - Creates/updates historical_predictions table
    - Progress saved to .backfill_progress.json
    - Logs to backfill_historical.log
"""
import asyncio
import argparse
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.db.session import AsyncSessionLocal
from app.models.route import Route
from app.schemas.prediction import PredictionRequest
from app.api.v1.predict import predict_route_safety
from app.api.v1.routes import normalize_route_type, get_safety_color_code
from app.schemas.route import RouteSafetyResponse

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('backfill_historical.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

PROGRESS_FILE = Path('.backfill_progress.json')


async def create_historical_predictions_table():
    """Create table for storing historical predictions if it doesn't exist."""
    async with AsyncSessionLocal() as db:
        # Create table
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS historical_predictions (
                id SERIAL PRIMARY KEY,
                route_id INTEGER NOT NULL REFERENCES routes(route_id),
                prediction_date DATE NOT NULL,
                risk_score FLOAT NOT NULL,
                confidence FLOAT NOT NULL,
                color_code VARCHAR(10) NOT NULL,
                calculated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(route_id, prediction_date)
            )
        """))

        # Create indexes separately
        await db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_historical_predictions_route
                ON historical_predictions(route_id)
        """))

        await db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_historical_predictions_date
                ON historical_predictions(prediction_date)
        """))

        await db.commit()
        logger.info("✅ Historical predictions table ready")


def save_progress(completed_routes: List[int], current_date: date):
    """Save progress to resume later if interrupted."""
    progress = {
        'completed_routes': completed_routes,
        'current_date': current_date.isoformat(),
    }
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f)


def load_progress() -> Optional[dict]:
    """Load progress from previous run."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return None


async def get_routes_to_process(limit: Optional[int] = None) -> List[Route]:
    """
    Get routes to process, ordered by accident count (most dangerous first).
    """
    async with AsyncSessionLocal() as db:
        query = (
            select(Route)
            .join(Route.mountain)
            .where(
                Route.latitude.isnot(None),
                Route.longitude.isnot(None),
            )
            .order_by(Route.mountain.has(accident_count=0).desc())  # Routes with accidents first
            .limit(limit) if limit else select(Route).where(
                Route.latitude.isnot(None),
                Route.longitude.isnot(None),
            )
        )

        result = await db.execute(query)
        routes = result.scalars().all()
        logger.info(f"📍 Processing {len(routes)} routes")
        return routes


async def calculate_and_store_prediction(
    route: Route,
    prediction_date: date,
    db
) -> bool:
    """Calculate prediction for a route on a specific date and store it."""
    try:
        # Normalize route type
        normalized_type = normalize_route_type(route.type)

        # Create prediction request
        prediction_request = PredictionRequest(
            latitude=route.latitude,
            longitude=route.longitude,
            route_type=normalized_type,
            planned_date=prediction_date,
            elevation_meters=None,  # Auto-detect
        )

        # Calculate prediction (uses historical weather for past dates)
        prediction = await predict_route_safety(prediction_request, db)

        # Determine color code
        color_code = get_safety_color_code(prediction.risk_score, prediction.confidence)

        # Insert or update
        stmt = text("""
            INSERT INTO historical_predictions
                (route_id, prediction_date, risk_score, confidence, color_code)
            VALUES (:route_id, :prediction_date, :risk_score, :confidence, :color_code)
            ON CONFLICT (route_id, prediction_date)
            DO UPDATE SET
                risk_score = EXCLUDED.risk_score,
                confidence = EXCLUDED.confidence,
                color_code = EXCLUDED.color_code,
                calculated_at = NOW()
        """)

        await db.execute(stmt, {
            "route_id": route.route_id,
            "prediction_date": prediction_date,
            "risk_score": round(prediction.risk_score, 1),
            "confidence": round(prediction.confidence, 1),
            "color_code": color_code
        })
        await db.commit()

        return True

    except Exception as e:
        logger.error(f"❌ Failed for route {route.route_id} on {prediction_date}: {e}")
        await db.rollback()
        return False


async def backfill_historical_data(
    days: int = 30,
    limit: Optional[int] = None,
    resume: bool = False,
    batch_size: int = 10
):
    """
    Main backfill function.

    Args:
        days: Number of days to backfill (1-365)
        limit: Limit number of routes (None = all)
        resume: Resume from previous progress
        batch_size: Number of routes to process in parallel
    """
    logger.info("=" * 80)
    logger.info(f"🚀 Starting historical predictions backfill")
    logger.info(f"   Days: {days}")
    logger.info(f"   Route limit: {limit or 'all'}")
    logger.info(f"   Resume: {resume}")
    logger.info("=" * 80)

    # Create table
    await create_historical_predictions_table()

    # Get routes
    routes = await get_routes_to_process(limit)
    total_routes = len(routes)

    # Load progress if resuming
    completed_route_ids = set()
    start_date = date.today() - timedelta(days=days - 1)

    if resume:
        progress = load_progress()
        if progress:
            completed_route_ids = set(progress['completed_routes'])
            logger.info(f"📂 Resuming: {len(completed_route_ids)} routes already completed")

    # Filter out completed routes
    routes_to_process = [r for r in routes if r.route_id not in completed_route_ids]

    logger.info(f"🎯 Processing {len(routes_to_process)} routes over {days} days")
    logger.info(f"   Total predictions: {len(routes_to_process) * days:,}")
    logger.info(f"   Estimated time: {(len(routes_to_process) * days) / 3600:.1f} hours")

    # Process routes
    completed_count = len(completed_route_ids)
    success_count = 0
    fail_count = 0

    async with AsyncSessionLocal() as db:
        for route_idx, route in enumerate(routes_to_process, 1):
            logger.info(f"\n📍 Route {completed_count + route_idx}/{total_routes}: {route.name}")

            # Process all dates for this route
            route_success = 0
            for day_offset in range(days):
                prediction_date = start_date + timedelta(days=day_offset)

                success = await calculate_and_store_prediction(route, prediction_date, db)
                if success:
                    route_success += 1
                    success_count += 1
                else:
                    fail_count += 1

                # Progress indicator every 10 days
                if (day_offset + 1) % 10 == 0:
                    logger.info(f"   ⏳ {day_offset + 1}/{days} days complete ({route_success} successful)")

            logger.info(f"   ✅ Completed: {route_success}/{days} predictions")

            # Mark route as completed
            completed_route_ids.add(route.route_id)

            # Save progress every route
            save_progress(list(completed_route_ids), date.today())

            # Progress summary every 10 routes
            if route_idx % 10 == 0:
                pct = (completed_count + route_idx) / total_routes * 100
                logger.info(f"\n🎯 Overall Progress: {completed_count + route_idx}/{total_routes} routes ({pct:.1f}%)")
                logger.info(f"   ✅ Successful: {success_count:,}")
                logger.info(f"   ❌ Failed: {fail_count:,}")

    # Final summary
    logger.info("\n" + "=" * 80)
    logger.info("🎉 BACKFILL COMPLETE")
    logger.info(f"   Routes processed: {len(routes_to_process)}")
    logger.info(f"   Total predictions: {success_count:,} successful, {fail_count:,} failed")
    logger.info(f"   Date range: {start_date} to {date.today()}")
    logger.info("=" * 80)

    # Clean up progress file
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
        logger.info("🧹 Progress file cleaned up")


def main():
    parser = argparse.ArgumentParser(description='Backfill historical predictions')
    parser.add_argument(
        '--days',
        type=int,
        default=30,
        help='Number of days to backfill (default: 30, max: 365)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Limit number of routes (default: all routes)'
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        help='Resume from previous progress'
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=10,
        help='Batch size for parallel processing (default: 10)'
    )

    args = parser.parse_args()

    # Validate days
    if args.days < 1 or args.days > 365:
        logger.error("❌ Days must be between 1 and 365")
        return

    # Run backfill
    asyncio.run(backfill_historical_data(
        days=args.days,
        limit=args.limit,
        resume=args.resume,
        batch_size=args.batch_size
    ))


if __name__ == '__main__':
    main()
