#!/usr/bin/env python3
"""
Mountain Project Ticks Scraper
==============================
Scrapes tick (ascent) data from route statistics pages.
Uses routes already collected by scrape_mp_routes_v2.py.

Input: data/.mp_scrape_progress_v2.json (routes with URLs)
Output: data/mp_ticks.csv

Usage:
    python scripts/scrape_mp_ticks.py [--limit N] [--resume]

Note: Requires Selenium with Chrome for JavaScript rendering.
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import pandas as pd
import json
import time
import re
import argparse
from pathlib import Path
from datetime import datetime
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Rate limiting
REQUEST_DELAY = 0.5  # seconds between requests


def setup_driver():
    """Setup headless Chrome driver."""
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

    driver = webdriver.Chrome(options=chrome_options)
    driver.set_page_load_timeout(30)
    return driver


def get_stats_url(route_url):
    """Convert route URL to stats URL.

    Route: https://www.mountainproject.com/route/105835705/southeast-buttress
    Stats: https://www.mountainproject.com/route/stats/105835705/southeast-buttress
    """
    return route_url.replace('/route/', '/route/stats/')


def parse_date(date_str):
    """Parse date string to YYYY-MM-DD format."""
    if not date_str:
        return None

    try:
        # Try common formats
        for fmt in ['%b %d, %Y', '%B %d, %Y', '%m/%d/%Y', '%Y-%m-%d', '%b %d, %y', '%m/%d/%y']:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                year = dt.year

                # Fix 2-digit year parsing (assume 1950-2050 range)
                if year > 2050:
                    year -= 100
                elif year < 1950:
                    year += 100

                return f"{year:04d}-{dt.month:02d}-{dt.day:02d}"
            except ValueError:
                continue
    except Exception:
        pass

    return None


def scrape_route_ticks(driver, route_id, route_url, route_name):
    """Scrape all ticks from a route stats page."""
    stats_url = get_stats_url(route_url)
    ticks = []

    try:
        driver.get(stats_url)
        time.sleep(2)  # Wait for JS to load

        # Find all tables
        tables = driver.find_elements(By.TAG_NAME, "table")

        # Find the tick table (usually the one with most rows)
        tick_table = None
        max_rows = 0

        for table in tables:
            try:
                rows = table.find_elements(By.TAG_NAME, "tr")
                if len(rows) > max_rows:
                    max_rows = len(rows)
                    tick_table = table
            except Exception:
                continue

        if not tick_table or max_rows < 2:
            return ticks

        rows = tick_table.find_elements(By.TAG_NAME, "tr")

        for row in rows:
            try:
                text = row.text.strip()
                if not text or len(text) < 5:
                    continue

                # Parse tick format: "Username\nDate · Style. Notes..."
                lines = text.split('\n')
                if len(lines) < 2:
                    continue

                username = lines[0].strip()

                # Skip header rows
                if username.lower() in ['name', 'user', 'climber', 'ticks', '']:
                    continue

                tick_info = ' '.join(lines[1:])

                # Extract date
                date = None
                date_match = re.search(r'([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})', tick_info)
                if date_match:
                    date = parse_date(date_match.group(1))

                # Extract style
                style = None
                style_patterns = ['Lead', 'Follow', 'TR', 'Solo', 'Top Rope', 'Aid', 'Flash', 'Onsight', 'Redpoint', 'Send']
                for s in style_patterns:
                    if s.lower() in tick_info.lower():
                        style = s
                        break

                # Only add if we have at least username and date
                if username and date:
                    ticks.append({
                        'route_id': route_id,
                        'route_name': route_name,
                        'climber_name': username,
                        'date': date,
                        'style': style,
                    })

            except Exception as e:
                continue

        return ticks

    except TimeoutException:
        logger.warning(f"Timeout loading {stats_url}")
        return ticks
    except Exception as e:
        logger.error(f"Error scraping {stats_url}: {e}")
        return ticks


def load_routes():
    """Load routes from progress file."""
    progress_file = Path("data/.mp_scrape_progress_v2.json")

    if not progress_file.exists():
        # Try CSV file
        csv_file = Path("data/mp_routes_v2.csv")
        if csv_file.exists():
            df = pd.read_csv(csv_file)
            return df.to_dict('records')
        else:
            logger.error("No route data found!")
            return []

    with open(progress_file) as f:
        data = json.load(f)

    return list(data.get('routes', {}).values())


def main(limit=None, resume=False):
    """Main scraping function."""
    # Load routes
    routes = load_routes()
    logger.info(f"Loaded {len(routes)} routes")

    if not routes:
        return

    # Progress tracking
    progress_file = Path("data/.mp_ticks_progress.json")
    output_file = Path("data/mp_ticks.csv")

    processed_ids = set()
    all_ticks = []

    if resume and progress_file.exists():
        with open(progress_file) as f:
            progress = json.load(f)
            processed_ids = set(progress.get('processed_ids', []))
            all_ticks = progress.get('ticks', [])
        logger.info(f"Resuming: {len(processed_ids)} routes processed, {len(all_ticks)} ticks collected")

    # Filter routes
    routes_to_process = [r for r in routes if r['mp_route_id'] not in processed_ids]

    if limit:
        routes_to_process = routes_to_process[:limit]

    logger.info(f"Processing {len(routes_to_process)} routes")

    # Setup driver
    driver = setup_driver()
    logger.info("Chrome driver initialized")

    try:
        for idx, route in enumerate(routes_to_process):
            route_id = route['mp_route_id']
            route_url = route['url']
            route_name = route['name']

            ticks = scrape_route_ticks(driver, route_id, route_url, route_name)
            all_ticks.extend(ticks)
            processed_ids.add(route_id)

            # Progress logging
            if (idx + 1) % 10 == 0:
                logger.info(f"Processed {idx + 1}/{len(routes_to_process)} routes, {len(all_ticks)} ticks collected")

                # Save progress
                with open(progress_file, 'w') as f:
                    json.dump({
                        'processed_ids': list(processed_ids),
                        'ticks': all_ticks,
                        'last_updated': datetime.now().isoformat()
                    }, f)

            # Rate limiting
            time.sleep(REQUEST_DELAY)

    except KeyboardInterrupt:
        logger.info("Interrupted! Saving progress...")
    finally:
        driver.quit()

        # Save progress
        with open(progress_file, 'w') as f:
            json.dump({
                'processed_ids': list(processed_ids),
                'ticks': all_ticks,
                'last_updated': datetime.now().isoformat()
            }, f)

        # Save to CSV
        if all_ticks:
            df = pd.DataFrame(all_ticks)
            df.to_csv(output_file, index=False)
            logger.info(f"Saved {len(all_ticks)} ticks to {output_file}")

        # Print stats
        print(f"\n=== TICK SCRAPING STATS ===")
        print(f"Routes processed: {len(processed_ids)}")
        print(f"Ticks collected: {len(all_ticks)}")
        if all_ticks:
            df = pd.DataFrame(all_ticks)
            print(f"\nTicks per route: {len(all_ticks) / len(processed_ids):.1f} avg")
            print(f"Unique climbers: {df['climber_name'].nunique()}")
            print(f"Date range: {df['date'].min()} to {df['date'].max()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Mountain Project ticks")
    parser.add_argument("--limit", type=int, help="Limit number of routes to process")
    parser.add_argument("--resume", action="store_true", help="Resume from progress file")
    args = parser.parse_args()

    main(limit=args.limit, resume=args.resume)
