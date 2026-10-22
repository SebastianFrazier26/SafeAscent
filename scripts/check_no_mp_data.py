#!/usr/bin/env python3
"""Fail CI if tracked data, docs or test fixtures carry Mountain Project route/area URLs or real
MP ids. Synthetic test ids (>= 900000000) are allowed. Stdlib only."""
from __future__ import annotations

import csv
import io
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

SCOPES = ("data/", "docs/", "backend/tests/")
# Assembled from parts so this repo file never spells the host (check_no_scrapers.py bans it in
# source files, and the guard must not need an exemption).
HOST = "mountain" + "project" + ".com"
# A climber profile or photo page is as identifying as a route page.
URL = re.compile(re.escape(HOST) + r"/(?:route|area|v|user|photo)/\d+", re.IGNORECASE)
ID_KEYS = ("mp_id", "mp_route_id", "mp_location_id", "location_id", "route_id", "parent_id", "area_id",
           "decided_id", "pick_id", "candidate_id", "llm_pick", "llm_answer", "owner_answer")
# Any snake_case prefix carries the id too (decided_route_id, legacy_route_id, candidate_route_ids),
# and the R10 sheets number their candidates (candidate_1_id). accident_id ends in none of these.
_SNAKE = r"(?:[a-z0-9]+_)*(?:" + "|".join(ID_KEYS) + r"|candidate_\d+_id)"
_KEY = r"(?<![A-Za-z0-9_])" + _SNAKE
# camelCase keys (mpRouteId, routeId, decidedAreaId): matched case-sensitively, so prose is untouched.
_CAMEL = r"(?<![A-Za-z0-9_])(?:[a-z][A-Za-z0-9]*)?(?:mp|Mp|route|Route|area|Area|location|Location|parent|Parent|decided|Decided|pick|Pick)Ids?"
# An MP id is 6+ digits with no leading zero and stands alone: accident and test ids share these
# key names but are small, and a UUID literal ('00000000-...', '12345678-...') is not an MP id.
_ID = r"(?<![\w-])[1-9]\d{5,}(?![\w-])"
_VALUE = r"""["']?\s*[:=]\s*["']?(""" + _ID + ")"
_LIST = r"""(?:s["']?\s*[:=]\s*|\s+IN\s*)[\[(]([^\])]*)[\])]"""
ID_KEY = re.compile(_KEY + _VALUE, re.IGNORECASE)
ID_LIST = re.compile(_KEY + _LIST, re.IGNORECASE)
CAMEL_KEY = re.compile(_CAMEL + _VALUE)
CAMEL_LIST = re.compile(_CAMEL + r"""["']?\s*[:=]\s*[\[(]([^\])]*)[\])]""")
HEADER = re.compile(r"(?:" + _SNAKE + r")s?|" + _CAMEL[len(r"(?<![A-Za-z0-9_])"):])
SYNTHETIC_FLOOR = 900_000_000


def _keyed_ids(text: str) -> list[int]:
    ids = [int(m) for m in ID_KEY.findall(text) + CAMEL_KEY.findall(text)]
    for body in ID_LIST.findall(text) + CAMEL_LIST.findall(text):
        ids += [int(m) for m in re.findall(_ID, body)]
    return ids


def _csv_ids(text: str) -> list[int]:
    rows = csv.reader(io.StringIO(text))
    header = next(rows, None)
    if header is None:
        return []
    # By suffix, like the text keys: decided_id, candidate_3_id, legacy_route_id, mpRouteId.
    cols = [i for i, h in enumerate(header) if HEADER.fullmatch(h.strip())
            or re.fullmatch(_SNAKE + "s?", h.strip().lower())]
    out: list[int] = []
    for row in rows:
        for i in cols:
            if i < len(row):
                out += [int(m) for m in re.findall(_ID, row[i])]
    return out


def violations(paths: Iterable[str], read: Callable[[str], str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        if not path.startswith(SCOPES):
            continue
        text = read(path)
        if URL.search(text):
            found.append(f"{path}: Mountain Project route/area URL")
        ids = _keyed_ids(text) + (_csv_ids(text) if path.endswith(".csv") else [])
        real = [i for i in ids if i < SYNTHETIC_FLOOR]
        if real:
            found.append(f"{path}: {len(real)} real MP id(s)")
    return found


def main() -> int:
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    found = violations(tracked, lambda p: Path(p).read_text(encoding="utf-8", errors="ignore"))
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
