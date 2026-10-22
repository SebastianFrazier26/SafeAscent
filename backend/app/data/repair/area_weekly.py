"""R7: the audit found exactly one area_weekly_weather point at longitude +113.2 (a
dropped minus sign). Anything else positive is unexplained and stops the fix. The table is
dropped at 2b-3 (plan 7).

G13: longitude is numeric(6,2), so a Python float bound for an equality WHERE can miss a row
a count just found (binary-float 113.2 is not the stored decimal 113.2). The guard and the
update both stay inside SQL comparisons (`> 0`, `-longitude`) rather than round-tripping the
value through a Python float, and the update's rowcount must equal what the guard counted.
"""

from __future__ import annotations

import argparse
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

R7_VERSION = "r7-v1"
EXPECTED = Decimal("113.2")


async def run_r7(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    positive = (
        await conn.execute(text("SELECT longitude FROM area_weekly_weather WHERE longitude > 0"))
    ).scalars().all()
    if not positive:
        return {"updated": 0, "note": "no positive longitudes"}
    if len(positive) != 1 or positive[0] != EXPECTED:
        raise SystemExit(f"expected exactly one positive longitude ({EXPECTED}), found {sorted(positive)}")
    if not args.apply:
        return {"mode": "dry_run", "would_update_longitude": float(EXPECTED)}
    updated = (
        await conn.execute(text("UPDATE area_weekly_weather SET longitude = -longitude WHERE longitude > 0"))
    ).rowcount
    # The guard above counted exactly one qualifying row; anything else means a concurrent
    # write slipped in between the count and the update, and the transaction must not commit it.
    if updated != 1:
        raise SystemExit(f"expected to update exactly 1 row, updated {updated}")
    report = ValidationReport("repair:r7")
    report.accept()
    run_id = await start_run(conn, source="repair:r7", window_start=None, window_end=None, content_sha256=None)
    await finish_run(conn, run_id, status="ok", report=report, rows_upserted=updated, rule_version=R7_VERSION,
                     problems=[f"longitude {EXPECTED} -> {-EXPECTED}"])
    return {"updated": updated}
