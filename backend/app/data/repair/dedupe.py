"""R5: exact duplicates merge automatically; fuzzy pairs (±2 days, ≤5 km) are scored and
auto-decided at ≥0.90 (merge, and only when the place names agree) and ≤0.40 (distinct);
only the band between goes to the owner. A row whose point is untrusted cannot use the distance rule, so it is compared on
date, state and text evidence instead, and every such pair goes to the owner (owner
decision O2: never auto-merged). Duplicates are only ever marked (incident_group_id,
is_canonical), never deleted, and a row that leaves a group is restored.

The rule version carries a hash of the decision set and the group heads, so a changed
outcome is re-applied instead of being skipped as already recorded."""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from calendar import monthrange
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import review
from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.geocode import TRUSTED_POINT_PRECISIONS, require_r3
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.geo import haversine_km
from app.pipelines.gnis import state_code
from app.pipelines.textsim import jaro_winkler, normalize_text, place_key
from app.pipelines.validate import ValidationReport

R5_BASE = "r5-v1"
MERGE_AT, DISTINCT_AT = 0.90, 0.40
MAX_DAYS, MAX_KM = 2, 5.0
# An interval overlap is weaker evidence than the same day; scaling the date component
# keeps imprecise pairs out of the auto-merge band so the owner decides them.
PRECISION_WEIGHT = {"day": 1.0, "month": 0.6, "year": 0.3}
WEIGHTS = {"date": 0.30, "distance": 0.25, "type": 0.15, "severity": 0.15, "name": 0.15}
GEO_RANK = {"exact": 0, "crag": 1, "area": 2, "park_centroid": 3, "region_fallback": 4, "unknown": 5}
SOURCE_RANK = {"aac": 0, "avalanche": 1, "nps": 2}
RANGE_YEAR_SOURCE = "aac_source_id_neighbours"
# I2 (owner, 2026-10-01): an area or park-centroid point stands for a whole area, so two rows
# sharing one are only half as likely to be co-located as two crag-level points.
FULL_DISTANCE_PRECISIONS = frozenset({"exact", "crag"})
AREA_DISTANCE_CREDIT = 0.5
# I3 (owner): the rows accidents_clean keeps (O3: a NULL country stays, flagged).
CLEAN_ACTIVITY = frozenset({"climbing", "climbing_approach"})
# O2 text evidence. These thresholds only decide which untrusted-point pairs the owner sees
# (nothing is auto-decided on them); they are first guesses, to be checked against the
# review size in the Task 13 rehearsal. Too low floods the CSV, too high double counts.
PLACE_MATCH_AT = 0.85
NARRATIVE_MATCH_AT = 0.35
MIN_NARRATIVE_TOKENS = 3
MIN_MENTION_LEN = 4
# Words every accident narrative shares; overlap on them says nothing about the incident.
_GENERIC_WORDS = frozenset({
    "about", "above", "after", "again", "also", "back", "because", "been", "before", "below", "being",
    "could", "down", "during", "each", "from", "have", "having", "into", "just", "more", "most", "near",
    "only", "onto", "other", "over", "said", "some", "than", "that", "their", "them", "then", "there",
    "these", "they", "this", "through", "under", "until", "upon", "very", "were", "what", "when",
    "where", "which", "while", "with", "would", "your",
    "accident", "climb", "climbed", "climber", "climbers", "climbing", "fall", "fell", "falling", "feet",
    "injured", "injuries", "injury", "party", "partner", "rope", "route", "summit", "team",
})
Basis = Literal["distance", "place_name", "place_mention", "narrative", "conflict"]


@dataclass(frozen=True)
class DupRow:
    accident_id: int
    family: SourceFamily
    date: date | None
    lat: float | None
    lon: float | None
    accident_type: str | None
    severity: str | None
    place: str
    date_precision: str | None
    geocode_precision: str | None
    exact_key: tuple[object, ...]
    year_lo: int | None = None
    year_hi: int | None = None
    year_source: str | None = None
    state: str | None = None
    # place_key of mountain, route and location, for the O2 mention check.
    names: tuple[str, ...] = ()
    # Normalised narrative, held in memory only for O2; never exported, logged or printed.
    narrative: str = field(default="", repr=False, compare=False)
    # place_key of mountain and of route, each on its own: a shared one names the same summit or route.
    peaks: frozenset[str] = frozenset()
    # Would accidents_clean keep this row (activity class, country, no exclusion)?
    clean: bool = True


def _same(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.5
    return 1.0 if normalize_text(a) == normalize_text(b) else 0.0


def _coords(r: DupRow) -> tuple[float, float] | None:
    # region_fallback/unknown points are kept on the row but are city/state centroids;
    # two unrelated accidents at the same centroid must not look co-located.
    if r.lat is None or r.lon is None or (r.geocode_precision or "unknown") not in TRUSTED_POINT_PRECISIONS:
        return None
    return (r.lat, r.lon)


def date_interval(
    d: date | None, precision: str | None, *, year_range: tuple[int, int] | None = None
) -> tuple[date, date] | None:
    if d is None:
        return None
    if precision == "day":
        return (d, d)
    if precision == "month":
        return (d.replace(day=1), d.replace(day=monthrange(d.year, d.month)[1]))
    if precision == "year":
        lo, hi = year_range or (d.year, d.year)
        return (date(lo, 1, 1), date(hi, 12, 31))
    return None


def row_interval(r: DupRow) -> tuple[date, date] | None:
    """A neighbour-derived year is a range, never the calendar year of its point date (owner
    decision 2026-09-30). At day/month precision R1 has year_lo == year_hi, so the finer
    interval already lies inside it."""
    if r.year_source == RANGE_YEAR_SOURCE and r.date_precision == "year":
        if r.year_lo is None or r.year_hi is None:
            return None
        return date_interval(r.date, r.date_precision, year_range=(r.year_lo, r.year_hi))
    return date_interval(r.date, r.date_precision)


def interval_gap_days(a: tuple[date, date], b: tuple[date, date]) -> int:
    return max(0, (b[0] - a[1]).days, (a[0] - b[1]).days)


def pair_score(a: DupRow, b: DupRow) -> float:
    ia, ib = row_interval(a), row_interval(b)
    gap = interval_gap_days(ia, ib) if ia is not None and ib is not None else MAX_DAYS + 1
    weight = min(PRECISION_WEIGHT.get(a.date_precision or "", 0.0), PRECISION_WEIGHT.get(b.date_precision or "", 0.0))
    ca, cb = _coords(a), _coords(b)
    if ca is None or cb is None:
        near = 0.5
    else:
        near = max(0.0, 1 - haversine_km(ca[0], ca[1], cb[0], cb[1]) / MAX_KM)
        if not {a.geocode_precision, b.geocode_precision} <= FULL_DISTANCE_PRECISIONS:
            near *= AREA_DISTANCE_CREDIT
    name = jaro_winkler(a.place, b.place) if a.place and b.place else 0.5
    score = (
        WEIGHTS["date"] * weight * max(0.0, 1 - gap / (MAX_DAYS + 1))
        + WEIGHTS["distance"] * near
        + WEIGHTS["type"] * _same(a.accident_type, b.accident_type)
        + WEIGHTS["severity"] * _same(a.severity, b.severity)
        + WEIGHTS["name"] * name
    )
    return round(score, 6)


def band(score: float) -> Literal["merge", "distinct", "review"]:
    if score >= MERGE_AT:
        return "merge"
    if score <= DISTINCT_AT:
        return "distinct"
    return "review"


def places_agree(a: DupRow, b: DupRow) -> bool:
    return bool(a.peaks & b.peaks) or bool(a.place and b.place and jaro_winkler(a.place, b.place) >= PLACE_MATCH_AT)


def decide(a: DupRow, b: DupRow, score: float, basis: Basis) -> Literal["merge", "distinct", "review"]:
    """The auto decision for a scored pair that has no owner decision. Only the distance rule
    auto-decides (O2: an untrusted-point pair always goes to the owner), and an auto merge also
    needs the place names to agree (I2): same day and place-level point alone is not enough."""
    if basis != "distance":
        return "review"
    verdict = band(score)
    if verdict == "merge" and not places_agree(a, b):
        return "review"
    return verdict


def exact_groups(rows: Sequence[DupRow]) -> list[list[int]]:
    by_key: dict[tuple[object, ...], list[int]] = defaultdict(list)
    for row in rows:
        # M2 (owner): with no date and no narrative, equal remaining fields are no evidence of one incident.
        if row.date is None and not row.narrative:
            continue
        by_key[(row.family, *row.exact_key)].append(row.accident_id)
    return sorted(sorted(ids) for ids in by_key.values() if len(ids) > 1)


def _exact_index(rows: Sequence[DupRow]) -> dict[int, int]:
    return {member: n for n, group in enumerate(exact_groups(rows)) for member in group}


def _windowed(rows: Sequence[DupRow]) -> Iterable[tuple[DupRow, DupRow]]:
    """Every pair whose date intervals are ≤ MAX_DAYS apart, each once."""
    dated = sorted(
        ((span, r) for r in rows if (span := row_interval(r)) is not None),
        key=lambda t: (t[0][0], t[1].accident_id),
    )
    for i, (sa, a) in enumerate(dated):
        for sb, b in dated[i + 1 :]:
            # Sorted by interval start, so once b starts more than MAX_DAYS after a ends,
            # every later row does too.
            if (sb[0] - sa[1]).days > MAX_DAYS:
                break
            yield a, b


def candidate_pairs(rows: Sequence[DupRow]) -> list[tuple[int, int, float]]:
    exact = _exact_index(rows)
    pairs = []
    for a, b in _windowed(rows):
        ca, cb = _coords(a), _coords(b)
        if ca is None or cb is None or haversine_km(ca[0], ca[1], cb[0], cb[1]) > MAX_KM:
            continue
        if a.accident_id in exact and exact[a.accident_id] == exact.get(b.accident_id):
            continue
        lo, hi = sorted((a.accident_id, b.accident_id))
        pairs.append((lo, hi, pair_score(a, b)))
    return sorted(pairs)


def _tokens(narrative: str) -> frozenset[str]:
    return frozenset(t for t in narrative.split() if len(t) >= 4 and not t.isdigit() and t not in _GENERIC_WORDS)


def _mentions(names: Iterable[str], narrative: str) -> bool:
    padded = f" {narrative} "
    return any(len(n) >= MIN_MENTION_LEN and f" {n} " in padded for n in names)


def _text_evidence(a: DupRow, b: DupRow, tokens: Mapping[int, frozenset[str]]) -> Basis | None:
    if a.place and b.place and jaro_winkler(a.place, b.place) >= PLACE_MATCH_AT:
        return "place_name"
    if _mentions({a.place, *a.names}, b.narrative) or _mentions({b.place, *b.names}, a.narrative):
        return "place_mention"
    ta, tb = tokens[a.accident_id], tokens[b.accident_id]
    if len(ta) >= MIN_NARRATIVE_TOKENS and len(tb) >= MIN_NARRATIVE_TOKENS:
        if len(ta & tb) / len(ta | tb) >= NARRATIVE_MATCH_AT:
            return "narrative"
    return None


def untrusted_pairs(rows: Sequence[DupRow]) -> list[tuple[int, int, float, Basis]]:
    """O2: pairs where at least one point is untrusted (or missing), pre-filtered on the date
    window and a compatible state before any text is compared (Task 5 carry). A blank state
    is compatible with any state: a stateless row is exactly the one most likely to be a
    second copy. Every pair returned goes to the owner, whatever its score."""
    exact = _exact_index(rows)
    tokens = {r.accident_id: _tokens(r.narrative) for r in rows}
    pairs: list[tuple[int, int, float, Basis]] = []
    for a, b in _windowed(rows):
        if _coords(a) is not None and _coords(b) is not None:
            continue
        if a.state and b.state and a.state != b.state:
            continue
        if a.accident_id in exact and exact[a.accident_id] == exact.get(b.accident_id):
            continue
        basis = _text_evidence(a, b, tokens)
        if basis is None:
            continue
        lo, hi = sorted((a.accident_id, b.accident_id))
        pairs.append((lo, hi, pair_score(a, b), basis))
    return sorted(pairs)


def groups(ids: Iterable[int], merges: Iterable[tuple[int, int]]) -> list[list[int]]:
    parent = {i: i for i in ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in merges:
        parent[find(a)] = find(b)
    members: dict[int, list[int]] = defaultdict(list)
    for i in parent:
        members[find(i)].append(i)
    return sorted(sorted(m) for m in members.values() if len(m) > 1)


def _group_of(ids: Iterable[int], merges: Iterable[tuple[int, int]]) -> dict[int, int]:
    return {member: group[0] for group in groups(ids, merges) for member in group}


Pair = tuple[int, int]


def resolve_merges(
    ids: Iterable[int], decided: Mapping[Pair, tuple[str, str]], *, pending: Iterable[Pair] = ()
) -> tuple[list[Pair], dict[Pair, Pair]]:
    """(merges to apply, {auto merge withheld for the owner: the pair that withheld it}).
    decided maps a pair to (decision, decided_by); pending are pairs waiting for the owner.
    Groups are transitive, so auto merges A-B and B-C would join A and C even where A-C was
    judged distinct, or is still waiting for the owner (I1); such a group keeps only its owner
    merges and its auto merges go to the owner instead. Owner merges outrank an auto distinct
    and settle a pending pair they already join; contradictory owner decisions are refused."""
    ids = list(ids)
    owner_merges = sorted(p for p, (d, by) in decided.items() if d == "merge" and by == "owner")
    owner_group = _group_of(ids, owner_merges)
    clash = sorted(
        p for p, (d, by) in decided.items()
        if d == "distinct" and by == "owner" and p[0] in owner_group and owner_group[p[0]] == owner_group.get(p[1])
    )
    if clash:
        raise ValueError(f"owner decisions contradict: distinct pair(s) {clash[:5]} are joined by owner merges")
    blocking = sorted(
        {p for p, (d, _) in decided.items() if d == "distinct"} | set(pending)
    )
    blocking = [p for p in blocking if not (p[0] in owner_group and owner_group[p[0]] == owner_group.get(p[1]))]
    merges = sorted(p for p, (d, _) in decided.items() if d == "merge")
    full_group = _group_of(ids, merges)
    split: dict[int, Pair] = {}
    for a, b in blocking:
        if a in full_group and full_group[a] == full_group.get(b):
            split.setdefault(full_group[a], (a, b))
    withheld = {p: split[full_group[p[0]]] for p in merges if decided[p][1] == "auto" and full_group[p[0]] in split}
    return [p for p in merges if p not in withheld], withheld


def canonical(members: Sequence[DupRow]) -> int:
    best = min(
        members,
        key=lambda r: (
            # I3 (owner): a head the clean set drops would drop the whole incident with it.
            0 if r.clean else 1,
            0 if r.date_precision == "day" else 1,
            GEO_RANK.get(r.geocode_precision or "unknown", 5),
            SOURCE_RANK[r.family],
            r.accident_id,
        ),
    )
    return best.accident_id


def heads(rows_by_id: Mapping[int, DupRow], merges: Iterable[tuple[int, int]]) -> dict[int, int]:
    head_of: dict[int, int] = {}
    for group in groups(rows_by_id, merges):
        head = canonical([rows_by_id[i] for i in group])
        for member in group:
            head_of[member] = head
    return head_of


def group_changes(rows_by_id: Mapping[int, DupRow], merges: Iterable[tuple[int, int]]) -> list[Change]:
    # Emitted for every row, not only current members: a pair flipped to distinct (owner
    # decision, R11 row, R3 score drift) must reset the rows that left the group, or they
    # would stay non-canonical and silently drop out of accidents_clean.
    head_of = heads(rows_by_id, merges)
    changes: list[Change] = []
    for accident_id in sorted(rows_by_id):
        head = head_of.get(accident_id)
        changes += [
            Change(accident_id, "incident_group_id", head, "r5"),
            Change(accident_id, "is_canonical", head is None or head == accident_id, "r5"),
        ]
    return changes


def rule_version(decisions: Mapping[tuple[int, int], str], *, heads: Mapping[int, int] | None = None) -> str:
    # Heads count too: an R3 relabel can move a group's head with no decision changing.
    body = "".join(f"{a},{b},{d};" for (a, b), d in sorted(decisions.items()))
    body += "|" + "".join(f"{m}>{h};" for m, h in sorted((heads or {}).items()))
    return f"{R5_BASE}+{hashlib.sha256(body.encode()).hexdigest()[:10]}"


def next_version(base: str, applied: Sequence[str]) -> str:
    """applied: rule versions of earlier applied r5 runs, oldest first. A rerun of the latest
    outcome reuses its version (nothing to write). An outcome that returns after another one
    (merge -> distinct -> merge) finds its revision keys already recorded and would be skipped,
    leaving the middle outcome in place; it gets a new generation instead."""
    def same_base(version: str) -> bool:
        return version == base or version.startswith(base + ".")

    if applied and same_base(applied[-1]):
        return applied[-1]
    used = {v for v in applied if same_base(v)}
    return base if not used else f"{base}.{len(used) + 1}"


def owned_by_r5(is_canonical: bool, group_id: int | None, history: Sequence[str]) -> bool:
    """history: rule versions of the row's incident_group_id/is_canonical revisions, oldest
    first. Whoever set the group fields last owns them; a row marked by another rule, or by
    hand with no revision, is left alone."""
    if history:
        return history[-1].startswith("r5-")
    return is_canonical and group_id is None


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int(value: object) -> int | None:
    return int(float(str(value))) if value is not None else None


def _dup_row(r: Mapping[str, object]) -> DupRow:
    digest = hashlib.md5(str(r["description"] or "").encode(), usedforsecurity=False).hexdigest()
    names = tuple(k for k in (place_key(_str(r[c])) for c in ("mountain", "route", "location")) if k)
    return DupRow(
        int(str(r["accident_id"])),
        source_family(_str(r["source"])),
        r["date"] if isinstance(r["date"], date) else None,
        float(str(r["latitude"])) if r["latitude"] is not None else None,
        float(str(r["longitude"])) if r["longitude"] is not None else None,
        _str(r["accident_type"]),
        _str(r["injury_severity"]),
        place_key(f"{r['mountain'] or ''} {r['route'] or ''}"),
        _str(r["date_precision"]),
        _str(r["geocode_precision"]),
        (r["date"], r["latitude"], r["longitude"], r["accident_type"], r["activity"], r["injury_severity"],
         r["mountain"], r["route"], digest),
        year_lo=_int(r["year_lo"]),
        year_hi=_int(r["year_hi"]),
        year_source=_str(r["year_source"]),
        state=state_code(_str(r["state"])),
        names=names,
        narrative=normalize_text(_str(r["description"])),
        peaks=frozenset(k for k in (place_key(_str(r[c])) for c in ("mountain", "route")) if k),
        clean=(
            _str(r["activity_class"]) in CLEAN_ACTIVITY
            and _str(r["country"]) in (None, "US")
            and r["excluded_reason"] is None
        ),
    )


@dataclass
class Plan:
    rows: dict[int, DupRow]
    locators: dict[int, Mapping[str, object]]
    decided: dict[Pair, tuple[str, float, str]]  # pair -> (decision, score, decided_by)
    review: list[tuple[int, int, float, Basis]]
    merges: list[Pair]
    conflict_with: dict[Pair, Pair]
    scored: set[Pair]
    owner: dict[Pair, str]
    skipped: Counter[str]


async def _group_history(conn: AsyncConnection) -> dict[int, list[str]]:
    result = await conn.execute(text(
        "SELECT accident_id, rule_version FROM internal.accident_revisions "
        "WHERE field IN ('incident_group_id', 'is_canonical') ORDER BY id"
    ))
    history: dict[int, list[str]] = defaultdict(list)
    for accident_id, version in result.all():
        history[int(accident_id)].append(str(version))
    return history


async def _plan(conn: AsyncConnection) -> Plan:
    await require_r3(conn, "r5")
    raw = await fetch_accidents(conn, [
        "source", "source_id", "date", "latitude", "longitude", "accident_type", "activity", "injury_severity",
        "mountain", "route", "location", "state", "description", "date_precision", "geocode_precision",
        "year_lo", "year_hi", "year_source", "incident_group_id", "is_canonical",
        "activity_class", "country", "excluded_reason",
    ])
    history = await _group_history(conn)
    rows: dict[int, DupRow] = {}
    locators: dict[int, Mapping[str, object]] = {}
    skipped: Counter[str] = Counter()
    for r in raw:
        accident_id = int(str(r["accident_id"]))
        group_id = _int(r["incident_group_id"])
        if not owned_by_r5(bool(r["is_canonical"]), group_id, history.get(accident_id, ())):
            skipped["labelled_elsewhere"] += 1
            continue
        rows[accident_id] = _dup_row(r)
        locators[accident_id] = {
            k: r[k] for k in ("source", "source_id", "date", "state", "mountain", "route", "accident_type", "injury_severity")
        }
    owner = {
        (int(a), int(b)): str(d)
        for a, b, d in (await conn.execute(text(
            "SELECT low_id, high_id, decision FROM internal.duplicate_decisions WHERE decided_by = 'owner'"))).all()
    }
    # An owner decision is never re-decided, even once its pair stops being a candidate; it
    # only lapses for a row R5 no longer owns.
    kept_owner = {p: d for p, d in owner.items() if p[0] in rows and p[1] in rows}
    if len(kept_owner) < len(owner):
        skipped["owner_decision_lapsed"] = len(owner) - len(kept_owner)
    owner = kept_owner
    decided: dict[Pair, tuple[str, float, str]] = {p: (d, 1.0, "owner") for p, d in owner.items()}
    review: list[tuple[int, int, float, Basis]] = []
    scored: set[Pair] = set()
    row_list = list(rows.values())
    for group in exact_groups(row_list):
        for other in group[1:]:
            scored.add((group[0], other))
            decided.setdefault((group[0], other), ("merge", 1.0, "auto"))
    for lo, hi, score in candidate_pairs(row_list):
        scored.add((lo, hi))
        if (lo, hi) in owner:
            decided[(lo, hi)] = (owner[(lo, hi)], score, "owner")
            continue
        verdict = decide(rows[lo], rows[hi], score, "distance")
        if verdict == "review":
            review.append((lo, hi, score, "distance"))
        else:
            decided[(lo, hi)] = (verdict, score, "auto")
    for lo, hi, score, basis in untrusted_pairs(row_list):
        scored.add((lo, hi))
        if (lo, hi) in owner:
            decided[(lo, hi)] = (owner[(lo, hi)], score, "owner")
        else:
            review.append((lo, hi, score, basis))
    merges, withheld = resolve_merges(
        rows, {p: (d, by) for p, (d, _, by) in decided.items()}, pending=[(lo, hi) for lo, hi, _, _ in review]
    )
    for pair in withheld:
        _, score, _ = decided.pop(pair)
        review.append((pair[0], pair[1], score, "conflict"))
    return Plan(rows, locators, decided, sorted(review), merges, withheld, scored, owner, skipped)


REVIEW_SIDE = ["source", "source_id", "date", "date_precision", "state", "mountain", "route", "accident_type",
               "injury_severity", "geocode_precision"]
REVIEW_COLUMNS = ["low_id", "high_id", "basis", "conflict_with", "score", *(f"{c}_{s}" for s in "ab" for c in REVIEW_SIDE)]


def _review_rows(plan: Plan) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for lo, hi, score, basis in plan.review:
        trigger = plan.conflict_with.get((lo, hi))
        row: dict[str, object] = {
            "low_id": lo, "high_id": hi, "basis": basis, "score": score,
            "conflict_with": f"{trigger[0]}-{trigger[1]}" if trigger else "",
        }
        for side, accident_id in (("a", lo), ("b", hi)):
            loc, dup = plan.locators[accident_id], plan.rows[accident_id]
            values = dict(loc) | {"date_precision": dup.date_precision, "geocode_precision": dup.geocode_precision}
            row |= {f"{c}_{side}": values.get(c) for c in REVIEW_SIDE}
        out.append(row)
    return out


async def _applied_versions(conn: AsyncConnection) -> list[str]:
    result = await conn.execute(text(
        "SELECT validation_report->>'rule_version' FROM source_ingest_log "
        "WHERE source = 'repair:r5' AND status = 'ok' ORDER BY finished_at, started_at"
    ))
    return [str(v) for (v,) in result.all() if v is not None]


async def run_r5(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    plan = await _plan(conn)
    decisions = {pair: d for pair, (d, _, _) in plan.decided.items()}
    head_of = heads(plan.rows, plan.merges)
    version = next_version(rule_version(decisions, heads=head_of), await _applied_versions(conn))
    changes = group_changes(plan.rows, plan.merges)
    report = ValidationReport("repair:r5")
    for _ in plan.rows:
        report.accept()
    pending = len(plan.review)
    # Undecided review pairs stay unmerged (double counted) until the owner decides: a
    # duplicate counted twice is recoverable, a distinct incident merged away is not.
    summary = await run_step(
        conn, step="r5", rule_version=version, changes=changes, report=report, apply=args.apply,
        metrics={"review_pending": pending},
    )
    path = None
    if args.apply:
        for (lo, hi), (d, score, by) in sorted(plan.decided.items()):
            if by == "owner":
                continue
            await conn.execute(text(
                "INSERT INTO internal.duplicate_decisions (low_id, high_id, score, decision, decided_by, rule_version, run_id) "
                "VALUES (:lo, :hi, :s, :d, 'auto', :v, CAST(:run AS uuid)) ON CONFLICT (low_id, high_id) DO UPDATE SET "
                "score = EXCLUDED.score, decision = EXCLUDED.decision, rule_version = EXCLUDED.rule_version, "
                "run_id = EXCLUDED.run_id, decided_at = now() "
                "WHERE internal.duplicate_decisions.decided_by = 'auto'"),
                {"lo": lo, "hi": hi, "s": score, "d": d, "v": R5_BASE, "run": summary["run_id"]})
        if plan.review:
            out = Path(args.out) if args.out else review.REVIEW_DIR / "duplicates.csv"
            review._write(out, _review_rows(plan), REVIEW_COLUMNS, "decision", overwrite=args.overwrite)
            path = str(out)
    bases = Counter(basis for *_, basis in plan.review)
    return summary | {
        "auto_decisions": sum(1 for _, _, by in plan.decided.values() if by == "auto"),
        "owner_decisions": len(plan.owner),
        "review_pairs": len(plan.review),
        "review_pending": pending,
        "review_basis": dict(sorted(bases.items())),
        "groups": len(groups(plan.rows, plan.merges)),
        "skipped": dict(sorted(plan.skipped.items())),
        "path": path,
    }


DECISIONS = frozenset({"merge", "distinct"})


def read_pair_decisions(path: Path) -> dict[Pair, tuple[str, float]]:
    """Keyed by the pair, not by one id: a low id can sit in several review pairs, so the
    single-key review.read_decisions does not fit here."""
    decided: dict[Pair, tuple[str, float]] = {}
    # utf-8-sig: Excel's "CSV UTF-8" prepends a BOM that would otherwise glue onto low_id.
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for number, row in enumerate(csv.DictReader(fh), start=1):
            value = (row.get("decision") or "").strip().lower()
            if value not in DECISIONS:
                raise ValueError(f"{path.name} row {number}: decision must be merge or distinct, got {value!r}")
            raw = [(row.get(k) or "").strip() for k in ("low_id", "high_id")]
            if not all(x.isdecimal() for x in raw) or int(raw[0]) >= int(raw[1]):
                raise ValueError(f"{path.name} row {number}: low_id/high_id must be ids with low < high, got {raw}")
            score = (row.get("score") or "").strip()
            if not re.fullmatch(r"\d+(\.\d+)?", score) or float(score) > 1:
                raise ValueError(f"{path.name} row {number}: score must be a number in [0, 1], got {score!r}")
            pair = (int(raw[0]), int(raw[1]))
            if pair in decided:
                raise ValueError(f"{path.name} row {number}: duplicate pair {pair}")
            decided[pair] = (value, float(score))
    return decided


async def run_r5_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    incoming = read_pair_decisions(Path(args.review_file))
    plan = await _plan(conn)
    stray = sorted(p for p in incoming if p not in plan.scored and p not in plan.owner)
    if stray:
        raise ValueError(f"{len(stray)} pair(s) are not a pair R5 scores between rows it owns, e.g. {stray[:5]}")
    # A re-import of the same file is a no-op. A different answer would silently replace an
    # owner decision (stale file, wrong copy), so replacing one has to be asked for.
    changed = sorted(p for p, (d, _) in incoming.items() if p in plan.owner and plan.owner[p] != d)
    if changed and not args.overwrite:
        raise ValueError(
            f"{len(changed)} pair(s) already decided differently by the owner, e.g. {changed[:5]}; "
            "pass --overwrite to replace those decisions"
        )
    new = {p: v for p, v in incoming.items() if plan.owner.get(p) != v[0]}
    owner_after = {p: (d, "owner") for p, d in plan.owner.items()} | {p: (d, "owner") for p, (d, _) in new.items()}
    resolve_merges(plan.rows, owner_after)
    if args.apply:
        for (lo, hi), (d, score) in sorted(new.items()):
            await conn.execute(text(
                "INSERT INTO internal.duplicate_decisions (low_id, high_id, score, decision, decided_by, rule_version) "
                "VALUES (:lo, :hi, :s, :d, 'owner', :v) ON CONFLICT (low_id, high_id) DO UPDATE SET "
                "decision = EXCLUDED.decision, decided_by = 'owner', rule_version = EXCLUDED.rule_version, "
                "run_id = NULL, decided_at = now()"),
                {"lo": lo, "hi": hi, "s": score, "d": d, "v": R5_BASE})
    overwritten = [f"{lo}-{hi}: {plan.owner[(lo, hi)]}->{d}" for (lo, hi), (d, _) in sorted(new.items()) if (lo, hi) in plan.owner]
    return {
        "mode": "apply" if args.apply else "dry_run", "imported": len(new), "unchanged": len(incoming) - len(new),
        "overwritten": overwritten,
    }
