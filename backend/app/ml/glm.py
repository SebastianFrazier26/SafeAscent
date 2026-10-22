"""Penalized Poisson regression by Newton/IRLS, written here rather than imported (owner rule:
the model is built in-house). Penalty is 0.5 * sum(p_j * b_j^2); a nonneg column found negative
is fixed at 0 and the fit is redone (active set), which keeps route density's gamma >= 0 so more
exposure with fixed incidents can never raise RR (P3:469)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

ETA_CLIP = 30.0
_MIN_STEP = 1e-10


@dataclass(frozen=True)
class GlmFit:
    coef: NDArray[np.float64]
    iterations: int
    converged: bool
    fixed_zero: tuple[int, ...]
    deviance: float


def linear_predictor(X: NDArray[np.float64], coef: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.asarray(X @ coef, dtype=np.float64)


def _mu(X: NDArray[np.float64], b: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.asarray(np.exp(np.clip(X @ b, -ETA_CLIP, ETA_CLIP)), dtype=np.float64)


def _objective(
    X: NDArray[np.float64], y: NDArray[np.float64], b: NDArray[np.float64], pen: NDArray[np.float64]
) -> float:
    eta = np.clip(X @ b, -ETA_CLIP, ETA_CLIP)
    return float(np.sum(np.exp(eta) - y * eta) + 0.5 * np.sum(pen * b * b))


def _gradient(
    X: NDArray[np.float64], y: NDArray[np.float64], b: NDArray[np.float64], pen: NDArray[np.float64]
) -> NDArray[np.float64]:
    return np.asarray(X.T @ (_mu(X, b) - y) + pen * b, dtype=np.float64)


def _irls(
    X: NDArray[np.float64],
    y: NDArray[np.float64],
    pen: NDArray[np.float64],
    tol: float,
    max_iter: int,
) -> tuple[NDArray[np.float64], int, bool]:
    b = np.zeros(X.shape[1])
    b[0] = np.log(max(float(y.mean()), np.exp(-ETA_CLIP)))
    obj = _objective(X, y, b, pen)
    for it in range(1, max_iter + 1):
        mu = _mu(X, b)
        grad = X.T @ (mu - y) + pen * b
        hess = (X.T * mu) @ X + np.diag(pen)
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            # Collinear unpenalized columns: the minimum-norm Newton direction still descends.
            step = np.linalg.lstsq(hess, grad, rcond=None)[0]
        t = 1.0
        while True:
            cand = b - t * step
            cand_obj = _objective(X, y, cand, pen)
            if cand_obj <= obj + 1e-12 * (1.0 + abs(obj)):
                break
            t /= 2.0
            if t < _MIN_STEP:
                # No descent along the Newton direction: stationary only if the step is negligible.
                small = bool(np.max(np.abs(step)) < tol * (1.0 + np.max(np.abs(b))))
                return b, it, small
        done = np.max(np.abs(cand - b)) < tol * (1.0 + np.max(np.abs(b)))
        b, obj = cand, cand_obj
        if done:
            return b, it, True
    return b, max_iter, False


def _deviance(y: NDArray[np.float64], mu: NDArray[np.float64]) -> float:
    with np.errstate(divide="ignore", invalid="ignore"):
        term = np.where(y > 0, y * np.log(y / mu), 0.0)
    return float(2.0 * np.sum(term - (y - mu)))


def fit_poisson(
    X: NDArray[np.float64],
    y: NDArray[np.float64],
    penalty: NDArray[np.float64],
    *,
    nonneg: Sequence[int] = (),
    tol: float = 1e-9,
    max_iter: int = 100,
) -> GlmFit:
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    penalty = np.asarray(penalty, dtype=np.float64)
    if X.ndim != 2 or y.ndim != 1 or X.shape[0] != y.shape[0] or X.shape[0] == 0:
        raise ValueError("X must be (n, p) and y length n, n > 0")
    n_col = X.shape[1]
    if penalty.shape != (n_col,) or np.any(penalty < 0) or not np.all(np.isfinite(penalty)):
        raise ValueError("penalty must be finite, non-negative, one per column")
    if not np.all(np.isfinite(X)):
        raise ValueError("X must be finite")
    if np.any(y < 0) or not np.all(np.isfinite(y)):
        raise ValueError("counts must be finite and non-negative")
    if not np.allclose(X[:, 0], 1.0):
        raise ValueError("column 0 must be the intercept")
    if any(j <= 0 or j >= n_col for j in nonneg):
        raise ValueError("nonneg indices must name non-intercept columns")

    if not np.any(y > 0):
        # The MLE sits at eta = -inf; no finite fit exists, so say so rather than invent one.
        coef = np.zeros(n_col)
        coef[0] = -ETA_CLIP
        return GlmFit(coef, 0, False, (), _deviance(y, _mu(X, coef)))

    # An all-zero column is unidentified (flat likelihood); 0 is its minimum-norm value.
    dead = {j for j in range(1, n_col) if not np.any(X[:, j])}
    nonneg_set = set(nonneg)
    fixed: set[int] = set()
    total_iters = 0
    seen: set[frozenset[int]] = set()
    while True:
        seen.add(frozenset(fixed))
        keep = [j for j in range(n_col) if j not in fixed and j not in dead]
        b_keep, iters, ok = _irls(X[:, keep], y, penalty[keep], tol, max_iter)
        total_iters += iters
        coef = np.zeros(n_col)
        coef[keep] = b_keep
        if not ok:
            return GlmFit(coef, total_iters, False, tuple(sorted(fixed)), _deviance(y, _mu(X, coef)))
        neg = [j for j in nonneg_set - fixed if coef[j] < 0.0]
        if neg:
            fixed.add(min(neg, key=lambda j: coef[j]))
        else:
            # KKT for a bound at 0: release a fixed column whose objective still falls as it rises.
            grad = _gradient(X, y, coef, penalty)
            scale = tol * (1.0 + float(np.max(np.abs(grad[keep]))))
            want_up = [j for j in fixed if grad[j] < -scale]
            if not want_up:
                return GlmFit(coef, total_iters, True, tuple(sorted(fixed)), _deviance(y, _mu(X, coef)))
            fixed.discard(min(want_up, key=lambda j: grad[j]))
        if frozenset(fixed) in seen:
            return GlmFit(coef, total_iters, False, tuple(sorted(fixed)), _deviance(y, _mu(X, coef)))
