from collections import Counter

import pytest

from app.ml.cells import TYPE_GROUPS
from app.ml.fixtures import SYNTHETIC_ID_FLOOR, fitted_toy
from app.ml.materialize import conf_counts, score_rows
from app.ml.posterior import Thresholds, summarize

LEVELS = ("high", "medium", "low", "insufficient")


@pytest.fixture(scope="module")
def world():
    toy = fitted_toy(local_incidents=0, archetype_incidents=0, tick_coverage=None, ticks_value=None)
    th = Thresholds(d_star=1.0, conf_high=2.0, conf_medium=4.0, phi=1.0)
    summary = summarize(**toy.inputs(th, evidence_far=True))
    routes = [(SYNTHETIC_ID_FLOOR + i, int(a), "trad") for i, a in enumerate(sorted(set(toy.cells.area_id.tolist())))]
    routes += [(SYNTHETIC_ID_FLOOR + 900, routes[0][1], "unknown"),   # no row: unknown group
               (SYNTHETIC_ID_FLOOR + 901, routes[0][1] + 777, "trad"),  # no row: area the cells do not hold
               (SYNTHETIC_ID_FLOOR + 902, routes[0][1], TYPE_GROUPS[0])]  # a second route in a held area
    return toy.cells, summary, routes


def test_counts_equal_the_rows_materialize_writes(world):
    cells, summary, routes = world
    written = Counter(score_rows("v", cells, summary, routes)["conf_level"])
    counts = conf_counts(cells, summary, routes)
    assert set(counts) == set(LEVELS)
    assert counts == {k: written.get(k, 0) for k in LEVELS}
    assert sum(counts.values()) == sum(written.values()) > 0


def test_the_fixture_exercises_more_than_one_level(world):
    cells, summary, routes = world
    assert sum(1 for v in conf_counts(cells, summary, routes).values() if v) >= 2
