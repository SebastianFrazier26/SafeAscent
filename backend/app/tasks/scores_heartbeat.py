"""Daily heartbeat of the stored-score model (P3 Task 23). Serving reads model.route_scores and
nothing recomputes at night, so what can silently rot is the data: no active version, a promotion
that left the current month without rows, or a bundle whose bytes no longer match their digest.
Each is a /fail on healthchecks.io with the reason in the task result."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.celery_app import celery_app
from app.config import settings
from app.db.ssl import connect_args_for
from app.healthchecks import ping
from app.utils.time_utils import utc_today

logger = logging.getLogger(__name__)

# sha256 runs in Postgres so the bundle bytes never cross the wire.
ACTIVE_SQL = text(
    "SELECT version, bundle IS NOT NULL AS present, "
    "COALESCE(encode(sha256(bundle), 'hex') = bundle_sha256, false) AS sha_ok "
    "FROM model.registry WHERE status = 'active' ORDER BY version")
MISSING_SQL = text(
    "SELECT count(*) FROM scorable_routes r WHERE NOT EXISTS ("
    "SELECT 1 FROM model.route_scores s WHERE s.model_version = :version AND s.route_id = r.route_id "
    "AND s.month = :month)")


@dataclass(frozen=True)
class ActiveRow:
    version: str
    bundle_present: bool
    sha_ok: bool


@dataclass(frozen=True)
class Facts:
    active: list[ActiveRow]
    missing_routes: int


async def _load_facts(month: int) -> Facts:
    # A fresh engine per run: each Celery run has its own event loop, and pooled connections
    # from an earlier loop are unusable in the next.
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool,
                                 connect_args=connect_args_for(settings.DATABASE_URL))
    try:
        async with engine.connect() as conn:
            active = [ActiveRow(str(v), bool(p), bool(ok)) for v, p, ok in (await conn.execute(ACTIVE_SQL)).all()]
            missing = 0
            if len(active) == 1:
                missing = int((await conn.execute(MISSING_SQL, {"version": active[0].version,
                                                                "month": month})).scalar_one())
        return Facts(active=active, missing_routes=missing)
    finally:
        await engine.dispose()


def _verdict(facts: Facts) -> dict[str, Any]:
    if not facts.active:
        return {"ok": False, "reason": "no_active_version"}
    if len(facts.active) > 1:
        return {"ok": False, "reason": "multiple_active_versions"}
    row = facts.active[0]
    if not row.bundle_present:
        return {"ok": False, "reason": "bundle_missing", "version": row.version}
    if not row.sha_ok:
        return {"ok": False, "reason": "bundle_sha_mismatch", "version": row.version}
    if facts.missing_routes:
        return {"ok": False, "reason": "missing_scores", "version": row.version, "missing": facts.missing_routes}
    return {"ok": True, "version": row.version}


def _ping_both(suffix: Any) -> None:
    # The nightly check predates this task and still has a dead-man alert configured; the heartbeat
    # keeps it meaningful instead of letting it page for a job that no longer exists.
    ping(settings.HEALTHCHECKS_SCORES_URL, suffix)
    ping(settings.HEALTHCHECKS_NIGHTLY_URL, suffix)


def run() -> dict[str, Any]:
    month = utc_today().month
    try:
        facts = asyncio.run(_load_facts(month))
    except Exception as exc:
        logger.error("scores heartbeat could not read the registry: %s", type(exc).__name__)
        _ping_both("/fail")
        raise
    result = _verdict(facts)
    if result["ok"]:
        result["month"] = month
        _ping_both("")
    else:
        logger.error("scores heartbeat failed: %s", result)
        _ping_both("/fail")
    return result


@celery_app.task(name="app.tasks.scores_heartbeat.scores_heartbeat")
def scores_heartbeat() -> dict[str, Any]:
    return run()
