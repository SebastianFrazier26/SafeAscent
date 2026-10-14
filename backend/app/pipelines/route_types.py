"""P2-3/P2-4 type groups from MP route facts, first match wins: mixed, ice, alpine, trad,
sport, then toprope/aid by their underlying rock group (P3:56, ruling R-1), else rock (owner
2026-10-13: MP type "rock" with no other discipline flag, for the 105,701 rock routes with
sport/trad unknown), else unknown (stored, never scored). MP has no bolt count, so a toprope- or
aid-only route with no sport or trad flag maps to trad. Boulder-only routes, and a rock route
with a V grade, get no type group and are never scored."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

TYPE_RULE_VERSION = "rt-mp-v2"
TypeGroup = Literal["sport", "trad", "alpine", "ice", "mixed", "rock", "unknown"]
FLAGS = frozenset(
    {"trad", "sport", "tr", "aid", "alpine", "snow", "ice", "mixed", "bouldering", "commitment", "rock"}
)
ROPED = FLAGS - {"bouldering"}
_ALIASES = {"boulder": "bouldering", "top rope": "tr", "toprope": "tr"}
# Known MP type tokens that carry no type-group signal (Task 2 Step 1 profile).
IGNORED_TOKENS = frozenset({"unknown", "grade i", "grade ii", "grade iii"})
_COMMITMENT_TOKEN = re.compile(r"^grade (iv|v|vi|vii)$")
_MIXED = re.compile(r"(?<![A-Za-z])M\d", re.I)
_ICE = re.compile(r"\b(?:WI|AI)\s?\d", re.I)
_NA_GRADE = re.compile(r"(?<![\w.])5\.\d|\b(?:WI|AI)\s?\d|(?<![A-Za-z])M\d|(?<![A-Za-z])[AC]\d", re.I)
_COMMITMENT_GRADE = re.compile(r"(?:^|\s)(IV|V|VI|VII)(?=\s|$)")
# A bare "V\d" is the boulder (V-scale) grade; a "rock" route graded on it is a controller
# ruling (owner 2026-10-13): unscorable as a boulder, not stored as rock.
_V_GRADE = re.compile(r"(?<![A-Za-z])V\d", re.I)


@dataclass(frozen=True)
class TypeResult:
    type_group: TypeGroup | None
    is_boulder: bool
    disciplines: tuple[str, ...]


def _has_commitment_grade(grade: str) -> bool:
    # A bare roman numeral is also a UIAA rock grade and "V4" is a boulder grade, so a
    # commitment grade counts only as its own token inside a YDS/ice/mixed/aid grade string.
    return bool(_NA_GRADE.search(grade)) and bool(_COMMITMENT_GRADE.search(grade))


def flags_from_mp_type(type_str: str | None) -> set[str]:
    flags: set[str] = set()
    for raw in (type_str or "").split(","):
        token = " ".join(raw.lower().split())
        token = _ALIASES.get(token, token)
        if token in FLAGS:
            flags.add(token)
        elif _COMMITMENT_TOKEN.match(token):
            flags.add("commitment")
    return flags


def map_type_group(flags: Iterable[str], grades: Iterable[str | None]) -> TypeResult:
    f = {x.lower() for x in flags} & FLAGS
    grade_list = [g for g in grades if g]
    text = " ".join(grade_list)
    disciplines = tuple(sorted(f - {"commitment"}))
    if "bouldering" in f and not (f & ROPED):
        return TypeResult(None, True, disciplines)
    group: TypeGroup
    if "mixed" in f or _MIXED.search(text):
        group = "mixed"
    elif "ice" in f or _ICE.search(text):
        group = "ice"
    elif f & {"alpine", "snow", "commitment"} or any(_has_commitment_grade(g) for g in grade_list):
        group = "alpine"
    elif "trad" in f:
        group = "trad"
    elif "sport" in f:
        group = "sport"
    elif f & {"tr", "aid"}:
        group = "trad"
    elif "rock" in f:
        if any(_V_GRADE.search(g) for g in grade_list):
            return TypeResult(None, True, disciplines)
        group = "rock"
    else:
        group = "unknown"
    return TypeResult(group, False, disciplines)
