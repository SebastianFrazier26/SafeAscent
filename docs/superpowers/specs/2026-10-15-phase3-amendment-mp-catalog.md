# Phase 3 Amendment: the MP Catalog and Later Decisions

- **Date:** 2026-10-15
- **Status:** Records owner decisions made after the Phase 3 spec was written (2026-09-27 rev 3), plus the design defaults the Phase 3 implementation plan adopts pending owner review (`docs/superpowers/plans/2026-10-15-phase3-model-v2.md`, Appendix A, "DPn").
- **Amends:**
  - `docs/superpowers/specs/2026-09-27-phase3-model-v2-design.md` (cited "P3:line");
  - `docs/superpowers/specs/2026-09-28-phase3-amendment-similarity-confidence.md` (cited "A:line").
- **Precedence:** where this document and P3/A disagree, this document wins. Where a line below says "default pending DPn", the owner may still overrule it, and the plan names the tasks that change.

## 1. Owner decisions since P3 (binding)

| # | Decision | Date / source |
|---|---|---|
| O1 | **MP is the route source.** `canonical_routes`/`canonical_areas` are keyed by MP id (migration `0008_mp_catalog`). OpenBeta is dropped entirely, not marked absent. | owner 2026-10-12 |
| O2 | **Seven type values:** `sport`, `trad`, `rock`, `alpine`, `ice`, `mixed`, `unknown`. `rock` (about 63% of routes) is an MP route with no discipline, or a TR/aid-only route (`rt-mp-v3`). `unknown` is never scorable; boulders are never scorable. | owner 2026-10-12/13 |
| O3 | **Coordinates are area-level only.** A route's point is always its location's, never `mp_routes` lat/lon. | owner 2026-10-12 |
| O4 | **Elevation** comes from USGS 3DEP (1/3″, then 2″ fallback), cross-checked against EPQS with a fail-closed gate (`static_features epqs-check`). | MP catalog plan |
| O5 | **Weather** is keyed by the crag's local calendar day `(grid_bucket, tz, date)`. Incident ERA5 history starts 1990-01-01; an earlier incident reads `conditions_missing`, never zero. Normals use a bucket's `min(tz)`. | plan 3, DP1 |
| O6 | **Exposure.** Rock ticks arrive gradually (about 140 days), scraped near accidents first, so coverage is biased (MNAR). Exposure carries coverage and never reads a partial sum as low traffic (`internal.exposure_index`, migration `0011`). | owner 2026-10-15; exposure plan |
| O7 | **Accident → route links** come from R10 only: the identity on `accidents.mp_route_id`, owner decisions, or the gated LLM pass (view `accident_route_links`, migration `0010`). Nothing links by name. | owner 2026-10-12/14 |
| O8 | **Jev is Phase 4 incident triage only.** The risk model is built in-house. No LLM or hosted model substitutes for the model or any part of scoring. | owner 2026-10-12 |
| O9 | **The legacy `weather` table is dropped as a hard gate of the MVP-1 PR** (that PR may not merge while it exists). | plan 3 decision D9 |
| O10 | **Phase 4 parts that depend on Phase 3** (retrain trigger, incident weights, tick exposure) wait for Phase 3. | owner 2026-10-12 (DP14 of Phase 4) |
| O11 | **Migrations.** Exposure takes `0011`; Phase 3 takes `0012_model_registry` and `0013_drop_legacy_weather`; Phase 4 renumbers after Phase 3 (`0014`–`0016`). | owner instruction 2026-10-15 |
| O12 | **AAC year ranges are intervals** `[year_lo, year_hi]`, never the stored point year or its synthetic July-1 date. | owner 2026-09-30 |

## 2. Where P3 and A are superseded

### Data reality and definitions

- **P3:20, P3:252, P3:343 (MP display wording).** Already reversed by A:45–48 (D5). Restated here: MP rock routes, ice/mixed route facts and tick aggregates may be displayed; MP prose never is. The MP catalog is the *only* catalog, not a supplement (O1).
- **P3:45 (exposure reality).** `mp_ticks` (CA/NV, capped at 16) is superseded by the tick seam: `internal.route_tick_scrapes` plus the views `route_tick_coverage`/`route_tick_months`. Each route is `missing`, `scraped` or `complete as of`. Ice/mixed are complete in prod; rock arrives over about 140 days (O6).
- **P3:49 (catalog coverage).** "OpenBeta is thin on ice/mixed/alpine" no longer applies. Coverage is MP's: about 168K internal routes, with the scorable set given by the `scorable_routes` view.
- **P3:56 (route type group).**
  - Six scorable groups, not five: `rock` is added (O2).
  - "Toprope and aid map to the group of the underlying rock route" is replaced by "TR/aid-only routes are `rock`" (`rt-mp-v3`).
  - Pooling: `rock` is its own group, pooled with `sport`/`trad` through a family effect (rock family) and ridge-shrunk type effects. `alpine`/`ice`/`mixed` form the mountain family. Default pending DP3.
- **P3:57 (area hierarchy).**
  - The OpenBeta area tree is replaced by `canonical_areas` (MP area tree, `ltree` `path`).
  - Levels are: area (the MP area a route is directly attached to) → region (the depth-2 path prefix) → state (`state_code`) → national.
  - The sizing figures (694 regions, 27,877 leaf areas) are re-measured on the MP tree and reported in each model card.
- **P3:58 (cell).** A cell is MP area × one of six groups × month. It exists only for areas with directly attached scorable routes, so cells are disjoint (exposure plan DP5).
- **P3:59 and P3:243–259 (M5 exposure components).**
  - `OpenBeta ticks` is removed.
  - `mp_tick_count` and `mp_ice_mixed_ticks` are replaced by one seam-based `mp_ticks` component with coverage.
  - NPS visitation, permits and objective popularity are deferred until their sources are verified (exposure plan DP6).
  - MVP-1 fits on `route_density`, `season_share` and `mp_ticks` only.
  - "Optional, removable" still holds by flag (`EXPOSURE_ENABLE_MP_TICKS`). But `mp_ticks` is now the main traffic signal, so the with/without comparison in the model card shows a large gap.
  - **New:** rock-family ticks enter the fit only once national rock tick coverage reaches 0.90. No coverage or missingness term ever enters the fit, because scrape order depends on accident locations. Default pending DP5.
- **P3:61–71 (RR, display score, display bands).**
  - Percentiles are taken within each of the six groups.
  - The display bands are 25/50/75 on that percentile through MVP-1, per A:43 (D3), which already superseded the P3 percentile bands until gate G7 says otherwise.
  - The lowest band renders neutral, "Lower than typical for this type", never green or "safe". Default pending DP12; resolves A:179 Q3.
- **P3:72 and P3:285–292 (insufficient data).** Superseded by A:39–43 and A:145–150 (D2): gray means no geo evidence below national *and* no feature neighbour within d\*. The "thin catalog coverage" clause no longer applies to the MP catalog. Objective coverage badges wait for the objectives re-plan.
- **P3:86 (`year` precision rows).** A year-precision row is the interval `[year_lo, year_hi]` (O12). Inside any window its weight is the overlap in days ÷ the interval's days. Months are spread by the type group's national seasonal profile. Default pending DP19.
- **P3:87 (geocode precision in spatial terms).** An R10 route or area link places an incident regardless of geocode precision (O7). Otherwise:
  - only `exact`/`crag`/`area` points are placed at the nearest scorable area within 3 km;
  - `park_centroid`, `region_fallback` and `unknown` count at state level only when the state is verified;
  - the rest are excluded.

  Default pending DP4.

### Components and storage

- **P3:131–146 (module list).** Renamed in the plan:
  - `exposure.py` is folded into `features.py` (design matrix with missing indicators);
  - `baseline_eb.py` is split into `glm.py` (Poisson IRLS, written in-house) and `eb.py` (Poisson-Gamma EB with the geo/archetype blend of A:89–93);
  - `scoring.py` becomes `posterior.py` + `materialize.py`;
  - `thresholds.py` becomes `tuning.py`.

  Added: `incidents.py`, `dataset.py`, `folds.py`, `archetypes.py`, `metrics.py`, `kernel_adapter.py`, `train.py`. `baseline_bayes.py`, `rules.py` and `conditions.py` are later plans.
- **P3:136 (bundle).** A zip of `.npy` arrays plus `manifest.json`, numpy-only and never pickled, instead of Parquet. Default pending DP7.
- **P3:146 and P3:431–434 (nightly scoring).** In MVP-1 (CM = 1) the trainer materializes one row per (version, route, month) at train time. A daily nightly scorer arrives with v2.1 rules. The nightly Celery slot becomes a heartbeat that checks the active version's rows. Default pending DP2.
- **P3:150 (`route_static_features`).** Exists as migration `0009` with `source = 'mp'`, point-keyed `feature_points` (3DEP elevation, O4) and area-level coordinates (O3). Aspect describes the area, not the face, and stays deferred (A:64). Phase 3 reads the `scoring_unit_features` view, the MVP-1 read contract.
- **P3:151 and P3:176 (weather join).** Weather joins by `(grid_bucket, tz, local date)` through `accidents.tz` (O5), not `(lat, lon, date)`. MVP-1 uses climate normals only (CM = 1). Day-level joins are v2.1+.
- **P3:152 (`exposure_index`).** Lives at `internal.exposure_index`, with:
  - PK `(area_id, type_group, month, as_of, data_cutoff)`;
  - `area_id` as the MP `bigint`;
  - added columns `tick_coverage`, `exposure_point`/`exposure_lo`/`exposure_hi` and `run_id`.

  Training reads the snapshot whose `data_cutoff` equals the fold cutoff (exposure plan, "Conflicts" 1–6).
- **P3:153–158 (registry, `route_daily_scores`).**
  - Both live in schema `model`, as `model.registry` and `model.route_scores`. Default pending DP1.
  - `route_scores` is per month, not per day (DP2).
  - It carries A:108's columns (`conf_level`, `n_eff_geo`, `n_eff_feature`, the ranges) plus percentile ranges and the per-10K fields.
  - `kind` is dropped; `family` is `kernel | eb_v1`.
- **P3:159 and P3:163–169 (objective-level scoring, `objective_daily_scores`).** Deferred to the objectives re-plan (exposure plan conflict 7; plan DP18).
- **P3:161 (grid buckets / catalog size).** "Public catalog is OpenBeta rock plus MP ice/mixed plus objectives" now reads "the MP catalog".
- **P3:185 (plain-words exposure).** "Tick counts where they exist (OpenBeta, and internally MP)" now reads "MP logged ticks, with per-area coverage".

### Owner decisions M2–M13

- **P3:206–213 (capacity sizing).** "× 5" type groups becomes "× 6", and the counts are re-measured on the MP tree. Budgets are unchanged.
- **P3:230 (trainer credentials).** "SELECT on data tables" is replaced by an explicit allowlist:
  - reads: `accidents_clean`, `accident_route_links`, `canonical_areas`, `canonical_routes`, `scorable_routes`, `scoring_unit_features`, `historical_predictions`, `internal.exposure_index` (USAGE on `internal`);
  - writes: `model.registry` (INSERT, UPDATE) and `model.route_scores` (INSERT, DELETE);
  - LOGIN through an owner script.

  `accidents_clean_daily`/`accident_conditions` wait for v2.3. Default pending DP17.
- **P3:241 (M4 packaging).** The `train` group holds scipy for MVP-1 (gamma quantiles, predictive log score). numpyro/jax join with v2.2, lightgbm/shap with v2.3. Default pending DP8.
- **P3:261–292 (M6, deferred).** Adopted as amended by A:151:
  - the "minimum parent-region incidents" axis is replaced by d\*;
  - the CI-ratio axis sets `conf_level` (thresholds chosen on CV for per-level PIT coverage, monotone error and Low-stratum calibration);
  - a φ interval factor is calibrated to 90% coverage;
  - the route-density axis is dropped (density is a model covariate).

  Default pending DP11.
- **P3:271** is superseded as above.
- **P3:332–336 (M10).** "Five groups" becomes "six" (P3:56 above). Bouldering is still not scored.
- **P3:338–344 (M11, split by discipline).** Replaced by O1: MP for every discipline. The Objective layer is a later re-plan.
- **P3:346–353 (M12 lightning).** Unchanged, but not used in MVP-1 (CM = 1).

### Validation

- **P3:363–368 (spatial block CV).** "8 state-group regions" are now defined: `pacific_nw`, `california`, `southwest`, `rockies_north`, `colorado`, `central`, `southeast`, `northeast` (`app/ml/cells.py`). Default pending DP10.
- **P3:371 (rolling origin).** Each fold reads an exposure snapshot built with `exposure build --cutoff <origin>`. Rock-family ticks are normalized within the snapshot, so an era's tick volume cancels.
- **P3:376–378 (recent holdout).** For 2019–2024 the exposure snapshot is the 2016 one, the nearest backtest cutoff not after the origin.
- **P3:380–384 (prospective kernel backtest).**
  - `historical_predictions.route_id` is the MP route id.
  - The nightly's 1-year purge now keeps 2026-02-05..2026-07-02 until Phase 3 records it.
  - Part 4 gates on top-10% capture; its log score (in-window calibration, kernel-favouring) is reported.
  - It runs only once the R11 refresh has incidents in the window.

  Default pending DP14.
- **P3:392 (kernel through the same harness).** The kernel's math is frozen under `app/ml/kernel_ref/` for the backtest only, never served. It is evaluated per (area, group, month) with training-fold accidents, neutral weather, and `rock` passed as `trad`.
- **P3:398 (leakage controls).** The exposure plan's conflict 6 is accepted: tick counts are frozen at first scrape, so a tick logged after a cutoff for a climb before it is counted.

### Serving, UX, tests

- **P3:433–441 (serving).**
  - The detail endpoint reads the stored row.
  - `explain()` decomposes that same number exactly.
  - `POST /predict` (an arbitrary point) is retired; default pending DP23.
  - Hourly risk is `null` until v2.1, and trends show the v2 monthly profile; default pending DP24.
- **P3:443–450 (UX).**
  - Low confidence shows the number, its range, a hatched marker and a label (A:42).
  - Gray is only `insufficient` (no evidence).
  - Missing rows read "Unavailable".
  - The disclaimer is unchanged.
  - The Ascents tab headline is A:125's option C. The per-10,000-logged-ascents line appears only where a cell's tick coverage is complete; default pending DP13, resolving A:177 Q1.
- **P3:456 and P3:469 (tests).** "Five type groups" becomes "six". The property "raising route density never raises RR" is guaranteed by a non-negative density coefficient.
- **P3:475 (CI backtest fixture).** The fixture is fully synthetic (generated), not an anonymized copy of real data.
- **P3:483–497 (risks).** The "thin public catalog (OpenBeta)" row is void. Added risks:
  - MNAR tick coverage (O6, DP5);
  - incident assignment rate by state (DP4);
  - kernel-history purge (DP14).

### Milestones

- **P3:503–508 (MVP-0 remaining).**
  - "Re-measure region and leaf-area counts on the OpenBeta area tree" becomes "on the MP tree".
  - The kernel registry row is family `kernel`, status `active` until the first v2 promotion, then `retired` (never `previous`). Rollback never restores the kernel; default pending DP15.
- **P3:511–521 (MVP-1).**
  - Prerequisites are Phase 2a plus the MP catalog, R10 and exposure X-2, not the "Phase 2b route-density proxy".
  - Acceptance adds the `weather` drop (O9) and amendment gates G1–G7.
  - "First promotion auto-published" stays. The public issue and its artifacts are aggregate-only (no route ids or coordinates), with per-route movers in `model.registry.diff_report`; default pending DP16.

### Amendment A

- **A:56–61 (MVP-1 feature set).** Elevation comes from 3DEP via `feature_points` (O4), not `elevation_service.py`. Climate normals come from `cell_climate_normals` per bucket via `scoring_unit_features`; `pending` normals are missing, never zero. A:181 Q5 is answered: the normals and elevation moved into plan 3/MP catalog work (`0007`, `0009`).
- **A:66 (difficulty).** Accident links now exist (R10, O7), but accident grades are still unlinked, so difficulty stays deferred.
- **A:71–79 (similarity distance).** Option C is adopted as written: archetypes by in-house k-means on seven standardized area features, K and feature weights learned on held-out folds. Default pending DP9; A:180 Q4 is answered "learned from data".
- **A:108 and A:136 (`route_daily_scores`).** Read `model.route_scores` (DP2).
- **A:117 (per-10,000 logged ascents).** The denominator is the seam's complete-route tick mean per year for that calendar month, and is shown only at full cell coverage.
- **A:138 (Redis keys).** Keys are `(model_version, route_id, month)`; default pending DP22.

## 3. Unchanged

These P3/A items carry over without change:
- the M1 library choice for v2.2;
- M2's runner budgets and fallback;
- M3 storage in Neon;
- M7 (count all incidents);
- M8 avalanche scope (the ski-approach flag is reported with/without);
- M9 auto-publish with a hard gate and `/rollback`;
- M13 (no re-identification);
- the v2.3 event-count gate;
- A's G1–G7.
