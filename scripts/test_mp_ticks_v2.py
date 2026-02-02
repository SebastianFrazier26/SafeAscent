"""
Mountain Project Tick Scraper v2 - Fixed to use stats page
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
import hashlib
import re

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

def scrape_route_ticks_from_stats(driver, route_id, route_name_slug, limit=10):
    """
    Scrape ticks from the route stats page
    route_id: MP route ID (e.g., '105924807')
    route_name_slug: URL-friendly route name (e.g., 'the-nose')
    """
    stats_url = f"https://www.mountainproject.com/route/stats/{route_id}/{route_name_slug}"

    print(f"\n{'=' * 80}")
    print(f"Navigating to stats page: {stats_url}")
    print(f"{'=' * 80}")

    driver.get(stats_url)

    # Wait for page to load and JavaScript to execute
    print("Waiting for page to load...")
    time.sleep(5)  # Give JavaScript time to load tick data

    tick_data = []

    print("\nLooking for tick table...")

    try:
        # Find all tables
        tables = driver.find_elements(By.TAG_NAME, "table")
        print(f"Found {len(tables)} tables on page")

        # Look for the table with the most rows (likely the tick table)
        tick_table = None
        max_rows = 0

        for i, table in enumerate(tables):
            rows = table.find_elements(By.TAG_NAME, "tr")
            if len(rows) > max_rows:
                max_rows = len(rows)
                tick_table = table
                print(f"  Table {i+1} has {len(rows)} rows (new max)")

        if tick_table and max_rows > 10:  # Tick table should have many rows
            print(f"\nUsing table with {max_rows} rows as tick table")
            rows = tick_table.find_elements(By.TAG_NAME, "tr")

            print(f"Extracting first {min(limit, len(rows))} ticks...\n")

            for i, row in enumerate(rows[:limit]):
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

                    # Extract date (format: "Jan 21, 2026")
                    date = None
                    date_match = re.search(r'([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})', tick_info)
                    if date_match:
                        date = date_match.group(1)

                    # Extract style (Lead, Follow, TR, Solo, etc.)
                    style = None
                    style_match = re.search(r'\.\s*(Lead|Follow|TR|Solo|Top Rope|Aid)', tick_info, re.IGNORECASE)
                    if style_match:
                        style = style_match.group(1)

                    # Extract pitches
                    pitches = None
                    pitches_match = re.search(r'(\d+)\s*pitches?', tick_info, re.IGNORECASE)
                    if pitches_match:
                        pitches = int(pitches_match.group(1))

                    # Extract notes (everything after style)
                    notes = None
                    if style:
                        notes_start = tick_info.find(style) + len(style)
                        notes = tick_info[notes_start:].strip('. ')
                        if notes:
                            notes = notes[:500]  # Limit length

                    # Generate IDs
                    climber_id = f"cl_{hashlib.md5(username.lower().encode()).hexdigest()[:8]}"
                    ascent_id = f"as_{hashlib.md5(f'{route_id}_{climber_id}_{date}'.encode()).hexdigest()[:8]}"

                    tick = {
                        'ascent_id': ascent_id,
                        'route_id': route_id,
                        'climber_id': climber_id,
                        'climber_username': username,
                        'date': date,
                        'style': style,
                        'pitches': pitches,
                        'notes': notes
                    }

                    tick_data.append(tick)

                    # Print sample
                    if i < 5:
                        print(f"Tick {i+1}:")
                        print(f"  Climber: {username} (ID: {climber_id})")
                        print(f"  Date: {date}")
                        print(f"  Style: {style}")
                        print(f"  Pitches: {pitches}")
                        print(f"  Notes: {notes[:100] if notes else 'No notes'}...")
                        print()

                except Exception as e:
                    print(f"  Error parsing tick {i+1}: {e}")
                    continue

            if len(rows) > limit:
                print(f"... and {len(rows) - limit} more ticks available\n")

        else:
            print("Could not find tick table with sufficient rows")

    except Exception as e:
        print(f"Error finding ticks: {e}")

    return tick_data

def test_tick_scraping():
    """Test tick scraping with known route"""
    print("=" * 80)
    print("TESTING MOUNTAIN PROJECT TICK SCRAPING V2")
    print("=" * 80)
    print("\nTesting with The Nose on El Capitan")
    print("This route has 645 ticks according to MP")

    driver = setup_driver()

    try:
        # The Nose: route ID 105924807
        ticks = scrape_route_ticks_from_stats(driver, "105924807", "the-nose", limit=10)

        print(f"\n{'=' * 80}")
        print(f"RESULTS: Found {len(ticks)} tick entries")
        print(f"{'=' * 80}")

        if ticks:
            print("\nSample tick data:")
            for i, tick in enumerate(ticks[:5]):
                print(f"\nTick {i+1}:")
                print(f"  Ascent ID: {tick['ascent_id']}")
                print(f"  Climber: {tick['climber_username']} (ID: {tick['climber_id']})")
                print(f"  Date: {tick['date']}")
                print(f"  Style: {tick['style']}")
                print(f"  Pitches: {tick['pitches']}")
                print(f"  Notes: {tick['notes'][:100] if tick['notes'] else 'None'}...")
        else:
            print("\nNo ticks extracted - need to investigate page structure further")
            print("This is expected on first attempt with dynamic content")

    finally:
        driver.quit()

    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)
    print("\nNext step: Analyze the page structure from the debug output above")
    print("to build proper selectors for extracting tick data")

if __name__ == '__main__':
    test_tick_scraping()
