"""From EB posteriors to what a route shows (A:95-110, A:112-131): RR with a 90% interval,
national within-type-group percentiles, the confidence level, the evidence split, and the
per-10,000-logged-ascents line where ticks are complete. Insufficient means no geo evidence, no
archetype evidence and no feature neighbour within d* (A:145-150, rework R4), nothing else: a cell
with zero local accidents still gets a borrowed number, a range and its confidence level."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.special import gammaincinv

from app.ml.cells import TYPE_GROUPS
from app.ml.dataset import CellData
from app.ml.eb import CellPost
from app.ml.features import Design
from app.ml.glm import ETA_CLIP, linear_predictor

F64 = NDArray[np.float64]
Q_LO, Q_HI = 0.05, 0.95
# Task 18 selects phi from this grid (tuning imports it from here); above its top a fitted phi is a
# misfit to fix, not a width to show.
PHI_GRID = (1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
# Evidence gate on "high" (rework R2, owner to set; Task 15/18 may tune): a narrow range on borrowed
# evidence alone is prior strength, not knowledge of the route.
N_HIGH = 10.0
N_GEO = 5.0
# The own-incident arm of the gate needs one whole incident of effective mass: fractional y_own
# (placement weights) of 0.02 is not an incident at this route.
N_OWN = 1.0
GEO_TOL = 1e-9
TICKS_COMPLETE = 1.0 - 1e-9


def _check_ratio(name: str, v: float | None) -> None:
    if v is not None and not (np.isfinite(v) and v >= 1.0):
        raise ValueError(f"{name} must be None (level disabled) or finite and >= 1")


@dataclass(frozen=True)
class Thresholds:
    """d_star = inf means never gray by distance. conf_high / conf_medium = None disables that level
    (Task 18's select_conf finding nothing gives conf_high None: no cell is "high"); inf is refused so
    a missing threshold can never silently read as "everything high"."""

    d_star: float
    conf_high: float | None
    conf_medium: float | None
    phi: float
    n_high: float = N_HIGH
    n_geo: float = N_GEO
    n_own: float = N_OWN

    def __post_init__(self) -> None:
        if np.isnan(self.d_star) or self.d_star < 0:
            raise ValueError("d_star must be >= 0 (inf allowed: never gray by distance)")
        _check_ratio("conf_high", self.conf_high)
        _check_ratio("conf_medium", self.conf_medium)
        if self.conf_high is not None and self.conf_medium is not None and self.conf_high > self.conf_medium:
            raise ValueError("need conf_high <= conf_medium")
        if not (1.0 <= self.phi <= max(PHI_GRID)):
            raise ValueError(f"phi must lie in [1, {max(PHI_GRID)}] (max of PHI_GRID)")
        if not all(np.isfinite(v) and v >= 0 for v in (self.n_high, self.n_geo, self.n_own)):
            raise ValueError("n_high, n_geo and n_own must be finite and >= 0")


@dataclass(frozen=True)
class CellSummary:
    rr_mean: F64
    rr_q05: F64
    rr_q95: F64
    percentile: F64
    pct_q05: F64
    pct_q95: F64
    conf_level: NDArray[np.object_]
    insufficient: NDArray[np.bool_]
    n_eff_geo: F64
    n_eff_feature: F64
    per10k_mean: F64
    per10k_q05: F64
    per10k_q95: F64
    per10k_reason: NDArray[np.object_]


def interval90(shape: F64, rate: F64, phi: float) -> tuple[F64, F64, F64]:
    """(q05, mean, q95): the union of the nominal Gamma(shape, rate) and the phi-widened
    Gamma(shape/phi, rate/phi) 90% intervals, so phi only ever widens. Widening alone would pull q95
    down for small shapes (below shape/phi ~= 0.01373 under the mean), making zero-incident cells
    look safer as phi grows. The single interval definition: Task 15/18's PIT coverage and phi
    selection must call this, never re-derive quantiles. MIN_PRIOR_SHAPE (0.05) keeps the nominal
    q95 above the mean, so q95 >= mean is asserted, not clamped."""
    a, b = shape / phi, rate / phi
    mean = np.asarray(shape / rate, dtype=np.float64)
    lo = np.asarray(np.minimum(gammaincinv(shape, Q_LO) / rate, gammaincinv(a, Q_LO) / b), dtype=np.float64)
    hi = np.asarray(np.maximum(gammaincinv(shape, Q_HI) / rate, gammaincinv(a, Q_HI) / b), dtype=np.float64)
    if not np.all(hi >= mean):
        raise AssertionError("q95 below the mean: posterior shape under the MIN_PRIOR_SHAPE floor")
    return lo, mean, hi


def percentile_in(x: F64, ref: F64, w: F64) -> F64:
    """Weighted mid-rank of each x in ref: 100 * (W(< x) + 0.5 * W(= x)) / W."""
    order = np.argsort(ref)
    r = ref[order]
    c0 = np.concatenate([[0.0], np.cumsum(w[order])])
    below, upto = c0[np.searchsorted(r, x, "left")], c0[np.searchsorted(r, x, "right")]
    return np.asarray(100.0 * (below + 0.5 * (upto - below)) / c0[-1], dtype=np.float64)


def n_eff_geo(cp: CellPost) -> F64:
    """Geographic effective incident mass behind a cell: its own incidents plus what its prior borrows
    from area, region and state. The one definition; model.predict and summarize both call it."""
    ch, b, wc = cp.chain, cp.blend_geo, cp.omega_cell
    inner = ch["omega_area"] * ch["O_area"] + (1 - ch["omega_area"]) * (
        ch["omega_region"] * ch["O_region"] + (1 - ch["omega_region"]) * ch["omega_state"] * ch["O_state"])
    return np.asarray(wc * np.maximum(cp.y_own, 0.0) + (1 - wc) * b * inner, dtype=np.float64)


def insufficient_mask(geo_evidence: NDArray[np.bool_], n_eff_feature: F64, evidence_dist: F64,
                      d_star: float) -> NDArray[np.bool_]:
    """Gray only with no evidence at all: no geo incident, no archetype incident, no neighbour within d*."""
    return np.asarray(~geo_evidence & (n_eff_feature == 0) & (evidence_dist > d_star), dtype=np.bool_)


def confidence_level(ratio: F64, insufficient: NDArray[np.bool_], th: Thresholds, n_eff_geo: F64,
                     n_eff_feature: F64, y_own: F64) -> NDArray[np.object_]:
    """Width sets the level, evidence caps it. "high" needs n_eff_geo + n_eff_feature >= n_high and
    either y_own >= n_own or n_eff_geo >= n_geo; failing that it falls to whatever conf_medium gives
    (so "low" when medium is disabled). Neither "high" nor "medium" without some evidence (> 0).
    A disabled (None) level is skipped."""
    by_ratio_high = np.zeros(ratio.shape, dtype=np.bool_) if th.conf_high is None else ratio <= th.conf_high
    by_ratio_med = np.zeros(ratio.shape, dtype=np.bool_) if th.conf_medium is None else ratio <= th.conf_medium
    total = n_eff_geo + n_eff_feature
    some = total > 0
    evidence = some & (total >= th.n_high) & ((y_own >= th.n_own) | (n_eff_geo >= th.n_geo))
    high = by_ratio_high & evidence
    medium = by_ratio_med & some
    return np.asarray(np.where(insufficient, "insufficient",
                               np.where(high, "high", np.where(medium, "medium", "low"))), dtype=object)


def per10k_reason(tick_coverage: F64, ticks_value: F64, insufficient: NDArray[np.bool_]) -> NDArray[np.object_]:
    """None where the per-10K line shows (or the cell is insufficient and shows nothing)."""
    full = (tick_coverage >= TICKS_COMPLETE) & (ticks_value > 0)
    missing = np.isnan(tick_coverage) | np.isnan(ticks_value)
    reason = np.where(missing, "ticks_missing",
                      np.where(tick_coverage < TICKS_COMPLETE, "ticks_incomplete", "no_ticks_logged")).astype(object)
    reason[full | insufficient] = None
    return np.asarray(reason, dtype=object)


def summarize(cells: CellData, design: Design, coef: F64, cp: CellPost, *,
              thresholds: Thresholds, evidence_dist: F64, years: float,
              geo_evidence: NDArray[np.bool_]) -> CellSummary:
    n = cells.n
    if evidence_dist.shape != (n,) or geo_evidence.shape != (n,) or cp.post.shape.shape != (n,):
        raise ValueError("evidence_dist, geo_evidence and the posterior must be one value per cell")
    if geo_evidence.dtype != np.bool_:
        raise ValueError("geo_evidence must have bool dtype")
    if np.any(np.isnan(evidence_dist)):
        raise ValueError("evidence_dist has NaN; use inf for 'no neighbour'")
    if not (np.isfinite(years) and years > 0):
        raise ValueError("years must be finite and > 0")
    ex = list(design.exposure_cols)
    eta = linear_predictor(design.X, coef)
    eta_exp = linear_predictor(design.X[:, ex], coef[ex])
    mu0 = np.exp(np.clip(eta, -ETA_CLIP, ETA_CLIP))
    lo, mean, hi = interval90(cp.post.shape, cp.post.rate, thresholds.phi)
    hazard = np.exp(eta - eta_exp)
    exposure = np.exp(eta_exp)

    ch, b, wc = cp.chain, cp.blend_geo, cp.omega_cell
    n_geo = n_eff_geo(cp)
    n_feat = np.asarray((1 - wc) * (1 - b) * ch["omega_arch"] * ch["O_arch"], dtype=np.float64)

    # geo_evidence may exceed the chain (state-only incidents, DP4, never in any level's O), but a cell
    # whose chain carries geo mass must never be reported as lacking it.
    if np.any((n_geo > GEO_TOL) & ~geo_evidence):
        raise ValueError("geo_evidence is False on a cell whose n_eff_geo > 0")
    insufficient = insufficient_mask(geo_evidence, n_feat, evidence_dist, thresholds.d_star)
    suff = ~insufficient
    if np.any(cells.n_routes[suff] <= 0):
        raise ValueError("a sufficient cell has n_routes <= 0; percentiles are route-weighted")
    rr = np.full((3, n), np.nan)
    pct = np.full((3, n), np.nan)
    for gi in range(len(TYPE_GROUPS)):
        for m in range(1, 13):
            sel = (cells.g == gi) & (cells.month == m)
            if not sel.any():
                continue
            # r_ref takes every cell in (g, m), insufficient ones included: their expected counts are
            # still part of the national rate, they just do not get a number of their own.
            r_ref = float((mu0[sel] * mean[sel]).sum() / exposure[sel].sum())
            for k, theta in enumerate((lo, mean, hi)):
                rr[k, sel] = theta[sel] * hazard[sel] / r_ref
            ok = sel & suff
            if ok.any():
                for k in range(3):
                    pct[k, ok] = percentile_in(rr[k, ok], rr[1, ok], cells.n_routes[ok])
    rr[:, insufficient] = np.nan
    if not (np.all(np.isfinite(rr[:, suff])) and np.all(rr[0, suff] > 0)):
        raise ValueError("non-finite or non-positive RR on a sufficient cell")
    with np.errstate(divide="ignore", over="ignore"):
        ratio = rr[2] / rr[0]
    conf = confidence_level(ratio, insufficient, thresholds, n_geo, n_feat, cp.y_own)

    per_year = mu0 * np.vstack([lo, mean, hi]) / years
    full = (cells.tick_coverage >= TICKS_COMPLETE) & (cells.ticks_value > 0) & suff
    p10 = np.full((3, n), np.nan)
    p10[:, full] = 1e4 * per_year[:, full] / cells.ticks_value[full]
    return CellSummary(
        rr_mean=rr[1], rr_q05=rr[0], rr_q95=rr[2], percentile=pct[1], pct_q05=pct[0], pct_q95=pct[2],
        conf_level=conf, insufficient=insufficient,
        n_eff_geo=np.where(insufficient, np.nan, n_geo), n_eff_feature=np.where(insufficient, np.nan, n_feat),
        per10k_mean=p10[1], per10k_q05=p10[0], per10k_q95=p10[2],
        per10k_reason=per10k_reason(cells.tick_coverage, cells.ticks_value, insufficient),
    )
