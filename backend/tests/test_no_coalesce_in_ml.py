"""P3 Task 25 (G6): the scoring code never turns a missing value into a number. Missing is never 0,
so the ML package and the stored-score reader may not coalesce, `or 0`, nan_to_num or fillna(0.
Each allowlist entry is matched by the stripped line's content (never a line number or a pattern)
and must still be present, so an entry cannot outlive the line it excuses."""

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
FILES = sorted(APP.glob("ml/**/*.py")) + [APP / "services" / "model_scores.py"]
FORBIDDEN = re.compile(r"coalesce\(|\bor 0\b|nan_to_num\(|fillna\(0", re.IGNORECASE)

ALLOWED: dict[str, set[str]] = {
    # Exposure columns only: both are masked back to NaN where the season/ticks input is missing.
    "ml/features.py": {
        '"x_season": np.where(season_missing, np.nan, np.log1p(30.0 * np.nan_to_num(cells.season_share, nan=0.0))),',
        '"x_ticks": np.where(tick_on, np.log1p(np.nan_to_num(cells.ticks_per_route, nan=0.0) / tick_ref), np.nan),',
    },
    # Distance moments: a missing feature contributes nothing to the distance, via the `both` mask.
    "ml/archetypes.py": {
        "return cls(np.nan_to_num(mean), np.where((sd > 0) & np.isfinite(sd), sd, 1.0))",
        "num += np.where(both, w[f] * (np.nan_to_num(a) - np.nan_to_num(b)) ** 2, 0.0)",
    },
    # Row counts of a DELETE/UPDATE report, not a score.
    "ml/registry.py": {
        "return PruneReport(int(deleted or 0), int(nulled or 0))",
    },
}


def _hits() -> list[tuple[str, int, str]]:
    out = []
    for path in FILES:
        rel = path.relative_to(APP).as_posix()
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if FORBIDDEN.search(line):
                out.append((rel, n, line.strip()))
    return out


def test_scored_files_are_found():
    assert len(FILES) > 15 and any(p.name == "model_scores.py" for p in FILES)


def test_no_unlisted_coalesce_in_ml():
    stray = [f"{rel}:{n}: {text}" for rel, n, text in _hits() if text not in ALLOWED.get(rel, set())]
    assert not stray, "a missing value must stay missing:\n" + "\n".join(stray)


@pytest.mark.parametrize("rel", sorted(ALLOWED))
def test_every_allowlist_line_still_exists(rel):
    present = {text for r, _, text in _hits() if r == rel}
    assert ALLOWED[rel] <= present, f"stale allowlist entries: {sorted(ALLOWED[rel] - present)}"
