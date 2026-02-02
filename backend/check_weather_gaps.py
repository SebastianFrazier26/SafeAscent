#!/usr/bin/env python3
"""
Check weather data coverage and identify gaps.

Analyzes which accidents have incomplete weather data and why.
"""
import asyncio
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
from app.db.session import AsyncSessionLocal
from app.models.accident import Accident
from app.models.weather import Weather
from datetime import timedelta


async def analyze_weather_coverage():
    """Analyze weather data coverage for all accidents."""
    async with AsyncSessionLocal() as session:
        # Get all accidents with dates
        stmt = select(Accident).where(
            and_(
                Accident.date.isnot(None),
                Accident.latitude.isnot(None),
                Accident.longitude.isnot(None),
            )
        )
        result = await session.execute(stmt)
        accidents = result.scalars().all()

        print(f"Total accidents with dates and coordinates: {len(accidents)}\n")

        # Analyze weather coverage
        coverage_stats = {
            "0_days": [],
            "1-2_days": [],
            "3-4_days": [],
            "5-6_days": [],
            "7_days": [],
        }

        for accident in accidents:
            # Calculate expected date range (7 days before accident)
            end_date = accident.date
            start_date = end_date - timedelta(days=6)

            # Query weather data for this accident
            weather_stmt = select(Weather).where(
                and_(
                    Weather.accident_id == accident.accident_id,
                    Weather.date >= start_date,
                    Weather.date <= end_date,
                )
            )
            weather_result = await session.execute(weather_stmt)
            weather_records = weather_result.scalars().all()

            num_days = len(weather_records)

            # Categorize
            if num_days == 0:
                coverage_stats["0_days"].append(accident)
            elif num_days <= 2:
                coverage_stats["1-2_days"].append(accident)
            elif num_days <= 4:
                coverage_stats["3-4_days"].append(accident)
            elif num_days <= 6:
                coverage_stats["5-6_days"].append(accident)
            else:
                coverage_stats["7_days"].append(accident)

        # Print statistics
        print("=" * 70)
        print("WEATHER COVERAGE ANALYSIS")
        print("=" * 70)
        print()

        total = len(accidents)
        for category, accs in coverage_stats.items():
            count = len(accs)
            pct = (count / total) * 100
            print(f"{category:12} {count:5} accidents ({pct:5.1f}%)")

        print()
        print("=" * 70)
        print("DETAILED BREAKDOWN")
        print("=" * 70)
        print()

        # Show sample accidents with no weather data
        if coverage_stats["0_days"]:
            print(f"\n### Sample accidents with 0 weather records (showing first 10):")
            for acc in coverage_stats["0_days"][:10]:
                print(f"  ID {acc.accident_id}: {acc.date} at ({acc.latitude:.4f}, {acc.longitude:.4f})")
                print(f"    Location: {acc.location or 'Unknown'}")
                print(f"    Activity: {acc.activity}")
                print()

        # Show sample accidents with partial data
        if coverage_stats["3-4_days"]:
            print(f"\n### Sample accidents with 3-4 weather records (showing first 5):")
            for acc in coverage_stats["3-4_days"][:5]:
                # Get weather dates
                weather_stmt = select(Weather).where(
                    Weather.accident_id == acc.accident_id
                ).order_by(Weather.date)
                weather_result = await session.execute(weather_stmt)
                weather_records = weather_result.scalars().all()

                dates_str = ", ".join([str(w.date) for w in weather_records])
                print(f"  ID {acc.accident_id}: {acc.date}")
                print(f"    Weather dates: {dates_str}")
                print()

        # Summary
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        full_week = len(coverage_stats["7_days"])
        usable = len(coverage_stats["5-6_days"]) + full_week
        partial = len(coverage_stats["3-4_days"])
        insufficient = len(coverage_stats["1-2_days"])
        none = len(coverage_stats["0_days"])

        print(f"\n✅ Full week (7 days):          {full_week:4} ({full_week/total*100:.1f}%)")
        print(f"🟨 Usable (5-6 days):           {len(coverage_stats['5-6_days']):4} ({len(coverage_stats['5-6_days'])/total*100:.1f}%)")
        print(f"🟧 Partial (3-4 days):          {partial:4} ({partial/total*100:.1f}%)")
        print(f"🟥 Insufficient (1-2 days):     {insufficient:4} ({insufficient/total*100:.1f}%)")
        print(f"❌ No data (0 days):            {none:4} ({none/total*100:.1f}%)")
        print()
        print(f"📊 Currently usable (≥5 days):  {usable:4} ({usable/total*100:.1f}%)")
        print(f"📊 Could be usable (≥3 days):   {usable + partial:4} ({(usable + partial)/total*100:.1f}%)")
        print(f"📊 Need backfill or exclude:    {insufficient + none:4} ({(insufficient + none)/total*100:.1f}%)")


if __name__ == "__main__":
    asyncio.run(analyze_weather_coverage())
