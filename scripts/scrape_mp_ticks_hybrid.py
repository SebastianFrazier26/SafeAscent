#!/usr/bin/env python3
"""
Mountain Project Ticks Scraper (Hybrid Version)
================================================
Scrapes tick (ascent) data from route statistics pages.
Uses hybrid approach: writes to local files AND batches to Neon database.

Input: data/.mp_scrape_progress_v2.json (routes with URLs)
Output:
    - data/mp_ticks.csv (local file - source of truth)
    - Neon database mp_ticks table (synced in batches)

Usage:
    python scripts/scrape_mp_ticks_hybrid.py [--limit N] [--resume]

Note: Requires Selenium with Chrome for JavaScript rendering.
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, InvalidSessionIdException, WebDriverException
import pandas as pd
import json
import time
import re
import argparse
from pathlib import Path
from datetime import datetime
import logging
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Rate limiting
REQUEST_DELAY = 0.5  # seconds between requests
DRIVER_RESTART_INTERVAL = 100  # Restart driver every N routes to prevent session issues

# Database configuration
NEON_URL = "postgresql://neondb_owner:npg_mrghaUfM78Xb@ep-billowing-bonus-ajxfhalu-pooler.c-3.us-east-2.aws.neon.tech/neondb?sslmode=require"
DB_BATCH_SIZE = 100  # Insert to DB every N ticks


def get_db_connection():
    """Get database connection with retry logic."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(NEON_URL)
            conn.autocommit = False
            return conn
        except Exception as e:
            logger.warning(f"DB connection attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return None


def batch_insert_ticks(conn, ticks_batch):
    """
    Batch insert ticks to database using upsert (ON CONFLICT DO NOTHING).

    Args:
        conn: Database connection
        ticks_batch: List of tick dictionaries

    Returns:
        Number of rows inserted
    """
    if not ticks_batch or not conn:
        return 0

    try:
        cur = conn.cursor()

        # Prepare values for insert
        values = []
        for tick in ticks_batch:
            values.append((
                tick['route_id'],
                tick.get('route_name', '')[:255] if tick.get('route_name') else None,
                tick['climber_name'][:255],
                tick.get('date'),
                tick.get('style', '')[:50] if tick.get('style') else None,
            ))

        # Upsert query (ignore duplicates)
        insert_sql = """
            INSERT INTO mp_ticks (route_id, route_name, climber_name, tick_date, style)
            VALUES %s
            ON CONFLICT (route_id, climber_name, tick_date) DO NOTHING
        """

        execute_values(cur, insert_sql, values, page_size=100)
        inserted = cur.rowcount
        conn.commit()

        return inserted

    except Exception as e:
        logger.error(f"Database insert failed: {e}")
        try:
            conn.rollback()
        except:
            pass
        return 0


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
    """Convert route URL to stats URL."""
    return route_url.replace('/route/', '/route/stats/')


def parse_date(date_str):
    """Parse date string to YYYY-MM-DD format."""
    if not date_str:
        return None

    try:
        for fmt in ['%b %d, %Y', '%B %d, %Y', '%m/%d/%Y', '%Y-%m-%d', '%b %d, %y', '%m/%d/%y']:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                year = dt.year
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
        time.sleep(2)

        tables = driver.find_elements(By.TAG_NAME, "table")
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

                lines = text.split('\n')
                if len(lines) < 2:
                    continue

                username = lines[0].strip()

                if username.lower() in ['name', 'user', 'climber', 'ticks', '']:
                    continue

                tick_info = ' '.join(lines[1:])

                date = None
                date_match = re.search(r'([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})', tick_info)
                if date_match:
                    date = parse_date(date_match.group(1))

                style = None
                style_patterns = ['Lead', 'Follow', 'TR', 'Solo', 'Top Rope', 'Aid', 'Flash', 'Onsight', 'Redpoint', 'Send']
                for s in style_patterns:
                    if s.lower() in tick_info.lower():
                        style = s
                        break

                if username and date:
                    ticks.append({
                        'route_id': route_id,
                        'route_name': route_name,
                        'climber_name': username,
                        'date': date,
                        'style': style,
                    })

            except Exception:
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
    """Main scraping function with hybrid file + database writes."""
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
    db_synced_count = 0  # Track how many ticks have been synced to DB

    if resume and progress_file.exists():
        with open(progress_file) as f:
            progress = json.load(f)
            processed_ids = set(progress.get('processed_ids', []))
            all_ticks = progress.get('ticks', [])
            db_synced_count = progress.get('db_synced_count', 0)
        logger.info(f"Resuming: {len(processed_ids)} routes processed, {len(all_ticks)} ticks collected, {db_synced_count} synced to DB")

    # Connect to database
    db_conn = get_db_connection()
    if db_conn:
        logger.info("Connected to Neon database for hybrid writes")
    else:
        logger.warning("Could not connect to database - will write to local files only")

    # Filter routes
    routes_to_process = [r for r in routes if r['mp_route_id'] not in processed_ids]

    if limit:
        routes_to_process = routes_to_process[:limit]

    logger.info(f"Processing {len(routes_to_process)} routes")

    # Setup driver
    driver = setup_driver()
    logger.info("Chrome driver initialized")
    routes_since_restart = 0
    consecutive_errors = 0
    pending_db_ticks = []  # Buffer for database writes

    try:
        for idx, route in enumerate(routes_to_process):
            route_id = route['mp_route_id']
            route_url = route['url']
            route_name = route['name']

            try:
                ticks = scrape_route_ticks(driver, route_id, route_url, route_name)
                all_ticks.extend(ticks)
                pending_db_ticks.extend(ticks)  # Add to DB buffer
                processed_ids.add(route_id)
                routes_since_restart += 1
                consecutive_errors = 0

            except (InvalidSessionIdException, WebDriverException) as e:
                logger.warning(f"Driver error on route {route_id}: {e}")
                consecutive_errors += 1

                try:
                    driver.quit()
                except:
                    pass

                logger.info("Restarting Chrome driver...")
                time.sleep(2)
                driver = setup_driver()
                routes_since_restart = 0

                try:
                    ticks = scrape_route_ticks(driver, route_id, route_url, route_name)
                    all_ticks.extend(ticks)
                    pending_db_ticks.extend(ticks)
                    processed_ids.add(route_id)
                    consecutive_errors = 0
                except Exception as retry_e:
                    logger.error(f"Retry failed for route {route_id}: {retry_e}")
                    processed_ids.add(route_id)

            # Batch insert to database when buffer is full
            if len(pending_db_ticks) >= DB_BATCH_SIZE and db_conn:
                inserted = batch_insert_ticks(db_conn, pending_db_ticks)
                if inserted > 0:
                    db_synced_count += inserted
                    logger.info(f"DB sync: inserted {inserted} ticks (total synced: {db_synced_count})")
                pending_db_ticks = []

                # Reconnect if connection was lost
                if inserted == 0:
                    try:
                        db_conn.close()
                    except:
                        pass
                    db_conn = get_db_connection()

            # Periodic driver restart
            if routes_since_restart >= DRIVER_RESTART_INTERVAL:
                logger.info(f"Preventive driver restart after {routes_since_restart} routes")
                try:
                    driver.quit()
                except:
                    pass
                time.sleep(2)
                driver = setup_driver()
                routes_since_restart = 0

            # Progress logging and local file save
            if (idx + 1) % 10 == 0:
                logger.info(f"Processed {len(processed_ids)}/{len(routes)} routes, {len(all_ticks)} ticks collected")

                # Save progress to local file (source of truth)
                with open(progress_file, 'w') as f:
                    json.dump({
                        'processed_ids': list(processed_ids),
                        'ticks': all_ticks,
                        'routes_processed': len(processed_ids),
                        'ticks_collected': len(all_ticks),
                        'db_synced_count': db_synced_count,
                        'last_updated': datetime.now().isoformat()
                    }, f)

            time.sleep(REQUEST_DELAY)

            if consecutive_errors >= 5:
                logger.error("Too many consecutive errors, stopping...")
                break

    except KeyboardInterrupt:
        logger.info("Interrupted! Saving progress...")
    finally:
        # Final DB sync
        if pending_db_ticks and db_conn:
            inserted = batch_insert_ticks(db_conn, pending_db_ticks)
            if inserted > 0:
                db_synced_count += inserted
                logger.info(f"Final DB sync: inserted {inserted} ticks")

        try:
            driver.quit()
        except:
            pass

        if db_conn:
            try:
                db_conn.close()
            except:
                pass

        # Save final progress
        with open(progress_file, 'w') as f:
            json.dump({
                'processed_ids': list(processed_ids),
                'ticks': all_ticks,
                'routes_processed': len(processed_ids),
                'ticks_collected': len(all_ticks),
                'db_synced_count': db_synced_count,
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
        print(f"Ticks synced to DB: {db_synced_count}")
        if all_ticks:
            df = pd.DataFrame(all_ticks)
            print(f"\nTicks per route: {len(all_ticks) / max(len(processed_ids), 1):.1f} avg")
            print(f"Unique climbers: {df['climber_name'].nunique()}")
            print(f"Date range: {df['date'].min()} to {df['date'].max()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Mountain Project ticks (hybrid mode)")
    parser.add_argument("--limit", type=int, help="Limit number of routes to process")
    parser.add_argument("--resume", action="store_true", help="Resume from progress file")
    args = parser.parse_args()

    main(limit=args.limit, resume=args.resume)
