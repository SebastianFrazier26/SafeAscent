"""
Targeted Mountain Project ID Search for Accident-Prone Routes

Focuses ONLY on routes with accidents and uses smart search strategies
to maximize MP ID discovery for routes likely to be on Mountain Project.
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

def should_skip_route(route_name, mountain_name):
    """
    Determine if route is unlikely to be on Mountain Project
    (pure mountaineering routes, glacier routes, etc.)
    """
    skip_keywords = [
        'buttress', 'glacier', 'couloir', 'gully',
        'avalanche gulch', 'football field', 'aai', 'iai',
        'tourist route', 'orient express', 'southside',
        'kautz', 'emmons', 'ingraham'
    ]

    combined = f"{route_name} {mountain_name}".lower()

    # McKinley/Denali routes usually not on MP
    if 'mckinley' in combined or 'denali' in combined:
        return True

    # Pure glacier routes
    for keyword in skip_keywords:
        if keyword in combined:
            return True

    return False

def smart_search_mp(driver, route_name, mountain_name, route_grade=None):
    """
    Smart search for MP route ID using multiple strategies
    Returns: MP route ID or None
    """
    # Strategy 1: Full route + mountain name
    queries = [
        f"{route_name} {mountain_name}",
    ]

    # Strategy 2: Add state/region if available
    if mountain_name:
        # Try without "Mount" prefix
        clean_mountain = mountain_name.replace('Mount ', '').replace('Mt ', '').replace('Mt. ', '')
        queries.append(f"{route_name} {clean_mountain}")

    # Strategy 3: Route name only (for famous routes)
    if len(route_name.split()) <= 3:  # Short route names
        queries.append(route_name)

    # Try each query
    for query in queries:
        search_url = f"https://www.mountainproject.com/search?q={query.replace(' ', '+')}"

        try:
            driver.get(search_url)
            time.sleep(2)

            # Look for route links
            route_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/route/']")

            for link in route_links:
                href = link.get_attribute('href')
                if not href:
                    continue

                # Extract route ID from URL
                match = re.search(r'/route/(\d+)/', href)
                if not match:
                    continue

                mp_route_id = match.group(1)
                link_text = link.text.strip().lower()
                route_lower = route_name.lower()

                # Check if it's a good match
                if route_lower in link_text or link_text in route_lower:
                    # Double check by visiting the route page
                    driver.get(href)
                    time.sleep(1)

                    # Verify it's the right route
                    page_title = driver.title.lower()
                    if route_lower in page_title or any(word in page_title for word in route_name.lower().split() if len(word) > 3):
                        return mp_route_id

        except Exception as e:
            continue

    return None

def find_accident_route_ids(limit=None):
    """
    Find MP IDs for accident-prone routes only
    """
    print("\n" + "=" * 80)
    print("TARGETED MP ID SEARCH - ACCIDENT ROUTES ONLY")
    print("=" * 80)

    # Load routes
    routes_df = pd.read_csv('data/tables/routes.csv')

    # Filter: only routes with accidents AND without MP IDs
    accident_routes = routes_df[
        (routes_df['accident_count'] > 0) &
        (routes_df['mp_route_id'].isna())
    ]

    # Sort by accident count (prioritize most important routes)
    accident_routes = accident_routes.sort_values('accident_count', ascending=False)

    if limit:
        accident_routes = accident_routes.head(limit)

    print(f"\nRoutes with accidents: {len(routes_df[routes_df['accident_count'] > 0])}")
    print(f"Routes WITHOUT MP IDs: {len(accident_routes)}")
    print(f"Processing: {len(accident_routes)} routes\n")

    # Skip pure mountaineering routes
    routes_to_search = []
    routes_skipped = []

    for idx, route in accident_routes.iterrows():
        if should_skip_route(route['name'], route['mountain_name']):
            routes_skipped.append((route['name'], route['mountain_name']))
        else:
            routes_to_search.append((idx, route))

    print(f"Routes likely on MP: {len(routes_to_search)}")
    print(f"Routes skipped (pure mountaineering): {len(routes_skipped)}\n")

    if routes_skipped:
        print("Skipped routes (not on MP):")
        for name, mountain in routes_skipped[:10]:
            print(f"  - {name} on {mountain}")
        if len(routes_skipped) > 10:
            print(f"  ... and {len(routes_skipped) - 10} more")
        print()

    # Search for MP IDs
    driver = setup_driver()
    found_count = 0
    not_found_count = 0

    try:
        pbar = tqdm(routes_to_search, desc="Finding MP IDs")

        for idx, route in pbar:
            route_name = route['name'][:30] if len(route['name']) > 30 else route['name']
            pbar.set_description(f"Searching: {route_name}")

            mp_route_id = smart_search_mp(
                driver,
                route['name'],
                route['mountain_name'],
                route.get('grade')
            )

            if mp_route_id:
                routes_df.at[idx, 'mp_route_id'] = mp_route_id
                found_count += 1
                pbar.set_postfix({'found': found_count, 'not_found': not_found_count})
            else:
                not_found_count += 1
                pbar.set_postfix({'found': found_count, 'not_found': not_found_count})

            # Save progress every 10 routes
            if (found_count + not_found_count) % 10 == 0:
                routes_df.to_csv('data/tables/routes.csv', index=False)

            time.sleep(2)  # Respectful delay

        pbar.close()

        # Final save
        routes_df.to_csv('data/tables/routes.csv', index=False)

        print("\n" + "=" * 80)
        print("SEARCH COMPLETE")
        print("=" * 80)
        print(f"Routes searched: {len(routes_to_search)}")
        print(f"MP IDs found: {found_count}")
        print(f"Not found: {not_found_count}")
        print(f"\nTotal routes with MP IDs: {routes_df['mp_route_id'].notna().sum()}")
        print(f"Coverage: {routes_df['mp_route_id'].notna().sum() / len(routes_df) * 100:.1f}%")

    finally:
        driver.quit()

if __name__ == '__main__':
    # Process top 100 accident routes (most important)
    find_accident_route_ids(limit=100)
