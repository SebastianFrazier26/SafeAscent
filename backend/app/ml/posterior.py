"""From EB posteriors to what a route shows (A:95-110, A:112-131): RR with a 90% interval,
national within-type-group percentiles, the confidence level, the evidence split, and the
per-10,000-logged-ascents line where ticks are complete. Insufficient means no geo evidence and
no feature neighbour within d* (A:145-150), nothing else: a cell with zero local accidents still
gets a borrowed number, a range and its confidence level."""

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


@dataclass(frozen=True)
class Thresholds:
    d_star: float
    conf_high: float
    conf_medium: float
    phi: float

    def __post_init__(self) -> None:
        if not (np.isfinite(self.d_star) and self.d_star >= 0):
            raise ValueError("d_star must be finite and >= 0")
        if not (1.0 <= self.conf_high <= self.conf_medium < np.inf):
            raise ValueError("need 1 <= conf_high <= conf_medium < inf")
        if not (1.0 <= self.phi < np.inf):
            raise ValueError("phi (dispersion widening) must be finite and >= 1")


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


def gamma_interval(shape: F64, rate: F64, phi: float) -> tuple[F64, F64, F64]:
    """(q05, mean, q95) of the phi-widened Gamma(shape/phi, rate/phi); the mean is unchanged by phi.
    Below shape/phi ~= 0.01373 a Gamma's mean sits above its own q95 (MIN_PRIOR_SHAPE 0.05 with phi
    > 3.64 reaches that), so q95 is lifted to the mean: the shown range always holds the shown number
    (route_scores CHECK rr_q05 <= rr_mean <= rr_q95). q05 <= median <= mean always holds for a Gamma."""
    a, b = shape / phi, rate / phi
    mean = np.asarray(shape / rate, dtype=np.float64)
    lo = np.asarray(gammaincinv(a, Q_LO) / b, dtype=np.float64)
    hi = np.asarray(np.maximum(gammaincinv(a, Q_HI) / b, mean), dtype=np.float64)
    return lo, mean, hi


def percentile_in(x: F64, ref: F64, w: F64) -> F64:
    """Weighted mid-rank of each x in ref: 100 * (W(< x) + 0.5 * W(= x)) / W."""
    order = np.argsort(ref)
    r = ref[order]
    c0 = np.concatenate([[0.0], np.cumsum(w[order])])
    below, upto = c0[np.searchsorted(r, x, "left")], c0[np.searchsorted(r, x, "right")]
    return np.asarray(100.0 * (below + 0.5 * (upto - below)) / c0[-1], dtype=np.float64)


def insufficient_mask(geo_evidence: NDArray[np.bool_], evidence_dist: F64, d_star: float) -> NDArray[np.bool_]:
    return np.asarray(~geo_evidence & (evidence_dist > d_star), dtype=np.bool_)


def confidence_level(ratio: F64, insufficient: NDArray[np.bool_], th: Thresholds) -> NDArray[np.object_]:
    return np.asarray(np.where(insufficient, "insufficient",
                               np.where(ratio <= th.conf_high, "high",
                                        np.where(ratio <= th.conf_medium, "medium", "low"))), dtype=object)


def per10k_reason(tick_coverage: F64, ticks_value: F64, insufficient: NDArray[np.bool_]) -> NDArray[np.object_]:
    """None where the per-10K line shows (or the cell is insufficient and shows nothing)."""
    full = (tick_coverage == 1.0) & (ticks_value > 0)
    missing = np.isnan(tick_coverage) | np.isnan(ticks_value)
    reason = np.where(missing, "ticks_missing",
                      np.where(tick_coverage < 1.0, "ticks_incomplete", "no_ticks_logged")).astype(object)
    reason[full | insufficient] = None
    return np.asarray(reason, dtype=object)


def summarize(cells: CellData, design: Design, coef: F64, cp: CellPost, *,
              thresholds: Thresholds, evidence_dist: F64, years: float,
              geo_evidence: NDArray[np.bool_]) -> CellSummary:
    n = cells.n
    if evidence_dist.shape != (n,) or geo_evidence.shape != (n,) or cp.post.shape.shape != (n,):
        raise ValueError("evidence_dist, geo_evidence and the posterior must be one value per cell")
    if not (np.isfinite(years) and years > 0):
        raise ValueError("years must be finite and > 0")
    ex = list(design.exposure_cols)
    eta = linear_predictor(design.X, coef)
    eta_exp = linear_predictor(design.X[:, ex], coef[ex])
    mu0 = np.exp(np.clip(eta, -ETA_CLIP, ETA_CLIP))
    lo, mean, hi = gamma_interval(cp.post.shape, cp.post.rate, thresholds.phi)
    hazard = np.exp(eta - eta_exp)
    exposure = np.exp(eta_exp)
    insufficient = insufficient_mask(geo_evidence, evidence_dist, thresholds.d_star)
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
            ok = sel & ~insufficient
            if ok.any():
                for k in range(3):
                    pct[k, ok] = percentile_in(rr[k, ok], rr[1, ok], cells.n_routes[ok])
    rr[:, insufficient] = np.nan
    suff = ~insufficient
    if not (np.all(np.isfinite(rr[:, suff])) and np.all(rr[0, suff] > 0)):
        raise ValueError("non-finite or non-positive RR on a sufficient cell")
    with np.errstate(divide="ignore", over="ignore"):
        ratio = rr[2] / rr[0]
    conf = confidence_level(ratio, insufficient, thresholds)

    ch, b, wc = cp.chain, cp.blend_geo, cp.omega_cell
    geo_inner = ch["omega_area"] * ch["O_area"] + (1 - ch["omega_area"]) * (
        ch["omega_region"] * ch["O_region"] + (1 - ch["omega_region"]) * ch["omega_state"] * ch["O_state"])
    n_geo = wc * np.maximum(cp.y_own, 0.0) + (1 - wc) * b * geo_inner
    n_feat = (1 - wc) * (1 - b) * ch["omega_arch"] * ch["O_arch"]

    per_year = mu0 * np.vstack([lo, mean, hi]) / years
    full = (cells.tick_coverage == 1.0) & (cells.ticks_value > 0) & suff
    p10 = np.full((3, n), np.nan)
    p10[:, full] = 1e4 * per_year[:, full] / cells.ticks_value[full]
    return CellSummary(
        rr_mean=rr[1], rr_q05=rr[0], rr_q95=rr[2], percentile=pct[1], pct_q05=pct[0], pct_q95=pct[2],
        conf_level=conf, insufficient=insufficient,
        n_eff_geo=np.where(insufficient, np.nan, n_geo), n_eff_feature=np.where(insufficient, np.nan, n_feat),
        per10k_mean=p10[1], per10k_q05=p10[0], per10k_q95=p10[2],
        per10k_reason=per10k_reason(cells.tick_coverage, cells.ticks_value, insufficient),
    )
