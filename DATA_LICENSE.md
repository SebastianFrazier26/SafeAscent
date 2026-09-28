# Data licensing

**This is a summary, not legal advice.** It documents the licensing terms SafeAscent believes apply to each data source as of the date below, pending review by the owner's lawyer.

The code in this repository is licensed under Apache-2.0 (see `LICENSE`). That license covers **code only**. Data follows the rules below.

## OpenBeta

OpenBeta climb and area data is published under **CC0 1.0** (public domain dedication). SafeAscent may store, display, and redistribute it. We credit OpenBeta as a courtesy, though CC0 does not require it.

Verified 2026-09-28:
- [OpenBeta/climbing-data README](https://github.com/OpenBeta/climbing-data/blob/main/README.md) — License section links to the repo's `LICENSE` file, "Creative Commons CC0".
- [openbeta.io/about](https://openbeta.io/about) — "All climbing content (excluding photos) is available under the Creative Commons Public Domain license."

Note: OpenBeta's CC0 dedication covers climbing content; photos are explicitly excluded per openbeta.io/about. SafeAscent does not use OpenBeta photos.

## Mountain Project

- **Ice and mixed route facts** (route name, grade, location as area name and coordinates, and route type) may be **displayed** in SafeAscent as facts. They are not bulk-redistributed: no exports, downloads, or public datasets.
- **All other Mountain Project-derived data** (every `mp_*` table, including rock routes, descriptions, and tick aggregates) is **internal only**. It is never displayed, exported, sent to third parties, or committed to any repository.
- No Mountain Project prose (descriptions, comments, beta) is ever displayed or committed.
- Mountain Project data never appears in fixtures, tests, docs, screenshots, or commits.
- This repository does not redistribute Mountain Project data and claims no license or right to it.

## Accident and weather sources

Accident records (AAC, Avalanche.org/CAIC, NPS) and weather data (Open-Meteo) live in the production database. They are not committed to this repository. Each source's own terms govern reuse; nothing here grants rights to them.

## User-submitted incident facts (future, Phase 4)

Facts submitted by users about incidents are planned to be released under **CC0**. This section is forward-looking; the feature does not exist yet.

## No scrapers

No scraper code is committed to any GitHub repository. Open-API clients (OpenBeta GraphQL, Open-Meteo, NOAA, USGS, Macrostrat, NWS, AirNow, SNOTEL) are fine. CI's `guards` job (`scripts/check_no_scrapers.py`) enforces this.
