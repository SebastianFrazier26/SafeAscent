"""R7: the audit found exactly one area_weekly_weather point at longitude +113.2 (a
dropped minus sign). Anything else positive is unexplained and stops the fix. The table is
dropped at 2b-3 (plan 7).

r7-v2 (2026-11-23): the table holds one row per point per week (the legacy collector wrote 52
weeks per point), so the one flipped point is 52 rows on prod. r7-v1 counted rows and refused;
v2 guards on the distinct point (latitude 37.10, longitude 113.20), refuses if any row already
sits at the negated point (the flip would merge two series), and updates exactly the rows the
guard counted for it.

G13: latitude/longitude are numeric(6,2), so a Python float bound for an equality WHERE can miss
a row a count just found (binary-float 113.2 is not the stored decimal 113.2). The guard and the
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

R7_VERSION = "r7-v2"
EXPECTED_LAT = Decimal("37.1")
EXPECTED = Decimal("113.2")

_GUARD = text(
    "SELECT p.latitude, p.longitude, count(*) AS n, "
    "(SELECT count(*) FROM area_weekly_weather q WHERE q.latitude = p.latitude AND q.longitude = -p.longitude) AS negated "
    "FROM area_weekly_weather p WHERE p.longitude > 0 GROUP BY p.latitude, p.longitude"
)


async def run_r7(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    points = (await conn.execute(_GUARD)).all()
    if not points:
        return {"updated": 0, "note": "no positive longitudes"}
    if len(points) != 1 or (points[0][0], points[0][1]) != (EXPECTED_LAT, EXPECTED):
        found = sorted((str(lat), str(lon), int(n)) for lat, lon, n, _ in points)
        raise SystemExit(f"expected exactly one positive point ({EXPECTED_LAT}, {EXPECTED}), found {found}")
    _, _, n, negated = points[0]
    rows = int(n)
    if negated:
        raise SystemExit(f"({EXPECTED_LAT}, {-EXPECTED}) already holds rows ({int(negated)}): the flip would merge two series")
    if not args.apply:
        return {"mode": "dry_run", "latitude": float(EXPECTED_LAT), "would_update_longitude": float(EXPECTED),
                "would_update_rows": rows}
    updated = (
        await conn.execute(
            text("UPDATE area_weekly_weather SET longitude = -longitude WHERE longitude > 0 AND latitude = :lat"),
            {"lat": EXPECTED_LAT},
        )
    ).rowcount
    # The guard counted these rows in the same transaction; a different count means a concurrent
    # write slipped in between, and the transaction must not commit it.
    if updated != rows:
        raise SystemExit(f"expected to update exactly {rows} rows, updated {updated}")
    report = ValidationReport("repair:r7")
    for _ in range(updated):
        report.accept()
    run_id = await start_run(conn, source="repair:r7", window_start=None, window_end=None, content_sha256=None)
    await finish_run(conn, run_id, status="ok", report=report, rows_upserted=updated, rule_version=R7_VERSION,
                     problems=[f"latitude {EXPECTED_LAT}: longitude {EXPECTED} -> {-EXPECTED} on {updated} weekly rows"])
    return {"updated": updated}
