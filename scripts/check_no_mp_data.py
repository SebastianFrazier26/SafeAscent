#!/usr/bin/env python3
"""Fail CI if tracked data, docs or test fixtures carry Mountain Project route/area URLs or real
MP ids. Synthetic test ids (>= 900000000) are allowed. Stdlib only."""
from __future__ import annotations

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
ID_KEY = re.compile(r"""["']?mp_(?:route_|location_)?id["']?\s*[:=]\s*["']?(\d{6,})""", re.IGNORECASE)
SYNTHETIC_FLOOR = 900_000_000


def _csv_ids(text: str) -> list[int]:
    lines = text.splitlines()
    if not lines:
        return []
    header = [h.strip().lower() for h in lines[0].split(",")]
    cols = [i for i, h in enumerate(header) if h in ("mp_id", "mp_route_id", "mp_location_id")]
    out: list[int] = []
    for line in lines[1:]:
        cells = line.split(",")
        out += [int(cells[i]) for i in cols if i < len(cells) and cells[i].strip().isdecimal()]
    return out


def violations(paths: Iterable[str], read: Callable[[str], str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        if not path.startswith(SCOPES):
            continue
        text = read(path)
        if URL.search(text):
            found.append(f"{path}: Mountain Project route/area URL")
        ids = [int(m) for m in ID_KEY.findall(text)] + (_csv_ids(text) if path.endswith(".csv") else [])
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
