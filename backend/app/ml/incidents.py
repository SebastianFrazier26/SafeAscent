"""Incident placement (DP4) and point-in-time weights. A year-precision AAC row is the interval
[year_lo, year_hi] (owner 2026-09-30), never its synthetic July-1 date; a month row is its month,
never its synthetic day 15. Weight inside a window = overlap days / interval days. A `state`
placement only flags evidence (DP4 ruling F39): it never carries an area or group weights."""

from __future__ import annotations

import math
from calendar import monthrange
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import numpy as np

from app.data.repair.activity import APPROACH_FLAG
from app.ml.cells import NEAREST_AREA_KM, TRUSTED_FOR_CELLS, TYPE_GROUPS
from app.pipelines.geo import haversine_km
from app.pipelines.gnis import state_code

Level = Literal["cell", "state", "dropped"]


@dataclass(frozen=True)
class IncidentRow:
    accident_id: int
    date: date | None
    date_precision: str | None
    year_lo: int | None
    year_hi: int | None
    state: str | None
    geocode_precision: str | None
    lat: float | None
    lon: float | None
    inclusion_flag: str | None
    link_method: str | None
    link_route_id: int | None
    link_area_id: int | None
    route_area_id: int | None
    route_type_group: str | None
    route_scorable: bool | None
    weight: float = 1.0
    # accidents.year: the only year a non-AAC year-precision row with no date carries.
    year: int | None = None


@dataclass(frozen=True)
class AreaInfo:
    area_id: int
    state_code: str
    lat: float
    lon: float
    routes_by_group: Mapping[str, int]


class AreaLookup:
    def __init__(self, areas: Sequence[AreaInfo]) -> None:
        self._by_id = {a.area_id: a for a in areas}
        self._ids = np.array([a.area_id for a in areas], dtype=np.int64)
        self._lat = np.array([a.lat for a in areas], dtype=np.float64)
        self._lon = np.array([a.lon for a in areas], dtype=np.float64)

    def get(self, area_id: int | None) -> AreaInfo | None:
        return None if area_id is None else self._by_id.get(area_id)

    def nearest(self, lat: float, lon: float, max_km: float) -> AreaInfo | None:
        if self._ids.size == 0:
            return None
        # pre-flight F18: a fixed lon box skips true neighbours above ~45 N; scale by cos(lat)
        lon_deg = max_km / (111.0 * max(math.cos(math.radians(lat)), 0.1))
        box = (np.abs(self._lat - lat) < max_km / 100.0) & (np.abs(self._lon - lon) < lon_deg)
        best: tuple[float, int] | None = None
        for i in np.flatnonzero(box):
            km = haversine_km(lat, lon, float(self._lat[i]), float(self._lon[i]))
            if km <= max_km and (best is None or (km, int(self._ids[i])) < best):
                best = (km, int(self._ids[i]))
        return None if best is None else self._by_id[best[1]]


@dataclass(frozen=True)
class Placement:
    accident_id: int
    level: Level
    reason: str
    area_id: int | None
    state_code: str | None
    group_weights: tuple[tuple[str, float], ...]
    month: int | None
    lo: date | None
    hi: date | None
    weight: float
    flags: frozenset[str]


def interval(row: IncidentRow) -> tuple[date, date] | None:
    """Half-open [lo, hi). None when the row has no usable time: an `unknown` or missing
    precision is never read as a day."""
    ranged = row.year_lo is not None and row.year_hi is not None and row.year_lo < row.year_hi
    if row.date_precision == "year" or ranged:
        lo = row.year_lo if row.year_lo is not None else (row.date.year if row.date else row.year)
        hi = row.year_hi if row.year_hi is not None else lo
        return None if lo is None or hi is None else (date(lo, 1, 1), date(hi + 1, 1, 1))
    if row.date is None:
        return None
    if row.date_precision == "month":
        first = row.date.replace(day=1)
        return first, first + timedelta(days=monthrange(first.year, first.month)[1])
    if row.date_precision == "day":
        return row.date, row.date + timedelta(days=1)
    return None


def _split(info: AreaInfo) -> tuple[tuple[str, float], ...]:
    counts = {g: n for g, n in info.routes_by_group.items() if g in TYPE_GROUPS and n > 0}
    total = sum(counts.values())
    return tuple((g, n / total) for g, n in sorted(counts.items()))


def place(row: IncidentRow, areas: AreaLookup) -> Placement:
    span = interval(row)
    flags = frozenset(
        f
        for f, on in (("ski_approach", row.inclusion_flag == APPROACH_FLAG), ("llm_link", row.link_method == "llm"))
        if on
    )
    month = (
        row.date.month
        if span is not None and row.date is not None and row.date_precision in ("day", "month")
        and (span[1] - span[0]).days <= 31
        else None
    )
    st = state_code(row.state)

    def out(level: Level, reason: str, info: AreaInfo | None = None,
            groups: tuple[tuple[str, float], ...] = ()) -> Placement:
        return Placement(
            row.accident_id, level, reason,
            None if info is None else info.area_id,
            st if info is None else info.state_code,
            groups, month,
            None if span is None else span[0], None if span is None else span[1],
            row.weight, flags,
        )

    if span is None:
        return out("dropped", "no_date")
    if row.link_route_id is not None and row.route_type_group is None and row.route_area_id is not None:
        return out("dropped", "boulder_route")
    linked = areas.get(row.route_area_id if row.link_route_id is not None else row.link_area_id)
    if row.link_route_id is not None and row.route_scorable and row.route_type_group in TYPE_GROUPS and linked:
        return out("cell", "route_link", linked, ((str(row.route_type_group), 1.0),))
    if linked is not None and (split := _split(linked)):
        return out("cell", "area_link", linked, split)
    if (
        row.link_route_id is None and row.link_area_id is None and row.geocode_precision in TRUSTED_FOR_CELLS
        and row.lat is not None and row.lon is not None
    ):
        near = areas.nearest(row.lat, row.lon, NEAREST_AREA_KM)
        if near is not None and (split := _split(near)):
            return out("cell", "nearest_area", near, split)
    if st is None:
        return out("dropped", "no_state")
    return out("state", "state_only")


def window_fraction(p: Placement, start: date, end: date) -> float:
    if p.lo is None or p.hi is None or p.level == "dropped":
        return 0.0
    overlap = (min(p.hi, end) - max(p.lo, start)).days
    return max(overlap, 0) / (p.hi - p.lo).days
