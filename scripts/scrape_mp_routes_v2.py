#!/usr/bin/env python3
"""
Mountain Project Route Scraper v2
=================================
Scrapes all climbing routes from Mountain Project for all 50 US states.

Strategy:
1. Start from homepage, collect all 50 state URLs
2. For each state, recursively navigate through area hierarchy
3. Collect all routes and build location hierarchy with parent_id

Output Files:
- data/mp_routes_v2.csv      - All routes
- data/mp_locations_v2.csv   - All locations with parent_id hierarchy

Usage:
    python scripts/scrape_mp_routes_v2.py [--state STATE] [--test]

    --state STATE   Scrape only one state (e.g., "Colorado")
    --test          Scrape only first 3 states for testing
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import json
import re
import argparse
from datetime import datetime
from pathlib import Path
import logging
from collections import deque

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
MP_BASE_URL = "https://www.mountainproject.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
REQUEST_DELAY = 1.0  # seconds between requests

# US States (for filtering)
US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California",
    "Colorado", "Connecticut", "Delaware", "Florida", "Georgia",
    "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland",
    "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri",
    "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
    "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming"
]


class MountainProjectScraper:
    def __init__(self, output_dir="data"):
        self.output_dir = Path(output_dir)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

        # Data storage
        self.locations = {}  # mp_id -> location info
        self.routes = {}     # mp_id -> route info

        # Progress tracking
        self.progress_file = self.output_dir / ".mp_scrape_progress_v2.json"
        self.completed_states = set()
        self.visited_areas = set()
        self._load_progress()

    def _load_progress(self):
        """Load progress from previous run."""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r') as f:
                    data = json.load(f)
                    self.completed_states = set(data.get("completed_states", []))
                    self.visited_areas = set(data.get("visited_areas", []))

                    # Load existing data
                    if "locations" in data:
                        self.locations = data["locations"]
                    if "routes" in data:
                        self.routes = data["routes"]

                    logger.info(f"Loaded progress: {len(self.completed_states)} states, "
                                f"{len(self.locations)} locations, {len(self.routes)} routes")
            except Exception as e:
                logger.warning(f"Could not load progress: {e}")

    def _save_progress(self):
        """Save progress for resume."""
        with open(self.progress_file, 'w') as f:
            json.dump({
                "completed_states": list(self.completed_states),
                "visited_areas": list(self.visited_areas),
                "locations": self.locations,
                "routes": self.routes,
                "last_updated": datetime.now().isoformat()
            }, f)

    def _request(self, url):
        """Make a rate-limited request."""
        time.sleep(REQUEST_DELAY)
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            logger.error(f"Request failed: {url} - {e}")
            return None

    def get_state_urls(self):
        """Get all US state URLs from the homepage."""
        logger.info("Fetching state URLs from homepage...")
        response = self._request(MP_BASE_URL)
        if not response:
            return {}

        soup = BeautifulSoup(response.text, 'html.parser')
        states_found = {}

        all_area_links = soup.find_all('a', href=re.compile(r'/area/\d+/'))

        for link in all_area_links:
            text = link.get_text(strip=True)
            href = link.get('href', '')

            for state in US_STATES:
                if text.lower() == state.lower() and state not in states_found:
                    match = re.search(r'/area/(\d+)/', href)
                    mp_id = match.group(1) if match else None
                    full_url = href if href.startswith('http') else f"{MP_BASE_URL}{href}"

                    states_found[state] = {
                        'name': state,
                        'url': full_url,
                        'mp_id': mp_id
                    }

                    # Also add to locations
                    self.locations[mp_id] = {
                        'mp_id': mp_id,
                        'name': state,
                        'parent_id': None,  # States have no parent
                        'url': full_url,
                        'latitude': None,
                        'longitude': None,
                    }
                    break

        logger.info(f"Found {len(states_found)} states")
        return states_found

    def parse_area_page(self, url, parent_id=None):
        """
        Parse an area page to extract:
        - Sub-areas (with their parent relationship)
        - Routes
        - Coordinates
        """
        response = self._request(url)
        if not response:
            return [], []

        soup = BeautifulSoup(response.text, 'html.parser')
        sub_areas = []
        routes = []

        # Extract area ID from URL
        area_id_match = re.search(r'/area/(\d+)/', url)
        area_id = area_id_match.group(1) if area_id_match else None

        # Extract coordinates from page
        lat, lon = self._extract_coordinates(soup)

        # Update location with coordinates if we have them
        if area_id and area_id in self.locations:
            if lat and lon:
                self.locations[area_id]['latitude'] = lat
                self.locations[area_id]['longitude'] = lon

        # Find sub-areas in left navigation
        left_nav = soup.select('.lef-nav-row')
        for item in left_nav:
            href = item.get('href', '')
            if not href:
                # Sometimes it's a nested link
                link = item.select_one('a[href*="/area/"]')
                if link:
                    href = link.get('href', '')

            if '/area/' in href:
                sub_url = href if href.startswith('http') else f"{MP_BASE_URL}{href}"
                sub_match = re.search(r'/area/(\d+)/', href)
                sub_id = sub_match.group(1) if sub_match else None

                # Get the name (clean it up - remove route counts)
                name_text = item.get_text(strip=True)
                # Remove trailing numbers like "0/1/2/0/0/0"
                name = re.sub(r'\d+(/\d+)+$', '', name_text).strip()

                if sub_id and sub_id not in self.locations:
                    sub_areas.append({
                        'mp_id': sub_id,
                        'name': name,
                        'url': sub_url,
                        'parent_id': area_id,
                    })

                    self.locations[sub_id] = {
                        'mp_id': sub_id,
                        'name': name,
                        'parent_id': area_id,
                        'url': sub_url,
                        'latitude': None,
                        'longitude': None,
                    }

        # Find routes on this page
        route_links = soup.select('a[href*="/route/"]')
        seen_route_ids = set()

        for link in route_links:
            href = link.get('href', '')
            route_match = re.search(r'/route/(\d+)/', href)
            if not route_match:
                continue

            route_id = route_match.group(1)
            if route_id in seen_route_ids or route_id in self.routes:
                continue
            seen_route_ids.add(route_id)

            route_url = href if href.startswith('http') else f"{MP_BASE_URL}{href}"
            raw_name = link.get_text(strip=True)

            # Clean up route name - remove concatenated type/pitch info
            route_name = self._clean_route_name(raw_name)

            # Try to extract grade from parent element
            grade, route_type, length_ft, pitches = self._extract_route_info(link)

            route_data = {
                'mp_route_id': route_id,
                'name': route_name,
                'url': route_url,
                'location_id': area_id,
                'grade': grade,
                'type': route_type,
                'length_ft': length_ft,
                'pitches': pitches,
                'latitude': None,
                'longitude': None,
            }

            routes.append(route_data)
            self.routes[route_id] = route_data

        return sub_areas, routes

    def _clean_route_name(self, raw_name):
        """
        Clean route name by removing concatenated type/pitch info.

        Examples:
        - "Exum RidgeTrad, Alpine 6 pitches" -> "Exum Ridge"
        - "Circus in the WindSport" -> "Circus in the Wind"
        - "V1-5-PG134●B.F.G.Boulder" -> "B.F.G."
        """
        name = raw_name

        # Remove everything before and including ● (grade conversion tables, length info)
        # Examples: "5.64c14V12S 4b2,064●Southeast Buttress" -> "Southeast Buttress"
        #           "V1-5-PG134●B.F.G.Boulder" -> "B.F.G.Boulder"
        if '●' in name:
            name = name.split('●', 1)[-1]

        # Remove trailing type info (Trad, Sport, Boulder, Ice, Aid, Alpine, Mixed, TR)
        # These appear directly concatenated to the name without space
        # Pattern: NameTrad or NameSport or NameTrad, Alpine 6 pitches
        type_pattern = r'(Trad|Sport|Boulder|Ice|Aid|Alpine|Mixed|TR)(?:,\s*(?:Trad|Sport|Boulder|Ice|Aid|Alpine|Mixed|TR))*(?:,?\s*\d+\s*pitche?s?)?$'
        name = re.sub(type_pattern, '', name, flags=re.IGNORECASE)

        # Clean up any remaining artifacts
        name = name.strip(' ,')

        # If name is empty after cleaning, use original
        if not name:
            name = raw_name

        return name

    def _extract_coordinates(self, soup):
        """Extract lat/lon from page."""
        lat, lon = None, None

        # Try to find in GPS table cell
        gps_cell = soup.find('td', string=re.compile(r'GPS:', re.I))
        if gps_cell:
            next_cell = gps_cell.find_next_sibling('td')
            if next_cell:
                text = next_cell.get_text()
                match = re.search(r'([-\d.]+),\s*([-\d.]+)', text)
                if match:
                    lat = float(match.group(1))
                    lon = float(match.group(2))
                    return lat, lon

        # Try script tags
        for script in soup.find_all('script'):
            if script.string:
                lat_match = re.search(r'"lat":\s*([-\d.]+)', script.string)
                lon_match = re.search(r'"lng":\s*([-\d.]+)', script.string)
                if lat_match and lon_match:
                    lat = float(lat_match.group(1))
                    lon = float(lon_match.group(1))
                    return lat, lon

        return lat, lon

    def _extract_route_info(self, link_element):
        """Extract route grade, type, length, pitches from context."""
        grade = ""
        route_type = "unknown"
        length_ft = None
        pitches = None

        # Look at parent row/container
        parent = link_element.find_parent('tr') or link_element.find_parent('div')
        if not parent:
            return grade, route_type, length_ft, pitches

        full_text = parent.get_text()

        # Extract grade - look for patterns like 5.10a, WI4, M7, A2, etc.
        grade_patterns = [
            r'(5\.\d+[a-d]?(?:/[a-d])?)',  # YDS: 5.10a, 5.11b/c
            r'(WI\d+\+?)',                   # Ice: WI4, WI5+
            r'(M\d+\+?)',                    # Mixed: M7, M8+
            r'(A[0-5])',                     # Aid: A0, A3
            r'(C[0-5])',                     # Clean aid: C2
            r'(V\d+)',                       # Bouldering: V5
        ]

        for pattern in grade_patterns:
            match = re.search(pattern, full_text)
            if match:
                grade = match.group(1)
                break

        # Determine type from grade or text
        text_lower = full_text.lower()
        if 'wi' in grade.lower():
            route_type = "ice"
        elif grade.lower().startswith('m') and re.match(r'm\d', grade.lower()):
            route_type = "mixed"
        elif grade.lower().startswith('a') or grade.lower().startswith('c'):
            route_type = "aid"
        elif grade.lower().startswith('v'):
            route_type = "boulder"
        elif 'sport' in text_lower:
            route_type = "sport"
        elif 'trad' in text_lower:
            route_type = "trad"
        elif 'tr' in text_lower or 'top rope' in text_lower or 'toprope' in text_lower:
            route_type = "toprope"
        elif 'alpine' in text_lower:
            route_type = "alpine"
        elif '5.' in grade:
            route_type = "rock"  # Generic rock, could be sport/trad

        # Extract length
        length_match = re.search(r'(\d+)\s*(?:ft|feet|\')', full_text, re.I)
        if length_match:
            length_ft = int(length_match.group(1))

        # Extract pitches
        pitch_match = re.search(r'(\d+)\s*pitch', full_text, re.I)
        if pitch_match:
            pitches = int(pitch_match.group(1))

        return grade, route_type, length_ft, pitches

    def scrape_state(self, state_name, state_url, state_id):
        """Scrape all areas and routes for a state using BFS."""
        logger.info(f"\n{'='*60}")
        logger.info(f"SCRAPING: {state_name}")
        logger.info(f"{'='*60}")

        # BFS through area hierarchy
        areas_to_visit = deque([(state_url, state_id)])
        areas_processed = 0

        while areas_to_visit:
            area_url, area_id = areas_to_visit.popleft()

            if area_url in self.visited_areas:
                continue
            self.visited_areas.add(area_url)

            sub_areas, routes = self.parse_area_page(area_url, area_id)

            # Add sub-areas to queue
            for sub in sub_areas:
                if sub['url'] not in self.visited_areas:
                    areas_to_visit.append((sub['url'], sub['mp_id']))

            areas_processed += 1

            if areas_processed % 50 == 0:
                logger.info(f"  Processed {areas_processed} areas, "
                            f"found {len(self.routes)} routes total, "
                            f"{len(areas_to_visit)} areas remaining")
                self._save_progress()

        logger.info(f"Completed {state_name}: {areas_processed} areas")

    def scrape_route_details(self, route_id):
        """Fetch detailed info for a specific route (lat/lon, length, etc.)."""
        if route_id not in self.routes:
            return

        route = self.routes[route_id]
        response = self._request(route['url'])
        if not response:
            return

        soup = BeautifulSoup(response.text, 'html.parser')

        # Get coordinates
        lat, lon = self._extract_coordinates(soup)
        if lat and lon:
            route['latitude'] = lat
            route['longitude'] = lon

        # Get additional details from the route page
        # Type from tags
        type_row = soup.find('td', string=re.compile(r'Type:', re.I))
        if type_row:
            type_cell = type_row.find_next_sibling('td')
            if type_cell:
                type_text = type_cell.get_text().lower()
                if 'sport' in type_text:
                    route['type'] = 'sport'
                elif 'trad' in type_text:
                    route['type'] = 'trad'
                elif 'ice' in type_text:
                    route['type'] = 'ice'
                elif 'aid' in type_text:
                    route['type'] = 'aid'
                elif 'mixed' in type_text:
                    route['type'] = 'mixed'
                elif 'alpine' in type_text:
                    route['type'] = 'alpine'
                elif 'boulder' in type_text:
                    route['type'] = 'boulder'

    def scrape_all(self, states=None, test_mode=False):
        """Main scraping function."""
        # Get state URLs
        state_urls = self.get_state_urls()

        if states:
            # Filter to specific states
            state_urls = {k: v for k, v in state_urls.items() if k in states}

        if test_mode:
            # Only first 3 states in test mode
            state_urls = dict(list(state_urls.items())[:3])
            logger.info(f"TEST MODE: Scraping {list(state_urls.keys())}")

        # Scrape each state
        for state_name, state_info in state_urls.items():
            if state_name in self.completed_states:
                logger.info(f"Skipping {state_name} (already completed)")
                continue

            self.scrape_state(state_name, state_info['url'], state_info['mp_id'])
            self.completed_states.add(state_name)
            self._save_progress()
            self.save_results()

        logger.info(f"\n{'='*60}")
        logger.info("SCRAPING COMPLETE")
        logger.info(f"Total locations: {len(self.locations)}")
        logger.info(f"Total routes: {len(self.routes)}")
        logger.info(f"{'='*60}")

    def save_results(self):
        """Save final results to CSV."""
        # Save locations
        locations_file = self.output_dir / "mp_locations_v2.csv"
        locations_list = list(self.locations.values())
        df_locations = pd.DataFrame(locations_list)
        df_locations.to_csv(locations_file, index=False)
        logger.info(f"Saved {len(df_locations)} locations to {locations_file}")

        # Save routes
        routes_file = self.output_dir / "mp_routes_v2.csv"
        routes_list = list(self.routes.values())
        df_routes = pd.DataFrame(routes_list)
        df_routes.to_csv(routes_file, index=False)
        logger.info(f"Saved {len(df_routes)} routes to {routes_file}")


def main():
    parser = argparse.ArgumentParser(description="Scrape Mountain Project routes")
    parser.add_argument("--state", type=str, help="Scrape only specific state(s), comma-separated")
    parser.add_argument("--test", action="store_true", help="Test mode (first 3 states)")
    parser.add_argument("--reset", action="store_true", help="Reset progress and start fresh")
    args = parser.parse_args()

    scraper = MountainProjectScraper()

    if args.reset:
        logger.info("Resetting progress...")
        scraper.completed_states = set()
        scraper.visited_areas = set()
        scraper.locations = {}
        scraper.routes = {}

    states = None
    if args.state:
        states = [s.strip() for s in args.state.split(',')]

    scraper.scrape_all(states=states, test_mode=args.test)


if __name__ == "__main__":
    main()
