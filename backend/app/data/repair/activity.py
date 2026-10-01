"""R4: versioned activity classes. Ski/snowmobile rows count as climbing approaches only
when the narrative uses climbing vocabulary (uphill-skinning phrasing excluded) and the
point is within 1 km of alpine/ice/mixed terrain; unknown vocabularies stop the run.
A blank or placeholder activity is never guessed (owner decision O1, review round 1): those
rows wait, unclassified, for the owner's r4-review-export/-import round trip, and the apply
goes ahead without them."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import review
from app.data.repair.checks import refuse_ineligible
from app.data.repair.framework import Change, fetch_accidents, run_step
from app.data.repair.geocode import TRUSTED_POINT_PRECISIONS, require_r3
from app.data.repair.sources import SourceFamily, source_family
from app.pipelines.geo import haversine_km_many
from app.pipelines.textsim import normalize_text
from app.pipelines.validate import ValidationReport

R4_VERSION = "r4-v1"
R4_OBJECTIVES_VERSION = "r4-v2"
# Owner classifications of rows needs_review sends to the owner. Every r4-review-* revision freezes the row
# against later R4 runs.
R4_REVIEW_VERSION = "r4-review-v1"
SKI_APPROACH_RADIUS_M = 1000
APPROACH_FLAG = "ski_approach_best_effort"
GOLDEN_QUOTAS = {"ski_approach": 15, "ski_other": 15, "nps": 10, "climbing": 10, "non_climbing": 10}
CLIMBING = frozenset({
    "climbing", "rock climbing", "ice climbing", "mixed climbing", "alpine climbing", "mountaineering",
    "trad climbing", "sport climbing", "bouldering", "top rope", "aid climbing", "ice", "rock", "alpine",
})
SKI_LIKE = frozenset({
    "skiing", "backcountry skiing", "ski touring", "snowboarding", "backcountry snowboarding", "splitboarding",
    "snowmobiling", "snowmobile", "sidecountry skiing", "ski mountaineering",
})
NON_CLIMBING = frozenset({
    "hiking", "snowshoeing", "hunting", "sledding", "snowbiking", "residential", "highway",
    "inbounds skiing", "ranger", "motorized", "walking", "scrambling off trail",
})
CLIMB_LEXICON = ("ice climb", "couloir climb", "summit attempt", "alpine route", "rappel", "crampon")
SKIN_EXCLUSIONS = ("skin track", "skinning", "climbing skins", "on skins", "skinned up")
# Reviewed placeholders: a source's "we don't know" says nothing either way (review round 1:
# avalanche "unknown" would block the apply as unmapped; AAC "other" was non_climbing on no evidence).
PLACEHOLDER = frozenset({"unknown", "other"})
# An AAC ski-mountaineering report is often a climbing objective reached on skis; the
# avalanche-centre rule (lexicon + terrain) was not built for AAC narratives.
AAC_REVIEW = frozenset({"ski mountaineering"})
REVIEW_CLASSES = frozenset({"climbing", "climbing_approach", "non_climbing"})
REVIEW_CHOICES = REVIEW_CLASSES | {"none"}


def is_blank(activity: str | None) -> bool:
    return not normalize_text(activity)


def needs_review(family: SourceFamily, activity: str | None) -> bool:
    act = normalize_text(activity)
    return not act or act in PLACEHOLDER or (family == "aac" and act in AAC_REVIEW)


def _mentions(narrative: str, phrases: Sequence[str]) -> bool:
    # Phrases match from a word start (D5): "nice climb" is not "ice climb", while
    # "rappelled" and "crampons" still match their stems.
    padded = f" {narrative}"
    return any(f" {normalize_text(p)}" in padded for p in phrases)


def classify_activity(
    family: SourceFamily, activity: str | None, text: str, near_climb_terrain: bool
) -> tuple[str, str | None] | None:
    """(class, inclusion flag), or None when the row needs the owner (needs_review) or its
    activity is outside every vocabulary (the run stops until a reviewed change maps it)."""
    if needs_review(family, activity):
        return None
    act = normalize_text(activity)
    if family == "nps":
        return ("climbing", None) if act == "rock climbing" else ("non_climbing", None)
    if act in CLIMBING:
        return ("climbing", None)
    if act in SKI_LIKE:
        narrative = normalize_text(text)
        if _mentions(narrative, CLIMB_LEXICON) and not _mentions(narrative, SKIN_EXCLUSIONS) and near_climb_terrain:
            return ("climbing_approach", APPROACH_FLAG)
        return ("non_climbing", None)
    if act in NON_CLIMBING:
        return ("non_climbing", None)
    return None


def golden_stratum(row: Mapping[str, object]) -> str:
    family, outcome = row["family"], row["outcome"]
    if family == "nps":
        return "nps"
    if outcome == "climbing_approach":
        return "ski_approach"
    if normalize_text(str(row["activity"] or "")) in SKI_LIKE:
        return "ski_other"
    return "climbing" if outcome == "climbing" else "non_climbing"


def decided_elsewhere(activity_class: str | None, history: Sequence[str]) -> str | None:
    """Why R4 must leave a row alone, or None. history is the rule versions of the row's
    activity_class revisions, oldest first; whoever set the class last owns it."""
    if any(version.startswith("r4-review-") for version in history):
        return "owner_decided"
    last = history[-1] if history else None
    if activity_class is not None and (last is None or not last.startswith("r4-v")):
        return "labelled_elsewhere"
    return None


@dataclass(frozen=True)
class ActivityRow:
    accident_id: int
    family: SourceFamily
    activity: str | None
    narrative: str
    near_climb_terrain: bool


@dataclass
class R4Input:
    rows: list[ActivityRow]
    version: str
    skipped: Counter[str]


def _id(row: Mapping[str, object]) -> int:
    return int(str(row["accident_id"]))


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


async def _terrain(conn: AsyncConnection) -> tuple[NDArray[np.float64], str]:
    points = list((await conn.execute(text(
        "SELECT DISTINCT l.latitude, l.longitude FROM mp_routes r JOIN mp_locations l ON l.mp_id = r.location_id "
        "WHERE l.latitude IS NOT NULL AND l.longitude IS NOT NULL "
        "AND (r.type ILIKE '%alpine%' OR r.type ILIKE '%ice%' OR r.type ILIKE '%mixed%')"
    ))).all())
    version = R4_VERSION
    # Spec R4 counts objectives too; they arrive in plan 6. The rule version records which
    # terrain set decided the row, so the plan-6 re-run writes new revisions.
    if (await conn.execute(text("SELECT to_regclass('public.objectives') IS NOT NULL"))).scalar_one():
        points += list((await conn.execute(text("SELECT lat, lon FROM objectives WHERE lat IS NOT NULL AND lon IS NOT NULL"))).all())
        version = R4_OBJECTIVES_VERSION
    return np.array(points, dtype=np.float64).reshape(-1, 2), version


async def _snapshot(conn: AsyncConnection) -> dict[int, tuple[str | None, str | None]]:
    """(source, activity) as first loaded, so a hand edit to a live row never feeds the rule
    (G17 principle, as R1 and R3)."""
    result = await conn.execute(text("SELECT accident_id, source, activity FROM internal.accidents_raw"))
    return {int(a): (_str(s), _str(act)) for a, s, act in result.all()}


async def _history(conn: AsyncConnection) -> dict[int, list[str]]:
    result = await conn.execute(
        text("SELECT accident_id, rule_version FROM internal.accident_revisions WHERE field = 'activity_class' ORDER BY id")
    )
    history: dict[int, list[str]] = defaultdict(list)
    for accident_id, version in result.all():
        history[int(accident_id)].append(str(version))
    return history


def _near(lat: object, lon: object, terrain: NDArray[np.float64]) -> bool:
    if lat is None or lon is None or not len(terrain):
        return False
    d = haversine_km_many(float(str(lat)), float(str(lon)), terrain[:, 0], terrain[:, 1])
    return bool((d * 1000 <= SKI_APPROACH_RADIUS_M).any())


async def _rows(conn: AsyncConnection) -> R4Input:
    await require_r3(conn, "r4")
    snapshot = await _snapshot(conn)
    history = await _history(conn)
    live = await fetch_accidents(
        conn, ["description", "tags", "latitude", "longitude", "geocode_precision", "activity_class"]
    )
    terrain, version = await _terrain(conn)
    rows: list[ActivityRow] = []
    skipped: Counter[str] = Counter()
    for r in live:
        accident_id = _id(r)
        why = decided_elsewhere(_str(r["activity_class"]), history.get(accident_id, ()))
        if why is not None:
            skipped[why] += 1
            continue
        if accident_id not in snapshot:
            skipped["not_in_snapshot"] += 1
            continue
        source, act = snapshot[accident_id]
        # Narrative text is read live and only in memory: never written to a CSV, log or output.
        narrative = f"{r['description'] or ''} {r['tags'] or ''}"
        near = (
            normalize_text(act) in SKI_LIKE
            and r["geocode_precision"] in TRUSTED_POINT_PRECISIONS
            and _near(r["latitude"], r["longitude"], terrain)
        )
        rows.append(ActivityRow(accident_id, source_family(source), act, narrative, near))
    return R4Input(rows, version, skipped)


def _class_changes(accident_id: int, cls: str, version: str, method: str) -> list[Change]:
    return [
        Change(accident_id, "activity_class", cls, method),
        Change(accident_id, "activity_rule_version", version, method),
        Change(accident_id, "inclusion_flag", APPROACH_FLAG if cls == "climbing_approach" else None, method),
    ]


async def run_r4(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    inputs = await _rows(conn)
    report = ValidationReport("repair:r4")
    changes: list[Change] = []
    unmapped: Counter[str] = Counter()
    classes: Counter[str] = Counter()
    pending = 0
    for row in inputs.rows:
        if needs_review(row.family, row.activity):
            pending += 1
            report.quarantine(str(row.accident_id), "activity_pending_review")
            continue
        result = classify_activity(row.family, row.activity, row.narrative, row.near_climb_terrain)
        if result is None:
            unmapped[f"{row.family}:{normalize_text(row.activity)}"] += 1
            report.quarantine(str(row.accident_id), "unmapped_activity")
            continue
        report.accept()
        classes[result[0]] += 1
        changes += _class_changes(row.accident_id, result[0], inputs.version, "r4")
    if unmapped and args.apply:
        raise SystemExit(f"unmapped activity values, extend activity.py first: {dict(unmapped)}")
    summary = await run_step(
        conn, step="r4", rule_version=inputs.version, changes=changes, report=report, apply=args.apply,
        metrics={"review_pending": pending},
    )
    return summary | {
        "unmapped": dict(unmapped),
        "terrain_version": inputs.version,
        "review_pending": pending,
        "classes": dict(sorted(classes.items())),
        "skipped": dict(sorted(inputs.skipped.items())),
    }


def _out(args: argparse.Namespace, name: str) -> Path:
    return Path(args.out) if args.out else review.REVIEW_DIR / name


async def run_r4_golden_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    """Drawn only from rows the rule decides, after the vocabulary is complete: needs_review
    rows are the owner's, and a golden set drawn before the vocabulary settles would describe
    a different pool."""
    inputs = await _rows(conn)
    locators = {_id(r): r for r in await fetch_accidents(conn, ["source", "source_id", "date"])}
    candidates: list[dict[str, object]] = []
    unmapped: Counter[str] = Counter()
    for row in inputs.rows:
        if needs_review(row.family, row.activity):
            continue
        result = classify_activity(row.family, row.activity, row.narrative, row.near_climb_terrain)
        if result is None:
            unmapped[f"{row.family}:{normalize_text(row.activity)}"] += 1
            continue
        candidates.append(
            locators[row.accident_id] | {"activity": row.activity, "family": row.family, "outcome": result[0]}
        )
    if unmapped:
        raise SystemExit(f"unmapped activity values, extend activity.py before drawing the golden set: {dict(unmapped)}")
    out = _out(args, "activity_golden_candidates.csv")
    taken = review.export_stratified(
        candidates, stratum=golden_stratum, quotas=GOLDEN_QUOTAS, seed=4, path=out,
        columns=["accident_id", "source", "source_id", "date", "activity"], decision_column="label",
        overwrite=args.overwrite,
    )
    return {
        "strata": taken,
        "path": str(out),
        "instructions": (
            "label = climbing|climbing_approach|non_climbing from the source record; then copy only "
            "accident_id,label into data/golden/activity_v1.csv"
        ),
    }


async def _review_pool(conn: AsyncConnection) -> tuple[list[dict[str, object]], dict[int, str | None]]:
    """Pending rows, and the rows the owner already decided (id -> recorded class)."""
    snapshot = await _snapshot(conn)
    history = await _history(conn)
    rows = await fetch_accidents(
        conn, ["source", "source_id", "date", "state", "mountain", "route", "accident_type", "activity_class"]
    )
    pending = [
        r for r in rows
        if _id(r) in snapshot and needs_review(source_family(snapshot[_id(r)][0]), snapshot[_id(r)][1])
        and r["activity_class"] is None
    ]
    decided = {
        _id(r): _str(r["activity_class"]) for r in rows
        if decided_elsewhere(_str(r["activity_class"]), history.get(_id(r), ())) == "owner_decided"
    }
    return pending, decided


REVIEW_COLUMNS = ["accident_id", "source", "source_id", "date", "state", "mountain", "route", "accident_type"]


async def run_r4_review_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    """Every pending row, not a sample: each one is a class nobody has decided yet."""
    pool, _ = await _review_pool(conn)
    out = _out(args, "r4_activity_review.csv")
    review._write(out, pool, REVIEW_COLUMNS, "activity_class", overwrite=args.overwrite)
    return {
        "exported": len(pool),
        "path": str(out),
        "instructions": (
            "read each source report by its locator; activity_class = climbing|climbing_approach|non_climbing, "
            "or none if the report does not settle it (the row stays pending)"
        ),
    }


async def run_r4_review_import(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    pool, decided = await _review_pool(conn)
    decisions = review.read_decisions(
        Path(args.review_file), key="accident_id", decision_column="activity_class", allowed=REVIEW_CHOICES
    )
    refuse_ineligible(decisions, {_id(r) for r in pool} | set(decided))
    # A re-import of the same file is a no-op. A different answer for a decided row would be a
    # silent second decision under the same rule version, so it needs a new review version.
    changed = sorted(a for a, c in decisions.items() if a in decided and c != (decided[a] or "none"))
    if changed:
        raise ValueError(f"{len(changed)} row(s) already decided differently under {R4_REVIEW_VERSION}, e.g. {changed[:5]}")
    report = ValidationReport("repair:r4-review")
    changes: list[Change] = []
    already = 0
    for accident_id, choice in sorted(decisions.items()):
        if accident_id in decided:
            already += 1
            continue
        if choice == "none":
            report.quarantine(str(accident_id), "owner_none")
            continue
        report.accept()
        changes += _class_changes(accident_id, choice, R4_REVIEW_VERSION, "owner_review")
    pending = len(pool) - report.accepted
    summary = await run_step(
        conn, step="r4-review", rule_version=R4_REVIEW_VERSION, changes=changes, report=report, apply=args.apply,
        metrics={"review_pending": pending},
    )
    return summary | {"review_pending": pending, "already_decided": already}
