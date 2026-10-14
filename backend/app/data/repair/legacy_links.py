"""R10 on the MP catalog. The identity on accidents.mp_route_id needs no job (the view);
this step links legacy accident links that carried a usable MP id, and lists the rest for
optional owner review with ranked candidates. Nothing is linked by name: candidate ranking
only orders the review list. Never writes accidents."""

from __future__ import annotations

import argparse
import csv
import math
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import review
from app.pipelines.geo import haversine_km
from app.pipelines.textsim import jaro_winkler, normalize_text, place_key

R10_VERSION = "r10-mp-v1"
ROUTE_RADIUS_KM = 2.0
AREA_RADIUS_KM = 3.0
MAX_CANDIDATES = 5
_DIGITS = re.compile(r"^\s*(\d{1,18})\s*$")
_DEG_PER_KM = 1 / 111.0

LEGACY_SQL = text(
    "SELECT a.accident_id, a.mp_route_id, a.route_id, a.mountain_id, r.mp_route_id, r.name, r.latitude, r.longitude, "
    "m.name, m.latitude, m.longitude FROM accidents a "
    "LEFT JOIN routes r ON r.route_id = a.route_id LEFT JOIN mountains m ON m.mountain_id = a.mountain_id "
    "WHERE a.route_id IS NOT NULL OR a.mountain_id IS NOT NULL ORDER BY a.accident_id"
)
CATALOG_IDS_SQL = text("SELECT route_id FROM canonical_routes")
ROUTE_BOX_SQL = text(
    "SELECT r.route_id, m.name, a.lat, a.lon, a.coord_precision FROM canonical_routes r JOIN canonical_areas a ON a.area_id = r.area_id "
    "JOIN mp_routes m ON m.mp_route_id = r.route_id WHERE NOT r.is_boulder AND a.lat BETWEEN :la0 AND :la1 "
    "AND a.lon BETWEEN :lo0 AND :lo1"
)
AREA_BOX_SQL = text(
    "SELECT a.area_id, l.name, a.lat, a.lon, a.coord_precision FROM canonical_areas a JOIN mp_locations l ON l.mp_id = a.area_id "
    "WHERE a.lat BETWEEN :la0 AND :la1 AND a.lon BETWEEN :lo0 AND :lo1"
)


@dataclass(frozen=True)
class LegacyRow:
    accident_id: int
    mp_route_id: int | None
    legacy_route_id: int | None
    legacy_mountain_id: int | None
    legacy_mp_id: str | None
    route_name: str | None
    route_lat: float | None
    route_lon: float | None
    mountain_name: str | None
    mountain_lat: float | None
    mountain_lon: float | None


@dataclass(frozen=True)
class Link:
    accident_id: int
    route_id: int | None
    area_id: int | None
    method: str


@dataclass(frozen=True)
class Unresolved:
    accident_id: int
    legacy_route_id: int | None
    legacy_mountain_id: int | None
    reason: str
    candidate_route_ids: tuple[int, ...] = ()
    candidate_area_ids: tuple[int, ...] = ()
    # False: no coordinate search ran (no legacy coordinates, or the reason has none to run), as
    # opposed to a search that found nothing within the radius.
    candidates_searched: bool = False


def _legacy_id(raw: str | None) -> int | None:
    m = _DIGITS.match(raw or "")
    return int(m.group(1)) if m else None


def plan_row(row: LegacyRow, catalog_ids: frozenset[int]) -> Link | Unresolved | None:
    if row.mp_route_id is not None:
        return None
    if row.legacy_route_id is not None:
        mp_id = _legacy_id(row.legacy_mp_id)
        if mp_id is None:
            return Unresolved(row.accident_id, row.legacy_route_id, row.legacy_mountain_id, "legacy_route_no_mp_id")
        if mp_id not in catalog_ids:
            return Unresolved(row.accident_id, row.legacy_route_id, row.legacy_mountain_id, "legacy_mp_id_not_in_catalog")
        return Link(row.accident_id, mp_id, None, "legacy_mp_id")
    if row.legacy_mountain_id is not None:
        return Unresolved(row.accident_id, None, row.legacy_mountain_id, "legacy_mountain_only")
    return None


def _box(lat: float, lon: float, km: float) -> dict[str, float]:
    dlat = km * _DEG_PER_KM
    dlon = km * _DEG_PER_KM / max(math.cos(math.radians(lat)), 0.01)
    return {"la0": lat - dlat, "la1": lat + dlat, "lo0": lon - dlon, "lo1": lon + dlon}


async def _ranked(conn: AsyncConnection, sql: TextClause, lat: float, lon: float, km: float, name: str | None,
                  key: str) -> tuple[int, ...]:
    norm = normalize_text if key == "route" else place_key
    target = norm(name)
    scored: list[tuple[float, int, float, int]] = []
    for cid, cname, clat, clon, precision in (await conn.execute(sql, _box(lat, lon, km))).all():
        d = haversine_km(lat, lon, float(clat), float(clon))
        if d <= km:
            # On equal names an area's own point beats one inherited from a parent, whose distance is approximate.
            scored.append((-jaro_winkler(target, norm(cname)), 0 if precision == "own" else 1, d, int(cid)))
    return tuple(cid for _, _, _, cid in sorted(scored)[:MAX_CANDIDATES])


async def _with_candidates(conn: AsyncConnection, u: Unresolved, row: LegacyRow) -> Unresolved:
    if u.reason == "legacy_route_no_mp_id" and row.route_lat is not None and row.route_lon is not None:
        ids = await _ranked(conn, ROUTE_BOX_SQL, row.route_lat, row.route_lon, ROUTE_RADIUS_KM, row.route_name, "route")
        return Unresolved(u.accident_id, u.legacy_route_id, u.legacy_mountain_id, u.reason, candidate_route_ids=ids,
                          candidates_searched=True)
    if u.reason == "legacy_mountain_only" and row.mountain_lat is not None and row.mountain_lon is not None:
        ids = await _ranked(conn, AREA_BOX_SQL, row.mountain_lat, row.mountain_lon, AREA_RADIUS_KM, row.mountain_name,
                            "area")
        return Unresolved(u.accident_id, u.legacy_route_id, u.legacy_mountain_id, u.reason, candidate_area_ids=ids,
                          candidates_searched=True)
    return u


async def _legacy_rows(conn: AsyncConnection) -> list[LegacyRow]:
    return [LegacyRow(int(a), None if mp is None else int(mp), None if r is None else int(r),
                      None if m is None else int(m), lmp, rn, rla, rlo, mn, mla, mlo)
            for a, mp, r, m, lmp, rn, rla, rlo, mn, mla, mlo in (await conn.execute(LEGACY_SQL)).all()]


async def run_r10(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    catalog = frozenset(int(r) for (r,) in (await conn.execute(CATALOG_IDS_SQL)).all())
    rows = await _legacy_rows(conn)
    owner_decided = {int(a) for (a,) in (await conn.execute(text(
        "SELECT accident_id FROM internal.r10_unresolved WHERE owner_decision IS NOT NULL"))).all()}
    llm_linked = {int(a) for (a,) in (await conn.execute(text(
        "SELECT accident_id FROM accident_legacy_links WHERE method = 'llm'"))).all()}
    decided = owner_decided | llm_linked
    links: list[Link] = []
    unresolved: list[Unresolved] = []
    conflicts = 0
    for row in rows:
        if row.mp_route_id is not None and _legacy_id(row.legacy_mp_id) not in (None, row.mp_route_id):
            conflicts += 1
        if row.accident_id in decided:
            continue
        planned = plan_row(row, catalog)
        if isinstance(planned, Link):
            links.append(planned)
        elif isinstance(planned, Unresolved):
            unresolved.append(await _with_candidates(conn, planned, row))
    summary: dict[str, object] = {
        "legacy_accidents": len(rows), "owner_decided": len(owner_decided), "llm_linked": len(llm_linked - owner_decided), "legacy_conflict": conflicts,
        "links": dict(Counter(link.method for link in links)),
        "unresolved": dict(sorted(Counter(u.reason for u in unresolved).items())),
        "applied": bool(args.apply),
    }
    if not args.apply:
        return summary
    run_id = uuid.uuid4()
    await conn.execute(text(
        "DELETE FROM accident_legacy_links WHERE method = 'legacy_mp_id' "
        "AND NOT (accident_id = ANY(CAST(:ids AS integer[])))"), {"ids": [link.accident_id for link in links]})
    if links:
        await conn.execute(text(
            "INSERT INTO accident_legacy_links (accident_id, route_id, area_id, method, r10_version, run_id) "
            "VALUES (:a, :r, NULL, 'legacy_mp_id', :v, :run) ON CONFLICT (accident_id) DO UPDATE SET "
            "route_id = EXCLUDED.route_id, area_id = NULL, method = EXCLUDED.method, r10_version = EXCLUDED.r10_version, "
            "run_id = EXCLUDED.run_id, updated_at = now() WHERE accident_legacy_links.method = 'legacy_mp_id'"),
            [{"a": link.accident_id, "r": link.route_id, "v": R10_VERSION, "run": run_id} for link in links])
    await conn.execute(text(
        "INSERT INTO accident_legacy_links (accident_id, route_id, area_id, method, r10_version, run_id) "
        "SELECT accident_id, decided_route_id, decided_area_id, 'owner', :v, :run FROM internal.r10_unresolved "
        "WHERE owner_decision = 'link' ON CONFLICT (accident_id) DO UPDATE SET route_id = EXCLUDED.route_id, "
        "area_id = EXCLUDED.area_id, method = 'owner', llm_call_id = NULL, r10_version = EXCLUDED.r10_version, "
        "run_id = EXCLUDED.run_id, updated_at = now()"), {"v": R10_VERSION, "run": run_id})
    await conn.execute(text(
        "DELETE FROM accident_legacy_links l USING internal.r10_unresolved u WHERE l.accident_id = u.accident_id "
        "AND u.owner_decision = 'no_link'"))
    await conn.execute(text(
        "DELETE FROM internal.r10_unresolved WHERE owner_decision IS NULL "
        "AND NOT (accident_id = ANY(CAST(:ids AS integer[]))) "
        "AND accident_id NOT IN (SELECT accident_id FROM accident_legacy_links WHERE method = 'llm')"),
        {"ids": [u.accident_id for u in unresolved]})
    if unresolved:
        await conn.execute(text(
            "INSERT INTO internal.r10_unresolved (accident_id, legacy_route_id, legacy_mountain_id, reason, "
            "candidate_route_ids, candidate_area_ids, candidates_searched, run_id) VALUES (:a, :lr, :lm, :reason, "
            "CAST(:cr AS bigint[]), CAST(:ca AS bigint[]), :searched, :run) ON CONFLICT (accident_id) DO UPDATE SET "
            "reason = EXCLUDED.reason, candidate_route_ids = EXCLUDED.candidate_route_ids, "
            "candidate_area_ids = EXCLUDED.candidate_area_ids, candidates_searched = EXCLUDED.candidates_searched, "
            # An LLM pick names a letter in the candidate set it was shown; a changed set voids it.
            "llm_call_id = CASE WHEN internal.r10_unresolved.candidate_route_ids = EXCLUDED.candidate_route_ids "
            "AND internal.r10_unresolved.candidate_area_ids = EXCLUDED.candidate_area_ids "
            "THEN internal.r10_unresolved.llm_call_id END, "
            "run_id = EXCLUDED.run_id WHERE internal.r10_unresolved.owner_decision IS NULL"),
            [{"a": u.accident_id, "lr": u.legacy_route_id, "lm": u.legacy_mountain_id, "reason": u.reason,
              "cr": list(u.candidate_route_ids), "ca": list(u.candidate_area_ids), "searched": u.candidates_searched,
              "run": run_id} for u in unresolved])
    return summary


EXPORT_COLUMNS = ["accident_id", "reason", "candidates_searched", "legacy_name", "legacy_grade", "legacy_type"] + [
    f"candidate_{i}_{c}" for i in range(1, MAX_CANDIDATES + 1)
    for c in ("id", "name", "grade", "km", "coord_precision")
] + ["llm_pick", "llm_confidence", "decided_kind", "decided_id"]
EXPORT_SQL = text(
    "SELECT u.accident_id, u.reason, u.candidates_searched, u.candidate_route_ids, u.candidate_area_ids, "
    "coalesce(r.name, m.name) AS legacy_name, r.grade, r.type, coalesce(r.latitude, m.latitude) AS lat, "
    "coalesce(r.longitude, m.longitude) AS lon, c.pick_id, c.pick_none, c.confidence FROM internal.r10_unresolved u "
    "LEFT JOIN internal.r10_llm_calls c ON c.call_id = u.llm_call_id "
    "LEFT JOIN routes r ON r.route_id = u.legacy_route_id LEFT JOIN mountains m ON m.mountain_id = u.legacy_mountain_id "
    "WHERE u.owner_decision IS NULL ORDER BY u.accident_id"
)


async def _describe(conn: AsyncConnection, kind: str,
                    ids: Sequence[int]) -> dict[int, tuple[str, str | None, float, float, str]]:
    if not ids:
        return {}
    sql = (
        "SELECT r.mp_route_id, r.name, r.grade, a.lat, a.lon, a.coord_precision FROM mp_routes r JOIN canonical_routes c "
        "ON c.route_id = r.mp_route_id JOIN canonical_areas a ON a.area_id = c.area_id "
        "WHERE r.mp_route_id = ANY(CAST(:ids AS bigint[]))"
        if kind == "route" else
        "SELECT l.mp_id, l.name, NULL, a.lat, a.lon, a.coord_precision FROM mp_locations l JOIN canonical_areas a ON a.area_id = l.mp_id "
        "WHERE l.mp_id = ANY(CAST(:ids AS bigint[]))"
    )
    rows = (await conn.execute(text(sql), {"ids": list(ids)})).all()
    return {int(i): (n, g, float(la), float(lo), p) for i, n, g, la, lo, p in rows if la is not None and lo is not None}


async def run_r10_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    out: list[dict[str, object]] = []
    for a, reason, searched, cr, ca, name, grade, rtype, lat, lon, pick, pick_none, conf in (
            await conn.execute(EXPORT_SQL)).all():
        kind, ids = ("route", list(cr)) if cr else ("area", list(ca))
        info = await _describe(conn, kind, ids)
        row: dict[str, object] = {"accident_id": a, "reason": reason, "candidates_searched": searched,
                                  "legacy_name": name, "legacy_grade": grade,
                                  "legacy_type": rtype, "llm_pick": "none" if pick_none else pick,
                                  "llm_confidence": conf}
        for i, cid in enumerate(ids, start=1):
            # A candidate whose catalog row changed since r10 ran keeps its id, with the rest blank.
            described = info.get(cid)
            row[f"candidate_{i}_id"] = cid
            if described is None:
                continue
            n, g, la, lo, precision = described
            km = None if lat is None or lon is None else round(haversine_km(float(lat), float(lon), la, lo), 2)
            row |= {f"candidate_{i}_name": n, f"candidate_{i}_grade": g, f"candidate_{i}_km": km,
                    f"candidate_{i}_coord_precision": precision}
        out.append(row)
    review._write(Path(args.out), out, EXPORT_COLUMNS, "owner_decision", overwrite=bool(args.overwrite))
    return {"rows": len(out), "out": args.out}


async def run_r10_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    routes = frozenset(int(r) for (r,) in (await conn.execute(CATALOG_IDS_SQL)).all())
    areas = frozenset(int(a) for (a,) in (await conn.execute(text("SELECT area_id FROM canonical_areas"))).all())
    pending = {int(a) for (a,) in (await conn.execute(text(
        "SELECT accident_id FROM internal.r10_unresolved WHERE owner_decision IS NULL"))).all()}
    decisions: list[dict[str, object]] = []
    seen: set[int] = set()
    with Path(args.review_file).open(newline="", encoding="utf-8-sig") as fh:
        for number, row in enumerate(csv.DictReader(fh), start=2):
            raw_id = (row.get("accident_id") or "").strip()
            if not raw_id.isdecimal() or int(raw_id) not in pending:
                raise ValueError(f"line {number}: accident_id {raw_id!r} is not an undecided R10 row")
            if int(raw_id) in seen:
                raise ValueError(f"line {number}: duplicate accident_id {raw_id}")
            seen.add(int(raw_id))
            decision = (row.get("owner_decision") or "").strip().lower()
            kind = (row.get("decided_kind") or "").strip().lower()
            target = (row.get("decided_id") or "").strip()
            if decision == "no_link":
                decisions.append({"a": int(raw_id), "d": "no_link", "r": None, "ar": None})
                continue
            if decision != "link" or kind not in ("route", "area") or not target.isdecimal():
                raise ValueError(f"line {number}: need owner_decision link with decided_kind route|area and decided_id, "
                                 "or no_link")
            if int(target) not in (routes if kind == "route" else areas):
                raise ValueError(f"line {number}: {kind} {target} is not in the catalog")
            decisions.append({"a": int(raw_id), "d": "link", "r": int(target) if kind == "route" else None,
                              "ar": int(target) if kind == "area" else None})
    counts = dict(Counter(str(d["d"]) for d in decisions))
    if args.apply and decisions:
        updated = {int(a) for (a,) in (await conn.execute(text(
            "UPDATE internal.r10_unresolved u SET owner_decision = d.d, decided_route_id = d.r, decided_area_id = d.ar, "
            "decided_at = now() FROM unnest(CAST(:a AS integer[]), CAST(:d AS text[]), CAST(:r AS bigint[]), "
            "CAST(:ar AS bigint[])) AS d(a, d, r, ar) WHERE u.accident_id = d.a AND u.owner_decision IS NULL "
            "RETURNING u.accident_id"), {k: [d[k] for d in decisions] for k in ("a", "d", "r", "ar")})).all()}
        # The caller's transaction rolls back on this raise, so a partial import never lands.
        missed = sorted(seen - updated)
        if missed:
            raise ValueError(f"accidents {missed} were decided or removed while this import ran; re-export and retry")
    return {"link": counts.get("link", 0), "no_link": counts.get("no_link", 0), "applied": bool(args.apply)}
