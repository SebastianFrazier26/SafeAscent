"""
Fix malformed dates in accidents.csv

Some dates have format "1990.0-04-04" instead of "1990-04-04"
This script cleans them up.
"""

import pandas as pd
import re

# Load accidents
df = pd.read_csv('data/tables/accidents.csv')

print(f"Total accidents: {len(df)}")

# Fix malformed dates
def fix_date(date_str):
    """Fix dates like '1990.0-04-04' to '1990-04-04'"""
    if pd.isna(date_str):
        return date_str

    # Replace year.0 with year
    fixed = re.sub(r'(\d{4})\.0-', r'\1-', str(date_str))
    return fixed

# Count malformed dates before fix
malformed_before = df['date'].dropna().str.match(r'^\d{4}\.\d+-').sum()
print(f"Malformed dates before fix: {malformed_before}")

# Apply fix
df['date'] = df['date'].apply(fix_date)

# Count malformed dates after fix
malformed_after = df['date'].dropna().str.match(r'^\d{4}\.\d+-').sum()
print(f"Malformed dates after fix: {malformed_after}")

# Verify all dates are now valid format
valid_dates = df['date'].dropna().str.match(r'^\d{4}-\d{2}-\d{2}$').sum()
total_dates = df['date'].notna().sum()
print(f"Valid dates: {valid_dates}/{total_dates} ({valid_dates/total_dates*100:.1f}%)")

# Save
df.to_csv('data/tables/accidents.csv', index=False)
print("\n✅ Fixed dates saved to accidents.csv")
