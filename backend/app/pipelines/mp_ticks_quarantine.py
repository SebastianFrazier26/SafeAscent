"""R8: flag garbage mp_ticks rows instead of deleting them.

"future" is relative to the day the row was captured (created_at) and to today at run
time, whichever is earlier: a tick dated after its own capture day cannot be real, and
stays flagged even once the calendar passes it.

created_at is `timestamp without time zone DEFAULT CURRENT_TIMESTAMP`, so it holds the
writer session's local time, which no later session can prove (SHOW TimeZone only shows the
reader's). US zones are at most 10 h behind UTC, so one day of slack on the capture day keeps
a real tick from being flagged whatever zone the writer used; "after today" stays strict.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

RULE_VERSION = "r8-v1"
EPOCH = date(1970, 1, 1)
CAPTURE_SLACK = timedelta(days=1)


def classify_tick(tick_date: date | None, captured_on: date | None, route_known: bool, today: date) -> str | None:
    cutoff = min(today, captured_on + CAPTURE_SLACK) if captured_on is not None else today
    if tick_date is not None and tick_date > cutoff:
        return "future"
    if not route_known:
        return "orphan_route"
    if tick_date is not None and tick_date < EPOCH:
        return "pre_1970"
    return None


# Mirrors classify_tick; test_sql_matches_the_python_rule_and_is_idempotent pins them together.
QUARANTINE_SQL = """
WITH classified AS (
  SELECT t.tick_id,
         CASE
           WHEN t.tick_date > LEAST(CAST(:today AS date), t.created_at::date + 1) THEN 'future'
           -- Nested CASE, not OR: Postgres does not promise to short-circuit OR, and
           -- 'abc'::bigint would abort the whole statement.
           WHEN CASE WHEN t.route_id ~ '^[0-9]{1,18}$'
                     THEN NOT EXISTS (SELECT 1 FROM mp_routes r WHERE r.mp_route_id = t.route_id::bigint)
                     ELSE true END THEN 'orphan_route'
           WHEN t.tick_date < DATE '1970-01-01' THEN 'pre_1970'
         END AS reason
  FROM mp_ticks t
)
UPDATE mp_ticks m
SET quarantine_reason = c.reason, quarantine_rule_version = :rule_version
FROM classified c
WHERE m.tick_id = c.tick_id
  AND (m.quarantine_reason IS DISTINCT FROM c.reason OR m.quarantine_rule_version IS DISTINCT FROM :rule_version)
"""

COUNTS_SQL = "SELECT coalesce(quarantine_reason, 'clean'), count(*) FROM mp_ticks GROUP BY 1"


async def run(conn: AsyncConnection, *, today: date) -> dict[str, int]:
    run_id = await start_run(conn, source="mp_ticks_quarantine", window_start=None, window_end=today, content_sha256=None)
    changed = (await conn.execute(text(QUARANTINE_SQL), {"today": today, "rule_version": RULE_VERSION})).rowcount
    counts = {str(reason): int(n) for reason, n in (await conn.execute(text(COUNTS_SQL))).all()}
    report = ValidationReport("mp_ticks_quarantine")
    report.rows_in = sum(counts.values())
    report.accepted = counts.get("clean", 0)
    for reason, n in counts.items():
        if reason != "clean":
            report.quarantined[reason] = n
    await finish_run(conn, run_id, status="ok", report=report, rows_upserted=changed)
    return counts


async def _main() -> None:
    from app.pipelines.db import ingest_engine
    from app.services.temporal_weighting import utc_today

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            counts = await run(conn, today=utc_today())
    finally:
        await engine.dispose()
    print(json.dumps({"rule_version": RULE_VERSION, "counts": counts}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
