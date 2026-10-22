"""Synthetic data for model tests, no real ids (every id is at least 900000000): CellData built through
assemble_cells so fixtures share the production cell layout (synthetic_cells, fitted_toy,
synthetic_problem), RawData for backtests (synthetic_raw), selector and gate inputs (fold_results_toy,
challenger_metrics, rows_for_diff), and seed_training_db, which writes a synthetic_raw world into a
migrated database for the train CLI test. The synthetic_cells signature is fixed (pre-flight pair
8 <-> 20), and _world's rng call order is part of synthetic_problem's contract."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from app.ml.cells import FAMILY_OF, TYPE_GROUPS
from app.ml.dataset import CellData, ExposureRow, StaticRow, assemble_cells
from app.ml.eb import CellPost, fit_eb
from app.ml.features import Design, TickPolicy, build_design
from app.ml.glm import ETA_CLIP, linear_predictor
from app.ml.incidents import AreaInfo

if TYPE_CHECKING:  # posterior imports scipy (train group); the fixture itself must not need it
    from app.ml.posterior import Thresholds

SYNTHETIC_ID_FLOOR = 900_000_000
BACKGROUND = (0.0, 0.0, 6.0, 0.0, 3.0, 0.0)
STATES = {"CO": (39.0, -105.5), "UT": (38.5, -111.0), "WA": (47.5, -121.0), "NH": (44.0, -71.5)}


def synthetic_cells(seed: int, n_areas: int, rock_tick_coverage: float = 1.0) -> CellData:
    """Areas across CO/UT/WA/NH, one to three type groups each, 12 months per group. Rock-family
    cells carry tick_coverage = rock_tick_coverage, and that share of rock (area, group) series has
    ticks; the rest reads NaN, as an unscraped area does."""
    rng = np.random.default_rng(seed)
    state_codes = tuple(STATES)
    exposure: list[ExposureRow] = []
    static: list[StaticRow] = []
    areas: list[AreaInfo] = []
    paths: dict[int, str] = {}
    for k in range(n_areas):
        st = state_codes[k % len(state_codes)]
        area_id = SYNTHETIC_ID_FLOOR + 1000 + k
        parent = SYNTHETIC_ID_FLOOR + 100 * (1 + state_codes.index(st))
        paths[area_id] = f"{parent}.{area_id}"
        lat0, lon0 = STATES[st]
        lat, lon = lat0 + float(rng.normal(0, 0.5)), lon0 + float(rng.normal(0, 0.5))
        groups = rng.choice(len(TYPE_GROUPS), size=int(rng.integers(1, 4)), replace=False)
        by_group: dict[str, int] = {}
        for gi in sorted(int(x) for x in groups):
            g = TYPE_GROUPS[gi]
            n_routes = int(rng.integers(1, 40))
            by_group[g] = n_routes
            rock = FAMILY_OF[g] == "rock_family"
            covered = not rock or rng.random() < rock_tick_coverage
            coverage = rock_tick_coverage if rock else 1.0
            peak = int(rng.integers(1, 13))
            raw = np.exp(-0.5 * ((np.arange(1, 13) - peak) / 2.0) ** 2)
            share = raw / raw.sum()
            per_route = float(rng.lognormal(1.0, 0.8))
            for m in range(1, 13):
                tpr = per_route * float(share[m - 1]) if covered else None
                exposure.append(ExposureRow(area_id, g, m, n_routes, float(share[m - 1]),
                                            None if tpr is None else tpr * n_routes, tpr, coverage))
        areas.append(AreaInfo(area_id, st, lat, lon, by_group))
        elevation = float(rng.normal(2000.0, 800.0))
        for m in range(1, 13):
            tmax = 25.0 - 0.006 * elevation - 12.0 * np.cos(2 * np.pi * (m - 7) / 12) + float(rng.normal(0, 1))
            static.append(StaticRow(area_id, m, (
                elevation, lat, tmax, tmax - 12.0, float(rng.gamma(2.0, 30.0)),
                float(max(0.0, rng.normal(30.0 - tmax, 10.0))), float(rng.integers(0, 20)),
            )))
    return assemble_cells(exposure, static, areas, paths)


@dataclass(frozen=True)
class FittedToy:
    cells: CellData
    design: Design
    coef: NDArray[np.float64]
    cp: CellPost
    y: NDArray[np.float64]
    years: float
    geo_evidence: NDArray[np.bool_]
    target_cell: int

    def inputs(self, thresholds: Thresholds, evidence_far: bool = False) -> dict[str, Any]:
        """Keyword arguments for posterior.summarize. Every cell has a feature neighbour at distance 0
        except the target with evidence_far, which sits beyond any d_star."""
        dist = np.zeros(self.cells.n)
        if evidence_far:
            dist[self.target_cell] = 1e3
        return {"cells": self.cells, "design": self.design, "coef": self.coef, "cp": self.cp,
                "thresholds": thresholds, "evidence_dist": dist, "years": self.years,
                "geo_evidence": self.geo_evidence}


def fitted_toy(*, local_incidents: float, archetype_incidents: int, density: int = 10,
               tick_coverage: float | None = 1.0, ticks_value: float | None = 120.0,
               n_mates: int = 6) -> FittedToy:
    """One target trad area alone in NH (archetype 0), n_mates CO areas sharing its archetype, and
    n_mates UT background areas (archetype 1) with BACKGROUND incidents in January and July. The target
    July cell holds local_incidents; archetype_incidents spread one each over the CO July cells.
    coef is fixed: a base rate plus a positive route-density effect, nothing else."""
    if archetype_incidents > n_mates:
        raise ValueError("at most one archetype incident per mate area")
    target = SYNTHETIC_ID_FLOOR + 1000
    layout = ([(target, "NH", density, 0)]
              + [(target + 1 + k, "CO", 10, 0) for k in range(n_mates)]
              + [(target + 1 + n_mates + k, "UT", 10, 1) for k in range(n_mates)])
    exposure: list[ExposureRow] = []
    static: list[StaticRow] = []
    areas: list[AreaInfo] = []
    paths: dict[int, str] = {}
    for area_id, st, n_routes, _ in layout:
        root = SYNTHETIC_ID_FLOOR + 100 * (1 + tuple(STATES).index(st))
        paths[area_id] = f"{root}.{root + 1}.{area_id}"  # region = first two labels: one region per state
        lat, lon = STATES[st]
        areas.append(AreaInfo(area_id, st, lat, lon, {"trad": n_routes}))
        tv, cov = (ticks_value, tick_coverage) if area_id == target else (120.0, 1.0)
        for m in range(1, 13):
            exposure.append(ExposureRow(area_id, "trad", m, n_routes, 1 / 12,
                                        None if tv is None else tv / 12,
                                        None if tv is None else tv / 12 / n_routes, cov))
            static.append(StaticRow(area_id, m, (2000.0, lat, 15.0, 3.0, 50.0, 5.0, 4.0)))
    cells = assemble_cells(exposure, static, areas, paths)
    design = build_design(cells, policy=TickPolicy())
    coef = np.zeros(design.X.shape[1])
    coef[0] = np.log(0.05)
    coef[design.names.index("x_density")] = 0.5
    mu0 = np.exp(np.clip(linear_predictor(design.X, coef), -ETA_CLIP, ETA_CLIP))
    gi = TYPE_GROUPS.index("trad")
    y = np.zeros(cells.n)
    target_cell = cells.index[(target, gi, 7)]
    y[target_cell] = local_incidents
    for k in range(archetype_incidents):
        y[cells.index[(target + 1 + k, gi, 7)]] = 1.0
    # Clustered, not even, so the levels see extra-Poisson variance.
    for k in range(n_mates):
        for m in (1, 7):
            y[cells.index[(target + 1 + n_mates + k, gi, m)]] = BACKGROUND[k % len(BACKGROUND)]
    arch = np.array([a for _, _, _, a in sorted(layout)], dtype=np.int64)
    fit = fit_eb(cells, y, mu0, arch)
    cp = fit.cell_posterior(cells, y, mu0, arch)
    a = cells.area_idx
    has = [np.bincount(keys, weights=y, minlength=int(keys.max()) + 1) > 0
           for keys in (a, cells.region_idx[a], cells.state_idx[a])]
    geo = has[0][a] | has[1][cells.region_idx[a]] | has[2][cells.state_idx[a]]
    return FittedToy(cells, design, coef, cp, y, 10.0, np.asarray(geo, dtype=np.bool_), target_cell)


@dataclass(frozen=True)
class ReferenceTerms:
    log_hazard_over_rref: float
    log_theta_over_mean: float


_TOY_RAW: dict[str, float | None] = {"elevation_m": 2500.0, "lat": 44.0, "tmax_mean": 15.0, "tmin_mean": 3.0,
                                     "precip_mm": 50.0, "snowfall_cm": 5.0, "freeze_thaw_days": 4.0}
_TOY_Z = {"z_elevation_m": 0.8, "z_lat": -0.3, "z_tmax_mean": 1.1, "z_tmin_mean": -0.6, "z_precip_mm": 0.4,
          "z_snowfall_cm": -1.3, "z_freeze_thaw_days": 0.9}
_TOY_COEF = {"z_elevation_m": 0.15, "z_lat": -0.05, "z_tmax_mean": 0.1, "z_tmin_mean": 0.07, "z_precip_mm": -0.2,
             "z_snowfall_cm": 0.12, "z_freeze_thaw_days": 0.25, "ind_elevation_missing": 0.3,
             "ind_normals_missing": 0.2, "a_trad": 0.1, "m_7": 0.05}


def toy_bundle_and_row() -> tuple[Any, dict[str, Any]]:
    """A ModelBundle and an explain() row for the fitted toy's target July trad cell. The toy has no
    static variation, so the target's z values are overridden and center/scale chosen to reproduce them
    from _TOY_RAW; coef gets nonzero covariate and season effects. log_rref per (g, m) is read back
    from summarize (log of theta * hazard / rr), not recomputed."""
    from app.ml.bundle import ModelBundle
    from app.ml.features import transform_static
    from app.ml.posterior import Thresholds, summarize

    toy = fitted_toy(local_incidents=2, archetype_incidents=3)
    d = toy.design
    coef = toy.coef.copy()
    center, scale = d.center.copy(), d.scale.copy()
    X = d.X.copy()
    tr = transform_static(_TOY_RAW)
    for name, z in _TOY_Z.items():
        k = d.names.index(name)
        X[toy.target_cell, k] = z
        scale[k] = 2.5 + 0.5 * (k % 3)
        center[k] = tr[name] - z * scale[k]
    for name, c in _TOY_COEF.items():
        coef[d.names.index(name)] = c
    design = dataclasses.replace(d, X=X, center=center, scale=scale)
    th = Thresholds(d_star=1.0, conf_high=2.0, conf_medium=4.0, phi=1.0)
    inputs = {**toy.inputs(th), "design": design, "coef": coef}
    s = summarize(**inputs)
    _, mean, _ = _gamma(toy, th)
    ex = list(design.exposure_cols)
    hazard = np.exp(linear_predictor(X, coef) - linear_predictor(X[:, ex], coef[ex]))
    cells = toy.cells
    log_rref = np.zeros((len(TYPE_GROUPS), 12))
    for gi in range(len(TYPE_GROUPS)):
        for m in range(1, 13):
            sel = np.flatnonzero((cells.g == gi) & (cells.month == m) & ~s.insufficient)
            if sel.size:
                c = sel[0]
                log_rref[gi, m - 1] = float(np.log(mean[c] * hazard[c] / s.rr_mean[c]))
    const = {g: {str(m): float(-log_rref[gi, m - 1]) for m in range(1, 13)} for gi, g in enumerate(TYPE_GROUPS)}
    bundle = ModelBundle("toy", "rock_family",
                         {"coef": coef, "center": center, "scale": scale, "log_rref": log_rref,
                          "tick_ref": np.array(d.tick_ref)},
                         {"names": list(d.names), "const": const, "families_on": sorted(d.families_on)})
    c = toy.target_cell
    row = {"type_group": "trad", "month": 7, "raw_features": dict(_TOY_RAW), "rr_mean": float(s.rr_mean[c]),
           "n_eff_geo": float(s.n_eff_geo[c]), "n_eff_feature": float(s.n_eff_feature[c])}
    return bundle, row


def _gamma(toy: FittedToy, th: Thresholds) -> tuple[Any, Any, Any]:
    from app.ml.posterior import interval90

    return interval90(toy.cp.post.shape, toy.cp.post.rate, th.phi)


def bundle_reference_terms(bundle: Any, row: dict[str, Any]) -> ReferenceTerms:
    """log(hazard / r_ref) and log(posterior mean) for the row's cell, from the toy's own arrays by the
    A:112-131 formulas, without going through explain or the bundle's stored constants."""
    from app.ml.posterior import Thresholds

    toy = fitted_toy(local_incidents=2, archetype_incidents=3)
    th = Thresholds(d_star=1.0, conf_high=2.0, conf_medium=4.0, phi=1.0)
    d = toy.design
    coef = bundle.arrays["coef"]
    ex = list(d.exposure_cols)
    gi = TYPE_GROUPS.index(row["type_group"])
    c = toy.target_cell
    # The bundle's center/scale were chosen so the target row's z values equal _TOY_Z.
    X = d.X.copy()
    for name, z in _TOY_Z.items():
        X[c, d.names.index(name)] = z
    eta = linear_predictor(X, coef)
    eta_exp = linear_predictor(X[:, ex], coef[ex])
    _, mean, _ = _gamma(toy, th)
    mu0 = np.exp(np.clip(eta, -ETA_CLIP, ETA_CLIP))
    sel = (toy.cells.g == gi) & (toy.cells.month == row["month"])
    r_ref = float((mu0[sel] * mean[sel]).sum() / np.exp(eta_exp[sel]).sum())
    return ReferenceTerms(float(eta[c] - eta_exp[c] - np.log(r_ref)), float(np.log(mean[c])))


# Archetype prototypes: (elevation_m, monthly precip_mm, monthly snowfall_cm, freeze-thaw days). The true
# risk effect is per prototype x type group, so a GLM additive in features and type cannot absorb it and
# only feature-space pooling (archetype x group) can carry it into a held-out state.
_PROTOTYPES = ((600.0, 90.0, 0.0, 1.0), (1700.0, 45.0, 8.0, 6.0), (2900.0, 25.0, 40.0, 14.0),
               (3900.0, 15.0, 90.0, 22.0))


@dataclass(frozen=True)
class _World:
    exposure: list[ExposureRow]
    static: list[StaticRow]
    areas: list[AreaInfo]
    paths: dict[int, str]
    log_rate: dict[tuple[int, int, int], float]


def _world(rng: np.random.Generator, n_areas: int, states: dict[str, tuple[float, float]],
           id_base: int) -> _World:
    """The synthetic_problem generator (Task 15): route density, season share, a type-group effect, a
    prototype x group effect shared across states, and per-area noise. log_rate is the expected count
    over 20 years. The rng call order is part of the contract: synthetic_problem's seeds depend on it."""
    effect = rng.normal(0.0, 0.9, size=(len(_PROTOTYPES), len(TYPE_GROUPS)))
    type_effect = rng.normal(0.0, 0.3, size=len(TYPE_GROUPS))
    state_codes = tuple(states)
    exposure: list[ExposureRow] = []
    static: list[StaticRow] = []
    areas: list[AreaInfo] = []
    paths: dict[int, str] = {}
    log_rate: dict[tuple[int, int, int], float] = {}
    for k in range(n_areas):
        st = state_codes[k % len(state_codes)]
        area_id = id_base + k
        root = SYNTHETIC_ID_FLOOR + 100 * (1 + state_codes.index(st))
        paths[area_id] = f"{root}.{root + 1 + (k // len(state_codes)) % 3}.{area_id}"
        lat0, lon0 = states[st]
        lat = lat0 + float(rng.normal(0, 0.5))
        p = int(rng.integers(len(_PROTOTYPES)))
        elev0, precip0, snow0, ft0 = _PROTOTYPES[p]
        elevation = elev0 + float(rng.normal(0, 150.0))
        noise = float(rng.normal(0, 0.2))
        groups = sorted(int(x) for x in rng.choice(len(TYPE_GROUPS), size=int(rng.integers(1, 4)), replace=False))
        by_group: dict[str, int] = {}
        for gi in groups:
            n_routes = int(rng.integers(3, 40))
            by_group[TYPE_GROUPS[gi]] = n_routes
            raw = 1.0 + 0.5 * np.cos(2 * np.pi * (np.arange(1, 13) - int(rng.integers(1, 13))) / 12)
            share = raw / raw.sum()
            for m in range(1, 13):
                exposure.append(ExposureRow(area_id, TYPE_GROUPS[gi], m, n_routes, float(share[m - 1]),
                                            None, None, None))
                log_rate[(area_id, gi, m)] = (-1.8 + 0.7 * np.log(n_routes) + np.log(12 * share[m - 1])
                                              + type_effect[gi] + effect[p, gi] + noise)
        areas.append(AreaInfo(area_id, st, lat, lon0, by_group))
        for m in range(1, 13):
            tmax = 25.0 - 0.006 * elevation - 12.0 * np.cos(2 * np.pi * (m - 7) / 12)
            static.append(StaticRow(area_id, m, (
                elevation, lat, tmax, tmax - 12.0, precip0 * float(rng.uniform(0.8, 1.2)),
                snow0 * float(rng.uniform(0.8, 1.2)), ft0 + float(rng.integers(0, 3)),
            )))
    return _World(exposure, static, areas, paths, log_rate)


def synthetic_problem(seed: int, return_truth: bool = False, n_areas: int = 96,
                      state_mass: dict[str, float] | None = None) -> Any:
    """Cells, Poisson counts and (with return_truth) the true per-cell rates, from a log-linear model:
    route density, season share, a type-group effect, a prototype x group effect shared across
    states, and per-area noise. Every month and both families carry incidents, so the GLM converges.
    state_mass maps a state code to state-only incident mass, spread over the groups present there."""
    from app.ml.dataset import Counts

    rng = np.random.default_rng(seed)
    w = _world(rng, n_areas, STATES, SYNTHETIC_ID_FLOOR + 1000)
    cells = assemble_cells(w.exposure, w.static, w.areas, w.paths)
    truth = np.empty(cells.n)
    for key, i in cells.index.items():
        truth[i] = np.exp(w.log_rate[key])
    y = rng.poisson(truth).astype(np.float64)
    sm = np.zeros((len(cells.states), len(TYPE_GROUPS)))
    for code, mass in (state_mass or {}).items():
        si = cells.states.index(code)
        present = np.unique(cells.g[cells.state_idx[cells.area_idx] == si]).astype(np.int64)
        sm[si, present] = mass / present.size
    counts = Counts(y=y, state_mass=sm, years=20.0, dropped={},
                    placed={"cell": float(y.sum()), "state": float(sm.sum())})
    return (cells, counts, truth) if return_truth else (cells, counts)


def permute_problem(cells: CellData, counts: Any, perm: NDArray[np.int64]) -> tuple[CellData, Any]:
    """Cells and counts with every per-cell array (and cells.index) reordered by perm."""
    perm = np.asarray(perm, dtype=np.int64)
    if np.sort(perm).tolist() != list(range(cells.n)):
        raise ValueError("perm must be a permutation of the cells")
    return cells.take(perm), dataclasses.replace(counts, y=counts.y[perm])


# Two states per state group (Colorado's group holds only CO), so every spatial fold holds out real areas.
RAW_STATES = {"WA": (47.5, -121.0), "OR": (44.0, -121.5), "CA": (37.5, -119.0), "NV": (39.0, -116.5),
              "AZ": (34.5, -111.5), "UT": (38.5, -111.0), "MT": (46.5, -110.0), "WY": (43.0, -107.5),
              "CO": (39.0, -105.5), "TX": (31.0, -99.0), "MN": (46.0, -94.0), "KY": (37.5, -85.0),
              "NC": (35.5, -80.0), "NH": (44.0, -71.5), "NY": (43.0, -75.0)}
RAW_START = date(1990, 1, 1)
RAW_END = date(2026, 10, 1)


def synthetic_raw(seed: int, drop_cutoffs: tuple[date, ...] = (), n_areas: int = 120, rate_scale: float = 1.0) -> Any:
    """RawData for backtests: the synthetic_problem world over RAW_STATES, with incidents drawn as
    placements over [1990-01-01, 2026-10-01) at the world's true rate (log_rate is a 20-year count).
    Precision mix: day (most), month, and 1-3 year intervals with no month; about 5% carry ski_approach
    and 8% llm_link; a few state-only and dropped rows. Every rock-and-mountain cell has full tick
    coverage. Exposure is the same snapshot at every BACKTEST_CUTOFFS value and at the production
    cutoff RAW_END, except drop_cutoffs."""
    from app.ml.backtest import RawData
    from app.ml.folds import BACKTEST_CUTOFFS
    from app.ml.incidents import Placement

    rng = np.random.default_rng(seed)
    w = _world(rng, n_areas, RAW_STATES, SYNTHETIC_ID_FLOOR + 10_000)
    state_of = {a.area_id: a.state_code for a in w.areas}
    tick_rng = np.random.default_rng([seed, 1])
    per_route = {(a.area_id, g): float(tick_rng.lognormal(1.0, 0.5)) for a in w.areas for g in a.routes_by_group}
    exposure = [dataclasses.replace(e, ticks_per_route=per_route[(e.area_id, e.type_group)] * e.season_share,
                                    ticks_value=per_route[(e.area_id, e.type_group)] * e.season_share * e.n_routes,
                                    tick_coverage=1.0)
                for e in w.exposure if e.season_share is not None]
    placements: list[Placement] = []
    next_id = SYNTHETIC_ID_FLOOR + 1_000_000

    def flags() -> frozenset[str]:
        u = rng.random()
        return frozenset({"ski_approach"} if u < 0.05 else {"llm_link"} if u < 0.13 else set())

    for (area_id, gi, m), lr in sorted(w.log_rate.items()):
        years = [y for y in range(RAW_START.year, RAW_END.year + 1) if date(y, m, 1) < RAW_END]
        n = int(rng.poisson(rate_scale * np.exp(lr) / 20.0 * len(years)))
        for _ in range(n):
            y = years[int(rng.integers(len(years)))]
            kind = rng.random()
            month: int | None = m
            if kind < 0.75:
                lo = date(y, m, int(rng.integers(1, 29)))
                hi = lo + timedelta(days=1)
            elif kind < 0.85:
                lo = date(y, m, 1)
                hi = date(y + (m == 12), m % 12 + 1, 1)
            else:
                span = int(rng.integers(1, 4))
                lo, hi, month = date(y, 1, 1), date(min(y + span, RAW_END.year), 1, 1), None
                if hi <= lo:
                    lo, hi = date(y - 1, 1, 1), date(y, 1, 1)
            placements.append(Placement(next_id, "cell", "route_link", area_id, state_of[area_id],
                                        ((TYPE_GROUPS[gi], 1.0),), month, lo, hi, 1.0, flags()))
            next_id += 1
    for st in RAW_STATES:
        for _ in range(int(rng.poisson(3.0))):
            lo = date(int(rng.integers(RAW_START.year, RAW_END.year)), int(rng.integers(1, 13)), 10)
            placements.append(Placement(next_id, "state", "beyond_3km", None, st, (), lo.month, lo,
                                        lo + timedelta(days=1), 1.0, frozenset()))
            next_id += 1
    for _ in range(5):
        placements.append(Placement(next_id, "dropped", "no_date", None, None, (), None, None, None, 1.0, frozenset()))
        next_id += 1
    cutoffs = [c for c in (*BACKTEST_CUTOFFS, RAW_END) if c not in drop_cutoffs]
    return RawData(placements=placements, areas=list(w.areas), paths=dict(w.paths), static=list(w.static),
                   exposure_by_cutoff={c: list(exposure) for c in cutoffs}, production_cutoff=RAW_END)


def synthetic_fold_data(seed: int, n_areas: int = 120, rate_scale: float = 1.0) -> Any:
    """prepare() over synthetic_raw's spatial folds (production cutoff) and rolling folds."""
    from app.ml.backtest import prepare
    from app.ml.folds import rolling_folds, spatial_folds

    raw = synthetic_raw(seed, n_areas=n_areas, rate_scale=rate_scale)
    return [prepare(raw, f) for f in spatial_folds(raw.production_cutoff) + rolling_folds()]


def toy_fold_result(shape: Any, rate: Any, scale: Any, obs: Any, dist: Any) -> Any:
    from app.ml.backtest import FoldResult
    from app.ml.folds import Fold

    n = obs.size

    def f64(v: float) -> NDArray[np.float64]:
        return np.full(n, v, dtype=np.float64)

    return FoldResult(
        fold=Fold("toy", "spatial", RAW_START, RAW_END, RAW_START, RAW_END, frozenset(), RAW_END), shape=shape, rate=rate, scale=scale, obs=obs,
        mu_point=np.asarray(shape / rate * scale, dtype=np.float64), conf=np.full(n, "low", dtype=object),
        insufficient=np.zeros(n, dtype=np.bool_), zero_history=np.zeros(n, dtype=np.bool_),
        group=np.zeros(n, dtype=np.int64), routes=f64(10.0), evidence_dist=dist,
        geo_evidence=np.ones(n, dtype=np.bool_), percentile=f64(50.0), n_eff_geo=f64(20.0),
        n_eff_feature=f64(1.0), y_own=f64(0.0))


TOY_TIERS = ((40.0, 6000), (8.0, 2000), (2.0, 600))


def fold_results_toy(*, gain_by_band: tuple[float, ...] | None = None, calibrated: bool = True,
                     tiers: tuple[tuple[float, int], ...] = TOY_TIERS, true_phi: float = 1.0,
                     seed: int = 0) -> tuple[Any, Any, Any]:
    """(pooled, geo, dist) FoldResult lists for the tuning selectors.

    With gain_by_band: cells split into the tuning BAND_QUANTILES bands of evidence distance; geo-only
    predicts a mean of 2.5 where counts are Poisson(1), and pooled's mean per band is solved so its mean
    log-score gain over geo-only in that band equals the given value.

    Without: posterior-width tiers of (shape, cells), by default many narrow cells and few wide ones,
    so pooled O/E error grows as confidence drops. Counts come from the stated Gamma widened by
    true_phi (1: calibrated); calibrated=False also makes every true rate 3 times the stated one, so no
    level covers. geo is pooled."""
    from scipy.optimize import brentq

    from app.ml.metrics import nb_logpmf
    from app.ml.tuning import BAND_QUANTILES

    rng = np.random.default_rng(seed)
    if gain_by_band is not None:
        if len(gain_by_band) != len(BAND_QUANTILES) - 1:
            raise ValueError("one gain per band")
        n = 4000
        dist = np.linspace(0.1, 5.0, n)
        edges = np.quantile(dist, BAND_QUANTILES)
        band = np.searchsorted(edges[1:], dist, side="left")
        obs = rng.poisson(1.0, n).astype(np.float64)
        a = np.full(n, 200.0)
        geo_mean = 2.5
        pooled_rate = np.empty(n)
        for b, target in enumerate(gain_by_band):
            sel = band == b
            k, s = obs[sel], a[sel]
            base = nb_logpmf(k, s, s / geo_mean, np.ones(k.size))

            def gap(m: float) -> float:
                return float(np.mean(nb_logpmf(k, s, s / m, np.ones(k.size)) - base)) - target  # noqa: B023

            m = brentq(gap, float(k.mean()), geo_mean) if target > 0 else brentq(gap, geo_mean, 100.0)
            pooled_rate[sel] = a[sel] / m
        one = np.ones(n)
        pooled = toy_fold_result(a, pooled_rate, one, obs, dist)
        geo = toy_fold_result(a, a / geo_mean, one, obs, dist)
        return [pooled], [geo], dist
    shape = np.concatenate([np.full(n, a) for a, n in tiers])
    rate = shape.copy()
    scale = rng.uniform(0.5, 2.0, shape.size)
    theta = rng.gamma(shape / true_phi, true_phi / rate)
    obs = rng.poisson(theta * scale * (1.0 if calibrated else 3.0)).astype(np.float64)
    dist = np.zeros(shape.size)
    res = toy_fold_result(shape, rate, scale, obs, dist)
    return [res], [res], dist


def challenger_metrics(*, worse: bool = False, break_: str | None = None) -> dict[str, Any]:
    """A metrics dict in the shape train.py writes to model.registry.metrics (promote.evaluate_gate).
    worse lowers the held-out comparison metrics so the default dict beats it; break_ names the one
    gate component to fail, leaving every other component passing."""
    ls, top = (-0.60, 0.30) if worse else (-0.50, 0.40)
    zero_oe = [0.4, 0.5, 0.6] if break_ == "g2_zero_history" else [0.85, 1.05, 1.3]
    m: dict[str, Any] = {
        "main": {
            "log_score": -0.50, "top10": top,
            "calibration": {"scheme": "decile", "n_pass": 7 if break_ == "calibration" else 9,
                            "passed": break_ != "calibration"},
            "zero_history": {"oe": zero_oe},
            "by_kind": {k: {"log_score": ls, "top10": top} for k in ("spatial", "rolling")},
        },
        "geo_only": {"log_score": -0.55, "calibration": {"scheme": "decile",
                                                         "n_pass": 10 if break_ == "g1_pooling" else 6},
                     "zero_history": {"oe": [0.5, 0.7, 0.9]}},
        "permuted": {"log_score": -0.52 if break_ == "g3_negative_control" else -0.545},
        "sensitivity": {},
        "prospective": None, "prospective_reason": "no_test_incidents", "prospective_kernel": None,
        "recent": {},
        "thresholds": ({"conf_selected": False, "conf_high": None, "conf_medium": None, "phi": 1.0}
                       if break_ == "g4_confidence" else
                       {"conf_selected": True, "conf_high": 3.0, "conf_medium": 8.0, "phi": 1.0}),
        "stability": {"method": "state_group_subsample", "median_spearman": 0.9,
                      "median_band_churn": 0.2 if break_ == "stability" else 0.05, "n_subsamples": 20,
                      "n_failed": 0, "passed": break_ != "stability"},
        "capacity": {"wall_hours": 6.0 if break_ == "capacity" else 1.0, "peak_rss_gb": 4.0, "bundle_mb": 40.0,
                     "curve": None},
        "vs_active": {k: {"log_score_ci": [0.02, 0.05, 0.08]} for k in ("spatial", "rolling")},
    }
    return m


def rows_for_diff(*, changed_share: float, coverage_shift: float = 0.0,
                  n: int = 1000) -> tuple[list[tuple[int, str, str, float | None]], list[tuple[int, str, str, float | None]]]:
    """(challenger_rows, active_rows) over n synthetic routes. changed_share of them move one band up
    (red wraps to green); the rest move 1 point inside their band. coverage_shift adds challenger-only
    insufficient routes so challenger coverage drops by just over that share, without touching any
    compared route's band."""
    centres = (12.5, 37.5, 62.5, 87.5)
    states = tuple(STATES)
    n_changed = round(changed_share * n)
    active: list[tuple[int, str, str, float | None]] = []
    challenger: list[tuple[int, str, str, float | None]] = []
    for i in range(n):
        rid, g, st = SYNTHETIC_ID_FLOOR + i, TYPE_GROUPS[i % len(TYPE_GROUPS)], states[i % len(states)]
        b = i % len(centres)
        active.append((rid, g, st, centres[b]))
        challenger.append((rid, g, st, centres[(b + 1) % len(centres)] if i < n_changed else centres[b] + 1.0))
    extra = int(np.ceil(n * coverage_shift / (1.0 - coverage_shift))) if coverage_shift > 0 else 0
    challenger += [(SYNTHETIC_ID_FLOOR + n + j, TYPE_GROUPS[0], states[0], None) for j in range(extra)]
    return challenger, active


ROUTE_ID_BASE = SYNTHETIC_ID_FLOOR + 2_000_000
# A world whose model passes every gate component against a kernel with no signal, stability and
# confidence selection included: smaller or sparser worlds fail stability churn (measured 2026-10-22).
TRAINING_WORLD: dict[str, Any] = {"seed": 1, "rate_scale": 6.0, "n_areas": 200}
PREDICTION_MONTHS = (2, 3, 4, 5, 6)  # the prospective window (folds.PROSPECTIVE) holds Feb 5 - Jul 3, 2026


async def _bulk(conn: Any, table: str, cols: dict[str, tuple[Any, ...]]) -> None:
    """One parameterized INSERT ... SELECT FROM unnest(...) per call. cols maps a column to (array cast,
    values) or (array cast, values, select expression over the unnested column). Table and column names
    are constants of this module, never input."""
    from sqlalchemy import text

    names = list(cols)
    sql = (f"INSERT INTO {table} ({', '.join(names)}) SELECT "
           + ", ".join(cols[n][2] if len(cols[n]) > 2 else n for n in names) + " FROM unnest("
           + ", ".join(f"CAST(:{n} AS {cols[n][0]})" for n in names) + f") AS t({', '.join(names)})")
    await conn.execute(text(sql), {n: cols[n][1] for n in names})


def _accident_fields(p: Any) -> tuple[date | None, str, int | None, int | None, int | None]:
    """(date, date_precision, year, year_lo, year_hi) that incidents.place reads back as p's interval."""
    if p.lo is None or p.hi is None:
        return None, "unknown", None, None, None
    days = (p.hi - p.lo).days
    if days == 1:
        return p.lo, "day", p.lo.year, None, None
    if p.month is not None and p.lo.day == 1 and days <= 31:
        return p.lo, "month", p.lo.year, None, None
    return None, "year", p.lo.year, p.lo.year, p.hi.year - 1


async def seed_training_db(conn: Any, raw: Any, *, prediction_seed: int = 0) -> dict[str, int]:
    """Writes `raw` (a synthetic_raw world) into a migrated database as the tables training reads: MP
    locations and routes with fixture names only, the canonical catalog, per-route static features and
    climate normals, one exposure snapshot per cutoff (all built on the production day), accidents with
    route links (llm_link incidents through an R10 llm link), and the old kernel's stored predictions
    over the prospective window, drawn at random so the kernel carries no signal. Fully synthetic: every
    id is at least SYNTHETIC_ID_FLOOR. Returns row counts."""
    import uuid

    from app.data.repair.activity import APPROACH_FLAG

    area_by_id = {a.area_id: a for a in raw.areas}
    static_by_area: dict[int, dict[int, tuple[float | None, ...]]] = {}
    for s in raw.static:
        static_by_area.setdefault(s.area_id, {})[s.month] = s.values
    state_of_node: dict[int, str] = {}
    parent: dict[int, int | None] = {}
    path_of: dict[int, str] = {}
    for area_id, path in raw.paths.items():
        labels = [int(x) for x in path.split(".")]
        for depth, node in enumerate(labels):
            state_of_node.setdefault(node, area_by_id[area_id].state_code)
            parent[node] = labels[depth - 1] if depth else None
            path_of[node] = ".".join(str(x) for x in labels[: depth + 1])
    nodes = sorted(path_of)
    leaf = [n in area_by_id for n in nodes]

    def key(a: AreaInfo) -> str:
        return f"{a.lat:.5f}:{a.lon:.5f}"

    await _bulk(conn, "mp_locations", {
        "mp_id": ("bigint[]", nodes), "name": ("text[]", [f"Fixture Area {n}" for n in nodes]),
        "parent_id": ("bigint[]", [parent[n] for n in nodes]),
        "latitude": ("double precision[]", [area_by_id[n].lat if f else None for n, f in zip(nodes, leaf)]),
        "longitude": ("double precision[]", [area_by_id[n].lon if f else None for n, f in zip(nodes, leaf)])})
    await _bulk(conn, "canonical_areas", {
        "area_id": ("bigint[]", nodes), "parent_id": ("bigint[]", [parent[n] for n in nodes]),
        "path": ("ltree[]", [path_of[n] for n in nodes]),
        "depth": ("smallint[]", [path_of[n].count(".") + 1 for n in nodes]),
        "tree_status": ("text[]", ["us"] * len(nodes)), "state_code": ("text[]", [state_of_node[n] for n in nodes]),
        "lat": ("double precision[]", [area_by_id[n].lat if f else None for n, f in zip(nodes, leaf)]),
        "lon": ("double precision[]", [area_by_id[n].lon if f else None for n, f in zip(nodes, leaf)]),
        "coord_precision": ("text[]", ["own" if f else "none" for f in leaf]),
        "coord_source_id": ("bigint[]", [n if f else None for n, f in zip(nodes, leaf)]),
        "point_key": ("text[]", [key(area_by_id[n]) if f else None for n, f in zip(nodes, leaf)]),
        "catalog_version": ("text[]", ["fixture"] * len(nodes))})

    route_of: dict[tuple[int, str], int] = {}
    routes: list[tuple[int, int, str]] = []
    for a in raw.areas:
        for g, n in sorted(a.routes_by_group.items()):
            for _ in range(n):
                rid = ROUTE_ID_BASE + len(routes)
                route_of.setdefault((a.area_id, g), rid)
                routes.append((rid, a.area_id, g))
    rids = [r for r, _, _ in routes]
    await _bulk(conn, "mp_routes", {
        "mp_route_id": ("bigint[]", rids), "name": ("text[]", [f"Fixture Route {r}" for r in rids]),
        "location_id": ("bigint[]", [a for _, a, _ in routes]), "type": ("text[]", [g for _, _, g in routes])})
    await _bulk(conn, "canonical_routes", {
        "route_id": ("bigint[]", rids), "area_id": ("bigint[]", [a for _, a, _ in routes]),
        "type_group": ("text[]", [g for _, _, g in routes]), "is_boulder": ("boolean[]", [False] * len(rids)),
        "disciplines": ("text[]", [g for _, _, g in routes], "ARRAY[disciplines]"),
        "type_rule_version": ("text[]", ["rt-mp-v3"] * len(rids)), "scorable": ("boolean[]", [True] * len(rids)),
        "facts_sha256": ("text[]", ["fixture"] * len(rids)), "catalog_version": ("text[]", ["fixture"] * len(rids))})

    # One grid bucket and one feature point per area, so every route's min() over the area is the area's value.
    bucket = {a.area_id: i + 1 for i, a in enumerate(raw.areas)}
    await _bulk(conn, "feature_points", {
        "point_key": ("text[]", [key(a) for a in raw.areas]),
        "lat": ("double precision[]", [a.lat for a in raw.areas]),
        "lon": ("double precision[]", [a.lon for a in raw.areas]),
        "grid_bucket": ("integer[]", [bucket[a.area_id] for a in raw.areas]),
        "h3_r5": ("text[]", ["fixture"] * len(raw.areas)), "h3_r7": ("text[]", ["fixture"] * len(raw.areas)),
        "tz": ("text[]", ["America/Denver"] * len(raw.areas)),
        "elevation_m": ("real[]", [static_by_area[a.area_id][1][0] for a in raw.areas]),
        "feature_version": ("text[]", ["fixture"] * len(raw.areas))})
    await _bulk(conn, "route_static_features", {
        "route_id": ("bigint[]", rids), "source": ("text[]", ["mp"] * len(rids)),
        "point_key": ("text[]", [key(area_by_id[a]) for _, a, _ in routes]),
        "lat": ("double precision[]", [static_by_area[a][1][1] for _, a, _ in routes]),
        "elevation_m": ("real[]", [static_by_area[a][1][0] for _, a, _ in routes]),
        "grid_bucket": ("integer[]", [bucket[a] for _, a, _ in routes]),
        "coord_precision": ("text[]", ["own"] * len(rids)), "feature_version": ("text[]", ["fixture"] * len(rids))})
    await _bulk(conn, "cell_normals_status", {
        "grid_bucket": ("integer[]", list(bucket.values())), "status": ("text[]", ["complete"] * len(bucket))})
    normals = [(bucket[a], m, v) for a, by_month in static_by_area.items() for m, v in sorted(by_month.items())]
    await _bulk(conn, "cell_climate_normals", {
        "grid_bucket": ("integer[]", [b for b, _, _ in normals]), "month": ("smallint[]", [m for _, m, _ in normals]),
        **{c: ("real[]", [v[i] for _, _, v in normals]) for i, c in enumerate(
            ("tmax_mean", "tmin_mean", "precip_mm", "snowfall_cm", "freeze_thaw_days"), start=2)},
        "n_years": ("smallint[]", [10] * len(normals)), "period_start_year": ("smallint[]", [2016] * len(normals)),
        "period_end_year": ("smallint[]", [2025] * len(normals)),
        "source": ("text[]", ["derived_cell_daily_conditions"] * len(normals)),
        "normals_version": ("text[]", ["fixture"] * len(normals))})

    n_exposure = 0
    for cutoff, rows in sorted(raw.exposure_by_cutoff.items()):
        def comp(value: float | None, **extra: Any) -> dict[str, Any]:
            return {"value": None, "missing": True} if value is None else {"value": value, "missing": False, **extra}

        await _bulk(conn, "internal.exposure_index", {
            "area_id": ("bigint[]", [e.area_id for e in rows]), "type_group": ("text[]", [e.type_group for e in rows]),
            "month": ("smallint[]", [e.month for e in rows]), "n_routes": ("integer[]", [e.n_routes for e in rows]),
            "proxy": ("jsonb[]", [json.dumps({"season_share": comp(e.season_share),
                                              "mp_ticks": comp(e.ticks_value, per_route_mean=e.ticks_per_route)},
                                             allow_nan=False) for e in rows]),
            "proxy_version": ("text[]", ["x-mp-v1"] * len(rows)),
            "as_of": ("date[]", [raw.production_cutoff] * len(rows)), "data_cutoff": ("date[]", [cutoff] * len(rows)),
            "tick_coverage": ("real[]", [e.tick_coverage for e in rows])})
        n_exposure += len(rows)

    acc: dict[str, list[Any]] = {c: [] for c in ("accident_id", "source", "date", "year", "state", "date_precision",
                                                  "year_lo", "year_hi", "geocode_precision", "country",
                                                  "activity_class", "inclusion_flag", "mp_route_id")}
    llm: list[tuple[int, int]] = []
    for p in raw.placements:
        day, precision, year, lo, hi = _accident_fields(p)
        route = None
        if p.level == "cell":
            route = route_of[(p.area_id, p.group_weights[0][0])]
            if "llm_link" in p.flags:
                llm.append((p.accident_id, route))
                route = None
        for c, v in (("accident_id", p.accident_id), ("source", "AAC"), ("date", day), ("year", year),
                     ("state", p.state_code), ("date_precision", precision), ("year_lo", lo), ("year_hi", hi),
                     ("geocode_precision", "area" if p.level == "cell" else "unknown"), ("country", "US"),
                     ("activity_class", "climbing"),
                     ("inclusion_flag", APPROACH_FLAG if "ski_approach" in p.flags else None), ("mp_route_id", route)):
            acc[c].append(v)
    casts = {"accident_id": "integer[]", "date": "date[]", "year": "double precision[]", "year_lo": "smallint[]",
             "year_hi": "smallint[]", "mp_route_id": "bigint[]"}
    await _bulk(conn, "accidents", {c: (casts.get(c, "text[]"), v) for c, v in acc.items()})
    await _bulk(conn, "accident_legacy_links", {
        "accident_id": ("integer[]", [a for a, _ in llm]), "route_id": ("bigint[]", [r for _, r in llm]),
        "method": ("text[]", ["llm"] * len(llm)), "r10_version": ("text[]", ["fixture"] * len(llm)),
        "llm_call_id": ("uuid[]", [str(uuid.UUID(int=a)) for a, _ in llm])})

    rng = np.random.default_rng(prediction_seed)
    pred = [(r, date(2026, m, 15), float(rng.uniform(0.0, 100.0))) for r in rids for m in PREDICTION_MONTHS]
    await _bulk(conn, "historical_predictions", {
        "route_id": ("integer[]", [r for r, _, _ in pred]), "prediction_date": ("date[]", [d for _, d, _ in pred]),
        "risk_score": ("double precision[]", [s for _, _, s in pred]),
        "color_code": ("text[]", ["green"] * len(pred))})
    from sqlalchemy import text

    # Fresh bulk loads have no planner statistics; without them the incidents join plans as a nested loop.
    await conn.execute(text("ANALYZE"))
    return {"areas": len(nodes), "routes": len(rids), "exposure": n_exposure, "accidents": len(acc["accident_id"]),
            "llm_links": len(llm), "predictions": len(pred)}
