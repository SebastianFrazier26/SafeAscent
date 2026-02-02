#!/usr/bin/env python3
"""
The Mountaineers Incident Reports Downloader

Downloads PDF incident reports from The Mountaineers organization.
Creates an index of available reports for manual review.

Data source: https://www.mountaineers.org/about/safety/safety-reports

Usage:
    python scrape_mountaineers.py [--download] [--output-dir DIR]
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
from pathlib import Path
import argparse
import time
import sys
from urllib.parse import urljoin

BASE_URL = "https://www.mountaineers.org/about/safety/safety-reports"

def fetch_report_links():
    """Fetch all PDF report links from the safety reports page."""
    print("Fetching report links from The Mountaineers safety reports page...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }

    try:
        response = requests.get(BASE_URL, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # Find all PDF links
        report_links = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            if '.pdf' in href.lower() or 'download/file' in href:
                full_url = urljoin(BASE_URL, href)
                text = link.get_text(strip=True)

                # Try to extract year and period from URL or text
                year = None
                period = None

                # Extract from URL path
                parts = href.split('/')
                for part in parts:
                    if part.isdigit() and len(part) == 4:
                        year = part
                    elif any(month in part.lower() for month in ['january', 'march', 'june', 'september', 'december', 'q1', 'q2', 'q3', 'q4']):
                        period = part

                report_links.append({
                    'title': text,
                    'url': full_url,
                    'year': year,
                    'period': period
                })

        print(f"Found {len(report_links)} report links")
        return report_links

    except requests.exceptions.RequestException as e:
        print(f"Error fetching page: {e}", file=sys.stderr)
        return []

def download_pdf(url: str, output_path: Path) -> bool:
    """Download a single PDF file."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }

    try:
        response = requests.get(url, headers=headers, timeout=60, stream=True)
        response.raise_for_status()

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return True

    except Exception as e:
        print(f"  Error downloading {url}: {e}", file=sys.stderr)
        return False

def main():
    parser = argparse.ArgumentParser(
        description='Download The Mountaineers incident reports'
    )
    parser.add_argument(
        '--download',
        action='store_true',
        help='Download PDF files (otherwise just create index)'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default='../data/mountaineers_reports',
        help='Directory to save PDF files (default: ../data/mountaineers_reports)'
    )
    parser.add_argument(
        '--index-output',
        type=str,
        default='../data/mountaineers_reports_index.csv',
        help='Output CSV index file (default: ../data/mountaineers_reports_index.csv)'
    )

    args = parser.parse_args()

    print("="*70)
    print("The Mountaineers Incident Reports Scraper")
    print("="*70)

    # Fetch report links
    report_links = fetch_report_links()

    if not report_links:
        print("No reports found.", file=sys.stderr)
        return 1

    # Create index
    df = pd.DataFrame(report_links)
    df = df.sort_values(['year', 'period'], ascending=[False, False])

    # Save index
    index_path = Path(args.index_output)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(index_path, index=False)
    print(f"\nIndex saved to: {index_path}")
    print(f"Total reports cataloged: {len(df)}")

    # Download PDFs if requested
    if args.download:
        print(f"\nDownloading PDFs to: {args.output_dir}")
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        success_count = 0
        for idx, row in df.iterrows():
            # Create filename from year and title
            year = row['year'] if row['year'] else 'unknown'
            period = row['period'] if row['period'] else 'report'
            filename = f"{year}_{period}_{idx}.pdf"
            filename = filename.replace(' ', '_').replace('/', '-')

            output_path = output_dir / filename

            if output_path.exists():
                print(f"  Skip (exists): {filename}")
                success_count += 1
                continue

            print(f"  Downloading: {filename}")
            if download_pdf(row['url'], output_path):
                success_count += 1
                time.sleep(1)  # Be respectful with rate limiting

        print(f"\nDownloaded {success_count}/{len(df)} reports successfully")

    print("\n" + "="*70)
    print("Note: The Mountaineers reports are in PDF format.")
    print("Manual review is recommended for extracting structured incident data.")
    print("Reports contain incidents organized by activity type (climbing, hiking, etc.)")
    print("="*70)

    return 0

if __name__ == '__main__':
    sys.exit(main())
