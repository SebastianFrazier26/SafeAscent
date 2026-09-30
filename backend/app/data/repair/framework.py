"""The one writer for accident repairs. Every changed field gets an
internal.accident_revisions row keyed (accident_id, field, rule_version); a rerun with the
same rule version finds the key and changes nothing."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.pipelines.ingest_log import finish_run, sha256_rows, start_run
from app.pipelines.validate import ValidationReport

# Identifiers interpolated into SQL come only from these sets, never from data.
REPAIRABLE_FIELDS = frozenset(
    {
        "date", "year", "country", "date_precision", "year_source", "year_lo", "year_hi",
        "geocode_precision", "geocode_method", "activity_class", "activity_rule_version",
        "inclusion_flag", "incident_group_id", "is_canonical", "severity_scale", "excluded_reason",
        "source_url", "exp_years_climbing", "exp_stated_level", "exp_first_season", "guided",
        "exp_rule_version",
    }
)
READABLE_COLUMNS = REPAIRABLE_FIELDS | {
    "source", "source_id", "state", "location", "mountain", "route", "latitude", "longitude",
    "elevation_meters", "accident_type", "activity", "injury_severity", "description", "tags", "mp_route_id",
}
LOCATION = "latlon"

Value = str | int | float | bool | date | tuple[float, float] | None


@dataclass(frozen=True)
class Change:
    accident_id: int
    field: str
    new_value: Value
    method: str


@dataclass
class ApplyResult:
    applied: int = 0
    unchanged: int = 0
    already_recorded: int = 0


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, tuple):
        return f"{float(value[0]):.6f},{float(value[1]):.6f}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


async def fetch_accidents(conn: AsyncConnection, columns: Sequence[str]) -> list[dict[str, object]]:
    unknown = set(columns) - READABLE_COLUMNS
    if unknown:
        raise ValueError(f"not readable: {sorted(unknown)}")
    cols = ", ".join(["accident_id", *columns])
    result = await conn.execute(text(f"SELECT {cols} FROM accidents ORDER BY accident_id"))
    return [dict(row._mapping) for row in result]


async def apply_changes(
    conn: AsyncConnection, changes: Sequence[Change], *, rule_version: str, run_id: uuid.UUID
) -> ApplyResult:
    for change in changes:
        if change.field != LOCATION and change.field not in REPAIRABLE_FIELDS:
            raise ValueError(f"field {change.field!r} is not repairable")
    result = ApplyResult()
    for change in changes:
        key = {"a": change.accident_id, "f": change.field, "v": rule_version}
        recorded = await conn.execute(
            text("SELECT 1 FROM internal.accident_revisions WHERE accident_id = :a AND field = :f AND rule_version = :v"),
            key,
        )
        if recorded.first() is not None:
            result.already_recorded += 1
            continue
        if change.field == LOCATION:
            row = (
                await conn.execute(
                    text("SELECT latitude, longitude FROM accidents WHERE accident_id = :a FOR UPDATE"),
                    {"a": change.accident_id},
                )
            ).one()
            old: object = None if row.latitude is None or row.longitude is None else (row.latitude, row.longitude)
            if old == change.new_value:
                result.unchanged += 1
                continue
            lat, lon = change.new_value if isinstance(change.new_value, tuple) else (None, None)
            # The coords trigger only fills `coordinates` when both are non-null, so a
            # nulled location must clear it here or the old point would survive.
            await conn.execute(
                text(
                    "UPDATE accidents SET latitude = :lat, longitude = :lon, updated_at = now(), "
                    "coordinates = CASE WHEN CAST(:lat AS double precision) IS NULL THEN NULL ELSE coordinates END "
                    "WHERE accident_id = :a"
                ),
                {"lat": lat, "lon": lon, "a": change.accident_id},
            )
        else:
            old = (
                await conn.execute(
                    text(f"SELECT {change.field} FROM accidents WHERE accident_id = :a FOR UPDATE"),
                    {"a": change.accident_id},
                )
            ).scalar_one()
            if old == change.new_value:
                result.unchanged += 1
                continue
            await conn.execute(
                text(f"UPDATE accidents SET {change.field} = :v, updated_at = now() WHERE accident_id = :a"),
                {"v": change.new_value, "a": change.accident_id},
            )
        await conn.execute(
            text(
                "INSERT INTO internal.accident_revisions "
                "(accident_id, field, old_value, new_value, method, rule_version, run_id) "
                "VALUES (:a, :f, :old, :new, :m, :v, :run)"
            ),
            key | {"old": _as_text(old), "new": _as_text(change.new_value), "m": change.method, "run": run_id},
        )
        result.applied += 1
    return result


def _proposal(change: Change) -> str:
    if change.field == LOCATION:
        return f"{LOCATION}={'null' if change.new_value is None else 'moved'}"
    return f"{change.field}={_as_text(change.new_value)}"


async def run_step(
    conn: AsyncConnection,
    *,
    step: str,
    rule_version: str,
    changes: Sequence[Change],
    report: ValidationReport,
    apply: bool,
) -> dict[str, object]:
    """Dry run by default: the summary is returned, never logged. On apply, start_run,
    the writes and finish_run share `conn`, so the caller's one commit lands them together."""
    summary: dict[str, object] = {
        "step": step,
        "rule_version": rule_version,
        "proposed": dict(Counter(_proposal(c) for c in changes).most_common(40)),
        "report": report.summary(),
    }
    if not apply:
        return summary | {"mode": "dry_run"}
    # The hash covers the proposed changes, not the rule version (pre-flight G13): the
    # version is recorded through finish_run's allowlisted validation_report instead.
    content = sha256_rows((c.accident_id, c.field, _as_text(c.new_value), c.method) for c in changes)
    run_id = await start_run(conn, source=f"repair:{step}", window_start=None, window_end=None, content_sha256=content)
    applied = await apply_changes(conn, changes, rule_version=rule_version, run_id=run_id)
    await finish_run(conn, run_id, status="ok", report=report, rows_upserted=applied.applied, rule_version=rule_version)
    return summary | {
        "mode": "apply",
        "run_id": str(run_id),
        "applied": applied.applied,
        "unchanged": applied.unchanged,
        "already_recorded": applied.already_recorded,
    }
