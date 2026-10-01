"""The one writer for accident repairs. Every changed field gets an
internal.accident_revisions row keyed (accident_id, field, rule_version); a rerun with the
same rule version finds the key and changes nothing."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
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
    """Revision text is what a reversal reads back, so it is canonical: bools are
    "true"/"false", integral floats drop ".0" (year is float8, so 2003.0 audits as "2003"),
    and a location is "lat,lon" at 6 decimals with "null" for a missing half."""
    if value is None:
        return None
    if isinstance(value, tuple):
        return ",".join("null" if part is None else f"{float(part):.6f}" for part in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
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
    seen: set[tuple[int, str]] = set()
    for change in changes:
        if change.field != LOCATION and change.field not in REPAIRABLE_FIELDS:
            raise ValueError(f"field {change.field!r} is not repairable")
        # One revision row per key, so a second change to the same key in one batch would
        # either be silently skipped or fight the first; refuse before anything is written.
        if (change.accident_id, change.field) in seen:
            raise ValueError(f"duplicate change for accident {change.accident_id} field {change.field!r}")
        seen.add((change.accident_id, change.field))
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
            # A half-null pair stays a tuple, so nulling it is a real change and clears both halves.
            old: object = None if row.latitude is None and row.longitude is None else (row.latitude, row.longitude)
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
    metrics: Mapping[str, int | float] | None = None,
) -> dict[str, object]:
    """Dry run by default: the summary is returned, never logged. On apply, start_run,
    the writes and finish_run share `conn`, so the caller's one commit lands them together."""
    # `proposed` is the top 40 only; a rehearsal read it as the whole run (2026-10-01), so
    # the totals ride alongside it.
    summary: dict[str, object] = {
        "step": step,
        "rule_version": rule_version,
        "proposed": dict(Counter(_proposal(c) for c in changes).most_common(40)),
        "proposed_total": len(changes),
        "proposed_by_field": dict(sorted(Counter(c.field for c in changes).items())),
        "report": report.summary(),
    }
    if not apply:
        return summary | {"mode": "dry_run"}
    # Repair runs never dedup on this hash (find_completed is not consulted; idempotency is
    # the revisions key), so it only fingerprints what the run proposed, under which rule.
    content = sha256_rows(
        [(rule_version,), *((c.accident_id, c.field, _as_text(c.new_value), c.method) for c in changes)]
    )
    run_id = await start_run(conn, source=f"repair:{step}", window_start=None, window_end=None, content_sha256=content)
    applied = await apply_changes(conn, changes, rule_version=rule_version, run_id=run_id)
    await finish_run(
        conn, run_id, status="ok", report=report, rows_upserted=applied.applied, rule_version=rule_version,
        metrics=metrics,
    )
    return summary | {
        "mode": "apply",
        "run_id": str(run_id),
        "applied": applied.applied,
        "unchanged": applied.unchanged,
        "already_recorded": applied.already_recorded,
    }
