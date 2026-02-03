#!/usr/bin/env python3
"""
Extract Structured Data from AAC Accident Reports
==================================================
Uses Gemini API to extract location, date, route, and other structured
information from American Alpine Club accident narrative text.

Input: data/aac_accidents.xlsx
Output: data/processed_aac_accidents.csv

Environment Variables:
- GEMINI_API_KEY: Your Google Gemini API key

Usage:
    python scripts/extract_aac_accidents.py [--limit N] [--resume]

    --limit N   Process only N records (for testing)
    --resume    Resume from last saved progress
"""

import pandas as pd
import json
import re
import time
import argparse
import os
from pathlib import Path
from datetime import datetime
import logging

try:
    import google.generativeai as genai
except ImportError:
    print("Please install google-generativeai: pip install google-generativeai")
    exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Rate limiting (Gemini has generous limits, but let's be safe)
REQUESTS_PER_MINUTE = 30
REQUEST_DELAY = 60 / REQUESTS_PER_MINUTE

# US States for validation
US_STATES = {
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
}

EXTRACTION_PROMPT = """Extract structured information from this climbing accident report.

<accident_report>
{text}
</accident_report>

Extract the following information and respond ONLY with a valid JSON object (no markdown, no explanation):

{{
    "state": "US state where the accident occurred (full name, e.g., 'Colorado')",
    "area": "General area or park name (e.g., 'Rocky Mountain National Park', 'Yosemite')",
    "specific_location": "Specific crag, wall, or formation (e.g., 'El Capitan', 'Lumpy Ridge')",
    "route_name": "Name of the route if mentioned (e.g., 'The Nose', 'Casual Route')",
    "route_grade": "Climbing grade if mentioned (e.g., '5.10a', '5.9', 'WI4')",
    "date": "Date of accident in YYYY-MM-DD format if available, otherwise null",
    "accident_type": "Primary type: 'fall', 'rockfall', 'equipment_failure', 'rappel_error', 'weather', 'medical', 'avalanche', 'crevasse', 'other'",
    "activity": "Type of climbing: 'rock_climbing', 'ice_climbing', 'alpine_climbing', 'mountaineering', 'bouldering', 'other'",
    "severity": "'fatal' if someone died, 'serious' if hospitalized/major injury, 'minor' otherwise",
    "fatalities": "Number of deaths as integer, 0 if none",
    "injuries": "Number of injuries as integer, 0 if none",
    "contributing_factors": ["List of factors like 'inexperience', 'equipment_failure', 'weather', 'anchor_failure', 'protection_pulled', 'rope_cut', 'held_fall', etc."],
    "summary": "One sentence summary of what happened (max 100 words)"
}}

Important:
- Respond with ONLY the JSON object, no other text
- If information is not available, use null
- For US states, use the full name (e.g., "California" not "CA")
- For grades, use standard notation (5.10a, WI4, M5, etc.)
- Be conservative - only extract what's clearly stated"""


def extract_accident_info(model, text, title=None, publication_year=None):
    """Use Gemini to extract structured data from accident narrative."""
    # Add context from title if available
    full_text = text
    if title and pd.notna(title):
        full_text = f"Title: {title}\n\n{text}"

    try:
        response = model.generate_content(
            EXTRACTION_PROMPT.format(text=full_text[:8000]),  # Truncate very long texts
            generation_config=genai.types.GenerationConfig(
                temperature=0.1,  # Low temperature for consistent extraction
                max_output_tokens=1024,
            )
        )

        response_text = response.text

        # Parse JSON from response
        # Try to find JSON in the response (handle potential markdown wrapping)
        response_text = response_text.strip()
        if response_text.startswith('```json'):
            response_text = response_text[7:]
        if response_text.startswith('```'):
            response_text = response_text[3:]
        if response_text.endswith('```'):
            response_text = response_text[:-3]
        response_text = response_text.strip()

        json_match = re.search(r'\{[\s\S]*\}', response_text)
        if json_match:
            data = json.loads(json_match.group())

            # Validate state
            if data.get('state') and data['state'] not in US_STATES:
                # Try to fix common issues
                state_lower = data['state'].lower()
                for us_state in US_STATES:
                    if us_state.lower() == state_lower:
                        data['state'] = us_state
                        break

            # Add publication year as fallback date context
            if not data.get('date') and publication_year:
                data['publication_year'] = int(publication_year)

            return data
        else:
            logger.warning(f"Could not parse JSON from response: {response_text[:200]}")
            return None

    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"API error: {e}")
        return None


def process_aac_data(input_file, output_file, limit=None, resume=False):
    """Process AAC accident data using Gemini API."""

    # Check for API key
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        logger.error("GEMINI_API_KEY environment variable not set!")
        logger.info("Set it with: export GEMINI_API_KEY=your_key_here")
        logger.info("Get a key at: https://makersuite.google.com/app/apikey")
        exit(1)

    # Initialize Gemini
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')  # Best model for this task
    logger.info("Initialized Gemini 2.0 Flash model")

    # Load data
    logger.info(f"Reading {input_file}")
    df = pd.read_excel(input_file)
    logger.info(f"Loaded {len(df)} records")

    # Load progress if resuming
    progress_file = Path(output_file).with_suffix('.progress.json')
    processed_ids = set()
    results = []

    if resume and progress_file.exists():
        with open(progress_file, 'r') as f:
            progress = json.load(f)
            processed_ids = set(progress.get('processed_ids', []))
            results = progress.get('results', [])
            logger.info(f"Resuming from {len(processed_ids)} processed records")

    # Filter out already processed
    df_to_process = df[~df['ID'].isin(processed_ids)]

    if limit:
        df_to_process = df_to_process.head(limit)
        logger.info(f"Limiting to {len(df_to_process)} records")

    # Process records
    total = len(df_to_process)
    for idx, (_, row) in enumerate(df_to_process.iterrows()):
        record_id = row['ID']
        text = row.get('Text', '')
        title = row.get('Accident Title', '')
        pub_year = row.get('Publication Year')

        if pd.isna(text) or len(str(text).strip()) < 50:
            logger.warning(f"Skipping record {record_id}: insufficient text")
            processed_ids.add(record_id)
            continue

        # Extract information
        extracted = extract_accident_info(model, str(text), title, pub_year)

        if extracted:
            extracted['source_id'] = record_id
            extracted['source'] = 'aac'
            results.append(extracted)

        processed_ids.add(record_id)

        # Progress logging
        if (idx + 1) % 10 == 0:
            logger.info(f"Processed {idx + 1}/{total} records ({len(results)} successful)")

            # Save progress
            with open(progress_file, 'w') as f:
                json.dump({
                    'processed_ids': list(processed_ids),
                    'results': results,
                    'last_updated': datetime.now().isoformat()
                }, f)

        # Rate limiting
        time.sleep(REQUEST_DELAY)

    # Final save of progress
    with open(progress_file, 'w') as f:
        json.dump({
            'processed_ids': list(processed_ids),
            'results': results,
            'last_updated': datetime.now().isoformat()
        }, f)

    # Convert to DataFrame
    if results:
        df_results = pd.DataFrame(results)

        # Standardize columns
        output_df = pd.DataFrame({
            'source_id': df_results['source_id'],
            'source': df_results['source'],
            'date': df_results.get('date'),
            'state': df_results.get('state'),
            'latitude': None,  # Will be geocoded later
            'longitude': None,
            'location_name': df_results.apply(
                lambda r: ', '.join(filter(None, [r.get('area'), r.get('specific_location')])),
                axis=1
            ),
            'route_name': df_results.get('route_name'),
            'route_grade': df_results.get('route_grade'),
            'activity': df_results.get('activity'),
            'accident_type': df_results.get('accident_type'),
            'severity': df_results.get('severity'),
            'fatalities': pd.to_numeric(df_results.get('fatalities', 0), errors='coerce').fillna(0).astype(int),
            'injuries': pd.to_numeric(df_results.get('injuries', 0), errors='coerce').fillna(0).astype(int),
            'description': df_results.get('summary'),
            'contributing_factors': df_results.get('contributing_factors').apply(
                lambda x: json.dumps(x) if isinstance(x, list) else None
            ),
        })

        # Save results
        output_df.to_csv(output_file, index=False)
        logger.info(f"Saved {len(output_df)} processed records to {output_file}")

        # Clean up progress file on successful completion
        if len(processed_ids) >= len(df):
            if progress_file.exists():
                progress_file.unlink()
                logger.info("Removed progress file (processing complete)")

        # Print statistics
        print("\n=== Extraction Statistics ===")
        print(f"Total processed: {len(output_df)}")
        print(f"\nBy state (top 10):")
        print(output_df['state'].value_counts().head(10).to_string())
        print(f"\nBy accident type:")
        print(output_df['accident_type'].value_counts().to_string())
        print(f"\nBy activity:")
        print(output_df['activity'].value_counts().to_string())
        print(f"\nBy severity:")
        print(output_df['severity'].value_counts().to_string())
        print(f"\nRoutes mentioned: {output_df['route_name'].notna().sum()}")
        print(f"Grades mentioned: {output_df['route_grade'].notna().sum()}")
    else:
        logger.warning("No results extracted")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract AAC accident data using Gemini API")
    parser.add_argument("--limit", type=int, help="Process only N records")
    parser.add_argument("--resume", action="store_true", help="Resume from progress file")
    args = parser.parse_args()

    input_file = Path("data/aac_accidents.xlsx")
    output_file = Path("data/processed_aac_accidents.csv")

    if not input_file.exists():
        logger.error(f"Input file not found: {input_file}")
        exit(1)

    process_aac_data(str(input_file), str(output_file), limit=args.limit, resume=args.resume)
