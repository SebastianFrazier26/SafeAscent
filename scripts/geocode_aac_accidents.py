#!/usr/bin/env python3
"""
Geocode AAC Accident Locations
==============================
Converts state + location_name to lat/lon coordinates.

Primary: Google Geocoding API
Fallback: Gemini API for location interpretation

Input: data/processed_aac_accidents.csv
Output: data/processed_aac_accidents.csv (updated with lat/lon)

Environment Variables:
- GOOGLE_MAPS_API_KEY: Google Geocoding API key
- GEMINI_API_KEY: Fallback for ambiguous locations

Usage:
    python scripts/geocode_aac_accidents.py [--limit N] [--resume]
"""

import pandas as pd
import json
import time
import argparse
import os
import requests
from pathlib import Path
from datetime import datetime
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data/aac_geocoding.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Rate limiting
GOOGLE_DELAY = 0.1  # 10 requests/second limit for Google
GEMINI_DELAY = 2.0  # Conservative for Gemini

# Google Geocoding API
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def geocode_google(location_query, api_key):
    """Geocode using Google Maps Geocoding API."""
    try:
        params = {
            'address': location_query,
            'key': api_key,
            'components': 'country:US'  # Bias towards US results
        }

        response = requests.get(GOOGLE_GEOCODE_URL, params=params, timeout=10)
        data = response.json()

        if data.get('status') == 'OK' and data.get('results'):
            result = data['results'][0]
            location = result['geometry']['location']

            return {
                'latitude': location['lat'],
                'longitude': location['lng'],
                'formatted_address': result.get('formatted_address'),
                'source': 'google',
                'confidence': 'high' if result['geometry'].get('location_type') in ['ROOFTOP', 'GEOMETRIC_CENTER'] else 'medium'
            }
        elif data.get('status') == 'ZERO_RESULTS':
            return None
        else:
            logger.warning(f"Google API error: {data.get('status')} - {data.get('error_message', '')}")
            return None

    except Exception as e:
        logger.error(f"Google geocoding error: {e}")
        return None


def geocode_gemini(location_name, state, api_key):
    """Use Gemini to interpret ambiguous climbing locations and return coordinates."""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.0-flash')

        prompt = f"""You are a climbing geography expert. Given this climbing location, provide the approximate GPS coordinates.

Location: {location_name}
State/Region: {state if state else 'Unknown (likely US)'}

This is from a climbing accident report. The location might be:
- A well-known climbing area (Yosemite, Joshua Tree, Red Rocks, etc.)
- A specific crag or wall
- A mountain or peak
- A national park or wilderness area

Respond with ONLY a JSON object (no markdown, no explanation):
{{
    "latitude": <decimal latitude>,
    "longitude": <decimal longitude>,
    "interpreted_location": "<your interpretation of where this is>",
    "confidence": "<high/medium/low>"
}}

If you cannot determine coordinates with reasonable confidence, respond:
{{"error": "Unable to geocode"}}
"""

        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.1,
                max_output_tokens=256,
            )
        )

        response_text = response.text.strip()
        # Clean markdown if present
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
        response_text = response_text.strip()

        data = json.loads(response_text)

        if 'error' in data:
            return None

        return {
            'latitude': float(data['latitude']),
            'longitude': float(data['longitude']),
            'formatted_address': data.get('interpreted_location'),
            'source': 'gemini',
            'confidence': data.get('confidence', 'medium')
        }

    except Exception as e:
        logger.error(f"Gemini geocoding error: {e}")
        return None


def build_geocode_query(row):
    """Build a search query from AAC accident data."""
    parts = []

    location_name = row.get('location_name')
    state = row.get('state')
    route_name = row.get('route_name')

    if location_name and pd.notna(location_name):
        parts.append(str(location_name))

    if state and pd.notna(state):
        parts.append(str(state))
    else:
        parts.append('USA')  # Default to USA

    return ', '.join(parts)


def geocode_aac_accidents(input_file, limit=None, resume=False):
    """Main geocoding function."""

    # Check for API keys
    google_key = os.environ.get('GOOGLE_MAPS_API_KEY')
    gemini_key = os.environ.get('GEMINI_API_KEY')

    if not google_key:
        logger.error("GOOGLE_MAPS_API_KEY not set!")
        logger.info("Set it with: export GOOGLE_MAPS_API_KEY=your_key")
        logger.info("Get a key at: https://console.cloud.google.com/apis/credentials")

        if not gemini_key:
            logger.error("Neither GOOGLE_MAPS_API_KEY nor GEMINI_API_KEY is set. Cannot proceed.")
            exit(1)
        else:
            logger.warning("Falling back to Gemini-only geocoding (slower, less accurate)")

    # Load data
    logger.info(f"Loading {input_file}")
    df = pd.read_csv(input_file)
    logger.info(f"Loaded {len(df)} records")

    # Progress tracking
    progress_file = Path(input_file).with_suffix('.geocode_progress.json')
    geocoded = {}

    if resume and progress_file.exists():
        with open(progress_file) as f:
            progress = json.load(f)
            geocoded = progress.get('geocoded', {})
        logger.info(f"Resuming: {len(geocoded)} already geocoded")

    # Filter records needing geocoding
    needs_geocoding = df[
        (df['latitude'].isna()) &
        (df['location_name'].notna()) &
        (~df['source_id'].astype(str).isin(geocoded.keys()))
    ]

    if limit:
        needs_geocoding = needs_geocoding.head(limit)

    logger.info(f"Geocoding {len(needs_geocoding)} records")

    # Stats
    google_success = 0
    gemini_success = 0
    failed = 0

    try:
        for idx, (_, row) in enumerate(needs_geocoding.iterrows()):
            source_id = str(row['source_id'])
            query = build_geocode_query(row)

            result = None

            # Try Google first
            if google_key:
                result = geocode_google(query, google_key)
                if result:
                    google_success += 1
                time.sleep(GOOGLE_DELAY)

            # Fallback to Gemini
            if not result and gemini_key:
                result = geocode_gemini(
                    row.get('location_name', ''),
                    row.get('state', ''),
                    gemini_key
                )
                if result:
                    gemini_success += 1
                time.sleep(GEMINI_DELAY)

            if result:
                geocoded[source_id] = result
            else:
                failed += 1
                geocoded[source_id] = {'error': 'Failed to geocode'}

            # Progress logging
            if (idx + 1) % 50 == 0:
                logger.info(f"Progress: {idx + 1}/{len(needs_geocoding)} | Google: {google_success} | Gemini: {gemini_success} | Failed: {failed}")

                # Save progress
                with open(progress_file, 'w') as f:
                    json.dump({
                        'geocoded': geocoded,
                        'last_updated': datetime.now().isoformat()
                    }, f)

    except KeyboardInterrupt:
        logger.info("Interrupted! Saving progress...")

    # Save final progress
    with open(progress_file, 'w') as f:
        json.dump({
            'geocoded': geocoded,
            'last_updated': datetime.now().isoformat()
        }, f)

    # Apply geocoding results to dataframe
    logger.info("Applying geocoding results to dataframe...")

    for source_id, geo_result in geocoded.items():
        if 'error' not in geo_result:
            mask = df['source_id'].astype(str) == source_id
            df.loc[mask, 'latitude'] = geo_result['latitude']
            df.loc[mask, 'longitude'] = geo_result['longitude']

    # Save updated CSV
    df.to_csv(input_file, index=False)
    logger.info(f"Updated {input_file}")

    # Final stats
    total_with_coords = df['latitude'].notna().sum()
    print(f"\n=== GEOCODING COMPLETE ===")
    print(f"Google API successes: {google_success}")
    print(f"Gemini API successes: {gemini_success}")
    print(f"Failed: {failed}")
    print(f"\nTotal records with coordinates: {total_with_coords}/{len(df)} ({100*total_with_coords/len(df):.1f}%)")

    # Cleanup progress file if complete
    if len(geocoded) >= len(needs_geocoding):
        if progress_file.exists():
            progress_file.unlink()
            logger.info("Removed progress file (geocoding complete)")

    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Geocode AAC accident locations")
    parser.add_argument("--limit", type=int, help="Limit number of records to process")
    parser.add_argument("--resume", action="store_true", help="Resume from progress file")
    parser.add_argument("--google-key", type=str, help="Google Maps API key (or set GOOGLE_MAPS_API_KEY)")
    parser.add_argument("--gemini-key", type=str, help="Gemini API key (or set GEMINI_API_KEY)")
    args = parser.parse_args()

    # Set API keys from args if provided
    if args.google_key:
        os.environ['GOOGLE_MAPS_API_KEY'] = args.google_key
    if args.gemini_key:
        os.environ['GEMINI_API_KEY'] = args.gemini_key

    input_file = Path("data/processed_aac_accidents.csv")

    if not input_file.exists():
        logger.error(f"Input file not found: {input_file}")
        exit(1)

    geocode_aac_accidents(str(input_file), limit=args.limit, resume=args.resume)
