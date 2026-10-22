"""Held-out metrics (P3:386-392, A:154-173). Every metric takes predictive Gamma posteriors so the
log score scores the full NegBin predictive, not a point.

The predictive count over the test window is theta ~ Gamma(shape, rate), N | theta ~
Poisson(theta * scale), scale = mu0 * tau. Which Gamma a metric wants:
- log score and PIT (`nb_logpmf`, `mean_log_score`, `pit_coverage`) score a predictive
  distribution, so callers pass the phi-widened Gamma (shape / phi, rate / phi); Task 18's phi
  selection calls `pit_coverage` that way.
- `interval_coverage` judges the interval the UI shows, `posterior.interval90` (the union of the
  nominal and phi-widened 90% intervals), so it takes the nominal Gamma and phi.

Missing is never 0: every array input must be finite, or the metric raises."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.special import betainc, gammaincinv, gammaln

from app.pipelines.exposure_math import spearman as exposure_spearman

Arr = NDArray[np.float64]
MetricsReport = dict[str, object]
METRICS_REPORT_KEYS = ("log_score", "deviance", "top10", "calibration", "coverage", "oe",
                       "n_cells", "observed", "expected")
PIT_LO, PIT_HI = 0.05, 0.95


def _finite(name: str, x: ArrayLike, *, min_value: float | None = None, positive: bool = False) -> Arr:
    a = np.asarray(x, dtype=np.float64)
    if not np.all(np.isfinite(a)):
        raise ValueError(f"{name} must be finite (missing is never 0: drop or impute upstream)")
    if positive and not np.all(a > 0):
        raise ValueError(f"{name} must be > 0")
    if min_value is not None and not np.all(a >= min_value):
        raise ValueError(f"{name} must be >= {min_value}")
    return a


def _predictive(k: ArrayLike, shape: ArrayLike, rate: ArrayLike, scale: ArrayLike) -> tuple[Arr, Arr, Arr]:
    kk = _finite("k", k, min_value=0.0)
    a = _finite("shape", shape, positive=True)
    b = _finite("rate", rate, positive=True)
    s = _finite("scale", scale, positive=True)
    np.broadcast_shapes(kk.shape, a.shape, b.shape, s.shape)
    return kk, a, b / (b + s)


def nb_logpmf(k: Arr, shape: Arr, rate: Arr, scale: Arr) -> Arr:
    """log P(N = k) under the Gamma-Poisson predictive; gammaln makes it continuous in k, so
    fractional (placement-weighted) counts score."""
    kk, a, p = _predictive(k, shape, rate, scale)
    return np.asarray(gammaln(kk + a) - gammaln(a) - gammaln(kk + 1.0) + a * np.log(p)
                      + kk * np.log1p(-p), dtype=np.float64)


def mean_log_score(k: Arr, shape: Arr, rate: Arr, scale: Arr) -> float:
    """Mean of `nb_logpmf`; pass the phi-widened Gamma."""
    return float(np.mean(nb_logpmf(k, shape, rate, scale)))


def poisson_deviance(k: Arr, mu: Arr) -> float:
    kk = _finite("k", k, min_value=0.0)
    m = _finite("mu", mu, positive=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        term = np.where(kk > 0, kk * np.log(kk / m), 0.0)
    return float(2.0 * np.sum(term - (kk - m)))


@dataclass(frozen=True)
class Calibration:
    """bins are (expected, observed, O/E), lowest predicted risk first."""

    scheme: Literal["decile", "quintile", "insufficient_support"]
    bins: list[tuple[float, float, float]]
    n_pass: int
    passed: bool


def _bins(pred: Arr, obs: Arr, n: int) -> list[tuple[float, float, float]]:
    order = np.argsort(pred, kind="stable")
    out = []
    for chunk in np.array_split(order, n):
        e, o = float(pred[chunk].sum()), float(obs[chunk].sum())
        out.append((e, o, o / e if e > 0 else float("nan")))
    return out


def calibration(pred: Arr, obs: Arr, *, lo: float = 0.8, hi: float = 1.25, min_expected: float = 10.0) -> Calibration:
    """O/E per equal-count bin of predicted risk (P3:275): 8 of 10 deciles in [lo, hi] when every
    decile holds >= min_expected expected incidents; else quintiles, 4 of 5, under the same support
    rule. Quintiles are a fallback for thin support only, never for a decile scheme that fails."""
    p = _finite("pred", pred, min_value=0.0)
    o = _finite("obs", obs, min_value=0.0)
    if p.shape != o.shape:
        raise ValueError("pred and obs must have the same shape")
    if not min_expected > 0:
        raise ValueError("min_expected must be > 0")
    schemes: tuple[tuple[Literal["decile", "quintile"], int, int], ...] = (("decile", 10, 8), ("quintile", 5, 4))
    for scheme, n, need in schemes:
        bins = _bins(p, o, n)
        if all(e >= min_expected for e, _, _ in bins):
            k = sum(lo <= r <= hi for _, _, r in bins)
            return Calibration(scheme, bins, k, k >= need)
    return Calibration("insufficient_support", _bins(p, o, 5), 0, False)


def top_capture(pred: Arr, obs: Arr, frac: float = 0.10) -> float:
    """Share of observed incidents in the top frac of cells by pred (P3:383). Cells tied at the cut
    count in proportion (budget left / tied cells), so the result never depends on sort order among
    ties. NaN when no incident was observed."""
    p = _finite("pred", pred)
    o = _finite("obs", obs, min_value=0.0)
    if p.shape != o.shape or p.ndim != 1 or p.size == 0:
        raise ValueError("pred and obs must be equal-length non-empty 1-D arrays")
    if not 0.0 < frac <= 1.0:
        raise ValueError("frac must lie in (0, 1]")
    total = float(o.sum())
    if total == 0:
        return float("nan")
    budget = frac * p.size
    cut = float(np.sort(p)[::-1][int(np.ceil(budget)) - 1])
    above, tied = p > cut, p == cut
    take = budget - float(above.sum())
    return (float(o[above].sum()) + float(o[tied].sum()) * take / float(tied.sum())) / total


def _nb_cdf(k: Arr, shape: Arr, p: Arr) -> Arr:
    """P(N <= k) for integer k = I_p(shape, k + 1); 0 below k = 0."""
    return np.asarray(np.where(k < 0, 0.0, betainc(shape, np.maximum(k, 0.0) + 1.0, p)), dtype=np.float64)


def _randomized_pit(kk: Arr, a: Arr, p: Arr, v: Arr) -> Arr:
    lo, hi = _nb_cdf(np.asarray(kk - 1.0, dtype=np.float64), a, p), _nb_cdf(kk, a, p)
    return np.asarray(lo + v * (hi - lo), dtype=np.float64)


def _in_band(u: Arr) -> NDArray[np.bool_]:
    return (u >= PIT_LO) & (u <= PIT_HI)


def pit_coverage(k: Arr, shape: Arr, rate: Arr, scale: Arr, *, seed: int = 0) -> float:
    """Randomized PIT of floor(k), u ~ U(F(k-1), F(k)), and the share in [0.05, 0.95]: a calibrated
    predictive gives 0.90 even at counts of 0 and 1. Pass the phi-widened Gamma."""
    kk, a, p = _predictive(k, shape, rate, scale)
    kk = np.floor(kk)
    a, p = np.broadcast_to(a, kk.shape), np.broadcast_to(p, kk.shape)
    v = np.random.default_rng(seed).uniform(size=kk.shape)
    return float(np.mean(_in_band(_randomized_pit(kk, a, p, v))))


def interval_coverage(k: Arr, shape: Arr, rate: Arr, scale: Arr, phi: float, *, seed: int = 0) -> float:
    """Coverage of the shown interval, `posterior.interval90`, on the count scale. A cell is covered
    when its randomized PIT (same draw) lies in [0.05, 0.95] under the nominal Gamma(shape, rate) or
    under the phi-widened Gamma(shape/phi, rate/phi): the union interval90 takes, carried through
    the Poisson. As scale grows it converges to the share of k/scale inside interval90; at phi = 1 it
    equals `pit_coverage` of the nominal Gamma. Pass the nominal Gamma and phi."""
    if not (np.isfinite(phi) and phi >= 1.0):
        raise ValueError("phi must be finite and >= 1")
    kk, a, p_nom = _predictive(k, shape, rate, scale)
    _, a_w, p_w = _predictive(k, np.asarray(shape) / phi, np.asarray(rate) / phi, scale)
    kk = np.floor(kk)
    v = np.random.default_rng(seed).uniform(size=kk.shape)
    nominal = _randomized_pit(kk, np.broadcast_to(a, kk.shape), np.broadcast_to(p_nom, kk.shape), v)
    widened = _randomized_pit(kk, np.broadcast_to(a_w, kk.shape), np.broadcast_to(p_w, kk.shape), v)
    return float(np.mean(_in_band(nominal) | _in_band(widened)))


def oe_ci(O: float, E: float, level: float = 0.90) -> tuple[float, float, float]:  # noqa: E741 (frozen interface: O, E as in the spec)
    """(lo, O/E, hi): exact (Garwood) Poisson interval on O, divided by E, equal tails of
    (1 - level) / 2. O may be fractional (placement weights); O = 0 has lower bound 0."""
    if not (np.isfinite(O) and O >= 0):
        raise ValueError("O must be finite and >= 0")
    if not (np.isfinite(E) and E > 0):
        raise ValueError("E must be finite and > 0")
    if not 0.0 < level < 1.0:
        raise ValueError("level must lie in (0, 1)")
    a = (1.0 - level) / 2.0
    lo = 0.0 if O == 0 else float(gammaincinv(O, a))
    hi = float(gammaincinv(O + 1.0, 1.0 - a))
    return lo / E, O / E, hi / E


def spearman(a: Arr, b: Arr) -> float | None:
    return exposure_spearman([float(x) for x in a], [float(x) for x in b])
