"""
Test the ascents scraper with a route that has an MP ID
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

def setup_driver():
    """Setup Chrome driver"""
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def create_route_slug(route_name):
    """Convert route name to URL slug format"""
    slug = route_name.lower()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug.strip('-')

def scrape_route_ticks(driver, route_id, route_name_slug, mp_route_id):
    """Scrape all ticks from a route"""
    stats_url = f"https://www.mountainproject.com/route/stats/{mp_route_id}/{route_name_slug}"

    try:
        driver.get(stats_url)
        time.sleep(3)

        tick_data = []
        tables = driver.find_elements(By.TAG_NAME, "table")

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
                            notes = notes[:500]

                    climber_id = f"cl_{hashlib.md5(username.lower().encode()).hexdigest()[:8]}"
                    ascent_id = f"as_{hashlib.md5(f'{mp_route_id}_{climber_id}_{date}'.encode()).hexdigest()[:8]}"

                    tick = {
                        'ascent_id': ascent_id,
                        'route_id': route_id,
                        'mp_route_id': mp_route_id,
                        'climber_id': climber_id,
                        'climber_username': username,
                        'date': date,
                        'style': style,
                        'pitches': pitches,
                        'notes': notes
                    }

                    tick_data.append(tick)

                except Exception:
                    continue

        return tick_data

    except Exception as e:
        print(f"Error: {e}")
        return []

def test_with_real_route():
    """Test scraper with a route from our table that has an MP ID"""
    print("=" * 80)
    print("TESTING ASCENTS SCRAPER WITH REAL ROUTE")
    print("=" * 80)

    # Load routes with MP IDs
    routes_df = pd.read_csv('data/tables/routes.csv')
    routes_with_mp = routes_df[routes_df['mp_route_id'].notna()]

    print(f"\nFound {len(routes_with_mp)} routes with MP IDs")

    # Find Casual Route on Longs Peak (known popular route)
    test_route = routes_with_mp[routes_with_mp['name'] == 'Casual Route'].iloc[0]

    print("\nTesting with Casual Route on Longs Peak (popular route):")
    print(f"  Route: {test_route['name']}")
    print(f"  Mountain: {test_route['mountain_name']}")
    print(f"  MP ID: {test_route['mp_route_id']}")
    print(f"  Our route ID: {test_route['route_id']}")

    driver = setup_driver()

    try:
        route_slug = create_route_slug(test_route['name'])
        mp_id = str(int(test_route['mp_route_id']))  # Convert to int then string to remove .0

        print(f"\nScraping ticks from:")
        print(f"  https://www.mountainproject.com/route/stats/{mp_id}/{route_slug}")

        ticks = scrape_route_ticks(driver, test_route['route_id'], route_slug, mp_id)

        print(f"\n{'=' * 80}")
        print(f"RESULTS")
        print(f"{'=' * 80}")
        print(f"Found {len(ticks)} ticks")

        if ticks:
            print(f"\nUnique climbers: {len(set(t['climber_id'] for t in ticks))}")

            print(f"\nFirst 5 ticks:")
            for i, tick in enumerate(ticks[:5]):
                print(f"\n{i+1}. {tick['climber_username']} ({tick['date']})")
                print(f"   Style: {tick['style']}, Pitches: {tick['pitches']}")
                print(f"   Notes: {tick['notes'][:80] if tick['notes'] else 'None'}...")

            # Save to test CSV
            test_df = pd.DataFrame(ticks)
            test_df.to_csv('data/test_ascents.csv', index=False)
            print(f"\n✓ Saved {len(ticks)} ascents to data/test_ascents.csv")

        else:
            print("\n✗ No ticks found - page structure may have changed")

    finally:
        driver.quit()

if __name__ == '__main__':
    test_with_real_route()
