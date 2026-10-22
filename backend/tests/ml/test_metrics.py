import numpy as np
import pytest
from scipy.stats import chi2, nbinom

from app.ml.metrics import (
    METRICS_REPORT_KEYS,
    calibration,
    interval_coverage,
    mean_log_score,
    nb_logpmf,
    oe_ci,
    pit_coverage,
    poisson_deviance,
    spearman,
    top_capture,
)
from app.ml.posterior import interval90


def test_nb_logpmf_matches_scipy_on_integers():
    k, shape, rate, scale = np.array([0.0, 1.0, 4.0]), np.array([2.0]), np.array([3.0]), np.array([1.5])
    p = rate / (rate + scale)
    np.testing.assert_allclose(nb_logpmf(k, shape, rate, scale), nbinom.logpmf(k, shape, p), rtol=1e-10)


def test_nb_logpmf_scores_fractional_counts_between_neighbours():
    shape, rate, scale = np.array([2.0]), np.array([1.0]), np.array([1.0])
    lo, mid, hi = nb_logpmf(np.array([1.0, 1.5, 2.0]), shape, rate, scale)
    assert np.isfinite(mid) and min(lo, hi) - 0.1 < mid < max(lo, hi) + 0.1


def test_mean_log_score_is_the_mean():
    k, shape, rate, scale = np.array([0.0, 2.0]), np.array([2.0, 2.0]), np.array([1.0, 1.0]), np.array([1.0, 1.0])
    assert mean_log_score(k, shape, rate, scale) == pytest.approx(float(np.mean(nb_logpmf(k, shape, rate, scale))))


def test_metrics_refuse_nan_and_bad_parameters():
    one = np.array([1.0])
    with pytest.raises(ValueError):
        nb_logpmf(np.array([np.nan]), one, one, one)
    with pytest.raises(ValueError):
        nb_logpmf(one, one, np.array([0.0]), one)
    with pytest.raises(ValueError):
        nb_logpmf(np.array([-1.0]), one, one, one)
    with pytest.raises(ValueError):
        poisson_deviance(one, np.array([np.nan]))
    with pytest.raises(ValueError):
        calibration(np.array([1.0, np.nan]), np.array([1.0, 1.0]))
    with pytest.raises(ValueError):
        top_capture(np.array([1.0, 2.0]), np.array([np.nan, 1.0]))
    with pytest.raises(ValueError):
        pit_coverage(one, one, one, np.array([np.inf]))
    with pytest.raises(ValueError):
        oe_ci(float("nan"), 1.0)
    with pytest.raises(ValueError):
        oe_ci(1.0, 0.0)
    with pytest.raises(ValueError):
        top_capture(one, one, frac=0.0)


def test_poisson_deviance_known_values():
    assert poisson_deviance(np.array([3.0, 5.0]), np.array([3.0, 5.0])) == pytest.approx(0.0)
    assert poisson_deviance(np.array([0.0]), np.array([2.5])) == pytest.approx(5.0)
    assert poisson_deviance(np.array([2.0]), np.array([1.0])) == pytest.approx(2.0 * (2.0 * np.log(2.0) - 1.0))


def test_top_capture_splits_ties():
    pred = np.array([5.0, 5.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    obs = np.array([1.0, 0.0, 0, 0, 0, 0, 0, 0, 0, 1.0])
    assert top_capture(pred, obs, frac=0.10) == pytest.approx(0.25)   # one of two tied cells, half the incident


def test_top_capture_takes_everything_above_the_cut_and_a_share_of_the_tie():
    pred = np.array([9.0, 4.0, 4.0, 4.0, 4.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    obs = np.array([2.0, 0.0, 0.0, 0.0, 4.0, 1.0, 1.0, 0.0, 0.0, 0.0])
    # budget 3 cells: the 9 whole, then 2 of the 4 tied at 4 -> half their 4 incidents
    assert top_capture(pred, obs, frac=0.30) == pytest.approx((2.0 + 4.0 * 2 / 4) / 8.0)
    # the answer cannot depend on where the tied mass sits within the tie
    assert top_capture(pred, obs[[0, 4, 2, 3, 1, 5, 6, 7, 8, 9]], frac=0.30) == pytest.approx(0.5)


def test_top_capture_fractional_budget_and_no_incidents():
    pred = np.array([3.0, 2.0, 1.0, 0.0])
    obs = np.array([1.0, 1.0, 0.0, 0.0])
    assert top_capture(pred, obs, frac=0.375) == pytest.approx(0.75)   # 1.5 cells: one whole, half the next
    assert np.isnan(top_capture(pred, np.zeros(4)))


def test_calibration_falls_back_to_quintiles_then_reports_no_support():
    rng = np.random.default_rng(0)
    pred = rng.gamma(2.0, 1.0, 2000)
    obs = rng.poisson(pred).astype(float)
    assert calibration(pred, obs).scheme == "decile" and calibration(pred, obs).passed
    small = calibration(pred[:60] / 50, obs[:60] * 0)
    assert small.scheme == "insufficient_support" and not small.passed


def _per_bin(ratios: list[float], per_bin: int) -> tuple[np.ndarray, np.ndarray]:
    # Equal preds sort stably, so bin j holds cells [j*per_bin, (j+1)*per_bin). Each bin's observed
    # mass sits on one cell so O is exact and ratios at the 0.8/1.25 edges are not float-fuzzed.
    n = len(ratios) * per_bin
    pred = np.full(n, 10.0 / per_bin)
    obs = np.zeros(n)
    obs[::per_bin] = [r * 10.0 for r in ratios]
    return pred, obs


def test_calibration_min_expected_is_inclusive():
    c = calibration(*_per_bin([1.0] * 9 + [2.0], per_bin=10))
    assert c.scheme == "decile" and c.n_pass == 9 and c.passed
    assert [e for e, _, _ in c.bins] == [10.0] * 10


def test_calibration_bins_by_predicted_rank():
    rng = np.random.default_rng(4)
    pred = rng.permutation(np.linspace(1.0, 3.0, 100))
    obs = np.where(pred > np.quantile(pred, 0.9), 50.0, pred)
    c = calibration(pred, obs)
    assert c.scheme == "decile" and c.n_pass == 9 and c.bins[-1][2] > 2.0
    assert [b[0] for b in c.bins] == sorted(b[0] for b in c.bins)


def test_calibration_needs_eight_of_ten_and_does_not_fall_back_on_failure():
    edges = [0.8, 1.25, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    passing = calibration(*_per_bin(edges + [0.5, 2.0], per_bin=10))
    assert passing.scheme == "decile" and passing.n_pass == 8 and passing.passed
    failing = calibration(*_per_bin(edges[:7] + [0.5, 0.5, 2.0], per_bin=10))
    assert failing.scheme == "decile" and failing.n_pass == 7 and not failing.passed


def test_calibration_quintiles_need_four_of_five():
    pred, obs = _per_bin([1.0, 1.0, 1.0, 1.0, 3.0], per_bin=12)   # deciles hold 5 expected each
    c = calibration(pred, obs)
    assert c.scheme == "quintile" and c.n_pass == 4 and c.passed
    pred, obs = _per_bin([1.0, 1.0, 1.0, 0.1, 3.0], per_bin=12)
    c = calibration(pred, obs)
    assert c.scheme == "quintile" and c.n_pass == 3 and not c.passed


def test_pit_coverage_near_nominal_for_correct_model():
    rng = np.random.default_rng(1)
    shape, rate, scale = np.full(5000, 3.0), np.full(5000, 2.0), np.full(5000, 1.0)
    k = rng.poisson(rng.gamma(shape, 1 / rate) * scale).astype(float)
    assert 0.86 <= pit_coverage(k, shape, rate, scale) <= 0.94


def test_pit_is_randomized_within_the_count_step():
    # P(N = 0) = p**shape = 0.5: a randomized PIT for k = 0 is U(0, 0.5), so 90% land in [0.05, 0.95].
    # Any fixed point in the step (lower edge, upper edge, midpoint) gives 0 or 1 instead.
    n = 20000
    one = np.ones(n)
    cov = pit_coverage(np.zeros(n), one, one, one, seed=3)
    assert cov == pytest.approx(0.90, abs=0.01)
    assert pit_coverage(np.zeros(n), one, one, one, seed=3) == cov


def test_pit_uses_the_floor_of_fractional_counts():
    one = np.ones(4000)
    assert pit_coverage(np.full(4000, 0.7), one, one, one) == pit_coverage(np.zeros(4000), one, one, one)


def test_interval_coverage_is_the_count_scale_image_of_interval90():
    rng = np.random.default_rng(5)
    n = 4000
    shape, rate = rng.gamma(4.0, 1.0, n) + 0.5, rng.gamma(4.0, 1.0, n) + 0.5
    scale = np.full(n, 1e6)   # Poisson noise negligible: k/scale is a draw of theta
    theta = rng.gamma(shape * 0.6, 1.0 / (rate * 0.6))   # over-dispersed against the nominal fit
    k = rng.poisson(theta * scale).astype(float)
    lo, _, hi = interval90(shape, rate, 2.0)
    direct = float(np.mean((k / scale >= lo) & (k / scale <= hi)))
    assert interval_coverage(k, shape, rate, scale, phi=2.0) == pytest.approx(direct, abs=0.005)


def test_interval_coverage_only_widens():
    rng = np.random.default_rng(6)
    n = 3000
    shape, rate, scale = np.full(n, 2.0), np.full(n, 2.0), np.full(n, 3.0)
    k = rng.poisson(rng.gamma(0.7, 1 / 0.7, n) * scale).astype(float)
    nominal = interval_coverage(k, shape, rate, scale, phi=1.0)
    assert nominal == pit_coverage(k, shape, rate, scale)
    assert interval_coverage(k, shape, rate, scale, phi=3.0) >= max(nominal, pit_coverage(k, shape / 3, rate / 3, scale))


def test_oe_ci_exact_poisson_known_values():
    lo, oe, hi = oe_ci(0.0, 2.0)
    assert lo == 0.0 and oe == 0.0
    assert hi == pytest.approx(-np.log(0.05) / 2.0, rel=1e-10)   # 2.9957/E: the 95% one-sided bound
    lo, oe, hi = oe_ci(10.0, 10.0)
    assert lo == pytest.approx(chi2.ppf(0.05, 20) / 2 / 10, rel=1e-8)
    assert hi == pytest.approx(chi2.ppf(0.95, 22) / 2 / 10, rel=1e-8)
    assert (lo, hi) == pytest.approx((0.54254, 1.69622), abs=1e-4)
    lo95, _, hi95 = oe_ci(10.0, 10.0, level=0.95)
    assert lo95 < lo and hi95 > hi


def test_oe_ci_and_spearman():
    lo, oe, hi = oe_ci(10.0, 10.0)
    assert lo < 1.0 == oe < hi
    assert spearman(np.array([1, 2, 3.0]), np.array([3, 2, 1.0])) == pytest.approx(-1.0)
    assert spearman(np.array([1.0, 1.0]), np.array([1.0, 2.0])) is None


def test_metrics_report_keys_are_frozen():
    assert METRICS_REPORT_KEYS == ("log_score", "deviance", "top10", "calibration", "coverage", "oe",
                                   "n_cells", "observed", "expected")


def test_interval_coverage_keeps_the_nominal_arm_where_widening_alone_would_drop_it():
    # Tiny shape: Gamma(shape/phi) piles mass at 0, so k = 1 sits above the widened 95th percentile
    # but inside the nominal interval. interval90's union keeps it covered.
    n = 4000
    s, one, k = np.full(n, 0.05), np.ones(n), np.ones(n)
    assert pit_coverage(k, s / 4, s / 4, one) < 0.5
    assert interval_coverage(k, s, s, one, phi=4.0) == pit_coverage(k, s, s, one) == 1.0
