#!/usr/bin/env python3
"""
Test version of MP scraper - scrapes only New Hampshire (smallest state in the list)
and limits to first 50 routes for validation
"""
import sys
sys.path.insert(0, '/Users/sebastianfrazier/SafeAscent/scripts')

# Import the main scraper
import importlib.util
spec = importlib.util.spec_from_file_location("scraper", "/Users/sebastianfrazier/SafeAscent/scripts/scrape_mp_routes_comprehensive.py")
scraper_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scraper_module)

# Modify configuration for testing
scraper_module.US_STATE_AREA_IDS = {'New Hampshire': 105872225}  # Only NH
scraper_module.OUTPUT_FILE = "/Users/sebastianfrazier/SafeAscent/data/mp_routes_test.csv"
scraper_module.PROGRESS_FILE = "/Users/sebastianfrazier/SafeAscent/data/mp_scraping_test_progress.json"
scraper_module.ERROR_LOG_FILE = "/Users/sebastianfrazier/SafeAscent/data/mp_scraping_test_errors.log"

class TestMPRouteScraper(scraper_module.MPRouteScraper):
    """Test version with route limit"""
    def __init__(self, max_routes=50):
        super().__init__()
        self.max_routes = max_routes
        self.routes_count = 0
    
    def _extract_route_details(self, url):
        if self.routes_count >= self.max_routes:
            self.logger.info(f"Reached max routes limit ({self.max_routes}), stopping")
            return None
        result = super()._extract_route_details(url)
        if result:
            self.routes_count += 1
        return result

if __name__ == '__main__':
    print("=" * 60)
    print("MP ROUTE SCRAPER - TEST MODE")
    print("=" * 60)
    print(f"State: New Hampshire only")
    print(f"Max routes: 50")
    print(f"Output: mp_routes_test.csv")
    print("=" * 60)
    
    scraper = TestMPRouteScraper(max_routes=50)
    try:
        scraper.scrape_all_states()
        scraper._print_summary()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        scraper._save_progress()
        scraper._save_routes_to_csv()
    except Exception as e:
        print(f"\n\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        scraper.cleanup()
        print("\nTest files created:")
        print(f"  - {scraper_module.OUTPUT_FILE}")
        print(f"  - {scraper_module.ERROR_LOG_FILE}")
