"""
Mountain Project Climber Profile & Tick List Scraper

Scrapes climber profiles and complete tick lists from Mountain Project.
Discovers new routes and expands the dataset exponentially.

Features:
- Live progress tracking with tqdm
- Incremental saves every 5 climbers
- Resumable progress tracking
- Automatic route discovery
- Foreign key integrity maintenance
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import pandas as pd
import time
import re
from datetime import datetime
import os
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

def create_route_slug(route_name):
    """Convert route name to URL slug format"""
    slug = route_name.lower()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug.strip('-')

def search_climber(driver, username):
    """
    Search for climber on Mountain Project
    Returns profile URL or None if not found
    """
    try:
        search_url = f"https://www.mountainproject.com/search?q={username.replace(' ', '+')}"
        driver.get(search_url)
        time.sleep(2)

        # Look for user profile link
        links = driver.find_elements(By.TAG_NAME, "a")
        for link in links:
            href = link.get_attribute('href')
            if href and '/user/' in href and username.lower() in link.text.lower():
                return href

        return None

    except Exception as e:
        print(f"  Error searching for {username}: {e}")
        return None

def scrape_climber_profile(driver, profile_url, climber_id, username):
    """
    Scrape climber profile data
    Returns updated climber dict
    """
    try:
        driver.get(profile_url)
        time.sleep(2)

        climber_data = {
            'climber_id': climber_id,
            'username': username,
            'location': None,
            'years_climbing': None,
            'bio': None,
            'total_ticks': None,
            'mp_user_id': None
        }

        # Extract MP user ID from URL
        mp_user_id_match = re.search(r'/user/(\d+)/', profile_url)
        if mp_user_id_match:
            climber_data['mp_user_id'] = mp_user_id_match.group(1)

        # Try to find profile details (this varies by MP page structure)
        try:
            # Location
            location_elements = driver.find_elements(By.XPATH, "//td[contains(text(), 'Location')]/following-sibling::td")
            if location_elements:
                climber_data['location'] = location_elements[0].text.strip()

            # Years climbing
            years_elements = driver.find_elements(By.XPATH, "//td[contains(text(), 'Years Climbing')]/following-sibling::td")
            if years_elements:
                years_text = years_elements[0].text.strip()
                years_match = re.search(r'(\d+)', years_text)
                if years_match:
                    climber_data['years_climbing'] = int(years_match.group(1))

            # Bio
            bio_elements = driver.find_elements(By.CLASS_NAME, "bio")
            if bio_elements:
                climber_data['bio'] = bio_elements[0].text.strip()[:500]  # Limit length

        except Exception:
            pass  # Some fields might not be available

        return climber_data

    except Exception as e:
        print(f"  Error scraping profile: {e}")
        return None

def scrape_climber_ticks(driver, mp_user_id, climber_id, username, existing_routes, next_route_id, next_ascent_id):
    """
    Scrape all ticks from climber's tick list
    Returns: new_ascents, new_routes, updated_next_route_id, updated_next_ascent_id
    """
    try:
        # Navigate to ticks page
        ticks_url = f"https://www.mountainproject.com/user/{mp_user_id}/{create_route_slug(username)}/tick-list"
        driver.get(ticks_url)
        time.sleep(3)

        new_ascents = []
        new_routes = []
        routes_by_name = {r['name'].lower(): r for r in existing_routes}

        # Find tick table
        tables = driver.find_elements(By.TAG_NAME, "table")

        if not tables:
            return new_ascents, new_routes, next_route_id, next_ascent_id

        # Process largest table (usually the tick list)
        tick_table = max(tables, key=lambda t: len(t.find_elements(By.TAG_NAME, "tr")))
        rows = tick_table.find_elements(By.TAG_NAME, "tr")

        for row in rows:
            try:
                text = row.text.strip()
                if not text or len(text) < 10:
                    continue

                # Parse tick row
                # Typical format: "Route Name · Grade · Location\nDate · Style · Notes"
                lines = text.split('\n')
                if len(lines) < 1:
                    continue

                # Extract route name and grade from first line
                first_line = lines[0]
                parts = first_line.split('·')

                if len(parts) < 2:
                    continue

                route_name = parts[0].strip()
                grade = parts[1].strip() if len(parts) > 1 else None

                # Try to get MP route ID from link
                mp_route_id = None
                try:
                    route_link = row.find_element(By.TAG_NAME, "a")
                    href = route_link.get_attribute('href')
                    if href and '/route/' in href:
                        mp_id_match = re.search(r'/route/(\d+)/', href)
                        if mp_id_match:
                            mp_route_id = mp_id_match.group(1)
                except:
                    pass

                # Extract date, style, notes from second line if available
                date = None
                style = None
                notes = None
                pitches = None

                if len(lines) > 1:
                    tick_info = lines[1]

                    # Date
                    date_match = re.search(r'([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})', tick_info)
                    if date_match:
                        date = date_match.group(1)

                    # Style
                    style_match = re.search(r'\b(Lead|Follow|TR|Solo|Top Rope|Aid|Send)\b', tick_info, re.IGNORECASE)
                    if style_match:
                        style = style_match.group(1)

                    # Pitches
                    pitches_match = re.search(r'(\d+)\s*pitches?', tick_info, re.IGNORECASE)
                    if pitches_match:
                        pitches = int(pitches_match.group(1))

                    # Notes (everything after style)
                    if style:
                        notes_start = tick_info.find(style) + len(style)
                        notes = tick_info[notes_start:].strip('. ')
                        if notes:
                            notes = notes[:500]

                # Check if route exists in our database
                route_key = route_name.lower()
                if route_key in routes_by_name:
                    route_id = routes_by_name[route_key]['route_id']
                else:
                    # Create new route
                    route_id = next_route_id
                    new_route = {
                        'route_id': route_id,
                        'name': route_name,
                        'mountain_id': None,
                        'mountain_name': None,
                        'grade': grade,
                        'grade_yds': None,
                        'length_ft': None,
                        'pitches': pitches,
                        'type': None,
                        'first_ascent_year': None,
                        'latitude': None,
                        'longitude': None,
                        'accident_count': 0,
                        'mp_route_id': mp_route_id
                    }
                    new_routes.append(new_route)
                    routes_by_name[route_key] = new_route
                    next_route_id += 1

                # Create ascent record
                ascent = {
                    'ascent_id': next_ascent_id,
                    'route_id': route_id,
                    'mp_route_id': mp_route_id,
                    'climber_id': climber_id,
                    'climber_username': username,
                    'date': date,
                    'style': style,
                    'pitches': pitches,
                    'notes': notes
                }
                new_ascents.append(ascent)
                next_ascent_id += 1

            except Exception as e:
                continue

        return new_ascents, new_routes, next_route_id, next_ascent_id

    except Exception as e:
        print(f"  Error scraping ticks: {e}")
        return [], [], next_route_id, next_ascent_id

def scrape_all_climbers(limit=None):
    """
    Main scraper function - processes all climbers in climbers table

    Args:
        limit: Optional limit on number of climbers to process (for testing)
    """
    print("\n" + "=" * 80)
    print("MOUNTAIN PROJECT CLIMBER PROFILE & TICK LIST SCRAPER")
    print("=" * 80)
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Load existing data
    climbers_df = pd.read_csv('data/tables/climbers.csv')
    routes_df = pd.read_csv('data/tables/routes.csv')
    ascents_df = pd.read_csv('data/tables/ascents.csv')

    print(f"Loaded {len(climbers_df)} climbers")
    print(f"Loaded {len(routes_df)} existing routes")
    print(f"Loaded {len(ascents_df)} existing ascents\n")

    # Prepare for resumable progress
    progress_file = 'data/tables/climbers_progress.txt'
    processed_climbers = set()

    if os.path.exists(progress_file):
        with open(progress_file, 'r') as f:
            processed_climbers = set(int(line.strip()) for line in f if line.strip().isdigit())
        print(f"Resuming: {len(processed_climbers)} climbers already processed\n")

    # Filter unprocessed climbers
    climbers_to_process = climbers_df[~climbers_df['climber_id'].isin(processed_climbers)]

    if limit:
        climbers_to_process = climbers_to_process.head(limit)

    print(f"Processing {len(climbers_to_process)} climbers...\n")

    # Setup driver
    driver = setup_driver()

    # Tracking variables
    next_route_id = routes_df['route_id'].max() + 1 if len(routes_df) > 0 else 1
    next_ascent_id = ascents_df['ascent_id'].max() + 1 if len(ascents_df) > 0 else 1

    all_new_routes = []
    all_new_ascents = []
    updated_climbers = []

    stats = {
        'profiles_found': 0,
        'profiles_not_found': 0,
        'total_new_routes': 0,
        'total_new_ascents': 0
    }

    try:
        # Process climbers with progress bar
        pbar = tqdm(climbers_to_process.iterrows(), total=len(climbers_to_process), desc="Scraping climbers")

        for idx, climber in pbar:
            climber_id = climber['climber_id']
            username = climber['username']

            pbar.set_description(f"Processing: {username[:30]}")

            # Skip if already processed
            if climber_id in processed_climbers:
                continue

            # Search for climber profile
            profile_url = search_climber(driver, username)

            if not profile_url:
                stats['profiles_not_found'] += 1
                # Mark as processed even if not found
                processed_climbers.add(climber_id)
                with open(progress_file, 'a') as f:
                    f.write(f"{climber_id}\n")
                continue

            stats['profiles_found'] += 1

            # Scrape profile data
            climber_data = scrape_climber_profile(driver, profile_url, climber_id, username)

            if climber_data and climber_data['mp_user_id']:
                updated_climbers.append(climber_data)

                # Scrape tick list
                existing_routes = routes_df.to_dict('records') + all_new_routes
                new_ascents, new_routes, next_route_id, next_ascent_id = scrape_climber_ticks(
                    driver, climber_data['mp_user_id'], climber_id, username,
                    existing_routes, next_route_id, next_ascent_id
                )

                if new_ascents:
                    all_new_ascents.extend(new_ascents)
                    all_new_routes.extend(new_routes)
                    stats['total_new_routes'] += len(new_routes)
                    stats['total_new_ascents'] += len(new_ascents)

                # Update progress bar with stats
                pbar.set_postfix({
                    'routes': stats['total_new_routes'],
                    'ascents': stats['total_new_ascents'],
                    'found': stats['profiles_found']
                })

            # Mark as processed
            processed_climbers.add(climber_id)
            with open(progress_file, 'a') as f:
                f.write(f"{climber_id}\n")

            # Save incrementally every 5 climbers
            if len(processed_climbers) % 5 == 0:
                save_progress(climbers_df, updated_climbers, all_new_routes, all_new_ascents,
                             routes_df, ascents_df)

            # Respectful delay
            time.sleep(2)

        pbar.close()

        # Final save
        save_progress(climbers_df, updated_climbers, all_new_routes, all_new_ascents,
                     routes_df, ascents_df)

        # Print final summary
        print_summary(stats, len(processed_climbers), all_new_routes, all_new_ascents)

    finally:
        driver.quit()

    print(f"\nFinished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

def save_progress(climbers_df, updated_climbers, new_routes, new_ascents, routes_df, ascents_df):
    """Save current progress to disk"""

    # Update climbers
    if updated_climbers:
        updated_df = pd.DataFrame(updated_climbers)
        climbers_df.update(updated_df.set_index('climber_id'))
        climbers_df.to_csv('data/tables/climbers.csv', index=False)

    # Add new routes
    if new_routes:
        new_routes_df = pd.DataFrame(new_routes)
        combined_routes = pd.concat([routes_df, new_routes_df], ignore_index=True)
        combined_routes.to_csv('data/tables/routes.csv', index=False)

    # Add new ascents
    if new_ascents:
        new_ascents_df = pd.DataFrame(new_ascents)
        combined_ascents = pd.concat([ascents_df, new_ascents_df], ignore_index=True)
        combined_ascents.to_csv('data/tables/ascents.csv', index=False)

def print_summary(stats, total_processed, new_routes, new_ascents):
    """Print final summary statistics"""
    print("\n" + "=" * 80)
    print("SCRAPING COMPLETE")
    print("=" * 80)
    print(f"\nClimbers processed: {total_processed}")
    print(f"  - Profiles found: {stats['profiles_found']}")
    print(f"  - Profiles not found: {stats['profiles_not_found']}")
    print(f"\nData collected:")
    print(f"  - New routes discovered: {stats['total_new_routes']}")
    print(f"  - New ascents recorded: {stats['total_new_ascents']}")

    if new_routes:
        routes_df = pd.read_csv('data/tables/routes.csv')
        print(f"\nTotal routes in database: {len(routes_df)}")

    if new_ascents:
        ascents_df = pd.read_csv('data/tables/ascents.csv')
        print(f"Total ascents in database: {len(ascents_df)}")

    print("\n" + "=" * 80)

if __name__ == '__main__':
    # Process all climbers (or set limit for testing)
    scrape_all_climbers()
