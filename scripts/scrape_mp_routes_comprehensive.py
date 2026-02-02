#!/usr/bin/env python3
"""
Comprehensive Mountain Project Route Scraper for US Routes

This script recursively traverses Mountain Project area pages starting from US state areas,
extracts all routes with detailed information, and saves incrementally to CSV.

Features:
- Recursive area traversal
- JSON-LD structured data extraction
- Rate limiting and respectful scraping
- Incremental saving and resume capability
- Progress tracking with tqdm
- Comprehensive error handling and logging
- US routes only

Author: Claude Sonnet 4.5
Date: 2026-01-31
"""

import os
import sys
import time
import json
import csv
import logging
import re
from datetime import datetime
from typing import Dict, List, Set, Optional, Tuple
from urllib.parse import urljoin, urlparse, parse_qs
import random

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from bs4 import BeautifulSoup
from tqdm import tqdm


# Configuration
BASE_URL = "https://www.mountainproject.com"
OUTPUT_DIR = "/Users/sebastianfrazier/SafeAscent/data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "mp_routes_scraped.csv")
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "mp_scraping_progress.json")
ERROR_LOG_FILE = os.path.join(OUTPUT_DIR, "mp_scraping_errors.log")

# Rate limiting: 2-3 seconds between requests
MIN_DELAY = 2.0
MAX_DELAY = 3.0

# Save every N routes
SAVE_INTERVAL = 100

# US State Area IDs (starting points)
US_STATE_AREA_IDS = {
    'California': 105708959,
    'Colorado': 105708956,
    'Washington': 105708945,
    'Utah': 105708961,
    'Wyoming': 105708958,
    'New York': 105800424,
    'North Carolina': 105873282,
    'New Hampshire': 105872225,
    'Arizona': 105708962,
    'Nevada': 105708964,
    'Oregon': 105708943,
    'Texas': 105708953,
}

# CSV column headers
CSV_HEADERS = [
    'mp_route_id',
    'name',
    'grade_yds',
    'grade',
    'type',
    'latitude',
    'longitude',
    'elevation_ft',
    'pitches',
    'length_ft',
    'description',
    'protection_rating',
    'commitment_grade',
    'first_ascent',
    'area_name',
    'state',
    'url'
]


class MPRouteScraper:
    """Comprehensive Mountain Project route scraper with resume capability."""
    
    def __init__(self):
        """Initialize the scraper with driver, progress tracking, and logging."""
        self.driver = None
        self.routes_scraped: List[Dict] = []
        self.visited_areas: Set[int] = set()
        self.visited_routes: Set[int] = set()
        self.current_state: Optional[str] = None
        self.stats = {
            'areas_visited': 0,
            'routes_found': 0,
            'routes_saved': 0,
            'errors': 0,
            'start_time': None,
            'last_save_time': None
        }
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(ERROR_LOG_FILE),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Initialize driver
        self._init_driver()
        
        # Load progress if exists
        self._load_progress()
    
    def _init_driver(self):
        """Initialize Selenium Chrome driver in headless mode."""
        try:
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.set_page_load_timeout(30)
            self.logger.info("Chrome driver initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize Chrome driver: {e}")
            raise
    
    def _load_progress(self):
        """Load progress from previous run if exists."""
        if os.path.exists(PROGRESS_FILE):
            try:
                with open(PROGRESS_FILE, 'r') as f:
                    progress = json.load(f)
                    self.visited_areas = set(progress.get('visited_areas', []))
                    self.visited_routes = set(progress.get('visited_routes', []))
                    self.stats.update(progress.get('stats', {}))
                    self.logger.info(f"Resumed from previous session: {len(self.visited_areas)} areas, {len(self.visited_routes)} routes")
            except Exception as e:
                self.logger.warning(f"Could not load progress file: {e}")
        
        # Load existing CSV data
        if os.path.exists(OUTPUT_FILE):
            try:
                with open(OUTPUT_FILE, 'r', newline='', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        self.routes_scraped.append(row)
                        if row.get('mp_route_id'):
                            self.visited_routes.add(int(row['mp_route_id']))
                self.logger.info(f"Loaded {len(self.routes_scraped)} existing routes from CSV")
            except Exception as e:
                self.logger.warning(f"Could not load existing CSV: {e}")
    
    def _save_progress(self):
        """Save current progress to allow resuming."""
        try:
            progress = {
                'visited_areas': list(self.visited_areas),
                'visited_routes': list(self.visited_routes),
                'stats': self.stats,
                'last_updated': datetime.now().isoformat()
            }
            with open(PROGRESS_FILE, 'w') as f:
                json.dump(progress, f, indent=2)
            self.logger.debug("Progress saved")
        except Exception as e:
            self.logger.error(f"Failed to save progress: {e}")
    
    def _save_routes_to_csv(self):
        """Save all routes to CSV file."""
        try:
            # Ensure output directory exists
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            
            # Write to CSV
            with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
                writer.writeheader()
                writer.writerows(self.routes_scraped)
            
            self.stats['routes_saved'] = len(self.routes_scraped)
            self.stats['last_save_time'] = datetime.now().isoformat()
            self.logger.info(f"Saved {len(self.routes_scraped)} routes to {OUTPUT_FILE}")
        except Exception as e:
            self.logger.error(f"Failed to save routes to CSV: {e}")
    
    def _rate_limit(self):
        """Implement rate limiting between requests."""
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        time.sleep(delay)
    
    def _extract_id_from_url(self, url: str) -> Optional[int]:
        """Extract Mountain Project ID from URL."""
        try:
            # URL format: /route/ROUTE_ID/route-name
            # or /area/AREA_ID/area-name
            match = re.search(r'/(?:route|area)/(\d+)/', url)
            if match:
                return int(match.group(1))
        except Exception as e:
            self.logger.debug(f"Could not extract ID from URL {url}: {e}")
        return None
    
    def _parse_json_ld(self, soup: BeautifulSoup) -> Optional[Dict]:
        """Extract JSON-LD structured data from page."""
        try:
            script_tags = soup.find_all('script', type='application/ld+json')
            for script in script_tags:
                try:
                    data = json.loads(script.string)
                    # Look for schema that contains route/place information
                    if isinstance(data, dict) and '@type' in data:
                        return data
                    elif isinstance(data, list):
                        # Sometimes it's an array of schemas
                        for item in data:
                            if isinstance(item, dict) and '@type' in item:
                                return item
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            self.logger.debug(f"Error parsing JSON-LD: {e}")
        return None
    
    def _extract_coordinates(self, soup: BeautifulSoup, json_ld: Optional[Dict]) -> Tuple[Optional[float], Optional[float]]:
        """Extract latitude and longitude from page."""
        lat, lon = None, None
        
        # Try JSON-LD first
        if json_ld:
            try:
                if 'geo' in json_ld:
                    geo = json_ld['geo']
                    lat = float(geo.get('latitude', 0)) or None
                    lon = float(geo.get('longitude', 0)) or None
                elif 'location' in json_ld and isinstance(json_ld['location'], dict):
                    geo = json_ld['location'].get('geo', {})
                    lat = float(geo.get('latitude', 0)) or None
                    lon = float(geo.get('longitude', 0)) or None
            except (ValueError, TypeError, KeyError) as e:
                self.logger.debug(f"Could not extract coordinates from JSON-LD: {e}")
        
        # Try to find in page content if not in JSON-LD
        if lat is None or lon is None:
            try:
                # Look for coordinate display
                coord_div = soup.find('div', class_='coordinate-text')
                if coord_div:
                    coord_text = coord_div.get_text()
                    coord_match = re.search(r'(-?\d+\.\d+),\s*(-?\d+\.\d+)', coord_text)
                    if coord_match:
                        lat = float(coord_match.group(1))
                        lon = float(coord_match.group(2))
            except Exception as e:
                self.logger.debug(f"Could not extract coordinates from page: {e}")
        
        return lat, lon
    
    def _parse_grade(self, grade_text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Parse grade string to extract YDS grade, protection rating, and commitment grade.
        
        Examples:
        - "5.10a" -> ("5.10a", None, None)
        - "5.10a PG13" -> ("5.10a", "PG13", None)
        - "5.10a R" -> ("5.10a", "R", None)
        - "IV 5.10a" -> ("5.10a", None, "IV")
        - "V 5.12c R" -> ("5.12c", "R", "V")
        """
        if not grade_text:
            return None, None, None
        
        grade_yds = None
        protection_rating = None
        commitment_grade = None
        
        # Extract YDS grade (5.x format)
        yds_match = re.search(r'5\.\d+[a-d]?(?:/\d+[a-d]?)?', grade_text)
        if yds_match:
            grade_yds = yds_match.group(0)
        
        # Extract protection rating (R, X, PG13, etc.)
        if 'PG13' in grade_text or 'PG-13' in grade_text:
            protection_rating = 'PG13'
        elif ' R' in grade_text or grade_text.endswith('R'):
            protection_rating = 'R'
        elif ' X' in grade_text or grade_text.endswith('X'):
            protection_rating = 'X'
        
        # Extract commitment grade (Roman numerals I-VII)
        commitment_match = re.search(r'\b(I{1,3}|IV|V|VI|VII)\b', grade_text)
        if commitment_match:
            commitment_grade = commitment_match.group(1)
        
        return grade_yds, protection_rating, commitment_grade
    
    def _extract_route_details(self, url: str) -> Optional[Dict]:
        """Extract detailed route information from route page."""
        try:
            self._rate_limit()
            self.driver.get(url)
            
            # Wait for page to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            json_ld = self._parse_json_ld(soup)
            
            # Extract route ID from URL
            route_id = self._extract_id_from_url(url)
            if not route_id:
                self.logger.warning(f"Could not extract route ID from URL: {url}")
                return None
            
            # Check if already visited
            if route_id in self.visited_routes:
                self.logger.debug(f"Route {route_id} already visited, skipping")
                return None
            
            route_data = {
                'mp_route_id': route_id,
                'url': url,
                'state': self.current_state
            }
            
            # Extract name
            try:
                name_elem = soup.find('h1')
                if name_elem:
                    route_data['name'] = name_elem.get_text(strip=True)
                elif json_ld and 'name' in json_ld:
                    route_data['name'] = json_ld['name']
            except Exception as e:
                self.logger.debug(f"Could not extract name: {e}")
            
            # Extract grade
            try:
                grade_elem = soup.find('span', class_='rateYDS')
                if grade_elem:
                    grade_text = grade_elem.get_text(strip=True)
                    route_data['grade'] = grade_text
                    
                    # Parse grade components
                    grade_yds, protection, commitment = self._parse_grade(grade_text)
                    route_data['grade_yds'] = grade_yds
                    route_data['protection_rating'] = protection
                    route_data['commitment_grade'] = commitment
            except Exception as e:
                self.logger.debug(f"Could not extract grade: {e}")
            
            # Extract type (sport, trad, boulder, etc.)
            try:
                type_elem = soup.find('span', class_='small')
                if type_elem:
                    type_text = type_elem.get_text(strip=True)
                    # Parse out the type (usually first word)
                    types = []
                    if 'Sport' in type_text:
                        types.append('Sport')
                    if 'Trad' in type_text:
                        types.append('Trad')
                    if 'Boulder' in type_text:
                        types.append('Boulder')
                    if 'TR' in type_text or 'Top Rope' in type_text:
                        types.append('TR')
                    if 'Alpine' in type_text:
                        types.append('Alpine')
                    if 'Ice' in type_text:
                        types.append('Ice')
                    if 'Mixed' in type_text:
                        types.append('Mixed')
                    if 'Aid' in type_text:
                        types.append('Aid')
                    
                    route_data['type'] = ', '.join(types) if types else type_text
            except Exception as e:
                self.logger.debug(f"Could not extract type: {e}")
            
            # Extract coordinates
            lat, lon = self._extract_coordinates(soup, json_ld)
            route_data['latitude'] = lat
            route_data['longitude'] = lon
            
            # Extract detailed route information from description table
            try:
                description_table = soup.find('table', class_='description-details')
                if description_table:
                    rows = description_table.find_all('tr')
                    for row in rows:
                        cells = row.find_all('td')
                        if len(cells) >= 2:
                            label = cells[0].get_text(strip=True).lower()
                            value = cells[1].get_text(strip=True)
                            
                            if 'length' in label:
                                # Extract numeric length in feet
                                length_match = re.search(r'(\d+)\s*ft', value)
                                if length_match:
                                    route_data['length_ft'] = int(length_match.group(1))
                            
                            elif 'pitch' in label:
                                pitch_match = re.search(r'(\d+)', value)
                                if pitch_match:
                                    route_data['pitches'] = int(pitch_match.group(1))
                            
                            elif 'elevation' in label:
                                elev_match = re.search(r'([\d,]+)\s*ft', value)
                                if elev_match:
                                    route_data['elevation_ft'] = int(elev_match.group(1).replace(',', ''))
                            
                            elif 'fa' in label or 'first ascent' in label:
                                route_data['first_ascent'] = value
            except Exception as e:
                self.logger.debug(f"Could not extract route details from table: {e}")
            
            # Extract description
            try:
                desc_div = soup.find('div', class_='fr-view')
                if desc_div:
                    # Get text content, clean up whitespace
                    description = desc_div.get_text(separator=' ', strip=True)
                    # Limit length to avoid huge descriptions
                    if len(description) > 5000:
                        description = description[:5000] + '...'
                    route_data['description'] = description
            except Exception as e:
                self.logger.debug(f"Could not extract description: {e}")
            
            # Extract area/location breadcrumb
            try:
                breadcrumb = soup.find('div', class_='mb-half small text-warm')
                if breadcrumb:
                    links = breadcrumb.find_all('a')
                    if links:
                        # Last link before route is usually the parent area
                        route_data['area_name'] = links[-1].get_text(strip=True)
            except Exception as e:
                self.logger.debug(f"Could not extract area name: {e}")
            
            # Mark as visited
            self.visited_routes.add(route_id)
            self.stats['routes_found'] += 1
            
            return route_data
            
        except TimeoutException:
            self.logger.error(f"Timeout loading route page: {url}")
            self.stats['errors'] += 1
        except Exception as e:
            self.logger.error(f"Error extracting route details from {url}: {e}")
            self.stats['errors'] += 1
        
        return None
    
    def _extract_routes_from_area(self, area_url: str) -> List[str]:
        """Extract all route URLs from an area page."""
        route_urls = []
        
        try:
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            # Find all route links
            # Routes are typically in a table with class 'route-table' or similar
            route_links = soup.find_all('a', href=re.compile(r'/route/\d+/'))
            
            for link in route_links:
                href = link.get('href')
                if href:
                    full_url = urljoin(BASE_URL, href)
                    route_urls.append(full_url)
            
            # Remove duplicates while preserving order
            route_urls = list(dict.fromkeys(route_urls))
            
        except Exception as e:
            self.logger.error(f"Error extracting routes from area {area_url}: {e}")
            self.stats['errors'] += 1
        
        return route_urls
    
    def _extract_subareas(self, area_url: str) -> List[Tuple[int, str]]:
        """Extract sub-area IDs and URLs from an area page."""
        subareas = []
        
        try:
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            # Find all sub-area links
            # Sub-areas are typically in divs with specific classes
            area_links = soup.find_all('a', href=re.compile(r'/area/\d+/'))
            
            for link in area_links:
                href = link.get('href')
                if href:
                    area_id = self._extract_id_from_url(href)
                    if area_id and area_id not in self.visited_areas:
                        full_url = urljoin(BASE_URL, href)
                        subareas.append((area_id, full_url))
            
            # Remove duplicates
            subareas = list(dict.fromkeys(subareas))
            
        except Exception as e:
            self.logger.error(f"Error extracting sub-areas from {area_url}: {e}")
            self.stats['errors'] += 1
        
        return subareas
    
    def _process_area(self, area_id: int, area_url: str, progress_bar: tqdm):
        """Process a single area: extract routes and recurse into sub-areas."""
        # Check if already visited
        if area_id in self.visited_areas:
            return
        
        try:
            self._rate_limit()
            self.driver.get(area_url)
            
            # Wait for page to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # Mark area as visited
            self.visited_areas.add(area_id)
            self.stats['areas_visited'] += 1
            
            # Extract routes from this area
            route_urls = self._extract_routes_from_area(area_url)
            
            for route_url in route_urls:
                route_data = self._extract_route_details(route_url)
                
                if route_data:
                    self.routes_scraped.append(route_data)
                    progress_bar.update(1)
                    progress_bar.set_description(f"Routes: {len(self.routes_scraped)}, Areas: {len(self.visited_areas)}")
                    
                    # Save incrementally
                    if len(self.routes_scraped) % SAVE_INTERVAL == 0:
                        self._save_routes_to_csv()
                        self._save_progress()
            
            # Extract and process sub-areas
            subareas = self._extract_subareas(area_url)
            
            for subarea_id, subarea_url in subareas:
                self._process_area(subarea_id, subarea_url, progress_bar)
            
        except TimeoutException:
            self.logger.error(f"Timeout loading area page: {area_url}")
            self.stats['errors'] += 1
        except Exception as e:
            self.logger.error(f"Error processing area {area_id} ({area_url}): {e}")
            self.stats['errors'] += 1
    
    def scrape_all_states(self):
        """Main method to scrape all US state areas."""
        self.stats['start_time'] = datetime.now().isoformat()
        self.logger.info("Starting comprehensive Mountain Project scraping...")
        self.logger.info(f"Starting with {len(US_STATE_AREA_IDS)} US states")
        
        # Create progress bar
        with tqdm(total=0, desc="Scraping routes", unit="route") as progress_bar:
            for state_name, area_id in US_STATE_AREA_IDS.items():
                self.current_state = state_name
                self.logger.info(f"\n{'='*60}")
                self.logger.info(f"Processing state: {state_name} (ID: {area_id})")
                self.logger.info(f"{'='*60}")
                
                area_url = f"{BASE_URL}/area/{area_id}/"
                
                try:
                    self._process_area(area_id, area_url, progress_bar)
                except Exception as e:
                    self.logger.error(f"Error processing state {state_name}: {e}")
                    self.stats['errors'] += 1
                
                # Save after each state
                self._save_routes_to_csv()
                self._save_progress()
                
                self.logger.info(f"Completed {state_name}: {len(self.routes_scraped)} total routes")
        
        # Final save
        self._save_routes_to_csv()
        self._save_progress()
        
        # Print summary
        self._print_summary()
    
    def _print_summary(self):
        """Print scraping summary statistics."""
        end_time = datetime.now()
        start_time = datetime.fromisoformat(self.stats['start_time'])
        duration = end_time - start_time
        
        print("\n" + "="*60)
        print("SCRAPING SUMMARY")
        print("="*60)
        print(f"Total areas visited: {len(self.visited_areas)}")
        print(f"Total routes found: {len(self.routes_scraped)}")
        print(f"Total errors: {self.stats['errors']}")
        print(f"Duration: {duration}")
        print(f"Output file: {OUTPUT_FILE}")
        print(f"Progress file: {PROGRESS_FILE}")
        print(f"Error log: {ERROR_LOG_FILE}")
        print("="*60)
    
    def cleanup(self):
        """Clean up resources."""
        if self.driver:
            try:
                self.driver.quit()
                self.logger.info("Driver closed successfully")
            except Exception as e:
                self.logger.error(f"Error closing driver: {e}")


def main():
    """Main entry point."""
    scraper = None
    try:
        scraper = MPRouteScraper()
        scraper.scrape_all_states()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Saving progress...")
        if scraper:
            scraper._save_routes_to_csv()
            scraper._save_progress()
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
    finally:
        if scraper:
            scraper.cleanup()


if __name__ == "__main__":
    main()

