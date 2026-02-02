"""Test climber scraper on small batch"""

from scrape_mp_climbers import scrape_all_climbers

if __name__ == '__main__':
    # Test with just 5 climbers
    print("Testing climber scraper with 5 climbers...")
    scrape_all_climbers(limit=5)
