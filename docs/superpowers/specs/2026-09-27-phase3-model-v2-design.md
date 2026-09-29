# Phase 3: Risk Model v2 (Option C Hybrid) Design

- **Date:** 2026-09-27
- **Status:** Draft — decisions recorded 2026-09-27 (rev 3)
- **Evidence base:** read-only Neon data audit run 2026-09-27 (`analyst` role). It is to be committed as `docs/data-audit/2026-09.md` (MVP-0), and figures below cite its sections as "audit §N".
- **Depends on:** Phase 2 data platform (`docs/superpowers/specs/2026-09-27-phase2-data-platform-design.md`).
  - **Phase 2a (data repair)** must land before MVP-1 trains. It covers the `date_precision` backfill, AAC year repair, dedupe, `geocode_precision`, the weather relink, wind units, and the route-type mapper.
  - **Phase 2b (expansion)** supplies the composite exposure proxy covariates beyond route density, the static terrain and geology features for v2.2, and the accident refresh that makes the prospective backtest and the post-2019 holdout usable.

## Goal

Replace the hand-tuned kernel model with a model whose parameters are **fit from data**, calibrated against held-out incidents, and retrained as data grows. The kernel model is `safety_algorithm.py`, `safety_algorithm_vectorized.py` and `location_safety_computation.py`, with its constants in `algorithm_config.py`. Accuracy beats everything else: v2 ships only when it beats the current model on the same backtest.

## Non-goals

- A "safe" label or any claim about a route's current condition.
- Personalization by climber experience (v3; see M13).
- Scoring bouldering (see M10).
- Using an LLM or hosted model for scoring. Jev is approved only for Phase 4 incident-report triage.
- Displaying or redistributing MP descriptions or other prose, ever (see M11). Ice/mixed route facts, rock routes, and MP tick aggregates may be displayed (owner decision 2026-09-28; `DATA_LICENSE.md` wording pending legal review); MP tick data also enters as optional, removable internal exposure components (see M5).

## Data reality (from the 2026-09-27 audit)

These facts shape every choice below.

- **Climbing incidents with a plausible exact date: about 850.** That is 644 AAC, 122 NPS and 82 avalanche (ice climbing) (audit §1).
  - The table holds 4,790 accident rows, but at least 90% of avalanche rows and 80% of NPS rows are not climbing (audit §2).
  - AAC day-15 dates (1,567 rows) are month-precision, and about 123 AAC July-1 dates are year-precision.
  - About 422 AAC rows dated 2019 or later have corrupted years (audit §1).
- **Honest post-2019 holdout: about 50 climbing incidents from 2019–2024, plus 23 avalanche ice-climbing events from 2020 on. There is nothing after mid-2024** until the Phase 2 accident refresh lands (audit §9).
- **Sparsity:** only 1,241 cells on a 0.5° grid contain routes (audit §7). By climbing-incident count:

  | Incidents in the cell | Cells |
  |---|---|
  | 0 | 923 |
  | 1 | 115 |
  | 2–5 | 119 |
  | 6 or more | 84 |

  46,124 routes sit in cells with zero incidents. Partial pooling is essential, and many areas will honestly be "insufficient data" at launch.
- **Geocodes:**
  - About 216 foreign (mostly Alberta and BC) AAC rows sit at central-CO and WY fallback points.
  - 81 Alaska rows sit in California.
  - NPS rows sit at 51 park centroids (audit §3).
- **Exposure:** `ascents` is empty. `mp_ticks` matches only 5.4% of `mp_routes`, covers CA (27% of routes) and NV (13%) only, is capped at 16 ticks per route, and contains future-dated and orphan rows (audit §6).
- **Weather:** 26,691 rows are mislinked, wind units are unlabelled (km/h is inferred), and `visibility_avg` is NULL everywhere (audit §10).
- **`historical_predictions`:** about 23.2M kernel scores, computed each day 2026-02-05 → 2026-07-02 before the day's incidents were known, for about 159K routes per day. 15.3% are capped at 100 (audit §11).
  - Cadence: the daily scoring run recomputes and writes that day's scores from **live weather forecasts**, so each row is a genuine as-of prediction. Phase 2 compaction (`prediction_archive_v1`) changes only the storage layout, not this daily cadence; from MVP-1 on, the equivalent daily record is `route_daily_scores`.
- **Route catalog coverage:** OpenBeta is thin on ice (188 US routes), mixed (137) and alpine (6,270). Phase 2 (DECIDED) fills ice/mixed from MP route facts (~4.8K) and mountaineering via the Objective layer (M11).

## Definitions

- **Incident:** a row in `accidents` that is climbing (per Phase 2a activity rules), not a duplicate after dedupe, and has no `excluded_reason`.
  - Avalanche scope follows M8.
  - AAC rows whose year is still unverified carry `excluded_reason = 'year_unverified'` until Phase 2a repairs them.
- **Route type group:** one of five groups, `sport`, `trad`, `alpine`, `ice`, `mixed` (M10), assigned by the versioned Phase 2a mapper from catalog type flags. Toprope and aid map to the group of the underlying rock route. 65% of `mp_routes.type` values are generic or unknown (audit §5); a route the mapper cannot place gets `type_group = unknown`, is not scored, and shows the insufficient-data chip with "route type unknown". Bouldering is excluded from scoring (M10).
- **Area hierarchy:** national → state → region (depth-1 area below state) → crag (leaf area), taken from Phase 2's `canonical_areas`: the OpenBeta area tree, plus `mp_facts` ice/mixed areas attached under it, plus one area row per objective (M11). MP rock areas are mapped onto it internally for accident and feature matching only. The MP-based counts used for sizing below (694 route-bearing regions, 27,877 leaf areas) are re-measured on the OpenBeta tree in MVP-0.
- **Cell:** area × route type group × month.
- **Exposure proxy (see M5):** a vector of per-cell covariates, not a single number. It is defined in the Phase 2 spec and enters the model with estimated coefficients.
- **Scoring unit:** a catalog route, or, where route-level data is missing, an objective × type group (see Objective-level scoring). Both carry a Phase 2 coverage badge: **Route-level**, **Objective-level**, or **Thin data**.
- **Baseline relative rate (RR):** expected incidents for the cell given its exposure covariates and hierarchy, ÷ the national rate for the same type group and month. Reported with a 90% credible interval (q05, q95).
- **Conditions multiplier (CM):** v2.1 and v2.2 use a product of versioned rule multipliers (`rules.py`). A learned case-crossover odds ratio replaces rules only after the v2.3 event-count gate is met.
- **Display score:** the percentile of RR × CM within each of the five type groups, nationally, for the date.
- **Display band** (used for promotion diffs and UI copy), by percentile:
  - `lower` below 25
  - `typical` 25–75
  - `elevated` 75–90
  - `high` 90 or above
  - `insufficient` when the insufficient-data rule applies

  The UI never renders `lower` as green or "safe".
- **Insufficient data:** set by the procedure in M6. The thresholds are frozen per model version, stored in the bundle, and shown in the model card. Rendered as a neutral gray chip. It also covers thin-catalog coverage: a route, area or type group whose catalog coverage is flagged thin by the Phase 2 coverage strategy is shown as `insufficient` with a coverage warning, never silently omitted and never scored low by default.
- **date_precision** (new `accidents` column; backfilled by Phase 2a) is one of three values, set by these rules:

  | Rows | Value |
  |---|---|
  | AAC dated before 2019 with day = 15 | `month` (month real, day synthetic) |
  | AAC dated July 1 | `year` (about 123 real year-precision rows; the ~6 genuine July-1 dates are sacrificed) |
  | Other AAC dated before 2019 | `day` |
  | Avalanche and NPS | `day` (their day-of-month distribution is flat) |
  | AAC dated 2019 or later | Precision and year come from the Phase 2a repair. Until then the rows are `excluded_reason = 'year_unverified'` and enter no training or evaluation set. |

  Which data each precision feeds:
  - **`day`** feeds everything, including day-level weather joins and the CM.
  - **`month`** feeds the seasonal baseline (cell counts by month) but never day-level weather joins or the CM.
  - **`year`** feeds annual spatial totals only, spread across the type group's season by the national seasonal profile. It never feeds month cells directly, weather, or the CM.
- **geocode_precision** (Phase 2a column) is one of `exact | crag | area | park_centroid | region_fallback | unknown`. Only rows at area level or better (`exact`, `crag`, `area`) enter spatial terms (crag and region random effects, BYM2). `park_centroid` rows count at state level only. `region_fallback` and `unknown` rows count toward state and national totals only if their state is verified, and are otherwise excluded.

## Architecture

```mermaid
flowchart LR
  subgraph Offline["Offline — scheduled GitHub Actions"]
    A[accidents after Phase 2a<br/>dedupe + date_precision + geocode_precision] --> T1[train_baseline]
    X[exposure_index<br/>composite proxy] --> T1
    S[route_static_features] --> T1
    A -. v2.3 only, gated .-> T2[train_conditions<br/>case-crossover]
    W[cell_daily_conditions history] -. v2.3 .-> T2
    RU[rules.py] --> B[bundle]
    T1 --> B
    T2 -.-> B
    B --> G[validation gate<br/>challenger vs active]
    G -->|pass| P[auto-promote → active]
    G -->|fail| RJ[rejected, never promoted]
    P --> N[promotion issue + diff report<br/>@owner, /rollback]
  end
  subgraph Neon
    MR[(model_registry<br/>bundle bytea)]
  end
  B --> MR
  P --> MR
  subgraph Nightly["Nightly — Railway Celery"]
    GB[grid buckets 0.1°] --> F[fetch forecast/alerts] --> W2[cell_daily_conditions today]
    MR -->|active version| SC[app/ml/scoring.score]
    W2 --> SC
    S --> SC
    SC --> RS[route_daily_scores]
    SC --> OS[objective_daily_scores]
  end
  subgraph API
    RS --> L[map/list endpoints]
    OS --> L
    SC --> D[detail endpoint<br/>same score + explanation]
  end
```

One scoring function serves both the nightly batch and the detail endpoint. The three kernel implementations are deleted once MVP-1 is active.

## Components and interfaces

All new ML code lives in `backend/app/ml/`. The functions are pure: `scoring.py` does no DB or network I/O.

| Module | Interface | Notes |
|---|---|---|
| `ml/scoring.py` | `score(routes_df, conditions_df, bundle) -> scores_df` | Output columns: `route_id, date, rr_mean, rr_q05, rr_q95, cm, risk, percentile, band, insufficient, top_terms`. Deterministic. |
| `ml/bundle.py` | `ModelBundle` (frozen dataclass): `version, family, baseline_table, exposure_coefs, rule_multipliers, rules_version, cm_coefs \| None, percentile_ref, thresholds, data_hash`; `to_bytes() / from_bytes()`, `load_active(conn)`, `save(conn)` | Serialized as a zip of Parquet tables plus `manifest.json` and stored in `model_registry.bundle` (M3). |
| `ml/exposure.py` | `build_exposure(cells_df, proxy_df) -> design_df` | Turns the Phase 2 proxy components into log-scale covariates plus missingness indicators. |
| `ml/baseline_eb.py` | `fit_eb(cells_df, design_df) -> baseline_table` | MVP-1, in two stages. (1) A Poisson GLM on the exposure covariates plus a shared month profile and type main effects gives the expected count μ₀ per cell; type × month deviations are ridge-penalized toward the shared profile so sparse `ice` and `mixed` borrow strength. (2) Poisson-Gamma empirical Bayes shrinks observed/μ₀ up the hierarchy (crag → region → state → national), then shrinks each type group's national rate toward the all-type rate, in closed form. |
| `ml/baseline_bayes.py` | `fit_hier(cells_df, design_df, features_df, h3_adj) -> baseline_table` | v2.2, NumPyro NegBin: `log μ = Zγ (exposure proxy) + Xβ (static) + a_type + u_state + u_region + u_region×type + u_crag + s(month) + δ_type(month) + BYM2(h3_r5)`. `a_type`, `u_region×type` and `δ_type(month)` are partially pooled across the five type groups (shared hyperpriors), which is what lets sparse `ice` and `mixed` borrow from `alpine` and the rock groups. NUTS with 4 chains in parallel on 4 CPU devices. Stores posterior mean, q05 and q95 per cell, never raw draws. |
| `ml/rules.py` | `RULES_VERSION`, `apply_rules(conditions_df) -> multipliers` | The primary conditions layer through v2.2. Transparent, versioned multipliers for NWS warnings (red-flag, winter-storm, avalanche), lightning (M12), AQI > 150, SNOTEL rapid loading, and freeze-thaw on `ice`/`mixed`/`alpine`. Each rule's docstring cites its source. |
| `ml/conditions.py` | `build_case_crossover(accidents, conditions_hist) -> strata_df`; `fit_clogit(strata_df) -> cm_coefs`; `apply_cm(...)` | v2.3 only. Built but not trained until the event-count gate is met. Controls share the location and weekday, fall in the same month, and come from other years (up to 8). |
| `ml/thresholds.py` | `select_thresholds(fold_results) -> Thresholds`; `sensitivity(fold_results, chosen) -> Report` | The M6 procedure. |
| `ml/backtest.py` | `run_backtest(model_factory, split) -> MetricsReport` | Used by the gate, CI, the kernel comparison, and the prospective backtest. |
| `ml/promote.py` | `evaluate_gate(challenger, active) -> GateResult`; `diff_report(challenger, active) -> DiffReport`; `promote(version)`, `rollback()` | M9 logic. `promote` and `rollback` each run in one DB transaction. |
| `ml/explain.py` | `explain(route_row, bundle) -> list[Term]` | GLM term contributions (MVP-1–v2.2); SHAP only if v2.3 adopts LightGBM. Nearby incidents are returned as context only and do not drive the score. |
| `tasks/score_nightly.py` | Celery task replacing `safety_computation_optimized.compute_daily_safety_scores_optimized` | Reads the **active** registry version once at task start, fetches conditions per grid bucket, calls `score()`, and bulk-upserts. |

### Feature store and registry tables (Alembic migrations)

- `route_static_features(route_id PK, source, area_path ltree, type_group, aspect_deg, slope_deg, elevation_m, lithology, pitches, length_m, h3_r5, grid_bucket, updated_at)`
- `cell_daily_conditions(grid_bucket, tz, date, tmax, tmin, precip_mm, snowfall_cm, wind_max_ms, gust_max_ms, freeze_thaw, swe_delta_mm, nws_alert_codes text[], aqi, record_kind, source, fetched_at, PK(grid_bucket, tz, date))`. `date` is the crag's local calendar day in IANA zone `tz`; `record_kind` is `era5`, `stopgap` (Forecast API `past_days`, non-ERA5) or `forecast` (Phase 2 plan 3, revised 2026-09-28). Wind is stored in m/s. There is no visibility column. Lightning is not a column of this table (revised 2026-09-28): read it through Phase 2 plan 7's SQL functions `lightning_glm_count(grid_bucket, tz, date)` (GLM total lightning from 2018-02-13, local day) and `lightning_nldn_count(grid_bucket, utc_date)` (NLDN cloud-to-ground, UTC-day tiles; a local day L is matched to UTC days L and L+1). Both return 0 only inside recorded coverage and NULL (missing, not zero) outside it (M12); area/objective coverage comes from the `scope_lightning_coverage` view.
- `exposure_index(area_id, type_group, month, n_routes, proxy jsonb, proxy_version, as_of, PK(area_id, type_group, month, as_of))`. `proxy` holds the Phase 2 components (route density, season share, OpenBeta ticks, NPS visitation, permits, objective popularity, the optional internal `mp_tick_count` and `mp_ice_mixed_ticks`, in-app ticks later), each with its own missing flag. Objectives use their `canonical_areas` row.
- `model_registry`:
  - Columns: `version text PK, family, kind, created_at, data_hash, train_window, metrics jsonb, thresholds jsonb, model_card jsonb, bundle bytea, bundle_sha256, bundle_bytes, status, promoted_at, promoted_by, parent_version, gate_result jsonb, diff_report jsonb`.
  - `status` is one of `candidate | active | previous | rejected | retired`.
  - `promoted_by` is `auto` for gate promotions and `owner` for a `/rollback` or manual `workflow_dispatch` promotion.
  - A partial unique index on `status = 'active'` keeps exactly one active version.
- `route_daily_scores(route_id, date, model_version, rr_mean, rr_q05, rr_q95, cm, percentile, band, insufficient, PK(route_id, date))`
- `objective_daily_scores(objective_id, type_group, date, model_version, rr_mean, rr_q05, rr_q95, cm, percentile, band, insufficient, coverage_level, PK(objective_id, type_group, date))`. Same 400-day retention and compaction as `route_daily_scores` (Phase 2 Storage).

`grid_bucket` is lat/lon rounded to 0.1° (about 11 km). The catalog's routes (about 168K in internal MP data today; the public catalog is OpenBeta rock plus MP-fact ice/mixed plus objectives, M11) collapse to an estimated 8–15K buckets, which bounds weather API calls. `area_weekly_weather` already uses 969 points on this grid.

### Objective-level scoring

- **When:** for each Phase 2 `objectives` row × type group in its `disciplines` whose coverage badge is `objective` (fewer than 5 scorable catalog routes of that type group). Where a route-level score exists, the route score is shown and the objective score is not used for it.
- **How:** the same `score()`, with the objective's `canonical_areas` row as the cell's area (so its baseline comes from its region/state through the hierarchy), its static features from `route_static_features` computed at the objective point, and its grid bucket's conditions. Curated `objective_routes` are also `canonical_routes` rows and score as ordinary routes.
- **Percentiles:** ranked in the same national per-type-group reference distribution as routes, so an objective and a route with the same risk get the same percentile.
- **Insufficient data:** the M6 thresholds apply unchanged; `coverage_level = 'thin'` always renders `insufficient` with a coverage warning.
- **Storage:** `objective_daily_scores`, written by the nightly task in the same run as `route_daily_scores`.

## Data flow

1. **Ingest (Phase 2a/2b):**
   - Accidents are repaired, deduped across and within sources, and tagged with `date_precision`, `geocode_precision` and `excluded_reason`.
   - Weather is relinked by (lat, lon, date), not by `accident_id`.
2. **Features:** static features are computed once per route. The exposure proxy is recomputed monthly with `as_of`.
3. **Train:** the baseline uses `day` and `month` rows for cell-month counts, and `year` rows for annual spatial totals only. Rules need no training. The CM is not trained until the v2.3 gate.
4. **Gate and promote:** see M9.
5. **Serve:** the nightly `score()` runs over all scorable routes and objective-level objectives with the active bundle. The detail endpoint calls `score()` on one route with today's cached conditions row.

## Why exposure matters (plain words)

A crag with 500 popular routes will produce more accident reports than a crag with 5 obscure ones, even if each climb there is equally dangerous, simply because many more people climb there. If the model counted accidents alone, it would call the busiest, best-known areas the most dangerous and quiet areas the safest. Mostly it would be measuring popularity.

To estimate **risk per climb**, the model needs a stand-in for "how much climbing happens here". We have no direct count of climber-days, so we combine several partial signals: how many routes an area has, tick counts where they exist (OpenBeta, and internally MP), peak popularity, park visitation and permit numbers. The model then learns from the data how strongly each signal tracks the true amount of climbing, rather than us guessing.

## Owner decisions

M1–M9 were recorded 2026-09-27; rev 2 (same day) revises M2, M6, M7, M8 and M9 and adds M10–M13; rev 3 (same day) revises M5, M11 and M12 and adds objective-level scoring. M6 and M7 are deferred until after Phase 2.

### M1 — Bayesian library: **NumPyro** (DECIDED)

JAX NUTS runs well on CPU and parallelizes chains across cores. Only the training job imports NumPyro; the serving image stays lean (M4).

### M2 — Training runs in **scheduled GitHub Actions** (DECIDED), with a capacity plan

**Workflows:**
- `model-train.yml`: `schedule` (baseline monthly; CM quarterly once v2.3 is live) plus `workflow_dispatch`.
- `timeout-minutes: 330` gives a clean failure before GitHub's hard kill.

**Runner limits** (verified 2026-09-27 against docs.github.com):
- Each job runs at most **6 hours**.
- Standard Linux runners for public repos have **4 vCPU, 16 GB RAM and 14 GB SSD** (private repos get 2 vCPU and 8 GB).
- In public repos, **scheduled workflows are auto-disabled after 60 days without repository activity.** Mitigation: the training job pings a healthchecks.io check (Phase 1 D2) with a 35-day period, so a silently disabled schedule raises an alert.

**Why cost scales with cells, not rows:** every model fits on **aggregated cell counts** (area × type group × month). Adding accident rows or ticks changes the counts inside existing cells, not the number of observations. Cost grows with the number of cells and random effects, which grows with new routes or areas (for example, the OpenBeta expansion). The estimates below are to be replaced by measurements in the MVP-1 capacity benchmark:

| Level | Rough size |
|---|---|
| Region × type group × month | 694 × 5 × 12 ≈ 42K cells (upper bound; many region × type pairs are empty) |
| Crag random effects | at most 27,877 route-bearing leaf areas |
| Crag random effects fit only where a crag has ≥1 incident or ≥20 routes (the rest pool to region) | a few thousand parameters |

**Budget** (per run, measured and written to `model_registry.metrics.capacity` and the model card):

| Resource | Budget | Warning alert | Hard limit |
|---|---|---|---|
| Wall time (whole job) | ≤ 3 h | > 4 h | 5.5 h (timeout) |
| Peak RSS | ≤ 10 GB | > 12 GB | 16 GB |
| Bundle size | ≤ 50 MB | > 100 MB | — |

- Memory is controlled by keeping only per-cell summaries (never full posterior draws) and by running 4 chains as parallel host devices.
- **Scaling check:** the MVP-1 benchmark fits at 1×, 2× and 4× synthetic cell counts, fits a power-law curve for time and RSS, and stores it. Each run projects the next quarter's cell count, and a warning also fires if the projected time exceeds 5 h.
- An alert opens a GitHub issue labelled `model-capacity`.

**Fallback:** the same `Dockerfile.train` image runs as a **Railway one-off job** (a separate service with no public port, started on demand, sized with more RAM) using the same `trainer` credentials. The switch is made when either:
- two consecutive runs cross a warning threshold, or
- one run hits the hard limit.

**Credentials (DECIDED):** Actions secret `TRAINER_DATABASE_URL`. The `trainer` Postgres role is created **via SQL** (Phase 1 role procedure), not `neonctl`. It has SELECT on data tables and write access limited to model tables (INSERT/UPDATE on `model_registry`; any future model-output table is granted explicitly in its migration). It is separate from the read-only `analyst` role, which still runs the full backtest.

### M3 — Artifacts stored **in Neon** (DECIDED)

The bundle is a zip of Parquet plus JSON, stored in `model_registry.bundle` (bytea) with its sha256, size, metrics, thresholds and model card alongside. There is no new provider; Cloudflare R2 is dropped.

- **Retention:** keep bytes for the active version and the last 10 promoted versions. Bytes of `rejected` and `retired` versions are nulled after 90 days, but their metadata rows stay.
- **Loading:** the scorer loads the bundle once per process, keyed by version and checked against its sha256.

### M4 — Packaging: **uv with a separate `train` dependency group** (DECIDED)

The `train` group holds numpyro, jax, lightgbm and shap; production installs exclude it. `requirements.txt` migrates to `uv` (Phase 1 toolchain).

### M5 — Exposure: a **composite exposure proxy** (DECIDED)

MP ticks alone cannot serve as exposure (see Data reality): they cover CA/NV rock only, capped at 16 per route. The exposure measure is the composite proxy **defined in the Phase 2 spec** (P2-9). Its components, each as `log(1 + x)` with a missing indicator:
- route density per cell
- season share from climatology
- OpenBeta ticks (rock popularity; sparse)
- NPS recreational visitation for cells in parks
- climbing permit counts where parks publish them (for example Denali and Rainier)
- objective popularity (Wikidata sitelinks)
- **optional internal MP components:** `mp_tick_count` (existing rock ticks, CA/NV) and `mp_ice_mixed_ticks` (Phase 2 P2-14 `mp_tick_aggregates`). Used here as internal exposure covariates, not as displayed content; tick aggregates may separately be displayed (owner decision 2026-09-28; see M11). Removable: dropping the table and retraining leaves them missing-flagged, and the gate decides whether the retrained model is promoted.
- in-app ticks, later

The components enter the model **as covariates with estimated coefficients**. No coefficient is fixed at 1, and no single hand-built formula is used:
- MVP-1 estimates them in the stage-1 Poisson GLM.
- v2.2 gives them weakly informative priors (Normal(0, 1) on the log scale). Route-density γ is constrained ≥ 0.

The model card reports gate metrics with and without the MP components, so their removal cost is always known.

### M6 — Insufficient-data thresholds: **a data-driven procedure** (DEFERRED — decide after Phase 2)

The procedure below is the proposal and is kept as written; the owner confirms or revises it once Phase 2 data (repaired accidents, OpenBeta catalog, coverage supplements) is in. The thresholds are chosen from backtest reliability, frozen per model version, and documented in the model card. The procedure runs inside every training run, and its output is part of the gate.

1. **Candidate grid.**

   | Criterion | Candidate values |
   |---|---|
   | CI ratio q95/q05 | 4, 6, 8, 10, 15 |
   | Minimum incidents in the parent region over the full record | 0, 1, 2, 3, 5, 8 |
   | Minimum exposure percentile (route density) within type group (each of the five) | 0, 5, 10 |

   That is 90 combinations. A route is `insufficient` if it fails any criterion.
2. **Held-out evaluation.** For each combination, pool the held-out predictions from spatial block CV and rolling-origin CV (see Validation), restricted to routes marked sufficient. Then compute:
   - **Calibration:** O/E per predicted-risk decile. Pass when at least 8 of 10 deciles fall within [0.8, 1.25] and every decile has ≥ 10 expected incidents. If there are too few, merge to quintiles and require 4 of 5.
   - **Rank stability:** refit on 20 bootstrap resamples of incidents (resampled by region). Pass when the median Spearman correlation of route percentiles against the full fit is ≥ 0.8 and the median share of sufficient routes changing display band is ≤ 10%.
   - **Coverage:** the share of routes marked sufficient.
3. **Selection.** Among passing combinations, choose the one with the highest coverage. Break ties by the lower CI ratio, then the higher parent-incident minimum. If no combination passes, the model version fails the gate.
4. **Sensitivity analysis.** For the chosen combination, re-evaluate every neighbour one grid step away on each axis. If any less-strict neighbour fails calibration, move one step stricter on that axis and repeat, so the chosen point is not on a cliff edge. The report goes in the model card:
   - coverage, calibration and stability for the chosen point and its neighbours
   - coverage by state and type group
   - catalog-coverage warnings from the Phase 2 coverage strategy (thin ice, mixed, alpine)
5. **Freeze.** The thresholds are stored in `model_registry.thresholds` and in the bundle, and never change within a version.

**Launch honesty:** with 923 of 1,241 route-bearing 0.5° cells at zero climbing incidents, a large share of routes will likely be `insufficient` at MVP-1; the procedure reports the exact figure. UX plans for this:
- the gray chip reads "Not enough reported incidents to estimate"
- the map shows insufficient areas as a neutral hatch, not an absence
- the about page publishes coverage by state
- no percentile is shown for insufficient routes
- routes and areas with thin catalog coverage carry a coverage warning, so an absent or sparse ice/mixed/alpine area reads as "we lack data", not "low risk"

Coverage is a monitored metric: a ±10-point shift between versions is flagged prominently in the M9 diff report.

### M7 — Severity: **count all incidents for v2** (DEFERRED — decide after Phase 2)

The audit shows severity fields are populated for AAC and avalanche rows (audit §8), but not comparable across sources: NPS is 100% fatal because it is a mortality dataset. Phase 2a verifies severity completeness and consistency. After Phase 2, the owner decides whether a fatality- or severity-weighted variant is added. If added, it goes through the same gate and auto-promotion as any other family (M9).

### M8 — Avalanche scope (DECIDED)

- Avalanche incidents count **only when tied to climbing routes**.
- **Ski approaches to climbs** are included, on a best-effort basis, when:
  - the record's description mentions climbing or mountaineering terms (ice climb, couloir climb, summit attempt, alpine route, and similar; "climbing" meaning uphill skinning is excluded by a phrase list), **and**
  - it lies within **1 km** (configurable as `ski_approach_radius_m`, default 1000, stored in the bundle) of an `alpine`, `ice` or `mixed` route.
- These rows carry `inclusion_flag = 'ski_approach_best_effort'`. The model card reports model metrics with and without them.
- Pure backcountry skiing, snowmobile and snowshoe records are excluded.

### M9 — Promotion: **AUTO-PUBLISH, owner notified to review** (DECIDED)

A challenger that passes the full validation gate is promoted to `active` automatically. There is no manual-approval hold: band churn, marginal metrics, coverage shifts and new model families are all reported in the diff report, not held.

**Hard validation gate (kept).** A challenger is promoted only if it passes every gate component (see Validation plan) and beats the active version on the gated backtest metrics. A challenger that fails is marked `rejected`, opens a `model-rejected` issue, and is never promoted.

**Owner notification.** The workflow opens **a GitHub issue** labelled `model-promotion`, @mentioning the owner. GitHub emails @mentions by default, so there is no new provider, API key or cost. A transactional email API was considered and rejected: it adds a secret and a vendor for one message a month.

The issue body contains, in this order so the owner's review starts where it matters:
1. **Band-shift alert** (top of the issue): the share of routes changing display band between the active and challenger scores for the same reference date (the latest nightly date). If it exceeds 5%, or the insufficient-data coverage moves by more than 10 points, or this is the first version of a new model family, a bold `⚠ LARGE SHIFT` banner states which and links the relevant sections. Why 5%: a routine monthly retrain should move very few routes, and the M6 stability check already tolerates up to 10% churn from resampling noise, so over half that ceiling usually means a structural change (data repair, new source, bug).
2. the band from→to matrix, overall and per type group
3. the 25 biggest movers (route, area, old → new percentile, top driving term)
4. a map of changes: a PNG rendered in the job (matplotlib; state outlines; routes coloured by percentile delta), uploaded as a workflow artifact and linked. A GeoJSON artifact is also uploaded for zooming.
5. metric deltas (challenger vs. active) for every gate metric, with CIs; metrics close to a gate threshold (for example exactly 8 of 10 deciles passing, or a log-score improvement CI that includes 0) are marked "marginal"
6. the full gate result

**One-click rollback.** The owner comments `/rollback` on the promotion issue. A `model-rollback.yml` workflow (triggered by `issue_comment`) then:
- checks that `github.actor` is the repo owner and that the issue carries the `model-promotion` label
- in one transaction, sets the current `active` version to `retired` and the `previous` version to `active`
- comments the result

`workflow_dispatch` with a `version` input is the fallback for rollback and for promoting a specific earlier version.

**Serving.** Nightly scoring always reads the version whose `status = 'active'` at task start. A promotion or rollback takes effect on the next nightly run, or immediately via the existing admin-gated rescore task.

### M10 — Route type groups: **five groups; bouldering not scored** (DECIDED)

- Groups: `sport`, `trad`, `alpine`, `ice`, `mixed`. They replace the earlier three-group scheme everywhere: cells, percentiles, bands, exposure, thresholds, tests.
- `ice` and `mixed` are sparse (few incidents, thin catalog), so every model partially pools across type groups (see `baseline_eb.py` and `baseline_bayes.py`). A group that stays too sparse in an area falls to `insufficient` under M6 rather than borrowing a confident score.
- **Bouldering is excluded from scoring in v2.** Bouldering incidents are rarely reported, and the risk profile (short falls onto pads, no ropes or anchors, no objective hazards) differs from roped and alpine climbing, so the data cannot support a meaningful rate. Bouldering routes show no score. This stands unless the owner reverses it.

### M11 — Route catalog: **split by discipline** (DECIDED; Phase 2 P2-1)

- **Rock** (`sport`, `trad`, rock `alpine`): **OpenBeta (CC0)**, the public primary source.
- **`ice` and `mixed`:** **MP route facts** (name, grade, location, type) are displayed, per the owner's rationale that MP compiled them from published guidebooks. Never MP descriptions, photos, comments, or other MP prose.
- **Mountaineering and glaciated objectives:** the Phase 2 **Objective layer** (GNIS, Wikidata, NPS/USFS-curated CC0 routes), scored at objective level where route-level data is missing (see Objective-level scoring).
- MP rock routes and tick aggregates (ascent counts by season/month) may be displayed; ticks are public (owner decision 2026-09-28; `DATA_LICENSE.md` wording pending legal review). MP descriptions and other prose are never displayed, served, redistributed, or sent to third parties.
- Where coverage is still thin, routes, areas and type groups show "insufficient data" with a coverage warning rather than being silently absent or scored low (see Definitions and M6).

### M12 — Lightning: **NLDN tiles 1989–2017 + GOES GLM 2018+** (DECIDED)

- **1989–2017:** NOAA NCEI SWDI NLDN daily cloud-to-ground flash counts per 0.1° tile (free; use constraint "cite dataset"), stored as `lightning_cg_nldn`. Western coverage is sparse before 1989, and there are no tiles north of 54°N.
- **2018 onward and live:** NOAA GOES Geostationary Lightning Mapper (GLM), stored as `lightning_density` (±54° latitude).
- **Missing, never zero:** outside coverage (before 1989, and north of 54°N, e.g. Denali) the covariate is NULL, and any learned term uses a missing indicator.
- **Homogeneity:** NLDN (cloud-to-ground) and GLM (total lightning) differ, so the model uses each as a within-cell, within-era anomaly against that cell's climatology for the same source; 2018–2026 overlap years calibrate the two (Phase 2 P2-7).
- Live GLM lightning near a route or objective drives a **hazard alert** (the rule-based hazard banner), not just a multiplier.

### M13 — Climber experience: **structured facts, not re-identification** (DECIDED)

- **Declined:** inferring experience from OpenBeta ticks and matching accident reports to climbers by name. Reports are mostly anonymized, matching would re-identify injured or deceased climbers, and ticks are too sparse to be reliable.
- **Instead:** Phase 2a extracts experience as a structured fact from accident records where the record states it (for example years climbing, "experienced", "first season"), with an `unknown` default. Phase 4 adds self-reported experience in user profiles and incident reports.
- Personalization by experience stays a later milestone (v3), gated as in the Phase 4 spec.

## Validation plan

**Reference split data:** only incidents allowed by Definitions (no `year_unverified`, dedupe before splitting). Spatial terms use only rows at `geocode_precision` area level or better.

The gate has four parts.

**1. Spatial block CV (primary).**
- Leave one region out over 8 state-group regions, using all years with trustworthy dates.
- This is the main estimate of generalization, because the product's question is mostly "where" and the time holdout is tiny.

**2. Rolling-origin temporal CV on pre-2019 data.**
- Origins at 2004, 2008, 2012 and 2016. Each fold trains on data up to its origin and tests on the next 3 years (all sources).
- Evaluated at month resolution, so `month`-precision AAC rows count.

**3. Honest recent holdout (reported, not gated).**
- About 50 climbing incidents from 2019–2024, plus 23 avalanche ice-climbing events from 2020 on.
- O/E is reported with an exact Poisson CI. It is too small to gate on, but a 90% CI excluding [0.5, 2] opens an issue.
- After the Phase 2 refresh, Phase 2a year-repaired AAC rows join this set.

**4. Prospective kernel backtest (once Phase 2b ingests Feb–Jul 2026 incidents).**
- **Kernel side:** the kernel's `historical_predictions` scores (23.2M rows, 2026-02-05 → 2026-07-02, computed the same day before the incidents were known) are scored against incidents in that window.
- **v2 side:** v2 is scored with as-of information: a bundle trained on data before 2026-02-05, and conditions from the archive for each date.
- The kernel caps 15% of scores at 100, so ties get average ranks, and top-10% capture splits tied mass proportionally.
- This becomes a gate component for the first promotion after the refresh.

**Metrics:**
- Poisson deviance, mean log score, and O/E per predicted-risk decile (each decile within [0.8, 1.25], with at least 8 of 10 required).
- Top-10% capture: the share of test incidents in the top 10% of cells by predicted rate.
- Band stability (M6).
- All metrics are also reported per type group; `ice` and `mixed` are reported with their small-sample CIs and are not gated separately.

**Comparison:** the kernel model is scored through the same harness. v2 must beat it on log score and top-10% capture in parts 1 and 2, and in part 4 once available.

**Leakage controls:**
- features computed as of the prediction date
- dedupe before splitting
- no `month` or `year` rows in weather joins or the CM
- exposure proxy components use only values dated before the test window, or time-invariant values such as route counts

## Conditions layer

**Rules are primary.** v2.1–v2.2 use rule multipliers only (`rules.py`). The learned CM is postponed to v2.3 behind an event-count gate, because with about 850 day-precise climbing events it is underpowered.

**Power estimate** for 1:8 matched case-crossover conditional logistic regression, per binary weather term with 10% prevalence on control days:

n ≈ (z_α + z_β)² · (1 + 1/M) / (p(1−p) · (ln OR)²)

For about 8 terms with a Bonferroni α of 0.00625, detecting OR = 1.5 at 80% power needs about **970 cases in training**, and OR = 1.3 needs about **2,300**.

Today's roughly 850 events, with 20% held out, leave about 680 for training. That gives about **60% power for OR 1.5 and about 20% for OR 1.3.** Also, about 475 of the AAC day-precise events are from 1989–1994, which concentrates the signal in one era.

**v2.3 gate:**
- At least **1,500 deduped, day-precise climbing incidents with repaired weather joins**, which gives about 1,200 in training and ≥ 80% power at OR 1.5 across 8 terms.
- **Plus**, for each rule a learned term would replace, **at least 100 exposed cases**.
- Until the gate is met, `conditions.py` exists with tests but is not trained.
- The event count is shown in every model card, so progress toward the gate is visible.

## Retraining and monitoring

- **Schedule:** baseline monthly. CM quarterly once v2.3 is live. Either one runs an extra time when at least 50 newly reviewed incidents land.
- **Every retrain goes through the gate plus M9.** A gate failure marks the challenger `rejected` and opens a `model-rejected` issue.
- **Nightly monitoring** is logged to the active version's `metrics.monitoring`:

  | Signal | Alert when |
  |---|---|
  | PSI of each rule and CM input vs. its training distribution | > 0.25 |
  | Rolling 12-month O/E (once the accident refresh is regular) | outside [0.7, 1.4] |
  | Share of routes insufficient | ±10-point shift |
  | Capacity | M2 thresholds |

## Serving

- The nightly batch runs on the existing Celery worker (Railway). Budget: < 30 min for all routes. `score()` is vectorized pandas/NumPy, so the cost is dominated by per-bucket weather fetches.
- The bundle is loaded once per process and cached by version. The detail endpoint reads `route_daily_scores` for the number and calls `score()` plus `explain()` for the breakdown, so the two can never disagree.
- **Weather sources**, all landing in `cell_daily_conditions` in m/s:
  - Open-Meteo forecast per bucket
  - Open-Meteo/ERA5 archive for history
  - SNOTEL
  - NWS alerts API
  - AirNow AQI
  - NOAA SWDI NLDN tiles (history 1989–2017) and GOES GLM lightning (2018+ and live; M12)

## UX safety rules

- No "safe", "low risk", or green state anywhere. The `lower` band renders as "lower than typical for this type", in a neutral palette.
- Insufficient data → a gray chip "Not enough reported incidents to estimate", with no percentile. The map uses a neutral hatch, and the about page publishes coverage by state (M6).
- An active NWS warning, live GLM lightning, or another rule-based hazard overrides the percentile with a hazard banner.
- Thin catalog coverage (M11) shows a coverage warning; bouldering routes show no score (M10).
- A permanent disclaimer on every score: "Based on reported incidents and estimated climbing traffic, not on the current condition of this route."
- Explanations list the top 3 terms in plain language, plus nearby incidents labelled "context, not cause".

## Testing strategy

- **Unit tests (`tests/ml/`):**
  - EB posterior matches a hand-computed Poisson-Gamma result on toy cells, and shrinkage moves low-exposure cells toward the parent.
  - Percentiles rank within each of the five type groups; bands are assigned correctly; bouldering and `unknown` type routes get no score.
  - Partial pooling: a toy `ice` cell with zero incidents shrinks toward the pooled type rate, not to zero.
  - The M6 selection picks the max-coverage passing combination, and the sensitivity step moves stricter on a failing neighbour.
  - Rule multipliers apply and are versioned.
  - Wind is in m/s end to end.
  - A gate-failing challenger is `rejected` and never promoted; a passing one auto-promotes.
  - The diff report shows the `LARGE SHIFT` banner at > 5% band change, a > 10-point coverage shift, and a new family.
  - The ski-approach filter uses the configured radius (default 1 km).
  - Lightning is NULL, not 0, before 1989 and north of 54°N; NLDN fills 1989–2017 and GLM 2018+.
  - Objectives with `coverage_level = 'objective'` get exactly one row per type group in `objective_daily_scores`; `thin` ones are `insufficient`; objectives with route-level coverage get none.
  - Rollback flips `active`/`previous` atomically, and the partial unique index rejects two actives.
- **Property tests (Hypothesis):**
  - RR > 0 and q05 ≤ mean ≤ q95.
  - Raising route density with fixed incidents never raises RR.
  - CM = 1 when no rule fires.
  - `score()` is deterministic and row-order invariant, and every scored route or objective × type group gets exactly one row.
- **Data-contract tests:**
  - No `month` or `year` row, and no `year_unverified` row, enters a weather join or CM training set.
  - No row below area-level `geocode_precision` enters spatial terms.
- **Backtest as a CI job:** `uv run python -m app.ml.backtest --fixture small` runs on every PR against a frozen, anonymized fixture with synthetic exposure and metric floors. The full backtest runs as a manual/scheduled workflow using the read-only `analyst` role.
- **Parity test:** batch output and detail-endpoint output for 100 sampled routes are identical.
- **Capacity benchmark:** the synthetic 1×/2×/4× cell fit runs in `model-train.yml` on `workflow_dispatch` with `benchmark=true`.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Reporting bias (popular areas report more) | Composite exposure covariates with estimated coefficients; RR framed as "reported incidents"; disclaimer. |
| Exposure proxy weak or regionally uneven | Missingness indicators; coefficients estimated, not fixed; coverage by state in the model card; in-app ticks in v3. |
| Tiny recent holdout (~50) | Spatial CV primary; rolling-origin CV; prospective kernel backtest after the refresh; recent O/E reported with CI. |
| Synthetic or corrupted dates leak into weather joins | `date_precision` rules; `year_unverified` exclusion; data-contract tests. |
| Fallback geocodes inflate CO/WY, CA | `geocode_precision` gate on spatial terms (Phase 2a repair). |
| Sparse data gives overconfident scores | Hierarchical shrinkage; M6 data-driven insufficient state. |
| Learned CM overfits ~850 events | Rules primary; v2.3 event-count gate from the power estimate. |
| Route-type label noise (65% generic) | Versioned Phase 2a mapper; unmappable routes marked `unknown` and not scored. |
| Sparse `ice`/`mixed` data | Partial pooling across the five type groups; M6 insufficient state; per-type metrics reported with CIs. |
| Thin public catalog (OpenBeta ice/mixed/alpine) | MP ice/mixed facts, objective-level scoring, coverage badges; coverage warnings instead of silent absence. |
| MP data exposure | Only ice/mixed MP route facts are displayed (M11); MP prose, rock data and ticks stay internal; Phase 1/2 CI guards. MP tick components removable by table drop + retrain. |
| Bad auto-promotion | Hard gate (a failing model is never promoted); diff report flags large band and coverage shifts at the top; owner notified on every promotion; `/rollback` in one comment. |
| Training outgrows the Actions runner | Cell-based cost, M2 budget and scaling projection, Railway one-off fallback. |
| Schedule silently disabled after 60 days of inactivity | healthchecks.io dead-man check on training. |
| Model silently degrades | PSI and O/E monitoring; gate on every retrain. |

## Milestones

**MVP-0: Audit + harness** (no user-visible change)
- **Done 2026-09-27:** the data audit (date precision, avalanche/NPS scope, geocodes, duplicates, route types, exposure, sparsity, severity, post-2019 counts, weather, `historical_predictions`).
- **Remaining in Phase 3:**
  - commit the audit to `docs/data-audit/2026-09.md`
  - `ml/backtest.py` running the kernel model through spatial and rolling-origin splits, with kernel metrics stored as the first `model_registry` row (`family = kernel`, `status = active`)
  - the `model_registry` migration and the `trainer` role (via SQL)
  - re-measure region and leaf-area counts on the OpenBeta area tree
- **Moved to Phase 2a:** the `date_precision` and `geocode_precision` columns and backfill, dedupe, AAC year repair, weather relink and wind units, and the route-type mapper.
- *Accept:* audit doc merged; the kernel metrics reproduce with one command; the registry holds the kernel row.

**MVP-1: EB baseline + single scoring path + promotion pipeline** (requires Phase 2a merged, and the Phase 2b route-density proxy at minimum)
- Build: `exposure.py`, `baseline_eb.py`, `scoring.py`, `bundle.py`, `thresholds.py`, `promote.py`, `route_daily_scores`, `objective_daily_scores`, the nightly task reading the active version, the detail endpoint on `score()`, the insufficient-data UX, the disclaimer, and `model-train.yml`, `model-rollback.yml` and the capacity benchmark. CM = 1 in this milestone.
- *Accept:*
  - beats the kernel on log score and top-10% capture in spatial CV and rolling-origin CV
  - decile O/E within [0.8, 1.25] for ≥ 8 of 10
  - the M6 procedure and sensitivity report are in the model card
  - capacity within budget, with the scaling curve recorded
  - parity test green
  - first promotion auto-published, owner notified via the promotion issue
  - `/rollback` exercised once in staging
  - the three kernel implementations removed

**v2.1: Rule-based conditions (primary conditions layer)**
- Build: `rules.py` wired into `score()` and the hazard banner. Visibility is dropped unless a real source is added (the Open-Meteo path hardcodes 10000 m in `weather_service.py`).
- *Accept:* each rule has a documented source; the hazard override is visible in the UI; no regression on baseline gate metrics.

**v2.2: Full hierarchical baseline** (requires the Phase 2b static features and proxy components)
- Build: `route_static_features` populated; `baseline_bayes.py` in NumPyro; BYM2 on H3 r5.
- *Accept:*
  - R-hat < 1.01, no divergences
  - within M2 budget
  - beats MVP-1 on log score in spatial and rolling-origin CV
  - prospective backtest run if the Phase 2b refresh has landed

**v2.3: Learned CM** (only after the event-count gate: ≥ 1,500 day-precise deduped climbing incidents with repaired weather, and ≥ 100 exposed cases per replaced rule)
- Build: case-crossover strata and conditional logistic regression, then LightGBM with monotone constraints if it wins.
- *Accept:*
  - held-out conditional log-likelihood better than rules-only
  - reliability slope in [0.8, 1.2]
  - replaced rules retired, others kept

**v3: Personal + live data**
- In-app ticks join the exposure proxy (and may dominate by estimated coefficient); user-submitted incidents via Phase 4 triage; experience personalization from Phase 2a structured experience facts and Phase 4 self-reports (M13).
- *Accept:* retrains run unattended through the gate and M9 auto-publication, with the owner reviewing each promotion issue.
