"""Refresh the MP catalog (canonical_areas, canonical_routes) from mp_locations/mp_routes.
Derived columns only: no names, grades, URLs or prose are copied; displayable facts stay in
the MP tables, read by the same ids through app.catalog_seam (Phase 4). Rows are inserted or
updated, never deleted (FKs pin them to the MP rows). Route coordinates are never read: they
come only from the route's location (owner decision 2026-10-12).

One transaction. A run whose scorable count falls more than MAX_SCORABLE_DROP below the
stored catalog's is rejected and writes nothing but its log row: a scrape that blanks type
strings must not silently unscore routes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.pipelines.area_tree import AreaNode, LocationRow, build_area_tree, root_state
from app.pipelines.ingest_log import finish_run, start_run, write_quarantine
from app.pipelines.route_types import TYPE_RULE_VERSION, TypeGroup, flags_from_mp_type, map_type_group
from app.pipelines.validate import ValidationReport

CATALOG_VERSION = "cat-mp-v1"
SOURCE = "mp_catalog"
MAX_SCORABLE_DROP = 0.03
CHUNK = 20_000
QUARANTINED_COORD_ISSUES = frozenset({"outside_us", "not_finite", "out_of_range"})
UnscorableReason = Literal["boulder", "no_area", "not_us", "unknown_type", "no_coordinates"]

LOCATIONS_SQL = text("SELECT mp_id, parent_id, latitude, longitude FROM mp_locations")
ROOTS_SQL = text("SELECT mp_id, name FROM mp_locations WHERE parent_id IS NULL")
ROUTES_SQL = text("SELECT mp_route_id, location_id, type, grade FROM mp_routes")
STORED_SCORABLE_SQL = text("SELECT count(*) FROM canonical_routes WHERE scorable")

AREA_UPSERT = text("""
INSERT INTO canonical_areas AS c (area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision,
  coord_source_id, point_key, catalog_version, run_id)
SELECT u.area_id, u.parent_id, CAST(u.path AS ltree), u.depth, u.tree_status, u.state_code, u.lat, u.lon,
       u.coord_precision, u.coord_source_id, u.point_key, :v, :run
FROM unnest(CAST(:area_id AS bigint[]), CAST(:parent_id AS bigint[]), CAST(:path AS text[]),
            CAST(:depth AS smallint[]), CAST(:tree_status AS text[]), CAST(:state_code AS text[]),
            CAST(:lat AS float8[]), CAST(:lon AS float8[]), CAST(:coord_precision AS text[]),
            CAST(:coord_source_id AS bigint[]), CAST(:point_key AS text[]))
  AS u(area_id, parent_id, path, depth, tree_status, state_code, lat, lon, coord_precision, coord_source_id, point_key)
ON CONFLICT (area_id) DO UPDATE SET parent_id = EXCLUDED.parent_id, path = EXCLUDED.path, depth = EXCLUDED.depth,
  tree_status = EXCLUDED.tree_status, state_code = EXCLUDED.state_code, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
  coord_precision = EXCLUDED.coord_precision, coord_source_id = EXCLUDED.coord_source_id,
  point_key = EXCLUDED.point_key, catalog_version = EXCLUDED.catalog_version, run_id = EXCLUDED.run_id,
  updated_at = now()
WHERE (c.parent_id, c.path, c.depth, c.tree_status, c.state_code, c.lat, c.lon, c.coord_precision,
       c.coord_source_id, c.point_key, c.catalog_version)
  IS DISTINCT FROM (EXCLUDED.parent_id, EXCLUDED.path, EXCLUDED.depth, EXCLUDED.tree_status, EXCLUDED.state_code,
       EXCLUDED.lat, EXCLUDED.lon, EXCLUDED.coord_precision, EXCLUDED.coord_source_id, EXCLUDED.point_key,
       EXCLUDED.catalog_version)
RETURNING (xmax = 0) AS inserted
""")
ROUTE_UPSERT = text("""
INSERT INTO canonical_routes AS c (route_id, area_id, type_group, is_boulder, disciplines, type_rule_version, scorable,
  unscorable_reason, facts_sha256, catalog_version, run_id)
SELECT u.route_id, u.area_id, u.type_group, u.is_boulder, string_to_array(u.disciplines, ','), :rv, u.scorable,
       u.reason, u.sha, :v, :run
FROM unnest(CAST(:route_id AS bigint[]), CAST(:area_id AS bigint[]), CAST(:type_group AS text[]),
            CAST(:is_boulder AS boolean[]), CAST(:disciplines AS text[]), CAST(:scorable AS boolean[]),
            CAST(:reason AS text[]), CAST(:sha AS text[]))
  AS u(route_id, area_id, type_group, is_boulder, disciplines, scorable, reason, sha)
ON CONFLICT (route_id) DO UPDATE SET area_id = EXCLUDED.area_id, type_group = EXCLUDED.type_group,
  is_boulder = EXCLUDED.is_boulder, disciplines = EXCLUDED.disciplines, type_rule_version = EXCLUDED.type_rule_version,
  scorable = EXCLUDED.scorable, unscorable_reason = EXCLUDED.unscorable_reason, facts_sha256 = EXCLUDED.facts_sha256,
  catalog_version = EXCLUDED.catalog_version, run_id = EXCLUDED.run_id, updated_at = now()
WHERE (c.area_id, c.type_group, c.is_boulder, c.disciplines, c.type_rule_version, c.scorable, c.unscorable_reason,
       c.facts_sha256, c.catalog_version)
  IS DISTINCT FROM (EXCLUDED.area_id, EXCLUDED.type_group, EXCLUDED.is_boulder, EXCLUDED.disciplines,
       EXCLUDED.type_rule_version, EXCLUDED.scorable, EXCLUDED.unscorable_reason, EXCLUDED.facts_sha256,
       EXCLUDED.catalog_version)
RETURNING (xmax = 0) AS inserted
""")


@dataclass(frozen=True)
class RouteFacts:
    route_id: int
    location_id: int | None
    type: str | None
    grade: str | None


@dataclass(frozen=True)
class CatalogRoute:
    route_id: int
    area_id: int | None
    type_group: TypeGroup | None
    is_boulder: bool
    disciplines: tuple[str, ...]
    scorable: bool
    unscorable_reason: UnscorableReason | None
    facts_sha256: str


@dataclass
class RefreshResult:
    status: Literal["ok", "rejected"]
    counts: dict[str, object]
    problems: list[str]
    report: ValidationReport = field(default_factory=lambda: ValidationReport(SOURCE))
    rows_upserted: int = 0


def _facts_sha(f: RouteFacts) -> str:
    raw = json.dumps([CATALOG_VERSION, TYPE_RULE_VERSION, f.location_id, f.type, f.grade], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def classify_route(facts: RouteFacts, areas: Mapping[int, AreaNode]) -> CatalogRoute:
    t = map_type_group(flags_from_mp_type(facts.type), [facts.grade])
    area = areas.get(facts.location_id) if facts.location_id is not None else None
    reason: UnscorableReason | None
    if t.is_boulder:
        reason = "boulder"
    elif area is None or area.tree_status in ("cycle", "orphan_parent"):
        reason = "no_area"
    elif area.tree_status == "foreign_root":
        reason = "not_us"
    elif t.type_group == "unknown":
        reason = "unknown_type"
    elif area.coord_precision == "none":
        reason = "no_coordinates"
    else:
        reason = None
    return CatalogRoute(facts.route_id, area.area_id if area else None, t.type_group, t.is_boulder, t.disciplines,
                        reason is None, reason, _facts_sha(facts))


def _area_columns(nodes: Sequence[AreaNode]) -> dict[str, list[object]]:
    return {
        "area_id": [n.area_id for n in nodes], "parent_id": [n.parent_id for n in nodes],
        "path": [n.path for n in nodes], "depth": [n.depth for n in nodes],
        "tree_status": [n.tree_status for n in nodes], "state_code": [n.state_code for n in nodes],
        "lat": [n.lat for n in nodes], "lon": [n.lon for n in nodes],
        "coord_precision": [n.coord_precision for n in nodes], "coord_source_id": [n.coord_source_id for n in nodes],
        "point_key": [n.point_key for n in nodes],
    }


def _route_columns(routes: Sequence[CatalogRoute]) -> dict[str, list[object]]:
    return {
        "route_id": [r.route_id for r in routes], "area_id": [r.area_id for r in routes],
        "type_group": [r.type_group for r in routes], "is_boulder": [r.is_boulder for r in routes],
        "disciplines": [",".join(r.disciplines) for r in routes], "scorable": [r.scorable for r in routes],
        "reason": [r.unscorable_reason for r in routes], "sha": [r.facts_sha256 for r in routes],
    }


async def _upsert(conn: AsyncConnection, stmt: TextClause, columns: dict[str, list[object]],
                  params: dict[str, object]) -> tuple[int, int]:
    inserted = changed = 0
    n = len(next(iter(columns.values()))) if columns else 0
    for start in range(0, n, CHUNK):
        chunk: dict[str, object] = {k: v[start : start + CHUNK] for k, v in columns.items()}
        for (was_insert,) in (await conn.execute(stmt, chunk | params)).all():
            inserted += int(bool(was_insert))
            changed += int(not was_insert)
    return inserted, changed


async def refresh(conn: AsyncConnection, *, run_id: uuid.UUID, dry_run: bool = False) -> RefreshResult:
    report = ValidationReport(SOURCE)
    locations = [LocationRow(int(i), None if p is None else int(p), la, lo)
                 for i, p, la, lo in (await conn.execute(LOCATIONS_SQL)).all()]
    roots = {int(i): root_state(n) for i, n in (await conn.execute(ROOTS_SQL)).all()}
    areas = build_area_tree(locations, roots)
    for node in areas.values():
        if node.tree_status in ("cycle", "orphan_parent"):
            report.quarantine(str(node.area_id), node.tree_status)
        elif node.tree_status == "us" and node.coord_issue in QUARANTINED_COORD_ISSUES:
            report.quarantine(str(node.area_id), node.coord_issue)
        else:
            report.accept()
    routes = [classify_route(RouteFacts(int(r), None if loc is None else int(loc), t, g), areas)
              for r, loc, t, g in (await conn.execute(ROUTES_SQL)).all()]
    scorable = sum(r.scorable for r in routes)
    counts: dict[str, object] = {
        "areas": len(areas),
        "areas_by_status": dict(sorted(Counter(n.tree_status for n in areas.values()).items())),
        "areas_by_coord": dict(sorted(Counter(n.coord_precision for n in areas.values()).items())),
        "routes": len(routes),
        "routes_scorable": scorable,
        "routes_by_reason": dict(sorted(Counter(r.unscorable_reason or "scorable" for r in routes).items())),
        "routes_by_type_group": dict(sorted(Counter(r.type_group or "boulder" for r in routes).items())),
    }
    stored = int((await conn.execute(STORED_SCORABLE_SQL)).scalar_one())
    counts["stored_scorable"] = stored
    if stored and scorable < stored * (1 - MAX_SCORABLE_DROP):
        problem = f"scorable routes {scorable} fell more than {MAX_SCORABLE_DROP:.0%} below the stored {stored}"
        return RefreshResult("rejected", counts, [problem], report)
    if dry_run:
        return RefreshResult("ok", counts, [], report)
    params: dict[str, object] = {"v": CATALOG_VERSION, "run": run_id}
    a_ins, a_chg = await _upsert(conn, AREA_UPSERT, _area_columns(list(areas.values())), params)
    r_ins, r_chg = await _upsert(conn, ROUTE_UPSERT, _route_columns(routes), params | {"rv": TYPE_RULE_VERSION})
    counts |= {"areas_inserted": a_ins, "areas_changed": a_chg, "routes_inserted": r_ins, "routes_changed": r_chg}
    return RefreshResult("ok", counts, [], report, rows_upserted=r_ins + r_chg)


async def run_logged(engine: AsyncEngine, *, dry_run: bool) -> RefreshResult:
    async with engine.begin() as conn:
        if dry_run:
            return await refresh(conn, run_id=uuid.uuid4(), dry_run=True)
        run_id = await start_run(conn, source=SOURCE, window_start=None, window_end=None, content_sha256=None)
        result = await refresh(conn, run_id=run_id)
        if result.report.quarantined_total():
            await write_quarantine(conn, run_id, result.report)
        await finish_run(conn, run_id, status=result.status, report=result.report, rows_upserted=result.rows_upserted,
                         problems=result.problems or None, rule_version=f"{CATALOG_VERSION}/{TYPE_RULE_VERSION}")
        return result


def cli() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.pipelines.mp_catalog")
    parser.add_argument("step", choices=["refresh"])
    parser.add_argument("--dry-run", action="store_true", help="compute and print counts; write nothing")
    args = parser.parse_args()
    from app.pipelines.db import ingest_engine

    async def go() -> RefreshResult:
        engine = ingest_engine()
        try:
            return await run_logged(engine, dry_run=args.dry_run)
        finally:
            await engine.dispose()

    result = asyncio.run(go())
    print(json.dumps({"status": result.status, "problems": result.problems, **result.counts,
                      "quarantined": dict(result.report.quarantined)}, sort_keys=True, default=str))
    if result.status != "ok":
        sys.exit(1)


if __name__ == "__main__":
    cli()
