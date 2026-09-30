# Phase 2a Foundations (PR 2a-0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the data-platform floor every later Phase 2 plan stands on: boundary validation with quarantine, the ingest run log, the `internal` schema, the Phase 2a accident columns and frozen raw copy, the `ingest` and `trainer` roles, the R8 tick quarantine, and the loader for the privately scraped MP ice/mixed tick aggregates.

**Architecture:** A typed `app/pipelines/` package holds pure validation (`validate.py`), the 0.1° grid key (`grid.py`), the run log and quarantine writer (`ingest_log.py`) and the first two jobs (`mp_ticks_quarantine.py`, `mp_tick_aggregates.py`). Jobs are CLIs (`python -m app.pipelines.<job>`) that connect as the `ingest` role and are run as explicit owner/agent steps or by GitHub Actions; nothing runs at app startup. One Alembic revision (`0004`) adds the schema; roles and grants are owner-run SQL with SCRAM verifiers, following `create_roles.sql`.

**Tech Stack:** Python 3.12, uv, SQLAlchemy 2.0 async + asyncpg, Alembic 1.14, pydantic 2 (already a dependency), pytest + pytest-asyncio, ruff 0.8.4, mypy; Postgres 16 + PostGIS on Neon; psql 16 for role scripts.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` (binding), amended by `docs/superpowers/specs/2026-09-28-phase3-amendment-similarity-confidence.md` (D1–D5, §3.1). Phase 3 consumer contract: `docs/superpowers/specs/2026-09-27-phase3-model-v2-design.md` (P3:150–161).

**Revised:** 2026-09-28 after the plan review (`.superpowers/sdd/2026-09-27-phase1b-foundations-pr5-8/phase2-plan-review.md`) and the owner decisions below.

---

## Defaults pending owner confirmation

The owner had not yet decided these on 2026-09-28. Each default is the option most consistent with "accurate safety results first, then security, then low cost". Every plan implements the default; the places that depend on one are marked `[default pending: DPn]`. If the owner picks the alternative, only the marked tasks change.

| # | Question | Default (implemented) | Alternative | Why the default |
|---|---|---|---|---|
| DP1 | ERA5 incident backfill start year (plan 3) | 1940-01-01, the earliest ERA5 day in the Open-Meteo archive. Days before 1979 are pre-satellite reanalysis; the date alone identifies them, and the docs say so. Pre-1940 incidents stay `conditions_missing` (NULL, never 0). | Start in 1990 (the spec's cost figure). Pre-1990 incidents are flagged `conditions_missing`. | Every clean incident with a date gets its case-crossover windows, so old incidents are not silently dropped. The extra units fit inside the Professional month's 5M. |
| DP2 | Toprope- or aid-only climbs with no bolt information (plan 4) | `unknown` type group. Spec rule 6 says "if neither is known → `unknown`", and the catalog has no gear flag. | Map them to `trad`. | Guessing a type scores the route against the wrong peer group. |
| DP3 | Lightning windows (plan 7) | NLDN SWDI tiles from 1989-01-01 through the latest published month, appended monthly. This also covers GLM's 2018-01-01..2018-02-12 gap. GLM from 2018-02-13. Before 1989: NULL, because the network was not yet national and counts would read low. Overlap calibration uses every full year where both exist (2018 onward). | (a) NLDN from 1986, including the pre-national years, with a coverage flag. (b) NLDN 1989–2017 only, with NULL for 2018-01-01..02-12. | One homogeneous cloud-to-ground series over the longest reliable span. Incomplete early coverage would read as "little lightning", which reads as safe. |
| DP4 | Coverage badge when a place has 1–4 scorable routes (plan 6) | `thin`, with the reason `few_routes` recorded. Never `route`. | The original plan: `route`. | The spec's `route` badge needs ≥5 routes. Amendment D1 forbids thin evidence reading as confident. |
| DP5 | Timing of the first Open-Meteo Professional window (plan 3) | Buy it when plan 3's runbook is ready. Yearly windows follow each January. | Wait for January 2027 and do the first backfill then. | MVP-0 is not held back 3+ months. The yearly January window is bought either way. |

Prices and limits were read on 2026-09-28. Re-check them before any purchase.

## Owner decisions 2026-09-28 (binding; they supersede the recommendations further down where they differ)

- **Open-Meteo.** Buy **one** Professional month ($99) per year, in January, started on or after Jan 10 so ERA5's ~5-day lag has cleared.
  - That window batch-appends the prior year's ERA5 days and recomputes the monthly normals and day-of-year climatology. It also gives normals and 3-year history to every grid bucket first seen since the last window.
  - Between windows, recent days come from the Forecast API's `past_days`. This is a clearly flagged non-ERA5 stopgap (`cell_daily_conditions.record_kind = 'stopgap'`). The January batch replaces stopgap rows with ERA5.
  - There is **no weekly ERA5 append**. A bucket without normals is `pending` (missing), never 0.
  - Spec P2-5 ("one Professional month, then Standard; weekly append") is wrong on one point: the Standard tier excludes the historical/archive API (Open-Meteo pricing FAQ, read 2026-09-28). The spec is not edited here; these plans carry the correction.
  - The nightly forecast (and its `past_days`) runs on the paid Standard plan the spec already budgets for the forecast. The free tier is non-commercial only.
- **OpenBeta stays the primary rock catalog source.** `ob_ticks` stays missing (D17), and plan 4 adds a one-time stratified sample (~1–2K climbs) that measures OpenBeta tick coverage, with an explicit revisit trigger for P2-9.
- **Private MP crawler (backup, outside the repo).** A private crawler in `~/Developer/safeascent-private` (never on GitHub) collects rock route facts and tick aggregates (month × style counts; no usernames, no prose) as a backup, beside the existing private ice/mixed tick job. Ingesting its outputs is a separate owner-gated task (legal review pending) and is not part of these plans. No repo code references the MP site.
- **Weather day = the crag's local calendar day** in the point's IANA timezone, not UTC.
  - Daily conditions are keyed by series `(grid_bucket, tz)`, and Open-Meteo is requested with `timezone=<tz>`.
  - Accidents carry `tz`. GLM flashes are bucketed into local days.
  - NLDN tiles are UTC days and cannot be localised. They are stored with `day_basis = 'utc'`, and a local day L is matched to UTC days {L, L+1}.
  - "Future" still means after the current UTC day at run time. US local days never run ahead of UTC.

---

## How Phase 2 is split, and why

The spec is too large for one PR or one plan, so it is cut into eight plans. Each produces working, tested software on `main`. The order puts the Phase 3 critical path first: MVP-0 needs 2a, and MVP-1 needs 2a plus the similarity features the amendment pulled forward.

The **Needs** column lists the plans each plan must have merged and applied. Because Alembic history is linear, every plan also sits after all lower-numbered plans (see the paragraph after the table). The content dependency is named in brackets.

| # | File | Spec milestones | Produces | Needs |
|---|---|---|---|---|
| 1 | `2026-09-28-phase2a-foundations.md` (this) | 2a-0, R8, P2-14 load | validation core, `grid_bucket_key`, run log, `internal` schema, accident columns, `accidents_raw`, roles, tick quarantine, tick-aggregate loader | Phase 1 merged |
| 2 | `2026-09-28-phase2a-accident-repair.md` | 2a-1, 2a-2 | R1–R5, R9, R12, GNIS summits, `accidents_clean` | 1 |
| 3 | `2026-09-28-phase2a-conditions-refresh.md` | 2a-3, 2a-4, P2-5, amendment §3.1 normals | Open-Meteo archive client, `localday`, `grid_bucket_series`, `cell_daily_conditions`, incident backfill, **monthly normals + day-of-year climatology** (moved here from plan 5 so they fall inside the Professional window), `era5_window` January batch, R6 (build + verify), R7, R11 loaders | 1, 2 (`accidents_clean`) |
| 4 | `2026-09-28-phase2b-catalog.md` | 2b-1 | OpenBeta catalog, route types, matcher, `mp_facts`, MP schema split, `check_no_mp_data.py`, OpenBeta tick-coverage sample | 1, 2 (`textsim`/`geo`), 3 (chain) |
| 5 | `2026-09-28-phase2b-similarity-features.md` | 2b-2 (part), amendment §3.1 | point elevation (3DEP), `feature_points` (with `tz`), `route_static_features` MVP-1 columns, scoring-unit features view, R10 relink job + first run, `internal.r10_unresolved` | 3 (normals tables, `grid_bucket_series`), 4 (`0010` FKs `canonical_routes`; points read `canonical_areas`) |
| 6 | `2026-09-28-phase2b-objectives-coverage.md` | 2b-2 (rest), R10 drop | objectives, curated routes, coverage badges, R10 re-run, guarded legacy drop `0012` | 2, 4, 5 (R10 job) |
| 7 | `2026-09-28-phase2b-live-feeds-lightning.md` | 2b-3 | nightly forecast + `past_days` stopgap, alerts, AQI, SNOTEL, GLM live/daily, NLDN backfill + monthly append, lightning-day frequency, `/health/data` | 3, 5 (`feature_points`), 6 (chain on `0012`) |
| 8 | `2026-09-28-phase2b-exposure-compaction.md` | 2b-4, 2b-5 | `exposure_index` v1, prediction archive compaction | 4, 5, 6, 7 |

Alembic history is linear, so plans **merge** in the numeric order of their revisions:

`0004_phase2a_foundation` (plan 1) → `0005_gnis_and_duplicate_decisions`, `0006_accidents_clean` (plan 2) → `0007_cell_daily_conditions` (plan 3) → `0008_catalog`, `0009_mp_ticks_internal` (plan 4) → `0010_static_features` (plan 5) → `0011_objectives`, `0012_drop_legacy_routes` (plan 6) → `0013_live_feeds` (plan 7) → `0014_exposure`, `0015_prediction_archive` (plan 8).

Plan 5 no longer owns a drop migration. The guarded legacy drop moved to plan 6, so R10 can be re-run after plan 6's objective areas exist and before the columns go. A plan developed in parallel must rebase its first revision's `down_revision` onto the current head (and renumber) before merging.

Why this cut:
- Each plan is one reviewable PR (plans 2, 3 and 7 are two PRs each, marked inside) and owns its own migration(s).
- Each ends with owner/agent runbook tasks that put its data in Neon.
- Plan 4 cannot run ahead of plan 3. It imports plan 2's helpers and chains on `0007`.
- Plan 5 needs the catalog: `0010` foreign-keys `canonical_routes`, and points are read from `canonical_areas`.
- 2b-6 (optional OpenBeta contribution) needs maintainer agreement and has no plan.

## Refresh cadence (every feed, one table) `[D8]`

"Future" is always relative to the run-time UTC day. The owning plan's cron line is authoritative for the exact minute; this table fixes the frequency and the rule.

| Data | Cadence | Mechanism (owning plan) | Rule |
|---|---|---|---|
| OpenBeta catalog | weekly | GitHub Actions `catalog-weekly.yml` (plan 4) | Rows unseen in a successful full load get `retired_at`. A load that drops the US total by >3% is rejected. |
| Open-Meteo forecast | nightly 01:00 UTC | Celery beat on the ingest service (plan 7) | Future days are `record_kind='forecast'`. |
| Open-Meteo `past_days` stopgap | nightly, same request as the forecast (`past_days=3`) | plan 7 | Past days are `record_kind='stopgap'`. They replace forecast rows and never replace `era5` rows. |
| ERA5 Professional window | yearly, one 30-day window starting on or after Jan 10; the first window per DP5 | `python -m app.pipelines.era5_window run` (plan 3), owner-started | Appends ERA5 from each series' `last_era5_date` to `today − 5` and replaces stopgap/forecast rows. Adds 3-year history for new series. Runs the incident backfill for new clean accidents. Prunes non-incident ERA5 rows older than 3 years. The `--max-units` budget is cumulative per window. |
| Monthly normals + day-of-year climatology | rebuilt in every January window from the trailing 10 complete calendar years | `era5_window` → `normals` (plan 3) | A bucket first seen between windows is `cell_normals_status.status = 'pending'` (missing, never 0) until the next window. |
| Elevation / static features | once per new point, after each catalog load | `static_features points|elevation|routes` (plan 5) | Transient HTTP errors are retried, never stored as `no_tile`. |
| GNIS summits | quarterly | plan 2 loader (plan 6 re-seeds objectives) | |
| MP tick quarantine (R8) | on every tick load and weekly | `data-weekly.yml` (plan 7) runs `mp_ticks_quarantine` | Relative to `LEAST(today, capture day + 1)`. |
| MP ice/mixed tick aggregates | after each completed private scrape | owner runs `mp_tick_aggregates` (plan 1) | The scrape month and later stay `partial_month`. Closed months are INSERT-only; changed counts are quarantined `count_changed`. |
| `mp_tick_counts` | weekly after R8 | `data-weekly.yml` (plans 4/7) | |
| Accidents (R1–R5, R9, R12) | on each refresh load | plan 2 jobs, re-run by plan 3's R11 runbook | R1/R2 touch only ids present in `internal.accidents_raw`. |
| R10 legacy relink | once in plan 5; re-run after plan 6's data load, before `0012` | plan 5 job, plan 6 runbook | Unresolved rows go to `internal.r10_unresolved`; nothing is nulled. |
| NWS alerts | hourly (:05) | beat (plan 7) | "Not collected" is distinct from "no alert". |
| AirNow AQI | hourly (:35) | beat (plan 7) | Every run is logged, including failures. |
| SNOTEL | daily | `data-daily.yml` (plan 7) | Stations selected by `stationTriplets=*:*:SNTL`. |
| GLM live | every 10 minutes | beat (plan 7) | Every run is logged. Consumers require freshness; no data never reads as "no lightning". |
| GLM daily totals | daily | `data-daily.yml` (plan 7) | Non-zero local-day totals plus coverage periods; an incomplete hour → NULL/`none`. |
| NLDN tiles | monthly append (3rd of the month) | `data-weekly.yml` monthly schedule (plan 7) | Only new months are downloaded. Pre-1989 → NULL (DP3). |
| Exposure index | monthly, with a training `cutoff` parameter | plan 8 | Missing components are flagged, never 0. |
| Prediction fold | daily, outside the nightly scoring window | plan 8 | Folds rows older than 7 days into `prediction_archive_v1`. The archive keeps 400 days. |
| OpenBeta tick-coverage sample | once (plan 4 runbook), repeated only when its revisit trigger fires | plan 4 | `ob_ticks` stays missing until the trigger fires (D17). |

## Decisions (owner) — read before starting any Phase 2 plan

Each item is a choice the spec leaves open or that later owner decisions reopened. The review verdicts of 2026-09-28 are folded in. Every plan implements the **Decision** line, and each place that depends on it is marked `[assumes D<n>]`.

**D1. Boundary validation library.**
- (a) pandera (the spec's word): DataFrame schemas; pulls in pandas (+~60 MB in the API image unless isolated) and typeguard.
- (b) pydantic 2 models per source row (already a dependency) plus a small in-house `ValidationReport` that counts and quarantines.
- *Decision: (b)* (review: agree). Same checks the spec lists (ranges, unique non-null keys, US bbox, ±5% row count), with no new dependency.

**D2. MP data placement after owner decision D5 (2026-09-28).**
- (a) Spec as written: everything MP goes to `internal`. This breaks the map and the Ascents tab.
- (b) Split by content:
  - `mp_routes`/`mp_locations` stay in `public` (displayable facts, no prose columns).
  - Raw `mp_ticks` (it has `climber_name`) moves to `internal`.
  - The app reads ticks only through a public aggregate table (`mp_tick_counts`) built by `ingest`.
  - `mp_tick_aggregates` stays `internal`.
- (c) Move nothing until the legal review lands.
- *Decision: (b)*, done in plan 4, with two review additions:
  - Plan 4's `-m db` acceptance asserts `has_table_privilege('app', 'internal.mp_ticks', 'SELECT')` is false.
  - Plan 8's `0014` runs `REVOKE ALL ON exposure_index FROM app`, because `exposure_index` holds MP-derived covariates and `app` would otherwise get SELECT through default privileges. `verify_roles_phase2.sql` gains a row for it.

**D3. What "future" means for stored and aggregated ticks.** Owner rule: reject ticks dated after the current UTC day at load time, and years after the current year.
- **Stored `mp_ticks`.** Quarantine `future` when `tick_date > LEAST(run-time UTC today, created_at::date + 1)`. A tick dated after the day it was captured is impossible, and it stays quarantined even after that date passes.
  - Why the one-day slack: `created_at` is `timestamp without time zone DEFAULT CURRENT_TIMESTAMP`, so it holds the **writer session's** local time. That session's zone cannot be proven from `SHOW TimeZone` in a later session.
  - US zones are at most 10 h behind UTC, so the slack makes the rule correct whatever zone the writer used.
  - Task 10 checks load provenance (per-role/per-database `TimeZone` overrides and the private loader's recorded run time) and records it.
- **Private tick export, aggregated to `YYYY-MM`.** A day cannot be checked inside a month.
  - The cut-off is `min(today, scraped_at)` per route.
  - Months after the current UTC month → `future_month`.
  - Months at or after the route's scrape month → `partial_month`, **permanently for that export**. Only a newer scrape can supply them.
  - A route whose `scraped_at` is after today → `scrape_after_today`.
  - The outcome no longer depends on the load date, so a reload of the same file is a true no-op.

**D4. Grid-bucket key.** P3 says "lat/lon rounded to 0.1°" and uses one column.
- *Decision:* integer `floor(lat*10+0.5)*10000 + floor(lon*10+0.5) + 5000` (4 bytes; decodes exactly), with the review's changes:
  - `0004` creates one immutable SQL function, `grid_bucket_key(lat double precision, lon double precision) RETURNS integer` (IMMUTABLE, PARALLEL SAFE).
  - `grid.grid_bucket_sql(lat, lon)` renders `grid_bucket_key((lat)::float8, (lon)::float8)`. The float8 cast is required: a `numeric` value just off a .x5 step (more digits than a double holds) lands in a different bucket in numeric math than as the double Python reads.
  - Every later SQL use calls the function; no plan copies the expression.
  - `test_migration_0004.py` checks Python/SQL parity at negative-longitude .x5 points.
  - NLDN tile centres sit on 0.1° multiples, so they align with this key.

**D5. Elevation / DEM source for every route point** (amendment §3.1, MVP-1).
- (a) USGS 3DEP 1/3″ (~10 m) read as remote Cloud-Optimized GeoTIFFs with rasterio, sampling only the blocks under our points.
  - The 2″ fallback applies where a 1/3″ tile is missing. 1/3″ covers most of Alaska, so the fallback is rare.
  - Public domain, and the same source v2.2 aspect/slope needs.
- (b) Open-Meteo elevation API (Copernicus GLO-90, 90 m).
- (c) USGS EPQS per point.
- *Decision: (a)* (review: agree with changes):
  - Masked sampling: nodata and a missing `nodata` tag are NULL, never 0.
  - A 404 means `no_tile`. Transient errors are retried and never stored.
  - The EPQS cross-check is redesigned. EPQS serves the best available DEM (often 1 m lidar), not 1/3″, so a per-point 5 m gate fails by design. Plan 5 compares |Δ| median and p90 against tolerances **stratified by slope**, needs ≥180 successful points, and makes slow, retried calls.

**D6. Monthly climate normals source** (amendment §3.1: mean tmax, tmin, precipitation, snowfall, freeze-thaw days).
- *Decision:* Open-Meteo archive `era5_seamless` (ERA5 0.25° + ERA5-Land 0.1°) over the trailing 10 complete calendar years per 0.1° bucket.
  - Computed inside the Professional window (owner decision above) by plan 3.
  - A month with too few valid days is NULL, not a sum over zeros. Missing days are never summed as 0; freeze-thaw days are scaled by valid days.
  - Open-Meteo's returned `elevation` is the 90 m DEM height of the request point, and its output is already lapse-rate-downscaled to it. It is recorded per bucket (`ref_elevation_m`) so the model sees the route-vs-bucket gap. `elevation=<route elevation>` downscaling is a v2.2 option.
- *Challenger:* PRISM daily 800 m data has been free since 2025-03-27, so CONUS freeze-thaw is derivable. That makes PRISM a stronger v2.2 challenger through the gate. PRISM has no snowfall, and AK/HI need another source.

**D7. Storage shape for static features and normals.**
- *Decision:*
  - (i) `feature_points` holds elevation and keys, keyed by a rounded point (`point_key`, 5 decimals, computed only in Python).
    - `point_key` is **stored** on `canonical_areas` and objectives, never rebuilt with SQL `to_char`.
    - `feature_points.tz` carries the local-day timezone.
  - (ii) `cell_climate_normals(grid_bucket, month, …)` and a wide day-of-year `cell_climatology` are created in plan 3's `0007` and filled in the same paid pass. Their completion is tracked per bucket in `cell_normals_status`.
  - (iii) Plan 5 adds one scoring-unit features view, so objectives and routes expose the same feature contract to Phase 3, normals included.
  - (iv) The 10-year daily series is not persisted beyond the 3-year window.
- The earlier claim that points could be computed "before the catalog" is withdrawn: `0010` foreign-keys `canonical_routes`.

**D8. Refresh cadence.** *Decision:* the single table "Refresh cadence" above. It replaces the earlier weekly-ERA5-append recommendation, which the Standard tier cannot serve.

**D9. The `weather` table (R6).** The live kernel (`app/api/v1/predict.py:571`, `app/tasks/safety_computation_optimized.py`) still joins `weather` on `accident_id`, and amendment D4 forbids interim scorer changes.
- *Decision:* build `cell_daily_conditions`, verify it against `weather` (r > 0.95), and leave `weather` read-only in place.
- Dropping `weather` is a **hard gate of the Phase 3 MVP-1 PR** that deletes the kernel. That PR's checklist must contain "drop `weather` (plan 3 D9)"; MVP-1 does not merge while `weather` is still read.
- The relaunch caveat must also say the live kernel's legacy `weather` rows include mislinked incidents (the R3/R5 defects), so live scores until MVP-1 inherit them.

**D10. R1 rows whose year stays unverified.**
- *Decision:* leave `accidents.date` untouched and set only the new columns; Phase 3 excludes the row through `excluded_reason`.
- The same principle covers `region_fallback` geocodes: coordinates are **kept** and labelled by `geocode_precision`. Only foreign rows (`outside_us`) lose coordinates.
- Plan 2's runbook reports the unverified-year count as a Phase 3 recency-bias note.

**D11. OpenBeta ingest source.**
- The exporter's `schema.sql` (verified 2026-09-28) has no area UUIDs, no `mp_id`, no pitches and no `ice`/`mixed`/`aid`/`snow` flags. The parquet file therefore cannot build `canonical_areas` or `type_group`.
- *Decision:* a weekly full GraphQL load (`bulkAreas` per US state) is the only OpenBeta source (review: agree). Caveats plan 4 implements:
  - `bulkAreas` takes **≥2 ancestor area UUIDs** (the USA area UUID plus the state's UUID), not names. Plan 4 resolves and records the UUIDs first.
  - Pages hold ≤2000 items (the default is 500), with a deterministic ordering, so a page boundary never skips or repeats a climb.
  - `metadata.mp_id` is a **String**; the validator parses it to an integer and quarantines non-numeric values.
  - Empty or failed responses are retried with backoff, since 2 of 12 test calls came back empty. An empty state is never recorded as "no climbs".
  - Each load is rejected if the US total drops >3% **or** any single state drops beyond its own tolerance, against the previous successful load.
  - Coordinates of exactly (0, 0) are missing, never a location.
- If the parquet export later gains area UUIDs and discipline flags, switching back is a contained change to `catalog.py`.

**D12. Where filled manual CSVs live.** *Decision:* commit only header-only templates and loaders. Filled CSVs stay in `~/Developer/safeascent-private/manual/` until legal Q6 is answered. R5 and matcher review CSVs stay private too (`data/review/` is gitignored).

**D13. `trainer` role timing.**
- *Decision:* create `trainer` now as **NOLOGIN with no grants**. Phase 3 adds LOGIN, a password (same mechanism as `ingest`: plaintext via `\getenv` over verify-full TLS, never argv) and SELECT on training views.
- `default_transaction_read_only` is a session default any client can override, not a privilege boundary. Granting SELECT on raw `accidents` would expose narratives.
- `triage_worker` is Phase 4 and is not created.

**D14. Pipeline credentials.**
- *Decision:* add `INGEST_DATABASE_URL: str | None = None` to `Settings`. Job CLIs refuse to run without it.
- `db.ingest_engine()` refuses a non-local URL that asks for anything weaker than verify-full.
- GitHub Actions data workflows get the URL from an Actions secret and also set `DATABASE_URL` to the same value, only to satisfy `Settings`.
- When plan 7's beat-driven feeds land, the ingest credential goes on a **dedicated ingest worker/beat service**, never on the general `worker`.

**D15. Lithology fallback** (v2.2 feature, plan 5 PR 2b-2b).
- *Decision:* Macrostrat only. A miss is `lithology = NULL`, `lithology_source = 'none'`. Non-200 responses are retried and never stored as `none`.
- Neon Launch has no storage cap; storage costs about $0.35/GB-month. SGMC (~0.5–1 GB) is a cost question, not a limit. Revisit with the measured miss rate before v2.2 trains.

**D16. R10 legacy cleanup mechanics.**
- *Decision:*
  - The R10 job writes `accident_route_links` and **never nulls** an unresolved legacy link. Unresolved accidents go to `internal.r10_unresolved` for owner review (`owner_decision IN ('link','no_link')`).
  - Rule 4 derives the type via `route_types.map_type_group`. The 0.80–0.90 score band goes to review, not auto-link.
  - R10 runs in plan 5 and is re-run after plan 6's data load.
  - `0012_drop_legacy_routes` (plan 6) refuses unless every accident with a non-null `route_id` or `mountain_id` has an `accident_route_links` row, or an `r10_unresolved` row with a non-null `owner_decision`. Old values survive in `internal.accidents_raw`, `internal.accident_revisions` and `r10_unresolved`.
- After the drop, the API's `mountain_id` filter returns 422 like `route_id` does today, and the two fields leave `AccidentResponse`. The two legacy-bug tests are deleted with the column.

**D17. OpenBeta tick counts (`ob_ticks` exposure component).** OpenBeta ticks exist only per climb in GraphQL (`userTicksByClimbId`), are sparse, and are not in the export. There is no bulk tick query.
- *Decision:* `ob_ticks` is missing-flagged everywhere in exposure v1 (reason `no_bulk_source`); Phase 3 treats it as zero-information. This overrides P2-9 ("rock popularity from OpenBeta ticks") with owner sign-off (2026-09-28).
- Plan 4 adds a **one-time stratified sample** of ~1–2K climbs (strata: state × type group × catalog popularity) that measures tick coverage: the share of climbs with any tick, and ticks per climb-year.
- **Revisit trigger for P2-9:** reconsider using OpenBeta ticks if any of these happens:
  - the sample's coverage clears the threshold recorded in plan 4's sample task;
  - OpenBeta adds a bulk tick query;
  - the sample is re-run a year later and shows a material rise.

**D18. Prediction compaction mechanics (P2-8).**
- *Decision:*
  - The nightly run keeps writing `historical_predictions`. A daily `ingest` job folds rows older than 7 days into `public.prediction_archive_v1(route_id, month_start, scores smallint[31])` and deletes them. The trends endpoint reads both; `app` gets SELECT on the archive only.
  - Each score is stored as `round(score * 10)` so one decimal survives. The band recomputed from the archive therefore equals the band shown on the day (24.5 stays 245 → green; 0.05–0.49 stays estimable). `-1` marks an insufficient (gray) day, and NULL marks no prediction.
  - Archive retention is 400 days (spec). The writer's 1-year purge (`safety_computation_optimized.py:905-908`) is aligned to it.
  - The Phase 3 backtest reads the archive plus recent rows, or runs before the first fold.
  - `VACUUM FULL` runs in a window that cannot overlap the nightly scorer (which can run to about 10:00 UTC).

## Global Constraints

- Branch per PR off an up-to-date `main`: this plan is `feat/p2a-foundations`. Never commit to `main`. Never `git push`; the owner pushes with `/commitandpush`.
- CI's single required check is `ci-ok` (jobs `backend`, `frontend`, `guards`, `ci-ok`); do not rename jobs. Every task leaves `uv run pytest`, `uv run ruff check . ../scripts/` and `uv run mypy` green (run from `backend/`).
- Python via uv; ruff 0.8.4 (`E4,E7,E9,F`); every new core module is typed and added to a mypy strict allowlist block in `backend/pyproject.toml`.
- Migrations: new Alembic revisions from `0004`, run as `migrator`, rehearsed on a Neon branch, applied as an explicit owner/agent step, **never at app startup**; forward-only unless the revision says otherwise; downgrades refuse when they would lose data (the `0002`/`0003` convention). No DDL anywhere under `backend/app/` (`tests/test_no_runtime_ddl.py`).
- Roles: created only via owner-run SQL, never neonctl/Console/API, following main's `create_roles.sql` (PR #6, a03540e): Neon rejects client-side SCRAM verifiers, so each password is generated by `scripts.write_role_url --role <r> --env-file .env.<r> --generate-password` (64 hex chars, written 0600 to a gitignored file), reaches `psql` only through `\getenv` (never argv) and the server as plaintext over verify-full TLS (the server stores SCRAM-SHA-256); every role script refuses a rerun before any password is sent and refuses verifier-shaped or short (<32 chars) values. `app` stays SELECT-only except `historical_predictions`. `app` has no privilege on schema `internal`. Least privilege for `ingest`; `trainer` exists as NOLOGIN with no grants until Phase 3 `[assumes D13]`.
- TLS: every connection to Neon verifies the server certificate and host name. Python uses `app.db.ssl.connect_args_for` (verify-full with certifi); owner/analyst `psql`, `pg_dump` and `pg_restore` URLs go through the `verify_full_url` helper (Task 8 Step 1), which forces `sslmode=verify-full&sslrootcert=system` (libpq 16+). `sslmode=require` is never used.
- Secrets: agents never read `backend/.env*` and never print credentials; runbook commands load env files inside subshells and pass passwords via `PGPASSWORD`, never argv.
- No scraper code in the repo, ever (`scripts/check_no_scrapers.py`). Loaders may read a private export file; they never fetch MP pages. The MP scrape runs only in `~/Developer/safeascent-private`.
- Mountain Project: route facts and tick aggregates may be displayed; MP descriptions/prose never, anywhere (fixtures, docs, commits). No real MP ids, names or URLs in tests or fixtures; use synthetic ids ≥ 900000000. `DATA_LICENSE.md` is not edited (pending legal review).
- OpenBeta is CC0; never OpenBeta photos/media.
- Accuracy over everything: missing or thin data never reads as safe; missing numeric values are NULL, never 0; every loader validates types, ranges, dates and coordinates at the boundary and quarantines bad rows (never silently drops) and reports counts.
- "Future" means after the current UTC day **at run time** (`today` is read per run and injected into pure functions; never a constant date). Tick loads also reject years after the current year.
- Weather days are the crag's local calendar day (owner decision 2026-09-28); plan 3 owns `app/pipelines/localday.py`. US local days never run ahead of the UTC day, so the "future" rule above is unchanged.
- Accidents link to routes via `accidents.mp_route_id`; legacy `routes`/`mountains` and `accidents.route_id`/`mountain_id` are untouched until plan 5's R10 relink and plan 6's guarded `0012` drop.
- Cost: batch jobs over always-on services; Open-Meteo Professional only in the yearly January window (owner decision); Neon Launch plan (~3.7 GB used; no storage cap, storage billed at about $0.35/GB-month; the 4.5 GB figure in the spec is a self-imposed budget, not a limit); Railway us-east4.
- Comments are load-bearing only. `CHANGELOG.md` gets one dated entry per PR (Keep a Changelog).
- TDD: failing test, minimal code, green, commit.

## Review Focus

1. **A tick month equal to the route's scrape month**, loaded after that month has ended — expect quarantine `partial_month`, not acceptance and not silent loss (Task 6 test `test_scrape_month_and_later_are_partial_not_dropped`).
2. **A stored tick dated two days after its own `created_at` day but before today** — expect `future`; one day after stays clean because of the writer-timezone slack (Task 5 tests `test_tick_after_capture_day_is_future_even_when_before_today`, `test_tick_one_day_after_capture_is_allowed_for_writer_zone_slack`).
3. **A coordinate on the antimeridian side of the Aleutians (lon +175)** — expect inside the US, not quarantined (Task 1 test `test_aleutians_east_of_180_are_inside_the_us`).
4. **A second load of the identical tick export** — expect a logged no-op, zero rows upserted, not duplicate counts (Task 6 test `test_second_identical_load_is_a_noop`).
5. **Running `0004` on prod where `migrator` cannot create schemas and the owner script has not run** — expect a clear refusal naming `create_roles_phase2.sql`, not a half-applied migration (Task 2 test `test_0004_refuses_without_internal_schema_when_unprivileged`).
6. **A `.x5` coordinate at negative longitude, stored as `numeric`** — expect the SQL bucket to equal the Python bucket (Task 2 test `test_grid_bucket_key_matches_python_at_half_steps`).
7. **A remote `INGEST_DATABASE_URL` carrying `sslmode=require`** — expect the job to refuse before connecting (Task 3 test `test_ingest_engine_refuses_weaker_tls_on_a_remote_host`).
8. **Reloading the same export in a later month** — expect a no-op, not acceptance of a partial month (Task 6 test `test_reload_in_a_later_month_is_still_a_noop`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/app/pipelines/__init__.py` | Create | Package marker (docstring states the no-scraper rule). |
| `backend/app/pipelines/validate.py` | Create | US bbox, date/range/coord problem checks, `ValidationReport`, batch gate. |
| `backend/app/pipelines/grid.py` | Create | 0.1° grid key in Python; `grid_bucket_sql` renders a call to the SQL function `grid_bucket_key` `[assumes D4]`. |
| `backend/app/pipelines/db.py` | Create | `ingest_engine()` from `settings.INGEST_DATABASE_URL`; refuses weaker-than-verify-full TLS on a remote host `[assumes D14]`. |
| `backend/app/pipelines/ingest_log.py` | Create | Run log, no-op detection, quarantine writer, content hashing. |
| `backend/app/pipelines/mp_ticks_quarantine.py` | Create | R8 classifier + set-based SQL job + CLI. |
| `backend/app/pipelines/mp_tick_aggregates.py` | Create | Private SQLite export reader, ice/mixed-only validation, INSERT-only load + CLI. |
| `backend/app/models/pipeline.py` | Create | Typed models: `SourceIngestLog`, `AccidentRevision`, `IngestQuarantine`, `MpTickAggregate`. |
| `backend/app/models/accident.py` | Modify | Phase 2a columns. |
| `backend/app/models/__init__.py` | Modify | Import `pipeline`. |
| `backend/app/config.py` | Modify | `INGEST_DATABASE_URL`. |
| `.env.example` | Modify | `INGEST_DATABASE_URL=`. |
| `backend/alembic/env.py` | Modify | `include_schemas` limited to `public` and `internal`. |
| `backend/alembic/versions/0004_phase2a_foundation.py` | Create | Schema guard, `grid_bucket_key()` SQL function, `internal.accidents_raw`, accident columns, run log, revisions, quarantine, tick aggregates, tick quarantine columns. |
| `backend/db/roles/create_roles_phase2.sql` | Create | `ingest` (LOGIN), `trainer` (NOLOGIN, no grants), schema `internal` (owner-only: needs database CREATE). |
| `backend/db/roles/grants_phase2.sql` | Create | Idempotent Phase 2 grants (extended by later plans). |
| `backend/db/roles/verify_roles_phase2.sql` | Create | Exact-privilege checks for `ingest`/`trainer` and `app` vs `internal`. |
| `backend/scripts/write_role_url.py` | Modify | `ROLES` gains `ingest` (`trainer` gets a URL only in Phase 3). |
| `backend/tests/pgtest.py` | Create | Throwaway migrated DB helper for pipeline tests. |
| `backend/tests/test_validate.py`, `test_grid.py`, `test_migration_0004.py`, `test_ingest_log.py`, `test_roles_phase2.py`, `test_mp_ticks_quarantine.py`, `test_mp_tick_aggregates.py` | Create | Tests. |
| `backend/tests/verify/__init__.py`, `backend/tests/verify/_db.py`, `backend/tests/verify/test_phase2a_foundation.py` | Create | `-m db` acceptance checks against a Neon branch as `analyst`; `_db.fetch` connects with `connect_args_for` (verify-full). |
| `backend/tests/test_write_role_url.py` | Modify | `ingest` accepted; `trainer` rejected until Phase 3. |
| `backend/pyproject.toml` | Modify | mypy allowlist blocks; `db` marker; default deselection. |
| `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`, `CHANGELOG.md` | Modify | Roles, schema, jobs, commands. |

## Pre-flight conflict ledger (this plan)

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 3, 5, 6 | `validate.ValidationReport`, `today` injection | Task 1 first; signatures frozen in its Interfaces block. |
| 1, 2 | plans 3, 5, 6, 7, 8 | `grid.grid_bucket`, `grid_bucket_sql`, SQL `grid_bucket_key()` | Frozen here; later plans import or call the function, never copy the expression. |
| 2 | 3, 5, 6 | tables `source_ingest_log`, `internal.ingest_quarantine`, `internal.mp_tick_aggregates`, `mp_ticks.quarantine_reason` | Task 2 before any DB-backed task. |
| 2 | 4 | `internal` schema creation (migration guard vs owner script) | Both use `CREATE SCHEMA IF NOT EXISTS internal`; owner script sets `AUTHORIZATION migrator`; test in Task 4 runs the prod order. |
| 1, 3, 5, 6 | each other | `backend/pyproject.toml` mypy allowlist | Serial; each task appends its module names to the same block. |
| 3 | 5, 6 | `ingest_log.start_run/finish_run/write_quarantine/find_completed` | Task 3 before 5 and 6. |
| 4 | 5, 6, all later plans | `backend/db/roles/grants_phase2.sql`, `verify_roles_phase2.sql` | Cumulative files: each later plan appends its grants and expected-privilege rows in its own migration task. |
| 2 | 7 | `backend/app/models/accident.py` docs | Task 7 documents only. |
| 5 | plan 4 (MP split) | `mp_ticks` moves to `internal.mp_ticks` | Plan 4 Task 6 updates `mp_ticks_quarantine.QUARANTINE_SQL` and its test in the same commit as the move. |
| 2 | plan 2 | `accidents` columns and `internal.accident_revisions (accident_id, field, rule_version)` unique key | Frozen here; plan 2 only writes rows. |
| 2 | plans 5, 6 (R10) | `app/models/legacy.py`, `accidents.route_id`/`mountain_id` | Untouched here; plan 5 relinks, plan 6's `0012` drops. |
| 7 | every later plan | `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md` | Each plan adds its own dated entry/lines; rebase before merge. |

## Cross-plan interface ledger (frozen names later plans rely on)

| Interface | Defined in | Consumed by |
|---|---|---|
| `validate.ValidationReport`, `in_us`, `date_problem`, `coord_problem`, `range_problem`, `batch_gate` | 1/T1 | 2, 3, 4, 5, 6, 7, 8 |
| `grid.grid_bucket`, `grid.bucket_center`, `grid.grid_bucket_sql` | 1/T1 | 3, 5, 6, 7, 8 |
| SQL function `grid_bucket_key(lat double precision, lon double precision) RETURNS integer` (IMMUTABLE) | 1/T2 (`0004`) | 3 (`0007` view), 5, 6, 7, 8 |
| `ingest_log.start_run`, `finish_run`, `find_completed`, `write_quarantine`, `sha256_rows`, `last_ok_rows_in` | 1/T3 | 2–8 |
| `db.ingest_engine` (verify-full refusal) | 1/T3 | 2–8 |
| `tests.verify._db.fetch(sql)` (analyst, verify-full) | 1/T5 | every later plan's `tests/verify` module |
| `accidents` Phase 2a columns; `internal.accident_revisions` | 1/T2 | 2, 3, 5 |
| `app/data/repair/framework.apply_changes` | 2/T1 | 2, 3, 5 |
| `open_meteo.ArchiveClient` | 3/T1 | 7 |
| `localday.tz_for_point(lat, lon) -> str \| None`, `localday.local_date(ts_utc, tz) -> date`; `pipelines` dependency group (created with `timezonefinder`) | 3 | 4, 5, 6, 7 |
| `grid_bucket_series(grid_bucket, tz, first_seen_at, last_era5_date)`, PK `(grid_bucket, tz)` | 3/`0007` | 5, 6, 7 |
| `cell_daily_conditions` PK `(grid_bucket, tz, date)`, `record_kind IN ('era5','stopgap','forecast')`; `accidents.tz` | 3/`0007` | 5, 7, 8, Phase 3 |
| `cell_climate_normals`, `cell_climatology`, `cell_normals_status(grid_bucket, status IN ('pending','complete','insufficient'), …)`; `normals.py` | 3 | 5, 6, 7, Phase 3 |
| `era5_window run --window-start … --max-units …` (January batch; cumulative budget) | 3 | 5, 6, 7 (new series wait for it) |
| `canonical_routes`, `canonical_areas`, `route_types.map_type_group`, `match.*` | 4 | 5, 6, 8 |
| `feature_points` (with `tz`, stored `point_key`), `route_static_features`, scoring-unit features view | 5 | 6, 7, 8, Phase 3 |
| `internal.r10_unresolved`, R10 job | 5/`0010` | 6 (`0012_drop_legacy_routes` guard) |
| `objectives`, `coverage.badge_for`, `0012_drop_legacy_routes` | 6 | 7, 8, Phase 3 |

---

### Task 1: Validation core and the grid key

**Files:**
- Create: `backend/app/pipelines/__init__.py`, `backend/app/pipelines/validate.py`, `backend/app/pipelines/grid.py`
- Test: `backend/tests/test_validate.py`, `backend/tests/test_grid.py`
- Modify: `backend/pyproject.toml` (mypy strict allowlist)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `in_us(lat: float, lon: float) -> bool`
  - `date_problem(value: date | None, *, today: date, earliest: date | None = None) -> str | None` returning `"missing_date" | "future" | "too_early" | None`
  - `coord_problem(lat: float | None, lon: float | None) -> str | None` returning `"missing_coords" | "not_finite" | "out_of_range" | "outside_us" | None`
  - `range_problem(value: float | None, lo: float, hi: float) -> str | None` returning `"not_finite" | "out_of_range" | None` (None value → None: missing is allowed unless the caller requires it)
  - `@dataclass ValidationReport(source: str)` with `rows_in: int`, `accepted: int`, `quarantined: Counter[str]`, `issues: list[Issue]`, methods `accept() -> None`, `quarantine(row_ref: str, reason: str, **detail: object) -> None`, `quarantined_total() -> int`, `summary() -> dict[str, object]` (counts only)
  - `@dataclass(frozen=True) Issue(row_ref: str, reason: str, detail: dict[str, object])`
  - `batch_gate(report: ValidationReport, *, previous_rows_in: int | None, count_tolerance: float, max_quarantine_share: float) -> list[str]` (empty list = pass)
  - `grid.bucket_index(x: float) -> int`, `grid.grid_bucket(lat: float, lon: float) -> int`, `grid.bucket_center(bucket: int) -> tuple[float, float]`, `grid.grid_bucket_sql(lat_expr: str, lon_expr: str) -> str` (renders a call to the SQL function `grid_bucket_key`, created by `0004` in Task 2)

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_validate.py`:

```python
from datetime import date

import pytest

from app.pipelines.validate import (
    ValidationReport,
    batch_gate,
    coord_problem,
    date_problem,
    in_us,
    range_problem,
)

TODAY = date(2026, 9, 28)


@pytest.mark.parametrize(
    "lat,lon",
    [
        (40.0, -105.3),  # Boulder
        (63.07, -151.0),  # Denali
        (19.8, -155.5),  # Mauna Kea
        (24.6, -82.9),  # Dry Tortugas
        (44.8, -66.95),  # Maine, east of Lubec
    ],
)
def test_us_points_are_inside(lat, lon):
    assert in_us(lat, lon)


def test_aleutians_east_of_180_are_inside_the_us():
    assert in_us(52.9, 175.0)


@pytest.mark.parametrize("lat,lon", [(51.0, -115.0), (-33.4, -70.6), (27.99, 86.93), (37.8, 122.0)])
def test_foreign_or_sign_flipped_points_are_outside(lat, lon):
    assert not in_us(lat, lon)


@pytest.mark.parametrize("lat,lon", [(49.25, -123.1), (49.1, -113.9), (32.5, -117.0)])
def test_us_boxes_are_a_coarse_prefilter_that_admits_border_slivers(lat, lon):
    # Southern BC/AB and Tijuana sit inside the boxes. in_us only rejects gross errors
    # (sign flips, other continents); country is decided by plan 2's R3 geocode repair.
    assert in_us(lat, lon)


def test_date_problem_is_relative_to_the_injected_today():
    assert date_problem(TODAY, today=TODAY) is None
    assert date_problem(date(2026, 9, 29), today=TODAY) == "future"
    assert date_problem(date(2026, 9, 29), today=date(2026, 9, 29)) is None
    assert date_problem(None, today=TODAY) == "missing_date"
    assert date_problem(date(1969, 12, 31), today=TODAY, earliest=date(1970, 1, 1)) == "too_early"


def test_coord_problem_reasons():
    assert coord_problem(None, -105.0) == "missing_coords"
    assert coord_problem(float("nan"), -105.0) == "not_finite"
    assert coord_problem(91.0, -105.0) == "out_of_range"
    assert coord_problem(48.0, 2.3) == "outside_us"
    assert coord_problem(40.0, -105.3) is None


def test_range_problem_treats_missing_as_allowed_and_nan_as_bad():
    assert range_problem(None, 0, 75) is None
    assert range_problem(float("inf"), 0, 75) == "not_finite"
    assert range_problem(-0.1, 0, 75) == "out_of_range"
    assert range_problem(75.0, 0, 75) is None


def test_report_counts_and_summary_hold_no_row_payloads():
    report = ValidationReport("fixture")
    report.accept()
    report.quarantine("row-2", "future", value="2027-01-01")
    report.quarantine("row-3", "future", value="3901-01-01")
    assert report.rows_in == 3
    assert report.accepted == 1
    assert report.quarantined_total() == 2
    summary = report.summary()
    assert summary == {"source": "fixture", "rows_in": 3, "accepted": 1, "quarantined": {"future": 2}}


def test_batch_gate_rejects_count_swings_and_heavy_quarantine():
    report = ValidationReport("fixture")
    for _ in range(90):
        report.accept()
    for i in range(10):
        report.quarantine(f"r{i}", "outside_us")
    assert batch_gate(report, previous_rows_in=100, count_tolerance=0.05, max_quarantine_share=0.2) == []
    assert batch_gate(report, previous_rows_in=120, count_tolerance=0.05, max_quarantine_share=0.2) == [
        "rows_in 100 differs from last ok run 120 by more than 5%"
    ]
    assert batch_gate(report, previous_rows_in=None, count_tolerance=0.05, max_quarantine_share=0.05) == [
        "quarantined share 0.100 exceeds 0.050"
    ]


def test_batch_gate_rejects_an_empty_batch():
    assert batch_gate(ValidationReport("x"), previous_rows_in=None, count_tolerance=0.05, max_quarantine_share=1.0) == [
        "no rows"
    ]
```

`backend/tests/test_grid.py`:

```python
from decimal import Decimal

import pytest

from app.pipelines.grid import bucket_center, bucket_index, grid_bucket, grid_bucket_sql


def test_bucket_index_rounds_half_up_not_to_even():
    assert bucket_index(40.25) == 403
    assert bucket_index(-105.25) == -1052
    assert bucket_index(-105.26) == -1053


def test_grid_bucket_round_trips_to_the_cell_center():
    bucket = grid_bucket(40.01, -105.27)
    assert bucket == 400 * 10000 + (-1053) + 5000
    assert bucket_center(bucket) == (40.0, -105.3)


def test_antimeridian_and_alaska_buckets_decode():
    assert bucket_center(grid_bucket(52.9, 175.0)) == (52.9, 175.0)
    assert bucket_center(grid_bucket(71.3, -156.8)) == (71.3, -156.8)


def test_southern_latitudes_are_rejected():
    with pytest.raises(ValueError):
        grid_bucket(-33.4, -70.6)


def test_sql_expression_calls_the_one_function_with_float8_casts():
    assert grid_bucket_sql("l.latitude", "l.longitude") == (
        "grid_bucket_key((l.latitude)::float8, (l.longitude)::float8)"
    )


@pytest.mark.parametrize(
    "lat,lon,bucket",
    [(40.05, -105.25, 4013948), (40.05, -105.35, 4013947), (64.15, -149.95, 6423501), (19.85, -155.45, 1993446)],
)
def test_half_steps_round_up_at_negative_longitudes(lat, lon, bucket):
    assert grid_bucket(lat, lon) == bucket


def test_a_numeric_just_below_a_half_step_buckets_as_its_double():
    # asyncpg hands Python the float8 of a numeric; the SQL function sees the same double
    # only because grid_bucket_sql casts. test_migration_0004 checks the DB side.
    assert grid_bucket(float(Decimal("40.04999999999999999")), -105.3) == 4013947
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_validate.py tests/test_grid.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipelines'`.

- [ ] **Step 3: Write the implementation**

`backend/app/pipelines/__init__.py`:

```python
"""Data-platform jobs (Phase 2). Loaders read open APIs or local export files only; anything
that fetches and parses web pages lives in ~/Developer/safeascent-private, never here."""
```

`backend/app/pipelines/validate.py`:

```python
"""Boundary checks shared by every loader. Bad rows are quarantined with a reason and
counted, never dropped silently; `today` is always injected so "future" moves with the run."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

# (lat_lo, lat_hi, lon_lo, lon_hi). Alaska's Aleutians cross the antimeridian, hence two boxes.
# Deliberately coarse: southern BC/AB and Tijuana fall inside. This rejects gross errors only;
# plan 2's R3 decides the country.
US_BOXES: tuple[tuple[float, float, float, float], ...] = (
    (24.3, 49.5, -125.0, -66.8),
    (51.0, 71.6, -180.0, -129.9),
    (51.0, 53.1, 172.0, 180.0),
    (18.8, 22.4, -160.3, -154.7),
)


def in_us(lat: float, lon: float) -> bool:
    return any(lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi for lat_lo, lat_hi, lon_lo, lon_hi in US_BOXES)


def date_problem(value: date | None, *, today: date, earliest: date | None = None) -> str | None:
    if value is None:
        return "missing_date"
    if value > today:
        return "future"
    if earliest is not None and value < earliest:
        return "too_early"
    return None


def coord_problem(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return "missing_coords"
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return "not_finite"
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return "out_of_range"
    if not in_us(lat, lon):
        return "outside_us"
    return None


def range_problem(value: float | None, lo: float, hi: float) -> str | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return "not_finite"
    if not lo <= value <= hi:
        return "out_of_range"
    return None


@dataclass(frozen=True)
class Issue:
    row_ref: str
    reason: str
    detail: dict[str, object]


@dataclass
class ValidationReport:
    source: str
    rows_in: int = 0
    accepted: int = 0
    quarantined: Counter[str] = field(default_factory=Counter)
    issues: list[Issue] = field(default_factory=list)

    def accept(self) -> None:
        self.rows_in += 1
        self.accepted += 1

    def quarantine(self, row_ref: str, reason: str, **detail: object) -> None:
        self.rows_in += 1
        self.quarantined[reason] += 1
        self.issues.append(Issue(row_ref, reason, dict(detail)))

    def quarantined_total(self) -> int:
        return sum(self.quarantined.values())

    def summary(self) -> dict[str, object]:
        return {
            "source": self.source,
            "rows_in": self.rows_in,
            "accepted": self.accepted,
            "quarantined": dict(sorted(self.quarantined.items())),
        }


def batch_gate(
    report: ValidationReport,
    *,
    previous_rows_in: int | None,
    count_tolerance: float,
    max_quarantine_share: float,
) -> list[str]:
    if report.rows_in == 0:
        return ["no rows"]
    problems: list[str] = []
    if previous_rows_in:
        if abs(report.rows_in - previous_rows_in) / previous_rows_in > count_tolerance:
            problems.append(
                f"rows_in {report.rows_in} differs from last ok run {previous_rows_in} "
                f"by more than {count_tolerance:.0%}"
            )
    share = report.quarantined_total() / report.rows_in
    if share > max_quarantine_share:
        problems.append(f"quarantined share {share:.3f} exceeds {max_quarantine_share:.3f}")
    return problems
```

`backend/app/pipelines/grid.py`:

```python
"""The 0.1° grid key (P3: "lat/lon rounded to 0.1°"), packed into one integer.

floor(x*10 + 0.5) rather than round(): Python rounds half to even and Postgres's
double-precision round() is platform-dependent. SQL uses the immutable function
grid_bucket_key() from migration 0004, always on float8 arguments: a `numeric` value with
more digits than a double holds (40.04999999999999999) buckets as 400 in numeric math but
as 401 once read into Python as a float. Casting first makes both sides the same IEEE math.
"""

from __future__ import annotations

import math

LON_OFFSET = 5000
LAT_STRIDE = 10000


def bucket_index(x: float) -> int:
    return math.floor(x * 10 + 0.5)


def grid_bucket(lat: float, lon: float) -> int:
    if not (0.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"grid_bucket covers northern-hemisphere US points only, got {lat}, {lon}")
    return bucket_index(lat) * LAT_STRIDE + bucket_index(lon) + LON_OFFSET


def bucket_center(bucket: int) -> tuple[float, float]:
    lat_i, lon_part = divmod(bucket, LAT_STRIDE)
    return lat_i / 10, (lon_part - LON_OFFSET) / 10


def grid_bucket_sql(lat_expr: str, lon_expr: str) -> str:
    return f"grid_bucket_key(({lat_expr})::float8, ({lon_expr})::float8)"
```

In `backend/pyproject.toml`, append to the first strict override's `module` list: `"app.pipelines", "app.pipelines.validate", "app.pipelines.grid"`.

- [ ] **Step 4: Run tests, lint and types**

Run: `cd backend && uv run pytest tests/test_validate.py tests/test_grid.py -q && uv run ruff check . ../scripts/ && uv run mypy`
Expected: all tests PASS; ruff `All checks passed!`; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipelines/__init__.py backend/app/pipelines/validate.py backend/app/pipelines/grid.py \
  backend/tests/test_validate.py backend/tests/test_grid.py backend/pyproject.toml
git commit -m "feat(pipelines): boundary validation report and 0.1° grid key"
```

---

### Task 2: Migration `0004_phase2a_foundation` and typed models

**Files:**
- Create: `backend/alembic/versions/0004_phase2a_foundation.py`, `backend/app/models/pipeline.py`, `backend/tests/pgtest.py`, `backend/tests/test_migration_0004.py`
- Modify: `backend/app/models/accident.py`, `backend/app/models/__init__.py`, `backend/alembic/env.py`, `backend/pyproject.toml`

**Interfaces:**
- Consumes: `0003_hist_insufficient_data` (down revision).
- Produces (DB):
  - Schema `internal` (owned by `migrator` in prod).
  - Function `public.grid_bucket_key(lat double precision, lon double precision) RETURNS integer` (SQL, IMMUTABLE, STRICT, PARALLEL SAFE) — the one SQL form of the D4 key `[assumes D4]`.
  - `internal.accidents_raw` — frozen copy of `accidents` as of the migration (pre-2a shape).
  - `accidents` columns: `date_precision`, `year_source`, `year_lo`, `year_hi`, `geocode_precision`, `geocode_method`, `country`, `activity_class`, `activity_rule_version`, `inclusion_flag`, `incident_group_id`, `is_canonical` (NOT NULL default true), `severity_scale`, `excluded_reason`, `source_url`, `updated_at`, `exp_years_climbing`, `exp_stated_level` (NOT NULL default `'unknown'`), `exp_first_season`, `guided` (NOT NULL default `'unknown'`), `exp_rule_version`.
  - `internal.accident_revisions(id, accident_id, field, old_value, new_value, method, rule_version, run_id, created_at)`, unique `(accident_id, field, rule_version)`.
  - `public.source_ingest_log(run_id, source, window_start, window_end, started_at, finished_at, status, rows_in, rows_upserted, rows_quarantined, content_sha256, validation_report, cost_units)`.
  - `internal.ingest_quarantine(id, run_id, source, row_ref, reason, detail, created_at)`.
  - `internal.mp_tick_aggregates(mp_route_id, period, style, tick_count, scrape_run_id, scraped_at, loaded_run_id)`, PK `(mp_route_id, period, style)`.
  - `mp_ticks.quarantine_reason`, `mp_ticks.quarantine_rule_version`.
- Produces (Python): `app.models.pipeline.SourceIngestLog`, `AccidentRevision`, `IngestQuarantine`, `MpTickAggregate`; `tests.pgtest.migrated_db(revision="head", seed_sql=None)` context manager yielding a database name, `tests.pgtest.sa_url(name) -> str`, `tests.pgtest.pg_url(name) -> str`, `tests.pgtest.requires_pg` marker.

- [ ] **Step 1: Write the test helper and failing tests**

`backend/tests/pgtest.py`:

```python
"""Throwaway migrated databases for pipeline tests. Needs MIGRATIONS_TEST_ADMIN_URL
(see test_migrations.py); tests using it skip without one."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from alembic import command

from tests.test_migrations import ADMIN_URL, _alembic_cfg, _db_url, _execute

requires_pg = pytest.mark.skipif(not ADMIN_URL, reason="MIGRATIONS_TEST_ADMIN_URL not set")


def pg_url(name: str) -> str:
    return _db_url(name)


def sa_url(name: str) -> str:
    return _db_url(name).replace("postgresql://", "postgresql+asyncpg://", 1)


def run_sql(name: str, sql: str) -> None:
    asyncio.run(_execute(_db_url(name), sql))


@contextmanager
def migrated_db(revision: str = "head", seed_sql: str | None = None) -> Iterator[str]:
    assert ADMIN_URL is not None
    name = f"p2_{uuid.uuid4().hex[:12]}"
    asyncio.run(_execute(ADMIN_URL, f'CREATE DATABASE "{name}"'))
    try:
        command.upgrade(_alembic_cfg(name), revision)
        if seed_sql:
            run_sql(name, seed_sql)
        yield name
    finally:
        asyncio.run(_execute(ADMIN_URL, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
```

`backend/tests/test_migration_0004.py`:

```python
import asyncio
from decimal import Decimal

import asyncpg
import pytest
from alembic import command

from app.pipelines.grid import grid_bucket, grid_bucket_sql
from tests.pgtest import migrated_db, pg_url, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg

SEED = """
INSERT INTO accidents (accident_id, source, date, latitude, longitude) VALUES
  (1, 'AAC', '2001-05-02', 40.0, -105.0), (2, 'NPS', '2010-07-01', 36.5, -118.3);
"""


async def _one(url: str, sql: str) -> object:
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetchval(sql)
    finally:
        await conn.close()


def _val(name: str, sql: str) -> object:
    return asyncio.run(_one(pg_url(name), sql))


def test_0004_snapshots_accidents_before_adding_columns_and_checks_clean():
    with migrated_db("0003_hist_insufficient_data", SEED) as name:
        cfg = _alembic_cfg(name)
        command.upgrade(cfg, "head")
        command.check(cfg)
        assert _val(name, "SELECT count(*) FROM internal.accidents_raw") == 2
        assert _val(
            name,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'internal' AND table_name = 'accidents_raw' AND column_name = 'date_precision'",
        ) == 0
        assert _val(name, "SELECT bool_and(is_canonical) FROM accidents") is True
        assert _val(name, "SELECT count(*) FROM accidents WHERE exp_stated_level = 'unknown' AND guided = 'unknown'") == 2


HALF_STEPS = [
    ("40.05", "-105.25"),
    ("40.05", "-105.35"),
    ("64.15", "-149.95"),
    ("19.85", "-155.45"),
    ("52.95", "175.05"),
    ("40.04999999999999999", "-105.3"),
    ("40.1", "-105.25000000000000001"),
]


def test_grid_bucket_key_matches_python_at_half_steps():
    with migrated_db("head") as name:
        for lat, lon in HALF_STEPS:
            expr = grid_bucket_sql("'" + lat + "'::numeric", "'" + lon + "'::numeric")
            assert _val(name, f"SELECT {expr}") == grid_bucket(float(Decimal(lat)), float(Decimal(lon))), (lat, lon)
        assert _val(
            name,
            "SELECT provolatile = 'i' FROM pg_proc WHERE proname = 'grid_bucket_key'",
        ) is True


def test_0004_enum_checks_reject_unknown_values():
    with migrated_db("head", SEED) as name:
        for sql in (
            "UPDATE accidents SET date_precision = 'week' WHERE accident_id = 1",
            "UPDATE accidents SET geocode_precision = 'city' WHERE accident_id = 1",
            "UPDATE accidents SET exp_years_climbing = 200 WHERE accident_id = 1",
            "UPDATE accidents SET year_lo = 2005, year_hi = 2001 WHERE accident_id = 1",
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                run_sql(name, sql)


def test_0004_tick_quarantine_reason_check():
    with migrated_db("head") as name:
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(
                name,
                "INSERT INTO mp_ticks (tick_id, route_id, climber_name, quarantine_reason) "
                "VALUES (1, '900000001', 'x', 'bad')",
            )


def test_0004_revision_key_is_unique_per_rule_version():
    with migrated_db("head", SEED) as name:
        insert = (
            "INSERT INTO internal.accident_revisions (accident_id, field, old_value, new_value, method, rule_version, run_id) "
            "VALUES (1, 'date_precision', NULL, 'day', 'r2', 'r2-v1', gen_random_uuid())"
        )
        run_sql(name, insert)
        with pytest.raises(asyncpg.UniqueViolationError):
            run_sql(name, insert)


def test_0004_tick_period_check():
    with migrated_db("head") as name:
        run_sql(
            name,
            "INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) "
            "VALUES (900000001, '2025-01', 'lead', 3), (900000001, 'total', 'all', 3)",
        )
        with pytest.raises(asyncpg.CheckViolationError):
            run_sql(
                name,
                "INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) "
                "VALUES (900000001, '2025-13', 'lead', 1)",
            )


def test_0004_downgrade_refuses_while_revisions_exist():
    with migrated_db("head", SEED) as name:
        run_sql(
            name,
            "INSERT INTO internal.accident_revisions (accident_id, field, old_value, new_value, method, rule_version, run_id) "
            "VALUES (1, 'country', NULL, 'US', 'r3', 'r3-v1', gen_random_uuid())",
        )
        with pytest.raises(RuntimeError, match="refusing to downgrade 0004"):
            command.downgrade(_alembic_cfg(name), "0003_hist_insufficient_data")


def test_0004_downgrade_is_clean_when_empty():
    with migrated_db("head", SEED) as name:
        command.downgrade(_alembic_cfg(name), "0003_hist_insufficient_data")
        assert _val(name, "SELECT to_regclass('internal.accidents_raw')") is None
        assert _val(name, "SELECT to_regclass('public.source_ingest_log')") is None
        assert _val(name, "SELECT count(*) FROM pg_proc WHERE proname = 'grid_bucket_key'") == 0


def test_0004_refuses_without_internal_schema_when_unprivileged():
    with migrated_db("0003_hist_insufficient_data") as name:
        # A non-superuser without CREATE on the database stands in for prod's migrator.
        # The guard raises before any DDL, so table ownership never comes into play.
        run_sql(
            name,
            "CREATE ROLE p2_nocreate LOGIN PASSWORD 'pw';"
            "GRANT ALL ON SCHEMA public TO p2_nocreate;"
            "GRANT ALL ON ALL TABLES IN SCHEMA public TO p2_nocreate;",
        )
        cfg = _alembic_cfg(name)
        base = pg_url(name).split("://", 1)[1].split("@", 1)[1]
        cfg.set_main_option("sqlalchemy.url", f"postgresql+asyncpg://p2_nocreate:pw@{base}")
        try:
            with pytest.raises(RuntimeError, match="create_roles_phase2.sql"):
                command.upgrade(cfg, "head")
            assert _val(name, "SELECT to_regclass('public.source_ingest_log')") is None
            assert _val(name, "SELECT version_num FROM alembic_version") == "0003_hist_insufficient_data"
        finally:
            run_sql(name, "DROP OWNED BY p2_nocreate; DROP ROLE p2_nocreate;")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && MIGRATIONS_TEST_ADMIN_URL=postgresql://test_user:test_password@localhost:5432/postgres uv run pytest tests/test_migration_0004.py -q` (local PostGIS from `docker compose up db`, or rely on CI)
Expected: FAIL with `Can't locate revision identified by 'head'`-style errors on `internal.*` / missing revision `0004`.

- [ ] **Step 3: Write the migration**

`backend/alembic/versions/0004_phase2a_foundation.py`:

```python
"""Phase 2a foundation: internal schema, grid_bucket_key(), frozen accidents_raw, accident
repair columns, ingest run log, quarantine, MP tick aggregates, mp_ticks quarantine columns.

Schema `internal` is created by db/roles/create_roles_phase2.sql in prod (CREATE SCHEMA
needs CREATE on the database, which migrator deliberately lacks). Here it is created only
when the current role may, i.e. in local/CI databases.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_phase2a_foundation"
down_revision = "0003_hist_insufficient_data"
branch_labels = None
depends_on = None

# One SQL definition of the D4 grid key. Callers pass float8 (grid.grid_bucket_sql adds the
# casts) so a numeric column buckets exactly as the double Python reads from it.
GRID_BUCKET_KEY_SQL = """
CREATE FUNCTION public.grid_bucket_key(lat double precision, lon double precision) RETURNS integer
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$ SELECT floor(lat * 10 + 0.5)::int * 10000 + floor(lon * 10 + 0.5)::int + 5000 $$
"""

ENUM_CHECKS = {
    "date_precision": ("day", "month", "year", "unknown"),
    "geocode_precision": ("exact", "crag", "area", "park_centroid", "region_fallback", "unknown"),
    "activity_class": ("climbing", "climbing_approach", "non_climbing"),
    "severity_scale": ("full", "fatal_only", "unknown"),
    "exp_stated_level": ("novice", "intermediate", "experienced", "expert", "unknown"),
    "guided": ("guided", "unguided", "unknown"),
}


def _ensure_internal_schema() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT to_regnamespace('internal')").scalar() is not None:
        return
    can_create = bind.exec_driver_sql(
        "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
    ).scalar()
    if not can_create:
        raise RuntimeError(
            "schema internal does not exist and this role cannot create it: run "
            "backend/db/roles/create_roles_phase2.sql as the owner first"
        )
    op.execute("CREATE SCHEMA internal")


def upgrade() -> None:
    _ensure_internal_schema()
    op.execute(GRID_BUCKET_KEY_SQL)

    # Snapshot before the new columns exist, so accidents_raw keeps the pre-2a shape.
    op.execute("CREATE TABLE internal.accidents_raw AS TABLE public.accidents")
    bind = op.get_bind()
    live = bind.exec_driver_sql("SELECT count(*) FROM public.accidents").scalar_one()
    raw = bind.exec_driver_sql("SELECT count(*) FROM internal.accidents_raw").scalar_one()
    if live != raw:
        raise RuntimeError(f"accidents_raw copy mismatch: {raw} of {live} rows")
    op.execute("ALTER TABLE internal.accidents_raw ADD PRIMARY KEY (accident_id)")

    add = op.add_column
    add("accidents", sa.Column("date_precision", sa.Text(), nullable=True))
    add("accidents", sa.Column("year_source", sa.Text(), nullable=True))
    add("accidents", sa.Column("year_lo", sa.SmallInteger(), nullable=True))
    add("accidents", sa.Column("year_hi", sa.SmallInteger(), nullable=True))
    add("accidents", sa.Column("geocode_precision", sa.Text(), nullable=True))
    add("accidents", sa.Column("geocode_method", sa.Text(), nullable=True))
    add("accidents", sa.Column("country", sa.Text(), nullable=True))
    add("accidents", sa.Column("activity_class", sa.Text(), nullable=True))
    add("accidents", sa.Column("activity_rule_version", sa.Text(), nullable=True))
    add("accidents", sa.Column("inclusion_flag", sa.Text(), nullable=True))
    add("accidents", sa.Column("incident_group_id", sa.Integer(), nullable=True))
    add("accidents", sa.Column("is_canonical", sa.Boolean(), server_default=sa.true(), nullable=False))
    add("accidents", sa.Column("severity_scale", sa.Text(), nullable=True))
    add("accidents", sa.Column("excluded_reason", sa.Text(), nullable=True))
    add("accidents", sa.Column("source_url", sa.Text(), nullable=True))
    add("accidents", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    add("accidents", sa.Column("exp_years_climbing", sa.SmallInteger(), nullable=True))
    add("accidents", sa.Column("exp_stated_level", sa.Text(), server_default="unknown", nullable=False))
    add("accidents", sa.Column("exp_first_season", sa.Boolean(), nullable=True))
    add("accidents", sa.Column("guided", sa.Text(), server_default="unknown", nullable=False))
    add("accidents", sa.Column("exp_rule_version", sa.Text(), nullable=True))
    for column, values in ENUM_CHECKS.items():
        allowed = ", ".join(f"'{v}'" for v in values)
        op.create_check_constraint(f"accidents_{column}_check", "accidents", f"{column} IS NULL OR {column} IN ({allowed})")
    op.create_check_constraint(
        "accidents_exp_years_climbing_check", "accidents", "exp_years_climbing IS NULL OR exp_years_climbing BETWEEN 0 AND 80"
    )
    op.create_check_constraint(
        "accidents_year_bounds_check", "accidents", "year_lo IS NULL OR year_hi IS NULL OR year_lo <= year_hi"
    )
    op.create_index("idx_accidents_incident_group", "accidents", ["incident_group_id"])

    op.create_table(
        "source_ingest_log",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rows_in", sa.Integer(), nullable=True),
        sa.Column("rows_upserted", sa.Integer(), nullable=True),
        sa.Column("rows_quarantined", sa.Integer(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("validation_report", postgresql.JSONB(), nullable=True),
        sa.Column("cost_units", sa.Numeric(), nullable=True),
        sa.CheckConstraint("status IN ('running', 'ok', 'rejected', 'failed')", name="source_ingest_log_status_check"),
    )
    op.create_index("ix_source_ingest_log_source_finished", "source_ingest_log", ["source", "finished_at"])

    op.create_table(
        "accident_revisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("accident_id", sa.Integer(), sa.ForeignKey("public.accidents.accident_id"), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("accident_id", "field", "rule_version", name="accident_revisions_key"),
        schema="internal",
    )

    op.create_table(
        "ingest_quarantine",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("public.source_ingest_log.run_id"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("row_ref", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="internal",
    )
    op.create_index("ix_ingest_quarantine_run", "ingest_quarantine", ["run_id"], schema="internal")

    op.create_table(
        "mp_tick_aggregates",
        sa.Column("mp_route_id", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("style", sa.Text(), nullable=False),
        sa.Column("tick_count", sa.Integer(), nullable=False),
        sa.Column("scrape_run_id", sa.Text(), nullable=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("loaded_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("mp_route_id", "period", "style"),
        sa.CheckConstraint(
            "period = 'total' OR period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="mp_tick_aggregates_period_check"
        ),
        sa.CheckConstraint("tick_count >= 0", name="mp_tick_aggregates_count_check"),
        schema="internal",
    )

    op.add_column("mp_ticks", sa.Column("quarantine_reason", sa.Text(), nullable=True))
    op.add_column("mp_ticks", sa.Column("quarantine_rule_version", sa.Text(), nullable=True))
    op.create_check_constraint(
        "mp_ticks_quarantine_reason_check",
        "mp_ticks",
        "quarantine_reason IS NULL OR quarantine_reason IN ('future', 'orphan_route', 'pre_1970')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("internal.accident_revisions", "internal.mp_tick_aggregates", "public.source_ingest_log"):
        rows = bind.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
        if rows:
            raise RuntimeError(f"refusing to downgrade 0004: {table} has {rows} rows")
    op.drop_constraint("mp_ticks_quarantine_reason_check", "mp_ticks", type_="check")
    op.drop_column("mp_ticks", "quarantine_rule_version")
    op.drop_column("mp_ticks", "quarantine_reason")
    op.drop_table("mp_tick_aggregates", schema="internal")
    op.drop_table("ingest_quarantine", schema="internal")
    op.drop_table("accident_revisions", schema="internal")
    op.drop_table("source_ingest_log")
    op.drop_index("idx_accidents_incident_group", "accidents")
    for name in (*ENUM_CHECKS, "exp_years_climbing", "year_bounds"):
        op.drop_constraint(f"accidents_{name}_check", "accidents", type_="check")
    for column in (
        "exp_rule_version", "guided", "exp_first_season", "exp_stated_level", "exp_years_climbing",
        "updated_at", "source_url", "excluded_reason", "severity_scale", "is_canonical", "incident_group_id",
        "inclusion_flag", "activity_rule_version", "activity_class", "country", "geocode_method",
        "geocode_precision", "year_hi", "year_lo", "year_source", "date_precision",
    ):
        op.drop_column("accidents", column)
    op.drop_table("accidents_raw", schema="internal")
    op.execute("DROP FUNCTION public.grid_bucket_key(double precision, double precision)")
```

`gen_random_uuid()` in the tests is core Postgres 13+; no extension needed.

- [ ] **Step 4: Models and env.py**

Append to the `Accident` class in `backend/app/models/accident.py` (after `mp_route_id`, same `Column` style as the file; add `Boolean, DateTime, SmallInteger, text` to the import line):

```python
    # Phase 2a repair columns (migration 0004). NULL means "not yet classified", never a default guess.
    date_precision = Column(Text, nullable=True)
    year_source = Column(Text, nullable=True)
    year_lo = Column(SmallInteger, nullable=True)
    year_hi = Column(SmallInteger, nullable=True)
    geocode_precision = Column(Text, nullable=True)
    geocode_method = Column(Text, nullable=True)
    country = Column(Text, nullable=True)
    activity_class = Column(Text, nullable=True)
    activity_rule_version = Column(Text, nullable=True)
    inclusion_flag = Column(Text, nullable=True)
    incident_group_id = Column(Integer, nullable=True)
    is_canonical = Column(Boolean, nullable=False, server_default=text("true"))
    severity_scale = Column(Text, nullable=True)
    excluded_reason = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    exp_years_climbing = Column(SmallInteger, nullable=True)
    exp_stated_level = Column(Text, nullable=False, server_default=text("'unknown'"))
    exp_first_season = Column(Boolean, nullable=True)
    guided = Column(Text, nullable=False, server_default=text("'unknown'"))
    exp_rule_version = Column(Text, nullable=True)
```

and add `Index("idx_accidents_incident_group", "incident_group_id"),` to its `__table_args__`.

`backend/app/models/pipeline.py`:

```python
"""Phase 2 pipeline bookkeeping tables (migration 0004)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Identity, Index, Integer, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SourceIngestLog(Base):
    __tablename__ = "source_ingest_log"
    __table_args__ = (Index("ix_source_ingest_log_source_finished", "source", "finished_at"),)

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    rows_in: Mapped[int | None] = mapped_column(Integer)
    rows_upserted: Mapped[int | None] = mapped_column(Integer)
    rows_quarantined: Mapped[int | None] = mapped_column(Integer)
    content_sha256: Mapped[str | None] = mapped_column(Text)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_units: Mapped[Decimal | None] = mapped_column(Numeric)


class AccidentRevision(Base):
    __tablename__ = "accident_revisions"
    __table_args__ = (
        UniqueConstraint("accident_id", "field", "rule_version", name="accident_revisions_key"),
        {"schema": "internal"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    accident_id: Mapped[int] = mapped_column(Integer, ForeignKey("accidents.accident_id"))
    field: Mapped[str] = mapped_column(Text)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestQuarantine(Base):
    __tablename__ = "ingest_quarantine"
    __table_args__ = (Index("ix_ingest_quarantine_run", "run_id"), {"schema": "internal"})

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("source_ingest_log.run_id"))
    source: Mapped[str] = mapped_column(Text)
    row_ref: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MpTickAggregate(Base):
    __tablename__ = "mp_tick_aggregates"
    __table_args__ = {"schema": "internal"}

    mp_route_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    period: Mapped[str] = mapped_column(Text, primary_key=True)
    style: Mapped[str] = mapped_column(Text, primary_key=True)
    tick_count: Mapped[int] = mapped_column(Integer)
    scrape_run_id: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loaded_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
```

`backend/app/models/__init__.py`: add `from app.models import pipeline  # noqa: F401  (Phase 2 bookkeeping tables)` under the `legacy` import.

`backend/alembic/env.py`: add, above `_configure`,

```python
MANAGED_SCHEMAS = frozenset({"public", "internal"})


def include_name(name: str | None, type_: str, parent_names: Any) -> bool:
    # include_schemas reflects every schema; PostGIS's tiger/topology must never be diffed.
    if type_ == "schema":
        return name is None or name in MANAGED_SCHEMAS
    return True
```

and pass `include_schemas=True, include_name=include_name,` to `context.configure(...)` in `_configure`.

In `backend/pyproject.toml` add a new override block after the celery-adjacent one:

```toml
# SQLAlchemy models: declarative_base() is typed Any, so subclassing Base always trips
# disallow_subclassing_any; everything else strict still applies.
[[tool.mypy.overrides]]
module = ["app.models.pipeline"]
ignore_errors = false
disallow_any_generics = true
disallow_untyped_calls = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
warn_unused_ignores = true
warn_return_any = true
no_implicit_reexport = true
strict_equality = true
```

- [ ] **Step 5: Run the migration tests and the existing migration suite**

Run: `cd backend && MIGRATIONS_TEST_ADMIN_URL=postgresql://test_user:test_password@localhost:5432/postgres uv run pytest tests/test_migration_0004.py tests/test_migrations.py tests/test_ascent_analytics.py -q && uv run mypy && uv run ruff check . ../scripts/`
Expected: PASS. `test_baseline_builds_live_schema_and_models_match` still passes (it runs `alembic check` at head). If `alembic check` reports a diff, fix the model to match the migration, never the reverse.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0004_phase2a_foundation.py backend/alembic/env.py backend/app/models/ \
  backend/tests/pgtest.py backend/tests/test_migration_0004.py backend/pyproject.toml
git commit -m "feat(db): 0004 phase 2a foundation schema (internal schema, accidents_raw, run log, quarantine)"
```

---

### Task 3: Ingest run log, quarantine writer and the ingest engine

**Files:**
- Create: `backend/app/pipelines/ingest_log.py`, `backend/app/pipelines/db.py`, `backend/tests/test_ingest_log.py`, `backend/tests/test_pipelines_db.py`
- Modify: `backend/app/config.py`, `.env.example`, `backend/pyproject.toml`

**Interfaces:**
- Consumes: `ValidationReport` (Task 1); tables from Task 2.
- Produces:
  - `RunStatus = Literal["ok", "rejected", "failed"]`
  - `async start_run(conn: AsyncConnection, *, source: str, window_start: date | None, window_end: date | None, content_sha256: str | None) -> uuid.UUID`
  - `async finish_run(conn: AsyncConnection, run_id: uuid.UUID, *, status: RunStatus, report: ValidationReport, rows_upserted: int, problems: list[str] | None = None, cost_units: float | None = None) -> None`
  - `async find_completed(conn, *, source: str, window_start: date | None, window_end: date | None, content_sha256: str) -> uuid.UUID | None`
  - `async last_ok_rows_in(conn, source: str) -> int | None`
  - `async write_quarantine(conn, run_id: uuid.UUID, report: ValidationReport) -> int`
  - `sha256_rows(rows: Iterable[Sequence[object]]) -> str` (order-independent)
  - `db.ingest_engine() -> AsyncEngine` (raises `SystemExit` when `settings.INGEST_DATABASE_URL` is unset)
  - `db.verified_connect_args(url: str) -> dict[str, object]` (raises `SystemExit`, without echoing the URL, when a non-local URL asks for a TLS mode other than `verify-full` or would connect without certificate and host-name verification) `[assumes D14]`
  - `Settings.INGEST_DATABASE_URL: str | None = None`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_ingest_log.py`:

```python
import asyncio
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.ingest_log import (
    find_completed,
    finish_run,
    last_ok_rows_in,
    sha256_rows,
    start_run,
    write_quarantine,
)
from app.pipelines.validate import ValidationReport
from tests.pgtest import migrated_db, requires_pg, sa_url


def test_sha256_rows_ignores_order():
    assert sha256_rows([(1, "a"), (2, "b")]) == sha256_rows([(2, "b"), (1, "a")])
    assert sha256_rows([(1, "a")]) != sha256_rows([(1, "b")])


@requires_pg
def test_run_lifecycle_quarantine_and_noop_lookup():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                report = ValidationReport("fixture")
                report.accept()
                report.quarantine("r2", "future", value="2027-01-01")
                run_id = await start_run(
                    conn, source="fixture", window_start=date(2026, 9, 1), window_end=date(2026, 9, 27), content_sha256="abc"
                )
                assert await write_quarantine(conn, run_id, report) == 1
                await finish_run(conn, run_id, status="ok", report=report, rows_upserted=1)
            async with engine.connect() as conn:
                assert await find_completed(
                    conn, source="fixture", window_start=date(2026, 9, 1), window_end=date(2026, 9, 27), content_sha256="abc"
                ) == run_id
                assert await find_completed(
                    conn, source="fixture", window_start=date(2026, 9, 1), window_end=date(2026, 9, 27), content_sha256="xyz"
                ) is None
                assert await last_ok_rows_in(conn, "fixture") == 2
                row = (await conn.execute(text(
                    "SELECT status, rows_in, rows_upserted, rows_quarantined, validation_report->'quarantined'->>'future' "
                    "FROM source_ingest_log WHERE run_id = :r"), {"r": run_id})).one()
                assert tuple(row) == ("ok", 2, 1, 1, "1")
                reason = (await conn.execute(text("SELECT reason FROM internal.ingest_quarantine"))).scalar_one()
                assert reason == "future"
        finally:
            await engine.dispose()

    with migrated_db() as name:
        asyncio.run(scenario(sa_url(name)))


@requires_pg
def test_rejected_runs_are_not_noops_and_not_row_count_baselines():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                report = ValidationReport("fixture")
                report.accept()
                run_id = await start_run(conn, source="fixture", window_start=None, window_end=None, content_sha256="abc")
                await finish_run(conn, run_id, status="rejected", report=report, rows_upserted=0, problems=["no rows"])
            async with engine.connect() as conn:
                assert await find_completed(conn, source="fixture", window_start=None, window_end=None, content_sha256="abc") is None
                assert await last_ok_rows_in(conn, "fixture") is None
        finally:
            await engine.dispose()

    with migrated_db() as name:
        asyncio.run(scenario(sa_url(name)))
```

`backend/tests/test_pipelines_db.py`:

```python
import ssl

import pytest

from app.pipelines.db import verified_connect_args

REMOTE = "postgresql+asyncpg://ingest:secret-pw@ep-fixture-123.us-east-2.aws.neon.tech/neondb"


def test_remote_url_gets_a_verifying_context():
    args = verified_connect_args(REMOTE)
    context = args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


@pytest.mark.parametrize("query", ["?sslmode=require", "?ssl=require", "?sslmode=prefer", "?ssl=disable"])
def test_ingest_engine_refuses_weaker_tls_on_a_remote_host(query):
    with pytest.raises(SystemExit) as refused:
        verified_connect_args(REMOTE + query)
    assert "secret-pw" not in str(refused.value)
    assert "verify-full" in str(refused.value)


def test_explicit_verify_full_is_accepted():
    assert isinstance(verified_connect_args(REMOTE + "?ssl=verify-full")["ssl"], ssl.SSLContext)


def test_local_urls_need_no_tls():
    assert verified_connect_args("postgresql+asyncpg://test_user:pw@localhost:5432/postgres") == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_ingest_log.py tests/test_pipelines_db.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipelines.ingest_log'` (and `app.pipelines.db`).

- [ ] **Step 3: Implement**

`backend/app/pipelines/ingest_log.py`:

```python
"""source_ingest_log bookkeeping: every job run is logged with counts, a content hash and
its validation summary; a repeat of the same (source, window, hash) is a no-op."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.validate import ValidationReport

RunStatus = Literal["ok", "rejected", "failed"]


def sha256_rows(rows: Iterable[Sequence[object]]) -> str:
    digest = hashlib.sha256()
    for line in sorted(json.dumps(list(row), default=str, separators=(",", ":")) for row in rows):
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


async def start_run(
    conn: AsyncConnection,
    *,
    source: str,
    window_start: date | None,
    window_end: date | None,
    content_sha256: str | None,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO source_ingest_log (run_id, source, window_start, window_end, status, content_sha256) "
            "VALUES (:run_id, :source, :ws, :we, 'running', :sha)"
        ),
        {"run_id": run_id, "source": source, "ws": window_start, "we": window_end, "sha": content_sha256},
    )
    return run_id


async def finish_run(
    conn: AsyncConnection,
    run_id: uuid.UUID,
    *,
    status: RunStatus,
    report: ValidationReport,
    rows_upserted: int,
    problems: list[str] | None = None,
    cost_units: float | None = None,
) -> None:
    summary = report.summary() | {"problems": problems or []}
    await conn.execute(
        text(
            "UPDATE source_ingest_log SET finished_at = now(), status = :status, rows_in = :rows_in, "
            "rows_upserted = :up, rows_quarantined = :q, validation_report = CAST(:report AS jsonb), "
            "cost_units = :cost WHERE run_id = :run_id"
        ),
        {
            "status": status,
            "rows_in": report.rows_in,
            "up": rows_upserted,
            "q": report.quarantined_total(),
            "report": json.dumps(summary),
            "cost": cost_units,
            "run_id": run_id,
        },
    )


async def find_completed(
    conn: AsyncConnection,
    *,
    source: str,
    window_start: date | None,
    window_end: date | None,
    content_sha256: str,
) -> uuid.UUID | None:
    result = await conn.execute(
        text(
            "SELECT run_id FROM source_ingest_log WHERE source = :source AND status = 'ok' "
            "AND window_start IS NOT DISTINCT FROM :ws AND window_end IS NOT DISTINCT FROM :we "
            "AND content_sha256 = :sha ORDER BY finished_at DESC LIMIT 1"
        ),
        {"source": source, "ws": window_start, "we": window_end, "sha": content_sha256},
    )
    found: uuid.UUID | None = result.scalar_one_or_none()
    return found


async def last_ok_rows_in(conn: AsyncConnection, source: str) -> int | None:
    result = await conn.execute(
        text(
            "SELECT rows_in FROM source_ingest_log WHERE source = :source AND status = 'ok' "
            "ORDER BY finished_at DESC LIMIT 1"
        ),
        {"source": source},
    )
    rows_in: int | None = result.scalar_one_or_none()
    return rows_in


async def write_quarantine(conn: AsyncConnection, run_id: uuid.UUID, report: ValidationReport) -> int:
    if not report.issues:
        return 0
    await conn.execute(
        text(
            "INSERT INTO internal.ingest_quarantine (run_id, source, row_ref, reason, detail) "
            "VALUES (:run_id, :source, :row_ref, :reason, CAST(:detail AS jsonb))"
        ),
        [
            {
                "run_id": run_id,
                "source": report.source,
                "row_ref": issue.row_ref,
                "reason": issue.reason,
                "detail": json.dumps(issue.detail, default=str),
            }
            for issue in report.issues
        ],
    )
    return len(report.issues)
```

`backend/app/pipelines/db.py`:

```python
"""The ingest role's engine. Jobs never use DATABASE_URL (the app role cannot write).

The ingest role writes data every model trains on, so a remote connection must verify the
server certificate and host name (D14). connect_args_for already builds a verify-full
context; this module additionally refuses a URL whose own query asks for a weaker mode, so a
copied `sslmode=require` URL fails loudly instead of depending on which setting wins.
"""

from __future__ import annotations

import ssl

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.ssl import connect_args_for

TLS_QUERY_KEYS = ("ssl", "sslmode")


def verified_connect_args(url: str) -> dict[str, object]:
    args = connect_args_for(url)
    if not args:
        return args
    query = make_url(url).query
    weaker = sorted(key for key in TLS_QUERY_KEYS if key in query and query[key] != "verify-full")
    if weaker:
        raise SystemExit(
            f"INGEST_DATABASE_URL sets {', '.join(weaker)} to something other than verify-full on a "
            "remote host; remove it (TLS is verified by app.db.ssl) or set verify-full"
        )
    context = args.get("ssl")
    if not (
        isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    ):
        raise SystemExit("INGEST_DATABASE_URL would connect to a remote host without verify-full TLS")
    return args


def ingest_engine() -> AsyncEngine:
    url = settings.INGEST_DATABASE_URL
    if not url:
        raise SystemExit("INGEST_DATABASE_URL is not set (the ingest role's URL); see DEPLOYMENT.md")
    return create_async_engine(url, poolclass=NullPool, connect_args=verified_connect_args(url))
```

`backend/app/config.py`, after `WORKER_HEARTBEAT_TTL_SECONDS`:

```python
    # Phase 2 data jobs connect as the least-privilege ingest role; never set on the API.
    INGEST_DATABASE_URL: str | None = None
```

`.env.example`, new section before the frontend block:

```
# --- Backend: data pipelines (Phase 2) -------------------------------------------
# The ingest role's URL (written by scripts/write_role_url.py). Empty everywhere except
# the data workflows and, from plan 7, the dedicated ingest service (never the general
# worker). Job CLIs refuse to run without it, and refuse a remote URL below verify-full.
INGEST_DATABASE_URL=
```

Append `"app.pipelines.ingest_log", "app.pipelines.db"` to the first strict mypy block.

- [ ] **Step 4: Run**

Run: `cd backend && uv run pytest tests/test_ingest_log.py tests/test_pipelines_db.py tests/test_env_example_parity.py tests/test_settings.py -q && uv run mypy && uv run ruff check . ../scripts/`
Expected: PASS (DB tests run in CI; they skip locally without `MIGRATIONS_TEST_ADMIN_URL`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipelines/ingest_log.py backend/app/pipelines/db.py backend/app/config.py .env.example \
  backend/tests/test_ingest_log.py backend/tests/test_pipelines_db.py backend/pyproject.toml
git commit -m "feat(pipelines): ingest run log, quarantine writer, verify-full ingest engine"
```

---

### Task 4: `ingest` and `trainer` roles, `internal` schema, Phase 2 grants

> **Superseded in part (2026-09-29 final review).** The code blocks below are the task as first written. The committed code, tests and SQL files are the source of truth where they differ (M7 narrowed `ingest`'s SELECT to `mp_routes`; I1 made R8 stamp only flagged rows).

**Files:**
- Create: `backend/db/roles/create_roles_phase2.sql`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`, `backend/tests/test_roles_phase2.py`
- Modify: `backend/scripts/write_role_url.py` (`ROLES`), `backend/tests/test_write_role_url.py`

**Interfaces:**
- Consumes: Phase 1 roles (`migrator`, `app`, `analyst`) already created by `create_roles.sql`; migration `0004`.
- Produces: role `ingest` (LOGIN); role `trainer` (NOLOGIN, no password, no grants — Phase 3 adds LOGIN and SELECT on training views) `[assumes D13]`; schema `internal AUTHORIZATION migrator`; env var `INGEST_PASSWORD` (plaintext, from `.env.ingest`); `write_role_url --role ingest [--generate-password]`. `grants_phase2.sql` and `verify_roles_phase2.sql` are **cumulative**: later plans append grants and expected rows.

Superseded by commits f73bad1/488c1b8 — `backend/db/roles/*` is authoritative (ingest has column-level SELECT on `mp_ticks` without `climber_name`); the code blocks below are the task as dispatched, not what shipped.

- [ ] **Step 1: Failing tests**

In `backend/tests/test_write_role_url.py` add:

```python
def test_ingest_role_generates_password_and_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OWNER_DATABASE_URL", "postgresql://owner:ownerpw@db.example.test/neondb?sslmode=require")
    env_file = tmp_path / ".env.ingest"
    assert main(["--role", "ingest", "--env-file", str(env_file), "--generate-password"]) == 0
    lines = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
    assert len(lines["INGEST_PASSWORD"]) == 64
    assert lines["INGEST_DATABASE_URL"].startswith("postgresql+asyncpg://ingest:")
    assert lines["INGEST_DATABASE_URL"].endswith("?ssl=verify-full")
    assert lines["INGEST_PASSWORD"] not in capsys.readouterr().out


def test_trainer_has_no_credential_until_phase_3(tmp_path, monkeypatch):
    monkeypatch.setenv("OWNER_DATABASE_URL", "postgresql://owner:ownerpw@db.example.test/neondb")
    with pytest.raises(SystemExit):
        main(["--role", "trainer", "--env-file", str(tmp_path / ".env.trainer"), "--generate-password"])
```

`backend/tests/test_roles_phase2.py`:

```python
"""Prod order, rehearsed: 0003 → create_roles.sql → create_roles_phase2.sql → migrator
upgrades to head → grants_phase2.sql → verify scripts."""

import asyncio
from urllib.parse import urlsplit

import asyncpg
import pytest
from alembic import command

from tests.pgtest import requires_pg
from tests.test_migrations import (
    ADMIN_URL,
    ANALYST_FIXTURE_SQL,
    OWNER_ROLE,
    PASSWORDS,
    ROLE_PASSWORD_ENV,
    ROLES_DIR,
    _alembic_cfg,
    _as,
    _denied,
    _fetch_row,
    _psql,
    _require_psql,
    _role_url,
    _run,
    fresh_db,  # noqa: F401  (fixture)
)

pytestmark = requires_pg
PHASE2_PASSWORDS = {"ingest": "test-ingest-password-0123456789abcdef0"}
PHASE2_PASSWORD_ENV = {"INGEST_PASSWORD": PHASE2_PASSWORDS["ingest"]}
ALL_ROLES = ("ingest", "trainer", "migrator", "app", "analyst", OWNER_ROLE)


def _drop_roles() -> None:
    assert ADMIN_URL is not None
    if urlsplit(ADMIN_URL).hostname not in ("localhost", "127.0.0.1"):
        raise RuntimeError("refusing to DROP ROLE on a non-local host")

    async def go() -> None:
        conn = await asyncpg.connect(ADMIN_URL)
        try:
            await conn.execute("DROP ROLE IF EXISTS " + ", ".join(ALL_ROLES))
        finally:
            await conn.close()

    asyncio.run(go())


@pytest.fixture
def phase2_roles_cleanup():
    _drop_roles()
    yield
    _drop_roles()


def test_phase2_roles_least_privilege(phase2_roles_cleanup, fresh_db):  # noqa: F811
    _require_psql()
    _run(
        fresh_db,
        f"CREATE ROLE {OWNER_ROLE} LOGIN NOSUPERUSER CREATEROLE PASSWORD '{PASSWORDS['owner']}';"
        f'ALTER DATABASE "{fresh_db}" OWNER TO {OWNER_ROLE};'
        "CREATE EXTENSION postgis;",
    )
    owner_url = _role_url(fresh_db, OWNER_ROLE, PASSWORDS["owner"])
    cfg = _alembic_cfg(fresh_db)
    cfg.set_main_option("sqlalchemy.url", owner_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    command.upgrade(cfg, "0003_hist_insufficient_data")
    _as(owner_url, ANALYST_FIXTURE_SQL)

    ok = _psql(owner_url, ROLES_DIR / "create_roles.sql", ROLE_PASSWORD_ENV)
    assert ok.returncode == 0, ok.stderr

    for bad in ("short-pw", "SCRAM-SHA-256$4096:c2FsdA==$c3RvcmVk:c2VydmVy" + "x" * 8, "md5" + "0" * 32):
        refused = _psql(owner_url, ROLES_DIR / "create_roles_phase2.sql", {"INGEST_PASSWORD": bad})
        assert refused.returncode != 0
        assert "INGEST_PASSWORD must be a plaintext password of at least 32 characters" in refused.stderr
        assert bad not in refused.stdout + refused.stderr

    created = _psql(owner_url, ROLES_DIR / "create_roles_phase2.sql", PHASE2_PASSWORD_ENV)
    assert created.returncode == 0, created.stderr
    for plaintext in PHASE2_PASSWORDS.values():
        assert plaintext not in created.stdout + created.stderr

    # A rerun stops at the secret-free guard, so no CREATE ROLE ... PASSWORD reaches the server log.
    rerun = _psql(owner_url, ROLES_DIR / "create_roles_phase2.sql", PHASE2_PASSWORD_ENV)
    assert rerun.returncode != 0
    assert "ingest/trainer already exist; do not rerun create_roles_phase2.sql" in rerun.stderr
    for plaintext in PHASE2_PASSWORDS.values():
        assert plaintext not in rerun.stdout + rerun.stderr

    migrator_url = _role_url(fresh_db, "migrator", PASSWORDS["migrator"])
    cfg.set_main_option("sqlalchemy.url", migrator_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    command.upgrade(cfg, "head")

    granted = _psql(owner_url, ROLES_DIR / "grants_phase2.sql", {})
    assert granted.returncode == 0, granted.stderr
    again = _psql(owner_url, ROLES_DIR / "grants_phase2.sql", {})
    assert again.returncode == 0, again.stderr  # idempotent

    for script in ("verify_roles.sql", "verify_roles_phase2.sql"):
        verified = _psql(owner_url, ROLES_DIR / script, {})
        assert verified.returncode == 0, verified.stdout + verified.stderr
        assert "PASSED" in verified.stdout

    ingest = _role_url(fresh_db, "ingest", PHASE2_PASSWORDS["ingest"])
    app = _role_url(fresh_db, "app", PASSWORDS["app"])

    _as(ingest, "INSERT INTO source_ingest_log (run_id, source, status) VALUES (gen_random_uuid(), 't', 'running')")
    _as(ingest, "INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) VALUES (900000001, 'total', 'all', 1)")
    _denied(ingest, "UPDATE internal.mp_tick_aggregates SET tick_count = 2 WHERE false")
    _as(ingest, "UPDATE mp_ticks SET quarantine_reason = NULL WHERE false")
    _denied(ingest, "UPDATE mp_ticks SET climber_name = 'x' WHERE false")
    _denied(ingest, "DELETE FROM source_ingest_log")
    _denied(ingest, "UPDATE historical_predictions SET risk_score = 1 WHERE false")
    _denied(ingest, "CREATE TABLE internal.nope (x int)")
    _denied(ingest, "CREATE TABLE public.nope (x int)")

    # trainer cannot log in and holds no table privilege anywhere until Phase 3 (D13).
    trainer_state = _fetch_row(
        fresh_db,
        "SELECT r.rolcanlogin, "
        "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "        WHERE n.nspname IN ('public', 'internal') AND c.relkind IN ('r', 'p', 'v', 'm') "
        "          AND has_table_privilege('trainer', c.oid, 'SELECT')) "
        "FROM pg_roles r WHERE r.rolname = 'trainer'",
    )
    assert list(trainer_state) == [False, False]

    _denied(app, "SELECT count(*) FROM internal.mp_tick_aggregates")
    _denied(app, "SELECT count(*) FROM internal.accidents_raw")
    _as(app, "SELECT count(*) FROM source_ingest_log")

    # The schema belongs to migrator, so a grant without SET ROLE would be a silent no-op.
    _as(owner_url, "SET ROLE migrator; GRANT USAGE ON SCHEMA internal TO app; RESET ROLE;")
    stray = _psql(owner_url, ROLES_DIR / "verify_roles_phase2.sql", {})
    assert stray.returncode != 0
    assert "app has no USAGE on schema internal" in stray.stderr
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_write_role_url.py tests/test_roles_phase2.py -q`
Expected: FAIL (`invalid choice: 'ingest'`; missing SQL files).

- [ ] **Step 3: Implement**

`backend/scripts/write_role_url.py`: `ROLES = ("migrator", "app", "ingest")`. `trainer` is left out on purpose: it has no login until Phase 3, which adds it here with its grants (argparse `choices` rejects it with exit code 2, which `test_trainer_has_no_credential_until_phase_3` expects as `SystemExit`).

`backend/db/roles/create_roles_phase2.sql`:

```sql
-- Run as the database owner, once, after create_roles.sql. Same mechanism as
-- create_roles.sql (PR #6): INGEST_PASSWORD comes via \getenv, never argv, and reaches the
-- server as plaintext over verify-full TLS; the server stores SCRAM-SHA-256.
\set ON_ERROR_STOP on
\set VERBOSITY terse
\set SHOW_CONTEXT never

-- Secret-free, so a rerun stops here instead of sending a CREATE ROLE ... PASSWORD that
-- fails and lands in the server log via log_min_error_statement.
SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('ingest', 'trainer')) AS roles_absent \gset
\if :roles_absent
\else
  DO $$ BEGIN RAISE EXCEPTION 'ingest/trainer already exist; do not rerun create_roles_phase2.sql'; END $$;
\endif

\getenv ingest_password INGEST_PASSWORD
\if :{?ingest_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'INGEST_PASSWORD is not set'; END $$;
\endif

-- A value shaped like a SCRAM or md5 hash is stored as that hash (Neon rejects it at
-- COMMIT); write_role_url --generate-password makes 64 hex chars.
SELECT length(:'ingest_password') >= 32
   AND :'ingest_password' !~ '^SCRAM-SHA-256\$'
   AND :'ingest_password' !~ '^md5[0-9a-f]{32}$' AS password_ok \gset
\if :password_ok
\else
  DO $$ BEGIN RAISE EXCEPTION 'INGEST_PASSWORD must be a plaintext password of at least 32 characters, not a SCRAM or md5 verifier'; END $$;
\endif

SELECT to_regrole('migrator') IS NOT NULL AND to_regrole('app') IS NOT NULL AND to_regrole('analyst') IS NOT NULL
  AS phase1_roles_ok \gset
\if :phase1_roles_ok
\else
  DO $$ BEGIN RAISE EXCEPTION 'run create_roles.sql first (migrator, app, analyst must exist)'; END $$;
\endif

BEGIN;

CREATE ROLE ingest LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'ingest_password';
-- D13: a placeholder with no login and no grants. default_transaction_read_only is a session
-- default any client can override, so it is not a boundary; Phase 3 adds LOGIN and SELECT on
-- training views only.
CREATE ROLE trainer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

-- migrator cannot create schemas (verify_roles.sql asserts it), so the owner does it here.
CREATE SCHEMA IF NOT EXISTS internal AUTHORIZATION migrator;
REVOKE ALL ON SCHEMA internal FROM PUBLIC;

GRANT USAGE ON SCHEMA public TO ingest;

COMMIT;

\echo 'roles ingest and trainer created; schema internal owned by migrator'
```

`backend/db/roles/grants_phase2.sql` (cumulative; later plans append inside the transaction):

```sql
-- Idempotent Phase 2 grants. Run as the owner after every Phase 2 migration, then
-- verify_roles_phase2.sql. GRANT on objects migrator owns needs SET ROLE migrator.
\set ON_ERROR_STOP on
BEGIN;
SET ROLE migrator;

GRANT USAGE ON SCHEMA internal TO ingest, analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA internal TO analyst;

-- Plan 1 (0004)
GRANT SELECT ON public.accidents, public.mp_routes, public.mp_locations, public.mp_ticks TO ingest;
GRANT SELECT, INSERT, UPDATE ON public.source_ingest_log TO ingest;
GRANT UPDATE (quarantine_reason, quarantine_rule_version) ON public.mp_ticks TO ingest;
-- INSERT only (spec): accepted months are closed, so a reload never needs to change them.
GRANT SELECT, INSERT ON internal.mp_tick_aggregates TO ingest;
GRANT SELECT, INSERT ON internal.ingest_quarantine TO ingest;
-- trainer: nothing until Phase 3 (D13).

RESET ROLE;
COMMIT;
\echo 'phase 2 grants applied'
```

The identity column on `ingest_quarantine` uses an implicit sequence; `GENERATED BY DEFAULT AS IDENTITY` needs no sequence grant for INSERT (identity sequences are used with the table owner's rights).

`backend/db/roles/verify_roles_phase2.sql`:

```sql
-- Read-only exact-privilege checks for the Phase 2 roles. Exits non-zero on any failure.
\set ON_ERROR_STOP on

CREATE TEMP TABLE role_checks (check_name text PRIMARY KEY, ok boolean NOT NULL);

INSERT INTO role_checks
SELECT 'role exists: ' || r, EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r)
FROM unnest(ARRAY['ingest', 'trainer']) AS r;

INSERT INTO role_checks
SELECT 'no elevated attributes, noinherit: ' || rolname,
       NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit)
FROM pg_roles WHERE rolname IN ('ingest', 'trainer');

INSERT INTO role_checks
SELECT 'no role memberships: ' || r,
       NOT EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles pr ON pr.oid = m.member WHERE pr.rolname = r)
FROM unnest(ARRAY['ingest', 'trainer']) AS r;

INSERT INTO role_checks VALUES
  ('trainer cannot log in until Phase 3', NOT (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'trainer')),
  ('ingest can log in', (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'ingest')),
  ('schema internal is owned by migrator',
     (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'internal') = 'migrator'),
  ('app has no USAGE on schema internal', NOT has_schema_privilege('app', 'internal', 'USAGE')),
  ('trainer has no USAGE on schema internal', NOT has_schema_privilege('trainer', 'internal', 'USAGE')),
  ('ingest cannot CREATE in internal', NOT has_schema_privilege('ingest', 'internal', 'CREATE')),
  ('ingest cannot CREATE in public', NOT has_schema_privilege('ingest', 'public', 'CREATE')),
  ('trainer cannot CREATE in public', NOT has_schema_privilege('trainer', 'public', 'CREATE'));

-- The exact set of tables ingest may write. Later plans append rows here.
CREATE TEMP TABLE ingest_writes (tbl text, priv text);
INSERT INTO ingest_writes VALUES
  ('public.source_ingest_log', 'INSERT'), ('public.source_ingest_log', 'UPDATE'),
  ('internal.mp_tick_aggregates', 'INSERT'),
  ('internal.ingest_quarantine', 'INSERT');

WITH tables AS (
  SELECT c.oid, n.nspname || '.' || c.relname AS tbl
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname IN ('public', 'internal') AND c.relkind IN ('r', 'p')
    AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
), privs AS (
  SELECT t.tbl, p.priv, has_table_privilege('ingest', t.oid, p.priv) AS held
  FROM tables t CROSS JOIN unnest(ARRAY['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) AS p(priv)
)
INSERT INTO role_checks
SELECT 'ingest ' || priv || ' on ' || tbl || ' matches the expected set',
       held = EXISTS (SELECT 1 FROM ingest_writes w WHERE w.tbl = privs.tbl AND w.priv = privs.priv)
FROM privs;

WITH tables AS (
  SELECT c.oid, n.nspname || '.' || c.relname AS tbl
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname IN ('public', 'internal') AND c.relkind IN ('r', 'p', 'v', 'm')
)
INSERT INTO role_checks
SELECT 'app holds nothing on internal relation ' || tbl, NOT has_table_privilege('app', oid, 'SELECT')
FROM tables WHERE tbl LIKE 'internal.%'
UNION ALL
SELECT 'trainer holds no privilege on ' || tbl,
       NOT (has_table_privilege('trainer', oid, 'SELECT') OR has_table_privilege('trainer', oid, 'INSERT')
            OR has_table_privilege('trainer', oid, 'UPDATE') OR has_table_privilege('trainer', oid, 'DELETE')
            OR has_table_privilege('trainer', oid, 'TRUNCATE'))
FROM tables;

INSERT INTO role_checks VALUES
  ('ingest may update only the quarantine columns of mp_ticks',
     has_column_privilege('ingest', 'public.mp_ticks', 'quarantine_reason', 'UPDATE')
     AND NOT has_column_privilege('ingest', 'public.mp_ticks', 'climber_name', 'UPDATE')
     AND NOT has_column_privilege('ingest', 'public.mp_ticks', 'tick_date', 'UPDATE'));

SELECT check_name, ok FROM role_checks ORDER BY ok, check_name;

DO $$
DECLARE failed text;
BEGIN
  SELECT string_agg(check_name, '; ') INTO failed FROM role_checks WHERE NOT ok;
  IF failed IS NOT NULL THEN RAISE EXCEPTION 'PHASE 2 ROLE CHECKS FAILED: %', failed; END IF;
END $$;

\echo 'ALL PHASE 2 ROLE CHECKS PASSED'
```

Note: a column-level `UPDATE` grant makes `has_table_privilege(..., 'UPDATE')` false, so the `mp_ticks` table-level row in the expected-set check correctly expects no table-level UPDATE.

- [ ] **Step 4: Run**

Run: `cd backend && MIGRATIONS_TEST_ADMIN_URL=postgresql://test_user:test_password@localhost:5432/postgres uv run pytest tests/test_roles_phase2.py tests/test_write_role_url.py tests/test_migrations.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db/roles/create_roles_phase2.sql backend/db/roles/grants_phase2.sql backend/db/roles/verify_roles_phase2.sql \
  backend/scripts/write_role_url.py backend/tests/test_write_role_url.py backend/tests/test_roles_phase2.py
git commit -m "feat(db): ingest role, NOLOGIN trainer placeholder, internal schema, Phase 2 grants and checks"
```

---

### Task 5: R8 — quarantine garbage `mp_ticks` rows

> **Superseded in part (2026-09-29 final review).** The code blocks below are the task as first written. The committed code, tests and SQL files are the source of truth where they differ (M7 narrowed `ingest`'s SELECT to `mp_routes`; I1 made R8 stamp only flagged rows).

**Files:**
- Create: `backend/app/pipelines/mp_ticks_quarantine.py`, `backend/tests/test_mp_ticks_quarantine.py`, `backend/tests/verify/__init__.py`, `backend/tests/verify/_db.py`, `backend/tests/verify/test_phase2a_foundation.py`
- Modify: `backend/pyproject.toml` (mypy allowlist; `db` marker; default deselection)

**Interfaces:**
- Consumes: `ingest_log.start_run/finish_run` (Task 3), `ValidationReport` (Task 1), `db.ingest_engine` (Task 3), `temporal_weighting.utc_today()` (existing, `app/services/temporal_weighting.py:25`).
- Produces: `RULE_VERSION = "r8-v1"`, `CAPTURE_SLACK = timedelta(days=1)`, `classify_tick(tick_date: date | None, captured_on: date | None, route_known: bool, today: date) -> str | None`, `QUARANTINE_SQL: str`, `async run(conn: AsyncConnection, *, today: date) -> dict[str, int]` (counts by reason after the run), CLI `python -m app.pipelines.mp_ticks_quarantine`; `tests.verify._db.fetch(sql: str) -> list[asyncpg.Record]` (analyst, verify-full via `connect_args_for`; skips without `VERIFY_DATABASE_URL`). `[assumes D3]`

- [ ] **Step 1: Failing tests**

`backend/tests/test_mp_ticks_quarantine.py`:

```python
import asyncio
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.pipelines.mp_ticks_quarantine import classify_tick, run
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)


def test_tick_after_capture_day_is_future_even_when_before_today():
    assert classify_tick(date(2026, 3, 1), date(2026, 2, 8), True, TODAY) == "future"
    assert classify_tick(date(2026, 2, 10), date(2026, 2, 8), True, TODAY) == "future"


def test_tick_one_day_after_capture_is_allowed_for_writer_zone_slack():
    # created_at holds the writer session's local time, which may be up to 10 h behind UTC.
    assert classify_tick(date(2026, 2, 9), date(2026, 2, 8), True, TODAY) is None


def test_tick_after_today_is_future_when_capture_day_unknown():
    assert classify_tick(date(2026, 9, 29), None, True, TODAY) == "future"
    assert classify_tick(date(2026, 9, 28), None, True, TODAY) is None


def test_precedence_future_then_orphan_then_pre_1970():
    assert classify_tick(date(3901, 1, 1), date(2026, 2, 8), False, TODAY) == "future"
    assert classify_tick(date(1965, 1, 1), date(2026, 2, 8), False, TODAY) == "orphan_route"
    assert classify_tick(date(1965, 1, 1), date(2026, 2, 8), True, TODAY) == "pre_1970"
    assert classify_tick(None, date(2026, 2, 8), True, TODAY) is None


SEED = """
INSERT INTO mp_locations (mp_id, name) VALUES (900000100, 'Fixture Area');
INSERT INTO mp_routes (mp_route_id, name, location_id) VALUES (900000001, 'Fixture Route', 900000100);
INSERT INTO mp_ticks (tick_id, route_id, climber_name, tick_date, created_at) VALUES
  (1, '900000001', 'c', '2025-01-04', '2026-02-08 10:00'),
  (2, '900000001', 'c', '2026-03-01', '2026-02-08 10:00'),
  (3, '900000001', 'c', '3901-01-15', '2026-02-08 10:00'),
  (4, '900000999', 'c', '2025-01-04', '2026-02-08 10:00'),
  (5, 'abc',       'c', '2025-01-04', '2026-02-08 10:00'),
  (6, '900000001', 'c', '1965-06-01', '2026-02-08 10:00'),
  (7, '900000001', 'c', NULL,         '2026-02-08 10:00'),
  (8, '900000001', 'c', '2026-02-09', '2026-02-08 23:00');
"""


@requires_pg
def test_sql_matches_the_python_rule_and_is_idempotent():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                counts = await run(conn, today=TODAY)
            assert counts == {"future": 2, "orphan_route": 2, "pre_1970": 1, "clean": 3}
            async with engine.begin() as conn:
                again = await run(conn, today=TODAY)
                changed = (await conn.execute(text(
                    "SELECT rows_upserted FROM source_ingest_log WHERE source = 'mp_ticks_quarantine' "
                    "ORDER BY finished_at DESC LIMIT 1"))).scalar_one()
            assert again == counts
            assert changed == 0
            async with engine.connect() as conn:
                rows = dict((await conn.execute(text("SELECT tick_id, quarantine_reason FROM mp_ticks"))).all())
            assert rows == {
                1: None, 2: "future", 3: "future", 4: "orphan_route", 5: "orphan_route", 6: "pre_1970", 7: None, 8: None,
            }
        finally:
            await engine.dispose()

    with migrated_db(seed_sql=SEED) as name:
        asyncio.run(scenario(sa_url(name)))
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_mp_ticks_quarantine.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`backend/app/pipelines/mp_ticks_quarantine.py`:

```python
"""R8: flag garbage mp_ticks rows instead of deleting them.

"future" is relative to the day the row was captured (created_at) and to today at run
time, whichever is earlier: a tick dated after its own capture day cannot be real, and
stays flagged even once the calendar passes it.

created_at is `timestamp without time zone DEFAULT CURRENT_TIMESTAMP`, so it holds the
writer session's local time, which no later session can prove (SHOW TimeZone only shows the
reader's). US zones are at most 10 h behind UTC, so one day of slack on the capture day keeps
a real tick from being flagged whatever zone the writer used; "after today" stays strict.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

RULE_VERSION = "r8-v1"
EPOCH = date(1970, 1, 1)
CAPTURE_SLACK = timedelta(days=1)


def classify_tick(tick_date: date | None, captured_on: date | None, route_known: bool, today: date) -> str | None:
    cutoff = min(today, captured_on + CAPTURE_SLACK) if captured_on is not None else today
    if tick_date is not None and tick_date > cutoff:
        return "future"
    if not route_known:
        return "orphan_route"
    if tick_date is not None and tick_date < EPOCH:
        return "pre_1970"
    return None


# Mirrors classify_tick; test_sql_matches_the_python_rule_and_is_idempotent pins them together.
QUARANTINE_SQL = """
WITH classified AS (
  SELECT t.tick_id,
         CASE
           WHEN t.tick_date > LEAST(CAST(:today AS date), t.created_at::date + 1) THEN 'future'
           -- Nested CASE, not OR: Postgres does not promise to short-circuit OR, and
           -- 'abc'::bigint would abort the whole statement.
           WHEN CASE WHEN t.route_id ~ '^[0-9]{1,18}$'
                     THEN NOT EXISTS (SELECT 1 FROM mp_routes r WHERE r.mp_route_id = t.route_id::bigint)
                     ELSE true END THEN 'orphan_route'
           WHEN t.tick_date < DATE '1970-01-01' THEN 'pre_1970'
         END AS reason
  FROM mp_ticks t
)
UPDATE mp_ticks m
SET quarantine_reason = c.reason, quarantine_rule_version = :rule_version
FROM classified c
WHERE m.tick_id = c.tick_id
  AND (m.quarantine_reason IS DISTINCT FROM c.reason OR m.quarantine_rule_version IS DISTINCT FROM :rule_version)
"""

COUNTS_SQL = "SELECT coalesce(quarantine_reason, 'clean'), count(*) FROM mp_ticks GROUP BY 1"


async def run(conn: AsyncConnection, *, today: date) -> dict[str, int]:
    run_id = await start_run(conn, source="mp_ticks_quarantine", window_start=None, window_end=today, content_sha256=None)
    changed = (await conn.execute(text(QUARANTINE_SQL), {"today": today, "rule_version": RULE_VERSION})).rowcount
    counts = {str(reason): int(n) for reason, n in (await conn.execute(text(COUNTS_SQL))).all()}
    report = ValidationReport("mp_ticks_quarantine")
    report.rows_in = sum(counts.values())
    report.accepted = counts.get("clean", 0)
    for reason, n in counts.items():
        if reason != "clean":
            report.quarantined[reason] = n
    await finish_run(conn, run_id, status="ok", report=report, rows_upserted=changed)
    return counts


async def _main() -> None:
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            counts = await run(conn, today=utc_today())
    finally:
        await engine.dispose()
    print(json.dumps({"rule_version": RULE_VERSION, "counts": counts}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
```

`rowcount` counts only rows whose flag changed, so a second run logs `rows_upserted = 0`.

Append `"app.pipelines.mp_ticks_quarantine"` to the first strict mypy block. In `[tool.pytest.ini_options]` add the marker `"db: read-only acceptance checks against a Neon branch as analyst; needs VERIFY_DATABASE_URL; run with -m db"` and change `addopts`' selector to `-m 'not needs_data and not db'`.

`backend/tests/verify/__init__.py`: empty.

`backend/tests/verify/_db.py` (every later plan's `tests/verify` module uses `fetch` rather than its own connect, so every acceptance run verifies TLS the way the app does):

```python
"""Read-only connection for -m db acceptance checks. VERIFY_DATABASE_URL is the analyst URL as
write_role_url wrote it (any driver prefix or ssl query is fine): the DSN is rebuilt without its
query and TLS comes from app.db.ssl.connect_args_for, i.e. verify-full against Neon."""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from app.db.ssl import connect_args_for

URL = os.environ.get("VERIFY_DATABASE_URL")


def fetch(sql: str) -> list[asyncpg.Record]:
    if not URL:
        pytest.skip("VERIFY_DATABASE_URL not set")
    dsn = make_url(URL).set(drivername="postgresql", query={}).render_as_string(hide_password=False)
    connect_args = connect_args_for(URL)

    async def go() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(dsn, **connect_args)
        try:
            return await conn.fetch(sql)
        finally:
            await conn.close()

    return asyncio.run(go())
```

`backend/tests/verify/test_phase2a_foundation.py`:

```python
"""Acceptance checks for plan 1, run by the owner/agent against a Neon branch or prod as
the read-only analyst role: VERIFY_DATABASE_URL=<analyst url> uv run pytest -m db tests/verify."""

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db


def test_accidents_raw_matches_live_row_count():
    [row] = fetch("SELECT (SELECT count(*) FROM internal.accidents_raw) = (SELECT count(*) FROM accidents) AS same")
    assert row["same"]


def test_no_future_tick_is_unflagged():
    [row] = fetch(
        "SELECT count(*) AS n FROM mp_ticks WHERE quarantine_reason IS NULL "
        "AND tick_date > LEAST((now() AT TIME ZONE 'UTC')::date, created_at::date + 1)"
    )
    assert row["n"] == 0


def test_r8_flags_are_known_reasons_under_the_current_rule_version():
    rows = fetch(
        "SELECT coalesce(quarantine_reason, 'clean') AS r, count(*) AS n, "
        "bool_and(quarantine_rule_version = 'r8-v1') AS versioned FROM mp_ticks GROUP BY 1"
    )
    assert {r["r"] for r in rows} <= {"clean", "future", "orphan_route", "pre_1970"}
    assert all(r["versioned"] for r in rows)
    assert sum(r["n"] for r in rows if r["r"] != "clean") > 0
```

Plan 4's `0009` moves `mp_ticks` to `internal.mp_ticks`; that plan updates these two queries in the same commit.

- [ ] **Step 4: Run**

Run: `cd backend && uv run pytest tests/test_mp_ticks_quarantine.py -q && uv run pytest -q --co -m db tests/verify | tail -1 && uv run mypy && uv run ruff check . ../scripts/`
Expected: unit tests PASS; the DB test PASSES in CI; `-m db` collects 3 tests; default `uv run pytest` does not collect them.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipelines/mp_ticks_quarantine.py backend/tests/test_mp_ticks_quarantine.py \
  backend/tests/verify/ backend/pyproject.toml
git commit -m "feat(pipelines): R8 mp_ticks quarantine relative to capture day and run-time today"
```

---

### Task 6: Loader for the private MP ice/mixed tick-aggregate export

**Files:**
- Create: `backend/app/pipelines/mp_tick_aggregates.py`, `backend/tests/test_mp_tick_aggregates.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes:
  - Tasks 1 and 3; `internal.mp_tick_aggregates` (Task 2).
  - The private export's table `route_tick_totals(mp_route_id, total_ticks, last_page, pages_fetched, complete, scraped_at)`, read from `~/Developer/safeascent-private/mp_ticks/src/mp_ticks/db.py` on 2026-09-28. `total_ticks` is the total MP reported for the route at scrape time.
  - The private export's table `route_tick_monthly(mp_route_id, year_month, style, n)`, with styles `lead|follow|tr|solo|unknown`.
- Produces:
  - `is_ice_mixed(route_type: str | None) -> bool`
  - `read_export(path: Path) -> tuple[list[RouteTotal], list[MonthlyRow]]`
  - `validate(totals, monthly, *, route_types: dict[int, str | None], existing: dict[tuple[int, str, str], int], today: date) -> tuple[list[AggregateRow], ValidationReport, dict[str, int]]`
  - `async existing_counts(conn, route_ids: set[int]) -> dict[tuple[int, str, str], int]`
  - `async load(conn, rows: list[AggregateRow], *, run_id: uuid.UUID, scrape_run_id: str) -> int` (INSERT … ON CONFLICT DO NOTHING)
  - `async main(path: Path, *, today: date, max_quarantine_share: float, dry_run: bool) -> dict[str, object]`
  - CLI `python -m app.pipelines.mp_tick_aggregates --sqlite PATH [--dry-run] [--max-quarantine-share 0.10]`
  - `[assumes D3]`

Semantics:
- **Row kinds.** `period = 'total', style = 'all'` is **MP's reported total** for the route at scrape time, not a sum of our accepted months. Monthly rows are stored only for closed, complete months.
- **Month cut-off.** Each route's cut-off is `min(today, scraped_at)`.
  - A month at or after the route's scrape month is `partial_month`. The scrape could not have seen the whole month, and a later reload of the same file never changes that.
  - Months after the current UTC month are `future_month`.
- **Scope.** Only routes whose `mp_routes.type` names ice or mixed are accepted; others are `not_ice_mixed` (P2-14's scope).
- **INSERT-only.** `ingest` holds INSERT, not UPDATE (spec).
  - A row already stored with the same count counts as `already_loaded`.
  - A row already stored with a different count (a later scrape saw late-logged ticks, or MP's total grew) is quarantined `count_changed` with both values. The drift is measured and never silently applied.

- [ ] **Step 1: Failing tests**

`backend/tests/test_mp_tick_aggregates.py`:

```python
import ast
import asyncio
import sqlite3
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.pipelines.mp_tick_aggregates as loader
from app.pipelines.mp_tick_aggregates import is_ice_mixed, main, read_export, validate
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)
R1, R2, R3, R4 = 900000001, 900000002, 900000003, 900000004
SCRAPED = "2026-09-20T00:00:00+00:00"
ICE = {R1: "Ice", R2: "Ice, Mixed", R4: "Trad"}


def _export(
    tmp_path: Path,
    monthly: list[tuple[int, str, str, int]],
    totals: list[tuple[int, int, int]],
    scraped_at: str = SCRAPED,
    name: str = "ticks.sqlite",
) -> Path:
    path = tmp_path / name
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE route_tick_totals (mp_route_id INTEGER PRIMARY KEY, total_ticks INTEGER NOT NULL, "
        "last_page INTEGER NOT NULL, pages_fetched INTEGER NOT NULL, complete INTEGER NOT NULL, scraped_at TEXT NOT NULL);"
        "CREATE TABLE route_tick_monthly (mp_route_id INTEGER NOT NULL, year_month TEXT NOT NULL, style TEXT NOT NULL, "
        "n INTEGER NOT NULL, PRIMARY KEY (mp_route_id, year_month, style));"
    )
    db.executemany("INSERT INTO route_tick_monthly VALUES (?, ?, ?, ?)", monthly)
    db.executemany(
        "INSERT INTO route_tick_totals VALUES (?, ?, 1, 1, ?, ?)", [(r, t, c, scraped_at) for r, t, c in totals]
    )
    db.commit()
    db.close()
    return path


def _keys(rows):
    return {(r.mp_route_id, r.period, r.style, r.tick_count) for r in rows}


def test_loader_imports_no_network_client():
    tree = ast.parse(Path(loader.__file__).read_text())
    imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"httpx", "requests", "urllib", "aiohttp", "http", "socket"}


def test_ice_mixed_detection():
    assert is_ice_mixed("Ice") and is_ice_mixed("Trad, Mixed, Alpine") and is_ice_mixed("ice")
    assert not is_ice_mixed("Trad") and not is_ice_mixed(None) and not is_ice_mixed("Sport, Alpine")


def test_scrape_month_and_later_are_partial_not_dropped(tmp_path):
    path = _export(tmp_path, [(R1, "2026-09", "lead", 2), (R1, "2026-01", "lead", 3)], [(R1, 5, 1)])
    totals, monthly = read_export(path)
    for today in (TODAY, date(2026, 10, 5), date(2027, 3, 1)):
        rows, report, _ = validate(totals, monthly, route_types=ICE, existing={}, today=today)
        assert report.quarantined == {"partial_month": 1}
        assert _keys(rows) == {(R1, "2026-01", "lead", 3), (R1, "total", "all", 5)}


def test_future_months_bad_values_scope_and_incomplete_routes_are_quarantined(tmp_path):
    path = _export(
        tmp_path,
        [
            (R1, "2026-10", "lead", 1),
            (R1, "3901-01", "tr", 1),
            (R1, "1965-02", "lead", 1),
            (R1, "2025-13", "lead", 1),
            (R1, "2025-02", "lead", 0),
            (R1, "2025-02", "bogus", 1),
            (R2, "2025-02", "lead", 4),
            (R3, "2025-02", "lead", 4),
            (R4, "2025-02", "lead", 1),
        ],
        [(R1, 9, 1), (R2, 4, 0), (R3, 4, 1), (R4, 1, 1)],
    )
    totals, monthly = read_export(path)
    rows, report, stats = validate(totals, monthly, route_types=ICE, existing={}, today=TODAY)
    assert report.quarantined == {
        "future_month": 2,
        "pre_1970": 1,
        "bad_year_month": 1,
        "nonpositive_count": 1,
        "bad_style": 1,
        "route_incomplete": 2,
        "unknown_route": 2,
        "not_ice_mixed": 2,
    }
    assert _keys(rows) == {(R1, "total", "all", 9)}
    assert stats == {
        "routes": 4,
        "mp_reported_total": 18,
        "accepted_month_ticks": 0,
        "already_loaded": 0,
        "total_mismatch_routes": 1,
    }


def test_a_scrape_stamped_after_today_is_quarantined(tmp_path):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3)], [(R1, 3, 1)], scraped_at="2026-10-02T00:00:00+00:00")
    totals, monthly = read_export(path)
    rows, report, _ = validate(totals, monthly, route_types=ICE, existing={}, today=TODAY)
    assert rows == []
    assert report.quarantined == {"scrape_after_today": 2}


def test_stored_rows_are_never_overwritten(tmp_path):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 4), (R1, "2025-03", "lead", 1)], [(R1, 7, 1)])
    totals, monthly = read_export(path)
    existing = {(R1, "2025-01", "lead"): 3, (R1, "total", "all"): 7}
    rows, report, stats = validate(totals, monthly, route_types=ICE, existing=existing, today=TODAY)
    assert _keys(rows) == {(R1, "2025-03", "lead", 1)}
    assert report.quarantined == {"count_changed": 1}
    assert stats["already_loaded"] == 1


def _seed_ice_route() -> str:
    return (
        "INSERT INTO mp_locations (mp_id, name) VALUES (900000100, 'Fixture Area');"
        f"INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES ({R1}, 'Fixture Ice', 900000100, 'Ice');"
    )


async def _stored(url: str) -> list[tuple[str, str, int]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT period, style, tick_count FROM internal.mp_tick_aggregates ORDER BY period, style"))
            return [tuple(r) for r in result.all()]
    finally:
        await engine.dispose()


@requires_pg
def test_second_identical_load_is_a_noop(tmp_path, monkeypatch):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3), (R1, "2025-02", "follow", 2)], [(R1, 5, 1)])
    with migrated_db(seed_sql=_seed_ice_route()) as name:
        url = sa_url(name)
        monkeypatch.setattr(loader, "ingest_engine", lambda: create_async_engine(url))
        first = asyncio.run(main(path, today=TODAY, max_quarantine_share=0.1, dry_run=False))
        second = asyncio.run(main(path, today=TODAY, max_quarantine_share=0.1, dry_run=False))
        assert first["status"] == "ok" and first["rows_upserted"] == 3
        assert second["status"] == "noop"
        assert asyncio.run(_stored(url)) == [("2025-01", "lead", 3), ("2025-02", "follow", 2), ("total", "all", 5)]


@requires_pg
def test_reload_in_a_later_month_is_still_a_noop(tmp_path, monkeypatch):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3), (R1, "2026-09", "lead", 1)], [(R1, 4, 1)])
    with migrated_db(seed_sql=_seed_ice_route()) as name:
        url = sa_url(name)
        monkeypatch.setattr(loader, "ingest_engine", lambda: create_async_engine(url))
        first = asyncio.run(main(path, today=TODAY, max_quarantine_share=0.5, dry_run=False))
        later = asyncio.run(main(path, today=date(2026, 11, 2), max_quarantine_share=0.5, dry_run=False))
        assert first["status"] == "ok"
        assert later["status"] == "noop"
        assert ("2026-09", "lead", 1) not in asyncio.run(_stored(url))


@requires_pg
def test_changed_counts_are_quarantined_never_overwritten(tmp_path, monkeypatch):
    first_path = _export(tmp_path, [(R1, "2025-01", "lead", 3)], [(R1, 3, 1)], name="a.sqlite")
    second_path = _export(
        tmp_path, [(R1, "2025-01", "lead", 4), (R1, "2025-03", "lead", 1)], [(R1, 5, 1)],
        scraped_at="2026-09-25T00:00:00+00:00", name="b.sqlite",
    )
    with migrated_db(seed_sql=_seed_ice_route()) as name:
        url = sa_url(name)
        monkeypatch.setattr(loader, "ingest_engine", lambda: create_async_engine(url))
        asyncio.run(main(first_path, today=TODAY, max_quarantine_share=0.9, dry_run=False))
        second = asyncio.run(main(second_path, today=TODAY, max_quarantine_share=0.9, dry_run=False))
        assert second["status"] == "ok" and second["rows_upserted"] == 1
        assert second["report"]["quarantined"] == {"count_changed": 2}
        assert asyncio.run(_stored(url)) == [("2025-01", "lead", 3), ("2025-03", "lead", 1), ("total", "all", 3)]


@requires_pg
def test_heavy_quarantine_rejects_the_batch_and_writes_nothing(tmp_path, monkeypatch):
    path = _export(tmp_path, [(R1, "2026-10", "lead", 1), (R1, "2025-01", "lead", 1)], [(R1, 2, 1)])
    with migrated_db(seed_sql=_seed_ice_route()) as name:
        url = sa_url(name)
        monkeypatch.setattr(loader, "ingest_engine", lambda: create_async_engine(url))
        result = asyncio.run(main(path, today=TODAY, max_quarantine_share=0.1, dry_run=False))
        assert result["status"] == "rejected"
        assert result["rows_upserted"] == 0
        assert asyncio.run(_stored(url)) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_mp_tick_aggregates.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`backend/app/pipelines/mp_tick_aggregates.py`:

```python
"""Load the privately scraped MP ice/mixed tick aggregates (P2-14) into
internal.mp_tick_aggregates. Reads a local SQLite export only; it never fetches anything.

Months are the finest grain in the export, so completeness is judged per month (D3): a month
at or after the route's scrape month (cut-off min(today, scraped_at)) is partial for this
export forever, and months after the current UTC month are future. The outcome depends only
on the file, so reloading it in a later month is a no-op rather than a way to accept a
partial month. The table is INSERT-only: a stored count is never overwritten, and a
different count from a later scrape is quarantined with both values.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.db import ingest_engine
from app.pipelines.ingest_log import find_completed, finish_run, sha256_rows, start_run, write_quarantine
from app.pipelines.validate import ValidationReport, batch_gate

SOURCE = "mp_tick_aggregates"
STYLES = frozenset({"lead", "follow", "tr", "solo", "unknown"})
YEAR_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
ICE_MIXED = re.compile(r"\b(ice|mixed)\b", re.IGNORECASE)


@dataclass(frozen=True)
class RouteTotal:
    mp_route_id: int
    total_ticks: int
    complete: bool
    scraped_at: datetime


@dataclass(frozen=True)
class MonthlyRow:
    mp_route_id: int
    year_month: str
    style: str
    n: int


@dataclass(frozen=True)
class AggregateRow:
    mp_route_id: int
    period: str
    style: str
    tick_count: int
    scraped_at: datetime


def is_ice_mixed(route_type: str | None) -> bool:
    return route_type is not None and ICE_MIXED.search(route_type) is not None


def read_export(path: Path) -> tuple[list[RouteTotal], list[MonthlyRow]]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        totals = [
            RouteTotal(int(r), int(t), bool(c), datetime.fromisoformat(s))
            for r, t, c, s in db.execute("SELECT mp_route_id, total_ticks, complete, scraped_at FROM route_tick_totals")
        ]
        monthly = [
            MonthlyRow(int(r), str(ym), str(st), int(n))
            for r, ym, st, n in db.execute("SELECT mp_route_id, year_month, style, n FROM route_tick_monthly")
        ]
    finally:
        db.close()
    return totals, monthly


def _route_problem(route_id: int, total: RouteTotal | None, route_types: dict[int, str | None], today: date) -> str | None:
    if route_id not in route_types:
        return "unknown_route"
    if not is_ice_mixed(route_types[route_id]):
        return "not_ice_mixed"
    if total is None or not total.complete:
        return "route_incomplete"
    if total.scraped_at.tzinfo is None:
        return "scrape_time_unzoned"
    if total.scraped_at.astimezone(UTC).date() > today:
        return "scrape_after_today"
    return None


def _month_problem(year_month: str, *, today: date, cutoff: date) -> str | None:
    match = YEAR_MONTH.match(year_month)
    if match is None:
        return "bad_year_month"
    year, month = int(match.group(1)), int(match.group(2))
    if (year, month) > (today.year, today.month):
        return "future_month"
    if (year, month) >= (cutoff.year, cutoff.month):
        return "partial_month"
    if year < 1970:
        return "pre_1970"
    return None


def _accept_or_compare(
    row: AggregateRow,
    ref: str,
    existing: dict[tuple[int, str, str], int],
    report: ValidationReport,
    accepted: list[AggregateRow],
    stats: dict[str, int],
) -> bool:
    stored = existing.get((row.mp_route_id, row.period, row.style))
    if stored is not None and stored != row.tick_count:
        report.quarantine(ref, "count_changed", stored=stored, exported=row.tick_count)
        return False
    report.accept()
    if stored is None:
        accepted.append(row)
    else:
        stats["already_loaded"] += 1
    return True


def validate(
    totals: list[RouteTotal],
    monthly: list[MonthlyRow],
    *,
    route_types: dict[int, str | None],
    existing: dict[tuple[int, str, str], int],
    today: date,
) -> tuple[list[AggregateRow], ValidationReport, dict[str, int]]:
    by_route = {t.mp_route_id: t for t in totals}
    report = ValidationReport(SOURCE)
    accepted: list[AggregateRow] = []
    stats = {
        "routes": len(by_route),
        "mp_reported_total": sum(t.total_ticks for t in totals),
        "accepted_month_ticks": 0,
        "already_loaded": 0,
        "total_mismatch_routes": 0,
    }
    raw_sums: dict[int, int] = {}
    for row in monthly:
        raw_sums[row.mp_route_id] = raw_sums.get(row.mp_route_id, 0) + row.n
        ref = f"{row.mp_route_id}:{row.year_month}:{row.style}"
        total = by_route.get(row.mp_route_id)
        if (problem := _route_problem(row.mp_route_id, total, route_types, today)) is not None:
            report.quarantine(ref, problem)
            continue
        assert total is not None
        cutoff = min(today, total.scraped_at.astimezone(UTC).date())
        if (problem := _month_problem(row.year_month, today=today, cutoff=cutoff)) is not None:
            report.quarantine(ref, problem, n=row.n)
        elif row.style not in STYLES:
            report.quarantine(ref, "bad_style", style=row.style)
        elif row.n <= 0:
            report.quarantine(ref, "nonpositive_count", n=row.n)
        else:
            candidate = AggregateRow(row.mp_route_id, row.year_month, row.style, row.n, total.scraped_at)
            if _accept_or_compare(candidate, ref, existing, report, accepted, stats):
                stats["accepted_month_ticks"] += row.n
    for total in totals:
        ref = f"{total.mp_route_id}:total"
        if (problem := _route_problem(total.mp_route_id, total, route_types, today)) is not None:
            report.quarantine(ref, problem)
        elif total.total_ticks < 0:
            report.quarantine(ref, "bad_total", total=total.total_ticks)
        else:
            candidate = AggregateRow(total.mp_route_id, "total", "all", total.total_ticks, total.scraped_at)
            _accept_or_compare(candidate, ref, existing, report, accepted, stats)
            if raw_sums.get(total.mp_route_id, 0) != total.total_ticks:
                stats["total_mismatch_routes"] += 1
    return accepted, report, stats


async def existing_counts(conn: AsyncConnection, route_ids: set[int]) -> dict[tuple[int, str, str], int]:
    if not route_ids:
        return {}
    result = await conn.execute(
        text(
            "SELECT mp_route_id, period, style, tick_count FROM internal.mp_tick_aggregates "
            "WHERE mp_route_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": sorted(route_ids)},
    )
    return {(int(r), str(p), str(s)): int(n) for r, p, s, n in result.all()}


async def load(conn: AsyncConnection, rows: list[AggregateRow], *, run_id: uuid.UUID, scrape_run_id: str) -> int:
    if not rows:
        return 0
    await conn.execute(
        text(
            "INSERT INTO internal.mp_tick_aggregates "
            "(mp_route_id, period, style, tick_count, scrape_run_id, scraped_at, loaded_run_id) "
            "VALUES (:mp_route_id, :period, :style, :tick_count, :scrape_run_id, :scraped_at, :run_id) "
            "ON CONFLICT (mp_route_id, period, style) DO NOTHING"
        ),
        [
            {
                "mp_route_id": r.mp_route_id,
                "period": r.period,
                "style": r.style,
                "tick_count": r.tick_count,
                "scrape_run_id": scrape_run_id,
                "scraped_at": r.scraped_at,
                "run_id": run_id,
            }
            for r in rows
        ],
    )
    return len(rows)


async def main(path: Path, *, today: date, max_quarantine_share: float, dry_run: bool) -> dict[str, object]:
    totals, monthly = read_export(path)
    sha = sha256_rows(
        [(t.mp_route_id, t.total_ticks, t.complete, t.scraped_at.isoformat()) for t in totals]
        + [(m.mp_route_id, m.year_month, m.style, m.n) for m in monthly]
    )
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            if await find_completed(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=sha):
                return {"status": "noop", "rows_upserted": 0}
            route_types = {
                int(r): (str(t) if t is not None else None)
                for r, t in (await conn.execute(text("SELECT mp_route_id, type FROM mp_routes"))).all()
            }
            existing = await existing_counts(conn, {t.mp_route_id for t in totals})
            rows, report, stats = validate(totals, monthly, route_types=route_types, existing=existing, today=today)
            problems = batch_gate(report, previous_rows_in=None, count_tolerance=1.0, max_quarantine_share=max_quarantine_share)
            if dry_run:
                return {"status": "dry_run", "report": report.summary(), "stats": stats, "problems": problems}
            run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=sha)
            await write_quarantine(conn, run_id, report)
            if problems:
                await finish_run(conn, run_id, status="rejected", report=report, rows_upserted=0, problems=problems)
                return {"status": "rejected", "rows_upserted": 0, "problems": problems, "report": report.summary()}
            inserted = await load(conn, rows, run_id=run_id, scrape_run_id=f"sqlite:{sha[:12]}")
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=inserted)
            return {"status": "ok", "rows_upserted": inserted, "report": report.summary(), "stats": stats}
    finally:
        await engine.dispose()


def cli() -> None:
    from app.services.temporal_weighting import utc_today

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--max-quarantine-share", type=float, default=0.10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        main(args.sqlite, today=utc_today(), max_quarantine_share=args.max_quarantine_share, dry_run=args.dry_run)
    )
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    cli()
```

A rejected run's quarantine rows are written in the same transaction as its log row, so a rejection still leaves its evidence. `ingest` needs only SELECT and INSERT on this table: accepted months are closed, so nothing is ever updated.

In `test_heavy_quarantine_rejects_the_batch_and_writes_nothing`:
- `2026-10` is `future_month`.
- `2025-01` and the total are accepted.
- That makes one quarantined row in three, over the 0.1 gate, so the batch is rejected.

`test_changed_counts_are_quarantined_never_overwritten`:
- The second export changes `2025-01` (3 → 4) and the total (3 → 5), so both are `count_changed`.
- Only the new `2025-03` row is inserted.
- The quarantine share is 2 of 3, under that test's 0.9 gate.

Append `"app.pipelines.mp_tick_aggregates"` to the first strict mypy block.

- [ ] **Step 4: Run**

Run: `cd backend && uv run pytest tests/test_mp_tick_aggregates.py -q && uv run mypy && uv run ruff check . ../scripts/ && python ../scripts/check_no_scrapers.py`
Expected: PASS; the guard prints no violations.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipelines/mp_tick_aggregates.py backend/tests/test_mp_tick_aggregates.py backend/pyproject.toml
git commit -m "feat(pipelines): load private MP ice/mixed tick aggregates, partial months held, INSERT-only"
```

---

### Task 7: Docs, CHANGELOG, final verification

**Files:**
- Modify: `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`, `CHANGELOG.md`

- [ ] **Step 1: Update docs**

`CLAUDE.md`, "Database and migrations": replace "`ingest`/`trainer`/`triage_worker` land later." with:

```
`ingest` (Phase 2 data jobs: writes only the tables listed in `backend/db/roles/verify_roles_phase2.sql`; its URL must verify TLS — `app.pipelines.db` refuses anything weaker than verify-full on a remote host) and `trainer` (NOLOGIN, no grants until Phase 3) are created by `create_roles_phase2.sql`, which also creates schema `internal` (owned by `migrator`; `app` has no access). After every Phase 2 migration the owner runs `grants_phase2.sql` then `verify_roles_phase2.sql`. `triage_worker` lands in Phase 4.
```

Add to "Commands": `` - `uv run python -m app.pipelines.<job>`: Phase 2 data jobs; connect as `ingest` via `INGEST_DATABASE_URL` and refuse without it. `VERIFY_DATABASE_URL=<analyst url> uv run pytest -m db tests/verify`: read-only acceptance checks. ``

`DEPLOYMENT.md`, "Database, roles, and migrations": add a bullet: "Phase 2 roles and schema: `create_roles_phase2.sql` (owner, once), migrations as `migrator`, then `grants_phase2.sql` and `verify_roles_phase2.sql` after each Phase 2 migration. Runbook: `docs/superpowers/plans/2026-09-28-phase2a-foundations.md` Tasks 8–10. Owner/analyst `psql` URLs always use `sslmode=verify-full&sslrootcert=system`." Revisions list gains `0004_phase2a_foundation`.

`data/DATABASE_STRUCTURE.md`: add sections for `source_ingest_log` (public), and a short "Schema `internal`" section listing `accidents_raw`, `accident_revisions`, `ingest_quarantine`, `mp_tick_aggregates` with one-line purposes; add `quarantine_reason`/`quarantine_rule_version` to a new `mp_ticks` subsection; note `mp_locations` has no `elevation_ft` column (the table above is stale) — correct that row.

`CHANGELOG.md` under `## [Unreleased]`:

```
### Phase 2a foundations (PR 2a-0) — 2026-09-28

- New `app/pipelines/` package: boundary validation with row quarantine and batch gates (`validate.py`), the packed 0.1° grid key (`grid.py`), the ingest run log (`ingest_log.py`), and an `ingest`-role engine (`INGEST_DATABASE_URL`) that refuses weaker-than-verify-full TLS on a remote host.
- Migration `0004_phase2a_foundation`: immutable SQL function `grid_bucket_key()` (the one SQL form of the grid key); schema `internal`; frozen `internal.accidents_raw`; Phase 2a accident columns with enum checks; `internal.accident_revisions`; `source_ingest_log`; `internal.ingest_quarantine`; `internal.mp_tick_aggregates`; `mp_ticks.quarantine_reason`. Downgrade refuses while revisions, aggregates or run logs exist.
- Role `ingest` (owner-run SQL; plaintext password via `\getenv` over verify-full, rerun guard, verifier/short-value refusal, as main's `create_roles.sql`) and a NOLOGIN, grant-less `trainer` placeholder; cumulative `grants_phase2.sql`, exact-privilege `verify_roles_phase2.sql`.
- R8: `mp_ticks` rows flagged `future` (after the day after they were captured, or after today, whichever is earlier), `orphan_route`, or `pre_1970`; nothing deleted.
- Loader for the privately produced MP ice/mixed tick aggregates: reads a local SQLite export, accepts ice/mixed routes only, holds back the scrape month and later as `partial_month`, stores MP's reported total, never overwrites a stored count (`count_changed` is quarantined), and is a logged no-op on a repeat load.
```

- [ ] **Step 2: Full verification**

Run: `cd backend && uv run pytest -q && uv run ruff check . ../scripts/ && uv run mypy && cd .. && python scripts/check_no_scrapers.py`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md DEPLOYMENT.md data/DATABASE_STRUCTURE.md CHANGELOG.md
git commit -m "docs: Phase 2a foundations — roles, internal schema, pipeline commands"
```

---

### Task 8: OWNER/AGENT RUNBOOK — backups, restore rehearsal

Runs from `/Users/sebastianfrazier/Developer/SafeAscent/backend` on `main` after this plan's PR merges. The agent may run it only when the owner has placed the needed env files; the agent never opens them. No command prints a password.

**Pre-merge gate (read before merging this plan's PR; final re-review N1).** The merged `Accident` model needs `0004`, which is applied only in Task 9 Step 4, so a deploy of the merge before then breaks `/predict`. Before clicking merge:
- confirm auto-deploy is **off** on every Railway service (`api`, `worker`, `beat`, `frontend`); if it is on, pause it on every service first;
- after merging, do not start a manual deploy;
- turn auto-deploy back on, or deploy, only after Task 9 Step 4 has succeeded (Task 9's deploy gate).

Put this gate in the PR description too.

**Shell state (Tasks 8–10, final review M10).** The blocks below rely on shell functions and variables that live only in the shell that defined them: `split_pg_url`, `pg_verify_full`, `verify_full_url`, `branch_host` (Step 1), `PGBIN` (Step 1), and each step's `B`/`BRANCH_HOST`. Run Tasks 8–10 in one terminal session. In a new shell, first re-run Step 1's definitions and re-set `PGBIN` and the current step's `B`/`BRANCH_HOST`. Each branch-side subshell starts with an explicit check, `[ -n "${BRANCH_HOST-}" ] || { …; exit 1; }` (plus `type split_pg_url verify_full_url >/dev/null || exit 1` where the helpers are used), before any `$(…)`. A `${VAR:?}` inside `$(…)` only kills the command substitution, not the block, in both bash and zsh (final re-review N2). With `BRANCH_HOST` unset, the block stops at that first line. Production blocks are written out separately and never depend on `BRANCH_HOST`.

**Files (gitignored or outside the repo, never committed):** `backend/.env.owner`, `backend/.env.analyst`, `~/Developer/safeascent-private/backups/pre-2a/*.dump`.

- [ ] **Step 1 (owner/agent): Tools, helpers, and the prod server's major version**

Use Phase 1 Plan B Task 8 Step 1 verbatim (`brew install libpq neonctl`, `psql` 16+ from `libpq`, and the `split_pg_url` and `pg_verify_full` shell functions).

`split_pg_url` keeps whatever TLS query the URL carries, and Neon's Console URLs say `sslmode=require` (encrypts without checking the certificate). Define this helper in the same shell and call it right after every `split_pg_url` in Phase 2 runbooks. It rewrites `PG_URL_NOPASS` to plain `postgresql://` with `sslmode=verify-full&sslrootcert=system` (the OS trust store), dropping any `ssl`/`sslmode`/`sslrootcert` it had:

```bash
verify_full_url() {
  PG_URL_NOPASS="$(uv run python3 -c '
import sys
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
u = urlsplit(sys.argv[1])
query = [(k, v) for k, v in parse_qsl(u.query) if k not in ("ssl", "sslmode", "sslrootcert")]
query += [("sslmode", "verify-full"), ("sslrootcert", "system")]
print(urlunsplit(("postgresql", u.netloc, u.path, urlencode(query, quote_via=quote), u.fragment)))
' "$PG_URL_NOPASS")"
}

# Direct host of Neon branch $1. Prints only the host name, never the URL's password.
branch_host() {
  neonctl connection-string "${1:?branch name}" --project-id still-morning-74008008 \
      --role-name neondb_owner --database-name neondb \
    | python3 -c '
import sys
from urllib.parse import urlsplit
host = urlsplit(sys.stdin.read().strip()).hostname or ""
# No pipefail here: a failed neonctl must print nothing, never "None".
if not host.startswith("ep-"):
    sys.exit("branch_host: no Neon endpoint host returned")
print(host)'
}
```

`quote_via=quote` matters: `urlencode`'s default `quote_plus` turns the space in the analyst URL's `options=-c default_transaction_read_only=on` into `+`, which libpq passes through literally, so the connection fails with `unrecognized configuration parameter "+default_transaction_read_only"` (hit in Task 10 Step 1, 2026-09-29).

`${1:?}` matters: with an empty branch name `neonctl connection-string` silently returns the default branch's, i.e. **production's**, URL (Phase 1 Plan B Task 8 Step 3).

Check TLS once: `split_pg_url "$OWNER_DATABASE_URL"; verify_full_url; psql "$PG_URL_NOPASS" -XAt -c "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"` (inside the usual `set -a; . ./.env.owner` subshell). On Neon `pg_stat_ssl.ssl` can read `f` even over TLS (the proxy terminates it); `\conninfo` showing `SSL Connection | true` is the proof. A certificate error here means the OS trust store is missing the issuer; stop and fix that. Never fall back to `require`.

**Record the prod server's major version, and use dump/restore tools of that same major** (final review C1). This machine's `libpq` is 18.6, and `pg_restore` 18 always sends `SET transaction_timeout = 0`, which a PG16 server rejects (`unrecognized configuration parameter "transaction_timeout"`), so `--exit-on-error` aborts before any data. A PG16 `pg_restore` in turn cannot read a dump written by `pg_dump` 18 (`unsupported version (1.16) in file header`). So dumps and restores both use the server's major:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.owner; set +a
  type split_pg_url verify_full_url >/dev/null || exit 1
  split_pg_url "$OWNER_DATABASE_URL"; verify_full_url
  psql "$PG_URL_NOPASS" -XAt -c 'SHOW server_version_num' )
```

Record the number in the PR (e.g. `160009` means major 16). Then, with `PGMAJOR` set to that number divided by 10000:

```bash
PGMAJOR=16   # from server_version_num above
brew install "postgresql@${PGMAJOR:?}"   # keg-only: it does not replace libpq's psql on PATH
PGBIN="/opt/homebrew/opt/postgresql@${PGMAJOR:?}/bin"
for x in pg_dump pg_restore psql; do "${PGBIN:?}/$x" --version; done
```

Expected: all three print `(PostgreSQL) <PGMAJOR>.x`. If the server's major is 18, libpq's own binaries already match and `PGBIN=/opt/homebrew/opt/libpq/bin`. If it is below 16, stop: `sslrootcert=system` needs a 16+ client. The other `psql` calls in Tasks 8–10 (plain queries, role scripts) keep using libpq's `psql`; only Steps 2 and 3 use `$PGBIN`.

- [ ] **Step 2 (owner/agent): Dump the three tables the repairs touch**

The dumps must come from `$PGBIN`'s `pg_dump`, or Step 3's same-major `pg_restore` cannot read them.

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
mkdir -p ~/Developer/safeascent-private/backups/pre-2a && chmod 700 ~/Developer/safeascent-private/backups/pre-2a
( set -a; . ./.env.owner; set +a
  type split_pg_url verify_full_url >/dev/null || exit 1
  split_pg_url "$OWNER_DATABASE_URL"; verify_full_url
  for t in accidents weather mp_ticks; do
    "${PGBIN:?}/pg_dump" "$PG_URL_NOPASS" -Fc --no-owner --no-privileges -t "public.$t" \
      -f ~/Developer/safeascent-private/backups/pre-2a/$t.dump || exit 1
  done )
ls -l ~/Developer/safeascent-private/backups/pre-2a
```

Expected: three non-empty `.dump` files. They contain accident narratives and climber names: they stay in the private directory (no remote), never in the repo. They are an offline copy as of today; the Neon `pre-2a` branch, taken immediately before the prod apply (Task 9 Step 4), is the rollback point.

- [ ] **Step 3 (owner/agent): Restore rehearsal** (spec 2a-0 acceptance)

The dumps are restored unmodified into a scratch database, `restore_drill`, on a throwaway branch made from `production`.

Why not rewrite the schema name on the fly: a `sed s/public\./drill./` rewrite would also rewrite type references such as `public.geography`, which breaks the restore. It would silently alter any narrative text containing "public.". A separate database needs no rewriting at all.

Only the **pre-data and data sections** are restored (table definitions, sequences, and rows). Post-data (indexes, primary keys, the FKs from `accidents` to `mountains`/`routes`/`mp_routes`, and the `update_coordinates()` triggers) is deliberately **not rehearsed**: a `-t` dump does not carry the trigger function or the FK targets, so post-data fails by construction (`function public.update_coordinates() does not exist`, reproduced 2026-09-29). The `pre-2a` branch is the full fallback; these dumps prove the rows themselves are recoverable.

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
B=p2a-restore-drill
neonctl branches create --project-id still-morning-74008008 --name "${B:?}" --parent production --output json \
  | python3 -c 'import json,sys; b=json.load(sys.stdin)["branch"]; print(b["id"], b["name"], b["parent_id"])'
BRANCH_HOST="$(branch_host "$B")"
( set -a; . ./.env.owner; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set: re-run the step that sets it' >&2; exit 1; }
  type split_pg_url verify_full_url >/dev/null || exit 1
  case "$OWNER_DATABASE_URL" in *"@${BRANCH_HOST}/"*) echo 'BRANCH_HOST is production: stop' >&2; exit 1 ;; esac
  URL="$(printf '%s' "$OWNER_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  split_pg_url "$URL"; verify_full_url
  SRC="$PG_URL_NOPASS"
  DRILL="$(printf '%s' "$SRC" | sed -E 's#^(postgresql://[^/]+)/[^?]*#\1/restore_drill#')"
  "${PGBIN:?}/psql" "$SRC" -X -q -v ON_ERROR_STOP=1 -c "CREATE DATABASE restore_drill" || exit 1
  "$PGBIN/psql" "$DRILL" -X -q -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS postgis" || exit 1
  for t in accidents weather mp_ticks; do
    "$PGBIN/pg_restore" --no-owner --no-privileges --exit-on-error --section=pre-data --section=data \
      -d "$DRILL" ~/Developer/safeascent-private/backups/pre-2a/$t.dump || { echo "$t: restore FAILED"; exit 1; }
  done
  for t in accidents weather mp_ticks; do
    a="$("$PGBIN/psql" "$SRC" -XAt -c "SELECT count(*) FROM public.$t")"
    b="$("$PGBIN/psql" "$DRILL" -XAt -c "SELECT count(*) FROM public.$t")"
    [ "$a" = "$b" ] && echo "$t restored: match" || echo "$t restored: MISMATCH"
  done
  "$PGBIN/psql" "$SRC" -X -q -c "DROP DATABASE restore_drill" )
neonctl branches delete "${B:?}" --project-id still-morning-74008008
```

Expected: the branch id with parent `br-restless-bar-ajw5zy4b` (production), then `accidents restored: match`, `weather restored: match`, `mp_ticks restored: match`. Only match/mismatch is printed, never counts of personal data. The branch is created after the dumps, so a production write in between can make a count differ by the rows written since; rerun Step 2 and this step back to back if that happens.

Record "restore rehearsed 2026-MM-DD (pre-data + data; post-data not rehearsed), server major NN, tools postgresql@NN" in the PR thread.

Cost (final re-review N9, owner's call): this drill writes ~23M `mp_ticks` rows into branch storage and WAL. A local `postgis/postgis:<PGMAJOR>` container proves the same thing, that the dumps are readable and complete, at no Neon cost: restore into it with the same `$PGBIN` tools, and compare against the counts from `$SRC`. It was not chosen by default because the Neon branch also rehearses the real server's extensions and settings.

Rehearsed locally 2026-09-29 against `postgis/postgis:16-3.4-alpine` with a representative 0003-state database: the old command (no `--section`) failed on `CREATE TRIGGER … update_coordinates()`; libpq 18.6's `pg_restore` failed on `SET transaction_timeout`; PG16 `pg_dump`/`pg_restore` with the section flags restored all three tables with matching counts.

---

### Task 9: OWNER/AGENT RUNBOOK — create Phase 2 roles, rehearse `0004` on a Neon branch, apply to prod

**Deploy gate: migrate before deploy (owner decision 2026-09-29, final review C3).** The merged `Accident` model maps the 21 columns `0004` adds, and `select(Accident)` in `/predict` selects all of them, so any build of this merge raises `UndefinedColumnError` on prod until Step 4 has applied `0004`. Until Step 4 has succeeded:
- do not deploy `main` to any Railway service (auto-deploy is off until the relaunch; do not start a manual deploy);
- if auto-deploy has been turned on by then, pause it on every service before merging, and turn it back on only after Step 4.

The old code ignores the extra columns, so migrating first is always safe. `DEPLOYMENT.md` states the same rule.

**Files (gitignored, never committed):** `backend/.env.ingest`, plus the Phase 1 `backend/.env.owner`, `backend/.env.migrator`, `backend/.env.analyst`. `trainer` has no credential until Phase 3 (D13).

- [ ] **Step 1 (owner): Generate the ingest password straight into a file and write its role URL**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.owner; set +a; uv run python -m scripts.write_role_url --role ingest --env-file .env.ingest --generate-password )
git check-ignore -v .env.ingest
```

Expected: `wrote INGEST_PASSWORD and INGEST_DATABASE_URL to .env.ingest` (0600, password never printed), and the file matched by `.env.*`. A rerun refuses while the file already holds `INGEST_PASSWORD`; delete the file only if the role has not been created yet.

- [ ] **Step 2 (owner/agent): Rehearse on a Neon branch made from `production`**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
B=p2a-0-rehearsal
neonctl branches create --project-id still-morning-74008008 --name "${B:?}" --parent production --output json \
  | python3 -c 'import json,sys; b=json.load(sys.stdin)["branch"]; print(b["id"], b["name"], b["parent_id"])'
BRANCH_HOST="$(branch_host "$B")"
( set -a; . ./.env.owner; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set: re-run the step that sets it' >&2; exit 1; }
  case "$OWNER_DATABASE_URL" in *"@${BRANCH_HOST}/"*) echo 'BRANCH_HOST is production: stop' >&2; exit 1 ;; esac
  echo "branch host ok" )
```

Expected: parent `br-restless-bar-ajw5zy4b` (production), then `branch host ok`. Keep this shell: Step 3 and Task 10 reuse `BRANCH_HOST`.

**Neon role memberships are per branch**, not per project: a `GRANT migrator TO CURRENT_USER WITH SET TRUE, INHERIT FALSE` run on `p2a-0-rehearsal` has no effect on `production`. If Step 2's rehearsal needed that re-grant (i.e. the relaunch's `REVOKE` has already run), Step 4's prod apply needs the same `GRANT … WITH SET TRUE, INHERIT FALSE` run against prod first — check membership on each branch independently, don't assume the rehearsal result carries over.

**Owner membership in `migrator` (ordering vs the Phase 1 relaunch).** `create_roles_phase2.sql` needs the owner to hold SET on `migrator` (`CREATE SCHEMA internal AUTHORIZATION migrator`). Phase 1's `create_roles.sql` granted it `WITH SET TRUE, INHERIT TRUE`, and the Phase 1 relaunch gate (plan 2026-09-27-phase1b Task 26 Step 3, `REVOKE migrator FROM CURRENT_USER`) removes it:
- **Before that REVOKE has run** (current state): run the steps below as written.
- **After it has run:** `create_roles_phase2.sql` refuses up front, before any password is sent (`owner needs SET on migrator: …`). As the owner, run `GRANT migrator TO CURRENT_USER WITH SET TRUE, INHERIT FALSE` first. SET without INHERIT lets the owner act as `migrator` explicitly but does not give it `migrator`'s table access back, and `verify_roles.sql` still passes. Optionally `REVOKE migrator FROM CURRENT_USER` again once Step 2 is done.
- `grants_phase2.sql` can instead be run as `migrator` directly (`MIGRATOR_DATABASE_URL`, through `split_pg_url; verify_full_url`), which needs no owner membership. Every later plan's grants step can use this route. As the owner without SET it refuses with `run as migrator, or as an owner with SET on migrator`.

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.owner; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set: re-run the step that sets it' >&2; exit 1; }
  type split_pg_url verify_full_url >/dev/null || exit 1
  BRANCH_URL="$(printf '%s' "$OWNER_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  split_pg_url "$BRANCH_URL"; verify_full_url
  set -a; . ./.env.ingest; set +a
  psql "$PG_URL_NOPASS" -X -q -f db/roles/create_roles_phase2.sql ) &&
( set -a; . ./.env.migrator; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set: re-run the step that sets it' >&2; exit 1; }
  export MIGRATOR_DATABASE_URL="$(printf '%s' "$MIGRATOR_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  uv run alembic upgrade head && uv run alembic current && uv run alembic check ) &&
( set -a; . ./.env.owner; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set: re-run the step that sets it' >&2; exit 1; }
  type split_pg_url verify_full_url >/dev/null || exit 1
  BRANCH_URL="$(printf '%s' "$OWNER_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  split_pg_url "$BRANCH_URL"; verify_full_url
  psql "$PG_URL_NOPASS" -X -q -f db/roles/grants_phase2.sql
  psql "$PG_URL_NOPASS" -X -q -f db/roles/verify_roles.sql
  psql "$PG_URL_NOPASS" -X -q -f db/roles/verify_roles_phase2.sql )
```

Expected: `roles ingest and trainer created; schema internal owned by migrator`; `0004_phase2a_foundation (head)`; `No new upgrade operations detected.`; `phase 2 grants applied`; `ALL ROLE CHECKS PASSED`; `ALL PHASE 2 ROLE CHECKS PASSED`. Any failure stops the rollout; paste only the failing check names to the agent.

- [ ] **Step 3 (owner/agent): Acceptance on the branch as `analyst`**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.analyst; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set: re-run the step that sets it' >&2; exit 1; }
  VERIFY_DATABASE_URL="$(printf '%s' "$ANALYST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")" \
    uv run pytest -m db tests/verify/test_phase2a_foundation.py -q -k accidents_raw )
```

Only the host is swapped. `tests/verify/_db.py` strips the driver prefix and query and connects with `connect_args_for`, i.e. verify-full, so no `ssl=`→`sslmode=` rewrite is needed.

Expected: `1 passed`. (The R8 checks run after Task 10.) Keep the branch for Task 10's rehearsal.

- [ ] **Step 4 (owner): Snapshot `pre-2a`, then apply to prod**

Pick a time well clear of the 02:00 UTC nightly (it writes `historical_predictions`). If Task 8's dumps are not from today, re-run Task 8 Step 2 first (it needs `PGBIN` from Task 8 Step 1; the `mp_ticks` dump takes minutes), so that the snapshot below stays immediately before the apply (final re-review N4).

Immediately before the apply, snapshot production (final review I3) and note the UTC time:

```bash
neonctl branches create --project-id still-morning-74008008 --name pre-2a --parent production --output json \
  | python3 -c 'import json,sys; b=json.load(sys.stdin)["branch"]; print(b["id"], b["name"], b["parent_id"], b["created_at"])'
```

Expected: parent `br-restless-bar-ajw5zy4b` (production). Record the `created_at` in the PR. `pre-2a` is the point-in-time fallback; do not delete it until plan 3 lands.

Then apply to **production**. These are Step 2's and Step 3's blocks with no host substitution (final re-review N3):

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
# PRODUCTION
( set -a; . ./.env.owner; set +a
  type split_pg_url verify_full_url >/dev/null || exit 1
  split_pg_url "$OWNER_DATABASE_URL"; verify_full_url
  set -a; . ./.env.ingest; set +a
  psql "$PG_URL_NOPASS" -X -q -f db/roles/create_roles_phase2.sql ) &&
( set -a; . ./.env.migrator; set +a
  uv run alembic upgrade head && uv run alembic current && uv run alembic check ) &&
( set -a; . ./.env.owner; set +a
  type split_pg_url verify_full_url >/dev/null || exit 1
  split_pg_url "$OWNER_DATABASE_URL"; verify_full_url
  psql "$PG_URL_NOPASS" -X -q -f db/roles/grants_phase2.sql
  psql "$PG_URL_NOPASS" -X -q -f db/roles/verify_roles.sql
  psql "$PG_URL_NOPASS" -X -q -f db/roles/verify_roles_phase2.sql )
# PRODUCTION acceptance as analyst
( set -a; . ./.env.analyst; set +a
  VERIFY_DATABASE_URL="$ANALYST_DATABASE_URL" \
    uv run pytest -m db tests/verify/test_phase2a_foundation.py -q -k accidents_raw )
```

Expected outputs are identical to Steps 2 and 3. The membership note under Step 2 applies to production separately.

If `alembic upgrade` fails with a lock timeout (`canceling statement due to lock timeout`) or a deadlock (`0004` locks `accidents` then `mp_ticks`; the ascent-analytics endpoint reads them in the other order), nothing was applied: `0004` runs in one transaction. Rerun the migrator block.

After Step 3 passes on prod, the deploy gate above is lifted.

- [ ] **Step 5 (owner): Validate the `NOT VALID` constraint** — 0004 added `mp_ticks_quarantine_reason_check` as `NOT VALID` (I2) so the migration itself never scans the full table; validate it now, as `migrator`. `VALIDATE` scans all ~23M rows under SHARE UPDATE EXCLUSIVE, which blocks neither reads nor writes; `lock_timeout` only stops it from queueing behind DDL or a manual `VACUUM`.

First on the rehearsal branch, to learn how long the scan takes:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.migrator; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set: re-run the step that sets it' >&2; exit 1; }
  type split_pg_url verify_full_url >/dev/null || exit 1
  split_pg_url "$(printf '%s' "$MIGRATOR_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"; verify_full_url
  time psql "$PG_URL_NOPASS" -X -c "SET lock_timeout = '5s'; ALTER TABLE mp_ticks VALIDATE CONSTRAINT mp_ticks_quarantine_reason_check;" )
```

Then on **production**:

```bash
# PRODUCTION
( set -a; . ./.env.migrator; set +a
  type split_pg_url verify_full_url >/dev/null || exit 1
  split_pg_url "$MIGRATOR_DATABASE_URL"; verify_full_url
  psql "$PG_URL_NOPASS" -X -c "SET lock_timeout = '5s'; ALTER TABLE mp_ticks VALIDATE CONSTRAINT mp_ticks_quarantine_reason_check;" )
```

Expected: `SET` then `ALTER TABLE` (no error; the constraint was already true for every existing row, since `quarantine_reason` is NULL until Task 10's R8 run). A failure here means some row already violates the check — stop and diagnose before Task 10 writes any quarantine values.

- [ ] **Rollback (final review I3).** What to do when something fails partway:
  - `alembic upgrade` fails (lock timeout, deadlock, any error): nothing was applied (one transaction). Fix the cause and rerun.
  - `0004` applied but `grants_phase2.sql` or a verify script fails: fix and rerun them; both are idempotent. Nothing needs reverting.
  - `0004` must be reverted **before Task 10 runs**: as `migrator`, `uv run alembic downgrade 0003_hist_insufficient_data`. It refuses once any new table holds rows or any Phase 2a column holds a non-default value, which is the case after Task 10. If this merge has been deployed since Step 4, first redeploy the pre-merge build on every service (the merged model needs `0004`, so downgrading under it breaks `/predict`), and put the deploy gate back in force (final re-review N6).
  - After Task 10 has written, or if the data itself is wrong: restore `production` from `pre-2a` with Neon's branch restore, or, more precisely, restore `production` to a timestamp just before Step 4 (point-in-time, within the project's history retention). The exact Console/`neonctl` path was not checked while writing this; confirm it in Neon's docs before relying on it.
  - **`historical_predictions` caveat:** a branch or point-in-time restore of `production` discards every write made after that point, including the nightly's `historical_predictions` rows (and any Phase 1 role or grant change). After such a restore, trigger the nightly once (`DEPLOYMENT.md`, Insufficient-data rollout step 3) to rebuild today and the next 2 days; the rows for days in between are lost, not recomputed. Also check that the deploy gate still holds: the restored database has no `0004`.

- [ ] **Step 6 (owner): Store the ingest credential for data workflows** — GitHub → Settings → Secrets and variables → Actions → New repository secret `INGEST_DATABASE_URL`, pasted from `.env.ingest` via an editor (never `cat` in a shared terminal). The URL must not carry `sslmode=require` (the job refuses it). Do not add it to Railway yet: plan 7 puts it on a dedicated ingest service, never on the general `worker` (D14).

---

### Task 10: OWNER/AGENT RUNBOOK — run R8 and load the private tick aggregates

- [ ] **Step 1 (owner/agent): Record the provenance of `mp_ticks.created_at`** (D3)

`SHOW TimeZone` in this session proves nothing about the session that wrote the rows. The rule already carries one day of slack, so this step records evidence rather than gating the run. Check three things:

```bash
( set -a; . ./.env.analyst; set +a
  type split_pg_url verify_full_url >/dev/null || exit 1
  split_pg_url "$ANALYST_DATABASE_URL"; verify_full_url
  psql "$PG_URL_NOPASS" -XAt \
    -c "SELECT coalesce(r.rolname, '(all roles)') || ' / ' || coalesce(d.datname, '(all dbs)') || ': ' || array_to_string(s.setconfig, ',')
          FROM pg_db_role_setting s LEFT JOIN pg_roles r ON r.oid = s.setrole LEFT JOIN pg_database d ON d.oid = s.setdatabase
         WHERE array_to_string(s.setconfig, ',') ILIKE '%timezone%'" \
    -c "SELECT setting FROM pg_settings WHERE name = 'TimeZone'" \
    -c "SELECT date_trunc('hour', max(created_at)) FROM mp_ticks" )
```

Then check the code that **loaded `public.mp_ticks`** (final review I2). That is not the private ice/mixed tick scraper under `~/Developer/safeascent-private/mp_ticks`, which writes a local SQLite export and never touched `public.mp_ticks`. The owner supplies the path. If nobody knows which code loaded the table, or it no longer exists, skip the grep and record `loader: unknown`; do not grep some other code in its place.

```bash
MP_TICKS_LOADER='<owner: path to the code that loaded public.mp_ticks>'
if [ -e "${MP_TICKS_LOADER:?}" ]; then
  grep -rniE --exclude-dir=.venv --exclude-dir=node_modules \
    "SET +TIME *ZONE|SET +timezone|PGTZ|['\"]timezone['\"]|timezone *=" "$MP_TICKS_LOADER" \
    || echo 'loader sets no time zone'
else
  echo 'loader: unknown'
fi
```

The pattern matches the ways a loader can set its session zone (`SET TIME ZONE`, `SET timezone`, `PGTZ`, a `timezone` connection option or `server_settings` key, `options='-c timezone=…'`), and not `datetime.timezone.utc` or `from datetime import timezone`.

Expected:
- No per-role or per-database `TimeZone` override (empty first query), and a server default of `GMT`/`UTC`.
- The loader sets no time zone, or `loader: unknown`.
- The latest `created_at` hour matches the UTC time the private load log says the load ran.

Record the three facts (or `loader: unknown`) in the PR. If any of them disagrees, the one-day slack in `QUARANTINE_SQL` still covers zones down to UTC−14; tell the agent only if the load evidently ran **ahead** of UTC, which would need a different rule.

- [ ] **Step 2 (owner/agent): Run R8 on the rehearsal branch, then prod**

On the rehearsal branch:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.ingest; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set: re-run the step that sets it' >&2; exit 1; }
  export INGEST_DATABASE_URL="$(printf '%s' "$INGEST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.mp_ticks_quarantine )
( set -a; . ./.env.analyst; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set: re-run the step that sets it' >&2; exit 1; }
  VERIFY_DATABASE_URL="$(printf '%s' "$ANALYST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")" \
    uv run pytest -m db tests/verify/test_phase2a_foundation.py -q )
```

Expected: one JSON line, e.g. `{"counts": {"clean": …, "future": …, "orphan_route": …, "pre_1970": …}, "rule_version": "r8-v1"}`. Only flagged rows are written (owner decision 2026-09-29, final review I1): clean rows keep `quarantine_reason` and `quarantine_rule_version` NULL, and the run's `source_ingest_log.validation_report` records `"rule_version": "r8-v1"`. The first run updates only the ~5K flagged rows, not the whole table.
- Compare with the audit: 1,322 future at the 2026-02-08 cutoff, and 3,602 orphan route ids. The one-day capture slack may lower `future` slightly below 1,322. `future` must still be ≥ 57, the rows dated after 2026-09-28.
- Record the counts in the PR.
- Re-run: the counts are identical.
- The branch acceptance run prints `4 passed` (final re-review N8: a verify-side problem shows up here, before production).
- Then on **production**:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
# PRODUCTION
( set -a; . ./.env.ingest; set +a
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.mp_ticks_quarantine )
( set -a; . ./.env.analyst; set +a
  VERIFY_DATABASE_URL="$ANALYST_DATABASE_URL" uv run pytest -m db tests/verify/test_phase2a_foundation.py -q )
```

  Expected: the same JSON shape, then `4 passed`.

- [ ] **Step 3 (owner): Load the tick aggregates, only after the private scrape reports complete**

In `~/Developer/safeascent-private/mp_ticks`: `./status.sh` must show all routes done; stop the scraper (`pkill -TERM -f mp_ticks.run`) so the WAL checkpoints. The loader opens its export `mode=ro`, which can still need to (re)create the export's `-wal` file even for a read (e.g. right after a checkpoint); that fails loudly if this process can't write to the private repo's directory (I1, Task 6 review). Take a consistent snapshot into a writable temp directory first and load from that, not the live file, so the load never depends on that directory's permissions:

```bash
SNAPSHOT="$(mktemp -d)/mp_ice_ticks.snapshot.sqlite"
sqlite3 ~/Developer/safeascent-private/mp_ticks/mp_ice_ticks.sqlite ".backup '$SNAPSHOT'"

cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.ingest; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set: re-run the step that sets it' >&2; exit 1; }
  [ -s "${SNAPSHOT-}" ] || { echo 'SNAPSHOT missing' >&2; exit 1; }
  export INGEST_DATABASE_URL="$(printf '%s' "$INGEST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.mp_tick_aggregates --sqlite "$SNAPSHOT" --dry-run )
```

Expected: `"status": "dry_run"` with a report of counts only. If `problems` is non-empty, stop and review the quarantine reasons with the agent (counts only). Otherwise load on the branch, twice:

```bash
( set -a; . ./.env.ingest; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set: re-run the step that sets it' >&2; exit 1; }
  export INGEST_DATABASE_URL="$(printf '%s' "$INGEST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.mp_tick_aggregates --sqlite "$SNAPSHOT" &&
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.mp_tick_aggregates --sqlite "$SNAPSHOT" )
```

Expected: `"status": "ok"`, then `"status": "noop"`.

Then on **production**, against the same `$SNAPSHOT`:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
# PRODUCTION
( set -a; . ./.env.ingest; set +a
  [ -s "${SNAPSHOT-}" ] || { echo 'SNAPSHOT missing' >&2; exit 1; }
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.mp_tick_aggregates --sqlite "$SNAPSHOT" --dry-run )
# Only if the dry run's problems list is empty:
( set -a; . ./.env.ingest; set +a
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.mp_tick_aggregates --sqlite "$SNAPSHOT" &&
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.mp_tick_aggregates --sqlite "$SNAPSHOT" )
```

Expected: `dry_run`, then `ok`, then `noop`. Then clean up (final re-review N7):

```bash
neonctl branches delete p2a-0-rehearsal --project-id still-morning-74008008
[ -n "${SNAPSHOT-}" ] && rm -rf "$(dirname "$SNAPSHOT")"
```

- [ ] **Step 4 (owner): Later scrapes**
- Reloading the same export never accepts its `partial_month` rows (D3). Months at or after the scrape month land only from a **newer** private scrape that finished after those months closed.
- After each new scrape, run Step 3 on it. **Known limitation (final review M9, owner decision pending):** MP's totals only grow and `period='total'` is INSERT-only, so a newer scrape turns almost every stored total into `count_changed`. At ~7–12 rows per route that is roughly 8–12% of the batch, above the default `--max-quarantine-share 0.10`, so the reload is likely **rejected** whole and its newly closed months do not land. The first load is unaffected. Decide before the second load: exclude `total` rows from the share, or key totals per scrape. Do not just raise `--max-quarantine-share`, which would also hide real drift.
- `count_changed` rows (late-logged ticks, or MP's total grew) are quarantined, never applied. Review their count in the run's report. If the drift matters for exposure, rebuilding the table is a new migration plus a full reload, decided by the owner.
- Removal (legal Q1): `DROP TABLE internal.mp_tick_aggregates` via a new migration, then plan 8's removal drill.

---

## Self-review (done while writing; redone 2026-09-28 after the plan review)

- **Spec coverage for this plan's scope:**
  - 2a-0: backup, `accidents_raw`, revisions table, new columns; restore rehearsed into a scratch database (Tasks 2 and 8).
  - R8 (Task 5): the frozen date is replaced per the owner rule, with one day of writer-zone slack.
  - P2-14 load (Task 6): ice/mixed only, INSERT-only, partial months held.
  - `ingest` role via SQL (Task 4), `source_ingest_log` (Tasks 2 and 3), validation (Task 1), the `grid_bucket_key` SQL function (Task 2).
  - Everything else is mapped to plans 2–8 in the split table.
- **Review items for this file:**
  - A3/D3: partial months and the provenance check (Tasks 5, 6, 10).
  - A12/D4: one SQL function plus parity tests (Tasks 1, 2).
  - SEC1: `verify_full_url`, `tests/verify/_db.py` and the `db.verified_connect_args` refusal (Tasks 3, 5, 8–10).
  - SEC2: `pg_restore` into a scratch database (Task 8).
  - SEC3/D13: trainer is NOLOGIN with no grants (Task 4).
  - P4: INSERT-only, ice/mixed scope, and `total` = MP's reported total (Tasks 4, 6).
  - P1: Needs table and merge order.
  - D8: one cadence table.
  - D2, D5–D7, D9–D11, D14–D18: decision text.
  - Minors: the heavy-quarantine test now checks the table is empty; the `in_us` box caveat is documented and tested; the report-only R8 verify test now asserts reason and version sets.
- **Placeholders:** none. Runbook angle-bracket values are hosts the owner copies from the Console, as in Phase 1 Plan B; `2026-MM-DD` in Task 8 is the date the drill actually ran.
- **Type and name consistency:**
  - `ValidationReport.summary()` is the only report serializer, and `finish_run(..., report=...)` is used everywhere.
  - `grid_bucket_sql` renders `grid_bucket_key((…)::float8, (…)::float8)`, and the function name matches the ledger and `0004`.
  - `verified_connect_args`/`ingest_engine`, `tests.verify._db.fetch` and `validate(..., route_types=, existing=, today=)` are spelled the same in Interfaces, tests and code.
  - Contract names (`grid_bucket_series`, `cell_normals_status`, `era5_window`, `internal.r10_unresolved`, `0011_objectives`, `0012_drop_legacy_routes`) match the revision contract.
- **Review Focus:** each item maps to a named test in Tasks 1, 2, 3, 5 and 6.
