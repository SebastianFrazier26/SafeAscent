# Phase 2: Data Platform Design

- **Date:** 2026-09-27
- **Status:** Draft — decisions recorded 2026-09-27 (rev 3)
- **Depends on:** Phase 1 (Alembic baseline, DB roles created via SQL, split Celery, healthchecks.io, the no-scraper CI guard D9). The Cloudflare free proxy is Phase 4 infra; nothing here depends on it.
- **Feeds:** Phase 3 (`2026-09-27-phase3-model-v2-design.md`) and Phase 4 (`2026-09-27-phase4-users-incident-reporting-design.md`). 2a blocks MVP-0/MVP-1; 2b feeds v2.1 onward.
- **Evidence:** `data-audit-2026-09-27.md` (to be committed as `docs/data-audit/2026-09.md`), `ice-alpine-coverage-2026-09-27.md`, and the checks below.

## Goal

Trustworthy, traceable incidents, routes, objectives and weather (2a), then the features Phase 3 needs (2b). Accuracy first, low cost, built in-house.

## Non-goals

- **Any Mountain Project (MP) scraping beyond the one ice/mixed tick pass** (P2-14).
- Displaying or serving any MP data other than ice/mixed route facts; exporting, committing, or sending MP data to third parties (P2-1).
- Committing scraper code to any GitHub repo (P2-15, Phase 1 D9).
- Storing AAC narrative text for new records.
- Scoring bouldering (P2-3).
- Model fitting (Phase 3).
- Any LLM in the pipeline. Jev handles Phase 4 triage only.
- Matching accident records to people or profiles by name (P2-11).

## Verified 2026-09-27

- **DB (read-only `analyst` role)**
  - `mp_routes` has **no stars, votes, or quality columns**.
  - Route coordinates come only from `mp_locations`: 24,784 distinct points in 4,048 0.1° cells; climbing incidents sit in 703 cells.
  - AAC `source_id` rises monotonically with year (median 1990 → 2018 across id buckets). Severity non-null on 2,761/2,763 AAC rows, 100% elsewhere.
  - The DB is 3,547 MB, of which `historical_predictions` is 3,374 MB.
  - The live weather table is **`weather`**, not `weather_patterns` (matches Phase 1 §2.4; only stale docs say `weather_patterns`).
  - Mountaineering/ice/alpine accidents: 989 / 290 / 179; only ~26% of mountaineering and ~23% of ice accidents link to an MP route.
  - About 60 accidents sit at city or park centroids under a wrong mountain (e.g. "Mount McKinley" at 37.8,−122.0 and 47.6,−122.2; "Denali" at 37.8,−119.6; "Grand Teton" at 34.2,−118.6).
- **OpenBeta (live GraphQL and parquet-exporter releases)**
  - 206,241 US climbs, 76,743 of them boulders. Roped flags (overlapping): sport 63,164, trad 61,811, tr 19,387, alpine 6,270, aid 1,403, snow 317, ice 188, mixed 137.
  - Parity or better with MP for rock and rock-alpine (Grand Teton NP 300 vs 298; RMNP rock 523 vs 381). About 1/15 of MP's ice + mixed (325 vs 4,781). Adirondack ice: 1 vs 389.
  - Ice *areas* exist with stable UUIDs (e.g. "Adirondack Ice & Mixed", "Lake City Ice Park", "Winona Ice Park") but mostly hold 0–1 climbs.
  - Crag-level coordinates (Lumpy Ridge: 320 climbs, 20 distinct points). Has pitches, `boltsCount`, discipline flags; no stars, no aspect.
  - Ticks exist in the API (`Climb.ticks`) but are sparse (17 on those 320 climbs) and not in the export.
  - Export is CC0, weekly; totals swing (231,931 on 09-20, 212,874 on 09-27).
  - `bulkImportAreas` is an authenticated mutation; who may call it is not documented.
- **Open-Meteo:** Standard $29/mo for 1M calls; Professional $99/mo for 5M. A request counts as several calls above 10 variables or 14 days.
- **NOAA GOES GLM:** anonymous `s3://noaa-goes16|18|19/GLM-L2-LCFA/YYYY/DDD/HH/` (netCDF, 20 s files). GOES-19 is GOES-East since 2025-04-07 (GOES-16 standby); GOES-18 is West since 2023-01-04. Coverage ±54° latitude, so Denali and most of Alaska are outside. Record from ~2018.
- **NOAA NCEI SWDI NLDN tiles** (checked 2026-09-27, see Lightning below): public daily cloud-to-ground flash counts per 0.1° tile, 1986 → 2026-09.
- **Other:** Blitzortung raw data goes only to station operators, non-commercial, and its archive is by request with unclear terms (not used). EPQS is one point per request. Macrostrat is CC-BY-4.0. USGS GNIS is public domain; Wikidata is CC0. OSM is ODbL. Neon storage $0.35/GB-month. CAIC has no documented JSON API.

## Phase 2a: Data repair

### Principles

- **Backup first.** Before any repair, create Neon branch `pre-2a` and take a `pg_dump` of `accidents`, `weather`, and `mp_ticks` (stored outside the repo).
- **No silent overwrites.**
  - `accidents_raw` is a frozen copy of the table.
  - Every change writes a row to `accident_revisions(id, accident_id, field, old_value, new_value, method, rule_version, run_id, created_at)`.
- **Idempotent scripts** in `backend/app/data/repair/`, keyed on `(accident_id, field, rule_version)`. Excluded rows stay; training reads `accidents_clean`.

**New `accidents` columns:**

- `date_precision` (`day|month|year|unknown`), `year_source`, `year_lo`, `year_hi`
- `geocode_precision` (Phase 3 enum: `exact|crag|area|park_centroid|region_fallback|unknown`), `geocode_method`, `country`
- `activity_class`, `activity_rule_version`, `inclusion_flag` (e.g. `ski_approach_best_effort`)
- `incident_group_id`, `is_canonical`
- `severity_scale`, `excluded_reason`, `source_url`, `updated_at`
- Experience facts (R12): `exp_years_climbing smallint NULL`, `exp_stated_level` (`novice|intermediate|experienced|expert|unknown`), `exp_first_season bool NULL`, `guided` (`guided|unguided|unknown`), `exp_rule_version`

**`accidents_clean`** keeps a row when all of these hold:

- `is_canonical`
- `activity_class IN ('climbing','climbing_approach')`
- `country = 'US'`
- `excluded_reason IS NULL` (rows with `date_precision='unknown'` carry `excluded_reason='year_unverified'`, matching Phase 3)

### Repairs

| # | Issue | Fix | Verification |
|---|---|---|---|
| R1 | AAC year corruption (422 rows ≥2019) | **Rule-based (P2-12).** For each suspect row, take the median year of the 10 nearest trusted rows on each side by `source_id`, and set `year_lo`/`year_hi` from their min and max. Keep the parsed day/month only if `year_lo = year_hi`; otherwise `date_precision='year'`. Spread >2 yr → `unknown` + `year_unverified`. Hand-check 40 rows against the AAC Publications archive (year and title only). No LLM. | The 2023 AAC count is ≤2× the 2015–18 mean. ≥90% of the 40 checked rows are within ±1 year. |
| R2 | Date precision | AAC day-15 → `month`; AAC Jul-1 → `year`; else `day` (R1 overrides). | ~1,567 AAC rows are `month` and ~123 are `year`. A CI test asserts that Phase 3's CM training set is `day`-only. |
| R3a | **City/park-centroid misgeocodes (runs first)** | For each accident whose `mountain` text matches a USGS GNIS summit uniquely (name, plus state when present), compare to its coordinates. If >50 km apart, move it to the summit, `geocode_precision='area'`, `geocode_method='gnis_summit'`. Ambiguous names (several GNIS hits, no state) → coords nulled, `unknown`. Fixes the ~60 known cases ("Mount McKinley" in the Bay Area and Seattle, "Denali" in Yosemite, "Grand Teton" near LA) and any others the rule finds. | No row named for Denali, Rainier, Hood, Shasta, Grand Teton or Mt Washington lies >50 km from its GNIS summit. The ~60-row list is hand-checked. |
| R3 | Other misgeocodes | Fallback-cluster rows (~216) naming foreign places: set `country`, null coords, `unknown`. The 81 "AK" rows below 55°N and the remaining fallback rows: re-geocode offline against GNIS (by mountain + state); no unique hit → `region_fallback` if the state is verified, else `unknown`. NPS rows → `park_centroid`. Rows linked to a route → `crag`. | No `exact`/`crag`/`area` rows remain at the fallback coordinates, and no Alaska-text rows remain below 55°N. 50-row plotted spot-check. |
| R4 | Non-climbing rows | Enum `activity_class`, set by versioned rules. Avalanche `ice_climbing` → `climbing`. A ski or snowmobile row becomes `climbing_approach` (`inclusion_flag='ski_approach_best_effort'`) only if its text matches the climbing lexicon (ice climb, couloir climb, summit attempt, alpine route, rappel, crampon; uphill-skinning "climbing" excluded by a phrase list) **and** it lies within `ski_approach_radius_m` (default 1,000 m, Phase 3 M8) of an alpine, ice or mixed route or objective; otherwise `non_climbing`. NPS `rock_climbing` → `climbing`; other NPS rows → `non_climbing`. | A 60-row hand-labelled golden set scores ≥95% in pytest. |
| R5 | Duplicates | Exact duplicates (48 AAC, 36 NPS) merge automatically. Fuzzy pairs (±2 days, ≤5 km) are recomputed **after R1–R3** and scored (date gap, distance, same type, same severity, name similarity). **Auto-decide (P2-10):** score ≥0.90 → merge; ≤0.40 → distinct. Only the band between goes to the owner as `data/review/duplicates_<run_id>.csv` (a `decision` column: `merge|distinct`), re-imported by `repair.review import`. Canonical row precedence: day precision, then better geocode, then AAC, then lowest id. | No exact duplicates remain among canonical rows; every candidate has an auto or owner decision; 30 auto-merged pairs spot-checked at 100%. |
| R6 | Weather mislinks, unlabelled wind units, empty visibility | Fetch history into `cell_daily_conditions(grid_bucket, date)` from the Open-Meteo archive (`era5_seamless`, `wind_speed_unit=ms`). Accidents join on (cell, date), never on `accident_id`. Old table → `weather_legacy`, then dropped; visibility dropped; wind `*_ms`. | Unit test: 36 km/h = 10 m/s. p99 gust <60 m/s. Old vs new temperature r >0.95 on the correctly linked rows. |
| R7 | `area_weekly_weather` sign error | Negate the single +113.2 longitude point. The table is dropped at 2b-3. | No rows with lon >0. |
| R8 | `mp_ticks` garbage | Add `quarantine_reason`: `future` (after 2026-02-08), `orphan_route`, or `pre_1970`. Nothing is deleted. Internal validation only. | Counts match the audit: 1,322 future, 3,602 orphan ids. |
| R9 | Severity | Add `severity_scale` (NPS records fatalities only). Hand-check 50 AAC rows; if agreement is below 90%, open a rule-based re-extraction task. | Agreement rate recorded. |
| R10 | Legacy links | Copy the 421 rows where `route_id = mp_route_id`; match the other 762 with the 2b matcher into `accident_route_links`; weak matches link to an area or objective instead of a route. Then drop `accidents_route_id_fkey`, `route_id`, `mountain_id`, and the `routes` and `mountains` tables (dumped first). `ascents` and `climbers` were already dropped by Phase 1's `0002`. | No FK points at a legacy table. |
| R11 | Accident refresh, 2024-08 to now | **AAC (P2-12):** facts only (date, place, activity, type, severity, experience facts, URL, own one-line summary) entered by hand from ANAC 2025/2026 and The Prescription into `data/manual/aac_refresh.csv`; ~60–120 rows. **CAIC/avalanche.org:** no documented JSON API, so the fetcher is a scraper: it lives in `~/Developer/safeascent-private/` (never committed, P2-15), runs at ≤1 request per 5 s, and writes a facts-only CSV that the committed `ingest` loader reads. **NPS:** re-pull the same mortality source (a scraper, if it needs HTML parsing, follows the same private-dir rule); log any post-2024 gap. | Feb 5–Jul 2 2026 incident N reported; the `historical_predictions` backtest runs. |
| R12 | Experience facts (P2-11) | Versioned regex/lexicon rules over existing `description` text (internal) fill the experience columns: "N years (of) climbing" → `exp_years_climbing`; "experienced", "expert", "novice", "beginner" → `exp_stated_level`; "first season/year" → `exp_first_season`; "guide(d)", "client", "unguided", "independent" → `guided`. Anything unmatched stays NULL/`unknown`. New AAC rows get the columns from the manual CSV. Phase 4 self-reports feed the same columns. Scoring never requires them. | 80-row golden set: precision ≥95% on filled values (recall reported). A test asserts Phase 3 scoring runs with all experience columns NULL. |

## Phase 2b: Data expansion

### Route catalog: rock from OpenBeta, ice/mixed facts from MP, mountaineering via objectives (P2-1, DECIDED)

**Public (displayed) catalog, by discipline:**

| Discipline | Source | What is displayed |
|---|---|---|
| Rock: `sport`, `trad`, rock `alpine` | **OpenBeta (CC0)**, primary | Everything we ingest (CC0) |
| `ice`, `mixed` | **MP route facts** (`internal.mp_routes`/`mp_locations`, already held) | **Facts only:** route name, grade, location (area name, coordinates), type. Owner's rationale: MP compiled these facts from published guidebooks. OpenBeta ice/mixed climbs are used where they exist and win on a match. |
| Mountaineering and glaciated objectives | **Objective layer** (GNIS, Wikidata, NPS/USFS-curated; see Coverage strategy) | Our own CC0 records |

**MP rules:**

- **Never displayed, served, exported or committed:** MP descriptions, photos, comments, protection/beta text, star ratings, tick details, user names, or any other MP prose; and all MP *rock* data (which stays internal for matching and modeling).
- **Displayed:** ice/mixed route facts only, copied into `canonical_routes`/`canonical_areas` with `source='mp_facts'` and `redistributable=false`. `redistributable=false` rows are shown in the UI and API responses but excluded from any bulk export, dataset release, and OpenBeta contribution.
- **Internal:** everything else under schema `internal` (`mp_routes`, `mp_locations`, `mp_ticks`, `mp_tick_aggregates`, `mp_route_links`), which the `app` (API) role cannot read. Used in aggregate for modeling and for matching accidents and features to catalog routes. Never sent to third parties (Jev included; see Phase 4).
- **Legal background:** MP has no data API, and its owner onX sent OpenBeta cease-and-desist and DMCA notices in 2021, so non-commercial status does not protect us. The owner is consulting a lawyer (Legal questions Q1–Q3).

**Guard rails:**

- CI test: the `app` role has no privilege on schema `internal`.
- The `mp_facts` copy job is the only path from `internal` to public tables; a test asserts it writes only the columns `name, grade, lat, lon, type_group, area name` and only rows whose mapped `type_group` is `ice` or `mixed`.
- CI check `scripts/check_no_mp_data.py` fails if tracked files under `data/`, `tests/fixtures/` or `docs/` contain `mountainproject.com` URLs or `mp_route_id` values.
- Phase 1's `no-scrapers` guard (D9) keeps MP HTML-parsing code out of the repo.
- `mp_*` tables move to schema `internal` in the first 2b migration.

**Ingest**

- **Weekly bulk load:** the latest parquet release. Reject it if the US count drops more than 3%.
- **Deltas:** GraphQL queries for areas with `updatedAt` after the last run.

**Matching** (in-house, `app/pipelines/match.py`). Used to link internal MP routes and accidents to canonical OpenBeta records.

1. **Areas first.** Candidates share an H3 r7 cell or a neighbouring one. Score = 0.5·Jaro-Winkler(name) + 0.3·path-token overlap + 0.2·distance decay (reaching 0 at 2 km).
2. **Climbs within matched areas.** Score = 0.6·name + 0.25·grade + 0.15·discipline.
3. **Thresholds (P2-10):** ≥0.90 and unique links automatically; <0.80 is auto-decided as no link; only 0.80–0.90 goes to the owner as `data/review/matches_<run_id>.csv`. A non-null OpenBeta `mp_id` short-circuits the scoring.
4. **Tuning:** 300 hand-labelled pairs; the auto-link threshold must reach precision ≥0.98.
5. **Rock** MP routes with no OpenBeta match are never promoted into the catalog; accidents on them link to the matched area or objective. **Ice/mixed** MP routes with no OpenBeta match are promoted as `mp_facts` rows; with a match, the OpenBeta row is canonical and the MP link stays internal.

### Route types (P2-3, P2-4, DECIDED)

Each catalog route gets `type_group` ∈ `sport|trad|alpine|ice|mixed|unknown` (five scored types plus `unknown`), set by the versioned mapper `app/pipelines/route_types.py` (`type_rule_version`) from OpenBeta flags, grades, and the MP type (internally for matched rock routes; as the source for `mp_facts` ice/mixed rows). Precedence, first match wins:

1. `mixed` flag or M grade → `mixed`
2. `ice` flag or WI/AI grade → `ice`
3. `alpine` or `snow` flag, or grade IV+ → `alpine`
4. `trad` flag → `trad`
5. `sport` flag, or rock with bolts and no trad flag → `sport`
6. Toprope-only or aid-only: the underlying rock type from rules 4–5 (bolts → `sport`, gear → `trad`); if neither is known → `unknown`
7. Otherwise → `unknown` (stored, not scored; shows "route type unknown")

**Bouldering:** boulder-only climbs are ingested into `canonical_routes` with `is_boulder = true`, `type_group = NULL`, `scored = false` (Phase 4 ticks can reference them). They are excluded from `route_static_features`, `exposure_index` and every scoring table via the `scorable_routes` view (`scored AND type_group <> 'unknown'`).

Target: `unknown` ≤25% of roped routes.

### Coverage strategy and objectives (P2-2, DECIDED)

OpenBeta is at parity or better for rock and rock-alpine but thin for ice, mixed, and snow/glacier mountaineering, exactly where many serious accidents happen (Denali: 149 ice/alpine/mountaineering accidents, 3 OpenBeta climbs in the box; Hood 45, no snow/glacier routes; Shasta 49, none). Decided strategy:

1. **OpenBeta primary** for rock and rock-alpine.
2. **MP facts for ice and mixed** (P2-1): ~4.8K routes, closing the ~1/15 OpenBeta gap (e.g. Adirondack ice 1 → 389).
3. **Objective entity**, separate from Route, for mountaineering and glaciated terrain, so a score can attach where no route-level data exists:
   - **Peaks:** seeded from USGS GNIS summits (public domain), cross-linked to Wikidata QIDs (CC0) for elevation checks and popularity signals.
   - **Ice areas:** OpenBeta's existing ice areas (stable UUIDs), now populated by matched `mp_facts` ice/mixed routes.
   - **Standard mountaineering routes** (e.g. Denali West Buttress, Rainier Disappointment Cleaver, Hood South Side, Shasta Avalanche Gulch): hand-curated from NPS/USFS public information into our own CC0 table, written in our own words. **Never copy MP text.**
   - Scores attach at objective level (per objective × type group) where route-level data is missing; Phase 3 scores objectives with the same `score()` and stores them in `objective_daily_scores`.
4. **Fix misgeocodes first** (R3a), then rank objectives by clean ice/alpine/mountaineering accident count where the catalog (OpenBeta + `mp_facts`) has <5 routes in the discipline.
5. **Hand-curate the top ~30 hotspot objectives** lacking coverage (M2b-2). Seed list, re-ranked by the post-R3a query: Denali (West Buttress, West Rib, Cassin), Foraker, Rainier (DC, Emmons, Kautz, Liberty Ridge), Hood (South Side), Shasta (Avalanche Gulch, Casaval), Baker (Coleman-Deming, Easton), Adams (South Spur), Jefferson, North Sister, Mount Thompson/Snoqualmie, Mt Washington (Huntington and Tuckerman Ravines), Longs Peak, Mount Whitney (Mountaineers Route). Ice venues (Frankenstein Cliff, Cathedral/Whitehorse, Chapel Pond, Poke-O-Moonshine, Ouray, Kelso/Stevens Gulch, Vail, Hyalite) are covered by `mp_facts`; one gets a curated objective only if the post-load coverage query still marks it `thin`.
6. **Contribute back (optional):** push our curated CC0 objective routes (never `mp_facts` rows) to OpenBeta via `bulkImportAreas`, only after OpenBeta maintainers agree.
7. **OSM (optional):** only `natural=peak` / `natural=glacier`, as a separate layer joined at query time and never merged into catalog tables (ODbL share-alike caution; Q5).
8. **Coverage badges** (UI, per area/objective and type group, rules versioned in `coverage_rule_version`):
   - **Route-level:** ≥5 scorable catalog routes of that type group (OpenBeta, `mp_facts`, or curated).
   - **Objective-level:** an objective exists with <5 such routes; the score is the objective's regional/terrain score and is labelled so.
   - **Thin data:** ≥1 clean accident within 5 km, <5 catalog routes, and no curated objective route.
   - Plus the discipline count, and a "lightning: no satellite coverage" flag above 54°N. Thin data shows Phase 3's `insufficient` chip with a coverage warning, never a low score.

**Schema** (all CC0 or public domain, all servable and redistributable):

- `objectives(objective_id uuid PK, kind` (`peak|ice_area|glacier|formation`)`, name, lat, lon, geom geography, elevation_m, gnis_id NULL, wikidata_qid NULL, ob_area_uuid NULL, disciplines text[], coverage_level` (`route|objective|thin`)`, coverage_rule_version, source` (`gnis|wikidata|openbeta|curated`)`, license` (`public_domain|CC0`)`, curated_by NULL, updated_at)`. Unique on `gnis_id` and `ob_area_uuid` when non-null.
- `objective_routes(objective_route_id uuid PK, objective_id FK, name, type_group, grade_text, season_months smallint[], source_url, source_kind` (`nps|usfs|owner`)`, license = 'CC0', curated_at)`. Grades and descriptions are our own wording. Each row is also inserted into `canonical_routes` with `source='safeascent_curated'` so it scores like any route.
- `route_objective_links(route_id FK, objective_id FK, relation` (`on|approach_via|near`)`, method, score, PK(route_id, objective_id))`.
- `accident_route_links` gains `objective_id NULL`, so an accident can link to a route, an area, or an objective.
- `objective_permit_counts(objective_id, year, month NULL, attempts, summits, source_url)`: hand-entered from NPS Denali and Rainier reports and other published permit data.

### Static features (`route_static_features`)

Computed once per distinct point (24,784 plus objective points), not per route. Route coordinates are crag-level, so every feature carries a precision flag.

- **Terrain**
  - **DEM:** USGS 3DEP 1/3″ (~10 m) 1°×1° tiles, cached locally (~300–400 tiles).
  - rasterio/numpy: elevation; slope/aspect by Horn's method.
  - **Crag aspect:** circular mean of cells steeper than 40° within 200 m, with `aspect_confidence` (mean resultant length). Null when fewer than 5 cells qualify.
  - 200-point EPQS check (|Δ| <5 m); Alaska falls back to 2″ (`dem_res_m`). Replaces Open-Elevation.
- **Lithology:** Macrostrat point query (≤5 requests/s, cached), normalized to ~10 classes. When Macrostrat returns nothing, fall back to USGS SGMC polygons in PostGIS.
- **Spatial keys:** H3 r5 and r7, and a `grid_bucket` of 0.1°.

### Dynamic features (`cell_daily_conditions`, `cell_climatology`)

| Source | Use | Cadence and cost |
|---|---|---|
| Open-Meteo forecast | Temperature, precip, snow, wind, gust, freeze-thaw (≤10 vars) | Nightly across ~4–5K cells, ~150K calls/mo, within **Standard, $29/mo** (P2-5) |
| Open-Meteo archive (ERA5 / ERA5-Land) | History and climatology | One-time backfill: incident cells from 1990 (~0.67M units), all cells for the last 3 years (~0.32M), 10-year normals for all cells (~1.05M), about **2.0M units**, so **one month of Professional ($99)**, then Standard (P2-5). Weekly append after that. |
| NRCS SNOTEL | SWE, snow depth, 3-day ΔSWE | Daily, free. Station attached if within 30 km and ±500 m elevation. |
| NWS alerts, plus the IEM VTEC archive for history | Warnings, polygon mapped to cells | Hourly, free |
| AirNow | AQI | Hourly bulk file, free |
| NOAA GOES GLM | Live lightning and daily flash counts | Every 10 min (live) and daily totals; 2018 onward (P2-7) |
| NOAA NCEI SWDI NLDN tiles | Historical cloud-to-ground flash counts | One-time backfill 1989–2017, then monthly as an overlap check (P2-7) |

`cell_climatology(grid_bucket, doy, var, mean, p10, p90, n_years)` is Phase 3's "typical day" reference, and includes lightning-day frequency.

### Lightning (P2-7, DECIDED)

- **Live and 2018+: GOES GLM (DECIDED).** Read `GLM-L2-LCFA` from GOES-19 (East) and GOES-18 (West) anonymously on AWS; history 2018–2025-04 from GOES-16 East.
  - **Live:** a Celery beat task every 10 minutes aggregates the last 30 minutes of flashes into route-bearing grid buckets and writes `lightning_recent(grid_bucket, window_end, flashes)` (24 h rolling). This drives Phase 3's lightning hazard banner.
  - **Daily:** flash counts per bucket per UTC day go to `cell_daily_conditions.lightning_density`.
  - **Coverage:** ±54° latitude. Buckets north of 54° (Denali, most of Alaska) get `lightning_coverage='none'` and NULL density, and the UI shows "lightning: no satellite coverage".
- **1989–2017: NOAA NCEI SWDI NLDN daily tiles (DECIDED; verified usable 2026-09-27).**
  - **What:** daily counts of Vaisala NLDN cloud-to-ground flashes per 0.1° tile, per UTC day. File header: "the count of Vaisala NLDN lightning strikes within a 0.1 degree grid cell for the specified day"; columns `ZDAY,CENTERLON,CENTERLAT,TOTAL_COUNT`; only non-zero tiles listed. The 0.1° tiles line up with our `grid_bucket`.
  - **Access (free, anonymous):** yearly bulk files `https://www.ncei.noaa.gov/pub/data/swdi/database-csv/v2/nldn-tiles-YYYY.csv.gz` (1986–2025; monthly `nldn-tiles-YYYYMM` files for 2026 through 202609), and the REST service `https://www.ncei.noaa.gov/swdiws/csv/nldn/{start}:{end}?stat=tilesum:{lon},{lat}` (returns `DAY,CENTERLAT,CENTERLON,FCOUNT`). Both fetched successfully 2026-09-27.
  - **Date range:** files from 1986, but western US coverage is sparse before 1989 (1987: 0.36M western flashes vs 7.0M east of 104°W; 1988: 1.9M vs 7.2M; 1990: 2.3M vs 13.6M). **Use 1989 onward; lightning is missing (NULL) before 1989.** Alaska: the 2025 file has no tiles north of 54°N, so Alaska stays `lightning_coverage='none'`.
  - **Restrictions:** raw NLDN strike data is restricted to NOAA/government users under NOAA's contract with Vaisala ([NCEI Lightning Products](https://www.ncei.noaa.gov/products/lightning-products), read 2026-09-27; the raw `nldn` endpoint returns HTTP 400 publicly). The daily 0.1° tile summaries are published openly via SWDI (same page). The SWDI metadata's use constraint is "Cite dataset when used as a source", and electronic downloads are free ([SWDI metadata C00773](https://www.ncei.noaa.gov/metadata/geoportal/rest/metadata/item/gov.noaa.ncdc:C00773/html), read 2026-09-27). We store derived counts only, cite SWDI in `DATA_LICENSE.md`, and confirm redistribution of derived values with the legal contact (Q4; low risk, not blocking).
  - **Homogeneity:** NLDN (cloud-to-ground only) and GLM (total lightning) measure different things, and NLDN detection efficiency changed with network upgrades. Store them as **separate columns** (`lightning_cg_nldn`, `lightning_density`) with a `lightning_source`. Phase 3 uses NLDN as a within-cell, within-era anomaly (count relative to that cell's climatology for the same era), not an absolute rate; 2018–2026 overlap years calibrate the two.
  - **Storage:** daily rows only for incident-bearing buckets and their 8 neighbours, 1989–2017 (~1M rows, <0.1 GB). Route-bearing buckets get lightning-day climatology in `cell_climatology` only.
- **Blitzortung: not used.** Raw data goes only to station operators, non-commercial, and its archive is by request with unclear terms. Optional future: host a station.
- Phase 3 M12 uses both: NLDN 1989–2017, GLM 2018+, and "missing, never zero" before 1989 and north of 54°N.

### Exposure: how much climbing happens where (P2-9, DECIDED)

**In plain words:** accident counts only mean something next to how many people climb there. A busy crag produces more reports than a quiet one even if it is no more dangerous. We have no direct count of climber-days. In-app ticks (Phase 4) will not have enough traffic for years, the existing internal MP rock ticks are CA/NV-only, capped at 16 per route, and partly garbage, and OpenBeta ticks are sparse. So the primary measure is a **composite proxy**: several partial signals that each track climbing traffic somewhat, which Phase 3 weighs by fitting them against the data rather than by us picking weights.

**Components** per (area or objective, type group, month), each stored raw plus a missing flag in `exposure_index.proxy` (jsonb, Phase 3 schema), and entering Phase 3 as `log(1 + x)` covariates with estimated coefficients (Phase 3 M5):

| Component | Source | Notes |
|---|---|---|
| `route_density` | Scorable OpenBeta + curated routes | Always present |
| `season_share` | `cell_climatology` | Share of climbable days. Rock: tmax 5–32 °C, precip <2 mm. Ice/mixed: tmax <0 °C on 7 of the prior 10 days. Alpine: both snow and dry-day windows. |
| `ob_ticks` | OpenBeta `Climb.ticks` counts | Sparse; missing-flagged where zero coverage |
| `nps_visits` | NPS IRMA monthly recreation visits | Park cells only |
| `permit_attempts` | `objective_permit_counts` | Denali, Rainier, and others that publish counts |
| `objective_popularity` | Wikidata sitelink count (CC0) for linked objectives | Proxy for how widely known a peak is |
| `in_app_ticks` | Phase 4 aggregates | Later; zero weight until Phase 4 volume allows |
| `mp_tick_count` | Internal `mp_ticks` (quarantine-filtered, rock, CA/NV) | **Optional, internal-only.** Censored at 16 per route; missing-flagged outside CA/NV. Also used as a validation check for rock popularity. |
| `mp_ice_mixed_ticks` | `internal.mp_tick_aggregates` (P2-14) | **Optional, internal-only.** Per (route, year-month, style) counts rolled up to area × type group × month, for `ice` and `mixed` only. |

- **MP components are removable:** both are computed only when `EXPOSURE_ENABLE_MP_TICKS=true` (default true). Removal = drop `internal.mp_tick_aggregates` (and/or quarantine `mp_ticks`), set the flag false, recompute `exposure_index` (the keys become missing-flagged), and retrain through the Phase 3 gate. `exposure_index.proxy` is never serialized by any API endpoint.
- **Uncertainty:** `exposure_lo`/`exposure_hi` at ×/÷3, or ×/÷1.5 where permit counts exist.
- **Validation (reported, not tuned to):** Spearman correlation against permit counts and, internally, against quarantine-filtered CA/NV MP ticks.

### MP ice/mixed tick aggregates (P2-14, DECIDED)

- **Scope:** the ~4.8K `ice` and `mixed` MP routes only; one pass, run now.
- **Politeness:** obeys `robots.txt` (re-read at start; honours its `Crawl-delay: 60`, i.e. one request per 60 s, so the pass takes several days), an honest User-Agent naming SafeAscent and a contact email, no parallelism, stop on any 403/429.
- **What is kept:** aggregates only, per route: total tick count, and counts per (year-month, style). **No usernames, comments, notes or other free text** are stored, even transiently on disk beyond the page being parsed.
- **Where it runs:** the scraper lives in `~/Developer/safeascent-private/` (local only, no git remote, P2-15) and writes a local SQLite file. The committed `ingest` loader later copies it into Neon.
- **Table:** `internal.mp_tick_aggregates(mp_route_id bigint, period text` (`'total'` or `'YYYY-MM'`)`, style text` (`'all'` for totals)`, tick_count int, scrape_run_id, scraped_at, PK(mp_route_id, period, style))`. Isolated: nothing else FKs to it; only the exposure job (`ingest` role, SELECT) reads it.
- **Removal:** one `DROP TABLE` plus retrain (see Exposure). The owner is consulting a lawyer on keeping it (Q1).

## Tables

Each table gets an Alembic revision (`0002+`, run as `migrator`) and a SQLAlchemy 2.0 model using `Mapped[]`.

- **`canonical_areas`:** `area_id uuid PK`, `name`, `parent_id`, `path ltree`, `lat`, `lon`, `geom geography`, `ob_area_uuid NULL`, `objective_id NULL`, `coord_precision`, `source` (`openbeta|mp_facts|safeascent_curated`), `redistributable bool`, `updated_at`.
- **`canonical_routes`:** `route_id uuid PK`, `area_id FK`, `name`, `grade`, `disciplines text[]`, `type_group`, `type_rule_version`, `is_boulder`, `scored`, `pitches`, `length_m`, `bolts`, `ob_climb_uuid UNIQUE NULL`, `source` (`openbeta|mp_facts|safeascent_curated`), `redistributable bool` (false for `mp_facts`), `updated_at`. No MP ids or MP prose columns; the MP link lives in `internal.mp_route_links`.
- **`internal.mp_route_links`:** `route_id FK`, `mp_route_id`, `match_score`, `match_method`. Internal schema only.
- **`internal.mp_tick_aggregates`:** see P2-14.
- **`objective_daily_scores`:** Phase 3 schema (objective-level scores).
- **`objectives`, `objective_routes`, `route_objective_links`, `objective_permit_counts`:** see Coverage strategy.
- **`accident_route_links`:** `accident_id`, `canonical_route_id NULL`, `canonical_area_id NULL`, `objective_id NULL`, `method`, `score`.
- **`route_static_features`:** Phase 3's columns plus `dem_res_m`, `aspect_confidence`, `coord_precision`, `lithology_source`, `feature_version`.
- **`cell_daily_conditions`:** Phase 3's columns (m/s; PK `(grid_bucket, date)`) plus `snow_depth_cm`, `is_forecast`, `model`, `lightning_cg_nldn`, `lightning_source`, `lightning_coverage` (`glm|nldn|none`).
- **`lightning_recent`:** `grid_bucket`, `window_end`, `flashes`; 24 h retention.
- **`exposure_index`:** Phase 3 schema `(area_id, type_group, month, n_routes, proxy jsonb, proxy_version, as_of)` plus `exposure_lo`, `exposure_hi`. Objectives use their `canonical_areas` row.
- **`source_ingest_log`:** `run_id PK`, `source`, `window_start`, `window_end`, `started_at`, `finished_at`, `status` (`ok|rejected|failed`), `rows_in`, `rows_upserted`, `content_sha256`, `validation_report jsonb`, `cost_units`.
- **Model artifacts:** Phase 3 `model_registry.bundle` (Phase 3 M3). Phase 2 creates no artifact table.

## Pipelines

- **No scrapers in the repo (P2-15, DECIDED; Phase 1 D9):** no scraper code is ever committed or published to GitHub (any repo). Anything that fetches and parses HTML pages (the MP tick pass, CAIC/avalanche.org, any NPS page parsing) lives in `~/Developer/safeascent-private/` (local only, no remote) and hands off a local file (CSV or SQLite); the committed `app/pipelines/` loaders only read those files. Clients for open, documented APIs are not scrapers and live in `app/pipelines/`: OpenBeta GraphQL, Open-Meteo, NOAA (GLM on AWS, SWDI), USGS (3DEP, EPQS, GNIS), Macrostrat, NWS, AirNow, SNOTEL, IEM, NPS IRMA, Wikidata.
- **Jobs** (`app/pipelines/<source>.py`): fetch → stage (hash) → validate → upsert (`ON CONFLICT`) → log. Same `(source, window, hash)` is a no-op; backfills are chunked windows resuming from `source_ingest_log`.
- **Validation:** pandera schemas check:
  - value ranges (tmax −60..55 °C, gust 0..75 m/s, precip 0..500 mm, flash counts ≥0)
  - unique, non-null keys
  - coordinates inside the US bounding box (Alaska and Hawaii included)
  - row count within ±5% of the last run

  A failed check means no upsert and a `rejected` status.
- **Scheduling (P2-6, DECIDED):**
  - **Celery beat (time-critical):** forecast 01:00 UTC (before scoring); GLM live every 10 min; NWS alerts and AirNow hourly.
  - **GitHub Actions cron (batch):** OpenBeta weekly, ERA5 append, GLM daily totals and backfill, NLDN backfill, SNOTEL, exposure, objective seeding, review-CSV exports. Logs print counts only.
  - **`ingest` DB role, created via SQL** (Phase 1 role procedure), not `neonctl`, because `neonctl`-created roles are granted `neon_superuser`. Least privilege: INSERT/UPDATE on ingest tables, SELECT on what they read; on `internal`, only INSERT on `mp_tick_aggregates` (the one-time load) and SELECT on `mp_ticks`, `mp_tick_aggregates`, `mp_routes`, `mp_locations` for the exposure, matcher and `mp_facts` jobs. Credentials as an Actions secret and a Railway env var.
- **Monitoring:** healthchecks.io ping per job; `/health/data` staleness (forecast >26 h, alerts >2 h, GLM live >30 min, OpenBeta >9 d); a failed Actions run opens an issue.
- **Edge:** public endpoints sit behind the Cloudflare free proxy (Phase 4 infra, DECIDED); nothing in Phase 2 depends on it.

## Storage (Neon)

- **`historical_predictions` (P2-8, DECIDED):** the nightly kernel scoring run **recomputes and writes every day's scores from that day's live weather forecasts**, so each row is a genuine as-of prediction. Compaction changes the layout only, not the cadence:
  - Layout: `internal.prediction_archive_v1(route_id, month_start, scores smallint[])`, one row per route per month, the array indexed by day of month. Keyed on MP ids, so internal-only.
  - Migration: dump, convert, verify row-for-row, then drop the old table (3.37 GB → ~0.15 GB).
  - After compaction the nightly run upserts that day's slot (`scores[day]`) instead of inserting rows. From Phase 3 MVP-1 on, `route_daily_scores` is the daily record.
- **New tables:** `cell_daily_conditions` ~1.3 GB, `objective_daily_scores` <0.05 GB, `mp_tick_aggregates` <0.01 GB, `cell_climatology` ~0.2 GB, canonical, objective and feature tables ~0.3 GB (boulders included), NLDN rows <0.1 GB.
- **Phase 3 `route_daily_scores`:** keep 400 days of daily rows, then compact the same way.
- **Net:** 3.55 GB → ~3.9 GB, about $1.4/mo.

## Licensing

- **MP:** ice/mixed route facts (name, grade, location, type) displayed, not bulk-redistributed; everything else internal only, including rock data and tick aggregates; never MP prose; never in exports, fixtures, docs, or the repo (P2-1).
- **OpenBeta:** CC0. **Our curated objectives and routes:** CC0, own wording.
- **AAC:** narratives copyrighted, facts not; store facts + URL, existing summaries internal.
- **Public domain** (cite in `DATA_LICENSE.md`): CAIC, NPS, USGS (3DEP, GNIS, SGMC), NOAA/NWS (GOES GLM, SWDI NLDN tiles — "cite dataset"), NRCS, EPA, IEM. **Wikidata:** CC0. **Open-Meteo, Macrostrat:** CC-BY attribution. **OSM:** ODbL, separate query-time layer only. **Blitzortung:** not used.

### Legal questions for the owner's legal contact

1. May we keep the ice/mixed MP tick aggregates (`internal.mp_tick_aggregates`: per-route and per-month/style counts only, no user data), for internal exposure modeling, never displayed or redistributed? (Removal is one table drop plus retrain.)
2. May we display MP ice/mixed route facts (name, grade, location, type), given MP compiled them from published guidebooks, and with no MP prose, photos or comments?
3. Is continued internal use of the MP data already scraped (rock and ice, modeling and matching only) acceptable?
4. May we publish scores derived from NOAA SWDI NLDN tile counts (Vaisala-sourced, publicly released by NOAA)?
5. Does a query-time OSM peaks/glacier layer keep our catalog and scores outside ODbL share-alike?
6. Do AAC Publications terms permit the manual facts CSV (date, place, type, severity, URL)?

### Open items (owner decisions pending)

- **Git history rewrite** (Phase 1 open item 1): the public repo's history holds 18 old scraper files and a leaked Neon password. A `git filter-repo` rewrite plus force push is the owner's call and is not assumed done.

## Testing

- **Unit:** R1 interpolation, R2 rules, R3a GNIS distance rule, R4 golden set, R5 scoring and auto-decision bands, R12 experience golden set, route-type precedence (toprope/aid map to underlying rock type; boulders unscored), coverage-badge rules, wind conversion, `mp_facts` copy writes only fact columns and only ice/mixed rows, Horn aspect (45° north plane → 0±1°), circular mean, matcher precision, NLDN tile parsing (lon/lat column order), GLM ±54° coverage flag, exposure monotonicity, MP tick components absent (missing-flagged) when the flag is off or the table is dropped.
- **Pipelines:** recorded responses (no network), pandera rejection, idempotency (second run upserts 0), review CSV export → import round-trip. Migrations up/down on a Neon branch; `alembic check` clean.
- **Guards:** `app` role has no privilege on `internal`; `check_no_mp_data.py` and `check_no_scrapers.py` in CI; no MP rows in fixtures; no API response schema contains an MP prose field.
- Every verification cell above is a `pytest -m db` test (analyst, Neon branch).

## Milestones

| M | Scope | Acceptance |
|---|---|---|
| 2a-0 | Backup, `accidents_raw`, revisions table, new columns | Applied on a branch, then prod. Restore rehearsed. |
| 2a-1 | R1, R2, R9 | Verifications pass. AAC check ≥90%. |
| 2a-2 | R3a, R3–R5, R12, `accidents_clean` | Golden sets pass. Uncertain duplicate pairs decided via CSV. Re-running the audit clears red flags 1–4. |
| 2a-3 | R6–R8, incident-cell ERA5 backfill | Every clean `day` incident has a 7-day window. `weather` dropped. |
| 2a-4 | R11 | Refresh loaded. Feb–Jul 2026 N reported. Phase 3 MVP-0 unblocked. |
| 2b-1 | OpenBeta catalog, `internal` schema move, matcher, route types, `mp_facts` ice/mixed load, `mp_tick_aggregates` load from the local SQLite | Precision ≥0.98. `unknown` ≤25%. MP guards green. ~4.8K ice/mixed routes displayable with facts only. |
| 2b-2 | R10, static features, objectives seeded (GNIS, Wikidata, OpenBeta ice areas), top ~30 hotspot objectives curated | 100% elevation coverage. EPQS check passes. Legacy tables dropped. Every seed-list objective has ≥1 curated route and a coverage badge. |
| 2b-3 | Dynamic feeds, GLM live + daily, NLDN backfill, climatology | 7 consecutive nightly runs `ok`. Staleness alert drilled. NLDN–GLM 2018–2025 overlap correlation reported. |
| 2b-4 | Exposure v1 | Spearman reported. Uncertainty columns populated. Exposure recomputes cleanly with the MP components disabled (removal drill). |
| 2b-5 | Prediction compaction | Archive verified. Nightly upsert into the archive works. DB ≤4.5 GB. |
| 2b-6 (optional) | Contribute curated ice routes to OpenBeta | Only after maintainers agree. |

## Owner decisions (recorded 2026-09-27, rev 3)

1. **P2-1 Route catalog:** rock (sport, trad, rock-alpine) from OpenBeta (CC0), public primary; ice and mixed from MP route facts (name, grade, location, type; displayed, not bulk-redistributed); mountaineering/glaciated via the Objective layer; MP rock data and all MP prose, photos and comments never displayed. DECIDED.
2. **P2-2 Coverage strategy:** OpenBeta for rock, MP facts for ice/mixed, Objective entity (GNIS + Wikidata + OpenBeta ice areas + curated CC0 standard routes), misgeocode fix first, top ~30 hotspot curation, optional OpenBeta contribution (curated rows only) and OSM layer, coverage badges. DECIDED.
3. **P2-3 Bouldering:** ingested flagged `scored=false`, excluded from scoring tables. DECIDED.
4. **P2-4 Route types:** sport, trad, alpine, ice, mixed, plus `unknown` (unscored); toprope and aid map to the underlying rock type. DECIDED.
5. **P2-5 Open-Meteo:** one month of Professional ($99) for the backfill, then Standard ($29/mo). DECIDED.
6. **P2-6 Scheduling:** Celery beat for time-critical feeds, GitHub Actions for batch; `ingest` role created via SQL. DECIDED.
7. **P2-7 Lightning:** NOAA NCEI SWDI NLDN daily 0.1° tiles for 1989–2017 (free, "cite dataset"), GOES GLM for 2018+ and live; missing (not zero) before 1989 and north of 54°N. DECIDED. Legal Q4 is a confirmation, not a blocker.
8. **P2-8 `historical_predictions`:** compact storage; still recomputed and written every day from live forecasts by the nightly run. DECIDED.
9. **P2-9 Exposure:** composite proxy primary; rock popularity from OpenBeta ticks, composite proxies and existing internal MP ticks; optional, removable internal MP components. DECIDED.
10. **P2-10 Review queues:** clear duplicate and match cases auto-decided; only uncertain ones go to the owner as CSV. DECIDED.
11. **P2-11 Experience:** structured optional facts extracted from accident records; scoring works without them; no name matching. DECIDED.
12. **P2-12 AAC:** year repair via `source_id` neighbours plus a 40-row hand check (no LLM); refresh via a manual facts CSV. DECIDED.
13. **P2-13 Cloudflare:** free proxy in front of the site (Phase 4 infra). DECIDED.
14. **P2-14 MP ice/mixed ticks:** one polite pass (robots.txt, Crawl-delay 60 s, honest UA), aggregates only, isolated `internal.mp_tick_aggregates`, loaded from local SQLite by `ingest`; keeping it pending the owner's lawyer (Q1). DECIDED.
15. **P2-15 No scrapers in any repo:** scrapers live only in local private dirs with no remote; open-API clients may live in the repo. DECIDED.
