"""Synthetic CellData for model tests: no DB, no real ids (area ids start at 900000000). Built through
assemble_cells so the fixture shares the production cell layout. Task 20 extends this module; the
synthetic_cells signature is fixed (pre-flight pair 8 <-> 20)."""

from __future__ import annotations

from dataclasses import dataclass
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
    # Clustered, not even: MoM sees extra-Poisson variance and keeps every kappa below KAPPA_MAX.
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
