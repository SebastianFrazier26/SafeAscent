"""
Accident Data Cleaning and Consolidation Script
Combines AAC, Avalanche, and NPS mortality data into unified accidents.csv
"""

import pandas as pd
import numpy as np
import re
from datetime import datetime
import hashlib

def generate_accident_id(source, original_id):
    """Generate unique accident ID"""
    return f"ac_{hashlib.md5(f'{source}_{original_id}'.encode()).hexdigest()[:8]}"

def extract_date_from_text(text, year):
    """Extract date from accident text"""
    if pd.isna(text):
        return None

    # Common patterns: "On May 7, 1989", "May 7", "May 1989"
    month_pattern = r'\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+(\d{1,2})(?:,\s*(\d{4}))?\b'
    match = re.search(month_pattern, text, re.IGNORECASE)

    if match:
        month_str = match.group(1)
        day = match.group(2)
        year_found = match.group(3) if match.group(3) else year

        # Convert month name to number
        month_map = {
            'january': '01', 'jan': '01', 'february': '02', 'feb': '02',
            'march': '03', 'mar': '03', 'april': '04', 'apr': '04',
            'may': '05', 'june': '06', 'jun': '06', 'july': '07', 'jul': '07',
            'august': '08', 'aug': '08', 'september': '09', 'sep': '09', 'sept': '09',
            'october': '10', 'oct': '10', 'november': '11', 'nov': '11',
            'december': '12', 'dec': '12'
        }
        month_num = month_map.get(month_str.lower(), '01')

        try:
            return f"{year_found}-{month_num}-{day.zfill(2)}"
        except:
            return f"{year_found}-{month_num}-01"

    # If no date found, use year only
    if year:
        return f"{int(year)}-01-01"

    return None

def extract_location_from_text(text, title):
    """Extract location (mountain/crag) from text and title"""
    if pd.isna(text) and pd.isna(title):
        return None, None, None

    combined_text = f"{title} {text}" if not pd.isna(title) else text

    # Common patterns for location
    # "Colorado, Rocky Mountain National Park"
    # "Yosemite National Park, California"
    location_info = {
        'state': None,
        'park': None,
        'mountain': None
    }

    # Extract state
    states = ['Alaska', 'California', 'Colorado', 'Washington', 'Montana', 'Wyoming',
              'Utah', 'Oregon', 'New Hampshire', 'Vermont', 'New York', 'Arizona']
    for state in states:
        if state in combined_text:
            location_info['state'] = state
            break

    # Extract park/area
    park_pattern = r'([\w\s]+(?:National Park|State Park|Wilderness|Mountain|Peak|Crag|Wall|Canyon))'
    parks = re.findall(park_pattern, combined_text)
    if parks:
        location_info['park'] = parks[0].strip()

    return location_info['state'], location_info['park'], location_info['mountain']

def extract_route_from_text(text):
    """Extract route name from text"""
    if pd.isna(text):
        return None

    # Look for quoted route names or specific patterns
    # Pattern: climbing "Route Name" or on Route Name (5.X)
    quote_pattern = r'["\u201c]([^"\u201d]+)["\u201d]'
    matches = re.findall(quote_pattern, text)
    if matches:
        return matches[0]

    # Pattern: "attempting to lead Route Name"
    lead_pattern = r'(?:lead|climb|attempting)\s+([A-Z][A-Za-z\s\'\-]+),\s*a\s+\d'
    match = re.search(lead_pattern, text)
    if match:
        return match.group(1).strip()

    return None

def get_age_from_aac(row):
    """Extract age category from AAC data"""
    age_cols = ['<15', '15-20', '21-25', '26-30', '31-35', '36-50', '51-75', '>75']
    for col in age_cols:
        if row.get(col, 0) == 1:
            return col
    return None

def get_injury_severity(row, source):
    """Determine injury severity"""
    if source == 'aac':
        if row.get('Deadly', 0) == 1:
            return 'fatal'
        elif row.get('Serious', 0) == 1:
            return 'serious'
        elif row.get('Minor', 0) == 1:
            return 'minor'
    elif source == 'avalanche':
        if row.get('killed_count', 0) > 0:
            return 'fatal'
        elif row.get('buried_count', 0) > 0:
            return 'serious'
    elif source == 'nps':
        return 'fatal'  # NPS data is mortality data
    return 'unknown'

def get_accident_type(row, source):
    """Determine accident type"""
    if source == 'aac':
        # Check various tag columns
        if row.get('Rappel Error', 0) == 1:
            return 'rappel'
        elif row.get('Belay Error', 0) == 1:
            return 'belay'
        elif row.get('Anchor Failure / Error', 0) == 1:
            return 'anchor_failure'
        elif row.get('Avalanche', 0) == 1:
            return 'avalanche'
        elif row.get('Natural Rockfall', 0) == 1:
            return 'rockfall'
        elif row.get('Ledge Fall', 0) == 1 or row.get('Fall', 0) == 1:
            return 'fall'
        elif row.get('Ice Climbing', 0) == 1:
            return 'ice_climbing'
        # Check description
        elif 'roped' in str(row.get('Tags Applied', '')).lower():
            return 'roped_climbing'
        elif 'solo' in str(row.get('Tags Applied', '')).lower():
            return 'solo'
    elif source == 'avalanche':
        return 'avalanche'
    elif source == 'nps':
        cause = str(row.get('Cause of Death', '')).lower()
        if 'fall' in cause:
            return 'fall'
        elif 'avalanche' in cause:
            return 'avalanche'

    return 'unknown'

def process_aac_accidents(aac_df):
    """Process AAC accident data"""
    print(f"Processing {len(aac_df)} AAC accidents...")

    accidents = []
    for idx, row in aac_df.iterrows():
        accident_id = generate_accident_id('aac', row['ID'])
        date = extract_date_from_text(row['Text'], row['Publication Year'])
        state, park, mountain = extract_location_from_text(row['Text'], row['Accident Title'])
        route = extract_route_from_text(row['Text'])
        age = get_age_from_aac(row)
        injury = get_injury_severity(row, 'aac')
        acc_type = get_accident_type(row, 'aac')

        accidents.append({
            'accident_id': accident_id,
            'source': 'AAC',
            'source_id': row['ID'],
            'date': date,
            'year': int(row['Publication Year']) if pd.notna(row['Publication Year']) else None,
            'state': state,
            'location': park,
            'mountain': mountain,
            'route': route,
            'latitude': None,
            'longitude': None,
            'accident_type': acc_type,
            'activity': 'climbing',
            'injury_severity': injury,
            'age_range': age,
            'description': row['Text'][:1000] if pd.notna(row['Text']) else None,  # Truncate for CSV
            'tags': row['Tags Applied'] if pd.notna(row['Tags Applied']) else None
        })

        if (idx + 1) % 500 == 0:
            print(f"  Processed {idx + 1} AAC records...")

    return accidents

def process_avalanche_accidents(aval_df):
    """Process avalanche accident data"""
    print(f"Processing {len(aval_df)} avalanche accidents...")

    accidents = []
    for idx, row in aval_df.iterrows():
        accident_id = generate_accident_id('avalanche', row['id'])

        # Parse date
        date = None
        if pd.notna(row['observed_at']):
            try:
                date_obj = pd.to_datetime(row['observed_at'])
                date = date_obj.strftime('%Y-%m-%d')
            except:
                pass

        accidents.append({
            'accident_id': accident_id,
            'source': 'Avalanche',
            'source_id': row['id'],
            'date': date,
            'year': int(row['water_year']) if pd.notna(row['water_year']) else None,
            'state': row['state'] if pd.notna(row['state']) else None,
            'location': row['location'] if pd.notna(row['location']) else None,
            'mountain': None,
            'route': None,
            'latitude': row['latitude'] if pd.notna(row['latitude']) else None,
            'longitude': row['longitude'] if pd.notna(row['longitude']) else None,
            'accident_type': 'avalanche',
            'activity': row['activity'] if pd.notna(row['activity']) else 'backcountry',
            'injury_severity': get_injury_severity(row, 'avalanche'),
            'age_range': None,
            'description': row['accident_summary'][:1000] if pd.notna(row['accident_summary']) else None,
            'tags': f"buried:{row['buried_count']}, killed:{row['killed_count']}" if pd.notna(row['buried_count']) else None
        })

        if (idx + 1) % 500 == 0:
            print(f"  Processed {idx + 1} avalanche records...")

    return accidents

def process_nps_mortality(nps_df):
    """Process NPS mortality data (climbing-related only)"""
    # Filter for climbing-related activities
    climbing_activities = ['Climbing', 'Canyoneering', 'Base jumping', 'Mountaineering']
    climbing_df = nps_df[nps_df['Activity'].isin(climbing_activities)].copy()

    print(f"Processing {len(climbing_df)} NPS climbing mortality records...")

    accidents = []
    for idx, row in climbing_df.iterrows():
        accident_id = generate_accident_id('nps', idx)

        # Parse date
        date = None
        year = None
        if pd.notna(row['Incident Date']):
            try:
                date_obj = pd.to_datetime(row['Incident Date'])
                date = date_obj.strftime('%Y-%m-%d')
                year = date_obj.year
            except:
                pass

        accidents.append({
            'accident_id': accident_id,
            'source': 'NPS',
            'source_id': idx,
            'date': date,
            'year': year,
            'state': None,  # Could be extracted from park name
            'location': row['Park Name'] if pd.notna(row['Park Name']) else None,
            'mountain': None,
            'route': None,
            'latitude': None,
            'longitude': None,
            'accident_type': get_accident_type(row, 'nps'),
            'activity': row['Activity'] if pd.notna(row['Activity']) else None,
            'injury_severity': 'fatal',
            'age_range': row['Age Range'] if pd.notna(row['Age Range']) else None,
            'description': f"Cause: {row['Cause of Death']}, Activity: {row['Activity']}",
            'tags': f"sex:{row['Sex']}, cause:{row['Cause of Death']}"
        })

        if (idx + 1) % 50 == 0:
            print(f"  Processed {idx + 1} NPS records...")

    return accidents

def main():
    print("=" * 80)
    print("ACCIDENT DATA CLEANING AND CONSOLIDATION")
    print("=" * 80)

    # Load data
    print("\nLoading data sources...")
    aac_df = pd.read_excel('data/aac_accidents.xlsx')
    aval_df = pd.read_csv('data/avalanche_accidents.csv')
    nps_df = pd.read_excel('data/nps_mortality.xlsx', sheet_name='CY2007-Present Q2')

    print(f"  AAC Accidents: {len(aac_df)} records")
    print(f"  Avalanche Accidents: {len(aval_df)} records")
    print(f"  NPS Mortality: {len(nps_df)} total records")

    # Process each source
    print("\n" + "=" * 80)
    all_accidents = []

    all_accidents.extend(process_aac_accidents(aac_df))
    all_accidents.extend(process_avalanche_accidents(aval_df))
    all_accidents.extend(process_nps_mortality(nps_df))

    # Create DataFrame
    print("\n" + "=" * 80)
    print("Creating unified dataset...")
    accidents_df = pd.DataFrame(all_accidents)

    # Sort by date
    accidents_df = accidents_df.sort_values('date', na_position='last')

    # Save to CSV
    output_file = 'data/accidents.csv'
    accidents_df.to_csv(output_file, index=False)

    print(f"\n{'=' * 80}")
    print(f"SUCCESS!")
    print(f"{'=' * 80}")
    print(f"Total accidents processed: {len(accidents_df)}")
    print(f"  AAC: {len([a for a in all_accidents if a['source'] == 'AAC'])}")
    print(f"  Avalanche: {len([a for a in all_accidents if a['source'] == 'Avalanche'])}")
    print(f"  NPS: {len([a for a in all_accidents if a['source'] == 'NPS'])}")
    print(f"\nOutput saved to: {output_file}")

    # Show summary statistics
    print(f"\nDataset Summary:")
    print(f"  Date range: {accidents_df['date'].min()} to {accidents_df['date'].max()}")
    print(f"  States covered: {accidents_df['state'].nunique()} unique states")
    print(f"  Injury severity distribution:")
    print(accidents_df['injury_severity'].value_counts().to_string())
    print(f"\n  Accident type distribution:")
    print(accidents_df['accident_type'].value_counts().head(10).to_string())

if __name__ == '__main__':
    main()
