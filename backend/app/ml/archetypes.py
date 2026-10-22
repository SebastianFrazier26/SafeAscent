"""Feature-space similarity (A:71-79, D1): areas are clustered into archetypes so an Alaska
alpine crag and a Rocky Mountain alpine crag can share evidence, and each query's distance to
the nearest incident-bearing area defines "feature evidence exists" (d*, A:150). Missing features
are skipped in the distance, never filled."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.ml.dataset import STATIC_FEATURES, CellData

ARCH_FEATURES = ("elevation_m", "lat", "tmax_jul", "tmin_jan", "precip_year", "snow_year", "freeze_thaw_year")
MIN_SHARED_FEATURES = 3


def _first_present(x: NDArray[np.float64]) -> NDArray[np.float64]:
    # elevation and lat repeat on every month row; a missing January row must not erase them
    present = ~np.isnan(x)
    first = np.argmax(present, axis=1)
    out = x[np.arange(x.shape[0]), first]
    return np.where(present.any(axis=1), out, np.nan)


def area_feature_matrix(cells: CellData) -> NDArray[np.float64]:
    s = cells.area_static
    j = {f: i for i, f in enumerate(STATIC_FEATURES)}
    return np.column_stack([
        _first_present(s[:, :, j["elevation_m"]]), _first_present(s[:, :, j["lat"]]),
        s[:, 6, j["tmax_mean"]], s[:, 0, j["tmin_mean"]],
        np.log1p(s[:, :, j["precip_mm"]].sum(axis=1)), np.log1p(s[:, :, j["snowfall_cm"]].sum(axis=1)),
        s[:, :, j["freeze_thaw_days"]].sum(axis=1),
    ])  # a NaN month makes the annual sum NaN: missing, never a partial year read as a full one


def _nan_moments(F: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    # hand-rolled so an all-NaN column yields NaN without numpy's empty-slice RuntimeWarning
    n = (~np.isnan(F)).sum(axis=0)
    safe = np.maximum(n, 1)
    mean = np.where(n > 0, np.nansum(F, axis=0) / safe, np.nan)
    var = np.where(n > 0, np.nansum((F - mean) ** 2, axis=0) / safe, np.nan)
    return mean, np.sqrt(var)


@dataclass(frozen=True)
class Standardizer:
    mean: NDArray[np.float64]
    sd: NDArray[np.float64]

    @classmethod
    def fit(cls, F: NDArray[np.float64]) -> Standardizer:
        mean, sd = _nan_moments(F)
        return cls(np.nan_to_num(mean), np.where((sd > 0) & np.isfinite(sd), sd, 1.0))

    def apply(self, F: NDArray[np.float64]) -> NDArray[np.float64]:
        return np.asarray((F - self.mean) / self.sd, dtype=np.float64)


def weighted_sqdist(A: NDArray[np.float64], B: NDArray[np.float64], w: NDArray[np.float64], *,
                    min_shared: int = MIN_SHARED_FEATURES) -> NDArray[np.float64]:
    # min_shared is capped at the feature count so a low-dimensional space can still be compared
    need = min(min_shared, A.shape[1])
    total = float(w.sum())
    num = np.zeros((A.shape[0], B.shape[0]))
    wsum = np.zeros_like(num)
    nshared = np.zeros(num.shape, dtype=np.int64)
    for f in range(A.shape[1]):
        a, b = A[:, f][:, None], B[:, f][None, :]
        both = ~np.isnan(a) & ~np.isnan(b)
        num += np.where(both, w[f] * (np.nan_to_num(a) - np.nan_to_num(b)) ** 2, 0.0)
        wsum += np.where(both, w[f], 0.0)
        nshared += both
    ok = (wsum > 0) & (nshared >= need)
    return np.where(ok, num * total / np.where(ok, wsum, 1.0), np.nan)


def _nearest(D: NDArray[np.float64]) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
    Dinf = np.where(np.isnan(D), np.inf, D)
    idx = np.argmin(Dinf, axis=1).astype(np.int64)
    return idx, Dinf[np.arange(D.shape[0]), idx]


def kmeans(Z: NDArray[np.float64], k: int, w: NDArray[np.float64], *, seed: int = 0,
           iters: int = 50) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
    """A row with no comparable center (too few shared features) is labelled -1, as an all-NaN row is."""
    rng = np.random.default_rng(seed)
    usable = np.flatnonzero(~np.isnan(Z).all(axis=1))
    labels = np.full(Z.shape[0], -1, dtype=np.int64)
    k = min(k, usable.size)
    if k == 0:
        return labels, np.empty((0, Z.shape[1]))
    Zu = Z[usable]
    centers = [Zu[rng.integers(usable.size)]]
    for _ in range(1, k):
        _, d = _nearest(weighted_sqdist(Zu, np.array(centers), w))
        d = np.where(np.isfinite(d), d, 0.0)
        p = d / d.sum() if d.sum() > 0 else np.full(d.size, 1.0 / d.size)
        centers.append(Zu[rng.choice(usable.size, p=p)])
    C = np.array(centers)
    prev: NDArray[np.int64] = np.full(usable.size, -2, dtype=np.int64)
    for _ in range(iters):
        new, d = _nearest(weighted_sqdist(Zu, C, w))
        new = np.where(np.isfinite(d), new, -1)
        if np.array_equal(new, prev):
            break
        prev = new
        for c in range(k):
            members = Zu[new == c]
            if members.shape[0]:
                mean, _ = _nan_moments(members)
                C[c] = np.where(np.isnan(mean), C[c], mean)
    labels[usable] = prev
    return labels, C


def nearest_distance(Zq: NDArray[np.float64], Zev: NDArray[np.float64], w: NDArray[np.float64], *,
                     chunk: int = 4096) -> NDArray[np.float64]:
    out = np.full(Zq.shape[0], np.inf)
    if Zev.shape[0] == 0:
        return out
    for s in range(0, Zq.shape[0], chunk):
        _, d = _nearest(weighted_sqdist(Zq[s:s + chunk], Zev, w))
        out[s:s + chunk] = np.sqrt(d)
    return out
