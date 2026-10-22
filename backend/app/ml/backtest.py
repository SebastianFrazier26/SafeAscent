"""Backtest harness (P3 Task 17, P3:365-392, A:154-173): fold preparation, held-out scoring, pooled
reports with strata, the G1 geo-only and G3 permuted references, the sensitivity variants, the
prospective window and the 2019-2024 recent holdout.

Held-out scoring passes fit.y_train as y_own: zeros on a spatial fold's held-out cells, the training
window's counts on a rolling fold's cells. Test-window counts only ever reach `obs`.

Every report is JSON-ready: a metric that cannot be computed (no incidents, no cells) is None, never 0
and never NaN.

    uv run --group train python -m app.ml.backtest --fixture small   # CI floors, no DB, no network"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.special import gammaln
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ml.cells import TRAIN_START, TYPE_GROUPS
from app.ml.dataset import (
    CellData,
    Counts,
    ExposureRow,
    StaleExposure,
    StaticRow,
    assemble_cells,
    count_incidents,
    pick_snapshot,
    read_area_paths,
    read_areas,
    read_exposure,
    read_incidents,
    read_static,
    seasonal_profile,
)
from app.ml.folds import Fold, prospective_fold, rolling_folds, spatial_folds
from app.ml.incidents import AreaInfo, AreaLookup, Placement, place
from app.ml.metrics import (
    MetricsReport,
    calibration,
    interval_coverage,
    mean_log_score,
    oe_ci,
    poisson_deviance,
    top_capture,
)
from app.ml.model import Fitted, ModelParams, fit_model, fold_train_mask
from app.ml.posterior import Thresholds, summarize

F64 = NDArray[np.float64]
BoolArr = NDArray[np.bool_]
LEVEL_YEARS = 5
PERMUTE_SEED = 0
QUARTILE_EDGES = (25.0, 50.0, 75.0)
P3_EDGES = (25.0, 75.0, 90.0)
BAND_OE = (0.8, 1.25)
RECENT_OE_RANGE = (0.5, 2.0)
RECENT_FOLD = Fold("recent", "rolling", TRAIN_START, date(2019, 1, 1), date(2019, 1, 1), date(2025, 1, 1),
                   frozenset(), date(2016, 1, 1))
SENSITIVITIES: dict[str, dict[str, Any]] = {
    "no_ski": {"exclude_flags": frozenset({"ski_approach"})},
    "no_llm": {"exclude_flags": frozenset({"llm_link"})},
    "no_mp_ticks": {"mp_ticks": False},
}


@dataclass(frozen=True)
class RawData:
    placements: list[Placement]
    areas: list[AreaInfo]
    paths: dict[int, str]
    static: list[StaticRow]
    exposure_by_cutoff: dict[date, list[ExposureRow]]
    production_cutoff: date


async def load_raw(conn: AsyncConnection, cutoffs: Sequence[date], *, today: date) -> RawData:
    """Everything a training run reads, once: incidents placed against the scorable areas, static rows,
    and one exposure snapshot per backtest cutoff plus the newest production snapshot."""
    areas = await read_areas(conn)
    lookup = AreaLookup(areas)
    placements = [place(r, lookup) for r in await read_incidents(conn)]
    exposure: dict[date, list[ExposureRow]] = {}
    for cutoff in cutoffs:
        as_of = await pick_snapshot(conn, data_cutoff=cutoff, production=False, today=today)
        exposure[cutoff] = await read_exposure(conn, as_of=as_of, data_cutoff=cutoff)
    prod = await pick_snapshot(conn, data_cutoff=today, production=True, today=today)
    exposure[prod] = await read_exposure(conn, as_of=prod, data_cutoff=prod)
    return RawData(placements, areas, await read_area_paths(conn), await read_static(conn), exposure, prod)


def reweight(raw: RawData, mult_by_state: Mapping[str, float]) -> RawData:
    """Each placement's weight times its state's multiplier (the stability bootstrap). Every state that
    holds a placement must be named: a forgotten state would silently keep weight 1."""
    missing = sorted({p.state_code for p in raw.placements
                      if p.state_code is not None and p.state_code not in mult_by_state})
    if missing:
        raise ValueError(f"mult_by_state lacks states {missing}")
    out = [p if p.state_code is None else dataclasses.replace(p, weight=p.weight * float(mult_by_state[p.state_code]))
           for p in raw.placements]
    return dataclasses.replace(raw, placements=out)


def _exposure(raw: RawData, cutoff: date) -> list[ExposureRow]:
    rows = raw.exposure_by_cutoff.get(cutoff)
    if rows is None:
        raise StaleExposure(f"no exposure snapshot with data_cutoff {cutoff}; run "
                            f"`exposure build --cutoff {cutoff}` (Task 21)")
    return rows


def _cells(raw: RawData, cutoff: date, *, mp_ticks: bool = True) -> CellData:
    rows = _exposure(raw, cutoff)
    if not mp_ticks:
        # The removal drill: ticks read as never scraped, not as zero traffic.
        rows = [dataclasses.replace(r, ticks_value=None, ticks_per_route=None, tick_coverage=None) for r in rows]
    return assemble_cells(rows, raw.static, raw.areas, raw.paths)


def fit_full(raw: RawData, params: ModelParams, *, cutoff: date | None) -> tuple[Fitted, CellData, Counts]:
    """Every cell trains, on [TRAIN_START, cutoff); cutoff None is the production snapshot."""
    end = raw.production_cutoff if cutoff is None else cutoff
    cells = _cells(raw, end)
    counts = count_incidents(raw.placements, cells, start=TRAIN_START, end=end)
    return fit_model(cells, counts, params), cells, counts


@dataclass(frozen=True)
class FoldData:
    fold: Fold
    cells: CellData
    train: Counts
    test: Counts
    train_mask: BoolArr
    test_mask: BoolArr
    scale_tau: float


def _years_before(d: date, years: int) -> date:
    return date(d.year - years, d.month, 28 if (d.month, d.day) == (2, 29) else d.day)


def prepare(raw: RawData, fold: Fold, *, exclude_flags: frozenset[str] = frozenset(),
            mp_ticks: bool = True) -> FoldData:
    cells = _cells(raw, fold.exposure_cutoff, mp_ticks=mp_ticks)
    # The month profile spreads month-unknown incidents; it comes from the training window only, so
    # the test window never shapes its own spread.
    profile = seasonal_profile(raw.placements, start=fold.train_start, end=fold.train_end,
                               exclude_flags=exclude_flags)

    def counts(start: date, end: date) -> Counts:
        return count_incidents(raw.placements, cells, start=start, end=end, exclude_flags=exclude_flags,
                               profile=profile)

    train = counts(fold.train_start, fold.train_end)
    if fold.kind == "spatial":
        train_mask = fold_train_mask(cells, fold)
        test_mask = ~train_mask
        # Same window: held-out cells use their own mu0 from a GLM fit without them.
        return FoldData(fold, cells, train, train, train_mask, test_mask, 1.0)
    test = counts(fold.test_start, fold.test_end)
    total = float(train.y.sum())
    if total <= 0:
        raise ValueError(f"fold {fold.name} has no training incidents; tau has no level")
    recent = counts(max(fold.train_start, _years_before(fold.train_end, LEVEL_YEARS)), fold.train_end)
    # Reporting volume drifts: the last training years' national rate over the window mean rescales
    # the test expectation without reading the test window.
    level = (float(recent.y.sum()) / recent.years) / (total / train.years)
    everything = np.ones(cells.n, dtype=np.bool_)
    return FoldData(fold, cells, train, test, everything, everything.copy(), test.years / train.years * level)


@dataclass(frozen=True)
class FoldResult:
    """Test cells of one fold. percentile is the shown within-type-group percentile (NaN when
    insufficient), which the G7 band tables need."""

    fold: Fold
    shape: F64
    rate: F64
    scale: F64
    obs: F64
    mu_point: F64
    conf: NDArray[np.object_]
    insufficient: BoolArr
    zero_history: BoolArr
    group: NDArray[np.int64]
    routes: F64
    evidence_dist: F64
    geo_evidence: BoolArr
    percentile: F64


def _stage1_key(fold: Fold, params: ModelParams) -> str:
    return f"{fold.name}|{params.lambdas!r}|{params.tick_policy!r}"


def run_fold(fd: FoldData, params: ModelParams, thresholds: Thresholds, *,
             stage1_cache: dict[str, Fitted] | None = None) -> FoldResult:
    """stage1_cache reuses a fold's GLM across stage-2 sweeps; fit_model refuses a cached fit whose
    stage-1 inputs differ, so one cache must not span FoldData built with different flags or data."""
    key = _stage1_key(fd.fold, params)
    cached = None if stage1_cache is None else stage1_cache.get(key)
    fit = fit_model(fd.cells, fd.train, params, train_mask=fd.train_mask, reuse_stage1=cached)
    if stage1_cache is not None and cached is None:
        stage1_cache[key] = fit
    cells = fd.cells
    y_own = fit.y_train
    mu0, cp, dist, geo = fit.predict(cells, y_own)
    s = summarize(cells, fit.design_for(cells), fit.glm.coef, cp, thresholds=thresholds, evidence_dist=dist,
                  years=fit.years, geo_evidence=geo)
    area_train = np.bincount(cells.area_idx, weights=y_own, minlength=cells.area_id.size)
    zero_history = (y_own == 0) & (area_train[cells.area_idx] == 0)
    t = np.flatnonzero(fd.test_mask)
    scale = np.asarray(mu0[t] * fd.scale_tau, dtype=np.float64)
    shape, rate = cp.post.shape[t], cp.post.rate[t]
    return FoldResult(
        fold=fd.fold, shape=shape, rate=rate, scale=scale, obs=np.asarray(fd.test.y[t], dtype=np.float64),
        mu_point=np.asarray(shape / rate * scale, dtype=np.float64), conf=s.conf_level[t], insufficient=s.insufficient[t],
        zero_history=zero_history[t], group=cells.g[t].astype(np.int64), routes=cells.n_routes[t],
        evidence_dist=dist[t], geo_evidence=geo[t], percentile=s.percentile[t],
    )


def _num(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


def _oe(obs: float, exp: float) -> list[float] | None:
    return None if not exp > 0 else [float(v) for v in oe_ci(obs, exp)]


def _calibration_dict(pred: F64, obs: F64) -> dict[str, Any]:
    c = calibration(pred, obs)
    return {"scheme": c.scheme, "bins": [[_num(e), _num(o), _num(r)] for e, o, r in c.bins],
            "n_pass": c.n_pass, "passed": c.passed}


@dataclass(frozen=True)
class _Pooled:
    shape: F64
    rate: F64
    scale: F64
    obs: F64
    mu: F64
    conf: NDArray[np.object_]
    insufficient: BoolArr
    zero_history: BoolArr
    group: NDArray[np.int64]
    routes: F64
    percentile: F64
    kind: NDArray[np.str_]

    def take(self, sel: BoolArr) -> _Pooled:
        return _Pooled(**{f.name: getattr(self, f.name)[sel] for f in dataclasses.fields(self)})


def _pool(results: Sequence[FoldResult]) -> _Pooled:
    def cat(name: str) -> Any:
        return np.concatenate([getattr(r, name) for r in results]) if results else np.zeros(0)

    return _Pooled(cat("shape"), cat("rate"), cat("scale"), cat("obs"), cat("mu_point"), cat("conf"),
                   cat("insufficient").astype(np.bool_), cat("zero_history").astype(np.bool_),
                   cat("group").astype(np.int64), cat("routes"), cat("percentile"),
                   np.concatenate([np.full(r.obs.size, r.fold.kind) for r in results]) if results
                   else np.zeros(0, dtype=str))


def _log_score(p: _Pooled, phi: float) -> float:
    """The phi-widened Gamma, as the metrics module asks for a predictive score."""
    return mean_log_score(p.obs, np.asarray(p.shape / phi, dtype=np.float64), np.asarray(p.rate / phi, dtype=np.float64),
                          p.scale)


def _block(p: _Pooled, phi: float) -> MetricsReport:
    n = int(p.obs.size)
    obs, exp = float(p.obs.sum()), float(p.mu.sum())
    out: MetricsReport = {"n_cells": n, "observed": obs, "expected": exp}
    if n == 0:
        return {**out, "log_score": None, "deviance": None, "top10": None, "calibration": None,
                "coverage": None, "oe": None}
    return {
        **out,
        "log_score": _log_score(p, phi),
        "deviance": poisson_deviance(p.obs, p.mu),
        "top10": _num(top_capture(p.mu, p.obs)),
        "calibration": _calibration_dict(p.mu, p.obs),
        "coverage": interval_coverage(p.obs, p.shape, p.rate, p.scale, phi),
        "oe": _oe(obs, exp),
    }


def _poisson_logpmf(k: float, mu: float) -> float:
    return float(k * math.log(mu) - mu - gammaln(k + 1.0)) if mu > 0 else (0.0 if k == 0 else -math.inf)


def _bands(p: _Pooled, phi: float, edges: tuple[float, ...]) -> dict[str, Any]:
    """G7 (A:169-173), reported only (D3). Bands are lower-inclusive like risk_bands; insufficient cells
    have no percentile and no band. band_log_score is the mean Poisson log score of each band's total
    observed count under its total expected count, so schemes with the same band count compare."""
    ok = np.isfinite(p.percentile)
    lows = (0.0, *edges)
    highs = (*edges, math.inf)
    rows: list[dict[str, Any]] = []
    for lo, hi in zip(lows, highs):
        b = p.take(ok & (p.percentile >= lo) & (p.percentile < hi))
        o, e, r = float(b.obs.sum()), float(b.mu.sum()), float(b.routes.sum())
        rows.append({
            "lo": lo, "hi": None if math.isinf(hi) else hi, "n_cells": int(b.obs.size), "observed": o,
            "expected": e, "rate_per_route": o / r if r > 0 else None, "oe": _oe(o, e),
            "log_score": (_log_score(b, phi) if b.obs.size else None),
            "band_logpmf": _poisson_logpmf(o, e) if e > 0 else None,
        })
    filled = [b for b in rows if b["n_cells"] > 0]
    rates = [b["rate_per_route"] for b in filled]
    monotone = (len(filled) == len(rows) and None not in rates
                and all(a <= c for a, c in zip(rates, rates[1:])))
    in_range = all(b["oe"] is not None and BAND_OE[0] <= b["oe"][1] <= BAND_OE[1] for b in rows)
    lps = [b["band_logpmf"] for b in rows]
    return {"edges": list(edges), "bands": rows, "monotone_rise": bool(monotone), "oe_in_range": bool(in_range),
            "passed": bool(monotone and in_range),
            "band_log_score": None if None in lps else _num(float(np.mean(lps)))}


def report(results: Sequence[FoldResult], *, phi: float) -> MetricsReport:
    """Pooled metrics plus by_kind, by_group, by_conf, zero_history and bands. phi widens the Gamma for
    the log score and sets the shown interval for coverage (metrics module docstring). Insufficient
    cells are scored everywhere except by_conf, where they are only counted (insufficient_share)."""
    p = _pool(results)
    out = _block(p, phi)
    out["by_kind"] = {k: _block(p.take(p.kind == k), phi) for k in sorted({str(x) for x in p.kind})}
    out["by_group"] = {g: _block(p.take(p.group == gi), phi) for gi, g in enumerate(TYPE_GROUPS)
                       if (p.group == gi).any()}
    by_conf: dict[str, Any] = {lvl: _block(p.take(p.conf == lvl), phi) for lvl in ("high", "medium", "low")}
    by_conf["insufficient_share"] = float(p.insufficient.mean()) if p.obs.size else None
    out["by_conf"] = by_conf
    out["zero_history"] = _block(p.take(p.zero_history), phi)
    out["bands"] = {"quartile_25_50_75": _bands(p, phi, QUARTILE_EDGES), "p3_percentile": _bands(p, phi, P3_EDGES)}
    return out


def permute_static(raw: RawData, seed: int = PERMUTE_SEED) -> RawData:
    """G3 negative control: every area takes a randomly drawn area's static rows (fixed seed), mostly
    from another state group, which breaks any feature-to-risk link the pooling could have learned."""
    ids = sorted({s.area_id for s in raw.static})
    source = dict(zip(ids, (ids[i] for i in np.random.default_rng(seed).permutation(len(ids)))))
    by_area: dict[int, list[StaticRow]] = {}
    for s in raw.static:
        by_area.setdefault(s.area_id, []).append(s)
    static = [dataclasses.replace(s, area_id=a) for a in ids for s in by_area[source[a]]]
    return dataclasses.replace(raw, static=static)


@dataclass(frozen=True)
class Suite:
    main: MetricsReport
    geo_only: MetricsReport
    permuted: MetricsReport
    sensitivity: dict[str, MetricsReport]
    prospective: MetricsReport | None
    prospective_reason: str | None
    recent: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"main": self.main, "geo_only": self.geo_only, "permuted": self.permuted,
                "sensitivity": self.sensitivity, "prospective": self.prospective,
                "prospective_reason": self.prospective_reason, "recent": self.recent}


def _run(fds: Sequence[FoldData], params: ModelParams, thresholds: Thresholds,
         cache: dict[str, Fitted] | None = None) -> MetricsReport:
    return report([run_fold(fd, params, thresholds, stage1_cache=cache) for fd in fds], phi=thresholds.phi)


def prospective_report(raw: RawData, params: ModelParams, thresholds: Thresholds) -> tuple[MetricsReport | None, str | None]:
    """Part 4 (DP14): None with a reason until the window holds test incidents (R11 refresh)."""
    fd = prepare(raw, prospective_fold())
    if not float(fd.test.y.sum()) > 0:
        return None, "no_test_incidents"
    return _run([fd], params, thresholds), None


def recent_report(raw: RawData, params: ModelParams, thresholds: Thresholds) -> dict[str, Any]:
    """Part 3: 2019-2024 on the 2016 snapshot (no 2019 snapshot exists by design), O/E with its 90% CI
    only, never gated; open_issue when the CI lies wholly outside [0.5, 2] (P3:377)."""
    r = run_fold(prepare(raw, RECENT_FOLD), params, thresholds)
    obs, exp = float(r.obs.sum()), float(r.mu_point.sum())
    ci = _oe(obs, exp)
    lo, hi = RECENT_OE_RANGE
    return {"n_cells": int(r.obs.size), "observed": obs, "expected": exp, "oe": ci,
            "open_issue": ci is not None and (ci[2] < lo or ci[0] > hi)}


def run_suite(raw: RawData, params: ModelParams, thresholds: Thresholds, *, cutoff: date | None) -> Suite:
    end = raw.production_cutoff if cutoff is None else cutoff
    folds = spatial_folds(end) + rolling_folds()
    fds = [prepare(raw, f) for f in folds]
    # geo_only differs from main only in blend (stage 2), so it reuses main's GLMs.
    cache: dict[str, Fitted] = {}
    main = _run(fds, params, thresholds, cache)
    geo_only = _run(fds, dataclasses.replace(params, blend=False), thresholds, cache)
    permuted_raw = permute_static(raw)
    permuted = _run([prepare(permuted_raw, f) for f in folds], params, thresholds)
    sensitivity = {name: _run([prepare(raw, f, **kw) for f in folds], params, thresholds)
                   for name, kw in SENSITIVITIES.items()}
    prospective, reason = prospective_report(raw, params, thresholds)
    return Suite(main, geo_only, permuted, sensitivity, prospective, reason, recent_report(raw, params, thresholds))


FIXTURE_TH = Thresholds(d_star=2.0, conf_high=3.0, conf_medium=8.0, phi=1.5)
COVERAGE_FLOOR = (0.80, 0.97)


def fixture_floors(suite: Suite) -> dict[str, bool]:
    m, g, p = suite.main, suite.geo_only, suite.permuted
    cov = m["coverage"]
    cal = m["calibration"]
    return {
        "main_beats_geo_only": bool(m["log_score"] > g["log_score"]),  # type: ignore[operator]
        "calibration_passed": bool(isinstance(cal, dict) and cal.get("passed") is True),
        "coverage_in_range": bool(cov is not None and COVERAGE_FLOOR[0] <= cov <= COVERAGE_FLOOR[1]),  # type: ignore[operator]
        "permuted_below_main": bool(p["log_score"] < m["log_score"]),  # type: ignore[operator]
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.ml.backtest")
    ap.add_argument("--fixture", choices=("small",), required=True,
                    help="run the suite on fixtures.synthetic_raw(seed=0) and check the CI floors (F42)")
    ap.parse_args(argv)
    from app.ml.fixtures import synthetic_raw

    t0 = time.monotonic()
    suite = run_suite(synthetic_raw(seed=0), ModelParams(), FIXTURE_TH, cutoff=None)
    floors = fixture_floors(suite)
    passed = all(floors.values())
    print(json.dumps({"passed": passed, "floors": floors, "wall_seconds": round(time.monotonic() - t0, 2),
                      "main_log_score": suite.main["log_score"], "geo_only_log_score": suite.geo_only["log_score"],
                      "permuted_log_score": suite.permuted["log_score"], "coverage": suite.main["coverage"]},
                     allow_nan=False))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
