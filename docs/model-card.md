# SafeAscent risk model card (eb_v1)

Skeleton. Task 20's generator fills every section from the trained bundle and the backtest results; nothing here is a result yet.

## Data window

## Event counts toward the v2.3 gate (P3:416)

## Coverage by state and type group

## Gate metrics

With and without MP ticks, ski approach incidents and LLM route links.

## G1–G7

## Thresholds

Read from `backend/app/ml/tuning.py`:

- `LEVEL_COVERAGE` = (0.85, 0.97): each confidence level's shown 90% range must cover held-out outcomes between 85% and 97%. The window is wide on purpose: shown ranges are deliberately conservative, about 94-95% against the nominal 90%, so a 0.95 ceiling would fail on that drift alone, which is not miscalibration.
- `STABLE_CHURN` = 0.10: the median route-weighted share of cells changing 25/50/75 band across subsamples must be at most 0.10.
- `STABLE_SPEARMAN` = 0.8: the median Spearman rank correlation with the full fit must be at least 0.8.

The stability check is a random state-group SUBSAMPLE, not a bootstrap. Each draw takes the eight state groups with replacement and keeps only which groups were drawn. Multiplicity is ignored, because weighting incidents by m without weighting exposure would raise those states' observed-to-expected ratio m-fold. Drawn groups train once, and undrawn groups are held out of training like a spatial fold, never kept with zero incident weight (that would read whole states as accident-free). A subsample whose fit fails counts as unstable, not dropped.

## Known limitations

- The design is rank-deficient in `fam_mountain` vs the ridged `a_*` group effects; identifiable through the ridge, with the unpenalized `fam_mountain` absorbing the shared family effect (DP3).
- State-only incidents (`park_centroid`, `region_fallback`, `unknown`, far points) never enter an EB level's counts (DP4, F39), but they count as geo evidence for their state and type group: such a cell is scored, never gray, and reads at most "low" on that evidence alone. The card reports the state-only incident share per state and the number of cells kept out of gray only by it.
- `hazard_center[g, m]` is the log of the exposure-weighted mean hazard over the cells of type group g and month m; `log_rref - hazard_center` is log θ̄(g, m), the exposure-weighted posterior mean the evidence term is measured against.
- Rolling-origin folds are point-in-time for ticks only (F22): `season_share` and `route_density` use today's climate window and catalog, so the backtests are not fully point-in-time.
- The log-score comparison against the old kernel is not like-for-like. The kernel yields a point score, which the backtest maps to an expected count with a Poisson calibration fitted on the training cells (Poisson log score at that point mean). The model is scored on its full predictive distribution (a negative binomial from the phi-widened Gamma). Read the gap as indicative, not as a pure model-versus-model difference.
