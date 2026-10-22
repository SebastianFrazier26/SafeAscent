"""CV selection of everything a version freezes (P3 M6 as amended by A:151; G5): archetype K and
weights, phi, d*, confidence thresholds, plus state-group subsample stability. Only held-out fold results
(backtest.run_fold, which scores with y_own = fit.y_train) are read, so nothing is tuned on data it
later scores.

Coverage is always the shown interval (posterior.interval90, the nominal/widened union) on the count
scale, via metrics.interval_coverage, never raw PIT of the widened Gamma. Because the union only ever
widens, phi = 1 already over-covers on conservative ranges and is then the pick; nothing here narrows
an interval below interval90."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date
from itertools import combinations
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray

from app.ml.backtest import FoldData, FoldResult, RawData, fit_full, run_fold
from app.ml.cells import STATE_GROUPS
from app.ml.metrics import calibration, interval_coverage, mean_log_score, nb_logpmf, spearman
from app.ml.model import Fitted, ModelFitError, ModelParams
from app.ml.dataset import CellData
from app.ml.posterior import PHI_GRID, Thresholds, confidence_level, insufficient_mask, interval90, percentile_in
from app.services.risk_bands import RISK_BAND_THRESHOLDS

__all__ = ["K_GRID", "WEIGHT_GRID", "PHI_GRID", "RATIO_GRID", "NEUTRAL", "TuneResult", "select_conf",
           "errors_ordered", "select_d_star", "select_phi", "stability", "thresholds_from", "tune"]

F64 = NDArray[np.float64]
K_GRID = (8, 16, 32, 64)
WEIGHT_GRID = (0.0, 1.0, 2.0)
RATIO_GRID = (2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 15.0)
BAND_QUANTILES = (0.0, 0.25, 0.5, 0.75, 0.9, 1.0)
TARGET_COVERAGE = 0.90
# Shown ranges are deliberately conservative (about 94-95% of a nominal 90%); a 0.95 ceiling would fail
# G4 on that drift alone, which is not miscalibration (controller ruling, Task 18 review).
LEVEL_COVERAGE = (0.85, 0.97)
# Pooled |log O/E| carries sampling noise; a level may beat the less confident one by this much.
ERROR_ORDER_TOL = 0.01
MIN_BAND_EXPECTED = 20.0
MIN_LEVEL_EXPECTED = 20.0
MIN_LOW_CALIBRATION_EXPECTED = 50.0
WEIGHT_GAIN_MIN = 0.001  # mean log score is per cell, so this is a per-cell gain
STABLE_SPEARMAN = 0.8
STABLE_CHURN = 0.10
# Disabled conf levels are None (posterior refuses inf); d_star inf never grays by distance, so the
# sweeps score every held-out cell.
NEUTRAL = Thresholds(d_star=math.inf, conf_high=None, conf_medium=None, phi=1.0)


class TuneResult(NamedTuple):
    params: ModelParams
    thresholds: Thresholds
    report: dict[str, Any]


@dataclass(frozen=True)
class _Pooled:
    obs: F64
    shape: F64
    rate: F64
    scale: F64
    mu: F64
    routes: F64
    evidence_dist: F64
    insufficient: NDArray[np.bool_]
    geo_evidence: NDArray[np.bool_]
    n_eff_geo: F64
    n_eff_feature: F64
    y_own: F64


_FIELDS = {"obs": "obs", "shape": "shape", "rate": "rate", "scale": "scale", "mu": "mu_point", "routes": "routes",
           "evidence_dist": "evidence_dist", "insufficient": "insufficient", "geo_evidence": "geo_evidence",
           "n_eff_geo": "n_eff_geo", "n_eff_feature": "n_eff_feature", "y_own": "y_own"}


def _cat(results: list[FoldResult]) -> _Pooled:
    if not results:
        raise ValueError("no fold results to select on")
    return _Pooled(**{k: np.concatenate([getattr(r, f) for r in results]) for k, f in _FIELDS.items()})


def thresholds_from(*, d_star: float, conf: tuple[float, float] | None, phi: float) -> Thresholds:
    """No selected pair disables both levels (None): every sufficient cell then reads "low" and gate G4
    fails, never "everything high"."""
    return Thresholds(d_star=d_star, conf_high=None if conf is None else conf[0],
                      conf_medium=None if conf is None else conf[1], phi=phi)


def phi_coverage(results: list[FoldResult]) -> dict[float, float]:
    R = _cat(results)
    return {f: interval_coverage(R.obs, R.shape, R.rate, R.scale, f) for f in PHI_GRID}


def select_phi(results: list[FoldResult]) -> float:
    """The PHI_GRID value whose pooled shown-interval coverage is closest to 0.90, ties to the smaller."""
    cov = phi_coverage(results)
    return min(PHI_GRID, key=lambda f: (abs(cov[f] - TARGET_COVERAGE), f))


def select_d_star(pooled: list[FoldResult], geo: list[FoldResult], dist: F64) -> float:
    """Upper edge of the last evidence-distance band, walking out from the nearest, where pooling beats
    geo-only on log score; bands under MIN_BAND_EXPECTED expected incidents are skipped. 0 when the
    first counted band already shows no gain."""
    P, G = _cat(pooled), _cat(geo)
    gain = nb_logpmf(P.obs, P.shape, P.rate, P.scale) - nb_logpmf(G.obs, G.shape, G.rate, G.scale)
    finite = np.isfinite(dist)
    if not finite.any():
        return 0.0
    edges = np.quantile(dist[finite], BAND_QUANTILES)
    d_star, lo = 0.0, -np.inf
    for hi in edges[1:]:
        band = (dist > lo) & (dist <= hi)
        lo = hi
        if P.mu[band].sum() < MIN_BAND_EXPECTED:
            continue
        if gain[band].mean() <= 0.0:
            break  # neighbours this far stop helping; farther bands are not considered
        d_star = float(hi)
    return d_star


def errors_ordered(errs: list[float]) -> bool:
    """Mean |log O/E| per counted level, most confident first, never falls as confidence drops
    (beyond ERROR_ORDER_TOL)."""
    return all(a <= b + ERROR_ORDER_TOL for a, b in zip(errs, errs[1:]))


def select_conf(results: list[FoldResult], phi: float, *, d_star: float = math.inf) -> tuple[float, float] | None:
    """(t_high, t_medium) from RATIO_GRID pairs. Levels come from posterior.confidence_level (width
    plus the evidence gate) on the cells that stay sufficient under d_star, exactly as shown."""
    R = _cat(results)
    insufficient = R.insufficient | insufficient_mask(R.geo_evidence, R.n_eff_feature, R.evidence_dist, d_star)
    keep = ~insufficient
    lo, _, hi = interval90(R.shape, R.rate, phi)
    with np.errstate(divide="ignore", over="ignore"):
        ratio = np.asarray(hi / lo, dtype=np.float64)
    total_routes = float(R.routes[keep].sum())
    if total_routes <= 0:
        return None
    best: tuple[float, float, float] | None = None
    for th, tm in combinations(RATIO_GRID, 2):
        conf = confidence_level(ratio, insufficient, Thresholds(d_star, th, tm, phi), R.n_eff_geo,
                                R.n_eff_feature, R.y_own)
        ok, errs = True, []
        for lvl in ("high", "medium", "low"):
            sel = keep & (conf == lvl)
            exp = float(R.mu[sel].sum())
            if exp < MIN_LEVEL_EXPECTED:
                continue
            cov = interval_coverage(R.obs[sel], R.shape[sel], R.rate[sel], R.scale[sel], phi)
            ok &= LEVEL_COVERAGE[0] <= cov <= LEVEL_COVERAGE[1]
            errs.append(abs(math.log(max(float(R.obs[sel].sum()), 0.5) / exp)))
        ok &= errors_ordered(errs)
        low = keep & (conf == "low")
        if R.mu[low].sum() >= MIN_LOW_CALIBRATION_EXPECTED:
            ok &= calibration(R.mu[low], R.obs[low]).passed
        if ok:
            share = float(R.routes[keep & ((conf == "high") | (conf == "medium"))].sum() / total_routes)
            if best is None or share > best[0]:  # pairs come smallest t_high first, so ties keep it
                best = (share, th, tm)
    return None if best is None else (best[1], best[2])


def _score(fold_data: list[FoldData], params: ModelParams,
           cache: dict[str, Fitted]) -> tuple[float, list[FoldResult]]:
    res = [run_fold(fd, params, NEUTRAL, stage1_cache=cache) for fd in fold_data]
    R = _cat(res)
    return mean_log_score(R.obs, R.shape, R.rate, R.scale), res


def tune(fold_data: list[FoldData]) -> TuneResult:
    # One stage-1 cache for this one fold_data list: every sweep below changes stage 2 only.
    cache: dict[str, Fitted] = {}
    base = ModelParams()
    scored = {k: _score(fold_data, replace(base, k_arch=k), cache) for k in K_GRID}
    k_best = max(K_GRID, key=lambda k: (scored[k][0], -k))
    params = replace(base, k_arch=k_best)
    best, res = scored[k_best]
    weights = list(params.arch_weights)
    for j in range(len(weights)):
        for w in WEIGHT_GRID:
            trial = weights.copy()
            trial[j] = w
            if w == weights[j] or sum(trial) == 0:
                continue
            sc, r = _score(fold_data, replace(params, arch_weights=tuple(trial)), cache)
            if sc > best + WEIGHT_GAIN_MIN:
                best, res, weights = sc, r, trial
    params = replace(params, arch_weights=tuple(weights))
    cov = phi_coverage(res)
    phi = select_phi(res)
    geo = [run_fold(fd, replace(params, blend=False), NEUTRAL, stage1_cache=cache) for fd in fold_data]
    d_star = select_d_star(res, geo, _cat(res).evidence_dist)
    conf = select_conf(res, phi, d_star=d_star)
    th = thresholds_from(d_star=d_star, conf=conf, phi=phi)
    report = {"k_arch": k_best, "k_scores": {str(k): scored[k][0] for k in K_GRID}, "arch_weights": weights,
              "phi": phi, "phi_coverage": {str(f): c for f, c in cov.items()}, "d_star": d_star,
              "conf_high": th.conf_high, "conf_medium": th.conf_medium, "conf_selected": conf is not None,
              "cv_log_score": best}
    return TuneResult(params, th, report)


def _band(p: F64) -> NDArray[np.int64]:
    return np.digitize(p, RISK_BAND_THRESHOLDS).astype(np.int64)  # lower-inclusive, as color_code_for


def _rerank(rr: F64, cells: CellData, sel: NDArray[np.bool_]) -> F64:
    """Route-weighted within-(type group, month) percentiles among the sel cells only."""
    out = np.full(cells.n, np.nan)
    for gi in np.unique(cells.g[sel]):
        for m in range(1, 13):
            s = sel & (cells.g == gi) & (cells.month == m)
            if s.any():
                out[s] = percentile_in(rr[s], rr[s], cells.n_routes[s])
    return out


def stability(raw: RawData, params: ModelParams, thresholds: Thresholds, *, cutoff: date | None,
              n_subsamples: int = 20, seed: int = 0) -> dict[str, Any]:
    """Random state-group subsample (P3:276), not a bootstrap: each draw takes the eight groups with
    replacement and keeps only which groups were drawn, ignoring multiplicity, then refits on those
    groups' states. Undrawn groups are held out of training, as in a spatial fold, rather than kept
    with zero incident weight: zero weight on cells that keep their exposure would read whole states
    as accident-free, and zero local accidents never means safe. Multiplicity is ignored because
    multiplying incident weights by m without multiplying exposure would raise those states' O/E
    m-fold. A subsample's universe is its drawn cells, so both fits are re-ranked into within-(group,
    month) percentiles among the cells sufficient in both, and the stats are their Spearman and the
    route-weighted share changing 25/50/75 band. A subsample whose fit fails or leaves no comparable
    cells counts as unstable (Spearman -1, churn 1) in n_failed, never dropped."""
    fit, cells, _ = fit_full(raw, params, cutoff=cutoff)
    base = fit.summarize(cells, fit.y_train, thresholds)
    groups = list(STATE_GROUPS)
    cell_state = np.asarray(cells.states)[cells.state_idx[cells.area_idx]]
    rng = np.random.default_rng(seed)
    rhos: list[float] = []
    churn: list[float] = []
    failed = 0
    for _ in range(n_subsamples):
        draw = rng.choice(len(groups), size=len(groups), replace=True)
        states = frozenset(s for i in np.unique(draw) for s in STATE_GROUPS[groups[int(i)]])
        drawn = np.isin(cell_state, sorted(states))
        try:
            if not drawn.any():
                raise ModelFitError("subsample draws no state holding a cell")
            f, c, _ = fit_full(raw, params, cutoff=cutoff, states=states)
        except ModelFitError:
            failed += 1
            rhos.append(-1.0)
            churn.append(1.0)
            continue
        s = f.summarize(c, f.y_train, thresholds)
        ok = drawn & ~base.insufficient & ~s.insufficient
        pb, ps = _rerank(base.rr_mean, cells, ok), _rerank(s.rr_mean, cells, ok)
        rho = spearman(pb[ok], ps[ok])
        w = cells.n_routes[ok]
        if rho is None or not w.sum() > 0:
            failed += 1  # an undefined correlation counts as unstable, never as 0
            rhos.append(-1.0)
            churn.append(1.0)
            continue
        rhos.append(rho)
        churn.append(float(np.average(_band(pb[ok]) != _band(ps[ok]), weights=w)))
    med_rho, med_churn = float(np.median(rhos)), float(np.median(churn))
    return {"method": "state_group_subsample", "median_spearman": med_rho, "median_band_churn": med_churn,
            "n_subsamples": n_subsamples, "n_failed": failed, "spearman": rhos, "band_churn": churn,
            "passed": bool(med_rho >= STABLE_SPEARMAN and med_churn <= STABLE_CHURN)}
