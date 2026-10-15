# Exposure Index on the MP Catalog (PRs X-1, X-2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision 1 (2026-10-15):** this plan replaces the exposure half (PR 2b-4) of `docs/superpowers/plans/2026-09-28-phase2b-exposure-compaction.md`, which targeted an OpenBeta catalog that no longer exists. That plan's compaction half (PR 2b-5) is not carried over here `[default pending: DP11]`. Design calls are marked `[default pending: DPn]`, each with the recommended default the plan is written against. The owner can overrule any of them before execution starts.

**Goal:** Load the private rock tick aggregates into prod through the existing tick seam, using a local, file-only loader (X-1). Then build `internal.exposure_index` (X-2): one row per MP area × type group × month, holding raw exposure components with missing flags and per-cell tick coverage, computed as of a data cutoff. Phase 3 can then build leak-free training snapshots and tell low traffic apart from no data.

**Architecture:**
- **X-1: the loader.** The committed loader `app/pipelines/mp_tick_aggregates.py` gains `--scope rock`. It reads one aggregate-only SQLite export, which the owner produces privately outside the repo. It refuses a raw scrape DB and any path inside the repository, and it writes only `internal.mp_tick_aggregates` and the seam `internal.route_tick_scrapes`. Routes whose seam row is already as new are skipped, so loading the same cumulative export twice is a no-op.
- **X-2: the exposure job.** The job is three modules:
  - `exposure_math.py`: pure rules and arithmetic.
  - `exposure_components.py`: one set-based SQL reader per component.
  - `exposure.py`: assembly, snapshots, validation and CLI.
- **How the job reads its inputs:**
  - Ticks come only through `internal.route_tick_coverage` and `internal.route_tick_months`. A route is `missing`, `scraped` or `complete as of`, so an unscraped route is never a zero.
  - Each cell stores its tick coverage (`n_complete / n_routes`), both in the proxy and in a `tick_coverage` column.
- **Where it runs.** Both jobs run from the owner's Mac as `ingest` through the runbook helpers `[default pending: DP8]`. Nothing new runs on GitHub Actions or Railway.

**Tech Stack:** Python 3.12, SQLAlchemy async + asyncpg, Alembic, Postgres (window functions over `cell_daily_conditions`), numpy, sqlite3 (stdlib), pytest.

**Spec:**
- `docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md`: §Exposure (P2-9), §MP ice/mixed tick aggregates (P2-14), P2-15 (no scrapers), milestone 2b-4.
- `docs/superpowers/specs/2026-09-27-phase3-model-v2-design.md`: P3:59 (proxy is a covariate vector), P3:137 (`ml/exposure.py`), P3:152 (`exposure_index` shape), M5 (P3:243–257), P3:398 (leakage controls).
- `docs/superpowers/specs/2026-09-28-phase3-amendment-similarity-confidence.md`: §1B, ticks are a partial sample, not exposure.
- Foundation code this plan builds on, read 2026-10-15 on `feat/mp-catalog` HEAD `32d331d`:
  - migrations `0008_mp_catalog`, `0009_static_features`, `0010_accident_route_links`;
  - `app/models/{catalog,features,links}.py`;
  - `app/pipelines/{mp_catalog,mp_tick_aggregates,static_features,catalog_cycle}.py`;
  - `app/data/repair/legacy_links.py`.
- Owner decisions: the SDD ledger `SafeAscent/.superpowers/sdd/2026-10-12-mp-catalog-similarity/progress.md`, plus the owner memory of 2026-10-12/15.

**Prerequisites:**
- The MP catalog plan (`2026-10-12-mp-catalog-similarity.md`) is merged, and its Task 6 runbook has put prod at `0010_accident_route_links` with the catalog refreshed.
- X-2's `season_share` needs C-2's points (`feature_points.tz`) and their ERA5 series. Until a Professional window has filled route-point series, `season_share` reads `insufficient_history` and `exposure_point` is NULL: honest, never floored (Review Focus 5).
- X-1 needs only `0008`, so it can ship and start loading first.

**Two PRs:**
- **X-1** `feat/exposure-rock-ticks` = Tasks 1–2 + runbook Task 3. No migration.
- **X-2** `feat/exposure-index` = Tasks 4–9 + runbook Task 10. Migration `0011_exposure_index`.

## Migration numbering

- This plan takes `0011_exposure_index` (`down_revision = "0010_accident_route_links"`), tested by `test_migration_0011.py`.
- **Phase 4 renumbers after it.** Its `0008_accounts`/`0009_reports`/`0010_report_promotions` (still at those numbers in `2026-10-12-phase4-users-incident-reporting.md`) become `0012_accounts` (`down_revision = "0011_exposure_index"`), `0013_reports` and `0014_report_promotions`. The renumbering also covers:
  - its `test_migration_0008/9/10.py`, which become `0012/13/14`;
  - its `migrated_db(...)` pins and downgrade targets;
  - its runbook's expected `MIG current` lines.

  Task 9 Step 4 edits that plan document.
- The 2026-09-28 objectives plan also claims `0011`/`0012`. It is an OpenBeta-era plan due for its own re-plan, so it is not edited here; its re-plan numbers after Phase 4.
- Before writing `0011`, the implementer runs `cd backend && uv run alembic heads` and confirms the single head is `0010_accident_route_links`.

## Global Constraints

The foundations plan's Global Constraints and the MP catalog plan's Global Constraints apply. Also:
- **Missing is never 0, and code never invents a number.**
  - A component that is not known is `{"value": null, "missing": true, "reason": …}`. A measured zero (complete routes with no ticks that month, a month with no climbable days) is stored as 0 and stays distinguishable.
  - No floor, default, extrapolation or scaling stands in for a missing input. Tick sums are never scaled up by coverage.
  - `Component.present` refuses a non-finite value.
- **Ticks are read only through the seam views.** `internal.route_tick_coverage` gives the route's status and `internal.route_tick_months` gives its counts. The exposure code never reads `internal.mp_tick_aggregates` directly. A month counts only for a `complete` route and only up to its `complete_through`.
- **Coverage is always stored.** Every `mp_ticks` component carries `coverage`, `n_complete`, `n_scraped` and `n_missing`. The row also carries `tick_coverage` (NULL only when the MP components are disabled or removed), so Phase 3 can tell low traffic from no data.
- **Data cutoff.** Tick months are counted only up to the last complete month strictly before `data_cutoff` (P3:398). `route_density` is the current catalog and `season_share` is the latest 3 ERA5 years; both are documented as time-invariant covariates `[default pending: DP4]`.
- **The scraper-code rule holds.**
  - No scraper code and no scrape output is committed: no file name matching `scrape*`/`*scraper*`, no banned host in source (`scripts/check_no_scrapers.py`), and no MP ids below 900000000 in `data/`, `docs/`, `backend/tests/` (`scripts/check_no_mp_data.py`).
  - The loader reads only an exported aggregate file. It imports no network client (`test_loader_imports_no_network_client`), refuses an export inside the repository, and refuses an export holding any table but the two aggregate tables.
- **Least privilege.**
  - Grants change only in the Phase 2 three-file lockstep: `backend/db/roles/grants_phase2.sql`, `verify_roles_phase2.sql`, and `backend/tests/test_roles_phase2.py`, all green together. `create_roles_phase2.sql` is untouched.
  - `exposure_index` lives in schema `internal` `[default pending: DP1]`. As a result, `app` holds nothing on it by construction, `analyst` reads it through the schema-wide SELECT, and `trainer` gets nothing until Phase 3 (D13).
  - No API endpoint reads it; a test greps `app/api/`.
- **Parameterised SQL only.** Values travel as bound parameters, and arrays go through `CAST(:x AS bigint[])`. The only f-strings in SQL interpolate module constants. There is no DDL in `app/` (`tests/test_no_runtime_ddl.py`).
- **Fixtures** use synthetic MP ids ≥ 900000000 and `Fixture …` names. Tests never read a real export.
- **No paid or external call** is made by anything in this plan. The loader and the exposure job read local files and the database only. If a later component needs an external API, it comes behind a gate in its own plan (Appendix B).
- **Runbooks** follow the plan-2 safety structure:
  - a Neon branch rehearsal first;
  - a snapshot branch before the first prod write;
  - a STOP for the owner before prod;
  - an explicit `TARGET_HOST`;
  - `save_once` for every piece of evidence a later gate compares;
  - `gate_pass`/`gate_require` around every prod write.
- Every new module joins the strict mypy allowlist in `backend/pyproject.toml`. `app.models.exposure` joins the models block.
- `.env.example` mirrors `Settings` (`tests/test_env_example_parity.py`).

## Decisions

**Owner decisions (binding), recorded in the SDD ledger and memory:**
1. **MP is the primary source, and OpenBeta is dropped.**
   - OpenBeta ticks measured about 0.2% of MP's volume, so `ob_ticks` is removed from the proxy entirely, not marked absent.
   - The legacy `mp_tick_count` (CA/NV `public.mp_ticks`, capped at 16) is superseded by the seam's rock ticks and is removed too.
2. **Ice/mixed ticks are complete in prod** (`internal.mp_tick_aggregates`, seam backfilled by `0008`).
3. **Rock ticks.**
   - They arrive gradually from the private scraper at 60 s per request: about 163K US roped routes over roughly 140 days, with routes near accidents scraped first.
   - Unscraped routes are MISSING, never zero. Exposure shows coverage per area and month.
   - Scraped data lives in SQLite on the owner's Mac and never goes to GitHub. A local loader pushes aggregates into prod through the seam as `ingest`, reading only an exported aggregate file.
4. **Areas are MP areas** (`canonical_areas`, ltree `path`). Type groups are `sport`, `trad`, `rock`, `alpine`, `ice`, `mixed`, `unknown`. `unknown` is never scorable (catalog D-E), so it never forms a cell.
5. **Keep the P2-9 components that still make sense**, each with its source, licence and access stated, and defer any whose source is unverified (Appendix B).
6. Migrations start at `0011`, and Phase 4 renumbers after this plan.

**Decisions this plan makes. Each DP has a recommended default; the plan is written against the default.**
- **D-1 Components in the proxy** (`PROXY_VERSION = "x-mp-v1"`) are `route_density`, `season_share` and `mp_ticks`.
  - `nps_visits`, `permit_attempts`, `objective_popularity` and `in_app_ticks` are deferred and omitted from the proxy until their source lands. Each one that lands bumps `PROXY_VERSION` (Appendix B) `[default pending: DP6]`.
  - The alternative to omitting them is to carry them now as permanently missing keys. Phase 3 can then fit nothing for them, but the key set would stay stable.
- **D-2 Cells: one row per (area, type group, month) for every area with at least one scorable route attached directly** (`scorable_routes.area_id`). `n_routes` counts those routes only, so cells are disjoint and Phase 3 sums up the `path` hierarchy itself `[default pending: DP5]`.
  - Alternative: subtree rollups per ancestor area. That double-counts across levels, and Phase 3's EB already does the hierarchy.
- **D-3 Rock tick coverage** `[default pending: DP2]`.
  - The `mp_ticks` value is the sum over the cell's `complete` routes of each route's mean ticks per year in that calendar month. It is stored raw, never scaled to `n_routes`.
  - Beside it, `per_route_mean` (the value ÷ `n_complete`), `coverage = n_complete / n_routes`, `n_complete`, `n_scraped` and `n_missing` are stored.
  - It is present when `n_complete ≥ 1`. Otherwise it is missing (`no_complete_routes`) with `coverage: 0`.
  - Alternatives:
    - (b) present only at `coverage = 1`. Most rock cells would stay missing for ~140 days.
    - (c) extrapolate the value × `n_routes / n_complete`. Rejected: it invents numbers.
- **D-4 Tick window** `[default pending: DP3]`.
  - Each complete route contributes the 60 months ending at its last complete month that is both ≤ `complete_through` and strictly before `data_cutoff`. Its per-month mean is the sum over that month's 5 occurrences ÷ 5.
  - The window is the same length for every route, so routes are comparable and backtests at older cutoffs still get 5 years.
  - Alternative: all history. Older MP years are thinner, which biases old routes low.
- **D-5 Season share** is a time-invariant climate covariate `[default pending: DP4]`.
  - Source: `cell_daily_conditions` ERA5 rows (`record_kind = 'era5'`) of the area's `(grid_bucket, tz)` series over `[today − 3×365 d, today)`. That is the 3-year window `era5_window` keeps for non-incident series; older rows are pruned, so a strict cutoff would leave every backtest older than 3 years without it.
  - Rules: rock (sport, trad, rock) day = tmax 5–32 °C and precip < 2 mm. Ice/mixed day = tmax < 0 °C on ≥ 7 of the 10 days ending that day. Alpine day = either (spec "both windows", read as the union, as in the 2026-09-28 plan).
  - Unknown days (a NULL input, or a missing day in the 10-day window that could change the answer) are excluded, never counted false.
  - A calendar month with fewer than 28 known days is missing (`insufficient_history`).
- **D-6 Unfitted point and bounds.**
  - `exposure_point = route_density × season_share`, NULL when either is missing (no floor).
  - `exposure_lo`/`exposure_hi` = point ÷/× 3. The ×/÷1.5 permit case returns with `permit_attempts`.
  - Phase 3 fits the real combination (M5). This number only anchors the bounds.
- **D-7 One tick component.** `mp_ticks` covers every type group: rock groups from the rock scrape, ice/mixed from the ice scrape, all through the same seam with the same semantics. One flag, `EXPOSURE_ENABLE_MP_TICKS` (default true), removes it `[default pending: DP7]`.
  - Alternative: the spec's two keys (`mp_tick_count` for rock, `mp_ice_mixed_ticks`), each missing `not_applicable_type_group` on the other half of the rows.
- **D-8 Rock loader scope.** `--scope rock` accepts exactly the routes the ice scope rejects: every `mp_routes` row whose type does not match `is_ice_mixed`. Ice and mixed rows in a rock export are quarantined `ice_mixed_route`, so each route has one owning export. Boulders never enter the rock scrape (owner, 2026-10-12); one that does loads harmlessly, since the seam is per route and exposure reads only scorable routes.
- **D-9 Aggregate-only export** (rock scope).
  - The file may hold only `route_tick_totals` (`mp_route_id`, `total_ticks`, `complete`, `scraped_at` required; `last_page` and `pages_fetched` allowed) and `route_tick_monthly` (`mp_route_id`, `year_month`, `style`, `n`). Any other table, view or column makes the loader refuse before it reads a row.
  - The ice scope keeps its current behaviour, since its existing export may hold scraper bookkeeping tables.
  - An export path inside the repository is refused (both scopes).
- **D-10 Incremental loading.** The rock scope reads the totals first. It skips every route whose seam row already has a `scraped_at` at least as new, then reads monthly rows only for the rest. A cumulative export can therefore be reloaded every time, and an unchanged one is `noop` without a log row. `existing_counts` uses `= ANY(CAST(:ids AS bigint[]))`, so 163K routes never hit asyncpg's 32,767-parameter limit.
- **D-11 Snapshots.** Snapshots are keyed `(as_of, data_cutoff)`.
  - Production snapshots (`data_cutoff = as_of`): the newest 3 are kept.
  - Backtest snapshots (`data_cutoff < as_of`) are kept until dropped with `exposure drop`.
  - At roughly 25K route-bearing areas × ~1.5 groups × 12 months ≈ 450K rows of ~250 B, the estimate is about 120 MB a snapshot and about 360 MB for three (about $0.13/month on Neon storage). This is an estimate; measure it in Task 10.
- **D-12 Where it runs** `[default pending: DP8]`.
  - The rock loader and `exposure build` run from the owner's Mac as `ingest` through `scripts/runbook_helpers.sh`, against the direct Neon host. The loader must run there because the export never leaves the Mac. Exposure follows each load, plus a monthly rebuild.
  - Nothing goes to GitHub Actions (that would put the `ingest` credential in an Actions secret), and nothing is added to the `catalog-cycle` cron (yearly).
  - Alternative: an `exposure` step at the end of `catalog_cycle`, which needs the same credential it already holds. It runs only yearly unless the schedule changes.
- **D-13 Credential** `[default pending: DP9]`. The loader and the job use the existing `ingest` role, which the Mac runbooks already use and which already holds every grant they need, except the new `internal.exposure_index` grant.
  - Alternative: a new `tick_loader` role holding only INSERT on `internal.mp_tick_aggregates`, the seam upsert, the run log and quarantine. That changes `create_roles_phase2.sql` and needs a new owner password step.
- **D-14 Load cadence and prod snapshots** `[default pending: DP10]`.
  - The owner exports weekly.
  - The first prod load is rehearsed on a branch and preceded by a `pre-xp-1` snapshot.
  - Each later load is gated by a prod dry run (quarantine share ≤ 10%, no batch problems) and preceded by its own dated snapshot branch, which the owner deletes when satisfied.
  - The branch rehearsal is repeated only when the loader code changes.
- **D-15 Validation** (reported, never tuned to).
  - The Spearman correlation of `exposure_point` against `mp_ticks` over full-coverage cells.
  - Counts: cells, NULL points, full-coverage cells, mean tick coverage.
  - The permit Spearman returns with `permit_attempts`.
  - These metrics join `ingest_log._ALLOWED_METRICS` in a reviewed change (G13); there is no raw jsonb UPDATE of the run log.

## Conflicts with the Phase 3 spec (for the owner and the Phase 3 re-plan)

1. **P3:152 table shape.**
   - **Location.** The table is `internal.exposure_index`, not public `exposure_index` `[default pending: DP1]`.
   - **Primary key.** The PK adds `data_cutoff`, which P3:398 backtests need.
   - **Area keys.** `area_id` is the MP `bigint`, not an OpenBeta uuid.
   - **New columns.** `tick_coverage`, `exposure_lo`/`exposure_hi` and `run_id` are added.
   - **Why internal.** The Phase 1 check `verify_roles.sql` requires `app` to SELECT every public table, so the 2026-09-28 plan's REVOKE on a public table would fail it.
   - **What Phase 3 must grant.** `trainer` needs USAGE on `internal` plus SELECT on that one table. That changes the `trainer has no USAGE on schema internal` row of `verify_roles_phase2.sql`.
2. **P3 M10 has five type groups. Cells here have six,** because `rock` (sport-or-trad, discipline unknown; owner 2026-10-13) holds about 63% of routes. Phase 3's cells, percentiles, bands and thresholds need a `rock` group or a rule for pooling it into `sport`/`trad`.
3. **P3 M5 component list.** `OpenBeta ticks` is gone. `mp_tick_count` (CA/NV, capped at 16) and `mp_ice_mixed_ticks` are replaced by one seam-based `mp_ticks` with coverage `[default pending: DP7]`. NPS visits, permits and objective popularity are deferred. MVP-1 fits on `route_density`, `season_share` and `mp_ticks` only.
4. **M5 calls the MP components "optional, removable".** `mp_ticks` is now the main traffic signal, not a side covariate. The model card's with/without-MP comparison will show a much larger gap, and the removal drill leaves only density × season.
5. **Coverage is informative, not random** (MNAR). Routes near accidents are scraped first, so partially covered cells over-represent accident areas. `ml/exposure.py` (P3:137) must take `tick_coverage`/`n_complete` as covariates or use only full-coverage cells. It must not read a partial sum as low traffic `[default pending: DP2]`.
6. **P3:398 leakage.**
   - `season_share` is treated as time-invariant `[default pending: DP4]`.
   - Tick months respect the cutoff, but MP tick dates are climb dates. Counts are frozen at first scrape, so a tick logged after the cutoff for a climb before it is counted. That is unavoidable with aggregate exports, and is documented in `DATABASE_STRUCTURE.md`.
7. **Objective-level scoring (P3:163–169)** needs exposure rows for objective areas. Those rows wait for the objectives re-plan.

## Review Focus

1. **A trad cell of 3 routes where 1 route is complete, 1 is scraped and 1 was never scraped.** Expect `mp_ticks` present with only the complete route's ticks, `coverage` 1/3, `n_complete` 1, `n_scraped` 1, `n_missing` 1, and `tick_coverage` 0.333 on the row. The value is never scaled to 3 routes, and a zero is never filled in for the other two (Task 7 `test_partial_coverage_is_raw_and_counted`).
2. **A cell whose routes are all `scraped` or `missing`.** Expect `{"value": null, "missing": true, "reason": "no_complete_routes", "coverage": 0.0, …}`, never `present(0)` (Task 7 `test_no_complete_route_is_missing_not_zero`).
3. **A rock export that contains an ice route, a raw-ticks table, or that lives under the repo root.** Expect the ice route quarantined `ice_mixed_route`, and a refusal before any row is read for the other two (Task 1 tests).
4. **Loading the same cumulative export twice, then an older one.** Expect the second load `noop` with `routes_unchanged`, and the older one never rolling the seam back (Task 2 `test_rock_reload_is_a_noop_and_older_never_rolls_back`).
5. **An area whose ERA5 series is empty, or a month with a NULL tmax inside an ice window.** Expect `season_share` missing `insufficient_history` and `exposure_point`/bounds NULL, never a floored number. The ice day is unknown, not false (Task 5 `test_unknown_days_are_not_false`, Task 6 `test_season_sql_matches_python_rules`).
6. **A backtest cutoff in the middle of a month.** Expect that month and later months excluded from ticks, and a route's months after `complete_through` never read (Task 7 `test_cutoff_and_complete_through_bound_the_window`).
7. **The build with `EXPOSURE_ENABLE_MP_TICKS=false`.** Expect `mp_ticks` missing `disabled`, `tick_coverage` NULL, and the `route_density`/`season_share` digests identical to the enabled run (Task 8 `test_removal_drill_changes_only_mp_ticks`).

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/app/pipelines/mp_tick_aggregates.py` | Modify | `--scope rock`, export location and aggregate-only checks, seam-based skipping, `ANY(array)` lookups. |
| `backend/tests/test_mp_tick_aggregates.py` | Modify | Rock scope tests. |
| `backend/tests/verify/test_tick_seam.py` | Create | `-m db` seam cells (sums agree, scope ownership). |
| `backend/alembic/versions/0011_exposure_index.py` | Create | `internal.exposure_index`. |
| `backend/app/models/exposure.py`, `backend/app/models/__init__.py` | Create / Modify | `ExposureIndex` model. |
| `backend/app/pipelines/exposure_math.py` | Create | `Component`, day rules, Spearman, point, bounds, `tick_component` (pure). |
| `backend/app/pipelines/exposure_components.py` | Create | `scope`, `route_density`, `season`, `mp_ticks` readers. |
| `backend/app/pipelines/exposure.py` | Create | `build`, `validate`, `drop_snapshot`, CLI. |
| `backend/app/pipelines/ingest_log.py` | Modify | Exposure metrics join `_ALLOWED_METRICS`. |
| `backend/app/config.py`, `.env.example` | Modify | `EXPOSURE_ENABLE_MP_TICKS`. |
| `backend/db/roles/grants_phase2.sql`, `verify_roles_phase2.sql`, `backend/tests/test_roles_phase2.py` | Modify | Lockstep grants for `internal.exposure_index`. |
| tests: `test_migration_0011.py`, `test_exposure_math.py`, `test_exposure_components.py`, `test_exposure.py`, `test_no_exposure_in_api.py`; `tests/verify/test_exposure.py` | Create | Tests. |
| `backend/pyproject.toml` | Modify | mypy allowlists. |
| `CHANGELOG.md`, `CLAUDE.md`, `data/DATABASE_STRUCTURE.md`, `docs/superpowers/plans/2026-10-12-phase4-users-incident-reporting.md` | Modify | Docs; Phase 4 renumbering. |

## Pre-flight conflict ledger

| Task A | Task B | Shared file / interface | Resolution |
|---|---|---|---|
| 1 | 2 | `mp_tick_aggregates.Scope`, `read_export(path, routes=None)`, `validate(..., scope=)`, `check_export_location`, `check_aggregate_only` | Frozen in Task 1. |
| 2 | 3, ice runbooks | `main(path, *, today, max_quarantine_share, dry_run, scope="ice_mixed")`, CLI `--scope` | The default keeps the ice path byte-for-byte compatible; the existing ice tests stay green. |
| 4 | 6–10 | `internal.exposure_index` columns and PK | Task 4 first. |
| 5 | 6–8 | `Component`, `tick_component`, `exposure_point`, `bounds`, `spearman`, `RULE_BY_GROUP`, constants | Pure; frozen in Task 5. |
| 6, 7 | 8 | `Key`, `Scope`, `scope(conn)`, `keys(scope_)`, `route_density(scope_)`, `season(conn, scope_, *, today)`, `mp_ticks(conn, scope_, *, enabled, cutoff)` | Frozen in Tasks 6–7. |
| 8 | 10, Phase 3 | `build(conn, *, today, cutoff=None, mp_enabled, run_id=None)`, `validate(conn, *, as_of, data_cutoff)`, CLI | Frozen in Task 8. |
| 4 | Phase 4, objectives re-plan | Alembic chain | Phase 4 → `0012`–`0014` (Task 9 Step 4). |
| 4 | Phase 3 | `trainer` grant on `internal.exposure_index` | Phase 3's role change, not here (D13). |
| 2, 8 | `ingest_log._ALLOWED_METRICS` | Allowlisted metric names | Task 8 appends; nothing else changes. |

---

# PR X-1 — `feat/exposure-rock-ticks`

### Task 1: Rock scope, export-location and aggregate-only checks (pure and SQLite side)

**Files:**
- Modify: `backend/app/pipelines/mp_tick_aggregates.py`, `backend/tests/test_mp_tick_aggregates.py`

**Interfaces:**
- Consumes: the existing `RouteTotal`, `MonthlyRow`, `validate`, `read_export`, `is_ice_mixed`.
- Produces:
  - `Scope = Literal["ice_mixed", "rock"]`, `SCOPES: tuple[Scope, ...] = ("ice_mixed", "rock")`;
  - `SOURCE_BY_SCOPE = {"ice_mixed": "mp_tick_aggregates", "rock": "mp_tick_aggregates_rock"}`;
  - `EXPORT_COLUMNS: dict[str, frozenset[str]]`, `REQUIRED_COLUMNS: dict[str, frozenset[str]]`, `REPO_ROOT: Path`;
  - `check_export_location(path: Path, repo_root: Path = REPO_ROOT) -> None` (raises `SystemExit`);
  - `check_aggregate_only(path: Path) -> None` (raises `SystemExit`);
  - `read_export(path: Path, routes: set[int] | None = None) -> tuple[list[RouteTotal], list[MonthlyRow]]` (monthly rows filtered to `routes` when given);
  - `validate(totals, monthly, *, route_types, existing, today, scope: Scope = "ice_mixed")`, where the rock scope quarantines `ice_mixed_route` instead of `not_ice_mixed`.

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_mp_tick_aggregates.py`:

```python
from app.pipelines.mp_tick_aggregates import check_aggregate_only, check_export_location

ROCK = {R1: "Ice", R4: "Trad"}


def test_rock_scope_quarantines_ice_routes_and_accepts_rock(tmp_path):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3), (R4, "2025-01", "lead", 2)], [(R1, 3, 1), (R4, 2, 1)])
    totals, monthly = read_export(path)
    rows, report, _ = validate(totals, monthly, route_types=ROCK, existing={}, today=TODAY, scope="rock")
    assert report.quarantined == {"ice_mixed_route": 2}
    assert _keys(rows) == {(R4, "2025-01", "lead", 2), (R4, "total", "all", 2)}


def test_ice_scope_is_unchanged_by_the_scope_parameter(tmp_path):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3), (R4, "2025-01", "lead", 2)], [(R1, 3, 1), (R4, 2, 1)])
    totals, monthly = read_export(path)
    rows, report, _ = validate(totals, monthly, route_types=ROCK, existing={}, today=TODAY)
    assert report.quarantined == {"not_ice_mixed": 2}
    assert _keys(rows) == {(R1, "2025-01", "lead", 3), (R1, "total", "all", 3)}


def test_read_export_filters_monthly_rows_to_the_requested_routes(tmp_path):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3), (R4, "2025-02", "tr", 1)], [(R1, 3, 1), (R4, 1, 1)])
    totals, monthly = read_export(path, routes={R4})
    assert {t.mp_route_id for t in totals} == {R1, R4}
    assert [(m.mp_route_id, m.year_month) for m in monthly] == [(R4, "2025-02")]
    assert read_export(path, routes=set())[1] == []


def test_an_export_inside_the_repository_is_refused(tmp_path):
    inside = tmp_path / "repo" / "data" / "ticks.sqlite"
    inside.parent.mkdir(parents=True)
    with pytest.raises(SystemExit, match="inside the repository"):
        check_export_location(inside, repo_root=tmp_path / "repo")
    check_export_location(tmp_path / "private" / "ticks.sqlite", repo_root=tmp_path / "repo")


def test_the_real_repo_root_is_the_checkout_root():
    assert (loader.REPO_ROOT / "backend" / "app" / "pipelines" / "mp_tick_aggregates.py").is_file()


def test_a_raw_table_or_extra_column_is_refused_before_any_row_is_read(tmp_path):
    raw = _export(tmp_path, [(R4, "2025-01", "lead", 2)], [(R4, 2, 1)], name="raw.sqlite")
    db = sqlite3.connect(raw)
    db.execute("CREATE TABLE tick_rows (mp_route_id INTEGER, climber TEXT)")
    db.commit()
    db.close()
    with pytest.raises(SystemExit, match="tick_rows"):
        check_aggregate_only(raw)
    wide = _export(tmp_path, [(R4, "2025-01", "lead", 2)], [(R4, 2, 1)], name="wide.sqlite")
    db = sqlite3.connect(wide)
    db.execute("ALTER TABLE route_tick_monthly ADD COLUMN note TEXT")
    db.commit()
    db.close()
    with pytest.raises(SystemExit, match="route_tick_monthly.note"):
        check_aggregate_only(wide)
    check_aggregate_only(_export(tmp_path, [(R4, "2025-01", "lead", 2)], [(R4, 2, 1)], name="ok.sqlite"))


def test_a_missing_required_column_is_refused(tmp_path):
    path = tmp_path / "thin.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE route_tick_totals (mp_route_id INTEGER PRIMARY KEY, total_ticks INTEGER NOT NULL);"
        "CREATE TABLE route_tick_monthly (mp_route_id INTEGER, year_month TEXT, style TEXT, n INTEGER);")
    db.close()
    with pytest.raises(SystemExit, match="route_tick_totals lacks"):
        check_aggregate_only(path)
```

(`_export` builds `route_tick_totals` with `last_page` and `pages_fetched`, which are allowed. `ROCK` maps R1 to an ice type and R4 to a rock type.)

- [ ] **Step 2: Run the tests to confirm they fail.** `cd backend && uv run pytest tests/test_mp_tick_aggregates.py -q -k "rock or repository or raw_table or required_column or filters or repo_root or unchanged"` → FAIL (`ImportError: check_aggregate_only`).

- [ ] **Step 3: Implement.** In `backend/app/pipelines/mp_tick_aggregates.py`:

Add a paragraph to the module docstring:

```text
Rock scope (exposure plan X-1, 2026-10-15): the same loader reads the private rock tick export with
--scope rock. Each route has one owning export: the rock scope takes exactly the routes the ice
scope rejects. A rock export must hold only the two aggregate tables (check_aggregate_only), and
no export may live inside the repository (check_export_location): scraped data never reaches git.
```

Add `from typing import Literal` to the imports, then add after `ICE_MIXED`:

```python
Scope = Literal["ice_mixed", "rock"]
SCOPES: tuple[Scope, ...] = ("ice_mixed", "rock")
SOURCE_BY_SCOPE: dict[str, str] = {"ice_mixed": SOURCE, "rock": "mp_tick_aggregates_rock"}
EXPORT_COLUMNS: dict[str, frozenset[str]] = {
    "route_tick_totals": frozenset({"mp_route_id", "total_ticks", "last_page", "pages_fetched", "complete", "scraped_at"}),
    "route_tick_monthly": frozenset({"mp_route_id", "year_month", "style", "n"}),
}
REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "route_tick_totals": frozenset({"mp_route_id", "total_ticks", "complete", "scraped_at"}),
    "route_tick_monthly": frozenset({"mp_route_id", "year_month", "style", "n"}),
}
REPO_ROOT = Path(__file__).resolve().parents[3]


def check_export_location(path: Path, repo_root: Path = REPO_ROOT) -> None:
    resolved, root = path.resolve(), repo_root.resolve()
    if resolved == root or root in resolved.parents:
        raise SystemExit(f"refusing {path}: tick exports never live inside the repository ({root}); "
                         "keep them in the private directory")


def check_aggregate_only(path: Path) -> None:
    """A raw scrape database (climber rows, page HTML, notes) must never be read by a committed
    loader, so anything beyond the two aggregate tables and their known columns is refused."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        objects = {str(n) for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
                   if not str(n).startswith("sqlite_")}
        extra = sorted(objects - set(EXPORT_COLUMNS))
        if extra:
            raise SystemExit(f"refusing {path}: not an aggregate-only export (unexpected {', '.join(extra)})")
        for table, allowed in EXPORT_COLUMNS.items():
            # table comes from the fixed EXPORT_COLUMNS keys, never from the file.
            cols = {str(r[1]) for r in db.execute(f"PRAGMA table_info({table})")}
            if not cols:
                raise SystemExit(f"refusing {path}: {table} is missing")
            if unknown := sorted(cols - allowed):
                raise SystemExit(f"refusing {path}: unexpected column(s) {', '.join(f'{table}.{c}' for c in unknown)}")
            if lacking := sorted(REQUIRED_COLUMNS[table] - cols):
                raise SystemExit(f"refusing {path}: {table} lacks {', '.join(lacking)}")
    finally:
        db.close()
```

Change `read_export` so that it filters monthly rows while iterating (memory stays bounded by the routes asked for):

```python
def read_export(path: Path, routes: set[int] | None = None) -> tuple[list[RouteTotal], list[MonthlyRow]]:
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            totals = [
                RouteTotal(int(r), int(t), bool(c), datetime.fromisoformat(s))
                for r, t, c, s in db.execute(
                    "SELECT mp_route_id, total_ticks, complete, scraped_at FROM route_tick_totals"
                )
            ]
            monthly = [
                MonthlyRow(int(r), str(ym), str(st), int(n))
                for r, ym, st, n in db.execute("SELECT mp_route_id, year_month, style, n FROM route_tick_monthly")
                if routes is None or int(r) in routes
            ]
        finally:
            db.close()
    except sqlite3.OperationalError as exc:
        ...  # unchanged WAL-mode SystemExit
    return totals, monthly
```

Change `_route_problem` and `validate`:

```python
def _route_problem(
    route_id: int, total: RouteTotal | None, route_types: dict[int, str | None], today: date, scope: Scope
) -> str | None:
    if route_id not in route_types:
        return "unknown_route"
    ice = is_ice_mixed(route_types[route_id])
    if scope == "ice_mixed" and not ice:
        return "not_ice_mixed"
    if scope == "rock" and ice:
        return "ice_mixed_route"
    if total is None or not total.complete:
        return "route_incomplete"
    if total.scraped_at.tzinfo is None:
        return "scrape_time_unzoned"
    if total.scraped_at.astimezone(UTC).date() > today:
        return "scrape_after_today"
    return None
```

`validate(...)` gains the keyword `scope: Scope = "ice_mixed"` and passes it to both `_route_problem` calls. Nothing else in `validate` changes.

- [ ] **Step 4: Run the tests.** `cd backend && uv run pytest tests/test_mp_tick_aggregates.py -q && uv run mypy && uv run ruff check .` → all pass, including every existing ice test.

- [ ] **Step 5: Commit.** `git add backend/app/pipelines/mp_tick_aggregates.py backend/tests/test_mp_tick_aggregates.py && git commit -m "feat(pipelines): rock scope, aggregate-only and in-repo refusals for the tick loader"`

---

### Task 2: Seam-based incremental loading, the `--scope` CLI, seam verify cells, X-1 docs

**Files:**
- Modify: `backend/app/pipelines/mp_tick_aggregates.py`, `backend/tests/test_mp_tick_aggregates.py`, `CLAUDE.md`, `CHANGELOG.md`, `data/DATABASE_STRUCTURE.md`
- Create: `backend/tests/verify/test_tick_seam.py`

**Interfaces:**
- Consumes: Task 1's interfaces; the 0008 seam `internal.route_tick_scrapes`.
- Produces:
  - `existing_counts(conn, route_ids)`, now with `= ANY(CAST(:ids AS bigint[]))`;
  - `async seam_scraped_at(conn, route_ids: set[int]) -> dict[int, datetime]`;
  - `routes_to_load(totals: list[RouteTotal], seam: dict[int, datetime]) -> set[int]`;
  - `async main(path, *, today, max_quarantine_share, dry_run, scope: Scope = "ice_mixed") -> dict[str, object]`. Rock returns `{"status": "noop", "rows_upserted": 0, "routes_unchanged": n}` when nothing is newer. On other paths, `stats["routes_unchanged"]` is set and the log source is `SOURCE_BY_SCOPE[scope]`.
  - CLI `--scope {ice_mixed,rock}` (default `ice_mixed`).

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_mp_tick_aggregates.py`:

```python
from app.pipelines.mp_tick_aggregates import RouteTotal, existing_counts, routes_to_load


def _seed_rock_and_ice() -> str:
    return (
        "INSERT INTO mp_locations (mp_id, name) VALUES (900000100, 'Fixture Area');"
        f"INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES ({R1}, 'Fixture Ice', 900000100, 'Ice'),"
        f" ({R4}, 'Fixture Rock', 900000100, 'Trad');"
    )


def test_routes_to_load_skips_routes_whose_seam_is_as_new():
    at = datetime.fromisoformat(SCRAPED)
    newer = datetime.fromisoformat("2026-09-25T00:00:00+00:00")
    totals = [RouteTotal(R1, 1, True, at), RouteTotal(R4, 1, True, newer), RouteTotal(R2, 1, True, at)]
    assert routes_to_load(totals, {R1: at, R4: at}) == {R4, R2}


@requires_pg
def test_rock_scope_loads_rock_and_writes_the_seam(tmp_path, monkeypatch):
    path = _export(tmp_path, [(R1, "2025-01", "lead", 3), (R4, "2025-01", "lead", 2), (R4, "2025-03", "tr", 1)],
                   [(R1, 3, 1), (R4, 3, 1)])
    with migrated_db(seed_sql=_seed_rock_and_ice()) as name:
        url = sa_url(name)
        monkeypatch.setattr(loader, "ingest_engine", lambda: create_async_engine(url))
        out = asyncio.run(main(path, today=TODAY, max_quarantine_share=1.0, dry_run=False, scope="rock"))
        assert out["status"] == "ok" and out["report"]["quarantined"] == {"ice_mixed_route": 2}
        scrapes, months = asyncio.run(_seam_state(url))
        assert [(r, c, t) for r, _, c, _, t in scrapes] == [(R4, True, 3)]
        assert months == [(R4, "2025-01", 2), (R4, "2025-03", 1)]
        log = fetch(name, "SELECT source, status FROM source_ingest_log ORDER BY started_at")
        assert log == [("mp_tick_aggregates_rock", "ok")]


@requires_pg
def test_rock_reload_is_a_noop_and_older_never_rolls_back(tmp_path, monkeypatch):
    first = _export(tmp_path, [(R4, "2025-01", "lead", 2)], [(R4, 2, 1)], name="first.sqlite")
    older = _export(tmp_path, [(R4, "2025-01", "lead", 2)], [(R4, 2, 1)],
                    scraped_at="2026-08-01T00:00:00+00:00", name="older.sqlite")
    with migrated_db(seed_sql=_seed_rock_and_ice()) as name:
        url = sa_url(name)
        monkeypatch.setattr(loader, "ingest_engine", lambda: create_async_engine(url))
        assert asyncio.run(main(first, today=TODAY, max_quarantine_share=1.0, dry_run=False, scope="rock"))["status"] == "ok"
        again = asyncio.run(main(first, today=TODAY, max_quarantine_share=1.0, dry_run=False, scope="rock"))
        assert again == {"status": "noop", "rows_upserted": 0, "routes_unchanged": 1}
        assert asyncio.run(main(older, today=TODAY, max_quarantine_share=1.0, dry_run=False, scope="rock"))["status"] == "noop"
        assert asyncio.run(_seam_state(url))[0][0][3] == date(2026, 8, 31)
        assert fetch(name, "SELECT count(*) FROM source_ingest_log") == [(1,)]


@requires_pg
def test_rock_scope_refuses_a_raw_export_before_touching_the_database(tmp_path, monkeypatch):
    raw = _export(tmp_path, [(R4, "2025-01", "lead", 2)], [(R4, 2, 1)])
    db = sqlite3.connect(raw)
    db.execute("CREATE TABLE tick_rows (mp_route_id INTEGER)")
    db.commit()
    db.close()

    def boom():
        raise AssertionError("the database must not be opened")

    monkeypatch.setattr(loader, "ingest_engine", boom)
    with pytest.raises(SystemExit, match="aggregate-only"):
        asyncio.run(main(raw, today=TODAY, max_quarantine_share=1.0, dry_run=False, scope="rock"))


@requires_pg
def test_existing_counts_handles_more_ids_than_the_parameter_limit():
    with migrated_db() as name:
        async def go(engine):
            async with engine.connect() as conn:
                return await existing_counts(conn, set(range(900000000, 900040000)))

        assert run_on_engine(name, go) == {}
```

Add `fetch` and `run_on_engine` to the existing `from tests.pgtest import …` line.

`backend/tests/verify/test_tick_seam.py`:

```python
"""Read-only seam cells (exposure plan X-1): run with VERIFY against a branch or prod."""

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db


def test_complete_totals_equal_their_counted_months():
    [row] = fetch(
        "SELECT count(*) AS bad FROM internal.route_tick_coverage c "
        "LEFT JOIN (SELECT route_id, sum(tick_count) AS s FROM internal.route_tick_months GROUP BY 1) m "
        "ON m.route_id = c.route_id WHERE c.tick_status = 'complete' AND c.tick_total <> coalesce(m.s, 0)")
    assert row["bad"] == 0


def test_status_counts_by_type_group_are_reported():
    rows = fetch(
        "SELECT r.type_group, c.tick_status, count(*) AS n FROM scorable_routes r "
        "JOIN internal.route_tick_coverage c ON c.route_id = r.route_id GROUP BY 1, 2 ORDER BY 1, 2")
    assert rows
    for r in rows:
        print(r["type_group"], r["tick_status"], r["n"])
```

(`tests/verify/_db.py`'s `fetch(sql)` is the shared helper the catalog cells use; it skips without `VERIFY_DATABASE_URL`.)

- [ ] **Step 2: Run the tests to confirm they fail.** `cd backend && uv run pytest tests/test_mp_tick_aggregates.py -q -k "rock or routes_to_load or parameter_limit"` → FAIL (`ImportError: routes_to_load`, or `TypeError: unexpected keyword 'scope'`).

- [ ] **Step 3: Implement.** In `mp_tick_aggregates.py`:

```python
async def existing_counts(conn: AsyncConnection, route_ids: set[int]) -> dict[tuple[int, str, str], int]:
    if not route_ids:
        return {}
    # One array parameter, not an expanding IN: a cumulative rock export names ~163K routes, far
    # past asyncpg's 32,767-parameter limit.
    result = await conn.execute(
        text("SELECT mp_route_id, period, style, tick_count FROM internal.mp_tick_aggregates "
             "WHERE mp_route_id = ANY(CAST(:ids AS bigint[]))"),
        {"ids": sorted(route_ids)},
    )
    return {(int(r), str(p), str(s)): int(n) for r, p, s, n in result.all()}


async def seam_scraped_at(conn: AsyncConnection, route_ids: set[int]) -> dict[int, datetime]:
    if not route_ids:
        return {}
    result = await conn.execute(
        text("SELECT mp_route_id, scraped_at FROM internal.route_tick_scrapes "
             "WHERE mp_route_id = ANY(CAST(:ids AS bigint[]))"),
        {"ids": sorted(route_ids)},
    )
    return {int(r): at for r, at in result.all()}


def routes_to_load(totals: list[RouteTotal], seam: dict[int, datetime]) -> set[int]:
    """Routes the seam has not seen at this scrape time or later. An unzoned scrape time is kept so
    validate quarantines it, rather than raising on the comparison here."""
    out = set()
    for t in totals:
        seen = seam.get(t.mp_route_id)
        if seen is None or t.scraped_at.tzinfo is None or seen < t.scraped_at:
            out.add(t.mp_route_id)
    return out
```

Replace `main` with:

```python
async def main(
    path: Path, *, today: date, max_quarantine_share: float, dry_run: bool, scope: Scope = "ice_mixed"
) -> dict[str, object]:
    source = SOURCE_BY_SCOPE[scope]
    check_export_location(path)
    if scope == "rock":
        check_aggregate_only(path)
    totals, monthly = read_export(path, routes=set() if scope == "rock" else None)
    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            unchanged = 0
            if scope == "rock":
                todo = routes_to_load(totals, await seam_scraped_at(conn, {t.mp_route_id for t in totals}))
                unchanged = len(totals) - len(todo)
                if not todo:
                    return {"status": "noop", "rows_upserted": 0, "routes_unchanged": unchanged}
                totals = [t for t in totals if t.mp_route_id in todo]
                monthly = read_export(path, routes=todo)[1]
            sha = sha256_rows(
                [(t.mp_route_id, t.total_ticks, t.complete, t.scraped_at.isoformat()) for t in totals]
                + [(m.mp_route_id, m.year_month, m.style, m.n) for m in monthly]
            )
            if await find_completed(conn, source=source, window_start=None, window_end=None, content_sha256=sha):
                return {"status": "noop", "rows_upserted": 0, "routes_unchanged": unchanged}
            route_types = {
                int(r): (str(t) if t is not None else None)
                for r, t in (await conn.execute(text("SELECT mp_route_id, type FROM mp_routes"))).all()
            }
            existing = await existing_counts(conn, {t.mp_route_id for t in totals})
            rows, report, stats = validate(totals, monthly, route_types=route_types, existing=existing, today=today,
                                           scope=scope)
            stats["routes_unchanged"] = unchanged
            problems = batch_gate(
                report, previous_rows_in=None, count_tolerance=1.0, max_quarantine_share=max_quarantine_share
            )
            scrapes = seam_rows(totals, monthly, report)
            stats["seam_complete"] = sum(1 for sc in scrapes if sc.complete)
            stats["seam_scraped"] = len(scrapes) - stats["seam_complete"]
            if dry_run:
                return {"status": "dry_run", "report": report.summary(), "stats": stats, "problems": problems}
            run_id = await start_run(conn, source=source, window_start=None, window_end=None, content_sha256=sha)
            await write_quarantine(conn, run_id, report)
            if problems:
                await finish_run(conn, run_id, status="rejected", report=report, rows_upserted=0, problems=problems)
                return {"status": "rejected", "rows_upserted": 0, "problems": problems, "report": report.summary()}
            inserted = await load(conn, rows, run_id=run_id, scrape_run_id=f"sqlite:{sha[:12]}")
            await write_seam(conn, scrapes, run_id=run_id)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=inserted)
            return {"status": "ok", "rows_upserted": inserted, "report": report.summary(), "stats": stats}
    finally:
        await engine.dispose()
```

(The ice path's statements are unchanged, apart from the location check and the extra `routes_unchanged: 0` stat. The `find_completed` noop now also carries `routes_unchanged`. Update any existing ice test that compares the whole noop dict, and only that assertion.)

`cli()` gains the following and passes `scope=args.scope` to `main`:

```python
    parser.add_argument("--scope", choices=SCOPES, default="ice_mixed",
                        help="ice_mixed (the one-time ice/mixed export) or rock (the incremental rock export)")
```

Docs:
- `CLAUDE.md` commands list:
  - Change `mp_tick_aggregates` to `mp_tick_aggregates --sqlite <export> [--scope ice_mixed|rock] [--dry-run]`.
  - Add one line: "`--scope rock` loads the private rock export: aggregate tables only, never a path inside the repo, routes the seam already holds at that scrape time skipped (reloading a cumulative export is a no-op); run from the Mac as `ingest` (exposure plan Task 3)."
- `CHANGELOG.md`: a dated "Rock tick loader (PR X-1)" entry.
- `data/DATABASE_STRUCTURE.md`, under `internal.route_tick_scrapes`: "written by both export scopes; `source_ingest_log.source` is `mp_tick_aggregates` (ice/mixed) or `mp_tick_aggregates_rock`".

- [ ] **Step 4: Run the tests.** `cd backend && uv run pytest tests/test_mp_tick_aggregates.py -q && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd .. && python3 scripts/check_no_scrapers.py && python3 scripts/check_no_mp_data.py` → all green.

- [ ] **Step 5: Commit.** `git add backend/app/pipelines/mp_tick_aggregates.py backend/tests/ CLAUDE.md CHANGELOG.md data/DATABASE_STRUCTURE.md && git commit -m "feat(pipelines): incremental rock tick loads through the seam; --scope CLI; seam verify cells"`

---

### Task 3: OWNER/AGENT RUNBOOK — rock tick loads (branch, then prod; then each later load)

This runbook follows the MP catalog plan's Task 6 pattern; its Shell state, Secrets, Long runs and Gates rules apply verbatim.
- `TARGET_HOST` is explicit, and the helpers refuse while it is unset.
- No block defines a helper.
- Credentials come only from the gitignored `backend/.env.<role>` files of the main checkout, sourced inside the helpers. Agents never read, copy or print them.
- **The private export is never opened by an agent** except through the loader and the two count queries below. It is never copied into the repository, and its path is never committed.
- **Owner does:** Step 0's export and confirmation, the Step 3 sign-off, the go for Step 4, and each later export.

**Prerequisite:** prod at `0010_accident_route_links` with the catalog refreshed (MP catalog plan Task 6). X-1 merged, or the main checkout on `feat/exposure-rock-ticks`.

**Re-enter the rehearsal branch** (never creates anything):

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend
. scripts/runbook_helpers.sh
unset TARGET_HOST
B=xp-1-rehearsal
BRANCH_HOST="$(branch_host "${B:?}")"
( set -a; . ./.env.owner; set +a
  [ -n "${BRANCH_HOST-}" ] || { echo 'BRANCH_HOST is not set' >&2; exit 1; }
  case "$OWNER_DATABASE_URL" in *"@${BRANCH_HOST}/"* | *"@${BRANCH_HOST}?"* | *"@${BRANCH_HOST}:"*) echo 'BRANCH_HOST is production: stop' >&2; exit 1 ;; esac
  echo "branch host ok" ) && export TARGET_HOST="$BRANCH_HOST"
[ -n "${TARGET_HOST-}" ] && [ "$TARGET_HOST" != production ] && echo "target is the rehearsal branch"
EXPORT="${EXPORT:?set EXPORT to the owner-supplied export path}"
```

Expected: `branch host ok`, `target is the rehearsal branch`.

**Prod preamble** (Step 4 blocks only, after the owner's go):

```bash
cd /Users/sebastianfrazier/Developer/SafeAscent/backend && . scripts/runbook_helpers.sh && export TARGET_HOST=production GATE_DIR=../data/review/gates_xp1_prod || exit 1
EXPORT="${EXPORT:?set EXPORT to the owner-supplied export path}"
```

- [ ] **Step 0 (owner, then agent): The export, checkout, tools**
  - **Owner.**
    - Runs the private export outside this repository. That code lives only in `~/Developer/safeascent-private/` and is never committed.
    - Writes a fresh single-file SQLite (no `-wal` sidecar; use `sqlite3 <live db> ".backup <out>"` when the source is in WAL mode) at `~/Developer/safeascent-private/exports/rock_ticks_<YYYYMMDD>.sqlite`.
    - The file holds exactly the two aggregate tables of decision D-9:
      - `route_tick_totals(mp_route_id, total_ticks, complete 0/1, scraped_at ISO-8601 with a UTC offset[, last_page, pages_fetched])`, where `scraped_at` is the time of the route's last page and `complete = 1` only when every page was fetched;
      - `route_tick_monthly(mp_route_id, year_month 'YYYY-MM', style in lead/follow/tr/solo/unknown, n > 0)`.
    - There is no climber, comment or text column anywhere.
    - **Confirms in writing:** the path, that the file is aggregate-only, and that the scraper's rate is unchanged (one 60 s limiter).
  - **Agent.** As MP catalog Task 6 Step 0: the main checkout is clean and on the branch carrying X-1; `uv sync --frozen`; `psql --version`; `command -v neonctl`; helpers loaded. Then, read-only and counts only:

```bash
[ ! -e "${EXPORT}-wal" ] || { echo "WAL sidecar present: ask the owner for a .backup copy" >&2; exit 1; }
case "$(cd "$(dirname "$EXPORT")" && pwd -P)" in /Users/sebastianfrazier/Developer/SafeAscent*) echo "export is inside the repo: STOP" >&2; exit 1 ;; esac
sqlite3 -readonly "$EXPORT" "SELECT count(*), sum(complete) FROM route_tick_totals; SELECT count(*) FROM route_tick_monthly"
```

Expected: no refusal line, and three numbers: routes, complete routes, monthly rows. The agent never runs any other query on the export.

- [ ] **Step 1 (agent): Branch `xp-1-rehearsal` from `production`**

```bash
B=xp-1-rehearsal
neonctl branches create --project-id still-morning-74008008 --name "${B:?}" --parent production --no-secrets --output json \
  | python3 -c 'import json,sys; b=json.load(sys.stdin)["branch"]; print(b["id"], b["name"], b["parent_id"])'
```

Then run the re-entry block, then `MIG current`. Expected: `0010_accident_route_links` (or `0011_exposure_index` once X-2 is on prod). X-1 has no migration and no grant change.

- [ ] **Step 2 (agent): Dry run, load, reload on the branch**

```bash
save_once ../data/review/xp1_dry_branch.json INGMOD app.pipelines.mp_tick_aggregates --sqlite "$EXPORT" --scope rock --dry-run
bg_start ../data/review/logs/xp1_load_branch.log INGMOD app.pipelines.mp_tick_aggregates --sqlite "$EXPORT" --scope rock
```

```bash
bg_wait ../data/review/logs/xp1_load_branch.log
```

```bash
save_once ../data/review/xp1_load_branch.json grep '^{' ../data/review/logs/xp1_load_branch.log
INGMOD app.pipelines.mp_tick_aggregates --sqlite "$EXPORT" --scope rock
VERIFY tests/verify/test_tick_seam.py -q -s
```

Expected:
- **Dry run:** `status: dry_run`, empty `problems`, and `report.quarantined` limited to `ice_mixed_route`, `route_incomplete`, `partial_month`, `future_month` and `unknown_route` with small counts. `stats.seam_complete` should be close to the export's complete count.
- **Load:** `status: ok`, with `rows_upserted` equal to the dry run's accepted rows minus `already_loaded_*`.
- **Second load:** `{"routes_unchanged": N, "rows_upserted": 0, "status": "noop"}`.
- **Verify:** passes and prints status counts by type group.

- [ ] **Step 3 (agent, then STOP for the owner): Branch evidence**

The agent pastes:
- the dry-run JSON (counts only);
- the load JSON;
- the verify's per-type-group `missing`/`scraped`/`complete` counts.

**STOP: owner review.** Continue only on the owner's written "rock load ok".

- [ ] **Step 4 (owner go, then agent): Prod, first load**

4a — gate (read-only, fails closed):

```bash
( cd /Users/sebastianfrazier/Developer/SafeAscent/backend && . scripts/runbook_helpers.sh && export TARGET_HOST=production GATE_DIR=../data/review/gates_xp1_prod || exit 1
  # PRODUCTION (read-only)
  [ -s ../data/review/xp1_load_branch.json ] || { echo "no branch evidence" >&2; exit 1; }
  cur="$(MIG current)" || exit 1
  printf '%s\n' "$cur"
  case "$cur" in
    0010_accident_route_links | "0010_accident_route_links (head)" | 0011_exposure_index | "0011_exposure_index (head)") ;;
    *) echo "prod is not at 0010/0011: STOP" >&2; exit 1 ;;
  esac
  ( set -a; . ./.env.ingest; set +a
    python3 -c 'import os, sys, urllib.parse as u
h = (u.urlsplit(os.environ.get("INGEST_DATABASE_URL", "")).hostname or "").lower()
ok = h.startswith("ep-") and "-pooler." not in h
print("ingest direct host:", ok)
sys.exit(0 if ok else 1)' ) || { echo "ingest URL is not the direct Neon host: STOP" >&2; exit 1; }
  gate_pass xp1_load )
```

Expected: the revision line, `ingest direct host: True`, `GATE OK: xp1_load`.

4a-snap — snapshot `pre-xp-1`. Run it once. If neonctl says the branch exists, STOP and ask the owner; never recreate it:

```bash
( cd /Users/sebastianfrazier/Developer/SafeAscent/backend && . scripts/runbook_helpers.sh && export TARGET_HOST=production GATE_DIR=../data/review/gates_xp1_prod || exit 1
  # PRODUCTION snapshot
  gate_require xp1_load || exit 1
  snap="$(neonctl branches create --project-id still-morning-74008008 --name pre-xp-1 --parent production --no-secrets --output json \
    | python3 -c 'import json,sys; b=json.load(sys.stdin)["branch"]; print(b["id"], b["name"], b["parent_id"], b["created_at"])')" || exit 1
  printf '%s\n' "$snap"
  case "$snap" in *" pre-xp-1 br-restless-bar-ajw5zy4b "*) gate_pass xp1_snapshot ;; *) echo "snapshot parent is not production: STOP" >&2; exit 1 ;; esac )
```

4b — gate: the prod dry run must match the branch dry run for the same file:

```bash
( cd /Users/sebastianfrazier/Developer/SafeAscent/backend && . scripts/runbook_helpers.sh && export TARGET_HOST=production GATE_DIR=../data/review/gates_xp1_prod || exit 1
  # PRODUCTION (read-only dry run)
  gate_require xp1_snapshot || exit 1
  EXPORT="${EXPORT:?}"
  save_once ../data/review/xp1_dry_prod.json INGMOD app.pipelines.mp_tick_aggregates --sqlite "$EXPORT" --scope rock --dry-run || exit 1
  python3 - <<'EOF' || exit 1
import json
b = json.load(open("../data/review/xp1_dry_branch.json")); p = json.load(open("../data/review/xp1_dry_prod.json"))
assert p["status"] == "dry_run" and not p["problems"], p
for k in ("routes", "seam_complete", "seam_scraped"):
    assert p["stats"][k] == b["stats"][k], (k, b["stats"][k], p["stats"][k])
assert p["report"]["quarantined"] == b["report"]["quarantined"], (b["report"], p["report"])
print("prod dry run matches branch")
EOF
  gate_pass xp1_apply )
```

4c — load prod:

```bash
( cd /Users/sebastianfrazier/Developer/SafeAscent/backend && . scripts/runbook_helpers.sh && export TARGET_HOST=production GATE_DIR=../data/review/gates_xp1_prod || exit 1
  gate_require xp1_apply || exit 1
  bg_start ../data/review/logs/xp1_load_prod.log INGMOD app.pipelines.mp_tick_aggregates --sqlite "${EXPORT:?}" --scope rock )
```

Then, with the prod preamble:
- `bg_wait ../data/review/logs/xp1_load_prod.log` until `status: ok`;
- `VERIFY tests/verify/test_tick_seam.py -q -s` → pass.

Record the counts and the snapshot id in the PR.

- [ ] **Step 5 (owner, then agent): Each later load** `[default pending: DP10]`

The owner writes a new dated export (Step 0 contract) and gives its path. The agent then runs the blocks below. `D` is the export date and `GATE_DIR=../data/review/gates_xp1_load_$D`. The branch rehearsal is repeated only if `mp_tick_aggregates.py` changed since the last rehearsal.

```bash
( cd /Users/sebastianfrazier/Developer/SafeAscent/backend && . scripts/runbook_helpers.sh && export TARGET_HOST=production || exit 1
  D="${D:?}"; export GATE_DIR="../data/review/gates_xp1_load_$D"; EXPORT="${EXPORT:?}"
  # PRODUCTION (read-only dry run)
  save_once "../data/review/xp1_dry_prod_$D.json" INGMOD app.pipelines.mp_tick_aggregates --sqlite "$EXPORT" --scope rock --dry-run || exit 1
  python3 - "../data/review/xp1_dry_prod_$D.json" <<'EOF' || exit 1
import json, sys
p = json.load(open(sys.argv[1]))
if p["status"] == "noop":
    print("nothing newer in this export"); sys.exit(1)
r = p["report"]; q = sum(r["quarantined"].values())
assert p["status"] == "dry_run" and not p["problems"], p
assert r["rows_in"] and q / r["rows_in"] <= 0.10, (q, r["rows_in"])
print("load gate ok:", p["stats"]["routes"], "routes,", p["stats"]["seam_complete"], "complete")
EOF
  snap="$(neonctl branches create --project-id still-morning-74008008 --name "pre-xp-load-$D" --parent production --no-secrets --output json \
    | python3 -c 'import json,sys; b=json.load(sys.stdin)["branch"]; print(b["id"], b["name"], b["parent_id"])')" || exit 1
  case "$snap" in *" br-restless-bar-ajw5zy4b") gate_pass load ;; *) echo "snapshot parent is not production: STOP" >&2; exit 1 ;; esac )
```

```bash
( cd /Users/sebastianfrazier/Developer/SafeAscent/backend && . scripts/runbook_helpers.sh && export TARGET_HOST=production || exit 1
  D="${D:?}"; export GATE_DIR="../data/review/gates_xp1_load_$D"
  gate_require load || exit 1
  bg_start "../data/review/logs/xp1_load_prod_$D.log" INGMOD app.pipelines.mp_tick_aggregates --sqlite "${EXPORT:?}" --scope rock )
```

Then:
- `bg_wait` that log until `status: ok`;
- `VERIFY tests/verify/test_tick_seam.py -q -s`;
- once X-2 is on prod, `INGMOD app.pipelines.exposure build` (Task 10 Step 5).

"Nothing newer" means no load is needed. Any other failure: STOP and report the counts. The owner deletes old `pre-xp-load-*` snapshots when satisfied; the agent never deletes a branch.

---

# PR X-2 — `feat/exposure-index`

### Task 4: Migration `0011_exposure_index`, model, setting, lockstep grants

**Files:**
- Create: `backend/alembic/versions/0011_exposure_index.py`, `backend/app/models/exposure.py`, `backend/tests/test_migration_0011.py`
- Modify:
  - `backend/app/models/__init__.py`, `backend/app/config.py`, `.env.example`, `backend/pyproject.toml`;
  - the lockstep trio: `backend/db/roles/grants_phase2.sql`, `backend/db/roles/verify_roles_phase2.sql`, `backend/tests/test_roles_phase2.py`.

**Interfaces:**
- Consumes: `0010_accident_route_links` (down revision); `canonical_areas.area_id`.
- Produces (DB): `internal.exposure_index`:

  | Column | Type and constraints |
  |---|---|
  | `area_id` | `bigint NOT NULL REFERENCES canonical_areas(area_id) ON DELETE RESTRICT` |
  | `type_group` | `text NOT NULL CHECK IN ('sport','trad','rock','alpine','ice','mixed')` |
  | `month` | `smallint NOT NULL CHECK 1..12` |
  | `n_routes` | `integer NOT NULL CHECK > 0` |
  | `proxy` | `jsonb NOT NULL CHECK (jsonb_typeof(proxy) = 'object')` |
  | `proxy_version` | `text NOT NULL` |
  | `as_of` | `date NOT NULL` |
  | `data_cutoff` | `date NOT NULL CHECK (data_cutoff <= as_of)` |
  | `tick_coverage` | `real NULL CHECK (tick_coverage BETWEEN 0 AND 1)` |
  | `exposure_point`, `exposure_lo`, `exposure_hi` | `real NULL`, CHECK all-NULL-or-none |
  | `run_id` | `uuid NULL` |

  The PK is `(area_id, type_group, month, as_of, data_cutoff)`, with index `ix_exposure_index_snapshot (as_of, data_cutoff)`. The downgrade refuses while any row exists.
- Produces (Python): `app.models.exposure.ExposureIndex`; `Settings.EXPOSURE_ENABLE_MP_TICKS: bool = True`.
- Produces (grants): `ingest` gets SELECT, INSERT, UPDATE, DELETE on `internal.exposure_index`. `analyst` gets SELECT through the existing schema-wide grant. `app` and `trainer` get nothing.

- [ ] **Step 1: Write the failing test.** `backend/tests/test_migration_0011.py`:

```python
import asyncpg
import pytest
from alembic import command

from tests.pgtest import migrated_db, requires_pg, run_sql
from tests.test_migrations import _alembic_cfg

pytestmark = requires_pg
PIN = "0011_exposure_index"
SEED = """
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), (900000110, 'Fixture Crag', 900000100, 40.2, -105.3);
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  (900000100, NULL, '900000100', 1, 'us', 'CO', NULL, NULL, 'none', NULL, NULL, 'v'),
  (900000110, 900000100, '900000100.900000110', 2, 'us', 'CO', 40.2, -105.3, 'own', 900000110,
   '40.20000:-105.30000', 'v');
"""


def _row(**over: str) -> str:
    cols = {"area_id": "900000110", "type_group": "'trad'", "month": "7", "n_routes": "1", "proxy": "'{}'",
            "proxy_version": "'x-mp-v1'", "as_of": "'2026-10-15'", "data_cutoff": "'2026-10-15'",
            "tick_coverage": "NULL", "exposure_point": "NULL", "exposure_lo": "NULL", "exposure_hi": "NULL"}
    cols.update(over)
    return f"INSERT INTO internal.exposure_index ({', '.join(cols)}) VALUES ({', '.join(cols.values())})"


def test_0011_checks():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        run_sql(name, _row())
        run_sql(name, _row(month="8", tick_coverage="0", exposure_point="2", exposure_lo="0.6667", exposure_hi="6"))
        for bad in ({"type_group": "'unknown'"}, {"month": "13"}, {"n_routes": "0"},
                    {"data_cutoff": "'2026-10-16'"}, {"exposure_point": "1.0"}, {"tick_coverage": "1.5"},
                    {"proxy": "'[]'"}):
            with pytest.raises(asyncpg.CheckViolationError):
                run_sql(name, _row(**{"month": "9", **bad}))


def test_0011_models_match_at_head():
    with migrated_db() as name:
        command.check(_alembic_cfg(name))


def test_0011_downgrade_refuses_while_rows_exist():
    with migrated_db(revision=PIN, seed_sql=SEED) as name:
        run_sql(name, _row())
        with pytest.raises(RuntimeError, match="refusing to downgrade 0011"):
            command.downgrade(_alembic_cfg(name), "0010_accident_route_links")
        run_sql(name, "DELETE FROM internal.exposure_index")
        command.downgrade(_alembic_cfg(name), "0010_accident_route_links")
```

Append to `test_phase2_roles_least_privilege` in `backend/tests/test_roles_phase2.py`, after the R10 block:

```python
    # Exposure (0011): ingest rebuilds and prunes snapshots; analyst reads; app holds nothing in internal.
    _as(ingest, "SELECT count(*) FROM internal.exposure_index; "
        "UPDATE internal.exposure_index SET run_id = NULL WHERE false; DELETE FROM internal.exposure_index WHERE false")
    _denied(ingest, "TRUNCATE internal.exposure_index")
    _as(analyst, "SELECT count(*) FROM internal.exposure_index")
    _denied(app, "SELECT count(*) FROM internal.exposure_index")
    _denied(analyst, "BEGIN READ WRITE; DELETE FROM internal.exposure_index WHERE false; COMMIT")
```

In `test_verify_phase2_catches_stray_grants`:
- add `"GRANT TRUNCATE ON internal.exposure_index TO ingest;"` to the stray-grant SQL;
- add `"ingest TRUNCATE on internal.exposure_index matches the expected set",` to the expected failures.

- [ ] **Step 2: Run the tests to confirm they fail.** `cd backend && uv run pytest tests/test_migration_0011.py -q` → FAIL (no revision `0011_exposure_index`).

- [ ] **Step 3: Implement.** `backend/alembic/versions/0011_exposure_index.py`:

```python
"""Exposure (P2-9) on the MP catalog: one row per area x type group x month per snapshot, the raw
proxy components with missing flags, tick coverage, and an unfitted point with x/÷3 bounds. In
schema internal: it carries MP tick aggregates, and the Phase 1 role check requires app to read
every public table, so app stays out by schema rather than by a REVOKE."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_exposure_index"
down_revision = "0010_accident_route_links"
branch_labels = None
depends_on = None

# Scorable type groups only (0008's set minus 'unknown', which is never scorable).
TYPE_GROUPS = "('sport', 'trad', 'rock', 'alpine', 'ice', 'mixed')"


def upgrade() -> None:
    op.create_table(
        "exposure_index",
        sa.Column("area_id", sa.BigInteger(), sa.ForeignKey("canonical_areas.area_id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("type_group", sa.Text(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("n_routes", sa.Integer(), nullable=False),
        sa.Column("proxy", postgresql.JSONB(), nullable=False),
        sa.Column("proxy_version", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("data_cutoff", sa.Date(), nullable=False),
        sa.Column("tick_coverage", sa.REAL(), nullable=True),
        sa.Column("exposure_point", sa.REAL(), nullable=True),
        sa.Column("exposure_lo", sa.REAL(), nullable=True),
        sa.Column("exposure_hi", sa.REAL(), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("area_id", "type_group", "month", "as_of", "data_cutoff"),
        sa.CheckConstraint(f"type_group IN {TYPE_GROUPS}", name="exposure_index_type_group_check"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="exposure_index_month_check"),
        sa.CheckConstraint("n_routes > 0", name="exposure_index_n_routes_check"),
        sa.CheckConstraint("jsonb_typeof(proxy) = 'object'", name="exposure_index_proxy_check"),
        sa.CheckConstraint("data_cutoff <= as_of", name="exposure_index_cutoff_check"),
        sa.CheckConstraint("tick_coverage IS NULL OR tick_coverage BETWEEN 0 AND 1",
                           name="exposure_index_tick_coverage_check"),
        sa.CheckConstraint(
            "(exposure_point IS NULL) = (exposure_lo IS NULL) AND (exposure_lo IS NULL) = (exposure_hi IS NULL)",
            name="exposure_index_bounds_check"),
        schema="internal",
    )
    op.create_index("ix_exposure_index_snapshot", "exposure_index", ["as_of", "data_cutoff"], schema="internal")


def downgrade() -> None:
    n = op.get_bind().exec_driver_sql("SELECT count(*) FROM internal.exposure_index").scalar_one()
    if n:
        raise RuntimeError(f"refusing to downgrade 0011: internal.exposure_index has {n} rows")
    op.drop_index("ix_exposure_index_snapshot", "exposure_index", schema="internal")
    op.drop_table("exposure_index", schema="internal")
```

`backend/app/models/exposure.py`:

```python
"""Exposure index (migration 0011)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import REAL, BigInteger, CheckConstraint, Date, ForeignKey, Index, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ExposureIndex(Base):
    __tablename__ = "exposure_index"
    __table_args__ = (
        CheckConstraint("type_group IN ('sport', 'trad', 'rock', 'alpine', 'ice', 'mixed')",
                        name="exposure_index_type_group_check"),
        CheckConstraint("month BETWEEN 1 AND 12", name="exposure_index_month_check"),
        CheckConstraint("n_routes > 0", name="exposure_index_n_routes_check"),
        CheckConstraint("jsonb_typeof(proxy) = 'object'", name="exposure_index_proxy_check"),
        CheckConstraint("data_cutoff <= as_of", name="exposure_index_cutoff_check"),
        CheckConstraint("tick_coverage IS NULL OR tick_coverage BETWEEN 0 AND 1",
                        name="exposure_index_tick_coverage_check"),
        CheckConstraint(
            "(exposure_point IS NULL) = (exposure_lo IS NULL) AND (exposure_lo IS NULL) = (exposure_hi IS NULL)",
            name="exposure_index_bounds_check"),
        Index("ix_exposure_index_snapshot", "as_of", "data_cutoff"),
        {"schema": "internal"},
    )

    area_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("canonical_areas.area_id", ondelete="RESTRICT"),
                                         primary_key=True, autoincrement=False)
    type_group: Mapped[str] = mapped_column(Text, primary_key=True)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    data_cutoff: Mapped[date] = mapped_column(Date, primary_key=True)
    n_routes: Mapped[int] = mapped_column(Integer)
    proxy: Mapped[dict[str, Any]] = mapped_column(JSONB)
    proxy_version: Mapped[str] = mapped_column(Text)
    tick_coverage: Mapped[float | None] = mapped_column(REAL)
    exposure_point: Mapped[float | None] = mapped_column(REAL)
    exposure_lo: Mapped[float | None] = mapped_column(REAL)
    exposure_hi: Mapped[float | None] = mapped_column(REAL)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
```

Register the model in `app/models/__init__.py` with `from app.models import exposure  # noqa: F401  (exposure index, 0011)`. Add `"app.models.exposure"` to the models mypy block.

`config.py`:
`EXPOSURE_ENABLE_MP_TICKS: bool = True` with the comment `# P2-9: false makes mp_ticks missing ("disabled") in every new exposure snapshot (the removal drill).`

`.env.example`: `EXPOSURE_ENABLE_MP_TICKS=true`.

`grants_phase2.sql`: append before `RESET ROLE;`:

```sql
-- Exposure plan (0011). The exposure job rebuilds snapshots and prunes old production ones (DELETE);
-- analyst reads it through the schema-wide SELECT above; app holds nothing in internal; trainer
-- gets it in Phase 3 (D13), together with USAGE on internal.
GRANT SELECT, INSERT, UPDATE, DELETE ON internal.exposure_index TO ingest;
```

`verify_roles_phase2.sql`:
- Append to `ingest_privs` (after the R10 rows, with the comment `-- Exposure (0011): snapshots are rebuilt and pruned.`): `('internal.exposure_index', 'SELECT'), ('internal.exposure_index', 'INSERT'), ('internal.exposure_index', 'UPDATE'), ('internal.exposure_index', 'DELETE')`.
- Add `'internal.exposure_index'` to the `catalog relation exists` array.
- `app holds nothing on internal relation …`, `analyst can SELECT …` and `trainer holds no privilege on …` then cover it with no further rows.

- [ ] **Step 4: Run the tests.** `cd backend && PATH=/opt/homebrew/opt/libpq/bin:$PATH CI=1 uv run pytest tests/test_migration_0011.py tests/test_roles_phase2.py tests/test_env_example_parity.py tests/test_migrations.py -q && uv run mypy` → PASS (no psql skips).

- [ ] **Step 5: Commit.** `git add backend/alembic/versions/0011_exposure_index.py backend/app/models/ backend/app/config.py .env.example backend/pyproject.toml backend/db/roles/ backend/tests/test_migration_0011.py backend/tests/test_roles_phase2.py && git commit -m "feat(db): 0011 internal.exposure_index; ingest grants in lockstep"`

---

### Task 5: Pure exposure math

**Files:**
- Create: `backend/app/pipelines/exposure_math.py`, `backend/tests/test_exposure_math.py`
- Modify: `backend/pyproject.toml` (strict mypy block: `app.pipelines.exposure_math`)

**Interfaces:**
- Produces:
  - constants: `PROXY_VERSION = "x-mp-v1"`, `COMPONENTS = ("route_density", "season_share", "mp_ticks")`, `TYPE_GROUPS = ("sport", "trad", "rock", "alpine", "ice", "mixed")`, `MONTHS = range(1, 13)`, `RULE_BY_GROUP`, `ROCK_TMAX_LO/HI`, `ROCK_MAX_PRECIP`, `ICE_WINDOW`, `ICE_MIN_FROZEN`, `SEASON_YEARS = 3`, `MIN_SEASON_DAYS = 28`, `TICK_WINDOW_MONTHS = 60`, `TICK_WINDOW_YEARS = 5`, `BOUND_FACTOR = 3.0`;
  - `@dataclass(frozen=True) Component(value, missing, reason=None, detail=())`, with `present(value, **detail)`, `absent(reason, **detail)`, `get(name) -> float | None` and `to_json() -> dict[str, object]`;
  - `rock_day(tmax, precip) -> bool | None`, `ice_day(frozen: int, known: int) -> bool | None`, `alpine_day(rock, ice) -> bool | None`;
  - `spearman(x, y) -> float | None`, `exposure_point(route_density, season) -> float | None`, `bounds(point) -> tuple[float | None, float | None]`;
  - `tick_component(*, n_routes, n_complete, n_scraped, n_missing, window_ticks) -> Component`.

- [ ] **Step 1: Write the failing tests.** `backend/tests/test_exposure_math.py`:

```python
import math

import pytest

from app.pipelines.exposure_math import (
    Component, alpine_day, bounds, exposure_point, ice_day, rock_day, spearman, tick_component,
)


def test_rock_day_rule_and_unknown_inputs():
    assert rock_day(20.0, 0.0) is True
    assert rock_day(40.0, 0.0) is False and rock_day(20.0, 5.0) is False and rock_day(4.9, 0.0) is False
    assert rock_day(None, 0.0) is None and rock_day(20.0, None) is None


def test_unknown_days_are_not_false():
    assert ice_day(frozen=7, known=7) is True
    assert ice_day(frozen=6, known=10) is False
    assert ice_day(frozen=6, known=9) is None
    assert ice_day(frozen=0, known=4) is False
    assert ice_day(frozen=1, known=4) is None
    assert alpine_day(True, None) is True and alpine_day(False, None) is None and alpine_day(False, False) is False


def test_component_json_keeps_zero_and_missing_apart():
    assert Component.present(0.0).to_json() == {"value": 0.0, "missing": False}
    assert Component.absent("disabled").to_json() == {"value": None, "missing": True, "reason": "disabled"}
    assert Component.present(2.0, coverage=0.5).get("coverage") == 0.5
    with pytest.raises(ValueError, match="finite"):
        Component.present(math.nan)
    with pytest.raises(ValueError, match="finite"):
        Component.absent("x", coverage=math.inf)


def test_partial_coverage_is_raw_never_scaled():
    c = tick_component(n_routes=3, n_complete=1, n_scraped=1, n_missing=1, window_ticks=10.0)
    assert c.value == 2.0
    assert c.to_json() == {"value": 2.0, "missing": False, "coverage": 1 / 3, "n_complete": 1.0, "n_scraped": 1.0,
                           "n_missing": 1.0, "per_route_mean": 2.0, "window_months": 60.0}


def test_no_complete_route_is_missing_with_coverage_zero():
    c = tick_component(n_routes=2, n_complete=0, n_scraped=1, n_missing=1, window_ticks=0.0)
    assert c.to_json() == {"value": None, "missing": True, "reason": "no_complete_routes", "coverage": 0.0,
                           "n_complete": 0.0, "n_scraped": 1.0, "n_missing": 1.0}


def test_complete_routes_with_no_ticks_are_a_measured_zero():
    assert tick_component(n_routes=1, n_complete=1, n_scraped=0, n_missing=0, window_ticks=0.0).value == 0.0


def test_status_counts_must_add_up():
    with pytest.raises(ValueError, match="add up"):
        tick_component(n_routes=3, n_complete=1, n_scraped=0, n_missing=0, window_ticks=1.0)


def test_point_and_bounds_never_floor_a_missing_input():
    assert exposure_point(10.0, 0.5) == 5.0 and exposure_point(10.0, 0.0) == 0.0
    assert exposure_point(10.0, None) is None and exposure_point(None, 0.5) is None
    assert bounds(9.0) == (3.0, 27.0) and bounds(0.0) == (0.0, 0.0) and bounds(None) == (None, None)


def test_spearman_ties_and_degenerate_input():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 2, 3], [3, 2, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None and spearman([1, 2], [1, 2]) is None
```

- [ ] **Step 2: Run the tests to confirm they fail.** `cd backend && uv run pytest tests/test_exposure_math.py -q` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement.** `backend/app/pipelines/exposure_math.py`:

```python
"""Exposure (P2-9) on the MP catalog: the pure rules and arithmetic. Components are stored raw with
a missing flag and Phase 3 fits their weights (P3 M5). Nothing here turns a missing input into a
number: absent stays absent, a measured zero stays zero, and tick sums are never scaled by coverage."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

PROXY_VERSION = "x-mp-v1"
COMPONENTS = ("route_density", "season_share", "mp_ticks")
TYPE_GROUPS = ("sport", "trad", "rock", "alpine", "ice", "mixed")
MONTHS = range(1, 13)
RULE_BY_GROUP = {"sport": "rock", "trad": "rock", "rock": "rock", "alpine": "alpine", "ice": "ice", "mixed": "ice"}
ROCK_TMAX_LO, ROCK_TMAX_HI, ROCK_MAX_PRECIP = 5.0, 32.0, 2.0
ICE_WINDOW, ICE_MIN_FROZEN = 10, 7
SEASON_YEARS = 3
MIN_SEASON_DAYS = 28
TICK_WINDOW_MONTHS = 60
TICK_WINDOW_YEARS = TICK_WINDOW_MONTHS // 12
BOUND_FACTOR = 3.0

Detail = tuple[tuple[str, float], ...]


def _finite(name: str, value: float) -> float:
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return v


@dataclass(frozen=True)
class Component:
    value: float | None
    missing: bool
    reason: str | None = None
    detail: Detail = ()

    @classmethod
    def present(cls, value: float, **detail: float) -> Component:
        return cls(_finite("component value", value), False, None,
                   tuple(sorted((k, _finite(k, v)) for k, v in detail.items())))

    @classmethod
    def absent(cls, reason: str, **detail: float) -> Component:
        return cls(None, True, reason, tuple(sorted((k, _finite(k, v)) for k, v in detail.items())))

    def get(self, name: str) -> float | None:
        return dict(self.detail).get(name)

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"value": self.value, "missing": self.missing}
        if self.reason is not None:
            out["reason"] = self.reason
        out.update(self.detail)
        return out


def rock_day(tmax: float | None, precip: float | None) -> bool | None:
    if tmax is None or precip is None:
        return None
    return ROCK_TMAX_LO <= tmax <= ROCK_TMAX_HI and precip < ROCK_MAX_PRECIP


def ice_day(frozen: int, known: int) -> bool | None:
    """frozen/known count days with tmax < 0 and with any tmax in the ICE_WINDOW days ending today;
    an absent or NULL day is unknown, so it decides nothing unless it could not change the answer."""
    if frozen >= ICE_MIN_FROZEN:
        return True
    if frozen + (ICE_WINDOW - known) < ICE_MIN_FROZEN:
        return False
    return None


def alpine_day(rock: bool | None, ice: bool | None) -> bool | None:
    if rock is True or ice is True:
        return True
    if rock is None or ice is None:
        return None
    return False


def _ranks(values: Sequence[float]) -> np.ndarray:
    x = np.asarray(values, dtype=np.float64)
    order = x.argsort(kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x), dtype=np.float64)
    for v in np.unique(x):
        tied = x == v
        ranks[tied] = ranks[tied].mean()
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def exposure_point(route_density: float | None, season: float | None) -> float | None:
    # No floor on a missing season: inventing exposure would lower per-exposure risk.
    if route_density is None or season is None:
        return None
    return route_density * season


def bounds(point: float | None) -> tuple[float | None, float | None]:
    if point is None or not math.isfinite(point):
        return None, None
    return point / BOUND_FACTOR, point * BOUND_FACTOR


def tick_component(*, n_routes: int, n_complete: int, n_scraped: int, n_missing: int, window_ticks: float) -> Component:
    """window_ticks: the cell's ticks in that calendar month over each complete route's
    TICK_WINDOW_MONTHS window. The value is their per-year mean, raw: it covers the complete routes
    only and is never scaled to n_routes (DP2)."""
    if n_complete + n_scraped + n_missing != n_routes or n_routes <= 0:
        raise ValueError(f"route statuses must add up to n_routes ({n_complete}+{n_scraped}+{n_missing} != {n_routes})")
    counts = {"coverage": n_complete / n_routes, "n_complete": n_complete, "n_scraped": n_scraped,
              "n_missing": n_missing}
    if n_complete == 0:
        return Component.absent("no_complete_routes", **counts)
    per_year = window_ticks / TICK_WINDOW_YEARS
    return Component.present(per_year, per_route_mean=per_year / n_complete, window_months=TICK_WINDOW_MONTHS, **counts)
```

- [ ] **Step 4: Run the tests.** `cd backend && uv run pytest tests/test_exposure_math.py -q && uv run mypy` → PASS.

- [ ] **Step 5: Commit.** `git add backend/app/pipelines/exposure_math.py backend/tests/test_exposure_math.py backend/pyproject.toml && git commit -m "feat(pipelines): exposure math: components, day rules, tick coverage, bounds"`

---

### Task 6: Cell scope, route density, season share (SQL)

**Files:**
- Create: `backend/app/pipelines/exposure_components.py`, `backend/tests/test_exposure_components.py`
- Modify: `backend/pyproject.toml` (strict block: `app.pipelines.exposure_components`)

**Interfaces:**
- Consumes: Task 5's interfaces; `scorable_routes(area_id, type_group)`, `canonical_areas.point_key`, `feature_points(point_key, grid_bucket, tz)`, `cell_daily_conditions(grid_bucket, tz, date, tmax, precip_mm, record_kind)`.
- Produces:
  - `Key = tuple[int, str, int]`, `Scope = dict[tuple[int, str], int]`;
  - `async scope(conn) -> Scope`, which raises `ValueError` on a scorable type group outside `TYPE_GROUPS`;
  - `keys(scope_: Scope) -> list[Key]`, sorted;
  - `route_density(scope_) -> dict[Key, Component]`;
  - `async season(conn, scope_, *, today: date) -> dict[Key, Component]`, with missing reasons `no_point`, `no_tz` and `insufficient_history` (detail `n_days`);
  - `SERIES_CHUNK = 500`.

- [ ] **Step 1: Write the failing tests.** `backend/tests/test_exposure_components.py`:

```python
from collections import defaultdict
from datetime import date, timedelta

import pytest

from app.pipelines.exposure_components import keys, route_density, scope, season
from app.pipelines.exposure_math import ICE_WINDOW, alpine_day, ice_day, rock_day
from tests.pgtest import migrated_db, requires_pg, run_on_conn

TODAY = date(2026, 10, 15)
CRAG, ICE_AREA, BARE = 900000110, 900000120, 900000130
KEY, ICE_KEY, BARE_KEY = "40.20000:-105.30000", "40.30000:-105.40000", "40.40000:-105.50000"
SEED = f"""
INSERT INTO mp_locations (mp_id, name, parent_id, latitude, longitude) VALUES
  (900000100, 'Colorado', NULL, NULL, NULL), ({CRAG}, 'Fixture Crag', 900000100, 40.2, -105.3),
  ({ICE_AREA}, 'Fixture Ice', 900000100, 40.3, -105.4), ({BARE}, 'Fixture Bare', 900000100, 40.4, -105.5);
INSERT INTO mp_routes (mp_route_id, name, location_id, type) VALUES
  (900000001, 'Fixture One', {CRAG}, 'Trad'), (900000002, 'Fixture Two', {CRAG}, 'Trad'),
  (900000003, 'Fixture Three', {CRAG}, 'Trad'), (900000004, 'Fixture Ice', {ICE_AREA}, 'Ice'),
  (900000005, 'Fixture Bare', {BARE}, 'Sport'), (900000006, 'Fixture Unknown', {CRAG}, 'unknown');
INSERT INTO canonical_areas (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version) VALUES
  (900000100, NULL, '900000100', 1, 'us', 'CO', NULL, NULL, 'none', NULL, NULL, 'v'),
  ({CRAG}, 900000100, '900000100.{CRAG}', 2, 'us', 'CO', 40.2, -105.3, 'own', {CRAG}, '{KEY}', 'v'),
  ({ICE_AREA}, 900000100, '900000100.{ICE_AREA}', 2, 'us', 'CO', 40.3, -105.4, 'own', {ICE_AREA}, '{ICE_KEY}', 'v'),
  ({BARE}, 900000100, '900000100.{BARE}', 2, 'us', 'CO', 40.4, -105.5, 'own', {BARE}, '{BARE_KEY}', 'v');
INSERT INTO canonical_routes (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version) VALUES
  (900000001, {CRAG}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000002, {CRAG}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000003, {CRAG}, 'trad', false, '{{trad}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000004, {ICE_AREA}, 'ice', false, '{{ice}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000005, {BARE}, 'sport', false, '{{sport}}', 'rt-mp-v3', true, NULL, 'h', 'v'),
  (900000006, {CRAG}, 'unknown', false, '{{}}', 'rt-mp-v3', false, 'unknown_type', 'h', 'v');
INSERT INTO feature_points (point_key, lat, lon, grid_bucket, h3_r5, h3_r7, tz, feature_version) VALUES
  ('{KEY}', 40.2, -105.3, 11, 'a', 'b', 'America/Denver', 'f-v1'),
  ('{ICE_KEY}', 40.3, -105.4, 12, 'a', 'b', 'America/Denver', 'f-v1'),
  ('{BARE_KEY}', 40.4, -105.5, 13, 'a', 'b', NULL, 'f-v1');
"""
# Crag: every day 20 °C and dry, except every 7th day wet; one NULL-tmax day a month.
# Ice area: frozen Dec–Feb, thawed otherwise; a few NULL days inside the frozen spell.
START = TODAY - timedelta(days=3 * 365)


def _climate() -> tuple[str, list[tuple[int, date, float | None, float | None]]]:
    days = []
    for i in range((TODAY - START).days):
        d = START + timedelta(days=i)
        crag_tmax = None if d.day == 15 else 20.0
        days.append((11, d, crag_tmax, 5.0 if i % 7 == 0 else 0.0))
        frozen = d.month in (12, 1, 2)
        ice_tmax = None if frozen and d.day in (3, 4) else (-5.0 if frozen else 6.0)
        days.append((12, d, ice_tmax, 0.0))
    values = ", ".join(
        f"({b}, 'America/Denver', DATE '{d}', {'NULL' if t is None else t}, {p}, 'open_meteo_archive', 'era5')"
        for b, d, t, p in days)
    return ("INSERT INTO cell_daily_conditions (grid_bucket, tz, date, tmax, precip_mm, source, record_kind) "
            f"VALUES {values};"), days


def _expected(days, bucket: int, rule: str) -> dict[int, tuple[int, float | None]]:
    series = {d: (t, p) for b, d, t, p in days if b == bucket}
    per_month: dict[int, list[bool]] = defaultdict(list)
    for d, (t, p) in series.items():
        window = [series.get(d - timedelta(days=k), (None, None))[0] for k in range(ICE_WINDOW)]
        known = sum(1 for x in window if x is not None)
        frozen = sum(1 for x in window if x is not None and x < 0)
        r, i = rock_day(t, p), ice_day(frozen, known)
        ok = {"rock": r, "ice": i, "alpine": alpine_day(r, i)}[rule]
        if ok is not None:
            per_month[d.month].append(ok)
    return {m: (len(v), sum(v) / len(v)) for m, v in per_month.items()}


@requires_pg
def test_scope_counts_directly_attached_scorable_routes_only():
    with migrated_db(seed_sql=SEED) as name:
        s = run_on_conn(name, scope)
    assert s == {(CRAG, "trad"): 3, (ICE_AREA, "ice"): 1, (BARE, "sport"): 1}
    assert len(keys(s)) == 36
    assert route_density(s)[(CRAG, "trad", 7)].to_json() == {"value": 3.0, "missing": False}


@requires_pg
def test_season_sql_matches_python_rules():
    sql, days = _climate()
    with migrated_db(seed_sql=SEED + sql) as name:
        async def go(conn):
            return await season(conn, await scope(conn), today=TODAY)

        got = run_on_conn(name, go)
    rock, ice = _expected(days, 11, "rock"), _expected(days, 12, "ice")
    for m in range(1, 13):
        n, share = rock[m]
        assert got[(CRAG, "trad", m)].value == pytest.approx(share) and got[(CRAG, "trad", m)].get("n_days") == n
        n, share = ice[m]
        assert got[(ICE_AREA, "ice", m)].value == pytest.approx(share) and got[(ICE_AREA, "ice", m)].get("n_days") == n
    assert got[(ICE_AREA, "ice", 7)].value == 0.0
    assert got[(BARE, "sport", 7)].to_json() == {"value": None, "missing": True, "reason": "no_tz"}


@requires_pg
def test_no_history_is_missing_never_zero():
    with migrated_db(seed_sql=SEED) as name:
        async def go(conn):
            return await season(conn, await scope(conn), today=TODAY)

        got = run_on_conn(name, go)
    assert got[(CRAG, "trad", 1)].to_json() == {"value": None, "missing": True, "reason": "insufficient_history",
                                                "n_days": 0.0}


@requires_pg
def test_an_unexpected_scorable_type_group_is_refused():
    with migrated_db(seed_sql=SEED) as name:
        with pytest.raises(ValueError, match="outside"):
            run_on_conn(name, lambda conn: _scope_with_unknown(conn))


async def _scope_with_unknown(conn):
    from sqlalchemy import text

    await conn.execute(text("UPDATE canonical_routes SET scorable = true, unscorable_reason = NULL "
                            "WHERE route_id = 900000006"))
    return await scope(conn)
```

(The crag's NULL-tmax day on the 15th is excluded from rock days; the ice area's NULL days within the frozen spell leave ice days unknown only where they could change the answer. `_expected` re-derives both from the pure rules, so the test pins the SQL to `exposure_math`.)

- [ ] **Step 2: Run the tests to confirm they fail.** `cd backend && uv run pytest tests/test_exposure_components.py -q` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement.** `backend/app/pipelines/exposure_components.py`:

```python
"""Exposure component readers: one set-based query per component over the MP catalog (cells are
areas with directly attached scorable routes, DP5). Each returns a Component for every key of the
scope, so assembly fails loudly on a gap instead of filling one."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.exposure_math import (
    ICE_MIN_FROZEN, ICE_WINDOW, MIN_SEASON_DAYS, MONTHS, ROCK_MAX_PRECIP, ROCK_TMAX_HI, ROCK_TMAX_LO,
    RULE_BY_GROUP, SEASON_YEARS, TYPE_GROUPS, Component,
)

Key = tuple[int, str, int]
Scope = dict[tuple[int, str], int]
SERIES_CHUNK = 500

SCOPE_SQL = text("SELECT area_id, type_group, count(*) FROM scorable_routes GROUP BY 1, 2")
AREA_SERIES_SQL = text(
    "SELECT a.area_id, f.grid_bucket, f.tz FROM canonical_areas a JOIN feature_points f ON f.point_key = a.point_key "
    "WHERE a.area_id = ANY(CAST(:areas AS bigint[]))")
# Window frame by calendar days: a day absent from the series is unknown, and ice_day's
# (ICE_WINDOW - known) term counts it so, exactly as exposure_math.ice_day does.
SEASON_SQL = text(f"""
WITH s AS (
  SELECT * FROM unnest(CAST(:buckets AS integer[]), CAST(:tzs AS text[])) AS s(grid_bucket, tz)
), d AS (
  SELECT c.grid_bucket, c.tz, c.date,
         CASE WHEN c.tmax IS NULL OR c.precip_mm IS NULL THEN NULL
              ELSE c.tmax BETWEEN {ROCK_TMAX_LO} AND {ROCK_TMAX_HI} AND c.precip_mm < {ROCK_MAX_PRECIP} END AS rock,
         count(*) FILTER (WHERE c.tmax < 0) OVER w AS frozen,
         count(c.tmax) OVER w AS known
  FROM cell_daily_conditions c JOIN s ON s.grid_bucket = c.grid_bucket AND s.tz = c.tz
  WHERE c.record_kind = 'era5' AND c.date >= :since AND c.date < :until
  WINDOW w AS (PARTITION BY c.grid_bucket, c.tz ORDER BY c.date
               RANGE BETWEEN INTERVAL '{ICE_WINDOW - 1} days' PRECEDING AND CURRENT ROW)
), k AS (
  SELECT grid_bucket, tz, extract(month FROM date)::int AS month, rock,
         CASE WHEN frozen >= {ICE_MIN_FROZEN} THEN true
              WHEN frozen + ({ICE_WINDOW} - known) < {ICE_MIN_FROZEN} THEN false END AS ice
  FROM d
)
SELECT k.grid_bucket, k.tz, k.month, r.rule, count(r.ok) AS n_days, avg(r.ok::int) AS share
FROM k CROSS JOIN LATERAL (VALUES ('rock', k.rock), ('ice', k.ice), ('alpine', k.rock OR k.ice)) AS r(rule, ok)
GROUP BY 1, 2, 3, 4
""")


async def scope(conn: AsyncConnection) -> Scope:
    out = {(int(a), str(tg)): int(n) for a, tg, n in (await conn.execute(SCOPE_SQL)).all()}
    bad = sorted({tg for _, tg in out} - set(TYPE_GROUPS))
    if bad:
        raise ValueError(f"scorable type groups outside {TYPE_GROUPS}: {bad}")
    return out


def keys(scope_: Scope) -> list[Key]:
    return sorted((a, tg, m) for (a, tg) in scope_ for m in MONTHS)


def route_density(scope_: Scope) -> dict[Key, Component]:
    return {(a, tg, m): Component.present(n) for (a, tg), n in scope_.items() for m in MONTHS}


async def season(conn: AsyncConnection, scope_: Scope, *, today: date) -> dict[Key, Component]:
    """Time-invariant climate covariate (DP4): the latest SEASON_YEARS of ERA5 days before today,
    whatever the snapshot's data cutoff, because era5_window prunes older non-incident rows."""
    areas = sorted({a for a, _ in scope_})
    series: dict[int, tuple[int, str] | None] = {
        int(a): ((int(b), str(tz)) if tz is not None else None)
        for a, b, tz in (await conn.execute(AREA_SERIES_SQL, {"areas": areas})).all()
    }
    wanted = sorted({s for s in series.values() if s is not None})
    since = today - timedelta(days=SEASON_YEARS * 365)
    shares: dict[tuple[int, str, int, str], tuple[int, float | None]] = defaultdict(lambda: (0, None))
    for i in range(0, len(wanted), SERIES_CHUNK):
        chunk = wanted[i : i + SERIES_CHUNK]
        rows = (await conn.execute(SEASON_SQL, {"buckets": [b for b, _ in chunk], "tzs": [t for _, t in chunk],
                                                 "since": since, "until": today})).all()
        for b, tz, m, rule, n, share in rows:
            shares[(int(b), str(tz), int(m), str(rule))] = (int(n), float(share) if share is not None else None)
    out: dict[Key, Component] = {}
    for a, tg in scope_:
        point = series.get(a)
        for m in MONTHS:
            if a not in series:
                out[(a, tg, m)] = Component.absent("no_point")
            elif point is None:
                out[(a, tg, m)] = Component.absent("no_tz")
            else:
                n, share = shares[(point[0], point[1], m, RULE_BY_GROUP[tg])]
                out[(a, tg, m)] = (Component.present(share, n_days=n) if share is not None and n >= MIN_SEASON_DAYS
                                   else Component.absent("insufficient_history", n_days=n))
    return out
```

(`no_point`: a scorable area always has a `point_key`, but the point can be absent from `feature_points` until C-2's `points` step has run.)

- [ ] **Step 4: Run the tests.** `cd backend && uv run pytest tests/test_exposure_components.py -q && uv run mypy` → PASS.

- [ ] **Step 5: Commit.** `git add backend/app/pipelines/exposure_components.py backend/tests/test_exposure_components.py backend/pyproject.toml && git commit -m "feat(pipelines): exposure cells, route density, season share from ERA5"`

---

### Task 7: `mp_ticks` with coverage from the seam

**Files:**
- Modify: `backend/app/pipelines/exposure_components.py`, `backend/tests/test_exposure_components.py`

**Interfaces:**
- Consumes: `internal.route_tick_coverage(route_id, tick_status, complete_through, …)`, `internal.route_tick_months(route_id, period, style, tick_count)`, `tick_component`.
- Produces: `async mp_ticks(conn, scope_, *, enabled: bool, cutoff: date) -> dict[Key, Component]`, with missing reasons `disabled`, `table_dropped` and `no_complete_routes`.

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_exposure_components.py`:

```python
from app.pipelines.exposure_components import mp_ticks

# Crag: 900000001 complete through 2026-08-31, 900000002 scraped (incomplete), 900000003 never scraped.
# Ice: 900000004 complete through 2026-01-31.
TICKS = """
INSERT INTO internal.route_tick_scrapes (mp_route_id, source, scraped_at, complete, complete_through, total_ticks) VALUES
  (900000001, 'mp_scrape', '2026-09-10T00:00:00+00', true, DATE '2026-08-31', 18),
  (900000002, 'mp_scrape', '2026-09-10T00:00:00+00', false, NULL, NULL),
  (900000004, 'mp_scrape', '2026-02-10T00:00:00+00', true, DATE '2026-01-31', 6);
INSERT INTO internal.mp_tick_aggregates (mp_route_id, period, style, tick_count) VALUES
  (900000001, '2021-07', 'lead', 1), (900000001, '2025-07', 'lead', 4), (900000001, '2025-07', 'follow', 5),
  (900000001, '2026-07', 'lead', 3), (900000001, '2026-08', 'lead', 2), (900000001, '2026-09', 'lead', 7),
  (900000001, '2021-08', 'lead', 3),
  (900000002, '2025-07', 'lead', 50),
  (900000004, '2025-01', 'lead', 4), (900000004, '2026-01', 'lead', 2);
"""


def _ticks(name, *, enabled=True, cutoff=TODAY):
    async def go(conn):
        return await mp_ticks(conn, await scope(conn), enabled=enabled, cutoff=cutoff)

    return run_on_conn(name, go)


@requires_pg
def test_partial_coverage_is_raw_and_counted():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        got = _ticks(name)
    july = got[(CRAG, "trad", 7)].to_json()
    # 900000001's window is 2021-09 .. 2026-08: July occurs as 2022..2026 -> (4 + 5 + 3) / 5; 2021-07 is outside.
    assert july == {"value": 12 / 5, "missing": False, "coverage": 1 / 3, "n_complete": 1.0, "n_scraped": 1.0,
                    "n_missing": 1.0, "per_route_mean": 12 / 5, "window_months": 60.0}
    assert got[(CRAG, "trad", 8)].value == 2 / 5
    assert got[(CRAG, "trad", 1)].value == 0.0


@requires_pg
def test_no_complete_route_is_missing_not_zero():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        got = _ticks(name)
    assert got[(BARE, "sport", 7)].to_json() == {"value": None, "missing": True, "reason": "no_complete_routes",
                                                 "coverage": 0.0, "n_complete": 0.0, "n_scraped": 0.0,
                                                 "n_missing": 1.0}


@requires_pg
def test_cutoff_and_complete_through_bound_the_window():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        mid = _ticks(name, cutoff=date(2026, 7, 20))
        ice = _ticks(name)
    # Cutoff 2026-07-20: the last complete month before it is 2026-06, so the window is 2021-07..2026-06.
    assert mid[(CRAG, "trad", 7)].value == (1 + 4 + 5) / 5
    assert mid[(CRAG, "trad", 8)].value == 3 / 5
    # The ice route's window ends at its complete_through (2026-01), not at today's cutoff.
    assert ice[(ICE_AREA, "ice", 1)].value == (4 + 2) / 5 and ice[(ICE_AREA, "ice", 1)].get("coverage") == 1.0


@requires_pg
def test_disabled_and_dropped_are_missing_with_no_coverage():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        off = _ticks(name, enabled=False)
        from tests.pgtest import run_sql

        run_sql(name, "DROP VIEW internal.route_tick_months")
        gone = _ticks(name)
    assert off[(CRAG, "trad", 7)].to_json() == {"value": None, "missing": True, "reason": "disabled"}
    assert gone[(CRAG, "trad", 7)].to_json() == {"value": None, "missing": True, "reason": "table_dropped"}
```

(The `2026-09` row of 900000001 lies after its `complete_through`, so the 0008 view never exposes it. The incomplete route's 50 ticks are never read.)

- [ ] **Step 2: Run the tests to confirm they fail.** `cd backend && uv run pytest tests/test_exposure_components.py -q -k "coverage or complete or window or disabled"` → FAIL (`ImportError: mp_ticks`).

- [ ] **Step 3: Implement.** Append to `exposure_components.py`, adding `TICK_WINDOW_MONTHS` and `tick_component` to the `exposure_math` import:

```python
STATUS_SQL = text(
    "SELECT s.area_id, s.type_group, c.tick_status, count(*) FROM scorable_routes s "
    "JOIN internal.route_tick_coverage c ON c.route_id = s.route_id GROUP BY 1, 2, 3")
# Each complete route's window is the TICK_WINDOW_MONTHS months ending at the last month that is both
# within complete_through and fully before the cutoff (DP3); route_tick_months already hides every
# month after complete_through, so an absent row inside the window is a real zero.
WINDOW_TICKS_SQL = text(f"""
WITH lim AS (
  SELECT s.route_id, s.area_id, s.type_group,
         LEAST(c.complete_through, (date_trunc('month', CAST(:cutoff AS date)) - interval '1 day')::date) AS last_day
  FROM scorable_routes s JOIN internal.route_tick_coverage c ON c.route_id = s.route_id
  WHERE c.tick_status = 'complete'
), m AS (
  SELECT route_id, to_date(period || '-01', 'YYYY-MM-DD') AS first_day, tick_count FROM internal.route_tick_months
)
SELECT l.area_id, l.type_group, extract(month FROM m.first_day)::int AS month, sum(m.tick_count) AS ticks
FROM lim l JOIN m ON m.route_id = l.route_id
WHERE m.first_day <= l.last_day
  AND m.first_day > (date_trunc('month', l.last_day) - interval '{TICK_WINDOW_MONTHS} months')::date
GROUP BY 1, 2, 3
""")


async def _exists(conn: AsyncConnection, name: str) -> bool:
    return (await conn.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name})).scalar() is True


async def mp_ticks(conn: AsyncConnection, scope_: Scope, *, enabled: bool, cutoff: date) -> dict[Key, Component]:
    if not enabled:
        return {k: Component.absent("disabled") for k in keys(scope_)}
    if not (await _exists(conn, "internal.route_tick_months") and await _exists(conn, "internal.route_tick_coverage")):
        return {k: Component.absent("table_dropped") for k in keys(scope_)}
    statuses: dict[tuple[int, str], dict[str, int]] = defaultdict(dict)
    for a, tg, status, n in (await conn.execute(STATUS_SQL)).all():
        statuses[(int(a), str(tg))][str(status)] = int(n)
    sums = {(int(a), str(tg), int(m)): float(n)
            for a, tg, m, n in (await conn.execute(WINDOW_TICKS_SQL, {"cutoff": cutoff})).all()}
    out: dict[Key, Component] = {}
    for (a, tg), n_routes in scope_.items():
        st = statuses[(a, tg)]
        for m in MONTHS:
            out[(a, tg, m)] = tick_component(
                n_routes=n_routes, n_complete=st.get("complete", 0), n_scraped=st.get("scraped", 0),
                n_missing=st.get("missing", 0), window_ticks=sums.get((a, tg, m), 0.0))
    return out
```

(`sums.get(..., 0.0)` is used only when `n_complete ≥ 1`: there it is the measured zero the seam contract defines. When `n_complete = 0`, `tick_component` returns missing and ignores the value.)

- [ ] **Step 4: Run the tests.** `cd backend && uv run pytest tests/test_exposure_components.py -q && uv run mypy` → PASS.

- [ ] **Step 5: Commit.** `git add backend/app/pipelines/exposure_components.py backend/tests/test_exposure_components.py && git commit -m "feat(pipelines): mp_ticks exposure component with per-cell coverage from the tick seam"`

---

### Task 8: Snapshot build, validation, CLI, removal drill, no-API guard

**Files:**
- Create: `backend/app/pipelines/exposure.py`, `backend/tests/test_exposure.py`, `backend/tests/test_no_exposure_in_api.py`
- Modify: `backend/app/pipelines/ingest_log.py`, `backend/pyproject.toml` (strict block: `app.pipelines.exposure`)

**Interfaces:**
- Consumes: Tasks 5–7; `ingest_log.start_run/finish_run`; `Settings.EXPOSURE_ENABLE_MP_TICKS`.
- Produces:
  - constants: `SOURCE = "exposure"`, `KEEP_SNAPSHOTS = 3`, `INSERT_CHUNK = 5000`;
  - `async build(conn, *, today: date, cutoff: date | None = None, mp_enabled: bool, run_id: uuid.UUID | None = None, write: bool = True) -> dict[str, object]`. It returns `cells`, `null_points`, `as_of`, `data_cutoff`, `mp_enabled`, `proxy_version`, and `digests: dict[str, str]` (a sha256 per component). It raises `ValueError` when the cutoff is after today.
  - `async validate(conn, *, as_of: date, data_cutoff: date) -> dict[str, float]`, returning `cells`, `null_points`, `full_coverage_cells`, `tick_coverage_mean` and `spearman_point_ticks`; the last two are omitted when undefined.
  - `async drop_snapshot(conn, *, as_of: date, data_cutoff: date) -> int`.
  - CLI `python -m app.pipelines.exposure build [--cutoff YYYY-MM-DD] [--dry-run]` and `python -m app.pipelines.exposure drop --as-of YYYY-MM-DD --cutoff YYYY-MM-DD`.
  - `ingest_log._ALLOWED_METRICS` gains `cells`, `null_points`, `full_coverage_cells`, `tick_coverage_mean`, `spearman_point_ticks`.

- [ ] **Step 1: Write the failing tests.** `backend/tests/test_exposure.py`:

```python
import asyncio
from datetime import date

import pytest
from sqlalchemy import text

from app.pipelines.exposure import build, drop_snapshot, validate
from tests.pgtest import fetch, migrated_db, requires_pg, run_on_conn
from tests.test_exposure_components import CRAG, SEED, TICKS, TODAY, _climate

CLIMATE = _climate()[0]


@requires_pg
def test_snapshot_rows_components_coverage_and_bounds():
    with migrated_db(seed_sql=SEED + TICKS + CLIMATE) as name:
        run_on_conn(name, lambda conn: build(conn, today=TODAY, mp_enabled=True))
        [(proxy, cov, point, lo, hi, cutoff, version)] = fetch(
            name, "SELECT proxy, tick_coverage, exposure_point, exposure_lo, exposure_hi, data_cutoff, proxy_version "
                  f"FROM internal.exposure_index WHERE area_id = {CRAG} AND type_group = 'trad' AND month = 7")
    import json

    proxy = json.loads(proxy) if isinstance(proxy, str) else proxy
    assert set(proxy) == {"route_density", "season_share", "mp_ticks"} and version == "x-mp-v1"
    assert cutoff == TODAY and cov == pytest.approx(1 / 3)
    assert point == pytest.approx(3 * proxy["season_share"]["value"], rel=1e-6)
    assert (lo, hi) == pytest.approx((point / 3, point * 3), rel=1e-6)


@requires_pg
def test_missing_season_gives_null_point_and_bounds():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        run_on_conn(name, lambda conn: build(conn, today=TODAY, mp_enabled=True))
        rows = fetch(name, "SELECT count(*) FILTER (WHERE exposure_point IS NOT NULL), count(*) "
                           "FROM internal.exposure_index")
    assert rows == [(0, 36)]


@requires_pg
def test_removal_drill_changes_only_mp_ticks():
    with migrated_db(seed_sql=SEED + TICKS + CLIMATE) as name:
        on = run_on_conn(name, lambda conn: build(conn, today=TODAY, mp_enabled=True, write=False))
        off = run_on_conn(name, lambda conn: build(conn, today=TODAY, mp_enabled=False, write=False))
        assert fetch(name, "SELECT count(*) FROM internal.exposure_index") == [(0,)]
        run_on_conn(name, lambda conn: build(conn, today=TODAY, cutoff=date(2026, 9, 1), mp_enabled=False))
        assert fetch(name, "SELECT count(*) FROM internal.exposure_index WHERE tick_coverage IS NOT NULL "
                           "OR proxy->'mp_ticks'->>'reason' <> 'disabled'") == [(0,)]
    assert on["digests"]["route_density"] == off["digests"]["route_density"]
    assert on["digests"]["season_share"] == off["digests"]["season_share"]
    assert on["digests"]["mp_ticks"] != off["digests"]["mp_ticks"]


@requires_pg
def test_backtest_snapshots_survive_production_pruning_and_can_be_dropped():
    with migrated_db(seed_sql=SEED + TICKS) as name:
        async def go(conn):
            await build(conn, today=date(2026, 5, 1), cutoff=date(2025, 1, 1), mp_enabled=True)
            for day in (date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)):
                await build(conn, today=day, mp_enabled=True)
            before = (await conn.execute(text(
                "SELECT DISTINCT as_of, data_cutoff FROM internal.exposure_index ORDER BY 1"))).all()
            dropped = await drop_snapshot(conn, as_of=date(2026, 5, 1), data_cutoff=date(2025, 1, 1))
            return [tuple(r) for r in before], dropped

        before, dropped = run_on_conn(name, go)
    assert before == [(date(2026, 5, 1), date(2025, 1, 1)), (date(2026, 7, 1), date(2026, 7, 1)),
                      (date(2026, 8, 1), date(2026, 8, 1)), (date(2026, 9, 1), date(2026, 9, 1))]
    assert dropped == 36


@requires_pg
def test_validate_reports_counts_and_omits_undefined_spearman():
    with migrated_db(seed_sql=SEED + TICKS + CLIMATE) as name:
        async def go(conn):
            await build(conn, today=TODAY, mp_enabled=True)
            return await validate(conn, as_of=TODAY, data_cutoff=TODAY)

        got = run_on_conn(name, go)
    assert got["cells"] == 36 and got["full_coverage_cells"] == 12
    assert "spearman_point_ticks" in got or got["full_coverage_cells"] < 3


def test_cutoff_after_today_is_refused():
    with pytest.raises(ValueError, match="after today"):
        asyncio.run(build(None, today=TODAY, cutoff=date(2026, 10, 16), mp_enabled=True))  # type: ignore[arg-type]
```

`backend/tests/test_no_exposure_in_api.py`:

```python
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "app" / "api"


def test_no_endpoint_reads_the_exposure_index():
    assert [str(p) for p in API.rglob("*.py") if "exposure_index" in p.read_text(encoding="utf-8")] == []
```

- [ ] **Step 2: Run the tests to confirm they fail.** `cd backend && uv run pytest tests/test_exposure.py -q` → FAIL (`ModuleNotFoundError: app.pipelines.exposure`).

- [ ] **Step 3: Implement.** In `backend/app/pipelines/ingest_log.py`:

```python
# Run-level numbers a job may add (hand-check agreement, rows left for owner review, R6's
# sample size/correlation/gust percentile, the exposure snapshot's counts and Spearman). A new
# metric joins this set in a reviewed change rather than riding a raw jsonb UPDATE (G13).
_ALLOWED_METRICS = frozenset({"agreement", "review_pending", "n", "r_tmax", "p99_gust", "cells", "null_points",
                              "full_coverage_cells", "tick_coverage_mean", "spearman_point_ticks"})
```

`backend/app/pipelines/exposure.py`:

```python
"""Exposure snapshots (P2-9 on the MP catalog): assemble the components per area x type group x
month, write one snapshot keyed (as_of, data_cutoff), keep the newest KEEP_SNAPSHOTS production
snapshots (data_cutoff = as_of) and every backtest snapshot until dropped. Runs as ingest from the
owner's Mac after tick loads (DP8); never served by the API."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.exposure_components import keys, mp_ticks, route_density, scope, season
from app.pipelines.exposure_math import COMPONENTS, PROXY_VERSION, Component, bounds, exposure_point, spearman

SOURCE = "exposure"
KEEP_SNAPSHOTS = 3
INSERT_CHUNK = 5000

UPSERT_SQL = text(
    "INSERT INTO internal.exposure_index (area_id, type_group, month, n_routes, proxy, proxy_version, as_of, "
    "data_cutoff, tick_coverage, exposure_point, exposure_lo, exposure_hi, run_id) VALUES (:a, :tg, :m, :n, "
    "CAST(:p AS jsonb), :v, :d, :c, :cov, :pt, :lo, :hi, :run) "
    "ON CONFLICT (area_id, type_group, month, as_of, data_cutoff) DO UPDATE SET n_routes = EXCLUDED.n_routes, "
    "proxy = EXCLUDED.proxy, proxy_version = EXCLUDED.proxy_version, tick_coverage = EXCLUDED.tick_coverage, "
    "exposure_point = EXCLUDED.exposure_point, exposure_lo = EXCLUDED.exposure_lo, "
    "exposure_hi = EXCLUDED.exposure_hi, run_id = EXCLUDED.run_id")
# A rebuild of the same snapshot must not keep cells whose area left the scope.
CLEAR_SQL = text("DELETE FROM internal.exposure_index WHERE as_of = :d AND data_cutoff = :c")
PRUNE_SQL = text(
    "DELETE FROM internal.exposure_index WHERE data_cutoff = as_of AND as_of NOT IN "
    "(SELECT DISTINCT as_of FROM internal.exposure_index WHERE data_cutoff = as_of ORDER BY as_of DESC LIMIT :k)")
VALIDATE_SQL = text(
    "SELECT exposure_point, tick_coverage, (proxy->'mp_ticks'->>'value')::float8 FROM internal.exposure_index "
    "WHERE as_of = :d AND data_cutoff = :c")


def _digest(part: dict[tuple[int, str, int], Component]) -> str:
    h = hashlib.sha256()
    for key in sorted(part):
        h.update(json.dumps([list(key), part[key].to_json()], sort_keys=True).encode())
    return h.hexdigest()


async def build(conn: AsyncConnection, *, today: date, cutoff: date | None = None, mp_enabled: bool,
                run_id: uuid.UUID | None = None, write: bool = True) -> dict[str, object]:
    cut = cutoff or today
    if cut > today:
        raise ValueError(f"data cutoff {cut} is after today {today}")
    s = await scope(conn)
    parts: dict[str, dict[tuple[int, str, int], Component]] = {
        "route_density": route_density(s),
        "season_share": await season(conn, s, today=today),
        "mp_ticks": await mp_ticks(conn, s, enabled=mp_enabled, cutoff=cut),
    }
    rows = []
    for key in keys(s):
        area, tg, month = key
        comps = {name: parts[name][key] for name in COMPONENTS}
        point = exposure_point(comps["route_density"].value, comps["season_share"].value)
        lo, hi = bounds(point)
        rows.append({"a": area, "tg": tg, "m": month, "n": s[(area, tg)],
                     "p": json.dumps({name: c.to_json() for name, c in comps.items()}, sort_keys=True),
                     "v": PROXY_VERSION, "d": today, "c": cut, "cov": comps["mp_ticks"].get("coverage"),
                     "pt": point, "lo": lo, "hi": hi, "run": run_id})
    if write:
        await conn.execute(CLEAR_SQL, {"d": today, "c": cut})
        for i in range(0, len(rows), INSERT_CHUNK):
            await conn.execute(UPSERT_SQL, rows[i : i + INSERT_CHUNK])
        await conn.execute(PRUNE_SQL, {"k": KEEP_SNAPSHOTS})
    return {"cells": len(rows), "null_points": sum(1 for r in rows if r["pt"] is None), "as_of": today.isoformat(),
            "data_cutoff": cut.isoformat(), "mp_enabled": mp_enabled, "proxy_version": PROXY_VERSION,
            "digests": {name: _digest(parts[name]) for name in COMPONENTS}}


async def validate(conn: AsyncConnection, *, as_of: date, data_cutoff: date) -> dict[str, float]:
    rows = (await conn.execute(VALIDATE_SQL, {"d": as_of, "c": data_cutoff})).all()
    coverages = [float(c) for _, c, _ in rows if c is not None]
    full = [(float(p), float(t)) for p, c, t in rows if p is not None and t is not None and c == 1.0]
    out: dict[str, float] = {"cells": float(len(rows)), "null_points": float(sum(1 for p, _, _ in rows if p is None)),
                             "full_coverage_cells": float(sum(1 for c in coverages if c == 1.0))}
    if coverages:
        out["tick_coverage_mean"] = sum(coverages) / len(coverages)
    rho = spearman([p for p, _ in full], [t for _, t in full])
    if rho is not None:
        out["spearman_point_ticks"] = rho
    return out


async def drop_snapshot(conn: AsyncConnection, *, as_of: date, data_cutoff: date) -> int:
    result = await conn.execute(CLEAR_SQL, {"d": as_of, "c": data_cutoff})
    return int(result.rowcount or 0)


async def _run(args: argparse.Namespace, *, today: date, mp_enabled: bool) -> dict[str, object]:
    from app.pipelines.db import ingest_engine
    from app.pipelines.ingest_log import finish_run, start_run
    from app.pipelines.validate import ValidationReport

    engine = ingest_engine()
    try:
        if args.cmd == "drop":
            async with engine.begin() as conn:
                return {"dropped_rows": await drop_snapshot(conn, as_of=args.as_of, data_cutoff=args.cutoff)}
        if args.dry_run:
            async with engine.connect() as conn:
                summary = await build(conn, today=today, cutoff=args.cutoff, mp_enabled=mp_enabled, write=False)
                await conn.rollback()
                return {"status": "dry_run", **summary}
        async with engine.begin() as conn:
            cut = args.cutoff or today
            run_id = await start_run(conn, source=SOURCE, window_start=cut, window_end=today, content_sha256=None)
            summary = await build(conn, today=today, cutoff=args.cutoff, mp_enabled=mp_enabled, run_id=run_id)
            checks = await validate(conn, as_of=today, data_cutoff=cut)
            report = ValidationReport(SOURCE)
            report.rows_in = report.accepted = int(str(summary["cells"]))
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=report.accepted, metrics=checks)
            return {"status": "ok", **summary, **checks}
    finally:
        await engine.dispose()


def cli() -> None:
    from app.config import settings
    from app.services.temporal_weighting import utc_today

    parser = argparse.ArgumentParser(prog="python -m app.pipelines.exposure")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write one exposure snapshot")
    b.add_argument("--cutoff", type=date.fromisoformat, default=None,
                   help="count tick months only before this date (Phase 3 backtests); default today (UTC)")
    b.add_argument("--dry-run", action="store_true", help="compute and print the summary and digests; write nothing")
    d = sub.add_parser("drop", help="delete one snapshot")
    d.add_argument("--as-of", type=date.fromisoformat, required=True)
    d.add_argument("--cutoff", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    if args.cmd == "build":
        args.dry_run = bool(args.dry_run)
    result = asyncio.run(_run(args, today=utc_today(), mp_enabled=settings.EXPOSURE_ENABLE_MP_TICKS))
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    cli()
```

(A failure inside `build` rolls the whole transaction back, including the run's `running` log row, so a crashed run leaves nothing behind, as in the other one-transaction jobs. `drop` refuses nothing by design: it is the owner's tool for backtest snapshots.)

- [ ] **Step 4: Run the tests.** `cd backend && uv run pytest tests/test_exposure.py tests/test_no_exposure_in_api.py tests/test_ingest_log.py -q && uv run mypy && uv run ruff check .` → PASS.

- [ ] **Step 5: Commit.** `git add backend/app/pipelines/exposure.py backend/app/pipelines/ingest_log.py backend/tests/test_exposure.py backend/tests/test_no_exposure_in_api.py backend/pyproject.toml && git commit -m "feat(pipelines): exposure snapshots by data cutoff, validation metrics, removal drill, CLI"`

---

### Task 9: Acceptance cells, X-2 docs, Phase 4 renumbering

**Files:**
- Create: `backend/tests/verify/test_exposure.py`
- Modify: `CHANGELOG.md`, `CLAUDE.md`, `data/DATABASE_STRUCTURE.md`, `docs/superpowers/plans/2026-10-12-phase4-users-incident-reporting.md`

- [ ] **Step 1: Write the acceptance cells.** `backend/tests/verify/test_exposure.py`:

```python
"""Read-only exposure cells (exposure plan X-2), run with VERIFY against a branch or prod."""

import pytest

from tests.verify._db import fetch

pytestmark = pytest.mark.db
LATEST = ("(SELECT max(as_of) FROM internal.exposure_index WHERE data_cutoff = as_of)")


def test_latest_production_snapshot_is_consistent():
    [row] = fetch(
        "SELECT count(*) AS n, "
        "count(*) FILTER (WHERE (exposure_point IS NULL) <> (exposure_lo IS NULL OR exposure_hi IS NULL)) AS bounds_bad, "
        "count(*) FILTER (WHERE (proxy->'season_share'->>'missing')::boolean AND exposure_point IS NOT NULL) AS floored, "
        "count(*) FILTER (WHERE (proxy->'mp_ticks'->>'missing')::boolean AND proxy->'mp_ticks'->>'value' IS NOT NULL) "
        "  AS tick_value_on_missing, "
        "count(*) FILTER (WHERE NOT (proxy->'mp_ticks'->>'missing')::boolean "
        "  AND (proxy->'mp_ticks'->>'n_complete')::float < 1) AS present_without_complete, "
        "count(*) FILTER (WHERE tick_coverage IS DISTINCT FROM (proxy->'mp_ticks'->>'coverage')::real) AS coverage_drift "
        f"FROM internal.exposure_index WHERE data_cutoff = as_of AND as_of = {LATEST}")
    assert row["n"] > 0
    for k in ("bounds_bad", "floored", "tick_value_on_missing", "present_without_complete", "coverage_drift"):
        assert row[k] == 0, k


def test_cells_match_the_scorable_catalog():
    [row] = fetch(
        "SELECT (SELECT count(*) * 12 FROM (SELECT DISTINCT area_id, type_group FROM scorable_routes) s) AS expected, "
        f"(SELECT count(*) FROM internal.exposure_index WHERE data_cutoff = as_of AND as_of = {LATEST}) AS got")
    assert row["expected"] == row["got"]


def test_app_cannot_read_the_exposure_index():
    [row] = fetch("SELECT has_table_privilege('app', 'internal.exposure_index', 'SELECT') AS can")
    assert row["can"] is False


def test_coverage_by_type_group_is_reported():
    rows = fetch(
        "SELECT type_group, count(*) AS cells, avg(tick_coverage) AS mean_cov, "
        "count(*) FILTER (WHERE tick_coverage = 1) AS full, count(*) FILTER (WHERE exposure_point IS NULL) AS null_points "
        f"FROM internal.exposure_index WHERE data_cutoff = as_of AND as_of = {LATEST} AND month = 1 GROUP BY 1 ORDER BY 1")
    assert rows
    for r in rows:
        print(r["type_group"], r["cells"], r["mean_cov"], r["full"], r["null_points"])
```

- [ ] **Step 2: Docs.**
  - **`CHANGELOG.md`:** a dated "Exposure index (PR X-2)" entry.
  - **`CLAUDE.md`:**
    - Migration list: append `0011_exposure_index` (`internal.exposure_index`; refuses to downgrade while it holds rows).
    - Commands: `uv run python -m app.pipelines.exposure build [--cutoff YYYY-MM-DD] [--dry-run] | drop --as-of D --cutoff D`, as `ingest`. Production snapshots: the newest 3 are kept. Backtest snapshots stay until dropped. `EXPOSURE_ENABLE_MP_TICKS=false` is the removal drill. `exposure_index` lives in `internal` and is never served.
    - The mypy allowlist: the three new pipeline modules and `app.models.exposure`.
  - **`data/DATABASE_STRUCTURE.md`:** `internal.exposure_index`:
    - columns and PK;
    - the proxy components and their missing reasons (`no_point`, `no_tz`, `insufficient_history`, `no_complete_routes`, `disabled`, `table_dropped`);
    - tick coverage semantics (raw sum over complete routes, never scaled; a 60-month window; the scrape-order bias);
    - `season_share` as time-invariant; climb dates vs log dates;
    - "app has no access; analyst reads; trainer from Phase 3".

- [ ] **Step 3: Run the checks.** `cd backend && uv run pytest -q && uv run mypy && uv run ruff check . ../scripts/ && cd .. && python3 scripts/check_no_scrapers.py && python3 scripts/check_no_mp_data.py` → green. The `-m db` cells are exercised in Task 10.

- [ ] **Step 4: Renumber Phase 4.** In `docs/superpowers/plans/2026-10-12-phase4-users-incident-reporting.md`, replace in this order (highest first, so no replacement feeds the next):

| Find | Replace |
|---|---|
| `0010_report_promotions` | `0014_report_promotions` |
| `0009_reports` | `0013_reports` |
| `0008_accounts` | `0012_accounts` |
| `test_migration_0010` | `test_migration_0014` |
| `test_migration_0009` | `test_migration_0013` |
| `test_migration_0008` | `test_migration_0012` |
| `0008_creates_accounts_tables_and_seeds_settings` | `0012_creates_accounts_tables_and_seeds_settings` |

Then set its first migration's `down_revision` to `"0011_exposure_index"`, and change its runbook's expected first `MIG current` line to `0011_exposure_index`. Then check:
- `grep -n "0007_cell_daily_conditions\|0010_static_features" docs/superpowers/plans/2026-10-12-phase4-users-incident-reporting.md` lists only references that describe older revisions. Fix any `down_revision` or "prod is at" line it shows.
- `grep -c "0008_\|0009_\|0010_" …` is 0 outside those described references.

Add a revision note at the top of that plan: "Renumbered 2026-10-15: the exposure plan takes 0011."

- [ ] **Step 5: Commit.** `git add backend/tests/verify/test_exposure.py CHANGELOG.md CLAUDE.md data/DATABASE_STRUCTURE.md docs/superpowers/plans/2026-10-12-phase4-users-incident-reporting.md && git commit -m "docs: exposure index docs and acceptance cells; Phase 4 renumbered to 0012-0014"`

---

### Task 10: OWNER/AGENT RUNBOOK — migrate `0011`, first snapshot, removal drill (branch, then prod)

The Shell state, Secrets, Long runs and Gates rules are as in Task 3. **Owner does:** the Step 3 sign-off and the go for Step 4. Prerequisite: prod at `0010_accident_route_links`, and X-2 on the main checkout.

The re-entry block is Task 3's with `B=xp-2-rehearsal`, without the `EXPORT` line. The prod preamble is Task 3's with `GATE_DIR=../data/review/gates_xp2_prod`.

- [ ] **Step 0 (agent): Checkout and tools.** As Task 3 Step 0 (agent part). `uv run alembic heads` prints exactly `0011_exposure_index (head)`.

- [ ] **Step 1 (agent): Branch `xp-2-rehearsal`; migrate; grants**

```bash
B=xp-2-rehearsal
neonctl branches create --project-id still-morning-74008008 --name "${B:?}" --parent production --no-secrets --output json \
  | python3 -c 'import json,sys; b=json.load(sys.stdin)["branch"]; print(b["id"], b["name"], b["parent_id"])'
```

Then run the re-entry block, then:

```bash
MIG current
MIG upgrade 0011_exposure_index
MIG current
OWNER_PSQL -q -f db/roles/grants_phase2.sql
OWNER_PSQL -q -f db/roles/verify_roles.sql
OWNER_PSQL -q -f db/roles/verify_roles_phase2.sql
MIG check
```

Expected, in order:
1. `0010_accident_route_links`.
2. `0011_exposure_index (head)`.
3. `phase 2 grants applied`.
4. `ALL ROLE CHECKS PASSED`.
5. `ALL PHASE 2 ROLE CHECKS PASSED`.
6. `No new upgrade operations detected.`

Anything else: STOP.

- [ ] **Step 2 (agent): Dry run, removal drill, build on the branch**

```bash
save_once ../data/review/xp2_dry_branch.json INGMOD app.pipelines.exposure build --dry-run
save_once ../data/review/xp2_drill_branch.json env EXPOSURE_ENABLE_MP_TICKS=false INGMOD app.pipelines.exposure build --dry-run
python3 - <<'EOF'
import json
on = json.load(open("../data/review/xp2_dry_branch.json")); off = json.load(open("../data/review/xp2_drill_branch.json"))
assert on["cells"] == off["cells"] and off["mp_enabled"] is False
assert on["digests"]["route_density"] == off["digests"]["route_density"]
assert on["digests"]["season_share"] == off["digests"]["season_share"]
assert on["digests"]["mp_ticks"] != off["digests"]["mp_ticks"]
print("removal drill: only mp_ticks changes")
EOF
bg_start ../data/review/logs/xp2_build_branch.log INGMOD app.pipelines.exposure build
```

```bash
bg_wait ../data/review/logs/xp2_build_branch.log
```

```bash
save_once ../data/review/xp2_build_branch.json grep '^{' ../data/review/logs/xp2_build_branch.log
VERIFY tests/verify/test_exposure.py tests/verify/test_tick_seam.py -q -s
ANALYST_PSQL -At -c "SELECT pg_size_pretty(pg_total_relation_size('internal.exposure_index'))"
```

Expected:
- The dry run prints `cells` = 12 × the scorable (area, type group) pairs, plus `null_points`.
- The drill prints `removal drill: only mp_ticks changes`.
- The build prints `status: ok` with the same `cells`, plus `tick_coverage_mean`, `full_coverage_cells` and (when defined) `spearman_point_ticks`.
- The verify cells all pass and print coverage by type group.
- The size is recorded; the D-11 estimate is about 120 MB.

A high `null_points` means route-point ERA5 series are not filled yet. It is reported, never floored.

- [ ] **Step 3 (agent, then STOP for the owner): Branch evidence**

The agent pastes:
- the dry-run summary;
- the drill line;
- the build JSON (counts, coverage mean, Spearman);
- the per-type-group coverage table;
- the table size.

All are counts, never names or ids. **STOP: owner review.** Continue only on the owner's written "exposure ok".

- [ ] **Step 4 (owner go, then agent): Prod**

4a — gate (read-only, fails closed): as Task 3's 4a, except that prod must read exactly `0010_accident_route_links` (with or without ` (head)`), the branch evidence file is `../data/review/xp2_build_branch.json`, and the gate is `gate_pass xp2_migrate`.

4a-snap — snapshot `pre-xp-2`: as Task 3's 4a-snap, with `gate_require xp2_migrate`, the name `pre-xp-2` and `gate_pass xp2_snapshot`.

4b — migrate, grants, check:

```bash
( cd /Users/sebastianfrazier/Developer/SafeAscent/backend && . scripts/runbook_helpers.sh && export TARGET_HOST=production GATE_DIR=../data/review/gates_xp2_prod || exit 1
  # PRODUCTION
  gate_require xp2_migrate || exit 1
  gate_require xp2_snapshot || exit 1
  MIG upgrade 0011_exposure_index || exit 1
  cur="$(MIG current)" || exit 1
  printf '%s\n' "$cur"
  case "$cur" in 0011_exposure_index | "0011_exposure_index (head)") ;; *) echo "prod is not at 0011: STOP" >&2; exit 1 ;; esac
  OWNER_PSQL -q -f db/roles/grants_phase2.sql || exit 1
  OWNER_PSQL -q -f db/roles/verify_roles.sql || exit 1
  OWNER_PSQL -q -f db/roles/verify_roles_phase2.sql || exit 1
  MIG check || exit 1
  gate_pass xp2_migrated )
```

4c — gate: the prod dry run must match the branch's within catalog drift:

```bash
( cd /Users/sebastianfrazier/Developer/SafeAscent/backend && . scripts/runbook_helpers.sh && export TARGET_HOST=production GATE_DIR=../data/review/gates_xp2_prod || exit 1
  # PRODUCTION (read-only dry run)
  gate_require xp2_migrated || exit 1
  save_once ../data/review/xp2_dry_prod.json INGMOD app.pipelines.exposure build --dry-run || exit 1
  python3 - <<'EOF' || exit 1
import json
b = json.load(open("../data/review/xp2_dry_branch.json")); p = json.load(open("../data/review/xp2_dry_prod.json"))
assert p["status"] == "dry_run" and p["proxy_version"] == b["proxy_version"], p
assert abs(p["cells"] - b["cells"]) <= 0.01 * b["cells"], (b["cells"], p["cells"])
print("prod dry run matches branch")
EOF
  gate_pass xp2_build )
```

4d — build prod:

```bash
( cd /Users/sebastianfrazier/Developer/SafeAscent/backend && . scripts/runbook_helpers.sh && export TARGET_HOST=production GATE_DIR=../data/review/gates_xp2_prod || exit 1
  gate_require xp2_build || exit 1
  bg_start ../data/review/logs/xp2_build_prod.log INGMOD app.pipelines.exposure build )
```

Then, with the prod preamble:
- `bg_wait ../data/review/logs/xp2_build_prod.log` until `status: ok`;
- `VERIFY tests/verify/test_exposure.py -q -s` → all pass.

The PR records the build JSON and the snapshot id. The code deploys after 4b: no API reads the table, so deploy order is free.

- [ ] **Step 5 (agent, recurring): Rebuilds** `[default pending: DP8]`. After every rock load (Task 3 Step 5), and at least monthly, run with the prod preamble:
  - `INGMOD app.pipelines.exposure build`;
  - `VERIFY tests/verify/test_exposure.py -q -s`;
  - paste the per-type-group coverage lines into the load's record.

  Backtest snapshots for Phase 3 are `INGMOD app.pipelines.exposure build --cutoff YYYY-MM-DD`, dropped with `drop --as-of … --cutoff …` when no longer needed.

---

## Appendix A: Owner questions (`[default pending: DPn]`)

| DP | Question | Recommended default (this plan is written against it) |
|---|---|---|
| DP1 | Where does `exposure_index` live? | `internal` schema: `app` excluded by construction, and Phase 1's "app reads every public table" check is untouched. Phase 3 grants `trainer` USAGE on `internal` plus SELECT on this one table. |
| DP2 | How are partially scraped rock cells represented? | Raw sum over complete routes, plus `per_route_mean`, `coverage` and status counts. Present when ≥ 1 route is complete; never scaled up. Phase 3 must use coverage. |
| DP3 | Tick window? | The last 60 complete months per route before the cutoff (5 occurrences of each calendar month). |
| DP4 | Season share and the data cutoff? | Time-invariant: the latest 3 ERA5 years, at least 28 known days per month. Older ERA5 is pruned, so strict cutoffs would blank it in backtests. |
| DP5 | Cell scope? | Areas with directly attached scorable routes. Cells are disjoint and Phase 3 sums the hierarchy. |
| DP6 | Deferred components (NPS visits, permits, objective popularity, in-app ticks)? | Omitted from the proxy until their source lands, with a `PROXY_VERSION` bump each time. Not carried as permanently missing keys. |
| DP7 | One tick component or two? | One `mp_ticks` for every type group, with one removal flag. |
| DP8 | Where do loads and rebuilds run? | The owner's Mac, as `ingest`, through the runbook. Rebuild after each load and at least monthly. No GitHub Actions secret; not on `catalog-cycle`. |
| DP9 | Which credential? | The existing `ingest` role. The alternative is a new `tick_loader` role limited to the aggregates, seam and log. |
| DP10 | Load cadence and prod safety? | Weekly exports. A branch rehearsal for the first load and after loader code changes. A prod dry-run gate and a dated snapshot before every load; the owner deletes old snapshots. |
| DP11 | The old plan's prediction compaction (2b-5)? | Not in this plan. It stays in the 2026-09-28 plan for its own re-plan after Phase 4, and takes the next free migration number then. |

## Appendix B: Components: source, licence, access, status

| Component | Source | Licence | Access | Status |
|---|---|---|---|---|
| `route_density` | `scorable_routes` (MP catalog, derived columns) | MP-derived, internal only (`DATA_LICENSE.md`) | Database, read as `ingest` | **Built.** Time-invariant covariate. |
| `season_share` | `cell_daily_conditions` ERA5 rows for the area's `feature_points` series | Open-Meteo, CC BY 4.0 attribution (already credited in `DATA_LICENSE.md`/README); bought in the Professional window (plan 3) | Database only; no API call from this plan | **Built.** Missing until the window fills route-point series. |
| `mp_ticks` | `internal.route_tick_coverage` / `route_tick_months` (ice/mixed export, plus the rock export via X-1) | MP-derived, internal (P2-14; the owner's lawyer consult is pending). Removal: flag false or the views gone, then rebuild and retrain. | Local aggregate file → `ingest` loader → database | **Built.** |
| `nps_visits` | NPS IRMA Stats monthly recreation visits | Believed US-federal public domain; **not verified this session** | Manual report download into a curated CSV (no API client) | **Deferred** `[default pending: DP6]`. Re-add with `nps_units`/`nps_monthly_visits` and the 2026-09-28 plan's Task 6 loaders, after the owner verifies the source and terms. |
| `permit_attempts` | `objective_permit_counts` (NPS Denali/Rainier reports, hand-entered) | US-federal public domain (unverified) | Curated CSV | **Deferred.** The objectives layer is not built (roadmap item 4). Brings back the ×/÷1.5 bounds. |
| `objective_popularity` | Wikidata sitelink counts for linked objectives | CC0 | Open API (`wikidata.py` in the objectives plan), behind its own gate | **Deferred.** Needs objectives. |
| `in_app_ticks` | Phase 4 aggregates | Own data | Database | **Deferred.** Phase 4 volume. |
| `ob_ticks` | OpenBeta ticks | CC0 | — | **Removed** (owner: ~0.2% of MP volume). |
| `mp_tick_count` (legacy CA/NV `public.mp_ticks`, capped at 16) | — | — | — | **Removed.** Superseded by `mp_ticks`. |

---

## Self-review

- **Spec coverage.**
  - P2-9: the components kept are route density, season share and MP ticks (D-1, Tasks 5–7). The rest are deferred with source, licence and access stated (Appendix B), and `ob_ticks` is removed.
  - Also covered: the missing flag with a reason on every component; raw storage; bounds (D-6); the Spearman validation, reported (D-15, Task 8); the removal drill (Task 8 test, Task 10 Step 2); the proxy never served (Task 8 guard, `internal` schema).
  - P3:152/398: the snapshot is keyed with `data_cutoff`, tick months stop before the cutoff, and the time-invariant covariates are documented.
  - Owner decisions:
    - rock ticks are missing until scraped (the seam views only, Global Constraints);
    - coverage per area and month (`tick_coverage` column plus proxy detail, Task 7, and the verify cell);
    - the local loader that reads only an aggregate file (Tasks 1–2) with the scraper guards rechecked (Task 2 Step 4);
    - MP areas and the type groups (Task 4 CHECK, Task 6 scope);
    - migration 0011 with Phase 4 renumbered (Task 9 Step 4);
    - the runbook safety structure (Tasks 3, 10).
- **Placeholder scan.**
  - No step says "TBD" or "add tests". Each code step carries its code.
  - Two steps depend on files not quoted here, and each says what to confirm or copy: the `tests/verify/test_mp_catalog.py` `fetch` helper (with a stated fallback), and the Phase 4 doc's exact `down_revision`/`MIG current` lines (found by grep in Task 9 Step 4).
- **Type consistency.**
  - `Key = tuple[int, str, int]` with bigint MP area ids throughout.
  - `Component.present/absent/get/to_json` is used identically in Tasks 5–8.
  - `tick_component` keywords match between Task 5 and Task 7.
  - `build(..., write=)` is used by both the test and the CLI dry run.
  - `SOURCE_BY_SCOPE`/`Scope` are shared by Tasks 1–2.
  - The metric names in `_ALLOWED_METRICS` equal `validate`'s keys.
- **Review Focus.** Each of the 7 items names its owning test: Tasks 1, 2, 5, 6, 7 and 8.
- **Known gaps, flagged rather than designed around.**
  - `season_share` is missing until route-point ERA5 exists.
  - Partial rock coverage is MNAR (scrape order).
  - Objective rows wait for the objectives re-plan.
  - The Phase 3 changes are listed under "Conflicts with the Phase 3 spec".
