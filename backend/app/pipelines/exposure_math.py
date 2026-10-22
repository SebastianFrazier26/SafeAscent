"""Exposure (P2-9) on the MP catalog: the pure rules and arithmetic. Components are stored raw with
a missing flag and Phase 3 fits their weights (P3 M5). Nothing here turns a missing input into a
number: absent stays absent, a measured zero stays zero, and tick sums are never scaled by coverage."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.pipelines.era5_fill import HISTORY_YEARS

PROXY_VERSION = "x-mp-v1"
COMPONENTS = ("route_density", "season_share", "mp_ticks")
TYPE_GROUPS = ("sport", "trad", "rock", "alpine", "ice", "mixed")
MONTHS = range(1, 13)
RULE_BY_GROUP = {"sport": "rock", "trad": "rock", "rock": "rock", "alpine": "alpine", "ice": "ice", "mixed": "ice"}
ROCK_TMAX_LO, ROCK_TMAX_HI, ROCK_MAX_PRECIP = 5.0, 32.0, 2.0
ICE_WINDOW, ICE_MIN_FROZEN = 10, 7
# F9: equal to era5_fill.HISTORY_YEARS, not redeclared, so the window this reads and the window
# era5_fill prunes to can't drift apart.
SEASON_YEARS = HISTORY_YEARS
MIN_SEASON_DAYS = 28
TICK_WINDOW_MONTHS = 60
TICK_WINDOW_YEARS = TICK_WINDOW_MONTHS // 12
BOUND_FACTOR = 3.0

Detail = tuple[tuple[str, float], ...]


def _finite(name: str, value: float) -> float:
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return v


@dataclass(frozen=True)
class Component:
    value: float | None
    missing: bool
    reason: str | None = None
    detail: Detail = ()

    @classmethod
    def present(cls, value: float, **detail: float) -> Component:
        return cls(_finite("component value", value), False, None,
                   tuple(sorted((k, _finite(k, v)) for k, v in detail.items())))

    @classmethod
    def absent(cls, reason: str, **detail: float) -> Component:
        return cls(None, True, reason, tuple(sorted((k, _finite(k, v)) for k, v in detail.items())))

    def get(self, name: str) -> float | None:
        return dict(self.detail).get(name)

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"value": self.value, "missing": self.missing}
        if self.reason is not None:
            out["reason"] = self.reason
        out.update(self.detail)
        return out


def rock_day(tmax: float | None, precip: float | None) -> bool | None:
    if tmax is None or precip is None:
        return None
    return ROCK_TMAX_LO <= tmax <= ROCK_TMAX_HI and precip < ROCK_MAX_PRECIP


def ice_day(frozen: int, known: int) -> bool | None:
    """frozen/known count days with tmax < 0 and with any tmax in the ICE_WINDOW days ending today;
    an absent or NULL day is unknown, so it decides nothing unless it could not change the answer."""
    if frozen >= ICE_MIN_FROZEN:
        return True
    if frozen + (ICE_WINDOW - known) < ICE_MIN_FROZEN:
        return False
    return None


def alpine_day(rock: bool | None, ice: bool | None) -> bool | None:
    if rock is True or ice is True:
        return True
    if rock is None or ice is None:
        return None
    return False


def _ranks(values: Sequence[float]) -> NDArray[np.float64]:
    x = np.asarray(values, dtype=np.float64)
    order = x.argsort(kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x), dtype=np.float64)
    for v in np.unique(x):
        tied = x == v
        ranks[tied] = ranks[tied].mean()
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def exposure_point(route_density: float | None, season: float | None) -> float | None:
    # No floor on a missing season: inventing exposure would lower per-exposure risk.
    if route_density is None or season is None:
        return None
    return route_density * season


def bounds(point: float | None) -> tuple[float | None, float | None]:
    if point is None or not math.isfinite(point):
        return None, None
    return point / BOUND_FACTOR, point * BOUND_FACTOR


def tick_component(*, n_routes: int, n_complete: int, n_scraped: int, n_missing: int, window_ticks: float) -> Component:
    """window_ticks: the cell's ticks in that calendar month over each complete route's
    TICK_WINDOW_MONTHS window. The value is their per-year mean, raw: it covers the complete routes
    only and is never scaled to n_routes (DP2)."""
    if n_complete + n_scraped + n_missing != n_routes or n_routes <= 0:
        raise ValueError(f"route statuses must add up to n_routes ({n_complete}+{n_scraped}+{n_missing} != {n_routes})")
    counts = {"coverage": n_complete / n_routes, "n_complete": n_complete, "n_scraped": n_scraped,
              "n_missing": n_missing}
    if n_complete == 0:
        return Component.absent("no_complete_routes", **counts)
    per_year = window_ticks / TICK_WINDOW_YEARS
    return Component.present(per_year, per_route_mean=per_year / n_complete, window_months=TICK_WINDOW_MONTHS, **counts)
