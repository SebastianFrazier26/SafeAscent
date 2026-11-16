import dataclasses
from datetime import date

import numpy as np
import pytest

import app.ml.kernel_ref.safety_algorithm_vectorized as kernel_mod
from app.ml import kernel_adapter as ka
from app.ml.backtest import prepare
from app.ml.fixtures import synthetic_raw
from app.ml.folds import prospective_fold, rolling_folds, spatial_folds
from app.ml.kernel_ref.safety_algorithm import SafetyPrediction

CUTOFF = date(2026, 10, 1)


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return SafetyPrediction(risk_score=float(len(kw["accidents"])), num_contributing_accidents=0,
                                top_contributing_accidents=[], metadata={})


def _small(raw, fold, n_areas=6):
    fd = prepare(raw, fold)
    keep = np.isin(fd.cells.area_idx, np.arange(n_areas))
    return fd.cells.subset(keep)


def test_kernel_sees_only_training_accidents_without_weather(monkeypatch):
    raw = synthetic_raw(seed=41)
    rec = Recorder()
    monkeypatch.setattr(kernel_mod, "calculate_safety_score_vectorized", rec)
    fold = rolling_folds()[0]
    cells = _small(raw, fold)
    scores = ka.kernel_cell_scores(cells, raw.placements, raw.areas, train_end=fold.train_end,
                                   reference_year=fold.test_start.year)
    assert scores.shape == (cells.n,) and len(rec.calls) == cells.n
    seen = [a for c in rec.calls for a in c["accidents"]]
    assert seen, "the fixture should put training accidents near these areas"
    assert all(a.weather_pattern is None for a in seen)
    assert all(a.accident_date < fold.train_end for a in seen)
    assert all(c["route_type"] != "rock" for c in rec.calls)
    assert {c["current_date"] for c in rec.calls} <= {date(fold.test_start.year, m, 15) for m in range(1, 13)}
    assert all(c["today"] == fold.train_end for c in rec.calls)


def test_real_kernel_runs_and_scores_more_near_more_accidents():
    raw = synthetic_raw(seed=42)
    fold = rolling_folds()[3]
    cells = _small(raw, fold, n_areas=3)
    s = ka.kernel_cell_scores(cells, raw.placements, raw.areas, train_end=fold.train_end,
                              reference_year=fold.test_start.year)
    assert np.all(np.isfinite(s)) and np.all(s >= 0) and s.max() > 0
    none = ka.kernel_cell_scores(cells, [], raw.areas, train_end=fold.train_end, reference_year=2016)
    assert np.all(none == 0)


def test_calibration_map_is_fit_on_training_cells_only(monkeypatch):
    raw = synthetic_raw(seed=43)
    fd = prepare(raw, spatial_folds(CUTOFF)[4])
    scores = np.random.default_rng(0).gamma(2.0, 5.0, size=fd.cells.n)
    base = ka.fold_predictions(fd, scores)
    y = fd.train.y.copy()
    y[fd.test_mask] += 100.0
    moved = ka.fold_predictions(dataclasses.replace(fd, train=dataclasses.replace(fd.train, y=y)), scores)
    np.testing.assert_array_equal(base.mu, moved.mu)
    scores2 = scores.copy()
    scores2[fd.test_mask] *= 3.0
    a, b = ka.calibration_map(scores[fd.train_mask], fd.train.y[fd.train_mask])
    a2, b2 = ka.calibration_map(scores2[fd.train_mask], fd.train.y[fd.train_mask])
    assert (a, b) == (a2, b2)


def test_kernel_report_shape(monkeypatch):
    raw = synthetic_raw(seed=44)
    monkeypatch.setattr(kernel_mod, "calculate_safety_score_vectorized", Recorder())
    folds = [spatial_folds(CUTOFF)[4], rolling_folds()[3]]
    r = ka.kernel_report(raw, folds)
    assert set(r["by_kind"]) == {"spatial", "rolling"}
    assert r["coverage"] is None and np.isfinite(r["log_score"])
    assert r["n_cells"] == sum(int(prepare(raw, f).test_mask.sum()) for f in folds)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeConn:
    def __init__(self, routes, preds):
        self.routes, self.preds, self.seen = routes, preds, []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.seen.append((sql, params))
        return FakeResult(self.preds if "historical_predictions" in sql else self.routes)


async def test_prospective_kernel_averages_scores_and_keeps_null_cells_missing():
    raw = synthetic_raw(seed=45)
    fold = prospective_fold()
    cells = prepare(raw, fold).cells
    a0 = int(cells.area_id[0])
    g = int(cells.g[cells.area_idx == 0][0])
    from app.ml.cells import TYPE_GROUPS

    gname = TYPE_GROUPS[g]
    routes = [(900_500_001, a0, gname), (900_500_002, a0, gname), (900_500_003, a0, gname)]
    preds = [(900_500_001, date(2026, 3, 1), 10.0), (900_500_002, date(2026, 3, 9), 30.0),
             (900_500_003, date(2026, 3, 2), None), (900_500_003, date(2026, 4, 2), None),
             (900_500_009, date(2026, 3, 2), 50.0)]
    conn = FakeConn(routes, preds)
    out = await ka.prospective_kernel(conn, fold, cells)
    assert out[cells.index[(a0, g, 3)]] == pytest.approx(20.0)
    assert np.isnan(out[cells.index[(a0, g, 4)]])
    assert np.isnan(out[cells.index[(a0, g, 5)]])
    sql, params = next(q for q in conn.seen if "historical_predictions" in q[0])
    assert ":a" in sql and ":b" in sql and params == {"a": fold.test_start, "b": fold.test_end}


def test_prospective_kernel_report_ranks_only_scored_cells():
    raw = synthetic_raw(seed=46)
    fd = prepare(raw, prospective_fold())
    scores = np.full(fd.cells.n, np.nan)
    scores[: fd.cells.n // 2] = np.linspace(1.0, 50.0, fd.cells.n // 2)
    r, reason = ka.prospective_kernel_report(fd, scores)
    assert reason is None and r is not None
    assert r["kernel_coverage"] == pytest.approx((fd.cells.n // 2) / fd.cells.n)
    assert r["n_cells"] == fd.cells.n // 2


def test_prospective_kernel_report_without_test_incidents_returns_a_reason():
    raw = synthetic_raw(seed=46)
    fd = prepare(raw, prospective_fold())
    scores = np.linspace(1.0, 50.0, fd.cells.n)
    empty = dataclasses.replace(fd, test=dataclasses.replace(fd.test, y=np.zeros_like(fd.test.y)))
    assert ka.prospective_kernel_report(empty, scores) == (None, "no_test_incidents")
    # Incidents only in cells the kernel never scored: still nothing to calibrate against.
    y = np.zeros_like(fd.test.y)
    y[fd.test_mask.nonzero()[0][0]] = 1.0
    unscored = scores.copy()
    unscored[fd.test_mask.nonzero()[0][0]] = np.nan
    only_unscored = dataclasses.replace(fd, test=dataclasses.replace(fd.test, y=y))
    assert ka.prospective_kernel_report(only_unscored, unscored) == (None, "no_scored_test_incidents")
