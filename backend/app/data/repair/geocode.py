"""R3a (city/park-centroid misgeocodes against GNIS summits) then R3 (fallback clusters,
foreign places, Alaska text below 55°N, per-source precision labels). Also the R3a and R3
hand checks, and the owner review of names that match more than one summit."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import review
from app.data.repair.checks import YES_NO, record_check, refuse_ineligible
from app.data.repair.framework import LOCATION, Change, fetch_accidents, run_step
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.geo import haversine_km
from app.pipelines.gnis import Summit, state_code
from app.pipelines.textsim import normalize_text, place_key
from app.pipelines.validate import ValidationReport, in_us

R3_VERSION = "r3-v1"
R3_REVIEW_VERSION = "r3-review-v1"
MOVE_KM = 50.0
ALASKA_MIN_LAT = 55.0
# Points a spatial rule may use. region_fallback/unknown rows keep their coordinates
# (labelled, never deleted) but those points are known-bad centroids.
TRUSTED_POINT_PRECISIONS = frozenset({"exact", "crag", "area", "park_centroid"})
# Reviewed aliases, keyed by GNIS feature id so a name can only ever reach the one feature
# the evidence is about. Add an entry only with a source for it.
DENALI_GNIS_ID = 1414314
GNIS_ALIASES: dict[int, tuple[str, ...]] = {
    # GNIS DomesticNames lists this AK summit as "Mount McKinley" (2026-09-29 file, Task 6);
    # "Denali" was its official name 2015-2025 and is what most accident records say.
    DENALI_GNIS_ID: ("denali",),
}
# Whole-token matches. Left out on purpose because they are also US summit or place names:
# "peru" (Peru Peak VT), "bolivia", "scotland" (US towns), "matterhorn" (Matterhorn Peak CA,
# the Matterhorn NV/OR), "yukon" (the Yukon River, AK), "ontario", "patagonia", "norway",
# "china". "new mexico" is stripped before matching "mexico".
FOREIGN_TERMS: dict[str, str] = {
    "canada": "CA", "british columbia": "CA", "alberta": "CA", "robson": "CA",
    "squamish": "CA", "bugaboos": "CA", "mexico": "MX", "el potrero chico": "MX", "orizaba": "MX",
    "nepal": "NP", "everest": "NP", "ama dablam": "NP", "pakistan": "PK", "k2": "PK",
    "huascaran": "PE", "argentina": "AR", "aconcagua": "AR", "fitz roy": "AR", "chile": "CL",
    "ecuador": "EC", "chimborazo": "EC", "cotopaxi": "EC", "tibet": "CN", "tanzania": "TZ",
    "kilimanjaro": "TZ", "switzerland": "CH", "chamonix": "FR", "mont blanc": "FR",
    "dolomites": "IT", "new zealand": "NZ", "iceland": "IS", "kyrgyzstan": "KG",
    "elbrus": "RU",
}


class KeepType(Enum):
    KEEP = "keep"


KEEP = KeepType.KEEP


@dataclass(frozen=True)
class GeoRow:
    accident_id: int
    family: SourceFamily
    mountain: str | None
    state: str | None
    lat: float | None
    lon: float | None
    mp_route_id: int | None


@dataclass(frozen=True)
class GeoFix:
    accident_id: int
    location: tuple[float, float] | None | KeepType
    precision: str
    method: str
    country: str | None


class SummitIndex:
    def __init__(self, summits: Sequence[Summit]) -> None:
        self._by_key: dict[str, list[Summit]] = defaultdict(list)
        self.ids: frozenset[int] = frozenset(s.gnis_id for s in summits)
        for s in summits:
            self._by_key[s.name_key].append(s)
        for s in summits:
            for alias in GNIS_ALIASES.get(s.gnis_id, ()):
                if s not in self._by_key[alias]:
                    self._by_key[alias].append(s)

    def lookup(self, key: str, state: str | None) -> list[Summit]:
        hits = self._by_key.get(key, [])
        return [s for s in hits if s.state_code == state] if state else list(hits)


def foreign_country(*texts: str | None) -> str | None:
    haystack = f" {' '.join(normalize_text(t) for t in texts if t)} ".replace(" new mexico ", " ")
    for term, code in FOREIGN_TERMS.items():
        if f" {term} " in haystack:
            return code
    return None


def _point(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 3), round(lon, 3)


def find_fallback_points(rows: Sequence[GeoRow], *, min_rows: int = 5, min_names: int = 3) -> set[tuple[float, float]]:
    names: dict[tuple[float, float], set[str]] = defaultdict(set)
    counts: Counter[tuple[float, float]] = Counter()
    for r in rows:
        if r.lat is None or r.lon is None:
            continue
        p = _point(r.lat, r.lon)
        counts[p] += 1
        names[p].add(place_key(r.mountain))
    return {p for p, n in counts.items() if n >= min_rows and len(names[p]) >= min_names}


def _unique_summit(key: str, state: str | None, index: SummitIndex) -> tuple[list[Summit], str]:
    hits = index.lookup(key, state)
    if hits or state is None:
        return hits, "gnis_summit"
    # The state can come from the same bad geocode ("Mount McKinley" rows marked CA), so
    # an empty state-filtered lookup falls back to a nationwide match, but only a unique one.
    national = index.lookup(key, None)
    return (national, "gnis_summit_state_mismatch") if len(national) == 1 else ([], "gnis_summit")


def _moveable(row: GeoRow) -> bool:
    # Only geocoded points move on name evidence; an avalanche-centre or route-linked point
    # was measured or linked by the source and outranks a summit name match.
    return row.family != "avalanche" and row.mp_route_id is None


def _r3a(row: GeoRow, index: SummitIndex, state: str | None) -> GeoFix | None:
    key = place_key(row.mountain)
    if not key or row.lat is None or row.lon is None or not _moveable(row):
        return None
    hits, method = _unique_summit(key, state, index)
    if len(hits) == 1:
        s = hits[0]
        if haversine_km(row.lat, row.lon, s.lat, s.lon) > MOVE_KM:
            return GeoFix(row.accident_id, (s.lat, s.lon), "area", method, "US")
        return None
    if not hits and state is not None:
        # The named summit exists only in other states (several), so either the state or the
        # point is wrong and nothing says which: untrusted, kept, owner review.
        elsewhere = index.lookup(key, None)
        if elsewhere and all(haversine_km(row.lat, row.lon, s.lat, s.lon) > MOVE_KM for s in elsewhere):
            return GeoFix(row.accident_id, KEEP, "unknown", "gnis_state_unmatched", "US")
        return None
    if len(hits) > 1 and all(haversine_km(row.lat, row.lon, s.lat, s.lon) > MOVE_KM for s in hits):
        # Being near one candidate settles it; otherwise no candidate is picked (owner review).
        # Spec R3a nulls the state-less case; with a known state the point is kept, labelled.
        if state is None:
            return GeoFix(row.accident_id, None, "unknown", "gnis_ambiguous", None)
        return GeoFix(row.accident_id, KEEP, "unknown", "gnis_ambiguous", "US")
    return None


def geocode_fix(row: GeoRow, index: SummitIndex, fallback: set[tuple[float, float]]) -> GeoFix:
    state = state_code(row.state)
    foreign = foreign_country(row.mountain, row.state)
    if foreign is not None:
        return GeoFix(row.accident_id, None, "unknown", "foreign_place", foreign)
    moved = _r3a(row, index, state)
    if moved is not None:
        return moved
    lat, lon = row.lat, row.lon
    if lat is None or lon is None:
        return GeoFix(row.accident_id, KEEP, "unknown", "no_coords", "US" if state else None)
    if _point(lat, lon) in fallback or (state == "AK" and lat < ALASKA_MIN_LAT):
        hits, method = _unique_summit(place_key(row.mountain), state, index) if state else ([], "gnis_summit")
        if len(hits) == 1 and _moveable(row):
            return GeoFix(row.accident_id, (hits[0].lat, hits[0].lon), "area", method, "US")
        # Coordinates stay (D10 principle); the label keeps every spatial rule off them.
        if state is not None:
            return GeoFix(row.accident_id, KEEP, "region_fallback", "fallback_state", "US")
        return GeoFix(row.accident_id, KEEP, "unknown", "fallback_no_state", None)
    if not in_us(lat, lon):
        return GeoFix(row.accident_id, None, "unknown", "outside_us", None)
    if row.family == "nps":
        return GeoFix(row.accident_id, KEEP, "park_centroid", "nps_source", "US")
    if row.mp_route_id is not None:
        return GeoFix(row.accident_id, KEEP, "crag", "route_link", "US")
    if row.family == "avalanche":
        return GeoFix(row.accident_id, KEEP, "exact", "source_coords", "US")
    return GeoFix(row.accident_id, KEEP, "area", "source_geocode", "US")


def changes_for(fix: GeoFix) -> list[Change]:
    changes: list[Change] = []
    if not isinstance(fix.location, KeepType):
        changes.append(Change(fix.accident_id, LOCATION, fix.location, fix.method))
    changes += [
        Change(fix.accident_id, "geocode_precision", fix.precision, fix.method),
        Change(fix.accident_id, "geocode_method", fix.method, fix.method),
        Change(fix.accident_id, "country", fix.country, fix.method),
    ]
    return changes


NOT_IN_SNAPSHOT = "not_in_snapshot"
SNAPSHOT_POINT = "snapshot_point"


def snapshot_fixes(
    raw: Mapping[int, GeoRow], live: Mapping[int, tuple[float | None, float | None]], index: SummitIndex
) -> tuple[list[GeoFix], list[Change]]:
    """Decisions come from the pre-repair snapshot only (G17 principle, as R1): a hand edit to
    a live row, or R3's own earlier moves and nulls, never feed a later run. A kept point is
    the snapshot point, so a live point that drifted from it is put back (audited) rather
    than left under a label that describes a different point. Rows absent from the snapshot
    are labelled untrusted and never moved."""
    fallback = find_fallback_points(list(raw.values()))
    fixes: list[GeoFix] = []
    changes: list[Change] = []
    for accident_id, (lat, lon) in live.items():
        row = raw.get(accident_id)
        if row is None:
            fix = GeoFix(accident_id, KEEP, "unknown", NOT_IN_SNAPSHOT, None)
        else:
            fix = geocode_fix(row, index, fallback)
            if fix.location is KEEP and row.lat is not None and row.lon is not None and (lat, lon) != (row.lat, row.lon):
                changes.append(Change(accident_id, LOCATION, (row.lat, row.lon), SNAPSHOT_POINT))
        fixes.append(fix)
        changes += changes_for(fix)
    return fixes, changes


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _float(value: object) -> float | None:
    return float(str(value)) if value is not None else None


def _geo_row(r: Mapping[str, object]) -> GeoRow:
    return GeoRow(
        int(str(r["accident_id"])),
        source_family(_str(r["source"])),
        _str(r["mountain"]),
        _str(r["state"]),
        _float(r["latitude"]),
        _float(r["longitude"]),
        int(str(r["mp_route_id"])) if r["mp_route_id"] is not None else None,
    )


async def _index(conn: AsyncConnection) -> SummitIndex:
    result = await conn.execute(text("SELECT gnis_id, name, name_key, state_code, lat, lon FROM gnis_summits"))
    summits = [Summit(int(g), str(n), str(k), str(s), float(la), float(lo)) for g, n, k, s, la, lo in result.all()]
    if not summits:
        raise SystemExit("gnis_summits is empty: run python -m app.pipelines.gnis first")
    return SummitIndex(summits)


async def raw_geo_rows(conn: AsyncConnection) -> dict[int, GeoRow]:
    result = await conn.execute(
        text(
            "SELECT accident_id, source, mountain, state, latitude, longitude, mp_route_id "
            "FROM internal.accidents_raw ORDER BY accident_id"
        )
    )
    rows = {int(r.accident_id): _geo_row(dict(r._mapping)) for r in result}
    if not rows:
        raise SystemExit("internal.accidents_raw is empty: 0004 snapshots accidents before any repair")
    return rows


async def run_r3_clusters(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    """Coordinates and counts only (no names), for the owner's cluster review before r3 --apply."""
    rows = list((await raw_geo_rows(conn)).values())
    points = find_fallback_points(rows)
    sizes = Counter(_point(r.lat, r.lon) for r in rows if r.lat is not None and r.lon is not None)
    by_point = {f"{lat},{lon}": sizes[(lat, lon)] for lat, lon in sorted(points)}
    return {"fallback_points": len(points), "rows_at_fallback_points": sum(by_point.values()), "points": by_point}


async def run_r3(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    raw = await raw_geo_rows(conn)
    index = await _index(conn)
    live = {_id(r): (_float(r["latitude"]), _float(r["longitude"])) for r in await fetch_accidents(conn, ["latitude", "longitude"])}
    fixes, changes = snapshot_fixes(raw, live, index)
    report = ValidationReport("repair:r3")
    methods: Counter[str] = Counter()
    for fix in fixes:
        methods[fix.method] += 1
        if fix.precision in TRUSTED_POINT_PRECISIONS:
            report.accept()
        else:
            report.quarantine(str(fix.accident_id), fix.method)
    summary = await run_step(conn, step="r3", rule_version=R3_VERSION, changes=changes, report=report, apply=args.apply)
    # CLI output only. A missing alias feature means "Denali" rows silently stop resolving.
    missing = sorted(set(GNIS_ALIASES) - index.ids)
    return summary | {"methods": dict(sorted(methods.items())), "missing_alias_ids": missing}


def plot_stratum(row: Mapping[str, object]) -> str:
    method = str(row.get("geocode_method") or "")
    if method.startswith("gnis_summit"):
        return "gnis_move"
    return str(row.get("geocode_precision") or "unknown")


PLOT_QUOTAS = {"gnis_move": 10, "region_fallback": 10, "unknown": 5, "area": 10, "crag": 5, "exact": 5, "park_centroid": 5}
# The precision label is shown on purpose: the plot check judges whether the label fits the
# point (a region_fallback point should NOT sit on the named place), not the point alone.
CHECK_COLUMNS = ["accident_id", "source_id", "location", "mountain", "state", "latitude", "longitude", "geocode_precision"]


def _out(args: argparse.Namespace, name: str) -> Path:
    # Resolved per call, so the default follows review.REVIEW_DIR wherever the CLI runs from.
    return Path(args.out) if args.out else review.REVIEW_DIR / name


async def _checked_rows(conn: AsyncConnection) -> list[dict[str, object]]:
    return await fetch_accidents(
        conn, ["source_id", "location", "mountain", "state", "latitude", "longitude", "geocode_precision", "geocode_method"]
    )


def _id(row: Mapping[str, object]) -> int:
    return int(str(row["accident_id"]))


async def r3a_check_pool(conn: AsyncConnection) -> list[dict[str, object]]:
    return [r for r in await _checked_rows(conn) if str(r["geocode_method"] or "").startswith("gnis_summit")]


async def run_r3a_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    """Every gnis_summit* move, not a sample: a wrong move relocates an accident, so each one is checked."""
    moved = await r3a_check_pool(conn)
    summits = {
        (float(la), float(lo)): f"{n} ({s}, GNIS {g})"
        for g, n, s, la, lo in (await conn.execute(text("SELECT gnis_id, name, state_code, lat, lon FROM gnis_summits"))).all()
    }
    previous = {
        int(a): str(old)
        for a, old in (
            await conn.execute(
                text("SELECT accident_id, old_value FROM internal.accident_revisions WHERE field = :f AND rule_version = :v"),
                {"f": LOCATION, "v": R3_VERSION},
            )
        ).all()
    }
    rows = [
        r | {
            "summit": summits.get((float(str(r["latitude"])), float(str(r["longitude"]))), ""),
            "previous_location": previous.get(_id(r), ""),
        }
        for r in moved
    ]
    out = _out(args, "r3a_moves.csv")
    review._write(out, rows, [*CHECK_COLUMNS, "summit", "previous_location"], "correct", overwrite=args.overwrite)
    return {
        "exported": len(rows),
        "path": str(out),
        "instructions": "every row: is the new point the summit the record names? correct=yes|no",
    }


async def run_r3a_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    decisions = review.read_decisions(Path(args.review_file), key="accident_id", decision_column="correct", allowed=YES_NO)
    refuse_ineligible(decisions, {_id(r) for r in await r3a_check_pool(conn)})
    return await record_check(conn, "repair:r3a-check", decisions)


async def r3_plot_pool(conn: AsyncConnection) -> list[dict[str, object]]:
    return [r for r in await _checked_rows(conn) if r["latitude"] is not None and r["longitude"] is not None]


async def run_r3_plot_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows = await r3_plot_pool(conn)
    out = _out(args, "r3_spot_check.csv")
    geojson = out.with_suffix(".geojson")
    # The CSV refuses on its own; the GeoJSON is checked first so neither half is replaced alone.
    if geojson.exists() and not args.overwrite:
        raise FileExistsError(f"{geojson.name} already exists; pass --overwrite to replace it")
    taken = review.export_stratified(
        rows, stratum=plot_stratum, quotas=PLOT_QUOTAS, seed=3, path=out, columns=CHECK_COLUMNS,
        decision_column="plausible", overwrite=args.overwrite,
    )
    by_id = {_id(r): r for r in rows}
    with out.open(newline="", encoding="utf-8") as fh:
        chosen = [by_id[int(r["accident_id"])] for r in csv.DictReader(fh)]
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [_float(r["longitude"]), _float(r["latitude"])]},
            "properties": {k: r[k] for k in ("accident_id", "source_id", "mountain", "state", "geocode_precision")},
        }
        for r in chosen
    ]
    geojson.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return {
        "strata": taken,
        "csv": str(out),
        "geojson": str(geojson),
        "instructions": (
            "open the GeoJSON in a local GIS (QGIS), never an online viewer; plausible=yes if the point fits its "
            "precision label (exact/crag/area/park_centroid: at or near the named place; region_fallback/unknown: "
            "a centroid that is not the place), else no"
        ),
    }


async def run_r3_plot_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    decisions = review.read_decisions(Path(args.review_file), key="accident_id", decision_column="plausible", allowed=YES_NO)
    refuse_ineligible(decisions, {_id(r) for r in await r3_plot_pool(conn)})
    return await record_check(conn, "repair:r3-plot-check", decisions)


def review_candidates(row: GeoRow, index: SummitIndex) -> list[Summit]:
    """The summits that put a row in review: in its state, else nationwide (no state, or the
    name exists only in other states)."""
    key, state = place_key(row.mountain), state_code(row.state)
    hits = index.lookup(key, state) or (index.lookup(key, None) if state else [])
    return sorted(hits, key=lambda s: s.gnis_id)


def owner_review_changes(accident_id: int, choice: str, candidates: Sequence[Summit]) -> list[Change]:
    if choice == "none":
        return []
    picked = [s for s in candidates if str(s.gnis_id) == choice]
    if not picked:
        raise ValueError(f"accident {accident_id}: gnis_id {choice} is not a candidate for this row")
    s = picked[0]
    return changes_for(GeoFix(accident_id, (s.lat, s.lon), "area", "owner_summit", "US"))


MAX_LISTED_CANDIDATES = 30


REVIEW_METHODS = frozenset({"gnis_ambiguous", "gnis_state_unmatched"})


async def _ambiguous_pool(conn: AsyncConnection) -> tuple[list[dict[str, object]], dict[int, list[Summit]]]:
    rows = await fetch_accidents(conn, ["source_id", "location", "mountain", "state", "latitude", "longitude", "geocode_method"])
    raw, index = await raw_geo_rows(conn), await _index(conn)
    pool = [r for r in rows if r["geocode_method"] in REVIEW_METHODS and _id(r) in raw]
    # Candidates from the snapshot fields R3 decided on, not from the live row.
    return pool, {_id(r): review_candidates(raw[_id(r)], index) for r in pool}


async def run_r3_ambiguous_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    pool, by_id = await _ambiguous_pool(conn)
    rows = []
    for r in pool:
        candidates = by_id[_id(r)]
        listed = "; ".join(f"{s.gnis_id} {s.name} {s.state_code} {s.lat},{s.lon}" for s in candidates[:MAX_LISTED_CANDIDATES])
        if len(candidates) > MAX_LISTED_CANDIDATES:
            listed += f"; (+{len(candidates) - MAX_LISTED_CANDIDATES} more in gnis_summits)"
        rows.append(r | {"candidates": listed})
    out = _out(args, "r3_ambiguous.csv")
    columns = ["accident_id", "source_id", "location", "mountain", "state", "latitude", "longitude", "candidates"]
    review._write(out, rows, columns, "gnis_id", overwrite=args.overwrite)
    return {
        "exported": len(rows),
        "path": str(out),
        "instructions": (
            "read each report; gnis_id = the candidate summit it names (state and point may both be wrong), "
            "or none if the report does not settle it"
        ),
    }


async def run_r3_ambiguous_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    _, candidates = await _ambiguous_pool(conn)
    allowed = frozenset({"none"} | {str(s.gnis_id) for cs in candidates.values() for s in cs})
    path = Path(args.review_file)
    try:
        decisions = review.read_decisions(path, key="accident_id", decision_column="gnis_id", allowed=allowed)
    except ValueError as exc:
        raise ValueError(f"{exc} (gnis_id is not a candidate, or the row is not pending review)") from exc
    refuse_ineligible(decisions, set(candidates))
    report = ValidationReport("repair:r3-review")
    changes: list[Change] = []
    for accident_id, choice in decisions.items():
        row_changes = owner_review_changes(accident_id, choice, candidates[accident_id])
        if row_changes:
            report.accept()
        else:
            report.quarantine(str(accident_id), "owner_none")
        changes += row_changes
    return await run_step(
        conn, step="r3-review", rule_version=R3_REVIEW_VERSION, changes=changes, report=report, apply=args.apply
    )
