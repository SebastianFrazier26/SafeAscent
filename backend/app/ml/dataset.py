"""Training data on the MP catalog, read as trainer through views only (DP17). Readers keep SQL
NULL as None; assembly keeps it as NaN. Exposure comes from one internal.exposure_index snapshot
whose data_cutoff is the fold cutoff (P3:398, X-conflict 6).

ExposureRow from internal.exposure_index (proxy is the x-mp-v1 component JSON):
  season_share    <- proxy.season_share.value
  ticks_value     <- proxy.mp_ticks.value            (per-year ticks over complete routes, unscaled)
  ticks_per_route <- proxy.mp_ticks.per_route_mean
  tick_coverage   <- tick_coverage column
A component with missing = true, or a null value, reads None; a measured 0.0 stays 0.0."""

from __future__ import annotations

import json
from calendar import monthrange
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ml.cells import G_INDEX, TYPE_GROUPS, region_key
from app.ml.incidents import AreaInfo, IncidentRow, Placement, window_fraction

STATIC_FEATURES = ("elevation_m", "lat", "tmax_mean", "tmin_mean", "precip_mm", "snowfall_cm", "freeze_thaw_days")
MAX_SNAPSHOT_AGE_DAYS = 35
PROFILE_PSEUDOCOUNT = 0.5

INCIDENTS_SQL = """
SELECT a.accident_id, a.date, a.date_precision, a.year_lo, a.year_hi, a.state, a.geocode_precision,
       a.latitude, a.longitude, a.inclusion_flag, l.method, l.route_id, l.area_id,
       r.area_id AS route_area_id, r.type_group AS route_type_group, r.scorable AS route_scorable, a.year
FROM accidents_clean a
LEFT JOIN accident_route_links l ON l.accident_id = a.accident_id
LEFT JOIN canonical_routes r ON r.route_id = l.route_id
ORDER BY a.accident_id
"""
AREAS_SQL = """
SELECT s.area_id, s.state_code, a.lat, a.lon, s.type_group, count(*) AS n
FROM scorable_routes s JOIN canonical_areas a ON a.area_id = s.area_id
WHERE s.type_group <> 'unknown' AND a.lat IS NOT NULL
GROUP BY s.area_id, s.state_code, a.lat, a.lon, s.type_group
"""
PATHS_SQL = "SELECT DISTINCT area_id, area_path::text FROM scorable_routes WHERE area_path IS NOT NULL"
SNAPSHOT_SQL = "SELECT max(as_of) FROM internal.exposure_index WHERE data_cutoff = :cutoff"
LATEST_PRODUCTION_SQL = "SELECT max(as_of) FROM internal.exposure_index WHERE as_of = data_cutoff"
EXPOSURE_SQL = """
SELECT area_id, type_group, month, n_routes, proxy, tick_coverage FROM internal.exposure_index
WHERE as_of = :as_of AND data_cutoff = :cutoff
"""
# Every scorable area gets all 12 months, so an area with no route_static_features rows reads
# None rather than vanishing. Normals count only when every sampled route's normals are done:
# a min over a partial subset of routes would read as the area's value.
STATIC_SQL = """
SELECT s.area_id, m.month, min(u.elevation_m), min(u.lat), min(u.tmax_mean), min(u.tmin_mean), min(u.precip_mm),
       min(u.snowfall_cm), min(u.freeze_thaw_days), bool_and(u.normals_status IN ('complete', 'insufficient'))
FROM scorable_routes s
CROSS JOIN generate_series(1, 12) AS m(month)
LEFT JOIN scoring_unit_features u ON u.unit_kind = 'route' AND u.unit_id = s.route_id AND u.month = m.month
GROUP BY s.area_id, m.month
"""


class MissingAreas(RuntimeError):
    pass


class StaleExposure(RuntimeError):
    pass


@dataclass(frozen=True)
class ExposureRow:
    area_id: int
    type_group: str
    month: int
    n_routes: int
    season_share: float | None
    ticks_value: float | None
    ticks_per_route: float | None
    tick_coverage: float | None


@dataclass(frozen=True)
class StaticRow:
    area_id: int
    month: int
    values: tuple[float | None, ...]


@dataclass(frozen=True)
class CellData:
    area_idx: NDArray[np.int64]
    g: NDArray[np.int8]
    month: NDArray[np.int8]
    n_routes: NDArray[np.float64]
    season_share: NDArray[np.float64]
    ticks_value: NDArray[np.float64]
    ticks_per_route: NDArray[np.float64]
    tick_coverage: NDArray[np.float64]
    static: NDArray[np.float64]
    area_id: NDArray[np.int64]
    region_idx: NDArray[np.int64]
    state_idx: NDArray[np.int64]
    area_static: NDArray[np.float64]
    regions: tuple[str, ...]
    states: tuple[str, ...]
    index: Mapping[tuple[int, int, int], int]

    @property
    def n(self) -> int:
        return int(self.g.size)

    def take(self, rows: NDArray[np.int64]) -> CellData:
        """The cells at `rows`, in that order; area, region and state tables are kept whole so the
        indices still mean the same areas."""
        rows = np.asarray(rows, dtype=np.int64)
        if rows.ndim != 1 or (rows.size and (rows.min() < 0 or rows.max() >= self.n)):
            raise ValueError("rows must be a 1-d array of cell positions")
        if np.unique(rows).size != rows.size:
            raise ValueError("rows must not repeat a cell")
        pos = {int(old): new for new, old in enumerate(rows)}
        return replace(
            self, area_idx=self.area_idx[rows], g=self.g[rows], month=self.month[rows],
            n_routes=self.n_routes[rows], season_share=self.season_share[rows],
            ticks_value=self.ticks_value[rows], ticks_per_route=self.ticks_per_route[rows],
            tick_coverage=self.tick_coverage[rows], static=self.static[rows],
            index={k: pos[v] for k, v in self.index.items() if v in pos},
        )

    def subset(self, mask: NDArray[np.bool_]) -> CellData:
        mask = np.asarray(mask)
        if mask.dtype != np.bool_ or mask.shape != (self.n,):
            raise ValueError("mask must be a bool array with one value per cell")
        return self.take(np.flatnonzero(mask))


@dataclass(frozen=True)
class Counts:
    y: NDArray[np.float64]
    state_mass: NDArray[np.float64]
    years: float
    # Row counts per drop reason, except cell_without_exposure, which is weight mass whose cell
    # the snapshot does not hold. placed holds only mass that landed in y or state_mass.
    dropped: Mapping[str, float]
    placed: Mapping[str, float]


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _int(v: Any) -> int | None:
    return None if v is None else int(v)


def _nan(v: float | None) -> float:
    return np.nan if v is None else v


def _component(proxy: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    comp = proxy.get(key)
    if not isinstance(comp, Mapping) or comp.get("missing") or comp.get("value") is None:
        return None
    return comp


async def read_incidents(conn: AsyncConnection) -> list[IncidentRow]:
    rows = (await conn.execute(text(INCIDENTS_SQL))).all()
    return [
        IncidentRow(
            accident_id=int(r[0]), date=r[1], date_precision=r[2], year_lo=_int(r[3]), year_hi=_int(r[4]),
            state=r[5], geocode_precision=r[6], lat=_num(r[7]), lon=_num(r[8]), inclusion_flag=r[9],
            link_method=r[10], link_route_id=_int(r[11]), link_area_id=_int(r[12]), route_area_id=_int(r[13]),
            route_type_group=r[14], route_scorable=r[15], year=_int(r[16]),
        )
        for r in rows
    ]


async def read_areas(conn: AsyncConnection) -> list[AreaInfo]:
    groups: dict[int, dict[str, int]] = defaultdict(dict)
    meta: dict[int, tuple[str, float, float]] = {}
    for area_id, st, lat, lon, g, n in (await conn.execute(text(AREAS_SQL))).all():
        groups[int(area_id)][str(g)] = int(n)
        meta[int(area_id)] = (str(st), float(lat), float(lon))
    return [AreaInfo(a, *meta[a], dict(groups[a])) for a in sorted(meta)]


async def read_area_paths(conn: AsyncConnection) -> dict[int, str]:
    return {int(a): str(p) for a, p in (await conn.execute(text(PATHS_SQL))).all()}


async def pick_snapshot(conn: AsyncConnection, *, data_cutoff: date, production: bool, today: date) -> date:
    if production:
        as_of: date | None = (await conn.execute(text(LATEST_PRODUCTION_SQL))).scalar_one()
        if as_of is None or (today - as_of).days > MAX_SNAPSHOT_AGE_DAYS:
            # F21 (controller ruling 2026-10-22): the 35-day gate stays; the runbook rebuilds before every run.
            raise StaleExposure(f"newest production exposure snapshot is {as_of}; run "
                                "INGMOD app.pipelines.exposure build first")
        return as_of
    as_of = (await conn.execute(text(SNAPSHOT_SQL), {"cutoff": data_cutoff})).scalar_one()
    if as_of is None:
        raise StaleExposure(f"no exposure snapshot with data_cutoff {data_cutoff}; run "
                            f"`exposure build --cutoff {data_cutoff}` (Task 21)")
    return as_of


async def read_exposure(conn: AsyncConnection, *, as_of: date, data_cutoff: date) -> list[ExposureRow]:
    out = []
    result = await conn.execute(text(EXPOSURE_SQL), {"as_of": as_of, "cutoff": data_cutoff})
    for area_id, g, month, n_routes, raw, cov in result.all():
        proxy: Mapping[str, Any] = raw if isinstance(raw, Mapping) else json.loads(raw)
        season, ticks = _component(proxy, "season_share"), _component(proxy, "mp_ticks")
        out.append(ExposureRow(
            int(area_id), str(g), int(month), int(n_routes),
            None if season is None else float(season["value"]),
            None if ticks is None else float(ticks["value"]),
            None if ticks is None else _num(ticks.get("per_route_mean")),
            _num(cov),
        ))
    if not out:  # pruned between pick_snapshot and this read, or never built
        raise StaleExposure(f"no exposure rows for as_of {as_of}, data_cutoff {data_cutoff}")
    return out


async def read_static(conn: AsyncConnection) -> list[StaticRow]:
    out = []
    for r in (await conn.execute(text(STATIC_SQL))).all():
        normals_ok = bool(r[9])
        vals = [_num(r[2]), _num(r[3])] + [_num(v) if normals_ok else None for v in r[4:9]]
        out.append(StaticRow(int(r[0]), int(r[1]), tuple(vals)))
    return out


def assemble_cells(exposure: Sequence[ExposureRow], static: Sequence[StaticRow], areas: Sequence[AreaInfo],
                   paths: Mapping[int, str]) -> CellData:
    area_ids = sorted({e.area_id for e in exposure})
    a_pos = {a: i for i, a in enumerate(area_ids)}
    st_of = {a.area_id: a.state_code for a in areas}
    missing = sorted(a for a in area_ids if a not in st_of or a not in paths)
    if missing:  # pre-flight F24: a named error, not a KeyError
        raise MissingAreas(f"exposure areas absent from read_areas/paths: {missing[:20]}")
    states = tuple(sorted({st_of[a] for a in area_ids}))
    regions = tuple(sorted({region_key(paths[a]) for a in area_ids}))
    area_static = np.full((len(area_ids), 12, len(STATIC_FEATURES)), np.nan)
    for s in static:
        if s.area_id in a_pos:
            area_static[a_pos[s.area_id], s.month - 1] = [_nan(v) for v in s.values]
    rows = sorted(exposure, key=lambda e: (e.area_id, G_INDEX[e.type_group], e.month))
    area_idx = np.array([a_pos[e.area_id] for e in rows], dtype=np.int64)
    month = np.array([e.month for e in rows], dtype=np.int8)
    return CellData(
        area_idx=area_idx, g=np.array([G_INDEX[e.type_group] for e in rows], dtype=np.int8), month=month,
        n_routes=np.array([e.n_routes for e in rows], dtype=np.float64),
        season_share=np.array([_nan(e.season_share) for e in rows], dtype=np.float64),
        ticks_value=np.array([_nan(e.ticks_value) for e in rows], dtype=np.float64),
        ticks_per_route=np.array([_nan(e.ticks_per_route) for e in rows], dtype=np.float64),
        tick_coverage=np.array([_nan(e.tick_coverage) for e in rows], dtype=np.float64),
        static=area_static[area_idx, month.astype(np.int64) - 1],
        area_id=np.array(area_ids, dtype=np.int64),
        region_idx=np.array([regions.index(region_key(paths[a])) for a in area_ids], dtype=np.int64),
        state_idx=np.array([states.index(st_of[a]) for a in area_ids], dtype=np.int64),
        area_static=area_static, regions=regions, states=states,
        index={(e.area_id, G_INDEX[e.type_group], e.month): i for i, e in enumerate(rows)},
    )


def seasonal_profile(placements: Sequence[Placement], *, start: date, end: date,
                     exclude_flags: frozenset[str] = frozenset()) -> NDArray[np.float64]:
    """National month profile per type group from month-known incidents in the window, with a
    0.5 pseudocount per month so a month with no reports is small, never impossible."""
    counts = np.full((len(TYPE_GROUPS), 12), PROFILE_PSEUDOCOUNT)
    for p in placements:
        if p.month is None or p.level != "cell" or p.flags & exclude_flags:
            continue
        w = window_fraction(p, start, end) * p.weight
        for g, share in p.group_weights:
            counts[G_INDEX[g], p.month - 1] += w * share
    out: NDArray[np.float64] = counts / counts.sum(axis=1, keepdims=True)
    return out


def count_incidents(placements: Sequence[Placement], cells: CellData, *, start: date, end: date,
                    exclude_flags: frozenset[str] = frozenset(),
                    profile: NDArray[np.float64] | None = None) -> Counts:
    prof = (seasonal_profile(placements, start=start, end=end, exclude_flags=exclude_flags)
            if profile is None else profile)
    # pre-flight F23: the January-only state split relies on every (area, g) carrying all 12 months
    if len(cells.index) != 12 * len({(a, g) for a, g, _ in cells.index}):
        raise ValueError("partial exposure snapshot: some (area, type group) lacks one of the 12 months")
    y = np.zeros(cells.n)
    state_mass = np.zeros((len(cells.states), len(TYPE_GROUPS)))
    dropped: defaultdict[str, float] = defaultdict(float)
    placed: defaultdict[str, float] = defaultdict(float)
    state_share = _state_group_share(cells)
    for p in placements:
        if p.level == "dropped" or p.flags & exclude_flags:
            dropped[p.reason if p.level == "dropped" else "excluded_flag"] += 1
            continue
        w = window_fraction(p, start, end) * p.weight
        if w == 0.0:
            continue
        if p.level == "state":
            # F39 (DP4): state-only evidence is kept apart in state_mass and never enters y.
            if p.state_code in cells.states:
                si = cells.states.index(p.state_code)
                state_mass[si] += w * state_share[si]
                placed["state"] += w
            else:
                dropped["state_without_cells"] += 1
            continue
        if p.area_id is None:
            raise ValueError(f"cell placement {p.accident_id} carries no area")
        for g, share in p.group_weights:
            gi = G_INDEX[g]
            months = [(p.month, w)] if p.month is not None else _month_mass(p, prof[gi], start, end)
            for m, mw in months:
                key = (p.area_id, gi, m)
                if key in cells.index:
                    y[cells.index[key]] += share * mw
                    placed["cell"] += share * mw
                else:
                    dropped["cell_without_exposure"] += share * mw
    return Counts(y=y, state_mass=state_mass, years=(end - start).days / 365.25, dropped=dict(dropped),
                  placed=dict(placed))


def _month_mass(p: Placement, prof: NDArray[np.float64], start: date, end: date) -> list[tuple[int, float]]:
    """Window mass per calendar month of a month-unknown placement. Each year in [lo, hi) takes its
    day share of the interval and spreads it by the profile over that year's months inside the
    interval; each (year, month) then counts only its in-window day fraction, so a cutoff mid-year
    keeps no mass from months after it."""
    assert p.lo is not None and p.hi is not None
    total = (p.hi - p.lo).days
    out = [0.0] * 12
    for year in range(p.lo.year, (p.hi - timedelta(days=1)).year + 1):
        segs = []
        for m in range(1, 13):
            first = date(year, m, 1)
            last = first + timedelta(days=monthrange(year, m)[1])
            lo, hi = max(first, p.lo), min(last, p.hi)
            if hi > lo:
                win = max((min(hi, end) - max(lo, start)).days, 0)
                segs.append((m, (hi - lo).days, win))
        days = sum(d for _, d, _ in segs)
        norm = sum(float(prof[m - 1]) for m, _, _ in segs)
        for m, d, win in segs:
            out[m - 1] += p.weight * days / total * float(prof[m - 1]) / norm * win / d
    return [(m, out[m - 1]) for m in range(1, 13) if out[m - 1] > 0.0]


def _state_group_share(cells: CellData) -> NDArray[np.float64]:
    """Each state's route share per type group, which splits a state-only incident's weight."""
    share = np.zeros((len(cells.states), len(TYPE_GROUPS)))
    january = cells.month == 1
    np.add.at(share, (cells.state_idx[cells.area_idx[january]], cells.g[january]), cells.n_routes[january])
    total = share.sum(axis=1, keepdims=True)
    out: NDArray[np.float64] = np.divide(share, total, out=np.zeros_like(share), where=total > 0)
    return out
