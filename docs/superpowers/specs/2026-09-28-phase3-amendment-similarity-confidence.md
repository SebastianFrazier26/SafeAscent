# Phase 3 Amendment: Similarity Pooling, Confidence, Unified Scorer

- **Date:** 2026-09-28
- **Status:** Owner decisions D1–D5 recorded 2026-09-28 (§2). Per-10,000-logged-ascents unit and counts-only interim recorded 2026-09-28 (§3.4). Still pending owner review: the §3.4 headline choice and the questions in §6.
- **Amends:** `docs/superpowers/specs/2026-09-27-phase3-model-v2-design.md` (cited below as "P3:line").
- **Principle:** accuracy of safety results comes first. Everything here must still pass the P3 validation gate.

## 1. Problem and evidence

**A. A route with no nearby accident reads as "no risk".**
- The kernel only uses accidents inside a spatial Gaussian. When nothing is nearby, the raw score falls toward 0. The interim fix turns raw < 0.05 into gray "insufficient" (`backend/app/services/risk_bands.py:15-22`, `estimable_score` at `:51`). The code comment says this is interim "until the Phase 3 model borrows evidence from similar routes anywhere" (`:18`).
- P3 pools evidence only up the geographic hierarchy (crag → region → state → national, P3:138) and across type groups, plus BYM2 on geographic adjacency (P3:139). Static route features do not enter until v2.2 (P3:139, P3:150).
- Nothing lets an Alaska alpine route inform a similar Rocky Mountain alpine route except the national type-group rate. P3 expects many routes to be insufficient (46,124 routes sit in zero-incident cells, P3:40), and M6 makes "minimum incidents in the parent region" a hard criterion (P3:271).

**B. The Ascents tab rate is not a usable metric** (`backend/app/api/v1/mp_routes.py:1640-1795`, `frontend/src/utils/accidentRate.ts`).
- The numerator counts only accidents linked to this exact `route_id` (`mp_routes.py:1700-1726`), which contradicts D1.
- The denominator is `mp_ticks`. It matches 5.4% of routes, covers CA and NV only, and is capped at 16 ticks per route (P3:45). One accident over 16 ticks is 62.5 per 1,000, and 2 over 1 tick is 2,000 per 1,000 (`mp_routes.py:1746-1762`).
- The numerator covers the whole accident record while the ticks cover a short, recent window, so the two don't line up in time.
- Months with no ticks report 0.0 (`:1748`). `accidentRate.ts:16` hides this in the UI.

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

## 2. Owner decisions (2026-09-28)

- **D1 — Similar-route evidence, shipping in MVP-1.** A route's estimate draws on characteristically similar routes anywhere, by geographic proximity **and** by feature similarity across regions. Having no accident on the exact route, or nearby, never by itself yields a low or "safe" result. The owner's words: "a core of the design... fundamental to our algorithm/rating sys". There is no geo-only first release.
- **D2 — Insufficient means no evidence at all.** Gray "insufficient" is used only when there is neither geographic nor feature-similar evidence. Otherwise:
  - There is no hard minimum ascent or incident count. Each score, and each Ascents-tab figure, carries a **confidence** level.
  - A **Low confidence** route (some similar evidence, wide interval) shows its number, its range, a **hatched marker** and a "Low confidence" label.
- **D3 — Bands stay at 25/50/75 through MVP-1.** They switch to the P3 percentile bands (P3:63-70) only if held-out calibration shows the percentile bands are more accurate. This is gate G7 in §5.
- **D4 — No interim popup fix.** The P3 unified scorer (P3:127, P3:433) fixes the marker/popup disagreement. **Relaunch caveat:** a relaunch before Phase 3 MVP-1 ships with the two scorers disagreeing as described in §1C, so the release notes must state that the popup and the map can show different numbers.
- **D5 — Keep the Ascents tab and the MP rock routes.**
  - MP ticks are public and may be displayed. MP descriptions or other prose are never displayed.
  - The owner is consulting a lawyer on `DATA_LICENSE.md`.
  - The following text still says MP ticks and rock data are internal only or never shown, and must be updated to match this decision: P3:20, P3:252 and P3:343 (M5/M11), `DATA_LICENSE.md:20`, and the "Data rules" section of the repo `CLAUDE.md`. Those edits are outside this amendment and are tracked in §6.

## 3. Design choices

### 3.1 Similarity features and distance

**MVP-1 feature set (D1).** Only features that can be computed reliably for every route, from coordinates and catalog type alone:

| Feature | Source | Why it is reliable now |
|---|---|---|
| type group | Phase 2a mapper (P3:56) | Already required. `unknown` is not scored. |
| elevation | DEM at the route or location point (`elevation_service.py`) | Available everywhere, and continuous. |
| monthly climate normals: mean tmax and tmin, precipitation, snowfall, freeze-thaw days | ERA5/Open-Meteo archive per 0.1° grid bucket (P3:161), which P3 already needs for weather | Available everywhere. Captures temperature, cold and snow. |
| latitude | coordinates | A proxy for daylight and season length. |

**Deferred to v2.2**, each added when its data proves reliable, and gated like every other change:
- **Aspect, as northness and eastness.** Route coordinates are inherited from the parent location (`mp_routes.py:612`), so a DEM aspect at that point describes the area, not the face.
- **Lithology** (Macrostrat) and the **glaciated flag.** These need the Phase 2b static-feature build.
- **Difficulty.** Accident grades are unlinked (§1C), so difficulty carries no incident signal until Phase 2a links accidents to routes.
- **Length and pitch count.** Too sparse in the catalog.

MVP-1 therefore needs elevation and climate normals in `route_static_features` (P3:150). That pulls those two Phase 2b items forward as an MVP-1 prerequisite.

| Option | How it works | Trade-offs |
|---|---|---|
| **A. Gower distance with hand-set weights** | A mixed-type distance. Type-group mismatch uses the existing type-similarity matrix. | Transparent and quick. The weights are guesses, and guessed weights are exactly what P3 is trying to replace. |
| **B. Features as model covariates** | Features enter the log-rate model with estimated coefficients. Similarity is implicit: routes with similar covariates get similar predicted rates. | Principled. The data decides which features matter, and Alaska informs Rocky through β. Assumes log-linear effects, so it misses interactions such as alpine × high elevation unless they are added. |
| **C. Gower distance with CV-learned weights** | Per-feature weights are chosen by spatial-CV log score. | Captures "similar overall". It adds tuning cost and a risk of overfitting on about 850 incidents. |

**Recommendation: B as the backbone, plus C for defining "feature-similar neighbours."**
- C is used only for archetypes (§3.2), the evidence count (§3.3), and the explanation text ("similar routes elsewhere").
- Missing features are skipped in the distance and count against confidence.

### 3.2 Pooling and weighting scheme

| Option | How it works | Trade-offs |
|---|---|---|
| **A. Covariates plus the existing geo hierarchy** | Option B from §3.1 feeding P3's current random effects. | Minimal change. Transfer happens only through linear β. |
| **B. A crossed archetype random effect** | Cluster cells into K archetypes by the §3.1 distance (k-prototypes). Add `u_archetype` (and `u_archetype×type`) crossed with, not nested in, the geo hierarchy. | Captures interactions nonparametrically. Alaska alpine and Rocky alpine share an archetype. The effect is explicit and explainable. K and the clustering choice need CV, and crossed effects break P3's closed-form EB (MVP-1 needs iteration). |
| **C. Kernel prior in feature space** | Each cell's shrinkage target is the kernel-weighted O/E of its k nearest feature-space cells anywhere, then EB-shrunk as in P3. | The most flexible, close to a GP. The kNN (k ≈ 200) keeps cost bounded against about 42K cells. Bandwidth is tuned by CV, and the prior is harder to explain. |

**Recommendation: A + B, both in MVP-1 (D1).**
- **MVP-1:** add the §3.1 MVP-1 features to the stage-1 GLM (P3:138). In stage 2, shrink toward a **precision-weighted blend of the geo parent and the archetype parent** instead of the geo parent alone.
- **v2.2:** crossed `u_archetype` alongside geo and BYM2 in NumPyro (P3:139), with the deferred features added.
- **C** is carried as a challenger through the same gate.
- **Geo-only** is not a shipping candidate. It is fit only as a diagnostic reference in §5, so the gain from pooling is measured and reported.

### 3.3 Confidence metric (score and Ascents tab)

| Option | Definition | Trade-offs |
|---|---|---|
| **A. Effective evidence n_eff** | The pooling-weighted count of incidents and exposure behind the estimate. Shown as, for example, "≈ 44 comparable incidents: 3 local, 41 from similar routes." | Intuitive and shows where the evidence comes from. It is not a probability, and the counts can look reassuring when the neighbours are only loosely similar. |
| **B. Interval-based level** | The 90% credible interval (q05, q95) already exists in P3 (P3:61). Map the ratio q95/q05 to Low, Medium or High, with the thresholds chosen by calibration (§5). | Principled, and directly measures uncertainty. Abstract on its own. |
| **C. Local data share** | The Poisson-Gamma weight n/(n+k), i.e. the share of the estimate coming from this route's own geographic data. | Explains "how local" the number is. It is not uncertainty: a well-backed pooled estimate would read as weak. |

**Recommendation: B as the single headline confidence, with A as supporting text.** C is folded into A's local-versus-similar split.
- **Display (D2):**
  - **High and Medium:** the number with its range.
  - **Low:** the number, the range, a hatched marker and a "Low confidence" label.
  - **Insufficient:** gray, no number.
- **Stored per row:** `conf_level`, `n_eff_geo`, `n_eff_feature`, `rr_q05`, `rr_q95` (adds 3 columns to `route_daily_scores`, P3:158).
- The Ascents tab uses the same fields.
- The old response `confidence` (0-100) is still advertised in the `predict.py:89-91` docstring but no longer exists in the schema. Delete that stale text.

### 3.4 Replacing "accidents per 1,000 ascents" (owner decision 2026-09-28)

MP ticks may be displayed (D5), but they remain a partial, capped sample, not exposure (§1B).

**Owner decision (2026-09-28): unit and interim display.**
- The Phase 3 shrunk estimate (option B's form, prior from similar routes) is stated **per 10,000 logged ascents**, never per 1,000, per 100 or as a percentage. Display wording: "About N per 10,000 logged ascents (likely A–B) · <confidence>", with the range from the posterior interval and the confidence label from §3.3.
- Until that model exists, the Ascents tab shows counts only ("N accidents · M logged ascents", overall and per month) in neutral styling, with no rate of any kind. Shipped on `fix/ascents-counts-and-join`, which also moved the accident join to `accidents.mp_route_id`.
- Whether option C's relative index is the headline above the per-10,000 line is still open (§6 Q1).

| Option | Display | Trade-offs |
|---|---|---|
| **A. Raw counts with an exact interval** | "N reported incidents; M logged ascents," with a Poisson upper bound. | Honest, with no modeling. M is not exposure (capped at 16, CA/NV only), and N = 0 still reads as safe. |
| **B. Bayesian shrunk rate** | Posterior incidents per 1,000 logged ascents, with a Gamma prior from similar routes. | Fixes the 0 and the >1,000 values. The per-ascent unit exists only where ticks exist, and the capped denominator biases it. |
| **C. Relative-to-similar-routes index** | "1.8× (0.9–3.4×) the reported-incident rate of comparable routes." This is the model's RR and CI (P3:61), plus the seasonal profile from the model instead of per-route monthly rates. | Works everywhere and satisfies D1. It is consistent with the score by construction. It is relative rather than absolute, and it depends on the model. |

**Recommendation: C as the headline**, with the D2 confidence label.
- Show local reported-incident counts and logged-tick counts (with their monthly distribution) as context, labelled "logged ascents, a partial sample". Never compute a rate from them.
- Ticks also enter the model as an exposure covariate (P3 M5).
- Retire the per-1,000 rate in `accidentRate.ts` and the per-route monthly rates.

### 3.5 Unified scorer and caching (D4)

- **No interim fix.** The kernel paths are not reconciled, since P3 deletes them at MVP-1. Until then, the §1C disagreement is a known relaunch caveat (D4).
- **MVP-1:**
  - One `ml/scoring.score()` feeds `route_daily_scores`, which is the source of truth for the map, the popup and every explanation endpoint (`mp_routes.py:713, 1033, 1300`).
  - The popup stops using `bypass_cache: true` and reads the stored row.
  - Redis keys become `(route_id, date, model_version)`, so a promotion or rollback invalidates the cache.
  - The detail endpoint reads the stored number and confidence, and calls `explain()` only for the breakdown.
- **Parity test** (extends P3:475): the marker, the popup and the explanation endpoints return an identical score and confidence for 100 sampled routes.

## 4. Interaction with the current rules

- **raw < 0.05 → gray** (`risk_bands.py:22, 51`): this is a kernel-only rule and is retired at MVP-1. A pooled posterior rate is never zero, so the "0.0" failure mode goes away by construction.
- **The new insufficient rule (D2)** is frozen per model version. A route is `insufficient` when any of these holds:
  - its type group is `unknown`, or it is bouldering (unchanged, P3:56)
  - it has no incident-bearing geo level below national **and** no feature-space neighbour within distance d\*
  - its catalog coverage is thin (unchanged, P3:72)

  d\* is the largest distance at which neighbours still improve held-out log score.
- **M6 changes.** The "min parent-region incidents" axis (P3:271) is replaced by d\*. The CI-ratio axis (P3:269) stops creating `insufficient` and instead sets `conf_level`. The calibration and stability checks must still pass **on the Low-confidence stratum on its own**. If they fail there, the gate fails. This way accuracy, not a minimum count, protects the user.
- **Bands (D3).** `risk_bands.py:24` (25/50/75, lower-inclusive) stays the single band definition through MVP-1, applied to the MVP-1 0-100 display score (P3:63). The P3 percentile bands (lower <25, typical 25–75, elevated 75–90, high ≥90) replace it only if they pass G7.

## 5. Evaluation (added to the P3 gate)

- **G1 — Pooling gain, reported against a geo-only reference.** Run the P3 harness: spatial block CV (leave one region out, P3 Validation §1), which directly tests cross-region transfer, plus rolling-origin CV.
  - Compare, in order: geo-only (reference only), + MVP-1 covariates, + archetype blend, and the kernel prior (challenger).
  - Report the log-score gain with a 90% CI and the decile O/E.
  - The MVP-1 shipping design must not be worse than geo-only on either measure. If it is, the feature set or the pooling is broken, and MVP-1 does not ship until that is fixed.
- **G2 — Zero-history stratum.** Compute O/E and log score for held-out cells that had **0 training incidents**. This is the direct test of "no accident ≠ safe." Pooling must bring O/E closer to 1 than the geo-only reference.
- **G3 — Negative control.** Permute features across regions. The pooling gain should disappear; if it does not, the gain is leakage or artifact.
- **G4 — Confidence validity.**
  - The 90% intervals should cover about 90% of held-out counts (PIT histogram).
  - Coverage should hold within each confidence level.
  - Error should shrink monotonically as confidence rises.
  - The Low stratum must meet the calibration requirement from §4 (D2).
- **G5 — Leakage.** Archetype rates and d\* are fit on training folds only. The static features themselves are time-invariant.
- **G6 — Parity.** The §3.5 parity test.
- **G7 — Band scheme (D3).**
  - On pooled held-out predictions, compute each band's observed incident rate and O/E, with exact Poisson CIs, under both 25/50/75 and the P3 percentile bands.
  - A scheme passes when every band has O/E within [0.8, 1.25] and the observed rates rise monotonically from band to band.
  - Switch to percentile bands only if they pass **and** beat 25/50/75 on band-level log score, with a 90% CI above 0. Otherwise keep 25/50/75.
  - The result goes in the model card, and it is re-run whenever a promotion might change the scheme.

## 6. Open questions for the owner

1. **Per-1,000 replacement:** the unit is decided (per 10,000 logged ascents, shrunk, counts-only until then; §3.4). Still open: approve option C as the headline above it?
2. **Aligning the MP text with D5:** once the lawyer's review is in, who updates P3:20, P3:252 and P3:343, `DATA_LICENSE.md:20` and the `CLAUDE.md` data rules, and when? Until then the repo gives two contradictory answers on whether ticks may be displayed.
3. **Green under 25/50/75:** the lowest band renders green today (`risk_bands.py`). Keep green through MVP-1, or use a neutral colour for it in line with "shouldn't show as safe" (P3:444)?
4. **Feature weights:** learned only from data (recommended), or with owner or expert priors, for example forcing alpine and ice to count as similar?
5. **Moving Phase 2b work forward:** do elevation and climate normals in `route_static_features` become part of Phase 2a, or an MVP-1 task?
