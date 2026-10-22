"""Validation gate and promotion report (P3 M9, A:154-173). Fail closed: a missing, None or non-finite
metric fails its component and is listed under the component's "missing", never read as 0. The public
issue is aggregate-only (DP16); per-route movers stay in model.registry.diff_report.

`active` holds metrics of the active version refit on the current folds (pre-flight F44), in the
challenger's shape ({"main": report, ...}); for the kernel row a bare `kernel_report` is also read as
its "main". Every value here is JSON-ready with allow_nan=False."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.ml.tuning import STABLE_CHURN, STABLE_SPEARMAN
from app.services.risk_bands import color_code_for

BAND_SHIFT_ALERT = 0.05
COVERAGE_SHIFT_ALERT = 10.0
WALL_LIMIT_HOURS = 5.5
RSS_LIMIT_GB = 16.0
MAX_MOVERS = 25
NO_PROSPECTIVE = "no_test_incidents"
Row = tuple[int, str, str, float | None]  # route_id, type_group, state_code, percentile (None = insufficient)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    components: dict[str, dict[str, Any]]
    active_family: str = ""
    not_gated: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DiffReport:
    """band_shift and coverage_shift_points are None when no route can be compared (missing, not 0);
    large_shift is then True, since a shift cannot be ruled out."""

    band_shift: float | None
    coverage_shift_points: float | None
    new_family: bool
    large_shift: bool
    matrix: dict[str, int]
    per_group: dict[str, dict[str, int]]
    per_state: dict[str, dict[str, float]]
    metric_deltas: dict[str, Any] = field(default_factory=dict)
    movers: list[dict[str, Any]] = field(default_factory=list)


def _get(d: Any, *path: str) -> Any:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float, np.integer, np.floating)):
        return None
    v = float(x)
    return v if math.isfinite(v) else None


class _Inputs:
    """Reads gate inputs and records each one that is absent or not a finite number."""

    def __init__(self, sides: dict[str, Any]) -> None:
        self.sides = sides
        self.missing: list[str] = []

    def num(self, side: str, *path: str) -> float | None:
        v = _num(_get(self.sides[side], *path))
        if v is None:
            self.missing.append(".".join((side, *path)))
        return v

    def flag(self, side: str, *path: str) -> bool | None:
        v = _get(self.sides[side], *path)
        if not isinstance(v, bool):
            self.missing.append(".".join((side, *path)))
            return None
        return v

    def oe(self, side: str, *path: str) -> float | None:
        ci = _get(self.sides[side], *path)
        v = _num(ci[1]) if isinstance(ci, (list, tuple)) and len(ci) == 3 else None
        if v is None:
            self.missing.append(".".join((side, *path)))
        return v

    def comp(self, value: Any, threshold: Any, ok: bool, marginal: bool = False) -> dict[str, Any]:
        miss, self.missing = self.missing, []
        return {"passed": bool(ok) and not miss, "marginal": bool(marginal), "value": value,
                "threshold": threshold, "missing": miss}


def _ci_lo(ci: Any) -> float | None:
    if not isinstance(ci, (list, tuple)) or len(ci) < 2:
        return None
    return _num(ci[0])


def evaluate_gate(ch: dict[str, Any], active: dict[str, Any] | None, *, active_family: str) -> GateResult:
    act_main = _get(active, "main")
    if act_main is None and active_family == "kernel" and isinstance(active, dict):
        act_main = active
    r = _Inputs({"challenger": ch, "active": {"main": act_main}})
    c: dict[str, dict[str, Any]] = {}
    for kind in ("spatial", "rolling"):
        mine = r.num("challenger", "main", "by_kind", kind, "log_score")
        theirs = r.num("active", "main", "by_kind", kind, "log_score")
        lo = _ci_lo(_get(ch, "vs_active", kind, "log_score_ci"))
        # Without a CI a gain cannot be called clear, so it is flagged; marginal never fails (M9).
        c[f"{kind}_log_score"] = r.comp(mine, theirs, mine is not None and theirs is not None and mine > theirs,
                                        marginal=lo is None or lo <= 0.0)
        mt = r.num("challenger", "main", "by_kind", kind, "top10")
        tt = r.num("active", "main", "by_kind", kind, "top10")
        c[f"{kind}_top10"] = r.comp(mt, tt, mt is not None and tt is not None and mt >= tt)

    scheme = _get(ch, "main", "calibration", "scheme")
    if not isinstance(scheme, str):
        r.missing.append("challenger.main.calibration.scheme")
    need = {"decile": 8, "quintile": 4}.get(scheme) if isinstance(scheme, str) else None
    n_pass = r.num("challenger", "main", "calibration", "n_pass")
    cal_ok = r.flag("challenger", "main", "calibration", "passed")
    c["calibration"] = r.comp(n_pass, need, cal_ok is True and need is not None and n_pass is not None
                              and n_pass >= need, marginal=n_pass is not None and n_pass == need)

    m_ls, g_ls = r.num("challenger", "main", "log_score"), r.num("challenger", "geo_only", "log_score")
    m_np = r.num("challenger", "main", "calibration", "n_pass")
    g_np = r.num("challenger", "geo_only", "calibration", "n_pass")
    c["g1_pooling"] = r.comp({"log_score": m_ls, "n_pass": m_np}, {"log_score": g_ls, "n_pass": g_np},
                             None not in (m_ls, g_ls, m_np, g_np)
                             and m_ls >= g_ls and m_np >= g_np)  # type: ignore[operator]

    zm, zg = r.oe("challenger", "main", "zero_history", "oe"), r.oe("challenger", "geo_only", "zero_history", "oe")
    c["g2_zero_history"] = r.comp(zm, zg, zm is not None and zg is not None and abs(zm - 1) <= abs(zg - 1))

    m_ls, g_ls = r.num("challenger", "main", "log_score"), r.num("challenger", "geo_only", "log_score")
    p_ls = r.num("challenger", "permuted", "log_score")
    if m_ls is not None and g_ls is not None and p_ls is not None:
        gain, p_gain = m_ls - g_ls, p_ls - g_ls
        c["g3_negative_control"] = r.comp(p_gain, 0.5 * gain, gain > 0 and p_gain < 0.5 * gain)
    else:
        c["g3_negative_control"] = r.comp(None, None, False)

    selected = r.flag("challenger", "thresholds", "conf_selected")
    hi, med = r.num("challenger", "thresholds", "conf_high"), r.num("challenger", "thresholds", "conf_medium")
    c["g4_confidence"] = r.comp({"conf_high": hi, "conf_medium": med}, "selected",
                                selected is True and hi is not None and med is not None)

    st_ok = r.flag("challenger", "stability", "passed")
    rho = r.num("challenger", "stability", "median_spearman")
    churn = r.num("challenger", "stability", "median_band_churn")
    c["stability"] = r.comp({"median_spearman": rho, "median_band_churn": churn},
                            {"median_spearman": STABLE_SPEARMAN, "median_band_churn": STABLE_CHURN},
                            st_ok is True and rho is not None and churn is not None
                            and rho >= STABLE_SPEARMAN and churn <= STABLE_CHURN)

    wall, rss = r.num("challenger", "capacity", "wall_hours"), r.num("challenger", "capacity", "peak_rss_gb")
    c["capacity"] = r.comp({"wall_hours": wall, "peak_rss_gb": rss},
                           {"wall_hours": WALL_LIMIT_HOURS, "peak_rss_gb": RSS_LIMIT_GB},
                           wall is not None and rss is not None and wall < WALL_LIMIT_HOURS and rss < RSS_LIMIT_GB)

    not_gated: dict[str, str] = {}
    if "prospective" in ch and ch["prospective"] is None and ch.get("prospective_reason") == NO_PROSPECTIVE:
        not_gated["prospective_top10"] = NO_PROSPECTIVE  # DP14: gated once the window holds test incidents
    else:
        pm = r.num("challenger", "prospective", "top10")
        pk = r.num("challenger", "prospective_kernel", "top10")
        c["prospective_top10"] = r.comp(pm, pk, pm is not None and pk is not None and pm >= pk)

    return GateResult(all(v["passed"] for v in c.values()), c, active_family, not_gated)


def _pct(p: float | None) -> float | None:
    return None if p is None or not math.isfinite(p) else float(p)


def _band(p: float | None) -> str:
    return "insufficient" if p is None else color_code_for(round(p, 1))


def _coverage(rows: Sequence[Row]) -> float | None:
    return sum(_pct(r[3]) is not None for r in rows) / len(rows) if rows else None


def diff_report(challenger_rows: Sequence[Row], active_rows: Sequence[Row], *, reference_month: int,
                new_family: bool) -> DiffReport:
    old = {r[0]: _pct(r[3]) for r in active_rows}
    matrix: Counter[str] = Counter()
    per_group: dict[str, Counter[str]] = defaultdict(Counter)
    deltas: dict[str, list[float]] = defaultdict(list)
    movers: list[dict[str, Any]] = []
    changed = both = 0
    for rid, g, st, raw in challenger_rows:
        if rid not in old:
            continue
        p, q = _pct(raw), old[rid]
        key = f"{_band(q)}->{_band(p)}"
        matrix[key] += 1
        per_group[g][key] += 1
        both += 1
        changed += _band(q) != _band(p)
        if p is not None and q is not None:
            deltas[st].append(p - q)
            movers.append({"route_id": rid, "type_group": g, "old": q, "new": p, "month": reference_month})
    new_cov, old_cov = _coverage(challenger_rows), _coverage(active_rows)
    cov_shift = None if new_cov is None or old_cov is None else 100.0 * (new_cov - old_cov)
    band_shift = changed / both if both else None
    large = (new_family or band_shift is None or cov_shift is None or band_shift > BAND_SHIFT_ALERT
             or abs(cov_shift) > COVERAGE_SHIFT_ALERT)
    return DiffReport(
        band_shift=band_shift, coverage_shift_points=cov_shift, new_family=new_family, large_shift=large,
        matrix=dict(matrix), per_group={g: dict(v) for g, v in per_group.items()},
        per_state={s: {"routes": float(len(d)), "mean_delta": float(np.mean(d))} for s, d in sorted(deltas.items())},
        movers=sorted(movers, key=lambda m: -abs(m["new"] - m["old"]))[:MAX_MOVERS],
    )


def _shown(x: Any) -> Any:
    """Display form for the issue: non-finite is null, floats keep 4 significant digits."""
    if isinstance(x, dict):
        return {str(k): _shown(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_shown(v) for v in x]
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    n = _num(x)
    return None if n is None else float(f"{n:.4g}")


def _cell(v: Any) -> str:
    return json.dumps(_shown(v), allow_nan=False)[:80]


def _reasons(diff: DiffReport) -> list[str]:
    out = []
    if diff.band_shift is None or diff.coverage_shift_points is None:
        out.append("no comparable routes, so the shift is unknown")
    if diff.band_shift is not None and diff.band_shift > BAND_SHIFT_ALERT:
        out.append(f"band shift {100 * diff.band_shift:.1f}%")
    if diff.coverage_shift_points is not None and abs(diff.coverage_shift_points) > COVERAGE_SHIFT_ALERT:
        out.append(f"coverage shift {diff.coverage_shift_points:+.1f} points")
    if diff.new_family:
        out.append("first version of a new model family")
    return out


def _gate_rows(gate: GateResult) -> list[str]:
    rows = []
    for k, v in gate.components.items():
        value = f"missing: {', '.join(v['missing'])}" if v["missing"] else _cell(v["value"])
        rows.append(f"| {k} | {v['passed']} | {v['marginal']} | {value} | {_cell(v['threshold'])} |")
    rows += [f"| {k} | not gated: {why} | | | |" for k, why in gate.not_gated.items()]
    return rows


def _delta(v: Any) -> str:
    n = _num(v)
    return "missing" if n is None else f"{n:+.4f}"


def issue_markdown(version: str, gate: GateResult, diff: DiffReport, *, public: bool = True) -> str:
    """With public=True nothing per-route is rendered: only counts, per-group and per-state aggregates,
    metric deltas and the gate."""
    out: list[str] = []
    if diff.large_shift:
        out.append(f"**⚠ LARGE SHIFT** ({'; '.join(_reasons(diff))}): see the band matrix and the per-state table.")
    shift = "unknown" if diff.band_shift is None else f"{100 * diff.band_shift:.1f}%"
    cov = "unknown" if diff.coverage_shift_points is None else f"{diff.coverage_shift_points:+.1f} points"
    out += [f"## Model {version}: {'promoted' if gate.passed else 'rejected'}", "",
            f"Compared against the active {gate.active_family or 'version'}, refit on the current folds. "
            f"Band shift: {shift}. Coverage shift: {cov}.", "",
            "### Band from → to (all routes)", "| from → to | routes |", "|---|---|",
            *(f"| {k} | {v} |" for k, v in sorted(diff.matrix.items())), "",
            "### Per type group", "| group | from → to | routes |", "|---|---|---|",
            *(f"| {g} | {k} | {v} |" for g, m in sorted(diff.per_group.items()) for k, v in sorted(m.items())), "",
            "### Per state", "| state | routes compared | mean percentile change |", "|---|---|---|",
            *(f"| {s} | {int(v['routes'])} | {v['mean_delta']:+.1f} |" for s, v in diff.per_state.items()), ""]
    if diff.metric_deltas:
        out += ["### Metric deltas (challenger − active)", "| metric | delta |", "|---|---|",
                *(f"| {k} | {_delta(v)} |" for k, v in sorted(diff.metric_deltas.items())), ""]
    out += ["### Gate", "| component | passed | marginal | value | threshold |", "|---|---|---|---|---|",
            *_gate_rows(gate)]
    if gate.active_family == "kernel":
        out += ["", "spatial_log_score and rolling_log_score compare the model's predictive (NegBin) log score "
                "against the kernel's Poisson point score: not like-for-like, though they still gate."]
    if "prospective_top10" in gate.components:
        out += ["", "prospective_top10 ranks the kernel's stored prospective scores against the model's. The "
                "kernel's prospective log score uses a calibration map fit in-window, so it is not a held-out "
                "comparison and is not gated."]
    out += ["", "Rollback: the owner runs `TRAIN rollback` from the Mac (runbook, Task 21)."]
    if not public:
        out += ["", "### Biggest movers", *(f"- {m['route_id']} {m['type_group']}: {m['old']:.1f} → {m['new']:.1f}"
                                            for m in diff.movers)]
    return "\n".join(out)
