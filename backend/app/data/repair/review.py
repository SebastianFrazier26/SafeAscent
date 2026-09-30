"""Owner review round-trips: a seeded sample goes out as CSV to gitignored data/review/,
decisions come back through the same file. Blank or unknown decisions fail loudly."""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

# The one gitignored place review CSVs may land; anything else could end up committed.
REVIEW_DIR = Path(__file__).resolve().parents[4] / "data" / "review"
# Spreadsheet apps evaluate a cell starting with these as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _cell(value: object) -> object:
    if isinstance(value, str | int | float) and not isinstance(value, bool) and str(value).startswith(_FORMULA_PREFIXES):
        return "'" + str(value)
    return value


def _write(path: Path, rows: Sequence[Mapping[str, object]], columns: Sequence[str], decision_column: str) -> None:
    # Narrative text stays out: review files are handled outside the database access controls.
    if any(c.lower() == "description" for c in [*columns, decision_column]):
        raise ValueError("review CSVs must not carry the description column")
    target = path.resolve()
    if not target.is_relative_to(REVIEW_DIR.resolve()):
        raise ValueError(f"review CSVs go under {REVIEW_DIR} (gitignored), not {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([*columns, decision_column])
        for row in rows:
            writer.writerow([_cell(row.get(c)) for c in columns] + [""])


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
            raw_key = (row.get(key) or "").strip()
            if not raw_key.isdigit():
                raise ValueError(f"{path.name} row {number}: invalid {key} {raw_key!r}")
            if int(raw_key) in decisions:
                raise ValueError(f"{path.name} row {number}: duplicate {key} {raw_key}")
            decisions[int(raw_key)] = value
    return decisions
