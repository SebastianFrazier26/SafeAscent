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
- State-only incidents (`park_centroid`, `region_fallback`, `unknown`, far points) never enter an EB level's counts (DP4, F39). The card reports the state-only incident share per state.
- Rolling-origin folds are point-in-time for ticks only (F22): `season_share` and `route_density` use today's climate window and catalog, so the backtests are not fully point-in-time.
