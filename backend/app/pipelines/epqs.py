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

from app.pipelines.dem import Elevation

EPQS_URL = "https://epqs.nationalmap.gov/v1/json"
NODATA = -1_000_000.0
MIN_COMPARED = 180
MIN_PER_CLASS = 30
PER_CLASS_TARGET = 80
GROSS_M = 100.0
SLOPE_CLASSES: tuple[tuple[str, float, float], ...] = (("flat", 0.0, 10.0), ("moderate", 10.0, 30.0), ("steep", 30.0, 91.0))
TOLERANCE_M: dict[str, tuple[float, float]] = {"flat": (2.0, 5.0), "moderate": (4.0, 10.0), "steep": (8.0, 20.0)}
NEIGHBOUR_M = 15.0

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
        self._client = httpx.Client(transport=transport, timeout=timeout_s)
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


def compare(rows: Sequence[tuple[str, float | None, float | None]]) -> dict[str, object]:
    by: dict[str, list[float]] = defaultdict(list)
    gross = 0
    for cls, dem, ref in rows:
        if dem is None or ref is None or not (math.isfinite(dem) and math.isfinite(ref)):
            continue
        d = abs(dem - ref)
        by[cls].append(d)
        gross += int(d > GROSS_M)
    compared = sum(len(v) for v in by.values())
    ok = compared >= MIN_COMPARED and gross == 0
    classes: dict[str, dict[str, float | int | None]] = {}
    for cls, _, _ in SLOPE_CLASSES:
        diffs = by.get(cls, [])
        if len(diffs) < MIN_PER_CLASS:
            classes[cls] = {"n": len(diffs), "median": None, "p90": None, "passed": 0}
            ok = False
            continue
        median = statistics.median(diffs)
        p90 = float(np.percentile(diffs, 90))
        max_median, max_p90 = TOLERANCE_M[cls]
        passed = median <= max_median and p90 <= max_p90
        classes[cls] = {"n": len(diffs), "median": round(median, 2), "p90": round(p90, 2), "passed": int(passed)}
        ok = ok and passed
    return {"compared": compared, "gross": gross, "classes": classes, "passed": int(ok)}
