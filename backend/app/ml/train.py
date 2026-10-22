"""Phase 3 training CLI, as `trainer` from the owner's Mac only (pre-flight F48; runbooks: `TRAIN ...`).

    uv run --group train python -m app.ml.train run [--today YYYY-MM-DD] [--benchmark] [--out-dir out]
    uv run --group train python -m app.ml.train kernel-baseline [--today YYYY-MM-DD]
    uv run --group train python -m app.ml.train rollback [--version V]

Run `INGMOD app.pipelines.exposure build` first (pre-flight F21): the production snapshot must be at most
35 days old. Reads happen in one connection, the CPU work with no connection open, and every registry
write in one transaction, so a failed run leaves no candidate behind.

Stdout carries exactly one outcome line. Exceptions are reported by class name only (pre-flight F49): a
SQLAlchemy or driver message can carry the host, SQL text and parameter values."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import logging
import math
import resource
import sys
import time
import tracemalloc
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.config import settings
from app.healthchecks import ping
from app.ml import kernel_adapter as ka
from app.ml import registry
from app.ml.backtest import FoldData, RawData, fit_full, load_raw, prepare, report, run_fold, run_suite
from app.ml.bundle import ModelBundle
from app.ml.cells import TRAIN_START
from app.ml.features import Lambdas, TickPolicy
from app.ml.folds import BACKTEST_CUTOFFS, Fold, prospective_fold, rolling_folds, spatial_folds
from app.ml.materialize import RouteRow, conf_counts, read_routes, write_scores
from app.ml.metrics import MetricsReport
from app.ml.model import FAMILY, MODEL_CARD_NOTES, Fitted, ModelParams, fit_model
from app.ml.posterior import Thresholds
from app.ml.promote import DiffReport, GateResult, diff_report, evaluate_gate, issue_markdown
from app.ml.tuning import stability, tune

logger = logging.getLogger("app.ml.train")
F64 = NDArray[np.float64]
KERNEL = "kernel"
CI_BOOT = 2000
CI_SEED = 0
BENCH_SCALES = (1, 2, 4)
BENCH_AREAS = 96
ACTIVE_ROWS_SQL = text(
    "SELECT s.route_id, s.type_group, a.state_code, s.percentile FROM model.route_scores s "
    "JOIN canonical_areas a ON a.area_id = s.area_id WHERE s.model_version = :v AND s.month = :m "
    "ORDER BY s.route_id")
VERSION_PREFIX = "eb"
VERSIONS_SQL = text("SELECT version FROM model.registry WHERE version LIKE :p")


def _clean(x: Any) -> Any:
    """JSON-ready copy: numpy scalars become Python numbers and a non-finite float becomes None (missing),
    never 0, so every json.dumps here can run with allow_nan=False."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        v = float(x)
        return v if math.isfinite(v) else None
    return x


def _dumps(x: Any, **kw: Any) -> str:
    return json.dumps(_clean(x), allow_nan=False, **kw)


def thresholds_json(th: Thresholds) -> dict[str, Any]:
    d = dataclasses.asdict(th)
    d["d_star"] = None if math.isinf(th.d_star) else th.d_star  # None = never gray by distance
    return d


def thresholds_from_json(d: dict[str, Any]) -> Thresholds:
    return Thresholds(**{**d, "d_star": math.inf if d.get("d_star") is None else float(d["d_star"])})


def params_from_json(d: dict[str, Any]) -> ModelParams:
    return ModelParams(k_arch=int(d["k_arch"]), arch_weights=tuple(float(w) for w in d["arch_weights"]),
                       blend=bool(d["blend"]), lambdas=Lambdas(**d["lambdas"]),
                       tick_policy=TickPolicy(**d["tick_policy"]), seed=int(d["seed"]))


def data_hash(raw: RawData) -> str:
    h = hashlib.sha256()
    for p in sorted(raw.placements, key=lambda p: p.accident_id):
        h.update(repr((p.accident_id, p.level, p.area_id, p.state_code, p.group_weights, p.month, p.lo, p.hi,
                       p.weight, sorted(p.flags))).encode())
    for cutoff in sorted(raw.exposure_by_cutoff):
        h.update(repr((cutoff, sorted(dataclasses.astuple(r) for r in raw.exposure_by_cutoff[cutoff]))).encode())
    h.update(repr(sorted((s.area_id, s.month, s.values) for s in raw.static)).encode())
    return h.hexdigest()


def peak_rss_gb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS and KiB on Linux.
    return float(rss) / (1024**3 if sys.platform == "darwin" else 1024**2)


def fold_ci(diffs: Sequence[float], weights: Sequence[float], *, seed: int = CI_SEED) -> list[float] | None:
    """[q05, point, q95] of the cell-weighted mean per-fold difference, resampling folds. None with
    fewer than two folds: one fold gives no spread, so the gain cannot be called clear."""
    d, w = np.asarray(diffs, dtype=np.float64), np.asarray(weights, dtype=np.float64)
    ok = np.isfinite(d) & (w > 0)
    d, w = d[ok], w[ok]
    if d.size < 2:
        return None
    idx = np.random.default_rng(seed).integers(0, d.size, size=(CI_BOOT, d.size))
    boot = (d[idx] * w[idx]).sum(axis=1) / w[idx].sum(axis=1)
    return [float(np.quantile(boot, 0.05)), float(np.average(d, weights=w)), float(np.quantile(boot, 0.95))]


def benchmark_curve(params: ModelParams, seed: int = 0) -> dict[str, Any]:
    """P3:223: fit time and traced peak memory at 1x/2x/4x synthetic cell counts, and the power law
    seconds = a * cells^b (likewise for memory) fit on log-log."""
    from app.ml.fixtures import synthetic_problem

    n_cells: list[int] = []
    seconds: list[float] = []
    peak_mb: list[float] = []
    for k in BENCH_SCALES:
        cells, counts = synthetic_problem(seed, n_areas=BENCH_AREAS * k)
        tracemalloc.start()
        t0 = time.perf_counter()
        fit_model(cells, counts, params)
        seconds.append(time.perf_counter() - t0)
        peak_mb.append(tracemalloc.get_traced_memory()[1] / 1024**2)
        tracemalloc.stop()
        n_cells.append(cells.n)
    x = np.log(np.asarray(n_cells, dtype=np.float64))

    def law(y: list[float]) -> dict[str, float]:
        b, a = np.polyfit(x, np.log(np.asarray(y, dtype=np.float64)), 1)
        return {"coef": float(math.exp(a)), "exponent": float(b)}

    return {"n_cells": n_cells, "seconds": seconds, "peak_mb": peak_mb, "time": law(seconds), "memory": law(peak_mb)}


@dataclass(frozen=True)
class Loaded:
    raw: RawData
    active: registry.ActiveVersion | None
    active_bundle: ModelBundle | None
    prospective: FoldData
    prospective_scores: F64
    routes: list[RouteRow]
    taken_versions: frozenset[str]


async def load(conn: AsyncConnection, *, today: date) -> Loaded:
    raw = await load_raw(conn, BACKTEST_CUTOFFS, today=today)
    act = await registry.active(conn)
    bundle = None if act is None or act.family == KERNEL else await registry.load_bundle(conn, act.version)
    pfd = prepare(raw, prospective_fold())
    scores = await ka.prospective_kernel(conn, pfd.fold, pfd.cells)
    routes = await read_routes(conn)
    taken = {str(v) for (v,) in (await conn.execute(VERSIONS_SQL, {"p": f"{VERSION_PREFIX}-{today.isoformat()}%"})).all()}
    return Loaded(raw, act, bundle, pfd, scores, routes, frozenset(taken))


def new_version(today: date, taken: frozenset[str]) -> str:
    base = f"{VERSION_PREFIX}-{today.isoformat()}"
    if base not in taken:
        return base
    return next(f"{base}.{i}" for i in range(2, 10_000) if f"{base}.{i}" not in taken)


def _folds(raw: RawData) -> list[Fold]:
    return spatial_folds(raw.production_cutoff) + rolling_folds()


def _per_fold_log_scores(fds: Sequence[FoldData], params: ModelParams,
                         th: Thresholds) -> tuple[MetricsReport, list[float | None]]:
    results = [run_fold(fd, params, th) for fd in fds]
    return report(results, phi=th.phi), [_get(report([r], phi=th.phi), ("log_score",)) for r in results]


def active_comparison(loaded: Loaded, fds: Sequence[FoldData], params: ModelParams, th: Thresholds
                      ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Pre-flight F44: the active version scored on the current folds, never its stored metrics. The
    kernel row is re-run through kernel_report; an eb version is refit with its frozen params and
    thresholds from its bundle. Returns (active metrics in the challenger's shape, vs_active)."""
    if loaded.active is None:
        return None, None
    folds = [fd.fold for fd in fds]
    if loaded.active.family == KERNEL:
        kfolds = ka.kernel_folds(loaded.raw, folds)
        act_main = ka.report_from(kfolds)
        theirs = [_get(ka.report_from([k]), ("log_score",)) for k in kfolds]
    else:
        assert loaded.active_bundle is not None
        man = loaded.active_bundle.manifest
        act_main, theirs = _per_fold_log_scores(fds, params_from_json(man["params"]),
                                                thresholds_from_json(man["thresholds"]))
    _, mine = _per_fold_log_scores(fds, params, th)
    vs: dict[str, Any] = {}
    for kind in ("spatial", "rolling"):
        sel = [i for i, f in enumerate(folds) if f.kind == kind]
        diffs = [math.nan if a is None or b is None else a - b for a, b in ((mine[i], theirs[i]) for i in sel)]
        vs[kind] = {"log_score_ci": fold_ci(diffs, [float(fds[i].test_mask.sum()) for i in sel])}
    return {"main": act_main}, vs


def metric_deltas(ch: dict[str, Any], act: dict[str, Any] | None) -> dict[str, float | None]:
    """challenger - active on the gated held-out metrics; None (missing) when either side lacks one."""
    if act is None:
        return {}
    out: dict[str, float | None] = {}
    for path in (("log_score",), ("top10",), *((("by_kind", k, m) for k in ("spatial", "rolling")
                                                for m in ("log_score", "top10")))):
        x, y = _get(ch.get("main"), path), _get(act.get("main"), path)
        out[".".join(path)] = x - y if x is not None and y is not None else None
    return out


def _get(d: Any, path: Sequence[str]) -> float | None:
    for key in path:
        d = d.get(key) if isinstance(d, dict) else None
    ok = isinstance(d, (int, float)) and not isinstance(d, bool) and math.isfinite(d)
    return float(d) if ok else None


@dataclass(frozen=True)
class Candidate:
    version: str
    fit: Fitted
    cells: Any
    thresholds: Thresholds
    metrics: dict[str, Any]
    active_metrics: dict[str, Any] | None
    model_card: dict[str, Any]
    bundle: bytes


def build_candidate(loaded: Loaded, *, today: date, t0: float, benchmark: bool) -> Candidate:
    raw = loaded.raw
    version = new_version(today, loaded.taken_versions)
    fds = [prepare(raw, f) for f in _folds(raw)]
    logger.info("tuning on %d folds", len(fds))
    tuned = tune(fds)
    params, th = tuned.params, tuned.thresholds
    suite = run_suite(raw, params, th, cutoff=None)
    active_metrics, vs_active = active_comparison(loaded, fds, params, th)
    fit, cells, counts = fit_full(raw, params, cutoff=None)
    bundle = fit.to_bundle(version, th, cells)
    bundle_bytes = bundle.to_bytes()
    stab = stability(raw, params, th, cutoff=None)
    counts_by_conf = conf_counts(cells, fit.summarize(cells, fit.y_train, th), loaded.routes)
    metrics: dict[str, Any] = suite.to_dict()
    metrics["prospective_kernel"] = (None if suite.prospective is None
                                     else ka.prospective_kernel_report(loaded.prospective, loaded.prospective_scores))
    metrics["thresholds"] = {**thresholds_json(th), "conf_selected": bool(tuned.report["conf_selected"])}
    metrics["tuning"] = tuned.report
    metrics["stability"] = stab
    metrics["vs_active"] = vs_active
    capacity: dict[str, Any] = {"wall_hours": (time.monotonic() - t0) / 3600.0, "peak_rss_gb": peak_rss_gb(),
                                "bundle_mb": len(bundle_bytes) / 1024**2, "n_cells": int(cells.n),
                                "curve": benchmark_curve(params) if benchmark else None}
    metrics["capacity"] = capacity
    metrics = _clean(metrics)
    card = _clean({
        "version": version, "family": FAMILY, "data_cutoff": raw.production_cutoff.isoformat(),
        "train_start": TRAIN_START.isoformat(), "notes": list(MODEL_CARD_NOTES),
        "params": bundle.manifest["params"], "thresholds": metrics["thresholds"], "tuning": tuned.report,
        "conf_counts": counts_by_conf, "incidents_in_cells": float(counts.y.sum()), "state_only_mass": float(counts.state_mass.sum()),
        "sensitivity_log_score": {k: v.get("log_score") for k, v in suite.sensitivity.items()},
        "main_log_score": suite.main.get("log_score"), "stability": {k: stab[k] for k in (
            "method", "median_spearman", "median_band_churn", "n_subsamples", "n_failed", "passed")},
        "capacity": capacity, "diagnostics": bundle.manifest.get("diagnostics", {}),
    })
    return Candidate(version, fit, cells, th, metrics, active_metrics, card, bundle_bytes)


@dataclass(frozen=True)
class Outcome:
    version: str
    gate: GateResult
    diff: DiffReport


async def record(conn: AsyncConnection, loaded: Loaded, cand: Candidate, *, today: date) -> Outcome:
    act = loaded.active
    await registry.insert_candidate(
        conn, version=cand.version, family=FAMILY, data_hash=data_hash(loaded.raw), train_start=TRAIN_START,
        data_cutoff=loaded.raw.production_cutoff, exposure_as_of=loaded.raw.production_cutoff,
        metrics=cand.metrics, thresholds=_clean(thresholds_json(cand.thresholds)), model_card=cand.model_card,
        bundle=cand.bundle, parent_version=None if act is None else act.version)
    summary = cand.fit.summarize(cand.cells, cand.fit.y_train, cand.thresholds)
    n = await write_scores(conn, cand.version, cand.cells, summary, loaded.routes)
    month = today.month

    async def rows(version: str) -> list[tuple[int, str, str, float | None]]:
        res = await conn.execute(ACTIVE_ROWS_SQL, {"v": version, "m": month})
        return [(int(r), str(g), str(s), None if p is None else float(p)) for r, g, s, p in res.all()]

    old = [] if act is None else await rows(act.version)
    diff = diff_report(await rows(cand.version), old, reference_month=month,
                       new_family=act is None or act.family != FAMILY)
    diff = dataclasses.replace(diff, metric_deltas=metric_deltas(cand.metrics, cand.active_metrics))
    gate = evaluate_gate(cand.metrics, cand.active_metrics, active_family="" if act is None else act.family)
    await registry.set_gate(conn, cand.version, _clean(dataclasses.asdict(gate)), _clean(dataclasses.asdict(diff)), n)
    if gate.passed:
        await registry.promote(conn, cand.version, by="auto")
    else:
        await registry.reject(conn, cand.version)
    pruned = await registry.prune(conn, today=today)
    logger.info("wrote %d score rows; pruned %d score rows and %d bundles", n, pruned.scores_deleted,
                pruned.bundles_nulled)
    return Outcome(cand.version, gate, diff)


def failed_components(gate: GateResult) -> list[str]:
    return [k for k, v in gate.components.items() if not v["passed"]]


OUTPUTS = ("issue.md", "status.json")


def clear_outputs(out_dir: Path) -> None:
    """A failed run must not leave an earlier run's issue.md or status.json for the runbook to read."""
    for name in OUTPUTS:
        (out_dir / name).unlink(missing_ok=True)


def write_outputs(out_dir: Path, outcome: Outcome) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "issue.md").write_text(issue_markdown(outcome.version, outcome.gate, outcome.diff, public=True) + "\n",
                                      encoding="utf-8")
    failed = failed_components(outcome.gate)
    status = {"version": outcome.version, "promoted": outcome.gate.passed, "large_shift": outcome.diff.large_shift,
              "rejected_reason": None if outcome.gate.passed else ", ".join(failed)}
    (out_dir / "status.json").write_text(_dumps(status, indent=2) + "\n", encoding="utf-8")


async def run(engine: AsyncEngine, *, today: date, benchmark: bool, out_dir: Path) -> Outcome:
    t0 = time.monotonic()
    async with engine.connect() as conn:
        loaded = await load(conn, today=today)
    cand = build_candidate(loaded, today=today, t0=t0, benchmark=benchmark)
    async with engine.begin() as conn:
        outcome = await record(conn, loaded, cand, today=today)
    write_outputs(out_dir, outcome)
    return outcome


async def kernel_baseline(engine: AsyncEngine, *, today: date) -> str:
    """MVP-0 accept (P3:505): the kernel's metrics on the current folds become the first registry row,
    active, only while the registry is empty."""
    async with engine.connect() as conn:
        if (await conn.execute(text("SELECT count(*) FROM model.registry"))).scalar_one():
            raise registry.PromotionRefused("the registry is not empty; the kernel baseline is only the first row")
        raw = await load_raw(conn, BACKTEST_CUTOFFS, today=today)
    version = f"{KERNEL}-{today.isoformat()}"
    metrics = _clean({"main": ka.kernel_report(raw, _folds(raw))})
    async with engine.begin() as conn:
        if (await conn.execute(text("SELECT count(*) FROM model.registry"))).scalar_one():
            raise registry.PromotionRefused("the registry is not empty; the kernel baseline is only the first row")
        await registry.insert_candidate(
            conn, version=version, family=KERNEL, data_hash=data_hash(raw), train_start=TRAIN_START,
            data_cutoff=raw.production_cutoff, exposure_as_of=raw.production_cutoff, metrics=metrics, thresholds=None,
            model_card={"family": KERNEL, "note": "kernel metrics on the current folds (MVP-0 baseline)"}, bundle=None,
            parent_version=None)
        await conn.execute(text("UPDATE model.registry SET status = 'active', promoted_at = now(), "
                                "promoted_by = 'owner' WHERE version = :v"), {"v": version})
    return version


async def rollback(engine: AsyncEngine, *, version: str | None) -> tuple[str, str]:
    async with engine.begin() as conn:
        if version is None:
            return await registry.rollback(conn)
        current = await registry.active(conn)
        await registry.promote(conn, version, by="owner")
        return ("none" if current is None else current.version), version


def _engine() -> AsyncEngine:
    from app.ml.db import trainer_engine

    return trainer_engine()


async def _with_engine(fn: Any, **kw: Any) -> Any:
    engine = _engine()
    try:
        return await fn(engine, **kw)
    finally:
        await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m app.ml.train")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="train, materialize, gate, then promote or reject")
    r.add_argument("--today", type=date.fromisoformat, default=None)
    r.add_argument("--benchmark", action="store_true", help="also fit 1x/2x/4x synthetic cells (P3:223)")
    r.add_argument("--out-dir", type=Path, default=Path("out"), help="where issue.md and status.json go")
    k = sub.add_parser("kernel-baseline", help="insert the kernel row as active into an empty registry")
    k.add_argument("--today", type=date.fromisoformat, default=None)
    b = sub.add_parser("rollback", help="restore previous, or promote --version as the owner")
    b.add_argument("--version", default=None)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    today = getattr(args, "today", None) or date.today()
    try:
        if args.cmd == "run":
            clear_outputs(args.out_dir)
            outcome = asyncio.run(_with_engine(run, today=today, benchmark=args.benchmark, out_dir=args.out_dir))
            if outcome.gate.passed:
                print(f"promoted {outcome.version}")
            else:
                print(f"rejected {outcome.version}: {', '.join(failed_components(outcome.gate))}")
            ping(settings.HEALTHCHECKS_TRAIN_URL)
        elif args.cmd == "kernel-baseline":
            print(f"kernel baseline {asyncio.run(_with_engine(kernel_baseline, today=today))}")
        else:
            old, new = asyncio.run(_with_engine(rollback, version=args.version))
            print(f"rolled back {old} -> {new}")
        return 0
    except (registry.RollbackRefused, registry.PromotionRefused) as exc:
        # These messages name versions and states only, never connection details.
        print(f"refused: {exc}")
        return 1
    except BaseException as exc:
        if not isinstance(exc, (Exception, SystemExit)):
            raise
        logger.error("%s failed: %s", args.cmd, type(exc).__name__)
        print(f"failed: {type(exc).__name__}")
        if args.cmd == "run":
            ping(settings.HEALTHCHECKS_TRAIN_URL, "/fail")
        return 1

if __name__ == "__main__":
    sys.exit(main())
