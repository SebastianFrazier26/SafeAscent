# Phase 3 Amendment: Similarity Pooling, Confidence, Unified Scorer

- **Date:** 2026-09-28
- **Status:** Draft for owner review. Nothing here is decided until the owner signs off on the recommendations and the open questions.
- **Amends:** `docs/superpowers/specs/2026-09-27-phase3-model-v2-design.md` (cited below as "P3:line").
- **Principle:** accuracy of safety results comes first. Every recommendation below ships only if it passes the P3 validation gate. A pooling scheme that looks right but does not improve held-out accuracy is rejected.

## 1. Problem and evidence

**A. A route with no nearby accident reads as "no risk".**
- The kernel only uses accidents inside a spatial Gaussian. When nothing is nearby, the raw score falls toward 0. The interim fix turns raw < 0.05 into gray "insufficient" (`backend/app/services/risk_bands.py:15-22`, `estimable_score` at `:51`). The code comment says this is interim "until the Phase 3 model borrows evidence from similar routes anywhere" (`:18`).
- P3 pools evidence only up the geographic hierarchy (crag → region → state → national, P3:138) and across type groups, plus BYM2 on geographic adjacency (P3:139). Static route features (elevation, aspect, lithology) do not enter until v2.2 (P3:139, P3:150).
- Nothing lets an Alaska alpine route inform a similar Rocky Mountain alpine route except the national type-group rate. P3 expects many routes to be insufficient (46,124 routes sit in zero-incident cells, P3:40), and M6 makes "minimum incidents in the parent region" a hard criterion (P3:271).

**B. The Ascents tab rate is not a usable metric** (`backend/app/api/v1/mp_routes.py:1640-1795`, `frontend/src/utils/accidentRate.ts`).
- The numerator counts only accidents linked to this exact `route_id` (`mp_routes.py:1700-1726`), which contradicts owner decision 1.
- The denominator is `mp_ticks`. It matches 5.4% of routes, covers CA and NV only, and is capped at 16 ticks per route (P3:45). One accident over 16 ticks is 62.5 per 1,000, and 2 over 1 tick is 2,000 per 1,000 (`mp_routes.py:1746-1762`).
- The numerator covers the whole accident record while the ticks cover a short, recent window, so the two don't line up in time.
- Months with no ticks report 0.0 (`:1748`). `accidentRate.ts:16` hides this in the UI.
- **Licensing conflict:** the tab displays MP tick counts, but P3 says MP ticks are internal only and never displayed (P3:20, P3:343).

**C. The map marker and the route popup use two different scorers.**
- **Marker:** the nightly job scores each location once and adjusts per route. The path is `safety_computation_optimized.py:775-843`, then `location_safety_computation.py:301` (`compute_location_base_score_vectorized`), then `:428` (`compute_route_risk_score`). The result is written to Redis, and the map reads it (`mp_routes.py:473-484`).
- **Popup:** `useRouteSafety`, then `fetchRouteSafety` (`frontend/src/services/api.ts:146-150`), which calls `POST /mp-routes/{id}/safety` with **`bypass_cache: true`**. That runs live `predict_route_safety` (`mp_routes.py:593-667`, `predict.py:63`) and then `calculate_safety_score_vectorized` (`safety_algorithm_vectorized.py:283`).

| # | Aspect | Nightly marker | Live popup |
|---|---|---|---|
| 1 | Route-type filter | None. All accidents are weighted by `ROUTE_TYPE_WEIGHTS` (`location_safety_computation.py:469`). | Accidents beyond 50 km are dropped unless the type weight is ≥ 0.85 (`predict.py:140-174`). |
| 2 | Spatial bandwidth, temporal λ, elevation decay | Taken from the location's most common route type (`safety_computation_optimized.py:821`, `location_safety_computation.py:328`). | Taken from the route's own type (`safety_algorithm_vectorized.py:106`). |
| 3 | Seasonal boost | A flat average that assumes 25% same-season (`location_safety_computation.py:371-372`). | An exact same-season match per accident (`safety_algorithm_vectorized.py:148-150`). |
| 4 | Route elevation | `None`, so the elevation weight is 1 (`safety_computation_optimized.py:374`). | Looked up from a DEM (`predict.py:112-121`). |
| 5 | Weather similarity | `historical_stats=None`, so no extreme-weather term (`safety_computation_optimized.py:579`). The forecast is prefetched at night, keyed to 0.01° (`:810`). | Uses historical stats (`predict.py:291`) and fetches the forecast live at click time (`:230`). |
| 6 | Route-type normalization | Substring match: "Trad, Sport" becomes `sport`, and `unknown` routes are excluded (`safety_computation_optimized.py:360, 394-417`). | Exact-map lookup: "Trad, Sport" and "Alpine, Ice" both become `trad`, and unknown routes are scored as trad (`mp_routes.py:118-159`). |
| 7 | Evidence count for `estimable_score` | Accidents with influence above 1e-6 (`location_safety_computation.py:411, 509`). | All accidents that survive the filter (`safety_algorithm_vectorized.py:422`). |

Grade weighting is inert in both paths: accident grades are always `None` (`safety_computation_optimized.py:325`, `predict.py:207-210`).

## 2. Requirements (owner decisions 2026-09-28)

- **R1 — Similar-route evidence.** A route's estimate must draw on characteristically similar routes anywhere: geographic proximity, **and** feature similarity (elevation, sun and aspect, temperature and climate, rock type, grade and difficulty, type group), across regions. The lack of an accident on the exact route, or nearby, never by itself yields a low or "safe" result.
- **R2 — Insufficient means no evidence at all.** "Insufficient data" applies only when neither geo-similar nor feature-similar evidence exists. It replaces the P3 M6 "min incidents in parent region" criterion (P3:271).
- **R3 — No hard minimum ascent count.** Each score, and each Ascents-tab figure, instead carries a **confidence** metric that says how much data backs it.
- **R4 — Replace "accidents per 1,000 ascents"** if a better metric exists (§3.4).
- **R5 — One scorer.** The map marker, the popup, and the explanation endpoints (forecast, risk breakdown, time of day: `mp_routes.py:713, 1033, 1300`) show the same number for the same route and date. This extends P3:127 and P3:433 to cover the interim period.

## 3. Design choices

### 3.1 Similarity features and distance

Candidate features, per route or scoring cell:
- type group
- normalized difficulty within the grading system
- elevation
- relief or length and pitch count
- aspect, as northness and eastness
- glaciated flag
- lithology class
- monthly climate normals: mean tmax and tmin, precipitation, snowfall, freeze-thaw days
- latitude, as a daylight proxy

| Option | How it works | Trade-offs |
|---|---|---|
| **A. Gower distance with hand-set weights** | A mixed-type distance. Type-group mismatch uses the existing type-similarity matrix. | Transparent and quick. The weights are guesses, and guessed weights are exactly what P3 is trying to replace. |
| **B. Features as model covariates** | Features enter the log-rate model with estimated coefficients. Similarity is implicit: routes with similar covariates get similar predicted rates. | Principled. The data decides which features matter, and Alaska informs Rocky through β. Assumes log-linear effects, so it misses interactions such as alpine × high elevation unless they are added. |
| **C. Gower distance with CV-learned weights** | Per-feature weights are chosen by spatial-CV log score. | Captures "similar overall". It adds tuning cost and a risk of overfitting on about 850 incidents. |

**Recommendation: B as the backbone, plus C for defining "feature-similar neighbours."** C is used only for archetypes (§3.2), the evidence count (§3.3), and the explanation text ("similar routes elsewhere"). Missing features are skipped in the distance and count against confidence. Grade needs Phase 2a accident-to-route linking before it carries any signal.

### 3.2 Pooling and weighting scheme

| Option | How it works | Trade-offs |
|---|---|---|
| **A. Covariates plus the existing geo hierarchy** | Option B from §3.1 feeding P3's current random effects. | Minimal change. Transfer happens only through linear β. |
| **B. A crossed archetype random effect** | Cluster cells into K archetypes by the §3.1 distance (k-prototypes). Add `u_archetype` (and `u_archetype×type`) crossed with, not nested in, the geo hierarchy. | Captures interactions nonparametrically. Alaska alpine and Rocky alpine share an archetype. The effect is explicit and explainable. K and the clustering choice need CV, and crossed effects break P3's closed-form EB (MVP-1 needs iteration). |
| **C. Kernel prior in feature space** | Each cell's shrinkage target is the kernel-weighted O/E of its k nearest feature-space cells anywhere, then EB-shrunk as in P3. | The most flexible, close to a GP. The kNN (k ≈ 200) keeps cost bounded against about 42K cells. Bandwidth is tuned by CV, and the prior is harder to explain. |

**Recommendation: A + B.**
- **MVP-1:** add the available static features to the stage-1 GLM (P3:138). In stage 2, shrink toward a **precision-weighted blend of the geo parent and the archetype parent** instead of the geo parent alone.
- **v2.2:** crossed `u_archetype` alongside geo and BYM2 in NumPyro (P3:139).
- **C** is carried as a challenger through the same gate.
- **Ablation guard:** geo-only, then + covariates, then + archetype, each promoted only if it wins (§5).

### 3.3 Confidence metric (score and Ascents tab)

| Option | Definition | Trade-offs |
|---|---|---|
| **A. Effective evidence n_eff** | The pooling-weighted count of incidents and exposure behind the estimate. Shown as, for example, "≈ 44 comparable incidents: 3 local, 41 from similar routes." | Intuitive and shows where the evidence comes from. It is not a probability, and the counts can look reassuring when the neighbours are only loosely similar. |
| **B. Interval-based level** | The 90% credible interval (q05, q95) already exists in P3 (P3:61). Map the ratio q95/q05 to Low, Medium or High, with the thresholds chosen by calibration (§5). | Principled, and directly measures uncertainty. Abstract on its own. |
| **C. Local data share** | The Poisson-Gamma weight n/(n+k), i.e. the share of the estimate coming from this route's own geographic data. | Explains "how local" the number is. It is not uncertainty: a well-backed pooled estimate would read as weak. |

**Recommendation: B as the single headline confidence, with A as supporting text.** C is folded into A's local-versus-similar split.
- Stored per row: `conf_level`, `n_eff_geo`, `n_eff_feature`, `rr_q05`, `rr_q95` (adds 3 columns to `route_daily_scores`, P3:158).
- The Ascents tab uses the same fields.
- The old response `confidence` (0-100) is still advertised in the `predict.py:89-91` docstring but no longer exists in the schema. Delete that stale text.

### 3.4 Replacing "accidents per 1,000 ascents"

| Option | Display | Trade-offs |
|---|---|---|
| **A. Raw counts with an exact interval** | "N reported incidents; M logged ascents," with a Poisson upper bound. | Honest, with no modeling. M is not exposure (capped, CA/NV only), and showing M breaks M11. With N = 0 it still reads as safe. |
| **B. Bayesian shrunk rate** | Posterior incidents per 1,000 logged ascents, with a Gamma prior from similar routes. | Fixes the 0 and the >1,000 values. The per-ascent unit only exists where ticks exist, and it still displays MP-derived exposure. |
| **C. Relative-to-similar-routes index** | "1.8× (0.9–3.4×) the reported-incident rate of comparable routes." This is the model's RR and CI (P3:61), plus the seasonal profile from the model instead of per-route monthly rates. | Works everywhere and satisfies R1. It is consistent with the score by construction and displays no MP data. It is relative rather than absolute, and it depends on the model. |

**Recommendation: C.**
- Show local raw counts ("3 reported incidents within this area since 1990") as context only. Never show MP tick counts.
- Ticks stay an internal exposure covariate (P3 M5).
- Retire `accidentRate.ts` and the per-route monthly rates.

### 3.5 Unified scorer and caching

| Option | What changes | Trade-offs |
|---|---|---|
| **A. Interim read-through** | The popup and explanation endpoints read the stored nightly value (Redis, then `historical_predictions`). On a miss, they recompute with the **nightly** function for that route's location. Drop `bypass_cache: true` for the headline number. | Small, restores marker/popup agreement immediately, and does not change the model. The popup no longer reflects forecasts updated during the day. |
| **B. Reconcile the kernel paths now** | One shared kernel function, resolving the 7 differences in §1C. | Each resolution is a model change that needs a backtest. That effort goes into code P3 deletes at MVP-1. |
| **C. Wait for MVP-1** | `ml/scoring.score()` plus `route_daily_scores` (P3:127, P3:433, parity test). | Correct end state, but the disagreement persists until then. |

**Recommendation: A now, C as the end state; skip B.**
- In MVP-1, `route_daily_scores` is the source of truth.
- Redis keys become `(route_id, date, model_version)`, so a promotion or rollback invalidates the cache.
- The detail endpoint reads the stored number and confidence, and calls `explain()` only for the breakdown.
- Add a parity test: marker equals popup for 100 sampled routes.

## 4. Interaction with the current rules

- **raw < 0.05 → gray** (`risk_bands.py:22, 51`): this is a kernel-only rule and is retired at MVP-1. A pooled posterior rate is never zero, so the "0.0" failure mode goes away by construction.
- **The new insufficient rule** is frozen per model version. A route is `insufficient` when any of these holds:
  - its type group is `unknown`, or it is bouldering (unchanged, P3:56)
  - it has no incident-bearing geo level below national **and** no feature-space neighbour within distance d\*
  - its catalog coverage is thin (unchanged, P3:72)

  d\* is the largest distance at which neighbours still improve held-out log score.
- **M6 changes.** The "min parent-region incidents" axis (P3:271) is replaced by d\*. The CI-ratio axis (P3:269) stops creating `insufficient` and instead sets `conf_level`. The calibration and stability checks must still pass **on the Low-confidence stratum on its own**. If they fail there, the gate fails. This way accuracy, not a minimum count, protects the user.
- **Bands.** The 25/50/75 bands on the kernel's raw 0-100 score (`risk_bands.py:24`) stay until MVP-1. P3 moves to **percentile** bands of 25/75/90, with no green and no "safe" (P3:63-70, P3:444). Showing the same percentile band as "25/50/75" would mean something different. This is open question 3.

## 5. Evaluation (added to the P3 gate)

1. **Ablation ladder through the P3 harness.** Run geo-only (the current MVP-1 design), then + feature covariates, then + archetype effect, then the kernel prior (challenger). Use spatial block CV (leave one region out, P3 Validation §1), which tests cross-region transfer directly, together with rolling-origin CV. Each step ships only if its spatial-CV log-score gain has a 90% CI above 0, **and** decile O/E does not get worse.
2. **Zero-history stratum.** Compute O/E and log score for held-out cells that had **0 training incidents**. This is the direct test of "no accident ≠ safe." Feature pooling must bring O/E closer to 1 than geo-only does.
3. **Negative control.** Permute features across regions. The pooling gain should disappear; if it does not, the gain is leakage or artifact.
4. **Confidence validity.**
   - The 90% intervals should cover about 90% of held-out counts (PIT histogram).
   - Coverage should hold within each confidence level.
   - Error should shrink monotonically as confidence rises.
   - The Low stratum must meet the calibration requirement from §4.
5. **Leakage.** Archetype rates and d\* are fit on training folds only. The static features themselves are time-invariant.
6. **Interim fix A.** A test asserts that the marker and popup scores are identical, and that the explanation endpoints return the stored score.

## 6. Open questions for the owner

1. **Timing of features:** pull a minimal static feature set into MVP-1 (elevation, type, climate normals, aspect; a partial Phase 2b dependency), or ship MVP-1 geo-only and add feature pooling at v2.2? Recommended: pull them in, because R1 is the point of the model.
2. **Low-confidence display:** show the score, its interval and a desaturated or hatched marker, or collapse it to gray? Recommended: show it, provided the Low stratum passes calibration.
3. **Bands at MVP-1:** percentile 25/75/90 from P3 with no green, or keep 25/50/75? Green conflicts with "shouldn't show as safe."
4. **Ascents tab:** agree to stop displaying MP tick counts (M11), and rename or merge the tab into "Compared with similar routes"?
5. **Ship interim fix A now?** Meanwhile, which number should users see: the nightly one (recommended, since it is what the map already shows) or the live one?
6. **Feature weights:** learned only from data (recommended), or with owner or expert priors, for example forcing alpine and ice to count as similar?
