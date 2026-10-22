"""model.registry state machine (P3 M9). Every transition is one transaction and one statement
set; the partial unique indexes make a second active or previous row impossible."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ml.bundle import ModelBundle, sha256


def _dumps(value: Any) -> str:
    return json.dumps(value, allow_nan=False)


class PromotionRefused(RuntimeError):
    pass


class BundleMissing(RuntimeError):
    pass


class BundleCorrupt(RuntimeError):
    pass


class RollbackRefused(RuntimeError):
    pass


def _require_transaction(conn: AsyncConnection) -> None:
    # A transition is several statements; outside one transaction a failure halfway (say, the old previous
    # retired but the new version never activated) would commit, so the caller must own the transaction.
    if not conn.in_transaction():
        raise RuntimeError("registry transitions must run inside a transaction (engine.begin())")


@dataclass(frozen=True)
class ActiveVersion:
    version: str
    family: str
    bundle_sha256: str | None


@dataclass(frozen=True)
class PruneReport:
    scores_deleted: int
    bundles_nulled: int


async def insert_candidate(conn: AsyncConnection, *, version: str, family: str, data_hash: str, train_start: date,
                           data_cutoff: date, exposure_as_of: date | None, metrics: dict[str, Any],
                           thresholds: dict[str, Any] | None, model_card: dict[str, Any], bundle: bytes | None,
                           parent_version: str | None) -> None:
    _require_transaction(conn)
    await conn.execute(text(
        "INSERT INTO model.registry (version, family, data_hash, train_start, data_cutoff, exposure_as_of, metrics, "
        "thresholds, model_card, bundle, bundle_sha256, bundle_bytes, status, parent_version) VALUES "
        "(:version, :family, :data_hash, :train_start, :data_cutoff, :exposure_as_of, CAST(:metrics AS jsonb), "
        "CAST(:thresholds AS jsonb), CAST(:model_card AS jsonb), :bundle, :sha, :nbytes, 'candidate', :parent)"),
        {"version": version, "family": family, "data_hash": data_hash, "train_start": train_start,
         "data_cutoff": data_cutoff, "exposure_as_of": exposure_as_of, "metrics": _dumps(metrics),
         "thresholds": None if thresholds is None else _dumps(thresholds), "model_card": _dumps(model_card),
         "bundle": bundle, "sha": None if bundle is None else sha256(bundle),
         "nbytes": None if bundle is None else len(bundle), "parent": parent_version})


async def set_gate(conn: AsyncConnection, version: str, gate_result: dict[str, Any], diff_report: dict[str, Any],
                   scores_rows: int) -> None:
    _require_transaction(conn)
    res = await conn.execute(text(
        "UPDATE model.registry SET gate_result = CAST(:g AS jsonb), diff_report = CAST(:d AS jsonb), "
        "scores_rows = :n WHERE version = :v AND status = 'candidate'"),
        {"g": _dumps(gate_result), "d": _dumps(diff_report), "n": scores_rows, "v": version})
    if res.rowcount != 1:  # pre-flight F14: never a silent no-op
        raise PromotionRefused(f"{version} is not a candidate")


async def reject(conn: AsyncConnection, version: str) -> None:
    _require_transaction(conn)
    res = await conn.execute(text(
        "UPDATE model.registry SET status = 'rejected' WHERE version = :v AND status = 'candidate'"), {"v": version})
    if res.rowcount != 1:  # pre-flight F14
        raise PromotionRefused(f"{version} is not a candidate")


async def promote(conn: AsyncConnection, version: str, *, by: Literal["auto", "owner"],
                  override_rejection: bool = False) -> str | None:
    """override_rejection lets the owner promote a version the gate or the owner rejected; nothing
    else may (a rejection is a decision, so undoing it must be explicit)."""
    _require_transaction(conn)
    row = (await conn.execute(text(
        "SELECT status, family, gate_result ->> 'passed', scores_rows, bundle IS NOT NULL, "
        "EXISTS (SELECT 1 FROM model.route_scores s WHERE s.model_version = r.version) "
        "FROM model.registry r WHERE version = :v FOR UPDATE"), {"v": version})).one_or_none()
    if row is None:
        raise PromotionRefused(f"no such version {version}")
    status, family, passed, rows, has_bundle, has_scores = row
    if family == "kernel":
        raise PromotionRefused("the kernel family is never re-promoted (DP15)")
    if status == "active":
        raise PromotionRefused(f"{version} is already active")
    if status == "rejected" and not (by == "owner" and override_rejection):
        raise PromotionRefused(f"{version} was rejected; only the owner with override_rejection may promote it")
    if by == "auto" and (status != "candidate" or passed != "true"):
        raise PromotionRefused(f"{version} has not passed the gate (status {status})")
    if not has_bundle:  # pre-flight F13: a pruned version is not servable
        raise PromotionRefused(f"{version} has no bundle (pruned or never stored)")
    if not (rows and has_scores):
        raise PromotionRefused(f"{version} has no materialized scores")
    await conn.execute(text("UPDATE model.registry SET status = 'retired' WHERE status = 'previous'"))
    old = (await conn.execute(text(
        "UPDATE model.registry SET status = CASE WHEN family = 'kernel' THEN 'retired' ELSE 'previous' END "
        "WHERE status = 'active' RETURNING version, family"))).one_or_none()
    await conn.execute(text(
        "UPDATE model.registry SET status = 'active', promoted_at = now(), promoted_by = :by WHERE version = :v"),
        {"by": by, "v": version})
    return None if old is None or old[1] == "kernel" else str(old[0])


async def rollback(conn: AsyncConnection) -> tuple[str, str]:
    _require_transaction(conn)
    prev = (await conn.execute(text(
        "SELECT version FROM model.registry WHERE status = 'previous' AND family <> 'kernel' "
        "AND bundle IS NOT NULL FOR UPDATE"))).scalar_one_or_none()
    if prev is None:
        raise RollbackRefused("no previous version to restore; promote a specific version with TRAIN rollback --version")
    cur = (await conn.execute(text(
        "UPDATE model.registry SET status = 'retired' WHERE status = 'active' RETURNING version"))).scalar_one_or_none()
    if cur is None:  # pre-flight F15
        raise RollbackRefused("no active version")
    await conn.execute(text(
        "UPDATE model.registry SET status = 'active', promoted_at = now(), promoted_by = 'owner' WHERE version = :v"),
        {"v": prev})
    return str(cur), str(prev)


async def active(conn: AsyncConnection) -> ActiveVersion | None:
    row = (await conn.execute(text(
        "SELECT version, family, bundle_sha256 FROM model.registry WHERE status = 'active'"))).one_or_none()
    return None if row is None else ActiveVersion(str(row[0]), str(row[1]), row[2])


async def load_bundle(conn: AsyncConnection, version: str) -> ModelBundle:
    row = (await conn.execute(text("SELECT bundle, bundle_sha256 FROM model.registry WHERE version = :v"),
                              {"v": version})).one()
    if row[0] is None:  # pre-flight F13: pruned bundle
        raise BundleMissing(f"bundle of {version} was pruned")
    raw, digest = bytes(row[0]), row[1]
    if sha256(raw) != digest:
        raise BundleCorrupt(f"bundle of {version} does not match its sha256")
    bundle = ModelBundle.from_bytes(raw)
    if bundle.version != version:
        raise BundleCorrupt(f"bundle stored under {version} declares version {bundle.version}")
    return bundle


async def prune(conn: AsyncConnection, *, today: date, keep_days: int = 90) -> PruneReport:
    """M3 retention: scores and bundle bytes of rejected/retired/stale candidate versions older
    than keep_days go; metadata rows stay. Active and previous are never touched."""
    def stale(t: str) -> str:
        return (f"{t}created_at < CAST(:today AS date) - make_interval(days => :keep) "
                f"AND {t}status IN ('rejected', 'retired', 'candidate')")

    _require_transaction(conn)
    params = {"today": today, "keep": keep_days}
    deleted = (await conn.execute(text(
        "DELETE FROM model.route_scores s USING model.registry r WHERE s.model_version = r.version "
        f"AND {stale('r.')}"), params)).rowcount
    nulled = (await conn.execute(text(
        f"UPDATE model.registry SET bundle = NULL, bundle_sha256 = NULL, bundle_bytes = NULL "
        f"WHERE bundle IS NOT NULL AND {stale('')}"), params)).rowcount
    await conn.execute(text(f"UPDATE model.registry SET scores_rows = 0 WHERE scores_rows > 0 AND {stale('')}"),
                       params)
    return PruneReport(int(deleted or 0), int(nulled or 0))
