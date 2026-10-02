"""Local calendar days (owner decision 2026-09-28: a weather day is the crag's local day).

Every conditions series is keyed by (grid_bucket, IANA timezone) and requested with that
timezone, so a 19:30 thunderstorm lands on the day the accident report gives, not on the
next UTC day. Points are registered here; each new bucket's normals start 'pending' and are
filled in the next Professional window (missing until then, never 0).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterable
from datetime import date, datetime
from functools import lru_cache
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.grid import grid_bucket
from app.pipelines.ingest_log import finish_run, start_run, write_quarantine
from app.pipelines.validate import ValidationReport, in_us

if TYPE_CHECKING:
    from timezonefinder import TimezoneFinder

SOURCE = "series_register"
# Owner decision OQ2 (2026-10-01): incident ERA5 starts 1990; earlier incidents are reported
# conditions_missing, so a point whose only incidents predate this buys no incident series.
# era5_fill's incident keys must use the same date.
INCIDENT_START = date(1990, 1, 1)

# accidents.tz is the zone the incident series was registered under (the view joins on it), so
# only incidents that got a zone register one. A year range (R1) is not a known day: no window.
INCIDENT_POINTS_SQL = (
    "SELECT DISTINCT a.latitude, a.longitude FROM accidents_clean_daily a "
    "JOIN accidents t ON t.accident_id = a.accident_id "
    "WHERE a.point_trusted AND a.latitude IS NOT NULL AND a.longitude IS NOT NULL AND t.tz IS NOT NULL "
    "AND a.year_lo IS NOT DISTINCT FROM a.year_hi "
    f"AND a.date >= DATE '{INCIDENT_START.isoformat()}'"
)
# Every route point (owner OQ4), falling back to its area's point. Only mp_locations' mp_id,
# latitude and longitude: ingest's grant on that table is exactly those columns.
ROUTE_POINTS_SQL = (
    "SELECT DISTINCT COALESCE(r.latitude, l.latitude), COALESCE(r.longitude, l.longitude) "
    "FROM mp_routes r LEFT JOIN mp_locations l ON l.mp_id = r.location_id "
    "WHERE COALESCE(r.latitude, l.latitude) IS NOT NULL AND COALESCE(r.longitude, l.longitude) IS NOT NULL"
)
_TRUSTED_POINTS_SQL = (
    "SELECT c.accident_id, c.latitude, c.longitude, t.tz FROM accidents_clean c "
    "JOIN accidents t ON t.accident_id = c.accident_id "
    "WHERE c.point_trusted AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL"
)


@lru_cache(maxsize=1)
def _finder() -> TimezoneFinder:
    # Imported here: the API image is built without the pipelines group.
    from timezonefinder import TimezoneFinder

    return TimezoneFinder()


@lru_cache(maxsize=1)
def _known_zones() -> frozenset[str]:
    return frozenset(available_timezones())


def _zone(lat: float, lon: float) -> tuple[str | None, str | None]:
    """(zone, None) or (None, quarantine reason)."""
    name = _finder().timezone_at_land(lng=lon, lat=lat)
    # timezone_at_land already drops the Etc/GMT±N ocean zones; any other Etc/* zone has no
    # civil calendar day either.
    if name is None or name.startswith("Etc/"):
        return None, "no_timezone"
    # The DB only checks tz <> '': a name the tz database cannot resolve (finder data newer than
    # tzdata, say) would make Open-Meteo requests and local_date fail later, so it stops here.
    if name not in _known_zones():
        return None, "unknown_timezone"
    return str(name), None


def tz_for_point(lat: float, lon: float) -> str | None:
    return _zone(lat, lon)[0]


def local_date(ts_utc: datetime, tz: str) -> date:
    if ts_utc.tzinfo is None:
        raise ValueError("local_date needs a timezone-aware UTC timestamp")
    return ts_utc.astimezone(ZoneInfo(tz)).date()


async def register_series(
    conn: AsyncConnection, points: Iterable[tuple[float, float]], *, report: ValidationReport
) -> set[tuple[int, str]]:
    series: set[tuple[int, str]] = set()
    for lat, lon in points:
        ref = f"{lat:.5f}:{lon:.5f}"
        if not in_us(lat, lon):
            report.quarantine(ref, "outside_us")
            continue
        tz, problem = _zone(lat, lon)
        if tz is None:
            report.quarantine(ref, problem or "no_timezone")
            continue
        report.accept()
        series.add((grid_bucket(lat, lon), tz))
    if series:
        await conn.execute(
            text("INSERT INTO grid_bucket_series (grid_bucket, tz) VALUES (:b, :tz) ON CONFLICT DO NOTHING"),
            [{"b": b, "tz": tz} for b, tz in sorted(series)],
        )
        await conn.execute(
            text("INSERT INTO cell_normals_status (grid_bucket, status) VALUES (:b, 'pending') ON CONFLICT DO NOTHING"),
            [{"b": b} for b in sorted({b for b, _ in series})],
        )
    return series


async def assign_accident_tz(conn: AsyncConnection, *, report: ValidationReport) -> int:
    """Recompute every clean trusted point's zone and clear it everywhere else (D2: a reviewed
    point may have moved across a zone, or stopped being trusted). Returns rows changed."""
    rows = (await conn.execute(text(_TRUSTED_POINTS_SQL))).all()
    updates: list[dict[str, object]] = []
    for accident_id, lat, lon, current in rows:
        ref = f"accident:{accident_id}"
        zone: str | None = None
        if not in_us(float(lat), float(lon)):
            report.quarantine(ref, "outside_us")
        else:
            zone, problem = _zone(float(lat), float(lon))
            if zone is None:
                report.quarantine(ref, problem or "no_timezone")
            else:
                report.accept()
        if zone != current:
            updates.append({"id": accident_id, "tz": zone})
    if updates:
        await conn.execute(
            text("UPDATE accidents SET tz = :tz WHERE accident_id = :id AND tz IS DISTINCT FROM :tz"), updates
        )
    cleared = await conn.execute(
        text(
            "UPDATE accidents t SET tz = NULL WHERE t.tz IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM accidents_clean c WHERE c.accident_id = t.accident_id AND c.point_trusted "
            "AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL)"
        )
    )
    return len(updates) + cleared.rowcount


async def register_all(conn: AsyncConnection, *, report: ValidationReport) -> dict[str, int]:
    changed = await assign_accident_tz(conn, report=report)
    assigned = (await conn.execute(text("SELECT count(*) FROM accidents WHERE tz IS NOT NULL"))).scalar_one()
    incident = [(float(a), float(b)) for a, b in (await conn.execute(text(INCIDENT_POINTS_SQL))).all()]
    routes = [(float(a), float(b)) for a, b in (await conn.execute(text(ROUTE_POINTS_SQL))).all()]
    incident_series = await register_series(conn, incident, report=report)
    route_series = await register_series(conn, routes, report=report)
    return {
        "accident_tz_changed": changed,
        "accident_tz_assigned": int(assigned),
        "incident_series": len(incident_series),
        "route_series": len(route_series),
        "series_total": len(incident_series | route_series),
    }


async def register_logged(engine: AsyncEngine) -> dict[str, object]:
    """register_all plus its ingest-log run and quarantine rows, in one transaction (D12: the
    localday and era5_window CLIs share this)."""
    report = ValidationReport(SOURCE)
    async with engine.begin() as conn:
        counts = await register_all(conn, report=report)
        run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=None)
        await write_quarantine(conn, run_id, report)
        await finish_run(conn, run_id, status="ok", report=report, rows_upserted=counts["series_total"])
    return {**counts, "report": report.summary()}


async def _main() -> dict[str, object]:
    from app.pipelines.db import ingest_engine

    engine = ingest_engine()
    try:
        return await register_logged(engine)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["register"])
    parser.parse_args()
    print(json.dumps(asyncio.run(_main()), sort_keys=True, default=str))
