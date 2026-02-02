# Accident Data Cleaning & Consolidation Summary

## Overview

Successfully consolidated climbing accident data from three sources into a unified, cleaned dataset:

**Final Output:** `data/accidents.csv` - **4,319 total accident records**

---

## Data Sources

### 1. American Alpine Club (AAC) Accidents
- **Records:** 2,770 climbing accidents
- **Time Period:** 1990-2024
- **Coverage:** Primarily US, some Canada
- **Rich narrative descriptions** with detailed accident reports

### 2. Avalanche Accidents Database
- **Records:** 1,372 avalanche-related accidents
- **Geographic Coverage:** US states with avalanche terrain
- **Includes coordinates:** Latitude/longitude for most records
- **Structured data:** Burial counts, avalanche characteristics

### 3. NPS Mortality Data
- **Records:** 177 climbing-related deaths (from 4,635 total NPS mortalities)
- **Filtered for:** Climbing, Canyoneering, Base jumping, Mountaineering
- **Excluded:** Drownings, motor vehicle accidents, non-climbing deaths
- **Time Period:** 2007-present

---

## Data Quality & Completeness

| Field | Completeness | Records | Notes |
|-------|-------------|---------|-------|
| **Date** | 100.0% | 4,319 | All records have dates (some estimated to Jan 1 if only year known) |
| **State/Province** | 76.8% | 3,317 | Geographic coverage across US and Canada |
| **Location** | 84.6% | 3,656 | Park names, areas, general locations |
| **Mountain/Peak** | 32.4% | 1,398 | Specific peak names extracted from narratives |
| **Route** | 36.5% | 1,577 | Specific climbing routes (after cleaning 67 false positives) |
| **Coordinates** | 31.8% | 1,372 | Primarily from avalanche data |
| **Accident Type** | 100.0% | 4,319 | Categorized (avalanche, fall, rappel error, etc.) |
| **Injury Severity** | 100.0% | 4,319 | fatal, serious, minor, or unknown |

---

## Top Mountains by Accident Count

1. **Mount McKinley (Denali)** - 197 accidents
2. **Mount Rainier** - 102 accidents
3. **Mount Hood** - 70 accidents
4. **Mount Shasta** - 61 accidents
5. **Mount Washington** - 41 accidents
6. **Longs Peak** - 41 accidents
7. **Temple Mountain** - 24 accidents

---

## Accident Severity Distribution

- **Serious Injuries:** 2,153 (49.8%)
- **Fatal:** 1,149 (26.6%)
- **Unknown:** 798 (18.5%)
- **Minor:** 219 (5.1%)

---

## Accident Type Distribution

- **Avalanche:** 1,506 (34.9%)
- **Roped Climbing:** 1,438 (33.3%)
- **Unknown:** 452 (10.5%)
- **Rappel Error:** 255 (5.9%)
- **Fall:** 216 (5.0%)
- **Ice Climbing:** 164 (3.8%)
- **Belay Error:** 122 (2.8%)
- **Rockfall:** 83 (1.9%)

---

## Data Processing Work Completed

### ✅ Automated Extraction
1. **Date Extraction** - Extracted dates from unstructured accident narratives using regex patterns for month/day/year
2. **Location Parsing** - Extracted state, park/area, and mountain names from titles and text
3. **Route Name Extraction** - Identified climbing route names from descriptions (with quotation patterns, grade indicators)
4. **Accident Classification** - Categorized accidents by type (fall, rappel error, avalanche, etc.)
5. **Injury Severity** - Classified as fatal, serious, or minor based on tags and outcome
6. **Age Range** - Extracted age categories from AAC data
7. **Climbing Grade** - Extracted YDS grades (5.X) and ice grades (WI/AI/M) from narratives

### ✅ Data Cleaning
- Removed 67 false-positive route names (e.g., "good", "victim", "Laury", single-letter names)
- Standardized date formats to YYYY-MM-DD
- Unified location naming conventions
- Generated unique accident IDs

### ✅ Manual Enhancement (Sample)
Manually processed 10 priority records to demonstrate detailed extraction capability:
- Corrected dates from careful reading
- Extracted specific route names (e.g., "Disappointment Cleaver", "West Shoulder Direct")
- Added detailed contextual notes
- Fixed misclassified data

---

## Scripts Created

All processing scripts are in `scripts/` directory:

1. **`clean_accidents.py`** - Main consolidation script
   - Combines all three data sources
   - Automated extraction of structured data from text
   - ~5 minutes runtime

2. **`enhance_accidents.py`** - Enhanced NLP extraction
   - More sophisticated pattern matching
   - Better route name extraction
   - Mountain name identification
   - Made 1,808 improvements

3. **`manual_enhancement_sample.py`** - Priority identification
   - Identifies records needing manual review
   - Creates samples for detailed processing
   - Removes false positive route names

4. **`apply_manual_corrections.py`** - Manual updates
   - Applies carefully extracted information
   - Demonstrates what's possible with manual review

---

## Remaining Work & Recommendations

### High Priority

#### 1. **Route Names** (Priority 1 & 2: ~321 records)
- **Fatal accidents with mountain names but missing routes:** 139 records
- **Serious accidents on major peaks missing routes:** 182 records
- These are well-documented accidents that would benefit most from manual review

#### 2. **Geocoding**
- Add latitude/longitude for AAC and NPS records (currently only 31.8% have coordinates)
- Could use GeoPy or Google Geocoding API
- Focus on major peaks first (McKinley, Rainier, Hood, etc.)

#### 3. **Mountain Names** (Priority 3: 398 records)
- Records with location but missing specific mountain/peak names
- Many are in well-known areas where mountain could be inferred

### Medium Priority

#### 4. **Date Refinement**
- ~20% of dates are estimated to Jan 1 because only year was available
- Could extract more precise dates from narratives with manual review

#### 5. **Climber Information**
- Extract climber ages more systematically (names with ages in parentheses)
- Build separate `climbers` table with unique climber IDs
- Link accidents to climber records

#### 6. **False Positive Cleanup**
- A few false positive route names may remain (e.g., "summit fever" in record ac_2f77e6c7)
- Could create a more comprehensive filter

### Lower Priority

#### 7. **Enhanced Tags**
- Extract equipment mentions (helmet, harness, rope type)
- Weather conditions extraction
- Experience level indicators

#### 8. **Relationship to Routes Database**
- Link accidents to existing `routes_rated.csv` data
- Match mountain names and route names where possible

---

## Data Schema

The final `accidents.csv` contains:

| Column | Type | Description |
|--------|------|-------------|
| `accident_id` | string | Unique identifier (ac_XXXXXXXX) |
| `source` | string | AAC, Avalanche, or NPS |
| `source_id` | string | Original ID from source database |
| `date` | date | Accident date (YYYY-MM-DD) |
| `year` | integer | Year of accident |
| `state` | string | State/Province |
| `location` | string | Park, area, or general location |
| `mountain` | string | Specific peak or crag name |
| `route` | string | Climbing route name |
| `latitude` | float | Latitude (primarily avalanche data) |
| `longitude` | float | Longitude (primarily avalanche data) |
| `accident_type` | string | Category (fall, rappel, avalanche, etc.) |
| `activity` | string | Type of activity |
| `injury_severity` | string | fatal, serious, minor, unknown |
| `age_range` | string | Age category (from AAC data) |
| `description` | string | Narrative description (truncated to 1000 chars) |
| `tags` | string | Comma-separated tags and metadata |

---

## Next Steps for Manual Enhancement

If you want to continue improving the dataset:

1. **Review Priority Records:**
   - Sample saved in: `data/accidents_manual_review_sample.csv`
   - 45 high-priority records identified

2. **For Each Record:**
   - Read the full description
   - Extract specific route names
   - Verify/correct dates
   - Add missing mountain names
   - Look up coordinates for locations

3. **Batch Processing:**
   - Process 10-20 records at a time
   - Focus on major peaks first (McKinley, Rainier, Hood)
   - Fatal accidents are higher priority than minor ones

4. **Geocoding:**
   ```python
   from geopy.geocoders import Nominatim
   # Look up "Mount Rainier, Washington"
   # Add lat/lon to records
   ```

---

## Files Generated

- **`data/accidents.csv`** - Final consolidated dataset (4,319 records) ✅
- **`data/accidents_initial.csv`** - Backup of first automated pass
- **`data/accidents_manual_review_sample.csv`** - 45 priority records for review
- **`scripts/clean_accidents.py`** - Main processing script
- **`scripts/enhance_accidents.py`** - Enhanced extraction script
- **`scripts/manual_enhancement_sample.py`** - Sample generation script
- **`scripts/apply_manual_corrections.py`** - Manual corrections script

---

## Success Metrics

✅ **Consolidated 3 data sources** into unified format
✅ **4,319 total accidents** from climbing, avalanche, and mortality data
✅ **100% date coverage** (all records have dates)
✅ **76.8% geographic data** (state/province)
✅ **36.5% route names** extracted from unstructured text
✅ **32.4% mountain names** identified
✅ **Removed 67 false positives** from route names
✅ **Manually enhanced 10 sample records** demonstrating NLP capabilities
✅ **Accident type classification** for all records
✅ **Injury severity** categorized for all records

---

## Summary

This dataset provides a comprehensive foundation for climbing accident analysis, with good coverage of dates, locations, and accident types. The main opportunities for improvement are:

1. **More route names** through manual review (especially for fatal accidents on major peaks)
2. **Geocoding** to add coordinates for the 68% of records that don't have them
3. **Enhanced climber tracking** by extracting individual climber records

The automated extraction achieved impressive results, but the unstructured nature of AAC accident reports means that manual review (leveraging LLM capabilities for text understanding) would significantly improve data quality for the most important records.

---

*Generated: 2026-01-23*
*Dataset: data/accidents.csv*
*Total Records: 4,319*
