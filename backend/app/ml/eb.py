"""Stage 2 (P3:138 as amended by A:89-93): closed-form Poisson-Gamma empirical Bayes up the MP
area hierarchy, with each cell shrunk toward a precision-weighted blend of its geographic parent
and its feature archetype x type group. Held-out levels inherit their parent, so a region with no
training data still borrows from similar routes anywhere (D1)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.ml.cells import TYPE_GROUPS
from app.ml.dataset import CellData

MIN_PRIOR_SHAPE = 0.05
KAPPA_MAX = 1e6
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


def mom_kappa(O: F64, E: F64, parent: F64) -> float:
    ok = E > 0
    if ok.sum() < 2:
        return KAPPA_MAX
    O, E, parent = O[ok], E[ok], parent[ok]
    s2 = float(np.sum(E * (O / E - parent) ** 2) / E.sum())
    pbar = float(np.sum(E * parent) / E.sum())
    tau2 = s2 - pbar / float(E.mean())
    # pbar <= 0 only when every parent rate is 0 (no events anywhere); kappa = 0 there would give
    # a zero-rate posterior on E = 0 groups, so pool fully instead.
    if tau2 <= 0 or pbar <= 0:
        return KAPPA_MAX
    return min(pbar / tau2, KAPPA_MAX)


def _prior_shape(kappa: float, parent: F64) -> F64:
    out: F64 = np.maximum(kappa * parent, MIN_PRIOR_SHAPE)
    return out


def _level(keys: I64, y: F64, mu0: F64, parent: F64) -> tuple[Level, float]:
    """Groups cells by `keys` (< 0 = no group); `parent` is per cell and constant within a group.
    pre-flight F29: O(n) via unique + bincount; a per-key mask is O(K*n), hours at catalog scale."""
    ok = keys >= 0
    uniq, first, inv = np.unique(keys[ok], return_index=True, return_inverse=True)
    O = np.bincount(inv, weights=y[ok], minlength=uniq.size).astype(np.float64)
    E = np.bincount(inv, weights=mu0[ok], minlength=uniq.size).astype(np.float64)
    P: F64 = parent[ok][first]
    kappa = mom_kappa(O, E, P)
    shape = O + _prior_shape(kappa, P)
    rate = E + kappa
    return ({int(uniq[i]): (float(O[i]), float(E[i]), float(shape[i]), float(rate[i])) for i in range(uniq.size)},
            kappa)


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


def _theta(table: _Table, q: I64, parent: F64, kappa: float) -> tuple[F64, F64, F64, F64]:
    """(shape, rate, O, E) per query; an absent key inherits its parent's prior."""
    found, rows = table.lookup(q)
    shape = np.where(found, rows[:, 2], _prior_shape(kappa, parent))
    rate = np.where(found, rows[:, 3], kappa)
    return shape, rate, rows[:, 0], rows[:, 1]


def _arch_keys(cells: CellData, arch_of_area: I64) -> I64:
    # k-means labels -1 for "no comparable centre"; such cells get no archetype key, never index -1.
    k = arch_of_area[cells.area_idx]
    out: I64 = np.where(k >= 0, k * len(TYPE_GROUPS) + cells.g.astype(np.int64), -1).astype(np.int64)
    return out


@dataclass(frozen=True)
class EbFit:
    national: float
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

    def prior(self, cells: CellData, arch_of_area: I64) -> tuple[F64, F64, dict[str, F64]]:
        """Cell prior mean, geo blend weight and the per-level chain, all vectorized."""
        a = cells.area_idx.astype(np.int64)
        st, rg = cells.state_idx[a], cells.region_idx[a]
        nat = np.full(a.size, self.national)
        s_sh, s_rt, s_O, s_E = _theta(_Table.of(self.state), st, nat, self.kappa_state)
        r_sh, r_rt, r_O, r_E = _theta(_Table.of(self.region), rg, _ratio(s_sh, s_rt), self.kappa_region)
        a_sh, a_rt, a_O, a_E = _theta(_Table.of(self.area), a, _ratio(r_sh, r_rt), self.kappa_area)
        ak = _arch_keys(cells, arch_of_area)
        k_sh, k_rt, k_O, k_E = _theta(_Table.of(self.arch), ak, nat, self.kappa_arch)
        wg = a_rt * a_rt / a_sh
        use_arch = (ak >= 0) & self.blend
        wa = np.where(use_arch, k_rt * k_rt / k_sh, 0.0)
        prior = (wg * a_sh / a_rt + wa * k_sh / k_rt) / (wg + wa)
        chain = {
            "omega_area": a_E / (a_E + self.kappa_area), "O_area": a_O,
            "omega_region": r_E / (r_E + self.kappa_region), "O_region": r_O,
            "omega_state": s_E / (s_E + self.kappa_state), "O_state": s_O,
            "omega_arch": k_E / (k_E + self.kappa_arch), "O_arch": k_O,
        }
        return prior, wg / (wg + wa), chain

    def cell_posterior(self, cells: CellData, y_own: F64, mu0: F64, arch_of_area: I64) -> CellPost:
        prior, bgeo, chain = self.prior(cells, arch_of_area)
        shape = np.asarray(y_own + _prior_shape(self.kappa_cell, prior), dtype=np.float64)
        rate = np.asarray(mu0 + self.kappa_cell, dtype=np.float64)
        return CellPost(GammaPost(shape, rate), prior, mu0 / (mu0 + self.kappa_cell), bgeo, y_own, chain)


def _mean_of(level: Level, keys: I64, fallback: F64) -> F64:
    found, rows = _Table.of(level).lookup(keys)
    out: F64 = np.where(found, rows[:, 2] / np.where(found, rows[:, 3], 1.0), fallback)
    return out


def fit_eb(cells: CellData, y: F64, mu0: F64, arch_of_area: I64, *, blend: bool = True) -> EbFit:
    national = float(y.sum() / mu0.sum()) if mu0.sum() > 0 else 1.0
    a = cells.area_idx.astype(np.int64)
    st, rg = cells.state_idx[a], cells.region_idx[a]
    nat = np.full(a.size, national)
    state, k_s = _level(st, y, mu0, nat)
    st_mean = _mean_of(state, st, nat)
    region, k_r = _level(rg, y, mu0, st_mean)
    area, k_a = _level(a, y, mu0, _mean_of(region, rg, st_mean))
    arch, k_k = _level(_arch_keys(cells, arch_of_area), y, mu0, nat)
    partial = EbFit(national, state, region, area, arch, k_s, k_r, k_a, k_k, 1.0, blend)
    prior, _, _ = partial.prior(cells, arch_of_area)
    k_c = mom_kappa(y, mu0, prior)
    return EbFit(national, state, region, area, arch, k_s, k_r, k_a, k_k, k_c, blend)
