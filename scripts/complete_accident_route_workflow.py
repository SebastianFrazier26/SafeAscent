"""
Complete Workflow: Find MP IDs and Scrape Ticks for Accident Routes

This runs the full pipeline:
1. Find MP IDs for accident-prone routes
2. Scrape ticks from those routes
3. Build comprehensive ascent dataset
"""

import subprocess
import sys
import pandas as pd
from datetime import datetime

def run_command(cmd, description):
    """Run a command and return success status"""
    print("\n" + "=" * 80)
    print(f"{description}")
    print("=" * 80)
    print(f"Running: {' '.join(cmd)}\n")

    try:
        result = subprocess.run(cmd, check=True, capture_output=False, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Error: {e}")
        return False

def show_progress():
    """Show current data collection progress"""
    routes = pd.read_csv('data/tables/routes.csv')
    ascents = pd.read_csv('data/tables/ascents.csv')
    climbers = pd.read_csv('data/tables/climbers.csv')

    print("\n" + "=" * 80)
    print("CURRENT DATASET STATUS")
    print("=" * 80)
    print(f"Routes:   {len(routes):,} total")
    print(f"          {routes['mp_route_id'].notna().sum():,} with MP IDs ({routes['mp_route_id'].notna().sum()/len(routes)*100:.1f}%)")
    print(f"Ascents:  {len(ascents):,} total")
    print(f"Climbers: {len(climbers):,} total")
    print("=" * 80)

def main():
    """Run complete workflow"""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 15 + "ACCIDENT ROUTE DATA COLLECTION WORKFLOW" + " " * 24 + "║")
    print("╚" + "=" * 78 + "╝")
    print(f"\nStarted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Show initial status
    show_progress()

    # Step 1: Find MP IDs for accident routes
    print("\n" + "🔍 STEP 1: Finding Mountain Project IDs")
    success = run_command(
        [sys.executable, 'scripts/targeted_mp_id_search.py'],
        "Searching for MP IDs on accident-prone routes"
    )

    if not success:
        print("\n❌ MP ID search failed. Exiting.")
        return

    # Step 2: Scrape ticks from routes with MP IDs
    print("\n" + "📊 STEP 2: Scraping Ticks from Accident Routes")
    success = run_command(
        [sys.executable, 'scripts/scrape_mp_ascents.py'],
        "Collecting ascent data from routes with accidents"
    )

    if not success:
        print("\n❌ Ascent scraping failed.")

    # Show final results
    print("\n" + "🎉 WORKFLOW COMPLETE!")
    show_progress()

    # Generate summary report
    routes = pd.read_csv('data/tables/routes.csv')
    ascents = pd.read_csv('data/tables/ascents.csv')

    accident_routes_with_mp = routes[
        (routes['accident_count'] > 0) &
        (routes['mp_route_id'].notna())
    ]

    print("\n" + "📈 ACCIDENT ROUTE DATA SUMMARY:")
    print("-" * 80)
    print(f"Accident routes with MP coverage: {len(accident_routes_with_mp)}")
    print(f"Total ascents on accident routes: {len(ascents)}")
    print(f"Ascents with dates: {ascents['date'].notna().sum()} ({ascents['date'].notna().sum()/len(ascents)*100:.1f}%)")

    print(f"\n✨ Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == '__main__':
    main()
