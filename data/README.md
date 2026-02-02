# SafeAscent Climbing & Mountaineering Incident Data

## Overview
This directory contains datasets of climbing, mountaineering, and avalanche incidents in the United States. The data is compiled from multiple authoritative sources to enable comprehensive analysis of incident patterns, risk factors, and safety trends.

**Last Updated:** January 17, 2026
**Data Collection Period:** 1990-2026 (varies by source)

---

## Datasets

### 1. Avalanche Accidents (`avalanche_accidents.csv`)
**Source:** Colorado Avalanche Information Center (CAIC) API
**URL:** https://avalanche.org / https://api.avalanche.state.co.us
**Collection Method:** Automated API scraping
**Collection Date:** January 17, 2026

**Coverage:**
- **Time Period:** March 1997 - January 2026
- **Geographic Scope:** 15 U.S. states
- **Total Incidents:** 1,372
- **Total Fatalities:** 473

**Data Fields (43 total):**

#### Basic Information
- `id` - Unique incident identifier (UUID)
- `type` - Report type (incident_report, accident_report)
- `observed_at` - Date/time of incident (ISO 8601)
- `date_known` - Date accuracy (Estimated, Exact, etc.)
- `time_known` - Time accuracy (Unknown, Estimated, Exact)
- `water_year` - Avalanche season water year
- `state` - U.S. state abbreviation
- `latitude` - Incident latitude (decimal degrees)
- `longitude` - Incident longitude (decimal degrees)
- `status` - Report status (approved, pending, etc.)
- `investigation_status` - Investigation phase
- `created_at` - Record creation timestamp
- `updated_at` - Record update timestamp

#### Location & Context
- `location` - Location description/name
- `is_anonymous` - Whether reporter is anonymous
- `is_anonymous_location` - Whether location is anonymized

#### Incident Details
- `involvement_summary` - Brief summary of casualties (e.g., "1 snowmobiler caught, buried, and killed")
- `accident_summary` - Detailed narrative description
- `weather_summary` - Weather conditions
- `snowpack_summary` - Snowpack conditions
- `rescue_summary` - Rescue operations details
- `activity` - Activity type
- `travel_mode` - Mode of travel
- `authors` - Report authors (typically avalanche center name)
- `links_media` - Media coverage links
- `links_social_media` - Social media links
- `closest_avalanche_center` - Nearest avalanche forecast center

#### Casualty Statistics
- `involved_count` - Total people involved
- `buried_count` - Number of people buried
- `killed_count` - Number of fatalities
- `travel_activities` - JSON breakdown by activity type

#### Avalanche Technical Details
- `avalanche_aspect` - Slope aspect (N, NE, E, SE, S, SW, W, NW)
- `avalanche_elevation_feet` - Elevation in feet
- `avalanche_relative_size` - Relative size (R1-R5)
- `avalanche_destructive_size` - Destructive size (D1-D5)
- `avalanche_type_code` - Avalanche classification code
- `avalanche_problem_type` - Problem type (persistent, storm, wind, wet, etc.)
- `avalanche_primary_trigger` - Primary trigger code
- `avalanche_secondary_trigger` - Secondary trigger code
- `avalanche_angle_average` - Average slope angle (degrees)
- `avalanche_crown_average` - Average crown depth
- `avalanche_weak_layer` - Weak layer description
- `avalanche_surface` - Surface condition

#### Affected Groups
- `affected_groups` - JSON array with details about affected parties

**Citation:**
Colorado Avalanche Information Center, U.S. Avalanche Accident Reports, https://avalanche.org, Accessed January 17, 2026

---

### 2. AAC Climbing Accidents (`aac_accidents.xlsx`)
**Source:** American Alpine Club / GitHub Repository
**URL:** https://github.com/ecaroom/climbing-accidents
**Coverage:** 1990-2019 (2,724 climbing accidents)

**Scope:**
- All climbing disciplines (alpine, trad, sport, ice, top-rope)
- North American incidents
- Detailed narratives and accident analysis
- Enhanced discipline tagging

**Note:** This dataset predates the project and was obtained from an external source.

---

### 3. NPS Mortality Data (`nps_mortality.xlsx`)
**Source:** National Park Service
**Coverage:** Mortality incidents in U.S. National Parks

**Scope:**
- All causes of death in national parks
- Includes but not limited to climbing/mountaineering
- Geographic focus on national park lands

**Note:** This dataset predates the project and was obtained from an external source.

---

## Additional Data Sources (Not Yet Collected)

### The Mountaineers Incident Reports
**URL:** https://www.mountaineers.org/about/safety/safety-reports
**Format:** PDF reports (quarterly, 2000-2025)
**Geographic Focus:** Pacific Northwest (primarily Washington)

**Content:**
- Incident rates per 1,000 trip days
- Breakdown by activity type (climbing, scrambling, hiking, kayaking, etc.)
- Organized outings perspective
- Quarterly and annual trend reports

**Collection Status:** Index created; PDFs available for download
**Recommendation:** Manual review and extraction recommended due to PDF format

---

### Mountain Rescue Association (MRA)
**URL:** https://mra.org/training-education/accident-reports
**Format:** Comprehensive PDF document
**Coverage:** 1994-2011 (documented), ongoing updates

**Content:**
- Search and rescue operations data
- Multi-casualty incident analysis
- Rescue team perspective on climbing incidents
- Educational materials for rescue operations

**Collection Status:** Primary document identified
**Recommendation:** Contact MRA directly for structured data access

**Note:** MRA data significantly overlaps with AAC data but provides rescue operations perspective.

---

### Park-Specific NPS Incident Reports
**Sources:**
- Yosemite National Park: https://nps.gov/yose/planyourvisit/climbing_safety.htm
- Sequoia/Kings Canyon: https://nps.gov/seki
- NPS Morning Reports Archive: https://npshistory.com/morningreport/

**Coverage:** 1989-present (varies by park)

**Content:**
- Detailed park-specific incident reports
- Search and rescue operations
- Medical clinic data (injury classifications)
- High-elevation and specialized hazard incidents

**Collection Status:** Sources identified
**Recommendation:** May complement existing nps_mortality.xlsx with more detailed narratives

---

## Data Quality Notes

### Avalanche Accidents
**Strengths:**
- Comprehensive, authoritative source (CAIC)
- Standardized data collection
- Technical avalanche details
- Geographic coordinates
- Regular updates

**Limitations:**
- Data availability starts 1997 (not 1950 as initially advertised)
- Primarily avalanche-related incidents only
- Some historical records may be incomplete
- Geographic bias toward states with avalanche forecast centers

### AAC Accidents
**Strengths:**
- Long time series (1990-2019)
- All climbing disciplines
- Detailed narratives
- Lesson-focused analysis

**Limitations:**
- Data ends 2019 (needs updating for 2020-2026)
- May miss incidents not reported to AAC
- North America wide (includes Canada/Mexico)

### NPS Mortality
**Strengths:**
- Official government source
- Comprehensive park coverage
- All mortality causes

**Limitations:**
- Focused on national park lands only
- May lack technical climbing details
- Broader than climbing-specific incidents

---

## Data Integration Considerations

### Overlapping Incidents
Some incidents may appear in multiple datasets:
- Avalanche accidents in national parks → both avalanche_accidents.csv and nps_mortality.xlsx
- Climbing incidents → both aac_accidents.xlsx and NPS data
- Well-publicized incidents → may be in AAC, NPS, and regional sources

**Recommendation:** When combining datasets, deduplicate using:
1. Date + Location matching
2. Casualty count verification
3. Narrative similarity analysis

### Complementary Strengths
- **Avalanche data:** Technical avalanche details, comprehensive modern coverage
- **AAC data:** Climbing technique/human factors, educational focus
- **NPS data:** National park incidents, official records
- **Regional sources (Mountaineers, MRA):** Local detail, activity-specific trends

---

## File Inventory

| Filename | Format | Size | Records | Status |
|----------|--------|------|---------|--------|
| `avalanche_accidents.csv` | CSV | ~800KB | 1,372 | ✅ Complete |
| `aac_accidents.xlsx` | Excel | 7.5MB | 2,724+ | ✅ Pre-existing |
| `nps_mortality.xlsx` | Excel | 235KB | Unknown | ✅ Pre-existing |
| `avalanche_test.csv` | CSV | 31KB | 22 | 🧪 Test data |

---

## Scripts & Tools

### `/scripts/scrape_avalanche.py`
Automated scraper for CAIC avalanche incident data.

**Usage:**
```bash
python scrape_avalanche.py --start-year 1997 --end-year 2026 --output ../data/avalanche_accidents.csv
```

**Options:**
- `--start-year YEAR` - Starting year (default: 1950)
- `--end-year YEAR` - Ending year (default: current year)
- `--output FILE` - Output file path
- `--format {csv,xlsx,json}` - Output format (default: csv)

**Features:**
- Automatic pagination handling
- Rate limiting (2 requests/second)
- Incremental saving (resumable)
- Comprehensive error handling

### `/scripts/scrape_mountaineers.py`
PDF report downloader for The Mountaineers incident data.

**Status:** Created but requires manual PDF parsing

---

## Future Work

### Recommended Next Steps
1. **Update AAC data:** Collect 2020-2026 accidents to complement existing dataset
2. **Parse Mountaineers PDFs:** Extract structured data from quarterly reports
3. **Integrate park-specific data:** Scrape Yosemite, Sequoia/Kings Canyon incident archives
4. **Contact MRA:** Request structured historical data
5. **Deduplicate:** Identify and merge overlapping incidents across datasets
6. **Enrich:** Add weather data correlation, geographic analysis, trend modeling

### Additional Data Sources to Consider
- **State-specific avalanche centers:**
  - Northwest Avalanche Center (WA/OR): https://nwac.us
  - Utah Avalanche Center
  - Chugach Avalanche Center (Alaska)
  - Idaho Panhandle Avalanche Center

- **International comparison data:**
  - British Mountaineering Council (UK/Ireland)
  - UIAA international database
  - Alpine nations (France, Switzerland, etc.)

- **Academic sources:**
  - Published research papers on climbing injuries
  - Medical journal case studies
  - Risk analysis studies

---

## Legal & Ethical Considerations

### Data Usage
- All data is from publicly accessible sources
- Respect original source terms of use
- Cite sources appropriately in publications
- Anonymized data should remain anonymized

### Required Citations
When using this data, please cite:
1. Original source organizations (CAIC, AAC, NPS, etc.)
2. Access dates
3. This repository (if distributing derivative datasets)

**Example Citation:**
```
Colorado Avalanche Information Center (CAIC). U.S. Avalanche Accident Reports.
Retrieved from https://avalanche.org, January 17, 2026.

American Alpine Club. Accidents in North American Climbing Database.
Retrieved from https://publications.americanalpineclub.org, [Access Date].

National Park Service. Mortality and SAR Data. Retrieved [Access Date].
```

### Privacy
- Incident reports may contain personal information
- Use aggregated analysis when possible
- Respect family privacy for fatal incidents
- Follow IRB guidelines if conducting formal research

---

## Contact & Contributions

For questions about this data collection:
- Check source websites for official data updates
- Contact source organizations directly for data clarifications
- Review CAIC, AAC, and NPS terms of use before redistribution

---

## Technical Notes

### Data Processing
All datasets can be loaded with pandas:
```python
import pandas as pd

# Avalanche data
avalanche_df = pd.read_csv('avalanche_accidents.csv')
avalanche_df['observed_at'] = pd.to_datetime(avalanche_df['observed_at'])

# AAC data
aac_df = pd.read_excel('aac_accidents.xlsx')

# NPS data
nps_df = pd.read_excel('nps_mortality.xlsx')
```

### JSON Fields
Some fields contain JSON-encoded data:
- `travel_activities` - Breakdown of casualties by activity type
- `affected_groups` - Array of affected party details

Parse with:
```python
import json
df['travel_activities'] = df['travel_activities'].apply(
    lambda x: json.loads(x) if pd.notna(x) else None
)
```

### Geographic Coordinates
Latitude/longitude fields use WGS84 decimal degrees:
- Suitable for GIS mapping
- Can be used with Folium, GeoPandas, ArcGIS
- Some records may have null coordinates

### Avalanche Classification Codes
- **Size:** R1-R5 (relative), D1-D5 (destructive)
- **Type:** SS (soft slab), HS (hard slab), L (loose), WL (wet loose), etc.
- **Trigger:** N (natural), AS (skier), AM (snowmobiler), etc.
- **Aspect:** Cardinal and intercardinal directions

See CAIC classification guide: https://avalanche.state.co.us/

---

**Document Version:** 1.0
**Created:** January 17, 2026
**Author:** SafeAscent Data Collection Project
