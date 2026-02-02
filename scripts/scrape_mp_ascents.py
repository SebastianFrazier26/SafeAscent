"""
Production Mountain Project Ascents Scraper
Scrapes tick data from all routes in our routes table
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import pandas as pd
import time
import hashlib
import re
from datetime import datetime
import os

def setup_driver():
    """Setup Chrome driver"""
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def create_route_slug(route_name):
    """Convert route name to URL slug format"""
    # Remove special characters, convert to lowercase, replace spaces with hyphens
    slug = route_name.lower()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug.strip('-')

def scrape_route_ticks(driver, route_id, route_name_slug, mp_route_id=None, existing_climbers=None, next_ascent_id=1, next_climber_id=1):
    """
    Scrape all ticks from a route stats page
    Returns list of tick dictionaries
    """
    # If we have MP route ID, use it; otherwise skip
    if not mp_route_id:
        print(f"  No Mountain Project ID for route {route_id}, skipping...")
        return []

    if existing_climbers is None:
        existing_climbers = {}

    stats_url = f"https://www.mountainproject.com/route/stats/{mp_route_id}/{route_name_slug}"

    try:
        driver.get(stats_url)
        time.sleep(3)  # Wait for page load

        tick_data = []
        new_climbers = []

        # Find all tables
        tables = driver.find_elements(By.TAG_NAME, "table")

        # Find table with most rows (tick table)
        tick_table = None
        max_rows = 0

        for table in tables:
            rows = table.find_elements(By.TAG_NAME, "tr")
            if len(rows) > max_rows:
                max_rows = len(rows)
                tick_table = table

        if tick_table and max_rows > 10:
            rows = tick_table.find_elements(By.TAG_NAME, "tr")

            for row in rows:
                try:
                    text = row.text.strip()
                    if not text or len(text) < 10:
                        continue

                    # Parse tick format: "Username\nDate · Pitches. Style. Notes..."
                    lines = text.split('\n')
                    if len(lines) < 2:
                        continue

                    username = lines[0].strip()
                    tick_info = lines[1].strip() if len(lines) > 1 else ""

                    # Extract date
                    date = None
                    date_match = re.search(r'([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})', tick_info)
                    if date_match:
                        date = date_match.group(1)

                    # Extract style
                    style = None
                    style_match = re.search(r'\.\s*(Lead|Follow|TR|Solo|Top Rope|Aid)', tick_info, re.IGNORECASE)
                    if style_match:
                        style = style_match.group(1)

                    # Extract pitches
                    pitches = None
                    pitches_match = re.search(r'(\d+)\s*pitches?', tick_info, re.IGNORECASE)
                    if pitches_match:
                        pitches = int(pitches_match.group(1))

                    # Extract notes
                    notes = None
                    if style:
                        notes_start = tick_info.find(style) + len(style)
                        notes = tick_info[notes_start:].strip('. ')
                        if notes:
                            notes = notes[:500]  # Limit length

                    # Get or create climber ID
                    if username in existing_climbers:
                        climber_id = existing_climbers[username]
                    else:
                        climber_id = next_climber_id
                        existing_climbers[username] = climber_id
                        new_climbers.append({
                            'climber_id': climber_id,
                            'username': username,
                            'location': None,
                            'years_climbing': None,
                            'bio': None,
                            'total_ticks': None,
                            'mp_user_id': None
                        })
                        next_climber_id += 1

                    tick = {
                        'ascent_id': next_ascent_id,
                        'route_id': route_id,  # Our internal route ID
                        'mp_route_id': mp_route_id,  # Mountain Project route ID
                        'climber_id': climber_id,
                        'climber_username': username,
                        'date': date,
                        'style': style,
                        'pitches': pitches,
                        'notes': notes
                    }

                    tick_data.append(tick)
                    next_ascent_id += 1

                except Exception as e:
                    continue

        return tick_data, new_climbers, next_ascent_id, next_climber_id

    except Exception as e:
        print(f"  Error scraping {stats_url}: {e}")
        return [], [], next_ascent_id, next_climber_id

def scrape_all_routes():
    """
    Main scraper function - processes all routes in routes table
    """
    print("=" * 80)
    print("MOUNTAIN PROJECT ASCENTS SCRAPER")
    print("=" * 80)
    print(f"\nStarted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Load routes table
    routes_df = pd.read_csv('data/tables/routes.csv')
    print(f"Loaded {len(routes_df)} routes from routes table")

    # Check if we have existing files to resume from
    ascents_file = 'data/tables/ascents.csv'
    climbers_file = 'data/tables/climbers.csv'
    progress_file = 'data/tables/ascents_progress.txt'

    existing_ascents = []
    processed_routes = set()
    existing_climbers = {}

    # Load existing ascents
    if os.path.exists(ascents_file):
        existing_df = pd.read_csv(ascents_file)
        existing_ascents = existing_df.to_dict('records')
        next_ascent_id = existing_df['ascent_id'].max() + 1 if len(existing_df) > 0 else 1
        print(f"Found existing ascents file with {len(existing_ascents)} ascents")
    else:
        next_ascent_id = 1

    # Load existing climbers
    if os.path.exists(climbers_file):
        climbers_df = pd.read_csv(climbers_file)
        existing_climbers = dict(zip(climbers_df['username'], climbers_df['climber_id']))
        next_climber_id = climbers_df['climber_id'].max() + 1 if len(climbers_df) > 0 else 1
        print(f"Found existing climbers file with {len(climbers_df)} climbers")
    else:
        next_climber_id = 1

    # Load progress
    if os.path.exists(progress_file):
        with open(progress_file, 'r') as f:
            processed_routes = set(int(line.strip()) for line in f if line.strip().isdigit())
        print(f"Resuming: {len(processed_routes)} routes already processed")

    # Setup driver
    driver = setup_driver()

    try:
        all_ascents = existing_ascents
        all_new_climbers = []
        total_routes = len(routes_df)

        for idx, route in routes_df.iterrows():
            route_id = route['route_id']

            # Skip if already processed
            if route_id in processed_routes:
                continue

            print(f"\n[{idx+1}/{total_routes}] Processing: {route['name']} on {route['mountain_name']}")

            # Skip routes without MP IDs
            if pd.isna(route['mp_route_id']):
                print(f"  No MP ID, skipping")
                processed_routes.add(route_id)
                with open(progress_file, 'a') as f:
                    f.write(f"{route_id}\n")
                continue

            # Create slug from route name
            route_slug = create_route_slug(route['name'])

            # Get MP route ID (convert to int then string to remove .0)
            mp_route_id = str(int(route['mp_route_id']))

            ticks, new_climbers, next_ascent_id, next_climber_id = scrape_route_ticks(
                driver, route_id, route_slug, mp_route_id,
                existing_climbers, next_ascent_id, next_climber_id
            )

            if ticks:
                print(f"  Found {len(ticks)} ticks, {len(new_climbers)} new climbers")
                all_ascents.extend(ticks)
                all_new_climbers.extend(new_climbers)

                # Save progress incrementally (every 10 routes)
                if (idx + 1) % 10 == 0:
                    ascents_df = pd.DataFrame(all_ascents)
                    ascents_df.to_csv(ascents_file, index=False)

                    # Update climbers file with new climbers
                    if all_new_climbers:
                        if os.path.exists(climbers_file):
                            existing_climbers_df = pd.read_csv(climbers_file)
                            new_climbers_df = pd.DataFrame(all_new_climbers)
                            climbers_df = pd.concat([existing_climbers_df, new_climbers_df], ignore_index=True)
                        else:
                            climbers_df = pd.DataFrame(all_new_climbers)
                        climbers_df.to_csv(climbers_file, index=False)

                    print(f"  Saved {len(all_ascents)} total ascents, {len(existing_climbers)} total climbers")
            else:
                print(f"  No ticks found")

            # Mark as processed
            processed_routes.add(route_id)
            with open(progress_file, 'a') as f:
                f.write(f"{route_id}\n")

            # Respectful delay
            time.sleep(2)

        # Final save
        if all_ascents:
            ascents_df = pd.DataFrame(all_ascents)
            ascents_df.to_csv(ascents_file, index=False)

            # Final climbers update
            if all_new_climbers:
                if os.path.exists(climbers_file):
                    existing_climbers_df = pd.read_csv(climbers_file)
                    new_climbers_df = pd.DataFrame(all_new_climbers)
                    climbers_df = pd.concat([existing_climbers_df, new_climbers_df], ignore_index=True)
                else:
                    climbers_df = pd.DataFrame(all_new_climbers)
                climbers_df.to_csv(climbers_file, index=False)

            print(f"\n{'=' * 80}")
            print(f"SCRAPING COMPLETE")
            print(f"{'=' * 80}")
            print(f"Total routes processed: {len(processed_routes)}")
            print(f"Total ascents collected: {len(all_ascents)}")
            print(f"Unique climbers: {len(existing_climbers)}")
            print(f"Saved to: {ascents_file}, {climbers_file}")

    finally:
        driver.quit()

    print(f"\nFinished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == '__main__':
    scrape_all_routes()
