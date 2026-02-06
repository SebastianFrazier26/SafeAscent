# SafeAscent Data Sources

**Last Updated:** February 2026

---

## Overview

SafeAscent aggregates climbing accident data from multiple authoritative sources to power safety predictions. All data is stored in a Neon PostgreSQL database with PostGIS for spatial queries.

---

## Data Summary

| Source | Records | Geocoded | Coverage |
|--------|---------|----------|----------|
| **AAC** (American Alpine Club) | 2,770 | 99.9% | 1990-2019 |
| **Avalanche.org** (CAIC) | 1,372 | 100% | 1997-2026 |
| **NPS** (National Park Service) | 848 | 77% | Various |
| **Mountain Project** | ~168,000 routes | 100% | Current |
| **Total Accidents** | **~6,900** | **96%** | **1990-2026** |

---

## Accident Data Sources

### American Alpine Club (AAC)
- **Coverage:** 1990-2019 climbing accidents
- **Scope:** All climbing disciplines (alpine, trad, sport, ice)
- **Processing:** Gemini 2.0 Flash for field extraction, Google Maps API for geocoding
- **Quality:** Detailed narratives with lesson-focused analysis

### Avalanche.org (CAIC)
- **Coverage:** 1997-2026 avalanche incidents
- **Scope:** 15 U.S. states with avalanche activity
- **Data:** Technical avalanche details (size, trigger, aspect, weak layer)
- **Quality:** Standardized data collection, regular updates

### National Park Service (NPS)
- **Coverage:** Mortality incidents in U.S. National Parks
- **Scope:** All causes of death (filtered for climbing-related)
- **Quality:** Official government records

---

## Route Data

### Mountain Project
- **Routes:** ~168,000 climbing routes across the United States
- **Locations:** ~45,000 areas in hierarchical structure
- **Data:** Name, grade, type, coordinates, length, pitches
- **Excluded:** Boulder routes (different risk profile)

---

## Weather Data

### Open-Meteo Historical Archive
- **Purpose:** 7-day weather windows for each accident
- **Variables:** Temperature, wind, precipitation, visibility, cloud cover
- **Resolution:** 1km grid (coordinates rounded to 0.01°)
- **Records:** ~25,000 weather observations

---

## Data Quality Notes

### Geocoding Accuracy
- **AAC:** 99.9% successfully geocoded (Google Maps + Gemini fallback)
- **Avalanche.org:** 100% (coordinates provided in source)
- **NPS:** 77% (some records lack specific locations)

### Date Coverage
- Dates recovered for AAC using publication year - 1 + month + 15th
- ~4,800 accidents have both coordinates and dates (weather-ready)

### Duplicate Handling
- Some incidents may appear in multiple sources
- Deduplication via date + location + casualty count matching

---

## Database Schema

See [DATABASE_STRUCTURE.md](./DATABASE_STRUCTURE.md) for detailed table schemas and query patterns.

---

## Legal & Attribution

All data is from publicly accessible sources. When using this data:

1. **Cite original sources** (AAC, CAIC/Avalanche.org, NPS)
2. **Respect privacy** for fatal incidents
3. **Use aggregated analysis** when possible

**Example Citation:**
```
American Alpine Club. Accidents in North American Climbing.
Colorado Avalanche Information Center. U.S. Avalanche Accident Reports.
National Park Service. Mortality and SAR Data.
```

---

*SafeAscent - Climbing Safety Through Data*
