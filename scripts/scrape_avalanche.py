#!/usr/bin/env python3
"""
Avalanche Incident Data Scraper

Collects avalanche accident and incident reports from the Colorado Avalanche Information Center (CAIC) API.
Data source: https://avalanche.org / https://api.avalanche.state.co.us

This script fetches comprehensive historical avalanche incident data including:
- Date, location, coordinates
- Casualties (involved, caught, buried, injured, killed)
- Activity/travel mode
- Avalanche technical details (size, trigger, aspect, elevation)
- Detailed narratives and summaries

Usage:
    python scrape_avalanche.py [--start-year YEAR] [--end-year YEAR] [--output FILE]
"""

import requests
import pandas as pd
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
import sys

# API Configuration
API_BASE_URL = "https://api.avalanche.state.co.us/api/v2/reports"
DEFAULT_START_YEAR = 1950  # CAIC has data from 1950-present
DEFAULT_END_YEAR = datetime.now().year
REQUESTS_PER_SECOND = 2  # Rate limiting
PER_PAGE = 100  # Records per API request

def build_api_params(start_date: str, end_date: str, page: int = 1, per: int = PER_PAGE) -> Dict[str, Any]:
    """Build API query parameters."""
    return {
        'r[observed_at_gteq]': start_date,
        'r[observed_at_lteq]': end_date,
        'r[is_locked_eq]': 'false',
        'r[status_eq]': 'approved',
        'r[type_in][]': ['incident_report', 'accident_report'],
        'r[sorts][]': ['observed_at desc', 'created_at desc'],
        'page': page,
        'per': per
    }

def fetch_page(params: Dict[str, Any]) -> tuple[List[Dict], Dict[str, str]]:
    """
    Fetch a single page of results from the API.

    Returns:
        Tuple of (data list, pagination headers dict)
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': '*/*',
        'Origin': 'https://avalanche.org',
        'Referer': 'https://avalanche.org/'
    }

    try:
        response = requests.get(API_BASE_URL, params=params, headers=headers, timeout=30)
        response.raise_for_status()

        pagination_info = {
            'current_page': response.headers.get('current-page', '1'),
            'total_count': response.headers.get('total-count', '0'),
            'total_pages': response.headers.get('total-pages', '1'),
            'page_items': response.headers.get('page-items', '0')
        }

        return response.json(), pagination_info

    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}", file=sys.stderr)
        return [], {}

def extract_incident_data(incident: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract relevant fields from a single incident report.

    Args:
        incident: Raw incident data from API

    Returns:
        Flattened dictionary with key fields
    """
    # Basic info
    extracted = {
        'id': incident.get('id'),
        'type': incident.get('type'),
        'observed_at': incident.get('observed_at'),
        'date_known': incident.get('date_known'),
        'time_known': incident.get('time_known'),
        'water_year': incident.get('water_year'),
        'state': incident.get('state'),
        'latitude': incident.get('latitude'),
        'longitude': incident.get('longitude'),
        'status': incident.get('status'),
        'investigation_status': incident.get('investigation_status'),
        'is_anonymous': incident.get('is_anonymous'),
        'created_at': incident.get('created_at'),
        'updated_at': incident.get('updated_at'),
    }

    # Public report details
    if 'public_report_detail' in incident and incident['public_report_detail']:
        detail = incident['public_report_detail']
        extracted.update({
            'location': detail.get('location'),
            'involvement_summary': detail.get('involvement_summary'),
            'accident_summary': detail.get('accident_summary'),
            'weather_summary': detail.get('weather_summary'),
            'snowpack_summary': detail.get('snowpack_summary'),
            'rescue_summary': detail.get('rescue_summary'),
            'activity': detail.get('activity'),
            'travel_mode': detail.get('travel_mode'),
            'authors': detail.get('authors'),
            'links_media': detail.get('links_media'),
            'links_social_media': detail.get('links_social_media'),
            'closest_avalanche_center': detail.get('closest_avalanche_center'),
        })

    # Involvement summary (casualty counts)
    if 'involvement_summary' in incident and incident['involvement_summary']:
        inv_sum = incident['involvement_summary']
        extracted.update({
            'involved_count': inv_sum.get('involved', 0),
            'buried_count': inv_sum.get('buried', 0),
            'killed_count': inv_sum.get('killed', 0),
        })

        # Extract travel activity breakdown
        if 'normalized_travel_activity' in inv_sum:
            activities = inv_sum['normalized_travel_activity']
            extracted['travel_activities'] = json.dumps(activities) if activities else None

    # Avalanche observations (technical details)
    if 'avalanche_observations' in incident and incident['avalanche_observations']:
        # Take first observation (most incidents have one avalanche)
        avy_obs = incident['avalanche_observations'][0]
        extracted.update({
            'avalanche_aspect': avy_obs.get('aspect'),
            'avalanche_elevation_feet': avy_obs.get('elevation_feet'),
            'avalanche_relative_size': avy_obs.get('relative_size'),
            'avalanche_destructive_size': avy_obs.get('destructive_size'),
            'avalanche_type_code': avy_obs.get('type_code'),
            'avalanche_problem_type': avy_obs.get('problem_type'),
            'avalanche_primary_trigger': avy_obs.get('primary_trigger'),
            'avalanche_secondary_trigger': avy_obs.get('secondary_trigger'),
            'avalanche_angle_average': avy_obs.get('angle_average'),
            'avalanche_crown_average': avy_obs.get('crown_average'),
            'avalanche_weak_layer': avy_obs.get('weak_layer'),
            'avalanche_surface': avy_obs.get('surface'),
        })

    # Affected groups
    if 'affected_groups' in incident and incident['affected_groups']:
        # Aggregate affected groups info
        groups_info = []
        for group in incident['affected_groups']:
            groups_info.append({
                'activity': group.get('normalized_activity'),
                'travel_activity': group.get('normalized_travel_activity'),
                'degree_caught': group.get('normalized_degree_caught'),
                'degree_hurt': group.get('normalized_degree_hurt'),
            })
        extracted['affected_groups'] = json.dumps(groups_info) if groups_info else None

    return extracted

def fetch_year_data(year: int) -> List[Dict[str, Any]]:
    """
    Fetch all incidents for a given avalanche season (year).

    Avalanche seasons run from Sept 1 to Aug 31 of the following year.

    Args:
        year: Starting year of avalanche season (e.g., 2024 for 2024-2025 season)

    Returns:
        List of extracted incident dictionaries
    """
    start_date = f"{year}-09-01"
    end_date = f"{year + 1}-08-31"

    print(f"\nFetching {year}-{year + 1} season...")

    all_incidents = []
    page = 1

    while True:
        params = build_api_params(start_date, end_date, page=page)
        data, pagination = fetch_page(params)

        if not data:
            break

        # Extract relevant fields from each incident
        for incident in data:
            extracted = extract_incident_data(incident)
            all_incidents.append(extracted)

        # Check pagination
        total_pages = int(pagination.get('total_pages', 1))
        total_count = int(pagination.get('total_count', 0))

        print(f"  Page {page}/{total_pages}: {len(data)} incidents (Total: {total_count})")

        if page >= total_pages:
            break

        page += 1
        time.sleep(1.0 / REQUESTS_PER_SECOND)  # Rate limiting

    print(f"  Collected {len(all_incidents)} incidents for {year}-{year + 1}")
    return all_incidents

def main():
    parser = argparse.ArgumentParser(
        description='Scrape avalanche incident data from CAIC API'
    )
    parser.add_argument(
        '--start-year',
        type=int,
        default=DEFAULT_START_YEAR,
        help=f'Starting year for data collection (default: {DEFAULT_START_YEAR})'
    )
    parser.add_argument(
        '--end-year',
        type=int,
        default=DEFAULT_END_YEAR,
        help=f'Ending year for data collection (default: current year)'
    )
    parser.add_argument(
        '--output',
        type=str,
        default='../data/avalanche_accidents.csv',
        help='Output file path (default: ../data/avalanche_accidents.csv)'
    )
    parser.add_argument(
        '--format',
        type=str,
        choices=['csv', 'xlsx', 'json'],
        default='csv',
        help='Output format (default: csv)'
    )

    args = parser.parse_args()

    print("="*70)
    print("Avalanche Incident Data Scraper")
    print("="*70)
    print(f"Data source: CAIC API (api.avalanche.state.co.us)")
    print(f"Date range: {args.start_year}-{args.end_year}")
    print(f"Output file: {args.output}")
    print("="*70)

    # Collect data year by year
    all_data = []
    for year in range(args.start_year, args.end_year + 1):
        year_data = fetch_year_data(year)
        all_data.extend(year_data)

        # Save incrementally (in case of interruption)
        if year_data:
            temp_df = pd.DataFrame(all_data)
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            temp_file = output_path.parent / f"{output_path.stem}_temp{output_path.suffix}"
            temp_df.to_csv(temp_file, index=False)

    # Final save
    if all_data:
        df = pd.DataFrame(all_data)

        # Convert date columns to datetime
        date_columns = ['observed_at', 'created_at', 'updated_at']
        for col in date_columns:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')

        # Sort by observation date
        df = df.sort_values('observed_at', ascending=False)

        # Save to file
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if args.format == 'csv':
            df.to_csv(output_path, index=False)
        elif args.format == 'xlsx':
            df.to_excel(output_path, index=False)
        elif args.format == 'json':
            df.to_json(output_path, orient='records', indent=2, date_format='iso')

        print("\n" + "="*70)
        print(f"SUCCESS: Collected {len(all_data)} incidents")
        print(f"Saved to: {output_path}")
        print("="*70)
        print("\nData summary:")
        print(f"  Date range: {df['observed_at'].min()} to {df['observed_at'].max()}")
        print(f"  Total incidents: {len(df)}")
        if 'killed_count' in df.columns:
            print(f"  Total fatalities: {df['killed_count'].sum()}")
        if 'state' in df.columns:
            print(f"  States covered: {df['state'].nunique()}")

        # Clean up temp file
        temp_file = output_path.parent / f"{output_path.stem}_temp{output_path.suffix}"
        if temp_file.exists():
            temp_file.unlink()
    else:
        print("\nNo data collected.", file=sys.stderr)
        return 1

    return 0

if __name__ == '__main__':
    sys.exit(main())
