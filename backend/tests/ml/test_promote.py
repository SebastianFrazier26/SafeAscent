import copy
import json
import math
import re

import pytest

from app.ml.fixtures import SYNTHETIC_ID_FLOOR, challenger_metrics, rows_for_diff
from app.ml.promote import diff_report, evaluate_gate, issue_markdown

COMPONENTS = ("spatial_log_score", "rolling_log_score", "spatial_top10", "rolling_top10", "calibration",
              "g1_pooling", "g2_zero_history", "g3_negative_control", "g4_confidence", "stability", "capacity")
# Every leaf the gate reads, as (side, path); deleting or NaN-ing any one must fail the gate.
INPUTS = (
    ("ch", ("main", "by_kind", "spatial", "log_score")), ("ac", ("main", "by_kind", "spatial", "log_score")),
    ("ch", ("main", "by_kind", "rolling", "log_score")), ("ac", ("main", "by_kind", "rolling", "log_score")),
    ("ch", ("main", "by_kind", "spatial", "top10")), ("ac", ("main", "by_kind", "spatial", "top10")),
    ("ch", ("main", "by_kind", "rolling", "top10")), ("ac", ("main", "by_kind", "rolling", "top10")),
    ("ch", ("main", "calibration", "n_pass")), ("ch", ("main", "calibration", "passed")),
    ("ch", ("main", "calibration", "scheme")),
    ("ch", ("main", "log_score")), ("ch", ("geo_only", "log_score")), ("ch", ("permuted", "log_score")),
    ("ch", ("geo_only", "calibration", "n_pass")),
    ("ch", ("main", "zero_history", "oe")), ("ch", ("geo_only", "zero_history", "oe")),
    ("ch", ("thresholds", "conf_selected")), ("ch", ("thresholds", "conf_high")),
    ("ch", ("thresholds", "conf_medium")),
    ("ch", ("stability", "passed")), ("ch", ("stability", "median_spearman")),
    ("ch", ("stability", "median_band_churn")),
    ("ch", ("capacity", "wall_hours")), ("ch", ("capacity", "peak_rss_gb")),
)
NUMERIC = {p for _, p in INPUTS if p[-1] not in ("passed", "conf_selected", "oe", "scheme")}


def _pair():
    return challenger_metrics(), challenger_metrics(worse=True)


def _parent(d, path):
    for k in path[:-1]:
        d = d[k]
    return d


# --- gate ---------------------------------------------------------------------------------------


def test_gate_fails_on_any_component_and_passes_when_all_hold():
    good, weaker = _pair()
    result = evaluate_gate(good, weaker, active_family="eb_v1")
    assert result.passed
    assert set(result.components) == set(COMPONENTS)
    for broken in ("calibration", "g1_pooling", "g2_zero_history", "g3_negative_control", "g4_confidence",
                   "stability", "capacity"):
        result = evaluate_gate(challenger_metrics(break_=broken), weaker, active_family="eb_v1")
        assert not result.passed and not result.components[broken]["passed"]
        # the fixture breaks one component only, so each rule is pinned by its own input
        assert [k for k, v in result.components.items() if not v["passed"]] == [broken]
    assert not evaluate_gate(good, None, active_family="kernel").passed  # nothing to beat: fail closed


def test_comparisons_need_the_challenger_to_beat_active():
    good = challenger_metrics()
    same = evaluate_gate(good, challenger_metrics(), active_family="eb_v1")
    # equal log score is not a gain; equal top10 is enough (>=)
    assert not same.components["spatial_log_score"]["passed"]
    assert same.components["spatial_top10"]["passed"]
    better = evaluate_gate(challenger_metrics(worse=True), good, active_family="eb_v1")
    assert not better.passed
    assert not better.components["rolling_top10"]["passed"]


@pytest.mark.parametrize("side,path", INPUTS, ids=[".".join((s, *p)) for s, p in INPUTS])
def test_missing_input_fails_closed(side, path):
    ch, ac = _pair()
    del _parent(ch if side == "ch" else ac, path)[path[-1]]
    result = evaluate_gate(ch, ac, active_family="eb_v1")
    assert not result.passed
    failed = [v for v in result.components.values() if not v["passed"]]
    assert failed and any(m.endswith(".".join(path)) for v in failed for m in v["missing"])


@pytest.mark.parametrize("side,path", sorted(INPUTS, key=str), ids=lambda x: str(x))
def test_nan_or_none_never_passes(side, path):
    for bad in (float("nan"), None, math.inf if path in NUMERIC else "yes"):
        ch, ac = _pair()
        _parent(ch if side == "ch" else ac, path)[path[-1]] = bad
        assert not evaluate_gate(ch, ac, active_family="eb_v1").passed, (path, bad)


def test_whole_sections_missing_fail_closed():
    for key in ("main", "geo_only", "permuted", "thresholds", "stability", "capacity"):
        ch, ac = _pair()
        del ch[key]
        assert not evaluate_gate(ch, ac, active_family="eb_v1").passed, key
    ch, ac = _pair()
    assert not evaluate_gate(ch, {}, active_family="eb_v1").passed


def test_stability_and_capacity_rechecked_against_limits():
    ch, ac = _pair()
    ch["stability"]["median_band_churn"] = 0.11  # STABLE_CHURN stays 0.10 even if passed says True
    assert not evaluate_gate(ch, ac, active_family="eb_v1").components["stability"]["passed"]
    ch, ac = _pair()
    ch["capacity"]["peak_rss_gb"] = 16.0  # hard limit is strict
    assert not evaluate_gate(ch, ac, active_family="eb_v1").components["capacity"]["passed"]


def test_g4_needs_both_thresholds_not_just_the_flag():
    ch, ac = _pair()
    ch["thresholds"]["conf_medium"] = None
    assert not evaluate_gate(ch, ac, active_family="eb_v1").components["g4_confidence"]["passed"]


def test_marginal_is_reported_never_held():
    ch, ac = _pair()
    ch["vs_active"]["spatial"]["log_score_ci"] = [-0.01, 0.03, 0.07]
    r = evaluate_gate(ch, ac, active_family="eb_v1")
    assert r.passed and r.components["spatial_log_score"]["marginal"]
    assert not r.components["rolling_log_score"]["marginal"]
    del ch["vs_active"]
    r = evaluate_gate(ch, ac, active_family="eb_v1")
    assert r.passed and r.components["rolling_log_score"]["marginal"]  # no CI: cannot claim a clear gain


def test_kernel_active_lacking_a_metric_fails_that_component():
    ch = challenger_metrics()
    kernel = {"log_score": -0.7, "top10": None, "coverage": None,
              "by_kind": {k: {"log_score": -0.7, "top10": None} for k in ("spatial", "rolling")}}
    r = evaluate_gate(ch, kernel, active_family="kernel")
    assert not r.passed
    assert r.components["spatial_log_score"]["passed"] and r.components["rolling_log_score"]["passed"]
    assert not r.components["spatial_top10"]["passed"] and not r.components["rolling_top10"]["passed"]
    kernel["by_kind"] = {k: {"log_score": -0.7, "top10": 0.2} for k in ("spatial", "rolling")}
    assert evaluate_gate(ch, kernel, active_family="kernel").passed
    assert evaluate_gate(ch, {"main": kernel}, active_family="kernel").passed
    # a bare report is accepted only as the kernel's; a refit version's metrics nest under main
    assert not evaluate_gate(ch, kernel, active_family="eb_v1").passed


def test_prospective_is_gated_only_once_it_exists():
    ch, ac = _pair()
    r = evaluate_gate(ch, ac, active_family="eb_v1")
    assert "prospective_top10" not in r.components and r.not_gated == {"prospective_top10": "no_test_incidents"}
    ch["prospective"] = {"top10": 0.5}
    r = evaluate_gate(ch, ac, active_family="eb_v1")
    assert not r.passed and r.components["prospective_top10"]["missing"]  # no kernel side: fail closed
    ch["prospective_kernel"] = {"top10": 0.6}
    assert not evaluate_gate(ch, ac, active_family="eb_v1").passed
    ch["prospective_kernel"] = {"top10": 0.5}
    r = evaluate_gate(ch, ac, active_family="eb_v1")
    assert r.passed and r.not_gated == {}
    ch["prospective"] = {"top10": None}  # exists but NaN-serialized top10 is missing, not a pass
    assert not evaluate_gate(ch, ac, active_family="eb_v1").passed


def test_prospective_absent_without_reason_fails_closed():
    for mutate in (lambda m: m.pop("prospective"), lambda m: m.update(prospective_reason=None)):
        ch, ac = _pair()
        mutate(ch)
        r = evaluate_gate(ch, ac, active_family="eb_v1")
        assert not r.passed and not r.components["prospective_top10"]["passed"]


# --- diff report --------------------------------------------------------------------------------


def test_large_shift_banner_rules():
    ch, ac = rows_for_diff(changed_share=0.06)
    d = diff_report(ch, ac, reference_month=10, new_family=False)
    assert d.large_shift and "LARGE SHIFT" in issue_markdown("v", evaluate_gate(challenger_metrics(), None,
                                                                                active_family="kernel"), d)
    ch, ac = rows_for_diff(changed_share=0.02, coverage_shift=0.11)
    d = diff_report(ch, ac, reference_month=10, new_family=False)
    assert d.large_shift and d.band_shift <= 0.05 and d.coverage_shift_points < -10.0
    ch, ac = rows_for_diff(changed_share=0.0)
    assert diff_report(ch, ac, reference_month=10, new_family=True).large_shift


def test_small_shift_has_no_banner():
    ch, ac = rows_for_diff(changed_share=0.05)
    d = diff_report(ch, ac, reference_month=10, new_family=False)
    assert d.band_shift == pytest.approx(0.05) and d.coverage_shift_points == 0.0 and not d.large_shift
    gate = evaluate_gate(*_pair(), active_family="eb_v1")
    assert "LARGE SHIFT" not in issue_markdown("v", gate, d)


def test_diff_tables_count_band_moves():
    ch, ac = rows_for_diff(changed_share=0.1, n=400)
    d = diff_report(ch, ac, reference_month=10, new_family=False)
    assert sum(d.matrix.values()) == 400
    assert d.matrix["green->yellow"] == 10 and d.matrix["red->green"] == 10
    assert sum(n for m in d.per_group.values() for n in m.values()) == 400
    assert sum(v["routes"] for v in d.per_state.values()) == 400
    assert len(d.movers) == 25 and abs(d.movers[0]["new"] - d.movers[0]["old"]) == 75.0


def test_insufficient_is_a_band_and_nan_is_insufficient():
    rid = SYNTHETIC_ID_FLOOR
    d = diff_report([(rid, "trad", "WA", float("nan")), (rid + 1, "trad", "WA", 30.0)],
                    [(rid, "trad", "WA", 30.0), (rid + 1, "trad", "WA", None)], reference_month=1, new_family=False)
    assert d.matrix == {"yellow->insufficient": 1, "insufficient->yellow": 1}
    assert d.band_shift == 1.0 and d.coverage_shift_points == 0.0 and d.movers == []


def test_no_comparable_routes_is_missing_not_zero():
    d = diff_report([], [], reference_month=7, new_family=False)
    assert d.band_shift is None and d.coverage_shift_points is None and d.large_shift
    md = issue_markdown("v", evaluate_gate(*_pair(), active_family="eb_v1"), d)
    assert "LARGE SHIFT" in md and "no comparable routes" in md


# --- issue --------------------------------------------------------------------------------------


def test_public_issue_never_carries_route_ids():
    ch, ac = rows_for_diff(changed_share=0.2)
    d = diff_report(ch, ac, reference_month=10, new_family=False)
    md = issue_markdown("v", evaluate_gate(challenger_metrics(), None, active_family="kernel"), d)
    assert d.movers and not any(str(m["route_id"]) in md for m in d.movers)
    assert "9000" not in md
    assert not re.search(r"\d{6,}", md)  # no id-length number of any kind
    private = issue_markdown("v", evaluate_gate(challenger_metrics(), None, active_family="kernel"), d,
                             public=False)
    assert str(d.movers[0]["route_id"]) in private  # the public path is what drops them


def test_issue_points_rollback_at_the_runbook():
    md = issue_markdown("v", evaluate_gate(challenger_metrics(), None, active_family="kernel"),
                        diff_report([], [], reference_month=7, new_family=True))
    assert "/rollback" not in md and "TRAIN rollback" in md


def test_issue_reports_missing_and_nan_as_null_never_raises():
    ch, ac = _pair()
    ch["stability"]["median_spearman"] = float("nan")
    del ch["capacity"]["wall_hours"]
    gate = evaluate_gate(ch, ac, active_family="eb_v1")
    d = diff_report(*rows_for_diff(changed_share=0.0), reference_month=10, new_family=False)
    d = type(d)(**{**d.__dict__, "metric_deltas": {"main.log_score": 0.05, "main.top10": float("nan")}})
    md = issue_markdown("v", gate, d)
    assert "rejected" in md and "NaN" not in md and "Infinity" not in md
    assert "missing: challenger.capacity.wall_hours" in md
    assert "| main.top10 | missing |" in md and "| main.log_score | +0.0500 |" in md
    assert "not gated: no_test_incidents" in md


def test_issue_labels_prospective_kernel_comparison():
    ch, ac = _pair()
    ch["prospective"], ch["prospective_kernel"] = {"top10": 0.5}, {"top10": 0.4}
    md = issue_markdown("v", evaluate_gate(ch, ac, active_family="eb_v1"),
                        diff_report(*rows_for_diff(changed_share=0.0), reference_month=10, new_family=False))
    assert "in-window" in md and "not a held-out" in md


def test_gate_result_is_json_ready():
    ch, ac = _pair()
    ch["main"]["by_kind"]["spatial"]["top10"] = float("nan")
    gate = evaluate_gate(ch, ac, active_family="eb_v1")
    json.dumps({"components": gate.components}, allow_nan=False)
    deep = copy.deepcopy(gate.components)
    assert deep["spatial_top10"]["value"] is None


def test_issue_labels_kernel_log_score_comparison():
    d = diff_report(*rows_for_diff(changed_share=0.0), reference_month=10, new_family=False)
    ch = challenger_metrics()
    kernel = {"by_kind": {k: {"log_score": -0.7, "top10": 0.2} for k in ("spatial", "rolling")}}
    label = "not like-for-like"
    assert label in issue_markdown("v", evaluate_gate(ch, kernel, active_family="kernel"), d)
    assert label not in issue_markdown("v", evaluate_gate(*_pair(), active_family="eb_v1"), d)
