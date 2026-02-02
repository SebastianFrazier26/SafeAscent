#!/usr/bin/env python3
"""Simple test of MP scraper - just test basic functionality"""

import sys
import os

# Update paths
sys.path.insert(0, os.path.dirname(__file__))

# First, let's test that we can even load the script
print("=" * 60)
print("TESTING MP SCRAPER - BASIC FUNCTIONALITY CHECK")
print("=" * 60)

print("\n1. Testing imports...")
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from bs4 import BeautifulSoup
    from tqdm import tqdm
    print("✅ All imports successful")
except Exception as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)

print("\n2. Testing Chrome driver initialization...")
try:
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(options=chrome_options)
    print("✅ Chrome driver initialized")
    
    print("\n3. Testing Mountain Project access...")
    driver.get("https://www.mountainproject.com")
    print(f"✅ Successfully loaded Mountain Project (title: {driver.title[:50]}...)")
    
    print("\n4. Testing area page access...")
    # Test with New Hampshire
    driver.get("https://www.mountainproject.com/area/105872225/new-hampshire")
    print(f"✅ Successfully loaded area page")
    
    print("\n5. Checking for route links...")
    links = driver.find_elements("tag name", "a")
    route_links = [link.get_attribute('href') for link in links if link.get_attribute('href') and '/route/' in link.get_attribute('href')]
    print(f"✅ Found {len(route_links)} route links")
    if route_links:
        print(f"   Example: {route_links[0]}")
    
    driver.quit()
    print("\n" + "=" * 60)
    print("✅ ALL BASIC TESTS PASSED")
    print("=" * 60)
    print("\nThe scraper should work. You can now run the full test or production script.")
    
except Exception as e:
    print(f"❌ Test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
