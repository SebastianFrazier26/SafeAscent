"""R3a (city/park-centroid misgeocodes against GNIS summits) then R3 (fallback clusters,
foreign places, Alaska text below 55°N, points in another state than the row's, per-source
precision labels). Also the R3a and R3 hand checks, and the owner review of names that match
more than one summit."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import review
from app.data.repair.checks import YES_NO, agreement, record_check, refuse_ineligible
from app.data.repair.framework import LOCATION, Change, fetch_accidents, run_step
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.geo import haversine_km
from app.pipelines.gnis import Summit, state_code
from app.pipelines.textsim import normalize_text, place_key
from app.pipelines.validate import ValidationReport, in_us

R3_VERSION = "r3-v5"
# Owner decisions. Every r3-review-* revision freezes the row against later R3 runs.
R3_REVIEW_VERSION = "r3-review-v1"
R3_PICK_VERSION = "r3-review-pick-v1"
MOVE_KM = 50.0
# A candidate fallback point this close to a GNIS summit is that summit, not a state or park
# centroid: popular summits collect many rows under several route names (owner, 2026-11-16).
SUMMIT_EXEMPT_KM = 0.25
# MP crag exemption (owner, 2026-11-16; r3-v3). A candidate point that survives the summit check
# is a crag, not a centroid, when an MP area this close, small enough to be one crag rather than
# a whole mountain or park, shares a distinctive name key with most of the point's rows. Fitted
# on the p2a-2 rehearsal (17 points, 157 rows): 2 km admitted town and whole-mountain points,
# 600 m lost real crags.
MP_CRAG_EXEMPT_KM = 1.0
MP_CRAG_MAX_ROUTES = 400
# A key in more MP area names than this is a park or region word, not one crag's name.
MP_KEY_MAX_AREAS = 25
MP_KEY_MIN_SHARE = 0.6
MP_KEY_MIN_ROWS = 3
# The whole cleaned area name is a key too, when at least this long: some crag names are made
# only of common words.
MP_PHRASE_MIN_CHARS = 5
# Cycle guard on the parent chain, as in the rehearsal analysis.
MP_TREE_MAX_DEPTH = 20
# State contradiction (owner, 2026-11-16; r3-v4): a point's state is its nearest GNIS summit's
# within this radius. On the p2a-2 rehearsal every one of the 42 fallback points has a summit
# within 23.4 km, and the summit's state matches the place-first hand check's on every point with a
# stated row. Farther than this, the point's state is undetermined and nothing changes. r3-v5
# (owner, 2026-11-17) checks every stated row's point, not only fallback points, with a border
# guard: a row whose own state also has a summit within this radius is not contradicted. On the
# rehearsal the wider check adds 150 rows; the guard keeps 13 of them, each with a summit of the
# other state 1-8 km away and one of its own 1.5-23 km away.
POINT_STATE_KM = 25.0
STATE_CONTRADICTION = "fallback_state_contradiction"
ALASKA_MIN_LAT = 55.0
# The Aleutians and the western Alaska Peninsula lie south of 55N at these longitudes; the
# 55N rule is for AK-labelled rows geocoded into the lower 48 or Hawaii. The latitude floor
# keeps Kauai and Niihau (lon -159..-160) out of the exemption.
ALEUTIAN_MIN_LAT = 50.0
ALEUTIAN_MAX_LON = -158.0
ALEUTIAN_MIN_LON = 172.0
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
_PROVINCES = (
    "alberta ab", "british columbia bc", "manitoba mb", "new brunswick nb", "newfoundland and labrador nl",
    "newfoundland nl", "nova scotia ns", "northwest territories nt", "nunavut nu", "ontario on",
    "prince edward island pe", "quebec qc", "saskatchewan sk", "yukon yt", "yukon territory yt",
)
_MEXICAN_STATES = (
    "aguascalientes", "baja california", "baja california sur", "campeche", "chiapas", "chihuahua",
    "coahuila", "colima", "durango", "guanajuato", "guerrero", "hidalgo", "jalisco", "estado de mexico",
    "mexico city", "ciudad de mexico", "michoacan", "morelos", "nayarit", "nuevo leon", "oaxaca", "puebla",
    "queretaro", "quintana roo", "san luis potosi", "sinaloa", "sonora", "tabasco", "tamaulipas", "tlaxcala",
    "veracruz", "yucatan", "zacatecas",
)
# Country names and foreign peak or place names that occur as a whole state value. Only the
# entire folded state field is compared, never substrings and never mountain text (owner,
# 2026-11-16): a US-state row whose mountain text names a Canadian province (e.g. a Colorado slide path) is not foreign.
_COUNTRY_NAMES = {
    "mexico": "MX", "nepal": "NP", "pakistan": "PK", "argentina": "AR", "chile": "CL", "ecuador": "EC",
    "tibet": "CN", "china": "CN", "tanzania": "TZ", "switzerland": "CH", "new zealand": "NZ",
    "iceland": "IS", "kyrgyzstan": "KG", "peru": "PE", "france": "FR", "italy": "IT", "russia": "RU",
    "austria": "AT", "bolivia": "BO", "india": "IN", "japan": "JP", "united kingdom": "GB",
    "scotland": "GB", "norway": "NO",
}
_PLACE_NAMES = {
    "robson": "CA", "squamish": "CA", "bugaboos": "CA", "potrero chico": "MX", "orizaba": "MX",
    "everest": "NP", "ama dablam": "NP", "k2": "PK", "huascaran": "PE", "aconcagua": "AR",
    "fitz roy": "AR", "chimborazo": "EC", "cotopaxi": "EC", "kilimanjaro": "TZ", "chamonix": "FR",
    "mont blanc": "FR", "dolomites": "IT", "elbrus": "RU",
}
# The whole state field, folded. Canadian two-letter codes never collide with US ones; Mexican
# abbreviations do (CO, MO ...), so only full Mexican state names are listed.
STATE_FOREIGN: dict[str, str] = (
    {p.rsplit(" ", 1)[0]: "CA" for p in _PROVINCES}
    | {p.rsplit(" ", 1)[1]: "CA" for p in _PROVINCES}
    | {"canada": "CA"}
    | {m: "MX" for m in _MEXICAN_STATES}
    | _COUNTRY_NAMES
    | _PLACE_NAMES
)


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
    # r3-v5: a contradicted route-linked row's point came in through the link, so the link goes too.
    clear_route_link: bool = False


class SummitIndex:
    def __init__(self, summits: Sequence[Summit]) -> None:
        self._by_key: dict[str, list[Summit]] = defaultdict(list)
        self.ids: frozenset[int] = frozenset(s.gnis_id for s in summits)
        # Sorted by latitude once, so a radius query scans one latitude band, not every summit:
        # r3-v5 asks for every stated row's point, not a handful of candidates.
        self._by_lat: tuple[Summit, ...] = tuple(sorted(summits, key=lambda s: s.lat))
        self._lats: tuple[float, ...] = tuple(s.lat for s in self._by_lat)
        for s in summits:
            self._by_key[s.name_key].append(s)
        for s in summits:
            for alias in GNIS_ALIASES.get(s.gnis_id, ()):
                if s not in self._by_key[alias]:
                    self._by_key[alias].append(s)

    def summits_within(self, lat: float, lon: float, km: float) -> list[tuple[Summit, float]]:
        band = km / 111.0
        lo, hi = bisect.bisect_left(self._lats, lat - band), bisect.bisect_right(self._lats, lat + band)
        near = ((s, haversine_km(lat, lon, s.lat, s.lon)) for s in self._by_lat[lo:hi])
        return [(s, d) for s, d in near if d <= km]

    def nearest_summit_within(self, lat: float, lon: float, km: float) -> tuple[Summit, float] | None:
        return min(self.summits_within(lat, lon, km), key=lambda sd: (sd[1], sd[0].gnis_id), default=None)

    def lookup(self, key: str, state: str | None) -> list[Summit]:
        hits = self._by_key.get(key, [])
        return [s for s in hits if s.state_code == state] if state else list(hits)


@dataclass(frozen=True)
class MpArea:
    mp_id: int
    name: str
    lat: float | None
    lon: float | None
    # Routes under the area, its whole subtree included.
    routes: int


@dataclass(frozen=True)
class CragMatch:
    area: MpArea
    distance_km: float
    key: str
    rows: int
    share: float


_CRAG_STOP = frozenset(
    "mount mountain mt the of and a an peak point national park state area via route rock to in on at wall crag cliff "
    "cliffs".split()
)
_MP_STATS_SUFFIX = re.compile(r"[\d,]+/[\d,/]+.*$")
# A sort prefix is digits ("05. ", "07a.") or one capital letter and a space ("B. "). The rehearsal
# analysis also took any 1-2 letters ("St. ", "Mt. "), which ate "St." from "St. Vrain"; not intended.
_MP_SORT_PREFIX = re.compile(r"^[\W_]*(?:\d+[A-Za-z]?\.\s*|[A-Z]\.\s+)")
_ALNUM = re.compile(r"[a-z0-9]+")


def crag_norm(value: str | None) -> str:
    # Deliberately not textsim.normalize_text: no abbreviation expansion, so the keys are the ones
    # the owner-approved counts were measured with. Accents fold as in _fold ("Pêche" -> "peche").
    decomposed = unicodedata.normalize("NFKD", value or "")
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(_ALNUM.findall(folded.lower().replace("'", "")))


def crag_tokens(value: str | None) -> frozenset[str]:
    return frozenset(t for t in crag_norm(value).split() if t not in _CRAG_STOP and len(t) > 1)


def clean_mp_name(name: str | None) -> str:
    """An MP area name without its sort prefix ("B. ", "05. ", "*") or a stats suffix."""
    return crag_norm(_MP_SORT_PREFIX.sub("", _MP_STATS_SUFFIX.sub("", name or "")))


def subtree_route_counts(parents: Mapping[int, int | None], direct: Mapping[int, int]) -> Counter[int]:
    counts: Counter[int] = Counter()
    for location, n in direct.items():
        node: int | None = location
        for _ in range(MP_TREE_MAX_DEPTH):
            if node is None:
                break
            counts[node] += n
            node = parents.get(node)
    return counts


class MpAreaIndex:
    def __init__(self, areas: Sequence[MpArea]) -> None:
        # Key frequency counts every area, located or not: a word is common whether or not the
        # areas carrying it have coordinates.
        self._cleaned = {a.mp_id: clean_mp_name(a.name) for a in areas}
        self._token_areas: Counter[str] = Counter(t for c in self._cleaned.values() for t in crag_tokens(c))
        self._padded = tuple(f" {c} " for c in self._cleaned.values())
        self._phrase_areas: dict[str, int] = {}
        self._crags = tuple(
            sorted(
                (a for a in areas if a.lat is not None and a.lon is not None and 0 < a.routes <= MP_CRAG_MAX_ROUTES),
                key=lambda a: a.mp_id,
            )
        )
        self.crag_areas = len(self._crags)

    def _areas_with_phrase(self, phrase: str) -> int:
        # Always the full scan, one word or several: _token_areas skips stop words, so a one-word
        # phrase such as "cliffs" would otherwise count 0 areas and pass the cap.
        if phrase not in self._phrase_areas:
            self._phrase_areas[phrase] = sum(1 for c in self._padded if f" {phrase} " in c)
        return self._phrase_areas[phrase]

    def _keys(self, area: MpArea) -> list[str]:
        cleaned = self._cleaned[area.mp_id]
        keys = {t for t in crag_tokens(cleaned) if self._token_areas[t] <= MP_KEY_MAX_AREAS}
        if (
            len(cleaned) >= MP_PHRASE_MIN_CHARS
            and crag_tokens(cleaned)
            and self._areas_with_phrase(cleaned) <= MP_KEY_MAX_AREAS
        ):
            keys.add(cleaned)
        return sorted(keys)

    def crag_match(self, lat: float, lon: float, mountains: Sequence[str | None]) -> CragMatch | None:
        """The best qualifying MP area for a point's rows (most rows sharing one key, then the
        nearest area), or None when no area meets the exemption."""
        if not mountains:
            return None
        padded = [f" {crag_norm(m)} " for m in mountains]
        band = MP_CRAG_EXEMPT_KM / 111.0
        near = []
        for a in self._crags:
            assert a.lat is not None and a.lon is not None
            if abs(a.lat - lat) > band:
                continue
            d = haversine_km(lat, lon, a.lat, a.lon)
            if d <= MP_CRAG_EXEMPT_KM:
                near.append((d, a))
        best: tuple[int, MpArea, float, str] | None = None
        for d, a in sorted(near, key=lambda x: (x[0], x[1].mp_id)):
            for key in self._keys(a):
                # Word-bounded: a token is one whole word of the row's name, a phrase a run of them.
                k = sum(1 for p in padded if f" {key} " in p)
                if best is None or k > best[0]:
                    best = (k, a, d, key)
        if best is None:
            return None
        k, area, d, key = best
        share = k / len(mountains)
        if k < MP_KEY_MIN_ROWS or share < MP_KEY_MIN_SHARE:
            return None
        return CragMatch(area, d, key, k, share)


def _fold(value: str | None) -> str:
    # "Querétaro" and "Cañada" fold to plain ASCII before matching, not to "quer taro".
    decomposed = unicodedata.normalize("NFKD", value or "")
    return normalize_text("".join(c for c in decomposed if not unicodedata.combining(c)))


_COUNTRY_SUFFIXES = {"canada": "CA", "mexico": "MX"}


def foreign_state(state: str | None) -> str | None:
    """The whole state field only, never a substring ("Durango, CO", "Ontario, OR" are US);
    "<province or Mexican state>, <its country>" is the one compound form accepted."""
    folded = _fold(state)
    if folded in STATE_FOREIGN:
        return STATE_FOREIGN[folded]
    head, _, country = folded.rpartition(" ")
    code = _COUNTRY_SUFFIXES.get(country)
    return code if code is not None and STATE_FOREIGN.get(head) == code else None


def classify_state(state: str | None) -> str:
    """Pre-check class of a raw state value: blank, us:XX, foreign:XX, or unmapped."""
    if not (state or "").strip():
        return "blank"
    code = state_code(state)
    if code is not None:
        return f"us:{code}"
    foreign = foreign_state(state)
    return f"foreign:{foreign}" if foreign else "unmapped"


def _point(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 3), round(lon, 3)


Point = tuple[float, float]


@dataclass(frozen=True)
class FallbackSplit:
    fallback: set[Point]
    on_summit: set[Point]
    on_crag: dict[Point, CragMatch]


def split_fallback_points(
    rows: Sequence[GeoRow], index: SummitIndex, mp_index: MpAreaIndex, *, min_rows: int = 5, min_names: int = 3
) -> FallbackSplit:
    """Candidate points split into fallbacks and the ones each exemption took out. The summit
    check runs first, so a point on a summit is never also listed as a crag."""
    names: dict[Point, set[str]] = defaultdict(set)
    mountains: dict[Point, list[str | None]] = defaultdict(list)
    for r in rows:
        if r.lat is None or r.lon is None:
            continue
        p = _point(r.lat, r.lon)
        mountains[p].append(r.mountain)
        key = place_key(r.mountain)
        if key:
            names[p].add(key)
    candidates = {p for p, ms in mountains.items() if len(ms) >= min_rows and len(names[p]) >= min_names}
    on_summit = {p for p in candidates if index.nearest_summit_within(p[0], p[1], SUMMIT_EXEMPT_KM) is not None}
    on_crag: dict[Point, CragMatch] = {}
    for p in sorted(candidates - on_summit):
        match = mp_index.crag_match(p[0], p[1], mountains[p])
        if match is not None:
            on_crag[p] = match
    return FallbackSplit(candidates - on_summit - set(on_crag), on_summit, on_crag)


def find_fallback_points(
    rows: Sequence[GeoRow], index: SummitIndex, mp_index: MpAreaIndex, *, min_rows: int = 5, min_names: int = 3
) -> set[Point]:
    return split_fallback_points(rows, index, mp_index, min_rows=min_rows, min_names=min_names).fallback


@dataclass(frozen=True)
class PointState:
    # The nearest summit's state, and every state with a summit within POINT_STATE_KM.
    nearest: str
    states: frozenset[str]

    def contradicts(self, state: str | None) -> bool:
        return state is not None and state not in self.states


def point_states(points: Iterable[Point], index: SummitIndex) -> dict[Point, PointState]:
    """The state of each point with a GNIS summit within POINT_STATE_KM; undetermined points are absent."""
    states: dict[Point, PointState] = {}
    for p in set(points):
        near = index.summits_within(p[0], p[1], POINT_STATE_KM)
        if near:
            nearest = min(near, key=lambda sd: (sd[1], sd[0].gnis_id))[0]
            states[p] = PointState(nearest.state_code, frozenset(s.state_code for s, _ in near))
    return states


def stated_points(rows: Iterable[GeoRow]) -> set[Point]:
    """The points r3-v5 checks: every row with coordinates and a US state."""
    return {_point(r.lat, r.lon) for r in rows if r.lat is not None and r.lon is not None and state_code(r.state)}


def alaska_point_misplaced(lat: float, lon: float) -> bool:
    """An AK-labelled point that cannot be in Alaska (shared with the acceptance cell)."""
    aleutian = lat >= ALEUTIAN_MIN_LAT and (lon <= ALEUTIAN_MAX_LON or lon >= ALEUTIAN_MIN_LON)
    return lat < ALASKA_MIN_LAT and not aleutian


def _moveable(row: GeoRow) -> bool:
    # Only geocoded points move on name evidence; an avalanche-centre or route-linked point
    # was measured or linked by the source and outranks a summit name match.
    return row.family != "avalanche" and row.mp_route_id is None


def _unmapped_state(row: GeoRow, state: str | None) -> bool:
    return state is None and bool((row.state or "").strip())


def _r3a(row: GeoRow, index: SummitIndex, state: str | None) -> GeoFix | None:
    key = place_key(row.mountain)
    # A state field that is neither blank nor a US state cannot scope a name match.
    if not key or row.lat is None or row.lon is None or not _moveable(row) or _unmapped_state(row, state):
        return None
    hits = index.lookup(key, state)
    if len(hits) == 1:
        s = hits[0]
        if haversine_km(row.lat, row.lon, s.lat, s.lon) > MOVE_KM:
            return GeoFix(row.accident_id, (s.lat, s.lon), "area", "gnis_summit", "US")
        return None
    if not hits and state is not None:
        # The named summit exists only in other states (one or several), so the state or the
        # point is wrong and nothing says which: untrusted, kept, owner review. Never a move.
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


def geocode_fix(
    row: GeoRow, index: SummitIndex, fallback: set[tuple[float, float]],
    states: Mapping[Point, PointState] | None = None,
) -> GeoFix:
    state = state_code(row.state)
    # The state field alone decides foreign, matched whole (N3); mountain text never does.
    foreign = None if state else foreign_state(row.state)
    if foreign is not None:
        return GeoFix(row.accident_id, None, "unknown", "foreign_place", foreign)
    lat, lon = row.lat, row.lon
    # Every stated row's point (r3-v5). The rule is about the point, so a route-linked or avalanche
    # row is no exception; PointState's border guard keeps a point near a state line.
    point_state = (states or {}).get(_point(lat, lon)) if lat is not None and lon is not None else None
    contradicted = point_state is not None and point_state.contradicts(state)
    moved = _r3a(row, index, state)
    # A contradiction outranks R3a's kept-point labels (owner, 2026-11-16): they vouch for no point.
    # An R3a move goes to a summit in the row's own state, so it stands.
    if moved is not None and not (contradicted and moved.location is KEEP):
        return moved
    if lat is None or lon is None:
        return GeoFix(row.accident_id, KEEP, "unknown", "no_coords", "US" if state else None)
    misplaced = _point(lat, lon) in fallback or (state == "AK" and alaska_point_misplaced(lat, lon))
    if misplaced or contradicted:
        hits = index.lookup(place_key(row.mountain), state) if state else []
        if len(hits) == 1 and _moveable(row):
            return GeoFix(row.accident_id, (hits[0].lat, hits[0].lon), "area", "gnis_summit", "US")
    if contradicted:
        return GeoFix(row.accident_id, None, "unknown", STATE_CONTRADICTION, "US", row.mp_route_id is not None)
    if misplaced:
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
    if fix.clear_route_link:
        changes.append(Change(fix.accident_id, "mp_route_id", None, fix.method))
    return changes


NOT_IN_SNAPSHOT = "not_in_snapshot"
SNAPSHOT_POINT = "snapshot_point"
MOVED_BY_OTHER_RULE = "moved_by_other_rule"
LINK_RESTORED = "route_link_restored"
LiveRow = tuple[float | None, float | None, str | None]
# Per accident, (field, rule_version) in revision order (oldest first).
History = Mapping[int, Sequence[tuple[str, str]]]


def _owner_decided(history: Sequence[tuple[str, str]]) -> bool:
    return any(version.startswith("r3-review-") for _, version in history)


def _labelled_elsewhere(precision: str | None, history: Sequence[tuple[str, str]]) -> bool:
    # Whoever set the label last owns it: an R3 label later relabelled by another rule is
    # that rule's, not R3's to overwrite.
    last = next((version for field, version in reversed(history) if field == "geocode_precision"), None)
    return precision is not None and (last is None or not last.startswith("r3-v"))


def _r3_touched_link(history: Sequence[tuple[str, str]]) -> bool:
    return any(field == "mp_route_id" and version.startswith("r3-v") for field, version in history)


def snapshot_fixes(
    raw: Mapping[int, GeoRow], live: Mapping[int, LiveRow], index: SummitIndex, mp_index: MpAreaIndex,
    history: History, live_links: Mapping[int, int | None] | None = None,
) -> tuple[list[GeoFix], list[Change], Counter[str]]:
    """Decisions come from the pre-repair snapshot only (G17 principle, as R1): a hand edit to
    a live row, or R3's own earlier moves and nulls, never feed a later run. A kept point is
    the snapshot point, so a live point that drifted from it without an audited cause is put
    back (audited) rather than left under a label that describes a different point.

    Never re-decided, only counted: rows an owner decided (any r3-review-* revision), and rows
    whose geocode_precision was set by something other than an R3 rule version (curated after
    the snapshot). Rows absent from the snapshot are labelled untrusted and never moved.

    A route link an earlier R3 rule cleared is put back from the snapshot (audited) once the
    current rule no longer contradicts the row, like every other R3 output. Owner-decided rows are
    not: an owner placement is labelled area/owner_summit or unknown/gnis_rejected, never
    route_link/crag, so a cleared link under it is consistent. `live_links` (live mp_route_id)
    limits the restore to rows whose link is still NULL; without it the writer's unchanged check
    does that at apply."""
    fallback = find_fallback_points(list(raw.values()), index, mp_index)
    states = point_states(stated_points(raw.values()), index)
    fixes: list[GeoFix] = []
    changes: list[Change] = []
    skipped: Counter[str] = Counter()
    for accident_id, (lat, lon, precision) in live.items():
        past = history.get(accident_id, ())
        if _owner_decided(past):
            skipped["owner_decided"] += 1
            continue
        if _labelled_elsewhere(precision, past):
            skipped["labelled_elsewhere"] += 1
            continue
        row = raw.get(accident_id)
        if row is None:
            fix = GeoFix(accident_id, KEEP, "unknown", NOT_IN_SNAPSHOT, None)
        else:
            fix = geocode_fix(row, index, fallback, states)
            if fix.location is KEEP and row.lat is not None and row.lon is not None and (lat, lon) != (row.lat, row.lon):
                if any(field == LOCATION and not version.startswith("r3-") for field, version in past):
                    # An audited non-R3 rule placed the live point: keep it, but the decision
                    # was about the snapshot point, so it cannot vouch for this one.
                    fix = GeoFix(accident_id, KEEP, "unknown", MOVED_BY_OTHER_RULE, fix.country)
                else:
                    changes.append(Change(accident_id, LOCATION, (row.lat, row.lon), SNAPSHOT_POINT))
        fixes.append(fix)
        changes += changes_for(fix)
        if (
            row is not None and row.mp_route_id is not None and not fix.clear_route_link and _r3_touched_link(past)
            and (live_links is None or live_links.get(accident_id) is None)
        ):
            changes.append(Change(accident_id, "mp_route_id", row.mp_route_id, LINK_RESTORED))
    return fixes, changes, skipped


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


GNIS_SQL = "SELECT gnis_id, name, name_key, state_code, lat, lon FROM gnis_summits"
# Only the columns ingest's grant covers at 0006 (when Stage B runs R3): never url.
MP_LOCATIONS_SQL = "SELECT mp_id, name, parent_id, latitude, longitude FROM mp_locations"
MP_ROUTE_COUNTS_SQL = "SELECT location_id, count(*) FROM mp_routes WHERE location_id IS NOT NULL GROUP BY location_id"


def summit_index_from_rows(rows: Iterable[Sequence[object]]) -> SummitIndex:
    return SummitIndex([
        Summit(int(str(g)), str(n), str(k), str(s), float(str(la)), float(str(lo))) for g, n, k, s, la, lo in rows
    ])


def mp_area_index_from_rows(
    locations: Iterable[Sequence[object]], route_counts: Iterable[Sequence[object]]
) -> MpAreaIndex:
    """From MP_LOCATIONS_SQL and MP_ROUTE_COUNTS_SQL rows (shared with the acceptance cell)."""
    located = [(int(str(m)), str(n), int(str(p)) if p is not None else None, _float(la), _float(lo))
               for m, n, p, la, lo in locations]
    direct = {int(str(loc)): int(str(c)) for loc, c in route_counts}
    routes = subtree_route_counts({m: p for m, _, p, _, _ in located}, direct)
    return MpAreaIndex([MpArea(m, n, la, lo, routes[m]) for m, n, _, la, lo in located])


async def _index(conn: AsyncConnection) -> SummitIndex:
    index = summit_index_from_rows((await conn.execute(text(GNIS_SQL))).all())
    if not index.ids:
        raise SystemExit("gnis_summits is empty: run python -m app.pipelines.gnis first")
    return index


def require_crag_areas(index: MpAreaIndex) -> MpAreaIndex:
    # Refused rather than run without them: an index with no usable area silently turns every
    # crag back into a fallback (empty mp_locations or mp_routes, or no coordinates).
    if not index.crag_areas:
        raise SystemExit(
            "no MP area has coordinates and 1-400 routes: R3's crag exemption needs mp_locations and mp_routes loaded"
        )
    return index


async def _mp_index(conn: AsyncConnection) -> MpAreaIndex:
    return require_crag_areas(mp_area_index_from_rows(
        (await conn.execute(text(MP_LOCATIONS_SQL))).all(), (await conn.execute(text(MP_ROUTE_COUNTS_SQL))).all()
    ))


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


def cluster_summary(rows: Sequence[GeoRow], index: SummitIndex, mp_index: MpAreaIndex) -> dict[str, object]:
    """Coordinates, row counts and source-family counts, plus the GNIS and MP ids (and MP area
    names) behind each exemption, and the points whose stated rows R3 nulls for naming another
    state (state counts only); never accident text. For the owner's review before r3 --apply."""
    split = split_fallback_points(rows, index, mp_index)
    states = point_states(stated_points(rows) | split.fallback, index)
    per_point: Counter[Point] = Counter()
    families: dict[Point, Counter[str]] = defaultdict(Counter)
    row_states: dict[Point, Counter[str]] = defaultdict(Counter)
    dropped: Counter[Point] = Counter()
    unlinked: Counter[Point] = Counter()
    for r in rows:
        if r.lat is not None and r.lon is not None:
            p = _point(r.lat, r.lon)
            per_point[p] += 1
            code = state_code(r.state)
            row_states[p][code or ("blank" if not (r.state or "").strip() else "other")] += 1
            if p in split.fallback:
                families[p][r.family] += 1
            # The step's own decision per row, so a row R3a moves first is not counted.
            fix = geocode_fix(r, index, split.fallback, states)
            if fix.method == STATE_CONTRADICTION:
                dropped[p] += 1
                unlinked[p] += fix.clear_route_link
    summit_excluded = []
    for lat, lon in sorted(split.on_summit):
        near = index.nearest_summit_within(lat, lon, SUMMIT_EXEMPT_KM)
        assert near is not None
        summit_excluded.append({
            "lat": lat, "lon": lon, "rows": per_point[(lat, lon)], "gnis_id": near[0].gnis_id,
            "distance_m": round(near[1] * 1000),
        })
    crag_excluded = [
        {
            "lat": lat, "lon": lon, "rows": per_point[(lat, lon)], "mp_area_id": m.area.mp_id, "mp_area_name": m.area.name,
            "distance_m": round(m.distance_km * 1000), "matched_key": m.key, "share": round(m.share, 3),
        }
        for (lat, lon), m in sorted(split.on_crag.items())
    ]
    state_contradictions = [
        {
            "lat": lat, "lon": lon, "point_state": states[(lat, lon)].nearest, "fallback": (lat, lon) in split.fallback,
            "rows_dropped": dropped[(lat, lon)], "links_cleared": unlinked[(lat, lon)],
            "row_states": dict(sorted(row_states[(lat, lon)].items())),
        }
        for lat, lon in sorted(dropped)
    ]
    by_point = {
        f"{lat},{lon}": {"rows": sum(families[(lat, lon)].values()), "families": dict(sorted(families[(lat, lon)].items()))}
        for lat, lon in sorted(split.fallback)
    }
    return {
        "fallback_points": len(split.fallback),
        "rows_at_fallback_points": sum(sum(f.values()) for f in families.values()),
        "summit_excluded_points": len(split.on_summit),
        "summit_excluded_rows": sum(per_point[p] for p in split.on_summit),
        "summit_excluded": summit_excluded,
        "crag_excluded_points": len(split.on_crag),
        "crag_excluded_rows": sum(per_point[p] for p in split.on_crag),
        "crag_excluded": crag_excluded,
        "state_undetermined_points": len(split.fallback - set(states)),
        "state_contradiction_points": len(state_contradictions),
        "state_contradiction_rows": sum(dropped.values()),
        "state_contradiction_fallback_rows": sum(n for p, n in dropped.items() if p in split.fallback),
        "state_contradiction_links": sum(unlinked.values()),
        "state_contradictions": state_contradictions,
        "points": by_point,
    }


async def run_r3_clusters(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    return cluster_summary(list((await raw_geo_rows(conn)).values()), await _index(conn), await _mp_index(conn))


async def run_r3_states(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    """Task 13 pre-check: every raw state value with its class. Before r3 --apply the owner
    confirms each foreign one (R3 nulls those points) and maps or accepts each unmapped one."""
    result = await conn.execute(text("SELECT state, count(*) FROM internal.accidents_raw GROUP BY 1 ORDER BY 1"))
    states = [{"state": st, "n": int(n), "class": classify_state(st)} for st, n in result.all()]
    return {
        "states": states,
        "unmapped": [s for s in states if s["class"] == "unmapped"],
        "foreign": [s for s in states if str(s["class"]).startswith("foreign:")],
    }


async def _history(conn: AsyncConnection) -> dict[int, list[tuple[str, str]]]:
    result = await conn.execute(text("SELECT accident_id, field, rule_version FROM internal.accident_revisions ORDER BY id"))
    history: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for accident_id, field, version in result.all():
        history[int(accident_id)].append((str(field), str(version)))
    return history


async def run_r3(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    raw = await raw_geo_rows(conn)
    index = await _index(conn)
    rows = await fetch_accidents(conn, ["latitude", "longitude", "geocode_precision", "mp_route_id"])
    live: dict[int, LiveRow] = {
        _id(r): (_float(r["latitude"]), _float(r["longitude"]), _str(r["geocode_precision"])) for r in rows
    }
    links = {_id(r): int(str(r["mp_route_id"])) if r["mp_route_id"] is not None else None for r in rows}
    fixes, changes, skipped = snapshot_fixes(raw, live, index, await _mp_index(conn), await _history(conn), links)
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
    return summary | {
        "methods": dict(sorted(methods.items())), "skipped": dict(sorted(skipped.items())), "missing_alias_ids": missing,
    }


async def require_r3(conn: AsyncConnection, step: str) -> None:
    """For steps that read R3's point labels (D12). The run log, not the revisions: an r3
    apply that changed nothing still labelled every row."""
    ran = await conn.execute(
        text("SELECT EXISTS (SELECT 1 FROM source_ingest_log WHERE source = 'repair:r3' AND status = 'ok')")
    )
    if not ran.scalar_one():
        raise SystemExit(f"{step} reads R3's geocode_precision labels: apply r3 first")


def plot_stratum(row: Mapping[str, object]) -> str:
    method = str(row.get("geocode_method") or "")
    if method.startswith("gnis_summit"):
        return "gnis_move"
    return str(row.get("geocode_precision") or "unknown")


PLOT_QUOTAS = {"gnis_move": 10, "region_fallback": 10, "unknown": 5, "area": 10, "crag": 5, "exact": 5, "park_centroid": 5}
# The precision label is shown on purpose: the plot check judges whether the label fits the point,
# not the point alone. A fallback label is a property of the cluster, so a region_fallback point
# that sits at the named place is plausible too (owner, 2026-11-17).
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


def rejection_changes(row: GeoRow) -> list[Change]:
    """A move the owner rejected goes back to its snapshot point, untrusted, into the review pool."""
    point = (row.lat, row.lon) if row.lat is not None and row.lon is not None else None
    country = "US" if state_code(row.state) else None
    return changes_for(GeoFix(row.accident_id, point, "unknown", "gnis_rejected", country))


async def run_r3a_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    """With --apply: records the agreement and sends every "no" row back (r3-review-v1).
    The dry run reports both and writes nothing."""
    decisions = review.read_decisions(Path(args.review_file), key="accident_id", decision_column="correct", allowed=YES_NO)
    refuse_ineligible(decisions, {_id(r) for r in await r3a_check_pool(conn)})
    raw = await raw_geo_rows(conn)
    rejected = sorted(a for a, v in decisions.items() if v == "no")
    report = ValidationReport("repair:r3a-reject")
    changes: list[Change] = []
    for accident_id in rejected:
        report.quarantine(str(accident_id), "gnis_rejected")
        changes += rejection_changes(raw[accident_id])
    apply = bool(getattr(args, "apply", False))
    if apply:
        check = await record_check(conn, "repair:r3a-check", decisions)
    else:
        # The rate is recorded with the rejections it implies, never ahead of them.
        check = {"source": "repair:r3a-check", "n": len(decisions), "agreement": agreement(decisions), "mode": "dry_run"}
    if not changes:
        return check
    sent_back = await run_step(
        conn, step="r3a-reject", rule_version=R3_REVIEW_VERSION, changes=changes, report=report, apply=apply
    )
    return check | {"rejected": sent_back}


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
            "a centroid, or a shared point that is at the named place), else no"
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


REVIEW_METHODS = frozenset({"gnis_ambiguous", "gnis_state_unmatched", "gnis_rejected"})


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
        conn, step="r3-review", rule_version=R3_PICK_VERSION, changes=changes, report=report, apply=args.apply
    )
