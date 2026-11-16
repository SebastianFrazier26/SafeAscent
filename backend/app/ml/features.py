"""Stage-1 design matrix (P3 M5 + A:54-61). Missing covariates enter as the training mean plus a
missing indicator (DP20). Rock-family ticks enter only once national rock tick coverage reaches
rock_coverage_min (DP5): rock routes near accidents were scraped first (X-conflict 5), so a
coverage or missingness term would encode the outcome. No coverage term ever enters."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.ml.cells import FAMILY_OF, TYPE_GROUPS
from app.ml.dataset import STATIC_FEATURES, CellData

LOG1P_FEATURES = frozenset({"precip_mm", "snowfall_cm"})
# A column whose spread is below this is constant up to float noise: dividing by that noise would turn
# rounding error into a huge standardized covariate, so it is left unscaled (sd = 1).
SD_FLOOR = 1e-8


def transform_static_columns(static: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
    """The single implementation of the static-feature transform, before standardizing: z_<name> (NaN
    where missing) plus the two missing indicators, for an (n, len(STATIC_FEATURES)) array."""
    if np.isinf(static).any():
        raise ValueError("static features must not be infinite")
    # z_lat has no indicator of its own: a missing lat is only safe to impute when
    # ind_elevation_missing already flags the row (a route without a feature row loses both).
    if (np.isnan(static[:, 1]) & ~np.isnan(static[:, 0])).any():
        raise ValueError("lat missing where elevation is present: no indicator would mark the imputed lat")
    out: dict[str, NDArray[np.float64]] = {}
    for j, f in enumerate(STATIC_FEATURES):
        v = static[:, j]
        if f in LOG1P_FEATURES:
            # log1p gives -inf at -1 and NaN below it, and a NaN here would be imputed as "missing"
            # downstream (training and explain at serve time alike): refuse the row instead.
            if (v <= -1.0).any():
                raise ValueError(f"{f} must be > -1 for log1p")
            v = np.log1p(v)
        out[f"z_{f}"] = v
    out["ind_elevation_missing"] = np.isnan(static[:, 0]).astype(np.float64)
    out["ind_normals_missing"] = np.asarray(np.isnan(static[:, 2:]).any(axis=1), dtype=np.float64)
    return out


def transform_static(raw: Mapping[str, float | None]) -> dict[str, float]:
    """One route's static features through transform_static_columns. Absent keys read as missing."""
    vals = [raw.get(f) for f in STATIC_FEATURES]
    row = np.array([[math.nan if v is None else float(v) for v in vals]])
    return {k: float(v[0]) for k, v in transform_static_columns(row).items()}


@dataclass(frozen=True)
class TickPolicy:
    rock_coverage_min: float = 0.90
    enabled: bool = True


@dataclass(frozen=True)
class Lambdas:
    type_main: float = 2.0
    type_month: float = 8.0
    covariate: float = 0.1


@dataclass(frozen=True)
class Design:
    X: NDArray[np.float64]
    names: tuple[str, ...]
    penalty: NDArray[np.float64]
    nonneg: tuple[int, ...]
    center: NDArray[np.float64]
    scale: NDArray[np.float64]
    exposure_cols: tuple[int, ...]
    tick_ref: float
    families_on: frozenset[str] = field(default_factory=frozenset)


def tick_families(cells: CellData, policy: TickPolicy) -> frozenset[str]:
    if not policy.enabled:
        return frozenset()
    rock = np.isin(cells.g, [TYPE_GROUPS.index(g) for g in ("sport", "trad", "rock")]) & (cells.month == 1)
    routes = cells.n_routes[rock].sum()
    covered = np.nansum(cells.tick_coverage[rock] * cells.n_routes[rock])
    on = {"mountain"}
    if routes > 0 and covered / routes >= policy.rock_coverage_min:
        on.add("rock_family")
    return frozenset(on)


def build_design(cells: CellData, *, policy: TickPolicy, lambdas: Lambdas = Lambdas(),
                 fit_stats: Design | None = None) -> Design:
    n = cells.n
    names: list[str] = ["intercept", "fam_mountain"]
    cols: list[NDArray[np.float64]] = [np.ones(n), np.array([FAMILY_OF[TYPE_GROUPS[g]] == "mountain" for g in cells.g],
                                                             dtype=np.float64)]
    pen: list[float] = [0.0, 0.0]
    for gi, g in enumerate(TYPE_GROUPS):
        names.append(f"a_{g}")
        cols.append((cells.g == gi).astype(np.float64))
        pen.append(lambdas.type_main)
    for m in range(2, 13):
        names.append(f"m_{m}")
        cols.append((cells.month == m).astype(np.float64))
        pen.append(0.0)
    for gi, g in enumerate(TYPE_GROUPS):
        for m in range(1, 13):
            names.append(f"d_{g}_{m}")
            cols.append(((cells.g == gi) & (cells.month == m)).astype(np.float64))
            pen.append(lambdas.type_month)
    families_on = fit_stats.families_on if fit_stats is not None else tick_families(cells, policy)
    fam = np.array([FAMILY_OF[TYPE_GROUPS[g]] for g in cells.g])
    tick_on = np.isin(fam, sorted(families_on)) & ~np.isnan(cells.ticks_per_route)
    tick_ref = fit_stats.tick_ref if fit_stats is not None else (
        float(cells.ticks_per_route[tick_on].mean()) if tick_on.any() and cells.ticks_per_route[tick_on].mean() > 0
        else 1.0)
    season_missing = np.isnan(cells.season_share)
    raw: dict[str, NDArray[np.float64]] = {
        "x_density": np.log1p(cells.n_routes),
        "x_season": np.where(season_missing, np.nan, np.log1p(30.0 * np.nan_to_num(cells.season_share, nan=0.0))),
        "ind_season_missing": season_missing.astype(np.float64),
        "x_ticks": np.where(tick_on, np.log1p(np.nan_to_num(cells.ticks_per_route, nan=0.0) / tick_ref), np.nan),
        "ind_ticks_off": (~tick_on).astype(np.float64),
    }
    raw.update(transform_static_columns(cells.static))
    order = ["x_density", "x_season", "ind_season_missing", "x_ticks", "ind_ticks_off", "z_elevation_m",
             "ind_elevation_missing", "z_lat", "z_tmax_mean", "z_tmin_mean", "z_precip_mm", "z_snowfall_cm",
             "z_freeze_thaw_days", "ind_normals_missing"]
    k0 = len(names)
    center = np.zeros(k0 + len(order))
    scale = np.ones(k0 + len(order))
    for k, name in enumerate(order, start=k0):
        v = raw[name]
        if name.startswith(("x_", "z_")):
            if fit_stats is not None:
                mu, sd = fit_stats.center[k], fit_stats.scale[k]
            else:
                ok = ~np.isnan(v)
                mu = float(v[ok].mean()) if ok.any() else 0.0
                sd = float(v[ok].std()) if ok.any() and v[ok].std() > SD_FLOOR else 1.0
            center[k], scale[k] = mu, sd
            # A missing value is the training mean (standardized 0); its indicator carries the gap.
            v = np.where(np.isnan(v), 0.0, (v - mu) / sd)
        names.append(name)
        cols.append(v)
        pen.append(lambdas.covariate)
    X = np.column_stack(cols)
    if not np.isfinite(X).all():
        bad = sorted({names[j] for j in np.nonzero(~np.isfinite(X).all(axis=0))[0]})
        raise ValueError(f"design matrix has non-finite values in {bad}")
    idx = {nm: i for i, nm in enumerate(names)}
    return Design(X=X, names=tuple(names), penalty=np.array(pen), nonneg=(idx["x_density"],),
                  center=center, scale=scale,
                  exposure_cols=tuple(idx[c] for c in ("x_density", "x_season", "ind_season_missing", "x_ticks",
                                                       "ind_ticks_off")),
                  tick_ref=tick_ref, families_on=families_on)
