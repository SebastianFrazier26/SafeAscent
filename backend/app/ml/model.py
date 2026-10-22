"""Phase 3 fit/predict orchestration (P3 Task 15): stage 1 penalized Poisson GLM, archetypes, stage 2
EB, then bundle export. Held-out cells (train_mask False) contribute neither y nor mu0 to either stage,
nor covariate statistics, nor archetype centres, nor evidence points.

EB keys regions as "state|region": an area's state (GNIS/lat-lon) can disagree with its MP path near
borders, and fit_eb refuses a region that does not nest in one state (controller ruling 2026-10-22; a
border-split region then pools as two groups).

geo_evidence is the chain's geographic mass (posterior.n_eff_geo > GEO_TOL) or state-only incident mass
for the cell's (state, group) (DP4, controller ruling 2026-10-22): such incidents never enter an EB level's
O, but they keep a cell out of gray. Only states holding a training area count, so a held-out state never
lights up its own evidence."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.ml.archetypes import (
    ARCH_FEATURES,
    Standardizer,
    area_feature_matrix,
    kmeans,
    nearest_distance,
    weighted_sqdist,
)
from app.ml.bundle import ModelBundle
from app.ml.cells import TYPE_GROUPS
from app.ml.dataset import STATIC_FEATURES, CellData, Counts
from app.ml.eb import CellPost, EbFit, fit_eb
from app.ml.explain import EVIDENCE, TYPE_SEASON, explain
from app.ml.features import Design, Lambdas, TickPolicy, build_design
from app.ml.folds import Fold
from app.ml.glm import ETA_CLIP, GlmFit, fit_poisson, linear_predictor
from app.ml.posterior import GEO_TOL, CellSummary, Thresholds, interval90, n_eff_geo, summarize

F64 = NDArray[np.float64]
I64 = NDArray[np.int64]
BoolArr = NDArray[np.bool_]
FAMILY = "eb_v1"
PARITY_TOL = 1e-6
PARITY_SAMPLE = 200

MODEL_CARD_NOTES = (
    "The design is rank-deficient in fam_mountain vs the ridged a_* group effects; it is identifiable "
    "through the ridge, and the unpenalized fam_mountain absorbs the shared family effect (DP3).",
    "State-only incidents (park_centroid, region_fallback, unknown, far points) never enter an EB level's O "
    "(DP4, F39), but they count as geo evidence for their (state, type group): such a cell is scored, never "
    "gray, and reads at most 'low' unless other evidence lifts it. The card reports the state-only share per "
    "state (diagnostics.state_only_share) and the cells kept out of gray only by it.",
    "hazard_center[g, m] is log of the exposure-weighted mean hazard exp(eta - eta_exposure) over the cells "
    "of type group g and month m; log_rref - hazard_center = log theta_bar(g, m), the exposure-weighted "
    "posterior mean the evidence term is measured against (about 0 when the EB is centred).",
    "Rolling-origin folds are point-in-time for ticks only (F22): season_share and route_density use "
    "today's climate window and catalog, so backtests are not fully point-in-time.",
)


class ModelFitError(RuntimeError):
    pass


class BundleParityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelParams:
    k_arch: int = 16
    arch_weights: tuple[float, ...] = (1.0,) * len(ARCH_FEATURES)
    blend: bool = True
    lambdas: Lambdas = Lambdas()
    tick_policy: TickPolicy = TickPolicy()
    seed: int = 0

    def __post_init__(self) -> None:
        if self.k_arch < 1:
            raise ValueError("k_arch must be >= 1")
        if len(self.arch_weights) != len(ARCH_FEATURES):
            raise ValueError(f"arch_weights needs {len(ARCH_FEATURES)} weights, one per ARCH_FEATURES")
        if not all(math.isfinite(w) and w >= 0 for w in self.arch_weights) or sum(self.arch_weights) <= 0:
            raise ValueError("arch_weights must be finite, >= 0 and not all zero")


def fold_train_mask(cells: CellData, fold: Fold) -> BoolArr:
    """Training cells for a fold: every cell outside fold.held_out_states (Task 9 carry: folds do not
    enforce this themselves)."""
    held = np.array([s in fold.held_out_states for s in cells.states], dtype=np.bool_)
    out: BoolArr = ~held[cells.state_idx[cells.area_idx]]
    return out


def eb_cells(cells: CellData) -> CellData:
    """cells with region keys qualified by state, so every region nests in exactly one state."""
    a_state = cells.state_idx
    pair = a_state * len(cells.regions) + cells.region_idx
    uniq, inv = np.unique(pair, return_inverse=True)
    regions = tuple(f"{cells.states[int(p) // len(cells.regions)]}|{cells.regions[int(p) % len(cells.regions)]}"
                    for p in uniq)
    return dataclasses.replace(cells, region_idx=inv.astype(np.int64), regions=regions)


def _assign(Z: F64, centers: F64, w: F64) -> I64:
    if centers.shape[0] == 0:
        return np.full(Z.shape[0], -1, dtype=np.int64)
    D = weighted_sqdist(Z, centers, w)
    Dinf = np.where(np.isnan(D), np.inf, D)
    idx = np.argmin(Dinf, axis=1).astype(np.int64)
    out: I64 = np.where(np.isfinite(Dinf[np.arange(Z.shape[0]), idx]), idx, -1).astype(np.int64)
    return out


def _quantiles(x: NDArray[Any]) -> dict[str, float]:
    if x.size == 0:
        return {"n": 0}
    q = np.quantile(x.astype(np.float64), [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0])
    return {"n": int(x.size), "min": float(q[0]), "p10": float(q[1]), "p25": float(q[2]), "median": float(q[3]),
            "p75": float(q[4]), "p90": float(q[5]), "max": float(q[6]), "mean": float(x.mean())}


@dataclass(frozen=True)
class Fitted:
    design: Design
    glm: GlmFit
    eb: EbFit
    arch_of_area: I64
    std: Standardizer
    ev_points: tuple[F64, ...]  # per type group: standardized features of training areas holding incidents
    params: ModelParams
    years: float
    arch_centers: F64
    area_ids: I64
    y_train: F64  # y * mask in the caller's cell order: the default y_own for to_bundle
    state_evidence: NDArray[np.bool_]  # (states, groups): state-only mass > GEO_TOL, training states only
    state_idx: I64
    region_idx: I64
    states: tuple[str, ...]
    regions: tuple[str, ...]
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def _check_cells(self, cells: CellData) -> None:
        # EB levels are keyed by area/region/state index, so only the same area table can be scored.
        if not (np.array_equal(cells.area_id, self.area_ids) and np.array_equal(cells.state_idx, self.state_idx)
                and np.array_equal(cells.region_idx, self.region_idx) and cells.states == self.states
                and cells.regions == self.regions):
            raise ValueError("cells carry a different area table than the fit; EB keys would not line up")

    def design_for(self, cells: CellData) -> Design:
        return build_design(cells, policy=self.params.tick_policy, lambdas=self.params.lambdas,
                            fit_stats=self.design)

    def _z(self, cells: CellData) -> F64:
        return self.std.apply(area_feature_matrix(cells))

    def predict(self, cells: CellData, y_own: F64) -> tuple[F64, CellPost, F64, BoolArr]:
        self._check_cells(cells)
        design = self.design_for(cells)
        mu0 = np.exp(np.clip(linear_predictor(design.X, self.glm.coef), -ETA_CLIP, ETA_CLIP))
        cp = self.eb.cell_posterior(eb_cells(cells), np.asarray(y_own, dtype=np.float64), mu0, self.arch_of_area)
        Z = self._z(cells)
        w = np.array(self.params.arch_weights, dtype=np.float64)
        dist = np.full(cells.n, np.inf)
        for gi in range(len(TYPE_GROUPS)):
            sel = np.flatnonzero(cells.g == gi)
            if sel.size == 0:
                continue
            areas, inv = np.unique(cells.area_idx[sel], return_inverse=True)
            dist[sel] = nearest_distance(Z[areas], self.ev_points[gi], w)[inv]
        geo = np.asarray((n_eff_geo(cp) > GEO_TOL) | self._state_only(cells), dtype=np.bool_)
        return mu0, cp, dist, geo

    def _state_only(self, cells: CellData) -> NDArray[np.bool_]:
        out: NDArray[np.bool_] = self.state_evidence[cells.state_idx[cells.area_idx], cells.g.astype(np.int64)]
        return out

    def summarize(self, cells: CellData, y_own: F64, thresholds: Thresholds) -> CellSummary:
        _, cp, dist, geo = self.predict(cells, y_own)
        return summarize(cells, self.design_for(cells), self.glm.coef, cp, thresholds=thresholds,
                         evidence_dist=dist, years=self.years, geo_evidence=geo)

    def to_bundle(self, version: str, thresholds: Thresholds, cells: CellData,
                  y_own: F64 | None = None) -> ModelBundle:
        """Arrays plus a JSON manifest, no per-cell posteriors (those live in model.route_scores). Refuses
        to export unless explain() on a sample of cells reproduces each cell's log posterior mean as its
        evidence term (BundleParityError), so a bundle inconsistency cannot hide in the residual."""
        y = self.y_train if y_own is None else np.asarray(y_own, dtype=np.float64)
        if y.shape != (cells.n,):
            raise ValueError("y_own must have one value per cell (default: the training y, same cell order)")
        _, cp, dist, geo = self.predict(cells, y)
        design = self.design_for(cells)
        coef = self.glm.coef
        s = summarize(cells, design, coef, cp, thresholds=thresholds, evidence_dist=dist, years=self.years,
                      geo_evidence=geo)
        _, mean, _ = interval90(cp.post.shape, cp.post.rate, thresholds.phi)
        ex = list(design.exposure_cols)
        eta = linear_predictor(design.X, coef)
        eta_exp = linear_predictor(design.X[:, ex], coef[ex])
        mu0 = np.exp(np.clip(eta, -ETA_CLIP, ETA_CLIP))
        exposure = np.exp(eta_exp)
        hazard = np.exp(eta - eta_exp)
        # Same r_ref as summarize; hazard_center is the exposure-weighted typical hazard of (g, m), so
        # log_rref - hazard_center = log theta_bar(g, m), the drift the Task 14 carry asks to watch.
        log_rref = np.full((len(TYPE_GROUPS), 12), np.nan)
        hazard_center = np.full((len(TYPE_GROUPS), 12), np.nan)
        const: dict[str, dict[str, float]] = {g: {} for g in TYPE_GROUPS}
        for gi, g in enumerate(TYPE_GROUPS):
            for m in range(1, 13):
                sel = (cells.g == gi) & (cells.month == m)
                if not sel.any():
                    continue  # no cells: no constant at all, never a 0
                log_rref[gi, m - 1] = math.log(float((mu0[sel] * mean[sel]).sum() / exposure[sel].sum()))
                hazard_center[gi, m - 1] = math.log(float((exposure[sel] * hazard[sel]).sum()
                                                          / exposure[sel].sum()))
                const[g][str(m)] = -float(log_rref[gi, m - 1])
        th = dataclasses.asdict(thresholds)
        th["d_star"] = None if math.isinf(thresholds.d_star) else thresholds.d_star  # None = never gray by distance
        params = dataclasses.asdict(self.params)
        params["arch_weights"] = list(self.params.arch_weights)
        bundle = ModelBundle(
            version=version, family=FAMILY,
            arrays={"coef": coef, "center": design.center, "scale": design.scale, "arch_centers": self.arch_centers,
                    "arch_weights": np.array(self.params.arch_weights, dtype=np.float64),
                    "std_mean": self.std.mean, "std_sd": self.std.sd, "log_rref": log_rref,
                    "hazard_center": hazard_center},
            manifest={"names": list(design.names), "tick_ref": design.tick_ref,
                      "families_on": sorted(design.families_on), "const": const, "thresholds": th,
                      "params": params, "years": self.years,
                      "kappas": {k: float(getattr(self.eb, f"kappa_{k}"))
                                 for k in ("state", "region", "area", "arch", "cell")},
                      "notes": list(MODEL_CARD_NOTES)},
        )
        # Parity runs on the bytes that ship, not on the in-memory object.
        shipped = ModelBundle.from_bytes(bundle.to_bytes())
        _check_shipped(shipped, design)
        median = check_parity(shipped, cells, s, mean)
        kept = geo & ~(n_eff_geo(cp) > GEO_TOL) & (s.n_eff_feature == 0) & (dist > thresholds.d_star)
        diagnostics = {**self.diagnostics, "median_route": median,
                       "theta_bar_gm": _finite_quantiles(log_rref - hazard_center),
                       "cells_kept_out_of_gray_only_by_state_mass": int(kept.sum())}
        return dataclasses.replace(bundle, manifest={**bundle.manifest, "diagnostics": diagnostics})


def _check_shipped(shipped: ModelBundle, design: Design) -> None:
    """The exposure side cancels out of explain(), so parity cannot see it; compare it directly."""
    ex = list(design.exposure_cols)
    ok = (shipped.manifest["tick_ref"] == design.tick_ref
          and shipped.manifest["families_on"] == sorted(design.families_on)
          and np.array_equal(shipped.arrays["center"][ex], design.center[ex])
          and np.array_equal(shipped.arrays["scale"][ex], design.scale[ex]))
    if not ok:
        raise BundleParityError("shipped bundle's tick_ref, families_on or exposure center/scale differ from the fit")


def _finite_quantiles(x: F64) -> dict[str, float]:
    return _quantiles(np.exp(x[np.isfinite(x)]))


def _raw_features(cells: CellData, c: int) -> dict[str, float | None]:
    return {f: (None if math.isnan(v) else float(v)) for f, v in zip(STATIC_FEATURES, cells.static[c])}


def check_parity(bundle: ModelBundle, cells: CellData, s: CellSummary, mean: F64) -> dict[str, Any]:
    """explain()'s evidence term must equal log(posterior mean) and its terms must sum to log(rr_mean),
    within PARITY_TOL, on an evenly spaced sample of sufficient cells, one per (g, m), and the median route. Returns the
    median route's type_season and evidence terms (the theta_bar ~ 1 check, for the runbook)."""
    suff = np.flatnonzero(~s.insufficient)
    if suff.size == 0:
        raise BundleParityError("no sufficient cell to check the bundle against")
    w = cells.n_routes[suff]
    order = np.argsort(s.rr_mean[suff], kind="stable")
    cum = np.cumsum(w[order])
    med = int(suff[order[int(np.searchsorted(cum, 0.5 * cum[-1]))]])
    spread = suff[np.linspace(0, suff.size - 1, min(PARITY_SAMPLE, suff.size)).astype(np.int64)]
    gm = cells.g[suff].astype(np.int64) * 12 + cells.month[suff].astype(np.int64)
    _, first = np.unique(gm, return_index=True)  # every (g, m) with a sufficient cell gets one
    sample = np.unique(np.concatenate([spread, suff[first], [med]]))
    out: dict[str, Any] = {}
    for c in sample.tolist():
        rr = float(s.rr_mean[c])
        terms = explain(bundle, type_group=TYPE_GROUPS[int(cells.g[c])], month=int(cells.month[c]),
                        raw_features=_raw_features(cells, c), rr_mean=rr, n_eff_geo=float(s.n_eff_geo[c]),
                        n_eff_feature=float(s.n_eff_feature[c]), top=None)
        by = {t.name: t.log_effect for t in terms}
        total = sum(t.log_effect for t in terms)
        if abs(total - math.log(rr)) > PARITY_TOL or abs(by[EVIDENCE] - math.log(float(mean[c]))) > PARITY_TOL:
            raise BundleParityError(
                f"bundle parity failed on cell {c}: evidence {by[EVIDENCE]:.9g} vs log mean "
                f"{math.log(float(mean[c])):.9g}, terms {total:.9g} vs log rr {math.log(rr):.9g}")
        if c == med:
            area_id = int(cells.area_id[int(cells.area_idx[c])])
            out = {"area_id": area_id, "type_group": TYPE_GROUPS[int(cells.g[c])], "month": int(cells.month[c]),
                   "rr_mean": rr, TYPE_SEASON: by.get(TYPE_SEASON, 0.0), EVIDENCE: by[EVIDENCE]}
    return out


def fit_model(cells: CellData, counts: Counts, params: ModelParams, *,
              train_mask: BoolArr | None = None) -> Fitted:
    n = cells.n
    mask = np.ones(n, dtype=np.bool_) if train_mask is None else np.asarray(train_mask)
    if mask.dtype != np.bool_ or mask.shape != (n,):
        raise ValueError("train_mask must be a bool array with one value per cell")
    if not mask.any():
        raise ValueError("train_mask selects no cell")
    y_in = np.asarray(counts.y, dtype=np.float64)
    if y_in.shape != (n,):
        raise ValueError("counts.y must have one value per cell")
    # A canonical (area, group, month) order makes every reduction bitwise independent of row order.
    canon = np.lexsort((cells.month, cells.g, cells.area_idx)).astype(np.int64)
    cc, y, m = cells.take(canon), y_in[canon], mask[canon]

    train_design = build_design(cc.subset(m), policy=params.tick_policy, lambdas=params.lambdas)
    design = build_design(cc, policy=params.tick_policy, lambdas=params.lambdas, fit_stats=train_design)
    glm = fit_poisson(design.X[m], y[m], design.penalty, nonneg=design.nonneg)
    if not glm.converged:
        # Separation (an unpenalized month/family indicator with no events) leaves no finite optimum;
        # any coefs here would be a stall, not a fit (Task 10 ruling).
        raise ModelFitError(f"stage-1 GLM did not converge after {glm.iterations} iterations; refusing its coefs")
    mu0 = np.exp(np.clip(linear_predictor(design.X, glm.coef), -ETA_CLIP, ETA_CLIP))

    w = np.array(params.arch_weights, dtype=np.float64)
    F = area_feature_matrix(cc)
    train_areas = np.unique(cc.area_idx[m])
    std = Standardizer.fit(F[train_areas])
    Z = std.apply(F)
    _, centers = kmeans(Z[train_areas], params.k_arch, w, seed=params.seed)
    arch_of_area = _assign(Z, centers, w)

    y_tr = np.where(m, y, 0.0)
    ecc = eb_cells(cc)
    eb = fit_eb(ecc, y_tr, np.where(m, mu0, 0.0), arch_of_area, blend=params.blend)

    ev_points: list[F64] = []
    for gi in range(len(TYPE_GROUPS)):
        areas = np.unique(cc.area_idx[(cc.g == gi) & (y_tr > 0)])
        ev_points.append(Z[areas])

    state_mass = np.asarray(counts.state_mass, dtype=np.float64)
    if state_mass.shape != (len(cells.states), len(TYPE_GROUPS)):
        raise ValueError("counts.state_mass must be (states, type groups)")
    train_states = np.unique(cc.state_idx[train_areas])
    is_train_state = np.zeros(len(cells.states), dtype=np.bool_)
    is_train_state[train_states] = True
    # A held-out state's own state-only incidents are test data: they must not mark its cells as evidenced.
    state_evidence = (state_mass > GEO_TOL) & is_train_state[:, None]

    labels = arch_of_area[train_areas]
    region_of_train = ecc.region_idx[train_areas]
    cell_y_by_state = np.bincount(cc.state_idx[cc.area_idx], weights=y_tr, minlength=len(cells.states))
    diagnostics: dict[str, Any] = {
        "glm_iterations": int(glm.iterations),
        "glm_fixed_zero": [design.names[j] for j in glm.fixed_zero],
        "n_train_cells": int(m.sum()), "n_train_areas": int(train_areas.size),
        "areas_per_region": _quantiles(np.bincount(region_of_train)[np.unique(region_of_train)]),
        "kappa": {k: float(getattr(eb, f"kappa_{k}")) for k in ("state", "region", "area", "arch", "cell")},
        "arch_sizes": [int(v) for v in np.bincount(labels[labels >= 0], minlength=centers.shape[0])],
        "areas_without_archetype": int((arch_of_area < 0).sum()),
        # Whole counting-window totals (Counts), not restricted to training cells.
        "dropped_by_reason_window_total": {str(k): float(v) for k, v in sorted(counts.dropped.items())},
        "placed_by_level_window_total": {str(k): float(v) for k, v in sorted(counts.placed.items())},
        "state_only_mass_window_total": {cc.states[int(si)]: float(state_mass[si].sum()) for si in train_states},
        "state_only_share": {
            cc.states[int(si)]: (float(state_mass[si].sum() / (state_mass[si].sum() + cell_y_by_state[si]))
                                 if state_mass[si].sum() + cell_y_by_state[si] > 0 else None)
            for si in train_states},
    }
    return Fitted(design=build_design(cells, policy=params.tick_policy, lambdas=params.lambdas,
                                      fit_stats=train_design),
                  glm=glm, eb=eb, arch_of_area=arch_of_area, std=std, ev_points=tuple(ev_points), params=params,
                  years=float(counts.years), arch_centers=np.asarray(centers, dtype=np.float64),
                  area_ids=np.asarray(cells.area_id, dtype=np.int64), y_train=np.where(mask, y_in, 0.0),
                  state_evidence=state_evidence, state_idx=cells.state_idx.copy(),
                  region_idx=cells.region_idx.copy(), states=cells.states, regions=cells.regions,
                  diagnostics=diagnostics)
