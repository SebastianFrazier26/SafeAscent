"""USGS 3DEP elevation (public domain) sampled from remote Cloud-Optimized GeoTIFFs: only
the blocks under our points are fetched. 1/3″ first, 2″ where 1/3″ has no tile (the
remaining Alaska gaps) or where a 1/3″ tile exists but is masked at the point (partial
coverage at coasts, borders and in Alaska; final review minor, 2026-10-14). Masked cells, fill values and missing tiles are NULL, never 0.
A tile that cannot be read for any reason other than 'does not exist' raises
TransientTileError, and its points stay unfilled for the next run."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

import httpx
import numpy as np
import rasterio
import rasterio.windows
from numpy.typing import NDArray
from rasterio.errors import RasterioIOError
from rasterio.io import DatasetReader

from app.pipelines import USER_AGENT

TILE_BASE = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation"
RES: dict[str, tuple[str, float]] = {"3dep_13": ("13", 10.0), "3dep_2": ("2", 60.0)}
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "2",
    # Without these a stalled S3 read hangs the run instead of surfacing as a transient error.
    "GDAL_HTTP_TIMEOUT": "30",
    "GDAL_HTTP_CONNECTTIMEOUT": "10",
    "GDAL_HTTP_USERAGENT": USER_AGENT,
}
# S3 answers 403 instead of 404 for a missing key when listing is not public.
MISSING_STATUS = frozenset({403, 404})
# Below any US elevation; catches float32 fill values in tiles that declare no nodata.
LOW_SENTINEL = -1.0e4

Opener = Callable[[str], AbstractContextManager[DatasetReader] | None]


class TransientTileError(Exception):
    """The tile may exist but could not be read now; retry on a later run."""


@dataclass(frozen=True)
class Elevation:
    elevation_m: float | None
    dem_source: str
    dem_res_m: float | None


def tile_name(lat: float, lon: float) -> str:
    west = math.floor(lon)
    hemi = "w" if west < 0 else "e"
    return f"n{math.floor(lat) + 1:02d}{hemi}{abs(west):03d}"


def tile_url(lat: float, lon: float, source: str) -> str:
    folder, _ = RES[source]
    name = tile_name(lat, lon)
    return f"{TILE_BASE}/{folder}/TIFF/current/{name}/USGS_{folder}_{name}.tif"


def _exists(url: str, *, transport: httpx.BaseTransport | None = None) -> bool:
    try:
        with httpx.Client(transport=transport, timeout=20.0, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            status = client.head(url).status_code
    except httpx.HTTPError as exc:
        raise TransientTileError(type(exc).__name__) from None
    if status == 200:
        return True
    if status in MISSING_STATUS:
        return False
    raise TransientTileError(f"HTTP {status}")


@contextmanager
def _opened(url: str) -> Iterator[DatasetReader]:
    with rasterio.Env(**GDAL_ENV):
        try:
            src = rasterio.open(f"/vsicurl/{url}")
        except RasterioIOError as exc:
            raise TransientTileError(str(exc)[:200]) from None
        with src:
            yield src


def open_remote(url: str) -> AbstractContextManager[DatasetReader] | None:
    return _opened(url) if _exists(url) else None


def _sample(src: DatasetReader, points: Sequence[tuple[float, float]]) -> list[float | None]:
    # Points are WGS84 but are used as EPSG:4269 (NAD83) unprojected on purpose: the shift is sub-metre.
    left, bottom, right, top = src.bounds
    coords = [(lon, lat) for lat, lon in points]
    out: list[float | None] = []
    for (lon, lat), value in zip(coords, src.sample(coords, masked=True)):
        if not (left <= lon <= right and bottom <= lat <= top) or np.ma.is_masked(value[0]):  # type: ignore[no-untyped-call]  # numpy.ma stubs are unannotated
            out.append(None)
            continue
        v = float(value[0])
        out.append(v if math.isfinite(v) and v > LOW_SENTINEL else None)
    return out


def sample_points(points: Sequence[tuple[float, float]], opener: Opener = open_remote) -> list[Elevation | None]:
    result: list[Elevation | None] = [None] * len(points)
    by_tile: dict[str, list[int]] = defaultdict(list)
    for i, (lat, lon) in enumerate(points):
        by_tile[tile_name(lat, lon)].append(i)
    for indices in by_tile.values():
        lat0, lon0 = points[indices[0]]
        # Per point: None = no tile read yet, "nodata" = a tile was read but is masked there.
        found: dict[int, Elevation] = {}
        any_tile = False
        try:
            for source in ("3dep_13", "3dep_2"):
                todo = [i for i in indices if i not in found]
                if not todo:
                    break
                handle = opener(tile_url(lat0, lon0, source))
                if handle is None:
                    continue
                any_tile = True
                with handle as src:
                    values = _sample(src, [points[i] for i in todo])
                _, res_m = RES[source]
                for i, v in zip(todo, values):
                    if v is not None:
                        found[i] = Elevation(v, source, res_m)
        except (TransientTileError, RasterioIOError):
            for i in indices:
                result[i] = None
            continue
        for i in indices:
            result[i] = found.get(i) or Elevation(None, "nodata" if any_tile else "no_tile", None)
    return result


def read_window(
    lat: float, lon: float, radius_m: float, opener: Opener = open_remote
) -> tuple[NDArray[np.float64], float, float, tuple[int, int]] | None:
    """A DEM window around a point (masked and fill cells as NaN), its cell size in meters,
    and the point's (row, col). None when no tile exists; TransientTileError otherwise."""
    for source in ("3dep_13", "3dep_2"):
        handle = opener(tile_url(lat, lon, source))
        if handle is None:
            continue
        # WGS84 lon/lat used as EPSG:4269 without reprojection on purpose: the shift is sub-metre.
        try:
            with handle as src:
                row, col = src.index(lon, lat)
                res_x, res_y = abs(src.res[0]), abs(src.res[1])
                dx = res_x * 111_320.0 * math.cos(math.radians(lat))
                dy = res_y * 110_574.0
                half_r, half_c = int(radius_m / dy) + 2, int(radius_m / dx) + 2
                win = rasterio.windows.Window(col - half_c, row - half_r, 2 * half_c + 1, 2 * half_r + 1)
                raw = src.read(1, window=win, boundless=True, masked=True)
                z = np.ma.filled(raw.astype(np.float64), np.nan)  # type: ignore[no-untyped-call]
                z[~np.isfinite(z) | (z <= LOW_SENTINEL)] = np.nan
                return z, dx, dy, (half_r, half_c)
        except RasterioIOError as exc:
            raise TransientTileError(str(exc)[:200]) from None
    return None
