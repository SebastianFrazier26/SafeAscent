from __future__ import annotations

from typing import Literal

SourceFamily = Literal["aac", "avalanche", "nps"]


def source_family(source: str | None) -> SourceFamily:
    key = (source or "").strip().lower()
    if key.startswith("aac"):
        return "aac"
    if key in {"caic", "avalanche", "avalanche.org"} or "avalanche" in key:
        return "avalanche"
    if key.startswith("nps"):
        return "nps"
    raise ValueError(f"unmapped source {source!r}: extend sources.source_family in a reviewed change")
