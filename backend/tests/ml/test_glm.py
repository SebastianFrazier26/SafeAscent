import numpy as np
import pytest

from app.ml.glm import fit_poisson, linear_predictor


def _mm(a, b):
    # numpy 2.2 on Apple Accelerate flags spurious divide/over/invalid on finite matmul (results are correct).
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        return a @ b


def test_intercept_only_recovers_the_mean():
    y = np.array([0.0, 1.0, 2.0, 5.0])
    fit = fit_poisson(np.ones((4, 1)), y, np.zeros(1))
    assert fit.converged and np.exp(fit.coef[0]) == pytest.approx(2.0, rel=1e-8)


def test_recovers_known_coefficients():
    rng = np.random.default_rng(0)
    X = np.column_stack([np.ones(20000), rng.normal(size=20000), rng.integers(0, 2, 20000)])
    beta = np.array([-1.0, 0.4, -0.7])
    y = rng.poisson(np.exp(_mm(X, beta))).astype(float)
    fit = fit_poisson(X, y, np.zeros(3))
    np.testing.assert_allclose(fit.coef, beta, atol=0.05)


def test_ridge_shrinks_a_penalized_column_toward_zero():
    rng = np.random.default_rng(1)
    X = np.column_stack([np.ones(200), rng.integers(0, 2, 200)])
    y = rng.poisson(np.exp(0.5 + 1.0 * X[:, 1])).astype(float)
    free = fit_poisson(X, y, np.array([0.0, 0.0])).coef[1]
    tight = fit_poisson(X, y, np.array([0.0, 1e4])).coef[1]
    assert abs(tight) < 0.05 < abs(free)


def test_nonneg_column_is_fixed_at_zero_when_the_data_want_it_negative():
    rng = np.random.default_rng(2)
    X = np.column_stack([np.ones(5000), rng.normal(size=5000)])
    y = rng.poisson(np.exp(0.2 - 0.5 * X[:, 1])).astype(float)
    fit = fit_poisson(X, y, np.zeros(2), nonneg=(1,))
    assert fit.coef[1] == 0.0 and fit.fixed_zero == (1,)


def test_fractional_counts_are_accepted_and_negative_refused():
    fit = fit_poisson(np.ones((3, 1)), np.array([0.25, 0.5, 0.0]), np.zeros(1))
    assert np.exp(fit.coef[0]) == pytest.approx(0.25)
    with pytest.raises(ValueError, match="non-negative"):
        fit_poisson(np.ones((2, 1)), np.array([-1.0, 1.0]), np.zeros(1))


def _first_order_conditions(X, y, coef, pen):
    mu = np.exp(_mm(X, coef))
    return _mm(X.T, mu - y) + pen * coef


def test_penalized_fit_satisfies_its_own_score_equations():
    rng = np.random.default_rng(3)
    X = np.column_stack([np.ones(500), rng.normal(size=500), rng.normal(size=500)])
    y = rng.poisson(np.exp(0.3 + 0.6 * X[:, 1] - 0.2 * X[:, 2])).astype(float)
    pen = np.array([0.0, 50.0, 5.0])
    fit = fit_poisson(X, y, pen)
    assert fit.converged
    np.testing.assert_allclose(_first_order_conditions(X, y, fit.coef, pen), 0.0, atol=1e-6)


def test_nonneg_column_the_data_want_positive_is_left_free():
    rng = np.random.default_rng(4)
    X = np.column_stack([np.ones(3000), rng.normal(size=3000)])
    y = rng.poisson(np.exp(0.1 + 0.5 * X[:, 1])).astype(float)
    fit = fit_poisson(X, y, np.zeros(2), nonneg=(1,))
    assert fit.fixed_zero == () and fit.coef[1] == pytest.approx(0.5, abs=0.05)


def test_nonneg_fit_is_the_constrained_optimum():
    rng = np.random.default_rng(5)
    X = np.column_stack([np.ones(4000), rng.normal(size=4000), rng.normal(size=4000)])
    y = rng.poisson(np.exp(0.2 - 0.4 * X[:, 1] + 0.3 * X[:, 2])).astype(float)
    fit = fit_poisson(X, y, np.zeros(3), nonneg=(1, 2))
    assert fit.converged and fit.fixed_zero == (1,) and fit.coef[2] > 0
    g = _first_order_conditions(X, y, fit.coef, np.zeros(3))
    assert abs(g[0]) < 1e-6 and abs(g[2]) < 1e-6 and g[1] > 0


def test_all_zero_counts_report_no_finite_fit():
    fit = fit_poisson(np.column_stack([np.ones(4), np.arange(4.0)]), np.zeros(4), np.zeros(2))
    assert not fit.converged and np.all(np.isfinite(fit.coef)) and fit.coef[1] == 0.0


def test_all_zero_column_gets_zero_and_the_rest_still_fits():
    y = np.array([0.0, 1.0, 2.0, 5.0])
    X = np.column_stack([np.ones(4), np.zeros(4)])
    fit = fit_poisson(X, y, np.zeros(2))
    assert fit.converged and fit.coef[1] == 0.0
    assert np.exp(fit.coef[0]) == pytest.approx(2.0, rel=1e-8)


def test_iteration_cap_reports_not_converged():
    rng = np.random.default_rng(6)
    X = np.column_stack([np.ones(1000), rng.normal(size=1000)])
    y = rng.poisson(np.exp(1.0 + 2.0 * X[:, 1])).astype(float)
    fit = fit_poisson(X, y, np.zeros(2), max_iter=1)
    assert not fit.converged and fit.iterations == 1


def test_extreme_predictors_do_not_overflow():
    X = np.column_stack([np.ones(4), np.array([0.0, 1.0, 2.0, 1e3])])
    y = np.array([1.0, 2.0, 3.0, 4.0])
    with np.errstate(over="raise"):
        fit = fit_poisson(X, y, np.array([0.0, 1.0]))
    assert np.all(np.isfinite(fit.coef)) and np.isfinite(fit.deviance)


def test_deviance_is_zero_for_a_saturated_intercept():
    fit = fit_poisson(np.ones((3, 1)), np.full(3, 2.0), np.zeros(1))
    assert fit.deviance == pytest.approx(0.0, abs=1e-9)


def test_bad_inputs_are_refused():
    with pytest.raises(ValueError, match="intercept"):
        fit_poisson(np.column_stack([np.arange(3.0), np.ones(3)]), np.ones(3), np.zeros(2))
    with pytest.raises(ValueError, match="penalty"):
        fit_poisson(np.ones((3, 1)), np.ones(3), np.array([-1.0]))
    with pytest.raises(ValueError, match="nonneg"):
        fit_poisson(np.ones((3, 1)), np.ones(3), np.zeros(1), nonneg=(0,))


def test_linear_predictor_is_x_times_coef():
    X = np.array([[1.0, 2.0], [1.0, -1.0]])
    np.testing.assert_allclose(linear_predictor(X, np.array([0.5, 2.0])), [4.5, -1.5])


def test_a_fixed_column_is_released_when_a_later_fix_makes_it_want_positive():
    # Fixing alone ends at (1, 2, 3) here; the constrained optimum (brute-forced) frees 2 and 3.
    rng = np.random.default_rng(6)
    X = np.column_stack([np.ones(2000), _mm(rng.normal(size=(2000, 3)), rng.normal(size=(3, 3)))])
    b = np.r_[0.0, rng.normal(size=3) * 0.5]
    y = rng.poisson(np.exp(np.clip(_mm(X, b), -5, 3))).astype(float)
    fit = fit_poisson(X, y, np.zeros(4), nonneg=(1, 2, 3))
    assert fit.converged and fit.fixed_zero == (1,)
    g = _first_order_conditions(X, y, fit.coef, np.zeros(4))
    assert np.all(np.abs(g[[0, 2, 3]]) < 1e-5) and g[1] >= 0 and np.all(fit.coef[2:] >= 0)


def test_a_fit_stalled_at_the_eta_clip_is_not_reported_converged():
    x = np.linspace(0.0, 1.0, 200)
    X = np.column_stack([np.ones(200), x])
    y = np.round(np.exp(32.0 * x))
    fit = fit_poisson(X, y, np.zeros(2))
    g = _first_order_conditions(X, y, fit.coef, np.zeros(2))
    # The optimum (slope ~32) sits past the eta clip, so the gradient stays large; it must not pass.
    assert np.max(np.abs(g)) > 1.0 and not fit.converged


def test_heavy_penalties_converge_to_the_penalized_optimum():
    rng = np.random.default_rng(7)
    X = np.column_stack([np.ones(800), rng.normal(size=(800, 5))])
    beta = np.array([0.5, 0.8, -0.6, 0.3, 0.0, -0.2])
    y = rng.poisson(np.exp(_mm(X, beta))).astype(float)
    pen = np.array([1e3, 5e3, 5e3, 5e3, 5e3, 5e3])
    fit = fit_poisson(X, y, pen)
    assert fit.converged
    np.testing.assert_allclose(_first_order_conditions(X, y, fit.coef, pen), 0.0, atol=1e-6)


def test_all_zero_counts_with_every_column_penalized_have_a_finite_optimum():
    X = np.column_stack([np.ones(4), np.arange(4.0)])
    pen = np.array([1.0, 1.0])
    fit = fit_poisson(X, np.zeros(4), pen)
    assert fit.converged and np.all(np.isfinite(fit.coef))
    np.testing.assert_allclose(_first_order_conditions(X, np.zeros(4), fit.coef, pen), 0.0, atol=1e-9)
