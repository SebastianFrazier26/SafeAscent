"""
Test Mountain Project tick scraper
Demonstrates what data we can extract from route ticks and climber profiles
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

def setup_driver():
    """Setup Chrome driver with appropriate options"""
    chrome_options = Options()
    chrome_options.add_argument('--headless')  # Run in background
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def generate_climber_id(username):
    """Generate unique climber ID from username"""
    return f"cl_{hashlib.md5(username.lower().encode()).hexdigest()[:8]}"

def generate_ascent_id(route_id, climber_id, date):
    """Generate unique ascent ID"""
    combined = f"{route_id}_{climber_id}_{date}"
    return f"as_{hashlib.md5(combined.encode()).hexdigest()[:8]}"

def scrape_route_ticks(driver, route_url, route_id, limit=10):
    """Scrape ticks/ascents from a route page"""
    print(f"\n{'=' * 80}")
    print(f"Scraping ticks from: {route_url}")
    print(f"{'=' * 80}")

    driver.get(route_url)
    time.sleep(3)  # Let page load

    ticks = []

    try:
        # Find the ticks section - MP uses table with class 'table table-striped'
        # Ticks are in rows with user info

        # Try to find tick rows - they're in a table
        tick_rows = driver.find_elements(By.CSS_SELECTOR, "tr[data-userid]")

        print(f"\nFound {len(tick_rows)} ticks on this route")
        print(f"Extracting data from first {min(limit, len(tick_rows))} ticks...\n")

        for i, row in enumerate(tick_rows[:limit]):
            try:
                # Extract climber username
                username_elem = row.find_element(By.CSS_SELECTOR, "a[href^='/user/']")
                username = username_elem.text.strip()

                # Extract date
                date_elem = row.find_element(By.CSS_SELECTOR, "td:nth-child(2)")
                date = date_elem.text.strip()

                # Extract style (Lead, Follow, TR, Solo, etc.)
                style_elem = row.find_element(By.CSS_SELECTOR, "td:nth-child(3)")
                style = style_elem.text.strip()

                # Extract rating (stars) if given
                rating = None
                try:
                    rating_elem = row.find_element(By.CSS_SELECTOR, "span.scoreStars")
                    # Count filled stars
                    stars = len(rating_elem.find_elements(By.CSS_SELECTOR, "img[alt='Star']"))
                    rating = stars
                except:
                    pass

                # Extract notes/comments - these are in expandable sections
                notes = None
                try:
                    # Look for comment icon or text
                    comment_elem = row.find_element(By.CSS_SELECTOR, "td:last-child")
                    notes_preview = comment_elem.text.strip()
                    if notes_preview:
                        notes = notes_preview
                except:
                    pass

                climber_id = generate_climber_id(username)
                ascent_id = generate_ascent_id(route_id, climber_id, date)

                tick_data = {
                    'ascent_id': ascent_id,
                    'route_id': route_id,
                    'climber_id': climber_id,
                    'climber_username': username,
                    'date': date,
                    'style': style,
                    'rating': rating,
                    'notes': notes
                }

                ticks.append(tick_data)

                # Print sample
                if i < 5:
                    print(f"Tick {i+1}:")
                    print(f"  Climber: {username} (ID: {climber_id})")
                    print(f"  Date: {date}")
                    print(f"  Style: {style}")
                    print(f"  Rating: {rating if rating else 'N/A'}")
                    print(f"  Notes: {notes[:100] if notes else 'No notes'}...")
                    print()

            except Exception as e:
                print(f"  Error parsing tick {i+1}: {e}")
                continue

        if len(tick_rows) > limit:
            print(f"... and {len(tick_rows) - limit} more ticks not shown\n")

    except Exception as e:
        print(f"Error finding ticks: {e}")

    return ticks

def scrape_climber_profile(driver, username, limit=5):
    """Scrape a climber's profile and tick list"""
    # MP user URLs are typically: /user/{userid}/{username}
    # We need to find the user first or construct the URL

    print(f"\n{'=' * 80}")
    print(f"Scraping climber profile: {username}")
    print(f"{'=' * 80}")

    # Search for the user
    search_url = f"https://www.mountainproject.com/search?q={username.replace(' ', '+')}"
    driver.get(search_url)
    time.sleep(2)

    try:
        # Find user profile link in search results
        user_link = driver.find_element(By.CSS_SELECTOR, f"a[href*='/user/'][href*='{username.lower().replace(' ', '-')}']")
        profile_url = user_link.get_attribute('href')

        print(f"\nFound profile: {profile_url}")

        # Visit profile
        driver.get(profile_url)
        time.sleep(2)

        climber_data = {
            'username': username,
            'climber_id': generate_climber_id(username),
            'location': None,
            'personal_info': None,
            'ticks': []
        }

        # Extract location if available
        try:
            location_elem = driver.find_element(By.CSS_SELECTOR, "td[contains(text(), 'Location:')]")
            location = location_elem.find_element(By.XPATH, "following-sibling::td").text.strip()
            climber_data['location'] = location
            print(f"Location: {location}")
        except:
            print("Location: Not found")

        # Navigate to ticks tab
        try:
            ticks_tab = driver.find_element(By.LINK_TEXT, "Ticks")
            ticks_tab.click()
            time.sleep(2)

            # Find tick entries
            tick_rows = driver.find_elements(By.CSS_SELECTOR, "tr[data-routeid]")

            print(f"\nTotal ticks by this climber: {len(tick_rows)}")
            print(f"Extracting first {min(limit, len(tick_rows))} ticks...\n")

            for i, row in enumerate(tick_rows[:limit]):
                try:
                    # Extract route name and link
                    route_link = row.find_element(By.CSS_SELECTOR, "a[href^='/route/']")
                    route_name = route_link.text.strip()
                    route_url = route_link.get_attribute('href')
                    route_id = route_url.split('/')[4]  # Extract route ID from URL

                    # Extract date
                    date = row.find_element(By.CSS_SELECTOR, "td:nth-child(1)").text.strip()

                    # Extract style
                    style = row.find_element(By.CSS_SELECTOR, "td:nth-child(3)").text.strip()

                    tick_info = {
                        'route_name': route_name,
                        'route_url': route_url,
                        'route_id': route_id,
                        'date': date,
                        'style': style
                    }

                    climber_data['ticks'].append(tick_info)

                    if i < 5:
                        print(f"Tick {i+1}: {route_name} ({date}) - {style}")
                        print(f"  URL: {route_url}")
                        print()

                except Exception as e:
                    print(f"  Error parsing tick {i+1}: {e}")
                    continue

            if len(tick_rows) > limit:
                print(f"... and {len(tick_rows) - limit} more ticks\n")

        except Exception as e:
            print(f"Error accessing ticks: {e}")

        return climber_data

    except Exception as e:
        print(f"Error finding user profile: {e}")
        return None

def test_full_workflow():
    """Test the complete workflow: route ticks → climbers → climber tick lists"""
    print("=" * 80)
    print("MOUNTAIN PROJECT COMPREHENSIVE TICK SCRAPER - TEST")
    print("=" * 80)
    print("\nThis test demonstrates the full data extraction workflow:")
    print("1. Scrape route ticks (ascents)")
    print("2. Identify unique climbers")
    print("3. Scrape climber profiles and full tick lists")
    print("4. Discover new routes from climber tick lists")

    driver = setup_driver()

    try:
        # Test with a well-known route: Snake Dike on Half Dome
        route_url = "https://www.mountainproject.com/route/105833381/snake-dike"
        route_id = "rt_test001"

        # Step 1: Scrape route ticks
        ticks = scrape_route_ticks(driver, route_url, route_id, limit=10)

        print(f"\n{'=' * 80}")
        print(f"RESULTS: Found {len(ticks)} ticks")
        print(f"{'=' * 80}")

        if ticks:
            # Step 2: Pick first climber and scrape their profile
            first_climber = ticks[0]['climber_username']
            climber_data = scrape_climber_profile(driver, first_climber, limit=5)

            if climber_data:
                print(f"\n{'=' * 80}")
                print(f"CLIMBER PROFILE RESULTS")
                print(f"{'=' * 80}")
                print(f"Username: {climber_data['username']}")
                print(f"Climber ID: {climber_data['climber_id']}")
                print(f"Location: {climber_data['location']}")
                print(f"Total routes climbed: {len(climber_data['ticks'])}")

                # Show new routes discovered
                print(f"\nNew routes discovered from this climber's tick list:")
                for tick in climber_data['ticks'][:5]:
                    print(f"  - {tick['route_name']} (ID: {tick['route_id']})")

        print(f"\n{'=' * 80}")
        print("TEST COMPLETE!")
        print(f"{'=' * 80}")
        print("\nThis demonstrates the full workflow. The production scraper will:")
        print("  1. Process all 622 routes in our database")
        print("  2. Extract ALL ticks (not just 10)")
        print("  3. Build a queue of unique climbers")
        print("  4. Process each climber's full tick list")
        print("  5. Add newly discovered routes to our routes table")
        print("  6. Continue until all climbers and routes are processed")
        print(f"\nEstimated final dataset size:")
        print(f"  Routes: 10,000-50,000+")
        print(f"  Climbers: 5,000-20,000+")
        print(f"  Ascents: 100,000-500,000+")

    finally:
        driver.quit()

if __name__ == '__main__':
    test_full_workflow()
