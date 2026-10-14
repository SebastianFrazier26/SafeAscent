"""Data-platform jobs (Phase 2). Loaders read open APIs or local export files only; anything
that fetches and parses web pages lives in ~/Developer/safeascent-private, never here."""

from __future__ import annotations

import tomllib
from pathlib import Path


def _version() -> str:
    # package = false (pyproject), so there is no installed metadata to ask; both images copy pyproject.toml.
    try:
        with (Path(__file__).resolve().parents[2] / "pyproject.toml").open("rb") as fh:
            return str(tomllib.load(fh)["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


# USGS asks bulk users of EPQS and the 3DEP buckets to identify themselves (final review S5).
USER_AGENT = f"SafeAscent/{_version()} (+https://github.com/SebastianFrazier26/SafeAscent)"
