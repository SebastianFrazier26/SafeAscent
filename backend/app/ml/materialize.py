"""Stored scores (P3 DP2): one model.route_scores row per scorable route x month, copied from its cell's
summary. A NaN in the summary is written as SQL NULL, never 0; the table's CHECKs then refuse a number
that is missing where it must be present."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ml.cells import G_INDEX, MONTHS
from app.ml.dataset import CellData
from app.ml.posterior import CellSummary

BATCH_ROUTES = 20_000
NO_EVIDENCE = "no_evidence"
ROUTES_SQL = text("SELECT route_id, area_id, type_group FROM scorable_routes WHERE type_group <> 'unknown' "
                  "ORDER BY route_id")
RouteRow = tuple[int, int, str]

_DOUBLE = ("rr_mean", "rr_q05", "rr_q95", "percentile", "pct_q05", "pct_q95", "per10k_mean", "per10k_q05",
           "per10k_q95")
_REAL = ("n_eff_geo", "n_eff_feature")
_COLUMNS = ("model_version", "route_id", "month", "type_group", "area_id", "insufficient", "insufficient_reason",
            "rr_mean", "rr_q05", "rr_q95", "percentile", "pct_q05", "pct_q95", "conf_level", "n_eff_geo",
            "n_eff_feature", "per10k_mean", "per10k_q05", "per10k_q95", "per10k_reason")
_CASTS = {"model_version": "text[]", "route_id": "bigint[]", "month": "smallint[]", "type_group": "text[]",
          "area_id": "bigint[]", "insufficient": "boolean[]", "insufficient_reason": "text[]", "conf_level": "text[]",
          "per10k_reason": "text[]", **{c: "double precision[]" for c in _DOUBLE}, **{c: "real[]" for c in _REAL}}
INSERT_SQL = text(
    f"INSERT INTO model.route_scores ({', '.join(_COLUMNS)}) SELECT * FROM unnest("
    + ", ".join(f"CAST(:{c} AS {_CASTS[c]})" for c in _COLUMNS) + ")")


async def read_routes(conn: AsyncConnection) -> list[RouteRow]:
    return [(int(r), int(a), str(g)) for r, a, g in (await conn.execute(ROUTES_SQL)).all()]


def _opt(x: Any) -> float | None:
    v = float(x)
    return v if math.isfinite(v) else None


def score_rows(version: str, cells: CellData, summary: CellSummary,
               routes_by_cell: Sequence[RouteRow]) -> dict[str, list[Any]]:
    """Column arrays for every (route, month) whose (area, type group, month) cell exists. A route of
    an unknown or unscorable group, or in an area the cells do not hold, gets no row."""
    cols: dict[str, list[Any]] = {c: [] for c in _COLUMNS}
    for route_id, area_id, group in routes_by_cell:
        gi = G_INDEX.get(group)
        if gi is None:
            continue
        for m in MONTHS:
            c = cells.index.get((area_id, gi, m))
            if c is None:
                continue
            ins = bool(summary.insufficient[c])
            cols["model_version"].append(version)
            cols["route_id"].append(route_id)
            cols["month"].append(m)
            cols["type_group"].append(group)
            cols["area_id"].append(area_id)
            cols["insufficient"].append(ins)
            cols["insufficient_reason"].append(NO_EVIDENCE if ins else None)
            cols["conf_level"].append(str(summary.conf_level[c]))
            reason = summary.per10k_reason[c]
            cols["per10k_reason"].append(None if reason is None else str(reason))
            for name in (*_DOUBLE, *_REAL):
                cols[name].append(_opt(getattr(summary, name)[c]))
    return cols


def conf_counts(cells: CellData, summary: CellSummary, routes_by_cell: Sequence[RouteRow]) -> dict[str, int]:
    """Rows per conf_level over exactly the rows score_rows would write (route x month), for the model
    card. All four levels are always present, so a level with no rows reads 0, never absent."""
    counts = {"high": 0, "medium": 0, "low": 0, "insufficient": 0}
    for _, area_id, group in routes_by_cell:
        gi = G_INDEX.get(group)
        if gi is None:
            continue
        for m in MONTHS:
            c = cells.index.get((area_id, gi, m))
            if c is not None:
                counts[str(summary.conf_level[c])] += 1
    return counts


async def write_scores(conn: AsyncConnection, version: str, cells: CellData, summary: CellSummary,
                       routes_by_cell: Sequence[RouteRow]) -> int:
    """Inserts in batches of BATCH_ROUTES routes; returns the rows written. Never replaces: a second
    write for the same version fails on the primary key."""
    if summary.insufficient.shape != (cells.n,):
        raise ValueError("summary must hold one value per cell")
    written = 0
    for start in range(0, len(routes_by_cell), BATCH_ROUTES):
        cols = score_rows(version, cells, summary, routes_by_cell[start:start + BATCH_ROUTES])
        if not cols["route_id"]:
            continue
        await conn.execute(INSERT_SQL, cols)
        written += len(cols["route_id"])
    return written
