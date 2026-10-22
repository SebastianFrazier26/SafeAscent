import dataclasses

import numpy as np
import pytest

from app.ml.archetypes import (
    ARCH_FEATURES,
    Standardizer,
    area_feature_matrix,
    kmeans,
    nearest_distance,
    weighted_sqdist,
)
from app.ml.dataset import STATIC_FEATURES
from app.ml.fixtures import synthetic_cells


def test_partial_distance_skips_missing_and_rescales():
    a = np.array([[0.0, np.nan, 2.0, 3.0]])
    b = np.array([[1.0, 5.0, 2.0, 3.0]])
    d = weighted_sqdist(a, b, np.ones(4))
    assert d[0, 0] == pytest.approx(1.0 * 4 / 3)
    assert np.isnan(weighted_sqdist(np.array([[np.nan, 1.0]]), np.array([[1.0, np.nan]]), np.ones(2))[0, 0])


def test_rescaling_uses_shared_weights():
    a = np.array([[0.0, np.nan, 0.0, 0.0]])
    b = np.array([[2.0, 9.0, 0.0, 0.0]])
    w = np.array([1.0, 3.0, 1.0, 1.0])
    assert weighted_sqdist(a, b, w)[0, 0] == pytest.approx(4.0 * 6 / 3)


def test_too_few_shared_features_is_no_evidence():  # pre-flight F67
    a = np.array([[0.0, np.nan, np.nan, 3.0]])
    b = np.array([[0.0, 5.0, 2.0, 3.0]])
    assert np.isnan(weighted_sqdist(a, b, np.ones(4))[0, 0])
    assert np.isinf(nearest_distance(a, b, np.ones(4))[0])


def test_kmeans_is_deterministic_and_separates_clusters():
    rng = np.random.default_rng(0)
    Z = np.vstack([rng.normal(0, 0.1, (50, 2)), rng.normal(5, 0.1, (50, 2))])
    l1, c1 = kmeans(Z, 2, np.ones(2), seed=0)
    l2, c2 = kmeans(Z, 2, np.ones(2), seed=0)
    np.testing.assert_array_equal(l1, l2)
    np.testing.assert_array_equal(c1, c2)
    assert len(set(l1[:50])) == 1 and len(set(l1[50:])) == 1 and l1[0] != l1[-1]


def test_kmeans_seed_changes_init_but_same_seed_repeats():
    rng = np.random.default_rng(1)
    Z = rng.normal(0, 1, (200, 4))
    runs = [kmeans(Z, 6, np.ones(4), seed=s, iters=1)[0] for s in (0, 0, 1)]
    np.testing.assert_array_equal(runs[0], runs[1])
    assert not np.array_equal(runs[0], runs[2])


def test_all_missing_row_has_no_archetype():
    Z = np.array([[0.0, 0.0], [np.nan, np.nan], [1.0, 1.0]])
    labels, _ = kmeans(Z, 2, np.ones(2), seed=0)
    assert labels[1] == -1


def test_row_with_too_few_features_has_no_archetype():
    Z = np.array([[0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0], [np.nan, np.nan, np.nan, 2.0]])
    labels, _ = kmeans(Z, 2, np.ones(4), seed=0)
    assert labels[2] == -1 and labels[0] >= 0 and labels[1] >= 0


def test_nearest_distance_inf_without_evidence():
    q = np.array([[0.0, 0.0], [3.0, 4.0]])
    assert np.all(np.isinf(nearest_distance(q, np.empty((0, 2)), np.ones(2))))
    np.testing.assert_allclose(nearest_distance(q, np.array([[0.0, 0.0]]), np.ones(2)), [0.0, 5.0])


def test_nearest_distance_chunking_matches_unchunked():
    rng = np.random.default_rng(2)
    q, ev = rng.normal(size=(37, 5)), rng.normal(size=(11, 5))
    q[rng.random(q.shape) < 0.2] = np.nan
    np.testing.assert_array_equal(nearest_distance(q, ev, np.ones(5), chunk=4),
                                  nearest_distance(q, ev, np.ones(5)))


def test_standardizer_ignores_missing_and_all_nan_column():
    F = np.array([[1.0, np.nan, 5.0], [3.0, np.nan, 5.0], [np.nan, np.nan, 5.0]])
    s = Standardizer.fit(F)
    np.testing.assert_allclose(s.mean, [2.0, 0.0, 5.0])
    np.testing.assert_allclose(s.sd, [1.0, 1.0, 1.0])
    Z = s.apply(F)
    np.testing.assert_allclose(Z[:2, 0], [-1.0, 1.0])
    assert np.isnan(Z[2, 0]) and np.isnan(Z[:, 1]).all()


def test_area_feature_matrix_columns_and_missing_month():
    cells = synthetic_cells(seed=3, n_areas=4)
    s = np.zeros((4, 12, len(STATIC_FEATURES)))
    j = {f: i for i, f in enumerate(STATIC_FEATURES)}
    s[:, :, j["elevation_m"]] = 3000.0
    s[:, :, j["lat"]] = 40.0
    s[:, :, j["tmax_mean"]] = np.arange(12)
    s[:, :, j["tmin_mean"]] = -np.arange(12) - 1
    s[:, :, j["precip_mm"]] = 10.0
    s[:, :, j["freeze_thaw_days"]] = 2.0
    s[1, 0, :] = np.nan
    F = area_feature_matrix(dataclasses.replace(cells, area_static=s))
    assert F.shape == (4, len(ARCH_FEATURES))
    np.testing.assert_allclose(F[0], [3000.0, 40.0, 6.0, -1.0, np.log1p(120.0), 0.0, 24.0])
    np.testing.assert_allclose(F[1, :3], [3000.0, 40.0, 6.0])
    assert np.isnan(F[1, 3:]).all()
