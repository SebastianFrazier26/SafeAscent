from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def haversine_km_many(
    lat: float, lon: float, lats: NDArray[np.float64], lons: NDArray[np.float64]
) -> NDArray[np.float64]:
    p1, p2 = np.radians(lat), np.radians(lats)
    dp, dl = p2 - p1, np.radians(lons - lon)
    h = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    out: NDArray[np.float64] = 2 * EARTH_RADIUS_KM * np.arcsin(np.minimum(1.0, np.sqrt(h)))
    return out
