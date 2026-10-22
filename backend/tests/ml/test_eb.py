import dataclasses
import time

import numpy as np
import pytest

from app.ml.cells import TYPE_GROUPS
from app.ml.eb import KAPPA_MAX, MIN_PRIOR_SHAPE, EbFit, _level, fit_eb, mom_kappa
from app.ml.fixtures import synthetic_cells


def test_mom_kappa_hand_computed():
    O, E, p = np.array([2.0, 8.0]), np.array([4.0, 4.0]), np.array([1.0, 1.0])
    s2 = (4 * (0.5 - 1) ** 2 + 4 * (2 - 1) ** 2) / 8
    assert mom_kappa(O, E, p) == pytest.approx(1.0 / (s2 - 1.0 / 4.0))
    assert mom_kappa(np.array([4.0, 4.0]), E, p) == 1e6          # no extra variance: full pooling


def test_mom_kappa_zero_parent_pools_fully_instead_of_kappa_zero():
    assert mom_kappa(np.array([0.0, 3.0]), np.array([1.0, 1.0]), np.zeros(2)) == KAPPA_MAX


def test_poisson_gamma_posterior_matches_hand_value():
    cells = synthetic_cells(seed=5, n_areas=30)
    mu0 = np.full(cells.n, 0.1)
    y = np.zeros(cells.n)
    y[0] = 3.0
    fit = fit_eb(cells, y, mu0, np.full(len(cells.area_id), -1), blend=False)
    post = fit.cell_posterior(cells, y, mu0, np.full(len(cells.area_id), -1))
    k, pi = fit.kappa_cell, post.prior_mean[0]
    assert post.post.mean[0] == pytest.approx((3.0 + max(k * pi, MIN_PRIOR_SHAPE)) / (0.1 + k))


def test_zero_incident_cell_shrinks_to_parent_not_zero():
    cells = synthetic_cells(seed=6, n_areas=30)
    mu0 = np.full(cells.n, 0.2)
    y = np.random.default_rng(0).poisson(0.2, cells.n).astype(float)
    y[cells.area_idx == 0] = 0.0
    fit = fit_eb(cells, y, mu0, np.zeros(len(cells.area_id), dtype=np.int64))
    post = fit.cell_posterior(cells, y, mu0, np.zeros(len(cells.area_id), dtype=np.int64))
    zero = cells.area_idx == 0
    assert np.all(post.post.mean[zero] > 0) and np.all(post.post.shape >= MIN_PRIOR_SHAPE)


def test_zero_local_accident_cell_borrows_a_material_prior():
    cells = synthetic_cells(seed=6, n_areas=30)
    mu0 = np.full(cells.n, 0.2)
    y = np.random.default_rng(0).poisson(0.5, cells.n).astype(float)
    y[cells.area_idx == 0] = 0.0
    arch = np.zeros(len(cells.area_id), dtype=np.int64)
    fit = fit_eb(cells, y, mu0, arch)
    post = fit.cell_posterior(cells, y, mu0, arch)
    zero = cells.area_idx == 0
    # The borrowed prior is a sizable share of the national ratio, not a near-zero floor.
    assert np.all(post.prior_mean[zero] > 0.1 * fit.national)
    assert np.all(post.post.mean[zero] > 0.1 * fit.national)


def test_no_events_anywhere_floors_shape_and_stays_positive():
    cells = synthetic_cells(seed=8, n_areas=20)
    mu0 = np.full(cells.n, 0.3)
    y = np.zeros(cells.n)
    arch = np.zeros(len(cells.area_id), dtype=np.int64)
    fit = fit_eb(cells, y, mu0, arch)
    post = fit.cell_posterior(cells, y, mu0, arch)
    assert fit.national == 0.0
    np.testing.assert_array_equal(post.post.shape, MIN_PRIOR_SHAPE)
    assert np.all(np.isfinite(post.post.mean)) and np.all(post.post.mean > 0)
    assert all(v[2] == MIN_PRIOR_SHAPE for lvl in (fit.state, fit.region, fit.area, fit.arch) for v in lvl.values())


def test_held_out_area_inherits_parent_and_archetype():
    cells = synthetic_cells(seed=7, n_areas=30)
    mu0 = np.full(cells.n, 0.2)
    arch = np.zeros(len(cells.area_id), dtype=np.int64)
    y = np.zeros(cells.n)
    y[cells.area_idx == 1] = 1.0                       # evidence only in area 1 (same archetype)
    train = cells.area_idx != 0
    fit = fit_eb(cells, np.where(train, y, 0.0), np.where(train, mu0, 0.0), arch)
    post = fit.cell_posterior(cells, np.zeros(cells.n), mu0, arch)
    assert np.all(post.post.mean[cells.area_idx == 0] > 0)
    geo_only = fit_eb(cells, np.where(train, y, 0.0), np.where(train, mu0, 0.0), arch, blend=False)
    g = geo_only.cell_posterior(cells, np.zeros(cells.n), mu0, arch)
    assert not np.allclose(post.prior_mean, g.prior_mean)


def test_unlabelled_archetype_is_never_indexed():
    cells = synthetic_cells(seed=9, n_areas=30)
    rng = np.random.default_rng(1)
    mu0 = np.full(cells.n, 0.2)
    y = rng.poisson(0.3, cells.n).astype(float)
    arch = rng.integers(0, 3, len(cells.area_id))
    arch[::4] = -1
    fit = fit_eb(cells, y, mu0, arch)
    post = fit.cell_posterior(cells, y, mu0, arch)
    unlabelled = arch[cells.area_idx] < 0
    assert all(k >= 0 for k in fit.arch)
    np.testing.assert_array_equal(post.blend_geo[unlabelled], 1.0)
    np.testing.assert_array_equal(post.chain["O_arch"][unlabelled], 0.0)
    assert np.all(post.blend_geo[~unlabelled] < 1.0)


def test_level_groups_match_brute_force_on_sparse_keys():
    rng = np.random.default_rng(3)
    key_set = np.array([7, 40, 41, 1000, 99_999])
    keys = rng.choice(key_set, 500).astype(np.int64)
    keys[:20] = -1
    y = rng.poisson(1.0, 500).astype(float)
    mu0 = rng.uniform(0.1, 2.0, 500)
    parent = np.full(500, 0.8)
    level, kappa = _level(keys, y, mu0, parent)
    assert set(level) == set(key_set.tolist())
    for k in key_set:
        m = keys == k
        O, E, shape, rate = level[int(k)]
        assert O == pytest.approx(y[m].sum()) and E == pytest.approx(mu0[m].sum())
        assert shape == pytest.approx(O + max(kappa * 0.8, MIN_PRIOR_SHAPE)) and rate == pytest.approx(E + kappa)


def test_level_is_linear_in_cells_at_catalog_scale():
    rng = np.random.default_rng(4)
    n, k = 100_000, 10_000
    keys = rng.integers(0, k, n).astype(np.int64)
    y = rng.poisson(0.05, n).astype(float)
    mu0 = rng.uniform(0.01, 0.1, n)
    t0 = time.perf_counter()
    level, _ = _level(keys, y, mu0, np.full(n, 1.0))
    assert time.perf_counter() - t0 < 5.0
    assert len(level) == np.unique(keys).size


def test_fit_and_posterior_at_catalog_scale():
    base = synthetic_cells(seed=10, n_areas=8)
    rng = np.random.default_rng(5)
    n, n_areas = 100_000, 10_000
    cells = dataclasses.replace(
        base,
        area_idx=rng.integers(0, n_areas, n).astype(np.int64),
        g=rng.integers(0, len(TYPE_GROUPS), n).astype(np.int8),
        region_idx=rng.integers(0, 500, n_areas).astype(np.int64),
        state_idx=rng.integers(0, 40, n_areas).astype(np.int64),
        area_id=np.arange(n_areas, dtype=np.int64) + 900_000_000,
    )
    y = rng.poisson(0.05, n).astype(float)
    mu0 = rng.uniform(0.01, 0.1, n)
    arch = rng.integers(-1, 12, n_areas)
    t0 = time.perf_counter()
    fit = fit_eb(cells, y, mu0, arch)
    post = fit.cell_posterior(cells, y, mu0, arch)
    assert time.perf_counter() - t0 < 20.0
    assert np.all(np.isfinite(post.post.mean)) and np.all(post.post.mean > 0)


def _reference_prior(fit: EbFit, cells, arch_of_area):
    """Straightforward per-cell walk of the brief's dict-based algorithm."""
    def theta(level, key, parent, kappa):
        if key in level:
            return level[key][2], level[key][3]
        return max(kappa * parent, MIN_PRIOR_SHAPE), kappa

    prior, bgeo = np.empty(cells.n), np.empty(cells.n)
    for i in range(cells.n):
        a = int(cells.area_idx[i])
        s_sh, s_rt = theta(fit.state, int(cells.state_idx[a]), fit.national, fit.kappa_state)
        r_sh, r_rt = theta(fit.region, int(cells.region_idx[a]), s_sh / s_rt, fit.kappa_region)
        a_sh, a_rt = theta(fit.area, a, r_sh / r_rt, fit.kappa_area)
        wg = a_rt * a_rt / a_sh
        k = int(arch_of_area[a])
        if fit.blend and k >= 0:
            k_sh, k_rt = theta(fit.arch, k * len(TYPE_GROUPS) + int(cells.g[i]), fit.national, fit.kappa_arch)
            wa = k_rt * k_rt / k_sh
        else:
            k_sh, k_rt, wa = 1.0, 1.0, 0.0
        prior[i] = (wg * a_sh / a_rt + wa * k_sh / k_rt) / (wg + wa)
        bgeo[i] = wg / (wg + wa)
    return prior, bgeo


@pytest.mark.parametrize("blend", [True, False])
def test_vectorized_posterior_matches_per_cell_reference(blend):
    cells = synthetic_cells(seed=11, n_areas=10)
    assert 150 <= cells.n
    rng = np.random.default_rng(6)
    mu0 = rng.uniform(0.05, 0.5, cells.n)
    y = rng.poisson(mu0 * rng.gamma(2.0, 0.5, cells.n)).astype(float)
    arch = rng.integers(-1, 3, len(cells.area_id))
    train = cells.area_idx != 2                        # area 2 held out: absent keys inherit
    fit = fit_eb(cells, np.where(train, y, 0.0), np.where(train, mu0, 0.0), arch, blend=blend)
    post = fit.cell_posterior(cells, np.where(train, y, 0.0), mu0, arch)
    prior, bgeo = _reference_prior(fit, cells, arch)
    np.testing.assert_allclose(post.prior_mean, prior, rtol=1e-12)
    np.testing.assert_allclose(post.blend_geo, bgeo, rtol=1e-12)
    np.testing.assert_allclose(post.post.shape, np.where(train, y, 0.0)
                               + np.maximum(fit.kappa_cell * prior, MIN_PRIOR_SHAPE), rtol=1e-12)


def test_posteriors_finite_positive_and_carry_y_own():
    for seed in range(5):
        cells = synthetic_cells(seed=20 + seed, n_areas=25)
        rng = np.random.default_rng(seed)
        mu0 = rng.uniform(0.0, 0.4, cells.n)
        y = rng.poisson(0.2, cells.n).astype(float)
        arch = rng.integers(-1, 4, len(cells.area_id))
        fit = fit_eb(cells, y, mu0, arch)
        post = fit.cell_posterior(cells, y, mu0, arch)
        for arr in (post.post.mean, post.prior_mean, post.post.shape, post.post.rate):
            assert np.all(np.isfinite(arr)) and np.all(arr > 0)
        assert post.y_own is y
        assert np.all(post.post.shape >= MIN_PRIOR_SHAPE)
        for name, arr in post.chain.items():
            assert np.all(np.isfinite(arr)), name
