import dataclasses
import time

import numpy as np
import pytest

from app.ml.cells import TYPE_GROUPS
from scipy.special import gammaln, polygamma
from scipy.stats import gamma as gamma_dist
from scipy.stats import nbinom

from app.ml.eb import KAPPA_MAX, MIN_PRIOR_SHAPE, EbFit, _level, fit_eb, ml_kappa, nb_loglik
from app.ml.fixtures import synthetic_cells


def _mom_kappa(obs, E, p):
    """The pre-rework method-of-moments estimator, kept only as the simulation's comparison."""
    s2 = float(np.sum(E * (obs / E - p) ** 2) / E.sum())
    pbar = float(np.sum(E * p) / E.sum())
    tau2 = s2 - pbar / float(E.mean())
    return KAPPA_MAX if tau2 <= 0 else min(pbar / tau2, KAPPA_MAX)


def test_nb_loglik_matches_scipy_negative_binomial():
    obs, E, p, k = np.array([0.0, 2.0, 7.0]), np.array([0.3, 1.5, 2.0]), np.array([0.8, 1.2, 2.5]), 1.7
    # O ~ NB(n = k p, success prob k / (k + E)); nb_loglik drops the kappa-free -log O! term.
    ref = nbinom.logpmf(obs, k * p, k / (k + E)) + gammaln(obs + 1)
    assert nb_loglik(k, obs, E, p) == pytest.approx(float(ref.sum()), rel=1e-12)


def test_ml_kappa_is_an_interior_maximum_on_overdispersed_counts():
    obs, E, p = np.array([0.0, 9.0, 0.0, 6.0, 1.0]), np.full(5, 2.0), np.full(5, 1.6)
    k = ml_kappa(obs, E, p)
    assert 1e-3 < k < KAPPA_MAX
    for f in (0.97, 1.03):
        assert nb_loglik(k, obs, E, p) >= nb_loglik(k * f, obs, E, p)


def test_ml_kappa_returns_the_finite_bound_when_homogeneity_is_supported():
    E, p = np.array([4.0, 4.0]), np.array([1.0, 1.0])
    assert ml_kappa(np.array([4.0, 4.0]), E, p) == KAPPA_MAX == 1e4
    assert ml_kappa(np.array([3.0]), np.array([1.0]), np.array([1.0])) == KAPPA_MAX  # one group


def test_ml_kappa_zero_parent_raises():
    with pytest.raises(ValueError, match="positive"):
        ml_kappa(np.array([0.0, 3.0]), np.array([1.0, 1.0]), np.zeros(2))


@pytest.mark.parametrize("n_groups", [100, 150])
def test_ml_kappa_rarely_hits_the_bound_when_true_kappa_is_finite(n_groups):
    # Sparse exposure as in the catalog: most groups expect well under one incident. MoM called these
    # homogeneous 40-70% of the time; the marginal likelihood must recover a finite kappa.
    hits = mom_hits = 0
    for seed in range(20):
        rng = np.random.default_rng(seed)
        E = rng.lognormal(-3.0, 2.0, n_groups)
        obs = rng.poisson(E * rng.gamma(1.0, 1.0, n_groups)).astype(float)
        p = np.full(n_groups, obs.sum() / E.sum())
        hits += ml_kappa(obs, E, p) >= KAPPA_MAX
        mom_hits += _mom_kappa(obs, E, p) >= KAPPA_MAX
    assert hits <= 4 < mom_hits


def test_poisson_gamma_posterior_matches_hand_value():
    cells = synthetic_cells(seed=5, n_areas=30)
    mu0 = np.full(cells.n, 0.1)
    y = np.zeros(cells.n)
    y[0] = 3.0
    fit = fit_eb(cells, y, mu0, np.full(len(cells.area_id), -1), blend=False)
    post = fit.cell_posterior(cells, y, mu0, np.full(len(cells.area_id), -1))
    pi = post.prior_mean[0]
    _, v, _, _ = fit.prior(cells, np.full(len(cells.area_id), -1))
    k = max(1.0 / (1.0 / fit.kappa_cell + v[0] / pi), MIN_PRIOR_SHAPE / pi)
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
        _level(keys, np.ones(4), np.ones(4), np.array([1.0, 2.0, 1.0, 1.0]), np.zeros(4))
    with pytest.raises(ValueError, match="varies within"):
        _level(keys, np.ones(4), np.ones(4), np.ones(4), np.array([0.0, 0.0, 0.1, 0.2]))


def test_floor_binds_on_kappa_and_keeps_prior_mean_at_parent():
    # Tiny parent with tiny kappa: a shape floor would read 0.05/kappa, far above the parent.
    keys = np.array([0, 0, 1, 1, 2], dtype=np.int64)
    y = np.array([0.0, 0.0, 40.0, 0.0, 0.0])
    mu0 = np.array([1.0, 1.0, 1.0, 1.0, 0.0])
    level, kappa = _level(keys, y, mu0, np.full(5, 1e-3), np.zeros(5))
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
    level, kappa = _level(keys, y, mu0, parent, np.full(500, 0.01))
    assert set(level) == set(key_set.tolist())
    for k in key_set:
        m = keys == k
        obs, E, shape, rate = level[int(k)]
        kp = max(1.0 / (1.0 / kappa + 0.01 / 0.8), MIN_PRIOR_SHAPE / 0.8)
        assert obs == pytest.approx(y[m].sum()) and E == pytest.approx(mu0[m].sum())
        assert shape == pytest.approx(obs + kp * 0.8) and rate == pytest.approx(E + kp)


def test_level_is_linear_in_cells_at_catalog_scale():
    rng = np.random.default_rng(4)
    n, k = 100_000, 10_000
    keys = rng.integers(0, k, n).astype(np.int64)
    y = rng.poisson(0.05, n).astype(float)
    mu0 = rng.uniform(0.01, 0.1, n)
    t0 = time.perf_counter()
    level, _ = _level(keys, y, mu0, np.full(n, 1.0), np.zeros(n))
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
    def theta(level, key, sh, rt, kappa):
        if key in level:
            return level[key][2], level[key][3]
        m, v = sh / rt, sh / rt ** 2
        kp = max(1.0 / (1.0 / kappa + v / m), MIN_PRIOR_SHAPE / m)
        return kp * m, kp

    nat = (fit.national ** 2 / fit.national_var, fit.national / fit.national_var)  # as (shape, rate)
    prior, var, bgeo = np.empty(cells.n), np.empty(cells.n), np.empty(cells.n)
    for i in range(cells.n):
        a = int(cells.area_idx[i])
        s_sh, s_rt = theta(fit.state, int(cells.state_idx[a]), *nat, fit.kappa_state)
        r_sh, r_rt = theta(fit.region, int(cells.region_idx[a]), s_sh, s_rt, fit.kappa_region)
        a_sh, a_rt = theta(fit.area, a, r_sh, r_rt, fit.kappa_area)
        pg = 1.0 / polygamma(1, a_sh)
        k = int(arch_of_area[a])
        if fit.blend and k >= 0:
            k_sh, k_rt = theta(fit.arch, k * len(TYPE_GROUPS) + int(cells.g[i]), *nat, fit.kappa_arch)
            pa = 1.0 / polygamma(1, k_sh)
        else:
            k_sh, k_rt, pa = 1.0, 1.0, 0.0
        wg, wa = pg / (pg + pa), pa / (pg + pa)
        mg, ma = a_sh / a_rt, k_sh / k_rt
        prior[i] = wg * mg + wa * ma
        var[i] = wg * (mg / a_rt + mg ** 2) + wa * (ma / k_rt + ma ** 2) - prior[i] ** 2
        bgeo[i] = wg
    return prior, var, bgeo


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
    prior, var, bgeo = _reference_prior(fit, cells, arch)
    np.testing.assert_allclose(post.prior_mean, prior, rtol=1e-12)
    np.testing.assert_allclose(post.blend_geo, bgeo, rtol=1e-12)
    kc = np.maximum(1.0 / (1.0 / fit.kappa_cell + var / prior), MIN_PRIOR_SHAPE / prior)
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


def test_held_out_area_inherits_parent_mean_and_carries_parent_variance():
    cells = synthetic_cells(seed=12, n_areas=60)
    rng = np.random.default_rng(0)
    mu0 = rng.lognormal(-3, 1.5, cells.n)
    y = rng.poisson(mu0 * rng.gamma(1.0, 1.0, cells.n)).astype(float)
    arch = np.zeros(len(cells.area_id), dtype=np.int64)
    train = cells.area_idx != 0
    fit = fit_eb(cells, np.where(train, y, 0.0), np.where(train, mu0, 0.0), arch, blend=False)
    r_sh, r_rt = fit.region[int(cells.region_idx[0])][2:]
    m, v = r_sh / r_rt, r_sh / r_rt ** 2
    _, E, shape, rate = fit.area[0]
    assert E == 0.0 and shape / rate == pytest.approx(m, rel=1e-12)
    assert 1.0 / (1.0 / fit.kappa_area + v / m) > MIN_PRIOR_SHAPE / m      # floor not binding here
    assert shape / rate ** 2 == pytest.approx(m / fit.kappa_area + v, rel=1e-9)
    post = fit.cell_posterior(cells, np.zeros(cells.n), mu0, arch)
    held = cells.area_idx == 0
    np.testing.assert_allclose(post.prior_mean[held], m, rtol=1e-12)
    assert np.all(post.post.shape[held] / post.post.rate[held] ** 2 > v * 0.5)


def _five_incident_fit():
    cells = synthetic_cells(seed=12, n_areas=60)
    rng = np.random.default_rng(0)
    mu0 = rng.lognormal(-3, 1.5, cells.n)
    y = rng.poisson(mu0 * rng.gamma(1.0, 1.0, cells.n)).astype(float)
    arch = (np.arange(len(cells.area_id)) % 3).astype(np.int64)
    i = int(np.flatnonzero(cells.area_idx == 5)[0])
    y[i] = 5.0
    return cells, y, mu0, arch, i


def test_route_with_five_local_incidents_moves_well_off_its_area_rate():
    cells, y, mu0, arch, i = _five_incident_fit()
    fit = fit_eb(cells, y, mu0, arch)
    post = fit.cell_posterior(cells, y, mu0, arch)
    area_rate = fit.area[5][2] / fit.area[5][3]
    assert fit.kappa_cell < KAPPA_MAX
    assert post.post.mean[i] > 1.5 * area_rate and post.post.mean[i] > 2.0 * post.prior_mean[i]


def test_zero_incident_interval_narrows_as_parent_exposure_grows():
    # Every other cell sits exactly at one rate, so all kappas hit the bound: the target's width then
    # comes only from how well its area/archetype parents are measured, and must shrink with exposure.
    cells = synthetic_cells(seed=13, n_areas=40)
    arch = (np.arange(len(cells.area_id)) % 3).astype(np.int64)
    i = int(np.flatnonzero(cells.area_idx == 3)[0])
    widths = []
    for f in (0.25, 1.0, 4.0, 16.0, 64.0):
        mu0 = np.full(cells.n, 0.05 * f)
        y = 0.8 * mu0
        mu0[i], y[i] = 0.05, 0.0
        fit = fit_eb(cells, y, mu0, arch)
        post = fit.cell_posterior(cells, y, mu0, arch)
        lo, hi = gamma_dist.ppf([0.05, 0.95], post.post.shape[i], scale=1.0 / post.post.rate[i])
        widths.append(hi / lo)
    assert all(a > b for a, b in zip(widths, widths[1:]))
    assert widths[0] > 1.5                                   # sparse parents: a genuinely wide range


def test_ml_kappa_with_no_events_at_a_level_pools_fully():
    assert ml_kappa(np.zeros(4), np.array([0.1, 2.0, 0.5, 9.0]), np.full(4, 0.7)) == KAPPA_MAX


def test_ml_kappa_falls_back_to_a_grid_when_the_optimiser_fails(monkeypatch, caplog):
    import types

    import app.ml.eb as eb

    obs, E, p = np.array([0.0, 9.0, 0.0, 6.0, 1.0]), np.full(5, 2.0), np.full(5, 1.6)
    good = ml_kappa(obs, E, p)
    monkeypatch.setattr(eb, "minimize_scalar",
                        lambda *a, **k: types.SimpleNamespace(success=False, x=np.nan, message="forced"))
    with caplog.at_level("WARNING", logger="app.ml.eb"):
        k = ml_kappa(obs, E, p)
    assert "grid argmax" in caplog.text
    assert np.isfinite(k) and k == pytest.approx(good, rel=0.1)       # grid step on log kappa is ~0.1


def test_blend_is_a_moment_matched_mixture_not_pulled_to_the_low_mean():
    from app.ml.eb import blend_moments

    sh_g, rt_g, sh_a, rt_a = np.array([1.0]), np.array([6.0]), np.array([31.0]), np.array([31.0])
    mean, var, wg = blend_moments(sh_g, rt_g, sh_a, rt_a, np.array([True]))
    m_g, m_a, v_g, v_a = 1 / 6, 1.0, 1 / 36, 31 / 31 ** 2
    assert m_g < mean[0] < m_a
    # The old inverse-variance blend weighted the low mean by 1/v = 36 vs 31: mean ~0.55.
    old = (36 * m_g + 31 * m_a) / 67
    assert mean[0] > old + 0.3
    assert var[0] >= max(v_g, v_a) and 0 < wg[0] < 0.1
    alone, alone_var, w1 = blend_moments(sh_g, rt_g, sh_a, rt_a, np.array([False]))
    assert alone[0] == pytest.approx(m_g) and alone_var[0] == pytest.approx(v_g) and w1[0] == 1.0


def test_kappa_area_is_finite_with_several_areas_per_region():
    base = synthetic_cells(seed=14, n_areas=120)
    n_areas = len(base.area_id)
    region = (np.arange(n_areas) // 6).astype(np.int64)          # 6 areas per region, 20 regions
    cells = dataclasses.replace(base, region_idx=region, state_idx=region // 5)
    rng = np.random.default_rng(2)
    mu0 = rng.lognormal(-1.5, 1.0, cells.n)
    theta = rng.gamma(1.0, 1.0, n_areas)[cells.area_idx]           # real between-area spread
    y = rng.poisson(mu0 * theta).astype(float)
    fit = fit_eb(cells, y, mu0, np.zeros(n_areas, dtype=np.int64))
    assert fit.kappa_area < KAPPA_MAX
