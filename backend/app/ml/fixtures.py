"""Synthetic CellData for model tests: no DB, no real ids (area ids start at 900000000). Built through
assemble_cells so the fixture shares the production cell layout. Task 20 extends this module; the
synthetic_cells signature is fixed (pre-flight pair 8 <-> 20)."""

from __future__ import annotations

import numpy as np

from app.ml.cells import FAMILY_OF, TYPE_GROUPS
from app.ml.dataset import CellData, ExposureRow, StaticRow, assemble_cells
from app.ml.incidents import AreaInfo

SYNTHETIC_ID_FLOOR = 900_000_000
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
