import dataclasses
import math

import numpy as np
import pytest

from app.ml import model as model_mod
from app.ml.bundle import ModelBundle
from app.ml.cells import TYPE_GROUPS
from app.ml.explain import Term
from app.ml.fixtures import permute_problem, synthetic_problem
from app.ml.folds import spatial_folds
from app.ml.model import (
    BundleParityError,
    ModelFitError,
    ModelParams,
    check_parity,
    eb_cells,
    fit_model,
    fold_train_mask,
)
from app.ml.posterior import Thresholds, interval90

TH = Thresholds(d_star=float("inf"), conf_high=3.0, conf_medium=None, phi=1.5)


def test_fit_is_deterministic_and_row_order_invariant():
    cells, counts = synthetic_problem(seed=11)
    a = fit_model(cells, counts, ModelParams()).predict(cells, counts.y)[1].post.mean
    b = fit_model(cells, counts, ModelParams()).predict(cells, counts.y)[1].post.mean
    np.testing.assert_array_equal(a, b)
    perm = np.random.default_rng(0).permutation(cells.n)
    pc, pk = permute_problem(cells, counts, perm)
    c = fit_model(pc, pk, ModelParams()).predict(pc, pk.y)[1].post.mean
    np.testing.assert_allclose(c, a[perm], rtol=1e-9)


def test_held_out_cells_do_not_see_their_own_counts():
    cells, counts = synthetic_problem(seed=12)
    mask = cells.state_idx[cells.area_idx] != 0
    fit = fit_model(cells, counts, ModelParams(), train_mask=mask)
    bumped = counts.y.copy()
    bumped[~mask] += 100.0
    fit2 = fit_model(cells, type(counts)(bumped, counts.state_mass, counts.years, counts.dropped, counts.placed),
                     ModelParams(), train_mask=mask)
    np.testing.assert_allclose(fit.glm.coef, fit2.glm.coef)
    for k in ("kappa_cell", "kappa_area", "kappa_region", "kappa_state", "kappa_arch"):
        assert getattr(fit.eb, k) == pytest.approx(getattr(fit2.eb, k))
    for g in range(len(TYPE_GROUPS)):
        np.testing.assert_array_equal(fit.ev_points[g], fit2.ev_points[g])
    shifted = dataclasses.replace(cells, static=np.where(mask[:, None], cells.static, cells.static + 50.0))
    fit3 = fit_model(shifted, counts, ModelParams(), train_mask=mask)
    np.testing.assert_array_equal(fit3.design.center, fit.design.center)
    assert fit3.design.tick_ref == fit.design.tick_ref


def test_pooling_beats_geo_only_on_feature_driven_synthetic_risk():
    # Compared on the absolute rate mu0 * theta: theta alone is relative to each fit's own mu0.
    cells, counts, truth = synthetic_problem(seed=13, return_truth=True)
    mask = cells.state_idx[cells.area_idx] != 0
    pooled = fit_model(cells, counts, ModelParams(), train_mask=mask)
    geo = fit_model(cells, counts, ModelParams(blend=False), train_mask=mask)
    zero = np.zeros(cells.n)

    def err(f):
        mu0, cp, _, _ = f.predict(cells, zero)
        return np.abs(np.log(mu0 * cp.post.mean)[~mask] - np.log(truth[~mask])).mean()

    assert err(pooled) < err(geo)


def test_non_converged_glm_is_refused():
    # Zero July incidents among training cells: the unpenalized m_7 runs to -inf (separation).
    cells, counts = synthetic_problem(seed=14)
    mask = cells.state_idx[cells.area_idx] != 0
    y = np.where(mask & (cells.month == 7), 0.0, counts.y)
    with pytest.raises(ModelFitError, match="did not converge"):
        fit_model(cells, dataclasses.replace(counts, y=y), ModelParams(), train_mask=mask)
    # The held-out July counts are not training data, so they cannot rescue the fit.
    y_held = y.copy()
    y_held[~mask & (cells.month == 7)] += 5.0
    with pytest.raises(ModelFitError):
        fit_model(cells, dataclasses.replace(counts, y=y_held), ModelParams(), train_mask=mask)


def _border_problem():
    cells, counts = synthetic_problem(seed=15)
    # Move one area to another state while its MP path keeps it in the first state's region.
    area = 0
    other = (int(cells.state_idx[area]) + 1) % len(cells.states)
    state_idx = cells.state_idx.copy()
    state_idx[area] = other
    return dataclasses.replace(cells, state_idx=state_idx), counts, area


def test_region_key_is_state_qualified_so_border_regions_nest():
    cells, counts, area = _border_problem()
    ec = eb_cells(cells)
    for r in range(len(ec.regions)):
        assert np.unique(ec.state_idx[ec.region_idx == r]).size == 1
    assert ec.regions[int(ec.region_idx[area])].startswith(cells.states[int(cells.state_idx[area])] + "|")
    fit = fit_model(cells, counts, ModelParams())
    assert np.all(np.isfinite(fit.predict(cells, counts.y)[1].post.mean))


def test_unqualified_regions_would_not_nest():
    cells, counts, _ = _border_problem()
    with pytest.raises(ValueError, match="parent rate varies"):
        from app.ml.eb import fit_eb

        fit_eb(cells, counts.y, np.full(cells.n, counts.y.mean()), np.full(cells.area_id.size, -1))


def test_area_without_features_gets_no_archetype_and_no_neighbour():
    cells, counts = synthetic_problem(seed=16)
    area = 3
    area_static = cells.area_static.copy()
    area_static[area] = np.nan
    static = np.where((cells.area_idx == area)[:, None], np.nan, cells.static)
    cells = dataclasses.replace(cells, area_static=area_static, static=static)
    fit = fit_model(cells, counts, ModelParams())
    assert fit.arch_of_area[area] == -1
    _, cp, dist, _ = fit.predict(cells, counts.y)
    sel = cells.area_idx == area
    assert np.all(cp.chain["omega_arch"][sel] == 0.0) and np.all(cp.chain["O_arch"][sel] == 0.0)
    assert np.all(np.isinf(dist[sel]))
    assert fit.diagnostics["areas_without_archetype"] == 1


def test_evidence_points_come_from_training_cells_only():
    cells, counts = synthetic_problem(seed=17)
    mask = cells.state_idx[cells.area_idx] != 0
    fit = fit_model(cells, counts, ModelParams(), train_mask=mask)
    for g in range(len(TYPE_GROUPS)):
        held = np.unique(cells.area_idx[~mask & (cells.g == g) & (counts.y > 0)])
        train = np.unique(cells.area_idx[mask & (cells.g == g) & (counts.y > 0)])
        assert fit.ev_points[g].shape[0] == train.size
        if held.size:
            Zheld = fit.std.apply(model_mod.area_feature_matrix(cells))[held]
            for row in Zheld:
                assert not any(np.array_equal(row, p, equal_nan=True) for p in fit.ev_points[g])


def test_spatial_fold_mask_keeps_held_out_states_out_of_training():
    cells, counts = synthetic_problem(seed=18)
    fold = next(f for f in spatial_folds(cutoff=__import__("datetime").date(2026, 1, 1))
                if f.held_out_states & set(cells.states))
    mask = fold_train_mask(cells, fold)
    held = np.isin(np.array(cells.states)[cells.state_idx[cells.area_idx]], sorted(fold.held_out_states))
    np.testing.assert_array_equal(mask, ~held)
    assert held.any() and mask.any()
    fit = fit_model(cells, counts, ModelParams(), train_mask=mask)
    bumped = dataclasses.replace(counts, y=np.where(held, counts.y + 50.0, counts.y))
    fit2 = fit_model(cells, bumped, ModelParams(), train_mask=mask)
    np.testing.assert_allclose(fit.glm.coef, fit2.glm.coef)
    assert fit.eb.kappa_cell == pytest.approx(fit2.eb.kappa_cell)
    zero = np.zeros(cells.n)
    np.testing.assert_allclose(fit.predict(cells, zero)[1].post.mean[held],
                               fit2.predict(cells, zero)[1].post.mean[held])


def test_bundle_round_trips_and_carries_the_contract():
    cells, counts = synthetic_problem(seed=19)
    fit = fit_model(cells, counts, ModelParams())
    bundle = fit.to_bundle("eb-test", TH, cells)
    again = ModelBundle.from_bytes(bundle.to_bytes())
    assert again.family == "eb_v1" and again.version == "eb-test"
    assert set(again.arrays) == {"coef", "center", "scale", "arch_centers", "arch_weights", "std_mean", "std_sd",
                                 "log_rref", "hazard_center"}
    for k, v in bundle.arrays.items():
        np.testing.assert_array_equal(again.arrays[k], v)
    assert again.arrays["log_rref"].shape == (6, 12)
    man = again.manifest
    assert man["thresholds"]["d_star"] is None and man["thresholds"]["conf_medium"] is None
    assert man["names"] == list(fit.design.names)
    for key in ("tick_ref", "families_on", "const", "params", "years", "kappas", "notes", "diagnostics"):
        assert key in man
    present = {(int(g), int(m)) for g, m in zip(cells.g, cells.month)}
    for gi, g in enumerate(TYPE_GROUPS):
        for m in range(1, 13):
            if (gi, m) in present:
                assert man["const"][g][str(m)] == pytest.approx(-again.arrays["log_rref"][gi, m - 1])
            else:
                assert str(m) not in man["const"][g] and np.isnan(again.arrays["log_rref"][gi, m - 1])
    diag = man["diagnostics"]
    assert {"glm_iterations", "kappa", "areas_per_region", "dropped_by_reason", "median_route"} <= set(diag)
    assert {"type_season", "evidence"} <= set(diag["median_route"])


def test_parity_check_refuses_a_tampered_bundle():
    cells, counts = synthetic_problem(seed=20)
    fit = fit_model(cells, counts, ModelParams())
    bundle = fit.to_bundle("v", TH, cells)
    s = fit.summarize(cells, counts.y, TH)
    cp = fit.predict(cells, counts.y)[1]
    _, mean, _ = interval90(cp.post.shape, cp.post.rate, TH.phi)
    check_parity(bundle, cells, s, mean)
    center = bundle.arrays["center"].copy()
    center[bundle.manifest["names"].index("z_elevation_m")] += 0.5
    tampered = dataclasses.replace(bundle, arrays={**bundle.arrays, "center": center})
    with pytest.raises(BundleParityError):
        check_parity(tampered, cells, s, mean)


def test_to_bundle_refuses_when_explain_disagrees(monkeypatch):
    cells, counts = synthetic_problem(seed=21)
    fit = fit_model(cells, counts, ModelParams())
    real = model_mod.explain

    def skewed(*args, **kwargs):
        terms = real(*args, **kwargs)
        return terms[:-2] + [Term(terms[-2].name, terms[-2].label, terms[-2].log_effect + 0.01),
                             Term(terms[-1].name, terms[-1].label, terms[-1].log_effect - 0.01)]

    monkeypatch.setattr(model_mod, "explain", skewed)
    with pytest.raises(BundleParityError):
        fit.to_bundle("v", TH, cells)


def test_predict_refuses_a_different_area_table():
    cells, counts = synthetic_problem(seed=22)
    fit = fit_model(cells, counts, ModelParams())
    other, _ = synthetic_problem(seed=22, n_areas=40)
    with pytest.raises(ValueError, match="area table"):
        fit.predict(other, np.zeros(other.n))


def test_summary_scores_only_with_evidence():
    cells, counts = synthetic_problem(seed=23)
    mask = cells.state_idx[cells.area_idx] != 0
    fit = fit_model(cells, counts, ModelParams(), train_mask=mask)
    s = fit.summarize(cells, np.where(mask, counts.y, 0.0), TH)
    ok = ~s.insufficient
    assert np.all(np.isfinite(s.rr_mean[ok])) and np.all(np.isnan(s.rr_mean[~ok]))
    # A held-out state has no own incidents and no geo mass: archetype evidence alone never reads "high".
    assert "high" not in set(s.conf_level[~mask])
    assert all(math.isnan(v) for v in s.n_eff_geo[~ok])


def test_bad_inputs_raise():
    cells, counts = synthetic_problem(seed=24)
    with pytest.raises(ValueError):
        fit_model(cells, counts, ModelParams(), train_mask=np.ones(cells.n))
    with pytest.raises(ValueError):
        fit_model(cells, counts, ModelParams(), train_mask=np.zeros(cells.n, dtype=bool))
    with pytest.raises(ValueError):
        ModelParams(arch_weights=(1.0,) * 3)
    sub = cells.subset(cells.month == 1)
    assert sub.n == int((cells.month == 1).sum()) and all(sub.month == 1)
    assert all(sub.index[k] < sub.n for k in sub.index) and len(sub.index) == sub.n
