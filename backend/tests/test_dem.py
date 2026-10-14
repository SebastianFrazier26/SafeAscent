from contextlib import contextmanager

import httpx
import numpy as np
import pytest
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.transform import from_origin

from app.pipelines.dem import (
    GDAL_ENV,
    TransientTileError,
    _exists,
    read_window,
    sample_points,
    tile_name,
    tile_url,
)


def test_tile_naming_uses_the_northwest_corner():
    assert tile_name(40.25, -105.6) == "n41w106"
    assert tile_name(63.07, -151.5) == "n64w152"
    assert tile_url(40.25, -105.6, "3dep_13").endswith("/13/TIFF/current/n41w106/USGS_13_n41w106.tif")
    assert tile_url(63.07, -151.5, "3dep_2").endswith("/2/TIFF/current/n64w152/USGS_2_n64w152.tif")


def test_tile_naming_east_of_180():
    assert tile_name(52.9, 175.3) == "n53e175"
    assert tile_name(51.9, -179.5) == "n52w180"


def _write(path, *, nodata):
    data = np.full((100, 100), 1650.0, dtype="float32")
    data[0:10, 0:10] = nodata if nodata is not None else -3.4028235e38
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=1,
        dtype="float32",
        crs="EPSG:4269",
        transform=from_origin(-106.0, 41.0, 0.01, 0.01),
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture
def tile(tmp_path):
    return _write(tmp_path / "t.tif", nodata=-999999.0)


@pytest.fixture
def tile_without_nodata(tmp_path):
    return _write(tmp_path / "n.tif", nodata=None)


def opener_for(mapping):
    @contextmanager
    def _open(path):
        with rasterio.open(path) as src:
            yield src

    def opener(url):
        for key, path in mapping.items():
            if key in url:
                return _open(path)
        return None

    return opener


def test_samples_elevation(tile):
    [e] = sample_points([(40.5, -105.5)], opener_for({"/13/": tile}))
    assert e is not None and (e.elevation_m, e.dem_source, e.dem_res_m) == (
        1650.0,
        "3dep_13",
        10.0,
    )


def test_nodata_is_null_not_zero(tile):
    [e] = sample_points([(40.95, -105.95)], opener_for({"/13/": tile}))
    assert e is not None and (e.elevation_m, e.dem_source) == (None, "nodata")


def test_tile_without_nodata_never_returns_zero(tile_without_nodata):
    fill, outside, ok = sample_points(
        [(40.95, -105.95), (39.5, -105.5), (40.5, -105.5)],
        opener_for({"/13/": tile_without_nodata}),
    )
    assert fill is not None and (fill.elevation_m, fill.dem_source) == (None, "nodata")
    assert outside is not None and (outside.elevation_m, outside.dem_source) == (
        None,
        "nodata",
    )
    assert ok is not None and ok.elevation_m == 1650.0


# Escalated: float() of a masked element silently yields NaN with a UserWarning, which would
# hide a dropped mask check behind the isfinite() guard.
@pytest.mark.filterwarnings("error::UserWarning")
def test_declared_nodata_above_the_sentinel_is_still_masked(tmp_path):
    # -9999 sits above LOW_SENTINEL, so only the dataset mask can catch it.
    path = _write(tmp_path / "m.tif", nodata=-9999.0)
    [e] = sample_points([(40.95, -105.95)], opener_for({"/13/": path}))
    assert e is not None and (e.elevation_m, e.dem_source) == (None, "nodata")
    out = read_window(40.95, -105.95, 20_000.0, opener_for({"/13/": path}))
    assert out is not None and np.isnan(out[0][out[3]]) and np.nanmin(out[0]) == 1650.0


def test_falls_back_to_2_arcsecond(tile):
    [e] = sample_points([(40.5, -105.5)], opener_for({"/2/": tile}))
    assert e is not None and (e.dem_source, e.dem_res_m) == ("3dep_2", 60.0)


def test_no_tile_at_all():
    [e] = sample_points([(40.5, -105.5)], lambda url: None)
    assert e is not None and (e.elevation_m, e.dem_source, e.dem_res_m) == (
        None,
        "no_tile",
        None,
    )


def test_transient_tile_error_leaves_points_unfilled(tile):
    def flaky(url):
        raise TransientTileError("HTTP 503")

    assert sample_points([(40.5, -105.5), (40.6, -105.4)], flaky) == [None, None]


def test_transient_on_one_third_does_not_fall_back_to_2_arcsecond(tile):
    two = opener_for({"/2/": tile})

    def opener(url):
        if "/13/" in url:
            raise TransientTileError("HTTP 503")
        return two(url)

    assert sample_points([(40.5, -105.5)], opener) == [None]


def test_transient_tile_only_affects_its_own_points(tile):
    good = opener_for({"/13/": tile})

    def opener(url):
        if "n53e175" in url:
            raise TransientTileError("timeout")
        return good(url)

    here, there = sample_points([(40.5, -105.5), (52.9, 175.3)], opener)
    assert here is not None and here.elevation_m == 1650.0
    assert there is None


def _head(status=None, exc=None):
    def handler(request):
        assert request.method == "HEAD"
        if exc is not None:
            raise exc
        return httpx.Response(status)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize("status", [403, 404])
def test_missing_key_is_does_not_exist(status):
    assert _exists("https://example.invalid/t.tif", transport=_head(status)) is False


def test_200_exists():
    assert _exists("https://example.invalid/t.tif", transport=_head(200)) is True


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_server_error_is_transient_not_missing(status):
    with pytest.raises(TransientTileError):
        _exists("https://example.invalid/t.tif", transport=_head(status))


def test_timeout_is_transient_not_missing():
    with pytest.raises(TransientTileError):
        _exists(
            "https://example.invalid/t.tif",
            transport=_head(exc=httpx.ReadTimeout("slow")),
        )


def test_read_window_masks_nodata_as_nan(tile):
    out = read_window(40.95, -105.95, 20_000.0, opener_for({"/13/": tile}))
    assert out is not None
    z, dx, dy, (r, c) = out
    assert np.isnan(z[r, c])
    assert np.nanmax(z) == 1650.0
    assert not (z == 0).any()
    assert dx > 0 and dy > 0


def test_read_window_no_tile():
    assert read_window(40.5, -105.5, 500.0, lambda url: None) is None


def test_read_window_falls_back_to_2_arcsecond(tile):
    out = read_window(40.5, -105.5, 500.0, opener_for({"/2/": tile}))
    assert out is not None and np.nanmax(out[0]) == 1650.0


def test_read_window_transient_raises(tile):
    def flaky(url):
        raise TransientTileError("HTTP 503")

    with pytest.raises(TransientTileError):
        read_window(40.5, -105.5, 500.0, flaky)


class _ReadFails:
    def __init__(self, src):
        self._src = src

    def __getattr__(self, name):
        return getattr(self._src, name)

    def read(self, *args, **kwargs):
        raise RasterioIOError("CURL error: Connection reset by peer")


def test_read_window_mid_read_error_is_transient_and_skips_2_arcsecond(tile):
    tried = []

    @contextmanager
    def broken(path):
        with rasterio.open(path) as src:
            yield _ReadFails(src)

    def opener(url):
        tried.append(url)
        return broken(tile)

    with pytest.raises(TransientTileError):
        read_window(40.5, -105.5, 500.0, opener)
    assert len(tried) == 1 and "/13/" in tried[0]


def test_gdal_http_timeouts_are_bounded():
    assert int(GDAL_ENV["GDAL_HTTP_TIMEOUT"]) > 0
    assert int(GDAL_ENV["GDAL_HTTP_CONNECTTIMEOUT"]) > 0
