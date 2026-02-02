"""
Manual enhancement of accident records using detailed text analysis
This script identifies records that need manual review and creates a sample for detailed processing
"""

import pandas as pd
import re

def is_likely_false_route_name(route_name):
    """Identify likely false positive route names"""
    if pd.isna(route_name):
        return False

    route = str(route_name).lower().strip()

    # Common false positives
    false_positives = [
        'summit fever', 'good', 'laury', 'rocks', 'fever', 'injury',
        'error', 'failure', 'accident', 'fall', 'death', 'climb',
        'hike', 'trip', 'day', 'time', 'party', 'group', 'team',
        'leader', 'victim', 'climber', 'guide', 'member'
    ]

    # If the route name is just a single word that's too generic
    if ' ' not in route and route in false_positives:
        return True

    # If it's too short
    if len(route) < 3:
        return True

    # If it looks like a person's name (single capitalized word)
    if re.match(r'^[A-Z][a-z]+$', route_name) and len(route) < 10:
        if not any(word in route for word in ['ridge', 'face', 'wall', 'crack', 'route']):
            return True

    return False

def clean_false_routes():
    """Remove obvious false positive route names"""
    df = pd.read_csv('data/accidents.csv')

    print("Cleaning false positive route names...")
    cleaned_count = 0

    for idx, row in df.iterrows():
        if pd.notna(row['route']) and is_likely_false_route_name(row['route']):
            print(f"  Removing false route: '{row['route']}' from record {row['accident_id']}")
            df.at[idx, 'route'] = None
            cleaned_count += 1

    print(f"\nCleaned {cleaned_count} false positive route names")

    # Save
    df.to_csv('data/accidents.csv', index=False)
    return df

def identify_priority_records(df):
    """Identify AAC records that would most benefit from manual review"""
    aac = df[df['source'] == 'AAC'].copy()

    # Priority 1: Fatal accidents with mountain names but missing routes
    priority1 = aac[(aac['injury_severity'] == 'fatal') &
                    (aac['mountain'].notna()) &
                    (aac['route'].isna())]

    # Priority 2: Serious accidents on major peaks missing route info
    major_peaks = ['McKinley', 'Rainier', 'Hood', 'Shasta', 'Washington', 'Longs']
    priority2 = aac[(aac['injury_severity'].isin(['fatal', 'serious'])) &
                    (aac['mountain'].isin(major_peaks)) &
                    (aac['route'].isna())]

    # Priority 3: Records with location info but missing mountain/route
    priority3 = aac[(aac['location'].notna()) &
                    (aac['mountain'].isna()) &
                    (aac['route'].isna())]

    print("\n" + "=" * 80)
    print("PRIORITY RECORDS FOR MANUAL REVIEW")
    print("=" * 80)
    print(f"\nPriority 1 (Fatal, has mountain, missing route): {len(priority1)} records")
    print(f"Priority 2 (Serious on major peaks, missing route): {len(priority2)} records")
    print(f"Priority 3 (Has location, missing mountain/route): {len(priority3)} records")

    return priority1, priority2, priority3

def create_manual_review_sample(df, n_samples=50):
    """Create a sample of records for detailed manual review"""
    priority1, priority2, priority3 = identify_priority_records(df)

    # Take samples from each priority
    sample = pd.concat([
        priority1.head(20),
        priority2.head(20),
        priority3.head(10)
    ]).drop_duplicates(subset=['accident_id'])

    # Save to a separate file for review
    sample.to_csv('data/accidents_manual_review_sample.csv', index=False)
    print(f"\nCreated sample of {len(sample)} records for manual review")
    print(f"Saved to: data/accidents_manual_review_sample.csv")

    return sample

def show_sample_records(sample):
    """Display sample records with their full text for manual review"""
    print("\n" + "=" * 80)
    print("SAMPLE RECORDS FOR MANUAL ENHANCEMENT")
    print("=" * 80)

    for idx, row in sample.head(10).iterrows():
        print(f"\n{'=' * 80}")
        print(f"Accident ID: {row['accident_id']}")
        print(f"Date: {row['date']}")
        print(f"Location: {row['location']}, {row['state']}")
        print(f"Mountain: {row['mountain']}")
        print(f"Route: {row['route']}")
        print(f"Type: {row['accident_type']}, Severity: {row['injury_severity']}")
        print(f"\nDescription (first 400 chars):")
        desc = str(row['description'])[:400]
        print(desc)
        print("...")

def main():
    print("=" * 80)
    print("MANUAL ACCIDENT DATA ENHANCEMENT")
    print("=" * 80)

    # Step 1: Clean false positives
    df = clean_false_routes()

    # Step 2: Identify priority records
    sample = create_manual_review_sample(df, n_samples=50)

    # Step 3: Show sample
    show_sample_records(sample)

    print("\n" + "=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    print("1. Review the sample records in data/accidents_manual_review_sample.csv")
    print("2. For each record, extract:")
    print("   - Specific route names from the description")
    print("   - Exact dates (month/day/year)")
    print("   - Mountain/crag names")
    print("   - Coordinates (can be geocoded from location names)")
    print("3. Update the main accidents.csv file with enhanced information")

if __name__ == '__main__':
    main()
