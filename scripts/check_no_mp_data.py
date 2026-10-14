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

SCOPES = ("data/", "docs/", "backend/tests/fixtures/")
# Assembled from parts so this repo file never spells the host (check_no_scrapers.py bans it in
# source files, and the guard must not need an exemption).
HOST = "mountain" + "project" + ".com"
URL = re.compile(re.escape(HOST) + r"/(?:route|area|v)/\d+", re.IGNORECASE)
ID_KEYS = ("mp_id", "mp_route_id", "mp_location_id", "location_id", "route_id", "parent_id", "area_id")
_KEY = r"(?<![A-Za-z0-9_])(?:" + "|".join(ID_KEYS) + r")"
# An MP id is 6+ digits with no leading zero and stands alone: accident and test ids share these
# key names but are small, and a UUID literal ('00000000-...', '12345678-...') is not an MP id.
_ID = r"(?<![\w-])[1-9]\d{5,}(?![\w-])"
ID_KEY = re.compile(_KEY + r"""["']?\s*[:=]\s*["']?(""" + _ID + ")", re.IGNORECASE)
ID_LIST = re.compile(_KEY + r"""(?:s["']?\s*[:=]\s*|\s+IN\s*)[\[(]([^\])]*)[\])]""", re.IGNORECASE)
SYNTHETIC_FLOOR = 900_000_000


def _keyed_ids(text: str) -> list[int]:
    ids = [int(m) for m in ID_KEY.findall(text)]
    for body in ID_LIST.findall(text):
        ids += [int(m) for m in re.findall(_ID, body)]
    return ids


def _csv_ids(text: str) -> list[int]:
    rows = csv.reader(io.StringIO(text))
    header = next(rows, None)
    if header is None:
        return []
    keys = {k + s for k in ID_KEYS for s in ("", "s")}
    cols = [i for i, h in enumerate(header) if h.strip().lower() in keys]
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
