import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from app.ml.eb import MIN_PRIOR_SHAPE
from app.ml.fixtures import fitted_toy
from app.ml.posterior import (
    Thresholds,
    confidence_level,
    gamma_interval,
    insufficient_mask,
    percentile_in,
    summarize,
)

TH = Thresholds(d_star=1.0, conf_high=3.0, conf_medium=8.0, phi=1.0)


def _through_double_array(x: np.ndarray) -> np.ndarray:
    # Task 20 binds Python floats into CAST(:x AS double precision[]); this is that round trip.
    return np.asarray([float(v) for v in x], dtype=np.float64)


@settings(max_examples=200, deadline=None)
@given(shape_extra=st.floats(0, 50), rate=st.floats(1e-3, 1e3), phi=st.floats(1.0, 4.0))
def test_interval_brackets_the_mean_even_at_tiny_shape(shape_extra, rate, phi):
    shape = MIN_PRIOR_SHAPE + shape_extra
    lo, mean, hi = gamma_interval(np.array([shape]), np.array([rate]), phi)
    assert 0 < lo[0] <= mean[0] <= hi[0]
    assert np.all(np.isfinite([lo[0], mean[0], hi[0]]))
    stored = _through_double_array(lo)
    assert 0 < stored[0] == lo[0]


def test_shape_floor_q05_underflows_in_float32_but_not_double():
    lo, _, _ = gamma_interval(np.array([MIN_PRIOR_SHAPE]), np.array([1e3]), 4.0)
    assert lo[0] > 0 and np.float32(lo[0]) == 0.0
    assert _through_double_array(lo)[0] > 0


def test_q95_is_lifted_to_the_mean_only_where_the_gamma_puts_it_below():
    lo, mean, hi = gamma_interval(np.array([MIN_PRIOR_SHAPE, 5.0]), np.array([1.0, 1.0]), 4.0)
    assert hi[0] == mean[0]                  # shape/phi = 0.0125 < 0.01373: raw q95 sits under the mean
    assert hi[1] > mean[1] > lo[1]


def test_percentile_is_mid_rank_with_ties():
    ref, w = np.array([1.0, 2.0, 2.0, 3.0]), np.ones(4)
    assert percentile_in(np.array([2.0]), ref, w)[0] == pytest.approx(50.0)
    assert percentile_in(np.array([0.5]), ref, w)[0] == 0.0 and percentile_in(np.array([9.0]), ref, w)[0] == 100.0


def test_percentile_is_route_weighted():
    ref, w = np.array([1.0, 2.0]), np.array([3.0, 1.0])
    assert percentile_in(np.array([2.0]), ref, w)[0] == pytest.approx(100 * (3 + 0.5) / 4)


def test_insufficient_needs_both_no_geo_evidence_and_no_near_neighbour():
    geo = np.array([True, True, False, False, False])
    dist = np.array([0.0, 9.0, 0.0, 1.0, 1.0 + 1e-9])
    assert insufficient_mask(geo, dist, 1.0).tolist() == [False, False, False, False, True]


def test_confidence_thresholds_are_inclusive_upper_bounds():
    th = Thresholds(d_star=1.0, conf_high=3.0, conf_medium=8.0, phi=1.0)
    ratio = np.array([1.0, 3.0, 3.0001, 8.0, 8.0001, np.inf, 1.0])
    ins = np.array([False] * 6 + [True])
    assert confidence_level(ratio, ins, th).tolist() == [
        "high", "high", "medium", "medium", "low", "low", "insufficient"]


@pytest.mark.parametrize("kw", [dict(conf_high=9.0, conf_medium=8.0), dict(conf_high=0.5), dict(phi=0.9),
                                dict(d_star=-1.0), dict(d_star=np.inf)])
def test_thresholds_reject_nonsense(kw):
    base = dict(d_star=1.0, conf_high=3.0, conf_medium=8.0, phi=1.0)
    with pytest.raises(ValueError):
        Thresholds(**(base | kw))


def test_no_local_incident_borrows_from_archetype():
    toy = fitted_toy(local_incidents=0, archetype_incidents=5)
    s = summarize(**toy.inputs(thresholds=TH))
    i = toy.target_cell
    assert not toy.geo_evidence[i]
    assert not s.insufficient[i] and s.conf_level[i] in ("low", "medium") and s.rr_mean[i] > 0
    assert s.n_eff_geo[i] == pytest.approx(0.0) and s.n_eff_feature[i] > 0


def test_insufficient_only_without_geo_and_feature_evidence():
    toy = fitted_toy(local_incidents=0, archetype_incidents=0)
    s = summarize(**toy.inputs(thresholds=Thresholds(d_star=0.5, conf_high=3.0, conf_medium=8.0, phi=1.0),
                               evidence_far=True))
    i = toy.target_cell
    assert s.insufficient[i] and s.conf_level[i] == "insufficient"
    for arr in (s.rr_mean, s.rr_q05, s.rr_q95, s.percentile, s.pct_q05, s.pct_q95, s.n_eff_geo,
                s.n_eff_feature, s.per10k_mean):
        assert np.isnan(arr[i])
    # Only that cell: every other cell keeps a number.
    assert s.insufficient.sum() == 1


def test_far_cell_with_geo_evidence_is_not_insufficient():
    toy = fitted_toy(local_incidents=1, archetype_incidents=0)
    s = summarize(**toy.inputs(thresholds=TH, evidence_far=True))
    i = toy.target_cell
    assert toy.geo_evidence[i] and not s.insufficient[i] and np.isfinite(s.rr_mean[i])


def test_low_confidence_cell_still_shows_number_and_range():
    toy = fitted_toy(local_incidents=0, archetype_incidents=1)
    s = summarize(**toy.inputs(thresholds=TH))
    i = toy.target_cell
    assert s.conf_level[i] == "low"
    assert 0 < s.rr_q05[i] < s.rr_mean[i] < s.rr_q95[i] and np.isfinite(s.rr_q95[i])
    assert 0 <= s.pct_q05[i] <= s.percentile[i] <= s.pct_q95[i] <= 100


@pytest.mark.parametrize("local,arch", [(0, 0), (0, 5), (2, 2), (5, 0)])
def test_sufficient_cells_are_finite_and_ordered(local, arch):
    toy = fitted_toy(local_incidents=local, archetype_incidents=arch)
    s = summarize(**toy.inputs(thresholds=Thresholds(1.0, 3.0, 8.0, 4.0)))
    ok = ~s.insufficient
    assert ok.all()
    for arr in (s.rr_mean, s.rr_q05, s.rr_q95, s.percentile, s.pct_q05, s.pct_q95, s.n_eff_geo, s.n_eff_feature):
        assert np.all(np.isfinite(arr[ok]))
    assert np.all(0 < s.rr_q05[ok]) and np.all(s.rr_q05[ok] <= s.rr_mean[ok])
    assert np.all(s.rr_mean[ok] <= s.rr_q95[ok])
    assert np.all(s.pct_q05[ok] <= s.percentile[ok]) and np.all(s.percentile[ok] <= s.pct_q95[ok])
    assert np.all(_through_double_array(s.rr_q05[ok]) > 0)


def test_rr_reference_is_one_on_average_within_group_month():
    toy = fitted_toy(local_incidents=2, archetype_incidents=3)
    s = summarize(**toy.inputs(thresholds=TH))
    july = toy.cells.month == 7
    w = np.exp(toy.design.X[:, list(toy.design.exposure_cols)] @ toy.coef[list(toy.design.exposure_cols)])
    assert np.sum(w[july] * s.rr_mean[july]) / w[july].sum() == pytest.approx(1.0)


def test_n_eff_geo_counts_local_incidents():
    toy = fitted_toy(local_incidents=3, archetype_incidents=0)
    s = summarize(**toy.inputs(thresholds=TH))
    i = toy.target_cell
    wc = toy.cp.omega_cell[i]
    assert s.n_eff_geo[i] >= wc * 3 - 1e-12 and s.n_eff_geo[i] > 0


def test_raising_density_with_fixed_incidents_never_raises_rr():
    lo = fitted_toy(local_incidents=2, archetype_incidents=2, density=5)
    hi = fitted_toy(local_incidents=2, archetype_incidents=2, density=50)
    assert summarize(**hi.inputs(thresholds=TH)).rr_mean[hi.target_cell] <= \
        summarize(**lo.inputs(thresholds=TH)).rr_mean[lo.target_cell] + 1e-12


@pytest.mark.parametrize("coverage,ticks,reason", [
    (0.5, 120.0, "ticks_incomplete"),
    (0.5, 0.0, "ticks_incomplete"),
    (None, None, "ticks_missing"),
    (None, 120.0, "ticks_missing"),
    (1.0, None, "ticks_missing"),
    (1.0, 0.0, "no_ticks_logged"),
    (1.0, 120.0, None),
])
def test_per10k_only_at_full_tick_coverage(coverage, ticks, reason):
    toy = fitted_toy(local_incidents=1, archetype_incidents=1, tick_coverage=coverage, ticks_value=ticks)
    s = summarize(**toy.inputs(thresholds=TH))
    i = toy.target_cell
    assert s.per10k_reason[i] == reason
    if reason is None:
        assert 0 < s.per10k_q05[i] <= s.per10k_mean[i] <= s.per10k_q95[i] < np.inf
        mu0 = np.exp(toy.design.X[i] @ toy.coef)
        theta = toy.cp.post.mean[i]
        assert s.per10k_mean[i] == pytest.approx(1e4 * mu0 * theta / toy.years / toy.cells.ticks_value[i])
    else:
        assert np.isnan([s.per10k_mean[i], s.per10k_q05[i], s.per10k_q95[i]]).all()


def test_summarize_rejects_misaligned_evidence():
    toy = fitted_toy(local_incidents=1, archetype_incidents=1)
    kw = toy.inputs(thresholds=TH)
    kw["evidence_dist"] = kw["evidence_dist"][:-1]
    with pytest.raises(ValueError):
        summarize(**kw)
