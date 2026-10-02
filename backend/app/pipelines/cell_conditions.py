"""Validated writer for cell_daily_conditions: one Open-Meteo location response becomes local-day
rows for one (grid_bucket, tz) series.

Missing is never zero: a None value stays NULL, and freeze_thaw is NULL unless both temperatures
are present. Precedence era5 > stopgap > forecast lives in the upsert's WHERE, so no caller can
overwrite an ERA5 day with a stopgap or forecast one, whatever order runs land in.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.open_meteo import LocationResponse
from app.pipelines.validate import ValidationReport, range_problem

RecordKind = Literal["era5", "stopgap", "forecast"]
Origin = Literal["archive", "forecast_api"]

RANGES: dict[str, tuple[float, float]] = {
    "tmax": (-60.0, 55.0),
    "tmin": (-70.0, 50.0),
    "precip_mm": (0.0, 500.0),
    "snowfall_cm": (0.0, 300.0),
    "wind_max_ms": (0.0, 75.0),
    "gust_max_ms": (0.0, 75.0),
}


@dataclass(frozen=True)
class DayRow:
    grid_bucket: int
    tz: str
    date: date
    tmax: float | None
    tmin: float | None
    precip_mm: float | None
    snowfall_cm: float | None
    wind_max_ms: float | None
    gust_max_ms: float | None
    freeze_thaw: bool | None
    record_kind: RecordKind
    model: str
    source: str


def _kind(origin: Origin, day: date, today: date) -> RecordKind:
    if origin == "archive":
        return "era5"
    return "stopgap" if day < today else "forecast"


def day_rows(
    grid_bucket: int,
    tz: str,
    response: LocationResponse,
    *,
    today: date,
    origin: Origin,
    model: str,
    source: str,
    report: ValidationReport,
) -> list[DayRow]:
    d = response.daily
    # A missing zone is quarantined too: without it the day boundaries cannot be shown to be the series'.
    if response.timezone != tz:
        for day in d.time:
            report.quarantine(f"{grid_bucket}:{tz}:{day.isoformat()}", "timezone_mismatch", got=response.timezone)
        return []
    rows: list[DayRow] = []
    for i, day in enumerate(d.time):
        ref = f"{grid_bucket}:{tz}:{day.isoformat()}"
        if origin == "archive" and day > today:
            report.quarantine(ref, "future")
            continue
        values = {
            "tmax": d.temperature_2m_max[i],
            "tmin": d.temperature_2m_min[i],
            "precip_mm": d.precipitation_sum[i],
            "snowfall_cm": d.snowfall_sum[i],
            "wind_max_ms": d.wind_speed_10m_max[i],
            "gust_max_ms": d.wind_gusts_10m_max[i],
        }
        problem = next(
            ((field, p) for field, v in values.items() if (p := range_problem(v, *RANGES[field])) is not None), None
        )
        if problem is not None:
            field, reason = problem
            report.quarantine(ref, reason, field=field, value=values[field])
            continue
        tmax, tmin = values["tmax"], values["tmin"]
        if tmax is not None and tmin is not None and tmax < tmin:
            report.quarantine(ref, "tmax_below_tmin", tmax=tmax, tmin=tmin)
            continue
        freeze_thaw = (tmax > 0 and tmin < 0) if tmax is not None and tmin is not None else None
        report.accept()
        rows.append(
            DayRow(
                grid_bucket, tz, day, tmax, tmin, values["precip_mm"], values["snowfall_cm"], values["wind_max_ms"],
                values["gust_max_ms"], freeze_thaw, _kind(origin, day, today), model, source,
            )
        )
    return rows


_COLUMNS = (
    "grid_bucket", "tz", "date", "tmax", "tmin", "precip_mm", "snowfall_cm", "wind_max_ms", "gust_max_ms",
    "freeze_thaw", "record_kind", "is_forecast", "model", "source",
)
# 14 bind params per row plus the shared run_id: 1000 rows stay under half of asyncpg's 32767-argument
# limit. Multi-row VALUES rather than executemany so RETURNING counts what the precedence WHERE let through.
CHUNK_ROWS = 1000

_RANK = "(CASE {t}.record_kind WHEN 'era5' THEN 3 WHEN 'stopgap' THEN 2 ELSE 1 END)"
_VALUE_COLUMNS = tuple(c for c in _COLUMNS if c not in ("grid_bucket", "tz", "date"))
_UPDATE_SET = ", ".join(f"{c} = EXCLUDED.{c}" for c in _VALUE_COLUMNS)
_STORED, _NEW = _RANK.format(t="cell_daily_conditions"), _RANK.format(t="EXCLUDED")
_CHANGED = (
    f"({', '.join(f'cell_daily_conditions.{c}' for c in _VALUE_COLUMNS)}) IS DISTINCT FROM "
    f"({', '.join(f'EXCLUDED.{c}' for c in _VALUE_COLUMNS)})"
)
# Only the weather columns: plan 7's AQI, alerts, SWE and snow depth on the same row survive a replacement.
# Equal ranks replace only when a value differs, so a newer forecast supersedes an older one while
# an identical re-fetch leaves the row (and its fetched_at/run_id) alone and is not counted.
_UPSERT_TAIL = (
    "ON CONFLICT (grid_bucket, tz, date) DO UPDATE SET "
    f"{_UPDATE_SET}, fetched_at = now(), run_id = EXCLUDED.run_id "
    f"WHERE {_STORED} < {_NEW} OR ({_STORED} = {_NEW} AND {_CHANGED}) "
    "RETURNING 1"
)


async def upsert_rows(conn: AsyncConnection, rows: Sequence[DayRow], *, run_id: uuid.UUID) -> int:
    """Writes on the caller's connection and transaction, so a batch lands whole or not at all.
    Returns rows inserted or replaced; rows the precedence rule kept out, and identical
    equal-rank re-writes, are not counted."""
    if not rows:
        return 0
    keys = {(r.grid_bucket, r.tz, r.date) for r in rows}
    if len(keys) != len(rows):
        # Postgres rejects a second touch of one row in a single ON CONFLICT statement, and across
        # chunks the winner would silently depend on order.
        raise ValueError(f"duplicate (grid_bucket, tz, date) keys: {len(rows) - len(keys)} in {len(rows)} rows")
    written = 0
    for start in range(0, len(rows), CHUNK_ROWS):
        chunk = rows[start : start + CHUNK_ROWS]
        params: dict[str, object] = {"run_id": run_id}
        values_sql = []
        for i, r in enumerate(chunk):
            values_sql.append("(" + ", ".join(f":{c}_{i}" for c in _COLUMNS) + ", now(), :run_id)")
            row = {
                "grid_bucket": r.grid_bucket, "tz": r.tz, "date": r.date, "tmax": r.tmax, "tmin": r.tmin,
                "precip_mm": r.precip_mm, "snowfall_cm": r.snowfall_cm, "wind_max_ms": r.wind_max_ms,
                "gust_max_ms": r.gust_max_ms, "freeze_thaw": r.freeze_thaw, "record_kind": r.record_kind,
                "is_forecast": r.record_kind == "forecast", "model": r.model, "source": r.source,
            }
            params.update({f"{c}_{i}": row[c] for c in _COLUMNS})
        result = await conn.execute(
            text(
                f"INSERT INTO cell_daily_conditions ({', '.join(_COLUMNS)}, fetched_at, run_id) "
                f"VALUES {', '.join(values_sql)} {_UPSERT_TAIL}"
            ),
            params,
        )
        written += len(result.all())
    return written
