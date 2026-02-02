"""
Live monitoring dashboard for scraping progress
Shows real-time stats on data collection
"""

import pandas as pd
import time
import os
from datetime import datetime

def show_stats():
    """Display current scraping statistics"""

    print("\n" + "=" * 80)
    print(" " * 25 + "SAFEASCENT DATA COLLECTION")
    print("=" * 80)
    print(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Load tables
    try:
        routes = pd.read_csv('data/tables/routes.csv')
        ascents = pd.read_csv('data/tables/ascents.csv')
        climbers = pd.read_csv('data/tables/climbers.csv')

        # Calculate stats
        print("📊 CURRENT DATABASE STATUS:")
        print("-" * 80)
        print(f"Routes:     {len(routes):,} total")
        print(f"            {routes['mp_route_id'].notna().sum():,} with Mountain Project IDs")
        print(f"            {routes[routes['accident_count'] > 0].shape[0]:,} with accidents")
        print()
        print(f"Climbers:   {len(climbers):,} total")
        print(f"            {climbers['mp_user_id'].notna().sum():,} with profiles scraped")
        print()
        print(f"Ascents:    {len(ascents):,} total")
        print(f"            {ascents['date'].notna().sum():,} with dates ({ascents['date'].notna().sum()/len(ascents)*100:.1f}%)")
        print(f"            {ascents['style'].notna().sum():,} with style info")
        print(f"            {ascents['notes'].notna().sum():,} with notes")
        print()

        # Top routes by ascents
        route_counts = ascents.groupby('route_id').size().sort_values(ascending=False).head(5)
        route_names = routes.set_index('route_id')['name']

        print("🔥 TOP ROUTES BY ASCENT COUNT:")
        print("-" * 80)
        for i, (route_id, count) in enumerate(route_counts.items(), 1):
            route_name = route_names.get(route_id, "Unknown")
            print(f"{i}. {route_name[:40]:40} - {count:3} ascents")

        print()

        # Growth stats (compare to backup)
        backup_dir = 'data/tables/backup_20260125_150628'
        if os.path.exists(f'{backup_dir}/ascents.csv'):
            old_ascents = pd.read_csv(f'{backup_dir}/ascents.csv')
            old_routes = pd.read_csv(f'{backup_dir}/routes.csv')

            print("📈 GROWTH SINCE SESSION START:")
            print("-" * 80)
            print(f"Routes:   {len(routes) - len(old_routes):+,} new ({len(old_routes):,} → {len(routes):,})")
            print(f"Ascents:  {len(ascents) - len(old_ascents):+,} new ({len(old_ascents):,} → {len(ascents):,})")

            growth_pct = ((len(ascents) - len(old_ascents)) / len(old_ascents) * 100) if len(old_ascents) > 0 else 0
            print(f"Growth:   {growth_pct:.1f}% increase in ascents")

        print("\n" + "=" * 80)

        # Check if scraper is still running
        progress_file = 'data/tables/climbers_progress.txt'
        if os.path.exists(progress_file):
            with open(progress_file, 'r') as f:
                processed = len([l for l in f if l.strip()])
            print(f"Climber scraper progress: {processed}/178 climbers processed")

    except Exception as e:
        print(f"Error loading stats: {e}")

if __name__ == '__main__':
    while True:
        os.system('clear' if os.name != 'nt' else 'cls')
        show_stats()
        print("\nPress Ctrl+C to exit monitor...")
        time.sleep(10)  # Update every 10 seconds
