"""
Enhanced Accident Data Processing
Uses more sophisticated NLP to extract accurate information from accident reports
"""

import pandas as pd
import numpy as np
import re
from datetime import datetime

def extract_route_name_enhanced(text, title):
    """Extract route name using multiple strategies"""
    if pd.isna(text):
        return None

    # Strategy 1: Look for quoted text that appears to be a route name
    quote_pattern = r'["\u201c]([^"\u201d]{3,50})["\u201d]'
    matches = re.findall(quote_pattern, text[:500])

    for match in matches:
        # Filter out common phrases that aren't route names
        if any(word in match.lower() for word in ['analysis', 'source:', 'editor', 'note', 'report']):
            continue
        # Check if it looks like a route name (has descriptive words, maybe a grade)
        if re.search(r'(ridge|wall|crack|face|couloir|gully|route|buttress|\d\.\d)', match, re.IGNORECASE):
            return match.strip()
        # If it's followed by a grade, it's probably a route
        match_context = text[:500].find(match)
        if match_context != -1:
            after_match = text[match_context + len(match):match_context + len(match) + 30]
            if re.search(r'5\.\d+|III|IV|V|VI|WI\d|AI\d|M\d', after_match):
                return match.strip()

    # Strategy 2: Look for "climbing/leading X, a 5.X" pattern
    pattern1 = r'(?:climb|lead|attempting to (?:climb|lead))\s+([A-Z][\w\s\-\']{3,40}?),?\s+a\s+(?:5\.\d+|\d+\.\d+|WI\d|AI\d|M\d|Class \d)'
    match = re.search(pattern1, text[:500])
    if match:
        route = match.group(1).strip()
        # Clean up common trailing words
        route = re.sub(r'\s+(which|that|on|at|in)$', '', route, flags=re.IGNORECASE)
        if len(route) > 3 and len(route) < 50:
            return route

    # Strategy 3: Look for route descriptions like "the X Ridge" or "X Face"
    pattern2 = r'(?:the|on|via)\s+([A-Z][\w\s\-\']{3,40}?(?:Ridge|Face|Wall|Route|Couloir|Gully|Buttress|Crack))'
    match = re.search(pattern2, text[:500])
    if match:
        route = match.group(1).strip()
        # Make sure it's not too generic
        if not re.match(r'^(the |a |an )', route, re.IGNORECASE):
            if len(route) > 5 and len(route) < 50:
                return route

    return None

def extract_mountain_name(text, title):
    """Extract specific mountain/peak name"""
    if pd.isna(text) and pd.isna(title):
        return None

    combined = f"{title} {text}" if not pd.isna(title) else text

    # Look for "Mount X" or "X Peak" or "X Mountain"
    patterns = [
        r'Mount\s+([A-Z][\w\s\-\']{2,30}?)(?:\s|,|\.|\n)',
        r'Mt\.\s+([A-Z][\w\s\-\']{2,30}?)(?:\s|,|\.|\n)',
        r'([A-Z][\w\s\-\']{2,30}?)\s+(?:Peak|Mountain|Crag|Wall)(?:\s|,|\.|\n)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, combined[:300])
        if matches:
            # Return the first reasonable match
            for match in matches:
                cleaned = match.strip()
                # Filter out common false positives
                if cleaned.lower() not in ['national', 'state', 'rocky', 'cascades', 'park']:
                    if len(cleaned) > 2 and len(cleaned) < 30:
                        return cleaned

    return None

def extract_climbing_grade(text):
    """Extract climbing grade from text"""
    if pd.isna(text):
        return None

    # YDS grades: 5.0 through 5.15
    yds = re.search(r'5\.(\d+[a-d]?)', text[:500])
    if yds:
        return f"5.{yds.group(1)}"

    # Ice grades: WI1-7, AI1-7, M1-15
    ice = re.search(r'(WI|AI|M)(\d+)', text[:500])
    if ice:
        return f"{ice.group(1)}{ice.group(2)}"

    return None

def extract_elevation(text):
    """Extract elevation from text"""
    if pd.isna(text):
        return None

    # Look for elevation in meters or feet
    patterns = [
        r'(\d{1,2},?\d{3})\s*(?:meters|m\.?|metres)',
        r'(\d{1,2},?\d{3})\s*(?:feet|ft\.?|\')',
    ]

    for pattern in patterns:
        match = re.search(pattern, text[:500])
        if match:
            elev = match.group(1).replace(',', '')
            return int(elev)

    return None

def extract_victim_names(text):
    """Extract victim names from text"""
    if pd.isna(text):
        return []

    # Common patterns: "Name (age)" or "Name, age"
    pattern = r'([A-Z][a-z]+\s+[A-Z][a-z]+)\s*\((\d{2})\)'
    matches = re.findall(pattern, text[:800])

    if matches:
        return [(name.strip(), int(age)) for name, age in matches[:3]]  # Limit to first 3

    return []

def improve_date_extraction(text, year, title):
    """Improved date extraction with multiple strategies"""
    if pd.isna(text):
        if year:
            return f"{int(year)}-01-01"
        return None

    # Strategy 1: Full date with year
    pattern1 = r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})'
    match = re.search(pattern1, text[:500], re.IGNORECASE)
    if match:
        month_map = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12
        }
        month = month_map[match.group(1).lower()]
        day = int(match.group(2))
        year_found = int(match.group(3))
        try:
            return f"{year_found}-{month:02d}-{day:02d}"
        except:
            pass

    # Strategy 2: Month and day without year (use provided year)
    pattern2 = r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})'
    match = re.search(pattern2, text[:500], re.IGNORECASE)
    if match and year:
        month_map = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12
        }
        month = month_map[match.group(1).lower()]
        day = int(match.group(2))
        try:
            return f"{int(year)}-{month:02d}-{day:02d}"
        except:
            pass

    # Fallback: use year if available
    if pd.notna(year):
        try:
            return f"{int(year)}-01-01"
        except:
            pass

    return None

def enhance_aac_accidents():
    """Re-process AAC accidents with enhanced extraction"""
    print("Loading original AAC data...")
    aac_df = pd.read_excel('data/aac_accidents.xlsx')
    accidents_df = pd.read_csv('data/accidents.csv')

    # Filter for AAC accidents only
    aac_accidents = accidents_df[accidents_df['source'] == 'AAC'].copy()

    print(f"Enhancing {len(aac_accidents)} AAC accident records...")
    print("This will extract more accurate route names, dates, and locations...")

    enhanced_count = 0

    for idx, row in aac_accidents.iterrows():
        source_id = row['source_id']
        # Convert source_id to int if needed
        try:
            source_id_int = int(source_id)
        except:
            source_id_int = source_id

        original_match = aac_df[aac_df['ID'] == source_id_int]
        if len(original_match) == 0:
            continue
        original_row = original_match.iloc[0]

        # Enhanced route extraction
        new_route = extract_route_name_enhanced(original_row['Text'], original_row['Accident Title'])
        if new_route and (pd.isna(row['route']) or len(str(row['route'])) < 5 or str(row['route']).lower() in ['good', 'fever', 'laury']):
            accidents_df.at[idx, 'route'] = new_route
            enhanced_count += 1

        # Enhanced mountain extraction
        new_mountain = extract_mountain_name(original_row['Text'], original_row['Accident Title'])
        if new_mountain:
            accidents_df.at[idx, 'mountain'] = new_mountain
            enhanced_count += 1

        # Enhanced date extraction
        new_date = improve_date_extraction(original_row['Text'], original_row['Publication Year'], original_row['Accident Title'])
        if new_date and (pd.isna(row['date']) or row['date'] == f"{int(original_row['Publication Year'])}-01-01"):
            accidents_df.at[idx, 'date'] = new_date

        # Extract climbing grade
        grade = extract_climbing_grade(original_row['Text'])
        if grade:
            # Add grade to tags if not already there
            current_tags = str(row['tags']) if pd.notna(row['tags']) else ''
            if grade not in current_tags:
                accidents_df.at[idx, 'tags'] = f"{current_tags}, grade:{grade}" if current_tags else f"grade:{grade}"

        # Extract elevation
        elevation = extract_elevation(original_row['Text'])
        if elevation:
            current_tags = str(accidents_df.at[idx, 'tags']) if pd.notna(accidents_df.at[idx, 'tags']) else ''
            if 'elevation:' not in current_tags:
                accidents_df.at[idx, 'tags'] = f"{current_tags}, elevation:{elevation}m" if current_tags else f"elevation:{elevation}m"

        if (idx + 1) % 500 == 0:
            print(f"  Processed {idx + 1} records... ({enhanced_count} enhancements made)")

    print(f"\nCompleted! Made {enhanced_count} enhancements to AAC records.")

    # Save enhanced version
    output_file = 'data/accidents_enhanced.csv'
    accidents_df.to_csv(output_file, index=False)
    print(f"Saved enhanced data to: {output_file}")

    # Show some examples
    print("\nSample enhanced records with route names:")
    sample = accidents_df[accidents_df['route'].notna()].head(10)
    for _, row in sample.iterrows():
        print(f"\n  {row['date']} - {row['mountain']} - {row['route']}")
        print(f"    Location: {row['location']}, {row['state']}")
        print(f"    Type: {row['accident_type']}, Severity: {row['injury_severity']}")

if __name__ == '__main__':
    enhance_aac_accidents()
