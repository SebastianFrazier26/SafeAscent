"""The old kernel as a backtest reference (P3:392, DP14). Folds call the kernel's own math per
(area, type group, month) cell with training-fold accidents only; the prospective part reads the
kernel's STORED historical_predictions and never recomputes them.

The kernel is reached through the module attribute so tests can monkeypatch it (pre-flight F41);
Task 23's `git mv` to app.ml.kernel_ref updates this one import."""

from __future__ import annotations

import math
from calendar import monthrange
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from numpy.typing import NDArray
from scipy.special import gammaln
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

import app.services.safety_algorithm_vectorized as kernel_mod
from app.ml.backtest import FoldData, RawData, prepare
from app.ml.cells import G_INDEX, TYPE_GROUPS
from app.ml.dataset import STATIC_FEATURES, CellData
from app.ml.folds import Fold
from app.ml.glm import fit_poisson
from app.ml.incidents import AreaInfo, Placement
from app.ml.metrics import MetricsReport, calibration, oe_ci, poisson_deviance, top_capture
from app.services.safety_algorithm import AccidentData
from app.services.weather_similarity import WeatherPattern

F64 = NDArray[np.float64]
PREDICTIONS_SQL = text("SELECT route_id, prediction_date, risk_score FROM historical_predictions "
                       "WHERE prediction_date >= :a AND prediction_date < :b")
ROUTES_SQL = text("SELECT route_id, area_id, type_group FROM scorable_routes")
# The kernel's spatial weight is exp(-d^2 / 2h^2); past 6 bandwidths it is below 1.6e-8 per accident,
# so accidents that far away are not passed (a cell call over every US accident is hours of Python).
SPATIAL_CUTOFF_BANDWIDTHS = 6.0
NEUTRAL_TEMP_C = 10.0
# Weather never moves the score here (every accident pattern is None, weight 0.5), but the kernel
# still wants a current week; these fill the fields normals do not carry.
NEUTRAL_WIND, NEUTRAL_VISIBILITY, NEUTRAL_CLOUD = 3.0, 10_000.0, 50.0
SLOPE_RIDGE = 1e-6
_ELEV, _TMAX, _TMIN, _PRECIP = (STATIC_FEATURES.index(f) for f in ("elevation_m", "tmax_mean", "tmin_mean",
                                                                     "precip_mm"))


def _kernel_type(group: str) -> str:
    # The kernel scored unknown-discipline routes as trad (A:32).
    return "trad" if group == "rock" else group


def _flat_week(static_row: F64, year: int, month: int) -> WeatherPattern:
    tmax, tmin, precip = static_row[_TMAX], static_row[_TMIN], static_row[_PRECIP]
    if np.isnan(tmax) or np.isnan(tmin) or np.isnan(precip):
        lo = avg = hi = NEUTRAL_TEMP_C
        daily = 0.0
    else:
        lo, hi = float(tmin), float(tmax)
        avg = (lo + hi) / 2.0
        daily = float(precip) / monthrange(year, month)[1]
    return WeatherPattern(temperature=[avg] * 7, precipitation=[daily] * 7, wind_speed=[NEUTRAL_WIND] * 7,
                          visibility=[NEUTRAL_VISIBILITY] * 7, cloud_cover=[NEUTRAL_CLOUD] * 7,
                          daily_temps=[(lo, avg, hi)] * 7)


def _haversine_km(lat: float, lon: float, lats: F64, lons: F64) -> F64:
    p1, p2 = math.radians(lat), np.radians(lats)
    a = (np.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * np.cos(p2) * np.sin(np.radians(lons - lon) / 2) ** 2)
    return np.asarray(2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0))), dtype=np.float64)


def training_accidents(placements: Sequence[Placement], areas: Sequence[AreaInfo], cells: CellData, *,
                       train_end: date) -> list[AccidentData]:
    """Cell-placed incidents whose whole interval ends by train_end, at their area's point and dated at
    the interval midpoint. A multi-year interval reaching past train_end is test-window mass and is left
    out rather than redated."""
    where = {a.area_id: a for a in areas}
    elev = {int(cells.area_id[i]): float(np.nanmin(cells.area_static[i, :, _ELEV]))
            for i in range(cells.area_id.size) if np.isfinite(cells.area_static[i, :, _ELEV]).any()}
    out = []
    for p in placements:
        if p.level != "cell" or p.area_id not in where or p.lo is None or p.hi is None or p.hi > train_end:
            continue
        a = where[p.area_id]
        group = max(p.group_weights, key=lambda gw: gw[1])[0]
        out.append(AccidentData(
            accident_id=p.accident_id, latitude=a.lat, longitude=a.lon, elevation_meters=elev.get(p.area_id),
            accident_date=p.lo + timedelta(days=(p.hi - p.lo).days // 2), route_type=_kernel_type(group),
            severity="unknown", weather_pattern=None, grade=None))
    return out


def kernel_cell_scores(cells: CellData, placements: Sequence[Placement], areas: Sequence[AreaInfo], *,
                       train_end: date, reference_year: int) -> F64:
    """The kernel's risk_score per cell at date(reference_year, month, 15), as of train_end."""
    accidents = training_accidents(placements, areas, cells, train_end=train_end)
    lats = np.array([a.latitude for a in accidents], dtype=np.float64)
    lons = np.array([a.longitude for a in accidents], dtype=np.float64)
    radius = SPATIAL_CUTOFF_BANDWIDTHS * max(kernel_mod.SPATIAL_BANDWIDTH.values())
    where = {a.area_id: a for a in areas}
    out = np.zeros(cells.n)
    near_by_area: dict[int, list[AccidentData]] = {}
    for c in range(cells.n):
        ai = int(cells.area_idx[c])
        area = where[int(cells.area_id[ai])]
        if ai not in near_by_area:
            keep = (np.flatnonzero(_haversine_km(area.lat, area.lon, lats, lons) <= radius) if accidents
                    else np.zeros(0, dtype=np.int64))
            near_by_area[ai] = [accidents[int(i)] for i in keep]
        month = int(cells.month[c])
        elevation = cells.static[c, _ELEV]
        pred = kernel_mod.calculate_safety_score_vectorized(
            route_lat=area.lat, route_lon=area.lon,
            route_elevation_m=None if np.isnan(elevation) else float(elevation),
            route_type=_kernel_type(TYPE_GROUPS[int(cells.g[c])]),
            current_date=date(reference_year, month, 15),
            current_weather=_flat_week(cells.static[c], reference_year, month),
            accidents=near_by_area[ai], today=train_end)
        out[c] = float(pred.risk_score)
    return out


def calibration_map(scores: F64, y: F64) -> tuple[float, float]:
    """Poisson count ~ a + b log1p(score). The tiny ridge on b keeps the fit defined when every score
    is 0 (no accident near any training cell)."""
    X = np.column_stack([np.ones(scores.size), np.log1p(scores)])
    fit = fit_poisson(X, np.asarray(y, dtype=np.float64), np.array([0.0, SLOPE_RIDGE]))
    if not fit.converged:
        raise RuntimeError("kernel calibration map did not converge")
    return float(fit.coef[0]), float(fit.coef[1])


@dataclass(frozen=True)
class KernelFold:
    kind: str
    score: F64
    mu: F64
    obs: F64


def fold_predictions(fd: FoldData, scores: F64) -> KernelFold:
    """Test-cell expected counts from a map fit on the fold's training cells only, scaled by tau."""
    tr, te = fd.train_mask, fd.test_mask
    a, b = calibration_map(scores[tr], fd.train.y[tr])
    mu = np.exp(a + b * np.log1p(scores[te])) * fd.scale_tau
    return KernelFold(fd.fold.kind, scores[te], mu, np.asarray(fd.test.y[te], dtype=np.float64))


def _reference_year(fold: Fold) -> int:
    # Spatial folds test the whole history; the last full training year stands in for "now".
    return fold.train_end.year - 1 if fold.kind == "spatial" else fold.test_start.year


def _poisson_log_score(k: F64, mu: F64) -> float:
    return float(np.mean(k * np.log(mu) - mu - gammaln(k + 1.0)))


def _num(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


def _block(kf: Sequence[KernelFold]) -> MetricsReport:
    obs = np.concatenate([k.obs for k in kf]) if kf else np.zeros(0)
    mu = np.concatenate([k.mu for k in kf]) if kf else np.zeros(0)
    score = np.concatenate([k.score for k in kf]) if kf else np.zeros(0)
    o, e = float(obs.sum()), float(mu.sum())
    out: MetricsReport = {"n_cells": int(obs.size), "observed": o, "expected": e, "coverage": None}
    if obs.size == 0:
        return {**out, "log_score": None, "deviance": None, "top10": None, "calibration": None, "oe": None}
    c = calibration(mu, obs)
    return {**out, "log_score": _poisson_log_score(obs, mu), "deviance": poisson_deviance(obs, mu),
            "top10": _num(top_capture(score, obs)),
            "calibration": {"scheme": c.scheme, "bins": [[_num(x) for x in b] for b in c.bins],
                            "n_pass": c.n_pass, "passed": c.passed},
            "oe": [float(v) for v in oe_ci(o, e)] if e > 0 else None}


def kernel_folds(raw: RawData, folds: Sequence[Fold]) -> list[KernelFold]:
    """One KernelFold per fold, in order. A spatial fold's held-out states lend the kernel no accidents."""
    results = []
    for fold in folds:
        fd = prepare(raw, fold)
        train_pl = [p for p in raw.placements if p.state_code not in fold.held_out_states]
        scores = kernel_cell_scores(fd.cells, train_pl, raw.areas, train_end=fold.train_end,
                                    reference_year=_reference_year(fold))
        results.append(fold_predictions(fd, scores))
    return results


def report_from(results: Sequence[KernelFold]) -> MetricsReport:
    out = _block(results)
    out["by_kind"] = {k: _block([r for r in results if r.kind == k]) for k in sorted({r.kind for r in results})}
    return out


def kernel_report(raw: RawData, folds: Sequence[Fold]) -> MetricsReport:
    """The kernel's metrics on the same folds and test cells as the model (pooled plus by_kind). The
    kernel has no interval, so coverage is None; top10 ranks the raw scores."""
    return report_from(kernel_folds(raw, folds))


async def prospective_kernel(conn: AsyncConnection, fold: Fold, cells: CellData) -> F64:
    """Mean stored risk_score per (area, type group, month) over [test_start, test_end). A cell with
    no stored score, or only NULL (insufficient) ones, is NaN: unscored, never 0."""
    groups = {int(r): (int(a), str(g)) for r, a, g in (await conn.execute(ROUTES_SQL)).all()}
    total: defaultdict[int, float] = defaultdict(float)
    n: defaultdict[int, int] = defaultdict(int)
    rows = (await conn.execute(PREDICTIONS_SQL, {"a": fold.test_start, "b": fold.test_end})).all()
    for route_id, day, score in rows:
        hit = groups.get(int(route_id))
        if score is None or hit is None or hit[1] not in G_INDEX:
            continue
        c = cells.index.get((hit[0], G_INDEX[hit[1]], day.month))
        if c is not None:
            total[c] += float(score)
            n[c] += 1
    out = np.full(cells.n, np.nan)
    for c, k in n.items():
        out[c] = total[c] / k
    return out


def prospective_kernel_report(fd: FoldData, scores: F64) -> MetricsReport:
    """DP14: scored cells only, ranked by raw stored score (the gated top10); the log score uses an
    in-window calibration map, which favours the kernel and is reported, never gated."""
    ok = fd.test_mask & np.isfinite(scores)
    out: MetricsReport
    if not ok.any():
        out = _block([])
    else:
        a, b = calibration_map(scores[ok], fd.test.y[ok])
        mu = np.exp(a + b * np.log1p(scores[ok]))
        out = _block([KernelFold(fd.fold.kind, scores[ok], mu, np.asarray(fd.test.y[ok], dtype=np.float64))])
    out["kernel_coverage"] = float(ok.sum() / fd.test_mask.sum()) if fd.test_mask.any() else None
    return out
