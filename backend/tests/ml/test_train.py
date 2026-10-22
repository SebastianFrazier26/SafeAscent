import dataclasses
import json
import math
from datetime import date

import numpy as np
import pytest

from app.ml import train
from app.ml.model import ModelParams
from app.ml.posterior import Thresholds


def test_fold_ci_brackets_the_weighted_mean_and_needs_two_folds():
    lo, mid, hi = train.fold_ci([0.1, 0.2, 0.3, 0.4], [1, 1, 1, 1])
    assert lo <= mid <= hi and mid == pytest.approx(0.25)
    assert train.fold_ci([0.1, 0.3], [3, 1])[1] == pytest.approx(0.15)
    assert train.fold_ci([0.5], [10]) is None
    # A fold with no comparable score is dropped, never read as a 0 difference.
    assert train.fold_ci([0.5, math.nan, 0.7], [1, 1, 1])[1] == pytest.approx(0.6)


def test_metric_deltas_are_missing_not_zero_when_a_side_lacks_the_metric():
    ch = {"main": {"log_score": -1.0, "top10": 0.3, "by_kind": {"spatial": {"log_score": -1.2, "top10": None}}}}
    act = {"main": {"log_score": -1.5, "top10": float("nan"), "by_kind": {"spatial": {"log_score": -1.0}}}}
    d = train.metric_deltas(ch, act)
    assert d["log_score"] == pytest.approx(0.5)
    assert d["top10"] is None and d["by_kind.spatial.top10"] is None and d["by_kind.rolling.log_score"] is None
    assert d["by_kind.spatial.log_score"] == pytest.approx(-0.2)
    assert train.metric_deltas(ch, None) == {}


def test_clean_turns_non_finite_into_null_and_numpy_into_python():
    out = train._clean({"a": np.float64("nan"), "b": [np.int64(3), np.inf, np.bool_(True)], 4: (1.5,)})
    assert out == {"a": None, "b": [3, None, True], "4": [1.5]}
    json.dumps(out, allow_nan=False)


def test_thresholds_and_params_round_trip_through_the_bundle_manifest_form():
    th = Thresholds(d_star=math.inf, conf_high=None, conf_medium=None, phi=1.5)
    d = train.thresholds_json(th)
    assert d["d_star"] is None
    assert train.thresholds_from_json(json.loads(json.dumps(d, allow_nan=False))) == th
    p = ModelParams(k_arch=8, arch_weights=(1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0))
    raw = dataclasses.asdict(p)
    raw["arch_weights"] = list(p.arch_weights)
    assert train.params_from_json(json.loads(json.dumps(raw))) == p


def test_new_version_never_reuses_a_taken_name():
    today = date(2026, 10, 20)
    assert train.new_version(today, frozenset()) == "eb-2026-10-20"
    assert train.new_version(today, frozenset({"eb-2026-10-20", "eb-2026-10-20.2"})) == "eb-2026-10-20.3"


def test_benchmark_fits_a_power_law_over_three_scales():
    curve = train.benchmark_curve(ModelParams(k_arch=4))
    assert len(curve["n_cells"]) == 3 and curve["n_cells"][0] < curve["n_cells"][1] < curve["n_cells"][2]
    assert all(s > 0 for s in curve["seconds"]) and math.isfinite(curve["time"]["exponent"])
    json.dumps(curve, allow_nan=False)


def test_rss_is_reported_in_gigabytes():
    assert 0.0 < train.peak_rss_gb() < 64.0
