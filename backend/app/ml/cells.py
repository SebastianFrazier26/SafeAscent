"""Phase 3 cell vocabulary on the MP catalog: six scorable type groups pooled in two families
(DP3), the MP area hierarchy (area -> region = depth-2 ancestor -> state -> national), and the
eight state groups spatial-block CV leaves out (DP10)."""

from __future__ import annotations

from datetime import date
from typing import Final, Literal

from app.pipelines.exposure_math import TYPE_GROUPS as _TYPE_GROUPS

TYPE_GROUPS: Final[tuple[str, ...]] = _TYPE_GROUPS
G_INDEX: Final = {g: i for i, g in enumerate(TYPE_GROUPS)}
FAMILY_OF: Final[dict[str, Literal["rock_family", "mountain"]]] = {
    "sport": "rock_family",
    "trad": "rock_family",
    "rock": "rock_family",
    "alpine": "mountain",
    "ice": "mountain",
    "mixed": "mountain",
}
MONTHS: Final = tuple(range(1, 13))
TRAIN_START: Final = date(1990, 1, 1)
NEAREST_AREA_KM: Final = 3.0
TRUSTED_FOR_CELLS: Final = ("exact", "crag", "area")
STATE_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "pacific_nw": ("WA", "OR", "ID", "AK"),
    "california": ("CA", "NV", "HI"),
    "southwest": ("AZ", "NM", "UT"),
    "rockies_north": ("MT", "WY", "SD", "ND", "NE"),
    "colorado": ("CO",),
    "central": ("TX", "OK", "KS", "MN", "WI", "IA", "MO", "AR", "LA", "MI", "IL", "IN", "OH"),
    "southeast": ("KY", "TN", "MS", "AL", "GA", "FL", "SC", "NC", "VA", "WV", "DC", "MD", "DE"),
    "northeast": ("PA", "NJ", "NY", "CT", "RI", "MA", "VT", "NH", "ME"),
}
STATE_GROUP_OF: Final = {s: g for g, states in STATE_GROUPS.items() for s in states}


def region_key(path: str) -> str:
    labels = path.split(".")
    if not all(label.isdigit() for label in labels):
        raise ValueError(f"not an MP area path: {path!r}")
    return ".".join(labels[:2])
