from datetime import date

from app.ml.cells import STATE_GROUPS, TRAIN_START
from app.ml.folds import (
    BACKTEST_CUTOFFS,
    prospective_fold,
    rolling_folds,
    spatial_folds,
)


def test_spatial_folds_cover_each_state_group_once():
    folds = spatial_folds(date(2026, 10, 1))
    assert [f.name for f in folds] == [f"spatial:{g}" for g in STATE_GROUPS]
    held = [s for f in folds for s in f.held_out_states]
    assert sorted(held) == sorted(s for v in STATE_GROUPS.values() for s in v)


def test_spatial_folds_use_production_cutoff_for_exposure():
    cutoff = date(2026, 10, 1)
    for f in spatial_folds(cutoff):
        assert (f.train_start, f.train_end, f.test_start, f.test_end) == (
            TRAIN_START,
            cutoff,
            TRAIN_START,
            cutoff,
        )
        assert f.exposure_cutoff == cutoff and f.held_out_states


def test_rolling_folds_never_overlap_train_and_test():
    for f in rolling_folds():
        assert f.train_end == f.test_start == f.exposure_cutoff
        assert (f.test_end.year - f.test_start.year) == 3 and not f.held_out_states
    assert [f.test_start.year for f in rolling_folds()] == [2004, 2008, 2012, 2016]


def test_prospective_fold_matches_the_kernel_window():
    f = prospective_fold()
    assert (f.test_start, f.test_end, f.exposure_cutoff) == (
        date(2026, 2, 5),
        date(2026, 7, 3),
        date(2026, 2, 5),
    )
    assert f.train_end == f.test_start and not f.held_out_states
    assert set(BACKTEST_CUTOFFS) >= {f.exposure_cutoff for f in rolling_folds()} | {
        f.exposure_cutoff
    }


def test_temporal_exposure_never_after_train_end():
    for f in [*rolling_folds(), prospective_fold()]:
        assert f.exposure_cutoff <= f.train_end < f.test_end
