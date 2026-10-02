"""R6 acceptance: ERA5 cell temperatures vs the legacy weather rows that were linked
correctly (dated inside their accident's 7-day window and within ~5 km of it)."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.grid import grid_bucket_sql
from app.pipelines.ingest_log import finish_run, start_run
from app.pipelines.validate import ValidationReport

GRID = grid_bucket_sql("a.latitude", "a.longitude")
R6_SQL = f"""
WITH linked AS (
  SELECT w.temperature_max AS legacy_tmax, c.tmax AS new_tmax
  FROM weather w
  JOIN accidents a ON a.accident_id = w.accident_id
  JOIN cell_daily_conditions c ON c.grid_bucket = {GRID} AND c.tz = a.tz AND c.date = w.date
  WHERE w.date BETWEEN a.date - 6 AND a.date
    AND abs(w.latitude - a.latitude) < 0.05 AND abs(w.longitude - a.longitude) < 0.05
    AND w.temperature_max IS NOT NULL AND c.tmax IS NOT NULL AND c.record_kind = 'era5'
)
SELECT (SELECT count(*) FROM linked) AS n,
       (SELECT corr(legacy_tmax, new_tmax) FROM linked) AS r_tmax,
       (SELECT percentile_cont(0.99) WITHIN GROUP (ORDER BY gust_max_ms) FROM cell_daily_conditions
         WHERE gust_max_ms IS NOT NULL AND record_kind = 'era5') AS p99_gust
"""


async def verify(conn: AsyncConnection) -> dict[str, float | int | None]:
    row = (await conn.execute(text(R6_SQL))).one()
    return {
        "n": int(row.n),
        "r_tmax": float(row.r_tmax) if row.r_tmax is not None else None,
        "p99_gust": float(row.p99_gust) if row.p99_gust is not None else None,
    }


async def _main() -> dict[str, float | int | None]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        async with engine.begin() as conn:
            result = await verify(conn)
            run_id = await start_run(conn, source="r6_verify", window_start=None, window_end=None, content_sha256=None)
            await finish_run(conn, run_id, status="ok", report=ValidationReport("r6_verify"), rows_upserted=0,
                             problems=[json.dumps(result)])
    finally:
        await engine.dispose()
    return result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_main()), sort_keys=True))
