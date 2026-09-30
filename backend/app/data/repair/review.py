"""Owner review round-trips: a seeded sample goes out as CSV to gitignored data/review/,
decisions come back through the same file. Blank or unknown decisions fail loudly."""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path


def _write(path: Path, rows: Sequence[Mapping[str, object]], columns: Sequence[str], decision_column: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([*columns, decision_column])
        for row in rows:
            writer.writerow([row.get(c) for c in columns] + [""])


def export_sample(
    rows: Sequence[Mapping[str, object]],
    *,
    n: int,
    seed: int,
    path: Path,
    columns: Sequence[str],
    decision_column: str,
) -> int:
    chosen = random.Random(seed).sample(list(rows), min(n, len(rows)))
    _write(path, chosen, columns, decision_column)
    return len(chosen)


def export_stratified(
    rows: Sequence[Mapping[str, object]],
    *,
    stratum: Callable[[Mapping[str, object]], str],
    quotas: Mapping[str, int],
    seed: int,
    path: Path,
    columns: Sequence[str],
    decision_column: str,
) -> dict[str, int]:
    """A uniform sample is mostly easy rows, so a >=95% bar would pass without testing
    the rule; quotas pull in the rows where the rule actually fires. A short stratum is
    topped up from the rest so the set keeps its size. The stratum is never written:
    the labeller must not see what the rule decided."""
    rng = random.Random(seed)
    pools: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        pools[stratum(row)].append(row)
    chosen: list[Mapping[str, object]] = []
    taken: dict[str, int] = {}
    for name in sorted(quotas):
        pool = pools.get(name, [])
        pick = rng.sample(pool, min(quotas[name], len(pool)))
        taken[name] = len(pick)
        chosen += pick
    shortfall = sum(quotas.values()) - len(chosen)
    if shortfall > 0:
        picked = {id(r) for r in chosen}
        rest = [r for r in rows if id(r) not in picked]
        extra = rng.sample(rest, min(shortfall, len(rest)))
        taken["_topup"] = len(extra)
        chosen += extra
    rng.shuffle(chosen)
    _write(path, chosen, columns, decision_column)
    return taken


def read_decisions(path: Path, *, key: str, decision_column: str, allowed: frozenset[str]) -> dict[int, str]:
    decisions: dict[int, str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for number, row in enumerate(csv.DictReader(fh), start=1):
            value = (row.get(decision_column) or "").strip().lower()
            if not value:
                raise ValueError(f"{path.name} row {number}: blank {decision_column}")
            if value not in allowed:
                raise ValueError(f"{path.name} row {number}: {value!r} not in {sorted(allowed)}")
            decisions[int(row[key])] = value
    return decisions
