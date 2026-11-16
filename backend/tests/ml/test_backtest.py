import dataclasses
import json
from datetime import date

import numpy as np
import pytest

from app.ml import backtest as bt
from app.ml.backtest import prepare, report, run_fold, run_suite
from app.ml.cells import TRAIN_START
from app.ml.dataset import StaleExposure
from app.ml.fixtures import synthetic_raw
from app.ml.folds import Fold, prospective_fold, rolling_folds, spatial_folds
from app.ml.metrics import interval_coverage, mean_log_score
from app.ml.model import ModelParams
from app.ml.posterior import Thresholds

TH = Thresholds(d_star=2.0, conf_high=3.0, conf_medium=8.0, phi=1.5)
CUTOFF = date(2026, 10, 1)


def test_spatial_fold_never_trains_on_held_out_states():
    raw = synthetic_raw(seed=21)
    fold = spatial_folds(date(2026, 10, 1))[0]
    fd = prepare(raw, fold)
    held = np.isin(np.array(fd.cells.states)[fd.cells.state_idx[fd.cells.area_idx]], sorted(fold.held_out_states))
    assert held.any()
    assert not (fd.train_mask & held).any() and fd.test_mask[held].all() and fd.scale_tau == 1.0
    assert not fd.test_mask[~held].any()


def test_rolling_fold_counts_split_at_origin_and_scale_by_years():
    raw = synthetic_raw(seed=22)
    fold = rolling_folds()[0]
    fd = prepare(raw, fold)
    assert fd.train.years == pytest.approx((fold.train_end - fold.train_start).days / 365.25)
    assert 0 < fd.scale_tau < 1
    assert fd.train_mask.all() and fd.test_mask.all()


def test_rolling_tau_level_reads_only_the_training_window():
    raw = synthetic_raw(seed=22)
    fold = rolling_folds()[0]
    base = prepare(raw, fold).scale_tau
    # Doubling every incident inside the test window must not move tau.
    doubled = [p if p.lo is None or p.lo < fold.test_start else dataclasses.replace(p, weight=2 * p.weight)
               for p in raw.placements]
    assert prepare(dataclasses.replace(raw, placements=doubled), fold).scale_tau == base


@pytest.mark.slow  # pre-flight F43: a full run_suite; CI covers it through `app.ml.backtest --fixture small`
def test_report_has_strata_and_pooling_beats_geo_only_on_synthetic():
    raw = synthetic_raw(seed=23)
    suite = run_suite(raw, ModelParams(), TH, cutoff=date(2026, 10, 1))
    assert {"by_kind", "by_group", "by_conf", "zero_history", "bands"} <= set(suite.main)
    assert set(suite.main["bands"]) == {"quartile_25_50_75", "p3_percentile"}
    assert suite.main["log_score"] > suite.geo_only["log_score"]
    assert suite.permuted["log_score"] < suite.main["log_score"]
    assert suite.prospective is None or "top10" in suite.prospective
    assert set(suite.sensitivity) == {"no_ski", "no_llm", "no_mp_ticks"}
    assert set(suite.main["by_kind"]) == {"spatial", "rolling"}
    assert suite.recent["oe"] is not None and isinstance(suite.recent["open_issue"], bool)
    json.dumps(suite.to_dict(), allow_nan=False)


def test_missing_backtest_snapshot_names_the_command():
    raw = synthetic_raw(seed=24, drop_cutoffs=(date(2004, 1, 1),))
    with pytest.raises(Exception, match=r"exposure build --cutoff 2004-01-01"):
        prepare(raw, rolling_folds()[0])
    with pytest.raises(StaleExposure):
        prepare(raw, rolling_folds()[0])


def _bump(fd, mask, extra=100.0):
    y = fd.train.y.copy()
    y[mask] += extra
    return dataclasses.replace(fd, train=dataclasses.replace(fd.train, y=y))


def _same_prediction(a, b):
    for f in ("shape", "rate", "scale", "mu_point", "evidence_dist", "geo_evidence", "insufficient"):
        np.testing.assert_array_equal(getattr(a, f), getattr(b, f))
    assert list(a.conf) == list(b.conf)


def test_spatial_fold_scoring_never_sees_held_out_counts():
    # Carry 1: y_own is fit.y_train (zeros on held-out cells), never the window's counts.
    raw = synthetic_raw(seed=25)
    fd = prepare(raw, spatial_folds(CUTOFF)[4])
    base = run_fold(fd, ModelParams(), TH)
    bumped = run_fold(_bump(fd, fd.test_mask), ModelParams(), TH)
    _same_prediction(base, bumped)
    assert base.zero_history.all()


def test_rolling_fold_scoring_never_sees_test_window_counts():
    raw = synthetic_raw(seed=26)
    fd = prepare(raw, rolling_folds()[1])
    base = run_fold(fd, ModelParams(), TH)
    y = fd.test.y + 50.0
    moved = run_fold(dataclasses.replace(fd, test=dataclasses.replace(fd.test, y=y)), ModelParams(), TH)
    _same_prediction(base, moved)
    np.testing.assert_array_equal(moved.obs, y)
    # A rolling cell keeps its own past, so a cell with training incidents is not zero-history.
    assert not base.zero_history[fd.train.y > 0].any()


def test_report_scores_the_widened_gamma_and_the_shown_interval():
    raw = synthetic_raw(seed=27)
    res = [run_fold(prepare(raw, f), ModelParams(), TH) for f in (spatial_folds(CUTOFF)[4], rolling_folds()[2])]
    r = report(res, phi=TH.phi)
    cat = {f: np.concatenate([getattr(x, f) for x in res]) for f in ("obs", "shape", "rate", "scale")}
    assert r["log_score"] == pytest.approx(mean_log_score(cat["obs"], cat["shape"] / TH.phi,
                                                          cat["rate"] / TH.phi, cat["scale"]))
    # Carry 3: coverage is the shown union interval, not raw PIT.
    assert r["coverage"] == pytest.approx(interval_coverage(cat["obs"], cat["shape"], cat["rate"],
                                                            cat["scale"], TH.phi))
    assert r["n_cells"] == cat["obs"].size
    assert set(r["by_conf"]) == {"high", "medium", "low", "insufficient_share"}
    for scheme in r["bands"].values():
        assert {"bands", "monotone_rise", "oe_in_range", "passed", "band_log_score"} <= set(scheme)
        assert len(scheme["bands"]) == 4
    json.dumps(r, allow_nan=False)


def test_top10_without_incidents_is_null_not_zero():
    # Carry 4: top_capture is NaN with no incidents; the report says missing (JSON null), never 0.
    raw = synthetic_raw(seed=28)
    res = run_fold(prepare(raw, spatial_folds(CUTOFF)[4]), ModelParams(), TH)
    empty = dataclasses.replace(res, obs=np.zeros_like(res.obs))
    r = report([empty], phi=TH.phi)
    assert r["top10"] is None and r["observed"] == 0.0
    assert json.loads(json.dumps(r, allow_nan=False))["top10"] is None


def test_stage1_cache_reuses_the_glm_only_for_the_same_fold_data():
    raw = synthetic_raw(seed=29)
    fd = prepare(raw, spatial_folds(CUTOFF)[4])
    cache: dict = {}
    pooled = run_fold(fd, ModelParams(), TH, stage1_cache=cache)
    assert len(cache) == 1
    geo = run_fold(fd, ModelParams(blend=False), TH, stage1_cache=cache)
    fresh = run_fold(fd, ModelParams(blend=False), TH)
    _same_prediction(geo, fresh)
    assert not np.array_equal(pooled.shape, geo.shape)


def test_prospective_is_none_with_a_reason_when_the_window_is_empty():
    raw = synthetic_raw(seed=30)
    lo, hi = prospective_fold().test_start, prospective_fold().test_end
    quiet = [p for p in raw.placements if p.lo is None or p.hi <= lo or p.lo >= hi]
    report_, reason = bt.prospective_report(dataclasses.replace(raw, placements=quiet), ModelParams(), TH)
    assert report_ is None and reason == "no_test_incidents"


def test_reweight_multiplies_by_state_and_refuses_a_missing_state():
    raw = synthetic_raw(seed=31)
    states = {p.state_code for p in raw.placements if p.state_code is not None}
    mult = {s: 2.0 for s in states} | {"CO": 0.0}
    out = bt.reweight(raw, mult)
    for a, b in zip(raw.placements, out.placements):
        if a.state_code is None:
            assert b.weight == a.weight
        else:
            assert b.weight == a.weight * mult[a.state_code]
    with pytest.raises(ValueError, match="CO"):
        bt.reweight(raw, {s: 1.0 for s in states if s != "CO"})


def test_fit_full_trains_every_cell_up_to_the_production_cutoff():
    raw = synthetic_raw(seed=32)
    fit, cells, counts = bt.fit_full(raw, ModelParams(), cutoff=None)
    assert counts.years == pytest.approx((raw.production_cutoff - TRAIN_START).days / 365.25)
    np.testing.assert_array_equal(fit.y_train, counts.y)
    assert cells.n == fit.y_train.size


def test_no_mp_ticks_blanks_every_tick_column():
    raw = synthetic_raw(seed=33)
    fold = Fold("recent", "rolling", TRAIN_START, date(2019, 1, 1), date(2019, 1, 1), date(2025, 1, 1),
                frozenset(), date(2016, 1, 1))
    with_ticks = prepare(raw, fold)
    assert np.isfinite(with_ticks.cells.ticks_per_route).any()
    fd = prepare(raw, fold, mp_ticks=False)
    for f in ("ticks_value", "ticks_per_route", "tick_coverage"):
        assert np.isnan(getattr(fd.cells, f)).all()


def test_exclude_flags_drop_flagged_incidents_from_both_windows():
    raw = synthetic_raw(seed=34)
    fold = rolling_folds()[3]
    a, b = prepare(raw, fold), prepare(raw, fold, exclude_flags=frozenset({"ski_approach"}))
    assert b.train.y.sum() < a.train.y.sum() and b.test.y.sum() < a.test.y.sum()


@pytest.mark.slow
def test_fixture_cli_passes_its_floors(capsys):
    assert bt.main(["--fixture", "small"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["passed"] is True and out["wall_seconds"] > 0
