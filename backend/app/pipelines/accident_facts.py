"""R11: facts-only accident refresh from a hand-filled CSV (manual AAC entries; CAIC/NPS exports
produced by private tools outside this repo). New rows only: nothing existing is overwritten or
deleted. country is taken from the data (coordinates and a US state), never assumed. Rows are
linked to an MP route only on a unique strong name match near the point; everything else goes
to the owner (data/review/r11_route_matches.csv) and is applied later with --apply-route-ids."""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import json
import math
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.data.repair import review
from app.data.repair.activity import R4_VERSION, classify_activity, needs_review
from app.data.repair.geocode import TRUSTED_POINT_PRECISIONS, classify_state
from app.data.repair.severity import severity_scale
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.db import ingest_engine
from app.pipelines.geo import haversine_km_many
from app.pipelines.gnis import STATE_CODES
from app.pipelines.ingest_log import finish_run, sha256_rows, start_run, write_quarantine
from app.pipelines.localday import tz_for_point
from app.pipelines.textsim import normalize_text
from app.pipelines.validate import ValidationReport, coord_problem, date_problem

SOURCE = "accident_facts"
ROUTE_IDS_SOURCE = "accident_facts:route_ids"
COLUMNS: tuple[str, ...] = (
    "source", "source_id", "date", "date_precision", "state", "mountain", "route", "mp_route_id", "latitude",
    "longitude", "geocode_precision", "activity", "accident_type", "injury_severity", "exp_years_climbing",
    "exp_stated_level", "exp_first_season", "guided", "source_url", "summary",
)
EARLIEST = dt.date(1970, 1, 1)
REFRESH_SINCE = dt.date(2024, 8, 1)
# Derived from gnis.STATE_CODES (the one US list geocode.classify_state uses), never a second copy.
US_STATES: frozenset[str] = frozenset({*(name.lower() for name in STATE_CODES), *(c.lower() for c in STATE_CODES.values())})
METHOD = "facts_csv"
CLIMBING_CLASSES = frozenset({"climbing", "climbing_approach"})
# Same radius as R4's ski-approach terrain test: a crag-precision point is within about a
# kilometre of its routes, and MP route points are often their area's point.
ROUTE_MATCH_RADIUS_M = 1000
REVIEW_NAME = "r11_route_matches.csv"
REVIEW_COLUMNS = [
    "source", "source_id", "date", "state", "mountain", "route", "latitude", "longitude", "reason",
    "candidate_ids", "candidate_names", "candidate_distances_m",
]
MAX_REVIEW_CANDIDATES = 10
_ROUTE_ABBREV = {"rte": "route"}


class FactRow(BaseModel):
    # Lengths are the accidents column lengths (0001 baseline), so an over-long value is
    # quarantined at parse instead of failing the INSERT mid-transaction.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=50)
    source_id: str = Field(min_length=1, max_length=100)
    date: dt.date
    date_precision: Literal["day", "month", "year"]
    state: str = Field(min_length=2, max_length=100)
    mountain: str | None = Field(default=None, max_length=255)
    route: str | None = Field(default=None, max_length=255)
    mp_route_id: int | None = Field(default=None, ge=1)
    latitude: float | None = None
    longitude: float | None = None
    geocode_precision: Literal["exact", "crag", "area", "park_centroid", "region_fallback", "unknown"]
    activity: str = Field(min_length=1, max_length=100)
    accident_type: str | None = Field(default=None, max_length=100)
    injury_severity: str | None = Field(default=None, max_length=50)
    exp_years_climbing: int | None = Field(default=None, ge=0, le=80)
    exp_stated_level: Literal["novice", "intermediate", "experienced", "expert", "unknown"] = "unknown"
    exp_first_season: bool | None = None
    guided: Literal["guided", "unguided", "unknown"] = "unknown"
    source_url: str = Field(pattern=r"^https://\S+$")
    summary: str = Field(min_length=1, max_length=200)

    @property
    def ref(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass(frozen=True)
class Derived:
    family: SourceFamily
    activity_class: str
    inclusion_flag: str | None
    severity_scale: str


def derive(row: FactRow) -> Derived | str:
    """The checks that would otherwise raise inside insert_new: Derived, or a quarantine reason."""
    try:
        family = source_family(row.source)
    except ValueError:
        return "unmapped_source"
    try:
        scale = severity_scale(family, row.injury_severity)
    except ValueError:
        return "unmapped_severity"
    if needs_review(family, row.activity):
        return "activity_needs_review"
    # No terrain check here, so a ski row can never qualify as an approach; a later full R4
    # run cannot upgrade it either (R4 skips rows it did not label).
    classified = classify_activity(family, row.activity, row.summary, near_climb_terrain=False)
    if classified is None:
        return "unmapped_activity"
    return Derived(family, classified[0], classified[1], scale)


def _country_problem(row: FactRow) -> str | None:
    state = classify_state(row.state)
    if row.latitude is not None or row.longitude is not None:
        if (problem := coord_problem(row.latitude, row.longitude)) is not None:
            return problem
        # The US boxes are coarse (southern BC is inside), so a foreign state still settles it.
        if state.startswith("foreign:"):
            return "outside_us"
        return None if state.startswith("us:") else "unknown_country"
    if row.geocode_precision != "unknown":
        return "precision_without_coords"
    return None if state.startswith("us:") else "unknown_country"


def parse_facts(path: Path, *, today: dt.date, report: ValidationReport) -> list[FactRow]:
    rows: list[FactRow] = []
    seen: set[tuple[str, str]] = set()
    # utf-8-sig: Excel's "CSV UTF-8" prepends a BOM that would otherwise glue onto "source".
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"{path.name}: header must be exactly {','.join(COLUMNS)}")
        for number, raw in enumerate(reader, start=2):
            ref = f"{path.name}:{number}"
            if None in raw:
                report.quarantine(ref, "invalid", fields=["<extra fields>"])
                continue
            # A blank cell is "not given": the field default applies (exp_stated_level and
            # guided become 'unknown', optional facts stay NULL), never 0 or ''.
            given = {k: v.strip() for k, v in raw.items() if isinstance(v, str) and v.strip()}
            try:
                row = FactRow.model_validate(given)
            except ValidationError as exc:
                report.quarantine(ref, "invalid", fields=sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]}))
                continue
            problem = (
                date_problem(row.date, today=today, earliest=EARLIEST)
                or ("duplicate_in_file" if (row.source, row.source_id) in seen else None)
                or _country_problem(row)
            )
            derived = derive(row) if problem is None else None
            if isinstance(derived, str):
                problem = derived
            if problem is not None:
                report.quarantine(ref, problem)
                continue
            seen.add((row.source, row.source_id))
            report.accept()
            rows.append(row)
    return rows


def month_gaps(dates: Iterable[dt.date], *, since: dt.date, today: dt.date) -> list[str]:
    """Closed months since `since` with no incident: the spec's post-2024 gap log. The current
    month is not closed yet, so it is never reported as a gap."""
    seen = {(d.year, d.month) for d in dates}
    gaps: list[str] = []
    year, month = since.year, since.month
    while (year, month) < (today.year, today.month):
        if (year, month) not in seen:
            gaps.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return gaps


def _fold(value: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return normalize_text("".join(c for c in decomposed if not unicodedata.combining(c)))


def route_key(name: str | None) -> str:
    tokens = [_ROUTE_ABBREV.get(t, t) for t in _fold(name).split()]
    return " ".join(t for t in tokens if t != "the")


@dataclass(frozen=True)
class Candidate:
    mp_route_id: int
    name: str
    distance_m: float


@dataclass(frozen=True)
class RouteMatch:
    mp_route_id: int | None
    reason: str | None
    candidates: tuple[Candidate, ...]


def match_route(route: str | None, candidates: Sequence[Candidate]) -> RouteMatch:
    """A link needs exactly one candidate whose normalised name equals the row's route. Not a
    Jaro-Winkler threshold: JW scores "Northeast Ridge"/"Northwest Ridge" 0.97 and "Ellingwood
    Arete"/"Ellingwood Ridge" 0.93, both different routes; near names go to the owner instead."""
    nearest = tuple(sorted(candidates, key=lambda c: c.distance_m))
    key = route_key(route)
    if not key:
        return RouteMatch(None, "no_route_text", nearest)
    if not nearest:
        return RouteMatch(None, "no_candidate", nearest)
    strong = [c for c in nearest if route_key(c.name) == key]
    if len(strong) == 1:
        return RouteMatch(strong[0].mp_route_id, None, nearest)
    return RouteMatch(None, "ambiguous" if strong else "no_name_match", nearest)


_CANDIDATES = text(
    "SELECT p.mp_route_id, p.name, p.lat, p.lon FROM ("
    "SELECT r.mp_route_id, r.name, "
    "CASE WHEN r.latitude IS NOT NULL AND r.longitude IS NOT NULL THEN r.latitude ELSE l.latitude END AS lat, "
    "CASE WHEN r.latitude IS NOT NULL AND r.longitude IS NOT NULL THEN r.longitude ELSE l.longitude END AS lon "
    "FROM mp_routes r LEFT JOIN mp_locations l ON l.mp_id = r.location_id) p "
    "WHERE p.lat BETWEEN :lat_lo AND :lat_hi AND p.lon BETWEEN :lon_lo AND :lon_hi"
)


async def route_candidates(conn: AsyncConnection, lat: float, lon: float) -> list[Candidate]:
    # A generous box first, then the exact great-circle distance.
    dlat = ROUTE_MATCH_RADIUS_M / 1000 / 111.0 * 1.5
    dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
    found = (await conn.execute(_CANDIDATES, {
        "lat_lo": lat - dlat, "lat_hi": lat + dlat, "lon_lo": lon - dlon, "lon_hi": lon + dlon,
    })).all()
    if not found:
        return []
    meters = haversine_km_many(
        lat, lon, np.array([float(r[2]) for r in found]), np.array([float(r[3]) for r in found])
    ) * 1000
    return [
        Candidate(int(r[0]), str(r[1]), round(float(d), 1))
        for r, d in zip(found, meters, strict=True) if d <= ROUTE_MATCH_RADIUS_M
    ]


async def _existing(conn: AsyncConnection) -> set[tuple[str, str]]:
    result = await conn.execute(text("SELECT source, source_id FROM accidents WHERE source_id IS NOT NULL"))
    return {(str(s), str(i)) for s, i in result.all()}


async def _known_routes(conn: AsyncConnection, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    result = await conn.execute(text("SELECT mp_route_id FROM mp_routes WHERE mp_route_id = ANY(:ids)"), {"ids": sorted(ids)})
    return {int(i) for (i,) in result.all()}


def _demote(report: ValidationReport, ref: str, reason: str) -> None:
    """A row parse_facts accepted and the database refuses: it moves from accepted to
    quarantined, so the run's rows_in still counts each CSV row once."""
    if report.accepted > 0:
        report.accepted -= 1
        report.rows_in -= 1
    report.quarantine(ref, reason)


@dataclass(frozen=True)
class Planned:
    row: FactRow
    derived: Derived
    match: RouteMatch | None


def _review_row(row: FactRow, match: RouteMatch) -> dict[str, object]:
    shown = match.candidates[:MAX_REVIEW_CANDIDATES]
    return {
        "source": row.source, "source_id": row.source_id, "date": row.date.isoformat(), "state": row.state,
        "mountain": row.mountain, "route": row.route, "latitude": row.latitude, "longitude": row.longitude,
        "reason": match.reason,
        "candidate_ids": " | ".join(str(c.mp_route_id) for c in shown),
        "candidate_names": " | ".join(c.name for c in shown),
        "candidate_distances_m": " | ".join(f"{c.distance_m:.0f}" for c in shown),
    }


async def plan_new(conn: AsyncConnection, rows: list[FactRow], *, report: ValidationReport) -> list[Planned]:
    """Every database-dependent refusal and route decision, before any write: read-only, so
    --dry-run runs exactly this."""
    existing = await _existing(conn)
    known = await _known_routes(conn, {r.mp_route_id for r in rows if r.mp_route_id is not None})
    planned: list[Planned] = []
    for row in rows:
        derived = derive(row)
        if isinstance(derived, str):
            _demote(report, row.ref, derived)
            continue
        if (row.source, row.source_id) in existing:
            _demote(report, row.ref, "already_present")
            continue
        if row.mp_route_id is not None:
            if row.mp_route_id not in known:
                _demote(report, row.ref, "unknown_route")
                continue
            planned.append(Planned(row, derived, None))
            continue
        # A route link only matters for rows the clean views keep.
        if derived.activity_class not in CLIMBING_CLASSES:
            planned.append(Planned(row, derived, None))
            continue
        if row.latitude is None or row.longitude is None:
            match = RouteMatch(None, "no_coords", ())
        elif row.geocode_precision not in TRUSTED_POINT_PRECISIONS:
            match = RouteMatch(None, "untrusted_point", ())
        else:
            match = match_route(row.route, await route_candidates(conn, row.latitude, row.longitude))
        planned.append(Planned(row, derived, match))
    return planned


# Exactly the columns grants_phase2.sql grants ingest INSERT on (G3): keep the three in lockstep.
INSERT_COLUMNS: tuple[str, ...] = (
    "source", "source_id", "date", "year", "date_precision", "state", "mountain", "route", "mp_route_id",
    "latitude", "longitude", "geocode_precision", "geocode_method", "country", "tz", "activity", "activity_class",
    "activity_rule_version", "inclusion_flag", "accident_type", "injury_severity", "severity_scale",
    "exp_years_climbing", "exp_stated_level", "exp_first_season", "guided", "exp_rule_version", "source_url",
    "description", "updated_at",
)
INSERT = text(
    f"INSERT INTO accidents ({', '.join(INSERT_COLUMNS)}) VALUES "
    f"({', '.join('now()' if c == 'updated_at' else f':{c}' for c in INSERT_COLUMNS)})"
)


def _params(p: Planned) -> dict[str, object]:
    row = p.row
    route_id = row.mp_route_id if row.mp_route_id is not None else (p.match.mp_route_id if p.match else None)
    zone = tz_for_point(row.latitude, row.longitude) if row.latitude is not None and row.longitude is not None else None
    return {
        "source": row.source, "source_id": row.source_id, "date": row.date, "year": float(row.date.year),
        "date_precision": row.date_precision, "state": row.state, "mountain": row.mountain, "route": row.route,
        "mp_route_id": route_id, "latitude": row.latitude, "longitude": row.longitude,
        "geocode_precision": row.geocode_precision, "geocode_method": METHOD,
        # parse_facts quarantined every row that is not US by both its point and its state.
        "country": "US", "tz": zone, "activity": row.activity, "activity_class": p.derived.activity_class,
        "activity_rule_version": R4_VERSION, "inclusion_flag": p.derived.inclusion_flag,
        "accident_type": row.accident_type, "injury_severity": row.injury_severity,
        "severity_scale": p.derived.severity_scale, "exp_years_climbing": row.exp_years_climbing,
        "exp_stated_level": row.exp_stated_level, "exp_first_season": row.exp_first_season, "guided": row.guided,
        "exp_rule_version": METHOD, "source_url": row.source_url, "description": row.summary,
    }


async def insert_new(
    conn: AsyncConnection,
    rows: list[FactRow],
    *,
    report: ValidationReport,
    review_rows: list[dict[str, object]] | None = None,
) -> int:
    """Insert rows not already present. Rows needing a route decision are appended to
    review_rows (ids, names and distances only)."""
    planned = await plan_new(conn, rows, report=report)
    for p in planned:
        await conn.execute(INSERT, _params(p))
        if review_rows is not None and p.match is not None and p.match.mp_route_id is None:
            review_rows.append(_review_row(p.row, p.match))
    return len(planned)


_OWN_ROW = (
    "SELECT a.accident_id, a.mp_route_id, "
    "EXISTS (SELECT 1 FROM internal.accidents_raw r WHERE r.accident_id = a.accident_id) AS legacy "
    "FROM accidents a WHERE a.source = :source AND a.source_id = :source_id"
)


async def apply_route_ids(conn: AsyncConnection, rows: list[FactRow], *, report: ValidationReport, apply: bool) -> int:
    """The owner's later mp_route_id fills for rows already loaded. Only R11's own rows (absent
    from the frozen 0004 snapshot, so never a legacy row) and only where mp_route_id IS NULL:
    a link set by anyone else is never overwritten."""
    known = await _known_routes(conn, {r.mp_route_id for r in rows if r.mp_route_id is not None})
    updated = 0
    for row in rows:
        if row.mp_route_id is None:
            continue
        if row.mp_route_id not in known:
            _demote(report, row.ref, "unknown_route")
            continue
        found = (await conn.execute(text(_OWN_ROW), {"source": row.source, "source_id": row.source_id})).all()
        if not found:
            _demote(report, row.ref, "not_loaded")
            continue
        if len(found) > 1 or any(bool(legacy) for _, _, legacy in found):
            _demote(report, row.ref, "not_an_r11_row")
            continue
        current = found[0][1]
        if current is not None:
            if int(current) != row.mp_route_id:
                _demote(report, row.ref, "route_already_set")
            continue
        if apply:
            await conn.execute(
                text("UPDATE accidents SET mp_route_id = :rid, updated_at = now() "
                     "WHERE accident_id = :aid AND mp_route_id IS NULL"),
                {"rid": row.mp_route_id, "aid": int(found[0][0])},
            )
        updated += 1
    return updated


async def _gap_log(conn: AsyncConnection, families: set[str], *, today: dt.date) -> list[str]:
    dated = (await conn.execute(text("SELECT source, date FROM accidents WHERE date >= :since"),
                                {"since": REFRESH_SINCE})).all()
    by_family: dict[str, list[dt.date]] = {f: [] for f in families}
    for source, day in dated:
        try:
            family = source_family(str(source))
        except ValueError:
            continue
        if family in by_family:
            by_family[family].append(day)
    lines: list[str] = []
    for name in sorted(families):
        gaps = month_gaps(by_family[name], since=REFRESH_SINCE, today=today)
        lines.append(f"post-2024 gap {name}: {','.join(gaps) if gaps else 'none'}")
    return lines


def _families(rows: Iterable[FactRow]) -> set[str]:
    return {d.family for d in (derive(r) for r in rows) if isinstance(d, Derived)}


async def _read_only(engine: AsyncEngine, rows: list[FactRow], report: ValidationReport,
                     *, route_ids: bool) -> dict[str, object]:
    async with engine.connect() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        if route_ids:
            n = await apply_route_ids(conn, rows, report=report, apply=False)
            out: dict[str, object] = {"would_update": n}
        else:
            planned = await plan_new(conn, rows, report=report)
            pending = sum(1 for p in planned if p.match is not None and p.match.mp_route_id is None)
            out = {"would_insert": len(planned), "review_pending": pending}
        await conn.rollback()
    return {"mode": "dry_run", **out, "report": report.summary()}


async def main(path: Path, *, today: dt.date, dry_run: bool, overwrite: bool = False,
               route_ids: bool = False) -> dict[str, object]:
    report = ValidationReport(ROUTE_IDS_SOURCE if route_ids else SOURCE)
    rows = parse_facts(path, today=today, report=report)
    engine = ingest_engine()
    try:
        if dry_run:
            return await _read_only(engine, rows, report, route_ids=route_ids)
        sha = sha256_rows([(r.source, r.source_id, r.mp_route_id) for r in rows])
        async with engine.begin() as conn:
            run_id = await start_run(conn, source=report.source, window_start=None, window_end=None, content_sha256=sha)
            if route_ids:
                n = await apply_route_ids(conn, rows, report=report, apply=True)
                await write_quarantine(conn, run_id, report)
                await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n)
                return {"updated": n, "report": report.summary()}
            pending: list[dict[str, object]] = []
            n = await insert_new(conn, rows, report=report, review_rows=pending)
            gaps = await _gap_log(conn, _families(rows), today=today)
            await write_quarantine(conn, run_id, report)
            await finish_run(conn, run_id, status="ok", report=report, rows_upserted=n, problems=gaps,
                             metrics={"review_pending": len(pending)})
            out = review.REVIEW_DIR / REVIEW_NAME
            if pending:
                # Inside the transaction: a refused overwrite rolls the whole load back.
                review._write(out, pending, REVIEW_COLUMNS, "mp_route_id", overwrite=overwrite, encoding="utf-8-sig")
    finally:
        await engine.dispose()
    return {"inserted": n, "gaps": gaps, "review_pending": len(pending),
            "review_path": str(out) if pending else None, "report": report.summary()}


if __name__ == "__main__":
    from app.services.temporal_weighting import utc_today

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing r11_route_matches.csv")
    parser.add_argument("--apply-route-ids", action="store_true",
                        help="set mp_route_id from the CSV on already-loaded R11 rows where it is NULL")
    args = parser.parse_args()
    result = asyncio.run(main(args.file, today=utc_today(), dry_run=args.dry_run, overwrite=args.overwrite,
                              route_ids=args.apply_route_ids))
    print(json.dumps(result, sort_keys=True, default=str))
