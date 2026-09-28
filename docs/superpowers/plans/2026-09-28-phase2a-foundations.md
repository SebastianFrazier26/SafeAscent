# Phase 2a Foundations (PR 2a-0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the data-platform floor every later Phase 2 plan stands on: boundary validation with quarantine, the ingest run log, the `internal` schema, the Phase 2a accident columns and frozen raw copy, the `ingest` and `trainer` roles, the R8 tick quarantine, and the loader for the privately scraped MP ice/mixed tick aggregates.

**Architecture:** A typed `app/pipelines/` package holds pure validation (`validate.py`), the 0.1° grid key (`grid.py`), the run log and quarantine writer (`ingest_log.py`) and the first two jobs (`mp_ticks_quarantine.py`, `mp_tick_aggregates.py`). Jobs are CLIs (`python -m app.pipelines.<job>`) that connect as the `ingest` role and are run as explicit owner/agent steps or by GitHub Actions; nothing runs at app startup. One Alembic revision (`0004`) adds the schema; roles and grants are owner-run SQL with SCRAM verifiers, following `create_roles.sql`.

**Tech Stack:** Python 3.12, uv, SQLAlchemy 2.0 async + asyncpg, Alembic 1.14, pydantic 2 (already a dependency), pytest + pytest-asyncio, ruff 0.8.4, mypy; Postgres 16 + PostGIS on Neon; psql 16 for role scripts.

**Spec:** `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md` (binding), amended by `docs/superpowers/specs/2026-09-28-phase3-amendment-similarity-confidence.md` (D1–D5, §3.1). Phase 3 consumer contract: `docs/superpowers/specs/2026-09-27-phase3-model-v2-design.md` (P3:150–161).

---

## How Phase 2 is split, and why

The spec is too large for one PR or one plan. It is cut into eight plans, each producing working, tested software on `main`, ordered so the Phase 3 critical path (MVP-0 needs 2a; MVP-1 needs 2a plus the similarity features the amendment pulled forward) lands first.

| # | File | Spec milestones | Produces | Needs |
|---|---|---|---|---|
| 1 | `2026-09-28-phase2a-foundations.md` (this) | 2a-0, R8, P2-14 load | validation core, run log, `internal` schema, accident columns, `accidents_raw`, roles, tick quarantine, tick-aggregate loader | Phase 1 merged |
| 2 | `2026-09-28-phase2a-accident-repair.md` | 2a-1, 2a-2 | R1–R5, R9, R12, GNIS summits, `accidents_clean` | 1 |
| 3 | `2026-09-28-phase2a-conditions-refresh.md` | 2a-3, 2a-4 | Open-Meteo archive client, `cell_daily_conditions`, incident backfill, R6 (build + verify), R7, R11 loaders | 1, 2 |
| 4 | `2026-09-28-phase2b-catalog.md` | 2b-1 | OpenBeta catalog, route types, matcher, `mp_facts`, MP schema split, `check_no_mp_data.py` | 1 |
| 5 | `2026-09-28-phase2b-similarity-features.md` | 2b-2 (part), amendment §3.1 | point elevation (3DEP), monthly climate normals, `route_static_features` MVP-1 columns, R10 legacy cleanup | 1, 3, 4 |
| 6 | `2026-09-28-phase2b-objectives-coverage.md` | 2b-2 (rest) | objectives, curated routes, coverage badges | 2, 4, 5 |
| 7 | `2026-09-28-phase2b-live-feeds-lightning.md` | 2b-3 | nightly forecast, alerts, AQI, SNOTEL, GLM live/daily, NLDN backfill, day-of-year climatology, `/health/data` | 3, 5 |
| 8 | `2026-09-28-phase2b-exposure-compaction.md` | 2b-4, 2b-5 | `exposure_index` v1, prediction archive compaction | 4, 6, 7 |

Alembic history is linear, so plans **merge** in numeric order of their revisions (`0004` plan 1, `0005`–`0006` plan 2, `0007` plan 3, `0008`–`0009` plan 4, `0010`–`0011` plan 5, `0012` plan 6, `0013` plan 7, `0014`–`0015` plan 8); a plan developed in parallel rebases its first revision's `down_revision` onto the current head (and renumbers) before merging.

Why this cut: each plan is one reviewable PR (plans 2, 3 and 7 are two PRs each, marked inside), each owns its own migration(s), and each ends with owner/agent runbook tasks that put its data in Neon. Plans 4 and 2/3 are independent after plan 1 and can run in parallel. Plan 5 keys features on **points**, not routes (Decision D7), so elevation and normals for every MP location can be computed before the OpenBeta catalog exists; only the per-route table waits for plan 4. 2b-6 (optional OpenBeta contribution) needs maintainer agreement and has no plan.

## Decisions needed (owner) — read before starting any Phase 2 plan

Each item is a real choice the spec leaves open or that later owner decisions reopened. Every plan is written assuming the **Recommendation**, and each place that depends on it is marked `[assumes D<n>]`. Overrule any of them and the marked tasks change; nothing else does.

**D1. Boundary validation library.**
- (a) pandera (the spec's word): DataFrame schemas; pulls in pandas (+~60 MB in the API image unless isolated) and typeguard.
- (b) pydantic 2 models per source row (already a dependency, typed, mypy plugin configured) plus a small in-house `ValidationReport` that counts and quarantines.
- *Recommendation: (b).* Same checks the spec lists (ranges, unique non-null keys, US bbox, ±5% row count) with no new dependency; row-level quarantine is easier to express per row than per frame.

**D2. MP data placement after owner decision D5 (2026-09-28).** The spec moves all `mp_*` tables to `internal` with no `app` access. D5 later allowed MP rock routes and tick aggregates to be displayed, and the live app reads `mp_routes`, `mp_locations` and `mp_ticks` today.
- (a) Spec as written: everything MP goes to `internal`. Breaks the map and the Ascents tab.
- (b) Split by content: `mp_routes`/`mp_locations` stay in `public` (displayable facts, no prose columns exist); raw `mp_ticks` (it has `climber_name`) moves to `internal`; the app reads ticks only through a public aggregate table (`mp_tick_counts`) built by `ingest`; `mp_tick_aggregates` stays `internal` until a display feature needs it.
- (c) Move nothing until the legal review of `DATA_LICENSE.md` lands.
- *Recommendation: (b)*, done in plan 4. It keeps personal data (climber names) out of the `app` role's reach, matches D5, and costs one endpoint change covered by the existing `test_ascent_analytics.py`.

**D3. What "future" means for stored and aggregated ticks.** Owner rule: reject ticks dated after the current UTC day at load time, and years after the current year.
- Stored `mp_ticks` rows were loaded at `created_at`. *Recommendation:* quarantine `future` when `tick_date > LEAST(created_at::date, run-time UTC today)`; a tick dated after the day it was captured is impossible and stays quarantined even after that date passes. Assumes Neon's `created_at` defaults were written in UTC (Neon's default `TimeZone`); Task 10 checks it.
- The private tick export is aggregated to `YYYY-MM`, so a day cannot be checked inside the current month. *Recommendation:* quarantine months after the current UTC month as `future_month`, and the current month as `month_not_closed`; a reload after the month ends picks those rows up.

**D4. Grid-bucket key.** P3 says "lat/lon rounded to 0.1°" and uses one column.
- (a) text `'40.0:-105.3'` (readable, 12+ bytes);
- (b) integer `floor(lat*10+0.5)*10000 + floor(lon*10+0.5) + 5000` (4 bytes; same float math in Python and SQL; decodes exactly);
- (c) two smallint columns.
- *Recommendation: (b).* `cell_daily_conditions` will hold tens of millions of rows; the key is in its PK.

**D5. Elevation / DEM source for every route point** (amendment §3.1, MVP-1).
- (a) USGS 3DEP 1/3″ (~10 m) read as remote Cloud-Optimized GeoTIFFs with rasterio, sampling only the blocks under our points (no bulk tile download), 2″ fallback where 1/3″ is missing (parts of Alaska); EPQS 200-point cross-check. Public domain; the same source v2.2 aspect/slope needs. Cost: rasterio (bundled GDAL) in a `pipelines` dependency group that the Railway image does not install.
- (b) Open-Meteo elevation API (Copernicus GLO-90, 90 m): 100 points per call, trivial client, attribution required, not reusable for aspect.
- (c) USGS EPQS per point: public domain, 3DEP-backed, no GDAL, but one point per request (~30K requests) and no aspect.
- *Recommendation: (a).* Highest accuracy, free, and no second DEM when v2.2 adds aspect.

**D6. Monthly climate normals source** (amendment §3.1: mean tmax, tmin, precipitation, snowfall, freeze-thaw days).
- (a) Open-Meteo archive `era5_seamless` (ERA5 + ERA5-Land), daily for the trailing 10 complete calendar years per 0.1° bucket, reduced to monthly normals. Same source as `cell_daily_conditions`, covers Alaska and Hawaii, gives snowfall and freeze-thaw directly. ~9–25 km native resolution smooths mountain temperatures. Cost is inside the already-decided P2-5 budget (one $99 Professional month).
- (b) PRISM 1991–2020 normals (800 m): best mountain temperatures in CONUS, free with citation, but CONUS only (a second source for AK/HI) and no snowfall or freeze-thaw days.
- (c) Daymet v4 (1 km daily, North America): freeze-thaw derivable, free, but one pixel per request and about a one-year publication lag.
- *Recommendation: (a)*, recording the reference elevation Open-Meteo used per bucket so the model can see the route-vs-bucket elevation gap; PRISM can be a v2.2 challenger through the gate.

**D7. Storage shape for static features and normals.**
- Spec: `route_static_features` per route and `cell_climatology(grid_bucket, doy, var, mean, p10, p90, n_years)` (long, day-of-year).
- *Recommendation:* (i) `feature_points` keyed by a rounded point (5 decimals) holding elevation and keys, so the work is per distinct point (spec: "computed once per distinct point") and independent of the catalog; `route_static_features` is then a per-route join table in the P3 shape. (ii) A wide monthly `cell_climate_normals(grid_bucket, month, ...)` (~5K buckets × 12 rows) for MVP-1, plus a wide day-of-year `cell_climatology(grid_bucket, doy, <var>_mean/_p10/_p90, ...)` (~1.8M rows, ≈0.2 GB, the spec's estimate) computed in the **same** paid ERA5 pass, because a second 10-year fetch would double the Open-Meteo cost; its lightning-day frequency column is filled later by plan 7. (iii) The 10-year daily series used to build normals is not persisted beyond the spec's 3-year window, which keeps Neon within the ~3.9 GB estimate.

**D8. Refresh cadence.** Data is continuously updated and "future" is always relative to run time.
- *Recommendation:* OpenBeta weekly (spec); Open-Meteo forecast nightly 01:00 UTC (spec); ERA5 archive weekly append of the days now ≥5 days old (ERA5 lag); climate normals yearly in January when a new complete year exists, never on a fixed year span; elevation computed once per new point (weekly after OpenBeta); GNIS quarterly; tick quarantine re-evaluated on every tick load and weekly; accidents on each refresh load.

**D9. The `weather` table (R6).** The live kernel (`app/api/v1/predict.py:571`, `app/tasks/safety_computation_optimized.py`) still joins `weather` on `accident_id`, and amendment D4 forbids interim scorer changes.
- (a) Spec: rename to `weather_legacy` and drop at 2a-3 (breaks the live kernel).
- (b) Build `cell_daily_conditions`, verify it against `weather` (r > 0.95), leave `weather` read-only in place, and drop it in the Phase 3 MVP-1 PR that deletes the kernel.
- *Recommendation: (b).* Plan 3 does everything in R6 except the drop and records the drop as a Phase 3 MVP-1 prerequisite.

**D10. R1 rows whose year stays unverified.** Spec marks them `unknown` + `year_unverified` but does not say what happens to `accidents.date`, which the live kernel reads.
- (a) Null the date (the row leaves the live kernel: fewer accidents, lower live risk);
- (b) leave `date` untouched and only set the new columns; Phase 3 excludes the row through `excluded_reason`.
- *Recommendation: (b).* Missing data must never read as safe; a corrupted-year row only over-weights recency (conservative). Rows the rule dates confidently get the repaired date, with a revision row.

**D11. OpenBeta ingest source.** The spec says weekly parquet bulk load plus GraphQL deltas. The exporter's `schema.sql` (read 2026-09-28) has no area UUIDs, no `mp_id`, no pitches and no `ice`/`mixed`/`aid`/`snow` flags, so the parquet file cannot build `canonical_areas` or `type_group`.
- *Recommendation:* a weekly full GraphQL load (`bulkAreas` per US state) is the only OpenBeta source; the spec's "reject if the US count drops more than 3%" compares against the previous successful weekly load, so the parquet file is not needed. Plan 4 Task 3 verifies the live GraphQL schema before coding; if the parquet export later gains area UUIDs and discipline flags, switching back is a contained change to `catalog.py`.

**D12. Where filled manual CSVs live.** `data/manual/aac_refresh.csv` (R11) holds AAC facts + URLs; Legal Q6 (AAC terms) is open, and the repo is public.
- *Recommendation:* commit only a header-only template and the loader; keep the filled CSV in `~/Developer/safeascent-private/manual/` until Q6 is answered. Same for owner review CSVs that contain accident text (`data/review/` is gitignored).

**D13. `trainer` role timing.** Phase 2 needs only `ingest`.
- *Recommendation:* create `trainer` now, read-only (`default_transaction_read_only = on`, SELECT on training inputs as they appear), so Phase 3 only adds its model-table writes. `triage_worker` is Phase 4 and is not created.

**D14. Pipeline credentials.** Settings is the only config surface, and `DATABASE_URL` is required at import.
- *Recommendation:* add `INGEST_DATABASE_URL: str | None = None` to `Settings`. Job CLIs refuse to run without it. GitHub Actions data workflows get it from an Actions secret and also set `DATABASE_URL` to the same value (the job never opens that engine; this only satisfies `Settings`). Railway gets it on the worker only when plan 7's beat-driven feeds land.

**D15. Lithology fallback (v2.2 feature, plan 5 PR 2b-2b).** The spec falls back to USGS SGMC polygons in PostGIS when Macrostrat returns nothing.
- (a) Load SGMC now: a national polygon layer, roughly 0.5–1 GB in Neon (storage cost and the 4.5 GB ceiling), used only for misses.
- (b) Macrostrat only; a miss is stored as `lithology = NULL`, `lithology_source = 'none'` (missing, never guessed); measure the miss rate first.
- *Recommendation: (b)*, revisit with the measured miss rate before v2.2 trains. Lithology is not an MVP-1 feature (amendment §3.1).

**D16. R10 legacy cleanup mechanics.** The spec drops `accidents.route_id`/`mountain_id` and the `routes`/`mountains` tables after relinking.
- *Recommendation:* the R10 job writes `accident_route_links`, then nulls `route_id`/`mountain_id` through the audited repair writer (old values kept in `internal.accident_revisions` and the pre-drop dump); migration `0011` refuses to run while any accident still has either column set. The API's `mountain_id` filter then returns 422 like `route_id` does today, and the two fields leave `AccidentResponse`. Tests that exist only to prove the old legacy-link bug (`test_accidents_count_by_mp_route_id_not_legacy_route_id`, `test_seed_really_has_the_colliding_legacy_link`) are deleted with the column.

**D17. OpenBeta tick counts (`ob_ticks` exposure component).** OpenBeta ticks exist only per climb in GraphQL (`Climb.ticks`), are sparse (17 on 320 Lumpy Ridge climbs), and are not in the export.
- (a) Monthly per-climb crawl: ~200K GraphQL requests per run, for a component that is near-empty.
- (b) Missing-flag `ob_ticks` everywhere in exposure v1 (Phase 3 estimates its coefficient as zero-information) and revisit if OpenBeta adds a bulk tick query.
- *Recommendation: (b).* Plan 8 writes the component as missing with reason `no_bulk_source`.

**D18. Prediction compaction mechanics (P2-8).** The spec has the nightly run upsert `internal.prediction_archive_v1.scores[day]`. The owner rule keeps `app` SELECT-only except `historical_predictions`, and the historical-trends endpoint (`app/api/v1/mp_routes.py:1570`) must read old days.
- (a) Spec as written: `app` gains INSERT/UPDATE on an `internal` table (breaks both the owner rule and the "`app` has no privilege on `internal`" guard).
- (b) The nightly run keeps writing `historical_predictions`; a daily `ingest` job folds rows older than 7 days into `public.prediction_archive_v1(route_id, month_start, scores smallint[31])` and deletes them; the trends endpoint reads both. `app` gets SELECT on the archive only. Score `-1` marks an insufficient (gray) day and NULL marks no prediction, because a bare NULL cannot tell them apart.
- *Recommendation: (b).* Same storage outcome (3.37 GB → ~0.2 GB), cadence unchanged, owner rule and guards intact; MP route ids in a public table are allowed since amendment D5.

## Global Constraints

- Branch per PR off an up-to-date `main`: this plan is `feat/p2a-foundations`. Never commit to `main`. Never `git push`; the owner pushes with `/commitandpush`.
- CI's single required check is `ci-ok` (jobs `backend`, `frontend`, `guards`, `ci-ok`); do not rename jobs. Every task leaves `uv run pytest`, `uv run ruff check . ../scripts/` and `uv run mypy` green (run from `backend/`).
- Python via uv; ruff 0.8.4 (`E4,E7,E9,F`); every new core module is typed and added to a mypy strict allowlist block in `backend/pyproject.toml`.
- Migrations: new Alembic revisions from `0004`, run as `migrator`, rehearsed on a Neon branch, applied as an explicit owner/agent step, **never at app startup**; forward-only unless the revision says otherwise; downgrades refuse when they would lose data (the `0002`/`0003` convention). No DDL anywhere under `backend/app/` (`tests/test_no_runtime_ddl.py`).
- Roles: created only via owner-run SQL with client-side SCRAM-SHA-256 verifiers (`scripts.write_role_url --scram`), never neonctl/Console/API. `app` stays SELECT-only except `historical_predictions`. `app` has no privilege on schema `internal`. Least privilege for `ingest` and `trainer`.
- Secrets: agents never read `backend/.env*` and never print credentials; runbook commands load env files inside subshells and pass passwords via `PGPASSWORD`, never argv.
- No scraper code in the repo, ever (`scripts/check_no_scrapers.py`). Loaders may read a private export file; they never fetch MP pages. The MP scrape runs only in `~/Developer/safeascent-private`.
- Mountain Project: route facts and tick aggregates may be displayed; MP descriptions/prose never, anywhere (fixtures, docs, commits). No real MP ids, names or URLs in tests or fixtures; use synthetic ids ≥ 900000000. `DATA_LICENSE.md` is not edited (pending legal review).
- OpenBeta is CC0; never OpenBeta photos/media.
- Accuracy over everything: missing or thin data never reads as safe; missing numeric values are NULL, never 0; every loader validates types, ranges, dates and coordinates at the boundary and quarantines bad rows (never silently drops) and reports counts.
- "Future" means after the current UTC day **at run time** (`today` is read per run and injected into pure functions; never a constant date). Tick loads also reject years after the current year.
- Accidents link to routes via `accidents.mp_route_id`; legacy `routes`/`mountains` and `accidents.route_id`/`mountain_id` are untouched until plan 5's guarded R10 migration.
- Cost: batch jobs over always-on services; Neon launch plan (~3.7 GB used); Railway us-east4.
- Comments are load-bearing only. `CHANGELOG.md` gets one dated entry per PR (Keep a Changelog).
- TDD: failing test, minimal code, green, commit.

## Review Focus

1. **A tick month equal to the current month** at load time — expect quarantine `month_not_closed`, not acceptance and not silent loss (Task 6 test `test_current_month_is_quarantined_not_dropped`).
2. **A stored tick dated after its own `created_at` day but before today** — expect `future` (Task 5 test `test_tick_after_capture_day_is_future_even_when_before_today`).
3. **A coordinate on the antimeridian side of the Aleutians (lon +175)** — expect inside the US, not quarantined (Task 1 test `test_aleutians_east_of_180_are_inside_the_us`).
4. **A second load of the identical tick export** — expect a logged no-op, zero rows upserted, not duplicate counts (Task 6 test `test_second_identical_load_is_a_noop`).
5. **Running `0004` on prod where `migrator` cannot create schemas and the owner script has not run** — expect a clear refusal naming `create_roles_phase2.sql`, not a half-applied migration (Task 2 test `test_0004_refuses_without_internal_schema_when_unprivileged`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/app/pipelines/__init__.py` | Create | Package marker (docstring states the no-scraper rule). |
| `backend/app/pipelines/validate.py` | Create | US bbox, date/range/coord problem checks, `ValidationReport`, batch gate. |
| `backend/app/pipelines/grid.py` | Create | 0.1° grid key in Python and SQL `[assumes D4]`. |
| `backend/app/pipelines/db.py` | Create | `ingest_engine()` from `settings.INGEST_DATABASE_URL`. |
| `backend/app/pipelines/ingest_log.py` | Create | Run log, no-op detection, quarantine writer, content hashing. |
| `backend/app/pipelines/mp_ticks_quarantine.py` | Create | R8 classifier + set-based SQL job + CLI. |
| `backend/app/pipelines/mp_tick_aggregates.py` | Create | Private SQLite export reader, validation, upsert + CLI. |
| `backend/app/models/pipeline.py` | Create | Typed models: `SourceIngestLog`, `AccidentRevision`, `IngestQuarantine`, `MpTickAggregate`. |
| `backend/app/models/accident.py` | Modify | Phase 2a columns. |
| `backend/app/models/__init__.py` | Modify | Import `pipeline`. |
| `backend/app/config.py` | Modify | `INGEST_DATABASE_URL`. |
| `.env.example` | Modify | `INGEST_DATABASE_URL=`. |
| `backend/alembic/env.py` | Modify | `include_schemas` limited to `public` and `internal`. |
| `backend/alembic/versions/0004_phase2a_foundation.py` | Create | Schema guard, `internal.accidents_raw`, accident columns, run log, revisions, quarantine, tick aggregates, tick quarantine columns. |
| `backend/db/roles/create_roles_phase2.sql` | Create | `ingest`, `trainer`, schema `internal` (owner-only: needs database CREATE). |
| `backend/db/roles/grants_phase2.sql` | Create | Idempotent Phase 2 grants (extended by later plans). |
| `backend/db/roles/verify_roles_phase2.sql` | Create | Exact-privilege checks for `ingest`/`trainer` and `app` vs `internal`. |
| `backend/scripts/write_role_url.py` | Modify | `ROLES` gains `ingest`, `trainer`. |
| `backend/tests/pgtest.py` | Create | Throwaway migrated DB helper for pipeline tests. |
| `backend/tests/test_validate.py`, `test_grid.py`, `test_migration_0004.py`, `test_ingest_log.py`, `test_roles_phase2.py`, `test_mp_ticks_quarantine.py`, `test_mp_tick_aggregates.py` | Create | Tests. |
| `backend/tests/verify/__init__.py`, `backend/tests/verify/test_phase2a_foundation.py` | Create | `-m db` acceptance checks against a Neon branch as `analyst`. |
| `backend/tests/test_write_role_url.py` | Modify | New roles accepted. |
| `backend/pyproject.toml` | Modify | mypy allowlist blocks; `db` marker; default deselection. |
| `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`, `CHANGELOG.md` | Modify | Roles, schema, jobs, commands. |

## Pre-flight conflict ledger (this plan)

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 3, 5, 6 | `validate.ValidationReport`, `today` injection | Task 1 first; signatures frozen in its Interfaces block. |
| 1 | plans 3, 5, 7 | `grid.grid_bucket`, `grid_bucket_sql` | Frozen here; later plans import, never redefine. |
| 2 | 3, 5, 6 | tables `source_ingest_log`, `internal.ingest_quarantine`, `internal.mp_tick_aggregates`, `mp_ticks.quarantine_reason` | Task 2 before any DB-backed task. |
| 2 | 4 | `internal` schema creation (migration guard vs owner script) | Both use `CREATE SCHEMA IF NOT EXISTS internal`; owner script sets `AUTHORIZATION migrator`; test in Task 4 runs the prod order. |
| 1, 3, 5, 6 | each other | `backend/pyproject.toml` mypy allowlist | Serial; each task appends its module names to the same block. |
| 3 | 5, 6 | `ingest_log.start_run/finish_run/write_quarantine/find_completed` | Task 3 before 5 and 6. |
| 4 | 5, 6, all later plans | `backend/db/roles/grants_phase2.sql`, `verify_roles_phase2.sql` | Cumulative files: each later plan appends its grants and expected-privilege rows in its own migration task. |
| 2 | 7 | `backend/app/models/accident.py` docs | Task 7 documents only. |
| 5 | plan 4 (MP split) | `mp_ticks` moves to `internal.mp_ticks` | Plan 4 Task 6 updates `mp_ticks_quarantine.QUARANTINE_SQL` and its test in the same commit as the move. |
| 2 | plan 2 | `accidents` columns and `internal.accident_revisions (accident_id, field, rule_version)` unique key | Frozen here; plan 2 only writes rows. |
| 2 | plan 5 (R10) | `app/models/legacy.py`, `accidents.route_id`/`mountain_id` | Untouched here. |
| 7 | every later plan | `CHANGELOG.md`, `CLAUDE.md`, `DEPLOYMENT.md` | Each plan adds its own dated entry/lines; rebase before merge. |

## Cross-plan interface ledger (frozen names later plans rely on)

| Interface | Defined in | Consumed by |
|---|---|---|
| `validate.ValidationReport`, `in_us`, `date_problem`, `coord_problem`, `range_problem`, `batch_gate` | 1/T1 | 2, 3, 4, 5, 6, 7, 8 |
| `grid.grid_bucket`, `grid.bucket_center`, `grid.grid_bucket_sql` | 1/T1 | 3, 5, 6, 7, 8 |
| `ingest_log.start_run`, `finish_run`, `find_completed`, `write_quarantine`, `sha256_rows`, `last_ok_rows_in` | 1/T3 | 2–8 |
| `db.ingest_engine` | 1/T3 | 2–8 |
| `accidents` Phase 2a columns; `internal.accident_revisions` | 1/T2 | 2, 3, 5 |
| `app/data/repair/framework.apply_changes` | 2/T1 | 2, 3, 5 |
| `open_meteo.ArchiveClient` | 3/T1 | 5, 7 |
| `cell_daily_conditions` | 3/T2 | 5, 7, 8 |
| `canonical_routes`, `canonical_areas`, `route_types.map_type_group`, `match.*` | 4 | 5, 6, 8 |
| `feature_points`, `route_static_features`, `cell_climate_normals` | 5 | 6, 7, 8, Phase 3 |
| `objectives`, `coverage.badge_for` | 6 | 7, 8, Phase 3 |

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
  - `grid.bucket_index(x: float) -> int`, `grid.grid_bucket(lat: float, lon: float) -> int`, `grid.bucket_center(bucket: int) -> tuple[float, float]`, `grid.grid_bucket_sql(lat_expr: str, lon_expr: str) -> str`

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


def test_sql_expression_uses_the_same_math():
    assert grid_bucket_sql("l.latitude", "l.longitude") == (
        "(floor(l.latitude * 10 + 0.5)::int * 10000 + floor(l.longitude * 10 + 0.5)::int + 5000)"
    )
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
double-precision round() is platform-dependent, while this expression is identical IEEE
math on both sides, so a bucket computed in SQL always equals one computed here.
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
    return (
        f"(floor({lat_expr} * 10 + 0.5)::int * {LAT_STRIDE} + floor({lon_expr} * 10 + 0.5)::int + {LON_OFFSET})"
    )
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

import asyncpg
import pytest
from alembic import command

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
"""Phase 2a foundation: internal schema, frozen accidents_raw, accident repair columns,
ingest run log, quarantine, MP tick aggregates, mp_ticks quarantine columns.

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
- Create: `backend/app/pipelines/ingest_log.py`, `backend/app/pipelines/db.py`, `backend/tests/test_ingest_log.py`
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

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_ingest_log.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipelines.ingest_log'`.

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
"""The ingest role's engine. Jobs never use DATABASE_URL (the app role cannot write)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.ssl import connect_args_for


def ingest_engine() -> AsyncEngine:
    url = settings.INGEST_DATABASE_URL
    if not url:
        raise SystemExit("INGEST_DATABASE_URL is not set (the ingest role's URL); see DEPLOYMENT.md")
    return create_async_engine(url, poolclass=NullPool, connect_args=connect_args_for(url))
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
# the data workflows and, from plan 7, the worker. Job CLIs refuse to run without it.
INGEST_DATABASE_URL=
```

Append `"app.pipelines.ingest_log", "app.pipelines.db"` to the first strict mypy block.

- [ ] **Step 4: Run**

Run: `cd backend && uv run pytest tests/test_ingest_log.py tests/test_env_example_parity.py tests/test_settings.py -q && uv run mypy && uv run ruff check . ../scripts/`
Expected: PASS (DB tests run in CI; they skip locally without `MIGRATIONS_TEST_ADMIN_URL`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipelines/ingest_log.py backend/app/pipelines/db.py backend/app/config.py .env.example \
  backend/tests/test_ingest_log.py backend/pyproject.toml
git commit -m "feat(pipelines): ingest run log, quarantine writer, ingest engine"
```

---

### Task 4: `ingest` and `trainer` roles, `internal` schema, Phase 2 grants

**Files:**
- Create: `backend/db/roles/create_roles_phase2.sql`, `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`, `backend/tests/test_roles_phase2.py`
- Modify: `backend/scripts/write_role_url.py` (`ROLES`), `backend/tests/test_write_role_url.py`

**Interfaces:**
- Consumes: Phase 1 roles (`migrator`, `app`, `analyst`) already created by `create_roles.sql`; migration `0004`.
- Produces: roles `ingest`, `trainer`; schema `internal AUTHORIZATION migrator`; env vars `INGEST_PASSWORD_SCRAM`, `TRAINER_PASSWORD_SCRAM`; `write_role_url --role ingest|trainer`. `grants_phase2.sql` and `verify_roles_phase2.sql` are **cumulative**: later plans append grants and expected rows.

- [ ] **Step 1: Failing tests**

In `backend/tests/test_write_role_url.py` add:

```python
@pytest.mark.parametrize("role", ["ingest", "trainer"])
def test_phase2_roles_are_accepted(role, monkeypatch, capsys):
    monkeypatch.setenv(f"{role.upper()}_PASSWORD", "pw-for-test")
    assert main(["--role", role, "--scram"]) == 0
    assert capsys.readouterr().out.startswith("SCRAM-SHA-256$4096:")
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

from scripts.write_role_url import scram_verifier
from tests.pgtest import requires_pg
from tests.test_migrations import (
    ADMIN_URL,
    ANALYST_FIXTURE_SQL,
    OWNER_ROLE,
    PASSWORDS,
    ROLES_DIR,
    _alembic_cfg,
    _as,
    _denied,
    _psql,
    _require_psql,
    _role_url,
    _run,
    fresh_db,  # noqa: F401  (fixture)
)

pytestmark = requires_pg
PHASE2_PASSWORDS = {"ingest": "test-ingest-pw", "trainer": "test-trainer-pw"}
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

    phase1 = {r: scram_verifier(PASSWORDS[r]) for r in ("migrator", "app")}
    ok = _psql(owner_url, ROLES_DIR / "create_roles.sql",
               {"MIGRATOR_PASSWORD_SCRAM": phase1["migrator"], "APP_PASSWORD_SCRAM": phase1["app"]})
    assert ok.returncode == 0, ok.stderr

    phase2 = {r: scram_verifier(p) for r, p in PHASE2_PASSWORDS.items()}
    created = _psql(owner_url, ROLES_DIR / "create_roles_phase2.sql",
                    {"INGEST_PASSWORD_SCRAM": phase2["ingest"], "TRAINER_PASSWORD_SCRAM": phase2["trainer"]},
                    "--echo-queries")
    assert created.returncode == 0, created.stderr
    for plaintext in PHASE2_PASSWORDS.values():
        assert plaintext not in created.stdout + created.stderr

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
    trainer = _role_url(fresh_db, "trainer", PHASE2_PASSWORDS["trainer"])
    app = _role_url(fresh_db, "app", PASSWORDS["app"])

    _as(ingest, "INSERT INTO source_ingest_log (run_id, source, status) VALUES (gen_random_uuid(), 't', 'running')")
    _as(ingest, "INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) VALUES (900000001, 'total', 'all', 1)")
    _as(ingest, "UPDATE mp_ticks SET quarantine_reason = NULL WHERE false")
    _denied(ingest, "UPDATE mp_ticks SET climber_name = 'x' WHERE false")
    _denied(ingest, "DELETE FROM source_ingest_log")
    _denied(ingest, "UPDATE historical_predictions SET risk_score = 1 WHERE false")
    _denied(ingest, "CREATE TABLE internal.nope (x int)")
    _denied(ingest, "CREATE TABLE public.nope (x int)")

    _as(trainer, "SELECT count(*) FROM accidents")
    with pytest.raises(asyncpg.exceptions.ReadOnlySQLTransactionError):
        _as(trainer, "INSERT INTO source_ingest_log (run_id, source, status) VALUES (gen_random_uuid(), 't', 'running')")

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

`backend/scripts/write_role_url.py`: `ROLES = ("migrator", "app", "ingest", "trainer")`.

`backend/db/roles/create_roles_phase2.sql`:

```sql
-- Run as the database owner, once, after create_roles.sql. Verifiers only, via \getenv,
-- exactly as create_roles.sql: no password reaches the server, its logs, or argv.
\set ON_ERROR_STOP on
\set VERBOSITY terse
\set SHOW_CONTEXT never

\getenv ingest_scram INGEST_PASSWORD_SCRAM
\getenv trainer_scram TRAINER_PASSWORD_SCRAM
\if :{?ingest_scram}
\else
  DO $$ BEGIN RAISE EXCEPTION 'INGEST_PASSWORD_SCRAM is not set'; END $$;
\endif
\if :{?trainer_scram}
\else
  DO $$ BEGIN RAISE EXCEPTION 'TRAINER_PASSWORD_SCRAM is not set'; END $$;
\endif

SELECT :'ingest_scram' ~ '^SCRAM-SHA-256\$[0-9]+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$'
   AND :'trainer_scram' ~ '^SCRAM-SHA-256\$[0-9]+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$'
   AS verifiers_ok \gset
\if :verifiers_ok
\else
  DO $$ BEGIN RAISE EXCEPTION 'INGEST_PASSWORD_SCRAM / TRAINER_PASSWORD_SCRAM must be SCRAM-SHA-256 verifiers'; END $$;
\endif

SELECT to_regrole('migrator') IS NOT NULL AND to_regrole('app') IS NOT NULL AND to_regrole('analyst') IS NOT NULL
  AS phase1_roles_ok \gset
\if :phase1_roles_ok
\else
  DO $$ BEGIN RAISE EXCEPTION 'run create_roles.sql first (migrator, app, analyst must exist)'; END $$;
\endif

BEGIN;

CREATE ROLE ingest LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'ingest_scram';
CREATE ROLE trainer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'trainer_scram';
-- Read-only until Phase 3 grants its model-table writes.
ALTER ROLE trainer SET default_transaction_read_only = on;

-- migrator cannot create schemas (verify_roles.sql asserts it), so the owner does it here.
CREATE SCHEMA IF NOT EXISTS internal AUTHORIZATION migrator;
REVOKE ALL ON SCHEMA internal FROM PUBLIC;

GRANT USAGE ON SCHEMA public TO ingest, trainer;

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
GRANT SELECT, INSERT, UPDATE ON internal.mp_tick_aggregates TO ingest;
GRANT SELECT, INSERT ON internal.ingest_quarantine TO ingest;
GRANT SELECT ON public.accidents TO trainer;

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
  ('trainer defaults to read-only transactions',
     EXISTS (SELECT 1 FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole
             WHERE r.rolname = 'trainer' AND 'default_transaction_read_only=on' = ANY (s.setconfig))),
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
  ('internal.mp_tick_aggregates', 'INSERT'), ('internal.mp_tick_aggregates', 'UPDATE'),
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
  WHERE n.nspname IN ('public', 'internal') AND c.relkind IN ('r', 'p')
)
INSERT INTO role_checks
SELECT 'app/trainer hold nothing on internal table ' || tbl,
       NOT (has_table_privilege('app', oid, 'SELECT') OR has_table_privilege('trainer', oid, 'SELECT'))
FROM tables WHERE tbl LIKE 'internal.%'
UNION ALL
SELECT 'trainer has no write privilege on ' || tbl,
       NOT (has_table_privilege('trainer', oid, 'INSERT') OR has_table_privilege('trainer', oid, 'UPDATE')
            OR has_table_privilege('trainer', oid, 'DELETE') OR has_table_privilege('trainer', oid, 'TRUNCATE'))
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
git commit -m "feat(db): ingest and trainer roles, internal schema, Phase 2 grants and checks"
```

---

### Task 5: R8 — quarantine garbage `mp_ticks` rows

**Files:**
- Create: `backend/app/pipelines/mp_ticks_quarantine.py`, `backend/tests/test_mp_ticks_quarantine.py`, `backend/tests/verify/__init__.py`, `backend/tests/verify/test_phase2a_foundation.py`
- Modify: `backend/pyproject.toml` (mypy allowlist; `db` marker; default deselection)

**Interfaces:**
- Consumes: `ingest_log.start_run/finish_run` (Task 3), `ValidationReport` (Task 1), `db.ingest_engine` (Task 3), `temporal_weighting.utc_today()` (existing, `app/services/temporal_weighting.py:25`).
- Produces: `RULE_VERSION = "r8-v1"`, `classify_tick(tick_date: date | None, captured_on: date | None, route_known: bool, today: date) -> str | None`, `QUARANTINE_SQL: str`, `async run(conn: AsyncConnection, *, today: date) -> dict[str, int]` (counts by reason after the run), CLI `python -m app.pipelines.mp_ticks_quarantine`. `[assumes D3]`

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
  (7, '900000001', 'c', NULL,         '2026-02-08 10:00');
"""


@requires_pg
def test_sql_matches_the_python_rule_and_is_idempotent():
    async def scenario(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                counts = await run(conn, today=TODAY)
            assert counts == {"future": 2, "orphan_route": 2, "pre_1970": 1, "clean": 2}
            async with engine.begin() as conn:
                again = await run(conn, today=TODAY)
                changed = (await conn.execute(text(
                    "SELECT rows_upserted FROM source_ingest_log WHERE source = 'mp_ticks_quarantine' "
                    "ORDER BY finished_at DESC LIMIT 1"))).scalar_one()
            assert again == counts
            assert changed == 0
            async with engine.connect() as conn:
                rows = dict((await conn.execute(text("SELECT tick_id, quarantine_reason FROM mp_ticks"))).all())
            assert rows == {1: None, 2: "future", 3: "future", 4: "orphan_route", 5: "orphan_route", 6: "pre_1970", 7: None}
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
stays flagged even once the calendar passes it. created_at is written by a UTC server.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

RULE_VERSION = "r8-v1"
EPOCH = date(1970, 1, 1)


def classify_tick(tick_date: date | None, captured_on: date | None, route_known: bool, today: date) -> str | None:
    cutoff = min(today, captured_on) if captured_on is not None else today
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
           WHEN t.tick_date > LEAST(CAST(:today AS date), t.created_at::date) THEN 'future'
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

`backend/tests/verify/test_phase2a_foundation.py`:

```python
"""Acceptance checks for plan 1, run by the owner/agent against a Neon branch or prod as
the read-only analyst role: VERIFY_DATABASE_URL=<analyst url> uv run pytest -m db tests/verify."""

import asyncio
import os

import asyncpg
import pytest

pytestmark = pytest.mark.db
URL = os.environ.get("VERIFY_DATABASE_URL")


def _fetch(sql: str) -> list[asyncpg.Record]:
    if not URL:
        pytest.skip("VERIFY_DATABASE_URL not set")

    async def go() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(URL)
        try:
            return await conn.fetch(sql)
        finally:
            await conn.close()

    return asyncio.run(go())


def test_accidents_raw_matches_live_row_count():
    [row] = _fetch("SELECT (SELECT count(*) FROM internal.accidents_raw) = (SELECT count(*) FROM accidents) AS same")
    assert row["same"]


def test_no_future_tick_is_unflagged():
    [row] = _fetch(
        "SELECT count(*) AS n FROM mp_ticks WHERE quarantine_reason IS NULL "
        "AND tick_date > LEAST((now() AT TIME ZONE 'UTC')::date, created_at::date)"
    )
    assert row["n"] == 0


def test_r8_counts_are_reported():
    rows = _fetch("SELECT coalesce(quarantine_reason, 'clean') AS r, count(*) AS n FROM mp_ticks GROUP BY 1 ORDER BY 1")
    print({r["r"]: r["n"] for r in rows})
    assert rows
```

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
- Consumes: Tasks 1, 3; `internal.mp_tick_aggregates` (Task 2); the private export's tables `route_tick_totals(mp_route_id, total_ticks, last_page, pages_fetched, complete, scraped_at)` and `route_tick_monthly(mp_route_id, year_month, style, n)` (read from `~/Developer/safeascent-private/mp_ticks/src/mp_ticks/db.py` on 2026-09-28; styles `lead|follow|tr|solo|unknown`).
- Produces: `read_export(path: Path) -> tuple[list[RouteTotal], list[MonthlyRow]]`, `validate(totals, monthly, *, known_routes: set[int], today: date) -> tuple[list[AggregateRow], ValidationReport, dict[str, int]]`, `async load(conn, rows: list[AggregateRow], *, run_id: uuid.UUID, scrape_run_id: str) -> int`, `async main(path: Path, *, today: date, max_quarantine_share: float, dry_run: bool) -> dict[str, object]`, CLI `python -m app.pipelines.mp_tick_aggregates --sqlite PATH [--dry-run] [--max-quarantine-share 0.10]`. `[assumes D3]`

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
from app.pipelines.mp_tick_aggregates import main, read_export, validate
from tests.pgtest import migrated_db, requires_pg, sa_url

TODAY = date(2026, 9, 28)
R1, R2, R3 = 900000001, 900000002, 900000003


def _export(tmp_path: Path, monthly: list[tuple[int, str, str, int]], totals: list[tuple[int, int, int]]) -> Path:
    path = tmp_path / "ticks.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE route_tick_totals (mp_route_id INTEGER PRIMARY KEY, total_ticks INTEGER NOT NULL, "
        "last_page INTEGER NOT NULL, pages_fetched INTEGER NOT NULL, complete INTEGER NOT NULL, scraped_at TEXT NOT NULL);"
        "CREATE TABLE route_tick_monthly (mp_route_id INTEGER NOT NULL, year_month TEXT NOT NULL, style TEXT NOT NULL, "
        "n INTEGER NOT NULL, PRIMARY KEY (mp_route_id, year_month, style));"
    )
    db.executemany("INSERT INTO route_tick_monthly VALUES (?, ?, ?, ?)", monthly)
    db.executemany(
        "INSERT INTO route_tick_totals VALUES (?, ?, 1, 1, ?, '2026-09-20T00:00:00+00:00')", totals
    )
    db.commit()
    db.close()
    return path


def test_loader_imports_no_network_client():
    tree = ast.parse(Path(loader.__file__).read_text())
    imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"httpx", "requests", "urllib", "aiohttp", "http", "socket"}


def test_current_month_is_quarantined_not_dropped(tmp_path):
    path = _export(tmp_path, [(R1, "2026-09", "lead", 2), (R1, "2026-01", "lead", 3)], [(R1, 5, 1)])
    totals, monthly = read_export(path)
    rows, report, _ = validate(totals, monthly, known_routes={R1}, today=TODAY)
    assert report.quarantined == {"month_not_closed": 1}
    assert {(r.period, r.style, r.tick_count) for r in rows} == {("2026-01", "lead", 3), ("total", "all", 3)}


def test_future_months_years_bad_values_and_incomplete_routes_are_quarantined(tmp_path):
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
        ],
        [(R1, 9, 1), (R2, 4, 0), (R3, 4, 1)],
    )
    totals, monthly = read_export(path)
    rows, report, stats = validate(totals, monthly, known_routes={R1, R2}, today=TODAY)
    assert report.quarantined == {
        "future_month": 2,
        "pre_1970": 1,
        "bad_year_month": 1,
        "nonpositive_count": 1,
        "bad_style": 1,
        "route_incomplete": 1,
        "unknown_route": 1,
    }
    assert rows == []
    assert stats == {"routes": 3, "mp_reported_total": 17, "accepted_ticks": 0}


@requires_pg
def test_second_identical_load_is_a_noop(tmp_path, monkeypatch):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3), (R1, "2025-02", "follow", 2)], [(R1, 5, 1)])
    seed = (
        f"INSERT INTO mp_locations (mp_id, name) VALUES (900000100, 'Fixture Area');"
        f"INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES ({R1}, 'Fixture Ice', 900000100, 'Ice');"
    )
    with migrated_db(seed_sql=seed) as name:
        url = sa_url(name)

        def engine_factory():
            return create_async_engine(url)

        monkeypatch.setattr(loader, "ingest_engine", engine_factory)
        first = asyncio.run(main(path, today=TODAY, max_quarantine_share=0.1, dry_run=False))
        second = asyncio.run(main(path, today=TODAY, max_quarantine_share=0.1, dry_run=False))
        assert first["status"] == "ok" and first["rows_upserted"] == 3
        assert second["status"] == "noop"

        async def check() -> list[tuple[str, str, int]]:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as conn:
                    result = await conn.execute(text(
                        "SELECT period, style, tick_count FROM internal.mp_tick_aggregates ORDER BY period, style"))
                    return [tuple(r) for r in result.all()]
            finally:
                await engine.dispose()

        assert asyncio.run(check()) == [("2025-01", "lead", 3), ("2025-02", "follow", 2), ("total", "all", 5)]


@requires_pg
def test_heavy_quarantine_rejects_the_batch_and_writes_nothing(tmp_path, monkeypatch):
    path = _export(tmp_path, [(R1, "2026-10", "lead", 1), (R1, "2025-01", "lead", 1)], [(R1, 2, 1)])
    seed = (
        "INSERT INTO mp_locations (mp_id, name) VALUES (900000100, 'Fixture Area');"
        f"INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES ({R1}, 'Fixture Ice', 900000100, 'Ice');"
    )
    with migrated_db(seed_sql=seed) as name:
        monkeypatch.setattr(loader, "ingest_engine", lambda: create_async_engine(sa_url(name)))
        result = asyncio.run(main(path, today=TODAY, max_quarantine_share=0.1, dry_run=False))
        assert result["status"] == "rejected"
        assert result["rows_upserted"] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_mp_tick_aggregates.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`backend/app/pipelines/mp_tick_aggregates.py`:

```python
"""Load the privately scraped MP ice/mixed tick aggregates (P2-14) into
internal.mp_tick_aggregates. Reads a local SQLite export only; it never fetches anything.

Months are the finest grain in the export, so "after today" is enforced per month: later
months are future, and the current month is held back until it closes (D3).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.db import ingest_engine
from app.pipelines.ingest_log import find_completed, finish_run, sha256_rows, start_run, write_quarantine
from app.pipelines.validate import ValidationReport, batch_gate

SOURCE = "mp_tick_aggregates"
STYLES = frozenset({"lead", "follow", "tr", "solo", "unknown"})
YEAR_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


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


def _month_problem(year_month: str, today: date) -> str | None:
    match = YEAR_MONTH.match(year_month)
    if match is None:
        return "bad_year_month"
    year, month = int(match.group(1)), int(match.group(2))
    if year > today.year or (year, month) > (today.year, today.month):
        return "future_month"
    if (year, month) == (today.year, today.month):
        return "month_not_closed"
    if year < 1970:
        return "pre_1970"
    return None


def validate(
    totals: list[RouteTotal], monthly: list[MonthlyRow], *, known_routes: set[int], today: date
) -> tuple[list[AggregateRow], ValidationReport, dict[str, int]]:
    by_route = {t.mp_route_id: t for t in totals}
    report = ValidationReport(SOURCE)
    accepted: list[AggregateRow] = []
    route_sums: dict[int, int] = {}
    for row in monthly:
        ref = f"{row.mp_route_id}:{row.year_month}:{row.style}"
        total = by_route.get(row.mp_route_id)
        if row.mp_route_id not in known_routes:
            report.quarantine(ref, "unknown_route")
        elif total is None or not total.complete:
            report.quarantine(ref, "route_incomplete")
        elif (problem := _month_problem(row.year_month, today)) is not None:
            report.quarantine(ref, problem, n=row.n)
        elif row.style not in STYLES:
            report.quarantine(ref, "bad_style", style=row.style)
        elif row.n <= 0:
            report.quarantine(ref, "nonpositive_count", n=row.n)
        else:
            report.accept()
            accepted.append(AggregateRow(row.mp_route_id, row.year_month, row.style, row.n, total.scraped_at))
            route_sums[row.mp_route_id] = route_sums.get(row.mp_route_id, 0) + row.n
    for route_id, n in sorted(route_sums.items()):
        accepted.append(AggregateRow(route_id, "total", "all", n, by_route[route_id].scraped_at))
    stats = {
        "routes": len(by_route),
        "mp_reported_total": sum(t.total_ticks for t in totals),
        "accepted_ticks": sum(route_sums.values()),
    }
    return accepted, report, stats


async def load(conn: AsyncConnection, rows: list[AggregateRow], *, run_id: uuid.UUID, scrape_run_id: str) -> int:
    if not rows:
        return 0
    await conn.execute(
        text(
            "INSERT INTO internal.mp_tick_aggregates "
            "(mp_route_id, period, style, tick_count, scrape_run_id, scraped_at, loaded_run_id) "
            "VALUES (:mp_route_id, :period, :style, :tick_count, :scrape_run_id, :scraped_at, :run_id) "
            "ON CONFLICT (mp_route_id, period, style) DO UPDATE SET tick_count = EXCLUDED.tick_count, "
            "scrape_run_id = EXCLUDED.scrape_run_id, scraped_at = EXCLUDED.scraped_at, loaded_run_id = EXCLUDED.loaded_run_id"
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
        [(t.mp_route_id, t.total_ticks, t.complete) for t in totals]
        + [(m.mp_route_id, m.year_month, m.style, m.n) for m in monthly]
        + [("today-month", today.year, today.month)]
    )
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            if await find_completed(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=sha):
                return {"status": "noop", "rows_upserted": 0}
            known = {int(r) for (r,) in (await conn.execute(text("SELECT mp_route_id FROM mp_routes"))).all()}
            rows, report, stats = validate(totals, monthly, known_routes=known, today=today)
            problems = batch_gate(report, previous_rows_in=None, count_tolerance=1.0, max_quarantine_share=max_quarantine_share)
            if dry_run:
                return {"status": "dry_run", "report": report.summary(), "stats": stats, "problems": problems}
            run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=sha)
            await write_quarantine(conn, run_id, report)
            if problems:
                await finish_run(conn, run_id, status="rejected", report=report, rows_upserted=0, problems=problems)
                return {"status": "rejected", "rows_upserted": 0, "problems": problems, "report": report.summary()}
            upserted = await load(conn, rows, run_id=run_id, scrape_run_id=f"sqlite:{sha[:12]}")
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=upserted)
            return {"status": "ok", "rows_upserted": upserted, "report": report.summary(), "stats": stats}
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

The quarantined current-month rows are written to `internal.ingest_quarantine` in the same transaction as a rejected run's log row, so a rejection still leaves its evidence; the `ON CONFLICT` upsert is why `ingest` holds UPDATE on this table (a reload after a month closes replaces counts).

Append `"app.pipelines.mp_tick_aggregates"` to the first strict mypy block.

- [ ] **Step 4: Run**

Run: `cd backend && uv run pytest tests/test_mp_tick_aggregates.py -q && uv run mypy && uv run ruff check . ../scripts/ && python ../scripts/check_no_scrapers.py`
Expected: PASS; the guard prints no violations.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipelines/mp_tick_aggregates.py backend/tests/test_mp_tick_aggregates.py backend/pyproject.toml
git commit -m "feat(pipelines): load private MP ice/mixed tick aggregates with month-level future checks"
```

---

### Task 7: Docs, CHANGELOG, final verification

**Files:**
- Modify: `CLAUDE.md`, `DEPLOYMENT.md`, `data/DATABASE_STRUCTURE.md`, `CHANGELOG.md`

- [ ] **Step 1: Update docs**

`CLAUDE.md`, "Database and migrations": replace "`ingest`/`trainer`/`triage_worker` land later." with:

```
`ingest` (Phase 2 data jobs: writes only the tables listed in `backend/db/roles/verify_roles_phase2.sql`) and `trainer` (read-only until Phase 3) are created by `create_roles_phase2.sql`, which also creates schema `internal` (owned by `migrator`; `app` has no access). After every Phase 2 migration the owner runs `grants_phase2.sql` then `verify_roles_phase2.sql`. `triage_worker` lands in Phase 4.
```

Add to "Commands": `` - `uv run python -m app.pipelines.<job>`: Phase 2 data jobs; connect as `ingest` via `INGEST_DATABASE_URL` and refuse without it. `VERIFY_DATABASE_URL=<analyst url> uv run pytest -m db tests/verify`: read-only acceptance checks. ``

`DEPLOYMENT.md`, "Database, roles, and migrations": add a bullet: "Phase 2 roles and schema: `create_roles_phase2.sql` (owner, once), migrations as `migrator`, then `grants_phase2.sql` and `verify_roles_phase2.sql` after each Phase 2 migration. Runbook: `docs/superpowers/plans/2026-09-28-phase2a-foundations.md` Tasks 8–10." Revisions list gains `0004_phase2a_foundation`.

`data/DATABASE_STRUCTURE.md`: add sections for `source_ingest_log` (public), and a short "Schema `internal`" section listing `accidents_raw`, `accident_revisions`, `ingest_quarantine`, `mp_tick_aggregates` with one-line purposes; add `quarantine_reason`/`quarantine_rule_version` to a new `mp_ticks` subsection; note `mp_locations` has no `elevation_ft` column (the table above is stale) — correct that row.

`CHANGELOG.md` under `## [Unreleased]`:

```
### Phase 2a foundations (PR 2a-0) — 2026-09-28

- New `app/pipelines/` package: boundary validation with row quarantine and batch gates (`validate.py`), the packed 0.1° grid key (`grid.py`), the ingest run log (`ingest_log.py`), and an `ingest`-role engine (`INGEST_DATABASE_URL`).
- Migration `0004_phase2a_foundation`: schema `internal`; frozen `internal.accidents_raw`; Phase 2a accident columns with enum checks; `internal.accident_revisions`; `source_ingest_log`; `internal.ingest_quarantine`; `internal.mp_tick_aggregates`; `mp_ticks.quarantine_reason`. Downgrade refuses while revisions, aggregates or run logs exist.
- Roles `ingest` and `trainer` (SQL + SCRAM verifiers only), cumulative `grants_phase2.sql`, exact-privilege `verify_roles_phase2.sql`.
- R8: `mp_ticks` rows flagged `future` (after the day they were captured or after today, whichever is earlier), `orphan_route`, or `pre_1970`; nothing deleted.
- Loader for the privately produced MP ice/mixed tick aggregates: reads a local SQLite export, quarantines future and not-yet-closed months, and is a logged no-op on a repeat load.
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

### Task 8: OWNER/AGENT RUNBOOK — backup, `pre-2a` branch, restore rehearsal

Runs from `/Users/sebastianfrazier/Developer/SafeAscent/backend` on `main` after this plan's PR merges. The agent may run it only when the owner has placed the needed env files; the agent never opens them. No command prints a password.

**Files (gitignored or outside the repo, never committed):** `backend/.env.owner`, `backend/.env.analyst`, `~/Developer/safeascent-private/backups/pre-2a/*.dump`.

- [ ] **Step 1 (owner/agent): Tools and the `split_pg_url` helper**

Use Phase 1 Plan B Task 8 Step 1 verbatim (`psql` 16+ from `libpq`, and the `split_pg_url` shell function). Also `pg_dump --version` must report 16+.

- [ ] **Step 2 (owner): Create Neon branch `pre-2a` from `main`** (Console → Branches → New branch, name `pre-2a`, current data). It is the point-in-time fallback; do not delete it until plan 3 lands.

- [ ] **Step 3 (owner/agent): Dump the three tables the repairs touch**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
mkdir -p ~/Developer/safeascent-private/backups/pre-2a && chmod 700 ~/Developer/safeascent-private/backups/pre-2a
( set -a; . ./.env.owner; set +a
  split_pg_url "$OWNER_DATABASE_URL"
  for t in accidents weather mp_ticks; do
    pg_dump "$PG_URL_NOPASS" -Fc --no-owner --no-privileges -t "public.$t" \
      -f ~/Developer/safeascent-private/backups/pre-2a/$t.dump
  done )
ls -l ~/Developer/safeascent-private/backups/pre-2a
```

Expected: three non-empty `.dump` files. They contain accident narratives and climber names: they stay in the private directory (no remote), never in the repo.

- [ ] **Step 4 (owner/agent): Restore rehearsal** (spec 2a-0 acceptance)

In the Console create a throwaway branch `restore-drill` from `main`. Then:

```bash
BRANCH_HOST='<restore-drill direct host>'
( set -a; . ./.env.owner; set +a
  URL="$(printf '%s' "$OWNER_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  split_pg_url "$URL"
  psql "$PG_URL_NOPASS" -X -q -c "CREATE SCHEMA drill"
  for t in accidents weather mp_ticks; do
    pg_restore --no-owner --no-privileges -d "$PG_URL_NOPASS" --schema-only -t "$t" \
      ~/Developer/safeascent-private/backups/pre-2a/$t.dump -f - \
      | sed "s/public\./drill./g" | psql "$PG_URL_NOPASS" -X -q
    pg_restore --no-owner --no-privileges --data-only -t "$t" \
      ~/Developer/safeascent-private/backups/pre-2a/$t.dump -f - \
      | sed "s/public\./drill./g" | psql "$PG_URL_NOPASS" -X -q
  done
  psql "$PG_URL_NOPASS" -XAt -c "SELECT (SELECT count(*) FROM drill.accidents) = (SELECT count(*) FROM public.accidents),
                                         (SELECT count(*) FROM drill.weather) = (SELECT count(*) FROM public.weather),
                                         (SELECT count(*) FROM drill.mp_ticks) = (SELECT count(*) FROM public.mp_ticks)" )
```

Expected: `t|t|t`. Delete the `restore-drill` branch. Record "restore rehearsed <date>" in the PR thread (no counts of personal data needed).

---

### Task 9: OWNER/AGENT RUNBOOK — create Phase 2 roles, rehearse `0004` on a Neon branch, apply to prod

**Files (gitignored, never committed):** `backend/.env.ingest`, `backend/.env.trainer`, plus the Phase 1 `backend/.env.owner`, `backend/.env.migrator`.

- [ ] **Step 1 (owner): Generate passwords straight into files and write role URLs**

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
umask 077
printf 'INGEST_PASSWORD=%s\n' "$(openssl rand -hex 32)" > .env.ingest
printf 'TRAINER_PASSWORD=%s\n' "$(openssl rand -hex 32)" > .env.trainer
( set -a; . ./.env.owner; . ./.env.ingest; set +a; uv run python -m scripts.write_role_url --role ingest --env-file .env.ingest )
( set -a; . ./.env.owner; . ./.env.trainer; set +a; uv run python -m scripts.write_role_url --role trainer --env-file .env.trainer )
git check-ignore -v .env.ingest .env.trainer
```

Expected: `wrote INGEST_DATABASE_URL to .env.ingest`, `wrote TRAINER_DATABASE_URL to .env.trainer`, and both files matched by `.env.*`.

- [ ] **Step 2 (owner/agent): Rehearse on a Neon branch** (Console: new branch `p2a-0-rehearsal` from `main`, current data; copy its direct host)

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
BRANCH_HOST='<p2a-0-rehearsal direct host>'
( set -a; . ./.env.owner; set +a
  BRANCH_URL="$(printf '%s' "$OWNER_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  split_pg_url "$BRANCH_URL"
  export INGEST_PASSWORD_SCRAM="$( set -a; . ./.env.ingest; uv run python -m scripts.write_role_url --role ingest --scram )"
  export TRAINER_PASSWORD_SCRAM="$( set -a; . ./.env.trainer; uv run python -m scripts.write_role_url --role trainer --scram )"
  psql "$PG_URL_NOPASS" -X -q -f db/roles/create_roles_phase2.sql )
( set -a; . ./.env.migrator; set +a
  export MIGRATOR_DATABASE_URL="$(printf '%s' "$MIGRATOR_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  uv run alembic upgrade head && uv run alembic current && uv run alembic check )
( set -a; . ./.env.owner; set +a
  BRANCH_URL="$(printf '%s' "$OWNER_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  split_pg_url "$BRANCH_URL"
  psql "$PG_URL_NOPASS" -X -q -f db/roles/grants_phase2.sql
  psql "$PG_URL_NOPASS" -X -q -f db/roles/verify_roles.sql
  psql "$PG_URL_NOPASS" -X -q -f db/roles/verify_roles_phase2.sql )
```

Expected: `roles ingest and trainer created; schema internal owned by migrator`; `0004_phase2a_foundation (head)`; `No new upgrade operations detected.`; `phase 2 grants applied`; `ALL ROLE CHECKS PASSED`; `ALL PHASE 2 ROLE CHECKS PASSED`. Any failure stops the rollout; paste only the failing check names to the agent.

- [ ] **Step 3 (owner/agent): Acceptance on the branch as `analyst`**

```bash
( set -a; . ./.env.analyst; set +a
  VERIFY_DATABASE_URL="$(printf '%s' "$ANALYST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#; s#postgresql\+asyncpg:#postgresql:#; s#ssl=#sslmode=#")" \
    uv run pytest -m db tests/verify/test_phase2a_foundation.py -q -k accidents_raw )
```

Expected: `1 passed`. (The R8 checks run after Task 10.) Keep the branch for Task 10's rehearsal.

- [ ] **Step 4 (owner): Apply to prod** — repeat Step 2 without the `sed` host substitution (use `OWNER_DATABASE_URL` and `MIGRATOR_DATABASE_URL` as they are), then Step 3 against prod. Expected outputs are identical.

- [ ] **Step 5 (owner): Store the ingest credential for data workflows** — GitHub → Settings → Secrets and variables → Actions → New repository secret `INGEST_DATABASE_URL`, pasted from `.env.ingest` via an editor (never `cat` in a shared terminal). Do not add it to Railway yet (plan 7).

---

### Task 10: OWNER/AGENT RUNBOOK — run R8 and load the private tick aggregates

- [ ] **Step 1 (owner/agent): Confirm `created_at` is UTC** (D3 assumption)

```bash
( set -a; . ./.env.analyst; set +a
  U="${ANALYST_DATABASE_URL/postgresql+asyncpg:/postgresql:}"; U="${U/ssl=/sslmode=}"
  split_pg_url "$U"
  psql "$PG_URL_NOPASS" -XAt -c "SHOW TimeZone" -c "SELECT min(created_at), max(created_at) FROM mp_ticks" )
```

Expected: `UTC` (or `GMT`/`Etc/UTC`). If not UTC, stop and tell the agent: `QUARANTINE_SQL` must convert with `AT TIME ZONE`.

- [ ] **Step 2 (owner/agent): Run R8 on the rehearsal branch, then prod**

```bash
( set -a; . ./.env.ingest; set +a
  export INGEST_DATABASE_URL="$(printf '%s' "$INGEST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.mp_ticks_quarantine )
```

Expected: one JSON line, e.g. `{"counts": {"clean": …, "future": …, "orphan_route": …, "pre_1970": …}, "rule_version": "r8-v1"}`. Compare to the audit (1,322 future at the 2026-02-08 cutoff; 3,602 orphan route ids): `future` should be ≥ 57 (the rows dated after 2026-09-28). Record the counts in the PR. Re-run: identical counts. Then run without the `sed` for prod, and `VERIFY_DATABASE_URL=… uv run pytest -m db tests/verify/test_phase2a_foundation.py -q` → `3 passed`.

- [ ] **Step 3 (owner): Load the tick aggregates, only after the private scrape reports complete**

In `~/Developer/safeascent-private/mp_ticks`: `./status.sh` must show all routes done; stop the scraper (`pkill -TERM -f mp_ticks.run`) so the WAL checkpoints. Then from the repo:

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
( set -a; . ./.env.ingest; set +a
  export INGEST_DATABASE_URL="$(printf '%s' "$INGEST_DATABASE_URL" | sed -E "s#@[^/]+/#@${BRANCH_HOST}/#")"
  DATABASE_URL="$INGEST_DATABASE_URL" uv run python -m app.pipelines.mp_tick_aggregates \
    --sqlite ~/Developer/safeascent-private/mp_ticks/mp_ice_ticks.sqlite --dry-run )
```

Expected: `"status": "dry_run"` with a report of counts only. If `problems` is non-empty, stop and review the quarantine reasons with the agent (counts only). Then run without `--dry-run` (branch, then prod without `sed`): `"status": "ok"`; a second run prints `"status": "noop"`. Delete the `p2a-0-rehearsal` branch.

- [ ] **Step 4 (owner): Schedule the reload** — after each month closes (D3), re-run Step 3 on prod so `month_not_closed` rows land. Removal (legal Q1): `DROP TABLE internal.mp_tick_aggregates` via a new migration, then plan 8's removal drill.

---

## Self-review (done while writing)

- Spec coverage for this plan's scope: 2a-0 (backup, `accidents_raw`, revisions table, new columns; restore rehearsed — Tasks 2, 8), R8 (Task 5, with the frozen date replaced per owner rule), P2-14 load (Task 6), `ingest` role via SQL (Task 4), `source_ingest_log` (Task 2/3), validation (Task 1). Everything else is mapped to plans 2–8 in the split table.
- Placeholders: none; runbook angle-bracket values are hosts the owner copies from the Console, as in Phase 1 Plan B.
- Type consistency: `ValidationReport.summary()` is the only report serializer; `finish_run(..., report=...)` everywhere; `grid_bucket_sql` spelled identically in the ledger.
- Review Focus items each map to a named test in Tasks 1, 2, 5, 6.
