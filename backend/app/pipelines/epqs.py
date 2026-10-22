"""USGS Elevation Point Query Service cross-check of the 3DEP sampler. EPQS answers from
the best available DEM (often 1 m lidar), not the 1/3″ product we sample (review claim 11),
so the gate compares median and p90 |Δ| per slope class instead of a per-point bound.
EPQS stalls under load: calls are slow, time out, and retry with backoff."""

from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Sequence

import httpx
import numpy as np

from app.pipelines import USER_AGENT
from app.pipelines.dem import Elevation

EPQS_URL = "https://epqs.nationalmap.gov/v1/json"
NODATA = -1_000_000.0
MIN_COMPARED = 180
MIN_PER_CLASS = 30
PER_CLASS_TARGET = 80
SLOPE_CLASSES: tuple[tuple[str, float, float], ...] = (("flat", 0.0, 10.0), ("moderate", 10.0, 30.0), ("steep", 30.0, 91.0))
# Tolerances by the DEM a point was sampled from (final review minor, 2026-10-14). 3dep_2 (2″,
# ~60 m posts, mostly Alaska) is judged against EPQS's best DEM like 3dep_13 (1/3″, ~10 m), but a
# 60 m cell averages six times the ground, so its error grows with slope: roughly 2.5x the 1/3″
# median/p90 per class, and a gross bound of 200 m (60 m of run at a 45° slope is ~60 m of rise,
# plus the cell's own averaging). A class passes when the median and p90 of each pair's
# |Δ| / its own source's bound are at most 1, which for 3dep_13-only rows is the plain metre test.
DEFAULT_SOURCE = "3dep_13"
GROSS_M: dict[str, float] = {"3dep_13": 100.0, "3dep_2": 200.0}
TOLERANCE_M: dict[str, dict[str, tuple[float, float]]] = {
    "3dep_13": {"flat": (2.0, 5.0), "moderate": (4.0, 10.0), "steep": (8.0, 20.0)},
    "3dep_2": {"flat": (5.0, 12.5), "moderate": (10.0, 25.0), "steep": (20.0, 50.0)},
}
NEIGHBOUR_M = 15.0
# Controller ruling (2026-10-14): EPQS failing mostly on one kind of terrain inside a class
# would let the gate pass on a biased subset, so a class with more failures than this fails closed.
MAX_FAILURE_SHARE = 0.20

Sampler = Callable[[Sequence[tuple[float, float]]], list[Elevation | None]]


class EpqsClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        pause_s: float = 1.0,
        timeout_s: float = 20.0,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(transport=transport, timeout=timeout_s, headers={"User-Agent": USER_AGENT})
        self._pause = pause_s
        self._retries = retries
        self._sleep = sleep
        self.failed_calls = 0

    def elevation(self, lat: float, lon: float) -> float | None:
        params: dict[str, str | float] = {"x": lon, "y": lat, "units": "Meters", "wkid": 4326, "includeDate": "false"}
        for attempt in range(self._retries + 1):
            try:
                response: httpx.Response | None = self._client.get(EPQS_URL, params=params)
            except httpx.HTTPError:
                response = None
            self._sleep(self._pause)
            if response is not None and response.status_code == 200:
                try:
                    v = float(response.json().get("value"))
                except (TypeError, ValueError, AttributeError):
                    return None
                # float("NaN") parses; a NaN reference would slip past every <=/> test downstream.
                return None if not math.isfinite(v) or v <= NODATA else v
            if response is not None and response.status_code < 500 and response.status_code != 429:
                return None
            self._sleep(2.0 ** (attempt + 1))
        self.failed_calls += 1
        return None


def slope_class(deg: float) -> str:
    if not math.isfinite(deg):
        raise ValueError(f"slope_class needs a finite slope, got {deg!r}")
    for name, lo, hi in SLOPE_CLASSES:
        if lo <= deg < hi:
            return name
    return "steep"


def local_slopes(points: Sequence[tuple[float, float]], sampler: Sampler) -> list[float | None]:
    """Slope (deg) of the sampled DEM at each point from four neighbours 15 m away."""
    probes: list[tuple[float, float]] = []
    for lat, lon in points:
        dlat = NEIGHBOUR_M / 110_574.0
        dlon = NEIGHBOUR_M / (111_320.0 * math.cos(math.radians(lat)))
        probes += [(lat + dlat, lon), (lat - dlat, lon), (lat, lon + dlon), (lat, lon - dlon)]
    values = sampler(probes)
    out: list[float | None] = []
    for i in range(len(points)):
        quad = values[4 * i : 4 * i + 4]
        z = [e.elevation_m if e is not None else None for e in quad]
        if any(v is None for v in z):
            out.append(None)
            continue
        n, s, e, w = (float(v) for v in z if v is not None)
        out.append(math.degrees(math.atan(math.hypot((n - s) / (2 * NEIGHBOUR_M), (e - w) / (2 * NEIGHBOUR_M)))))
    return out


def compare(rows: Sequence[tuple[str, float | None, float | None]],
            sources: Sequence[str] | None = None) -> dict[str, object]:
    """sources[i] is the dem_source of rows[i] (default: all 3dep_13)."""
    if sources is not None and len(sources) != len(rows):
        raise ValueError(f"{len(sources)} sources for {len(rows)} rows")
    srcs = list(sources) if sources is not None else [DEFAULT_SOURCE] * len(rows)
    by: dict[str, list[tuple[float, str]]] = defaultdict(list)
    gross = 0
    for (cls, dem, ref), src in zip(rows, srcs, strict=True):
        if dem is None or ref is None or not (math.isfinite(dem) and math.isfinite(ref)):
            continue
        d = abs(dem - ref)
        by[cls].append((d, src))
        gross += int(d > GROSS_M[src])
    compared = sum(len(v) for v in by.values())
    ok = compared >= MIN_COMPARED and gross == 0
    classes: dict[str, dict[str, float | int | None]] = {}
    for cls, _, _ in SLOPE_CLASSES:
        pairs = by.get(cls, [])
        if len(pairs) < MIN_PER_CLASS:
            classes[cls] = {"n": len(pairs), "median": None, "p90": None, "passed": 0}
            ok = False
            continue
        diffs = [d for d, _ in pairs]
        median_ratio = statistics.median(d / TOLERANCE_M[src][cls][0] for d, src in pairs)
        p90_ratio = float(np.percentile([d / TOLERANCE_M[src][cls][1] for d, src in pairs], 90))
        passed = median_ratio <= 1.0 and p90_ratio <= 1.0
        classes[cls] = {"n": len(pairs), "median": round(statistics.median(diffs), 2),
                        "p90": round(float(np.percentile(diffs, 90)), 2),
                        "n_3dep_2": sum(1 for _, src in pairs if src == "3dep_2"), "passed": int(passed)}
        ok = ok and passed
    return {"compared": compared, "gross": gross, "classes": classes, "passed": int(ok)}


def failure_guard(rows: Sequence[tuple[str, float | None, float | None]]) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Per slope class: points attempted, EPQS failures (no usable reference value) and pairs
    compared; plus one problem per class whose failure share exceeds MAX_FAILURE_SHARE."""
    counts = {cls: {"attempted": 0, "failed": 0, "compared": 0} for cls, _, _ in SLOPE_CLASSES}
    for cls, dem, ref in rows:
        c = counts[cls]
        c["attempted"] += 1
        if ref is None or not math.isfinite(ref):
            c["failed"] += 1
        elif dem is not None and math.isfinite(dem):
            c["compared"] += 1
    problems = [
        f"epqs failure share {c['failed'] / c['attempted']:.3f} in class {cls} exceeds {MAX_FAILURE_SHARE:.2f}"
        for cls, c in counts.items()
        if c["attempted"] and c["failed"] / c["attempted"] > MAX_FAILURE_SHARE
    ]
    return counts, problems
