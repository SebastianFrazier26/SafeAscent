"""R12: experience facts stated in the record, by versioned regex. Unstated stays
NULL/unknown; two conflicting statements resolve to unknown (precision over recall).
Scoring never requires these columns.

Beyond the plan's lexicon, a match also needs the phrasing that makes it a statement about
a person: "experienced a fall" (verb), "expert terrain", "rescuers guided him" and "the
guide book" are the common narrative forms that state nothing, and negated or comparative
forms ("not a novice", "less experienced") state no level either."""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.data.repair import review
from app.data.repair.framework import Change, Value, fetch_accidents, run_step
from app.pipelines.validate import ValidationReport

R12_VERSION = "r12-v1"
FACT_COLUMNS = ("exp_years_climbing", "exp_stated_level", "exp_first_season", "guided")
FIELDS = (*FACT_COLUMNS, "exp_rule_version")
DEFAULTS: dict[str, Value] = {
    "exp_years_climbing": None, "exp_stated_level": "unknown", "exp_first_season": None, "guided": "unknown",
    "exp_rule_version": None,
}
_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
# Tens-compounds first so "twenty-five" is one number, not "five"; \b on both sides so
# "112 years" is not read as 12.
_NUM = (
    r"\b(\d{1,2}|(?:" + "|".join(_TENS) + r")(?:[-\s](?:" + "|".join(list(_UNITS)[1:10]) + r"))?|"
    + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")\b"
)
_DISCIPLINE = r"(?:(?:rock|ice|alpine|trad|sport|mixed|big\s+wall)\s+)?"
YEARS = re.compile(
    _NUM + r"\+?\s+years?\s+(?:of\s+)?" + _DISCIPLINE + r"(?:climbing|mountaineering)\b"
    + r"|\bclimbing\s+for\s+" + _NUM + r"\+?\s+years?\b",
    re.I,
)
# A party's pooled total is not any one person's experience.
_POOLED_BEFORE = re.compile(r"\bcombined\s+(?:total\s+of\s+)?(?:over\s+|nearly\s+|about\s+)?$", re.I)
_POOLED_AFTER = re.compile(r"^\W*(?:\w+\s+){0,2}(?:between|among)\s+them\b", re.I)
_PEOPLE = (
    r"(?:climbers?|leaders?|mountaineers?|alpinists?|partners?|part(?:y|ies)|teams?|pairs?|groups?|skiers?)"
)
LEVELS = {
    "novice": re.compile(
        r"\b(?:novices?|beginners?|inexperienced)\b"
        r"(?!\s+(?:routes?|slopes?|terrain|areas?|runs?|trails?|climbs?|walls?|crags?|lessons?|class(?:es)?|courses?)\b)",
        re.I,
    ),
    "expert": re.compile(r"\bexperts?\s+(?:\w+\s+)?" + _PEOPLE + r"\b|\b(?:was|were|is|are)\s+an?\s+expert\b", re.I),
    # A degree word or a person noun marks the adjective; "experienced a fall" is the verb.
    "experienced": re.compile(
        r"\b(?:an|very|highly|quite|extremely|well|fairly|reasonably|relatively|was|were|is|are)[-\s]+experienced\b"
        r"|\bexperienced\s+(?:\w+\s+)?" + _PEOPLE + r"\b",
        re.I,
    ),
}
# Applied to the text just before a match: negations, and comparisons that rank two
# people without stating either one's level ("the less experienced partner").
_QUALIFIED_BEFORE = re.compile(
    r"(?:\bnot|\bnever|n't|\bno|\bnon-?|\bless|\bmore|\bmost|\bleast)\s*(?:(?:his|her|their|the|a|an|its|very|so)\s+)?$",
    re.I,
)
FIRST = re.compile(
    r"\bfirst\s+(?:season|year)\s+(?:of\s+)?" + _DISCIPLINE + r"(?:climbing|mountaineering)\b"
    r"|\bfirst\s+(?:season|year)\s+as\s+an?\s+(?:\w+\s+)?(?:climber|mountaineer|alpinist)\b",
    re.I,
)
UNGUIDED = re.compile(
    r"\b(?:unguided|self[-\s]guided|without\s+(?:a\s+)?guides?)\b"
    r"|\bindependent\s+(?:part(?:y|ies)|climbers?|teams?|groups?|expeditions?|ascents?|trips?)\b",
    re.I,
)
# Bare "the guide" is left out: it is as often a guidebook or another party's guide.
GUIDED = re.compile(
    r"(?<![\w-])guided\s+(?:\w+\s+)?(?:part(?:y|ies)|climbs?|trips?|ascents?|groups?|expeditions?|clients?|tours?|teams?)\b"
    r"|\bclients?\b"
    r"|\b(?:their|his|her|with\s+(?:a|their|his|her)|hired\s+an?)\s+(?:(?:mountain|ice|rock|alpine)\s+)?guides?\b(?!\s*book)",
    re.I,
)


@dataclass(frozen=True)
class Experience:
    years: int | None
    level: str
    first_season: bool | None
    guided: str


NOTHING = Experience(None, "unknown", None, "unknown")
GOLDEN_QUOTAS = {"fired": 60, "silent": 20}


def parse_number(word: str) -> int | None:
    w = word.strip().lower()
    if w.isdecimal():
        return int(w)
    if w in _UNITS:
        return _UNITS[w]
    parts = re.split(r"[-\s]+", w)
    if parts[0] in _TENS and (len(parts) == 1 or (len(parts) == 2 and parts[1] in _UNITS and 0 < _UNITS[parts[1]] < 10)):
        return _TENS[parts[0]] + (_UNITS[parts[1]] if len(parts) == 2 else 0)
    return None


def _stated(pattern: re.Pattern[str], text: str) -> bool:
    return any(not _QUALIFIED_BEFORE.search(text[max(0, m.start() - 30):m.start()]) for m in pattern.finditer(text))


def _years(text: str) -> int | None:
    numbers: set[int] = set()
    for m in YEARS.finditer(text):
        if _POOLED_BEFORE.search(text[max(0, m.start() - 40):m.start()]) or _POOLED_AFTER.search(text[m.end():m.end() + 40]):
            continue
        n = parse_number(m.group(1) or m.group(2) or "")
        if n is not None:
            numbers.add(n)
    years = numbers.pop() if len(numbers) == 1 else None
    return years if years is not None and 0 <= years <= 80 else None


def extract_experience(text: str | None) -> Experience:
    if not text:
        return NOTHING
    levels = {name for name, pattern in LEVELS.items() if _stated(pattern, text)}
    level = levels.pop() if len(levels) == 1 else "unknown"
    first = True if _stated(FIRST, text) else None
    unguided, guided = _stated(UNGUIDED, text), _stated(GUIDED, text)
    guide = "unguided" if unguided and not guided else "guided" if guided and not unguided else "unknown"
    return Experience(_years(text), level, first, guide)


def facts(e: Experience) -> dict[str, Value]:
    return {
        "exp_years_climbing": e.years, "exp_stated_level": e.level, "exp_first_season": e.first_season,
        "guided": e.guided,
    }


def decided_elsewhere(current: Mapping[str, object], last: Mapping[str, str]) -> str | None:
    """Why R12 must leave a row alone, or None. last maps each experience field to the rule
    version of its latest revision. Whoever wrote a field last owns the row: the manual AAC
    CSV and Phase 4 self-reports feed these columns too, and their values are not R12's to
    re-decide."""
    if any(not version.startswith("r12-v") for version in last.values()):
        return "decided_elsewhere"
    if any(current[f] != DEFAULTS[f] and f not in last for f in FIELDS):
        return "labelled_elsewhere"
    return None


@dataclass(frozen=True)
class R12Row:
    accident_id: int
    current: dict[str, object]
    extracted: Experience


async def _rows(conn: AsyncConnection) -> tuple[list[R12Row], Counter[str]]:
    """Rows R12 decides. The narrative is not in the snapshot (ingest may never read it
    there), so it is read live and only in memory: never written to a CSV, log or output."""
    snapshot = {int(a) for (a,) in (await conn.execute(text("SELECT accident_id FROM internal.accidents_raw"))).all()}
    history: dict[int, dict[str, str]] = defaultdict(dict)
    result = await conn.execute(
        text(
            "SELECT accident_id, field, rule_version FROM internal.accident_revisions "
            "WHERE field = ANY(:fields) ORDER BY id"
        ),
        {"fields": list(FIELDS)},
    )
    for accident_id, field, version in result.all():
        history[int(accident_id)][str(field)] = str(version)
    rows: list[R12Row] = []
    skipped: Counter[str] = Counter()
    for r in await fetch_accidents(conn, ["description", *FIELDS]):
        accident_id = int(str(r["accident_id"]))
        why = decided_elsewhere(r, history.get(accident_id, {}))
        if why is not None:
            skipped[why] += 1
            continue
        if accident_id not in snapshot:
            skipped["not_in_snapshot"] += 1
            continue
        narrative = r["description"] if isinstance(r["description"], str) else None
        rows.append(R12Row(accident_id, {f: r[f] for f in FIELDS}, extract_experience(narrative)))
    return rows, skipped


async def run_r12(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    rows, skipped = await _rows(conn)
    report = ValidationReport("repair:r12")
    changes: list[Change] = []
    filled: Counter[str] = Counter()
    for row in rows:
        if row.extracted == NOTHING:
            report.quarantine(str(row.accident_id), "nothing_stated")
        else:
            report.accept()
        # R12 writes the explicit unknowns too, and exp_rule_version marks every row it decided.
        target = facts(row.extracted) | {"exp_rule_version": R12_VERSION}
        filled.update(f for f in FACT_COLUMNS if target[f] != DEFAULTS[f])
        # Only real differences become changes: apply_changes costs round trips per change
        # (G14), and an equal value would be counted unchanged and written nowhere anyway.
        changes += [Change(row.accident_id, f, v, "r12") for f, v in target.items() if row.current[f] != v]
    summary = await run_step(conn, step="r12", rule_version=R12_VERSION, changes=changes, report=report, apply=args.apply)
    return summary | {"filled": dict(sorted(filled.items())), "skipped": dict(sorted(skipped.items()))}


async def run_r12_golden_export(conn: AsyncConnection, args: argparse.Namespace) -> dict[str, object]:
    """Stratified toward rows where a rule fires, so the precision bar tests the rule; the
    silent rows measure recall. Only locators go out: no narrative, no rule decision."""
    rows, _ = await _rows(conn)
    fired = {row.accident_id: row.extracted != NOTHING for row in rows}
    locators = [
        {"accident_id": r["accident_id"], "source": r["source"], "source_id": r["source_id"], "date": r["date"]}
        for r in await fetch_accidents(conn, ["source", "source_id", "date"])
        if int(str(r["accident_id"])) in fired
    ]
    out = Path(args.out) if args.out else review.REVIEW_DIR / "experience_golden_candidates.csv"
    taken = review.export_stratified(
        locators,
        stratum=lambda r: "fired" if fired[int(str(r["accident_id"]))] else "silent",
        quotas=GOLDEN_QUOTAS, seed=12, path=out,
        columns=["accident_id", "source", "source_id", "date", "exp_years_climbing", "exp_stated_level", "exp_first_season"],
        decision_column="guided",
        overwrite=args.overwrite,
    )
    return {
        "strata": taken,
        "path": str(out),
        "instructions": (
            "from the source record fill exp_years_climbing (number or blank), exp_stated_level "
            "(novice|intermediate|experienced|expert|unknown), exp_first_season (true|false|blank), "
            "guided (guided|unguided|unknown); only what the record states. Then copy accident_id and "
            "those four columns into data/golden/experience_v1.csv"
        ),
    }
