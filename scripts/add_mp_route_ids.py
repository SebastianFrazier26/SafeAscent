"""
Add Mountain Project route IDs to routes table
Searches MP for each route and extracts the route ID from the URL

Features:
- Live progress tracking with tqdm
- Prioritizes routes by accident count
- Resumable progress
- Incremental saves
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import pandas as pd
import time
import re
from tqdm import tqdm

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

def search_mountain_project(driver, route_name, mountain_name):
    """
    Search Mountain Project for a route and return the MP route ID
    Returns: MP route ID (string) or None
    """
    query = f"{route_name} {mountain_name}"
    search_url = f"https://www.mountainproject.com/search?q={query.replace(' ', '+')}"

    try:
        driver.get(search_url)
        time.sleep(2)

        # Look for route links in search results
        # MP route URLs are: /route/{id}/{name-slug}
        route_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/route/']")

        for link in route_links:
            href = link.get_attribute('href')
            # Extract route ID from URL
            match = re.search(r'/route/(\d+)/', href)
            if match:
                mp_route_id = match.group(1)
                # Get the link text to verify it's a good match
                link_text = link.text.strip().lower()
                if route_name.lower() in link_text or link_text in route_name.lower():
                    return mp_route_id

        return None

    except Exception as e:
        print(f"  Error searching: {e}")
        return None

def add_mp_ids_to_routes(limit=None, start_from=0):
    """
    Add Mountain Project IDs to routes table
    limit: Maximum number of routes to process (None = all)
    start_from: Index to start from (for resuming)
    """
    print("=" * 80)
    print("ADDING MOUNTAIN PROJECT ROUTE IDs")
    print("=" * 80)

    routes_df = pd.read_csv('data/tables/routes.csv')

    # Add mp_route_id column if it doesn't exist
    if 'mp_route_id' not in routes_df.columns:
        routes_df['mp_route_id'] = None

    print(f"\nTotal routes in table: {len(routes_df)}")

    if limit:
        print(f"Processing routes {start_from} to {start_from + limit}\n")
        routes_to_process = routes_df.iloc[start_from:start_from + limit]
    else:
        print(f"Processing all routes starting from index {start_from}\n")
        routes_to_process = routes_df.iloc[start_from:]

    driver = setup_driver()
    found_count = 0
    not_found_count = 0
    skipped_count = 0

    try:
        pbar = tqdm(routes_to_process.iterrows(), total=len(routes_to_process), desc="Finding MP IDs")

        for idx, route in pbar:
            # Skip if already has MP ID
            if pd.notna(route['mp_route_id']):
                skipped_count += 1
                pbar.set_postfix({'found': found_count, 'not_found': not_found_count, 'skipped': skipped_count})
                continue

            route_name = route['name'][:30] if len(route['name']) > 30 else route['name']
            pbar.set_description(f"Searching: {route_name}")

            mp_route_id = search_mountain_project(driver, route['name'], route['mountain_name'])

            if mp_route_id:
                routes_df.at[idx, 'mp_route_id'] = mp_route_id
                found_count += 1
            else:
                not_found_count += 1

            pbar.set_postfix({'found': found_count, 'not_found': not_found_count, 'skipped': skipped_count})

            # Save progress every 10 routes
            if (found_count + not_found_count) % 10 == 0:
                routes_df.to_csv('data/tables/routes.csv', index=False)

            # Respectful delay
            time.sleep(2)

        pbar.close()

        # Final save
        routes_df.to_csv('data/tables/routes.csv', index=False)

        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Routes with MP IDs: {routes_df['mp_route_id'].notna().sum()}/{len(routes_df)}")
        print(f"This session - Found: {found_count}, Not found: {not_found_count}, Skipped: {skipped_count}")

    finally:
        driver.quit()

if __name__ == '__main__':
    # Start with high-value routes: those with 2+ accidents
    routes_df = pd.read_csv('data/tables/routes.csv')
    routes_df = routes_df.sort_values('accident_count', ascending=False)
    routes_df.to_csv('data/tables/routes.csv', index=False)

    print("\nStarting with routes sorted by accident count (highest first)...")
    print("This ensures we get MP IDs for the most important routes first.\n")

    # Process first 50 routes (most accidents)
    add_mp_ids_to_routes(limit=50, start_from=0)
