import dataclasses
import json
import math

import numpy as np
import pytest

from app.ml import posterior
from app.ml import tuning as tuning_mod
from app.ml.cells import STATE_GROUPS
from app.ml.fixtures import fold_results_toy, synthetic_fold_data, synthetic_raw, toy_fold_result
from app.ml.metrics import interval_coverage, pit_coverage
from app.ml.model import Fitted, ModelParams
from app.ml.tuning import NEUTRAL, PHI_GRID, RATIO_GRID, select_conf, select_d_star, select_phi, stability, tune


SMALL = 60  # areas: tune runs ~19 sweeps of 12 folds, so CI uses the small world


@pytest.fixture(scope="module")
def tuned31():
    return tune(synthetic_fold_data(seed=31, n_areas=SMALL))


def test_d_star_is_the_last_helpful_band():
    pooled, geo, dist = fold_results_toy(gain_by_band=(0.2, 0.1, -0.05, -0.1, -0.2))
    assert select_d_star(pooled, geo, dist) == np.quantile(dist, 0.5)


def test_d_star_zero_when_neighbours_never_help():
    pooled, geo, dist = fold_results_toy(gain_by_band=(-0.1,) * 5)
    assert select_d_star(pooled, geo, dist) == 0.0


def test_conf_selection_respects_coverage_and_monotone_error():
    good = fold_results_toy(calibrated=True)[0]
    assert select_conf(good, phi=1.0) is not None
    assert select_conf(fold_results_toy(calibrated=False)[0], phi=1.0) is None


def _tiers(spec, seed=0):
    """One FoldResult from (shape, cells, true-rate bias, tilt, deterministic) tiers. tilt bends the true
    rate with predicted risk (scale), so O/E runs low in low-risk bins and high in high-risk ones;
    deterministic counts are round(expected), far tighter than any posterior predicts."""
    rng = np.random.default_rng(seed)
    parts = []
    for a, n, bias, tilt, det in spec:
        shape = np.full(n, float(a))
        scale = rng.uniform(0.5, 2.0, n)
        theta = rng.gamma(shape, 1.0 / shape)
        obs = np.round(scale * bias) if det else rng.poisson(theta * scale * bias * (scale / 1.25) ** tilt)
        parts.append((shape, scale, np.asarray(obs, dtype=np.float64)))
    shape, scale, obs = (np.concatenate([p[i] for p in parts]) for i in range(3))
    return [toy_fold_result(shape, shape.copy(), scale, obs, np.zeros(shape.size))]


TIERS = [(40, 6000, 1.0, 0.0, False), (8, 2000, 1.0, 0.0, False), (2, 600, 1.0, 0.0, False)]


def test_select_conf_rejects_error_that_falls_as_confidence_drops():
    # The narrow tier (always "high") runs 12% hot: coverage still passes everywhere, but High's |log O/E|
    # beats the less confident levels' by far more than ERROR_ORDER_TOL, so every pair fails.
    hot = _tiers([(40, 6000, 1.12, 0.0, False), *TIERS[1:]])
    assert select_conf(hot, phi=1.0) is None


def test_error_order_allows_noise_within_tolerance():
    assert tuning_mod.errors_ordered([0.066, 0.065, 0.2])
    assert tuning_mod.errors_ordered([0.0, 0.0 - tuning_mod.ERROR_ORDER_TOL + 1e-9])
    assert not tuning_mod.errors_ordered([0.1, 0.05])


def test_select_conf_rejects_a_pair_whose_low_stratum_is_miscalibrated():
    # A very wide tier (ratio above 15) is "low" under every pair, and its O/E tilts with predicted risk,
    # so Low's decile calibration fails. Without that check (2, 15) wins: everything else is High/Medium.
    tilted = _tiers([*TIERS, (0.5, 3000, 1.2, 1.5, False)])
    assert select_conf(tilted, phi=1.0) is None


def test_select_conf_rejects_on_coverage_alone():
    # Deterministic counts: every level covers about 100% (> LEVEL_COVERAGE[1]) while O/E is the same in
    # every level and (2, 15) leaves Low empty, so only the coverage rule can reject.
    tight = _tiers([(a, n, b, t, True) for a, n, b, t, _ in TIERS])
    assert select_conf(tight, phi=1.0) is None


def test_level_coverage_admits_conservative_ranges():
    # Controller ruling: shown ranges run ~94-95% by design, so 0.96 must still pass.
    lo, hi = tuning_mod.LEVEL_COVERAGE
    assert lo == 0.85 and hi == 0.97


def _pooled(results):
    return {f: np.concatenate([getattr(r, f) for r in results]) for f in ("obs", "shape", "rate", "scale")}


def test_select_phi_judges_the_shown_union_interval():
    # Carry 1: the shown interval (posterior.interval90, union) on the count scale, never raw PIT of
    # the widened Gamma. Small shapes under true overdispersion make the two rules disagree: there the
    # widened Gamma alone pulls q95 down as phi grows, the union never does.
    res = fold_results_toy(tiers=((0.3, 3000), (0.1, 3000)), true_phi=2.0)[0]
    c = _pooled(res)
    union = {f: interval_coverage(c["obs"], c["shape"], c["rate"], c["scale"], f) for f in PHI_GRID}
    raw = {f: pit_coverage(c["obs"], c["shape"] / f, c["rate"] / f, c["scale"]) for f in PHI_GRID}
    by_union = min(PHI_GRID, key=lambda f: (abs(union[f] - 0.9), f))
    by_raw = min(PHI_GRID, key=lambda f: (abs(raw[f] - 0.9), f))
    assert by_union != by_raw
    assert select_phi(res) == by_union


def test_select_conf_judges_levels_by_the_shown_interval(monkeypatch):
    good = fold_results_toy(calibrated=True)[0]
    seen = []

    def never_covers(k, shape, rate, scale, phi, **kw):
        seen.append(phi)
        return 0.5

    monkeypatch.setattr(tuning_mod, "interval_coverage", never_covers)
    assert select_conf(good, phi=1.25) is None
    assert seen and set(seen) == {1.25}


def test_select_conf_levels_follow_the_evidence_gate(monkeypatch):
    # Levels are the shown ones: posterior.confidence_level with each cell's evidence, not width alone.
    good = fold_results_toy(calibrated=True)[0]
    bare = [dataclasses.replace(r, n_eff_geo=np.zeros_like(r.n_eff_geo), n_eff_feature=np.zeros_like(r.n_eff_feature))
            for r in good]
    calls = []
    real = tuning_mod.confidence_level

    def spy(ratio, insufficient, th, n_geo, n_feat, y_own):
        calls.append((n_geo, n_feat, y_own))
        return real(ratio, insufficient, th, n_geo, n_feat, y_own)

    monkeypatch.setattr(tuning_mod, "confidence_level", spy)
    select_conf(bare, phi=1.0)
    assert calls and all((g == 0).all() and (f == 0).all() for g, f, _ in calls)
    # No evidence: nothing is high or medium under any pair, so no pair can beat the first on share.
    assert select_conf(bare, phi=1.0) == (RATIO_GRID[0], RATIO_GRID[1])


def test_phi_grid_is_posteriors_and_no_conf_is_none_never_inf():
    # Carry 3.
    assert PHI_GRID is posterior.PHI_GRID
    assert NEUTRAL.conf_high is None and NEUTRAL.conf_medium is None and math.isinf(NEUTRAL.d_star)
    th = tuning_mod.thresholds_from(d_star=1.5, conf=None, phi=2.0)
    assert th.conf_high is None and th.conf_medium is None
    assert tuning_mod.thresholds_from(d_star=1.5, conf=(2.0, 6.0), phi=2.0).conf_high == 2.0


def test_tune_is_deterministic(tuned31):
    again = tune(synthetic_fold_data(seed=31, n_areas=SMALL))
    assert again.thresholds == tuned31.thresholds
    assert again.params == tuned31.params and again.report == tuned31.report


def test_tune_reads_held_out_results_only(tuned31):
    # Carry 2 / G5: a spatial fold's held-out cells score with y_own = fit.y_train (zeros), so their
    # training-window counts cannot move any selection.
    fds = synthetic_fold_data(seed=31, n_areas=SMALL)
    bumped = []
    for fd in fds:
        if fd.fold.kind == "spatial":
            y = fd.train.y.copy()
            y[fd.test_mask] += 100.0
            fd = dataclasses.replace(fd, train=dataclasses.replace(fd.train, y=y))
        bumped.append(fd)
    out = tune(bumped)
    assert out.thresholds == tuned31.thresholds and out.report == tuned31.report


def test_tune_report_is_json_and_phi_on_grid(tuned31):
    th, rep = tuned31.thresholds, tuned31.report
    assert th.phi in PHI_GRID and th.phi <= max(PHI_GRID)
    assert rep["conf_selected"] is (th.conf_high is not None)
    json.dumps(rep, allow_nan=False)


def test_stability_passes_on_stable_synthetic_problem():
    # Tuned and tested on one world; rate_scale 5 gives enough incidents per cell that rankings rest
    # on data, not on which groups were drawn.
    params, th = tune(synthetic_fold_data(seed=32, rate_scale=5.0))[:2]
    s = stability(synthetic_raw(seed=32, rate_scale=5.0), params, th, cutoff=None, n_subsamples=5)
    assert s["passed"] and s["median_spearman"] >= 0.8
    assert s["n_subsamples"] == 5 and s["n_failed"] == 0 and s["method"] == "state_group_subsample"
    json.dumps(s, allow_nan=False)


def test_stability_fails_on_an_unstable_problem():
    # A fifth of the incidents: rankings within (group, month) now move with the draw.
    s = stability(synthetic_raw(seed=32, rate_scale=0.2), ModelParams(), NEUTRAL, cutoff=None, n_subsamples=5)
    assert not s["passed"] and s["n_failed"] == 0


def test_stability_holds_undrawn_groups_out_of_training(monkeypatch):
    # Undrawn state groups are held out like a spatial fold, never trained as accident-free.
    real = tuning_mod.fit_full
    seen = []

    def spy(raw, params, *, cutoff, states=None):
        out = real(raw, params, cutoff=cutoff, states=states)
        seen.append((states, out))
        return out

    monkeypatch.setattr(tuning_mod, "fit_full", spy)
    s = stability(synthetic_raw(seed=33, n_areas=60), ModelParams(), NEUTRAL, cutoff=None, n_subsamples=2)
    assert seen[0][0] is None and len(seen) == 3
    for states, (fit, cells, _) in seen[1:]:
        assert all(set(g) <= states or not set(g) & states for g in STATE_GROUPS.values())
        held = ~np.isin(np.asarray(cells.states)[cells.state_idx[cells.area_idx]], sorted(states))
        assert held.any() and not fit.train_mask[held].any() and fit.train_mask[~held].all()
    assert len(s["spearman"]) == 2


def test_stability_scores_with_training_counts_only(monkeypatch):
    # Carry 2: y_own is fit.y_train (zeros on held-out cells), never the window's counts. The stats
    # compare drawn cells only, so a counts.y leak would not move them; pin the call itself.
    real = Fitted.summarize
    checked = []

    def strict(self, cells, y_own, thresholds):
        assert np.array_equal(y_own, self.y_train)
        checked.append(bool((~self.train_mask).any()))
        return real(self, cells, y_own, thresholds)

    monkeypatch.setattr(Fitted, "summarize", strict)
    stability(synthetic_raw(seed=33, n_areas=60), ModelParams(), NEUTRAL, cutoff=None, n_subsamples=2)
    assert checked == [False, True, True]
