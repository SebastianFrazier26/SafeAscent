"""
Quick test to check heatmap data availability
"""
import asyncio
from datetime import date
from sqlalchemy import select, func
from app.db.session import AsyncSessionLocal
from app.models.route import Route

async def test_heatmap():
    async with AsyncSessionLocal() as db:
        # Get all routes
        query = select(Route).where(
            Route.latitude.isnot(None),
            Route.longitude.isnot(None)
        ).limit(10)

        result = await db.execute(query)
        routes = result.scalars().all()

        print(f"Sample of first 10 routes:")
        print(f"{'Name':<30} {'Type':<15} {'Lat':<10} {'Lon':<10}")
        print("-" * 70)
        for r in routes:
            print(f"{r.name[:29]:<30} {str(r.type)[:14]:<15} {r.latitude:<10.4f} {r.longitude:<10.4f}")

        print(f"\n{'='*70}")
        print(f"IMPORTANT: Routes DO NOT have risk_score in database.")
        print(f"Risk scores are calculated on-demand via API and cached in Redis.")
        print(f"The frontend must fetch them via POST /routes/{{id}}/safety endpoint.")
        print(f"{'='*70}")

asyncio.run(test_heatmap())
