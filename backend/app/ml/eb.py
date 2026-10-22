"""Stage 2 (P3:138 as amended by A:89-93, rework R1 2026-10-22): Poisson-Gamma empirical Bayes up the
MP area hierarchy, with each cell shrunk toward a precision-weighted blend of its geographic parent
and its feature archetype x type group. Held-out levels inherit their parent, so a region with no
training data still borrows from similar routes anywhere (D1).

Per level, kappa maximises the negative-binomial marginal likelihood (method of moments hit its
upper bound on 30-76% of sparse fits, i.e. "certain" priors that ignored local accidents). Each
group's prior then carries its parent's posterior variance too, by moment matching:
kappa_eff = 1/(1/kappa + v/m), so a borrowed number is only as tight as the evidence behind it."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from scipy.optimize import minimize_scalar
from scipy.special import gammaln, polygamma

from app.ml.cells import TYPE_GROUPS
from app.ml.dataset import CellData

MIN_PRIOR_SHAPE = 0.05
# kappa is in expected-incident units (prior rate). A group would need E ~ 1e4 expected incidents
# before 1e4 stopped meaning "pool fully"; no MP area, region or state comes close. Finite so the
# moment-matched kappa_eff and the posterior rate stay finite.
KAPPA_MAX = 1e4
KAPPA_MIN = 1e-6
KAPPA_GRID_POINTS = 241  # fallback search over log kappa when the bounded optimiser reports failure
log = logging.getLogger(__name__)
Level = dict[int, tuple[float, float, float, float]]
F64 = NDArray[np.float64]
I64 = NDArray[np.int64]


@dataclass(frozen=True)
class GammaPost:
    shape: F64
    rate: F64

    @property
    def mean(self) -> F64:
        return np.asarray(self.shape / self.rate, dtype=np.float64)


@dataclass(frozen=True)
class CellPost:
    post: GammaPost
    prior_mean: F64
    omega_cell: F64
    blend_geo: F64
    y_own: F64  # pre-flight F32: carried, never reconstructed from shape - kappa*pi
    chain: dict[str, F64] = field(default_factory=dict)


def nb_loglik(kappa: float, O: F64, E: F64, parent: F64) -> float:  # noqa: E741 (O is the interface name)
    """Negative-binomial marginal log-likelihood of O_i ~ Poisson(E_i theta_i), theta_i ~
    Gamma(kappa p_i, kappa), dropping the kappa-free log O! term. Groups need E > 0."""
    a = kappa * parent
    return float(np.sum(gammaln(O + a) - gammaln(a) + a * np.log(kappa / (kappa + E))
                        + O * np.log(E / (kappa + E))))


def ml_kappa(O: F64, E: F64, parent: F64) -> float:  # noqa: E741 (O is the interface name)
    """Maximum marginal-likelihood kappa over [KAPPA_MIN, KAPPA_MAX], searched on log kappa. A maximum at
    the upper bound means the data support homogeneity: the bound is returned, and parent variance
    (kappa_eff) is what keeps such a prior from reading as certainty."""
    ok = E > 0
    if ok.sum() < 2:
        return KAPPA_MAX
    obs, E, parent = O[ok], E[ok], parent[ok]
    if not np.all(parent > 0):
        raise ValueError("EB parent rates must be positive")
    if obs.sum() == 0:
        return KAPPA_MAX  # no events at this level: no heterogeneity signal to fit
    lo, hi = np.log(KAPPA_MIN), np.log(KAPPA_MAX)
    res = minimize_scalar(lambda t: -nb_loglik(float(np.exp(t)), obs, E, parent), bounds=(lo, hi),
                          method="bounded", options={"xatol": 1e-6})
    if res.success and np.isfinite(res.x):
        best = float(np.exp(res.x))
    else:
        grid = np.exp(np.linspace(lo, hi, KAPPA_GRID_POINTS))
        best = float(grid[int(np.argmax([nb_loglik(float(k), obs, E, parent) for k in grid]))])
        log.warning("ml_kappa: bounded optimiser failed (%s); grid argmax kappa=%.4g", res.message, best)
    # Bounded Brent never evaluates the endpoints; compare them so a boundary maximum is reported as one.
    for edge in (KAPPA_MAX, KAPPA_MIN):
        if nb_loglik(edge, obs, E, parent) >= nb_loglik(best, obs, E, parent):
            return edge
    return best


def blend_moments(sh_g: F64, rt_g: F64, sh_a: F64, rt_a: F64,
                  use_arch: NDArray[np.bool_]) -> tuple[F64, F64, F64]:
    """Cell parent as a moment-matched mixture of the geo and archetype Gammas: (mean, var, geo weight).
    Weights are 1/trigamma(shape), the precision of log theta, so they do not depend on the rate's
    scale; the inverse Gamma variance (rate^2/shape) used before gave a low mean weight ~ 1/m^2 and
    pulled the blend toward the safer-looking source. The mixture variance includes the spread
    between the two means, so disagreeing sources widen the prior instead of narrowing it."""
    pg = 1.0 / polygamma(1, sh_g)
    pa = np.where(use_arch, 1.0 / polygamma(1, np.where(use_arch, sh_a, 1.0)), 0.0)
    wg = pg / (pg + pa)
    wa = 1.0 - wg
    m_g, m_a = sh_g / rt_g, sh_a / rt_a
    v_g, v_a = m_g / rt_g, m_a / rt_a
    mean = wg * m_g + np.where(use_arch, wa * m_a, 0.0)
    second = wg * (v_g + m_g ** 2) + np.where(use_arch, wa * (v_a + m_a ** 2), 0.0)
    within = wg * v_g + np.where(use_arch, wa * v_a, 0.0)
    var = np.maximum(second - mean ** 2, within)  # guards cancellation; the mixture var is >= within
    return (np.asarray(mean, dtype=np.float64), np.asarray(var, dtype=np.float64),
            np.asarray(wg, dtype=np.float64))


def _floored_kappa(kappa: float, parent: F64, parent_var: F64) -> F64:
    """Per-group prior strength: kappa_eff = 1/(1/kappa + v/m) moment-matches Gamma(kappa m, kappa)
    mixed over the parent's posterior (mean m, variance v). Flooring kappa (not the shape) keeps the
    prior mean exactly at the parent while guaranteeing shape >= MIN_PRIOR_SHAPE."""
    if not np.all(parent > 0):
        raise ValueError("EB parent rates must be positive")
    eff = 1.0 / (1.0 / kappa + parent_var / parent)
    out: F64 = np.maximum(eff, MIN_PRIOR_SHAPE / parent)
    return out


def _check_counts(name: str, x: F64, n: int) -> None:
    if x.shape != (n,):
        raise ValueError(f"{name} has shape {x.shape}, expected ({n},)")
    if not np.all(np.isfinite(x)) or np.any(x < 0):
        raise ValueError(f"{name} must be finite and non-negative")


def _level(keys: I64, y: F64, mu0: F64, parent: F64, parent_var: F64) -> tuple[Level, float]:
    """Groups cells by `keys` (< 0 = no group); `parent` (mean) and `parent_var` are per cell and
    constant within a group.
    pre-flight F29: O(n) via unique + bincount; a per-key mask is O(K*n), hours at catalog scale."""
    ok = keys >= 0
    uniq, first, inv = np.unique(keys[ok], return_index=True, return_inverse=True)
    obs = np.bincount(inv, weights=y[ok], minlength=uniq.size).astype(np.float64)
    E = np.bincount(inv, weights=mu0[ok], minlength=uniq.size).astype(np.float64)
    P: F64 = parent[ok][first]
    V: F64 = parent_var[ok][first]
    if not (np.allclose(P[inv], parent[ok], rtol=1e-12, atol=0.0)
            and np.allclose(V[inv], parent_var[ok], rtol=1e-12, atol=0.0)):
        raise ValueError("parent rate varies within an EB group")
    kappa = ml_kappa(obs, E, P)
    kp = _floored_kappa(kappa, P, V)
    shape = obs + kp * P
    rate = E + kp
    return ({int(uniq[i]): (float(obs[i]), float(E[i]), float(shape[i]), float(rate[i]))
             for i in range(uniq.size)}, kappa)


@dataclass(frozen=True)
class _Table:
    keys: I64
    vals: F64  # (K, 4): O, E, shape, rate; keys sorted

    @classmethod
    def of(cls, level: Level) -> _Table:
        keys = np.fromiter(level.keys(), dtype=np.int64, count=len(level))
        vals = np.array(list(level.values()), dtype=np.float64).reshape(len(level), 4)
        order = np.argsort(keys)
        return cls(keys[order], vals[order])

    def lookup(self, q: I64) -> tuple[NDArray[np.bool_], F64]:
        """Rows of `vals` for each query key; missing keys (incl. negatives) read found=False, zeros."""
        if self.keys.size == 0:
            return np.zeros(q.size, dtype=np.bool_), np.zeros((q.size, 4))
        pos = np.clip(np.searchsorted(self.keys, q), 0, self.keys.size - 1)
        found = self.keys[pos] == q
        rows = np.where(found[:, None], self.vals[pos], 0.0)
        return found, rows


def _ratio(num: F64, den: F64) -> F64:
    return np.asarray(num / den, dtype=np.float64)


def _theta(table: _Table, q: I64, parent: F64, parent_var: F64,
           kappa: float) -> tuple[F64, F64, F64, F64]:
    """(shape, rate, O, E) per query; an absent key inherits its parent's prior: mean = parent,
    variance = parent/kappa + parent_var."""
    found, rows = table.lookup(q)
    kp = _floored_kappa(kappa, parent, parent_var)
    shape = np.where(found, rows[:, 2], kp * parent)
    rate = np.where(found, rows[:, 3], kp)
    return shape, rate, rows[:, 0], rows[:, 1]


def _arch_keys(cells: CellData, arch_of_area: I64) -> I64:
    # k-means labels -1 for "no comparable centre"; such cells get no archetype key, never index -1.
    k = arch_of_area[cells.area_idx]
    out: I64 = np.where(k >= 0, k * len(TYPE_GROUPS) + cells.g.astype(np.int64), -1).astype(np.int64)
    return out


@dataclass(frozen=True)
class EbFit:
    national: float
    national_var: float  # Gamma(sum y, sum mu0) variance of the national ratio
    state: Level
    region: Level
    area: Level
    arch: Level
    kappa_state: float
    kappa_region: float
    kappa_area: float
    kappa_arch: float
    kappa_cell: float
    blend: bool

    def prior(self, cells: CellData, arch_of_area: I64) -> tuple[F64, F64, F64, dict[str, F64]]:
        """Cell prior mean and variance (blend_moments), the geo blend weight and the per-level chain,
        all vectorized."""
        a = cells.area_idx.astype(np.int64)
        st, rg = cells.state_idx[a], cells.region_idx[a]
        nat, nat_v = np.full(a.size, self.national), np.full(a.size, self.national_var)
        s_sh, s_rt, s_O, s_E = _theta(_Table.of(self.state), st, nat, nat_v, self.kappa_state)
        r_sh, r_rt, r_O, r_E = _theta(_Table.of(self.region), rg, _ratio(s_sh, s_rt),
                                      _ratio(_ratio(s_sh, s_rt), s_rt), self.kappa_region)
        a_sh, a_rt, a_O, a_E = _theta(_Table.of(self.area), a, _ratio(r_sh, r_rt),
                                      _ratio(_ratio(r_sh, r_rt), r_rt), self.kappa_area)
        ak = _arch_keys(cells, arch_of_area)
        k_sh, k_rt, k_O, k_E = _theta(_Table.of(self.arch), ak, nat, nat_v, self.kappa_arch)
        use_arch = (ak >= 0) & self.blend
        prior, prior_var, bgeo = blend_moments(a_sh, a_rt, k_sh, k_rt, use_arch)
        chain = {
            "omega_area": a_E / a_rt, "O_area": a_O,
            "omega_region": r_E / r_rt, "O_region": r_O,
            "omega_state": s_E / s_rt, "O_state": s_O,
            "omega_arch": np.where(use_arch, k_E / k_rt, 0.0), "O_arch": np.where(use_arch, k_O, 0.0),
        }
        return prior, prior_var, bgeo, chain

    def cell_posterior(self, cells: CellData, y_own: F64, mu0: F64, arch_of_area: I64) -> CellPost:
        _check_counts("y_own", y_own, cells.n)
        _check_counts("mu0", mu0, cells.n)
        prior, prior_var, bgeo, chain = self.prior(cells, arch_of_area)
        kp = _floored_kappa(self.kappa_cell, prior, prior_var)
        shape = np.asarray(y_own + kp * prior, dtype=np.float64)
        rate = np.asarray(mu0 + kp, dtype=np.float64)
        return CellPost(GammaPost(shape, rate), prior, _ratio(mu0, rate), bgeo, y_own, chain)


def _moments_of(level: Level, keys: I64, fallback: F64, fallback_var: F64) -> tuple[F64, F64]:
    found, rows = _Table.of(level).lookup(keys)
    rate = np.where(found, rows[:, 3], 1.0)
    mean: F64 = np.where(found, rows[:, 2] / rate, fallback)
    var: F64 = np.where(found, rows[:, 2] / (rate * rate), fallback_var)
    return mean, var


def fit_eb(cells: CellData, y: F64, mu0: F64, arch_of_area: I64, *, blend: bool = True) -> EbFit:
    _check_counts("y", y, cells.n)
    _check_counts("mu0", mu0, cells.n)
    if not mu0.sum() > 0 or not y.sum() > 0:
        # A zero national ratio would make every prior 0, i.e. "safe" with no evidence; refuse.
        raise ValueError("fit_eb needs positive total exposure and at least one event")
    national = float(y.sum() / mu0.sum())
    national_var = float(y.sum() / mu0.sum() ** 2)
    a = cells.area_idx.astype(np.int64)
    st, rg = cells.state_idx[a], cells.region_idx[a]
    nat, nat_v = np.full(a.size, national), np.full(a.size, national_var)
    state, k_s = _level(st, y, mu0, nat, nat_v)
    st_m, st_v = _moments_of(state, st, nat, nat_v)
    region, k_r = _level(rg, y, mu0, st_m, st_v)
    area, k_a = _level(a, y, mu0, *_moments_of(region, rg, st_m, st_v))
    arch, k_k = _level(_arch_keys(cells, arch_of_area), y, mu0, nat, nat_v)
    partial = EbFit(national, national_var, state, region, area, arch, k_s, k_r, k_a, k_k, 1.0, blend)
    prior, _, _, _ = partial.prior(cells, arch_of_area)
    k_c = ml_kappa(y, mu0, prior)
    return EbFit(national, national_var, state, region, area, arch, k_s, k_r, k_a, k_k, k_c, blend)
