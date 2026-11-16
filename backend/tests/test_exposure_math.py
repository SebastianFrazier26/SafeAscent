import math
import time

import numpy as np

import pytest

from app.pipelines.exposure_math import (
    Component, _ranks, alpine_day, bounds, exposure_point, ice_day, rock_day, spearman, tick_component,
)


def test_rock_day_rule_and_unknown_inputs():
    assert rock_day(20.0, 0.0) is True
    assert rock_day(40.0, 0.0) is False and rock_day(20.0, 5.0) is False and rock_day(4.9, 0.0) is False
    assert rock_day(None, 0.0) is None and rock_day(20.0, None) is None


def test_unknown_days_are_not_false():
    assert ice_day(frozen=7, known=7) is True
    assert ice_day(frozen=6, known=10) is False
    assert ice_day(frozen=6, known=9) is None
    assert ice_day(frozen=0, known=4) is False
    assert ice_day(frozen=1, known=4) is None
    assert alpine_day(True, None) is True and alpine_day(False, None) is None and alpine_day(False, False) is False


def test_component_json_keeps_zero_and_missing_apart():
    assert Component.present(0.0).to_json() == {"value": 0.0, "missing": False}
    assert Component.absent("disabled").to_json() == {"value": None, "missing": True, "reason": "disabled"}
    assert Component.present(2.0, coverage=0.5).get("coverage") == 0.5
    with pytest.raises(ValueError, match="finite"):
        Component.present(math.nan)
    with pytest.raises(ValueError, match="finite"):
        Component.absent("x", coverage=math.inf)


def test_partial_coverage_is_raw_never_scaled():
    c = tick_component(n_routes=3, n_complete=1, n_scraped=1, n_missing=1, window_ticks=10.0)
    assert c.value == 2.0
    assert c.to_json() == {"value": 2.0, "missing": False, "coverage": 1 / 3, "n_complete": 1.0, "n_scraped": 1.0,
                           "n_missing": 1.0, "per_route_mean": 2.0, "window_months": 60.0}


def test_no_complete_route_is_missing_with_coverage_zero():
    c = tick_component(n_routes=2, n_complete=0, n_scraped=1, n_missing=1, window_ticks=0.0)
    assert c.to_json() == {"value": None, "missing": True, "reason": "no_complete_routes", "coverage": 0.0,
                           "n_complete": 0.0, "n_scraped": 1.0, "n_missing": 1.0}


def test_complete_routes_with_no_ticks_are_a_measured_zero():
    assert tick_component(n_routes=1, n_complete=1, n_scraped=0, n_missing=0, window_ticks=0.0).value == 0.0


def test_status_counts_must_add_up():
    with pytest.raises(ValueError, match="add up"):
        tick_component(n_routes=3, n_complete=1, n_scraped=0, n_missing=0, window_ticks=1.0)


def test_point_and_bounds_never_floor_a_missing_input():
    assert exposure_point(10.0, 0.5) == 5.0 and exposure_point(10.0, 0.0) == 0.0
    assert exposure_point(10.0, None) is None and exposure_point(None, 0.5) is None
    assert bounds(9.0) == (3.0, 27.0) and bounds(0.0) == (0.0, 0.0) and bounds(None) == (None, None)


def test_spearman_ties_and_degenerate_input():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 2, 3], [3, 2, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None and spearman([1, 2], [1, 2]) is None


def test_spearman_tie_value():
    assert spearman([1, 2, 2, 3, 4], [2, 1, 4, 3, 5]) == pytest.approx(0.66689, abs=1e-5)


def test_ranks_average_ties():
    assert list(_ranks([3.0, 1.0, 2.0, 2.0, 2.0])) == [4.0, 0.0, 2.0, 2.0, 2.0]


def test_ranks_200k_under_two_seconds():
    rng = np.random.default_rng(0)
    v = rng.integers(0, 50_000, 200_000).astype(float)
    t0 = time.perf_counter()
    r = _ranks(v)
    assert time.perf_counter() - t0 < 2.0
    assert r.sum() == pytest.approx(200_000 * 199_999 / 2)


def test_spearman_nonfinite_is_none():
    assert spearman([1, 2, math.nan, 4], [1, 2, 3, 4]) is None
    assert spearman([1, 2, 3, 4], [1, math.inf, 3, 4]) is None


def test_rock_day_nonfinite_is_none():
    assert rock_day(math.nan, 0.0) is None and rock_day(20.0, math.inf) is None


@pytest.mark.parametrize("kw", [
    dict(n_routes=2, n_complete=-1, n_scraped=2, n_missing=1, window_ticks=1.0),
    dict(n_routes=1, n_complete=1, n_scraped=0, n_missing=0, window_ticks=-1.0),
    dict(n_routes=-1, n_complete=0, n_scraped=0, n_missing=-1, window_ticks=0.0),
])
def test_tick_component_rejects_negatives(kw):
    with pytest.raises(ValueError, match=">= 0"):
        tick_component(**kw)


def test_tick_component_zero_routes():
    with pytest.raises(ValueError, match="at least one route"):
        tick_component(n_routes=0, n_complete=0, n_scraped=0, n_missing=0, window_ticks=0.0)


@pytest.mark.parametrize("frozen,known", [(-1, 5), (6, 5), (5, 11)])
def test_ice_day_rejects_impossible_counts(frozen, known):
    with pytest.raises(ValueError):
        ice_day(frozen=frozen, known=known)


def test_component_invariants_and_reserved_keys():
    with pytest.raises(ValueError):
        Component(1.0, True, "x")
    with pytest.raises(ValueError):
        Component(None, False)
    with pytest.raises(ValueError):
        Component(math.nan, False)
    for k in ("value", "missing", "reason"):
        with pytest.raises(ValueError, match="reserved"):
            Component.present(1.0, **{k: 1.0})
        with pytest.raises(ValueError, match="reserved"):
            Component.absent("x", **{k: 1.0})


def test_exposure_point_nonfinite_and_negative():
    assert exposure_point(math.nan, 0.5) is None and exposure_point(1.0, math.inf) is None
    with pytest.raises(ValueError):
        exposure_point(-1.0, 0.5)
    with pytest.raises(ValueError):
        exposure_point(1.0, -0.1)
