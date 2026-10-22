import dataclasses

import numpy as np
import pytest
from hypothesis import given, settings, strategies as st
from scipy.special import gammaincinv

from app.ml.eb import MIN_PRIOR_SHAPE
from app.ml.fixtures import fitted_toy
from app.ml.posterior import (
    N_GEO,
    N_HIGH,
    N_OWN,
    PHI_GRID,
    Thresholds,
    confidence_level,
    interval90,
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
    lo, mean, hi = interval90(np.array([shape]), np.array([rate]), phi)
    assert 0 < lo[0] <= mean[0] <= hi[0]
    assert np.all(np.isfinite([lo[0], mean[0], hi[0]]))
    stored = _through_double_array(lo)
    assert 0 < stored[0] == lo[0]


def test_shape_floor_q05_underflows_in_float32_but_not_double():
    lo, _, _ = interval90(np.array([MIN_PRIOR_SHAPE]), np.array([1e3]), 4.0)
    assert lo[0] > 0 and np.float32(lo[0]) == 0.0
    assert _through_double_array(lo)[0] > 0


@settings(max_examples=200, deadline=None)
@given(shape_extra=st.floats(0, 50), rate=st.floats(1e-3, 1e3), phi=st.floats(1.0, 4.0))
def test_interval_is_the_union_of_nominal_and_widened(shape_extra, rate, phi):
    shape = MIN_PRIOR_SHAPE + shape_extra
    lo, _, hi = interval90(np.array([shape]), np.array([rate]), phi)
    for a in (shape, shape / phi):
        b = rate * a / shape
        assert lo[0] <= gammaincinv(a, 0.05) / b * (1 + 1e-12)
        assert hi[0] >= gammaincinv(a, 0.95) / b * (1 - 1e-12)


def test_phi_only_widens_even_at_the_shape_floor():
    shape, rate = np.array([MIN_PRIOR_SHAPE, 0.5, 5.0]), np.ones(3)
    lo1, _, hi1 = interval90(shape, rate, 1.0)
    for phi in PHI_GRID[1:]:
        lo, _, hi = interval90(shape, rate, phi)
        assert np.all(lo <= lo1) and np.all(hi >= hi1)
    # The phi-only interval's q95 falls under the mean at the floor; the union keeps the nominal one.
    assert gammaincinv(MIN_PRIOR_SHAPE / 4.0, 0.95) * 4.0 < MIN_PRIOR_SHAPE
    assert interval90(shape, rate, 4.0)[2][0] == pytest.approx(gammaincinv(MIN_PRIOR_SHAPE, 0.95))


def test_interval_asserts_q95_at_least_mean_below_the_floor():
    with pytest.raises(AssertionError):
        interval90(np.array([0.005]), np.array([1.0]), 1.0)


def test_percentile_is_mid_rank_with_ties():
    ref, w = np.array([1.0, 2.0, 2.0, 3.0]), np.ones(4)
    assert percentile_in(np.array([2.0]), ref, w)[0] == pytest.approx(50.0)
    assert percentile_in(np.array([0.5]), ref, w)[0] == 0.0 and percentile_in(np.array([9.0]), ref, w)[0] == 100.0


def test_percentile_is_route_weighted():
    ref, w = np.array([1.0, 2.0]), np.array([3.0, 1.0])
    assert percentile_in(np.array([2.0]), ref, w)[0] == pytest.approx(100 * (3 + 0.5) / 4)


def test_insufficient_needs_no_geo_no_feature_evidence_and_no_near_neighbour():
    geo = np.array([True, True, False, False, False, False])
    feat = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.3])
    dist = np.array([0.0, 9.0, 0.0, 1.0, 1.0 + 1e-9, 9.0])
    assert insufficient_mask(geo, feat, dist, 1.0).tolist() == [False, False, False, False, True, False]
    assert not insufficient_mask(geo, feat, dist, np.inf).any()          # d_star = inf: never gray by distance


def _levels(th, ratio, n_geo, n_feat, y_own):
    k = len(ratio)
    return confidence_level(np.asarray(ratio, float), np.zeros(k, bool), th, np.asarray(n_geo, float),
                            np.asarray(n_feat, float), np.asarray(y_own, float)).tolist()


def test_confidence_thresholds_are_inclusive_upper_bounds():
    th = Thresholds(d_star=1.0, conf_high=3.0, conf_medium=8.0, phi=1.0)
    ratio = np.array([1.0, 3.0, 3.0001, 8.0, 8.0001, np.inf, 1.0])
    ins = np.array([False] * 6 + [True])
    rich = np.full(7, 50.0)
    assert confidence_level(ratio, ins, th, rich, rich, np.ones(7)).tolist() == [
        "high", "high", "medium", "medium", "low", "low", "insufficient"]


def test_high_needs_evidence_else_capped_at_medium():
    th = Thresholds(d_star=1.0, conf_high=3.0, conf_medium=8.0, phi=1.0)
    assert (N_HIGH, N_GEO) == (10.0, 5.0) == (th.n_high, th.n_geo)
    cases = [  # (n_geo, n_feat, y_own) -> level at a narrow ratio
        ((10.0, 0.0, 1.0), "high"),
        ((5.0, 5.0, 0.0), "high"),            # no own incident but n_geo >= N_GEO
        ((4.9, 5.1, 1.0), "high"),            # own incident replaces the N_GEO arm
        ((4.9, 5.1, 0.0), "medium"),          # borrowed mostly from the archetype
        ((0.0, 50.0, 0.0), "medium"),         # archetype only: never high
        ((6.0, 3.9, 2.0), "medium"),          # total under N_HIGH
    ]
    for (g, f, y), want in cases:
        assert _levels(th, [1.5], [g], [f], [y]) == [want], (g, f, y)
    assert _levels(th, [20.0], [50.0], [50.0], [5.0]) == ["low"]   # evidence never narrows a wide range


def test_disabled_levels_are_skipped_never_all_high():
    rich = ([50.0, 50.0], [50.0, 50.0], [1.0, 1.0])
    no_high = Thresholds(d_star=1.0, conf_high=None, conf_medium=8.0, phi=1.0)
    assert _levels(no_high, [1.0, 20.0], *rich) == ["medium", "low"]
    none = Thresholds(d_star=1.0, conf_high=None, conf_medium=None, phi=1.0)
    assert _levels(none, [1.0, 20.0], *rich) == ["low", "low"]
    no_med = Thresholds(d_star=1.0, conf_high=3.0, conf_medium=None, phi=1.0)
    assert _levels(no_med, [1.0, 5.0], *rich) == ["high", "low"]


@pytest.mark.parametrize("kw", [
    dict(conf_high=9.0, conf_medium=8.0), dict(conf_high=0.5), dict(phi=0.9), dict(d_star=-1.0),
    dict(d_star=np.nan), dict(conf_high=np.nan), dict(conf_medium=np.nan), dict(conf_high=np.inf),
    dict(conf_medium=np.inf), dict(phi=max(PHI_GRID) + 1e-9), dict(phi=np.inf), dict(n_high=np.nan),
])
def test_thresholds_reject_nonsense(kw):
    base = dict(d_star=1.0, conf_high=3.0, conf_medium=8.0, phi=1.0)
    with pytest.raises(ValueError):
        Thresholds(**(base | kw))


def test_thresholds_accept_inf_d_star_none_levels_and_top_phi():
    Thresholds(d_star=np.inf, conf_high=None, conf_medium=None, phi=max(PHI_GRID))


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


def test_archetype_evidence_is_never_gray_even_far_away():
    toy = fitted_toy(local_incidents=0, archetype_incidents=3)
    s = summarize(**toy.inputs(thresholds=Thresholds(d_star=0.5, conf_high=3.0, conf_medium=8.0, phi=1.0),
                               evidence_far=True))
    i = toy.target_cell
    assert not toy.geo_evidence[i] and s.n_eff_feature[i] > 0
    assert not s.insufficient[i] and s.conf_level[i] in ("low", "medium")
    assert 0 < s.rr_q05[i] <= s.rr_mean[i] <= s.rr_q95[i]


def test_zero_local_incident_cell_is_not_high_even_with_a_loose_ratio():
    toy = fitted_toy(local_incidents=0, archetype_incidents=5)
    i = toy.target_cell
    first = summarize(**toy.inputs(thresholds=TH))
    loose = 2.0 * float(np.nanmax(first.rr_q95 / first.rr_q05))   # every cell is "high" by width
    s = summarize(**toy.inputs(thresholds=Thresholds(d_star=1.0, conf_high=loose, conf_medium=loose, phi=1.0)))
    assert s.conf_level[i] == "medium"


def test_summarize_rejects_nan_distance_and_non_bool_geo():
    toy = fitted_toy(local_incidents=1, archetype_incidents=1)
    kw = toy.inputs(thresholds=TH)
    kw["evidence_dist"] = kw["evidence_dist"].copy()
    kw["evidence_dist"][0] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        summarize(**kw)
    kw = toy.inputs(thresholds=TH)
    kw["geo_evidence"] = kw["geo_evidence"].astype(np.int64)
    with pytest.raises(ValueError, match="bool"):
        summarize(**kw)


def test_summarize_rejects_a_sufficient_cell_without_routes():
    toy = fitted_toy(local_incidents=1, archetype_incidents=1)
    kw = toy.inputs(thresholds=TH)
    n_routes = toy.cells.n_routes.copy()
    n_routes[toy.target_cell] = 0
    kw["cells"] = dataclasses.replace(toy.cells, n_routes=n_routes)
    with pytest.raises(ValueError, match="n_routes"):
        summarize(**kw)


def test_tick_coverage_within_rounding_of_one_is_complete():
    toy = fitted_toy(local_incidents=1, archetype_incidents=1, tick_coverage=1.0 - 1e-12)
    s = summarize(**toy.inputs(thresholds=TH))
    assert s.per10k_reason[toy.target_cell] is None and np.isfinite(s.per10k_mean[toy.target_cell])


@settings(max_examples=40, deadline=None)
@given(local=st.integers(0, 6), arch=st.integers(0, 6), density=st.integers(1, 60),
       phi=st.floats(1.0, max(PHI_GRID)), far=st.booleans())
def test_summarize_output_is_ordered_on_every_sufficient_cell(local, arch, density, phi, far):
    toy = fitted_toy(local_incidents=local, archetype_incidents=arch, density=density)
    s = summarize(**toy.inputs(thresholds=Thresholds(0.5, 3.0, 8.0, phi), evidence_far=far))
    ok = ~s.insufficient
    assert ok.any()
    q05 = _through_double_array(s.rr_q05[ok])
    assert np.all(0 < q05) and np.all(q05 <= s.rr_mean[ok]) and np.all(s.rr_mean[ok] <= s.rr_q95[ok])
    assert np.all(0 <= s.pct_q05[ok]) and np.all(s.pct_q05[ok] <= s.percentile[ok])
    assert np.all(s.percentile[ok] <= s.pct_q95[ok]) and np.all(s.pct_q95[ok] <= 100)
    assert set(s.conf_level[ok]) <= {"high", "medium", "low"}


def test_fractional_own_incident_does_not_open_the_high_arm():
    th = Thresholds(d_star=1.0, conf_high=3.0, conf_medium=8.0, phi=1.0)
    assert th.n_own == N_OWN == 1.0
    assert _levels(th, [1.5], [4.9], [50.0], [0.02]) == ["medium"]
    assert _levels(th, [1.5], [4.9], [50.0], [1.0]) == ["high"]


def test_failed_gate_with_medium_disabled_is_low():
    th = Thresholds(d_star=1.0, conf_high=3.0, conf_medium=None, phi=1.0)
    assert _levels(th, [1.5], [0.0], [50.0], [0.0]) == ["low"]


def test_medium_needs_some_evidence():
    th = Thresholds(d_star=np.inf, conf_high=3.0, conf_medium=8.0, phi=1.0)
    assert _levels(th, [1.5, 5.0, 5.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1e-6], [0.0, 0.0, 0.0]) == [
        "low", "low", "medium"]


def test_geo_evidence_must_agree_with_n_eff_geo():
    toy = fitted_toy(local_incidents=0, archetype_incidents=3)
    kw = toy.inputs(thresholds=TH)
    geo = kw["geo_evidence"].copy()
    geo[toy.target_cell] = True                       # caller claims geo evidence the chain does not have
    kw["geo_evidence"] = geo
    with pytest.raises(ValueError, match="disagrees"):
        summarize(**kw)
