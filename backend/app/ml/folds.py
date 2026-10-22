"""Validation folds (P3:365-384): spatial block CV over the eight state groups (primary),
rolling-origin on pre-2019 data, and the prospective kernel window.

Windows are half-open: a rolling or prospective fold trains on [train_start, train_end) and
tests on [test_start, test_end) with test_start == train_end, so no day is in both."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.ml.cells import STATE_GROUPS, TRAIN_START

ORIGINS = (2004, 2008, 2012, 2016)
TEST_YEARS = 3
PROSPECTIVE = (date(2026, 2, 5), date(2026, 7, 3))
BACKTEST_CUTOFFS = tuple(date(y, 1, 1) for y in ORIGINS) + (PROSPECTIVE[0],)


@dataclass(frozen=True)
class Fold:
    name: str
    kind: Literal["spatial", "rolling", "prospective"]
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    held_out_states: frozenset[str]
    exposure_cutoff: date


def spatial_folds(cutoff: date) -> list[Fold]:
    return [
        Fold(
            f"spatial:{g}",
            "spatial",
            TRAIN_START,
            cutoff,
            TRAIN_START,
            cutoff,
            frozenset(states),
            cutoff,
        )
        for g, states in STATE_GROUPS.items()
    ]


def rolling_folds() -> list[Fold]:
    return [
        Fold(
            f"rolling:{y}",
            "rolling",
            TRAIN_START,
            date(y, 1, 1),
            date(y, 1, 1),
            date(y + TEST_YEARS, 1, 1),
            frozenset(),
            date(y, 1, 1),
        )
        for y in ORIGINS
    ]


def prospective_fold() -> Fold:
    return Fold(
        "prospective:2026",
        "prospective",
        TRAIN_START,
        PROSPECTIVE[0],
        PROSPECTIVE[0],
        PROSPECTIVE[1],
        frozenset(),
        PROSPECTIVE[0],
    )
