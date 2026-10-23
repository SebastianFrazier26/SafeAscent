"""Stored-score reads (P3 Task 22, MVP-1 serving): the active model.registry version's
model.route_scores row for a route and month, never a live computation. A route without a row,
or no active version, is unavailable (None), never 0 and never gray: gray means the model has a
row and it holds no evidence at all."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.ml.bundle import ModelBundle
from app.ml.registry import load_bundle
from app.services.risk_bands import INSUFFICIENT_DATA_COLOR, DataStatus, SafetyColorCode, color_code_for
from app.utils.cache import SAFETY_SCORE_TTL, build_stored_score_key, cache_get, cache_set

logger = logging.getLogger(__name__)

Confidence = Literal["high", "medium", "low"]
CONFIDENCE_LEVELS: frozenset[str] = frozenset({"high", "medium", "low"})
# Bound on one ANY(...) array so the bulk map read never ships one 170K-element parameter.
BATCH_IDS = 20_000

_SCORE_COLUMNS = ("route_id", "month", "model_version", "type_group", "insufficient", "insufficient_reason",
                  "rr_mean", "rr_q05", "rr_q95", "percentile", "pct_q05", "pct_q95", "conf_level", "n_eff_geo",
                  "n_eff_feature", "per10k_mean", "per10k_q05", "per10k_q95", "per10k_reason")
_SELECT = ", ".join(f"s.{c}" for c in _SCORE_COLUMNS)
ACTIVE_SQL = text("SELECT version FROM model.registry WHERE status = 'active'")
ONE_SQL = text(f"SELECT {_SELECT} FROM model.route_scores s "
               "WHERE s.model_version = :version AND s.route_id = :route_id AND s.month = :month")
PROFILE_SQL = text(f"SELECT {_SELECT} FROM model.route_scores s "
                   "WHERE s.model_version = :version AND s.route_id = :route_id ORDER BY s.month")
# The join on status = 'active' reads the version and its rows in one statement, so a promotion
# between two statements can never pair one version's id with another's rows.
MANY_SQL = text(f"SELECT {_SELECT} FROM model.route_scores s "
                "JOIN model.registry r ON r.version = s.model_version AND r.status = 'active' "
                "WHERE s.route_id = ANY(CAST(:ids AS bigint[])) AND s.month = :month")


@dataclass(frozen=True)
class StoredScore:
    route_id: int
    month: int
    model_version: str
    type_group: str
    insufficient: bool
    insufficient_reason: str | None
    rr_mean: float | None
    rr_q05: float | None
    rr_q95: float | None
    percentile: float | None
    pct_q05: float | None
    pct_q95: float | None
    conf_level: str
    n_eff_geo: float | None
    n_eff_feature: float | None
    per10k_mean: float | None
    per10k_q05: float | None
    per10k_q95: float | None
    per10k_reason: str | None


_FLOATS = ("rr_mean", "rr_q05", "rr_q95", "percentile", "pct_q05", "pct_q95", "n_eff_geo", "n_eff_feature",
           "per10k_mean", "per10k_q05", "per10k_q95")
_FIELD_NAMES = tuple(f.name for f in fields(StoredScore))


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def servable(s: StoredScore) -> bool:
    """Whether a row can be shown as it is. The 0012 CHECKs already hold every rule but one:
    n_eff may be NULL on any row, and an ok row without its evidence split cannot be served."""
    if s.insufficient:
        return (s.insufficient_reason == "no_evidence" and s.percentile is None and s.pct_q05 is None
                and s.pct_q95 is None)
    nums = (s.percentile, s.pct_q05, s.pct_q95, s.rr_mean, s.rr_q05, s.rr_q95, s.n_eff_geo, s.n_eff_feature)
    if s.conf_level not in CONFIDENCE_LEVELS or not all(_finite(v) for v in nums):
        return False
    assert s.percentile is not None and s.pct_q05 is not None and s.pct_q95 is not None
    return 0.0 <= s.pct_q05 <= s.percentile <= s.pct_q95 <= 100.0


def _from_row(row: Sequence[Any]) -> StoredScore:
    raw = dict(zip(_SCORE_COLUMNS, row))
    for name in _FLOATS:
        if raw[name] is not None:
            raw[name] = float(raw[name])
    raw["route_id"], raw["month"], raw["insufficient"] = int(raw["route_id"]), int(raw["month"]), bool(raw["insufficient"])
    return StoredScore(**{n: raw[n] for n in _FIELD_NAMES})


def _from_cache(entry: object) -> StoredScore | None:
    """A cached entry is a whole StoredScore dict; anything else is a miss, never a default."""
    if not isinstance(entry, dict) or set(entry) != set(_FIELD_NAMES):
        return None
    try:
        s = StoredScore(**entry)
    except TypeError:
        return None
    if not isinstance(s.insufficient, bool) or any(
            getattr(s, n) is not None and not _finite(getattr(s, n)) for n in _FLOATS):
        return None
    return s if servable(s) else None


async def active_version(db: AsyncSession | AsyncConnection) -> str | None:
    v = (await db.execute(ACTIVE_SQL)).scalar_one_or_none()
    return None if v is None else str(v)


async def get_score(db: AsyncSession | AsyncConnection, route_id: int, month: int) -> StoredScore | None:
    """The active version's row for (route, month), through Redis under a version-keyed key, so a
    promotion or rollback misses the cache by construction. None when there is no active version,
    no row, or a row that cannot be served."""
    version = await active_version(db)
    if version is None:
        return None
    key = build_stored_score_key(version, route_id, month)
    cached = _from_cache(cache_get(key))
    if cached is not None and cached.model_version == version:
        return cached
    row = (await db.execute(ONE_SQL, {"version": version, "route_id": route_id, "month": month})).one_or_none()
    if row is None:
        return None
    s = _from_row(row)
    if not servable(s):
        logger.error("stored score not servable: version %s route %s month %s", version, route_id, month)
        return None
    cache_set(key, asdict(s), ttl_seconds=SAFETY_SCORE_TTL)
    return s


async def get_scores(db: AsyncSession | AsyncConnection, route_ids: Sequence[int], month: int) -> dict[int, StoredScore]:
    """The active version's rows for many routes in one month; a route without a servable row is
    absent from the result (the caller shows it as unavailable)."""
    out: dict[int, StoredScore] = {}
    ids = list(dict.fromkeys(int(r) for r in route_ids))
    for start in range(0, len(ids), BATCH_IDS):
        rows = (await db.execute(MANY_SQL, {"ids": ids[start:start + BATCH_IDS], "month": month})).all()
        for row in rows:
            s = _from_row(row)
            if servable(s):
                out[s.route_id] = s
            else:
                logger.error("stored score not servable: version %s route %s month %s", s.model_version,
                             s.route_id, month)
    return out


async def get_profile(db: AsyncSession | AsyncConnection, route_id: int) -> tuple[str | None, dict[int, StoredScore]]:
    """The active version and its servable rows for every month of one route."""
    version = await active_version(db)
    if version is None:
        return None, {}
    out: dict[int, StoredScore] = {}
    for row in (await db.execute(PROFILE_SQL, {"version": version, "route_id": route_id})).all():
        s = _from_row(row)
        if servable(s):
            out[s.month] = s
        else:
            logger.error("stored score not servable: version %s route %s month %s", version, route_id, s.month)
    return version, out


_bundle_cache: dict[str, ModelBundle] = {}


async def get_bundle(db: AsyncSession | AsyncConnection, version: str) -> ModelBundle:
    """The version's bundle (for /risk-breakdown's explain), loaded and sha-checked once per
    process: a version's bundle never changes, so a promotion is the only thing that loads a new one."""
    bundle = _bundle_cache.get(version)
    if bundle is None:
        # load_bundle only calls execute(), which an AsyncSession has with the same signature.
        bundle = await load_bundle(cast(AsyncConnection, db), version)
        _bundle_cache.clear()
        _bundle_cache[version] = bundle
    return bundle


def to_risk(s: StoredScore) -> tuple[float | None, SafetyColorCode, DataStatus]:
    """The displayed (score, colour, status). The kernel-era MIN_ESTIMABLE_SCORE floor does not
    apply: a percentile of 0.0 is a real rank, and only an insufficient row lacks a number."""
    if s.insufficient or s.percentile is None:
        return (None, INSUFFICIENT_DATA_COLOR, "insufficient_data")
    shown = round(s.percentile, 1)
    return (shown, color_code_for(shown), "ok")


def _interval(mean: float | None, lo: float | None, hi: float | None, digits: int) -> dict[str, float] | None:
    if mean is None or lo is None or hi is None:
        return None
    return {"mean": round(mean, digits), "lo": round(lo, digits), "hi": round(hi, digits)}


def _sig(x: float, digits: int = 4) -> float:
    # Significant digits, not decimals: a national rate can be ~1e-4, and fixed decimals would
    # round a real rate to 0.
    return float(f"{x:.{digits}g}")


def _sig_interval(mean: float | None, lo: float | None, hi: float | None) -> dict[str, float] | None:
    if mean is None or lo is None or hi is None:
        return None
    return {"mean": _sig(mean), "lo": _sig(lo), "hi": _sig(hi)}


def score_fields(s: StoredScore) -> dict[str, Any]:
    """Every field the safety schemas take from a stored row (the existing three plus the additive
    stored-score fields). Rounding is monotonic, so the range still brackets the shown score.
    relative_rate is the stored RR as it is: posterior.summarize already divides by the national
    reference rate r_ref(g, m), so it is the "x the national rate" multiplier (DP13)."""
    risk_score, color_code, data_status = to_risk(s)
    ok = data_status == "ok"
    out: dict[str, Any] = {
        "risk_score": risk_score,
        "color_code": color_code,
        "data_status": data_status,
        "model_version": s.model_version,
        "confidence": s.conf_level if ok else None,
        "score_range": (round(s.pct_q05, 1), round(s.pct_q95, 1)) if ok and s.pct_q05 is not None
        and s.pct_q95 is not None else None,
        "evidence": {"geo": round(s.n_eff_geo, 2), "similar": round(s.n_eff_feature, 2)}
        if ok and s.n_eff_geo is not None and s.n_eff_feature is not None else None,
        "insufficient_reason": None if ok else s.insufficient_reason,
        "relative_rate": _sig_interval(s.rr_mean, s.rr_q05, s.rr_q95) if ok else None,
        "per_10k_logged_ascents": _interval(s.per10k_mean, s.per10k_q05, s.per10k_q95, 3) if ok else None,
        "per_10k_reason": s.per10k_reason if ok else None,
    }
    return out


def risk_only(s: StoredScore | None) -> Mapping[str, Any]:
    """The three legacy fields for surfaces that only ever carried them (forecast days)."""
    if s is None:
        return {"risk_score": None, "color_code": None, "data_status": None}
    risk_score, color_code, data_status = to_risk(s)
    return {"risk_score": risk_score, "color_code": color_code, "data_status": data_status}
