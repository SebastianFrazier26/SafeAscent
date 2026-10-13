import numpy as np
import pytest

from app.pipelines.geo import haversine_km, haversine_km_many


def test_one_degree_of_latitude():
    assert haversine_km(40.0, -105.0, 41.0, -105.0) == pytest.approx(111.19, abs=0.05)


def test_vectorized_matches_scalar():
    lats, lons = np.array([41.0, 40.0]), np.array([-105.0, -104.0])
    out = haversine_km_many(40.0, -105.0, lats, lons)
    assert out[0] == pytest.approx(haversine_km(40.0, -105.0, 41.0, -105.0))
    assert out[1] == pytest.approx(haversine_km(40.0, -105.0, 40.0, -104.0))


def test_identical_points_are_zero_distance():
    assert haversine_km(40.0, -105.0, 40.0, -105.0) == pytest.approx(0.0, abs=1e-9)


def test_antipodal_points_are_half_earth_circumference():
    assert haversine_km(0.0, 0.0, 0.0, 180.0) == pytest.approx(
        6371.0088 * 3.141592653589793, abs=0.01
    )


def test_haversine_km_many_empty_arrays():
    out = haversine_km_many(40.0, -105.0, np.array([]), np.array([]))
    assert out.shape == (0,)


def test_haversine_km_many_identical_point_repeated():
    lats, lons = np.array([40.0, 40.0]), np.array([-105.0, -105.0])
    out = haversine_km_many(40.0, -105.0, lats, lons)
    assert out[0] == pytest.approx(0.0, abs=1e-9)
    assert out[1] == pytest.approx(0.0, abs=1e-9)
