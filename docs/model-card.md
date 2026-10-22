# SafeAscent risk model card (eb_v1)

Skeleton. Task 20's generator fills every section from the trained bundle and the backtest results; nothing here is a result yet.

## Data window

## Event counts toward the v2.3 gate (P3:416)

## Coverage by state and type group

## Gate metrics

With and without MP ticks, ski approach incidents and LLM route links.

## G1–G7

## Thresholds

## Known limitations

- The design is rank-deficient in `fam_mountain` vs the ridged `a_*` group effects; identifiable through the ridge, with the unpenalized `fam_mountain` absorbing the shared family effect (DP3).
- State-only incidents (`park_centroid`, `region_fallback`, `unknown`, far points) never enter an EB level's counts (DP4, F39), but they count as geo evidence for their state and type group: such a cell is scored, never gray, and reads at most "low" on that evidence alone. The card reports the state-only incident share per state and the number of cells kept out of gray only by it.
- `hazard_center[g, m]` is the log of the exposure-weighted mean hazard over the cells of type group g and month m; `log_rref - hazard_center` is log θ̄(g, m), the exposure-weighted posterior mean the evidence term is measured against.
- Rolling-origin folds are point-in-time for ticks only (F22): `season_share` and `route_density` use today's climate window and catalog, so the backtests are not fully point-in-time.
