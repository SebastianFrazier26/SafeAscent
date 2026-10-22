import dataclasses
import time

import numpy as np
import pytest

from app.ml.cells import TYPE_GROUPS
from app.ml.eb import KAPPA_MAX, MIN_PRIOR_SHAPE, EbFit, _level, fit_eb, mom_kappa
from app.ml.fixtures import synthetic_cells


def test_mom_kappa_hand_computed():
    obs, E, p = np.array([2.0, 8.0]), np.array([4.0, 4.0]), np.array([1.0, 1.0])
    s2 = (4 * (0.5 - 1) ** 2 + 4 * (2 - 1) ** 2) / 8
    assert mom_kappa(obs, E, p) == pytest.approx(1.0 / (s2 - 1.0 / 4.0))
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
    pi = post.prior_mean[0]
    k = max(fit.kappa_cell, MIN_PRIOR_SHAPE / pi)
    assert post.post.mean[0] == pytest.approx((3.0 + k * pi) / (0.1 + k))


def test_zero_incident_cell_shrinks_to_parent_not_zero():
    cells = synthetic_cells(seed=6, n_areas=30)
    mu0 = np.full(cells.n, 0.2)
    y = np.random.default_rng(0).poisson(0.2, cells.n).astype(float)
    y[cells.area_idx == 0] = 0.0
    fit = fit_eb(cells, y, mu0, np.zeros(len(cells.area_id), dtype=np.int64))
    post = fit.cell_posterior(cells, y, mu0, np.zeros(len(cells.area_id), dtype=np.int64))
    zero = cells.area_idx == 0
    assert np.all(post.post.mean[zero] > 0) and np.all(post.post.shape >= MIN_PRIOR_SHAPE * (1 - 1e-12))


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


def test_no_events_anywhere_is_refused_not_scored_zero():
    cells = synthetic_cells(seed=8, n_areas=20)
    arch = np.zeros(len(cells.area_id), dtype=np.int64)
    with pytest.raises(ValueError, match="at least one event"):
        fit_eb(cells, np.zeros(cells.n), np.full(cells.n, 0.3), arch)
    with pytest.raises(ValueError, match="positive total exposure"):
        fit_eb(cells, np.ones(cells.n), np.zeros(cells.n), arch)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -1.0])
@pytest.mark.parametrize("which", ["y", "mu0"])
def test_non_finite_or_negative_inputs_raise(bad, which):
    cells = synthetic_cells(seed=8, n_areas=20)
    arch = np.zeros(len(cells.area_id), dtype=np.int64)
    y, mu0 = np.ones(cells.n), np.full(cells.n, 0.3)
    (y if which == "y" else mu0)[3] = bad
    with pytest.raises(ValueError, match="finite and non-negative"):
        fit_eb(cells, y, mu0, arch)
    fit = fit_eb(cells, np.ones(cells.n), np.full(cells.n, 0.3), arch)
    with pytest.raises(ValueError, match="finite and non-negative"):
        fit.cell_posterior(cells, y, mu0, arch)


def test_parent_must_be_constant_within_group():
    keys = np.array([0, 0, 1, 1], dtype=np.int64)
    with pytest.raises(ValueError, match="varies within"):
        _level(keys, np.ones(4), np.ones(4), np.array([1.0, 2.0, 1.0, 1.0]))


def test_floor_binds_on_kappa_and_keeps_prior_mean_at_parent():
    # Tiny parent with tiny kappa: a shape floor would read 0.05/kappa, far above the parent.
    keys = np.array([0, 0, 1, 1, 2], dtype=np.int64)
    y = np.array([0.0, 0.0, 40.0, 0.0, 0.0])
    mu0 = np.array([1.0, 1.0, 1.0, 1.0, 0.0])
    level, kappa = _level(keys, y, mu0, np.full(5, 1e-3))
    assert kappa * 1e-3 < MIN_PRIOR_SHAPE
    _, E, shape, rate = level[2]
    assert E == 0.0 and shape == pytest.approx(MIN_PRIOR_SHAPE) and shape / rate == pytest.approx(1e-3)


def test_held_out_area_prior_equals_parent_mean():
    # Fixture regions are one area each, so the held-out area's region is held out too: its prior
    # must walk state -> region -> area with the mean unchanged, even where the shape floor binds.
    cells = synthetic_cells(seed=7, n_areas=120)
    mu0 = np.full(cells.n, 0.2)
    arch = np.zeros(len(cells.area_id), dtype=np.int64)
    y = np.zeros(cells.n)
    y[np.flatnonzero(cells.area_idx == 4)[:3]] = 30.0  # one hot cluster, same state as area 0
    train = cells.area_idx != 0
    fit = fit_eb(cells, np.where(train, y, 0.0), np.where(train, mu0, 0.0), arch, blend=False)
    post = fit.cell_posterior(cells, np.zeros(cells.n), mu0, arch)
    held = cells.area_idx == 0
    s = int(cells.state_idx[0])
    state_mean = fit.state[s][2] / fit.state[s][3]
    assert fit.kappa_region * state_mean < MIN_PRIOR_SHAPE  # the floor binds on this path
    np.testing.assert_allclose(post.prior_mean[held], state_mean, rtol=1e-12)
    for level, key in ((fit.region, int(cells.region_idx[0])), (fit.area, 0)):
        _, E, shape, rate = level[key]
        assert E == 0.0 and shape / rate == pytest.approx(state_mean, rel=1e-12)
    assert fit.region[int(cells.region_idx[0])][2] == pytest.approx(MIN_PRIOR_SHAPE)


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
        obs, E, shape, rate = level[int(k)]
        kp = max(kappa, MIN_PRIOR_SHAPE / 0.8)
        assert obs == pytest.approx(y[m].sum()) and E == pytest.approx(mu0[m].sum())
        assert shape == pytest.approx(obs + kp * 0.8) and rate == pytest.approx(E + kp)


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
        region_idx=(region := rng.integers(0, 500, n_areas).astype(np.int64)),
        state_idx=region % 40,                         # regions nest in states, as MP paths do
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
        kp = max(kappa, MIN_PRIOR_SHAPE / parent)
        return kp * parent, kp

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
    kc = np.maximum(fit.kappa_cell, MIN_PRIOR_SHAPE / prior)
    np.testing.assert_allclose(post.post.shape, np.where(train, y, 0.0) + kc * prior, rtol=1e-12)
    np.testing.assert_allclose(post.post.rate, mu0 + kc, rtol=1e-12)


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
        assert np.all(post.post.shape >= MIN_PRIOR_SHAPE * (1 - 1e-12))
        for name, arr in post.chain.items():
            assert np.all(np.isfinite(arr)), name
