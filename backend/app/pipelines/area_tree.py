"""The MP area tree as the catalog stores it (pure). Paths run root-first along parent_id;
a root that is a US state gives every descendant its state. Coordinates are judged on the
area's own pair; a pair equal to the parent's was copied from it, so its true source is the
topmost area in that run of equal pairs, and a state-level source is no location at all."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.pipelines.gnis import state_code
from app.pipelines.grid import point_key
from app.pipelines.validate import coord_problem

STATE_DEPTH = 1
TreeStatus = Literal["us", "foreign_root", "cycle", "orphan_parent"]
CoordPrecision = Literal["own", "inherited", "none"]
_Chain = tuple[int, ...] | Literal["cycle", "orphan_parent"]


@dataclass(frozen=True)
class LocationRow:
    mp_id: int
    parent_id: int | None
    lat: float | None
    lon: float | None


@dataclass(frozen=True)
class AreaNode:
    area_id: int
    parent_id: int | None
    path: str | None
    depth: int | None
    tree_status: TreeStatus
    state_code: str | None
    lat: float | None
    lon: float | None
    coord_precision: CoordPrecision
    coord_source_id: int | None
    point_key: str | None
    coord_issue: str | None


def root_state(name: str | None) -> str | None:
    return state_code(name)


def _chains(by_id: Mapping[int, LocationRow]) -> dict[int, _Chain]:
    """Root-first id chain per area (ending in the area), or why it has none. Iterative with
    memoisation, so a 45K-row tree is linear and a cycle can never loop."""
    done: dict[int, _Chain] = {}
    for start in by_id:
        trail: list[int] = []
        on_trail: set[int] = set()
        node: int | None = start
        base: _Chain
        while True:
            assert node is not None
            if node in done:
                base = done[node]
                break
            if node in on_trail:
                base = "cycle"
                break
            row = by_id.get(node)
            if row is None:
                base = "orphan_parent"
                break
            trail.append(node)
            on_trail.add(node)
            if row.parent_id is None:
                base = ()
                break
            node = row.parent_id
        for area in reversed(trail):
            if isinstance(base, str):
                done[area] = base
            else:
                base = (*base, area)
                done[area] = base
    return done


def _pair(row: LocationRow) -> tuple[float, float] | None:
    return (row.lat, row.lon) if row.lat is not None and row.lon is not None else None


def _source_index(chain: tuple[int, ...], by_id: Mapping[int, LocationRow]) -> int:
    """Index in chain of the area whose coordinates the last area really carries."""
    i = len(chain) - 1
    own = _pair(by_id[chain[i]])
    while i > 0 and own is not None and _pair(by_id[chain[i - 1]]) == own:
        i -= 1
    return i


def _path_depth(chain: _Chain) -> tuple[str | None, int | None]:
    if isinstance(chain, str):
        return None, None
    return ".".join(str(a) for a in chain), len(chain)


def _unlocated(row: LocationRow, chain: _Chain, status: TreeStatus, state: str | None, issue: str | None) -> AreaNode:
    path, depth = _path_depth(chain)
    return AreaNode(row.mp_id, row.parent_id, path, depth, status, state, None, None, "none", None, None, issue)


def build_area_tree(rows: Sequence[LocationRow], root_states: Mapping[int, str | None]) -> dict[int, AreaNode]:
    by_id = {r.mp_id: r for r in rows}
    nodes: dict[int, AreaNode] = {}
    for area_id, chain in _chains(by_id).items():
        row = by_id[area_id]
        status: TreeStatus
        state: str | None
        precision: CoordPrecision
        if isinstance(chain, str):
            status, state = chain, None
        else:
            state = root_states.get(chain[0])
            status = "us" if state else "foreign_root"
        pair = _pair(row)
        if pair is None:
            nodes[area_id] = _unlocated(row, chain, status, state, "missing_coords")
            continue
        problem = coord_problem(*pair)
        if problem is not None:
            nodes[area_id] = _unlocated(row, chain, status, state, problem)
            continue
        if isinstance(chain, str):
            source_id, precision = area_id, "own"
        else:
            i = _source_index(chain, by_id)
            if i + 1 <= STATE_DEPTH:
                nodes[area_id] = _unlocated(row, chain, status, state, "state_level")
                continue
            source_id = chain[i]
            precision = "own" if source_id == area_id else "inherited"
        path, depth = _path_depth(chain)
        nodes[area_id] = AreaNode(area_id, row.parent_id, path, depth, status, state, pair[0], pair[1], precision,
                                  source_id, point_key(*pair), None)
    return nodes
